"""Follow-ups — surface pending job-application follow-ups in the daily brief (Phase 2).

The follow-up *scan* (read submitted applications, check Gmail for replies, decide what's gone
quiet, draft nudge emails) is an LLM stage — see
``workspaces/personal/chief-of-staff/stages/followups.md`` — which writes its findings to
``workspaces/personal/chief-of-staff/followups.md``. THIS module just reads that file back and
summarizes it for the morning briefing, mirroring ``briefing.py``: deterministic, offline-safe,
returns friendly markdown and never raises.
"""

from __future__ import annotations

from pathlib import Path

from agentos.core import config

COS_DIR = "workspaces/personal/chief-of-staff"


def _followups_path() -> Path:
    return config.AGENTOS_ROOT / COS_DIR / "followups.md"


def _summarize(text: str, *, max_items: int = 6) -> str:
    """Pull the actionable bullet lines out of the followups markdown for the brief.

    The stage writes a markdown file; we want the at-a-glance items, not the whole doc.
    Prefer top-level bullets (``- `` / ``* ``); fall back to the first non-title lines.
    """
    lines = [ln.rstrip() for ln in text.splitlines()]
    bullets = [
        ln.strip()
        for ln in lines
        if ln.lstrip().startswith(("- ", "* "))
    ]
    if bullets:
        chosen = bullets[:max_items]
        extra = len(bullets) - len(chosen)
        out = "\n".join(chosen)
        if extra > 0:
            out += f"\n- _…and {extra} more in `followups.md`._"
        return out

    # No bullets — take the first few meaningful (non-heading, non-blank) lines.
    body = [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    if not body:
        return "- No pending follow-ups recorded. 👍"
    return "\n".join(f"- {ln}" for ln in body[:max_items])


def followups_section() -> str:
    """Briefing markdown summarizing pending application follow-ups. Offline-safe.

    Reads ``followups.md`` if the follow-up stage has produced one; otherwise returns a
    friendly placeholder. Never raises.
    """
    try:
        p = _followups_path()
        if not p.exists():
            return "- No follow-up scan yet — run the chief-of-staff follow-ups stage."
        text = p.read_text(encoding="utf-8").strip()
        if not text:
            return "- No pending follow-ups recorded. 👍"
        return _summarize(text)
    except Exception:  # noqa: BLE001 — a brief should degrade, never crash
        return "- (follow-ups unavailable)"
