"""Phase 5-E1E - tests for the bounded SimFin Free fundamentals collector.

Verifies the collector runs fully OFFLINE in dry-run (no network, no API key, no
package import), emits every committed-safe artifact, records the free-tier 12-month
delay, keeps usable_for_live_trading_today False, separates bank from standard
statements, treats a derived-ratio failure as non-blocking, never writes the key or
raw/normalized data to git, and never touches Paper Trader / GCP / deploy / order /
broker / automation paths.

The live path uses the OFFICIAL simfin package / bulk-download workflow (load each
dataset once, filter the universe locally) - NOT a per-ticker web API. It is exercised
with an INJECTED fake bulk loader (no real network, no package) and an INJECTED temp
api key + temp dirs - tests never delete or touch real SimFin data under
research/data/simfin/.
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

_RUNNER_MODULE = "research.run_phase5e1e_simfin_free_collector"
_FAKE_KEY = "SIMFIN_FAKE_TEST_KEY_DO_NOT_LEAK_0123456789"

# A small injected universe with standard names AND known banks.
_STD = ["AAPL", "MSFT", "AMZN", "NVDA", "APH", "ABT", "ACN"]
_BANKS = ["JPM", "BAC", "C", "WFC"]
_UNIVERSE = _STD + _BANKS


def _runner():
    return importlib.import_module(_RUNNER_MODULE)


def _read_csv(path):
    with open(path, "r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# Deterministic fake of the official simfin bulk loader. NO network, NO package.
# Faithful to SimFin's real schema: each company lives in exactly ONE template, so the
# standard statement datasets carry only non-banks and the dedicated *_banks datasets
# carry only banks. ``full_rows`` simulates the whole-market dataset before the LOCAL
# filter the runner applies to the universe.
def _make_fake_loader(call_log=None, with_derived=True):
    bank_set = set(_BANKS)
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
        want = set(want_tickers or [])
        is_bank_loader = loader_name.endswith("_banks")
        # A company appears only in its own template's dataset.
        scoped = want & bank_set if is_bank_loader else want - bank_set
        if loader_name == "companies":
            cols = ["Ticker", "SimFinId", "Company Name", "Industry"]
            recs = [{"Ticker": tk, "SimFinId": 1, "Company Name": "%s Inc." % tk,
                     "Industry": "x"} for tk in sorted(want)]
            return {"columns": cols, "records": recs, "full_rows": 5000}
        if loader_name.startswith("derived"):
            if not with_derived:
                return None
            cols = ["Ticker", "Fiscal Year", "Fiscal Period", "Net Profit Margin"]
            recs = [{"Ticker": tk, "Fiscal Year": "2023", "Fiscal Period": "Q1",
                     "Net Profit Margin": 0.1} for tk in sorted(scoped)]
            return {"columns": cols, "records": recs, "full_rows": 30000}
        return {"columns": base_cols, "records": _rows(scoped),
                "full_rows": 40000}

    return _loader


@pytest.fixture(scope="module")
def dry(tmp_path_factory):
    """Run the dry-run once into temp dirs; return (runner, report, out_dir)."""
    runner = _runner()
    out_dir = tmp_path_factory.mktemp("e1e_out")
    data_dir = tmp_path_factory.mktemp("e1e_data")
    report = runner.run(out_dir=out_dir, data_dir=data_dir, universe=_UNIVERSE,
                        verbose=False)
    return runner, report, out_dir


def _live(runner, tmp_path, **kw):
    kw.setdefault("live", True)
    kw.setdefault("api_key", _FAKE_KEY)
    kw.setdefault("package_available", True)
    kw.setdefault("universe", _UNIVERSE)
    kw.setdefault("out_dir", tmp_path / "o")
    kw.setdefault("data_dir", tmp_path / "d")
    kw.setdefault("verbose", False)
    return runner.run(**kw)


# --------------------------------------------------------------------------- #
# Dry-run: offline, no key, no package load, no network
# --------------------------------------------------------------------------- #
def test_runner_imports_and_phase():
    assert _runner().PHASE == "5-E1E"
    assert _runner().PROVIDER == "SimFin"


def test_dry_run_works_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("SIMFIN_API_KEY", raising=False)
    runner = _runner()
    report = runner.run(out_dir=tmp_path / "o", data_dir=tmp_path / "d",
                        universe=_UNIVERSE, verbose=False)
    assert report["mode"] == "dry_run"
    assert report["api_key_present"] is False
    assert report["recommendation"] == runner.REC_READY_LIVE
    assert report["network_used"] is False


def test_dry_run_no_network_never_invokes_loader(tmp_path, monkeypatch):
    runner = _runner()
    monkeypatch.delenv("SIMFIN_API_KEY", raising=False)

    def _boom(*a, **k):
        raise AssertionError("dry-run must not load any dataset")

    monkeypatch.setattr(runner, "_make_default_loader", lambda *a, **k: _boom)
    report = runner.run(out_dir=tmp_path / "o", data_dir=tmp_path / "d",
                        universe=_UNIVERSE, verbose=False)
    assert report["network_used"] is False
    assert report["recommendation"] == runner.REC_READY_LIVE


def test_recommendation_in_allowed_vocab(dry):
    runner, report, _ = dry
    assert report["recommendation"] in runner.ALLOWED_RECOMMENDATIONS
    assert set(report["allowed_recommendations"]) == set(runner.ALLOWED_RECOMMENDATIONS)


def test_main_returns_zero(tmp_path, monkeypatch):
    monkeypatch.delenv("SIMFIN_API_KEY", raising=False)
    assert _runner().main([]) == 0


# --------------------------------------------------------------------------- #
# Access method + no per-ticker web API
# --------------------------------------------------------------------------- #
def test_access_method_is_official_package(dry):
    runner, report, _ = dry
    assert report["access_method"] == "official_simfin_package_or_bulk"
    assert report["access_method"] == runner.ACCESS_BULK
    assert report["package_required"] is True
    assert report["package_install_command"].endswith("-m pip install simfin")


def test_source_has_no_per_ticker_web_api():
    src = inspect.getsource(_runner()).lower()
    for marker in ("urllib", "_http_get_json", "_build_request", "requests.get(",
                   "prod.simfin.com", "api/v3"):
        assert marker not in src, "per-ticker web API surface present: %s" % marker


def test_source_has_no_forbidden_execution_surfaces():
    src = inspect.getsource(_runner()).lower()
    for marker in ("gcloud", "place_order(", "submit_order(", "create_order(",
                   "subprocess", "os.system(", "d:\\\\", "d:/"):
        assert marker not in src, "forbidden marker in source: %s" % marker


def test_source_never_deletes_data():
    src = inspect.getsource(_runner())
    for marker in ("rmtree", ".unlink(", "os.remove(", "os.rmdir(", "shutil.rmtree"):
        assert marker not in src, "source must never delete data: %s" % marker


# --------------------------------------------------------------------------- #
# Artifacts present (10), text-only, no key
# --------------------------------------------------------------------------- #
def test_writes_all_committed_safe_artifacts(dry):
    runner, _, out_dir = dry
    for name in (runner._REPORT_OUT.name, runner._COLLECTION_PLAN_OUT.name,
                 runner._UNIVERSE_COVERAGE_OUT.name, runner._SCHEMA_CATALOG_OUT.name,
                 runner._ROW_COUNTS_OUT.name, runner._BANK_VS_STD_OUT.name,
                 runner._QUALITY_OUT.name, runner._PIT_OUT.name,
                 runner._SECRET_AUDIT_OUT.name, runner._E2_PLAN_OUT.name):
        assert (out_dir / name).is_file(), "missing artifact: %s" % name


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


def test_main_json_has_required_fields(dry):
    _, report, _ = dry
    for field in ("phase", "provider", "mode", "package_present", "api_key_present",
                  "api_key_logged", "free_tier", "free_history_years",
                  "free_data_delay_months", "usable_for_live_trading_today",
                  "universe_source", "universe_size", "datasets_loaded",
                  "dataset_row_counts", "standard_ticker_coverage_count",
                  "bank_ticker_coverage_count", "total_ticker_coverage_count",
                  "derived_ratios_available", "derived_ratios_required_for_next_phase",
                  "ratios_can_be_computed_internally", "point_in_time_safe_for_research",
                  "raw_files_written_count", "normalized_files_written_count",
                  "recommendation", "recommended_next_phase", "preview_only",
                  "orders_enabled", "automation_enabled", "broker_execution_enabled",
                  "production_replacement"):
        assert field in report, "missing required main-JSON field: %s" % field
    assert report["phase"] == "5-E1E"
    assert report["api_key_logged"] is False
    assert report["free_tier"] is True
    assert report["free_history_years"] == 5
    assert report["free_data_delay_months"] == 12
    assert report["derived_ratios_required_for_next_phase"] is False
    assert report["ratios_can_be_computed_internally"] is True


# --------------------------------------------------------------------------- #
# Free-tier facts, safety posture
# --------------------------------------------------------------------------- #
def test_free_tier_delay_recorded_as_12_months(dry):
    runner, report, out_dir = dry
    assert report["free_data_delay_months"] == 12
    rows = _read_csv(out_dir / runner._PIT_OUT.name)
    delay = next(r for r in rows if r["aspect"] == "free_data_delay_months")
    assert delay["value"] == "12"
    assert delay["safe_for_research_backtest"] == "yes"
    assert delay["safe_for_live_trading_today"] == "no"


def test_usable_for_live_trading_today_is_false(dry):
    _, report, _ = dry
    assert report["usable_for_live_trading_today"] is False
    assert report["point_in_time_safe_for_research"] is True


def test_safety_contract_flags(dry):
    _, report, _ = dry
    assert report["preview_only"] is True
    for flag in ("orders_enabled", "automation_enabled", "broker_execution_enabled",
                 "production_replacement", "writes_to_d_drive", "modifies_paper_trader",
                 "modifies_gcp", "installs_packages", "trains_model", "deploys"):
        assert report[flag] is False


def test_e2_plan_is_preview_only(dry):
    runner, _, out_dir = dry
    plan = json.loads((out_dir / runner._E2_PLAN_OUT.name).read_text(encoding="utf-8"))
    assert plan["phase"] == "5-E2"
    assert plan["preview_only"] is True
    assert plan["usable_for_live_trading_today"] is False
    assert plan["orders_enabled"] is False
    assert plan["derived_ratios_required_for_next_phase"] is False
    assert plan["ratios_can_be_computed_internally"] is True


def test_gitignore_keeps_simfin_data_out_of_git(dry):
    runner, report, _ = dry
    assert report["raw_normalized_gitignored"] is True
    gi = runner._DATA_DIR / ".gitignore"
    assert gi.is_file()
    text = gi.read_text(encoding="utf-8")
    assert "raw/" in text and "normalized/" in text and "*" in text


def test_phase5c_universe_source_resolves(tmp_path, monkeypatch):
    """When universe_source=phase5c and the committed CSV exists, the resolved source
    is 'phase5c' and the universe is the ~128 names (capped by max_tickers)."""
    monkeypatch.delenv("SIMFIN_API_KEY", raising=False)
    runner = _runner()
    report = runner.run(out_dir=tmp_path / "o", data_dir=tmp_path / "d",
                        universe_source="phase5c", max_tickers=128, verbose=False)
    assert report["universe_source"] == "phase5c"
    assert report["universe_size"] > 100


# --------------------------------------------------------------------------- #
# Live path (INJECTED fake bulk loader + temp api key; no real network, temp dirs)
# --------------------------------------------------------------------------- #
def test_live_collects_quarterly_and_writes_local(tmp_path):
    runner = _runner()
    out_dir, data_dir = tmp_path / "o", tmp_path / "d"
    report = _live(runner, tmp_path, out_dir=out_dir, data_dir=data_dir,
                   simfin_loader=_make_fake_loader())
    assert report["mode"] == "live_collection"
    assert report["api_key_present"] is True
    assert report["package_present"] is True
    assert report["network_used"] is True
    assert report["recommendation"] == runner.REC_READY_E2
    assert report["total_ticker_coverage_count"] == len(_UNIVERSE)
    assert report["standard_ticker_coverage_count"] == len(_STD)
    assert report["bank_ticker_coverage_count"] == len(_BANKS)
    assert report["raw_files_written_count"] > 0
    assert report["normalized_files_written_count"] > 0
    # raw/normalized written ONLY under the temp data dir.
    assert list((data_dir / "raw").rglob("*.json")), "expected raw payloads under temp data dir"
    assert list((data_dir / "normalized").rglob("*.csv")), "expected normalized CSVs under temp data dir"


def test_live_loads_each_dataset_once_not_per_ticker(tmp_path):
    runner = _runner()
    call_log = []
    _live(runner, tmp_path, simfin_loader=_make_fake_loader(call_log))
    # 9 declared bulk datasets, each loaded exactly once (dedicated bank loaders present
    # here, so no standard fallback re-load). Far fewer than (tickers x datasets).
    assert len(call_log) == len(runner.BULK_DATASETS)
    assert len(call_log) <= 9
    multi = [c for c in call_log if len(c[3]) != 1]
    assert multi, "loader should be called with ticker sets, not one ticker per call"
    assert all(c[1] == "us" for c in call_log)
    assert all(c[2] == "quarterly" for c in call_log if c[0] != "companies")


def test_live_separates_bank_and_standard(tmp_path):
    runner = _runner()
    out_dir = tmp_path / "o"
    report = _live(runner, tmp_path, out_dir=out_dir, simfin_loader=_make_fake_loader())
    rows = _read_csv(out_dir / runner._BANK_VS_STD_OUT.name)
    templates = {r["template"] for r in rows}
    assert {"standard", "banks"}.issubset(templates)
    bank_row = next(r for r in rows if r["template"] == "banks")
    assert int(bank_row["covered_names"]) == len(_BANKS)
    assert report["bank_template_separate"] is True
    # coverage report routes banks to the bank template.
    cov = _read_csv(out_dir / runner._UNIVERSE_COVERAGE_OUT.name)
    jpm = next(r for r in cov if r["ticker"] == "JPM")
    assert jpm["resolved_template"] == "banks"


def test_live_derived_failure_is_not_blocking(tmp_path):
    """Derived ratios are optional: if they fail to load, the collection is still ready
    (statements present) and derived_ratios_available is False."""
    runner = _runner()
    report = _live(runner, tmp_path, simfin_loader=_make_fake_loader(with_derived=False))
    assert report["derived_ratios_available"] is False
    assert report["recommendation"] == runner.REC_READY_E2
    assert report["total_ticker_coverage_count"] == len(_UNIVERSE)


def test_live_discovers_banks_in_standard_when_no_bank_loaders(tmp_path):
    """If the package ships NO dedicated *_banks loaders, banks must still be collected
    via the standard datasets and bank_template_separate recorded False."""
    runner = _runner()

    def _no_bank_loaders(loader_name, market, variant, want_tickers):
        if loader_name.endswith("_banks"):
            return None
        if loader_name == "companies":
            recs = [{"Ticker": tk, "SimFinId": 1} for tk in sorted(set(want_tickers or []))]
            return {"columns": ["Ticker", "SimFinId"], "records": recs, "full_rows": 5000}
        if loader_name.startswith("derived"):
            return None
        recs = []
        for tk in sorted(set(want_tickers or [])):
            for fy, fp in (("2023", "Q1"), ("2022", "Q4"), ("2021", "Q2")):
                recs.append({"Ticker": tk, "Fiscal Year": fy, "Fiscal Period": fp})
        return {"columns": ["Ticker", "Fiscal Year", "Fiscal Period"],
                "records": recs, "full_rows": 40000}

    report = _live(runner, tmp_path, simfin_loader=_no_bank_loaders)
    # All names (incl. banks) collected via the standard datasets.
    assert report["standard_ticker_coverage_count"] == len(_UNIVERSE)
    assert report["bank_ticker_coverage_count"] == 0
    assert report["bank_template_separate"] is False
    assert report["recommendation"] == runner.REC_READY_E2


def test_live_partial_coverage_needs_more(tmp_path):
    """Below the readiness gate -> NEEDS_MORE_SIMFIN_COVERAGE (not READY)."""
    runner = _runner()
    covered = {"AAPL", "MSFT"}  # only 2 of 11 -> well below the 80% gate

    def _sparse(loader_name, market, variant, want_tickers):
        sel = sorted(set(want_tickers or []) & covered)
        if loader_name == "companies":
            return {"columns": ["Ticker"], "records": [{"Ticker": t} for t in sel],
                    "full_rows": 5000}
        if loader_name.startswith("derived") or loader_name.endswith("_banks"):
            return None
        recs = []
        for tk in sel:
            for fy, fp in (("2023", "Q1"), ("2022", "Q4")):
                recs.append({"Ticker": tk, "Fiscal Year": fy, "Fiscal Period": fp})
        return {"columns": ["Ticker", "Fiscal Year", "Fiscal Period"],
                "records": recs, "full_rows": 40000}

    report = _live(runner, tmp_path, simfin_loader=_sparse)
    assert report["total_ticker_coverage_count"] == 2
    assert report["recommendation"] == runner.REC_NEEDS_MORE


def test_live_zero_coverage_uses_sec_fallback(tmp_path):
    runner = _runner()

    def _empty(loader_name, market, variant, want_tickers):
        if loader_name == "companies":
            return {"columns": ["Ticker"], "records": [], "full_rows": 5000}
        return {"columns": ["Ticker", "Fiscal Year", "Fiscal Period"],
                "records": [], "full_rows": 0}

    report = _live(runner, tmp_path, simfin_loader=_empty)
    assert report["total_ticker_coverage_count"] == 0
    assert report["recommendation"] == runner.REC_USE_SEC


def test_live_missing_key_is_blocked(tmp_path, monkeypatch):
    runner = _runner()
    monkeypatch.delenv("SIMFIN_API_KEY", raising=False)
    report = runner.run(live=True, out_dir=tmp_path / "o", data_dir=tmp_path / "d",
                        universe=_UNIVERSE, package_available=True, verbose=False)
    assert report["recommendation"] == runner.REC_BLOCKED_NO_KEY
    assert report["api_key_present"] is False
    assert report["network_used"] is False


def test_live_missing_package_blocks_cleanly(tmp_path):
    runner = _runner()

    def _must_not_load(*a, **k):
        raise AssertionError("no dataset may be loaded when the package is missing")

    report = runner.run(live=True, out_dir=tmp_path / "o", data_dir=tmp_path / "d",
                        universe=_UNIVERSE, api_key=_FAKE_KEY, package_available=False,
                        simfin_loader=_must_not_load, verbose=False)
    assert report["recommendation"] == runner.REC_BLOCKED_PKG
    assert report["package_present"] is False
    assert report["package_blocked"] is True
    assert "pip install simfin" in report["package_install_command"]
    assert report["network_used"] is False
    assert report["raw_files_written_count"] == 0


def test_live_key_never_written_to_any_artifact(tmp_path):
    runner = _runner()
    out_dir, data_dir = tmp_path / "o", tmp_path / "d"
    _live(runner, tmp_path, out_dir=out_dir, data_dir=data_dir,
          simfin_loader=_make_fake_loader())
    for base in (out_dir, data_dir):
        for path in base.rglob("*"):
            if path.is_file():
                assert _FAKE_KEY not in path.read_text(encoding="utf-8", errors="replace"), \
                    "API key leaked into %s" % path
