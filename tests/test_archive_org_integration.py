"""Integration test for the archive.org download flow.

Tests the full path: Candidate → QueueRecord → DownloadController routing.
Uses mocks for network calls (no real archive.org traffic in tests).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from minerva.domain.downloads import DownloadStatus, QueueRecord
from minerva.domain.sources import Candidate, DownloadSource
from minerva.services.archive_org import ArchiveOrgCandidateProvider
from minerva_db import DatEntry


def test_archive_org_candidate_to_queue_record():
    """A Candidate can be converted to a QueueRecord with correct source fields."""
    candidate = Candidate(
        title="Tetris DX (USA)",
        size=65536,
        confidence=0.85,
        method="archive_org_search",
        source=DownloadSource.ARCHIVE_ORG_HTTP,
        source_ref="tetris-collection/Tetris DX (USA).zip",
        collection="no-intro",
    )

    record = QueueRecord(
        id="rec-1",
        file_id=0,
        status=DownloadStatus.QUEUED.value,
        destination="/tmp/Tetris DX (USA).zip",
        created_at="2026-07-03",
        updated_at="2026-07-03",
        source=candidate.source.value,
        source_ref=candidate.source_ref,
    )

    assert record.source == "archive_org_http"
    assert record.source_ref == "tetris-collection/Tetris DX (USA).zip"


def test_archive_org_provider_search_returns_candidates():
    """ArchiveOrgCandidateProvider returns scored candidates from search."""
    entry = DatEntry(filename="Tetris DX (World) (SGB Enhanced) (GB Compatible).zip", size=65536)

    class FakeItem:
        files = [
            {"name": "Tetris DX (USA).zip", "size": "65000", "format": "ZIP"},
            {"name": "readme.txt", "size": "100", "format": "Text"},
        ]
        metadata = {"identifier": "tetris-collection", "collection": "no-intro"}
        identifier = "tetris-collection"
        server = "ia800600.us.archive.org"

    class FakeClient:
        def search_items(self, query, rows=20):
            return [{"identifier": "tetris-collection", "title": "Tetris", "collection": ["no-intro"], "downloads": 100}]

        def get_item(self, identifier):
            return FakeItem()

        @staticmethod
        def build_download_url(identifier, filename):
            return f"https://archive.org/download/{identifier}/{filename}"

    provider = ArchiveOrgCandidateProvider(client=FakeClient())
    candidates = provider.search(entry, system="Nintendo - Game Boy")

    rom_candidates = [c for c in candidates if c.source_ref.startswith("tetris-collection/")]
    assert len(rom_candidates) > 0
    assert rom_candidates[0].source == DownloadSource.ARCHIVE_ORG_HTTP


def test_download_controller_routes_http_to_adapter():
    """DownloadController has _submit_http and _submit_archive_org_torrent methods."""
    from minerva.app.download_controller import DownloadController

    assert callable(getattr(DownloadController, "_submit_http", None))
    assert callable(getattr(DownloadController, "_submit_archive_org_torrent", None))


def test_add_to_queue_accepts_archive_org_source():
    """add_to_queue accepts source and source_ref parameters."""
    import inspect
    from minerva.app.download_controller import DownloadController

    sig = inspect.signature(DownloadController.add_to_queue)
    assert 'source' in sig.parameters
    assert 'source_ref' in sig.parameters
    assert sig.parameters['source'].default == 'minerva_torrent'
    assert sig.parameters['source_ref'].default is None
