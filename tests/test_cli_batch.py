"""Tests for minerva_cli batch-* subcommands (headless, no Qt)."""

from __future__ import annotations

import json as _json
from unittest.mock import MagicMock

import pytest

import minerva_cli
from minerva.domain.reports import FolderImportSummary, QueueResult

def test_batch_list_prints_reports(capsys, monkeypatch, make_report):
    fake_svc = MagicMock()
    fake_svc._state.list_reports.return_value = [
        make_report("r1", status="ready"),
        make_report("r2", status="reviewed"),
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
    monkeypatch.setattr(minerva_cli, "_build_batch_service", lambda output_dir="downloads", **kw: fake_svc)
    args = MagicMock(json=False)
    minerva_cli.command_batch_list(args)
    out = capsys.readouterr().out
    assert "No reports" in out


def test_batch_queue_calls_queue_all_ready(capsys, monkeypatch):
    fake_svc = MagicMock()
    fake_svc.queue_all_ready.return_value = QueueResult(
        added=7, skipped_active=1, skipped_complete=2, skipped_missing=0)
    monkeypatch.setattr(minerva_cli, "_build_batch_service", lambda output_dir="downloads", **kw: fake_svc)
    args = MagicMock(filter="", no_reviewed=False, output_dir="downloads",
                     json=False, source="romresolve", source_ref=None)
    minerva_cli.command_batch_queue(args)
    out = capsys.readouterr().out
    assert "Queued 7" in out
    fake_svc.queue_all_ready.assert_called_once()


def test_batch_queue_with_filter(capsys, monkeypatch):
    fake_svc = MagicMock()
    fake_svc.queue_all_ready.return_value = QueueResult(0, 0, 0, 0)
    monkeypatch.setattr(minerva_cli, "_build_batch_service", lambda output_dir="downloads", **kw: fake_svc)
    args = MagicMock(filter="Nintendo", no_reviewed=True, output_dir="downloads",
                     json=False, source="romresolve", source_ref=None)
    minerva_cli.command_batch_queue(args)
    fake_svc.queue_all_ready.assert_called_once_with(name_filter="Nintendo", include_reviewed=False)


def test_batch_rematch_loops_over_ids(capsys, monkeypatch):
    fake_svc = MagicMock()
    monkeypatch.setattr(minerva_cli, "_build_batch_service", lambda output_dir="downloads", **kw: fake_svc)
    args = MagicMock(report_ids=["r1", "r2", "r3"], json=False)
    minerva_cli.command_batch_rematch(args)
    out = capsys.readouterr().out
    assert fake_svc.rematch_report.call_count == 3
    assert "3" in out


def test_batch_rematch_reports_failures(capsys, monkeypatch):
    fake_svc = MagicMock()
    fake_svc.rematch_report.side_effect = [None, ValueError("boom"), None]
    monkeypatch.setattr(minerva_cli, "_build_batch_service", lambda output_dir="downloads", **kw: fake_svc)
    args = MagicMock(report_ids=["r1", "r2", "r3"], json=False)
    minerva_cli.command_batch_rematch(args)
    out = capsys.readouterr().out
    assert "1 failed" in out


def test_batch_import_walks_folder(capsys, monkeypatch, tmp_path, make_report):
    (tmp_path / "a.dat").write_text('<?xml?><dataframe name="A"></dataframe>')
    (tmp_path / "b.dat").write_text('<?xml?><dataframe name="B"></dataframe>')
    (tmp_path / "ignore.txt").write_text("nope")

    fake_svc = MagicMock()
    fake_svc.import_report.side_effect = lambda path: make_report(path.stem)
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


# ── JSON output tests ─────────────────────────────────────────────────────────

def test_batch_list_json(capsys, monkeypatch, make_report):
    """batch-list --json emits a valid JSON array with id/name/status/collection/system."""
    fake_svc = MagicMock()
    fake_svc._state.list_reports.return_value = [
        make_report("r1", status="ready"),
        make_report("r2", status="reviewed"),
    ]
    monkeypatch.setattr(minerva_cli, "_build_batch_service", lambda output_dir="downloads": fake_svc)
    args = MagicMock(json=True)
    minerva_cli.command_batch_list(args)
    out = capsys.readouterr().out
    data = _json.loads(out)
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["id"] == "r1"
    assert data[0]["name"] == "Report-r1"
    assert data[0]["status"] == "ready"
    assert "collection" in data[0]
    assert "system" in data[0]


def test_batch_list_json_empty(capsys, monkeypatch):
    """batch-list --json with no reports emits []."""
    fake_svc = MagicMock()
    fake_svc._state.list_reports.return_value = []
    monkeypatch.setattr(minerva_cli, "_build_batch_service", lambda output_dir="downloads": fake_svc)
    args = MagicMock(json=True)
    minerva_cli.command_batch_list(args)
    out = capsys.readouterr().out
    assert _json.loads(out) == []


def test_batch_queue_json(capsys, monkeypatch):
    """batch-queue --json emits added/skipped_active/skipped_complete/skipped_missing."""
    fake_svc = MagicMock()
    fake_svc.queue_all_ready.return_value = QueueResult(
        added=5, skipped_active=1, skipped_complete=2, skipped_missing=3)
    monkeypatch.setattr(minerva_cli, "_build_batch_service", lambda output_dir="downloads", source="romresolve", source_ref=None: fake_svc)
    args = MagicMock(filter="", no_reviewed=False, output_dir="downloads",
                     json=True, source="romresolve", source_ref=None)
    minerva_cli.command_batch_queue(args)
    out = capsys.readouterr().out
    data = _json.loads(out)
    assert data["added"] == 5
    assert data["skipped_active"] == 1
    assert data["skipped_complete"] == 2
    assert data["skipped_missing"] == 3


def test_batch_import_json(capsys, monkeypatch, tmp_path):
    """batch-import --json emits imported/skipped/failed."""
    (tmp_path / "a.dat").write_text('<?xml?><dataframe name="A"></dataframe>')
    fake_svc = MagicMock()
    summary = FolderImportSummary(imported=2, skipped=1, failed=0)
    monkeypatch.setattr(minerva_cli, "_build_batch_service", lambda output_dir="downloads": fake_svc)
    from minerva.services.report_acquisition import ReportAcquisitionService
    monkeypatch.setattr(ReportAcquisitionService, "import_folder", staticmethod(lambda svc, root: summary))
    args = MagicMock(directory=str(tmp_path), json=True)
    minerva_cli.command_batch_import(args)
    out = capsys.readouterr().out
    data = _json.loads(out)
    assert data["imported"] == 2
    assert data["skipped"] == 1
    assert data["failed"] == 0


def test_batch_rematch_json(capsys, monkeypatch):
    """batch-rematch --json emits succeeded/failed."""
    fake_svc = MagicMock()
    monkeypatch.setattr(minerva_cli, "_build_batch_service", lambda output_dir="downloads": fake_svc)
    args = MagicMock(report_ids=["r1", "r2", "r3"], json=True)
    minerva_cli.command_batch_rematch(args)
    out = capsys.readouterr().out
    data = _json.loads(out)
    assert data["succeeded"] == 3
    assert data["failed"] == 0


def test_completed_json(capsys, monkeypatch, tmp_path):
    """completed --json emits file_id/destination/status/updated_at/source/source_ref."""
    from minerva.domain.downloads import QueueRecord
    from minerva_state import MinervaState
    state = MinervaState(tmp_path / "state.db")
    now = "2026-07-01T00:00:00+00:00"
    state.save_queue_record(QueueRecord(
        id="rec1", file_id=42, status="completed",
        destination="/lib/game.zip", created_at=now, updated_at=now,
        source="romresolve", source_ref="/workspace/fixdat1",
    ))
    state.save_queue_record(QueueRecord(
        id="rec2", file_id=43, status="downloading",
        destination="/lib/game2.zip", created_at=now, updated_at=now,
    ))
    monkeypatch.setattr("minerva_state.MinervaState", lambda: state)
    args = MagicMock(json=True, since=None)
    minerva_cli.command_completed(args)
    out = capsys.readouterr().out
    data = _json.loads(out)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["file_id"] == 42
    assert data[0]["status"] == "completed"
    assert data[0]["source"] == "romresolve"
    assert data[0]["source_ref"] == "/workspace/fixdat1"


def test_lookup_batch_json(capsys, monkeypatch, indexed_rom_db):
    """lookup-batch emits JSON array with status/file_id/collection/system."""
    monkeypatch.setattr("minerva_cli.MinervaDB", lambda: indexed_rom_db)
    queries = _json.dumps([
        {"stem": "super mario bros (world)", "size": 102400},
        {"stem": "nonexistent game", "size": 999},
    ])
    args = MagicMock(queries=queries)
    minerva_cli.command_lookup_batch(args)
    out = capsys.readouterr().out
    data = _json.loads(out)
    assert len(data) == 2
    assert data[0]["status"] == "exact"
    assert data[0]["file_id"] == 1
    assert data[0]["collection"] == "Nintendo"
    assert data[1]["status"] == "missing"


def test_find_json(capsys, monkeypatch, indexed_rom_db):
    """find --json emits JSON array with id/stem/size/collection/system/basename."""
    monkeypatch.setattr("minerva_cli.MinervaDB", lambda: indexed_rom_db)
    args = MagicMock(query="mario", collection=None, system=None, limit=20, json=True)
    minerva_cli.command_find(args)
    out = capsys.readouterr().out
    data = _json.loads(out)
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["id"] == 1
    assert data[0]["stem"] == "super mario bros (world)"
    assert data[0]["size"] == 102400
    assert data[0]["collection"] == "Nintendo"
    assert "basename" in data[0]
