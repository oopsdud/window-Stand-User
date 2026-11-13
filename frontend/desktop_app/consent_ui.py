"""Non-activating, fail-closed approval card for one concrete desktop action."""

import json

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from desktop_app.widgets import STYLE, TOOL_NAMES


class ConsentCard(QWidget):
    answered = Signal(dict)

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("白金之星 · 桌面控制请求")
        self.setObjectName("desk")
        self.setStyleSheet(STYLE)
        self.setMinimumSize(410, 300)
        self.resize(460, 340)
        self.request_id = None
        layout = QVBoxLayout(self)
        self.title = QLabel()
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details)
        note = QLabel("仅允许本次动作；批准后会重新核对界面。\n未回复、关闭或超时均不执行。")
        layout.addWidget(note)
        row = QHBoxLayout()
        for text, approved in (("允许本次动作", True), ("拒绝 / 我来操作", False)):
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False, answer=approved: self.reply(answer))
            row.addWidget(button)
        layout.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: self.reply(False))

    def present(self, data, visible=True):
        if self.request_id:
            self.reply(False)
        self.request_id = data["request_id"]
        tool = data.get("tool", "")
        self.title.setText(
            f"需要使用桌面：{data.get('target', '')[:180]}\n操作：{TOOL_NAMES.get(tool, tool)}"
        )
        self.details.setPlainText(json.dumps(data.get("params", {}), ensure_ascii=False, indent=2))
        self.timer.start(120000)
        self.setVisible(visible)

    def reply(self, approved):
        request_id, self.request_id = self.request_id, None
        self.timer.stop()
        self.hide()
        if request_id:
            self.answered.emit(
                {"command": "consent", "request_id": request_id, "approved": approved}
            )

    def closeEvent(self, event):
        self.reply(False)
        event.accept()

    def dismiss(self):
        self.request_id = None
        self.timer.stop()
        self.hide()
