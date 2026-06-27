"""Phase-2 GCP BigQuery connector: row->CostRecord mapping (no live BigQuery needed).

Verifies the connector emits the SAME record shape as loaders/gcp.py (source='gcp',
month-start period, mapped project) so it's a true drop-in input-adapter swap.
"""
from agentos.cost_analytics import store
from agentos.cost_analytics.connectors import gcp_billing

# Exactly the CostRecord keys loaders/gcp.py emits — proves drop-in compatibility.
GCP_RECORD_KEYS = {
    "project", "source", "service", "period", "amount_usd", "currency",
    "amount_native", "provenance", "billing_account", "native_id", "raw_ref",
}

# BigQuery result rows (already grouped by project+service+month, as the SQL returns).
BQ_ROWS = [
    {"gcp_project": "example-shop-prod", "service": "Cloud Run", "invoice_month": "202605", "net_cost_usd": 37.0, "currency": "USD"},
    {"gcp_project": "example-shop-prod", "service": "Cloud Firestore", "invoice_month": "202605", "net_cost_usd": 8.5, "currency": "USD"},
    {"gcp_project": "example-news-prod", "service": "Cloud Run", "invoice_month": "202605", "net_cost_usd": 18.0, "currency": "USD"},
    {"gcp_project": "mystery-app-prod", "service": "Cloud Run", "invoice_month": "202605", "net_cost_usd": 9.0, "currency": "USD"},
]


def test_rows_to_records_shape_matches_gcp_loader(cost_config):
    recs = gcp_billing.rows_to_records(BQ_ROWS, table="t")
    assert len(recs) == 4
    shop = next(r for r in recs if r["native_id"] == "example-shop-prod" and r["service"] == "Cloud Run")
    # same contract as loaders/gcp.py output
    assert shop["source"] == "gcp"
    assert shop["project"] == "example-shop"       # via cost-sources mappings.gcp
    assert shop["service"] == "Cloud Run"
    assert shop["period"] == "2026-05-01"          # YYYYMM -> month-start, like the CSV loader
    assert shop["amount_usd"] == 37.0
    assert shop["currency"] == "USD"
    assert shop["provenance"] == "billing-bigquery"
    assert set(shop) == GCP_RECORD_KEYS  # drop-in compatible with loaders/gcp.py output


def test_unmapped_gcp_project_is_not_dropped(cost_config):
    recs = gcp_billing.rows_to_records(BQ_ROWS)
    myst = next(r for r in recs if r["native_id"] == "mystery-app-prod")
    # unknown project still produces a record (resolves to the unmapped bucket, never dropped)
    assert myst["source"] == "gcp"
    assert myst["amount_usd"] == 9.0
    assert myst["project"]  # non-empty (bucket or heuristic), not silently lost


def test_ingest_replaces_gcp_source(monkeypatch, tmp_path, cost_config):
    # point the store at a temp db and stub the live query so no BigQuery is hit
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "costs.sqlite")
    monkeypatch.setattr(gcp_billing, "load_config", lambda: {"billing_exports": [{"table": "t"}], "lookback_months": 6})
    monkeypatch.setattr(gcp_billing, "_query_rows", lambda exp, since: BQ_ROWS)
    n = gcp_billing.ingest()
    assert n == 4
    rows = store.all_records()
    assert {r["source"] for r in rows} == {"gcp"}
    assert sum(r["amount_usd"] for r in rows) == 72.5
