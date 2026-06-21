"""Phase 5-D — tests for the external-data alpha enrichment inventory.

Verifies that the offline scanner compiles and runs, writes every required
artifact, classifies at least one discovered source, produces a valid Phase 5-E
implementation plan that compares against the Phase 5-C price-only baseline, and
holds the safety/honesty contract (preview-only, no orders/broker/automation, no
network/paid APIs, no fabricated data, no binary artifacts, no D: writes, no
Paper Trader / GCP changes).

Pure/offline: no database, no network. Runs under pytest and standalone.
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

_MODULE = "research.run_phase5d_external_data_alpha_enrichment"


def _runner():
    return importlib.import_module(_MODULE)


def _read_csv(path):
    with open(path, "r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def run_result():
    """Run the scanner once for the whole module; return (runner, report)."""
    runner = _runner()
    rc = runner.main()
    assert rc == 0
    with open(runner._REPORT_OUT, "r", encoding="utf-8") as fh:
        report = json.load(fh)
    return runner, report


# --------------------------------------------------------------------------- #
# Compiles / runs offline
# --------------------------------------------------------------------------- #
def test_runner_compiles():
    runner = _runner()
    assert hasattr(runner, "main") and callable(runner.main)


def test_runner_executes_offline(run_result):
    runner, report = run_result
    assert os.path.isfile(runner._REPORT_OUT)
    assert isinstance(report, dict)


def test_phase_is_5d(run_result):
    _, report = run_result
    assert report["phase"] == "5-D"


# --------------------------------------------------------------------------- #
# All required output files exist
# --------------------------------------------------------------------------- #
def test_all_required_output_files_exist(run_result):
    runner, _ = run_result
    for out in (
        runner._REPORT_OUT, runner._INVENTORY_OUT, runner._PIT_READINESS_OUT,
        runner._FEATURE_FAMILIES_OUT, runner._JOINABILITY_OUT, runner._MISSING_OUT,
        runner._PLAN_OUT,
    ):
        assert out.is_file(), f"missing required artifact: {out}"


# --------------------------------------------------------------------------- #
# Main JSON valid + safety flags
# --------------------------------------------------------------------------- #
def test_main_json_is_valid_and_safe(run_result):
    _, report = run_result
    assert report["preview_only"] is True
    assert report["orders_enabled"] is False
    assert report["automation_enabled"] is False
    assert report["broker_execution_enabled"] is False
    assert report["production_replacement"] is False


def test_report_explicit_provenance(run_result):
    _, report = run_result
    assert report["network_used"] is False
    assert report["paid_apis_used"] is False
    assert report["deployed"] is False
    assert report["binary_artifacts_created"] is False
    assert report["data_fabricated"] is False
    assert report["wrote_to_d_drive"] is False
    assert report["committed"] is False


def test_recommendation_vocabulary_is_valid(run_result):
    _, report = run_result
    allowed = {
        "PROCEED_TO_ENRICHED_ALPHA_PANEL",
        "PROCEED_TO_EXTERNAL_DATA_COLLECTION",
        "BLOCKED_NO_USABLE_EXTERNAL_DATA",
        "ERROR",
    }
    assert report["recommendation"] in allowed


# --------------------------------------------------------------------------- #
# Inventory CSV exists and has at least one discovered source
# --------------------------------------------------------------------------- #
def test_inventory_has_at_least_one_source(run_result):
    runner, report = run_result
    rows = _read_csv(runner._INVENTORY_OUT)
    assert len(rows) >= 1
    assert report["discovered_sources_count"] >= 1
    assert report["discovered_sources_count"] == len(rows)
    # Every classification field uses the declared vocabulary.
    for r in rows:
        assert r["family"] in runner.FAMILIES
        assert r["point_in_time_status"] in runner.PIT_STATUSES
        assert r["joinability"] in runner.JOINABILITIES
        assert r["leakage_risk"] in runner.LEAKAGE_RISKS
        assert r["recommended_use"] in runner.RECOMMENDED_USES


def test_point_in_time_readiness_csv_present(run_result):
    runner, _ = run_result
    rows = _read_csv(runner._PIT_READINESS_OUT)
    assert len(rows) >= 1
    assert {"point_in_time_status", "family", "path"} <= set(rows[0].keys())


def test_joinability_matrix_present(run_result):
    runner, _ = run_result
    rows = _read_csv(runner._JOINABILITY_OUT)
    assert len(rows) >= 1
    # The required risk dimensions must be explicit columns.
    for col in ("lookahead_risk", "stale_data_risk", "survivorship_risk",
                "missing_coverage_risk", "join_type", "join_keys"):
        assert col in rows[0].keys(), f"joinability matrix missing column: {col}"
    blob = " ".join(f"{r['join_type']} {r['join_keys']}".lower() for r in rows)
    for token in ("ticker", "date", "as-of"):
        assert token in blob, f"joinability matrix missing join concept: {token}"


def test_missing_alpha_sources_csv_present(run_result):
    runner, report = run_result
    rows = _read_csv(runner._MISSING_OUT)
    assert len(rows) >= 1
    names = {r["missing_source"] for r in rows}
    # The four truly-missing families must be surfaced, never fabricated.
    for expected in ("analyst_estimate_revisions", "news_sentiment",
                     "options_implied_volatility", "point_in_time_index_constituents"):
        assert expected in names, f"missing-source list omits {expected}"


def test_candidate_feature_families_present(run_result):
    runner, _ = run_result
    rows = _read_csv(runner._FEATURE_FAMILIES_OUT)
    fams = {r["feature_family"] for r in rows}
    # Must cover fundamentals + macro (ready) and the blocked external families.
    assert any("fundamental" in f for f in fams)
    assert any("macro" in f for f in fams)
    assert any("sentiment" in f for f in fams)
    # readiness vocabulary must be sane.
    allowed = {"ready_now", "partial", "caveated", "blocked_missing_data"}
    for r in rows:
        assert r["readiness"] in allowed, f"bad readiness: {r['readiness']}"


# --------------------------------------------------------------------------- #
# Phase 5-E plan is valid and compares against the Phase 5-C baseline
# --------------------------------------------------------------------------- #
def test_phase5e_plan_is_valid_json(run_result):
    runner, _ = run_result
    with open(runner._PLAN_OUT, "r", encoding="utf-8") as fh:
        plan = json.load(fh)
    assert plan["phase"] == "5-E"
    assert plan["preview_only"] is True
    assert plan["orders_enabled"] is False
    assert plan["automation_enabled"] is False
    assert plan["broker_execution_enabled"] is False
    assert plan["feature_families_first"]
    assert plan["exact_files_to_read"]
    assert plan["leakage_controls"]
    assert plan["expected_outputs"]
    assert plan["go_no_go_gates"]


def test_phase5e_plan_compares_to_phase5c_baseline(run_result):
    runner, _ = run_result
    with open(runner._PLAN_OUT, "r", encoding="utf-8") as fh:
        plan = json.load(fh)
    bc = plan["baseline_comparison"]
    blob = json.dumps(plan).lower()
    # The plan must explicitly compare the enriched model to the Phase 5-C
    # price-only baseline.
    assert "phase5c" in bc["baseline_metrics_source"].lower()
    assert "5-c" in blob or "phase5c" in blob or "price-only" in blob
    assert "baseline" in blob
    # A concrete improvement metric vs baseline must be specified.
    assert "delta" in json.dumps(bc).lower() or "improvement_metric" in bc


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
    lowered = src.lower()
    # D: appears only as a READ-ONLY input candidate, never opened for writing.
    assert 'open(r"d:' not in lowered
    assert "open('d:" not in lowered
    assert 'open("d:' not in lowered
    assert "_out_dir" in lowered and "research" in lowered


def test_no_paper_trader_or_gcp_or_deploy_references():
    runner = _runner()
    src = inspect.getsource(runner).lower()
    for forbidden in ("paper_trader", "gcloud ", "deploy(", "create_order(",
                      "place_order(", "execute_order(", "broker.", "scp ", "ssh "):
        assert forbidden not in src, f"runner references forbidden token: {forbidden}"


def test_no_order_broker_automation_implementation():
    runner = _runner()
    src = inspect.getsource(runner).lower()
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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
