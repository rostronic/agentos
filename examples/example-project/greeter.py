"""greeter — a deliberately tiny module so the example project is RUNNABLE.

This is the "real code" half of the example: a few lines of Python with a
trivial test (test_greeter.py). It exists so the projects-quickstart can show
a complete loop — register a real repo, run its test, then dispatch an agent at
it and watch the project's memory load.

Keep it boring on purpose: the point is the AgentOS wiring, not the logic.
"""

from __future__ import annotations


def greet(name: str) -> str:
    """Return a friendly greeting. Empty/whitespace name → a neutral fallback."""
    name = (name or "").strip()
    return f"Hello, {name}!" if name else "Hello there!"


if __name__ == "__main__":  # pragma: no cover - tiny CLI demo
    import sys

    print(greet(sys.argv[1] if len(sys.argv) > 1 else ""))
