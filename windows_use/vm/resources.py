"""Task-scoped resource release; no boot service or continuously resident supervisor."""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

from windows_use.vm.manager import ROOT, Manager, write_json
from windows_use.vm.protocol import read_message


def same_process(pid, created):
    try:
        return abs(psutil.Process(pid).create_time() - created) < 0.01
    except psutil.NoSuchProcess:
        return False


def watch_task(manager=None):
    """Start before boot, so cancellation or a killed model process still releases RAM."""
    manager = manager or Manager()
    owner = {"pid": os.getpid(), "created": psutil.Process().create_time()}
    executable = Path(sys.executable).with_name("pythonw.exe")
    subprocess.Popen(
        [
            str(executable if executable.is_file() else sys.executable),
            "-m",
            "windows_use.vm.resources",
            "--pid",
            str(owner["pid"]),
            "--created",
            str(owner["created"]),
            "--home",
            str(manager.home),
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return owner


def release_task(manager, owner):
    """Only the same dead task's automatically started VM may be saved."""
    record_path = manager.home / "auto-run.json"
    if not record_path.is_file():
        return "手动启动的虚拟机保持原状。"
    record = read_message(record_path)
    if any(record.get(key) != value for key, value in owner.items()):
        return "自动启动记录属于其他任务，未操作。"
    if same_process(owner["pid"], owner["created"]):
        raise RuntimeError("任务仍在运行，不能释放资源。")
    manager.recover_locks()
    with manager.exclusive():
        manager._no_task()
        # A new task or a manual action may have taken over while we waited.
        if not record_path.exists() or read_message(record_path) != record:
            return "虚拟机已被接管。"
        data = manager.load()
        if data["id"] != record.get("vm_id"):
            raise RuntimeError("虚拟机归属已变化，未释放。")
        manager.revoke_task(owner["pid"])
        state = manager.info(data).get("VMState")
        if state in {"running", "paused"}:
            manager.command("controlvm", data["id"], "savestate", timeout=180)
            if manager.info(data).get("VMState") != "saved":
                raise RuntimeError("保存状态未确认，请手动检查虚拟机。")
        elif state not in {"poweroff", "saved", "aborted"}:
            raise RuntimeError("虚拟机正在切换状态，请稍后手动保存。")
        record_path.unlink()
        return "已保存自动启动的虚拟机，释放客户机运行内存。"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--created", type=float, required=True)
    parser.add_argument("--home", required=True)
    args = parser.parse_args()
    owner = {"pid": args.pid, "created": args.created}
    manager = Manager(args.home)
    try:
        while same_process(args.pid, args.created):
            time.sleep(1)
        # Expire the 20s lease before saving a possibly disconnected guest.
        # One in-flight operation can still be suspended; never replay its request.
        time.sleep(21)
        result = release_task(manager, owner)
        write_json(manager.home / "resource-status.json", {"ok": True, "message": result})
    except Exception as error:
        write_json(
            manager.home / "resource-status.json",
            {"ok": False, "message": f"资源自动释放未完成，请手动保存虚拟机：{error}"},
        )


if __name__ == "__main__":
    main()
