"""Desktop Optimizer — entry point.

Run with:  python main.py
"""
import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from app.monitor import MetricsSampler
from app.ui import theme
from app.ui.main_window import MainWindow
from app.ui.tray import TrayIcon, status_icon


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Desktop Optimizer")
    app.setQuitOnLastWindowClosed(False)   # closing the window hides to tray
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 9))   # pyqtgraph text uses the app font
    app.setStyleSheet(theme.STYLESHEET)

    window = MainWindow()
    window.setWindowIcon(status_icon("good"))

    tray = TrayIcon(app)
    tray.open_requested.connect(
        lambda: (window.showNormal(), window.raise_(), window.activateWindow()))
    tray.quick_clean_requested.connect(window.run_quick_clean)
    window.set_tray(tray)
    tray.show()

    sampler = MetricsSampler()
    sampler.sample.connect(window.on_sample)

    def quit_app():
        window.request_quit()
        app.quit()

    tray.quit_requested.connect(quit_app)
    app.aboutToQuit.connect(lambda: (sampler.stop(), sampler.wait(3000)))

    sampler.start()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
