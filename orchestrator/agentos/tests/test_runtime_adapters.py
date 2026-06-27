"""Runtime axis — selecting an external agent runtime (agentcli / hermes).

Covers:
  - router runtime selection (native default → LLM provider; agentcli/hermes →
    the runtime adapter), with the subprocess fully mocked.
  - each adapter's happy path (mocked subprocess returns canned stdout →
    DispatchResult) and unreachable/error path.

No agentcli/hermes process is ever launched: subprocess.run is monkeypatched.
"""

from __future__ import annotations

import subprocess
import types

import pytest

from agentos.core import budget, router
from agentos.providers.agentcli_runtime import AgentCliRuntimeProvider
from agentos.providers.base import DispatchResult, ProviderError
from agentos.providers.hermes_runtime import HermesRuntimeProvider


@pytest.fixture
def unlimited_budget(monkeypatch):
    monkeypatch.setattr(budget, "budget_for_project", lambda project=None: {})


def _fake_completed(stdout="", stderr="", returncode=0):
    return types.SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


# --- router runtime selection ---
def test_router_native_uses_llm_provider(monkeypatch, unlimited_budget):
    """No runtime → native path uses the LLM provider (current behavior)."""
    calls = {"llm": 0, "runtime": 0}

    class _Fake:
        name = "fake"

        def dispatch(self, **_):
            return DispatchResult(text="native!", model="m", provider="fake", cost_usd=0.0)

    def _get(model, provider=None):
        calls["llm"] += 1
        return _Fake()

    def _rt(name):
        calls["runtime"] += 1
        return _Fake()

    monkeypatch.setattr(router, "_get_provider", _get)
    monkeypatch.setattr(router, "_runtime_provider", _rt)

    outcome = router.dispatch("researcher", "task")
    assert outcome.ok
    assert outcome.text == "native!"
    assert calls["llm"] == 1
    assert calls["runtime"] == 0  # runtime adapter never consulted


def test_router_runtime_override_uses_adapter(monkeypatch, unlimited_budget):
    """runtime_override='agentcli' → the runtime adapter, not _get_provider."""
    calls = {"llm": 0, "runtime_name": None}

    class _Fake:
        name = "agentcli"

        def dispatch(self, **_):
            return DispatchResult(
                text="from agentcli", model="qwen2.5", provider="agentcli",
                cost_usd=0.0, billed_to="external",
            )

    def _get(model, provider=None):
        calls["llm"] += 1
        raise AssertionError("native LLM provider must not be used for a runtime")

    def _rt(name):
        calls["runtime_name"] = name
        return _Fake()

    monkeypatch.setattr(router, "_get_provider", _get)
    monkeypatch.setattr(router, "_runtime_provider", _rt)

    outcome = router.dispatch("researcher", "task", runtime_override="agentcli")
    assert outcome.ok
    assert outcome.text == "from agentcli"
    assert outcome.billed_to == "external"
    assert calls["llm"] == 0
    assert calls["runtime_name"] == "agentcli"


def test_router_runtime_from_frontmatter(monkeypatch, unlimited_budget):
    """An agent dict with runtime='hermes' routes through the adapter."""
    seen = {}

    class _Fake:
        name = "hermes"

        def dispatch(self, **_):
            return DispatchResult(text="hi", model="m", provider="hermes", cost_usd=0.0)

    monkeypatch.setattr(
        router, "get_agent",
        lambda name: {"name": name, "runtime": "hermes", "model": "claude-sonnet-4-6"},
    )
    def _rt(name):
        seen["n"] = name
        return _Fake()

    monkeypatch.setattr(router, "_runtime_provider", _rt)
    monkeypatch.setattr(router, "_get_provider", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))

    outcome = router.dispatch("anyagent", "task")
    assert outcome.ok
    assert seen["n"] == "hermes"


def test_runtime_provider_unknown_raises():
    with pytest.raises(ProviderError, match="Unknown runtime"):
        router._runtime_provider("nonsense")


def test_runtime_provider_resolves_adapters():
    assert router._runtime_provider("agentcli").name == "agentcli"
    assert router._runtime_provider("hermes").name == "hermes"


# --- agentcli adapter ---
def test_agentcli_happy_path(monkeypatch):
    captured = {}

    def _run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed(stdout='{"reply": "agentcli says hi"}')

    monkeypatch.setattr(subprocess, "run", _run)
    p = AgentCliRuntimeProvider(binary="agentcli")
    r = p.dispatch(model="qwen2.5", system_prompt="be terse", user_message="hello")
    assert r.text == "agentcli says hi"
    assert r.provider == "agentcli"
    assert r.billed_to == "external"
    assert r.cost_usd == 0.0
    # one-shot, JSON, never delivers to a channel
    assert "agent" in captured["cmd"]
    assert "--json" in captured["cmd"]
    assert "--deliver" not in captured["cmd"]
    assert "--model" in captured["cmd"]


def test_agentcli_plain_stdout_fallback(monkeypatch):
    """Non-JSON stdout degrades to the raw text instead of crashing."""
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: _fake_completed(stdout="plain text"))
    p = AgentCliRuntimeProvider()
    r = p.dispatch(model="", system_prompt="", user_message="x")
    assert r.text == "plain text"


def test_agentcli_nonzero_exit_raises(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **k: _fake_completed(stderr="gateway down", returncode=1),
    )
    p = AgentCliRuntimeProvider()
    with pytest.raises(ProviderError) as exc:
        p.dispatch(model="m", system_prompt="", user_message="x")
    assert "gateway down" in str(exc.value)
    assert exc.value.retryable


def test_agentcli_binary_missing_raises(monkeypatch):
    def _boom(cmd, **k):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(subprocess, "run", _boom)
    p = AgentCliRuntimeProvider(binary="definitely-not-installed")
    with pytest.raises(ProviderError, match="not found"):
        p.dispatch(model="m", system_prompt="", user_message="x")


def test_agentcli_timeout_is_retryable(monkeypatch):
    def _timeout(cmd, **k):
        raise subprocess.TimeoutExpired(cmd, 600)

    monkeypatch.setattr(subprocess, "run", _timeout)
    p = AgentCliRuntimeProvider()
    with pytest.raises(ProviderError) as exc:
        p.dispatch(model="m", system_prompt="", user_message="x")
    assert exc.value.retryable


# --- Hermes adapter ---
def test_hermes_happy_path(monkeypatch):
    captured = {}

    def _run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed(stdout="hermes final answer\n")

    monkeypatch.setattr(subprocess, "run", _run)
    p = HermesRuntimeProvider(binary="hermes")
    r = p.dispatch(model="claude-opus", system_prompt="sys", user_message="hello")
    assert r.text == "hermes final answer"  # stripped
    assert r.provider == "hermes"
    assert r.billed_to == "external"
    assert r.cost_usd == 0.0
    # one-shot via -z; model forwarded; system prompt folded into the prompt
    assert "-z" in captured["cmd"]
    assert "-m" in captured["cmd"]
    prompt = captured["cmd"][captured["cmd"].index("-z") + 1]
    assert "sys" in prompt
    assert "hello" in prompt


def test_hermes_nonzero_exit_raises(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **k: _fake_completed(stderr="auth required", returncode=2),
    )
    p = HermesRuntimeProvider()
    with pytest.raises(ProviderError) as exc:
        p.dispatch(model="m", system_prompt="", user_message="x")
    assert "auth required" in str(exc.value)


def test_hermes_binary_missing_raises(monkeypatch):
    def _boom(cmd, **k):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(subprocess, "run", _boom)
    p = HermesRuntimeProvider(binary="definitely-not-installed")
    with pytest.raises(ProviderError, match="not found"):
        p.dispatch(model="m", system_prompt="", user_message="x")
