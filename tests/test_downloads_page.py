"""
Tests for ``DownloadsPage`` — model/view wiring and signal connections.

Covers: construction, view model chain, column count, delegate registration,
signal wiring, and widget lifecycle.
"""

from __future__ import annotations

from PyQt6 import QtWidgets
from PyQt6.QtCore import QSortFilterProxyModel as SortFilterProxy

from minerva.app.app_state import AppState
from minerva.app.pages import DownloadPageState, DownloadsPage
from minerva.app.worker_manager import WorkerManager
from minerva.domain.downloads import DownloadStatus
from minerva.ui.models.download_delegates import (
    DownloadMoreDelegate,
    DownloadNameDelegate,
    DownloadProgressDelegate,
    DownloadStatusDelegate,
)
from minerva.ui.models.download_model import DownloadTableModel

# ---------------------------------------------------------------------------
# ModelTester availability
# ---------------------------------------------------------------------------
try:
    from PyQt6.QtTest import QAbstractItemModelTester

    _HAVE_MODEL_TESTER = True
except ImportError:
    _HAVE_MODEL_TESTER = False


# ============================================================================
# Fixtures
# ============================================================================


def _make_app_state() -> AppState:
    """Create an ``AppState`` with a real ``WorkerManager`` wired."""
    state = AppState()
    state.worker_manager = WorkerManager()
    return state


def _make_page(app_state: AppState | None = None) -> DownloadsPage:
    """Create a ``DownloadsPage``, optionally with *app_state*."""
    return DownloadsPage(app_state or _make_app_state())


# ============================================================================
# Construction
# ============================================================================


def test_page_constructs(qtbot):
    """GIVEN an AppState with WorkerManager WHEN DownloadsPage is
    constructed THEN no exception is raised."""
    page = _make_page()
    qtbot.addWidget(page)
    assert isinstance(page, QtWidgets.QWidget)


def test_view_model_is_proxy(qtbot):
    """GIVEN a constructed DownloadsPage WHEN the view's model is
    queried THEN it returns a SortFilterProxy."""
    page = _make_page()
    qtbot.addWidget(page)
    proxy = page._view.model()
    assert isinstance(proxy, SortFilterProxy)


def test_proxy_source_is_download_model(qtbot):
    """GIVEN a constructed DownloadsPage WHEN the proxy's source model
    is queried THEN it returns a TorrentGroupTreeModel."""
    page = _make_page()
    qtbot.addWidget(page)
    proxy = page._view.model()
    source = proxy.sourceModel()
    from minerva.ui.models.torrent_group import TorrentGroupTreeModel
    assert isinstance(source, TorrentGroupTreeModel), f"Expected TorrentGroupTreeModel, got {type(source).__name__}"
    assert hasattr(source, "_groups"), "Source model should have _groups"


def test_column_count_matches_spec(qtbot):
    """GIVEN a constructed DownloadsPage WHEN the source model's column
    count is queried THEN it equals 9."""
    page = _make_page()
    qtbot.addWidget(page)
    proxy = page._view.model()
    source = proxy.sourceModel()
    assert source.columnCount() == 9


def test_delegates_assigned(qtbot):
    """The queue uses rich identity, status, progress and overflow delegates."""
    page = _make_page()
    qtbot.addWidget(page)

    assert isinstance(page._view.itemDelegateForColumn(0), DownloadNameDelegate)
    assert isinstance(page._view.itemDelegateForColumn(1), DownloadStatusDelegate)
    assert isinstance(page._view.itemDelegateForColumn(2), DownloadProgressDelegate)
    assert isinstance(page._view.itemDelegateForColumn(8), DownloadMoreDelegate)


def test_overflow_delegate_connected(qtbot):
    """The overflow delegate opens the contextual row menu."""
    page = _make_page()
    qtbot.addWidget(page)
    receivers = page._more_delegate.receivers(page._more_delegate.menu_requested)
    assert receivers > 0


# ============================================================================
# Initial state
# ============================================================================


def test_initial_state_empty(qtbot):
    """GIVEN a constructed DownloadsPage WHEN the initial state is
    checked THEN it is EMPTY."""
    page = _make_page()
    qtbot.addWidget(page)
    assert page._content_state.currentIndex() == 1  # EMPTY (ContentState: 0=loading, 1=empty)


def test_starts_with_no_records(qtbot):
    """GIVEN a constructed DownloadsPage WHEN the model row count is
    queried THEN it is zero."""
    page = _make_page()
    qtbot.addWidget(page)
    assert page._model.rowCount() == 0


# ============================================================================
# Signal wiring
# ============================================================================


def test_manager_downloads_changed_connected(qtbot):
    """GIVEN a DownloadsPage with a WorkerManager WHEN the manager
    emits downloads_changed THEN the model is updated."""
    app_state = _make_app_state()
    page = _make_page(app_state)
    qtbot.addWidget(page)

    app_state.worker_manager.add_to_queue("test.zip", "https://example.com/test.zip")

    qtbot.wait(50)

    assert page._model.rowCount() == 1
    assert page._content_state.currentIndex() == 3  # CONTENT (ContentState: 3=content)


def test_manager_downloads_changed_clear(qtbot):
    """GIVEN a DownloadsPage with records WHEN the manager has no
    downloads THEN the model is empty and state is EMPTY."""
    app_state = _make_app_state()
    page = _make_page(app_state)
    qtbot.addWidget(page)

    app_state.worker_manager.add_to_queue("test.zip", "https://example.com/test.zip")
    app_state.worker_manager.cancel(0)

    qtbot.wait(50)

    assert page._model.rowCount() == 1

    app_state.worker_manager._downloads.clear()
    app_state.worker_manager._active.clear()
    app_state.worker_manager._emit_downloads_changed()
    qtbot.wait(50)

    assert page._model.rowCount() == 0
    assert page._content_state.currentIndex() == 1  # EMPTY (ContentState: 1=empty)


# ============================================================================
# ModelTester
# ============================================================================


def test_modeltester_on_source(qtbot):
    """GIVEN a constructed DownloadsPage WHEN QAbstractItemModelTester
    checks the source model THEN no warnings are emitted."""
    page = _make_page()
    qtbot.addWidget(page)
    proxy = page._view.model()
    source = proxy.sourceModel()

    if _HAVE_MODEL_TESTER:
        tester = QAbstractItemModelTester(source)  # noqa: F841
    else:
        assert source.rowCount() == 0
        assert source.columnCount() == 9
        from PyQt6.QtCore import QModelIndex

        assert source.data(QModelIndex()) is None


# ============================================================================
# Widget lifecycle
# ============================================================================


def test_wait_exposed(qtbot):
    """GIVEN a constructed DownloadsPage WHEN shown and waitExposed is
    called THEN the page renders without error."""
    page = _make_page()
    qtbot.addWidget(page)
    page.show()
    qtbot.waitExposed(page)


# ============================================================================
# Action dispatch
# ============================================================================


def test_on_action_pause(qtbot, monkeypatch):
    """GIVEN a DownloadsPage with a download WHEN _on_action('pause') is
    called THEN the WorkerManager.pause is invoked."""
    app_state = _make_app_state()
    page = _make_page(app_state)
    qtbot.addWidget(page)

    app_state.worker_manager.add_to_queue("test.zip", "https://example.com/test.zip")
    app_state.worker_manager._downloads[0].status = DownloadStatus.DOWNLOADING
    app_state.worker_manager._active.add(1)

    source_index = page._model.index(0, 7)
    proxy_index = page._proxy.mapFromSource(source_index)

    page._on_action(proxy_index, "pause")
    assert app_state.worker_manager._downloads[0].status == DownloadStatus.PAUSED


def test_on_action_resume(qtbot):
    """GIVEN a DownloadsPage with a paused download WHEN
    _on_action('resume') is called THEN the WorkerManager.resume is
    invoked."""
    app_state = _make_app_state()
    page = _make_page(app_state)
    qtbot.addWidget(page)

    app_state.worker_manager.add_to_queue("test.zip", "https://example.com/test.zip")
    app_state.worker_manager._downloads[0].status = DownloadStatus.PAUSED

    source_index = page._model.index(0, 7)
    proxy_index = page._proxy.mapFromSource(source_index)

    page._on_action(proxy_index, "resume")
    assert app_state.worker_manager._downloads[0].status == DownloadStatus.DOWNLOADING


def test_on_action_remove(qtbot):
    """GIVEN a DownloadsPage with a download WHEN
    _on_action('remove') is called THEN the WorkerManager.cancel is
    invoked."""
    app_state = _make_app_state()
    page = _make_page(app_state)
    qtbot.addWidget(page)

    app_state.worker_manager.add_to_queue("test.zip", "https://example.com/test.zip")

    source_index = page._model.index(0, 7)
    proxy_index = page._proxy.mapFromSource(source_index)

    page._on_action(proxy_index, "remove")
    assert app_state.worker_manager._downloads[0].status == DownloadStatus.CANCELLED


# ============================================================================
# Multi-selection batch operations
# ============================================================================


def test_selected_records_empty(qtbot):
    """GIVEN a DownloadsPage with no selection WHEN _selected_records
    is called THEN an empty list is returned."""
    page = _make_page()
    qtbot.addWidget(page)
    assert page._selected_records() == []


def test_selected_records_single(qtbot):
    """GIVEN a DownloadsPage with one record selected WHEN
    _selected_records is called THEN a list with one record is returned."""
    app_state = _make_app_state()
    page = _make_page(app_state)
    qtbot.addWidget(page)

    app_state.worker_manager.add_to_queue("test.zip", "https://example.com/test.zip")
    page._refresh_from_controller()
    idx = page._proxy.index(0, 0)
    page._view.setCurrentIndex(idx)

    records = page._selected_records()
    assert len(records) == 1
    assert records[0].filename == "test.zip"


def test_pause_selected_pauses_active(qtbot):
    """GIVEN selected downloads with active status WHEN _pause_selected
    is called THEN only active downloads are paused."""
    app_state = _make_app_state()
    page = _make_page(app_state)
    qtbot.addWidget(page)

    app_state.worker_manager.add_to_queue("active.zip", "https://example.com/active.zip")
    app_state.worker_manager.add_to_queue("queued.zip", "https://example.com/queued.zip")
    app_state.worker_manager._downloads[0].status = DownloadStatus.DOWNLOADING
    app_state.worker_manager._active.add(1)
    page._refresh_from_controller()

    page._view.selectAll()
    page._pause_selected()

    assert app_state.worker_manager._downloads[0].status == DownloadStatus.PAUSED
    # Queued download should not be affected by pause
    assert app_state.worker_manager._downloads[1].status != DownloadStatus.PAUSED


def test_resume_selected_resumes_paused(qtbot):
    """GIVEN selected downloads with paused status WHEN _resume_selected
    is called THEN only paused downloads are resumed."""
    app_state = _make_app_state()
    page = _make_page(app_state)
    qtbot.addWidget(page)

    app_state.worker_manager.add_to_queue("paused.zip", "https://example.com/paused.zip")
    app_state.worker_manager.add_to_queue("active.zip", "https://example.com/active.zip")
    app_state.worker_manager._downloads[0].status = DownloadStatus.PAUSED
    app_state.worker_manager._downloads[1].status = DownloadStatus.DOWNLOADING
    app_state.worker_manager._active.add(2)
    page._refresh_from_controller()

    page._view.selectAll()
    page._resume_selected()

    assert app_state.worker_manager._downloads[0].status == DownloadStatus.DOWNLOADING
    # Already-downloading should not be affected by resume
    assert app_state.worker_manager._downloads[1].status == DownloadStatus.DOWNLOADING


def test_toggle_pause_resume_selected_pauses(qtbot):
    """GIVEN selected active downloads WHEN Space is pressed (toggle)
    THEN active downloads are paused."""
    app_state = _make_app_state()
    page = _make_page(app_state)
    qtbot.addWidget(page)

    app_state.worker_manager.add_to_queue("active.zip", "https://example.com/active.zip")
    app_state.worker_manager._downloads[0].status = DownloadStatus.DOWNLOADING
    app_state.worker_manager._active.add(1)
    page._refresh_from_controller()
    idx = page._proxy.index(0, 0)
    page._view.setCurrentIndex(idx)
    page._toggle_pause_resume_selected()

    assert app_state.worker_manager._downloads[0].status == DownloadStatus.PAUSED


def test_toggle_pause_resume_selected_resumes(qtbot):
    """GIVEN selected paused downloads WHEN Space is pressed (toggle)
    THEN paused downloads are resumed."""
    app_state = _make_app_state()
    page = _make_page(app_state)
    qtbot.addWidget(page)

    app_state.worker_manager.add_to_queue("paused.zip", "https://example.com/paused.zip")
    app_state.worker_manager._downloads[0].status = DownloadStatus.PAUSED
    page._refresh_from_controller()
    idx = page._proxy.index(0, 0)
    page._view.setCurrentIndex(idx)
    page._toggle_pause_resume_selected()

    assert app_state.worker_manager._downloads[0].status == DownloadStatus.DOWNLOADING


def test_batch_buttons_disabled_when_no_selection(qtbot):
    """GIVEN a DownloadsPage with records but no selection WHEN
    _on_selection_changed fires THEN batch buttons are disabled."""
    app_state = _make_app_state()
    page = _make_page(app_state)
    qtbot.addWidget(page)

    app_state.worker_manager.add_to_queue("test.zip", "https://example.com/test.zip")
    page._refresh_from_controller()
    page._torrent_engine_connected = True

    # Clear selection
    page._view.clearSelection()
    page._on_selection_changed()

    assert not page._pause_selected_btn.isEnabled()
    assert not page._resume_selected_btn.isEnabled()
    assert not page._cancel_selected_btn.isEnabled()


def test_batch_buttons_enabled_with_selection(qtbot):
    """GIVEN a DownloadsPage with a selected active download and
    native torrent engine connected WHEN _on_selection_changed fires THEN
    pause/remove buttons are enabled."""
    app_state = _make_app_state()
    page = _make_page(app_state)
    qtbot.addWidget(page)

    page._torrent_engine_connected = True  # native torrent engine is connected
    app_state.worker_manager.add_to_queue("test.zip", "https://example.com/test.zip")
    app_state.worker_manager._downloads[0].status = DownloadStatus.DOWNLOADING
    app_state.worker_manager._active.add(1)
    page._refresh_from_controller()
    idx = page._proxy.index(0, 0)
    page._view.setCurrentIndex(idx)
    page._on_selection_changed()

    assert page._pause_selected_btn.isEnabled()
    assert page._cancel_selected_btn.isEnabled()
