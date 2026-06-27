"""Phase 8 — token analytics parser + aggregator (synthetic transcripts)."""

from __future__ import annotations

import json

import pytest

from agentos.token_analytics import aggregator, jsonl_parser


def _write_session(path, model, project_cwd, rows):
    """rows = list of (input, output, cache_read, cache_write, ts)."""
    lines = []
    for inp, out, cr, cw, ts in rows:
        lines.append(json.dumps({
            "type": "assistant",
            "timestamp": ts,
            "cwd": project_cwd,
            "message": {
                "role": "assistant", "model": model,
                "usage": {
                    "input_tokens": inp, "output_tokens": out,
                    "cache_read_input_tokens": cr,
                    "cache_creation_input_tokens": cw,
                },
            },
        }))
    path.write_text("\n".join(lines) + "\n")


@pytest.fixture
def fake_projects(tmp_path, monkeypatch):
    proj = tmp_path / "projects"
    (proj / "sess-a").mkdir(parents=True)
    (proj / "sess-b").mkdir(parents=True)
    _write_session(
        proj / "sess-a" / "s1.jsonl", "claude-sonnet-4-6",
        "/Users/x/code/projects/ExampleShop",
        [(1000, 500, 2000, 100, "2026-05-01T10:00:00Z"),
         (200, 300, 1500, 0, "2026-05-01T11:00:00Z")],
    )
    _write_session(
        proj / "sess-b" / "s2.jsonl", "claude-haiku-4-5",
        "/Users/x/code/projects/ExampleNews",
        [(50, 80, 0, 0, "2026-05-02T09:00:00Z")],
    )
    monkeypatch.setattr(jsonl_parser, "PROJECTS_DIR", proj)
    monkeypatch.setattr(jsonl_parser, "CACHE_FILE", tmp_path / "cache.json")
    return proj


def test_parses_project_from_cwd(fake_projects):
    sessions = jsonl_parser.scan(use_cache=False)
    projects = {s["project"] for s in sessions}
    assert projects == {"ExampleShop", "ExampleNews"}


def test_token_sums(fake_projects):
    sessions = jsonl_parser.scan(use_cache=False)
    shop = next(s for s in sessions if s["project"] == "ExampleShop")
    assert shop["input"] == 1200
    assert shop["output"] == 800
    assert shop["cache_read"] == 3500
    assert shop["messages"] == 2


def test_aggregate_totals(fake_projects):
    agg = aggregator.aggregate(jsonl_parser.scan(use_cache=False))
    t = agg["totals"]
    assert t["sessions"] == 2
    assert t["messages"] == 3
    assert t["input"] == 1250  # 1200 + 50
    assert t["cost_usd"] > 0
    assert 0 <= t["cache_hit_pct"] <= 100


def test_aggregate_breakdowns(fake_projects):
    agg = aggregator.aggregate(jsonl_parser.scan(use_cache=False))
    proj_names = {p["name"] for p in agg["by_project"]}
    assert proj_names == {"ExampleShop", "ExampleNews"}
    model_names = {m["name"] for m in agg["by_model"]}
    assert "claude-sonnet-4-6" in model_names
    # two distinct days
    assert len(agg["by_day"]) == 2


def test_incremental_cache(fake_projects, tmp_path):
    # first scan populates cache
    jsonl_parser.scan(use_cache=True)
    assert (jsonl_parser.CACHE_FILE).exists()
    # second scan uses cache, same result
    s2 = jsonl_parser.scan(use_cache=True)
    assert len(s2) == 2


def test_empty_projects_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(jsonl_parser, "PROJECTS_DIR", tmp_path / "nope")
    assert jsonl_parser.scan(use_cache=False) == []


def test_dedup_by_message_id(tmp_path, monkeypatch):
    """REGRESSION (bug #2): the same message.id written 3x (streaming snapshots)
    must be counted ONCE, using the final snapshot — not summed 3x."""
    proj = tmp_path / "projects" / "sess"
    proj.mkdir(parents=True)
    # Same message id 'm1' written 3 times with growing output (streaming).
    lines = []
    for out in (100, 250, 400):  # final snapshot = 400
        lines.append(json.dumps({
            "type": "assistant", "timestamp": "2026-05-01T10:00:00Z",
            "cwd": "/x/projects/P",
            "message": {"role": "assistant", "id": "m1", "model": "claude-sonnet-4-6",
                        "usage": {"input_tokens": 50, "output_tokens": out}},
        }))
    (proj / "s.jsonl").write_text("\n".join(lines) + "\n")
    monkeypatch.setattr(jsonl_parser, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(jsonl_parser, "CACHE_FILE", tmp_path / "c.json")

    sessions = jsonl_parser.scan(use_cache=False)
    s = sessions[0]
    assert s["messages"] == 1            # one message, not three
    assert s["output"] == 400            # final snapshot, not 100+250+400=750
    assert s["input"] == 50              # not 150


def test_tips_repeated_read():
    from agentos.token_analytics import tips_engine
    sessions = [{
        "session_id": "s1", "project": "P", "cost_usd": 1.0,
        "file_reads": {"/a/b/foo.py": 12},  # 12 reads → flagged (>=8)
    }]
    tips = tips_engine.generate_tips(sessions, {"cache_hit_pct": 90})
    assert any(t["kind"] == "repeated_read" and "foo.py" in t["message"] for t in tips)


def test_tips_low_cache():
    from agentos.token_analytics import tips_engine
    tips = tips_engine.generate_tips([], {"cache_hit_pct": 30.0})
    assert any(t["kind"] == "low_cache" for t in tips)


def test_tips_no_false_positive_on_healthy_data():
    from agentos.token_analytics import tips_engine
    sessions = [{"session_id": "s", "project": "P", "cost_usd": 1.0, "file_reads": {"/x": 2}}]
    tips = tips_engine.generate_tips(sessions, {"cache_hit_pct": 95.0})
    assert not any(t["kind"] in ("repeated_read", "low_cache") for t in tips)


def test_aggregate_includes_tools_and_tips(fake_projects):
    agg = aggregator.aggregate(jsonl_parser.scan(use_cache=False))
    assert "top_tools" in agg
    assert "tips" in agg
    assert "plans" in agg
    assert agg["totals"]["turns"] == agg["totals"]["messages"]


def test_tool_counts(fake_projects):
    """Tool calls are counted from tool_use content blocks."""
    proj = fake_projects
    (proj / "tools").mkdir()
    (proj / "tools" / "t.jsonl").write_text(json.dumps({
        "type": "assistant", "timestamp": "2026-05-03T10:00:00Z", "cwd": "/x/projects/P",
        "message": {"role": "assistant", "id": "mt", "model": "claude-sonnet-4-6",
                    "usage": {"input_tokens": 10, "output_tokens": 10},
                    "content": [{"type": "tool_use", "name": "Read"},
                                {"type": "tool_use", "name": "Bash"},
                                {"type": "text", "text": "hi"}]}}) + "\n")
    sessions = jsonl_parser.scan(use_cache=False)
    tool_session = next(s for s in sessions if s["tools"])
    assert tool_session["tools"].get("Read") == 1
    assert tool_session["tools"].get("Bash") == 1
