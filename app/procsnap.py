"""Single-call process snapshot.

Windows can describe every process on the machine in one kernel
transition: `NtQuerySystemInformation(SystemProcessInformation)` fills a
buffer with the name, ids, thread and handle counts, memory, CPU times
and per-process I/O byte counters for all of them at once.

Getting the same data through psutil costs a syscall — often several —
per process per field. Measured on this 8-CPU / 268-process machine:

    this module, every field           2.4 ms
    process_iter(pid, name, memory)     38 ms
    the Processes tab's previous scan  553 ms

psutil's `num_threads()` is the pathological case: on Windows it takes
this same whole-machine snapshot once *per process*, so reading 268
integers cost 268 snapshots — 484 ms of that 553 ms. `oneshot()` does
not help; it does not cache that field.

Each consumer owns its own ProcessScanner. The reusable buffer and the
CPU-delta baseline are per-instance, so two threads scanning at
different cadences cannot corrupt one another — the failure the
Processes tab previously avoided by keeping a private psutil cache.

Everything here is read-only: one buffer, one kernel call, pure parsing.
"""
from __future__ import annotations

import ctypes
import logging
import os
import time
from ctypes import wintypes
from dataclasses import dataclass

log = logging.getLogger(__name__)

# The kernel wants ~463 KB for 268 processes (it also describes every
# thread). Start comfortably above that and grow only if told to.
INITIAL_BUFFER = 512 * 1024
MAX_BUFFER = 64 * 1024 * 1024

SystemProcessInformation = 5
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004

# Sanity value for the 64-bit layout; asserted by the smoke test so a
# silent struct-layout change cannot start producing plausible garbage.
EXPECTED_SIZE_X64 = 256

FILETIME_PER_S = 10_000_000          # 100 ns units
EPOCH_DELTA_S = 11_644_473_600       # 1601-01-01 -> 1970-01-01

# Process base priority -> the label Windows itself uses. Matches the
# names in optimizer.PRIORITY_CLASSES so the table reads consistently.
_PRIORITY_LABELS = {
    4: "Low", 6: "Below normal", 8: "Normal",
    10: "Above normal", 13: "High", 24: "Realtime",
}


class _UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", wintypes.USHORT),
                ("MaximumLength", wintypes.USHORT),
                ("Buffer", ctypes.c_void_p)]


class _SYSTEM_PROCESS_INFORMATION(ctypes.Structure):
    """ntdll's per-process record. Field order and types are fixed by the
    OS; ctypes derives the padding, so this is correct on 32- and 64-bit.
    Microsoft only ever appends to it, and NextEntryOffset makes appended
    fields harmless to ignore."""

    _fields_ = [
        ("NextEntryOffset", wintypes.ULONG),
        ("NumberOfThreads", wintypes.ULONG),
        ("WorkingSetPrivateSize", ctypes.c_longlong),
        ("HardFaultCount", wintypes.ULONG),
        ("NumberOfThreadsHighWatermark", wintypes.ULONG),
        ("CycleTime", ctypes.c_ulonglong),
        ("CreateTime", ctypes.c_longlong),
        ("UserTime", ctypes.c_longlong),
        ("KernelTime", ctypes.c_longlong),
        ("ImageName", _UNICODE_STRING),
        ("BasePriority", ctypes.c_long),
        ("UniqueProcessId", ctypes.c_void_p),
        ("InheritedFromUniqueProcessId", ctypes.c_void_p),
        ("HandleCount", wintypes.ULONG),
        ("SessionId", wintypes.ULONG),
        ("UniqueProcessKey", ctypes.c_size_t),
        ("PeakVirtualSize", ctypes.c_size_t),
        ("VirtualSize", ctypes.c_size_t),
        ("PageFaultCount", wintypes.ULONG),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivatePageCount", ctypes.c_size_t),
        ("ReadOperationCount", ctypes.c_longlong),
        ("WriteOperationCount", ctypes.c_longlong),
        ("OtherOperationCount", ctypes.c_longlong),
        ("ReadTransferCount", ctypes.c_longlong),
        ("WriteTransferCount", ctypes.c_longlong),
        ("OtherTransferCount", ctypes.c_longlong),
    ]


STRUCT_SIZE = ctypes.sizeof(_SYSTEM_PROCESS_INFORMATION)

_ntdll = ctypes.WinDLL("ntdll")
_ntdll.NtQuerySystemInformation.argtypes = (
    ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
    ctypes.POINTER(ctypes.c_ulong))
_ntdll.NtQuerySystemInformation.restype = ctypes.c_long


@dataclass(slots=True)
class ProcSample:
    """One process, as of one snapshot.

    `cpu` is the share of the whole machine (0-100), consistent with
    monitor.ProcInfo. `read_bps`/`write_bps` are per-second rates over
    the same interval; all of them are 0 on a scanner's first scan,
    which establishes the baseline.
    """

    pid: int
    ppid: int
    name: str
    session: int
    threads: int
    handles: int
    rss: int                 # working set, bytes
    private: int             # private commit — the real leak signal
    cpu: float               # % of the whole machine
    read_bps: float
    write_bps: float
    other_bps: float
    read_bytes: int          # cumulative, since process start
    write_bytes: int
    create_ts: float         # unix epoch seconds, 0 if unknown
    base_priority: int

    @property
    def priority_label(self) -> str:
        return _PRIORITY_LABELS.get(self.base_priority,
                                    str(self.base_priority))


class ProcessScanner:
    """Repeatable whole-machine process snapshot.

    Not thread-safe by design: give each thread its own instance rather
    than sharing one. That is cheaper than locking (the buffer is the
    only state) and it keeps two consumers' CPU baselines independent.
    """

    def __init__(self, ncpu: int | None = None):
        self._buf = ctypes.create_string_buffer(INITIAL_BUFFER)
        self._need = ctypes.c_ulong(0)
        # (pid, create_time) -> cumulative counters, so a recycled PID
        # starts a fresh baseline instead of inheriting a stranger's.
        self._prev: dict[tuple[int, int], tuple[int, int, int, int]] = {}
        self._prev_t: float | None = None
        self._ncpu = ncpu or os.cpu_count() or 1

    def scan(self) -> list[ProcSample]:
        """One kernel call, then pure parsing. Excludes PID 0 (Idle).

        Raises OSError if the kernel refuses the query — callers already
        treat a failed scan as "skip this cycle".
        """
        self._fill()
        now = time.monotonic()
        dt = (now - self._prev_t) if self._prev_t is not None else None
        self._prev_t = now
        # 100 ns CPU units available per wall-clock second, per core
        cpu_divisor = (dt * FILETIME_PER_S * self._ncpu) if dt else None

        rows: list[ProcSample] = []
        prev, fresh = self._prev, {}
        base, offset = ctypes.addressof(self._buf), 0
        while True:
            e = _SYSTEM_PROCESS_INFORMATION.from_address(base + offset)
            pid = e.UniqueProcessId or 0
            if pid:
                key = (pid, e.CreateTime)
                cpu_100ns = e.KernelTime + e.UserTime
                counters = (cpu_100ns, e.ReadTransferCount,
                            e.WriteTransferCount, e.OtherTransferCount)
                fresh[key] = counters
                was = prev.get(key)
                if was is not None and cpu_divisor:
                    cpu = max(0.0, (cpu_100ns - was[0]) / cpu_divisor) * 100.0
                    read_bps = max(0.0, (counters[1] - was[1]) / dt)
                    write_bps = max(0.0, (counters[2] - was[2]) / dt)
                    other_bps = max(0.0, (counters[3] - was[3]) / dt)
                else:
                    cpu = read_bps = write_bps = other_bps = 0.0
                rows.append(ProcSample(
                    pid=pid,
                    ppid=e.InheritedFromUniqueProcessId or 0,
                    name=self._name(e, pid),
                    session=e.SessionId,
                    threads=e.NumberOfThreads,
                    handles=e.HandleCount,
                    rss=e.WorkingSetSize,
                    private=e.PrivatePageCount,
                    cpu=cpu,
                    read_bps=read_bps, write_bps=write_bps,
                    other_bps=other_bps,
                    read_bytes=e.ReadTransferCount,
                    write_bytes=e.WriteTransferCount,
                    create_ts=(e.CreateTime / FILETIME_PER_S - EPOCH_DELTA_S
                               if e.CreateTime > 0 else 0.0),
                    base_priority=e.BasePriority,
                ))
            if e.NextEntryOffset == 0:
                break
            offset += e.NextEntryOffset
        self._prev = fresh          # drops exited processes automatically
        return rows

    # -- internals ------------------------------------------------------
    def _fill(self):
        """Run the query, growing the buffer only when the kernel says so."""
        while True:
            status = _ntdll.NtQuerySystemInformation(
                SystemProcessInformation, self._buf, len(self._buf),
                ctypes.byref(self._need))
            if status == 0:
                return
            if status & 0xFFFFFFFF != STATUS_INFO_LENGTH_MISMATCH:
                raise OSError(
                    f"NtQuerySystemInformation failed: "
                    f"0x{status & 0xFFFFFFFF:08X}")
            # Racing process churn can move the target between calls, so
            # take the kernel's figure with headroom rather than exactly.
            want = max(self._need.value * 2, len(self._buf) * 2)
            if want > MAX_BUFFER:
                raise OSError(f"process snapshot needs {want} bytes "
                              f"(over the {MAX_BUFFER} cap)")
            log.debug("Growing process-snapshot buffer to %d KB", want // 1024)
            self._buf = ctypes.create_string_buffer(want)

    @staticmethod
    def _name(entry, pid: int) -> str:
        if entry.ImageName.Buffer:
            try:
                return ctypes.wstring_at(entry.ImageName.Buffer,
                                         entry.ImageName.Length // 2)
            except (OSError, ValueError):
                pass
        return "System" if pid == 4 else "?"
