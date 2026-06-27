---
tier: global
type: preference
---
# Subagents: parallelism + naming

## "Spin up a subagent" means run in parallel
The user's model of a subagent is a **doer that runs in parallel**, not a thing with
extra capabilities. Before spawning, check the real bottleneck:

- **Parallelism** (independent searches, file processing, transforms) → a subagent
  genuinely helps.
- **Capability / auth / rights** blocker → parallelism won't unlock it. Surface the
  real blocker instead of spawning an agent that hits the same wall.
- If inline tool-level parallelism (multiple tool calls in one message) already covers
  it, do that and note a subagent wasn't needed.

Don't read a subagent request as confusion — it's a request for speed.

## Name agents/sessions with the active project's code prefix
Every Agent `description`, `spawn_task` title, or new session name originating from a
project's context is prefixed with that project's short code (e.g. `YF1`, `FAC`, `BLM`).
This keeps the parallel-session list scannable and prevents cross-project confusion.
(Per-command Bash `description`s don't need a prefix — they're command labels, not
session names.)

> Consolidates three former per-project prefix rules into one convention.
