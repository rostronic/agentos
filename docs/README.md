# AgentOS Docs

Index of the design and planning documents for AgentOS. All links are relative to
this `docs/` directory.

## Documents

- [USAGE.md](./USAGE.md) — **Start here to *use* AgentOS**: CLI, dashboard, Claude
  Desktop, agents, workflows, sprints, memory, daily brief — with verified commands.
- [FAQ.md](./FAQ.md) — Short answers to first-hour questions: provider choice,
  `401`/re-login, first-run timing, where project context goes, cost.
- [DESIGN.md](./DESIGN.md) — Umbrella architecture & vision: the authoritative,
  scannable map of what AgentOS is, how it's built, and where it's going.
- [projects-quickstart.md](./projects-quickstart.md) — Register a project and
  dispatch an agent scoped to it (uses the runnable `examples/example-project/`).
- [memory-quickstart.md](./memory-quickstart.md) — Put a fact at the right scope
  and prove it loaded, in five minutes.
- [daily-briefing.md](./daily-briefing.md) — Configure, run, and schedule
  `agentos brief` (the offline morning digest).
- [workspaces.md](./workspaces.md) — The memory **tier model**: global →
  workspace (personal | business) → project loading, how the active workspace is
  chosen, and why the personal/business split matters.
- [manage_memory_plan.md](./manage_memory_plan.md) — Implementation plan for the
  provider-agnostic, layered memory subsystem.
- [capability-roadmap.md](./capability-roadmap.md) — Catalog of proposed future
  capabilities; forward-looking, nothing here is built yet.
- [onboarding.md](./onboarding.md) — Runbook for bringing existing projects under
  AgentOS (register + copy memory in); non-destructive, copy-only.

## Reading order

Start with **DESIGN.md** for the architecture and vision, then read
**manage_memory_plan.md** (the memory subsystem). Once oriented, skim
**capability-roadmap.md** for what's next.
