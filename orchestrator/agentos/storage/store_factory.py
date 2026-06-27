"""Pick the TaskStore backend for a project (local sqlite or Linear).

Per-project selection lives in config/settings.yaml:

    projects:
      example-shop:
        task_store: linear
        linear_team_id: TEAM_ABC123
      mission-control:
        task_store: convex   # (falls back to local for now)

Default backend is the local sqlite store. The orchestrator calls
`store_for(project_slug)` and stays backend-agnostic — every backend conforms
to the TaskStore Protocol.
"""

from __future__ import annotations

from agentos.core.config import project_settings
from agentos.storage import file_store, local_store


def store_for(project_slug: str | None = None):
    """Return the configured TaskStore for a project (git-backed file store by default)."""
    backend = project_settings(project_slug).get("task_store", "file")
    if backend == "linear":
        from agentos.storage.linear_store import get_store
        return get_store()
    if backend == "local":
        # Legacy sqlite cache — still selectable.
        return local_store
    # 'file'/'git' (the source of truth), 'convex' (mirror), or unknown → file store.
    return file_store


def backend_name(project_slug: str | None = None) -> str:
    return project_settings(project_slug).get("task_store", "file")
