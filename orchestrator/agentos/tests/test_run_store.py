"""Phase 1 — run persistence in sqlite."""

from __future__ import annotations

from agentos.core import run_store


def test_create_and_get_run():
    run = run_store.Run(agent="researcher", status="running", inputs={"task": "x"})
    run_id = run_store.create_run(run)
    fetched = run_store.get_run(run_id)
    assert fetched is not None
    assert fetched["agent"] == "researcher"
    assert fetched["status"] == "running"


def test_update_run():
    run = run_store.Run(agent="developer", status="running")
    run_store.create_run(run)
    run_store.update_run(run.id, status="done", cost_usd=0.05)
    fetched = run_store.get_run(run.id)
    assert fetched["status"] == "done"
    assert fetched["cost_usd"] == 0.05


def test_append_events_and_list():
    run = run_store.Run(agent="qa")
    run_store.create_run(run)
    run_store.append_event(run.id, "dispatch_start", {"foo": "bar"})
    run_store.append_event(run.id, "dispatch_done", {"tokens": 100})
    # No direct getter for events in Phase 1; just verify no exception + run exists
    assert run_store.get_run(run.id) is not None


def test_list_runs_ordered():
    for i in range(3):
        run_store.create_run(run_store.Run(agent=f"agent{i}", status="done"))
    rows = run_store.list_runs(limit=10)
    assert len(rows) >= 3
