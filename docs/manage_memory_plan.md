# AgentOS Memory Subsystem — Final Implementation Plan

> Companion design doc: [./DESIGN.md](./DESIGN.md). This file is the *implementation*
> plan; DESIGN.md is the architecture/vision narrative. Linked from the root
> [../AGENTS.md](../AGENTS.md).

## Context & scope

AgentOS (`~/agentos`) is a real, in-development, model-agnostic
agentic platform (orchestrator + 8 specialist agents + workflows + dashboard;
Phases 0–10 already built — see [../README.md](../README.md)). It now needs a
**provider-agnostic, layered MEMORY subsystem** built as a native part of that
platform: one canonical tiered model, wired into the orchestrator, curated by a
platform-native agent, and viewable on the existing dashboard.

The platform currently carries two partially-built memory layouts on disk. The
**tier scaffold** (`AGENTS.md`, `CLAUDE.md`, `global/`, `workspaces/`, `inbox/`)
is the intended model and is *kept*. The older `memory/shared/` + `memory/per-agent/`
pair is reconciled: `memory/shared/` is a redundant duplicate of the global tier
and is deleted; `memory/per-agent/` is repurposed for global-scoped per-agent facts.

This plan reconciles the integrated design with its critique. Two critique findings
(C1, C2) puncture the central provider-agnosticism claim and are resolved below;
H1 (a gratuitous agent rename) is reversed — the curator stays the existing
`librarian`; H2/H3/M1–M4 are folded into the architecture and verification steps.

**IN SCOPE:** unify the tier model with the real platform structures; resolve the
redundant `memory/shared/` duplicate; implement provider-agnostic load/inject at
the orchestrator seam; implement provider-agnostic capture; expand the existing
`librarian` agent into the curator; add dashboard + CLI view/search.

**EXPLICITLY DEFERRED (do NOT touch this run): "Onboard existing projects."**
No `AGENTS.md` or `memory/` is written into any repo migrated from a prior
harness's monorepo (the user's registered projects).
The project tier's *loader contract* is designed and shipped here; authoring the
project files themselves is a follow-on, done one project at a time with the user.

---

## Integrated architecture (4 tiers → real AgentOS structures)

Primary axis is **tier** (scope breadth), matching the user's mental model and the
`AGENTS.md` standard. **Per-agent** is a narrow, explicitly-keyed sub-scope, not a
competing top-level layout.

### Canonical layout (target)

```
agentos/
  AGENTS.md                      # tier-0 entry point (keep)
  CLAUDE.md                      # Claude shim → @AGENTS.md (keep)
  global/
    AGENTS.md                    # @-imports the global docs (keep)
    memory/*.md                  # Tier 1 facts (keep, populated; index.md + fact files)
  workspaces/
    personal/{AGENTS.md, memory/, memory/per-agent/<agent>/}
    business/{AGENTS.md, memory/, memory/per-agent/<agent>/}
  memory/
    per-agent/<agent>/*.md       # GLOBAL-scoped agent facts only (see M1)
    handoffs/<run-id>.md         # Tier 4, platform-internal sessions
  inbox/                         # provider-agnostic capture staging (keep)
  # Tier 3 (project) lives in each project repo — loader-only, deferred
```

Current on-disk state confirmed: `global/memory/` holds `index.md` plus the fact
files (`identity.md`, `file-links.md`, `prod-deploy.md`, `project-scope.md`,
`secret-rotation.md`, `subagents.md`, `worktree-isolation.md`).
`workspaces/{personal,business}/` already have `AGENTS.md` + `memory/`
(`.gitkeep` + `index.md`). `inbox/` already has `README.md` + `.gitkeep`.
`memory/shared/` and `memory/per-agent/` are present but empty.

### Tier definitions

| Tier | Home | Load rule |
|---|---|---|
| **1 — Global/user** | `global/memory/*.md` via `global/AGENTS.md` | Always loaded. |
| **2 — Workspace** | `workspaces/<personal\|business>/memory/` | Loaded when workspace is resolvable (from `--project` map or explicit `--workspace`); see H3. |
| **3 — Project** | `<repo>/AGENTS.md` + `<repo>/memory/*.md` | **Loader contract only** this run. Read when `repo_path` is known; writes nothing into real repos. |
| **4 — Session/handoff** | project-scoped → `<repo>/memory/handoffs/` (deferred); platform-internal → `agentos/memory/handoffs/<run-id>.md` | Ephemeral. Never auto-promoted; only the curator graduates facts upward. |

### Per-agent sub-scope (resolves M1)

`memory/per-agent/<agent>/*.md` holds **global-scoped agent facts only** (e.g.
"researcher's preferred sources"). It is loaded for the matching agent on every
dispatch regardless of workspace. The earlier "any tier, loaded flat" framing was
incoherent (it would leak personal-workspace facts into business dispatches), so
per-agent is pinned to global scope. Workspace-specific agent facts, if ever needed,
are a future nested path (`workspaces/<ws>/memory/per-agent/<agent>/`) — not built now.

### Precedence, per-tier budget, and the deterministic hot-path read (resolves H2)

- **Conflict precedence: narrowest wins** → project > workspace > global. Per-agent
  (global-scoped) sits at global precedence.
- **Truncation order:** when the char budget is hit, drop the **least specific**
  facts first (global before workspace before project). Implemented via **per-tier
  sub-budgets** rather than one flat cap, so a fresh project fact is never crowded
  out by a stale global one.
- **Default read path is deterministic** (keyword/tf-idf match of the task against
  fact lines, per-tier sub-budgeted, total ~4–6k chars). **No model call on the hot
  path** — protects the per-run budget. Semantic "librarian surfacing" is
  **opt-in per workflow only**.

### Provider-agnostic injection (resolves C2 — the central fix)

**Verified against the code:** every provider already exposes the *same* uniform
`dispatch(*, model, system_prompt, user_message, …)` signature defined by the
`Provider` protocol in `providers/base.py`. The orchestrator hands each adapter a
`system_prompt`, and each adapter translates it to its own runtime convention —
`claude` → `system=`, `claude_code` → `--system-prompt`, `ollama` → a system-role
message, and the CLI runtimes `agentcli` (`agentcli agent --json --message "…"`)
and `hermes` (`hermes -z "…"`) fold `system_prompt` into the message *themselves*
(via their internal `_build_message` / `_build_prompt`, joined with a `---`
delimiter). So the earlier worry — "agentcli/hermes can't take an orchestrator
system prompt, so memory needs a separate `user_message` path" — was inverted:
**they do accept `system_prompt`**, and the adapter abstraction already hides the
per-runtime difference.

Consequence: there is **one injection seam, and it's enough by construction**.
`router.dispatch()` prepends the memory context to `system_prompt` at the
`system_prompt = agent.get("system_prompt", "")` block in `core/router.py`, for
**all providers uniformly**. No per-runtime strategy map at the injection seam, no
`Provider.dispatch()` signature change in `providers/base.py`, and no edits to the
agentcli/hermes adapters for injection — they already do the right thing with the
`system_prompt` they receive.

| Provider/runtime | How `system_prompt` reaches the model (already implemented) |
|---|---|
| `claude`, `claude_api` | Anthropic `system=` parameter. |
| `claude_code` | `--system-prompt` CLI flag. |
| `ollama` | system-role message in the messages array. |
| `openai` | system-role message. |
| `agentcli` (`agentcli agent --json --message "…"`), `hermes` (`hermes -z "…"`) | Adapter folds `system_prompt` into the single `--message` / `-z` payload (`{system_prompt}\n\n---\n\n{user_message}`). |

**Phase 2 keeps a lightweight verification task** — re-confirm each adapter still
forwards `system_prompt` faithfully before relying on prepend (cheap regression
insurance), but the design assumes the single-seam prepend, not a strategy map. We
do **not** adopt Anthropic beta memory-tool types (Claude-specific) on any path.

### Provider-agnostic capture (resolves C1 — the other central fix)

Capture must not depend on Claude Code's `autoMemoryDirectory`, or memory never
grows for ollama / agentcli / hermes / openai runs.

- **Primary mechanism (all providers):** `router.dispatch()` writes a structured
  capture record to `inbox/` **after every run, regardless of provider** —
  `{run_id, agent, project, workspace, timestamp, output_excerpt}`. The router
  already persists run output to `runs.sqlite`, so the data is in hand; this is a
  thin additional sink.
- **Claude `autoMemoryDirectory`:** documented as **best-effort, Claude-path-only
  supplementary** capture into the same `inbox/`, never the general mechanism.
- The capture record is raw staging, not memory. Only the curator promotes
  inbox → tiers (write discipline preserved).

### Curator agent — the existing `librarian` (resolves H1 — keep the name)

**Keep the agent named `librarian`** — do not rename, do not introduce a
"memory-manager". The `daily-briefing` workflow and other shipped workflows
reference `agent: librarian`, and the agent loader keys by directory name
(`get_agent(name)`); a rename is a breaking identifier change for zero functional
gain. **Expand the existing `librarian` spec in place** (`agents/librarian/agent.md`,
already model `claude-haiku-4-5`, tools `memory.read/write/search`); if vocabulary
matters, address it in the `description` field. No alias/redirect indirection.

The librarian's tools are re-mapped from the old shared/per-agent dichotomy onto
**tier + optional per-agent sub-scope**. Responsibilities:

- **Triage `inbox/`** capture records → classify into global / workspace
  (personal|business) / project / drop, honoring write-discipline (specific,
  concrete, durable, non-stale, non-duplicate).
- **Promote** keepers into the correct tier via `memory_store`; clear processed
  inbox items.
- **Consolidate (resolves M3):** consolidation **logic lives as instructions in the
  librarian system prompt + `memory_store` dedupe/prune primitives**, executed
  inside the metered `provider.dispatch()` run — *expand-in-place*. The interactive
  `anthropic-skills:consolidate-memory` **skill is reserved for the human-in-session
  path** and is NOT invoked from inside an autonomous dispatch (dispatched runs
  cannot call harness skills).
- **Surface (opt-in):** when run as a workflow step, return the
  `## Relevant memory for:` block — the smart read path augmenting the
  deterministic default.

Runs **scheduled** (disabled-by-default `memory-curate` entry in
`config/schedules.yaml` → `curate-memory.yaml` workflow) and **on demand**
(`agentos memory curate`).

### Workspace resolution fallback (resolves H3)

`project` is optional on `dispatch()` (ad-hoc CLI, MCP `dispatch(agent, task)`).
Defined behavior:

- `project` present → resolve workspace via `config/projects.yaml`; load
  global + workspace + project + per-agent.
- `--workspace <name>` given explicitly → use it.
- Neither present → load **global + per-agent only** (Tier 2/3 skipped, no error).

Workspace membership encoded in `config/projects.yaml`:
business and personal workspaces, each with its own set of projects.

---

## Disposition of the redundant / repurposed files

The tier scaffold **is** the model the user wants; the only genuine duplicate is
removed.

| Path | Action | Rationale |
|---|---|---|
| `agentos/AGENTS.md` (root) | **KEEP** | Tier-0 entry point. Edit §Tiers (project tier = repos, deferred); ensure the `@docs/DESIGN.md` / `@docs/manage_memory_plan.md` link targets exist. |
| `agentos/CLAUDE.md` | **KEEP** | Correct minimal Claude shim (`@AGENTS.md`). |
| `agentos/global/` (+ AGENTS.md + memory docs) | **KEEP** | Tier 1, populated, in use. Gated by a routine secrets/PII content review before commit (see below). |
| `agentos/workspaces/{personal,business}/` | **KEEP** | Tier 2 shells; `memory/` dirs already present (`.gitkeep` + `index.md`). |
| `agentos/inbox/` (+ README + .gitkeep) | **KEEP** | Provider-agnostic capture staging; curator triages it. |
| `agentos/memory/shared/` | **DELETE** | Empty, unreferenced, duplicates the global tier. |
| `agentos/memory/per-agent/` | **KEEP, repurpose** | Global-scoped agent sub-scope (M1). |

**Routine secrets/PII review (ordinary prudence, not incident recovery):** before
committing the populated `global/memory/*.md` (identity, secret-rotation,
prod-deploy, etc.), eyeball the files for any literal secret *values* — keys,
tokens, passwords. This is the same standard pre-commit hygiene applied to any new
markdown that mentions credentials; it is cheap insurance, not remediation of a
known incident. Procedures/runbooks are fine; only literal secret values are
excluded (see open question 3).

---

## Files to create / modify (real paths)

### Create
- `~/agentos/orchestrator/agentos/core/memory_context.py` — tier
  resolution, deterministic relevance, per-tier sub-budgets, precedence-aware
  truncation, frontmatter strip (read path). Exposes a shared **matcher primitive**
  (no budgeting) for reuse by the view (M4).
- `~/agentos/orchestrator/agentos/core/memory_store.py` —
  tier-correct write/append/dedupe/prune primitives + consolidation logic; used by
  librarian + CLI only.
- `~/agentos/orchestrator/agentos/core/memory_capture.py` —
  provider-agnostic post-run capture writer to `inbox/` (C1).
- `~/agentos/orchestrator/agentos/memory_view/loader.py` — read
  tiers/files/search for API + CLI (search mode = matcher primitive, no char cap /
  no precedence truncation, per M4).
- `~/agentos/config/projects.yaml` — `project → {workspace, repo_path}`
  map (business vs personal workspaces).
- `~/agentos/workflows/curate-memory.yaml` — inbox triage +
  consolidate (dispatches `librarian`).
- `~/agentos/docs/DESIGN.md` — seeded from this architecture.
- `~/agentos/docs/manage_memory_plan.md` — this implementation plan.
- `~/agentos/memory/per-agent/.gitkeep`,
  `~/agentos/memory/handoffs/.gitkeep`.
- Tests (the suite lives at `orchestrator/agentos/tests/`, run via `pytest agentos/tests/`):
  `~/agentos/orchestrator/agentos/tests/test_memory_context.py`,
  `test_memory_capture.py`, `test_memory_store.py`, `test_memory_view.py`.

### Modify
- `~/agentos/orchestrator/agentos/core/router.py` — prepend memory
  context to `system_prompt` at the single assembly seam (C2), uniformly for all
  providers; call `memory_capture` after every run (C1); thread `project`/`workspace`.
  No `Provider` signature change.
- `~/agentos/orchestrator/agentos/core/config.py` — add
  `projects_map()` / `workspace_for_project(project)` reading `config/projects.yaml`.
- `~/agentos/orchestrator/agentos/providers/*` adapters — **no
  injection edits required.** Every adapter (`claude`, `claude_code`, `ollama`,
  `openai`, `agentcli`, `hermes`) already accepts `system_prompt` and forwards it
  per-runtime; the agentcli/hermes adapters already fold it into their `--message`/
  `-z` payload. Phase 2 only re-verifies this, it does not change it.
- `~/agentos/orchestrator/agentos/entrypoints/api_server.py` — add
  `/api/memory/tree`, `/file`, `/search`, `/inbox` routes (path-validated to allowed
  dirs).
- `~/agentos/orchestrator/agentos/entrypoints/cli.py` —
  `agentos memory ls|show|search|curate`.
- `~/agentos/dashboard/index.html` — sidebar nav item + memory view
  (tier tree left, rendered fact right, search box, "inbox pending: N" badge).
- `~/agentos/config/schedules.yaml` — disabled `memory-curate`
  schedule.
- `~/agentos/AGENTS.md` — fix Tiers section + confirm `@docs/*` links
  resolve.
- `~/agentos/agents/librarian/agent.md` — **expand in place** (no
  rename): re-map tools to tier+per-agent sub-scope; add triage/promote/consolidate/
  surface responsibilities; consolidation as in-prompt logic, not a skill call.

### Delete
- `~/agentos/memory/shared/`

### Do NOT touch (deferred onboarding)
- Anything under a prior harness's monorepo projects.

---

## Phased implementation sequence

Each phase is sized for a single follow-up implementation run and ends verifiable.

**Phase 0 — Land the layout (no behavior change).**
- Routine secrets/PII review of the `global/memory/*.md` docs first.
- Commit the kept untracked files (root `AGENTS.md`/`CLAUDE.md`, `global/`,
  `workspaces/`, `inbox/`).
- Delete `memory/shared/`; add `memory/per-agent/.gitkeep`,
  `memory/handoffs/.gitkeep`; confirm `workspaces/{personal,business}/memory/`
  exist (they do).
- Seed `docs/DESIGN.md` + `docs/manage_memory_plan.md`; fix `AGENTS.md` links/Tiers.

**Phase 1 — Config + project map.**
- Add `config/projects.yaml` + `config.workspace_for_project()`.

**Phase 2 — Read path (core value) + single-seam injection.**
- Implement `memory_context.py` (tier order, per-tier sub-budgets, precedence
  truncation, frontmatter strip, shared matcher primitive).
- **Re-verify** every adapter forwards `system_prompt` faithfully (C2) — confirms
  the single-seam prepend is safe; agentcli/hermes already fold it into `--message`/`-z`.
- Wire injection into `router.dispatch()` as a uniform `system_prompt` prepend for
  all providers (no per-runtime branching).
- Handle absent-`project` fallback (H3).

**Phase 3 — Capture + curator + consolidation.**
- Implement `memory_capture.py`; call from `router.dispatch()` post-run (C1, all
  providers).
- Implement `memory_store.py` (write/append/dedupe/prune + consolidation logic).
- Expand `agents/librarian/agent.md` in place (H1); create
  `workflows/curate-memory.yaml`; add disabled `memory-curate` schedule.

**Phase 4 — View/search (dashboard + CLI).**
- `memory_view/loader.py` (search mode, no budget/truncation — M4); 4
  `/api/memory/*` routes; dashboard nav + view; `agentos memory` CLI.

**Phase 5 — Docs + close-out.**
- Finalize `docs/DESIGN.md` / `docs/manage_memory_plan.md`; document the deferred
  onboarding task + project-tier loader contract; document the single-seam
  `system_prompt` injection and the provider-agnostic capture mechanism.

**Deferred follow-on (NOT this run): "Onboard existing projects."** Uses the
Phase-2 project-tier loader + Phase-3 write primitives to author
`<repo>/AGENTS.md` + `<repo>/memory/` inside each real repo, worktree-isolated,
one project at a time with the user.

---

## Verification

| Phase | Verifiable outcome |
|---|---|
| 0 | `git status` clean of stray untracked memory files; all `@`-links in `AGENTS.md` resolve; secrets/PII review recorded; `memory/shared/` gone. |
| 1 | Unit test: business-workspace slugs → business; personal-workspace slug → personal; unknown project → unresolved (no crash). |
| 2 | `test_memory_context` proves tier order, **per-tier sub-budget caps**, **project>workspace>global precedence on conflict**, **least-specific-dropped-first truncation**, frontmatter strip. Dispatch with a seeded global fact shows it prepended to `system_prompt` for every provider (and, for agentcli/hermes, reaching the model via the adapter's existing `--message`/`-z` fold). Dispatch with empty memory is byte-identical to today. No extra model call on hot path. Absent-`project` dispatch loads global+per-agent only. |
| 3 | A dispatch to **a non-Claude provider** (e.g. ollama) writes a capture record to `inbox/` (proves C1). `curate-memory` against a seeded inbox item lands it in the correct tier and clears inbox; consolidation pass updates the tier `index.md` **without invoking the interactive skill**. `daily-briefing` still resolves `agent: librarian` (proves H1 no-regression). |
| 4 | Dashboard memory tab lists tiers, renders a fact, search returns the seeded fact (un-truncated, proving search≠inject budgeting — M4); `agentos memory search <q>` matches API; path traversal outside allowed dirs rejected; "inbox pending: N" badge accurate. |
| 5 | A fresh reader can run `agentos memory ls` and `agentos memory curate` from docs alone. |

---

## Open questions for the user

1. **Curator model/cost cadence.** Default `memory-curate` schedule is daily and
   disabled-by-default. Acceptable, or do you want a different cadence / it enabled
   out of the gate? The curator is a metered dispatch (librarian already defaults to
   a cheap haiku-class model).
2. **Capture excerpt size & retention.** How much run output should each `inbox/`
   capture record keep (full vs first N chars), and should processed capture records
   be deleted or archived after triage?
3. **`global/memory` commit contents.** The routine secrets/PII review may surface
   borderline-sensitive facts (e.g. secret-rotation *procedures* vs secret *values*).
   Confirm: procedures/runbooks OK to commit, only literal secrets/keys excluded?
4. **Workspace membership confirmation.** `projects.yaml` will encode
   business and personal workspaces, each with its own slugs. Any project
   missing or mis-bucketed? Where do new/ad-hoc projects default?
5. **Memory-context size for the CLI runtimes.** agentcli/hermes already accept
   `system_prompt` (folded into a single `--message`/`-z` payload), so injection
   works uniformly — no separate strategy needed. The only open knob is volume: a
   large memory block inflates that one CLI argument. Cap the per-tier budget more
   tightly for these runtimes, or treat them like everyone else?
