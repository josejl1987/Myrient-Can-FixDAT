# Logging System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a centralized logging system with rotating file output, console output, configurable verbosity via CLI/env/Settings, and a Settings UI card for level control and log file access.

**Architecture:** A single `minerva/logging_config.py` module provides `configure_logging(level)`, called once at startup from `bootstrap.main()` and `minerva_cli.main()`. It attaches a `RotatingFileHandler` (5 MB × 3, DEBUG+) and a `StreamHandler` (stderr, WARNING+) to the root logger. The Settings page gains a log-level dropdown and an "Open log file" button on the Advanced page.

**Tech Stack:** Python stdlib `logging`, `logging.handlers.RotatingFileHandler`, PyQt6 `QDesktopServices`, pytest with `tmp_path`

---

### Task 1: Create `minerva/logging_config.py`

**Files:**
- Create: `minerva/logging_config.py`
- Test: `tests/test_logging_config.py`

**Step 1: Write the failing test**

```python
"""Tests for minerva.logging_config — logging setup and configuration."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

import pytest

from minerva.logging_config import configure_logging, get_log_file_path


@pytest.fixture(autouse=True)
def _reset_logging():
    """Snapshot and restore root logger state between tests."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def test_get_log_file_path_creates_parent(tmp_path, monkeypatch):
    """get_log_file_path creates the parent directory if it doesn't exist."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    path = get_log_file_path()
    assert path.parent.exists()
    assert path.name == "minerva.log"


def test_configure_logging_attaches_two_handlers(tmp_path, monkeypatch):
    """configure_logging attaches a file handler and a console handler."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    configure_logging("INFO")
    root = logging.getLogger()
    handler_types = [type(h) for h in root.handlers]
    assert logging.FileHandler in handler_types or any(
        isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers
    )
    assert any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers)


def test_configure_logging_sets_root_level(tmp_path, monkeypatch):
    """configure_logging sets the root logger level."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    configure_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG

    configure_logging("WARNING")
    assert logging.getLogger().level == logging.WARNING


def test_configure_logging_is_idempotent(tmp_path, monkeypatch):
    """Calling configure_logging twice doesn't duplicate handlers."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    configure_logging("INFO")
    count = len(logging.getLogger().handlers)
    configure_logging("WARNING")
    assert len(logging.getLogger().handlers) == count


def test_configure_logging_file_receives_messages(tmp_path, monkeypatch):
    """Messages are actually written to the log file."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    log_path = configure_logging("DEBUG")
    test_logger = logging.getLogger("test.logging_config")
    test_logger.info("test message 12345")
    # Flush all handlers
    for h in logging.getLogger().handlers:
        h.flush()
    content = log_path.read_text(encoding="utf-8")
    assert "test message 12345" in content


def test_configure_logging_console_level_filters(tmp_path, monkeypatch):
    """Console handler respects a separate, higher level than file."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    configure_logging("INFO", console_level="WARNING")
    console_handlers = [
        h for h in logging.getLogger().handlers
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert len(console_handlers) == 1
    assert console_handlers[0].level == logging.WARNING


def test_configure_logging_fallback_on_dir_error(tmp_path, monkeypatch):
    """If the log directory can't be created, fall back to console-only."""
    # Point XDG_DATA_HOME to a path under a file (can't mkdir)
    blocking_file = tmp_path / "blocker"
    blocking_file.write_text("x")
    monkeypatch.setenv("XDG_DATA_HOME", str(blocking_file))
    # Should not raise
    result = configure_logging("INFO")
    # Still has a console handler
    assert any(isinstance(h, logging.StreamHandler) for h in logging.getLogger().handlers)
    # Result is a path (may not exist) — just check it returned something
    assert isinstance(result, Path)
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_logging_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'minerva.logging_config'`

**Step 3: Write minimal implementation**

```python
"""Centralized logging configuration for the Minerva application.

Call ``configure_logging()`` once at startup — from both the GUI entry
point (``minerva.app.bootstrap.main``) and the CLI (``minerva_cli.main``).
It attaches a rotating file handler (DEBUG+) and a console stream handler
(WARNING+ by default) to the root logger.

The log file lives under the platform-appropriate data directory::

    Linux:   ~/.local/share/MinervaFixDAT/logs/minerva.log
    macOS:   ~/Library/Application Support/MinervaFixDAT/logs/minerva.log
    Windows: %LOCALAPPDATA%/MinervaFixDAT/logs/minerva.log
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_APP_NAME = "MinervaFixDAT"
_LOG_FILE_NAME = "minerva.log"
_MAX_BYTES = 5_000_000  # 5 MB
_BACKUP_COUNT = 3

# Module-level sentinel so configure_logging is idempotent across
# repeated calls (e.g. when settings change at runtime).
_configured: bool = False


def get_log_file_path() -> Path:
    """Resolve the platform-appropriate log file path.

    Creates the parent directory if it doesn't exist. Falls back to
    ``./logs/minerva.log`` relative to the CWD if the platform path
    can't be determined.
    """
    data_home = os.environ.get("XDG_DATA_HOME")
    if data_home:
        base = Path(data_home) / _APP_NAME / "logs"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / _APP_NAME / "logs"
    elif sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            base = Path(local) / _APP_NAME / "logs"
        else:
            base = Path.home() / "AppData" / "Local" / _APP_NAME / "logs"
    else:
        base = Path.home() / ".local" / "share" / _APP_NAME / "logs"

    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Fall back to a local directory we can create
        base = Path("logs")
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError:
            base = Path.cwd()
    return base / _LOG_FILE_NAME


def configure_logging(
    level: str | int = "INFO",
    *,
    console_level: str | int | None = None,
) -> Path:
    """Configure the root logger with file and console handlers.

    Parameters:
        level: Root logger level (applies to file handler). Accepts a
            level name string ("DEBUG", "INFO", "WARNING", "ERROR") or
            an int (``logging.DEBUG`` etc.).
        console_level: Override level for the console handler. Defaults
            to the same as ``level``. Useful when you want the file to
            capture DEBUG while the console only shows WARNING+.

    Returns:
        The resolved log file path. The file may not exist yet if
        directory creation failed — in that case logging falls back to
        console-only.

    This function is idempotent: calling it again replaces existing
    handlers rather than stacking duplicates.
    """
    global _configured

    numeric_level = _coerce_level(level)
    numeric_console = _coerce_level(console_level) if console_level is not None else numeric_level

    root = logging.getLogger()
    # Remove existing handlers we attached (idempotent reconfiguration)
    for h in root.handlers[:]:
        if getattr(h, "_minerva_managed", False):
            root.removeHandler(h)
            h.close()

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # ── File handler (rotating, DEBUG+) ──────────────────────────────
    log_path = get_log_file_path()
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)  # file always captures everything
        file_handler.setFormatter(formatter)
        file_handler._minerva_managed = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)
    except OSError:
        # Can't create the log file — fall back to console-only.
        # Use stderr directly so the user sees something.
        sys.stderr.write(
            f"Warning: could not create log file at {log_path}, "
            f"logging to console only.\n"
        )
        log_path = Path("logs") / _LOG_FILE_NAME  # logical path for display

    # ── Console handler (stderr, configurable level) ─────────────────
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(numeric_console)
    console_handler.setFormatter(formatter)
    console_handler._minerva_managed = True  # type: ignore[attr-defined]
    root.addHandler(console_handler)

    root.setLevel(numeric_level)
    _configured = True
    return log_path


def _coerce_level(level: str | int) -> int:
    """Convert a level name or int to the numeric logging level."""
    if isinstance(level, int):
        return level
    numeric = logging.getLevelName(level.upper())
    if isinstance(numeric, int):
        return numeric
    # Invalid level name — default to INFO
    return logging.INFO
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_logging_config.py -v`
Expected: All 7 tests PASS

**Step 5: Commit**

```bash
git add minerva/logging_config.py tests/test_logging_config.py
git commit -m "feat: add centralized logging configuration module

configure_logging() attaches a rotating file handler (5MB×3, DEBUG+)
and a console stream handler (stderr, WARNING+ default) to the root
logger. Idempotent — safe to call again when settings change."
```

---

### Task 2: Wire logging into `bootstrap.main()`

**Files:**
- Modify: `minerva/app/bootstrap.py:14-23` (imports), `:88-111` (main function)
- Test: `tests/test_bootstrap.py`

**Step 1: Write the failing test**

Add to `tests/test_bootstrap.py`:

```python
def test_main_configures_logging(monkeypatch):
    """bootstrap.main() calls configure_logging before creating the window."""
    from minerva.app import bootstrap

    calls = []
    monkeypatch.setattr(bootstrap, "configure_logging", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(bootstrap, "create_application", lambda argv: MagicMock())
    monkeypatch.setattr(bootstrap, "create_main_window", lambda: MagicMock())
    monkeypatch.setattr("PyQt6.QtWidgets.QApplication", MagicMock())

    # Avoid the actual exec loop
    app_mock = MagicMock()
    app_mock.exec.return_value = 0
    monkeypatch.setattr(bootstrap, "create_application", lambda argv: app_mock)

    bootstrap.main([])

    assert len(calls) == 1
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bootstrap.py::test_main_configures_logging -v`
Expected: FAIL

**Step 3: Write minimal implementation**

Modify `minerva/app/bootstrap.py`:

In the imports section (after `import sys` and the `from pathlib import Path`), add:

```python
from minerva.logging_config import configure_logging
```

Change `logger` to `log` for consistency:

```python
log = logging.getLogger(__name__)
```

Replace the `main()` function:

```python
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
```

Also add `import os` to the imports if not already present.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_bootstrap.py::test_main_configures_logging -v`
Expected: PASS

**Step 5: Commit**

```bash
git add minerva/app/bootstrap.py tests/test_bootstrap.py
git commit -m "feat: configure logging at GUI startup

bootstrap.main() now calls configure_logging() with --verbose/-v
and --debug flags plus MINERVA_LOG_LEVEL env var support. Also
normalizes logger -> log for consistency."
```

---

### Task 3: Wire logging into `minerva_cli.py`

**Files:**
- Modify: `minerva_cli.py:17-28` (imports), `:320-339` (argparse), `:384-389` (dispatch)

**Step 1: Write the failing test**

Add to `tests/test_new_implementations.py` (or a new `tests/test_cli_logging.py`):

```python
def test_cli_configure_logging_called(monkeypatch):
    """minerva_cli.main() calls configure_logging on startup."""
    import minerva_cli

    calls = []
    monkeypatch.setattr(minerva_cli, "configure_logging", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(minerva_cli, "command_stats", lambda args: None)
    monkeypatch.setattr("sys.argv", ["minerva_cli.py", "stats"])

    minerva_cli.main()

    assert len(calls) == 1
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_logging.py -v`
Expected: FAIL with `configure_logging` not existing on `minerva_cli`

**Step 3: Write minimal implementation**

In `minerva_cli.py` imports (after `from pathlib import Path`), add:

```python
from minerva.logging_config import configure_logging
```

Add a `--verbose`/`-v` flag to the top-level parser. Modify the `main()` function:

```python
def main() -> None:
    parser = argparse.ArgumentParser(description="Minerva CLI")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose (DEBUG) logging")
    sub = parser.add_subparsers(dest="command")

    # ... existing subparsers unchanged ...

    args = parser.parse_args()

    # ── Logging setup ────────────────────────────────────────────────
    console_level = "DEBUG" if args.verbose else "WARNING"
    env_level = os.environ.get("MINERVA_LOG_LEVEL", "INFO")
    try:
        configure_logging(env_level, console_level=console_level)
    except Exception:
        pass

    commands = {
        "index": command_index,
        # ... rest unchanged ...
    }

    cmd = commands.get(args.command)
    if cmd is None:
        parser.print_help()
        sys.exit(1)
    cmd(args)
```

Also add `import os` to the imports if not present.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli_logging.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add minerva_cli.py tests/test_cli_logging.py
git commit -m "feat: configure logging at CLI startup

minerva_cli.main() now calls configure_logging() with -v/--verbose
flag and MINERVA_LOG_LEVEL env var support."
```

---

### Task 4: Add `log_level` to `SettingsDraft`

**Files:**
- Modify: `minerva/domain/settings.py:22-37`

**Step 1: Write the failing test**

Create `tests/test_settings_domain.py`:

```python
"""Tests for SettingsDraft log_level field."""

from minerva.domain.settings import SettingsDraft


def test_settings_draft_has_log_level_default():
    draft = SettingsDraft()
    assert draft.log_level == "INFO"


def test_settings_draft_accepts_log_level():
    draft = SettingsDraft(log_level="DEBUG")
    assert draft.log_level == "DEBUG"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_settings_domain.py -v`
Expected: FAIL with `AttributeError: 'SettingsDraft' object has no attribute 'log_level'`

**Step 3: Write minimal implementation**

In `minerva/domain/settings.py`, add `log_level` to the `SettingsDraft` dataclass:

```python
@dataclass
class SettingsDraft:
    output_directory: Path = Path("downloads")
    torrent_directory: Path = Path("torrents/Minerva Myrient - 1050 torrents")
    index_path: Path = Path("torrents/minerva_index.db")
    cover_directory: Path = Path("covers")
    keep_seeding: bool = True
    create_hardlinks: bool = True
    preserve_partial: bool = True
    open_destination: bool = False
    notifications: bool = True
    max_concurrent: int = 6
    timeout_seconds: int = 1800
    density: Density = Density.COMPACT
    theme: str = ThemeName.DARK
    accent: str = AccentName.BLUE
    log_level: str = "INFO"
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_settings_domain.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add minerva/domain/settings.py tests/test_settings_domain.py
git commit -m "feat: add log_level field to SettingsDraft"
```

---

### Task 5: Add Logging card to Settings Advanced page

**Files:**
- Modify: `minerva/app/pages/settings.py:250-262` (`_build_advanced_page`), `:266-283` (`_load_draft`), `:285-303` (`_apply_draft_to_form`), `:305-321` (`_form_to_draft`), `:323-333` (`_connect_dirty_signals`), `:361-382` (`_persist_draft`)

**Step 1: Write the failing test**

Add to `tests/test_settings_page.py`:

```python
def test_advanced_page_has_log_level_combo(qtbot):
    """The Advanced page has a log level dropdown."""
    app_state = _make_app_state()
    page = SettingsPage(app_state)
    qtbot.addWidget(page)
    assert hasattr(page, "_log_level_combo")
    assert page._log_level_combo.count() == 4  # DEBUG, INFO, WARNING, ERROR


def test_advanced_page_has_open_log_button(qtbot):
    """The Advanced page has an 'Open log file' button."""
    app_state = _make_app_state()
    page = SettingsPage(app_state)
    qtbot.addWidget(page)
    assert hasattr(page, "_open_log_btn")


def test_log_level_persists_to_settings(qtbot, tmp_path, monkeypatch):
    """Saving the log level writes it to QSettings."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    app_state = _make_app_state()
    page = SettingsPage(app_state)
    qtbot.addWidget(page)
    page._log_level_combo.setCurrentIndex(0)  # DEBUG
    page._on_save()
    assert page._settings.value("log_level", "", str) == "DEBUG"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_settings_page.py::test_advanced_page_has_log_level_combo -v`
Expected: FAIL with `AttributeError: 'SettingsPage' object has no attribute '_log_level_combo'`

**Step 3: Write minimal implementation**

In `minerva/app/pages/settings.py`, modify `_build_advanced_page`:

```python
    def _build_advanced_page(self) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)

        # ── Logging card ─────────────────────────────────────────────
        log_card = SurfacePanel("Logging", icon=Icons.status_warning())
        log_form = QtWidgets.QFormLayout()
        self._log_level_combo = QtWidgets.QComboBox()
        for level in ("DEBUG", "INFO", "WARNING", "ERROR"):
            self._log_level_combo.addItem(level, level)
        log_form.addRow("Log level", self._log_level_combo)
        self._log_path_label = QtWidgets.QLabel("")
        self._log_path_label.setWordWrap(True)
        self._log_path_label.setObjectName("logFilePath")
        log_form.addRow("Log file", self._log_path_label)
        self._open_log_btn = QtWidgets.QPushButton(Icons.folder_open(), "Open log file")
        self._open_log_btn.clicked.connect(self._on_open_log_file)
        log_form.addRow("", self._open_log_btn)
        log_card.body_layout.addLayout(log_form)
        layout.addWidget(log_card)

        # ── Migration and diagnostics (existing) ─────────────────────
        card = SurfacePanel("Migration and diagnostics", icon=Icons.database())
        migrate = QtWidgets.QPushButton("Migrate legacy settings")
        migrate.clicked.connect(self._on_migrate_legacy)
        open_config = QtWidgets.QPushButton(Icons.folder_open(), "Open settings location")
        open_config.clicked.connect(self._open_settings_location)
        card.body_layout.addWidget(migrate)
        card.body_layout.addWidget(open_config)
        layout.addWidget(card)
        layout.addStretch(1)
        return self._scroll_page(container)
```

Add the `_on_open_log_file` method (near `_open_settings_location`):

```python
    def _on_open_log_file(self) -> None:
        from PyQt6 import QtGui
        from minerva.logging_config import get_log_file_path

        log_path = get_log_file_path()
        if log_path.exists():
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(log_path)))
        else:
            NotificationBanner.show_warning(self, "Log file", "No log file exists yet.")
```

Update `_load_draft` to read `log_level`:

```python
    def _load_draft(self) -> None:
        s = self._settings
        self._draft = SettingsDraft(
            # ... existing fields ...
            log_level=s.value("log_level", "INFO", str),
        )
```

Update `_apply_draft_to_form` to set the combo and update the log path label:

```python
    def _apply_draft_to_form(self) -> None:
        self._loading = True
        draft = self._draft
        # ... existing assignments ...
        idx = self._log_level_combo.findData(draft.log_level)
        if idx >= 0:
            self._log_level_combo.setCurrentIndex(idx)
        # Display the log file path
        from minerva.logging_config import get_log_file_path
        self._log_path_label.setText(str(get_log_file_path()))
        self._loading = False
```

Update `_form_to_draft` to read the combo:

```python
    def _form_to_draft(self) -> SettingsDraft:
        return SettingsDraft(
            # ... existing fields ...
            log_level=self._log_level_combo.currentData(),
        )
```

Update `_connect_dirty_signals` to include the combo:

```python
    def _connect_dirty_signals(self) -> None:
        # ... existing connections ...
        self._log_level_combo.currentIndexChanged.connect(self._on_form_changed)
```

Update `_persist_draft` to write `log_level` and apply it live:

```python
    def _persist_draft(self) -> None:
        draft = self._draft
        values = {
            # ... existing values ...
            "log_level": draft.log_level,
        }
        for key, value in values.items():
            self._settings.setValue(key, value)
        self._settings.sync()

        # Apply log level live
        from minerva.logging_config import configure_logging
        try:
            configure_logging(draft.log_level)
        except Exception:
            pass
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_settings_page.py -v -k "log_level or open_log"`
Expected: PASS

**Step 5: Commit**

```bash
git add minerva/app/pages/settings.py tests/test_settings_page.py
git commit -m "feat: add Logging card to Settings Advanced page

Log level dropdown (DEBUG/INFO/WARNING/ERROR), log file path
display, and Open log file button. Level changes apply live via
configure_logging() and persist to QSettings."
```

---

### Task 6: Fix stale log messages and normalize logger names

**Files:**
- Modify: `minerva/app/torrent_monitor.py:59,66`
- Modify: `minerva/app/bootstrap.py:23` (already done in Task 2)
- Modify: `minerva/services/http_download.py:11`

**Step 1: Write the failing test**

Add to `tests/test_torrent_monitor.py`:

```python
def test_start_log_message_says_native_monitor(qtbot, caplog):
    """The start log message says NativeMonitor, not QbitMonitor."""
    import logging
    from minerva.app.torrent_monitor import NativeMonitor

    monitor = NativeMonitor()
    qtbot.addWidget(monitor)
    with caplog.at_level(logging.INFO):
        monitor.start()
    monitor.stop()
    assert any("NativeMonitor started" in r.message for r in caplog.records)
    assert not any("QbitMonitor" in r.message for r in caplog.records)
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_torrent_monitor.py::test_start_log_message_says_native_monitor -v`
Expected: FAIL — "NativeMonitor started" not found in records

**Step 3: Write minimal implementation**

In `minerva/app/torrent_monitor.py`:

Line 59:
```python
            log.info("NativeMonitor started (interval=%dms)", self._timer.interval())
```

Line 66:
```python
        log.info("NativeMonitor stopped")
```

In `minerva/services/http_download.py`, line 11:
```python
log = logging.getLogger(__name__)
```

Then find-and-replace all `logger.` references in `http_download.py` to `log.`. There should be only a few — check with grep first.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_torrent_monitor.py::test_start_log_message_says_native_monitor -v`
Expected: PASS

Run: `python -m pytest tests/test_torrent_monitor.py -v`
Expected: All existing tests still PASS

**Step 5: Commit**

```bash
git add minerva/app/torrent_monitor.py minerva/services/http_download.py tests/test_torrent_monitor.py
git commit -m "fix: rename stale QbitMonitor log messages to NativeMonitor

Also normalize logger -> log in http_download.py for consistency
with all other modules."
```

---

### Task 7: End-to-end smoke test

**Step 1: Run the full test suite**

Run: `python -m pytest tests/ -v -x --timeout=60`
Expected: All tests pass. If any pre-existing test breaks due to the logging changes, investigate.

**Step 2: Manual smoke test — GUI**

```bash
python minerva_gui.py --verbose
```

Expected: Application starts. Check `~/.local/share/MinervaFixDAT/logs/minerva.log` exists and contains entries like:
```
2026-07-09 16:30:00 INFO    minerva.app.bootstrap Minerva starting up (log level=INFO, console=DEBUG)
```

**Step 3: Manual smoke test — CLI**

```bash
python minerva_cli.py stats -v
```

Expected: Command runs. Check log file contains the CLI's log entries.

**Step 4: Manual smoke test — Settings UI**

1. Open the app.
2. Go to Settings → Advanced.
3. Verify the Logging card appears with the dropdown, path, and button.
4. Change the level to DEBUG.
5. Click Save.
6. Click "Open log file" — should open in the system text editor.
7. Verify the log file shows DEBUG-level entries.

**Step 5: Commit if any fixes were needed**

If the smoke tests revealed issues, fix and commit them. Otherwise, no commit needed.
