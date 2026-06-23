# Phase 7-H — TTM Fundamental Signal Retest (v1)

**Status:** research / signal-evaluation exercise only.
**Recommendation:** `TTM_SIGNAL_RELIABILITY_WEAK`.
**Did dense TTM fundamentals improve signal quality? → NO.**
**Not** a trading system, production model, order/execution automation, factor-weight
optimization, factor-sign flipping, regime-throttle activation, universe broadening, or
data collection. No live or paid data calls. Nothing committed or pushed. The D: price
panel is read-only; nothing was written to D:.

Governed by `docs/project_charter_sp500_multifactor_ranking_v1.md`.

---

## The one question this phase answers

Phase 7-G proved that a materially **denser** point-in-time quarterly/TTM fundamental
panel can be built locally (by de-cumulating YTD 10-Q flows) and authorized a
**fundamental-density-only** retest of the Phase 7-F signal. This phase is that retest.
It builds the dense TTM panel, constructs TTM value/quality/growth factors, preserves the
Phase 7-F price-momentum bucket, keeps low-volatility as a risk descriptor only, combines
**equal weight**, and grades the final composite through the **unmodified Phase 7-B
harness** against the Phase 7-F upgraded composite baseline.

> **Did dense TTM fundamentals improve signal quality? — NO.**
> The TTM composite scored **mean rank IC = +0.013954 (t = 0.92)**, *below* the Phase 7-F
> baseline of **+0.017726 (t = 1.25)** — incremental **−0.003772**, t-change **−0.32**. It
> is positive but does not beat 7-F, so the verdict is `TTM_SIGNAL_RELIABILITY_WEAK`.

---

## Headline comparison

| Series | mean rank IC | IC t-stat | n_periods | incremental vs 7-F |
|---|---:|---:|---:|---:|
| **Phase 7-F baseline** (recomputed) | **+0.017726** | **1.245** | 118 | 0.0 |
| Phase 7-F baseline (published) | +0.0177 | ~1.25 | — | — |
| Phase 7-H momentum-only | +0.007916 | 0.470 | 118 | −0.00981 |
| Phase 7-H **value_ttm** bucket | **+0.023006** | 1.099 | 64 | **+0.005281** |
| Phase 7-H **quality_ttm** bucket | −0.012712 | −0.675 | 73 | −0.030438 |
| Phase 7-H **growth_ttm** bucket | −0.003519 | −0.264 | 92 | −0.021245 |
| **Phase 7-H final composite** | **+0.013954** | **0.922** | 118 | **−0.003772** |

The Phase 7-F baseline was **recomputed live** (reusing 7-F's own construction on the same
universe/harness) and reproduces the published +0.0177 / t≈1.25 to four decimals — so the
comparison is exact, not a hard-coded number.

---

## What actually happened (honest read)

Dense TTM fundamentals **helped value and hurt quality/growth**, netting slightly negative:

* **value_ttm is the bright spot.** On its own the TTM value bucket scores **+0.023**,
  beating the entire 7-F composite by +0.0053. The denser TTM yields are real signal —
  best single factors: `ttm_operating_margin` +0.0289, `ttm_fcf_yield` +0.0217,
  `ttm_operating_cashflow_yield` +0.0182, `ttm_earnings_yield` +0.0169.
* **quality_ttm and growth_ttm drag.** As equal-weight buckets they are **negative**
  in-sample (−0.0127 and −0.0035). `ttm_ocf_margin` (−0.0151) and `ttm_ocf_growth`
  (−0.0037) are the main offenders. Equal-weighting them with value pulls the composite
  from +0.023 down to +0.014 — below 7-F.
* **low-volatility stays excluded, correctly.** Its standalone IC is **−0.0338 (t = −2.1)**
  — a *negative* alpha here — confirming the a-priori decision to treat it as a risk
  descriptor, never an alpha factor.

This is the disciplined outcome: we did **not** drop quality/growth or up-weight value to
manufacture a pass (that would be factor selection / weight optimization on the outcome).
Equal weight across approved buckets is the honest composite, and it is weak.

---

## TTM panel coverage (the densification worked)

| TTM field | tickers with TTM | total TTM windows | continuity-ready (≥8) | class | ≥ MIN_NAMES(20) |
|---|---:|---:|---:|---|:--:|
| revenue | 80 | 1,347 | 68 | core | ✓ |
| net_income | 112 | 2,267 | 106 | core | ✓ |
| operating_cash_flow | 119 | 3,122 | 116 | core | ✓ |
| operating_income | 98 | 2,125 | 95 | optional | ✓ |
| free_cash_flow | 90 | 2,325 | 88 | optional | ✓ |
| capital_expenditures | 92 | 2,399 | 89 | optional | ✓ |
| eps_diluted | 118 | 2,493 | 115 | optional | ✓ |

Each TTM window is four contiguous 3-month quarters (80–100 days apart); its value is the
sum of the four legs and its **availability is the max** of the four legs' availabilities,
so nothing leaks. The de-cumulation is reused **exactly** from Phase 7-G
(`G.build_3mo_quarters`). Coverage is sufficient — the limit is **not** TTM density.

---

## Gate matrix (`ttm_signal_gate_matrix.csv`)

**27 PASS / 3 FAIL / 2 WARNING — 0 safety failures.**

* **FAIL (results, honest):** `improvement_gate` (−0.003772 < +0.005),
  `absolute_ic_gate` (0.013954 < 0.0227), `t_stat_improves_gate` (Δt −0.32 ≤ 0).
* **WARNING:** `ic_significance_gate` (|t| 0.92 < 2), `sector_neutralization_caveat_gate`
  (static map; 7-G: no PIT sector history locally).
* **PASS:** leakage / placebo / composite-built / min-buckets / ttm-coverage-sufficient /
  non-negative-IC / baseline-recompute-consistent, plus low-vol-excluded, no-sign-flipping,
  no-weight-optimization, regimes-diagnostic-only, and all safety gates (no orders/broker/
  automation/network/live-data/paid-API/deploy/production-model/universe-broadening/
  data-collection/regime-throttle/future-fundamentals/Paper-Trader/GCP/D:-write).

---

## Why `TTM_SIGNAL_RELIABILITY_WEAK` (not improved, not blocked)

The composite is **positive but does not beat 7-F by +0.005** (it is in fact slightly
below), so by the strict task rule it is `WEAK` — borderline results are **not** rounded
up. It is not `NEEDS_BROADER_UNIVERSE_COLLECTION` because the composite is positive and the
TTM panel is sufficient; it is not `IMPROVED` because every improvement gate fails.

The deeper finding aligns with Phase 7-G: **local fundamental density is now exhausted**,
and the binding reliability constraint is the **~128-name current-constituent universe and
survivorship bias** — not the freshness of the fundamentals. Better quarterly data made
value sharper but could not lift a composite measured on a narrow, survivorship-biased
cross-section into significance.

---

## Validation & leakage

* Forward labels resolve strictly after the scoring month (`strictly_forward = true`).
* Within-period label-permutation **placebo collapses** toward zero.
* TTM availability = max of all de-cumulation legs; activated only when strictly before the
  scoring month-end.
* Phase 7-E regimes are reported **for diagnostics only** — never used to select, weight, or
  throttle factors.
* Single-touch holdout reserved and touched once.

---

## Artifacts (`research/output/phase7h_ttm_fundamental_signal_retest/`)

`phase7h_ttm_fundamental_signal_retest.json`, `ttm_panel_coverage.csv`,
`ttm_factor_catalog.csv`, `ttm_factor_scoreboard.csv`, `ttm_bucket_scoreboard.csv`,
`ttm_composite_scoreboard.csv`, `ttm_attribution_table.csv`,
`ttm_regime_diagnostic_scoreboard.csv`, `ttm_signal_gate_matrix.csv`,
`phase7i_next_plan.json`.

## Tests

`tests/test_phase7h_ttm_fundamental_signal_retest.py` — 17 tests (TTM-window summation +
max-availability leakage guard, as-of / year-over-year point-in-time lookups, the four
decision branches, the gate matrix, the attribution table, and a guarded end-to-end run
that reproduces the 7-F baseline and verifies all ten artifacts). All pass.

## Recommended next phase

Because dense TTM fundamentals did **not** improve the signal, the next best path is **not**
more local polishing. The binding limit is the universe. Phase 7-I should be a **controlled,
explicitly-approved, survivorship-aware broader-universe collection** (historical index
membership + delisted-name prices + point-in-time sectors), after which the now-validated
TTM factor set can be re-graded on a realistic cross-section. Keep equal weight; no
optimization, no sign flipping, regimes diagnostic only.

## Safety contract

Preview-only · no live/paid data · no universe broadening or collection in-phase · no
trading/order/automation · no factor-weight optimization · no factor-sign flipping ·
regimes diagnostic only · no regime-throttle activation · D: read-only · not committed ·
not pushed.
