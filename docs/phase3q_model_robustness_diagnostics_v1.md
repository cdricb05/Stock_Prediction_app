# Phase 3-Q — Model Robustness, Turnover, Cost, and Bad-Year Failure Diagnosis (v1)

## Purpose

Phase 3-Q is a **research-only diagnosis** phase on top of the Phase 3-P walk-forward ranking
model. Phase 3-P found a weak-but-promising best model — `ridge_technical_only` @ **126d**
(mean daily rank IC 0.082, top-minus-bottom decile spread 0.110, hit rate 0.66) — that failed
the full robustness pass on **one** criterion: its worst single year (2021) IC was −0.108,
below the −0.05 floor. Phase 3-Q analyses **why**, and decides whether the model can be
stabilised.

It answers six questions:

1. Why did the best model fail in its worst year (2021)?
2. Is the 2021 failure regime, sector, feature-instability, or turnover driven?
3. Does the 126d signal survive realistic transaction costs?
4. Is the 126d horizon clearly better than 21d / 63d?
5. Can regime filters or ensemble blending reduce bad-year drawdown without destroying IC?
6. Which next data family matters most: macro/inflation, sentiment, or more earnings coverage?

This phase **does not train a production model**. It rebuilds research-only out-of-sample (OOS)
rank scores **in memory** (reusing the Phase 3-P / Phase 3-O helper logic) and analyses them. It
**does not deploy**, **does not restart stock-api.service**, **does not enable**
`PREDICTOR_USE_MODEL_V2`, **does not run migrations**, **does not write to production DB**, and
**does not trade**. It creates no production model candidate, computes no production predictions
/ scores / portfolio weights / order instructions, and writes no deployable model artifact (no
pickle, no joblib dump, no ONNX / HDF5 / Keras / Torch export). It writes nothing to the D:
drive and calls no provider / paid-vendor / Alpha Vantage API. The universe is current-as-of, so
every result remains **survivorship-biased** and claims **no production edge**.

## Phase 3-P confirmation (gate)

Before any work the runner re-reads `phase3p_multisignal_walkforward_model.json` and confirms:
`phase == "3-P"`; the recommendation is `..._WEAK_BUT_PROMISING` or `..._RESEARCH_PASS`;
`recommended_next_phase.phase == "3-Q"`; and `production_model_candidate_created`,
`deployable_model_artifact_written`, `production_predictions_computed`,
`production_scores_computed`, `portfolio_weights_computed`, `provider_api_called`,
`alpha_vantage_called` are all `false`. If the confirmation fails or a required local panel is
missing, the phase returns `MODEL_ROBUSTNESS_BLOCKED_INPUTS`.

## Inputs (all read-only)

| Input | Use |
|---|---|
| `research/output/phase3p_multisignal_walkforward_model.json` | Phase 3-P confirmation gate + best-model facts |
| `research/output/phase3p_.../model_scoreboard.csv` | per-horizon IC / spread / worst-year / stability |
| `research/output/phase3p_.../feature_weight_summary.csv` | family-level weight concentration |
| `D:\…\phase2k_g_expanded_price_history_free.csv` | technical / regime / sector features (D: **read-only**) |
| `research/output/phase3l_.../aligned_feature_price_panel_universe.csv` | spine: `(ticker, scoring_date)`, forward labels |
| `research/output/phase3m_.../earnings_features_universe.csv` | partial earnings features (ensemble inputs) |

The full research panel is **rebuilt in memory** by reusing the Phase 3-P / Phase 3-O feature
factory helpers. The OOS rank scores for the focus model (all three horizons) and the
regime-aware ensemble (126d) are **regenerated in memory** with the same expanding **walk-forward**
folds and per-horizon **embargo** as Phase 3-P. The full prediction panel is **never written to
disk** — only summary CSVs (rank-bucket diagnostics) are written.

## Methods

### Bad-year attribution (`year_failure_attribution.csv`)
Per test year (2020–2025): mean / median daily rank IC, IC hit rate, decile spread, and the
mean daily IC **conditioned on regime** (risk-off, risk-on, high-vol, SPY-below-200d, low-breadth,
high-dispersion days). 2021 is compared against the other years and its **regime composition** is
measured.

### Regime performance (`regime_performance_summary.csv`)
The focus model's pooled OOS IC / spread by regime bucket: `risk_on`, `risk_off`, `high_vol`,
`low_vol`, `spy_above_200d`, `spy_below_200d`, `low_breadth`, `high_dispersion`. Breadth and
dispersion thresholds are the per-date medians across the panel (documented in the result JSON).

### Sector attribution (`sector_performance_summary.csv`)
Per sector: share of the model's top decile and bottom decile, the top-minus-bottom forward-label
spread, the 2021-only spread, and concentration risk (max sector share of the top decile).

### Horizon comparison (`horizon_comparison_summary.csv`)
21d vs 63d vs 126d on IC, spread, hit rate, worst-year IC, stability, fold count, and estimated
monthly top-decile **turnover**, to decide whether 126d should remain preferred.

### Turnover and transaction-cost sensitivity (`turnover_cost_sensitivity.csv`)
For the top decile / top quintile / top 30% buckets: approximate one-way **turnover** between
consecutive **monthly** rebalance dates, the gross top-minus-bottom spread, and the **net** spread
after **transaction cost** at 0 / 5 / 10 / 25 / 50 bps, plus the break-even cost and whether the
bucket stays positive. The horizon spread is scaled to a 21-trading-day month and round-trip cost
is applied to the turned-over fraction across both legs (`cost = 2 × turnover × bps`). All figures
are explicitly **approximate**.

### Feature weight stability (`feature_weight_stability_summary.csv`)
Family-level weight concentration (abs-weight share) from the Phase 3-P weight summary, plus
per-fold **sign stability** for the focus model (regenerated in memory). Flags whether
`ridge_technical_only` is over-concentrated and why the combined models underperform (noisy
partial earnings, missing macro / sentiment).

### Ensemble blend sensitivity (`ensemble_blend_sensitivity.csv`)
Research-only date-wise rank blends of the focus model, the regime-aware ensemble, and the
SEC-fundamental composite (100% / 75-25 / 50-50 variants). Each blend is scored on worst-year IC,
stability, decile spread, and hit rate to see whether blending damps the bad-year drawdown without
destroying IC.

### Improvement priority (`improvement_priority_table.csv`)
Ranks the next workstreams — macro/inflation, regime gating/blending, finishing earnings coverage,
sentiment, cost-aware turnover reduction, target engineering, point-in-time sector map — with
expected impact, urgency, blocker, cost/effort, and recommended next phase.

## Findings (this run)

- **2021 is a regime/style failure, not noise.** The technical/momentum model earns its IC in
  volatile, trending, **risk-off** markets (pooled risk-off IC ≈ 0.15) and is weakest in calm
  **risk-on / low-volatility** conditions (pooled risk-on IC ≈ 0.06). 2021 was an almost pure calm
  risk-on year (0% risk-off days, ~99% low-vol days); within that regime the short-horizon
  technical signal inverted, driving the worst-year IC to −0.108 versus +0.122 in the other years.
- **The signal survives cost comfortably.** Top-decile one-way monthly turnover ≈ 0.40 with a
  break-even cost ≈ 240 bps; the net monthly spread stays positive through 50 bps.
- **126d is the strongest but most fragile horizon.** It has the highest IC and spread, the
  lowest turnover, but the worst single-year IC and lowest stability; the fix is bad-year
  mitigation, not a horizon switch.
- **Blending helps but does not fully fix the bad year.** The best blend (50% focus + 50%
  regime-aware ensemble) lifts the 2021 worst-year IC from −0.108 to ≈ −0.069 while keeping IC
  ≈ 0.071 — an improvement, but still below the −0.05 floor.

## Decision gates

| Recommendation | Condition |
|---|---|
| `MODEL_ROBUSTNESS_PASS_READY_FOR_RISK_SIMULATION` | a blend/regime rule lifts worst-year IC above −0.05, net decile spread positive at 25 bps, stability improves or ≥ 0.70 |
| `MODEL_ROBUSTNESS_WEAK_FIXABLE_WITH_REGIME_AND_DATA` | positive gross, survives cost at 10 bps, bad year explainable, clear improvement path |
| `MODEL_ROBUSTNESS_FAILS_COST_OR_BAD_YEAR` | costs erase the model, or the bad year is unexplainable, or no blend/regime rule helps |
| `MODEL_ROBUSTNESS_BLOCKED_INPUTS` | Phase 3-P result or a required panel missing / unconfirmed |

`recommended_next_phase.phase` is always **`3-R`**: Risk Simulation + Non-Production Candidate
Packaging on PASS; **Add Macro/Inflation Data and Re-run Walk-Forward** on WEAK_FIXABLE; Rework
Target and Turnover-Aware Features on FAILS; Repair Phase 3-Q Inputs on BLOCKED.

**This run:** `MODEL_ROBUSTNESS_WEAK_FIXABLE_WITH_REGIME_AND_DATA` → next phase **3-R — Add
Macro/Inflation Data and Re-run Walk-Forward**. The model is positive gross and survives realistic
transaction cost; the 2021 failure is an explainable regime/style effect; blending narrows but
does not close the bad-year gap, so the priority is adding the regime-bearing macro/inflation
family (the blocked data the model needs to see the 2021 regime).

## Outputs

Result JSON `research/output/phase3q_model_robustness_diagnostics.json` plus, under
`research/output/phase3q_model_robustness_diagnostics/`: `year_failure_attribution.csv`,
`regime_performance_summary.csv`, `sector_performance_summary.csv`,
`horizon_comparison_summary.csv`, `turnover_cost_sensitivity.csv`,
`feature_weight_stability_summary.csv`, `ensemble_blend_sensitivity.csv`,
`improvement_priority_table.csv`, `readiness_decision_table.csv`. Every file is **Git-safe
(< 50 MB)**.

## Safety flags (result JSON)

`database_touched`, `database_write_executed`, `migration_executed`, `deployment_executed`,
`model_v2_enabled`, `production_edge_claimed`, `production_model_trained`,
`production_model_candidate_created`, `deployable_model_artifact_written`,
`production_predictions_computed`, `production_scores_computed`, `portfolio_weights_computed`,
`order_instructions_created`, `d_drive_written`, `provider_api_called`, `alpha_vantage_called`,
`paid_vendor_api_called`, `macro_faked`, `sentiment_faked` are all **false**;
`no_trading`, `no_orders`, `no_automation`, `research_diagnostics_only`, `d_drive_read`,
`labels_for_validation_only` are **true**. Macro / inflation and sentiment remain unimplemented
(no local data) and **are not faked**.

## Run

```powershell
# Windows PowerShell only.
python -B research\run_phase3q_model_robustness_diagnostics.py
python -B tests\test_phase3q_model_robustness_diagnostics.py
# Optional full end-to-end test into a temp dir:
$env:PHASE3Q_LIVE = "1"; python -B tests\test_phase3q_model_robustness_diagnostics.py
```
