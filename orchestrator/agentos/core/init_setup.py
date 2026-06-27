"""First-run setup — write config/{user,settings,budgets}.yaml from the tracked
`.example` templates and report provider readiness.

Pure functions (no typer / no prompting) so the CLI `agentos init` command stays a
thin wrapper and the logic is unit-testable. Mirrors the onboard.py (logic) +
cli.py (presentation) split.

The config directory is resolved at call time from, in order:
  1. an explicit ``config_dir`` argument,
  2. the ``AGENTOS_CONFIG_DIR`` environment variable,
  3. ``agentos.core.config.CONFIG_DIR`` (the default ~/agentos/config).
This keeps `agentos init` runnable against a temp HOME for testing without
monkeypatching module globals.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from agentos.core import config

# Providers the wizard knows how to set up. Other providers (ollama, openai) are
# valid in settings.yaml but not first-run targets.
KNOWN_PROVIDERS = ("claude_code", "claude_api")


@dataclass
class InitResult:
    config_dir: Path
    written: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    provider: str = "claude_code"
    provider_ok: bool = False
    provider_message: str = ""
    next_steps: list[str] = field(default_factory=list)


def resolve_config_dir(config_dir: str | Path | None = None) -> Path:
    """Resolve the target config dir (arg → AGENTOS_CONFIG_DIR env → config.CONFIG_DIR)."""
    if config_dir:
        return Path(config_dir).expanduser()
    env = os.environ.get("AGENTOS_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    return config.CONFIG_DIR


def _example_path(config_dir: Path, name: str) -> Path:
    return config_dir / f"{name}.example"


def _render_user_yaml(
    template: str,
    *,
    name: str,
    email: str,
    timezone: str,
    telegram_chat_id: str,
) -> str:
    """Fill the user.yaml.example template's identity values.

    Replaces the top-level ``key: "..."`` lines for the fields the wizard collects,
    preserving every comment and the rest of the template. Done line-by-line (not a
    blind global regex) so only the intended keys change.
    """
    values = {
        "name": name,
        "email": email,
        "timezone": timezone,
        "telegram_chat_id": telegram_chat_id,
    }
    out_lines: list[str] = []
    for line in template.splitlines():
        m = re.match(r"^(\s*)([A-Za-z_]+):\s*(.*)$", line)
        if m and m.group(2) in values:
            indent, key = m.group(1), m.group(2)
            out_lines.append(f'{indent}{key}: "{values[key]}"')
        else:
            out_lines.append(line)
    rendered = "\n".join(out_lines)
    # Preserve a trailing newline if the template had one (splitlines drops it).
    return rendered + "\n" if template.endswith("\n") else rendered


def _set_provider(settings_template: str, provider: str) -> str:
    """Set ``default_provider:`` in the settings.yaml template to ``provider``,
    preserving the inline comment if any. No-op if the key isn't present."""
    def repl(m: re.Match) -> str:
        comment = m.group("comment") or ""
        return f"{m.group('indent')}default_provider: {provider}{comment}"

    return re.sub(
        r"^(?P<indent>\s*)default_provider:\s*\S+(?P<comment>\s+#.*)?$",
        repl,
        settings_template,
        count=1,
        flags=re.MULTILINE,
    )


def provider_status(provider: str, config_dir: Path | None = None) -> tuple[bool, str]:
    """Is the chosen provider ready to run? Returns (ok, human message).

    - claude_code → the `claude` CLI must be on PATH (auth is checked at dispatch).
    - claude_api  → ANTHROPIC_API_KEY must be set (env or the .env in config_dir).
    """
    if provider == "claude_code":
        if shutil.which("claude"):
            return True, "`claude` CLI found on PATH (subscription billing)."
        return (
            False,
            "`claude` CLI not found on PATH. Install Claude Code and run `claude` "
            "then `/login` with your Max/Pro account — or switch to claude_api.",
        )
    if provider == "claude_api":
        key = _api_key_present(config_dir)
        if key:
            return True, "ANTHROPIC_API_KEY is set (metered API billing)."
        return (
            False,
            "ANTHROPIC_API_KEY not set. Add it to config/credentials/.env "
            "(copy from .env.example) or export it in your shell.",
        )
    return True, f"Provider '{provider}' selected (no readiness check)."


def _api_key_present(config_dir: Path | None) -> bool:
    """ANTHROPIC_API_KEY in the environment, or non-blank in <config_dir>/credentials/.env."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    cdir = config_dir or config.CONFIG_DIR
    env_file = cdir / "credentials" / ".env"
    if not env_file.exists():
        return False
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == "ANTHROPIC_API_KEY" and value.strip().strip('"').strip("'"):
            return True
    return False


def write_configs(
    *,
    name: str,
    email: str,
    timezone: str = "America/Los_Angeles",
    provider: str = "claude_code",
    telegram_chat_id: str = "",
    config_dir: str | Path | None = None,
    force: bool = False,
) -> InitResult:
    """Write config/{user,settings,budgets}.yaml from the `.example` templates.

    Identity values are substituted into user.yaml; the chosen provider is set in
    settings.yaml; budgets.yaml is copied verbatim. Existing files are left in place
    unless ``force`` is True. Raises FileNotFoundError if a template is missing.
    """
    cdir = resolve_config_dir(config_dir)
    cdir.mkdir(parents=True, exist_ok=True)
    result = InitResult(config_dir=cdir, provider=provider)

    # user.yaml — rendered from template with identity values.
    user_example = _example_path(cdir, "user.yaml")
    if not user_example.exists():
        raise FileNotFoundError(f"missing template: {user_example}")
    user_out = cdir / "user.yaml"
    if user_out.exists() and not force:
        result.skipped.append(user_out)
    else:
        rendered = _render_user_yaml(
            user_example.read_text(encoding="utf-8"),
            name=name, email=email, timezone=timezone,
            telegram_chat_id=telegram_chat_id,
        )
        user_out.write_text(rendered, encoding="utf-8")
        result.written.append(user_out)

    # settings.yaml — copy template, set default_provider.
    settings_example = _example_path(cdir, "settings.yaml")
    if not settings_example.exists():
        raise FileNotFoundError(f"missing template: {settings_example}")
    settings_out = cdir / "settings.yaml"
    if settings_out.exists() and not force:
        result.skipped.append(settings_out)
    else:
        settings_out.write_text(
            _set_provider(settings_example.read_text(encoding="utf-8"), provider),
            encoding="utf-8",
        )
        result.written.append(settings_out)

    # budgets.yaml — copy template verbatim.
    budgets_example = _example_path(cdir, "budgets.yaml")
    if not budgets_example.exists():
        raise FileNotFoundError(f"missing template: {budgets_example}")
    budgets_out = cdir / "budgets.yaml"
    if budgets_out.exists() and not force:
        result.skipped.append(budgets_out)
    else:
        shutil.copyfile(budgets_example, budgets_out)
        result.written.append(budgets_out)

    result.provider_ok, result.provider_message = provider_status(provider, cdir)
    result.next_steps = _next_steps(result)
    return result


def _next_steps(result: InitResult) -> list[str]:
    steps: list[str] = []
    if result.provider == "claude_code" and not result.provider_ok:
        steps.append("Install Claude Code, then run `claude` and `/login` (Max/Pro account).")
    if result.provider == "claude_api" and not result.provider_ok:
        steps.append(
            "Copy config/credentials/.env.example → .env and add your ANTHROPIC_API_KEY."
        )
    steps.append("Verify the CLI: `agentos --help` then `agentos agents`.")
    steps.append("Dispatch an agent: `agentos dispatch researcher \"find 3 sources on X\"`.")
    steps.append("Open the dashboard: `agentos serve` → http://127.0.0.1:8787")
    return steps
