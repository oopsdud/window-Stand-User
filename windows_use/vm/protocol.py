"""Bounded, observation-bound JSON protocol across private shared folders."""

import base64
import io
import json
import time
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path

from windows_use.vm.files import atomic_text

MAX_MESSAGE = 24 * 1024 * 1024
GUEST_TOOLS = frozenset(
    {
        "app_tool",
        "click_tool",
        "type_tool",
        "shortcut_tool",
        "scroll_tool",
        "move_tool",
        "ui_control_tool",
        "wait_tool",
        "scrape_tool",
        "workspace_tool",
    }
)


def write_message(path, value):
    path = Path(path)
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(payload) > MAX_MESSAGE:
        raise ValueError("虚拟机消息超过大小上限。")
    atomic_text(path, payload.decode("utf-8"))


def read_message(path):
    path = Path(path)
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise ValueError("通信文件不能是链接。")
    with path.open("rb") as source:
        data = source.read(MAX_MESSAGE + 1)
    if len(data) > MAX_MESSAGE:
        raise ValueError("虚拟机消息超过大小上限。")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("无效的虚拟机消息。")
    return value


def encode(value):
    from PIL import Image

    if isinstance(value, Image.Image):
        out = io.BytesIO()
        value.save(out, format="PNG")
        return {"_image": base64.b64encode(out.getvalue()).decode("ascii")}
    if isinstance(value, bytes):
        return {"_bytes": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Enum):
        return {"_enum": value.value}
    if is_dataclass(value):
        return {
            "_type": type(value).__name__,
            **{
                field.name: encode(getattr(value, field.name))
                for field in fields(value)
                if field.name not in {"control", "root_node", "dom_node"}
            },
        }
    if isinstance(value, (tuple, list)):
        return [encode(item) for item in value]
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    return value


def decode(value):
    from PIL import Image

    from windows_use.agent.desktop.views import DesktopState, Status, Window
    from windows_use.agent.tree.views import (
        BoundingBox,
        Center,
        ScrollElementNode,
        TextElementNode,
        TreeElementNode,
        TreeState,
    )

    classes = {
        c.__name__: c
        for c in (
            DesktopState,
            Window,
            TreeState,
            TreeElementNode,
            ScrollElementNode,
            TextElementNode,
            BoundingBox,
            Center,
        )
    }
    if isinstance(value, list):
        return [decode(item) for item in value]
    if not isinstance(value, dict):
        return value
    if "_image" in value:
        image = Image.open(io.BytesIO(base64.b64decode(value["_image"], validate=True)))
        if image.width * image.height > 32_000_000:
            raise ValueError("虚拟机截图过大。")
        image.load()
        return image
    if "_bytes" in value:
        return base64.b64decode(value["_bytes"], validate=True)
    if "_enum" in value:
        return Status(value["_enum"])
    if "_type" in value:
        name = value["_type"]
        if name not in classes or any(k in value for k in ("control", "root_node", "dom_node")):
            raise ValueError("不允许的虚拟机对象类型。")
        args = {key: decode(item) for key, item in value.items() if key != "_type"}
        for key in ("safety_identity", "safety_bounds"):
            if args.get(key) is not None:
                args[key] = tuple(args[key])
        return classes[name](**args)
    return {key: decode(item) for key, item in value.items()}


def validate_request(request, session, vm_id, seen, generation):
    if (
        session.get("vm_id") != vm_id
        or not session.get("active")
        or session.get("expires", 0) < time.time()
    ):
        raise ValueError("虚拟机任务授权已失效。")
    if request.get("session") != session.get("id") or not isinstance(session.get("id"), str):
        raise ValueError("虚拟机任务标识不匹配。")
    ident = request.get("id")
    if not isinstance(ident, str) or len(ident) != 32 or ident in seen:
        raise ValueError("重复或无效的虚拟机请求。")
    if not time.time() <= request.get("expires", 0) <= time.time() + 125:
        raise ValueError("虚拟机动作已过期。")
    if request.get("operation") == "execute":
        if not generation or request.get("generation") != generation:
            raise ValueError("观察版本已失效，请重新观察；不能重放动作。")
        if request.get("payload", {}).get("tool") not in GUEST_TOOLS:
            raise ValueError("客户机拒绝未登记工具。")
    elif request.get("operation") != "observe":
        raise ValueError("未知客户机请求。")
    return ident
