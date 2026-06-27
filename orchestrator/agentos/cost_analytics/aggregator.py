"""Roll up cost_records into the dashboard contract dict.

Mirrors token_analytics.aggregator.aggregate(): if records is None it reads from
the store; otherwise it aggregates the passed list (for tests). Returns EXACTLY the
shape documented in docs/cost-tracking-plan.md "Aggregation contract" — the API and
dashboard depend on these keys.

Amounts are rounded to 2 decimals at this boundary; the store keeps full precision.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agentos.cost_analytics import mapping, store

UNMAPPED = "unmapped"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _r2(x: float) -> float:
    return round(x or 0.0, 2)


def aggregate(records: list[dict] | None = None) -> dict:
    """Aggregate cost records into the dashboard contract dict."""
    if records is None:
        records = store.all_records()

    bucket = config_unmapped_bucket()

    total_usd = 0.0
    unmapped_usd = 0.0
    periods: list[str] = []
    sources_loaded: set[str] = set()

    by_project: dict[str, dict] = {}
    by_source: dict[str, dict] = {}
    by_service: dict[tuple, dict] = {}
    by_month: dict[str, float] = {}

    for rec in records:
        project = rec.get("project") or bucket
        source = rec.get("source") or "unknown"
        service = rec.get("service") or "unknown"
        period = rec.get("period") or ""
        amount = float(rec.get("amount_usd") or 0.0)
        native_id = rec.get("native_id")

        total_usd += amount
        if project == bucket:
            unmapped_usd += amount
        if period:
            periods.append(period)
        sources_loaded.add(source)

        # by_project (with pre-computed by_source split + native_ids for unmapped)
        p = by_project.setdefault(project, {
            "name": project, "amount_usd": 0.0, "by_source": {},
            "records": 0, "_native_ids": set(),
        })
        p["amount_usd"] += amount
        p["by_source"][source] = p["by_source"].get(source, 0.0) + amount
        p["records"] += 1
        if project == bucket and native_id:
            p["_native_ids"].add(native_id)

        # by_source
        s = by_source.setdefault(source, {"name": source, "amount_usd": 0.0, "records": 0})
        s["amount_usd"] += amount
        s["records"] += 1

        # by_service (keyed on (service, source) so same name across sources is distinct)
        sv = by_service.setdefault((service, source), {
            "name": service, "source": source, "amount_usd": 0.0,
        })
        sv["amount_usd"] += amount

        # by_month (YYYY-MM)
        if period:
            month = period[:7]
            by_month[month] = by_month.get(month, 0.0) + amount

    # --- shape outputs ---
    project_rows = []
    for p in by_project.values():
        row = {
            "name": p["name"],
            "amount_usd": _r2(p["amount_usd"]),
            "by_source": {k: _r2(v) for k, v in p["by_source"].items()},
            "records": p["records"],
        }
        if p["name"] == bucket:
            row["native_ids"] = sorted(p["_native_ids"])
        project_rows.append(row)
    project_rows.sort(key=lambda r: r["amount_usd"], reverse=True)

    source_rows = [
        {"name": s["name"], "amount_usd": _r2(s["amount_usd"]), "records": s["records"]}
        for s in by_source.values()
    ]
    source_rows.sort(key=lambda r: r["amount_usd"], reverse=True)

    service_rows = [
        {"name": sv["name"], "source": sv["source"], "amount_usd": _r2(sv["amount_usd"])}
        for sv in by_service.values()
    ]
    service_rows.sort(key=lambda r: r["amount_usd"], reverse=True)

    month_rows = [
        {"month": m, "amount_usd": _r2(by_month[m])} for m in sorted(by_month)
    ]

    warnings = mapping.validate_mappings()

    return {
        "totals": {
            "amount_usd": _r2(total_usd),
            "records": len(records),
            "unmapped_usd": _r2(unmapped_usd),
            "currency": "USD",
            "period_start": min(periods) if periods else None,
            "period_end": max(periods) if periods else None,
        },
        "by_project": project_rows,
        "by_source": source_rows,
        "by_service": service_rows,
        "by_month": month_rows,
        "warnings": warnings,
        "meta": {
            "sources_loaded": sorted(sources_loaded),
            "generated_at": _now(),
        },
    }


def config_unmapped_bucket() -> str:
    from agentos.core import config
    return config.cost_sources().get("unmapped_bucket", UNMAPPED)
