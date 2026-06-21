# Phase 5-E1 — Controlled FMP Backfill (v1)

## Why Phase 5-E1 follows Phase 5-E0

Phase 5-E0 stood up the **safe ingestion foundation**: the
[`research/providers/fmp_client.py`](../research/providers/fmp_client.py) adapter
(stdlib only, key from `FMP_API_KEY` env var only, URLs redacted, host
allow-listed) plus a trial collector that cataloged the 12 needed endpoints,
defined the normalized point-in-time schema, and audited secret safety. The 5-E0
live smoke confirmed the current FMP **`/stable/`** API authenticates and returns
data for `company_profile`, `income_statement_quarterly`, and `ratios_quarterly`.

Phase 5-E1 turns that foundation into a **controlled, resumable backfill system**.
It is still **Track A quant work** — building a stronger quantitative model before
any operational deployment — not Paper Trader, GCP, or order/automation work. It
**collects and normalizes** the paid external alpha data that Phase 5-E2 will turn
into model features. It does **not** build the enriched model and does **not**
fabricate data.

## What data is being backfilled

Priority-ordered endpoint families (the request plan enumerates one request per
endpoint × ticker; ticker-less endpoints appear once):

| # | endpoint | alpha family | stable status |
|---|---|---|---|
| 1 | company_profile | universe_mapping | stable_confirmed |
| 2 | income_statement_quarterly | fundamentals | stable_confirmed |
| 3 | balance_sheet_statement_quarterly | fundamentals | stable_confirmed |
| 4 | cash_flow_statement_quarterly | fundamentals | stable_confirmed |
| 5 | key_metrics_quarterly | fundamentals | stable_confirmed |
| 6 | ratios_quarterly | fundamentals | stable_confirmed |
| 7 | earnings_calendar | earnings | needs_live_verification |
| 8 | earnings_surprises | earnings | needs_live_verification |
| 9 | analyst_estimates | analyst_revisions | needs_live_verification |
| 10 | analyst_recommendations | analyst_revisions | needs_live_verification |
| 11 | analyst_price_targets | analyst_revisions | needs_live_verification |
| 12 | sp500_constituents | universe_mapping | stable_confirmed |

The default dry-run universe is small and explicit: **AAPL, MSFT, NVDA, AMZN,
JPM**. A full S&P 500 backfill is *supported by the code later* but **never runs
automatically** — it requires an explicit large `--universe` plus explicit
`--max-tickers` / `--max-requests`.

## What is stored locally and why it is not committed

Paid FMP responses are licensed data and must never enter Git. They are written
**only** under a git-ignored local tree:

- `research/data/fmp/raw/{endpoint_name}/{ticker}.json` — verbatim provider JSON.
- `research/data/fmp/normalized/{endpoint_name}.csv` — flattened point-in-time CSV.

[`research/data/fmp/.gitignore`](../research/data/fmp/.gitignore) ignores `raw/`,
`normalized/`, and (belt-and-braces) everything in that directory except the
`.gitignore` itself, so no paid payload can ever be staged from there. The runner
**self-heals** this `.gitignore` on every run (idempotent, deterministic content),
so a fresh checkout is always commit-safe.

The only artifacts written to the **committed** output directory
`research/output/phase5e1_fmp_controlled_backfill/` are *summaries* — counts,
redacted URLs, file paths, coverage/quality, and the plans:

- `phase5e1_fmp_controlled_backfill.json` — the main report.
- `fmp_backfill_request_plan.csv` — one planned request per endpoint × ticker.
- `fmp_backfill_endpoint_access_plan.csv` — per-endpoint entitlement plan.
- `fmp_backfill_storage_manifest.csv` — planned/actual local raw+normalized paths.
- `fmp_backfill_progress_template.csv` — the resumable progress tracker.
- `fmp_backfill_quality_report.csv` — coverage/quality (placeholder until live).
- `fmp_backfill_secret_safety_audit.csv` — the secret-safety leak scan results.
- `phase5e2_enriched_panel_plan.json` — the Phase 5-E2 feature plan.

None of these contain raw payloads, the API key, or the `apikey=` query marker.

## How API-key safety is handled

- The key is read **only** from the `FMP_API_KEY` environment variable
  (`fmp.resolve_api_key()` / `has_api_key()`). It is **never printed, never
  written to any artifact, never committed, never hard-coded.**
- Every persisted URL passes through `fmp.redacted_request_url()`, which **strips
  the key query parameter entirely** and appends a `<API_KEY_REDACTED>`
  placeholder, so no artifact ever contains the substring `apikey=`.
- After writing the committed artifacts the runner runs a **leak scan** over them
  (`fmp_backfill_secret_safety_audit.csv`) asserting no raw key and no key marker.
- Live requests are refused for any host other than `financialmodelingprep.com`.

## How endpoint entitlement verification works

Phase 5-E1 does **not** assume every endpoint is entitled. The endpoint access
plan records, per endpoint:

- `endpoint_status` — `stable_confirmed` (assume entitled, still verified on the
  first live call) or `needs_live_verification` (stable name not certain; must be
  **probed live** and observe a 200/empty/403/404 before reliance).
- `required_for_phase5e2` — whether the family materially feeds the enriched panel.
- `live_access_result` — `planned` in dry-run; in live mode one of `success`,
  `empty`, `blocked_403`, `not_found_404`, `rate_limited`, or `error`.

In live mode the progress template records, per request, the `status`
(`planned`/`success`/`empty`/`error`/`skipped`), `row_count`, `http_status`, a
sanitized `error_message_sanitized`, and the local raw/normalized file paths — so
a rerun can resume and so blocked/empty endpoints are visible, never silently
assumed entitled.

## How to run a bounded live sample

```powershell
Set-Location "C:\Users\binis\Stock_Prediction_app_push"
$env:FMP_API_KEY = "<your-key>"   # never commit this; environment only
python research\run_phase5e1_fmp_controlled_backfill.py --live --max-tickers 5 --max-endpoints 6 --max-requests 40
```

This requires `FMP_API_KEY`, caps total requests at `--max-requests`, writes raw
JSON + normalized CSV under the git-ignored `research/data/fmp/` tree, and updates
the committed artifacts with **summarized** coverage/quality/manifest data only. A
re-run overwrites raw files in place (deterministic `endpoint/ticker.json` naming;
resume via the progress template's per-request `status`).

## How to run a larger backfill later

The same runner supports a larger universe — it is intentionally **opt-in** and
never automatic:

```powershell
# Explicit, bounded; supply your own universe list and generous caps.
python research\run_phase5e1_fmp_controlled_backfill.py --live `
  --universe "AAPL,MSFT,NVDA,...full S&P 500 list..." `
  --max-tickers 503 --max-endpoints 12 --max-requests 7000
```

Respect the FMP plan's per-minute and monthly budget; the `MIN_SLEEP_SECONDS`
spacing throttles requests, the progress template enables checkpoint/resume, and
unsupported endpoints are skipped gracefully and recorded (never assumed).

## How Phase 5-E2 will convert this into model features

[`phase5e2_enriched_panel_plan.json`](../research/output/phase5e1_fmp_controlled_backfill/phase5e2_enriched_panel_plan.json)
specifies the next phase — *Phase 5-E2 — Enriched FMP Alpha Panel and Model
Rerun*. It appends point-in-time FMP feature families
(`fundamentals_quality_value_growth`, `profitability_and_margin_trend`,
`balance_sheet_leverage`, `cash_flow_quality`, `valuation_ratios`,
`earnings_surprise_event`, and the analyst revision/recommendation/price-target
signals *only if that data is available*) to the **Phase 5-C** survivor
cross-sectional panel — **same** universe, rebalance schedule, walk-forward folds,
and embargo, changing **only** the feature set. It measures
`delta_mean_rank_ic = enriched − price_only` on the primary label
`forward_excess_return_20d_vs_spy` with the same leakage controls (as-of joins,
label-shuffle placebo, survivorship gating), with explicit stale-data limits and
coverage thresholds. As in Phase 5-D this is framed strictly as an
**incremental-edge test, not a foregone win** — the paid data must clear the edge
+ stability gates or be reported as not worth deploying.

## Why this is still Track A quant work, not Paper Trader / GCP work

Phase 5-E1 only collects and normalizes research data to build a stronger
quantitative model. It writes nothing outside `research/output/...` and the
git-ignored `research/data/fmp/...`; it touches no Paper Trader file, no GCP
config, and no deployment path. The report carries the explicit safety contract:
`preview_only=true`; `orders_enabled`, `automation_enabled`,
`broker_execution_enabled`, `production_replacement` all `false`.

## Recommendation values

- `READY_FOR_LIVE_CONTROLLED_SAMPLE` — dry-run (with or without a key): the plan
  is built and ready to run a bounded live sample.
- `READY_FOR_PHASE5E2_ENRICHED_PANEL` — a live sample where enough core stable
  endpoints returned data (≥2 core fundamentals/profile successes).
- `NEEDS_ENDPOINT_ENTITLEMENT_REVIEW` — a live sample where core endpoints were
  attempted but blocked/empty; review FMP plan entitlements and stable names.
- `BLOCKED_MISSING_FMP_KEY` — `--live` was passed without `FMP_API_KEY` (no
  network performed; the run stays in dry-run).
- `ERROR` — a live sample where every request failed with no core endpoint
  attempted.

## Reproduce (Windows PowerShell only)

```powershell
Set-Location "C:\Users\binis\Stock_Prediction_app_push"
python research\run_phase5e1_fmp_controlled_backfill.py
python -m pytest tests\test_phase5e1_fmp_controlled_backfill.py -q
```

## Safety contract

Collection/normalization only. `preview_only=true`; `orders_enabled`,
`automation_enabled`, `broker_execution_enabled`, `production_replacement` all
`false`. Default dry-run does zero network. Live requires `FMP_API_KEY` + `--live`
+ bounded limits. No AlphaVantage, no other paid APIs, no orders, no broker
execution, no automation, no deploy, no Paper Trader changes, no GCP changes, no
full S&P 500 auto-backfill, no writes to D: (read-only input), no fabricated data,
no binary model artifacts, no committed secret, no committed paid data, no commit.
The API key is environment-only and always redacted in persisted artifacts.
