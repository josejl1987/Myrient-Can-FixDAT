"""Canonical loading, empty, error and content states for application pages."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6 import QtCore, QtWidgets

from minerva.ui.icons import Icons
from minerva.ui.widgets.empty_state import EmptyState


class ContentState(QtWidgets.QStackedWidget):
    """A four-state stack with consistent, bounded loading/error treatments."""

    LOADING = 0
    EMPTY = 1
    ERROR = 2
    CONTENT = 3

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)

        # Index 0 — loading
        self._loading = QtWidgets.QWidget()
        loading_outer = QtWidgets.QVBoxLayout(self._loading)
        loading_outer.setContentsMargins(24, 24, 24, 24)
        loading_outer.addStretch(1)
        loading_card = QtWidgets.QFrame()
        loading_card.setObjectName("emptyStateCard")
        loading_card.setFixedWidth(460)
        loading_layout = QtWidgets.QVBoxLayout(loading_card)
        loading_layout.setContentsMargins(38, 34, 38, 34)
        loading_layout.setSpacing(14)
        self._loading_label = QtWidgets.QLabel("Loading…")
        self._loading_label.setObjectName("emptyStateTitle")
        self._loading_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        loading_layout.addWidget(self._loading_label)
        self._loading_bar = QtWidgets.QProgressBar()
        self._loading_bar.setObjectName("contentStateLoadingBar")
        self._loading_bar.setRange(0, 0)
        self._loading_bar.setFixedWidth(260)
        loading_layout.addWidget(
            self._loading_bar,
            alignment=QtCore.Qt.AlignmentFlag.AlignHCenter,
        )
        loading_outer.addWidget(
            loading_card,
            alignment=QtCore.Qt.AlignmentFlag.AlignHCenter,
        )
        loading_outer.addStretch(1)

        # Index 1 — caller-supplied empty state
        self._empty = QtWidgets.QWidget()
        self._empty_layout = QtWidgets.QVBoxLayout(self._empty)
        self._empty_layout.setContentsMargins(0, 0, 0, 0)

        # Index 2 — shared error state
        self._error = EmptyState(
            icon=Icons.status_error(),
            title="Something went wrong",
            description="The operation could not be completed.",
            action_text="Retry",
        )
        self._error_title = self._error._title_label  # compatibility aliases
        self._error_desc = self._error._desc_label
        self._retry_btn = self._error._action_btn
        self._retry_callback: Callable[[], None] | None = None
        self._error.action_clicked.connect(self._on_retry)

        # Index 3 — caller-supplied content
        self._content = QtWidgets.QWidget()
        self._content_layout = QtWidgets.QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)

        self.addWidget(self._loading)
        self.addWidget(self._empty)
        self.addWidget(self._error)
        self.addWidget(self._content)

    def set_loading(self, message: str = "") -> None:
        self._loading_label.setText(message or "Loading…")
        self.setCurrentIndex(self.LOADING)

    def set_empty(self, widget: QtWidgets.QWidget) -> None:
        self._replace_widget(self._empty_layout, widget)
        self.setCurrentIndex(self.EMPTY)

    def set_error(
        self,
        message: str,
        retry: Callable[[], None] | None = None,
    ) -> None:
        self._error.set_description(
            message or "The operation could not be completed. Check the current configuration and try again."
        )
        self._retry_callback = retry
        if self._retry_btn is not None:
            self._retry_btn.setVisible(retry is not None)
        self.setCurrentIndex(self.ERROR)

    def set_content(self, widget: QtWidgets.QWidget) -> None:
        self._replace_widget(self._content_layout, widget)
        self.setCurrentIndex(self.CONTENT)

    @staticmethod
    def _replace_widget(layout: QtWidgets.QVBoxLayout, widget: QtWidgets.QWidget) -> None:
        while layout.count():
            item = layout.takeAt(0)
            current = item.widget()
            if current is not None and current is not widget:
                current.setParent(None)
                current.deleteLater()
        if layout.indexOf(widget) < 0:
            layout.addWidget(widget)

    def _on_retry(self) -> None:
        if self._retry_callback is not None:
            self._retry_callback()
