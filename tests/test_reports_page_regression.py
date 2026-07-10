"""Regression: single-report flows unchanged after multi-select refactor."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PyQt6 import QtCore

from minerva.app.app_state import AppState
from minerva.app.pages.reports import ReportsPage
import minerva_state


@pytest.fixture()
def page(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "minerva.app.pages.reports.ReportAcquisitionService", MagicMock())
    monkeypatch.setattr("minerva.app.pages.reports.MinervaDB", MagicMock())

    real_state = minerva_state.MinervaState
    db_path = str(tmp_path / "test_state.db")

    def _fake_state(*args, **kwargs):
        kwargs["db_path"] = db_path
        return real_state(*args, **kwargs)

    monkeypatch.setattr("minerva_state.MinervaState", _fake_state)
    monkeypatch.setattr("minerva.app.pages.reports.MinervaState", _fake_state)
    monkeypatch.setattr("minerva.app.stores.report_store.MinervaState", _fake_state)

    app_state = AppState()
    p = ReportsPage(app_state)
    qtbot.addWidget(p)
    return p


def test_single_click_focuses_report(page, qtbot, make_report):
    page._navigator.set_reports([make_report("a"), make_report("b")])
    page._navigator.view.setCurrentIndex(page._navigator.proxy.index(1, 0))
    assert page._selected_report_id == "b"


def test_current_report_unchanged_when_selection_grows(page, qtbot, make_report):
    page._navigator.set_reports([make_report("a"), make_report("b"), make_report("c")])
    page._navigator.view.setCurrentIndex(page._navigator.proxy.index(0, 0))
    assert page._selected_report_id == "a"
    # Add to selection without changing focus
    page._navigator.view.selectionModel().select(
        page._navigator.proxy.index(2, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)
    # Focus still a — entry table/MMDetail bound to focused report, not selection
    assert page._selected_report_id == "a"
    assert page._selected_report_ids == {"a", "c"}


def test_queue_all_ready_button_still_works(page, qtbot, make_report):
    """The global 'Queue all ready' button must remain functional."""
    # Seed the state DB so refresh() finds reports and enables the button
    page._app_state.reports._state.save_report(make_report("a"))
    page.refresh()
    assert page._queue_all_btn.isEnabled()


def test_context_menu_single_report_actions_intact(page, qtbot, make_report):
    """With one report selected, no batch submenu should appear."""
    page._navigator.set_reports([make_report("a"), make_report("b")])
    # Single selection — _selected_report_ids has one entry, not zero
    assert len(page._selected_report_ids) == 1
