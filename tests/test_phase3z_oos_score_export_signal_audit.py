"""Self-running tests for Phase 3-Z - OOS Score Export and Signal Audit.

Runs standalone (``python -B tests/test_phase3z_oos_score_export_signal_audit.py``) or under
pytest. The tests validate the already-produced Phase 3-Z outputs plus the pure decision logic;
they do NOT rebuild the heavy feature panel. Run the runner first if outputs are absent.

Guarantees checked: runner compiles; result JSON exists with phase "3-Z" and next phase "4-A";
all required CSV outputs exist and are Git-safe (< 50 MB each); the runner contacts no network
and no market-data vendor (no Yahoo / Stooq / Alpha Vantage / FRED / yfinance / paid API / DB /
deploy / D: write / production artifact); no fake OOS score rows; when READY the thresholds are
actually met; when BLOCKED the blocker is explicit and no fake rows are written.
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_RESEARCH = os.path.join(_REPO_ROOT, "research")
for _p in (_RESEARCH, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import run_phase3z_oos_score_export_signal_audit as z  # noqa: E402

_RUNNER_SRC_PATH = z.__file__
with open(_RUNNER_SRC_PATH, "r", encoding="utf-8") as _fh:
    _RUNNER_SRC = _fh.read()

_RESULT_JSON = z.RESULT_JSON
_O_DIR = z._Z_DIR

_MAX_FILE_BYTES = 50 * 1024 * 1024

# Forbidden import roots (assembled so they never appear here as real imports). urllib included:
# this phase needs NO network at all.
_FORBIDDEN_IMPORT_ROOTS = [
    "sklearn", "tensorflow", "torch", "keras", "lightgbm", "xgboost", "api_server",
    "yfinance", "requests", "httpx", "urllib", "http", "socket", "pandas_datareader",
    "fredapi", "finnhub", "alpha_vantage",
]
# Forbidden network usage / domain signatures (real call/domain forms, not bare vendor words).
_FORBIDDEN_USAGE = [
    "requests.get", "requests.post", "urlopen", "urllib.request", ".download(",
    "http.client", "socket.socket", "alphavantage.co", "finance.yahoo", "query1.finance",
    "query2.finance", "stooq.com", "stlouisfed", "finnhub.io", "polygon.io", "iexcloud.io",
]
_FORBIDDEN_DB = ["sqlalchemy", "psycopg2", "session.add", "session.commit", ".commit()",
                 "insert into", "update ", "create_engine", "engine.execute"]
_FORBIDDEN_DEPLOY_TRADE = ["create_order", "place_order", "submit_order", "deploy(",
                           "kubectl", "docker run", "predictor_use_model_v2", "alembic"]
_FORBIDDEN_ARTIFACT = ["pickle.dump", "joblib.dump", "torch.save", "model.save(",
                       ".onnx", ".h5", ".pt\"", ".pkl"]

_REQUIRED_TRUE = [
    "research_only", "labels_for_validation_only", "no_trading", "no_orders", "no_automation",
    "research_oos_scores_reconstructed",
]
_REQUIRED_FALSE = [
    "database_touched", "database_write_executed", "migration_executed", "deployment_executed",
    "model_v2_enabled", "production_model_trained", "production_model_candidate_created",
    "deployable_model_artifact_written", "production_predictions_computed",
    "production_scores_computed", "portfolio_weights_computed", "order_instructions_created",
    "d_drive_written", "provider_api_called", "alpha_vantage_called", "fred_called",
    "stooq_called", "yahoo_called", "yfinance_called", "paid_vendor_api_called",
    "network_called", "data_faked",
]
_ALLOWED_RECOMMENDATIONS = {
    "OOS_SCORE_EXPORT_READY_FOR_PORTFOLIO_SIMULATION",
    "OOS_SCORE_EXPORT_PARTIAL_NEEDS_REPAIR",
    "OOS_SCORE_EXPORT_BLOCKED_NO_SCORE_SOURCE",
    "OOS_SCORE_EXPORT_BLOCKED_INPUTS",
}
_REQUIRED_CSVS = ["panel", "summary", "decile", "yearly", "regime", "sector", "family",
                  "leakage", "readiness"]


# --------------------------------------------------------------------------- #
def _load_result():
    assert os.path.isfile(_RESULT_JSON), (
        "result JSON missing - run: python -B research/"
        "run_phase3z_oos_score_export_signal_audit.py")
    with open(_RESULT_JSON, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _read_csv_rows(path):
    import csv
    with open(path, "r", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- #
# Static source checks
# --------------------------------------------------------------------------- #
def test_runner_compiles():
    import py_compile
    py_compile.compile(_RUNNER_SRC_PATH, doraise=True)


def test_no_forbidden_imports():
    for root in _FORBIDDEN_IMPORT_ROOTS:
        pat = re.compile(r"^\s*(?:import|from)\s+%s(?:[.\s]|$)" % re.escape(root), re.MULTILINE)
        assert not pat.search(_RUNNER_SRC), "forbidden import root present: %s" % root


def test_no_forbidden_network_usage():
    low = _RUNNER_SRC.lower()
    for tok in _FORBIDDEN_USAGE:
        assert tok.lower() not in low, "forbidden network/domain token present: %s" % tok


def test_no_db_or_deploy_or_artifact_tokens():
    low = _RUNNER_SRC.lower()
    for tok in _FORBIDDEN_DB + _FORBIDDEN_DEPLOY_TRADE + _FORBIDDEN_ARTIFACT:
        assert tok.lower() not in low, "forbidden infra token present: %s" % tok


def test_no_literal_d_drive_write():
    # The price panel on D: is read via p3p.PRICE_CSV; this runner contains no literal D: path
    # and never opens a D: file for writing.
    assert "d:\\" not in _RUNNER_SRC.lower() and "d:/" not in _RUNNER_SRC.lower(), (
        "runner must not hardcode a D: path")


def test_allowed_recommendations_constant():
    assert set(z.ALLOWED_RECOMMENDATIONS) == _ALLOWED_RECOMMENDATIONS


def test_panel_columns_constant():
    assert z.PANEL_COLUMNS[:9] == [
        "date", "ticker", "horizon", "model_name", "oos_score", "cross_sectional_rank",
        "rank_percentile", "decile", "forward_return"]
    for col in ("label_available", "train_window_start", "train_window_end", "test_window_start",
                "test_window_end", "embargo_days", "feature_family_count", "source_status"):
        assert col in z.PANEL_COLUMNS


# --------------------------------------------------------------------------- #
# Pure decision-logic unit tests (no heavy run)
# --------------------------------------------------------------------------- #
def test_decide_rule_unit():
    # not inputs ok -> BLOCKED_INPUTS regardless
    assert z.decide(False, 999999, 999, 9999, 0, True) == z.REC_BLOCKED_INPUTS
    # inputs ok but zero rows -> BLOCKED_NO_SOURCE
    assert z.decide(True, 0, 0, 0, 0, False) == z.REC_BLOCKED_NO_SOURCE
    # all thresholds met -> READY
    assert z.decide(True, 100_000, 50, 500, 0, True) == z.REC_READY
    # a failed leakage check blocks READY -> PARTIAL
    assert z.decide(True, 100_000, 50, 500, 1, True) == z.REC_PARTIAL
    # no positive model/horizon -> PARTIAL
    assert z.decide(True, 100_000, 50, 500, 0, False) == z.REC_PARTIAL
    # below row threshold -> PARTIAL
    assert z.decide(True, 99_999, 50, 500, 0, True) == z.REC_PARTIAL


def test_next_phase_is_4a_for_all_recommendations():
    for rec in z.ALLOWED_RECOMMENDATIONS:
        assert z.build_recommended_next_phase(rec)["phase"] == "4-A"


def test_blocked_no_source_writes_no_fake_rows(tmp_path=None):
    import tempfile
    d = tempfile.mkdtemp(prefix="p3z_blocked_")
    rj = os.path.join(d, "result.json")
    res = z._finish_blocked(rj, d, z.REC_BLOCKED_NO_SOURCE, {"all_required_present": False},
                            "unit-test blocked path")
    assert res["recommendation"]["recommendation"] == z.REC_BLOCKED_NO_SOURCE
    assert res["recommended_next_phase"]["phase"] == "4-A"
    assert res["oos_score_panel_rows"] == 0
    # panel CSV exists with header only, no fabricated data rows
    panel_rows = _read_csv_rows(os.path.join(d, z._OUT_PATHS["panel"]))
    assert panel_rows == [], "blocked path must not write fake OOS score rows"
    # blocker is explicit
    assert "blocked" in res["blocked_reason"].lower() or res["blocked_reason"]
    for flag in _REQUIRED_FALSE:
        assert res[flag] is False, "blocked path flag must be False: %s" % flag


# --------------------------------------------------------------------------- #
# Produced-output checks (validate the real run)
# --------------------------------------------------------------------------- #
def test_result_json_exists_and_phase():
    r = _load_result()
    assert r["phase"] == "3-Z"
    assert r["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert r["recommended_next_phase"]["phase"] == "4-A"


def test_all_required_csv_outputs_exist():
    for key in _REQUIRED_CSVS:
        p = z._out(_O_DIR, key)
        assert os.path.isfile(p), "missing required output: %s" % p


def test_output_files_git_safe_under_50mb():
    for key in z._OUT_PATHS:
        p = z._out(_O_DIR, key)
        if os.path.isfile(p):
            assert os.path.getsize(p) < _MAX_FILE_BYTES, "%s exceeds 50 MB" % p
    if os.path.isfile(_RESULT_JSON):
        assert os.path.getsize(_RESULT_JSON) < _MAX_FILE_BYTES


def test_safety_flags():
    r = _load_result()
    for flag in _REQUIRED_TRUE:
        assert r.get(flag) is True, "expected True: %s" % flag
    for flag in _REQUIRED_FALSE:
        assert r.get(flag) is False, "expected False: %s" % flag


def test_panel_has_required_columns_and_no_fake_rows():
    r = _load_result()
    if r["recommendation"]["recommendation"] in (
            z.REC_BLOCKED_NO_SOURCE, z.REC_BLOCKED_INPUTS):
        return  # blocked path validated separately
    rows = _read_csv_rows(z._out(_O_DIR, "panel"))
    assert rows, "READY/PARTIAL panel must contain rows"
    assert list(rows[0].keys()) == z.PANEL_COLUMNS
    # every row is a reconstructed (not faked) score; label_available consistent with a finite
    # forward_return; no fabricated source tag.
    for row in rows[:5000]:
        assert row["source_status"] == z.SRC_RECONSTRUCTED
        if row["label_available"] == "1":
            float(row["forward_return"])  # parses as a real number
        float(row["oos_score"])
        assert 0 <= int(row["decile"]) <= 9
    assert len(rows) == r["oos_score_panel_rows"]


def test_ready_thresholds_actually_met():
    r = _load_result()
    if r["recommendation"]["recommendation"] != z.REC_READY:
        return
    assert r["oos_score_panel_rows"] >= z.READY_MIN_ROWS
    assert r["distinct_tickers"] >= z.READY_MIN_TICKERS
    assert r["distinct_dates"] >= z.READY_MIN_DATES
    assert r["leakage_checks_failed"] == 0
    # at least one model/horizon with positive mean IC and positive decile spread
    pos = [s for s in r["oos_score_summary"]
           if (s["mean_daily_rank_ic"] or 0) > 0
           and (s["top_minus_bottom_decile_spread"] or 0) > 0]
    assert pos, "READY requires a positive model/horizon"


def test_leakage_checks_present_and_consistent():
    r = _load_result()
    if r["recommendation"]["recommendation"] in (
            z.REC_BLOCKED_NO_SOURCE, z.REC_BLOCKED_INPUTS):
        return
    rows = _read_csv_rows(z._out(_O_DIR, "leakage"))
    assert rows, "leakage checks must be written"
    failed = sum(1 for x in rows if x["passed"] == "False")
    assert failed == r["leakage_checks_failed"]


def test_no_production_artifacts_on_disk():
    # The phase must not have written any serialized-estimator / production artifact next to its
    # outputs.
    for fn in os.listdir(_O_DIR):
        low = fn.lower()
        for ext in (".pkl", ".pickle", ".joblib", ".onnx", ".h5", ".pt", ".pb"):
            assert not low.endswith(ext), "unexpected model artifact written: %s" % fn


# --------------------------------------------------------------------------- #
# Standalone harness
# --------------------------------------------------------------------------- #
def _run_all():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    passed = failed = skipped = 0
    for name, fn in fns:
        try:
            fn()
            print("PASS", name)
            passed += 1
        except AssertionError as exc:
            print("FAIL", name, "-", exc)
            failed += 1
        except Exception as exc:  # noqa: BLE001
            print("ERROR", name, "-", repr(exc))
            failed += 1
    print("\n%d passed, %d failed, %d skipped, %d total"
          % (passed, failed, skipped, passed + failed + skipped))
    return failed


if __name__ == "__main__":
    raise SystemExit(1 if _run_all() else 0)
