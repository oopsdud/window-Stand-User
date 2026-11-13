"""VM lifecycle controls; management commands are not exposed through guest RPC."""

import json
import subprocess
import time

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from windows_use.vm.manager import ROOT, Manager

WINDOWS_ISO_URL = "https://www.microsoft.com/software-download/windows11"


class Operation(QThread):
    outcome = Signal(bool, str)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            result = self.operation()
            self.outcome.emit(
                True,
                result
                if isinstance(result, str)
                else json.dumps(result, ensure_ascii=False, indent=2),
            )
        except Exception as error:
            self.outcome.emit(False, str(error))


class VMPanel(QWidget):
    deactivated = Signal()
    activated = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("settingsBody")
        self.runner = None
        self.restoring = False
        self.activating = False
        self.actions = []
        self.sections = {}
        layout = QVBoxLayout(self)
        heading = QLabel("独立虚拟机 · 可选安装")
        heading.setObjectName("section")
        layout.addWidget(heading)
        note = QLabel("在独立 Windows 中工作，不占用主机鼠标。首次准备完成后，平时只需发任务。")
        note.setWordWrap(True)
        layout.addWidget(note)
        overview = self.section(
            layout, "运行状态", "先检查是否已准备好；客户机执行器在线后才能执行任务。"
        )
        self.status = QLabel("尚未检测，点击下方按钮查看当前状态。")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        overview.addWidget(self.status)
        self.buttons(overview, [("检测 / 刷新", lambda: self.run(lambda: Manager().diagnose()))])
        setup = self.section(
            layout,
            "首次准备",
            "① 下载并选择 Windows 11 x64 ISO → ② 安装系统 → ③ 执行器上线后启用。已有虚拟机无需重复创建。",
        )
        self.buttons(
            setup,
            [
                ("微软官方下载", lambda: QDesktopServices.openUrl(QUrl(WINDOWS_ISO_URL))),
                ("选择 ISO 文件", self.choose_iso),
            ],
        )
        self.iso = QLineEdit()
        self.iso.setPlaceholderText("选择下载好的 Windows 11 x64 ISO")
        setup.addWidget(self.iso)
        advanced = self.section(
            setup,
            "调整安装参数",
            "默认 2 核 / 4 GB 内存 / 80 GB 动态磁盘，通常无需更改。镜像版本索引需与你的 Windows 授权对应。",
            collapsed=True,
        )
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.memory = QSpinBox()
        self.memory.setRange(4096, 16384)
        self.memory.setSingleStep(1024)
        self.memory.setValue(4096)
        form.addRow("内存 MB", self.memory)
        self.cpus = QSpinBox()
        self.cpus.setRange(2, 8)
        self.cpus.setValue(2)
        form.addRow("处理器核心", self.cpus)
        self.disk = QSpinBox()
        self.disk.setRange(64, 256)
        self.disk.setValue(80)
        form.addRow("动态磁盘上限 GB", self.disk)
        self.image_index = QSpinBox()
        self.image_index.setRange(1, 99)
        form.addRow("镜像版本索引", self.image_index)
        advanced.addLayout(form)
        self.manual = QCheckBox("改为手动安装系统")
        self.manual.setToolTip("仅在自动安装不兼容时使用，需要自己完成客户机系统安装。")
        advanced.addWidget(self.manual)
        self.license = QCheckBox("已接受 Windows 许可并有对应使用授权")
        setup.addWidget(self.license)
        self.buttons(setup, [("一键安装并启动", self.create), ("启用按需运行", self.activate)])

        daily = self.section(
            layout,
            "日常使用",
            "后台启动：运行独立桌面。查看画面：手动进入客户机。保存并停止：保留进度、释放内存；正常关机：关闭客户机系统。",
        )
        self.buttons(
            daily,
            [
                ("后台启动", lambda: self.run(lambda: Manager().start())),
                ("查看虚拟机画面", lambda: self.run(lambda: Manager().start(headless=False))),
                ("保存进度并停止", lambda: self.run(lambda: Manager().control("save"))),
                ("正常关机", lambda: self.run(lambda: Manager().control("shutdown"))),
                ("打开成果文件夹", self.open_output),
            ],
        )
        backup = self.section(
            layout,
            "备份与恢复",
            "安装好软件并正常关机后创建基线。恢复基线会撤销之后的客户机磁盘改动，导出的共享成果保留。",
            collapsed=True,
        )
        self.buttons(
            backup,
            [
                ("创建基线快照", lambda: self.run(lambda: Manager().snapshot())),
                ("恢复到基线", self.restore),
            ],
        )
        maintenance = self.section(
            layout,
            "维护与排障",
            "连接异常或更新项目时使用。更新执行器需要先关机；恢复过期锁只处理已退出的任务。",
            collapsed=True,
        )
        self.buttons(
            maintenance,
            [
                ("单独安装 VirtualBox", self.install),
                ("查看客户机账号", self.credentials),
                ("恢复过期锁", lambda: self.run(lambda: Manager().recover_locks())),
                ("更新执行器", lambda: self.run(lambda: Manager().refresh_bundle())),
            ],
        )
        cleanup = self.section(
            layout,
            "停用与清理",
            "暂时不用，只需保存停止。彻底不用可选择停用并恢复主机；删除前先导出客户机重要文件。个人设置与共享成果保留。",
            collapsed=True,
        )
        self.buttons(
            cleanup,
            [
                ("停用并恢复主机", self.teardown),
                ("仅删除虚拟机", self.remove),
                ("清理客户机执行包", lambda: self.run(lambda: Manager().cleanup())),
                ("卸载运行环境", self.uninstall),
            ],
        )

    def section(self, parent, title, description, collapsed=False):
        frame = QFrame()
        frame.setObjectName("card")
        parent.addWidget(frame)
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(14, 12, 14, 12)
        header = QPushButton(title + ("  · 展开" if collapsed else "  · 收起"))
        header.setStyleSheet(
            "text-align: left; font-weight: 600; background: transparent; padding: 4px 0;"
        )
        header.setCheckable(True)
        header.setChecked(not collapsed)
        outer.addWidget(header)
        note = QLabel(description)
        note.setWordWrap(True)
        outer.addWidget(note)
        body = QWidget()
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 6, 0, 0)
        outer.addWidget(body)
        body.setVisible(not collapsed)
        header.toggled.connect(body.setVisible)
        header.toggled.connect(
            lambda expanded: header.setText(title + ("  · 收起" if expanded else "  · 展开"))
        )
        self.sections[title] = (header, body)
        return content

    def buttons(self, parent, entries):
        grid = QGridLayout()
        parent.addLayout(grid)
        for index, (text, callback) in enumerate(entries):
            button = QPushButton(text)
            button.clicked.connect(callback)
            grid.addWidget(button, index // 2, index % 2)
            self.actions.append(button)

    @property
    def busy(self):
        return self.runner is not None and self.runner.isRunning()

    def run(self, operation):
        if self.busy:
            return
        for button in self.actions:
            button.setEnabled(False)
        self.status.setText("正在处理，请稍候；主机桌面任务不会自动启动。")
        self.runner = Operation(operation, self)
        self.runner.outcome.connect(self.result)
        self.runner.finished.connect(self.finished)
        self.runner.start()

    def result(self, ok, text):
        if self.activating:
            self.activating = False
            if ok:
                self.activated.emit()
        if self.restoring:
            self.restoring = False
            if ok:
                self.deactivated.emit()
        if ok:
            try:
                data = json.loads(text)
                if isinstance(data, dict) and "state" in data:
                    state = {
                        "not_created": "未创建",
                        "poweroff": "已关机",
                        "running": "运行中",
                        "saved": "已保存并停止",
                        "paused": "已暂停",
                        "aborted": "异常停止",
                    }.get(data["state"], data["state"])
                    phase = {
                        "awaiting_iso": "等待选择 Windows ISO",
                        "creating": "配置未完成",
                        "prepared": "配置已准备",
                    }.get(data.get("phase"), "")
                    text = (
                        f"运行环境：{'已安装' if data['virtualbox'] else '未安装'}\n虚拟机：{state} · {phase}\n"
                        f"客户机执行器：{'在线' if data['guest_ready'] else '未连接 / 尚未安装系统'}\n"
                        f"主机可用内存：{data['available_memory_gib']} GiB；所在盘剩余：{data['free_gib']} GiB\n目录：{data['directory']}"
                    )
                    if data.get("error"):
                        text += "\n" + data["error"]
                    if data.get("resources"):
                        text += "\n" + data["resources"]["message"]
            except (ValueError, KeyError):
                pass
        self.status.setText(("完成\n" if ok else "未完成\n") + text)

    def finished(self):
        for button in self.actions:
            button.setEnabled(True)

    def choose_iso(self):
        filename, _ = QFileDialog.getOpenFileName(self, "选择微软安装镜像", "", "ISO (*.iso)")
        if filename:
            self.iso.setText(filename)

    def create(self):
        if not self.license.isChecked():
            self.status.setText("请先选择镜像，并确认所选 Windows 的许可条件。")
            return
        args = (
            self.iso.text(),
            self.memory.value(),
            self.cpus.value(),
            self.disk.value(),
            self.image_index.value(),
            not self.manual.isChecked(),
        )

        def setup():
            manager = Manager()
            if not manager.binary:
                self.script("Install-VirtualBox.ps1")
                manager = Manager()
            if manager.manifest.exists() and manager.load()["phase"] == "awaiting_iso":
                manager.finish_setup(args[0], args[4], args[5])
            else:
                manager.create(*args)
            return manager.start(headless=False)

        self.run(setup)

    def activate(self):
        def finish():
            from desktop_app.settings import SettingsStore

            manager = Manager()
            if not manager.diagnose()["guest_ready"]:
                raise RuntimeError("客户机执行器尚未在线，请先完成系统安装并登录，再启用。")
            manager.control("save")
            store = SettingsStore()
            config = store.load()
            if store.warning:
                raise RuntimeError("虚拟机已保存，但个人设置读取异常，未覆盖：" + store.warning)
            config.update(execution_environment="vm", vm_autostart=True, vm_auto_save=True)
            store.save(config)
            return "已保存虚拟机并启用按需运行。任务结束约 21 秒后释放内存；桌宠重新启动后也会保留设置。"

        self.activating = True
        self.run(finish)

    @staticmethod
    def script(name):
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ROOT / "vm" / name),
            ],
            capture_output=True,
            timeout=1800,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        if result.returncode == 3010:
            return "卸载已执行，Windows 要求重启后完成清理；请在方便时手动重启。"
        if result.returncode:
            raise RuntimeError(
                output[-4000:] or "安装/卸载未完成，请查看 vm 目录中的脚本及安装记录。"
            )
        return (
            output[-4000:] or "系统操作已结束。点击检测核对结果；若系统要求重启，请在方便时重启。"
        )

    def install(self):
        if (
            QMessageBox.question(
                self,
                "安装可选运行环境",
                "将在项目所在盘根目录安装 VirtualBox 主程序及必要驱动，Windows 可能提示管理员许可或重启。\n不安装扩展包、USB、桥接或 Host-only 网络驱动；不修改 Hyper-V、安全功能或系统启动配置。\n继续安装？",
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.run(lambda: self.script("Install-VirtualBox.ps1"))

    def uninstall(self):
        if (
            QMessageBox.question(
                self,
                "恢复主机",
                "只卸载本项目安装的 VirtualBox。请先删除本项目虚拟机，并确认其他 Windows 账号没有依赖该运行环境。\n系统日志和 MSI 缓存可能保留；个人模型、历史和导出成果保留。\n继续？",
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.run(lambda: self.script("Restore-Host.ps1"))

    def teardown(self):
        if (
            QMessageBox.question(
                self,
                "停用并恢复主机",
                "将正常关闭并删除本项目虚拟机（包括保存状态与快照），清理执行器，并卸载仅由本项目安装的 VirtualBox。\n客户机未导出的文件会丢失；原始 ISO、共享成果、主机模型与历史保留。已有或被其他虚拟机依赖的运行环境不会自动卸载。\n完成后切回本机逐次授权模式。确认继续？",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.restoring = True
        self.run(self.teardown_work)

    @staticmethod
    def teardown_work():
        from desktop_app.settings import SettingsStore

        manager = Manager()
        manager.recover_locks()
        if manager.manifest.exists():
            data = manager.load()
            state = manager.info(data).get("VMState")
            if state == "paused":
                manager.control("resume")
                state = "running"
            if state == "running":
                manager.control("shutdown")
                deadline = time.monotonic() + 90
                while (
                    time.monotonic() < deadline and manager.info(data).get("VMState") != "poweroff"
                ):
                    time.sleep(2)
                if manager.info(data).get("VMState") != "poweroff":
                    raise RuntimeError(
                        "客户机尚未正常关机，请处理保存提示后再停用；没有强制断电或删除。"
                    )
            manager.remove(data["id"])
        manager.cleanup()
        store = SettingsStore()
        config = store.load()
        if store.warning:
            raise RuntimeError("虚拟机已清理，但配置读取异常，未覆盖个人设置：" + store.warning)
        config["execution_environment"] = "host"
        store.save(config)
        journal = manager.home / "host-install.json"
        note = "既有运行环境保留。"
        if journal.is_file():
            record = json.loads(journal.read_text(encoding="utf-8-sig"))
            if (
                record.get("owner") == "window-Stand-User/v1"
                and record.get("phase") != "uninstalled"
            ):
                note = VMPanel.script("Restore-Host.ps1")
        # Only a verified package from this project's pinned download is removed.
        package = ROOT / ".local/vm-downloads/VirtualBox-7.2.22-175245-Win.exe"
        if package.is_file() and not package.is_symlink():
            import hashlib

            hasher = hashlib.sha256()
            with package.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    hasher.update(chunk)
            if (
                hasher.hexdigest()
                == "afd0ff1656cbe04ed88ce619594ae021419d97cce89fa9bdc443e9a963f1713f"
            ):
                package.unlink()
        return (
            "已停用，已切回本机逐次授权模式。共享成果和恢复记录保留；系统日志不强制清理。\n" + note
        )

    def remove(self):
        try:
            data = Manager().load()
            if (
                QMessageBox.question(
                    self,
                    "删除虚拟机",
                    "这会删除本项目客户机系统、软件和快照，无法撤销。请先导出客户机里的重要文件。\n共享成果、原始 ISO、主机模型配置与历史保留。\n确认删除？",
                )
                == QMessageBox.StandardButton.Yes
            ):
                self.run(lambda: Manager().remove(data["id"]))
        except Exception as error:
            self.status.setText(str(error))

    def restore(self):
        if (
            QMessageBox.question(
                self,
                "恢复基线",
                "客户机恢复到基线之后，后续系统盘改动将丢失；共享成果不回退。确认恢复？",
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.run(lambda: Manager().snapshot(restore=True))

    def open_output(self):
        path = Manager().home / "exchange" / "output" / "results"
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def credentials(self):
        try:
            from windows_use.vm.credentials import reveal

            password = reveal(Manager().load()["guest_password_protected"])
            QMessageBox.information(
                self,
                "仅用于独立客户机",
                "账号：StandUser\n密码：" + password + "\n不要用于主机或其他账号。",
            )
        except Exception as error:
            self.status.setText(str(error))
