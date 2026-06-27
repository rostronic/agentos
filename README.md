# AgentOS

**Multi-agent orchestration on Claude.** AgentOS is a model-agnostic agentic
operating system — an orchestrator, a roster of specialist agents, multi-agent
workflows, autonomous sprints, layered memory, and a fully-local mission-control
dashboard. One place to dispatch agents, run multi-step workflows, track spend and
tokens, and execute autonomous sprints — across any number of projects.

By **[Robert Ostronic](https://github.com/rostronic)**. Open source under
Apache-2.0 (see `LICENSE`).

It is **provider-agnostic by design**: Claude is the first-class backend (subscription
*or* API), but nothing in the core depends on a specific provider — the memory tiers,
work layer, and dashboard are all written against a thin provider interface.

---

## Why this exists

Running one Claude agent is easy. Running **many** — scoped to the right project,
with the right memory loaded, inside budget, with a kill switch, and a record of
everything that happened — is the actual engineering problem. AgentOS is that
substrate: deterministic memory assembly, per-project workspace isolation, budget
gates on every dispatch, worktree isolation for code tasks, and local-only
observability you can read without any cloud account.

## Marquee features

| Feature | What it does | Walkthrough |
|---|---|---|
| **Project onboarding** | Register a project, discover its existing docs, and dispatch agents that load *that* project's memory tier. | [docs/projects-quickstart.md](docs/projects-quickstart.md) |
| **Layered memory** | Facts load broad → narrow (global → workspace → project → per-agent), deterministically, with per-tier budgets. | [docs/memory-quickstart.md](docs/memory-quickstart.md) · [docs/workspaces.md](docs/workspaces.md) |
| **Daily briefing** | One offline morning digest (weather, plan, deadlines, pipeline health, tasks, runs, inbox) — zero model calls. | [docs/daily-briefing.md](docs/daily-briefing.md) |
| **Mission-control dashboard** | Fully-local UI: runs, kanban board, projects, inbox, live SSE feed — binds to `127.0.0.1` only. | [Dashboard](#dashboard) |
| **Cost & token analytics** | Per-run budget caps + a `/tokens` view that parses your local Claude transcripts and a `/insights` view of session quality. | [Cost & token analytics](#cost--token-analytics) |
| **Autonomous sprints** | Run a sprint's ready tasks → QA → advance, with budget caps, a kill switch, and approval gates. | [Autonomous sprints](#autonomous-sprints) |

## Quickstart

```bash
# 1. Clone
git clone https://github.com/rostronic/agentos.git ~/agentos
cd ~/agentos

# 2. First-run setup — interactive wizard. Writes config/{user,settings,budgets}.yaml
#    and checks your provider (the claude CLI or an ANTHROPIC_API_KEY).
cd orchestrator
pip install -e ".[dev]"     # editable install + test/lint extras
agentos init
#    non-interactive, for a scripted setup:
#    agentos init --name "Your Name" --email you@example.com --provider claude_code --yes

# 3. If using claude_code (the default), log Claude Code in once — runs on your
#    Max/Pro subscription, no per-token charge:
claude                       # then type: /login

# 4. Verify
agentos --help
agentos version
agentos agents               # lists the registered specialist agents

# 5. Dispatch your first agent
agentos dispatch researcher "find 3 sources on the Antikythera mechanism"
agentos runs                 # the run that just executed
agentos budget               # today's spend vs daily cap

# 6. Run a multi-agent workflow
agentos workflows
agentos run deep-research --arg topic="quantum computing"

# 7. Open the dashboard (fully local — no cloud, no accounts)
agentos serve                # → http://127.0.0.1:8787
```

`config/settings.yaml` is **required** — it sets your provider and per-project
permissions; `agentos init` creates it from the template. If you prefer an isolated
environment, create a venv first (`python -m venv .venv && source
.venv/bin/activate`) before `pip install -e .`.

## Provider setup (subscription vs API vs local)

AgentOS runs Claude inference through a configurable **provider**, set as
`default_provider` in `config/settings.yaml`:

| `default_provider` | How it bills | What you need |
|---|---|---|
| **`claude_code`** (default) | Your **Max/Pro subscription** — no per-token charge | the `claude` CLI on PATH, logged in (`/login`) |
| `claude_api` | Metered **Anthropic API**, pay per token | `ANTHROPIC_API_KEY` in `config/credentials/.env` |
| `ollama` | **Local, free** — for testing the plumbing | a running [Ollama](https://ollama.com) server |

Most people want `claude_code` — it uses what you already pay for. For the metered
API, copy `config/credentials/.env.example` → `.env`, add `ANTHROPIC_API_KEY`, and
set `default_provider: claude_api`. New to any of this? The
**[FAQ](docs/FAQ.md)** answers "which provider," the `401`/re-login prompt, and
first-run timing.

## Autonomous sprints

`agentos sprint <id>` (or "Run Sprint" in the dashboard) runs a sprint's ready
tasks autonomously: it picks the highest-priority ready task whose dependencies are
done, dispatches its assigned agent, runs QA (with bounded retries), and advances
the task's status. It stops to ask you — via the **Inbox** — when an agent blocks or
QA can't pass. Answering re-readies the task for the next pass.

```bash
agentos sprint <sprint_id> --mode full     # run ready tasks → QA → advance
agentos inbox                              # agent questions waiting on you
agentos inbox --answer <id> --text "Use Stripe"
agentos pause                              # kill switch (agentos resume to continue)
```

Safety rails, all enforced **before each dispatch**:

- **Budget cap** — `daily_usd` / `per_run_usd` in `config/budgets.yaml`
- **Kill switch** — `agentos pause` / dashboard "Halt all agents"
- **Task limits** — max tasks per run, max QA retries
- **Approval modes** (per project in `settings.yaml`): `manual`/`semi` stop at
  `review` for your sign-off; `full` auto-advances to `done`
- **Worktree isolation** — code tasks run in a fresh `git worktree`

## Dashboard

`agentos serve` starts a fully-local mission-control at `http://127.0.0.1:8787`:

- **No cloud, no accounts** — reads the orchestrator's sqlite directly
- **Live updates via SSE** — runs appear and update in real time
- **Views**: Dashboard (KPIs), Runs (+ event timeline), Projects, Tasks, **Board**
  (kanban — drag a card to change status), Inbox, Pipelines, Agents, Workflows (run
  them from the UI), **Token usage**, **Session insights**, Briefings, Live feed
- Binds to `127.0.0.1` only — your run history never leaves the machine

**First run:** the dashboard is empty until you dispatch an agent or run a workflow
— a welcome banner on the home view shows the exact commands to populate it. If the
page shows "Could not reach the API," start it with `agentos serve` in its own
terminal so it persists.

## Cost & token analytics

AgentOS treats spend as a first-class signal, not an afterthought:

- **Budget gates** — every dispatch checks `daily_usd` / `per_run_usd`
  (`config/budgets.yaml`); `agentos budget` shows today's usage vs cap, and the
  cap is enforced *before* the model is called.
- **`/tokens`** (CLI `agentos tokens`) — parses your local
  `~/.claude/projects` transcripts into per-day, per-project, per-model token
  usage. On `claude_code` it surfaces token volume rather than dollars, since the
  subscription has no per-token charge.
- **`/insights`** — mirrors the Claude Code usage-data report: session outcomes,
  friction points, and quality signals, parsed locally.

Everything is computed from files already on your machine — these views make **no**
model calls and need no account.

## Claude Desktop integration

The orchestrator exposes itself to Claude Desktop as an MCP server with 7 tools:
`list_agents`, `list_workflows`, `dispatch`, `run_workflow`, `get_run`,
`recent_runs`, `budget_status`.

1. Open `~/Library/Application Support/Claude/claude_desktop_config.json`
2. Merge in the `agentos` block from `config/claude-desktop-mcp.json.example`
   (after copying it and filling in your paths)
3. Restart Claude Desktop and ask: *"list my agents"*

Run the server standalone with `agentos mcp` (Ctrl+C to stop).

## Testing

CI runs `ruff check` + the unit suite on every PR (see
[`.github/workflows/ci.yml`](.github/workflows/ci.yml)). Locally:

```bash
cd orchestrator
pip install -e ".[dev]"

# Unit tests — no API key, no network, no cost
pytest agentos/tests/ -m "not integration"
ruff check .

# Live tests (real model calls, opt-in)
AGENTOS_RUN_INTEGRATION=1 pytest agentos/tests/ -m integration
```

To try a real end-to-end dispatch cheaply, run the shipped demo in
[`examples/example-project/`](examples/example-project/) — the
[projects quickstart](docs/projects-quickstart.md) registers it, runs its tests,
and dispatches an agent that loads its memory.

## Architecture

The authoritative map of what AgentOS is, how it's built, and where it's going lives
in **[docs/DESIGN.md](docs/DESIGN.md)**. In short:

```
agentos/
├── orchestrator/    # Python package (agentos) — CLI, MCP server, providers, work layer
├── agents/          # specialist agent specs (researcher, developer, qa, planner, …)
├── workflows/       # YAML multi-agent recipes
├── dashboard/       # local mission-control UI (single-file SPA + aiohttp API + SSE)
├── config/          # *.example templates — copy and fill in your own values
├── global/          # tier-1 memory: host-neutral identity + working rules
├── workspaces/      # tier-2 memory: personal/ and business/ cross-project facts
├── inbox/           # capture staging — raw memory awaiting curation
├── examples/        # a minimal, runnable example project to copy from
└── docs/            # design, usage, quickstarts, FAQ, roadmap
```

The agent context layer assembles memory **broad → narrow** — global → workspace
(personal | business) → project → per-agent — deterministically and with no model
call, so the right facts load at the right scope and work facts never leak into
personal projects. See [docs/workspaces.md](docs/workspaces.md) and
[docs/memory-quickstart.md](docs/memory-quickstart.md).

## Docs

- **[docs/projects-quickstart.md](docs/projects-quickstart.md)** — register a
  project and dispatch an agent scoped to it.
- **[docs/memory-quickstart.md](docs/memory-quickstart.md)** — put a fact at the
  right scope and prove it loaded.
- **[docs/workspaces.md](docs/workspaces.md)** — the four-tier memory model and the
  personal/business split.
- **[docs/daily-briefing.md](docs/daily-briefing.md)** — configure, run, and
  schedule `agentos brief`.
- **[docs/USAGE.md](docs/USAGE.md)** — the verified command walkthrough.
- **[docs/FAQ.md](docs/FAQ.md)** — provider choice, `401`/re-login, first-run timing,
  where project context goes.
- **[docs/DESIGN.md](docs/DESIGN.md)** — architecture & vision.

## Project meta

- **[AGENTS.md](AGENTS.md)** / **[CLAUDE.md](CLAUDE.md)** — the agent-facing entry
  point: the global memory tier every session loads (`CLAUDE.md` is the Claude shim
  that imports the provider-neutral `AGENTS.md`).
- **[BUGS.md](BUGS.md)** — every bug found during the build, its root cause, fix,
  and the regression test that keeps it from returning.

## Credits

Created and maintained by **Robert Ostronic**. The `/tokens` view's JSONL-parsing
approach was inspired by
[nateherkai/token-dashboard](https://github.com/nateherkai/token-dashboard); the
`/insights` view mirrors the Claude Code usage-data report format.
