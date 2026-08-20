"""System-tray presence: status icon, menu, notifications."""
from PySide6.QtCore import QRectF, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import theme


def status_icon(status: str, size: int = 64) -> QIcon:
    """Rounded dark tile with a status-colored dot."""
    pm = QPixmap(size, size)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(theme.SURFACE))
    p.setPen(QColor(255, 255, 255, 40))
    p.drawRoundedRect(QRectF(2, 2, size - 4, size - 4),
                      size * 0.22, size * 0.22)
    p.setBrush(QColor(theme.STATUS.get(status, theme.STATUS["good"])))
    p.setPen(QColor(0, 0, 0, 0))
    r = size * 0.42
    p.drawEllipse(QRectF((size - r) / 2, (size - r) / 2, r, r))
    p.end()
    return QIcon(pm)


class TrayIcon(QSystemTrayIcon):
    open_requested = Signal()
    quick_clean_requested = Signal()
    open_log_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setIcon(status_icon("good"))
        self.setToolTip("Desktop Optimizer")

        self._menu = QMenu()
        act_open = QAction("Open dashboard", self._menu)
        act_clean = QAction("Quick clean (temp + DNS)", self._menu)
        act_log = QAction("Open log folder", self._menu)
        act_quit = QAction("Exit", self._menu)
        act_open.triggered.connect(self.open_requested)
        act_clean.triggered.connect(self.quick_clean_requested)
        act_log.triggered.connect(self.open_log_requested)
        act_quit.triggered.connect(self.quit_requested)
        self._menu.addAction(act_open)
        self._menu.addAction(act_clean)
        self._menu.addAction(act_log)
        self._menu.addSeparator()
        self._menu.addAction(act_quit)
        self.setContextMenu(self._menu)
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_requested.emit()

    def set_status(self, status: str, tooltip: str):
        self.setIcon(status_icon(status))
        self.setToolTip(tooltip)

    def notify(self, title: str, body: str, level: str = "info"):
        icon = {
            "info": QSystemTrayIcon.Information,
            "warning": QSystemTrayIcon.Warning,
            "critical": QSystemTrayIcon.Critical,
        }.get(level, QSystemTrayIcon.Information)
        self.showMessage(title, body, icon, 8000)
