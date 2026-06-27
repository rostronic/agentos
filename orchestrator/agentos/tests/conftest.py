"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_runtime(tmp_path, monkeypatch):
    """Point runtime state (sqlite, spend file) at a temp dir per test."""
    import agentos.core.budget as budget_mod
    import agentos.core.killswitch as killswitch_mod
    import agentos.core.run_store as run_store_mod
    import agentos.storage.file_store as file_store_mod
    import agentos.storage.local_store as local_store_mod

    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(run_store_mod, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(run_store_mod, "DB_PATH", runtime / "runs.sqlite")
    monkeypatch.setattr(budget_mod, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(budget_mod, "SPEND_FILE", runtime / "daily_spend.json")
    monkeypatch.setattr(local_store_mod, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(local_store_mod, "DB_PATH", runtime / "work.sqlite")
    monkeypatch.setattr(file_store_mod, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(killswitch_mod, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(killswitch_mod, "PAUSE_FILE", runtime / "PAUSED")
    yield


# Generic project registry + cost-source mappings used by the cost/mapping tests.
# Decoupled from the instance's private config/projects.yaml + config/cost-sources.yaml
# so the suite is self-contained and ships with no real project names. Example
# slugs: example-shop, example-news, example-blog (heuristic-only).
_GENERIC_PROJECTS = {
    "example-shop": {"workspace": "business", "aliases": ["example-shop", "shop"]},
    "example-news": {"workspace": "business", "aliases": ["example-news", "news"]},
    "example-blog": {"workspace": "business", "aliases": ["example-blog", "blog"]},
    "example-brief": {"workspace": "personal", "aliases": ["example-brief", "brief"]},
}

_GENERIC_COST_SOURCES = {
    "mappings": {
        "claude": {
            "ExampleShop": "example-shop",
            "example-shop": "example-shop",
            "ExampleNews": "example-news",
            "example-news": "example-news",
        },
        "gcp": {
            "example-shop-prod": "example-shop",
            "example-news-prod": "example-news",
        },
        "stripe": {"acct_example-shop": "example-shop"},
        "openai": {"proj_news": "example-news"},
        "elevenlabs": {"acct_brief": "example-brief"},
    },
    "unmapped_bucket": "unmapped",
}


@pytest.fixture
def cost_config(monkeypatch):
    """Point config.projects() + config.cost_sources() at the generic test
    registry/mappings, so cost reconciliation tests don't depend on (or leak) the
    instance's private project config. Opt-in (not autouse)."""
    from agentos.core import config

    monkeypatch.setattr(config, "projects", lambda: _GENERIC_PROJECTS)
    monkeypatch.setattr(config, "cost_sources", lambda: _GENERIC_COST_SOURCES)
    yield
