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


def _use_tmp_data_dirs(runner, tmp_path):
    """Point the runner's PAID-DATA dirs (raw/normalized/.gitignore) at an isolated
    temp tree and return a restore() callable.

    SAFETY: live/cache tests must NEVER read, write, or delete the real
    research/data/fmp tree (doing so wiped the user's collected batch-001 data and
    broke --skip-existing). This redirects only the physical paid-data location; the
    committed OUTPUT dir is untouched, so committed artifacts stay canonical once
    real dirs are restored. Call restore() BEFORE regenerating canonical dry-run
    artifacts so those artifacts reference the real research/data/fmp paths."""
    saved = (runner._DATA_DIR, runner._RAW_DIR, runner._NORM_DIR, runner._GITIGNORE)
    data = tmp_path / "fmp"
    runner._DATA_DIR = data
    runner._RAW_DIR = data / "raw"
    runner._NORM_DIR = data / "normalized"
    runner._GITIGNORE = data / ".gitignore"
    runner._RAW_DIR.mkdir(parents=True, exist_ok=True)
    runner._NORM_DIR.mkdir(parents=True, exist_ok=True)

    def restore():
        (runner._DATA_DIR, runner._RAW_DIR, runner._NORM_DIR, runner._GITIGNORE) = saved

    return restore


def _reset_core_artifacts(runner):
    """Leave the committed working tree clean after a monkeypatched live core run.

    Clears the shared known-blocked register so it does not leak 402/403 rows into
    later tests, then regenerates the canonical dry-run core artifacts (header-only
    register included). It does NOT touch research/data/fmp/{raw,normalized}: live
    tests write paid data only into a temp dir (see _use_tmp_data_dirs), so the real
    cache is never created here and must never be deleted. Call this only after the
    real data dirs have been restored, so the regenerated artifacts are canonical."""
    if runner._CORE_KNOWN_BLOCKED_OUT.is_file():
        runner._CORE_KNOWN_BLOCKED_OUT.unlink()
    runner.run_core_backfill(live=False, universe_source="phase5c", max_tickers=25,
                             batch_id="core_batch_001", verbose=False)


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
    """Leave the committed OUTPUT artifacts canonical after the module runs.

    Live tests write paid data ONLY into per-test temp dirs (see _use_tmp_data_dirs),
    so this fixture must NOT delete research/data/fmp/{raw,normalized} - doing so
    previously wiped the user's real collected batch-001 cache and silently broke
    --skip-existing. It only re-emits the canonical dry-run committed artifacts.
    """
    yield
    runner = _runner()
    runner.run(live=False, universe=runner.DEFAULT_UNIVERSE, verbose=False)
    _reset_core_artifacts(runner)


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
def test_live_success_writes_local_data_only(monkeypatch, tmp_path):
    runner = _runner()
    client = _client()
    restore = _use_tmp_data_dirs(runner, tmp_path)
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
        restore()  # real data dirs back BEFORE regenerating canonical artifacts
        runner.run(live=False, universe=runner.DEFAULT_UNIVERSE, verbose=False)


# --------------------------------------------------------------------------- #
# Monkeypatched live ALL-BLOCKED path: every request 403 -> entitlement review.
# --------------------------------------------------------------------------- #
def test_live_all_blocked_recommends_entitlement_review(monkeypatch, tmp_path):
    runner = _runner()
    client = _client()
    restore = _use_tmp_data_dirs(runner, tmp_path)
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
        restore()  # real data dirs back BEFORE regenerating canonical artifacts
        runner.run(live=False, universe=runner.DEFAULT_UNIVERSE, verbose=False)


# =========================================================================== #
# Phase 5-E1B - expanded CORE fundamentals backfill (resumable, quota-safe)
# =========================================================================== #
@pytest.fixture(scope="module")
def core_run_result(monkeypatch_session):
    """Run the core backfill once in dry-run with NO key; return (runner, readiness)."""
    runner = _runner()
    readiness = runner.run_core_backfill(
        live=False, universe_source="phase5c", max_tickers=25,
        batch_id="core_batch_001", verbose=False)
    with open(runner._E1B_READINESS_OUT, "r", encoding="utf-8") as fh:
        on_disk = json.load(fh)
    assert on_disk["phase"] == readiness["phase"]
    return runner, readiness


def test_core_dry_run_executes_without_key(core_run_result):
    runner, readiness = core_run_result
    assert readiness["phase"] == "5-E1B"
    assert readiness["provider"] == "Financial Modeling Prep"
    assert readiness["mode"] == "dry_run"
    assert readiness["core_only"] is True
    assert readiness["live_request_count"] == 0
    assert readiness["network_used"] is False


def test_core_dry_run_recommends_expanded_live_batch(core_run_result):
    _, readiness = core_run_result
    assert readiness["recommendation"] == "READY_FOR_EXPANDED_CORE_LIVE_BATCH"
    assert readiness["recommendation"] in readiness["recommendation_allowed_values"]
    assert readiness["enough_for_phase5e2"] is False


def test_core_recommendation_vocabulary(core_run_result):
    _, readiness = core_run_result
    allowed = {"READY_FOR_EXPANDED_CORE_LIVE_BATCH", "READY_FOR_PHASE5E2_WITH_CORE_FUNDAMENTALS",
               "NEEDS_MORE_CORE_BACKFILL_BATCHES", "BLOCKED_MISSING_FMP_KEY", "ERROR"}
    assert set(readiness["recommendation_allowed_values"]) == allowed


def test_core_required_artifacts_exist(core_run_result):
    runner, _ = core_run_result
    for out in (runner._CORE_BATCH_PLAN_OUT, runner._CORE_BATCH_SUMMARY_OUT,
                runner._CORE_COVERAGE_OUT, runner._E1B_READINESS_OUT):
        assert out.is_file(), f"missing required Phase 5-E1B artifact: {out}"


def test_core_batch_plan_is_core_only(core_run_result):
    runner, _ = core_run_result
    rows = _read_csv(runner._CORE_BATCH_PLAN_OUT)
    assert rows
    names = {r["endpoint_name"] for r in rows}
    assert names == {"company_profile", "income_statement_quarterly",
                     "balance_sheet_statement_quarterly", "cash_flow_statement_quarterly"}
    # No earnings / analyst endpoints leak into the core batch.
    for forbidden in ("earnings_calendar", "earnings_surprises", "analyst_estimates",
                      "analyst_recommendations", "analyst_price_targets"):
        assert forbidden not in names
    for r in rows:
        assert "<API_KEY_REDACTED>" in r["request_url_redacted"]


def test_core_readiness_required_fields(core_run_result):
    _, readiness = core_run_result
    required = {
        "phase", "objective", "provider", "universe_source", "universe_size_available",
        "mode", "core_only", "batch_id", "planned_request_count", "live_request_count",
        "success_count", "empty_count", "error_count", "raw_files_written_count",
        "normalized_files_written_count", "skip_existing", "paid_data_gitignored",
        "coverage_by_endpoint", "coverage_by_ticker", "enough_for_phase5e2",
        "recommendation", "recommended_next_phase", "preview_only", "orders_enabled",
        "automation_enabled", "broker_execution_enabled", "production_replacement",
    }
    assert required <= set(readiness.keys()), f"missing fields: {required - set(readiness.keys())}"


def test_core_readiness_safety_flags(core_run_result):
    _, readiness = core_run_result
    assert readiness["preview_only"] is True
    assert readiness["orders_enabled"] is False
    assert readiness["automation_enabled"] is False
    assert readiness["broker_execution_enabled"] is False
    assert readiness["production_replacement"] is False
    assert readiness["api_key_logged"] is False
    assert readiness["paid_data_gitignored"] is True
    assert readiness["committed"] is False
    assert readiness["wrote_to_d_drive"] is False
    assert readiness["deployed"] is False


def test_core_coverage_thresholds_present(core_run_result):
    _, readiness = core_run_result
    thr = readiness["coverage_thresholds"]
    assert thr["min_core_ready_tickers"] >= 1
    assert 0 < thr["min_endpoint_coverage_pct"] <= 100
    assert 1 <= thr["min_core_endpoints_per_ticker"] <= 4


def test_core_artifacts_have_no_apikey_marker(core_run_result):
    runner, _ = core_run_result
    marker = "api" + "key="
    for out in (runner._CORE_BATCH_PLAN_OUT, runner._CORE_BATCH_SUMMARY_OUT,
                runner._CORE_COVERAGE_OUT, runner._CORE_REQUEST_RESULTS_OUT,
                runner._CORE_ERROR_REPORT_OUT, runner._CORE_KNOWN_BLOCKED_OUT,
                runner._E1B_READINESS_OUT):
        text = out.read_text(encoding="utf-8", errors="replace")
        assert marker not in text.lower(), f"key marker found in {out}"


def test_core_artifacts_have_no_key_looking_values(core_run_result):
    import re
    runner, _ = core_run_result
    keyish = re.compile(r"\b[A-Za-z0-9]{28,}\b")
    offenders = []
    for out in (runner._CORE_BATCH_PLAN_OUT, runner._CORE_BATCH_SUMMARY_OUT,
                runner._CORE_COVERAGE_OUT, runner._CORE_REQUEST_RESULTS_OUT,
                runner._CORE_ERROR_REPORT_OUT, runner._CORE_KNOWN_BLOCKED_OUT,
                runner._E1B_READINESS_OUT):
        text = out.read_text(encoding="utf-8", errors="replace")
        for m in keyish.findall(text):
            if m in ("API_KEY_REDACTED",):
                continue
            offenders.append((str(out), m))
    assert not offenders, f"suspicious key-looking tokens: {offenders}"


def test_core_paid_data_still_gitignored(core_run_result):
    runner, readiness = core_run_result
    assert runner._paid_data_gitignored() is True
    assert readiness["storage_raw_dir"] == "research/data/fmp/raw"
    assert readiness["storage_normalized_dir"] == "research/data/fmp/normalized"


# --------------------------------------------------------------------------- #
# Phase 5-C universe loading is deterministic OR falls back safely
# --------------------------------------------------------------------------- #
def test_phase5c_universe_loading_is_deterministic(monkeypatch, tmp_path):
    runner = _runner()
    csv_path = tmp_path / "prices.csv"
    csv_path.write_text(
        "ticker,date,adjusted_close,volume\n"
        "MSFT,2024-01-02,100,1000\n"
        "AAPL,2024-01-02,50,2000\n"
        "SPY,2024-01-02,400,5000\n"   # benchmark must be dropped
        "AAPL,2024-01-03,51,2100\n",   # duplicate ticker must be de-duped
        encoding="utf-8")
    monkeypatch.setattr(runner, "_phase5c_data_path", lambda: str(csv_path))
    tickers, source = runner.load_phase5c_universe()
    assert tickers == ["AAPL", "MSFT"]          # sorted, de-duped, SPY removed
    assert tickers == runner.load_phase5c_universe()[0]  # deterministic
    assert "phase5c_price_history" in source


def test_phase5c_universe_falls_back_safely(monkeypatch):
    runner = _runner()
    monkeypatch.setattr(runner, "_phase5c_data_path", lambda: None)
    tickers, source = runner.load_phase5c_universe()
    assert tickers == list(runner.DEFAULT_UNIVERSE)
    assert "fallback_sample" in source


def test_resolve_universe_explicit_wins(monkeypatch):
    runner = _runner()
    monkeypatch.setattr(runner, "_phase5c_data_path", lambda: None)
    tickers, source = runner.resolve_universe("phase5c", ["tsla", "amd"])
    assert tickers == ["TSLA", "AMD"]
    assert source == "cli_explicit"


# --------------------------------------------------------------------------- #
# Live without key in core mode -> BLOCKED (no network)
# --------------------------------------------------------------------------- #
def test_core_live_without_key_is_blocked(monkeypatch):
    runner = _runner()
    client = _client()
    monkeypatch.setattr(client, "has_api_key", lambda: False)
    readiness = runner.run_core_backfill(
        live=True, universe_source="default", max_tickers=3, max_requests=10,
        batch_id="b1", verbose=False)
    try:
        assert readiness["mode"] == "dry_run"
        assert readiness["recommendation"] == "BLOCKED_MISSING_FMP_KEY"
        assert readiness["network_used"] is False
    finally:
        runner.run_core_backfill(live=False, universe_source="phase5c", max_tickers=25,
                                 batch_id="core_batch_001", verbose=False)


# --------------------------------------------------------------------------- #
# Monkeypatched live partial-coverage path -> NEEDS_MORE_CORE_BACKFILL_BATCHES
# --------------------------------------------------------------------------- #
def test_core_live_partial_coverage_needs_more_batches(monkeypatch, tmp_path):
    runner = _runner()
    client = _client()
    restore = _use_tmp_data_dirs(runner, tmp_path)
    monkeypatch.setattr(client, "has_api_key", lambda: True)

    def _fake(endpoint, params=None, **kwargs):
        return [{"symbol": "AAPL", "date": "2024-03-31", "fillingDate": "2024-04-25",
                 "acceptedDate": "2024-04-25", "period": "Q1", "revenue": 100,
                 "netIncome": 20, "companyName": "Apple", "sector": "Tech"}]

    monkeypatch.setattr(client, "get_json", _fake)
    readiness = runner.run_core_backfill(
        live=True, universe_source="default", max_tickers=5, max_requests=50,
        batch_id="b_small", verbose=False)
    try:
        assert readiness["mode"] == "live_sample"
        assert readiness["live_request_count"] > 0
        assert readiness["success_count"] > 0
        assert readiness["raw_files_written_count"] > 0
        # 5 tickers succeed but < the 30-ticker threshold -> needs more batches.
        assert readiness["enough_for_phase5e2"] is False
        assert readiness["recommendation"] == "NEEDS_MORE_CORE_BACKFILL_BATCHES"
        # Raw + normalized live only under the git-ignored tree.
        assert runner._RAW_DIR.exists()
        assert not (runner._OUT_DIR / "raw").exists()
    finally:
        monkeypatch.undo()
        restore()  # real data dirs back BEFORE regenerating canonical artifacts
        _reset_core_artifacts(runner)


# --------------------------------------------------------------------------- #
# --skip-existing resumes: already-collected raw files consume no request budget
# --------------------------------------------------------------------------- #
def test_core_skip_existing_resumes_without_redownload(monkeypatch, tmp_path):
    runner = _runner()
    client = _client()
    restore = _use_tmp_data_dirs(runner, tmp_path)
    monkeypatch.setattr(client, "has_api_key", lambda: True)
    calls = {"n": 0}

    def _fake(endpoint, params=None, **kwargs):
        calls["n"] += 1
        return [{"symbol": "AAPL", "date": "2024-03-31", "fillingDate": "2024-04-25",
                 "acceptedDate": "2024-04-25", "period": "Q1", "revenue": 100,
                 "netIncome": 20, "companyName": "Apple", "sector": "Tech"}]

    monkeypatch.setattr(client, "get_json", _fake)
    try:
        # First batch: collect raw files for 3 tickers x 4 core endpoints.
        first = runner.run_core_backfill(
            live=True, universe_source="default", max_tickers=3, max_requests=50,
            batch_id="batch_a", verbose=False)
        assert first["live_request_count"] > 0
        first_calls = calls["n"]
        assert first_calls > 0

        # Second batch with --skip-existing: everything already collected -> NO
        # new network calls, no request budget consumed.
        second = runner.run_core_backfill(
            live=True, universe_source="default", max_tickers=3, max_requests=50,
            skip_existing=True, batch_id="batch_b", verbose=False)
        assert second["skip_existing"] is True
        assert second["live_request_count"] == 0
        assert calls["n"] == first_calls   # get_json was NOT called again
        # Coverage still counts the cached files as covered.
        assert second["success_count"] > 0
        assert second["skipped_count"] > 0
    finally:
        monkeypatch.undo()
        restore()  # real data dirs back BEFORE regenerating canonical artifacts
        _reset_core_artifacts(runner)


# --------------------------------------------------------------------------- #
# Phase 5-E1B hotfix - request-level results + retry diagnostics
# --------------------------------------------------------------------------- #
def test_core_request_results_and_error_report_exist(core_run_result):
    runner, _ = core_run_result
    assert runner._CORE_REQUEST_RESULTS_OUT.is_file()
    assert runner._CORE_ERROR_REPORT_OUT.is_file()


def test_core_request_results_columns_and_dry_run_is_planned(core_run_result):
    runner, _ = core_run_result
    rows = _read_csv(runner._CORE_REQUEST_RESULTS_OUT)
    assert rows, "request_results must have one row per planned request"
    required_cols = {"batch_id", "ticker", "endpoint_name", "status", "http_status",
                     "error_type", "error_message_sanitized", "likely_cause", "next_action",
                     "row_count", "raw_file_path", "normalized_file_path",
                     "request_url_redacted", "collected_at_utc"}
    assert required_cols <= set(rows[0].keys())
    # Dry-run: no network -> every request is 'planned', and the error report is empty.
    for r in rows:
        assert r["status"] == "planned"
        assert r["http_status"] == ""
        assert "<API_KEY_REDACTED>" in r["request_url_redacted"]
    err = _read_csv(runner._CORE_ERROR_REPORT_OUT)
    assert err == []  # header only, no error rows in dry-run


def test_core_error_report_columns(core_run_result):
    runner, _ = core_run_result
    # Even with no error rows, the header carries the groupable diagnostic columns.
    with open(runner._CORE_ERROR_REPORT_OUT, "r", newline="", encoding="utf-8") as fh:
        header = next(csv.reader(fh))
    assert set(header) == {"endpoint_name", "ticker", "http_status", "error_type",
                           "error_message_sanitized", "likely_cause", "next_action"}


def test_core_readiness_has_request_diagnostics_fields(core_run_result):
    _, readiness = core_run_result
    required = {"core_ready_ticker_count", "partial_ticker_count", "request_success_count",
                "request_error_count", "request_skipped_existing_count",
                "error_breakdown_by_http_status", "error_breakdown_by_endpoint",
                "next_retry_command"}
    assert required <= set(readiness.keys()), f"missing fields: {required - set(readiness.keys())}"
    # Dry-run: no successes/errors yet, breakdowns empty, retry command is concrete.
    assert readiness["request_error_count"] == 0
    assert readiness["error_breakdown_by_http_status"] == {}
    assert readiness["error_breakdown_by_endpoint"] == {}
    assert "--skip-existing" in readiness["next_retry_command"]
    assert "--core-only" in readiness["next_retry_command"]


def test_core_live_error_produces_error_rows(monkeypatch, tmp_path):
    runner = _runner()
    client = _client()
    restore = _use_tmp_data_dirs(runner, tmp_path)
    monkeypatch.setattr(client, "has_api_key", lambda: True)

    def _boom(endpoint, params=None, **kwargs):
        raise client.FmpError("provider returned HTTP 403", status_code=403,
                              error_type="http_error")

    monkeypatch.setattr(client, "get_json", _boom)
    try:
        readiness = runner.run_core_backfill(
            live=True, universe_source="default", max_tickers=3, max_requests=50,
            batch_id="err_batch", verbose=False)
        assert readiness["mode"] == "live_sample"
        assert readiness["request_error_count"] > 0
        # The error breakdown attributes failures to HTTP 403 and to core endpoints.
        assert readiness["error_breakdown_by_http_status"].get("403", 0) > 0
        assert readiness["error_breakdown_by_endpoint"]
        # request_results carries diagnostic cause/action for each failed request.
        rows = _read_csv(runner._CORE_REQUEST_RESULTS_OUT)
        err_rows = [r for r in rows if r["status"] == "error"]
        assert err_rows
        for r in err_rows:
            assert r["http_status"] == "403"
            assert r["likely_cause"]
            assert r["next_action"]
            assert "apikey=" not in r["request_url_redacted"].lower()
        # error_report.csv mirrors only the error rows.
        ereport = _read_csv(runner._CORE_ERROR_REPORT_OUT)
        assert len(ereport) == len(err_rows)
        for r in ereport:
            assert r["http_status"] == "403"
            assert r["likely_cause"]
    finally:
        monkeypatch.undo()
        restore()  # real data dirs back BEFORE regenerating canonical artifacts
        _reset_core_artifacts(runner)


def test_core_skip_existing_produces_skipped_existing_rows(monkeypatch, tmp_path):
    runner = _runner()
    client = _client()
    restore = _use_tmp_data_dirs(runner, tmp_path)
    monkeypatch.setattr(client, "has_api_key", lambda: True)

    def _fake(endpoint, params=None, **kwargs):
        return [{"symbol": "AAPL", "date": "2024-03-31", "fillingDate": "2024-04-25",
                 "acceptedDate": "2024-04-25", "period": "Q1", "revenue": 100,
                 "netIncome": 20, "companyName": "Apple", "sector": "Tech"}]

    monkeypatch.setattr(client, "get_json", _fake)
    try:
        runner.run_core_backfill(
            live=True, universe_source="default", max_tickers=3, max_requests=50,
            batch_id="seed_batch", verbose=False)
        second = runner.run_core_backfill(
            live=True, universe_source="default", max_tickers=3, max_requests=50,
            skip_existing=True, batch_id="resume_batch", verbose=False)
        assert second["request_skipped_existing_count"] > 0
        assert second["live_request_count"] == 0
        rows = _read_csv(runner._CORE_REQUEST_RESULTS_OUT)
        skipped = [r for r in rows if r["status"] == "skipped_existing"]
        assert skipped, "resume batch must report skipped_existing request rows"
        for r in skipped:
            assert "already collected" in r["likely_cause"].lower()
        # No 'error' or 'planned' leakage: every resumed row is skipped_existing.
        assert all(r["status"] == "skipped_existing" for r in rows)
    finally:
        monkeypatch.undo()
        restore()  # real data dirs back BEFORE regenerating canonical artifacts
        _reset_core_artifacts(runner)


# --------------------------------------------------------------------------- #
# Phase 5-E1B reliability patch - hardened skip-existing + known-blocked handling
# --------------------------------------------------------------------------- #
def test_core_known_blocked_artifact_exists(core_run_result):
    runner, _ = core_run_result
    assert runner._CORE_KNOWN_BLOCKED_OUT.is_file()
    with open(runner._CORE_KNOWN_BLOCKED_OUT, "r", newline="", encoding="utf-8") as fh:
        header = next(csv.reader(fh))
    assert set(header) == {"ticker", "endpoint_name", "http_status", "error_type",
                           "error_message_sanitized", "likely_cause",
                           "first_seen_batch_id", "last_seen_batch_id", "retry_policy"}
    # Dry-run: no errors yet -> header only, no remembered blocks.
    assert _read_csv(runner._CORE_KNOWN_BLOCKED_OUT) == []


def test_core_readiness_has_reliability_patch_fields(core_run_result):
    _, readiness = core_run_result
    required = {"request_skipped_known_blocked_count", "known_blocked_count",
                "known_blocked_by_http_status", "known_blocked_by_endpoint",
                "live_budget_spent_on_new_requests_count",
                "live_budget_spent_on_retries_count", "next_batch_command"}
    assert required <= set(readiness.keys()), f"missing: {required - set(readiness.keys())}"
    # Dry-run: nothing blocked or spent yet.
    assert readiness["known_blocked_count"] == 0
    assert readiness["known_blocked_by_http_status"] == {}
    assert readiness["request_skipped_known_blocked_count"] == 0
    assert readiness["live_budget_spent_on_new_requests_count"] == 0
    assert readiness["live_budget_spent_on_retries_count"] == 0


def test_core_next_batch_command_is_concrete(core_run_result):
    _, readiness = core_run_result
    cmd = readiness["next_batch_command"]
    for token in ("--core-only", "--skip-existing", "--skip-known-blocked",
                  "core_batch_002", "--max-tickers 50"):
        assert token in cmd, f"next_batch_command missing {token}: {cmd}"


def test_core_live_402_creates_known_blocked_register(monkeypatch, tmp_path):
    runner = _runner()
    client = _client()
    restore = _use_tmp_data_dirs(runner, tmp_path)
    monkeypatch.setattr(client, "has_api_key", lambda: True)

    def _boom(endpoint, params=None, **kwargs):
        raise client.FmpError("provider returned HTTP 402", status_code=402,
                              error_type="http_error")

    monkeypatch.setattr(client, "get_json", _boom)
    try:
        readiness = runner.run_core_backfill(
            live=True, universe_source="default", max_tickers=3, max_requests=50,
            batch_id="blocked_batch", verbose=False)
        assert readiness["mode"] == "live_sample"
        assert readiness["known_blocked_count"] > 0
        assert readiness["known_blocked_by_http_status"].get("402", 0) > 0
        rows = _read_csv(runner._CORE_KNOWN_BLOCKED_OUT)
        assert rows, "402 errors must be persisted to the known-blocked register"
        for r in rows:
            assert r["http_status"] == "402"
            assert r["retry_policy"] == "do_not_retry_without_override"
            assert r["first_seen_batch_id"] == "blocked_batch"
            assert r["last_seen_batch_id"] == "blocked_batch"
        # Register is committed-safe: no key marker.
        text = runner._CORE_KNOWN_BLOCKED_OUT.read_text(encoding="utf-8")
        assert "apikey=" not in text.lower()
    finally:
        monkeypatch.undo()
        restore()  # real data dirs back BEFORE regenerating canonical artifacts
        _reset_core_artifacts(runner)


def test_core_skip_known_blocked_skips_402_without_live_call(monkeypatch, tmp_path):
    runner = _runner()
    client = _client()
    restore = _use_tmp_data_dirs(runner, tmp_path)
    monkeypatch.setattr(client, "has_api_key", lambda: True)

    def _boom(endpoint, params=None, **kwargs):
        raise client.FmpError("provider returned HTTP 402", status_code=402,
                              error_type="http_error")

    def _ok(endpoint, params=None, **kwargs):
        return [{"symbol": "AAPL", "date": "2024-03-31", "fillingDate": "2024-04-25",
                 "acceptedDate": "2024-04-25", "period": "Q1", "revenue": 100,
                 "netIncome": 20, "companyName": "Apple", "sector": "Tech"}]

    try:
        # Seed the register with a batch that gets 402 on every request.
        monkeypatch.setattr(client, "get_json", _boom)
        runner.run_core_backfill(
            live=True, universe_source="default", max_tickers=3, max_requests=50,
            batch_id="seed_402", verbose=False)
        # Resume with --skip-known-blocked: even though the provider WOULD now answer,
        # the known-blocked pairs must be skipped with no live call spent.
        monkeypatch.setattr(client, "get_json", _ok)
        second = runner.run_core_backfill(
            live=True, universe_source="default", max_tickers=3, max_requests=50,
            skip_known_blocked=True, batch_id="resume_skip_blocked", verbose=False)
        assert second["request_skipped_known_blocked_count"] > 0
        assert second["live_request_count"] == 0, "known-blocked skips must spend no budget"
        rows = _read_csv(runner._CORE_REQUEST_RESULTS_OUT)
        blocked = [r for r in rows if r["status"] == "skipped_known_blocked"]
        assert blocked, "resume must report skipped_known_blocked rows"
        assert all(r["status"] == "skipped_known_blocked" for r in rows)
    finally:
        monkeypatch.undo()
        restore()  # real data dirs back BEFORE regenerating canonical artifacts
        _reset_core_artifacts(runner)


def test_core_retry_known_blocked_overrides_skip(monkeypatch, tmp_path):
    runner = _runner()
    client = _client()
    restore = _use_tmp_data_dirs(runner, tmp_path)
    monkeypatch.setattr(client, "has_api_key", lambda: True)

    def _boom(endpoint, params=None, **kwargs):
        raise client.FmpError("provider returned HTTP 402", status_code=402,
                              error_type="http_error")

    monkeypatch.setattr(client, "get_json", _boom)
    try:
        runner.run_core_backfill(
            live=True, universe_source="default", max_tickers=2, max_requests=50,
            batch_id="seed_402b", verbose=False)
        # --retry-known-blocked overrides --skip-known-blocked: the pairs are
        # re-attempted and accounted as retries, not new requests.
        second = runner.run_core_backfill(
            live=True, universe_source="default", max_tickers=2, max_requests=50,
            skip_known_blocked=True, retry_known_blocked=True,
            batch_id="retry_402b", verbose=False)
        assert second["live_request_count"] > 0
        assert second["request_skipped_known_blocked_count"] == 0
        assert second["live_budget_spent_on_retries_count"] > 0
        assert second["live_budget_spent_on_new_requests_count"] == 0
    finally:
        monkeypatch.undo()
        restore()  # real data dirs back BEFORE regenerating canonical artifacts
        _reset_core_artifacts(runner)


def test_core_skip_existing_detects_timestamped_raw_file(monkeypatch, tmp_path):
    runner = _runner()
    client = _client()
    restore = _use_tmp_data_dirs(runner, tmp_path)
    monkeypatch.setattr(client, "has_api_key", lambda: True)

    def _ok(endpoint, params=None, **kwargs):
        return [{"symbol": "AAPL", "date": "2024-03-31", "fillingDate": "2024-04-25",
                 "acceptedDate": "2024-04-25", "period": "Q1", "revenue": 100,
                 "netIncome": 20, "companyName": "Apple", "sector": "Tech"}]

    monkeypatch.setattr(client, "get_json", _ok)
    try:
        # Pre-seed a NON-canonical (timestamped) raw file for company_profile/AAPL.
        ep_dir = runner._RAW_DIR / "company_profile"
        ep_dir.mkdir(parents=True, exist_ok=True)
        with open(ep_dir / "AAPL.20240101T000000Z.json", "w", encoding="utf-8") as fh:
            json.dump([{"symbol": "AAPL", "companyName": "Apple"}], fh)
        readiness = runner.run_core_backfill(
            live=True, universe_source="default", max_tickers=1, max_requests=50,
            skip_existing=True, batch_id="ts_batch", verbose=False)
        rows = _read_csv(runner._CORE_REQUEST_RESULTS_OUT)
        prof = [r for r in rows if r["endpoint_name"] == "company_profile"
                and r["ticker"] == "AAPL"]
        assert prof and prof[0]["status"] == "skipped_existing", \
            "timestamped raw file must be detected by skip-existing"
        assert readiness["request_skipped_existing_count"] >= 1
    finally:
        monkeypatch.undo()
        restore()  # real data dirs back BEFORE regenerating canonical artifacts
        _reset_core_artifacts(runner)


def test_core_skip_existing_ignores_empty_raw_file(monkeypatch, tmp_path):
    runner = _runner()
    client = _client()
    restore = _use_tmp_data_dirs(runner, tmp_path)
    monkeypatch.setattr(client, "has_api_key", lambda: True)

    def _ok(endpoint, params=None, **kwargs):
        return [{"symbol": "AAPL", "date": "2024-03-31", "fillingDate": "2024-04-25",
                 "acceptedDate": "2024-04-25", "period": "Q1", "revenue": 100,
                 "netIncome": 20, "companyName": "Apple", "sector": "Tech"}]

    monkeypatch.setattr(client, "get_json", _ok)
    try:
        # A prior EMPTY raw file must NOT be trusted as a successful collection.
        ep_dir = runner._RAW_DIR / "company_profile"
        ep_dir.mkdir(parents=True, exist_ok=True)
        with open(ep_dir / "AAPL.json", "w", encoding="utf-8") as fh:
            json.dump([], fh)
        readiness = runner.run_core_backfill(
            live=True, universe_source="default", max_tickers=1, max_requests=50,
            skip_existing=True, batch_id="empty_batch", verbose=False)
        rows = _read_csv(runner._CORE_REQUEST_RESULTS_OUT)
        prof = [r for r in rows if r["endpoint_name"] == "company_profile"
                and r["ticker"] == "AAPL"]
        assert prof and prof[0]["status"] == "success", \
            "empty cached raw file must be re-fetched, not skipped"
        assert readiness["request_skipped_existing_count"] == 0
    finally:
        monkeypatch.undo()
        restore()  # real data dirs back BEFORE regenerating canonical artifacts
        _reset_core_artifacts(runner)


def test_core_no_paper_trader_gcp_order_automation_in_source():
    runner = _runner()
    src = (inspect.getsource(runner.run_core_backfill)
           + inspect.getsource(runner.run_live_backfill)
           + inspect.getsource(runner.run_core_preflight)
           + inspect.getsource(runner._merge_known_blocked)
           + inspect.getsource(runner._find_existing_raw)).lower()
    for forbidden in ("paper_trader", "gcloud ", "create_order", "place_order",
                      "submit_order", "broker_api", "schedule.every", "crontab"):
        assert forbidden not in src, f"core backfill references forbidden token: {forbidden}"


# --------------------------------------------------------------------------- #
# Cache-safety regression guards (the bug: test cleanup rmtree'd the user's REAL
# research/data/fmp paid data, which silently broke --skip-existing in batch 002)
# --------------------------------------------------------------------------- #
def test_core_skip_existing_in_tmp_dir_makes_no_live_call(monkeypatch, tmp_path):
    """A real-looking paid-data file under a TEMP raw dir must be detected by
    --skip-existing: NO FMP request is made, every planned row is skipped_existing,
    and live_request_count stays 0 - all without touching the real data tree."""
    runner = _runner()
    client = _client()
    restore = _use_tmp_data_dirs(runner, tmp_path)
    monkeypatch.setattr(client, "has_api_key", lambda: True)

    def _must_not_call(endpoint, params=None, **kwargs):
        raise AssertionError("get_json must not be called when skip-existing covers all requests")

    monkeypatch.setattr(client, "get_json", _must_not_call)
    try:
        ticker = runner.DEFAULT_UNIVERSE[0]
        for name in runner._CORE_ONLY_ENDPOINTS:
            ep_dir = runner._RAW_DIR / name
            ep_dir.mkdir(parents=True, exist_ok=True)
            with open(ep_dir / ("%s.json" % ticker), "w", encoding="utf-8") as fh:
                json.dump([{"symbol": ticker, "date": "2024-03-31"}], fh)
        readiness = runner.run_core_backfill(
            live=True, universe_source="default", max_tickers=1, max_requests=50,
            skip_existing=True, batch_id="tmp_skip_batch", verbose=False)
        assert readiness["mode"] == "live_sample"
        assert readiness["live_request_count"] == 0, "skip-existing must spend no live budget"
        assert readiness["request_skipped_existing_count"] == len(runner._CORE_ONLY_ENDPOINTS)
        rows = _read_csv(runner._CORE_REQUEST_RESULTS_OUT)
        assert rows and all(r["status"] == "skipped_existing" for r in rows)
    finally:
        monkeypatch.undo()
        restore()
        _reset_core_artifacts(runner)


def test_real_paid_data_dirs_are_never_deleted_by_a_live_test(monkeypatch, tmp_path):
    """Regression guard: a simulated live batch must NOT delete or mutate the REAL
    research/data/fmp/{raw,normalized} tree. Drop a sentinel into the real tree, run
    a redirected live batch exactly as the live tests do, and assert the sentinel
    survives. (This is the bug that wiped batch-001 and broke skip-existing.)"""
    runner = _runner()
    client = _client()
    real_raw = runner._RAW_DIR
    real_norm = runner._NORM_DIR
    raw_sentinel = real_raw / "__skip_existing_guard__" / "SENTINEL.json"
    norm_sentinel = real_norm / "__skip_existing_guard__.csv"
    raw_sentinel.parent.mkdir(parents=True, exist_ok=True)
    real_norm.mkdir(parents=True, exist_ok=True)
    raw_sentinel.write_text('[{"guard": true}]', encoding="utf-8")
    norm_sentinel.write_text("guard\n1\n", encoding="utf-8")
    try:
        restore = _use_tmp_data_dirs(runner, tmp_path)
        monkeypatch.setattr(client, "has_api_key", lambda: True)
        monkeypatch.setattr(
            client, "get_json",
            lambda *a, **k: [{"symbol": "AAPL", "date": "2024-03-31"}])
        try:
            runner.run_core_backfill(
                live=True, universe_source="default", max_tickers=2, max_requests=20,
                batch_id="guard_batch", verbose=False)
        finally:
            monkeypatch.undo()
            restore()
            _reset_core_artifacts(runner)
        assert runner._RAW_DIR is real_raw, "restore() must put the real raw dir back"
        assert raw_sentinel.is_file(), "a live test must NOT delete real raw paid data"
        assert norm_sentinel.is_file(), "a live test must NOT delete real normalized paid data"
    finally:
        # Remove ONLY the sentinels this test created; never touch the user's data.
        if raw_sentinel.is_file():
            raw_sentinel.unlink()
        if raw_sentinel.parent.is_dir():
            try:
                raw_sentinel.parent.rmdir()
            except OSError:
                pass
        if norm_sentinel.is_file():
            norm_sentinel.unlink()


def test_core_preflight_cache_only_reports_skips_without_network(monkeypatch, tmp_path):
    """run_core_preflight / --preflight-cache-only reports how many planned requests
    would be skipped vs. spent live, WITHOUT any network and WITHOUT a key. Seeded
    raw files surface as existing_raw_files_detected and reduce the live-call count.
    This is the safe pre-check to run before any live batch."""
    runner = _runner()
    client = _client()
    restore = _use_tmp_data_dirs(runner, tmp_path)

    def _must_not_call(endpoint, params=None, **kwargs):
        raise AssertionError("preflight must never call the provider")

    monkeypatch.setattr(client, "get_json", _must_not_call)
    monkeypatch.setattr(client, "has_api_key", lambda: False)  # no key required
    try:
        ticker = runner.DEFAULT_UNIVERSE[0]
        for name in runner._CORE_ONLY_ENDPOINTS[:2]:
            ep_dir = runner._RAW_DIR / name
            ep_dir.mkdir(parents=True, exist_ok=True)
            with open(ep_dir / ("%s.json" % ticker), "w", encoding="utf-8") as fh:
                json.dump([{"symbol": ticker, "date": "2024-03-31"}], fh)
        pf = runner.run_core_preflight(
            universe_source="default", max_tickers=2, max_requests=100,
            skip_existing=True, skip_known_blocked=True, batch_id="core_batch_002",
            verbose=False)
        assert pf["network_used"] is False
        assert pf["mode"] == "preflight_cache_only"
        # 4 core endpoints x 2 tickers = 8 planned; 2 seeded files -> 2 skipped.
        assert pf["planned_request_count"] == 8
        assert pf["existing_raw_files_detected"] == 2
        assert pf["planned_skipped_existing_count"] == 2
        assert pf["planned_live_request_count_if_live"] == 6
        assert runner._CORE_PREFLIGHT_OUT.is_file()
        assert runner._E1B_PREFLIGHT_JSON_OUT.is_file()
        assert "apikey=" not in runner._CORE_PREFLIGHT_OUT.read_text(encoding="utf-8").lower()
        # CLI entrypoint works with no key and exits 0.
        rc = runner.main(["--core-only", "--preflight-cache-only",
                          "--universe-source", "default", "--max-tickers", "2",
                          "--skip-existing", "--skip-known-blocked",
                          "--batch-id", "core_batch_002"])
        assert rc == 0
    finally:
        monkeypatch.undo()
        restore()
        # Leave a canonical (real-dir) preflight summary on disk, not tmp paths.
        runner.run_core_preflight(
            universe_source="phase5c", max_tickers=50, max_requests=100,
            skip_existing=True, skip_known_blocked=True,
            batch_id="core_batch_002", verbose=False)
        _reset_core_artifacts(runner)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
