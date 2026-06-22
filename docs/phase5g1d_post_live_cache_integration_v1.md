# Phase 5-G1D — Post-Live Cache Integration and Coverage Rebuild (v1)

**Track A (quant brain). Cache-only rebuild. Preview-only.** Zero network, no API key read or
required, no ticker fetched. No raw file deleted, modified, or untracked. Only committed-safe text
artifacts written. No model trained, no prediction computed, no database touched, no service
restarted, no order placed, no automation enabled, no binary artifact created. Batch 2 NOT run,
Phase 5-G2 NOT run. No commit, no push.

## Why this phase exists

Phase 5-G1C ran a controlled live Alpha Vantage earnings collection (Batch 1) that grew the Phase
3-M raw cache from **50 → 70** ticker payloads (the 20 new names GS, HCA, HD, HON, IBM, ICE, INTC,
INTU, ISRG, ITW, JNJ, JPM, KLAC, KO, LIN, LLY, LMT, LOW, MA, MAR). All 20 raw files exist, are
gitignored, and contain `quarterlyEarnings`. But the **committed-safe** Phase 3-M earnings events /
features artifacts were never rebuilt from the new 70-file cache, so coverage still read **50 / 128**
and the Phase 5-G1 wrapper reported `EXISTING_COLLECTOR_NEEDS_PATCH`. This phase closes that gap by
integrating the cached raw payloads into the committed-safe artifacts — **using local cache only**.

## Did the existing Phase 3-M code already have a cache-only rebuild path?

**Yes.** `research/run_phase3m_earnings_estimates_signal_gate.py` already supports an offline,
cache-only rebuild. When `run()` is called with **no supported provider API key** in the
environment, `detect_providers()` returns `None` and execution takes the *"no provider key"* branch
(`run_phase3m_…py:2054-2090`). That branch:

- never constructs a `ProviderClient`, so **no network code is reachable** (no key required);
- calls `_detect_cached_provider()` + `_load_cached_events()` to read and normalize every raw
  payload already on disk in `research/output/phase3m_earnings_estimates_signal_gate/raw/`;
- rebuilds `earnings_events_universe.csv`, `earnings_features_universe.csv`, and
  `earnings_feature_coverage_by_ticker.csv` from cache via `_finish_collecting()` and **preserves**
  (re-writes from cache, never zeroes) the partial state.

So **no new collector or parser was written**. Phase 5-G1D is a thin wrapper that *forces* that
existing cache-only branch and documents the result.

## What this phase does

`research/run_phase5g1d_post_live_cache_integration.py`:

1. Snapshots `coverage_count_before` (distinct non-SPY tickers in
   `earnings_features_universe.csv`) and `raw_cache_count_before`.
2. Builds a **cache-only environment** by stripping every supported provider key
   (`ALPHAVANTAGE_API_KEY`, `FMP_API_KEY`, `FINNHUB_API_KEY`, `INTRINIO_API_KEY`) from the env it
   hands to the Phase 3-M `run()`. This **guarantees** the no-key cache-only branch — and therefore
   zero network and no key use — regardless of the ambient shell.
3. Parses every cached payload in-memory (read-only) to produce a per-ticker
   `earnings_cache_parse_status.csv` (quarterly-earnings count, provider note/error marker,
   point-in-time usability, newly-collected, integrated).
4. Invokes the Phase 3-M cache-only rebuild (`apply=True`), which rewrites the committed-safe
   artifacts from the 70-file cache, then re-reads `earnings_features_universe.csv` for the
   authoritative `coverage_count_after`.
5. Verifies git hygiene (new raw payloads gitignored, none staged, none deleted/untracked) and
   computes the recommendation.

`--audit-only` (wrapper `apply=False`) runs a projection without rewriting any production artifact;
the test suite uses this so it never overwrites committed state (the Phase 5-G lesson).

## Result (this run)

| Field | Value |
|---|---|
| `coverage_count_before` | **50** |
| `coverage_count_after` | **70** |
| `raw_cache_count_before` / `after` | **70 / 70** (unchanged) |
| `new_cached_tickers_detected` | 20 (the Batch-1 names) |
| `new_tickers_integrated` | 20 (all) |
| `failed_tickers` | **0** |
| `network_used` | **False** |
| `api_key_used` / `api_key_required` | **False / False** |
| `raw_files_deleted` / `untracked` / `staged` | **False / False / False** |
| `new_raw_payload_gitignored` | **True** |
| `recommendation` | **`CACHE_INTEGRATION_SUCCESS_NEEDS_BATCH2`** |
| `phase5g2_allowed` | **False** (70 < 75) |

`committed_safe_artifacts_modified` (all committed-safe text under the Phase 3-M output dir):
`earnings_events_universe.csv`, `earnings_features_universe.csv`,
`earnings_feature_coverage_by_ticker.csv`, `collection_progress.json`, `collection_progress.csv`,
`decision_table.csv`, `provider_access_report.json`. (The IC / combined-panel artifacts stay
header-only because coverage 70 is still below the Phase 3-M signal-gate minimum of 75 — the gate is
deliberately NOT run yet.)

The underlying Phase 3-M `collection_progress` recommendation reads
`EARNINGS_PROVIDER_COLLECTION_BLOCKED_NEEDS_API_KEY` — that is expected and benign: it reflects
"no key present to collect *more* tickers", not a failure. The cache rebuild itself fully succeeded.

## PIT safety

`availability_date` remains the reported/publication date (`reported_date`), never back-dated to the
fiscal quarter end — enforced unchanged by the reused Phase 3-M `_norm_event()`. Events where
`reported_date <= fiscal_date_ending` are flagged not usable. No new label is computed.

## Committed-safe artifacts (this phase)

`research/output/phase5g1d_post_live_cache_integration/`:

- `phase5g1d_post_live_cache_integration.json` — full report (all required fields + safety flags).
- `post_live_cache_integration_audit.csv` — flat check/value/detail audit.
- `earnings_cache_parse_status.csv` — per-ticker parse status for every cached ticker.
- `phase5g1e_next_collection_or_g2_plan.json` — next-step plan (Batch 2 vs Phase 5-G2).

## Recommendation & next step

**`CACHE_INTEGRATION_SUCCESS_NEEDS_BATCH2`.** Coverage is now 70 / 128 — above the prior 50 but
below the 75 minimum required for the Phase 5-G2 event-alpha rerun. **5 more tickers** are needed.

Recommended next phase: **5-G1C Batch 2** — one more controlled live collection batch via the Phase
5-G1 wrapper (≥5 new tickers), then re-run Phase 5-G1D, then Phase 5-G2. Phase 5-G2 is **not yet
allowed**. The exact Batch-2 command (NOT executed here):

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
$env:ALPHAVANTAGE_API_KEY = "<your_key>"
python research\run_phase5g1_earnings_coverage_expansion.py --live --max-new-tickers 20
```

## Run commands (integration + tests)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python research\run_phase5g1d_post_live_cache_integration.py          # apply: rewrite committed-safe artifacts from cache
python research\run_phase5g1d_post_live_cache_integration.py --audit-only   # projection only, no production write
python -m pytest tests\test_phase5g1d_post_live_cache_integration.py -q
```

## Safety contract

Cache-only · zero network / API call · no API key read or required · no ticker fetched · no raw file
deleted, modified, or untracked · newly collected raw payloads stay gitignored and unstaged ·
committed-safe text artifacts only · no model trained / deployed · no Paper Trader / GCP / deploy ·
no orders / broker / automation · no binary artifacts · Batch 2 not run · Phase 5-G2 not run · no
commit · no push.
