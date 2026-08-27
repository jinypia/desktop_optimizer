# Changelog

All notable changes to Desktop Optimizer. The newest release is at the
top; paste the relevant section into the GitHub release body, since
**Check for updates** shows those notes to users.

Versions before 1.2.0 were never tagged, so their entries below are
reconstructed from the commit history.

## 1.2.1 — 2026-08-27

### Fixed

- **The Processes tab froze the window for over a minute at a time.**
  Opening it left the interface unresponsive, recovering only to freeze
  again a few seconds later. Columns were set to `ResizeToContents`, which
  makes Qt re-measure the entire column on every cell write — roughly a
  million font-metric computations per refresh at ~350 processes, against
  a 3-second refresh timer.

  | Table refresh | Before | After |
  |---|---|---|
  | 352 processes, real display | 68,122 ms | 74 ms |

  Columns are now auto-sized once after the first scan and stay
  user-resizable, and cells are updated in place instead of rebuilding
  ~2,800 of them every few seconds.

  Two notes for anyone reading the diff: reusing the cells *alone* made it
  measurably **worse** (87 s) while the resize mode was still on — the
  resize mode was the entire cost. And this bug is invisible to the
  offscreen platform the test suite uses, which measured the same code at
  49 ms against 68,122 ms on a real display, so the regression test
  asserts the column configuration rather than timing.

## 1.2.0 — 2026-08-25

### Faster

- **Every process is now read in one kernel call** instead of one or more
  syscalls per process per field (`app/procsnap.py`). Measured on an
  8-CPU / 270-process machine:

  | Scan | Before | After |
  |---|---|---|
  | Sampler's top-process scan | 38 ms | 5.0 ms |
  | Processes tab refresh | 553 ms | 3.8 ms |
  | Processes tab, while open | 2.3% of the machine | 0.016% |

  The Processes tab had been spending 484 ms of its 553 ms inside psutil's
  `num_threads()`, which on Windows takes a whole-machine snapshot once
  *per process*. It was using half the app's own CPU budget to draw one
  table.
- The collector also gathers per-process disk I/O, handle counts and
  private bytes at no extra cost, and retires the private psutil cache the
  Processes tab kept to avoid racing the sampler.
- Dropped a startup priming pass that cost more than every later scan
  combined.

### Added

- **Guide tab** — the whole manual, inside the app, so the installed
  build explains itself. Its alert-threshold and throttling tables are
  generated from `app/config.py`, so they cannot drift from the real
  behaviour.
- **About box** — version, install type, elevation state, Qt/Python
  versions, log location, licence.
- **First-run welcome** — shown once. The app starts as a strip docked in
  the taskbar rather than a window, which was easy to miss entirely.
- **Check for updates** — manual only, on the Guide tab and in the tray
  menu. One HTTPS request to the GitHub release list, only when asked; no
  telemetry, no timer, no background polling, and it never downloads or
  installs anything.

  Certificates are verified through the **Windows** trust store rather
  than OpenSSL's, so the check works behind the HTTPS-inspecting proxies
  common on managed networks. Without that it failed on exactly those
  machines: a corporate root CA measured here was rejected by OpenSSL 3
  for *"Basic Constraints of CA cert not marked critical"* while Windows
  accepted it. Networks that genuinely block the request, or are simply
  offline, still get a specific explanation and a pointer to the browser
  rather than a generic failure.
- **Reduced mode (automatic)** — everything the app sends to the Windows
  shell (tray icon, tooltip, toasts, taskbar position checks) is a
  synchronous call that blocks whenever Explorer is busy; a single toast
  was measured blocking for ten seconds. Those calls are now timed, and if
  the shell proves slow the app pauses that traffic, says so, and restores
  itself once Windows responds normally. Monitoring never stops.

### Fixed

- The one-time "Still monitoring" tray hint could be consumed without ever
  being displayed, so a first-time user got no explanation of where the
  app had gone.
- **Waking from sleep was reported as a failure.** `time.monotonic()` is
  backed by `QueryPerformanceCounter`, which keeps advancing through modern
  standby, so every resume raised a red status, a critical toast and a
  collector restart — and three resumes would trip reduced mode. Both
  watchdogs now tell "nothing was running" apart from "the collector died".
- A floating mini strip could be stranded off-screen after a monitor was
  disconnected, with nothing watching to bring it back.
- The mini strip stopped re-asserting its z-order once docked, so it could
  end up permanently buried behind another window.
- **A momentarily missing taskbar permanently un-docked the strip.** The
  taskbar is routinely unavailable for an instant — auto-hidden, covered
  by a fullscreen app, mid-Explorer-restart — and one such moment made the
  strip give up docking for the whole session, freezing it where the tray
  cluster happened to end and drifting further out of place with every
  icon that came or went. Measured 319 px off target on the machine where
  it was found. The docking intent now survives a transient failure, keeps
  retrying, and the strip is parked somewhere reachable rather than left
  where nobody can see it.
- **"Check for updates" could offer an older release as a download.** The
  button used the latest release's own page for every outcome, so a build
  newer than the newest published release linked to that older release —
  reading as "download this" and walking the user backwards a version. It
  now links to a specific release only when that release is genuinely an
  upgrade.

### Build

- `app/version.py` is now genuinely the single source of truth: the build
  reads it and passes it to Inno Setup, and **fails** if
  `packaging/version_info.txt` disagrees, rather than shipping an
  installer whose version contradicts the app.

## 1.1.0 — 2026-08-24

- **Mini mode**: a compact always-on-top strip that docks into the taskbar
  beside the clock, and is now the default surface at startup.
- **Live taskbar load icon**: the notification-area icon shows current CPU
  load as a number with a fill bar, tinted by overall health.
- **Hard throttling when unobserved** — three cadence tiers, below-normal
  priority and a working-set release when the dashboard is away, reaching
  0.08% CPU and 21 MB at idle.
- Windows installer packaging (PyInstaller + Inno Setup) plus a portable
  ZIP; uninstall now removes everything it created.
- MIT licence and third-party notices.
- Rewritten installation manual.

## 1.0.0 — 2026-08-21

- Initial release: real-time CPU, memory, disk, network and
  responsiveness monitoring with sustained-threshold alerts and
  recommendations.
- Details, Processes and Optimize tabs; one-click cleanup and recovery
  actions, all user-triggered.
- Self-diagnostics: rotating log file, exception hooks, collector stall
  watchdog, single-instance guard, and freeze forensics that record the
  stack the GUI thread was stuck in.
- Own-overhead policy and reduction (2.67% → 1.30% average CPU).
