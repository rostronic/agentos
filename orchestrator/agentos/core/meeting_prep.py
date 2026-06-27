"""Meeting prep — for each real meeting today/tomorrow, pull context and write a brief.

Flow (all READ-ONLY; never writes the calendar, never sends mail):
  1. `gog calendar events` for today + tomorrow.
  2. Filter to *real* meetings — drop solo focus blocks (no other attendees) and
     all-day / informational items, so we only prep things with another human.
  3. For each meeting, `gog gmail search` the attendee address and/or the subject
     to surface recent context + the last time the user corresponded with them.
  4. Write a per-meeting brief to
     `workspaces/personal/chief-of-staff/meeting-prep/<date>-<slug>.md`
     (who / context / last contact / your goal).

`prep_today(date_str)` returns a summary dict and writes the files.
`meeting_prep_section(date_str)` is the briefing hook (today's meetings, offline-safe).

Same defensive contract as briefing.py / weekly_plan.py: every external (gog) call is
wrapped so a missing binary / OAuth failure / offline run degrades to "no calendar
access" rather than raising.
"""

from __future__ import annotations

import datetime
import json
import re
import subprocess
from pathlib import Path

from agentos.core import config

PREP_SUBDIR = "meeting-prep"

# eventTypes that are never a "meeting to prep" even if they sneak an attendee in.
_SKIP_EVENT_TYPES = {"outOfOffice", "focusTime", "workingLocation"}


def _today() -> datetime.date:
    return datetime.date.today()


def _prep_dir() -> Path:
    return config.AGENTOS_ROOT / config.cos_dir() / PREP_SUBDIR


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "meeting").lower()).strip("-")
    return (s or "meeting")[:40]


def _fetch_events(start: datetime.date, end_inclusive: datetime.date) -> list[dict]:
    """gog calendar events for [start, end_inclusive]. [] on any failure (offline-safe)."""
    end = end_inclusive + datetime.timedelta(days=1)  # gog --to is exclusive of next day
    cmd = [
        "gog", "-a", config.user_email(), "calendar", "events",
        "--from", start.isoformat(), "--to", end.isoformat(),
        "--order", "asc", "--max", "50", "--json",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except Exception:  # noqa: BLE001 — gog missing / blocked / timeout
        return []
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    try:
        data = json.loads(proc.stdout)
    except Exception:  # noqa: BLE001
        return []
    events = data.get("events", data) if isinstance(data, dict) else data
    return events if isinstance(events, list) else []


def _other_attendees(event: dict) -> list[str]:
    """Attendee emails that aren't the user. Empty => solo block."""
    account = config.user_email().lower()
    out: list[str] = []
    for a in event.get("attendees") or []:
        if not isinstance(a, dict):
            continue
        if a.get("self") or a.get("resource"):
            continue
        email = (a.get("email") or "").strip()
        if email and email.lower() != account:
            out.append(email)
    return out


def _is_real_meeting(event: dict) -> bool:
    """True only for events with another human attendee (skip solo focus blocks)."""
    if event.get("eventType") in _SKIP_EVENT_TYPES:
        return False
    start = event.get("start") or {}
    if "date" in start and "dateTime" not in start:
        return False  # all-day / informational
    return bool(_other_attendees(event))


def _start_local(event: dict) -> str:
    start = event.get("start") or {}
    return start.get("dateTime") or start.get("date") or ""


def _gmail_context(query: str, *, max_results: int = 3) -> list[str]:
    """Best-effort `gog gmail search`. Returns short subject/snippet lines; [] on failure."""
    cmd = [
        "gog", "-a", config.user_email(), "gmail", "search", query,
        "--max", str(max_results), "--json",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except Exception:  # noqa: BLE001
        return []
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    try:
        data = json.loads(proc.stdout)
    except Exception:  # noqa: BLE001
        return []
    threads = data.get("threads", data) if isinstance(data, dict) else data
    if not isinstance(threads, list):
        return []
    lines: list[str] = []
    for t in threads[:max_results]:
        if not isinstance(t, dict):
            continue
        subj = t.get("subject") or t.get("snippet") or "(thread)"
        date = (t.get("date") or t.get("internalDate") or "")
        date = str(date)[:10]
        lines.append(f"{date} — {str(subj)[:90]}".strip(" —"))
    return lines


def _build_prep(event: dict, date_str: str) -> dict:
    summary = event.get("summary") or "(meeting)"
    attendees = _other_attendees(event)
    when = _start_local(event)[11:16] or "all-day"
    location = event.get("location") or ""

    # Context: search by the primary attendee, then by subject keywords.
    context: list[str] = []
    last_contact = ""
    if attendees:
        primary = attendees[0]
        ctx = _gmail_context(f"from:{primary} OR to:{primary}")
        context += ctx
        if ctx:
            last_contact = ctx[0]
    if not context and summary:
        # Fall back to a subject-keyword search.
        kw = " ".join(w for w in re.findall(r"[A-Za-z0-9]{4,}", summary)[:3])
        if kw:
            context += _gmail_context(kw)

    return {
        "summary": summary,
        "when": when,
        "attendees": attendees,
        "location": location,
        "context": context,
        "last_contact": last_contact,
        "slug": f"{date_str}-{when.replace(':', '')}-{_slug(summary)}",
    }


def _render_prep_md(prep: dict, date_str: str) -> str:
    who = ", ".join(prep["attendees"]) or "(no external attendees listed)"
    loc = f" · {prep['location']}" if prep["location"] else ""
    lines = [
        f"# Meeting prep — {prep['summary']}",
        "",
        f"- **When:** {date_str} {prep['when']}{loc}",
        f"- **Who:** {who}",
        "",
        "## Recent context (Gmail, read-only)",
    ]
    if prep["context"]:
        lines += [f"- {c}" for c in prep["context"]]
    else:
        lines.append("- _No recent email found (or Gmail unavailable)._")
    lines += [
        "",
        f"- **Last contact:** {prep['last_contact'] or '(unknown)'}",
        "",
        "## Your goal",
        "- [ ] _What does a win from this meeting look like? Fill before you walk in._",
        "",
        "---",
        "_Auto-staged by `agentos` meeting-prep. Read-only: no email sent, calendar untouched._",
    ]
    return "\n".join(lines) + "\n"


def prep_today(date_str: str | None = None) -> dict:
    """Prep meetings for `date_str` (default today) + tomorrow. Writes per-meeting briefs.

    Returns {date, meetings: [...], files: [...], calendar_available, error?}. Offline-safe.
    """
    try:
        today = datetime.date.fromisoformat(date_str) if date_str else _today()
    except (ValueError, TypeError):
        today = _today()
    tomorrow = today + datetime.timedelta(days=1)

    events = _fetch_events(today, tomorrow)
    if not events:
        return {
            "date": today.isoformat(),
            "meetings": [],
            "files": [],
            "calendar_available": False,
        }

    by_day: dict[str, list[dict]] = {}
    for e in events:
        if not isinstance(e, dict) or not _is_real_meeting(e):
            continue
        ds = _start_local(e)[:10] or today.isoformat()
        by_day.setdefault(ds, []).append(e)

    out_meetings: list[dict] = []
    out_files: list[str] = []
    prep_dir = _prep_dir()
    for ds, evs in by_day.items():
        for e in evs:
            prep = _build_prep(e, ds)
            out_meetings.append(
                {
                    "date": ds,
                    "summary": prep["summary"],
                    "when": prep["when"],
                    "attendees": prep["attendees"],
                }
            )
            try:
                prep_dir.mkdir(parents=True, exist_ok=True)
                path = prep_dir / f"{prep['slug']}.md"
                path.write_text(_render_prep_md(prep, ds), encoding="utf-8")
                out_files.append(str(path))
            except OSError:
                pass

    return {
        "date": today.isoformat(),
        "meetings": out_meetings,
        "files": out_files,
        "calendar_available": True,
    }


def meeting_prep_section(date_str: str | None = None) -> str:
    """Briefing section: today's real meetings (with whom). Offline-safe, no writes."""
    try:
        today = datetime.date.fromisoformat(date_str) if date_str else _today()
    except (ValueError, TypeError):
        today = _today()

    events = _fetch_events(today, today)
    if not events:
        return "- No calendar access — meeting prep skipped (gog offline)."

    meetings = [e for e in events if isinstance(e, dict) and _is_real_meeting(e)]
    if not meetings:
        return "- No meetings today — heads-down day. 🎧"

    out: list[str] = []
    for e in sorted(meetings, key=_start_local):
        when = _start_local(e)[11:16] or "all-day"
        who = ", ".join(_other_attendees(e)[:3]) or "?"
        out.append(f"- **{when}** {e.get('summary', '(meeting)')} — with {who}")
    return "\n".join(out)
