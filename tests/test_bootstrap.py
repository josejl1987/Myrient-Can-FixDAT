"""Tests for bootstrap — resource root, app creation, and main window."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from unittest.mock import MagicMock

from PyQt6 import QtCore, QtWidgets

from minerva.app.bootstrap import _resource_root, create_main_window

# Capture the real QApplication class before any test monkeypatches it.
_REAL_QAPP = QtWidgets.QApplication


class TestResourceRoot:
    def test_returns_path_that_exists(self):
        """_resource_root returns an existing directory."""
        root = _resource_root()
        assert isinstance(root, Path)
        assert root.exists(), f"Resource root does not exist: {root}"
        assert root.is_dir()

    def test_returns_path_containing_minerva(self):
        """_resource_root path contains 'minerva'."""
        root = _resource_root()
        parts = [p.lower() for p in root.parts]
        assert "minerva" in parts, f"'minerva' not in path parts {parts}"

    def test_not_frozen_returns_package_parent(self):
        """When not frozen, _resource_root returns parent of parent of __file__."""
        root = _resource_root()
        # _resource_root lives in minerva/app/bootstrap.py,
        # so parent.parent = repo/minerva
        assert root == Path(
            sys.modules["minerva.app.bootstrap"].__file__
        ).resolve().parent.parent

    def test_frozen_uses_meipass(self, monkeypatch):
        """When frozen with _MEIPASS, _resource_root returns _MEIPASS/minerva."""
        fake_meipass = "/fake/pyinstaller/extract"
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", fake_meipass, raising=False)
        root = _resource_root()
        assert root == Path(fake_meipass) / "minerva"

    def test_frozen_without_meipass_falls_back(self, monkeypatch):
        """When frozen but _MEIPASS missing, falls back to file-based path."""
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        root = _resource_root()
        assert root == Path(
            sys.modules["minerva.app.bootstrap"].__file__
        ).resolve().parent.parent


class TestCreateMainWindow:
    def test_returns_main_window(self, qtbot, monkeypatch, tmp_path):
        """GIVEN the bootstrap helper WHEN called THEN it returns an AppShell."""
        # Isolate QSettings to a temp directory so test data never leaks
        # into the user's real config.
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        old_format = QtCore.QSettings.defaultFormat()
        QtCore.QSettings.setDefaultFormat(QtCore.QSettings.Format.IniFormat)
        try:
            settings = QtCore.QSettings("MinervaFixDAT_test", "MinervaGUI_test")
            settings.setValue("state_db_path", str(tmp_path / "state.db"))
            settings.setValue("index_path", str(tmp_path / "index.db"))
            settings.sync()

            # Patch AppShell to use the test-scoped QSettings org/app.
            import minerva.app.shell as shell_mod
            original_shell = shell_mod.AppShell
            monkeypatch.setattr(
                shell_mod, "AppShell",
                lambda *a, **kw: original_shell(
                    *a, settings_org="MinervaFixDAT_test",
                    settings_app="MinervaGUI_test", **kw,
                ),
            )
            win = create_main_window()
            qtbot.addWidget(win)
            assert win is not None
            assert win.__class__.__name__ == "AppShell"
        finally:
            QtCore.QSettings.setDefaultFormat(old_format)


class _FakeQApplication:
    """Stand-in for QtWidgets.QApplication that returns the existing instance.

    create_application() does ``app = QtWidgets.QApplication(argv)``. We
    intercept that to return the already-running qapp so we don't try to
    create a second QApplication (which Qt forbids).

    We also preserve the ``instance()`` classmethod so pytest-qt's internal
    ``_process_events()`` teardown keeps working.
    """

    def __new__(cls, argv):
        return _REAL_QAPP.instance()

    @staticmethod
    def instance():
        return _REAL_QAPP.instance()


class TestCreateApplication:
    """Test create_application without creating a conflicting QApplication."""

    def test_sets_application_name(self, qapp, monkeypatch, tmp_path):
        """create_application sets applicationName correctly."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setattr(QtWidgets, "QApplication", _FakeQApplication)

        from minerva.app.bootstrap import create_application

        app = create_application([])
        assert app.applicationName() == "Minerva Can FixDAT"

    def test_sets_organization_name(self, qapp, monkeypatch, tmp_path):
        """create_application sets organizationName correctly."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setattr(QtWidgets, "QApplication", _FakeQApplication)

        from minerva.app.bootstrap import create_application

        app = create_application([])
        assert app.organizationName() == "MinervaFixDAT"

    def test_sets_fusion_style(self, qapp, monkeypatch, tmp_path):
        """create_application sets the Fusion style.

        apply_theme() installs a stylesheet that wraps the style in a
        QStyleSheetStyle proxy, so we can't read objectName() after the
        full create_application flow. Instead we spy on setStyle to
        verify it was called with 'Fusion'.
        """
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setattr(QtWidgets, "QApplication", _FakeQApplication)

        from minerva.app.bootstrap import create_application

        styles_set: list[str] = []
        real_set_style = qapp.setStyle
        monkeypatch.setattr(
            qapp, "setStyle", lambda s: styles_set.append(s) or real_set_style(s)
        )

        create_application([])
        assert "Fusion" in styles_set, (
            f"Expected setStyle('Fusion') to be called, got {styles_set}"
        )

    def test_sets_display_name(self, qapp, monkeypatch, tmp_path):
        """create_application sets applicationDisplayName."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setattr(QtWidgets, "QApplication", _FakeQApplication)

        from minerva.app.bootstrap import create_application

        app = create_application([])
        assert app.applicationDisplayName() == "Minerva Can FixDAT"


def test_wizard_runs_before_show():
    """GIVEN the bootstrap source WHEN parsed THEN the is_first_run check
    appears BEFORE win.show()."""
    source_path = (
        Path(__file__).resolve().parents[1]
        / "minerva"
        / "app"
        / "bootstrap.py"
    )
    source = source_path.read_text("utf-8")
    tree = ast.parse(source)

    # Find the main() function
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            lines = source.splitlines()
            # Find line numbers (0-indexed) of is_first_run and win.show
            first_run_line = None
            show_line = None
            for i, line in enumerate(lines):
                if "is_first_run" in line and first_run_line is None:
                    first_run_line = i
                if "win.show()" in line and show_line is None:
                    show_line = i
            assert first_run_line is not None, "is_first_run not found in main()"
            assert show_line is not None, "win.show() not found in main()"
            assert first_run_line < show_line, (
                f"is_first_run (line {first_run_line + 1}) must come before "
                f"win.show() (line {show_line + 1})"
            )


def test_main_configures_logging(monkeypatch):
    """bootstrap.main() calls configure_logging before creating the window."""
    from minerva.app import bootstrap

    calls = []
    monkeypatch.setattr(bootstrap, "configure_logging", lambda *a, **k: calls.append((a, k)))

    app_mock = MagicMock()
    app_mock.exec.return_value = 0
    monkeypatch.setattr(bootstrap, "create_application", lambda argv: app_mock)
    monkeypatch.setattr(bootstrap, "create_main_window", lambda: MagicMock())

    bootstrap.main([])

    assert len(calls) == 1
