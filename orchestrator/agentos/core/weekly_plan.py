"""Weekly plan — deterministic helpers for the chief-of-staff weekly planner.

The planning pass (an LLM full session per workspaces/personal/chief-of-staff/run_prompt.md)
writes `plans/<week>.md` + `proposals/<week>.calendar.json`. THIS module does the deterministic,
approval-gated calendar apply: read the proposals JSON, create a `gog` calendar event for each
block the user marked `status: "approved"`, and flip it to `"created"`. Idempotent — it skips
`"created"` blocks and writes the JSON back after each successful create, so a re-run never
double-books. It NEVER touches `"proposed"` blocks (approval gate).
"""

from __future__ import annotations

import datetime
import json
import shlex
import subprocess
from pathlib import Path

from agentos.core import config


def current_week(date_str: str | None = None) -> str:
    d = datetime.date.fromisoformat(date_str) if date_str else datetime.date.today()
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _proposals_path(week: str) -> Path:
    return config.AGENTOS_ROOT / config.cos_dir() / "proposals" / f"{week}.calendar.json"


def _plan_path(week: str) -> Path:
    return config.AGENTOS_ROOT / config.cos_dir() / "plans" / f"{week}.md"


def load_proposals(week: str) -> list[dict]:
    p = _proposals_path(week)
    if not p.exists():
        raise FileNotFoundError(f"No proposals for {week}: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def _write_proposals(week: str, events: list[dict]) -> None:
    _proposals_path(week).write_text(json.dumps(events, indent=2) + "\n", encoding="utf-8")


def status_summary(week: str) -> dict:
    events = load_proposals(week)
    by: dict[str, int] = {}
    for e in events:
        st = e.get("status", "proposed")
        by[st] = by.get(st, 0) + 1
    return {
        "week": week,
        "total": len(events),
        "by_status": by,
        "proposals_path": str(_proposals_path(week)),
        "plan_path": str(_plan_path(week)),
    }


def _create_cmd(e: dict, *, dry_run: bool) -> list[str]:
    cal = e.get("calendar_id") or "primary"
    desc = (
        f"Focus: {e.get('focus_area', '')} · AgentOS weekly plan "
        f"· proposal-id {e.get('proposal_id', '')}"
    )
    cmd = [
        "gog", "-a", config.user_email(), "calendar", "create", cal,
        "--summary", e.get("summary", "(block)"),
        "--from", e["start"], "--to", e["end"],
        "--start-timezone", config.user_timezone(),
        "--description", desc,
        "--json", "--no-input",
    ]
    color = str(e.get("color", ""))
    if color.isdigit() and 1 <= int(color) <= 11:
        cmd += ["--event-color", color]
    if dry_run:
        cmd.append("--dry-run")
    return cmd


def _parse_event_id(stdout: str) -> str | None:
    try:
        data = json.loads(stdout)
    except Exception:  # noqa: BLE001
        return None
    if isinstance(data, dict):
        return data.get("id") or (data.get("result") or {}).get("id")
    return None


def apply(week: str, *, dry_run: bool = True) -> dict:
    """Create calendar events for `approved` blocks. Idempotent + approval-gated.

    dry_run=True (default): build and return the intended `gog` commands, create nothing.
    """
    events = load_proposals(week)
    created: list[str] = []
    skipped: list[str] = []
    failed: list[tuple[str, str]] = []
    planned: list[str] = []

    for e in events:
        st = e.get("status", "proposed")
        if st == "created":
            skipped.append(e.get("proposal_id", "?"))
            continue
        if st != "approved":
            continue  # leave "proposed" untouched — approval gate
        cmd = _create_cmd(e, dry_run=dry_run)
        if dry_run:
            planned.append(" ".join(shlex.quote(c) for c in cmd))
            continue
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except Exception as ex:  # noqa: BLE001
            failed.append((e.get("proposal_id", "?"), str(ex)))
            continue
        if proc.returncode != 0:
            failed.append((e.get("proposal_id", "?"), (proc.stderr or proc.stdout or "error").strip()[:200]))
            continue
        eid = _parse_event_id(proc.stdout)
        e["status"] = "created"
        if eid:
            e["event_id"] = eid
        _write_proposals(week, events)  # write back per-event so a re-run never double-books
        created.append(e.get("proposal_id", "?"))

    return {
        "week": week,
        "dry_run": dry_run,
        "created": created,
        "skipped_already_created": skipped,
        "failed": failed,
        "planned": planned,
    }
