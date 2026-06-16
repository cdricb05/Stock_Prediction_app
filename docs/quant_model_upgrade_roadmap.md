# Quant Model Upgrade Roadmap

Status: **Phase 1 (this change) — research/evaluation only.** No model rewrite,
no live-API change, no deployment, no automation, no fabricated features.

This roadmap is intentionally honest about what the current system is and is not.
Phase 1 builds the evidence base; later phases are gated on that evidence.

## Where we are (current production model)

`api_server.py` trains a small suite of **price-only** models per request on
adjusted close:

- Fast set (default `PREDICTOR_FAST_ONLY=1`): Drift, LinearTrend,
  XGBoost-on-returns, Naive, SMA.
- Optional: Prophet, ARIMA, ETS, LSTM.

It returns a 5-business-day point forecast, a BUY/HOLD/SELL recommendation,
a "confidence", an "agreement", residual alpha vs SPY, a z-score, and a rationale.

Known limitations (the reason for this roadmap):

- **Confidence is not a probability.** It is `100 − coefficient_of_variation` of
  the per-model day-5 forecasts — a dispersion measure, not a calibrated chance
  of being right.
- **Reported metrics are in-sample fit error** (MAE%/RMSE% of fitted values),
  not out-of-sample skill. A model can have tiny fitted error and zero edge.
- **No prediction interval / no calibrated downside.** `lo`/`hi` are `None`.
- **No real performance history** and **no proof of out-of-sample edge.**
- **Data quality:** `stock_prices` contains ~1.6k duplicate `(ticker,date)`
  rows; SPY history starts 2023-07-24; most tickers start 2024-01-02. The
  research harness de-duplicates and bounds windows accordingly.

## Phase 1 — Walk-Forward Baseline Harness (this change)

Goal: answer "does the current model have any measurable out-of-sample edge?"
without changing anything live.

Delivered (additive, under `research/`, `tests/`, `docs/`):

- `research/walk_forward_dataset.py` — point-in-time target builder
  (5-session forward return, SPY excess, positive/outperform flags), de-dups
  `stock_prices`, never uses future data in features.
- `research/current_model_baseline.py` — replays the **exact** production model
  functions (imported from `api_server`) over historical as-of dates on
  point-in-time slices.
- `research/metrics.py` — numpy-only metrics: rank IC / Spearman, precision@K,
  confidence-decile calibration, BUY/HOLD/SELL confusion matrix, drawdown.
- `research/run_walk_forward_baseline.py` — CLI + honest report generator →
  `docs/current_model_walk_forward_baseline_v1.md`.
- `tests/test_walk_forward_baseline.py` — pure-logic tests (no DB, no pytest
  needed; self-running).

Acceptance: live service unchanged, no restart, no deploy, no schema change,
fast-smoke runs, report can be generated on the VM, report states edge honestly.

## Phase 2 — Calibration & uncertainty (gated on Phase 1 showing any signal)

- Replace dispersion "confidence" with a **calibrated probability** (e.g.
  isotonic / Platt on walk-forward folds); report Brier score and reliability.
- Add **prediction intervals** (quantile models or conformal prediction) so
  downside is quantified, not implied.
- Decision rule driven by calibrated probability × expected move, not raw rel%.

## Phase 3 — Honest features (no fabrication, point-in-time only)

Only features with **true point-in-time availability** and no look-ahead:

- Price/volume-derived factors (momentum, volatility, liquidity) computed
  strictly from data ≤ as_of.
- Real fundamentals / earnings / macro **only** if a vetted point-in-time source
  is wired in. No synthetic news, sentiment, macro, seasonality, or fundamentals.
- Every feature gets a leakage test before it is allowed into training.

## Phase 4 — Proper validation before any "quant-grade" claim

- Walk-forward / purged-embargoed cross-validation with confidence intervals.
- Regime splits (bull/bear/high-vol) and per-sector breakdowns.
- Transaction-cost-aware, turnover-aware backtest of the ranking it would drive.
- Pre-registered success criteria; no quant-grade claim without this evidence.

## Phase 5 — Productionization (only if Phase 4 passes)

- Versioned models + scheduled walk-forward refresh, monitored live IC decay.
- Still preview/manual-review by default; broker execution and automation remain
  out of scope unless separately and explicitly approved.

## Guardrails (all phases)

- No fabricated data of any kind. No live broker execution, no order execution,
  no automation. No schema migration without explicit approval. Changes must be
  reviewable and testable. "Quant-grade" is a claim that requires walk-forward
  evidence — not a default.
