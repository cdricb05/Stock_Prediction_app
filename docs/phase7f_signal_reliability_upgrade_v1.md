# Phase 7-F — Signal Reliability Upgrade (System 1 robustness, v1)

**Track A (quant brain) research. Offline, point-in-time, leakage-safe, local data only.**
No network, no provider call, no paid API, no API key read or required, no model trained
or deployed, **no factor-weight optimization, no factor-sign flipping to chase IC, no
regime-based factor selection**, no order / broker / automation / hedging / sizing, no
trade recommendation, no Paper Trader / GCP work, no live data, no binary artifact, no
commit, no push. Reads only local files (the Phase 2K-G price panel READ ONLY on D:,
committed SEC fundamentals / sector artifacts, and local FRED macro CSVs via Phase 7-E).
Writes nothing to D:.

- **Phase:** 7-F
- **Status:** Implemented and gated (pending owner review)
- **Constitution:** [project_charter_sp500_multifactor_ranking_v1.md](project_charter_sp500_multifactor_ranking_v1.md)
- **Predecessors:** [phase7b_validation_harness_foundation_v1.md](phase7b_validation_harness_foundation_v1.md), [phase7c_multifactor_ranking_engine_v1.md](phase7c_multifactor_ranking_engine_v1.md), [phase7e_regime_risk_overlay_foundation_v1.md](phase7e_regime_risk_overlay_foundation_v1.md)
- **Reuses:** Phase 7-C factor construction / loaders / grading; Phase 7-E regime classification; the unmodified Phase 7-B harness
- **Recommendation:** `SIGNAL_RELIABILITY_UPGRADED` (clears the task success gate — but **not** statistically significant; read the caveats)

---

## Why this phase exists

Phase 7-C built the first System 1 multi-factor ranking engine and graded **WEAK**
(equal-weight composite mean rank IC = −0.0074; price-only baseline = −0.0123;
incremental +0.0050, a near miss). Phase 7-E's regime diagnostics confirmed the signal is
fragile and regime-sensitive. Before *any* portfolio construction, sizing, hedging, or
regime activation, the signal foundation itself must be made more reliable.

This phase is strictly **signal engineering**: improve factor *integrity, robustness, and
interpretability* through better data and factor construction. It is **not** a trading
system, **not** a production model, **not** weight optimization, **not** a regime overlay,
**not** a sign-flipping rescue. The success gate, factor signs, and equal weights are all
fixed a priori; the regimes are used for diagnostics only.

## What was built

An offline signal-engineering engine
([research/run_phase7f_signal_reliability_upgrade.py](../research/run_phase7f_signal_reliability_upgrade.py)):

1. **Signal-weakness inventory** — the concrete 7-C / 7-E failure modes, each attributed
   to a root cause and tagged addressed / partial / not-addressed.
2. **Data-quality inventory** — local annual-10-K coverage, quarterly-balance recency, and
   the (thin) clean 3-month quarterly-frame coverage that bounds TTM.
3. **Upgraded factor catalogue** (equal weight throughout):
   - **momentum**: 12-1, 6-1, risk-adjusted 12-1;
   - **value**: earnings / sales / FCF / book yields (share-free via implied diluted
     shares = NI / EPS);
   - **quality**: ROE, ROA, net & operating & FCF margins, asset turnover, accruals —
     cash-flow-based and asset-efficiency measures, all **split-invariant**;
   - **growth**: revenue / earnings / FCF YoY — **split-invariant**;
   - **low-volatility**: catalogued as a **RISK DESCRIPTOR**, reported but **excluded from
     the alpha composite** a priori.
4. **TTM attempt** — annual+interim roll-forward from clean 3-month frames; coverage
   reported honestly (it is thin — a documented data-foundation gap).
5. **Grading** of every sub-factor, bucket, and the composite through the unmodified 7-B
   harness, against the **same-universe** Phase 7-C composite (the "old baseline") and the
   price-only baseline.
6. **Regime decomposition** of composite and bucket IC by the Phase 7-E regimes —
   **diagnostics only**; regimes never select, weight, or modify factors.

## Factor catalogue (equal weight; signs fixed a priori)

| Bucket | Sub-factors | Split-invariant | Notes |
|---|---|---|---|
| momentum | mom_12_1, mom_6_1, mom_riskadj | no (price) | horizon + risk-adjusted diversification |
| value | earnings / sales / FCF / book yield | no (price) | share-free via implied shares NI/EPS; residual split caveat |
| quality | ROE, ROA, net & oper & FCF margin, asset turnover, accruals | **yes** | cash-flow & asset-efficiency, not solely ROE |
| growth | revenue / earnings / FCF YoY | **yes** | prior-year base guarded positive |
| *(risk descriptor)* | low_volatility | no (price) | **excluded from the alpha composite** |
| *(attempt)* | ttm_earnings_yield | no (price) | TTM roll-forward; coverage-limited, not approved |

Each bucket score is the equal-weight mean of its **approved** sub-factor z-scores
(sector-neutralized → z → winsorized ±3, reusing 7-C's normalizer); the composite is the
equal-weight mean of the approved bucket scores. No weights are optimized.

## Leakage discipline

Every fundamental (annual flow, quarterly balance, TTM roll, implied shares) is used at
month *m* only when its `availability_datetime` is strictly before *m*'s market month-end.
Momentum skips the most recent month. Forward labels are next-month returns resolving
strictly after the decision date. **No-lookahead gate: 0 violations. Placebo (within-period
label shuffle): collapses to ~0.** All rank-IC math is reused unchanged from the 7-B harness.

## Result — the headline, and the honest decomposition

| Series (same universe, 2016-01..2026-06) | mean rank IC | t-stat | n months |
|---|---|---|---|
| Phase 7-C price-only baseline | −0.0123 | −0.72 | 121 |
| **Phase 7-C equal-weight composite (old baseline)** | **−0.0074** | −0.50 | 121 |
| Phase 7-C minus low-vol (old specs, attribution control) | +0.0123 | +0.92 | 112 |
| upgraded bucket: momentum | +0.0079 | +0.47 | 118 |
| upgraded bucket: value | +0.0250 | +1.45 | 66 |
| upgraded bucket: quality | −0.0091 | −0.57 | 94 |
| upgraded bucket: growth | −0.0048 | −0.41 | 102 |
| **upgraded equal-weight composite** | **+0.0177** | **+1.25** | 118 |
| incremental (upgraded − old baseline) | **+0.0251** | | |
| incremental (upgraded − price-only) | +0.0301 | | |

**Improvement attribution (the most important finding):**

| Step | IC | Contribution |
|---|---|---|
| old baseline (incl. low-vol) | −0.0074 | — |
| → reclassify low-vol out (same old specs) | +0.0123 | **+0.0197** |
| → full multi-spec fundamental upgrade | +0.0177 | **+0.0054** |

**The bulk of the improvement (+0.0197 of +0.0251) is the a-priori reclassification of
low-volatility as a risk descriptor — not the new fundamental specifications, which add a
modest +0.0054.** Low-vol standalone IC is −0.0338 (t = −2.15), the strongest single
signal in the panel and economically a defensive/risk characteristic rather than
cross-sectional alpha. Removing it from the equal-weight average is what lifts the
composite above zero. This is reported in `improvement_attribution` so the artifact itself
cannot overclaim.

## Which weaknesses were addressed

| ID | Weakness | Status in 7-F |
|---|---|---|
| W1 | composite IC negative | partial — composite now non-negative, but weak & insignificant |
| W2 | single-metric buckets | **addressed** — each bucket is a multi-spec blend |
| W3 | annual-only fundamentals | partial — quarterly balance recency; TTM coverage-limited |
| W4 | quality = ROE only | **addressed** — ROA, margins, FCF margin, turnover, accruals |
| W5 | low-vol treated as alpha | **addressed** — demoted to a reported risk descriptor |
| W6 | split-adjusted-EPS value distortion | partial — added cash-flow/sales/book yields; residual caveat |
| W7 | constrained ~127-name universe | **not addressed** — binding data-foundation gap for 7-G |
| W8 | no TTM / quarterly construction | partial — TTM implemented; local clean-quarterly frames too sparse |
| W9 | revisions / sentiment unavailable | not addressed — still empty locally; not fabricated |
| W10 | static (non-PIT) sector map | not addressed — inherited caveat |

## Regime diagnostics (descriptive only — not used for selection)

The upgraded composite shows the **same regime fragility** as 7-C: IC degrades in stress
(risk_off −0.024, high_vol −0.001, below_trend +0.008) and is stronger in calm/uptrend
(risk_on +0.024, low_vol +0.056, above_trend +0.022). These splits are short-sample and
mostly insignificant; they are reported as robustness context and were **never** used to
select, weight, or modify any factor.

## Risk gate matrix

**25 PASS / 0 FAIL / 2 WARNING.** All capability and safety gates pass, including
`improvement_gate` (+0.0251 ≥ +0.005), `nonnegative_ic_gate` (+0.0177 ≥ 0),
`low_vol_excluded_from_alpha_gate`, `no_sign_flipping_gate`, and `no_regime_selection_gate`.
The two WARNINGs are the **IC-significance flag** (|t| = 1.25 < 2 — the edge is not
significant) and the inherited **sector-map point-in-time caveat**.

## What is still caveated

- The upgraded composite IC is **not statistically significant** (|t| ≈ 1.25). It clears
  the task's mechanical success gate but is a weak, in-sample edge — **not** confirmed alpha.
- **Most of the gain is the low-vol reclassification**, not the fundamental specs; the
  quality and growth buckets are still negative in-sample.
- **Value-bucket coverage is thin** (~30–46% of label cells; ~64–66 months) — the positive
  value IC rests on a partial cross-section, a robustness concern.
- **Universe**: ~127 large caps with current-constituent bias (W7) — the binding gap.
- **TTM**: local clean 3-month frames are too sparse for broad coverage (W8) — 0 approved
  TTM months this run; attempted and reported, not faked.
- **Value yields** retain a residual adjusted-price vs as-reported-share split caveat (W6),
  reduced (most-recent implied shares) but not eliminated without unadjusted prices.
- Sector neutralization uses a static current-as-of map (W10); revisions/sentiment remain
  unavailable (W9).

## Did signal quality improve enough?

**Mechanically yes, substantively only partially.** Against the task's defined success
criteria the run qualifies as `SIGNAL_RELIABILITY_UPGRADED`: the upgraded composite beats
the same-universe Phase 7-C composite by +0.0251 (≥ +0.005) and is non-negative (+0.0177),
placebo/leakage/safety gates pass, regimes are diagnostic-only, no live data, no
trading/order logic. **But** the edge is statistically insignificant and is driven mainly
by the principled low-vol exclusion rather than the new fundamentals. The honest reading:
the signal is now *cleaner and non-negative*, not *reliable*. Genuine reliability is gated
on the universe and quarterly-data foundation (Phase 7-G).

## Recommendation

**`SIGNAL_RELIABILITY_UPGRADED`** — success gate cleared, every safety and leakage gate
passes, attribution and significance reported transparently.

Allowed values: `SIGNAL_RELIABILITY_UPGRADED` / `SIGNAL_RELIABILITY_WEAK` /
`NEEDS_DATA_FOUNDATION_UPGRADE` / `NEEDS_REVIEW` / `ERROR`.

## Committed-safe artifacts

Written to `research/output/phase7f_signal_reliability_upgrade/`:

- `phase7f_signal_reliability_upgrade.json` — main report (weakness inventory, catalogue, composite vs baselines, attribution, regime diagnostics, gates, recommendation)
- `signal_weakness_inventory.csv` — 7-C / 7-E failure modes → root cause → 7-F action
- `data_quality_inventory.csv` — annual / quarterly-balance / clean-quarterly-frame coverage
- `upgraded_factor_catalog.csv` — per sub-factor: bucket, role, definition, split-invariant, availability, approval, coverage
- `upgraded_factor_scoreboard.csv` — per sub-factor IC / t-stat / spreads / coverage / approval
- `upgraded_composite_scoreboard.csv` — baselines, buckets, upgraded composite, incrementals, attribution, cost / Sharpe / holdout
- `regime_diagnostic_scoreboard.csv` — composite & bucket IC by Phase 7-E regime (diagnostic only)
- `signal_reliability_gate_matrix.csv` — capability + safety gates
- `phase7g_next_plan.json` — hand-off to Phase 7-G (universe breadth + quarterly fundamentals)

Code + tests:

- [research/run_phase7f_signal_reliability_upgrade.py](../research/run_phase7f_signal_reliability_upgrade.py)
- [tests/test_phase7f_signal_reliability_upgrade.py](../tests/test_phase7f_signal_reliability_upgrade.py) (20 tests, all passing)

## Recommended next phase

**Phase 7-G — Signal Data Foundation Upgrade (universe breadth + quarterly fundamentals).**
The binding reliability constraint is now data, not specification: broaden the
survivorship-aware local universe and assemble a denser point-in-time quarterly
fundamentals panel, then re-grade this same upgraded catalogue through the unmodified 7-B
harness. Keep equal weight; no weight optimization, no sign flipping, no regime-based
selection; no live provider calls inside the grading phase.

## Safety contract

Research only · zero network / provider call · no Alpha Vantage / paid API · no model
trained or deployed · **no factor weights optimized · no factor signs flipped · no
regime-based factor selection** · no orders / broker / automation / hedging / sizing · no
trade recommendation · no Paper Trader / GCP / deploy · D: read-only (nothing written) ·
committed-safe text artifacts only · no commit · no push.
