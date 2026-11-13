"""Execution mode information and explicit optional sandbox preparation."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from desktop_app.runtime import DATA
from windows_use.sandbox import availability, launch, prepare


class ExecutionPanel(QWidget):
    save_requested = Signal()

    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("settingsBody")
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        layout = QVBoxLayout(body)
        self.environment = QComboBox()
        self.environment.addItem("本机：后台优先，桌面动作逐次批准", "host")
        self.environment.addItem("独立虚拟机：在客户机执行，不回退到主机", "vm")
        layout.addWidget(self.environment)
        self.vm_autostart = QCheckBox("虚拟机任务开始时自动启动已配置的客户机")
        layout.addWidget(self.vm_autostart)
        self.vm_auto_save = QCheckBox("任务结束后自动保存并停止按需启动的虚拟机，释放内存")
        self.vm_auto_save.setToolTip(
            "约 21 秒后保存；手动启动或打开画面接管的虚拟机保持运行。保存状态仍占磁盘。"
        )
        layout.addWidget(self.vm_auto_save)
        description = QLabel(
            "本机模式：后台优先，工作目录文本操作和网页读取无需鼠标。\n"
            "主机桌面操作：每次真实输入或焦点操作都需要明确批准。\n"
            "UIA 操作：不回退到鼠标；未知应用副作用仍需批准。\n"
            "拒绝或超时会停止相关任务，并保留已经完成的记录。\n\n"
            "批准的动作执行期间，请暂时让出键鼠；不是所有软件都能后台操作。\n\n"
            "虚拟机模式：只观察和操作客户机，连接失败不会切回主机。客户机的软件、账号和文件独立；发送等风险权限仍然检查。\n\n"
            "沙箱为可选的手动独立工作间，不接管 Agent 任务。默认不启动，不下载运行环境。"
            "沙箱内软件需另行准备，关闭并销毁后内部状态不保留。"
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        from desktop_app.vm_ui import VMPanel

        self.vm_panel = VMPanel()
        self.vm_panel.deactivated.connect(lambda: self.environment.setCurrentIndex(0))
        self.vm_panel.activated.connect(self.activate_vm)
        layout.addWidget(self.vm_panel)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.network = QCheckBox("允许沙箱联网（可访问网络；不是域名级隔离）")
        layout.addWidget(self.network)
        self.start = QPushButton("手动打开可选沙箱工作间")
        self.start.clicked.connect(self.open_sandbox)
        layout.addWidget(self.start)
        refresh = QPushButton("重新检测沙箱能力")
        refresh.clicked.connect(self.refresh)
        layout.addWidget(refresh)
        save = QPushButton("保存执行设置")
        save.clicked.connect(self.save_requested.emit)
        outer.addWidget(save)
        self.note = QLabel()
        self.note.setWordWrap(True)
        outer.addWidget(self.note)
        layout.addStretch()
        self.refresh()

    def load(self, config):
        self.vm_autostart.setChecked(config.get("vm_autostart", True))
        self.vm_auto_save.setChecked(config.get("vm_auto_save", True))
        self.environment.setCurrentIndex(
            max(0, self.environment.findData(config.get("execution_environment", "host")))
        )
        self.network.setChecked(bool(config.get("sandbox_network", False)))

    def activate_vm(self):
        self.environment.setCurrentIndex(self.environment.findData("vm"))
        self.vm_autostart.setChecked(True)
        self.vm_auto_save.setChecked(True)
        self.save_requested.emit()

    def values(self):
        return {
            "sandbox_network": self.network.isChecked(),
            "execution_environment": self.environment.currentData(),
            "vm_autostart": self.vm_autostart.isChecked(),
            "vm_auto_save": self.vm_auto_save.isChecked(),
        }

    def refresh(self):
        try:
            state = availability()
        except Exception as error:
            state = {"available": False, "reason": f"检测失败：{error}"}
        self.status.setText(state["reason"])
        self.start.setEnabled(state["available"])

    def open_sandbox(self):
        try:
            config = prepare(DATA / "sandbox", self.network.isChecked())
            launch(config)
            self.status.setText(
                f"已请求启动，输入与输出目录：{config.parent}\n这是独立工作间，不会自动接管本机应用。"
            )
        except Exception as error:
            self.status.setText(str(error))
