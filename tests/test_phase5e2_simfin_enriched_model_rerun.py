"""Tests for Phase 5-E2 — SimFin-Enriched Cross-Sectional Model Rerun.

All tests are offline and key-less. Heavy paths run on small **synthetic**
inputs injected straight into ``run(...)`` — no D: drive, no network, no real
SimFin data is read or deleted.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import math
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNNER_PATH = _REPO_ROOT / "research" / "run_phase5e2_simfin_enriched_model_rerun.py"
_DATA_GITIGNORE = _REPO_ROOT / "research" / "data" / "simfin" / ".gitignore"


def _load_runner():
    spec = importlib.util.spec_from_file_location("phase5e2_runner", str(_RUNNER_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_runner()


# --------------------------------------------------------------------------- #
# Synthetic data builders (deterministic; evaluable but ~zero true signal)
# --------------------------------------------------------------------------- #
def _epoch_ms(y: int, m: int, d: int) -> int:
    return int(dt.datetime(y, m, d, tzinfo=dt.timezone.utc).timestamp() * 1000)


def _business_days(start: dt.date, end: dt.date):
    out = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            out.append(cur.isoformat())
        cur += dt.timedelta(days=1)
    return out


def _synth_price_history(n_std=28, n_bank=5, seed=7):
    rng = np.random.RandomState(seed)
    dates = _business_days(dt.date(2017, 1, 2), dt.date(2024, 12, 31))
    tickers = ["SPY"] + [f"S{i:02d}" for i in range(n_std)] + [f"B{i}" for i in range(n_bank)]
    hist = {}
    for t in tickers:
        px = 100.0
        drift = 0.0003 + rng.normal(0, 0.0002)
        series = {}
        for d in dates:
            px *= (1.0 + drift + rng.normal(0, 0.012))
            px = max(px, 1.0)
            series[d] = (px, px * 1_000_000.0)
        hist[t] = series
    return hist, [f"S{i:02d}" for i in range(n_std)], [f"B{i}" for i in range(n_bank)]


def _quarters(y0=2016, y1=2024):
    qs = []
    for y in range(y0, y1 + 1):
        for qi, (q, em) in enumerate([("Q1", (3, 31)), ("Q2", (6, 30)),
                                      ("Q3", (9, 30)), ("Q4", (12, 31))]):
            qs.append((y, q, em[0], em[1]))
    return qs


def _publish(y, m, d):
    # period end + ~45 days
    base = dt.date(y, m, d) + dt.timedelta(days=45)
    return _epoch_ms(base.year, base.month, base.day)


def _std_pl_row(t, y, q, m, d, i):
    rev = 1000.0 + 50.0 * i + 10.0 * (y - 2016)
    return {
        "Ticker": t, "Fiscal Year": str(y), "Fiscal Period": q,
        "Report Date": _epoch_ms(y, m, d), "Publish Date": _publish(y, m, d),
        "Shares (Diluted)": 100.0 + i, "Revenue": rev,
        "Gross Profit": rev * 0.45, "Operating Income (Loss)": rev * 0.25,
        "Net Income": rev * 0.18,
    }


def _std_bs_row(t, y, q, m, d, i):
    ta = 5000.0 + 100.0 * i
    return {
        "Ticker": t, "Fiscal Year": str(y), "Fiscal Period": q,
        "Report Date": _epoch_ms(y, m, d), "Publish Date": _publish(y, m, d),
        "Total Assets": ta, "Total Liabilities": ta * 0.55, "Total Equity": ta * 0.45,
        "Total Current Assets": ta * 0.4, "Total Current Liabilities": ta * 0.2,
        "Short Term Debt": ta * 0.05, "Long Term Debt": ta * 0.2,
        "Cash, Cash Equivalents & Short Term Investments": ta * 0.15,
    }


def _std_cf_row(t, y, q, m, d, i):
    return {
        "Ticker": t, "Fiscal Year": str(y), "Fiscal Period": q,
        "Report Date": _epoch_ms(y, m, d), "Publish Date": _publish(y, m, d),
        "Net Cash from Operating Activities": 200.0 + i,
        "Change in Fixed Assets & Intangibles": -(50.0 + i),
    }


def _bank_pl_row(t, y, q, m, d, i):
    rev = 800.0 + 40.0 * i
    return {
        "Ticker": t, "Fiscal Year": str(y), "Fiscal Period": q,
        "Report Date": _epoch_ms(y, m, d), "Publish Date": _publish(y, m, d),
        "Shares (Diluted)": 80.0 + i, "Revenue": rev,
        "Operating Income (Loss)": rev * 0.3, "Net Income": rev * 0.2,
    }


def _bank_bs_row(t, y, q, m, d, i):
    ta = 9000.0 + 200.0 * i
    return {
        "Ticker": t, "Fiscal Year": str(y), "Fiscal Period": q,
        "Report Date": _epoch_ms(y, m, d), "Publish Date": _publish(y, m, d),
        "Total Assets": ta, "Total Liabilities": ta * 0.9, "Total Equity": ta * 0.1,
        "Total Deposits": ta * 0.6, "Net Loans": ta * 0.5,
        "Short Term Debt": ta * 0.05, "Long Term Debt": ta * 0.1,
    }


def _bank_cf_row(t, y, q, m, d, i):
    return {
        "Ticker": t, "Fiscal Year": str(y), "Fiscal Period": q,
        "Report Date": _epoch_ms(y, m, d), "Publish Date": _publish(y, m, d),
        "Net Cash from Operating Activities": 300.0 + i,
    }


def _synth_simfin_tables(std_tickers, bank_tickers):
    tables = {"standard": {"pl": [], "bs": [], "cf": []},
              "banks": {"pl": [], "bs": [], "cf": []}}
    qs = _quarters()
    for i, t in enumerate(std_tickers):
        for (y, q, m, d) in qs:
            tables["standard"]["pl"].append(_std_pl_row(t, y, q, m, d, i))
            tables["standard"]["bs"].append(_std_bs_row(t, y, q, m, d, i))
            tables["standard"]["cf"].append(_std_cf_row(t, y, q, m, d, i))
    for i, t in enumerate(bank_tickers):
        for (y, q, m, d) in qs:
            tables["banks"]["pl"].append(_bank_pl_row(t, y, q, m, d, i))
            tables["banks"]["bs"].append(_bank_bs_row(t, y, q, m, d, i))
            tables["banks"]["cf"].append(_bank_cf_row(t, y, q, m, d, i))
    return tables


@pytest.fixture(scope="module")
def synth():
    hist, std, banks = _synth_price_history()
    tables = _synth_simfin_tables(std, banks)
    return {"hist": hist, "std": std, "banks": banks, "tables": tables}


@pytest.fixture(scope="module")
def full_report(mod, synth, tmp_path_factory):
    out = tmp_path_factory.mktemp("e2_full")
    return mod.run(price_history=synth["hist"], simfin_tables=synth["tables"],
                   out_dir=out, write=True, verbose=False), out


# --------------------------------------------------------------------------- #
# Contract / source-level tests (no heavy data)
# --------------------------------------------------------------------------- #
def test_phase_and_provider(mod):
    assert mod.PHASE == "5-E2"
    assert mod.PROVIDER == "SimFin"


def test_recommendation_vocabulary(mod):
    assert set(mod.ALLOWED_RECOMMENDATIONS) == {
        "READY_FOR_PHASE5F_DEPLOYABLE_SCORER",
        "FUNDAMENTALS_IMPROVE_BUT_NOT_DEPLOYABLE",
        "NO_INCREMENTAL_EDGE_USE_PRICE_ONLY",
        "DATA_COVERAGE_BLOCKER",
        "PIT_SAFETY_BLOCKER",
        "ERROR",
    }


def test_no_network_or_key_or_forbidden_surfaces():
    src = _RUNNER_PATH.read_text(encoding="utf-8")
    low = src.lower()
    # No live network / API client surfaces.
    for tok in ("import simfin", "import urllib", "import requests", "import socket",
                "urlopen", "requests.get", "requests.post", "http://", "https://",
                "set_api_key", "simfin_api_key", "prod.simfin.com", "api/v3"):
        assert tok not in low, f"forbidden network/api token present: {tok!r}"
    # No order / broker / automation / deploy / D: write surfaces.
    for tok in ("create_order", "place_order", "broker.", "automation_on",
                "subprocess", "os.system", "deploy(", "gcloud"):
        assert tok not in low, f"forbidden execution token present: {tok!r}"


def test_uses_local_simfin_only_flag(full_report):
    report, _ = full_report
    di = report["data_inputs"]
    assert di["uses_local_simfin_only"] is True
    assert di["live_api_calls"] is False
    assert di["simfin_downloads"] is False
    assert di["api_key_required"] is False


def test_data_gitignore_keeps_payloads_uncommitted():
    assert _DATA_GITIGNORE.is_file(), "research/data/simfin/.gitignore must exist"
    body = _DATA_GITIGNORE.read_text(encoding="utf-8")
    assert "raw" in body and "normalized" in body


def test_outputs_never_under_simfin_data_dir():
    # Fresh module: assert the DEFAULT artifact paths (run() mutates globals when
    # given out_dir, so use a pristine import here).
    fresh = _load_runner()
    for p in (fresh._REPORT_OUT, fresh._FEATURE_CATALOG_OUT, fresh._PANEL_SAMPLE_OUT,
              fresh._SCOREBOARD_OUT, fresh._COVERAGE_OUT, fresh._GATE_MATRIX_OUT,
              fresh._INCREMENTAL_EDGE_OUT, fresh._PHASE5F_PLAN_OUT):
        norm = str(p).replace("\\", "/")
        assert "data/simfin" not in norm
        assert "research/output/phase5e2" in norm


# --------------------------------------------------------------------------- #
# Point-in-time / lag / leakage
# --------------------------------------------------------------------------- #
def test_availability_date_prefers_publish_then_lagged_report(mod):
    row_pub = {"Publish Date": _epoch_ms(2022, 5, 15), "Report Date": _epoch_ms(2022, 3, 31)}
    assert mod._availability_date(row_pub) == "2022-05-15"
    row_norep = {"Publish Date": "", "Report Date": _epoch_ms(2022, 3, 31)}
    got = mod._availability_date(row_norep)
    expected = (dt.date(2022, 3, 31) + dt.timedelta(days=mod.FALLBACK_REPORT_LAG_DAYS)).isoformat()
    assert got == expected


def test_asof_join_excludes_future_statements(mod):
    stmts = [
        {"avail_date": "2021-05-15", "order": 1, "items": {}},
        {"avail_date": "2021-08-15", "order": 2, "items": {}},
        {"avail_date": "2021-11-15", "order": 3, "items": {}},
    ]
    # As-of just after the 2nd publish -> pick index 1, never the future 3rd.
    assert mod._asof_index(stmts, "2021-09-01") == 1
    # As-of before any publish -> None (no lookahead).
    assert mod._asof_index(stmts, "2021-01-01") is None


def test_lag_assumption_is_explicit_in_report(full_report):
    report, _ = full_report
    pit = report["point_in_time"]
    assert pit["lag_assumption"]
    assert "Publish Date" in pit["lag_assumption"]
    assert pit["free_data_delay_months"] == 12
    assert pit["usable_for_live_trading_today"] is False
    assert pit["point_in_time_safe_for_research"] is True


def test_no_lookahead_in_full_run(full_report):
    report, _ = full_report
    assert report["max_lookahead_violation_days"] == 0
    assert report["point_in_time_safe"] is True


def test_placebo_ic_collapses(full_report):
    report, _ = full_report
    placebo = report.get("placebo_mean_ic")
    assert placebo is None or abs(placebo) <= 0.05  # label-shuffle must kill signal


# --------------------------------------------------------------------------- #
# Fundamental feature math (derived ratios computed internally)
# --------------------------------------------------------------------------- #
def test_standard_fundamental_features_computed(mod):
    stmts = [{
        "order": 4, "fiscal_year": "2021", "fiscal_period": "Q4",
        "items": {
            "Revenue": 1000.0, "Gross Profit": 450.0, "Operating Income (Loss)": 250.0,
            "Net Income": 180.0, "Total Assets": 5000.0, "Total Liabilities": 2750.0,
            "Total Equity": 2250.0, "Total Current Assets": 2000.0,
            "Total Current Liabilities": 1000.0, "Short Term Debt": 250.0,
            "Long Term Debt": 1000.0,
            "Cash, Cash Equivalents & Short Term Investments": 750.0,
            "Net Cash from Operating Activities": 200.0,
            "Change in Fixed Assets & Intangibles": -50.0,
        },
    }]
    feats = mod.compute_fundamental_features(stmts, 0, "standard", market_cap=None)
    assert feats["gross_margin"] == pytest.approx(0.45)
    assert feats["operating_margin"] == pytest.approx(0.25)
    assert feats["net_margin"] == pytest.approx(0.18)
    assert feats["liabilities_to_assets"] == pytest.approx(0.55)
    assert feats["debt_to_assets"] == pytest.approx(0.25)
    assert feats["current_ratio"] == pytest.approx(2.0)
    assert feats["fcf_to_assets"] == pytest.approx((200.0 - 50.0) / 5000.0)
    # bank-only features are absent (None) for a standard company.
    assert feats["deposits_to_assets"] is None
    assert feats["loans_to_assets"] is None


def test_bank_fundamental_features_separate(mod):
    stmts = [{
        "order": 4, "fiscal_year": "2021", "fiscal_period": "Q4",
        "items": {
            "Revenue": 800.0, "Operating Income (Loss)": 240.0, "Net Income": 160.0,
            "Total Assets": 9000.0, "Total Liabilities": 8100.0, "Total Equity": 900.0,
            "Total Deposits": 5400.0, "Net Loans": 4500.0,
            "Net Cash from Operating Activities": 300.0,
        },
    }]
    feats = mod.compute_fundamental_features(stmts, 0, "bank", market_cap=None)
    assert feats["equity_to_assets"] == pytest.approx(0.1)
    assert feats["deposits_to_assets"] == pytest.approx(0.6)
    assert feats["loans_to_assets"] == pytest.approx(0.5)
    # standard-only features do not apply to banks.
    assert feats["gross_margin"] is None
    assert feats["current_ratio"] is None


def test_missing_derived_ratios_not_blocking(full_report):
    report, _ = full_report
    assert report["derived_ratios_available"] is False
    assert report["ratios_computed_internally"] is True
    # The enriched models still evaluate despite no SimFin-provided ratios.
    assert report["scoreboard"]["price_plus_fundamentals"]["evaluable"] is True


# --------------------------------------------------------------------------- #
# Model comparison includes the Phase 5-C baseline
# --------------------------------------------------------------------------- #
def test_scoreboard_has_baseline_and_enriched_models(full_report):
    report, _ = full_report
    sb = report["scoreboard"]
    for m in ("price_only_full_panel_reference", "price_only_baseline",
              "fundamentals_only", "price_plus_fundamentals",
              "price_plus_fundamentals_quality"):
        assert m in sb
    assert sb["price_only_baseline"]["evaluable"] is True


def test_incremental_edge_block_present(full_report):
    report, _ = full_report
    ie = report["incremental_edge"]
    assert "baseline_mean_rank_ic" in ie
    assert "combined_mean_rank_ic" in ie
    assert "delta_mean_rank_ic" in ie
    assert isinstance(ie["fundamentals_beat_price_only"], bool)


def test_baseline_comparison_gate_passes(full_report):
    report, _ = full_report
    g = {x["gate_name"]: x for x in report["validation_gate_matrix"]}
    assert g["baseline_comparison_present"]["status"] == "PASS"
    assert g["bank_standard_handled"]["status"] == "PASS"
    assert g["no_lookahead_join"]["status"] == "PASS"
    assert g["missing_derived_ratios_not_blocking"]["status"] == "PASS"


def test_bank_template_names_discovered(full_report, synth):
    report, _ = full_report
    assert sorted(report["data_coverage"]["bank_template_names"]) == sorted(synth["banks"])
    assert report["data_coverage"]["bank_rows_with_fundamentals"] > 0


# --------------------------------------------------------------------------- #
# Recommendation + safety + artifacts
# --------------------------------------------------------------------------- #
def test_recommendation_in_allowed(full_report, mod):
    report, _ = full_report
    assert report["recommendation"] in mod.ALLOWED_RECOMMENDATIONS


def test_safety_flags(full_report):
    report, _ = full_report
    for flag in ("orders_enabled", "automation_enabled", "broker_execution_enabled",
                 "production_replacement", "writes_to_d_drive", "modifies_paper_trader",
                 "modifies_gcp", "deploys", "creates_binary_model_artifact"):
        assert report[flag] is False
    assert report["preview_only"] is True


def test_all_ten_artifacts_written(full_report):
    _, out = full_report
    expected = [
        "phase5e2_simfin_enriched_model_rerun.json",
        "simfin_enriched_feature_catalog.csv",
        "simfin_enriched_panel_sample.csv",
        "simfin_enriched_model_scoreboard.csv",
        "simfin_enriched_ic_by_year.csv",
        "simfin_enriched_decile_spread.csv",
        "simfin_enriched_coverage_report.csv",
        "simfin_enriched_validation_gate_matrix.csv",
        "simfin_enriched_incremental_edge_report.csv",
        "phase5f_deployable_scorer_plan.json",
    ]
    for name in expected:
        assert (out / name).is_file(), f"missing artifact: {name}"


def test_artifacts_are_text_not_binary(full_report):
    _, out = full_report
    for p in out.iterdir():
        assert p.suffix in (".json", ".csv"), f"non-text artifact: {p.name}"
        head = p.read_bytes()[:4096]
        assert b"\x00" not in head, f"binary content in {p.name}"


def test_feature_families_reported(full_report):
    report, _ = full_report
    assert set(report["feature_families"]) >= {
        "profitability", "growth", "leverage", "liquidity_quality", "valuation", "bank_specific"}


def test_phase5f_plan_gated_on_recommendation(full_report):
    _, out = full_report
    plan = json.loads((out / "phase5f_deployable_scorer_plan.json").read_text(encoding="utf-8"))
    assert plan["phase"] == "5-F"
    assert plan["preview_only"] is True
    assert plan["orders_enabled"] is False
    # proceed_to_5f is True only when the rerun recommends the deployable scorer.
    rep = json.loads((out / "phase5e2_simfin_enriched_model_rerun.json").read_text(encoding="utf-8"))
    assert plan["proceed_to_5f"] == (rep["recommendation"] == "READY_FOR_PHASE5F_DEPLOYABLE_SCORER")


# --------------------------------------------------------------------------- #
# Blocker paths (missing inputs) — graceful, never crash
# --------------------------------------------------------------------------- #
def test_missing_simfin_data_blocks(mod, synth, tmp_path):
    report = mod.run(price_history=synth["hist"], simfin_tables={"standard": {}, "banks": {}},
                     out_dir=tmp_path, write=True, verbose=False)
    assert report["recommendation"] == "DATA_COVERAGE_BLOCKER"


def test_missing_price_history_blocks(mod, synth, tmp_path):
    report = mod.run(price_history={}, simfin_tables=synth["tables"],
                     out_dir=tmp_path, write=True, verbose=False)
    assert report["recommendation"] == "DATA_COVERAGE_BLOCKER"


def test_run_does_not_read_or_delete_real_simfin_data(mod, synth, tmp_path):
    # Injected tables + injected price history => the real data tree is never touched.
    before = sorted(p.name for p in (_REPO_ROOT / "research" / "data" / "simfin").glob("**/*")
                    if p.is_file()) if (_REPO_ROOT / "research" / "data" / "simfin").exists() else []
    mod.run(price_history=synth["hist"], simfin_tables=synth["tables"],
            out_dir=tmp_path, write=True, verbose=False)
    after = sorted(p.name for p in (_REPO_ROOT / "research" / "data" / "simfin").glob("**/*")
                   if p.is_file()) if (_REPO_ROOT / "research" / "data" / "simfin").exists() else []
    assert before == after  # nothing added or removed in the payload tree
