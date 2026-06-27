"""First-run setup: write_configs renders valid yaml, provider checks, idempotency,
and the `agentos init` CLI command non-interactively into a temp config dir."""

from __future__ import annotations

import yaml
from typer.testing import CliRunner

from agentos.core import init_setup
from agentos.entrypoints.cli import app

runner = CliRunner()


def _seed_templates(cdir):
    """Stage the tracked .example templates into a temp config dir (init copies them)."""
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "user.yaml.example").write_text(
        '# header comment\n'
        'name: "Your Name"\n'
        'email: "you@example.com"\n'
        'timezone: "America/Los_Angeles"\n'
        'personal_dir: "workspaces/personal"\n'
        'cos_dir: "workspaces/personal/chief-of-staff"\n'
        'telegram_chat_id: ""\n',
        encoding="utf-8",
    )
    (cdir / "settings.yaml.example").write_text(
        "orchestrator:\n"
        "  default_provider: claude_code  # how Claude is billed\n"
        "  default_model: claude-sonnet-4-6\n",
        encoding="utf-8",
    )
    (cdir / "budgets.yaml.example").write_text(
        "defaults:\n  daily_usd: 50.00\n",
        encoding="utf-8",
    )


def test_write_configs_renders_user_yaml(tmp_path):
    cdir = tmp_path / "config"
    _seed_templates(cdir)
    res = init_setup.write_configs(
        name="Ada Lovelace", email="ada@example.com", timezone="Europe/London",
        provider="claude_api", config_dir=cdir,
    )
    user = yaml.safe_load((cdir / "user.yaml").read_text())
    assert user["name"] == "Ada Lovelace"
    assert user["email"] == "ada@example.com"
    assert user["timezone"] == "Europe/London"
    # untouched template keys survive
    assert user["personal_dir"] == "workspaces/personal"
    assert {p.name for p in res.written} == {"user.yaml", "settings.yaml", "budgets.yaml"}
    # template ends with a newline → rendered output should too (POSIX text file)
    assert (cdir / "user.yaml").read_text().endswith("\n")


def test_write_configs_sets_provider_in_settings(tmp_path):
    cdir = tmp_path / "config"
    _seed_templates(cdir)
    init_setup.write_configs(
        name="A", email="a@b.c", provider="claude_api", config_dir=cdir,
    )
    settings = yaml.safe_load((cdir / "settings.yaml").read_text())
    assert settings["orchestrator"]["default_provider"] == "claude_api"
    # other settings preserved
    assert settings["orchestrator"]["default_model"] == "claude-sonnet-4-6"


def test_write_configs_idempotent_without_force(tmp_path):
    cdir = tmp_path / "config"
    _seed_templates(cdir)
    init_setup.write_configs(name="A", email="a@b.c", config_dir=cdir)
    (cdir / "user.yaml").write_text('name: "Manual Edit"\n', encoding="utf-8")
    res2 = init_setup.write_configs(name="B", email="b@b.c", config_dir=cdir)
    # existing files kept, not clobbered
    assert (cdir / "user.yaml").read_text() == 'name: "Manual Edit"\n'
    assert all(p.name == "user.yaml" for p in res2.skipped if p.name == "user.yaml")
    assert {p.name for p in res2.skipped} == {"user.yaml", "settings.yaml", "budgets.yaml"}


def test_write_configs_force_overwrites(tmp_path):
    cdir = tmp_path / "config"
    _seed_templates(cdir)
    init_setup.write_configs(name="A", email="a@b.c", config_dir=cdir)
    res2 = init_setup.write_configs(name="Grace", email="g@b.c", config_dir=cdir, force=True)
    user = yaml.safe_load((cdir / "user.yaml").read_text())
    assert user["name"] == "Grace"
    assert {p.name for p in res2.written} == {"user.yaml", "settings.yaml", "budgets.yaml"}


def test_write_configs_missing_template_raises(tmp_path):
    cdir = tmp_path / "config"
    cdir.mkdir()
    try:
        init_setup.write_configs(name="A", email="a@b.c", config_dir=cdir)
        assert False, "expected FileNotFoundError"
    except FileNotFoundError as e:
        assert "template" in str(e)


def test_provider_status_claude_api_detects_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xyz")
    ok, msg = init_setup.provider_status("claude_api", tmp_path)
    assert ok and "ANTHROPIC_API_KEY" in msg


def test_provider_status_claude_api_reads_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    creds = tmp_path / "credentials"
    creds.mkdir()
    (creds / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-fromfile\n", encoding="utf-8")
    ok, _ = init_setup.provider_status("claude_api", tmp_path)
    assert ok


def test_provider_status_claude_api_missing_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ok, msg = init_setup.provider_status("claude_api", tmp_path)
    assert not ok and "not set" in msg


def test_provider_status_claude_code_missing_cli(monkeypatch):
    monkeypatch.setattr(init_setup.shutil, "which", lambda _: None)
    ok, msg = init_setup.provider_status("claude_code")
    assert not ok and "claude" in msg.lower()


def test_resolve_config_dir_prefers_arg_then_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOS_CONFIG_DIR", str(tmp_path / "fromenv"))
    assert init_setup.resolve_config_dir(tmp_path / "fromarg") == tmp_path / "fromarg"
    assert init_setup.resolve_config_dir() == tmp_path / "fromenv"


def test_cli_init_noninteractive(tmp_path, monkeypatch):
    cdir = tmp_path / "config"
    _seed_templates(cdir)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = runner.invoke(app, [
        "init", "--name", "Test User", "--email", "t@example.com",
        "--provider", "claude_api", "--config-dir", str(cdir), "--yes",
    ])
    assert result.exit_code == 0, result.output
    assert "Next steps" in result.output
    user = yaml.safe_load((cdir / "user.yaml").read_text())
    assert user["name"] == "Test User"
    assert yaml.safe_load((cdir / "settings.yaml").read_text())["orchestrator"]["default_provider"] == "claude_api"


def test_cli_init_rejects_unknown_provider(tmp_path):
    cdir = tmp_path / "config"
    _seed_templates(cdir)
    result = runner.invoke(app, [
        "init", "--name", "X", "--email", "x@y.z",
        "--provider", "gpt5", "--config-dir", str(cdir), "--yes",
    ])
    assert result.exit_code == 1
    assert "Unknown provider" in result.output


def test_cli_init_requires_name_noninteractive(tmp_path):
    cdir = tmp_path / "config"
    _seed_templates(cdir)
    result = runner.invoke(app, [
        "init", "--email", "x@y.z", "--config-dir", str(cdir), "--yes",
    ])
    assert result.exit_code == 1
    assert "required" in result.output.lower()
