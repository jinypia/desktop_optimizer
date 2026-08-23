# Desktop Optimizer

A lightweight Windows desktop app that monitors system performance in real
time, alerts you when it degrades, and offers safe one-click cleanup
actions. Built with Python, PySide6 (Qt), pyqtgraph, and psutil.

The app **never optimizes automatically** — it detects and recommends;
every action runs only when you click it.

---

## Requirements

- Windows 10 or 11
- Python 3.10+ (3.13 tested) — **prefer the [python.org](https://www.python.org/downloads/)
  installer over Microsoft Store Python.** The Store version sandboxes file
  writes (MSIX virtualization), which can make "Clear temp files" silently
  ineffective and hides the log file. The app detects Store Python and
  warns at startup, but full functionality needs the python.org build.
- ~500 MB disk space for the virtual environment (PySide6/Qt)

No administrator rights are needed for normal use. Cleanup actions work on
your own user's files and processes; system-protected items are skipped
automatically.

## Installation

### Option A — installer (recommended)

Download `DesktopOptimizer-1.0.0-setup.exe` from the
[releases page](https://github.com/jinypia/desktop_optimizer/releases) and
run it. Notes:

- **No administrator rights needed.** It installs per-user into
  `%LOCALAPPDATA%\Programs\Desktop Optimizer`.
- Creates a Start Menu entry, an optional desktop shortcut, and an
  optional "start when I sign in" entry (unchecked by default).
- Uninstall like any other app: Settings → Apps → Desktop Optimizer, or
  the Start Menu uninstall entry.
- Python does **not** need to be installed — the interpreter and Qt are
  bundled.
- A portable ZIP is also published: unzip anywhere and run
  `DesktopOptimizer.exe`, no installation at all.

Windows SmartScreen may warn on first run because the executable is not
code-signed (signing requires a paid certificate). Choose *More info →
Run anyway*, or unblock the file via its Properties dialog.

### Option B — run from source

Open PowerShell in the project folder and run:

```powershell
# 1. Create an isolated environment (do NOT install globally —
#    Microsoft Store Python breaks on PySide6's long paths)
python -m venv .venv

# 2. Install dependencies
.venv\Scripts\python -m pip install -r requirements.txt
```

**Behind a corporate proxy?** If `pip install` fails with connection-reset
errors, register the trusted hosts once, then retry step 2:

```powershell
pip config set global.trusted-host "pypi.org files.pythonhosted.org"
```

## Running the app

Either:

- **Double-click `run.bat`** — starts the app with no console window, or
- run from PowerShell: `.venv\Scripts\python main.py`

The dashboard window opens and a tray icon appears. **Closing the window
does not exit the app** — it keeps monitoring from the tray. To exit
completely, right-click the tray icon and choose **Exit**.

## Using the program

The window has four tabs on the left and an always-visible health panel on
the right (status, alerts with recommendations, top CPU processes).

### Dashboard tab

- **Stat cards (top row)** — current CPU % (with peak core and top
  process), memory (used/free and top consumer), disk activity (rates and
  fullest volume), and **Responsiveness**: the measured UI event-loop
  delay in milliseconds — under ~80 ms is smooth.
- **Vitals strip** — network down/up, process count, context switches/s,
  syscalls/s, pagefile use, uptime, and **the app's own cost** (CPU % +
  RAM), so you can always see what the monitor itself consumes.
- **Charts** — the last ~6 minutes of CPU, memory, disk-busy, and network
  history. Hover over any chart for an exact value readout at that point
  in time.

### The monitor's own overhead

Monitoring must not become its own performance problem, so the app is
built to get out of the way — and to prove it, the vitals strip always
shows its own live cost (`app cost: 0.4% CPU · 62 MB`). If it ever
averages more than 5% CPU over a minute, it raises a warning alert
against itself.

**While you're looking at it** (dashboard on screen): samples every
1.5 s, all-process scan every other sample, responsiveness probed 4×/s.
The sampler thread runs at low OS priority; volume usage and CPU
frequency are polled every 10th cycle; the Details and Processes tabs
refresh only while their tab is open.

**While you're not** (mini mode, tray, or just minimised) it throttles
itself hard:

| | Dashboard open | Hidden / minimised |
|---|---|---|
| Sample interval | 1.5 s | 4 s |
| All-process scan | every 2nd sample | every 8th sample |
| Responsiveness probe | 250 ms (precise) | 1 s (coarse) |
| Process priority | normal | below normal |
| Charts, cards, vitals strip | drawn | skipped (history still buffered) |
| Working set | resident | released back to Windows on hide |

Alerts, logging and the stall watchdog keep working throughout — only the
cost of *displaying* things is dropped. The stall threshold scales with
the cadence in use, so the slower background rate is never mistaken for a
freeze. Tune any of it in `app/config.py`.

Measure it yourself any time:

```powershell
.venv\Scripts\python tests\measure_overhead.py 80
```

### Details tab (professional view)

- **System** — hostname, user, OS build, CPU model, core/thread count,
  RAM, boot time, uptime.
- **CPU per core** — live utilization bar per logical core.
- **Kernel activity** — context switches/s, system calls/s, interrupts/s,
  process count, UI latency.
- **Memory** — in use / available / pagefile (swap) / battery state.
- **Volumes & Network** — per-drive capacity and per-interface IPv4,
  live up/down rates, and total transferred.

Heavy queries only run while this tab is open.

### Processes tab

The full process list — name, PID, user, CPU %, memory, threads, priority,
start time — refreshed every 3 s while visible. Click a column to sort,
type in the filter box to search. Select a row, then:

- **End process** / **End process tree** — terminates the app (with
  confirmation; children included for "tree").
- **Set priority** — choose Low → High and apply. Access-denied means the
  target is a system/elevated process; use *Restart as administrator*.

### Alerts and recommendations

The status line above the alerts list shows overall health:

| Status | Meaning |
|---|---|
| ● System healthy | all metrics normal |
| ▲ Performance degraded | a metric has stayed above its warning level |
| ■ Severe degradation | a metric is critically high |

When a metric stays high for a sustained period (not just a momentary
spike), an alert appears listing what happened, which processes are
responsible, and what to do about it. You also get a Windows toast
notification from the tray, so you don't need the window open. Each
degradation episode produces exactly one alert; a recovery entry is added
when the metric returns to normal.

### Optimize tab — one-click actions

All actions are manual — click a button, and the result is reported in the
log at the bottom. **Run all safe cleanups** chains the starred ones.

| Action | What it does |
|---|---|
| **Clear temp files** ★ | Deletes temp files untouched for 24+ hours (in-use/recent files skipped). Button shows the reclaimable size. |
| **Empty Recycle Bin** ★ | Empties the bin; the button shows its current size. |
| **Clear browser caches** ★ | Chrome / Edge / Firefox HTTP caches. Close browsers first for a full clean. |
| **Clear thumbnail cache** | Explorer thumbnail/icon caches — fixes stale thumbnails. Locked files need an Explorer restart first. |
| **Clear crash dumps & error reports** ★ | Old crash dumps and Windows Error Reporting queues from your profile. |
| **Trim process memory** ★ | Asks Windows to trim process working sets. Safe — pages return on demand. |
| **Purge standby memory** | Drops cached standby pages so they become immediately free. Requires administrator. |
| **Flush DNS cache** ★ | Clears the DNS resolver cache (helps after network/VPN changes). |
| **Switch power plan** | Toggles Balanced ⇄ High performance (some modern-standby laptops hide High performance). |
| **Restart graphics driver** | Sends Win+Ctrl+Shift+B — fixes display glitches; the screen flashes briefly. |
| **Restart Explorer** | Restarts the Windows shell (fixes a frozen taskbar). Asks for confirmation. |
| **Restart as administrator…** | Relaunches the app elevated (UAC prompt) to enable admin-only actions and control of elevated processes. |

### Mini mode — compact status above the taskbar

Press **Ctrl+M**, click **▭ Mini mode** (top-right of the tab bar), or pick
*Mini mode* from the tray menu. The dashboard collapses to a single strip
about 490×34 px showing status, CPU, memory, disk, network and
responsiveness:

```
● CPU 71%  MEM 62%  DSK 18%  NET 439.5 KB/s  RESP 495 ms   ▣
```

- Always on top, so it stays visible over other windows.
- **Drag it anywhere** — park it above the taskbar. The position is
  remembered between sessions.
- **Double-click** it, press Ctrl+M again, click **▣**, or use the tray's
  *Open dashboard* to return to the full view.
- Monitoring never pauses, and chart history keeps accumulating while
  hidden, so the graphs are unbroken when you come back — but the charts
  are not *redrawn* while nobody can see them, so mini mode is cheaper
  than the full dashboard.

### Taskbar icon (notification area)

The tray icon is a live readout: it shows the **current CPU load as a
number** with a fill bar underneath, colored by overall health (green /
amber / red). Status is visible at a glance with no window open at all.

- Hover it for a one-line CPU/memory summary.
- Click it to reopen the dashboard.
- Right-click for: **Open dashboard**, **Mini mode**, **Quick clean**
  (temp files + DNS), **Open log folder**, and **Exit**.

The icon is repainted only when the displayed number actually changes
(quantised to 2%), keeping it effectively free.

## Tuning thresholds

Alert rules live in `app/config.py`. Each rule defines the warning and
critical levels, how long the condition must be sustained before alerting,
and the hysteresis level below which it re-arms:

```python
Rule("cpu_high", "cpu", warn_at=85, critical_at=96, clear_below=70, sustain_s=20)
```

Also in `app/config.py`: sampling cadence (`SAMPLE_INTERVAL_S`) and chart
history length (`HISTORY_SAMPLES`). The temp-file age cutoff is
`TEMP_MIN_AGE_H` in `app/optimizer.py`.

## Building the installer

One command produces the installer and the portable ZIP into
`dist\installer\`:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

Prerequisites (one-time):

```powershell
# build tools into your venv (python.org Python, NOT the Store build)
.venv\Scripts\python -m pip install pyinstaller pillow

# Inno Setup 6, portable, no admin required
# download innosetup-6.x.x.exe from https://jrsoftware.org/isdl.php then:
.\innosetup-6.7.3.exe /PORTABLE=1 /VERYSILENT /DIR=%LOCALAPPDATA%\InnoSetup6
```

The script generates the icon, runs PyInstaller (windowed, onedir), builds
the Inno Setup installer, and zips the portable bundle. Override paths with
`-Python`, `-Iscc`, or `-WorkRoot`; skip icon regeneration with `-SkipIcon`.

Build work is staged on a local disk by default (`-WorkRoot`) because this
project often lives on a network share, where writing ~150 MB of Qt
binaries is slow.

To bump the version, edit `app/version.py`, then the matching values in
`packaging/version_info.txt` and `packaging/installer.iss`.

## Project layout

```
main.py                 entry point
run.bat                 launcher (venv + no console window)
assets/app.ico          application icon (generated from the tray artwork)
packaging/
  build.ps1             one-command build: exe + installer + portable zip
  desktop_optimizer.spec  PyInstaller spec (windowed onedir, Qt trimmed)
  installer.iss         Inno Setup script (per-user, no admin)
  make_icon.py          renders assets/app.ico
  version_info.txt      Windows version resource for the exe
app/
  config.py             sampling cadence + alert rules
  version.py            app name/version (single source of truth)
  diag.py               self-diagnostics: log file + exception hooks
  monitor.py            metrics sampler (QThread + psutil)
  analyzer.py           degradation detection -> alerts + recommendations
  optimizer.py          one-click cleanup actions (user-triggered only)
  util.py               formatting helpers
  ui/
    theme.py            dark theme tokens (accessibility-validated palette)
    widgets.py          metric cards + live charts with hover readout
    main_window.py      window shell: tabs + health side panel
    details_tab.py      professional detail view
    process_tab.py      full process list + controls
    optimize_tab.py     one-click action catalog + log
    workers.py          thread-pool worker plumbing
    tray.py             tray icon, menu, notifications
```

## Troubleshooting & self-diagnostics

The app monitors its own health too:

- **Log file** — everything (errors, crashes, action results, Qt warnings)
  is written to `logs\app.log` inside the app folder. Open it via the tray
  menu → **Open log folder**. Check this first when something looks wrong.
- **Stall watchdog** — if metric collection stops (charts frozen), the app
  detects the missing heartbeat within ~15 s, shows *"Monitoring stalled —
  restarting sampler"* in the status line, raises a toast, and restarts
  the collector automatically. If this repeats, the log contains the full
  traceback of what killed it.
- **Console mode** — run `.venv\Scripts\python main.py` from PowerShell to
  see live log output while reproducing a problem.
- **Single instance** — launching the app twice shows a notice instead of
  a second (stale-looking) window. If you ever see a frozen window, make
  sure you're not looking at an old copy: exit via the tray icon and
  relaunch.

- **Freeze forensics** — a watchdog thread monitors the GUI thread; if the
  window is unresponsive for 2+ seconds, the log records the exact stack
  it was stuck in and the total freeze duration after recovery.

Other common issues:

- **Window occasionally freezes ("no response")** — if the app runs from a
  network share, Windows pages its Qt libraries over the network, and any
  share hiccup freezes the app. `run.bat` therefore prefers a local-disk
  venv at `%LOCALAPPDATA%\DesktopOptimizer\venv` when one exists — create
  it with:
  `python -m venv %LOCALAPPDATA%\DesktopOptimizer\venv` then
  `%LOCALAPPDATA%\DesktopOptimizer\venv\Scripts\pip install -r requirements.txt`.
  Log writes are queued to a dedicated thread, so a slow share never
  blocks the UI. After any freeze, check `logs\app.log` for the recorded
  stack trace.
- **App won't start / import errors** — make sure you're running the venv
  interpreter (`.venv\Scripts\python main.py`), not the global one.
- **`pip install` connection resets** — see the trusted-host note under
  Installation.
- **Slow startup** — if the project sits on a network drive, first launch
  loads Qt DLLs over the network; moving the `.venv` to a local disk fixes
  it.
- **No toast notifications** — check Windows Settings → System →
  Notifications isn't suppressing them (Focus Assist / Do Not Disturb).

## License

Desktop Optimizer is open source under the **MIT License** — see
[`LICENSE`](LICENSE). You may use, modify and redistribute it, including
commercially, as long as the copyright notice is kept.

It bundles third-party open-source components, most notably **Qt for Python
(PySide6) under the LGPL v3**. If you redistribute the built installer or
portable ZIP, keep [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md) with
it — that file lists every dependency's license and what the LGPL requires
of you. Contributions are accepted under the same MIT terms.
