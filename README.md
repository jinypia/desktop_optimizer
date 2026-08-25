# Desktop Optimizer

A lightweight Windows performance monitor. It watches CPU, memory, disk,
network and responsiveness continuously, tells you when the machine
starts to degrade **and which process is responsible**, and gives you
safe one-click ways to get room back — without a reboot.

Built with Python, PySide6 (Qt), pyqtgraph and psutil.

## What it is for

Windows rarely tells you *why* it got slow. Task Manager shows you a
moment; it does not watch, it does not remember, and it does not say
"this has been bad for the last twenty seconds, and here is the process
doing it." That is the gap this fills:

- **It watches continuously** and only raises an alert when a problem is
  *sustained*, so you get one alert per episode instead of noise on every
  spike.
- **It names the culprit.** Every alert arrives with the specific
  processes responsible and concrete next steps.
- **It measures what you actually feel.** Alongside CPU and memory it
  tracks how late its own timer runs — if Windows cannot service a timer
  on time, it is not servicing your clicks either.
- **It offers a way out that is not a reboot** — eleven one-click
  actions, each one reporting exactly what it did.

## Two promises

**Nothing happens on its own.** The app detects and recommends; every
cleanup action runs only when you click it. Nothing is scheduled, and
nothing is changed behind your back.

**It refuses to become the problem.** A monitor that costs you
performance defeats itself, so this one throttles hard the moment you
stop looking at it, reports its own CPU and memory cost in the UI, and
raises a warning *against itself* if it ever exceeds its budget. Reading
every process on the machine takes one kernel call and about 2 ms; a
fully hidden app costs roughly 0.01% of an 8-core CPU. See
[The monitor's own overhead](#the-monitors-own-overhead).

## At a glance

| | |
|---|---|
| **Starts as** | a compact strip docked in your taskbar — double-click for the dashboard |
| **Dashboard** | four metric cards, a vitals strip, and ~6 minutes of live charts |
| **Details** | per-core CPU, kernel activity, every volume, every network interface |
| **Processes** | full list with owner, threads and priority — end, end tree, or reprioritise |
| **Optimize** | eleven cleanup and recovery actions, with an action log |
| **Guide** | the whole manual, in the app |
| **Alerts on** | sustained high CPU, memory pressure, disk saturation, UI lag, full drives |
| **Self-defence** | pauses its own shell traffic if Windows gets slow, and restores automatically |

There is a full manual **inside the app** on the Guide tab (and in the
tray menu), so the installed build explains itself without this file.
Release history is in [CHANGELOG.md](CHANGELOG.md).

---

## Installation

### What you need

| | Installer / portable ZIP | Running from source |
|---|---|---|
| Windows | 10 or 11, **64-bit** | 10 or 11, 64-bit |
| Python | **not needed** (bundled) | 3.10+ (3.13 tested) |
| Disk space | ~140 MB installed | ~500 MB (venv with Qt) |
| Administrator | **not needed** | not needed |

Cleanup actions work on your own user's files and processes; anything
system-protected is skipped automatically. A few extras (purge standby
memory, controlling elevated processes) need administrator rights, and the
app offers a *Restart as administrator* button for those — everything else
runs as a standard user.

### Install (recommended)

1. Go to the
   [**Releases** page](https://github.com/jinypia/desktop_optimizer/releases/latest).
2. Download **`DesktopOptimizer-<version>-setup.exe`**.
3. Run it. There is no UAC prompt — it installs just for you, into
   `%LOCALAPPDATA%\Programs\Desktop Optimizer`.
4. On the wizard's *Select Additional Tasks* page you can tick:
   - **Create a desktop shortcut** (on by default)
   - **Start Desktop Optimizer when I sign in** (off by default)
5. Finish. The app starts as a compact strip in your taskbar.

> **SmartScreen warning on first run.** The executable is not
> code-signed (a signing certificate is a paid, per-year expense), so
> Windows shows *"Windows protected your PC"*. Click **More info → Run
> anyway**. If you prefer, verify the download first with the SHA-256
> checksum published in the release notes:
> ```powershell
> Get-FileHash .\DesktopOptimizer-*-setup.exe -Algorithm SHA256
> ```

### Portable — no installation at all

Download **`DesktopOptimizer-portable.zip`**, unzip it anywhere (a USB
stick is fine), and run `DesktopOptimizer.exe`. Nothing is written outside
the folder except the log and settings described below.

### Updating

**To see whether there is a new version**, use **Check for updates** —
on the Guide tab, or in the tray menu. It compares this copy against the
latest published release and, if there is one, shows what changed and
offers to open the download page.

The check is **manual by design**. Nothing runs on a timer, at startup or
in the background, and the app never downloads or installs anything by
itself — consistent with the rest of the program. It makes one ordinary
HTTPS request to GitHub's release list and sends nothing about you or the
machine: no telemetry, no identifiers.

If your network blocks or inspects HTTPS, the check says so plainly and
points you at [the releases page](https://github.com/jinypia/desktop_optimizer/releases)
in a browser — which knows about the corporate proxy when the check does
not.

**To install the update:**

| Installed how | What to do |
|---|---|
| Setup program | Download the newer `…-setup.exe` and run it. It upgrades in place — closes the running copy, replaces the files, keeps your window and mini-strip preferences. No need to uninstall first. |
| Portable ZIP | Download the new `DesktopOptimizer-portable.zip`, exit the app, unpack over the existing folder. |
| From source | `git pull`, then `pip install -r requirements.txt` |

The app detects which of the three it is and tells you the right one.

### Uninstalling

**Settings → Apps → Installed apps → Desktop Optimizer → Uninstall**, or
the entry in the Start Menu folder. The uninstaller removes the program,
its shortcuts, its logs and its saved preferences — nothing is left
behind.

### Where the app keeps its files

| | Installed build | Running from source |
|---|---|---|
| Program | `%LOCALAPPDATA%\Programs\Desktop Optimizer` | your project folder |
| Log | `%LOCALAPPDATA%\DesktopOptimizer\logs\app.log` | `logs\app.log` in the project |
| Preferences | `HKCU\Software\jinypia\DesktopOptimizer` | same |

Reach the log at any time from the tray menu → **Open log folder**.

### Unattended / IT deployment

The installer is Inno Setup based, so the usual switches work:

```powershell
# The version is part of the filename, so resolve it once rather than
# hard-coding it into your deployment scripts.
$setup = (Get-ChildItem .\DesktopOptimizer-*-setup.exe)[0].FullName

# silent per-user install (no UI, no restart)
& $setup /VERYSILENT /NORESTART

# silent, and also create the sign-in shortcut
& $setup /VERYSILENT /NORESTART /TASKS="startupicon"

# install for all users instead (this one does need administrator)
& $setup /VERYSILENT /ALLUSERS

# silent uninstall
& "$env:LOCALAPPDATA\Programs\Desktop Optimizer\unins000.exe" /VERYSILENT
```

### Running from source instead

```powershell
# 1. Isolated environment. Use python.org Python, NOT the Microsoft Store
#    build: the Store version sandboxes file writes (MSIX virtualization),
#    which makes "Clear temp files" silently ineffective and hides the log.
#    The app warns at startup if it detects it.
python -m venv .venv

# 2. Dependencies
.venv\Scripts\python -m pip install -r requirements.txt

# 3. Run — either of these
.\run.bat                          # no console window
.venv\Scripts\python main.py       # with console output
```

**Behind a corporate proxy?** If `pip install` fails with connection-reset
errors, register the trusted hosts once, then retry step 2:

```powershell
pip config set global.trusted-host "pypi.org files.pythonhosted.org"
```

## First run

On the very first launch a short welcome dialog explains what the app is
and where it went — because it opens as a **compact strip docked in the
taskbar** (mini mode) rather than a window, plus an icon in the
notification area showing live CPU load.

- **Double-click the strip** for the full dashboard.
- **Closing the dashboard returns to the strip** — it does not exit.
- To exit completely, right-click the strip or the tray icon → **Exit**.

## Using the program

The window has five tabs on the left and an always-visible health panel on
the right (status, alerts with recommendations, top CPU processes).

Everything below is also available **inside the app** on the **Guide**
tab — reachable from the tray menu too — so the installed build needs no
documentation alongside it. The Guide generates its threshold and cadence
tables from `app/config.py`, so it cannot drift out of date.

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

**While you're not**, it throttles itself in three tiers — the mini strip
stays lively, a fully hidden app costs almost nothing:

| | Dashboard | Mini strip | Hidden (tray) |
|---|---|---|---|
| Sample interval | 1.5 s | 2.5 s | 4 s |
| All-process scan | every 2nd sample | every 8th | every 8th |
| Responsiveness probe | 250 ms (precise) | 1 s (coarse) | 1 s (coarse) |
| Process priority | normal | below normal | below normal |
| Charts, cards, vitals strip | drawn | skipped (history buffered) | skipped |
| Working set | resident | released on leaving the dashboard | released |

Alerts, logging and the stall watchdog keep working throughout — only the
cost of *displaying* things is dropped. The stall threshold scales with
the cadence in use, so the slower background rate is never mistaken for a
freeze. Tune any of it in `app/config.py`.

**How the process scan got cheap.** Walking every process is by far the
most expensive thing a monitor does, so `app/procsnap.py` asks Windows
once — `NtQuerySystemInformation(SystemProcessInformation)` returns the
name, ids, thread and handle counts, memory, CPU times and per-process
I/O byte counters for *every* process in a single kernel call. Going
through psutil instead means one or more syscalls per process per field.
Measured on an 8-CPU / 270-process machine:

| Scan | Before | After |
|---|---|---|
| Sampler's top-process scan | 38 ms | 5.0 ms |
| Processes tab refresh | 553 ms | 3.8 ms |
| Processes tab, while open | 2.3% of the machine | 0.016% |

The old Processes tab spent 484 ms of its 553 ms inside psutil's
`num_threads()`, which on Windows takes that same whole-machine snapshot
once *per process* — 270 snapshots to read 270 integers. (`oneshot()`
does not help; it does not cache that field.) Each consumer owns its own
scanner, so the reusable buffer and the CPU-delta baselines stay
per-thread and no locking is needed. The smoke test cross-checks every
field against psutil on each run, because a wrong struct layout would not
crash — it would quietly return plausible-looking numbers.

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

### Mini mode — the default view, docked in the taskbar

**The app starts in mini mode.** It opens as a compact strip (~490×34 px)
docked into the taskbar, immediately left of the tray icons and the clock,
showing status, CPU, memory, disk, network and responsiveness:

```
● CPU 26%  MEM 62%  DSK 0%  NET 10.3 KB/s  RESP 0 ms   ▣
```

- **Double-click** the strip (or press Ctrl+M, or click **▣**, or use the
  tray's *Open dashboard*) for the full dashboard. Closing the dashboard
  returns to the strip.
- **Right-click** it for: Open dashboard, Dock to taskbar (toggle), Hide to
  tray, Exit.
- **Drag it** anywhere to undock; it then floats always-on-top and its
  position is remembered. Re-dock from the right-click menu.
- It follows the taskbar: as tray icons come and go, or the bar moves
  between screens, the strip re-seats itself every few seconds.
- The app reopens in whichever view you used last, so if you prefer the
  dashboard, that preference sticks.

> **Why not *between* the icons and the clock?** On Windows 11 the tray
> icons and the date are drawn as a single block (`TrayNotifyWnd`) by the
> shell — the old per-element windows are gone, and Windows removed
> deskbands, so nothing can be inserted between them. The gap there is
> ~30 px wide. Immediately left of that block, inside the taskbar band, is
> the closest slot the OS allows; if the taskbar is vertical, auto-hidden,
> or too short, the strip floats just above it instead.

Monitoring never pauses in mini mode, and chart history keeps accumulating,
so the graphs are unbroken when you come back — the charts simply aren't
*redrawn* while nobody can see them.

### Taskbar icon (notification area)

The tray icon is a live readout: it shows the **current CPU load as a
number** with a fill bar underneath, colored by overall health (green /
amber / red). Status is visible at a glance with no window open at all.

- Hover it for a one-line CPU/memory summary.
- Click it to reopen the dashboard.
- Right-click for: **Open dashboard**, **Mini mode**, **Quick clean**
  (temp files + DNS), **Open log folder**, **User guide**, **Check for
  updates**, **About**, and **Exit**.

The icon is repainted only when the displayed number actually changes
(quantised to 2%), keeping it effectively free.

### Guide tab — the manual, in the app

The installed build ships no README next to it, so the whole manual lives
on the **Guide** tab: what each surface is for, how to read every number,
what each of the eleven Optimize actions actually does and how risky it
is, what triggers each alert, and how the app keeps out of your way. Also
reachable from the tray menu, with an **About** box reporting version,
build type, elevation state, Qt/Python versions and the log location, and
a **Check for updates** button (see [Updating](#updating)).

Its alert-threshold and throttling tables are generated from
`app/config.py` at render time rather than written by hand, so tuning a
threshold updates the documentation with it. The document is built on
first view and cached, so never opening the tab costs nothing.

## Tuning thresholds

Alert rules live in `app/config.py`. Each rule defines the warning and
critical levels, how long the condition must be sustained before alerting,
and the hysteresis level below which it re-arms:

```python
Rule("cpu_high", "cpu", warn_at=85, critical_at=96, clear_below=70, sustain_s=20)
```

Also in `app/config.py`: the three sampling cadences
(`SAMPLE_INTERVAL_S`, `SAMPLE_INTERVAL_MINI_S`, `SAMPLE_INTERVAL_BG_S`),
how often the expensive scans run (`PROC_SCAN_EVERY`,
`PROC_SCAN_EVERY_BG`, `VOLUME_SCAN_EVERY`, `FREQ_SCAN_EVERY`), and chart
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

### Releasing a new version

`app/version.py` is the single source of truth. The build reads it and
passes it to Inno Setup, so the installer can no longer disagree with what
the app reports about itself — which matters now that **Check for updates**
compares those numbers.

1. Bump `__version__` in `app/version.py`.
2. Update `packaging/version_info.txt` to match — both `filevers=(x, y, z, 0)`
   and the `"x.y.z.0"` File/ProductVersion strings. PyInstaller reads that
   file directly, so it cannot be injected; instead **the build fails** if
   it disagrees, rather than silently shipping wrong metadata.
3. Run `packaging\build.ps1`.
4. Add the release's entry to `CHANGELOG.md`.
5. Publish a GitHub release whose **tag is the version** (`v1.2.0` or
   `1.2.0` — the leading `v` is ignored), and attach the setup `.exe` and
   the portable `.zip`. Paste the changelog entry into the release body:
   that text is what **Check for updates** shows users. The in-app check
   reads the tag of the newest release, so an untagged or draft release
   will not be offered.

`tests\smoke_test.py` also asserts all three files agree, so drift is
caught before you build.

## Project layout

```
main.py                 entry point
run.bat                 launcher (venv + no console window)
LICENSE                 MIT
THIRD-PARTY-NOTICES.md  dependency licenses (incl. Qt/LGPL obligations)
requirements.txt        runtime dependencies
assets/app.ico          application icon (generated from the tray artwork)
tests/
  smoke_test.py         offscreen end-to-end test of the whole UI
  measure_overhead.py   measures the app's own CPU/RAM from outside
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
  procsnap.py           every process in one kernel call (see below)
  analyzer.py           degradation detection -> alerts + recommendations
  updates.py            manual release check (never automatic)
  optimizer.py          one-click cleanup actions (user-triggered only)
  util.py               formatting helpers
  ui/
    theme.py            dark theme tokens (accessibility-validated palette)
    widgets.py          metric cards + live charts with hover readout
    main_window.py      window shell: tabs + health side panel
    mini_window.py      compact taskbar-docked status strip
    taskbar_slot.py     finds the docking slot in the Windows taskbar
    details_tab.py      professional detail view
    process_tab.py      full process list + controls
    optimize_tab.py     one-click action catalog + log
    guide_tab.py        in-app manual + About (tables generated from config)
    workers.py          thread-pool worker plumbing
    tray.py             tray icon, menu, notifications
```

## Troubleshooting & self-diagnostics

The app monitors its own health too:

- **Log file** — everything (errors, crashes, action results, Qt warnings)
  is written to a rotating `app.log`: in
  `%LOCALAPPDATA%\DesktopOptimizer\logs\` for the installed build, or
  `logs\` in the project folder when running from source. Open it via the
  tray menu → **Open log folder**. Check this first when something looks
  wrong.
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
- **Sleep and resume are not faults** — waking from sleep, hibernation or
  modern standby means nothing was running, so of course no samples
  arrived. Both watchdogs recognise the difference: a suspended process
  shows a gap in the watchdog's *own* schedule, while a genuinely dead
  collector keeps ticking on time as the data goes stale. Resuming
  therefore rearms quietly (one line in the log) instead of flagging a
  stall or counting a freeze.
- **Reduced mode (automatic)** — the app does not just report problems, it
  gets out of them. Everything it sends to the Windows shell (tray icon,
  tooltip, toast notifications, taskbar position checks) is a *synchronous*
  cross-process call that blocks whenever `explorer.exe` is busy — on a
  loaded machine a single toast has been measured blocking for **10
  seconds**. So each of those calls is timed, and after a few slow ones —
  or after the watchdog sees repeated freezes — the app automatically:
  - drops the live numeric tray icon back to a static status icon,
  - stops toast notifications and taskbar position tracking,
  - tells you in the alerts panel and the log what it paused and why.

  Monitoring, alerting and cleanups keep running throughout; only the
  chatter that was freezing the window stops. It retries after five
  minutes and restores everything once Windows is responsive again. No
  action needed from you.

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
