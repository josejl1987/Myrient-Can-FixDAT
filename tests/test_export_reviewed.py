"""Tests for ReportAcquisitionService.export_reviewed."""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from minerva.domain.reports import ReportSummary, ReviewEntry
from minerva.services.report_acquisition import ReportAcquisitionService
from minerva_state import MinervaState


@pytest.fixture()
def tmp_state():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    state = MinervaState(db_path=tmp.name)
    yield state
    import os
    os.unlink(tmp.name)


def _seed_report_with_decisions(state: MinervaState) -> str:
    """Import a minimal report with approved + rejected entries. Returns report_id."""
    report_id = "test-export-report"
    report = ReportSummary(
        id=report_id, path="test.dat", name="Test",
        collection="Nintendo", system="Nintendo - Game Boy Color",
        imported_at=datetime.now(timezone.utc).isoformat(),
        requested_count=3, status="reviewed",
    )
    state.save_report(report)
    entries = [
        ReviewEntry(id=f"{report_id}_0", report_id=report_id, ordinal=0,
                    filename="game-a.zip", size=1024,
                    decision="accept", selected_file_id=1),
        ReviewEntry(id=f"{report_id}_1", report_id=report_id, ordinal=1,
                    filename="game-b.zip", size=2048,
                    decision="fuzzy", automatic_file_id=2),
        ReviewEntry(id=f"{report_id}_2", report_id=report_id, ordinal=2,
                    filename="game-c.zip", size=4096,
                    decision="reject"),
    ]
    state.replace_entries(report_id, entries)
    return report_id


class _FakeFile:
    def __init__(self, fid, stem, basename, size):
        self.id = fid
        self.stem = stem
        self.basename = basename
        self.size = size


class _FakeDB:
    """Replaces MinervaDB so we don't need the real 1.3 GB index."""

    def __init__(self):
        self._table = {
            1: _FakeFile(1, "game-a", "game-a.zip", 1024),
            2: _FakeFile(2, "game-b", "game-b.zip", 2048),
        }

    def get_files_by_ids(self, ids):
        return [self._table[i] for i in ids if i in self._table]

    def build_synthetic_dat(self, rows, name, system, collection):
        stems = ",".join(r["stem"] for r in rows)
        return (
            f"<dat name={name!r} system={system!r} "
            f"collection={collection!r} rows=[{stems}]/>"
        )


def test_export_reviewed_writes_dat_with_approved_entries(tmp_state, tmp_path, monkeypatch):
    svc = ReportAcquisitionService(state=tmp_state, db=_FakeDB())
    report_id = _seed_report_with_decisions(tmp_state)
    dest = tmp_path / "out.dat"

    count = svc.export_reviewed(report_id, dest)
    assert count == 2  # accept + fuzzy, not reject
    assert dest.exists()
    content = dest.read_text(encoding="utf-8")
    assert "game-a" in content
    assert "game-b" in content


def test_export_reviewed_zero_when_nothing_approved(tmp_state, tmp_path):
    """A report with no accept/fuzzy entries writes nothing and returns 0."""
    svc = ReportAcquisitionService(state=tmp_state, db=_FakeDB())
    report_id = "no-approved"
    tmp_state.save_report(ReportSummary(
        id=report_id, path="x.dat", name="X", collection="", system="",
        imported_at=datetime.now(timezone.utc).isoformat(),
        requested_count=1, status="reviewed"))
    tmp_state.replace_entries(report_id, [
        ReviewEntry(id=f"{report_id}_0", report_id=report_id, ordinal=0,
                    filename="g.zip", size=10, decision="reject")])
    dest = tmp_path / "out.dat"

    count = svc.export_reviewed(report_id, dest)
    assert count == 0
    assert not dest.exists()


def test_export_reviewed_uses_report_name_in_dat_title(tmp_state, tmp_path):
    svc = ReportAcquisitionService(state=tmp_state, db=_FakeDB())
    report_id = _seed_report_with_decisions(tmp_state)
    dest = tmp_path / "out.dat"

    svc.export_reviewed(report_id, dest)
    content = dest.read_text(encoding="utf-8")
    # name from ReportSummary.name + " reviewed"
    assert "Test reviewed" in content
