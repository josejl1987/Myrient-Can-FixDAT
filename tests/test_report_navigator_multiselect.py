"""Tests for ReportNavigator ExtendedSelection + selection_changed signal."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PyQt6 import QtCore, QtWidgets

_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from minerva.domain.reports import ReportSummary
from minerva.ui.widgets.report_navigator import ReportNavigator


def _make_report(rid: str, name: str = "R") -> ReportSummary:
    return ReportSummary(
        id=rid, path=f"{rid}.dat", name=name,
        collection="Nintendo", system="Nintendo - Game Boy Color",
        imported_at=datetime.now(timezone.utc).isoformat(),
        requested_count=5, status="reviewed",
    )


@pytest.fixture()
def nav(qtbot):
    navigator = ReportNavigator()
    qtbot.addWidget(navigator)
    return navigator


def test_selection_mode_is_extended(nav):
    assert nav.view.selectionMode() == QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection


def test_current_report_changed_fires_on_focus_not_on_ctrl_click(nav, qtbot):
    nav.set_reports([_make_report("a"), _make_report("b"), _make_report("c")])
    received_focus = []
    nav.current_report_changed.connect(lambda r: received_focus.append(r.id if r else None))

    # set_reports already focused row 0; now move focus to row 2
    nav.view.setCurrentIndex(nav.proxy.index(2, 0))
    assert received_focus == ["c"]

    # Now add row 0 to the selection (Ctrl-click style) without moving focus
    received_focus.clear()
    nav.view.selectionModel().select(
        nav.proxy.index(0, 0),
        QtCore.QItemSelectionModel.SelectionFlag.Select,
    )
    # current_report_changed should NOT fire — focus stayed on c
    assert received_focus == []


def test_selection_changed_emits_all_selected(nav, qtbot):
    nav.set_reports([_make_report("a"), _make_report("b"), _make_report("c")])
    received: list[list[str]] = []
    nav.selection_changed.connect(lambda reports: received.append([r.id for r in reports]))

    # Select a + c
    sm = nav.view.selectionModel()
    sm.select(nav.proxy.index(0, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)
    sm.select(nav.proxy.index(2, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)

    assert received[-1] == ["a", "c"]


def test_selected_reports_returns_summary_list(nav, qtbot):
    nav.set_reports([_make_report("a"), _make_report("b")])
    sm = nav.view.selectionModel()
    sm.select(nav.proxy.index(0, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)
    sm.select(nav.proxy.index(1, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)
    selected = nav.selected_reports()
    assert {r.id for r in selected} == {"a", "b"}


def test_focus_preserved_when_selection_grows(nav, qtbot):
    nav.set_reports([_make_report("a"), _make_report("b"), _make_report("c")])
    nav.view.setCurrentIndex(nav.proxy.index(0, 0))  # focus a
    # Add c to selection without changing focus
    nav.view.selectionModel().select(
        nav.proxy.index(2, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)
    assert nav.current_report() is not None
    assert nav.current_report().id == "a"
