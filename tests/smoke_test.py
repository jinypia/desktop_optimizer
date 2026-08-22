"""Offscreen smoke test for the dashboard.

Builds the real window with no display, feeds it synthetic samples
(including a sustained memory-pressure episode), exercises every tab, and
asserts the alerting, self-watchdog and error guards behave. Screenshots of
each tab are written next to this file for eyeballing.

Run:
    .venv\\Scripts\\python tests\\smoke_test.py
"""
import os
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from PySide6.QtGui import QFont                       # noqa: E402
from PySide6.QtWidgets import QApplication            # noqa: E402

from app.monitor import ProcInfo, Snapshot, VolumeInfo   # noqa: E402
from app.ui import theme                                 # noqa: E402
from app.ui.main_window import MainWindow                # noqa: E402

GB = 1 << 30


def snap(ts, cpu=35.0, mem=62.0, disk=18.0):
    """A plausible Snapshot for a 8-thread / 16 GB machine."""
    return Snapshot(
        ts=ts, cpu=cpu, per_core=[cpu] * 8, freq_mhz=2400.0,
        mem_percent=mem, mem_used=int(mem / 100 * 17e9), mem_total=int(17e9),
        swap_percent=12.0,
        disk_busy=disk, disk_read_bps=12e6, disk_write_bps=3e6,
        net_recv_bps=450e3, net_sent_bps=80e3,
        volumes=[VolumeInfo("C:\\", 72.0, int(340e9), int(475e9))],
        top_cpu=[ProcInfo(100 + i, n, c, r) for i, (n, c, r) in enumerate([
            ("chrome.exe", 14.0, int(1.9e9)), ("Teams.exe", 9.0, int(1.1e9)),
            ("python.exe", 6.0, int(0.4e9)), ("explorer.exe", 3.0, int(0.3e9)),
            ("OUTLOOK.EXE", 2.0, int(0.6e9)), ("svchost.exe", 1.0, int(0.1e9)),
        ])],
        top_mem=[ProcInfo(100, "chrome.exe", 14.0, int(1.9e9)),
                 ProcInfo(101, "Teams.exe", 9.0, int(1.1e9)),
                 ProcInfo(104, "OUTLOOK.EXE", 2.0, int(0.6e9))],
        uptime_s=86400.0,
        ctx_per_s=42000.0, intr_per_s=18000.0, syscalls_per_s=95000.0,
        proc_count=312, battery=None,
        self_cpu=0.9, self_rss=180 * (1 << 20),
    )


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 9))
    app.setStyleSheet(theme.STYLESHEET)
    win = MainWindow()
    win.show()

    t0 = time.time() - 200
    for i in range(100):                       # normal, varying load
        cpu = 30 + 25 * ((i % 13) / 12) + (8 if i % 7 == 0 else 0)
        win.on_sample(snap(t0 + i * 1.5, cpu=cpu,
                           mem=55 + i * 0.1, disk=10 + (i % 9) * 4))
    for i in range(100, 120):                  # sustained memory pressure
        win.on_sample(snap(t0 + i * 1.5, cpu=45.0, mem=94.5, disk=25.0))
    app.processEvents()

    assert win._alerts.count() >= 1, "expected at least one alert"
    first = win._alerts.item(0).text()
    assert "memory" in first.lower(), f"unexpected first alert: {first!r}"
    status = win._analyzer.status()
    assert status in ("warning", "critical"), f"unexpected status: {status}"
    print("alert[0]:", first.splitlines()[0])
    print("status:", status)

    # -- self-health: stall detection, recovery, UI error guard --
    stalls = []
    win.sampler_stalled.connect(lambda: stalls.append(1))
    win._last_sample_mono = time.monotonic() - 600
    win._check_heartbeat()
    assert win._stalled, "watchdog did not flag the stall"
    assert stalls, "sampler_stalled signal not emitted"
    assert "stalled" in win._status_label.text().lower()

    win.on_sample(snap(t0 + 121 * 1.5))        # heartbeat returns
    assert not win._stalled, "recovery not detected"

    win.on_sample(None)                        # corrupt sample must not raise
    assert win._ui_error_notes == 1, "UI error guard did not record the failure"
    print("watchdog stall/recovery + UI error guard OK")

    # -- tabs --
    assert win._tabs.count() == 4, f"expected 4 tabs, got {win._tabs.count()}"
    names = ["dashboard", "details", "processes", "optimize"]
    for idx, name in enumerate(names):
        win._tabs.setCurrentIndex(idx)
        app.processEvents()
        if idx == 1:                           # Details refreshes on sample
            win.on_sample(snap(t0 + 122 * 1.5))
            app.processEvents()
        if idx == 2:                           # Processes scans for real
            deadline = time.time() + 15
            while (time.time() < deadline
                   and win._processes._table.rowCount() == 0):
                app.processEvents()
                time.sleep(0.1)
            rows = win._processes._table.rowCount()
            assert rows > 10, f"process scan returned only {rows} rows"
            print(f"process scan found {rows} processes")
        win.grab().save(os.path.join(HERE, f"tab_{name}.png"))

    print("tabs OK — screenshots written to", HERE)
    print("SMOKE TEST PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
