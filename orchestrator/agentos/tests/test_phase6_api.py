"""Phase 6 — API endpoints for sprint run, inbox, kill switch."""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer

from agentos.core import ask_human, killswitch
from agentos.entrypoints.api_server import build_app
from agentos.storage import file_store as local_store
from agentos.storage.task_store import Project, Sprint, Task


@pytest.fixture
async def client():
    async with TestClient(TestServer(build_app())) as c:
        yield c


async def test_killswitch_get_and_set(client):
    resp = await client.get("/api/killswitch")
    assert (await resp.json())["paused"] is False

    await client.post("/api/killswitch", json={"paused": True, "reason": "test"})
    resp = await client.get("/api/killswitch")
    data = await resp.json()
    assert data["paused"] is True
    assert data["reason"] == "test"

    await client.post("/api/killswitch", json={"paused": False})
    assert (await (await client.get("/api/killswitch")).json())["paused"] is False
    killswitch.resume()  # cleanup


def _make_project_sprint():
    p = Project(name="T", slug="t")
    local_store.create_project(p)
    s = Sprint(project_id=p.id, name="S")
    local_store.create_sprint(s)
    return p, s


async def test_inbox_list_and_answer(client):
    p, s = _make_project_sprint()
    t = Task(project_id=p.id, sprint_id=s.id, title="x", status="blocked")
    local_store.create_task(t)
    qid = ask_human.file_question("Q?", task_id=t.id)

    resp = await client.get("/api/inbox")
    items = await resp.json()
    assert any(i["id"] == qid for i in items)

    resp = await client.post(f"/api/inbox/{qid}/answer", json={"answer": "yes"})
    result = await resp.json()
    assert result["resumed_task"] == t.id
    assert local_store.get_task(t.id)["status"] == "ready"


async def test_inbox_answer_requires_answer(client):
    p, s = _make_project_sprint()
    t = Task(project_id=p.id, sprint_id=s.id, title="x")
    local_store.create_task(t)
    qid = ask_human.file_question("Q?", task_id=t.id)
    resp = await client.post(f"/api/inbox/{qid}/answer", json={})
    assert resp.status == 400


async def test_run_sprint_endpoint(client, monkeypatch):
    from agentos.core import sprint_executor
    from agentos.core.router import DispatchOutcome

    monkeypatch.setattr(sprint_executor.budget, "budget_for_project", lambda project=None: {})
    import agentos.core.limits as limits_mod
    monkeypatch.setattr(limits_mod, "budget_for_project", lambda project=None: {})
    monkeypatch.setattr(
        sprint_executor.router, "dispatch",
        lambda agent, prompt, **kw: DispatchOutcome(
            ok=True, run_id="r", text="PASS" if agent == "qa" else "did it", cost_usd=0.0),
    )

    p, s = _make_project_sprint()
    t = Task(project_id=p.id, sprint_id=s.id, title="x", status="ready", assignee="developer")
    local_store.create_task(t)

    resp = await client.post(f"/api/sprints/{s.id}/run", json={"mode": "full"})
    data = await resp.json()
    assert data["ok"]
    assert len(data["processed"]) == 1
    assert data["processed"][0]["final_status"] == "done"
