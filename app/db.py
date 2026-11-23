"""SQLite storage: projects, chat messages, tasks, runs, and the task log (every step writes here)."""
import json
import os
import sqlite3
import threading
import time
import uuid

from pathlib import Path

from .config import env, store_dir

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, objective TEXT, files TEXT, setup TEXT, created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, role TEXT, content TEXT,
    attachments TEXT, created_at REAL, category TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, project_id TEXT, objective TEXT, summary TEXT, plan TEXT, status TEXT,
    result TEXT, error TEXT, created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, idx INTEGER, label TEXT, inputs TEXT,
    outputs TEXT, checks TEXT, passed INTEGER, status TEXT, attempts INTEGER DEFAULT 0, error TEXT,
    file TEXT, updated_at REAL);
CREATE TABLE IF NOT EXISTS task_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, task_id TEXT, event TEXT, detail TEXT,
    created_at REAL);
"""

JSON_COLS = {"files", "setup", "attachments", "plan", "result", "inputs", "outputs", "checks"}


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        path = Path(env("DB_PATH", "automation.db"))
        if not path.is_absolute():  # always next to the app, whatever folder it was started from
            path = store_dir() / path
        _conn = sqlite3.connect(str(path), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        cols = [r[1] for r in _conn.execute("PRAGMA table_info(messages)")]
        if "category" not in cols:  # databases made by the earlier version
            _conn.execute("ALTER TABLE messages ADD COLUMN category TEXT DEFAULT ''")
            _conn.commit()
    return _conn


def reset(path: str) -> None:
    """Point the store at a new database file (used by tests)."""
    global _conn
    if _conn is not None:
        _conn.close()
    _conn = None
    os.environ["DB_PATH"] = path
    conn()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _row(r) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS & d.keys():
        if d[k]:
            d[k] = json.loads(d[k])
    return d


def _exec(sql: str, args: tuple = ()) -> sqlite3.Cursor:
    with _lock:
        cur = conn().execute(sql, args)
        conn().commit()
        return cur


def _dump(v):
    return json.dumps(v, default=str) if isinstance(v, (dict, list)) else v


# --- task log -----------------------------------------------------------------
def log(event: str, detail: dict | str = "", project_id: str | None = None, task_id: str | None = None) -> None:
    if not isinstance(detail, str):
        detail = json.dumps(detail, default=str)
    _exec("INSERT INTO task_log (project_id, task_id, event, detail, created_at) VALUES (?,?,?,?,?)",
          (project_id, task_id, event, detail, time.time()))


def get_log(project_id: str | None = None, limit: int = 300) -> list[dict]:
    q, args = "SELECT * FROM task_log", ()
    if project_id:
        q, args = q + " WHERE project_id = ?", (project_id,)
    return [dict(r) for r in _query(q + " ORDER BY id DESC LIMIT ?", args + (limit,))]


# --- projects -------------------------------------------------------------------
def create_project(objective: str, files: list[dict]) -> str:
    pid, now = new_id(), time.time()
    setup = {"variables": [], "targets": [], "goal": None, "ready": False}
    _exec("INSERT INTO projects VALUES (?,?,?,?,?,?)", (pid, objective, _dump(files), _dump(setup), now, now))
    return pid


def _query(sql: str, args: tuple = ()) -> list:
    """Reads go through the same lock as writes: the engine thread and web requests share one connection."""
    with _lock:
        return conn().execute(sql, args).fetchall()


def get_project(pid: str) -> dict | None:
    rows = _query("SELECT * FROM projects WHERE id = ?", (pid,))
    return _row(rows[0]) if rows else None


def update_project(pid: str, **fields) -> None:
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k} = ?" for k in fields)
    _exec(f"UPDATE projects SET {cols} WHERE id = ?", (*map(_dump, fields.values()), pid))


# --- messages -------------------------------------------------------------------
def add_message(pid: str, role: str, content: str, attachments: list | None = None, category: str = "") -> None:
    _exec("INSERT INTO messages (project_id, role, content, attachments, created_at, category) VALUES (?,?,?,?,?,?)",
          (pid, role, content, _dump(attachments or []), time.time(), category))


def get_messages(pid: str) -> list[dict]:
    rows = _query("SELECT role, content, attachments, category FROM messages WHERE project_id = ? ORDER BY id",
                  (pid,))
    return [_row(r) for r in rows]


# --- tasks & runs -----------------------------------------------------------------
def create_task(pid: str, objective: str, summary: str, plan: dict) -> str:
    tid, now = new_id(), time.time()
    _exec("INSERT INTO tasks (id, project_id, objective, summary, plan, status, created_at, updated_at)"
          " VALUES (?,?,?,?,?,?,?,?)", (tid, pid, objective, summary, _dump(plan), "awaiting_approval", now, now))
    return tid


def update_task(tid: str, **fields) -> None:
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k} = ?" for k in fields)
    _exec(f"UPDATE tasks SET {cols} WHERE id = ?", (*map(_dump, fields.values()), tid))


def get_task(tid: str) -> dict | None:
    rows = _query("SELECT * FROM tasks WHERE id = ?", (tid,))
    return _row(rows[0]) if rows else None


def add_runs(tid: str, runs: list[dict]) -> None:
    with _lock:
        for i, r in enumerate(runs):
            conn().execute("INSERT INTO runs (task_id, idx, label, inputs, status, updated_at) VALUES (?,?,?,?,?,?)",
                           (tid, i, r["label"], _dump(r["inputs"]), "pending", time.time()))
        conn().commit()


def update_run(run_id: int, **fields) -> None:
    fields["updated_at"] = time.time()
    if "passed" in fields and fields["passed"] is not None:
        fields["passed"] = int(fields["passed"])
    cols = ", ".join(f"{k} = ?" for k in fields)
    _exec(f"UPDATE runs SET {cols} WHERE id = ?", (*map(_dump, fields.values()), run_id))


def get_runs(tid: str) -> list[dict]:
    return [_row(r) for r in _query("SELECT * FROM runs WHERE task_id = ? ORDER BY idx", (tid,))]
