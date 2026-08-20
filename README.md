# Desktop Optimizer

A lightweight Windows desktop app that monitors system performance in real
time, alerts you when it degrades, and offers safe one-click cleanup
actions. Built with Python, PySide6 (Qt), pyqtgraph, and psutil.

The app **never optimizes automatically** — it detects and recommends;
every action runs only when you click it.

---

## Requirements

- Windows 10 or 11
- Python 3.10+ (3.13 tested)
- ~500 MB disk space for the virtual environment (PySide6/Qt)

No administrator rights are needed for normal use. Cleanup actions work on
your own user's files and processes; system-protected items are skipped
automatically.

## Installation

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

### Reading the dashboard

- **Stat cards (top row)** — current CPU %, memory use, disk activity, and
  **Responsiveness**: the measured UI event-loop delay in milliseconds.
  This is the "does the desktop feel slow?" number — under ~80 ms is
  smooth.
- **Charts (left)** — the last ~6 minutes of CPU, memory, disk-busy, and
  network history. Hover over any chart for an exact value readout at that
  point in time.
- **Top processes (right)** — the processes using the most CPU right now,
  with their memory use.

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

### One-click optimize actions

All actions are manual — click a button, and the result is reported in the
log beneath the buttons:

| Action | What it does |
|---|---|
| **Clear temp files** | Deletes temp files untouched for 24+ hours (files in use or recent are skipped). The button shows the reclaimable size. |
| **Empty Recycle Bin** | Empties the bin; the button shows its current size. |
| **Flush DNS cache** | Clears the Windows DNS resolver cache (helps after network/VPN changes). |
| **Trim process memory** | Asks Windows to trim process working sets. Safe and reversible — pages return on demand. |
| **Restart Explorer…** | Restarts the Windows shell (fixes a frozen taskbar). Asks for confirmation because open folder windows close. |

### Tray icon

- The icon's dot color mirrors overall health (green / amber / red).
- Hover it for a one-line CPU/memory summary.
- Click it to reopen the dashboard.
- Right-click for: **Open dashboard**, **Quick clean** (temp files + DNS
  in one go), and **Exit**.

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

## Project layout

```
main.py                 entry point
run.bat                 launcher (venv + no console window)
app/
  config.py             sampling cadence + alert rules
  monitor.py            metrics sampler (QThread + psutil)
  analyzer.py           degradation detection -> alerts + recommendations
  optimizer.py          one-click cleanup actions (user-triggered only)
  util.py               formatting helpers
  ui/
    theme.py            dark theme tokens (accessibility-validated palette)
    widgets.py          metric cards + live charts with hover readout
    main_window.py      the dashboard
    tray.py             tray icon, menu, notifications
```

## Troubleshooting

- **App won't start / import errors** — make sure you're running the venv
  interpreter (`.venv\Scripts\python main.py`), not the global one.
- **`pip install` connection resets** — see the trusted-host note under
  Installation.
- **Slow startup** — if the project sits on a network drive, first launch
  loads Qt DLLs over the network; moving the `.venv` to a local disk fixes
  it.
- **No toast notifications** — check Windows Settings → System →
  Notifications isn't suppressing them (Focus Assist / Do Not Disturb).
