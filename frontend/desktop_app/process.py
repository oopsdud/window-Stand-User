"""Qt event-loop integration for the Agent's JSON-lines protocol."""

import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from desktop_app.runtime import FRONTEND, ROOT
from desktop_app.settings import model_secrets


class WorkerProcess(QObject):
    event = Signal(str, dict)
    exited = Signal(int)
    diagnostic = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(ROOT))
        self.process.setProcessEnvironment(QProcessEnvironment.systemEnvironment())
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.readyReadStandardError.connect(self._errors)
        self.process.started.connect(self._send_request)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._failed)
        self.buffer = b""
        self.secret = ""
        self.secrets = []
        self.request = None

    @property
    def busy(self):
        return self.process.state() != QProcess.ProcessState.NotRunning

    def start(self, config, task="", operation="run", script=None):
        if self.busy:
            raise RuntimeError("请先结束当前任务。")
        self.buffer = b""
        self.vm_mode = config.get("execution_environment") == "vm" and operation == "run"
        self.secret = config.get("api_key", "")
        self.secrets = model_secrets(config)
        self.request = {
            "config": {**config, "overlay_process_id": os.getpid()},
            "task": task,
            "operation": operation,
        }
        executable = Path(sys.executable)
        windowed = executable.with_name("pythonw.exe")
        self.process.setProgram(
            str(windowed if sys.platform == "win32" and windowed.is_file() else executable)
        )
        self.process.setArguments(["-u", str(script or FRONTEND / "start-desktop.pyw"), "--worker"])
        self.process.start()

    def _send_request(self):
        self.process.write((json.dumps(self.request, ensure_ascii=False) + "\n").encode("utf-8"))
        self.request = None

    def command(self, command):
        if self.busy:
            message = command if isinstance(command, dict) else {"command": command}
            self.process.write((json.dumps(message) + "\n").encode())

    def stop(self):
        if self.busy:
            if getattr(self, "vm_mode", False):
                from windows_use.vm.manager import Manager

                try:
                    Manager().revoke_task(int(self.process.processId()))
                except Exception as error:
                    self.diagnostic.emit(f"客户机撤销写入失败：{error}；任务租约会自动过期。")
            self.command("cancel")
            # Windows QProcess.kill uses TerminateProcess: it also stops a blocked LLM request.
            self.process.kill()

    def _read(self):
        self.buffer += bytes(self.process.readAllStandardOutput())
        if len(self.buffer) > 8 * 1024 * 1024:
            self.event.emit("fatal", {"content": "执行进程输出过大，任务已停止。"})
            self.stop()
            return
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                payload = json.loads(line.decode("utf-8"))
                if isinstance(payload.get("data"), dict) and isinstance(payload.get("type"), str):
                    self.event.emit(payload["type"], payload["data"])
            except (ValueError, AttributeError):
                self.diagnostic.emit("忽略了一条无法解析的执行进程消息。")

    def _errors(self):
        text = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        for secret in sorted(filter(None, self.secrets), key=len, reverse=True):
            text = text.replace(secret, "[已隐藏 Key]")
        if text.strip():
            self.diagnostic.emit(text[-6000:])

    def _failed(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.event.emit("fatal", {"content": "无法启动 Agent 进程，请检查本地 Python 环境。"})
            self.exited.emit(-1)

    def _finished(self, code, status):
        self._read()
        self._errors()
        self.request = None
        self.exited.emit(code)
