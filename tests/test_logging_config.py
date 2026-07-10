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
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.handlers.RotatingFileHandler)
        and getattr(h, "_minerva_managed", False)
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
