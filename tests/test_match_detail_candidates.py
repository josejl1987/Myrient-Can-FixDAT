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


def test_build_minerva_candidates_html_exists():
    """_build_minerva_candidates_html method exists on MatchDetailPanel."""
    from minerva.ui.widgets.match_detail_panel import MatchDetailPanel

    assert hasattr(MatchDetailPanel, "_build_minerva_candidates_html")
    assert callable(MatchDetailPanel._build_minerva_candidates_html)


def test_fetch_archive_org_candidates_exists():
    """_fetch_archive_org_candidates method exists on MatchDetailPanel."""
    from minerva.ui.widgets.match_detail_panel import MatchDetailPanel

    assert hasattr(MatchDetailPanel, "_fetch_archive_org_candidates")
    assert callable(MatchDetailPanel._fetch_archive_org_candidates)

def test_archive_org_search_task_class(qtbot):
    """_ArchiveOrgSearchTask is a QRunnable that can be instantiated."""
    from minerva.ui.widgets.match_detail_panel import _ArchiveOrgSearchTask, _TaskSignals
    from unittest.mock import MagicMock

    task = _ArchiveOrgSearchTask(MagicMock(), MagicMock(), "")
    assert isinstance(task.signals, _TaskSignals)

def test_task_signals_class():
    """_TaskSignals has succeeded, failed, finished signals."""
    from minerva.ui.widgets.match_detail_panel import _TaskSignals

    assert hasattr(_TaskSignals, "succeeded")
    assert hasattr(_TaskSignals, "failed")
    assert hasattr(_TaskSignals, "finished")


def test_archive_org_provider_initialized(qtbot):
    """MatchDetailPanel initializes _archive_org_provider."""
    from unittest.mock import MagicMock
    from minerva.ui.widgets.match_detail_panel import MatchDetailPanel
    from minerva.app.app_state import AppState

    app_state = MagicMock(spec=AppState)
    panel = MatchDetailPanel(app_state)
    assert panel._archive_org_provider is not None
