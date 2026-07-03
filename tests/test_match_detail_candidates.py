# tests/test_match_detail_candidates.py
"""Tests for merged candidate display with source badges."""
from __future__ import annotations

from minerva.domain.sources import DownloadSource


def test_candidate_source_badge_minerva():
    """Minerva candidates show a blue Minerva badge."""
    from minerva.ui.widgets.match_detail_panel import _source_badge

    badge = _source_badge(DownloadSource.MINERVA_TORRENT)
    assert "Minerva" in badge


def test_candidate_source_badge_archive_torrent():
    """Archive.org torrent candidates show a green torrent badge."""
    from minerva.ui.widgets.match_detail_panel import _source_badge

    badge = _source_badge(DownloadSource.ARCHIVE_ORG_TORRENT)
    assert "archive.org" in badge.lower()
    assert "torrent" in badge.lower() or "🌐" in badge


def test_candidate_source_badge_archive_http():
    """Archive.org HTTP candidates show an orange HTTP badge."""
    from minerva.ui.widgets.match_detail_panel import _source_badge

    badge = _source_badge(DownloadSource.ARCHIVE_ORG_HTTP)
    assert "archive.org" in badge.lower()
    assert "http" in badge.lower() or "⬇" in badge
