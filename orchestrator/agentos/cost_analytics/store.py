"""Cost-record persistence — sqlite is the local source of truth for spend.

Mirrors core/run_store.py: a single sqlite file in the runtime dir, schema applied
on connect, module-level DB_PATH overridable in tests. Kept in a SEPARATE file from
runs.sqlite — different lifecycle, independently wipeable test fixtures.

Records are append-only and immutable. Re-ingesting a source REPLACES all records
for that (source, period-range) via replace_source(), so re-running a load is
idempotent (see docs/cost-tracking-plan.md "Design rule").
"""

from __future__ import annotations

import json
import sqlite3

from agentos.core.config import AGENTOS_ROOT

RUNTIME_DIR = AGENTOS_ROOT / "orchestrator" / "runtime"
DB_PATH = RUNTIME_DIR / "costs.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cost_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    source TEXT NOT NULL,
    service TEXT NOT NULL,
    period TEXT NOT NULL,              -- YYYY-MM-DD
    amount_usd REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    amount_native REAL,
    provenance TEXT NOT NULL,
    billing_account TEXT,
    native_id TEXT,
    labels TEXT,                       -- JSON string
    raw_ref TEXT
);

CREATE INDEX IF NOT EXISTS idx_cost_project ON cost_records(project);
CREATE INDEX IF NOT EXISTS idx_cost_source ON cost_records(source);
CREATE INDEX IF NOT EXISTS idx_cost_period ON cost_records(period);
"""

# Columns in insert order (id is auto-assigned).
_COLS = (
    "project", "source", "service", "period", "amount_usd", "currency",
    "amount_native", "provenance", "billing_account", "native_id", "labels",
    "raw_ref",
)


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _row_values(rec: dict) -> tuple:
    labels = rec.get("labels")
    if isinstance(labels, (dict, list)):
        labels = json.dumps(labels)
    return (
        rec["project"], rec["source"], rec["service"], rec["period"],
        float(rec["amount_usd"]), rec.get("currency", "USD"),
        rec.get("amount_native"), rec["provenance"], rec.get("billing_account"),
        rec.get("native_id"), labels, rec.get("raw_ref"),
    )


def insert_records(records: list[dict]) -> int:
    """Append CostRecords. Returns number inserted."""
    if not records:
        return 0
    placeholders = ",".join("?" for _ in _COLS)
    with _conn() as conn:
        conn.executemany(
            f"INSERT INTO cost_records ({','.join(_COLS)}) VALUES ({placeholders})",
            [_row_values(r) for r in records],
        )
    return len(records)


def replace_source(source: str, records: list[dict]) -> int:
    """Idempotently load one source: DELETE this source's rows within the batch's
    period range, then INSERT the new batch.

    The batch's period range is [min(period), max(period)] over `records`. Re-running
    the same load yields the same store contents (idempotent). If `records` is empty,
    nothing is deleted (no period range to scope the delete to).
    """
    if not records:
        return 0
    periods = [r["period"] for r in records]
    lo, hi = min(periods), max(periods)
    with _conn() as conn:
        conn.execute(
            "DELETE FROM cost_records WHERE source = ? AND period >= ? AND period <= ?",
            (source, lo, hi),
        )
        placeholders = ",".join("?" for _ in _COLS)
        conn.executemany(
            f"INSERT INTO cost_records ({','.join(_COLS)}) VALUES ({placeholders})",
            [_row_values(r) for r in records],
        )
    return len(records)


def all_records() -> list[dict]:
    """Every cost record as a list of plain dicts (labels parsed back to objects)."""
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM cost_records ORDER BY period ASC, id ASC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("labels"):
            try:
                d["labels"] = json.loads(d["labels"])
            except (json.JSONDecodeError, TypeError):
                pass
        out.append(d)
    return out


def count() -> int:
    with _conn() as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM cost_records").fetchone()["c"]


def clear() -> None:
    """Wipe all records (test/maintenance helper)."""
    with _conn() as conn:
        conn.execute("DELETE FROM cost_records")
