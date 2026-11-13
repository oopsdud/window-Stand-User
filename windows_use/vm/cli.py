"""Scriptable VM management. All destructive operations require the exact owned ID."""

import argparse
import json
import sys

from windows_use.vm.manager import Manager


def main():
    parser = argparse.ArgumentParser(description="Stand User optional virtual machine manager")
    parser.add_argument(
        "action",
        choices=[
            "status",
            "prepare",
            "update",
            "create",
            "start",
            "show",
            "shutdown",
            "save",
            "pause",
            "resume",
            "snapshot",
            "restore",
            "delete",
            "recover",
            "cleanup",
        ],
    )
    parser.add_argument("--iso")
    parser.add_argument("--memory", type=int, default=4096)
    parser.add_argument("--cpus", type=int, default=2)
    parser.add_argument("--disk", type=int, default=80)
    parser.add_argument("--image-index", type=int, default=1)
    parser.add_argument("--manual-install", action="store_true")
    parser.add_argument("--confirm-id")
    parser.add_argument("--accept-windows-license", action="store_true")
    args = parser.parse_args()
    manager = Manager()
    try:
        if args.action == "status":
            result = manager.diagnose()
        elif args.action == "prepare":
            result = manager.create(None, args.memory, args.cpus, args.disk)
        elif args.action == "update":
            result = manager.refresh_bundle()
        elif args.action == "create":
            if not args.iso or not args.accept_windows_license:
                raise ValueError(
                    "创建前须选择自备微软 ISO，并通过 --accept-windows-license 确认你已阅读并接受该版本 Windows 的许可条件。"
                )
            if manager.manifest.exists() and manager.load()["phase"] == "awaiting_iso":
                result = manager.finish_setup(args.iso, args.image_index, not args.manual_install)
            else:
                result = manager.create(
                    args.iso,
                    args.memory,
                    args.cpus,
                    args.disk,
                    args.image_index,
                    not args.manual_install,
                )
        elif args.action in {"start", "show"}:
            result = manager.start(headless=args.action == "start")
        elif args.action in {"shutdown", "save", "pause", "resume"}:
            result = manager.control(args.action)
        elif args.action in {"snapshot", "restore"}:
            if args.action == "restore" and args.confirm_id != manager.load()["id"]:
                raise ValueError(
                    "恢复快照会丢弃之后的客户机改动，请用 --confirm-id 确认当前虚拟机 ID。"
                )
            result = manager.snapshot(restore=args.action == "restore")
        elif args.action == "delete":
            result = manager.remove(args.confirm_id)
        elif args.action == "recover":
            result = manager.recover_locks()
        else:
            result = manager.cleanup()
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2))
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
