"""Tests for ReportsPage batch toolbar actions over a multi-selection."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PyQt6 import QtCore, QtWidgets

_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from minerva.app.app_state import AppState
from minerva.app.pages.reports import ReportsPage
from minerva.domain.reports import QueueResult, ReportSummary
import minerva_state


def _make_report(rid: str) -> ReportSummary:
    return ReportSummary(
        id=rid, path=f"{rid}.dat", name=f"Report-{rid}",
        collection="Nintendo", system="Nintendo - Game Boy Color",
        imported_at=datetime.now(timezone.utc).isoformat(),
        requested_count=3, status="reviewed",
    )


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
        "minerva.app.pages.reports.ReportAcquisitionService",
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


def test_batch_buttons_enabled_when_selection_nonempty(page, qtbot):
    p, _ = page
    p._navigator.set_reports([_make_report("a"), _make_report("b")])
    sm = p._navigator.view.selectionModel()
    sm.select(p._navigator.proxy.index(0, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)
    qtbot.waitUntil(lambda: p._queue_selected_btn.isEnabled())
    assert p._queue_selected_btn.isEnabled()
    assert p._rematch_selected_btn.isEnabled()
    assert p._export_selected_btn.isEnabled()
    assert p._delete_selected_btn.isEnabled()


def test_queue_selected_calls_service_per_report(page, qtbot):
    p, fake_svc = page
    p._navigator.set_reports([_make_report("a"), _make_report("b"), _make_report("c")])
    sm = p._navigator.view.selectionModel()
    sm.select(p._navigator.proxy.index(0, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)
    sm.select(p._navigator.proxy.index(2, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)

    p.window().download_controller = MagicMock()
    p._queue_selected()

    called_ids = [call.args[0] for call in fake_svc.queue_ready.call_args_list]
    assert set(called_ids) == {"a", "c"}
    assert fake_svc.queue_ready.call_count == 2


def test_delete_selected_set_removes_reports(page, qtbot, monkeypatch):
    p, _ = page
    reports = [_make_report("a"), _make_report("b")]
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
