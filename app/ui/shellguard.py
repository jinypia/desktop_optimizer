"""Guard for cross-process calls into the Windows shell.

Talking to explorer.exe — `Shell_NotifyIcon` (tray icon/tooltip/balloon),
`FindWindow`/`GetWindowRect` on the taskbar — is *synchronous*. When the
shell is busy those calls block the calling thread for seconds, and a GUI
thread stuck in one stops pumping messages: Windows marks the window "not
responding", and an always-on-top strip parked over the taskbar makes that
whole area feel dead.

So every shell call goes through this guard, which times it and, if the
shell proves slow, **degrades the app automatically**: the live tray
readout falls back to a static icon, taskbar polling stops, and the
remaining chatter is cut to a minimum. It retries later and restores full
behaviour once the shell is responsive again — no user intervention.
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)

SLOW_MS = 300.0          # a shell call slower than this is "slow"
TRIP_AFTER = 3           # this many slow calls (within the window) -> degrade
TRIP_WINDOW_S = 60.0     # slow calls older than this are forgotten
RECOVER_AFTER_S = 300.0  # then probe once to see if the shell recovered


class ShellGuard:
    """Times shell calls; trips into degraded mode when they are slow."""

    # After this many episodes, stop re-enabling the live numeric tray icon
    # for the rest of the session: on a machine whose shell is chronically
    # slow, flapping in and out of it is worse than losing the readout.
    MAX_EPISODES_FOR_LIVE_ICON = 2

    def __init__(self):
        self._lock = threading.Lock()
        self._slow = []                  # monotonic timestamps of slow calls
        self._degraded = False
        self._degraded_since = 0.0
        self._reason = ""
        self._episodes = 0
        self._on_change = None           # callback(degraded: bool, reason: str)

    # -- wiring --------------------------------------------------------------
    def set_listener(self, callback):
        self._on_change = callback

    @property
    def degraded(self) -> bool:
        return self._degraded

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def live_icon_allowed(self) -> bool:
        """False once this shell has proven repeatedly slow — the tray then
        keeps a cheap static icon instead of a live numeric one."""
        return (not self._degraded
                and self._episodes < self.MAX_EPISODES_FOR_LIVE_ICON)

    # -- the wrapper ---------------------------------------------------------
    def call(self, label: str, fn, *args, **kwargs):
        """Run a shell call, timing it. Returns whatever fn returns."""
        started = time.monotonic()
        try:
            return fn(*args, **kwargs)
        finally:
            elapsed_ms = (time.monotonic() - started) * 1000.0
            if elapsed_ms >= SLOW_MS:
                self._record_slow(label, elapsed_ms)

    def _record_slow(self, label: str, elapsed_ms: float):
        now = time.monotonic()
        with self._lock:
            self._slow = [t for t in self._slow if now - t < TRIP_WINDOW_S]
            self._slow.append(now)
            count = len(self._slow)
            trip = count >= TRIP_AFTER and not self._degraded
        log.warning("Slow shell call: %s took %.0f ms (%d recent)",
                    label, elapsed_ms, count)
        if trip:
            self.degrade(f"the Windows shell is responding slowly "
                         f"({label} took {elapsed_ms:.0f} ms)")

    # -- degrade / recover ---------------------------------------------------
    def degrade(self, reason: str):
        """Enter degraded mode. Safe to call from any thread."""
        with self._lock:
            if self._degraded:
                return
            self._degraded = True
            self._degraded_since = time.monotonic()
            self._reason = reason
            self._episodes += 1
            episodes = self._episodes
            self._slow.clear()
        log.warning("Degrading to reduce shell traffic (episode %d): %s",
                    episodes, reason)
        if self._on_change:
            self._on_change(True, reason)

    def restore(self):
        with self._lock:
            if not self._degraded:
                return
            self._degraded = False
            self._reason = ""
            self._slow.clear()
            keep_static = self._episodes >= self.MAX_EPISODES_FOR_LIVE_ICON
        log.info("Shell responsive again — resuming%s",
                 " (live tray readout stays off for this session)"
                 if keep_static else "")
        if self._on_change:
            self._on_change(False, "")

    def due_for_retry(self) -> bool:
        """True once it is worth testing whether the shell recovered."""
        with self._lock:
            return (self._degraded
                    and time.monotonic() - self._degraded_since
                    >= RECOVER_AFTER_S)


# One guard for the whole process.
guard = ShellGuard()
