"""Tests for recursive folder import of fixdat files."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from minerva.domain.reports import ReportSummary, ScopeInferenceRequired
from minerva.services.report_acquisition import ReportAcquisitionService


def _write_fake_dat(path: Path, name: str = "Test", n: int = 1) -> None:
    path.write_text(
        f'<?xml version="1.0"?>\n<dataframe name="{name}">\n'
        + "".join(f'<game name="title-{i}.zip" size="1024"/>\n' for i in range(n))
        + '</dataframe>\n',
        encoding="utf-8",
    )


def test_collect_fixdat_files_recursive(tmp_path):
    root = tmp_path / "reports"
    (root / "sub").mkdir(parents=True)
    (root / "a.dat").write_text("x")
    (root / "sub" / "b.csv").write_text("x")
    (root / "sub" / "c.fixdat").write_text("x")
    (root / "ignored.txt").write_text("x")
    (root / "notes.md").write_text("x")

    files = ReportAcquisitionService.collect_fixdat_files(root)
    exts = {f.suffix.lower() for f in files}
    assert exts <= {".dat", ".csv", ".fixdat"}
    names = {f.name for f in files}
    assert {"a.dat", "b.csv", "c.fixdat"} <= names
    assert "ignored.txt" not in names
    assert "notes.md" not in names


def test_collect_fixdat_files_empty_dir(tmp_path):
    files = ReportAcquisitionService.collect_fixdat_files(tmp_path)
    assert files == []


def test_import_folder_imports_and_matches_each(tmp_path):
    root = tmp_path / "datset"
    root.mkdir()
    _write_fake_dat(root / "a.dat", "A", 1)
    _write_fake_dat(root / "b.dat", "B", 2)

    svc = MagicMock(spec=ReportAcquisitionService)
    svc.import_report.side_effect = lambda path: ReportSummary(
        id=path.stem, path=str(path), name=path.stem, collection="Nintendo",
        system="Nintendo - Game Boy Color",
        imported_at=datetime.now(timezone.utc).isoformat(),
        requested_count=1, status="draft")
    svc.match_report.return_value = None

    summary = ReportAcquisitionService.import_folder(svc, root)

    assert svc.import_report.call_count == 2
    assert svc.match_report.call_count == 2
    assert summary.imported == 2
    assert summary.skipped == 0
    assert summary.failed == 0


def test_import_folder_skips_scope_inference_required(tmp_path):
    root = tmp_path / "datset"
    root.mkdir()
    _write_fake_dat(root / "ambiguous.dat", "A", 1)

    svc = MagicMock(spec=ReportAcquisitionService)
    svc.import_report.side_effect = ScopeInferenceRequired([("a",), ("b",)])

    summary = ReportAcquisitionService.import_folder(svc, root)
    assert summary.imported == 0
    assert summary.skipped == 1
    assert summary.failed == 0


def test_import_folder_continues_after_failure(tmp_path):
    root = tmp_path / "datset"
    root.mkdir()
    _write_fake_dat(root / "good.dat", "Good", 1)
    _write_fake_dat(root / "bad.dat", "Bad", 1)

    def fake_import(path):
        if path.name == "bad.dat":
            raise ValueError("parse error")
        return ReportSummary(
            id=path.stem, path=str(path), name=path.stem, collection="",
            system="", imported_at=datetime.now(timezone.utc).isoformat(),
            requested_count=1, status="draft")

    svc = MagicMock(spec=ReportAcquisitionService)
    svc.import_report.side_effect = fake_import
    svc.match_report.return_value = None

    summary = ReportAcquisitionService.import_folder(svc, root)
    assert summary.imported == 1
    assert summary.failed == 1


def test_import_folder_skips_empty_reports(tmp_path):
    """Empty DATs (EmptyReportError) should be skipped, not failed."""
    from minerva.domain.reports import EmptyReportError

    root = tmp_path / "datset"
    root.mkdir()
    _write_fake_dat(root / "good.dat", "Good", 1)
    (root / "empty.dat").write_text('<?xml?><dataframe name="Empty"></dataframe>')

    def fake_import(path):
        if path.name == "empty.dat":
            raise EmptyReportError(path)
        return ReportSummary(
            id=path.stem, path=str(path), name=path.stem, collection="",
            system="", imported_at=datetime.now(timezone.utc).isoformat(),
            requested_count=1, status="draft")

    svc = MagicMock(spec=ReportAcquisitionService)
    svc.import_report.side_effect = fake_import
    svc.match_report.return_value = None

    summary = ReportAcquisitionService.import_folder(svc, root)
    assert summary.imported == 1
    assert summary.skipped == 1
    assert summary.failed == 0
