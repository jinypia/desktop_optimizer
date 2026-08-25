"""In-app manual.

The installed build has no README next to it, so everything a user needs
to understand the app lives here, one tab away.

Two rules for this file:

  - Numbers are generated, never retyped. The alert thresholds and the
    throttling cadences are read out of app/config.py, so the manual
    cannot drift away from what the code actually does.
  - It costs nothing until opened. The document is built on first view
    and then cached, so a user who never opens the Guide never pays for
    it.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout, QMessageBox, QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

from .. import config, optimizer
from ..analyzer import RULE_TITLES
from ..monitor import MetricsSampler
from ..updates import UPGRADE_HINT, install_kind as _install_kind
from ..version import APP_NAME, PUBLISHER, __version__
from . import theme

_INSTALL_WORDING = {
    "installed": "installed with the setup program",
    "portable": "as a portable copy",
    "source": "from source",
}


def _hint_html() -> str:
    """UPGRADE_HINT is plain text (it has shell commands on their own
    lines); both places that show it render rich text, which would
    otherwise collapse it to one run-on line."""
    hint = UPGRADE_HINT.get(_install_kind(), "")
    return (hint.replace("\n", "<br>")
                .replace("    ", "&nbsp;" * 4))


class GuideTab(QWidget):
    """Scrollable manual. Content is rendered lazily on first show."""

    open_log_requested = Signal()
    check_updates_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rendered = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 8, 4, 4)
        lay.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        btn_log = QPushButton("Open log folder")
        btn_log.setToolTip("Where the app records its own errors and actions")
        btn_log.clicked.connect(self.open_log_requested)
        self.btn_updates = QPushButton("Check for updates")
        self.btn_updates.setToolTip(
            "Ask GitHub once whether a newer release exists. Nothing is "
            "downloaded or installed, and no check ever runs on its own.")
        self.btn_updates.clicked.connect(self.check_updates_requested)
        btn_about = QPushButton("About")
        btn_about.clicked.connect(lambda: show_about(self))
        bar.addWidget(btn_log)
        bar.addWidget(self.btn_updates)
        bar.addWidget(btn_about)
        bar.addStretch(1)
        lay.addLayout(bar)

        self._view = QTextBrowser()
        self._view.setOpenExternalLinks(True)
        self._view.setFont(QFont("Segoe UI", 9))
        lay.addWidget(self._view, 1)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._rendered:
            self._rendered = True
            self._view.setHtml(_document())


# -- generated tables ---------------------------------------------------------

def _rules_rows() -> str:
    """Alert thresholds, straight from config.RULES."""
    out = []
    for r in config.RULES:
        out.append(_row(
            RULE_TITLES.get(r.rule_id, r.rule_id),
            f"{r.warn_at:g}{r.unit}",
            f"{r.critical_at:g}{r.unit}",
            f"{r.sustain_s:g} s",
            f"below {r.clear_below:g}{r.unit}"))
    return "".join(out)


def _cadence_rows() -> str:
    """Throttling tiers, straight from MetricsSampler.MODES."""
    labels = {"dashboard": "Dashboard open",
              "mini": "Mini strip only",
              "hidden": "Hidden (tray only)"}
    out = []
    for mode, (interval, scan_every) in MetricsSampler.MODES.items():
        out.append(_row(
            labels.get(mode, mode),
            f"every {interval:g} s",
            f"every {scan_every}{_ordinal(scan_every)} sample",
            "normal" if mode == "dashboard" else "below normal",
            "drawn" if mode == "dashboard" else "skipped"))
    return "".join(out)


def _ordinal(n: int) -> str:
    return {1: "st", 2: "nd", 3: "rd"}.get(n if n < 20 else n % 10, "th")


def _row(*cells: str) -> str:
    tds = "".join(
        f'<td style="padding:4px 10px 4px 0; color:{theme.TEXT_2};">{c}</td>'
        for c in cells)
    return f"<tr>{tds}</tr>"


def _head(*cells: str) -> str:
    tds = "".join(
        f'<td style="padding:4px 10px 4px 0; color:{theme.MUTED}; '
        f'font-size:11px;">{c}</td>' for c in cells)
    return f"<tr>{tds}</tr>"


def _table(header, rows: str) -> str:
    return (f'<table width="100%" cellspacing="0" cellpadding="0">'
            f'{_head(*header)}{rows}</table>')


def _actions_table() -> str:
    rows = [
        ("Clear temp files",
         f"Deletes files in your temp folders untouched for "
         f"{optimizer.TEMP_MIN_AGE_H}+ hours. Anything in use is skipped.",
         "Safe"),
        ("Empty Recycle Bin",
         "Permanently removes everything in the Recycle Bin.",
         "Permanent"),
        ("Clear browser caches",
         "Chrome, Edge and Firefox HTTP caches. Close the browser first "
         "for a full clean — open files are skipped, not forced.",
         "Safe"),
        ("Clear thumbnail cache",
         "Explorer's thumbnail and icon caches. Fixes wrong or stale "
         "thumbnails; Windows rebuilds them on demand.",
         "Safe"),
        ("Clear crash dumps &amp; error reports",
         "Old crash dumps and Windows Error Reporting queues in your "
         "profile. Only useful if you are not investigating a crash.",
         "Safe"),
        ("Trim process memory",
         "Asks Windows to page out each process's working set. Pages "
         "return on demand, so the next click in another app may be "
         "briefly slower.",
         "Reversible"),
        ("Purge standby memory",
         "Drops cached pages so they become immediately free. Everything "
         "that needed those pages must re-read them from disk.",
         "Admin"),
        ("Flush DNS cache",
         "Clears the DNS resolver cache. Useful after a VPN connect or a "
         "network change.",
         "Safe"),
        ("Switch power plan",
         "Toggles between Balanced and High performance. Some laptops "
         "hide the High performance plan.",
         "Safe"),
        ("Restart graphics driver",
         "Sends Win+Ctrl+Shift+B, Windows' own display reset. The screen "
         "flashes; nothing closes.",
         "Safe"),
        ("Restart Explorer",
         "Restarts the shell. The taskbar and any open folder windows "
         "disappear and come back empty.",
         "Disruptive"),
    ]
    tint = {"Safe": theme.STATUS["good"], "Reversible": theme.SERIES_CPU,
            "Permanent": theme.STATUS["warning"],
            "Disruptive": theme.STATUS["warning"],
            "Admin": theme.STATUS["warning"]}
    body = []
    for name, what, tag in rows:
        body.append(
            f'<tr>'
            f'<td style="padding:5px 10px 5px 0; color:{theme.TEXT}; '
            f'font-weight:600;" width="26%">{name}</td>'
            f'<td style="padding:5px 10px 5px 0; color:{theme.TEXT_2};">'
            f'{what}</td>'
            f'<td style="padding:5px 0; color:{tint[tag]}; font-size:11px;" '
            f'width="10%">{tag}</td>'
            f'</tr>')
    return (f'<table width="100%" cellspacing="0" cellpadding="0">'
            f'{_head("Action", "What it does", "")}{"".join(body)}</table>')


# -- the document -------------------------------------------------------------

def _document() -> str:
    h2 = (f"color:{theme.TEXT}; font-size:15px; font-weight:600; "
          f"margin:18px 0 2px 0;")
    h3 = (f"color:{theme.TEXT}; font-size:13px; font-weight:600; "
          f"margin:14px 0 2px 0;")
    p = f"color:{theme.TEXT_2}; margin:4px 0 8px 0; line-height:140%;"
    note = f"color:{theme.MUTED}; font-size:11px; margin:2px 0 10px 0;"
    key = f"color:{theme.TEXT}; font-weight:600;"

    def li(text):
        return f'<li style="{p} margin:2px 0;">{text}</li>'

    return f"""<html><body style="background:{theme.SURFACE};">
<div style="color:{theme.TEXT}; font-size:19px; font-weight:600;">
  {APP_NAME} <span style="color:{theme.MUTED}; font-size:13px;
  font-weight:400;">v{__version__}</span></div>
<div style="{p}">
  A performance monitor for Windows desktops. It watches CPU, memory,
  disk, network and responsiveness continuously, tells you when the
  machine is degrading <i>and which process is responsible</i>, and gives
  you safe one-click ways to get room back — without a reboot.</div>

<div style="background:#242423; border-left:2px solid {theme.SERIES_CPU};
     padding:8px 12px; margin:10px 0 4px 0; color:{theme.TEXT_2};">
  <b style="color:{theme.TEXT};">Nothing happens on its own.</b>
  The app detects and recommends; every cleanup action runs only when you
  click it. The one thing it does automatically is get <i>itself</i> out
  of the way — see <a href="#overhead"
  style="color:{theme.SERIES_CPU};">Staying out of the way</a>.</div>

<p style="{note}">Jump to:
  <a href="#surfaces" style="color:{theme.SERIES_CPU};">Where it lives</a> ·
  <a href="#reading" style="color:{theme.SERIES_CPU};">Reading the dashboard</a> ·
  <a href="#tabs" style="color:{theme.SERIES_CPU};">The tabs</a> ·
  <a href="#alerts" style="color:{theme.SERIES_CPU};">Alerts</a> ·
  <a href="#actions" style="color:{theme.SERIES_CPU};">Optimize actions</a> ·
  <a href="#overhead" style="color:{theme.SERIES_CPU};">Staying out of the way</a> ·
  <a href="#updates" style="color:{theme.SERIES_CPU};">Updates</a> ·
  <a href="#trouble" style="color:{theme.SERIES_CPU};">If something looks wrong</a>
</p>

<a name="surfaces"></a>
<div style="{h2}">Where it lives</div>
<div style="{p}">The app has three faces. It starts as the small one.</div>

<div style="{h3}">The mini strip <span style="color:{theme.MUTED};
     font-weight:400; font-size:11px;">— the default</span></div>
<div style="{p}">A compact always-on-top bar that docks into the taskbar
  beside the clock, showing CPU, memory, disk, network and
  responsiveness. <b style="{key}">Double-click it</b> for the full
  dashboard. <b style="{key}">Drag it</b> anywhere to undock;
  <b style="{key}">right-click</b> for its menu. It remembers where you
  put it.</div>

<div style="{h3}">The dashboard</div>
<div style="{p}">The full window: charts, detail views, the process list
  and the cleanup actions. Closing it does not quit the app — it drops
  back to the mini strip. <b style="{key}">Ctrl+M</b> toggles between
  the two.</div>

<div style="{h3}">The notification-area icon</div>
<div style="{p}">Always present. It shows live CPU load as a number with
  a fill bar, tinted by overall health:
  <span style="color:{theme.STATUS['good']};">
  {theme.STATUS_ICON['good']} green</span> healthy,
  <span style="color:{theme.STATUS['warning']};">
  {theme.STATUS_ICON['warning']} amber</span> degraded,
  <span style="color:{theme.STATUS['critical']};">
  {theme.STATUS_ICON['critical']} red</span> severe.
  Right-click it for the dashboard, mini mode, a quick clean, the log
  folder or exit.</div>

<a name="reading"></a>
<div style="{h2}">Reading the dashboard</div>
<ul>
{li(f'<b style="{key}">The four cards</b> — CPU, Memory, Disk and '
    f'Responsiveness. Each shows the headline number plus context, and '
    f'the heaviest process for that resource.')}
{li(f'<b style="{key}">Responsiveness</b> is the one to trust when the '
    f'machine <i>feels</i> slow. It measures how late the app\'s own '
    f'timer runs: if Windows cannot service a timer on time, it is not '
    f'servicing your clicks either. Smooth is under 80 ms.')}
{li(f'<b style="{key}">The vitals strip</b> — network throughput, '
    f'process count, context switches, system calls, pagefile use, '
    f'uptime, and the app\'s own cost.')}
{li(f'<b style="{key}">The charts</b> hold about six minutes of history. '
    f'Hover for a crosshair and exact values. History keeps accumulating '
    f'while the window is hidden, so reopening shows an unbroken line.')}
{li(f'<b style="{key}">The health panel</b> on the right is always '
    f'visible: overall status, the alert feed, and the top processes by '
    f'CPU.')}
</ul>

<a name="tabs"></a>
<div style="{h2}">The tabs</div>
<ul>
{li(f'<b style="{key}">Dashboard</b> — cards, vitals and live charts.')}
{li(f'<b style="{key}">Details</b> — the professional view: machine and '
    f'OS identity, per-core CPU bars, kernel activity, memory and '
    f'pagefile, battery, every volume, every network interface.')}
{li(f'<b style="{key}">Processes</b> — the full list with name, PID, '
    f'owner, CPU, memory, threads, priority and start time. Sort by any '
    f'column, filter by name, PID or owner. Select a row to end the '
    f'process, end its whole tree, or change its priority. Changing '
    f'priority is often better than killing: it takes CPU away from a '
    f'runaway process without losing your work.')}
{li(f'<b style="{key}">Optimize</b> — the one-click actions below, plus '
    f'a log of everything that has run.')}
{li(f'<b style="{key}">Guide</b> — this page.')}
</ul>
<div style="{note}">Details and Processes only query the system while
  their tab is open.</div>

<a name="alerts"></a>
<div style="{h2}">Alerts</div>
<div style="{p}">An alert needs a problem to be <i>sustained</i>, not a
  momentary spike — a metric must stay over the line for the whole window
  below. It then has to recover past a lower mark before it can fire
  again, so one episode produces one alert rather than one per second.
  Every alert carries specific recommendations, including which processes
  are responsible.</div>
{_table(("Alert", "Warning", "Critical", "Sustained for", "Clears"),
        _rules_rows())}
<div style="{note}">A drive is also flagged at
  {config.DISK_FULL_WARN}% full, and critically at
  {config.DISK_FULL_CRITICAL}%. Every threshold on this page is read from
  <b style="color:{theme.TEXT_2};">app/config.py</b>, so editing it there
  changes both the behaviour and this table.</div>

<a name="actions"></a>
<div style="{h2}">Optimize actions</div>
<div style="{p}">All of these run on a background thread, report what
  they did, and skip anything locked or in use rather than forcing it.
  Temp files and the Recycle Bin show their reclaimable size on the
  button.</div>
{_actions_table()}
<div style="{note}"><b>Admin</b> actions need elevation — use
  <i>Restart as administrator</i> on the Optimize tab.
  <b>Run all safe cleanups</b> runs the safe set in one go.
  <i>Quick clean</i> in the tray menu does temp files plus DNS.</div>

<a name="overhead"></a>
<div style="{h2}">Staying out of the way</div>
<div style="{p}">A monitor that costs you performance defeats itself, so
  this one is built to disappear. The vitals strip always shows its own
  live cost, and if it ever averages more than
  {config.SELF_CPU_BUDGET:g}% CPU over a minute it raises a warning
  <i>against itself</i>.</div>
{_table(("When", "Samples", "Full process scan", "Priority", "Charts"),
        _cadence_rows())}
<div style="{p}">Leaving the dashboard also hands memory back to Windows
  and drops the app to below-normal priority, so it can never compete
  with what you are actually using.</div>

<div style="{h3}">Reduced mode</div>
<div style="{p}">Everything the app sends to the Windows shell — the tray
  icon, its tooltip, toast notifications, taskbar position checks — is a
  synchronous call that blocks whenever Explorer is busy. On a loaded
  machine a single notification has been measured blocking for ten
  seconds. So each of those calls is timed, and if the shell proves slow
  the app automatically pauses the live tray readout, the toasts and the
  position tracking, and tells you it has done so. Monitoring, alerting
  and cleanups carry on. It restores itself once Windows is responsive
  again.</div>

<a name="updates"></a>
<div style="{h2}">Updates</div>
<div style="{p}"><b style="{key}">Check for updates</b> at the top of this
  page — or in the tray menu — compares this copy against the latest
  published release. It is a manual check: nothing runs on a timer, at
  startup or in the background, and the app never downloads or installs
  anything by itself. If a newer version exists it tells you what changed
  and offers to open the download page in your browser; installing it is
  your decision.</div>
<div style="{p}">You are running <b style="{key}">{__version__}</b>,
  {_INSTALL_WORDING.get(_install_kind(), "from source")}.
  {_hint_html()}</div>
<div style="{note}">The check makes one ordinary HTTPS request to
  GitHub's release list and sends nothing about you or this machine — no
  telemetry, no identifiers. On a managed corporate network it may be
  blocked or TLS-inspected; the app says so plainly and points you at the
  browser, which knows about the proxy when this check does not.</div>

<a name="trouble"></a>
<div style="{h2}">If something looks wrong</div>
<ul>
{li(f'<b style="{key}">Check the log first.</b> Errors, crashes, action '
    f'results and Qt warnings all go to a rotating log file — the '
    f'<i>Open log folder</i> button at the top of this page.')}
{li(f'<b style="{key}">Charts frozen?</b> The app watches its own '
    f'collector and restarts it automatically, saying so in the status '
    f'line. Waking from sleep is recognised as such and does not count.')}
{li(f'<b style="{key}">Window says "not responding"?</b> The watchdog '
    f'records the exact stack it was stuck in, then sheds whatever was '
    f'blocking it. See Reduced mode above.')}
{li(f'<b style="{key}">Access denied ending a process?</b> It belongs to '
    f'the system or another user. Use <i>Restart as administrator</i>.')}
{li(f'<b style="{key}">A cleanup freed nothing?</b> Files in use are '
    f'skipped by design. Close the owning app — for thumbnails, run '
    f'<i>Restart Explorer</i> first — and try again.')}
</ul>
<div style="{note}">Only one copy runs at a time; launching it again just
  points you at the existing tray icon.</div>
<br>
</body></html>"""


# -- about --------------------------------------------------------------------

def show_about(parent=None):
    """Small About box. Kept text-only so it costs nothing to open."""
    import platform

    from PySide6.QtCore import qVersion

    from .. import diag

    build = "Installed" if diag.IS_FROZEN else "Running from source"
    rights = "administrator" if optimizer.is_admin() else "standard user"
    box = QMessageBox(parent)
    box.setWindowTitle(f"About {APP_NAME}")
    box.setTextFormat(Qt.RichText)
    box.setText(
        f'<div style="font-size:15px; font-weight:600;">{APP_NAME}</div>'
        f'<div>Version {__version__} — {PUBLISHER}</div>')
    box.setInformativeText(
        f"<p>A lightweight Windows performance monitor: continuous "
        f"monitoring, degradation alerts that name the process "
        f"responsible, and safe one-click cleanups. It never optimizes "
        f"on its own.</p>"
        f"<p><b>{build}</b> · {rights}<br>"
        f"Python {platform.python_version()} · Qt {qVersion()} · "
        f"{platform.system()} {platform.release()}<br>"
        f"Logs: {diag.LOG_DIR}</p>"
        f"<p>Released under the MIT licence. Built with PySide6 (Qt), "
        f"pyqtgraph and psutil — see THIRD-PARTY-NOTICES.md for their "
        f"licences.</p>")
    box.setTextInteractionFlags(Qt.TextSelectableByMouse)
    box.setIcon(QMessageBox.NoIcon)
    box.setStandardButtons(QMessageBox.Close)
    box.exec()


# -- update result ------------------------------------------------------------

def show_update_result(parent, res) -> bool:
    """Report a finished update check. Returns True if the user asked to
    open the download page (the caller opens it — this stays UI-only).

    Nothing is downloaded here by design: the user said what they wanted
    installed, and this app does not install things behind their back.
    """
    box = QMessageBox(parent)
    box.setWindowTitle("Check for updates")
    box.setTextFormat(Qt.RichText)
    box.setIcon(QMessageBox.NoIcon)
    box.setTextInteractionFlags(Qt.TextSelectableByMouse)

    heading, body, offer_page = _update_wording(res)
    box.setText(f'<div style="font-size:14px; font-weight:600;">{heading}</div>')
    box.setInformativeText(body)
    if res.notes:
        # Release notes are markdown; show them verbatim rather than
        # half-rendering them.
        box.setDetailedText(f"Release notes for {res.latest}\n\n{res.notes}")

    open_btn = None
    if offer_page:
        open_btn = box.addButton("Open download page", QMessageBox.AcceptRole)
    box.addButton("Close", QMessageBox.RejectRole)
    box.exec()
    return open_btn is not None and box.clickedButton() is open_btn


def _update_wording(res):
    """(heading, html body, offer the download page?) for a check result."""
    from .. import updates

    hint = _hint_html()
    if res.status == updates.AVAILABLE:
        when = f" (published {res.published})" if res.published else ""
        return (f"Version {res.latest} is available",
                f"<p>You are running <b>{res.current}</b>. The latest "
                f"release is <b>{res.latest}</b>{when}.</p>"
                f"<p>{hint}</p>"
                f"<p style='color:{theme.MUTED};'>Nothing has been "
                f"downloaded. Use the button below to open the release page "
                f"in your browser.</p>", True)
    if res.status == updates.CURRENT:
        return ("You are up to date",
                f"<p>Version <b>{res.current}</b> is the latest release.</p>",
                False)
    if res.status == updates.AHEAD:
        return ("This build is newer than the latest release",
                f"<p>You are running <b>{res.current}</b>; the newest "
                f"published release is <b>{res.latest}</b>. This is normal "
                f"for a build made from source.</p>", True)
    if res.status == updates.NONE:
        return ("No releases published yet",
                f"<p>{res.detail}</p><p>You are running "
                f"<b>{res.current}</b>.</p>", True)
    # unreachable / blocked / error
    return ("Could not check for updates",
            f"<p>{res.detail}</p>"
            f"<p>You are running <b>{res.current}</b>. Nothing is wrong with "
            f"this copy — only the version check failed.</p>", True)
