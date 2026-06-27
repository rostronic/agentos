"""Phase 7 — Linear adapter (mocked GraphQL) + store factory."""

from __future__ import annotations

from agentos.storage import store_factory
from agentos.storage.linear_store import LinearStore


def _issue(id="iss1", title="Build it", state_name="Todo", state_type="unstarted",
           priority=2, labels=None, cycle_id=None):
    return {
        "id": id, "title": title, "description": "desc", "priority": priority,
        "url": f"https://linear.app/x/issue/{id}",
        "state": {"id": "st1", "name": state_name, "type": state_type},
        "cycle": {"id": cycle_id} if cycle_id else None,
        "labels": {"nodes": [{"name": n} for n in (labels or [])]},
    }


def test_issue_to_task_status_mapping():
    s = LinearStore(api_key="x", http=lambda *a: {})
    assert s.issue_to_task(_issue(state_type="backlog"))["status"] == "backlog"
    assert s.issue_to_task(_issue(state_type="unstarted"))["status"] == "ready"
    assert s.issue_to_task(_issue(state_type="started"))["status"] == "in_progress"
    assert s.issue_to_task(_issue(state_type="completed"))["status"] == "done"
    assert s.issue_to_task(_issue(state_name="In Review", state_type="started"))["status"] == "review"
    assert s.issue_to_task(_issue(state_name="Blocked", state_type="started"))["status"] == "blocked"


def test_issue_to_task_agent_label_becomes_assignee():
    t = LinearStore(api_key="x", http=lambda *a: {}).issue_to_task(
        _issue(labels=["agent:developer", "bug"]))
    assert t["assignee"] == "developer"


def test_issue_to_task_priority_mapping():
    s = LinearStore(api_key="x", http=lambda *a: {})
    assert s.issue_to_task(_issue(priority=1))["priority"] == "high"
    assert s.issue_to_task(_issue(priority=3))["priority"] == "medium"
    assert s.issue_to_task(_issue(priority=4))["priority"] == "low"


def test_list_tasks_mocked():
    def fake_http(query, variables, key):
        return {"data": {"issues": {"nodes": [_issue("a"), _issue("b", state_type="completed")]}}}
    s = LinearStore(api_key="x", http=fake_http)
    tasks = s.list_tasks(project_id="proj1")
    assert len(tasks) == 2
    assert {t["id"] for t in tasks} == {"a", "b"}


def test_list_tasks_status_filter():
    def fake_http(query, variables, key):
        return {"data": {"issues": {"nodes": [
            _issue("a", state_type="unstarted"), _issue("b", state_type="completed")]}}}
    s = LinearStore(api_key="x", http=fake_http)
    ready = s.list_tasks(project_id="p", status="ready")
    assert [t["id"] for t in ready] == ["a"]


def test_update_status_resolves_state_and_mutates():
    calls = []
    def fake_http(query, variables, key):
        calls.append(query)
        if "team(id" in query and "states" in query:
            return {"data": {"team": {"states": {"nodes": [
                {"id": "done-state", "name": "Done", "type": "completed"}]}}}}
        if "issue(id" in query and "team" in query:
            return {"data": {"issue": {"team": {"id": "team1"}}}}
        if "issueUpdate" in query:
            return {"data": {"issueUpdate": {"success": True}}}
        return {"data": {}}
    s = LinearStore(api_key="x", http=fake_http)
    s.update_task_status("iss1", "done")
    assert any("issueUpdate" in c for c in calls)


def test_missing_api_key_raises():
    s = LinearStore(api_key="", http=lambda *a: {})
    try:
        s.list_tasks()
        assert False, "should have raised"
    except RuntimeError as e:
        assert "LINEAR_API_KEY" in str(e)


def test_api_errors_surface():
    s = LinearStore(api_key="x", http=lambda *a: {"errors": [{"message": "bad"}]})
    try:
        s.get_task("iss1")
        assert False
    except RuntimeError as e:
        assert "Linear API error" in str(e)


# --- store factory ---
def test_factory_defaults_to_file(monkeypatch):
    from agentos.storage import file_store, local_store

    monkeypatch.setattr(store_factory, "project_settings", lambda slug=None: {})
    # Default backend is now the git-backed file store.
    assert store_factory.backend_name("anything") == "file"
    store = store_factory.store_for("anything")
    assert store is file_store
    assert hasattr(store, "create_task")  # file_store module
    # Legacy sqlite store stays selectable via task_store: local.
    monkeypatch.setattr(store_factory, "project_settings", lambda slug=None: {"task_store": "local"})
    assert store_factory.store_for("anything") is local_store


def test_factory_picks_linear(monkeypatch):
    monkeypatch.setattr(store_factory, "project_settings",
                        lambda slug=None: {"task_store": "linear"})
    monkeypatch.setattr("agentos.core.config.get_api_key", lambda p: "fake-key")
    store = store_factory.store_for("example-shop")
    assert isinstance(store, LinearStore)
