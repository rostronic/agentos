# Projects quickstart — register a project and dispatch an agent at it

This is the fastest path from "I have a project" to "an agent works on it with that
project's memory loaded." We use the runnable demo shipped at
[`examples/example-project/`](../examples/example-project/) so every command below
actually runs.

By the end you will have:

1. **Registered** the demo project in `config/projects.yaml`.
2. **Run its tests** to prove it's a real, working mini-repo.
3. **Dispatched an agent** scoped to it and confirmed the agent loaded the
   project's own curated memory.

> Prereq: you've done the one-time setup (`agentos init`, an `ANTHROPIC_API_KEY` or
> the `claude` CLI). See the [README](../README.md) quickstart. Step 3 is the only
> step that spends a run; steps 1–2 cost nothing.

---

## The demo project

`examples/example-project/` is intentionally tiny but complete:

```
examples/example-project/
  greeter.py          # one function: greet(name) -> str
  test_greeter.py     # three trivial pytest cases
  AGENTS.md           # the project's memory entry point (real facts)
  memory/
    curated.md        # durable project-tier facts (loaded on dispatch)
    handoffs/         # ephemeral session handoffs
```

Its memory says exactly what the code does — so when an agent loads it, you can see
the project tier working, not a placeholder.

---

## Step 1 — Register the project

A project becomes "known" to AgentOS when it has an entry in `config/projects.yaml`.
The tracked template `config/projects.yaml.example` already contains the demo entry —
copy it across (or add it to your existing `config/projects.yaml`):

```yaml
projects:
  example-project:
    workspace: business                          # personal | business — picks the workspace tier
    repo_path: ~/agentos/examples/example-project
    memory_path: examples/example-project        # RELATIVE to the agentos root
    aliases: [example-project, demo]
```

What each field does:

- **`workspace`** (`personal` | `business`) selects which workspace-tier memory
  loads on top of global — the cross-project facts for that sphere.
- **`repo_path`** is where the code lives — used by onboarding and the work layer.
- **`memory_path`** is the key one: the loader reads project-tier facts from
  `<agentos-root>/<memory_path>/memory/*.md`. Here that resolves to
  `examples/example-project/memory/`.
- **`aliases`** are alternate names that resolve to this slug.

Confirm the registry sees it:

```bash
cd orchestrator
.venv/bin/python -m agentos.entrypoints.cli sync-projects   # mirror registry → work layer
.venv/bin/python -m agentos.entrypoints.cli projects        # list work-layer projects
```

> **`onboard` vs. just registering.** `agentos onboard <slug>` is for a project
> whose knowledge needs *discovering* — it scans the repo's `AGENTS.md`/`README`
> and any legacy memory, stages it under `sources/`, and (with `--curate`) distills
> it into `memory/`. The demo ships **already curated**, so you can skip onboarding
> and go straight to dispatch. To see onboarding's discovery step run anyway
> (writes nothing):
>
> ```bash
> .venv/bin/python -m agentos.entrypoints.cli onboard example-project --dry-run
> # → finds repo:AGENTS.md as a source
> ```

---

## Step 2 — Run the project's tests

Prove it's a real, runnable repo before any agent touches it:

```bash
cd examples/example-project
python -m pytest -q
# 3 passed

python greeter.py Ada       # -> Hello, Ada!
python greeter.py           # -> Hello there!
```

---

## Step 3 — Dispatch an agent at the project

Now dispatch an agent **scoped to the project** with `--project example-project`.
Scoping is what makes the orchestrator load this project's memory tier on top of
global + the `business` workspace:

```bash
cd orchestrator
.venv/bin/python -m agentos.entrypoints.cli dispatch developer \
  "Summarize what greeter.greet returns for an empty name, and cite the project fact you used." \
  --project example-project
```

Because the dispatch is project-scoped, the orchestrator prepends a
**"Relevant memory (AgentOS — most specific last)"** block to the agent's prompt.
For this project that block includes, under a `### project` heading, the curated
facts from `examples/example-project/memory/curated.md` — e.g. that `greet("")`
returns the neutral `"Hello there!"` fallback. The agent's answer should reflect
that fact, demonstrating the project tier loaded.

### How memory loading is wired (so you can trust it)

The loader ([`core/memory_context.py`](../orchestrator/agentos/core/memory_context.py))
assembles tiers broad → narrow, narrowest last (wins on conflict):

| Tier | Source | Loads when |
|---|---|---|
| global | `global/memory/*.md` | always |
| workspace | `workspaces/<ws>/memory/*.md` | the project's `workspace` is set |
| **project** | `<memory_path>/memory/*.md` | a `--project` is given |
| per-agent | `memory/per-agent/<agent>/*.md` | always (agent-scoped) |

So `--project example-project` is what unlocks the project tier — and only that
project's facts, never a sibling's.

You can see the exact project block without spending a run:

```bash
cd orchestrator
.venv/bin/python -c "
from agentos.core import memory_context as mc
print(mc.build_context('developer', project='example-project',
                       task='what does greet return'))
"
```

---

## Adopt your own project

Repeat Step 1 for a real project: point `repo_path` at your checkout and
`memory_path` at where its `memory/` should live (in the project's own repo once
it's split out — see [docs/onboarding.md](onboarding.md) for the in-repo model).
Then:

- Let onboarding discover existing docs: `agentos onboard <slug>` (add `--curate`
  to distill them into `memory/curated.md`).
- Or hand-author `AGENTS.md` + `memory/curated.md`, modeled on the demo.

Dispatch with `--project <slug>` and the same loader picks up your project's tier.

## See also

- [docs/onboarding.md](onboarding.md) — the full discover → classify → curate runbook.
- [docs/DESIGN.md](DESIGN.md) — the layered-memory architecture (global → workspace
  → project → session tiers).
