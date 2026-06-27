"""Phase 2 — workflow loader and reference validation."""

from __future__ import annotations

import pytest

from agentos.core.workflow_loader import (
    WorkflowError,
    load_all_workflows,
    load_workflow,
    parse_workflow,
)


def test_loads_shipped_workflows():
    wfs = load_all_workflows()
    names = {w.name for w in wfs}
    assert "deep-research" in names
    assert "ship-feature" in names


def test_plan_sprint_loads():
    wf = load_workflow("plan-sprint")
    assert [s.id for s in wf.steps] == ["plan", "critique"]
    assert wf.steps[0].agent == "planner"
    assert wf.steps[1].agent == "critic"
    assert set(wf.inputs) == {"goal", "project"}


def test_deep_research_structure():
    wf = load_workflow("deep-research")
    assert len(wf.steps) == 3
    assert [s.id for s in wf.steps] == ["research", "critique", "synthesize"]
    assert wf.steps[0].agent == "researcher"


def test_every_shipped_workflow_references_valid_agents():
    from agentos.core.agent_loader import load_all_agents
    known = {a["name"] for a in load_all_agents()}
    for wf in load_all_workflows():
        for step in wf.steps:
            assert step.agent in known, f"{wf.name}/{step.id} uses unknown agent {step.agent}"


def test_rejects_missing_name():
    with pytest.raises(WorkflowError, match="name"):
        parse_workflow({"steps": [{"id": "a", "agent": "x", "prompt": "y"}]})


def test_rejects_no_steps():
    with pytest.raises(WorkflowError, match="no steps"):
        parse_workflow({"name": "empty", "steps": []})


def test_rejects_duplicate_step_id():
    with pytest.raises(WorkflowError, match="Duplicate"):
        parse_workflow({
            "name": "dup",
            "steps": [
                {"id": "a", "agent": "researcher", "prompt": "x"},
                {"id": "a", "agent": "scribe", "prompt": "y"},
            ],
        })


def test_rejects_forward_step_reference():
    """A step can't reference a step that hasn't run yet."""
    with pytest.raises(WorkflowError, match="hasn't run yet"):
        parse_workflow({
            "name": "forward",
            "steps": [
                {"id": "a", "agent": "researcher", "prompt": "{{steps.b.output}}"},
                {"id": "b", "agent": "scribe", "prompt": "hi"},
            ],
        })


def test_rejects_unknown_input_reference():
    with pytest.raises(WorkflowError, match="unknown input"):
        parse_workflow({
            "name": "badinput",
            "inputs": {"topic": {"type": "string"}},
            "steps": [{"id": "a", "agent": "researcher", "prompt": "{{inputs.missing}}"}],
        })


def test_accepts_valid_backward_reference():
    wf = parse_workflow({
        "name": "good",
        "inputs": {"topic": {"type": "string"}},
        "steps": [
            {"id": "a", "agent": "researcher", "prompt": "{{inputs.topic}}"},
            {"id": "b", "agent": "scribe", "prompt": "{{steps.a.output}}"},
        ],
    })
    assert len(wf.steps) == 2
