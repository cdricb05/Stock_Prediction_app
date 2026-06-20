# Phase 4-B — Non-Production Model Candidate Package (v1)

Phase 4-B turns the Phase 3-Z signal audit and the Phase 4-A turnover-aware portfolio
simulation into a single **governance-style, non-production candidate package**: the
selected research model + portfolio strategy, the evidence behind it, the mandatory
risk guardrails, the known failure modes, a Paper Trader preview-integration contract,
explicit no-go items, and a readiness decision.

This is **not** deployment, **not** production model training, **not** live prediction,
and **not** live portfolio construction. It only **packages** evidence that already
exists in the Phase 3-Z / 4-A outputs — every metric is copied verbatim, nothing is
recomputed or faked.

## What it does NOT do

It is research-only. It **does not deploy**. It **does not run migrations**. It **does
not write to a production database**. It **does not trade**. It places no orders,
implements no automation, trains no production model, creates no production model
candidate, writes no deployable model artifact, computes no production predictions /
scores, and computes no live **or** research portfolio weights. It **does not touch
Paper Trader**. It reads nothing from the D: data drive and calls no network: it **does
not call Alpha Vantage**, **does not call Yahoo**, **does not call Stooq**, **does not
call FRED**, does not use yfinance, and calls no paid vendor API. No metric is faked.

## Inputs (all local, read-only)

Phase 3-Z: `phase3z_oos_score_export_signal_audit.json`, `oos_score_summary.csv`,
`yearly_score_diagnostics.csv`, `regime_score_diagnostics.csv`,
`leakage_and_embargo_checks.csv`.

Phase 4-A: `phase4a_turnover_aware_portfolio_simulation.json`,
`strategy_scoreboard.csv`, `turnover_cost_sensitivity.csv`, `drawdown_summary.csv`,
`risk_budget_summary.csv`, `readiness_decision_table.csv`.

## Selected non-production candidate

| Item | Value |
| --- | --- |
| model_name | `ridge_combined_regime_interactions` |
| horizon | `126d` |
| strategy_name | `top_10_equal_weight` |
| transaction_cost_gate_bps | 25 |
| rebalance_frequency | monthly |
| holdings_target | 10 |
| max_single_name_weight | 10% |
| long_only | true |
| leverage_allowed | false |
| orders_allowed | false |
| automation_allowed | false |
| paper_trader_preview_only | true |

## Sharpe caveat (mandatory)

Phase 4-A uses 126-day forward excess returns sampled monthly, so the windows
**overlap**. Sharpe / Sortino are useful for **relative** strategy comparison but are
**optimistic** as tradable ratios. This caveat is carried into the package as a
mandatory guardrail and a known failure mode, and must be honored in any preview
integration.

## Outputs

- `candidate_summary_card.csv` — id, name, model, horizon, strategy, status,
  recommendation, next phase, top reasons, top risks, mandatory guardrails.
- `model_candidate_spec.csv` — the selected research model configuration.
- `selected_strategy_spec.csv` — the selected portfolio strategy configuration
  (orders/automation disabled, preview-only).
- `evidence_scorecard.csv` — OOS rows/tickers/dates, mean rank IC, decile spread,
  leakage failures, 25 bps annualized return / Sharpe / max drawdown, average turnover /
  holdings, cost-survival status, number of passing strategies at 25 bps.
- `risk_guardrails.csv` — the 12 mandatory guardrails.
- `known_failure_modes.csv` — 2024 drawdown, 50 bps cost failure, overlapping-label
  optimism, survivorship bias, incomplete global/earnings data, no live proof, no
  production artifact.
- `preview_integration_contract.csv` — what Paper Trader **may** show vs **must not** do.
- `no_go_items.csv` — explicit forbidden actions.
- `readiness_decision_table.csv` — the pass/fail rows behind the recommendation.

## Decision rule

READY (`NONPROD_CANDIDATE_READY_FOR_PREVIEW_INTEGRATION`) requires **all** of:
Phase 3-Z = `OOS_SCORE_EXPORT_READY_FOR_PORTFOLIO_SIMULATION`; Phase 4-A =
`PORTFOLIO_SIMULATION_READY_FOR_NONPRODUCTION_CANDIDATE`; selected strategy passes the
25 bps gate; no leakage failures; max drawdown better than −35%; average holdings ≥ 10;
average turnover ≤ 1.5; all required guardrails present.

| Condition | Recommendation |
| --- | --- |
| required input files missing | `NONPROD_CANDIDATE_BLOCKED_INPUTS` |
| selected strategy fails 25 bps / drawdown / turnover / holdings | `NONPROD_CANDIDATE_BLOCKED_RISK` |
| all gates pass | `NONPROD_CANDIDATE_READY_FOR_PREVIEW_INTEGRATION` |
| risk gates pass but evidence incomplete | `NONPROD_CANDIDATE_PARTIAL_NEEDS_RISK_REPAIR` |

The recommended next phase is always **4-C** (Paper Trader Preview Integration Plan when
READY, otherwise a repair variant).

## Guarantees

No network call, no market-data vendor, no D: access, no database, no migration, no
deployment, no model-v2 flag, no production model / candidate / artifact, no production
predictions / scores, no live or research portfolio weights, no orders, no trades, no
automation, no Paper Trader modification. Every metric is copied from the Phase 3-Z /
4-A outputs and is **never faked**. Every output file is Git-safe (well under 50 MB).
This phase is **research-only** and claims no production edge.
