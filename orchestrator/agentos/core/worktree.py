"""Git worktrees for isolated, parallel task work.

When the executor dispatches a code task, it creates a fresh git worktree so
multiple tasks can run without trampling each other's files. Each worktree is
on its own branch `agent/<task-id>`. Cleaned up after merge or when stale.

Worktrees live under ~/agentos/worktrees/<project-slug>/<task-id>/.
If the project isn't a git repo, worktree creation is skipped gracefully
(returns None) — the task still runs, just without isolation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from agentos.core.config import AGENTOS_ROOT

WORKTREES_DIR = AGENTOS_ROOT / "worktrees"


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60
    )


def is_git_repo(path: Path) -> bool:
    if not path.exists():
        return False
    r = _run_git(["rev-parse", "--is-inside-work-tree"], path)
    return r.returncode == 0 and r.stdout.strip() == "true"


def create_worktree(repo_path: Path, project_slug: str, task_id: str) -> Path | None:
    """Create a worktree on branch agent/<task-id>. Returns its path, or None
    if the repo isn't git (task proceeds without isolation)."""
    if not is_git_repo(repo_path):
        return None

    dest = WORKTREES_DIR / project_slug / task_id
    if dest.exists():
        return dest  # already created (idempotent)

    dest.parent.mkdir(parents=True, exist_ok=True)
    branch = f"agent/{task_id}"
    r = _run_git(["worktree", "add", "-b", branch, str(dest)], repo_path)
    if r.returncode != 0:
        # branch may already exist — try without -b
        r2 = _run_git(["worktree", "add", str(dest), branch], repo_path)
        if r2.returncode != 0:
            return None
    return dest


def remove_worktree(repo_path: Path, project_slug: str, task_id: str) -> bool:
    """Remove a task's worktree. Returns True if removed or already gone."""
    dest = WORKTREES_DIR / project_slug / task_id
    if not dest.exists():
        return True
    if not is_git_repo(repo_path):
        return False
    r = _run_git(["worktree", "remove", "--force", str(dest)], repo_path)
    return r.returncode == 0


def list_worktrees(repo_path: Path) -> list[str]:
    if not is_git_repo(repo_path):
        return []
    r = _run_git(["worktree", "list", "--porcelain"], repo_path)
    return [
        line.split(" ", 1)[1]
        for line in r.stdout.splitlines()
        if line.startswith("worktree ")
    ]
