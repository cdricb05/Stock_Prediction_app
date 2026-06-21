# Phase 5-C — Cross-Sectional Alpha Research Harness (v1)

## What this phase is (and is not)

Phase 5-C is **Track A — quantitative model evidence**. It builds an offline,
point-in-time **research harness** that answers one question with out-of-sample
statistics:

> *Do these price-only features and models show real, leakage-free edge for
> ranking S&P 500 tickers by 20-day forward excess return vs SPY?*

It is **not** a deployable live scorer, **not** a GCP deploy, **not** UI work,
**not** order/broker/automation code, and it creates **no** trained binary
artifacts. It does not commit. This is deliberately Track A (the quant brain),
not Track B (operational packaging): the deliverable is *mathematical evidence
and a go/no-go recommendation*, not a serving endpoint.

Runner: [`research/run_phase5c_cross_sectional_alpha_research.py`](../research/run_phase5c_cross_sectional_alpha_research.py).
Tests: [`tests/test_phase5c_cross_sectional_alpha_research.py`](../tests/test_phase5c_cross_sectional_alpha_research.py) (17 passing).
Primary report: [`research/output/phase5c_cross_sectional_alpha_research.json`](../research/output/phase5c_cross_sectional_alpha_research.json).

## Data source

Local price history **only**, read-only:
`D:\Stock_Prediction_app_data\phase2k_g\output\phase2k_g_expanded_price_history_free.csv`
(schema `ticker,date,adjusted_open,adjusted_high,adjusted_low,adjusted_close,volume`;
repo fallback `research/output/phase2g_price_history_real.csv`; override
`MODEL_V2_PRICE_HISTORY_CSV`). No network, no AlphaVantage, no paid APIs, no D:
writes. The locally available universe is **128 equities + SPY**, sessions
**2016-01-04 → 2026-06-16**.

> **Survivorship caveat (important):** every local ticker has near-full
> 2016–2026 history, i.e. the universe is a set of *survivors*. Absolute
> portfolio returns below are therefore **upward-biased**. Rank IC is less
> affected (it is cross-sectional within each date) but the surviving-name set
> still excludes delisted/collapsed tickers. This is surfaced as a
> `survivorship_bias_gate = WARNING` and caps the recommendation.

## How SPY is used (benchmark / regime proxy, never the universe)

SPY is **excluded from the trading universe**. It enters in exactly two roles:

1. **Benchmark for the label** — the primary target is each ticker's forward
   return *minus SPY's* over the same window.
2. **Market-regime proxy** — once per rebalance date a regime
   (`risk_on` / `neutral` / `risk_off`) is computed from SPY's trend
   (price vs 200d SMA), 63d realized volatility, and 252d drawdown (mirrors the
   Phase 5-A `score_market_v2` logic). The regime conditions the composite
   model's block weights (e.g. risk-off tilts toward low volatility).

## Universe alignment

Every ticker is reindexed onto SPY's master trading calendar with `NaN` where a
name has no print, so every feature window is index-comparable across names
(required for beta, relative strength, and per-date cross-sectional
standardization). Rebalances occur on the **last trading day of each calendar
month** (~123 dates), which keeps turnover realistic and aligns naturally with
the 20-session forward horizon.

## How features are computed (strictly point-in-time)

At rebalance index `i`, every feature uses only sessions `<= i`. 15 model
features across the six required families:

- **Market regime (SPY):** 20/63/126d SPY return, SMA50/200 trend,
  63d realized vol, 252d drawdown → `risk_on/neutral/risk_off`.
- **Trend / momentum:** `return_20d/63d/126d`, `mom_acceleration`
  (recent 20d return minus prior 20d return), `price_vs_sma50/200`,
  `sma50_vs_200`.
- **Mean reversion / overextension:** `return_zscore_5d` (latest 5d return
  standardized vs its trailing distribution), `dist_sma20`.
- **Relative strength:** `rs_20d_vs_spy`, `rs_63d_vs_spy`,
  `residual_alpha_20d` (= ticker 20d return − β·SPY 20d return, β over 63d).
- **Risk / volatility:** `realized_vol_63d`, `downside_vol_63d`
  (downside deviation), `vol_adj_momentum` (63d return / vol).
- **Liquidity:** `avg_dollar_volume_20d` (drives the liquidity gate).

Missing inputs degrade a single feature to `None` (dropped during
standardization) and never fabricate a value. Each feature is **z-scored
cross-sectionally per date** with a fixed sign so that *higher = more bullish*
(low-vol and overextension features are inverted). Per-date standardization is
leakage-free — it uses only that date's contemporaneous cross-section, no labels.

Features explicitly **missing** from local price data are tracked, not faked:
fundamentals, earnings events, analyst revisions, news sentiment, options /
implied volatility, and macro / rates / commodities.

## How labels are computed (future-only, no leakage)

At index `i`, forward labels use only sessions `> i`:
`forward_return_5d/20d/63d`, the **primary**
`forward_excess_return_20d_vs_spy` (ticker 20d forward return − SPY's),
cross-sectional `top_quintile`/`bottom_quintile` membership on that primary
target, and a `downside_risk_label` (forward 20d return < −10%). Labels exist
only for validation and (for the fitted models) training; they are never
available at scoring time.

## How walk-forward validation avoids leakage

Yearly **walk-forward** folds, **train-on-past / test-on-future**:

- For test year *Y*, training rows come only from dates **before** *Y*.
- A training sample is admitted only if its 20-session label window **closes at
  least 20 trading days (the embargo) before the first test as-of date** — this
  purges horizon overlap between the training labels and the test window.
- Features are past-only; labels are future-only; standardization is per-date
  contemporaneous; the ridge/logistic coefficients are fit on training rows
  only.
- **Computed placebo probe:** after a deterministic within-date label shuffle,
  mean rank IC must collapse to ~0. If a signal were a look-ahead artifact it
  would survive the shuffle. This run: **placebo mean IC = −0.009** (≈0) — the
  real signal does **not** survive shuffling, which is evidence *against*
  leakage. The minimum observed embargo gap was exactly 20 sessions.

## What each model does (mathematically)

1. **`cross_sectional_composite_zscore`** *(Stage-1 deployable; no fit)* —
   a weighted sum of the per-date standardized features, with **regime-dependent
   block weights** (judgement-set, from the Phase 5-B framework), re-standardized
   to a rank. Fully transparent and reproducible; this is the model that would
   actually ship, so the gates are judged on it.
2. **`ridge_cross_sectional_return_model`** *(Stage-2; fitted)* — closed-form
   ridge regression (numpy only, intercept unpenalized, λ=10) on the pooled
   training panel predicting `forward_excess_return_20d_vs_spy`; coefficients
   applied to the test cross-section.
3. **`top_quintile_score_model`** *(Stage-2; fitted, optional)* — deterministic
   L2-regularized logistic regression (fixed iterations, numpy only) estimating
   the probability of top-quintile forward excess return; used as a rank score.

## Model results (this run; out-of-sample, 112 scored dates)

| model | mean rank IC | IC t-stat | decile spread | worst year IC | top-20 net@50bps |
|---|---|---|---|---|---|
| cross_sectional_composite_zscore *(deployable baseline)* | **+0.0021** | 0.10 | +0.0024 | −0.060 (2018) | +2.01 |
| ridge_cross_sectional_return_model | +0.0350 | 1.72 | +0.0172 | −0.036 (2022) | +4.65 |
| top_quintile_score_model | +0.0452 | 1.88 | +0.0194 | −0.042 (2022) | +6.60 |

Key reads:

- The **deployable baseline composite has essentially no edge** (IC ≈ 0.002,
  t-stat 0.10) and is *negative in risk-off* (regime IC −0.12). Its naive
  regime weighting actually hurts.
- The **fitted models show modest, more stable OOS edge** (IC ≈ 0.035–0.045,
  t-stat ~1.7–1.9, positive decile spread, and — unlike the composite —
  *positive* IC in risk-off). This is consistent with well-known
  momentum/low-vol cross-sectional effects.
- **Absolute returns are survivorship-inflated** (top-10 gross total returns of
  8×–19× over ~9 years are not credible out-of-sample for a tradable universe).
  This is exactly why the recommendation does not lean on them.

## Gate results

`gate_summary = {PASS: 12, WARNING: 3, FAIL: 1}` (judged on the deployable
baseline). Highlights:

- `no_leakage_gate` **PASS** — structural checks clean, embargo ≥ 20, placebo
  IC ≈ 0.
- `mean_rank_ic_gate` **WARNING** — baseline IC 0.002 < 0.03 threshold.
- `worst_year_gate` **FAIL** — baseline worst-year IC −0.060 (2018) < −0.02.
- `turnover_gate` **WARNING** — top-20 monthly turnover 0.60.
- `survivorship_bias_gate` **WARNING** — universe is full-history survivors.
- `transaction_cost_survival_gate`, `drawdown_gate`, `regime_robustness_gate`,
  `liquidity_gate`, `sufficient_observations_gate`, `no_fake_data_gate`, and all
  four always-on safety gates (`preview_only`, `no_orders`,
  `no_broker_execution`, `no_automation`) **PASS**.

No PASS was forced. The baseline's WARNING/FAIL edge gates are reported honestly.

## Does the evidence support deployment?

**No — not as a price-only deployable scorer.** The model that would actually
ship (the Stage-1 composite) shows no usable rank-IC edge and fails the
worst-year stability gate. The fitted models demonstrate that *some* real,
leakage-clean price-only edge exists, but it is modest and its absolute-return
profile is survivorship-inflated, so it does not justify standing up a live
ticker scorer on price data alone.

**Recommendation: `PROCEED_TO_EXTERNAL_DATA_ALPHA_ENRICHMENT`.**

## What is missing / next step

The next source of edge should come from **external data**, consistent with the
prior research conclusion that price-only signal is limited:

> **Phase 5-D: External Data Alpha Enrichment — starting with fundamentals +
> earnings + analyst estimate revisions**, then news/sentiment, options/implied
> volatility, and macro/rates. A survivorship-free (point-in-time constituent)
> universe should be assembled alongside, so absolute-return evidence becomes
> trustworthy.

Only if a future iteration shows the **shippable baseline** itself clearing the
edge and stability gates on a survivorship-free universe would the path switch to
*Phase 5-D: Implement Deployable Ticker Scorer*.

## Outputs

- `research/output/phase5c_cross_sectional_alpha_research.json` — full report.
- `phase5c_feature_panel_sample.csv` — one recent cross-section (128 names).
- `phase5c_oos_scores_sample.csv` — sampled OOS (date, ticker, model, score, label).
- `phase5c_model_scoreboard.csv`, `phase5c_ic_by_year.csv`,
  `phase5c_decile_spread.csv`, `phase5c_regime_breakdown.csv`,
  `phase5c_turnover_cost_sensitivity.csv`, `phase5c_validation_gate_matrix.csv`.

CSV outputs are summarized/sampled — no massive full panel is written.

## Reproduce (Windows PowerShell only)

```powershell
Set-Location "C:\Users\binis\Stock_Prediction_app_push"
python research\run_phase5c_cross_sectional_alpha_research.py
python -m pytest tests\test_phase5c_cross_sectional_alpha_research.py -q
```

## Safety contract

Preview/research only. `preview_only=true`; `orders_enabled`,
`automation_enabled`, `broker_execution_enabled`, `production_replacement` all
`false`. No network, no paid APIs, no deploy, no Paper Trader changes, no GCP
changes, no D: writes, no binary model artifacts, no commit.
