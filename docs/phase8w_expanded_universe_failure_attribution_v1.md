# Phase 8-W - Expanded Universe Failure Attribution + Constrained-Alpha Salvage

Status: implemented + tested (11/11 targeted tests, fully offline) and **executed against the live
545-ticker expanded universe**. Runner: `research/run_phase8w_expanded_universe_failure_attribution.py`.
**Decision + numbers are in the Status block at the bottom and in
`research/output/phase8w_expanded_universe_failure_attribution/`.** Nothing committed, nothing pushed.

## Why this phase exists

Phase 8-V acquired a MATCHED EODHD price+fundamentals batch and grew the scoreable cross-section from
**299 to 545 tickers** (29,032 -> 38,725 point-in-time earnings events). But the four Phase 8-T
promoted earnings-surprise signals all degraded on the wider universe (**4 promoted -> 0 promoted**):
`surprise_sector_neutral` IC 0.0514 -> 0.0202 (t 2.96 -> 1.43), `surprise_x_quality` IC 0.0470 ->
0.0249 (t 2.97 -> 1.78). The before/after comparison showed a near-uniform **halving** of IC across
the whole earnings family - the signature of DILUTION, not a localized data error.

Phase 8-W does **not** acquire data and does **not** touch Paper Trader. It is an **attribution +
salvage** phase: rebuild the SAME expanded event table, split it into the original 299-ticker cohort
vs the 246 newly-scoreable names, re-measure every promoted signal on old / new / combined, attribute
the degradation across sector / liquidity / event-history / subperiod / data-quality, and finally test
a battery of **constrained variants** to decide whether a robust constrained alpha survives.

## Reuse

The scoring core is reused verbatim from Phase 8-T: `evaluate_ext` (monthly rank-IC, quintile
long-short spread, 25/50 bps net-of-cost, subperiod stability, placebo/challenge), `gate_reasons`
(the exact promotion gate), `scenario_battery`, `prepare_signals_ext`, over Phase 8-S's point-in-time
`build_event_table` and Benjamini-Hochberg control. **Every constrained variant is just the SAME 8-T
evaluation battery run on a filtered slice of the SAME `ev`, gated by the SAME promotion gate** - so a
"surviving" variant clears exactly the bar the 8-T promotions did. The phase reads only cached data
(the gitignored 8-V expanded panel + EODHD earnings cache); it never calls a network or a key.

## Workflow

1. Read the Phase 8-V report (`newly_scoreable_tickers` = the new cohort; the before/after verdict).
2. Rebuild the expanded event table from the same gitignored panel + earnings cache the 8-V "after"
   run used; tag each event `old` (original 299) or `new` (246 newly-scoreable).
3. **Cohort attribution**: re-measure all seven target signals
   (`surprise_sector_neutral`, `surprise_x_quality`, `positive_surprise_asymmetry`,
   `surprise_magnitude`, `earnings_acceleration`, `balance_sheet_strength`, `quality_composite`)
   separately on old / new / combined.
4. **Dimension attribution** of the primary focus signal (`surprise_sector_neutral`) by sector
   (own-IC + leave-one-sector-out delta + new-cohort share), liquidity (per-ticker dollar-volume
   quartiles), event-history depth, subperiod (pre/post-2020 + rate / drawdown / oil regimes), and
   data-quality (standardized-earnings and fundamentals availability).
5. **Constrained variants** (nine salvage hypotheses), each evaluated by the full 8-T gate with BH
   control across the set: old-cohort-only, high-liquidity-only, top-sector-only (sector-labelled),
   sector-neutral-within-old-cohort, quality-gated-surprise, large-surprise-only, post-2020-only,
   exclude-weak-sectors, require-minimum-event-history.
6. Emit one terminal decision + a preview-only Phase 8-X next plan.

A "weak sector" = removing it RAISES the combined IC **and** its own within-sector IC is itself below
the +0.03 promotion threshold (so a strong sector like Information Technology is never flagged on a
marginal leave-one-out alone). Liquidity uses a per-ticker mean-dollar-volume proxy sourced from the
base cache (old names) + the per-ticker normalized EOD CSVs (new names), because the scoring pipeline
and the 8-V expanded panel both drop `dollar_volume`.

## Terminal decisions

`CONSTRAINED_ALPHA_SURVIVES` (>=1 constrained variant clears the full 8-T gate) |
`ALPHA_REJECTED_AFTER_EXPANSION` (no variant survives and the original-cohort edge is not robust on
its own) | `NEEDS_ADDITIONAL_DIAGNOSTIC` (original cohort still strong but no variant fully clears the
multi-test gate; or no event table could be built) | `ERROR`.

## Committed-safe artifacts (14 required)

`research/output/phase8w_expanded_universe_failure_attribution/` (metadata only - never a payload,
never a key): `phase8w_expanded_universe_failure_attribution.json`,
`cohort_performance_comparison.csv`, `old_vs_new_signal_attribution.csv`, `sector_attribution.csv`,
`liquidity_bucket_attribution.csv`, `event_history_attribution.csv`, `subperiod_attribution.csv`,
`data_quality_attribution.csv`, `constrained_variant_scoreboard.csv`,
`constrained_promoted_signals.csv`, `constrained_rejected_signals.csv`, `final_research_decision.json`,
`phase8x_next_plan.json`, `secret_safety_audit.csv`. (Plus a harmless `phase8w_run_log.csv`.) No raw
or normalized provider data is written by this phase; the expanded panel + earnings it reads remain
under the gitignored `research/data/eodhd/` trees.

## Run

```powershell
# Offline attribution over the cached 8-V expanded panel + EODHD earnings (no network, no key):
python research/run_phase8w_expanded_universe_failure_attribution.py

# Test (fully offline; synthetic old-drift / new-flat cohorts, no key, no network):
python -m pytest tests/test_phase8w_expanded_universe_failure_attribution.py -q
```

## Constraints honored

Existing installed packages only (numpy + pandas). No package install. No external API call, no key
read or printed. No raw/normalized provider data written or committed. No Paper Trader, no GCP, no
deploy, no broker / order / automation logic. No full Phase-8 regression - targeted tests only. No
commit. No push.

## Status (live run)

Executed against the live gitignored 8-V expanded panel on `as_of = 2026-06-26`.

**Terminal decision: `CONSTRAINED_ALPHA_SURVIVES`.**

- **Universe:** 545 scoreable tickers / 38,725 PIT events. Cohorts: **old 299 tickers / 29,032
  events**, **new 246 tickers / 9,693 events**.
- **Cohort attribution (the headline finding):** every earnings-surprise signal carries its drift in
  the OLD cohort and is *anti-signal* in the NEW cohort -

  | signal | IC old | IC new | IC combined | t old |
  |---|---|---|---|---|
  | surprise_sector_neutral | **0.0514** | **-0.0076** | 0.0202 | 2.96 |
  | surprise_x_quality | 0.0471 | -0.0065 | 0.0249 | 2.99 |
  | positive_surprise_asymmetry | 0.0488 | -0.0088 | 0.0246 | 3.08 |
  | surprise_magnitude | 0.0411 | -0.0085 | 0.0181 | 2.68 |

  The old-cohort IC **exactly reproduces the original 8-T numbers**, confirming the attribution is
  faithful; the new cohort's negative IC halves the pooled IC. **The expansion did not break the
  signal in the original names - it added low-information names that average it down.**
- **Main reason alpha weakened:** dilution by a low-information new cohort, corroborated on every
  dimension: the new names sit overwhelmingly in the **least-liquid** quartiles (focus IC is +0.0472
  / t 2.25 in the top-liquidity quartile vs ~0 in the bottom), in **fundamentals-absent** /
  **SUE-missing** buckets (fundamentals-absent IC -0.137, t -2.85), and in **Unknown-sector** /
  weak-sector buckets. Weak (dilutive, sub-threshold) sectors: Consumer Staples, Energy, Health Care,
  Unknown.
- **Constrained salvage - 4 of 9 variants clear the full 8-T gate:** `old_cohort_only` (IC 0.0514,
  t 2.96, net-25bps +0.0058, subperiod-stable, BH-significant), `sector_neutral_within_old_cohort`
  (same), `top_sector_only` (Information Technology; IC 0.0961, t 2.46, net-25bps +0.0106; labelled
  sector-constrained), and `high_liquidity_only` (most-tradable names). These are **CONSTRAINED /
  exploratory** reads on a restricted cross-section, not unconditional alpha.
- **Does `surprise_sector_neutral` survive?** Yes - but only constrained to the original cohort (or
  high-liquidity / single-sector). **`surprise_x_quality`?** Survives only as the quality-gated read
  inside the same constrained slices; it does not clear the gate on the full expanded universe.
- **Safety:** preview-only; no orders, no automation, no broker, no Paper Trader / GCP touch; no
  network, no key. Leak scan clean over 15 committed-safe files. Not committed, not pushed.

**Exact next step:**
Carry the surviving CONSTRAINED variant(s) into the Paper Trader daily-review cockpit as
**preview-only** ranking ideas, clearly labelled constrained/exploratory (manual review; no orders),
and re-validate on the next data refresh before any reliance. If instead deepening the diagnostic is
preferred, Phase 8-X should acquire longer earnings/price history for the newly-added names (so their
SUE/quality features populate) and re-attribute.
