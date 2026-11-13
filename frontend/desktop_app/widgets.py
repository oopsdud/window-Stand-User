"""Native Chinese companion UI: task desk, settings, wardrobe and passive overlays."""

import copy
import html
import json
import os
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QBitmap, QColor, QCursor, QFont, QFontDatabase, QIcon, QPixmap, QRegion
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from desktop_app.brand import DEFAULT_ART
from desktop_app.pet import PetCanvas
from desktop_app.platform import mouse_buttons_down, protect_overlay
from desktop_app.settings import APPEARANCE_KEYS, PROVIDERS, STATES, asset_path, import_art

STYLE = """
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: #e6e3ef; }
QWidget#desk { background: #171520; }
QWidget#settingsBody { background: #171520; }
QFrame#masthead { background: #302642; border-radius: 18px; }
QLabel#brand { color: #d9bc76; font-size: 11px; font-weight: 600; }
QLabel#hero { color: #f5f2fc; font-size: 26px; font-weight: 600; }
QLabel#subhero { color: #bbb3ce; font-size: 12px; }
QLabel#muted { color: #a59cb7; font-size: 12px; }
QLabel#section { font-size: 18px; font-weight: 600; }
QLabel#pill { background: #39304b; color: #dfc487; border-radius: 12px; padding: 5px 12px; }
QTabWidget::pane { border: 0; background: transparent; }
QTabBar::tab { background: transparent; padding: 12px 23px; color: #a59cb7;
              border-bottom: 3px solid transparent; margin-bottom: 12px; }
QTabBar::tab:selected { color: #e8cb83; border-bottom: 3px solid #d8b76b; font-weight: 600; }
QLineEdit, QPlainTextEdit, QTextBrowser, QSpinBox, QComboBox, QListWidget {
    background: #24202f; border: 1px solid #51465f; border-radius: 9px; padding: 9px;
    selection-background-color: #68508a; selection-color: #ffffff;
}
QLineEdit:focus, QPlainTextEdit:focus { border: 1px solid #d8b76b; }
QComboBox { padding-right: 28px; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right;
    width: 26px; border: 0; }
QPushButton { background: #393044; border: 0; border-radius: 9px; padding: 10px 17px; }
QPushButton:hover { background: #51425f; }
QPushButton:pressed { background: #655277; }
QPushButton:disabled { background: #282430; color: #756b83; }
QPushButton#primary { background: #ad8a45; color: #17131d; font-weight: 600; }
QPushButton#primary:hover { background: #d0ad64; }
QPushButton#primary:disabled { background: #625643; }
QPushButton#stop { background: #472a39; color: #ffb0aa; }
QCheckBox { spacing: 9px; padding: 5px 0; }
QCheckBox::indicator { width: 17px; height: 17px; }
QScrollArea { border: 0; background: transparent; }
QScrollBar:vertical { background: #211c2c; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #756183; min-height: 26px; border-radius: 5px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QSlider::groove:horizontal { background: #40354e; height: 5px; border-radius: 2px; }
QSlider::handle:horizontal { background: #d8b76b; width: 17px; margin: -6px 0; border-radius: 8px; }
QFrame#preview { background: #241e33; border-radius: 16px; }
QFrame#card { background: #24202f; border: 1px solid #51465f; border-radius: 12px; }
QLabel#notice { color: #d9bc76; padding: 4px; }
"""

TOOL_NAMES = {
    "click_tool": "点击界面",
    "type_tool": "输入文字",
    "scroll_tool": "滚动页面",
    "move_tool": "移动 / 拖动",
    "app_tool": "管理应用",
    "shortcut_tool": "使用快捷键",
    "shell_tool": "执行命令",
    "scrape_tool": "读取网页",
    "wait_tool": "等待界面",
    "timer_tool": "管理定时任务",
    "file_tool": "处理文件",
    "memory_tool": "更新记忆",
    "desktop_tool": "管理桌面",
    "multi_edit_tool": "批量填写",
    "multi_select_tool": "批量选择",
    "workspace_tool": "后台处理工作目录",
    "ui_control_tool": "操作界面控件",
}


def label(text, name=None):
    result = QLabel(text)
    result.setWordWrap(True)
    if name:
        result.setObjectName(name)
    return result


def ensure_fonts():
    # Offscreen Qt on Windows has an empty font database. Reuse installed system
    # fonts for readable previews as well as fallback on unusual Windows setups.
    if not QFontDatabase.families():
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for filename in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
            if (fonts / filename).exists():
                QFontDatabase.addApplicationFont(str(fonts / filename))


def button(text, callback=None, name=None):
    result = QPushButton(text)
    result.setCursor(Qt.CursorShape.PointingHandCursor)
    if name:
        result.setObjectName(name)
    if callback:
        result.clicked.connect(callback)
    return result


def summarize(kind, data):
    if kind == "thought":
        return "执行计划", str(data.get("thought", ""))
    if kind == "tool_call":
        return TOOL_NAMES.get(data.get("tool_name"), "执行操作"), json.dumps(
            data.get("tool_params", {}), ensure_ascii=False
        )
    if kind == "tool_result":
        return ("操作结果" if data.get("is_success") else "操作未成功"), str(
            data.get("content") or ""
        )
    if kind == "error":
        return "正在处理问题", str(data.get("error") or "未知错误")
    if kind in {"done", "result"}:
        return "任务结果", str(data.get("content") or data.get("error") or "任务已结束")
    return kind, str(data.get("content", ""))


class DeskWindow(QWidget):
    appearance_requested = Signal(dict)
    run_requested = Signal(str)
    pause_requested = Signal()
    stop_requested = Signal()
    save_requested = Signal(dict)
    test_requested = Signal(dict)
    collapse_requested = Signal()
    background_requested = Signal()
    quit_requested = Signal()

    def __init__(self, config):
        super().__init__()
        ensure_fonts()
        self.setObjectName("desk")
        self.setWindowTitle("白金之星 · window-Stand User 桌宠")
        self.resize(880, 760)
        self.setMinimumSize(700, 610)
        self.setStyleSheet(STYLE)
        self.config = copy.deepcopy(config)
        self.entries = []
        self._quitting = False
        self._busy = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 18)
        layout.setSpacing(16)
        masthead = QFrame()
        masthead.setObjectName("masthead")
        row = QHBoxLayout(masthead)
        row.setContentsMargins(24, 20, 22, 20)
        heading = QVBoxLayout()
        heading.addWidget(label("window-Stand User  /  STAR PLATINUM", "brand"))
        self.hero = label("你的桌面，交给白金之星。", "hero")
        heading.addWidget(self.hero)
        heading.addWidget(label("你的指令，由白金之星执行。每一步，都有迹可循。", "subhero"))
        row.addLayout(heading, 1)
        self.brand_pet = PetCanvas()
        self.brand_pet.setFixedSize(86, 104)
        self.brand_pet.configure(config)
        row.addWidget(self.brand_pet)
        row.addWidget(button("收起为桌宠  ↘", self.collapse_requested.emit))
        layout.addWidget(masthead)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self._tasks()
        self._model_settings()
        self._appearance()
        from desktop_app.cognitive_ui import CognitiveSettings, MemoryPanel

        self.cognitive_settings = CognitiveSettings()
        self.cognitive_settings.save_requested.connect(self.save)
        self.cognitive_settings.test_requested.connect(self.test_requested.emit)
        self.tabs.addTab(self.cognitive_settings, "大脑与视觉")
        self.memory_panel = MemoryPanel()
        self.tabs.addTab(self.memory_panel, "历史与记忆")
        from desktop_app.execution_ui import ExecutionPanel

        self.execution_panel = ExecutionPanel()
        self.execution_panel.save_requested.connect(self.save)
        self.tabs.addTab(self.execution_panel, "执行方式")
        self.tabs.currentChanged.connect(
            lambda index: self.timer_panel.refresh() if index == 0 else None
        )
        self.memory_panel.continue_requested.connect(self._continue_history)
        self.tabs.currentChanged.connect(
            lambda index: self.memory_panel.refresh() if index == 4 else None
        )
        footer = QHBoxLayout()
        footer.addWidget(label("后台优先 · 桌面操作逐次批准", "muted"), 1)
        footer.addWidget(label("Ctrl + Alt + Space  呼出", "muted"))
        self.quit_button = button("退出程序", self.quit_requested.emit, "stop")
        self.quit_button.setToolTip("停止当前任务并完全退出；窗口右上角 × 仅隐藏到系统托盘")
        footer.addWidget(self.quit_button)
        layout.addLayout(footer)
        self.apply_config(config)

    def _tasks(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        row = QHBoxLayout()
        row.addWidget(label("今天想完成什么？", "section"), 1)
        self.status = label("● 准备就绪", "pill")
        row.addWidget(self.status)
        layout.addLayout(row)
        self.task = QPlainTextEdit()
        self.task.setPlaceholderText(
            "例如：打开记事本，写下今天的三项待办。\n可以指定应用、目标和保存位置。"
        )
        self.task.setFixedHeight(94)
        layout.addWidget(self.task)
        actions = QHBoxLayout()
        self.run = button(
            "开始任务  ↗", lambda: self.run_requested.emit(self.task.toPlainText()), "primary"
        )
        self.pause = button("暂停", self.pause_requested.emit)
        self.stop = button("停止", self.stop_requested.emit, "stop")
        self.pause.setEnabled(False)
        self.stop.setEnabled(False)
        actions.addWidget(self.run)
        actions.addWidget(self.pause)
        actions.addWidget(self.stop)
        actions.addStretch()
        actions.addWidget(button("导出记录", self.export))
        layout.addLayout(actions)
        self.run_note = label(
            "等待模型时可拖动桌宠；截图和操作时自动隐藏。Ctrl + Alt + Esc 可停止。", "muted"
        )
        layout.addWidget(self.run_note)
        from desktop_app.timer_ui import TimerPanel

        self.timer_toggle = QPushButton("稍后执行的任务 · 展开")
        self.timer_toggle.setCheckable(True)
        layout.addWidget(self.timer_toggle)
        self.timer_panel = TimerPanel()
        self.timer_panel.setMaximumHeight(230)
        self.timer_panel.hide()
        layout.addWidget(self.timer_panel)
        self.timer_toggle.toggled.connect(self.timer_panel.setVisible)
        self.timer_toggle.toggled.connect(
            lambda expanded: self.timer_toggle.setText(
                "稍后执行的任务 · " + ("收起" if expanded else "展开")
            )
        )
        self.timer_toggle.toggled.connect(
            lambda expanded: self.timer_panel.refresh() if expanded else None
        )
        self.output = QTextBrowser()
        self.output.setOpenExternalLinks(False)
        self.output.setOpenLinks(False)
        self.output.document().setMaximumBlockCount(1600)
        self.output.setHtml(
            "<div style='padding:22px;color:#b7acc9'><p style='font-size:18px;color:#dfc487'>"
            "从一个小任务开始</p><p>首次使用，先到「模型与执行」填写服务商、模型和 Key。"
            "</p><p>这里实时显示执行步骤；启用记忆后可在「历史与记忆」查看持久任务轨迹。"
            "独立大脑和视觉模型可在「大脑与视觉」配置。</p></div>"
        )
        layout.addWidget(self.output, 1)
        self.tabs.addTab(page, "任务")

    def _model_settings(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("settingsBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(4, 4, 14, 12)
        layout.addWidget(label("连接行动者模型", "section"))
        layout.addWidget(label("支持现有模型适配器；中转服务可选择 OpenAI / 兼容接口。", "muted"))
        form = QFormLayout()
        form.setSpacing(12)
        self.provider = QComboBox()
        for title, key in PROVIDERS:
            self.provider.addItem(title, key)
        self.model = QLineEdit()
        self.model.setPlaceholderText("服务商提供的 Model ID，可直接输入自定义模型名")
        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText("可留空使用默认地址；兼容接口通常填写 https://…/v1")
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("API Key（保存在本机，按当前 Windows 账号加密）")
        key_row = QHBoxLayout()
        key_row.addWidget(self.key, 1)
        reveal = QCheckBox("显示")
        reveal.toggled.connect(
            lambda checked: self.key.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        key_row.addWidget(reveal)
        self.api_version = QLineEdit()
        self.api_version.setPlaceholderText("Azure API version")
        form.addRow("服务商", self.provider)
        form.addRow("模型名称", self.model)
        form.addRow("接口地址", self.base_url)
        form.addRow("API Key", key_row)
        form.addRow("Azure 版本", self.api_version)
        layout.addLayout(form)
        layout.addWidget(label("执行偏好", "section"))
        row = QHBoxLayout()
        self.browser = QComboBox()
        for title, key in (
            ("Microsoft Edge", "EDGE"),
            ("Google Chrome", "CHROME"),
            ("Firefox", "FIREFOX"),
        ):
            self.browser.addItem(title, key)
        self.steps = QSpinBox()
        self.steps.setRange(1, 200)
        self.steps.setSuffix(" 步")
        row.addWidget(label("浏览器"))
        row.addWidget(self.browser, 1)
        row.addSpacing(15)
        row.addWidget(label("任务步数上限"))
        row.addWidget(self.steps)
        layout.addLayout(row)
        self.vision = QCheckBox("发送桌面截图给模型（需要模型支持图片）")
        self.experimental = QCheckBox("启用实验工具：文件、记忆与批量操作")
        self.observer = QCheckBox("执行时显示实时旁观窗")
        for item in (self.vision, self.experimental, self.observer):
            layout.addWidget(item)
        layout.addWidget(
            label("操作授权（保存后用于后续任务；未授权的风险操作会停止任务）", "muted")
        )
        self.risk_checks = {}
        for title, key in (
            ("删除 / 清空", "delete"),
            ("发送 / 发布", "send"),
            ("购买 / 支付", "purchase"),
            ("关闭 / 退出", "close"),
            ("文件写入 / 覆盖 / 移动", "file_write"),
            ("高级执行：Shell、提交快捷键、批量操作（可能绕过上述分类）", "system"),
            ("允许点击无 UIA 控件的坐标（无法校验目标与风险）", "unverified"),
        ):
            check = QCheckBox(title)
            self.risk_checks[key] = check
            layout.addWidget(check)
        self.instructions = QPlainTextEdit()
        self.instructions.setPlaceholderText("可选：补充执行要求，例如默认使用中文回复。")
        self.instructions.setFixedHeight(65)
        layout.addWidget(self.instructions)
        row = QHBoxLayout()
        self.save_model = button("保存设置", self.save, "primary")
        self.test = button("测试连接与工具调用", self.test_connection)
        self.cancel_test = button("取消测试", self.stop_requested.emit)
        self.cancel_test.setEnabled(False)
        row.addWidget(self.save_model)
        row.addWidget(self.test)
        row.addWidget(self.cancel_test)
        row.addStretch()
        layout.addLayout(row)
        self.settings_note = label(
            "测试会发起一次模型请求，不操作桌面。Key 留空时可使用已有环境配置。", "muted"
        )
        layout.addWidget(self.settings_note)
        layout.addStretch()
        scroll.setWidget(body)
        self.tabs.addTab(scroll, "模型与执行")
        self.provider.currentIndexChanged.connect(self._provider_changed)

    def _provider_changed(self):
        self.api_version.setEnabled(self.provider.currentData() == "azure_openai")

    def _appearance(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(25)
        preview_frame = QFrame()
        preview_frame.setObjectName("preview")
        preview_frame.setFixedWidth(250)
        left = QVBoxLayout(preview_frame)
        left.setContentsMargins(10, 12, 10, 12)
        left.addWidget(label("形象预览", "muted"), 0, Qt.AlignmentFlag.AlignCenter)
        left.addStretch()
        self.preview = PetCanvas()
        self.preview.setFixedSize(160, 160)
        left.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignCenter)
        self.preview_title = label("白金之星", "section")
        left.addWidget(self.preview_title, 0, Qt.AlignmentFlag.AlignCenter)
        left.addWidget(label("按桌宠实际尺寸预览", "muted"), 0, Qt.AlignmentFlag.AlignCenter)
        left.addStretch()
        layout.addWidget(preview_frame, 1)
        settings_column = QVBoxLayout()
        self.appearance_scroll = QScrollArea()
        self.appearance_scroll.setWidgetResizable(True)
        self.appearance_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.appearance_scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("settingsBody")
        self.appearance_scroll.setWidget(body)
        right = QVBoxLayout(body)
        right.setContentsMargins(0, 0, 12, 8)
        right.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        right.setSpacing(8)
        settings_column.addWidget(self.appearance_scroll, 1)
        right.addWidget(label("让它长成你喜欢的样子", "section"))
        self.pet_name = QLineEdit()
        self.pet_name.setMaxLength(20)
        self.pet_name.setPlaceholderText("给桌宠起个名字")
        right.addWidget(self.pet_name)
        self.art_state = QComboBox()
        for title, key in STATES:
            self.art_state.addItem(title, key)
        right.addWidget(label("要设置的状态", "muted"))
        right.addWidget(self.art_state)
        self.art_note = label("使用默认形象", "muted")
        right.addWidget(self.art_note)
        row = QHBoxLayout()
        self.upload = button("选择图片…", self.upload_art)
        self.reset_art = button("恢复默认", self.clear_art)
        row.addWidget(self.upload)
        row.addWidget(self.reset_art)
        right.addLayout(row)
        self.remove_white = QCheckBox("导入时自动去除白色背景")
        self.remove_white.setChecked(True)
        right.addWidget(self.remove_white)
        self.clean_art = button("去除当前图片白底", self.clean_current_art)
        right.addWidget(self.clean_art)
        right.addWidget(
            label(
                "PNG / JPG / WebP / GIF，最大 20 MB、120 帧。透明底效果更好。"
                "静态图片会增加整体动态；动图保留逐帧动画。",
                "muted",
            )
        )
        self.motion = QComboBox()
        for title, key in (("轻轻漂浮", "float"), ("安静呼吸", "breathe"), ("活泼跳动", "bounce")):
            self.motion.addItem(title, key)
        right.addWidget(self.motion)
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(100, 220)
        self.size_text = label("桌宠大小 · 160", "muted")
        right.addWidget(self.size_text)
        right.addWidget(self.size_slider)
        self.reduced = QCheckBox("减少动态（包括动图）")
        right.addWidget(self.reduced)
        self.save_appearance_button = button("保存外观", self.save_appearance, "primary")
        settings_column.addWidget(self.save_appearance_button)
        self.appearance_note = label(
            "未单独设置的状态沿用默认图片。图片只复制到本地，不上传云端。", "muted"
        )
        settings_column.addWidget(self.appearance_note)
        right.addStretch()
        layout.addLayout(settings_column, 2)
        for control in body.findChildren(QLineEdit) + body.findChildren(QComboBox):
            control.setMinimumHeight(38)
        for control in body.findChildren(QPushButton):
            control.setMinimumHeight(36)
        self.size_slider.setMinimumHeight(24)
        self.tabs.addTab(page, "桌宠外观")
        self.art_state.currentIndexChanged.connect(self.update_preview)
        self.pet_name.textChanged.connect(self.update_preview)
        self.motion.currentIndexChanged.connect(self.update_preview)
        self.size_slider.valueChanged.connect(self.update_preview)
        self.reduced.toggled.connect(self.update_preview)

    def apply_config(self, config):
        self.config = copy.deepcopy(config)
        self.brand_pet.configure(config)
        self.setWindowTitle(f"{config['pet_name']} · window-Stand User")
        self.cognitive_settings.load(config)
        self.execution_panel.load(config)
        for widget, value in (
            (self.provider, config["provider"]),
            (self.browser, config["browser"]),
            (self.motion, config["motion"]),
        ):
            widget.setCurrentIndex(max(0, widget.findData(value)))
        for widget, key in (
            (self.model, "model"),
            (self.base_url, "base_url"),
            (self.key, "api_key"),
            (self.api_version, "api_version"),
            (self.pet_name, "pet_name"),
        ):
            widget.setText(config[key])
        self.steps.setValue(config["max_steps"])
        self.vision.setChecked(config["use_vision"])
        self.experimental.setChecked(config["experimental"])
        for key, check in self.risk_checks.items():
            check.setChecked(key in config.get("allowed_risks", []))
        self.observer.setChecked(config["show_observer"])
        self.reduced.setChecked(config["reduce_motion"])
        self.remove_white.setChecked(config.get("auto_remove_white", True))
        self.size_slider.setValue(config["pet_size"])
        self.instructions.setPlainText(config["instructions"])
        self.hero.setText(f"你的桌面，交给{config['pet_name']}。")
        self._provider_changed()
        self.update_preview()

    def values(self):
        config = copy.deepcopy(self.config)
        if hasattr(self, "cognitive_settings"):
            config.update(self.cognitive_settings.values())
        if hasattr(self, "execution_panel"):
            config.update(self.execution_panel.values())
        config.update(
            provider=self.provider.currentData(),
            model=self.model.text().strip(),
            base_url=self.base_url.text().strip(),
            api_key=self.key.text().strip(),
            api_version=self.api_version.text().strip(),
            browser=self.browser.currentData(),
            max_steps=self.steps.value(),
            use_vision=self.vision.isChecked(),
            experimental=self.experimental.isChecked(),
            allowed_risks=[key for key, check in self.risk_checks.items() if check.isChecked()],
            show_observer=self.observer.isChecked(),
            instructions=self.instructions.toPlainText().strip(),
            pet_name=self.pet_name.text().strip() or "白金之星",
            pet_size=self.size_slider.value(),
            motion=self.motion.currentData(),
            reduce_motion=self.reduced.isChecked(),
            auto_remove_white=self.remove_white.isChecked(),
        )
        return config

    def save(self):
        self.save_requested.emit(self.values())

    def set_settings_note(self, text):
        self.settings_note.setText(text)
        self.cognitive_settings.note.setText(text)
        self.execution_panel.note.setText(text)

    def _continue_history(self, task):
        self.task.setPlainText(task)
        self.tabs.setCurrentIndex(0)

    def save_appearance(self):
        values = self.values()
        self.appearance_requested.emit({key: values[key] for key in APPEARANCE_KEYS})

    def test_connection(self):
        self.test_requested.emit(self.values())

    def update_preview(self, *_):
        values = self.values()
        state = self.art_state.currentData()
        # Select the state before decoding to avoid loading the old state's animation first.
        self.preview.state = state
        self.preview.configure(values)
        self.preview.setFixedSize(values["pet_size"], values["pet_size"])
        custom = self.config.get("assets", {}).get(state)
        if custom == DEFAULT_ART:
            custom = None
        self.art_note.setText(
            "已设置自定义图片"
            if custom
            else ("使用默认形象" if state == "idle" else "沿用默认 / 待机形象")
        )
        self.reset_art.setText("恢复默认形象" if state == "idle" else "沿用待机形象")
        self.reset_art.setEnabled(bool(custom))
        idle_art = self.config.get("assets", {}).get("idle")
        self.clean_art.setEnabled(bool(custom or (idle_art and idle_art != DEFAULT_ART)))
        self.preview_title.setText(self.pet_name.text() or "白金之星")
        self.size_text.setText(f"桌宠大小 · {self.size_slider.value()}")

    def upload_art(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择桌宠形象", "", "图片 (*.png *.jpg *.jpeg *.gif *.webp)"
        )
        if not path:
            return
        try:
            imported = import_art(path, remove_white=self.remove_white.isChecked())
            self.config.setdefault("assets", {})[self.art_state.currentData()] = imported
            self.appearance_note.setText("图片已导入，点击「保存外观」应用到桌宠。")
            self.update_preview()
        except Exception as error:
            self.appearance_note.setText(f"导入失败：{error}")

    def clear_art(self):
        self.config.setdefault("assets", {}).pop(self.art_state.currentData(), None)
        self.update_preview()
        self.appearance_note.setText("预览已更新，点击「保存外观」应用。")

    def clean_current_art(self):
        selected = self.art_state.currentData()
        path = asset_path(
            self.config.get("assets", {}).get(selected) or self.config.get("assets", {}).get("idle")
        )
        if not path:
            self.appearance_note.setText("请先选择一张自定义图片。")
            return
        try:
            self.config.setdefault("assets", {})[selected] = import_art(str(path))
            self.update_preview()
            self.appearance_note.setText(
                "已去除白底，点击保存外观应用。旧图片清晰度需重新导入原图恢复。"
            )
        except Exception as error:
            self.appearance_note.setText(f"去白底失败：{error}")

    def set_busy(self, busy, paused=False, testing=False):
        self._busy = busy
        self.run.setEnabled(not busy)
        self.task.setEnabled(not busy)
        self.pause.setEnabled(busy and not testing)
        self.pause.setText("继续任务" if paused else "暂停")
        self.stop.setEnabled(busy)
        self.tabs.setTabEnabled(1, not busy or testing)
        self.tabs.setTabEnabled(2, not busy)
        self.tabs.setTabEnabled(3, not busy)
        self.tabs.setTabEnabled(4, not busy)
        self.tabs.setTabEnabled(5, not busy)
        self.test.setEnabled(not busy)
        self.save_model.setEnabled(not busy)
        self.cancel_test.setEnabled(busy and testing)

    def append(self, title, content, tone="normal"):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.entries.append({"time": stamp, "title": title, "content": content})
        self.entries = self.entries[-500:]
        color = "#ffb0aa" if tone == "error" else "#dfc487"
        body = html.escape(content[:18000]).replace("\n", "<br>")
        fragment = (
            f"<p style='margin-top:14px;color:{color}'><b>{html.escape(title)}</b>"
            f" <span style='color:#a59cb7;font-size:11px'> · {stamp}</span></p>"
            f"<p style='color:#ded8e8;line-height:1.5'>{body}</p>"
        )
        scrollbar = self.output.verticalScrollBar()
        follow = scrollbar.value() >= scrollbar.maximum() - 30
        if len(self.entries) == 1:
            self.output.clear()
        self.output.append(fragment)
        if follow:
            scrollbar.setValue(scrollbar.maximum())

    def export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出本次执行记录", "windows-use-history.txt", "文本文件 (*.txt)"
        )
        if path:
            try:
                from pathlib import Path

                Path(path).write_text(
                    "\n\n".join(
                        f"[{e['time']}] {e['title']}\n{e['content']}" for e in self.entries
                    ),
                    encoding="utf-8",
                )
                self.run_note.setText("执行记录已导出。")
            except OSError as error:
                self.run_note.setText(f"导出失败：{error}")

    def closeEvent(self, event):
        if self._quitting:
            event.accept()
        else:
            event.ignore()
            self.background_requested.emit()


class PetOverlay(QWidget):
    open_requested = Signal(int)
    input_requested = Signal()
    background_requested = Signal()
    moved = Signal()

    def __init__(self, config):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("window-Stand User · 桌宠")
        self.setStyleSheet(STYLE)
        self.passive = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)
        self.canvas = PetCanvas()
        layout.addWidget(self.canvas, 0, Qt.AlignmentFlag.AlignCenter)
        self.menu = QMenu(self)
        self.menu.setStyleSheet(
            "QMenu { background: #171520; border: 1px solid #51465f; padding: 6px; }"
            "QMenu::item { padding: 8px 24px; border-radius: 5px; }"
            "QMenu::item:selected { background: #51425f; }"
        )
        for title, tab in (("任务", 0), ("设置", 1), ("换装", 2)):
            self.menu.addAction(title, lambda index=tab: self.open_requested.emit(index))
        self.menu.addSeparator()
        self.menu.addAction("隐藏到系统托盘", self.background_requested.emit)
        self.canvas.clicked.connect(self.input_requested.emit)
        self.canvas.dragged.connect(self._drag)
        self.canvas.drag_finished.connect(self.moved.emit)
        self.configure(config)
        self.mask_timer = QTimer(self)
        self.mask_timer.timeout.connect(self.update_hit_region)
        # Native layered windows already hit-test transparent pixels. Updating a window
        # region on every frame forces native redraws and can cause visible flashing.
        if QApplication.platformName() != "windows":
            self.mask_timer.start(100)

    def update_hit_region(self):
        if not self.isVisible() or self.canvas.press is not None:
            return
        pixmap = QPixmap(self.canvas.size() * self.devicePixelRatioF())
        pixmap.setDevicePixelRatio(self.devicePixelRatioF())
        pixmap.fill(Qt.GlobalColor.transparent)
        self.canvas.render(pixmap, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
        mask = pixmap.toImage().createMaskFromColor(
            QColor(0, 0, 0, 0).rgba(), Qt.MaskMode.MaskInColor
        )
        # Masks use logical window coordinates, not physical high-DPI pixels.
        mask = mask.scaled(self.canvas.size())
        region = QRegion(QBitmap.fromImage(mask)).translated(self.canvas.pos())
        if not region.isEmpty() and region != self.mask():
            self.setMask(region)

    def show_menu(self):
        if not self.passive:
            self.menu.popup(QCursor.pos())

    def contextMenuEvent(self, event):
        if not self.passive:
            self.menu.popup(event.globalPos())

    def _drag(self, delta):
        self.move(self.pos() + delta)

    def configure(self, config):
        self.config = config
        size = config["pet_size"]
        self.canvas.setFixedSize(size, size)
        self.setFixedSize(size + 16, size + 16)
        self.canvas.configure(config)
        self.adjustSize()

    def set_state(self, state, text):
        self.canvas.set_state(state)

    def set_passive(self, passive):
        self.passive = passive
        self.menu.hide()
        # Worker hides us before every action; stay draggable while it reasons.
        protect_overlay(self, False)
        self.adjustSize()

    def icon(self):
        pixmap = QPixmap(200, 200)
        pixmap.fill(Qt.GlobalColor.transparent)
        self.canvas.render(pixmap)
        return QIcon(pixmap)


class PetActions(QWidget):
    settings_requested = Signal()
    task_requested = Signal()

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(STYLE)
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(5)
        self.settings = button("设置", self.settings_requested.emit)
        self.task = button("任务", self.task_requested.emit, "primary")
        for widget in (self.settings, self.task):
            widget.setFixedSize(58, 28)
            widget.setStyleSheet("border-radius:14px; padding:2px;")
            row.addWidget(widget)
        self.setFixedSize(125, 32)
        protect_overlay(self, False)
        self.outside_timer = QTimer(self)
        self.outside_timer.setInterval(25)
        self.outside_timer.timeout.connect(self.check_outside_click)
        self._mouse_was_down = False
        self.dismissed_at = 0.0
        QApplication.instance().installEventFilter(self)

    def showEvent(self, event):
        self._mouse_was_down = mouse_buttons_down()
        self.outside_timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.outside_timer.stop()
        super().hideEvent(event)

    def check_outside_click(self):
        down = mouse_buttons_down()
        if down and not self._mouse_was_down and not self.frameGeometry().contains(QCursor.pos()):
            self.dismiss_outside()
        self._mouse_was_down = down

    def dismiss_outside(self):
        self.dismissed_at = time.monotonic()
        self.hide()

    def eventFilter(self, watched, event):
        if self.isVisible() and event.type() == QEvent.Type.MouseButtonPress:
            if not self.frameGeometry().contains(event.globalPosition().toPoint()):
                self.dismiss_outside()
        return False


class QuickInput(QWidget):
    run_requested = Signal(str)
    dismissed = Signal()

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setWindowTitle("window-Stand User · 任务")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(STYLE)
        self.setFixedWidth(276)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background:#171520; border:1px solid #51425f; border-radius:18px; }"
        )
        outer.addWidget(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 12, 10)
        header = QHBoxLayout()
        self.note = label("新任务", "muted")
        self.note.setStyleSheet("border:0; background:transparent;")
        header.addWidget(self.note, 1)
        self.close_button = button("×", self.dismiss)
        self.close_button.setFixedSize(24, 24)
        self.close_button.setStyleSheet(
            "border:0; background:transparent; padding:0; font-size:19px;"
        )
        self.close_button.setToolTip("关闭输入框；已暂停的任务将继续")
        header.addWidget(self.close_button)
        layout.addLayout(header)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("想让我做什么？")
        self.input.setStyleSheet(
            "QLineEdit { border:0; border-radius:12px; padding:7px; background:white; color:#171520; }"
        )
        self.input.returnPressed.connect(self.submit)
        self.send = button("发送", self.submit, "primary")
        self.send.setFixedSize(48, 32)
        self.send.setStyleSheet("border-radius:16px; padding:2px;")
        row.addWidget(self.input, 1)
        row.addWidget(self.send)
        layout.addLayout(row)
        self.set_busy(False)

    def set_busy(self, busy, paused=False):
        self.input.setEnabled(not busy)
        self.send.setEnabled(not busy)
        self.note.setText(
            "任务暂停中，关闭可继续" if paused else "正在执行任务" if busy else "新任务"
        )

    def submit(self):
        if self.input.isEnabled() and self.input.text().strip():
            self.run_requested.emit(self.input.text().strip())

    def dismiss(self):
        self.hide()
        self.dismissed.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.dismiss()
        else:
            super().keyPressEvent(event)


class Observer(QWidget):
    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(STYLE)
        self.setFixedSize(300, 184)
        self.has_content = False
        self.last_step = None
        self.phase_started = time.monotonic()
        self.phase_text = "等待任务"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        card = QFrame()
        card.setObjectName("card")
        inner = QVBoxLayout(card)
        self.title = label("等待任务", "muted")
        inner.addWidget(self.title)
        self.phase_label = label("观察 → 思考 → 执行", "muted")
        inner.addWidget(self.phase_label)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.output.setFont(QFont("Microsoft YaHei UI", 10))
        self.output.document().setMaximumBlockCount(0)
        inner.addWidget(self.output, 1)
        layout.addWidget(card)
        protect_overlay(self, False)
        self.clock = QTimer(self)
        self.clock.timeout.connect(self.update_elapsed)
        self.clock.start(1000)
        self.auto_scroll = QTimer(self)
        self.auto_scroll.setInterval(700)
        self.auto_scroll.timeout.connect(self.scroll_tick)
        self.auto_scroll.start()
        self.scroll_after = time.monotonic() + 2
        self.output.viewport().installEventFilter(self)
        self.output.verticalScrollBar().sliderPressed.connect(self.pause_scroll)

    def pause_scroll(self):
        self.scroll_after = time.monotonic() + 5

    def eventFilter(self, watched, event):
        if event.type() in {QEvent.Type.Wheel, QEvent.Type.MouseButtonPress}:
            self.pause_scroll()
        return False

    def scroll_tick(self):
        if not self.isVisible() or time.monotonic() < self.scroll_after or self.underMouse():
            return
        bar = self.output.verticalScrollBar()
        if bar.value() < bar.maximum():
            bar.setValue(min(bar.maximum(), bar.value() + 1))

    def update_elapsed(self):
        seconds = int(time.monotonic() - self.phase_started)
        suffix = (
            f" · {seconds} 秒"
            if self.phase_text
            in {
                "观察中",
                "思考中",
                "执行中",
                "等待下一步",
                "大脑整理与规划",
                "视觉识别中",
                "行动者思考中",
                "复用已验证流程",
            }
            else ""
        )
        step = f"第 {self.last_step + 1} 步 · " if isinstance(self.last_step, int) else ""
        self.title.setText(step + self.phase_text + suffix)

    def append(self, title, content, step=None, force=False):
        if title != self.phase_text or step != self.last_step:
            self.phase_started = time.monotonic()
        self.last_step = step
        self.has_content = True
        self.phase_text = title
        self.update_elapsed()
        self.output.setPlainText(content)
        self.output.verticalScrollBar().setValue(0)
        self.scroll_after = time.monotonic() + 2
