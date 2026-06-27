"""ask_human — the mechanism that lets an autonomous agent stop and ask.

When an agent (during sprint execution) needs a decision only the human can
make, it raises AskHuman. The sprint executor catches it, files an inbox item,
marks the task `blocked`, and moves on to other ready tasks. The human answers
via the dashboard or `agentos inbox answer`; the next execute-sprint pass
re-readies the task with the answer available in its context.

This poll-based resume (vs. a live async pause) fits the sprint-loop model and
keeps the orchestrator stateless between passes.
"""

from __future__ import annotations

from agentos.storage import file_store as local_store


class AskHuman(Exception):
    """Raised by an agent/step to request human input. Carries the question."""

    def __init__(self, prompt: str, *, kind: str = "question", options: list | None = None):
        super().__init__(prompt)
        self.prompt = prompt
        self.kind = kind
        self.options = options


def file_question(
    prompt: str,
    *,
    kind: str = "question",
    options: list | None = None,
    from_agent: str | None = None,
    run_id: str | None = None,
    task_id: str | None = None,
    sprint_id: str | None = None,
) -> str:
    """Create an inbox item and return its id."""
    return local_store.create_inbox_item(
        prompt, kind=kind, options=options, from_agent=from_agent,
        run_id=run_id, task_id=task_id, sprint_id=sprint_id,
    )


def answer_question(item_id: str, answer: str, answered_by: str = "human") -> dict:
    """Answer an inbox item and resume its task.

    Answering is not enough on its own — the task that blocked on this question
    must return to 'ready' so the next execute-sprint pass re-dispatches it with
    the answer now available (see answered_context). Without this, an answered
    question would leave the task stuck in 'blocked' forever (bug #1).

    Returns {"resumed_task": <task_id or None>}.
    """
    item = local_store.get_inbox_item(item_id)
    if not item:
        return {"resumed_task": None, "error": "inbox item not found"}
    # item_id may be a short prefix (bug #3) — update by the resolved full id.
    local_store.answer_inbox(item["id"], answer, answered_by)

    task_id = item.get("task_id")
    resumed = None
    if task_id:
        task = local_store.get_task(task_id)
        # Only re-ready if blocked AND no OTHER questions are still open for it.
        if task and task["status"] == "blocked" and not has_open_questions(task_id):
            local_store.update_task_status(task_id, "ready")
            resumed = task_id
    return {"resumed_task": resumed}


def answered_context(task_id: str) -> str:
    """Answered inbox items for a task, formatted for injection into the next
    dispatch so the agent sees the human's decisions. Empty string if none."""
    items = [
        i for i in local_store.list_inbox(status="answered")
        if i.get("task_id") == task_id
    ]
    if not items:
        return ""
    lines = ["\n\n## Human answers to your earlier questions:"]
    for i in items:
        lines.append(f"- Q: {i['prompt']}\n  A: {i['answer']}")
    return "\n".join(lines)


def has_open_questions(task_id: str) -> bool:
    return bool(local_store.open_inbox_for_task(task_id))
