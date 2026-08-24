"""Mini mode: a compact always-on-top status strip.

A small frameless window showing the essentials — CPU, memory, disk,
network and responsiveness — in one line. By default it docks into the
taskbar band, just left of the tray icons and clock (see taskbar_slot).
Drag it anywhere to undock; double-click or the restore button brings the
full dashboard back. Position and docked state are remembered.

Deliberately cheap: a handful of QLabel updates per sample, no charts.
"""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QWidget,
)

from ..monitor import Snapshot
from ..util import human_rate
from . import taskbar_slot, theme
from .shellguard import guard

log = logging.getLogger(__name__)

GB = 1 << 30
# Re-checking the slot means cross-process calls into explorer, so keep it
# infrequent: the taskbar only changes when icons appear or it is moved.
DOCK_KEEP_MS = 10000
RAISE_MIN_INTERVAL_S = 60.0   # re-assert z-order rarely, never every tick


class MiniWindow(QWidget):
    restore_requested = Signal()
    hide_requested = Signal()
    quit_requested = Signal()
    docked_changed = Signal(bool)

    def __init__(self, parent=None):
        # Qt.Tool keeps it off the alt-tab list; it is a companion window,
        # not a second application window.
        super().__init__(parent, Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Desktop Optimizer — mini")
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self._drag_offset = None
        self._docked = True
        self._last_raise = 0.0
        self._dock_timer = QTimer(self)
        self._dock_timer.setInterval(DOCK_KEEP_MS)
        self._dock_timer.setTimerType(Qt.VeryCoarseTimer)
        self._dock_timer.timeout.connect(self._keep_docked)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setObjectName("miniCard")
        outer.addWidget(card)
        row = QHBoxLayout(card)
        row.setContentsMargins(10, 6, 6, 6)
        row.setSpacing(10)

        self._status_dot = QLabel(theme.STATUS_ICON["good"])
        self._status_dot.setStyleSheet(
            f"color: {theme.STATUS['good']}; font-size: 11px;")
        row.addWidget(self._status_dot)

        # Widest value each slot must fit, so the strip never clips text and
        # never jitters in width as numbers change.
        widest = {"cpu": "100%", "mem": "100%", "disk": "100%",
                  "net": "999.9 MB/s", "resp": "9999 ms"}
        self._metrics = {}
        for key, label, color in (
                ("cpu", "CPU", theme.SERIES_CPU),
                ("mem", "MEM", theme.SERIES_MEM),
                ("disk", "DSK", theme.SERIES_DISK),
                ("net", "NET", theme.SERIES_NET_DOWN),
                ("resp", "RESP", theme.MUTED)):
            name = QLabel(label)
            name.setObjectName("miniKey")
            name.setStyleSheet(f"color: {color};")
            name.setMinimumWidth(name.fontMetrics().horizontalAdvance(label) + 2)
            value = QLabel("–")
            value.setObjectName("miniValue")
            value.setMinimumWidth(
                value.fontMetrics().horizontalAdvance(widest[key]) + 4)
            row.addWidget(name)
            row.addWidget(value)
            self._metrics[key] = value

        row.addSpacing(2)
        restore = QPushButton("▣")
        restore.setObjectName("miniButton")
        restore.setToolTip("Back to the full dashboard")
        restore.setFixedSize(20, 20)
        restore.setCursor(Qt.PointingHandCursor)
        restore.clicked.connect(self.restore_requested)
        row.addWidget(restore)

        self.setFixedSize(self.sizeHint().width(), 34)

    # -- live values ----------------------------------------------------------
    def update_snapshot(self, snap: Snapshot, ui_lag_ms: float, status: str):
        self._status_dot.setText(theme.STATUS_ICON[status])
        self._status_dot.setStyleSheet(
            f"color: {theme.STATUS[status]}; font-size: 11px;")
        self._metrics["cpu"].setText(f"{snap.cpu:.0f}%")
        self._metrics["mem"].setText(f"{snap.mem_percent:.0f}%")
        self._metrics["disk"].setText(f"{snap.disk_busy:.0f}%")
        self._metrics["net"].setText(human_rate(snap.net_recv_bps))
        self._metrics["resp"].setText(f"{ui_lag_ms:.0f} ms")

    # -- placement ------------------------------------------------------------
    def is_docked(self) -> bool:
        return self._docked

    def set_docked(self, docked: bool, announce: bool = True):
        """Docked = pinned into the taskbar band beside the tray cluster."""
        if docked:
            if not self.dock_to_taskbar():
                return
        else:
            self._docked = False
            self._dock_timer.stop()
        if announce:
            self.docked_changed.emit(self._docked)

    def dock_to_taskbar(self) -> bool:
        """Move into the taskbar slot. False if the OS offers no room."""
        pos = taskbar_slot.dock_position(self.size(), self.screen())
        if pos is None:
            log.info("No taskbar slot available — staying floating")
            self._docked = False
            self._dock_timer.stop()
            return False
        self.move(pos)
        if not self._docked:
            self._docked = True
        if self.isVisible():
            self._dock_timer.start()
        return True

    def _keep_docked(self):
        """Follow the taskbar: its size changes as tray icons come and go,
        and the whole bar can move between screens or edges.

        Skipped entirely while the shell is slow — a frozen position is far
        better than a frozen window.
        """
        if not self._docked or not self.isVisible() or guard.degraded:
            return
        pos = taskbar_slot.dock_position(self.size(), self.screen())
        if pos is None:
            return                      # taskbar hidden right now; sit tight
        if pos != self.pos():
            self.move(pos)
        self._raise_if_due()

    def _raise_if_due(self):
        """Re-assert z-order occasionally. Doing this on every tick fought
        with the shell for stacking order and cost a shell round-trip."""
        now = time.monotonic()
        if now - self._last_raise >= RAISE_MIN_INTERVAL_S:
            self._last_raise = now
            self.raise_()

    def park_bottom_right(self):
        """Fallback position: floating just above the taskbar, right side."""
        screen = self.screen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.right() - self.width() - 12,
                  area.bottom() - self.height() - 12)

    # -- context menu ---------------------------------------------------------
    def _show_menu(self, pos):
        menu = QMenu(self)
        act_open = QAction("Open dashboard", menu)
        act_open.triggered.connect(self.restore_requested)
        menu.addAction(act_open)

        act_dock = QAction("Dock to taskbar", menu)
        act_dock.setCheckable(True)
        act_dock.setChecked(self._docked)
        act_dock.triggered.connect(
            lambda checked: self.set_docked(checked))
        menu.addAction(act_dock)

        menu.addSeparator()
        act_hide = QAction("Hide to tray", menu)
        act_hide.triggered.connect(self.hide_requested)
        menu.addAction(act_hide)
        act_quit = QAction("Exit", menu)
        act_quit.triggered.connect(self.quit_requested)
        menu.addAction(act_quit)
        menu.exec(self.mapToGlobal(pos))

    def clamp_to_screen(self):
        """Keep the window reachable if the display layout changed."""
        screen = self.screen()
        if screen is None:
            return
        area = screen.availableGeometry()
        pos = self.pos()
        x = min(max(pos.x(), area.left()), max(area.right() - self.width(),
                                               area.left()))
        y = min(max(pos.y(), area.top()), max(area.bottom() - self.height(),
                                              area.top()))
        if (x, y) != (pos.x(), pos.y()):
            self.move(x, y)

    # -- drag to move, double-click to restore --------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = (event.globalPosition().toPoint()
                                 - self.frameGeometry().topLeft())
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            if self._docked:            # dragging it away undocks it
                self._docked = False
                self._dock_timer.stop()
                self.docked_changed.emit(False)
            event.accept()

    def showEvent(self, event):
        super().showEvent(event)
        if self._docked:
            self.dock_to_taskbar()
            self._dock_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._dock_timer.stop()

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        event.accept()

    def mouseDoubleClickEvent(self, event):
        self.restore_requested.emit()
        event.accept()

    def position(self) -> QPoint:
        return self.pos()
