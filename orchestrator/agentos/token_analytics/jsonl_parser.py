"""Parse Claude Code transcripts (~/.claude/projects/*.jsonl) into token usage.

Inspired by nateherkai/token-dashboard. Stays 100% local. Each .jsonl file is
one session; assistant messages carry a `usage` block we sum into per-session,
per-project, per-model, and per-day aggregates.

Incremental: results per file are cached on disk keyed by file mtime, so only
new/changed transcripts are re-parsed on each scan.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentos.core.config import AGENTOS_ROOT
from agentos.providers.pricing import cost_usd

PROJECTS_DIR = Path.home() / ".claude" / "projects"
CACHE_FILE = AGENTOS_ROOT / "orchestrator" / "runtime" / "token_cache.json"


def _project_from_cwd(cwd: str) -> str:
    """Derive a human project name from a session's working directory."""
    if not cwd:
        return "unknown"
    parts = Path(cwd).parts
    if "projects" in parts:
        i = parts.index("projects")
        if i + 1 < len(parts):
            return parts[i + 1]
    # strip a trailing .claude/worktrees/... segment
    for marker in (".claude", "worktrees"):
        if marker in parts:
            j = parts.index(marker)
            if j > 0:
                return parts[j - 1]
    return parts[-1] if parts else "unknown"


def _tool_names(content) -> list[str]:
    """Extract tool_use names from an assistant message's content blocks."""
    if not isinstance(content, list):
        return []
    return [b.get("name", "?") for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"]


def _file_reads(content) -> list[str]:
    """Extract file paths read (Read tool) for repeated-read detection."""
    if not isinstance(content, list):
        return []
    paths = []
    for b in content:
        if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Read":
            fp = (b.get("input") or {}).get("file_path")
            if fp:
                paths.append(fp)
    return paths


def _parse_file(path: Path) -> dict:
    """Summarize one transcript, DEDUPED BY message.id.

    Claude Code snapshots each streaming assistant response 2-3 times to disk as
    output grows. Summing every row inflates totals ~2x. We key usage by
    message.id and keep the LAST (complete) snapshot per id — matching what the
    API actually billed (see Nate Herk's token-dashboard accuracy note).
    """
    # message.id -> the latest seen record for that message
    by_msg: dict[str, dict] = {}
    project = "unknown"
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line or '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = d.get("message")
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            if project == "unknown" and d.get("cwd"):
                project = _project_from_cwd(d["cwd"])
            mid = msg.get("id") or f"_anon_{len(by_msg)}"
            by_msg[mid] = {  # last write wins (complete snapshot)
                "model": msg.get("model") or "unknown",
                "input": usage.get("input_tokens", 0) or 0,
                "output": usage.get("output_tokens", 0) or 0,
                "cache_read": usage.get("cache_read_input_tokens", 0) or 0,
                "cache_write": usage.get("cache_creation_input_tokens", 0) or 0,
                "ts": d.get("timestamp"),
                "tools": _tool_names(msg.get("content")),
                "reads": _file_reads(msg.get("content")),
            }

    summary = {
        "session_id": path.stem, "project": project,
        "models": {}, "by_day": {}, "tools": {}, "file_reads": {},
        "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
        "cost_usd": 0.0, "messages": len(by_msg),
        "first_ts": None, "last_ts": None,
        "top_turns": [],  # most expensive turns in this session (for Prompts tab)
    }
    turns = []
    for rec in by_msg.values():
        model, inp, out = rec["model"], rec["input"], rec["output"]
        cr, cw = rec["cache_read"], rec["cache_write"]
        c = cost_usd(model, inp, out, cw, cr)
        summary["input"] += inp
        summary["output"] += out
        summary["cache_read"] += cr
        summary["cache_write"] += cw
        summary["cost_usd"] += c

        m = summary["models"].setdefault(
            model, {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost": 0.0})
        m["input"] += inp
        m["output"] += out
        m["cache_read"] += cr
        m["cache_write"] += cw
        m["cost"] += c

        for tool in rec["tools"]:
            summary["tools"][tool] = summary["tools"].get(tool, 0) + 1
        for fp in rec["reads"]:
            summary["file_reads"][fp] = summary["file_reads"].get(fp, 0) + 1

        ts = rec["ts"]
        if ts:
            day = ts[:10]
            summary["by_day"].setdefault(day, {"tokens": 0, "cost": 0.0})
            summary["by_day"][day]["tokens"] += inp + out
            summary["by_day"][day]["cost"] += c
            if summary["first_ts"] is None or ts < summary["first_ts"]:
                summary["first_ts"] = ts
            if summary["last_ts"] is None or ts > summary["last_ts"]:
                summary["last_ts"] = ts

        turns.append({
            "model": model, "input": inp, "output": out,
            "cache_read": cr, "tokens": inp + out + cr + cw,
            "cost_usd": round(c, 4), "ts": ts,
            "tools": rec["tools"][:6],
        })

    # Keep only this session's most expensive turns (for the global Prompts tab).
    turns.sort(key=lambda r: r["tokens"], reverse=True)
    summary["top_turns"] = turns[:5]
    return summary


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache))


def scan(projects_dir: Path | None = None, use_cache: bool = True) -> list[dict]:
    """Return per-session summaries, parsing only new/changed files."""
    projects_dir = projects_dir or PROJECTS_DIR
    if not projects_dir.exists():
        return []
    cache = _load_cache() if use_cache else {}
    out, new_cache = [], {}
    for path in projects_dir.glob("**/*.jsonl"):
        key = str(path)
        mtime = path.stat().st_mtime
        cached = cache.get(key)
        if cached and cached.get("_mtime") == mtime:
            summary = cached["summary"]
        else:
            summary = _parse_file(path)
            summary = json.loads(json.dumps(summary))  # ensure plain types
        new_cache[key] = {"_mtime": mtime, "summary": summary}
        if summary["messages"] > 0:
            out.append(summary)
    if use_cache:
        _save_cache(new_cache)
    return out
