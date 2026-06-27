"""Per-model pricing table (USD per million tokens).

Shared by providers (for live cost) and token_analytics (for historic cost).
Update when prices change. Prices as of 2026-06.
"""

from __future__ import annotations

# (input_per_mtok, output_per_mtok, cache_write_per_mtok, cache_read_per_mtok)
PRICING: dict[str, tuple[float, float, float, float]] = {
    # Claude
    "claude-opus-4-8": (15.00, 75.00, 18.75, 1.50),
    "claude-sonnet-4-6": (3.00, 15.00, 3.75, 0.30),
    "claude-haiku-4-5": (0.80, 4.00, 1.00, 0.08),
    # OpenAI (approximate)
    "gpt-4o": (2.50, 10.00, 0.0, 1.25),
    "gpt-4o-mini": (0.15, 0.60, 0.0, 0.075),
    # Local — free
    "llama3.1:70b": (0.0, 0.0, 0.0, 0.0),
}

# Prefix fallbacks for model aliases with version suffixes
_PREFIX_FALLBACKS = {
    "claude-opus": "claude-opus-4-8",
    "claude-sonnet": "claude-sonnet-4-6",
    "claude-haiku": "claude-haiku-4-5",
    "gpt-4o-mini": "gpt-4o-mini",
    "gpt-4o": "gpt-4o",
}


def _resolve(model: str) -> tuple[float, float, float, float] | None:
    if model in PRICING:
        return PRICING[model]
    for prefix, canonical in _PREFIX_FALLBACKS.items():
        if model.startswith(prefix):
            return PRICING.get(canonical)
    return None


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    """Compute USD cost for a single completion. Returns 0.0 for unknown models."""
    rates = _resolve(model)
    if rates is None:
        return 0.0
    in_rate, out_rate, cw_rate, cr_rate = rates
    return (
        input_tokens * in_rate
        + output_tokens * out_rate
        + cache_write_tokens * cw_rate
        + cache_read_tokens * cr_rate
    ) / 1_000_000
