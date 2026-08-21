"""Desktop Optimizer — entry point.

Run with:  python main.py
Log file:  logs\\app.log (next to this file)
"""
import logging
import os
import platform
import sys

from PySide6.QtCore import QLockFile, qVersion
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox

from app import diag
from app.monitor import MetricsSampler
from app.ui import theme
from app.ui.main_window import MainWindow
from app.ui.tray import TrayIcon, status_icon

log = logging.getLogger(__name__)


def main() -> int:
    log_file = diag.setup_logging()

    app = QApplication(sys.argv)
    app.setApplicationName("Desktop Optimizer")
    app.setQuitOnLastWindowClosed(False)   # closing the window hides to tray
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 9))   # pyqtgraph text uses the app font
    app.setStyleSheet(theme.STYLESHEET)

    # Single instance — a second copy would show a confusingly stale window.
    # The lock lives beside the log, NOT in %TEMP%: Store Python virtualizes
    # temp, which would give each interpreter its own view of the lock.
    # Retry briefly: an elevated relaunch starts while the old copy is exiting.
    lock = QLockFile(os.path.join(diag.LOG_DIR, "app.lock"))
    got_lock = False
    for _ in range(15):
        if lock.tryLock(200):
            got_lock = True
            break
    if not got_lock:
        log.warning("Another instance is already running — exiting")
        QMessageBox.information(
            None, "Desktop Optimizer",
            "Desktop Optimizer is already running — look for its icon in "
            "the system tray (green/amber/red dot).")
        return 0

    log.info("Starting Desktop Optimizer — Python %s, Qt %s, %s; log: %s",
             platform.python_version(), qVersion(), platform.platform(),
             log_file)

    window = MainWindow()
    window.setWindowIcon(status_icon("good"))

    if diag.is_store_python():
        log.warning("Running under Microsoft Store Python — AppData/Temp "
                    "writes are virtualized; cleanup actions may not affect "
                    "the real file system")
        window.show_startup_note(
            "⚠ Microsoft Store Python detected — Windows sandboxes its file "
            "operations, so 'Clear temp files' may be ineffective. For full "
            "functionality install Python from python.org and rebuild .venv.")

    tray = TrayIcon(app)
    tray.open_requested.connect(
        lambda: (window.showNormal(), window.raise_(), window.activateWindow()))
    tray.quick_clean_requested.connect(window.run_quick_clean)
    tray.open_log_requested.connect(lambda: os.startfile(diag.LOG_DIR))
    window.set_tray(tray)
    tray.show()

    # Sampler lifecycle: started here, restarted when the watchdog reports
    # a stall (dead/stuck sampler thread).
    state = {"sampler": None}

    def start_sampler():
        sampler = MetricsSampler()
        sampler.sample.connect(window.on_sample)
        sampler.start()
        state["sampler"] = sampler

    def restart_sampler():
        old = state["sampler"]
        if old is not None:
            old.stop()
            if not old.wait(3000):
                log.error("Old sampler thread did not stop within 3 s")
        log.warning("Restarting metrics sampler after stall")
        start_sampler()

    window.sampler_stalled.connect(restart_sampler)

    def shutdown():
        sampler = state["sampler"]
        if sampler is not None:
            sampler.stop()
            sampler.wait(3000)
        log.info("Desktop Optimizer exited")

    def quit_app():
        window.request_quit()
        app.quit()

    tray.quit_requested.connect(quit_app)
    app.aboutToQuit.connect(shutdown)

    start_sampler()
    window.show()
    diag.FreezeWatch(window.last_gui_beat).start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
