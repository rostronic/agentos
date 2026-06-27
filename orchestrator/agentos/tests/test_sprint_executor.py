"""Phase 6 — sprint executor state machine (stubbed dispatch, no network)."""

from __future__ import annotations

import pytest

from agentos.core import budget, killswitch, sprint_executor
from agentos.core.router import DispatchOutcome
from agentos.storage import file_store as local_store
from agentos.storage.task_store import Project, Sprint, Task


@pytest.fixture
def unlimited_budget(monkeypatch):
    monkeypatch.setattr(budget, "budget_for_project", lambda project=None: {})
    # also patch the copy imported into sprint_executor's modules
    import agentos.core.limits as limits_mod
    monkeypatch.setattr(limits_mod, "budget_for_project", lambda project=None: {})


@pytest.fixture
def project_sprint():
    p = Project(name="Test", slug="test", repo_path=None)
    local_store.create_project(p)
    s = Sprint(project_id=p.id, name="S1", status="active")
    local_store.create_sprint(s)
    return p, s


def _add_task(project_id, sprint_id, **kw):
    t = Task(project_id=project_id, sprint_id=sprint_id,
             title=kw.pop("title", "T"), status=kw.pop("status", "ready"),
             assignee=kw.pop("assignee", "developer"), **kw)
    local_store.create_task(t)
    return t


def _stub_dispatch(monkeypatch, fn):
    monkeypatch.setattr(sprint_executor.router, "dispatch", fn)


def test_passing_task_full_mode_marked_done(project_sprint, unlimited_budget, monkeypatch):
    p, s = project_sprint
    t = _add_task(p.id, s.id, title="Build it")

    def disp(agent, prompt, **kw):
        text = "PASS — looks good" if agent == "qa" else "Did the work"
        return DispatchOutcome(ok=True, run_id="r", text=text, cost_usd=0.01)
    _stub_dispatch(monkeypatch, disp)

    res = sprint_executor.execute_sprint(s.id, mode="full")
    assert res.ok
    assert local_store.get_task(t.id)["status"] == "done"
    assert res.processed[0].qa_passed is True


def test_in_agentos_project_runs_in_place_no_worktree(unlimited_budget, monkeypatch):
    """REGRESSION: a project whose repo_path is inside the agentos repo (personal
    projects at workspaces/personal/<slug>) must run in place — never get a
    worktree (which previously rooted on the wrong repo)."""
    from agentos.core import config
    repo = str(config.AGENTOS_ROOT / "workspaces" / "personal" / "demo")
    p = Project(name="Demo", slug="demo", repo_path=repo)
    local_store.create_project(p)
    s = Sprint(project_id=p.id, name="S", status="active")
    local_store.create_sprint(s)
    t = _add_task(p.id, s.id, assignee="developer")

    # Fail loudly if a worktree is ever created for an in-agentos project.
    monkeypatch.setattr(sprint_executor.worktree, "create_worktree",
                        lambda *a, **k: pytest.fail("worktree created for in-agentos project"))
    seen = {}

    def disp(agent, prompt, **kw):
        seen[agent] = kw.get("workdir")
        return DispatchOutcome(ok=True, run_id="r",
                               text="PASS" if agent == "qa" else "done", cost_usd=0.0)
    _stub_dispatch(monkeypatch, disp)

    sprint_executor.execute_sprint(s.id, mode="full")
    # Runs from the agentos REPO ROOT (so .claude/settings.json command
    # allowlists resolve and the agent can git commit), not the subdir.
    assert seen["developer"] == str(config.AGENTOS_ROOT)
    assert local_store.get_task(t.id)["status"] == "done"


def test_passing_task_manual_mode_marked_review(project_sprint, unlimited_budget, monkeypatch):
    p, s = project_sprint
    t = _add_task(p.id, s.id)

    def disp(agent, prompt, **kw):
        return DispatchOutcome(ok=True, run_id="r",
                               text="PASS" if agent == "qa" else "done", cost_usd=0.0)
    _stub_dispatch(monkeypatch, disp)

    sprint_executor.execute_sprint(s.id, mode="manual")
    assert local_store.get_task(t.id)["status"] == "review"


def test_qa_fail_all_retries_blocks_and_files_inbox(project_sprint, unlimited_budget, monkeypatch):
    p, s = project_sprint
    t = _add_task(p.id, s.id)

    def disp(agent, prompt, **kw):
        return DispatchOutcome(ok=True, run_id="r",
                               text="FAIL — broken" if agent == "qa" else "attempt", cost_usd=0.0)
    _stub_dispatch(monkeypatch, disp)

    sprint_executor.execute_sprint(s.id, mode="full")
    assert local_store.get_task(t.id)["status"] == "blocked"
    inbox = local_store.list_inbox("open")
    assert any(i["task_id"] == t.id for i in inbox)


def test_killswitch_stops_before_dispatch(project_sprint, unlimited_budget, monkeypatch):
    p, s = project_sprint
    t = _add_task(p.id, s.id)
    killswitch.pause("manual stop")

    called = []
    _stub_dispatch(monkeypatch, lambda *a, **k: called.append(1) or DispatchOutcome(ok=True, run_id="r"))

    res = sprint_executor.execute_sprint(s.id, mode="full")
    assert "paused" in res.stopped_reason
    assert called == []  # nothing dispatched
    assert local_store.get_task(t.id)["status"] == "ready"  # untouched


def test_budget_block_stops_loop(project_sprint, monkeypatch):
    p, s = project_sprint
    _add_task(p.id, s.id)
    monkeypatch.setattr(budget, "budget_for_project", lambda project=None: {"daily_usd": 1.0})
    budget.record_spend(1.0)  # exhaust

    res = sprint_executor.execute_sprint(s.id, mode="full")
    assert "budget" in res.stopped_reason
    assert res.processed == []


def test_no_ready_tasks(project_sprint, unlimited_budget):
    p, s = project_sprint
    _add_task(p.id, s.id, status="backlog")  # not ready
    res = sprint_executor.execute_sprint(s.id, mode="full")
    assert res.stopped_reason == "no ready tasks"


def test_human_assigned_task_blocked_and_inboxed(project_sprint, unlimited_budget, monkeypatch):
    p, s = project_sprint
    t = _add_task(p.id, s.id, assignee="human")
    _stub_dispatch(monkeypatch, lambda *a, **k: DispatchOutcome(ok=True, run_id="r"))
    sprint_executor.execute_sprint(s.id, mode="full")
    assert local_store.get_task(t.id)["status"] == "blocked"
    assert any(i["task_id"] == t.id for i in local_store.list_inbox("open"))


def test_priority_order_high_first(project_sprint, unlimited_budget, monkeypatch):
    p, s = project_sprint
    _add_task(p.id, s.id, title="low one", priority="low")
    _add_task(p.id, s.id, title="high one", priority="high")
    order = []

    def disp(agent, prompt, **kw):
        if agent != "qa":
            order.append("high" if "high one" in prompt else "low")
        return DispatchOutcome(ok=True, run_id="r", text="PASS" if agent == "qa" else "x", cost_usd=0.0)
    _stub_dispatch(monkeypatch, disp)

    sprint_executor.execute_sprint(s.id, mode="full")
    assert order[0] == "high"


def test_dependency_blocks_until_done(project_sprint, unlimited_budget, monkeypatch):
    p, s = project_sprint
    dep = _add_task(p.id, s.id, title="dep", status="ready")
    blocked = _add_task(p.id, s.id, title="dependent", status="ready", depends_on=[dep.id])

    def disp(agent, prompt, **kw):
        return DispatchOutcome(ok=True, run_id="r", text="PASS" if agent == "qa" else "x", cost_usd=0.0)
    _stub_dispatch(monkeypatch, disp)

    # First pass: only dep is ready (dependent's dep isn't done yet at selection time)
    sprint_executor.execute_sprint(s.id, mode="full")
    # Both may complete across iterations since dep finishes then dependent becomes ready
    statuses = {local_store.get_task(dep.id)["status"], local_store.get_task(blocked.id)["status"]}
    assert "done" in statuses


def test_task_limit_cap(project_sprint, unlimited_budget, monkeypatch):
    p, s = project_sprint
    for i in range(5):
        _add_task(p.id, s.id, title=f"t{i}")

    def disp(agent, prompt, **kw):
        return DispatchOutcome(ok=True, run_id="r", text="PASS" if agent == "qa" else "x", cost_usd=0.0)
    _stub_dispatch(monkeypatch, disp)

    res = sprint_executor.execute_sprint(s.id, mode="full", max_tasks=2)
    assert len(res.processed) == 2
    assert "task limit" in res.stopped_reason


def test_dispatch_failure_blocks_task(project_sprint, unlimited_budget, monkeypatch):
    p, s = project_sprint
    t = _add_task(p.id, s.id)
    _stub_dispatch(monkeypatch, lambda agent, prompt, **k:
                   DispatchOutcome(ok=False, run_id="r", error="boom"))
    sprint_executor.execute_sprint(s.id, mode="full")
    assert local_store.get_task(t.id)["status"] == "blocked"


def test_full_resume_cycle(project_sprint, unlimited_budget, monkeypatch):
    """End-to-end: pass 1 blocks + asks; human answers; pass 2 completes,
    with the answer injected into the dev prompt."""
    from agentos.core import ask_human
    p, s = project_sprint
    t = _add_task(p.id, s.id, title="Build checkout",
                  acceptance_criteria="Works with chosen provider")

    # Pass 1: dev dispatch fails so the task blocks + an inbox item is filed.
    _stub_dispatch(monkeypatch, lambda agent, prompt, **k:
                   DispatchOutcome(ok=False, run_id="r1", error="need provider choice"))
    sprint_executor.execute_sprint(s.id, mode="full")
    assert local_store.get_task(t.id)["status"] == "blocked"
    qs = [i for i in local_store.list_inbox("open") if i["task_id"] == t.id]
    assert len(qs) == 1

    # Human answers → task re-readied.
    ask_human.answer_question(qs[0]["id"], "Use Stripe")
    assert local_store.get_task(t.id)["status"] == "ready"

    # Pass 2: dev succeeds and QA passes; verify the answer reached the prompt.
    seen_prompts = []

    def disp2(agent, prompt, **k):
        seen_prompts.append((agent, prompt))
        return DispatchOutcome(ok=True, run_id="r2",
                               text="PASS" if agent == "qa" else "built it", cost_usd=0.0)
    _stub_dispatch(monkeypatch, disp2)
    sprint_executor.execute_sprint(s.id, mode="full")

    assert local_store.get_task(t.id)["status"] == "done"
    dev_prompt = next(p for a, p in seen_prompts if a != "qa")
    assert "Use Stripe" in dev_prompt  # answered_context injected
