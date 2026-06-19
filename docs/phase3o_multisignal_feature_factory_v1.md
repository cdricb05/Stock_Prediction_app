# Phase 3-O — Multi-Signal Feature Factory + Research Baseline Model Gate (v1)

## Purpose

Phase 3-O builds the first **unified, research-only multi-signal feature factory** for the
current ~128-equity universe. It deliberately does **not** wait for the Phase 3-M / 3-N Alpha
Vantage earnings collection to finish (only 25 of 128 tickers are cached so far). Instead it
assembles every signal family it can from data that **already exists locally** and produces a
unified research feature panel, a feature registry, cross-sectional IC diagnostics, and a
baseline-model scoreboard.

This phase is **research-only**. It does not deploy, does not restart stock-api.service,
does not enable `PREDICTOR_USE_MODEL_V2`, does not run migrations, does not write to production DB,
and does not trade. It fits no production model, creates no production model
candidate, writes no deployable model artifact, computes no production predictions / scores /
portfolio weights, writes nothing to the D: drive, calls no Alpha Vantage / provider / paid-vendor
API, places no orders, and implements no automation. The universe is current-as-of, so every
result remains survivorship-biased and claims **no production edge**.

## Inputs (all read-only)

| Input | Path | Use |
|---|---|---|
| Price panel | `D:\Stock_Prediction_app_data\phase2k_g\output\phase2k_g_expanded_price_history_free.csv` | technical / seasonality / regime / sector / time-series features (D: **read-only**) |
| Price data-quality | `…\phase2k_g_data_quality_report.json` | provenance |
| Survivorship caveat | `…\phase2k_g_survivorship_caveat.json` | caveat provenance |
| Sector map | `research/input/phase2k_p_sector_map_current.csv` | sector-relative features (current-as-of, **not** point-in-time) |
| Phase 3-L panel | `research/output/phase3l_sec_universe_signal_gate/aligned_feature_price_panel_universe.csv` | **spine**: `(ticker, scoring_date)`, SEC features, forward labels |
| Phase 3-M earnings | `research/output/phase3m_earnings_estimates_signal_gate/earnings_features_universe.csv` | earnings-surprise features (partial, as-of merge) |
| Phase 3-M progress | `research/output/phase3m_earnings_estimates_signal_gate/collection_progress.json` | record cache count only — **no fetch** |

The earnings cache count is recorded for provenance only; **no new earnings data is fetched** and
no provider API is called.

## How the panel is built

The Phase 3-L aligned panel is the **spine**: each row is `(ticker, scoring_date)` with the SEC
fundamental features and the validation-only forward labels
(`forward_excess_return_vs_spy_{21,63,126}d`) already computed. Phase 3-O computes the other
families from the local price panel at each `scoring_date` (using only prices **up to and
including** that date — point-in-time, no lookahead), merges them onto the spine, and as-of-merges
the partial earnings features.

Forward labels are used **for validation/IC diagnostics only**; they are never converted into
predictions.

## Feature families and the registry

`feature_registry.csv` declares every family with columns `feature_name, feature_family,
input_source, point_in_time_rule, availability_lag_rule, uses_future_data, implemented, blocker,
notes`. The nine required families:

| Family | Implemented | Notes |
|---|---|---|
| `technical_price` | yes | trailing returns / vol / drawdown / SMA / skew / kurtosis |
| `seasonality_calendar` | yes | calendar flags + prior-years-only historical same-month stats |
| `market_regime` | yes | SPY trailing + cross-sectional dispersion/breadth (regime context) |
| `sector_relative` | yes | ticker-vs-sector / sector-vs-SPY / within-sector ranks |
| `sec_fundamental` | yes | reused from Phase 3-L (not recomputed) |
| `earnings_surprise` | yes (partial) | as-of merge; only the 25 Phase 3-M cached tickers carry values |
| `macro_inflation` | **no** | `blocker=external_macro_data_required` — declared, **not faked** |
| `sentiment` | **no** | `blocker=external_sentiment_data_required` — declared, **not faked** |
| `time_series_arima_style` | yes | local AR-style diagnostics, no statsmodels needed |

### Macro and sentiment are not faked

There is no local macro or sentiment data. Rather than fabricate values, Phase 3-O lists the
proposed macro features (`cpi_yoy`, `cpi_mom`, `inflation_regime`, `10y_yield`, `2y_yield`,
`yield_curve_10y_2y`, `real_rate_proxy`, `inflation_shock_flag`, `fed_policy_regime`) and sentiment
features (`news_sentiment_avg_7d`, `news_sentiment_avg_30d`, `sentiment_momentum`,
`negative_news_intensity`, `event_count_7d`, `analyst_tone_proxy`) in the registry with
`implemented=false` and blockers `external_macro_data_required` / `external_sentiment_data_required`.
They **are not faked**. The result JSON carries `macro_faked: false` and `sentiment_faked: false`.

### ARIMA is one signal family, not the whole model

The `time_series_arima_style` family (`ar1_beta_63d`, `ar1_residual_zscore_63d`,
`rolling_mean_reversion_signal_21d`, `trend_persistence_63d`, `forecast_error_direction_21d`) is
computed with simple local AR-style math and **does not require statsmodels**. ARIMA is treated as
**one signal family among many**, not the entire model. If statsmodels happens to be installed an
optional ARIMA path could be added later, but it is non-blocking and currently unused.

## IC diagnostics

`feature_ic_summary.csv` / `feature_family_ic_summary.csv` use the **same methodology as Phase
3-L / 3-M**: cross-sectional **daily Spearman rank IC** against
`forward_excess_return_vs_spy_{21,63,126}d`. Rows are grouped by `scoring_date`; within each date
(with at least 15 names) the IC is the correlation of the within-date feature and label ranks; ICs
are then summarized across dates (mean, median, sign hit-rate, information ratio).

Market-regime and calendar features are **identical across tickers on a given date**, so their
cross-sectional IC is undefined by construction — those rows are emitted with a note rather than a
misleading number. They are regime/calendar **conditioning** context, not cross-sectional alphas.

## Baseline model scoreboard

`baseline_model_scoreboard.csv` is **research-only** — no portfolio weights, no trade
recommendations, no production scores. Each model is a cross-sectional ranking signal scored per
date; per horizon it reports `sample_rows, sample_tickers, mean_forward_excess_return,
information_coefficient, top_decile_minus_bottom_decile_spread, hit_rate, annual_coverage_years,
notes`. Models: `benchmark_spy`, `equal_weight_universe`, `momentum_rank_composite`,
`seasonality_rank_composite`, `market_regime_adjusted_momentum`, `sector_neutral_momentum`,
`ar_style_mean_reversion`, `sec_fundamental_rank_composite`, `combined_multisignal_rank_composite`.

## Outputs

All under `research/output/phase3o_multisignal_feature_factory/` plus the result JSON
`research/output/phase3o_multisignal_feature_factory.json`. Every file is **Git-safe (< 50 MB)**;
`research_feature_panel_sample.csv` is a strided sample of the full in-memory panel (the full panel
is not committed). The result JSON includes `implemented_feature_families`,
`blocked_feature_families`, `feature_count_by_family`, `rows_in_feature_panel`,
`tickers_in_feature_panel`, `date_range`, `best_feature_families`,
`baseline_model_scoreboard_summary`, `macro_inflation_status`, `sentiment_status`,
`arima_style_status`, `recommendation`, and `recommended_next_phase`.

## Recommendation

| Recommendation | Meaning |
|---|---|
| `MULTISIGNAL_FEATURE_FACTORY_SUCCESS_READY_FOR_RESEARCH_MODEL` | every family (incl. macro + sentiment) implemented and signal present |
| `MULTISIGNAL_FEATURE_FACTORY_PARTIAL_MACRO_SENTIMENT_MISSING` | all locally-available families built with signal; macro + sentiment still blocked |
| `MULTISIGNAL_FEATURE_FACTORY_BLOCKED_INPUTS` | a required local input was missing |
| `MULTISIGNAL_FEATURE_FACTORY_FAILS_SIGNAL_CHECK` | inputs present but no family/baseline cleared the faint IC floor |

`recommended_next_phase.phase` is always **`3-P`**. On success or partial the next phase is a
*Research-Only Multi-Signal Walk-Forward Model* (with macro/sentiment added in parallel); if blocked,
repair inputs first; if it fails the signal check, rework the feature families before any training.

## Safety flags (result JSON)

`database_touched`, `database_write_executed`, `migration_executed`, `deployment_executed`,
`model_v2_enabled`, `production_edge_claimed`, `production_model_trained`,
`production_model_candidate_created`, `deployable_model_artifact_written`, `d_drive_written`,
`provider_api_called`, `paid_vendor_api_called`, `alpha_vantage_called`, `sentiment_faked`,
`macro_faked`, `production_predictions_computed`, `portfolio_weights_computed` are all **false**;
`no_trading`, `no_orders`, `no_automation`, `d_drive_read`, `labels_for_validation_only` are
**true**.

## Run

```powershell
# Windows PowerShell only.
python -B research\run_phase3o_multisignal_feature_factory.py
python -B tests\test_phase3o_multisignal_feature_factory.py
# Optional full end-to-end test into a temp dir:
$env:PHASE3O_LIVE = "1"; python -B tests\test_phase3o_multisignal_feature_factory.py
```
