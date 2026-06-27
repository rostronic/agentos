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

from agentos.core import ask_human, budget, config, killswitch, limits, router, run_store, worktree
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


def _approval_mode(project_id: str | None) -> str:
    if not project_id:
        return "manual"
    proj = local_store.get_project(project_id)
    slug = proj.get("slug") if proj else None
    return project_settings(slug).get("approval_mode", "manual")


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


def _process_task(task: dict, mode: str, project_slug: str | None, repo_path: Path | None,
                  sprint_id: str) -> tuple[TaskOutcome, float]:
    """Dispatch one task through dev → QA, returning its outcome and cost."""
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
    # workspaces/personal/<slug>) must NOT be worktree-isolated: a worktree of
    # the orchestrator repo to edit one subdir is wrong, and git-root discovery
    # from the session CWD has rooted such worktrees in the wrong repo entirely
    # (a past bug rooted such worktrees on the wrong repo). Run those in place instead.
    in_agentos = False
    if repo_path:
        try:
            repo_path.resolve().relative_to(config.AGENTOS_ROOT.resolve())
            in_agentos = True
        except ValueError:
            in_agentos = False

    wt = None
    # Every agent that might produce files needs a workdir into the repo, not just
    # developer/qa — researcher/analyst/scribe/planner write docs too. On split
    # (~/dev) repos that means a worktree for ALL agents; in-agentos projects run
    # in place (handled below). (Was dev/qa-only → analyst/scribe deliverables had
    # nowhere to land, 2026-06-12.)
    if repo_path and not in_agentos:
        wt = worktree.create_worktree(repo_path, project_slug or "project", task["id"])
    # In-agentos projects run from the REPO ROOT, not the project subdir:
    # Claude Code resolves .claude/settings.json (the command allowlist that
    # permits git add/commit) from the session's cwd — launching in a subdir
    # leaves agents unable to commit (vehicles sprints, 2026-06-12).
    workdir = wt or (config.AGENTOS_ROOT if (repo_path and in_agentos) else None)

    local_store.update_task_status(task["id"], "in_progress")
    cost = 0.0
    extra = ask_human.answered_context(task["id"])
    if wt:
        extra += f"\n\nWork in this directory: {wt}"
    elif workdir:
        extra += (f"\n\nYou are in the agentos repo root ({workdir}). This task's project "
                  f"lives at {repo_path} — keep ALL file changes inside that directory, "
                  f"and commit your work there (git add/commit from the repo root).")

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

    # QA loop — re-dispatch dev up to max_qa_retries on QA failure.
    max_retries = limits.max_qa_retries(project_slug)
    work = outcome.text
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
            project=project_slug, triggered_by="sprint", task_id=task["id"], workdir=str(workdir) if workdir else None,
        )
        cost += redo.cost_usd
        if redo.ok:
            work = redo.text
            local_store.link_run(task["id"], redo.run_id)

    if qa_passed:
        final = "done" if mode in _AUTO_DONE_MODES else "review"
        local_store.update_task_status(task["id"], final)
        return TaskOutcome(task["id"], task["title"], final, agent, outcome.run_id, True), cost

    local_store.update_task_status(task["id"], "blocked", reason="QA did not pass")
    ask_human.file_question(
        f"Task '{task['title']}' failed QA after {max_retries} retries. Needs review.",
        kind="decision", from_agent="qa", task_id=task["id"], sprint_id=sprint_id,
    )
    return TaskOutcome(task["id"], task["title"], "blocked", agent, outcome.run_id, False,
                       note="QA failed"), cost


def execute_sprint(sprint_id: str, *, mode: str | None = None,
                   max_tasks: int | None = None) -> SprintResult:
    """Run ready tasks in a sprint until none remain, the kill switch trips, the
    budget is exhausted, or the task-count limit is hit."""
    # Resolve the sprint's project for settings/budget scoping.
    # (list_sprints needs a project_id, so find via tasks if needed.)
    tasks_in_sprint = local_store.list_tasks(sprint_id=sprint_id)
    project_id = tasks_in_sprint[0]["project_id"] if tasks_in_sprint else None
    project = local_store.get_project(project_id) if project_id else None
    project_slug = project.get("slug") if project else None
    repo_path = None
    if project and project.get("repo_path"):
        rp = Path(project["repo_path"]).expanduser()
        if not rp.is_absolute():
            rp = config.AGENTOS_ROOT / rp
        repo_path = rp

    resolved_mode = mode or _approval_mode(project_id)
    cap = limits.max_tasks_per_run(project_slug, override=max_tasks)

    result = SprintResult(ok=True, sprint_id=sprint_id, mode=resolved_mode)
    parent = run_store.Run(
        workflow_name="execute-sprint", status="running",
        inputs={"sprint_id": sprint_id, "mode": resolved_mode},
        triggered_by="sprint", project=project_slug,
    )
    run_store.create_run(parent)

    processed = 0
    while processed < cap:
        if killswitch.is_paused():
            result.stopped_reason = f"paused: {killswitch.pause_reason()}"
            break
        block = budget.check_dispatch(project=project_slug)
        if block:
            result.stopped_reason = f"budget: {block.detail}"
            break

        ready = local_store.ready_tasks(sprint_id)
        if not ready:
            result.stopped_reason = "no ready tasks"
            break

        # Highest priority first (high > medium > low), then oldest.
        order = {"high": 0, "medium": 1, "low": 2}
        ready.sort(key=lambda t: (order.get(t.get("priority"), 1), t.get("created_at", "")))
        task = ready[0]

        run_store.append_event(parent.id, "task_start",
                               {"task_id": task["id"], "title": task["title"]})
        outcome, cost = _process_task(task, resolved_mode, project_slug, repo_path, sprint_id)
        result.processed.append(outcome)
        result.total_cost_usd += cost
        processed += 1
        run_store.append_event(parent.id, "task_done",
                               {"task_id": task["id"], "status": outcome.final_status})

    if processed >= cap and not result.stopped_reason:
        result.stopped_reason = f"hit task limit ({cap})"

    run_store.update_run(
        parent.id, status="done", ended_at=run_store._now(),
        cost_usd=result.total_cost_usd,
        output=f"Processed {len(result.processed)} tasks. {result.stopped_reason}",
    )
    run_store.append_event(parent.id, "sprint_done",
                           {"processed": len(result.processed), "reason": result.stopped_reason})
    # Notify on completion, and specifically if anything blocked needing the human.
    try:
        notifier.sprint_done(sprint_id, len(result.processed), result.stopped_reason)
        blocked = [o for o in result.processed if o.final_status == "blocked"]
        if blocked:
            notifier.notify("agent_blocked", "Sprint needs you",
                            f"{len(blocked)} task(s) blocked — check the inbox.")
    except Exception:  # noqa: BLE001 — notifications must never break a run
        pass
    return result
