"""Memory context assembly — the READ path of the layered memory subsystem.

Given an agent (+ optional project), gather layered memory into a single block to
prepend to the agent's system prompt. Fully **deterministic** (keyword relevance,
no model call) so it is safe on the dispatch hot path and never adds API cost.

Tiers, broad → narrow (output order; narrower appears later = "wins" on conflict):
  1. global     <root>/global/memory/*.md            always
  2. workspace  <root>/workspaces/<ws>/memory/*.md    when the project's workspace is known
  3. project    <root>/<memory_path>/memory/*.md      when the project is known
  4. per-agent  <root>/memory/per-agent/<agent>/*.md  global-scoped per-agent facts

Each tier has its OWN character budget, so a long global file can never crowd out a
fresh project fact (the budget is per-tier, not one flat cap). `index.md` files are
skipped (they're tables of contents, not facts).

This module only READS. Writing/curation lives in `memory_store.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentos.core import config

# Module-level so tests can monkeypatch (matches the conftest convention).
AGENTOS_ROOT = config.AGENTOS_ROOT

# Per-tier character budgets. Project gets the most (most specific / most useful);
# truncation is implicitly "least-specific dropped first" because each tier is
# capped independently and selection within a tier is relevance-ranked.
TIER_BUDGETS: dict[str, int] = {
    "global": 2000,
    "workspace": 1500,
    "project": 3000,
    "per-agent": 1000,
}

_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_WORD_RE = re.compile(r"[a-z0-9]+")


def _strip_frontmatter(text: str) -> str:
    return _FRONTMATTER_RE.sub("", text, count=1).strip()


def _tokens(s: str) -> set[str]:
    return set(_WORD_RE.findall(s.lower()))


def _read_facts(d: Path) -> list[tuple[str, str]]:
    """(filename, stripped-content) for every *.md fact file in `d`, sorted by name.

    `index.md` is skipped — it's a catalog, not a fact. Missing dir → []."""
    if not d.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for p in sorted(d.glob("*.md")):
        if p.name.lower() == "index.md":
            continue
        try:
            content = _strip_frontmatter(p.read_text(encoding="utf-8"))
        except OSError:
            continue
        if content:
            out.append((p.name, content))
    return out


def _tier_sources(
    agent_name: str | None, project: str | None, root: Path
) -> list[tuple[str, Path]]:
    """Ordered (tier, directory) list for the given agent/project."""
    sources: list[tuple[str, Path]] = [("global", root / "global" / "memory")]

    workspace = config.workspace_for_project(project)
    if workspace:
        sources.append(("workspace", root / "workspaces" / workspace / "memory"))

    if project:
        memory_path = config.project_config(project).get("memory_path")
        if memory_path:
            sources.append(("project", root / memory_path / "memory"))

    if agent_name:
        sources.append(("per-agent", root / "memory" / "per-agent" / agent_name))

    return sources


def _select_within_budget(
    facts: list[tuple[str, str]], task_tokens: set[str], budget: int
) -> list[str]:
    """Relevance-rank facts against the task and take them until `budget` is spent.

    With a task: rank by keyword overlap (desc), drop zero-overlap facts once we
    already have something, and never blow the budget on an oversized low-rank fact.
    Without a task: keep filename order until the budget is spent.
    """
    if task_tokens:
        ranked = sorted(
            facts, key=lambda f: len(_tokens(f[1]) & task_tokens), reverse=True
        )
    else:
        ranked = facts

    chosen: list[str] = []
    remaining = budget
    for _name, content in ranked:
        overlap = len(_tokens(content) & task_tokens) if task_tokens else 1
        if task_tokens and overlap == 0 and chosen:
            continue  # irrelevant and we already have content
        if len(content) > remaining and chosen:
            continue  # would overflow; skip rather than truncate mid-fact
        chosen.append(content)
        remaining -= len(content)
        if remaining <= 0:
            break
    return chosen


def build_context(
    agent_name: str | None = None,
    project: str | None = None,
    task: str = "",
    *,
    root: Path | None = None,
    budgets: dict[str, int] | None = None,
) -> str:
    """Assemble the layered-memory block for a dispatch. Returns "" if nothing applies.

    Deterministic and side-effect-free. The caller prepends the result to the
    agent's system prompt (or folds it into the user message for runtimes that own
    their own prompt — see the router wiring).
    """
    root = root or AGENTOS_ROOT
    budgets = budgets or TIER_BUDGETS
    task_tokens = _tokens(task)

    blocks: list[str] = []
    for tier, directory in _tier_sources(agent_name, project, root):
        facts = _read_facts(directory)
        if not facts:
            continue
        chosen = _select_within_budget(facts, task_tokens, budgets.get(tier, 1000))
        if chosen:
            blocks.append(f"### {tier}\n" + "\n\n".join(chosen))

    if not blocks:
        return ""
    header = "## Relevant memory (AgentOS — most specific last)\n"
    return header + "\n\n".join(blocks)
