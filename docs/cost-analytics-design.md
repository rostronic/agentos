# Cost Analytics — Cross-Source Cost Tracking Design

Design note for the **Cost** subsystem and dashboard tab. It explains how AgentOS
attributes **total real-dollar cost per project across every spend source** and what
the build's data model, ingestion, mapping, and aggregation contracts look like.

All project/account identifiers below are placeholders (`example-business`,
`example-business-prod`, `acct_example`, …). Map your own in
`config/cost-sources.yaml` (see `config/cost-sources.yaml.example`).

## Goal

Track **total real-dollar cost per project across every spend source** — not just
Claude. The existing "Token usage" view (`/api/tokens`) reports Claude
**API-equivalent** cost (what pay-per-token *would* have cost; on a flat-fee plan you
pay a subscription instead, so it's a "value of subscription" number, not money out
the door). This subsystem reports **actual money spent**, summed across:

- **Claude** API/transcript usage (folded in from the existing aggregator)
- **Google Cloud** (Cloud Run, Scheduler, Firestore, Artifact Registry, Logging…)
- **Firebase** (rolls up under the same GCP billing account)
- **Third-party APIs** (Stripe fees, OpenAI, ElevenLabs, YouTube, etc.)

The deliverable is one number per project — "what did `example-business` actually cost
me last month, and where did it go" — plus source/service/month breakdowns and an
explicit "unmapped" bucket so nothing is silently dropped.

**Design constraint for development:** the build runs in an ephemeral container with
**no live GCP/Firebase/Stripe credentials**. The MVP MUST run entirely from
**seedable local data** (a CSV/JSON dropped in, plus the existing local Claude
transcripts). Live cloud pulls are Phase 2+, behind the same loader interface so the
aggregator and dashboard never know the difference.

---

## Data model

### One normalized cost record

Every source is flattened to the same shape — a **CostRecord**. One record = one line
of spend (one service, one project, one period). Fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `project` | str | yes | registry slug (e.g. `example-business`) **or** `"unmapped"` — never null |
| `source` | str | yes | top-level origin: `claude`, `gcp`, `firebase`, `stripe`, `openai`, `elevenlabs`, `manual` |
| `service` | str | yes | sub-service within the source: e.g. `Cloud Run`, `Firestore`, `claude-opus-4-8`, `stripe-fee` |
| `period` | str | yes | the cost's day, `YYYY-MM-DD` (use the 1st of the month for monthly-granularity sources) |
| `amount_usd` | float | yes | cost in USD, already FX-converted |
| `currency` | str | yes | original currency before conversion (`USD` default); keep for provenance |
| `amount_native` | float | no | original amount before FX (== `amount_usd` when `currency==USD`) |
| `provenance` | str | yes | how we know: `transcript`, `bigquery-export`, `billing-csv`, `manual-entry`, `connector:<name>` |
| `billing_account` | str | no | GCP billing account id / Stripe account / etc. |
| `native_id` | str | no | the source's native project identifier *before* mapping (e.g. `example-business-prod`, `acct_example`). Kept so mapping is auditable. |
| `labels` | dict | no | passthrough of source labels (GCP resource labels, Stripe metadata) — future mapping signal |
| `raw_ref` | str | no | pointer back to the raw row (file + line, or export row id) for audit |

**Design rule:** records are append-only and **immutable**. Re-ingesting a source
**replaces all records for that `(source, period-range)`** rather than mutating rows,
so re-running a load is idempotent.

### Where it lives — decision

**SQLite: `orchestrator/runtime/costs.sqlite`, one table `cost_records`.**

This matches the existing convention exactly — `run_store.py` already uses
`RUNTIME_DIR = <agentos>/orchestrator/runtime` with `runs.sqlite`. Rationale vs the
alternatives:

- **Not JSONL/`cost/` data dir:** aggregation needs `GROUP BY project, source, service,
  month` and idempotent "delete + reload one source". SQL does both in a few lines;
  JSONL forces a full in-memory scan + manual dedup on every query and every re-load.
- **Not the existing `runs.sqlite`:** keep cost ingestion decoupled from run history —
  different lifecycle, different test fixtures, independently wipeable. A separate file
  is cheap.
- The **seed data the dev loads is CSV/JSON** (see ingestion); the *store* is SQLite.
  Loaders parse seed files → upsert into `cost_records`.

Table DDL (illustrative — finalize during the build):

```
cost_records(
  id INTEGER PRIMARY KEY,
  project TEXT NOT NULL,
  source TEXT NOT NULL,
  service TEXT NOT NULL,
  period TEXT NOT NULL,          -- YYYY-MM-DD
  amount_usd REAL NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  amount_native REAL,
  provenance TEXT NOT NULL,
  billing_account TEXT,
  native_id TEXT,
  labels TEXT,                   -- JSON string
  raw_ref TEXT
);
-- index on (project), (source), (period) for the GROUP BYs.
-- a load batch is keyed by (source, min(period)..max(period)); reload = DELETE then INSERT.
```

Proposed module layout (mirrors `token_analytics/`):

```
orchestrator/agentos/cost_analytics/
  store.py        # sqlite open/init, upsert_records(), replace_source(), query helpers
  loaders/
    claude.py     # folds aggregator.aggregate() by_project -> CostRecords
    gcp.py        # parses GCP billing export CSV/JSON -> CostRecords
    thirdparty.py # parses a generic manual/CSV cost file -> CostRecords
  mapping.py      # loads config/cost-sources.yaml, native_id -> slug resolution
  aggregator.py   # aggregate() -> the dashboard contract dict (below)
```

---

## Sources & ingestion

Each loader returns `list[CostRecord]` (plain dicts). A top-level `ingest_all()` calls
every loader and writes via `store.replace_source(source, records)`. All loaders accept
an explicit path/data argument so tests pass seeded fixtures (never touch live `~`/cloud).

### 1. Claude (reuse existing aggregator)

`token_analytics.aggregator.aggregate()` already returns `by_project` — a list of
`{name, cost_usd, input, output, ...}`. The Claude loader:

1. Calls `aggregate()` (or accepts a pre-computed agg dict in tests).
2. For each `by_project` row, emits **one CostRecord per project** (MVP granularity:
   one row per project for the whole window) with:
   `source="claude"`, `service="claude"` (or split per model using `by_model` later),
   `native_id = row["name"]` (the cwd-derived name, e.g. `ExampleBusiness`),
   `project = mapping.resolve("claude", native_id)`, `amount_usd = row["cost_usd"]`,
   `provenance="transcript"`, `period` = the latest day in `agg["by_day"]` (or month
   bucket).
3. **Important caveat to surface in the UI:** Claude `cost_usd` is API-equivalent. If
   you are on a flat-fee subscription, the *real* Claude cost is the monthly fee, not
   this number. The design's honest stance: in Phase 1 we ingest the API-equivalent
   number and **label it as such** in the breakdown (`service="claude (api-equiv)"`).
   Phase 2 adds a "subscription mode" that substitutes the flat fee allocated across
   projects by usage share. Do not silently present API-equiv as money-out.

### 2. Google Cloud + Firebase (one source — Firebase bills under GCP)

Firebase spend rolls up under the project's GCP **billing account**, so there is **one
ingestion path** for both. The realistic, industry-standard mechanism is the **GCP
Cloud Billing export** — either the **BigQuery export** or the **billing CSV export**.
Both share the same essential schema:

| Export column | CostRecord field |
|---|---|
| `service.description` (e.g. "Cloud Run", "Cloud Firestore") | `service` |
| `project.id` (e.g. `example-business-prod`, `example-site-prod`) | `native_id` → mapped to `project` |
| `cost` (+ `credits[].amount`) | `amount_usd` (sum cost + credits; credits are negative) |
| `usage_start_time` / `usage_end_time` | `period` (date of `usage_start_time`) |
| `currency` | `currency` |
| `labels[]` (key/value resource labels) | `labels` |
| `billing_account_id` | `billing_account` |

**MVP ingestion (offline):** the dev loads a **seeded CSV or newline-delimited JSON**
that mirrors this schema (a small hand-authored fixture representing one month of
`example-business-prod` + `example-site-prod` + one unmapped project). The loader:

1. Reads the file (CSV via `csv.DictReader`, or JSONL).
2. Groups rows by `(project.id, service.description, month)` and sums `cost +
   sum(credits)`.
3. Emits one CostRecord per group, `source="gcp"`, `provenance="billing-csv"` (or
   `"bigquery-export"`), `native_id = project.id`.
4. Firebase-specific SKUs (e.g. "Firebase Hosting") arrive in the **same export** under
   their own `service.description`; we tag `source="gcp"` but the `service` string
   preserves the Firebase name, so the UI can still see Firebase spend distinctly.

**Phase 2 (live):** a `connectors/gcp_billing.py` that runs a BigQuery query against
the billing export table and produces the *same* row dicts the offline loader consumes
— so only the *input adapter* changes, not the loader/aggregator. Gated on real GCP
creds (configure via `config/gcp_billing.yaml`; see `config/gcp_billing.yaml.example`).

### 3. Third-party APIs (Stripe, OpenAI, ElevenLabs, YouTube…)

These have no unified export. **MVP = manual / CSV entry.** A single generic
**`config/cost-manual.csv`** (or `.../cost-manual/*.csv`) the user/dev fills in:

```
source,service,native_id,period,amount_usd,currency,note
stripe,stripe-fee,acct_example,2026-05-01,12.40,USD,May processing fees
openai,gpt-4o,proj_example,2026-05-01,3.10,USD,enrichment
elevenlabs,tts,acct_example_voice,2026-05-01,5.00,USD,briefing voice
```

The `thirdparty.py` loader reads this straight into CostRecords (`provenance=
"manual-entry"`). `native_id` is mapped via the same mapping table. This gives full
cross-source coverage on day one with zero credentials.

**Phase 2+:** named connectors (`connectors/stripe.py` via Balance Transactions API,
`connectors/openai.py` via usage API) that emit the same CostRecord dicts. Aligns with
the **connector framework** on the capability roadmap and the **business-cockpit ETL**
phase.

---

## Project mapping / reconciliation (the hard part)

Each source names projects differently and **none** of them natively use the registry
slug:

- **Claude:** cwd-derived names like `ExampleBusiness`, `ExampleSite` (see
  `jsonl_parser._project_from_cwd`) — mixed case, no hyphen.
- **GCP:** project ids like `example-business-prod`, `example-site-prod`.
- **Third-party:** account-level ids (`acct_…`, `proj_…`) with no inherent link.

### Mapping table: `config/cost-sources.yaml`

A config file, loaded like the others via `core/config.py` (a `cost_sources()` loader
mirroring `projects()`). Shape:

```yaml
# native source identifier -> registry slug (from config/projects.yaml)
mappings:
  claude:
    ExampleBusiness: example-business
    example-business: example-business
    ExampleSite: example-site
  gcp:
    example-business-prod: example-business
    example-site-prod: example-site
  stripe:
    acct_example: example-business
  openai:
    proj_example: example-site
  elevenlabs:
    acct_example_voice: daily-briefing

# fall back to this slug when no mapping matches; NEVER drop the row.
unmapped_bucket: unmapped
```

### Resolution rules (`mapping.resolve(source, native_id)`)

1. Exact match in `mappings[source][native_id]` → that slug.
2. **Fallback heuristics** (so new GCP projects don't silently fall into "unmapped"):
   - strip a trailing `-prod` / `-dev` / `-staging` from a GCP id and lowercase →
     check against registry slugs and their `aliases` (from `projects.yaml`).
   - case-insensitive match of the native id against each registry slug + aliases.
3. No match → return `unmapped_bucket` (default `"unmapped"`), and the loader keeps
   `native_id` on the record so the UI can show *what* is unmapped and the user can add
   a mapping line.

### Reconciliation against `config/projects.yaml`

- On load, every resolved slug (except `unmapped`) is validated against
  `config.projects()` keys. A mapping pointing at a non-existent slug is a **config
  error** surfaced in the API response (`warnings: [...]`), not a silent pass — this
  catches typos in the mapping file.
- The "unmapped" total is a **first-class output**, never hidden. It is the user's
  to-do list: "$X of spend isn't attributed — add a mapping."

---

## Aggregation contract (exact dict shape)

`cost_analytics.aggregator.aggregate(records=None)` mirrors
`token_analytics.aggregator.aggregate()`: if `records is None` it reads from the store;
otherwise it aggregates the passed list (for tests). It returns **exactly** this dict
— the build and the dashboard MUST agree on these keys:

```python
{
  "totals": {
    "amount_usd": 142.87,        # grand total, all sources, all projects
    "records": 37,               # number of cost_records aggregated
    "unmapped_usd": 8.10,        # subset of amount_usd with project == "unmapped"
    "currency": "USD",
    "period_start": "2026-05-01",
    "period_end": "2026-05-31",
  },
  "by_project": [                # sorted by amount_usd desc; includes "unmapped"
    { "name": "example-business", "amount_usd": 74.20,
      # per-source split so the dashboard can render a stacked bar with no extra call:
      "by_source": { "gcp": 51.0, "claude": 18.2, "stripe": 5.0 },
      "records": 14 },
    { "name": "example-site",  "amount_usd": 60.57, "by_source": {...}, "records": 19 },
    { "name": "unmapped",  "amount_usd": 8.10,  "by_source": {"openai": 8.10},
      # unmapped rows additionally expose native ids so the user knows what to map:
      "native_ids": ["proj_unknown_x"], "records": 4 }
  ],
  "by_source": [                 # sorted by amount_usd desc
    { "name": "gcp",    "amount_usd": 95.0,  "records": 20 },
    { "name": "claude", "amount_usd": 36.4,  "records": 8 },
    { "name": "stripe", "amount_usd": 11.47, "records": 9 }
  ],
  "by_service": [                # top services across all sources, sorted desc
    { "name": "Cloud Run",  "source": "gcp",    "amount_usd": 60.0 },
    { "name": "Firestore",  "source": "gcp",    "amount_usd": 25.0 },
    { "name": "claude (api-equiv)", "source": "claude", "amount_usd": 36.4 }
  ],
  "by_month": [                  # sorted ascending by month; for the trend chart
    { "month": "2026-04", "amount_usd": 120.10 },
    { "month": "2026-05", "amount_usd": 142.87 }
  ],
  "warnings": [                  # mapping/config issues; [] when clean
    "mapping gcp:foo-prod -> 'foo' is not a registry slug"
  ],
  "meta": {
    "sources_loaded": ["claude", "gcp", "stripe", "openai"],
    "generated_at": "2026-06-15T00:00:00Z"
  }
}
```

Notes:
- Amounts rounded to 2 decimals at the API boundary (store full precision internally).
- `by_project[].by_source` is the key enabler for the stacked-bar UI — pre-computed
  server-side so the front-end stays dumb (matches how `token_analytics` pre-shapes
  `by_project`/`by_model`).
- `unmapped` is always present as a `by_project` entry **iff** unmapped spend > 0.

---

## API + dashboard surface

### API: `GET /api/costs`

Add to `orchestrator/agentos/entrypoints/api_server.py` using the existing
`routes = web.RouteTableDef()` pattern and the `asyncio.to_thread` wrapper (sqlite read
is blocking), exactly like `/api/tokens`:

```python
@routes.get("/api/costs")
async def costs(request):
    """Total real-dollar cost per project across all spend sources.

    Unlike /api/tokens (Claude API-EQUIVALENT, a 'value of subscription' number),
    this is actual money out: Claude + GCP/Firebase + third-party, attributed to
    registry-slug projects, with an explicit 'unmapped' bucket.
    """
    from agentos.cost_analytics import aggregator
    agg = await asyncio.to_thread(aggregator.aggregate)
    return _json(agg)
```

(Optional `?source=` / `?month=` query filters are Phase 2.)

### Dashboard: new "Cost" view under the **Insights** nav group

In `dashboard/index.html`:

1. **Nav link** — add under the existing `Insights` group (after "Token usage"):
   `<a data-view="costs">Cost</a>`.
2. **Router** — `costs` needs no arg, so the existing `else main.innerHTML = await
   views[view]()` branch covers it; just add a `views.costs()` async fn.
3. **`views.costs()`** fetches `/api/costs` and renders, reusing existing CSS
   (`.kpis/.kpi`, `.card`, `.bar-row/.bar-track/.bar-fill` via the `tkBars` helper
   pattern, `.tk` table styling, `tkfmt.usd`):
   - **KPI row:** Total cost, # sources, Unmapped $ (red if > 0), This-month total.
   - **Per-project total stacked by source:** a horizontal bar per project where the
     bar segments are colored per source (use `by_project[].by_source`). MVP-simple
     fallback: a table `Project | total | gcp | claude | stripe | …` with a single
     bar of total — stacking is a nice-to-have, the table is the must-have.
   - **Source breakdown:** `tkBars(by_source, …)` — one bar per source.
   - **Monthly trend:** `tkBars(by_month, m=>m.amount_usd, m=>m.month, …)` — same shape
     as the existing "Daily API-equivalent cost" chart.
   - **Unmapped callout:** if `totals.unmapped_usd > 0`, a yellow `.card` listing the
     `unmapped` project's `native_ids` with copy "Add these to
     `config/cost-sources.yaml` to attribute this spend."
   - **`warnings`:** render any as a red note at top.
4. **Framing copy** (the `.sub`): "Actual money spent per project — Claude + Google
   Cloud/Firebase + third-party APIs. Distinct from **Token usage**, which shows
   Claude *API-equivalent* cost (a subscription-value estimate, not money out)."

### How it differs from "Token usage" — state it in the UI

| | Token usage (`/api/tokens`) | Cost (`/api/costs`) |
|---|---|---|
| Money or value | API-**equivalent** (subscription value) | **Actual dollars** spent |
| Scope | Claude only | Claude + GCP/Firebase + third-party |
| Granularity | tokens, models, cache | sources, services, projects, months |

---

## MVP scope — build checklist (Phase 1, one pass, seeded local data)

Build these in order. Everything works from fixtures; **no live cloud/Stripe calls.**

- [ ] **1. Store** — `cost_analytics/store.py`: open/init `orchestrator/runtime/
      costs.sqlite` with the `cost_records` table; `replace_source(source, records)`
      (DELETE source rows in the batch's period range, then INSERT); query helpers used
      by the aggregator. Module-level `DB_PATH` overridable in tests (mirror
      `run_store.DB_PATH` / `jsonl_parser.CACHE_FILE` monkeypatch pattern).
- [ ] **2. Mapping config + resolver** — create `config/cost-sources.yaml` (with your
      `*-prod`→slug mappings + `unmapped_bucket`); add `config.cost_sources()` loader in
      `core/config.py`; `cost_analytics/mapping.py` `resolve(source, native_id)` with
      exact → heuristic (`-prod` strip, alias/case-insensitive against `projects.yaml`)
      → unmapped fallback; emit `warnings` for mappings whose target isn't a registry
      slug.
- [ ] **3. Loaders** —
      - `loaders/claude.py`: fold `token_analytics.aggregator.aggregate()` `by_project`
        into CostRecords (label service "claude (api-equiv)").
      - `loaders/gcp.py`: parse a seeded GCP billing-export CSV/JSONL → CostRecords
        (sum cost + credits, group by project/service/month).
      - `loaders/thirdparty.py`: parse `config/cost-manual.csv` → CostRecords.
      - `ingest_all(...)` that runs all three and writes via the store; accepts
        explicit fixture paths.
- [ ] **4. Seed fixtures** — commit a tiny `orchestrator/agentos/tests/fixtures/`
      GCP billing CSV (covering `example-business-prod`, `example-site-prod`, one
      **unmapped** project) and a manual-cost CSV. (Also drop a sample
      `config/cost-manual.csv` so the real dashboard has data.)
- [ ] **5. Aggregator** — `cost_analytics/aggregator.py::aggregate(records=None)`
      returning the **exact dict shape** above (`totals` incl. `unmapped_usd`,
      `by_project` w/ `by_source`, `by_source`, `by_service`, `by_month`, `warnings`,
      `meta`).
- [ ] **6. API route** — add `GET /api/costs` to `api_server.py` (pattern of
      `/api/tokens`, `asyncio.to_thread`).
- [ ] **7. Dashboard view** — `Cost` nav link under Insights + `views.costs()` in
      `dashboard/index.html` (KPIs, per-project table/stacked bar, source breakdown,
      monthly trend, unmapped callout, framing copy). Reuse `tkBars`, `tkfmt`, `.tk`
      styles.
- [ ] **8. Tests** — `tests/test_cost_analytics.py` (mirror `test_token_analytics.py`):
      - mapping: exact, `-prod`-strip heuristic, alias, → unmapped; bad-mapping warning.
      - loaders: GCP CSV parse sums credits, groups by month; manual CSV parse.
      - aggregator on seeded records: `totals.amount_usd` correct,
        `totals.unmapped_usd` correct, `by_project` includes `unmapped`, `by_source`/
        `by_service`/`by_month` sums reconcile to the total.
      - store: `replace_source` is idempotent (load twice → same totals).
      - API: add a `/api/costs` case to `test_api_server.py` asserting `totals` and
        `by_project` keys exist (use seeded store).

**MVP done =** `agentos serve` → dashboard "Cost" view shows per-project total cost,
source breakdown, monthly trend, and an unmapped bucket, all from seeded local files,
with green tests.

---

## Phased roadmap

**Phase 2 — live GCP/Firebase connector.** `connectors/gcp_billing.py` runs a BigQuery
query against the Cloud Billing export table (or reads the GCS-exported CSV) and emits
the *same* CostRecord dicts the offline loader consumes — only the input adapter
changes. Gated on real GCP creds + `config/gcp_billing.yaml`. Add a daily `agentos
cron` job to refresh the store. (See `docs/gcp-deployment-cookbook.md` for the GCP
project layout and the small monthly infra baseline this will surface.)

**Phase 2 — Claude subscription mode.** Replace API-equivalent Claude cost with the
real flat subscription fee, allocated across projects by usage share; toggle in the
Cost view (parallels the existing Token-usage "Settings/plan" tab).

**Phase 3 — named third-party connectors.** `connectors/stripe.py` (Balance
Transactions → fees), `connectors/openai.py`, `connectors/elevenlabs.py`,
`connectors/youtube.py`, behind the common connector interface. This is the
capability-roadmap **connector framework** feeding the **business-cockpit ETL** phase.

**Phase 3 — budgets & alerts per project.** Extend `config/budgets.yaml` /
`budget_for_project()` with monthly cost caps per project; surface "over budget" pills
in the Cost view; notify (Telegram/notifier) on breach.

**Phase 3 — per-project P&L / ROI.** Join cost (this subsystem) with revenue for true
per-project P&L — the explicit payoff of the **business-cockpit ETL** phase ("cost
attribution + per-project P&L"). The CostRecord schema is deliberately
revenue-shaped-compatible: a future `RevenueRecord` with the same
`project/source/period/amount_usd` shape nets directly against costs.

---

## Open questions / assumptions

1. **Flat-fee Claude → API-equiv is not money-out.** MVP ingests + clearly **labels**
   the API-equivalent number; "subscription mode" (real fee allocated by usage) is
   Phase 2. Assumption: it's better to show a labeled estimate than to omit Claude from
   "total cost." Confirm, or exclude Claude from the dollar total until Phase 2.
2. **Cost granularity = monthly** for GCP/third-party (billing exports are
   daily-resolution but most third-party entry is monthly). The schema stores a
   `period` date so we *can* go daily; the MVP UI trends by month.
3. **FX.** Assumes everything is USD in the MVP (the `currency`/`amount_native` fields
   exist for later). If a source bills in another currency, the manual CSV author
   pre-converts for now.
4. **What counts as "a project's cost"?** Shared infra (platform tooling,
   billing-account-level charges with no project label) — proposal: such GCP rows map
   to a `platform`/`shared` slug if labeled, else `unmapped`. Decide whether you want a
   dedicated `shared`/`platform` bucket instead of `unmapped` for these.
5. **Credits/refunds** are negative `amount_usd` records (GCP credits, Stripe refunds).
   Totals net them out. Confirm that's the desired view (vs gross spend).
6. **One billing account or several?** Design assumes Firebase + GCP under one account;
   `billing_account` is stored so multi-account is a non-breaking extension.
