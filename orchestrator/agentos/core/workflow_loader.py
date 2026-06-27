"""Load and validate workflow specs from ~/agentos/workflows/*.yaml."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from agentos.core.config import AGENTOS_ROOT

WORKFLOWS_DIR = AGENTOS_ROOT / "workflows"

# Matches {{inputs.foo}} and {{steps.bar.output}}
_INTERP_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


@dataclass
class WorkflowStep:
    id: str
    agent: str
    prompt: str


@dataclass
class Workflow:
    name: str
    description: str
    inputs: dict[str, Any] = field(default_factory=dict)
    steps: list[WorkflowStep] = field(default_factory=list)
    source_path: str = ""


class WorkflowError(Exception):
    """Raised when a workflow spec is invalid."""


def _referenced_vars(prompt: str) -> list[str]:
    return _INTERP_RE.findall(prompt)


def parse_workflow(data: dict, source_path: str = "") -> Workflow:
    """Parse and validate a workflow dict. Raises WorkflowError on problems."""
    if not data.get("name"):
        raise WorkflowError(f"Workflow missing 'name' ({source_path})")
    if not isinstance(data.get("steps"), list) or not data["steps"]:
        raise WorkflowError(f"Workflow '{data.get('name')}' has no steps")

    steps = []
    seen_ids: set[str] = set()
    for i, raw in enumerate(data["steps"]):
        sid = raw.get("id")
        if not sid:
            raise WorkflowError(f"Step {i} in '{data['name']}' missing 'id'")
        if sid in seen_ids:
            raise WorkflowError(f"Duplicate step id '{sid}' in '{data['name']}'")
        if not raw.get("agent"):
            raise WorkflowError(f"Step '{sid}' missing 'agent'")
        if not raw.get("prompt"):
            raise WorkflowError(f"Step '{sid}' missing 'prompt'")
        seen_ids.add(sid)
        steps.append(WorkflowStep(id=sid, agent=raw["agent"], prompt=raw["prompt"]))

    wf = Workflow(
        name=data["name"],
        description=data.get("description", ""),
        inputs=data.get("inputs", {}),
        steps=steps,
        source_path=source_path,
    )
    _validate_references(wf)
    return wf


def _validate_references(wf: Workflow) -> None:
    """Ensure every {{...}} reference points at a declared input or prior step."""
    valid_inputs = set(wf.inputs.keys())
    available_steps: set[str] = set()
    for step in wf.steps:
        for var in _referenced_vars(step.prompt):
            parts = var.split(".")
            if parts[0] == "inputs":
                if len(parts) < 2 or parts[1] not in valid_inputs:
                    raise WorkflowError(
                        f"Step '{step.id}' references unknown input '{var}'"
                    )
            elif parts[0] == "steps":
                if len(parts) < 3 or parts[1] not in available_steps:
                    raise WorkflowError(
                        f"Step '{step.id}' references step '{var}' that hasn't run yet"
                    )
            else:
                raise WorkflowError(
                    f"Step '{step.id}' has invalid reference '{var}' "
                    f"(must start with 'inputs.' or 'steps.')"
                )
        available_steps.add(step.id)


def load_workflow(name: str) -> Workflow:
    path = WORKFLOWS_DIR / f"{name}.yaml"
    if not path.exists():
        raise WorkflowError(f"Workflow not found: {name} (looked in {path})")
    data = yaml.safe_load(path.read_text())
    return parse_workflow(data, source_path=str(path))


def load_all_workflows() -> list[Workflow]:
    if not WORKFLOWS_DIR.exists():
        return []
    workflows = []
    for path in sorted(WORKFLOWS_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text())
            workflows.append(parse_workflow(data, source_path=str(path)))
        except (WorkflowError, yaml.YAMLError) as e:
            print(f"[warn] Skipping invalid workflow {path.name}: {e}")
    return workflows
