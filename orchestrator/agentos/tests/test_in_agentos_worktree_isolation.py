"""SECURITY [CRITICAL] — worktree isolation for in-agentos project dispatches.

Batch B fix (docs/reviews/2026-07-01-fable-code-review.md, finding #1, part (a)):
``sprint_executor._process_task`` (orchestrator/agentos/core/sprint_executor.py,
~L138-158) special-cases "in-agentos" projects (repo_path resolves under
``config.AGENTOS_ROOT`` — e.g. workspaces/personal/<slug>) to run with NO
worktree at all: ``workdir = config.AGENTOS_ROOT`` directly. Combined with
``dispatch_permission_mode: bypassPermissions`` (config/settings.yaml:21, which
also grants shell), every one of the ~11 in-agentos projects gets an
unsandboxed shell rooted at the live repo — a task-description-driven agent can
read/write/delete anything in the real ~/agentos checkout, not just its own
project subdir.

Required fix (either is acceptable per the review, tests below are written to
be agnostic between them):
  (a) give in-agentos tasks a real worktree too (e.g. worktree.create_worktree
      called against config.AGENTOS_ROOT itself, since that's the actual git
      repo root for these projects), or
  (b) keep running in place but resolve/guard the workdir so it can never be
      the raw repo root — e.g. chroot the agent's effective workdir to the
      project's own subdir with an explicit realpath containment check.

IMPORTANT — this directly reverses `test_in_agentos_project_runs_in_place_no_worktree`
in test_sprint_executor.py, which currently asserts the vulnerable pre-fix
behavior (workdir == AGENTOS_ROOT, no worktree ever created) as a "regression
guard". That test encodes the bug this ticket fixes and must be updated/removed
by whoever implements the fix — do not treat its current failure-after-fix as a
new regression.

These tests are RED (failing) until the fix lands.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentos.core import sprint_executor
from agentos.core.router import DispatchOutcome
from agentos.storage import file_store as local_store
from agentos.storage.task_store import Project, Sprint, Task


@pytest.fixture
def unlimited_budget(monkeypatch):
    from agentos.core import budget
    import agentos.core.limits as limits_mod
    monkeypatch.setattr(budget, "budget_for_project", lambda project=None: {})
    monkeypatch.setattr(limits_mod, "budget_for_project", lambda project=None: {})


def _add_task(project_id, sprint_id, **kw):
    t = Task(project_id=project_id, sprint_id=sprint_id,
             title=kw.pop("title", "T"), status=kw.pop("status", "ready"),
             assignee=kw.pop("assignee", "developer"), **kw)
    local_store.create_task(t)
    return t


def _stub_dispatch(monkeypatch, fn):
    monkeypatch.setattr(sprint_executor.router, "dispatch", fn)


@pytest.fixture
def in_agentos_git_repo(tmp_path, monkeypatch):
    """A real temp git repo standing in for the ~/agentos checkout, with a
    tracked subdir mimicking workspaces/personal/<slug> — so tests exercise
    real git-worktree behavior rather than mocking it away, and stay agnostic
    about whether the fix is "new worktree" or "chroot + guard"."""
    from agentos.core import config, worktree

    root = tmp_path / "agentos-repo"
    project_dir = root / "workspaces" / "personal" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "README.md").write_text("demo project\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.co"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)

    monkeypatch.setattr(config, "AGENTOS_ROOT", root)
    monkeypatch.setattr(worktree, "WORKTREES_DIR", root / "worktrees")
    return root, project_dir


def _make_in_agentos_task(project_dir):
    p = Project(name="Demo", slug="demo", repo_path=str(project_dir))
    local_store.create_project(p)
    s = Sprint(project_id=p.id, name="S", status="active")
    local_store.create_sprint(s)
    t = _add_task(p.id, s.id, assignee="developer")
    return p, s, t


def test_in_agentos_task_workdir_is_never_the_raw_repo_root(
    in_agentos_git_repo, unlimited_budget, monkeypatch
):
    """The dispatched developer/qa workdir for an in-agentos project must not be
    the literal AGENTOS_ROOT — that is the "unsandboxed shell at the live repo
    root" the finding describes. Accept either an isolated worktree path or a
    workdir confined to the project's own subdir; reject the raw root."""
    from agentos.core import config

    root, project_dir = in_agentos_git_repo
    p, s, t = _make_in_agentos_task(project_dir)

    seen = {}

    def disp(agent, prompt, **kw):
        seen[agent] = kw.get("workdir")
        return DispatchOutcome(ok=True, run_id="r",
                               text="PASS" if agent == "qa" else "done", cost_usd=0.0)
    _stub_dispatch(monkeypatch, disp)

    sprint_executor.execute_sprint(s.id, mode="full")

    assert local_store.get_task(t.id)["status"] == "done"
    for agent, workdir in seen.items():
        assert workdir is not None, f"{agent} dispatch got no workdir at all"
        assert Path(workdir).resolve() != root.resolve(), (
            f"{agent} dispatch ran with workdir == raw AGENTOS_ROOT "
            f"({root}) — unsandboxed shell at the live repo root"
        )


def test_in_agentos_task_gets_worktree_isolation(
    in_agentos_git_repo, unlimited_budget, monkeypatch
):
    """If the chosen fix is "create a worktree too", it must actually call
    worktree.create_worktree (rooted at AGENTOS_ROOT, the real git repo for
    in-agentos projects) rather than silently skipping worktree creation the
    way the current code does. This test is the direct negation of
    test_in_agentos_project_runs_in_place_no_worktree's `pytest.fail` guard."""
    from agentos.core import config

    root, project_dir = in_agentos_git_repo
    p, s, t = _make_in_agentos_task(project_dir)

    calls = []
    real_create_worktree = sprint_executor.worktree.create_worktree

    def spy_create_worktree(repo_path, project_slug, task_id):
        calls.append((repo_path, project_slug, task_id))
        return real_create_worktree(repo_path, project_slug, task_id)

    monkeypatch.setattr(sprint_executor.worktree, "create_worktree", spy_create_worktree)
    _stub_dispatch(monkeypatch, lambda agent, prompt, **kw: DispatchOutcome(
        ok=True, run_id="r", text="PASS" if agent == "qa" else "done", cost_usd=0.0))

    sprint_executor.execute_sprint(s.id, mode="full")

    assert calls, (
        "worktree.create_worktree was never called for an in-agentos project — "
        "the fix must isolate in-agentos tasks too (or replace this expectation "
        "with an equivalent chroot+guard test if that's the chosen approach)."
    )


def test_in_agentos_task_isolated_workdir_reused_across_dev_and_qa(
    in_agentos_git_repo, unlimited_budget, monkeypatch
):
    """Dev and QA dispatches for the same task must share the same isolated
    workdir (so QA reviews the same on-disk state dev produced) — mirrors the
    existing guarantee for split (~/dev) repos."""
    p, s, t = _make_in_agentos_task(in_agentos_git_repo[1])

    seen = {}

    def disp(agent, prompt, **kw):
        seen[agent] = kw.get("workdir")
        return DispatchOutcome(ok=True, run_id="r",
                               text="PASS" if agent == "qa" else "done", cost_usd=0.0)
    _stub_dispatch(monkeypatch, disp)

    sprint_executor.execute_sprint(s.id, mode="full")

    assert seen.get("developer") is not None
    assert seen.get("qa") is not None
    assert seen["developer"] == seen["qa"]


def test_in_agentos_task_workdir_confined_under_project_or_worktrees_dir(
    in_agentos_git_repo, unlimited_budget, monkeypatch
):
    """Whatever isolated path is chosen, it must resolve to somewhere under
    either the project's own subdir or the dedicated worktrees dir — never a
    sibling path that happens to also live under AGENTOS_ROOT but outside both
    (which would indicate a half-applied guard)."""
    from pathlib import Path
    from agentos.core import config, worktree

    root, project_dir = in_agentos_git_repo
    p, s, t = _make_in_agentos_task(project_dir)

    seen = {}

    def disp(agent, prompt, **kw):
        seen[agent] = kw.get("workdir")
        return DispatchOutcome(ok=True, run_id="r",
                               text="PASS" if agent == "qa" else "done", cost_usd=0.0)
    _stub_dispatch(monkeypatch, disp)

    sprint_executor.execute_sprint(s.id, mode="full")

    dev_workdir = Path(seen["developer"]).resolve()
    allowed_roots = [project_dir.resolve(), worktree.WORKTREES_DIR.resolve()]
    assert any(
        dev_workdir == allowed or allowed in dev_workdir.parents
        for allowed in allowed_roots
    ), f"workdir {dev_workdir} is outside both the project subdir and the worktrees dir"
