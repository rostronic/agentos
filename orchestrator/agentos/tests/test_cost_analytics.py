"""Phase-1 cost tracking — mapping, loaders, aggregator, store idempotency."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentos.cost_analytics import aggregator, mapping, store
from agentos.cost_analytics import loaders
from agentos.cost_analytics.loaders import claude, gcp, thirdparty

FIXTURES = Path(__file__).parent / "fixtures"
GCP_CSV = FIXTURES / "gcp-billing-sample.csv"
MANUAL_CSV = FIXTURES / "cost-manual-sample.csv"


@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    """Point the cost store at a throwaway sqlite file."""
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "costs.sqlite")
    return store


# --------------------------------------------------------------------------- #
# Mapping
# --------------------------------------------------------------------------- #
def test_mapping_exact(cost_config):
    assert mapping.resolve("gcp", "example-shop-prod") == "example-shop"
    assert mapping.resolve("claude", "ExampleShop") == "example-shop"
    assert mapping.resolve("stripe", "acct_example-shop") == "example-shop"


def test_mapping_prod_strip_heuristic(cost_config):
    # example-news-prod is in the explicit table; example-blog-staging is NOT — it
    # resolves only via the heuristic (strip -staging -> 'example-blog' slug).
    assert mapping.resolve("gcp", "example-news-prod") == "example-news"
    assert mapping.resolve("gcp", "example-blog-staging") == "example-blog"


def test_mapping_alias_case_insensitive(cost_config):
    # 'shop' is an alias of example-shop; 'NEWS' an alias of example-news
    assert mapping.resolve("gcp", "shop") == "example-shop"
    assert mapping.resolve("gcp", "NEWS") == "example-news"


def test_mapping_unmapped_fallback(cost_config):
    assert mapping.resolve("gcp", "totally-unknown-thing") == "unmapped"
    assert mapping.resolve("openai", "proj_unknown_x") == "unmapped"
    assert mapping.resolve("gcp", None) == "unmapped"


def test_mapping_bad_target_warning(monkeypatch, cost_config):
    from agentos.core import config
    monkeypatch.setattr(config, "cost_sources", lambda: {
        "mappings": {"gcp": {"foo-prod": "foo", "example-shop-prod": "example-shop"}},
        "unmapped_bucket": "unmapped",
    })
    warns = mapping.validate_mappings()
    assert any("gcp:foo-prod -> 'foo' is not a registry slug" in w for w in warns)
    # the valid mapping does not warn
    assert not any("example-shop" in w for w in warns)


# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #
def test_gcp_loader_sums_credits_and_groups_by_month(cost_config):
    records = gcp.load(GCP_CSV)
    # example-shop Cloud Run May: 30-5 + 12 = 37.00 (two rows, same month)
    shop_run_may = next(
        r for r in records
        if r["native_id"] == "example-shop-prod" and r["service"] == "Cloud Run"
        and r["period"] == "2026-05-01"
    )
    assert shop_run_may["amount_usd"] == pytest.approx(37.0)
    # example-shop Cloud Run April is a SEPARATE group (different month)
    shop_run_apr = next(
        r for r in records
        if r["native_id"] == "example-shop-prod" and r["service"] == "Cloud Run"
        and r["period"] == "2026-04-01"
    )
    assert shop_run_apr["amount_usd"] == pytest.approx(18.0)
    # all gcp records tagged source=gcp, provenance billing-csv, period month-start
    assert all(r["source"] == "gcp" for r in records)
    assert all(r["provenance"] == "billing-csv" for r in records)
    assert all(r["period"].endswith("-01") for r in records)


def test_gcp_loader_maps_unmapped(cost_config):
    records = gcp.load(GCP_CSV)
    mystery = [r for r in records if r["native_id"] == "mystery-app-prod"]
    assert mystery
    assert all(r["project"] == "unmapped" for r in mystery)


def test_gcp_firebase_service_preserved(cost_config):
    records = gcp.load(GCP_CSV)
    fb = next(r for r in records if r["service"] == "Firebase Hosting")
    assert fb["source"] == "gcp"             # billed under GCP
    assert fb["project"] == "example-shop"   # but service name preserved


def test_thirdparty_loader_parses(cost_config):
    records = thirdparty.load(MANUAL_CSV)
    by_native = {r["native_id"]: r for r in records}
    assert by_native["acct_example-shop"]["project"] == "example-shop"
    assert by_native["acct_example-shop"]["source"] == "stripe"
    assert by_native["acct_example-shop"]["amount_usd"] == pytest.approx(12.40)
    assert by_native["proj_news"]["project"] == "example-news"
    assert by_native["proj_unknown_x"]["project"] == "unmapped"
    assert all(r["provenance"] == "manual-entry" for r in records)


def test_claude_loader_labels_api_equiv(cost_config):
    agg = {
        "by_project": [
            {"name": "ExampleShop", "cost_usd": 18.2},
            {"name": "ExampleNews", "cost_usd": 6.0},
            {"name": "RandomThing", "cost_usd": 1.0},
        ],
        "by_day": [{"day": "2026-05-01"}, {"day": "2026-05-15"}],
    }
    records = claude.load(agg=agg)
    assert all(r["service"] == "claude (api-equiv)" for r in records)
    assert all(r["source"] == "claude" for r in records)
    assert all(r["period"] == "2026-05-15" for r in records)  # latest day
    by_proj = {r["native_id"]: r["project"] for r in records}
    assert by_proj["ExampleShop"] == "example-shop"
    assert by_proj["RandomThing"] == "unmapped"


# --------------------------------------------------------------------------- #
# ingest_all + aggregator reconciliation
# --------------------------------------------------------------------------- #
@pytest.fixture
def seeded(temp_store, cost_config):
    claude_agg = {
        "by_project": [
            {"name": "ExampleShop", "cost_usd": 18.2},
            {"name": "ExampleNews", "cost_usd": 6.0},
        ],
        "by_day": [{"day": "2026-05-10"}],
    }
    loaders.ingest_all(
        gcp_csv=GCP_CSV, manual_csv=MANUAL_CSV,
        claude_agg=claude_agg, include_claude=True,
    )
    return temp_store


def test_aggregate_totals_reconcile(seeded):
    agg = aggregator.aggregate()
    t = agg["totals"]

    # by_source sums reconcile to grand total
    src_sum = round(sum(s["amount_usd"] for s in agg["by_source"]), 2)
    assert src_sum == pytest.approx(t["amount_usd"])

    # by_project sums reconcile to grand total
    proj_sum = round(sum(p["amount_usd"] for p in agg["by_project"]), 2)
    assert proj_sum == pytest.approx(t["amount_usd"])

    # by_service sums reconcile
    svc_sum = round(sum(s["amount_usd"] for s in agg["by_service"]), 2)
    assert svc_sum == pytest.approx(t["amount_usd"])

    # by_month sums reconcile
    month_sum = round(sum(m["amount_usd"] for m in agg["by_month"]), 2)
    assert month_sum == pytest.approx(t["amount_usd"])

    assert t["records"] == seeded.count()
    assert t["currency"] == "USD"


def test_aggregate_unmapped(seeded):
    agg = aggregator.aggregate()
    # unmapped = gcp mystery-app (9.0 + 1.10) + openai proj_unknown_x (4.0) = 14.10
    assert agg["totals"]["unmapped_usd"] == pytest.approx(14.10)
    unmapped = next(p for p in agg["by_project"] if p["name"] == "unmapped")
    assert unmapped["amount_usd"] == pytest.approx(14.10)
    # native_ids exposed for the unmapped entry
    assert set(unmapped["native_ids"]) == {"mystery-app-prod", "proj_unknown_x"}


def test_aggregate_by_source_split_on_project(seeded):
    agg = aggregator.aggregate()
    shop = next(p for p in agg["by_project"] if p["name"] == "example-shop")
    # example-shop has gcp + claude + stripe
    assert "gcp" in shop["by_source"]
    assert "claude" in shop["by_source"]
    assert "stripe" in shop["by_source"]
    assert shop["by_source"]["claude"] == pytest.approx(18.2)
    assert shop["by_source"]["stripe"] == pytest.approx(12.40)


def test_aggregate_passed_records():
    """records=None reads store; passing a list aggregates that list (no store)."""
    recs = [
        {"project": "example-shop", "source": "gcp", "service": "Cloud Run",
         "period": "2026-05-01", "amount_usd": 10.0},
        {"project": "unmapped", "source": "openai", "service": "gpt-4o",
         "period": "2026-05-01", "amount_usd": 2.5, "native_id": "proj_x"},
    ]
    agg = aggregator.aggregate(recs)
    assert agg["totals"]["amount_usd"] == pytest.approx(12.5)
    assert agg["totals"]["unmapped_usd"] == pytest.approx(2.5)
    assert agg["totals"]["records"] == 2


def test_aggregate_contract_keys(seeded):
    agg = aggregator.aggregate()
    assert set(agg.keys()) == {
        "totals", "by_project", "by_source", "by_service", "by_month",
        "warnings", "meta",
    }
    assert set(agg["totals"].keys()) == {
        "amount_usd", "records", "unmapped_usd", "currency",
        "period_start", "period_end",
    }
    assert "sources_loaded" in agg["meta"]
    assert "generated_at" in agg["meta"]


# --------------------------------------------------------------------------- #
# Store idempotency
# --------------------------------------------------------------------------- #
def test_replace_source_idempotent(temp_store, cost_config):
    claude_agg = {
        "by_project": [{"name": "ExampleShop", "cost_usd": 18.2}],
        "by_day": [{"day": "2026-05-10"}],
    }
    loaders.ingest_all(gcp_csv=GCP_CSV, manual_csv=MANUAL_CSV, claude_agg=claude_agg)
    first_total = aggregator.aggregate()["totals"]["amount_usd"]
    first_count = temp_store.count()

    # load the SAME data again — totals + count must not change
    loaders.ingest_all(gcp_csv=GCP_CSV, manual_csv=MANUAL_CSV, claude_agg=claude_agg)
    second_total = aggregator.aggregate()["totals"]["amount_usd"]
    second_count = temp_store.count()

    assert second_total == pytest.approx(first_total)
    assert second_count == first_count


def test_replace_source_scoped_to_period_range(temp_store):
    """Re-loading one source only replaces rows in that batch's period range."""
    store.replace_source("gcp", [
        {"project": "example-shop", "source": "gcp", "service": "Cloud Run",
         "period": "2026-04-01", "amount_usd": 5.0, "provenance": "billing-csv"},
    ])
    store.replace_source("gcp", [
        {"project": "example-shop", "source": "gcp", "service": "Cloud Run",
         "period": "2026-05-01", "amount_usd": 7.0, "provenance": "billing-csv"},
    ])
    # both months survive (different period ranges)
    assert temp_store.count() == 2
    # re-load only May -> April untouched, May replaced
    store.replace_source("gcp", [
        {"project": "example-shop", "source": "gcp", "service": "Cloud Run",
         "period": "2026-05-01", "amount_usd": 9.0, "provenance": "billing-csv"},
    ])
    agg = aggregator.aggregate()
    assert agg["totals"]["amount_usd"] == pytest.approx(14.0)  # 5 (Apr) + 9 (May)
