"""Phase 5-E0 - tests for the FMP provider adapter + trial collector.

Verifies the adapter imports, the collector compiles and runs fully in DRY-RUN
mode with NO FMP_API_KEY and NO network, writes every required artifact, exposes
the correct phase/provider/mode and safety flags, catalogs all required endpoint
families, keeps the API key out of every artifact, and emits a Phase 5-E1 plan
that references FMP_API_KEY.

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

_RUNNER_MODULE = "research.run_phase5e0_fmp_provider_trial_collector"
_CLIENT_MODULE = "research.providers.fmp_client"


def _runner():
    return importlib.import_module(_RUNNER_MODULE)


def _client():
    return importlib.import_module(_CLIENT_MODULE)


def _read_csv(path):
    with open(path, "r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def run_result(monkeypatch_session):
    """Run the collector once in dry-run with NO key; return (runner, report)."""
    runner = _runner()
    report = runner.run(live=False, sample_tickers=runner.DEFAULT_SAMPLE_TICKERS, verbose=False)
    with open(runner._REPORT_OUT, "r", encoding="utf-8") as fh:
        on_disk = json.load(fh)
    assert on_disk["phase"] == report["phase"]
    return runner, report


@pytest.fixture(scope="module")
def monkeypatch_session():
    """Module-scoped env guard: ensure FMP_API_KEY is absent during the dry-run."""
    saved = os.environ.pop("FMP_API_KEY", None)
    try:
        yield
    finally:
        if saved is not None:
            os.environ["FMP_API_KEY"] = saved


@pytest.fixture(scope="module", autouse=True)
def _commit_safe_live_output_cleanup():
    """Guarantee the test module leaves NO live-only side effects behind.

    The live-failure test (``test_live_error_report_created_on_failure``) runs the
    collector in live mode, which creates the ``raw/`` + ``normalized/`` dirs and
    ``fmp_live_error_report.csv`` in the real output dir. Those must never be
    committed, so this finalizer removes them and re-asserts the canonical
    commit-safe dry-run artifacts after the whole module finishes - keeping
    `Test-Path .../raw`, `.../normalized`, and `.../fmp_live_error_report.csv`
    all False for commit-safety validation.
    """
    yield
    runner = _runner()
    for d in (runner._RAW_DIR, runner._NORM_DIR):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
    if runner._LIVE_ERROR_REPORT_OUT.is_file():
        try:
            runner._LIVE_ERROR_REPORT_OUT.unlink()
        except OSError:
            pass
    # Re-emit the canonical dry-run artifacts (also re-runs the runner's own
    # stale-live-output cleanup) so the trial dir is left commit-safe.
    runner.run(live=False, sample_tickers=runner.DEFAULT_SAMPLE_TICKERS, verbose=False)


# --------------------------------------------------------------------------- #
# Imports / compiles / dry-run executes with no key, no network
# --------------------------------------------------------------------------- #
def test_fmp_client_imports():
    client = _client()
    for fn in ("has_api_key", "sanitize_url", "build_fmp_url", "get_json",
               "endpoint_catalog", "trial_collection_plan"):
        assert hasattr(client, fn), f"fmp_client missing {fn}"


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


def test_phase_and_provider(run_result):
    _, report = run_result
    assert report["phase"] == "5-E0"
    assert report["provider"] == "Financial Modeling Prep"


def test_api_key_not_required(monkeypatch_session):
    client = _client()
    assert client.has_api_key() is False


# --------------------------------------------------------------------------- #
# All required output files exist
# --------------------------------------------------------------------------- #
def test_all_required_output_files_exist(run_result):
    runner, _ = run_result
    for out in (runner._REPORT_OUT, runner._CATALOG_OUT, runner._PLAN_OUT,
                runner._SCHEMA_OUT, runner._NORM_MANIFEST_OUT, runner._PIT_OUT,
                runner._SECRET_AUDIT_OUT, runner._BACKFILL_PLAN_OUT):
        assert out.is_file(), f"missing required artifact: {out}"


# --------------------------------------------------------------------------- #
# Main JSON valid + safety flags
# --------------------------------------------------------------------------- #
def test_main_json_safety_flags(run_result):
    _, report = run_result
    assert report["preview_only"] is True
    assert report["orders_enabled"] is False
    assert report["automation_enabled"] is False
    assert report["broker_execution_enabled"] is False
    assert report["production_replacement"] is False
    assert report["api_key_logged"] is False


def test_recommendation_no_key_is_ready_for_key(run_result):
    _, report = run_result
    assert report["recommendation"] == "READY_FOR_FMP_KEY"
    assert report["recommendation"] in report["recommendation_allowed_values"]


def test_recommendation_vocabulary(run_result):
    _, report = run_result
    allowed = {"READY_FOR_FMP_KEY", "READY_FOR_LIVE_SAMPLE",
               "READY_FOR_PHASE5E1_BACKFILL", "BLOCKED_MISSING_FMP_KEY", "ERROR"}
    assert set(report["recommendation_allowed_values"]) == allowed


# --------------------------------------------------------------------------- #
# Endpoint catalog covers every required family
# --------------------------------------------------------------------------- #
def test_endpoint_catalog_covers_required_endpoints(run_result):
    runner, _ = run_result
    rows = _read_csv(runner._CATALOG_OUT)
    names = {r["endpoint_name"] for r in rows}
    required = {
        "company_profile", "income_statement_quarterly",
        "balance_sheet_statement_quarterly", "cash_flow_statement_quarterly",
        "key_metrics_quarterly", "ratios_quarterly", "earnings_calendar",
        "earnings_surprises", "analyst_estimates", "analyst_recommendations",
        "analyst_price_targets", "sp500_constituents",
    }
    assert required <= names, f"catalog missing endpoints: {required - names}"


def test_catalog_covers_data_families(run_result):
    runner, _ = run_result
    rows = _read_csv(runner._CATALOG_OUT)
    fams = {r["alpha_family"] for r in rows}
    # fundamentals, earnings, analyst revisions all represented.
    assert "fundamentals" in fams
    assert "earnings" in fams
    assert "analyst_revisions" in fams


def test_planned_requests_present(run_result):
    runner, report = run_result
    rows = _read_csv(runner._PLAN_OUT)
    assert len(rows) >= 1
    assert report["planned_request_count"] == len(rows)
    # Every planned request carries a persist-safe redacted URL: the key query
    # parameter is stripped entirely (no "apikey=" substring) and a placeholder
    # token marks where the key would be appended at live-request time.
    for r in rows:
        assert "<API_KEY_REDACTED>" in r["request_url_redacted"]
        assert "apikey=" not in r["request_url_redacted"].lower()


# --------------------------------------------------------------------------- #
# Schema contract + point-in-time readiness present
# --------------------------------------------------------------------------- #
def test_schema_contract_present(run_result):
    runner, _ = run_result
    rows = _read_csv(runner._SCHEMA_OUT)
    assert len(rows) >= 1
    assert {"endpoint_name", "normalized_columns", "join_key",
            "point_in_time_date_field", "point_in_time_status"} <= set(rows[0].keys())


def test_point_in_time_readiness_present(run_result):
    runner, _ = run_result
    rows = _read_csv(runner._PIT_OUT)
    assert len(rows) >= 1
    allowed = {"point_in_time_safe", "potentially_point_in_time",
               "not_point_in_time_safe", "unknown"}
    for r in rows:
        assert r["point_in_time_status"] in allowed


# --------------------------------------------------------------------------- #
# Secret-safety audit + no key in artifacts
# --------------------------------------------------------------------------- #
def test_secret_safety_audit_api_key_logged_false(run_result):
    runner, _ = run_result
    rows = _read_csv(runner._SECRET_AUDIT_OUT)
    by_check = {r["check"]: r for r in rows}
    assert "api_key_logged" in by_check
    assert by_check["api_key_logged"]["value"] in ("False", "false")
    # The leak-scan check must pass.
    assert by_check["no_key_in_output_files"]["passed"] in ("True", "true")


def test_no_apikey_value_in_any_artifact(run_result):
    # The strict validation rule: NO generated artifact may contain the literal
    # key-query-parameter marker at all (not even with a REDACTED value).
    runner, _ = run_result
    trial_dir = runner._TRIAL_DIR
    marker = "api" + "key="
    bad = []
    for root, _dirs, files in os.walk(trial_dir):
        for fn in files:
            if not fn.endswith((".csv", ".json")):
                continue
            p = os.path.join(root, fn)
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            if marker in text.lower():
                j = text.lower().find(marker)
                bad.append((p, text[j:j + 40]))
    assert not bad, f"key query parameter marker found in artifacts: {bad}"


def test_no_hardcoded_key_in_source():
    runner = _runner()
    client = _client()
    for mod in (runner, client):
        src = inspect.getsource(mod)
        # The key is only ever referenced via the env var name, never a literal value.
        assert "FMP_API_KEY" in src
        # No obvious literal assignment of a key.
        assert "apikey=sk" not in src.lower()
        assert "api_key = \"" not in src.lower().replace("'", '"') or "os.environ" in src


# --------------------------------------------------------------------------- #
# Phase 5-E1 plan references FMP_API_KEY + correct next modeling phase
# --------------------------------------------------------------------------- #
def test_phase5e1_plan_references_fmp_key(run_result):
    runner, _ = run_result
    with open(runner._BACKFILL_PLAN_OUT, "r", encoding="utf-8") as fh:
        plan = json.load(fh)
    assert plan["required_env_var"] == "FMP_API_KEY"
    assert plan["preview_only"] is True
    assert plan["orders_enabled"] is False
    assert plan["automation_enabled"] is False
    assert plan["broker_execution_enabled"] is False
    blob = json.dumps(plan)
    assert "FMP_API_KEY" in blob
    assert "5-E2" in blob or "5E2" in blob


# --------------------------------------------------------------------------- #
# Provider-discipline: no AlphaVantage / other paid APIs referenced
# --------------------------------------------------------------------------- #
def test_no_alphavantage_or_other_paid_apis():
    runner = _runner()
    client = _client()
    for mod in (runner, client):
        src = inspect.getsource(mod).lower()
        # Usage tokens (hostnames / package imports), not honest prose mentions of
        # the "do not call AlphaVantage" rule.
        for forbidden in ("alphavantage.co", "alpha_vantage", "import yfinance", "yfinance.",
                          "polygon.io", "iexcloud", "quandl"):
            assert forbidden not in src, f"references forbidden provider: {forbidden}"


# --------------------------------------------------------------------------- #
# No D: writes; no Paper Trader / GCP / deploy / order code
# --------------------------------------------------------------------------- #
def test_no_d_drive_writes_in_source():
    runner = _runner()
    src = inspect.getsource(runner).lower()
    assert 'open(r"d:' not in src
    assert "open('d:" not in src
    assert 'open("d:' not in src


def test_no_paper_trader_gcp_deploy_order_references():
    runner = _runner()
    client = _client()
    for mod in (runner, client):
        src = inspect.getsource(mod).lower()
        for forbidden in ("paper_trader", "gcloud ", "deploy(", "create_order(",
                          "place_order(", "submit_order", "broker_api", "schedule.every",
                          "crontab"):
            assert forbidden not in src, f"references forbidden token: {forbidden}"


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
# Adapter unit behavior: sanitize_url redacts; dry-run get_json does no network
# --------------------------------------------------------------------------- #
def test_sanitize_url_redacts_key():
    client = _client()
    url = "https://financialmodelingprep.com/stable/profile?symbol=AAPL&apikey=SECRET123"
    assert "SECRET123" not in client.sanitize_url(url)
    assert "apikey=REDACTED" in client.sanitize_url(url)


def test_redacted_request_url_strips_key_param(monkeypatch_session):
    # The persist-safe form must NEVER contain the key query parameter, even
    # redacted: the parameter is stripped and a placeholder appended. Non-secret
    # params (period=quarter) survive so the request shape is still documented.
    client = _client()
    out = client.redacted_request_url("/stable/income-statement?symbol=AAPL&period=quarter")
    assert "apikey=" not in out.lower()
    assert "<API_KEY_REDACTED>" in out
    assert "period=quarter" in out
    assert out.startswith("https://financialmodelingprep.com/")


def test_get_json_dry_run_is_metadata_only(monkeypatch_session):
    client = _client()
    out = client.get_json("/stable/profile?symbol=AAPL", live=False)
    assert isinstance(out, dict)
    assert out.get("dry_run") is True
    assert "apikey=REDACTED" in out["sanitized_url"]
    assert out["api_key_logged"] is False


# --------------------------------------------------------------------------- #
# Stable endpoint paths (the Phase 5-E0 live HTTP 403 fix): the catalog must use
# the current /stable/ API, never the legacy /api/v3 or /api/v4 paths.
# --------------------------------------------------------------------------- #
def test_catalog_uses_stable_paths_only(run_result):
    runner, _ = run_result
    rows = _read_csv(runner._CATALOG_OUT)
    assert rows
    for r in rows:
        tmpl = r["endpoint_path_template"]
        assert tmpl.startswith("/stable/"), f"non-stable endpoint path: {tmpl}"
        assert "/api/v3" not in tmpl, f"legacy v3 path still present: {tmpl}"
        assert "/api/v4" not in tmpl, f"legacy v4 path still present: {tmpl}"


def test_no_legacy_api_paths_in_client_catalog():
    client = _client()
    for e in client.endpoint_catalog():
        tmpl = e["endpoint_path_template"]
        assert "/api/v3" not in tmpl
        assert "/api/v4" not in tmpl
        assert tmpl.startswith("/stable/")


def test_endpoint_status_recorded(run_result):
    runner, _ = run_result
    rows = _read_csv(runner._CATALOG_OUT)
    allowed = {"stable_confirmed", "needs_live_verification"}
    for r in rows:
        assert r["endpoint_status"] in allowed, f"bad status: {r}"
    # At least the fundamentals statements + profile are stable_confirmed.
    confirmed = {r["endpoint_name"] for r in rows
                 if r["endpoint_status"] == "stable_confirmed"}
    assert {"company_profile", "income_statement_quarterly",
            "ratios_quarterly", "key_metrics_quarterly"} <= confirmed


def test_first_live_smoke_excludes_unverified_endpoints():
    runner = _runner()
    smoke = runner._select_live_endpoints(3)
    names = [e["endpoint_name"] for e in smoke]
    assert len(smoke) == 3
    for e in smoke:
        assert e["endpoint_status"] == "stable_confirmed"
        assert e.get("smoke_safe") is True
    # Analyst / price-target endpoints must NOT appear in the first smoke.
    assert not ({"analyst_estimates", "analyst_recommendations",
                 "analyst_price_targets", "earnings_calendar", "earnings_surprises"}
                & set(names))
    # Profile (auth canary) + income statement are part of the first smoke.
    assert "company_profile" in names
    assert "income_statement_quarterly" in names


def test_dry_run_is_commit_safe_removes_stale_live_output(monkeypatch_session):
    """A default dry-run must leave NO live-only artifacts: even if a prior live
    smoke left raw/, normalized/, and fmp_live_error_report.csv behind, the
    dry-run runner removes them so commit-safety validation passes."""
    runner = _runner()
    # Simulate leftovers from a prior live smoke.
    runner._RAW_DIR.mkdir(parents=True, exist_ok=True)
    (runner._RAW_DIR / "company_profile").mkdir(parents=True, exist_ok=True)
    (runner._RAW_DIR / "company_profile" / "AAPL.json").write_text("{}", encoding="utf-8")
    runner._NORM_DIR.mkdir(parents=True, exist_ok=True)
    (runner._NORM_DIR / "company_profile.csv").write_text("symbol\nAAPL\n", encoding="utf-8")
    runner._LIVE_ERROR_REPORT_OUT.write_text("endpoint_name\n", encoding="utf-8")
    assert runner._RAW_DIR.is_dir()
    assert runner._NORM_DIR.is_dir()
    assert runner._LIVE_ERROR_REPORT_OUT.is_file()

    report = runner.run(live=False, sample_tickers=runner.DEFAULT_SAMPLE_TICKERS, verbose=False)
    assert report["mode"] == "dry_run"
    # The default dry-run is commit-safe: live-only output is gone.
    assert not runner._RAW_DIR.exists(), "raw/ must be removed by a dry-run"
    assert not runner._NORM_DIR.exists(), "normalized/ must be removed by a dry-run"
    assert not runner._LIVE_ERROR_REPORT_OUT.exists(), \
        "fmp_live_error_report.csv must be removed by a dry-run"
    # The always-on artifacts survive.
    assert runner._REPORT_OUT.is_file()
    assert runner._NORM_MANIFEST_OUT.is_file()


def test_live_error_report_created_on_failure(monkeypatch):
    """A live run whose requests all fail must write a sanitized error report
    (no key, no key-bearing URL) and recommend ERROR. Runs fully offline by
    monkeypatching the key check + the network call - no real FMP_API_KEY."""
    runner = _runner()
    client = _client()
    monkeypatch.setattr(client, "has_api_key", lambda: True)

    def _boom(endpoint, params=None, **kwargs):
        raise client.FmpError("provider returned HTTP 403", status_code=403,
                              error_type="http_error")

    monkeypatch.setattr(client, "get_json", _boom)
    report = runner.run(live=True, max_tickers=2, max_endpoints=3,
                        sample_tickers=runner.DEFAULT_SAMPLE_TICKERS, verbose=False)
    try:
        assert report["mode"] == "live_sample"
        assert report["recommendation"] == "ERROR"
        assert os.path.isfile(runner._LIVE_ERROR_REPORT_OUT)
        rows = _read_csv(runner._LIVE_ERROR_REPORT_OUT)
        assert rows, "live error report should contain at least one failed-request row"
        required_cols = {"endpoint_name", "ticker", "request_url_redacted", "http_status",
                         "error_type", "error_message_sanitized", "likely_cause", "next_action"}
        assert required_cols <= set(rows[0].keys())
        for r in rows:
            assert r["http_status"] == "403"
            assert "apikey=" not in r["request_url_redacted"].lower()
            assert "<API_KEY_REDACTED>" in r["request_url_redacted"]
            assert "403" in r["likely_cause"]
            assert r["next_action"]
    finally:
        # Restore canonical dry-run artifacts for any later test.
        monkeypatch.undo()
        runner.run(live=False, sample_tickers=runner.DEFAULT_SAMPLE_TICKERS, verbose=False)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
