# Phase 8-P - Alpha Vantage Earnings Coverage Expansion and Signal Readiness Gate

Status: tested (offline, 19/19). Decision artifacts generated. NOT committed.

## Why this phase exists

Phase 8-N ran a **live** bounded FMP backfill and proved the **current FMP plan is INSUFFICIENT**:
only **8/20** tickers covered on every critical family (the rest 402-subscription-blocked). Phase
8-O then selected the cheapest viable path: **Alpha Vantage first** for broad earnings
actual/estimate/surprise data (free `EARNINGS` endpoint), Finnhub later only for the analyst
families; an FMP upgrade is **not** recommended and FMP Ultimate is **explicitly rejected**.

Phase 8-P executes exactly that earnings step. Using the user's `ALPHAVANTAGE_API_KEY` (if present
in the environment), it runs a **controlled, bounded** live probe + collection of the Alpha Vantage
`EARNINGS` endpoint for the **FMP-blocked tickers first**, normalizes the quarterly
actual/estimate/surprise rows, and answers one question decisively: *does Alpha Vantage solve the
broad earnings-surprise coverage bottleneck that FMP could not?* It then **gates**: if combined
FMP + Alpha Vantage coverage reaches the 20-ticker scoring threshold, it emits signal-ready earnings
manifests; otherwise it names exactly the next batch / provider.

## What it reads (read-only)

- `research/output/phase8n_fmp_critical_data_backfill_signal_expansion/fmp_ticker_coverage_by_family.csv`
  - the FMP earnings coverage split: **covered** = `has_data==yes`, **blocked** = `has_data==no`
    on `earnings_surprises` / `earnings_calendar`. FMP covered 8 (AAPL, MSFT, NVDA, ABBV, ADBE,
    AMD, AMZN, JPM); FMP blocked 9 (ABT, ACN, ADI, ADP, AMAT, AMGN, AON, APD, APH).
  - Falls back to the deterministic Phase 8-N lists if the file is unavailable.
- best-effort promising tickers from prior phases (S8K/S8L/S8N) "if extractable" - `[]` on failure.

It never reads raw payloads.

## Universe priority (the request plan)

1. **FMP-blocked tickers first** - ABT, ACN, ADI, ADP, AMAT, AMGN, AON, APD, APH.
2. promising prior-signal tickers (best-effort; may be empty).
3. FMP-collected tickers (cross-check) - AAPL, MSFT, NVDA, ABBV, ADBE, AMD, AMZN, JPM.
4. Phase-5C-style large-cap fallback (GOOGL, META, TSLA, AVGO, COST, ... ) to reach the scoring
   threshold. **Not** an S&P 500 backfill.

Deduped, `skip_existing` applied, capped at `max_tickers`.

## Bounded live run (defaults)

`max_tickers=25`, `max_requests=25`, `skip_existing=True`, stop immediately on an **invalid key**,
stop after `2` consecutive **rate-limit** envelopes (Alpha Vantage free tier is 25 req/day),
respect provider throttling. **No full-universe backfill.**

## Provider focus - Alpha Vantage `EARNINGS`

`https://www.alphavantage.co/query?function=EARNINGS&symbol={symbol}` returns `quarterlyEarnings`
with `fiscalDateEnding`, `reportedDate`, `reportedEPS`, `estimatedEPS`, `surprise`,
`surprisePercentage`, `reportTime`. The normalizer flattens these to
`research/data/alphavantage/normalized/earnings_quarterly.csv`. `reportedDate` is the announcement
availability date, so a row with a `reportedDate` is **point-in-time safe**. Alpha Vantage does
**not** serve analyst recommendations / price targets - those remain a Finnhub job.

## Committed-safe artifacts (13)

| Artifact | Contents |
| --- | --- |
| `phase8p_alphavantage_earnings_expansion.json` | main report: key presence, requests, coverage, combined count, decision, leak scan |
| `alphavantage_key_detection_report.csv` | env var, provider, `key_present` (PRESENCE ONLY), `value_read=False` |
| `alphavantage_request_plan.csv` | prioritized plan (FMP-blocked first), `skip_existing`, `will_request`, redacted URL |
| `alphavantage_progress_report.csv` | per-ticker collection status (OK/EMPTY/RATE_LIMITED/INVALID_KEY/...) + quarterly row count |
| `alphavantage_error_report.csv` | per-ticker errors (rate-limit / invalid-key / HTTP / bad-response), redacted URL |
| `alphavantage_storage_manifest.csv` | gitignored raw/normalized paths + row counts (NO payloads), `committed=False` |
| `alphavantage_secret_safety_audit.csv` | key-from-env-only, never printed/written, leak-scan-clean, raw/normalized gitignored |
| `alphavantage_earnings_coverage_report.csv` | per ticker: `has_data`, `row_count`, first/last `reported_date`, has actual/est/surprise, `point_in_time_safe` |
| `fmp_vs_alphavantage_coverage_comparison.csv` | per ticker: fmp_covered / av_covered / combined / newly_covered_by_av |
| `combined_earnings_coverage_report.csv` | per ticker: source label (fmp / alphavantage / fmp+alphavantage) |
| `signal_ready_earnings_manifest.csv` | when threshold reached: 3 earnings-surprise signals x combined tickers |
| `provider_decision_after_alphavantage.csv` | FMP / Alpha Vantage / Finnhub roles + the decision |
| `phase8q_next_plan.json` | the next phase (score, next AV batch, or add Finnhub) |

All artifacts carry **metadata only** - never a payload, never a key. Raw/normalized Alpha Vantage
data lives only under the **gitignored** `research/data/alphavantage/{raw,normalized}/` trees.

## The three earnings-surprise signals this phase gates

`S8P-EARNSURP-RATES` (earnings_surprise x rates_sensitivity), `S8P-EARNSURP-SECTOR`
(x sector_leadership), `S8P-EARNSURP-VOL` (x volatility_sensitivity). All are **earnings-only** -
they do not require analyst data, so Alpha Vantage alone can make them score-ready. The manifest is
emitted only when combined coverage reaches the 20-ticker threshold; no signal is fabricated.

## Decision values

`ALPHAVANTAGE_EARNINGS_SOLVES_COVERAGE`, `READY_FOR_PROVIDER_EXPANDED_SIGNAL_SCORING`,
`READY_FOR_NEXT_ALPHAVANTAGE_BATCH`, `NEEDS_SECOND_PROVIDER_FOR_ANALYST_DATA`,
`BLOCKED_MISSING_ALPHAVANTAGE_KEY`, `ALPHAVANTAGE_RATE_LIMITED`,
`ALPHAVANTAGE_REJECTED_INSUFFICIENT_COVERAGE`, `ERROR`.

The report carries both a primary `decision` (these eight) and a `signal_readiness_decision`. When
combined coverage reaches the threshold, `decision = ALPHAVANTAGE_EARNINGS_SOLVES_COVERAGE` and
`signal_readiness_decision = READY_FOR_PROVIDER_EXPANDED_SIGNAL_SCORING`.

## Current decision (offline, no key in this shell)

- Alpha Vantage key present: **No** -> decision **`BLOCKED_MISSING_ALPHAVANTAGE_KEY`**.
- FMP covered 8 / blocked 9; combined coverage **8 / 20**; threshold **not** reached.
- still missing: ABT, ACN, ADI, ADP, AMAT, AMGN, AON, APD, APH.
- Second provider needed for the analyst families regardless: **Finnhub** (`FINNHUB_API_KEY`).

## Secret discipline (hard rules)

- `ALPHAVANTAGE_API_KEY` is read **only** from the environment, never printed, never written. The
  key-detection report records **presence only** (`value_read=False`).
- Every persisted URL strips the secret query parameter entirely and appends a placeholder, so no
  committed artifact contains `apikey=`. A leak scan over the written artifacts confirms
  `secret_safety_leak_scan_clean`.
- Default run is **offline** (no network). A live collection needs `--live` **and** the key. Tests
  inject a `transport` and never touch a key or the network.
- Raw + normalized payloads are written only under `research/data/alphavantage/{raw,normalized}/`,
  which the phase **force-gitignores** (belt-and-braces `*` + `!.gitignore`, mirroring
  `research/data/fmp/.gitignore`) before any write - so paid data can never be staged.

## Run

```powershell
# Offline plan + decision (default; reads Phase 8-N, no network):
python research/run_phase8p_alphavantage_earnings_expansion.py

# Bounded live collection (requires the key; FMP-blocked tickers first, 25 req cap):
$env:ALPHAVANTAGE_API_KEY = '<PASTE_ALPHAVANTAGE_API_KEY_HERE>'
python research/run_phase8p_alphavantage_earnings_expansion.py --live

# Test (fully offline):
python -m pytest tests/test_phase8p_alphavantage_earnings_expansion.py -q
```

## Constraints honored

Existing installed packages only (stdlib). No package install. No full S&P 500 backfill (bounded by
`max_tickers` / `max_requests`, `skip_existing`, stop on invalid key / rate limit). No API key
printed or written. No raw/normalized provider data committed. No Paper Trader, no GCP, no deploy,
no broker / order / automation logic, no live trading signals. No commit. No push.
