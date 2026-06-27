"""Git-backed file store — full TaskStore + inbox surface, parity with sqlite.

Mirrors test_task_store.py + test_ask_human.py against file_store, and asserts
files actually land under WORK_DIR (the git-backed proof). Also covers the
sqlite→files migration importer.
"""

from __future__ import annotations

from agentos.storage import file_store
from agentos.storage.task_store import Project, Sprint, Task


def _project(**kw) -> str:
    p = Project(name=kw.pop("name", "Mission Control"), slug=kw.pop("slug", "mc"), **kw)
    return file_store.create_project(p)


# --------------------------------------------------------------------------- #
# Projects / sprints
# --------------------------------------------------------------------------- #
def test_create_and_get_project():
    pid = _project(name="Demo", repo_path="~/code/demo", description="A demo")
    fetched = file_store.get_project(pid)
    assert fetched is not None
    assert fetched["name"] == "Demo"
    assert fetched["repo_path"] == "~/code/demo"
    assert fetched["status"] == "active"


def test_project_file_lands_on_disk():
    pid = _project(name="Demo")
    path = file_store.WORK_DIR / "projects" / f"{pid}.md"
    assert path.exists()
    assert path.read_text().startswith("---\n")


def test_list_projects_newest_first():
    _project(name="A")
    _project(name="B")
    rows = file_store.list_projects()
    assert len(rows) == 2
    assert {r["name"] for r in rows} == {"A", "B"}
    # deterministic ordering: created_at desc then id desc
    assert rows == sorted(rows, key=lambda d: (d["created_at"], d["id"]), reverse=True)


def test_create_and_list_sprints():
    pid = _project()
    sid = file_store.create_sprint(Sprint(project_id=pid, name="Sprint 1", goal="ship it"))
    sprints = file_store.list_sprints(pid)
    assert len(sprints) == 1
    assert sprints[0]["id"] == sid
    assert sprints[0]["status"] == "planned"
    assert (file_store.WORK_DIR / "sprints" / f"{sid}.md").exists()


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #
def test_create_and_get_task_roundtrips_depends_on():
    pid = _project()
    task = Task(project_id=pid, title="Build X", depends_on=["a", "b"], priority="high")
    tid = file_store.create_task(task)
    fetched = file_store.get_task(tid)
    assert fetched["title"] == "Build X"
    assert fetched["status"] == "backlog"
    assert fetched["priority"] == "high"
    assert fetched["depends_on"] == ["a", "b"]
    assert (file_store.WORK_DIR / "tasks" / f"{tid}.md").exists()


def test_list_tasks_filters():
    pid = _project()
    sid = file_store.create_sprint(Sprint(project_id=pid, name="S"))
    file_store.create_task(Task(project_id=pid, title="t1", status="ready", sprint_id=sid))
    file_store.create_task(Task(project_id=pid, title="t2", status="done"))
    assert len(file_store.list_tasks(project_id=pid)) == 2
    assert len(file_store.list_tasks(status="ready")) == 1
    assert len(file_store.list_tasks(sprint_id=sid)) == 1


def test_update_task_status_and_reason():
    pid = _project()
    tid = file_store.create_task(Task(project_id=pid, title="t"))
    file_store.update_task_status(tid, "in_progress", reason="started work")
    t = file_store.get_task(tid)
    assert t["status"] == "in_progress"
    assert "started work" in (t["description"] or "")
    # status history is recorded in the body
    body = (file_store.WORK_DIR / "tasks" / f"{tid}.md").read_text()
    assert "## Status history" in body
    assert "backlog → in_progress: started work" in body


def test_update_task_fields():
    pid = _project()
    tid = file_store.create_task(Task(project_id=pid, title="t"))
    file_store.update_task(tid, assignee="developer", depends_on=["x"])
    t = file_store.get_task(tid)
    assert t["assignee"] == "developer"
    assert t["depends_on"] == ["x"]


def test_update_task_description_rewrites_body():
    pid = _project()
    tid = file_store.create_task(Task(project_id=pid, title="t", description="old"))
    file_store.update_task(tid, description="new desc")
    assert file_store.get_task(tid)["description"] == "new desc"


def test_link_run():
    pid = _project()
    tid = file_store.create_task(Task(project_id=pid, title="t"))
    file_store.link_run(tid, "run-123")
    assert file_store.get_task(tid)["last_run_id"] == "run-123"


def test_ready_tasks_respects_dependencies():
    pid = _project()
    sid = file_store.create_sprint(Sprint(project_id=pid, name="S"))
    dep = file_store.create_task(Task(project_id=pid, sprint_id=sid, title="dep", status="in_progress"))
    blocked = file_store.create_task(
        Task(project_id=pid, sprint_id=sid, title="blocked", status="ready", depends_on=[dep])
    )
    free = file_store.create_task(
        Task(project_id=pid, sprint_id=sid, title="free", status="ready")
    )

    ready = {t["id"] for t in file_store.ready_tasks(sid)}
    assert free in ready
    assert blocked not in ready

    file_store.update_task_status(dep, "done")
    ready = {t["id"] for t in file_store.ready_tasks(sid)}
    assert blocked in ready
    assert free in ready


def test_stats():
    pid = _project()
    file_store.create_task(Task(project_id=pid, title="a", status="done"))
    file_store.create_task(Task(project_id=pid, title="b", status="ready"))
    s = file_store.stats()
    assert s["total_projects"] == 1
    assert s["total_tasks"] == 2
    assert s["by_status"]["done"] == 1
    assert s["per_project"][pid] == 2
    assert set(s.keys()) == {"total_projects", "total_tasks", "by_status", "per_project"}


# --------------------------------------------------------------------------- #
# Inbox
# --------------------------------------------------------------------------- #
def test_inbox_create_get_options_roundtrip():
    iid = file_store.create_inbox_item(
        "Which provider?", task_id="task-1", options=["Stripe", "Paddle"]
    )
    item = file_store.get_inbox_item(iid)
    assert item["status"] == "open"
    assert item["task_id"] == "task-1"
    assert item["options"] == ["Stripe", "Paddle"]
    assert item["prompt"] == "Which provider?"
    assert (file_store.WORK_DIR / "inbox" / f"{iid}.md").exists()


def test_inbox_answer_flow():
    iid = file_store.create_inbox_item("Q?")
    file_store.answer_inbox(iid, "yes", answered_by="human")
    item = file_store.get_inbox_item(iid)
    assert item["status"] == "answered"
    assert item["answer"] == "yes"
    assert item["answered_by"] == "human"
    assert item["answered_at"]
    assert file_store.list_inbox(status="open") == []
    assert len(file_store.list_inbox(status="answered")) == 1


def test_inbox_dismiss():
    iid = file_store.create_inbox_item("Q?")
    file_store.dismiss_inbox(iid)
    assert file_store.get_inbox_item(iid)["status"] == "dismissed"
    assert file_store.list_inbox(status="open") == []


def test_open_inbox_for_task():
    i1 = file_store.create_inbox_item("Q1", task_id="t1")
    file_store.create_inbox_item("Q2", task_id="t2")
    i3 = file_store.create_inbox_item("Q3", task_id="t1")
    file_store.answer_inbox(i3, "done")
    open_for_t1 = {i["id"] for i in file_store.open_inbox_for_task("t1")}
    assert open_for_t1 == {i1}


def test_list_inbox_ordering_deterministic():
    file_store.create_inbox_item("a")
    file_store.create_inbox_item("b")
    rows = file_store.list_inbox(status=None)
    assert rows == sorted(rows, key=lambda d: (d["created_at"], d["id"]), reverse=True)


# --------------------------------------------------------------------------- #
# Migration: work.sqlite → work/ files
# --------------------------------------------------------------------------- #
def test_migrate_no_sqlite_is_noop(tmp_path):
    res = file_store.migrate_from_sqlite(tmp_path / "absent.sqlite")
    assert res == {"migrated": 0, "reason": "no work.sqlite"}


def test_migrate_from_seeded_sqlite(tmp_path, monkeypatch):
    from agentos.storage import local_store

    db = tmp_path / "seed_work.sqlite"
    monkeypatch.setattr(local_store, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(local_store, "DB_PATH", db)

    pid = local_store.create_project(Project(name="Mig", slug="mig", repo_path="/tmp/mig"))
    sid = local_store.create_sprint(Sprint(project_id=pid, name="S1", goal="g"))
    tid = local_store.create_task(
        Task(project_id=pid, sprint_id=sid, title="task1", depends_on=["x", "y"], status="ready")
    )
    iid = local_store.create_inbox_item("Q?", task_id=tid, options=["A", "B"])

    res = file_store.migrate_from_sqlite(db)
    assert res["migrated_projects"] == 1
    assert res["migrated_sprints"] == 1
    assert res["migrated_tasks"] == 1
    assert res["migrated_inbox"] == 1

    # files exist and round-trip
    assert file_store.get_project(pid)["repo_path"] == "/tmp/mig"
    assert file_store.get_task(tid)["depends_on"] == ["x", "y"]
    assert file_store.get_inbox_item(iid)["options"] == ["A", "B"]

    # idempotent: second run skips everything
    res2 = file_store.migrate_from_sqlite(db)
    assert res2["migrated_projects"] == 0
    assert res2["migrated_tasks"] == 0
    assert res2["skipped"] == 4
