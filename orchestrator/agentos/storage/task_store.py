"""TaskStore — the pluggable persistence layer for the Work layer.

Projects → Sprints → Tasks sit *above* the Execution layer (runs). A project
maps to a repo, a sprint is a time-boxed batch of work, and a task is a unit an
agent (or human) can pick up. Tasks link down to runs via `last_run_id`.

This module defines the dataclasses and the `TaskStore` Protocol. The plan calls
for pluggable backends (Convex / Linear / GitHub later); for now the only
implementation is the local sqlite one in `local_store.py`. Keep all backends
conforming to this Protocol so the rest of the orchestrator stays backend-agnostic.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Project:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    slug: str = ""
    repo_path: str | None = None
    description: str | None = None
    status: str = "active"  # active / paused / archived
    lead_agent: str | None = None
    created_at: str = field(default_factory=_now)


@dataclass
class Sprint:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str = ""
    name: str = ""
    goal: str | None = None
    status: str = "planned"  # planned / active / done / cancelled
    starts_at: str | None = None
    ends_at: str | None = None
    created_at: str = field(default_factory=_now)


@dataclass
class Task:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str = ""
    sprint_id: str | None = None
    title: str = ""
    description: str | None = None
    # backlog / ready / in_progress / blocked / review / done / cancelled
    status: str = "backlog"
    assignee: str | None = None  # agent name or 'human'
    priority: str = "medium"  # high / medium / low
    depends_on: list[str] = field(default_factory=list)  # task ids
    acceptance_criteria: str | None = None
    estimate_minutes: int | None = None
    parent_task_id: str | None = None
    created_by: str = "human"  # human / agent / workflow
    last_run_id: str | None = None
    created_at: str = field(default_factory=_now)


class TaskStore(Protocol):
    """The contract every backend (sqlite/Convex/Linear/GitHub) must satisfy."""

    # --- projects ---
    def create_project(self, project: Project) -> str: ...
    def get_project(self, project_id: str) -> dict | None: ...
    def list_projects(self) -> list[dict]: ...

    # --- sprints ---
    def create_sprint(self, sprint: Sprint) -> str: ...
    def list_sprints(self, project_id: str) -> list[dict]: ...

    # --- tasks ---
    def create_task(self, task: Task) -> str: ...
    def get_task(self, task_id: str) -> dict | None: ...
    def list_tasks(
        self,
        project_id: str | None = None,
        sprint_id: str | None = None,
        status: str | None = None,
    ) -> list[dict]: ...
    def update_task_status(self, task_id: str, status: str, reason: str | None = None) -> None: ...
    def update_task(self, task_id: str, **fields) -> None: ...
    def link_run(self, task_id: str, run_id: str) -> None: ...
    def ready_tasks(self, sprint_id: str) -> list[dict]: ...

    # --- dashboard ---
    def stats(self) -> dict: ...
