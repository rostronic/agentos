"""Phase-2 LIVE GCP/Firebase connector — Cloud Billing BigQuery export -> Cost tab.

This is the "input-adapter swap" the cost plan calls Phase 2: it emits the SAME
`source="gcp"` CostRecords as loaders/gcp.py (which reads the seeded CSV), but pulls
REAL cost from the BigQuery billing export. Firebase bills through the same GCP account,
so this one pull covers Hosting, Cloud Run, Firestore, Storage, and the Gemini API.
Running it REPLACES the `gcp` source in the store, so the Cost tab shows live per-project
spend. It does NOT touch Claude/LLM cost (that's the Token Usage tab).

Project mapping reuses config/cost-sources.yaml `mappings.gcp` (unmapped GCP projects fall
to the unmapped bucket, never dropped). Connection config: config/gcp_billing.yaml.

Run (after the billing export is on + config filled — see the mission-control task):
    python -m agentos.cost_analytics.connectors.gcp_billing --dry-run   # print SQL, query nothing
    python -m agentos.cost_analytics.connectors.gcp_billing             # sync now (cron daily)
Needs: pip install google-cloud-bigquery  +  a service-account key (BigQuery Data Viewer + Job User).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from agentos.core.config import AGENTOS_ROOT
from agentos.cost_analytics import mapping, store

CONFIG_PATH = AGENTOS_ROOT / "config" / "gcp_billing.yaml"

# Net cost = gross cost + credits (credits stored negative), grouped per project+service+month.
QUERY_TEMPLATE = """\
SELECT
  project.id AS gcp_project,
  service.description AS service,
  invoice.month AS invoice_month,
  ROUND(SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)), 4) AS net_cost_usd,
  ANY_VALUE(currency) AS currency
FROM `{table_fqn}`
WHERE invoice.month >= @since_month
GROUP BY gcp_project, service, invoice_month
HAVING net_cost_usd > 0.005
ORDER BY invoice_month DESC, net_cost_usd DESC
"""


def _since_month(lookback_months: int) -> str:
    """'YYYYMM' string `lookback_months` before the current month."""
    now = datetime.now(timezone.utc)
    total = now.year * 12 + (now.month - 1) - lookback_months
    y, m = divmod(total, 12)
    return f"{y:04d}{m + 1:02d}"


def _table_fqn(exp: dict) -> str:
    return f"{exp['bq_project']}.{exp['dataset']}.{exp['table']}"


def rows_to_records(rows, *, table: str = "bigquery") -> list[dict]:
    """Map BigQuery result rows -> source='gcp' CostRecords (same shape as loaders/gcp.py).

    Pure + testable: pass fake row dicts with keys
    {gcp_project, service, invoice_month ('YYYYMM'), net_cost_usd, currency}.
    """
    out: list[dict] = []
    for r in rows:
        native = (r.get("gcp_project") or "").strip()
        month = str(r.get("invoice_month") or "")
        period = f"{month[:4]}-{month[4:6]}-01" if len(month) == 6 else "1970-01-01"
        amount = round(float(r.get("net_cost_usd") or 0.0), 6)
        out.append({
            "project": mapping.resolve("gcp", native),
            "source": "gcp",
            "service": (r.get("service") or "unknown").strip() or "unknown",
            "period": period,
            "amount_usd": amount,
            "currency": (r.get("currency") or "USD"),
            "amount_native": amount,
            "provenance": "billing-bigquery",
            "billing_account": None,
            "native_id": native,
            "raw_ref": f"bq:{table}:{native}:{r.get('service')}:{month}",
        })
    return out


def _query_rows(exp: dict, since_month: str) -> list[dict]:
    try:
        from google.cloud import bigquery
        from google.oauth2 import service_account
    except ImportError:
        sys.exit("google-cloud-bigquery not installed. Run: pip install google-cloud-bigquery")
    key = Path(str(exp["credentials_path"])).expanduser()
    if not key.exists():
        sys.exit(f"Service-account key not found: {key}")
    creds = service_account.Credentials.from_service_account_file(str(key))
    client = bigquery.Client(project=exp["bq_project"], credentials=creds)
    job = client.query(
        QUERY_TEMPLATE.format(table_fqn=_table_fqn(exp)),
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("since_month", "STRING", since_month)]),
    )
    return [dict(r) for r in job.result()]


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        sys.exit(f"Missing {CONFIG_PATH}. Fill it in (see the mission-control task) and retry.")
    cfg = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    if not cfg.get("billing_exports"):
        sys.exit(f"No billing_exports configured in {CONFIG_PATH}.")
    return cfg


def ingest() -> int:
    """Query every configured export and REPLACE the 'gcp' source in the store."""
    cfg = load_config()
    since = _since_month(int(cfg.get("lookback_months", 6)))
    records: list[dict] = []
    for exp in cfg["billing_exports"]:
        records += rows_to_records(_query_rows(exp, since), table=exp["table"])
    return store.replace_source("gcp", records)


def main() -> int:
    ap = argparse.ArgumentParser(description="Sync live GCP billing cost into the Cost tab.")
    ap.add_argument("--dry-run", action="store_true", help="Print the SQL; query nothing.")
    args = ap.parse_args()
    cfg = load_config()
    since = _since_month(int(cfg.get("lookback_months", 6)))
    if args.dry_run:
        print(f"since_month >= {since}   (lookback {cfg.get('lookback_months', 6)} months)")
        for exp in cfg["billing_exports"]:
            print(f"\n-- {_table_fqn(exp)}\n{QUERY_TEMPLATE.format(table_fqn=_table_fqn(exp))}")
        return 0
    n = ingest()
    print(f"Synced {n} GCP cost records into the store (source=gcp).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
