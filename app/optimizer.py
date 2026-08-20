"""User-triggered cleanup actions.

Every function here runs only when the user clicks its button — the app
never optimizes automatically. Functions are synchronous; the UI executes
them on a worker thread and shows the returned ActionResult.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass

import psutil

from .util import human_bytes

TEMP_MIN_AGE_H = 24          # only delete temp files untouched for this long
CREATE_NO_WINDOW = 0x08000000


@dataclass
class ActionResult:
    name: str
    ok: bool
    message: str


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
    total = count = 0
    for root_dir in _temp_dirs():
        for root, _dirs, files in os.walk(root_dir, onerror=lambda e: None):
            for f in files:
                try:
                    st = os.stat(os.path.join(root, f))
                    if st.st_mtime < cutoff:
                        total += st.st_size
                        count += 1
                except OSError:
                    continue
    return total, count


def clear_temp() -> ActionResult:
    cutoff = time.time() - TEMP_MIN_AGE_H * 3600
    freed = deleted = skipped = 0
    for root_dir in _temp_dirs():
        for root, _dirs, files in os.walk(root_dir, topdown=False, onerror=lambda e: None):
            for f in files:
                path = os.path.join(root, f)
                try:
                    st = os.stat(path)
                    if st.st_mtime >= cutoff:
                        skipped += 1
                        continue
                    os.remove(path)
                    freed += st.st_size
                    deleted += 1
                except OSError:
                    skipped += 1
            if root != root_dir:
                try:
                    os.rmdir(root)          # removes only now-empty dirs
                except OSError:
                    pass
    return ActionResult(
        "Clear temp files", True,
        f"Deleted {deleted:,} files, freed {human_bytes(freed)}; "
        f"{skipped:,} skipped (recent or in use).")


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


# -- shell ----------------------------------------------------------------------

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
