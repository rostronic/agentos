"""Methodology framework + per-project resolution (no network)."""

from __future__ import annotations

from agentos.core import config, methodology


def test_get_defaults_to_agile():
    assert methodology.get(None).name == "agile"
    assert methodology.get("nonexistent").name == "agile"
    assert methodology.get("XP").name == "xp"  # case-insensitive


def test_registry_has_four_disciplines():
    assert set(methodology.names()) == {"agile", "xp", "devops", "waterfall"}


def test_agile_has_no_extra_stages():
    a = methodology.get("agile")
    assert a.pre_implementation_stages({}) == []
    assert a.post_qa_stages({}) == []
    assert a.needs_qa is True


def test_xp_is_test_first_with_critic_gate():
    xp = methodology.get("xp")
    assert xp.test_first is True
    pre = xp.pre_implementation_stages({})
    assert len(pre) == 1 and pre[0].role == "qa" and pre[0].kind == "test"
    post = xp.post_qa_stages({})
    assert [s.role for s in post] == ["critic"]


def test_devops_adds_build_verify_after_qa():
    do = methodology.get("devops")
    assert do.pre_implementation_stages({}) == []
    post = do.post_qa_stages({})
    assert len(post) == 1 and post[0].role == "developer" and post[0].kind == "gate"


def test_order_ready_tasks_high_first():
    a = methodology.get("agile")
    tasks = [{"priority": "low", "created_at": "1"}, {"priority": "high", "created_at": "2"}]
    assert a.order_ready_tasks(tasks)[0]["priority"] == "high"


def test_methodology_for_resolution(monkeypatch):
    monkeypatch.setattr(config, "settings", lambda: {
        "orchestrator": {"default_methodology": "devops"},
        "projects": {"foo": {"methodology": "xp"}},
    })
    assert config.methodology_for("foo") == "xp"      # per-project wins
    assert config.methodology_for("bar") == "devops"  # global default
    monkeypatch.setattr(config, "settings", lambda: {})
    assert config.methodology_for("bar") == "agile"   # hard fallback


def test_cadence_for_resolution(monkeypatch):
    monkeypatch.setattr(config, "settings", lambda: {
        "orchestrator": {"default_cadence": "sprint"},
        "projects": {"foo": {"cadence": "backlog"}},
    })
    assert config.cadence_for("foo") == "backlog"
    assert config.cadence_for("bar") == "sprint"
    monkeypatch.setattr(config, "settings", lambda: {})
    assert config.cadence_for("bar") == "backlog"


def test_null_projects_key_is_safe(monkeypatch):
    # `projects:` present-but-null in YAML must not crash resolution.
    monkeypatch.setattr(config, "settings", lambda: {"projects": None, "orchestrator": None})
    assert config.project_settings("x") == {}
    assert config.methodology_for("x") == "agile"
    assert config.cadence_for("x") == "backlog"
