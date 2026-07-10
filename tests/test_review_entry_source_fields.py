"""Tests for ReviewEntry source selection fields."""
from __future__ import annotations

from minerva.domain.reports import ReviewEntry, ResolutionState
from minerva.domain.sources import DownloadSource
from minerva_state import MinervaState


def test_review_entry_has_source_fields():
    """ReviewEntry has selected_source and selected_source_ref fields."""
    entry = ReviewEntry(
        id="e1",
        report_id="r1",
        ordinal=0,
        filename="game.zip",
        size=1024,
    )
    assert hasattr(entry, "selected_source")
    assert hasattr(entry, "selected_source_ref")
    assert entry.selected_source == "minerva_torrent"  # default
    assert entry.selected_source_ref is None  # default


def test_review_entry_round_trips_source_fields(tmp_path):
    """ReviewEntry with source fields persists and reloads."""
    state = MinervaState(db_path=tmp_path / "state.db")

    # Save a report first
    from minerva.domain.reports import ReportSummary
    report = ReportSummary(
        id="r1",
        path="/tmp/test.dat",
        name="Test",
        collection="No-Intro",
        system="Nintendo - Game Boy",
        imported_at="2026-07-03",
        requested_count=1,
        status="draft",
    )
    state.save_report(report)

    # Save an entry with archive.org source
    entry = ReviewEntry(
        id="e1",
        report_id="r1",
        ordinal=0,
        filename="Tetris.zip",
        size=1024,
        selected_source=DownloadSource.ARCHIVE_ORG_HTTP.value,
        selected_source_ref="tetris-collection/Tetris.zip",
    )
    state.replace_entries("r1", [entry])

    # Reload and verify
    entries = state.get_entries("r1")
    assert len(entries) == 1
    loaded = entries[0]
    assert loaded.selected_source == "archive_org_http"
    assert loaded.selected_source_ref == "tetris-collection/Tetris.zip"


def test_set_entry_decision_with_source(tmp_path):
    """set_entry_decision can update selected_source and selected_source_ref."""
    state = MinervaState(db_path=tmp_path / "state.db")

    from minerva.domain.reports import ReportSummary
    report = ReportSummary(
        id="r1",
        path="/tmp/test.dat",
        name="Test",
        collection="No-Intro",
        system="Nintendo - Game Boy",
        imported_at="2026-07-03",
        requested_count=1,
        status="draft",
    )
    state.save_report(report)
    state.replace_entries(
        "r1",
        [
            ReviewEntry(
                id="e1",
                report_id="r1",
                ordinal=0,
                filename="Tetris.zip",
                size=1024,
            )
        ],
    )

    # Update with source info
    state.set_entry_decision(
        "r1",
        "e1",
        "accept",
        selected_source=DownloadSource.ARCHIVE_ORG_HTTP.value,
        selected_source_ref="tetris-collection/Tetris.zip",
    )

    entries = state.get_entries("r1")
    assert entries[0].decision == "accept"
    assert entries[0].selected_source == "archive_org_http"
    assert entries[0].selected_source_ref == "tetris-collection/Tetris.zip"
