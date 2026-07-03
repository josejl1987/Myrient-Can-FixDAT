"""Persistent selective-file download orchestration for qBittorrent."""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path

import requests
from PyQt6 import QtCore

from minerva.app.app_state import AppState
from minerva.app.qbit_monitor import QbitMonitor
from minerva.domain.downloads import (
    DOWNLOAD_ACTIVE_STATUSES,
    DOWNLOAD_DEAD_STATUSES,
    DOWNLOAD_DONE_STATUSES,
    DOWNLOAD_IN_PROGRESS_STATUSES,
    DownloadFileSpec,
    DownloadRuntime,
    DownloadStatus,
    QueueRecord,
    TorrentInfo,
)
from minerva.domain.sources import DownloadSource
from minerva_db import DEFAULT_TORRENT_DIR
from minerva_qbit import QBittorrentClient, QBittorrentError
from minerva_state import MinervaState

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _TaskSignals(QtCore.QObject):
    succeeded = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()


class _FunctionTask(QtCore.QRunnable):
    """Run a callable in QThreadPool and marshal its result to Qt signals."""

    def __init__(self, fn: Callable[[], object]) -> None:
        super().__init__()
        self.fn = fn
        self.signals = _TaskSignals()

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            self.signals.succeeded.emit(self.fn())
        except Exception as exc:
            log.exception("Background download operation failed")
            self.signals.failed.emit(str(exc))
        finally:
            self.signals.finished.emit()


class DownloadController(QtCore.QObject):
    """Own the persistent queue, selective torrent priorities and telemetry."""

    queue_changed = QtCore.pyqtSignal()
    runtime_changed = QtCore.pyqtSignal(list)  # list[DownloadRuntime]
    runtime_dirty = QtCore.pyqtSignal(set)  # set[str] of changed record IDs
    error = QtCore.pyqtSignal(str)
    activity_event = QtCore.pyqtSignal(str, str)  # category, message

    _monitor_hashes_requested = QtCore.pyqtSignal(object)
    _monitor_stop_requested = QtCore.pyqtSignal()

    _ACTIVE_UPLOAD_STATES = {"uploading", "stalledUP", "forcedUP", "queuedUP"}
    _QBT_STATUS_MAP: dict[str, DownloadStatus] = {
        "error": DownloadStatus.FAILED,
        "missingFiles": DownloadStatus.FAILED,
        "pausedDL": DownloadStatus.PAUSED,
        "queuedDL": DownloadStatus.QUEUED,
        "downloading": DownloadStatus.DOWNLOADING,
        "stalledDL": DownloadStatus.DOWNLOADING,
        "forcedDL": DownloadStatus.DOWNLOADING,
        "metaDL": DownloadStatus.STARTING,
        "checkingDL": DownloadStatus.STARTING,
        "checkingUP": DownloadStatus.STARTING,
        "allocating": DownloadStatus.STARTING,
        "checkingResumeData": DownloadStatus.STARTING,
        "moving": DownloadStatus.STARTING,
        "pausedUP": DownloadStatus.COMPLETED,
        # Upload-side states — normally progress >= 1.0 when these appear,
        # but _map_qbit_state guards the progress check first.  If a
        # transient reports progress < 1.0, map to PAUSED rather than
        # falling through to the DOWNLOADING default.
        "uploading": DownloadStatus.PAUSED,
        "stalledUP": DownloadStatus.PAUSED,
        "forcedUP": DownloadStatus.PAUSED,
        "queuedUP": DownloadStatus.QUEUED,
    }

    def __init__(
        self,
        state: MinervaState,
        app_state: AppState,
        qbit_client: QBittorrentClient,
        seed_dir: str | Path = ".qbitseed",
        allow_copy_fallback: bool = True,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._app_state = app_state
        self._qbit_template = qbit_client
        self._seed_dir = Path(seed_dir).expanduser().resolve()
        self._allow_copy_fallback = allow_copy_fallback
        self._pool = QtCore.QThreadPool.globalInstance()
        self._tasks: set[_FunctionTask] = set()
        self._runtime: dict[str, DownloadRuntime] = {}
        self._queue_cache: list[QueueRecord] | None = None  # invalidated on queue_changed
        self._spec_cache: dict[int, DownloadFileSpec] = {}
        self._submitting_torrents: set[str] = set()
        self._resubmit_torrents: set[str] = set()
        self._linking_records: set[str] = set()
        # ponytail: these sets are mutated from QThreadPool callbacks via
        # signal marshalling (which lands on the main thread), so they're
        # safe in practice.  The lock is defensive — it protects against
        # accidental future access from worker threads.
        self._sets_lock = threading.Lock()

        self._monitor: QbitMonitor | None = None
        self._monitor_thread: QtCore.QThread | None = None

        self.file_spec_resolver: Callable[[int], DownloadFileSpec | None] | None = None
        self._resolver_torrent_dir: Path = DEFAULT_TORRENT_DIR

        # Clear the spec cache when the index is rebuilt so we don't
        # serve stale file metadata (torrent path, qbit index, size)
        # from a previous index generation.
        self._app_state.index_state_changed.connect(self._on_index_changed)

    # ── Queue commands ──────────────────────────────────────────────────

    def add_to_queue(
        self,
        file_id: int,
        destination: str,
        report_entry_id: str | None = None,
        source: str = DownloadSource.MINERVA_TORRENT.value,
        source_ref: str | None = None,
    ) -> str:
        destination = str(Path(destination).expanduser())
        for existing in self._state.list_queue():
            if (
                existing.file_id == file_id
                and existing.source == source
                and Path(existing.destination) == Path(destination)
                and existing.status not in {
                    DownloadStatus.CANCELLED.value,
                    DownloadStatus.FAILED.value,
                }
            ):
                return existing.id

        record_id = uuid.uuid4().hex
        now = _now_iso()
        record = QueueRecord(
            id=record_id,
            file_id=file_id,
            report_entry_id=report_entry_id,
            source=source,
            source_ref=source_ref,
            status=DownloadStatus.QUEUED.value,
            destination=destination,
            created_at=now,
            updated_at=now,
        )
        self._state.save_queue_record(record)
        self._state.add_event("download", f"Queued {source} \u2192 {destination}")
        self._emit_changed()
        self._schedule_record_submission(record)
        return record_id

    def add_many_to_queue(
        self,
        items: Iterable[tuple],
    ) -> list[str]:
        """Queue several files; same-torrent items are submitted as one job.

        Batches DB writes and emits a single ``queue_changed`` signal instead
        of one per file — avoids O(N²) list_queue() scans and N UI rebuilds.
        """
        items_list = list(items)
        if not items_list:
            return []

        # Snapshot existing queue ONCE to deduplicate, instead of per-file.
        existing = self._state.list_queue()
        existing_keys: set[tuple[int, str, str]] = set()
        for rec in existing:
            if rec.status not in {
                DownloadStatus.CANCELLED.value,
                DownloadStatus.FAILED.value,
            }:
                existing_keys.add((rec.file_id, str(Path(rec.destination)), rec.source))

        now = _now_iso()
        ids: list[str] = []
        touched_torrents: set[str] = set()
        new_records: list[QueueRecord] = []

        for item in items_list:
            if len(item) == 3:
                file_id, destination, report_entry_id = item
                source = DownloadSource.MINERVA_TORRENT.value
                source_ref = None
            else:
                file_id, destination, report_entry_id, source, source_ref = item
            destination = str(Path(destination).expanduser())
            if (file_id, destination, source) in existing_keys:
                # Already queued — find and return its id.
                for rec in existing:
                    if rec.file_id == file_id and str(Path(rec.destination)) == destination and rec.source == source:
                        ids.append(rec.id)
                        break
                continue

            record_id = uuid.uuid4().hex
            record = QueueRecord(
                id=record_id,
                file_id=file_id,
                report_entry_id=report_entry_id,
                source=source,
                source_ref=source_ref,
                status=DownloadStatus.QUEUED.value,
                destination=destination,
                created_at=now,
                updated_at=now,
            )
            new_records.append(record)
            ids.append(record_id)
            # Track this new record so we don't re-add duplicates within the batch.
            existing_keys.add((file_id, destination, source))

        # Batch-insert all new records in a single transaction (one connection,
        # one commit) instead of N individual save_queue_record calls.
        if new_records:
            self._state.save_queue_records_batch(new_records)
            # Batch-resolve all file specs in one query (instead of N
            # individual _resolve_spec calls that each open a DB connection).
            specs = self._resolve_specs_batch([r.file_id for r in new_records])
            for record in new_records:
                spec = specs.get(record.file_id)
                if spec is not None:
                    touched_torrents.add(spec.torrent_name)
            self._state.add_event(
                "download",
                f"Batch queued {len(new_records)} files",
            )
            # Single emit for the whole batch — UI rebuilds once, not N times.
            self._emit_changed()

        for torrent_name in touched_torrents:
            self._schedule_torrent_submission(torrent_name)
        return ids

    def pause(self, record_id: str) -> None:
        record = self._get_record(record_id)
        if record is None:
            return
        if not record.qbit_hash:
            self._update_group_status([record], DownloadStatus.PAUSED)
            return
        group = self._records_for_hash(record.qbit_hash)
        self._run_qbit_task(
            lambda: self._logged_in_clone().pause(record.qbit_hash or ""),
            lambda _result: self._update_group_status(group, DownloadStatus.PAUSED),
            "Pause failed",
        )

    def resume(self, record_id: str) -> None:
        record = self._get_record(record_id)
        if record is None:
            return
        if not record.qbit_hash:
            self._state.update_queue_record(
                record.id, status=DownloadStatus.QUEUED.value, error=None,
            )
            self._emit_changed()
            self._schedule_record_submission(record)
            return
        group = self._records_for_hash(record.qbit_hash)
        self._run_qbit_task(
            lambda: self._logged_in_clone().resume(record.qbit_hash or ""),
            lambda _result: self._update_group_status(group, DownloadStatus.DOWNLOADING),
            "Resume failed",
        )

    def retry(self, record_id: str) -> None:
        record = self._get_record(record_id)
        if record is None:
            return
        # If this record was the last user of its torrent, remove the
        # torrent from qBittorrent to avoid orphaning it.
        if record.qbit_hash:
            siblings = [
                r for r in self._state.list_queue()
                if r.id != record.id and r.qbit_hash == record.qbit_hash
            ]
            if not siblings:
                torrent_hash = record.qbit_hash
                self._run_qbit_task(
                    lambda: self._logged_in_clone().delete_torrent(
                        torrent_hash, delete_files=False,
                    ),
                    lambda _result: None,
                    "Could not remove orphaned torrent on retry",
                )
        self._state.update_queue_record(
            record.id,
            status=DownloadStatus.QUEUED.value,
            error=None,
            qbit_hash=None,
        )
        self._emit_changed()
        self._schedule_record_submission(record)

    def pause_all(self) -> None:
        # Snapshot active records once to avoid StopIteration if pause()
        # removes records from the active set mid-iteration.
        active = self.get_active_records()
        hashes = {r.qbit_hash for r in active if r.qbit_hash}
        for torrent_hash in hashes:
            record = next(
                (r for r in active if r.qbit_hash == torrent_hash),
                None,
            )
            if record is not None:
                self.pause(record.id)

    def resume_all(self) -> None:
        for record in self.get_queue():
            if record.status in {DownloadStatus.PAUSED.value, DownloadStatus.QUEUED.value}:
                self.resume(record.id)

    def remove(self, record_id: str, delete_files: bool = False) -> None:
        record = self._get_record(record_id)
        if record is None:
            return
        spec = self._resolve_spec(record.file_id)
        siblings = [
            r for r in self._state.list_queue()
            if r.id != record.id and r.qbit_hash and r.qbit_hash == record.qbit_hash
        ]

        if record.qbit_hash:
            torrent_hash = record.qbit_hash
            if siblings:
                if spec is not None:
                    self._run_qbit_task(
                        lambda: self._logged_in_clone().set_file_priority(
                            torrent_hash, [spec.qbit_file_index], 0,
                        ),
                        lambda _result: None,
                        "Could not deselect torrent file",
                    )
                else:
                    log.warning(
                        "remove: spec is None for file_id=%d with siblings; "
                        "cannot deselect file in qBittorrent",
                        record.file_id,
                    )
            else:
                self._run_qbit_task(
                    lambda: self._logged_in_clone().delete_torrent(
                        torrent_hash, delete_files=delete_files,
                    ),
                    lambda _result: None,
                    "Could not remove torrent",
                )

        if delete_files and record.destination:
            dest = Path(record.destination)
            if dest.is_file():
                try:
                    dest.unlink()
                except OSError as exc:
                    self.error.emit(f"Could not remove {dest}: {exc}")

        self._state.delete_queue_record(record.id)
        self._runtime.pop(record.id, None)
        self._state.add_event("download", f"Removed {record.id[:8]} from queue")
        self._sync_monitor_hashes()
        self._emit_changed()
        self._emit_runtime()

    def remove_many(self, record_ids: Iterable[str], delete_files: bool = False) -> None:
        for record_id in list(record_ids):
            self.remove(record_id, delete_files=delete_files)

    # ── Queries ──────────────────────────────────────────────────────────

    def get_queue(self) -> list[QueueRecord]:
        """Return all queue records enriched with display-only metadata.

        Each ``QueueRecord``'s ``filename``, ``torrent_name``, ``collection``
        and ``system`` fields are resolved from the index via the spec
        resolver so the Downloads page can render real file names without its
        own lookup pass.  Records whose spec cannot be resolved keep empty
        strings — they still display, just without rich metadata.
        """
        records = self._state.list_queue()
        for record in records:
            spec = self._resolve_spec(record.file_id)
            if spec is not None:
                record.filename = spec.basename
                record.torrent_name = spec.torrent_name
                record.collection = spec.collection
                record.system = spec.system
        return records

    def get_runtime(self) -> list[DownloadRuntime]:
        return list(self._runtime.values())

    def get_runtime_for(self, record_id: str) -> DownloadRuntime | None:
        return self._runtime.get(record_id)

    def get_active_records(self) -> list[QueueRecord]:
        active = {s.value for s in DOWNLOAD_ACTIVE_STATUSES}
        return [r for r in self._state.list_queue() if r.status in active]

    def has_active_downloads(self) -> bool:
        in_progress = {s.value for s in DOWNLOAD_IN_PROGRESS_STATUSES | {DownloadStatus.QUEUED}}
        return any(
            r.status in in_progress
            for r in self._state.list_queue()
        )

    # ── Monitoring lifecycle ─────────────────────────────────────────────

    def start_monitoring(self) -> None:
        if self._monitor_thread is not None:
            self._sync_monitor_hashes()
            return
        thread = QtCore.QThread(self)
        monitor = QbitMonitor(self._qbit_template.clone())
        monitor.moveToThread(thread)
        thread.started.connect(monitor.start)
        monitor.snapshot_ready.connect(self._on_snapshot)
        monitor.connection_changed.connect(self._on_connection_changed)
        monitor.error.connect(self.error.emit)
        self._monitor_hashes_requested.connect(
            monitor.set_tracked_hashes,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        self._monitor_stop_requested.connect(
            monitor.stop,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        monitor.stopped.connect(thread.quit)
        thread.finished.connect(monitor.deleteLater)
        thread.start()
        self._monitor = monitor
        self._monitor_thread = thread
        self._sync_monitor_hashes()

    def stop_monitoring(self, timeout_ms: int = 5000) -> None:
        if self._monitor_thread is None:
            return
        if self._monitor is not None and self._monitor_thread.isRunning():
            QtCore.QMetaObject.invokeMethod(
                self._monitor,
                "stop",
                QtCore.Qt.ConnectionType.BlockingQueuedConnection,
            )
        self._monitor_thread.quit()
        if not self._monitor_thread.wait(timeout_ms):
            self._monitor_thread.terminate()
            self._monitor_thread.wait(1000)
        self._monitor_thread.deleteLater()
        self._monitor_thread = None
        self._monitor = None

    def reconcile(self) -> None:
        """Re-queue orphaned records and submit unstarted ones.

        Handles two cases:
        1. QUEUED records without a hash — never submitted.
        2. Active records with a hash whose torrent vanished from qBittorrent
           (e.g. after a qBittorrent restart or data loss).
        """
        live_hashes: set[str] | None = None
        for record in self._state.list_queue():
            if record.status == DownloadStatus.QUEUED.value and not record.qbit_hash:
                self._schedule_record_submission(record)
                continue
            # STARTING records without a hash — the torrent submission
            # failed or was interrupted. Re-queue them so they get retried.
            if record.status == DownloadStatus.STARTING.value and not record.qbit_hash:
                log.info("reconcile: STARTING record %s has no hash, re-queuing", record.id[:8])
                self._state.update_queue_record(
                    record.id,
                    status=DownloadStatus.QUEUED.value,
                    qbit_hash=None,
                    error=None,
                )
                self._schedule_record_submission(record)
                continue
            # Active record with a hash — verify the torrent still exists.
            if record.qbit_hash and record.status in {
                DownloadStatus.STARTING.value,
                DownloadStatus.DOWNLOADING.value,
                DownloadStatus.PAUSED.value,
            }:
                if live_hashes is None:
                    try:
                        client = self._logged_in_clone()
                        live_hashes = {
                            t.get("hash", "") for t in client.list_torrents()
                        }
                    except Exception:
                        log.warning("reconcile: could not query torrent engine, skipping hash verification")
                        live_hashes = set()  # Empty set = skip vanished-torrent check
                if record.qbit_hash not in live_hashes:
                    log.info(
                        "reconcile: torrent %s vanished, re-queuing %s",
                        record.qbit_hash[:8], record.id[:8],
                    )
                    self._state.update_queue_record(
                        record.id,
                        status=DownloadStatus.QUEUED.value,
                        qbit_hash=None,
                        error=None,
                    )
                    self._schedule_record_submission(record)
        self._sync_monitor_hashes()

    # ── Snapshot reconciliation ──────────────────────────────────────────

    @QtCore.pyqtSlot(list)
    def _on_snapshot(self, torrents: list[TorrentInfo]) -> None:
        # Use cached queue records instead of DB query every poll.
        # _cache_queue_records() is refreshed on queue_changed signal.
        all_records = self._get_cached_queue()
        records_by_hash: dict[str, list[QueueRecord]] = defaultdict(list)
        for record in all_records:
            if record.qbit_hash:
                records_by_hash[record.qbit_hash].append(record)

        # Truly terminal: these records must never be processed by the
        # snapshot loop under any circumstances.
        dead_values = {s.value for s in DOWNLOAD_DEAD_STATUSES}
        done_values = {s.value for s in DOWNLOAD_DONE_STATUSES}
        changed = False
        total_speed = 0
        dirty_runtime_ids: set[str] = set()
        for ti in torrents:
            group = records_by_hash.get(ti.hash, [])
            total_speed += ti.dlspeed
            # Build file index dict once per torrent instead of linear search
            # per record. O(files) once vs O(records × files) per poll.
            file_by_index: dict[int, TorrentFileInfo] = {}
            for f in ti.files:
                file_by_index[f.index] = f
            for record in group:
                if record.status in dead_values:
                    continue
                spec = self._resolve_spec(record.file_id)
                file_snapshot = None
                if spec is not None:
                    file_snapshot = file_by_index.get(spec.qbit_file_index)
                file_progress = file_snapshot.progress if file_snapshot is not None else ti.progress
                new_rt = DownloadRuntime(
                    record_id=record.id,
                    qbit_hash=ti.hash,
                    torrent_name=ti.name,
                    progress=file_progress,
                    download_speed=ti.dlspeed,
                    upload_speed=ti.upspeed,
                    eta=ti.eta,
                    peers=ti.peers,
                    seeds=ti.seeds,
                    ratio=ti.ratio,
                    save_path=ti.save_path,
                    raw_state=ti.state,
                )
                old_rt = self._runtime.get(record.id)
                if old_rt is None or old_rt != new_rt:
                    self._runtime[record.id] = new_rt
                    dirty_runtime_ids.add(record.id)
                new_status = self._map_qbit_state(ti.state, file_progress, ti.dlspeed)
                # Post-download records: only allow COMPLETED↔SEEDING
                # transitions; never regress to DOWNLOADING or earlier.
                if record.status in done_values:
                    if new_status in {DownloadStatus.COMPLETED, DownloadStatus.SEEDING}:
                        if record.status != new_status.value:
                            self._state.update_queue_record(record.id, status=new_status.value)
                            changed = True
                    continue
                if new_status == DownloadStatus.COMPLETED and record.status != DownloadStatus.COMPLETED.value:
                    self._schedule_completed_file(record, ti, new_status)
                elif record.status != new_status.value:
                    self._state.update_queue_record(record.id, status=new_status.value)
                    changed = True

        # Prune stale runtime entries for records in terminal/dead
        # statuses whose torrents are no longer in the snapshot.
        snapshot_hashes = {ti.hash for ti in torrents}
        dead_vals = {s.value for s in DOWNLOAD_DEAD_STATUSES}
        done_vals = {s.value for s in DOWNLOAD_DONE_STATUSES}
        stale_ids = [
            rid for rid, rt in self._runtime.items()
            if rt.qbit_hash not in snapshot_hashes
            and any(
                r.id == rid and (r.status in dead_vals or r.status in done_vals)
                for records in records_by_hash.values()
                for r in records
            )
        ]
        for rid in stale_ids:
            del self._runtime[rid]

        self._emit_runtime(dirty_runtime_ids)
        if total_speed:
            self.activity_event.emit("download-speed", str(total_speed))
        if changed:
            self._emit_changed()

    def _schedule_record_submission(self, record: QueueRecord) -> None:
        # Route by source — archive.org records don't use the local torrent index
        if record.source == DownloadSource.ARCHIVE_ORG_HTTP.value:
            self._submit_http(record)
            return
        if record.source == DownloadSource.ARCHIVE_ORG_TORRENT.value:
            self._submit_archive_org_torrent(record)
            return
        # Default: Minerva torrent (existing path)
        spec = self._resolve_spec(record.file_id)
        if spec is None:
            self._state.update_queue_record(
                record.id,
                status=DownloadStatus.FAILED.value,
                error=f"Index file {record.file_id} no longer exists",
            )
            self._emit_changed()
            return
        self._schedule_torrent_submission(spec.torrent_name)

    def _schedule_torrent_submission(self, torrent_name: str) -> None:
        if not self._app_state.qbit_state:
            # qBittorrent is disconnected — defer until reconnect.
            # reconcile() re-schedules QUEUED records on the next
            # successful connection.
            return
        with self._sets_lock:
            if torrent_name in self._submitting_torrents:
                self._resubmit_torrents.add(torrent_name)
                return
            self._submitting_torrents.add(torrent_name)
        QtCore.QTimer.singleShot(0, lambda name=torrent_name: self._submit_torrent_group(name))

    def _submit_torrent_group(self, torrent_name: str) -> None:
        group: list[tuple[QueueRecord, DownloadFileSpec]] = []
        for record in self._state.list_queue():
            if record.status == DownloadStatus.CANCELLED.value:
                continue
            spec = self._resolve_spec(record.file_id)
            if spec is not None and spec.torrent_name == torrent_name:
                group.append((record, spec))
        if not group:
            return
        torrent_path = group[0][1].torrent_path
        if not torrent_path.is_file():
            message = f"Torrent file not found: {torrent_path}"
            for record, _spec in group:
                self._state.update_queue_record(
                    record.id, status=DownloadStatus.FAILED.value, error=message,
                )
            self.error.emit(message)
            self._emit_changed()
            return

        existing_hash = next((r.qbit_hash for r, _ in group if r.qbit_hash), None)
        # Only include indices for active records — FAILED/CANCELLED/COMPLETED/SEEDING
        # records should not have their files prioritized in qBittorrent.
        _active_values = {s.value for s in DOWNLOAD_ACTIVE_STATUSES}
        target_indices = sorted({
            spec.qbit_file_index
            for record, spec in group
            if record.status in _active_values
        })
        group_ids = [
            record.id for record, _spec in group
            if record.status in {
                DownloadStatus.QUEUED.value,
                DownloadStatus.STARTING.value,
                DownloadStatus.DOWNLOADING.value,
                DownloadStatus.PAUSED.value,
            }
        ]
        if not group_ids:
            return
        for record, _spec in group:
            if record.id in group_ids and record.status != DownloadStatus.PAUSED.value:
                self._state.update_queue_record(
                    record.id, status=DownloadStatus.STARTING.value, error=None,
                )
        self._emit_changed()
        self._seed_dir.mkdir(parents=True, exist_ok=True)

        def operation() -> dict[str, object]:
            client = self._logged_in_clone()
            torrent_hash = existing_hash or client.add_torrent_paused(
                str(torrent_path), str(self._seed_dir),
            )
            if not torrent_hash:
                raise QBittorrentError("qBittorrent accepted the torrent but no hash was found")
            files = client.get_files(torrent_hash)
            # Retry a few times if qBittorrent hasn't finished processing
            # the torrent metadata yet (returns empty file list).
            for _ in range(5):
                if files:
                    break
                time.sleep(0.5)
                files = client.get_files(torrent_hash)
            available = {int(item.get("index", -1)) for item in files}
            missing = [idx for idx in target_indices if idx not in available]
            if missing:
                raise QBittorrentError(
                    f"Torrent file indices not found: {', '.join(map(str, missing))}"
                )
            all_indices = sorted(idx for idx in available if idx >= 0)
            client.set_file_priority(torrent_hash, all_indices, 0)
            client.set_file_priority(torrent_hash, target_indices, 1)
            # Only resume if no records were user-paused. If the user
            # explicitly paused this torrent, respect that and don't auto-resume.
            user_paused = any(
                rec.status == DownloadStatus.PAUSED.value
                for rec, _ in group
                if rec.id in group_ids
            )
            if not user_paused:
                try:
                    client.resume(torrent_hash)
                except Exception as exc:
                    log.warning("Could not resume torrent (user may need to resume manually): %s", exc)

            # Rename torrent to a friendly display name including collection/system.
            try:
                # ponytail: pick the first active record's spec so the display
                # name reflects the actual selection, not a cancelled neighbour.
                first_spec = next(
                    (spec for rec, spec in group
                     if rec.id in group_ids),
                    group[0][1],
                )
                if len(target_indices) == 1:
                    stem = Path(first_spec.basename).stem
                    display_name = f"{first_spec.collection} - {first_spec.system} - {stem}"
                else:
                    display_name = (
                        f"{first_spec.collection} - {first_spec.system}"
                        f" - {len(target_indices)} selected files"
                    )
                client.rename(torrent_hash, display_name)
            except Exception as exc:
                log.warning("Could not rename torrent in qBittorrent: %s", exc)

            return {"hash": torrent_hash, "record_ids": group_ids}

        def succeeded(result: object) -> None:
            payload = dict(result)
            torrent_hash = str(payload["hash"])
            for record_id in payload["record_ids"]:
                current = self._get_record(str(record_id))
                if current is None or current.status in {
                    DownloadStatus.CANCELLED.value,
                    DownloadStatus.COMPLETED.value,
                    DownloadStatus.SEEDING.value,
                    DownloadStatus.FAILED.value,
                    DownloadStatus.PAUSED.value,
                }:
                    # PAUSED records: user paused manually — respect that;
                    # just set the qbit_hash so we can track the torrent,
                    # but keep the PAUSED status.
                    if current is not None and current.status == DownloadStatus.PAUSED.value:
                        self._state.update_queue_record(
                            current.id, qbit_hash=torrent_hash, error=None,
                        )
                    continue
                self._state.update_queue_record(
                    current.id,
                    status=DownloadStatus.DOWNLOADING.value,
                    qbit_hash=torrent_hash,
                    error=None,
                )
            self._state.add_event(
                "download",
                f"Started {len(group_ids)} selected file(s) from {torrent_name}",
            )
            self._sync_monitor_hashes()
            self._emit_changed()

        def failed(message: str) -> None:
            for record_id in group_ids:
                current = self._get_record(record_id)
                if current is not None and current.status == DownloadStatus.STARTING.value:
                    self._state.update_queue_record(
                        current.id,
                        status=DownloadStatus.FAILED.value,
                        qbit_hash=None,
                        error=message,
                    )
            self.error.emit(f"Could not start {torrent_name}: {message}")
            self._emit_changed()

        def finished() -> None:
            with self._sets_lock:
                self._submitting_torrents.discard(torrent_name)
                needs_resubmit = torrent_name in self._resubmit_torrents
                if needs_resubmit:
                    self._resubmit_torrents.discard(torrent_name)
            if needs_resubmit:
                self._schedule_torrent_submission(torrent_name)

        self._run_task(operation, succeeded, failed, finished)

    # ── Archive.org HTTP download ───────────────────────────────────────

    def _submit_http(self, record: QueueRecord) -> None:
        """Download a file from archive.org via HTTP streaming."""
        if not record.source_ref:
            self._state.update_queue_record(
                record.id,
                status=DownloadStatus.FAILED.value,
                error="Missing source_ref for archive.org HTTP download",
            )
            self._emit_changed()
            return

        parts = record.source_ref.split("/", 1)
        if len(parts) != 2:
            self._state.update_queue_record(
                record.id,
                status=DownloadStatus.FAILED.value,
                error=f"Invalid source_ref: {record.source_ref}",
            )
            self._emit_changed()
            return

        identifier, filename = parts
        from urllib.parse import quote

        url = f"https://archive.org/download/{identifier}/{quote(filename)}"

        self._state.update_queue_record(
            record.id,
            status=DownloadStatus.DOWNLOADING.value,
            error=None,
        )
        self._emit_changed()

        def operation() -> Path:
            from minerva.services.http_download import HttpDownloadAdapter

            adapter = HttpDownloadAdapter()
            return adapter.download(url=url, destination=Path(record.destination))

        def succeeded(result: object) -> None:
            self._state.update_queue_record(
                record.id,
                status=DownloadStatus.COMPLETED.value,
                error=None,
            )
            self._state.add_event("download", f"Completed HTTP download: {record.source_ref}")
            self._emit_changed()

        def failed(message: str) -> None:
            self._state.update_queue_record(
                record.id,
                status=DownloadStatus.FAILED.value,
                error=message,
            )
            self.error.emit(f"HTTP download failed: {message}")
            self._emit_changed()

        self._run_task(operation, succeeded, failed)

    # ── Archive.org torrent download ────────────────────────────────────

    def _submit_archive_org_torrent(self, record: QueueRecord) -> None:
        """Download via archive.org's torrent file using qBittorrent."""
        if not record.source_ref:
            self._state.update_queue_record(
                record.id,
                status=DownloadStatus.FAILED.value,
                error="Missing source_ref for archive.org torrent download",
            )
            self._emit_changed()
            return

        parts = record.source_ref.split("/", 1)
        if len(parts) != 2:
            self._state.update_queue_record(
                record.id,
                status=DownloadStatus.FAILED.value,
                error=f"Invalid source_ref: {record.source_ref}",
            )
            self._emit_changed()
            return

        identifier, filename = parts
        torrent_url = f"https://archive.org/download/{identifier}/{identifier}_archive.torrent"

        self._state.update_queue_record(
            record.id,
            status=DownloadStatus.STARTING.value,
            error=None,
        )
        self._emit_changed()
        self._seed_dir.mkdir(parents=True, exist_ok=True)

        def operation() -> dict[str, object]:
            import tempfile

            client = self._logged_in_clone()
            response = requests.get(torrent_url, timeout=30)
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(
                suffix=".torrent", dir=str(self._seed_dir), delete=False
            ) as tf:
                tf.write(response.content)
                torrent_path = Path(tf.name)

            torrent_hash = client.add_torrent_paused(str(torrent_path), str(self._seed_dir))
            if not torrent_hash:
                raise QBittorrentError("qBittorrent accepted the torrent but no hash was found")

            files = client.get_files(torrent_hash)
            for _ in range(5):
                if files:
                    break
                time.sleep(0.5)
                files = client.get_files(torrent_hash)

            target_index = None
            all_indices = []
            for item in files:
                idx = int(item.get("index", -1))
                if idx >= 0:
                    all_indices.append(idx)
                if filename.lower() in str(item.get("name", "")).lower():
                    target_index = idx

            if target_index is None:
                target_index = all_indices[0] if all_indices else 0
                log.warning("Could not find %s in archive.org torrent, using first file", filename)

            client.set_file_priority(torrent_hash, all_indices, 0)
            client.set_file_priority(torrent_hash, [target_index], 1)
            client.resume(torrent_hash)

            try:
                torrent_path.unlink()
            except OSError:
                pass

            return {"hash": torrent_hash, "record_ids": [record.id]}

        def succeeded(result: object) -> None:
            payload = dict(result)
            torrent_hash = str(payload["hash"])
            self._state.update_queue_record(
                record.id,
                status=DownloadStatus.DOWNLOADING.value,
                qbit_hash=torrent_hash,
                error=None,
            )
            self._state.add_event("download", f"Started archive.org torrent: {identifier}")
            self._sync_monitor_hashes()
            self._emit_changed()

        def failed(message: str) -> None:
            self._state.update_queue_record(
                record.id,
                status=DownloadStatus.FAILED.value,
                error=message,
            )
            self.error.emit(f"Archive.org torrent failed: {message}")
            self._emit_changed()

        self._run_task(operation, succeeded, failed)

    # ── Completion / exposure ────────────────────────────────────────────

    def _schedule_completed_file(
        self,
        record: QueueRecord,
        torrent: TorrentInfo,
        final_status: DownloadStatus,
    ) -> None:
        with self._sets_lock:
            if record.id in self._linking_records:
                return
        spec = self._resolve_spec(record.file_id)
        if spec is None:
            self._state.update_queue_record(
                record.id,
                status=DownloadStatus.FAILED.value,
                error="Index metadata disappeared before completion",
            )
            self._emit_changed()
            return
        with self._sets_lock:
            self._linking_records.add(record.id)

        def operation() -> Path:
            client = self._logged_in_clone()
            files = client.get_files(torrent.hash)
            remote = next(
                (item for item in files if int(item.get("index", -1)) == spec.qbit_file_index),
                None,
            )
            if remote is None:
                raise FileNotFoundError(
                    f"qBittorrent file index {spec.qbit_file_index} is missing"
                )
            remote_name = str(remote.get("name", ""))
            save_path = Path(torrent.save_path)
            candidates = [
                save_path / remote_name,
                save_path / torrent.name / spec.path_in_torrent,
                save_path / spec.path_in_torrent,
            ]
            source = next((p for p in candidates if p.is_file()), None)
            if source is None:
                for candidate in save_path.rglob(spec.basename):
                    if candidate.is_file() and candidate.stat().st_size == spec.size:
                        source = candidate
                        break
            if source is None:
                raise FileNotFoundError(
                    f"Downloaded file not found for {spec.path_in_torrent}"
                )
            actual_size = source.stat().st_size
            if spec.size > 0 and actual_size != spec.size:
                raise OSError(
                    f"Downloaded size mismatch: expected {spec.size}, got {actual_size}"
                )

            destination = Path(record.destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                try:
                    if destination.samefile(source):
                        return destination
                except OSError:
                    pass
                raise FileExistsError(
                    f"Destination already exists and is not the downloaded file: {destination}"
                )
            try:
                os.link(source, destination)
            except OSError:
                if not self._allow_copy_fallback:
                    raise
                shutil.copy2(source, destination)
            if spec.size > 0 and destination.stat().st_size != spec.size:
                destination.unlink(missing_ok=True)
                raise OSError("Destination verification failed after exposure")
            return destination

        def succeeded(result: object) -> None:
            destination = Path(result)
            self._state.update_queue_record(
                record.id,
                status=final_status.value,
                error=None,
            )
            self._state.add_event("hardlink", f"Ready: {destination}")
            self.activity_event.emit("check", f"Completed {destination.name}")
            self._emit_changed()

        def failed(message: str) -> None:
            record_hash = record.qbit_hash
            self._state.update_queue_record(
                record.id,
                status=DownloadStatus.FAILED.value,
                error=message,
            )
            # Remove the orphan torrent if no other record needs it.
            if record_hash:
                siblings = [
                    r for r in self._state.list_queue()
                    if r.id != record.id and r.qbit_hash == record_hash
                ]
                if not siblings:
                    self._run_qbit_task(
                        lambda: self._logged_in_clone().delete_torrent(
                            record_hash, delete_files=False,
                        ),
                        lambda _result: None,
                        "Could not remove orphaned torrent after exposure failure",
                    )
            self._state.add_event("error", f"Exposure failed: {message}")
            self.error.emit(message)
            self._emit_changed()

        def finished() -> None:
            with self._sets_lock:
                self._linking_records.discard(record.id)

        self._run_task(operation, succeeded, failed, finished)

    # ── Helpers ──────────────────────────────────────────────────────────

    def _resolve_spec(self, file_id: int) -> DownloadFileSpec | None:
        if file_id in self._spec_cache:
            return self._spec_cache[file_id]
        if self.file_spec_resolver is None:
            log.error("_resolve_spec: file_spec_resolver is None, cannot resolve file_id=%d", file_id)
            return None
        spec = self.file_spec_resolver(file_id)
        if spec is not None:
            self._spec_cache[file_id] = spec
        else:
            log.warning("_resolve_spec: no spec found for file_id=%d", file_id)
        return spec

    def _resolve_specs_batch(self, file_ids: list[int]) -> dict[int, DownloadFileSpec]:
        """Resolve multiple file specs in one DB query, caching results.

        Falls back to per-file _resolve_spec if the batch resolver is
        unavailable (e.g., file_spec_resolver doesn't support batching).
        """
        result: dict[int, DownloadFileSpec] = {}
        # Check cache first — avoid querying for specs we already have.
        missing = [fid for fid in file_ids if fid not in self._spec_cache]
        if not missing:
            return {fid: self._spec_cache[fid] for fid in file_ids if fid in self._spec_cache}

        if self.file_spec_resolver is None:
            log.error("_resolve_specs_batch: file_spec_resolver is None")
            return {}

        # Try to find the underlying MinervaDB for batch resolution.
        # The resolver may be a bound method or a lambda wrapping one.
        batch_fn = None
        resolver_obj = getattr(self.file_spec_resolver, "__self__", None)
        if resolver_obj is not None:
            batch_fn = getattr(resolver_obj, "get_download_specs_batch", None)
        if batch_fn is None:
            # Lambda case: extract the captured db from closure cells.
            closure = getattr(self.file_spec_resolver, "__closure__", None)
            if closure:
                for cell in closure:
                    obj = cell.cell_contents
                    batch_fn = getattr(obj, "get_download_specs_batch", None)
                    if batch_fn is not None:
                        break

        if batch_fn is not None:
            try:
                specs = batch_fn(missing, self._resolver_torrent_dir)
                for fid, spec in specs.items():
                    self._spec_cache[fid] = spec
                result.update(specs)
            except Exception:
                log.debug("_resolve_specs_batch: batch failed, falling back to per-file", exc_info=True)
                for fid in missing:
                    spec = self._resolve_spec(fid)
                    if spec is not None:
                        result[fid] = spec
        else:
            for fid in missing:
                spec = self._resolve_spec(fid)
                if spec is not None:
                    result[fid] = spec

        return result

    def _on_index_changed(self, _state: object) -> None:
        """Drop the spec cache when the index is rebuilt."""
        self._spec_cache.clear()

    def _logged_in_clone(self) -> QBittorrentClient:
        client = self._qbit_template.clone()
        client.login()
        return client

    def _records_for_hash(self, torrent_hash: str) -> list[QueueRecord]:
        return [r for r in self._state.list_queue() if r.qbit_hash == torrent_hash]

    def _get_record(self, record_id: str) -> QueueRecord | None:
        return next((r for r in self._state.list_queue() if r.id == record_id), None)

    def _update_group_status(
        self,
        records: Iterable[QueueRecord],
        status: DownloadStatus,
    ) -> None:
        changed = False
        for record in records:
            current = self._get_record(record.id)
            if current is not None:
                self._state.update_queue_record(current.id, status=status.value)
                changed = True
        if changed:
            self._emit_changed()

    def _sync_monitor_hashes(self) -> None:
        hashes = {
            r.qbit_hash for r in self.get_active_records()
            if r.qbit_hash
        }
        self._monitor_hashes_requested.emit(hashes)
    def _get_cached_queue(self) -> list[QueueRecord]:
        """Return cached queue records, loading from DB only when stale.

        The cache is invalidated by ``_emit_changed()`` (which fires on
        queue mutations). Snapshot polls hit this cache instead of
        querying SQLite every second.
        """
        if self._queue_cache is None:
            self._queue_cache = self._state.list_queue()
        return self._queue_cache

    def _emit_changed(self) -> None:
        self._queue_cache = None
        self.queue_changed.emit()
        self._app_state.queue_changed.emit()

    def _emit_runtime(self, dirty_ids: set[str] | None = None) -> None:
        """Emit runtime telemetry. Always sends the full runtime list so
        the view can compute aggregate speeds. If ``dirty_ids`` is
        provided, also emit the delta set so the view can skip
        repainting unchanged rows."""
        self.runtime_changed.emit(self.get_runtime())
        if dirty_ids is not None:
            self.runtime_dirty.emit(dirty_ids)

    @classmethod
    def _map_qbit_state(
        cls, raw_state: str, progress: float, dlspeed: int = 0,
    ) -> DownloadStatus:
        if progress >= 1.0:
            if raw_state in cls._ACTIVE_UPLOAD_STATES:
                return DownloadStatus.SEEDING
            return DownloadStatus.COMPLETED
        # pausedUP with progress < 1.0 — could be a transient state
        # where the torrent has partial data. If actively downloading,
        # treat as downloading, not paused.
        if raw_state == "pausedUP":
            if dlspeed > 0:
                return DownloadStatus.DOWNLOADING
            return DownloadStatus.PAUSED
        return cls._QBT_STATUS_MAP.get(raw_state, DownloadStatus.DOWNLOADING)

    def _run_qbit_task(
        self,
        operation: Callable[[], object],
        succeeded: Callable[[object], None],
        error_prefix: str,
    ) -> None:
        self._run_task(
            operation,
            succeeded,
            lambda message: self.error.emit(f"{error_prefix}: {message}"),
        )

    def _run_task(
        self,
        operation: Callable[[], object],
        succeeded: Callable[[object], None],
        failed: Callable[[str], None],
        finished: Callable[[], None] | None = None,
    ) -> None:
        task = _FunctionTask(operation)
        self._tasks.add(task)
        task.signals.succeeded.connect(succeeded)
        task.signals.failed.connect(failed)

        def cleanup() -> None:
            if finished is not None:
                finished()
            self._tasks.discard(task)

        task.signals.finished.connect(cleanup)
        self._pool.start(task)
