"""Local sqlite implementation of the TaskStore (the Work layer).

Mirrors the run_store.py pattern: module-level functions + a `_conn()` that
lazily creates the schema. Lives in its OWN database (`work.sqlite`) separate
from runs.sqlite so the Work layer and Execution layer can evolve independently.

This module *is* the sqlite TaskStore — the module-level functions collectively
satisfy the `TaskStore` Protocol defined in task_store.py.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from agentos.core.config import AGENTOS_ROOT
from agentos.storage.task_store import Project, Sprint, Task

RUNTIME_DIR = AGENTOS_ROOT / "orchestrator" / "runtime"
DB_PATH = RUNTIME_DIR / "work.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT,
    repo_path TEXT,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    lead_agent TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sprints (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    goal TEXT,
    status TEXT NOT NULL DEFAULT 'planned',
    starts_at TEXT,
    ends_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    sprint_id TEXT,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'backlog',
    assignee TEXT,
    priority TEXT NOT NULL DEFAULT 'medium',
    depends_on TEXT,
    acceptance_criteria TEXT,
    estimate_minutes INTEGER,
    parent_task_id TEXT,
    created_by TEXT NOT NULL DEFAULT 'human',
    last_run_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (sprint_id) REFERENCES sprints(id)
);

CREATE TABLE IF NOT EXISTS inbox (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    from_agent TEXT,
    run_id TEXT,
    task_id TEXT,
    sprint_id TEXT,
    kind TEXT NOT NULL DEFAULT 'question',
    prompt TEXT NOT NULL,
    options TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    answer TEXT,
    answered_at TEXT,
    answered_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_sprints_project ON sprints(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_sprint ON tasks(sprint_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_inbox_status ON inbox(status);
CREATE INDEX IF NOT EXISTS idx_inbox_task ON inbox(task_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _task_row(row: sqlite3.Row) -> dict:
    """Decode a task row, parsing the JSON depends_on list."""
    d = dict(row)
    d["depends_on"] = json.loads(d.get("depends_on") or "[]")
    return d


# --------------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------------- #
def create_project(project: Project) -> str:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO projects (id, name, slug, repo_path, description,
               status, lead_agent, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                project.id, project.name, project.slug, project.repo_path,
                project.description, project.status, project.lead_agent,
                project.created_at,
            ),
        )
    return project.id


def get_project(project_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        return dict(row) if row else None


def update_project(slug: str, *, repo_path: str | None = None,
                   description: str | None = None) -> bool:
    """Reconcile mutable project fields by slug. Returns True if a row changed.
    Used by `sync-projects` to keep work-layer repo_path in step with
    config/projects.yaml after repo splits/migrations (stale paths route
    worktrees to the wrong repo)."""
    sets, vals = [], []
    if repo_path is not None:
        sets.append("repo_path = ?")
        vals.append(repo_path)
    if description is not None:
        sets.append("description = ?")
        vals.append(description)
    if not sets:
        return False
    vals.append(slug)
    with _conn() as conn:
        cur = conn.execute(
            f"UPDATE projects SET {', '.join(sets)} WHERE slug = ?", vals
        )
        return cur.rowcount > 0


def list_projects() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM projects ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Sprints
# --------------------------------------------------------------------------- #
def create_sprint(sprint: Sprint) -> str:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO sprints (id, project_id, name, goal, status,
               starts_at, ends_at, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                sprint.id, sprint.project_id, sprint.name, sprint.goal,
                sprint.status, sprint.starts_at, sprint.ends_at,
                sprint.created_at,
            ),
        )
    return sprint.id


def list_sprints(project_id: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sprints WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #
def create_task(task: Task) -> str:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO tasks (id, project_id, sprint_id, title, description,
               status, assignee, priority, depends_on, acceptance_criteria,
               estimate_minutes, parent_task_id, created_by, last_run_id,
               created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task.id, task.project_id, task.sprint_id, task.title,
                task.description, task.status, task.assignee, task.priority,
                json.dumps(task.depends_on), task.acceptance_criteria,
                task.estimate_minutes, task.parent_task_id, task.created_by,
                task.last_run_id, task.created_at,
            ),
        )
    return task.id


def get_task(task_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _task_row(row) if row else None


def list_tasks(
    project_id: str | None = None,
    sprint_id: str | None = None,
    status: str | None = None,
) -> list[dict]:
    clauses, params = [], []
    if project_id:
        clauses.append("project_id = ?")
        params.append(project_id)
    if sprint_id:
        clauses.append("sprint_id = ?")
        params.append(sprint_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM tasks{where} ORDER BY created_at DESC", params
        ).fetchall()
        return [_task_row(r) for r in rows]


def update_task_status(task_id: str, status: str, reason: str | None = None) -> None:
    with _conn() as conn:
        if reason:
            conn.execute(
                """UPDATE tasks
                   SET status = ?,
                       description = COALESCE(description, '') || ?
                   WHERE id = ?""",
                (status, f"\n[status→{status}] {reason}", task_id),
            )
        else:
            conn.execute(
                "UPDATE tasks SET status = ? WHERE id = ?", (status, task_id)
            )


def update_task(task_id: str, **fields: Any) -> None:
    if not fields:
        return
    if "depends_on" in fields and isinstance(fields["depends_on"], list):
        fields["depends_on"] = json.dumps(fields["depends_on"])
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _conn() as conn:
        conn.execute(
            f"UPDATE tasks SET {cols} WHERE id = ?", (*fields.values(), task_id)
        )


def link_run(task_id: str, run_id: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE tasks SET last_run_id = ? WHERE id = ?", (run_id, task_id)
        )


def ready_tasks(sprint_id: str) -> list[dict]:
    """Tasks in a sprint with status='ready' whose deps are all 'done'."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE sprint_id = ? AND status = 'ready'",
            (sprint_id,),
        ).fetchall()
        done_ids = {
            r["id"]
            for r in conn.execute(
                "SELECT id FROM tasks WHERE status = 'done'"
            ).fetchall()
        }
    out = []
    for row in rows:
        task = _task_row(row)
        if all(dep in done_ids for dep in task["depends_on"]):
            out.append(task)
    return out


def stats() -> dict:
    """Aggregate counts for the dashboard KPIs: by status, and per project."""
    with _conn() as conn:
        total_projects = conn.execute(
            "SELECT COUNT(*) AS c FROM projects"
        ).fetchone()["c"]
        total_tasks = conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"]
        by_status = {
            r["status"]: r["c"]
            for r in conn.execute(
                "SELECT status, COUNT(*) AS c FROM tasks GROUP BY status"
            ).fetchall()
        }
        per_project = {
            r["project_id"]: r["c"]
            for r in conn.execute(
                "SELECT project_id, COUNT(*) AS c FROM tasks GROUP BY project_id"
            ).fetchall()
        }
    return {
        "total_projects": total_projects,
        "total_tasks": total_tasks,
        "by_status": by_status,
        "per_project": per_project,
    }


# --------------------------------------------------------------------------- #
# Inbox — human-in-the-loop questions from agents
# --------------------------------------------------------------------------- #
import uuid as _uuid  # noqa: E402


def create_inbox_item(
    prompt: str,
    *,
    kind: str = "question",
    from_agent: str | None = None,
    run_id: str | None = None,
    task_id: str | None = None,
    sprint_id: str | None = None,
    options: list | None = None,
) -> str:
    item_id = str(_uuid.uuid4())
    with _conn() as conn:
        conn.execute(
            """INSERT INTO inbox (id, created_at, from_agent, run_id, task_id,
               sprint_id, kind, prompt, options, status)
               VALUES (?,?,?,?,?,?,?,?,?,'open')""",
            (
                item_id, _now(), from_agent, run_id, task_id, sprint_id,
                kind, prompt, json.dumps(options) if options else None,
            ),
        )
    return item_id


def _inbox_row(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["options"] = json.loads(d["options"]) if d.get("options") else None
    return d


def get_inbox_item(item_id: str) -> dict | None:
    """Look up an inbox item by full id, or by a unique id prefix (the CLI and
    dashboard display truncated 8-char ids). Ambiguous prefixes return None."""
    with _conn() as conn:
        row = conn.execute("SELECT * FROM inbox WHERE id = ?", (item_id,)).fetchone()
        if not row and len(item_id) >= 4:
            rows = conn.execute(
                "SELECT * FROM inbox WHERE id LIKE ? || '%' LIMIT 2", (item_id,)
            ).fetchall()
            if len(rows) == 1:
                row = rows[0]
        return _inbox_row(row) if row else None


def list_inbox(status: str | None = "open") -> list[dict]:
    with _conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM inbox WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM inbox ORDER BY created_at DESC"
            ).fetchall()
        return [_inbox_row(r) for r in rows]


def answer_inbox(item_id: str, answer: str, answered_by: str = "human") -> None:
    with _conn() as conn:
        conn.execute(
            """UPDATE inbox SET status='answered', answer=?, answered_at=?,
               answered_by=? WHERE id=?""",
            (answer, _now(), answered_by, item_id),
        )


def dismiss_inbox(item_id: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE inbox SET status='dismissed' WHERE id=?", (item_id,))


def open_inbox_for_task(task_id: str) -> list[dict]:
    """Open inbox items blocking a given task (used to resume it once answered)."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM inbox WHERE task_id = ? AND status = 'open'", (task_id,)
        ).fetchall()
        return [_inbox_row(r) for r in rows]
