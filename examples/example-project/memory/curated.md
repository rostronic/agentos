---
tier: project
type: curated
project: example-project
---
# example-project — curated memory

Durable, hand-curated facts about this project. One fact per bullet; keep it tight
and current. This is the project-tier knowledge an agent should always have when
working inside this repo — it loads automatically on any dispatch scoped with
`--project example-project`.

- **Goal:** demonstrate the AgentOS register → test → dispatch loop with a real,
  runnable mini-project; success is the quickstart working end-to-end on a fresh
  clone.
- **Public API:** exactly one function — `greeter.greet(name: str) -> str`. Returns
  `"Hello, {name}!"` for a non-empty name, `"Hello there!"` for empty/whitespace.
- **Tests:** `test_greeter.py` covers the named, whitespace-trimmed, and empty
  cases; run `python -m pytest` from the project directory.
- **Gotcha:** the empty-name fallback is a deliberate contract — changing it breaks
  `test_greet_empty_falls_back`. Trim before deciding (the code uses `name.strip()`).
- **Out of scope:** no persistence, no network, no dependencies. If a change needs
  any of those, it belongs in a different example.

> This is the curated project tier. To prove memory loading, dispatch any agent at
> this project and confirm these facts appear under "Relevant memory → project" —
> see [docs/projects-quickstart.md](../../docs/projects-quickstart.md).
