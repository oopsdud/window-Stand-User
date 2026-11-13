"""Optional Windows Sandbox preparation. Never enables OS features implicitly."""

import os
import subprocess
import uuid
from pathlib import Path
from xml.etree import ElementTree as ET


def availability():
    if os.name != "nt":
        return {"available": False, "reason": "仅支持 Windows。"}
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    ) as key:
        edition = winreg.QueryValueEx(key, "EditionID")[0]
    if edition.startswith("Core"):
        return {
            "available": False,
            "reason": "当前 Windows 家庭版不受官方 Sandbox 支持；后台处理和授权桌面仍可使用。",
        }
    binary = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "WindowsSandbox.exe"
    return {
        "available": binary.is_file(),
        "reason": "可启动；交互会话需实际验证。"
        if binary.is_file()
        else "尚未启用 Windows Sandbox，请通过 Windows 功能设置启用并按系统要求重启。",
        "binary": str(binary),
    }


def prepare(directory, networking=False):
    """Create a private exchange directory; no host-wide shares or credentials."""
    root = Path(directory).resolve() / uuid.uuid4().hex
    inputs, outputs = root / "input", root / "output"
    inputs.mkdir(parents=True)
    outputs.mkdir()
    config = ET.Element("Configuration")
    for key, value in {
        "Networking": "Enable" if networking else "Disable",
        "ClipboardRedirection": "Disable",
        "AudioInput": "Disable",
        "VideoInput": "Disable",
        "PrinterRedirection": "Disable",
        "MemoryInMB": "4096",
    }.items():
        ET.SubElement(config, key).text = value
    folders = ET.SubElement(config, "MappedFolders")
    for path, target, readonly in (
        (inputs, r"C:\TaskInput", "true"),
        (outputs, r"C:\TaskOutput", "false"),
    ):
        folder = ET.SubElement(folders, "MappedFolder")
        for key, value in (
            ("HostFolder", str(path)),
            ("SandboxFolder", target),
            ("ReadOnly", readonly),
        ):
            ET.SubElement(folder, key).text = value
    destination = root / "workspace.wsb"
    ET.ElementTree(config).write(destination, encoding="utf-8", xml_declaration=True)
    return destination


def launch(config):
    status = availability()
    if not status["available"]:
        raise RuntimeError(status["reason"])
    # Do not adopt or modify an existing sandbox, including one started by the user.
    import psutil

    if any(
        "windowssandbox" in (p.info.get("name") or "").lower()
        for p in psutil.process_iter(["name"])
    ):
        raise RuntimeError("已有沙箱实例；请先处理现有实例。本项目不会接管或关闭它。")
    return subprocess.Popen([status["binary"], str(Path(config).resolve())], close_fds=True)
