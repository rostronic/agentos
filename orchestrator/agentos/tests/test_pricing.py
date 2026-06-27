"""Phase 1 — pricing math."""

from __future__ import annotations

from agentos.providers.pricing import cost_usd


def test_sonnet_cost():
    # 1M input + 1M output at sonnet rates = $3 + $15 = $18
    assert abs(cost_usd("claude-sonnet-4-6", 1_000_000, 1_000_000) - 18.0) < 0.001


def test_haiku_cheaper_than_sonnet():
    haiku = cost_usd("claude-haiku-4-5", 100_000, 100_000)
    sonnet = cost_usd("claude-sonnet-4-6", 100_000, 100_000)
    assert haiku < sonnet


def test_unknown_model_returns_zero():
    assert cost_usd("some-unknown-model", 1000, 1000) == 0.0


def test_prefix_fallback_resolves():
    # versioned alias should resolve via prefix
    assert cost_usd("claude-sonnet-4-6-20260101", 1_000_000, 0) > 0


def test_cache_tokens_counted():
    base = cost_usd("claude-sonnet-4-6", 1000, 1000)
    with_cache = cost_usd("claude-sonnet-4-6", 1000, 1000, cache_write_tokens=1000, cache_read_tokens=1000)
    assert with_cache > base
