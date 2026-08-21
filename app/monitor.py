"""Background metrics sampling.

MetricsSampler runs on its own QThread (low priority) and emits a Snapshot
every SAMPLE_INTERVAL_S seconds. All psutil access happens off the GUI
thread.

Own-overhead budget: the expensive work is throttled so monitoring never
becomes its own performance problem —
  - the all-process scan (top lists, count) runs every 2nd cycle,
  - volume usage runs every 10th cycle,
  - the sampler measures the app's own CPU/RAM cost and reports it in
    each Snapshot so the UI can display and police it.

Resilience: an error in one sampling cycle is logged and skipped, not
fatal. If the thread dies anyway, the MainWindow watchdog notices the
missing heartbeat and asks main() to restart the sampler.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import psutil
from PySide6.QtCore import QThread, Signal

from . import config

log = logging.getLogger(__name__)

PROC_SCAN_EVERY = 2       # all-process scan every Nth cycle
VOLUME_SCAN_EVERY = 10    # disk_usage per volume every Nth cycle


@dataclass
class ProcInfo:
    pid: int
    name: str
    cpu: float   # share of total machine CPU, 0-100
    rss: int     # resident memory, bytes


@dataclass
class VolumeInfo:
    mount: str
    percent: float
    used: int
    total: int


@dataclass
class Snapshot:
    ts: float
    cpu: float
    per_core: list
    freq_mhz: float | None
    mem_percent: float
    mem_used: int
    mem_total: int
    swap_percent: float
    disk_busy: float          # % of the interval the disk was servicing I/O
    disk_read_bps: float
    disk_write_bps: float
    net_recv_bps: float
    net_sent_bps: float
    volumes: list
    top_cpu: list
    top_mem: list
    uptime_s: float
    ctx_per_s: float = 0.0
    intr_per_s: float = 0.0
    syscalls_per_s: float = 0.0
    proc_count: int = 0
    battery: tuple | None = None   # (percent, plugged, secs_left) or None
    self_cpu: float = 0.0          # this app's own CPU, % of total machine
    self_rss: int = 0              # this app's own resident memory


class MetricsSampler(QThread):
    sample = Signal(object)  # Snapshot

    def __init__(self, interval_s: float = config.SAMPLE_INTERVAL_S, parent=None):
        super().__init__(parent)
        self._interval = interval_s
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        log.info("Metrics sampler started (interval %.1fs, low priority)",
                 self._interval)
        try:
            self.setPriority(QThread.LowPriority)
        except Exception:
            pass
        try:
            self._run()
            log.info("Metrics sampler stopped normally")
        except Exception:
            log.exception("Metrics sampler DIED — watchdog will restart it")

    def _run(self):
        self._ncpu = psutil.cpu_count(logical=True) or 1
        psutil.cpu_percent(None, percpu=True)          # prime system counter
        for p in psutil.process_iter():                # prime per-process counters
            try:
                p.cpu_percent(None)
            except psutil.Error:
                pass
        self._self = psutil.Process()
        self._self.cpu_percent(None)
        self._last_disk = psutil.disk_io_counters()
        self._last_net = psutil.net_io_counters()
        self._last_cpu_stats = psutil.cpu_stats()
        self._last_t = time.monotonic()
        self._cycle = 0
        self._top_cpu, self._top_mem = [], []
        self._proc_count = 0
        self._volumes = []

        while not self._stop:
            self._sleep(self._interval)
            if self._stop:
                break
            now = time.monotonic()
            dt = max(now - self._last_t, 1e-3)
            self._last_t = now
            self._cycle += 1
            try:
                snap = self._collect(dt)
            except Exception:
                log.exception("Sampling cycle failed — skipping this cycle")
                continue
            self.sample.emit(snap)

    def _collect(self, dt: float) -> Snapshot:
        per_core = psutil.cpu_percent(None, percpu=True) or [0.0]
        cpu = sum(per_core) / len(per_core)
        try:
            freq = psutil.cpu_freq()
            freq_mhz = freq.current if freq else None
        except Exception:
            freq_mhz = None

        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()

        disk = psutil.disk_io_counters()
        if disk and self._last_disk:
            busy_ms = (disk.read_time - self._last_disk.read_time) + \
                      (disk.write_time - self._last_disk.write_time)
            disk_busy = min(100.0, max(0.0, busy_ms / (dt * 10.0)))
            disk_read_bps = max(0.0, (disk.read_bytes - self._last_disk.read_bytes) / dt)
            disk_write_bps = max(0.0, (disk.write_bytes - self._last_disk.write_bytes) / dt)
        else:
            disk_busy = disk_read_bps = disk_write_bps = 0.0
        self._last_disk = disk

        net = psutil.net_io_counters()
        if net and self._last_net:
            net_recv_bps = max(0.0, (net.bytes_recv - self._last_net.bytes_recv) / dt)
            net_sent_bps = max(0.0, (net.bytes_sent - self._last_net.bytes_sent) / dt)
        else:
            net_recv_bps = net_sent_bps = 0.0
        self._last_net = net

        stats = psutil.cpu_stats()
        ctx_per_s = max(0.0, (stats.ctx_switches - self._last_cpu_stats.ctx_switches) / dt)
        intr_per_s = max(0.0, (stats.interrupts - self._last_cpu_stats.interrupts) / dt)
        syscalls_per_s = max(0.0, (stats.syscalls - self._last_cpu_stats.syscalls) / dt)
        self._last_cpu_stats = stats

        if self._cycle % VOLUME_SCAN_EVERY == 1 or not self._volumes:
            self._volumes = self._scan_volumes()
        if self._cycle % PROC_SCAN_EVERY == 1:
            self._scan_processes()

        try:
            batt = psutil.sensors_battery()
            battery = ((batt.percent, batt.power_plugged, batt.secsleft)
                       if batt else None)
        except Exception:
            battery = None

        try:
            self_cpu = self._self.cpu_percent(None) / self._ncpu
            self_rss = self._self.memory_info().rss
        except psutil.Error:
            self_cpu, self_rss = 0.0, 0

        return Snapshot(
            ts=time.time(), cpu=cpu, per_core=per_core, freq_mhz=freq_mhz,
            mem_percent=vm.percent, mem_used=vm.used, mem_total=vm.total,
            swap_percent=sw.percent,
            disk_busy=disk_busy, disk_read_bps=disk_read_bps,
            disk_write_bps=disk_write_bps,
            net_recv_bps=net_recv_bps, net_sent_bps=net_sent_bps,
            volumes=self._volumes, top_cpu=self._top_cpu,
            top_mem=self._top_mem,
            uptime_s=time.time() - psutil.boot_time(),
            ctx_per_s=ctx_per_s, intr_per_s=intr_per_s,
            syscalls_per_s=syscalls_per_s,
            proc_count=self._proc_count, battery=battery,
            self_cpu=self_cpu, self_rss=self_rss,
        )

    @staticmethod
    def _scan_volumes():
        volumes = []
        try:
            parts = psutil.disk_partitions(all=False)
        except OSError:
            parts = []
        for part in parts:
            if "cdrom" in part.opts or not part.fstype:
                continue
            try:
                u = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            volumes.append(VolumeInfo(part.mountpoint, u.percent, u.used, u.total))
        return volumes

    def _scan_processes(self):
        procs = []
        for p in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                if p.info["pid"] == 0:      # System Idle Process
                    continue
                c = p.cpu_percent(None) / self._ncpu
                mem = p.info["memory_info"]
                procs.append(ProcInfo(p.info["pid"], p.info["name"] or "?",
                                      c, mem.rss if mem else 0))
            except psutil.Error:
                continue
        self._top_cpu = sorted(procs, key=lambda x: x.cpu,
                               reverse=True)[:config.TOP_PROCESS_COUNT]
        self._top_mem = sorted(procs, key=lambda x: x.rss,
                               reverse=True)[:config.TOP_PROCESS_COUNT]
        self._proc_count = len(procs) + 1

    def _sleep(self, seconds: float):
        end = time.monotonic() + seconds
        while not self._stop and time.monotonic() < end:
            time.sleep(0.1)
