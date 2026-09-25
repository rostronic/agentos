"""execute-sprint — the autonomous loop.

Picks ready tasks, dispatches the assigned agent, runs QA, advances task status,
and stops to ask the human (via inbox) when blocked. Bounded by the kill switch,
budgets, task-count limits, and QA-retry limits. Approval mode controls how far
tasks auto-advance.

This is poll-based and stateless between passes: each call processes the
currently-ready tasks and returns. Re-running picks up where it left off (e.g.
after a human answers an inbox question, or a dependency completes).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agentos.core import (
    ask_human,
    budget,
    config,
    killswitch,
    limits,
    methodology,
    router,
    run_store,
    worktree,
)
from agentos.core.config import project_settings
from agentos.notify import notifier
from agentos.storage import file_store as local_store

# Approval modes: how far the executor auto-advances tasks.
#   manual → dispatch + QA, but stop at 'review' (human does review→done)
#   semi   → same as manual for the review gate (human still approves done)
#   full   → QA pass auto-advances to 'done'
_AUTO_DONE_MODES = {"full"}


@dataclass
class TaskOutcome:
    task_id: str
    title: str
    final_status: str
    agent: str | None = None
    run_id: str | None = None
    qa_passed: bool | None = None
    note: str = ""


@dataclass
class SprintResult:
    ok: bool
    sprint_id: str
    mode: str
    processed: list[TaskOutcome] = field(default_factory=list)
    stopped_reason: str = ""
    total_cost_usd: float = 0.0


def _default_approval_mode() -> str:
    """Team default approval mode. The 2-gate model sets this to 'full' in
    settings (autonomous between plan-approval and prod-deploy); falls back to
    'manual' when unset."""
    return str(config.settings().get("orchestrator", {}).get("default_approval_mode", "manual"))


def _approval_mode(project_id: str | None) -> str:
    if not project_id:
        return _default_approval_mode()
    proj = local_store.get_project(project_id)
    slug = proj.get("slug") if proj else None
    return project_settings(slug).get("approval_mode") or _default_approval_mode()


def _task_prompt(task: dict, extra: str = "") -> str:
    parts = [
        "You are completing this task. Do the work and report concisely what you did.",
        f"\nTitle: {task['title']}",
    ]
    if task.get("description"):
        parts.append(f"Description: {task['description']}")
    if task.get("acceptance_criteria"):
        parts.append(f"Acceptance criteria: {task['acceptance_criteria']}")
    if extra:
        parts.append(extra)
    return "\n".join(parts)


def _qa_prompt(task: dict, work: str) -> str:
    return (
        "Review the work below against the task's acceptance criteria. "
        "Begin your reply with exactly 'PASS' or 'FAIL', then your reasoning.\n\n"
        f"Title: {task['title']}\n"
        f"Acceptance criteria: {task.get('acceptance_criteria') or '(none specified)'}\n\n"
        f"Work done:\n{work}"
    )


def _qa_verdict(qa_text: str) -> bool:
    """True if QA passed. Looks at the first non-empty line for PASS/FAIL."""
    for line in qa_text.strip().splitlines():
        s = line.strip().upper()
        if s.startswith("PASS"):
            return True
        if s.startswith("FAIL"):
            return False
    # Fallback: pass only if PASS appears and FAIL doesn't
    up = qa_text.upper()
    return "PASS" in up and "FAIL" not in up


def _run_stage(stage: methodology.Stage, task: dict, project_slug: str | None,
               workdir, prior: str = ""):
    """Dispatch one methodology Stage (a pre- or post-implementation agent step)."""
    instr = stage.instruction
    if prior:
        instr = f"{instr}\n\nContext from the prior step:\n{prior}"
    return router.dispatch(
        stage.role, _task_prompt(task, instr), project=project_slug,
        triggered_by="sprint", task_id=task["id"],
        workdir=str(workdir) if workdir else None,
    )


def _process_task(task: dict, mode: str, project_slug: str | None, repo_path: Path | None,
                  sprint_id: str | None, strat: methodology.Methodology) -> tuple[TaskOutcome, float]:
    """Run one task through the methodology's pipeline, returning its outcome and cost.

    Pipeline: [pre-implementation stages] → implement → [QA gate] → [post-QA gate
    stages]. `agile` (no pre/post stages, QA on) reproduces the original dev→QA flow.
    """
    agent = task.get("assignee") or "developer"
    if agent == "human":
        # Human-assigned tasks aren't auto-dispatched; surface to inbox.
        ask_human.file_question(
            f"Task '{task['title']}' is assigned to a human. Please complete it.",
            kind="approval", task_id=task["id"], sprint_id=sprint_id,
        )
        local_store.update_task_status(task["id"], "blocked", reason="assigned to human")
        return TaskOutcome(task["id"], task["title"], "blocked", note="human-assigned"), 0.0

    # Projects that live INSIDE the agentos repo (e.g. personal projects at
    # workspaces/personal/<slug>) get a worktree of the AGENTOS repo itself —
    # that IS their git root. The worktree is created against an explicit
    # config.AGENTOS_ROOT (never git-root discovery from the session CWD, which
    # once rooted such worktrees in the wrong repo).
    #
    # SECURITY: these tasks previously ran in place with workdir == the raw
    # AGENTOS_ROOT — with dispatch_permission_mode: bypassPermissions that was
    # an unsandboxed shell over the live ~/agentos checkout for every
    # task-description-driven agent (finding #1a,
    # docs/reviews/2026-07-01-fable-code-review.md). The workdir must NEVER be
    # the raw repo root: worktree first, project subdir as the fallback.
    in_agentos = False
    project_rel = None
    if repo_path:
        try:
            project_rel = repo_path.resolve().relative_to(config.AGENTOS_ROOT.resolve())
            in_agentos = True
        except ValueError:
            in_agentos = False

    wt = None
    # Every agent that might produce files needs a workdir into the repo, not just
    # developer/qa — researcher/analyst/scribe/planner write docs too. That means
    # a worktree for ALL agents. (Was dev/qa-only → analyst/scribe deliverables had
    # nowhere to land, 2026-06-12.)
    if repo_path:
        wt = worktree.create_worktree(
            config.AGENTOS_ROOT if in_agentos else repo_path,
            project_slug or "project", task["id"],
        )
    if wt:
        workdir = wt
    elif repo_path and in_agentos:
        # Worktree creation failed — confine the agent to the project's own
        # subdir. Never fall back to the raw repo root.
        workdir = repo_path
    else:
        workdir = None

    local_store.update_task_status(task["id"], "in_progress")
    cost = 0.0
    extra = ask_human.answered_context(task["id"])
    if wt and in_agentos:
        # The worktree root mirrors the agentos repo root, so .claude/settings.json
        # (the command allowlist that permits git add/commit) still resolves from
        # the session's cwd (vehicles sprints, 2026-06-12).
        extra += (f"\n\nWork in this directory: {wt} (an isolated worktree of the agentos "
                  f"repo). This task's project lives at {wt / project_rel} — keep ALL file "
                  f"changes inside that subdirectory, and commit your work "
                  f"(git add/commit from the worktree root {wt}).")
    elif wt:
        extra += f"\n\nWork in this directory: {wt}"
    elif workdir:
        extra += (f"\n\nWork in this directory: {workdir} — keep ALL file changes "
                  f"inside it.")

    # Pre-implementation stages (e.g. XP/TDD: QA writes the failing tests first).
    for stage in strat.pre_implementation_stages(task):
        pre = _run_stage(stage, task, project_slug, workdir)
        cost += pre.cost_usd
        if pre.ok and pre.text:
            extra += (f"\n\nA prior '{stage.kind}' step by {stage.role} produced:\n{pre.text}\n"
                      "Build on it — e.g. implement until those tests pass.")

    outcome = router.dispatch(
        agent, _task_prompt(task, extra), project=project_slug,
        triggered_by="sprint", task_id=task["id"], workdir=str(workdir) if workdir else None,
    )
    cost += outcome.cost_usd

    if outcome.blocked_reason:
        local_store.update_task_status(task["id"], "blocked", reason=outcome.error)
        ask_human.file_question(
            f"Task '{task['title']}' blocked: {outcome.error}",
            kind="decision", from_agent=agent, run_id=outcome.run_id,
            task_id=task["id"], sprint_id=sprint_id,
        )
        return TaskOutcome(task["id"], task["title"], "blocked", agent, outcome.run_id,
                           note=outcome.blocked_reason), cost

    if not outcome.ok:
        local_store.update_task_status(task["id"], "blocked", reason=outcome.error)
        ask_human.file_question(
            f"Task '{task['title']}' failed: {outcome.error}",
            kind="decision", from_agent=agent, run_id=outcome.run_id,
            task_id=task["id"], sprint_id=sprint_id,
        )
        return TaskOutcome(task["id"], task["title"], "blocked", agent, outcome.run_id,
                           note="dispatch failed"), cost

    local_store.link_run(task["id"], outcome.run_id)

    # QA gate — re-dispatch dev up to max_qa_retries on QA failure. Skippable per methodology.
    work = outcome.text
    if strat.needs_qa:
        max_retries = limits.max_qa_retries(project_slug)
        qa_passed = None
        for attempt in range(max_retries + 1):
            qa_out = router.dispatch(
                "qa", _qa_prompt(task, work), project=project_slug,
                triggered_by="sprint", task_id=task["id"], workdir=str(workdir) if workdir else None,
            )
            cost += qa_out.cost_usd
            if not qa_out.ok:
                break  # QA itself failed to run; treat as inconclusive
            qa_passed = _qa_verdict(qa_out.text)
            if qa_passed or attempt == max_retries:
                break
            # QA failed and retries remain — re-dispatch dev with the QA feedback.
            redo = router.dispatch(
                agent, _task_prompt(task, extra + f"\n\nQA feedback to address:\n{qa_out.text}"),
                project=project_slug, triggered_by="sprint", task_id=task["id"],
                workdir=str(workdir) if workdir else None,
            )
            cost += redo.cost_usd
            if redo.ok:
                work = redo.text
                local_store.link_run(task["id"], redo.run_id)

        if not qa_passed:
            local_store.update_task_status(task["id"], "blocked", reason="QA did not pass")
            ask_human.file_question(
                f"Task '{task['title']}' failed QA after {max_retries} retries. Needs review.",
                kind="decision", from_agent="qa", task_id=task["id"], sprint_id=sprint_id,
            )
            return TaskOutcome(task["id"], task["title"], "blocked", agent, outcome.run_id, False,
                               note="QA failed"), cost

    # Post-QA gate stages (e.g. XP critic review; DevOps CI/smoke build-verify).
    for stage in strat.post_qa_stages(task):
        gate = _run_stage(stage, task, project_slug, workdir, prior=work)
        cost += gate.cost_usd
        if not gate.ok or not _qa_verdict(gate.text):
            # stage.kind is literally "gate" for gate stages — don't render
            # "developer gate gate failed"; label with the kind only when it
            # adds information (e.g. "critic review gate failed").
            gate_label = "gate" if stage.kind == "gate" else f"{stage.kind} gate"
            local_store.update_task_status(
                task["id"], "blocked", reason=f"{stage.role} {gate_label} failed")
            ask_human.file_question(
                f"Task '{task['title']}' failed the {stage.role} {gate_label}. Needs review.",
                kind="decision", from_agent=stage.role, task_id=task["id"], sprint_id=sprint_id,
            )
            return TaskOutcome(task["id"], task["title"], "blocked", agent, outcome.run_id, True,
                               note=f"{gate_label} failed"), cost

    final = "done" if mode in _AUTO_DONE_MODES else "review"
    local_store.update_task_status(task["id"], final)
    return TaskOutcome(task["id"], task["title"], final, agent, outcome.run_id, True), cost


def _repo_path(project: dict | None) -> Path | None:
    if project and project.get("repo_path"):
        rp = Path(project["repo_path"]).expanduser()
        if not rp.is_absolute():
            rp = config.AGENTOS_ROOT / rp
        return rp
    return None


def _project_by_slug(slug: str | None) -> dict | None:
    if not slug:
        return None
    for p in local_store.list_projects():
        if p.get("slug") == slug:
            return p
    return None


def _run_loop(*, get_ready, strat, mode, project_slug, repo_path, sprint_id, cap, parent, result):
    """Shared pull→process loop for both sprint and continuous-backlog cadence.

    `get_ready` is a callable returning the currently-ready tasks; the methodology
    chooses which to pick next via `order_ready_tasks`.
    """
    processed = 0
    while processed < cap:
        if killswitch.is_paused():
            result.stopped_reason = f"paused: {killswitch.pause_reason()}"
            break
        block = budget.check_dispatch(project=project_slug)
        if block:
            result.stopped_reason = f"budget: {block.detail}"
            break

        ready = get_ready()
        if not ready:
            result.stopped_reason = "no ready tasks"
            break

        task = strat.order_ready_tasks(ready)[0]
        run_store.append_event(parent.id, "task_start",
                               {"task_id": task["id"], "title": task["title"]})
        outcome, cost = _process_task(task, mode, project_slug, repo_path, sprint_id, strat)
        result.processed.append(outcome)
        result.total_cost_usd += cost
        processed += 1
        run_store.append_event(parent.id, "task_done",
                               {"task_id": task["id"], "status": outcome.final_status})

    if processed >= cap and not result.stopped_reason:
        result.stopped_reason = f"hit task limit ({cap})"


def _finalize(parent, result: SprintResult, *, label: str) -> SprintResult:
    run_store.update_run(
        parent.id, status="done", ended_at=run_store._now(),
        cost_usd=result.total_cost_usd,
        output=f"Processed {len(result.processed)} tasks. {result.stopped_reason}",
    )
    run_store.append_event(parent.id, "sprint_done",
                           {"processed": len(result.processed), "reason": result.stopped_reason})
    # Notify on completion, and specifically if anything blocked needing the human.
    try:
        notifier.sprint_done(label, len(result.processed), result.stopped_reason)
        blocked = [o for o in result.processed if o.final_status == "blocked"]
        if blocked:
            notifier.notify("agent_blocked", "Team run needs you",
                            f"{len(blocked)} task(s) blocked — check the inbox.")
    except Exception:  # noqa: BLE001, S110
        pass
    return result


def execute_sprint(sprint_id: str, *, mode: str | None = None,
                   max_tasks: int | None = None) -> SprintResult:
    """Run ready tasks in a sprint (sprint cadence) until none remain, the kill
    switch trips, the budget is exhausted, or the task-count limit is hit."""
    # Resolve the sprint's project for settings/budget scoping.
    tasks_in_sprint = local_store.list_tasks(sprint_id=sprint_id)
    project_id = tasks_in_sprint[0]["project_id"] if tasks_in_sprint else None
    project = local_store.get_project(project_id) if project_id else None
    project_slug = project.get("slug") if project else None
    repo_path = _repo_path(project)

    resolved_mode = mode or _approval_mode(project_id)
    strat = methodology.get(config.methodology_for(project_slug))
    cap = limits.max_tasks_per_run(project_slug, override=max_tasks)

    result = SprintResult(ok=True, sprint_id=sprint_id, mode=resolved_mode)
    parent = run_store.Run(
        workflow_name="execute-sprint", status="running",
        inputs={"sprint_id": sprint_id, "mode": resolved_mode, "methodology": strat.name},
        triggered_by="sprint", project=project_slug,
    )
    run_store.create_run(parent)
    _run_loop(get_ready=lambda: local_store.ready_tasks(sprint_id), strat=strat,
              mode=resolved_mode, project_slug=project_slug, repo_path=repo_path,
              sprint_id=sprint_id, cap=cap, parent=parent, result=result)
    return _finalize(parent, result, label=sprint_id)


def run_backlog(project_slug: str, *, mode: str | None = None,
                max_tasks: int | None = None) -> SprintResult:
    """Continuous-backlog cadence: repeatedly pull the next ready backlog task
    (one with no sprint) for a project and run it through the project's
    methodology, until none remain / kill switch / budget / task-count limit."""
    project = _project_by_slug(project_slug)
    project_id = project.get("id") if project else None
    repo_path = _repo_path(project)

    resolved_mode = mode or _approval_mode(project_id)
    strat = methodology.get(config.methodology_for(project_slug))
    cap = limits.max_tasks_per_run(project_slug, override=max_tasks)

    label = f"backlog:{project_slug}"
    result = SprintResult(ok=True, sprint_id=label, mode=resolved_mode)
    parent = run_store.Run(
        workflow_name="run-backlog", status="running",
        inputs={"project": project_slug, "mode": resolved_mode, "methodology": strat.name},
        triggered_by="sprint", project=project_slug,
    )
    run_store.create_run(parent)
    _run_loop(get_ready=lambda: local_store.ready_tasks(project_id=project_id), strat=strat,
              mode=resolved_mode, project_slug=project_slug, repo_path=repo_path,
              sprint_id=None, cap=cap, parent=parent, result=result)
    return _finalize(parent, result, label=label)
