"""Manual update check.

Deliberately manual, like every other action in this app: nothing here
runs on a timer, at startup, or in the background. The user asks, once,
and the app makes exactly one HTTPS request to the GitHub releases API to
compare version numbers. It never downloads or installs anything — it
tells you what is available and where to get it, and you decide.

That also keeps it honest about privacy: no telemetry, no identifiers,
nothing sent but an ordinary GitHub API request, and only when asked.

Failure is expected and handled, not exceptional. On a managed corporate
network the request may be proxied, TLS-intercepted or blocked outright,
and urllib does not read Windows' proxy configuration the way a browser
does. Every one of those cases is reported as "check it in your browser
instead" with the reason, rather than a stack trace.

TLS verification is delegated to Windows rather than to OpenSSL's own
bundle. Corporate networks that inspect HTTPS install their own root CA
into the Windows store, and those certificates are frequently not quite
standards-perfect -- one measured here was rejected by OpenSSL 3 for
"Basic Constraints of CA cert not marked critical" while Windows itself
accepted it happily. Verifying the way the rest of the OS does means the
check works wherever the browser works, instead of failing on precisely
the managed machines this app is built for.
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .diag import APP_ROOT, IS_FROZEN
from .version import __version__

log = logging.getLogger(__name__)

REPO = "jinypia/desktop_optimizer"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
TIMEOUT_S = 8.0

# Statuses the UI switches on.
AVAILABLE = "available"     # a newer release exists
CURRENT = "current"         # already up to date
AHEAD = "ahead"             # local build is newer than the latest release
NONE = "none"               # the project has published no releases yet
UNREACHABLE = "unreachable"  # no network / DNS / proxy / timeout
BLOCKED = "blocked"         # TLS interception or API refusal
ERROR = "error"             # anything else


@dataclass
class UpdateCheck:
    status: str
    current: str = __version__
    latest: str = ""
    notes: str = ""
    published: str = ""
    url: str = RELEASES_PAGE
    detail: str = ""
    assets: list = field(default_factory=list)   # (name, url) pairs

    @property
    def ok(self) -> bool:
        return self.status in (AVAILABLE, CURRENT, AHEAD, NONE)


# -- version arithmetic -------------------------------------------------------

_NUM = re.compile(r"(\d+(?:\.\d+)*)(.*)")


def parse_version(text: str) -> tuple:
    """Return a sortable key for a version string.

    Numeric parts compare numerically, so 1.10.0 correctly beats 1.9.0
    where a plain string compare would not. A trailing suffix marks a
    pre-release, which sorts *below* the same numbers without one, so
    1.2.0 is newer than 1.2.0-rc1.
    """
    m = _NUM.match((text or "").strip().lstrip("vV"))
    if not m:
        return ((0,), 1)
    nums = [int(p) for p in m.group(1).split(".")]
    while len(nums) < 3:
        nums.append(0)
    is_final = 0 if m.group(2).strip(" .-_+") else 1
    return (tuple(nums), is_final)


def is_newer(candidate: str, baseline: str) -> bool:
    return parse_version(candidate) > parse_version(baseline)


# -- how this copy was installed ----------------------------------------------

def install_kind() -> str:
    """'installed' | 'portable' | 'source' — decides the upgrade advice."""
    if not IS_FROZEN:
        return "source"
    # Inno Setup leaves its uninstaller beside the exe; the portable ZIP
    # has no such thing.
    try:
        if any(n.lower().startswith("unins") and n.lower().endswith(".exe")
               for n in os.listdir(APP_ROOT)):
            return "installed"
    except OSError:
        pass
    return "portable"


UPGRADE_HINT = {
    "installed": (
        "Download the new …-setup.exe and run it. It upgrades in place — "
        "it closes this copy, replaces the files and keeps your window and "
        "mini-strip preferences. No need to uninstall first."),
    "portable": (
        "Download the new DesktopOptimizer-portable.zip, exit this copy, "
        "then unpack it over your current folder."),
    "source": (
        "Pull the new revision and refresh dependencies:\n"
        "    git pull\n"
        "    .venv\\Scripts\\pip install -r requirements.txt"),
}


# -- the check ----------------------------------------------------------------

def _ssl_context():
    """Verify certificates the way Windows does, not the way OpenSSL does.

    Returns None (meaning "urllib's default") if truststore is missing, so
    a stripped-down install still works — it just reverts to OpenSSL's
    stricter view and may report `blocked` behind an inspecting proxy.
    """
    try:
        import truststore
    except ImportError:
        log.debug("truststore unavailable; using OpenSSL verification")
        return None
    try:
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        log.warning("Could not build an OS trust context", exc_info=True)
        return None


def check(timeout: float = TIMEOUT_S, url: str = API_LATEST) -> UpdateCheck:
    """Ask GitHub for the latest release. Blocking — call it on a worker
    thread. Never raises; every failure comes back as a status."""
    req = urllib.request.Request(url, headers={
        # GitHub rejects requests without a User-Agent.
        "User-Agent": f"DesktopOptimizer/{__version__} (+{RELEASES_PAGE})",
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=_ssl_context()) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return _http_failure(e)
    except urllib.error.URLError as e:
        # A TLS failure arrives wrapped in URLError; it means interception
        # or a missing corporate root, not "the internet is down".
        if isinstance(e.reason, ssl.SSLError):
            return UpdateCheck(
                status=BLOCKED,
                detail="The secure connection was rejected, which usually "
                       "means a network appliance is inspecting HTTPS "
                       "traffic. Your browser will be able to reach the "
                       "page even when this cannot.")
        return UpdateCheck(
            status=UNREACHABLE,
            detail=f"Could not reach github.com ({e.reason}). If this "
                   f"machine uses a proxy, the browser knows about it and "
                   f"this check does not.")
    except (socket.timeout, TimeoutError):
        return UpdateCheck(
            status=UNREACHABLE,
            detail=f"No answer from github.com within {timeout:.0f} seconds.")
    except (ValueError, OSError) as e:      # malformed JSON, socket oddities
        log.exception("Update check failed")
        return UpdateCheck(status=ERROR, detail=str(e))

    return _interpret(payload)


def _http_failure(e: urllib.error.HTTPError) -> UpdateCheck:
    if e.code == 404:
        return UpdateCheck(
            status=NONE,
            detail="This project has not published any releases yet.")
    if e.code in (403, 429):
        return UpdateCheck(
            status=BLOCKED,
            detail="GitHub declined the request — usually its hourly "
                   "rate limit for unauthenticated callers. Trying again "
                   "later, or opening the page in a browser, will work.")
    return UpdateCheck(
        status=ERROR,
        detail=f"GitHub returned HTTP {e.code} ({e.reason}).")


def _interpret(payload: dict) -> UpdateCheck:
    tag = (payload.get("tag_name") or payload.get("name") or "").strip()
    if not tag:
        return UpdateCheck(status=NONE,
                           detail="The latest release has no version tag.")
    page = payload.get("html_url") or RELEASES_PAGE
    notes = (payload.get("body") or "").strip()
    published = (payload.get("published_at") or "")[:10]
    assets = [(a.get("name", ""), a.get("browser_download_url", ""))
              for a in payload.get("assets") or []
              if a.get("browser_download_url")]
    latest = tag.lstrip("vV")

    if is_newer(latest, __version__):
        status = AVAILABLE
    elif is_newer(__version__, latest):
        status = AHEAD
    else:
        status = CURRENT
    log.info("Update check: running %s, latest published %s -> %s",
             __version__, latest, status)
    return UpdateCheck(status=status, latest=latest, notes=notes,
                       published=published, url=page, assets=assets)
