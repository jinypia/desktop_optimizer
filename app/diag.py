"""Runtime self-diagnostics: file logging, exception hooks, Qt messages.

The app monitors the system — this module makes sure the app also notices
its own failures. Everything lands in a rotating log file in the app's own
logs/ folder. AppData is deliberately avoided: Microsoft Store Python
virtualizes writes there into its package container, which makes the log
invisible at the expected path.
"""
import atexit
import logging
import logging.handlers
import os
import queue
import sys
import threading
import time
import traceback

from PySide6.QtCore import qInstallMessageHandler

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(APP_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "app.log")


def is_store_python() -> bool:
    """True when running under Microsoft Store Python, whose MSIX
    virtualization silently redirects AppData/Temp file operations."""
    base = os.path.realpath(sys.base_prefix).lower()
    return "windowsapps" in base or "pythonsoftwarefoundation" in base


def setup_logging() -> str:
    """Configure root logging to a rotating file + stderr; hook exceptions."""
    os.makedirs(LOG_DIR, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s [%(threadName)s] %(name)s: %(message)s")

    fh = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)

    # The log file may live on a network share: write from a dedicated
    # thread so an SMB stall can never block the GUI or sampler threads.
    q = queue.SimpleQueue()
    root.addHandler(logging.handlers.QueueHandler(q))
    listener = logging.handlers.QueueListener(q, fh, sh)
    listener.start()
    atexit.register(listener.stop)

    # Uncaught exceptions on the GUI thread
    def _excepthook(exc_type, exc, tb):
        root.critical("Uncaught exception on GUI thread",
                      exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    # Uncaught exceptions in Python threads (workers, sampler)
    def _thread_excepthook(args):
        name = args.thread.name if args.thread else "?"
        root.critical("Uncaught exception in thread %s", name,
                      exc_info=(args.exc_type, args.exc_value,
                                args.exc_traceback))

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook

    # Qt's own warnings (plugin problems, painter errors, ...)
    def _qt_handler(_mode, _ctx, message):
        root.warning("Qt: %s", message)

    qInstallMessageHandler(_qt_handler)
    return LOG_FILE


class FreezeWatch(threading.Thread):
    """Watches the GUI thread's heartbeat from a daemon thread.

    When the GUI stops responding for `threshold_s`, logs the exact stack
    the main thread is stuck in — hard evidence for "sometimes it gets no
    response" reports — and logs the total duration once it recovers.
    """

    def __init__(self, get_beat, threshold_s: float = 2.0):
        super().__init__(name="FreezeWatch", daemon=True)
        self._get_beat = get_beat
        self._threshold = threshold_s
        self._main_ident = threading.main_thread().ident

    def run(self):
        log = logging.getLogger(__name__)
        frozen_since = None
        while True:
            time.sleep(0.5)
            try:
                age = time.monotonic() - self._get_beat()
            except Exception:
                continue
            if age > self._threshold and frozen_since is None:
                frozen_since = time.monotonic() - age
                frame = sys._current_frames().get(self._main_ident)
                stack = ("".join(traceback.format_stack(frame))
                         if frame else "(stack unavailable)")
                log.warning("GUI thread unresponsive for %.1f s — "
                            "currently stuck at:\n%s", age, stack)
            elif age < self._threshold / 2 and frozen_since is not None:
                log.warning("GUI thread recovered after %.1f s",
                            time.monotonic() - frozen_since)
                frozen_since = None
