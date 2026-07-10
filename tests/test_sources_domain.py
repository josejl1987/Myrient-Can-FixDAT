"""Tests for the download-source domain types."""
from __future__ import annotations

from minerva.domain.sources import (
    Candidate,
    CandidateProvider,
    DownloadSource,
)
from minerva_db import DatEntry


def test_download_source_enum_values():
    assert DownloadSource.MINERVA_TORRENT.value == "minerva_torrent"
    assert DownloadSource.ARCHIVE_ORG_TORRENT.value == "archive_org_torrent"
    assert DownloadSource.ARCHIVE_ORG_HTTP.value == "archive_org_http"


def test_candidate_creation():
    c = Candidate(
        title="Tetris DX",
        size=65536,
        confidence=0.95,
        method="fuzzy",
        source=DownloadSource.ARCHIVE_ORG_HTTP,
        source_ref="psx-tetris/Tetris DX (USA).zip",
        collection="no-intro",
        system="Nintendo - Game Boy",
    )
    assert c.source == DownloadSource.ARCHIVE_ORG_HTTP
    assert c.seeders is None
    assert c.torrent_url is None
    assert c.reasons == []


def test_candidate_provider_protocol_is_runtime_checkable():
    """Any class with a compatible search() method satisfies the protocol."""

    class FakeProvider:
        def search(self, entry, system=None):
            return []

    provider = FakeProvider()
    assert isinstance(provider, CandidateProvider)
