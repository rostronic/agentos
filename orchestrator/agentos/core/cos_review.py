"""Chief-of-staff weekly review — deterministic look-back ("close the loop") helpers.

Phase 2 companion to ``weekly_plan.py``. The planner *proposes* blocks and the apply step
flips them ``approved`` → ``created``. THIS module reads those same proposals files back and
asks the retro questions: which blocks actually happened (``created``/``approved``) vs. just
sat ``proposed``? How did each focus area's planned hours land against its ``target_hours``?
Which areas have gone neglected across the last week or two?

Everything here is deterministic file IO over
``workspaces/personal/chief-of-staff/proposals/<week>.calendar.json`` (same path layout as
``weekly_plan.py``) plus ``config/focus-areas.yaml`` and ``config/goals.md``. The
``*_section()`` helpers are briefing-facing: they mirror ``briefing.py`` — offline-safe,
returning friendly markdown defaults and never raising.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import yaml

from agentos.core import config

# A block "happened" (or is committed to happen) once it leaves the proposed state.
HAPPENED_STATUSES = ("created", "approved")


# --------------------------------------------------------------------------- paths/labels


def current_week(date_str: str | None = None) -> str:
    """ISO week label ``YYYY-Www`` — mirrors ``weekly_plan.current_week``."""
    d = datetime.date.fromisoformat(date_str) if date_str else datetime.date.today()
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _shift_week(week: str, back: int) -> str:
    """The ISO-week label ``back`` weeks before ``week`` (back=0 → same week)."""
    year, wk = week.split("-W")
    monday = datetime.date.fromisocalendar(int(year), int(wk), 1)
    iso = (monday - datetime.timedelta(weeks=back)).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _proposals_path(week: str) -> Path:
    return config.AGENTOS_ROOT / config.cos_dir() / "proposals" / f"{week}.calendar.json"


def _reviews_path(week: str) -> Path:
    return config.AGENTOS_ROOT / config.cos_dir() / "reviews" / f"{week}.md"


def load_proposals(week: str) -> list[dict]:
    """Read a week's blocks; ``[]`` when the file is absent (offline-/missing-safe)."""
    p = _proposals_path(week)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a corrupt file should not crash a review
        return []
    return data if isinstance(data, list) else []


def _load_focus_areas() -> list[dict]:
    """The configured focus areas (slug/label/target_hours/priority); ``[]`` if missing."""
    p = config.AGENTOS_ROOT / config.cos_dir() / "config" / "focus-areas.yaml"
    if not p.exists():
        return []
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return []
    areas = data.get("focus_areas") or []
    return [a for a in areas if isinstance(a, dict)]


# --------------------------------------------------------------------------- block math


def _block_hours(block: dict) -> float:
    """Duration of a block in hours from its RFC3339 start/end; 0.0 if unparseable."""
    try:
        start = datetime.datetime.fromisoformat(block["start"])
        end = datetime.datetime.fromisoformat(block["end"])
    except Exception:  # noqa: BLE001
        return 0.0
    hours = (end - start).total_seconds() / 3600.0
    return round(hours, 2) if hours > 0 else 0.0


def _area_planned_hours(blocks: list[dict]) -> dict[str, float]:
    """Total planned hours per focus area across all blocks (regardless of status)."""
    out: dict[str, float] = {}
    for b in blocks:
        area = b.get("focus_area", "(unassigned)")
        out[area] = round(out.get(area, 0.0) + _block_hours(b), 2)
    return out


def _area_happened_hours(blocks: list[dict]) -> dict[str, float]:
    """Hours per area that actually happened (created/approved blocks only)."""
    out: dict[str, float] = {}
    for b in blocks:
        if b.get("status", "proposed") not in HAPPENED_STATUSES:
            continue
        area = b.get("focus_area", "(unassigned)")
        out[area] = round(out.get(area, 0.0) + _block_hours(b), 2)
    return out


# --------------------------------------------------------------------------- review model


def weekly_review(week: str) -> dict:
    """Retro for one week: status counts, per-area planned vs. target, blocks that slipped.

    Returns a plain dict (no IO side effects). ``blocks_total == 0`` flags "no plan found".
    """
    blocks = load_proposals(week)
    areas = _load_focus_areas()

    by_status: dict[str, int] = {}
    for b in blocks:
        st = b.get("status", "proposed")
        by_status[st] = by_status.get(st, 0) + 1

    planned = _area_planned_hours(blocks)
    happened = _area_happened_hours(blocks)

    # Per-area roll-up keyed off the configured areas, plus any stray areas seen in blocks.
    targets = {a.get("slug"): a for a in areas if a.get("slug")}
    seen_areas = set(planned) | set(targets)
    per_area: list[dict] = []
    for slug in sorted(seen_areas):
        cfg = targets.get(slug, {})
        target = float(cfg.get("target_hours", 0) or 0)
        plan_h = planned.get(slug, 0.0)
        done_h = happened.get(slug, 0.0)
        per_area.append(
            {
                "slug": slug,
                "label": cfg.get("label", slug),
                "priority": cfg.get("priority", ""),
                "target_hours": target,
                "planned_hours": plan_h,
                "happened_hours": done_h,
                "planned_vs_target": round(plan_h - target, 2),
                "happened_vs_target": round(done_h - target, 2),
            }
        )

    # "Didn't happen" — blocks that never left the proposed state.
    didnt_happen = [
        {
            "proposal_id": b.get("proposal_id", "?"),
            "summary": b.get("summary", "(block)"),
            "focus_area": b.get("focus_area", ""),
            "start": b.get("start", ""),
            "hours": _block_hours(b),
        }
        for b in blocks
        if b.get("status", "proposed") not in HAPPENED_STATUSES
    ]

    return {
        "week": week,
        "blocks_total": len(blocks),
        "by_status": by_status,
        "per_area": per_area,
        "didnt_happen": didnt_happen,
        "happened_hours_total": round(sum(happened.values()), 2),
        "planned_hours_total": round(sum(planned.values()), 2),
        "proposals_path": str(_proposals_path(week)),
        "reviews_path": str(_reviews_path(week)),
    }


def _render_review(review: dict) -> str:
    """Markdown for one ``weekly_review`` dict — pure string assembly."""
    week = review["week"]
    if review["blocks_total"] == 0:
        return (
            f"# Weekly review — {week}\n\n"
            f"- No plan found for {week} "
            f"(`proposals/{week}.calendar.json` missing or empty).\n"
            "- Nothing to look back on yet — run the weekly planner first.\n"
        )

    by_status = review["by_status"]
    status_line = ", ".join(f"{k}: {v}" for k, v in sorted(by_status.items())) or "none"

    out = [f"# Weekly review — {week}", ""]
    out.append("## Did it happen?")
    out.append(
        f"- {review['blocks_total']} block(s) — {status_line}."
    )
    out.append(
        f"- **{review['happened_hours_total']}h** happened "
        f"(created/approved) of **{review['planned_hours_total']}h** planned."
    )
    out.append("")

    out.append("## Per-area: planned vs. target")
    out.append("| Area | Priority | Planned | Happened | Target | Δ vs target |")
    out.append("|---|---|---|---|---|---|")
    for a in review["per_area"]:
        delta = a["happened_vs_target"]
        sign = "+" if delta >= 0 else ""
        out.append(
            f"| {a['label']} | {a['priority'] or '—'} | {a['planned_hours']}h "
            f"| {a['happened_hours']}h | {a['target_hours']}h | {sign}{delta}h |"
        )
    out.append("")

    out.append("## Didn't happen (never approved)")
    if not review["didnt_happen"]:
        out.append("- Everything proposed was approved or created. 🎉")
    else:
        for b in review["didnt_happen"]:
            day = str(b["start"])[:10]
            out.append(
                f"- ◻️ {day} **{b['summary']}** "
                f"_({b['focus_area']}, {b['hours']}h)_ — `{b['proposal_id']}`"
            )
    out.append("")
    out.append(
        "---\n_Look-back generated by `cos_review`. Hours counted from block start/end; "
        '"happened" = status created or approved._'
    )
    return "\n".join(out) + "\n"


def write_review(week: str) -> Path:
    """Render and write ``reviews/<week>.md``; returns the path. Deterministic IO."""
    review = weekly_review(week)
    text = _render_review(review)
    path = _reviews_path(week)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- nudges


def neglect_nudges(weeks_back: int = 2) -> list[str]:
    """Areas with ~0 happened hours across the last 1–2 weeks (most-neglected first).

    Looks at the current week plus the prior ``weeks_back - 1`` weeks. Only considers areas
    that carry a positive ``target_hours`` (areas you actually meant to spend time on).
    Returns short human-readable nudge strings; ``[]`` when nothing is neglected or no data.
    """
    weeks_back = max(1, weeks_back)
    this_week = current_week()
    weeks = [_shift_week(this_week, i) for i in range(weeks_back)]

    areas = _load_focus_areas()
    targeted = {
        a["slug"]: a
        for a in areas
        if a.get("slug") and float(a.get("target_hours", 0) or 0) > 0
    }
    if not targeted:
        return []

    happened: dict[str, float] = {slug: 0.0 for slug in targeted}
    any_plan = False
    for wk in weeks:
        blocks = load_proposals(wk)
        if blocks:
            any_plan = True
        for slug, hrs in _area_happened_hours(blocks).items():
            if slug in happened:
                happened[slug] = round(happened[slug] + hrs, 2)

    if not any_plan:
        return []

    nudges: list[tuple[float, str]] = []
    span = "this week" if weeks_back == 1 else f"the last {weeks_back} weeks"
    for slug, hrs in happened.items():
        if hrs < 0.5:  # ~zero — effectively neglected
            label = targeted[slug].get("label", slug)
            nudges.append((hrs, f"**{label}** — ~0h in {span}; nothing has happened there."))
    nudges.sort(key=lambda t: t[0])  # most neglected (least hours) first
    return [msg for _, msg in nudges]


# --------------------------------------------------------------------------- briefing sections


def neglect_section() -> str:
    """Briefing markdown: which focus areas have gone neglected lately. Offline-safe."""
    try:
        nudges = neglect_nudges()
    except Exception:  # noqa: BLE001 — a brief should degrade, never crash
        return "- (neglect data unavailable)"
    if not nudges:
        return "- Nothing neglected — every focus area got time recently. 👍"
    return "\n".join(f"- ⚠️ {n}" for n in nudges[:6])


def goals_section() -> str:
    """Briefing markdown: north-star goals from ``config/goals.md``. Offline-safe.

    Reads the goals file verbatim (it is hand-authored markdown). Returns a friendly
    placeholder when the file is missing/empty so the brief always has something.
    """
    try:
        p = config.AGENTOS_ROOT / config.cos_dir() / "config" / "goals.md"
        if not p.exists():
            return "- (none) — add north-star goals in `config/goals.md`."
        text = p.read_text(encoding="utf-8").strip()
        if not text:
            return "- (none) — `config/goals.md` is empty."
        # Strip a leading H1 title; keep the body (bullets/sub-sections) for the brief.
        lines = text.splitlines()
        if lines and lines[0].lstrip().startswith("# "):
            lines = lines[1:]
        body = "\n".join(lines).strip()
        return body or "- (none yet)"
    except Exception:  # noqa: BLE001
        return "- (goals unavailable)"
