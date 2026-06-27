"""MCP server — exposes AgentOS to Claude Desktop.

Claude Desktop connects to this over stdio. It exposes the orchestrator as a
set of tools so you can say "list my agents" or "have the researcher find X"
and Claude Desktop dispatches through AgentOS.

Run standalone:
    python -m agentos.entrypoints.mcp_server

Or wire into Claude Desktop via config/claude-desktop-mcp.json.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

mcp = FastMCP("agentos")


@mcp.tool(description="List all registered AgentOS agents with their roles, models, and tools.")
def list_agents() -> list[dict[str, Any]]:
    from agentos.core.agent_loader import load_all_agents

    agents = load_all_agents()
    return [
        {
            "name": a["name"],
            "role": a.get("role", a["name"]),
            "description": a.get("description", ""),
            "model": (
                a["model"].get("preferred")
                if isinstance(a.get("model"), dict)
                else a.get("model")
            ),
            "tools": a.get("tools", []),
        }
        for a in agents
    ]


@mcp.tool(description="List all available AgentOS workflows (multi-agent recipes).")
def list_workflows() -> list[dict[str, Any]]:
    from agentos.core.workflow_loader import load_all_workflows

    return [
        {
            "name": w.name,
            "description": w.description,
            "steps": [{"id": s.id, "agent": s.agent} for s in w.steps],
            "inputs": list(w.inputs.keys()),
        }
        for w in load_all_workflows()
    ]


@mcp.tool(
    description=(
        "Dispatch a single AgentOS agent on a task. Returns the agent's response "
        "plus a run_id. agent must be one of the names from list_agents."
    )
)
def dispatch(agent: str, task: str, project: str | None = None) -> dict[str, Any]:
    from agentos.core import router

    outcome = router.dispatch(agent, task, project=project, triggered_by="desktop")
    return {
        "ok": outcome.ok,
        "run_id": outcome.run_id,
        "output": outcome.text,
        "error": outcome.error,
        "blocked_reason": outcome.blocked_reason,
        "cost_usd": outcome.cost_usd,
        "model": outcome.model,
    }


@mcp.tool(
    description=(
        "Run a multi-agent AgentOS workflow to completion. inputs is a dict of "
        "the workflow's required inputs (see list_workflows). Returns the final "
        "output, per-step results, and total cost."
    )
)
def run_workflow(name: str, inputs: dict[str, Any] | None = None, project: str | None = None) -> dict[str, Any]:
    from agentos.core import workflow_runner

    result = workflow_runner.run_workflow(
        name, inputs or {}, project=project, triggered_by="desktop"
    )
    return {
        "ok": result.ok,
        "run_id": result.run_id,
        "final_output": result.final_output,
        "error": result.error,
        "total_cost_usd": result.total_cost_usd,
        "steps": [
            {"step_id": s.step_id, "agent": s.agent, "ok": s.ok, "cost_usd": s.cost_usd}
            for s in result.steps
        ],
    }


@mcp.tool(description="Get the status and details of a specific run by its run_id.")
def get_run(run_id: str) -> dict[str, Any] | None:
    from agentos.core import run_store

    return run_store.get_run(run_id)


@mcp.tool(description="List the most recent AgentOS runs (default 10).")
def recent_runs(limit: int = 10) -> list[dict[str, Any]]:
    from agentos.core import run_store

    return run_store.list_runs(limit=limit)


@mcp.tool(description="Show today's AgentOS spend against the daily budget cap.")
def budget_status(project: str | None = None) -> dict[str, Any]:
    from agentos.core import budget
    from agentos.core.config import budget_for_project

    spent = budget.today_spend()
    cap = budget_for_project(project).get("daily_usd")
    return {
        "spent_usd": round(spent, 4),
        "daily_cap_usd": cap,
        "pct_used": round(spent / cap * 100, 1) if cap else None,
    }


def _resolve_project(ref: str | None) -> dict[str, Any] | None:
    from agentos.storage import file_store as local_store

    if not ref:
        return None
    for p in local_store.list_projects():
        if ref in (p["id"], p["slug"], p["name"]):
            return p
    return None


@mcp.tool(description="List AgentOS work-layer projects (the dashboard's Projects).")
def list_projects() -> list[dict[str, Any]]:
    from agentos.storage import file_store as local_store

    return local_store.list_projects()


@mcp.tool(description="List sprints for a project (by slug, name, or id) with their ids and goals.")
def list_sprints(project: str) -> list[dict[str, Any]]:
    from agentos.storage import file_store as local_store

    p = _resolve_project(project)
    return local_store.list_sprints(p["id"]) if p else []


@mcp.tool(description="List tasks, optionally filtered by project (slug/name/id) and status.")
def list_tasks(project: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    from agentos.storage import file_store as local_store

    pid = None
    if project:
        p = _resolve_project(project)
        if not p:
            return []
        pid = p["id"]
    return local_store.list_tasks(project_id=pid, status=status)


@mcp.tool(
    description=(
        "Plan a project: decompose a goal into phases (sprints) of real tasks via the "
        "planner agent, written to the work layer. `project` is a slug from list_projects. "
        "Returns the phases and their sprint ids. Writes nothing if planning fails."
    )
)
def plan_project(project: str, goal: str) -> dict[str, Any]:
    from agentos.core import plan_project as pp

    try:
        return pp.plan_project(project, goal)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


@mcp.tool(
    description=(
        "Run a sprint: the team implements its ready tasks (dispatch -> QA -> advance), "
        "stopping at the Inbox when blocked. mode: 'semi' (default; stops at review for "
        "your sign-off), 'manual', or 'full' (auto-advance). Get sprint_id from "
        "plan_project or list_sprints."
    )
)
def run_sprint(sprint_id: str, mode: str = "semi", max_tasks: int | None = None) -> dict[str, Any]:
    from agentos.core import sprint_executor

    r = sprint_executor.execute_sprint(sprint_id, mode=mode, max_tasks=max_tasks)
    return {
        "ok": r.ok,
        "sprint_id": r.sprint_id,
        "mode": r.mode,
        "stopped_reason": r.stopped_reason,
        "total_cost_usd": r.total_cost_usd,
        "processed": [
            {"task_id": o.task_id, "title": o.title, "final_status": o.final_status,
             "agent": o.agent, "qa_passed": o.qa_passed, "note": o.note}
            for o in r.processed
        ],
    }


def main() -> None:
    mcp.run()  # stdio transport by default


if __name__ == "__main__":
    main()
