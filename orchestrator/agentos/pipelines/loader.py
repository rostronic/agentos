"""Read-only health view of the legacy content cron jobs.

Two LOCAL sources, joined by job id:
  cron/jobs.json        — definition: name, schedule, description, enabled
  cron/jobs-state.json  — health (keyed by job uuid): last run status, error,
                          run/next timestamps, consecutive errors

Observes only — never writes, never touches GCP or the network. The "project"
is derived from the job-name prefix (e.g. shop_* → a configured label); unknown
prefixes fall back to a title-cased label so every managed project shows.
Epoch-ms fields are converted to ISO strings. Missing or unparseable files
degrade gracefully (empty list / "none" status).

The legacy cron directory defaults to ~/.agentos-legacy/cron and is overridable
via AGENTOS_CRON_DIR (point it at the prior harness's cron dir per instance).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

CRON_DIR = Path(
    os.environ.get("AGENTOS_CRON_DIR", str(Path.home() / ".agentos-legacy" / "cron"))
)
JOBS_FILE = CRON_DIR / "jobs.json"
STATE_FILE = CRON_DIR / "jobs-state.json"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


# Known job-name prefixes → readable project label. AgentOS manages a growing
# set of projects; add prefixes here as they come online. Unknown prefixes fall
# back to a title-cased version so nothing is hidden.
_PROJECT_LABELS = {
    "shop": "ExampleShop",
    "news": "ExampleNews",
    "gary": "Gary",
    "job": "General",
}


def _project_from_name(name: str) -> str:
    prefix = name.split("_", 1)[0] if "_" in name else name
    if not prefix:
        return "Unknown"
    return _PROJECT_LABELS.get(prefix, prefix.replace("-", " ").title())


def _epoch_ms_to_iso(ms) -> str | None:
    if not ms:
        return None
    try:
        return (
            datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
            .isoformat(timespec="seconds")
        )
    except (TypeError, ValueError, OSError):
        return None


def load_jobs() -> list[dict]:
    """Return one record per job, joining definition + health by id.

    Best-effort: a job present in jobs.json but absent from jobs-state.json
    gets last_run_status="none". Bad/missing files yield [].
    """
    defs = _read_json(JOBS_FILE)
    state = _read_json(STATE_FILE)
    job_defs = defs.get("jobs") if isinstance(defs, dict) else None
    if not isinstance(job_defs, list):
        return []
    state_jobs = state.get("jobs") if isinstance(state, dict) else {}
    if not isinstance(state_jobs, dict):
        state_jobs = {}

    out = []
    for job in job_defs:
        if not isinstance(job, dict):
            continue
        jid = job.get("id")
        name = job.get("name") or ""
        schedule = job.get("schedule") or {}
        st = (state_jobs.get(jid) or {}).get("state") or {}

        if st:
            last_status = st.get("lastRunStatus") or st.get("lastStatus") or "none"
        else:
            last_status = "none"

        out.append({
            "id": jid,
            "name": name,
            "project": _project_from_name(name),
            "schedule": schedule.get("expr"),
            "tz": schedule.get("tz"),
            "description": job.get("description") or "",
            "enabled": bool(job.get("enabled")),
            "last_run_status": last_status,
            "last_error": st.get("lastError"),
            "last_run_at": _epoch_ms_to_iso(st.get("lastRunAtMs")),
            "next_run_at": _epoch_ms_to_iso(st.get("nextRunAtMs")),
            "consecutive_errors": st.get("consecutiveErrors", 0) or 0,
        })
    return out


def summary(jobs: list[dict] | None = None) -> dict:
    """Counts for the dashboard KPI strip."""
    if jobs is None:
        jobs = load_jobs()
    by_project: dict[str, int] = {}
    enabled = 0
    erroring = 0
    ok = 0
    for j in jobs:
        by_project[j["project"]] = by_project.get(j["project"], 0) + 1
        if j["enabled"]:
            enabled += 1
        is_error = j["consecutive_errors"] > 0 or j["last_run_status"] == "error"
        if is_error:
            erroring += 1
        elif j["last_run_status"] == "ok":
            ok += 1
    return {
        "total": len(jobs),
        "by_project": by_project,
        "enabled": enabled,
        "erroring": erroring,
        "ok": ok,
    }
