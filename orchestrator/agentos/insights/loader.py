"""Load Claude Code session insights from ~/.claude/usage-data/.

Two sources per session, joined by session_id:
  facets/<id>.json       — qualitative: outcome, friction, helpfulness, summary
  session-meta/<id>.json — quantitative: duration, tools, tokens, errors, langs

Stays 100% local. Sessions with meta but no facet (or vice-versa) are still
included with whatever fields are available.
"""

from __future__ import annotations

import json
from pathlib import Path

USAGE_DIR = Path.home() / ".claude" / "usage-data"


def _project_from_path(p: str) -> str:
    if not p:
        return "unknown"
    parts = Path(p).parts
    if "projects" in parts:
        i = parts.index("projects")
        if i + 1 < len(parts):
            return parts[i + 1]
    for marker in (".claude", "worktrees"):
        if marker in parts:
            j = parts.index(marker)
            if j > 0:
                return parts[j - 1]
    return parts[-1] if parts else "unknown"


def _read_json_dir(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    for f in path.glob("*.json"):
        try:
            d = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        sid = d.get("session_id") or f.stem
        out[sid] = d
    return out


def load_sessions(usage_dir: Path | None = None) -> list[dict]:
    """Return one merged record per session, joined by session_id."""
    usage_dir = usage_dir or USAGE_DIR
    facets = _read_json_dir(usage_dir / "facets")
    metas = _read_json_dir(usage_dir / "session-meta")

    sessions = []
    for sid in set(facets) | set(metas):
        facet = facets.get(sid, {})
        meta = metas.get(sid, {})
        sessions.append({
            "session_id": sid,
            "project": _project_from_path(meta.get("project_path", "")),
            # qualitative (facet)
            "outcome": facet.get("outcome"),
            "helpfulness": facet.get("claude_helpfulness"),
            "session_type": facet.get("session_type"),
            "goal": facet.get("underlying_goal"),
            "goal_categories": facet.get("goal_categories") or {},
            "friction_counts": facet.get("friction_counts") or {},
            "friction_detail": facet.get("friction_detail") or "",
            "primary_success": facet.get("primary_success"),
            "satisfaction": facet.get("user_satisfaction_counts") or {},
            "summary": facet.get("brief_summary"),
            # quantitative (meta)
            "duration_minutes": meta.get("duration_minutes"),
            "tool_counts": meta.get("tool_counts") or {},
            "tool_errors": meta.get("tool_errors", 0),
            "tool_error_categories": meta.get("tool_error_categories") or {},
            "languages": meta.get("languages") or {},
            "git_commits": meta.get("git_commits", 0),
            "lines_added": meta.get("lines_added", 0),
            "lines_removed": meta.get("lines_removed", 0),
            "user_interruptions": meta.get("user_interruptions", 0),
            "start_time": meta.get("start_time"),
            "has_facet": bool(facet),
            "has_meta": bool(meta),
        })
    return sessions
