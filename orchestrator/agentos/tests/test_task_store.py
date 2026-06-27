"""Phase 5 — Work-layer persistence (projects / sprints / tasks)."""

from __future__ import annotations

from agentos.storage import local_store
from agentos.storage.task_store import Project, Sprint, Task


def _project(**kw) -> str:
    p = Project(name=kw.pop("name", "Mission Control"), slug=kw.pop("slug", "mc"), **kw)
    return local_store.create_project(p)


def test_create_and_get_project():
    pid = _project(name="Demo", repo_path="~/code/demo", description="A demo")
    fetched = local_store.get_project(pid)
    assert fetched is not None
    assert fetched["name"] == "Demo"
    assert fetched["repo_path"] == "~/code/demo"
    assert fetched["status"] == "active"


def test_list_projects():
    _project(name="A")
    _project(name="B")
    rows = local_store.list_projects()
    assert len(rows) == 2
    assert {r["name"] for r in rows} == {"A", "B"}


def test_create_and_list_sprints():
    pid = _project()
    sid = local_store.create_sprint(Sprint(project_id=pid, name="Sprint 1", goal="ship it"))
    sprints = local_store.list_sprints(pid)
    assert len(sprints) == 1
    assert sprints[0]["id"] == sid
    assert sprints[0]["status"] == "planned"


def test_create_and_get_task_roundtrips_depends_on():
    pid = _project()
    task = Task(project_id=pid, title="Build X", depends_on=["a", "b"], priority="high")
    tid = local_store.create_task(task)
    fetched = local_store.get_task(tid)
    assert fetched["title"] == "Build X"
    assert fetched["status"] == "backlog"
    assert fetched["priority"] == "high"
    assert fetched["depends_on"] == ["a", "b"]


def test_list_tasks_filters():
    pid = _project()
    sid = local_store.create_sprint(Sprint(project_id=pid, name="S"))
    local_store.create_task(Task(project_id=pid, title="t1", status="ready", sprint_id=sid))
    local_store.create_task(Task(project_id=pid, title="t2", status="done"))
    assert len(local_store.list_tasks(project_id=pid)) == 2
    assert len(local_store.list_tasks(status="ready")) == 1
    assert len(local_store.list_tasks(sprint_id=sid)) == 1


def test_update_task_status_and_reason():
    pid = _project()
    tid = local_store.create_task(Task(project_id=pid, title="t"))
    local_store.update_task_status(tid, "in_progress", reason="started work")
    t = local_store.get_task(tid)
    assert t["status"] == "in_progress"
    assert "started work" in (t["description"] or "")


def test_update_task_fields():
    pid = _project()
    tid = local_store.create_task(Task(project_id=pid, title="t"))
    local_store.update_task(tid, assignee="developer", depends_on=["x"])
    t = local_store.get_task(tid)
    assert t["assignee"] == "developer"
    assert t["depends_on"] == ["x"]


def test_link_run():
    pid = _project()
    tid = local_store.create_task(Task(project_id=pid, title="t"))
    local_store.link_run(tid, "run-123")
    assert local_store.get_task(tid)["last_run_id"] == "run-123"


def test_ready_tasks_respects_dependencies():
    pid = _project()
    sid = local_store.create_sprint(Sprint(project_id=pid, name="S"))
    dep = local_store.create_task(Task(project_id=pid, sprint_id=sid, title="dep", status="in_progress"))
    blocked = local_store.create_task(
        Task(project_id=pid, sprint_id=sid, title="blocked", status="ready", depends_on=[dep])
    )
    free = local_store.create_task(
        Task(project_id=pid, sprint_id=sid, title="free", status="ready")
    )

    # dep not done → only the dependency-free task is ready
    ready = {t["id"] for t in local_store.ready_tasks(sid)}
    assert free in ready
    assert blocked not in ready

    # finish the dependency → blocked task becomes ready
    local_store.update_task_status(dep, "done")
    ready = {t["id"] for t in local_store.ready_tasks(sid)}
    assert blocked in ready
    assert free in ready


def test_stats():
    pid = _project()
    local_store.create_task(Task(project_id=pid, title="a", status="done"))
    local_store.create_task(Task(project_id=pid, title="b", status="ready"))
    s = local_store.stats()
    assert s["total_projects"] == 1
    assert s["total_tasks"] == 2
    assert s["by_status"]["done"] == 1
    assert s["per_project"][pid] == 2
