"""Phase 2 — memory_context read path: tiers, per-tier budget, relevance, stripping."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentos.core import config
from agentos.core import memory_context as mc


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


def _write(d: Path, name: str, body: str, fm: str = "tier: x") -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(f"---\n{fm}\n---\n{body}\n", encoding="utf-8")


def test_empty_root_returns_empty(tmp_path):
    assert mc.build_context("developer", "example-shop", "anything", root=tmp_path) == ""


def test_global_only_when_no_project(tmp_path):
    _write(tmp_path / "global" / "memory", "rules.md", "Always use absolute paths.")
    out = mc.build_context("developer", None, "paths", root=tmp_path)
    assert "Always use absolute paths." in out
    assert "---" not in out  # frontmatter stripped
    assert "### global" in out


def test_all_tiers_layered_in_order(tmp_path):
    _write(tmp_path / "global" / "memory", "g.md", "GLOBALFACT")
    _write(tmp_path / "workspaces" / "business" / "memory", "w.md", "BIZFACT")
    _write(tmp_path / "projects" / "example-shop" / "memory", "p.md", "SHOPFACT")
    _write(tmp_path / "memory" / "per-agent" / "developer", "a.md", "DEVFACT")

    out = mc.build_context("developer", "example-shop", "", root=tmp_path)
    for needle in ("GLOBALFACT", "BIZFACT", "SHOPFACT", "DEVFACT"):
        assert needle in out
    # broad → narrow ordering (most specific last)
    assert (
        out.index("GLOBALFACT")
        < out.index("BIZFACT")
        < out.index("SHOPFACT")
        < out.index("DEVFACT")
    )


def test_unknown_project_skips_workspace_and_project_tiers(tmp_path):
    _write(tmp_path / "global" / "memory", "g.md", "GLOBALFACT")
    _write(tmp_path / "workspaces" / "business" / "memory", "w.md", "BIZFACT")
    out = mc.build_context("developer", "no-such-project", "", root=tmp_path)
    assert "GLOBALFACT" in out
    assert "BIZFACT" not in out  # workspace unresolved → not loaded


def test_index_md_is_skipped(tmp_path):
    gm = tmp_path / "global" / "memory"
    _write(gm, "index.md", "TABLE OF CONTENTS")
    _write(gm, "real.md", "REALFACT")
    out = mc.build_context("developer", None, "", root=tmp_path)
    assert "REALFACT" in out
    assert "TABLE OF CONTENTS" not in out


def test_per_tier_budget_isolation(tmp_path):
    # A huge global fact must not crowd out the project tier (budgets are per-tier).
    _write(tmp_path / "global" / "memory", "big.md", "G" * 5000)
    _write(tmp_path / "projects" / "example-shop" / "memory", "p.md", "PROJECTFACT")
    out = mc.build_context("developer", "example-shop", "", root=tmp_path)
    assert "PROJECTFACT" in out


def test_relevance_drops_irrelevant_when_task_present(tmp_path):
    gm = tmp_path / "global" / "memory"
    _write(gm, "a.md", "Deployment uses firebase and the regression suite.")
    _write(gm, "b.md", "Coffee preferences and lunch schedule notes.")
    out = mc.build_context("developer", None, "how do I deploy with firebase", root=tmp_path)
    assert "firebase" in out
    assert "Coffee preferences" not in out  # zero keyword overlap, dropped
