"""SECURITY [CRITICAL] — credential env scrubbing for dispatched `claude` subprocesses.

Batch B fix (docs/reviews/2026-07-01-fable-code-review.md, finding #1, part (b)):
``agentos.core.config._load_env()`` reads config/credentials/.env (API keys, bot
tokens, SMTP password, webhook URLs — see .env.example) into ``os.environ`` with
no scoping. ``ClaudeCodeProvider.dispatch()`` (orchestrator/agentos/providers/
claude_code.py) then calls ``subprocess.run(cmd, ...)`` with NO ``env=`` kwarg,
so the spawned `claude -p` process inherits the *entire* parent environment —
every secret in .env, plus anything else in os.environ — for every dispatched
task, including tasks whose prompts/instructions are attacker- or
task-description-influenced (Task.description / acceptance_criteria are
free-text and, combined with bypassPermissions shell access, can be read back
out by the dispatched agent).

Required fix: pass subprocess.run an explicit ``env=`` allowlist that excludes
credential-shaped vars (ANTHROPIC_API_KEY, OPENAI_API_KEY, TELEGRAM_BOT_TOKEN,
TELEGRAM_CHAT_ID, DISCORD_WEBHOOK_URL, EMAIL_ADDRESS, EMAIL_PASSWORD, etc.) while
still letting the `claude` binary itself run (PATH, HOME, and friends).

These tests are RED (failing) until the fix lands — today no `env=` kwarg is
passed at all, so subprocess.run inherits the real os.environ unfiltered.
"""

from __future__ import annotations

import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from agentos.providers.claude_code import ClaudeCodeProvider

# Names sourced from config/credentials/.env.example — every credential-shaped
# var the loader can put into os.environ.
_CREDENTIAL_VAR_NAMES = [
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "DISCORD_WEBHOOK_URL",
    "EMAIL_ADDRESS",
    "EMAIL_PASSWORD",
]

_SUCCESS_PAYLOAD = {
    "type": "result", "subtype": "success", "result": "ok",
    "total_cost_usd": 0.0, "usage": {}, "is_error": False,
}


def _fake_proc(stdout: str, returncode: int = 0, stderr: str = ""):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


@pytest.fixture
def captured_run(monkeypatch):
    """Stub subprocess.run, capture the kwargs it was called with, and stub the
    CLI as present so dispatch() proceeds past the availability check."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured.update(kwargs)
        return _fake_proc(json.dumps(_SUCCESS_PAYLOAD))

    monkeypatch.setattr("shutil.which", lambda b: "/usr/bin/claude")
    monkeypatch.setattr(subprocess, "run", fake_run)
    return captured


@pytest.fixture
def poisoned_environ(monkeypatch):
    """Simulate _load_env() having populated os.environ with real-shaped secrets,
    plus one arbitrary non-credential secret to prove this is an allowlist (not
    a hardcoded denylist that only catches the names we thought of)."""
    values = {}
    for name in _CREDENTIAL_VAR_NAMES:
        value = f"SECRET-{name}-do-not-leak"
        monkeypatch.setenv(name, value)
        values[name] = value
    monkeypatch.setenv("SOME_UNLISTED_PROJECT_SECRET", "SECRET-unlisted-do-not-leak")
    values["SOME_UNLISTED_PROJECT_SECRET"] = "SECRET-unlisted-do-not-leak"
    return values


def test_dispatch_passes_explicit_env_kwarg(captured_run, poisoned_environ):
    """subprocess.run must receive an explicit env= allowlist — not the implicit
    "no env kwarg = inherit everything" default subprocess.run falls back to."""
    provider = ClaudeCodeProvider()
    provider.dispatch(model="claude-sonnet-4-6", system_prompt="s", user_message="u")

    assert "env" in captured_run, (
        "subprocess.run was called with no env= kwarg — the dispatched `claude` "
        "process inherits the full parent environment, secrets included."
    )
    assert captured_run["env"] is not None


def _child_env(captured_run):
    """Fetch the env= kwarg passed to subprocess.run, failing loudly (rather
    than silently falling back to {}) if none was passed — so tests below
    fail for the right reason instead of passing vacuously against today's
    "no env kwarg at all" behavior."""
    assert "env" in captured_run, (
        "subprocess.run was called with no env= kwarg at all — nothing to scrub."
    )
    return captured_run["env"]


def test_dispatch_subprocess_env_excludes_known_credential_vars(captured_run, poisoned_environ):
    """None of the known credential var names/values may reach the child env."""
    provider = ClaudeCodeProvider()
    provider.dispatch(model="claude-sonnet-4-6", system_prompt="s", user_message="u")

    child_env = _child_env(captured_run)
    for name in _CREDENTIAL_VAR_NAMES:
        assert name not in child_env, f"{name} leaked into the dispatched subprocess env"
    # Belt-and-suspenders: the secret *value* must not appear anywhere in the child env.
    leaked_values = [v for v in child_env.values() if "do-not-leak" in v]
    assert not leaked_values, f"credential values leaked into child env: {leaked_values}"


def test_dispatch_subprocess_env_is_a_true_allowlist_not_a_denylist(captured_run, poisoned_environ):
    """A project-specific secret we never hardcoded a name for must ALSO be
    excluded — proves the fix allowlists safe vars rather than denylisting only
    the credential names this test file happens to know about."""
    provider = ClaudeCodeProvider()
    provider.dispatch(model="claude-sonnet-4-6", system_prompt="s", user_message="u")

    child_env = _child_env(captured_run)
    assert "SOME_UNLISTED_PROJECT_SECRET" not in child_env


def test_dispatch_subprocess_env_still_lets_the_cli_run(captured_run, poisoned_environ):
    """The allowlist must not be so aggressive it breaks the `claude` binary
    itself — PATH (binary/tool resolution) and HOME (config/keychain access)
    must still reach the child."""
    provider = ClaudeCodeProvider()
    provider.dispatch(model="claude-sonnet-4-6", system_prompt="s", user_message="u")

    child_env = _child_env(captured_run)
    assert child_env.get("PATH"), "PATH missing from scrubbed env — the claude binary can't resolve tools"
    assert child_env.get("HOME"), "HOME missing from scrubbed env — the claude CLI can't read its config/keychain"


def test_scrubbing_does_not_mutate_the_orchestrator_process_environment(captured_run, poisoned_environ):
    """The fix must scrub the CHILD's env (via env=), not delete keys from the
    live os.environ — other in-process code (e.g. the notifier, which reads
    TELEGRAM_BOT_TOKEN directly from os.environ) still needs these vars after a
    dispatch call returns."""
    provider = ClaudeCodeProvider()
    provider.dispatch(model="claude-sonnet-4-6", system_prompt="s", user_message="u")

    _child_env(captured_run)  # precondition: a scrub actually happened
    for name, value in poisoned_environ.items():
        assert os.environ.get(name) == value, (
            f"{name} was removed from the orchestrator's own os.environ — "
            "scrub the subprocess env, don't mutate the parent process's."
        )
