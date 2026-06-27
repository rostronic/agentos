"""plan-project — phased planning that writes real sprints + tasks (no mock data)."""

from __future__ import annotations

import types

from agentos.core import config, plan_project as pp
from agentos.storage import file_store as local_store

PLAN_JSON = """```json
{"phases":[
  {"name":"Phase 1: foundation","goal":"set up","tasks":[
    {"title":"Scaffold","description":"init","assignee":"developer","priority":"high","depends_on":[]},
    {"title":"Tests","description":"add tests","assignee":"qa","priority":"medium","depends_on":[1]}
  ]},
  {"name":"Phase 2: ship","goal":"deliver","tasks":[
    {"title":"Deploy prep","assignee":"developer","priority":"low"}
  ]}
]}
```"""


def _fake_dispatch(text, ok=True, error=""):
    return lambda *a, **k: types.SimpleNamespace(ok=ok, text=text, error=error)


def test_plan_project_writes_real_phases_and_tasks(monkeypatch):
    monkeypatch.setattr(config, "project_config", lambda s: {
        "workspace": "business", "repo_path": "~/x", "memory_path": "projects/demo", "aliases": ["demo"],
    })
    res = pp.plan_project("demo", "build the thing", dispatch=_fake_dispatch(PLAN_JSON))
    assert res["total_tasks"] == 3 and len(res["phases"]) == 2

    proj = next(p for p in local_store.list_projects() if p["slug"] == "demo")
    assert len(local_store.list_sprints(proj["id"])) == 2
    tasks = local_store.list_tasks(project_id=proj["id"])
    assert len(tasks) == 3
    assert all(t["status"] == "ready" for t in tasks)          # runnable by the executor
    a = next(t for t in tasks if t["title"] == "Scaffold")
    b = next(t for t in tasks if t["title"] == "Tests")
    assert a["id"] in b["depends_on"]                          # intra-phase dep linked


def test_plan_project_aborts_on_unparseable_output(monkeypatch):
    monkeypatch.setattr(config, "project_config", lambda s: {
        "workspace": "business", "repo_path": "~/x", "memory_path": "projects/demo2", "aliases": ["demo2"],
    })
    try:
        pp.plan_project("demo2", "x", dispatch=_fake_dispatch("here's a plan, no json"))
        assert False, "expected ValueError"
    except ValueError:
        pass
    # nothing written — no demo2 project/tasks materialized
    assert not any(p["slug"] == "demo2" for p in local_store.list_projects())


def test_plan_project_aborts_on_failed_dispatch(monkeypatch):
    monkeypatch.setattr(config, "project_config", lambda s: {
        "workspace": "business", "repo_path": "~/x", "memory_path": "projects/demo3", "aliases": ["demo3"],
    })
    try:
        pp.plan_project("demo3", "x", dispatch=_fake_dispatch("", ok=False, error="401"))
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass
    assert not any(p["slug"] == "demo3" for p in local_store.list_projects())


def test_plan_project_unknown_slug_raises(monkeypatch):
    monkeypatch.setattr(config, "project_config", lambda s: {})
    try:
        pp.plan_project("nope", "x", dispatch=_fake_dispatch(PLAN_JSON))
        assert False
    except ValueError:
        pass
