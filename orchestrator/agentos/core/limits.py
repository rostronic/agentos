"""Resource limits — caps the executor enforces per task and per sprint.

Complements budget.py (which caps dollars). These cap iteration counts and
task counts so an autonomous sprint can't loop forever or chew through a whole
backlog unattended.
"""

from __future__ import annotations

from agentos.core.config import budget_for_project

# Hard ceiling on tasks dispatched in a single execute-sprint invocation,
# regardless of config, as a runaway backstop.
ABSOLUTE_MAX_TASKS_PER_RUN = 100


def max_tasks_per_run(project: str | None = None, override: int | None = None) -> int:
    """How many tasks one execute-sprint call may dispatch before stopping."""
    if override is not None:
        return min(override, ABSOLUTE_MAX_TASKS_PER_RUN)
    configured = budget_for_project(project).get("max_tasks_per_sprint_run", 20)
    return min(int(configured), ABSOLUTE_MAX_TASKS_PER_RUN)


def max_qa_retries(project: str | None = None) -> int:
    """How many times a task may bounce dev→QA before being marked blocked."""
    return int(budget_for_project(project).get("max_qa_retries", 2))
