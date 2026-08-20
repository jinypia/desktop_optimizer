"""Background metrics sampling.

MetricsSampler runs on its own QThread and emits a Snapshot every
SAMPLE_INTERVAL_S seconds. All psutil access happens off the GUI thread.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import psutil
from PySide6.QtCore import QThread, Signal

from . import config


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


class MetricsSampler(QThread):
    sample = Signal(object)  # Snapshot

    def __init__(self, interval_s: float = config.SAMPLE_INTERVAL_S, parent=None):
        super().__init__(parent)
        self._interval = interval_s
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        ncpu = psutil.cpu_count(logical=True) or 1
        psutil.cpu_percent(None, percpu=True)          # prime system counter
        for p in psutil.process_iter():                # prime per-process counters
            try:
                p.cpu_percent(None)
            except psutil.Error:
                pass
        last_disk = psutil.disk_io_counters()
        last_net = psutil.net_io_counters()
        last_t = time.monotonic()

        while not self._stop:
            self._sleep(self._interval)
            if self._stop:
                break
            now = time.monotonic()
            dt = max(now - last_t, 1e-3)
            last_t = now

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
            if disk and last_disk:
                busy_ms = (disk.read_time - last_disk.read_time) + \
                          (disk.write_time - last_disk.write_time)
                disk_busy = min(100.0, max(0.0, busy_ms / (dt * 10.0)))
                disk_read_bps = max(0.0, (disk.read_bytes - last_disk.read_bytes) / dt)
                disk_write_bps = max(0.0, (disk.write_bytes - last_disk.write_bytes) / dt)
            else:
                disk_busy = disk_read_bps = disk_write_bps = 0.0
            last_disk = disk

            net = psutil.net_io_counters()
            if net and last_net:
                net_recv_bps = max(0.0, (net.bytes_recv - last_net.bytes_recv) / dt)
                net_sent_bps = max(0.0, (net.bytes_sent - last_net.bytes_sent) / dt)
            else:
                net_recv_bps = net_sent_bps = 0.0
            last_net = net

            volumes = []
            for part in psutil.disk_partitions(all=False):
                if "cdrom" in part.opts or not part.fstype:
                    continue
                try:
                    u = psutil.disk_usage(part.mountpoint)
                except OSError:
                    continue
                volumes.append(VolumeInfo(part.mountpoint, u.percent, u.used, u.total))

            procs = []
            for p in psutil.process_iter(["pid", "name", "memory_info"]):
                try:
                    if p.info["pid"] == 0:      # System Idle Process
                        continue
                    c = p.cpu_percent(None) / ncpu
                    mem = p.info["memory_info"]
                    procs.append(ProcInfo(p.info["pid"], p.info["name"] or "?",
                                          c, mem.rss if mem else 0))
                except psutil.Error:
                    continue
            top_cpu = sorted(procs, key=lambda x: x.cpu, reverse=True)[:config.TOP_PROCESS_COUNT]
            top_mem = sorted(procs, key=lambda x: x.rss, reverse=True)[:config.TOP_PROCESS_COUNT]

            self.sample.emit(Snapshot(
                ts=time.time(), cpu=cpu, per_core=per_core, freq_mhz=freq_mhz,
                mem_percent=vm.percent, mem_used=vm.used, mem_total=vm.total,
                swap_percent=sw.percent,
                disk_busy=disk_busy, disk_read_bps=disk_read_bps,
                disk_write_bps=disk_write_bps,
                net_recv_bps=net_recv_bps, net_sent_bps=net_sent_bps,
                volumes=volumes, top_cpu=top_cpu, top_mem=top_mem,
                uptime_s=time.time() - psutil.boot_time(),
            ))

    def _sleep(self, seconds: float):
        end = time.monotonic() + seconds
        while not self._stop and time.monotonic() < end:
            time.sleep(0.1)
