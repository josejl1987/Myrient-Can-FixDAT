"""Rich delegates for the Downloads workspace."""

from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets

from minerva.domain.downloads import DownloadStatus
from minerva.ui.icons import Icons
from minerva.ui.models.torrent_group import (
    IS_GROUP_ROLE,
    STATUS_ROLE,
    SUBTITLE_ROLE,
)
from minerva.ui.theme import ThemeTokens


def _draw_item_panel(
    painter: QtGui.QPainter,
    option: QtWidgets.QStyleOptionViewItem,
) -> None:
    style = option.widget.style() if option.widget else QtWidgets.QApplication.style()
    style.drawPrimitive(
        QtWidgets.QStyle.PrimitiveElement.PE_PanelItemViewItem,
        option,
        painter,
        option.widget,
    )


class DownloadNameDelegate(QtWidgets.QStyledItemDelegate):
    """Render group and file identity as readable two-line rows."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._tokens = ThemeTokens()

    def paint(self, painter, option, index) -> None:
        _draw_item_panel(painter, option)
        is_group = bool(index.data(IS_GROUP_ROLE))
        title = str(index.data(QtCore.Qt.ItemDataRole.DisplayRole) or "")
        subtitle = str(index.data(SUBTITLE_ROLE) or "")

        painter.save()
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        icon_size = 18 if is_group else 20
        icon_rect = QtCore.QRect(
            option.rect.left() + 8,
            option.rect.center().y() - icon_size // 2,
            icon_size,
            icon_size,
        )
        icon = Icons.queue() if is_group else Icons.app()
        icon.paint(painter, icon_rect)

        text_left = icon_rect.right() + 10
        text_rect = option.rect.adjusted(text_left - option.rect.left(), 5, -8, -5)
        title_rect = QtCore.QRect(
            text_rect.left(),
            text_rect.top(),
            text_rect.width(),
            max(18, text_rect.height() // 2),
        )
        subtitle_rect = QtCore.QRect(
            text_rect.left(),
            title_rect.bottom(),
            text_rect.width(),
            max(16, text_rect.bottom() - title_rect.bottom()),
        )

        title_font = QtGui.QFont(option.font)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(
            option.palette.highlightedText().color()
            if option.state & QtWidgets.QStyle.StateFlag.State_Selected
            else QtGui.QColor(self._tokens.text)
        )
        title_text = painter.fontMetrics().elidedText(
            title,
            QtCore.Qt.TextElideMode.ElideRight,
            title_rect.width(),
        )
        painter.drawText(
            title_rect,
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
            title_text,
        )

        subtitle_font = QtGui.QFont(option.font)
        if subtitle_font.pointSizeF() > 0:
            subtitle_font.setPointSizeF(max(8.0, subtitle_font.pointSizeF() - 1.0))
        painter.setFont(subtitle_font)
        painter.setPen(QtGui.QColor(self._tokens.text_muted))
        subtitle_text = painter.fontMetrics().elidedText(
            subtitle,
            QtCore.Qt.TextElideMode.ElideRight,
            subtitle_rect.width(),
        )
        painter.drawText(
            subtitle_rect,
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
            subtitle_text,
        )
        painter.restore()

    def sizeHint(self, option, index) -> QtCore.QSize:
        return QtCore.QSize(280, 54 if index.data(IS_GROUP_ROLE) else 50)


class DownloadStatusDelegate(QtWidgets.QStyledItemDelegate):
    """Render status as a semantic dot plus text, not a bulky badge."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._tokens = ThemeTokens()

    def paint(self, painter, option, index) -> None:
        _draw_item_panel(painter, option)
        status = index.data(STATUS_ROLE)
        if not isinstance(status, DownloadStatus):
            status = index.data(QtCore.Qt.ItemDataRole.UserRole)
        if not isinstance(status, DownloadStatus):
            status = DownloadStatus.QUEUED
        text, color, _background = self._style(status)

        painter.save()
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        dot_center = QtCore.QPointF(option.rect.left() + 10, option.rect.center().y())
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor(color))
        painter.drawEllipse(dot_center, 3.5, 3.5)

        text_rect = option.rect.adjusted(20, 0, -6, 0)
        painter.setPen(QtGui.QColor(color))
        painter.drawText(
            text_rect,
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
            text,
        )
        painter.restore()

    def _style(self, status: DownloadStatus) -> tuple[str, str, str]:
        t = self._tokens
        return {
            DownloadStatus.QUEUED: ("Queued", t.text_muted, t.surface_raised),
            DownloadStatus.STARTING: ("Starting", t.info_fg, t.info_surface),
            DownloadStatus.DOWNLOADING: ("Downloading", t.info_fg, t.info_surface),
            DownloadStatus.PAUSED: ("Paused", t.warning, t.warning_surface),
            DownloadStatus.SEEDING: ("Seeding", t.success, t.success_surface),
            DownloadStatus.COMPLETED: ("Completed", t.success, t.success_surface),
            DownloadStatus.FAILED: ("Failed", t.error, t.error_surface),
            DownloadStatus.CANCELLED: ("Cancelled", t.text_muted, t.surface_raised),
        }[status]


class DownloadProgressDelegate(QtWidgets.QStyledItemDelegate):
    def __init__(self, tokens: ThemeTokens = ThemeTokens(), parent=None) -> None:
        super().__init__(parent)
        self._tokens = tokens

    def paint(self, painter, option, index) -> None:
        _draw_item_panel(painter, option)
        value = index.data(QtCore.Qt.ItemDataRole.UserRole)
        value = max(0.0, min(1.0, float(value or 0.0)))
        status = index.data(STATUS_ROLE)

        painter.save()
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        text_rect = option.rect.adjusted(8, 2, -8, -18)
        painter.setPen(option.palette.text().color())
        painter.drawText(
            text_rect,
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
            f"{value:.0%}",
        )
        track = QtCore.QRectF(
            option.rect.left() + 8,
            option.rect.bottom() - 14,
            max(10, option.rect.width() - 16),
            5,
        )
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor(self._tokens.surface_raised))
        painter.drawRoundedRect(track, 2.5, 2.5)
        fill = QtCore.QRectF(track.left(), track.top(), track.width() * value, track.height())
        complete = status in {DownloadStatus.COMPLETED, DownloadStatus.SEEDING} or value >= 1.0
        painter.setBrush(QtGui.QColor(self._tokens.success if complete else self._tokens.info_fg))
        painter.drawRoundedRect(fill, 2.5, 2.5)
        painter.restore()


class DownloadMoreDelegate(QtWidgets.QStyledItemDelegate):
    """Single overflow affordance; actions remain in the contextual menu."""

    menu_requested = QtCore.pyqtSignal(QtCore.QModelIndex)

    def paint(self, painter, option, index) -> None:
        _draw_item_panel(painter, option)
        painter.save()
        font = QtGui.QFont(option.font)
        font.setBold(True)
        if font.pointSizeF() > 0:
            font.setPointSizeF(font.pointSizeF() + 2)
        painter.setFont(font)
        painter.setPen(option.palette.text().color())
        painter.drawText(option.rect, QtCore.Qt.AlignmentFlag.AlignCenter, "\u22ee")
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        if event.type() == QtCore.QEvent.Type.MouseButtonRelease:
            self.menu_requested.emit(index)
            return True
        return super().editorEvent(event, model, option, index)
