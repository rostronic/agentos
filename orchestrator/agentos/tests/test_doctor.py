"""Doctor health-check: a bare AGENTOS_ROOT reports FAILs (missing config), and a
minimally-populated root reports PASS with exit 0.

Follows the codebase pattern of pointing a temp AGENTOS_ROOT by monkeypatching the
``config`` module attributes (config.AGENTOS_ROOT / CONFIG_DIR / ENV_FILE) — doctor
reads those live, so no module re-import is needed.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from agentos.core import config, doctor
from agentos.entrypoints.cli import app

runner = CliRunner()


@pytest.fixture
def temp_root(tmp_path, monkeypatch):
    """Point config at an empty temp AGENTOS_ROOT and clear the inference provider,
    so checks are deterministic regardless of the host machine."""
    root = tmp_path / "agentos"
    cdir = root / "config"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "AGENTOS_ROOT", root)
    monkeypatch.setattr(config, "CONFIG_DIR", cdir)
    monkeypatch.setattr(config, "ENV_FILE", cdir / "credentials" / ".env")
    # No provider reachable by default → make credential behavior explicit per test.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(doctor.shutil, "which", lambda _: None)
    return root


def _status(report, name_contains):
    for c in report.checks:
        if name_contains in c.name:
            return c.status
    raise AssertionError(f"no check matching {name_contains!r} in {[c.name for c in report.checks]}")


def _populate(root):
    """Write the minimum that should make doctor pass: config files + dirs."""
    cdir = root / "config"
    (cdir / "credentials").mkdir(parents=True, exist_ok=True)
    (cdir / "settings.yaml").write_text("orchestrator:\n  default_provider: claude_code\n")
    (cdir / "budgets.yaml").write_text("defaults:\n  daily_usd: 50.0\n")
    (cdir / "projects.yaml").write_text("projects: {}\n")
    (cdir / "user.yaml").write_text('name: "Test"\nemail: "t@example.com"\n')
    (cdir / "credentials" / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-test\n")
    (root / "workspaces" / "personal").mkdir(parents=True, exist_ok=True)
    (root / "global" / "memory").mkdir(parents=True, exist_ok=True)


# --- bare root: FAILs ------------------------------------------------------


def test_bare_root_fails(temp_root):
    report = doctor.run_checks()
    assert report.failed > 0
    assert not report.ok
    # Missing config files are FAILs.
    assert _status(report, "settings.yaml") == doctor.FAIL
    assert _status(report, "budgets.yaml") == doctor.FAIL
    assert _status(report, "projects.yaml") == doctor.FAIL
    # Neither user.yaml nor user.yaml.example → FAIL.
    assert _status(report, "user.yaml") == doctor.FAIL
    # No API key, no `claude` CLI → provider unreachable is a FAIL.
    assert _status(report, "provider reachable") == doctor.FAIL


def test_bare_root_cli_nonzero(temp_root):
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code != 0, result.output
    assert "[FAIL]" in result.output
    assert "failed" in result.output.lower()


# --- populated root: PASS / exit 0 -----------------------------------------


def test_populated_root_passes(temp_root):
    _populate(temp_root)
    report = doctor.run_checks()
    assert report.ok, [
        (c.name, c.status, c.detail) for c in report.checks if c.status == doctor.FAIL
    ]
    assert report.failed == 0
    assert _status(report, "AGENTOS_ROOT") == doctor.PASS
    assert _status(report, "settings.yaml") == doctor.PASS
    assert _status(report, "user.yaml") == doctor.PASS
    assert _status(report, "provider reachable") == doctor.PASS  # key in .env
    assert _status(report, "personal") == doctor.PASS


def test_populated_root_cli_exit_zero(temp_root):
    _populate(temp_root)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "passed" in result.output.lower()


# --- specific behaviors ----------------------------------------------------


def test_user_example_only_warns_not_fails(temp_root):
    _populate(temp_root)
    cdir = temp_root / "config"
    (cdir / "user.yaml").unlink()
    (cdir / "user.yaml.example").write_text('name: "Your Name"\n')
    report = doctor.run_checks()
    assert _status(report, "user.yaml") == doctor.WARN
    assert report.ok  # a warning must not flip exit code


def test_invalid_yaml_is_fail(temp_root):
    _populate(temp_root)
    (temp_root / "config" / "settings.yaml").write_text("orchestrator: [unclosed\n")
    report = doctor.run_checks()
    assert _status(report, "settings.yaml") == doctor.FAIL
    assert not report.ok


def test_provider_pass_via_cli_without_key(temp_root, monkeypatch):
    """`claude` CLI on PATH alone (no API key) is enough to PASS provider check."""
    _populate(temp_root)
    (temp_root / "config" / "credentials" / ".env").write_text("# no key here\n")
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None)
    report = doctor.run_checks()
    assert _status(report, "provider reachable") == doctor.PASS


def test_missing_optional_tool_warns(temp_root):
    """git/gog/claude missing → WARN, never FAIL (they're optional)."""
    _populate(temp_root)  # which() still stubbed to None by the fixture
    report = doctor.run_checks()
    assert _status(report, "Tool: git") == doctor.WARN
    assert _status(report, "Tool: gog") == doctor.WARN


def test_unparseable_agent_spec_warns(temp_root):
    """A broken agent.md (invalid frontmatter) → WARN naming it, not a silent drop."""
    _populate(temp_root)
    good = temp_root / "agents" / "researcher"
    good.mkdir(parents=True)
    (good / "agent.md").write_text('---\nname: researcher\nmodel:\n  preferred: m\n---\nbody\n')
    bad = temp_root / "agents" / "broken"
    bad.mkdir(parents=True)
    # Unquoted apostrophe after a brace → YAML parse error, like the real drift seen.
    (bad / "agent.md").write_text("---\ndescription: {{x}}'s thing\n---\nbody\n")
    report = doctor.run_checks()
    assert _status(report, "Agents: load") == doctor.WARN
    detail = next(c.detail for c in report.checks if "Agents: load" in c.name)
    assert "broken" in detail
    assert report.ok  # still non-blocking
