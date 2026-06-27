"""Kill switch — a global pause flag for autonomous work.

The sprint executor checks this before dispatching each task. `agentos pause`
sets it; `agentos resume` clears it. The flag is a file so it works across
processes (CLI, dashboard, cron all see the same state).
"""

from __future__ import annotations


from agentos.core.config import AGENTOS_ROOT

RUNTIME_DIR = AGENTOS_ROOT / "orchestrator" / "runtime"
PAUSE_FILE = RUNTIME_DIR / "PAUSED"


def pause(reason: str = "") -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    PAUSE_FILE.write_text(reason or "paused")


def resume() -> None:
    PAUSE_FILE.unlink(missing_ok=True)


def is_paused() -> bool:
    return PAUSE_FILE.exists()


def pause_reason() -> str:
    if not PAUSE_FILE.exists():
        return ""
    return PAUSE_FILE.read_text().strip()
