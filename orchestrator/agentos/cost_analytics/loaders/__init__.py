"""Loaders parse seeded source files into normalized CostRecord dicts.

Each loader accepts an explicit path/data argument so tests pass fixtures and the
live cloud / ~ is never touched. ingest_all() runs every loader and writes via the
store. Phase 2 connectors (live GCP BigQuery, Stripe, OpenAI) emit the SAME record
dicts behind this same interface — only the input adapter changes.
"""

from __future__ import annotations

from pathlib import Path

from agentos.cost_analytics import store
from agentos.cost_analytics.loaders import claude, gcp, thirdparty


def ingest_all(
    *,
    gcp_csv: str | Path | None = None,
    manual_csv: str | Path | None = None,
    claude_agg: dict | None = None,
    include_claude: bool = False,
) -> dict[str, int]:
    """Run every loader and write each source via store.replace_source().

    All inputs are explicit so tests pass fixtures and live cloud / ~ is never
    touched. Each source is loaded idempotently (replace_source). Records from a
    manual CSV may carry several `source` values (stripe, openai, …); they are
    grouped by source so each source's replace is correctly scoped.

    `include_claude` defaults to False: the Cost tab tracks REAL external spend
    only. Claude/LLM token usage lives on the Token Usage tab — and on a Max
    subscription the API-equivalent figure isn't money out the door. Pass True to
    opt in (the loader + mapping are kept for that).

    Returns {source: records_written}.
    """
    written: dict[str, int] = {}

    if include_claude:
        claude_records = claude.load(agg=claude_agg)
        if claude_records:
            written["claude"] = store.replace_source("claude", claude_records)

    if gcp_csv is not None:
        gcp_records = gcp.load(gcp_csv)
        if gcp_records:
            written["gcp"] = store.replace_source("gcp", gcp_records)

    if manual_csv is not None:
        manual_records = thirdparty.load(manual_csv)
        by_source: dict[str, list[dict]] = {}
        for rec in manual_records:
            by_source.setdefault(rec["source"], []).append(rec)
        for source, recs in by_source.items():
            written[source] = store.replace_source(source, recs)

    return written
