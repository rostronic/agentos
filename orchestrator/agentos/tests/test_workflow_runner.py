"""Phase 2 — workflow runner with stubbed dispatch (no network)."""

from __future__ import annotations

import pytest

from agentos.core import workflow_runner
from agentos.core.router import DispatchOutcome
from agentos.core.workflow_loader import Workflow, WorkflowStep


@pytest.fixture
def two_step_wf(monkeypatch):
    """A simple workflow that passes step1 output into step2."""
    wf = Workflow(
        name="test-wf",
        description="test",
        inputs={"topic": {"type": "string", "required": True}},
        steps=[
            WorkflowStep(id="first", agent="researcher", prompt="Research {{inputs.topic}}"),
            WorkflowStep(id="second", agent="scribe", prompt="Summarize: {{steps.first.output}}"),
        ],
    )
    monkeypatch.setattr(workflow_runner, "load_workflow", lambda name: wf)
    return wf


def test_interpolate_inputs():
    out = workflow_runner._interpolate("Hello {{inputs.name}}", {"inputs": {"name": "world"}})
    assert out == "Hello world"


def test_interpolate_step_output():
    ctx = {"inputs": {}, "steps": {"a": {"output": "RESULT"}}}
    out = workflow_runner._interpolate("Prev: {{steps.a.output}}", ctx)
    assert out == "Prev: RESULT"


def test_interpolate_leaves_unresolved_literal():
    out = workflow_runner._interpolate("{{steps.missing.output}}", {"inputs": {}, "steps": {}})
    assert out == "{{steps.missing.output}}"


def test_runs_all_steps_in_order(two_step_wf, monkeypatch):
    calls = []

    def fake_dispatch(agent, prompt, **kwargs):
        calls.append((agent, prompt))
        return DispatchOutcome(ok=True, run_id="r", text=f"{agent}-output", cost_usd=0.01)

    monkeypatch.setattr(workflow_runner.router, "dispatch", fake_dispatch)

    result = workflow_runner.run_workflow("test-wf", {"topic": "AI"})
    assert result.ok
    assert len(result.steps) == 2
    assert calls[0][0] == "researcher"
    assert "Research AI" in calls[0][1]  # input interpolated
    assert "researcher-output" in calls[1][1]  # step1 output piped to step2


def test_final_output_is_last_step(two_step_wf, monkeypatch):
    def fake_dispatch(agent, prompt, **kwargs):
        return DispatchOutcome(ok=True, run_id="r", text=f"{agent}-says-hi", cost_usd=0.0)

    monkeypatch.setattr(workflow_runner.router, "dispatch", fake_dispatch)
    result = workflow_runner.run_workflow("test-wf", {"topic": "x"})
    assert result.final_output == "scribe-says-hi"


def test_total_cost_accumulates(two_step_wf, monkeypatch):
    def fake_dispatch(agent, prompt, **kwargs):
        return DispatchOutcome(ok=True, run_id="r", text="x", cost_usd=0.05)

    monkeypatch.setattr(workflow_runner.router, "dispatch", fake_dispatch)
    result = workflow_runner.run_workflow("test-wf", {"topic": "x"})
    assert result.total_cost_usd == pytest.approx(0.10)


def test_failure_halts_workflow(two_step_wf, monkeypatch):
    """If step 1 fails, step 2 never runs."""
    calls = []

    def fake_dispatch(agent, prompt, **kwargs):
        calls.append(agent)
        return DispatchOutcome(ok=False, run_id="r", error="boom")

    monkeypatch.setattr(workflow_runner.router, "dispatch", fake_dispatch)
    result = workflow_runner.run_workflow("test-wf", {"topic": "x"})
    assert not result.ok
    assert "boom" in result.error
    assert calls == ["researcher"]  # second step never dispatched


def test_missing_required_input_rejected(two_step_wf):
    result = workflow_runner.run_workflow("test-wf", {})  # no topic
    assert not result.ok
    assert "Missing required inputs" in result.error
    assert "topic" in result.error
