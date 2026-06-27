"""GCP (+ Firebase) loader — parses a Cloud Billing export CSV into CostRecords.

Firebase spend rolls up under the same GCP billing account, so there is ONE
ingestion path for both. The realistic, industry-standard mechanism is the GCP
Cloud Billing export (BigQuery or CSV). The MVP reads a seeded CSV mirroring that
schema; Phase 2 swaps the input adapter (connectors/gcp_billing.py) without
touching this loader's output shape.

Expected CSV columns (a subset of the real export):
  service.description   -> service        (e.g. "Cloud Run", "Cloud Firestore")
  project.id            -> native_id      (e.g. example-shop-prod) -> mapped to project
  cost                  -> amount_usd      (summed with credits; credits are negative)
  credits               -> added to cost   (optional; blank = 0)
  usage_start_time      -> period basis    (date; bucketed to month-start)
  currency              -> currency        (optional; default USD)
  billing_account_id    -> billing_account (optional)

Rows are grouped by (project.id, service.description, month) and cost+credits are
summed per group; one CostRecord is emitted per group.
"""

from __future__ import annotations

import csv
from pathlib import Path

from agentos.cost_analytics import mapping


def _month_start(date_str: str) -> str:
    """YYYY-MM-DD (or longer timestamp) -> first-of-month YYYY-MM-01."""
    ds = (date_str or "").strip()
    if len(ds) >= 7:
        return ds[:7] + "-01"
    return "1970-01-01"


def _to_float(val) -> float:
    if val is None:
        return 0.0
    s = str(val).strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def load(path: str | Path) -> list[dict]:
    """Parse a GCP billing-export CSV into CostRecords.

    Sums cost + credits (credits negative) and groups by
    (project.id, service.description, month).
    """
    path = Path(path)
    if not path.exists():
        return []

    # (native_id, service, month) -> aggregated dict
    groups: dict[tuple, dict] = {}
    with path.open(newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=2):  # row 1 = header
            native_id = (row.get("project.id") or "").strip()
            service = (row.get("service.description") or "").strip() or "unknown"
            month = _month_start(row.get("usage_start_time", ""))
            cost = _to_float(row.get("cost"))
            credits = _to_float(row.get("credits"))
            currency = (row.get("currency") or "USD").strip() or "USD"
            billing_account = (row.get("billing_account_id") or "").strip() or None
            amount = cost + credits  # credits are negative; net them out

            key = (native_id, service, month)
            g = groups.get(key)
            if g is None:
                g = {
                    "native_id": native_id,
                    "service": service,
                    "month": month,
                    "amount_usd": 0.0,
                    "currency": currency,
                    "billing_account": billing_account,
                    "rows": [],
                }
                groups[key] = g
            g["amount_usd"] += amount
            g["rows"].append(i)

    records: list[dict] = []
    for g in groups.values():
        amount = round(g["amount_usd"], 6)
        records.append({
            "project": mapping.resolve("gcp", g["native_id"]),
            "source": "gcp",
            "service": g["service"],
            "period": g["month"],
            "amount_usd": amount,
            "currency": g["currency"],
            "amount_native": amount,
            "provenance": "billing-csv",
            "billing_account": g["billing_account"],
            "native_id": g["native_id"],
            "raw_ref": f"{path.name}:rows={','.join(str(r) for r in g['rows'])}",
        })
    return records
