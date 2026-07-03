"""Continuous-backlog cadence + per-methodology pipelines (stubbed dispatch)."""

from __future__ import annotations

import pytest

from agentos.core import budget, config, sprint_executor
from agentos.core.router import DispatchOutcome
from agentos.storage import file_store as local_store
from agentos.storage.task_store import Project, Sprint, Task


@pytest.fixture
def unlimited_budget(monkeypatch):
    monkeypatch.setattr(budget, "budget_for_project", lambda project=None: {})
    import agentos.core.limits as limits_mod
    monkeypatch.setattr(limits_mod, "budget_for_project", lambda project=None: {})


def _project(slug):
    p = Project(name=slug, slug=slug, repo_path=None)
    local_store.create_project(p)
    return p


def _backlog_task(project_id, **kw):
    t = Task(project_id=project_id, sprint_id=None,
             title=kw.pop("title", "T"), status="ready",
             assignee=kw.pop("assignee", "developer"), **kw)
    local_store.create_task(t)
    return t


def _stub(monkeypatch, fn):
    monkeypatch.setattr(sprint_executor.router, "dispatch", fn)


def test_run_backlog_pulls_highest_priority_and_completes(unlimited_budget, monkeypatch):
    p = _project("bl")
    t1 = _backlog_task(p.id, title="low one", priority="low")
    t2 = _backlog_task(p.id, title="high one", priority="high")
    order = []

    def disp(agent, prompt, **kw):
        if agent != "qa":
            order.append("high" if "high one" in prompt else "low")
        return DispatchOutcome(ok=True, run_id="r",
                               text="PASS" if agent == "qa" else "x", cost_usd=0.0)
    _stub(monkeypatch, disp)

    res = sprint_executor.run_backlog("bl", mode="full")
    assert res.ok
    assert local_store.get_task(t1.id)["status"] == "done"
    assert local_store.get_task(t2.id)["status"] == "done"
    assert order[0] == "high"  # priority ordering honored in backlog mode


def test_run_backlog_ignores_sprint_attached_tasks(unlimited_budget, monkeypatch):
    p = _project("bl2")
    s = Sprint(project_id=p.id, name="S", status="active")
    local_store.create_sprint(s)
    sprinted = Task(project_id=p.id, sprint_id=s.id, title="sprinted",
                    status="ready", assignee="developer")
    local_store.create_task(sprinted)
    _stub(monkeypatch, lambda *a, **k: DispatchOutcome(ok=True, run_id="r", text="PASS", cost_usd=0.0))

    res = sprint_executor.run_backlog("bl2", mode="full")
    assert res.stopped_reason == "no ready tasks"  # sprint task is not in the backlog
    assert local_store.get_task(sprinted.id)["status"] == "ready"  # untouched


def test_xp_runs_tests_first_then_critic_gate(unlimited_budget, monkeypatch):
    p = _project("xpproj")
    monkeypatch.setattr(config, "methodology_for", lambda slug: "xp")
    _backlog_task(p.id, title="feature")
    calls = []

    def disp(agent, prompt, **kw):
        calls.append(agent)
        return DispatchOutcome(ok=True, run_id="r", text="PASS", cost_usd=0.0)
    _stub(monkeypatch, disp)

    sprint_executor.run_backlog("xpproj", mode="full")
    # XP: qa(write failing tests) → developer(impl) → qa(verify) → critic(review gate)
    assert calls == ["qa", "developer", "qa", "critic"]


def test_xp_critic_fail_blocks_task(unlimited_budget, monkeypatch):
    p = _project("xpfail")
    monkeypatch.setattr(config, "methodology_for", lambda slug: "xp")
    t = _backlog_task(p.id, title="feature")

    def disp(agent, prompt, **kw):
        return DispatchOutcome(ok=True, run_id="r",
                               text="FAIL no good" if agent == "critic" else "PASS", cost_usd=0.0)
    _stub(monkeypatch, disp)

    sprint_executor.run_backlog("xpfail", mode="full")
    assert local_store.get_task(t.id)["status"] == "blocked"


def test_devops_build_verify_runs_after_qa(unlimited_budget, monkeypatch):
    p = _project("doproj")
    monkeypatch.setattr(config, "methodology_for", lambda slug: "devops")
    _backlog_task(p.id, title="ship it")
    seq = []

    def disp(agent, prompt, **kw):
        is_build = "smoke build" in prompt or "CI checks" in prompt
        seq.append((agent, "build" if is_build else "work"))
        return DispatchOutcome(ok=True, run_id="r",
                               text="PASS" if agent == "qa" else "did it", cost_usd=0.0)
    _stub(monkeypatch, disp)

    sprint_executor.run_backlog("doproj", mode="full")
    # developer(impl) → qa → developer(build-verify gate, last)
    assert [a for a, _ in seq] == ["developer", "qa", "developer"]
    assert seq[-1][1] == "build"
