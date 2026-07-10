"""Reusable page header with a restrained action strip and overflow menu."""

from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets

from minerva.ui.density import Density
from minerva.ui.theme import ThemeTokens


class _OverflowActionProxy(QtWidgets.QPushButton):
    """Invisible button proxy preserving existing page button contracts.

    Pages historically keep a QPushButton reference and connect to ``clicked``.
    The proxy lets those actions live in the header overflow menu without
    forcing pages to know whether an action is rendered as a button or menu
    item.  Enabling the proxy also enables the corresponding QAction.
    """

    def __init__(
        self,
        action: QtGui.QAction,
        parent: QtWidgets.QWidget,
    ) -> None:
        super().__init__(parent)
        self._menu_action = action
        self.setVisible(False)
        action.triggered.connect(lambda _checked=False: self.click())

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        super().setEnabled(enabled)
        if hasattr(self, "_menu_action"):
            self._menu_action.setEnabled(enabled)


class PageHeader(QtWidgets.QWidget):
    """Page identity on the left and a deliberately compact action area."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        tokens: ThemeTokens = ThemeTokens(),
        density: Density = Density.COMPACT,
        parent: QtWidgets.QWidget | None = None,
        *,
        eyebrow: str = "WORKSPACE",
    ) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._density = density
        self._overflow_menu: QtWidgets.QMenu | None = None
        self._overflow_button: QtWidgets.QToolButton | None = None
        self.setObjectName("pageHeader")

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(24)

        text_layout = QtWidgets.QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        self.eyebrow_label = QtWidgets.QLabel(eyebrow.upper())
        self.eyebrow_label.setObjectName("pageEyebrow")
        self.eyebrow_label.setVisible(bool(eyebrow))
        text_layout.addWidget(self.eyebrow_label)

        self.title_label = QtWidgets.QLabel(title)
        self.title_label.setObjectName("pageTitle")
        text_layout.addWidget(self.title_label)

        self.subtitle_label = QtWidgets.QLabel(subtitle)
        self.subtitle_label.setObjectName("pageSubtitle")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setVisible(bool(subtitle))
        text_layout.addWidget(self.subtitle_label)
        root.addLayout(text_layout, 1)

        self._actions = QtWidgets.QHBoxLayout()
        self._actions.setContentsMargins(0, 0, 0, 0)
        self._actions.setSpacing(8)
        self._actions.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight
            | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        root.addLayout(self._actions)

    def set_title(self, text: str) -> None:
        self.title_label.setText(text)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text)
        self.subtitle_label.setVisible(bool(text))

    def set_eyebrow(self, text: str) -> None:
        self.eyebrow_label.setText(text.upper())
        self.eyebrow_label.setVisible(bool(text))

    def add_action(
        self,
        text: str,
        icon: QtGui.QIcon | None = None,
        *,
        primary: bool = False,
        danger: bool = False,
    ) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text)
        button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        if icon is not None:
            button.setIcon(icon)
            button.setIconSize(QtCore.QSize(16, 16))
        button.setObjectName(
            "dangerButton" if danger else "primaryButton" if primary else "subtleButton"
        )
        self._actions.addWidget(button)
        return button

    def add_overflow_action(
        self,
        text: str,
        icon: QtGui.QIcon | None = None,
        *,
        danger: bool = False,
    ) -> QtWidgets.QPushButton:
        """Add a menu action while returning a QPushButton-compatible proxy."""
        menu = self._ensure_overflow_menu()
        action = QtGui.QAction(icon or QtGui.QIcon(), text, self)
        if danger:
            action.setProperty("danger", True)
        menu.addAction(action)
        return _OverflowActionProxy(action, self)

    def add_overflow_separator(self) -> None:
        self._ensure_overflow_menu().addSeparator()

    def _ensure_overflow_menu(self) -> QtWidgets.QMenu:
        if self._overflow_menu is not None:
            return self._overflow_menu

        self._overflow_menu = QtWidgets.QMenu(self)
        button = QtWidgets.QToolButton(self)
        button.setObjectName("headerOverflowButton")
        button.setText("⋯")
        button.setToolTip("More actions")
        button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setMenu(self._overflow_menu)
        button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._actions.addWidget(button)
        self._overflow_button = button
        return self._overflow_menu

    def add_widget(self, widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
        self._actions.addWidget(widget)
        return widget
