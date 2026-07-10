"""
Application entry point — QApplication setup and AppShell launch.

Usage::

    python -m minerva.app.bootstrap              # normal launch
    python -m minerva.app.bootstrap --index       # build index first
    python -m minerva.app.bootstrap --rebuild     # rebuild index first

Compatibility::
    ``python minerva_gui.py`` forwards here via ``minerva/app/bootstrap.py``.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets

from minerva.logging_config import configure_logging

log = logging.getLogger(__name__)


def _resource_root() -> Path:
    """Resolve the package resource root.

    In a normal dev run this is ``minerva/`` (parent of ``app/``).
    In a PyInstaller frozen build, resources are extracted to
    ``sys._MEIPASS`` and ``__file__`` is not reliable.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "minerva"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def create_application(argv: list[str]) -> QtWidgets.QApplication:
    """Build and configure the QApplication instance."""
    QtCore.QCoreApplication.setAttribute(
        QtCore.Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True
    )
    app = QtWidgets.QApplication(argv)
    app.setApplicationName("Minerva Can FixDAT")
    app.setApplicationDisplayName("Minerva Can FixDAT")
    app.setApplicationVersion("0.1.0")
    app.setOrganizationName("MinervaFixDAT")
    app.setStyle("Fusion")

    # Load bundled gaming fonts
    from minerva.ui.theme import ThemeTokens

    tokens = ThemeTokens()
    font_dir = _resource_root() / "ui" / "fonts"
    if font_dir.is_dir():
        for ttf in sorted(font_dir.glob("*.ttf")):
            font_id = QtGui.QFontDatabase.addApplicationFont(str(ttf))
            if font_id < 0:
                log.warning("Failed to load font: %s", ttf.name)

    # Set a concrete default font so custom-painted delegates that read
    # QFont().pixelSize() get a real value (-1 produces QPainter warnings
    # in the Qt log).
    base_font = QtGui.QFont(tokens.body_font, 10)
    if base_font.pointSize() <= 0 and base_font.pixelSize() <= 0:
        base_font.setPointSize(10)
    app.setFont(base_font)

    # Apply the design-system theme
    from minerva.ui.theme import apply_theme

    apply_theme(app, tokens)

    # Initialise QtAwesome global defaults
    from minerva.ui.icons import Icons

    Icons._ensure()

    return app


def create_main_window() -> QtWidgets.QMainWindow:
    """Build the main application window."""
    from minerva.app.shell import AppShell

    return AppShell()


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse CLI args, create app, show shell, exec."""
    argv = list(sys.argv[1:] if argv is None else argv)

    # ── Logging setup (before anything else) ────────────────────────
    console_level = "WARNING"
    file_level = "INFO"
    if "--debug" in argv:
        file_level = "DEBUG"
        console_level = "DEBUG"
        argv.remove("--debug")
    elif "--verbose" in argv or "-v" in argv:
        console_level = "DEBUG"
        argv = [a for a in argv if a not in ("--verbose", "-v")]

    env_level = os.environ.get("MINERVA_LOG_LEVEL", "")
    if env_level:
        file_level = env_level

    try:
        configure_logging(file_level, console_level=console_level)
    except Exception:
        pass  # Never let logging crash the app

    log.info("Minerva starting up (log level=%s, console=%s)", file_level, console_level)

    if "--index" in argv or "--rebuild" in argv:
        print("Building index…")
        from minerva_db import build_index

        t0 = time.time()
        build_index()
        print(f"Done in {time.time() - t0:.1f}s")

    app = create_application(argv)
    win = create_main_window()

    # First-run wizard check (BEFORE show so wizard is modal)
    settings = QtCore.QSettings("MinervaFixDAT", "MinervaGUI")
    from minerva.ui.widgets.setup_wizard import SetupWizard, is_first_run
    if is_first_run(settings):
        wizard = SetupWizard(win)
        wizard.exec()

    win.show()

    exit_code = app.exec()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
