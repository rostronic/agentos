"""Load and validate agent specs from ~/agentos/agents/*/agent.md."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import frontmatter  # python-frontmatter

from agentos.core import config
from agentos.core.config import AGENTOS_ROOT

AGENTS_DIR = AGENTOS_ROOT / "agents"


def _render_template(text: str) -> str:
    """Substitute `{{user_name}}` mustache tokens in agent-spec strings.

    Agent specs (agent.md frontmatter + body) may reference the configured
    user by name; leaving the raw token in a description leaks it straight
    onto the dashboard's Agents cards. Falls back to "the user" when no name
    is configured so the literal `{{...}}` never survives.
    """
    return text.replace("{{user_name}}", config.user_name() or "the user")


def load_agent(agent_dir: Path) -> dict[str, Any] | None:
    """Parse a single agent spec. Returns None if invalid."""
    spec_file = agent_dir / "agent.md"
    if not spec_file.exists():
        return None

    try:
        post = frontmatter.load(spec_file)
        meta = {k: _render_template(v) if isinstance(v, str) else v
                for k, v in post.metadata.items()}
        meta["system_prompt"] = _render_template(post.content.strip())
        meta.setdefault("name", agent_dir.name)
        return meta
    except Exception as e:  # noqa: BLE001
        print(f"[warn] Could not parse {spec_file}: {e}")
        return None


def load_all_agents() -> list[dict[str, Any]]:
    """Load all valid agents from the agents directory."""
    if not AGENTS_DIR.exists():
        return []

    agents = []
    for agent_dir in sorted(AGENTS_DIR.iterdir()):
        if agent_dir.is_dir() and not agent_dir.name.startswith("."):
            agent = load_agent(agent_dir)
            if agent:
                agents.append(agent)

    return agents


def get_agent(name: str) -> dict[str, Any] | None:
    """Load a specific agent by name."""
    agent_dir = AGENTS_DIR / name
    return load_agent(agent_dir)
