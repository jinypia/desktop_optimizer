"""Main window: tabbed dashboard (Overview / Details / Processes / Optimize)
with an always-visible health & alerts side panel."""
from __future__ import annotations

import logging
import time
from collections import deque

import pyqtgraph as pg
from PySide6.QtCore import Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QPushButton, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from .. import config, optimizer
from ..analyzer import Alert, Analyzer
from ..monitor import Snapshot
from ..util import human_bytes, human_rate
from . import theme
from .details_tab import DetailsTab
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
    RESP_TIMER_MS = 250      # responsiveness probe period
    WATCHDOG_MS = 5000       # self-health check period
    # no samples for this long -> monitoring is considered stalled
    STALL_AFTER_S = config.SAMPLE_INTERVAL_S * 4 + 5

    sampler_stalled = Signal()   # main() restarts the sampler on this

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
        # ~60 s of own-overhead history; warns if the app itself gets heavy
        self._self_hist = deque(maxlen=40)
        self._self_warned = False

        pg.setConfigOptions(antialias=True, background=theme.SURFACE,
                            foreground=theme.MUTED)
        self._build_ui()
        self._start_lag_probe()
        self._start_watchdog()
        self._refresh_reclaimable()

    def set_tray(self, tray):
        self._tray = tray

    def show_startup_note(self, text: str):
        self._log_line(text)

    def request_quit(self):
        self._quitting = True

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
        self._tabs.currentChanged.connect(self._on_tab_changed)
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
        lag = max(0.0, (now - self._last_tick) * 1000.0 - self.RESP_TIMER_MS)
        self._last_tick = now
        # EMA smooths one-off spikes (window drags, etc.)
        self._ui_lag_ms = 0.7 * self._ui_lag_ms + 0.3 * lag

    # -- self-health watchdog -------------------------------------------------
    def _start_watchdog(self):
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(self.WATCHDOG_MS)
        self._watchdog.timeout.connect(self._check_heartbeat)
        self._watchdog.start()

    def _check_heartbeat(self):
        """Detect a dead/stuck sampler by its missing heartbeat."""
        age = time.monotonic() - self._last_sample_mono
        if age > self.STALL_AFTER_S and not self._stalled:
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

    # -- tab lifecycle -----------------------------------------------------------
    def _on_tab_changed(self, _index: int):
        self._processes.set_active(
            self._tabs.currentWidget() is self._processes)

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

        gb = 1 << 30
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
        self._watch_self_overhead(snap)

        self._chart_cpu.update(snap.cpu)
        self._chart_mem.update(snap.mem_percent)
        self._chart_disk.update(snap.disk_busy)
        self._chart_net.update(snap.net_recv_bps / 1024.0,
                               snap.net_sent_bps / 1024.0)

        self._update_proc_table(snap)
        if self._tabs.currentWidget() is self._details and self.isVisible():
            self._details.update_snapshot(snap, lag)

        events = self._analyzer.evaluate(snap, lag)
        for alert in events:
            self._add_alert(alert)
        status = self._analyzer.status()
        self._set_status(status)

        if self._tray:
            self._tray.set_status(
                status,
                f"CPU {snap.cpu:.0f}% · MEM {snap.mem_percent:.0f}% · "
                f"{STATUS_TEXT[status]}")
            for alert in events:
                if alert.severity in ("warning", "critical"):
                    body = alert.detail
                    if alert.recommendations:
                        body += "\n" + alert.recommendations[0]
                    self._tray.notify(alert.title, body, level=alert.severity)

        # every ~5 min: the temp-tree walk is the app's most expensive
        # recurring task, and reclaimable size changes slowly
        if self._sample_count % 200 == 1 and self._sample_count > 1:
            self._refresh_reclaimable()

    SELF_CPU_BUDGET = 5.0     # % of total machine CPU, sustained ~60 s

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
        event.ignore()
        self.hide()
        if not self._hide_hint_shown:
            self._hide_hint_shown = True
            self._tray.notify(
                "Still monitoring",
                "Desktop Optimizer keeps running in the tray. "
                "Right-click the tray icon to exit.")
