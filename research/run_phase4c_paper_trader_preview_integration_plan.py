"""Phase 4-C - Paper Trader Preview Integration Plan (research/planning-only).

This phase reads the Phase 4-B non-production candidate package and produces a
detailed, auditable, implementation-ready PLAN for integrating that candidate into
Paper Trader as PREVIEW ONLY. It defines the future preview API contract, the future
UI layout, the safety badge matrix, the data dependency contract, the implementation
sequence, the validation test plan, and the no-go enforcement list - and it records a
readiness decision.

This phase is PLANNING ONLY. It does NOT change Paper Trader code. It does NOT
implement the endpoint, the service layer, or the UI. It does NOT deploy, run
migrations, restart services, enable PREDICTOR_USE_MODEL_V2, write to any database,
trade, create orders, implement automation, train a production model, create a
production model candidate, write a deployable artifact, compute production
predictions / scores, or compute live or research portfolio weights. It touches no
Paper Trader file. It calls no network: it does not call Alpha Vantage, Yahoo, Stooq,
FRED, yfinance, or any paid vendor API. It reads nothing from the D: data drive and
writes nothing to D:. Every value about the candidate is copied verbatim from the
Phase 4-B package; no metric is faked.

Inputs (all local, read-only):
  - research/output/phase4b_nonproduction_candidate_package.json
  - research/output/phase4b_nonproduction_candidate_package/candidate_summary_card.csv
  - research/output/phase4b_nonproduction_candidate_package/model_candidate_spec.csv
  - research/output/phase4b_nonproduction_candidate_package/selected_strategy_spec.csv
  - research/output/phase4b_nonproduction_candidate_package/evidence_scorecard.csv
  - research/output/phase4b_nonproduction_candidate_package/risk_guardrails.csv
  - research/output/phase4b_nonproduction_candidate_package/known_failure_modes.csv
  - research/output/phase4b_nonproduction_candidate_package/preview_integration_contract.csv
  - research/output/phase4b_nonproduction_candidate_package/no_go_items.csv
  - research/output/phase4b_nonproduction_candidate_package/readiness_decision_table.csv
"""

from __future__ import annotations

import json
import os
from datetime import datetime

import pandas as pd

PHASE = "4-C"

# ---------------------------------------------------------------------------
# Paths (relative to the repo root; read-only inputs, local outputs only).
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUT_BASE = os.path.join(_REPO_ROOT, "research", "output")

_B_DIR = os.path.join(_OUT_BASE, "phase4b_nonproduction_candidate_package")
PHASE4B_RESULT_JSON = os.path.join(_OUT_BASE, "phase4b_nonproduction_candidate_package.json")
PHASE4B_CANDIDATE_CARD_CSV = os.path.join(_B_DIR, "candidate_summary_card.csv")
PHASE4B_MODEL_SPEC_CSV = os.path.join(_B_DIR, "model_candidate_spec.csv")
PHASE4B_STRATEGY_SPEC_CSV = os.path.join(_B_DIR, "selected_strategy_spec.csv")
PHASE4B_EVIDENCE_CSV = os.path.join(_B_DIR, "evidence_scorecard.csv")
PHASE4B_GUARDRAILS_CSV = os.path.join(_B_DIR, "risk_guardrails.csv")
PHASE4B_FAILURE_CSV = os.path.join(_B_DIR, "known_failure_modes.csv")
PHASE4B_PREVIEW_CONTRACT_CSV = os.path.join(_B_DIR, "preview_integration_contract.csv")
PHASE4B_NO_GO_CSV = os.path.join(_B_DIR, "no_go_items.csv")
PHASE4B_READINESS_CSV = os.path.join(_B_DIR, "readiness_decision_table.csv")

_C_DIR = os.path.join(_OUT_BASE, "phase4c_paper_trader_preview_integration_plan")
RESULT_JSON = os.path.join(_OUT_BASE, "phase4c_paper_trader_preview_integration_plan.json")

_OUT_PATHS = {
    "preview_api_contract": os.path.join(_C_DIR, "preview_api_contract.csv"),
    "preview_ui_contract": os.path.join(_C_DIR, "preview_ui_contract.csv"),
    "safety_badge_matrix": os.path.join(_C_DIR, "safety_badge_matrix.csv"),
    "data_dependency_contract": os.path.join(_C_DIR, "data_dependency_contract.csv"),
    "implementation_sequence": os.path.join(_C_DIR, "implementation_sequence.csv"),
    "validation_test_plan": os.path.join(_C_DIR, "validation_test_plan.csv"),
    "no_go_enforcement": os.path.join(_C_DIR, "no_go_enforcement.csv"),
    "readiness_decision_table": os.path.join(_C_DIR, "readiness_decision_table.csv"),
}

# Upstream Phase 4-B recommendation that READY gates on.
EXPECTED_4B_RECOMMENDATION = "NONPROD_CANDIDATE_READY_FOR_PREVIEW_INTEGRATION"

# Future preview-only endpoint (DEFINED here; NOT implemented in this phase).
PREVIEW_ENDPOINT = "GET /v1/research/candidate-preview"
PREVIEW_STATUS = "RESEARCH_NONPRODUCTION_CANDIDATE_PREVIEW"

# Decision values.
DEC_READY = "PREVIEW_INTEGRATION_PLAN_READY"
DEC_PARTIAL = "PREVIEW_INTEGRATION_PLAN_PARTIAL"
DEC_BLOCKED_INPUTS = "PREVIEW_INTEGRATION_PLAN_BLOCKED_INPUTS"
DEC_BLOCKED_SAFETY = "PREVIEW_INTEGRATION_PLAN_BLOCKED_SAFETY"
ALLOWED_RECOMMENDATIONS = (
    DEC_READY,
    DEC_PARTIAL,
    DEC_BLOCKED_INPUTS,
    DEC_BLOCKED_SAFETY,
)

# ---------------------------------------------------------------------------
# 1. Preview API contract - the 15 required read-only response fields.
# ---------------------------------------------------------------------------
API_RESPONSE_FIELDS = [
    ("candidate_id", "Phase 4-B candidate_id"),
    ("candidate_name", "Phase 4-B candidate_name"),
    ("model_name", "selected research model name"),
    ("horizon", "forward research horizon"),
    ("strategy_name", "selected portfolio strategy"),
    ("status", "RESEARCH_NONPRODUCTION_CANDIDATE_PREVIEW"),
    ("recommendation", "Phase 4-B recommendation string"),
    ("evidence_summary", "OOS evidence copied verbatim from Phase 4-B"),
    ("selected_strategy_summary", "selected strategy spec copied from Phase 4-B"),
    ("risk_guardrails", "mandatory guardrail list"),
    ("known_failure_modes", "known failure mode list"),
    ("no_go_items", "explicit forbidden actions"),
    ("safety_badges", "safety badge list shown in the UI"),
    ("generated_at", "package generation timestamp"),
    ("source_files", "Phase 4-B source artifact paths"),
]
# Read-only constraints the future endpoint MUST satisfy.
API_CONSTRAINTS = [
    ("read_only", "True", "the endpoint must be read-only (GET)"),
    ("database_write", "False", "must not write to the database"),
    ("creates_signals", "False", "must not create signals"),
    ("creates_trade_decisions", "False", "must not create trade decisions"),
    ("creates_orders", "False", "must not create orders"),
]

# ---------------------------------------------------------------------------
# 2. Preview UI contract - 7 required sections + always-visible banner badges.
# ---------------------------------------------------------------------------
UI_SECTIONS = [
    ("Candidate Summary card", "card",
     "id, name, model, horizon, strategy, status, recommendation"),
    ("Evidence card", "card",
     "OOS rows/tickers/dates, rank IC, decile spread, 25 bps return/Sharpe/maxDD"),
    ("Strategy card", "card",
     "top_10_equal_weight, monthly rebalance, 10 holdings, 10% cap, orders disabled"),
    ("Risk Guardrails card", "card", "the 12 mandatory guardrails"),
    ("Known Failure Modes card", "card",
     "2024 drawdown, 50 bps cost failure, overlapping labels, survivorship bias"),
    ("No-Go Items card", "card", "explicit forbidden actions"),
    ("Preview-only safety banner", "banner",
     "always-visible preview-only safety banner across the panel"),
]
# Banner badges the UI must clearly show (req #2).
UI_BANNER_BADGES = [
    "PREVIEW ONLY",
    "NO ORDERS",
    "NO AUTOMATION",
    "NO LIVE WEIGHTS",
    "NON-PRODUCTION CANDIDATE",
    "MANUAL REVIEW REQUIRED",
]

# ---------------------------------------------------------------------------
# 3. Safety badge matrix - the 11 required badges.
# ---------------------------------------------------------------------------
SAFETY_BADGES = [
    ("PREVIEW ONLY", "banner", "nothing is executed; preview only"),
    ("NON-PRODUCTION CANDIDATE", "banner", "research candidate, not a production model"),
    ("RESEARCH ONLY", "banner", "research evidence only; no production edge claimed"),
    ("NO ORDERS", "banner", "no order instructions or orders are ever created"),
    ("NO BROKER EXECUTION", "banner", "nothing is routed to a broker"),
    ("NO AUTOMATION", "banner", "no automated action; every step is manual"),
    ("NO LIVE PORTFOLIO WEIGHTS", "banner", "no live portfolio weights are computed"),
    ("MANUAL REVIEW REQUIRED", "banner", "a human must review before any action"),
    ("OVERLAPPING LABEL WARNING", "risk_card",
     "126d labels overlap; Sharpe/Sortino optimistic, not tradable"),
    ("SURVIVORSHIP BIAS WARNING", "risk_card", "current-as-of universe; results inflated"),
    ("2024 DRAWDOWN WARNING", "risk_card", "severe 2024 drawdown (-33.8% at 25 bps)"),
]

# ---------------------------------------------------------------------------
# 4. Data dependency contract - what the future preview may / must not read.
# ---------------------------------------------------------------------------
DATA_ALLOWED = [
    ("phase4b_result_json",
     "research/output/phase4b_nonproduction_candidate_package.json"),
    ("phase4b_csv_outputs",
     "research/output/phase4b_nonproduction_candidate_package/*.csv"),
]
DATA_FORBIDDEN = [
    ("raw_alpha_vantage_outputs", "must not read raw Alpha Vantage / provider price outputs"),
    ("call_providers", "must not call data providers"),
    ("train_models", "must not train models in the app"),
    ("d_drive_data", "must not use D: data drive files"),
    ("recompute_research_scores", "must not recompute research scores inside the app"),
]

# ---------------------------------------------------------------------------
# 5. Implementation sequence - safe future phases (no work done here).
# ---------------------------------------------------------------------------
IMPLEMENTATION_SEQUENCE = [
    ("4-D", "Paper Trader Read-Only Candidate Preview Service",
     "Add a read-only candidate preview service layer in Paper Trader that reads only "
     "the Phase 4-B package.", "Phase 4-C READY"),
    ("4-E", "Preview-Only API Endpoint",
     "Add the preview-only GET /v1/research/candidate-preview endpoint (read-only).",
     "4-D complete"),
    ("4-F", "Preview UI Panel + Safety Badges",
     "Add the UI panel (7 cards) and all safety badges.", "4-E complete"),
    ("4-G", "Tests + Smoke Checks",
     "Add Paper Trader tests and smoke checks for the preview endpoint and UI.",
     "4-F complete"),
    ("LATER", "Deferred - Refresh / Shadow / Production",
     "Later only: candidate refresh / shadow trading / production model candidate - "
     "each requires its own explicitly gated phase.", "explicit future approval"),
]

# ---------------------------------------------------------------------------
# 6. Validation test plan - required future Paper Trader tests.
# ---------------------------------------------------------------------------
VALIDATION_TESTS = [
    ("endpoint_returns_candidate_package", "endpoint returns the candidate package"),
    ("endpoint_is_read_only", "endpoint is read-only"),
    ("endpoint_no_database_write", "endpoint does not write database rows"),
    ("endpoint_no_orders", "endpoint does not create orders"),
    ("endpoint_no_trade_decisions", "endpoint does not create trade decisions"),
    ("ui_shows_all_safety_badges", "UI shows all safety badges"),
    ("ui_shows_risk_warnings", "UI shows risk warnings"),
    ("auth_api_key_rules_preserved", "auth / API-key rules are preserved"),
    ("existing_workflows_still_pass", "existing Paper Trader workflows still pass"),
]

# ---------------------------------------------------------------------------
# 7. No-go enforcement - what the future preview integration must NOT do.
# ---------------------------------------------------------------------------
NO_GO_ENFORCEMENT = [
    ("create_orders", "future preview integration must not create orders"),
    ("enable_automation", "must not enable automation"),
    ("enable_production_model_v2",
     "must not enable production model v2 (PREDICTOR_USE_MODEL_V2 stays disabled)"),
    ("create_live_portfolio_weights", "must not create live portfolio weights"),
    ("write_trade_decisions", "must not write to trade_decisions"),
    ("write_orders", "must not write to orders"),
    ("write_broker_execution_tables", "must not write to broker/execution tables"),
    ("call_network_providers", "must not call network providers"),
    ("modify_prediction_service", "must not modify the prediction service"),
    ("modify_gcp_service", "must not modify the GCP service"),
    ("deploy_anything", "must not deploy anything"),
]

# Exact output column orders.
API_CONTRACT_COLUMNS = ["api_item", "value", "category", "note"]
UI_CONTRACT_COLUMNS = ["ui_item", "kind", "required", "note"]
BADGE_COLUMNS = ["badge", "required", "surface", "note"]
DATA_DEP_COLUMNS = ["dependency_item", "allowed", "category", "note"]
IMPL_SEQ_COLUMNS = ["phase", "title", "scope", "gated_by"]
VALIDATION_COLUMNS = ["test_id", "requirement", "category", "note"]
NO_GO_COLUMNS = ["no_go_item", "enforced", "note"]
READINESS_COLUMNS = ["decision_item", "value", "passed", "note"]


# ---------------------------------------------------------------------------
# Input loading / validation.
# ---------------------------------------------------------------------------
def confirm_inputs():
    """Confirm all required Phase 4-B inputs exist."""
    status = {
        "phase4b_result_json_present": os.path.isfile(PHASE4B_RESULT_JSON),
        "phase4b_candidate_summary_card_present": os.path.isfile(PHASE4B_CANDIDATE_CARD_CSV),
        "phase4b_model_candidate_spec_present": os.path.isfile(PHASE4B_MODEL_SPEC_CSV),
        "phase4b_selected_strategy_spec_present": os.path.isfile(PHASE4B_STRATEGY_SPEC_CSV),
        "phase4b_evidence_scorecard_present": os.path.isfile(PHASE4B_EVIDENCE_CSV),
        "phase4b_risk_guardrails_present": os.path.isfile(PHASE4B_GUARDRAILS_CSV),
        "phase4b_known_failure_modes_present": os.path.isfile(PHASE4B_FAILURE_CSV),
        "phase4b_preview_integration_contract_present": os.path.isfile(PHASE4B_PREVIEW_CONTRACT_CSV),
        "phase4b_no_go_items_present": os.path.isfile(PHASE4B_NO_GO_CSV),
        "phase4b_readiness_decision_table_present": os.path.isfile(PHASE4B_READINESS_CSV),
    }
    status["all_required_present"] = all(status.values())
    return status


def _read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def gather_candidate():
    """Read the Phase 4-B package verbatim (no recompute, no faking)."""
    b = _read_json(PHASE4B_RESULT_JSON)
    rec = (b.get("recommendation") or {}).get("recommendation")
    return {
        "b": b,
        "phase4b_recommendation": rec,
        "candidate_id": b.get("candidate_id"),
        "candidate_name": b.get("candidate_name"),
        "model_name": (b.get("selected_candidate") or {}).get("model_name"),
        "horizon": (b.get("selected_candidate") or {}).get("horizon"),
        "strategy_name": (b.get("selected_candidate") or {}).get("strategy_name"),
        "selected_candidate": b.get("selected_candidate") or {},
        "evidence_summary": b.get("evidence_summary") or {},
        "mandatory_guardrails": b.get("mandatory_guardrails") or [],
        "known_failure_modes": b.get("known_failure_modes") or [],
        "no_go_items": b.get("no_go_items") or [],
        "sharpe_caveat": b.get("sharpe_caveat"),
    }


# ---------------------------------------------------------------------------
# Decision.
# ---------------------------------------------------------------------------
def evaluate_gates(cand, inputs_ok):
    """Return the list of decision gates (decision_item, value, passed, note, _kind)."""
    rec = cand.get("phase4b_recommendation") if cand else None
    api_fields = len(API_RESPONSE_FIELDS)
    ui_sections = len(UI_SECTIONS)
    badges = len(SAFETY_BADGES)
    no_gos = len(NO_GO_ENFORCEMENT)
    impl = len(IMPLEMENTATION_SEQUENCE)
    tests = len(VALIDATION_TESTS)

    return [
        {
            "decision_item": "phase4b_package_present",
            "value": inputs_ok,
            "passed": bool(inputs_ok),
            "note": "Phase 4-B package and CSV outputs must all be present",
            "_kind": "inputs",
        },
        {
            "decision_item": "phase4b_recommendation_ready",
            "value": rec,
            "passed": rec == EXPECTED_4B_RECOMMENDATION,
            "note": "Phase 4-B must be %s" % EXPECTED_4B_RECOMMENDATION,
            "_kind": "inputs",
        },
        {
            "decision_item": "all_required_api_fields_present",
            "value": api_fields,
            "passed": api_fields == 15,
            "note": "all 15 required preview API response fields must be defined",
            "_kind": "contract",
        },
        {
            "decision_item": "all_required_ui_sections_present",
            "value": ui_sections,
            "passed": ui_sections == 7,
            "note": "all 7 required preview UI sections must be defined",
            "_kind": "contract",
        },
        {
            "decision_item": "implementation_sequence_present",
            "value": impl,
            "passed": impl >= 5,
            "note": "implementation sequence 4-D..4-G (+ deferred) must be defined",
            "_kind": "contract",
        },
        {
            "decision_item": "validation_test_plan_present",
            "value": tests,
            "passed": tests == 9,
            "note": "all 9 required validation tests must be defined",
            "_kind": "contract",
        },
        {
            "decision_item": "all_required_safety_badges_present",
            "value": badges,
            "passed": badges == 11,
            "note": "all 11 required safety badges must be present",
            "_kind": "safety",
        },
        {
            "decision_item": "all_no_go_items_present",
            "value": no_gos,
            "passed": no_gos == 11,
            "note": "all 11 no-go enforcement items must be present",
            "_kind": "safety",
        },
        {
            "decision_item": "paper_trader_files_unmodified",
            "value": False,
            "passed": True,
            "note": "no Paper Trader file is modified by this planning phase",
            "_kind": "safety",
        },
    ]


def decide(inputs_ok, gates):
    if not inputs_ok:
        return DEC_BLOCKED_INPUTS
    inputs_gate_ok = all(g["passed"] for g in gates if g["_kind"] == "inputs")
    if not inputs_gate_ok:
        return DEC_BLOCKED_INPUTS
    safety_ok = all(g["passed"] for g in gates if g["_kind"] == "safety")
    if not safety_ok:
        return DEC_BLOCKED_SAFETY
    contract_ok = all(g["passed"] for g in gates if g["_kind"] == "contract")
    if contract_ok:
        return DEC_READY
    return DEC_PARTIAL


def build_recommended_next_phase(decision):
    title = {
        DEC_READY: "Paper Trader Read-Only Candidate Preview Service",
        DEC_PARTIAL: "Repair Preview Integration Plan",
        DEC_BLOCKED_INPUTS: "Repair Phase 4-C Inputs",
        DEC_BLOCKED_SAFETY: "Repair Preview Safety Contract",
    }[decision]
    return {
        "phase": "4-D",
        "title": "4-D - %s" % title,
        "purpose": "Proceed to the Paper Trader read-only candidate preview service."
        if decision == DEC_READY
        else "Repair Phase 4-C before proceeding to 4-D implementation.",
    }


def build_safety_flags():
    return {
        "research_only": True,
        "planning_only": True,
        "database_touched": False,
        "database_write_executed": False,
        "migration_executed": False,
        "deployment_executed": False,
        "paper_trader_files_modified": False,
        "paper_trader_code_implemented": False,
        "model_v2_enabled": False,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "nonproduction_model_candidate_packaged": False,
        "deployable_model_artifact_written": False,
        "production_predictions_computed": False,
        "production_scores_computed": False,
        "live_portfolio_weights_computed": False,
        "research_portfolio_weights_computed": False,
        "order_instructions_created": False,
        "orders_created": False,
        "trades_created": False,
        "d_drive_read": False,
        "d_drive_written": False,
        "provider_api_called": False,
        "alpha_vantage_called": False,
        "fred_called": False,
        "stooq_called": False,
        "yahoo_called": False,
        "yfinance_called": False,
        "paid_vendor_api_called": False,
        "network_called": False,
        "data_faked": False,
        "metrics_faked": False,
        "labels_for_validation_only": True,
    }


# ---------------------------------------------------------------------------
# Output assembly.
# ---------------------------------------------------------------------------
def _ensure_dirs():
    os.makedirs(_C_DIR, exist_ok=True)


def _write_csv(rows, columns, path):
    df = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)
    df.to_csv(path, index=False)


def _build_api_contract_rows():
    rows = [
        {"api_item": "endpoint", "value": PREVIEW_ENDPOINT, "category": "endpoint",
         "note": "future preview-only endpoint (NOT implemented in this phase)"},
        {"api_item": "method", "value": "GET", "category": "method",
         "note": "read-only HTTP GET"},
    ]
    rows += [
        {"api_item": key, "value": val, "category": "constraint", "note": note}
        for key, val, note in API_CONSTRAINTS
    ]
    rows += [
        {"api_item": key, "value": "required", "category": "response_field", "note": note}
        for key, note in API_RESPONSE_FIELDS
    ]
    return rows


def _build_ui_contract_rows():
    rows = [
        {"ui_item": name, "kind": kind, "required": True, "note": note}
        for name, kind, note in UI_SECTIONS
    ]
    rows += [
        {"ui_item": badge, "kind": "banner_badge", "required": True,
         "note": "always-visible preview safety badge"}
        for badge in UI_BANNER_BADGES
    ]
    return rows


def _build_badge_rows():
    return [
        {"badge": badge, "required": True, "surface": surface, "note": note}
        for badge, surface, note in SAFETY_BADGES
    ]


def _build_data_dep_rows():
    rows = [
        {"dependency_item": key, "allowed": True, "category": "may_read", "note": path}
        for key, path in DATA_ALLOWED
    ]
    rows += [
        {"dependency_item": key, "allowed": False, "category": "must_not", "note": note}
        for key, note in DATA_FORBIDDEN
    ]
    return rows


def _build_impl_seq_rows():
    return [
        {"phase": ph, "title": title, "scope": scope, "gated_by": gated_by}
        for ph, title, scope, gated_by in IMPLEMENTATION_SEQUENCE
    ]


def _build_validation_rows():
    return [
        {"test_id": tid, "requirement": req, "category": "future_paper_trader_test",
         "note": "required before the preview integration is accepted"}
        for tid, req in VALIDATION_TESTS
    ]


def _build_no_go_rows():
    return [
        {"no_go_item": key, "enforced": True, "note": note}
        for key, note in NO_GO_ENFORCEMENT
    ]


def _build_readiness_rows(gates, decision):
    rows = [
        {"decision_item": g["decision_item"], "value": g["value"],
         "passed": bool(g["passed"]), "note": g["note"]}
        for g in gates
    ]
    rows.append({
        "decision_item": "final_recommendation", "value": decision,
        "passed": decision == DEC_READY,
        "note": "planning-only; no Paper Trader code implemented in this phase",
    })
    return rows


def _write_plan_csvs(gates, decision):
    """Write the intrinsic plan matrices + readiness table (safe in any decision)."""
    _ensure_dirs()
    _write_csv(_build_api_contract_rows(), API_CONTRACT_COLUMNS, _OUT_PATHS["preview_api_contract"])
    _write_csv(_build_ui_contract_rows(), UI_CONTRACT_COLUMNS, _OUT_PATHS["preview_ui_contract"])
    _write_csv(_build_badge_rows(), BADGE_COLUMNS, _OUT_PATHS["safety_badge_matrix"])
    _write_csv(_build_data_dep_rows(), DATA_DEP_COLUMNS, _OUT_PATHS["data_dependency_contract"])
    _write_csv(_build_impl_seq_rows(), IMPL_SEQ_COLUMNS, _OUT_PATHS["implementation_sequence"])
    _write_csv(_build_validation_rows(), VALIDATION_COLUMNS, _OUT_PATHS["validation_test_plan"])
    _write_csv(_build_no_go_rows(), NO_GO_COLUMNS, _OUT_PATHS["no_go_enforcement"])
    _write_csv(_build_readiness_rows(gates, decision), READINESS_COLUMNS,
               _OUT_PATHS["readiness_decision_table"])


def _source_files():
    return {
        "phase4b_result_json": os.path.relpath(PHASE4B_RESULT_JSON, _REPO_ROOT).replace("\\", "/"),
        "phase4b_candidate_summary_card_csv": os.path.relpath(PHASE4B_CANDIDATE_CARD_CSV, _REPO_ROOT).replace("\\", "/"),
        "phase4b_model_candidate_spec_csv": os.path.relpath(PHASE4B_MODEL_SPEC_CSV, _REPO_ROOT).replace("\\", "/"),
        "phase4b_selected_strategy_spec_csv": os.path.relpath(PHASE4B_STRATEGY_SPEC_CSV, _REPO_ROOT).replace("\\", "/"),
        "phase4b_evidence_scorecard_csv": os.path.relpath(PHASE4B_EVIDENCE_CSV, _REPO_ROOT).replace("\\", "/"),
        "phase4b_risk_guardrails_csv": os.path.relpath(PHASE4B_GUARDRAILS_CSV, _REPO_ROOT).replace("\\", "/"),
        "phase4b_known_failure_modes_csv": os.path.relpath(PHASE4B_FAILURE_CSV, _REPO_ROOT).replace("\\", "/"),
        "phase4b_preview_integration_contract_csv": os.path.relpath(PHASE4B_PREVIEW_CONTRACT_CSV, _REPO_ROOT).replace("\\", "/"),
        "phase4b_no_go_items_csv": os.path.relpath(PHASE4B_NO_GO_CSV, _REPO_ROOT).replace("\\", "/"),
        "phase4b_readiness_decision_table_csv": os.path.relpath(PHASE4B_READINESS_CSV, _REPO_ROOT).replace("\\", "/"),
    }


def _base_result(inputs_status, generated_at, decision, gates, cand):
    nxt = build_recommended_next_phase(decision)
    cand = cand or {}
    result = {
        "phase": PHASE,
        "generated_at": generated_at or datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "inputs_read": _source_files(),
        "input_confirmation": inputs_status,
        "candidate_id": cand.get("candidate_id"),
        "candidate_name": cand.get("candidate_name"),
        "preview_api_contract": {
            "endpoint": PREVIEW_ENDPOINT,
            "method": "GET",
            "read_only": True,
            "database_write": False,
            "creates_signals": False,
            "creates_trade_decisions": False,
            "creates_orders": False,
            "required_response_fields": [k for k, _ in API_RESPONSE_FIELDS],
        },
        "preview_ui_contract": {
            "sections": [name for name, _, _ in UI_SECTIONS],
            "banner_badges": list(UI_BANNER_BADGES),
        },
        "safety_badges": [badge for badge, _, _ in SAFETY_BADGES],
        "data_dependency_contract": {
            "may_read": [k for k, _ in DATA_ALLOWED],
            "must_not": [k for k, _ in DATA_FORBIDDEN],
        },
        "implementation_sequence": [
            {"phase": ph, "title": title, "gated_by": gated_by}
            for ph, title, _, gated_by in IMPLEMENTATION_SEQUENCE
        ],
        "validation_test_plan": [tid for tid, _ in VALIDATION_TESTS],
        "no_go_items": [k for k, _ in NO_GO_ENFORCEMENT],
        "candidate_summary": {
            "model_name": cand.get("model_name"),
            "horizon": cand.get("horizon"),
            "strategy_name": cand.get("strategy_name"),
            "phase4b_recommendation": cand.get("phase4b_recommendation"),
            "status": PREVIEW_STATUS,
        },
        "evidence_summary": cand.get("evidence_summary") or {},
        "selected_strategy_summary": cand.get("selected_candidate") or {},
        "mandatory_guardrails": cand.get("mandatory_guardrails") or [],
        "known_failure_modes": cand.get("known_failure_modes") or [],
        "sharpe_caveat": cand.get("sharpe_caveat"),
        "decision_gates": [
            {"decision_item": g["decision_item"], "value": g["value"], "passed": bool(g["passed"])}
            for g in gates
        ],
        "recommended_next_phase": nxt,
        "outputs_written": {k: os.path.relpath(v, _REPO_ROOT).replace("\\", "/")
                            for k, v in _OUT_PATHS.items()},
        "interpretation": {
            "phase_is_planning_only": True,
            "paper_trader_code_implemented": False,
            "preview_endpoint_implemented": False,
            "scores_are_research_oos_not_production": True,
            "labels_used_for_validation_only": True,
            "production_edge_claimed": False,
        },
    }
    return result, nxt


def _finish_blocked(inputs_status, generated_at, reason, cand=None):
    """Write the intrinsic plan CSVs + a BLOCKED_INPUTS result (no fake candidate)."""
    decision = DEC_BLOCKED_INPUTS
    gates = evaluate_gates(cand, inputs_ok=False)
    _write_plan_csvs(gates, decision)
    result, nxt = _base_result(inputs_status, generated_at, decision, gates, cand)
    result["blocked"] = True
    result["blocker_reason"] = reason
    result["recommendation"] = {
        "recommendation": decision,
        "allowed_values": list(ALLOWED_RECOMMENDATIONS),
        "research_only": True,
        "planning_only": True,
        "implement_paper_trader_now": False,
        "deploy_now": False,
        "reason": reason,
    }
    result.update(build_safety_flags())
    with open(RESULT_JSON, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    return result


def run(generated_at=None):
    inputs_status = confirm_inputs()
    if not inputs_status["all_required_present"]:
        missing = [k for k, v in inputs_status.items()
                   if k != "all_required_present" and not v]
        return _finish_blocked(inputs_status, generated_at,
                               "Required Phase 4-B inputs missing: %s" % ", ".join(missing))

    try:
        cand = gather_candidate()
    except Exception as exc:  # unreadable / malformed upstream package
        return _finish_blocked(inputs_status, generated_at,
                               "Phase 4-B package unreadable/invalid: %s" % exc)

    gates = evaluate_gates(cand, inputs_ok=True)
    decision = decide(True, gates)

    # If the upstream candidate is not READY, treat the inputs as not plan-ready.
    if decision == DEC_BLOCKED_INPUTS:
        return _finish_blocked(
            inputs_status, generated_at,
            "Phase 4-B recommendation is '%s' (expected %s); upstream candidate is not "
            "ready for preview integration planning."
            % (cand.get("phase4b_recommendation"), EXPECTED_4B_RECOMMENDATION),
            cand=cand,
        )

    _write_plan_csvs(gates, decision)
    result, nxt = _base_result(inputs_status, generated_at, decision, gates, cand)

    if decision == DEC_READY:
        reason = (
            "Phase 4-B is %s for candidate %s; all 15 preview API fields, 7 UI sections, "
            "11 safety badges, 11 no-go items, the 4-D..4-G implementation sequence, and the "
            "9 validation tests are defined, and no Paper Trader file is modified. The "
            "preview integration plan is implementation-ready (planning only; nothing built)."
            % (cand.get("phase4b_recommendation"), cand.get("candidate_id"))
        )
    elif decision == DEC_BLOCKED_SAFETY:
        failed = [g["decision_item"] for g in gates if g["_kind"] == "safety" and not g["passed"]]
        reason = ("A safety contract gate failed (%s); the preview safety contract must be "
                  "repaired before planning is accepted." % ", ".join(failed))
    else:  # PARTIAL
        failed = [g["decision_item"] for g in gates if not g["passed"]]
        reason = ("Plan exists but a non-critical contract item is missing (%s); repair before "
                  "implementation." % ", ".join(failed))

    result["recommendation"] = {
        "recommendation": decision,
        "allowed_values": list(ALLOWED_RECOMMENDATIONS),
        "research_only": True,
        "planning_only": True,
        "implement_paper_trader_now": False,
        "create_production_model_candidate_now": False,
        "compute_live_portfolio_weights_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "reason": reason,
    }
    result.update(build_safety_flags())
    with open(RESULT_JSON, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    return result


def main():
    result = run()
    rec = result.get("recommendation", {}).get("recommendation")
    nxt = result.get("recommended_next_phase", {})
    print("phase %s | rec %s | next %s | candidate %s | api_fields %d | ui_sections %d | "
          "badges %d | no_go %d"
          % (result.get("phase"), rec, nxt.get("phase"), result.get("candidate_id"),
             len(result.get("preview_api_contract", {}).get("required_response_fields", [])),
             len(result.get("preview_ui_contract", {}).get("sections", [])),
             len(result.get("safety_badges", [])),
             len(result.get("no_go_items", []))))
    return result


if __name__ == "__main__":
    main()
