"""
Sidebar navigation widget for the Minerva app shell.
"""

from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets

from minerva.app.page_id import PageId
from minerva.ui.density import Density
from minerva.ui.icons import Icons
from minerva.ui.theme import ThemeTokens


class SidebarRow(QtWidgets.QPushButton):
    """A single row in the sidebar — icon + label, wired to a QAction.

    The row's active state is toggled by the ``active`` dynamic property,
    which is styled via QSS selectors in ``base.qss``.
    """

    def __init__(
        self,
        page_id: PageId,
        icon: QtGui.QIcon,
        label: str,
        action: QtGui.QAction,
        tokens: ThemeTokens = ThemeTokens(),
        density: Density = Density.COMFORTABLE,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._page_id = page_id
        self._action = action
        self._tokens = tokens
        self._density = density
        self._active = False

        self.setFixedHeight(density.nav_height)
        self.setIcon(icon)
        self.setIconSize(QtCore.QSize(18, 18))
        self.setText(label)
        self.setObjectName("sidebarRow")
        self.setProperty("active", False)

        # Wire click → action.trigger()
        self.clicked.connect(self._action.trigger)

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        """Refresh this row's palette after the global theme changes."""
        self._tokens = tokens
        self.style().unpolish(self)
        self.style().polish(self)

    def apply_density(self, density: Density) -> None:
        """Refresh this row's height after the global density changes."""
        self._density = density
        self.setFixedHeight(density.nav_height)

    @property
    def page_id(self) -> PageId:
        return self._page_id

    @property
    def is_active(self) -> bool:
        return self._active

    def set_active(self, active: bool) -> None:
        self._active = active
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)


class AppSidebar(QtWidgets.QWidget):
    """Fixed-width sidebar with 4 navigation rows.

    Parameters
    ----------
    actions : dict[PageId, QtGui.QAction]
        One QAction per top-level page.  The same QAction instances
        are wired into the sidebar row and the menu bar.
    tokens : ThemeTokens
        Colour tokens for the active / inactive visual.
    density : Density
        Used for row height (``nav_height``).
    """

    # Emitted when a row is clicked (after the action triggers).
    row_activated = QtCore.pyqtSignal(PageId)

    def __init__(
        self,
        actions: dict[PageId, QtGui.QAction],
        tokens: ThemeTokens = ThemeTokens(),
        density: Density = Density.COMFORTABLE,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._density = density
        self._rows: dict[PageId, SidebarRow] = {}

        self.setFixedWidth(240)
        self.setObjectName("appSidebar")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header label ────────────────────────────────────────────────
        self._header = QtWidgets.QLabel("MINERVA")
        self._header.setObjectName("sidebarHeader")
        self._header.setFixedHeight(48)
        layout.addWidget(self._header)

        # ── Separator ────────────────────────────────────────────────────
        self._separator = QtWidgets.QFrame()
        self._separator.setObjectName("sidebarSeparator")
        self._separator.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        self._separator.setFixedHeight(1)
        layout.addWidget(self._separator)

        # ── Sidebar rows (4 destinations) ────────────────────────────────
        icon_map = {
            PageId.REPORTS: Icons.reports(),
            PageId.LIBRARY: Icons.library(),
            PageId.DOWNLOADS: Icons.download(),
            PageId.SETTINGS: Icons.settings(),
        }

        label_map = {
            PageId.REPORTS: "Reports",
            PageId.LIBRARY: "Library",
            PageId.DOWNLOADS: "Downloads",
            PageId.SETTINGS: "Settings",
        }

        order = [
            PageId.REPORTS,
            PageId.LIBRARY,
            PageId.DOWNLOADS,
            PageId.SETTINGS,
        ]

        for pid in order:
            if pid not in actions:
                continue
            action = actions[pid]
            icon = icon_map[pid]
            label = label_map[pid]
            row = SidebarRow(
                page_id=pid,
                icon=icon,
                label=label,
                action=action,
                tokens=tokens,
                density=density,
            )
            self._rows[pid] = row
            layout.addWidget(row)

        layout.addStretch(1)

        # ── Selection badge ───────────────────────────────────────────────
        self._selection_badge = QtWidgets.QLabel("", self)
        self._selection_badge.setObjectName("selectionBadge")
        self._selection_badge.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._selection_badge.setFixedHeight(28)
        layout.addWidget(self._selection_badge)

    # ── Selection badge ─────────────────────────────────────────────────

    def set_selection_count(self, count: int) -> None:
        """Show how many items are currently selected."""
        if count > 0:
            self._selection_badge.setText(f"{count} selected")
        else:
            self._selection_badge.setText("")

    @property
    def rows(self) -> dict[PageId, SidebarRow]:
        return self._rows

    def set_active(self, page_id: PageId) -> None:
        """Highlight the row for *page_id* and deactivate others."""
        for pid, row in self._rows.items():
            row.set_active(pid == page_id)

    # ── Theme reactivity ───────────────────────────────────────────────

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        """Propagate new colour tokens to rows. QSS handles the rest."""
        self._tokens = tokens
        for child in self.findChildren(SidebarRow):
            child.apply_tokens(tokens)

    def apply_density(self, density: Density) -> None:
        """Propagate new density to every row."""
        self._density = density
        for child in self.findChildren(QtWidgets.QWidget):
            if isinstance(child, SidebarRow):
                child.apply_density(density)
