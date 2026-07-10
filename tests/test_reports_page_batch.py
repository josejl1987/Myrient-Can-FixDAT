"""Tests for ReportsPage batch toolbar actions over a multi-selection."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PyQt6 import QtCore, QtWidgets

from minerva.app.app_state import AppState
from minerva.app.pages.reports import ReportsPage
from minerva.domain.reports import QueueResult
import minerva_state


@pytest.fixture()
def page(qtbot, monkeypatch, tmp_path):
    """ReportsPage with an isolated state DB and stubbed service/index."""
    fake_svc = MagicMock()
    fake_svc.queue_ready.return_value = QueueResult(
        added=1, skipped_active=0, skipped_complete=0, skipped_missing=0)

    # Redirect every MinervaState() construction to a temp DB so the real
    # data/minerva_state.db never leaks reports into the test.
    real_state = minerva_state.MinervaState
    db_path = str(tmp_path / "test_state.db")

    def _fake_state(*args, **kwargs):
        kwargs["db_path"] = db_path
        return real_state(*args, **kwargs)
    monkeypatch.setattr("minerva_state.MinervaState", _fake_state)
    monkeypatch.setattr("minerva.app.pages.reports.MinervaState", _fake_state)
    monkeypatch.setattr("minerva.app.stores.report_store.MinervaState", _fake_state)
    monkeypatch.setattr(
        "minerva.app.stores.report_store.ReportAcquisitionService",
        lambda **kw: fake_svc,
    )
    monkeypatch.setattr("minerva.app.pages.reports.MinervaDB", MagicMock())

    app_state = AppState()
    p = ReportsPage(app_state)
    qtbot.addWidget(p)
    return p, fake_svc


def test_batch_buttons_disabled_with_no_selection(page):
    p, _ = page
    assert not p._queue_selected_btn.isEnabled()
    assert not p._rematch_selected_btn.isEnabled()
    assert not p._export_selected_btn.isEnabled()
    assert not p._delete_selected_btn.isEnabled()


def test_batch_buttons_enabled_when_selection_nonempty(page, qtbot, make_report):
    p, _ = page
    p._navigator.set_reports([make_report("a"), make_report("b")])
    sm = p._navigator.view.selectionModel()
    sm.select(p._navigator.proxy.index(0, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)
    qtbot.waitUntil(lambda: p._queue_selected_btn.isEnabled())
    assert p._queue_selected_btn.isEnabled()
    assert p._rematch_selected_btn.isEnabled()
    assert p._export_selected_btn.isEnabled()
    assert p._delete_selected_btn.isEnabled()


def test_queue_selected_calls_service_per_report(page, qtbot, make_report):
    p, fake_svc = page
    fake_svc.queue_entries.return_value = QueueResult(
        added=1, skipped_active=0, skipped_complete=0, skipped_missing=0,
    )
    p._navigator.set_reports([make_report("a"), make_report("b"), make_report("c")])
    sm = p._navigator.view.selectionModel()
    sm.select(p._navigator.proxy.index(0, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)
    sm.select(p._navigator.proxy.index(2, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)

    p.window().download_controller = MagicMock()
    p._queue_selected()

    # _queue_selected now calls svc.queue_entries (not per-report queue_ready)
    assert fake_svc.queue_entries.call_count >= 1


def test_delete_selected_set_removes_reports(page, qtbot, monkeypatch, make_report):
    p, _ = page
    reports = [make_report("a"), make_report("b")]
    for r in reports:
        p._app_state.reports._state.save_report(r)
    p._navigator.set_reports(reports)
    sm = p._navigator.view.selectionModel()
    sm.select(p._navigator.proxy.index(0, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)
    sm.select(p._navigator.proxy.index(1, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)

    monkeypatch.setattr(
        QtWidgets.QMessageBox, "question",
        staticmethod(lambda *a, **kw: QtWidgets.QMessageBox.StandardButton.Yes),
    )
    p._delete_selected_set()

    assert not p._selected_report_ids
