"""Phase 1 — router dispatch with stubbed providers (no network)."""

from __future__ import annotations

import pytest

from agentos.core import budget, router
from agentos.providers.base import DispatchResult, ProviderError


class FakeProvider:
    """Deterministic provider for tests."""

    name = "fake"

    def __init__(self, *, fail=False, retryable=False, text="fake response"):
        self.fail = fail
        self.retryable = retryable
        self.text = text
        self.calls = []

    def supports_model(self, model):
        return True

    def dispatch(self, *, model, system_prompt, user_message, temperature, max_tokens, workdir=None):
        self.calls.append(model)
        if self.fail:
            raise ProviderError("simulated failure", retryable=self.retryable)
        return DispatchResult(
            text=self.text, model=model, provider=self.name,
            input_tokens=100, output_tokens=50, cost_usd=0.01,
        )


@pytest.fixture
def unlimited_budget(monkeypatch):
    monkeypatch.setattr(budget, "budget_for_project", lambda project=None: {})


def test_successful_dispatch(monkeypatch, unlimited_budget):
    fake = FakeProvider(text="hello from researcher")
    monkeypatch.setattr(router, "_get_provider", lambda model, provider=None: fake)

    outcome = router.dispatch("researcher", "do research")
    assert outcome.ok
    assert outcome.text == "hello from researcher"
    assert outcome.cost_usd == 0.01
    assert outcome.run_id


def test_unknown_agent_fails_gracefully(unlimited_budget):
    outcome = router.dispatch("nonexistent", "task")
    assert not outcome.ok
    assert "Unknown agent" in outcome.error


def test_budget_block_prevents_dispatch(monkeypatch):
    monkeypatch.setattr(
        budget, "budget_for_project",
        lambda project=None: {"daily_usd": 1.0},
    )
    budget.record_spend(1.0, project=None)

    fake = FakeProvider()
    monkeypatch.setattr(router, "_get_provider", lambda model, provider=None: fake)

    outcome = router.dispatch("researcher", "task")
    assert not outcome.ok
    assert outcome.blocked_reason == "budget_exceeded"
    assert fake.calls == []  # provider never called


def test_fallback_chain_tries_next_model(monkeypatch, unlimited_budget):
    """First model fails non-retryably → router tries the fallback."""
    failing = FakeProvider(fail=True)
    working = FakeProvider(text="from fallback")
    providers = iter([failing, working])
    monkeypatch.setattr(router, "_get_provider", lambda model, provider=None: next(providers))

    # researcher has preferred=claude-sonnet-4-6, fallback=[gpt-4o, llama3.1:70b]
    outcome = router.dispatch("researcher", "task")
    assert outcome.ok
    assert outcome.text == "from fallback"


def test_all_models_fail_returns_error(monkeypatch, unlimited_budget):
    fake = FakeProvider(fail=True)
    monkeypatch.setattr(router, "_get_provider", lambda model, provider=None: fake)

    outcome = router.dispatch("researcher", "task")
    assert not outcome.ok
    assert outcome.error


def test_spend_recorded_after_success(monkeypatch, unlimited_budget):
    fake = FakeProvider()
    monkeypatch.setattr(router, "_get_provider", lambda model, provider=None: fake)

    router.dispatch("researcher", "task")
    assert budget.today_spend() == pytest.approx(0.01)


# --- Layered memory injection (Phase 2 wiring) -------------------------------- #
class CapturingProvider:
    """Records the exact system_prompt / user_message it was handed."""

    name = "cap"

    def __init__(self):
        self.system_prompt = None
        self.user_message = None
        self.calls = []

    def supports_model(self, model):
        return True

    def dispatch(self, *, model, system_prompt, user_message, temperature, max_tokens, workdir=None):
        self.system_prompt = system_prompt
        self.user_message = user_message
        self.calls.append(model)
        return DispatchResult(
            text="ok", model=model, provider=self.name,
            input_tokens=1, output_tokens=1, cost_usd=0.0,
        )


def test_memory_prepended_to_system_prompt_on_native(monkeypatch, unlimited_budget):
    from agentos.core import memory_context
    monkeypatch.setattr(memory_context, "build_context", lambda *a, **k: "MEMBLOCK")
    cap = CapturingProvider()
    monkeypatch.setattr(router, "_get_provider", lambda model, provider=None: cap)

    outcome = router.dispatch("researcher", "do research", project="example-shop")
    assert outcome.ok
    assert "MEMBLOCK" in cap.system_prompt          # injected into system prompt
    assert cap.user_message == "do research"        # message left untouched


def test_memory_folded_into_message_for_external_runtime(monkeypatch, unlimited_budget):
    from agentos.core import memory_context
    monkeypatch.setattr(memory_context, "build_context", lambda *a, **k: "MEMBLOCK")
    cap = CapturingProvider()
    monkeypatch.setattr(router, "_runtime_provider", lambda name: cap)

    outcome = router.dispatch("researcher", "do research", runtime_override="agentcli")
    assert outcome.ok
    assert "MEMBLOCK" in cap.user_message and "do research" in cap.user_message
    assert "MEMBLOCK" not in (cap.system_prompt or "")  # runtime owns its prompt


def test_no_memory_is_a_noop(monkeypatch, unlimited_budget):
    from agentos.core import memory_context
    monkeypatch.setattr(memory_context, "build_context", lambda *a, **k: "")
    cap = CapturingProvider()
    monkeypatch.setattr(router, "_get_provider", lambda model, provider=None: cap)

    outcome = router.dispatch("researcher", "do research")
    assert outcome.ok
    assert "MEMBLOCK" not in (cap.system_prompt or "")
    assert cap.user_message == "do research"


def test_memory_failure_never_breaks_dispatch(monkeypatch, unlimited_budget):
    from agentos.core import memory_context

    def boom(*a, **k):
        raise RuntimeError("memory exploded")

    monkeypatch.setattr(memory_context, "build_context", boom)
    cap = CapturingProvider()
    monkeypatch.setattr(router, "_get_provider", lambda model, provider=None: cap)

    outcome = router.dispatch("researcher", "do research")
    assert outcome.ok                               # dispatch still succeeds
    assert cap.user_message == "do research"
