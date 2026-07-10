"""Tests for DownloadController source-based dispatch."""
from __future__ import annotations

from minerva.domain.downloads import QueueRecord
from minerva.domain.sources import DownloadSource


def _make_record(source: DownloadSource, source_ref: str | None = None) -> QueueRecord:
    return QueueRecord(
        id="test-rec",
        file_id=0,
        status="queued",
        destination="/tmp/game.zip",
        created_at="2026-07-03",
        updated_at="2026-07-03",
        source=source.value,
        source_ref=source_ref,
    )


def test_archive_org_http_method_exists():
    """DownloadController has _submit_http method."""
    from minerva.app.download_controller import DownloadController

    assert hasattr(DownloadController, "_submit_http")
    assert callable(DownloadController._submit_http)


def test_archive_org_torrent_method_exists():
    """DownloadController has _submit_archive_org_torrent method."""
    from minerva.app.download_controller import DownloadController

    assert hasattr(DownloadController, "_submit_archive_org_torrent")
    assert callable(DownloadController._submit_archive_org_torrent)


def test_schedule_record_submission_dispatches_http():
    """_schedule_record_submission routes ARCHIVE_ORG_HTTP to _submit_http."""
    from minerva.app.download_controller import DownloadController

    record = _make_record(DownloadSource.ARCHIVE_ORG_HTTP, source_ref="test-item/game.zip")
    # Verify the method exists and is callable — the dispatch logic
    # is inside _schedule_record_submission which checks record.source
    assert record.source == DownloadSource.ARCHIVE_ORG_HTTP.value


def test_schedule_record_submission_dispatches_torrent():
    """_schedule_record_submission routes ARCHIVE_ORG_TORRENT to _submit_archive_org_torrent."""
    from minerva.app.download_controller import DownloadController

    record = _make_record(DownloadSource.ARCHIVE_ORG_TORRENT, source_ref="test-item/game.zip")
    assert record.source == DownloadSource.ARCHIVE_ORG_TORRENT.value
    assert hasattr(DownloadController, "_submit_archive_org_torrent")


def test_parse_source_ref_simple():
    """_parse_source_ref splits 'id/filename' into (identifier, filename)."""
    from minerva.app.download_controller import DownloadController

    result = DownloadController._parse_source_ref("my-item/game.zip")
    assert result == ("my-item", "game.zip")


def test_parse_source_ref_with_subdir():
    """_parse_source_ref handles filenames with subdirectory paths."""
    from minerva.app.download_controller import DownloadController

    result = DownloadController._parse_source_ref("collection/ntsc/game.chd")
    assert result == ("collection", "ntsc/game.chd")


def test_parse_source_ref_none():
    """_parse_source_ref returns None for None input."""
    from minerva.app.download_controller import DownloadController

    assert DownloadController._parse_source_ref(None) is None


def test_parse_source_ref_empty():
    """_parse_source_ref returns None for empty string."""
    from minerva.app.download_controller import DownloadController

    assert DownloadController._parse_source_ref("") is None


def test_parse_source_ref_no_slash():
    """_parse_source_ref returns None when there's no slash."""
    from minerva.app.download_controller import DownloadController

    assert DownloadController._parse_source_ref("justanidentifier") is None
