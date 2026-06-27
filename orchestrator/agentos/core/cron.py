"""Minimal cron scheduler for AgentOS workflows.

Reads config/schedules.yaml and fires due workflows. Run `agentos cron` once a
minute from system cron (or a loop), and it dispatches any schedule whose cron
expression matches the current minute.

Supports standard 5-field cron: minute hour day-of-month month day-of-week,
with `*`, lists (1,3,5), ranges (1-5), steps (*/15), and weekday names (MON-SUN).
No third-party dependency.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml

from agentos.core.config import AGENTOS_ROOT

SCHEDULES_FILE = AGENTOS_ROOT / "config" / "schedules.yaml"

_DOW = {"SUN": 0, "MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5, "SAT": 6}


def _parse_field(field: str, lo: int, hi: int) -> set[int]:
    """Expand one cron field into the set of matching integers."""
    field = field.strip().upper()
    for name, num in _DOW.items():
        field = field.replace(name, str(num))
    values: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = int(step_s)
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(part)
        values.update(range(start, end + 1, step))
    return values


def cron_matches(expr: str, when: datetime) -> bool:
    """True if the 5-field cron expression matches the given datetime (minute)."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"cron expr must have 5 fields, got {len(fields)}: {expr!r}")
    minute, hour, dom, month, dow = fields
    # Python weekday(): Mon=0..Sun=6; cron dow: Sun=0..Sat=6. Convert.
    cron_dow = (when.weekday() + 1) % 7
    return (
        when.minute in _parse_field(minute, 0, 59)
        and when.hour in _parse_field(hour, 0, 23)
        and when.day in _parse_field(dom, 1, 31)
        and when.month in _parse_field(month, 1, 12)
        and cron_dow in _parse_field(dow, 0, 6)
    )


def load_schedules(path: Path | None = None) -> list[dict]:
    path = path or SCHEDULES_FILE
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text()) or {}
    return [s for s in (data.get("schedules") or []) if s]


def due_schedules(when: datetime, schedules: list[dict] | None = None) -> list[dict]:
    """Enabled schedules whose cron matches `when`."""
    schedules = schedules if schedules is not None else load_schedules()
    due = []
    for s in schedules:
        if not s.get("enabled", False):
            continue
        try:
            if cron_matches(s["cron"], when):
                due.append(s)
        except (ValueError, KeyError):
            continue
    return due


def run_due(when: datetime | None = None, *, dry_run: bool = False) -> list[dict]:
    """Fire all due schedules. Returns a summary per fired schedule."""
    from agentos.core import workflow_runner
    when = when or datetime.now()
    results = []
    for s in due_schedules(when):
        entry = {"name": s.get("name"), "workflow": s.get("workflow"), "dry_run": dry_run}
        if dry_run:
            results.append(entry)
            continue
        res = workflow_runner.run_workflow(
            s["workflow"], s.get("inputs", {}), triggered_by="cron",
        )
        entry["ok"] = res.ok
        entry["run_id"] = res.run_id
        results.append(entry)
    return results
