"""Phase 6 — ask_human inbox + resume mechanism (incl. regression for bug #1)."""

from __future__ import annotations

from agentos.core import ask_human
from agentos.storage import file_store as local_store
from agentos.storage.task_store import Project, Sprint, Task


def _blocked_task_with_question():
    p = Project(name="T", slug="t")
    local_store.create_project(p)
    s = Sprint(project_id=p.id, name="S")
    local_store.create_sprint(s)
    t = Task(project_id=p.id, sprint_id=s.id, title="x", status="blocked")
    local_store.create_task(t)
    qid = ask_human.file_question("Which provider?", task_id=t.id, sprint_id=s.id,
                                  options=["Stripe", "Paddle"])
    return t, qid


def test_file_question_creates_open_inbox_item():
    t, qid = _blocked_task_with_question()
    item = local_store.get_inbox_item(qid)
    assert item["status"] == "open"
    assert item["task_id"] == t.id
    assert item["options"] == ["Stripe", "Paddle"]


def test_answering_reready_blocked_task():
    """REGRESSION (bug #1): answering must re-ready the blocked task."""
    t, qid = _blocked_task_with_question()
    result = ask_human.answer_question(qid, "Stripe")
    assert result["resumed_task"] == t.id
    assert local_store.get_task(t.id)["status"] == "ready"
    assert local_store.get_inbox_item(qid)["status"] == "answered"


def test_answer_injected_into_context():
    t, qid = _blocked_task_with_question()
    ask_human.answer_question(qid, "Use Stripe")
    ctx = ask_human.answered_context(t.id)
    assert "Which provider?" in ctx
    assert "Use Stripe" in ctx


def test_task_stays_blocked_if_other_questions_open():
    """A task with two open questions only resumes once BOTH are answered."""
    t, q1 = _blocked_task_with_question()
    q2 = ask_human.file_question("Second question?", task_id=t.id)
    # answer only the first
    r1 = ask_human.answer_question(q1, "a1")
    assert r1["resumed_task"] is None
    assert local_store.get_task(t.id)["status"] == "blocked"
    # answer the second → now it resumes
    r2 = ask_human.answer_question(q2, "a2")
    assert r2["resumed_task"] == t.id
    assert local_store.get_task(t.id)["status"] == "ready"


def test_answer_unknown_item_is_safe():
    result = ask_human.answer_question("nonexistent", "x")
    assert result["resumed_task"] is None
    assert result["error"]


def test_answer_by_short_id_prefix():
    """REGRESSION (bug #3): the CLI shows 8-char inbox ids; answering with one
    must resolve to the full id and actually answer + re-ready the task."""
    t, qid = _blocked_task_with_question()
    result = ask_human.answer_question(qid[:8], "Stripe")
    assert result.get("error") is None
    assert result["resumed_task"] == t.id
    assert local_store.get_inbox_item(qid)["status"] == "answered"
    assert local_store.get_task(t.id)["status"] == "ready"
