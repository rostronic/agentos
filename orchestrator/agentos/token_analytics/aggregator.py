"""Roll up per-session token summaries into dashboard-ready aggregates."""

from __future__ import annotations

from agentos.token_analytics import jsonl_parser, tips_engine

# Plan view (after Nate Herk's Settings tab). The raw cost is the pay-per-token
# API equivalent; these describe what you ACTUALLY pay so the UI can frame it.
PLANS = {
    "api": {"label": "Pay-per-token API", "monthly": None},
    "pro": {"label": "Claude Pro", "monthly": 20},
    "max": {"label": "Claude Max (5×)", "monthly": 100},
    "max20": {"label": "Claude Max (20×)", "monthly": 200},
}


def _blank_bucket() -> dict:
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
            "cost_usd": 0.0, "sessions": 0, "messages": 0}


def aggregate(sessions: list[dict] | None = None) -> dict:
    """Compute totals + breakdowns by project, model, and day."""
    if sessions is None:
        sessions = jsonl_parser.scan()

    totals = _blank_bucket()
    by_project: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    by_day: dict[str, dict] = {}
    by_tool: dict[str, int] = {}

    for s in sessions:
        totals["input"] += s["input"]
        totals["output"] += s["output"]
        totals["cache_read"] += s["cache_read"]
        totals["cache_write"] += s["cache_write"]
        totals["cost_usd"] += s["cost_usd"]
        totals["sessions"] += 1
        totals["messages"] += s["messages"]

        proj = by_project.setdefault(s["project"], _blank_bucket())
        for k in ("input", "output", "cache_read", "cache_write", "cost_usd", "messages"):
            proj[k] += s[k]
        proj["sessions"] += 1

        for model, m in s.get("models", {}).items():
            mb = by_model.setdefault(model, _blank_bucket())
            mb["input"] += m["input"]
            mb["output"] += m["output"]
            mb["cache_read"] += m["cache_read"]
            mb["cache_write"] += m["cache_write"]
            mb["cost_usd"] += m["cost"]

        for day, dd in s.get("by_day", {}).items():
            db = by_day.setdefault(day, {"tokens": 0, "cost_usd": 0.0})
            db["tokens"] += dd["tokens"]
            db["cost_usd"] += dd["cost"]

        for tool, n in s.get("tools", {}).items():
            by_tool[tool] = by_tool.get(tool, 0) + n

    def _top(d: dict, key: str, n: int = 12) -> list[dict]:
        rows = [{"name": k, **v} for k, v in d.items()]
        rows.sort(key=lambda r: r.get(key, 0), reverse=True)
        return rows[:n]

    cache_total = totals["cache_read"] + totals["cache_write"]
    total_in = totals["input"] + cache_total
    cache_pct = round(totals["cache_read"] / total_in * 100, 1) if total_in else 0.0

    totals_out = {
        **totals,
        "total_tokens": totals["input"] + totals["output"] + cache_total,
        "cache_hit_pct": cache_pct,
        "turns": totals["messages"],  # Nate's "turns" = deduped assistant messages
    }
    top_tools = sorted(
        [{"name": k, "calls": v} for k, v in by_tool.items()],
        key=lambda r: r["calls"], reverse=True,
    )[:12]

    # Expensive prompts/turns across all sessions (Nate's Prompts tab).
    all_turns = []
    for s in sessions:
        for tr in s.get("top_turns", []):
            all_turns.append({**tr, "project": s["project"], "session_id": s["session_id"]})
    all_turns.sort(key=lambda r: r["tokens"], reverse=True)
    expensive_prompts = all_turns[:30]

    return {
        "totals": totals_out,
        "by_project": _top(by_project, "cost_usd"),
        "by_model": _top(by_model, "cost_usd"),
        "by_day": [{"day": k, **v} for k, v in sorted(by_day.items())],
        "top_tools": top_tools,
        "expensive_prompts": expensive_prompts,
        "tips": tips_engine.generate_tips(sessions, totals_out),
        "plans": PLANS,
        "recent_sessions": sorted(
            [
                {"session_id": s["session_id"], "project": s["project"],
                 "tokens": s["input"] + s["output"], "cost_usd": s["cost_usd"],
                 "messages": s["messages"], "last_ts": s["last_ts"]}
                for s in sessions
            ],
            key=lambda r: r.get("last_ts") or "", reverse=True,
        )[:20],
    }
