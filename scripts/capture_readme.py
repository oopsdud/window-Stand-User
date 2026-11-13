"""Render README screenshots from real widgets and fixture data, without a model or user profile."""

import copy
import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["ANONYMIZED_TELEMETRY"] = "false"
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "frontend")]

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from desktop_app.pet import PetCanvas  # noqa: E402
from desktop_app.settings import DEFAULTS  # noqa: E402
from desktop_app.vm_ui import VMPanel  # noqa: E402
from desktop_app.widgets import STYLE, DeskWindow, QuickInput, ensure_fonts  # noqa: E402


def main():
    output = ROOT / "docs/images"
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    ensure_fonts()
    config = copy.deepcopy(DEFAULTS)
    config["reduce_motion"] = True
    desk = DeskWindow(config)
    desk.resize(1100, 820)
    desk.task.setPlainText("10 分钟后检查下载文件夹，帮我整理今天的文档。")
    desk.output.setHtml(
        "<div style='padding:24px'><h2 style='color:#dfc487'>白金之星 · 任务工作台</h2><p>在这里输入任务，查看执行步骤，或安排稍后执行。</p><p style='color:#a59cb7'>截图展示用示例内容 · 未调用模型或执行桌面动作</p></div>"
    )
    desk.show()
    app.processEvents()
    desk.grab().save(str(output / "task-workspace.png"))
    desk.tabs.setCurrentIndex(3)
    app.processEvents()
    desk.grab().save(str(output / "model-roles.png"))
    desk._quitting = True
    desk.close()

    scroll = QScrollArea()
    scroll.setObjectName("desk")
    scroll.setStyleSheet(STYLE)
    scroll.setWidgetResizable(True)
    panel = VMPanel()
    panel.sections["首次准备"][0].setChecked(False)
    panel.sections["日常使用"][0].setChecked(True)
    panel.status.setText("未连接客户机 · 请先完成系统安装，再检测连接。")
    scroll.setWidget(panel)
    scroll.resize(860, 850)
    scroll.show()
    app.processEvents()
    scroll.grab().save(str(output / "virtual-machine.png"))
    scroll.close()

    # A presentation surface containing the actual pet and quick-input widgets.
    stage = QWidget()
    stage.setObjectName("desk")
    stage.setStyleSheet(STYLE)
    layout = QHBoxLayout(stage)
    layout.setContentsMargins(48, 40, 48, 40)
    text = QVBoxLayout()
    title = QLabel("window-Stand User")
    title.setStyleSheet("font-size:32px;color:#dfc487;font-weight:700;")
    text.addWidget(title)
    subtitle = QLabel("A desktop companion that gets things done.")
    subtitle.setStyleSheet("font-size:17px;color:#b7acc9;")
    text.addWidget(subtitle)
    text.addStretch()
    quick = QuickInput()
    quick.setParent(stage)
    quick.setWindowFlags(Qt.WindowType.Widget)
    quick.note.setText("任务输入")
    quick.input.setText("帮我整理文件")
    text.addWidget(quick)
    layout.addLayout(text, 1)
    pet = PetCanvas()
    pet.configure(config)
    pet.setFixedSize(260, 300)
    layout.addWidget(pet)
    stage.resize(1000, 410)
    stage.show()
    app.processEvents()
    stage.grab().save(str(output / "companion.png"))
    stage.close()
    print("Rendered 4 fixture screenshots into docs/images; no user profile loaded.")


if __name__ == "__main__":
    main()
