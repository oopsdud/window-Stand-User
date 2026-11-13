"""Role configuration and local history management; no desktop automation imports."""

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from desktop_app.runtime import DATA
from desktop_app.settings import DEFAULTS, PROVIDERS
from windows_use.memory import MemoryStore


class ModelProfile(QWidget):
    test_requested = Signal(dict)

    def __init__(self, role):
        super().__init__()
        self.role = role
        layout = QVBoxLayout(self)
        self.enabled = QCheckBox("启用独立规划大脑" if role == "planner" else "启用独立视觉模型")
        layout.addWidget(self.enabled)
        note = QLabel(
            "行动者先观察和搜集信息；综合规划、执行失败、循环或达到整理间隔时调用大脑。"
            if role == "planner"
            else "同时在「模型与执行」勾选发送截图后生效。截图仅发给视觉模型，画面描述交给行动者。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.provider = QComboBox()
        for title, key in PROVIDERS:
            self.provider.addItem(title, key)
        self.fields = {key: QLineEdit() for key in ("model", "base_url", "api_key", "api_version")}
        self.fields["api_key"].setEchoMode(QLineEdit.EchoMode.Password)
        self.fields["base_url"].setPlaceholderText("独立接口地址；留空使用该服务商默认地址")
        form.addRow("服务商", self.provider)
        for key, title in (
            ("model", "模型名称"),
            ("base_url", "接口地址"),
            ("api_key", "API Key"),
            ("api_version", "Azure 版本"),
        ):
            self.fields[key].setMinimumHeight(36)
            form.addRow(title, self.fields[key])
        layout.addLayout(form)
        test = QPushButton("测试图片识别" if role == "vision_model" else "测试连接与工具调用")
        test.clicked.connect(
            lambda: self.test_requested.emit({**self.values(), "_test_role": self.role})
        )
        layout.addWidget(test)
        layout.addStretch()

    def load(self, profile):
        self.enabled.setChecked(profile.get("enabled", False))
        self.provider.setCurrentIndex(
            max(0, self.provider.findData(profile.get("provider", "openai")))
        )
        for key, field in self.fields.items():
            field.setText(profile.get(key, ""))

    def values(self):
        return {
            "enabled": self.enabled.isChecked(),
            "provider": self.provider.currentData(),
            **{key: field.text().strip() for key, field in self.fields.items()},
        }


class CognitiveSettings(QWidget):
    save_requested = Signal()
    test_requested = Signal(dict)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("settingsBody")
        settings = QVBoxLayout(body)
        roles = QTabWidget()
        self.profiles = {}
        for role, title in (("planner", "规划大脑"), ("vision_model", "截图视觉")):
            profile = ModelProfile(role)
            profile.test_requested.connect(self.test_requested.emit)
            self.profiles[role] = profile
            roles.addTab(profile, title)
        settings.addWidget(roles)
        self.memory = QCheckBox("保存并按需检索本地任务历史与用户偏好")
        self.reuse = QCheckBox("复用已验证的机械点击流程（每步重新定位和检查结果）")
        settings.addWidget(self.memory)
        settings.addWidget(self.reuse)
        limits = QFormLayout()
        self.interval = QSpinBox()
        self.interval.setRange(2, 30)
        self.interval.setSuffix(" 步")
        self.max_plans = QSpinBox()
        self.max_plans.setRange(1, 20)
        self.max_plans.setSuffix(" 次 / 任务")
        limits.addRow("最长信息整理间隔", self.interval)
        limits.addRow("规划调用上限", self.max_plans)
        settings.addLayout(limits)
        note = QLabel(
            "关闭记忆不删除已有记录。历史保存在项目 .windows-use/memory.sqlite3；截图不落库。"
            "未启用独立大脑时使用行动者模型完成明确请求的规划。已有模型设置继续作为行动者。"
        )
        note.setWordWrap(True)
        settings.addWidget(note)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        save = QPushButton("保存模型与记忆设置")
        save.setObjectName("primary")
        save.clicked.connect(self.save_requested.emit)
        layout.addWidget(save)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self._loading = False
        for profile in self.profiles.values():
            profile.enabled.toggled.connect(self._edited)
            profile.provider.currentIndexChanged.connect(self._edited)
            for field in profile.fields.values():
                field.textChanged.connect(self._edited)
        self.memory.toggled.connect(self._edited)
        self.reuse.toggled.connect(self._edited)
        self.interval.valueChanged.connect(self._edited)
        self.max_plans.valueChanged.connect(self._edited)

    def _edited(self, *_):
        if not self._loading:
            self.note.setText(
                "有未保存的修改。测试连接不会保存配置；请点击“保存模型与记忆设置”，重启后才会保留。"
            )

    def load(self, config):
        self._loading = True
        for role, profile in self.profiles.items():
            profile.load(config.get(role, DEFAULTS[role]))
        self.memory.setChecked(config.get("memory_enabled", True))
        self.reuse.setChecked(config.get("reuse_workflows", True))
        self.interval.setValue(config.get("planning_interval", 8))
        self.max_plans.setValue(config.get("max_plans", 6))
        self._loading = False
        self.note.clear()

    def values(self):
        return {
            **{role: profile.values() for role, profile in self.profiles.items()},
            "memory_enabled": self.memory.isChecked(),
            "reuse_workflows": self.reuse.isChecked(),
            "planning_interval": self.interval.value(),
            "max_plans": self.max_plans.value(),
        }


class MemoryPanel(QWidget):
    continue_requested = Signal(str)

    def __init__(self, path=None):
        super().__init__()
        self.path = path or DATA / "memory.sqlite3"
        self.current_after = 0
        layout = QVBoxLayout(self)
        note = QLabel(
            "任务概况、具体轨迹、用户偏好和可复用流程均保存在本机。"
            "“completed” 表示行动者报告完成；流程复用还要求逐步通过可观察条件验证。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        self.kind = QComboBox()
        for title, key in (
            ("任务与执行轨迹", "task"),
            ("用户偏好（含待确认）", "preference"),
            ("已验证流程", "workflow"),
        ):
            self.kind.addItem(title, key)
        row.addWidget(self.kind)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        row.addWidget(refresh)
        layout.addLayout(row)
        self.items = QComboBox()
        self.items.setMinimumHeight(36)
        self.items.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.items.setMinimumContentsLength(20)
        layout.addWidget(self.items)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        layout.addWidget(self.detail, 1)
        row = QHBoxLayout()
        for title, callback in (
            ("下一页轨迹", self.more),
            ("续接为新任务", self.continue_task),
            ("确认选中偏好", self.confirm),
            ("删除选中记录", self.remove),
        ):
            button = QPushButton(title)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        form = QFormLayout()
        self.name = QLineEdit()
        self.value = QLineEdit()
        self.name.setPlaceholderText("例如：默认浏览器")
        self.value.setPlaceholderText("例如：优先使用 Edge")
        form.addRow("偏好名称", self.name)
        form.addRow("偏好内容", self.value)
        layout.addLayout(form)
        save = QPushButton("添加 / 更新为已确认偏好")
        save.clicked.connect(self.save_preference)
        layout.addWidget(save)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.kind.currentIndexChanged.connect(self.refresh)
        self.items.currentIndexChanged.connect(self.show_item)

    def use_store(self, callback):
        store = None
        try:
            store = MemoryStore(self.path)
            return callback(store)
        except Exception as error:
            self.note.setText(f"记忆操作失败：{error}")
        finally:
            if store:
                store.close()

    def refresh(self):
        def load(store):
            kind = self.kind.currentData()
            records = (
                store.recent(100)
                if kind == "task"
                else store.preferences(False)
                if kind == "preference"
                else store.workflows()
            )
            self.items.blockSignals(True)
            self.items.clear()
            for item in records:
                title = (
                    f"{item['started']} [{item['status']}] {item['request']}"
                    if kind == "task"
                    else f"{'已确认' if item['confirmed'] else '待确认'} · {item['name']}"
                    if kind == "preference"
                    else f"{'启用' if item['enabled'] else '失效停用'} · {item['title']}"
                )
                self.items.addItem(title[:150], item)
            self.items.blockSignals(False)
            self.show_item()

        self.use_store(load)

    def show_item(self):
        self.current_after = 0
        self.detail.clear()
        item = self.items.currentData()
        if not item:
            return
        if self.kind.currentData() == "task":
            self.more()
        else:
            self.detail.setPlainText(json.dumps(item, ensure_ascii=False, indent=2))
            if self.kind.currentData() == "preference":
                self.name.setText(item["name"])
                self.value.setText(item["value"])

    def more(self):
        item = self.items.currentData()
        if not item or self.kind.currentData() != "task":
            return

        def load(store):
            data = store.read_task(item["id"], self.current_after)
            if self.current_after:
                data.pop("task", None)
            self.current_after = data.get("next_after", self.current_after)
            self.detail.appendPlainText(json.dumps(data, ensure_ascii=False, indent=2))

        self.use_store(load)

    def continue_task(self):
        item = self.items.currentData()
        if item and self.kind.currentData() == "task":
            self.continue_requested.emit(
                f"继续之前的任务：{item['request']}\n历史任务 ID：{item['id']}。"
                "先用 read_task_history 读取进度和失败记录，再重新观察当前桌面。不要重放旧坐标或默认上一次动作已完成。"
            )

    def confirm(self):
        item = self.items.currentData()
        if item and self.kind.currentData() == "preference":
            self.use_store(lambda s: s.confirm_preference(item["id"]))
            self.refresh()

    def remove(self):
        item = self.items.currentData()
        if item:
            self.use_store(lambda s: s.delete(self.kind.currentData(), item["id"]))
            self.refresh()

    def save_preference(self):
        def save(store):
            store.put_preference(
                self.name.text().strip(), self.value.text().strip(), confirmed=True
            )
            self.note.setText("偏好已保存，后续任务生效；不会扩大操作权限。")

        self.use_store(save)
        self.refresh()
