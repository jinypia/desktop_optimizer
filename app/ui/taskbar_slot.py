"""Find a place to dock the mini strip inside the Windows taskbar.

Windows offers no supported way to put custom UI *inside* the taskbar
(deskbands were removed after Windows 10), so the strip is an always-on-top
window positioned over the taskbar band instead — visually it reads as part
of it.

Where exactly: vertically centred in the taskbar, horizontally ending just
before the notification area, i.e. immediately left of the tray icons and
clock. On Windows 11 the icons and the clock are drawn by XAML inside one
`TrayNotifyWnd` block (the legacy `TrayClockWClass` / `SysPager` windows are
gone or zero-sized), so there is no gap *between* them to sit in — left of
the whole cluster is as close as the OS allows.

All Win32 rects are physical pixels; Qt positions windows in logical
pixels, so everything is divided by the screen's device pixel ratio.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QPoint, QRect, QSize

from .shellguard import guard

GAP_PX = 8              # logical px between the strip and the tray cluster
MIN_TASKBAR_PX = 24     # ignore an auto-hidden / collapsed taskbar

_u = ctypes.windll.user32
_u.FindWindowW.restype = wintypes.HWND
_u.FindWindowExW.restype = wintypes.HWND
_u.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
_u.IsWindow.argtypes = (wintypes.HWND,)

# These belong to explorer.exe, so every lookup is a cross-process call.
# Cache the handles and only re-find them when they stop being valid.
_cached = {"bar": None, "cluster": None}


def _rect(hwnd) -> QRect | None:
    if not hwnd:
        return None
    r = wintypes.RECT()
    if not guard.call("GetWindowRect", _u.GetWindowRect, hwnd, ctypes.byref(r)):
        return None
    return QRect(r.left, r.top, r.right - r.left, r.bottom - r.top)


def _taskbar_hwnd():
    hwnd = _cached["bar"]
    if hwnd and _u.IsWindow(hwnd):
        return hwnd
    hwnd = guard.call("FindWindow Shell_TrayWnd",
                      _u.FindWindowW, "Shell_TrayWnd", None)
    _cached["bar"] = hwnd
    _cached["cluster"] = None       # child handles belong to this parent
    return hwnd


def _cluster_hwnd():
    hwnd = _cached["cluster"]
    if hwnd and _u.IsWindow(hwnd):
        return hwnd
    bar = _taskbar_hwnd()
    if not bar:
        return None
    hwnd = guard.call("FindWindowEx TrayNotifyWnd", _u.FindWindowExW,
                      bar, None, "TrayNotifyWnd", None)
    _cached["cluster"] = hwnd
    return hwnd


def taskbar_rect() -> QRect | None:
    """Physical-pixel rect of the primary taskbar."""
    return _rect(_taskbar_hwnd())


def tray_cluster_rect() -> QRect | None:
    """Physical-pixel rect of the notification area (icons + clock)."""
    return _rect(_cluster_hwnd())


def dock_position(size: QSize, screen) -> QPoint | None:
    """Top-left (logical px) for a strip of `size` docked in the taskbar.

    Returns None when there is no usable slot — vertical taskbar, hidden
    taskbar, or not enough room — so the caller can fall back to floating.
    """
    bar = taskbar_rect()
    if bar is None or screen is None:
        return None

    dpr = screen.devicePixelRatio() or 1.0
    logical = QRect(round(bar.left() / dpr), round(bar.top() / dpr),
                    round(bar.width() / dpr), round(bar.height() / dpr))

    # Only horizontal taskbars have room for a wide strip.
    if logical.height() < MIN_TASKBAR_PX or logical.width() < logical.height() * 3:
        return None

    # Use the part of the bar actually on screen: an auto-hidden taskbar
    # leaves a sliver behind, and following it off screen would hide the
    # strip too.
    band = logical.intersected(screen.geometry())
    if band.height() < size.height():
        return None
    if band.width() < size.width() + GAP_PX * 2:
        return None

    cluster = tray_cluster_rect()
    right_edge = (cluster.left() / dpr if cluster is not None
                  else band.right() + 1)
    x = right_edge - GAP_PX - size.width()
    y = band.top() + (band.height() - size.height()) / 2.0

    # Refuse to run off the left end of the taskbar (tiny screens).
    if x < band.left() + GAP_PX:
        return None
    return QPoint(int(round(x)), int(round(y)))
