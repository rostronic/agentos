"""Read-only loader for the weekly SEO digests.

For each site that has a `docs/seo/reviews/` dir, picks the newest
`SEO_REVIEW_<date>.md` + matching `findings_<date>.json` and returns the digest
markdown plus the structured actionable / watch issues. The "Full digest" code
block in the markdown is extracted as a compact summary (used by notifications).

Sources are LOCAL files only — never writes, never hits the network. Repo paths
come from the project registry (config.projects); a project with no reviews dir
is simply omitted. Missing or unparseable files degrade gracefully.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from agentos.core.config import projects

_REVIEW_RE = re.compile(r"^SEO_REVIEW_(\d{4}-\d{2}-\d{2})\.md$")


def _label_for(slug: str, cfg: dict) -> str:
    """Display label for a site: an explicit registry `label`, else a title-cased
    slug (e.g. example-shop -> Example Shop)."""
    return str((cfg or {}).get("label") or slug.replace("-", " ").title())


def _sites() -> list[tuple[str, str]]:
    """Registered projects, in registry order, paired with a display label.

    No project is hardcoded: every registry entry is considered, and load_site
    silently skips any without a docs/seo/reviews dir (so non-SEO projects like a
    job-hunt entry never surface)."""
    return [(slug, _label_for(slug, cfg)) for slug, cfg in projects().items()]


def _reviews_dir(slug: str) -> Path | None:
    """Resolve <repo_path>/docs/seo/reviews for a registry slug, if it exists."""
    repo = projects().get(slug, {}).get("repo_path")
    if not repo:
        return None
    d = Path(repo).expanduser() / "docs" / "seo" / "reviews"
    return d if d.is_dir() else None


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _latest_date(reviews: Path) -> str | None:
    """Newest YYYY-MM-DD with an SEO_REVIEW_*.md, lexicographically (ISO-safe)."""
    dates = []
    for p in reviews.glob("SEO_REVIEW_*.md"):
        m = _REVIEW_RE.match(p.name)
        if m:
            dates.append(m.group(1))
    return max(dates) if dates else None


def _digest_block(markdown: str) -> str:
    """Extract the fenced 'Full digest' code block, or '' if absent.

    The weekly review embeds the messenger-style digest in a ``` fence under a
    '## Full digest' heading. We return its inner text (trimmed) for a compact
    notification body; falls back to '' so callers can degrade gracefully.
    """
    m = re.search(r"##\s*Full digest\s*\n+```[^\n]*\n(.*?)\n```", markdown, re.DOTALL)
    return m.group(1).strip() if m else ""


def load_site(slug: str, label: str) -> dict | None:
    """Latest digest + findings for one site, or None if it has no reviews."""
    reviews = _reviews_dir(slug)
    if reviews is None:
        return None
    date = _latest_date(reviews)
    if date is None:
        return None

    findings = _read_json(reviews / f"findings_{date}.json")
    markdown = _read_text(reviews / f"SEO_REVIEW_{date}.md")
    actionable = findings.get("actionable") if isinstance(findings, dict) else None
    watch = findings.get("watch") if isinstance(findings, dict) else None
    window = findings.get("window") if isinstance(findings, dict) else None

    return {
        "slug": slug,
        "label": label,
        "date": date,
        "window": window if isinstance(window, dict) else None,
        "actionable": actionable if isinstance(actionable, list) else [],
        "watch": watch if isinstance(watch, list) else [],
        "digest": _digest_block(markdown),
        "markdown": markdown,
    }


def load_sites() -> list[dict]:
    """All sites that have a weekly SEO review, newest digest each. Bad files → omit."""
    out = []
    for slug, label in _sites():
        site = load_site(slug, label)
        if site is not None:
            out.append(site)
    return out


def summary(sites: list[dict] | None = None) -> dict:
    """Counts for the dashboard KPI strip + the notification one-liner."""
    if sites is None:
        sites = load_sites()
    actionable = sum(len(s["actionable"]) for s in sites)
    watch = sum(len(s["watch"]) for s in sites)
    return {
        "sites": len(sites),
        "actionable": actionable,
        "watch": watch,
        "latest_date": max((s["date"] for s in sites), default=None),
    }


def notification_text(sites: list[dict] | None = None) -> str:
    """Compact one-message summary for Telegram / email.

    One line per site: label, window, actionable/watch counts, and each
    actionable issue. Empty string if there are no sites (nothing to send).
    """
    if sites is None:
        sites = load_sites()
    if not sites:
        return ""
    lines = ["Weekly SEO digest"]
    for s in sites:
        win = ""
        if s["window"] and s["window"].get("current"):
            cur = s["window"]["current"]
            win = f" ({cur[0]} → {cur[-1]})" if isinstance(cur, list) and cur else ""
        lines.append(
            f"\n{s['label']}{win} — {len(s['actionable'])} actionable, "
            f"{len(s['watch'])} watch"
        )
        for issue in s["actionable"]:
            sev = issue.get("severity", "?")
            area = issue.get("area", "?")
            detail = issue.get("detail", "")
            lines.append(f"  • [{sev}/{area}] {detail}")
    return "\n".join(lines)
