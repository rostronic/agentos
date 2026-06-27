"""Phase 9 — notification routing (channels mocked, no real push)."""

from __future__ import annotations

import pytest

from agentos.notify import notifier


@pytest.fixture
def captured(monkeypatch):
    """Capture push sends instead of firing real macOS notifications."""
    sent = []
    # Patch the dispatch dict entry (it holds a ref to the original function).
    patched = dict(notifier._CHANNELS)
    patched["push"] = lambda title, msg: sent.append(("push", title, msg)) or True
    monkeypatch.setattr(notifier, "_CHANNELS", patched)
    return sent


CFG = {
    "channels": {"push": {"enabled": True}, "telegram": {"enabled": False}},
    "triggers": {
        "sprint_completed": {"channels": ["push"]},
        "agent_blocked": {"channels": ["push", "telegram"]},
        "schedule_fired": {"channels": []},
    },
}


def test_routes_to_enabled_channel(captured):
    res = notifier.notify("sprint_completed", "Done", "5 tasks", config=CFG)
    assert res["sent"] == ["push"]
    assert len(captured) == 1


def test_skips_disabled_channel(captured):
    # agent_blocked wants push+telegram, but telegram is disabled → only push
    res = notifier.notify("agent_blocked", "Blocked", "need input", config=CFG)
    assert res["sent"] == ["push"]


def test_silent_trigger_sends_nothing(captured):
    res = notifier.notify("schedule_fired", "Fired", "x", config=CFG)
    assert res["sent"] == []
    assert captured == []


def test_unknown_trigger_sends_nothing(captured):
    res = notifier.notify("nonexistent", "x", "y", config=CFG)
    assert res["sent"] == []


def test_budget_threshold_picks_right_trigger(monkeypatch):
    calls = []
    monkeypatch.setattr(notifier, "notify", lambda trig, *a, **k: calls.append(trig))
    notifier.budget_threshold(80, 8.0, 10.0)
    notifier.budget_threshold(100, 10.0, 10.0)
    assert calls == ["budget_80_percent", "budget_exceeded"]


def test_push_send_off_macos_is_safe(monkeypatch):
    """If osascript is missing, _send_push returns False, never raises."""
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    assert notifier._send_push("t", "m") is False
