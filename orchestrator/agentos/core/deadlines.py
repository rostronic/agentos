"""Deadline radar — surface upcoming dates across the user's personal workspaces.

Two sources, merged and sorted soonest-first:
  1. **Markdown scan** — regex over `workspaces/personal/*/` `.md` files for ISO dates
     (`YYYY-MM-DD`) and a few common written formats ("June 20, 2026", "06/20/2026").
     Each hit carries its source project + a short context line so the radar is
     traceable. Only future-or-today dates are kept.
  2. **Calendar** — `gog calendar events` for the next ~30 days (READ-ONLY; never
     writes the calendar). Best-effort: if gog is missing / OAuth fails / offline, the
     calendar source is simply skipped.

`scan_deadlines()` writes the merged radar to
`workspaces/personal/chief-of-staff/deadlines.md` and returns the rows.
`deadlines_section(date_str)` is the briefing hook (next ~7 days, offline-safe).

Mirrors the briefing/weekly-plan style: deterministic helpers, every external call
defended so the radar degrades to "scan-only" or "(unavailable)" rather than raising.
Other projects are READ-ONLY — this module only ever reads their markdown.
"""

from __future__ import annotations

import datetime
import json
import re
import subprocess
from pathlib import Path

from agentos.core import config

# Projects we never scan as a "source" (chief-of-staff is the radar's own home;
# memory is cross-cutting glue, not a deadline source).
SKIP_PROJECTS = {"chief-of-staff", "memory"}

CALENDAR_HORIZON_DAYS = 30
BRIEFING_HORIZON_DAYS = 7

# Lines whose date is almost certainly NOT a deadline — log/changelog noise.
_NOISE_HINTS = ("changelog", "updated:", "last updated", "as of", "© ", "copyright")

MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
# "June 20, 2026" / "Jun 20 2026" / "20 June 2026"
_LONG_RE = re.compile(
    r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b"
)
_LONG_RE_DMY = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b"
)
# "06/20/2026" or "6/20/26" (assume US M/D/Y)
_SLASH_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")

# Words near a date that hint it's a real deadline (used only to label, not filter).
_DEADLINE_HINTS = (
    "due", "deadline", "expires", "expire", "renew", "renewal", "by ", "before",
    "submit", "closing", "closes", "ends", "end ", "register", "registration",
    "payment", "pay ", "appointment", "appt", "scheduled", "rsvp", "ship",
)


def _personal_root() -> Path:
    return config.AGENTOS_ROOT / config.personal_dir()


def _today() -> datetime.date:
    return datetime.date.today()


def _safe_date(year: int, month: int, day: int) -> datetime.date | None:
    try:
        return datetime.date(year, month, day)
    except ValueError:
        return None


def _normalize_year(y: int) -> int:
    return 2000 + y if y < 100 else y


def _extract_dates(line: str) -> list[datetime.date]:
    """Pull every parseable date out of one line of text."""
    out: list[datetime.date] = []
    for y, m, d in _ISO_RE.findall(line):
        dt = _safe_date(int(y), int(m), int(d))
        if dt:
            out.append(dt)
    for mon, d, y in _LONG_RE.findall(line):
        mnum = MONTHS.get(mon.lower())
        if mnum:
            dt = _safe_date(int(y), mnum, int(d))
            if dt:
                out.append(dt)
    for d, mon, y in _LONG_RE_DMY.findall(line):
        mnum = MONTHS.get(mon.lower())
        if mnum:
            dt = _safe_date(int(y), mnum, int(d))
            if dt:
                out.append(dt)
    for m, d, y in _SLASH_RE.findall(line):
        dt = _safe_date(_normalize_year(int(y)), int(m), int(d))
        if dt:
            out.append(dt)
    return out


def _context(line: str) -> str:
    """A trimmed, de-noised one-liner for the radar."""
    s = re.sub(r"\s+", " ", line).strip()
    s = s.lstrip("#->*•[]| \t").strip()
    return s[:120]


def _looks_like_deadline(line: str) -> bool:
    low = line.lower()
    return any(h in low for h in _DEADLINE_HINTS)


def _is_noise(line: str) -> bool:
    low = line.lower()
    return any(h in low for h in _NOISE_HINTS)


def _scan_markdown(today: datetime.date, horizon: int | None = None) -> list[dict]:
    """Scan every personal project's .md for future dates.

    `horizon=None` keeps everything from today forward; an int caps it to N days out.
    """
    root = _personal_root()
    rows: list[dict] = []
    if not root.exists():
        return rows
    cutoff = today + datetime.timedelta(days=horizon) if horizon is not None else None
    seen: set[tuple] = set()  # (project, date, context) dedupe
    for project_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        project = project_dir.name
        if project in SKIP_PROJECTS or project.startswith("."):
            continue
        for md in sorted(project_dir.rglob("*.md")):
            # Skip vendored / archived noise dirs cheaply.
            parts = {p.lower() for p in md.parts}
            if {".git", "node_modules", "archive", "archived"} & parts:
                continue
            try:
                text = md.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for line in text.splitlines():
                if not line.strip() or _is_noise(line):
                    continue
                if not _looks_like_deadline(line):
                    continue  # require a deadline cue — a bare date in prose is noise
                for dt in _extract_dates(line):
                    if dt < today:
                        continue
                    if cutoff is not None and dt > cutoff:
                        continue
                    ctx = _context(line)
                    key = (project, dt.isoformat(), ctx)
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(
                        {
                            "date": dt.isoformat(),
                            "project": project,
                            "source": "markdown",
                            "context": ctx,
                            "file": str(md),
                            "is_deadline_hint": _looks_like_deadline(line),
                            "days_out": (dt - today).days,
                        }
                    )
    return rows


def _event_start_date(event: dict) -> str | None:
    start = event.get("start") or {}
    raw = start.get("dateTime") or start.get("date") or ""
    return raw[:10] if len(raw) >= 10 else None


def _scan_calendar(today: datetime.date, horizon: int = CALENDAR_HORIZON_DAYS) -> list[dict]:
    """Read upcoming calendar events via gog (READ-ONLY). Empty list on any failure."""
    end = today + datetime.timedelta(days=horizon)
    cmd = [
        "gog", "-a", config.user_email(), "calendar", "events",
        "--from", today.isoformat(), "--to", end.isoformat(),
        "--order", "asc", "--max", "100", "--json",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except Exception:  # noqa: BLE001 — gog missing / blocked / timeout → skip source
        return []
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    try:
        data = json.loads(proc.stdout)
    except Exception:  # noqa: BLE001
        return []
    events = data.get("events", data) if isinstance(data, dict) else data
    if not isinstance(events, list):
        return []
    rows: list[dict] = []
    for e in events:
        if not isinstance(e, dict):
            continue
        ds = _event_start_date(e)
        if not ds:
            continue
        try:
            dt = datetime.date.fromisoformat(ds)
        except ValueError:
            continue
        if dt < today:
            continue
        rows.append(
            {
                "date": dt.isoformat(),
                "project": "calendar",
                "source": "calendar",
                "context": (e.get("summary") or "(busy)")[:120],
                "file": "",
                "is_deadline_hint": True,
                "days_out": (dt - today).days,
            }
        )
    return rows


def _sort_rows(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (r["date"], 0 if r["source"] == "calendar" else 1, r["project"]))


def _deadlines_path() -> Path:
    return config.AGENTOS_ROOT / config.cos_dir() / "deadlines.md"


def _render_markdown(rows: list[dict], today: datetime.date, *, calendar_ok: bool) -> str:
    head = [
        f"# Deadline radar — generated {today.isoformat()}",
        "",
        "Soonest first. Sources: personal-workspace markdown scan + calendar (next "
        f"{CALENDAR_HORIZON_DAYS} days, read-only). Auto-generated by `agentos` — "
        "verify before acting; the markdown scan is heuristic and may surface "
        "non-deadline dates.",
        "",
    ]
    if not calendar_ok:
        head.append("> ⚠️ Calendar unavailable (gog offline / OAuth) — markdown-only radar.\n")
    if not rows:
        head.append("_No upcoming dates found._")
        return "\n".join(head) + "\n"
    head.append("| When | In | Source | Project | Context |")
    head.append("|---|---|---|---|---|")
    for r in rows:
        d = r["days_out"]
        when = "today" if d == 0 else ("tomorrow" if d == 1 else f"{d}d")
        flag = " ⏰" if r.get("is_deadline_hint") else ""
        ctx = r["context"].replace("|", "\\|")
        head.append(
            f"| {r['date']} | {when}{flag} | {r['source']} | {r['project']} | {ctx} |"
        )
    return "\n".join(head) + "\n"


def scan_deadlines() -> list[dict]:
    """Scan markdown + calendar, write deadlines.md, return merged rows (soonest first).

    Offline-safe: a failing source contributes nothing; the file is still written.
    """
    today = _today()
    md_rows: list[dict] = []
    try:
        md_rows = _scan_markdown(today)
    except Exception:  # noqa: BLE001
        md_rows = []
    cal_rows: list[dict] = []
    calendar_ok = False
    try:
        cal_rows = _scan_calendar(today)
        calendar_ok = True
    except Exception:  # noqa: BLE001
        cal_rows = []
    rows = _sort_rows(md_rows + cal_rows)
    try:
        path = _deadlines_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_markdown(rows, today, calendar_ok=calendar_ok), encoding="utf-8")
    except OSError:
        pass
    return rows


def deadlines_section(date_str: str | None = None) -> str:
    """Briefing section: upcoming deadlines in the next ~7 days. Offline-safe."""
    try:
        today = datetime.date.fromisoformat(date_str) if date_str else _today()
    except (ValueError, TypeError):
        today = _today()

    rows: list[dict] = []
    try:
        rows += _scan_markdown(today, horizon=BRIEFING_HORIZON_DAYS)
    except Exception:  # noqa: BLE001
        pass
    try:
        rows += _scan_calendar(today, horizon=BRIEFING_HORIZON_DAYS)
    except Exception:  # noqa: BLE001
        pass

    rows = _sort_rows(rows)
    if not rows:
        return "- Nothing due in the next 7 days. 🎉"

    out: list[str] = []
    for r in rows[:10]:
        d = r["days_out"]
        when = "today" if d == 0 else ("tomorrow" if d == 1 else f"in {d}d")
        flag = "⏰ " if r.get("is_deadline_hint") else ""
        proj = "" if r["project"] == "calendar" else f" _({r['project']})_"
        out.append(f"- {flag}**{when}** — {r['context']}{proj}")
    return "\n".join(out)
