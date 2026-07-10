"""Torrent-grouped models for the Downloads page."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import ClassVar

from PyQt6 import QtCore

from minerva.domain.downloads import DownloadStatus
from minerva.ui.models.download_model import (
    DownloadRecord,
    format_eta,
    format_speed,
    status_text,
)

# Extra roles consumed by the rich queue delegates.  They deliberately live
# outside the ordinary DisplayRole/UserRole contract so the model remains
# usable with standard Qt views and sorting proxies.
IS_GROUP_ROLE = int(QtCore.Qt.ItemDataRole.UserRole) + 1
SUBTITLE_ROLE = IS_GROUP_ROLE + 1
RECORD_ROLE = SUBTITLE_ROLE + 1
STATUS_ROLE = RECORD_ROLE + 1
GROUP_ROLE = STATUS_ROLE + 1


@dataclass
class TorrentGroup:
    """Aggregate view of one torrent and its selected files."""

    key: str
    torrent_name: str
    torrent_hash: str | None
    files: list[DownloadRecord]

    _STATUS_PRIORITY: ClassVar[dict[DownloadStatus, int]] = {
        DownloadStatus.FAILED: 0,
        DownloadStatus.DOWNLOADING: 1,
        DownloadStatus.STARTING: 2,
        DownloadStatus.SEEDING: 3,
        DownloadStatus.PAUSED: 4,
        DownloadStatus.QUEUED: 5,
        DownloadStatus.COMPLETED: 6,
        DownloadStatus.CANCELLED: 7,
    }

    @property
    def aggregate_progress(self) -> float:
        if not self.files:
            return 0.0
        return sum(f.progress for f in self.files) / len(self.files)

    @property
    def aggregate_speed(self) -> float:
        return sum(f.speed for f in self.files)

    @property
    def aggregate_upload_speed(self) -> float:
        return sum(f.upload_speed for f in self.files)

    @property
    def aggregate_eta(self) -> float:
        values = [f.eta_seconds for f in self.files if f.eta_seconds > 0]
        return max(values, default=0.0)

    @property
    def aggregate_ratio(self) -> float:
        if not self.files:
            return 0.0
        return sum(f.ratio for f in self.files) / len(self.files)

    @property
    def completed_count(self) -> int:
        complete_states = {
            DownloadStatus.COMPLETED,
            DownloadStatus.SEEDING,
        }
        return sum(
            f.status in complete_states or f.progress >= 0.999
            for f in self.files
        )

    @property
    def status(self) -> DownloadStatus:
        if not self.files:
            return DownloadStatus.QUEUED
        return min(
            self.files,
            key=lambda f: self._STATUS_PRIORITY.get(f.status, 99),
        ).status

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def collection(self) -> str:
        return self.files[0].collection if self.files else ""

    @property
    def system(self) -> str:
        return self.files[0].system if self.files else ""

    @property
    def torrent_label(self) -> str:
        """Human-readable group label without appending a count badge."""
        if self.collection and self.system:
            return f"{self.collection} \u2014 {self.system}"
        name = self.torrent_name or "Unnamed torrent"
        if len(name) in (32, 40) and all(c in "0123456789abcdef" for c in name):
            return f"{name[:8]}..."
        return name

    @property
    def subtitle(self) -> str:
        parts = [
            status_text(self.status),
            f"{self.completed_count} of {self.file_count} files complete",
        ]
        if self.aggregate_speed > 0:
            parts.append(format_speed(self.aggregate_speed))
        return " \u00b7 ".join(parts)


def group_records(records: list[DownloadRecord]) -> list[TorrentGroup]:
    """Group records by ``torrent_hash`` (preferred) or ``torrent_name``."""
    buckets: dict[str, list[DownloadRecord]] = {}
    for record in records:
        key = record.torrent_hash or record.torrent_name or record.filename or str(record.id)
        buckets.setdefault(key, []).append(record)

    return [
        TorrentGroup(
            key=key,
            torrent_name=group_records_[0].torrent_name or key,
            torrent_hash=group_records_[0].torrent_hash,
            files=group_records_,
        )
        for key, group_records_ in buckets.items()
    ]


class GroupMode(enum.Enum):
    TORRENT = "torrent"
    REPORT = "report"
    PLATFORM = "platform"


@dataclass
class GroupedDownloads:
    key: str
    label: str
    sublabel: str = ""
    groups: list[TorrentGroup] = field(default_factory=list)

    _STATUS_PRIORITY: ClassVar[dict[DownloadStatus, int]] = {
        status: index
        for index, status in enumerate(
            [
                DownloadStatus.FAILED,
                DownloadStatus.DOWNLOADING,
                DownloadStatus.STARTING,
                DownloadStatus.SEEDING,
                DownloadStatus.PAUSED,
                DownloadStatus.QUEUED,
                DownloadStatus.COMPLETED,
                DownloadStatus.CANCELLED,
            ]
        )
    }

    @property
    def aggregate_progress(self) -> float:
        if not self.groups:
            return 0.0
        total_files = sum(g.file_count for g in self.groups)
        if not total_files:
            return 0.0
        return sum(
            g.aggregate_progress * g.file_count for g in self.groups
        ) / total_files

    @property
    def aggregate_speed(self) -> float:
        return sum(g.aggregate_speed for g in self.groups)

    @property
    def status(self) -> DownloadStatus:
        if not self.groups:
            return DownloadStatus.QUEUED
        return min(
            self.groups,
            key=lambda g: self._STATUS_PRIORITY.get(g.status, 99),
        ).status

    @property
    def file_count(self) -> int:
        return sum(g.file_count for g in self.groups)

    @property
    def collection(self) -> str:
        return self.groups[0].collection if self.groups else ""

    @property
    def system(self) -> str:
        return self.groups[0].system if self.groups else ""


def group_records_by_mode(
    records: list[DownloadRecord],
    mode: GroupMode,
) -> list[GroupedDownloads]:
    torrent_groups = group_records(records)

    if mode == GroupMode.TORRENT:
        return [
            GroupedDownloads(
                key=group.key,
                label=group.torrent_label,
                groups=[group],
            )
            for group in torrent_groups
        ]

    buckets: dict[str, GroupedDownloads] = {}
    for group in torrent_groups:
        if mode == GroupMode.REPORT:
            key = group.files[0].report_id or "__no_report__"
            label = group.files[0].report_name or "Unknown report"
        else:
            key = group.files[0].system or "__no_platform__"
            label = group.files[0].system or "Unknown platform"

        if key in buckets:
            buckets[key].groups.append(group)
        else:
            buckets[key] = GroupedDownloads(key=key, label=label, groups=[group])

    return list(buckets.values())


class TorrentGroupTreeModel(QtCore.QAbstractItemModel):
    """Two-level tree model: torrent group rows with file children."""

    def __init__(
        self,
        groups: list[TorrentGroup] | None = None,
        columns: list | None = None,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._columns: list = columns or []
        self._groups: list[TorrentGroup] = groups or []
        self._records_by_qid: dict[str, DownloadRecord] = {}
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        self._records_by_qid = {
            record.queue_id: record
            for group in self._groups
            for record in group.files
            if record.queue_id
        }

    def set_groups(self, groups: list[TorrentGroup]) -> None:
        self.beginResetModel()
        self._groups = groups
        self._rebuild_index()
        self.endResetModel()

    def group_at(self, group_row: int) -> TorrentGroup | None:
        return self._groups[group_row] if 0 <= group_row < len(self._groups) else None

    def file_at(self, group_row: int, child_row: int) -> DownloadRecord | None:
        group = self.group_at(group_row)
        if group is not None and 0 <= child_row < len(group.files):
            return group.files[child_row]
        return None

    def record_at(self, row: int) -> DownloadRecord | None:
        return self.flat_record_at(row)

    def record_count(self) -> int:
        return sum(group.file_count for group in self._groups)

    def flat_record_at(self, idx: int) -> DownloadRecord | None:
        for group in self._groups:
            if idx < len(group.files):
                return group.files[idx]
            idx -= len(group.files)
        return None

    def flat_index_of(self, queue_id: str) -> tuple[int, int] | None:
        for group_row, group in enumerate(self._groups):
            for child_row, record in enumerate(group.files):
                if record.queue_id == queue_id:
                    return group_row, child_row
        return None

    def flat_index(
        self,
        group_row: int,
        child_row: int,
        column: int = 0,
    ) -> QtCore.QModelIndex:
        if child_row < 0:
            return self.createIndex(group_row, column, 0)
        return self.createIndex(child_row, column, group_row + 1)

    def set_records(self, records: list[DownloadRecord]) -> None:
        self.set_groups(group_records(records))

    def index(
        self,
        row: int,
        column: int,
        parent: QtCore.QModelIndex = QtCore.QModelIndex(),
    ) -> QtCore.QModelIndex:  # noqa: N802
        if not self.hasIndex(row, column, parent):
            return QtCore.QModelIndex()
        if not parent.isValid():
            return self.createIndex(row, column, 0)
        return self.createIndex(row, column, parent.row() + 1)

    def parent(self, index: QtCore.QModelIndex) -> QtCore.QModelIndex:  # noqa: N802
        if not index.isValid() or index.internalId() == 0:
            return QtCore.QModelIndex()
        return self.createIndex(index.internalId() - 1, 0, 0)

    def rowCount(
        self,
        parent: QtCore.QModelIndex = QtCore.QModelIndex(),
    ) -> int:  # noqa: N802
        if not parent.isValid():
            return len(self._groups)
        if parent.internalId() == 0:
            group = self.group_at(parent.row())
            return len(group.files) if group is not None else 0
        return 0

    def columnCount(
        self,
        parent: QtCore.QModelIndex = QtCore.QModelIndex(),
    ) -> int:  # noqa: N802
        return len(self._columns)

    def headerData(
        self,
        section: int,
        orientation: QtCore.Qt.Orientation,
        role: int = QtCore.Qt.ItemDataRole.DisplayRole,
    ) -> str | None:  # noqa: N802
        if (
            orientation == QtCore.Qt.Orientation.Horizontal
            and role == QtCore.Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(self._columns)
        ):
            return self._columns[section].header
        return None

    def flags(self, index: QtCore.QModelIndex) -> QtCore.Qt.ItemFlags:  # noqa: N802
        if not index.isValid():
            return QtCore.Qt.ItemFlag.NoItemFlags
        return QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable

    def data(
        self,
        index: QtCore.QModelIndex,
        role: int = QtCore.Qt.ItemDataRole.DisplayRole,
    ) -> object:  # noqa: N802
        if not index.isValid() or not 0 <= index.column() < len(self._columns):
            return None

        is_group = index.internalId() == 0
        group = self.group_at(index.row()) if is_group else self.group_at(index.internalId() - 1)
        if group is None:
            return None
        record = None if is_group else self.file_at(index.internalId() - 1, index.row())
        if not is_group and record is None:
            return None

        if role == IS_GROUP_ROLE:
            return is_group
        if role == GROUP_ROLE:
            return group
        if role == RECORD_ROLE:
            return record
        if role == STATUS_ROLE:
            return group.status if is_group else record.status
        if role == SUBTITLE_ROLE:
            return group.subtitle if is_group else _record_subtitle(record)

        spec = self._columns[index.column()]
        raw = (
            _group_column_value(index.column(), group)
            if is_group
            else spec.accessor(record)
        )

        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            if spec.format_fn is not None and not is_group:
                return spec.format_fn(raw)
            return raw
        if role == QtCore.Qt.ItemDataRole.UserRole:
            return raw
        if role == QtCore.Qt.ItemDataRole.ToolTipRole:
            if index.column() == 0:
                title = group.torrent_label if is_group else record.filename
                subtitle = group.subtitle if is_group else _record_subtitle(record)
                return f"{title}\n{subtitle}" if subtitle else title
            return str(raw) if raw is not None else ""
        return None


def _record_subtitle(record: DownloadRecord) -> str:
    scope = " \u00b7 ".join(part for part in (record.collection, record.system) if part)
    progress = f"{max(0.0, min(1.0, record.progress)):.0%} complete"
    return f"{progress} \u00b7 {scope}" if scope else progress


def _group_column_value(col: int, group: TorrentGroup) -> object:
    if col == 0:
        return group.torrent_label
    if col == 1:
        return group.status
    if col == 2:
        return group.aggregate_progress
    if col == 3:
        return format_speed(group.aggregate_speed) if group.aggregate_speed > 0 else "\u2014"
    if col == 4:
        return format_speed(group.aggregate_upload_speed) if group.aggregate_upload_speed > 0 else "\u2014"
    if col == 5:
        return format_eta(group.aggregate_eta)
    if col == 6:
        seeds = sum(record.seeds for record in group.files)
        peers = sum(record.peers for record in group.files)
        return f"{seeds} ({peers})" if peers else str(seeds)
    if col == 7:
        return f"{group.aggregate_ratio:.2f}"
    if col == 8:
        return group.status
    return ""
