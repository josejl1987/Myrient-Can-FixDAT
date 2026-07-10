"""Tests for ReportNavigator ExtendedSelection + selection_changed signal."""

from __future__ import annotations

import pytest
from PyQt6 import QtCore, QtWidgets

from minerva.ui.widgets.report_navigator import ReportNavigator


@pytest.fixture()
def nav(qtbot):
    navigator = ReportNavigator()
    qtbot.addWidget(navigator)
    return navigator


def test_selection_mode_is_extended(nav):
    assert nav.view.selectionMode() == QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection


def test_current_report_changed_fires_on_focus_not_on_ctrl_click(nav, qtbot, make_report):
    nav.set_reports([make_report("a"), make_report("b"), make_report("c")])
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


def test_selection_changed_emits_all_selected(nav, qtbot, make_report):
    nav.set_reports([make_report("a"), make_report("b"), make_report("c")])
    received: list[list[str]] = []
    nav.selection_changed.connect(lambda reports: received.append([r.id for r in reports]))

    # Select a + c
    sm = nav.view.selectionModel()
    sm.select(nav.proxy.index(0, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)
    sm.select(nav.proxy.index(2, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)

    assert received[-1] == ["a", "c"]


def test_selected_reports_returns_summary_list(nav, qtbot, make_report):
    nav.set_reports([make_report("a"), make_report("b")])
    sm = nav.view.selectionModel()
    sm.select(nav.proxy.index(0, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)
    sm.select(nav.proxy.index(1, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)
    selected = nav.selected_reports()
    assert {r.id for r in selected} == {"a", "b"}


def test_focus_preserved_when_selection_grows(nav, qtbot, make_report):
    nav.set_reports([make_report("a"), make_report("b"), make_report("c")])
    nav.view.setCurrentIndex(nav.proxy.index(0, 0))  # focus a
    # Add c to selection without changing focus
    nav.view.selectionModel().select(
        nav.proxy.index(2, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)
    assert nav.current_report() is not None
    assert nav.current_report().id == "a"
