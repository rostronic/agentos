"""plan-project — decompose a project goal into phases (sprints) of real tasks.

Dispatches the **planner** agent for a structured phased plan, then writes real
Sprint + Task records to the work layer so the sprint executor can implement them
phase by phase.

NO mock/sample data: everything comes from the planner's output on a real goal.
If the dispatch fails or the output won't parse as the expected JSON, NOTHING is
written (we'd rather plan nothing than seed fake tasks).
"""

from __future__ import annotations

import json
import re

from agentos.core import onboard

_PROMPT = """You are planning a software project for an autonomous agent team.
Decompose the goal into ordered PHASES; each phase is a sprint of small,
independently-shippable tasks.

Project: {project}
Goal: {goal}

Return ONLY valid JSON (no prose, no markdown fences) shaped exactly like:
{{"phases":[
  {{"name":"Phase 1: <short>","goal":"<one line>","tasks":[
    {{"title":"<short title>","description":"<one line>","assignee":"developer|qa|researcher|planner|scribe|analyst|human","priority":"high|medium|low","acceptance":"<acceptance criteria>","depends_on":[]}}
  ]}}
]}}
`depends_on` lists 1-based indices of earlier tasks IN THE SAME PHASE that this task
depends on. Keep tasks small and concrete. Aim for 2-5 phases, 2-8 tasks each."""


def _parse_plan(text: str) -> dict:
    """Extract the JSON plan from the planner's reply. Raises ValueError if absent."""
    t = re.sub(r"```(?:json)?", "", text).strip()
    a, b = t.find("{"), t.rfind("}")
    if a == -1 or b == -1 or b <= a:
        raise ValueError("planner did not return a JSON object")
    plan = json.loads(t[a : b + 1])
    if not isinstance(plan.get("phases"), list) or not plan["phases"]:
        raise ValueError("planner JSON has no phases")
    return plan


def plan_project(slug: str, goal: str, *, dispatch=None) -> dict:
    """Plan `goal` for project `slug` into phases of tasks in the work layer.

    `dispatch` is injectable for tests; defaults to router.dispatch (real planner)."""
    from agentos.core import config, router
    from agentos.storage import file_store as local_store
    from agentos.storage.task_store import Sprint, Task

    if not config.project_config(slug):
        raise ValueError(f"unknown project '{slug}' — add it to config/projects.yaml first")

    runner = dispatch or router.dispatch
    outcome = runner("planner", _PROMPT.format(project=slug, goal=goal),
                     project=slug, triggered_by="plan-project")
    if not getattr(outcome, "ok", False):
        raise RuntimeError(f"planner dispatch failed: {getattr(outcome, 'error', '')}")

    plan = _parse_plan(outcome.text)  # raises before anything is written

    proj = onboard.ensure_work_project(slug)
    phases_out = []
    for ph in plan["phases"]:
        sid = local_store.create_sprint(Sprint(
            project_id=proj["id"], name=str(ph.get("name", "Phase")), goal=ph.get("goal"),
        ))
        task_ids: list[str] = []
        tasks = ph.get("tasks") or []
        for t in tasks:
            tid = local_store.create_task(Task(
                project_id=proj["id"], sprint_id=sid,
                title=str(t.get("title", "(untitled)")),
                description=t.get("description"),
                status="ready",
                assignee=t.get("assignee"),
                priority=str(t.get("priority") or "medium").lower(),
                acceptance_criteria=t.get("acceptance"),
                created_by="plan-project",
            ))
            task_ids.append(tid)
        # resolve intra-phase dependencies (1-based indices → task ids)
        for t, tid in zip(tasks, task_ids):
            deps = [
                task_ids[i - 1]
                for i in (t.get("depends_on") or [])
                if isinstance(i, int) and 1 <= i <= len(task_ids) and task_ids[i - 1] != tid
            ]
            if deps:
                local_store.update_task(tid, depends_on=deps)
        phases_out.append({"name": ph.get("name"), "sprint_id": sid, "tasks": len(task_ids)})

    return {"project": slug, "phases": phases_out, "total_tasks": sum(p["tasks"] for p in phases_out)}
