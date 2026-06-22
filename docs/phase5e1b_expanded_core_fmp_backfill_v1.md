# Phase 5-E1B — Expanded Core FMP Fundamentals Backfill (v1)

Track A (quant brain). Phase 5-E1B extends the controlled FMP backfill
([`research/run_phase5e1_fmp_controlled_backfill.py`](../research/run_phase5e1_fmp_controlled_backfill.py))
from a 5-ticker smoke sample into a **larger, quota-safe, resumable cross-sectional
CORE fundamentals dataset** sized for the Phase 5-E2 enriched alpha panel. It still
**does not build the model** — it collects and normalizes the core data Phase 5-E2
needs. All paid raw + normalized data stay **local and git-ignored**; only
summarized plan / coverage / readiness artifacts are committed.

## Why expand core fundamentals before Phase 5-E2

The Phase 5-E1 live smoke proved the FMP `/stable/` API authenticates and returns
the four core fundamentals/profile endpoints for 5 tickers (20 successful requests,
0 empty, raw + normalized written locally). Five tickers is enough to prove the
pipeline but **far too narrow for cross-sectional model validation**: Phase 5-E2
measures whether enriched features add rank-IC edge over the Phase 5-C price-only
baseline *across the S&P 500 cross-section*, on walk-forward folds. A handful of
names cannot support a credible cross-sectional rank IC, decile spread, or
coverage threshold. Phase 5-E1B therefore widens collection to the **Phase 5-C
price-history universe** (the same names the baseline already ranks), one bounded
batch at a time, so the enriched panel is built on the same universe as the
baseline it must beat.

It is deliberately **core-only**. The four endpoints —
`company_profile`, `income_statement_quarterly`,
`balance_sheet_statement_quarterly`, `cash_flow_statement_quarterly` — are the
`stable_confirmed`, live-verified fundamentals/profile set. They give value /
quality / growth / margin / leverage / cash-flow features (the bulk of the
Phase 5-E2 feature plan) without depending on any unverified entitlement.

## Why earnings / analyst endpoints are not blocking this step

The five earnings/analyst endpoints (`earnings_calendar`, `earnings_surprises`,
`analyst_estimates`, `analyst_recommendations`, `analyst_price_targets`) remain
`needs_live_verification` in the endpoint access plan: their stable `/stable/`
names and our plan entitlement are not yet confirmed. The Phase 5-E2 plan already
treats those families as **optional** ("only if that data is available"). Blocking
the entire expanded backfill on unverified endpoints would stall the parts that
are confirmed and valuable. So Phase 5-E1B collects the confirmed core
fundamentals now; the earnings/analyst families can be verified and added later
(in a follow-up batch or phase) without redoing core collection.

## How to run the first live core batch (batch 001)

```powershell
Set-Location "C:\Users\binis\Stock_Prediction_app_push"
$env:FMP_API_KEY = "<your-key>"   # environment only; never commit, never logged
python research\run_phase5e1_fmp_controlled_backfill.py --live --core-only `
  --universe-source phase5c --max-tickers 25 --max-requests 100 `
  --skip-existing --batch-id core_batch_001
```

This loads the universe from the Phase 5-C price history (safe fallback to the
sample universe if that CSV is unavailable), collects the four core endpoints for
the first 25 names (4 × 25 = 100 requests, capped by `--max-requests`), writes raw
JSON under `research/data/fmp/raw/{endpoint}/{ticker}.json` and normalized CSV
under `research/data/fmp/normalized/{endpoint}.csv` (both git-ignored), and
refreshes the committed summary artifacts:

- `fmp_core_backfill_batch_plan.csv` — one planned request per (endpoint, ticker),
  with redacted URLs and target raw paths.
- `fmp_core_backfill_batch_summary.csv` — per-endpoint success / empty / error /
  covered counts + coverage % + live access result.
- `fmp_core_backfill_coverage_report.csv` — per-ticker endpoints-covered + a
  `core_ready` flag.
- `phase5e1b_core_backfill_readiness.json` — the consolidated readiness summary
  (counts, coverage_by_endpoint, coverage_by_ticker, thresholds, recommendation).

The default mode is **dry-run** (no `--live`): no network, no key required, builds
the batch plan + readiness only, and reports `READY_FOR_EXPANDED_CORE_LIVE_BATCH`.

## How to run subsequent batches with `--skip-existing`

Collect the universe in quota-safe slices. Each later batch reuses everything the
prior batches already wrote — a (endpoint, ticker) whose raw file already exists is
recorded `skipped` and **consumes no request budget**, so nothing is re-downloaded:

```powershell
# batch 002: the next 25 names; already-collected files are skipped automatically
python research\run_phase5e1_fmp_controlled_backfill.py --live --core-only `
  --universe-source phase5c --max-tickers 50 --max-requests 100 `
  --skip-existing --batch-id core_batch_002
```

`--resume` is an alias that implies `--skip-existing`. Raise `--max-tickers` each
batch (it is the prefix size of the universe considered); `--skip-existing` makes
the overlap with prior batches free, so successive batches collect only the new
tail. Respect your FMP plan's per-minute / monthly budget; `--max-requests` is a
hard ceiling and the adapter throttles between calls.

## How to know when enough coverage exists for Phase 5-E2

`phase5e1b_core_backfill_readiness.json` carries the decision. `enough_for_phase5e2`
becomes `true` (and the recommendation becomes
`READY_FOR_PHASE5E2_WITH_CORE_FUNDAMENTALS`) only when **both** coverage gates pass:

- every core endpoint's `coverage_pct` ≥ `min_endpoint_coverage_pct` (60%), and
- the number of **core-ready** tickers (≥ `min_core_endpoints_per_ticker` = 3 of 4
  core endpoints covered) ≥ `min_core_ready_tickers` (30).

Recommendation values:

- `READY_FOR_EXPANDED_CORE_LIVE_BATCH` — dry-run: the batch plan is ready to run live.
- `READY_FOR_PHASE5E2_WITH_CORE_FUNDAMENTALS` — both coverage gates pass; proceed to 5-E2.
- `NEEDS_MORE_CORE_BACKFILL_BATCHES` — live data collected but below the gates; run
  more `--skip-existing` batches.
- `BLOCKED_MISSING_FMP_KEY` — `--live` without `FMP_API_KEY` (no network performed).
- `ERROR` — every attempted core request failed/empty (inspect the batch summary
  and sanitized errors).

Read `coverage_by_endpoint` / `coverage_by_ticker` in the readiness JSON (or the
two CSV reports) to see exactly which names/statements are still missing before
running the next batch.

## Request-level diagnostics (hotfix)

The first live `core_batch_001` succeeded for all 25 `company_profile` requests but
only 8 of 25 for each quarterly statement (49 success / 51 error), and the batch
summary exposed only per-endpoint counts — not *why* the 51 failed. The hotfix adds
two committed-safe, payload-free artifacts that record one row per planned request:

- `fmp_core_backfill_request_results.csv` — one row per planned request with
  `batch_id`, `ticker`, `endpoint_name`, `status`
  (`planned` / `success` / `empty` / `error` / `skipped_existing`), `http_status`,
  `error_type`, `error_message_sanitized`, `likely_cause`, `next_action`,
  `row_count`, `raw_file_path`, `normalized_file_path`, `request_url_redacted`
  (key always stripped), and `collected_at_utc`.
- `fmp_core_backfill_error_report.csv` — only the error rows, with the groupable
  fields `endpoint_name`, `ticker`, `http_status`, `error_type`,
  `error_message_sanitized`, `likely_cause`, `next_action`.

`likely_cause` / `next_action` are derived deterministically from the HTTP status
and error type: 401 → bad/unauthorized key, 402/403 → endpoint not entitled on the
plan, 404 → wrong symbol or stable endpoint name, 429 → rate limited (wait, then
resume with `--skip-existing`), 5xx → transient provider error (retry), timeout /
non-JSON → connectivity / malformed body. This is what tells us whether the 51
failures are rate-limit, entitlement, symbol, timeout, or request-construction
issues — and exactly what to do next.

The readiness JSON gains matching fields: `core_ready_ticker_count`,
`partial_ticker_count`, `request_success_count`, `request_error_count`,
`request_skipped_existing_count`, `error_breakdown_by_http_status`,
`error_breakdown_by_endpoint`, and a ready-to-paste `next_retry_command` (the
`--skip-existing` retry for this batch). A `--skip-existing` resume marks each
already-collected request `skipped_existing` (no network, no budget) so the retry
spends quota only on the requests that still need collecting.

## Reliability patch — hardened skip-existing + known-blocked register

The first live `core_batch_001` retry exposed two reliability gaps. **(a)** With
`--skip-existing`, every previously-collected request was re-sent (0 skipped): the
old guard trusted any file that merely existed, and the test teardown wipes the
git-ignored raw tree between runs, so a stale/empty file (or none at all) silently
failed to resume. **(b)** The 51 failures were **HTTP 402** — a structural FMP plan
block on the quarterly statements, not a transient rate-limit — yet each retry
spent a live call re-hitting them.

The patch fixes both:

- **Validated skip-existing.** A `(endpoint, ticker)` is skipped only when a
  *valid* raw file exists — parseable JSON with ≥1 row. An empty or corrupt cached
  file is re-fetched instead of being trusted. Detection is keyed purely on
  `raw/{endpoint}/{ticker}.json` (no batch_id in the name) and falls back to a
  deterministic glob (`{ticker}*.json`) so timestamped / alternately-named files
  from an older layout are still found. Skips consume **no** request budget.
- **Known-blocked register.** `fmp_core_backfill_known_blocked.csv` persists every
  structural block (HTTP **402** / **403**) across batches with columns `ticker`,
  `endpoint_name`, `http_status`, `error_type`, `error_message_sanitized`,
  `likely_cause`, `first_seen_batch_id`, `last_seen_batch_id`, `retry_policy`. A
  402/403 carries `retry_policy = do_not_retry_without_override`; a repeat block
  keeps `first_seen_batch_id` and advances `last_seen_batch_id`. Transient errors
  (429/5xx/timeout) are **not** remembered — they stay retryable.
- **`--skip-known-blocked`** skips registered blocks (status `skipped_known_blocked`,
  no call, no budget). **`--retry-known-blocked`** overrides it to re-attempt them
  after a plan upgrade. A known-blocked skip is **not** counted as covered.

Run the next batch widening the slice while reusing collected files and skipping
known 402s, so live calls are spent mainly on new tickers:

```powershell
python research\run_phase5e1_fmp_controlled_backfill.py --live --core-only `
  --universe-source phase5c --max-tickers 50 --max-requests 100 `
  --skip-existing --skip-known-blocked --batch-id core_batch_002
```

The readiness JSON gains `request_skipped_known_blocked_count`, `known_blocked_count`,
`known_blocked_by_http_status`, `known_blocked_by_endpoint`,
`live_budget_spent_on_new_requests_count`, `live_budget_spent_on_retries_count`, and
a ready-to-paste `next_batch_command` (the command above). A *retry* is a live call
that re-hit a pair already in the register at the start of the batch; everything
else is a *new* request.

## Cache-safety fix — tests never touch real paid data, plus a pre-live preflight

The first `core_batch_002` live attempt re-requested already-collected tickers
(AAPL, ABBV, ABT, …) even with `--skip-existing`. Root cause: the **test suite**
was deleting the user's real paid-data cache. The module teardown and the live-test
cleanups ran `shutil.rmtree(runner._RAW_DIR)` / `rmtree(runner._NORM_DIR)` against
the **real** `research/data/fmp/{raw,normalized}` tree, so after the suite ran there
were no cached files left for `--skip-existing` to detect.

The fix isolates all live/cache simulations from real data:

- **Temp-dir redirection.** Every live test now redirects the runner's paid-data
  dirs to a per-test `tmp_path` tree via `_use_tmp_data_dirs(runner, tmp_path)`
  before any live write, and restores the real paths **before** regenerating the
  canonical dry-run artifacts. No test reads, writes, or deletes the real
  `research/data/fmp/` tree. The committed OUTPUT dir is never redirected, so
  committed artifacts stay canonical.
- **No more real-data deletion.** All `shutil.rmtree` of the real raw/normalized
  dirs is removed from the tests; `_reset_core_artifacts` only clears the
  known-blocked register and re-emits the dry-run artifacts.
- **Regression guard.** `test_real_paid_data_dirs_are_never_deleted_by_a_live_test`
  drops a sentinel into the real tree, runs a redirected live batch, and asserts the
  sentinel survives. `test_core_skip_existing_in_tmp_dir_makes_no_live_call` proves
  a valid cached file in a temp raw dir is skipped with **zero** live calls.

**Always run the preflight before a live batch.** A new read-only mode reports,
without any network or `FMP_API_KEY`, exactly how the batch will behave:

```powershell
python research\run_phase5e1_fmp_controlled_backfill.py --core-only `
  --universe-source phase5c --max-tickers 50 --max-requests 100 `
  --skip-existing --skip-known-blocked --batch-id core_batch_002 --preflight-cache-only
```

It prints and writes `phase5e1b_core_preflight.json` + `fmp_core_backfill_preflight.csv`
with `existing_raw_files_detected`, `planned_skipped_existing_count`,
`planned_skipped_known_blocked_count`, and `planned_live_request_count_if_live` — so
the live-call cost is known up front. It never calls FMP, never spends budget, and
never mutates paid data.

## Why raw / normalized paid data stay local and ignored

FMP responses are licensed paid data and must never enter Git. They are written
**only** under [`research/data/fmp/`](../research/data/fmp/), which
[`research/data/fmp/.gitignore`](../research/data/fmp/.gitignore) ignores
(`raw/`, `normalized/`, then `*` + `!.gitignore`, so only the `.gitignore` is ever
committable). The runner self-heals that `.gitignore` on every run. The committed
artifacts hold **summaries only** — counts, coverage percentages, redacted URLs,
and local file paths — never payloads and never the API key. A secret leak scan
runs over the committed core artifacts each run
(`secret_safety_leak_scan_clean` in the readiness JSON) asserting no raw key and no
`apikey=` marker. The key is read solely from `FMP_API_KEY`, never printed, never
written to disk, never hard-coded.

## Safety contract

Collection / normalization only. `preview_only=true`; `orders_enabled`,
`automation_enabled`, `broker_execution_enabled`, `production_replacement` all
`false`. Default dry-run does zero network. Live requires `FMP_API_KEY` + `--live`
+ bounded `--max-tickers` / `--max-requests`. No AlphaVantage / other paid APIs, no
orders, no broker execution, no automation, no deploy, no Paper Trader changes, no
GCP changes, no full S&P 500 auto-backfill (batches are explicit and bounded), no
writes to D: (read-only input only), no fabricated data, no binary model artifacts,
no committed secret, no committed paid data, no commit.

## Reproduce (Windows PowerShell only)

```powershell
Set-Location "C:\Users\binis\Stock_Prediction_app_push"
python research\run_phase5e1_fmp_controlled_backfill.py --core-only --universe-source phase5c --max-tickers 25
python -m pytest tests\test_phase5e1_fmp_controlled_backfill.py -q
```
