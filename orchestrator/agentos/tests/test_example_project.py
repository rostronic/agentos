"""Smoke test for the shipped runnable demo at examples/example-project/.

Guards the project showcase flow described in docs/projects-quickstart.md:
  1. The demo's own code + test are intact and importable.
  2. onboarding.discover() picks up the demo's AGENTS.md as a repo source.
  3. memory_context.build_context() loads the demo's curated project-tier memory
     when a dispatch is scoped with project="example-project".

These exercise the REAL files under examples/example-project against the REAL
loaders (no monkeypatched directories), so a regression in the example or in the
register→dispatch→load wiring fails here.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from agentos.core import config
from agentos.core import memory_context as mc

# orchestrator/agentos/tests/ -> repo root is four parents up.
REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_DIR = REPO_ROOT / "examples" / "example-project"


def test_example_dir_is_present():
    assert (EXAMPLE_DIR / "greeter.py").is_file()
    assert (EXAMPLE_DIR / "test_greeter.py").is_file()
    assert (EXAMPLE_DIR / "AGENTS.md").is_file()
    assert (EXAMPLE_DIR / "memory" / "curated.md").is_file()


def test_greeter_module_contract():
    """Load greeter.py directly and check the public contract the docs promise."""
    spec = importlib.util.spec_from_file_location(
        "_example_greeter", EXAMPLE_DIR / "greeter.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.greet("Ada") == "Hello, Ada!"
    assert mod.greet("  Grace  ") == "Hello, Grace!"
    assert mod.greet("") == "Hello there!"
    assert mod.greet("   ") == "Hello there!"


def test_onboard_discovers_example_repo_docs(monkeypatch):
    """`agentos onboard example-project` should find the demo's AGENTS.md."""
    from agentos.core import onboard as ob

    monkeypatch.setattr(
        config, "project_config",
        lambda s: {
            "workspace": "business",
            "repo_path": str(EXAMPLE_DIR),
            "memory_path": "examples/example-project",
            "aliases": ["example-project", "demo"],
        },
    )
    # Use a known-empty central dir so we only assert on the repo-doc discovery.
    plan = ob.discover("example-project", central_dir=EXAMPLE_DIR / "nonexistent")
    labels = {s.label for s in plan.sources}
    assert "repo:AGENTS.md" in labels


def test_dispatch_loads_example_project_memory(monkeypatch):
    """The core showcase claim: scoping to example-project loads its curated facts."""
    monkeypatch.setattr(
        config, "projects",
        lambda: {"example-project": {
            "workspace": "business", "memory_path": "examples/example-project",
        }},
    )
    out = mc.build_context(
        "developer", project="example-project", task="what does greet return",
        root=REPO_ROOT,
    )
    assert "greeter.greet" in out          # a curated fact loaded
    assert "### project" in out            # under the project tier
    assert "---" not in out                # frontmatter stripped
