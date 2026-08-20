"""Shared thread-pool worker for running blocking calls off the GUI thread."""
from PySide6.QtCore import QObject, QRunnable, Signal

from .. import optimizer


class WorkerSignals(QObject):
    done = Signal(object)


class Worker(QRunnable):
    """Runs a callable on the global thread pool, emits its result.

    QThreadPool does not keep the Python wrapper alive, so in-flight
    workers hold themselves in a class-level set — otherwise the signals
    object can be garbage-collected mid-run ("Signal source has been
    deleted").
    """

    _inflight = set()

    def __init__(self, fn):
        super().__init__()
        self.setAutoDelete(False)      # Python owns the lifetime
        self._fn = fn
        self.signals = WorkerSignals()
        Worker._inflight.add(self)

    def run(self):
        try:
            try:
                result = self._fn()
            except Exception as e:  # surface the error, don't kill the pool
                result = optimizer.ActionResult(
                    getattr(self._fn, "__name__", "action"), False, str(e))
            try:
                self.signals.done.emit(result)   # queued: args copied here
            except RuntimeError:
                pass    # app is shutting down; the result has no audience
        finally:
            Worker._inflight.discard(self)
