"""Configuration loading — settings, budgets, credentials."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# Framework root. Defaults to ~/agentos but is overridable via $AGENTOS_ROOT so the
# framework works from any clone location (CI, custom paths, etc.).
AGENTOS_ROOT = Path(os.environ.get("AGENTOS_ROOT") or (Path.home() / "agentos")).expanduser()
CONFIG_DIR = AGENTOS_ROOT / "config"
ENV_FILE = CONFIG_DIR / "credentials" / ".env"


def _load_env() -> None:
    """Load KEY=VALUE pairs from the .env file into os.environ (no overwrite)."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


@lru_cache(maxsize=1)
def settings() -> dict[str, Any]:
    """Global settings from settings.yaml."""
    _load_env()
    return _load_yaml("settings.yaml")


@lru_cache(maxsize=1)
def budgets() -> dict[str, Any]:
    """Budget config from budgets.yaml."""
    return _load_yaml("budgets.yaml")


# --- user identity ---------------------------------------------------------
# Personal values (name, email, timezone, dirs, telegram) live in config/user.yaml
# (gitignored). config/user.yaml.example is the tracked template. Modules read these
# via the accessors below instead of hardcoding identity, so the framework stays
# generic and each instance configures its own values.

_USER_DEFAULTS: dict[str, Any] = {
    "name": "",
    "email": "",
    "timezone": "America/Los_Angeles",
    "personal_dir": "workspaces/personal",
    "cos_dir": "workspaces/personal/chief-of-staff",
    "telegram_chat_id": "",
    # Daily-brief weather location. Default = San Francisco, CA — a sensible,
    # non-personal placeholder so `agentos brief` works before configuration.
    "weather_lat": 37.7749,
    "weather_lon": -122.4194,
    "weather_label": "San Francisco, CA",
}


@lru_cache(maxsize=1)
def user_config() -> dict[str, Any]:
    """User identity from config/user.yaml, overlaid on safe defaults.

    Falls back to user.yaml.example if user.yaml is absent (e.g. a fresh clone
    that hasn't been configured), then to _USER_DEFAULTS. Never raises.
    """
    data = _load_yaml("user.yaml")
    if not data:
        data = _load_yaml("user.yaml.example")
    merged = dict(_USER_DEFAULTS)
    if isinstance(data, dict):
        merged.update({k: v for k, v in data.items() if v is not None})
    return merged


def user_name() -> str:
    """Configured user display name (empty string if unset)."""
    return str(user_config().get("name", "") or "")


def user_email() -> str:
    """Primary account email for gog/calendar/gmail + brief User-Agent."""
    return str(user_config().get("email", "") or "")


def user_timezone() -> str:
    """IANA timezone used when creating calendar events."""
    return str(user_config().get("timezone") or _USER_DEFAULTS["timezone"])


def personal_dir() -> str:
    """Personal workspace dir, relative to AGENTOS_ROOT."""
    return str(user_config().get("personal_dir") or _USER_DEFAULTS["personal_dir"])


def cos_dir() -> str:
    """Chief-of-staff working dir, relative to AGENTOS_ROOT."""
    return str(user_config().get("cos_dir") or _USER_DEFAULTS["cos_dir"])


def telegram_chat_id() -> str:
    """Telegram chat id for notifications (empty string if disabled)."""
    return str(user_config().get("telegram_chat_id", "") or "")


def _user_float(key: str) -> float:
    """Read a numeric user-config value, falling back to its default."""
    raw = user_config().get(key)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(_USER_DEFAULTS[key])


def weather_location() -> tuple[float, float, str]:
    """Daily-brief weather location as (lat, lon, label) from config/user.yaml.

    Falls back to the _USER_DEFAULTS placeholder so the brief never hardcodes a
    city and always resolves a usable location for any instance.
    """
    lat = _user_float("weather_lat")
    lon = _user_float("weather_lon")
    label = str(user_config().get("weather_label") or _USER_DEFAULTS["weather_label"])
    return lat, lon, label


def get_api_key(provider: str) -> str | None:
    """Fetch the API key for a provider from the environment."""
    _load_env()
    key_names = {
        "claude": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
    }
    return os.environ.get(key_names.get(provider, ""))


def project_settings(project: str | None) -> dict[str, Any]:
    """Settings for a specific project, falling back to defaults."""
    if not project:
        return {}
    return settings().get("projects", {}).get(project, {})


def budget_for_project(project: str | None) -> dict[str, Any]:
    """Effective budget for a project = defaults overlaid with project overrides."""
    b = budgets()
    effective = dict(b.get("defaults", {}))
    if project:
        effective.update(b.get("projects", {}).get(project, {}))
    return effective


@lru_cache(maxsize=1)
def projects() -> dict[str, Any]:
    """Project registry from projects.yaml: slug -> {workspace, repo_path, memory_path}."""
    return _load_yaml("projects.yaml").get("projects", {})


@lru_cache(maxsize=1)
def cost_sources() -> dict[str, Any]:
    """Cost-source mapping from cost-sources.yaml.

    Shape: {"mappings": {source: {native_id: slug}}, "unmapped_bucket": "unmapped"}.
    Used by cost_analytics.mapping to reconcile native per-source project ids to
    registry slugs. Mirrors projects() / budgets().
    """
    return _load_yaml("cost-sources.yaml")


def project_config(slug: str | None) -> dict[str, Any]:
    """Registry entry for one project slug, or {} if unknown."""
    if not slug:
        return {}
    return projects().get(slug, {})


def workspace_for_project(slug: str | None) -> str | None:
    """The workspace ('personal'|'business') a project belongs to, or None if unknown."""
    return project_config(slug).get("workspace") or None
