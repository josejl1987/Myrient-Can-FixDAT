"""Polished empty-state placeholder shared by every page."""

from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets

from minerva.ui.density import Density
from minerva.ui.theme import ThemeTokens


class EmptyState(QtWidgets.QWidget):
    """Centred, bounded empty/error state with one clear recovery action."""

    action_clicked = QtCore.pyqtSignal()

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        icon: QtGui.QIcon | None = None,
        title: str = "",
        description: str = "",
        action_text: str = "",
        tokens: ThemeTokens = ThemeTokens(),
        density: Density = Density.COMPACT,
    ) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._density = density

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(0)
        outer.addStretch(1)

        card = QtWidgets.QFrame()
        card.setObjectName("emptyStateCard")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(42, 36, 42, 36)
        card_layout.setSpacing(11)
        card_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._content_layout = card_layout

        self._icon_label = QtWidgets.QLabel()
        self._icon_label.setObjectName("emptyStateIconWell")
        self._icon_label.setFixedSize(72, 72)
        self._icon_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        if icon is not None:
            self._icon_label.setPixmap(icon.pixmap(34, 34))
        card_layout.addWidget(
            self._icon_label,
            alignment=QtCore.Qt.AlignmentFlag.AlignHCenter,
        )
        card_layout.addSpacing(3)

        self._title_label = QtWidgets.QLabel(title)
        self._title_label.setObjectName("emptyStateTitle")
        self._title_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._title_label.setWordWrap(True)
        card_layout.addWidget(self._title_label)

        self._desc_label: QtWidgets.QLabel | None = None
        if description:
            self._desc_label = self._make_description(description)
            card_layout.addWidget(self._desc_label)

        self._action_btn: QtWidgets.QPushButton | None = None
        if action_text:
            self._action_btn = self._make_action(action_text)
            card_layout.addSpacing(7)
            card_layout.addWidget(
                self._action_btn,
                alignment=QtCore.Qt.AlignmentFlag.AlignHCenter,
            )

        outer.addWidget(card, alignment=QtCore.Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(1)

    @staticmethod
    def _make_description(text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setObjectName("emptyStateDescription")
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setMaximumWidth(410)
        return label

    def _make_action(self, text: str) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text)
        button.setObjectName("accentButton")
        button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(self.action_clicked.emit)
        return button

    def set_icon(self, icon: QtGui.QIcon) -> None:
        self._icon_label.setPixmap(icon.pixmap(34, 34))

    def set_title(self, text: str) -> None:
        self._title_label.setText(text)

    def set_description(self, text: str) -> None:
        if self._desc_label is not None:
            self._desc_label.setText(text)
            return
        self._desc_label = self._make_description(text)
        title_index = self._content_layout.indexOf(self._title_label)
        self._content_layout.insertWidget(title_index + 1, self._desc_label)

    def set_action_text(self, text: str) -> None:
        if self._action_btn is not None:
            self._action_btn.setText(text)
        elif text:
            self._action_btn = self._make_action(text)
            self._content_layout.addSpacing(7)
            self._content_layout.addWidget(
                self._action_btn,
                alignment=QtCore.Qt.AlignmentFlag.AlignHCenter,
            )
