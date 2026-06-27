# example-project — project-tier memory (runnable demo)

A complete, working mini-project you can register with AgentOS to see the memory
tiers load end-to-end. It is intentionally tiny: a one-function Python module
(`greeter.py`), a trivial test (`test_greeter.py`), and the project-tier memory
below. See [docs/projects-quickstart.md](../../docs/projects-quickstart.md) for the
register → test → dispatch walkthrough.

A project's memory travels **with its code**: this `AGENTS.md` is the entry point,
and durable facts live in `memory/`. When a dispatch is scoped to this project
(`--project example-project`), this tier loads on top of the workspace and global
tiers — narrowest tier wins on conflict.

## What this project is

`greeter` is a demo library exposing a single function, `greet(name)`, that returns
a friendly greeting and falls back to a neutral `"Hello there!"` for an empty name.
It exists to exercise the AgentOS onboarding + memory-loading flow, not to do real
work.

## Key facts

- **Repo / layout:** flat, self-contained — `greeter.py` (code), `test_greeter.py`
  (tests), `memory/` (project-tier facts). No build step, no dependencies.
- **Stack:** plain Python 3.11+; tests run under `pytest` with **no plugins or
  fixtures** required.
- **Run the tests:** `python -m pytest` from this directory (the test imports
  `greeter` by module name, so run it from here or add this dir to `PYTHONPATH`).
- **Convention:** `greet("")` and whitespace-only input must keep returning the
  neutral fallback — that contract is covered by a test; don't break it.

## Pointers

- Durable, curated facts: [memory/curated.md](memory/curated.md)
- Session handoffs (ephemeral): `memory/handoffs/`

> Register this project in `config/projects.yaml` (see
> `config/projects.yaml.example`) so the orchestrator knows its slug, workspace,
> and memory path. Quickstart: [docs/projects-quickstart.md](../../docs/projects-quickstart.md).
