"""
Tests for AppSidebar — row count, QAction wiring, active visual.
"""

from __future__ import annotations

from unittest.mock import Mock

from PyQt6 import QtCore, QtGui, QtWidgets

from minerva.app.app_sidebar import AppSidebar, SidebarRow
from minerva.app.page_id import PageId


def _make_actions() -> dict[PageId, QtGui.QAction]:
    """Create a set of QAction stubs for testing (4 sidebar destinations)."""
    result = {}
    for pid in PageId:
        result[pid] = QtGui.QAction(pid.value)
    return result


def test_sidebar_has_four_rows(qtbot):
    """GIVEN an AppSidebar with 4 registered actions WHEN enumerated
    THEN exactly 4 rows exist in order."""
    actions = _make_actions()
    sidebar = AppSidebar(actions)
    qtbot.add_widget(sidebar)

    rows = sidebar.rows
    assert len(rows) == 4

    expected_order = [
        PageId.REPORTS,
        PageId.LIBRARY,
        PageId.DOWNLOADS,
        PageId.SETTINGS,
    ]
    assert list(rows.keys()) == expected_order


def test_sidebar_rows_have_correct_labels(qtbot):
    """GIVEN an AppSidebar WHEN checking row labels THEN each row
    displays the correct human-readable label."""
    actions = _make_actions()
    sidebar = AppSidebar(actions)
    qtbot.add_widget(sidebar)

    expected_labels = {
        PageId.REPORTS: "Fix Reports",
        PageId.LIBRARY: "Library",
        PageId.DOWNLOADS: "Downloads",
        PageId.SETTINGS: "Settings",
    }
    for pid, row in sidebar.rows.items():
        assert row.text() == expected_labels[pid], f"Mismatch for {pid}"


def test_sidebar_fixed_width(qtbot):
    """GIVEN an AppSidebar WHEN constructed THEN its width is 240 px."""
    actions = _make_actions()
    sidebar = AppSidebar(actions)
    qtbot.add_widget(sidebar)

    assert sidebar.width() == 240 or sidebar.minimumWidth() >= 240
    assert sidebar.minimumWidth() == 240


def test_sidebar_row_click_triggers_action(qtbot):
    """GIVEN an AppSidebar with a wired action WHEN the row is clicked
    THEN the corresponding QAction is triggered."""
    actions = _make_actions()
    sidebar = AppSidebar(actions)
    qtbot.add_widget(sidebar)

    mock = Mock()
    actions[PageId.LIBRARY].triggered.connect(mock)

    row = sidebar.rows[PageId.LIBRARY]
    row.click()

    mock.assert_called_once()


def test_set_active_highlights_one_row(qtbot):
    """GIVEN an AppSidebar WHEN set_active(LIBRARY) is called THEN
    only the LIBRARY row reports is_active as True."""
    actions = _make_actions()
    sidebar = AppSidebar(actions)
    qtbot.add_widget(sidebar)

    sidebar.set_active(PageId.LIBRARY)

    for pid, row in sidebar.rows.items():
        if pid == PageId.LIBRARY:
            assert row.is_active, f"{pid} should be active"
        else:
            assert not row.is_active, f"{pid} should not be active"


def test_set_active_switches_active_row(qtbot):
    """GIVEN an AppSidebar with LIBRARY active WHEN set_active(REPORTS)
    is called THEN LIBRARY deactivates and REPORTS activates."""
    actions = _make_actions()
    sidebar = AppSidebar(actions)
    qtbot.add_widget(sidebar)

    sidebar.set_active(PageId.LIBRARY)
    sidebar.set_active(PageId.REPORTS)

    assert sidebar.rows[PageId.REPORTS].is_active
    assert not sidebar.rows[PageId.LIBRARY].is_active


def test_sidebar_header_exists(qtbot):
    """GIVEN an AppSidebar WHEN examining children THEN a QLabel with
    text 'MINERVA' exists."""
    actions = _make_actions()
    sidebar = AppSidebar(actions)
    qtbot.add_widget(sidebar)

    labels = sidebar.findChildren(QtWidgets.QLabel)
    header_texts = [lbl.text() for lbl in labels if lbl.text() == "MINERVA"]
    assert len(header_texts) == 1


def test_sidebar_has_no_tray_toggle(qtbot):
    """GIVEN an AppSidebar WHEN constructed THEN no tray toggle button
    exists — downloads live in the Downloads page."""
    actions = _make_actions()
    sidebar = AppSidebar(actions)
    qtbot.add_widget(sidebar)

    assert not hasattr(sidebar, "tray_toggle")
