"""System-tray presence: live status icon, menu, notifications.

Every method here that touches the notification area goes through
shellguard: `setIcon`, `setToolTip` and `showMessage` all call
`Shell_NotifyIcon`, which blocks the GUI thread whenever explorer.exe is
busy. They are therefore rate-limited, and in degraded mode the live
numeric icon is replaced by a single static one.
"""
import time

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import (
    QAction, QColor, QFont, QIcon, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import theme
from .shellguard import guard

# The notification area is the single most expensive thing we touch, so
# updates are deliberately sparse: the icon only when the coarse load
# bucket changes, the tooltip only occasionally (nobody sees it unless
# they hover).
ICON_MIN_INTERVAL_S = 5.0
TOOLTIP_MIN_INTERVAL_S = 15.0
LOAD_BUCKET = 10        # redraw only per 10% of load
# Toasts are the worst of the bunch: showMessage has been measured
# blocking the GUI thread for 10+ seconds on a loaded machine. Never fire
# them in bursts, and not at all while the shell is struggling — the alert
# is still in the alerts panel, the log and the strip's status colour.
NOTIFY_MIN_INTERVAL_S = 60.0


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
    guide_requested = Signal()
    about_requested = Signal()
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
        act_guide = QAction("User guide", self._menu)
        act_about = QAction("About", self._menu)
        act_quit = QAction("Exit", self._menu)
        act_open.triggered.connect(self.open_requested)
        act_mini.triggered.connect(self.mini_requested)
        act_clean.triggered.connect(self.quick_clean_requested)
        act_log.triggered.connect(self.open_log_requested)
        act_guide.triggered.connect(self.guide_requested)
        act_about.triggered.connect(self.about_requested)
        act_quit.triggered.connect(self.quit_requested)
        self._menu.addAction(act_open)
        self._menu.addAction(act_mini)
        self._menu.addAction(act_clean)
        self._menu.addAction(act_log)
        self._menu.addSeparator()
        self._menu.addAction(act_guide)
        self._menu.addAction(act_about)
        self._menu.addSeparator()
        self._menu.addAction(act_quit)
        self.setContextMenu(self._menu)
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_requested.emit()

    def set_status(self, status: str, tooltip: str, load: float | None = None):
        """Update the notification-area icon, sparingly.

        Each call into the shell can block for seconds when explorer is
        busy, so the icon is only pushed when its coarse bucket changes and
        at most every few seconds; the tooltip even less often. In degraded
        mode the numeric readout is dropped for a static status icon.
        """
        # While degraded, write nothing to the notification area at all.
        # A single setToolTip has been measured blocking for 13 s on a
        # struggling shell; the status is still in the mini strip, the
        # alerts panel and the log.
        if guard.degraded:
            return

        now = time.monotonic()
        if load is None or not guard.live_icon_allowed:
            key = (status, None)
            icon_for = lambda: status_icon(status)
        else:
            key = (status, int(load) // LOAD_BUCKET)
            icon_for = lambda: load_icon(status, load)

        # Health changes are worth showing immediately; load is not.
        urgent = key[0] != getattr(self, "_icon_key", (None,))[0]
        due = now - getattr(self, "_icon_at", 0.0) >= ICON_MIN_INTERVAL_S
        if key != getattr(self, "_icon_key", None) and (urgent or due):
            self._icon_key = key
            self._icon_at = now
            guard.call("tray setIcon", self.setIcon, icon_for())

        if now - getattr(self, "_tip_at", 0.0) >= TOOLTIP_MIN_INTERVAL_S:
            self._tip_at = now
            guard.call("tray setToolTip", self.setToolTip, tooltip)

    def show_static_icon(self, status: str):
        """One cheap icon push, used when entering degraded mode."""
        self._icon_key = (status, None)
        self._icon_at = time.monotonic()
        guard.call("tray setIcon (static)", self.setIcon, status_icon(status))

    def notify(self, title: str, body: str, level: str = "info") -> bool:
        """Raise a Windows toast. Returns False if it was suppressed.

        Suppressed while the shell is slow, and never more than one per
        NOTIFY_MIN_INTERVAL_S: a burst of alerts would otherwise mean a
        burst of multi-second GUI stalls.
        """
        if guard.degraded:
            return False
        now = time.monotonic()
        if now - getattr(self, "_notify_at", 0.0) < NOTIFY_MIN_INTERVAL_S:
            return False
        self._notify_at = now
        icon = {
            "info": QSystemTrayIcon.Information,
            "warning": QSystemTrayIcon.Warning,
            "critical": QSystemTrayIcon.Critical,
        }.get(level, QSystemTrayIcon.Information)
        guard.call("tray showMessage", self.showMessage,
                   title, body, icon, 8000)
        return True
