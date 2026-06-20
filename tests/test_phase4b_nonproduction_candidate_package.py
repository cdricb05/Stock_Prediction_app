"""Self-running tests for Phase 4-B - Non-Production Model Candidate Package.

Verifies the runner is research-only (no network / vendor / DB / deploy / D: /
Paper Trader / production-artifact tokens), produces the required governance outputs,
packages a non-production candidate, and that READY is reported only when every
decision gate actually passes. Runs under pytest and standalone.
"""

import json
import os
import re
import sys
from pathlib import Path

import pytest

# Ensure the repo root is importable when this file is run as a standalone
# script (``python -B tests/test_phase4b_nonproduction_candidate_package.py``),
# where the repo root is not automatically on sys.path the way it is under pytest.
_REPO_ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT_BOOTSTRAP))

from research import run_phase4b_nonproduction_candidate_package as r4b

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_RUNNER_PATH = os.path.join(_REPO_ROOT, "research", "run_phase4b_nonproduction_candidate_package.py")
with open(_RUNNER_PATH, "r", encoding="utf-8") as _fh:
    _RUNNER_SRC = _fh.read()
_SRC_LOWER = _RUNNER_SRC.lower()

# ---------------------------------------------------------------------------
# Forbidden-token discipline (mirrors Phase 4-A).
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
# "predictor_use_model_v2" is intentionally excluded: the runner names the flag only
# in guardrail / no-go text asserting it STAYS DISABLED. The real protection is the
# model_v2_enabled == False safety flag, checked in test_safety_flags_true_and_false.
# "broker"/"create_order" likewise appear only as forbidden preview-contract / no-go
# item names, so they are excluded here and covered by the contract/flag assertions.
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
    "nonproduction_model_candidate_packaged",
    "labels_for_validation_only",
]
_REQUIRED_FALSE = [
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_model_trained",
    "production_model_candidate_created", "deployable_model_artifact_written",
    "production_predictions_computed", "production_scores_computed",
    "live_portfolio_weights_computed", "research_portfolio_weights_computed",
    "order_instructions_created", "orders_created", "trades_created",
    "paper_trader_files_modified", "d_drive_read", "d_drive_written",
    "provider_api_called", "alpha_vantage_called", "fred_called", "stooq_called",
    "yahoo_called", "yfinance_called", "paid_vendor_api_called", "network_called",
    "data_faked", "metrics_faked",
]

_REQUIRED_CSVS = [
    "candidate_summary_card.csv",
    "model_candidate_spec.csv",
    "selected_strategy_spec.csv",
    "evidence_scorecard.csv",
    "risk_guardrails.csv",
    "known_failure_modes.csv",
    "preview_integration_contract.csv",
    "no_go_items.csv",
    "readiness_decision_table.csv",
]

_MANDATORY_GUARDRAILS = [
    "preview_only", "no_orders", "no_automation", "manual_review_required",
    "no_production_deployment", "no_live_model_enablement",
    "max_10_holdings_for_selected_strategy", "max_single_name_weight_10pct",
    "drawdown_warning_2024_severe", "overlapping_label_caveat",
    "survivorship_bias_caveat", "fresh_validation_before_production",
]


# ---------------------------------------------------------------------------
# Shared run fixture.
# ---------------------------------------------------------------------------
_RESULT = None


def _get_result():
    global _RESULT
    if _RESULT is None:
        _RESULT = r4b.run(generated_at="2026-06-20T00:00:00")
    return _RESULT


def _read_csv_rows(name):
    path = r4b._OUT_PATHS[name.replace(".csv", "")]
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


def test_inputs_only_reference_phase3z_and_phase4a():
    # The runner must read only Phase 3-Z and Phase 4-A artifacts.
    for other in ["phase3m", "phase3u", "phase3v", "phase3w", "phase3x", "phase3y"]:
        assert other not in _SRC_LOWER, "runner references disallowed phase: %s" % other


def test_allowed_recommendations_constant():
    assert r4b.ALLOWED_RECOMMENDATIONS == (
        "NONPROD_CANDIDATE_READY_FOR_PREVIEW_INTEGRATION",
        "NONPROD_CANDIDATE_PARTIAL_NEEDS_RISK_REPAIR",
        "NONPROD_CANDIDATE_BLOCKED_INPUTS",
        "NONPROD_CANDIDATE_BLOCKED_RISK",
    )


def test_decide_unit():
    # Inputs missing -> BLOCKED_INPUTS regardless of gates.
    assert r4b.decide(False, []) == r4b.DEC_BLOCKED_INPUTS
    risk_fail = [{"_kind": "risk", "passed": False}, {"_kind": "evidence", "passed": True}]
    assert r4b.decide(True, risk_fail) == r4b.DEC_BLOCKED_RISK
    evidence_fail = [{"_kind": "risk", "passed": True}, {"_kind": "evidence", "passed": False}]
    assert r4b.decide(True, evidence_fail) == r4b.DEC_PARTIAL
    all_pass = [{"_kind": "risk", "passed": True}, {"_kind": "evidence", "passed": True}]
    assert r4b.decide(True, all_pass) == r4b.DEC_READY


def test_next_phase_is_4c_for_all_decisions():
    for dec in r4b.ALLOWED_RECOMMENDATIONS:
        assert r4b.build_recommended_next_phase(dec)["phase"] == "4-C"


# ---------------------------------------------------------------------------
# Result / output tests.
# ---------------------------------------------------------------------------
def test_result_json_exists_and_phase():
    _get_result()
    assert os.path.isfile(r4b.RESULT_JSON)
    with open(r4b.RESULT_JSON, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["phase"] == "4-B"


def test_recommended_next_phase_is_4c():
    res = _get_result()
    assert res["recommended_next_phase"]["phase"] == "4-C"


def test_recommendation_is_allowed():
    res = _get_result()
    assert res["recommendation"]["recommendation"] in r4b.ALLOWED_RECOMMENDATIONS


def test_all_required_csv_outputs_exist():
    _get_result()
    for name in _REQUIRED_CSVS:
        path = os.path.join(r4b._B_DIR, name)
        assert os.path.isfile(path), "missing output: %s" % name


def test_output_files_git_safe_under_50mb():
    _get_result()
    limit = 50 * 1024 * 1024
    for name in _REQUIRED_CSVS:
        path = os.path.join(r4b._B_DIR, name)
        assert os.path.getsize(path) < limit, "%s too large" % name
    assert os.path.getsize(r4b.RESULT_JSON) < limit


def test_safety_flags_true_and_false():
    res = _get_result()
    for k in _REQUIRED_TRUE:
        assert res.get(k) is True, "expected %s True" % k
    for k in _REQUIRED_FALSE:
        assert res.get(k) is False, "expected %s False" % k


def test_nonproduction_packaged_no_production_candidate():
    res = _get_result()
    assert res["nonproduction_model_candidate_packaged"] is True
    assert res["production_model_candidate_created"] is False
    assert res["production_predictions_computed"] is False
    assert res["production_scores_computed"] is False
    assert res["live_portfolio_weights_computed"] is False
    assert res["deployable_model_artifact_written"] is False
    assert res["orders_created"] is False
    assert res["trades_created"] is False
    assert res["paper_trader_files_modified"] is False
    assert res["d_drive_read"] is False and res["d_drive_written"] is False
    assert res["network_called"] is False
    assert res["data_faked"] is False and res["metrics_faked"] is False


def test_all_mandatory_guardrails_present():
    _get_result()
    rows = _read_csv_rows("risk_guardrails")
    present = {r["guardrail"] for r in rows}
    for g in _MANDATORY_GUARDRAILS:
        assert g in present, "missing mandatory guardrail: %s" % g
    res = _get_result()
    assert set(res["mandatory_guardrails"]) == set(_MANDATORY_GUARDRAILS)


def test_known_failure_modes_recorded():
    _get_result()
    rows = _read_csv_rows("known_failure_modes")
    modes = {r["failure_mode"] for r in rows}
    for expected in ["weak_2024_year_drawdown", "cost_stress_failure_at_50bps",
                     "overlapping_126d_labels_optimistic_sharpe",
                     "survivorship_biased_universe", "no_production_model_artifact"]:
        assert expected in modes, "missing failure mode: %s" % expected


def test_preview_contract_allows_and_forbids():
    _get_result()
    rows = _read_csv_rows("preview_integration_contract")
    allowed = {r["contract_item"] for r in rows if r["allowed"] == "True"}
    forbidden = {r["contract_item"] for r in rows if r["allowed"] == "False"}
    assert "model_score" in allowed and "rank_percentile" in allowed
    assert "no_orders" in forbidden and "no_broker_execution" in forbidden
    assert "no_live_portfolio_weights" in forbidden


def test_selected_strategy_spec_disables_orders_and_automation():
    _get_result()
    rows = {r["spec_item"]: r["value"] for r in _read_csv_rows("selected_strategy_spec")}
    assert rows["orders_allowed"] == "False"
    assert rows["automation_allowed"] == "False"
    assert rows["paper_trader_preview_only"] == "True"
    assert rows["strategy_name"] == "top_10_equal_weight"


def test_evidence_not_faked_matches_upstream():
    """Evidence values must equal the upstream Phase 3-Z / 4-A numbers (not faked)."""
    _get_result()
    ev = {r["metric"]: r["value"] for r in _read_csv_rows("evidence_scorecard")}
    z = r4b._read_json(r4b.PHASE3Z_RESULT_JSON)
    bmh = z["best_model_horizon"]
    assert float(ev["mean_rank_ic"]) == pytest.approx(bmh["mean_daily_rank_ic"])
    assert int(ev["oos_row_count"]) == z["oos_score_panel_rows"]
    assert int(ev["leakage_failures"]) == z["leakage_checks_failed"]
    import csv
    with open(r4b.PHASE4A_SCOREBOARD_CSV, "r", encoding="utf-8", newline="") as fh:
        sb = [row for row in csv.DictReader(fh)
              if row["strategy_name"] == "top_10_equal_weight"
              and int(row["transaction_cost_bps"]) == 25][0]
    assert float(ev["annualized_return_at_25bps"]) == pytest.approx(float(sb["annualized_return"]))
    assert float(ev["sharpe_at_25bps"]) == pytest.approx(float(sb["sharpe"]))
    assert float(ev["max_drawdown_at_25bps"]) == pytest.approx(float(sb["max_drawdown"]))


def test_ready_only_if_all_gates_pass():
    """If the recommendation is READY, every readiness decision row must have passed."""
    res = _get_result()
    rows = _read_csv_rows("readiness_decision_table")
    if res["recommendation"]["recommendation"] == r4b.DEC_READY:
        for row in rows:
            if row["decision_item"] == "final_recommendation":
                assert row["passed"] == "True"
            else:
                assert row["passed"] == "True", "READY but gate failed: %s" % row["decision_item"]


def test_blocked_inputs_writes_no_fake_candidate(monkeypatch):
    """Missing inputs -> BLOCKED_INPUTS, no fabricated candidate evidence."""
    monkeypatch.setattr(r4b, "PHASE4A_RESULT_JSON", os.path.join(r4b._OUT_BASE, "_missing_4a.json"))
    res = r4b.run(generated_at="2026-06-20T00:00:00")
    assert res["recommendation"]["recommendation"] == r4b.DEC_BLOCKED_INPUTS
    assert res["nonproduction_model_candidate_packaged"] is False
    assert res["recommended_next_phase"]["phase"] == "4-C"
    # evidence scorecard must be empty (header only) - no faked rows.
    rows = _read_csv_rows("evidence_scorecard")
    assert rows == []
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
        fn()
        passed += 1
        print("PASS: %s" % fn.__name__)
    print("\n%d standalone tests passed" % passed)


if __name__ == "__main__":
    _run_all()
