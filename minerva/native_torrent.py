"""Native torrent session backed by libtorrent.

This module is Minerva's only torrent backend. It uses libtorrent's Python
bindings directly, with no external torrent daemon, Web API, or external torrent client
involved.

Seeding policy is enforced per-torrent via libtorrent's
``seed_time_limit`` and ``share_ratio_limit`` settings, so users cannot
bypass it by changing client settings (there is no separate client).

Usage::

    from minerva.native_torrent import NativeTorrentSession

    session = NativeTorrentSession(save_dir="downloads")
    session.start()

    h = session.add_torrent_paused("/path/to/file.torrent", "/save/dir")
    files = session.get_files(h)
    session.set_file_priority(h, [i for i, f in enumerate(files) if skip], 0)
    session.resume(h)

    # Poll for completion
    info = session.get_torrent_info(h)
    if info and info["progress"] >= 1.0:
        ...
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

try:
    import libtorrent as lt
except ModuleNotFoundError as exc:  # pragma: no cover - exercised in unprovisioned envs
    lt = None  # type: ignore[assignment]
    _LIBTORRENT_IMPORT_ERROR: ModuleNotFoundError | None = exc
else:
    _LIBTORRENT_IMPORT_ERROR = None

log = logging.getLogger(__name__)


class NativeTorrentError(RuntimeError):
    """Base exception for native torrent session errors."""


class NativeTorrentSession:
    """In-process torrent session backed by libtorrent.

    Seeding policy:
        * ``seed_ratio`` — target share ratio (default 2.0).  When a torrent
          reaches this ratio, it is auto-paused.
        * ``seed_time_hours`` — maximum seeding time in hours (default 48).
          When a torrent has seeded this long, it is auto-paused.
        * ``max_active_downloads`` — concurrent downloading torrents (default 3).
        * ``max_active_seeds`` — concurrent seeding torrents (default 5).
    """

    # ── Priority constants ─────────────────────────────────────────────
    PRIORITY_SKIP = 0
    PRIORITY_NORMAL = 1
    PRIORITY_HIGH = 6
    PRIORITY_MAX = 7

    def __init__(
        self,
        save_dir: str | Path = "downloads",
        seed_ratio: float = 2.0,
        seed_time_hours: int = 48,
        max_active_downloads: int = 3,
        max_active_seeds: int = 5,
        listen_interfaces: str = "0.0.0.0:6881,[::]:6881",
    ) -> None:
        self._save_dir = Path(save_dir).expanduser().resolve()
        self._save_dir.mkdir(parents=True, exist_ok=True)
        self._seed_ratio = seed_ratio
        self._seed_time_seconds = seed_time_hours * 3600
        self._max_active_downloads = max_active_downloads
        self._max_active_seeds = max_active_seeds
        self._listen_interfaces = listen_interfaces

        self._session: lt.session | None = None
        self._handles: dict[str, lt.torrent_handle] = {}
        # Cache of latest torrent_status per info_hash, updated by
        # state_update_alert (delta-only from libtorrent).
        self._status_cache: dict[str, lt.torrent_status] = {}
        self._alert_thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.RLock()

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Create the libtorrent session and start the alert pump thread."""
        self._require_libtorrent()
        with self._lock:
            if self._session is not None:
                return
            self._session = lt.session()
            settings = self._session.get_settings()
            settings["listen_interfaces"] = self._listen_interfaces
            settings["active_downloads"] = self._max_active_downloads
            settings["active_seeds"] = self._max_active_seeds
            settings["seed_time_limit"] = self._seed_time_seconds
            settings["share_ratio_limit"] = int(self._seed_ratio * 100)
            settings["enable_dht"] = True
            settings["enable_lsd"] = True
            settings["enable_natpmp"] = True
            settings["enable_upnp"] = True
            self._session.apply_settings(settings)
            self._running = True
            self._alert_thread = threading.Thread(
                target=self._pump_alerts, daemon=True, name="libtorrent-alerts",
            )
            self._alert_thread.start()
        log.info(
            "NativeTorrentSession started (seed_ratio=%.1f, seed_time=%dh)",
            self._seed_ratio, self._seed_time_seconds // 3600,
        )
    def stop(self) -> None:
        """Stop the alert pump, flush resume data, and destroy the session."""
        self._running = False
        with self._lock:
            session = self._session
            handles = list(self._handles.values())
        if session is None:
            return
        # Request save_resume_data for all valid handles.
        pending = 0
        for handle in handles:
            if handle.is_valid():
                handle.save_resume_data(lt.torrent_handle.save_info_dict)
                pending += 1
        # Wait for resume data alerts (max 5s) on the calling thread.
        deadline = time.monotonic() + 5.0
        while pending > 0 and time.monotonic() < deadline:
            with self._lock:
                if self._session is None:
                    break
                alert = self._session.wait_for_alert(
                    int((deadline - time.monotonic()) * 1000),
                )
                if alert is None:
                    break
                alerts = self._session.pop_alerts()
            for a in alerts:
                if type(a).__name__ == "save_resume_data_alert":
                    self._save_resume_data(a)
                    pending -= 1
        if self._alert_thread is not None:
            self._alert_thread.join(timeout=5)
            self._alert_thread = None
        with self._lock:
            self._session = None
            self._handles.clear()
            self._status_cache.clear()
        log.info("NativeTorrentSession stopped")
    # ── Public API ───────────────────────────────────────────────────────

    @property
    def is_logged_in(self) -> bool:
        """Always True — the session is in-process, no login needed."""
        return self._session is not None

    def clone(self) -> "NativeTorrentSession":
        """Return self — the session is shared, no per-call clone needed."""
        return self

    def login(self) -> bool:
        """No-op — the session is always available once started."""
        if self._session is None:
            self.start()
        return True

    def test_connection(self) -> str:
        """Return the loaded libtorrent version string."""
        self._require_libtorrent()
        return f"libtorrent {lt.__version__}"

    def add_torrent_paused(
        self, torrent_path: str, save_path: str,
    ) -> str:
        """Add a torrent in paused state.  Returns the info hash hex string."""
        self._ensure_session()
        try:
            ti = lt.torrent_info(str(torrent_path))
            info_hash = str(ti.info_hash())
            # Default: fresh add, paused
            atp = lt.add_torrent_params()
            atp.ti = ti
            atp.save_path = str(save_path)
            atp.flags = lt.torrent_flags.paused
            # Load resume data if available (crash recovery)
            resume_file = self._save_dir / f"{info_hash}.fastresume"
            if resume_file.is_file():
                try:
                    atp = lt.read_resume_data(resume_file.read_bytes())
                    # Merge: resume data may lack the info dict or save_path
                    atp.ti = ti
                    atp.save_path = str(save_path)
                    log.info("Loaded resume data for %s", info_hash[:8])
                except Exception:
                    log.warning(
                        "Failed to load resume data for %s",
                        info_hash[:8], exc_info=True,
                    )
            with self._lock:
                if self._session is None:
                    raise NativeTorrentError("libtorrent session not started")
                handle = self._session.add_torrent(atp)
                self._handles[info_hash] = handle
        except NativeTorrentError:
            raise
        except RuntimeError as exc:
            raise NativeTorrentError(f"Failed to add torrent: {exc}") from exc
        log.info(
            "Added torrent (paused): %s -> %s",
            Path(torrent_path).name, info_hash[:8],
        )
        return info_hash
    def get_files(self, torrent_hash: str) -> list[dict]:
        """Return file list for a torrent.  Each entry has 'index', 'name', 'size', 'priority'."""
        with self._lock:
            if self._session is None:
                raise NativeTorrentError("Session not started")
            handle = self._handles.get(torrent_hash)
        if handle is None:
            return []
        try:
            status = handle.status()
            ti = status.torrent_file
            if ti is None:
                ti = handle.get_torrent_info()
            if ti is None:
                return []
            files = ti.files()
        except RuntimeError as exc:
            log.warning("get_files: failed to get torrent info for %s: %s", torrent_hash[:8], exc)
            return []
        result: list[dict] = []
        # file_progress() returns a list of bytes downloaded per file
        try:
            fp_list = handle.file_progress()
        except RuntimeError:
            fp_list = []
        try:
            pri_list = handle.get_file_priorities()
        except RuntimeError:
            pri_list = []
        for i in range(files.num_files()):
            fsize = files.file_size(i)
            if i < len(fp_list) and fsize > 0:
                file_progress = float(fp_list[i]) / fsize
            else:
                file_progress = 0.0
            result.append(
                {
                    "index": i,
                    "name": files.file_path(i),
                    "size": fsize,
                    "progress": file_progress,
                    "priority": int(pri_list[i]) if i < len(pri_list) else 0,
                }
            )
        return result

    def set_file_priority(
        self, torrent_hash: str, file_ids: list[int], priority: int = 0,
    ) -> None:
        """Set file priority for multiple files at once.

        0 = skip, 1 = normal, 6 = high, 7 = max.
        Uses prioritize_files() for batch efficiency instead of per-file calls.
        """
        with self._lock:
            if self._session is None:
                raise NativeTorrentError("Session not started")
            handle = self._handles.get(torrent_hash)
        if handle is None:
            return
        lt_priority = {
            0: 0,   # skip
            1: 4,   # normal
            6: 6,   # high
            7: 7,   # max
        }.get(priority, 4)
        # Read current priorities, update target indices, apply in one call.
        try:
            ti = handle.status().torrent_file
            if ti is None:
                ti = handle.get_torrent_info()
            num_files = ti.num_files()
            priorities = handle.get_file_priorities() if num_files else []
            # If priorities list is wrong length, pad with current default (1=normal)
            # not 0 (skip) to avoid unintentionally de-prioritizing files.
            while len(priorities) < num_files:
                priorities.append(1)
            for fid in file_ids:
                if 0 <= fid < num_files:
                    priorities[fid] = lt_priority
            handle.prioritize_files(priorities)
            # Give libtorrent time to apply priorities for large torrents.
            # prioritize_files() is async — get_file_priorities() may return
            # stale values if called immediately after.
            if num_files > 100:
                time.sleep(0.1)
        except RuntimeError:
            # Fallback: per-file priority setting
            for fid in file_ids:
                try:
                    handle.file_priority(fid, lt_priority)
                except RuntimeError as exc:
                    log.debug("set_file_priority: per-file fallback failed for %d: %s", fid, exc)

    def pause(self, torrent_hash: str) -> None:
        """Pause a running torrent."""
        with self._lock:
            if self._session is None:
                raise NativeTorrentError("Session not started")
            handle = self._handles.get(torrent_hash)
        if handle is not None:
            handle.pause()

    def resume(self, torrent_hash: str) -> None:
        """Resume a paused torrent."""
        with self._lock:
            if self._session is None:
                raise NativeTorrentError("Session not started")
            handle = self._handles.get(torrent_hash)
        if handle is not None:
            handle.resume()


    def list_torrents(self, hashes: list[str] | None = None) -> list[dict]:
        """Return torrent snapshots, optionally restricted to *hashes*.

        Uses ``_status_cache`` populated by ``state_update_alert`` (delta
        updates from libtorrent). Falls back to ``handle.status()`` for
        torrents not yet in the cache (e.g. just added).
        """
        with self._lock:
            if self._session is None:
                raise NativeTorrentError("Session not started")
            handles = dict(self._handles)
            status_cache = dict(self._status_cache)
        result: list[dict] = []
        for info_hash, handle in handles.items():
            if hashes is not None and info_hash not in hashes:
                continue
            if not handle.is_valid():
                continue
            # Use cached status from state_update_alert if available;
            # otherwise fall back to synchronous status() call.
            status = status_cache.get(info_hash)
            if status is None:
                try:
                    status = handle.status()
                except RuntimeError as exc:
                    log.debug("list_torrents: status() failed for %s: %s", info_hash[:8], exc)
                    continue
            result.append(self._status_to_dict(status, info_hash))
        return result

    def get_torrent_info(self, torrent_hash: str) -> dict | None:
        """Get torrent status info, using cached status when available."""
        with self._lock:
            if self._session is None:
                raise NativeTorrentError("Session not started")
            handle = self._handles.get(torrent_hash)
            status = self._status_cache.get(torrent_hash)
        if handle is None or not handle.is_valid():
            return None
        if status is None:
            try:
                status = handle.status()
            except RuntimeError as exc:
                log.debug("get_torrent_info: status() failed for %s: %s", torrent_hash[:8], exc)
                return None
        return self._status_to_dict(status, torrent_hash)

    def delete_torrent(self, torrent_hash: str, delete_files: bool = False) -> None:
        """Remove torrent from the session."""
        with self._lock:
            if self._session is None:
                raise NativeTorrentError("Session not started")
            handle = self._handles.get(torrent_hash)
            if handle is None:
                return
            flags = lt.session.delete_files if delete_files else 0
            self._session.remove_torrent(handle, flags)
            self._handles.pop(torrent_hash, None)
            self._status_cache.pop(torrent_hash, None)
        # Clean up the .fastresume file (crash recovery data)
        resume_file = self._save_dir / f"{torrent_hash}.fastresume"
        try:
            resume_file.unlink(missing_ok=True)
        except OSError:
            pass
        log.info("Removed torrent: %s (delete_files=%s)", torrent_hash[:8], delete_files)

    def rename(self, torrent_hash: str, new_name: str) -> None:
        """Rename a torrent in the session.

        Note: libtorrent does not have a direct 'rename torrent' API.
        The torrent name is derived from the .torrent file metadata, not
        settable at runtime. This is a no-op for the native session —
        the display name is handled by the controller/monitor layer.
        """
        pass  # No-op: libtorrent doesn't support renaming torrents

    def rename_file(self, torrent_hash: str, old_path: str, new_path: str) -> None:
        """Rename a file within a torrent.

        Uses libtorrent's rename_file() which takes a file index and new path.
        We resolve the index by matching old_path against the torrent's file
        list.
        """
        with self._lock:
            if self._session is None:
                raise NativeTorrentError("Session not started")
            handle = self._handles.get(torrent_hash)
        if handle is None:
            return
        try:
            ti = handle.status().torrent_file
            if ti is None:
                ti = handle.get_torrent_info()
            if ti is None:
                return
            files = ti.files()
            # Normalize backslashes to forward slashes (Windows path compat).
            # Match on full path or on path-component boundary (prefixed with
            # '/') to prevent substring suffix collisions (e.g. 'rom.zip'
            # matching 'badrom.zip').
            norm_old = old_path.replace("\\", "/")
            for i in range(files.num_files()):
                fp = files.file_path(i)
                if fp == norm_old or fp.endswith("/" + norm_old) or norm_old.endswith("/" + fp):
                    handle.rename_file(i, new_path)
                    return
            log.warning(
                "rename_file: could not find '%s' in torrent %s",
                old_path, torrent_hash[:8],
            )
        except RuntimeError:
            log.warning("rename_file failed for %s", torrent_hash[:8], exc_info=True)
    # ── Seeding policy enforcement ──────────────────────────────────────

    def _enforce_seeding_policy(self, handle: lt.torrent_handle, status: lt.torrent_status) -> None:
        """Auto-pause torrents that have met seeding targets."""
        if not status.is_seeding:
            return
        # Check share ratio
        if status.total_download > 0:
            ratio = status.total_upload / status.total_download
            if ratio >= self._seed_ratio:
                handle.pause()
                log.info(
                    "Seed ratio %.1f reached for %s, pausing",
                    ratio, status.name,
                )
                return
        # Check seed time
        if status.seeding_time >= self._seed_time_seconds:
            handle.pause()
            log.info(
                "Seed time %dh reached for %s, pausing",
                self._seed_time_seconds // 3600, status.name,
            )

    def _check_seeding_policy(self) -> None:
        """Periodically check all handles for seeding policy compliance."""
        with self._lock:
            handles = dict(self._handles)
        for handle in handles.values():
            try:
                if not handle.is_valid():
                    continue
                status = handle.status()
                self._enforce_seeding_policy(handle, status)
            except RuntimeError as exc:
                log.debug("Seeding policy check failed for handle: %s", exc)
                continue
    # ── Alert pump ──────────────────────────────────────────────────────
    def _pump_alerts(self) -> None:
        """Background thread that drains libtorrent alerts and enforces policy.

        Calls ``post_torrent_updates()`` every cycle so libtorrent
        asynchronously produces ``state_update_alert`` containing only
        torrents whose status changed — avoiding the need for the UI
        thread to call ``handle.status()`` on every torrent.
        """
        update_counter = 0
        while self._running:
            with self._lock:
                session = self._session
            if session is None:
                break
            try:
                # Request delta updates from libtorrent every cycle.
                # libtorrent will emit state_update_alert with only
                # changed torrents (research: libtorrent/Transmission pattern).
                if update_counter % 2 == 0:
                    session.post_torrent_updates(
                        lt.status_flags_t.query_pieces | lt.status_flags_t.query_accurate_download_counters
                    )
                update_counter += 1

                alert = session.wait_for_alert(1000)  # 1s timeout
                if alert is None:
                    self._check_seeding_policy()
                    continue
                alerts = session.pop_alerts()
                for a in alerts:
                    self._handle_alert(a)
            except Exception:
                log.warning("Alert pump error", exc_info=True)
                time.sleep(1)

    def _handle_alert(self, alert: lt.alert) -> None:
        """Process a single libtorrent alert."""
        alert_type = type(alert).__name__
        try:
            if alert_type == "state_update_alert":
                # Delta update: cache only changed torrent statuses.
                # The UI thread reads from _status_cache instead of calling
                # handle.status() per torrent (O(N) → O(changed)).
                for status in alert.status:
                    try:
                        h = status.handle
                        if h.is_valid():
                            info_hash = str(h.info_hash())
                            with self._lock:
                                self._status_cache[info_hash] = status
                    except RuntimeError as exc:
                        log.debug("Failed to cache status for handle: %s", exc)
                return

            if alert_type == "torrent_finished_alert":
                handle = alert.handle
                if handle.is_valid():
                    handle.save_resume_data(lt.torrent_handle.save_info_dict)
                    log.info("Torrent finished: %s", handle.status().name)

            elif alert_type == "save_resume_data_alert":
                self._save_resume_data(alert)

            elif alert_type == "torrent_error_alert":
                handle = alert.handle
                if handle.is_valid():
                    log.warning("Torrent error: %s — %s", handle.status().name, str(alert.message()))

            # Enforce seeding policy on state changes
            if hasattr(alert, "handle") and alert.handle.is_valid():
                status = alert.handle.status()
                self._enforce_seeding_policy(alert.handle, status)
        except RuntimeError as exc:
            log.warning("Alert handler failed for %s: %s", alert_type, exc)
    # ── Helpers ─────────────────────────────────────────────────────────

    def _save_resume_data(self, alert: lt.alert) -> None:
        """Write resume data from a save_resume_data_alert to disk."""
        try:
            data = lt.write_resume_data(alert.params)
            buf = lt.bencode(data)
            handle = alert.handle
            if handle.is_valid():
                ih = str(handle.info_hash())
                resume_path = self._save_dir / f"{ih}.fastresume"
                resume_path.write_bytes(buf)
                log.debug("Wrote resume data: %s", resume_path.name)
        except Exception:
            log.warning("Failed to write resume data", exc_info=True)

    @staticmethod
    def _require_libtorrent() -> None:
        if lt is None:
            raise NativeTorrentError(
                "libtorrent Python bindings are not installed. "
                "Install the project dependencies, including libtorrent, "
                "before using the native torrent engine."
            ) from _LIBTORRENT_IMPORT_ERROR

    def _ensure_session(self) -> None:
        self._require_libtorrent()
        if self._session is None:
            self.start()
        if self._session is None:
            raise NativeTorrentError("libtorrent session not started")

    def _get_handle(self, torrent_hash: str) -> lt.torrent_handle | None:
        with self._lock:
            return self._handles.get(torrent_hash)

    def _status_to_dict(self, status: lt.torrent_status, info_hash: str) -> dict:
        """Convert a libtorrent torrent_status into Minerva telemetry."""
        state_map = {
            lt.torrent_status.checking_files: "checking",
            lt.torrent_status.downloading_metadata: "metadata",
            lt.torrent_status.downloading: "downloading",
            lt.torrent_status.finished: "completed",
            lt.torrent_status.seeding: "seeding",
            lt.torrent_status.allocating: "allocating",
            lt.torrent_status.checking_resume_data: "checking_resume",
        }
        raw_state = state_map.get(status.state, "unknown")
        if getattr(status, "errc", None):
            raw_state = "error"
        elif status.paused:
            raw_state = "completed" if status.progress >= 1.0 else "paused"
        if status.all_time_download > 0:
            seed_ratio = float(status.all_time_upload / status.all_time_download)
        else:
            seed_ratio = 0.0
        seed_time_remaining = -1
        if status.is_seeding and self._seed_ratio > 0:
            remaining_ratio = max(self._seed_ratio - seed_ratio, 0)
            up_rate = status.upload_rate
            if up_rate > 0:
                remaining_bytes = remaining_ratio * status.all_time_download
                seed_time_remaining = int(remaining_bytes / up_rate)
                if self._seed_time_seconds > 0:
                    time_left = max(self._seed_time_seconds - status.seeding_time, 0)
                    seed_time_remaining = min(seed_time_remaining, time_left)
        return {
            "hash": info_hash,
            "name": status.name,
            "progress": float(status.progress),
            "state": raw_state,
            "dlspeed": int(status.download_rate),
            "upspeed": int(status.upload_rate),
            "size": int(status.total_wanted),
            "completed": int(status.total_wanted_done),
            "ratio": seed_ratio,
            "eta": int(status.total_wanted - status.total_wanted_done) // max(int(status.download_rate), 1) if status.download_rate > 0 else -1,
            "save_path": status.save_path,
            "category": "",
            "num_seeds": int(status.num_seeds),
            "num_leechs": max(int(status.num_peers) - int(status.num_seeds), 0),
            "seed_ratio": seed_ratio,
            "seed_time_remaining": seed_time_remaining,
        }

    # ── Compute info hash (static, for compatibility) ──────────────────

    @staticmethod
    def compute_info_hash(torrent_path: Path) -> str | None:
        """Compute the v1 info-hash of a .torrent file."""
        try:
            NativeTorrentSession._require_libtorrent()
            ti = lt.torrent_info(str(torrent_path))
            return str(ti.info_hash())
        except Exception:
            log.warning("compute_info_hash: failed for %s", torrent_path, exc_info=True)
            return None
