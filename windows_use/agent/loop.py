from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, deque

from windows_use.agent.desktop.views import DesktopState

REASONING_TOOLS = {
    "consult_brain",
    "recall_history",
    "read_task_history",
    "read_trace_entry",
    "suggest_preference",
    "scrape_tool",
    "memory_tool",
}
_EXEMPT = {"done_tool", "wait_tool"} | REASONING_TOOLS
_IGNORE_PARAMS = {"thought"}


class ProgressHalt(RuntimeError):
    """A deterministic no-progress stop, not a retryable provider/network error."""

    error_source = "no_progress"


class ModelConfigurationError(ProgressHalt):
    """Non-retryable provider configuration error, with the responsible role identified."""

    error_source = "model_configuration"


def semantic_scene(desktop_state):
    """Ignore taskbar clocks, tab RAM counters, element IDs and focus-only changes."""
    active = getattr(desktop_state, "active_window", None)
    tree = getattr(desktop_state, "tree_state", None)
    window_name = str(getattr(active, "name", ""))
    handle = getattr(active, "handle", 0)
    if not isinstance(handle, int):
        handle = str(handle)
    rows = []
    for node in getattr(tree, "interactive_nodes", None) or []:
        if active and node.window_name != window_name and getattr(node, "hwnd", 0) != handle:
            continue
        name = re.sub(r"\s*-\s*(?:内存使用率|Memory usage).*", "", node.name, flags=re.I)
        meta = getattr(node, "metadata", {})
        values = {
            key: meta[key]
            for key in ("value", "toggle_state", "selected", "expand_collapse_state")
            if key in meta
        }
        rows.append(
            (node.control_type, name, json.dumps(values, sort_keys=True, ensure_ascii=False))
        )
    raw = json.dumps([handle, window_name, sorted(rows)], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


_REPEAT_NUDGES = {
    3: "You have repeated the same action {n} times — make sure it is actually making progress.",
    5: "Same action repeated {n} times. If you are stuck, try a different approach.",
    8: "Same action repeated {n} times. Stop and take a completely different strategy.",
}


class LoopGuard:
    """Watches for signs that the agent is looping:

    - **Action repetition**: the same tool + params appearing too often
      in the last `window` steps.
    - **UI stagnation**: the desktop state (active window, interactive elements)
      not changing across consecutive steps, meaning actions have no visible effect.
    - **State cycle**: the agent returning to a UI state it has already
      visited (e.g. A→B→A), which consecutive-stagnation cannot catch.
    - **Failed action retry**: the agent calling the exact same action
      that just failed, unchanged, which will produce the same failure.

    Call ``check()`` before each LLM step to get a warning string (or
    None), ``record_action()`` after executing a tool, and
    ``record_state()`` after capturing desktop state.
    """

    def __init__(self, window: int = 15) -> None:
        self._hashes: deque[str] = deque(maxlen=window)
        self._last_state: tuple[str, str] | None = None  # (window_id, content_hash)
        self._stagnant = 0
        # State cycle detection
        self._visited_states: dict[str, int] = {}  # fingerprint → visit count
        self._cycle_warning: str = ""
        # Failed action retry detection
        self._last_action_key: str | None = None
        self._last_action_failed: bool = False
        self._failed_retry_warning: str = ""
        self._last_tool = None

    def record_action(self, tool: str, params: dict, is_success: bool = True) -> None:
        self._last_tool = tool
        if tool in _EXEMPT:
            return
        filtered = {k: v for k, v in params.items() if k not in _IGNORE_PARAMS}
        normalised = {
            k: v.strip().lower() if isinstance(v, str) else v for k, v in filtered.items()
        }
        raw = json.dumps({tool: normalised}, sort_keys=True).encode()
        key = hashlib.sha256(raw).hexdigest()[:12]
        self._hashes.append(key)

        # Failed action retry: same key as last action and last action failed
        if not is_success and self._last_action_key == key and self._last_action_failed:
            self._failed_retry_warning = (
                f"You are retrying '{tool}' with the same parameters after it already failed. "
                "The same action will produce the same failure — change your approach, "
                "try different parameters, or use a different tool."
            )
        else:
            self._failed_retry_warning = ""

        self._last_action_key = key
        self._last_action_failed = not is_success

    def record_state(self, desktop_state: DesktopState) -> None:
        """Record the current desktop state as a fingerprint.

        Uses window handle (HWND) as the window identity and interactive elements
        string as the content indicator.
        """
        if self._last_tool in REASONING_TOOLS | {"wait_tool"}:
            self._cycle_warning = ""
            return
        # Window identity: use HWND (handle) as stable window identifier
        if desktop_state.active_window:
            window_id = f"{desktop_state.active_window.handle}"
        else:
            window_id = "no_window"

        # Content indicator: use interactive elements string if available
        digest = semantic_scene(desktop_state)
        state = (window_id, digest)

        if state == self._last_state:
            self._stagnant += 1
        else:
            self._stagnant = 0
        self._last_state = state

        # State cycle detection: track all-time visits to this state
        fingerprint = f"{window_id}|{digest}"
        count = self._visited_states.get(fingerprint, 0) + 1
        self._visited_states[fingerprint] = count
        if len(self._visited_states) > 100:
            del self._visited_states[next(iter(self._visited_states))]
        if count == 2:
            self._cycle_warning = (
                "You have returned to a UI state you already visited. "
                "You may be cycling between states. Try a completely different approach "
                "instead of repeating the same steps."
            )
        elif count >= 3:
            self._cycle_warning = (
                f"You have returned to this state {count} times. "
                "You are stuck in a loop — stop and reconsider your strategy entirely. "
                "Try an alternative method or approach."
            )
        else:
            self._cycle_warning = ""

    def check(self) -> str | None:
        warnings: list[str] = []

        if self._hashes:
            top = Counter(self._hashes).most_common(1)[0][1]
            for threshold in sorted(_REPEAT_NUDGES, reverse=True):
                if top >= threshold:
                    warnings.append(_REPEAT_NUDGES[threshold].format(n=top))
                    break

        if self._stagnant >= 4:
            warnings.append(
                f"The UI has not changed for {self._stagnant} steps. "
                "Your actions may not be having any effect. "
                "If waiting for something external (OTP, email, slow load) "
                "use wait_tool first, then re-check."
            )

        if self._cycle_warning:
            warnings.append(self._cycle_warning)

        if self._failed_retry_warning:
            warnings.append(self._failed_retry_warning)

        return "\n\n".join(warnings) or None

    def reset(self) -> None:
        self._last_tool = None
        self._hashes.clear()
        self._last_state = None
        self._stagnant = 0
        self._visited_states = {}
        self._cycle_warning = ""
        self._last_action_key = None
        self._last_action_failed = False
        self._failed_retry_warning = ""
