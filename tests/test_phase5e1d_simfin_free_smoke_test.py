"""Phase 5-E1D - tests for the SimFin Free live smoke test (official package / bulk).

Verifies the smoke test runs fully OFFLINE in dry-run (no network, no API key, no
package import), writes every committed-safe artifact, records the free-tier 12-month
delay, keeps usable_for_live_trading_today False, tracks banks separately from
standard companies, never writes the key or raw/normalized data to git, and never
touches Paper Trader / GCP / deploy / order / broker / automation paths.

The live path now uses the OFFICIAL simfin package / bulk-download workflow (download
each dataset once, then filter the test tickers locally) - NOT a per-ticker web API.
It is exercised here with an INJECTED fake bulk loader (no real network, no package)
and an INJECTED temp api key, writing only to temp folders - tests never delete or
touch real SimFin data under research/data/simfin/. A missing package is proven to
block cleanly (BLOCKED_NEEDS_SIMFIN_PACKAGE) with no download.
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

_RUNNER_MODULE = "research.run_phase5e1d_simfin_free_smoke_test"
_FAKE_KEY = "SIMFIN_FAKE_TEST_KEY_DO_NOT_LEAK_0123456789"


def _runner():
    return importlib.import_module(_RUNNER_MODULE)


def _read_csv(path):
    with open(path, "r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# A deterministic fake of the official simfin bulk loader. NO network, NO package.
# Returns quarterly rows spanning several fiscal years for the requested tickers, for
# every loader (including dedicated *_banks loaders), so a standard company AND a bank
# both verify. ``full_rows`` simulates the size of the whole-market dataset before the
# LOCAL filter the runner applies.
def _make_fake_loader(call_log=None):
    base_cols = ["Ticker", "SimFinId", "Currency", "Fiscal Year", "Fiscal Period",
                 "Report Date", "Revenue", "Net Income"]

    def _rows(tickers):
        recs = []
        for tk in sorted(set(tickers or [])):
            for fy, fp in (("2023", "Q1"), ("2022", "Q4"), ("2021", "Q2"),
                           ("2020", "Q3"), ("2019", "Q1")):
                recs.append({"Ticker": tk, "SimFinId": 1, "Currency": "USD",
                             "Fiscal Year": fy, "Fiscal Period": fp,
                             "Report Date": "%s-03-31" % fy, "Revenue": 100,
                             "Net Income": 10})
        return recs

    def _loader(loader_name, market, variant, want_tickers):
        if call_log is not None:
            call_log.append((loader_name, market, variant,
                             tuple(sorted(want_tickers or []))))
        if loader_name == "companies":
            cols = ["Ticker", "SimFinId", "Company Name", "Industry"]
            recs = [{"Ticker": tk, "SimFinId": 1, "Company Name": "%s Inc." % tk,
                     "Industry": "x"} for tk in sorted(set(want_tickers or []))]
            return {"columns": cols, "records": recs, "full_rows": 5000}
        return {"columns": base_cols, "records": _rows(want_tickers),
                "full_rows": 40000}

    return _loader


@pytest.fixture(scope="module")
def dry(tmp_path_factory):
    """Run the dry-run once into temp dirs; return (runner, report, out_dir)."""
    runner = _runner()
    out_dir = tmp_path_factory.mktemp("e1d_out")
    data_dir = tmp_path_factory.mktemp("e1d_data")
    report = runner.run(out_dir=out_dir, data_dir=data_dir, verbose=False)
    return runner, report, out_dir


# --------------------------------------------------------------------------- #
# Dry-run: offline, no key, no package load, no network
# --------------------------------------------------------------------------- #
def test_runner_imports_and_phase():
    assert _runner().PHASE == "5-E1D"
    assert _runner().PROVIDER == "SimFin"


def test_dry_run_works_without_api_key_or_package(tmp_path, monkeypatch):
    monkeypatch.delenv("SIMFIN_API_KEY", raising=False)
    runner = _runner()
    report = runner.run(out_dir=tmp_path / "o", data_dir=tmp_path / "d", verbose=False)
    assert report["mode"] == "dry_run"
    assert report["api_key_present"] is False
    assert report["recommendation"] == runner.REC_READY_LIVE
    assert report["network_used"] is False


def test_dry_run_never_invokes_the_bulk_loader(tmp_path, monkeypatch):
    """Even if the default loader would explode, dry-run must never call it."""
    runner = _runner()
    monkeypatch.delenv("SIMFIN_API_KEY", raising=False)

    def _boom(*a, **k):
        raise AssertionError("dry-run must not load any dataset")

    monkeypatch.setattr(runner, "_make_default_loader", lambda *a, **k: _boom)
    report = runner.run(out_dir=tmp_path / "o", data_dir=tmp_path / "d", verbose=False)
    assert report["network_used"] is False
    assert report["recommendation"] == runner.REC_READY_LIVE


def test_recommendation_in_allowed_vocab(dry):
    runner, report, _ = dry
    assert report["recommendation"] in runner.ALLOWED_RECOMMENDATIONS


# --------------------------------------------------------------------------- #
# Access-method correction surfaced in the report
# --------------------------------------------------------------------------- #
def test_access_method_is_official_package_not_web_api(dry):
    runner, report, _ = dry
    assert report["access_method"] == "official_simfin_package_or_bulk"
    assert report["access_method"] == runner.ACCESS_BULK
    assert report["package_required"] is True
    assert report["web_api_per_ticker_deprecated"] is True
    assert report["live_web_api_result"] == "rejected_due_500_429"
    assert report["package_install_command"].endswith("-m pip install simfin")


def test_source_has_no_per_ticker_web_api_for_fundamentals():
    """The deprecated per-ticker web API must be gone: no urllib, no v3 statement URL,
    no per-ticker HTTP getter. Fundamentals come from the bulk package only."""
    src = inspect.getsource(_runner()).lower()
    for marker in ("urllib", "_http_get_json", "_build_request",
                   "companies/statements/verbose", "prod.simfin.com", "api/v3"):
        assert marker not in src, "deprecated per-ticker web API surface present: %s" % marker


# --------------------------------------------------------------------------- #
# Artifacts present (now 9, including the new package probe), text-only, no key
# --------------------------------------------------------------------------- #
def test_writes_all_committed_safe_artifacts(dry):
    runner, _, out_dir = dry
    for name in (runner._REPORT_OUT.name, runner._DATASET_PLAN_OUT.name,
                 runner._REQUEST_PLAN_OUT.name, runner._SCHEMA_PROBE_OUT.name,
                 runner._COVERAGE_OUT.name, runner._PIT_OUT.name,
                 runner._SECRET_AUDIT_OUT.name, runner._PACKAGE_PROBE_OUT.name,
                 runner._COLLECTOR_PLAN_OUT.name):
        assert (out_dir / name).is_file(), "missing artifact: %s" % name


def test_package_probe_report_has_required_columns(dry):
    runner, _, out_dir = dry
    rows = _read_csv(out_dir / runner._PACKAGE_PROBE_OUT.name)
    assert rows, "package probe report should have a summary row"
    required = {"package_present", "package_version", "access_method",
                "datasets_attempted", "datasets_loaded", "rows_loaded",
                "columns_detected", "test_tickers_found", "quarterly_dates_found",
                "recommendation"}
    assert required.issubset(set(rows[0].keys()))
    assert rows[0]["access_method"] == "official_simfin_package_or_bulk"


def test_artifacts_are_text_not_binary(dry):
    _, _, out_dir = dry
    for path in out_dir.iterdir():
        raw = path.read_bytes()
        assert b"\x00" not in raw, "binary NUL byte in artifact: %s" % path.name
        raw.decode("utf-8")


def test_no_key_in_dry_run_outputs(dry):
    _, _, out_dir = dry
    for path in out_dir.iterdir():
        text = path.read_text(encoding="utf-8").lower()
        assert "apikey=" not in text
        assert "api-key=" not in text


# --------------------------------------------------------------------------- #
# Free-tier facts, banks-vs-standard separation, safety posture
# --------------------------------------------------------------------------- #
def test_free_tier_delay_recorded_as_12_months(dry):
    runner, report, out_dir = dry
    assert report["free_data_delay_months"] == 12
    assert report["free_history_years"] == 5
    rows = _read_csv(out_dir / runner._PIT_OUT.name)
    delay = next(r for r in rows if r["aspect"] == "free_data_delay_months")
    assert delay["value"] == "12"
    assert delay["safe_for_research_backtest"] == "yes"
    assert delay["safe_for_live_trading_today"] == "no"


def test_usable_for_live_trading_today_is_false(dry):
    _, report, _ = dry
    assert report["usable_for_live_trading_today"] is False
    assert report["point_in_time_safe_for_research"] is True


def test_bank_datasets_tracked_separately(dry):
    runner, report, out_dir = dry
    assert report["standard_tickers_tested"] == ["AAPL", "MSFT", "AMZN", "NVDA", "APH", "ABT", "ACN"]
    assert report["bank_tickers_tested"] == ["JPM", "BAC", "C"]
    # Dataset access plan declares a dedicated banks template with bank-named datasets.
    rows = _read_csv(out_dir / runner._DATASET_PLAN_OUT.name)
    templates = {r["company_template"] for r in rows}
    assert {"standard", "banks"}.issubset(templates)
    bank_names = {r["dataset_name"] for r in rows if r["company_template"] == "banks"}
    assert any("(Banks)" in n for n in bank_names)


def test_safety_contract_flags(dry):
    _, report, _ = dry
    assert report["preview_only"] is True
    for flag in ("orders_enabled", "automation_enabled", "broker_execution_enabled",
                 "production_replacement", "writes_to_d_drive", "modifies_paper_trader",
                 "modifies_gcp", "installs_packages", "builds_collector"):
        assert report[flag] is False


def test_collector_plan_is_preview_only_and_bulk(dry):
    runner, _, out_dir = dry
    plan = json.loads((out_dir / runner._COLLECTOR_PLAN_OUT.name).read_text(encoding="utf-8"))
    assert plan["preview_only"] is True
    assert plan["usable_for_live_trading_today"] is False
    assert plan["orders_enabled"] is False
    assert plan["access_method"] == "official_simfin_package_or_bulk"
    assert plan["package_required"] is True


# --------------------------------------------------------------------------- #
# Source-level guards: no deletion of real data, no forbidden surfaces
# --------------------------------------------------------------------------- #
def test_source_never_deletes_data():
    src = inspect.getsource(_runner())
    for marker in ("rmtree", ".unlink(", "os.remove(", "os.rmdir(", "shutil.rmtree"):
        assert marker not in src, "source must never delete data: %s" % marker


def test_source_has_no_forbidden_execution_surfaces():
    src = inspect.getsource(_runner()).lower()
    # No execution/deploy/D:-write code. (The literal install command appears only as a
    # documentation string; the runner never executes it - no subprocess/os.system.)
    for marker in ("gcloud", "place_order(", "submit_order(", "create_order(",
                   "subprocess", "os.system(", "d:\\\\", "d:/"):
        assert marker not in src, "forbidden marker in source: %s" % marker


def test_gitignore_keeps_simfin_data_out_of_git(dry):
    runner, report, _ = dry
    assert report["raw_normalized_gitignored"] is True
    gi = runner._DATA_DIR / ".gitignore"
    assert gi.is_file()
    text = gi.read_text(encoding="utf-8")
    assert "raw/" in text and "normalized/" in text and "*" in text


def test_main_returns_zero(tmp_path, monkeypatch):
    monkeypatch.delenv("SIMFIN_API_KEY", raising=False)
    assert _runner().main([]) == 0


# --------------------------------------------------------------------------- #
# Live path (INJECTED fake bulk loader + temp api key; no real network, temp dirs)
# --------------------------------------------------------------------------- #
def test_live_with_fake_loader_verifies_quarterly_and_writes_local(tmp_path):
    runner = _runner()
    out_dir, data_dir = tmp_path / "o", tmp_path / "d"
    call_log = []
    report = runner.run(live=True, max_tickers=10, out_dir=out_dir, data_dir=data_dir,
                        api_key=_FAKE_KEY, package_available=True,
                        simfin_loader=_make_fake_loader(call_log), verbose=False)
    assert report["mode"] == "live_smoke"
    assert report["api_key_present"] is True
    assert report["package_present"] is True
    assert report["network_used"] is True
    # Quarterly verified for both a standard company and a bank -> ready for collector.
    assert report["recommendation"] == runner.REC_READY_E1E
    assert report["schema_verified"] is True
    assert report["raw_files_written_count"] > 0
    assert report["normalized_files_written_count"] > 0
    # Bulk dataset cache/raw/normalized written ONLY under the temp data dir.
    assert (data_dir / "raw").is_dir()
    assert (data_dir / "normalized").is_dir()
    assert list((data_dir / "raw").rglob("*.json")), "expected raw payloads under temp data dir"


def test_live_loads_each_dataset_once_not_per_ticker(tmp_path):
    """Prove the bulk workflow: one load per dataset, never one per ticker."""
    runner = _runner()
    call_log = []
    runner.run(live=True, max_tickers=10, out_dir=tmp_path / "o", data_dir=tmp_path / "d",
               api_key=_FAKE_KEY, package_available=True,
               simfin_loader=_make_fake_loader(call_log), verbose=False)
    # 9 declared bulk datasets, each loaded exactly once (banks ship dedicated loaders
    # here, so no standard-template fallback fires). Far fewer than 10 tickers x datasets.
    assert len(call_log) == len(runner.BULK_DATASETS)
    assert len(call_log) <= 9
    # Each call requests a SET of tickers, never a single ticker per call.
    multi_ticker_calls = [c for c in call_log if len(c[3]) != 1]
    assert multi_ticker_calls, "loader should be called with ticker sets, not one ticker per call"
    # market/variant are passed through as required.
    assert all(c[1] == "us" for c in call_log)
    assert all(c[2] == "quarterly" for c in call_log if c[0] != "companies")


def test_live_discovers_banks_in_standard_when_no_bank_loaders(tmp_path):
    """If the package ships NO dedicated *_banks loaders, banks must still verify via
    the standard datasets, and the report records bank_template_separate = False."""
    runner = _runner()

    def _loader_no_bank_endpoints(loader_name, market, variant, want_tickers):
        if loader_name.endswith("_banks"):
            return None  # package has no dedicated bank loaders
        if loader_name == "companies":
            recs = [{"Ticker": tk, "SimFinId": 1} for tk in sorted(set(want_tickers or []))]
            return {"columns": ["Ticker", "SimFinId"], "records": recs, "full_rows": 5000}
        # Standard statement loaders carry whatever tickers were requested (incl. banks).
        recs = []
        for tk in sorted(set(want_tickers or [])):
            for fy, fp in (("2023", "Q1"), ("2022", "Q4"), ("2021", "Q2")):
                recs.append({"Ticker": tk, "Fiscal Year": fy, "Fiscal Period": fp})
        return {"columns": ["Ticker", "Fiscal Year", "Fiscal Period"],
                "records": recs, "full_rows": 40000}

    report = runner.run(live=True, max_tickers=10, out_dir=tmp_path / "o",
                        data_dir=tmp_path / "d", api_key=_FAKE_KEY,
                        package_available=True, simfin_loader=_loader_no_bank_endpoints,
                        verbose=False)
    assert report["recommendation"] == runner.REC_READY_E1E
    assert report["bank_template_separate"] is False


def test_live_key_never_written_to_any_artifact(tmp_path):
    runner = _runner()
    out_dir, data_dir = tmp_path / "o", tmp_path / "d"
    runner.run(live=True, max_tickers=10, out_dir=out_dir, data_dir=data_dir,
               api_key=_FAKE_KEY, package_available=True,
               simfin_loader=_make_fake_loader(), verbose=False)
    # The fake key must not appear in ANY committed artifact NOR any local data file.
    for base in (out_dir, data_dir):
        for path in base.rglob("*"):
            if path.is_file():
                assert _FAKE_KEY not in path.read_text(encoding="utf-8", errors="replace"), \
                    "API key leaked into %s" % path


def test_live_missing_key_is_blocked(tmp_path, monkeypatch):
    runner = _runner()
    monkeypatch.delenv("SIMFIN_API_KEY", raising=False)
    report = runner.run(live=True, out_dir=tmp_path / "o", data_dir=tmp_path / "d",
                        package_available=True, verbose=False)
    assert report["recommendation"] == runner.REC_BLOCKED_NO_KEY
    assert report["api_key_present"] is False
    assert report["network_used"] is False


def test_live_missing_package_blocks_cleanly_with_install_command(tmp_path):
    """A missing simfin package must return BLOCKED_NEEDS_SIMFIN_PACKAGE cleanly (no
    stack trace), surface the exact install command, and make NO download."""
    runner = _runner()

    def _must_not_load(*a, **k):
        raise AssertionError("no dataset may be loaded when the package is missing")

    report = runner.run(live=True, out_dir=tmp_path / "o", data_dir=tmp_path / "d",
                        api_key=_FAKE_KEY, package_available=False,
                        simfin_loader=_must_not_load, verbose=False)
    assert report["recommendation"] == runner.REC_BLOCKED_PKG
    assert report["package_present"] is False
    assert report["package_blocked"] is True
    assert report["package_install_command"].endswith("-m pip install simfin")
    assert "pip install simfin" in report["package_install_command"]
    assert report["network_used"] is False
    assert report["raw_files_written_count"] == 0
