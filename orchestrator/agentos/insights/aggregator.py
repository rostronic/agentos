"""Aggregate session insights into dashboard-ready breakdowns.

Answers the "where is my time going and was it worth it" questions that the
token page (cost) doesn't: outcome distribution, friction leaderboard, what's
working, project quality comparison, and notable sessions.
"""

from __future__ import annotations

from collections import Counter

from agentos.insights import loader

_OUTCOME_SCORE = {
    "fully_achieved": 1.0, "mostly_achieved": 0.75,
    "partially_achieved": 0.4, "failed": 0.0,
}


def aggregate(sessions: list[dict] | None = None) -> dict:
    if sessions is None:
        sessions = loader.load_sessions()

    scored = [s for s in sessions if s.get("outcome")]
    outcomes = Counter(s["outcome"] for s in scored)
    helpfulness = Counter(s["helpfulness"] for s in sessions if s.get("helpfulness"))
    session_types = Counter(s["session_type"] for s in sessions if s.get("session_type"))
    successes = Counter(s["primary_success"] for s in sessions if s.get("primary_success"))

    # Friction leaderboard
    friction = Counter()
    for s in sessions:
        for k, v in s.get("friction_counts", {}).items():
            friction[k] += v

    # Goal categories (what you ask for)
    goals = Counter()
    for s in sessions:
        for k, v in s.get("goal_categories", {}).items():
            goals[k] += v

    # Tool usage + tool errors
    tools = Counter()
    tool_errors = Counter()
    langs = Counter()
    for s in sessions:
        for k, v in s.get("tool_counts", {}).items():
            tools[k] += v
        for k, v in s.get("tool_error_categories", {}).items():
            tool_errors[k] += v
        for k, v in s.get("languages", {}).items():
            langs[k] += v

    # Per-project quality
    proj: dict[str, dict] = {}
    for s in sessions:
        p = proj.setdefault(s["project"], {
            "sessions": 0, "score_sum": 0.0, "scored": 0,
            "duration": 0.0, "tool_errors": 0, "commits": 0,
        })
        p["sessions"] += 1
        p["duration"] += s.get("duration_minutes") or 0
        p["tool_errors"] += s.get("tool_errors") or 0
        p["commits"] += s.get("git_commits") or 0
        if s.get("outcome") in _OUTCOME_SCORE:
            p["score_sum"] += _OUTCOME_SCORE[s["outcome"]]
            p["scored"] += 1
    project_rows = []
    for name, p in proj.items():
        success_pct = round(p["score_sum"] / p["scored"] * 100) if p["scored"] else None
        project_rows.append({
            "name": name, "sessions": p["sessions"],
            "success_pct": success_pct,
            "avg_duration": round(p["duration"] / p["sessions"], 1) if p["sessions"] else 0,
            "tool_errors": p["tool_errors"], "commits": p["commits"],
        })
    project_rows.sort(key=lambda r: r["sessions"], reverse=True)

    # Notable sessions: clean wins (fully_achieved, short) + worst (failed/partial, long)
    def _dur(s):
        return s.get("duration_minutes") or 0
    wins = sorted(
        [s for s in sessions if s.get("outcome") == "fully_achieved"],
        key=_dur,
    )[:5]
    sinks = sorted(
        [s for s in sessions if s.get("outcome") in ("partially_achieved", "failed", "mostly_achieved")],
        key=_dur, reverse=True,
    )[:5]

    def _slim(s):
        return {
            "session_id": s["session_id"], "project": s["project"],
            "outcome": s.get("outcome"), "duration_minutes": s.get("duration_minutes"),
            "summary": s.get("summary"), "goal": s.get("goal"),
        }

    total_scored = sum(outcomes.values())
    success_rate = round(
        sum(_OUTCOME_SCORE.get(o, 0) * n for o, n in outcomes.items()) / total_scored * 100
    ) if total_scored else None

    return {
        "totals": {
            "sessions": len(sessions),
            "scored_sessions": total_scored,
            "success_rate_pct": success_rate,
            "total_friction": sum(friction.values()),
            "total_tool_errors": sum(tool_errors.values()),
        },
        "outcomes": [{"name": k, "count": v} for k, v in outcomes.most_common()],
        "helpfulness": [{"name": k, "count": v} for k, v in helpfulness.most_common()],
        "session_types": [{"name": k, "count": v} for k, v in session_types.most_common()],
        "primary_successes": [{"name": k, "count": v} for k, v in successes.most_common()],
        "friction": [{"name": k, "count": v} for k, v in friction.most_common()],
        "goal_categories": [{"name": k, "count": v} for k, v in goals.most_common(10)],
        "top_tools": [{"name": k, "count": v} for k, v in tools.most_common(10)],
        "tool_errors": [{"name": k, "count": v} for k, v in tool_errors.most_common()],
        "languages": [{"name": k, "count": v} for k, v in langs.most_common(8)],
        "by_project": project_rows,
        "wins": [_slim(s) for s in wins],
        "time_sinks": [_slim(s) for s in sinks],
    }
