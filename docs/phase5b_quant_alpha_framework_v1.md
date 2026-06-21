# Phase 5-B — Quant Alpha Framework for S&P 500 Ticker Selection (v1)

## What this phase is (and is not)

This phase is the **quant brain specification** — the design contract that
Phase 5-C will implement. It is **not** UI work, **not** a GCP deploy, **not**
broker execution, and **not** a trained model. Nothing here is fitted, served,
or executed. It defines *what* the ticker-selection model will be, *how* it will
be validated, and *what must pass* before it is ever trusted.

The canonical, machine-readable contract is
[`research/model_v2_pricing_engine/alpha_framework.json`](../research/model_v2_pricing_engine/alpha_framework.json).
The runner [`research/run_phase5b_quant_alpha_framework.py`](../research/run_phase5b_quant_alpha_framework.py)
validates it and derives the catalog/gate/plan artifacts under `research/output/`.

## SPY is the regime proxy, not the trading universe

The business goal is **not** to trade SPY. SPY is the **benchmark and
market-regime proxy**. The real trading universe is **S&P 500 equities**. The
model's job is to **rank tickers** cross-sectionally and propose
**BUY / SELL / HOLD / AVOID** candidates.

SPY enters the system in exactly one place: a **market-regime layer** computed
once per date from the SPY price series (via the Phase 5-A `score_market_v2`
engine). That regime — trend, volatility percentile, drawdown, and a composite
risk-on/off score — then **conditions** the cross-sectional ranking: it sets how
aggressive long exposure may be, how high the BUY bar sits, and which risk flags
dominate. SPY is the weather; the S&P 500 names are what we actually pick.

## How S&P 500 tickers will be ranked

On each date, every constituent (SPY excluded) is scored point-in-time across six
feature families:

1. **Trend / momentum** — 5/20/63/126d returns, momentum acceleration,
   price-vs-SMA50/200, SMA50-vs-200, moving-average slope.
2. **Mean reversion / overextension** — 21d return z-score, distance from SMA20,
   an RSI-style proxy from price returns.
3. **Relative strength** — return minus SPY (20d, 63d), beta-adjusted residual
   (alpha) return, sector-relative return *(future — needs sector mapping)*, and
   cross-sectional percentile rank.
4. **Risk / volatility** — realized vol (21/63d), downside (semi-)vol, max
   drawdown, beta to SPY, idiosyncratic vol, volatility-adjusted momentum.
5. **Liquidity** — average dollar volume, volume trend, volume-shock z-score, and
   a hard minimum-liquidity gate.
6. **Event / fundamental / sentiment placeholders** — earnings surprise, analyst
   revisions, news/sector sentiment, narrative, fundamental quality, valuation.
   **None are available from local price history**, so they are catalogued as
   `future`/`missing` and the framework degrades safely rather than fabricating.

Features combine into a regime-adjusted composite `signal_score` in `[-1, 1]`;
the universe percentile of that score is the `rank_score` in `[0, 1]`, which
drives candidate selection. See
[`research/output/phase5b_feature_catalog.csv`](../research/output/phase5b_feature_catalog.csv)
for the full machine-readable catalog (layer, family, availability, data source).

## Why this is mathematically different from UI polishing

UI work rearranges how an *existing* answer is displayed. This work defines the
**answer**: a cross-sectional ranking function over ~500 assets, the forward
**labels** it optimizes (primary: **20d forward excess return vs SPY**), the
**estimator families** allowed, and the **statistical evidence** (rank IC, decile
spread, cost-survival, regime robustness, no-leakage) required before the ranking
is trusted at all. It is an estimation-and-validation problem with look-ahead
hazards and overfit risk — not a layout problem.

## Labels / targets

- **Primary:** `forward_excess_return_20d_vs_spy` — 20-session forward return
  minus SPY's, the natural cross-sectional alpha target.
- **Secondary:** `top_quintile_forward_excess_return_probability` (classification)
  and `downside_risk_probability` (forward drawdown/loss beyond a threshold).
- **Auxiliary:** 5/20/63d forward returns and bottom-quintile downside class.

All labels are forward-looking and exist **only** for backtest/validation; they
are never available at scoring time. See
[`research/output/phase5b_label_catalog.csv`](../research/output/phase5b_label_catalog.csv).

## Model families (staged)

1. **Stage 1 — baseline deployable (Phase 5-C):** transparent cross-sectional
   **composite z-score** with **fixed judgement-set weights** plus the SPY
   **regime adjustment**. No fitted coefficients; fully auditable and
   reproducible offline. This is the only model 5-C ships.
2. **Stage 2 — statistical:** ridge / elastic-net regression and logistic
   regression for top-quintile probability. Promotion requires out-of-sample
   rank IC ≥ baseline, stable coefficients, and calibrated probabilities.
3. **Stage 3 — later candidates:** gradient-boosted trees, regime-specific and
   sector-neutral models, ensembles, Bayesian shrinkage, drift-aware monitoring.
   Each requires durable, cost-surviving out-of-sample improvement over Stage 2.

Promotion is **evidence-gated**: a fancier model is used only after the gates
below confirm it actually adds edge that survives costs and regimes.

## Scoring design and actions

Each ticker yields: `signal_score`, `rank_score`, `expected_return`,
`confidence`, `action`, `reason_codes`, `risk_flags`.

- **BUY** — top-rank, positive regime-adjusted signal, passes liquidity, no hard
  risk flag. In **risk_off** the BUY bar rises and the BUY budget shrinks.
- **SELL** — for an existing **paper** long whose signal has turned negative /
  rank collapsed / a hard risk flag fired → preview recommendation to exit/trim.
  **No short selling is implied**; SELL never opens a new short.
- **HOLD** — mid-rank / marginal names with no decisive signal; default in
  neutral regime.
- **AVOID** — do **not** initiate: low/negative rank, failed liquidity gate, or
  severe risk flags.
- **INSUFFICIENT_DATA** — not enough point-in-time history/features (or regime
  unknown); never guessed.

**Regime → exposure:** risk_off reduces long exposure and tilts to low-beta;
risk_on permits the full BUY budget and a momentum tilt; neutral caps conviction
and widens HOLD; unknown degrades toward HOLD/INSUFFICIENT_DATA.

**Confidence capping:** while `calibration_status` is `limited` (no out-of-sample
probability calibration yet), confidence is **capped at 0.60**. Missing features,
unknown regime, or thin liquidity reduce it further. Confidence must never
advertise probabilistic precision the model has not earned.

## Validation methodology (senior-quant)

Walk-forward validation with **purged/embargoed** splits (to kill horizon
leakage), then: **rank IC** (overall, by year, by regime), **top-minus-bottom
decile spread**, top-quintile hit rate, **turnover**, **transaction-cost
survival**, max drawdown, Sharpe (with caveats), sector concentration, beta
exposure, liquidity/capacity, feature-importance stability, confidence
calibration, and **shadow-trading capture** (preview picks logged live, no
orders, compared to realized outcomes).

## Acceptance gates (go / no-go)

See [`research/output/phase5b_validation_gate_matrix.csv`](../research/output/phase5b_validation_gate_matrix.csv).
Gates are blocking and staged by *when* they apply:

- **Before Phase 5-C preview ranking:** minimum rank IC (G1), positive decile
  spread (G2), transaction-cost survival (G3), turnover (G4), no-leakage (G7),
  no-fake-data (G8), plus the always-on safety gates.
- **Before GCP deployment:** drawdown (G5), regime robustness (G6).
- **Before any execution (paper, then live):** paper/shadow validation (G13).
- **Always on:** preview-only (G9), no-orders (G10), no-broker-execution (G11),
  no-automation (G12).

A gate that cannot yet be evaluated is recorded as **NOT_YET_EVALUATED** and
**blocks** promotion — it is never silently passed.

## What Phase 5-C will build

In `research/model_v2_pricing_engine`, on top of the Phase 5-A engine:

```python
score_ticker_v2(ticker: str) -> dict                       # extend: action/reason_codes/risk_flags
rank_universe_v2(max_tickers: int | None = None) -> dict    # new: cross-sectional ranking + regime overlay
generate_trade_candidates_v2(top_n: int = 20) -> dict       # new: BUY/SELL/HOLD/AVOID candidate lists
```

Full response schemas and the build order are in
[`research/output/phase5b_phase5c_implementation_plan.json`](../research/output/phase5b_phase5c_implementation_plan.json).
All three return preview payloads with `orders_enabled` / `broker_execution_enabled`
/ `automation_enabled` = false.

## What is implemented now vs later

- **Now (5-B):** the framework spec, feature/label catalogs, validation-gate
  matrix, and the Phase 5-C implementation plan. **No model, no training, no
  ranking code, no endpoints.**
- **Later:** 5-C builds the baseline ranking + validation harness (preview-only);
  5-D/5-E add the GCP preview endpoints below.

## What data is missing and how it degrades safely

Local price history (the D: SPY/constituent CSV and repo fallbacks) supports the
trend, mean-reversion, relative-strength, risk, and liquidity families. It does
**not** carry fundamentals, estimates, earnings, sector mapping, news, or
sentiment. Those features are catalogued with `availability: future` and a named
`data_source` that is explicitly *not local*. When data is absent the model marks
the feature in `missing_features`, lowers confidence, and — when too little
remains — returns `INSUFFICIENT_DATA`. **It never fabricates a value.** Provider
limitations therefore degrade features; they never block the framework.

## Future GCP endpoints (Phase 5-D / 5-E — not implemented now)

- `GET /predict_market_v2` → `score_market_v2("SPY")`
- `GET /predict_v2/{ticker}` → `score_ticker_v2(ticker)`
- `GET /rank_universe_v2` → `rank_universe_v2(max_tickers)`
- `GET /trade_candidates_v2` → `generate_trade_candidates_v2(top_n)`

All GET, read-only, flag-gated, preview payloads. Not built in this phase.

## Why the model stays preview-only until validated

The model is uncalibrated and unproven. Until the edge gates (G1–G4, G7–G8) pass
on walk-forward out-of-sample data, the deployment gates (G5–G6) pass, and a
shadow-trading period (G13) confirms the preview picks behave as claimed, the
output informs **manual review only**. Orders, broker execution, automation, and
production replacement remain hard-disabled.

## Reproduce

```powershell
Set-Location "C:\Users\binis\Stock_Prediction_app_push"
python research\run_phase5b_quant_alpha_framework.py
python -m pytest tests\test_phase5b_quant_alpha_framework.py -q
```
