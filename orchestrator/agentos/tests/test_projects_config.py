"""Phase 1 — project registry (config/projects.yaml) workspace mapping.

Uses a synthetic in-memory registry (monkeypatched onto config.projects) so the
suite is self-contained and carries no instance-specific project names. The shape
asserted here is exactly what a real config/projects.yaml provides.
"""

from __future__ import annotations

import pytest

from agentos.core import config

# A representative registry: two business sites, one personal project, and a
# merged-away slug that survives only as an alias on another entry.
_REGISTRY = {
    "example-shop": {
        "workspace": "business",
        "repo_path": "~/dev/example-shop",
        "memory_path": "projects/example-shop",
        "aliases": ["example-shop", "shop", "start-a-project"],
    },
    "example-news": {
        "workspace": "business",
        "repo_path": "~/dev/example-news",
        "memory_path": "projects/example-news",
        "aliases": ["example-news", "news"],
    },
    "example-blog": {
        "workspace": "business",
        "repo_path": "~/dev/example-blog",
        "memory_path": "projects/example-blog",
        "aliases": ["example-blog", "blog"],
    },
    "home-garage": {
        "workspace": "personal",
        "repo_path": "~/agentos/workspaces/personal/home-garage",
        "memory_path": "workspaces/personal/home-garage",
        "aliases": ["home-garage", "garage"],
    },
}


@pytest.fixture(autouse=True)
def _registry(monkeypatch):
    monkeypatch.setattr(config, "projects", lambda: _REGISTRY)
    yield


def test_business_projects_map_to_business():
    for slug in ("example-shop", "example-news", "example-blog"):
        assert config.workspace_for_project(slug) == "business", slug


def test_personal_project_maps_to_personal():
    assert config.workspace_for_project("home-garage") == "personal"


def test_unknown_project_is_unresolved():
    assert config.workspace_for_project("does-not-exist") is None
    assert config.workspace_for_project(None) is None
    assert config.project_config("does-not-exist") == {}


def test_registry_entries_have_repo_and_memory_paths():
    cfg = config.project_config("example-shop")
    assert cfg["repo_path"] == "~/dev/example-shop"
    assert cfg["memory_path"] == "projects/example-shop"


def test_merged_alias_listed_on_owning_project():
    # A de-registered slug (start-a-project) survives as an alias on the project
    # it was folded into, and must NOT reappear as its own entry.
    cfg = config.project_config("example-shop")
    assert "start-a-project" in cfg.get("aliases", [])
    assert config.project_config("start-a-project") == {}
