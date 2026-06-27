"""Notifications — tell the human when something needs attention.

A thin abstraction over channels. macOS push works today; telegram/discord/email
are stubs to wire to your own notification bridges later. Routing is config-driven
(config/notifications.yaml: `channels` enable-flags + `triggers` event→channels),
so which events go where is data, not code.

Trigger names match config/notifications.yaml:
  sprint_completed, run_failed, run_repeatedly_failed, agent_blocked,
  budget_80_percent, budget_exceeded, schedule_fired, run_timeout
"""

from __future__ import annotations

import subprocess

import yaml

from agentos.core.config import AGENTOS_ROOT

CONFIG_FILE = AGENTOS_ROOT / "config" / "notifications.yaml"

# Fallback if the config file is missing/unparseable.
DEFAULT_CONFIG = {
    "channels": {"push": {"enabled": True}},
    "triggers": {
        "sprint_completed": {"channels": ["push"]},
        "run_failed": {"channels": ["push"]},
        "agent_blocked": {"channels": ["push"]},
        "budget_80_percent": {"channels": ["push"]},
        "budget_exceeded": {"channels": ["push"]},
    },
}


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return yaml.safe_load(CONFIG_FILE.read_text()) or DEFAULT_CONFIG
        except (yaml.YAMLError, OSError):
            pass
    return DEFAULT_CONFIG


# --- channels ---------------------------------------------------------------
def _send_push(title: str, message: str) -> bool:
    """macOS notification via osascript. No-op (False) off macOS."""
    try:
        safe_msg = message.replace('"', "'")[:200]
        safe_title = title.replace('"', "'")[:80]
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe_msg}" with title "AgentOS" subtitle "{safe_title}"'],
            capture_output=True, timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.SubprocessError):
        return False


def _send_stub(title: str, message: str) -> bool:
    return False  # telegram/discord/email — wire to your own bridges later


_CHANNELS = {
    "push": _send_push,
    "telegram": _send_stub,
    "discord": _send_stub,
    "email": _send_stub,
}


def push(title: str, message: str) -> bool:
    """Send a macOS push directly (used by the daily brief). Returns True if sent."""
    return _send_push(title, message)


def notify(trigger: str, title: str, message: str, *, config: dict | None = None) -> dict:
    """Route a trigger to its configured + enabled channels."""
    cfg = config or _load_config()
    chan_cfg = cfg.get("channels", {})
    wanted = cfg.get("triggers", {}).get(trigger, {}).get("channels", [])

    sent = []
    for ch in wanted:
        if not chan_cfg.get(ch, {}).get("enabled", False):
            continue
        fn = _CHANNELS.get(ch)
        if fn and fn(title, message):
            sent.append(ch)
    return {"sent": sent, "trigger": trigger}


# --- convenience emitters used by the orchestrator --------------------------
def sprint_done(sprint_id: str, processed: int, reason: str) -> dict:
    return notify("sprint_completed", "Sprint complete",
                  f"{processed} tasks processed · {reason}")


def run_failed(agent: str, error: str) -> dict:
    return notify("run_failed", f"{agent} failed", error[:160])


def agent_blocked(agent: str, question: str) -> dict:
    return notify("agent_blocked", f"{agent} needs you", question[:160])


def budget_threshold(pct: int, spent: float, cap: float) -> dict:
    trigger = "budget_exceeded" if pct >= 100 else "budget_80_percent"
    return notify(trigger, f"Budget at {pct}%", f"${spent:.2f} of ${cap:.2f} spent today")
