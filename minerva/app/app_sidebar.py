"""Sidebar navigation widget for the Minerva app shell."""

from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets

from minerva.app.page_id import PageId
from minerva.ui.density import Density
from minerva.ui.icons import Icons
from minerva.ui.theme import ThemeTokens


class SidebarRow(QtWidgets.QPushButton):
    """A single rounded sidebar destination wired to a shared QAction."""

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
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.clicked.connect(self._action.trigger)

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self.style().unpolish(self)
        self.style().polish(self)

    def apply_density(self, density: Density) -> None:
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
    """Branded fixed-width application navigation.

    The sidebar deliberately contains only top-level destinations.  Page-level
    tools live in each page header, preventing the navigation rail from turning
    into a second toolbar.
    """

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
        self.setMinimumWidth(240)
        self.setObjectName("appSidebar")

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(0)

        root.addWidget(self._build_brand())
        root.addSpacing(22)

        section = QtWidgets.QLabel("WORKSPACE")
        section.setObjectName("sidebarSectionLabel")
        root.addWidget(section)

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

        nav = QtWidgets.QWidget()
        nav_layout = QtWidgets.QVBoxLayout(nav)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(5)
        for pid in order:
            if pid not in actions:
                continue
            row = SidebarRow(
                page_id=pid,
                icon=icon_map[pid],
                label=label_map[pid],
                action=actions[pid],
                tokens=tokens,
                density=density,
            )
            self._rows[pid] = row
            nav_layout.addWidget(row)
        root.addWidget(nav)
        root.addStretch(1)

        self._selection_badge = QtWidgets.QLabel("")
        self._selection_badge.setObjectName("selectionBadge")
        self._selection_badge.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._selection_badge.setVisible(False)
        root.addWidget(
            self._selection_badge,
            alignment=QtCore.Qt.AlignmentFlag.AlignHCenter,
        )
        root.addSpacing(10)
        root.addWidget(self._build_footer())

    @staticmethod
    def _build_brand() -> QtWidgets.QWidget:
        brand = QtWidgets.QWidget()
        brand.setObjectName("sidebarBrand")
        layout = QtWidgets.QHBoxLayout(brand)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(11)

        logo = QtWidgets.QLabel("M")
        logo.setObjectName("sidebarLogo")
        logo.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logo)

        text = QtWidgets.QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        header = QtWidgets.QLabel("MINERVA")
        header.setObjectName("sidebarHeader")
        subtitle = QtWidgets.QLabel("CAN FIXDAT")
        subtitle.setObjectName("sidebarSubtitle")
        text.addWidget(header)
        text.addWidget(subtitle)
        layout.addLayout(text, 1)
        return brand

    @staticmethod
    def _build_footer() -> QtWidgets.QFrame:
        footer = QtWidgets.QFrame()
        footer.setObjectName("sidebarFooter")
        layout = QtWidgets.QHBoxLayout(footer)
        layout.setContentsMargins(11, 9, 11, 9)
        layout.setSpacing(8)

        dot = QtWidgets.QLabel("●")
        dot.setObjectName("sidebarFooterDot")
        layout.addWidget(dot)

        text = QtWidgets.QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        title = QtWidgets.QLabel("Local workspace")
        title.setObjectName("sidebarFooterTitle")
        subtitle = QtWidgets.QLabel("Minerva 0.1")
        subtitle.setObjectName("sidebarFooterSubtitle")
        text.addWidget(title)
        text.addWidget(subtitle)
        layout.addLayout(text, 1)
        return footer

    def set_selection_count(self, count: int) -> None:
        self._selection_badge.setText(f"{count} selected" if count > 0 else "")
        self._selection_badge.setVisible(count > 0)

    @property
    def rows(self) -> dict[PageId, SidebarRow]:
        return self._rows

    def set_active(self, page_id: PageId) -> None:
        for pid, row in self._rows.items():
            row.set_active(pid == page_id)

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        for child in self.findChildren(SidebarRow):
            child.apply_tokens(tokens)

    def apply_density(self, density: Density) -> None:
        self._density = density
        for child in self.findChildren(SidebarRow):
            child.apply_density(density)
