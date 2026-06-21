# Phase 5-E0 — FMP External Alpha Provider Adapter & Trial Collector (v1)

## Why we are moving to paid external alpha data

Phase 5-C proved, out-of-sample, that the **price-only** cross-sectional model is
not deployment-grade (baseline rank IC ≈ 0; fitted models only modestly above
noise; survivorship-inflated absolute returns). Phase 5-D then inventoried every
non-price dataset already on disk and found that the strongest documented
cross-sectional alpha families beyond price — **analyst estimate revisions,
earnings actual-vs-estimate at full coverage, ratios / key-metrics, analyst price
targets, recommendations** — are **absent or only partial** locally. The honest
conclusion from 5-D was: do not keep dragging quant research on limited free/local
data; the next edge requires a real provider.

Phase 5-E0 acts on that. It does **not** build the enriched model and does **not**
fabricate data. It stands up the **reliable ingestion foundation**: a safe
provider adapter plus a trial collector that catalogs the needed endpoints, plans
a small sample, defines the normalized point-in-time schema, and audits secret
safety — so that once a key is set, Phase 5-E1 can backfill cleanly.

## Why more price data is not the main bottleneck

The Phase 5-C/3-P evidence is consistent: adding more price history or more
price-derived technical features did **not** move the headline edge (Phase 3-P's
best model was `ridge_technical_only @126d`, worst-year IC −0.108, FAIL). The
missing ingredient is **orthogonal, point-in-time fundamental / estimate / event
signal**, not more of the same price series. That is what a fundamentals-and-
estimates provider supplies.

## Why FMP is the first practical provider to test

Financial Modeling Prep covers, from a single REST API and a small paid monthly
trial: company fundamentals (income / balance-sheet / cash-flow, quarterly),
ratios & key metrics, the earnings calendar, earnings surprises, analyst
estimates, analyst recommendations, analyst price targets, and S&P 500
constituents (current + a historical add/drop endpoint that can remove
survivorship). It maps almost one-to-one onto the families Phase 5-D flagged as
missing, which makes it the cheapest first experiment.

## Endpoint paths use the current FMP `/stable/` API

The first live smoke (Phase 5-E0) failed with **HTTP 403 on every request** even
though the key was present (`api_key_present: true`). Root cause: the adapter was
built against the **legacy `/api/v3` and `/api/v4`** paths, which current FMP keys
are no longer entitled to — current keys are provisioned against the **`/stable/`**
API, where the symbol is a query parameter (`?symbol=AAPL`). All endpoint path
templates were migrated to `/stable/...`. A leakage-free
[`fmp_live_error_report.csv`](../research/output/phase5e0_fmp_provider_trial/fmp_live_error_report.csv)
now records the sanitized HTTP status, error type, likely cause, and next action
for any failed live request.

Because not every legacy endpoint has a one-to-one, certain stable name, each
catalog entry carries an **`endpoint_status`**:

- `stable_confirmed` — the stable path/shape is known; safe to collect.
- `needs_live_verification` — a best-effort stable path is provided but the name
  is **not certain**; it is **excluded from the first live smoke** and must show a
  live 200 (or be confirmed in FMP docs) before Phase 5-E1 relies on it. We do not
  guess silently — the uncertainty is recorded in the catalog and the report.

## Endpoint families needed (12 cataloged)

| endpoint | alpha family | stable path | status | point-in-time basis | leakage |
|---|---|---|---|---|---|
| company_profile | universe_mapping | `/stable/profile?symbol=` | confirmed | current snapshot (NOT PIT) | high |
| income_statement_quarterly | fundamentals | `/stable/income-statement?symbol=&period=quarter` | confirmed | `fillingDate`/`acceptedDate` | low |
| balance_sheet_statement_quarterly | fundamentals | `/stable/balance-sheet-statement?symbol=&period=quarter` | confirmed | `fillingDate`/`acceptedDate` | low |
| cash_flow_statement_quarterly | fundamentals | `/stable/cash-flow-statement?symbol=&period=quarter` | confirmed | `fillingDate`/`acceptedDate` | low |
| key_metrics_quarterly | fundamentals | `/stable/key-metrics?symbol=&period=quarter` | confirmed | period `date` → lag to filing | medium |
| ratios_quarterly | fundamentals | `/stable/ratios?symbol=&period=quarter` | confirmed | period `date` → lag to filing | medium |
| earnings_calendar | earnings | `/stable/earnings?symbol=` | needs_live_verification | announcement `date` | low |
| earnings_surprises | earnings | `/stable/earnings?symbol=` | needs_live_verification | reporting `date` | low |
| analyst_estimates | analyst_revisions | `/stable/analyst-estimates?symbol=` | needs_live_verification | consensus snapshot (needs successive snapshots) | medium |
| analyst_recommendations | analyst_revisions | `/stable/grades?symbol=` | needs_live_verification | dated grade | low |
| analyst_price_targets | analyst_revisions | `/stable/price-target-summary?symbol=` | needs_live_verification | `publishedDate` | low |
| sp500_constituents | universe_mapping | `/stable/sp500-constituent` | confirmed | current list (NOT PIT) → use historical endpoint | high |

Endpoint paths are **configurable string templates** in
[`research/providers/fmp_client.py`](../research/providers/fmp_client.py); the code
does **not** assume every plan supports every endpoint (live collection skips a
failing endpoint gracefully and records the sanitized error).

## How secrets are handled

- The key is read **only** from the `FMP_API_KEY` environment variable
  (`resolve_api_key()` / `has_api_key()`).
- It is **never printed, never written to any artifact, never committed, never
  hard-coded.** Every URL that is persisted is passed through
  `redacted_request_url()`, which **strips the key query parameter entirely** and
  appends a `<API_KEY_REDACTED>` placeholder instead — so no persisted or
  committed artifact ever contains the key query parameter, not even with a
  redacted value. Non-secret parameters (e.g. `period=quarter`) are preserved so
  the artifact still documents the real request shape.
- A live key-bearing URL is built (`build_fmp_url()`) only transiently for an
  actual request and is never stored; exceptions are sanitized so a key cannot
  leak through an error string.
- After writing all artifacts the collector runs a **leak scan**
  (`fmp_secret_safety_audit.csv`) that re-reads every output file and asserts no
  raw key value and **no key-query-parameter marker** is present.

## Dry-run vs live-sample mode

- **Default (`python research/run_phase5e0_fmp_provider_trial_collector.py`)** —
  dry-run. **No network.** Writes all artifacts. Recommendation `READY_FOR_FMP_KEY`
  when no key is set, or `READY_FOR_LIVE_SAMPLE` when a key is present but `--live`
  was not passed.
- **Live smoke (`… --live --max-tickers 2 --max-endpoints 3`)** — requires
  `FMP_API_KEY`. Collects a **tiny** sample only. The first smoke set is the
  **safest `stable_confirmed` endpoints only**, in `smoke_priority` order:
  `company_profile` (auth canary), `income_statement_quarterly`, then
  `ratios_quarterly` — analyst, price-target, and earnings endpoints (all
  `needs_live_verification`) are deliberately held back until basic stable auth is
  confirmed. Writes raw JSON under `raw/`, normalized CSVs under `normalized/`, and
  a sanitized `fmp_live_error_report.csv` for any failures, then recommends
  `READY_FOR_PHASE5E1_BACKFILL` (or `ERROR` if every request failed). If `--live`
  is passed **without** a key, the run stays in dry-run and recommends
  `BLOCKED_MISSING_FMP_KEY` (no network). It never performs a full S&P 500
  backfill.

## How point-in-time fields are handled

Every normalized record carries appended PIT metadata columns:
`_provider, _endpoint, _alpha_family, _join_key, _join_key_value, _pit_date_field,
_pit_date_value, _point_in_time_status, _collected_note`. The
`point_in_time_status` vocabulary matches Phase 5-D
(`point_in_time_safe` / `potentially_point_in_time` / `not_point_in_time_safe` /
`unknown`). Statements are PIT-safe on `fillingDate`/`acceptedDate`; key-metrics
and ratios expose only a period `date` and must be lagged to the matching
statement's filing availability; analyst consensus is a snapshot needing
successive captures for a true revision series; profile and the current S&P 500
list are explicitly **not** point-in-time.

## What Phase 5-E1 backfill will do

[`phase5e1_backfill_plan.json`](../research/output/phase5e0_fmp_provider_trial/phase5e1_backfill_plan.json)
specifies: read `FMP_API_KEY`; backfill the modeling universe (S&P 500, current +
historical constituents for PIT membership) across the fundamentals / earnings /
analyst families; store raw JSON + normalized point-in-time CSVs; throttle,
checkpoint and resume within the trial's request budget; skip unsupported
endpoints gracefully; never log or commit the key.

## How Phase 5-E2 will test whether the paid data improves the model

Phase 5-E2 — *Enriched Alpha Panel Rerun with FMP Data* — appends the new
point-in-time FMP features to the **same** survivor universe, rebalance schedule,
walk-forward folds, embargo (≥20), and models as Phase 5-C, changing **only** the
feature set. It measures `delta_mean_rank_ic = enriched − price_only` on the
primary label `forward_excess_return_20d_vs_spy` with the same leakage controls
(as-of joins, placebo label-shuffle, survivorship gating). As in Phase 5-D this is
framed strictly as an **incremental-edge test, not a foregone win** — the prior
multisignal attempt did not beat price-only at the headline, so the paid data must
prove its edge or be reported as not worth deploying.

## Files

- `research/providers/__init__.py`, `research/providers/fmp_client.py` — the adapter.
- `research/run_phase5e0_fmp_provider_trial_collector.py` — the trial collector.
- `tests/test_phase5e0_fmp_provider_trial_collector.py` — 30 tests (dry-run, no key, no network).
- `research/output/phase5e0_fmp_provider_trial/` — 8 always-on artifacts (report,
  endpoint catalog, trial collection plan, schema contract, normalized sample
  manifest, point-in-time readiness, secret safety audit, Phase 5-E1 backfill
  plan); plus `fmp_live_error_report.csv`, `raw/`, and `normalized/` which appear
  only after a live smoke.

## Reproduce (Windows PowerShell only)

```powershell
Set-Location "C:\Users\binis\Stock_Prediction_app_push"
python research\run_phase5e0_fmp_provider_trial_collector.py
python -m pytest tests\test_phase5e0_fmp_provider_trial_collector.py -q
# Optional live smoke AFTER you set the key:
$env:FMP_API_KEY = "<your-key>"   # never commit this
python research\run_phase5e0_fmp_provider_trial_collector.py --live --max-tickers 2 --max-endpoints 3
```

## Safety contract

Ingestion-foundation only. `preview_only=true`; `orders_enabled`,
`automation_enabled`, `broker_execution_enabled`, `production_replacement` all
`false`. No AlphaVantage, no other paid APIs, no orders, no broker execution, no
automation, no deploy, no Paper Trader changes, no GCP changes, no writes to D:
(read-only input), no fabricated data, no binary model artifacts, no committed
secret, no commit. The API key is environment-only and always redacted in
persisted artifacts.
