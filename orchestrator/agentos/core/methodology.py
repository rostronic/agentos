"""SDLC methodology strategies — pluggable, per-project discipline for the team.

A *methodology* is the engineering discipline applied to each task: which agents
touch it, in what order, and which gates it must pass. It is independent of
*cadence* (sprint vs continuous backlog — see ``config.cadence_for`` and
``sprint_executor``).

The executor consumes a Methodology through a few small hooks:
  - ``order_ready_tasks(ready)``        → which ready task to pick next
  - ``pre_implementation_stages(task)`` → stages BEFORE the implementer (XP: write tests first)
  - ``post_qa_stages(task)``            → stages AFTER QA passes (XP: critic review; DevOps: build-verify)
plus the ``needs_qa`` flag. ``agile`` reproduces today's developer→qa flow exactly, so it is
the safe default and existing behavior is unchanged.

Public catalog (see docs/methodologies.md): **waterfall, agile, xp, devops**.
Honest mappings: Scrum = agile + sprint cadence; Kanban = agile + backlog cadence;
TDD is folded into XP (XP is test-first plus review).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stage:
    """One agent step in a task's pipeline."""

    role: str           # agent name: developer / qa / critic / planner / ...
    kind: str           # implement | test | review | deploy | gate
    instruction: str = ""  # extra prompt text appended for this stage


def _priority_order(ready: list[dict]) -> list[dict]:
    """Default ordering — high > medium > low, then oldest first (today's behavior)."""
    rank = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        ready,
        key=lambda t: (rank.get(t.get("priority"), 1), t.get("created_at") or ""),
    )


@dataclass(frozen=True)
class Methodology:
    """A pluggable engineering discipline. Defaults reproduce ``agile``."""

    name: str
    description: str = ""
    test_first: bool = False      # write failing tests before implementing (XP)
    needs_qa: bool = True         # run the QA gate after implementation
    needs_critic: bool = False    # run a critic code-review gate after QA (XP)
    deploy_verify: bool = False   # run a CI/smoke build-verify stage after QA (DevOps)
    phase_gate: bool = False      # sequential phases w/ a human gate between (Waterfall; P3)

    def order_ready_tasks(self, ready: list[dict]) -> list[dict]:
        return _priority_order(ready)

    def pre_implementation_stages(self, task: dict) -> list[Stage]:
        """Stages run before the implementer. XP/TDD write tests first."""
        if self.test_first:
            return [
                Stage(
                    "qa",
                    "test",
                    "Write the failing acceptance/unit tests for this task FIRST, derived from "
                    "the acceptance criteria. Do NOT implement the feature — only the tests. "
                    "Report the test names/paths and what each asserts.",
                )
            ]
        return []

    def post_qa_stages(self, task: dict) -> list[Stage]:
        """Gate stages run after QA passes. Each must reply PASS/FAIL; a FAIL blocks the task."""
        stages: list[Stage] = []
        if self.needs_critic:
            stages.append(
                Stage(
                    "critic",
                    "gate",
                    "Adversarially review the implementation against the acceptance criteria for "
                    "correctness, simplicity, and regressions. Begin your reply with PASS or FAIL.",
                )
            )
        if self.deploy_verify:
            stages.append(
                Stage(
                    "developer",
                    "gate",
                    "Run the CI checks / a smoke build (NOT a production deploy) and verify the "
                    "change is healthy. Begin your reply with PASS or FAIL.",
                )
            )
        return stages


REGISTRY: dict[str, Methodology] = {
    "agile": Methodology(
        "agile",
        description="Lightweight iterative: developer → qa. The baseline (today's flow).",
    ),
    "xp": Methodology(
        "xp",
        description="Extreme Programming: test-first, implement, then a critic review gate.",
        test_first=True,
        needs_critic=True,
    ),
    "devops": Methodology(
        "devops",
        description="CI/CD discipline: developer → qa → CI/smoke build-verify (preview, not prod).",
        deploy_verify=True,
    ),
    "waterfall": Methodology(
        "waterfall",
        description="Sequential phases in dependency order, with a human phase-gate between "
        "phases (gate enforcement is P3; runs as agile + ordering until then).",
        phase_gate=True,
    ),
}

DEFAULT = "agile"

# Cadence is a separate dial from methodology.
CADENCES = ("sprint", "backlog")
DEFAULT_CADENCE = "backlog"


def get(name: str | None) -> Methodology:
    """Resolve a methodology by name, falling back to the default (agile)."""
    return REGISTRY.get((name or "").strip().lower(), REGISTRY[DEFAULT])


def names() -> list[str]:
    return list(REGISTRY)
