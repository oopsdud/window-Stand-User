"""Host-side guest connection; never falls back to host desktop input."""

import os
import threading
import time
import uuid

from windows_use.vm.manager import Manager
from windows_use.vm.protocol import decode, read_message, write_message


def dispatch(guest, name, params, local):
    from windows_use.agent.registry.views import ToolResult
    from windows_use.execution import BACKGROUND
    from windows_use.vm.protocol import GUEST_TOOLS

    if name in GUEST_TOOLS:
        try:
            return ToolResult.model_validate(
                guest.call("execute", {"tool": name, "params": params})
            )
        except Exception as error:
            return ToolResult(is_success=False, safety_blocked=True, error=str(error))
    if name in BACKGROUND:
        return local()
    return ToolResult(
        is_success=False, safety_blocked=True, error="虚拟机不支持此工具；没有回退为主机操作。"
    )


class GuestClient:
    def __init__(self, settings, cancelled, manager=None):
        self.manager = manager or Manager()
        self.root = self.manager.home
        self.cancelled = cancelled
        self.generation = None
        self.stopped = threading.Event()
        self.lock = self.root / "task.lock"
        self.thread = None
        if self.lock.exists():
            self.manager.recover_locks()
        data = self.manager.load()
        self.booted = False
        if self.manager.info(data).get("VMState") in {"poweroff", "saved"} and settings.get(
            "vm_autostart", True
        ):
            owner = None
            if settings.get("vm_auto_save", True):
                from windows_use.vm.resources import watch_task

                owner = watch_task(self.manager)
            self.manager.start(auto_owner=owner)
            self.booted = True
        with self.manager.exclusive():
            data = self.manager.load()
            if self.manager.info(data).get("VMState") != "running":
                raise RuntimeError("虚拟机未运行。请先启动并登录客户机；不会回退到主机。")
            self.session = {
                "vm_id": data["id"],
                "id": uuid.uuid4().hex,
                "active": True,
                "pid": os.getpid(),
                "use_vision": bool(settings.get("use_vision")),
                "allowed_risks": list(settings.get("allowed_risks", [])),
            }
            try:
                self.fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError as error:
                raise RuntimeError("虚拟机已有任务连接；请先停止旧任务或恢复过期锁。") from error
            os.write(self.fd, str(os.getpid()).encode())
        self.session_path = self.root / "exchange" / "input" / "session.json"
        try:
            # Consecutive tasks reuse the VM during the resource-release grace period.
            lease_path = self.root / "auto-run.json"
            if lease_path.exists():
                from windows_use.vm.manager import write_json
                from windows_use.vm.resources import watch_task

                if settings.get("vm_auto_save", True):
                    lease = read_message(lease_path)
                    if lease.get("pid") != os.getpid():
                        write_json(lease_path, {**watch_task(self.manager), "vm_id": data["id"]})
                else:
                    lease_path.unlink()
            self.renew()
            self.thread = threading.Thread(target=self.keepalive, daemon=True)
            self.thread.start()
        except BaseException:
            os.close(self.fd)
            self.lock.unlink(missing_ok=True)
            raise

    def renew(self):
        revoked = self.session_path.with_name("revoked.json")
        if revoked.is_file() and read_message(revoked).get("session") == self.session["id"]:
            self.stopped.set()
            raise RuntimeError("虚拟机任务已被撤销。")
        if self.session_path.exists():
            previous = read_message(self.session_path)
            if previous.get("id") == self.session["id"] and not previous.get("active"):
                self.stopped.set()
                raise RuntimeError("虚拟机任务已被撤销。")
        write_message(self.session_path, {**self.session, "expires": time.time() + 20})

    def keepalive(self):
        while not self.stopped.wait(4):
            if self.cancelled.is_set():
                return
            try:
                self.renew()
            except Exception:
                self.stopped.set()
                return

    def close(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(5)
        try:
            write_message(self.session_path, {**self.session, "active": False, "expires": 0})
        finally:
            os.close(self.fd)
            self.lock.unlink(missing_ok=True)

    def call(self, operation, payload=None, timeout=45):
        if self.cancelled.is_set() or self.stopped.is_set():
            raise RuntimeError("虚拟机任务已取消。")
        ident = uuid.uuid4().hex
        write_message(
            self.root / "exchange" / "input" / "request.json",
            {
                "id": ident,
                "session": self.session["id"],
                "operation": operation,
                "generation": self.generation,
                "expires": time.time() + timeout,
                "payload": payload or {},
            },
        )
        response = self.root / "exchange" / "output" / "response.json"
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            if self.cancelled.is_set() or self.stopped.is_set():
                raise RuntimeError("虚拟机任务已停止；已派发动作结果可能未知，不能自动重放。")
            if response.is_file():
                message = read_message(response)
                if message.get("id") == ident and message.get("session") == self.session["id"]:
                    if message.get("error"):
                        raise RuntimeError(message["error"])
                    self.generation = message.get("generation")
                    return message["result"]
            time.sleep(0.1)
        raise RuntimeError(
            "虚拟机未响应。请检查客户机已登录、Guest Additions 和执行器；不会切回主机。动作结果未知，不自动重试。"
        )

    def observe(self, as_bytes=False):
        result = self.call("observe", {"as_bytes": as_bytes}, timeout=120 if self.booted else 45)
        self.booted = False
        self.displays = result["displays"]
        self.environment = result["environment"]
        return decode(result["state"])
