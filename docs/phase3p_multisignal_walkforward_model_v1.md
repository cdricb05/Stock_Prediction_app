# Phase 3-P — Research-Only Multi-Signal Walk-Forward Ranking Model (v1)

## Purpose

Phase 3-P is the first real **research-only model layer** on top of the Phase 3-O multi-signal
feature factory. It trains and evaluates **non-deployable** research models with proper
**walk-forward** validation and a per-horizon **embargo**, and compares three model families:

1. **Baseline rank composites** reproduced from Phase 3-O (momentum, sector-neutral momentum,
   seasonality, AR-style mean-reversion, SEC-fundamental, and the combined multi-signal blend).
2. A **regularized cross-sectional linear ranking model** — a NumPy closed-form **ridge** model
   (no sklearn, no third-party ML dependency).
3. A **regime-aware ensemble** — separate risk-on / risk-off sub-models with a documented
   fallback to a single global model when a regime subset is too thin.

The **core model is a cross-sectional multi-signal ranking model**, trained and validated with
walk-forward splits. ARIMA-style features are **one conditioning family among many**, not the
model.

This phase is **research-only**. It trains models **in memory only**. It **does not deploy**,
**does not restart stock-api.service**, **does not enable** `PREDICTOR_USE_MODEL_V2`,
**does not run migrations**, **does not write to production DB**, and **does not trade**. It
creates no production model candidate, computes no production predictions / scores / portfolio
weights / order instructions, and writes no deployable model artifact (no pickle, no joblib dump,
no ONNX / HDF5 / Keras / Torch export). It writes nothing to the D: drive and calls no provider /
paid-vendor / Alpha Vantage API. The universe is current-as-of, so every result remains
survivorship-biased and claims **no production edge**.

## Phase 3-O confirmation (gate)

Before doing any work the runner re-reads `phase3o_multisignal_feature_factory.json` and confirms:
`phase == "3-O"`; the recommendation is `..._PARTIAL_MACRO_SENTIMENT_MISSING` or
`..._SUCCESS_READY_FOR_RESEARCH_MODEL`; `recommended_next_phase.phase == "3-P"`; and
`macro_faked`, `sentiment_faked`, `provider_api_called`, `alpha_vantage_called`,
`production_model_candidate_created`, `deployable_model_artifact_written`,
`portfolio_weights_computed` are all `false`. If the confirmation fails or a required local panel
is missing, the phase returns `MULTISIGNAL_WALKFORWARD_MODEL_BLOCKED_INPUTS`.

## Inputs (all read-only)

| Input | Use |
|---|---|
| `research/output/phase3o_multisignal_feature_factory.json` | Phase 3-O confirmation gate |
| `D:\…\phase2k_g_expanded_price_history_free.csv` | technical / regime / sector / AR features (D: **read-only**) |
| `research/input/phase2k_p_sector_map_current.csv` | sector-relative features (current-as-of) |
| `research/output/phase3l_…/aligned_feature_price_panel_universe.csv` | spine: `(ticker, scoring_date)`, SEC features, forward labels |
| `research/output/phase3m_…/earnings_features_universe.csv` | partial earnings-surprise features (as-of merge) |

The full research panel is **rebuilt in memory** by importing and reusing the Phase 3-O feature
factory helper logic (preferred input strategy). The Git-safe Phase 3-O sample CSV is **not**
relied upon. The full panel is **never written to disk**.

## Feature sets

Phase 3-O **implemented families only** — `technical_price`, `seasonality_calendar`
(cross-sectional seasonal stats), `sector_relative`, `sec_fundamental`, `earnings_surprise`
(partial), `time_series_arima_style`. `market_regime` features are used only as **conditioning /
interaction** inputs (not standalone cross-sectional alpha). `macro_inflation` and `sentiment`
are **not implemented and not faked** (no local data).

`model_feature_set.csv` enumerates: `technical_only`, `seasonality_only`, `sector_relative_only`,
`sec_fundamental_only`, `earnings_partial_only`, `ar_style_only`, `combined_no_earnings`,
`combined_with_partial_earnings`, and `combined_regime_interactions` (the last adds
momentum×risk-off, volatility×high-vol, and sector-relative×risk-off interactions when enough
non-null rows exist).

## Walk-forward validation

- **Time index:** `scoring_date`. **Window:** expanding (all history before the embargo cutoff).
- **Minimum training years:** 3. **Test folds:** yearly, 2020 → 2026 where data allows.
- **Embargo** (per horizon): the last *h* trading days before each test year are excluded from
  training so **no training label window overlaps the test window** — 21d → 21-day embargo,
  63d → 63-day embargo, 126d → 126-day embargo. Horizons 21d / 63d / 126d are evaluated
  separately.

### Ridge model and leakage control

The target is the **cross-sectional z-score of forward excess return by `scoring_date`** (date-wise
de-meaned; a rank target is also supported). The ridge weights come from the closed-form solution
`w = (XᵀX + λI)⁻¹ Xᵀy`. **Every transform is fit on training rows only**: median imputation
(`train_median_imputer`), standardization (`train_standardizer`), the de-meaned target, and the
ridge weights. Test rows are transformed with the training parameters and never refit. Zero-variance
training features are dropped safely. The regularization strength λ ∈ {0.1, 1, 10, 100} is chosen
on an **inner 80/20 time split of the training window only** (no test leakage). Folds with
insufficient training or test rows are skipped and documented.

### Regime-aware ensemble

Within each fold, a risk-on (calm) and a risk-off / high-vol sub-model are trained on the
`combined_with_partial_earnings` feature set; each test row is routed to its regime's sub-model.
If a regime subset has fewer than the minimum training rows, that regime **falls back to the
global model**, and the fallback is recorded in `walkforward_fold_summary.csv`.

## Evaluation metrics

For each model and horizon the scoreboard reports `fold_count`, `train_rows_total`,
`test_rows_total`, `test_tickers`, `mean_daily_rank_ic`, `median_daily_rank_ic`,
`rank_ic_hit_rate`, `top_decile_minus_bottom_decile_spread`,
`top_quintile_minus_bottom_quintile_spread`, `top_decile_hit_rate`, `annual_coverage_years`,
`worst_year_ic`, `best_year_ic`, and `stability_score` (fraction of test years with positive mean
daily rank IC). IC is the **cross-sectional daily Spearman rank IC** — the same statistic as
Phase 3-L / 3-M / 3-O — pooled across out-of-sample test folds.

## Decision gates

| Recommendation | Condition |
|---|---|
| `MULTISIGNAL_WALKFORWARD_MODEL_RESEARCH_PASS` | ≥4 yearly folds, ≥75 test tickers, best model mean daily rank IC ≥ 0.03 on ≥1 horizon, positive decile spread, hit rate ≥ 0.52, worst-year IC > −0.05 |
| `MULTISIGNAL_WALKFORWARD_MODEL_WEAK_BUT_PROMISING` | ≥4 folds, best IC ≥ 0.015, positive decile spread, but not a full pass |
| `MULTISIGNAL_WALKFORWARD_MODEL_FAILS_ROBUSTNESS` | enough data but IC / spread weak or unstable |
| `MULTISIGNAL_WALKFORWARD_MODEL_BLOCKED_INPUTS` | Phase 3-O result or a required panel missing / unconfirmed |

`recommended_next_phase.phase` is always **`3-Q`**: Research Model Robustness/Turnover/Risk
Simulation on PASS; Add Macro/Sentiment + finish earnings coverage on WEAK; Rework Feature
Families and Targets on FAILS; Repair Phase 3-P Inputs on BLOCKED.

## Outputs

Result JSON `research/output/phase3p_multisignal_walkforward_model.json` plus, under
`research/output/phase3p_multisignal_walkforward_model/`: `model_feature_set.csv`,
`walkforward_fold_summary.csv`, `model_scoreboard.csv`, `yearly_model_stability.csv`,
`feature_weight_summary.csv`, `readiness_decision_table.csv`. Every file is **Git-safe (< 50 MB)**.

## Safety flags (result JSON)

`database_touched`, `database_write_executed`, `migration_executed`, `deployment_executed`,
`model_v2_enabled`, `production_edge_claimed`, `production_model_trained`,
`production_model_candidate_created`, `deployable_model_artifact_written`,
`production_predictions_computed`, `production_scores_computed`, `portfolio_weights_computed`,
`order_instructions_created`, `d_drive_written`, `provider_api_called`, `alpha_vantage_called`,
`paid_vendor_api_called`, `macro_faked`, `sentiment_faked` are all **false**; `no_trading`,
`no_orders`, `no_automation`, `research_models_trained_in_memory`, `research_oos_scores_computed`,
`d_drive_read`, `labels_for_validation_only` are **true**.

## Run

```powershell
# Windows PowerShell only.
python -B research\run_phase3p_multisignal_walkforward_model.py
python -B tests\test_phase3p_multisignal_walkforward_model.py
# Optional full end-to-end test into a temp dir:
$env:PHASE3P_LIVE = "1"; python -B tests\test_phase3p_multisignal_walkforward_model.py
```
