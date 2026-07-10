"""Contextual inspector for the selected download queue entry."""

from __future__ import annotations

from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets

from minerva.domain.downloads import DownloadStatus
from minerva.ui.icons import Icons
from minerva.ui.models.download_model import (
    DownloadRecord,
    format_eta,
    format_speed,
)
from minerva.ui.widgets.cover_art import CoverLabel
from minerva.ui.widgets.status_badge import BadgeKind, StatusBadge


class DownloadInspector(QtWidgets.QFrame):
    """Compact selected-download panel with live telemetry and actions."""

    pause_requested = QtCore.pyqtSignal(str)
    resume_requested = QtCore.pyqtSignal(str)
    retry_requested = QtCore.pyqtSignal(str)
    remove_requested = QtCore.pyqtSignal(str)
    open_folder_requested = QtCore.pyqtSignal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("downloadInspectorPanel")
        self.setMinimumWidth(350)
        self.setMaximumWidth(480)
        self._record: DownloadRecord | None = None
        self._stats_values: dict[str, QtWidgets.QLabel] = {}

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(14)

        heading = QtWidgets.QLabel("Selected download")
        heading.setObjectName("downloadInspectorHeading")
        root.addWidget(heading)
        root.addWidget(self._separator())

        # Hero
        hero = QtWidgets.QHBoxLayout()
        hero.setSpacing(14)
        self._cover = CoverLabel(76, 76)
        self._cover.setObjectName("downloadInspectorCover")
        hero.addWidget(self._cover, 0, QtCore.Qt.AlignmentFlag.AlignTop)

        hero_text = QtWidgets.QVBoxLayout()
        hero_text.setSpacing(4)
        self._title = QtWidgets.QLabel("Select a download")
        self._title.setObjectName("downloadInspectorTitle")
        self._title.setWordWrap(True)
        hero_text.addWidget(self._title)
        self._scope = QtWidgets.QLabel("Queue details will appear here")
        self._scope.setObjectName("downloadInspectorSubtitle")
        self._scope.setWordWrap(True)
        hero_text.addWidget(self._scope)
        self._torrent = QtWidgets.QLabel("")
        self._torrent.setObjectName("downloadInspectorTorrent")
        self._torrent.setWordWrap(True)
        hero_text.addWidget(self._torrent)
        hero_text.addStretch(1)
        hero.addLayout(hero_text, 1)
        root.addLayout(hero)

        self._status_badge = StatusBadge("No selection", BadgeKind.NEUTRAL)
        root.addWidget(self._status_badge, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self._separator())

        # Progress
        progress_header = QtWidgets.QHBoxLayout()
        self._progress_label = QtWidgets.QLabel("Overall progress")
        self._progress_label.setObjectName("downloadInspectorLabel")
        progress_header.addWidget(self._progress_label)
        progress_header.addStretch(1)
        self._progress_value = QtWidgets.QLabel("0%")
        self._progress_value.setObjectName("downloadInspectorValue")
        progress_header.addWidget(self._progress_value)
        root.addLayout(progress_header)

        self._progress = QtWidgets.QProgressBar()
        self._progress.setObjectName("downloadInspectorProgress")
        self._progress.setRange(0, 100)
        self._progress.setTextVisible(False)
        root.addWidget(self._progress)

        stats = QtWidgets.QFrame()
        stats.setObjectName("downloadInspectorStats")
        stats_layout = QtWidgets.QVBoxLayout(stats)
        stats_layout.setContentsMargins(0, 4, 0, 4)
        stats_layout.setSpacing(0)
        for key, label, icon in (
            ("down", "Download speed", Icons.download()),
            ("up", "Upload speed", Icons.upload()),
            ("eta", "ETA", Icons.activity()),
            ("seeds", "Seeds", Icons.queue()),
            ("ratio", "Ratio", Icons.chart()),
        ):
            stats_layout.addWidget(self._build_stat_row(key, label, icon))
        root.addWidget(stats)

        self._destination = QtWidgets.QLabel("\u2014")
        self._destination.setObjectName("downloadInspectorDestination")
        self._destination.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._destination.setVisible(False)
        root.addWidget(self._destination)

        self._error = QtWidgets.QLabel("")
        self._error.setObjectName("downloadInspectorError")
        self._error.setWordWrap(True)
        self._error.hide()
        root.addWidget(self._error)
        root.addStretch(1)

        # Sticky action block
        root.addWidget(self._separator())
        action_label = QtWidgets.QLabel("Actions")
        action_label.setObjectName("downloadInspectorLabel")
        root.addWidget(action_label)

        action_row = QtWidgets.QHBoxLayout()
        action_row.setSpacing(8)
        self._open_btn = self._make_button(
            "Open folder", Icons.folder_open(), "downloadInspectorAction"
        )
        self._toggle_btn = self._make_button(
            "Pause", Icons.pause(), "downloadInspectorAction"
        )
        self._more_btn = QtWidgets.QToolButton()
        self._more_btn.setObjectName("downloadInspectorAction")
        self._more_btn.setText("More")
        self._more_btn.setIcon(Icons.settings())
        self._more_btn.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self._more_btn.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self._more_menu = QtWidgets.QMenu(self._more_btn)
        self._retry_action = self._more_menu.addAction(Icons.retry(), "Retry")
        self._copy_path_action = self._more_menu.addAction(Icons.copy(), "Copy destination")
        self._more_btn.setMenu(self._more_menu)
        action_row.addWidget(self._open_btn, 1)
        action_row.addWidget(self._toggle_btn, 1)
        action_row.addWidget(self._more_btn, 1)
        root.addLayout(action_row)

        self._remove_btn = self._make_button(
            "Remove from queue", Icons.trash(), "downloadInspectorRemove"
        )
        root.addWidget(self._remove_btn)
        # Compatibility alias for callers/tests from the previous inspector.
        self._retry_btn = QtWidgets.QPushButton()
        self._retry_btn.hide()

        self._open_btn.clicked.connect(self._emit_open)
        self._toggle_btn.clicked.connect(self._emit_toggle)
        self._retry_action.triggered.connect(self._emit_retry)
        self._copy_path_action.triggered.connect(self._copy_destination)
        self._remove_btn.clicked.connect(self._emit_remove)
        self.clear()

    @staticmethod
    def _separator() -> QtWidgets.QFrame:
        line = QtWidgets.QFrame()
        line.setObjectName("downloadInspectorSeparator")
        line.setFixedHeight(1)
        return line

    def _build_stat_row(
        self,
        key: str,
        label: str,
        icon: QtGui.QIcon,
    ) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget()
        row.setObjectName("downloadInspectorStatRow")
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(10)
        icon_label = QtWidgets.QLabel()
        icon_label.setObjectName("downloadInspectorStatIcon")
        icon_label.setPixmap(icon.pixmap(15, 15))
        layout.addWidget(icon_label)
        key_label = QtWidgets.QLabel(label)
        key_label.setObjectName("downloadInspectorLabel")
        layout.addWidget(key_label)
        layout.addStretch(1)
        value = QtWidgets.QLabel("\u2014")
        value.setObjectName("downloadInspectorValue")
        layout.addWidget(value)
        self._stats_values[key] = value
        return row

    @staticmethod
    def _make_button(
        text: str,
        icon: QtGui.QIcon,
        object_name: str,
    ) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text)
        button.setIcon(icon)
        button.setObjectName(object_name)
        button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        return button

    def clear(self) -> None:  # type: ignore[override]
        self._record = None
        self._cover.set_placeholder()
        self._title.setText("Select a download")
        self._scope.setText("Queue details will appear here")
        self._torrent.clear()
        self._status_badge.setText("No selection")
        self._status_badge.set_kind(BadgeKind.NEUTRAL)
        self._progress.setValue(0)
        self._progress_value.setText("0%")
        for value in self._stats_values.values():
            value.setText("\u2014")
        self._destination.setText("\u2014")
        self._error.hide()
        for button in (
            self._open_btn,
            self._toggle_btn,
            self._more_btn,
            self._remove_btn,
        ):
            button.setEnabled(False)
        self._retry_action.setVisible(False)
        self._copy_path_action.setEnabled(False)

    def set_record(
        self,
        record: DownloadRecord,
        cover_path: str | Path | None = None,
    ) -> None:
        self._record = record
        self._cover.set_cover(cover_path)
        self._title.setText(record.filename or "Unnamed download")
        self._scope.setText(
            " \u00b7 ".join(part for part in (record.collection, record.system) if part)
            or "Indexed file"
        )
        source = record.torrent_name or "Torrent not submitted yet"
        self._torrent.setText(source)
        self._torrent.setToolTip(source)

        status_value = record.status.value if hasattr(record.status, "value") else str(record.status)
        self._status_badge.setText(status_value.replace("_", " ").title())
        self._set_status_state(status_value)

        progress = max(0, min(100, int(record.progress * 100)))
        self._progress.setValue(progress)
        self._progress_value.setText(f"{progress}%")
        self._stats_values["down"].setText(
            format_speed(record.speed) if record.speed > 0 else "\u2014"
        )
        self._stats_values["up"].setText(
            format_speed(record.upload_speed) if record.upload_speed > 0 else "\u2014"
        )
        self._stats_values["eta"].setText(format_eta(record.eta_seconds))
        self._stats_values["seeds"].setText(
            f"{record.seeds} of {record.peers}" if record.peers > 0 else str(record.seeds)
        )
        self._stats_values["ratio"].setText(f"{record.ratio:.2f}")

        destination = record.destination or record.save_path or ""
        self._destination.setText(destination or "\u2014")
        self._destination.setToolTip(destination)
        self._error.setText(record.error_message or "")
        self._error.setVisible(bool(record.error_message))

        active = record.status in {
            DownloadStatus.STARTING,
            DownloadStatus.DOWNLOADING,
            DownloadStatus.SEEDING,
        }
        resumable = record.status in {DownloadStatus.QUEUED, DownloadStatus.PAUSED}
        self._toggle_btn.setText("Pause" if active else "Resume")
        self._toggle_btn.setIcon(Icons.pause() if active else Icons.play())
        self._toggle_btn.setEnabled(active or resumable)
        self._retry_action.setVisible(record.status == DownloadStatus.FAILED)
        self._open_btn.setEnabled(bool(destination))
        self._copy_path_action.setEnabled(bool(destination))
        self._more_btn.setEnabled(True)
        self._remove_btn.setEnabled(True)

    def _set_status_state(self, state: str) -> None:
        kind = {
            "downloading": BadgeKind.INFO,
            "seeding": BadgeKind.SUCCESS,
            "completed": BadgeKind.SUCCESS,
            "failed": BadgeKind.ERROR,
            "paused": BadgeKind.WARNING,
            "queued": BadgeKind.NEUTRAL,
            "starting": BadgeKind.INFO,
            "cancelled": BadgeKind.WARNING,
        }.get(state, BadgeKind.NEUTRAL)
        self._status_badge.set_kind(kind)

    def _emit_open(self) -> None:
        if self._record is not None:
            self.open_folder_requested.emit(
                self._record.destination or self._record.save_path
            )

    def _emit_toggle(self) -> None:
        if self._record is None:
            return
        if self._record.status in {
            DownloadStatus.STARTING,
            DownloadStatus.DOWNLOADING,
            DownloadStatus.SEEDING,
        }:
            self.pause_requested.emit(self._record.queue_id)
        else:
            self.resume_requested.emit(self._record.queue_id)

    def _emit_retry(self) -> None:
        if self._record is not None:
            self.retry_requested.emit(self._record.queue_id)

    def _emit_remove(self) -> None:
        if self._record is not None:
            self.remove_requested.emit(self._record.queue_id)

    def _copy_destination(self) -> None:
        if self._record is None:
            return
        value = self._record.destination or self._record.save_path
        if value:
            QtWidgets.QApplication.clipboard().setText(value)
