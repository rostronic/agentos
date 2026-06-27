"""Phase 1 — budget enforcement."""

from __future__ import annotations

import pytest

from agentos.core import budget


@pytest.fixture
def fixed_budget(monkeypatch):
    """Pin a known budget so tests don't depend on budgets.yaml."""
    monkeypatch.setattr(
        budget, "budget_for_project",
        lambda project=None: {"daily_usd": 10.0, "per_run_usd": 2.0},
    )


def test_allows_dispatch_under_budget(fixed_budget):
    block = budget.check_dispatch(project="test")
    assert not block


def test_records_and_accumulates_spend(fixed_budget):
    budget.record_spend(1.50, project="test")
    budget.record_spend(2.25, project="test")
    assert abs(budget.today_spend() - 3.75) < 0.001


def test_blocks_when_daily_cap_reached(fixed_budget):
    budget.record_spend(10.0, project="test")
    block = budget.check_dispatch(project="test")
    assert block
    assert block.reason == "budget_exceeded"


def test_blocks_per_run_estimate_over_cap(fixed_budget):
    block = budget.check_dispatch(project="test", estimated_usd=5.0)
    assert block
    assert block.reason == "per_run_exceeded"


def test_threshold_crossed_at_80(fixed_budget):
    budget.record_spend(8.5, project="test")
    assert budget.threshold_crossed(project="test") == 80


def test_threshold_crossed_at_100(fixed_budget):
    budget.record_spend(10.0, project="test")
    assert budget.threshold_crossed(project="test") == 100


def test_reset_clears_spend(fixed_budget):
    budget.record_spend(5.0, project="test")
    budget.reset_today()
    assert budget.today_spend() == 0.0
