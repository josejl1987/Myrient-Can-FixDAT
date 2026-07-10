"""Tests for CLI logging setup."""

from __future__ import annotations

from unittest.mock import MagicMock

import minerva_cli


def test_cli_configure_logging_called(monkeypatch):
    """minerva_cli.main() calls configure_logging on startup."""
    calls = []
    monkeypatch.setattr(minerva_cli, "configure_logging", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(minerva_cli, "command_stats", lambda args: None)
    monkeypatch.setattr("sys.argv", ["minerva_cli.py", "stats"])

    minerva_cli.main()

    assert len(calls) == 1


def test_cli_verbose_sets_debug_console(monkeypatch):
    """--verbose sets console level to DEBUG."""
    calls = []
    monkeypatch.setattr(minerva_cli, "configure_logging", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(minerva_cli, "command_stats", lambda args: None)
    monkeypatch.setattr("sys.argv", ["minerva_cli.py", "stats", "--verbose"])

    minerva_cli.main()

    assert calls[0][1].get("console_level") == "DEBUG"
