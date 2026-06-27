"""SEO auto-remediation bridge — findings → ready tasks, de-duped.

The autouse ``isolate_runtime`` fixture (conftest.py) already points
``file_store.WORK_DIR`` at a per-test temp dir, so every task we file lands in a
clean store. We additionally stand up two work-store projects and synthetic
``findings_<date>.json`` files, then monkeypatch the registry resolution so the
bridge finds them.
"""

from __future__ import annotations

import json

import pytest

from agentos.seo import remediate
from agentos.storage import file_store as fs
from agentos.storage.task_store import Project


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _write_findings(reviews, date, actionable, watch=None):
    reviews.mkdir(parents=True, exist_ok=True)
    (reviews / f"findings_{date}.json").write_text(json.dumps({
        "run_date": date,
        "window": {"current": ["2026-06-16", "2026-06-22"]},
        "actionable": actionable,
        "watch": watch or [],
    }))
    # A markdown sibling so this looks like a real reviews dir (not required by
    # the bridge, but mirrors production layout).
    (reviews / f"SEO_REVIEW_{date}.md").write_text(f"# review {date}\n")


@pytest.fixture
def seo_env(tmp_path, monkeypatch):
    """Two sites (example-shop, example-news) with registry entries + work-store
    projects, plus a helper to drop synthetic findings for a slug/date."""
    repos = {}
    for slug, name in (("example-shop", "ExampleShop"), ("example-news", "example-news")):
        repo = tmp_path / slug
        (repo / "docs" / "seo" / "reviews").mkdir(parents=True)
        repos[slug] = repo
        # Create the matching work-store project (bridge resolves slug -> uuid).
        fs.create_project(Project(slug=slug, name=name, repo_path=str(repo)))

    monkeypatch.setattr(remediate, "projects", lambda: {
        slug: {"repo_path": str(repo)} for slug, repo in repos.items()
    })

    def add_findings(slug, date, actionable, watch=None):
        reviews = repos[slug] / "docs" / "seo" / "reviews"
        _write_findings(reviews, date, actionable, watch)

    add_findings.repos = repos  # type: ignore[attr-defined]
    return add_findings


# A representative GENUINE actionable finding.
MAJOR_SITEMAP = {
    "severity": "major",
    "area": "sitemap",
    "detail": "Sitemap has 3 errors blocking submission (crawled — 2026-06-25).",
}


# --------------------------------------------------------------------------- #
# Filing
# --------------------------------------------------------------------------- #
def test_files_ready_task_for_actionable(seo_env):
    seo_env("example-shop", "2026-06-25", actionable=[MAJOR_SITEMAP])
    res = remediate.remediate_project("example-shop")

    assert res.error is None
    assert res.created_count == 1
    assert res.deduped == []
    filed = res.created[0]
    assert filed.severity == "major" and filed.area == "sitemap"

    pid = remediate.project_id_for_slug("example-shop")
    tasks = fs.list_tasks(project_id=pid)
    assert len(tasks) == 1
    t = tasks[0]
    assert t["status"] == "ready"
    assert t["created_by"] == "workflow"
    assert t["priority"] == "high"  # major -> high
    # The de-dupe key is stamped into the body for later runs to find.
    assert remediate._key_marker(filed.key) in t["description"]


def test_watch_and_info_never_filed(seo_env):
    seo_env(
        "example-shop", "2026-06-25",
        actionable=[
            {"severity": "watch", "area": "indexing", "detail": "/feed not indexed"},
            {"severity": "info", "area": "sitemap", "detail": "coverage reads 0"},
        ],
        watch=[{"severity": "watch", "area": "indexing", "detail": "/comedians"}],
    )
    res = remediate.remediate_project("example-shop")
    assert res.created_count == 0
    assert res.actionable_total == 0          # none were genuinely actionable
    assert res.skipped_non_actionable == 2    # watch + info in actionable[] skipped
    pid = remediate.project_id_for_slug("example-shop")
    assert fs.list_tasks(project_id=pid) == []


# --------------------------------------------------------------------------- #
# De-dupe — the core guarantee
# --------------------------------------------------------------------------- #
def test_rerun_same_findings_is_deduped(seo_env):
    """Running twice over the SAME findings file files the task ONCE."""
    seo_env("example-shop", "2026-06-25", actionable=[MAJOR_SITEMAP])

    first = remediate.remediate_project("example-shop")
    assert first.created_count == 1

    second = remediate.remediate_project("example-shop")
    assert second.created_count == 0
    assert second.deduped == [first.created[0].key]

    pid = remediate.project_id_for_slug("example-shop")
    assert len(fs.list_tasks(project_id=pid)) == 1  # still exactly one


def test_recurring_issue_next_week_is_deduped(seo_env):
    """The SAME underlying issue in a LATER week's findings (different date and
    bumped error count) maps to the same key → not re-filed."""
    seo_env("example-shop", "2026-06-25", actionable=[MAJOR_SITEMAP])
    first = remediate.remediate_project("example-shop")
    assert first.created_count == 1

    # Next week: same issue, new date, count 3 -> 5. Stable key strips both.
    recurring = {
        "severity": "major",
        "area": "sitemap",
        "detail": "Sitemap has 5 errors blocking submission (crawled — 2026-07-02).",
    }
    seo_env("example-shop", "2026-07-02", actionable=[recurring])
    second = remediate.remediate_project("example-shop")
    assert second.created_count == 0
    assert second.deduped == [first.created[0].key]

    pid = remediate.project_id_for_slug("example-shop")
    assert len(fs.list_tasks(project_id=pid)) == 1


def test_new_distinct_issue_files_a_second_task(seo_env):
    seo_env("example-shop", "2026-06-25", actionable=[MAJOR_SITEMAP])
    remediate.remediate_project("example-shop")

    distinct = {
        "severity": "major",
        "area": "indexing",
        "detail": "Homepage returns noindex — blocking all organic traffic.",
    }
    seo_env("example-shop", "2026-07-02", actionable=[MAJOR_SITEMAP, distinct])
    res = remediate.remediate_project("example-shop")
    assert res.created_count == 1                 # only the new one
    assert len(res.deduped) == 1                  # the recurring sitemap one
    pid = remediate.project_id_for_slug("example-shop")
    assert len(fs.list_tasks(project_id=pid)) == 2


def test_duplicate_within_one_file_filed_once(seo_env):
    dup = dict(MAJOR_SITEMAP)
    seo_env("example-shop", "2026-06-25", actionable=[MAJOR_SITEMAP, dup])
    res = remediate.remediate_project("example-shop")
    assert res.created_count == 1
    assert len(res.deduped) == 1


# --------------------------------------------------------------------------- #
# Both projects covered by the same code path (a site with no orchestrator)
# --------------------------------------------------------------------------- #
def test_remediate_all_covers_both_sites(seo_env):
    seo_env("example-shop", "2026-06-25", actionable=[MAJOR_SITEMAP])
    seo_env("example-news", "2026-06-25", actionable=[{
        "severity": "major", "area": "robots",
        "detail": "robots.txt disallows / — entire site blocked from crawl.",
    }])
    results = remediate.remediate_all()
    by_slug = {r.slug: r for r in results}
    assert by_slug["example-shop"].created_count == 1
    assert by_slug["example-news"].created_count == 1

    shop_pid = remediate.project_id_for_slug("example-shop")
    news_pid = remediate.project_id_for_slug("example-news")
    assert len(fs.list_tasks(project_id=shop_pid)) == 1
    assert len(fs.list_tasks(project_id=news_pid)) == 1
    # Re-run is fully deduped across both.
    assert all(r.created_count == 0 for r in remediate.remediate_all())


# --------------------------------------------------------------------------- #
# Graceful degradation + dry-run
# --------------------------------------------------------------------------- #
def test_missing_findings_is_soft_error(seo_env):
    # example-news has a reviews dir but no findings files yet.
    res = remediate.remediate_project("example-news")
    assert res.created_count == 0
    assert res.error == "no findings file"


def test_dry_run_files_nothing(seo_env):
    seo_env("example-shop", "2026-06-25", actionable=[MAJOR_SITEMAP])
    res = remediate.remediate_project("example-shop", dry_run=True)
    assert res.created_count == 1                 # reports what WOULD be filed
    assert res.created[0].task_id == "(dry-run)"
    pid = remediate.project_id_for_slug("example-shop")
    assert fs.list_tasks(project_id=pid) == []    # but nothing was written


# --------------------------------------------------------------------------- #
# Summary + notification helpers (consumed by the run-path shell)
# --------------------------------------------------------------------------- #
def test_summary_line_and_notification(seo_env):
    seo_env("example-shop", "2026-06-25", actionable=[MAJOR_SITEMAP])
    results = remediate.remediate_all()
    line = remediate.summary_line(results)
    assert line.startswith("NEW_SEO_TASKS=1")
    assert "example-shop:1new/0deduped" in line

    body = remediate.notification_text(results)
    assert "1 new task" in body
    assert "[major/sitemap]" in body
    assert "deploy to prod" in body  # human-gated reminder present

    # Quiet week → empty notification body, count 0.
    quiet = remediate.remediate_all()
    assert remediate.summary_line(quiet).startswith("NEW_SEO_TASKS=0")
    assert remediate.notification_text(quiet) == ""


def test_cli_notify_output_shape(seo_env, capsys):
    seo_env("example-shop", "2026-06-25", actionable=[MAJOR_SITEMAP])
    rc = remediate.main(["--slug", "example-shop", "--notify"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("NEW_SEO_TASKS=1")
    assert "---NOTIFY---" in out
    # Body after the fence carries the human message.
    body = out.split("---NOTIFY---", 1)[1]
    assert "new task" in body


def test_issue_key_stable_across_date_and_count():
    a = remediate.issue_key("sitemap", "3 errors crawled — 2026-06-25")
    b = remediate.issue_key("sitemap", "5 errors crawled — 2026-07-02")
    assert a == b
    # Different area → different key.
    assert remediate.issue_key("indexing", "3 errors") != a
