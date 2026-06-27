"""Project onboarding — discover a project's existing knowledge and stage it for
curation into AgentOS project-tier memory.

NON-DESTRUCTIVE: sources are read read-only; this module only writes under
``<root>/projects/<slug>/`` (the interim project-tier home). Nothing is written
into the source locations (the project repo or the legacy Claude store).

Flow:
  discover(slug)  → find candidate sources (repo CLAUDE.md/AGENTS.md + matching
                    central memory files), skipping facts already promoted to the
                    global tier.
  scaffold(plan)  → copy the raw sources into <project>/sources/ (NOT loaded by the
                    memory reader) and create an AGENTS.md stub + memory/ dir.
  curate(plan)    → (optional, costs a run) dispatch the librarian to distill the
                    staged sources into clean project-tier facts under memory/.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from agentos.core import config

# Legacy agent auto-memory store (cwd-slug specific; interim source for migration).
# Overridable via AGENTOS_CENTRAL_MEMORY_DIR; the default points at the prior
# harness's per-cwd memory under ~/.claude (configure per instance).
CENTRAL_MEMORY_DIR = Path(
    os.environ.get(
        "AGENTOS_CENTRAL_MEMORY_DIR",
        str(Path.home() / ".claude" / "projects" / "_legacy" / "memory"),
    )
)

# Legacy task list ("mission control") — interim source for task migration.
# Overridable via AGENTOS_MISSION_CONTROL.
MISSION_CONTROL = Path(
    os.environ.get(
        "AGENTOS_MISSION_CONTROL",
        str(Path.home() / ".agentos-legacy" / "MISSION_CONTROL.md"),
    )
)

# MISSION_CONTROL.md status/priority → work-layer values.
_MC_STATUS = {
    "to-do": "ready", "todo": "ready", "ready": "ready", "blocked": "blocked",
    "planned": "backlog", "backlog": "backlog", "in progress": "in_progress",
    "in-progress": "in_progress", "review": "review", "done": "done",
    "cancelled": "cancelled", "canceled": "cancelled",
}
_MC_PRIORITY = {"high", "medium", "low"}

# Central files already consolidated into the GLOBAL tier — never re-copy per
# project. These are filename stems of facts that live in the global tier; the
# default set covers the framework's universal rules. An instance migrating from a
# prior harness can extend it via AGENTOS_GLOBAL_PROMOTED (comma-separated stems)
# to also skip its own already-promoted, project-specific notes.
GLOBAL_PROMOTED: set[str] = {
    "MEMORY",
    "user_setup",
    "feedback_absolute_file_links",
    "feedback_subagent_parallelism",
    "feedback_worktree_isolation",
    "feedback_stay_in_invoked_project",
    "feedback_subagent_prefix",
    "prod_deploy_approval",
    "feedback_deploy_to_prod_phrase",
    "explicit_prod_deploy_question",
    "secret_rotation_simplicity",
}
GLOBAL_PROMOTED.update(
    s.strip() for s in os.environ.get("AGENTOS_GLOBAL_PROMOTED", "").split(",") if s.strip()
)


@dataclass
class Source:
    label: str
    path: Path
    text: str


@dataclass
class OnboardPlan:
    slug: str
    workspace: str | None
    repo_path: Path | None
    memory_path: str | None
    aliases: list[str]
    sources: list[Source] = field(default_factory=list)


def _aliases(slug: str, cfg: dict) -> list[str]:
    raw = cfg.get("aliases")
    if raw:
        return [a.lower() for a in raw]
    return list({slug.lower(), slug.replace("-", "_").lower(), slug.replace("-", "").lower()})


def _tokens(s: str) -> set[str]:
    return set(t for t in re.split(r"[^a-z0-9]+", s.lower()) if t)


def discover(
    slug: str,
    *,
    central_dir: Path | None = None,
) -> OnboardPlan:
    """Find candidate sources for a project. Read-only; raises on unknown slug."""
    cfg = config.project_config(slug)
    if not cfg:
        raise ValueError(
            f"Unknown project '{slug}' — add it to config/projects.yaml first"
        )
    central_dir = central_dir or CENTRAL_MEMORY_DIR
    repo_path = Path(cfg["repo_path"]).expanduser() if cfg.get("repo_path") else None
    aliases = _aliases(slug, cfg)
    plan = OnboardPlan(
        slug=slug,
        workspace=cfg.get("workspace"),
        repo_path=repo_path,
        memory_path=cfg.get("memory_path"),
        aliases=aliases,
    )

    # 1. The project repo's own instruction / design docs (root level only, to
    #    avoid node_modules etc.). Covers more than CLAUDE.md so thin projects whose
    #    knowledge lives in a README/SOP/design doc still get discovered.
    if repo_path and repo_path.is_dir():
        candidates: list[Path] = [
            repo_path / n for n in ("CLAUDE.md", "AGENTS.md", "README.md", "ARCHITECTURE.md")
        ]
        for pat in ("*_SOP.md", "*_DESIGN.md", "AGENT_DESIGN*.md", "MASTER_PLAN.md", "*_PLAN.md"):
            candidates.extend(sorted(repo_path.glob(pat)))
        seen: set[Path] = set()
        for fp in candidates:
            if fp in seen or not fp.is_file():
                continue
            seen.add(fp)
            try:
                plan.sources.append(Source(f"repo:{fp.name}", fp, fp.read_text(encoding="utf-8")))
            except OSError:
                pass

    # 2. Central memory files matching this project's aliases — match on the
    #    FILENAME only (not content). Content matching caused cross-contamination
    #    (e.g. slug "vehicles" matched a "My Drive/Vehicles/" path in another note).
    #    Central memory files are named after their project (example-shop_project.md,
    #    feedback_shop_dev_localhost.md), so filename tokens are the reliable signal.
    #    Global-promoted facts are skipped (already in the global tier).
    if central_dir.is_dir():
        for fp in sorted(central_dir.glob("*.md")):
            if fp.stem in GLOBAL_PROMOTED:
                continue
            if any(a in _tokens(fp.name) for a in aliases):
                try:
                    text = fp.read_text(encoding="utf-8")
                except OSError:
                    continue
                plan.sources.append(Source(f"central:{fp.name}", fp, text))

    return plan


def _project_dir(plan: OnboardPlan, root: Path) -> Path:
    if not plan.memory_path:
        raise ValueError(f"project '{plan.slug}' has no memory_path in projects.yaml")
    return root / plan.memory_path


def scaffold(
    plan: OnboardPlan,
    *,
    root: Path | None = None,
    overwrite: bool = False,
) -> list[Path]:
    """Stage discovered sources under <project>/sources/ and create the AGENTS.md
    stub + memory/ dir. Returns the paths written. Raw sources go in sources/ which
    the memory reader does NOT load — curation promotes them into memory/."""
    root = root or config.AGENTOS_ROOT
    pdir = _project_dir(plan, root)
    sources_dir = pdir / "sources"
    memory_dir = pdir / "memory"
    sources_dir.mkdir(parents=True, exist_ok=True)
    memory_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for src in plan.sources:
        name = src.label.replace(":", "-").replace("/", "-").lower()
        if not name.endswith(".md"):
            name += ".md"
        dest = sources_dir / name
        if dest.exists() and not overwrite:
            continue
        header = f"<!-- raw source: {src.path} — staged for curation, not yet curated -->\n\n"
        dest.write_text(header + src.text.strip() + "\n", encoding="utf-8")
        written.append(dest)

    agents_md = pdir / "AGENTS.md"
    if not agents_md.exists() or overwrite:
        agents_md.write_text(
            f"# {plan.slug} — project memory (AgentOS)\n\n"
            f"Workspace: {plan.workspace or 'unassigned'}.\n\n"
            f"Curated project-tier facts live in `memory/`. Raw discovered sources are "
            f"in `sources/` awaiting curation (run `agentos onboard {plan.slug} --curate` "
            f"or curate by hand).\n",
            encoding="utf-8",
        )
        written.append(agents_md)

    keep = memory_dir / ".gitkeep"
    if not keep.exists():
        keep.write_text("", encoding="utf-8")
        written.append(keep)

    return written


def curate_prompt(plan: OnboardPlan) -> str:
    """Build the librarian prompt to distill staged sources into project-tier facts."""
    blocks = "\n\n".join(f"### {s.label}\n{s.text}" for s in plan.sources)
    return (
        f"You are curating project-tier memory for the '{plan.slug}' project "
        f"(workspace: {plan.workspace}). From the raw sources below, extract ONLY "
        f"genuinely project-specific, durable facts (stack, deploy policy, dev "
        f"ports, project-specific flags). DROP anything that is a general working "
        f"rule already held globally (agent-naming prefixes, stay-in-project, "
        f"prod-deploy approval/phrase, worktree isolation, secret rotation, "
        f"absolute file links). Deduplicate. Output clean markdown bullet facts, "
        f"no preamble.\n\n=== RAW SOURCES ===\n{blocks}"
    )


def curate(plan: OnboardPlan, *, root: Path | None = None) -> str:
    """Dispatch the librarian to distill staged sources into memory/curated.md.

    Returns the curator's output text. Costs one metered run."""
    from agentos.core import router

    if not plan.sources:
        return "No sources to curate."
    outcome = router.dispatch(
        "librarian", curate_prompt(plan), project=plan.slug, triggered_by="onboard"
    )
    if not outcome.ok:
        return f"Curation dispatch failed: {outcome.error}"
    root = root or config.AGENTOS_ROOT
    memory_dir = _project_dir(plan, root) / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    out_path = memory_dir / "curated.md"
    out_path.write_text(
        f"---\ntier: project\nproject: {plan.slug}\nsource: librarian-onboarding\n---\n"
        + outcome.text.strip()
        + "\n",
        encoding="utf-8",
    )
    return f"Curated → {out_path}\n\n{outcome.text}"


# ----------------------------------------------------------------------------- #
# Work layer: project record + task migration (part of onboarding)
# ----------------------------------------------------------------------------- #
def ensure_work_project(slug: str) -> dict:
    """Create the work-layer Project for a registry slug if missing; return it.

    The work layer (work.sqlite) is what the dashboard Projects/Tasks/Board read —
    distinct from the memory registry. Onboarding keeps them in sync."""
    from agentos.storage import file_store as local_store
    from agentos.storage.task_store import Project

    for p in local_store.list_projects():
        if p["slug"] == slug:
            return p
    cfg = config.project_config(slug)
    pid = local_store.create_project(Project(
        name=slug, slug=slug, repo_path=cfg.get("repo_path"),
        description=f"workspace: {cfg.get('workspace') or 'platform'}",
    ))
    return local_store.get_project(pid)


def import_tasks(slug: str, *, mc_path: Path | None = None) -> dict:
    """Migrate a project's tasks from MISSION_CONTROL.md into the work layer.

    Imports ALL statuses (including done — history matters). Matches rows to the
    project by alias TOKEN in the title (so 'fac' matches 'FAC' but not 'Facebook').
    Idempotent by title. Returns {imported, skipped, by_status}."""
    import re as _re

    from agentos.storage import file_store as local_store
    from agentos.storage.task_store import Task

    mc = Path(mc_path) if mc_path else MISSION_CONTROL
    if not mc.is_file():
        return {"imported": 0, "skipped": 0, "by_status": {}, "reason": "no MISSION_CONTROL.md"}

    proj = ensure_work_project(slug)
    aliases = set(_aliases(slug, config.project_config(slug)))
    existing = {t["title"] for t in local_store.list_tasks(project_id=proj["id"])}

    imported = skipped = 0
    by_status: dict[str, int] = {}
    for line in mc.read_text(encoding="utf-8").splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) < 3:
            continue
        tid, title, status = cols[0], cols[1], cols[2]
        if not _re.match(r"^T\d+$", tid):
            continue
        if not (_tokens(title) & aliases):
            continue
        full = f"[{tid}] {title}"
        if full in existing:
            skipped += 1
            continue
        raw = _re.sub(r"[^\w\s/-]", "", status).strip().lower()
        wl = _MC_STATUS.get(raw, "backlog")
        prio = cols[4].strip().lower() if len(cols) >= 5 and cols[4].strip().lower() in _MC_PRIORITY else "medium"
        local_store.create_task(Task(
            project_id=proj["id"], title=full, status=wl, priority=prio,
            created_by="onboard:mission-control",
        ))
        imported += 1
        by_status[wl] = by_status.get(wl, 0) + 1
    return {"imported": imported, "skipped": skipped, "by_status": by_status}
