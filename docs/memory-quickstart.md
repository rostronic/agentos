# Memory quickstart — give an agent the right facts at the right scope

AgentOS agents don't start from a blank slate. Before every dispatch the
orchestrator assembles a **memory block** from layered markdown files and prepends
it to the agent's prompt. This page is the five-minute version: where to put a fact
so the right agent sees it, and how to prove it loaded.

For the full tier model and the personal/business split, see
[workspaces.md](workspaces.md). For the architecture, see [DESIGN.md](DESIGN.md).

---

## The mental model

Memory loads **broad → narrow**, narrowest last. A narrower fact wins on conflict.

```
global  ─────────────────────────►  always (identity + universal rules)
   └─ workspace (personal | business) ─►  cross-project facts for the active sphere
        └─ project ───────────────►  facts about the one project being worked on
             └─ per-agent ────────►  facts scoped to the dispatched agent
                                       (narrower = wins on conflict)
```

The read path is **deterministic** (keyword relevance, no model call), so it is
free and safe on the dispatch hot path. Each tier has its own character budget, so
a long global file can never crowd out a fresh project fact.

---

## Where do I put a fact?

| Put it in… | When the fact is… | Example |
|---|---|---|
| **Global** — `global/memory/*.md` | true everywhere, every project | "always cite files with absolute paths" |
| **Workspace** — `workspaces/<personal\|business>/memory/*.md` | shared across all projects in **one** sphere | "all business projects deploy on Firebase Hosting" |
| **Project** — the project's own `memory/*.md` | specific to a single project | "Product X's production site id is …" |
| **Per-agent** — `memory/per-agent/<agent>/*.md` | true for one agent across all projects | "the qa agent always runs `pytest -q` first" |

Rule of thumb: **write the fact at the broadest scope where it's still true.** If
it's only true for one project, keep it in that project's `memory/` so it never
leaks into a sibling.

---

## Step 1 — Look at what's already loaded

The global tier ships populated. Browse it:

```bash
ls global/memory/                 # identity.md, prod-deploy.md, file-links.md, …
cat global/memory/index.md        # the catalog (one line per fact)
```

Each file is plain markdown with YAML frontmatter (`tier`, `type`). `index.md`
files are catalogs, not facts — they're skipped during assembly.

## Step 2 — Add a fact

Drop a new markdown file in the right tier's `memory/` directory. For a
project-scoped fact, that's the project's own `memory/` (see the
[projects quickstart](projects-quickstart.md) for registering a project and where
its `memory/` resolves). A minimal fact file:

```markdown
---
tier: project
type: fact
---
# Deploy target

Product X's production site renders live from Firestore (force-dynamic), so a
content change is visible immediately — no rebuild needed.
```

## Step 3 — Prove it loaded (no run, no cost)

You can render the exact memory block an agent would receive **without spending a
dispatch**:

```bash
cd orchestrator
.venv/bin/python -c "
from agentos.core import memory_context as mc
print(mc.build_context('developer', project='example-project',
                       task='what does greet return'))
"
```

The output is the `Relevant memory (AgentOS — most specific last)` block, with one
`### <tier>` heading per tier that contributed. Confirm your new fact shows up
under the heading you expect. Then dispatch for real:

```bash
.venv/bin/python -m agentos.entrypoints.cli dispatch developer \
  "…" --project example-project
```

A project-scoped dispatch (`--project <slug>`) is what unlocks the project tier —
and only that project's facts, never a sibling's.

---

## Capturing facts as you work (the inbox)

You don't have to hand-author every file. Raw notes can land in `inbox/` as a
capture-staging area; the **librarian** agent (and `agentos onboard <slug>
--curate`) distills staged sources into curated `memory/` files. That keeps the
loaded tiers tight while nothing gets lost. See
[onboarding.md](onboarding.md) for the discover → classify → curate runbook and
[manage_memory_plan.md](manage_memory_plan.md) for the subsystem design.

## See also

- [workspaces.md](workspaces.md) — the full four-tier model + personal/business split.
- [projects-quickstart.md](projects-quickstart.md) — register a project, then watch
  its memory tier load on dispatch.
- [DESIGN.md](DESIGN.md) — layered-memory architecture in context.
