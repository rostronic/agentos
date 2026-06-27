"""Claude loader — folds token_analytics.aggregate() by_project into CostRecords.

IMPORTANT (api-equivalent caveat): token_analytics cost_usd is the pay-per-token
API EQUIVALENT, not money out the door — on a Max subscription the real Claude cost
is the flat monthly fee. We ingest the API-equivalent number and LABEL it as such
(service="claude (api-equiv)") so it's never silently presented as money spent.
Phase 2 adds a subscription mode (flat fee allocated by usage share).
"""

from __future__ import annotations

from agentos.cost_analytics import mapping

SERVICE_LABEL = "claude (api-equiv)"


def _latest_day(agg: dict) -> str | None:
    """The latest day present in the aggregate's by_day, else None."""
    days = [d.get("day") for d in agg.get("by_day", []) if d.get("day")]
    return max(days) if days else None


def load(agg: dict | None = None, *, period: str | None = None) -> list[dict]:
    """Return one CostRecord per by_project row.

    MVP granularity: one record per project for the whole window. `agg` is a
    pre-computed token_analytics aggregate (tests pass one); if None, compute it.
    `period` overrides the derived period (defaults to the latest day in by_day).
    """
    if agg is None:
        from agentos.token_analytics import aggregator as tok_aggregator
        agg = tok_aggregator.aggregate()

    period = period or _latest_day(agg)
    records: list[dict] = []
    for row in agg.get("by_project", []):
        native_id = row.get("name")
        cost = row.get("cost_usd", 0.0) or 0.0
        if not native_id or cost <= 0:
            continue
        rec_period = period or "1970-01-01"
        records.append({
            "project": mapping.resolve("claude", native_id),
            "source": "claude",
            "service": SERVICE_LABEL,
            "period": rec_period,
            "amount_usd": round(float(cost), 6),
            "currency": "USD",
            "amount_native": round(float(cost), 6),
            "provenance": "transcript",
            "native_id": native_id,
            "raw_ref": f"token_analytics:by_project:{native_id}",
        })
    return records
