# Phase 4-A — Turnover-Aware Portfolio Simulation and Risk Budget (v1)

Phase 4-A consumes the Phase 3-Z out-of-sample (OOS) score panel and runs a
**research-only** long-only portfolio simulation. The question it answers: does the
Phase 3-P/3-Z ranking signal *survive realistic portfolio construction* — monthly
rebalancing, turnover, transaction costs, single-name position limits, drawdowns, and
year-to-year stability — rather than only showing up as a thin daily rank IC?

This phase needs **no network** and **no D: drive**. It reads only the four local
Phase 3-Z artifacts.

## What it does NOT do

It is research-only. It **does not deploy**. It **does not run migrations**. It **does not write to a production database**. It **does not trade**.
It places no orders, implements no automation, trains no production model, creates no
production model candidate, writes no deployable model artifact, computes no production
predictions / scores, and computes no **live** portfolio weights or order instructions.
The only weights it computes are **research** portfolio weights used to measure the
signal. It reads nothing from the D: data drive and calls no network: it **does not call
Alpha Vantage**, **does not call Yahoo**, **does not call Stooq**, **does not call FRED**,
does not use yfinance, and calls no paid vendor API. Forward returns are validation-only
research labels and are **never faked**.

## Inputs (all local, read-only)

- `research/output/phase3z_oos_score_export_signal_audit.json` — identifies the best
  audited model/horizon (`ridge_combined_regime_interactions @ 126d`).
- `research/output/phase3z_oos_score_export_signal_audit/oos_score_panel.csv` — the
  per-date/ticker OOS score panel (the only data source for the simulation).
- `.../oos_score_summary.csv`, `.../decile_forward_return_summary.csv`,
  `.../leakage_and_embargo_checks.csv` — context / provenance.

Only rows with `label_available == 1`, the best model, and the `126d` horizon are used.
`forward_return` (126-trading-day forward **excess** return vs SPY) is the research
label for each held name.

## Simulation design

- **Rebalance:** first available trading date of each calendar month.
- **Ranking:** by `oos_score` (higher = better) within each rebalance cross-section.
- **Strategies (long-only):**
  `top_10_equal_weight`, `top_20_equal_weight`, `top_decile_equal_weight`,
  `top_decile_score_weighted_capped`.
- **Transaction costs:** 0, 10, 25, 50 bps, charged on traded notional
  (`cost_drag = bps/1e4 × turnover`, where `turnover = Σ|w_new − w_prev|`, two-sided).
- **Risk constraints:** max single-name weight **10%**, no leverage (gross ≤ 100%),
  long-only (weights ≥ 0), cash held when fewer than the required names exist.

## Annualization convention (read this before quoting Sharpe)

Each per-rebalance return is the weighted mean of holdings' `forward_return`, a
**126-trading-day** forward excess return sampled at **monthly** cadence — so the
observations **overlap** (≈6 consecutive months share overlapping windows). This is an
honest research proxy, not a tradable backtest. We deliberately do **not** smooth the
overlap away with a power transform (that artificially compresses drawdowns). Instead:

- **Return / volatility / Sharpe / Sortino** use arithmetic horizon annualization on the
  raw per-rebalance net returns: `ann_return = mean(net) × (252/126)` and
  `ann_vol = std(net) × sqrt(252/126)`. Because the monthly samples overlap they are
  autocorrelated, so **Sharpe and Sortino are optimistic** and should be read as
  *relative comparisons across strategies and cost levels*, not as tradable
  risk-adjusted ratios.
- **Max drawdown** is taken from the literal compounded monthly path
  (`equity = cumprod(1+net)`) — the same series stored in `monthly_portfolio_returns.csv`.
- **Annual return** is the annualized rate of each calendar year's mean net return.

Results remain **survivorship-biased** (current-as-of universe) and claim no production edge.

## Outputs

- `strategy_scoreboard.csv` — one row per (strategy, cost): periods, annualized
  return/vol, Sharpe, Sortino, max drawdown, monthly hit rate, average turnover/holdings,
  worst month, worst/best year, total cost drag, `GATE_PASS`/`GATE_FAIL`.
- `monthly_portfolio_returns.csv` — the transparent per-rebalance ledger (gross, turnover,
  cost drag, net, holdings).
- `annual_performance.csv` — per year × strategy × cost.
- `turnover_cost_sensitivity.csv` — how each strategy degrades across the cost grid and
  whether it `survives_costs`.
- `drawdown_summary.csv` — max drawdown with start / trough / recovery / duration.
- `position_concentration_summary.csv` — holdings and concentration per strategy.
- `risk_budget_summary.csv` — empirically-checked position-cap / leverage / long-only
  guarantees.
- `readiness_decision_table.csv` — the pass/fail rows behind the recommendation.

## Decision rule

READY requires **at least one strategy at 25 bps** with: positive annualized return,
positive Sharpe, max drawdown better than −35%, average holdings ≥ 10, average turnover
≤ 1.5 per rebalance, and ≥ 36 monthly periods.

| Condition | Recommendation |
| --- | --- |
| Phase 3-Z inputs missing / invalid | `PORTFOLIO_SIMULATION_BLOCKED_INPUTS` |
| no valid return series can be built | `PORTFOLIO_SIMULATION_BLOCKED_NO_VALID_STRATEGY` |
| ≥ 1 strategy clears all thresholds at 25 bps | `PORTFOLIO_SIMULATION_READY_FOR_NONPRODUCTION_CANDIDATE` |
| strategies exist but none passes | `PORTFOLIO_SIMULATION_PARTIAL_NEEDS_RISK_REPAIR` |

The recommended next phase is always **4-B** (Non-Production Model Candidate Package when
READY, otherwise a repair variant).

## Guarantees

No network call, no market-data vendor, no D: access, no database, no migration, no
deployment, no model-v2 flag, no production model / candidate / artifact, no production
predictions / scores, no **live** portfolio weights, no orders, no trades, no automation.
Returns are computed from Phase 3-Z labels and are **never faked**. Every output file is
Git-safe (well under 50 MB). This phase is **research-only** and claims no production edge.
