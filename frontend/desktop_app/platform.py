"""Small Win32 integration layer: non-activating overlays and recovery hotkeys."""

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, Qt


def protect_overlay(widget, passthrough):
    widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    widget.setWindowFlag(Qt.WindowType.WindowTransparentForInput, passthrough)
    # Defense in depth: capture handshake hides the window even where this API fails.
    handle = int(widget.winId())
    try:
        affinity = ctypes.windll.user32.SetWindowDisplayAffinity
        affinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        affinity.restype = wintypes.BOOL
        excluded = bool(affinity(handle, 0x11)) and sys.getwindowsversion().build >= 19041
        widget.setProperty("captureExcluded", excluded)
        return excluded
    except OSError:
        widget.setProperty("captureExcluded", False)
        return False


def action_passthrough(widget, enabled):
    """Toggle layered-window hit testing without Qt recreating/hiding the window."""
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        getter = user32.GetWindowLongPtrW
        setter = user32.SetWindowLongPtrW
        getter.argtypes = [wintypes.HWND, ctypes.c_int]
        getter.restype = ctypes.c_ssize_t
        setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        setter.restype = ctypes.c_ssize_t
        handle = int(widget.winId())
        style = getter(handle, -20)
        style = style | 0x20 if enabled else style & ~0x20
        ctypes.set_last_error(0)
        setter(handle, -20, style)
        return ctypes.get_last_error() == 0
    except (OSError, AttributeError):
        return False


def overlaps_action(widget, params):
    points = []
    if params.get("loc"):
        points.append(params["loc"])
    points.extend(
        item[:2] for item in params.get("elements", []) if isinstance(item, list) and len(item) >= 2
    )
    if not points:
        return False
    rect = wintypes.RECT()
    function = ctypes.windll.user32.GetWindowRect
    function.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    if not function(int(widget.winId()), ctypes.byref(rect)):
        return True
    return any(rect.left <= x < rect.right and rect.top <= y < rect.bottom for x, y in points)


def mouse_buttons_down():
    return any(ctypes.windll.user32.GetAsyncKeyState(key) & 0x8000 for key in (1, 2, 4))


def release_agent_modifiers():
    # Emergency stop may interrupt a Ctrl+click or drag between down/up events.
    user32 = ctypes.windll.user32
    for key in (0x10, 0x11, 0x12, 0x5B, 0x5C):
        user32.keybd_event(key, 0, 0x0002, 0)
    for flag in (0x0004, 0x0010, 0x0040):
        user32.mouse_event(flag, 0, 0, 0, 0)


class Hotkeys(QAbstractNativeEventFilter):
    def __init__(self, show, stop):
        super().__init__()
        self.callbacks = {0x57A1: show, 0x57A2: stop}
        self.registered = []
        for identity, key in ((0x57A1, 0x20), (0x57A2, 0x1B)):
            if ctypes.windll.user32.RegisterHotKey(None, identity, 0x4000 | 0x2 | 0x1, key):
                self.registered.append(identity)

    def nativeEventFilter(self, event_type, message):
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == 0x0312 and msg.wParam in self.callbacks:
            self.callbacks[msg.wParam]()
            return True, 0
        return False, 0

    def close(self):
        for identity in self.registered:
            ctypes.windll.user32.UnregisterHotKey(None, identity)
