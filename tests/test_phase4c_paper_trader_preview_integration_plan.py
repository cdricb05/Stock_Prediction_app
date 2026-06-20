"""Self-running tests for Phase 4-C - Paper Trader Preview Integration Plan.

Verifies the runner is research/planning-only (no network / vendor / DB / deploy /
D: / Paper Trader code / production-artifact tokens), produces the required plan
outputs (preview API contract, UI contract, safety badge matrix, data dependency
contract, implementation sequence, validation test plan, no-go enforcement, readiness
table), and that READY is reported only when every contract/safety gate actually
passes. Runs under pytest and standalone.
"""

import json
import os
import re
import sys
from pathlib import Path

import pytest

# Ensure the repo root is importable when this file is run as a standalone
# script (``python -B tests/test_phase4c_paper_trader_preview_integration_plan.py``),
# where the repo root is not automatically on sys.path the way it is under pytest.
_REPO_ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT_BOOTSTRAP))

from research import run_phase4c_paper_trader_preview_integration_plan as r4c

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_RUNNER_PATH = os.path.join(_REPO_ROOT, "research", "run_phase4c_paper_trader_preview_integration_plan.py")
with open(_RUNNER_PATH, "r", encoding="utf-8") as _fh:
    _RUNNER_SRC = _fh.read()
_SRC_LOWER = _RUNNER_SRC.lower()

# ---------------------------------------------------------------------------
# Forbidden-token discipline (mirrors Phase 4-A / 4-B).
# ---------------------------------------------------------------------------
_FORBIDDEN_IMPORT_ROOTS = [
    "urllib", "http", "httpx", "socket", "requests", "aiohttp", "yfinance",
    "pandas_datareader", "alpha_vantage", "fredapi", "sklearn", "torch",
    "tensorflow", "keras", "xgboost", "lightgbm", "sqlalchemy", "psycopg2",
    "boto3", "google", "ftplib", "telnetlib",
]
_FORBIDDEN_USAGE = [
    "requests.get", "requests.post", "urlopen", "urllib.request", "httpx.",
    "aiohttp.", "alphavantage.co", "www.alphavantage", "finance.yahoo",
    "query1.finance", "query2.finance", "stooq.com", "stooq.pl",
    "stlouisfed.org", "fredapi", "finnhub.io", "polygon.io", "yf.download",
    "yfinance.", "socket.socket", "smtplib",
]
_FORBIDDEN_DB = [
    "psycopg2", "sqlalchemy", "create_engine", "session.add", "session.commit",
    "db.session", "cursor.execute", "insert into", "update set", "delete from",
    ".to_sql(",
]
# "predictor_use_model_v2" / "broker" / "trade_decisions" / "orders" / "deploy" are
# intentionally excluded: in this planning runner they appear only inside contract /
# no-go / safety-badge TEXT asserting those actions STAY FORBIDDEN. The real
# protection is the False safety flags (model_v2_enabled, orders_created,
# deployment_executed, ...) checked in test_safety_flags_true_and_false.
_FORBIDDEN_DEPLOY_TRADE = [
    "alembic", "subprocess", "os.system", "docker", "kubectl",
    "place_order", "submit_order",
]
# Bare "production_model" / "deployable_model" are intentionally excluded: they
# appear only as *False* safety-flag key names, not as real artifact writes.
_FORBIDDEN_ARTIFACT = [
    ".joblib", ".pkl", "pickle.dump", "torch.save", "model.save(", "joblib.dump",
]

_REQUIRED_TRUE = [
    "research_only",
    "planning_only",
    "labels_for_validation_only",
]
_REQUIRED_FALSE = [
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "paper_trader_files_modified",
    "paper_trader_code_implemented", "model_v2_enabled", "production_model_trained",
    "production_model_candidate_created", "nonproduction_model_candidate_packaged",
    "deployable_model_artifact_written", "production_predictions_computed",
    "production_scores_computed", "live_portfolio_weights_computed",
    "research_portfolio_weights_computed", "order_instructions_created",
    "orders_created", "trades_created", "d_drive_read", "d_drive_written",
    "provider_api_called", "alpha_vantage_called", "fred_called", "stooq_called",
    "yahoo_called", "yfinance_called", "paid_vendor_api_called", "network_called",
    "data_faked", "metrics_faked",
]

_REQUIRED_CSVS = [
    "preview_api_contract.csv",
    "preview_ui_contract.csv",
    "safety_badge_matrix.csv",
    "data_dependency_contract.csv",
    "implementation_sequence.csv",
    "validation_test_plan.csv",
    "no_go_enforcement.csv",
    "readiness_decision_table.csv",
]

_REQUIRED_API_FIELDS = [
    "candidate_id", "candidate_name", "model_name", "horizon", "strategy_name",
    "status", "recommendation", "evidence_summary", "selected_strategy_summary",
    "risk_guardrails", "known_failure_modes", "no_go_items", "safety_badges",
    "generated_at", "source_files",
]
_REQUIRED_UI_SECTIONS = [
    "Candidate Summary card", "Evidence card", "Strategy card",
    "Risk Guardrails card", "Known Failure Modes card", "No-Go Items card",
    "Preview-only safety banner",
]
_REQUIRED_SAFETY_BADGES = [
    "PREVIEW ONLY", "NON-PRODUCTION CANDIDATE", "RESEARCH ONLY", "NO ORDERS",
    "NO BROKER EXECUTION", "NO AUTOMATION", "NO LIVE PORTFOLIO WEIGHTS",
    "MANUAL REVIEW REQUIRED", "OVERLAPPING LABEL WARNING",
    "SURVIVORSHIP BIAS WARNING", "2024 DRAWDOWN WARNING",
]
_REQUIRED_NO_GO = [
    "create_orders", "enable_automation", "enable_production_model_v2",
    "create_live_portfolio_weights", "write_trade_decisions", "write_orders",
    "write_broker_execution_tables", "call_network_providers",
    "modify_prediction_service", "modify_gcp_service", "deploy_anything",
]
_REQUIRED_IMPL_PHASES = ["4-D", "4-E", "4-F", "4-G", "LATER"]
_REQUIRED_VALIDATION_TESTS = [
    "endpoint_returns_candidate_package", "endpoint_is_read_only",
    "endpoint_no_database_write", "endpoint_no_orders", "endpoint_no_trade_decisions",
    "ui_shows_all_safety_badges", "ui_shows_risk_warnings",
    "auth_api_key_rules_preserved", "existing_workflows_still_pass",
]


# ---------------------------------------------------------------------------
# Shared run fixture.
# ---------------------------------------------------------------------------
_RESULT = None


def _get_result():
    global _RESULT
    if _RESULT is None:
        _RESULT = r4c.run(generated_at="2026-06-20T00:00:00")
    return _RESULT


def _read_csv_rows(name):
    path = r4c._OUT_PATHS[name.replace(".csv", "")]
    import csv
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------------------
# Static source tests.
# ---------------------------------------------------------------------------
def test_runner_compiles():
    import py_compile
    py_compile.compile(_RUNNER_PATH, doraise=True)


def test_no_forbidden_imports():
    for root in _FORBIDDEN_IMPORT_ROOTS:
        pat = re.compile(r"^\s*(?:import|from)\s+%s\b" % re.escape(root), re.MULTILINE)
        assert not pat.search(_RUNNER_SRC), "forbidden import root: %s" % root


def test_no_forbidden_network_usage():
    for tok in _FORBIDDEN_USAGE:
        assert tok.lower() not in _SRC_LOWER, "forbidden network usage: %s" % tok


def test_no_db_tokens():
    for tok in _FORBIDDEN_DB:
        assert tok.lower() not in _SRC_LOWER, "forbidden db token: %s" % tok


def test_no_deploy_trade_or_artifact_tokens():
    for tok in _FORBIDDEN_DEPLOY_TRADE + _FORBIDDEN_ARTIFACT:
        assert tok.lower() not in _SRC_LOWER, "forbidden deploy/trade/artifact token: %s" % tok


def test_no_literal_d_drive_access():
    assert "d:\\" not in _SRC_LOWER and "d:/" not in _SRC_LOWER, "literal D: drive path present"


def test_inputs_only_reference_phase4b():
    # The runner must read only the Phase 4-B package (no earlier-phase artifacts).
    for other in ["phase3m", "phase3u", "phase3v", "phase3w", "phase3x", "phase3y",
                  "phase3z", "phase4a"]:
        assert other not in _SRC_LOWER, "runner references disallowed phase: %s" % other


def test_allowed_recommendations_constant():
    assert r4c.ALLOWED_RECOMMENDATIONS == (
        "PREVIEW_INTEGRATION_PLAN_READY",
        "PREVIEW_INTEGRATION_PLAN_PARTIAL",
        "PREVIEW_INTEGRATION_PLAN_BLOCKED_INPUTS",
        "PREVIEW_INTEGRATION_PLAN_BLOCKED_SAFETY",
    )


def test_decide_unit():
    # Inputs missing -> BLOCKED_INPUTS regardless of gates.
    assert r4c.decide(False, []) == r4c.DEC_BLOCKED_INPUTS
    inputs_fail = [{"_kind": "inputs", "passed": False}, {"_kind": "safety", "passed": True},
                   {"_kind": "contract", "passed": True}]
    assert r4c.decide(True, inputs_fail) == r4c.DEC_BLOCKED_INPUTS
    safety_fail = [{"_kind": "inputs", "passed": True}, {"_kind": "safety", "passed": False},
                   {"_kind": "contract", "passed": True}]
    assert r4c.decide(True, safety_fail) == r4c.DEC_BLOCKED_SAFETY
    contract_fail = [{"_kind": "inputs", "passed": True}, {"_kind": "safety", "passed": True},
                     {"_kind": "contract", "passed": False}]
    assert r4c.decide(True, contract_fail) == r4c.DEC_PARTIAL
    all_pass = [{"_kind": "inputs", "passed": True}, {"_kind": "safety", "passed": True},
                {"_kind": "contract", "passed": True}]
    assert r4c.decide(True, all_pass) == r4c.DEC_READY


def test_next_phase_is_4d_for_all_decisions():
    for dec in r4c.ALLOWED_RECOMMENDATIONS:
        assert r4c.build_recommended_next_phase(dec)["phase"] == "4-D"


# ---------------------------------------------------------------------------
# Result / output tests.
# ---------------------------------------------------------------------------
def test_result_json_exists_and_phase():
    _get_result()
    assert os.path.isfile(r4c.RESULT_JSON)
    with open(r4c.RESULT_JSON, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["phase"] == "4-C"


def test_recommended_next_phase_is_4d():
    res = _get_result()
    assert res["recommended_next_phase"]["phase"] == "4-D"


def test_recommendation_is_allowed():
    res = _get_result()
    assert res["recommendation"]["recommendation"] in r4c.ALLOWED_RECOMMENDATIONS


def test_all_required_csv_outputs_exist():
    _get_result()
    for name in _REQUIRED_CSVS:
        path = os.path.join(r4c._C_DIR, name)
        assert os.path.isfile(path), "missing output: %s" % name


def test_output_files_git_safe_under_50mb():
    _get_result()
    limit = 50 * 1024 * 1024
    for name in _REQUIRED_CSVS:
        path = os.path.join(r4c._C_DIR, name)
        assert os.path.getsize(path) < limit, "%s too large" % name
    assert os.path.getsize(r4c.RESULT_JSON) < limit


def test_safety_flags_true_and_false():
    res = _get_result()
    for k in _REQUIRED_TRUE:
        assert res.get(k) is True, "expected %s True" % k
    for k in _REQUIRED_FALSE:
        assert res.get(k) is False, "expected %s False" % k


def test_planning_only_no_paper_trader_code():
    res = _get_result()
    assert res["planning_only"] is True
    assert res["paper_trader_files_modified"] is False
    assert res["paper_trader_code_implemented"] is False
    assert res["production_model_candidate_created"] is False
    assert res["nonproduction_model_candidate_packaged"] is False
    assert res["production_predictions_computed"] is False
    assert res["production_scores_computed"] is False
    assert res["live_portfolio_weights_computed"] is False
    assert res["research_portfolio_weights_computed"] is False
    assert res["deployable_model_artifact_written"] is False
    assert res["orders_created"] is False and res["trades_created"] is False
    assert res["d_drive_read"] is False and res["d_drive_written"] is False
    assert res["network_called"] is False
    assert res["data_faked"] is False and res["metrics_faked"] is False


def test_all_required_api_fields_present():
    _get_result()
    rows = _read_csv_rows("preview_api_contract")
    fields = {r["api_item"] for r in rows if r["category"] == "response_field"}
    for f in _REQUIRED_API_FIELDS:
        assert f in fields, "missing API response field: %s" % f
    res = _get_result()
    assert set(res["preview_api_contract"]["required_response_fields"]) == set(_REQUIRED_API_FIELDS)
    # Endpoint is read-only and writes nothing.
    constraints = {r["api_item"]: r["value"] for r in rows if r["category"] == "constraint"}
    assert constraints["read_only"] == "True"
    assert constraints["database_write"] == "False"
    assert constraints["creates_orders"] == "False"
    assert constraints["creates_trade_decisions"] == "False"


def test_all_required_ui_sections_present():
    _get_result()
    rows = _read_csv_rows("preview_ui_contract")
    sections = {r["ui_item"] for r in rows if r["kind"] in ("card", "banner")}
    for s in _REQUIRED_UI_SECTIONS:
        assert s in sections, "missing UI section: %s" % s
    banner = {r["ui_item"] for r in rows if r["kind"] == "banner_badge"}
    for b in ["PREVIEW ONLY", "NO ORDERS", "NO AUTOMATION", "NO LIVE WEIGHTS",
              "NON-PRODUCTION CANDIDATE", "MANUAL REVIEW REQUIRED"]:
        assert b in banner, "missing UI banner badge: %s" % b


def test_all_required_safety_badges_present():
    _get_result()
    rows = _read_csv_rows("safety_badge_matrix")
    badges = {r["badge"] for r in rows}
    for b in _REQUIRED_SAFETY_BADGES:
        assert b in badges, "missing safety badge: %s" % b
    res = _get_result()
    assert set(res["safety_badges"]) == set(_REQUIRED_SAFETY_BADGES)


def test_all_no_go_items_present():
    _get_result()
    rows = _read_csv_rows("no_go_enforcement")
    items = {r["no_go_item"] for r in rows}
    for n in _REQUIRED_NO_GO:
        assert n in items, "missing no-go item: %s" % n
    for r in rows:
        assert r["enforced"] == "True"
    res = _get_result()
    assert set(res["no_go_items"]) == set(_REQUIRED_NO_GO)


def test_implementation_sequence_present():
    _get_result()
    rows = _read_csv_rows("implementation_sequence")
    phases = [r["phase"] for r in rows]
    for ph in _REQUIRED_IMPL_PHASES:
        assert ph in phases, "missing implementation phase: %s" % ph


def test_validation_test_plan_present():
    _get_result()
    rows = _read_csv_rows("validation_test_plan")
    tests = {r["test_id"] for r in rows}
    for t in _REQUIRED_VALIDATION_TESTS:
        assert t in tests, "missing validation test: %s" % t


def test_data_dependency_contract_allows_phase4b_forbids_providers():
    _get_result()
    rows = _read_csv_rows("data_dependency_contract")
    allowed = {r["dependency_item"] for r in rows if r["allowed"] == "True"}
    forbidden = {r["dependency_item"] for r in rows if r["allowed"] == "False"}
    assert "phase4b_result_json" in allowed and "phase4b_csv_outputs" in allowed
    assert "call_providers" in forbidden
    assert "d_drive_data" in forbidden
    assert "recompute_research_scores" in forbidden


def test_evidence_not_faked_matches_phase4b():
    """Candidate evidence must equal the Phase 4-B package numbers (not faked)."""
    res = _get_result()
    if res["recommendation"]["recommendation"] != r4c.DEC_READY:
        pytest.skip("non-READY run: candidate evidence not populated")
    b = r4c._read_json(r4c.PHASE4B_RESULT_JSON)
    assert res["candidate_id"] == b["candidate_id"]
    assert res["candidate_name"] == b["candidate_name"]
    assert res["evidence_summary"] == b["evidence_summary"]
    assert res["selected_strategy_summary"] == b["selected_candidate"]
    assert res["candidate_summary"]["model_name"] == b["selected_candidate"]["model_name"]
    assert res["candidate_summary"]["strategy_name"] == b["selected_candidate"]["strategy_name"]


def test_ready_only_if_all_gates_pass():
    """If the recommendation is READY, every readiness decision row must have passed."""
    res = _get_result()
    rows = _read_csv_rows("readiness_decision_table")
    if res["recommendation"]["recommendation"] == r4c.DEC_READY:
        for row in rows:
            assert row["passed"] == "True", "READY but gate failed: %s" % row["decision_item"]


def test_blocked_inputs_writes_no_fake_candidate(monkeypatch):
    """Missing Phase 4-B package -> BLOCKED_INPUTS, no fabricated candidate."""
    monkeypatch.setattr(r4c, "PHASE4B_RESULT_JSON",
                        os.path.join(r4c._OUT_BASE, "_missing_4b.json"))
    res = r4c.run(generated_at="2026-06-20T00:00:00")
    assert res["recommendation"]["recommendation"] == r4c.DEC_BLOCKED_INPUTS
    assert res["paper_trader_code_implemented"] is False
    assert res["recommended_next_phase"]["phase"] == "4-D"
    assert res["candidate_id"] is None
    assert res["evidence_summary"] == {}
    # restore a clean, valid run for any later assertions.
    monkeypatch.undo()
    global _RESULT
    _RESULT = None
    _get_result()


# ---------------------------------------------------------------------------
# Standalone harness.
# ---------------------------------------------------------------------------
def _run_all():
    import types
    funcs = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and isinstance(v, types.FunctionType)]
    passed = 0
    for fn in funcs:
        argcount = fn.__code__.co_argcount
        if argcount:
            # tests that need monkeypatch are exercised under pytest only.
            print("SKIP (needs fixture): %s" % fn.__name__)
            continue
        try:
            fn()
            passed += 1
            print("PASS: %s" % fn.__name__)
        except Exception:
            # A skip outside pytest raises; treat it as skipped, not failed.
            import pytest as _pt
            if isinstance(sys.exc_info()[1], _pt.skip.Exception):
                print("SKIP: %s" % fn.__name__)
                continue
            raise
    print("\n%d standalone tests passed" % passed)


if __name__ == "__main__":
    _run_all()
