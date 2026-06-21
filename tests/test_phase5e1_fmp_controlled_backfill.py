"""Phase 5-E1 - tests for the controlled FMP backfill system.

Verifies the runner compiles and runs fully in DRY-RUN mode with NO FMP_API_KEY
and NO network, writes every required committed artifact, exposes the correct
phase/provider/mode + safety flags, keeps the API key and raw paid payloads out of
every committed artifact, git-ignores the local raw/normalized paid-data
directories, models endpoint entitlement (needs_live_verification handling), and
emits a Phase 5-E2 plan that references the Phase 5-C price-only baseline. Two
monkeypatched live tests (no real key, no real network) exercise the success and
all-blocked paths.

Pure/offline: no network, no live key required. Runs under pytest.
"""
from __future__ import annotations

import csv
import importlib
import inspect
import json
import os
import shutil
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pytest  # noqa: E402

_RUNNER_MODULE = "research.run_phase5e1_fmp_controlled_backfill"
_CLIENT_MODULE = "research.providers.fmp_client"


def _runner():
    return importlib.import_module(_RUNNER_MODULE)


def _client():
    return importlib.import_module(_CLIENT_MODULE)


def _read_csv(path):
    with open(path, "r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def monkeypatch_session():
    """Module-scoped env guard: ensure FMP_API_KEY is absent during the dry-run."""
    saved = os.environ.pop("FMP_API_KEY", None)
    try:
        yield
    finally:
        if saved is not None:
            os.environ["FMP_API_KEY"] = saved


@pytest.fixture(scope="module")
def run_result(monkeypatch_session):
    """Run the backfill once in dry-run with NO key; return (runner, report)."""
    runner = _runner()
    report = runner.run(live=False, universe=runner.DEFAULT_UNIVERSE, verbose=False)
    with open(runner._REPORT_OUT, "r", encoding="utf-8") as fh:
        on_disk = json.load(fh)
    assert on_disk["phase"] == report["phase"]
    return runner, report


@pytest.fixture(scope="module", autouse=True)
def _commit_safe_paid_data_cleanup():
    """Remove any local paid-data dirs the monkeypatched live tests create.

    research/data/fmp/{raw,normalized} are git-ignored, but the tests still tidy
    them so the working tree is left clean, then re-emit the canonical dry-run
    artifacts.
    """
    yield
    runner = _runner()
    for d in (runner._RAW_DIR, runner._NORM_DIR):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
    runner.run(live=False, universe=runner.DEFAULT_UNIVERSE, verbose=False)


# --------------------------------------------------------------------------- #
# Compiles / dry-run executes with no key, no network
# --------------------------------------------------------------------------- #
def test_runner_compiles():
    runner = _runner()
    assert hasattr(runner, "main") and callable(runner.main)
    assert hasattr(runner, "run") and callable(runner.run)


def test_runner_executes_dry_run(run_result):
    runner, report = run_result
    assert os.path.isfile(runner._REPORT_OUT)
    assert isinstance(report, dict)
    assert report["mode"] == "dry_run"
    assert report["live_request_count"] == 0
    assert report["raw_files_written_count"] == 0
    assert report["normalized_files_written_count"] == 0
    assert report["network_used"] is False


def test_dry_run_requires_no_key(monkeypatch_session):
    client = _client()
    assert client.has_api_key() is False


def test_phase_provider_and_mode(run_result):
    _, report = run_result
    assert report["phase"] == "5-E1"
    assert report["provider"] == "Financial Modeling Prep"
    assert report["mode"] == "dry_run"


# --------------------------------------------------------------------------- #
# All required committed output artifacts exist
# --------------------------------------------------------------------------- #
def test_all_required_output_artifacts_exist(run_result):
    runner, _ = run_result
    for out in (runner._REPORT_OUT, runner._REQUEST_PLAN_OUT, runner._ACCESS_PLAN_OUT,
                runner._STORAGE_MANIFEST_OUT, runner._PROGRESS_TEMPLATE_OUT,
                runner._QUALITY_REPORT_OUT, runner._SECRET_AUDIT_OUT, runner._PANEL_PLAN_OUT):
        assert out.is_file(), f"missing required artifact: {out}"


def test_recommendation_dry_run_ready_for_sample(run_result):
    _, report = run_result
    assert report["recommendation"] == "READY_FOR_LIVE_CONTROLLED_SAMPLE"
    assert report["recommendation"] in report["recommendation_allowed_values"]


def test_recommendation_vocabulary(run_result):
    _, report = run_result
    allowed = {"READY_FOR_LIVE_CONTROLLED_SAMPLE", "READY_FOR_PHASE5E2_ENRICHED_PANEL",
               "NEEDS_ENDPOINT_ENTITLEMENT_REVIEW", "BLOCKED_MISSING_FMP_KEY", "ERROR"}
    assert set(report["recommendation_allowed_values"]) == allowed


# --------------------------------------------------------------------------- #
# Safety flags correct
# --------------------------------------------------------------------------- #
def test_main_json_safety_flags(run_result):
    _, report = run_result
    assert report["preview_only"] is True
    assert report["orders_enabled"] is False
    assert report["automation_enabled"] is False
    assert report["broker_execution_enabled"] is False
    assert report["production_replacement"] is False
    assert report["api_key_logged"] is False
    assert report["paid_data_gitignored"] is True
    assert report["full_sp500_backfill_auto"] is False
    assert report["committed"] is False
    assert report["wrote_to_d_drive"] is False


# --------------------------------------------------------------------------- #
# raw/ and normalized/ paid-data directories are gitignored
# --------------------------------------------------------------------------- #
def test_paid_data_directories_are_gitignored(run_result):
    runner, report = run_result
    assert runner._GITIGNORE.is_file(), "research/data/fmp/.gitignore must exist"
    text = runner._GITIGNORE.read_text(encoding="utf-8")
    assert "raw/" in text
    assert "normalized/" in text
    assert runner._paid_data_gitignored() is True
    # The storage dirs must live under the git-ignored paid-data tree, not output.
    assert report["storage_raw_dir"] == "research/data/fmp/raw"
    assert report["storage_normalized_dir"] == "research/data/fmp/normalized"


# --------------------------------------------------------------------------- #
# No raw paid data / no key marker / no key-looking values in committed artifacts
# --------------------------------------------------------------------------- #
def test_no_apikey_marker_in_committed_artifacts(run_result):
    runner, _ = run_result
    marker = "api" + "key="
    out_dir = runner._OUT_DIR
    bad = []
    for root, _dirs, files in os.walk(out_dir):
        for fn in files:
            if not fn.endswith((".csv", ".json")):
                continue
            p = os.path.join(root, fn)
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            if marker in text.lower():
                bad.append(p)
    assert not bad, f"key query parameter marker found in committed artifacts: {bad}"


def test_redacted_urls_use_placeholder(run_result):
    runner, _ = run_result
    rows = _read_csv(runner._REQUEST_PLAN_OUT)
    assert rows
    for r in rows:
        assert "<API_KEY_REDACTED>" in r["request_url_redacted"]
        assert "apikey=" not in r["request_url_redacted"].lower()


def test_committed_output_has_no_raw_payload_dirs(run_result):
    # The committed output dir must hold only summarized artifacts: no raw/ or
    # normalized/ payload subdirectories should ever appear under it.
    runner, _ = run_result
    out_dir = runner._OUT_DIR
    for sub in ("raw", "normalized"):
        assert not (out_dir / sub).exists(), f"committed output must not contain {sub}/ payloads"


def test_no_key_looking_value_in_committed_artifacts(run_result):
    # Defense in depth: no long opaque token that resembles a leaked API key
    # should appear in committed artifacts. (FMP keys are ~32 hex chars.)
    import re
    runner, _ = run_result
    keyish = re.compile(r"\b[A-Za-z0-9]{28,}\b")
    offenders = []
    for out in (runner._REQUEST_PLAN_OUT, runner._ACCESS_PLAN_OUT, runner._PROGRESS_TEMPLATE_OUT,
                runner._STORAGE_MANIFEST_OUT, runner._QUALITY_REPORT_OUT, runner._SECRET_AUDIT_OUT,
                runner._PANEL_PLAN_OUT, runner._REPORT_OUT):
        text = out.read_text(encoding="utf-8", errors="replace")
        for m in keyish.findall(text):
            # Allow the redaction placeholder token and known long identifiers.
            if m in ("API_KEY_REDACTED",):
                continue
            offenders.append((str(out), m))
    assert not offenders, f"suspicious key-looking tokens in artifacts: {offenders}"


# --------------------------------------------------------------------------- #
# Endpoint access plan models entitlement (needs_live_verification handling)
# --------------------------------------------------------------------------- #
def test_endpoint_access_plan_handles_needs_live_verification(run_result):
    runner, _ = run_result
    rows = _read_csv(runner._ACCESS_PLAN_OUT)
    assert rows
    statuses = {r["endpoint_status"] for r in rows}
    assert "stable_confirmed" in statuses
    assert "needs_live_verification" in statuses
    # Every needs_live_verification row says it must be probed before reliance.
    for r in rows:
        if r["endpoint_status"] == "needs_live_verification":
            assert "probe" in r["access_assumption"].lower()
        # In dry-run nothing is verified yet.
        assert r["live_access_result"] == "planned"
    # Required-for-E2 families are flagged.
    fams_required = {r["alpha_family"] for r in rows if r["required_for_phase5e2"] in ("True", "true")}
    assert "fundamentals" in fams_required


def test_required_endpoint_families_planned(run_result):
    runner, _ = run_result
    rows = _read_csv(runner._REQUEST_PLAN_OUT)
    names = {r["endpoint_name"] for r in rows}
    required = {
        "company_profile", "income_statement_quarterly", "balance_sheet_statement_quarterly",
        "cash_flow_statement_quarterly", "key_metrics_quarterly", "ratios_quarterly",
        "earnings_calendar", "earnings_surprises", "analyst_estimates",
        "analyst_recommendations", "analyst_price_targets",
    }
    assert required <= names, f"request plan missing endpoints: {required - names}"


# --------------------------------------------------------------------------- #
# Progress template includes success/empty/error tracking fields
# --------------------------------------------------------------------------- #
def test_progress_template_has_status_and_tracking_fields(run_result):
    runner, _ = run_result
    rows = _read_csv(runner._PROGRESS_TEMPLATE_OUT)
    assert rows
    required_cols = {"endpoint_name", "ticker", "request_url_redacted", "status", "row_count",
                     "raw_file_path", "normalized_file_path", "http_status",
                     "error_message_sanitized", "collected_at_utc"}
    assert required_cols <= set(rows[0].keys())
    # Dry-run rows are all 'planned' (resumable template).
    for r in rows:
        assert r["status"] == "planned"


# --------------------------------------------------------------------------- #
# Phase 5-E2 plan references the Phase 5-C baseline + correct next phase
# --------------------------------------------------------------------------- #
def test_phase5e2_plan_references_phase5c_baseline(run_result):
    runner, _ = run_result
    with open(runner._PANEL_PLAN_OUT, "r", encoding="utf-8") as fh:
        plan = json.load(fh)
    assert plan["phase"] == "5-E2"
    blob = json.dumps(plan)
    assert "5-C" in blob, "Phase 5-E2 plan must reference the Phase 5-C baseline"
    assert "forward_excess_return_20d_vs_spy" in blob
    # Feature families and incremental-edge gate present.
    fams = json.dumps(plan["feature_families_to_build"])
    for fam in ("fundamentals_quality_value_growth", "earnings_surprise_event",
                "valuation_ratios"):
        assert fam in fams
    assert "incremental_edge_gates_vs_phase5c" in plan
    assert plan["next_phase"].startswith("Phase 5-E2")
    assert plan["preview_only"] is True
    assert plan["orders_enabled"] is False


# --------------------------------------------------------------------------- #
# Secret-safety audit present + passes
# --------------------------------------------------------------------------- #
def test_secret_safety_audit_passes(run_result):
    runner, _ = run_result
    rows = _read_csv(runner._SECRET_AUDIT_OUT)
    by_check = {r["check"]: r for r in rows}
    assert by_check["api_key_logged"]["value"] in ("False", "false")
    assert by_check["no_key_in_output_files"]["passed"] in ("True", "true")
    assert by_check["paid_data_gitignored"]["passed"] in ("True", "true")
    assert by_check["dry_run_does_no_network"]["passed"] in ("True", "true")


# --------------------------------------------------------------------------- #
# Source hygiene: no hardcoded key, no AlphaVantage/other paid APIs, no D: write,
# no Paper Trader / GCP / deploy / order / automation references
# --------------------------------------------------------------------------- #
def test_no_hardcoded_key_in_source():
    runner = _runner()
    src = inspect.getsource(runner)
    assert "FMP_API_KEY" in src
    assert "apikey=sk" not in src.lower()


def test_no_alphavantage_or_other_paid_apis():
    runner = _runner()
    src = inspect.getsource(runner).lower()
    for forbidden in ("alphavantage.co", "alpha_vantage", "import yfinance", "yfinance.",
                      "polygon.io", "iexcloud", "quandl"):
        assert forbidden not in src, f"references forbidden provider: {forbidden}"


def test_no_d_drive_writes_in_source():
    runner = _runner()
    src = inspect.getsource(runner).lower()
    assert 'open(r"d:' not in src
    assert "open('d:" not in src
    assert 'open("d:' not in src


def test_no_paper_trader_gcp_deploy_order_automation_references():
    runner = _runner()
    src = inspect.getsource(runner).lower()
    for forbidden in ("paper_trader", "gcloud ", "deploy(", "create_order(", "place_order(",
                      "submit_order", "broker_api", "schedule.every", "crontab"):
        assert forbidden not in src, f"references forbidden token: {forbidden}"


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


def test_no_paper_trader_or_gcp_files_modified():
    # The runner writes only under research/output/phase5e1_... and research/data/fmp/.
    runner = _runner()
    for out in (runner._OUT_DIR, runner._DATA_DIR):
        assert "paper_trader" not in str(out).lower()
        assert "gcp" not in str(out).lower()


# --------------------------------------------------------------------------- #
# Bounded live mode is opt-in: --live without a key is BLOCKED (no network)
# --------------------------------------------------------------------------- #
def test_live_without_key_is_blocked(monkeypatch):
    runner = _runner()
    client = _client()
    monkeypatch.setattr(client, "has_api_key", lambda: False)
    report = runner.run(live=True, max_tickers=2, max_endpoints=3, max_requests=10,
                        universe=runner.DEFAULT_UNIVERSE, verbose=False)
    try:
        assert report["mode"] == "dry_run"  # no key -> no live, no network
        assert report["recommendation"] == "BLOCKED_MISSING_FMP_KEY"
        assert report["network_used"] is False
    finally:
        runner.run(live=False, universe=runner.DEFAULT_UNIVERSE, verbose=False)


# --------------------------------------------------------------------------- #
# Monkeypatched live SUCCESS path (no real key, no real network): raw + normalized
# written LOCALLY (git-ignored), committed artifacts stay payload-free.
# --------------------------------------------------------------------------- #
def test_live_success_writes_local_data_only(monkeypatch):
    runner = _runner()
    client = _client()
    monkeypatch.setattr(client, "has_api_key", lambda: True)

    def _fake(endpoint, params=None, **kwargs):
        return [{"symbol": "AAPL", "date": "2024-03-31", "fillingDate": "2024-04-25",
                 "acceptedDate": "2024-04-25", "period": "Q1", "revenue": 100,
                 "netIncome": 20, "companyName": "Apple", "sector": "Tech"}]

    monkeypatch.setattr(client, "get_json", _fake)
    report = runner.run(live=True, max_tickers=2, max_endpoints=4, max_requests=20,
                        universe=runner.DEFAULT_UNIVERSE, verbose=False)
    try:
        assert report["mode"] == "live_sample"
        assert report["live_request_count"] > 0
        assert report["raw_files_written_count"] > 0
        assert report["normalized_files_written_count"] > 0
        assert report["endpoint_success_count"] > 0
        # Enough core stable endpoints returned data -> ready for the enriched panel.
        assert report["recommendation"] == "READY_FOR_PHASE5E2_ENRICHED_PANEL"
        # Raw + normalized live under the git-ignored paid-data tree, NOT output.
        assert runner._RAW_DIR.exists()
        assert runner._NORM_DIR.exists()
        assert not (runner._OUT_DIR / "raw").exists()
        assert not (runner._OUT_DIR / "normalized").exists()
        # Progress template now records concrete success rows.
        prog = _read_csv(runner._PROGRESS_TEMPLATE_OUT)
        assert any(r["status"] == "success" for r in prog)
        # No key marker leaked into committed artifacts even after a live run.
        marker = "api" + "key="
        for out in (runner._REQUEST_PLAN_OUT, runner._PROGRESS_TEMPLATE_OUT,
                    runner._STORAGE_MANIFEST_OUT, runner._REPORT_OUT):
            assert marker not in out.read_text(encoding="utf-8", errors="replace").lower()
    finally:
        monkeypatch.undo()
        for d in (runner._RAW_DIR, runner._NORM_DIR):
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
        runner.run(live=False, universe=runner.DEFAULT_UNIVERSE, verbose=False)


# --------------------------------------------------------------------------- #
# Monkeypatched live ALL-BLOCKED path: every request 403 -> entitlement review.
# --------------------------------------------------------------------------- #
def test_live_all_blocked_recommends_entitlement_review(monkeypatch):
    runner = _runner()
    client = _client()
    monkeypatch.setattr(client, "has_api_key", lambda: True)

    def _boom(endpoint, params=None, **kwargs):
        raise client.FmpError("provider returned HTTP 403", status_code=403,
                              error_type="http_error")

    monkeypatch.setattr(client, "get_json", _boom)
    report = runner.run(live=True, max_tickers=2, max_endpoints=3, max_requests=20,
                        universe=runner.DEFAULT_UNIVERSE, verbose=False)
    try:
        assert report["mode"] == "live_sample"
        assert report["endpoint_error_count"] > 0
        assert report["endpoint_success_count"] == 0
        assert report["recommendation"] == "NEEDS_ENDPOINT_ENTITLEMENT_REVIEW"
        # Progress template records 'error' rows with sanitized http_status.
        prog = _read_csv(runner._PROGRESS_TEMPLATE_OUT)
        err_rows = [r for r in prog if r["status"] == "error"]
        assert err_rows
        for r in err_rows:
            assert r["http_status"] == "403"
            assert "apikey=" not in r["request_url_redacted"].lower()
        # Access plan records the blocked result for attempted endpoints.
        access = _read_csv(runner._ACCESS_PLAN_OUT)
        assert any(r["live_access_result"] == "blocked_403" for r in access)
    finally:
        monkeypatch.undo()
        for d in (runner._RAW_DIR, runner._NORM_DIR):
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
        runner.run(live=False, universe=runner.DEFAULT_UNIVERSE, verbose=False)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
