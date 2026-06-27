"""Workflow runner — executes a workflow step by step.

Each step dispatches an agent via the router. Step outputs are captured and
made available to later steps through {{steps.<id>.output}} interpolation.
Inputs are available as {{inputs.<name>}}.

The whole workflow is one parent run; each step is a child dispatch. A JSONL
event log is written under ~/agentos/logs/runs/<run-id>.jsonl for replay/debug.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from agentos.core import router, run_store
from agentos.core.config import AGENTOS_ROOT
from agentos.core.workflow_loader import Workflow, load_workflow

LOGS_DIR = AGENTOS_ROOT / "logs" / "runs"

_INTERP_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


@dataclass
class StepResult:
    step_id: str
    agent: str
    ok: bool
    output: str = ""
    error: str = ""
    run_id: str = ""
    cost_usd: float = 0.0


@dataclass
class WorkflowResult:
    ok: bool
    run_id: str
    workflow_name: str
    steps: list[StepResult] = field(default_factory=list)
    final_output: str = ""
    error: str = ""
    total_cost_usd: float = 0.0


def _interpolate(template: str, context: dict[str, Any]) -> str:
    """Replace {{inputs.x}} and {{steps.y.output}} with values from context."""
    def repl(match: re.Match) -> str:
        path = match.group(1).split(".")
        node: Any = context
        for part in path:
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return match.group(0)  # leave unresolved refs literal
        return str(node)

    return _INTERP_RE.sub(repl, template)


class JsonlLogger:
    """Append-only JSONL event log for a single workflow run."""

    def __init__(self, run_id: str):
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self.path = LOGS_DIR / f"{run_id}.jsonl"

    def log(self, event_type: str, **data: Any) -> None:
        from datetime import datetime, timezone
        record = {"ts": datetime.now(timezone.utc).isoformat(), "type": event_type, **data}
        with self.path.open("a") as f:
            f.write(json.dumps(record) + "\n")


def run_workflow(
    name: str,
    inputs: dict[str, Any] | None = None,
    *,
    project: str | None = None,
    triggered_by: str = "cli",
    on_step: Callable[[StepResult], None] | None = None,
) -> WorkflowResult:
    """Run a named workflow to completion (or until a step fails)."""
    inputs = inputs or {}
    try:
        wf: Workflow = load_workflow(name)
    except Exception as e:  # noqa: BLE001
        return WorkflowResult(ok=False, run_id="", workflow_name=name, error=str(e))

    # Validate required inputs are present
    missing = [
        key for key, spec in wf.inputs.items()
        if isinstance(spec, dict) and spec.get("required") and key not in inputs
    ]
    if missing:
        return WorkflowResult(
            ok=False, run_id="", workflow_name=name,
            error=f"Missing required inputs: {', '.join(missing)}",
        )

    # Parent run record
    parent = run_store.Run(
        workflow_name=name, status="running", inputs=inputs,
        triggered_by=triggered_by, project=project,
    )
    run_store.create_run(parent)
    logger = JsonlLogger(parent.id)
    logger.log("workflow_start", workflow=name, inputs=inputs)

    context: dict[str, Any] = {"inputs": inputs, "steps": {}}
    results: list[StepResult] = []
    total_cost = 0.0

    for step in wf.steps:
        prompt = _interpolate(step.prompt, context)
        logger.log("step_start", step_id=step.id, agent=step.agent)
        run_store.append_event(parent.id, "step_start", {"step": step.id, "agent": step.agent}, step_id=step.id)

        outcome = router.dispatch(
            step.agent, prompt, project=project,
            triggered_by=f"workflow:{name}", task_id=parent.id,
        )
        total_cost += outcome.cost_usd
        sr = StepResult(
            step_id=step.id, agent=step.agent, ok=outcome.ok,
            output=outcome.text, error=outcome.error,
            run_id=outcome.run_id, cost_usd=outcome.cost_usd,
        )
        results.append(sr)
        if on_step:
            on_step(sr)

        if not outcome.ok:
            logger.log("step_failed", step_id=step.id, error=outcome.error)
            run_store.append_event(parent.id, "step_failed", {"step": step.id, "error": outcome.error}, step_id=step.id)
            run_store.update_run(
                parent.id, status="failed", ended_at=run_store._now(),
                error=f"Step '{step.id}' failed: {outcome.error}",
                cost_usd=total_cost,
            )
            return WorkflowResult(
                ok=False, run_id=parent.id, workflow_name=name,
                steps=results, error=outcome.error, total_cost_usd=total_cost,
            )

        context["steps"][step.id] = {"output": outcome.text}
        logger.log("step_done", step_id=step.id, cost_usd=outcome.cost_usd)
        run_store.append_event(parent.id, "step_done", {"step": step.id, "cost_usd": outcome.cost_usd}, step_id=step.id)

    final = results[-1].output if results else ""
    run_store.update_run(
        parent.id, status="done", ended_at=run_store._now(),
        output=final, cost_usd=total_cost,
    )
    logger.log("workflow_done", final_cost_usd=total_cost)
    return WorkflowResult(
        ok=True, run_id=parent.id, workflow_name=name,
        steps=results, final_output=final, total_cost_usd=total_cost,
    )
