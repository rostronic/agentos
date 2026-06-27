# AgentOS — Project Onboarding Runbook

> Status: **proposed / not yet executed.** Prerequisites below must be built first, and
> nothing is migrated until explicitly approved. Last updated 2026-06-07.

Bringing an existing project under AgentOS = **registering it**, **copying its memory in**,
and (eventually) **giving it its own repo**. This runbook covers the repeatable process.

## Scope & constraints

- **Non-destructive.** A prior harness's workspace (the legacy source) is **read-only**
  during onboarding — we **copy**, never move or edit, the project's existing files.
- **Interim model.** While a project still lives in a legacy monorepo, its
  **project-tier memory is copied into AgentOS centrally** (not into the project repo).
  The eventual in-repo model kicks in only when the project gets its own repo — see
  [github-strategy.md](github-strategy.md).
- **One project at a time**, verified before moving on.

## Two interacting tracks

| Track | What | When |
|---|---|---|
| **Memory onboarding** | Register + copy the project's knowledge into AgentOS | Now (copy-only, monorepo untouched) |
| **Repo split** | Give the project its own GitHub repo (with history) | Later, per [github-strategy.md](github-strategy.md) |

They converge per project: **the repo split is when project memory moves from the
AgentOS-side copy into the project's own repo.**

## Prerequisites (must be built first — from [manage_memory_plan.md](manage_memory_plan.md))

Onboarding cannot run until these exist:
1. **`config/projects.yaml`** — registry: `slug → {workspace, repo_path, memory_path}`.
2. **`memory_context.py`** loader — resolves global + workspace + project tiers (project
   tier reads the AgentOS-side `projects/<slug>/` path in the interim model).
3. **`memory_store.py`** — write/dedupe/prune primitives onboarding uses.
4. *(ideal)* **`agentos onboard <project>`** command/workflow so onboarding is one step,
   not hand-done — the `librarian` agent discovers → classifies → proposes → you approve.

## Interim project-memory location

```
~/agentos/projects/<slug>/
  AGENTS.md          # project entry (provider-agnostic)
  memory/*.md        # project-tier facts, copied & curated
```

This is a **snapshot copy**, clearly marked. Deleting it changes nothing else (fully
reversible). On repo split, this content moves into `<repo>/AGENTS.md` + `<repo>/memory/`.

## Per-project steps (copy-only; the legacy source stays read-only)

1. **Register.** Add to `config/projects.yaml`: `workspace` (e.g. business vs personal),
   `repo_path` → the project's checkout (interim: its path in the legacy monorepo).
   Optionally create it in the work layer (`POST /api/projects`) so it appears in
   Projects / Tasks / Board.
2. **Discover** (read-only) the project's existing knowledge from its sources (below).
3. **Classify & dedupe** — drop anything already promoted to the **global** tier (the
   prefix/scope/prod-deploy/secret-rotation rules) so it isn't duplicated; keep only
   genuinely **project-specific** facts. The `librarian` does this.
4. **Copy** the kept facts into `~/agentos/projects/<slug>/AGENTS.md` + `memory/*.md`.
   **Nothing is written to the legacy source.**
5. **Wire & verify** — a dispatch/dry-run with `project=<slug>` loads global + its
   workspace + its project memory, and a *sibling* project's facts are absent.
6. **Confirm no drift** — a `git status` on the legacy source shows **no changes**.
7. **Repeat** for the next project.

## Per-project source inventory (where to copy *from*)

Each project's in-repo `CLAUDE.md`/`AGENTS.md` (read-only) **plus** any central
per-project memory from a prior harness's auto-memory store. A typical inventory:

| Project | Workspace | In-repo docs | Central memory files to mine |
|---|---|---|---|
| **example-shop** | business | `CLAUDE.md` | `example-shop_project`, `feedback_shop_dev_localhost`, `dev_port_convention` (shop parts) |
| **example-news** | business | `CLAUDE.md` | `example-news_project`, `example-news_deploy_policy`, `firebase_webframeworks_flag` |
| *(minimal projects)* | business | *(minimal)* | *(little/none — mostly just register)* |
| *(asset-only project)* | personal | *(asset folder, no repo)* | `<slug>_project` |
| **mission-control** | *(platform tooling)* | `AGENTS.md`, `ARCHITECTURE.md`, `CLAUDE.md` | *(repo-native docs; see github-strategy mission-control caveat)* |

> Already promoted to **global** (do **not** re-copy per project): the per-project
> subagent-prefix rules, stay-in-project/scope, prod-deploy approval + literal phrase +
> unmissable ask, worktree isolation, secret-rotation, absolute-file-links.

## Suggested order

1. **Pilot one rich project end-to-end** to shake out the process.
2. Remaining business projects (near-empty ones → basically registration).
3. Personal / asset-only projects.

## Verification checklist (per project)

- [ ] Appears in `projects.yaml` with correct `workspace` + `repo_path`.
- [ ] `~/agentos/projects/<slug>/` holds the curated copy; no global-tier duplicates.
- [ ] Dispatch with `project=<slug>` loads global + workspace + project; siblings absent.
- [ ] A `git status` on the legacy source is clean — nothing written to the monorepo.
- [ ] (At repo-split time) memory relocated in-repo; `repo_path` updated; loader still resolves.

## Eventual state (after repo split)

Once a project has its own repo ([github-strategy.md](github-strategy.md)):
- Its memory lives in `<repo>/AGENTS.md` + `<repo>/memory/` (travels with the code).
- `projects.yaml` `repo_path` points at the new checkout (e.g. `~/dev/<proj>`).
- The interim `~/agentos/projects/<slug>/` copy can be retired.
