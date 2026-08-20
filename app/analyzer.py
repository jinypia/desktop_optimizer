"""Degradation detection.

Each rule fires when its metric stays above a threshold for a sustained
window, and re-arms only after the metric falls below a hysteresis level.
The analyzer never acts on its own — it produces Alert objects with
human-readable recommendations for the UI to display.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from . import config
from .monitor import Snapshot
from .util import human_bytes

SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}

RULE_TITLES = {
    "cpu_high": "High CPU load",
    "mem_high": "High memory pressure",
    "disk_busy": "Disk saturated",
    "ui_lag": "System responsiveness degraded",
}


@dataclass
class Alert:
    rule_id: str
    severity: str          # "warning" | "critical" | "info" (recovery)
    title: str
    detail: str
    recommendations: list
    ts: float


class Analyzer:
    def __init__(self, rules=config.RULES):
        self._rules = rules
        self._history = {r.metric: deque(maxlen=1200) for r in rules}
        self._active = {}          # rule_id -> current severity
        self._full_volumes = {}    # mount -> current severity

    # -- public ------------------------------------------------------------
    def evaluate(self, snap: Snapshot, ui_lag_ms: float) -> list:
        feeds = {
            "cpu": snap.cpu,
            "memory": snap.mem_percent,
            "disk_busy": snap.disk_busy,
            "ui_lag": ui_lag_ms,
        }
        events = []
        for rule in self._rules:
            value = feeds[rule.metric]
            hist = self._history[rule.metric]
            hist.append((snap.ts, value))
            sev = self._sustained_severity(rule, hist, snap.ts)
            active = self._active.get(rule.rule_id)
            if sev and (active is None or SEVERITY_RANK[sev] > SEVERITY_RANK[active]):
                self._active[rule.rule_id] = sev
                events.append(self._alert(rule, sev, value, snap))
            elif active and value < rule.clear_below:
                self._active.pop(rule.rule_id, None)
                events.append(Alert(
                    rule.rule_id, "info",
                    f"{RULE_TITLES[rule.rule_id]} — recovered",
                    f"Back down to {value:.0f}{rule.unit}.", [], snap.ts))
        events.extend(self._check_volumes(snap))
        return events

    def status(self) -> str:
        """Overall health: good | warning | critical."""
        levels = list(self._active.values()) + list(self._full_volumes.values())
        if "critical" in levels:
            return "critical"
        if levels:
            return "warning"
        return "good"

    # -- internals -----------------------------------------------------------
    def _sustained_severity(self, rule, hist, now):
        if hist[0][0] > now - rule.sustain_s:      # not enough history yet
            return None
        window = [v for t, v in hist if t >= now - rule.sustain_s]
        if len(window) < 3:
            return None
        low = min(window)
        if low >= rule.critical_at:
            return "critical"
        if low >= rule.warn_at:
            return "warning"
        return None

    def _alert(self, rule, sev, value, snap):
        detail = f"Sustained at {value:.0f}{rule.unit} for the last {rule.sustain_s:.0f} s."
        return Alert(rule.rule_id, sev, RULE_TITLES[rule.rule_id], detail,
                     self._recommendations(rule.rule_id, snap), snap.ts)

    def _recommendations(self, rule_id, snap):
        if rule_id == "cpu_high":
            tops = ", ".join(f"{p.name} ({p.cpu:.0f}%)" for p in snap.top_cpu[:3])
            return [
                f"Heaviest CPU users: {tops}",
                "Close or restart the heaviest of these if you don't need them",
                "On laptops, check the active power plan isn't throttling",
            ]
        if rule_id == "mem_high":
            tops = ", ".join(f"{p.name} ({human_bytes(p.rss)})" for p in snap.top_mem[:3])
            return [
                f"Largest memory users: {tops}",
                "Close applications you are not using",
                "Run 'Trim process memory' below",
            ]
        if rule_id == "disk_busy":
            return [
                "Often caused by Windows Update, Search indexing or antivirus "
                "scans — they usually finish on their own",
                "Postpone disk-heavy work (large copies, builds) until it drops",
                "Run 'Clear temp files' to reduce temp-file churn",
            ]
        if rule_id == "ui_lag":
            return [
                "Usually follows CPU, memory or disk pressure — check the charts",
                "Close heavy applications, then run 'Trim process memory'",
            ]
        return []

    def _check_volumes(self, snap):
        events = []
        for vol in snap.volumes:
            sev = None
            if vol.percent >= config.DISK_FULL_CRITICAL:
                sev = "critical"
            elif vol.percent >= config.DISK_FULL_WARN:
                sev = "warning"
            active = self._full_volumes.get(vol.mount)
            if sev and (active is None or SEVERITY_RANK[sev] > SEVERITY_RANK[active]):
                self._full_volumes[vol.mount] = sev
                free = vol.total - vol.used
                events.append(Alert(
                    "disk_full", sev, f"Drive {vol.mount} almost full",
                    f"{vol.percent:.0f}% used — {human_bytes(free)} free "
                    f"of {human_bytes(vol.total)}.",
                    [
                        "Run 'Clear temp files' and 'Empty Recycle Bin' below",
                        "Uninstall unused applications or move large files off the drive",
                    ],
                    snap.ts))
            elif active and sev is None and vol.percent < config.DISK_FULL_WARN - 3:
                self._full_volumes.pop(vol.mount, None)
        return events
