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

from PySide6.QtGui import QFont                          # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox   # noqa: E402

from app import config, diag, optimizer                  # noqa: E402
from app.analyzer import RULE_TITLES                     # noqa: E402
from app.monitor import MetricsSampler                   # noqa: E402
from app.monitor import ProcInfo, Snapshot, VolumeInfo   # noqa: E402
from app.ui import theme                                 # noqa: E402
from app.ui.main_window import MainWindow                # noqa: E402
from app.ui.tray import TrayIcon                         # noqa: E402

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

    # Waking from sleep is not a stall: nothing ran, so no samples arrived.
    # This used to fire a red status and a critical toast on every resume.
    stalls.clear()
    win._stalled = False
    win._last_watchdog_tick = time.monotonic() - 3600
    win._last_sample_mono = time.monotonic() - 3600
    win._check_heartbeat()
    assert not win._stalled, "resume from sleep misreported as a stall"
    assert not stalls, "resume from sleep pointlessly restarted the sampler"
    # A genuinely dead sampler has the opposite shape — ticks keep arriving
    # on time while the sample age climbs — and must still be caught.
    win._last_watchdog_tick = time.monotonic()
    win._last_sample_mono = time.monotonic() - 600
    win._check_heartbeat()
    assert win._stalled, "a dead sampler is no longer detected"
    assert stalls, "a dead sampler did not request a restart"
    win.on_sample(snap(t0 + 121 * 1.5))        # back to healthy
    assert not win._stalled
    print("resume from sleep distinguished from a dead sampler")

    # -- the one-call process collector (app/procsnap.py) --
    # Cross-checked against psutil on every run: the struct layout is
    # dictated by the OS, and a wrong layout would not crash, it would
    # quietly return plausible-looking garbage.
    import ctypes as _ct

    import psutil as _ps

    from app import procsnap

    if _ct.sizeof(_ct.c_void_p) == 8:
        assert procsnap.STRUCT_SIZE == procsnap.EXPECTED_SIZE_X64, \
            (f"SYSTEM_PROCESS_INFORMATION is {procsnap.STRUCT_SIZE} bytes, "
             f"expected {procsnap.EXPECTED_SIZE_X64} — layout drift")

    scanner = procsnap.ProcessScanner()
    scanner.scan()                             # establishes the baseline
    time.sleep(0.4)                            # let some CPU accrue
    t_scan = time.perf_counter()
    rows = scanner.scan()
    scan_ms = (time.perf_counter() - t_scan) * 1000.0
    assert len(rows) > 50, f"only {len(rows)} processes in the snapshot"
    assert all(r.pid != 0 for r in rows), "PID 0 (Idle) must be excluded"
    assert len({r.pid for r in rows}) == len(rows), "duplicate PIDs"

    # a regression to the old per-process path would be ~200x this
    assert scan_ms < 100.0, f"snapshot took {scan_ms:.0f} ms — fast path lost"

    by_pid = {r.pid: r for r in rows}
    ps_procs = {}
    for p in _ps.process_iter(["pid", "name", "num_threads"]):
        if p.info["pid"]:
            ps_procs[p.info["pid"]] = p.info

    # coverage: the snapshot must see essentially everything psutil sees
    both = set(by_pid) & set(ps_procs)
    assert len(both) > len(ps_procs) * 0.9, \
        f"snapshot covered only {len(both)} of {len(ps_procs)} processes"

    # names: psutil reports '' for a few protected processes, so only
    # compare where it actually has an answer
    named = [pid for pid in both if ps_procs[pid]["name"]]
    bad_names = [(pid, ps_procs[pid]["name"], by_pid[pid].name)
                 for pid in named
                 if ps_procs[pid]["name"] != by_pid[pid].name]
    assert len(bad_names) <= len(named) * 0.05, \
        f"names disagree with psutil: {bad_names[:5]}"

    # thread counts come from the same field Task Manager shows
    thr_bad = [(pid, ps_procs[pid]["num_threads"], by_pid[pid].threads)
               for pid in both
               if abs((ps_procs[pid]["num_threads"] or 0)
                      - by_pid[pid].threads) > 5]
    assert len(thr_bad) <= len(both) * 0.1, \
        f"thread counts disagree with psutil: {thr_bad[:5]}"

    # memory and handles, spot-checked where psutil can actually look
    spot = drift_bad = 0
    for pid in sorted(both):
        if spot >= 25:
            break
        try:
            p = _ps.Process(pid)
            ps_rss, ps_h = p.memory_info().rss, p.num_handles()
        except _ps.Error:
            continue
        spot += 1
        r = by_pid[pid]
        # both are live numbers sampled moments apart, so allow real drift
        if abs(ps_rss - r.rss) > max(ps_rss * 0.5, 16 << 20):
            drift_bad += 1
        if abs(ps_h - r.handles) > max(ps_h * 0.5, 64):
            drift_bad += 1
    assert spot >= 5, f"only {spot} processes were spot-checkable"
    assert drift_bad <= 2, \
        f"{drift_bad} memory/handle readings disagree with psutil"

    # derived rates must be physically possible
    assert all(0.0 <= r.cpu <= 100.0 for r in rows), \
        f"CPU out of range: {[r.cpu for r in rows if not 0 <= r.cpu <= 100][:3]}"
    assert sum(r.cpu for r in rows) <= 100.0 * 2, \
        f"total CPU {sum(r.cpu for r in rows):.0f}% exceeds the machine"
    assert all(r.read_bps >= 0 and r.write_bps >= 0 for r in rows)
    now_ts = time.time()
    assert all(0 <= r.create_ts <= now_ts + 60 for r in rows), \
        "implausible process start times"

    # cumulative I/O counters may only ever grow
    rows2 = scanner.scan()
    prev_io = {r.pid: (r.read_bytes, r.write_bytes, r.create_ts) for r in rows}
    for r in rows2:
        was = prev_io.get(r.pid)
        if was and was[2] == r.create_ts:
            assert r.read_bytes >= was[0] and r.write_bytes >= was[1], \
                f"I/O counters went backwards for {r.name} ({r.pid})"

    # the buffer must grow when the kernel says it is too small
    small = procsnap.ProcessScanner()
    small._buf = _ct.create_string_buffer(512)
    grown = small.scan()
    assert len(grown) > 50, "scan failed after buffer growth"
    assert len(small._buf) > 512, "buffer never grew"

    # two scanners must not share baselines (the old cross-thread bug)
    a, b = procsnap.ProcessScanner(), procsnap.ProcessScanner()
    a.scan(); b.scan()
    time.sleep(0.3)
    a.scan()
    assert b._prev_t is not None and a._prev_t != b._prev_t, \
        "scanners are sharing state"

    # Per-process I/O attribution has to be right, not merely present:
    # naming the process that is actually saturating the disk is the whole
    # reason this data is collected. Prove it with real writes.
    import tempfile
    probe = os.path.join(tempfile.gettempdir(), "procsnap_io_probe.bin")
    io_scan = procsnap.ProcessScanner()
    io_scan.scan()
    blob = b"\0" * (1 << 20)
    try:
        with open(probe, "wb") as fh:
            for _ in range(48):                # 48 MB, then force it out
                fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
        mine = {r.pid: r for r in io_scan.scan()}[os.getpid()]
    finally:
        if os.path.exists(probe):
            os.remove(probe)
    assert mine.write_bps > 5e6, \
        f"48 MB of writes attributed as only {mine.write_bps/1e6:.1f} MB/s"
    print(f"I/O attribution OK — our own 48 MB write showed up as "
          f"{mine.write_bps/1e6:.0f} MB/s against {mine.name}")

    busy = max(rows, key=lambda r: r.cpu)
    hot = max(rows, key=lambda r: r.write_bps)
    print(f"collector OK — {len(rows)} processes in {scan_ms:.2f} ms, "
          f"{len(both)} cross-checked vs psutil")
    print(f"  busiest CPU: {busy.name} {busy.cpu:.1f}% · "
          f"most writes: {hot.name} {hot.write_bps/1e6:.1f} MB/s · "
          f"handles peak: {max(r.handles for r in rows):,}")

    # -- tabs --
    assert win._tabs.count() == 5, f"expected 5 tabs, got {win._tabs.count()}"
    names = ["dashboard", "details", "processes", "optimize", "guide"]
    for idx, name in enumerate(names):
        win._tabs.setCurrentIndex(idx)
        app.processEvents()
        if idx == 1:                           # Details refreshes on sample
            win.on_sample(snap(t0 + 122 * 1.5))
            app.processEvents()
        if idx == 2:                           # Processes scans for real
            # Generous: a full process scan is slow on a loaded machine,
            # and this test is expected to run on exactly those.
            started = time.time()
            while (time.time() - started < 60
                   and win._processes._table.rowCount() == 0):
                app.processEvents()
                time.sleep(0.1)
            rows = win._processes._table.rowCount()
            assert rows > 10, (f"process scan returned only {rows} rows "
                               f"after {time.time() - started:.0f}s")
            print(f"process scan found {rows} processes "
                  f"in {time.time() - started:.1f}s")
        win.grab().save(os.path.join(HERE, f"tab_{name}.png"))

    print("tabs OK — screenshots written to", HERE)

    # -- the in-app manual --
    # The installed build ships no README, so the Guide has to carry the
    # explanation. Its numbers are generated from config so they cannot
    # drift; assert exactly that rather than the prose around them.
    guide = win._guide
    assert guide._rendered, "guide did not render when its tab was shown"
    html = guide._view.toPlainText()
    assert len(html) > 3000, f"guide suspiciously short ({len(html)} chars)"

    for rule in config.RULES:
        assert RULE_TITLES[rule.rule_id] in html, \
            f"guide never mentions the {rule.rule_id} alert"
        assert f"{rule.warn_at:g}{rule.unit}" in html, \
            f"guide omits the warning threshold for {rule.rule_id}"
        assert f"{rule.critical_at:g}{rule.unit}" in html, \
            f"guide omits the critical threshold for {rule.rule_id}"
    for interval in (config.SAMPLE_INTERVAL_S, config.SAMPLE_INTERVAL_MINI_S,
                     config.SAMPLE_INTERVAL_BG_S):
        assert f"every {interval:g} s" in html, \
            f"guide omits the {interval}s sampling cadence"
    assert f"{config.SELF_CPU_BUDGET:g}%" in html, \
        "guide omits the app's own CPU budget"
    assert str(optimizer.TEMP_MIN_AGE_H) in html, \
        "guide omits the temp-file age cutoff"
    assert str(config.DISK_FULL_WARN) in html, "guide omits the disk-full mark"

    # every Optimize action the user can click must be documented
    documented = 0
    for attr in dir(win._optimize):
        if not attr.startswith("btn_"):
            continue
        label = getattr(win._optimize, attr).text().split("  ·")[0].strip()
        # buttons carry an "(admin)" hint the manual states in prose
        label = label.replace("  (admin)", "")
        assert label.replace("&&", "&") in html.replace("&&", "&"), \
            f"Optimize button {label!r} is not explained in the guide"
        documented += 1
    assert documented >= 9, f"only checked {documented} action buttons"
    print(f"guide OK — {len(html):,} chars, thresholds generated from "
          f"config, {documented} actions documented")

    # About must not raise, and must report the real environment
    from app.ui.guide_tab import show_about
    from app.version import __version__ as _ver
    _boxes = []
    _real_exec = QMessageBox.exec

    def _capture(self):                      # don't block the test on modals
        _boxes.append(self)
        return QMessageBox.Close

    QMessageBox.exec = _capture
    try:
        show_about(None)
        assert _boxes, "About dialog never opened"
        text = _boxes[-1].text() + _boxes[-1].informativeText()
        assert _ver in text, "About omits the version"
        assert "MIT" in text, "About omits the licence"
        assert diag.LOG_DIR in text, "About omits the log location"

        # first-run welcome: shows once, then never again
        win._settings.setValue("intro/shown", False)
        _boxes.clear()
        win.maybe_show_welcome()
        assert _boxes, "first-run welcome never appeared"
        welcome = _boxes[-1].informativeText()
        assert "taskbar" in welcome, "welcome does not say where the app went"
        assert "automatically" in welcome, \
            "welcome does not promise it changes nothing on its own"
        _boxes.clear()
        win.maybe_show_welcome()
        assert not _boxes, "welcome reappeared on a later launch"
    finally:
        QMessageBox.exec = _real_exec
    print("about + first-run welcome OK (shown once, then suppressed)")

    # -- manual update check --
    # An update check is only worth having if the version it reports is
    # right, so first pin down that nothing has drifted out of sync.
    from app import updates
    from app.ui.guide_tab import show_update_result

    iss = open(os.path.join(os.path.dirname(HERE), "packaging",
                            "installer.iss"), encoding="utf-8").read()
    vinfo = open(os.path.join(os.path.dirname(HERE), "packaging",
                              "version_info.txt"), encoding="utf-8").read()
    assert f'#define AppVersion "{_ver}"' in iss, \
        f"installer.iss fallback version disagrees with {_ver}"
    parts = _ver.split(".")
    assert f"filevers=({', '.join(parts)}, 0)" in vinfo, \
        f"version_info.txt filevers disagrees with {_ver}"
    assert f'"{_ver}.0"' in vinfo, \
        f"version_info.txt File/ProductVersion disagrees with {_ver}"
    print(f"version {_ver} consistent across app, installer and exe metadata")

    # version ordering must be numeric, not lexical
    assert updates.is_newer("1.10.0", "1.9.0"), "1.10.0 must beat 1.9.0"
    assert not updates.is_newer("1.9.0", "1.10.0")
    assert updates.is_newer("2.0.0", "1.99.99")
    assert updates.is_newer("1.2.0", "1.2.0-rc1"), \
        "a final release must beat its own release candidate"
    assert not updates.is_newer("1.2.0-rc1", "1.2.0")
    assert not updates.is_newer("1.1.0", "1.1.0"), "equal is not newer"
    assert updates.is_newer("v1.2.0", "1.1.0"), "a 'v' prefix must be ignored"
    assert updates.parse_version("1.2") == updates.parse_version("1.2.0"), \
        "short and padded versions must compare equal"
    assert updates.parse_version("garbage")[0] == (0,), "junk must not crash"

    # each API outcome must map to a sane, non-crashing result
    seen_status = {}
    for label, payload, expect in (
            ("newer", {"tag_name": "v99.0.0", "body": "Lots of things.",
                       "published_at": "2026-09-01T00:00:00Z",
                       "html_url": "https://example.invalid/r/99",
                       "assets": [{"name": "s.exe",
                                   "browser_download_url": "https://x/s.exe"}]},
             updates.AVAILABLE),
            ("same", {"tag_name": f"v{_ver}"}, updates.CURRENT),
            ("older", {"tag_name": "v0.0.1"}, updates.AHEAD),
            ("untagged", {}, updates.NONE)):
        res = updates._interpret(payload)
        assert res.status == expect, \
            f"{label}: got {res.status}, expected {expect}"
        assert res.current == _ver
        seen_status[expect] = res
    assert seen_status[updates.AVAILABLE].latest == "99.0.0"
    assert seen_status[updates.AVAILABLE].assets == [("s.exe",
                                                      "https://x/s.exe")]

    # a real check must never raise, whatever the network does
    unreachable = updates.check(timeout=0.2,
                               url="https://127.0.0.1:9/nope")
    assert unreachable.status in (updates.UNREACHABLE, updates.BLOCKED,
                                  updates.ERROR), unreachable.status
    assert unreachable.detail, "a failed check must explain itself"
    assert not unreachable.ok
    print(f"offline check degrades gracefully: {unreachable.status}")

    # the result dialog must render every outcome, and only offer the
    # download page when there is somewhere useful to go
    _boxes.clear()
    QMessageBox.exec = _capture
    try:
        for status, res in seen_status.items():
            show_update_result(None, res)
            assert _boxes, f"no dialog for {status}"
            shown = _boxes[-1].text() + _boxes[-1].informativeText()
            assert _ver in shown, f"{status} dialog omits the running version"
        show_update_result(None, unreachable)
        assert unreachable.detail in _boxes[-1].informativeText(), \
            "failure dialog does not show the reason"
        # up-to-date is the one case with nothing to download
        assert not show_update_result(None, seen_status[updates.CURRENT]), \
            "offered a download page when already up to date"
    finally:
        QMessageBox.exec = _real_exec

    assert updates.install_kind() == "source", \
        "running from source should report as such in tests"
    assert updates.install_kind() in updates.UPGRADE_HINT, \
        "every install kind needs upgrade advice"

    # The button must be wired, must not block the GUI thread, and must
    # always hand the button back however the check turns out. This one
    # does hit the network, so the dialog stays stubbed out.
    QMessageBox.exec = _capture
    try:
        assert win._guide.btn_updates.isEnabled()
        win.check_for_updates()
        assert not win._guide.btn_updates.isEnabled(), \
            "button should disable while a check is in flight"
        assert win._guide.btn_updates.text() == "Checking…"
        win.check_for_updates()          # must not start a second check
        blocked_ms = 0.0
        deadline = time.time() + 40
        while win._update_in_flight and time.time() < deadline:
            spun = time.perf_counter()
            app.processEvents()
            blocked_ms = max(blocked_ms, (time.perf_counter() - spun) * 1000)
            time.sleep(0.02)
        assert not win._update_in_flight, "update check never finished"
        assert blocked_ms < 500, \
            f"GUI thread stalled {blocked_ms:.0f} ms — check is not async"
    finally:
        QMessageBox.exec = _real_exec
    assert win._guide.btn_updates.isEnabled(), "button left disabled"
    assert win._guide.btn_updates.text() == "Check for updates"
    log_text = win._optimize.log.toPlainText()
    assert "release" in log_text or "Update check" in log_text or \
        "Up to date" in log_text, "the check left nothing in the action log"
    print(f"update check ran off the GUI thread (max GUI stall "
          f"{blocked_ms:.0f} ms), button restored")

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

    # A taskbar that is briefly unavailable — auto-hidden, covered by a
    # fullscreen app, or mid-Explorer-restart — must not permanently
    # undock the strip. It used to: one such moment stranded it at a stale
    # position with no taskbar button and no alt-tab entry, i.e. invisible.
    from PySide6.QtCore import QPoint
    real_dock_position = taskbar_slot.dock_position
    mini.show()
    mini.set_docked(True, announce=False)
    assert mini.is_docked(), "should start docked for this check"

    taskbar_slot.dock_position = lambda *_a, **_k: None   # taskbar vanishes
    mini.dock_to_taskbar()
    assert mini.is_docked(), \
        "a transient missing taskbar must not undock the strip"
    assert mini._dock_timer.isActive(), \
        "the retry timer must keep running so docking can recover"
    assert mini._is_on_screen(), \
        "with no slot the strip must still be somewhere reachable"
    mini._keep_docked()                       # must also tolerate it
    assert mini.is_docked(), "_keep_docked must not undock either"

    taskbar_slot.dock_position = lambda size, screen: QPoint(1234, 2000)
    mini._keep_docked()                       # taskbar comes back
    assert mini.pos() == QPoint(1234, 2000), \
        f"strip did not re-dock once a slot reappeared: {mini.pos()}"
    assert mini.is_docked()
    print("transient taskbar loss no longer strands the strip")

    # ...but an explicit undock is still respected
    mini.set_docked(False, announce=False)
    assert not mini.is_docked(), "explicit undock must still work"
    assert not mini._dock_timer.isActive(), \
        "undocked strip must not keep polling the shell"
    taskbar_slot.dock_position = real_dock_position
    mini.set_docked(True, announce=False)
    mini.hide()
    print("explicit undock still honoured (no shell polling while floating)")

    # -- proactive self-healing when the shell blocks us --
    from app.ui import shellguard
    from app.ui.shellguard import guard

    guard.restore()                              # start from a clean state
    assert not guard.degraded

    slow_calls = []

    def slow_shell():                            # pretend explorer is busy
        time.sleep((shellguard.SLOW_MS + 50) / 1000.0)
        slow_calls.append(1)

    for _ in range(shellguard.TRIP_AFTER):
        guard.call("test shell call", slow_shell)
    assert len(slow_calls) == shellguard.TRIP_AFTER
    assert guard.degraded, "guard did not degrade after repeated slow calls"
    assert "slowly" in guard.reason, f"unhelpful reason: {guard.reason!r}"
    print(f"shell guard degraded after {shellguard.TRIP_AFTER} slow calls")

    # the guard listener must marshal onto the GUI thread, never touch
    # widgets from the caller's thread (that crashed the app once)
    import threading as _th
    seen_thread = []
    guard.restore()
    win.shell_state_changed.connect(
        lambda *_: seen_thread.append(_th.current_thread().name))

    def trip_from_worker():
        guard.degrade("tripped from a worker thread")

    t = _th.Thread(target=trip_from_worker, name="worker")
    t.start()
    t.join()
    for _ in range(20):                          # let the queued slot run
        app.processEvents()
        time.sleep(0.02)
    assert seen_thread, "shell_state_changed never reached a slot"
    assert seen_thread[-1] == "MainThread", \
        f"slot ran on {seen_thread[-1]}, not the GUI thread"
    print("guard callback marshalled to the GUI thread")

    # degraded mode must be visible to the user and must stop the chatter
    log_text = win._optimize.log.toPlainText()
    assert "Reduced mode" in log_text, "degradation not reported to the user"
    assert any("reduced mode" in win._alerts.item(i).text().lower()
               for i in range(win._alerts.count())), \
        "no alert raised for reduced mode"
    from app.ui.tray import load_icon, status_icon
    icon_calls = []

    class FakeTray:
        def setIcon(self, icon): icon_calls.append("icon")
        def setToolTip(self, tip): icon_calls.append("tip")
        def showMessage(self, *a): icon_calls.append("toast")
        set_status = TrayIcon.set_status
        notify = TrayIcon.notify

    fake = FakeTray()
    guard.restore()                              # rate-limit test needs a
    icon_calls.clear()                           # healthy shell
    fake.set_status("good", "tooltip", load=37.0)
    assert icon_calls.count("icon") == 1, \
        f"expected one icon push, got {icon_calls}"
    icon_calls.clear()
    for _ in range(5):                           # rapid repeat calls
        fake.set_status("good", "tooltip", load=38.0)
    assert not icon_calls, f"tray was hammered while rate-limited: {icon_calls}"
    print("tray updates rate-limited (no push within the interval)")

    # while degraded the tray must be completely silent: a single
    # setToolTip was measured blocking the GUI thread for 13 seconds
    guard.degrade("test: verifying the tray goes silent")
    assert guard.degraded
    icon_calls.clear()
    fake._icon_key = None
    fake._tip_at = 0.0
    fake.set_status("critical", "tooltip", load=99.0)
    assert not icon_calls, f"tray written to while degraded: {icon_calls}"
    assert fake.notify("t", "b", "warning") is False, \
        "toast fired while the shell was known slow"
    assert "toast" not in icon_calls
    print("tray fully silent while degraded (no icon, tooltip or toast)")
    guard.restore()
    assert fake.notify("t", "b", "warning") is True, "toast never fires"
    assert fake.notify("t", "b", "critical") is False, \
        "toasts must be rate-limited, not fired back to back"
    print("toasts suppressed while degraded, and rate-limited otherwise")
    guard.degrade("re-arm for the remaining assertions")

    # docking must stand still rather than poll a slow shell
    mini_docked_before = mini.pos()
    mini._keep_docked()
    assert mini.pos() == mini_docked_before, \
        "dock polling should be skipped in degraded mode"

    # ... and recovery is automatic
    guard.restore()
    assert not guard.degraded
    assert "responsive again" in win._optimize.log.toPlainText()
    print("recovery restores full behaviour")

    # the freeze watchdog must trigger the same self-healing
    fired = []
    watch = __import__("app.diag", fromlist=["diag"]).FreezeWatch(
        lambda: time.monotonic(), on_repeated_freeze=fired.append,
        trip_after=2, trip_window_s=60)
    watch._note_freeze('File "app\\ui\\tray.py", line 1, in set_status',
                       __import__("logging").getLogger("test"))
    assert not fired, "tripped too early"
    watch._note_freeze('File "app\\ui\\tray.py", line 1, in set_status',
                       __import__("logging").getLogger("test"))
    assert fired, "watchdog did not trigger self-healing"
    assert "notification-area" in fired[0], f"vague diagnosis: {fired[0]}"
    print(f"freeze watchdog self-heals: {fired[0]}")

    # ...but being suspended is not a freeze. During a real freeze this
    # thread keeps running; only a suspend stops it too.
    assert watch._is_suspend_gap(watch.POLL_S * watch.SUSPEND_FACTOR + 1), \
        "a multi-minute scheduling gap must read as suspend"
    assert not watch._is_suspend_gap(watch.POLL_S), \
        "a normal poll must not read as suspend"
    print("freeze watchdog ignores suspend gaps")

    # -- a suppressed hint stays owed instead of being silently consumed --
    class HintTray:
        notify = TrayIcon.notify

        def __init__(self):
            self.toasts = []

        def showMessage(self, *a):
            self.toasts.append(a)

        def isVisible(self):
            return True

    hint_tray = HintTray()
    win.set_tray(hint_tray)
    win._hide_hint_shown = False
    hint_tray._notify_at = time.monotonic()      # inside the rate limit
    win.hide_to_tray()
    assert not hint_tray.toasts, "toast fired while rate-limited"
    assert not win._hide_hint_shown, \
        "one-shot hint was consumed even though nothing was shown"
    hint_tray._notify_at = 0.0                   # rate limit has passed
    win.hide_to_tray()
    assert hint_tray.toasts, "the owed hint was never delivered"
    assert win._hide_hint_shown, "hint not marked shown after delivery"
    win.set_tray(None)
    print("suppressed tray hint is retried, not lost")

    # -- a floating strip stranded off-screen must come back --
    mini.show()
    mini.set_docked(False, announce=False)
    mini.move(99000, 99000)                      # monitor went away
    mini._on_screens_changed()
    area = mini.screen().availableGeometry()
    assert mini.x() + mini.width() <= area.right() + 1, \
        f"strip still off-screen horizontally at {mini.pos()}"
    assert mini.y() + mini.height() <= area.bottom() + 1, \
        f"strip still off-screen vertically at {mini.pos()}"
    print(f"stranded strip recovered to {mini.x()},{mini.y()} "
          f"inside {area.width()}x{area.height()}")
    mini.hide()

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
