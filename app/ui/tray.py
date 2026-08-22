"""System-tray presence: live status icon, menu, notifications."""
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import (
    QAction, QColor, QFont, QIcon, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import theme


_ICON_CACHE = {}


def status_icon(status: str, size: int = 64) -> QIcon:
    """Rounded dark tile with a status-colored dot (cached — this is
    called every sample and must not churn GDI objects)."""
    cached = _ICON_CACHE.get((status, size))
    if cached is not None:
        return cached
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
    icon = QIcon(pm)
    _ICON_CACHE[(status, size)] = icon
    return icon


_LOAD_ICON_CACHE = {}
LOAD_STEP = 2       # quantise to 2% so the cache stays small (~50 entries)


def load_icon(status: str, percent: float, size: int = 64) -> QIcon:
    """Tile showing the current CPU load as a number plus a fill bar.

    This is the taskbar-resident readout: the notification area shows live
    load without any window open. Cached per (status, quantised percent) —
    repainting on every sample was previously a real cost.
    """
    pct = max(0, min(100, int(percent)))
    key = (status, pct // LOAD_STEP, size)
    cached = _LOAD_ICON_CACHE.get(key)
    if cached is not None:
        return cached

    accent = QColor(theme.STATUS.get(status, theme.STATUS["good"]))
    pm = QPixmap(size, size)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    radius = size * 0.22
    p.setBrush(QColor(theme.SURFACE))
    p.setPen(QColor(255, 255, 255, 40))
    p.drawRoundedRect(QRectF(2, 2, size - 4, size - 4), radius, radius)

    # load bar along the bottom edge
    bar_h = max(3.0, size * 0.13)
    bar_w = (size - 10) * (pct / 100.0)
    p.setPen(QPen(Qt.NoPen))
    p.setBrush(QColor(theme.GRID))
    p.drawRoundedRect(QRectF(5, size - 4 - bar_h, size - 10, bar_h), 2, 2)
    if bar_w > 0:
        p.setBrush(accent)
        p.drawRoundedRect(QRectF(5, size - 4 - bar_h, bar_w, bar_h), 2, 2)

    # load number (100% shown as "99" so it still fits legibly)
    text = str(min(pct, 99))
    font = QFont("Segoe UI")
    font.setBold(True)
    font.setPixelSize(int(size * 0.52))
    p.setFont(font)
    p.setPen(QColor(theme.TEXT))
    p.drawText(QRectF(0, 1, size, size - bar_h - 4),
               Qt.AlignCenter, text)
    p.end()

    icon = QIcon(pm)
    _LOAD_ICON_CACHE[key] = icon
    return icon


class TrayIcon(QSystemTrayIcon):
    open_requested = Signal()
    mini_requested = Signal()
    quick_clean_requested = Signal()
    open_log_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setIcon(status_icon("good"))
        self.setToolTip("Desktop Optimizer")

        self._menu = QMenu()
        act_open = QAction("Open dashboard", self._menu)
        act_mini = QAction("Mini mode", self._menu)
        act_clean = QAction("Quick clean (temp + DNS)", self._menu)
        act_log = QAction("Open log folder", self._menu)
        act_quit = QAction("Exit", self._menu)
        act_open.triggered.connect(self.open_requested)
        act_mini.triggered.connect(self.mini_requested)
        act_clean.triggered.connect(self.quick_clean_requested)
        act_log.triggered.connect(self.open_log_requested)
        act_quit.triggered.connect(self.quit_requested)
        self._menu.addAction(act_open)
        self._menu.addAction(act_mini)
        self._menu.addAction(act_clean)
        self._menu.addAction(act_log)
        self._menu.addSeparator()
        self._menu.addAction(act_quit)
        self.setContextMenu(self._menu)
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_requested.emit()

    def set_status(self, status: str, tooltip: str, load: float | None = None):
        """Update the notification-area icon.

        With `load`, the icon shows live CPU % (quantised, so the repaint
        happens only when the displayed number actually changes).
        """
        if load is None:
            key = (status, None)
            icon = status_icon(status)
        else:
            key = (status, int(load) // LOAD_STEP)
            icon = load_icon(status, load)
        if key != getattr(self, "_icon_key", None):
            self._icon_key = key
            self.setIcon(icon)
        self.setToolTip(tooltip)

    def notify(self, title: str, body: str, level: str = "info"):
        icon = {
            "info": QSystemTrayIcon.Information,
            "warning": QSystemTrayIcon.Warning,
            "critical": QSystemTrayIcon.Critical,
        }.get(level, QSystemTrayIcon.Information)
        self.showMessage(title, body, icon, 8000)
