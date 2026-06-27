"""Budget enforcement — checked before every dispatch.

Tracks daily spend in a small JSON file. Per-run caps are checked against
limits; daily caps against accumulated spend. A dispatch that would exceed
either is blocked (returns a BudgetBlock) rather than silently proceeding.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from agentos.core.config import AGENTOS_ROOT, budget_for_project

RUNTIME_DIR = AGENTOS_ROOT / "orchestrator" / "runtime"
SPEND_FILE = RUNTIME_DIR / "daily_spend.json"


@dataclass
class BudgetBlock:
    """Returned when a dispatch is blocked. Falsy is allowed; check .blocked."""

    blocked: bool
    reason: str = ""
    detail: str = ""

    def __bool__(self) -> bool:
        return self.blocked


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_spend() -> dict:
    if not SPEND_FILE.exists():
        return {}
    try:
        return json.loads(SPEND_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_spend(data: dict) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    SPEND_FILE.write_text(json.dumps(data, indent=2))


def today_spend() -> float:
    """Total USD spent today across all projects."""
    data = _load_spend()
    return float(data.get(_today(), {}).get("_total", 0.0))


def check_dispatch(project: str | None = None, estimated_usd: float = 0.0) -> BudgetBlock:
    """Check whether a dispatch is allowed under daily + per-run budgets.

    Call BEFORE dispatching. estimated_usd is a pre-flight guess (often 0 since
    we don't know cost until after); the daily cap is the real guard here.
    """
    b = budget_for_project(project)
    daily_cap = b.get("daily_usd")
    per_run_cap = b.get("per_run_usd")

    spent = today_spend()
    if daily_cap is not None and spent >= daily_cap:
        return BudgetBlock(
            blocked=True,
            reason="budget_exceeded",
            detail=f"Daily cap ${daily_cap:.2f} reached (spent ${spent:.2f})",
        )
    if per_run_cap is not None and estimated_usd > per_run_cap:
        return BudgetBlock(
            blocked=True,
            reason="per_run_exceeded",
            detail=f"Estimated ${estimated_usd:.2f} exceeds per-run cap ${per_run_cap:.2f}",
        )
    return BudgetBlock(blocked=False)


def record_spend(cost_usd: float, project: str | None = None) -> None:
    """Record actual spend after a dispatch completes."""
    data = _load_spend()
    day = _today()
    bucket = data.setdefault(day, {"_total": 0.0})
    bucket["_total"] = round(bucket.get("_total", 0.0) + cost_usd, 6)
    key = project or "_unassigned"
    bucket[key] = round(bucket.get(key, 0.0) + cost_usd, 6)
    _save_spend(data)


def threshold_crossed(project: str | None = None) -> int | None:
    """Return 80 or 100 if today's spend just crossed that % of the daily cap."""
    b = budget_for_project(project)
    daily_cap = b.get("daily_usd")
    if not daily_cap:
        return None
    pct = today_spend() / daily_cap * 100
    if pct >= 100:
        return 100
    if pct >= 80:
        return 80
    return None


def reset_today() -> None:
    """Clear today's spend counter (agentos budget --reset)."""
    data = _load_spend()
    data.pop(_today(), None)
    _save_spend(data)
