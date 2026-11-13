"""Execution guards inspired by arc-cua; see docs/SAFETY_MIGRATION.md.

These checks are defense in depth, not an OS sandbox or a semantic proof of safety.
"""

import re
from dataclasses import dataclass

RISK_PHRASES = {
    "delete": (
        "delete",
        "remove",
        "erase",
        "trash",
        "discard",
        "clear all",
        "empty trash",
        "permanently",
    ),
    "send": ("send", "post", "publish", "share", "reply all", "forward", "tweet"),
    "purchase": (
        "buy",
        "purchase",
        "pay",
        "checkout",
        "check out",
        "place order",
        "order now",
        "subscribe",
        "donate",
        "confirm payment",
    ),
    "close": (
        "close",
        "quit",
        "exit",
        "sign out",
        "log out",
        "logout",
        "shut down",
        "restart",
        "uninstall",
    ),
}
CHINESE_PHRASES = {
    "delete": ("删除", "移除", "清空", "销毁", "丢弃", "格式化"),
    "send": ("发送", "发布", "分享", "转发", "群发"),
    "purchase": ("购买", "付款", "支付", "结算", "下单", "订阅", "捐款", "转账"),
    "close": ("关闭", "退出", "注销", "关机", "重启", "卸载"),
}
RISK_CATEGORIES = frozenset(RISK_PHRASES) | {"system", "file_write", "unverified"}
_PATTERNS = {
    key: re.compile(r"\b(?:" + "|".join(map(re.escape, phrases)) + r")\b", re.I)
    for key, phrases in RISK_PHRASES.items()
}


def risks_of(label):
    return {
        key
        for key, pattern in _PATTERNS.items()
        if pattern.search(label or "")
        or any(word in (label or "") for word in CHINESE_PHRASES[key])
    }


def redact(value, secrets):
    """Redact registered literal values, longest first, without changing the input."""
    secrets = sorted({s for s in secrets if isinstance(s, str) and s}, key=len, reverse=True)
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[secret]")
        return value
    if isinstance(value, dict):
        return {redact(k, secrets): redact(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact(v, secrets) for v in value)
    return value


def redact_messages(messages, secrets):
    """Copy text/structured message fields; image pixels require separate masking."""
    return [
        message.model_copy(
            update={
                key: redact(getattr(message, key), secrets)
                for key in type(message).model_fields
                if key != "images"
            }
        )
        for message in messages
    ]


@dataclass(frozen=True)
class SafetyDecision:
    reason: str
    terminal: bool = True


class SafetyPolicy:
    def __init__(self, allowed_risks=()):
        self.allowed_risks = frozenset(allowed_risks)
        self._consumed_snapshot = None
        unknown = self.allowed_risks - RISK_CATEGORIES
        if unknown:
            raise ValueError(f"Unknown allowed_risks: {sorted(unknown)}")

    def require(self, risks):
        missing = set(risks) - self.allowed_risks
        if missing:
            return SafetyDecision(
                "操作安全拦截：尚未授权 "
                + ", ".join(sorted(missing))
                + "。本次任务停止，请调整授权或手动完成此操作。"
            )
        return None

    def check(self, name, params, desktop):
        if name == "workspace_tool":
            return self.require({"file_write"}) if params.get("operation") == "create" else None
        if name == "ui_control_tool":
            if params.get("operation") == "inspect":
                return None
            nodes = (
                getattr(
                    getattr(getattr(desktop, "desktop_state", None), "tree_state", None),
                    "interactive_nodes",
                    [],
                )
                or []
            )
            index = params.get("element_id")
            if type(index) is not int or not 0 <= index < len(nodes):
                return SafetyDecision("控件编号无效，请重新观察。", terminal=False)
            return self.require(risks_of(nodes[index].name) | risks_of(nodes[index].safety_name))
        if name == "desktop_tool" and params.get("action") == "remove":
            return self.require({"close"})
        if name == "shell_tool":
            return self.require({"system"})
        if name in {"file_tool", "memory_tool"}:
            mode = params.get("mode")
            return self.require(
                {"delete"}
                if mode == "delete"
                else {"file_write"}
                if mode not in {"read", "list", "view", "exists"}
                else set()
            )
        if name == "shortcut_tool":
            shortcut = params.get("shortcut", "").lower().replace(" ", "")
            if params.get("element_id") is not None or shortcut in {
                "enter",
                "return",
                "ctrl+enter",
                "shift+enter",
                "ctrl+v",
            }:
                permission = self.require({"system"})
                if permission:
                    return permission
                focus = self.check_shortcut_focus(params, desktop)
                if focus:
                    return focus
            if shortcut in {"delete", "del", "shift+delete", "shift+del"}:
                return self.require({"delete"})
            if shortcut in {"alt+f4", "ctrl+w", "ctrl+f4"}:
                return self.require({"close"})
            # Enter, paste and arbitrary shortcuts may submit or execute commands.
            return self.require(
                set()
                if shortcut
                in {
                    "escape",
                    "esc",
                    "tab",
                    "shift+tab",
                    "alt+tab",
                    "ctrl+c",
                    "ctrl+a",
                    "ctrl+f",
                    "left",
                    "right",
                    "up",
                    "down",
                }
                else {"system"}
            )
        if name in {"multi_select_tool", "multi_edit_tool"} or (
            name == "move_tool" and params.get("drag")
        ):
            # A batch changes the scene between targets; do not pretend one snapshot validates it.
            return self.require({"system"})
        if name not in {"click_tool", "type_tool"}:
            if name not in {
                "done_tool",
                "app_tool",
                "desktop_tool",
                "wait_tool",
                "timer_tool",
                "scrape_tool",
                "scroll_tool",
                "move_tool",
                "recall_history",
                "read_task_history",
                "read_trace_entry",
                "consult_brain",
                "suggest_preference",
            }:
                return self.require({"system"})
            return None
        if name == "click_tool" and params.get("clicks", 1) == 0:
            return None
        if name == "type_tool" and params.get("press_enter"):
            decision = self.require({"system"})
            if decision:
                return decision
        loc = params.get("loc")
        state = getattr(desktop, "desktop_state", None)
        if state is not None and state is self._consumed_snapshot:
            return SafetyDecision(
                "该观察已用于执行操作；必须重新观察，不能重放旧坐标。", terminal=False
            )
        tree = getattr(state, "tree_state", None)
        nodes = getattr(tree, "interactive_nodes", None) or []
        if not isinstance(loc, (list, tuple)) or len(loc) != 2:
            return SafetyDecision("操作安全拦截：目标坐标无效。")
        x, y = loc
        targets = [
            n
            for n in nodes
            if n.bounding_box.left <= x < n.bounding_box.right
            and n.bounding_box.top <= y < n.bounding_box.bottom
        ]
        if not targets:
            active = getattr(state, "active_window", None)
            box = getattr(active, "bounding_box", None)
            if box is not None and not (box.left <= x < box.right and box.top <= y < box.bottom):
                return SafetyDecision(
                    f"坐标 ({x},{y}) 不在当前前台窗口内，也没有命中本次观察的任何控件。"
                    "可能把截图像素误当桌面坐标；本次未点击。请优先传 element_id，"
                    "或先切换目标窗口。工具支持副屏负坐标。",
                    terminal=False,
                )
            return self.require({"unverified"})
        target = min(targets, key=lambda n: n.bounding_box.width * n.bounding_box.height)
        if params.get("target_name") and params["target_name"] not in {
            target.name,
            target.safety_name,
        }:
            return SafetyDecision(
                "坐标命中的控件名称与目标不一致；本次未点击，请重新定位真实目标。", terminal=False
            )
        decision = self.require(risks_of(target.name) | risks_of(target.safety_name))
        if decision:
            return decision
        try:
            if not desktop.safety_target_is_current(target, loc):
                diagnostic = getattr(desktop, "last_target_check", None)
                reason = diagnostic.get("reason") if isinstance(diagnostic, dict) else None
                detail = {
                    "foreground_changed": "前台窗口与观察时不同。",
                    "missing_control": "观察中缺少可验证的控件。",
                    "missing_foreground_snapshot": "观察中缺少前台窗口信息。",
                    "missing_runtime_identity": "控件未提供运行时身份。",
                    "hit_target_mismatch": "坐标实际命中的控件与观察目标不一致。",
                    "name_changed": "控件名称已变化。",
                    "bounds_changed": "控件位置或大小已变化。",
                }.get(reason, "")
                return SafetyDecision(
                    "目标已变化或无法校验；本次操作未执行，请重新观察后再决定。" + detail,
                    terminal=False,
                )
        except Exception:
            return SafetyDecision("目标校验失败；本次操作未执行，请重新观察。", terminal=False)
        return None

    def check_shortcut_focus(self, params, desktop):
        state = getattr(desktop, "desktop_state", None)
        nodes = getattr(getattr(state, "tree_state", None), "interactive_nodes", []) or []
        index = params.get("element_id")
        if index is not None:
            if type(index) is not int or not 0 <= index < len(nodes):
                return SafetyDecision("按键目标编号无效，请重新观察。", terminal=False)
            target = nodes[index]
        else:
            focused = [
                n
                for n in nodes
                if n.metadata.get("has_focused")
                and n.control_type not in {"文档", "Document", "窗格", "Pane"}
            ]
            if len(focused) != 1:
                return SafetyDecision(
                    "未确认按键接收控件。请先点击目标、重新观察焦点，再用 element_id 发送按键；本次未按键。",
                    terminal=False,
                )
            target = focused[0]
        if not target.metadata.get("has_focused"):
            return SafetyDecision(
                "目标控件没有焦点。请先点击该控件并重新观察，再发送按键；本次未按键。",
                terminal=False,
            )
        try:
            if desktop.safety_keyboard_target_is_current(target):
                return None
        except Exception:
            pass
        return SafetyDecision(
            "实时键盘焦点与观察目标不一致或无法校验；请重新观察，本次未按键。", terminal=False
        )

    def consume(self, name, desktop):
        # Consume before invocation: an exception may occur after a partial side effect.
        if name in {"click_tool", "type_tool"}:
            self._consumed_snapshot = getattr(desktop, "desktop_state", None)
