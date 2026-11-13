"""Local, transactional task/trace/preference/procedure storage. No model or UI imports."""

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def terms(text):
    """English words and Chinese bigrams, without an embedding service dependency."""
    words = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text.lower())
    return set(
        t
        for w in words
        for t in ([w] if w.isascii() else [w[i : i + 2] for i in range(max(1, len(w) - 1))])
    )


class MemoryStore:
    def __init__(self, path, secrets=()):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.secrets = tuple(s for s in secrets if s)
        self.db = sqlite3.connect(self.path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, request TEXT NOT NULL, status TEXT NOT NULL,
                started TEXT NOT NULL, finished TEXT, summary TEXT DEFAULT '',
                checkpoint TEXT DEFAULT '{}', parent_id TEXT);
            CREATE TABLE IF NOT EXISTS task_steps (
                id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                step INTEGER NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, created TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS steps_task ON task_steps(task_id, id);
            CREATE TABLE IF NOT EXISTS preferences (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, value TEXT NOT NULL,
                source_task TEXT, source_quote TEXT NOT NULL DEFAULT '',
                confirmed INTEGER NOT NULL DEFAULT 0, updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS workflows (
                id TEXT PRIMARY KEY, task_key TEXT NOT NULL, title TEXT NOT NULL,
                plan TEXT NOT NULL, source_task TEXT NOT NULL,
                successes INTEGER NOT NULL DEFAULT 1, failures INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1, updated TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS workflows_task ON workflows(task_key, enabled);
            PRAGMA user_version=1;
        """)

    def clean(self, value):
        if isinstance(value, dict):
            return json.dumps(
                {str(k): self._clean_value(v) for k, v in value.items()},
                ensure_ascii=False,
                default=str,
            )
        if not isinstance(value, str):
            return json.dumps(self._clean_value(value), ensure_ascii=False, default=str)
        text = value
        for secret in sorted(self.secrets, key=len, reverse=True):
            text = text.replace(secret, "[secret]")
        # Redact obvious bearer/key/password fields too; this is not a general PII detector.
        return re.sub(
            r"(?i)((?:api[_-]?key|password|authorization|密码)\s*[=:]\s*)[^\s,;]+",
            r"\1[secret]",
            text,
        )

    def _clean_value(self, value):
        if isinstance(value, str):
            return self.clean(value)
        if isinstance(value, dict):
            return {self.clean(str(k)): self._clean_value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._clean_value(v) for v in value]
        return value

    def recover_interrupted(self):
        """Only the singleton GUI calls this on startup, never an arbitrary reader."""
        with self.db:
            self.db.execute(
                "UPDATE tasks SET status='interrupted',finished=? WHERE status='running'", (now(),)
            )

    def start(self, request, parent_id=None):
        task_id = uuid.uuid4().hex
        with self.db:
            self.db.execute(
                "INSERT INTO tasks(id,request,status,started,parent_id) VALUES(?,?,?,?,?)",
                (task_id, self.clean(request), "running", now(), parent_id),
            )
        return task_id

    def append(self, task_id, step, kind, data):
        with self.db:
            self.db.execute(
                "INSERT INTO task_steps(task_id,step,kind,data,created) VALUES(?,?,?,?,?)",
                (task_id, step, kind, self.clean(data), now()),
            )

    def checkpoint(self, task_id, data):
        with self.db:
            self.db.execute("UPDATE tasks SET checkpoint=? WHERE id=?", (self.clean(data), task_id))

    def finish(self, task_id, status, summary):
        if status not in {"completed", "failed", "cancelled", "interrupted"}:
            raise ValueError("Invalid terminal status")
        with self.db:
            self.db.execute(
                "UPDATE tasks SET status=?,summary=?,finished=? WHERE id=? AND status='running'",
                (status, self.clean(summary), now(), task_id),
            )

    def recent(self, limit=40):
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM tasks ORDER BY rowid DESC LIMIT ?", (min(limit, 100),)
            )
        ]

    def read_task(self, task_id, after=0, limit=30):
        task = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            return {}
        rows = self.db.execute(
            "SELECT * FROM task_steps WHERE task_id=? AND id>? ORDER BY id LIMIT ?",
            (task_id, after, min(max(limit, 1), 50)),
        ).fetchall()
        return {
            "task": dict(task),
            "steps": [dict(r) for r in rows],
            "next_after": rows[-1]["id"] if rows else after,
        }

    def search(self, query, limit=5):
        tokens = terms(query)
        # Bounded candidate scan. Rankings reward overlap; recency breaks ties.
        rows = self.db.execute(
            "SELECT id,request,status,summary,started FROM tasks ORDER BY rowid DESC LIMIT 1000"
        ).fetchall()
        scored = [
            (len(tokens & terms(r["request"] + " " + r["summary"])), -i, dict(r))
            for i, r in enumerate(rows)
        ]
        found = [
            r
            for score, _, r in sorted(scored, key=lambda x: (x[0], x[1]), reverse=True)
            if score > 0
        ][: min(limit, 10)]
        return [
            {**r, "request": r["request"][:1000], "summary": r["summary"][:1800]} for r in found
        ]

    def read_trace(self, entry_id, offset=0):
        row = self.db.execute(
            "SELECT id,task_id,kind,data FROM task_steps WHERE id=?", (entry_id,)
        ).fetchone()
        if not row:
            return {}
        data = row["data"]
        offset = max(0, offset)
        end = min(len(data), offset + 12000)
        return {
            "id": row["id"],
            "task_id": row["task_id"],
            "kind": row["kind"],
            "content": data[offset:end],
            "next_offset": end if end < len(data) else None,
        }

    def preferences(self, confirmed_only=True):
        sql = "SELECT * FROM preferences" + (" WHERE confirmed=1" if confirmed_only else "")
        return [dict(r) for r in self.db.execute(sql + " ORDER BY updated DESC LIMIT 100")]

    def relevant_preferences(self, query, budget=6000):
        tokens = terms(query)
        rows = self.preferences()
        rows.sort(key=lambda r: len(tokens & terms(r["name"] + " " + r["value"])), reverse=True)
        selected = []
        for row in rows:
            size = len(row["name"]) + len(row["value"])
            if size > budget:
                continue
            selected.append({k: row[k] for k in ("id", "name", "value", "updated")})
            budget -= size
            if len(selected) >= 20:
                break
        return selected

    def put_preference(self, name, value, *, confirmed=False, source_task=None, quote=""):
        if not name.strip() or not value.strip() or len(name) > 100 or len(value) > 2000:
            raise ValueError("偏好名称不能为空且不超过 100 字，内容为 1–2000 字。")
        with self.db:
            # A model suggestion may never replace a user-confirmed preference.
            self.db.execute(
                """INSERT INTO preferences(name,value,confirmed,source_task,source_quote,updated)
                VALUES(?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value,
                confirmed=excluded.confirmed,source_task=excluded.source_task,
                source_quote=excluded.source_quote,updated=excluded.updated
                WHERE excluded.confirmed=1 OR preferences.confirmed=0""",
                (
                    self.clean(name),
                    self.clean(value),
                    int(confirmed),
                    source_task,
                    self.clean(quote),
                    now(),
                ),
            )

    def confirm_preference(self, preference_id):
        with self.db:
            self.db.execute(
                "UPDATE preferences SET confirmed=1,updated=? WHERE id=?", (now(), preference_id)
            )

    def delete(self, kind, item_id):
        table = {"task": "tasks", "preference": "preferences", "workflow": "workflows"}[kind]
        with self.db:
            self.db.execute(f"DELETE FROM {table} WHERE id=?", (item_id,))

    def workflows(self, task=None):
        if task is None:
            rows = self.db.execute("SELECT * FROM workflows ORDER BY updated DESC LIMIT 100")
        else:
            rows = self.db.execute(
                "SELECT * FROM workflows WHERE task_key=? AND enabled=1 ORDER BY successes DESC LIMIT 5",
                (self.clean(task.strip().casefold()),),
            )
        return [dict(r) for r in rows]

    def save_workflow(self, task, title, plan, task_id):
        workflow_id = uuid.uuid4().hex
        with self.db:
            self.db.execute(
                "INSERT INTO workflows(id,task_key,title,plan,source_task,updated) VALUES(?,?,?,?,?,?)",
                (
                    workflow_id,
                    self.clean(task.strip().casefold()),
                    self.clean(title),
                    self.clean(plan),
                    task_id,
                    now(),
                ),
            )
        return workflow_id

    def workflow_result(self, workflow_id, success):
        with self.db:
            if success:
                self.db.execute(
                    "UPDATE workflows SET successes=successes+1,updated=? WHERE id=?",
                    (now(), workflow_id),
                )
            else:
                self.db.execute(
                    "UPDATE workflows SET failures=failures+1,enabled=0,updated=? WHERE id=?",
                    (now(), workflow_id),
                )

    def close(self):
        self.db.close()
