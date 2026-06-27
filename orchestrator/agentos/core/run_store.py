"""Run persistence — sqlite is the orchestrator's local source of truth.

Every dispatch and workflow run is recorded here. This is what lets the
orchestrator function headlessly (CLI/cron) without the dashboard. The Convex
pusher (Phase 4) mirrors these rows to the dashboard.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agentos.core.config import AGENTOS_ROOT

RUNTIME_DIR = AGENTOS_ROOT / "orchestrator" / "runtime"
DB_PATH = RUNTIME_DIR / "runs.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    workflow_name TEXT,
    agent TEXT,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    inputs TEXT,
    output TEXT,
    error TEXT,
    triggered_by TEXT,
    model TEXT,
    cost_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0,
    project TEXT
);

CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    step_id TEXT,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    payload TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_events_run ON run_events(run_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


@dataclass
class Run:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str | None = None
    workflow_name: str | None = None
    agent: str | None = None
    status: str = "queued"
    started_at: str = field(default_factory=_now)
    ended_at: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    output: str | None = None
    error: str | None = None
    triggered_by: str = "cli"
    model: str | None = None
    cost_tokens: int = 0
    cost_usd: float = 0.0
    project: str | None = None


def create_run(run: Run) -> str:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO runs (id, task_id, workflow_name, agent, status,
               started_at, ended_at, inputs, output, error, triggered_by,
               model, cost_tokens, cost_usd, project)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run.id, run.task_id, run.workflow_name, run.agent, run.status,
                run.started_at, run.ended_at, json.dumps(run.inputs), run.output,
                run.error, run.triggered_by, run.model, run.cost_tokens,
                run.cost_usd, run.project,
            ),
        )
    return run.id


def update_run(run_id: str, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _conn() as conn:
        conn.execute(f"UPDATE runs SET {cols} WHERE id = ?", (*fields.values(), run_id))


def append_event(run_id: str, event_type: str, payload: dict | None = None, step_id: str | None = None) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO run_events (run_id, step_id, ts, type, payload) VALUES (?,?,?,?,?)",
            (run_id, step_id, _now(), event_type, json.dumps(payload or {})),
        )


def get_run(run_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None


def list_runs(limit: int = 20, status: str | None = None) -> list[dict]:
    with _conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM runs WHERE status = ? ORDER BY started_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def get_events(run_id: str) -> list[dict]:
    """All events for a run, oldest first."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM run_events WHERE run_id = ? ORDER BY id ASC", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def events_since(after_id: int = 0, limit: int = 200) -> list[dict]:
    """Events with id > after_id, oldest first. Used by the SSE live stream."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM run_events WHERE id > ? ORDER BY id ASC LIMIT ?",
            (after_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def max_event_id() -> int:
    with _conn() as conn:
        row = conn.execute("SELECT MAX(id) AS m FROM run_events").fetchone()
        return row["m"] or 0


def stats() -> dict:
    """Aggregate counts for the dashboard home KPIs."""
    with _conn() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"]
        by_status = {
            r["status"]: r["c"]
            for r in conn.execute(
                "SELECT status, COUNT(*) AS c FROM runs GROUP BY status"
            ).fetchall()
        }
        cost = conn.execute("SELECT COALESCE(SUM(cost_usd),0) AS s FROM runs").fetchone()["s"]
        return {"total_runs": total, "by_status": by_status, "total_cost_usd": cost}
