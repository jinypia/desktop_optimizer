"""Professional detail view: system info, per-core CPU, memory, kernel
activity, per-volume disks, per-interface network, battery.

Refreshed only while the tab is visible — heavy queries never run hidden.
"""
from __future__ import annotations

import os
import platform
import sys
import time

import psutil
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QProgressBar, QScrollArea, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ..monitor import Snapshot
from ..util import human_bytes, human_rate

MAX_CORE_BARS = 64


def _cpu_name() -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
    except OSError:
        return platform.processor() or "?"


def _fmt_uptime(seconds: float) -> str:
    d, rem = divmod(int(seconds), 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return f"{d}d {h}h {m}m" if d else f"{h}h {m}m"


class _InfoGrid(QGroupBox):
    """A titled two-column grid of label→value rows, updatable by key."""

    def __init__(self, title: str, rows):
        super().__init__(title)
        self._values = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(12, 8, 12, 10)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        for i, key in enumerate(rows):
            name = QLabel(key)
            name.setObjectName("detailKey")
            val = QLabel("–")
            val.setObjectName("detailValue")
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(name, i, 0)
            grid.addWidget(val, i, 1)
            self._values[key] = val
        grid.setColumnStretch(1, 1)

    def set(self, key: str, text: str):
        self._values[key].setText(text)


class DetailsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_nic = None          # (timestamp, {nic: counters})
        self._nic_meta_age = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        cols = QHBoxLayout(body)
        cols.setContentsMargins(4, 0, 4, 4)
        cols.setSpacing(10)
        left = QVBoxLayout()
        right = QVBoxLayout()
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)

        # -- system (static) --
        self._sys = _InfoGrid("System", [
            "Computer", "User", "OS", "CPU", "Cores / threads",
            "Total RAM", "Python", "Booted", "Uptime"])
        left.addWidget(self._sys)

        # -- per-core CPU --
        cores = QGroupBox("CPU per core")
        core_grid = QGridLayout(cores)
        core_grid.setContentsMargins(12, 8, 12, 10)
        core_grid.setHorizontalSpacing(10)
        core_grid.setVerticalSpacing(6)
        n = min(psutil.cpu_count(logical=True) or 1, MAX_CORE_BARS)
        self._core_bars = []
        per_row = 2
        for i in range(n):
            lbl = QLabel(f"#{i}")
            lbl.setObjectName("detailKey")
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            bar.setFixedHeight(8)
            pct = QLabel("0%")
            pct.setObjectName("detailKey")
            pct.setFixedWidth(34)
            pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            r, c = divmod(i, per_row)
            core_grid.addWidget(lbl, r, c * 3)
            core_grid.addWidget(bar, r, c * 3 + 1)
            core_grid.addWidget(pct, r, c * 3 + 2)
            core_grid.setColumnStretch(c * 3 + 1, 1)
            self._core_bars.append((bar, pct))
        left.addWidget(cores)

        # -- kernel / activity --
        self._act = _InfoGrid("Kernel activity", [
            "Context switches", "System calls", "Interrupts",
            "Processes", "UI latency"])
        left.addWidget(self._act)
        left.addStretch(1)

        # -- memory --
        self._mem = _InfoGrid("Memory", [
            "In use", "Available", "Usage", "Pagefile (swap)", "Battery"])
        right.addWidget(self._mem)

        # -- disks --
        disks_box = QGroupBox("Volumes")
        db = QVBoxLayout(disks_box)
        db.setContentsMargins(8, 8, 8, 8)
        self._disk_table = self._make_table(
            ["Mount", "Total", "Free", "Used %"])
        db.addWidget(self._disk_table)
        right.addWidget(disks_box)

        # -- network interfaces --
        net_box = QGroupBox("Network interfaces")
        nb = QVBoxLayout(net_box)
        nb.setContentsMargins(8, 8, 8, 8)
        self._net_table = self._make_table(
            ["Interface", "IPv4", "Down", "Up", "Received", "Sent"])
        nb.addWidget(self._net_table)
        right.addWidget(net_box)
        right.addStretch(1)

        self._fill_static()

    @staticmethod
    def _make_table(headers):
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionMode(QAbstractItemView.NoSelection)
        t.setFocusPolicy(Qt.NoFocus)
        t.setShowGrid(False)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        t.setFixedHeight(150)
        return t

    def _fill_static(self):
        win = sys.getwindowsversion()
        vm = psutil.virtual_memory()
        self._sys.set("Computer", platform.node())
        self._sys.set("User", os.environ.get("USERNAME", "?"))
        self._sys.set("OS", f"{platform.system()} {platform.release()} "
                            f"(build {win.build})")
        self._sys.set("CPU", _cpu_name())
        phys = psutil.cpu_count(logical=False) or "?"
        logi = psutil.cpu_count(logical=True) or "?"
        self._sys.set("Cores / threads", f"{phys} / {logi}")
        self._sys.set("Total RAM", human_bytes(vm.total))
        self._sys.set("Python", f"{platform.python_version()} "
                                f"({'admin' if _is_admin() else 'standard user'})")
        self._sys.set("Booted", time.strftime(
            "%Y-%m-%d %H:%M", time.localtime(psutil.boot_time())))

    # -- live refresh (only called while tab is visible) ----------------------
    def update_snapshot(self, snap: Snapshot, ui_lag_ms: float):
        self._sys.set("Uptime", _fmt_uptime(snap.uptime_s))

        for i, (bar, pct) in enumerate(self._core_bars):
            v = snap.per_core[i] if i < len(snap.per_core) else 0.0
            bar.setValue(int(v))
            pct.setText(f"{v:.0f}%")

        self._act.set("Context switches", f"{snap.ctx_per_s:,.0f} /s")
        self._act.set("System calls", f"{snap.syscalls_per_s:,.0f} /s")
        self._act.set("Interrupts", f"{snap.intr_per_s:,.0f} /s")
        self._act.set("Processes", f"{snap.proc_count:,}")
        self._act.set("UI latency", f"{ui_lag_ms:.0f} ms")

        self._mem.set("In use", human_bytes(snap.mem_used))
        self._mem.set("Available", human_bytes(snap.mem_total - snap.mem_used))
        self._mem.set("Usage", f"{snap.mem_percent:.0f}%")
        sw = psutil.swap_memory()
        self._mem.set("Pagefile (swap)",
                      f"{human_bytes(sw.used)} / {human_bytes(sw.total)} "
                      f"({sw.percent:.0f}%)")
        if snap.battery:
            pct, plugged, secs = snap.battery
            state = "charging" if plugged else "on battery"
            left = (f", {secs // 3600}h {(secs % 3600) // 60}m left"
                    if secs and secs > 0 else "")
            self._mem.set("Battery", f"{pct:.0f}% ({state}{left})")
        else:
            self._mem.set("Battery", "no battery (desktop)")

        self._update_disks(snap)
        self._update_network()

    def _update_disks(self, snap: Snapshot):
        t = self._disk_table
        t.setRowCount(len(snap.volumes))
        for r, vol in enumerate(snap.volumes):
            free = vol.total - vol.used
            for c, text in enumerate((vol.mount, human_bytes(vol.total),
                                      human_bytes(free), f"{vol.percent:.0f}%")):
                item = t.item(r, c)
                if item is None:
                    item = QTableWidgetItem()
                    if c > 0:
                        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    t.setItem(r, c, item)
                item.setText(text)

    def _update_network(self):
        now = time.monotonic()
        counters = psutil.net_io_counters(pernic=True)
        rates = {}
        if self._last_nic:
            last_t, last = self._last_nic
            dt = max(now - last_t, 1e-3)
            for nic, c in counters.items():
                if nic in last:
                    rates[nic] = ((c.bytes_recv - last[nic].bytes_recv) / dt,
                                  (c.bytes_sent - last[nic].bytes_sent) / dt)
        self._last_nic = (now, counters)

        try:
            addrs = psutil.net_if_addrs()
            stats = psutil.net_if_stats()
        except OSError:
            addrs, stats = {}, {}

        # show active interfaces first, skip loopback
        nics = [n for n in counters
                if not n.lower().startswith("loopback")
                and stats.get(n) is not None and stats[n].isup]
        nics.sort(key=lambda n: -(counters[n].bytes_recv + counters[n].bytes_sent))
        t = self._net_table
        t.setRowCount(len(nics))
        for r, nic in enumerate(nics):
            ipv4 = next((a.address for a in addrs.get(nic, [])
                         if getattr(a.family, "name", "") == "AF_INET"), "—")
            down, up = rates.get(nic, (0.0, 0.0))
            c = counters[nic]
            row = (nic, ipv4, human_rate(max(down, 0)), human_rate(max(up, 0)),
                   human_bytes(c.bytes_recv), human_bytes(c.bytes_sent))
            for col, text in enumerate(row):
                item = t.item(r, col)
                if item is None:
                    item = QTableWidgetItem()
                    if col >= 2:
                        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    t.setItem(r, col, item)
                item.setText(text)


def _is_admin() -> bool:
    from .. import optimizer
    return optimizer.is_admin()
