"""Domain types for the persistent download queue and live telemetry."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class DownloadControllerProtocol(Protocol):
    """Minimal interface required by services that need to enqueue files.

    Keeping this in the domain layer lets service code depend on an
    abstraction instead of the concrete Qt-based controller, avoiding
    circular imports and keeping services UI-agnostic.
    """

    def add_to_queue(
        self,
        file_id: int,
        destination: str,
        report_entry_id: str | None = None,
    ) -> str | None:
        ...
class DownloadStatus(str, enum.Enum):
    """Lifecycle state for a single selected file."""

    QUEUED = "queued"
    STARTING = "starting"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    SEEDING = "seeding"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ── Semantic status groupings (single source of truth) ──────────────────
# Use these instead of redefining status sets locally.

#: Statuses where the download is in progress or waiting to start.
DOWNLOAD_ACTIVE_STATUSES = frozenset({
    DownloadStatus.QUEUED, DownloadStatus.STARTING, DownloadStatus.DOWNLOADING,
    DownloadStatus.PAUSED, DownloadStatus.SEEDING,
})
#: Statuses where the download is actively transferring data.
DOWNLOAD_IN_PROGRESS_STATUSES = frozenset({
    DownloadStatus.STARTING, DownloadStatus.DOWNLOADING,
})
#: Terminal statuses — never re-process these records.
DOWNLOAD_DEAD_STATUSES = frozenset({
    DownloadStatus.FAILED, DownloadStatus.CANCELLED,
})
#: Post-download — file is already exposed; only allow COMPLETED↔SEEDING.
DOWNLOAD_DONE_STATUSES = frozenset({
    DownloadStatus.COMPLETED, DownloadStatus.SEEDING,
})


@dataclass(frozen=True, slots=True)
class DownloadFileSpec:
    """Immutable index metadata required to download one selected file."""

    file_id: int
    torrent_name: str
    torrent_path: Path
    select_index: int
    basename: str
    path_in_torrent: str
    size: int
    collection: str
    system: str

    @property
    def qbit_file_index(self) -> int:
        """Return qBittorrent's zero-based file index."""
        return max(0, self.select_index - 1)


@dataclass(frozen=True, slots=True)
class TorrentFileInfo:
    """Point-in-time state for one file inside a torrent."""

    index: int
    name: str
    size: int
    progress: float
    priority: int


@dataclass(frozen=True, slots=True)
class TorrentInfo:
    """Point-in-time qBittorrent torrent state."""

    hash: str
    name: str
    progress: float
    state: str
    dlspeed: int
    upspeed: int
    size: int
    completed: int
    ratio: float
    eta: int
    save_path: str
    category: str = ""
    seeds: int = 0
    peers: int = 0
    files: tuple[TorrentFileInfo, ...] = ()


@dataclass(frozen=True, slots=True)
class DownloadRuntime:
    """Transient telemetry for one queue record.

    This data deliberately stays out of the application-state SQLite database;
    it is refreshed from qBittorrent every polling cycle.
    """

    record_id: str
    qbit_hash: str | None
    torrent_name: str
    progress: float = 0.0
    download_speed: int = 0
    upload_speed: int = 0
    eta: int = -1
    peers: int = 0
    seeds: int = 0
    ratio: float = 0.0
    save_path: str = ""
    raw_state: str = ""


@dataclass
class QueueRecord:
    """Persistent queue entry representing one selected file."""

    id: str
    file_id: int
    report_entry_id: str | None = None
    status: str = DownloadStatus.QUEUED.value
    qbit_hash: str | None = None
    destination: str = ""
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""
