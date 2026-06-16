# Phase 2B — Walk-Forward Training + Evaluation (v1)

_Offline / research only. This harness changes **no** live behavior: it does not modify `api_server.py`, does not write to the production database, does not deploy, and is not on the request path. It trains a transparent ranking baseline on the Phase 2A features and measures out-of-sample skill._

> **DATA SOURCE: SYNTHETIC SAMPLE.** This report was generated on synthetic `SYN_*` price series with a *planted* momentum signal (a persistent per-ticker drift that trailing returns can estimate), purely to exercise the harness end-to-end. **The numbers below are NOT a market result and do NOT constitute evidence of a production edge.** Any edge shown is the planted synthetic signal being recovered — that only proves the pipeline works.

## Overview

- Source: `SYNTHETIC (planted momentum signal via persistent per-ticker drift, SYN_* tickers; no --db-url given — NOT real market data)`
- Generated: 2026-06-16
- Primary target: `realized_excess_return_5d_vs_spy` (rank/regression)
- Secondary target: `outperform_spy_flag` (probability label; calibrated in Phase 2C)
- Model(s): numpy-only **ridge** (alpha=10.0) with a **mean-z-score composite** fallback. No sklearn, no xgboost, no LSTM/Prophet/ARIMA.
- Features: 19 within-date z-scored columns (`*_z`) from Phase 2A.
- Horizon: 5 sessions; embargo: 5 sessions (>= horizon, so train labels never overlap the test window).
- Out-of-sample rows: 808 across 8 tickers and 4 folds.

## Walk-forward folds

| fold | model | train range | train rows | test range | test rows | tickers | rank IC |
|---|---|---|---|---|---|---|---|
| 0 | ridge | 2023-06-30 → 2023-09-21 | 480 | 2023-09-29 → 2023-11-02 | 200 | 8 | 0.1172 |
| 1 | ridge | 2023-06-30 → 2023-10-26 | 680 | 2023-11-03 → 2023-12-07 | 200 | 8 | 0.2581 |
| 2 | ridge | 2023-06-30 → 2023-11-30 | 880 | 2023-12-08 → 2024-01-11 | 200 | 8 | 0.5175 |
| 3 | ridge | 2023-06-30 → 2024-01-04 | 1080 | 2024-01-12 → 2024-02-16 | 208 | 8 | 0.4110 |

Train dates are strictly before test dates in every fold, separated by a 5-session embargo (purge). This is expanding-window walk-forward — never a random split.

## Out-of-sample metrics

- **Rank IC (Spearman)**: 0.2137
- Pearson IC: 0.3101
- Hit rate, positive return: 0.480
- Hit rate, outperform SPY: 0.465
- Universe baseline (equal-weight, realized excess vs SPY): mean -0.0031, hit 0.465, n 808
- Drawdown (realized 5d returns): worst -0.1003, mean -0.0006, pct negative 0.520

Sub-period rank ICs (stability): 0.2006, 0.3763

Pooled precision@K (positive label = outperform SPY):

| K | precision | feasible |
|---|---|---|
| 5 | 0.600 | yes |
| 10 | 0.500 | yes |
| 25 | 0.720 | yes |
| 50 | 0.840 | yes |

## Top-N vs universe baseline

Per-date top-N selection by score, mean realized EXCESS return vs SPY (each date weighted equally). The model adds value only if a top-N slice beats the 'hold everything' universe row.

| slice | n_dates | avg picks | mean excess | hit rate |
|---|---|---|---|---|
| top-5 | 101 | 5.0 | 0.0032 | 0.511 |
| top-10 | 101 | 8.0 | -0.0031 | 0.465 |
| top-25 | 101 | 8.0 | -0.0031 | 0.465 |
| top-50 | 101 | 8.0 | -0.0031 | 0.465 |
| universe (all) | 101 | 8.0 | -0.0031 | 0.465 |

_On a small synthetic universe, top-25/50 collapse to 'all' (clamped to the number of names per date)._

## Score buckets

Mean realized excess return by score quintile (monotone increasing = the score orders forward excess return).

| bucket | n | score range | mean realized excess | hit rate |
|---|---|---|---|---|
| 1 | 162 | [-0.0224, -0.0116] | -0.0108 | 0.383 |
| 2 | 161 | [-0.0115, -0.0069] | -0.0062 | 0.484 |
| 3 | 162 | [-0.0068, -0.0025] | -0.0134 | 0.358 |
| 4 | 161 | [-0.0025, 0.0158] | -0.0054 | 0.453 |
| 5 | 162 | [0.0162, 0.0505] | 0.0202 | 0.648 |

## Decision gates (plan §8)

- **Gate 1** — Rank IC >= 0.03 and positive each sub-period: rank IC 0.2137, sub-periods [0.2006, 0.3763] → **PASS**
- **Gate 2** — Top-5 beats equal-weight universe (excess return): top-5 excess 0.0032 vs universe -0.0031 → **PASS**
- **Gate 3** — Calibrated probability with monotone buckets / Brier: _deferred to Phase 2C (calibration layer)_
- **Gate 4** — BUY basket beats SPY + universe after costs: _deferred to Phase 2C/2D (decision rule + cost model)_
- **Gate 5** — Honest interval coverage + measured drawdown: _deferred to Phase 2C (risk/interval layer)_

> Gates are computed here only to demonstrate the harness. **A PASS on synthetic data is not a promotion signal** — promotion requires all five gates passing on the real GCP DB across two non-overlapping sub-periods (plan §8).

## Verdict

**HARNESS VALIDATED (SYNTHETIC).** The walk-forward pipeline runs end-to-end and recovers the planted synthetic signal (rank IC 0.2137). This is **not** a market edge and **not** grounds for promotion. Run on the real GCP DB to measure a genuine edge.

## Running this on the real GCP DB

This exact harness runs unchanged against production data — only the input source differs:

```bash
# Real, read-only DB run (small slice), writes the same two artifacts:
python -m model.train --db-url "$DB_URL" --max-tickers 50 \
    --start-date 2022-01-01 --end-date 2025-01-01 \
    --predictions-output research/output/phase2b_oos_predictions.csv \
    --report-output docs/phase2b_walk_forward_training_real.md
```

- With `--db-url`, the dataset comes from `model.features.build_feature_dataset(..., with_labels=True)` (read-only `DISTINCT ON` de-dup), so features/labels share one point-in-time anchor.
- On the real universe the per-date cross-section is large, so the top-25/50 slices and precision@25/50 in gate 2 become meaningful (they collapse to 'all' only on the tiny synthetic universe).
- The current model's walk-forward baseline (`research/run_walk_forward_baseline.py`) is the number to beat: gate 1 requires the new model's rank IC to clear 0.03 and stay positive across two non-overlapping sub-periods.
- **No write-back, no deploy.** Persisting predictions to `prediction_outputs` and wiring the API read path are Phase 2D/2E and stay behind the `PREDICTOR_USE_MODEL_V2` flag.


This synthetic/sample report is not production edge.
This exact phrase is intentionally included so validation confirms the report does not claim production edge.
