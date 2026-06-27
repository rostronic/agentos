"""Phase 6 — git worktree manager (against real temp git repos)."""

from __future__ import annotations

import subprocess

import pytest

from agentos.core import worktree


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.co"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "README.md").write_text("hi")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    # isolate worktrees dir
    monkeypatch.setattr(worktree, "WORKTREES_DIR", tmp_path / "worktrees")
    return repo


def test_is_git_repo_true(git_repo):
    assert worktree.is_git_repo(git_repo)


def test_is_git_repo_false(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert not worktree.is_git_repo(plain)


def test_create_worktree_makes_branch_and_dir(git_repo):
    wt = worktree.create_worktree(git_repo, "proj", "task-123")
    assert wt is not None
    assert wt.exists()
    assert (wt / "README.md").exists()  # worktree has repo contents
    branches = subprocess.run(
        ["git", "branch", "--list", "agent/task-123"],
        cwd=git_repo, capture_output=True, text=True,
    ).stdout
    assert "agent/task-123" in branches


def test_create_worktree_idempotent(git_repo):
    wt1 = worktree.create_worktree(git_repo, "proj", "task-1")
    wt2 = worktree.create_worktree(git_repo, "proj", "task-1")
    assert wt1 == wt2


def test_create_worktree_non_git_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(worktree, "WORKTREES_DIR", tmp_path / "wt")
    plain = tmp_path / "plain"
    plain.mkdir()
    assert worktree.create_worktree(plain, "proj", "t1") is None


def test_parallel_tasks_get_separate_worktrees(git_repo):
    wt_a = worktree.create_worktree(git_repo, "proj", "task-a")
    wt_b = worktree.create_worktree(git_repo, "proj", "task-b")
    assert wt_a != wt_b
    assert wt_a.exists() and wt_b.exists()


def test_remove_worktree(git_repo):
    wt = worktree.create_worktree(git_repo, "proj", "task-x")
    assert wt.exists()
    assert worktree.remove_worktree(git_repo, "proj", "task-x")
    assert not wt.exists()


def test_remove_nonexistent_is_safe(git_repo):
    assert worktree.remove_worktree(git_repo, "proj", "never-made")
