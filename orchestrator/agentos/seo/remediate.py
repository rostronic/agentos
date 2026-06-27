"""SEO auto-remediation bridge — turn weekly findings into ``ready`` tasks.

This is the standing back-half of the weekly SEO pipeline (task "T4"). After
``weekly_seo_review.py`` writes a dated ``findings_<date>.json`` for a site, this
module reads the newest one and, for each GENUINE actionable issue, auto-files a
``ready`` task in the agentos work store under the correct project — so the team
acts on every digest automatically instead of a human re-reading it each Monday.

What "genuine actionable" means here (and the hard line this module holds):
  * Only ``findings["actionable"][]`` with severity in :data:`ACTIONABLE_SEVERITIES`
    (``major`` / ``actionable``) become tasks. ``watch`` / ``info`` are surfaced
    in the digest but NEVER filed — they are quality signals, not work.
  * It creates TASKS only. It never spawns a code-writing agent, never opens a
    PR, never deploys. Remediation of site code stays human-gated; the filed
    task is the hand-off point, not an auto-fix.

De-dupe is the other half of "standing": the same underlying issue recurring in
a later weekly run must NOT mint a second task. Each finding gets a stable
:func:`issue_key` (area + a date/count-normalized detail), which is stamped into
the task body as ``[seo-key: <key>]``. Before filing, we scan the project's
existing tasks for that marker and skip any key already present (in any status —
an open task is in flight; a done one was already handled). This mirrors a
project's own ``scripts/seo_remediation_dispatch.py`` key (where one exists) so the
two stay consistent, but lives in agentos so a project with no dispatch script is
covered by the same code path.

Provider-agnostic and LOCAL: reads committed findings files, writes the
git-backed ``work/tasks`` tree via :mod:`agentos.storage.file_store`. No network.

Programmatic entry point: :func:`remediate_project` (one slug) and
:func:`remediate_all` (every managed SEO site). A thin CLI (``__main__``) lets the
run-path shell call it and emit a one-line, parseable summary the notifier reads.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from agentos.core.config import projects
from agentos.storage import file_store as fs
from agentos.storage.task_store import Task

# --------------------------------------------------------------------------- #
# What counts as work. Keep in lockstep with weekly_seo_review.py's severity
# vocabulary: only these become tasks. `watch` / `info` are digest-only.
# --------------------------------------------------------------------------- #
ACTIONABLE_SEVERITIES = frozenset({"major", "actionable", "critical"})

# How the stable de-dupe key is stamped into a task body so a later run can find
# it. A single line, machine-greppable, human-legible.
KEY_MARKER_PREFIX = "[seo-key:"


def _key_marker(key: str) -> str:
    return f"{KEY_MARKER_PREFIX} {key}]"


_KEY_MARKER_RE = re.compile(r"\[seo-key:\s*([a-z0-9-]+)\]")


# --------------------------------------------------------------------------- #
# Stable de-dupe key — same definition a per-project dispatch script uses, so a
# recurring issue maps to one key across runs (and across repo conventions).
# --------------------------------------------------------------------------- #
def _slugify(text: str, *, maxlen: int = 20) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s[:maxlen].strip("-")


def issue_key(area: str, detail: str) -> str:
    """Stable de-dupe key for a finding, e.g. ``seo-sitemap-3f2a1c9d``.

    Derived from ``area`` + a normalized ``detail`` so the *same* issue reported
    in a later weekly run produces the *same* key. Volatile substrings are
    stripped before hashing: ISO dates, "crawled —" noise, and bare numbers
    (a sitemap going 3→5 errors is the same issue, not a new one). This matches
    a project's ``scripts/seo_remediation_dispatch.py:issue_key`` convention.
    """
    normalized = (detail or "").lower()
    normalized = re.sub(r"\d{4}-\d{2}-\d{2}", "", normalized)
    normalized = re.sub(r"crawled\s+[—-]+", "", normalized)
    normalized = re.sub(r"\d+", "#", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    digest = hashlib.sha1(f"{area}|{normalized}".encode("utf-8")).hexdigest()[:8]
    return f"seo-{_slugify(area)}-{digest}"


# --------------------------------------------------------------------------- #
# Findings discovery — resolve a slug's reviews dir from the registry, exactly
# like seo.loader does, then pick the newest findings_<date>.json.
# --------------------------------------------------------------------------- #
def _reviews_dir(slug: str) -> Path | None:
    repo = projects().get(slug, {}).get("repo_path")
    if not repo:
        return None
    d = Path(repo).expanduser() / "docs" / "seo" / "reviews"
    return d if d.is_dir() else None


def latest_findings_path(slug: str) -> Path | None:
    """Newest ``findings_*.json`` for a slug, or None if the site has none."""
    reviews = _reviews_dir(slug)
    if reviews is None:
        return None
    matches = sorted(glob.glob(str(reviews / "findings_*.json")))
    return Path(matches[-1]).resolve() if matches else None


def _load_findings(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


# --------------------------------------------------------------------------- #
# Project id resolution — the work store keys tasks by project UUID, not slug.
# --------------------------------------------------------------------------- #
def project_id_for_slug(slug: str) -> str | None:
    """UUID of the work-store project whose slug matches, or None."""
    for p in fs.list_projects():
        if p.get("slug") == slug:
            return p.get("id")
    return None


def _existing_keys(project_id: str) -> set[str]:
    """All seo-keys already stamped into this project's tasks (any status).

    Reads the description of every task (file_store exposes the body via the
    ``description`` field) and harvests the ``[seo-key: ...]`` markers. A key
    found here is NOT re-filed — that is the de-dupe guarantee across runs.
    """
    keys: set[str] = set()
    for t in fs.list_tasks(project_id=project_id):
        for blob in (t.get("description") or "", t.get("title") or "",
                     t.get("acceptance_criteria") or ""):
            keys.update(_KEY_MARKER_RE.findall(blob))
    return keys


# --------------------------------------------------------------------------- #
# Task shaping
# --------------------------------------------------------------------------- #
def _task_title(severity: str, area: str) -> str:
    return f"SEO remediation [{severity}/{area}] (auto-filed from weekly review)"


def _task_body(slug: str, run_date: str, finding: dict, key: str) -> str:
    area = finding.get("area", "unknown")
    severity = finding.get("severity", "unknown")
    detail = finding.get("detail", "")
    return (
        f"Auto-filed from the weekly SEO review for **{slug}** ({run_date}).\n\n"
        f"**Finding** [{severity}/{area}]: {detail}\n\n"
        f"{_key_marker(key)}\n\n"
        "This is a remediation TASK, not an auto-fix. A human (or a Dev agent a "
        "human spawns) fixes on a feature branch and opens a PR with `gh pr "
        "create`; review + merge stay human-gated. Merge is NOT a deploy — prod "
        'deploy stays gated on the literal phrase "deploy to prod".'
    )


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #
@dataclass
class FiledTask:
    key: str
    task_id: str
    title: str
    severity: str
    area: str


@dataclass
class ProjectResult:
    slug: str
    project_id: str | None
    findings_path: str | None
    run_date: str | None
    actionable_total: int = 0          # genuine actionable findings in the file
    created: list[FiledTask] = field(default_factory=list)   # NEW tasks this run
    deduped: list[str] = field(default_factory=list)         # keys skipped (already tracked)
    skipped_non_actionable: int = 0    # watch/info not filed
    error: str | None = None

    @property
    def created_count(self) -> int:
        return len(self.created)


# --------------------------------------------------------------------------- #
# Core
# --------------------------------------------------------------------------- #
def remediate_project(slug: str, *, findings_path: str | Path | None = None,
                      dry_run: bool = False) -> ProjectResult:
    """File ``ready`` tasks for one site's genuine actionable SEO findings.

    De-dupes within the batch and against the project's existing tasks. Filing
    is idempotent across weekly runs: re-running over the same (or a recurring)
    finding creates nothing new. ``dry_run=True`` computes what WOULD be filed
    without writing any task.
    """
    pid = project_id_for_slug(slug)
    path = Path(findings_path).resolve() if findings_path else latest_findings_path(slug)
    result = ProjectResult(
        slug=slug,
        project_id=pid,
        findings_path=str(path) if path else None,
        run_date=None,
    )
    if path is None:
        result.error = "no findings file"
        return result
    if pid is None:
        result.error = f"no work-store project for slug '{slug}'"
        return result

    findings = _load_findings(path)
    result.run_date = findings.get("run_date")
    actionable = findings.get("actionable")
    actionable = actionable if isinstance(actionable, list) else []

    existing = _existing_keys(pid)
    batch_seen: set[str] = set()

    for finding in actionable:
        severity = str(finding.get("severity", "")).lower()
        area = finding.get("area", "unknown")
        detail = finding.get("detail", "")
        if severity not in ACTIONABLE_SEVERITIES:
            result.skipped_non_actionable += 1
            continue
        result.actionable_total += 1
        key = issue_key(area, detail)
        if key in existing or key in batch_seen:
            result.deduped.append(key)
            continue
        batch_seen.add(key)
        title = _task_title(severity, area)
        if dry_run:
            result.created.append(FiledTask(key=key, task_id="(dry-run)",
                                            title=title, severity=severity, area=area))
            continue
        task = Task(
            project_id=pid,
            title=title,
            description=_task_body(slug, result.run_date or "unknown", finding, key),
            status="ready",
            priority="high" if severity in {"major", "critical"} else "medium",
            created_by="workflow",
        )
        task_id = fs.create_task(task)
        result.created.append(FiledTask(key=key, task_id=task_id, title=title,
                                        severity=severity, area=area))

    return result


def default_slugs() -> tuple[str, ...]:
    """The managed SEO sites, in registry order — every registered project.

    No slug is hardcoded: it derives from config/projects.yaml. A project with no
    findings is simply skipped by remediate_project (soft 'no findings file'), so a
    non-SEO entry never produces a task. Matches seo.loader's registry-driven order.
    """
    return tuple(projects().keys())


def remediate_all(slugs: tuple[str, ...] | None = None, *,
                  dry_run: bool = False) -> list[ProjectResult]:
    """Remediate every managed SEO site. Sites with no findings are skipped
    (their result carries ``error='no findings file'`` and zero created)."""
    if slugs is None:
        slugs = default_slugs()
    return [remediate_project(s, dry_run=dry_run) for s in slugs]


def summary_line(results: list[ProjectResult]) -> str:
    """One parseable line for the run-path shell: total NEW tasks + per-site.

    Always starts with ``NEW_SEO_TASKS=<n>`` so the notifier can grep the count
    and only notify when n > 0.
    """
    total_new = sum(r.created_count for r in results)
    parts = [f"NEW_SEO_TASKS={total_new}"]
    for r in results:
        if r.error and not r.created:
            continue
        parts.append(f"{r.slug}:{r.created_count}new/{len(r.deduped)}deduped")
    return " ".join(parts)


def notification_text(results: list[ProjectResult]) -> str:
    """Human Telegram/email body listing the NEW tasks. Empty if none created."""
    created = [(r, t) for r in results for t in r.created]
    if not created:
        return ""
    lines = [f"SEO auto-remediation: {len(created)} new task(s) filed"]
    for r, t in created:
        lines.append(f"\n{r.slug} [{t.severity}/{t.area}]")
        lines.append(f"  • {t.title}")
    lines.append(
        "\nTasks are ready in the work store. Remediation is human-gated: "
        "spawn a Dev agent / fix by hand, open a PR, merge after review. "
        "No deploy without \"deploy to prod\"."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI — invoked by the run-path shell after a successful weekly review.
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="File ready tasks from the latest weekly SEO findings "
                    "(de-duped). Creates TASKS only — never spawns agents, "
                    "opens PRs, or deploys.")
    parser.add_argument(
        "--slug", action="append", dest="slugs", default=None,
        help="Project slug to remediate (repeatable). Default: all registered "
             "projects (config/projects.yaml); non-SEO ones are skipped.")
    parser.add_argument(
        "--findings", default=None,
        help="Explicit findings_*.json path (implies a single --slug).")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Compute what would be filed without creating any task.")
    parser.add_argument(
        "--json", action="store_true",
        help="Emit a JSON result object instead of human text.")
    parser.add_argument(
        "--notify", action="store_true",
        help="After the summary line, emit a `---NOTIFY---` fence followed by "
             "the human notification body (the run-path shell reads this to "
             "post Telegram/email only when NEW tasks were filed).")
    args = parser.parse_args(argv)

    slugs = tuple(args.slugs) if args.slugs else default_slugs()
    if args.findings:
        if len(slugs) != 1:
            print("error: --findings requires exactly one --slug", file=sys.stderr)
            return 2
        results = [remediate_project(slugs[0], findings_path=args.findings,
                                     dry_run=args.dry_run)]
    else:
        results = [remediate_project(s, dry_run=args.dry_run) for s in slugs]

    if args.json:
        payload = [
            {
                "slug": r.slug,
                "project_id": r.project_id,
                "findings_path": r.findings_path,
                "run_date": r.run_date,
                "actionable_total": r.actionable_total,
                "created": [
                    {"key": t.key, "task_id": t.task_id, "title": t.title,
                     "severity": t.severity, "area": t.area}
                    for t in r.created
                ],
                "deduped": r.deduped,
                "skipped_non_actionable": r.skipped_non_actionable,
                "error": r.error,
            }
            for r in results
        ]
        json.dump(payload, sys.stdout, indent=2)
        print()
    else:
        # First line is always the parseable summary (the run-path greps it).
        print(summary_line(results))
        for r in results:
            tag = f"  ! {r.error}" if r.error else ""
            print(f"# {r.slug}: {r.created_count} new, {len(r.deduped)} deduped, "
                  f"{r.actionable_total} actionable, "
                  f"{r.skipped_non_actionable} non-actionable skipped{tag}")
            for t in r.created:
                print(f"    + [{t.severity}/{t.area}] {t.key} -> {t.task_id}")
        if args.notify:
            # Fenced notification body for the run-path shell; empty body still
            # emits the fence so the parser is uniform (it gates on the count).
            print("---NOTIFY---")
            print(notification_text(results))

    return 0


if __name__ == "__main__":
    sys.exit(main())
