# Phase 4-C — Paper Trader Preview Integration Plan (v1)

Phase 4-C turns the Phase 4-B non-production candidate package into a detailed,
auditable, **implementation-ready plan** for showing that candidate inside Paper
Trader as **PREVIEW ONLY**. It defines the future preview API contract, the future UI
layout, the safety-badge matrix, the data-dependency contract, the implementation
sequence, the validation test plan, and the no-go enforcement list — and records a
readiness decision.

This is a **planning phase only**. It changes **no** Paper Trader code. Nothing is
deployed, no service is restarted, no migration is run, `PREDICTOR_USE_MODEL_V2` is
not enabled, no database is written, no order/trade is created, no automation is
implemented, no production model / candidate / artifact is produced, no production
predictions / scores are computed, and no live **or** research portfolio weights are
computed. It touches no `C:\Users\binis\paper_trader` file, reads nothing from the D:
data drive, and calls no network (no Alpha Vantage / Yahoo / Stooq / FRED / yfinance /
paid vendor API). Every candidate value is copied verbatim from the Phase 4-B package;
no metric is faked.

## Inputs (all local, read-only)

The Phase 4-B package only: `phase4b_nonproduction_candidate_package.json` plus its
nine CSVs (`candidate_summary_card`, `model_candidate_spec`, `selected_strategy_spec`,
`evidence_scorecard`, `risk_guardrails`, `known_failure_modes`,
`preview_integration_contract`, `no_go_items`, `readiness_decision_table`).

## Selected candidate (from Phase 4-B, verbatim)

| Item | Value |
| --- | --- |
| candidate_id | `NPC-RIDGE-CRI-126D-TOP10EW-25BPS` |
| model_name | `ridge_combined_regime_interactions` |
| horizon | `126d` |
| strategy_name | `top_10_equal_weight` |
| Phase 4-B recommendation | `NONPROD_CANDIDATE_READY_FOR_PREVIEW_INTEGRATION` |

## 1. Preview API contract (defined, not implemented)

A future read-only endpoint **`GET /v1/research/candidate-preview`**. It must be
read-only, must not write the database, must not create signals, must not create trade
decisions, and must not create orders. Required response fields (15): `candidate_id`,
`candidate_name`, `model_name`, `horizon`, `strategy_name`, `status`,
`recommendation`, `evidence_summary`, `selected_strategy_summary`, `risk_guardrails`,
`known_failure_modes`, `no_go_items`, `safety_badges`, `generated_at`, `source_files`.

## 2. Preview UI contract (defined, not implemented)

Seven sections: **Candidate Summary card**, **Evidence card**, **Strategy card**,
**Risk Guardrails card**, **Known Failure Modes card**, **No-Go Items card**, and an
always-visible **Preview-only safety banner**. The banner must clearly show: PREVIEW
ONLY, NO ORDERS, NO AUTOMATION, NO LIVE WEIGHTS, NON-PRODUCTION CANDIDATE, MANUAL
REVIEW REQUIRED.

## 3. Safety badge matrix (11)

PREVIEW ONLY · NON-PRODUCTION CANDIDATE · RESEARCH ONLY · NO ORDERS · NO BROKER
EXECUTION · NO AUTOMATION · NO LIVE PORTFOLIO WEIGHTS · MANUAL REVIEW REQUIRED ·
OVERLAPPING LABEL WARNING · SURVIVORSHIP BIAS WARNING · 2024 DRAWDOWN WARNING.

## 4. Data dependency contract

The future preview may read **only** the Phase 4-B package JSON and its CSVs. It must
not read raw Alpha Vantage / provider price outputs, must not call providers, must not
train models, must not use D: data, and must not recompute research scores in the app.

## 5. Implementation sequence (future, gated)

| Phase | Title | Gated by |
| --- | --- | --- |
| 4-D | Paper Trader Read-Only Candidate Preview Service | Phase 4-C READY |
| 4-E | Preview-Only API Endpoint | 4-D complete |
| 4-F | Preview UI Panel + Safety Badges | 4-E complete |
| 4-G | Tests + Smoke Checks | 4-F complete |
| LATER | Candidate refresh / shadow trading / production model candidate | explicit future approval |

## 6. Validation test plan (future Paper Trader tests)

Endpoint returns the candidate package; endpoint is read-only; endpoint writes no
database rows; endpoint creates no orders; endpoint creates no trade decisions; UI
shows all safety badges; UI shows risk warnings; auth / API-key rules are preserved;
existing Paper Trader workflows still pass.

## 7. No-go enforcement (11)

The future preview integration must not: create orders; enable automation; enable
production model v2; create live portfolio weights; write to `trade_decisions`; write
to `orders`; write to broker/execution tables; call network providers; modify the
prediction service; modify the GCP service; deploy anything.

## Decision rule

`PREVIEW_INTEGRATION_PLAN_READY` requires **all** of: Phase 4-B recommendation is
`NONPROD_CANDIDATE_READY_FOR_PREVIEW_INTEGRATION`; all 15 API fields present; all 7 UI
sections present; all 11 safety badges present; all 11 no-go items present;
implementation sequence present; validation test plan present; Paper Trader files
modified == false.

| Condition | Recommendation |
| --- | --- |
| Phase 4-B package missing / invalid / not READY | `PREVIEW_INTEGRATION_PLAN_BLOCKED_INPUTS` |
| any safety badge / no-go item missing | `PREVIEW_INTEGRATION_PLAN_BLOCKED_SAFETY` |
| plan exists but one non-critical contract item missing | `PREVIEW_INTEGRATION_PLAN_PARTIAL` |
| all gates pass | `PREVIEW_INTEGRATION_PLAN_READY` |

The recommended next phase is always **4-D** (the read-only preview service when
READY, otherwise a repair variant).

## Outputs

`phase4c_paper_trader_preview_integration_plan.json` plus eight CSVs:
`preview_api_contract.csv`, `preview_ui_contract.csv`, `safety_badge_matrix.csv`,
`data_dependency_contract.csv`, `implementation_sequence.csv`,
`validation_test_plan.csv`, `no_go_enforcement.csv`, `readiness_decision_table.csv`.

## Guarantees

No network call, no market-data vendor, no D: access, no database, no migration, no
deployment, no model-v2 flag, no production model / candidate / artifact, no production
predictions / scores, no live or research portfolio weights, no orders, no trades, no
automation, **no Paper Trader code modification**. Every candidate value is copied from
the Phase 4-B package and is **never faked**. Every output file is Git-safe (well under
50 MB). This phase is **planning-only** and claims no production edge.
