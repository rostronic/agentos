"""Phase 3 — memory_store write path: tier resolution, promote/dedupe, inbox capture."""

from __future__ import annotations

import pytest

from agentos.core import config
from agentos.core import memory_store as ms


@pytest.fixture(autouse=True)
def _registry(monkeypatch):
    """Resolve the generic 'example-shop' slug to the business workspace +
    projects/example-shop memory path, decoupled from the instance's private
    config/projects.yaml."""
    monkeypatch.setattr(
        config, "projects",
        lambda: {"example-shop": {
            "workspace": "business", "memory_path": "projects/example-shop",
        }},
    )
    yield


def test_tier_dir_resolution(tmp_path):
    assert ms.tier_dir("global", root=tmp_path) == tmp_path / "global" / "memory"
    assert (
        ms.tier_dir("workspace", project="example-shop", root=tmp_path)
        == tmp_path / "workspaces" / "business" / "memory"
    )
    assert (
        ms.tier_dir("project", project="example-shop", root=tmp_path)
        == tmp_path / "projects" / "example-shop" / "memory"
    )
    assert (
        ms.tier_dir("per-agent", agent="developer", root=tmp_path)
        == tmp_path / "memory" / "per-agent" / "developer"
    )


def test_promote_creates_file(tmp_path):
    p = ms.promote("global", "Deploy Rule", "Always ask before prod.", root=tmp_path)
    assert p == tmp_path / "global" / "memory" / "deploy-rule.md"
    assert "Always ask before prod." in p.read_text()


def test_promote_dedupes_identical_content(tmp_path):
    ms.promote("global", "rule", "Same fact.", root=tmp_path)
    p = ms.promote("global", "rule", "Same fact.", root=tmp_path)
    # identical content must not be duplicated
    assert p.read_text().count("Same fact.") == 1


def test_promote_appends_new_content(tmp_path):
    ms.promote("global", "rule", "First fact.", root=tmp_path)
    p = ms.promote("global", "rule", "Second fact.", root=tmp_path)
    text = p.read_text()
    assert "First fact." in text and "Second fact." in text


def test_append_unique_returns_false_on_duplicate(tmp_path):
    p = ms.promote("global", "r", "hello world", root=tmp_path)
    assert ms.append_unique(p, "hello world") is False
    assert ms.append_unique(p, "brand new line") is True


def test_capture_list_and_clear(tmp_path):
    path = ms.capture(
        "raw run output", ts="2026-06-07T12:00:00", run_id="abc123",
        agent="researcher", project="example-shop", root=tmp_path,
    )
    assert path.exists()
    assert "run_id: abc123" in path.read_text()

    captures = ms.list_captures(root=tmp_path)
    assert path in captures

    ms.clear_capture(path)
    assert not path.exists()
    assert ms.list_captures(root=tmp_path) == []


def test_list_captures_excludes_readme(tmp_path):
    inbox = ms.inbox_dir(tmp_path)
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "README.md").write_text("not a capture")
    ms.capture("x", ts="2026-06-07T00:00:00", run_id="r1", root=tmp_path)
    names = [p.name for p in ms.list_captures(root=tmp_path)]
    assert "README.md" not in names
    assert any(n.endswith("-r1.md") for n in names)
