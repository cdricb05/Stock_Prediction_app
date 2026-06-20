# Phase 3-Z — Out-of-Sample Score Export and Signal Audit (v1)

Phase 3-Z builds a **research-only** out-of-sample (OOS) date/ticker **score panel** and a set
of signal-audit diagnostics on top of the existing local Phase 3-O / 3-P / 3-Q / 3-R outputs.
Its purpose is to make the walk-forward research model's per-name scores inspectable and to
prove (or disprove) whether those scores are actually predictive out-of-sample, before any
portfolio simulation.

This phase needs **no network**, **no ETF data**, and **no provider call**. It is research-only.

## Why reconstruction

Phase 3-P trained and evaluated the walk-forward ranking models **in memory** and persisted only
aggregate scoreboards (`research_oos_scores_computed: true`, `feature_panel_summary.written_to_disk:
false`). No exact per-row OOS score panel exists on disk. So Phase 3-Z **reconstructs** the panel
faithfully: it imports the Phase 3-P walk-forward machinery and re-runs the expanding walk-forward
with the per-horizon embargo and the same NumPy closed-form ridge ranking model — every transform
(median imputer, standardizer, lambda selection, ridge weights) fit on **training rows only** — and
captures the per-row OOS test scores. Nothing is faked: if reconstruction were impossible, the
phase emits a BLOCKED result naming the missing source and the exact next code change required
(persist per-row OOS test scores in `evaluate_ridge_model`).

## What it does NOT do

It is research-only. It **does not deploy**. It **does not run migrations**. It **does not write to production DB**. It **does not trade**.
It places no orders, implements no automation, trains no production model, creates no production
model candidate, computes no production predictions / scores / portfolio weights / order
instructions, writes no deployable model artifact, restarts no service, enables no model-v2 serving
flag, and writes nothing to the D: data drive (the local price panel is **read-only** through the
Phase 3-P loader). It calls no network: it **does not call Yahoo**. It **does not call Stooq**. It **does not call FRED**. It does not call Alpha Vantage, does not use yfinance, and calls no paid vendor API.
Forward returns are used for **validation-only** OOS IC and are **never faked** or turned into
production predictions. The universe is current-as-of, so all results remain survivorship-biased
and claim no production edge.

## Inputs (all local, read-only)

- `research/output/phase3o_multisignal_feature_factory.json` (+ dir) — feature factory contract.
- `research/output/phase3p_multisignal_walkforward_model.json` (+ `feature_weight_summary.csv`).
- `research/output/phase3q_model_robustness_diagnostics.json`.
- `research/output/phase3r_macro_regime_walkforward.json`.
- `research/output/phase3l_sec_universe_signal_gate/aligned_feature_price_panel_universe.csv`.
- `D:/Stock_Prediction_app_data/phase2k_g/output/phase2k_g_expanded_price_history_free.csv`
  (read-only, only as needed for the panel rebuild).

## Output panel schema

`oos_score_panel.csv` (one row per date × ticker × horizon × model):

    date, ticker, horizon, model_name, oos_score, cross_sectional_rank, rank_percentile, decile,
    forward_return, label_available, train_window_start, train_window_end, test_window_start,
    test_window_end, embargo_days, feature_family_count, source_status

`cross_sectional_rank` / `rank_percentile` / `decile` are computed per `(date, horizon, model)`
cross-section on `oos_score` (decile 0 = lowest score, 9 = highest). `source_status` is
`reconstructed_from_phase3p_walkforward` for every row.

## Summary outputs

- `oos_score_summary.csv` — per model/horizon: rows, tickers, dates, mean/median daily rank IC,
  IC hit-rate, top-minus-bottom decile/quintile spread, top-decile hit-rate, worst/best year IC,
  stability score.
- `decile_forward_return_summary.csv` — mean/median forward return by decile (the predictiveness
  curve).
- `yearly_score_diagnostics.csv` — IC and decile spread by test year.
- `regime_score_diagnostics.csv` — IC by risk-on / risk-off / low-vol / high-vol.
- `sector_score_diagnostics.csv` — IC and decile spread by sector.
- `feature_family_contribution_summary.csv` — per-family standardized-weight contribution share
  (read from the Phase 3-P weight summary; no retrain).
- `leakage_and_embargo_checks.csv` — explicit per-fold embargo / leakage checks (each decisionable).
- `readiness_decision_table.csv` — pass/fail rows behind the recommendation.

## Git-safe panel size

The panel is the only large output. Each model/horizon block is ~190k–203k rows (~31–40 MB), and
the hard limit is **50 MB per file**, so the panel exports as many candidate blocks (priority
order) as fit under a ~46 MB budget; any block that does not fit is **logged** in
`omitted_model_horizons` (never silently dropped) and remains reproducible by re-running with a
different candidate list. Diagnostics are always computed from the **exported** panel, so the panel
and summaries agree.

## Decision rule

| Condition | Recommendation |
| --- | --- |
| required inputs missing / corrupt | `OOS_SCORE_EXPORT_BLOCKED_INPUTS` |
| no panel found and none reconstructed (0 rows) | `OOS_SCORE_EXPORT_BLOCKED_NO_SCORE_SOURCE` |
| panel >= 100,000 rows, >= 50 tickers, >= 500 dates, no failed leakage check, and >= 1 model/horizon with positive mean daily rank IC and positive top-minus-bottom decile spread | `OOS_SCORE_EXPORT_READY_FOR_PORTFOLIO_SIMULATION` |
| panel exists but fails any readiness threshold | `OOS_SCORE_EXPORT_PARTIAL_NEEDS_REPAIR` |

The recommended next phase is always **4-A**: *Turnover-Aware Portfolio Simulation and Risk Budget*
when READY, otherwise a repair/extend variant of 4-A.

## Guarantees

The phase performs no network call and reads no market-data vendor. It trains no production model,
creates no production candidate, writes no deployable model artifact, computes no production
predictions / scores / portfolio weights / order instructions, touches no database, runs no
migration, deploys nothing, and writes nothing to D:. Score rows are reconstructed from the Phase
3-P walk-forward and are **never faked**. Every output file is Git-safe (well under 50 MB). This
phase is **research-only** and claims no production edge.
