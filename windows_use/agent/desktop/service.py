import base64
import csv
import ctypes
import io
import logging
import os
import random
import re
import subprocess
from contextlib import contextmanager
from time import perf_counter, sleep
from typing import Literal

import requests
import win32con
import win32gui
import win32process
from fuzzywuzzy import process
from markdownify import markdownify
from PIL import Image, ImageDraw, ImageFont, ImageGrab
from psutil import Process

import windows_use.uia as uia
from windows_use.agent.desktop.config import KEY_ALIASES
from windows_use.agent.desktop.utils import escape_text_for_sendkeys
from windows_use.agent.desktop.views import Browser, DesktopState, Size, Status, Window
from windows_use.agent.tree.service import Tree
from windows_use.agent.tree.views import BoundingBox, TreeElementNode, TreeState
from windows_use.vdm.core import get_all_desktops, get_current_desktop, is_window_on_current_desktop

logger = logging.getLogger(__name__)


class Desktop:
    def authorize_action(self, name, params):
        from windows_use.execution import route

        mode = route(name, params)
        if mode == "background":
            return None
        if mode == "blocked":
            return "混合模式禁止任意 Shell、无范围文件及批量输入；请使用 workspace_tool 或逐步控件操作。"
        callback = getattr(self, "consent_callback", None)
        if callback is None:
            return "该操作需要用户桌面授权。当前入口没有授权界面，操作未执行。"
        return callback(name, params)

    def safety_keyboard_target_is_current(self, target):
        """Verify keyboard recipient independently of the mouse hit target."""
        state = self.desktop_state
        if (
            not state
            or not state.active_window
            or uia.GetForegroundWindow() != state.active_window.handle
        ):
            return False
        current = uia.GetFocusedControl()
        return bool(
            current
            and target.safety_identity
            and tuple(current.GetRuntimeId()) == target.safety_identity
            and (current.Name or "") == target.safety_name
        )

    def safety_target_is_current(self, target, loc):
        """Check the live hit target against the observed UIA control and geometry."""
        self.last_target_check = {
            "loc": list(loc),
            "target_name": target.safety_name or target.name,
        }

        def reject(reason, **details):
            self.last_target_check.update(reason=reason, **details)
            return False

        if target.control is None:
            return reject("missing_control")
        if self.desktop_state.active_window is None:
            return reject("missing_foreground_snapshot")
        foreground = uia.GetForegroundWindow()
        if foreground != self.desktop_state.active_window.handle:
            return reject(
                "foreground_changed",
                expected_window=self.desktop_state.active_window.handle,
                actual_window=foreground,
            )
        expected = target.safety_identity
        if not expected:
            return reject("missing_runtime_identity")
        current = uia.ControlFromPoint(*loc)
        hit_chain = []
        for _ in range(8):
            if current is None:
                return reject("hit_target_mismatch", hit_chain=hit_chain)
            identity = tuple(current.GetRuntimeId())
            hit_chain.append({"name": current.Name or "", "runtime_id": identity})
            if identity == expected:
                rect = current.BoundingRectangle
                if (current.Name or "") != target.safety_name:
                    return reject("name_changed", actual_name=current.Name)
                bounds = (rect.left, rect.top, rect.right, rect.bottom)
                if bounds != target.safety_bounds:
                    return reject(
                        "bounds_changed", expected_bounds=target.safety_bounds, actual_bounds=bounds
                    )
                self.last_target_check["reason"] = "verified"
                return True
            current = current.GetParentControl()
        return reject("hit_target_mismatch", hit_chain=hit_chain)

    def __init__(
        self, use_vision: bool = False, use_annotation: bool = False, use_accessibility: bool = True
    ):
        self.use_vision = use_vision
        self.use_annotation = use_annotation
        self.use_accessibility = use_accessibility
        self.tree = Tree(self)
        self.desktop_state = None
        self.ignored_process_ids = set()

        # Cached system info (does not change during session)
        self._cached_windows_version: str | None = None
        self._cached_default_language: str | None = None
        self._cached_user_account_type: str | None = None

    def warm_up(self):
        """Pre-warm expensive resources to eliminate cold start delay.

        Runs the 3 PowerShell commands (Windows version, language, account type) in parallel
        and caches their results. Also triggers UIA COM client initialization so the first
        actual agent step doesn't pay the ~1-2s comtypes typelib generation cost.
        """
        from concurrent.futures import ThreadPoolExecutor as _TPE

        def _warm_uia():
            try:
                uia.GetRootControl()
            except Exception:
                pass

        with _TPE(max_workers=4) as executor:
            executor.submit(self.get_windows_version)
            executor.submit(self.get_default_language)
            executor.submit(self.get_user_account_type)
            executor.submit(_warm_uia)

    def get_state(self, as_bytes: bool = False) -> DesktopState:
        start_time = perf_counter()

        controls_handles = self.get_controls_handles()  # Taskbar,Program Manager,Apps, Dialogs
        windows, windows_handles = self.get_windows(controls_handles=controls_handles)  # Apps
        active_window = self.get_active_window(windows=windows)  # Active Window
        active_window_handle = active_window.handle if active_window else None

        try:
            active_desktop = get_current_desktop()
            all_desktops = get_all_desktops()
        except RuntimeError:
            active_desktop = {
                "id": "00000000-0000-0000-0000-000000000000",
                "name": "Default Desktop",
            }
            all_desktops = [active_desktop]

        if active_window is not None and active_window in windows:
            windows.remove(active_window)

        logger.debug(f"Active window: {active_window or 'No Active Window Found'}")
        logger.debug(f"Windows: {windows}")

        # Preparing handles for Tree
        other_windows_handles = list(controls_handles - windows_handles)

        if self.use_accessibility:
            tree_state = self.tree.get_state(active_window_handle, other_windows_handles)
        else:
            tree_state = TreeState()

        if self.use_vision:
            if self.use_annotation:
                nodes = tree_state.interactive_nodes if tree_state else []
                if nodes:
                    screenshot = self.get_annotated_screenshot(nodes=nodes, as_bytes=as_bytes)
                else:
                    screenshot = self.get_screenshot(as_bytes=as_bytes)
            else:
                screenshot = self.get_screenshot(as_bytes=as_bytes)
        else:
            screenshot = None

        self.desktop_state = DesktopState(
            active_window=active_window,
            windows=windows,
            active_desktop=active_desktop,
            all_desktops=all_desktops,
            screenshot=screenshot,
            tree_state=tree_state,
        )

        end_time = perf_counter()
        logger.debug(f"[Desktop] Desktop State capture took {end_time - start_time:.2f} seconds")
        return self.desktop_state

    def get_window_status(self, control: uia.Control) -> Status:
        if uia.IsIconic(control.NativeWindowHandle):
            return Status.MINIMIZED
        elif uia.IsZoomed(control.NativeWindowHandle):
            return Status.MAXIMIZED
        elif uia.IsWindowVisible(control.NativeWindowHandle):
            return Status.NORMAL
        else:
            return Status.HIDDEN

    def get_cursor_location(self) -> tuple[int, int]:
        return uia.GetCursorPos()

    def get_element_under_cursor(self) -> uia.Control:
        return uia.ControlFromCursor()

    def get_apps_from_start_menu(self) -> dict[str, str]:
        command = "Get-StartApps | ConvertTo-Csv -NoTypeInformation"
        apps_info, status = self.execute_command(command)

        if status != 0 or not apps_info:
            logger.error(f"Failed to get apps from start menu: {apps_info}")
            return {}

        try:
            reader = csv.DictReader(io.StringIO(apps_info.strip()))
            return {
                row.get("Name").lower(): row.get("AppID")
                for row in reader
                if row.get("Name") and row.get("AppID")
            }
        except Exception as e:
            logger.error(f"Error parsing start menu apps: {e}")
            return {}

    def execute_command(self, command: str, timeout: int = 10) -> tuple[str, int]:
        try:
            utf8_command = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; " + command
            encoded = base64.b64encode(utf8_command.encode("utf-16le")).decode("ascii")
            result = subprocess.run(
                ["powershell", "-NoProfile", "-EncodedCommand", encoded],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=timeout,
                cwd=os.path.expanduser(path="~"),
            )
            stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
            stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
            return (stdout or stderr, result.returncode)
        except subprocess.TimeoutExpired:
            return ("Command execution timed out", 1)
        except Exception as e:
            return (f"Command execution failed: {type(e).__name__}: {e}", 1)

    def is_window_browser(self, node: uia.Control):
        """Give any node of the app and it will return True if the app is a browser, False otherwise."""
        process = Process(node.ProcessId)
        return Browser.has_process(process.name())

    def get_default_language(self) -> str:
        if self._cached_default_language is not None:
            return self._cached_default_language
        command = "Get-Culture | Select-Object Name,DisplayName | ConvertTo-Csv -NoTypeInformation"
        response, _ = self.execute_command(command)
        reader = csv.DictReader(io.StringIO(response))
        result = "".join([row.get("DisplayName") for row in reader])
        self._cached_default_language = result
        return result

    def _find_window_by_name(self, name: str) -> tuple["Window | None", str]:
        """Find a window by fuzzy name match. Returns (window, error_msg).
        If the returned window is None, error_msg describes the failure reason.
        """
        window_list = [
            w
            for w in [self.desktop_state.active_window] + self.desktop_state.windows
            if w is not None
        ]
        if not window_list:
            return None, "No windows found on the desktop."
        windows = {window.name: window for window in window_list}
        matched = process.extractOne(name, list(windows.keys()), score_cutoff=70)
        if matched is None:
            return None, f"Application {name.title()} not found."
        window_name, _ = matched
        return windows.get(window_name), ""

    def resize_app(
        self, name: str | None = None, size: tuple[int, int] = None, loc: tuple[int, int] = None
    ) -> tuple[str, int]:
        if name is not None:
            target_app, error = self._find_window_by_name(name)
            if target_app is None:
                return error, 1
        else:
            target_app = self.desktop_state.active_window
            if target_app is None:
                return "No active app found", 1

        if target_app.status == Status.MINIMIZED:
            return f"{target_app.name} is minimized", 1
        elif target_app.status == Status.MAXIMIZED:
            return f"{target_app.name} is maximized", 1
        else:
            app_control = uia.ControlFromHandle(target_app.handle)
            if loc is None:
                x = app_control.BoundingRectangle.left
                y = app_control.BoundingRectangle.top
                loc = (x, y)
            if size is None:
                width = app_control.BoundingRectangle.width()
                height = app_control.BoundingRectangle.height()
                size = (width, height)
            x, y = loc
            width, height = size
            app_control.MoveWindow(x, y, width, height)
            return (f"{target_app.name} resized to {width}x{height} at {x},{y}.", 0)

    def is_app_running(self, name: str) -> bool:
        apps, _ = self.get_windows()
        apps_dict = {app.name: app for app in apps}
        return process.extractOne(name, list(apps_dict.keys()), score_cutoff=60) is not None

    def app(
        self,
        mode: Literal["launch", "switch", "resize"],
        name: str | None = None,
        loc: tuple[int, int] | None = None,
        size: tuple[int, int] | None = None,
    ):
        match mode:
            case "launch":
                response, status, pid = self.launch_app(name)
                if status != 0:
                    return response

                # Smart wait using UIA Exists (avoids manual Python loops)
                launched = False
                try:
                    if pid > 0:
                        if uia.WindowControl(ProcessId=pid).Exists(maxSearchSeconds=10):
                            launched = True

                    if not launched:
                        # Fallback: Regex search for the window title
                        safe_name = re.escape(name)
                        if uia.WindowControl(RegexName=f"(?i).*{safe_name}.*").Exists(
                            maxSearchSeconds=10
                        ):
                            launched = True
                except Exception as e:
                    logger.warning(f"Error verifying app launch (likely transient COM error): {e}")
                    # Assume launched if we got this far without launch_app failing,
                    # as the subprocess call succeeded.
                    launched = True

                if launched:
                    return f"{name.title()} launched."
                return f"Launching {name.title()} sent, but window not detected yet."
            case "resize":
                response, status = self.resize_app(name=name, size=size, loc=loc)
                if status != 0:
                    return response
                else:
                    return response
            case "switch":
                response, status = self.switch_app(name)
                if status != 0:
                    raise RuntimeError(response)
                else:
                    return response

    def launch_app(self, name: str) -> tuple[str, int, int]:
        apps_map = self.get_apps_from_start_menu()
        matched_app = process.extractOne(name, apps_map.keys(), score_cutoff=70)
        if matched_app is None:
            return (f"{name.title()} not found in start menu.", 1, 0)
        app_name, _ = matched_app
        appid = apps_map.get(app_name)
        if appid is None:
            return (name, f"{name.title()} not found in start menu.", 1, 0)

        pid = 0
        if os.path.exists(appid) or "\\" in appid:
            # It's a file path, we can try to get the PID using PassThru
            command = f'Start-Process "{appid}" -PassThru | Select-Object -ExpandProperty Id'
            response, status = self.execute_command(command)
            if status == 0 and response.strip().isdigit():
                pid = int(response.strip())
        else:
            # It's an AUMID (Store App)
            command = f'Start-Process "shell:AppsFolder\\{appid}"'
            response, status = self.execute_command(command)

        return response, status, pid

    def switch_app(self, name: str):
        app, error = self._find_window_by_name(name)
        if app is None:
            return error, 1

        target_handle = app.handle
        if uia.GetForegroundWindow() == target_handle:
            return (
                f"'{app.name}' is already the foreground window. No switch was needed; inspect its current controls instead of switching again.",
                0,
            )
        was_minimized = uia.IsIconic(target_handle)
        self.bring_window_to_top(target_handle)
        deadline = perf_counter() + 1.0
        while uia.GetForegroundWindow() != target_handle and perf_counter() < deadline:
            sleep(0.05)
        if uia.GetForegroundWindow() != target_handle:
            if self.activate_visible_caption(target_handle):
                deadline = perf_counter() + 1.0
                while uia.GetForegroundWindow() != target_handle and perf_counter() < deadline:
                    sleep(0.05)
        if uia.GetForegroundWindow() != target_handle:
            return (
                f"Window switch failed: '{app.name}' did not become the foreground window. Observe again before acting.",
                1,
            )
        if was_minimized:
            content = f"{app.name.title()} restored from minimized and switched to it."
        else:
            content = f"Switched to {app.name.title()} window."
        return content, 0

    def activate_visible_caption(self, target_handle):
        """Click only a verified visible non-client title bar, never arbitrary page content."""
        try:
            left, top, right, bottom = win32gui.GetWindowRect(target_handle)
            width = right - left
            if width < 160 or bottom <= top:
                return False
            hit_test = ctypes.windll.user32.SendMessageTimeoutW
            hit_test.argtypes = [
                ctypes.wintypes.HWND,
                ctypes.wintypes.UINT,
                ctypes.wintypes.WPARAM,
                ctypes.wintypes.LPARAM,
                ctypes.wintypes.UINT,
                ctypes.wintypes.UINT,
                ctypes.POINTER(ctypes.c_size_t),
            ]
            hit_test.restype = ctypes.c_void_p
            for x in (left + width // 2, left + width // 4, right - 160):
                for y in (top + 8, top + 16):
                    under = win32gui.WindowFromPoint((x, y))
                    if win32gui.GetAncestor(under, 2) != target_handle:
                        continue
                    result = ctypes.c_size_t()
                    position = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
                    if (
                        hit_test(target_handle, 0x84, 0, position, 0x2, 150, ctypes.byref(result))
                        and result.value == 2
                    ):
                        uia.Click(x, y, waitTime=0.1)
                        return True
        except Exception as error:
            logger.debug("Visible caption activation unavailable: %s", error)
        return False

    def bring_window_to_top(self, target_handle: int):
        if not win32gui.IsWindow(target_handle):
            raise ValueError("Invalid window handle")
        try:
            if win32gui.IsIconic(target_handle):
                # Avoid coupling this worker's input queue to an unresponsive application.
                show = ctypes.windll.user32.ShowWindowAsync
                show.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
                show(target_handle, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(target_handle)
            win32gui.BringWindowToTop(target_handle)
        except Exception as error:
            # Foreground restrictions are normal. switch_app verifies the result and
            # may activate a visible, hit-tested title bar with a real mouse click.
            logger.debug("Foreground request denied: %s", error)

    def get_element_handle_from_label(self, label: int) -> uia.Control:
        tree_state = self.desktop_state.tree_state
        selector = tree_state.build_selector_map()
        element_node = selector.node_of(label)
        if element_node is None:
            raise ValueError(f"No element found for label {label}")
        control = selector.control_of(label)
        if control is not None:
            return control
        if element_node.hwnd:
            return uia.ControlFromHandle(element_node.hwnd)
        raise ValueError(f"No live control available for label {label}")

    def get_coordinates_from_label(self, label: int) -> tuple[int, int]:
        element_handle = self.get_element_handle_from_label(label)
        bounding_rectangle = element_handle.BoundingRectangle
        return bounding_rectangle.xcenter(), bounding_rectangle.ycenter()

    def click(self, loc: tuple[int, int], button: str = "left", clicks: int = 2):
        x, y = loc
        if clicks == 0:
            uia.SetCursorPos(x, y)
            return
        match button:
            case "left":
                if clicks >= 2:
                    uia.DoubleClick(x, y)
                else:
                    uia.Click(x, y)
            case "right":
                for _ in range(clicks):
                    uia.RightClick(x, y)
            case "middle":
                for _ in range(clicks):
                    uia.MiddleClick(x, y)

    def type(
        self,
        loc: tuple[int, int],
        text: str,
        caret_position: Literal["start", "end", "none"] = "none",
        clear: Literal["true", "false"] = "false",
        press_enter: Literal["true", "false"] = "false",
    ):
        x, y = loc
        uia.Click(x, y)
        if caret_position == "start":
            uia.SendKeys("{Home}", waitTime=0.05)
        elif caret_position == "end":
            uia.SendKeys("{End}", waitTime=0.05)
        if clear == "true":
            sleep(0.5)
            uia.SendKeys("{Ctrl}a", waitTime=0.05)
            uia.SendKeys("{Back}", waitTime=0.05)
        escaped_text = escape_text_for_sendkeys(text)
        uia.SendKeys(escaped_text, interval=0.01, waitTime=0.05)
        if press_enter == "true":
            uia.SendKeys("{Enter}", waitTime=0.05)

    def scroll(
        self,
        loc: tuple[int, int] = None,
        type: Literal["horizontal", "vertical"] = "vertical",
        direction: Literal["up", "down", "left", "right"] = "down",
        wheel_times: int = 1,
    ) -> str | None:
        if loc:
            self.move(loc)
        match type:
            case "vertical":
                match direction:
                    case "up":
                        uia.WheelUp(wheel_times)
                    case "down":
                        uia.WheelDown(wheel_times)
                    case _:
                        return 'Invalid direction. Use "up" or "down".'
            case "horizontal":
                match direction:
                    case "left":
                        uia.WheelLeft(wheel_times)
                    case "right":
                        uia.WheelRight(wheel_times)
                    case _:
                        return 'Invalid direction. Use "left" or "right".'
            case _:
                return 'Invalid type. Use "horizontal" or "vertical".'
        return None

    def drag(self, loc: tuple[int, int]):
        x, y = loc
        cx, cy = uia.GetCursorPos()
        uia.DragTo(cx, cy, x, y)

    def move(self, loc: tuple[int, int]):
        x, y = loc
        uia.MoveTo(x, y, moveSpeed=10)

    def shortcut(self, shortcut: str):
        keys = shortcut.split("+")
        sendkeys_str = ""
        for key in keys:
            key = key.strip()
            if len(key) == 1:
                # Single character key (a-z, 0-9, etc.)
                sendkeys_str += key
            else:
                # Named key - resolve alias then wrap in braces for SendKeys
                name = KEY_ALIASES.get(key.lower(), key)
                sendkeys_str += "{" + name + "}"
        uia.SendKeys(sendkeys_str, interval=0.01)

    def multi_select(
        self,
        press_ctrl: Literal["true", "false"] = "false",
        elements: list[tuple[int, int] | int] = [],
    ):
        if press_ctrl == "true":
            uia.PressKey(uia.Keys.VK_CONTROL, waitTime=0.05)
        for element in elements:
            x, y = element
            uia.Click(x, y, waitTime=0.2)
            sleep(0.5)
        uia.ReleaseKey(uia.Keys.VK_CONTROL, waitTime=0.05)

    def multi_edit(self, elements: list[tuple[int, int, str] | tuple[int, str]]):
        for element in elements:
            x, y, text = element
            self.type((x, y), text=text, clear="true")

    def scrape(self, url: str) -> str:
        response = requests.get(url, timeout=10)
        html = response.text
        content = markdownify(html=html)
        return content

    def is_window_visible(self, window: uia.Control) -> bool:
        is_minimized = self.get_window_status(window) != Status.MINIMIZED
        size = window.BoundingRectangle
        area = size.width() * size.height()
        is_overlay = self.is_overlay_window(window)
        return not is_overlay and is_minimized and area > 10

    def is_overlay_window(self, element: uia.Control) -> bool:
        no_children = element.GetFirstChildControl() is None
        is_name = "Overlay" in element.Name.strip()
        return no_children or is_name

    def get_controls_handles(self, optimized: bool = False):
        handles = set()

        def callback(hwnd, _):
            if win32gui.IsWindowVisible(hwnd) and is_window_on_current_desktop(hwnd):
                if self.is_ignored_window(hwnd):
                    return
                handles.add(hwnd)

        win32gui.EnumWindows(callback, None)

        if desktop_hwnd := win32gui.FindWindow("Progman", None):
            handles.add(desktop_hwnd)
        if taskbar_hwnd := win32gui.FindWindow("Shell_TrayWnd", None):
            handles.add(taskbar_hwnd)
        if secondary_taskbar_hwnd := win32gui.FindWindow("Shell_SecondaryTrayWnd", None):
            handles.add(secondary_taskbar_hwnd)
        return handles

    def is_ignored_window(self, handle):
        ignored = getattr(self, "ignored_process_ids", set())
        if not ignored:
            return False
        try:
            return win32process.GetWindowThreadProcessId(handle)[1] in ignored
        except Exception:
            return False

    def get_active_window(self, windows: list[Window] | None = None) -> Window | None:
        try:
            if windows is None:
                windows, _ = self.get_windows()
            active_window = self.get_foreground_window()
            if active_window.ClassName == "Progman":
                return None
            active_window_handle = active_window.NativeWindowHandle
            if self.is_ignored_window(active_window_handle):
                return None
            for window in windows:
                if window.handle != active_window_handle:
                    continue
                return window
            # In case active window is not present in the windows list
            return Window(
                **{
                    "name": active_window.Name,
                    "is_browser": self.is_window_browser(active_window),
                    "depth": 0,
                    "bounding_box": BoundingBox(
                        left=active_window.BoundingRectangle.left,
                        top=active_window.BoundingRectangle.top,
                        right=active_window.BoundingRectangle.right,
                        bottom=active_window.BoundingRectangle.bottom,
                        width=active_window.BoundingRectangle.width(),
                        height=active_window.BoundingRectangle.height(),
                    ),
                    "status": self.get_window_status(active_window),
                    "handle": active_window_handle,
                    "process_id": active_window.ProcessId,
                }
            )
        except Exception as ex:
            logger.error(f"Error in get_active_window: {ex}")
        return None

    def get_foreground_window(self) -> uia.Control:
        handle = uia.GetForegroundWindow()
        active_window = self.get_window_from_element_handle(handle)
        return active_window

    def get_window_from_element_handle(self, element_handle: int) -> uia.Control:
        current = uia.ControlFromHandle(element_handle)
        root_handle = uia.GetRootControl().NativeWindowHandle

        while True:
            parent = current.GetParentControl()
            if parent is None or parent.NativeWindowHandle == root_handle:
                return current
            current = parent

    def get_windows(
        self, controls_handles: set[int] | None = None
    ) -> tuple[list[Window], set[int]]:
        try:
            windows = []
            window_handles = set()
            controls_handles = controls_handles or self.get_controls_handles()
            for depth, hwnd in enumerate(controls_handles):
                try:
                    child = uia.ControlFromHandle(hwnd)
                except Exception:
                    continue

                # Filter out Overlays (e.g. NVIDIA, Steam)
                if self.is_overlay_window(child):
                    continue

                if isinstance(child, uia.WindowControl | uia.PaneControl):
                    window_pattern = child.GetPattern(uia.PatternId.WindowPattern)
                    if window_pattern is None:
                        continue

                    if window_pattern.CanMinimize and window_pattern.CanMaximize:
                        status = self.get_window_status(child)

                        bounding_rect = child.BoundingRectangle
                        if bounding_rect.isempty() and status != Status.MINIMIZED:
                            continue

                        windows.append(
                            Window(
                                **{
                                    "name": child.Name,
                                    "depth": depth,
                                    "status": status,
                                    "bounding_box": BoundingBox(
                                        left=bounding_rect.left,
                                        top=bounding_rect.top,
                                        right=bounding_rect.right,
                                        bottom=bounding_rect.bottom,
                                        width=bounding_rect.width(),
                                        height=bounding_rect.height(),
                                    ),
                                    "handle": child.NativeWindowHandle,
                                    "process_id": child.ProcessId,
                                    "is_browser": self.is_window_browser(child),
                                }
                            )
                        )
                        window_handles.add(child.NativeWindowHandle)
        except Exception as ex:
            logger.error(f"Error in get_windows: {ex}")
            windows = []
        return windows, window_handles

    def get_windows_version(self) -> str:
        if self._cached_windows_version is not None:
            return self._cached_windows_version
        response, status = self.execute_command("(Get-CimInstance Win32_OperatingSystem).Caption")
        result = response.strip() if status == 0 else "Windows"
        self._cached_windows_version = result
        return result

    def get_user_account_type(self) -> str:
        if self._cached_user_account_type is not None:
            return self._cached_user_account_type
        response, status = self.execute_command(
            "(Get-LocalUser -Name $env:USERNAME).PrincipalSource"
        )
        result = (
            "Local Account"
            if response.strip() == "Local"
            else "Microsoft Account"
            if status == 0
            else "Local Account"
        )
        self._cached_user_account_type = result
        return result

    def get_dpi_scaling(self):
        user32 = ctypes.windll.user32
        dpi = user32.GetDpiForSystem()
        return dpi / 96.0

    def get_screen_size(self) -> Size:
        width, height = uia.GetVirtualScreenSize()
        return Size(width=width, height=height)

    def describe_displays(self):
        left, top, width, height = uia.GetVirtualScreenRect()
        lines = [
            f"Virtual screen physical bounds: ({left}, {top}) to ({left + width}, {top + height})."
        ]
        for index, rect in enumerate(uia.GetMonitorsRect(), 1):
            lines.append(
                f"Monitor {index}: ({rect.left}, {rect.top}) to ({rect.right}, {rect.bottom})."
            )
        lines.append(
            f"All tool/UIA coordinates are global PHYSICAL desktop pixels, including negative coordinates. "
            f"The full-desktop screenshot origin is ({left}, {top}), not necessarily (0, 0). "
            "Do not guess primary-monitor coordinates for secondary-monitor windows. Prefer observed UIA coordinates; "
            "if an image is resized, rescale its pixels to the original desktop size before adding the screenshot origin."
        )
        if self.use_annotation:
            lines.append(
                "Annotated screenshots additionally have 5 pixels of padding at the top and left."
            )
        if self.desktop_state:
            for window in [self.desktop_state.active_window, *self.desktop_state.windows]:
                if window:
                    box = window.bounding_box
                    lines.append(
                        f"Window {window.handle} ({window.name}): ({box.left}, {box.top}) to ({box.right}, {box.bottom})."
                    )
        return "\n".join(lines)

    def get_screenshot(self, as_bytes: bool = False) -> bytes | Image.Image:
        try:
            screenshot = ImageGrab.grab(all_screens=True)
        except Exception:
            logger.warning("Failed to capture virtual screen, using primary screen")
            screenshot = ImageGrab.grab()
        if as_bytes:
            buffered = io.BytesIO()
            screenshot.save(buffered, format="PNG")
            screenshot = buffered.getvalue()
            buffered.close()
        return screenshot

    def get_annotated_screenshot(
        self, nodes: list[TreeElementNode], as_bytes: bool = False
    ) -> bytes | Image.Image:
        screenshot = self.get_screenshot()
        # Add padding
        padding = 5
        width = int(screenshot.width + (1.5 * padding))
        height = int(screenshot.height + (1.5 * padding))
        padded_screenshot = Image.new("RGB", (width, height), color=(255, 255, 255))
        padded_screenshot.paste(screenshot, (padding, padding))

        draw = ImageDraw.Draw(padded_screenshot)
        font_size = 12
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()

        def get_random_color():
            return f"#{random.randint(0, 0xFFFFFF):06x}"

        left_offset, top_offset, _, _ = uia.GetVirtualScreenRect()

        def draw_annotation(label: int, node: TreeElementNode):
            box = node.bounding_box
            color = get_random_color()

            # Scale and pad the bounding box also clip the bounding box
            # Adjust for virtual screen offset so coordinates map to the screenshot image
            adjusted_box = (
                int(box.left - left_offset) + padding,
                int(box.top - top_offset) + padding,
                int(box.right - left_offset) + padding,
                int(box.bottom - top_offset) + padding,
            )
            # Draw bounding box
            draw.rectangle(adjusted_box, outline=color, width=2)

            # Label dimensions
            label_width = draw.textlength(str(label), font=font)
            label_height = font_size
            left, top, right, bottom = adjusted_box

            # Label position above bounding box
            label_x1 = right - label_width
            label_y1 = top - label_height - 4
            label_x2 = label_x1 + label_width
            label_y2 = label_y1 + label_height + 4

            # Draw label background and text
            draw.rectangle([(label_x1, label_y1), (label_x2, label_y2)], fill=color)
            draw.text((label_x1 + 2, label_y1 + 2), str(label), fill=(255, 255, 255), font=font)

        for label, node in enumerate(nodes):
            draw_annotation(label, node)

        if as_bytes:
            buffered = io.BytesIO()
            padded_screenshot.save(buffered, format="PNG")
            padded_screenshot = buffered.getvalue()
            buffered.close()
        return padded_screenshot

    @contextmanager
    def auto_minimize(self):
        try:
            handle = uia.GetForegroundWindow()
            uia.ShowWindow(handle, win32con.SW_MINIMIZE)
            yield
        finally:
            uia.ShowWindow(handle, win32con.SW_RESTORE)
