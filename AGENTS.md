# agentos — provider-agnostic agent memory (global entry)

This repo is the **source of truth** for cross-project agent memory. It is host-neutral:
Claude is the current agent, but nothing here depends on a specific provider. A future
agent reads these `AGENTS.md` + markdown files directly.

- Architecture & vision: [docs/DESIGN.md](docs/DESIGN.md)
- Memory subsystem plan: [docs/manage_memory_plan.md](docs/manage_memory_plan.md)
- How to view/search & operate: [README.md](README.md)

## Tiers
1. **Global** (`global/`) — host-neutral identity + universal rules. Always loaded.
2. **Workspace** (`workspaces/<name>/`) — personal vs business; the cross-project facts
   for that sphere. Loaded for the active workspace.
3. **Project** — lives in each project's **own repo** (`AGENTS.md` + `memory/`), so it
   travels with the code. Loaded when cwd is inside the project. (Populated during the
   separate project-onboarding task.)
4. **Session** — ephemeral handoffs in a project's `memory/handoffs/`.

## Global tier (loaded now)
@global/AGENTS.md
