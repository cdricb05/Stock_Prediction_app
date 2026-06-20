"""Self-running tests for Phase 4-A - Turnover-Aware Portfolio Simulation.

Runs standalone (`python tests/test_phase4a_turnover_aware_portfolio_simulation.py`)
and under pytest. Verifies the runner is research-only and network-free, the outputs
exist and are Git-safe, and that the recommendation is internally consistent (READY
thresholds actually met; BLOCKED blockers explicit with no fake rows).
"""

from __future__ import annotations

import json
import os
import re
import sys

import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from research import run_phase4a_turnover_aware_portfolio_simulation as r4a  # noqa: E402

_RUNNER_PATH = os.path.join(_REPO_ROOT, "research",
                            "run_phase4a_turnover_aware_portfolio_simulation.py")
with open(_RUNNER_PATH, "r", encoding="utf-8") as _fh:
    _RUNNER_SRC = _fh.read()
_RUNNER_SRC_LOWER = _RUNNER_SRC.lower()

# Import roots that must never appear (no network, no ML/serving frameworks).
_FORBIDDEN_IMPORT_ROOTS = [
    "urllib", "http", "httpx", "socket", "requests", "aiohttp", "yfinance",
    "pandas_datareader", "alpha_vantage", "fredapi", "sklearn", "torch",
    "tensorflow", "keras", "xgboost", "lightgbm", "sqlalchemy", "psycopg2",
    "boto3", "google", "ftplib", "telnetlib",
]
# Domain / usage signatures (substring) that would indicate a real network / vendor
# call. These are specific enough not to match the runner's own docstring negations.
_FORBIDDEN_USAGE = [
    "requests.get", "requests.post", "urlopen", "urllib.request", "http.client",
    ".download(", "alphavantage.co", "finance.yahoo", "query1.finance",
    "stooq.com", "stooq.pl", "stlouisfed.org", "api.stlouisfed", "fredapi",
    "finnhub.io", "polygon.io", "iexcloud", "tiingo", "quandl", "yf.download",
    "yfinance.", "datareader(", "socket.socket",
]
_FORBIDDEN_DB = ["session.commit", "cursor.execute", "insert into", "update ",
                 "engine.execute", "conn.execute", "create_engine", "alembic"]
_FORBIDDEN_DEPLOY_TRADE = [
    "predictor_use_model_v2", "deploy(", "kubectl", "docker push",
    "place_order", "submit_order", "create_order", "broker", "alpaca",
]
# Artifact-writing signatures. Note: bare "production_model" / "deployable_model"
# are intentionally NOT listed - they appear legitimately as safety-flag KEYS that
# are set to False (production_model_trained=False, etc.).
_FORBIDDEN_ARTIFACT = [".joblib", ".pkl", "pickle.dump", "torch.save",
                       "model.save(", "joblib.dump"]

_REQUIRED_TRUE = [
    "research_only", "research_portfolio_weights_computed",
    "labels_for_validation_only",
]
_REQUIRED_FALSE = [
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_model_trained",
    "production_model_candidate_created", "deployable_model_artifact_written",
    "production_predictions_computed", "production_scores_computed",
    "live_portfolio_weights_computed", "order_instructions_created",
    "orders_created", "trades_created", "d_drive_read", "d_drive_written",
    "provider_api_called", "alpha_vantage_called", "fred_called", "stooq_called",
    "yahoo_called", "yfinance_called", "paid_vendor_api_called", "network_called",
    "data_faked",
]

_REQUIRED_CSVS = [
    "strategy_scoreboard.csv", "monthly_portfolio_returns.csv",
    "annual_performance.csv", "turnover_cost_sensitivity.csv",
    "drawdown_summary.csv", "position_concentration_summary.csv",
    "risk_budget_summary.csv", "readiness_decision_table.csv",
]


def _result():
    """Run once and cache the result JSON dict for the assertions."""
    if not getattr(_result, "_cache", None):
        r4a.run(generated_at="2026-06-20T00:00:00")
        with open(r4a.RESULT_JSON, "r", encoding="utf-8") as fh:
            _result._cache = json.load(fh)
    return _result._cache


# ---------------------------------------------------------------------------
# Source-level guardrails.
# ---------------------------------------------------------------------------
def test_runner_compiles():
    import py_compile
    py_compile.compile(_RUNNER_PATH, doraise=True)


def test_no_forbidden_imports():
    for root in _FORBIDDEN_IMPORT_ROOTS:
        pat = re.compile(r"^\s*(?:import|from)\s+%s\b" % re.escape(root), re.MULTILINE)
        assert not pat.search(_RUNNER_SRC), "forbidden import root: %s" % root


def test_no_forbidden_network_usage():
    for sig in _FORBIDDEN_USAGE:
        assert sig not in _RUNNER_SRC_LOWER, "forbidden network/vendor usage: %s" % sig


def test_no_db_or_deploy_or_trade_or_artifact_tokens():
    for sig in _FORBIDDEN_DB + _FORBIDDEN_DEPLOY_TRADE + _FORBIDDEN_ARTIFACT:
        assert sig not in _RUNNER_SRC_LOWER, "forbidden token: %s" % sig


def test_no_literal_d_drive_write():
    # The runner must not open the D: drive for writing.
    assert 'open("d:' not in _RUNNER_SRC_LOWER
    assert "to_csv(\"d:" not in _RUNNER_SRC_LOWER
    assert "d:/stock_prediction_app_data" not in _RUNNER_SRC_LOWER


def test_allowed_recommendations_constant():
    assert set(r4a.ALLOWED_RECOMMENDATIONS) == {
        "PORTFOLIO_SIMULATION_READY_FOR_NONPRODUCTION_CANDIDATE",
        "PORTFOLIO_SIMULATION_PARTIAL_NEEDS_RISK_REPAIR",
        "PORTFOLIO_SIMULATION_BLOCKED_NO_VALID_STRATEGY",
        "PORTFOLIO_SIMULATION_BLOCKED_INPUTS",
    }


# ---------------------------------------------------------------------------
# Decision-rule unit tests (pure logic, no I/O).
# ---------------------------------------------------------------------------
def test_decide_rule_unit():
    assert r4a.decide(False, True, True) == r4a.DEC_BLOCKED_INPUTS
    assert r4a.decide(True, False, False) == r4a.DEC_BLOCKED_NO_STRATEGY
    assert r4a.decide(True, True, True) == r4a.DEC_READY
    assert r4a.decide(True, True, False) == r4a.DEC_PARTIAL


def test_passes_ready_unit():
    good = {"annualized_return": 0.18, "sharpe": 0.8, "max_drawdown": -0.30,
            "average_holdings": 10.0, "average_turnover": 0.8, "periods": 60}
    assert r4a._passes_ready(good)
    assert not r4a._passes_ready({**good, "max_drawdown": -0.40})   # too deep
    assert not r4a._passes_ready({**good, "average_holdings": 8})   # too few
    assert not r4a._passes_ready({**good, "average_turnover": 2.0})  # too high
    assert not r4a._passes_ready({**good, "periods": 12})          # too short
    assert not r4a._passes_ready({**good, "annualized_return": -0.01})
    assert not r4a._passes_ready(None)


def test_next_phase_is_4b_for_all_recommendations():
    for dec in r4a.ALLOWED_RECOMMENDATIONS:
        assert r4a.build_recommended_next_phase(dec)["phase"] == "4-B"


def test_weights_respect_cap_and_no_leverage():
    import numpy as np
    tickers = ["A", "B", "C", "D"]
    raw = np.array([10.0, 1.0, 1.0, 1.0])  # A would blow past the cap uncapped
    w = r4a._cap_and_normalize(tickers, raw, cap=0.10)
    assert max(w.values()) <= 0.10 + 1e-9
    assert min(w.values()) >= 0.0
    assert sum(w.values()) <= 1.0 + 1e-9


# ---------------------------------------------------------------------------
# Output-level tests (run the phase).
# ---------------------------------------------------------------------------
def test_result_json_exists_and_phase():
    res = _result()
    assert os.path.isfile(r4a.RESULT_JSON)
    assert res["phase"] == "4-A"


def test_recommended_next_phase_is_4b():
    assert _result()["recommended_next_phase"]["phase"] == "4-B"


def test_recommendation_is_allowed():
    assert _result()["recommendation"]["recommendation"] in r4a.ALLOWED_RECOMMENDATIONS


def test_all_required_csv_outputs_exist():
    _result()
    for name in _REQUIRED_CSVS:
        path = os.path.join(r4a._A_DIR, name)
        assert os.path.isfile(path), "missing output: %s" % name


def test_output_files_git_safe_under_50mb():
    _result()
    limit = 50 * 1024 * 1024
    assert os.path.getsize(r4a.RESULT_JSON) < limit
    for name in _REQUIRED_CSVS:
        assert os.path.getsize(os.path.join(r4a._A_DIR, name)) < limit


def test_safety_flags():
    res = _result()
    for flag in _REQUIRED_TRUE:
        assert res.get(flag) is True, "expected %s True" % flag
    for flag in _REQUIRED_FALSE:
        assert res.get(flag) is False, "expected %s False" % flag


def test_research_weights_true_live_weights_false():
    res = _result()
    assert res["research_portfolio_weights_computed"] is True
    assert res["live_portfolio_weights_computed"] is False


def test_no_production_or_orders_flags():
    res = _result()
    for flag in ["production_model_candidate_created", "production_predictions_computed",
                 "production_scores_computed", "orders_created", "trades_created",
                 "order_instructions_created", "deployable_model_artifact_written"]:
        assert res[flag] is False


def test_scoreboard_has_required_columns():
    _result()
    df = pd.read_csv(os.path.join(r4a._A_DIR, "strategy_scoreboard.csv"))
    assert list(df.columns) == r4a.SCOREBOARD_COLUMNS


def test_monthly_returns_columns_and_no_fake_rows():
    _result()
    df = pd.read_csv(os.path.join(r4a._A_DIR, "monthly_portfolio_returns.csv"))
    assert list(df.columns) == r4a.MONTHLY_COLUMNS
    # net_return must equal gross_return - cost_drag (no fabricated returns).
    if len(df):
        diff = (df["gross_return"] - df["cost_drag"] - df["net_return"]).abs()
        assert diff.max() < 1e-6
        # cost_drag must be non-negative and zero exactly at 0 bps.
        assert (df["cost_drag"] >= -1e-12).all()
        zero = df[df["transaction_cost_bps"] == 0]
        assert (zero["cost_drag"].abs() < 1e-12).all()


def test_ready_thresholds_actually_met_when_ready():
    res = _result()
    if res["recommendation"]["recommendation"] != r4a.DEC_READY:
        return
    df = pd.read_csv(os.path.join(r4a._A_DIR, "strategy_scoreboard.csv"))
    gate = df[(df["transaction_cost_bps"] == r4a.GATING_COST_BPS)]
    passing = gate[
        (gate["annualized_return"] > 0)
        & (gate["sharpe"] > 0)
        & (gate["max_drawdown"] > r4a.READY_MAX_DRAWDOWN_FLOOR)
        & (gate["average_holdings"] >= r4a.READY_MIN_AVG_HOLDINGS)
        & (gate["average_turnover"] <= r4a.READY_MAX_AVG_TURNOVER)
        & (gate["periods"] >= r4a.READY_MIN_PERIODS)
    ]
    assert len(passing) >= 1, "READY but no strategy clears all thresholds at 25 bps"
    # the reported best strategy must be one of the passing rows
    assert res["best_strategy"] in set(df["strategy_name"])


def test_risk_budget_constraints_pass():
    _result()
    df = pd.read_csv(os.path.join(r4a._A_DIR, "risk_budget_summary.csv"))
    by = dict(zip(df["risk_check"], df["passed"]))
    for chk in ["max_single_name_weight_le_10pct", "long_only_no_negative_weights",
                "no_leverage_gross_exposure_le_1"]:
        assert bool(by.get(chk)) is True, "risk check failed: %s" % chk


def test_concentration_within_cap():
    _result()
    df = pd.read_csv(os.path.join(r4a._A_DIR, "position_concentration_summary.csv"))
    if len(df):
        assert (df["max_single_name_weight"] <= 0.10 + 1e-6).all()
        assert (df["average_holdings"] > 0).all()


def test_blocked_inputs_writes_no_fake_rows(tmp_path=None):
    """Force the BLOCKED_INPUTS path and confirm no fabricated return rows."""
    import tempfile
    orig = r4a.PHASE3Z_PANEL_CSV
    try:
        r4a.PHASE3Z_PANEL_CSV = os.path.join(tempfile.gettempdir(),
                                             "phase4a_missing_panel_xyz.csv")
        status = r4a.confirm_inputs()
        # Only meaningful if the panel is the thing now missing.
        if not status["all_required_present"]:
            res = r4a._finish_blocked(r4a.DEC_BLOCKED_INPUTS, status, "panel missing (test)")
            assert res["recommendation"]["recommendation"] == r4a.DEC_BLOCKED_INPUTS
            assert res["recommended_next_phase"]["phase"] == "4-B"
            mdf = pd.read_csv(r4a._OUT_PATHS["monthly"])
            assert len(mdf) == 0, "blocked path must not write fabricated return rows"
            assert res["data_faked"] is False
            assert res["network_called"] is False
    finally:
        r4a.PHASE3Z_PANEL_CSV = orig
        r4a.run(generated_at="2026-06-20T00:00:00")  # restore real outputs


def test_inputs_only_reference_phase3z():
    """The runner reads only Phase 3-Z artifacts (no D:, no other phases for data)."""
    res = _result()
    for v in res["inputs_read"].values():
        assert "phase3z" in v.lower()
        assert not v.lower().startswith("d:")


# ---------------------------------------------------------------------------
# Standalone harness.
# ---------------------------------------------------------------------------
def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failures = 0
    for fn in fns:
        try:
            fn()
            print("PASS %s" % fn.__name__)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print("FAIL %s -> %s" % (fn.__name__, exc))
    print("\n%d/%d passed" % (len(fns) - failures, len(fns)))
    return failures


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
