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


def test_render_match_results_exists():
    """_render_match_results method exists on MatchDetailPanel."""
    from minerva.ui.widgets.match_detail_panel import MatchDetailPanel

    assert hasattr(MatchDetailPanel, "_render_match_results")
    assert callable(MatchDetailPanel._render_match_results)


def test_match_search_task_class(qtbot):
    """_MatchSearchTask is a QRunnable that can be instantiated."""
    from unittest.mock import MagicMock

    from minerva.ui.widgets.match_detail_panel import _MatchSearchTask, _TaskSignals

    task = _MatchSearchTask(MagicMock(), MagicMock(), MagicMock(), "", None)
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

    from minerva.app.app_state import AppState
    from minerva.ui.widgets.match_detail_panel import MatchDetailPanel

    app_state = MagicMock(spec=AppState)
    panel = MatchDetailPanel(app_state)
    assert panel._archive_org_provider is not None


def test_render_match_results_shows_archive_org_unavailable(qtbot):
    """When archive_org_status is error/timeout, a footer label is shown."""
    from unittest.mock import MagicMock

    from minerva.app.app_state import AppState
    from minerva.ui.widgets.match_detail_panel import MatchDetailPanel

    app_state = MagicMock(spec=AppState)
    panel = MatchDetailPanel(app_state)

    panel._render_match_results(
        {"minerva": [], "archive_org": [], "archive_org_status": "timeout"},
        None,
        "10 KB",
    )
    text = panel._candidates_detail.text()
    assert "Archive.org timed out" in text

    panel._render_match_results(
        {"minerva": [], "archive_org": [], "archive_org_status": "error"},
        None,
        "10 KB",
    )
    text = panel._candidates_detail.text()
    assert "Archive.org unavailable" in text
