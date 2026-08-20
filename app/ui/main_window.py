"""Main dashboard window."""
from __future__ import annotations

import time
from functools import partial

import pyqtgraph as pg
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import config, optimizer
from ..analyzer import Alert, Analyzer
from ..monitor import Snapshot
from ..util import human_bytes, human_rate
from . import theme
from .widgets import LiveChart, MetricCard

STATUS_TEXT = {
    "good": "System healthy",
    "warning": "Performance degraded",
    "critical": "Severe degradation",
}


class _WorkerSignals(QObject):
    done = Signal(object)


class _Worker(QRunnable):
    """Runs a callable on the global thread pool, emits its result."""

    def __init__(self, fn):
        super().__init__()
        self._fn = fn
        self.signals = _WorkerSignals()

    def run(self):
        try:
            result = self._fn()
        except Exception as e:  # surface the error, don't kill the pool thread
            result = optimizer.ActionResult(
                getattr(self._fn, "__name__", "action"), False, str(e))
        self.signals.done.emit(result)


class MainWindow(QMainWindow):
    RESP_TIMER_MS = 250      # responsiveness probe period

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Desktop Optimizer")
        self.resize(1200, 800)

        self._analyzer = Analyzer()
        self._pool = QThreadPool.globalInstance()
        self._tray = None
        self._ui_lag_ms = 0.0
        self._sample_count = 0
        self._hide_hint_shown = False
        self._quitting = False
        self._alerts_placeholder = None

        pg.setConfigOptions(antialias=True, background=theme.SURFACE,
                            foreground=theme.MUTED)
        self._build_ui()
        self._start_lag_probe()
        self._refresh_reclaimable()

    def set_tray(self, tray):
        self._tray = tray

    def request_quit(self):
        self._quitting = True

    # -- layout ----------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # metric cards
        cards = QHBoxLayout()
        cards.setSpacing(10)
        self._card_cpu = MetricCard("CPU", theme.SERIES_CPU)
        self._card_mem = MetricCard("Memory", theme.SERIES_MEM)
        self._card_disk = MetricCard("Disk", theme.SERIES_DISK)
        self._card_resp = MetricCard("Responsiveness", theme.MUTED)
        for card in (self._card_cpu, self._card_mem,
                     self._card_disk, self._card_resp):
            cards.addWidget(card, 1)
        root.addLayout(cards)

        body = QHBoxLayout()
        body.setSpacing(10)
        root.addLayout(body, 1)

        # charts (2x2)
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
        body.addWidget(chart_frame, 1)

        # side column
        side = QWidget()
        side.setFixedWidth(380)
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, 0, 0)
        side_lay.setSpacing(10)
        side_lay.addWidget(self._build_alerts_panel(), 1)
        side_lay.addWidget(self._build_process_panel(), 0)
        side_lay.addWidget(self._build_actions_panel(), 0)
        body.addWidget(side, 0)

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
            table.setRowHeight(r, 22)
        table.setFixedHeight(rows * 22 + 30)
        self._proc_table = table
        lay.addWidget(table)
        return panel

    def _build_actions_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        title = QLabel("One-click optimize")
        title.setObjectName("panelTitle")
        note = QLabel("Actions run only when you click — nothing is automatic.")
        note.setObjectName("panelNote")
        note.setWordWrap(True)
        lay.addWidget(title)
        lay.addWidget(note)

        self._btn_temp = QPushButton("Clear temp files")
        self._btn_bin = QPushButton("Empty Recycle Bin")
        self._btn_dns = QPushButton("Flush DNS cache")
        self._btn_trim = QPushButton("Trim process memory")
        self._btn_explorer = QPushButton("Restart Explorer…")

        self._btn_temp.clicked.connect(partial(
            self._run_action, self._btn_temp, optimizer.clear_temp, True))
        self._btn_bin.clicked.connect(partial(
            self._run_action, self._btn_bin, optimizer.empty_recycle_bin, True))
        self._btn_dns.clicked.connect(partial(
            self._run_action, self._btn_dns, optimizer.flush_dns))
        self._btn_trim.clicked.connect(partial(
            self._run_action, self._btn_trim, optimizer.trim_working_sets))
        self._btn_explorer.clicked.connect(self._confirm_restart_explorer)

        for btn in (self._btn_temp, self._btn_bin, self._btn_dns,
                    self._btn_trim, self._btn_explorer):
            lay.addWidget(btn)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(400)
        self._log.setFixedHeight(80)
        self._log.setPlaceholderText("Action results appear here.")
        lay.addWidget(self._log)
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

    # -- per-sample update ------------------------------------------------------
    @Slot(object)
    def on_sample(self, snap: Snapshot):
        self._sample_count += 1
        lag = self._ui_lag_ms

        freq = f" · {snap.freq_mhz / 1000:.1f} GHz" if snap.freq_mhz else ""
        self._card_cpu.update_values(
            f"{snap.cpu:.0f}%", f"{len(snap.per_core)} threads{freq}")
        self._card_mem.update_values(
            f"{snap.mem_percent:.0f}%",
            f"{human_bytes(snap.mem_used)} / {human_bytes(snap.mem_total)}")
        self._card_disk.update_values(
            f"{snap.disk_busy:.0f}%",
            f"R {human_rate(snap.disk_read_bps)} · "
            f"W {human_rate(snap.disk_write_bps)}")
        resp_word = ("Smooth" if lag < 80 else
                     "Sluggish" if lag < 300 else "Very slow")
        self._card_resp.update_values(f"{lag:.0f} ms", resp_word)

        self._chart_cpu.update(snap.cpu)
        self._chart_mem.update(snap.mem_percent)
        self._chart_disk.update(snap.disk_busy)
        self._chart_net.update(snap.net_recv_bps / 1024.0,
                               snap.net_sent_bps / 1024.0)

        self._update_proc_table(snap)

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

        if self._sample_count % 30 == 1 and self._sample_count > 1:
            self._refresh_reclaimable()

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
        self._log.appendPlainText(f"[{ts}] {text}")

    # -- one-click actions ---------------------------------------------------------
    def _run_action(self, button: QPushButton, fn, refresh_reclaimable=False):
        button.setEnabled(False)
        worker = _Worker(fn)
        worker.signals.done.connect(
            lambda res: self._action_done(button, res, refresh_reclaimable))
        self._pool.start(worker)

    def _action_done(self, button: QPushButton, result, refresh_reclaimable):
        button.setEnabled(True)
        mark = "✓" if result.ok else "✗"
        self._log_line(f"{mark} {result.name}: {result.message}")
        if refresh_reclaimable:
            self._refresh_reclaimable()

    def _confirm_restart_explorer(self):
        ret = QMessageBox.warning(
            self, "Restart Explorer",
            "This closes and restarts Windows Explorer — the taskbar and any "
            "open folder windows will briefly disappear. Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret == QMessageBox.Yes:
            self._run_action(self._btn_explorer, optimizer.restart_explorer)

    def run_quick_clean(self):
        """Tray shortcut: temp files + DNS cache."""
        self._log_line("Quick clean started (temp files + DNS cache)…")
        self._run_action(self._btn_temp, optimizer.clear_temp, True)
        self._run_action(self._btn_dns, optimizer.flush_dns)

    def _refresh_reclaimable(self):
        worker = _Worker(lambda: (optimizer.scan_temp(),
                                  optimizer.recycle_bin_size()))
        worker.signals.done.connect(self._reclaimable_done)
        self._pool.start(worker)

    def _reclaimable_done(self, res):
        if not isinstance(res, tuple):
            return
        (temp_bytes, _count), (bin_bytes, _items) = res
        self._btn_temp.setText(f"Clear temp files  ·  ~{human_bytes(temp_bytes)}")
        self._btn_bin.setText(f"Empty Recycle Bin  ·  {human_bytes(bin_bytes)}")

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
