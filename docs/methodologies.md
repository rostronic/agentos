# SDLC Methodologies

AgentOS runs the specialist team through a **pluggable software-development
methodology**, chosen *per project*. The methodology is the engineering
discipline applied to each task — which agents touch it, in what order, and which
gates it must pass. It is **independent of cadence** (how work is scheduled).

Two dials, both per project:

| Dial | Values | Default |
|------|--------|---------|
| **methodology** (discipline) | `agile` · `xp` · `devops` · `waterfall` | `agile` |
| **cadence** (scheduling) | `backlog` (continuous pull) · `sprint` (batched deliverable) | `backlog` |

> Common names that fold into the two dials: **Scrum** = agile + sprint cadence ·
> **Kanban** = agile + backlog cadence · **TDD** is folded into XP (XP is test-first
> *plus* review). We model the four distinct disciplines rather than ship redundant
> names.

## The four disciplines

Each task flows through a pipeline of agent stages. `qa`/`critic` gates are
**agent-to-agent** (a `FAIL` re-loops or blocks the task to your inbox) — not human
stops. Human approval lives at exactly **two** points, globally (see *Gates* below).

| Discipline | Per-task pipeline | Test-first | Extra gate |
|------------|-------------------|:----------:|------------|
| **agile** (default) | `developer` → `qa` | – | – |
| **xp** | `qa` (write failing tests) → `developer` → `qa` (verify) → `critic` (review) | ✅ | critic review |
| **devops** | `developer` → `qa` → `developer` (CI / smoke build-verify, on a preview — not prod) | – | build-verify |
| **waterfall** | `planner` (design) → `developer` → `qa`, in dependency order, phase by phase | – | phase-gate¹ |

¹ Per-phase **human** gates are a planned enhancement; today waterfall runs as agile
plus strict dependency/phase ordering.

`agile` is the baseline and the safe default — it reproduces the original
`developer → qa` flow exactly, so enabling the methodology system changes nothing
until you opt a project into another discipline.

## Cadence

- **`backlog`** (default) — continuous pull: the team repeatedly takes the next
  ready backlog task (one with no sprint) for the project and runs it, until the
  backlog drains. Maximizes throughput; no batch boundaries.
- **`sprint`** — the team works the ready tasks of a named sprint to completion,
  then you start the next sprint. Use when you want work batched into deliverables.

Both run the *same* per-task pipeline; cadence only decides which tasks feed it.

## Gates (human-in-the-loop)

The team is autonomous between **two** human gates:

1. **Plan approval** — the chief-of-staff decomposes a goal into backlog tasks; the
   team starts only once you approve them (tasks move `backlog → ready`).
2. **Prod deploy** — nothing ships to production without your explicit approval.

In between, the team runs in **`full`** mode (`qa`/`critic` gates are agent-to-agent;
a task that can't pass after retries escalates to your inbox).

## Configuring it

In `config/settings.yaml`:

```yaml
orchestrator:
  default_methodology: agile      # agile | xp | devops | waterfall
  default_cadence: backlog        # backlog | sprint
  default_approval_mode: full     # 2-gate model

projects:
  example-app:        { methodology: xp }                        # test-first + critic review
  example-site:       { methodology: devops }                    # + CI/smoke build-verify
  example-compliance: { methodology: waterfall, cadence: sprint }
```

Precedence: per-project value → `orchestrator` default → built-in fallback
(`agile` / `backlog`). Resolved in code by `config.methodology_for(slug)` and
`config.cadence_for(slug)`.

## Running it

- **Sprint cadence:** `sprint_executor.execute_sprint(sprint_id)`
- **Backlog cadence:** `sprint_executor.run_backlog(project_slug)`

Both honour the project's methodology automatically.

## Honest limitations

- `agile` and the agile-derived presets (Scrum/Kanban) are intentionally the same
  per-task pipeline — they differ only in cadence.
- `waterfall` per-phase human gates are not yet enforced (dependency ordering only).
- The `devops` build-verify stage runs CI/smoke checks on a **preview**, never a
  production deploy — prod deploy is always the human gate.
