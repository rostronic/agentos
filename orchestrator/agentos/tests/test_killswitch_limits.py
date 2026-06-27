"""Phase 6 — kill switch and resource limits."""

from __future__ import annotations

from agentos.core import killswitch, limits


def test_killswitch_pause_resume():
    assert not killswitch.is_paused()
    killswitch.pause("testing")
    assert killswitch.is_paused()
    assert killswitch.pause_reason() == "testing"
    killswitch.resume()
    assert not killswitch.is_paused()


def test_resume_when_not_paused_is_safe():
    killswitch.resume()  # no-op, no error
    assert not killswitch.is_paused()


def test_max_tasks_override_respected(monkeypatch):
    monkeypatch.setattr(limits, "budget_for_project", lambda project=None: {})
    assert limits.max_tasks_per_run(override=5) == 5


def test_max_tasks_capped_at_absolute(monkeypatch):
    monkeypatch.setattr(limits, "budget_for_project", lambda project=None: {})
    assert limits.max_tasks_per_run(override=99999) == limits.ABSOLUTE_MAX_TASKS_PER_RUN


def test_max_tasks_default(monkeypatch):
    monkeypatch.setattr(limits, "budget_for_project", lambda project=None: {})
    assert limits.max_tasks_per_run() == 20


def test_max_qa_retries_default(monkeypatch):
    monkeypatch.setattr(limits, "budget_for_project", lambda project=None: {})
    assert limits.max_qa_retries() == 2
