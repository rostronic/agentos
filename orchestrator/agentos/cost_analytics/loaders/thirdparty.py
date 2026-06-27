"""Third-party loader — parses config/cost-manual.csv into CostRecords.

Third-party APIs (Stripe fees, OpenAI, ElevenLabs, YouTube…) have no unified
export, so the MVP uses a single hand-filled CSV. Each row's `source` carries its
own origin (stripe, openai, …) and `native_id` is reconciled via the same mapping
table. Phase 2 adds named connectors that emit these same record dicts.

Expected CSV columns:
  source,service,native_id,period,amount_usd,currency,note
"""

from __future__ import annotations

import csv
from pathlib import Path

from agentos.cost_analytics import mapping


def _to_float(val) -> float:
    s = str(val or "").strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def load(path: str | Path) -> list[dict]:
    """Parse a manual-cost CSV into CostRecords (one record per non-empty row)."""
    path = Path(path)
    if not path.exists():
        return []

    records: list[dict] = []
    with path.open(newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=2):  # row 1 = header
            source = (row.get("source") or "").strip()
            native_id = (row.get("native_id") or "").strip()
            period = (row.get("period") or "").strip()
            if not source or not period:
                continue  # skip blank/comment rows
            service = (row.get("service") or "").strip() or source
            amount = round(_to_float(row.get("amount_usd")), 6)
            currency = (row.get("currency") or "USD").strip() or "USD"
            note = (row.get("note") or "").strip()
            records.append({
                "project": mapping.resolve(source, native_id),
                "source": source,
                "service": service,
                "period": period,
                "amount_usd": amount,
                "currency": currency,
                "amount_native": amount,
                "provenance": "manual-entry",
                "native_id": native_id or None,
                "labels": {"note": note} if note else None,
                "raw_ref": f"{path.name}:row={i}",
            })
    return records
