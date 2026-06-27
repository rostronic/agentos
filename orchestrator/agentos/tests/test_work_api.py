"""Phase 5 — Work-layer API endpoints (projects / sprints / tasks)."""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer

from agentos.entrypoints.api_server import build_app
from agentos.storage import file_store as local_store
from agentos.storage.task_store import Sprint, Task


@pytest.fixture
async def client():
    app = build_app()
    async with TestClient(TestServer(app)) as c:
        yield c


async def test_create_and_list_projects(client):
    resp = await client.post("/api/projects", json={"name": "MC", "slug": "mc"})
    assert resp.status == 201
    created = await resp.json()
    assert created["name"] == "MC"

    resp = await client.get("/api/projects")
    projects = await resp.json()
    assert any(p["name"] == "MC" for p in projects)


async def test_create_project_validates(client):
    resp = await client.post("/api/projects", json={})
    assert resp.status == 400


async def test_project_detail_includes_sprints_and_counts(client):
    resp = await client.post("/api/projects", json={"name": "P"})
    pid = (await resp.json())["id"]
    local_store.create_sprint(Sprint(project_id=pid, name="S1"))
    local_store.create_task(Task(project_id=pid, title="t1", status="done"))
    local_store.create_task(Task(project_id=pid, title="t2", status="ready"))

    resp = await client.get(f"/api/projects/{pid}")
    detail = await resp.json()
    assert len(detail["sprints"]) == 1
    assert detail["task_counts"]["done"] == 1
    assert detail["task_total"] == 2


async def test_project_detail_404(client):
    resp = await client.get("/api/projects/nope")
    assert resp.status == 404


async def test_sprints_endpoints(client):
    pid = (await (await client.post("/api/projects", json={"name": "P"})).json())["id"]
    resp = await client.post("/api/sprints", json={"project_id": pid, "name": "Sprint 1"})
    assert resp.status == 201
    resp = await client.get(f"/api/sprints?project_id={pid}")
    sprints = await resp.json()
    assert len(sprints) == 1
    assert sprints[0]["name"] == "Sprint 1"


async def test_create_and_filter_tasks(client):
    pid = (await (await client.post("/api/projects", json={"name": "P"})).json())["id"]
    resp = await client.post("/api/tasks", json={
        "project_id": pid, "title": "Do thing", "priority": "high",
        "depends_on": ["x"],
    })
    assert resp.status == 201
    task = await resp.json()
    assert task["priority"] == "high"
    assert task["depends_on"] == ["x"]

    resp = await client.get(f"/api/tasks?project_id={pid}")
    tasks = await resp.json()
    assert len(tasks) == 1


async def test_create_task_validates(client):
    resp = await client.post("/api/tasks", json={"title": "no project"})
    assert resp.status == 400


async def test_task_detail_includes_linked_run(client):
    from agentos.core import run_store

    pid = (await (await client.post("/api/projects", json={"name": "P"})).json())["id"]
    tid = (await (await client.post("/api/tasks", json={"project_id": pid, "title": "t"})).json())["id"]
    run = run_store.Run(agent="developer", status="done")
    run_store.create_run(run)
    local_store.link_run(tid, run.id)

    resp = await client.get(f"/api/tasks/{tid}")
    detail = await resp.json()
    assert detail["last_run"]["agent"] == "developer"


async def test_task_status_transition(client):
    pid = (await (await client.post("/api/projects", json={"name": "P"})).json())["id"]
    tid = (await (await client.post("/api/tasks", json={"project_id": pid, "title": "t"})).json())["id"]
    resp = await client.post(f"/api/tasks/{tid}/status", json={"status": "in_progress", "reason": "go"})
    assert resp.status == 200
    updated = await resp.json()
    assert updated["status"] == "in_progress"


async def test_task_status_404(client):
    resp = await client.post("/api/tasks/nope/status", json={"status": "done"})
    assert resp.status == 404


async def test_work_stats_endpoint(client):
    pid = (await (await client.post("/api/projects", json={"name": "P"})).json())["id"]
    await client.post("/api/tasks", json={"project_id": pid, "title": "t"})
    resp = await client.get("/api/work-stats")
    stats = await resp.json()
    assert stats["total_projects"] >= 1
    assert "by_status" in stats
