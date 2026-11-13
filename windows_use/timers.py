"""Persistent one-shot task timers. No saved coordinates, credentials or desktop approvals."""

import json
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

GRACE_SECONDS = 300


class TimerStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS timers (
                id TEXT PRIMARY KEY, task TEXT NOT NULL, due REAL NOT NULL,
                environment TEXT NOT NULL, risks TEXT NOT NULL, status TEXT NOT NULL,
                created REAL NOT NULL, note TEXT NOT NULL DEFAULT '')""")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=3)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def public(row):
        result = dict(row)
        result["run_at"] = (
            datetime.fromtimestamp(result["due"], timezone.utc)
            .astimezone()
            .isoformat(timespec="seconds")
        )
        result["risks"] = json.loads(result["risks"])
        return result

    def create(self, task, environment, risks=(), delay_seconds=None, run_at=None, now=None):
        now = time.time() if now is None else now
        if not isinstance(task, str) or not 1 <= len(task.strip()) <= 4000:
            raise ValueError("定时任务内容须为 1～4000 字。")
        if environment not in {"host", "vm"}:
            raise ValueError("未知执行环境。")
        if (delay_seconds is None) == (not run_at):
            raise ValueError("请且仅提供 delay_seconds 或 run_at。")
        if run_at:
            moment = datetime.fromisoformat(run_at.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                raise ValueError("指定时间需含时区，例如 2026-11-13T18:30:00+08:00。")
            due = moment.timestamp()
        else:
            due = now + float(delay_seconds)
        if not math.isfinite(due) or not 1 <= due - now <= 30 * 86400:
            raise ValueError("定时时间需在 1 秒至 30 天内。")
        ident = uuid.uuid4().hex
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if (
                db.execute("SELECT COUNT(*) FROM timers WHERE status='pending'").fetchone()[0]
                >= 100
            ):
                raise ValueError("最多保留 100 个待执行定时任务。")
            db.execute(
                "INSERT INTO timers VALUES (?,?,?,?,?,'pending',?,'')",
                (ident, task.strip(), due, environment, json.dumps(list(risks)), now),
            )
            return self.public(db.execute("SELECT * FROM timers WHERE id=?", (ident,)).fetchone())

    def list(self):
        with self.connection() as db:
            return [
                self.public(row)
                for row in db.execute(
                    "SELECT * FROM timers ORDER BY CASE WHEN status='pending' THEN 0 ELSE 1 END, due DESC LIMIT 200"
                )
            ]

    def cancel(self, ident):
        with self.connection() as db:
            changed = db.execute(
                "UPDATE timers SET status='cancelled',note='用户取消' WHERE id=? AND status='pending'",
                (ident,),
            ).rowcount
        if not changed:
            raise ValueError("未找到待执行定时任务；运行中的任务请使用停止按钮。")
        return "已取消定时任务。"

    def claim(self, now=None):
        now = time.time() if now is None else now
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE timers SET status='missed',note='已超过执行时间 5 分钟，未自动补做' WHERE status='pending' AND due<?",
                (now - GRACE_SECONDS,),
            )
            row = db.execute(
                "SELECT * FROM timers WHERE status='pending' AND due<=? ORDER BY due LIMIT 1",
                (now,),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE timers SET status='running' WHERE id=? AND status='pending'", (row["id"],)
            )
            return self.public(row)

    def finish(self, ident, status, note=""):
        if status not in {"done", "failed", "cancelled", "interrupted"}:
            raise ValueError("无效定时任务状态。")
        with self.connection() as db:
            db.execute(
                "UPDATE timers SET status=?,note=? WHERE id=? AND status='running'",
                (status, note[:1000], ident),
            )

    def recover(self):
        with self.connection() as db:
            db.execute(
                "UPDATE timers SET status='interrupted',note='程序曾退出；结果可能未知，不自动重做' WHERE status='running'"
            )

    def tool(self, operation, config, task="", delay_seconds=None, run_at=None, timer_id="", **_):
        if operation == "create":
            if config.get("_scheduled_timer"):
                raise ValueError("定时任务不能自动创建后续定时任务；请让用户单独安排。")
            result = self.create(
                task,
                config.get("execution_environment", "host"),
                config.get("allowed_risks", []),
                delay_seconds,
                run_at,
            )
        elif operation == "list":
            result = self.list()
        elif operation == "cancel":
            result = self.cancel(timer_id)
        elif operation == "clock":
            result = datetime.now().astimezone().isoformat(timespec="seconds")
        else:
            raise ValueError("不支持的定时器操作。")
        return json.dumps(
            {
                "result": result,
                "notice": "桌宠须保持运行（可隐藏到托盘）；到时重新观察并按原执行环境执行。桌面授权不继承，超过 5 分钟不补做。",
            },
            ensure_ascii=False,
        )
