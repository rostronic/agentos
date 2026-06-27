"""Phase 1 — agent loader validates the 8 shipped agent specs."""

from __future__ import annotations

from agentos.core.agent_loader import get_agent, load_all_agents

EXPECTED_AGENTS = {
    "researcher", "developer", "qa", "planner",
    "critic", "librarian", "scribe", "analyst",
}


def test_loads_all_eight_agents():
    agents = load_all_agents()
    names = {a["name"] for a in agents}
    assert EXPECTED_AGENTS.issubset(names), f"Missing: {EXPECTED_AGENTS - names}"


def test_every_agent_has_required_fields():
    for agent in load_all_agents():
        assert agent.get("name"), "agent missing name"
        assert agent.get("system_prompt"), f"{agent['name']} has empty system prompt"
        assert agent.get("model"), f"{agent['name']} has no model"


def test_model_spec_has_preferred():
    for agent in load_all_agents():
        model = agent["model"]
        if isinstance(model, dict):
            assert model.get("preferred"), f"{agent['name']} model dict missing 'preferred'"


def test_get_specific_agent():
    researcher = get_agent("researcher")
    assert researcher is not None
    assert researcher["name"] == "researcher"
    assert "research" in researcher["system_prompt"].lower()


def test_get_unknown_agent_returns_none():
    assert get_agent("nonexistent-agent-xyz") is None
