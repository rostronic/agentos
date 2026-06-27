# AgentOS — Architecture & Vision

> Umbrella design document. The authoritative, scannable map of what AgentOS is,
> how it is built, and where it is going. Companion plans live alongside this file:
> the memory subsystem in [./manage_memory_plan.md](./manage_memory_plan.md) and
> future phases in [./capability-roadmap.md](./capability-roadmap.md).

---

## 1. What AgentOS is

**AgentOS is a model-agnostic agentic operating system** — a single orchestrator
that turns goals into work done by a roster of specialist agents, coordinated
through multi-agent workflows, executed under hard budget and safety rails, and
observed through a fully-local mission-control dashboard. It runs Claude inference
either through a Max/Pro subscription (via the `claude` CLI) or the metered
Anthropic API, and is built so that no subsystem assumes a specific model or
provider. The aim is a durable, private "OS" for getting real project work done by
agents — not a chatbot wrapper.

### Design principles

- **Model- and provider-agnostic.** Agents declare a `preferred` model and a
  `fallback` chain; a router resolves the actual provider at dispatch time.
  Providers sit behind one interface (`providers/base.py`), so Claude (subscription
  or API), OpenAI, Ollama, and runtime adapters are interchangeable. Memory files
  are host-neutral markdown a future non-Claude agent can read directly.
- **Local-first and private.** The dashboard binds to `127.0.0.1` only and reads
  the orchestrator's SQLite directly — no Convex, no cloud, no accounts. Run
  history, token analytics, and memory never leave the machine.
- **Budget-gated.** Every dispatch is checked against `config/budgets.yaml`
  (daily USD, per-run USD, token and wall-time caps, workflow nesting depth) before
  it runs. Overruns block the run and surface in the inbox.
- **Human-in-the-loop.** Autonomous sprints stop and ask via the inbox when an
  agent blocks or QA can't pass; approval modes (`manual`/`semi`/`full`) gate
  whether work auto-advances; a kill switch halts everything. Production deploys
  require explicit per-deploy approval.
- **Bug → fix → regression discipline.** Every bug found during build is logged in
  [../BUGS.md](../BUGS.md), fixed, and locked down with a named regression test so
  it can't silently return. (See the two entries there for the pattern.)

---

## 2. Architecture & subsystems

The orchestrator is a Python package (`orchestrator/agentos`) with three entry
points and a set of core engines, providers, and storage adapters. Everything
below is built and shipped (Phases 0–10).

### Entry points (`entrypoints/`)

- **CLI (`cli.py`)** — the primary surface: `agentos dispatch`, `run`, `agents`,
  `workflows`, `runs`, `budget`, `sprint`, `inbox`, `pause`/`resume`, `serve`,
  `mcp`, `cron`, `sessions`, `task-add`, `sync-tasks`, and more.
- **MCP server (`mcp_server.py`)** — a FastMCP stdio server exposing AgentOS to
  Claude Desktop with 7 tools: `list_agents`, `list_workflows`, `dispatch`,
  `run_workflow`, `get_run`, `recent_runs`, `budget_status`.
- **API server (`api_server.py`)** — the aiohttp backend behind `agentos serve`:
  REST + SSE feeding the local dashboard.

### Providers & the runtime axis (`providers/`)

A `Provider` protocol (`base.py`) with one `dispatch()` contract returning a
`DispatchResult` (output + token usage). Concrete providers:

- `claude_code.py` — runs through the `claude` CLI on your Max/Pro **subscription**
  (no per-token charge); the **default**.
- `claude.py` — metered Anthropic **API** (`ANTHROPIC_API_KEY`, pay per token).
- `openai.py`, `ollama.py` — alternate / local-model providers.
- `agentcli_runtime.py`, `hermes_runtime.py` — runtime adapters (the "runtime
  axis": how/where the agent process executes, orthogonal to which model answers).
- `pricing.py` — per-model pricing used for cost accounting.

The **router** (`core/router.py`) picks the provider/model from an agent's
`preferred` + `fallback` chain.

### The 8 agents (`agents/`)

Each agent is a markdown spec with YAML front-matter (`role`, `model.preferred`,
`model.fallback`, `tools`, `temperature`, `max_tokens`) plus a system prompt.

| Agent | Role |
|---|---|
| **researcher** | Fan-out web research, source gathering, cited synthesis |
| **developer** | Writes/edits/tests code, git ops, PRs (never pushes to main) |
| **qa** | Verifies changes against acceptance criteria, finds regressions |
| **planner** | Decomposes goals into task graphs with acceptance criteria |
| **critic** | Adversarial reviewer — tries to refute plans/outputs |
| **analyst** | Data queries, metrics, statistical summaries |
| **scribe** | Docs, ADRs, changelogs, summaries, postmortems |
| **librarian** | Curates memory; surfaces relevant prior context before work |

The **librarian** is also the memory curator (see §6) — there is one curator agent,
not a separate "memory-manager".

### Workflows (`workflows/`)

YAML recipes that chain agents, passing each step's output forward via
`{{steps.<id>.output}}` and `{{inputs.<name>}}` interpolation. Shipped recipes:
`deep-research`, `ship-feature`, `plan-sprint`, `triage-inbox`, `daily-briefing`.
Run with `agentos run <name> --arg key=value`. Loaded/validated by
`core/workflow_loader.py`, executed by `core/workflow_runner.py`.

### Work layer — projects / sprints / tasks (Phase 5)

A structured backlog above raw dispatches: **projects** contain **sprints**, which
contain **tasks** with status (`backlog`/`ready`/`blocked`/`review`/`done`/…),
priority, assignee (agent role or `human`), acceptance criteria, and dependencies.
`plan-sprint` turns a goal into this graph. Task data lives behind a pluggable
store (see below).

### Autonomous sprints + safety rails (Phase 6)

`agentos sprint <id>` (or "Run Sprint" in the dashboard) runs a sprint's ready
tasks autonomously (`core/sprint_executor.py`): pick the highest-priority ready
task whose deps are done → dispatch its agent → run QA with bounded retries →
advance status. Rails enforced **before each dispatch**:

- **Budget cap** (`core/budget.py` ← `config/budgets.yaml`).
- **Kill switch** (`core/killswitch.py`) — `agentos pause` / dashboard "Halt all".
- **Task limits** (`core/limits.py`) — max tasks per run, max QA retries.
- **Approval modes** (per project in `settings.yaml`): `manual`/`semi` stop at
  `review`; `full` auto-advances to `done`.
- **Worktree isolation** (`core/worktree.py`) — code tasks run in a fresh
  `git worktree` under `worktrees/`, never touching the live tree.
- **ask_human / inbox** (`core/ask_human.py`) — when an agent blocks or QA can't
  pass, the sprint stops and posts a question to the inbox. Answering re-readies
  the task (`blocked`→`ready`) so the next pass re-dispatches with the answer in
  context (this exact resume bug is BUGS.md #1).

### Budget (Phase 1)

Pre-dispatch enforcement of `daily_usd`, `per_run_usd`, `per_run_max_tokens`,
`per_run_max_wall_seconds`, and `max_workflow_depth`, with per-project overrides
that stack on the defaults. `agentos budget` shows today's spend vs cap.

### Token analytics + session insights (Phases 8 / 8b)

- **Token analytics (`token_analytics/`)** — parses `~/.claude/projects` JSONL
  transcripts into real cost/token usage. Dedupes by `message.id` to avoid the
  ~2.2× inflation from Claude Code's streaming snapshots (BUGS.md #2). Surfaced at
  the `/tokens` page and `agentos tokens`; `tips_engine.py` derives savings tips.
- **Session insights (`insights/`)** — parses `~/.claude/usage-data` facets+meta
  into outcomes, friction, and quality signals at the `/insights` page.

### Notifications (Phase 9)

`notify/notifier.py` routes events (sprint completed, run failed/repeatedly-failed,
agent blocked, budget 80%/exceeded, run timeout, schedule fired) to channels
(macOS push on by default; Telegram/Discord/email opt-in) per
`config/notifications.yaml`.

### Task stores (Phase 7)

A store factory (`storage/store_factory.py`) selects the backend per project,
all behind a common `task_store.py` interface. Configured per project in
`settings.yaml` (`task_store: file (default) | local | linear`).

**ADR — git-backed tasks (Phase 11, now default):** the Work layer
(projects/sprints/tasks + the ask_human inbox) is persisted as tracked Markdown +
YAML-frontmatter docs under a top-level `work/` tree (one file per entity), via
`storage/file_store.py`. This is now the **default** backend and the source of
truth — it gives tasks the same "travels with the repo" property that project-tier
memory already has (Tier 3 / Memory), so every session can read all tasks from git.
The legacy SQLite store (`local_store.py`) is demoted to a selectable local cache
(`task_store: local`); the **Linear** adapter (`linear_store.py`) remains
selectable (`task_store: linear`). A one-time `agentos work migrate` folds an
existing `work.sqlite` into the `work/` tree (idempotent).

### Cron / scheduling (Phase 10)

`core/cron.py` + `config/schedules.yaml` run workflows on standard cron
expressions (`agentos cron`). A more ambitious cloud-native scheduler migration
is possible but out of scope here.

### Dashboard (Phase 4)

`agentos serve` → fully-local mission-control at `http://127.0.0.1:8787`: KPI
dashboard, Runs (+ event-timeline detail), Agents, Workflows (runnable from the
UI), Tokens, Insights, and a live SSE event feed. SQLite-backed, no cloud.

### Memory (in-flight)

A tiered, host-neutral markdown knowledge base curated by the librarian. The tier
scaffold (`global/`, `workspaces/`, `inbox/`) is already on disk; the orchestrator
load/inject seam, capture, and curation wiring are the current build. Fully
described in §6 and in [./manage_memory_plan.md](./manage_memory_plan.md).

---

## 3. Directory layout (current)

```
~/agentos/
├── AGENTS.md            # Global memory entry (host-neutral); CLAUDE.md @-imports it
├── CLAUDE.md            # Thin Claude shim → @AGENTS.md
├── README.md            # Operator quick-start
├── BUGS.md              # Bug → fix → regression log
├── orchestrator/        # Python package `agentos`
│   └── agentos/
│       ├── entrypoints/ # cli.py, mcp_server.py, api_server.py
│       ├── core/        # router, budget, limits, killswitch, worktree,
│       │                #   sprint_executor, ask_human, cron, *_loader/runner,
│       │                #   run_store, agent_loader, config
│       ├── providers/   # base + claude_code, claude, openai, ollama,
│       │                #   agentcli_runtime, hermes_runtime, pricing
│       ├── storage/     # task_store, local_store, linear_store, store_factory
│       ├── token_analytics/  # jsonl_parser, aggregator, tips_engine
│       ├── insights/    # loader, aggregator
│       ├── notify/      # notifier
│       ├── pipelines/   # loader
│       └── tests/       # unit + integration (incl. named regression tests)
├── agents/              # 8 agent specs (analyst, critic, developer, librarian,
│                        #   planner, qa, researcher, scribe)
├── workflows/           # deep-research, ship-feature, plan-sprint,
│                        #   triage-inbox, daily-briefing
├── config/              # settings.yaml, budgets.yaml, notifications.yaml,
│                        #   schedules.yaml, claude-desktop-mcp.json, credentials/
├── dashboard/           # local mission-control UI (Phase 4)
├── docs/                # this file + plan docs (see §4)
├── global/              # MEMORY: global tier — AGENTS.md + memory/*.md (always loaded)
├── workspaces/          # MEMORY: workspace tier — business/ and personal/
├── inbox/               # MEMORY: capture landing zone (staging, not truth)
├── memory/              # legacy store — per-agent/ (repurposed, see §6);
│                        #   shared/ is an empty duplicate slated for deletion
├── skills/              # memory-curate/ (placeholder for the curator; curation
│                        #   ships as a workflow — see manage_memory_plan.md)
├── tools/               # local MCP child servers (browser, filesystem, git, search)
├── logs/                # run event logs
├── worktrees/           # ephemeral git worktrees for code tasks
├── bin/                 # CLI shims
└── workspaces/          # memory tiers; business/ holds symlinks to ~/dev repos,
                         # personal/ holds the personal projects' files (in-repo)
```

---

## 4. In-flight plans

Two design documents extend this one. Keep cross-links relative so the docs travel
together with the repo:

- **Memory subsystem** → [./manage_memory_plan.md](./manage_memory_plan.md).
  The detailed plan for the tiered memory model, inbox triage, librarian curation,
  and the cleanup of the legacy `memory/` store. Summarized in §6.
- **Capability roadmap** → [./capability-roadmap.md](./capability-roadmap.md).
  The proposal for future phases (11+) — what AgentOS grows into next. Summarized
  in §5.

---

## 5. Phases

All phases below are **built and shipped**. The roadmap doc proposes the next ones
(Phase 11+); nothing past Phase 10 is committed here yet.

| Phase | Status | What it adds |
|------|--------|--------------|
| 0 — Skeleton | ✅ Done | Directory structure, `agentos --help`, 8 agent specs |
| 1 — First dispatch | ✅ Done | Claude provider, `agentos dispatch`, budget enforcement, run log |
| 2 — Workflows | ✅ Done | Workflow runner, `agentos run`, `{{interpolation}}`, JSONL logs |
| 3 — Claude Desktop | ✅ Done | FastMCP server (7 tools), paste-in config |
| 4 — Dashboard | ✅ Done | Fully-local dashboard (aiohttp + SSE + SPA), `agentos serve` |
| 5 — Work layer | ✅ Done | Projects/sprints/tasks, `plan-sprint` workflow |
| 6 — Autonomous sprints | ✅ Done | execute-sprint loop, worktrees, ask_human/inbox, kill switch, approval gates |
| 7 — Pluggable task stores | ✅ Done | Linear adapter + store factory |
| 8 — Token analytics | ✅ Done | Parse `~/.claude/projects` transcripts, `/tokens`, `agentos tokens` |
| 8b — Session insights | ✅ Done | Parse `~/.claude/usage-data`, `/insights` page |
| 9 — Notifications | ✅ Done | Notifier (macOS push), config-driven triggers |
| 10 — Extra workflows + cron | ✅ Done | `triage-inbox`, `daily-briefing`; cron scheduler |
| 11+ | 🔜 Proposed | See [./capability-roadmap.md](./capability-roadmap.md) |

---

## 6. Memory model

AgentOS keeps a **tiered, host-neutral markdown** knowledge base so agents don't
re-derive what's already known. It is provider-agnostic by design: Claude
`@`-imports the same files a future non-Claude agent would read directly. The tier
scaffold is on disk now; the orchestrator wiring and curation are the in-flight
build (the full plan is in [./manage_memory_plan.md](./manage_memory_plan.md)).
The shape:

1. **Global** (`global/`) — host-neutral identity + universal working rules.
   Always loaded. Entry: `global/AGENTS.md`, catalog `global/memory/index.md`
   (identity, file-links, subagents, prod-deploy, worktree-isolation,
   secret-rotation, project-scope).
2. **Workspace** (`workspaces/<name>/`) — cross-project facts for a sphere
   (e.g. **business** vs **personal**). Loaded for the active workspace.
3. **Project** — lives in each project's **own repo** (`AGENTS.md` + `memory/`) so
   it travels with the code. Loaded when cwd is inside the project.
   **Onboarding existing projects from a prior harness's workspace is a deferred
   follow-on** — planned, not done now.
4. **Session** — ephemeral handoffs in a project's `memory/handoffs/`.

**Capture & curation flow.** Raw auto-captured notes land in `inbox/` — *staging,
not truth*. The **librarian** agent (the one curator; not a renamed
"memory-manager") triages each item into a tier, dedupes and fixes stale facts,
promotes the keepers into the right home, and clears processed items. Curation runs
as in-prompt logic inside a `curate-memory` workflow — *not* by calling the harness
`anthropic-skills:consolidate-memory` skill, which is reserved for the
human-in-session (an orchestrator agent can't invoke harness skills). The workflow,
the `agentos memory` CLI, and the disabled-by-default schedule are part of the
in-flight memory work (see [./manage_memory_plan.md](./manage_memory_plan.md)), not
yet shipped.

**Legacy `memory/` cleanup.** The pre-tier store is being retired:
`memory/shared/` is an empty duplicate of the new tiers and is **slated for
deletion**; `memory/per-agent/` is **repurposed for global-scoped agent facts**.

**Routine prudence (not incident recovery).** As ordinary hygiene, eyeball
`global/memory/*.md` and workspace memory files for any real secret *values* before
committing — memory is plaintext markdown in git. This is standard care, not a
response to any past leak.

---

*This document is the umbrella. When a subsystem grows its own design notes, link
them from here and keep this map accurate to the repo.*
