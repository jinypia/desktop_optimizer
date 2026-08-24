"""Runtime self-diagnostics: file logging, exception hooks, Qt messages.

The app monitors the system — this module makes sure the app also notices
its own failures. Everything lands in a rotating log file.

Log location depends on how the app runs:
  - installed (frozen): %LOCALAPPDATA%\\DesktopOptimizer\\logs — the
    install directory must not be assumed writable.
  - from source: the project's own logs/ folder. AppData is deliberately
    avoided there because Microsoft Store Python virtualizes writes into
    its package container, which hides the log from the expected path.
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

IS_FROZEN = bool(getattr(sys, "frozen", False))

if IS_FROZEN:
    APP_ROOT = os.path.dirname(os.path.abspath(sys.executable))
    DATA_DIR = os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
        "DesktopOptimizer")
else:
    APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DATA_DIR = APP_ROOT

LOG_DIR = os.path.join(DATA_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "app.log")


def is_store_python() -> bool:
    """True when running under Microsoft Store Python, whose MSIX
    virtualization silently redirects AppData/Temp file operations."""
    if IS_FROZEN:
        return False        # bundled interpreter: never the Store build
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

    # A windowed build (pythonw / PyInstaller --windowed) has no stderr;
    # attaching a StreamHandler to None would fail on every record.
    handlers = [fh]
    if sys.stderr is not None:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        handlers.append(sh)

    # The log file may live on a network share: write from a dedicated
    # thread so an SMB stall can never block the GUI or sampler threads.
    q = queue.SimpleQueue()
    root.addHandler(logging.handlers.QueueHandler(q))
    listener = logging.handlers.QueueListener(q, *handlers)
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

    It does not just observe: after `trip_after` freezes inside
    `trip_window_s` it calls `on_repeated_freeze(diagnosis)` so the app can
    shed whatever is blocking it, without waiting for the user to notice.
    """

    def __init__(self, get_beat, threshold_s: float = 2.0,
                 on_repeated_freeze=None, trip_after: int = 3,
                 trip_window_s: float = 120.0):
        super().__init__(name="FreezeWatch", daemon=True)
        self._get_beat = get_beat
        self._threshold = threshold_s
        self._main_ident = threading.main_thread().ident
        self._on_repeated = on_repeated_freeze
        self._trip_after = trip_after
        self._trip_window = trip_window_s
        self._events = []          # monotonic times of recent freezes
        self._tripped = False

    POLL_S = 0.5
    # Our own sleep overshooting this much means this thread was not
    # scheduled either — the whole process was suspended (sleep, hibernate,
    # modern standby). That is not a GUI freeze: during a real freeze this
    # thread keeps running normally and only the main thread is stuck, so
    # the two cases are cleanly distinguishable from right here.
    SUSPEND_FACTOR = 10.0

    def _is_suspend_gap(self, slept: float) -> bool:
        return slept > self.POLL_S * self.SUSPEND_FACTOR

    def run(self):
        log = logging.getLogger(__name__)
        frozen_since = None
        last_poll = time.monotonic()
        while True:
            time.sleep(self.POLL_S)
            now = time.monotonic()
            slept, last_poll = now - last_poll, now
            if self._is_suspend_gap(slept):
                log.info("Process was not scheduled for %.0f s (suspend or "
                         "resume) — not counting it as a freeze", slept)
                frozen_since = None
                continue
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
                self._note_freeze(stack, log)
            elif age < self._threshold / 2 and frozen_since is not None:
                log.warning("GUI thread recovered after %.1f s",
                            time.monotonic() - frozen_since)
                frozen_since = None

    def _note_freeze(self, stack: str, log):
        now = time.monotonic()
        self._events = [t for t in self._events
                        if now - t < self._trip_window]
        self._events.append(now)
        if self._tripped or len(self._events) < self._trip_after:
            return
        self._tripped = True
        self._events.clear()
        # Name the culprit from the stack when we recognise it.
        if "tray.py" in stack or "Shell_NotifyIcon" in stack:
            what = "notification-area updates"
        elif "taskbar_slot" in stack:
            what = "taskbar position checks"
        else:
            what = "calls into the Windows shell"
        minutes = self._trip_window / 60.0
        window = ("the last minute" if minutes <= 1
                  else f"the last {minutes:.0f} minutes")
        diagnosis = (f"the interface froze {self._trip_after} times in "
                     f"{window}, blocked in {what}")
        log.error("Repeated freezes detected — self-healing: %s", diagnosis)
        if self._on_repeated:
            try:
                self._on_repeated(diagnosis)
            except Exception:
                log.exception("Self-healing callback failed")

    def rearm(self):
        """Allow tripping again (after the app has recovered)."""
        self._tripped = False
        self._events.clear()
