# Phase 3-A — Greenfield Research Model Baseline (v1)

_Implemented by `research/train_phase3a_greenfield_baseline.py` and validated by
`tests/test_phase3a_greenfield_baseline.py`. Phase 3-A is a **research-only model-training
phase**: it builds a fresh feature set from the expanded D: price / volume panel plus the
populated current-as-of sector map and trains baseline models from scratch under real
walk-forward out-of-sample validation. It trains research models offline only — it creates no
production model candidate, writes no deployable model artifact, and claims no production edge._

> Scope and safety. This phase reads the expanded D: price-history CSV and its provenance JSONs
> **read-only** and reads the small C: sector-map / phase summaries; it writes only three small
> files under `research/output` (a results JSON and two summary CSVs) and **nothing to the D:
> drive** and **no model pickle / joblib / binary**. It is research tooling: it
> **does not deploy**, it **does not restart stock-api.service**, it **does not enable** the model-v2
> serving flag, it **does not run migrations**, it **does not write to production DB**, and it
> **does not trade**. No order placement, no automation, no production model candidate, and no
> deployable artifact happen here, and it claims no **production edge**.

## Why we stopped the Phase 2K rescue path

The Phase 2K track screened, diagnosed, and narrowly retested a handful of single residual
signals. Phase 2K-N reconfirmed three of them (`residual_price_momentum_12_1@5d`,
`short_horizon_residual_reversal_5d@21d`, `short_horizon_residual_reversal_21d@21d`) as positive,
above-zero, year-stable — but **sub-floor**: each had a mean residual rank IC of roughly
0.010–0.015, below the 0.03 confirmation floor, and none cleared the KEEP gate. Continuing to
tune those same weak signals was unlikely to lift them over the bar. Phase 3-A is therefore a
**strategic pivot**: stop rescuing the weak single-signal path and start a clean greenfield
model-research track from scratch.

## Why this is a greenfield rebuild

Instead of re-measuring three pre-registered residual signals, Phase 3-A builds a **fresh, broad
feature set** and trains **multi-feature baseline models** from scratch, asking one question:
*can a model trained from scratch on this price / volume / sector panel produce a robust
out-of-sample signal?* It does not reuse the old single-signal rescue logic, the old KEEP/floor
gates, or the residualization pipeline; it is a new feature → label → walk-forward → model
construction.

## Data used

- **Panel:** `phase2k_g_expanded_price_history_free.csv` — 338,169 rows, 129 series
  (128 equities + SPY benchmark), 2,628 trading dates, 2016-01-04 → 2026-06-16, columns
  `ticker, date, adjusted_open, adjusted_high, adjusted_low, adjusted_close, volume`. Read
  read-only. SPY is treated as the **benchmark only**, never as an equity prediction target
  (128 equity tickers are modeled).
- **Provenance:** `phase2k_g_data_quality_report.json` (status `PASS_WITH_CAVEAT`),
  `phase2k_g_data_build_summary.json`, `phase2k_g_survivorship_caveat.json`.
- **Sector / industry:** `research/input/phase2k_p_sector_map_current.csv` (Phase 2K-Q, 128 rows,
  100% coverage, 11 sectors) with provenance from `phase2k_q_populate_sector_map.json`.

## Survivorship caveat

The universe is **current-as-of / current-membership**, not point-in-time, so it is
**survivorship-biased**. The data build explicitly disclaims any point-in-time membership and
defers a clean point-in-time build. Phase 3-A carries this caveat forward unchanged: every
out-of-sample number reported here is survivorship-biased and is **not a production edge**.

## Sector map caveat

The sector map is **current-as-of 2026-06-18 and not point-in-time** (`point_in_time = false`
for every row). Sector-relative features built from it inherit that caveat; a clean point-in-time
classification would tighten, never rescue, any result.

## Features (strictly trailing; no look-ahead)

43 trailing / point-in-time features across seven families: **price momentum**
(`return_5/10/21/63/126/252d`, `momentum_12_1`), **reversal** (`reversal_5/10/21d`),
**volatility / risk** (`realized_vol_21/63/126d`, `downside_vol_21d`, `max_drawdown_63d`,
`distance_from_63d_high`), **volume / liquidity** (`dollar_volume`, `avg_dollar_volume_21d`,
`volume_zscore_21d`, `volume_trend_21d`), **market-relative** (`excess_return_vs_spy_5/21/63d`,
`rolling_beta_63d`, `rolling_corr_spy_63d`), **sector-relative** (cross-sectional within
date+sector demeans of return / momentum / reversal / volatility), and **cross-sectional ranks**
(market-by-date and sector-by-date percentile ranks of the most informative base features). Every
feature uses only data through the current session; nothing reads forward.

## Labels (strictly forward; never forward-filled)

For horizons **5, 21, 63** trading days: `forward_return`, `forward_spy_return`,
`forward_excess_return_vs_spy`, `binary_outperform_spy`, and a cross-sectional
`forward_return_rank` by date. Labels use only realized future prices and are never forward
filled. Models are scored against `forward_excess_return_vs_spy`.

## Model types

1. **MODEL_FREE_COMPOSITE_BASELINE** — a hand-built blend of signed cross-sectional feature
   ranks; **no training**; used purely as the benchmark a learned model must beat.
2. **RIDGE_LINEAR_RANK_MODEL** — closed-form ridge regression (numpy only, unpenalised
   intercept) on standardized features, trained on past data and predicting forward excess
   return. No sklearn dependency.
3. **OPTIONAL_LOGISTIC_OUTPERFORMER** — a numpy regularised logistic regression (bounded
   gradient descent) on `binary_outperform_spy`. Implemented because it stays simple and stable
   with no new dependency; included, not skipped.

All models are research models trained in-memory per fold. **No model is persisted, pickled, or
written to disk as a deployable artifact.**

## Walk-forward design

Real out-of-sample validation: chronological, **non-overlapping ~6-month** validation windows
(126 sessions) after a **≥3-year** training window (756 sessions), with a **max-horizon embargo**
(63 sessions) between train and validation. Each model trains only on dates strictly before its
validation window. This produced **15 folds**. Metrics are computed separately per model, per
horizon (5/21/63d), and per fold.

## Metrics

Per model / horizon / fold: per-date Spearman **rank IC** (mean, median, std, information ratio,
fraction of positive dates), **top / bottom quintile** forward excess returns and their spread,
**top-quintile hit rate** vs SPY, **positive-spread fraction**, **top-quintile sector
concentration**, and a **turnover proxy**. Aggregated across folds: mean / median rank IC,
**fold win rate**, positive-spread fraction, worst / best fold, by-year IC, and a benchmark
comparison vs the model-free composite. Ridge coefficients are summarized per horizon.

## Recommendation

This run selected **`GREENFIELD_BASELINE_WEAK_BUT_IMPROVABLE`**, routing to Phase 3-B
("Greenfield Feature and Label Refinement"). The best learned configuration was the **ridge model
at the 63-day horizon: mean out-of-sample rank IC ≈ 0.051**, fold win rate ≈ 0.64, positive-spread
fraction ≈ 0.71, mean top-minus-bottom spread ≈ 0.031, and it **beat the model-free composite at
every horizon** (the composite was near zero or negative). The logistic outperformer was also
positive at every horizon. This is materially stronger than the old Phase 2K single-signal path
(~0.013 IC).

It is **not** called promising because one walk-forward fold was **catastrophic** (worst fold
rank IC ≈ −0.114, beyond the −0.05 stability bound): the signal is real but **unstable across
regimes**. Under the conservative rules, an unstable best fold and a still-improvable spread
profile mean the honest verdict is *weak but improvable*, not promising. The feature families
clearly show potential, which is exactly what a refinement phase should pursue.

## Why no production model candidate is created

Even a promising baseline would not justify a production model candidate, and this baseline is
not even promising. No lead from the prior track ever cleared confirmation, the universe is
survivorship-biased, the sector map is current-as-of (not point-in-time), and one fold is
catastrophic. Phase 3-A therefore trains **research models only**: it sets
`research_model_trained = true` but `production_model_trained = false`,
`production_model_candidate_created = false`, and `deployable_model_artifact_written = false`,
keeps the model-v2 serving flag disabled, and claims no **production edge**.

## What Phase 3-B should do

Phase 3-B ("Greenfield Feature and Label Refinement") should refine the feature set, labels,
horizons, and sector handling and then re-run the walk-forward — investigating the catastrophic
fold (which regime drove it), pruning redundant or unstable features, testing horizon and
neutralization choices, and stabilizing the long-short spread — before any stricter robustness
validation or production-candidate discussion. Like every phase in this track, Phase 3-B **does
not deploy**, **does not restart stock-api.service**, **does not enable** the model-v2 flag,
**does not run migrations**, **does not write to production DB**, and **does not trade**, and it
claims no **production edge**.

## Safety flags (from the results JSON)

```
database_touched                    = false
database_write_executed             = false
migration_executed                  = false
deployment_executed                 = false
model_v2_enabled                    = false
production_edge_claimed             = false
no_trading                          = true
no_orders                           = true
no_automation                       = true
research_model_trained              = true
production_model_trained            = false
production_model_candidate_created  = false
deployable_model_artifact_written   = false
d_drive_read                        = true
d_drive_written                     = false
network_used                        = false
```

## Conclusion

Phase 3-A abandoned the sub-floor Phase 2K rescue path and rebuilt from scratch: a 43-feature
trailing panel, strictly-forward 5/21/63d labels, three baseline models, and 15 embargoed
walk-forward folds. A numpy ridge model produced a positive out-of-sample rank IC (~0.051 at 63d)
that beats the model-free composite at every horizon — a genuinely more encouraging starting
point than the old path — but one catastrophic fold makes it **weak but improvable**, not
promising. It trained research models only: it created no production model candidate, wrote no
deployable artifact, read the D: panel read-only and wrote nothing to D:, fetched nothing from
the network, and all results remain survivorship-biased / current-as-of and are **not a
production edge**.
