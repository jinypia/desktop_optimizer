"""Runtime self-diagnostics: file logging, exception hooks, Qt messages.

The app monitors the system — this module makes sure the app also notices
its own failures. Everything lands in a rotating log file in the app's own
logs/ folder. AppData is deliberately avoided: Microsoft Store Python
virtualizes writes there into its package container, which makes the log
invisible at the expected path.
"""
import logging
import logging.handlers
import os
import sys
import threading

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
    root.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)

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
