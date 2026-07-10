"""
Background ``QRunnable`` with signal-based result marshalling.

Extracted from ``minerva_gui`` during the legacy cleanup (Phase 11).
"""

from __future__ import annotations

from PyQt6 import QtCore


class TaskSignals(QtCore.QObject):
    """Signals used by background QRunnable tasks."""

    result = QtCore.pyqtSignal(object)
    error = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()
    progress = QtCore.pyqtSignal(int, int)


class TaskRunner(QtCore.QRunnable):
    """Run a callable in QThreadPool and marshal its result to the GUI thread."""

    def __init__(self, function, *args, **kwargs):
        super().__init__()
        self._function = function
        self._args = args
        self._kwargs = kwargs
        self.signals = TaskSignals()

    @QtCore.pyqtSlot()
    def run(self):
        try:
            result = self._function(*self._args, **self._kwargs)
        except Exception as exc:  # noqa: BLE001
            import traceback

            self.signals.error.emit(
                f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}",
            )
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()

    @classmethod
    def wrap_result(cls, function, *args, **kwargs) -> TaskRunner:
        """Wrap *function* so its return value becomes an ``OperationResult``.

        * If the callable returns an ``OperationResult``, it flows through
          unchanged.
        * If it returns any other value, it's wrapped in
          ``OperationResult.success(payload=...)``.
        * If it raises, the exception becomes an
          ``OperationResult.failed(...)`` emitted via ``result`` (not
          ``error``), so callers only need to connect to ``result``.
        """
        from minerva.ui.result import OperationResult

        def _wrapped(*a, **kw):
            try:
                value = function(*a, **kw)
            except Exception as exc:  # noqa: BLE001
                return OperationResult.from_exception(exc, stage=getattr(function, "__name__", "task"))
            if isinstance(value, OperationResult):
                return value
            return OperationResult.success(
                summary=f"{getattr(function, '__name__', 'task')} completed",
                payload=value,
            )

        return cls(_wrapped, *args, **kwargs)
