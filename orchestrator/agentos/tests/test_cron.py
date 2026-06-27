"""Phase 10 — cron matcher + schedule runner."""

from __future__ import annotations

from datetime import datetime

import pytest

from agentos.core import cron


# 2026-06-03 is a Wednesday.
WED_8AM = datetime(2026, 6, 3, 8, 0)
WED_830 = datetime(2026, 6, 3, 8, 30)
MON_9AM = datetime(2026, 6, 1, 9, 0)  # Monday


def test_every_minute():
    assert cron.cron_matches("* * * * *", WED_8AM)


def test_specific_time():
    assert cron.cron_matches("0 8 * * *", WED_8AM)
    assert not cron.cron_matches("0 8 * * *", WED_830)


def test_step_minutes():
    assert cron.cron_matches("*/15 * * * *", datetime(2026, 6, 3, 8, 15))
    assert not cron.cron_matches("*/15 * * * *", datetime(2026, 6, 3, 8, 7))


def test_range_hours():
    assert cron.cron_matches("0 9-17 * * *", datetime(2026, 6, 3, 13, 0))
    assert not cron.cron_matches("0 9-17 * * *", datetime(2026, 6, 3, 20, 0))


def test_weekday_name():
    assert cron.cron_matches("0 9 * * MON", MON_9AM)
    assert not cron.cron_matches("0 9 * * MON", WED_8AM)


def test_list_field():
    assert cron.cron_matches("0 8,12,18 * * *", datetime(2026, 6, 3, 12, 0))
    assert not cron.cron_matches("0 8,12,18 * * *", datetime(2026, 6, 3, 15, 0))


def test_invalid_expr_raises():
    with pytest.raises(ValueError):
        cron.cron_matches("0 8 * *", WED_8AM)  # only 4 fields


def test_due_schedules_respects_enabled():
    schedules = [
        {"name": "a", "workflow": "w", "cron": "* * * * *", "enabled": True},
        {"name": "b", "workflow": "w", "cron": "* * * * *", "enabled": False},
    ]
    due = cron.due_schedules(WED_8AM, schedules)
    assert [s["name"] for s in due] == ["a"]


def test_due_schedules_skips_non_matching():
    schedules = [{"name": "a", "workflow": "w", "cron": "0 9 * * *", "enabled": True}]
    assert cron.due_schedules(WED_8AM, schedules) == []


def test_run_due_dry_run(monkeypatch):
    monkeypatch.setattr(cron, "load_schedules",
                        lambda: [{"name": "a", "workflow": "daily-briefing",
                                  "cron": "* * * * *", "enabled": True}])
    results = cron.run_due(WED_8AM, dry_run=True)
    assert len(results) == 1
    assert results[0]["dry_run"] is True


def test_run_due_fires_workflow(monkeypatch):
    fired = []
    monkeypatch.setattr(cron, "load_schedules",
                        lambda: [{"name": "a", "workflow": "daily-briefing",
                                  "cron": "* * * * *", "enabled": True, "inputs": {"focus": "x"}}])

    class FakeResult:
        ok = True
        run_id = "r1"

    import agentos.core.workflow_runner as wr
    monkeypatch.setattr(wr, "run_workflow",
                        lambda name, inputs, **kw: fired.append((name, inputs)) or FakeResult())
    results = cron.run_due(WED_8AM, dry_run=False)
    assert results[0]["ok"]
    assert fired == [("daily-briefing", {"focus": "x"})]
