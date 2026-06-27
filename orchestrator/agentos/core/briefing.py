"""Daily briefing — assemble a morning digest from local AgentOS state.

Deterministic and offline: pipeline/cron health, open work-layer tasks, overnight
agent runs, and pending inbox questions. Written to ~/agentos/briefings/<date>.md
and surfaced via a macOS notification.

Planned follow-ups (tracked, refine as more projects onboard): a news/research
section (researcher dispatch) and email delivery (needs an email channel +
credential). Each section is computed defensively so one failing source never
breaks the whole brief.
"""

from __future__ import annotations

from pathlib import Path

from agentos.core import config

ACTIVE_TASK_STATUSES = ("ready", "in_progress", "blocked", "review")


def _safe(fn, default):
    try:
        return fn()
    except Exception:  # noqa: BLE001 — a brief should degrade, never crash
        return default


def _pipelines_section() -> str:
    from agentos.pipelines import loader

    jobs = loader.load_jobs()
    if not jobs:
        return "- No scheduled jobs found."
    s = loader.summary(jobs)
    erroring = [j for j in jobs if j.get("last_run_status") == "error"]
    out = [
        f"- {s.get('enabled', 0)} enabled job(s) across "
        f"{len(s.get('by_project', {}))} project(s); **{s.get('erroring', 0)} erroring**"
    ]
    for j in erroring[:8]:
        out.append(f"  - ⚠️ `{j.get('project', '')}/{j.get('name', '')}` — {j.get('last_error', 'error')}")
    return "\n".join(out)


def _tasks_section() -> str:
    from agentos.storage import file_store as local_store

    active = [t for t in local_store.list_tasks() if t.get("status") in ACTIVE_TASK_STATUSES]
    if not active:
        return "- No open tasks."
    by: dict[str, list] = {}
    for t in active:
        by.setdefault(t["status"], []).append(t)
    out = []
    for st in ACTIVE_TASK_STATUSES:
        items = by.get(st, [])
        if items:
            titles = ", ".join(t["title"][:50] for t in items[:6])
            out.append(f"- **{st}** ({len(items)}): {titles}")
    return "\n".join(out)


def _runs_section() -> str:
    from agentos.core import run_store

    s = run_store.stats()
    by = s.get("by_status", {})
    out = [
        f"- {s.get('total_runs', 0)} total runs · {by.get('done', 0)} done, "
        f"{by.get('failed', 0)} failed, {by.get('running', 0)} running · "
        f"${s.get('total_cost_usd', 0):.2f} API spend"
    ]
    for r in run_store.list_runs(limit=5, status="failed"):
        label = r.get("agent") or r.get("workflow_name") or "run"
        out.append(f"  - ❌ `{label}` — {(r.get('error') or '')[:80]}")
    return "\n".join(out)


def _inbox_section() -> str:
    from agentos.storage import file_store as local_store

    items = local_store.list_inbox(status="open")
    if not items:
        return "- Inbox clear — nothing waiting on you. 🎉"
    return "\n".join(
        f"- ❓ {i.get('from_agent', 'agent')}: {i.get('prompt', '')[:90]}" for i in items[:8]
    )


# Optional personal touches for the daily morning briefing.
SPANISH_WORDS = [
    ("aprovechar", "to make the most of", "¡Hay que aprovechar el día!"),
    ("lograr", "to achieve / accomplish", "¡Lo logramos!"),
    ("imprescindible", "essential / indispensable", "Es imprescindible tener un plan."),
    ("desarrollar", "to develop / build out", "Voy a desarrollar un nuevo agente."),
    ("avanzar", "to advance / make progress", "Avanzamos bastante anoche."),
    ("madrugada", "the small hours (2–6 AM)", "Trabajé hasta la madrugada."),
    ("mejorar", "to improve", "Cada día mejoramos."),
]
INSIGHTS = [
    "🔑 Before the inbox, name the ONE task that makes today a win — do that first.",
    "🤖 Review cron statuses weekly. One broken job = weeks of missed value.",
    "🏄 Recovery is ROI: sleep + surf measurably sharpen decisions.",
    "💰 Ship beats perfect: a daily-running scraper beats a perfect one shipped in 3 weeks.",
    "🎯 Sunday audit: stale 'in progress' tasks are usually blockers in disguise.",
    "🔋 Guard 9–11 AM — your peak focus window.",
    "🤝 If a task fits one sentence with clear success criteria, an agent can take it.",
]


def _pick(seq, date_str):
    import datetime
    try:
        idx = datetime.date.fromisoformat(date_str).toordinal() % len(seq)
    except Exception:  # noqa: BLE001
        idx = 0
    return seq[idx]


def _spanish_section(date_str: str) -> str:
    word, meaning, example = _pick(SPANISH_WORDS, date_str)
    return f"- **{word}** — {meaning}\n  - _{example}_"


def _insight_section(date_str: str) -> str:
    return f"- {_pick(INSIGHTS, date_str)}"


def _weather_section() -> str:
    """Configured location via the NWS API (no auth). Best-effort, short timeout.

    Location is read from config/user.yaml (weather_lat/weather_lon/weather_label)
    so the brief works for any user; NWS covers US locations.
    """
    import json
    import urllib.request

    lat, lon, _label = config.weather_location()
    contact = config.user_email() or "agentos@example.com"
    headers = {"User-Agent": f"AgentOS-daily-brief ({contact})"}

    def _get(url: str):
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=6) as r:  # noqa: S310 (trusted gov URL)
            return json.load(r)

    pts = _get(f"https://api.weather.gov/points/{lat},{lon}")
    fc = _get(pts["properties"]["forecast"])
    periods = fc["properties"]["periods"][:2]
    return "\n".join(
        f"- **{p['name']}:** {p['temperature']}°{p['temperatureUnit']}, {p['shortForecast']}"
        for p in periods
    )


def _today_blocks_section(date_str: str) -> str:
    """Today's time-blocks from the chief-of-staff weekly plan (offline file read)."""
    import datetime
    import json

    d = datetime.date.fromisoformat(date_str)
    iso = d.isocalendar()
    week_file = (
        config.AGENTOS_ROOT
        / config.cos_dir()
        / "proposals"
        / f"{iso[0]}-W{iso[1]:02d}.calendar.json"
    )
    if not week_file.exists():
        return "- No plan for this week yet — run the weekly planner (chief-of-staff)."
    events = json.loads(week_file.read_text(encoding="utf-8"))
    today = sorted(
        (e for e in events if str(e.get("start", "")).startswith(date_str)),
        key=lambda e: e.get("start", ""),
    )
    if not today:
        return "- No blocks scheduled today."
    marks = {"created": "✅", "approved": "☑️"}
    out = []
    for e in today:
        start, end = str(e.get("start", ""))[11:16], str(e.get("end", ""))[11:16]
        mark = marks.get(e.get("status", "proposed"), "◻️")
        out.append(
            f"- {mark} {start}–{end} **{e.get('summary', '(block)')}** "
            f"_({e.get('focus_area', '')})_"
        )
    return "\n".join(out)


def _meetings_section(date_str: str) -> str:
    from agentos.core import meeting_prep
    return meeting_prep.meeting_prep_section(date_str)


def _deadlines_section(date_str: str) -> str:
    from agentos.core import deadlines
    return deadlines.deadlines_section(date_str)


def _followups_section() -> str:
    from agentos.core import followups
    return followups.followups_section()


def _neglect_section() -> str:
    from agentos.core import cos_review
    return cos_review.neglect_section()


def build_briefing(*, date_str: str) -> str:
    """Compose the morning digest markdown for the given date string."""
    today_plan = _safe(lambda: _today_blocks_section(date_str), "- (plan unavailable)")
    meetings = _safe(lambda: _meetings_section(date_str), "- (meeting prep unavailable)")
    deadlines_md = _safe(lambda: _deadlines_section(date_str), "- (deadlines unavailable)")
    followups_md = _safe(_followups_section, "- (follow-ups unavailable)")
    neglect = _safe(_neglect_section, "- (neglect data unavailable)")
    weather = _safe(_weather_section, "- (weather unavailable)")
    weather_label = _safe(lambda: config.weather_location()[2], "")
    pipelines = _safe(_pipelines_section, "- (pipeline data unavailable)")
    tasks = _safe(_tasks_section, "- (task data unavailable)")
    runs = _safe(_runs_section, "- (run data unavailable)")
    inbox = _safe(_inbox_section, "- (inbox unavailable)")
    insight = _safe(lambda: _insight_section(date_str), "- (insight unavailable)")
    spanish = _safe(lambda: _spanish_section(date_str), "- (spanish unavailable)")
    return f"""# Daily update — {date_str}

## 🌤 Weather — {weather_label}
{weather}

## 🗓 Today's plan
{today_plan}

## 🤝 Meetings today
{meetings}

## ⏰ Deadlines (next 7 days)
{deadlines_md}

## 🔧 Pipelines & cron
{pipelines}

## 📋 Open tasks
{tasks}

## 🤖 Agent runs (recent)
{runs}

## 📥 Inbox (needs you)
{inbox}

## ✉️ Application follow-ups
{followups_md}

## 🌵 Neglected focus areas
{neglect}

## 💡 Insight
{insight}

## 🇪🇸 Spanish word of the day
{spanish}

---
_Calendar, email, and per-project live stats are connector follow-ups. Generated by `agentos brief`._
"""


def write_briefing(text: str, *, date_str: str, root: Path | None = None) -> Path:
    """Write the briefing to <root>/briefings/<date_str>.md."""
    d = (root or config.AGENTOS_ROOT) / "briefings"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{date_str}.md"
    path.write_text(text, encoding="utf-8")
    return path
