# Phase 8-R - Broad Earnings/Fundamentals Bundle Evaluation (EODHD test-first)

Status: implemented + tested (offline, 28/28). A live run executed against the user's EODHD_API_KEY.
The key is VALID (the EODHD `user`/`eod` endpoints return HTTP 200) but the account is on the FREE
tier (`subscriptionType=free`, `dailyRateLimit=20`), which serves EOD price data ONLY: the
`fundamentals` and `earnings_calendar` endpoints both return **HTTP 403 "Only EOD data allowed for
free users."** The earnings/fundamentals/analyst bundle we need is therefore NOT entitled on this
plan. The runner classifies this free-tier 403 distinctly from an invalid key and emits
**`NEEDS_VENDOR_QUOTES`** (subscribe to a paid EODHD Fundamentals plan, then re-run). No raw or
normalized payloads were written (nothing was entitled); only the committed-safe metadata artifacts
+ the gitignored `research/data/eodhd/.gitignore` exist. NOT committed.

## Why this phase exists

Phase 8-Q decided `MIXED_FREE_PLUS_PAID_CORE_STACK`: keep free where it genuinely works (FRED
macro, the OHLCV cache, ticker/sector identity), **buy one cheap broad earnings/fundamentals
provider** for the core signal, defer expensive alt-data. The test-first paid provider is **EODHD**
(~$20-$80/mo single bundle: earnings + fundamentals + analyst, bulk download, point-in-time dates);
**FMP Premium is the fallback** only if EODHD fails; **FMP Ultimate is rejected**.

Phase 8-R is the bounded evaluation that de-risks that purchase **before** any budget is committed.
It is **not** a full S&P 500 backfill - it is a coverage / point-in-time / cost probe.

## What it does

1. Reads the prior evidence (read-only, never raw payloads): the 8-Q decision + cost tiers, the 8-P
   Alpha Vantage coverage, the 8-N FMP-blocked ticker set.
2. Builds a bounded, prioritized request plan: **FMP-blocked tickers first**, then the current
   research universe (Phase 5C / 8K / 8L candidates), then FMP-covered cross-check, then a
   large-cap fallback. Each ticker costs at most **2 requests** (one fundamentals call yields four
   families; one earnings-calendar call).
3. If `EODHD_API_KEY` is present and `--live` is passed: runs the bounded probe (defaults
   `max_tickers=50`, `max_requests=100`, `skip_existing=true`), classifying per data family:
   status, rows, first/last date, **has point-in-time dates**, **historical depth sufficient**,
   **usable for backtest**. Stops on an invalid key, a systematic plan-block (3 consecutive 402s),
   or consecutive rate-limits.
4. If the key is absent (the current state): writes the full plan + all planning artifacts and
   emits `BLOCKED_MISSING_EODHD_KEY` with the exact next command to set the key.

## Data families probed (EODHD)

| Family | Endpoint | PIT basis | PIT-safe by design |
| --- | --- | --- | --- |
| `earnings_history` (actual/estimate/surprise/report date) | fundamentals | `reportDate` (announcement) | yes |
| `earnings_calendar` | calendar/earnings | `report_date` | yes |
| `fundamentals_statements` | fundamentals | `filing_date` (SEC) | yes (when present) |
| `analyst_estimates` | fundamentals (Earnings::Trend) | revised; no clean as-of | **no** |
| `analyst_ratings` (price targets / recommendations) | fundamentals (AnalystRatings) | current snapshot only | **no** |

The **core family** that decides whether EODHD can be the backbone is `earnings_history`.

## Decision logic

Over the probed tickers, compute the fraction with a **backtest-usable** core earnings panel
(status OK + point-in-time dates + historical depth >= 24 quarters or >= 8 years):

- `>= 0.80` and PIT-safe and not throttled -> **`EODHD_ACCEPT_AS_CORE_PROVIDER`**
- `>= 0.40` or rate-limited mid-run -> **`EODHD_PROMISING_NEEDS_LARGER_TRIAL`**
- below `0.40`, or a systematic 402 plan-block, or no usable coverage -> **`EODHD_REJECTED_INSUFFICIENT_COVERAGE`** (and the **FMP Premium fallback is recommended**)
- key missing, or present-but-rejected-as-invalid (HTTP 401) -> **`BLOCKED_MISSING_EODHD_KEY`**
- key VALID but on the FREE tier (HTTP 403 "Only EOD data allowed for free users") -> **`NEEDS_VENDOR_QUOTES`** (subscribe to a paid EODHD Fundamentals plan, then re-run; FMP fallback stays ON STANDBY because EODHD was not fairly tested - the data was simply not entitled)

Allowed decision values: `EODHD_ACCEPT_AS_CORE_PROVIDER`, `EODHD_PROMISING_NEEDS_LARGER_TRIAL`,
`EODHD_REJECTED_INSUFFICIENT_COVERAGE`, `BLOCKED_MISSING_EODHD_KEY`,
`FMP_PREMIUM_FALLBACK_RECOMMENDED`, `NEEDS_VENDOR_QUOTES`, `ERROR`.

**Current decision (live run, key valid but FREE tier): `NEEDS_VENDOR_QUOTES`** - the bundle is not
entitled on the free plan; subscribe to a paid EODHD Fundamentals plan, then re-run `--live`.

## Committed-safe artifacts (13)

| Artifact | Contents |
| --- | --- |
| `phase8r_broad_bundle_evaluation.json` | main report: evidence, plan, coverage, decision, next command |
| `eodhd_key_detection_report.csv` | env-var presence boolean (`value_read=False`) |
| `broad_bundle_request_plan.csv` | prioritized (ticker, endpoint) plan + skip/will_request + redacted URLs |
| `broad_bundle_probe_results.csv` | per (family, ticker): attempted/status/rows/dates/PIT/depth/usable |
| `eodhd_coverage_by_family.csv` | per family: attempted / with-data / PIT-safe / backtest-usable / fraction |
| `eodhd_vs_fmp_av_coverage_comparison.csv` | per ticker: fmp vs alphavantage vs eodhd vs newly-covered |
| `eodhd_point_in_time_readiness.csv` | per family: PIT basis + PIT-safe design + counts |
| `vendor_scorecard.csv` | 9 axes: breadth, coverage, history, PIT, freshness, schema, bulk, limits, cost |
| `provider_cost_value_decision.csv` | EODHD vs FMP Premium vs FMP Ultimate (rejected) + decision |
| `fmp_premium_fallback_plan.csv` | fallback trigger/conditions; plan-only until upgrade; Ultimate rejected |
| `broad_bundle_procurement_questions.csv` | questions to ask EODHD before buying |
| `phase8s_next_plan.json` | next phase keyed to the decision |
| `secret_safety_audit.csv` | key never read/printed/written; leak-scan result; data tree gitignored |

All artifacts carry **metadata only** - never a payload, never a key. Raw + normalized EODHD
payloads live ONLY under the gitignored `research/data/eodhd/{raw,normalized}/` trees.

## Vendor scorecard axes

coverage 100-500 tickers - history depth - point-in-time safety - update frequency - schema
stability - bulk/batch support - API request limits - estimated cost tier (from prior artifacts
only) - data-family breadth. Axes that require a live run read `not_measured (set EODHD_API_KEY and
--live)` until the key is present.

## Secret discipline (hard rules)

- `EODHD_API_KEY` is checked as a **presence boolean only** via `os.environ`; the value is never
  read into an artifact, printed, or written.
- Every persisted URL strips the `api_token` / `apikey` query parameter **entirely** and appends a
  placeholder. A leak scan over the written committed artifacts confirms
  `secret_safety_leak_scan_clean` (no held key value and no `api_token=`/`apikey=` marker).
- Default run is OFFLINE; a live evaluation needs **both** `--live` AND the key. Tests inject a
  `transport` and never touch a key or the network.
- The `research/data/eodhd/` tree is force-gitignored (`*` + `!.gitignore`) **before** any write,
  so licensed raw/normalized payloads can never be staged or committed.

## Run

```powershell
# Offline plan (default; no network, no key needed) -> BLOCKED_MISSING_EODHD_KEY today:
python research/run_phase8r_broad_bundle_evaluation.py

# Bounded live evaluation (requires EODHD_API_KEY):
$env:EODHD_API_KEY = '<PASTE_EODHD_API_KEY_HERE>'
python research/run_phase8r_broad_bundle_evaluation.py --live

# Test (fully offline):
python -m pytest tests/test_phase8r_broad_bundle_evaluation.py -q
```

## Constraints honored

Existing installed packages only (stdlib). No package install. No full S&P 500 backfill (bounded
`max_tickers`/`max_requests`, skip-existing, stop on invalid key / rate-limit / systematic block).
No API key printed or written. No raw/normalized provider data in committed artifacts. No FMP
Ultimate evaluation. No Paper Trader, no GCP, no deploy, no broker / order / automation logic. No
commit. No push.
