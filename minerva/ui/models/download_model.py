"""Typed records and column definitions for the Downloads workspace."""

from __future__ import annotations

from dataclasses import dataclass

from minerva.domain.downloads import DownloadStatus
from minerva.ui.models.record_model import ColumnSpec, RecordListModel


@dataclass
class DownloadRecord:
    id: int
    queue_id: str
    filename: str
    url: str
    status: DownloadStatus
    file_id: int | None = None
    progress: float = 0.0
    speed: float = 0.0
    upload_speed: float = 0.0
    eta_seconds: float = 0.0
    destination: str = ""
    torrent_name: str = ""
    torrent_hash: str | None = None
    save_path: str = ""
    collection: str = ""
    system: str = ""
    report_id: str = ""
    report_name: str = ""
    error_message: str | None = None
    started_at: float | None = None
    completed_at: float | None = None
    peers: int = 0
    seeds: int = 0
    ratio: float = 0.0
    seed_ratio: float = 0.0
    seed_time_remaining: float = 0.0
    haystack: str = ""


def format_speed(bytes_per_sec: float) -> str:
    if bytes_per_sec <= 0:
        return "0 B/s"
    units = ["B/s", "KB/s", "MB/s", "GB/s"]
    value = float(bytes_per_sec)
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    return f"{value:.1f} {units[index]}" if index else f"{int(value)} {units[index]}"


def format_transfer_speed(bytes_per_sec: float) -> str:
    """Format a table speed, using an em dash for inactive transfers."""
    return format_speed(bytes_per_sec) if bytes_per_sec > 0 else "\u2014"


def format_eta(seconds: float) -> str:
    if seconds <= 0 or seconds >= 8_640_000:
        return "\u2014"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def status_text(status: DownloadStatus) -> str:
    value = status.value if hasattr(status, "value") else str(status)
    return value.replace("_", " ").title()


def _download_speed(record: DownloadRecord) -> float:
    return record.speed if record.status == DownloadStatus.DOWNLOADING else 0.0


def _upload_speed(record: DownloadRecord) -> float:
    return record.upload_speed if record.status in {
        DownloadStatus.DOWNLOADING,
        DownloadStatus.SEEDING,
    } else 0.0


def _seed_display(record: DownloadRecord) -> str:
    if record.peers > 0:
        return f"{record.seeds} ({record.peers})"
    return str(record.seeds)


# The order mirrors the production mock: identity first, compact telemetry in
# the middle, and a single overflow action at the far edge.
_DOWNLOAD_COLUMNS: list[ColumnSpec[DownloadRecord]] = [
    ColumnSpec("Name", lambda r: r.filename),
    ColumnSpec("Status", lambda r: r.status, format_fn=status_text),
    ColumnSpec("Progress", lambda r: r.progress, format_fn=lambda v: f"{v:.0%}"),
    ColumnSpec("Down", lambda r: _download_speed(r), format_fn=format_transfer_speed),
    ColumnSpec("Up", lambda r: _upload_speed(r), format_fn=format_transfer_speed),
    ColumnSpec("ETA", lambda r: r.eta_seconds, format_fn=format_eta),
    ColumnSpec("Seeds", lambda r: r, format_fn=_seed_display),
    ColumnSpec("Ratio", lambda r: r.ratio, format_fn=lambda v: f"{v:.2f}"),
    ColumnSpec("", lambda r: r.status),
]


class DownloadRecordListModel(RecordListModel[DownloadRecord]):
    def record_at(self, row: int) -> DownloadRecord | None:
        return self._records[row] if 0 <= row < len(self._records) else None


# Backward-compatible alias
DownloadTableModel = DownloadRecordListModel
