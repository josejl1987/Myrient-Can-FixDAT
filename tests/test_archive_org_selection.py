"""Real production tests for archive.org queue routing via queue_entries.

Replaces the former assertion-theater tests that merely re-derived the
source-splitting logic instead of testing production behavior. These
tests exercise ``ReportAcquisitionService.queue_entries`` end-to-end and
assert that the resulting queue records carry correct source metadata.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from minerva.domain.downloads import QueueRecord
from minerva.domain.library import LibraryItem
from minerva.domain.reports import (
    QueueItemSpec,
    QueueResult,
    ResolutionState,
    ReviewEntry,
    is_approved,
)
from minerva.domain.sources import DownloadSource
from minerva.parsers.dat_parser import DatEntry
from minerva.services.report_acquisition import ReportAcquisitionService
from minerva_state import MinervaState


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_state():
    """MinervaState backed by a temporary SQLite file."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    state = MinervaState(db_path=tmp.name)
    yield state
    os.unlink(tmp.name)


@pytest.fixture
def mock_db():
    """MinervaDB mock — get_files_by_ids returns empty by default.

    Individual tests monkeypatch the return value as needed.
    """
    db = MagicMock()
    db.get_files_by_ids.return_value = []
    return db


@pytest.fixture
def captured_controller():
    """A mock DownloadController that captures add_to_queue calls.

    Returns a deterministic record_id and stores every call's kwargs
    in ``calls`` so tests can assert on source metadata.
    """
    controller = MagicMock()
    controller._counter = 0
    captured: list[dict] = []

    def _add_to_queue(**kwargs):
        controller._counter += 1
        record_id = f"rec-{controller._counter}"
        captured.append({"record_id": record_id, **kwargs})
        return record_id

    controller.add_to_queue.side_effect = _add_to_queue
    controller.calls = captured
    return controller


@pytest.fixture
def service(tmp_state, mock_db, captured_controller):
    """ReportAcquisitionService with mock DB, real temp state, mock controller."""
    return ReportAcquisitionService(
        state=tmp_state,
        db=mock_db,
        download_controller=captured_controller,
        output_dir=Path("/tmp/test_downloads"),
    )


# ── Entry factories ─────────────────────────────────────────────────────────


def _ao_http_entry(
    *,
    entry_id: str = "e-ao-http",
    source_ref: str = "tetris-collection/Tetris.zip",
    size: int = 4096,
) -> ReviewEntry:
    return ReviewEntry(
        id=entry_id,
        report_id="r1",
        ordinal=0,
        filename="Tetris.zip",
        size=size,
        decision="accept",
        resolution=ResolutionState.READY,
        selected_source=DownloadSource.ARCHIVE_ORG_HTTP.value,
        selected_source_ref=source_ref,
    )


def _ao_torrent_entry(
    *,
    entry_id: str = "e-ao-torrent",
    source_ref: str = "nes-vault/Tetris (World).zip",
    size: int = 8192,
) -> ReviewEntry:
    return ReviewEntry(
        id=entry_id,
        report_id="r1",
        ordinal=1,
        filename="Tetris (World).zip",
        size=size,
        decision="accept",
        resolution=ResolutionState.READY,
        selected_source=DownloadSource.ARCHIVE_ORG_TORRENT.value,
        selected_source_ref=source_ref,
    )


def _minerva_entry(
    *,
    entry_id: str = "e-minerva",
    file_id: int = 42,
    size: int = 1024,
) -> ReviewEntry:
    return ReviewEntry(
        id=entry_id,
        report_id="r1",
        ordinal=2,
        filename="Super Mario Bros (World).zip",
        size=size,
        decision="accept",
        resolution=ResolutionState.READY,
        selected_file_id=file_id,
        selected_source=DownloadSource.MINERVA_TORRENT.value,
    )


def _library_item(
    *,
    file_id: int = 42,
    basename: str = "Super Mario Bros (World).zip",
    system: str = "Nintendo - Nintendo Entertainment System",
    size: int = 102400,
) -> LibraryItem:
    """Factory for a LibraryItem matching what get_files_by_ids returns."""
    return LibraryItem(
        id=file_id,
        stem="super mario bros (world)",
        basename=basename,
        collection="Nintendo",
        system=system,
        size=size,
        source_torrent="test_torrent.torrent",
        source_index=1,
        path_in_torrent=basename,
    )


# ── Tests: Archive.org HTTP routing ─────────────────────────────────────────


class TestQueueEntriesRoutesArchiveOrgHttp:
    """queue_entries routes Archive.org HTTP entries with correct metadata."""

    def test_source_and_file_id_and_source_ref(self, service, captured_controller):
        """Archive.org HTTP record has source=archive_org_http, file_id=0,
        source_ref set, expected_size set."""
        entry = _ao_http_entry(size=4096)
        result = service.queue_entries([entry])

        assert result.added == 1
        assert len(captured_controller.calls) == 1
        call = captured_controller.calls[0]

        assert call["source"] == DownloadSource.ARCHIVE_ORG_HTTP.value
        assert call["file_id"] == 0
        assert call["source_ref"] == "tetris-collection/Tetris.zip"
        assert call["expected_size"] == 4096

    def test_destination_derived_from_source_ref(self, service, captured_controller):
        """Destination basename is extracted from source_ref."""
        entry = _ao_http_entry(source_ref="my-item/Special Game (USA).zip")
        service.queue_entries([entry])

        call = captured_controller.calls[0]
        assert "Special Game (USA).zip" in call["destination"]

    def test_report_entry_id_propagated(self, service, captured_controller):
        """The entry's id is passed as report_entry_id."""
        entry = _ao_http_entry(entry_id="custom-entry-id")
        service.queue_entries([entry])

        assert captured_controller.calls[0]["report_entry_id"] == "custom-entry-id"

    def test_report_id_propagated(self, service, captured_controller):
        """The entry's report_id is passed through."""
        entry = _ao_http_entry()
        entry.report_id = "report-abc"
        service.queue_entries([entry])

        assert captured_controller.calls[0]["report_id"] == "report-abc"

    def test_no_torrent_url_for_http(self, service, captured_controller):
        """HTTP source should not set torrent_url or torrent_member_path."""
        service.queue_entries([_ao_http_entry()])

        call = captured_controller.calls[0]
        assert call.get("torrent_url") is None
        assert call.get("torrent_member_path") is None


# ── Tests: Archive.org torrent routing ──────────────────────────────────────


class TestQueueEntriesRoutesArchiveOrgTorrent:
    """queue_entries routes Archive.org torrent entries with correct metadata."""

    def test_source_and_file_id_and_source_ref(self, service, captured_controller):
        """Archive.org torrent record has source=archive_org_torrent, file_id=0,
        source_ref set, expected_size set."""
        entry = _ao_torrent_entry(size=8192)
        result = service.queue_entries([entry])

        assert result.added == 1
        assert len(captured_controller.calls) == 1
        call = captured_controller.calls[0]

        assert call["source"] == DownloadSource.ARCHIVE_ORG_TORRENT.value
        assert call["file_id"] == 0
        assert call["source_ref"] == "nes-vault/Tetris (World).zip"
        assert call["expected_size"] == 8192

    def test_torrent_url_constructed_from_identifier(self, service, captured_controller):
        """torrent_url is built from the identifier (first path segment of source_ref)."""
        entry = _ao_torrent_entry(source_ref="nes-vault/Tetris (World).zip")
        service.queue_entries([entry])

        call = captured_controller.calls[0]
        assert call["torrent_url"] is not None
        assert "nes-vault" in call["torrent_url"]
        assert call["torrent_url"].endswith(".torrent")

    def test_torrent_member_path_extracted(self, service, captured_controller):
        """torrent_member_path is the path after the identifier."""
        entry = _ao_torrent_entry(source_ref="nes-vault/sub/Tetris (World).zip")
        service.queue_entries([entry])

        call = captured_controller.calls[0]
        assert call["torrent_member_path"] == "sub/Tetris (World).zip"

    def test_destination_from_source_ref_basename(self, service, captured_controller):
        """Destination basename comes from the source_ref."""
        entry = _ao_torrent_entry(source_ref="nes-vault/Tetris (World).zip")
        service.queue_entries([entry])

        call = captured_controller.calls[0]
        assert "Tetris (World).zip" in call["destination"]


# ── Tests: Minerva routing ──────────────────────────────────────────────────


class TestQueueEntriesRoutesMinerva:
    """queue_entries routes Minerva entries with file_id and minerva_torrent source."""

    def test_source_and_file_id(self, service, captured_controller, mock_db):
        """Minerva record has source=minerva_torrent and the real file_id."""
        mock_db.get_files_by_ids.return_value = [_library_item(file_id=42)]

        entry = _minerva_entry(file_id=42)
        result = service.queue_entries([entry])

        assert result.added == 1
        assert len(captured_controller.calls) == 1
        call = captured_controller.calls[0]

        assert call["source"] == DownloadSource.MINERVA_TORRENT.value
        assert call["file_id"] == 42
        assert call["source_ref"] is None

    def test_destination_from_file_spec(self, service, captured_controller, mock_db):
        """Destination is built from the file spec's system and basename."""
        mock_db.get_files_by_ids.return_value = [
            _library_item(file_id=42, basename="Super Mario Bros (World).zip")
        ]

        service.queue_entries([_minerva_entry(file_id=42)])

        call = captured_controller.calls[0]
        assert "Super Mario Bros (World).zip" in call["destination"]

    def test_no_expected_size_for_minerva(self, service, captured_controller, mock_db):
        """Minerva entries don't set expected_size (it comes from the torrent)."""
        mock_db.get_files_by_ids.return_value = [_library_item(file_id=42)]

        service.queue_entries([_minerva_entry(file_id=42)])

        call = captured_controller.calls[0]
        assert call.get("expected_size") is None
        assert call.get("torrent_url") is None
        assert call.get("torrent_member_path") is None

    def test_minerva_skipped_when_file_not_found(
        self, service, captured_controller, mock_db
    ):
        """If get_files_by_ids returns no match, the entry is skipped (not queued
        with file_id=0)."""
        mock_db.get_files_by_ids.return_value = []

        result = service.queue_entries([_minerva_entry(file_id=999)])

        assert result.added == 0
        assert len(captured_controller.calls) == 0


# ── Tests: Mixed sources ───────────────────────────────────────────────────


class TestQueueEntriesMixedSources:
    """queue_entries handles mixed-source entries in a single call."""

    def test_mixed_entries_all_queued(
        self, service, captured_controller, mock_db
    ):
        """All three source types are queued in one call."""
        mock_db.get_files_by_ids.return_value = [_library_item(file_id=42)]

        entries = [
            _minerva_entry(entry_id="e1", file_id=42),
            _ao_http_entry(entry_id="e2"),
            _ao_torrent_entry(entry_id="e3"),
        ]
        result = service.queue_entries(entries)

        assert result.added == 3
        assert len(captured_controller.calls) == 3

        sources = [c["source"] for c in captured_controller.calls]
        assert DownloadSource.MINERVA_TORRENT.value in sources
        assert DownloadSource.ARCHIVE_ORG_HTTP.value in sources
        assert DownloadSource.ARCHIVE_ORG_TORRENT.value in sources

    def test_two_ao_http_same_basename_different_source_ref(
        self, service, captured_controller
    ):
        """Two Archive.org HTTP entries with same destination basename but
        different source_ref produce distinct queue records."""
        entries = [
            _ao_http_entry(
                entry_id="e1",
                source_ref="collection-a/Game.zip",
                size=100,
            ),
            _ao_http_entry(
                entry_id="e2",
                source_ref="collection-b/Game.zip",
                size=200,
            ),
        ]
        result = service.queue_entries(entries)

        assert result.added == 2
        assert len(captured_controller.calls) == 2
        assert captured_controller.calls[0]["source_ref"] == "collection-a/Game.zip"
        assert captured_controller.calls[1]["source_ref"] == "collection-b/Game.zip"
        assert captured_controller.calls[0]["record_id"] != captured_controller.calls[1]["record_id"]


# ── Tests: Approval filtering ───────────────────────────────────────────────


class TestQueueEntriesFiltering:
    """queue_entries filters by is_approved."""

    def test_pending_entries_skipped(self, service, captured_controller):
        """Entries with decision='pending' are not queued."""
        entry = _ao_http_entry()
        entry.decision = "pending"
        result = service.queue_entries([entry])

        assert result.added == 0
        assert len(captured_controller.calls) == 0

    def test_rejected_entries_skipped(self, service, captured_controller):
        """Entries with decision='reject' are not queued."""
        entry = _ao_http_entry()
        entry.decision = "reject"
        result = service.queue_entries([entry])

        assert result.added == 0

    def test_fuzzy_entries_queued(self, service, captured_controller):
        """Entries with decision='fuzzy' ARE queued (fuzzy is approved)."""
        entry = _ao_http_entry()
        entry.decision = "fuzzy"
        result = service.queue_entries([entry])

        assert result.added == 1
        assert len(captured_controller.calls) == 1

    def test_is_approved_predicate(self):
        """Directly verify is_approved matches the queue_entries filter."""
        for decision in ("accept", "fuzzy"):
            entry = ReviewEntry(
                id="x", report_id="r", ordinal=0, filename="f.zip", size=1,
                decision=decision,
            )
            assert is_approved(entry) is True

        for decision in ("pending", "reject", ""):
            entry = ReviewEntry(
                id="x", report_id="r", ordinal=0, filename="f.zip", size=1,
                decision=decision,
            )
            assert is_approved(entry) is False


# ── Tests: Fallback (no controller) ─────────────────────────────────────────


class TestQueueEntriesFallbackNoController:
    """When no download_controller is set, queue_entries writes directly to state."""

    def test_fallback_writes_to_state(self, tmp_state, mock_db):
        """Without a controller, the queue record is persisted via save_queue_record."""
        svc = ReportAcquisitionService(
            state=tmp_state,
            db=mock_db,
            download_controller=None,
            output_dir=Path("/tmp/test_downloads"),
        )
        entry = _ao_http_entry(size=4096)
        result = svc.queue_entries([entry])

        assert result.added == 1
        records = tmp_state.list_queue()
        assert len(records) == 1
        rec = records[0]
        assert rec.source == DownloadSource.ARCHIVE_ORG_HTTP.value
        assert rec.file_id == 0
        assert rec.source_ref == "tetris-collection/Tetris.zip"
        assert rec.expected_size == 4096

    def test_fallback_torrent_metadata(self, tmp_state, mock_db):
        """Fallback path persists torrent_url and torrent_member_path."""
        svc = ReportAcquisitionService(
            state=tmp_state,
            db=mock_db,
            download_controller=None,
            output_dir=Path("/tmp/test_downloads"),
        )
        entry = _ao_torrent_entry(source_ref="nes-vault/Tetris (World).zip")
        svc.queue_entries([entry])

        records = tmp_state.list_queue()
        assert len(records) == 1
        rec = records[0]
        assert rec.source == DownloadSource.ARCHIVE_ORG_TORRENT.value
        assert rec.torrent_url is not None
        assert "nes-vault" in rec.torrent_url
        assert rec.torrent_member_path == "Tetris (World).zip"
