"""Phase 1.5 — Claude Code provider (subscription-billed, mocked subprocess)."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from agentos.providers.base import ProviderError
from agentos.providers.claude_code import ClaudeCodeProvider, _to_cli_model


def test_model_alias_mapping():
    assert _to_cli_model("claude-sonnet-4-6") == "sonnet"
    assert _to_cli_model("claude-opus-4-8") == "opus"
    assert _to_cli_model("claude-haiku-4-5") == "haiku"
    assert _to_cli_model("some-custom-model") == "some-custom-model"


def _fake_proc(stdout: str, returncode: int = 0, stderr: str = ""):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_workdir_and_permission_mode_reach_the_cli(monkeypatch):
    """REGRESSION (bug #4): dispatches must run IN the task worktree and with a
    permission mode — headless -p can't answer prompts, so without one the
    agent can never write a file."""
    payload = {"type": "result", "subtype": "success", "result": "ok",
               "total_cost_usd": 0.0, "usage": {}, "is_error": False}
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured.update(kwargs)
        return _fake_proc(json.dumps(payload))

    monkeypatch.setattr("shutil.which", lambda b: "/usr/bin/claude")
    monkeypatch.setattr(subprocess, "run", fake_run)

    provider = ClaudeCodeProvider(permission_mode="acceptEdits",
                                  allowed_tools=["WebSearch", "WebFetch"])
    provider.dispatch(model="claude-sonnet-4-6", system_prompt="s",
                      user_message="u", workdir="/tmp/some-worktree")
    assert captured["cwd"] == "/tmp/some-worktree"
    i = captured["cmd"].index("--permission-mode")
    assert captured["cmd"][i + 1] == "acceptEdits"
    j = captured["cmd"].index("--allowedTools")
    assert captured["cmd"][j + 1] == "WebSearch,WebFetch"

    # Without a permission mode or workdir, neither flag nor cwd is forced.
    provider = ClaudeCodeProvider()
    provider.dispatch(model="claude-sonnet-4-6", system_prompt="s", user_message="u")
    assert "--permission-mode" not in captured["cmd"]
    assert captured["cwd"] is None


def test_successful_dispatch_is_subscription_billed(monkeypatch):
    payload = {
        "type": "result",
        "subtype": "success",
        "result": "Hello from Claude Code",
        "total_cost_usd": 0.0123,
        "usage": {"input_tokens": 100, "output_tokens": 50},
        "is_error": False,
    }
    monkeypatch.setattr("shutil.which", lambda b: "/usr/bin/claude")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_proc(json.dumps(payload)))

    provider = ClaudeCodeProvider()
    result = provider.dispatch(
        model="claude-sonnet-4-6", system_prompt="You are X", user_message="hi"
    )
    assert result.text == "Hello from Claude Code"
    # cost_usd carries the API-equivalent value (the CLI's own total_cost_usd) so the
    # dashboard Cost tab reflects real usage; billed_to="subscription" marks not charged.
    assert result.cost_usd == 0.0123
    assert result.subscription_equivalent_usd == 0.0123  # API-equivalent recorded
    assert result.billed_to == "subscription"
    assert result.input_tokens == 100


def test_not_logged_in_gives_clear_error(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda b: "/usr/bin/claude")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _fake_proc("", returncode=1, stderr="Not logged in"),
    )
    provider = ClaudeCodeProvider()
    with pytest.raises(ProviderError, match="not logged in"):
        provider.dispatch(model="claude-sonnet-4-6", system_prompt="", user_message="hi")


def test_missing_cli_gives_clear_error(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda b: None)
    provider = ClaudeCodeProvider()
    with pytest.raises(ProviderError, match="not found on PATH"):
        provider.dispatch(model="claude-sonnet-4-6", system_prompt="", user_message="hi")


def test_rate_limit_is_retryable(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda b: "/usr/bin/claude")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _fake_proc("", returncode=1, stderr="rate limit exceeded"),
    )
    provider = ClaudeCodeProvider()
    with pytest.raises(ProviderError) as exc:
        provider.dispatch(model="claude-sonnet-4-6", system_prompt="", user_message="hi")
    assert exc.value.retryable


def test_router_defaults_to_claude_code(monkeypatch):
    """With default settings, the router should pick the claude_code backend."""
    from agentos.core import router
    from agentos.core import config

    monkeypatch.setattr(
        config, "settings",
        lambda: {"orchestrator": {"default_provider": "claude_code"}},
    )
    provider = router._get_provider("claude-sonnet-4-6")
    assert provider.name == "claude_code"


def test_router_respects_claude_api_setting(monkeypatch):
    from agentos.core import router
    from agentos.core import config

    monkeypatch.setattr(
        config, "settings",
        lambda: {"orchestrator": {"default_provider": "claude_api"}},
    )
    provider = router._get_provider("claude-sonnet-4-6")
    assert provider.name == "claude"
