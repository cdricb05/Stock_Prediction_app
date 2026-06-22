# Phase 5-G1 — Earnings Event Coverage Expansion Plan + Controlled Collector (v1)

**Track A (quant brain) research. Dry-run-first. Preview-only.** No new provider, no new
paid data, no provider shopping, no new alpha feature, no coverage faking, no model
training, no deployment, no Paper Trader / GCP, no orders / broker / automation, no binary
artifacts, no package installs. In dry-run the network is never used and no API key is
required or read.

## Why this phase exists

Phase 5-G found the lineage's **first new-information win**: adding earnings-surprise /
post-earnings-drift (PEAD) features produced a real, leakage-clean, out-of-sample
**incremental** rank-IC edge of **+0.0091** over the Phase 5-C price champion
(`top_quintile_score_model`) on the covered names. But that edge was measured on only
**50 of the 128** Phase 5-C universe tickers — below the project's own **75-ticker**
event-signal gate — and there is still **no point-in-time analyst-revision series**. So
Phase 5-G was explicitly *not* shadow-ready.

This phase does the **one safe operational step** that unblocks a broader re-test: plan,
and (only on `--live`) perform, controlled PIT-safe earnings coverage expansion to
≥75/128 by **reusing the existing collector**. It answers: *can we safely reuse it without
deleting data, corrupting caches, making uncontrolled calls, or committing raw payloads?*
Answer: **yes** — see the inventory and safety audit below.

## 1 — Collector inventory

| Script | Role | Network | Reusable |
|---|---|---|---|
| `research/run_phase3m_earnings_estimates_signal_gate.py` | **The** earnings EPS estimate/actual/surprise collector | yes | **yes** |
| `research/run_phase3s_event_signal_readiness_gate.py` | event-signal readiness / coverage **status** gate | no (inspects local cache only) | no |
| `research/analyze_phase3e_fundamentals_earnings_feasibility.py` | feasibility + provider-requirements template | no | no |

**Phase 3-M collector properties (the one we reuse):**

- **Provider / source:** Alpha Vantage (priority order `alpha_vantage > fmp > finnhub >
  intrinio`), selected from an env key that is already present. The existing 50-ticker
  cache was collected via Alpha Vantage.
- **API key:** **required** for network collection, read from environment variables only
  (`ALPHAVANTAGE_API_KEY`, …). Keys are **never** printed, written to any artifact, or
  hardcoded; persisted endpoint strings are redacted; provider messages are sanitized.
- **Resumable:** **yes** — cache-first, with a `collection_progress.json` state file, a
  per-run network budget (default 20), a hard `MAX_TOTAL_PROVIDER_REQUESTS=200` cap, and a
  same-day provider-limit guard that avoids re-spending an already-exhausted daily quota.
- **Raw/cache location:**
  `research/output/phase3m_earnings_estimates_signal_gate/raw/{provider}_{TICKER}.json`.
  Each file holds **only the provider response body** (EPS facts), never the request URL
  or key.
- **Raw cache gitignored:** **NO** (finding). There is no repo-root `.gitignore`, and the
  50 raw Alpha Vantage payloads are currently **tracked in git**. They are plain factual
  EPS estimate/actual/surprise numbers (committed-safe, no secrets), so this is acceptable
  but is flagged in the safety audit. If raw payloads should not be versioned, add
  `research/output/**/raw/` to a `.gitignore` before the next live collection.
- **Rate-limit behaviour:** per-provider minimum request interval (Alpha Vantage ≈15 s),
  per-run budget, total cap, same-day guard, and graceful `_ProviderLimited` handling
  (records the limit and stops rather than crashing).
- **Existing data safety:** cache-first and **add-only**. With no key it rebuilds truthful
  partial state from the cache and preserves it (never zeroes the events/features CSVs);
  with a key it only fetches **missing** tickers and writes **new** raw files. Nothing is
  deleted or overwritten.

**Conclusion: the collector is safely reusable as-is.** No patch is required.

## 2 — Current coverage (vs the Phase 5-C universe)

The reference universe is the **128** Phase 5-C tickers (read from
`research/output/phase5c_oos_scores_sample.csv`). Coverage is read from the Phase 3-M
PIT feature file and is fully consistent with the on-disk raw cache:

| Metric | Value |
|---|--:|
| Universe | 128 |
| Covered (PIT-usable earnings features) | **50** |
| Coverage fraction | 39.06% |
| Missing | 78 |
| Target minimum (event-signal gate) | 75 |
| **New tickers needed to reach 75** | **25** |
| New tickers needed for full 128 | 78 |

The covered 50 are the alphabetically-first large caps (AAPL→GOOGL); collection was
rate-limited at the provider before reaching the 75 gate (`collection_progress.json`
records `provider_limit_hit: true`, est. 2 more runs at 20/run).

## 3 — Missing-ticker plan

Deterministic alphabetical order over the missing 78 (`earnings_missing_ticker_plan.csv`).
The **first batch to reach 75** (the 25 names marked `needed_for_75 = True`) is:

```
GS, HCA, HD, HON, IBM, ICE, INTC, INTU, ISRG, ITW, JNJ, JPM, KLAC, KO, LIN,
LLY, LMT, LOW, MA, MAR, MCD, MCO, MDLZ, MDT, META
```

The **full batch to reach 128** is all 78 missing tickers (GS…ZTS). At the Alpha Vantage
free-tier budget (~20 new tickers/run), reaching 75 takes ≈2 controlled runs and full
coverage ≈4 runs, spread across days to respect the daily quota.

## 4 — Controlled expansion wrapper

`research/run_phase5g1_earnings_coverage_expansion.py`:

- **Dry-run by default.** Makes no network call, needs no key, writes only into
  `research/output/phase5g1_earnings_coverage_expansion/`.
- **`--live` required** for any provider call. Live mode reuses the Phase 3-M
  `ProviderClient` (cache-first, throttled) and adds new raw files for **missing** tickers
  only, into the existing Phase 3-M cache — never deleting or overwriting.
- **`--max-new-tickers N`** caps new tickers per run (default 20).
- **`--ticker T`** (repeatable) / **`--batch-file path`** restrict collection to a subset
  (intersected with the missing set).
- **`--out-dir`** redirects all output (tests use a temp dir, so a test run can never
  touch production — the Phase 5-G determinism lesson is applied here).
- Produces the **exact future live command** but never executes it without `--live`.

### Output directory & committed-safe artifacts

`research/output/phase5g1_earnings_coverage_expansion/`:

- `phase5g1_earnings_coverage_expansion.json` — main result (all required fields).
- `earnings_coverage_gap_report.csv` — coverage metrics.
- `earnings_missing_ticker_plan.csv` — 78 missing tickers, first-25 flagged for the gate.
- `earnings_collector_inventory.csv` — the collector inventory above.
- `earnings_collection_safety_audit.csv` — the safety checks below.
- `phase5g2_event_alpha_rerun_plan.json` — gated Phase 5-G2 re-run plan.

## Recommendation

**`READY_FOR_CONTROLLED_EARNINGS_COLLECTION`.** The existing Phase 3-M collector is safely
reusable to expand PIT-safe coverage from 50→75(→128). No collector patch is needed; no new
provider or paid data is introduced. Recommended next phase: **5-G2** — re-run the Phase
5-G event-alpha on the expanded covered cross-section once coverage ≥75 (revision features
stay omitted until a real PIT source exists; survivorship still unresolved).

### Is a controlled live collection safe? Yes — when run deliberately

It reuses a tested, cache-first, network-budgeted, key-from-env collector that never
deletes data. The only repo-hygiene caveat is that raw payloads are currently git-tracked
(not gitignored); they are factual EPS numbers and committed-safe, but add a `.gitignore`
entry first if you prefer not to version them.

### Exact future live command (PowerShell — NOT executed here)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
$env:ALPHAVANTAGE_API_KEY = "<your key>"
python research\run_phase5g1_earnings_coverage_expansion.py --live --max-new-tickers 20
# repeat across days until coverage >= 75, then re-run the status-only dry-run to confirm.
```

Equivalent via the bare Phase 3-M resumable collector:

```powershell
$env:ALPHAVANTAGE_API_KEY = "<your key>"
$env:PHASE3M_MAX_NETWORK_REQUESTS_PER_RUN = "20"
python research\run_phase3m_earnings_estimates_signal_gate.py
```

## Run commands (dry-run + tests)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python research\run_phase5g1_earnings_coverage_expansion.py
python -m pytest tests\test_phase5g1_earnings_coverage_expansion.py -q
```

## Safety contract

Dry-run default · `--live`-gated network · no new provider / no provider shopping / no FMP
/ no SimFin expansion · no new paid data · no package installs · existing earnings data and
raw cache preserved (add-only) · committed-safe text artifacts only · no Paper Trader / GCP
/ deploy · no orders / broker / automation · no binary artifacts · no commit · no push.
