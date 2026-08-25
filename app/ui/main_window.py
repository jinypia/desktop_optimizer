"""Main window: tabbed dashboard (Overview / Details / Processes / Optimize)
with an always-visible health & alerts side panel."""
from __future__ import annotations

import logging
import time
from collections import deque

import pyqtgraph as pg
from PySide6.QtCore import (
    QSettings, Qt, QThreadPool, QTimer, QUrl, Signal, Slot,
)
from PySide6.QtGui import (
    QCloseEvent, QColor, QDesktopServices, QKeySequence, QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from .. import config, optimizer, updates
from ..analyzer import SEVERITY_RANK, Alert, Analyzer
from ..monitor import MetricsSampler, Snapshot
from ..util import human_bytes, human_rate
from ..version import APP_NAME
from . import shellguard, theme
from .details_tab import DetailsTab
from .guide_tab import GuideTab, show_about, show_update_result
from .mini_window import MiniWindow
from .optimize_tab import OptimizeTab
from .process_tab import ProcessTab
from .widgets import LiveChart, MetricCard
from .workers import Worker

log = logging.getLogger(__name__)

STATUS_TEXT = {
    "good": "System healthy",
    "warning": "Performance degraded",
    "critical": "Severe degradation",
}


class MainWindow(QMainWindow):
    RESP_TIMER_MS = 250         # responsiveness probe, dashboard visible
    RESP_TIMER_BG_MS = 1000     # ... and while hidden (fewer wakeups)
    WATCHDOG_MS = 5000          # self-health check period

    sampler_stalled = Signal()      # main() restarts the sampler on this
    view_mode_changed = Signal(str)  # "dashboard" | "mini" | "hidden"
    quit_requested = Signal()       # from the mini strip's context menu
    open_log_requested = Signal()   # Guide tab / tray -> main() opens it
    # Emitted from whichever thread trips the shell guard (often the freeze
    # watchdog). Connected to a slot on this object, so Qt queues it onto
    # the GUI thread — touching widgets from the watchdog thread is a
    # threading violation and crashed the app once already.
    shell_state_changed = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Desktop Optimizer"
                            + ("  [Administrator]" if optimizer.is_admin() else ""))
        self.resize(1060, 700)
        self.setMinimumSize(940, 620)

        self._analyzer = Analyzer()
        self._pool = QThreadPool.globalInstance()
        self._tray = None
        self._ui_lag_ms = 0.0
        self._sample_count = 0
        self._hide_hint_shown = False
        self._quitting = False
        self._alerts_placeholder = None
        self._last_sample_mono = time.monotonic()
        self._stalled = False
        self._ui_error_notes = 0
        self._last_trim = 0.0
        self._freeze_watch = None
        self._update_in_flight = False
        self.shell_state_changed.connect(self._on_shell_state_changed)
        # The listener only emits — never touch widgets on the caller's
        # thread. Signal emission itself is thread-safe.
        shellguard.guard.set_listener(
            lambda degraded, reason:
                self.shell_state_changed.emit(degraded, reason))
        # ~60 s of own-overhead history; warns if the app itself gets heavy
        self._self_hist = deque(maxlen=40)
        self._self_warned = False
        self._settings = QSettings("jinypia", "DesktopOptimizer")
        self._mini = None
        self._charts_stale = False
        self._view_mode = "dashboard"

        pg.setConfigOptions(antialias=True, background=theme.SURFACE,
                            foreground=theme.MUTED)
        self._build_ui()
        QShortcut(QKeySequence("Ctrl+M"), self, self.toggle_mini_mode)
        self._start_lag_probe()
        self._start_watchdog()
        self._refresh_reclaimable()

    def set_tray(self, tray):
        self._tray = tray

    def show_startup_note(self, text: str):
        self._log_line(text)

    def request_quit(self):
        self._quitting = True
        # Come back next launch in whichever surface was last in use.
        self._settings.setValue(
            "start/mode", "dashboard" if self._view_mode == "dashboard"
            else "mini")
        if self._mini is not None:
            if self._mini.isVisible() and not self._mini.is_docked():
                self._settings.setValue("mini/pos", self._mini.position())
            self._mini.close()

    def start_mode(self) -> str:
        """Surface to open on launch — mini by default."""
        mode = self._settings.value("start/mode", "mini")
        return mode if mode in ("dashboard", "mini") else "mini"

    def last_gui_beat(self) -> float:
        """Monotonic time of the GUI thread's last heartbeat (FreezeWatch)."""
        return self._last_tick

    # -- layout ----------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(8)

        # left: tabs
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_dashboard_tab(), "Dashboard")
        self._details = DetailsTab()
        self._tabs.addTab(self._details, "Details")
        self._processes = ProcessTab()
        self._processes.action_result.connect(self._on_action_result_only)
        self._tabs.addTab(self._processes, "Processes")
        self._optimize = OptimizeTab(self._run_action, self._quit_for_elevation)
        self._tabs.addTab(self._optimize, "Optimize")
        self._guide = GuideTab()
        self._guide.open_log_requested.connect(self.open_log_requested)
        self._guide.check_updates_requested.connect(self.check_for_updates)
        self._tabs.addTab(self._guide, "Guide")
        self._tabs.currentChanged.connect(self._on_tab_changed)

        mini_btn = QPushButton("▭  Mini mode")
        mini_btn.setObjectName("miniToggle")
        mini_btn.setToolTip(
            "Shrink to a compact strip above the taskbar (Ctrl+M)")
        mini_btn.setCursor(Qt.PointingHandCursor)
        mini_btn.clicked.connect(self.enter_mini_mode)
        self._tabs.setCornerWidget(mini_btn, Qt.TopRightCorner)

        root.addWidget(self._tabs, 1)

        # right: always-visible health column
        side = QWidget()
        side.setFixedWidth(310)
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, 0, 0)
        side_lay.setSpacing(8)
        side_lay.addWidget(self._build_alerts_panel(), 1)
        side_lay.addWidget(self._build_process_panel(), 0)
        root.addWidget(side, 0)

    def _build_dashboard_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(2, 6, 2, 2)
        lay.setSpacing(8)

        cards = QHBoxLayout()
        cards.setSpacing(8)
        self._card_cpu = MetricCard("CPU", theme.SERIES_CPU)
        self._card_mem = MetricCard("Memory", theme.SERIES_MEM)
        self._card_disk = MetricCard("Disk", theme.SERIES_DISK)
        self._card_resp = MetricCard("Responsiveness", theme.MUTED)
        for card in (self._card_cpu, self._card_mem,
                     self._card_disk, self._card_resp):
            cards.addWidget(card, 1)
        lay.addLayout(cards)

        # compact info strip: system vitals + the app's own overhead
        strip = QFrame()
        strip.setObjectName("panel")
        strip_lay = QHBoxLayout(strip)
        strip_lay.setContentsMargins(12, 5, 12, 5)
        strip_lay.setSpacing(0)
        self._strip = {}
        for i, key in enumerate(("net", "procs", "ctx", "syscalls",
                                 "pagefile", "uptime", "self")):
            if i:
                sep = QLabel("·")
                sep.setObjectName("stripSep")
                strip_lay.addWidget(sep)
            lbl = QLabel("–")
            lbl.setObjectName("stripLabel")
            self._strip[key] = lbl
            strip_lay.addWidget(lbl)
            strip_lay.addStretch(1)
        lay.addWidget(strip)

        chart_frame = QFrame()
        chart_frame.setObjectName("panel")
        cf_lay = QVBoxLayout(chart_frame)
        cf_lay.setContentsMargins(6, 6, 6, 6)
        glw = pg.GraphicsLayoutWidget()
        glw.setBackground(theme.SURFACE)
        cf_lay.addWidget(glw)

        n, dt = config.HISTORY_SAMPLES, config.SAMPLE_INTERVAL_S
        pct = lambda v: f"{v:.0f}%"
        self._chart_cpu = LiveChart(glw, 0, 0, "CPU %",
                                    [("CPU", theme.SERIES_CPU)],
                                    n, dt, y_range=(0, 100), fmt=pct)
        self._chart_mem = LiveChart(glw, 0, 1, "Memory %",
                                    [("Memory", theme.SERIES_MEM)],
                                    n, dt, y_range=(0, 100), fmt=pct)
        self._chart_disk = LiveChart(glw, 1, 0, "Disk busy %",
                                     [("Disk", theme.SERIES_DISK)],
                                     n, dt, y_range=(0, 100), fmt=pct,
                                     x_label=True)
        self._chart_net = LiveChart(glw, 1, 1, "Network KB/s",
                                    [("Down", theme.SERIES_NET_DOWN),
                                     ("Up", theme.SERIES_NET_UP)],
                                    n, dt,
                                    fmt=lambda v: f"{v:,.0f} KB/s",
                                    x_label=True)
        lay.addWidget(chart_frame, 1)
        return page

    def _build_alerts_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        self._status_label = QLabel()
        self._status_label.setObjectName("statusLabel")
        lay.addWidget(self._status_label)

        self._alerts = QListWidget()
        self._alerts.setWordWrap(True)
        self._alerts.setSelectionMode(QAbstractItemView.NoSelection)
        self._alerts.setFocusPolicy(Qt.NoFocus)
        lay.addWidget(self._alerts, 1)

        self._alerts_placeholder = QListWidgetItem(
            "No alerts yet — degradation events and recommendations "
            "will appear here.")
        self._alerts_placeholder.setForeground(QColor(theme.MUTED))
        self._alerts.addItem(self._alerts_placeholder)
        self._set_status("good")
        return panel

    def _build_process_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        title = QLabel("Top processes by CPU")
        title.setObjectName("panelTitle")
        lay.addWidget(title)

        rows = config.TOP_PROCESS_COUNT
        table = QTableWidget(rows, 3)
        table.setHorizontalHeaderLabels(["Process", "CPU %", "Memory"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setFocusPolicy(Qt.NoFocus)
        table.setShowGrid(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        for r in range(rows):
            table.setRowHeight(r, 20)
        table.setFixedHeight(rows * 20 + 28)
        self._proc_table = table
        lay.addWidget(table)
        return panel

    # -- responsiveness probe -----------------------------------------------
    def _start_lag_probe(self):
        self._lag_timer = QTimer(self)
        self._lag_timer.setTimerType(Qt.PreciseTimer)
        self._lag_timer.setInterval(self.RESP_TIMER_MS)
        self._lag_timer.timeout.connect(self._on_lag_tick)
        self._last_tick = time.monotonic()
        self._lag_timer.start()

    def _on_lag_tick(self):
        now = time.monotonic()
        expected = self._lag_timer.interval()
        lag = max(0.0, (now - self._last_tick) * 1000.0 - expected)
        self._last_tick = now
        # EMA smooths one-off spikes (window drags, etc.)
        self._ui_lag_ms = 0.7 * self._ui_lag_ms + 0.3 * lag

    # -- view mode & throttling ------------------------------------------------
    def _dashboard_on_screen(self) -> bool:
        return self.isVisible() and not self.isMinimized()

    def _sync_view_mode(self):
        """Derive the mode from what is actually on screen, and throttle.

        dashboard -> full cadence. mini -> slower, but still lively enough
        for a live strip. hidden -> slowest. Anything but the dashboard also
        drops to below-normal priority and hands the working set back.
        """
        if self._dashboard_on_screen():
            mode = "dashboard"
        elif self._mini is not None and self._mini.isVisible():
            mode = "mini"
        else:
            mode = "hidden"
        if mode == self._view_mode or not hasattr(self, "_lag_timer"):
            return
        previous, self._view_mode = self._view_mode, mode
        self.view_mode_changed.emit(mode)

        showing = mode == "dashboard"
        # fewer timer wakeups when the dashboard is away (easier on battery)
        self._lag_timer.setInterval(self.RESP_TIMER_MS if showing
                                    else self.RESP_TIMER_BG_MS)
        self._lag_timer.setTimerType(Qt.PreciseTimer if showing
                                     else Qt.CoarseTimer)
        self._last_tick = time.monotonic()
        optimizer.set_own_priority(background=not showing)

        if showing:
            log.info("View: dashboard — full cadence restored")
        elif previous == "dashboard" and self._trim_is_due():
            freed = optimizer.trim_self_working_set()
            log.info("View: %s — throttled cadence, below-normal priority, "
                     "released %s", mode, human_bytes(freed))
        else:
            log.info("View: %s", mode)

    TRIM_MIN_INTERVAL_S = 120.0

    def _trim_is_due(self) -> bool:
        """Trimming our own working set is a heavy syscall; toggling views
        repeatedly must not thrash it."""
        now = time.monotonic()
        if now - self._last_trim < self.TRIM_MIN_INTERVAL_S:
            return False
        self._last_trim = now
        return True

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_view_mode()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._sync_view_mode()

    def changeEvent(self, event):
        super().changeEvent(event)
        # a minimised window is still "visible" to Qt but unreadable to a human
        if event.type() == event.Type.WindowStateChange:
            self._sync_view_mode()

    # -- self-health watchdog -------------------------------------------------
    def _start_watchdog(self):
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(self.WATCHDOG_MS)
        self._watchdog.timeout.connect(self._check_heartbeat)
        self._last_watchdog_tick = time.monotonic()
        self._watchdog.start()

    # A watchdog tick that arrives this much later than scheduled means the
    # process was not being scheduled at all — sleep, hibernation, modern
    # standby, or a long stretch of starvation.
    RESUME_GAP_FACTOR = 2.0
    # ...and if that outage accounts for most of the missing samples, the
    # sampler is not dead, it was suspended along with everything else. A
    # genuinely dead sampler shows the opposite shape: ticks keep arriving
    # on time (small gap) while the sample age climbs (large age).
    RESUME_GAP_RATIO = 0.5

    def _resumed_from_suspend(self, now: float, age: float) -> bool:
        gap = now - self._last_watchdog_tick
        self._last_watchdog_tick = now
        return (gap > self.WATCHDOG_MS / 1000.0 * self.RESUME_GAP_FACTOR
                and gap > age * self.RESUME_GAP_RATIO)

    def _stall_after_s(self) -> float:
        """Stall threshold, derived from the cadence actually in use — the
        throttled cadences are slower, and must not read as a stall."""
        return MetricsSampler.MODES[self._view_mode][0] * 4 + 5

    def _check_heartbeat(self):
        """Detect a dead/stuck sampler by its missing heartbeat."""
        self._maybe_retry_shell()
        now = time.monotonic()
        age = now - self._last_sample_mono
        if self._resumed_from_suspend(now, age):
            # Waking from sleep is not a fault: nothing was running, so of
            # course no samples arrived. Alerting here fired a red status
            # and a critical toast on every single resume. Give the sampler
            # one fresh interval to report in instead.
            log.info("Resumed after %.0f s not running — rearming the "
                     "heartbeat instead of reporting a stall", age)
            self._last_sample_mono = now
            return
        if age > self._stall_after_s() and not self._stalled:
            self._stalled = True
            log.error("Monitoring stalled — no samples for %.0f s; "
                      "requesting sampler restart", age)
            self._status_label.setText(
                f"{theme.STATUS_ICON['critical']} Monitoring stalled — "
                "restarting sampler")
            self._status_label.setStyleSheet(
                f"color: {theme.STATUS['critical']};")
            self._log_line("⚠ Monitoring stalled (no data for "
                           f"{age:.0f} s) — restarting sampler. "
                           "See log via tray menu.")
            if self._tray:
                self._tray.set_status("critical", "Monitoring stalled")
                self._tray.notify(
                    "Desktop Optimizer problem",
                    "Metric collection stopped responding — restarting it. "
                    "Check the log (tray menu → Open log folder) if this "
                    "repeats.", level="critical")
            self.sampler_stalled.emit()

    # -- self-healing ------------------------------------------------------------
    def on_repeated_freeze(self, diagnosis: str):
        """Called from the freeze watchdog thread — act, don't just log.

        The app sheds the shell traffic that blocks the GUI thread rather
        than leaving the user with an unresponsive window.
        """
        shellguard.guard.degrade(diagnosis)

    @Slot(bool, str)
    def _on_shell_state_changed(self, degraded: bool, reason: str):
        """Reflect degraded mode in the UI. Always runs on the GUI thread —
        it is reached through shell_state_changed, never called directly."""
        if degraded:
            self._log_line(
                f"⚠ Reduced mode: {reason}. The live taskbar readout and "
                "position tracking are paused; monitoring continues. "
                "This lifts automatically once Windows responds normally.")
            self._add_alert(Alert(
                "self_degraded", "warning", "Desktop Optimizer reduced mode",
                f"Paused features that were blocking the interface — "
                f"{reason}.",
                ["Monitoring, alerts and cleanups all keep working",
                 "Normal behaviour returns automatically; see the log for "
                 "details"],
                time.time()))
            if self._tray:
                # one cheap push, then the tray goes silent until recovery
                self._tray.show_static_icon(self._analyzer.status())
        else:
            self._log_line("✓ Windows is responsive again — full behaviour "
                           "restored.")
            if self._freeze_watch is not None:
                self._freeze_watch.rearm()

    def set_freeze_watch(self, watch):
        self._freeze_watch = watch

    # -- first run ---------------------------------------------------------------
    def maybe_show_welcome(self):
        """Introduce the app once, on the very first launch.

        The app opens as a small strip docked in the taskbar, which is easy
        to miss and easy to misread as "nothing happened" — so say what it
        is, where it went, and that it will not touch anything by itself.
        """
        if self._settings.value("intro/shown", False, type=bool):
            return
        # Recorded before the dialog opens: if anything goes wrong in here,
        # the greeting is skipped rather than repeated every launch.
        self._settings.setValue("intro/shown", True)

        box = QMessageBox(self)
        box.setWindowTitle(f"Welcome to {APP_NAME}")
        box.setTextFormat(Qt.RichText)
        box.setIcon(QMessageBox.NoIcon)
        box.setText(
            f'<div style="font-size:15px; font-weight:600;">'
            f'{APP_NAME} is now watching this PC</div>')
        box.setInformativeText(
            "<p>It tracks CPU, memory, disk, network and responsiveness "
            "continuously, and tells you when the machine starts to "
            "degrade — including which process is to blame.</p>"
            "<p><b>It is running as a small strip docked in your "
            "taskbar</b>, next to the clock. Double-click that strip for "
            "the full dashboard, or right-click it for options. There is "
            "also an icon in the notification area showing live CPU load.</p>"
            "<p><b>Nothing is cleaned or changed automatically.</b> The "
            "Optimize tab has one-click actions, and each runs only when "
            "you click it.</p>"
            "<p>The <b>Guide</b> tab explains everything in detail.</p>")
        box.setTextInteractionFlags(Qt.TextSelectableByMouse)
        open_guide = box.addButton("Open the guide", QMessageBox.AcceptRole)
        box.addButton("Got it", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_guide:
            self.show_guide()

    def show_guide(self):
        """Bring up the dashboard on the Guide tab."""
        self.exit_mini_mode()
        self._tabs.setCurrentWidget(self._guide)

    def show_about(self):
        show_about(self if self.isVisible() else None)

    # -- manual update check -----------------------------------------------------
    def check_for_updates(self):
        """Ask GitHub once whether a newer release exists.

        Runs on the thread pool: this is a network round trip, and a
        managed network can leave it hanging for the full timeout. Doing it
        on the GUI thread would freeze the window for exactly as long.
        """
        if self._update_in_flight:
            return
        self._update_in_flight = True
        self._guide.btn_updates.setEnabled(False)
        self._guide.btn_updates.setText("Checking…")
        self._log_line("Checking for a newer release…")
        worker = Worker(updates.check)
        worker.signals.done.connect(self._update_check_done)
        self._pool.start(worker)

    def _update_check_done(self, res):
        self._update_in_flight = False
        self._guide.btn_updates.setEnabled(True)
        self._guide.btn_updates.setText("Check for updates")
        if not isinstance(res, updates.UpdateCheck):
            # Worker turns an unexpected exception into an ActionResult
            log.error("Update check returned %r", res)
            self._log_line("× Update check failed — see the log.")
            return

        if res.status == updates.AVAILABLE:
            self._log_line(f"↑ Version {res.latest} is available "
                           f"(you have {res.current}).")
        elif res.status == updates.CURRENT:
            self._log_line(f"✓ Up to date ({res.current} is the latest "
                           f"release).")
        else:
            self._log_line(f"Update check: {res.detail or res.status}")

        # The dialog is modal, so raise the window first when the app is
        # sitting in mini mode or the tray — otherwise the prompt appears
        # with no visible parent behind it.
        if not self.isVisible():
            self.show_guide()
        if show_update_result(self, res):
            QDesktopServices.openUrl(QUrl(res.url))
            self._log_line(f"Opened {res.url} in your browser.")

    def _maybe_retry_shell(self):
        """Probe once in a while whether the shell recovered."""
        if shellguard.guard.due_for_retry():
            shellguard.guard.restore()

    # -- tab lifecycle -----------------------------------------------------------
    def _on_tab_changed(self, _index: int):
        self._processes.set_active(
            self._tabs.currentWidget() is self._processes)

    # -- mini mode ---------------------------------------------------------------
    def _ensure_mini(self) -> MiniWindow:
        if self._mini is None:
            self._mini = MiniWindow()
            self._mini.restore_requested.connect(self.exit_mini_mode)
            self._mini.hide_requested.connect(self.hide_to_tray)
            self._mini.quit_requested.connect(self.quit_requested)
            self._mini.docked_changed.connect(self._on_mini_docked_changed)
            # Docked into the taskbar by default; a remembered free position
            # is only used if the user dragged it out of the dock before.
            docked = self._settings.value("mini/docked", True, type=bool)
            pos = self._settings.value("mini/pos")
            if docked:
                self._mini.set_docked(True, announce=False)
            elif pos is not None:
                self._mini.set_docked(False, announce=False)
                self._mini.move(pos)
                self._mini.clamp_to_screen()
            else:
                self._mini.set_docked(False, announce=False)
                self._mini.park_bottom_right()
        return self._mini

    def _on_mini_docked_changed(self, docked: bool):
        self._settings.setValue("mini/docked", docked)
        if not docked and self._mini is not None:
            self._settings.setValue("mini/pos", self._mini.position())
        log.info("Mini strip %s", "docked to taskbar" if docked
                 else "undocked (floating)")

    def enter_mini_mode(self):
        """Shrink to the compact strip; the dashboard hides entirely."""
        mini = self._ensure_mini()
        self._processes.set_active(False)      # stop scanning while hidden
        # Show the strip *before* hiding the dashboard: hiding first made
        # the app flip through "hidden" for a few milliseconds, which cost
        # a pointless working-set trim on every single toggle.
        mini.show()
        mini.raise_()
        self.hide()
        self._sync_view_mode()

    def exit_mini_mode(self):
        if self._mini is not None and self._mini.isVisible():
            if not self._mini.is_docked():
                self._settings.setValue("mini/pos", self._mini.position())
            self._mini.hide()
        if self._charts_stale:
            self._redraw_charts()
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._on_tab_changed(self._tabs.currentIndex())
        self._sync_view_mode()

    def hide_to_tray(self):
        """Hide everything; only the tray icon remains."""
        if self._mini is not None:
            self._mini.hide()
        self.hide()
        self._sync_view_mode()
        # Only burn the one-shot hint if the toast actually went out —
        # notify() suppresses while rate-limited or degraded, and a hint
        # nobody saw must still be owed.
        if self._tray and not self._hide_hint_shown:
            self._hide_hint_shown = self._tray.notify(
                "Still monitoring",
                "Desktop Optimizer keeps running in the notification area. "
                "Right-click its icon for the dashboard, mini mode or exit.")

    def start_in_mini_mode(self):
        """Startup path: bring up the strip instead of the dashboard."""
        mini = self._ensure_mini()
        mini.show()
        mini.raise_()
        self._sync_view_mode()

    def in_mini_mode(self) -> bool:
        return self._mini is not None and self._mini.isVisible()

    def toggle_mini_mode(self):
        if self.in_mini_mode():
            self.exit_mini_mode()
        else:
            self.enter_mini_mode()

    def _redraw_charts(self):
        for chart in (self._chart_cpu, self._chart_mem,
                      self._chart_disk, self._chart_net):
            chart.redraw()
        self._charts_stale = False

    # -- per-sample update ------------------------------------------------------
    @Slot(object)
    def on_sample(self, snap: Snapshot):
        self._last_sample_mono = time.monotonic()
        if self._stalled:
            self._stalled = False
            log.info("Monitoring recovered — samples flowing again")
            self._log_line("✓ Monitoring recovered — data is live again.")
        try:
            self._apply_sample(snap)
        except Exception:
            # a UI update bug must be visible, not a silent freeze
            log.exception("Dashboard update failed")
            self._ui_error_notes += 1
            if self._ui_error_notes <= 3:
                self._log_line("× Dashboard update error — see log "
                               "(tray menu → Open log folder).")

    def _apply_sample(self, snap: Snapshot):
        self._sample_count += 1
        lag = self._ui_lag_ms
        # Cards, charts and the vitals strip cost nothing to skip while the
        # dashboard is hidden or minimised (mini mode, tray, taskbar).
        visible = self._view_mode == "dashboard"

        gb = 1 << 30
        if visible:
            self._update_dashboard(snap, lag, gb)
        else:
            self._charts_stale = True
        self._chart_cpu.update(snap.cpu, redraw=visible)
        self._chart_mem.update(snap.mem_percent, redraw=visible)
        self._chart_disk.update(snap.disk_busy, redraw=visible)
        self._chart_net.update(snap.net_recv_bps / 1024.0,
                               snap.net_sent_bps / 1024.0, redraw=visible)

        self._update_proc_table(snap)
        if visible and self._tabs.currentWidget() is self._details:
            self._details.update_snapshot(snap, lag)

        events = self._analyzer.evaluate(snap, lag)
        for alert in events:
            self._add_alert(alert)
        status = self._analyzer.status()
        self._set_status(status)

        if self._mini is not None and self._mini.isVisible():
            self._mini.update_snapshot(snap, lag, status)

        if self._tray:
            self._tray.set_status(
                status,
                f"CPU {snap.cpu:.0f}% · MEM {snap.mem_percent:.0f}% · "
                f"{STATUS_TEXT[status]}",
                load=snap.cpu)
            # One toast for the worst thing that happened, not one each:
            # every toast is a blocking shell call.
            worst = max((a for a in events
                         if a.severity in ("warning", "critical")),
                        key=lambda a: SEVERITY_RANK[a.severity],
                        default=None)
            if worst is not None:
                body = worst.detail
                if worst.recommendations:
                    body += "\n" + worst.recommendations[0]
                self._tray.notify(worst.title, body, level=worst.severity)

        self._watch_self_overhead(snap)

        # every ~5 min: the temp-tree walk is the app's most expensive
        # recurring task, and reclaimable size changes slowly
        if self._sample_count % 200 == 1 and self._sample_count > 1:
            self._refresh_reclaimable()

    def _update_dashboard(self, snap: Snapshot, lag: float, gb: int):
        """Cards + vitals strip — only worth doing when on screen."""
        freq = f" · {snap.freq_mhz / 1000:.1f} GHz" if snap.freq_mhz else ""
        top_c = snap.top_cpu[0] if snap.top_cpu else None
        self._card_cpu.update_values(
            f"{snap.cpu:.0f}%",
            f"{len(snap.per_core)} thr{freq} · peak {max(snap.per_core):.0f}%",
            f"top: {top_c.name} {top_c.cpu:.0f}%" if top_c else "")
        top_m = snap.top_mem[0] if snap.top_mem else None
        self._card_mem.update_values(
            f"{snap.mem_percent:.0f}%",
            f"{snap.mem_used / gb:.1f} / {snap.mem_total / gb:.1f} GB · "
            f"free {(snap.mem_total - snap.mem_used) / gb:.1f}",
            f"top: {top_m.name} {human_bytes(top_m.rss)}" if top_m else "")
        fullest = max(snap.volumes, key=lambda v: v.percent, default=None)
        self._card_disk.update_values(
            f"{snap.disk_busy:.0f}%",
            f"R {human_rate(snap.disk_read_bps)} · "
            f"W {human_rate(snap.disk_write_bps)}",
            (f"{fullest.mount} {fullest.percent:.0f}% · free "
             f"{human_bytes(fullest.total - fullest.used)}") if fullest else "")
        resp_word = ("Smooth" if lag < 80 else
                     "Sluggish" if lag < 300 else "Very slow")
        self._card_resp.update_values(
            f"{lag:.0f} ms", resp_word,
            f"intr {snap.intr_per_s / 1000:.1f}k/s")

        self._strip["net"].setText(
            f"▼ {human_rate(snap.net_recv_bps)}   "
            f"▲ {human_rate(snap.net_sent_bps)}")
        self._strip["procs"].setText(f"{snap.proc_count:,} processes")
        self._strip["ctx"].setText(f"ctx {snap.ctx_per_s / 1000:.1f}k/s")
        self._strip["syscalls"].setText(
            f"syscalls {snap.syscalls_per_s / 1000:.0f}k/s")
        self._strip["pagefile"].setText(f"pagefile {snap.swap_percent:.0f}%")
        up = snap.uptime_s
        self._strip["uptime"].setText(
            f"up {int(up // 86400)}d {int(up % 86400 // 3600)}h")
        self._strip["self"].setText(
            f"app cost: {snap.self_cpu:.1f}% CPU · "
            f"{human_bytes(snap.self_rss)}")

    SELF_CPU_BUDGET = config.SELF_CPU_BUDGET   # % of machine, sustained ~60 s

    def _watch_self_overhead(self, snap: Snapshot):
        """The monitor must never become its own performance problem."""
        self._self_hist.append(snap.self_cpu)
        if (len(self._self_hist) == self._self_hist.maxlen
                and not self._self_warned):
            avg = sum(self._self_hist) / len(self._self_hist)
            if avg > self.SELF_CPU_BUDGET:
                self._self_warned = True
                log.warning("Own overhead high: avg %.1f%% CPU over ~60 s "
                            "(budget %.0f%%)", avg, self.SELF_CPU_BUDGET)
                self._add_alert(Alert(
                    "self_overhead", "warning",
                    "Desktop Optimizer overhead high",
                    f"The app itself averaged {avg:.1f}% CPU over the last "
                    f"minute (budget {self.SELF_CPU_BUDGET:.0f}%).",
                    ["Close the Processes/Details tab when not needed",
                     "Report this — the sampler may be misbehaving "
                     "(logs/app.log)"],
                    snap.ts))

    def _update_proc_table(self, snap: Snapshot):
        rows = self._proc_table.rowCount()
        for row in range(rows):
            if row < len(snap.top_cpu):
                p = snap.top_cpu[row]
                values = (p.name, f"{p.cpu:.0f}", human_bytes(p.rss))
            else:
                values = ("", "", "")
            for col, text in enumerate(values):
                item = self._proc_table.item(row, col)
                if item is None:
                    item = QTableWidgetItem()
                    if col > 0:
                        item.setTextAlignment(
                            Qt.AlignRight | Qt.AlignVCenter)
                    self._proc_table.setItem(row, col, item)
                item.setText(text)

    # -- alerts & status ---------------------------------------------------------
    def _set_status(self, status: str):
        self._status_label.setText(
            f"{theme.STATUS_ICON[status]} {STATUS_TEXT[status]}")
        self._status_label.setStyleSheet(f"color: {theme.STATUS[status]};")

    def _add_alert(self, alert: Alert):
        if self._alerts_placeholder is not None:
            self._alerts.takeItem(self._alerts.row(self._alerts_placeholder))
            self._alerts_placeholder = None
        ts = time.strftime("%H:%M:%S", time.localtime(alert.ts))
        icon = theme.STATUS_ICON.get(alert.severity, "●")
        lines = [f"{icon} {ts}  {alert.title}", alert.detail]
        lines += [f"   → {r}" for r in alert.recommendations]
        item = QListWidgetItem("\n".join(lines))
        color = {"warning": theme.STATUS["warning"],
                 "critical": theme.STATUS["critical"]}.get(
                     alert.severity, theme.STATUS["good"])
        item.setForeground(QColor(color))
        self._alerts.insertItem(0, item)
        while self._alerts.count() > 50:
            self._alerts.takeItem(self._alerts.count() - 1)
        self._log_line(f"{alert.title} — {alert.detail}")

    def _log_line(self, text: str):
        ts = time.strftime("%H:%M:%S")
        self._optimize.log.appendPlainText(f"[{ts}] {text}")

    # -- one-click actions ---------------------------------------------------------
    def _run_action(self, button: QPushButton, fn, refresh_reclaimable=False):
        button.setEnabled(False)
        worker = Worker(fn)
        worker.signals.done.connect(
            lambda res: self._action_done(button, res, refresh_reclaimable))
        self._pool.start(worker)

    def _action_done(self, button: QPushButton, result, refresh_reclaimable):
        button.setEnabled(True)
        self._on_action_result_only(result)
        if refresh_reclaimable:
            self._refresh_reclaimable()

    def _on_action_result_only(self, result):
        mark = "✓" if result.ok else "×"
        if result.ok:
            log.info("Action %s: %s", result.name, result.message)
        else:
            log.error("Action FAILED %s: %s", result.name, result.message)
        self._log_line(f"{mark} {result.name}: {result.message}")

    def run_quick_clean(self):
        """Tray shortcut: temp files + DNS cache."""
        self._log_line("Quick clean started (temp files + DNS cache)…")
        self._run_action(self._optimize.btn_temp, optimizer.clear_temp, True)
        self._run_action(self._optimize.btn_dns, optimizer.flush_dns)

    def _refresh_reclaimable(self):
        worker = Worker(lambda: (optimizer.scan_temp(),
                                 optimizer.recycle_bin_size()))
        worker.signals.done.connect(self._reclaimable_done)
        self._pool.start(worker)

    def _reclaimable_done(self, res):
        if not isinstance(res, tuple):
            # worker caught an exception and returned an ActionResult
            log.error("Reclaimable-size scan failed: %s",
                      getattr(res, "message", res))
            return
        (temp_bytes, _count), (bin_bytes, _items) = res
        self._optimize.btn_temp.setText(
            f"Clear temp files  ·  ~{human_bytes(temp_bytes)}")
        self._optimize.btn_bin.setText(
            f"Empty Recycle Bin  ·  {human_bytes(bin_bytes)}")

    def _quit_for_elevation(self):
        log.info("Relaunching elevated — closing this instance")
        self.request_quit()
        from PySide6.QtWidgets import QApplication
        QApplication.instance().quit()

    # -- window lifecycle ---------------------------------------------------------
    def closeEvent(self, event: QCloseEvent):
        if self._quitting or self._tray is None or not self._tray.isVisible():
            event.accept()
            return
        # Closing the dashboard falls back to the mini strip — that is the
        # app's default surface, not a hidden background process.
        event.ignore()
        self.enter_mini_mode()
        if not self._hide_hint_shown:
            self._hide_hint_shown = self._tray.notify(
                "Still monitoring",
                "Desktop Optimizer is in mini mode. Double-click the strip "
                "for the dashboard; right-click it for more options.")
