# Using AgentOS

Three ways to drive it: **CLI** (`agentos …`), the **dashboard** (`agentos serve`),
and **Claude Desktop** (MCP). All run locally on your subscription by default
(`claude_code`), budget-gated.

## Setup (once per shell)
```bash
source ~/agentos/orchestrator/.venv/bin/activate   # puts `agentos` on PATH
agentos --help
```
Inference auth: `claude` then `/login` (Max/Pro subscription, no per-token cost).
If auth lapses you'll see `401` on dispatches — just re-login.

## The 3 surfaces

### CLI
```bash
agentos agents                     # list the 8 specialist agents
agentos dispatch <agent> "task"    # run one agent  (e.g. agentos dispatch researcher "find 3 sources on X")
agentos workflows                  # list multi-agent recipes
agentos run <workflow> --arg k=v   # run a workflow (e.g. agentos run deep-research --arg topic="quantum")
agentos runs                       # recent runs
agentos budget                     # today's spend vs cap
```

### Dashboard  → http://127.0.0.1:8787
```bash
agentos serve                      # start it (run in your own terminal so it persists)
```
Tabs: Dashboard (KPIs) · Runs · Projects · Tasks · **Board** (kanban, drag to set status) ·
Inbox · Pipelines · Agents · Workflows (run from UI) · Token usage · Session insights ·
**Briefings** (your daily update) · Live feed.

### Claude Desktop (MCP)
Already wired. In a chat: *"list my agents"*, *"run the deep-research workflow on X"*,
*"dispatch the developer on the **example-shop** project to …"* (naming the project loads its
memory). 7 tools: list_agents, list_workflows, dispatch, run_workflow, get_run, recent_runs,
budget_status.

## Agents (8)
researcher · developer · qa · planner · analyst · critic · librarian · scribe.
Each is a `agents/<name>/agent.md` spec (model, tools, system prompt).

## Workflows (recipes)
`deep-research` (research→critique→synthesize) · `ship-feature` (plan→build→qa→doc) ·
`plan-sprint` · `triage-inbox` · `daily-briefing`.

## Work layer (projects · sprints · tasks)
```bash
agentos projects                   # list managed projects (registry: config/projects.yaml)
agentos tasks                      # list tasks
agentos task-add ...               # add a task
agentos sprint <id> --mode full    # autonomously run a sprint's ready tasks → QA → advance
agentos inbox                      # agent questions waiting on you
agentos inbox --answer <id> --text "..."   # answer → re-readies the task
agentos pause   /   agentos resume # kill switch for all agents
```
Safety rails on every dispatch: budget cap, kill switch, task/QA limits, approval modes
(per project in settings.yaml), worktree isolation for code tasks.

## Memory
Layered: **global** (always) → **workspace** (personal/business) → **project** → per-agent.
It's injected automatically into every dispatch for the named project.
```bash
agentos onboard <slug>             # discover a project's docs (preview)
agentos onboard <slug> --curate    # librarian distills them into project memory
```
Registry of projects + workspaces: `config/projects.yaml`.

## Daily briefing
```bash
agentos brief                      # generate today's digest now (also runs 8 AM via launchd)
```
Writes `~/agentos/briefings/<date>.md`, fires a macOS notification, and shows in the
dashboard **Briefings** tab. Covers weather · pipelines · tasks · runs · inbox · insight · Spanish.

## Ops / analytics
```bash
agentos tokens                     # token usage analytics
agentos cron                       # scheduled jobs
agentos notify-test                # test notifications
agentos pause / resume             # halt / resume all agents
```

## Where things live
- Code: `orchestrator/agentos/` · Agents: `agents/` · Workflows: `workflows/` · Dashboard: `dashboard/index.html`
- Config: `config/` (settings.yaml, budgets.yaml, projects.yaml, schedules.yaml)
- Memory: `global/`, `workspaces/`, `projects/<slug>/memory/`, capture staging in `inbox/`
- Docs: `docs/` (DESIGN, manage_memory_plan, onboarding, github-strategy, capability-roadmap, this file)
