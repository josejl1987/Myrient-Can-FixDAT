"""
Generic thread-lifecycle manager for AppShell.

Manages the ``_filter_load_thread`` (``threading.Thread``) used during
library index loading.  Also owns the in-process download queue used
by tests and when no DownloadController is wired.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from PyQt6 import QtCore

from minerva.ui.worker import Worker

log = logging.getLogger(__name__)


class WorkerManager(QtCore.QObject):
    """Manages the filter-load thread lifecycle and download queue."""

    downloads_changed = QtCore.pyqtSignal()

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.filter_load_thread: threading.Thread | None = None
        # Worker-based filter load: wraps a callable in a Worker started
        # on a QThread.  Cleared in _on_filter_load_worker_finished.
        self.filter_load_worker: Worker | None = None
        self._filter_load_qthread: QtCore.QThread | None = None
        # ponytail: download_worker holds a QThread-like worker (e.g. a
        # DownloadWorker) whose lifecycle outlives individual page swaps.
        # None when no worker has been attached.
        self.download_worker: QtCore.QThread | None = None
        self._downloads: list = []
        self._active: set[int] = set()
        self._next_id = 1

    def is_active(self) -> bool:
        if (
            self.filter_load_thread is not None
            and self.filter_load_thread.is_alive()
        ):
            return True
        if self.filter_load_worker is not None:
            return True
        if self.download_worker is not None and self.download_worker.isRunning():
            return True
        return False

    def prepare_shutdown(self, timeout_ms: int = 2500) -> bool:
        if (
            self.filter_load_thread is not None
            and self.filter_load_thread.is_alive()
        ):
            self.filter_load_thread.join(timeout=timeout_ms / 1000)
        if self.filter_load_worker is not None:
            self.filter_load_worker.cancel()
            if self._filter_load_qthread is not None:
                self._filter_load_qthread.wait(timeout_ms)
            self.filter_load_worker = None
            self._filter_load_qthread = None
        if self.download_worker is not None and self.download_worker.isRunning():
            self.download_worker.stop()
        return not self.is_active()

    # ── Worker-based filter load ────────────────────────────────────────

    def start_filter_load(
        self, fn: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Worker:
        """Wrap *fn* in a :class:`Worker`, start it on a QThread, and track it.

        The worker's ``cancel_event`` is injected automatically if *fn*
        declares a ``cancel_event`` parameter.  The slot is cleared when
        the worker finishes via :meth:`_on_filter_load_worker_finished`.
        """
        worker = Worker(fn, *args, **kwargs)
        worker.finished.connect(self._on_filter_load_worker_finished)
        self.filter_load_worker = worker
        self._filter_load_qthread = worker.start()
        return worker

    @QtCore.pyqtSlot()
    def _on_filter_load_worker_finished(self) -> None:
        """Clear the filter_load_worker slot when the worker finishes."""
        self.filter_load_worker = None
        self._filter_load_qthread = None

    # ── Download queue ──────────────────────────────────────────────────

    def get_downloads(self) -> list:
        """Return the current download queue (public read-only access)."""
        return list(self._downloads)

    def add_to_queue(self, filename: str, url: str) -> int:
        from minerva.ui.models.download_model import DownloadRecord
        from minerva.domain.downloads import DownloadStatus

        record = DownloadRecord(
            id=self._next_id,
            queue_id=str(self._next_id),
            filename=filename,
            url=url,
            status=DownloadStatus.QUEUED,
        )
        self._next_id += 1
        self._downloads.append(record)
        self._active.add(record.id)
        self._emit_downloads_changed()
        return record.id

    def cancel(self, index: int) -> None:
        from minerva.domain.downloads import DownloadStatus

        if 0 <= index < len(self._downloads):
            self._downloads[index].status = DownloadStatus.CANCELLED
            self._active.discard(self._downloads[index].id)
            self._emit_downloads_changed()

    def pause(self, download_id: int) -> None:
        from minerva.domain.downloads import DownloadStatus

        for d in self._downloads:
            if d.id == download_id:
                d.status = DownloadStatus.PAUSED
                break
        self._emit_downloads_changed()

    def resume(self, download_id: int) -> None:
        from minerva.domain.downloads import DownloadStatus

        for d in self._downloads:
            if d.id == download_id:
                d.status = DownloadStatus.DOWNLOADING
                break
        self._emit_downloads_changed()

    def remove(self, download_id: int) -> None:
        from minerva.domain.downloads import DownloadStatus

        for d in self._downloads:
            if d.id == download_id:
                d.status = DownloadStatus.CANCELLED
                break
        self._active.discard(download_id)
        self._emit_downloads_changed()

    def _emit_downloads_changed(self) -> None:
        self.downloads_changed.emit()
