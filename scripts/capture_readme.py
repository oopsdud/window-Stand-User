"""Render README screenshots from real widgets and fixture data, without a model or user profile."""

import copy
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

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

from desktop_app.consent_ui import ConsentCard  # noqa: E402
from desktop_app.pet import PetCanvas  # noqa: E402
from desktop_app.settings import DEFAULTS  # noqa: E402
from desktop_app.vm_ui import VMPanel  # noqa: E402
from desktop_app.widgets import STYLE, DeskWindow, QuickInput, ensure_fonts  # noqa: E402
from windows_use.execution import workspace_operation  # noqa: E402
from windows_use.memory import MemoryStore  # noqa: E402
from windows_use.timers import TimerStore  # noqa: E402


def capture_runtime(app, desk, output):
    """Run bounded local tools; illustrate the UI without starting an agent or timer worker."""
    local = ROOT / ".local"
    local.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="readme-demo-", dir=local) as directory:
        demo = Path(directory)
        assert demo.resolve().is_relative_to(local.resolve())
        workspace = demo / "workspace"
        source = "项目演示：整理文档、记录进度、保留可核验的结果。"
        workspace_operation(workspace, "create", "notes.txt", source)
        desk.memory_panel.path = demo / "memory.sqlite3"
        desk.timer_panel.path = demo / "timers.sqlite3"
        memory = MemoryStore(desk.memory_panel.path)
        request = "读取演示目录中的 notes.txt，生成一份工作摘要。"
        ident = memory.start(request)
        try:
            desk.tabs.setCurrentIndex(0)
            desk.resize(1100, 1040)
            desk.task.setPlainText(request)
            desk.set_busy(True)
            desk.status.setText("● 本地工具执行中")
            desk.append("演示说明", "真实文件工具执行 · 临时演示目录 · 未调用模型或操作鼠标")
            listing = workspace_operation(workspace, "list")
            desk.append("观察文件 · 第 1 步", f"工作目录内容：{listing}")
            memory.append(ident, 1, "workspace_list", {"result": listing})
            content = workspace_operation(workspace, "read", "notes.txt")
            desk.append("读取内容 · 第 2 步", content)
            memory.append(ident, 2, "workspace_read", {"file": "notes.txt", "result": content})
            app.processEvents()
            desk.grab().save(str(output / "task-running.png"))
            report = "# 工作摘要\n\n" + content + "\n\n来源：notes.txt\n"
            result = workspace_operation(workspace, "create", "summary.md", report)
            assert workspace_operation(workspace, "read", "summary.md") == report
            desk.append("写入并核验 · 第 3 步", result + "\n已重新读取文件，内容校验通过。")
            memory.append(ident, 3, "workspace_create", {"file": "summary.md", "verified": True})
            memory.finish(
                ident, "completed", "本地工具演示完成：summary.md 已创建并回读核验；未调用模型。"
            )
            desk.append("任务结果", "已生成 summary.md。\n\n" + report)
            desk.set_busy(False)
            desk.status.setText("● 演示已完成")
            app.processEvents()
            desk.output.verticalScrollBar().setValue(desk.output.verticalScrollBar().maximum())
            desk.grab().save(str(output / "task-result.png"))
            TimerStore(desk.timer_panel.path).create(
                "演示：10 分钟后检查工作目录，汇总新增文档。", "host", delay_seconds=600
            )
            desk.timer_toggle.setChecked(True)
            desk.timer_panel.refresh()
            app.processEvents()
            desk.grab().save(str(output / "scheduled-task.png"))
            desk.timer_toggle.setChecked(False)
            desk.tabs.setCurrentIndex(4)
            app.processEvents()
            desk.memory_panel.detail.verticalScrollBar().setValue(0)
            desk.grab().save(str(output / "task-history.png"))
        finally:
            memory.close()
    card = ConsentCard()
    card.resize(620, 390)
    card.present(
        {
            "request_id": "readme-demo",
            "tool": "click_tool",
            "target": "示例文档窗口（界面演示，不执行点击）",
            "params": {"loc": [640, 420], "button": "left", "clicks": 1},
        }
    )
    app.processEvents()
    card.grab().save(str(output / "desktop-approval.png"))
    card.dismiss()
    card.close()


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
    capture_runtime(app, desk, output)
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
    print(
        "Rendered 9 screenshots; local tools verified; no model, host input or user profile used."
    )


if __name__ == "__main__":
    main()
