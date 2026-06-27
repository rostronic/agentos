"""Git-backed file implementation of the TaskStore (the Work layer).

Source of truth is a **tracked** `work/` tree of Markdown + YAML-frontmatter docs
(one file per entity), mirroring how the memory tier persists tracked markdown so
tasks travel with the repo. This module is a drop-in for `local_store.py`: it
exposes the exact same module-level functions (TaskStore Protocol + inbox) with
identical signatures and return shapes, but reads/writes files instead of sqlite.

Layout (under WORK_DIR):
    work/projects/<id>.md
    work/sprints/<id>.md
    work/tasks/<id>.md       (frontmatter + body w/ append-only status history)
    work/inbox/<id>.md

Like `local_store`, this module *is* the file TaskStore — the module-level
functions collectively satisfy the `TaskStore` Protocol in task_store.py.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from agentos.core import config
from agentos.storage.task_store import Project, Sprint, Task

# Overridable root, read at call time so tests can monkeypatch it.
WORK_DIR = config.AGENTOS_ROOT / "work"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
def _projects_dir() -> Path:
    return WORK_DIR / "projects"


def _sprints_dir() -> Path:
    return WORK_DIR / "sprints"


def _tasks_dir() -> Path:
    return WORK_DIR / "tasks"


def _inbox_dir() -> Path:
    return WORK_DIR / "inbox"


def _ensure_dirs() -> None:
    for d in (_projects_dir(), _sprints_dir(), _tasks_dir(), _inbox_dir()):
        d.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# Doc (frontmatter + body) serialization
# --------------------------------------------------------------------------- #
def _write_doc(path: Path, frontmatter: dict, body: str) -> None:
    """Write a `---`-fenced YAML frontmatter doc with a markdown body."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = yaml.safe_dump(frontmatter, sort_keys=False, default_flow_style=False, allow_unicode=True)
    text = f"---\n{fm}---\n\n{body.rstrip()}\n" if body.strip() else f"---\n{fm}---\n"
    path.write_text(text, encoding="utf-8")


def _read_doc(path: Path) -> tuple[dict, str]:
    """Parse a frontmatter doc into (frontmatter_dict, body_str)."""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            fm_text = text[4:end]
            body = text[end + 4:]
            if body.startswith("\n"):
                body = body[1:]
            fm = yaml.safe_load(fm_text) or {}
            return fm, body.lstrip("\n")
    return {}, text


def _sort_key_desc(d: dict):
    """Sort newest-first: created_at desc then id desc (total, deterministic)."""
    return (d.get("created_at") or "", d.get("id") or "")


# --------------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------------- #
def _project_doc(p: dict) -> tuple[dict, str]:
    fm = {
        "id": p["id"],
        "slug": p.get("slug") or "",
        "name": p.get("name") or "",
        "repo_path": p.get("repo_path"),
        "description": p.get("description"),
        "status": p.get("status") or "active",
        "lead_agent": p.get("lead_agent"),
        "created_at": p["created_at"],
    }
    body = f"# {fm['name']}\n"
    if p.get("description"):
        body += f"\n{p['description']}\n"
    return fm, body


def create_project(project: Project) -> str:
    _ensure_dirs()
    fm, body = _project_doc(
        {
            "id": project.id,
            "slug": project.slug,
            "name": project.name,
            "repo_path": project.repo_path,
            "description": project.description,
            "status": project.status,
            "lead_agent": project.lead_agent,
            "created_at": project.created_at,
        }
    )
    _write_doc(_projects_dir() / f"{project.id}.md", fm, body)
    return project.id


def get_project(project_id: str) -> dict | None:
    path = _projects_dir() / f"{project_id}.md"
    if not path.exists():
        return None
    fm, _ = _read_doc(path)
    return fm


def list_projects() -> list[dict]:
    d = _projects_dir()
    if not d.is_dir():
        return []
    out = [_read_doc(p)[0] for p in d.glob("*.md")]
    out.sort(key=_sort_key_desc, reverse=True)
    return out


# --------------------------------------------------------------------------- #
# Sprints
# --------------------------------------------------------------------------- #
def create_sprint(sprint: Sprint) -> str:
    _ensure_dirs()
    fm = {
        "id": sprint.id,
        "project_id": sprint.project_id,
        "name": sprint.name,
        "goal": sprint.goal,
        "status": sprint.status,
        "starts_at": sprint.starts_at,
        "ends_at": sprint.ends_at,
        "created_at": sprint.created_at,
    }
    body = f"# {sprint.name}\n"
    if sprint.goal:
        body += f"\n{sprint.goal}\n"
    _write_doc(_sprints_dir() / f"{sprint.id}.md", fm, body)
    return sprint.id


def list_sprints(project_id: str) -> list[dict]:
    d = _sprints_dir()
    if not d.is_dir():
        return []
    out = [_read_doc(p)[0] for p in d.glob("*.md")]
    out = [s for s in out if s.get("project_id") == project_id]
    out.sort(key=_sort_key_desc, reverse=True)
    return out


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #
_DESC_HEADING = "## Status history"


def _parse_task_body(body: str) -> tuple[str, list[str]]:
    """Return (description_paragraph, status_history_lines).

    The body is: optional H1 title, optional description paragraph(s), then a
    `## Status history` section of `- ...` lines.
    """
    lines = body.splitlines()
    # drop leading H1 title line(s)
    idx = 0
    while idx < len(lines) and (not lines[idx].strip() or lines[idx].startswith("# ")):
        if lines[idx].startswith("# "):
            idx += 1
            break
        idx += 1
    desc_lines: list[str] = []
    history: list[str] = []
    in_history = False
    for line in lines[idx:]:
        if line.strip() == _DESC_HEADING:
            in_history = True
            continue
        if in_history:
            if line.startswith("- "):
                history.append(line[2:].strip())
            elif line.strip():
                history.append(line.strip())
        else:
            desc_lines.append(line)
    description = "\n".join(desc_lines).strip()
    return description, history


def _compose_description(description: str, history: list[str]) -> str:
    """Reconstruct the dict `description` field: body description plus any
    status-history lines that carried a reason (back-compat with sqlite, whose
    update_task_status appended `[status→X] reason` to description).

    A history line looks like "<ts> <old> → <new>: <reason>"; keep the reasons.
    """
    reason_lines = []
    for h in history:
        if "→" in h:
            after = h.split("→", 1)[1]
            if ":" in after:
                reason_lines.append(h.split(":", 1)[1].strip())
    out = description
    if reason_lines:
        out = (out + "\n" if out else "") + "\n".join(reason_lines)
    return out


def _write_task(fm: dict, description: str, history: list[str]) -> None:
    title = fm.get("title") or ""
    body = f"# {title}\n"
    if description:
        body += f"\n{description.strip()}\n"
    body += f"\n{_DESC_HEADING}\n"
    for line in history:
        body += f"- {line}\n"
    _write_doc(_tasks_dir() / f"{fm['id']}.md", fm, body)


def _load_task(path: Path) -> dict:
    fm, body = _read_doc(path)
    description, history = _parse_task_body(body)
    fm = dict(fm)
    if not isinstance(fm.get("depends_on"), list):
        fm["depends_on"] = list(fm.get("depends_on") or [])
    fm["description"] = _compose_description(description, history)
    return fm


def _task_fm(task: Task) -> dict:
    return {
        "id": task.id,
        "project_id": task.project_id,
        "sprint_id": task.sprint_id,
        "status": task.status,
        "assignee": task.assignee,
        "priority": task.priority,
        "depends_on": list(task.depends_on or []),
        "acceptance_criteria": task.acceptance_criteria,
        "estimate_minutes": task.estimate_minutes,
        "parent_task_id": task.parent_task_id,
        "created_by": task.created_by,
        "last_run_id": task.last_run_id,
        "created_at": task.created_at,
        "title": task.title,
    }


def create_task(task: Task) -> str:
    _ensure_dirs()
    fm = _task_fm(task)
    _write_task(fm, task.description or "", [])
    return task.id


def get_task(task_id: str) -> dict | None:
    path = _tasks_dir() / f"{task_id}.md"
    if not path.exists():
        return None
    return _load_task(path)


def list_tasks(
    project_id: str | None = None,
    sprint_id: str | None = None,
    status: str | None = None,
) -> list[dict]:
    d = _tasks_dir()
    if not d.is_dir():
        return []
    out = [_load_task(p) for p in d.glob("*.md")]
    if project_id:
        out = [t for t in out if t.get("project_id") == project_id]
    if sprint_id:
        out = [t for t in out if t.get("sprint_id") == sprint_id]
    if status:
        out = [t for t in out if t.get("status") == status]
    out.sort(key=_sort_key_desc, reverse=True)
    return out


def update_task_status(task_id: str, status: str, reason: str | None = None) -> None:
    path = _tasks_dir() / f"{task_id}.md"
    if not path.exists():
        return
    fm, body = _read_doc(path)
    description, history = _parse_task_body(body)
    old = fm.get("status")
    fm["status"] = status
    entry = f"{_now()} {old} → {status}"
    if reason:
        entry += f": {reason}"
    history.append(entry)
    _write_task(fm, description, history)


def update_task(task_id: str, **fields: Any) -> None:
    if not fields:
        return
    path = _tasks_dir() / f"{task_id}.md"
    if not path.exists():
        return
    fm, body = _read_doc(path)
    description, history = _parse_task_body(body)
    if "depends_on" in fields and not isinstance(fields["depends_on"], list):
        fields["depends_on"] = list(fields["depends_on"] or [])
    if "description" in fields:
        description = fields.pop("description") or ""
    fm.update(fields)
    _write_task(fm, description, history)


def link_run(task_id: str, run_id: str) -> None:
    update_task(task_id, last_run_id=run_id)


def ready_tasks(sprint_id: str) -> list[dict]:
    """Tasks in a sprint with status='ready' whose deps are all 'done'."""
    all_tasks = list_tasks()
    done_ids = {t["id"] for t in all_tasks if t.get("status") == "done"}
    out = []
    for task in all_tasks:
        if task.get("sprint_id") != sprint_id or task.get("status") != "ready":
            continue
        if all(dep in done_ids for dep in task.get("depends_on", [])):
            out.append(task)
    out.sort(key=lambda d: (d.get("created_at") or "", d.get("id") or ""))
    return out


def stats() -> dict:
    """Aggregate counts for the dashboard KPIs: by status, and per project."""
    projects = list_projects()
    tasks = list_tasks()
    by_status: dict[str, int] = {}
    per_project: dict[str, int] = {}
    for t in tasks:
        by_status[t.get("status")] = by_status.get(t.get("status"), 0) + 1
        pid = t.get("project_id")
        per_project[pid] = per_project.get(pid, 0) + 1
    return {
        "total_projects": len(projects),
        "total_tasks": len(tasks),
        "by_status": by_status,
        "per_project": per_project,
    }


# --------------------------------------------------------------------------- #
# Inbox — human-in-the-loop questions from agents
# --------------------------------------------------------------------------- #
def _load_inbox(path: Path) -> dict:
    fm, body = _read_doc(path)
    fm = dict(fm)
    if fm.get("options") is not None and not isinstance(fm["options"], list):
        fm["options"] = list(fm["options"])
    fm["prompt"] = body.strip()
    return fm


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
    _ensure_dirs()
    item_id = str(_uuid.uuid4())
    fm = {
        "id": item_id,
        "created_at": _now(),
        "from_agent": from_agent,
        "run_id": run_id,
        "task_id": task_id,
        "sprint_id": sprint_id,
        "kind": kind,
        "status": "open",
        "options": list(options) if options else None,
        "answer": None,
        "answered_at": None,
        "answered_by": None,
    }
    _write_doc(_inbox_dir() / f"{item_id}.md", fm, prompt)
    return item_id


def get_inbox_item(item_id: str) -> dict | None:
    """Look up an inbox item by full id, or by a unique id prefix (the CLI and
    dashboard display truncated 8-char ids). Ambiguous prefixes return None —
    mirrors local_store.get_inbox_item so short-id answering works (bug #3)."""
    path = _inbox_dir() / f"{item_id}.md"
    if path.exists():
        return _load_inbox(path)
    if len(item_id) >= 4:
        matches = sorted(_inbox_dir().glob(f"{item_id}*.md"))
        if len(matches) == 1:
            return _load_inbox(matches[0])
    return None


def list_inbox(status: str | None = "open") -> list[dict]:
    d = _inbox_dir()
    if not d.is_dir():
        return []
    out = [_load_inbox(p) for p in d.glob("*.md")]
    if status:
        out = [i for i in out if i.get("status") == status]
    out.sort(key=_sort_key_desc, reverse=True)
    return out


def answer_inbox(item_id: str, answer: str, answered_by: str = "human") -> None:
    path = _inbox_dir() / f"{item_id}.md"
    if not path.exists():
        return
    fm, body = _read_doc(path)
    fm["status"] = "answered"
    fm["answer"] = answer
    fm["answered_at"] = _now()
    fm["answered_by"] = answered_by
    _write_doc(path, fm, body)


def dismiss_inbox(item_id: str) -> None:
    path = _inbox_dir() / f"{item_id}.md"
    if not path.exists():
        return
    fm, body = _read_doc(path)
    fm["status"] = "dismissed"
    _write_doc(path, fm, body)


def open_inbox_for_task(task_id: str) -> list[dict]:
    """Open inbox items blocking a given task (used to resume it once answered)."""
    return [
        i
        for i in list_inbox(status="open")
        if i.get("task_id") == task_id
    ]


# --------------------------------------------------------------------------- #
# Migration: work.sqlite → work/ files
# --------------------------------------------------------------------------- #
def migrate_from_sqlite(db_path: Path | str | None = None) -> dict:
    """Idempotent one-time importer from the legacy sqlite Work store.

    No-op when the sqlite db is absent. Re-running skips entities whose files
    already exist. Returns counts.
    """
    import json
    import sqlite3

    from agentos.storage import local_store

    db_path = Path(db_path) if db_path is not None else local_store.DB_PATH
    if not Path(db_path).exists():
        return {"migrated": 0, "reason": "no work.sqlite"}

    _ensure_dirs()
    counts = {
        "migrated_projects": 0,
        "migrated_sprints": 0,
        "migrated_tasks": 0,
        "migrated_inbox": 0,
        "skipped": 0,
    }

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        for row in conn.execute("SELECT * FROM projects").fetchall():
            p = dict(row)
            path = _projects_dir() / f"{p['id']}.md"
            if path.exists():
                counts["skipped"] += 1
                continue
            fm, body = _project_doc(p)
            _write_doc(path, fm, body)
            counts["migrated_projects"] += 1

        for row in conn.execute("SELECT * FROM sprints").fetchall():
            s = dict(row)
            path = _sprints_dir() / f"{s['id']}.md"
            if path.exists():
                counts["skipped"] += 1
                continue
            fm = {
                "id": s["id"],
                "project_id": s["project_id"],
                "name": s["name"],
                "goal": s.get("goal"),
                "status": s.get("status") or "planned",
                "starts_at": s.get("starts_at"),
                "ends_at": s.get("ends_at"),
                "created_at": s["created_at"],
            }
            body = f"# {s['name']}\n"
            if s.get("goal"):
                body += f"\n{s['goal']}\n"
            _write_doc(path, fm, body)
            counts["migrated_sprints"] += 1

        for row in conn.execute("SELECT * FROM tasks").fetchall():
            t = dict(row)
            path = _tasks_dir() / f"{t['id']}.md"
            if path.exists():
                counts["skipped"] += 1
                continue
            fm = {
                "id": t["id"],
                "project_id": t["project_id"],
                "sprint_id": t.get("sprint_id"),
                "status": t.get("status") or "backlog",
                "assignee": t.get("assignee"),
                "priority": t.get("priority") or "medium",
                "depends_on": json.loads(t.get("depends_on") or "[]"),
                "acceptance_criteria": t.get("acceptance_criteria"),
                "estimate_minutes": t.get("estimate_minutes"),
                "parent_task_id": t.get("parent_task_id"),
                "created_by": t.get("created_by") or "human",
                "last_run_id": t.get("last_run_id"),
                "created_at": t["created_at"],
                "title": t.get("title") or "",
            }
            _write_task(fm, t.get("description") or "", [f"{_now()} migrated"])
            counts["migrated_tasks"] += 1

        for row in conn.execute("SELECT * FROM inbox").fetchall():
            i = dict(row)
            path = _inbox_dir() / f"{i['id']}.md"
            if path.exists():
                counts["skipped"] += 1
                continue
            fm = {
                "id": i["id"],
                "created_at": i["created_at"],
                "from_agent": i.get("from_agent"),
                "run_id": i.get("run_id"),
                "task_id": i.get("task_id"),
                "sprint_id": i.get("sprint_id"),
                "kind": i.get("kind") or "question",
                "status": i.get("status") or "open",
                "options": json.loads(i["options"]) if i.get("options") else None,
                "answer": i.get("answer"),
                "answered_at": i.get("answered_at"),
                "answered_by": i.get("answered_by"),
            }
            _write_doc(path, fm, i.get("prompt") or "")
            counts["migrated_inbox"] += 1
    finally:
        conn.close()

    return counts
