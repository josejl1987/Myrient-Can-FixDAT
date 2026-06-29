"""Tests for minerva_cli batch-* subcommands (headless, no Qt)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import minerva_cli
from minerva.domain.reports import FolderImportSummary, QueueResult, ReportSummary
from datetime import datetime, timezone


def _make_report(rid: str, status: str = "ready") -> ReportSummary:
    return ReportSummary(
        id=rid, path=f"{rid}.dat", name=f"Report-{rid}",
        collection="Nintendo", system="Nintendo - Game Boy Color",
        imported_at=datetime.now(timezone.utc).isoformat(),
        requested_count=10, status=status,
    )


def test_batch_list_prints_reports(capsys, monkeypatch):
    fake_svc = MagicMock()
    fake_svc._state.list_reports.return_value = [
        _make_report("r1", "ready"),
        _make_report("r2", "reviewed"),
    ]
    monkeypatch.setattr(minerva_cli, "_build_batch_service", lambda output_dir="downloads": fake_svc)
    minerva_cli.command_batch_list(MagicMock())
    out = capsys.readouterr().out
    assert "Report-r1" in out
    assert "Report-r2" in out
    assert "ready" in out
    assert "reviewed" in out


def test_batch_list_empty(capsys, monkeypatch):
    fake_svc = MagicMock()
    fake_svc._state.list_reports.return_value = []
    monkeypatch.setattr(minerva_cli, "_build_batch_service", lambda output_dir="downloads": fake_svc)
    minerva_cli.command_batch_list(MagicMock())
    out = capsys.readouterr().out
    assert "No reports" in out


def test_batch_queue_calls_queue_all_ready(capsys, monkeypatch):
    fake_svc = MagicMock()
    fake_svc.queue_all_ready.return_value = QueueResult(
        added=7, skipped_active=1, skipped_complete=2, skipped_missing=0)
    monkeypatch.setattr(minerva_cli, "_build_batch_service", lambda output_dir="downloads": fake_svc)
    args = MagicMock(filter="", no_reviewed=False, output_dir="downloads")
    minerva_cli.command_batch_queue(args)
    out = capsys.readouterr().out
    assert "Queued 7" in out
    fake_svc.queue_all_ready.assert_called_once()


def test_batch_queue_with_filter(capsys, monkeypatch):
    fake_svc = MagicMock()
    fake_svc.queue_all_ready.return_value = QueueResult(0, 0, 0, 0)
    monkeypatch.setattr(minerva_cli, "_build_batch_service", lambda output_dir="downloads": fake_svc)
    args = MagicMock(filter="Nintendo", no_reviewed=True, output_dir="downloads")
    minerva_cli.command_batch_queue(args)
    fake_svc.queue_all_ready.assert_called_once_with(name_filter="Nintendo", include_reviewed=False)


def test_batch_rematch_loops_over_ids(capsys, monkeypatch):
    fake_svc = MagicMock()
    monkeypatch.setattr(minerva_cli, "_build_batch_service", lambda output_dir="downloads": fake_svc)
    args = MagicMock(report_ids=["r1", "r2", "r3"])
    minerva_cli.command_batch_rematch(args)
    out = capsys.readouterr().out
    assert fake_svc.rematch_report.call_count == 3
    assert "3" in out


def test_batch_rematch_reports_failures(capsys, monkeypatch):
    fake_svc = MagicMock()
    fake_svc.rematch_report.side_effect = [None, ValueError("boom"), None]
    monkeypatch.setattr(minerva_cli, "_build_batch_service", lambda output_dir="downloads": fake_svc)
    args = MagicMock(report_ids=["r1", "r2", "r3"])
    minerva_cli.command_batch_rematch(args)
    out = capsys.readouterr().out
    assert "1 failed" in out


def test_batch_import_walks_folder(capsys, monkeypatch, tmp_path):
    (tmp_path / "a.dat").write_text('<?xml?><dataframe name="A"></dataframe>')
    (tmp_path / "b.dat").write_text('<?xml?><dataframe name="B"></dataframe>')
    (tmp_path / "ignore.txt").write_text("nope")

    fake_svc = MagicMock()
    fake_svc.import_report.side_effect = lambda path: _make_report(path.stem)
    fake_svc.match_report.return_value = None
    monkeypatch.setattr(minerva_cli, "_build_batch_service", lambda output_dir="downloads": fake_svc)

    args = MagicMock(directory=str(tmp_path))
    minerva_cli.command_batch_import(args)
    out = capsys.readouterr().out

    assert fake_svc.import_report.call_count == 2
    assert fake_svc.match_report.call_count == 2
    assert "2" in out


def test_batch_import_nonexistent_dir(capsys, monkeypatch):
    monkeypatch.setattr(minerva_cli, "_build_batch_service", lambda output_dir="downloads": MagicMock())
    args = MagicMock(directory="/nonexistent/path/xyz")
    with pytest.raises(SystemExit):
        minerva_cli.command_batch_import(args)
