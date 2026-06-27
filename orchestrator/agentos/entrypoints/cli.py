"""AgentOS CLI — main entry point."""

from __future__ import annotations

import sys

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="agentos",
    help="AgentOS — model-agnostic agentic operating system.",
    rich_markup_mode="rich",
    no_args_is_help=True,
)
console = Console()


@app.command("init")
def init_cmd(
    name: str = typer.Option(None, "--name", help="Your display name (used in agent prompts)"),
    email: str = typer.Option(None, "--email", help="Primary account email (calendar/gmail/brief)"),
    timezone: str = typer.Option(None, "--timezone", "--tz", help="IANA timezone, e.g. America/New_York"),
    provider: str = typer.Option(None, "--provider", help="Inference backend: claude_code (subscription) or claude_api (metered)"),
    telegram_chat_id: str = typer.Option(None, "--telegram-chat-id", help="Telegram chat id for notifications (optional)"),
    config_dir: str = typer.Option(None, "--config-dir", help="Target config dir (default: ~/agentos/config or $AGENTOS_CONFIG_DIR)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Non-interactive: accept defaults, never prompt"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing config files"),
):
    """First-run setup — write config/{user,settings,budgets}.yaml and check your provider.

    Runs interactively (prompts for any value you don't pass) when stdin is a TTY and
    --yes is not set; otherwise uses flags + defaults so it's scriptable and testable.
    """
    from agentos.core import init_setup

    defaults = {"timezone": "America/Los_Angeles", "provider": "claude_code"}
    interactive = sys.stdin.isatty() and not yes

    def resolve(value, key, prompt, *, default=None, required=False):
        if value:
            return value
        if interactive:
            return typer.prompt(prompt, default=default if default is not None else "")
        if required and not default:
            console.print(f"[red]--{key} is required in non-interactive mode (or pass --yes with a default).[/red]")
            raise typer.Exit(1)
        return default or ""

    if interactive:
        console.print("[bold cyan]AgentOS setup[/bold cyan] — writing config/{user,settings,budgets}.yaml\n")

    name = resolve(name, "name", "Your name", required=True)
    email = resolve(email, "email", "Your email", required=True)
    timezone = resolve(timezone, "timezone", "Timezone (IANA)", default=defaults["timezone"])
    provider = resolve(provider, "provider", "Provider [claude_code|claude_api]", default=defaults["provider"])
    if provider not in init_setup.KNOWN_PROVIDERS:
        console.print(f"[red]Unknown provider '{provider}'. Choose one of: {', '.join(init_setup.KNOWN_PROVIDERS)}.[/red]")
        raise typer.Exit(1)
    telegram_chat_id = telegram_chat_id or ""

    try:
        result = init_setup.write_configs(
            name=name, email=email, timezone=timezone, provider=provider,
            telegram_chat_id=telegram_chat_id, config_dir=config_dir, force=force,
        )
    except FileNotFoundError as e:
        console.print(f"[red]Setup failed:[/red] {e}")
        console.print("[dim]Run from a checkout that has config/*.yaml.example templates.[/dim]")
        raise typer.Exit(1)

    console.print(f"\n[bold]Config dir:[/bold] {result.config_dir}")
    for p in result.written:
        console.print(f"  [green]wrote[/green]   {p.name}")
    for p in result.skipped:
        console.print(f"  [yellow]kept[/yellow]    {p.name} [dim](exists — use --force to overwrite)[/dim]")

    badge = "[green]ready[/green]" if result.provider_ok else "[yellow]needs setup[/yellow]"
    console.print(f"\n[bold]Provider:[/bold] {result.provider} — {badge}")
    console.print(f"  [dim]{result.provider_message}[/dim]")

    console.print("\n[bold]Next steps:[/bold]")
    for i, step in enumerate(result.next_steps, 1):
        console.print(f"  {i}. {step}")


@app.command("agents")
def list_agents():
    """List all registered agents and their status."""
    from agentos.core.agent_loader import load_all_agents

    agents = load_all_agents()
    if not agents:
        console.print("[yellow]No agents found. Check ~/agentos/agents/*/agent.md[/yellow]")
        raise typer.Exit(1)

    table = Table(title="AgentOS — Registered Agents", show_header=True, header_style="bold cyan")
    table.add_column("Name", style="bold")
    table.add_column("Role")
    table.add_column("Model")
    table.add_column("Tools")
    table.add_column("Description")

    for agent in agents:
        table.add_row(
            agent["name"],
            agent.get("role", "—"),
            agent.get("model", {}).get("preferred", "—") if isinstance(agent.get("model"), dict) else agent.get("model", "—"),
            ", ".join(agent.get("tools", [])),
            agent.get("description", "—"),
        )

    console.print(table)


@app.command("dispatch")
def dispatch(
    agent: str = typer.Argument(..., help="Agent name (e.g. researcher, developer)"),
    task: str = typer.Argument(..., help="Task description or prompt"),
    model: str = typer.Option(None, "--model", "-m", help="Override model"),
    provider: str = typer.Option(None, "--provider", help="Force backend: claude_code/claude_api/ollama/openai"),
    runtime: str = typer.Option(None, "--runtime", help="Agent runtime: native/agentcli/hermes"),
    project: str = typer.Option(None, "--project", "-p", help="Project for budget scoping"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print dispatch params, don't run"),
):
    """Dispatch a single agent on a task."""
    from agentos.core import router

    console.print(f"[bold]Dispatching[/bold] [cyan]{agent}[/cyan]: {task}")
    if dry_run:
        console.print("[yellow]Dry run — no API call made.[/yellow]")
        return

    with console.status("[cyan]Working...[/cyan]"):
        outcome = router.dispatch(agent, task, model_override=model,
                                  provider_override=provider, runtime_override=runtime,
                                  project=project)

    if outcome.blocked_reason:
        console.print(f"[bold red]Blocked[/bold red] ({outcome.blocked_reason}): {outcome.error}")
        raise typer.Exit(2)
    if not outcome.ok:
        console.print(f"[bold red]Failed:[/bold red] {outcome.error}")
        console.print(f"[dim]run_id: {outcome.run_id}[/dim]")
        raise typer.Exit(1)

    console.print()
    console.print(outcome.text)
    console.print()
    if outcome.billed_to == "subscription":
        cost_str = "[green]subscription (Max/Pro — no API charge)[/green]"
    elif outcome.billed_to == "local":
        cost_str = "[green]local (Ollama — no charge)[/green]"
    else:
        cost_str = f"${outcome.cost_usd:.4f} (API)"
    console.print(
        f"[dim]run_id: {outcome.run_id}  |  model: {outcome.model}  |  "
        f"billed: {cost_str}[/dim]"
    )


@app.command("run")
def run_workflow_cmd(
    workflow: str = typer.Argument(..., help="Workflow name (e.g. deep-research)"),
    arg: list[str] = typer.Option([], "--arg", "-a", help="Workflow input as key=value"),
    project: str = typer.Option(None, "--project", "-p", help="Project for budget scoping"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate workflow, don't run"),
):
    """Run a named workflow."""
    from agentos.core import workflow_runner
    from agentos.core.workflow_loader import WorkflowError, load_workflow

    # Parse key=value args into inputs
    inputs: dict[str, str] = {}
    for pair in arg:
        if "=" not in pair:
            console.print(f"[red]Invalid --arg '{pair}' (expected key=value)[/red]")
            raise typer.Exit(1)
        k, _, v = pair.partition("=")
        inputs[k.strip()] = v.strip()

    if dry_run:
        try:
            wf = load_workflow(workflow)
        except WorkflowError as e:
            console.print(f"[red]Invalid workflow:[/red] {e}")
            raise typer.Exit(1)
        console.print(f"[bold]{wf.name}[/bold] — {wf.description}")
        console.print(f"Steps: {' → '.join(f'{s.id}({s.agent})' for s in wf.steps)}")
        console.print(f"Inputs provided: {inputs or '(none)'}")
        console.print("[yellow]Dry run — validation passed, no API call made.[/yellow]")
        return

    console.print(f"[bold]Running workflow[/bold] [cyan]{workflow}[/cyan]")

    def on_step(sr):
        status = "[green]✓[/green]" if sr.ok else "[red]✗[/red]"
        console.print(f"  {status} {sr.step_id} ([dim]{sr.agent}[/dim]) — ${sr.cost_usd:.4f}")

    result = workflow_runner.run_workflow(workflow, inputs, project=project, on_step=on_step)

    if not result.ok:
        console.print(f"[bold red]Workflow failed:[/bold red] {result.error}")
        console.print(f"[dim]run_id: {result.run_id}[/dim]")
        raise typer.Exit(1)

    console.print()
    console.print(result.final_output)
    console.print()
    console.print(
        f"[dim]run_id: {result.run_id}  |  steps: {len(result.steps)}  |  "
        f"total cost: ${result.total_cost_usd:.4f}[/dim]"
    )


@app.command("workflows")
def workflows_cmd():
    """List available workflows."""
    from agentos.core.workflow_loader import load_all_workflows

    wfs = load_all_workflows()
    if not wfs:
        console.print("[yellow]No workflows found in ~/agentos/workflows/[/yellow]")
        return
    table = Table(title="Available Workflows", header_style="bold cyan")
    table.add_column("Name", style="bold")
    table.add_column("Steps")
    table.add_column("Description")
    for wf in wfs:
        table.add_row(
            wf.name,
            " → ".join(s.agent for s in wf.steps),
            wf.description,
        )
    console.print(table)


@app.command("mcp")
def mcp_server():
    """Start the MCP server for Claude Desktop (stdio transport)."""
    try:
        from agentos.entrypoints.mcp_server import main as mcp_main
    except ImportError:
        console.print(
            "[red]fastmcp not installed.[/red] Run: "
            "uv pip install 'agentos[mcp]'  (or pip install fastmcp)"
        )
        raise typer.Exit(1)
    mcp_main()


@app.command("serve")
def serve_cmd(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address (keep localhost)"),
    port: int = typer.Option(8787, "--port", help="Port for the dashboard API"),
):
    """Start the local dashboard API (serves sqlite state + live SSE)."""
    from agentos.entrypoints.api_server import main as api_main

    console.print(f"[bold]AgentOS API[/bold] → http://{host}:{port}")
    console.print("[dim]Dashboard reads from here. Ctrl+C to stop.[/dim]")
    api_main(host=host, port=port)


@app.command("cron")
def cron(
    dry_run: bool = typer.Option(False, "--dry-run", help="List due schedules, don't fire"),
    list_all: bool = typer.Option(False, "--list", help="List all configured schedules"),
):
    """Fire due scheduled workflows. Run once a minute from system cron."""
    from agentos.core import cron as cron_mod

    if list_all:
        schedules = cron_mod.load_schedules()
        if not schedules:
            console.print("[yellow]No schedules configured in config/schedules.yaml[/yellow]")
            return
        table = Table(title="Scheduled workflows", header_style="bold cyan")
        table.add_column("Name")
        table.add_column("Workflow")
        table.add_column("Cron")
        table.add_column("Enabled")
        for s in schedules:
            en = "[green]yes[/green]" if s.get("enabled") else "[dim]no[/dim]"
            table.add_row(s.get("name", "—"), s.get("workflow", "—"), s.get("cron", "—"), en)
        console.print(table)
        return

    results = cron_mod.run_due(dry_run=dry_run)
    if not results:
        console.print("[dim]No schedules due right now.[/dim]")
        return
    for r in results:
        verb = "would fire" if dry_run else ("fired ✓" if r.get("ok") else "failed ✗")
        console.print(f"  {verb}: [cyan]{r['name']}[/cyan] → {r['workflow']}")


@app.command("budget")
def budget_cmd(
    reset: bool = typer.Option(False, "--reset", help="Reset daily spend counter"),
    project: str = typer.Option(None, "--project", "-p", help="Project to scope budget"),
):
    """Show or reset budget usage."""
    from agentos.core import budget as budget_mod
    from agentos.core.config import budget_for_project

    if reset:
        budget_mod.reset_today()
        console.print("[green]Daily spend counter reset.[/green]")
        return

    spent = budget_mod.today_spend()
    b = budget_for_project(project)
    cap = b.get("daily_usd")
    if cap:
        pct = spent / cap * 100
        bar_color = "red" if pct >= 100 else "yellow" if pct >= 80 else "green"
        console.print(
            f"Today's spend: [bold]${spent:.4f}[/bold] / ${cap:.2f} "
            f"([{bar_color}]{pct:.0f}%[/{bar_color}])"
        )
    else:
        console.print(f"Today's spend: [bold]${spent:.4f}[/bold] (no daily cap set)")


@app.command("runs")
def runs_cmd(
    limit: int = typer.Option(10, "--limit", "-n", help="How many recent runs to show"),
    run_id: str = typer.Argument(None, help="Show detail for a specific run id"),
):
    """List recent runs, or show one run's detail."""
    from agentos.core import run_store

    if run_id:
        run = run_store.get_run(run_id)
        if not run:
            console.print(f"[red]No run found: {run_id}[/red]")
            raise typer.Exit(1)
        for k, v in run.items():
            console.print(f"[cyan]{k}[/cyan]: {v}")
        return

    rows = run_store.list_runs(limit=limit)
    if not rows:
        console.print("[yellow]No runs yet.[/yellow]")
        return
    table = Table(title="Recent Runs", header_style="bold cyan")
    table.add_column("id", style="dim", max_width=12)
    table.add_column("agent")
    table.add_column("status")
    table.add_column("model")
    table.add_column("$", justify="right")
    table.add_column("started")
    for r in rows:
        status_color = {"done": "green", "failed": "red", "blocked": "yellow"}.get(r["status"], "white")
        table.add_row(
            r["id"][:8],
            r["agent"] or r["workflow_name"] or "—",
            f"[{status_color}]{r['status']}[/{status_color}]",
            (r["model"] or "—").replace("claude-", ""),
            f"{r['cost_usd']:.4f}",
            (r["started_at"] or "")[:19],
        )
    console.print(table)


@app.command("projects")
def projects_cmd():
    """List Work-layer projects."""
    from agentos.storage import file_store as local_store

    rows = local_store.list_projects()
    if not rows:
        console.print("[yellow]No projects yet. Create one in the dashboard or via the API.[/yellow]")
        return
    stats = local_store.stats()
    per_project = stats.get("per_project", {})
    table = Table(title="Projects", header_style="bold cyan")
    table.add_column("Name", style="bold")
    table.add_column("Slug", style="dim")
    table.add_column("Status")
    table.add_column("Repo")
    table.add_column("Tasks", justify="right")
    for p in rows:
        status_color = {"active": "green", "paused": "yellow", "archived": "dim"}.get(p["status"], "white")
        table.add_row(
            p["name"],
            p["slug"] or "—",
            f"[{status_color}]{p['status']}[/{status_color}]",
            p["repo_path"] or "—",
            str(per_project.get(p["id"], 0)),
        )
    console.print(table)


@app.command("tasks")
def tasks_cmd(
    project: str = typer.Option(None, "--project", "-p", help="Filter by project id"),
    status: str = typer.Option(None, "--status", "-s", help="Filter by status"),
):
    """List Work-layer tasks."""
    from agentos.storage import file_store as local_store

    rows = local_store.list_tasks(project_id=project, status=status)
    if not rows:
        console.print("[yellow]No tasks match.[/yellow]")
        return
    table = Table(title="Tasks", header_style="bold cyan")
    table.add_column("id", style="dim", max_width=12)
    table.add_column("Title", style="bold")
    table.add_column("Status")
    table.add_column("Assignee")
    table.add_column("Priority")
    status_colors = {
        "done": "green", "in_progress": "cyan", "blocked": "yellow",
        "review": "purple", "ready": "blue", "cancelled": "red", "backlog": "white",
    }
    for t in rows:
        sc = status_colors.get(t["status"], "white")
        table.add_row(
            t["id"][:8],
            t["title"],
            f"[{sc}]{t['status']}[/{sc}]",
            t["assignee"] or "—",
            t["priority"],
        )
    console.print(table)


@app.command("task-add")
def task_add_cmd(
    title: str = typer.Argument(..., help="Task title"),
    project: str = typer.Option(..., "--project", "-p", help="Project id"),
    description: str = typer.Option(None, "--description", "-d", help="Task description"),
    assignee: str = typer.Option(None, "--assignee", "-a", help="Agent name or 'human'"),
    priority: str = typer.Option("medium", "--priority", help="high / medium / low"),
):
    """Create a task in a project."""
    from agentos.storage import file_store as local_store
    from agentos.storage.task_store import Task

    if not local_store.get_project(project):
        console.print(f"[red]No project found: {project}[/red]")
        raise typer.Exit(1)
    task = Task(
        project_id=project,
        title=title,
        description=description,
        assignee=assignee,
        priority=priority,
    )
    local_store.create_task(task)
    console.print(f"[green]Created task[/green] [cyan]{task.id[:8]}[/cyan]: {title}")


@app.command("pause")
def pause_cmd(reason: str = typer.Argument("paused via CLI", help="Why you're pausing")):
    """Halt autonomous sprint execution (kill switch)."""
    from agentos.core import killswitch
    killswitch.pause(reason)
    console.print(f"[bold red]⏸ Paused[/bold red] — {reason}")
    console.print("[dim]Resume with: agentos resume[/dim]")


@app.command("resume")
def resume_cmd():
    """Clear the pause flag and allow autonomous work again."""
    from agentos.core import killswitch
    killswitch.resume()
    console.print("[bold green]▶ Resumed[/bold green]")


@app.command("sprint")
def sprint_cmd(
    sprint_id: str = typer.Argument(..., help="Sprint id to execute"),
    mode: str = typer.Option(None, "--mode", "-m", help="manual / semi / full"),
    max_tasks: int = typer.Option(None, "--max-tasks", help="Cap tasks this run"),
):
    """Run a sprint autonomously (dispatch ready tasks → QA → advance)."""
    from agentos.core import sprint_executor

    console.print(f"[bold]Executing sprint[/bold] [cyan]{sprint_id}[/cyan]")
    with console.status("[cyan]Running tasks…[/cyan]"):
        res = sprint_executor.execute_sprint(sprint_id, mode=mode, max_tasks=max_tasks)
    for o in res.processed:
        color = {"done": "green", "review": "yellow", "blocked": "red"}.get(o.final_status, "white")
        console.print(f"  [{color}]{o.final_status}[/{color}] {o.title} [dim]({o.agent or '—'})[/dim]")
    console.print(
        f"\n[dim]Processed {len(res.processed)} tasks · {res.stopped_reason} · "
        f"cost ${res.total_cost_usd:.4f}[/dim]"
    )


@app.command("inbox")
def inbox_cmd(
    answer_id: str = typer.Option(None, "--answer", help="Inbox item id to answer"),
    text: str = typer.Option(None, "--text", help="Your answer text"),
):
    """List open agent questions, or answer one (--answer <id> --text '...')."""
    from agentos.core import ask_human
    from agentos.storage import file_store as local_store

    if answer_id:
        if not text:
            console.print("[red]--text required when answering[/red]")
            raise typer.Exit(1)
        result = ask_human.answer_question(answer_id, text)
        if result.get("error"):
            console.print(f"[red]{result['error']} (id: {answer_id})[/red]")
            raise typer.Exit(1)
        console.print("[green]Answered.[/green]")
        if result.get("resumed_task"):
            console.print(f"[dim]Task {result['resumed_task'][:8]} re-readied for next sprint pass.[/dim]")
        return

    items = local_store.list_inbox("open")
    if not items:
        console.print("[green]Inbox empty — no open questions.[/green]")
        return
    table = Table(title="Inbox — open agent questions", header_style="bold cyan")
    table.add_column("ID", style="dim", max_width=10)
    table.add_column("From")
    table.add_column("Kind")
    table.add_column("Question")
    for i in items:
        table.add_row(i["id"][:8], i.get("from_agent") or "—", i["kind"], i["prompt"][:60])
    console.print(table)
    console.print("[dim]Answer with: agentos inbox --answer <id> --text '...'[/dim]")


@app.command("tokens")
def tokens_cmd():
    """Show token usage analytics from ~/.claude/projects transcripts."""
    from agentos.token_analytics import aggregator

    with console.status("[cyan]Scanning transcripts…[/cyan]"):
        agg = aggregator.aggregate()
    t = agg["totals"]
    console.print("\n[bold]Token usage[/bold] — [dim]API-equivalent (your Max plan covers this)[/dim]")
    console.print(f"  Sessions: {t['sessions']:,}   Messages: {t['messages']:,}")
    console.print(f"  Total tokens: {t['total_tokens']:,}   Cache hit: {t['cache_hit_pct']}%")
    console.print(f"  API-equivalent cost: [bold]${t['cost_usd']:,.2f}[/bold]\n")

    table = Table(title="By project", header_style="bold cyan")
    table.add_column("Project")
    table.add_column("API-equiv $", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Sessions", justify="right")
    for p in agg["by_project"][:10]:
        table.add_row(p["name"], f"${p['cost_usd']:,.2f}",
                      f"{p['input']+p['output']:,}", str(p["sessions"]))
    console.print(table)


@app.command("sync-tasks")
def sync_tasks_cmd(
    project: str = typer.Argument(..., help="Project slug (must have task_store set in settings)"),
):
    """List tasks from a project's configured backend (local/Linear)."""
    from agentos.storage import store_factory

    backend = store_factory.backend_name(project)
    console.print(f"Project [cyan]{project}[/cyan] → backend: [bold]{backend}[/bold]")
    try:
        store = store_factory.store_for(project)
        tasks = store.list_tasks(project_id=project) if backend == "linear" else store.list_tasks()
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]Could not reach {backend} backend:[/red] {e}")
        raise typer.Exit(1)

    if not tasks:
        console.print("[yellow]No tasks returned.[/yellow]")
        return
    table = Table(title=f"{project} tasks ({backend})", header_style="bold cyan")
    table.add_column("Title")
    table.add_column("Status")
    table.add_column("Assignee")
    for t in tasks[:50]:
        table.add_row(t.get("title", "—"), t.get("status", "—"), t.get("assignee") or "—")
    console.print(table)


@app.command("notify-test")
def notify_test_cmd():
    """Fire a test notification to verify your channels work."""
    from agentos.notify import notifier
    res = notifier.notify("sprint_completed", "Test", "AgentOS notifications are working ✅")
    if res["sent"]:
        console.print(f"[green]Sent via:[/green] {', '.join(res['sent'])}")
    else:
        console.print("[yellow]Nothing sent.[/yellow] Check config/notifications.yaml "
                      "(channels enabled? trigger routed?).")


@app.command("onboard")
def onboard_cmd(
    slug: str = typer.Argument(..., help="Project slug from config/projects.yaml"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be discovered; write nothing"),
    curate: bool = typer.Option(False, "--curate", help="Dispatch the librarian to distill sources into memory/ (costs a run)"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite already-staged sources"),
):
    """Discover a project's existing memory and stage it for curation (non-destructive)."""
    from agentos.core import onboard as ob

    try:
        plan = ob.discover(slug)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    console.print(f"[bold]Onboard {slug}[/bold]  workspace=[cyan]{plan.workspace or '—'}[/cyan]")
    console.print(f"[dim]repo:[/dim] {plan.repo_path or '—'}")
    if not plan.sources:
        console.print("[yellow]No source memory found (repo docs or central store). "
                      "Mostly a registration — nothing to curate.[/yellow]")
    for s in plan.sources:
        console.print(f"  • {s.label}  [dim]({len(s.text)} chars)[/dim]")

    if dry_run:
        console.print("[dim]dry-run — nothing written.[/dim]")
        return

    written = ob.scaffold(plan, overwrite=overwrite)
    for p in written:
        console.print(f"  [green]staged →[/green] {p}")

    # Work layer: create the dashboard project + migrate its tasks (all statuses).
    ob.ensure_work_project(slug)
    tres = ob.import_tasks(slug)
    if tres.get("reason"):
        console.print(f"  [green]work layer:[/green] project ready · tasks: {tres['reason']}")
    else:
        bys = ", ".join(f"{k}:{v}" for k, v in sorted(tres["by_status"].items())) or "none"
        console.print(f"  [green]work layer:[/green] project ready · imported {tres['imported']} task(s), "
                      f"{tres['skipped']} already present [{bys}]")

    if curate and plan.sources:
        console.print("[dim]Dispatching librarian to curate…[/dim]")
        console.print(ob.curate(plan))
    elif plan.sources:
        console.print(f"[green]Staged.[/green] Review ~/agentos/projects/{slug}/sources/, "
                      f"then run [bold]agentos onboard {slug} --curate[/bold] (or curate by hand into memory/).")


@app.command("brief")
def brief_cmd(
    no_notify: bool = typer.Option(False, "--no-notify", help="Skip the macOS notification"),
    quiet: bool = typer.Option(False, "--quiet", help="Don't print the briefing body"),
):
    """Generate today's daily update (digest → file + macOS notification)."""
    import datetime
    from agentos.core import briefing
    from agentos.notify import notifier

    date_str = datetime.date.today().isoformat()
    text = briefing.build_briefing(date_str=date_str)
    path = briefing.write_briefing(text, date_str=date_str)
    console.print(f"[green]Daily update →[/green] {path}")
    if not no_notify:
        notifier.push("Daily update ready", f"{date_str} — open {path.name}")
    if not quiet:
        console.print(text)


@app.command("weekly-plan")
def weekly_plan_cmd(
    apply_blocks: bool = typer.Option(False, "--apply", help="Create calendar events for approved blocks"),
    dry_run: bool = typer.Option(False, "--dry-run", help="With --apply: print intended gog actions, create nothing"),
    show_status: bool = typer.Option(False, "--status", help="Show this week's plan/proposal status"),
    week: str = typer.Option(None, "--week", "-w", help="ISO week (e.g. 2026-W26); default = current week"),
):
    """Chief-of-staff weekly plan — deterministic, approval-gated calendar apply + status.

    The plan itself is generated by the chief-of-staff agent session (run_prompt.md), not here.
    This command only applies blocks you've marked `status: approved` in the proposals file.
    """
    from agentos.core import weekly_plan

    wk = week or weekly_plan.current_week()
    if apply_blocks:
        try:
            report = weekly_plan.apply(wk, dry_run=dry_run)
        except FileNotFoundError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)
        if dry_run:
            n = len(report["planned"])
            console.print(f"[yellow]Dry run — {n} approved block(s) would be created:[/yellow]")
            for c in report["planned"]:
                console.print(f"  {c}")
        else:
            console.print(
                f"[green]Created {len(report['created'])}[/green] · "
                f"skipped {len(report['skipped_already_created'])} already-created · "
                f"[red]{len(report['failed'])} failed[/red]"
            )
            for pid, err in report["failed"]:
                console.print(f"  [red]✗ {pid}[/red] — {err}")
        return

    # default / --status: show this week's summary
    try:
        s = weekly_plan.status_summary(wk)
    except FileNotFoundError:
        console.print(f"No plan for {wk} yet — run the chief-of-staff weekly planner first.")
        raise typer.Exit(0)
    console.print(f"[bold]{s['week']}[/bold] — {s['total']} block(s): {s['by_status']}")
    console.print(f"  plan:      {s['plan_path']}")
    console.print(f"  proposals: {s['proposals_path']}")
    console.print(
        "Approve blocks (set [bold]status: approved[/bold]) in the proposals file, then:\n"
        "  [bold]agentos weekly-plan --apply --dry-run[/bold]   (preview)\n"
        "  [bold]agentos weekly-plan --apply[/bold]             (create)"
    )


@app.command("day-end")
def day_end_cmd(
    date: str = typer.Option(None, "--date", "-d", help="Date YYYY-MM-DD; default = today"),
    no_notify: bool = typer.Option(False, "--no-notify", help="Skip the macOS notification"),
    quiet: bool = typer.Option(False, "--quiet", help="Don't print the file paths"),
):
    """Evening shutdown — write today's review + tomorrow's top-3 (chief-of-staff)."""
    import datetime
    from agentos.core import day_end
    from agentos.notify import notifier

    date_str = date or datetime.date.today().isoformat()
    res = day_end.run_day_end(date_str)
    if res.get("errors"):
        console.print(f"[yellow]Shutdown finished with warnings:[/yellow] {res['errors']}")
    console.print(f"[green]Shutdown →[/green] {res['shutdown_path']}")
    console.print(f"[green]Tomorrow →[/green] {res['tomorrow_path']}")
    if not no_notify:
        notifier.push("Daily shutdown ready", f"{date_str} — {res['today_count']} block(s) today")
    if not quiet:
        console.print(
            f"[dim]{res['today_count']} block(s) today, {res['tomorrow_count']} tomorrow.[/dim]"
        )


@app.command("plan-project")
def plan_project_cmd(
    slug: str = typer.Argument(..., help="Project slug (from config/projects.yaml)"),
    goal: str = typer.Option(..., "--goal", "-g", help="The project goal to decompose"),
):
    """Decompose a project goal into phases (sprints) of real tasks, via the planner."""
    from agentos.core import plan_project as pp

    console.print(f"[dim]Planning [bold]{slug}[/bold] with the planner agent…[/dim]")
    try:
        res = pp.plan_project(slug, goal)
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]plan-project failed:[/red] {e}")
        raise typer.Exit(1)
    console.print(f"[green]Planned {res['total_tasks']} task(s) across {len(res['phases'])} phase(s):[/green]")
    for i, ph in enumerate(res["phases"], 1):
        console.print(f"  {i}. {ph['name']}  → sprint [dim]{ph['sprint_id'][:8]}[/dim] · {ph['tasks']} tasks")
    if res["phases"]:
        first = res["phases"][0]["sprint_id"]
        console.print(f"\nReview on the Board, then run a phase: [bold]agentos sprint {first[:8]} --mode semi[/bold]")


@app.command("sync-projects")
def sync_projects_cmd():
    """Create work-layer project records for any project in config/projects.yaml that's
    missing, so they appear in the dashboard Projects / Tasks / Board."""
    from agentos.core import config
    from agentos.storage import file_store as local_store
    from agentos.storage.task_store import Project

    existing = {p["slug"]: p for p in local_store.list_projects()}
    created = updated = 0
    for slug, cfg in config.projects().items():
        want_repo = cfg.get("repo_path")
        want_desc = f"workspace: {cfg.get('workspace') or 'platform'}"
        if slug not in existing:
            local_store.create_project(Project(
                name=slug, slug=slug, repo_path=want_repo, description=want_desc,
            ))
            console.print(f"  [green]+[/green] {slug}")
            created += 1
            continue
        # Reconcile repo_path from config (stale paths after a split/migration
        # route worktrees to the wrong repo — root cause of past mis-rooted
        # worktree incidents).
        cur = existing[slug]
        if (cur.get("repo_path") or "") != (want_repo or ""):
            local_store.update_project(slug, repo_path=want_repo, description=want_desc)
            console.print(f"  [yellow]~[/yellow] {slug}: repo_path → {want_repo}")
            updated += 1
    console.print(f"Synced [bold]{created}[/bold] new, reconciled [bold]{updated}[/bold]; "
                  f"{len(existing)} already present.")


@app.command("link")
def link_cmd():
    """Regenerate the symlink farm: projects/<slug> → repo_path for every split
    project (repo outside the monorepo). Skips projects whose memory still lives
    centrally (a real directory) — migrate memory into the repo first."""
    from pathlib import Path

    import os

    from agentos.core import config

    # Projects that still live inside a legacy monorepo checkout (pre-split) get
    # no farm entry. Set AGENTOS_LEGACY_MONOREPO to that checkout's path if you're
    # migrating from one; otherwise this defaults to a path that matches nothing.
    monorepo = Path(
        os.environ.get("AGENTOS_LEGACY_MONOREPO", str(Path.home() / ".agentos-legacy-monorepo"))
    ).expanduser().resolve()
    linked = skipped = 0

    def ensure(entry: Path, repo: Path, slug: str) -> None:
        nonlocal linked, skipped
        if entry.is_symlink():
            if entry.resolve() == repo.resolve():
                return
            entry.unlink()
        elif entry.exists():
            console.print(f"  [yellow]skip[/yellow] {slug}: real dir at {entry} — "
                          f"move its memory into {repo} first")
            skipped += 1
            return
        entry.symlink_to(repo)
        console.print(f"  [green]link[/green] {entry.relative_to(config.AGENTOS_ROOT)} → {repo}")
        linked += 1

    for slug, cfg in config.projects().items():
        repo = Path(cfg.get("repo_path", "")).expanduser()
        if not repo.exists() or monorepo in repo.resolve().parents:
            continue  # unsplit project (or missing repo) — no farm entry
        # machine farm: stable memory_path / tooling target
        ensure(config.AGENTOS_ROOT / "projects" / slug, repo, slug)
        # browse farm: grouped by workspace for humans
        ws = cfg.get("workspace")
        if ws:
            ensure(config.AGENTOS_ROOT / "workspaces" / ws / slug, repo, slug)
    console.print(f"Farm up to date: {linked} (re)linked, {skipped} skipped.")


work_app = typer.Typer(name="work", help="Work-layer (git-backed tasks) maintenance.", no_args_is_help=True)
app.add_typer(work_app, name="work")


@work_app.command("migrate")
def work_migrate():
    """Migrate the legacy work.sqlite store into the tracked work/ tree (idempotent)."""
    from agentos.storage import file_store

    res = file_store.migrate_from_sqlite()
    if res.get("reason") == "no work.sqlite":
        console.print("[yellow]Nothing to migrate[/yellow] — no work.sqlite found.")
        return
    console.print(
        "Migrated → "
        f"projects: [bold]{res['migrated_projects']}[/bold], "
        f"sprints: [bold]{res['migrated_sprints']}[/bold], "
        f"tasks: [bold]{res['migrated_tasks']}[/bold], "
        f"inbox: [bold]{res['migrated_inbox']}[/bold] "
        f"([dim]{res['skipped']} skipped[/dim])"
    )


@app.command("version")
def version():
    """Show AgentOS version."""
    from agentos import __version__
    console.print(f"agentos [bold]{__version__}[/bold]")


if __name__ == "__main__":
    app()
