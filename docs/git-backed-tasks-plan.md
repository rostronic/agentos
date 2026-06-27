# Git-backed tasks — implementation plan

**Status:** plan (no code). **Author:** planner agent. **Date:** 2026-06-17.

## 1. Goal / principle

Every agentos session must be able to read **all** tasks. Today the Phase-5 Work layer
(projects → sprints → tasks + the ask_human inbox) is persisted in
`~/agentos/orchestrator/runtime/work.sqlite`, which is **gitignored machine-local state**
(`/home/user/agentos/.gitignore` line: `orchestrator/runtime/*`). Memory, by contrast, is
tracked markdown and therefore travels. This plan makes **git-backed files the source of
truth for tasks** — mirroring how memory works — and demotes SQLite to (at most) a derived
local cache. It realizes the roadmap's "tasks travel with the repo" idea (the same property
the project-tier memory already has per `/home/user/agentos/docs/DESIGN.md` §Memory, and
`AGENTS.md` Tier 3).

All locked design decisions from the task brief are honored; this document pins the exact
layout, schemas, consumer fixes, test changes, and a sequential build checklist.

---

## 2. Current state (audited facts)

- **TaskStore module surface** lives in `/home/user/agentos/orchestrator/agentos/storage/local_store.py`.
  `store_factory.store_for()` returns the **module object** today (`return local_store`), so a
  drop-in backend must expose **module-level functions**, not a class. The file store will be a
  module exactly like `local_store.py`.
- **Dataclasses + Protocol** in `/home/user/agentos/orchestrator/agentos/storage/task_store.py`
  (`Project`, `Sprint`, `Task`, `TaskStore` Protocol). These are unchanged and reused verbatim.
- **`store_factory.store_for()`** (`/home/user/agentos/orchestrator/agentos/storage/store_factory.py`)
  reads `task_store` from `project_settings(slug)`; default is `"local"` → `local_store`. `"linear"`
  → `LinearStore` instance.
- **Inbox functions** are part of `local_store.py` (`create_inbox_item`, `get_inbox_item`,
  `list_inbox`, `answer_inbox`, `dismiss_inbox`, `open_inbox_for_task`) — NOT in the `TaskStore`
  Protocol, but part of the module surface the file store must match.
- **Test isolation** (`/home/user/agentos/orchestrator/agentos/tests/conftest.py`) monkeypatches
  `local_store_mod.RUNTIME_DIR` and `local_store_mod.DB_PATH` to a tmp dir, autouse for every test.
- **No `work.sqlite` exists** in this fresh clone — migration cannot be tested against real data
  (test it against a seeded temp sqlite instead, see §9).

### 2.1 Full module surface the file store must replicate

From `local_store.py`. **Every** symbol below must exist in `file_store.py` with the same
signature and the same return shapes (dicts with `depends_on` decoded to a list, `options`
decoded to a list/None, etc.):

**TaskStore Protocol methods (in `task_store.py`):**
`create_project(project) -> str`, `get_project(project_id) -> dict|None`,
`list_projects() -> list[dict]`, `create_sprint(sprint) -> str`,
`list_sprints(project_id) -> list[dict]`, `create_task(task) -> str`,
`get_task(task_id) -> dict|None`,
`list_tasks(project_id=None, sprint_id=None, status=None) -> list[dict]`,
`update_task_status(task_id, status, reason=None) -> None`,
`update_task(task_id, **fields) -> None`, `link_run(task_id, run_id) -> None`,
`ready_tasks(sprint_id) -> list[dict]`, `stats() -> dict`.

**Inbox functions (module-level, not in the Protocol):**
`create_inbox_item(prompt, *, kind="question", from_agent=None, run_id=None, task_id=None, sprint_id=None, options=None) -> str`,
`get_inbox_item(item_id) -> dict|None`, `list_inbox(status="open") -> list[dict]`,
`answer_inbox(item_id, answer, answered_by="human") -> None`,
`dismiss_inbox(item_id) -> None`, `open_inbox_for_task(task_id) -> list[dict]`.

Return-shape contracts proven by existing tests that must keep passing:
- `get_task(...)["depends_on"]` is a **list** (test_task_store: `== ["a","b"]`).
- `update_task_status(tid, status, reason=...)` appends the reason so it is visible in the
  task's `description` (test_task_store `test_update_task_status_and_reason`:
  `"started work" in t["description"]`). The file store records this in the body status-history
  log AND keeps it discoverable via `description` for back-compat (see §4.3).
- `stats()` returns `{"total_projects", "total_tasks", "by_status", "per_project"}`.
- `get_inbox_item(...)["options"]` is the original list (test_ask_human:
  `== ["Stripe","Paddle"]`); `["status"]` flows `open → answered`.

---

## 3. `work/` tree layout (PINNED)

New **tracked** top-level tree, parallel to `projects/`, `global/`, `memory/`:

```
work/
  projects/<project-id>.md      # one project work-record
  sprints/<sprint-id>.md        # one sprint
  tasks/<task-id>.md            # one task (frontmatter + body w/ status history)
  inbox/<item-id>.md            # one work-layer inbox item (ask_human question)
  .gitkeep                      # keep the tree present when empty
```

### 3.1 Filename scheme — DECISION: `<id>.md` (stable uuid, no slug)

Use the **canonical uuid** as the filename: `work/tasks/<task.id>.md`, etc.

**Justification (stability over prettiness, as the brief requires):**
- The brief offers `<id>.md` vs `<shortid>-<title-slug>.md`. Slugs invite rename-on-title-change
  (forbidden) and two tasks with the same title would need disambiguation. The id is already the
  canonical reference for `depends_on`, `parent_task_id`, `link_run`, and every consumer
  (`get_task(task_id)`, API path `/api/tasks/{task_id}`). Naming the file by id makes
  `get_task` an **O(1) path read** (`work/tasks/{id}.md`) with no directory scan — important
  without sqlite indexes.
- The human-readable title lives in the frontmatter/body, so browsing in an editor still shows it.
- Trade-off accepted: filenames aren't self-descriptive in a plain `ls`. This is the right call
  for a machine-authoritative store keyed by id; humans browse via the dashboard or by grepping
  frontmatter `title:`.

`project-id`, `sprint-id`, `item-id` files follow the same `<id>.md` rule.

### 3.2 Why one file per entity

- **Clean git merges:** edits to different tasks touch different files → no merge conflicts
  between unrelated work. A conflict only arises if two sessions edit the *same* entity.
- **O(1) point reads** by id; `list_*` globs the directory.
- Mirrors the memory tier's one-fact-per-file ergonomics.

---

## 4. File schemas (PINNED)

Format: **YAML frontmatter + markdown body**, consistent with the memory tier. Use
`yaml.safe_dump`/`safe_load` (PyYAML is already a dependency — `/home/user/agentos/orchestrator/agentos/core/config.py` imports `yaml`).

### 4.1 Project — `work/projects/<id>.md`

```markdown
---
id: <uuid>
slug: <slug or "">
name: <name>
repo_path: <path or null>
description: <text or null>
status: active            # active / paused / archived
lead_agent: <name or null>
created_at: <iso8601>
---

# <name>

<description body, optional — mirrors `description` frontmatter for human reading>
```

Frontmatter keys map 1:1 to the `Project` dataclass fields (incl. `repo_path`, which the brief's
proposed layout omitted but `local_store` and `sprint_executor._approval_mode`/repo resolution
both need — **keep `repo_path`**). `get_project` returns the frontmatter as a dict (same keys as
the sqlite row).

### 4.2 Sprint — `work/sprints/<id>.md`

```markdown
---
id: <uuid>
project_id: <project uuid>
name: <name>
goal: <text or null>
status: planned           # planned / active / done / cancelled
starts_at: <iso8601 or null>
ends_at: <iso8601 or null>
created_at: <iso8601>
---

# <name>

<goal>
```

### 4.3 Task — `work/tasks/<id>.md`

```markdown
---
id: <uuid>
project_id: <project uuid>
sprint_id: <sprint uuid or null>
status: backlog           # backlog/ready/in_progress/blocked/review/done/cancelled
assignee: <agent name or "human" or null>
priority: medium          # high / medium / low
depends_on: []            # list of task uuids (YAML list, NOT json string)
acceptance_criteria: <text or null>
estimate_minutes: <int or null>
parent_task_id: <uuid or null>
created_by: human         # human / agent / workflow / plan-project / onboard:*
last_run_id: <uuid or null>
created_at: <iso8601>
title: <short title>
---

# <title>

<description>

## Status history
- <iso8601> backlog → in_progress: started work
- <iso8601> in_progress → done
```

**`title` placement:** kept in frontmatter (so `list_tasks`/`stats` read it without parsing the
body) AND as the H1 for human reading. The body holds `description` + the append-only
**Status history** log.

**`update_task_status(task_id, status, reason)` behavior (matches sqlite semantics):**
1. Set frontmatter `status`.
2. Append a line to the `## Status history` section:
   `- <now> <old_status> → <new_status>[: <reason>]`.
3. **Back-compat:** because `test_update_task_status_and_reason` asserts the reason appears in
   `description`, the file store's `get_task(...)["description"]` must surface the reason. Implement
   by composing the returned `description` field as: the body's description paragraph **plus** the
   status-history lines that carried a `reason` (i.e. `description = body_description + "\n" +
   "\n".join(history_reason_lines)`). This reproduces sqlite's
   `description = description || "\n[status→X] reason"` without losing the structured history. Pin
   the exact composed format so the dev hits the assertion: include the literal reason substring.

**`description` round-trip:** `create_task` writes the dataclass `description` into the body's
description paragraph. `get_task`/`list_tasks` reconstruct `description` = body description (+ any
reason lines as above). `update_task(description=...)` rewrites the body description paragraph.

### 4.4 Inbox item — `work/inbox/<id>.md`

```markdown
---
id: <uuid>
created_at: <iso8601>
from_agent: <name or null>
run_id: <uuid or null>
task_id: <uuid or null>
sprint_id: <uuid or null>
kind: question            # question / approval / decision
status: open              # open / answered / dismissed
options: null             # YAML list or null
answer: null
answered_at: null
answered_by: null
---

<prompt text>
```

The `prompt` lives in the body (free text, possibly multi-line); `answer` in frontmatter
(set on answer). `get_inbox_item`/`list_inbox` return a dict with `prompt` (from body),
`options` (list/None), and all frontmatter keys — matching the sqlite row shape.

### 4.5 Cross-file computations (no SQL)

- **`list_tasks(project_id, sprint_id, status)`** — glob `work/tasks/*.md`, parse frontmatter,
  filter in Python by the provided (non-None) keys.
- **`ready_tasks(sprint_id)`** — load tasks where `sprint_id == arg AND status == "ready"`;
  compute `done_ids = {t.id for t in all tasks if status == "done"}` (global, matching sqlite which
  scans all tasks for done deps); return ready tasks whose every `depends_on` id is in `done_ids`.
- **`stats()`** — count project files; count task files; group by `status` and by `project_id`.
- **`open_inbox_for_task(task_id)`** — glob inbox, filter `task_id == arg AND status == "open"`.

### 4.6 Deterministic ordering (replaces sqlite `ORDER BY`)

sqlite used `ORDER BY created_at DESC` for `list_projects`, `list_sprints`, `list_inbox` and
`ORDER BY created_at DESC` for `list_tasks`. Reproduce **without** sqlite:

- `list_projects`, `list_sprints`, `list_tasks`, `list_inbox`: sort by **`created_at` descending,
  then `id` descending** as a stable tiebreaker (two entities created in the same ISO microsecond
  must still order deterministically across processes — id break makes it total).
- `ready_tasks`: order is irrelevant to correctness (the sprint executor re-sorts by priority then
  `created_at` in `/home/user/agentos/orchestrator/agentos/core/sprint_executor.py` lines ~231-232),
  but return sorted by `created_at` asc, then id, for determinism.

> Note: existing tests don't assert a specific list order, but determinism prevents flaky diffs and
> matches sqlite's stable behavior.

---

## 5. The file store module (`storage/file_store.py`)

New file `/home/user/agentos/orchestrator/agentos/storage/file_store.py`. A **module** (not a
class), exposing every symbol in §2.1.

- **Overridable root:** module-level `WORK_DIR = config.AGENTOS_ROOT / "work"` (i.e.
  `Path.home() / "agentos" / "work"`). Tests monkeypatch `file_store.WORK_DIR` to a tmp dir,
  exactly mirroring how sqlite tests override `local_store.DB_PATH` (§8). All path helpers derive
  from `WORK_DIR` read **at call time** (not captured at import) so monkeypatch takes effect:
  e.g. `def _tasks_dir(): return WORK_DIR / "tasks"`.
- **Directory bootstrap:** a `_ensure_dirs()` that `mkdir(parents=True, exist_ok=True)` the four
  subdirs on first write (analogous to `_conn()` creating the schema).
- **Serialization helpers:** `_read_doc(path) -> (frontmatter_dict, body_str)` and
  `_write_doc(path, frontmatter_dict, body_str)` using `yaml.safe_load`/`safe_dump` with a `---`
  fence. Centralize so all entities share one parser.
- **`depends_on`** is a native YAML list in frontmatter (no json.dumps). `update_task` accepts a
  list and stores it as-is (drop the json-encoding branch that `local_store.update_task` has).
- **Concurrency:** last-write-wins within a process (read-modify-write the single file). One file
  per entity means cross-entity writes never collide. Document that two processes editing the same
  task is the only conflict surface (acceptable; see §10).
- **No sqlite import.** The file store reads/writes files directly and must function with sqlite
  entirely absent.

---

## 6. Default backend switch (`store_factory.py`)

Change `/home/user/agentos/orchestrator/agentos/storage/store_factory.py`:

- Add backend name **`file`** (alias `git` accepted for the same module).
- **Default becomes the file store.** `store_for()` logic:
  - `task_store` setting `== "linear"` → `LinearStore` (unchanged).
  - `task_store` setting `in ("file", "git")` OR **unset/default** → return `file_store` module.
  - `task_store == "local"` → return `local_store` module (legacy/cache, still selectable).
  - `"convex"`/unknown → fall back to the **file** store (was `local`).
- `backend_name()` default string changes from `"local"` to `"file"`.

> Acceptance: with no `task_store:` in settings, `store_for(None)` returns the `file_store` module;
> `backend_name(None) == "file"`.

### 6.1 Direct-import consumers (the real breakage risk)

`store_factory.store_for()` is **barely used** — only `cli.py sync-tasks` and that's it. **Every
other consumer imports `local_store` directly**, so flipping the factory default alone changes
nothing for them. To actually make tasks git-backed, the direct importers must point at the file
store. Two viable strategies — **pick Strategy A** (least churn, lowest risk):

**Strategy A (CHOSEN): re-point the direct imports module-by-module.**
Replace `from agentos.storage import local_store` with `from agentos.storage import file_store as
local_store` (aliased) in each direct consumer, OR better, introduce a single indirection:

- Add `from agentos.storage import store_factory` and have call sites use
  `store_factory.default_store()` — a new tiny helper returning `store_for(None)` (the file store).
  But that's a wide diff. **Simplest correct move:** in each consumer module, change the import to
  `from agentos.storage import file_store as store` and do a mechanical rename of `local_store.` →
  `store.` within that module. This keeps each module backend-agnostic via one import line.

Because tests monkeypatch `local_store` by module object, **the test fixture must be updated to
also isolate `file_store.WORK_DIR`** (§8) — and the consumers under test must reference the file
store. To keep tests' existing `from agentos.storage import local_store` references working AND
exercise the new default, the cleanest path is:

> **Decision:** consumers import the **file store**, aliased as the local name they already use, so
> the body diff is just the import line. Tests are updated to import/seed via `file_store` (the new
> source of truth). The few tests that specifically pin sqlite behavior stay on `local_store`
> (they validate the legacy backend still works).

Per-file consumer audit and exact fix in §7.

---

## 7. Consumer audit (every caller of the task store / inbox)

Legend: **path** · functions called · access style · fix.

| Consumer | Store/inbox functions used | Access style | Fix |
|---|---|---|---|
| `/home/user/agentos/orchestrator/agentos/storage/store_factory.py` | returns store module | factory | Default → `file_store`; add `file`/`git`; convex/unknown → file. |
| `/home/user/agentos/orchestrator/agentos/storage/local_store.py` | (the legacy backend itself) | n/a | Unchanged. Stays selectable as `local`. |
| `/home/user/agentos/orchestrator/agentos/storage/task_store.py` | dataclasses + Protocol | n/a | Unchanged (reused by file store). |
| `/home/user/agentos/orchestrator/agentos/storage/linear_store.py` | (alt backend) | n/a | Unchanged. |
| `/home/user/agentos/orchestrator/agentos/core/sprint_executor.py` | `list_tasks`, `get_project`, `ready_tasks`, `update_task_status`, `link_run` | **direct `from agentos.storage import local_store`** (line 21) | Re-point import to `file_store` (aliased). High-value path: this is the autonomous loop. |
| `/home/user/agentos/orchestrator/agentos/core/ask_human.py` | `create_inbox_item`, `get_inbox_item`, `answer_inbox`, `list_inbox`, `open_inbox_for_task`, `get_task`, `update_task_status` | **direct import** (line 15) | Re-point import to `file_store`. Inbox now git-backed → blocked work travels. |
| `/home/user/agentos/orchestrator/agentos/core/plan_project.py` | `create_sprint`, `create_task`, `update_task` | **direct import** (inside `plan_project()`, line 53) | Re-point import to `file_store`. |
| `/home/user/agentos/orchestrator/agentos/core/onboard.py` | `list_projects`, `create_project`, `get_project`, `create_task`, `list_tasks` (in `ensure_work_project`/`import_tasks`) | **direct import** (lines 255, 277) | Re-point both imports to `file_store`. |
| `/home/user/agentos/orchestrator/agentos/core/briefing.py` | `list_tasks`, `list_inbox` | **direct import** (lines 47, 81) | Re-point import to `file_store`. |
| `/home/user/agentos/orchestrator/agentos/entrypoints/api_server.py` | `list_projects`, `create_project`, `get_project`, `list_sprints`, `create_sprint`, `list_tasks`, `create_task`, `get_task`, `update_task_status`, `stats`, `list_inbox` | **direct import** (line 26) | Re-point import to `file_store`. The dashboard reads through these endpoints (no separate dashboard fix needed). |
| `/home/user/agentos/orchestrator/agentos/entrypoints/mcp_server.py` | `list_projects`, `list_sprints`, `list_tasks` (in `_resolve_project` + tools) | **direct import** (inside tool fns) | Re-point all `from agentos.storage import local_store` to `file_store`. |
| `/home/user/agentos/orchestrator/agentos/entrypoints/cli.py` | `list_projects`, `stats`, `list_tasks`, `get_project`, `create_task`, `list_inbox`, `create_project` (projects/tasks/task-add/inbox/sync-projects cmds) + `store_factory` (sync-tasks) | **direct import** (per-command) + factory (sync-tasks) | Re-point per-command `local_store` imports to `file_store`. `sync-tasks` keeps using `store_factory` (now defaults to file). |

**Dashboard:** the Next.js/SPA dashboard (`/home/user/agentos/dashboard/`) talks **only** to the
aiohttp API (`api_server.py`) — confirmed by `api_server.py` docstring and the SSE/`/api/*`
endpoints. Fixing `api_server.py` covers it; no dashboard code change.

**`run_store`/`budget`/`killswitch`:** separate concerns (runs.sqlite, spend file, pause file) —
**out of scope**, untouched. Only the Work layer (work.sqlite) moves to files.

**Net:** 8 modules change one import line each (sprint_executor, ask_human, plan_project, onboard
×2-imports, briefing, api_server, mcp_server, cli) + store_factory's default logic. No call-site
logic changes — the file store is signature-compatible.

---

## 8. Test-suite impact (full suite must stay green)

The autouse fixture in `/home/user/agentos/orchestrator/agentos/tests/conftest.py` currently
isolates `local_store.RUNTIME_DIR`/`DB_PATH`. Add file-store isolation:

**conftest.py change (PINNED):** in `isolate_runtime`, also:
```
import agentos.storage.file_store as file_store_mod
monkeypatch.setattr(file_store_mod, "WORK_DIR", tmp_path / "work")
```
(no need to mkdir — the store's `_ensure_dirs()` creates subdirs on first write). Keep the existing
`local_store` sqlite patching so the legacy backend tests still isolate cleanly.

Per-test-file plan:

| Test file | Current assumption | Change |
|---|---|---|
| `test_task_store.py` | Tests `local_store` (sqlite) directly. | **Keep as-is** — it validates the legacy sqlite backend, which stays selectable. No change. Optionally rename intent in docstring. |
| **`test_file_store.py` (NEW)** | — | Port the full `test_task_store.py` + `test_ask_human` surface against `file_store`: project/sprint/task CRUD, `depends_on` round-trip as a list, `update_task_status` reason visible in `description`, `ready_tasks` dependency gating, `stats`, full inbox lifecycle (`create/get/list/answer/dismiss/open_inbox_for_task`), ordering determinism. Also assert files actually land under `WORK_DIR/tasks/<id>.md` etc. (git-backed proof). |
| `test_ask_human.py` | `from agentos.storage import local_store`; asserts inbox + resume. | Re-point its `local_store` import to `file_store` (the new source of truth) since `ask_human` now uses the file store. The assertions are backend-agnostic and pass unchanged. |
| `test_work_api.py` | `from agentos.storage import local_store`; seeds via local_store, reads via API. | Re-point seeding import to `file_store` (api_server now uses file store; seeding and reading must hit the SAME store). |
| `test_phase6_api.py` | `from agentos.storage import local_store` + `ask_human`. | Re-point `local_store` import to `file_store`. |
| `test_sprint_executor.py` | `from agentos.storage import local_store`; seeds + asserts task status. | Re-point `local_store` import to `file_store` (sprint_executor now reads/writes the file store). All state-machine assertions are backend-agnostic. |
| `test_plan_project.py` | `from agentos.storage import local_store`; reads back written tasks. | Re-point `local_store` import to `file_store`. |
| `test_mcp_server.py` | Tools call `local_store` internally; current tests don't touch work-layer tools (only agents/dispatch/budget). | No change required for the existing assertions. (If work-layer tool tests are added later, seed via `file_store`.) |
| `test_onboard.py` | `ensure_work_project`/`import_tasks` use `local_store`; tests `from agentos.storage import local_store`. | Re-point the two in-test `local_store` imports to `file_store` (onboard now writes the file store). `import_tasks` idempotency-by-title and token-match assertions are backend-agnostic. |
| `test_briefing.py` | `test_tasks_section_lists_open_tasks` seeds via `local_store`. | Re-point that import to `file_store` (briefing now reads the file store). |
| `test_linear_store.py` | Pure Linear, no local store. | No change. |
| `test_api_server.py`, `test_cost_analytics.py` | No work-layer store. | No change. |

**Principle for the rename:** wherever a test seeds work-layer state and then asserts via a
consumer (API/executor/briefing/mcp), the **seed import and the consumer must reference the same
store** — and that store is now `file_store`. The mechanical rule: in those test files, change
`from agentos.storage import local_store` → `from agentos.storage import file_store as
local_store` so the test body is otherwise untouched.

**Bar:** `cd /home/user/agentos/orchestrator && python -m pytest` is fully green, with the new
`test_file_store.py` included.

---

## 9. Migration: `work.sqlite` → `work/` files

One-time, **idempotent** importer; **no-op when sqlite absent** (the case in this fresh container).

- **Location:** a function in `file_store.py` (or a small `storage/work_migrate.py`)
  `migrate_from_sqlite(db_path=None) -> dict` that:
  1. Resolves `db_path` (default `local_store.DB_PATH`); if the file doesn't exist, return
     `{"migrated": 0, "reason": "no work.sqlite"}` (no-op).
  2. Reads each table (`projects`, `sprints`, `tasks`, `inbox`) via sqlite, decoding `depends_on`
     (json→list) and `options` (json→list).
  3. Writes each row as a `work/<kind>/<id>.md` doc **only if the file doesn't already exist**
     (idempotent; re-running adds nothing). For tasks, fold the sqlite `description` (which may
     already contain `[status→X] reason` suffixes from `update_task_status`) into the body
     description + seed a single Status-history line noting `migrated`.
  4. Returns `{"migrated_projects", "migrated_sprints", "migrated_tasks", "migrated_inbox",
     "skipped"}`.
- **CLI:** add `agentos work migrate` (a Typer sub-app `work` with a `migrate` command) in
  `/home/user/agentos/orchestrator/agentos/entrypoints/cli.py`. Prints the counts; safe to run
  repeatedly; prints "nothing to migrate" when sqlite is absent.
- **Testing migration (no real data here):** the dev seeds a **temp sqlite** by calling
  `local_store` functions with `local_store.DB_PATH` pointed at a tmp file (the existing pattern),
  then runs `migrate_from_sqlite(tmp_db)` and asserts the `work/` files exist with matching
  frontmatter, that `depends_on`/`options` round-trip as lists, and that a second run reports all
  `skipped` (idempotent). Add this to `test_file_store.py` or a `test_work_migrate.py`.

---

## 10. Concurrency, merge & ordering story

- **One file per entity → clean git merges.** Unrelated task edits never conflict. A real conflict
  only occurs when two branches/sessions edit the *same* entity file — then it's a normal git
  text-merge on a small YAML+markdown doc, human-resolvable.
- **Within a process:** last-write-wins read-modify-write on the single entity file. No locking
  needed for the single-user, mostly-sequential orchestrator. The sprint executor processes tasks
  one at a time per pass (`/home/user/agentos/orchestrator/agentos/core/sprint_executor.py`), so
  concurrent same-task writes from the executor don't happen.
- **Ordering without SQL:** §4.6 — sort by `created_at` desc then `id` desc for `list_*`; stable
  and deterministic across processes/machines.
- **Append-only status history** in the task body gives an auditable trail that travels in git
  (richer than sqlite's single mutated `description`).

---

## 11. `.gitignore` / tracking

- **Add nothing to ignore** for `work/`. It must be **tracked** (the whole point).
- Confirm `work/` is NOT covered by an existing ignore rule — current
  `/home/user/agentos/.gitignore` ignores `orchestrator/runtime/*`, `/projects/*/sources/`,
  `/inbox/*`, `/briefings/*`, `/workspace/`, `worktrees/`, dashboard build dirs. `work/` matches
  **none** of these → tracked by default. ✅
- Add `work/.gitkeep` so the empty tree exists on a fresh clone before the first task is written.
- Keep `orchestrator/runtime/*` ignored (sqlite stays local cache/legacy). No change to that line.
- **Acceptance:** `git check-ignore work/tasks/<id>.md` returns nothing (not ignored);
  `git status` shows new `work/**` files as tracked/added.

---

## 12. Docs updates

- **`/home/user/agentos/README.md` Phases table:** add a row, e.g.
  `| 11 — Git-backed tasks | ✅ Done | Work layer (projects/sprints/tasks/inbox) is tracked
  markdown under work/; sqlite demoted to legacy cache; tasks travel with the repo |`.
- **`/home/user/agentos/docs/DESIGN.md` §Task stores (Phase 7):** add a short ADR-style note: file
  store is now the default backend; the `work/` tree is authoritative; sqlite/Linear remain
  selectable via `settings.yaml task_store:`; cite that this gives tasks the same "travels with the
  repo" property as project-tier memory (§Tier 3 / Memory).
- **`/home/user/agentos/config/settings.yaml`:** update the comment block to list `task_store`
  options as `file (default) | local | linear` and show `file`/`git` as the new default.

---

## 13. MVP build checklist (developer follows in order)

Each item has an acceptance criterion. Do them in sequence; run the suite at the gates.

1. **Create `storage/file_store.py` skeleton + doc helpers.**
   Module-level `WORK_DIR = config.AGENTOS_ROOT / "work"`; `_ensure_dirs()`; `_read_doc`/`_write_doc`
   (yaml frontmatter + body); path helpers that read `WORK_DIR` at call time.
   *Acceptance:* `import file_store` works; `_write_doc` then `_read_doc` round-trips a frontmatter
   dict + body string exactly.

2. **Implement projects + sprints functions** (`create_project`, `get_project`, `list_projects`,
   `create_sprint`, `list_sprints`) per §4.1/§4.2, ordering per §4.6.
   *Acceptance:* create→get returns the dataclass fields incl. `repo_path`/`status` defaults;
   `list_projects` returns created records, newest-first.

3. **Implement tasks functions** (`create_task`, `get_task`, `list_tasks`, `update_task_status`,
   `update_task`, `link_run`, `ready_tasks`, `stats`) per §4.3/§4.5/§4.6.
   *Acceptance:* `get_task(...)["depends_on"]` is a list; `update_task_status(tid,"in_progress",
   reason="started work")` then `get_task` has `status=="in_progress"` and `"started work" in
   description`; `ready_tasks` gates on all deps `done`; `stats()` has the four keys with correct
   counts. Files exist at `WORK_DIR/tasks/<id>.md`.

4. **Implement inbox functions** (`create_inbox_item`, `get_inbox_item`, `list_inbox`,
   `answer_inbox`, `dismiss_inbox`, `open_inbox_for_task`) per §4.4.
   *Acceptance:* create→get returns `options` as the original list and `status=="open"`;
   `answer_inbox` flips to `answered` with `answer`/`answered_at`/`answered_by`; `open_inbox_for_task`
   returns only open items for that task.

5. **Add `WORK_DIR` isolation to conftest** (§8) and write **`test_file_store.py`** covering items
   2-4 + ordering + the file-on-disk assertions.
   *Acceptance:* `python -m pytest agentos/tests/test_file_store.py` green; tests prove files under
   the tmp `WORK_DIR`.

6. **Switch `store_factory` default to the file store** (§6): add `file`/`git`, default + convex/
   unknown → `file_store`, `local` still returns `local_store`, `backend_name` default `"file"`.
   *Acceptance:* with empty settings, `store_for(None) is file_store` and `backend_name(None)=="file"`;
   `store_for` for a `task_store: local` project returns `local_store`.

7. **Re-point the 8 direct-import consumers** to the file store (§6.1/§7): sprint_executor,
   ask_human, plan_project, onboard (both imports), briefing, api_server, mcp_server, cli. One
   import-line change each (alias as `local_store`/`store` to minimize body diff).
   *Acceptance:* grep shows no Work-layer consumer importing `local_store` for live use except
   `local_store.py` itself and the migration importer; app imports cleanly.

8. **Update the affected tests** (§8 table) to seed/read via `file_store` (mechanical
   `import file_store as local_store`), leaving `test_task_store.py` on sqlite.
   *Acceptance:* `cd orchestrator && python -m pytest` fully green.

9. **Migration importer + CLI** (§9): `migrate_from_sqlite()` (idempotent, no-op when absent) and
   `agentos work migrate`. Add migration tests against a seeded temp sqlite.
   *Acceptance:* seeded-temp-sqlite migration produces matching `work/` files; second run reports
   all skipped; absent sqlite → `{"migrated":0,"reason":"no work.sqlite"}`. Migration test green.

10. **Tracking / `.gitignore` check** (§11): add `work/.gitkeep`; confirm `work/**` not ignored.
    *Acceptance:* `git check-ignore work/tasks/x.md` prints nothing; `git status` lists `work/`
    files as new/tracked.

11. **Docs** (§12): README phases row, DESIGN ADR note, settings.yaml comment.
    *Acceptance:* the three docs mention the file store as default and "tasks travel with the repo".

12. **Final gate:** full suite green; manual smoke — `agentos task-add ... -p <pid>` writes a
    `work/tasks/<id>.md`; `agentos tasks` lists it; `agentos serve` → `/api/tasks` returns it.
    *Acceptance:* a created task appears as a tracked file AND via CLI AND via the API.

---

## 14. Phased roadmap (deferred)

- **P2 — sqlite as a read cache.** Optionally rebuild `work.sqlite` from `work/` for fast
  dashboard queries on large trees, treating files as truth and cache as derived (invalidate on file
  mtime). Not needed at current scale (glob over a few hundred small files is fine).
- **P2 — conflict UI.** Surface git merge conflicts in `work/` in the dashboard with a resolve view.
- **P3 — indexing.** A generated `work/index.md` (or `.json`) catalog for human browsing, rebuilt on
  write (mirrors `global/memory/index.md`).
- **P3 — GitHub task store.** A third backend (issues) behind the same Protocol, parallel to Linear.

---

## 15. Open questions / assumptions

1. **`repo_path` on the Project frontmatter** — the brief's proposed project frontmatter omitted it,
   but `local_store` and `sprint_executor` need it. *Assumption:* keep it (§4.1). Flag if the user
   wants it dropped.
2. **`description` reason back-compat** — I preserve sqlite's "reason shows up in description"
   behavior (test depends on it) while also writing structured Status-history (§4.3). If the user
   prefers to relax that test instead, the composition step can be simpler. *Assumption:* keep the
   test green as the bar requires; don't edit `test_task_store.py`.
3. **Consumer re-point vs. factory-only** — flipping only `store_factory` would NOT move the
   direct-import consumers (the majority). *Assumption:* re-point the direct imports (§6.1 Strategy
   A) — this is required to actually make tasks git-backed, and is the lowest-churn way.
4. **YAML for `created_at` timestamps** — store as ISO-8601 strings (quoted) to avoid PyYAML
   coercing them to `datetime` objects; `_read_doc` should treat all values as strings except
   `depends_on`/`options` (lists) and `estimate_minutes` (int|null). *Assumption:* pinned this way;
   ensures round-trip equality with sqlite's TEXT columns.
5. **`work/` location** — top-level `work/` (sibling of `projects/`), not under `orchestrator/`, so
   it's clearly memory-like shared truth. *Assumption:* confirmed by the brief ("new top-level
   `work/` tree").
