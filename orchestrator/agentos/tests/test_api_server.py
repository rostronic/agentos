"""Phase 4 — local dashboard API."""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer

from agentos.core import run_store
from agentos.entrypoints.api_server import build_app


@pytest.fixture
async def client():
    app = build_app()
    async with TestClient(TestServer(app)) as c:
        yield c


async def test_health(client):
    resp = await client.get("/api/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True


async def test_agents_endpoint(client):
    resp = await client.get("/api/agents")
    assert resp.status == 200
    agents = await resp.json()
    assert len(agents) == 8
    names = {a["name"] for a in agents}
    assert "researcher" in names
    assert all("system_prompt" in a for a in agents)


async def test_workflows_endpoint(client):
    resp = await client.get("/api/workflows")
    workflows = await resp.json()
    names = {w["name"] for w in workflows}
    assert "deep-research" in names


async def test_runs_endpoint_reflects_store(client):
    run_store.create_run(run_store.Run(agent="researcher", status="done", cost_usd=0.0))
    resp = await client.get("/api/runs")
    runs = await resp.json()
    assert len(runs) >= 1
    assert runs[0]["agent"] == "researcher"


async def test_run_detail_includes_events(client):
    run = run_store.Run(agent="developer", status="running")
    run_store.create_run(run)
    run_store.append_event(run.id, "step_start", {"x": 1})
    resp = await client.get(f"/api/runs/{run.id}")
    assert resp.status == 200
    detail = await resp.json()
    assert detail["agent"] == "developer"
    assert len(detail["events"]) == 1
    assert detail["events"][0]["type"] == "step_start"


async def test_run_detail_404(client):
    resp = await client.get("/api/runs/does-not-exist")
    assert resp.status == 404


async def test_stats_endpoint(client):
    run_store.create_run(run_store.Run(agent="qa", status="done"))
    resp = await client.get("/api/stats")
    stats = await resp.json()
    assert stats["total_runs"] >= 1
    assert "by_status" in stats
    assert "today_spend_usd" in stats


async def test_dispatch_endpoint_validates(client):
    resp = await client.post("/api/dispatch", json={"agent": "researcher"})
    assert resp.status == 400


async def test_dispatch_endpoint_delegates(client, monkeypatch):
    from agentos.core import router
    from agentos.core.router import DispatchOutcome

    monkeypatch.setattr(
        router, "dispatch",
        lambda agent, task, **kw: DispatchOutcome(
            ok=True, run_id="r1", text="done", billed_to="subscription"
        ),
    )
    resp = await client.post("/api/dispatch", json={"agent": "researcher", "task": "hi"})
    data = await resp.json()
    assert data["ok"]
    assert data["billed_to"] == "subscription"


async def test_costs_endpoint(client, tmp_path, monkeypatch):
    from agentos.cost_analytics import store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "costs.sqlite")
    store.replace_source("gcp", [
        {"project": "example-shop", "source": "gcp", "service": "Cloud Run",
         "period": "2026-05-01", "amount_usd": 51.0, "provenance": "billing-csv"},
    ])
    store.replace_source("openai", [
        {"project": "unmapped", "source": "openai", "service": "gpt-4o",
         "period": "2026-05-01", "amount_usd": 8.1, "provenance": "manual-entry",
         "native_id": "proj_unknown_x"},
    ])
    resp = await client.get("/api/costs")
    assert resp.status == 200
    agg = await resp.json()
    assert "totals" in agg and "by_project" in agg
    assert agg["totals"]["amount_usd"] == pytest.approx(59.1)
    assert agg["totals"]["unmapped_usd"] == pytest.approx(8.1)
    names = {p["name"] for p in agg["by_project"]}
    assert "example-shop" in names and "unmapped" in names


async def test_seo_endpoint(client, tmp_path, monkeypatch):
    import json

    from agentos.seo import loader

    reviews = tmp_path / "example-shop" / "docs" / "seo" / "reviews"
    reviews.mkdir(parents=True)
    (reviews / "SEO_REVIEW_2026-06-25.md").write_text(
        "# shop review\n\n## Full digest\n\n```\n*ExampleShop digest*\n```\n"
    )
    (reviews / "findings_2026-06-25.json").write_text(json.dumps({
        "window": {"current": ["2026-06-16", "2026-06-22"]},
        "actionable": [{"severity": "major", "area": "indexing", "detail": "0 of 6332 indexed"}],
        "watch": [],
    }))
    monkeypatch.setattr(loader, "projects", lambda: {
        "example-shop": {"repo_path": str(tmp_path / "example-shop"), "label": "ExampleShop"}
    })

    resp = await client.get("/api/seo")
    assert resp.status == 200
    data = await resp.json()
    assert data["summary"]["sites"] == 1
    assert data["summary"]["actionable"] == 1
    site = data["sites"][0]
    assert site["label"] == "ExampleShop" and site["date"] == "2026-06-25"
    assert site["actionable"][0]["detail"] == "0 of 6332 indexed"
    assert "ExampleShop digest" in site["digest"]
