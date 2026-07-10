"""Compact application KPI card with semantic icon treatment."""

from __future__ import annotations

from enum import Enum, auto

from PyQt6 import QtCore, QtGui, QtWidgets

from minerva.ui.density import Density
from minerva.ui.theme import ThemeTokens


class MetricKind(Enum):
    NEUTRAL = auto()
    SUCCESS = auto()
    WARNING = auto()
    ERROR = auto()
    INFO = auto()
    PURPLE = auto()


class MetricCard(QtWidgets.QFrame):
    """A compact metric tile: icon + label/context + value."""

    def __init__(
        self,
        label: str,
        value: str = "",
        kind: MetricKind = MetricKind.NEUTRAL,
        tokens: ThemeTokens = ThemeTokens(),
        density: Density = Density.COMPACT,
        parent: QtWidgets.QWidget | None = None,
        *,
        icon: QtGui.QIcon | None = None,
        subtitle: str = "",
    ) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._density = density
        self._kind = kind
        self._icon = icon
        self.setObjectName("metricCard")

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(13, 11, 13, 11)
        root.setSpacing(11)

        self._icon_container = QtWidgets.QFrame()
        self._icon_container.setObjectName("metricIconContainer")
        self._icon_container.setFixedSize(34, 34)
        icon_layout = QtWidgets.QVBoxLayout(self._icon_container)
        icon_layout.setContentsMargins(8, 8, 8, 8)
        self.icon_widget = QtWidgets.QLabel()
        self.icon_widget.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.icon_widget.setScaledContents(True)
        icon_layout.addWidget(self.icon_widget)
        self._icon_container.setVisible(icon is not None)
        root.addWidget(self._icon_container)

        text = QtWidgets.QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        self.label_widget = QtWidgets.QLabel(label)
        self.label_widget.setObjectName("metricTitle")
        text.addWidget(self.label_widget)
        self.subtitle_widget = QtWidgets.QLabel(subtitle)
        self.subtitle_widget.setObjectName("metricSubtitle")
        self.subtitle_widget.setVisible(bool(subtitle))
        self.subtitle_widget.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        text.addWidget(self.subtitle_widget)
        root.addLayout(text, 1)

        self.value_widget = QtWidgets.QLabel(value)
        self.value_widget.setObjectName("metricValue")
        self.value_widget.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight
            | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        root.addWidget(self.value_widget)

        self._apply_semantic_style()

    def _colour(self) -> tuple[str, str, str]:
        colour = {
            MetricKind.NEUTRAL: self._tokens.text_muted,
            MetricKind.SUCCESS: self._tokens.success,
            MetricKind.WARNING: self._tokens.warning,
            MetricKind.ERROR: self._tokens.error,
            MetricKind.INFO: self._tokens.info_fg,
            MetricKind.PURPLE: self._tokens.purple,
        }[self._kind]
        soft = {
            MetricKind.NEUTRAL: self._tokens.surface_raised,
            MetricKind.SUCCESS: self._tokens.success_surface,
            MetricKind.WARNING: self._tokens.warning_surface,
            MetricKind.ERROR: self._tokens.error_surface,
            MetricKind.INFO: self._tokens.info_surface,
            MetricKind.PURPLE: self._tokens.purple_surface,
        }[self._kind]
        border = {
            MetricKind.NEUTRAL: self._tokens.border,
            MetricKind.SUCCESS: self._tokens.success_border,
            MetricKind.WARNING: self._tokens.warning_border,
            MetricKind.ERROR: self._tokens.error_border,
            MetricKind.INFO: self._tokens.info_border,
            MetricKind.PURPLE: self._tokens.purple_border,
        }[self._kind]
        return colour, soft, border

    def _apply_semantic_style(self) -> None:
        colour, soft, border = self._colour()
        self._icon_container.setStyleSheet(
            "QFrame#metricIconContainer {"
            f"background: {soft}; border: 1px solid {border}; "
            f"border-radius: {self._tokens.radius_md};"
            "}"
        )
        self.value_widget.setStyleSheet(f"color: {colour};")
        if self._icon is not None:
            self.icon_widget.setPixmap(self._icon.pixmap(17, 17))

    def set_value(self, text: str) -> None:
        self.value_widget.setText(text)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_widget.setText(text)
        self.subtitle_widget.setVisible(bool(text))

    def set_icon(self, icon: QtGui.QIcon | None) -> None:
        self._icon = icon
        self._icon_container.setVisible(icon is not None)
        self._apply_semantic_style()

    def set_kind(self, kind: MetricKind) -> None:
        self._kind = kind
        self._apply_semantic_style()

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self._apply_semantic_style()

    def apply_density(self, density: Density) -> None:
        self._density = density
