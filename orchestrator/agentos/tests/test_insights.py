"""Phase 8b — session insights loader + aggregator (synthetic usage-data)."""

from __future__ import annotations

import json

import pytest

from agentos.insights import aggregator, loader


@pytest.fixture
def fake_usage(tmp_path, monkeypatch):
    ud = tmp_path / "usage-data"
    (ud / "facets").mkdir(parents=True)
    (ud / "session-meta").mkdir(parents=True)

    def facet(sid, **kw):
        (ud / "facets" / f"{sid}.json").write_text(json.dumps({"session_id": sid, **kw}))

    def meta(sid, **kw):
        (ud / "session-meta" / f"{sid}.json").write_text(json.dumps({"session_id": sid, **kw}))

    facet("s1", outcome="fully_achieved", claude_helpfulness="very_helpful",
          friction_counts={}, goal_categories={"bug_fix": 1},
          primary_success="correct_code_edits", brief_summary="Fixed the bug")
    meta("s1", project_path="/x/projects/ExampleShop", duration_minutes=30,
         tool_counts={"Bash": 10, "Edit": 5}, tool_errors=1,
         tool_error_categories={"Command Failed": 1}, git_commits=2, languages={"Python": 3})

    facet("s2", outcome="partially_achieved", claude_helpfulness="moderately_helpful",
          friction_counts={"wrong_approach": 2}, goal_categories={"feature_implementation": 1},
          brief_summary="Half done")
    meta("s2", project_path="/x/projects/ExampleShop", duration_minutes=120,
         tool_counts={"Bash": 20}, tool_errors=3, tool_error_categories={"Other": 3})

    # meta-only session (no facet) — still counted
    meta("s3", project_path="/x/projects/ExampleNews", duration_minutes=10, tool_counts={"Read": 2})

    monkeypatch.setattr(loader, "USAGE_DIR", ud)
    return ud


def test_loads_and_joins(fake_usage):
    sessions = loader.load_sessions()
    assert len(sessions) == 3
    s1 = next(s for s in sessions if s["session_id"] == "s1")
    assert s1["outcome"] == "fully_achieved"
    assert s1["project"] == "ExampleShop"
    assert s1["tool_counts"]["Bash"] == 10
    assert s1["has_facet"] and s1["has_meta"]


def test_meta_only_session_included(fake_usage):
    sessions = loader.load_sessions()
    s3 = next(s for s in sessions if s["session_id"] == "s3")
    assert s3["has_meta"] and not s3["has_facet"]
    assert s3["outcome"] is None


def test_outcome_distribution(fake_usage):
    a = aggregator.aggregate()
    outcomes = {o["name"]: o["count"] for o in a["outcomes"]}
    assert outcomes["fully_achieved"] == 1
    assert outcomes["partially_achieved"] == 1


def test_success_rate(fake_usage):
    a = aggregator.aggregate()
    # fully(1.0) + partial(0.4) over 2 scored = 70%
    assert a["totals"]["success_rate_pct"] == 70


def test_friction_leaderboard(fake_usage):
    a = aggregator.aggregate()
    friction = {f["name"]: f["count"] for f in a["friction"]}
    assert friction["wrong_approach"] == 2


def test_per_project_quality(fake_usage):
    a = aggregator.aggregate()
    shop = next(p for p in a["by_project"] if p["name"] == "ExampleShop")
    assert shop["sessions"] == 2
    assert shop["tool_errors"] == 4  # 1 + 3
    # success: fully(1.0)+partial(0.4) / 2 = 70%
    assert shop["success_pct"] == 70


def test_tool_errors_aggregated(fake_usage):
    a = aggregator.aggregate()
    errs = {e["name"]: e["count"] for e in a["tool_errors"]}
    assert errs["Command Failed"] == 1
    assert errs["Other"] == 3


def test_wins_and_sinks(fake_usage):
    a = aggregator.aggregate()
    assert any(w["session_id"] == "s1" for w in a["wins"])      # fully_achieved
    assert any(s["session_id"] == "s2" for s in a["time_sinks"])  # partial, long


def test_empty_usage_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "USAGE_DIR", tmp_path / "nope")
    a = aggregator.aggregate()
    assert a["totals"]["sessions"] == 0
    assert a["totals"]["success_rate_pct"] is None
