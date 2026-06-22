# Phase 5-F0 — Professional Alpha Model V3 Research Harness (v1)

## Why this phase exists

The strategic decision is to stop random data-provider experiments and build the
**real core trading brain**. The evidence so far:

- **Phase 5-C** (price-only cross-sectional alpha) found a weak/modest edge.
- **Phase 5-E2** (SimFin fundamentals) found **no incremental edge**; delayed
  fundamentals are not useful for a 5–20 day stock-ranking target.

Phase 5-F0 answers **one** question with out-of-sample evidence:

> Can a price / volume / regime **Alpha V3** model produce robust enough evidence
> to justify building a deployable daily-swing stock-ranking scorer (Phase 5-F1)?

It is **Track A quant research**. It trains nothing deployable, deploys nothing,
touches no Paper Trader / GCP code, writes nothing to `D:`, places no orders,
installs no packages, and persists no binary model artifact. It makes **no network
calls** and needs **no API key** — it reads only the local read-only price history.

## Target trading system (context)

Liquid U.S. equities (ETFs later); daily swing / cross-sectional ranking at 5, 10,
20-day horizons; **primary target = 20-day forward excess return vs SPY**. SPY is
the benchmark / market-regime proxy, never a ranked tradable name. Initial style is
long-only / long-flat, no leverage, no options/futures/FX. The goal is a model that
ranks stocks, then a risk engine that converts rankings into controlled positions.

## Inputs (all local / read-only)

| Input | Path | Use |
|-------|------|-----|
| Price history | `D:\…\phase2k_g_expanded_price_history_free.csv` | read-only; price/volume panel + labels |
| Phase 5-C harness | `research/run_phase5c_cross_sectional_alpha_research.py` | imported and reused (alignment, feature math, ridge, rank-IC, regime proxy, 5-C rerun) |
| Phase 5-C artifacts | `research/output/phase5c_*.{json,csv}` | committed baseline for comparison |

The Phase 5-C runner is **imported, not duplicated**, and rerun in-process so the
`price_only_phase5c_reference` is produced under the exact same data and calendar
the V3 models run through.

## Feature families (37 features, point-in-time, sign-adjusted)

Every feature uses only data at or before the as-of session. The sign convention
mirrors Phase 5-C: after the per-feature sign, **higher == more bullish**.

| Family | Features |
|--------|----------|
| momentum | return 5/10/20/63/126/252d, mom_acceleration, trend_persistence |
| reversal / overextension | reversal_1d, reversal_5d, dist_sma20, dist_sma50, return_zscore_5d, return_zscore_20d |
| relative strength | rs_vs_spy 5/20/63/126d, residual_alpha 20/63d |
| volatility / risk | realized_vol 20/63d, downside_vol_63d, vol_compression, vol_adj_momentum, beta_63d |
| volume / liquidity | log_dollar_volume_20d, volume_shock, price_volume_breakout |
| drawdown / recovery | dist_from_63d_high, dist_from_126d_high, max_drawdown_126d, recovery_from_low_63d, breakout_after_consolidation |
| regime interactions | mom_x_riskon, rev_x_highvol, volshock_x_breakout |

**Sector-relative strength is marked `unavailable`** — the local price-history CSV
has no reliable sector/industry map, so it is documented as unavailable, not faked.

## Labels (forward-looking only)

`forward_excess_return_5d/10d/20d_vs_spy` (primary = 20d), raw forward returns
5/10/20d, top/bottom quintile labels on the primary target, and a downside-risk
label (`forward_return_20d < -10%`). No label appears in the feature set.

## Models (one identical walk-forward harness)

1. `price_only_phase5c_reference` — the imported Phase 5-C harness, rerun in-process
   (best 5-C model by IC); the **baseline to beat**, not a deployment candidate.
2. `robust_price_volume_composite` — transparent fixed-weight z-score composite of
   the prior-weighted features; **no fit**.
3. `regularized_linear_rank_model` — ridge regression, walk-forward only.
4. `nonlinear_tree_model` — **sklearn HistGradientBoosting/RandomForest if present**;
   sklearn is **not installed in this environment**, so a documented numpy
   **gradient-boosted decision-stumps** fallback is used (deterministic, O(n)
   cumulative-sum split search). The model is still evaluated and clearly marked as
   the fallback (`sklearn_available=false`).
5. `ensemble_rank_model` — averages the **within-date cross-sectional ranks** of the
   non-leaky OOS component scores (composite + ridge + tree). Fully out-of-sample;
   no refit, no train/test contamination.

All fitted models use yearly walk-forward folds, train-on-past / test-on-future,
with a ≥20-session embargo/purge around the 20-day label horizon.

## Validation

Rank IC by date, mean / median rank IC, IC t-stat, IC by year, worst-year IC, IC by
regime; top-bottom decile spread; long-only **top-10 / top-20** portfolio simulation
in **equal-weight and volatility-adjusted (1/vol)** variants, monthly rebalance, with
**25 and 50 bps** transaction-cost sensitivity, turnover, and max drawdown; a
liquidity gate; a **placebo label-shuffle** leakage probe; forward-label / lookahead
structural checks; an explicit **survivorship-bias** caveat; and a direct comparison
versus Phase 5-C.

## Run it

```powershell
python research\run_phase5f0_alpha_model_v3_research.py
# optional: --price-csv <path>  --max-tickers N
```

No key, no network. Reads `D:` price history (read-only).

## Deployment gate (recommend 5-F1 only if ALL hold)

mean rank IC ≥ 0.03 · IC t-stat ≥ 2.0 · positive decile spread after costs · worst-
year IC > −0.02 · no leakage/placebo failure · acceptable turnover · acceptable
drawdown · **materially improves over Phase 5-C** (best V3 IC − 5-C IC ≥ 0.01). The
recommendation is driven by the actual gate outcomes, never forced.

## Recommendations (the five allowed values)

`READY_FOR_PHASE5F1_DEPLOYABLE_SCORER` · `EDGE_PRESENT_BUT_NOT_DEPLOYABLE` ·
`NO_ROBUST_EDGE` · `DATA_BLOCKER` · `ERROR`.

## Artifacts (committed-safe, under `research/output/phase5f0_alpha_model_v3_research/`)

`phase5f0_alpha_model_v3_research.json` · `alpha_v3_feature_catalog.csv` ·
`alpha_v3_panel_sample.csv` · `alpha_v3_model_scoreboard.csv` ·
`alpha_v3_ic_by_year.csv` · `alpha_v3_decile_spread.csv` ·
`alpha_v3_turnover_cost_report.csv` · `alpha_v3_portfolio_simulation.csv` ·
`alpha_v3_regime_breakdown.csv` · `alpha_v3_validation_gate_matrix.csv` ·
`phase5f1_deployable_scorer_plan.json` (gated on the recommendation).

## Result (v1 live run — 128-name universe, 112 OOS dates)

| Model | mean rank IC | IC t-stat | decile spread |
|-------|-------------:|----------:|--------------:|
| price_only_phase5c_reference | **0.0452** | 1.88 | 0.0194 |
| robust_price_volume_composite | −0.0029 | −0.15 | 0.0016 |
| regularized_linear_rank_model | 0.0381 | 1.95 | 0.0153 |
| nonlinear_tree_model (numpy fallback) | 0.0394 | 2.06 | 0.0144 |
| **ensemble_rank_model** (best V3) | **0.0425** | **2.26** | 0.0152 |

The best V3 model (the ensemble) shows a **genuine, leakage-clean out-of-sample
edge**: mean rank IC 0.0425 with t-stat 2.26, positive decile spread that survives
50 bps costs, positive IC in all three regimes, and a placebo IC ≈ 0.006 (collapses
under label shuffle). **But it does not beat the Phase 5-C price-only baseline**: the
5-C reference IC is 0.0452, so Δ(best V3 − 5-C) = **−0.0028** and the
`materially_beats_phase5c_gate` **FAILs**. Richer features + a nonlinear / ensemble
stack did not materially improve cross-sectional ranking over plain Phase 5-C price
features.

Gate summary: **PASS 15, FAIL 1, WARNING 4, NOT_EVALUABLE 0.** The single FAIL is the
material-improvement gate (correct and honest). WARNINGs: worst-year IC (2022 =
−0.029), top-20 turnover (0.52, just over the 0.50 floor), the survivorship-bias
caveat, and the sklearn-fallback flag.

**Recommendation: `EDGE_PRESENT_BUT_NOT_DEPLOYABLE`.** A real price/volume/regime
edge exists, but it neither clears the full deployment bar nor improves on the
existing Phase 5-C baseline, so a deployable scorer is **not** justified on this
evidence. `phase5f1_deployable_scorer_plan.json` is gated off (`proceed_to_5f1:
false`). Note the top-20 net-of-cost total return (~489%) is **survivorship-
inflated** — the local universe is full-history survivors; rank IC is the more
trustworthy metric and it does not beat 5-C.

## Safety contract

`preview_only=true`; `orders_enabled`, `automation_enabled`,
`broker_execution_enabled`, `production_replacement`, `network_used`,
`paid_apis_used`, `deployed`, `binary_artifacts_created`, `writes_to_d_drive`,
`modifies_paper_trader`, `modifies_gcp`, `uses_external_paid_provider`,
`provider_work`, `packages_installed` all `false`. Models are fit in memory for
evaluation only — nothing is persisted. No SimFin, no FMP, no provider shopping, no
live API calls, no package installs, no commit, no push.
