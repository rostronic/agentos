"""Local HTTP API for the mission-control dashboard.

Fully local — no cloud, no Convex. The Next.js dashboard reads from this over
localhost. It serves the orchestrator's sqlite state (runs, agents, workflows,
budget) and streams live run events via Server-Sent Events.

Start with:  agentos serve   (default http://127.0.0.1:8787)

Security: binds to 127.0.0.1 only. Do not expose to 0.0.0.0 — that would put
your run history (and dispatch endpoint) on your local network.
"""

from __future__ import annotations

import asyncio
import json
import re

from aiohttp import web

from agentos.core import budget, run_store
from agentos.core.agent_loader import load_all_agents
from agentos.core.config import AGENTOS_ROOT, budget_for_project
from agentos.core.workflow_loader import load_all_workflows
from agentos.storage import file_store as local_store
from agentos.storage.task_store import Project, Sprint, Task

DASHBOARD_DIR = AGENTOS_ROOT / "dashboard"

routes = web.RouteTableDef()


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


# CORS for the Next.js dev server (localhost:3000 → localhost:8787)
@web.middleware
async def cors_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@routes.get("/")
async def index(request):
    """Serve the dashboard single-page app.

    no-store so the browser never renders a stale cached copy of the SPA after
    an update (which would surface as a stuck 'Loading…').
    """
    html = DASHBOARD_DIR / "index.html"
    if not html.exists():
        return web.Response(text="Dashboard not found. Expected ~/agentos/dashboard/index.html", status=404)
    return web.FileResponse(
        html, headers={"Cache-Control": "no-store, must-revalidate"}
    )


@routes.get("/api/health")
async def health(request):
    return _json({"ok": True, "service": "agentos-api"})


BRIEFINGS_DIR = AGENTOS_ROOT / "briefings"


@routes.get("/api/briefings")
async def briefings_list(request):
    """List available daily briefings (newest first)."""
    items = []
    if BRIEFINGS_DIR.is_dir():
        for p in sorted(BRIEFINGS_DIR.glob("*.md"), reverse=True):
            items.append({"date": p.stem, "name": p.name})
    return _json(items)


@routes.get("/api/briefings/{date}")
async def briefing_get(request):
    """Return one briefing's markdown. Date-validated to prevent path traversal."""
    date = request.match_info["date"]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return _json({"error": "bad date"}, status=400)
    p = BRIEFINGS_DIR / f"{date}.md"
    if not p.is_file():
        return _json({"error": "not found"}, status=404)
    return _json({"date": date, "content": p.read_text(encoding="utf-8")})


@routes.get("/api/agents")
async def agents(request):
    out = []
    for a in load_all_agents():
        model = a.get("model")
        out.append({
            "name": a["name"],
            "role": a.get("role", a["name"]),
            "description": a.get("description", ""),
            "model": model.get("preferred") if isinstance(model, dict) else model,
            "tools": a.get("tools", []),
            "system_prompt": a.get("system_prompt", ""),
        })
    return _json(out)


@routes.get("/api/workflows")
async def workflows(request):
    out = [
        {
            "name": w.name,
            "description": w.description,
            "steps": [{"id": s.id, "agent": s.agent} for s in w.steps],
            "inputs": w.inputs,
        }
        for w in load_all_workflows()
    ]
    return _json(out)


@routes.get("/api/runs")
async def runs(request):
    limit = int(request.query.get("limit", 50))
    status = request.query.get("status")
    return _json(run_store.list_runs(limit=limit, status=status))


@routes.get("/api/runs/{run_id}")
async def run_detail(request):
    run_id = request.match_info["run_id"]
    run = run_store.get_run(run_id)
    if not run:
        return _json({"error": "not found"}, status=404)
    run["events"] = run_store.get_events(run_id)
    return _json(run)


@routes.get("/api/stats")
async def stats(request):
    s = run_store.stats()
    s["today_spend_usd"] = round(budget.today_spend(), 4)
    s["daily_cap_usd"] = budget_for_project(None).get("daily_usd")
    return _json(s)


# --------------------------------------------------------------------------- #
# Work layer — Projects / Sprints / Tasks
# --------------------------------------------------------------------------- #
@routes.get("/api/projects")
async def projects(request):
    return _json(local_store.list_projects())


@routes.post("/api/projects")
async def create_project(request):
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        return _json({"error": "name required"}, status=400)
    project = Project(
        name=name,
        slug=body.get("slug") or "",
        repo_path=body.get("repo_path"),
        description=body.get("description"),
        status=body.get("status", "active"),
        lead_agent=body.get("lead_agent"),
    )
    local_store.create_project(project)
    return _json(local_store.get_project(project.id), status=201)


@routes.get("/api/projects/{project_id}")
async def project_detail(request):
    project_id = request.match_info["project_id"]
    project = local_store.get_project(project_id)
    if not project:
        return _json({"error": "not found"}, status=404)
    project["sprints"] = local_store.list_sprints(project_id)
    tasks = local_store.list_tasks(project_id=project_id)
    counts: dict[str, int] = {}
    for t in tasks:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    project["task_counts"] = counts
    project["task_total"] = len(tasks)
    return _json(project)


@routes.get("/api/sprints")
async def sprints(request):
    project_id = request.query.get("project_id")
    if not project_id:
        return _json({"error": "project_id required"}, status=400)
    return _json(local_store.list_sprints(project_id))


@routes.post("/api/sprints")
async def create_sprint(request):
    body = await request.json()
    project_id = body.get("project_id")
    name = (body.get("name") or "").strip()
    if not project_id or not name:
        return _json({"error": "project_id and name required"}, status=400)
    sprint = Sprint(
        project_id=project_id,
        name=name,
        goal=body.get("goal"),
        status=body.get("status", "planned"),
        starts_at=body.get("starts_at"),
        ends_at=body.get("ends_at"),
    )
    local_store.create_sprint(sprint)
    return _json({"id": sprint.id}, status=201)


@routes.get("/api/tasks")
async def tasks(request):
    return _json(local_store.list_tasks(
        project_id=request.query.get("project_id"),
        sprint_id=request.query.get("sprint_id"),
        status=request.query.get("status"),
    ))


@routes.post("/api/tasks")
async def create_task(request):
    body = await request.json()
    project_id = body.get("project_id")
    title = (body.get("title") or "").strip()
    if not project_id or not title:
        return _json({"error": "project_id and title required"}, status=400)
    task = Task(
        project_id=project_id,
        sprint_id=body.get("sprint_id"),
        title=title,
        description=body.get("description"),
        status=body.get("status", "backlog"),
        assignee=body.get("assignee"),
        priority=body.get("priority", "medium"),
        depends_on=body.get("depends_on") or [],
        acceptance_criteria=body.get("acceptance_criteria"),
        estimate_minutes=body.get("estimate_minutes"),
        parent_task_id=body.get("parent_task_id"),
        created_by=body.get("created_by", "human"),
    )
    local_store.create_task(task)
    return _json(local_store.get_task(task.id), status=201)


@routes.get("/api/tasks/{task_id}")
async def task_detail(request):
    task = local_store.get_task(request.match_info["task_id"])
    if not task:
        return _json({"error": "not found"}, status=404)
    if task.get("last_run_id"):
        task["last_run"] = run_store.get_run(task["last_run_id"])
    return _json(task)


@routes.post("/api/tasks/{task_id}/status")
async def task_status(request):
    task_id = request.match_info["task_id"]
    if not local_store.get_task(task_id):
        return _json({"error": "not found"}, status=404)
    body = await request.json()
    status = body.get("status")
    if not status:
        return _json({"error": "status required"}, status=400)
    local_store.update_task_status(task_id, status, reason=body.get("reason"))
    return _json(local_store.get_task(task_id))


@routes.get("/api/work-stats")
async def work_stats(request):
    return _json(local_store.stats())


@routes.get("/api/budget")
async def budget_status(request):
    project = request.query.get("project")
    spent = budget.today_spend()
    cap = budget_for_project(project).get("daily_usd")
    return _json({
        "spent_usd": round(spent, 4),
        "daily_cap_usd": cap,
        "pct_used": round(spent / cap * 100, 1) if cap else None,
    })


@routes.post("/api/dispatch")
async def dispatch(request):
    from agentos.core import router
    body = await request.json()
    agent = body.get("agent")
    task = body.get("task")
    if not agent or not task:
        return _json({"error": "agent and task required"}, status=400)
    # Run blocking dispatch in a thread so the event loop stays responsive
    outcome = await asyncio.to_thread(
        router.dispatch, agent, task,
        project=body.get("project"), triggered_by="dashboard",
    )
    return _json({
        "ok": outcome.ok, "run_id": outcome.run_id, "output": outcome.text,
        "error": outcome.error, "blocked_reason": outcome.blocked_reason,
        "cost_usd": outcome.cost_usd, "billed_to": outcome.billed_to,
    })


@routes.post("/api/run-workflow")
async def run_workflow(request):
    from agentos.core import workflow_runner
    body = await request.json()
    name = body.get("name")
    if not name:
        return _json({"error": "name required"}, status=400)
    result = await asyncio.to_thread(
        workflow_runner.run_workflow, name, body.get("inputs", {}),
        project=body.get("project"), triggered_by="dashboard",
    )
    return _json({
        "ok": result.ok, "run_id": result.run_id,
        "final_output": result.final_output, "error": result.error,
        "total_cost_usd": result.total_cost_usd,
        "steps": [
            {"step_id": s.step_id, "agent": s.agent, "ok": s.ok}
            for s in result.steps
        ],
    })


@routes.get("/api/events")
async def events_stream(request):
    """Server-Sent Events — streams new run_events as they're written to sqlite.

    The dashboard opens this once and receives a push whenever any run emits an
    event (step_start, step_done, dispatch_done, etc.). Replaces Convex's
    reactive useQuery with a simple, fully-local stream.
    """
    resp = web.StreamResponse(
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        }
    )
    await resp.prepare(request)
    last_id = run_store.max_event_id()
    try:
        while True:
            new_events = await asyncio.to_thread(run_store.events_since, last_id)
            for ev in new_events:
                last_id = ev["id"]
                await resp.write(f"data: {json.dumps(ev)}\n\n".encode())
            await resp.write(b": keepalive\n\n")  # comment frame to detect drop
            await asyncio.sleep(1.0)
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    return resp


# --------------------------------------------------------------------------- #
# Phase 6 — autonomous sprint execution, inbox, kill switch
# --------------------------------------------------------------------------- #
@routes.post("/api/sprints/{sprint_id}/run")
async def run_sprint(request):
    from agentos.core import sprint_executor
    sprint_id = request.match_info["sprint_id"]
    body = await request.json() if request.can_read_body else {}
    res = await asyncio.to_thread(
        sprint_executor.execute_sprint, sprint_id,
        mode=body.get("mode"), max_tasks=body.get("max_tasks"),
    )
    return _json({
        "ok": res.ok, "sprint_id": res.sprint_id, "mode": res.mode,
        "stopped_reason": res.stopped_reason, "total_cost_usd": res.total_cost_usd,
        "processed": [
            {"task_id": o.task_id, "title": o.title, "final_status": o.final_status,
             "agent": o.agent, "qa_passed": o.qa_passed, "note": o.note}
            for o in res.processed
        ],
    })


@routes.get("/api/inbox")
async def inbox_list(request):
    status = request.query.get("status", "open")
    return _json(local_store.list_inbox(status=status))


@routes.post("/api/inbox/{item_id}/answer")
async def inbox_answer(request):
    from agentos.core import ask_human
    item_id = request.match_info["item_id"]
    body = await request.json()
    answer = body.get("answer")
    if not answer:
        return _json({"error": "answer required"}, status=400)
    result = ask_human.answer_question(item_id, answer, body.get("answered_by", "human"))
    return _json(result)


@routes.get("/api/killswitch")
async def killswitch_status(request):
    from agentos.core import killswitch
    return _json({"paused": killswitch.is_paused(), "reason": killswitch.pause_reason()})


@routes.post("/api/killswitch")
async def killswitch_set(request):
    from agentos.core import killswitch
    body = await request.json()
    if body.get("paused"):
        killswitch.pause(body.get("reason", "paused via dashboard"))
    else:
        killswitch.resume()
    return _json({"paused": killswitch.is_paused(), "reason": killswitch.pause_reason()})


@routes.get("/api/insights")
async def insights(request):
    """Session quality insights from ~/.claude/usage-data/ (facets + meta).

    Answers 'where is my time going, was it worth it' — outcomes, friction,
    per-project quality. Complements /api/tokens (which answers cost).
    """
    from agentos.insights import aggregator
    agg = await asyncio.to_thread(aggregator.aggregate)
    return _json(agg)


@routes.get("/api/pipelines")
async def pipelines(request):
    """Health of the legacy content cron jobs across managed projects.

    Read-only, LOCAL files only — joins cron/jobs.json (definition) with
    cron/jobs-state.json (health) by job id. One pane of glass for 'did each
    job run, what's its last status, what failed'.
    """
    from agentos.pipelines import loader

    jobs = await asyncio.to_thread(loader.load_jobs)
    summary = await asyncio.to_thread(loader.summary, jobs)
    return _json({"summary": summary, "jobs": jobs})


@routes.get("/api/seo")
async def seo(request):
    """Latest weekly SEO digest for each managed site.

    Read-only, LOCAL files only — joins each site's newest SEO_REVIEW_<date>.md
    (digest) with findings_<date>.json (actionable / watch issues) under its
    docs/seo/reviews/ dir. One pane of glass for 'what did this week's SEO
    review surface, and what needs dev attention'.
    """
    from agentos.seo import loader

    sites = await asyncio.to_thread(loader.load_sites)
    summary = await asyncio.to_thread(loader.summary, sites)
    return _json({"summary": summary, "sites": sites})


@routes.get("/api/tokens")
async def tokens(request):
    """Token usage analytics from ~/.claude/projects transcripts.

    Cost is API-EQUIVALENT (what pay-per-token would have cost). On a Max/Pro
    subscription you pay the flat fee instead, so this doubles as a
    "value of my subscription" number.
    """
    from agentos.token_analytics import aggregator
    agg = await asyncio.to_thread(aggregator.aggregate)
    return _json(agg)


@routes.get("/api/costs")
async def costs(request):
    """Total real-dollar cost per project across all spend sources.

    Unlike /api/tokens (Claude API-EQUIVALENT, a 'value of subscription' number),
    this is actual money out: Claude + GCP/Firebase + third-party, attributed to
    registry-slug projects, with an explicit 'unmapped' bucket.
    """
    from agentos.cost_analytics import aggregator
    agg = await asyncio.to_thread(aggregator.aggregate)
    return _json(agg)


def build_app() -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    app.add_routes(routes)
    return app


def main(host: str = "127.0.0.1", port: int = 8787) -> None:
    web.run_app(build_app(), host=host, port=port, print=lambda *a: None)


if __name__ == "__main__":
    main()
