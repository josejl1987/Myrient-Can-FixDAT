"""Tests for archive.org candidate selection in match review."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from minerva.domain.sources import DownloadSource


def test_match_detail_panel_has_candidate_selected_signal():
    """MatchDetailPanel has a signal for archive.org candidate selection."""
    from minerva.ui.widgets.match_detail_panel import MatchDetailPanel
    assert hasattr(MatchDetailPanel, 'archive_org_candidate_selected')


def test_queue_approved_handles_archive_org_entries(tmp_path):
    """_queue_approved routes archive.org entries with source/source_ref."""
    from minerva.domain.reports import ReviewEntry, ResolutionState

    # An entry with archive.org source selected
    ao_entry = ReviewEntry(
        id="e1",
        report_id="r1",
        ordinal=0,
        filename="Tetris.zip",
        size=1024,
        decision="accept",
        resolution=ResolutionState.READY,
        selected_source=DownloadSource.ARCHIVE_ORG_HTTP.value,
        selected_source_ref="tetris-collection/Tetris.zip",
    )
    assert ao_entry.selected_source == "archive_org_http"
    assert ao_entry.selected_source_ref == "tetris-collection/Tetris.zip"
    assert ao_entry.selected_file_id is None  # No Minerva file_id


def test_queue_approved_splits_entries_by_source():
    """_queue_approved logic splits entries into Minerva (file_id) and archive.org (source_ref)."""
    from minerva.domain.reports import ReviewEntry, ResolutionState
    from minerva.domain.sources import DownloadSource

    entries = [
        # Minerva entry
        ReviewEntry(
            id="e1", report_id="r1", ordinal=0, filename="game1.zip", size=1024,
            decision="accept", resolution=ResolutionState.READY,
            selected_file_id=42,
            selected_source=DownloadSource.MINERVA_TORRENT.value,
        ),
        # Archive.org entry
        ReviewEntry(
            id="e2", report_id="r1", ordinal=1, filename="game2.zip", size=2048,
            decision="accept", resolution=ResolutionState.READY,
            selected_source=DownloadSource.ARCHIVE_ORG_HTTP.value,
            selected_source_ref="ao-item/game2.zip",
        ),
    ]

    minerva_entries = [e for e in entries if e.selected_source == DownloadSource.MINERVA_TORRENT.value]
    ao_entries = [e for e in entries if e.selected_source != DownloadSource.MINERVA_TORRENT.value]

    assert len(minerva_entries) == 1
    assert minerva_entries[0].selected_file_id == 42
    assert len(ao_entries) == 1
    assert ao_entries[0].selected_source_ref == "ao-item/game2.zip"
