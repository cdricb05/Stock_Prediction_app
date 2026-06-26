# Phase 8-U - EODHD EOD Price-Universe Expansion + Robustness Re-test

Status: implemented + tested (13/13 targeted tests, fully offline). Runner:
`research/run_phase8u_eodhd_price_universe_expansion.py`. An offline rerun against the live local
cache was executed (no key in env -> 0 prices acquired). **Decision + numbers are in the Status
block at the bottom and in `research/output/phase8u_eodhd_price_universe_expansion/`.** Nothing
committed, nothing pushed.

## Why this phase exists

Phase 8-T promoted four earnings-surprise-family signals but its scoreable cross-section was capped
at 299 tickers because the local OHLCV cache only covers the 301-name phase7i priced universe. The
daemon found a ~503-name S&P-500 constituent list but no local prices for those names. Phase 8-U is
the bounded **price-universe expansion**: parse the S&P-500 list, diff it against the local price
cache, acquire EODHD adjusted EOD prices for the **missing** names (bounded, skip-existing), build an
expanded price panel, then rerun the **same Phase 8-T scoring core** on the wider universe and
compare the promoted alpha before vs after.

This is a **robustness-expansion** phase - not provider selection, not Paper Trader integration. It
is preview-only research: it widens the cross-section a ranking signal is measured on, never orders,
never automation, never broker execution. It does not touch Paper Trader or GCP. Raw + normalized
EOD price payloads stay under the gitignored `research/data/eodhd/{raw,normalized}/eod_prices/`.

## The honest structural finding (verified against the live caches)

Scoreability = **PRICE coverage ∩ a point-in-time EARNINGS event**. The Phase 8-S/8-T earnings cache
was acquired only for the already-priced 301 names, so:

- 504 S&P-500 constituents parsed; 301 priced; **294 missing from the price cache**.
- **All 294 missing names also lack cached EODHD earnings** (`missing_with_earnings_cache = 0`).

Therefore acquiring **EOD prices alone** for the missing names cannot enlarge the scoreable
cross-section - those names would gain price history but still have **zero** point-in-time earnings
events. This directly refines the prior checkpoint's premise ("the next bottleneck is price coverage,
not fundamentals coverage"): price coverage **is** a bottleneck, but relieving it alone does nothing
- the binding constraint is the **joint** price+earnings coverage of the *same* new names. The honest
next batch must pair an EOD-price acquisition with an 8-S fundamentals top-up for those names.

The runner is built to detect and exploit a genuine expansion automatically: if prices land for names
that already have cached earnings, the rerun picks them up and the before/after comparison becomes
non-trivial (the test suite exercises exactly this `EXPANDED_UNIVERSE_ALPHA_CONFIRMED` path with a
deliberately partial base cache + pre-seeded earnings).

## Workflow

1. Read Phase 8-T outputs (`final_research_decision.json` + main report) for the promoted baseline.
2. Parse the S&P-500 constituent symbols from the local Wikipedia table (`id="constituents"` only).
3. Diff against the local price cache -> `existing_price_coverage.csv`, `missing_price_tickers.csv`
   (each missing row flags whether it already has cached earnings).
4. **Bounded EODHD EOD acquisition** (`--max-tickers 250 --max-requests 500 --start-date 2016-01-01`,
   skip-existing) for the missing names. Reuses the proven 8-R host-allowlist / URL-redaction /
   error-taxonomy / gitignore discipline; raw payloads -> `raw/eod_prices/`, normalized per-ticker
   panels -> `normalized/eod_prices/`. Stops on invalid key / plan block / consecutive rate-limits.
5. Build the **expanded price panel** = existing cache + newly acquired EOD bars (benchmark merged
   from the base cache so index-relative returns stay valid), written under the gitignored
   `normalized/eod_prices/expanded_price_panel.csv`.
6. **Rerun the 8-T scoring core** BEFORE (base panel) and AFTER (expanded panel): identical
   point-in-time event table + extended features + 6-cycle / 43-scenario battery + 25 bps cost,
   subperiod-stability and placebo gates. (If nothing was acquired, the panel is unchanged and AFTER
   reuses BEFORE - no double work.)
7. **Compare** scoreable tickers, PIT events, per-scenario IC / spread / net-of-25bps, subperiod
   stability, and promotion status -> `before_after_scoreable_coverage.csv`,
   `before_after_alpha_comparison.csv`, `robustness_delta_report.csv`,
   `expanded_universe_scenario_scoreboard.csv`, `expanded_universe_{promoted,rejected}_signals.csv`.
8. Emit one terminal decision.

## Terminal decisions

`EXPANDED_UNIVERSE_ALPHA_CONFIRMED` (cross-section widened **and** every 8-T promoted signal still
clears the gate, focus signals `surprise_sector_neutral` / `surprise_x_quality` survive, none
dropped) | `EXPANDED_UNIVERSE_WEAKENS_ALPHA` (widened but promoted alpha degraded) |
`READY_FOR_NEXT_PRICE_BATCH` (cross-section did **not** grow - prices missing/0 or the newly priced
names lack earnings; acquire the next bounded price+fundamentals batch) |
`HARD_BLOCKER_REQUIRES_USER_ACTION` (invalid key / plan block / rate-limit) | `ERROR`.

## Committed-safe artifacts

`research/output/phase8u_eodhd_price_universe_expansion/` (metadata only - never a payload, never a
key): `phase8u_eodhd_price_universe_expansion.json`, `phase8u_run_log.csv`,
`sp500_name_list_extraction.csv`, `existing_price_coverage.csv`, `missing_price_tickers.csv`,
`price_acquisition_progress.csv`, `raw_price_storage_manifest.csv`,
`normalized_price_storage_manifest.csv`, `expanded_price_panel_manifest.csv`,
`before_after_scoreable_coverage.csv`, `before_after_alpha_comparison.csv`,
`expanded_universe_scenario_scoreboard.csv`, `expanded_universe_promoted_signals.csv`,
`expanded_universe_rejected_signals.csv`, `robustness_delta_report.csv`, `phase8v_next_plan.json`,
`secret_safety_audit.csv`.

Raw + normalized EOD price payloads (incl. the expanded panel) live ONLY under the gitignored
`research/data/eodhd/{raw,normalized}/eod_prices/` trees.

## Secret discipline

`EODHD_API_KEY` is read ONLY from the environment, never printed, never written to disk. Every
persisted URL is redacted; a leak scan over the committed artifacts confirms it is clean. The EOD
transport reuses the 8-R host allowlist, redaction, and error taxonomy verbatim.

## Run

```powershell
# Offline rerun on whatever prices are already cached (no network, no key):
python research/run_phase8u_eodhd_price_universe_expansion.py

# Bounded live EOD-price acquisition + rerun (requires a paid EODHD_API_KEY in the env):
$env:EODHD_API_KEY = '<PAID_EODHD_KEY>'
python research/run_phase8u_eodhd_price_universe_expansion.py --live --max-tickers 250 \
    --max-requests 500 --start-date 2016-01-01

# Test (fully offline; injected transport, no key, no network):
python -m pytest tests/test_phase8u_eodhd_price_universe_expansion.py -q
```

## Constraints honored

Existing installed packages only (numpy + pandas). No package install. Bounded acquisition
(max_tickers / max_requests, skip-existing, stop on invalid key / plan block / rate-limit). No
raw/normalized provider data committed (force-gitignored). No API key printed or written. No Paper
Trader, no GCP, no deploy, no broker / order / automation logic. No full Phase-8 regression - targeted
tests only. No commit. No push.

## Status (offline rerun)

Offline rerun executed against the live local cache on `as_of = 2026-06-26` (no key in env).

**Terminal decision: `READY_FOR_NEXT_PRICE_BATCH`.**

- **S&P-500 list:** 504 constituents parsed. **Priced:** 301. **Missing from the price cache:** 294 -
  **all 294 also lack cached EODHD earnings** (the binding-constraint finding above).
- **Acquisition:** offline (no key) -> 0 requests, 0 prices acquired. The expanded panel equals the
  base cache.
- **Rerun (BEFORE == AFTER, unchanged panel):** 29,032 usable PIT events / **299 scoreable tickers**;
  6 cycles, 43 scenarios, **4 promoted, 39 rejected** - an exact reproduction of the Phase 8-T
  campaign (a clean cross-check that the reused scoring core is identical).
- **8-T focus signals:** `surprise_sector_neutral` survives = **True**; `surprise_x_quality` survives
  = **True** (trivially - the cross-section did not change). **No new alpha** appeared.
- **Why not a wider universe:** no EOD prices were acquired (offline); and even a successful
  price-only acquisition for the 294 missing names would not make them scoreable because they have no
  cached earnings. The honest next batch must acquire **EOD prices AND EODHD fundamentals** for the
  same names.
- **Secret discipline:** leak scan clean over the committed-safe files; key never present/printed/
  written; `research/data/eodhd/{raw,normalized}/eod_prices/` force-gitignored.
- **Safety:** preview-only; no orders, no automation, no broker, no Paper Trader / GCP touch; not
  committed, not pushed.

**Exact next step:**
```powershell
$env:EODHD_API_KEY = '<PAID_EODHD_KEY>'; python research/run_phase8u_eodhd_price_universe_expansion.py `
  --live --max-tickers 250 --max-requests 500 --start-date 2016-01-01
```
paired with an 8-S fundamentals top-up for the same 294 names so they become scoreable (Phase 8-V).
