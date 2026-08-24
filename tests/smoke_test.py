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

    # -- mini mode --
    win._tabs.setCurrentIndex(0)
    win.enter_mini_mode()
    app.processEvents()
    assert win.in_mini_mode(), "mini mode did not activate"
    assert not win.isVisible(), "dashboard should hide in mini mode"
    win.on_sample(snap(t0 + 130 * 1.5, cpu=71.0))
    app.processEvents()
    mini = win._mini
    assert mini._metrics["cpu"].text() == "71%", \
        f"mini CPU not updated: {mini._metrics['cpu'].text()!r}"
    assert mini.width() < 520 and mini.height() <= 40, \
        f"mini window too big: {mini.width()}x{mini.height()}"
    for key, label in mini._metrics.items():     # nothing clipped
        need = label.fontMetrics().horizontalAdvance(label.text())
        assert label.width() >= need, \
            f"mini {key} clipped: {label.text()!r} needs {need}px, " \
            f"has {label.width()}px"
    mini.grab().save(os.path.join(HERE, "mini_mode.png"))
    print(f"mini mode OK — {mini.width()}x{mini.height()} px, "
          f"CPU {mini._metrics['cpu'].text()}")

    # charts must buffer while hidden, then redraw intact on restore
    assert win._charts_stale, "charts should be marked stale while hidden"
    buffered = len(win._chart_cpu._buffers[0])
    win.exit_mini_mode()
    app.processEvents()
    assert not win.in_mini_mode() and win.isVisible(), "restore failed"
    assert not win._charts_stale, "charts not redrawn on restore"
    assert len(win._chart_cpu._buffers[0]) == buffered, \
        "chart history lost while hidden"
    print(f"restore OK — {buffered} samples of history preserved")

    # -- three-tier throttling --
    from app import config
    from app.monitor import MetricsSampler
    sampler = MetricsSampler()
    assert sampler.interval() == config.SAMPLE_INTERVAL_S
    sampler.set_mode("mini")
    assert sampler.interval() == config.SAMPLE_INTERVAL_MINI_S
    sampler.set_mode("hidden")
    assert sampler.interval() == config.SAMPLE_INTERVAL_BG_S
    assert (config.SAMPLE_INTERVAL_S < config.SAMPLE_INTERVAL_MINI_S
            < config.SAMPLE_INTERVAL_BG_S), "cadence tiers out of order"
    assert config.PROC_SCAN_EVERY_BG > config.PROC_SCAN_EVERY

    modes = []
    win.view_mode_changed.connect(modes.append)
    win.enter_mini_mode()
    app.processEvents()
    assert modes and modes[-1] == "mini", f"expected mini, got {modes}"
    assert win._lag_timer.interval() == win.RESP_TIMER_BG_MS, \
        "responsiveness probe not slowed outside the dashboard"
    mini_stall = win._stall_after_s()
    win.hide_to_tray()
    app.processEvents()
    assert modes[-1] == "hidden", f"expected hidden, got {modes}"
    hidden_stall = win._stall_after_s()
    assert hidden_stall > mini_stall, "stall threshold must follow cadence"
    win.exit_mini_mode()
    app.processEvents()
    assert modes[-1] == "dashboard", f"expected dashboard, got {modes}"
    assert win._lag_timer.interval() == win.RESP_TIMER_MS
    print(f"throttling OK — sample {config.SAMPLE_INTERVAL_S}/"
          f"{config.SAMPLE_INTERVAL_MINI_S}/{config.SAMPLE_INTERVAL_BG_S} s, "
          f"stall {win._stall_after_s():.0f}/{mini_stall:.0f}/"
          f"{hidden_stall:.0f} s")

    # -- taskbar docking geometry --
    # The offscreen platform reports a virtual screen unrelated to the real
    # desktop, so the placement maths is checked against a stub screen that
    # matches the actual taskbar the Win32 calls just reported.
    from PySide6.QtCore import QRect
    from app.ui import taskbar_slot

    bar = taskbar_slot.taskbar_rect()
    cluster = taskbar_slot.tray_cluster_rect()
    print(f"taskbar {bar}, tray cluster {cluster}")
    assert bar is not None, "no taskbar found (Shell_TrayWnd missing?)"

    class StubScreen:
        """Stands in for the screen the taskbar actually lives on."""

        def __init__(self, rect, dpr=1.0):
            self._rect, self._dpr = rect, dpr

        def devicePixelRatio(self):
            return self._dpr

        def geometry(self):
            return self._rect

    # the real taskbar, at whatever scaling this desktop uses
    for dpr in (1.0, 1.5, 2.0):
        # a real taskbar scales with the ratio; keep it 48 logical px tall
        phys = QRect(0, int(2160 - 48 * dpr), int(3840), int(48 * dpr))
        taskbar_slot.taskbar_rect = lambda r=phys: r
        taskbar_slot.tray_cluster_rect = lambda r=phys: QRect(
            int(r.right() - 234 * dpr / 1.0), r.top(), int(234), r.height())
        screen = StubScreen(QRect(0, 0, int(3840 / dpr), int(2160 / dpr)), dpr)
        pos = taskbar_slot.dock_position(mini.size(), screen)
        assert pos is not None, f"no dock slot at dpr {dpr}"
        top, bottom = phys.top() / dpr, phys.bottom() / dpr
        assert top - 1 <= pos.y() and pos.y() + mini.height() <= bottom + 1, \
            f"dpr {dpr}: strip not inside the taskbar band ({pos.y()})"
        cl = taskbar_slot.tray_cluster_rect().left() / dpr
        assert pos.x() + mini.width() <= cl, \
            f"dpr {dpr}: strip overlaps the tray cluster ({pos.x()} vs {cl})"
        print(f"dock slot OK at dpr {dpr} — {pos.x()},{pos.y()}")

    # fallbacks: must decline rather than misplace the strip
    taskbar_slot.tray_cluster_rect = lambda: None
    cases = {
        "vertical taskbar": QRect(0, 0, 62, 1440),
        "auto-hidden": QRect(0, 2158, 3840, 48),
        "too short for the strip": QRect(0, 2140, 3840, 20),
    }
    for label, rect in cases.items():
        taskbar_slot.taskbar_rect = lambda r=rect: r
        screen = StubScreen(QRect(0, 0, 3840, 2160), 1.0)
        assert taskbar_slot.dock_position(mini.size(), screen) is None, \
            f"should have declined to dock: {label}"
        print(f"declines to dock: {label}")

    print(f"docking OK — strip {mini.width()}x{mini.height()} "
          f"fits a {bar.height()}px taskbar")

    # -- taskbar (notification area) load icon --
    from app.ui.tray import load_icon
    icon = load_icon("good", 71.0)
    assert not icon.isNull(), "tray load icon failed to render"
    icon.pixmap(64, 64).save(os.path.join(HERE, "tray_icon.png"))
    print("tray load icon OK")

    print("SMOKE TEST PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
