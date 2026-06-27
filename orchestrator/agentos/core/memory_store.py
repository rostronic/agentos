"""Memory store — the WRITE path of the layered memory subsystem.

Primitives used by the curator (`librarian`) and the `agentos memory` CLI to:
  - capture raw run output into the inbox staging area (provider-agnostic),
  - list / clear inbox captures during triage,
  - promote a curated fact into the correct tier (global / workspace / project /
    per-agent), with simple append-dedupe.

Tier directories mirror `memory_context._tier_sources`. This module never decides
*what* is worth keeping — that judgement is the librarian's; this is just safe I/O.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentos.core import config

AGENTOS_ROOT = config.AGENTOS_ROOT


# ----------------------------------------------------------------------------- #
# Tier resolution
# ----------------------------------------------------------------------------- #
def tier_dir(
    tier: str,
    *,
    workspace: str | None = None,
    project: str | None = None,
    agent: str | None = None,
    root: Path | None = None,
) -> Path:
    """Resolve the on-disk directory for a memory tier."""
    root = root or AGENTOS_ROOT
    if tier == "global":
        return root / "global" / "memory"
    if tier == "workspace":
        ws = workspace or config.workspace_for_project(project)
        if not ws:
            raise ValueError("workspace tier needs a workspace (or a known project)")
        return root / "workspaces" / ws / "memory"
    if tier == "project":
        if not project:
            raise ValueError("project tier needs a project slug")
        memory_path = config.project_config(project).get("memory_path")
        if not memory_path:
            raise ValueError(f"project '{project}' has no memory_path in projects.yaml")
        return root / memory_path / "memory"
    if tier == "per-agent":
        if not agent:
            raise ValueError("per-agent tier needs an agent name")
        return root / "memory" / "per-agent" / agent
    raise ValueError(f"unknown tier: {tier}")


# ----------------------------------------------------------------------------- #
# Promote a curated fact into a tier
# ----------------------------------------------------------------------------- #
def _slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "note"


def _norm(text: str) -> str:
    """Whitespace-normalized form for dedupe comparison."""
    return re.sub(r"\s+", " ", text).strip().lower()


def promote(
    tier: str,
    name: str,
    content: str,
    *,
    workspace: str | None = None,
    project: str | None = None,
    agent: str | None = None,
    root: Path | None = None,
) -> Path:
    """Write/append a curated fact into a tier file `<tier_dir>/<slug(name)>.md`.

    If the file exists, the content is appended only if not already present
    (dedupe). Returns the file path. Creates parent dirs as needed.
    """
    d = tier_dir(tier, workspace=workspace, project=project, agent=agent, root=root)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{_slugify(name)}.md"
    block = content.strip()
    if not path.exists():
        path.write_text(block + "\n", encoding="utf-8")
        return path
    append_unique(path, block)
    return path


def append_unique(path: Path, block: str) -> bool:
    """Append `block` to `path` unless its normalized text is already present.

    Returns True if appended, False if it was a duplicate."""
    block = block.strip()
    if not block:
        return False
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if _norm(block) in _norm(existing):
        return False
    sep = "" if existing.endswith("\n") or not existing else "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{sep}\n{block}\n")
    return True


# ----------------------------------------------------------------------------- #
# Inbox capture staging (provider-agnostic)
# ----------------------------------------------------------------------------- #
def inbox_dir(root: Path | None = None) -> Path:
    return (root or AGENTOS_ROOT) / "inbox"


def capture(
    text: str,
    *,
    ts: str,
    run_id: str,
    agent: str | None = None,
    project: str | None = None,
    root: Path | None = None,
) -> Path:
    """Write a raw capture record into the inbox staging area.

    `ts` is supplied by the caller (e.g. an ISO timestamp) so this stays
    deterministic and testable. Filename is `<ts>-<run_id>.md`.
    """
    d = inbox_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    safe_ts = re.sub(r"[^0-9A-Za-z._-]", "_", ts)
    path = d / f"{safe_ts}-{run_id}.md"
    fm = (
        f"---\nts: {ts}\nrun_id: {run_id}\n"
        f"agent: {agent or ''}\nproject: {project or ''}\n---\n"
    )
    path.write_text(fm + text.strip() + "\n", encoding="utf-8")
    return path


def list_captures(root: Path | None = None) -> list[Path]:
    """All pending capture files in the inbox (excludes README/.gitkeep)."""
    d = inbox_dir(root)
    if not d.is_dir():
        return []
    return [
        p
        for p in sorted(d.glob("*.md"))
        if p.name.lower() != "readme.md"
    ]


def clear_capture(path: Path) -> None:
    """Remove a processed capture file (transient staging)."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass
