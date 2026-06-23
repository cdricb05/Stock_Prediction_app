# Phase 7-E — Regime / Risk Overlay Foundation (System 2, descriptive, v1)

**Track A (quant brain) research. Offline, as-of / lagged, leakage-safe, descriptive.**
No network, no provider call, no paid API, no API key read or required, no model trained
or deployed, no factor-weight optimization, **no order / broker / automation / hedging /
order-sizing logic, no trade recommendation**, no Paper Trader / GCP work, no live data,
no binary artifact, no commit, no push. Reads only local files (the Phase 2K-G price panel
READ ONLY on D:, the committed sector / SEC fundamentals artifacts, and local FRED macro
CSVs on C:). Writes nothing to D:.

- **Phase:** 7-E
- **Status:** Implemented and gated (pending owner review)
- **Constitution:** [project_charter_sp500_multifactor_ranking_v1.md](project_charter_sp500_multifactor_ranking_v1.md)
- **Predecessors:** [phase7b_validation_harness_foundation_v1.md](phase7b_validation_harness_foundation_v1.md), [phase7c_multifactor_ranking_engine_v1.md](phase7c_multifactor_ranking_engine_v1.md), [phase7d_risk_decomposition_foundation_v1.md](phase7d_risk_decomposition_foundation_v1.md)
- **Reuses:** the Phase 7-C signals (factor construction, loaders, `grade_series`) and the Phase 7-B harness (`period_rank_ics`, `ic_summary`)
- **Recommendation:** `READY_FOR_PHASE7F_SIGNAL_RELIABILITY_UPGRADE`

---

## Why this phase exists

The charter is a two-system design: System 1 = multi-factor ranking engine (Phase 7-C),
System 2 = regime / risk overlay. Phase 7-D built the measurement-only book risk lens. This
phase builds the **first System 2 layer**: a **descriptive** regime classifier that labels
each historical scoring month into market regimes from lagged / as-of local data, then
reports how the Phase 7-C ranking signal (per-factor, baseline, composite IC) and a
market-risk proxy **behaved conditional on regime**, plus regime transitions / persistence
and a NOT-LIVE risk-throttle template.

This is **not** predictive, **not** a production model, **not** an order system, **not**
automation, **not** factor-weight optimization, **not** a signal-rescue attempt. It is a
regime *diagnostics and risk-throttle foundation*. It explains how the signal and risk
behaved by regime; it does **not** forecast regimes or returns.

## What was built

A descriptive regime overlay engine
([research/run_phase7e_regime_risk_overlay_foundation.py](../research/run_phase7e_regime_risk_overlay_foundation.py)):

1. **Reconstructs the Phase 7-C signals verbatim** (same loaders, factors, normalization,
   equal-weight composite, harness grading) to obtain each series' per-month rank IC,
   keyed `YYYY-MM`. The full-sample composite / baseline IC reproduce 7-C exactly.
2. **Classifies each scoring month into six regime axes** from local lagged / as-of data.
3. **Partitions** every series' per-month ICs by regime label and re-summarizes through the
   harness `ic_summary` (the IC math is reused unchanged — no new estimator).
4. Reports a **market-risk proxy by regime**, **regime transitions / persistence**, and a
   **NOT-LIVE risk-throttle template**.
5. Emits a regime gate matrix, a consolidated JSON report, and a Phase 7-F hand-off plan.

## The six regime axes (all as-of / lagged)

| Axis | Source | Rule (labels) |
|---|---|---|
| **risk_regime** | SPY price | risk-off when SPY ≥ 10% below its trailing 252-day peak, else risk-on |
| **vol_regime** | SPY price | high-vol when trailing 21-day annualized SPY realized vol exceeds its **as-of expanding median** (≥ 12-month history), else low-vol |
| **trend_regime** | SPY price | above-trend when SPY month-end close ≥ its 200-day SMA, else below-trend |
| **inflation_regime** | CPIAUCSL (FRED) | inflation-up when as-of CPI YoY is higher than 3 months earlier, else disinflationary |
| **rates_regime** | DGS10 (FRED) | rates-up when the as-of 10-year Treasury yield is higher than 3 months earlier |
| **dollar_regime** | DTWEXBGS (FRED) | dollar-up when the as-of broad trade-weighted USD index is higher than 3 months earlier |

**Leakage discipline.** Price regimes use only trailing daily SPY windows ending at
month-end *m*. Macro regimes read the latest observation lagged ≥ 1 month (CPI 2 months,
yields/USD 1 month), so no same-day and no future macro is used. The volatility threshold is
an **expanding** as-of median (only data ≤ *m*), not a full-sample median. Regime labels
never read the forward (*m*→*m*+1) return they condition — that return is the strictly-forward
IC reused from 7-C. As-of audit: **0 violations**.

## Regime history (this run, 2016-01 … 2026-06)

| Axis | Label | Months | Avg run (mo) | Persistence |
|---|---|---|---|---|
| risk_regime | risk_on / risk_off | 108 / 15 | 27.0 / 5.0 | 0.97 / 0.80 |
| vol_regime | high_vol / low_vol | 71 / 43 | 3.4 / 2.0 | 0.71 / 0.51 |
| trend_regime | above_trend / below_trend | 97 / 20 | 8.8 / 2.0 | 0.90 / 0.50 |
| inflation_regime | inflation_up / disinflation | 72 / 54 | 4.8 / 3.9 | 0.80 / 0.74 |
| rates_regime | rates_up / rates_down | 72 / 54 | 4.0 / 3.2 | 0.76 / 0.69 |
| dollar_regime | dollar_up / dollar_down | 69 / 57 | 3.6 / 3.2 | 0.74 / 0.68 |

Risk-on dominates the 2016–2026 large-cap tape (one ~27-month run); risk-off is rare and
short. Axis month-totals differ by warm-up (200-day SMA, 12-month vol history, 252-day
drawdown, macro lags).

## What signal behavior changed by regime (descriptive, not a forecast)

Full-sample equal-weight composite IC = **−0.0074**, price-only baseline = **−0.0123**
(identical to Phase 7-C). Conditional composite IC:

| Axis | "constructive" label → IC | "stress" label → IC |
|---|---|---|
| vol_regime | **low_vol → +0.0261** (t≈1.0, n=43) | high_vol → −0.0195 (n=70) |
| trend_regime | above_trend → +0.0023 (n=96) | **below_trend → −0.0441** (n=20) |
| risk_regime | risk_on → −0.0025 (n=106) | **risk_off → −0.0422** (n=15) |
| inflation_regime | inflation_up → +0.0073 (n=67) | disinflation → −0.0256 (n=54) |
| rates_regime | rates_up → −0.0067 | rates_down → −0.0083 |
| dollar_regime | dollar_down → −0.0061 | dollar_up → −0.0085 |

The composite ranks the universe **better in calm / uptrending / low-stress months**
(low-vol IC turns positive) and **worse in below-trend / risk-off / disinflationary**
months. The price-only baseline shows the same pattern, more sharply (low-vol +0.030,
below-trend −0.040). **Per-regime samples are short and t-stats are weak** — these are
descriptive splits, not significant or out-of-sample edges, and are **not** used to flip
signs or weight factors (forbidden).

## What risk behavior changed by regime

Market-risk proxy = SPY next-month return statistics + universe cross-sectional dispersion,
conditional on the regime label at decision month-end:

| Axis | Label | SPY fwd vol (ann.) | SPY worst fwd month | Univ. dispersion |
|---|---|---|---|---|
| risk_regime | risk_off / risk_on | **0.267** / 0.130 | −0.125 / −0.088 | 0.077 / 0.066 |
| vol_regime | high_vol / low_vol | 0.181 / 0.108 | −0.125 / −0.069 | 0.070 / 0.066 |
| trend_regime | below_trend / above_trend | 0.240 / 0.129 | −0.125 / −0.088 | 0.079 / 0.066 |

Realized SPY risk roughly **doubles** in risk-off / below-trend regimes, and cross-sectional
dispersion widens — the regimes where the ranking signal also degrades. The Phase 7-D book
decomposition (net beta 0.553, systematic variance share 0.324, effective ≈ 11.6 names) is a
**single as-of snapshot**, referenced for context but **not** re-estimated per regime.

## Risk-throttle template (NOT LIVE)

A `regime_throttle_template.csv` maps each price-based regime label to an illustrative
posture and a suggested gross-throttle band (risk_on 0.9–1.0; risk_off 0.3–0.6; high_vol
0.4–0.7; below_trend 0.4–0.7); macro axes are CONTEXT only. Every row is marked
**`activation_status = NOT_LIVE / NOT_TRADING`** and **`owner_review_required = true`**. The
bands are **illustrative defaults chosen a priori, NOT optimized and NOT fitted to the
IC-by-regime results**, and are connected to **no** sizing or order path.

## Risk gate matrix

**31 PASS / 0 FAIL / 1 WARNING.** All capability gates (regimes computed, ≥ 2 axes, ≥ 24
graded months, as-of/lagged 0 violations, no-forward-leakage, factor & composite IC by
regime, transitions, throttle-not-live) and all twenty safety gates (no orders / broker /
automation / hedging / order-sizing / trade-recommendation / network / live-data / paid-API /
deploy / production-model / weight-optimization / future-regime-labels / same-day-leakage /
Paper-Trader / GCP / D-drive-write; throttle-not-live; owner-review-required; preview-only)
PASS. The single WARNING is the **sector-map point-in-time caveat** inherited from the reused
7-C signals (static current-as-of map) — documented and non-blocking.

## What is still caveated

- The overlay is **descriptive, not predictive**: conditional IC / risk by regime is
  in-sample history, not a forecast and not an edge claim.
- Regime rules are **simple, fixed, a priori** (trend / vol / drawdown thresholds, 3-month
  macro direction) — not tuned, not fitted to the IC outcome.
- **Per-regime IC samples are short** (months split across regimes), so per-regime t-stats
  are weak — read them as descriptive only.
- The reused 7-C signals carry their own caveats (static sector map, annual-only
  fundamentals, **WEAK** composite edge with no confirmed alpha).
- The 7-D book risk decomposition is a **single as-of snapshot**, not re-estimated per regime.
- The throttle template is **illustrative and NOT LIVE** — values are placeholders pending
  owner review.

## Recommendation

**`READY_FOR_PHASE7F_SIGNAL_RELIABILITY_UPGRADE`** — the regime layer is built from local
lagged data only, classifies six axes with a clean as-of audit (0 violations), decomposes the
7-C signal and a market-risk proxy by regime, reports transitions / persistence, and emits a
NOT-LIVE throttle template. Every capability and safety gate passes; the lone warning is the
inherited sector-map PIT caveat.

Allowed values: `READY_FOR_PHASE7F_SIGNAL_RELIABILITY_UPGRADE` / `NEEDS_REGIME_REVIEW` /
`DATA_QUALITY_BLOCKED` / `ERROR`.

## Committed-safe artifacts

Written to `research/output/phase7e_regime_risk_overlay_foundation/`:

- `phase7e_regime_risk_overlay_foundation.json` — main report (axes, as-of audit, IC by regime, risk by regime, transitions, gates, recommendation)
- `regime_classification_summary.csv` — per axis/label: months, fraction, first/last month, avg run length, persistence
- `regime_transition_matrix.csv` — per axis: month-over-month from→to counts + transition probabilities
- `factor_ic_by_regime.csv` — per factor × axis × label: n months, mean rank IC, t-stat, full-sample IC, delta
- `composite_ic_by_regime.csv` — baseline & composite × axis × label, same columns
- `risk_by_regime.csv` — per axis/label: SPY fwd return mean / vol / worst, universe dispersion
- `regime_throttle_template.csv` — NOT-LIVE posture + suggested gross band per regime
- `regime_gate_matrix.csv` — capability + informational + safety gates
- `phase7f_next_plan.json` — hand-off to Phase 7-F (signal reliability upgrade)

Code + tests:

- [research/run_phase7e_regime_risk_overlay_foundation.py](../research/run_phase7e_regime_risk_overlay_foundation.py)
- [tests/test_phase7e_regime_risk_overlay_foundation.py](../tests/test_phase7e_regime_risk_overlay_foundation.py) (17 tests, all passing)

## Recommended next phase

**Phase 7-F — Signal Reliability Upgrade (System 1 robustness).** The composite is still
`WEAK`; reliability is the bottleneck before any regime throttle can be considered live.
Broaden the System 1 universe beyond ~127 large caps, add a TTM-quarterly fundamentals blend,
and stress-test factor / composite IC stability across the regimes labelled here (a
*descriptive* robustness input, **not** a weighting signal). Keep the throttle template
NOT LIVE until System 1 shows a confirmed, regime-robust edge and the owner endorses a sizing
philosophy.

## Safety contract

Research only · zero network / provider call · no Alpha Vantage / paid API · no model trained
or deployed · no factor weights optimized · as-of / lagged regime labels (no future, no
same-day forward leakage) · **descriptive, not predictive** · **no orders / broker /
automation / hedging / order sizing · no trade recommendation** · throttle template NOT LIVE
(owner review required) · no Paper Trader / GCP / deploy · D: read-only (nothing written) ·
committed-safe text artifacts only · no commit · no push.
