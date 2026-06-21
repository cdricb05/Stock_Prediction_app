"""Phase 5-C — tests for the cross-sectional alpha research harness.

Verifies that the offline harness compiles and runs, writes every required
artifact, optimizes the correct primary label, evaluates the required models,
emits the required validation gates and leakage checks, and holds the safety
contract (preview-only, no orders/broker/automation, no network/paid APIs, no
binary model artifacts, no D: writes, no Paper Trader / GCP changes).

Pure/offline: no database, no network. Runs under pytest and standalone. The
runner reads local price history (D: read-only input or a repo fallback); when
no local data is available it degrades to a BLOCKED report — data-dependent
assertions are skipped in that case, the safety contract is always checked.
"""
from __future__ import annotations

import csv
import importlib
import inspect
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pytest  # noqa: E402

_MODULE = "research.run_phase5c_cross_sectional_alpha_research"


def _runner():
    return importlib.import_module(_MODULE)


def _read_csv(path):
    with open(path, "r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def run_result():
    """Run the harness once for the whole module; return (runner, report)."""
    runner = _runner()
    rc = runner.main()
    assert rc == 0
    with open(runner._REPORT_OUT, "r", encoding="utf-8") as fh:
        report = json.load(fh)
    return runner, report


def _is_blocked(report) -> bool:
    return report.get("recommendation") == "BLOCKED_INSUFFICIENT_DATA" \
        and "model_scoreboard" not in report


# --------------------------------------------------------------------------- #
# Compiles / runs offline
# --------------------------------------------------------------------------- #
def test_runner_compiles():
    runner = _runner()
    assert hasattr(runner, "main") and callable(runner.main)


def test_runner_executes_offline(run_result):
    runner, report = run_result
    assert os.path.isfile(runner._REPORT_OUT)
    assert isinstance(report, dict) and report.get("phase") == "5-C"


# --------------------------------------------------------------------------- #
# All required output files exist
# --------------------------------------------------------------------------- #
def test_all_required_output_files_exist(run_result):
    runner, _ = run_result
    for out in (
        runner._REPORT_OUT, runner._PANEL_SAMPLE_OUT, runner._OOS_SCORES_OUT,
        runner._SCOREBOARD_OUT, runner._IC_BY_YEAR_OUT, runner._DECILE_SPREAD_OUT,
        runner._REGIME_OUT, runner._TURNOVER_OUT, runner._GATE_MATRIX_OUT,
    ):
        assert out.is_file(), f"missing required artifact: {out}"


# --------------------------------------------------------------------------- #
# Main JSON valid + primary label + safety flags (always hold)
# --------------------------------------------------------------------------- #
def test_main_json_is_valid_and_safe(run_result):
    _, report = run_result
    assert report["preview_only"] is True
    assert report["orders_enabled"] is False
    assert report["automation_enabled"] is False
    assert report["broker_execution_enabled"] is False
    assert report["production_replacement"] is False


def test_primary_label_is_forward_excess_return_20d_vs_spy(run_result):
    runner, report = run_result
    assert runner.PRIMARY_LABEL == "forward_excess_return_20d_vs_spy"
    if _is_blocked(report):
        pytest.skip("no local data available; primary label asserted on the module constant")
    assert report["primary_label"] == "forward_excess_return_20d_vs_spy"


# --------------------------------------------------------------------------- #
# Models evaluated
# --------------------------------------------------------------------------- #
def test_model_scoreboard_includes_required_models(run_result):
    _, report = run_result
    if _is_blocked(report):
        pytest.skip("no local data available; scoreboard not produced")
    sb = report["model_scoreboard"]
    assert "cross_sectional_composite_zscore" in sb
    assert "ridge_cross_sectional_return_model" in sb


def test_scoreboard_csv_lists_required_models(run_result):
    runner, report = run_result
    if _is_blocked(report):
        pytest.skip("no local data available; scoreboard csv not produced")
    models = {r["model"] for r in _read_csv(runner._SCOREBOARD_OUT)}
    assert "cross_sectional_composite_zscore" in models
    assert "ridge_cross_sectional_return_model" in models


# --------------------------------------------------------------------------- #
# Validation gates cover the required concepts
# --------------------------------------------------------------------------- #
def test_validation_gates_cover_required_checks(run_result):
    runner, report = run_result
    if _is_blocked(report):
        pytest.skip("no local data available; gate matrix not produced")
    gate_names = {r["gate_name"] for r in _read_csv(runner._GATE_MATRIX_OUT)}
    required = {
        "no_leakage_gate", "mean_rank_ic_gate", "positive_decile_spread_gate",
        "worst_year_gate", "turnover_gate", "transaction_cost_survival_gate",
        "drawdown_gate", "regime_robustness_gate", "liquidity_gate",
        "sufficient_observations_gate", "no_fake_data_gate",
        "preview_only_gate", "no_orders_gate", "no_broker_execution_gate",
        "no_automation_gate",
    }
    missing = required - gate_names
    assert not missing, f"gate matrix missing required gates: {sorted(missing)}"

    # The gate matrix must reference rank IC, decile spread, turnover, transaction
    # cost, drawdown, leakage, and regime concepts somewhere.
    blob = " ".join(
        f"{r['gate_name']} {r['metric']} {r['note']}".lower()
        for r in _read_csv(runner._GATE_MATRIX_OUT))
    for token in ("ic", "decile", "turnover", "cost", "drawdown",
                  "leakage", "regime"):
        assert token in blob, f"gate matrix missing concept: {token}"


def test_gate_status_values_are_valid(run_result):
    runner, report = run_result
    if _is_blocked(report):
        pytest.skip("no local data available; gate matrix not produced")
    allowed = {"PASS", "FAIL", "WARNING", "NOT_EVALUABLE"}
    for r in _read_csv(runner._GATE_MATRIX_OUT):
        assert r["status"] in allowed, f"bad gate status: {r['status']}"


# --------------------------------------------------------------------------- #
# Leakage checks are present (structural + computed placebo)
# --------------------------------------------------------------------------- #
def test_leakage_checks_present(run_result):
    _, report = run_result
    if _is_blocked(report):
        pytest.skip("no local data available; leakage checks not produced")
    lc = report["leakage_checks"]
    for key in ("features_use_future_data", "labels_use_past_data",
                "same_date_label_in_features", "embargo_respected",
                "placebo_mean_ic_baseline"):
        assert key in lc, f"leakage_checks missing {key}"
    # Walk-forward embargo of at least 20 trading days must be respected.
    assert report["walk_forward"]["embargo_days"] >= 20
    assert report["walk_forward"]["embargo_respected"] is True


def test_recommendation_vocabulary_is_valid(run_result):
    _, report = run_result
    allowed = {
        "PROCEED_TO_DEPLOYABLE_TICKER_SCORER",
        "PROCEED_TO_EXTERNAL_DATA_ALPHA_ENRICHMENT",
        "RESEARCH_EDGE_WEAK_DO_NOT_DEPLOY",
        "BLOCKED_INSUFFICIENT_DATA",
        "ERROR",
    }
    assert report["recommendation"] in allowed


# --------------------------------------------------------------------------- #
# No network / paid-API dependency
# --------------------------------------------------------------------------- #
def test_no_network_or_paid_api_dependency():
    runner = _runner()
    import_lines = [
        ln.strip() for ln in inspect.getsource(runner).splitlines()
        if ln.strip().startswith(("import ", "from "))
    ]
    joined = " ".join(import_lines).lower()
    for forbidden in ("requests", "urllib", "http.client", "httpx", "yfinance",
                      "alpha_vantage", "alphavantage", "socket", "boto3"):
        assert forbidden not in joined, f"runner imports network/paid token: {forbidden}"


# --------------------------------------------------------------------------- #
# No writes to D: ; no Paper Trader / GCP / deploy / order code
# --------------------------------------------------------------------------- #
def test_no_d_drive_writes_in_source():
    runner = _runner()
    src = inspect.getsource(runner)
    # D: appears only as a READ-ONLY input candidate, never opened for writing.
    lowered = src.lower()
    assert 'open(r"d:' not in lowered
    assert "open('d:" not in lowered
    assert 'open("d:' not in lowered
    # Every write goes through _write_json/_write_csv into research/output only.
    assert "_OUT_DIR" in src and "research" in src


def test_no_paper_trader_or_gcp_or_deploy_references():
    runner = _runner()
    src = inspect.getsource(runner).lower()
    for forbidden in ("paper_trader", "gcloud ", "deploy(", "create_order(",
                      "place_order(", "execute_order(", "broker.", "scp ", "ssh "):
        assert forbidden not in src, f"runner references forbidden token: {forbidden}"


def test_no_order_broker_automation_implementation():
    runner = _runner()
    src = inspect.getsource(runner).lower()
    # No scheduler / automation loops, no order placement primitives.
    for forbidden in ("schedule.every", "crontab", "while true", "place order",
                      "submit_order", "broker_api"):
        assert forbidden not in src, f"runner implements forbidden behavior: {forbidden}"


# --------------------------------------------------------------------------- #
# No binary model artifacts created anywhere in the repo
# --------------------------------------------------------------------------- #
def test_no_binary_model_artifacts_present():
    bad_ext = (".pkl", ".joblib", ".onnx", ".pt", ".h5", ".keras")
    offenders = []
    for root, _dirs, files in os.walk(_REPO_ROOT):
        if ".git" in root:
            continue
        for fn in files:
            if fn.lower().endswith(bad_ext):
                offenders.append(os.path.join(root, fn))
    assert not offenders, f"binary model artifacts found: {offenders}"


# --------------------------------------------------------------------------- #
# Report safety mirror fields (explicit, like Phase 5-A/5-B)
# --------------------------------------------------------------------------- #
def test_report_explicit_safety_and_provenance(run_result):
    _, report = run_result
    assert report["network_used"] is False
    assert report["paid_apis_used"] is False
    assert report["deployed"] is False
    assert report["binary_artifacts_created"] is False
    assert report["committed"] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
