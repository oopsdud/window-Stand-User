"""Timer list and one-at-a-time dispatch through the normal Agent worker."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from windows_use.timers import TimerStore


class TimerDispatcher:
    def __init__(self, store, launch):
        self.store, self.launch = store, launch
        self.active = None
        store.recover()

    def tick(self, busy=False, now=None):
        if busy or self.active:
            return
        job = self.store.claim(now)
        if job is None:
            return
        self.active = job["id"]
        try:
            if not self.launch(job):
                self.finish("failed", "无法启动，请检查模型配置或运行环境。")
        except Exception as error:
            self.finish("failed", str(error))

    def finish(self, status, note=""):
        if self.active:
            self.store.finish(self.active, status, note)
            self.active = None


class TimerPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.path = None
        layout = QVBoxLayout(self)
        note = QLabel(
            "照常输入任务并说明时间，例如‘10 分钟后帮我……’。保持桌宠运行；错过超过 5 分钟不补做。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.entries = QListWidget()
        self.entries.setWordWrap(True)
        layout.addWidget(self.entries, 1)
        row = QHBoxLayout()
        refresh = QPushButton("刷新列表")
        refresh.clicked.connect(self.refresh)
        cancel = QPushButton("取消选中的定时任务")
        cancel.clicked.connect(self.cancel)
        row.addWidget(refresh)
        row.addWidget(cancel)
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    def refresh(self):
        if self.path is None:
            return
        selected = self.entries.currentItem()
        ident = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        self.entries.clear()
        names = {
            "pending": "等待执行",
            "running": "执行中",
            "done": "已完成",
            "failed": "未完成",
            "cancelled": "已取消",
            "missed": "已错过",
            "interrupted": "执行中断",
        }
        try:
            rows = TimerStore(self.path).list()
            for job in rows:
                env = "虚拟机" if job["environment"] == "vm" else "本机"
                item = QListWidgetItem(
                    f"{job['run_at']} · {names.get(job['status'], job['status'])} · {env}\n{job['task']}\n{job['note']}"
                )
                item.setData(Qt.ItemDataRole.UserRole, job["id"])
                item.setToolTip("任务 ID：" + job["id"])
                self.entries.addItem(item)
                if ident == job["id"]:
                    self.entries.setCurrentItem(item)
            self.status.setText(f"共 {len(rows)} 条记录。执行中的任务使用桌宠停止按钮取消。")
        except Exception as error:
            self.status.setText(str(error))

    def cancel(self):
        selected = self.entries.currentItem()
        if not selected or self.path is None:
            self.status.setText("请先选择一个等待执行的任务。")
            return
        try:
            TimerStore(self.path).cancel(selected.data(Qt.ItemDataRole.UserRole))
            self.refresh()
        except Exception as error:
            self.status.setText(str(error))
