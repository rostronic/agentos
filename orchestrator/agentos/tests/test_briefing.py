"""Daily briefing — digest assembly and file output."""

from __future__ import annotations

from agentos.core import briefing


def test_build_briefing_has_all_sections(monkeypatch):
    # stub weather so the test never hits the network
    monkeypatch.setattr(briefing, "_weather_section", lambda: "- sunny")
    text = briefing.build_briefing(date_str="2026-06-07")
    assert "# Daily update — 2026-06-07" in text
    for header in ("Weather", "Pipelines & cron", "Open tasks", "Agent runs",
                   "Inbox", "Insight", "Spanish word of the day"):
        assert header in text


def test_personal_sections_are_deterministic_by_date():
    a = briefing._spanish_section("2026-06-07")
    b = briefing._spanish_section("2026-06-07")
    c = briefing._spanish_section("2026-06-08")
    assert a == b  # same date → same word
    assert "**" in a  # has a bolded word
    assert briefing._insight_section("2026-06-07").startswith("- ")
    # different date usually rotates (lists are short, so just assert it runs)
    assert isinstance(c, str)


def test_write_briefing_creates_dated_file(tmp_path):
    path = briefing.write_briefing("hello", date_str="2026-06-07", root=tmp_path)
    assert path == tmp_path / "briefings" / "2026-06-07.md"
    assert path.read_text() == "hello"


def test_tasks_section_lists_open_tasks(tmp_path):
    # conftest isolates work.sqlite to a temp dir, so this is safe.
    from agentos.storage import file_store as local_store
    from agentos.storage.task_store import Project, Task

    p = Project(name="Demo", slug="demo")
    local_store.create_project(p)
    local_store.create_task(Task(project_id=p.id, title="Wire the thing", status="ready"))

    section = briefing._tasks_section()
    assert "Wire the thing" in section
    assert "ready" in section


def test_sections_degrade_gracefully(monkeypatch):
    # a failing source must not crash the brief
    def boom():
        raise RuntimeError("nope")

    assert briefing._safe(boom, "- (unavailable)") == "- (unavailable)"
