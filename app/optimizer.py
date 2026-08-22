"""User-triggered cleanup and control actions.

Every function here runs only when the user clicks its button — the app
never optimizes automatically. Functions are synchronous; the UI executes
them on a worker thread and shows the returned ActionResult.
"""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

import psutil

from .diag import APP_ROOT, IS_FROZEN
from .util import human_bytes

TEMP_MIN_AGE_H = 24          # only delete temp files untouched for this long
CREATE_NO_WINDOW = 0x08000000


@dataclass
class ActionResult:
    name: str
    ok: bool
    message: str


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# -- generic sweep ------------------------------------------------------------

def _sweep(dirs, cutoff_ts=None):
    """Delete files under dirs (older than cutoff_ts if given); remove
    now-empty subdirs. Locked / in-use files are skipped, never forced."""
    freed = deleted = skipped = 0
    for root_dir in dirs:
        if not root_dir or not os.path.isdir(root_dir):
            continue
        for root, _d, files in os.walk(root_dir, topdown=False,
                                       onerror=lambda e: None):
            for f in files:
                path = os.path.join(root, f)
                try:
                    st = os.stat(path)
                    if cutoff_ts and st.st_mtime >= cutoff_ts:
                        skipped += 1
                        continue
                    os.remove(path)
                    freed += st.st_size
                    deleted += 1
                except OSError:
                    skipped += 1
            if root != root_dir:
                try:
                    os.rmdir(root)
                except OSError:
                    pass
    return freed, deleted, skipped


def _scan_size(dirs, cutoff_ts=None):
    total = count = 0
    for root_dir in dirs:
        if not root_dir or not os.path.isdir(root_dir):
            continue
        for root, _d, files in os.walk(root_dir, onerror=lambda e: None):
            for f in files:
                try:
                    st = os.stat(os.path.join(root, f))
                    if cutoff_ts and st.st_mtime >= cutoff_ts:
                        continue
                    total += st.st_size
                    count += 1
                except OSError:
                    continue
    return total, count


# -- temp files --------------------------------------------------------------

def _temp_dirs():
    dirs = []
    for d in (tempfile.gettempdir(),
              os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Temp")):
        if d and os.path.isdir(d) and d not in dirs:
            dirs.append(d)
    return dirs


def scan_temp():
    """Return (reclaimable_bytes, file_count) for deletable temp files."""
    cutoff = time.time() - TEMP_MIN_AGE_H * 3600
    return _scan_size(_temp_dirs(), cutoff)


def clear_temp() -> ActionResult:
    cutoff = time.time() - TEMP_MIN_AGE_H * 3600
    freed, deleted, skipped = _sweep(_temp_dirs(), cutoff)
    return ActionResult(
        "Clear temp files", True,
        f"Deleted {deleted:,} files, freed {human_bytes(freed)}; "
        f"{skipped:,} skipped (recent or in use).")


# -- browser caches -----------------------------------------------------------

def _browser_cache_dirs():
    lad = os.environ.get("LOCALAPPDATA", "")
    dirs = []
    for base in (os.path.join(lad, "Google", "Chrome", "User Data"),
                 os.path.join(lad, "Microsoft", "Edge", "User Data")):
        if not os.path.isdir(base):
            continue
        try:
            profiles = os.listdir(base)
        except OSError:
            continue
        for prof in profiles:
            if prof == "Default" or prof.startswith("Profile"):
                for sub in ("Cache", "Code Cache", "GPUCache"):
                    d = os.path.join(base, prof, sub)
                    if os.path.isdir(d):
                        dirs.append(d)
    ff = os.path.join(lad, "Mozilla", "Firefox", "Profiles")
    if os.path.isdir(ff):
        try:
            for prof in os.listdir(ff):
                d = os.path.join(ff, prof, "cache2")
                if os.path.isdir(d):
                    dirs.append(d)
        except OSError:
            pass
    return dirs


def clear_browser_caches() -> ActionResult:
    freed, deleted, skipped = _sweep(_browser_cache_dirs())
    return ActionResult(
        "Clear browser caches", True,
        f"Deleted {deleted:,} files, freed {human_bytes(freed)}; "
        f"{skipped:,} skipped. Close browsers first for a full clean.")


# -- thumbnail / icon cache ----------------------------------------------------

def clear_thumbnail_cache() -> ActionResult:
    d = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     "Microsoft", "Windows", "Explorer")
    freed = deleted = skipped = 0
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f.startswith(("thumbcache_", "iconcache_")) and f.endswith(".db"):
                path = os.path.join(d, f)
                try:
                    size = os.path.getsize(path)
                    os.remove(path)
                    freed += size
                    deleted += 1
                except OSError:
                    skipped += 1
    note = " Locked files belong to Explorer — run 'Restart Explorer' " \
           "first to release them." if skipped else ""
    return ActionResult(
        "Clear thumbnail cache", True,
        f"Deleted {deleted} cache files, freed {human_bytes(freed)}; "
        f"{skipped} locked.{note}")


# -- crash dumps & error reports -------------------------------------------------

def _crash_dirs():
    lad = os.environ.get("LOCALAPPDATA", "")
    wer = os.path.join(lad, "Microsoft", "Windows", "WER")
    return [os.path.join(lad, "CrashDumps"),
            os.path.join(wer, "ReportQueue"),
            os.path.join(wer, "ReportArchive"),
            os.path.join(wer, "Temp")]


def clear_crash_reports() -> ActionResult:
    freed, deleted, skipped = _sweep(_crash_dirs())
    return ActionResult(
        "Clear crash dumps & error reports", True,
        f"Deleted {deleted:,} files, freed {human_bytes(freed)}; "
        f"{skipped:,} skipped.")


# -- recycle bin ---------------------------------------------------------------

class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong),
                ("i64Size", ctypes.c_longlong),
                ("i64NumItems", ctypes.c_longlong)]


def recycle_bin_size():
    """Return (bytes, item_count) currently in the Recycle Bin."""
    info = _SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(info)
    if ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) != 0:
        return 0, 0
    return int(info.i64Size), int(info.i64NumItems)


def empty_recycle_bin() -> ActionResult:
    size, items = recycle_bin_size()
    if items == 0:
        return ActionResult("Empty Recycle Bin", True, "Recycle Bin is already empty.")
    SHERB_NO_UI = 0x1 | 0x2 | 0x4    # no confirmation, no progress, no sound
    res = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, SHERB_NO_UI)
    if res in (0, -2147418113):      # S_OK, or E_UNEXPECTED when already empty
        return ActionResult("Empty Recycle Bin", True,
                            f"Removed {items:,} items, freed {human_bytes(size)}.")
    return ActionResult("Empty Recycle Bin", False,
                        f"Shell error 0x{res & 0xFFFFFFFF:08X}.")


# -- network -------------------------------------------------------------------

def flush_dns() -> ActionResult:
    try:
        out = subprocess.run(["ipconfig", "/flushdns"], capture_output=True,
                             text=True, timeout=30, creationflags=CREATE_NO_WINDOW)
        ok = out.returncode == 0
        return ActionResult("Flush DNS cache", ok,
                            "DNS resolver cache flushed." if ok
                            else (out.stderr.strip() or "ipconfig failed."))
    except OSError as e:
        return ActionResult("Flush DNS cache", False, str(e))


# -- memory --------------------------------------------------------------------

def trim_working_sets() -> ActionResult:
    """Ask Windows to trim each accessible process's working set.

    Trimmed pages go to the standby list and are pulled back on demand, so
    this is safe — but the next access to a trimmed app may be briefly slower.
    """
    PROCESS_SET_QUOTA = 0x0100
    PROCESS_QUERY_INFORMATION = 0x0400
    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    # explicit prototypes: default c_int would truncate 64-bit HANDLEs
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int,
                                     ctypes.c_uint32)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    psapi.EmptyWorkingSet.argtypes = (ctypes.c_void_p,)
    before = psutil.virtual_memory().available
    trimmed = 0
    for proc in psutil.process_iter(["pid"]):
        pid = proc.info["pid"]
        if pid in (0, 4, os.getpid()):
            continue
        handle = kernel32.OpenProcess(
            PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION, False, pid)
        if not handle:
            continue
        try:
            if psapi.EmptyWorkingSet(handle):
                trimmed += 1
        finally:
            kernel32.CloseHandle(handle)
    time.sleep(0.5)
    gained = psutil.virtual_memory().available - before
    return ActionResult(
        "Trim process memory", True,
        f"Trimmed {trimmed} processes; available memory +{human_bytes(max(gained, 0))}.")


def purge_standby_list() -> ActionResult:
    """Purge the standby memory list (cached pages). Requires administrator
    rights and the SeProfileSingleProcessPrivilege."""
    if not is_admin():
        return ActionResult(
            "Purge standby memory", False,
            "Requires administrator — use 'Restart as administrator' first.")
    kernel32 = ctypes.windll.kernel32
    advapi = ctypes.windll.advapi32
    ntdll = ctypes.windll.ntdll

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", ctypes.c_uint32), ("HighPart", ctypes.c_int32)]

    class LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", LUID), ("Attributes", ctypes.c_uint32)]

    class TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [("PrivilegeCount", ctypes.c_uint32),
                    ("Privileges", LUID_AND_ATTRIBUTES * 1)]

    TOKEN_ADJUST_PRIVILEGES = 0x20
    TOKEN_QUERY = 0x8
    SE_PRIVILEGE_ENABLED = 0x2
    token = ctypes.c_void_p()
    kernel32.OpenProcessToken.argtypes = (
        ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p))
    if not kernel32.OpenProcessToken(kernel32.GetCurrentProcess(),
                                     TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
                                     ctypes.byref(token)):
        return ActionResult("Purge standby memory", False, "OpenProcessToken failed.")
    try:
        luid = LUID()
        if not advapi.LookupPrivilegeValueW(None, "SeProfileSingleProcessPrivilege",
                                            ctypes.byref(luid)):
            return ActionResult("Purge standby memory", False,
                                "LookupPrivilegeValue failed.")
        tp = TOKEN_PRIVILEGES(1, (LUID_AND_ATTRIBUTES * 1)(
            LUID_AND_ATTRIBUTES(luid, SE_PRIVILEGE_ENABLED)))
        advapi.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None)
    finally:
        kernel32.CloseHandle(token)

    before = psutil.virtual_memory().available
    SystemMemoryListInformation = 80
    cmd = ctypes.c_int(4)                       # MemoryPurgeStandbyList
    status = ntdll.NtSetSystemInformation(
        SystemMemoryListInformation, ctypes.byref(cmd), ctypes.sizeof(cmd))
    if status != 0:
        return ActionResult(
            "Purge standby memory", False,
            f"NtSetSystemInformation failed (0x{status & 0xFFFFFFFF:08X}).")
    time.sleep(0.5)
    gained = psutil.virtual_memory().available - before
    return ActionResult(
        "Purge standby memory", True,
        f"Standby list purged; available memory +{human_bytes(max(gained, 0))}.")


# -- power plan ------------------------------------------------------------------

BALANCED_GUID = "381b4222-f694-41f0-9685-ff5bb260df2e"
HIGH_PERF_GUID = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"


def active_power_plan():
    """Return (guid, name) of the active power scheme, or (None, '?')."""
    try:
        out = subprocess.run(["powercfg", "/getactivescheme"],
                             capture_output=True, text=True, timeout=15,
                             creationflags=CREATE_NO_WINDOW)
        m = re.search(r"([0-9a-fA-F-]{36})\s+\((.+)\)", out.stdout)
        if m:
            return m.group(1).lower(), m.group(2).strip()
    except OSError:
        pass
    return None, "?"


def toggle_power_plan() -> ActionResult:
    guid, name = active_power_plan()
    if guid is None:
        return ActionResult("Switch power plan", False,
                            "Could not read the active power scheme.")
    target, target_name = ((BALANCED_GUID, "Balanced")
                           if guid == HIGH_PERF_GUID
                           else (HIGH_PERF_GUID, "High performance"))
    r = subprocess.run(["powercfg", "/setactive", target],
                       capture_output=True, text=True, timeout=15,
                       creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0:
        return ActionResult(
            "Switch power plan", False,
            f"powercfg failed — this PC may not expose the {target_name} "
            "plan (modern-standby devices often hide it).")
    return ActionResult("Switch power plan", True,
                        f"Active plan: {name} → {target_name}.")


# -- shell / display ---------------------------------------------------------------

def restart_explorer() -> ActionResult:
    try:
        subprocess.run(["taskkill", "/F", "/IM", "explorer.exe"],
                       capture_output=True, timeout=30,
                       creationflags=CREATE_NO_WINDOW)
        subprocess.Popen(["explorer.exe"], creationflags=CREATE_NO_WINDOW,
                         close_fds=True)
        return ActionResult("Restart Explorer", True, "Explorer restarted.")
    except OSError as e:
        return ActionResult("Restart Explorer", False, str(e))


def restart_graphics_driver() -> ActionResult:
    """Send Win+Ctrl+Shift+B — Windows' built-in display-driver reset."""
    user32 = ctypes.windll.user32
    KEYEVENTF_KEYUP = 0x2
    keys = (0x5B, 0x11, 0x10, 0x42)      # LWIN, CTRL, SHIFT, B
    for vk in keys:
        user32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(keys):
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    return ActionResult("Restart graphics driver", True,
                        "Reset signal sent — the screen may flash briefly.")


def relaunch_as_admin() -> bool:
    """Start an elevated copy of the app (UAC prompt). Caller quits after."""
    if IS_FROZEN:
        # sys.executable IS the app; there is no script to pass
        params = None
    else:
        params = '"{}"'.format(os.path.join(APP_ROOT, "main.py"))
    r = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, params, APP_ROOT, 1)
    return r > 32


# -- process control ------------------------------------------------------------------

PRIORITY_CLASSES = {
    "Low": psutil.IDLE_PRIORITY_CLASS,
    "Below normal": psutil.BELOW_NORMAL_PRIORITY_CLASS,
    "Normal": psutil.NORMAL_PRIORITY_CLASS,
    "Above normal": psutil.ABOVE_NORMAL_PRIORITY_CLASS,
    "High": psutil.HIGH_PRIORITY_CLASS,
}


def end_process(pid: int, tree: bool = False) -> ActionResult:
    label = "End process tree" if tree else "End process"
    try:
        proc = psutil.Process(pid)
        name = proc.name()
        victims = (proc.children(recursive=True) if tree else []) + [proc]
        for v in victims:
            try:
                v.terminate()
            except psutil.Error:
                pass
        _gone, alive = psutil.wait_procs(victims, timeout=3)
        for v in alive:
            try:
                v.kill()
            except psutil.Error:
                pass
        return ActionResult(label, True,
                            f"{name} (PID {pid}) ended"
                            + (f" with {len(victims) - 1} children." if tree else "."))
    except psutil.NoSuchProcess:
        return ActionResult(label, True, f"PID {pid} already exited.")
    except psutil.AccessDenied:
        return ActionResult(label, False,
                            f"Access denied for PID {pid} — a system or "
                            "elevated process. Try 'Restart as administrator'.")
    except psutil.Error as e:
        return ActionResult(label, False, str(e))


def set_priority(pid: int, level: str) -> ActionResult:
    try:
        proc = psutil.Process(pid)
        proc.nice(PRIORITY_CLASSES[level])
        return ActionResult("Set priority", True,
                            f"{proc.name()} (PID {pid}) → {level}.")
    except psutil.NoSuchProcess:
        return ActionResult("Set priority", False, f"PID {pid} no longer exists.")
    except psutil.AccessDenied:
        return ActionResult("Set priority", False,
                            f"Access denied for PID {pid} — try "
                            "'Restart as administrator'.")
    except psutil.Error as e:
        return ActionResult("Set priority", False, str(e))
