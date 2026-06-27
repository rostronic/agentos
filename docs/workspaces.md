# Workspaces & the memory tier model

AgentOS gives every agent the right context automatically by layering memory in
**tiers**, broad to narrow. The middle tier — the **workspace** — is split into two
spheres, **personal** and **business**, so work facts never bleed into personal
projects and vice-versa. This doc explains how the tiers load, how the active
workspace is chosen, and why the personal/business split is worth having.

## The four tiers

When an agent is dispatched, the orchestrator assembles a memory block from up to
four tiers and prepends it to the agent's system prompt. Each tier is broader (more
general) than the one below it; narrower facts appear later and "win" on conflict.

| # | Tier | Lives in | Loaded when |
|---|------|----------|-------------|
| 1 | **Global** | `global/memory/*.md` | always |
| 2 | **Workspace** | `workspaces/<personal\|business>/memory/*.md` | the project's workspace is known |
| 3 | **Project** | the project's own `memory/` | the project is known |
| 4 | **Per-agent** | `memory/per-agent/<agent>/*.md` | always (agent-scoped, global) |

Each tier has its **own** character budget (global 2000, workspace 1500, project
3000, per-agent 1000), so a long global file can never crowd out a fresh project
fact — truncation always drops the least-specific tier first. `index.md` files are
catalogs, not facts, and are skipped during assembly. The read path is fully
deterministic (keyword relevance, no model call), so it is safe on the dispatch hot
path and adds no API cost. See `orchestrator/agentos/core/memory_context.py`.

```
global  ─────────────────────────►  always (identity + universal rules)
   └─ workspace (personal | business) ─►  the active sphere's cross-project facts
        └─ project ───────────────►  the one project being worked on
             └─ per-agent ────────►  facts scoped to the dispatched agent
                                       (narrower = wins on conflict)
```

## How the active workspace is chosen

The workspace is **selected by the project**, not set as a global mode. Each project
in `config/projects.yaml` declares which sphere it belongs to:

```yaml
projects:
  example-business:
    workspace: business        # ← selects the business memory tier
    repo_path: ~/dev/example-business
    memory_path: projects/example-business
  example-personal:
    workspace: personal        # ← selects the personal memory tier
    repo_path: ~/agentos/workspaces/personal/example-personal
    memory_path: workspaces/personal/example-personal
  mission-control:
    workspace: null            # ← platform tooling: no workspace tier loaded
    repo_path: ~/agentos
    memory_path: projects/mission-control
```

At dispatch time the orchestrator resolves the project's workspace via
`config.workspace_for_project(slug)` and, if it returns `personal` or `business`,
loads `workspaces/<that>/memory/*.md` as Tier 2. If the project is unknown, or its
`workspace` is `null`, Tier 2 is simply skipped — the agent still gets the global
tier and (when known) the project tier.

So the rule of thumb is: **the project you dispatch at determines the active
workspace.** Dispatch at a business project and the agent sees business-sphere
cross-project facts; dispatch at a personal project and it sees personal-sphere
facts. You never have to switch a mode by hand.

## Why the personal / business split matters

The two spheres carry genuinely different conventions, and keeping them separate is
what makes the workspace tier useful rather than noisy:

- **Relevance.** A business agent shouldn't reason about your household chores, and a
  personal agent shouldn't apply your production-deploy policy to your travel notes.
  Splitting the tier means each agent gets only the cross-project facts that apply to
  its sphere.
- **Privacy & safety.** Work conventions (brand voice, client constraints,
  deploy/approval policy) stay out of personal projects; personal facts stay out of
  anything work-facing. The boundary is structural, not a habit you have to remember.
- **Budget.** Tier 2 has a fixed character budget. Loading only the relevant sphere
  keeps that budget spent on facts that matter to the project at hand instead of
  diluting it with the other half of your life.

## What goes where

| Put it in… | When the fact is… | Example |
|---|---|---|
| **Global** (`global/memory/`) | true everywhere, every project | your name; "always cite files with absolute paths" |
| **Workspace** (`workspaces/<sphere>/memory/`) | shared across all projects in **one** sphere | "all business projects deploy on Firebase Hosting" |
| **Project** (the project's own `memory/`) | specific to a single project | "Product X's production site id is …" |

The per-sphere `AGENTS.md` files spell this out with a worked example for each side:

- [../workspaces/personal/AGENTS.md](../workspaces/personal/AGENTS.md)
- [../workspaces/business/AGENTS.md](../workspaces/business/AGENTS.md)

## Adding a project to a sphere

1. Register the project in `config/projects.yaml` (copy from
   `config/projects.yaml.example`) with `workspace: personal` or `workspace: business`.
2. That's it — the next dispatch at that project loads the matching workspace tier.
   To capture a cross-project fact for the whole sphere, drop it in
   `workspaces/<sphere>/memory/` (or let the memory-manager promote it there during
   use). See [onboarding.md](./onboarding.md) for bringing existing projects in.
