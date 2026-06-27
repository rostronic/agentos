# AgentOS Capability Roadmap (Phases 11+)

This document catalogs **proposed** capabilities that extend AgentOS beyond the
Phases 0–10 already shipped (see [../README.md](../README.md) for the built phase
table). It is forward-looking: nothing here is implemented yet.

The **memory subsystem** is the immediate next brick — it is being built now, with
its own design doc landing alongside this one at
[./manage_memory_plan.md](./manage_memory_plan.md). The
memory tier model that ships with it (the `global/`, `workspaces/{business,personal}/`,
and `inbox/` scaffold already on disk, curated by the existing **librarian**
agent) is a hard dependency for several capabilities below — they are explicitly
marked. The broader architectural context lives in [./DESIGN.md](./DESIGN.md).

What is **already built** and therefore NOT proposed here: the orchestrator + CLI
+ MCP server (7 tools), the 8 specialist agents, the `claude_code`/`claude_api`/
`ollama` providers with native/agentcli/hermes runtimes, the YAML workflow runner,
the work layer (projects/sprints/tasks), autonomous sprints (worktrees,
`ask_human`/inbox, kill switch, approval modes, budget caps), token analytics +
session insights, macOS notifications, the Linear task store, the cron scheduler,
and the local dashboard. Capabilities below either deepen those primitives or add
new ones.

---

## 1. Capability catalog

Merged and de-duplicated from two capability deep-dives. Items are grouped by
domain. **★** marks high-leverage items. Dependencies on the memory subsystem or
a future policy engine are called out inline.

### A. Governance, policy & security

- **Policy / guardrail engine ★** — A rules engine that *enforces* the user's
  operating rules **before** each dispatch and tool call, rather than relying on
  prompt discipline. First rules to codify: prod-deploy only on the literal phrase
  "deploy to prod"; code writes only inside a worktree; stay-in-project scoping;
  ≤2-step secret rotations. *Why it matters:* these are exactly the rules the user
  already carries in memory across their projects; making them
  machine-enforced removes the single biggest class of "agent did the wrong thing"
  risk. *Foundational dependency for many items below.*
- **Capability / permission scoping per agent** — Least-privilege grants over
  tools, filesystem paths, network egress, and providers, declared per agent.
  *Depends on:* policy engine.
- **Immutable audit log** — Append-only record of approvals, prod actions, and
  secret access. *Depends on:* policy engine.
- **Memory sensitivity tiers ★** — A visibility/sensitivity flag
  (public / personal / secret) on every memory fact, controlling which surface a
  fact appears on (e.g. secrets never surface outside a trusted session). Generalizes
  the "MEMORY.md only in the main session" rule. *Depends on:* memory
  subsystem (the tier model is the natural carrier for these flags).
- **Secret broker + `agentos rotate`** — A vault that brokers secrets to agents
  without exposing raw values in logs, plus a built-in rotation command honoring
  the ≤2-CLI-call rotation rule. Pairs with log redaction. *Why it matters:* the
  user does real secret rotations and wants them short and safe; routine prudence
  is to eyeball any `global/`/`workspaces/**/memory/*.md` for real secret *values*
  before committing.
- **Sandboxed / containerized execution** — Run agent code in a constrained
  sandbox so capability scoping is enforced at the OS level, not just by policy.

### B. Quality, evaluation & simulation

- **Agent eval / benchmark harness ★** — Golden tasks + scoring, run on a
  schedule, gating prompt/agent changes so regressions can't ship silently.
- **Output verification primitives** — Reusable building blocks (judge panels,
  self-critique, groundedness checks) any agent or workflow can call. The existing
  `critic` agent is the natural home.
- **Prompt / version management** — Versioned agent prompts with A/B comparison
  and eval-gated promotion. *Depends on:* eval harness.
- **Dry-run / simulation mode** — Preview a workflow's plan and projected cost
  with zero spend. *Why it matters:* lets the user sanity-check a multi-agent run
  against the budget cap before committing real quota.
- **Workflow replay / golden runs** — Record canonical multi-agent runs and
  regression-test behavior against them.
- **Chaos testing ★** — Inject provider failures and rate-limit responses to
  prove the fallback path actually works. *Depends on:* cost-aware router/fallback.
- **Synthetic task generation** — Auto-generate eval cases to broaden coverage.

### C. Orchestration depth

- **DAG workflow engine ★** — Beyond the current linear YAML recipes: fan-out /
  join, conditionals, per-node retries and budgets, and resumable checkpoints so a
  long run can recover instead of restarting.
- **Cost-aware model router + provider fallback ★** — Pick the cheapest model that
  passes evals, escalate on failure, and fall back on rate-limit
  (`claude_code` → `ollama` → `claude_api`). *Why it matters:* the user runs on a
  *subscription* (rate-limited, not dollar-limited) plus local `ollama` at $0 —
  routing should defer or drop to ollama near the rate ceiling. *Depends on:* eval
  harness (for "passes evals") and subscription rate-limit modeling.
- **Cross-project scheduling** — Priorities, queues, and concurrency caps across
  all managed projects so parallel work doesn't blow the rate ceiling.

### D. Triggers & reactivity

- **Unified event bus ★** — Triggers beyond cron: webhooks, file/git-push watch,
  email-in, and dashboard buttons, all able to launch workflows. *Why it matters:*
  turns AgentOS from "runs on a clock" into "reacts to what happens" (a PR opens, a
  transcript lands, an inquiry arrives).
- **Reactive pipelines** — Compose event triggers into multi-stage pipelines.
  *Depends on:* event bus.

### E. Knowledge, context & memory

- **Context compiler / budgeter ★** — Assemble the optimal context window per task
  with explicit token accounting, fighting context rot. Memory is a *store*; this
  is the *assembler*. *Depends on:* memory subsystem.
- **RAG / retrieval over memory + code ★** — Inject the most relevant memory and
  code per task instead of dumping everything. *Depends on:* memory subsystem and
  the codebase index.
- **Codebase index** — Symbol/dependency graph (tree-sitter / LSP) for precise code
  context across the multi-repo workspace.
- **Asset / document ingestion** — Ingest PDFs, images, and transcripts (e.g. photos,
  specs, meeting transcripts) into retrievable memory.
- **Auto-learning loop** — Run postmortems and corrections feed back into memory
  and into new eval cases. *Depends on:* memory subsystem + eval harness.
- **Provenance & staleness decay** — Confidence and age as first-class fields;
  re-verify or prune old claims. *Depends on:* memory subsystem.

### F. Observability & ops / resilience

- **Pipeline health / SLA monitoring ★** — Track scheduled jobs: last-success time,
  failure streaks, and alerting. *Why it matters:* AgentOS ships a cron scheduler
  (Phase 10), but `config/schedules.yaml` ships empty (all entries commented).
  An instance may already run scheduled jobs on an external scheduler that will
  migrate in, so health monitoring matters before — not after — those jobs land here.
- **`agentos doctor` ★** — One command to check config, provider auth, MCP health,
  disk, stale locks, and broken `@`-imports. *Why it matters:* fast triage after a
  laptop swap or a provider hiccup.
- **Snapshot / restore ★** — Git-backed snapshots of `runs.sqlite`, `work.sqlite`,
  and `memory/`. *Why it matters:* explicit laptop-swap insurance for a user who
  has already been through one.
- **Always-on headless deployment** — Run on a server / Pi so cron and heartbeats
  fire when the laptop is closed, with cross-machine state sync.
- **Idempotent cron + dead-letter queue** — Safe re-runs and a parking lot for
  failed jobs so a transient failure doesn't silently vanish.
- **Cost attribution rollups** — Spend by project / workspace / agent.
- **Run replay / time-travel debug** — Step back through a run's events to diagnose
  what an agent did and why.

### G. Human-in-the-loop & interface

- **Two-way notifications** — Reply to a push / Slack / email to answer an inbox
  question, closing the `ask_human` loop without opening the dashboard.
- **Diff / review UI with comments → memory** — Approve diffs from the UI; review
  comments flow back into memory. *Depends on:* memory subsystem.
- **Scheduled digests** — Extend the existing `daily-briefing` workflow into
  configurable digests.
- **Conversational control plane** — Natural-language ops ("what failed last
  night?", "pause <project>").
- **Universal search** — One box across runs, memory, logs, and inbox.
- **Approve-from-UI diffs + mobile / PWA** — Review and approve on the go.

### H. Prior-harness carry-forward (migration risk)

When migrating onto AgentOS from a prior agent harness, these are capabilities
worth preserving so the move is not a downgrade.

- **Heartbeat / proactivity engine ★** — Batched email + calendar + mentions
  checks, quiet hours, an editable `HEARTBEAT.md`, and smart "when to speak"
  judgment. *Depends on:* event bus + memory subsystem.
- **Multi-channel presence** — Slack / Discord / iMessage / WhatsApp / email, each
  channel-aware about what data it may surface. *Depends on:* memory sensitivity
  tiers (channel-aware hygiene).
- **Voice briefings** — TTS / ElevenLabs spoken briefings.
- **Safety reflexes as enforced defaults** — `trash` over `rm`; no deletes/restarts
  without permission; ask before anything leaves the machine. *Depends on:* policy
  engine.
- **"Write it down — no mental notes" discipline** — Enforce that durable facts get
  persisted to memory rather than living only in a session. *Depends on:* memory
  subsystem.

### I. Goals, time & integrations

- **Goal / OKR layer above sprints** — Long-horizon objectives with progress
  rollups over the existing sprint/task layer.
- **Calendar / deadline awareness** — A calendar MCP so agents schedule around the
  human and respect hard deadlines (e.g. an auction date).
- **Recurring rituals as workflows** — Weekly review, retro, grooming as
  first-class scheduled workflows.
- **Connector framework** — GitHub, Firebase/Firestore, Stripe, YouTube, Drive,
  Gmail connectors behind a common interface.
- **Project-metrics ETL → business cockpit ★** — Pull per-project metrics (e.g. Stripe
  revenue + YouTube views, Firestore traffic, lead interest) into the dashboard. *Why it
  matters:* turns mission-control from an ops view into a business cockpit across
  the user's portfolio. *Depends on:* connector framework.
- **Webhook in / out** — Inbound and outbound webhooks (overlaps with the event
  bus on the inbound side).

### J. Cost & resource intelligence

- **Subscription rate-limit modeling ★** — Model remaining `claude_code` quota
  (rate, not dollars) and queue/defer or fall back to $0 `ollama` near the ceiling.
  *Why it matters:* directly matches the user's billing model.
- **Per-project P&L / ROI** — Tie spend to project value.
- **Off-peak scheduling** — Run heavy jobs when rate headroom is largest.

### K. Extensibility & agent ecosystem

- **MCP / tool registry** — Discover, install, and health-check tools. *Why it
  matters:* `tools/` currently holds empty stubs (`browser/`, `filesystem/`, `git/`,
  `search/`); a registry gives them a real lifecycle.
- **Skill & agent SDK + templates** — Scaffolding to author new agents and skills.
- **Agent generator (meta-agent)** — Scaffold an `agent.md` from a spec/template.
- **Capability registry + auto-routing** — Auto-pick the right agent for a task.
- **Agent versioning + import / share / marketplace** — Version and exchange agents.

### L. Self-improvement (meta)

- **AgentOS-improves-AgentOS loop ★** — Mine telemetry (failures, costliest/slowest
  agents, `/insights` friction) to propose framework/agent/prompt improvements and
  auto-file entries in `BUGS.md`. *Depends on:* eval harness + cost attribution +
  auto-learning loop.

---

## 2. Proposed phase plan (Phases 11+)

Dependency-ordered and sized like the README phase table. The memory subsystem is
listed as **Phase 11** because it is in flight now and gates much of what follows.

| Phase | What it adds | Depends on |
|-------|--------------|------------|
| **11 — Memory subsystem** *(in flight)* | Tiered memory (`global` / `workspaces.{business,personal}` / `inbox`), curated by the librarian, with sensitivity flags and provenance fields. See [./manage_memory_plan.md](./manage_memory_plan.md). | — |
| **12 — Policy & guardrail engine ★** | Enforce prod-deploy phrase, worktree-only writes, stay-in-project, ≤2-step rotation, and safety reflexes before each dispatch/tool call; per-agent capability scoping; immutable audit log. | 11 (sensitivity flags) |
| **13 — Cost-aware router + provider fallback ★** | Cheapest-model-that-passes routing with `claude_code` → `ollama` → `claude_api` fallback and subscription rate-limit modeling. | 14 (evals, for "passes") — ship a heuristic router in 13, eval-gate it in 14 |
| **14 — Eval & quality harness ★** | Golden-task benchmarks + scoring on a schedule; verification primitives (judge/self-critique/groundedness); dry-run/simulation; prompt versioning with eval-gated promotion. | 12 |
| **15 — Event bus & reactivity ★** | Webhooks, file/git-push watch, email-in, and dashboard buttons → workflows; idempotent triggers + dead-letter queue. | 12 |
| **16 — RAG & context engineering ★** | Codebase index (tree-sitter/LSP), asset ingestion (PDFs/images/transcripts), context compiler/budgeter, and per-task retrieval over memory + code. | 11 |
| **17 — Resilience kit ★** | `agentos doctor`, git-backed snapshot/restore of the sqlite stores + `memory/`, and always-on headless deployment with cross-machine sync. | 11 |
| **18 — Prior-harness carry-forward ★** | Heartbeat/proactivity engine, multi-channel presence with channel-aware hygiene, two-way notifications, voice briefings. | 11, 12, 15 |
| **19 — Business-cockpit ETL ★** | Connector framework + project-metrics ETL (e.g. Stripe/YouTube revenue, Firestore content stats, signups) into the dashboard; cost attribution + per-project P&L. | 15 |
| **20 — DAG orchestration & goals** | Fan-out/join workflow engine with checkpoints; cross-project scheduling; goal/OKR layer and recurring rituals. | 13, 15 |
| **21 — Self-improvement loop** | Mine telemetry to propose framework/agent/prompt fixes and auto-file `BUGS.md` entries; auto-learning loop feeding memory + eval cases. | 14, 16, 19 |
| **22 — Ecosystem & interface depth** | MCP/tool registry, agent SDK + generator, capability registry/auto-routing; conversational control plane, universal search, approve-from-UI + PWA; chaos testing. | 13, 14 |

> **Deferred (follow-on, not in this plan):** project-tier onboarding — writing
> `AGENTS.md`/memory into the individual workspace projects migrated from a prior
> harness's monorepo.
> This is a natural extension of Phase 11 but is intentionally out of scope for now.
> Also note `memory/shared/` is an empty duplicate slated for deletion, and
> `memory/per-agent/` is being repurposed for global-scoped agent facts under the
> Phase 11 tier model.

---

## 3. Biggest leverage / do-first shortlist

Tailored to this user — runs multiple projects, is migrating off a prior harness,
has already been burned by a laptop swap, and runs on a rate-limited subscription
plus $0 local `ollama`.

1. **Finish Phase 11 (memory subsystem).** Everything high-leverage downstream —
   policy sensitivity flags, RAG, context engineering, channel-aware presence,
   "write it down" discipline — depends on it. It is also the current source of
   truth for the operating rules the user repeats across projects.

2. **Phase 12 — policy engine.** The user's most expensive failure mode is an agent
   ignoring a standing rule (deploying to prod without the phrase, writing outside a
   worktree, drifting between projects). Machine-enforcing these converts memorized
   discipline into hard guarantees, and unlocks the safety reflexes carried over
   from a prior harness.

3. **Phase 17 — resilience kit (`doctor` + snapshot/restore + headless).** This user
   has *already* lived a laptop swap. Git-backed snapshots of the sqlite stores and
   `memory/`, a one-shot `agentos doctor`, and a headless deployment so cron/
   heartbeats fire with the laptop closed are direct insurance against the failure
   he has actually experienced.

4. **Phase 13 — cost-aware router + fallback.** Billing is subscription rate-limits,
   not dollars, plus free local `ollama`. Modeling remaining quota and falling back
   to ollama near the ceiling keeps multi-project parallel work flowing instead of
   stalling on a rate cap — the single biggest throughput win for a portfolio
   operator.

5. **Phase 19 — business-cockpit ETL.** Across a portfolio of projects, the ability to
   see revenue, traffic, and lead interest in one dashboard turns mission-control
   from an ops console into a decision tool — the payoff that justifies running an
   agent OS over the whole portfolio.

---

*Cross-references:* architecture context in [./DESIGN.md](./DESIGN.md); the in-flight
memory work in [./manage_memory_plan.md](./manage_memory_plan.md); shipped phases in
[../README.md](../README.md).
