"""Deterministic execution routing. No model can grant desktop control."""

import json
import re
import time
import uuid
from pathlib import Path

BACKGROUND = frozenset(
    {
        "done_tool",
        "wait_tool",
        "timer_tool",
        "recall_history",
        "read_task_history",
        "read_trace_entry",
        "consult_brain",
        "suggest_preference",
        "workspace_tool",
        "scrape_tool",
    }
)
BLOCKED = frozenset(
    {"shell_tool", "multi_edit_tool", "multi_select_tool", "file_tool", "memory_tool"}
)


def route(name, params):
    if name in BLOCKED:
        return "blocked"
    if name in BACKGROUND or (name == "ui_control_tool" and params.get("operation") == "inspect"):
        return "background"
    return "foreground"


class ConsentBroker:
    """One exact action per approval; tokens never survive a worker restart."""

    def __init__(self, emit, cancelled, timeout=120):
        import threading

        self.emit, self.cancelled, self.timeout = emit, cancelled, timeout
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.pending = None
        self.answer = False

    def resolve(self, message):
        with self.lock:
            if (
                self.ready.is_set()
                or not self.pending
                or message.get("request_id") != self.pending["request_id"]
            ):
                return
            if time.monotonic() > self.pending["deadline"]:
                return
            self.answer = message.get("approved") is True
            self.ready.set()

    def revoke(self):
        with self.lock:
            self.answer = False
            self.ready.set()

    def request(self, tool, params, target):
        approved = False
        with self.lock:
            self.ready.clear()
            self.answer = False
            self.pending = {
                "request_id": uuid.uuid4().hex,
                "tool": tool,
                "params": params,
                "target": target,
                "deadline": time.monotonic() + self.timeout,
            }
            request = dict(self.pending)
        self.emit("consent_requested", {k: v for k, v in request.items() if k != "deadline"})
        try:
            while not self.ready.wait(0.05):
                if self.cancelled.is_set() or time.monotonic() >= request["deadline"]:
                    return False
            with self.lock:
                approved = (
                    self.answer
                    and not self.cancelled.is_set()
                    and time.monotonic() < request["deadline"]
                )
                return approved
        finally:
            with self.lock:
                self.pending = None
            self.emit("consent_closed", {"request_id": request["request_id"], "approved": approved})


def observation_identity(state):
    """Include geometry and focus: approval is invalid if the target scene moved."""
    active = getattr(state, "active_window", None)
    rows = []
    for node in getattr(getattr(state, "tree_state", None), "interactive_nodes", []) or []:
        name = re.sub(r"\s*-\s*(?:内存使用率|Memory usage).*", "", node.name, flags=re.I)
        rows.append(
            (
                name,
                node.hwnd,
                getattr(node, "safety_identity", None),
                getattr(node, "safety_bounds", None),
                node.control_type,
                node.center.x,
                node.center.y,
                node.metadata.get("value"),
                node.metadata.get("has_focused"),
            )
        )
    windows = [(window.handle, window.name) for window in getattr(state, "windows", []) or []]
    return json.dumps([getattr(active, "handle", None), windows, rows], ensure_ascii=False)


def authorize_host_action(desktop, broker, name, params, refresh, checkpoint):
    before = observation_identity(desktop.desktop_state)
    active = desktop.desktop_state.active_window
    if not broker.request(name, params, active.name if active else "桌面"):
        return "用户未批准、已取消或授权等待超时；操作未执行。已完成结果保留在历史中。"
    if checkpoint():
        return "批准后任务曾暂停，授权已失效，请重新发起任务。"
    started = time.monotonic()
    fresh = refresh()
    if checkpoint() or time.monotonic() - started > 15:
        return "批准后校验耗时过长或任务曾暂停，本次未执行，请重新发起任务。"
    if before != observation_identity(fresh):
        return "批准后界面、焦点或目标位置已变化；本次未执行，请重新发起任务。"
    return None


def workspace_operation(root, operation, relative="", content=""):
    from itertools import islice

    root = Path(root)
    if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
        raise ValueError("工作目录不能是链接或目录联接。")
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if Path(relative).is_absolute() or ":" in relative:
        raise ValueError("仅接受工作目录中的相对路径。")
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("路径超出任务工作目录。")
    # Refuse links/junctions at every existing path component.
    current = root
    for part in Path(relative).parts:
        current /= part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ValueError("工作目录不允许链接或目录联接。")
    if operation == "list":
        return json.dumps([p.name for p in islice(path.iterdir(), 200)], ensure_ascii=False)
    if operation == "read":
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("文本读取上限 1 MB。")
        return path.read_text(encoding="utf-8")
    if operation == "create":
        if len(content.encode("utf-8")) > 1024 * 1024:
            raise ValueError("文本写入上限 1 MB。")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as output:
            output.write(content)
        return f"已创建 {relative}；未覆盖已有文件。"
    raise ValueError("不支持的工作目录操作。")
