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
