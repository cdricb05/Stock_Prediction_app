# Phase 8-V - Combined EODHD Price + Fundamentals Universe Expansion + Robustness Re-test

Status: implemented + tested (16/16 targeted tests, fully offline). Runner:
`research/run_phase8v_combined_eodhd_universe_expansion.py`. An offline rerun against the live local
cache was executed (no key in env -> 0 acquired). **Decision + numbers are in the Status block at the
bottom and in `research/output/phase8v_combined_eodhd_universe_expansion/`.** Nothing committed,
nothing pushed.

## Why this phase exists

Phase 8-T promoted four earnings-surprise-family signals on a 299-ticker scoreable cross-section.
Phase 8-U then proved the binding constraint precisely: of the 294 S&P-500 names missing from the
local price cache, **all 294 also lack cached EODHD earnings**, because the 8-S/8-T earnings cache was
only ever acquired for the already-priced 301 names. Scoreability is the intersection of **PRICE**
coverage with a **point-in-time EARNINGS** event, so acquiring EOD prices alone for the missing names
cannot enlarge the scored universe - they would gain price history but still have zero earnings
events.

Phase 8-V is the honest next step: a **MATCHED, COMBINED** acquisition. For the same missing names it
acquires **both** EODHD EOD prices **and** EODHD fundamentals/earnings, marks a name scoreable only
when both are present, assembles an expanded combined panel, then reruns the **same Phase 8-T scoring
core** on the wider scoreable universe and compares the promoted alpha before vs after.

This is a **robustness-expansion** phase - not provider selection, not price-only, not
fundamentals-only, not Paper-Trader integration. It is preview-only research: it widens the
cross-section a ranking signal is measured on, never orders, never automation, never broker
execution. It does not touch Paper Trader or GCP. Raw + normalized EOD price and fundamentals
payloads stay under the gitignored `research/data/eodhd/{raw,normalized}/` trees.

## Reuse

8-V builds directly on **8-U** (`import run_phase8u_... as u8`): the EOD-price transport, the
expanded-panel builder, the scoring-paths factory and the S&P-500 parser are reused verbatim, and 8-U
itself reuses the 8-T scoring core, the 8-S data layer, and the 8-R EODHD transport / secret
discipline. The fundamentals leg reuses the proven 8-R/8-S fundamentals endpoint, classification
(`r8.classify_fundamentals`), earnings normalization (`r8._normalize_earnings`), persistence
(`r8._persist_raw` / `r8._append_normalized`) and error taxonomy verbatim. The only genuinely new
machinery is the matched two-leg acquisition loop and the combined coverage diff.

## Workflow

1. Read Phase 8-T outputs (promoted baseline) and the Phase 8-U decision (the finding this phase
   acts on).
2. Parse the S&P-500 constituent symbols from the local Wikipedia table (`id="constituents"` only).
3. Diff the S&P names against three caches: local prices, normalized EODHD earnings, raw EODHD
   fundamentals -> `existing_combined_coverage.csv`, `missing_combined_tickers.csv` (a name is
   "missing combined" if it lacks **either** a price **or** an earnings event; each row flags
   `need_price` / `need_fundamentals`).
4. **Bounded MATCHED acquisition** (`--max-tickers 250 --max-requests 1000 --start-date 2016-01-01`,
   skip-existing per leg) for the missing names: for each, fetch the EOD price **and** the
   fundamentals, persist both under the gitignored data tree, and mark the name scoreable only when
   **both** land. Two requests per ticker -> the request budget is double 8-U's. Stops on invalid key
   / plan block (402) / free-tier block (403, EOD-only) / consecutive rate-limits.
5. Build the **expanded price panel** (reuses 8-U; new EOD bars merged with the base cache,
   benchmark merged so index-relative returns stay valid). The shared data tree now also carries the
   newly acquired earnings/fundamentals.
6. **Rerun the 8-T scoring core** BEFORE (base panel) and AFTER (expanded panel): identical
   point-in-time event table + extended features + 6-cycle / 43-scenario battery + 25 bps cost,
   subperiod-stability, placebo and Benjamini-Hochberg gates. On the base panel the new names have
   earnings but no price -> excluded; on the expanded panel they have both -> scored. (If no new
   prices landed, the panel is unchanged and AFTER reuses BEFORE - no double work.)
7. **Compare** scoreable tickers, PIT events, per-scenario IC / spread / **net-of-25bps and
   net-of-50bps** / subperiod stability / promotion status -> `before_after_scoreable_coverage.csv`,
   `before_after_alpha_comparison.csv`, `robustness_delta_report.csv`,
   `expanded_universe_scenario_scoreboard.csv`, `expanded_universe_{promoted,rejected}_signals.csv`,
   plus `expanded_scoreable_coverage.csv` naming the names that newly entered the scored set.
8. Emit one terminal decision.

## Terminal decisions

`EXPANDED_UNIVERSE_ALPHA_CONFIRMED` (cross-section widened **and** every 8-T promoted signal still
clears the gate, focus signals `surprise_sector_neutral` / `surprise_x_quality` survive, none
dropped) | `EXPANDED_UNIVERSE_WEAKENS_ALPHA` (widened but promoted alpha degraded) |
`READY_FOR_NEXT_COMBINED_BATCH` (cross-section did **not** grow - acquire the next bounded matched
batch) | `HARD_BLOCKER_REQUIRES_USER_ACTION` (invalid key / plan block / free-tier block /
rate-limit) | `ERROR`.

## Committed-safe artifacts (18 required)

`research/output/phase8v_combined_eodhd_universe_expansion/` (metadata only - never a payload, never
a key): `phase8v_combined_eodhd_universe_expansion.json`, `matched_acquisition_universe.csv`,
`existing_combined_coverage.csv`, `missing_combined_tickers.csv`, `combined_acquisition_progress.csv`,
`raw_price_storage_manifest.csv`, `raw_fundamentals_storage_manifest.csv`,
`normalized_price_storage_manifest.csv`, `normalized_fundamentals_storage_manifest.csv`,
`expanded_scoreable_coverage.csv`, `before_after_scoreable_coverage.csv`,
`before_after_alpha_comparison.csv`, `expanded_universe_scenario_scoreboard.csv`,
`expanded_universe_promoted_signals.csv`, `expanded_universe_rejected_signals.csv`,
`robustness_delta_report.csv`, `phase8w_next_plan.json`, `secret_safety_audit.csv`. (Plus harmless
extras: `phase8v_run_log.csv`, `sp500_name_list_extraction.csv`, `expanded_price_panel_manifest.csv`.)

Raw + normalized EOD price AND fundamentals payloads (incl. the expanded panel) live ONLY under the
gitignored `research/data/eodhd/{raw,normalized}/{eod_prices,fundamentals}/` trees.

## Secret discipline

`EODHD_API_KEY` is read ONLY from the environment, never printed, never written to disk. Every
persisted URL is redacted; a leak scan over the committed artifacts confirms it is clean. Both legs
reuse the 8-R host allowlist, redaction and error taxonomy verbatim.

## Run

```powershell
# Offline rerun on whatever price+earnings are already cached (no network, no key):
python research/run_phase8v_combined_eodhd_universe_expansion.py

# Bounded live MATCHED acquisition + rerun (requires a PAID EODHD_API_KEY with fundamentals):
$env:EODHD_API_KEY = '<PAID_EODHD_KEY>'
python research/run_phase8v_combined_eodhd_universe_expansion.py --live --max-tickers 250 \
    --max-requests 1000 --start-date 2016-01-01

# Test (fully offline; injected combined transport, no key, no network):
python -m pytest tests/test_phase8v_combined_eodhd_universe_expansion.py -q
```

## Constraints honored

Existing installed packages only (numpy + pandas). No package install. Bounded acquisition
(max_tickers / max_requests, skip-existing per leg, stop on invalid key / plan block / free-tier /
rate-limit). No raw/normalized provider data committed (force-gitignored). No API key printed or
written. No Paper Trader, no GCP, no deploy, no broker / order / automation logic. No full Phase-8
regression - targeted tests only. No commit. No push.

## Status (offline rerun)

Offline rerun executed against the live local cache on `as_of = 2026-06-26` (no key in env).

**Terminal decision: `READY_FOR_NEXT_COMBINED_BATCH`.**

- **S&P-500 list:** 504 constituents parsed. **Priced:** 301. **Scoreable-ready (price + earnings):**
  300 by cache (299 actually scored - one priced+earnings name lacks forward-price overlap).
  **Missing combined:** 294 - and **all 294 need BOTH a price and fundamentals** (`need_price = 294`,
  `need_fundamentals = 294`), confirming the Phase 8-U finding exactly.
- **Acquisition:** offline (no key) -> 0 requests, 0 prices, 0 fundamentals, 0 matched-scoreable. The
  expanded panel equals the base cache.
- **Rerun (BEFORE == AFTER, unchanged panel):** 29,032 usable PIT events / **299 scoreable tickers**;
  6 cycles, 43 scenarios, **4 promoted, 39 rejected** - an exact reproduction of the Phase 8-T
  campaign (a clean cross-check that the reused scoring core is identical).
- **8-T focus signals:** `surprise_sector_neutral` survives = **True**; `surprise_x_quality` survives
  = **True** (trivially - the cross-section did not change). **No new alpha** appeared.
- **Why not a wider universe:** no data was acquired (offline). Unlike 8-U, the runner is now built to
  enlarge the scored set the moment a name gains **both** legs; the test suite proves this with a
  partial base cache + an injected matched transport (`EXPANDED_UNIVERSE_ALPHA_CONFIRMED`, before 12
  -> after 20).
- **Secret discipline:** leak scan clean over 19 committed-safe files; key never present / printed /
  written; `research/data/eodhd/{raw,normalized}/{eod_prices,fundamentals}/` force-gitignored.
- **Safety:** preview-only; no orders, no automation, no broker, no Paper Trader / GCP touch; not
  committed, not pushed.

**Exact next step:**
```powershell
$env:EODHD_API_KEY = '<PAID_EODHD_KEY>'; python research/run_phase8v_combined_eodhd_universe_expansion.py `
  --live --max-tickers 250 --max-requests 1000 --start-date 2016-01-01
```
This acquires the next bounded MATCHED price+fundamentals batch for the 294 missing names; each name
becomes scoreable only once both its EOD price and its point-in-time earnings event are cached.
