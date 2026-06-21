# Phase 5-A — model_v2 Preview Pricing Engine (v1)

## What was built

A self-contained, offline, **preview-only** pricing/prediction package — the
first operational version of the new "brain" — that scores S&P 500 movement
(via the **SPY** proxy) and returns a stable prediction payload the GCP
prediction service can serve in Phase 5-B.

Package (`research/model_v2_pricing_engine/`):

| File | Purpose |
| --- | --- |
| `__init__.py` | Public API: `score_market_v2`, `score_ticker_v2`, identity constants. |
| `features.py` | Pure (numpy-only) point-in-time feature math. No DB / network. |
| `scorer.py` | Local-CSV loader + transparent directional scoring model + contract assembly. |
| `manifest.json` | Model identity, methodology, data inputs, deployment plan, safety. |
| `feature_schema.json` | Machine-readable description of every v1 feature. |

Supporting:

- `research/run_phase5a_build_model_v2_pricing_engine.py` — locate data, validate
  package, score SPY, write outputs, print summary.
- `tests/test_phase5a_model_v2_pricing_engine.py` — contract / safety / degradation
  / no-network / runner-output tests.
- `research/output/phase5a_model_v2_pricing_engine.json` — engine + validation report.
- `research/output/phase5a_model_v2_sample_spy_prediction.json` — sample SPY payload.

This phase is **not** an attempt to perfect the model. It produces a deployable,
versioned `model_v2` package that we can serve and then improve iteratively.

## Data source used

Local price history only — **no network, no AlphaVantage, no paid APIs**.

- **Primary:** `D:\Stock_Prediction_app_data\phase2k_g\output\phase2k_g_expanded_price_history_free.csv`
  (read-only input; never written). Columns
  `ticker,date,adjusted_open,adjusted_high,adjusted_low,adjusted_close,volume`.
  SPY: **2,628 sessions, 2016-01-04 → 2026-06-16**.
- **Fallback:** `research/output/phase2g_price_history_real.csv` (columns
  `ticker,date,adj_close,volume`).
- **Override:** environment variable `MODEL_V2_PRICE_HISTORY_CSV`.

The loader tolerates differing close-column names (`adjusted_close` / `adj_close`
/ `close`) and picks the first readable file containing the ticker.

## Current scoring methodology

A **transparent, deterministic directional score** — deliberately simple and
deployable, honest about being a first version.

Features (price-only, point-in-time, trailing window only):

- **Momentum:** `return_21d`, `return_63d`, `return_126d`.
- **Trend:** `sma_50`, `sma_200`, `price_vs_sma_50`, `price_vs_sma_200`,
  `sma_50_vs_200` (moving-average trend / golden-cross proxy).
- **Volatility:** `realized_vol_21d`, `realized_vol_63d` (annualized).
- **Drawdown:** `drawdown_from_peak_252d`.
- **Mean reversion:** `return_zscore_21d`.
- **Volume (optional):** `volume_zscore_21d` (only when a real volume series is
  present; never fabricated).

Each sub-signal is bounded into `[-1, 1]` (sign-of-trend and `tanh`-damped
momentum / z-score), combined with fixed **judgement-set** weights, and
renormalized over the weight that had usable inputs:

```
trend_200 0.30 | trend_50_200 0.20 | mom_63 0.20 | mom_21 0.15 | meanrev 0.15
signal_score = sum(weight_i * subsignal_i) / sum(available weight_i)   in [-1, 1]
```

Mapping:

- `prediction`: `signal_score >= +0.15 → UP`, `<= -0.15 → DOWN`, else `NEUTRAL`;
  no usable signal → `INSUFFICIENT_DATA`.
- `confidence`: heuristic strength × coverage, **capped at 0.60** (calibration is
  limited — see below). Floor 0.05.
- `expected_return`: damped trailing-horizon momentum, shrunk 40% toward zero
  (`0.40 × return_21d`); `null` when unavailable, `0.0` for NEUTRAL.
- `risk_regime`: `risk_on` (uptrend + calm vol/drawdown), `risk_off` (downtrend +
  high vol or deep drawdown), `neutral`, or `unknown` (no trend/vol data).

Horizon: **21 sessions (~1 month)**.

### Latest SPY run (as of 2026-06-16)

`prediction=UP`, `signal_score≈0.684`, `expected_return≈0.0060`,
`confidence≈0.458`, `risk_regime=risk_on`, `calibration_status=limited`.

## Current prediction contract

`score_market_v2("SPY")` / `score_ticker_v2(ticker)` return a dict with:

```
model_version, model_name, target_ticker, as_of_date, horizon,
prediction (UP|DOWN|NEUTRAL|INSUFFICIENT_DATA),
expected_return (float|null), confidence (0..1),
risk_regime (risk_on|risk_off|neutral|unknown),
signal_score (float|null), calibration_status, calibration_limitations[],
features_used[], missing_features[], data_as_of, input_data_source,
preview_only=true, orders_enabled=false, automation_enabled=false,
broker_execution_enabled=false, production_replacement=false
```

`score_market_v2` additionally tags `market_proxy` and `is_market_view=true`.

## Calibration limitations (why `calibration_status = "limited"`)

This is an **honest, transparent heuristic**, not a "senior quant" model:

1. Weights are **judgement-set, not walk-forward fit** to realized outcomes.
2. `confidence` is a strength/coverage gauge, **not a calibrated probability**
   (no out-of-sample Brier / log-loss), so it is **capped at 0.60**.
3. `expected_return` is damped momentum, **not a calibrated point forecast**.
4. **Price-only** feature set — no fundamentals, macro, earnings, or sentiment.
5. Single-asset market-direction view; no cross-sectional or multi-horizon
   ensemble yet.

These are spelled out per-run in the payload's `calibration_limitations`.

## Why AlphaVantage / provider limitations do not block packaging

Packaging depends only on **local** price history. If a provider is rate-limited
or a file is missing, the loader simply finds the next configured source; if none
yields the ticker, the engine **degrades** — `prediction=INSUFFICIENT_DATA`,
`calibration_status=limited`, features marked missing — and still returns a valid,
serializable contract. Missing data lowers confidence and marks limitations; it
**never raises and never stops the package**.

## How this deploys to GCP in Phase 5-B

The package is import-clean with no DB/network/paid-API dependency, so it lifts
into the remote `stock-api.service` (project `stock-prediction-app-466420`, VM
`stock-prediction-vm-new`, zone `us-central1-a`, port 8000, tunnel
`http://127.0.0.1:9000`) as-is. Phase 5-B will:

1. Vendor the package onto the VM and point `MODEL_V2_PRICE_HISTORY_CSV` at the
   server-side price history (or bundle a refreshed SPY history file).
2. Add two **flag-off, preview-only** endpoints that wrap the scorer:

   - `GET /predict_v2/{ticker}` → `score_ticker_v2(ticker)`
   - `GET /predict_market_v2` → `score_market_v2("SPY")`

3. Keep model_v2 behind a feature flag, parallel to the existing model, until
   shadow validation and probability calibration are complete.

## Why this is preview-only (not broker execution yet)

Every payload hard-codes `preview_only=true` and
`orders_enabled / automation_enabled / broker_execution_enabled /
production_replacement = false`. The model is an uncalibrated first version: it
informs a **manual-review** preview only. Orders, automation, broker execution,
and any production-model replacement are explicitly out of scope until the model
is calibrated and validated in later phases.

## Reproduce

```powershell
Set-Location "C:\Users\binis\Stock_Prediction_app_push"
python research\run_phase5a_build_model_v2_pricing_engine.py
python -m pytest tests\test_phase5a_model_v2_pricing_engine.py -q
```
