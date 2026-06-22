"""Tests for Phase 5-F0B — Alpha Signal Quality Upgrade.

These tests run fully offline on a synthetic in-memory price history (no network,
no API key, no SimFin/FMP, no D: read). They verify the safety contract, the
quality-control candidate set (confidence / regime / horizon / turnover / industry
controls), the Phase 5-C comparison, the recommendation vocabulary, and that the
optional local industry metadata is used only for the Ticker -> IndustryId map.
"""
from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNNER = _REPO_ROOT / "research" / "run_phase5f0b_signal_quality_upgrade.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("phase5f0b_under_test", str(_RUNNER))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _business_days(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d.isoformat()
        d += timedelta(days=1)


def _synth_price_history(n_tickers: int = 36, seed: int = 7,
                         start=date(2015, 1, 2), end=date(2023, 12, 31)):
    """SPY + ``n_tickers`` seeded geometric random walks with dollar volume.
    Returns {ticker: {iso_date: (close, dollar_vol)}}."""
    rng = np.random.RandomState(seed)
    days = list(_business_days(start, end))
    n = len(days)
    hist = {}
    # SPY: gentle upward drift so the regime proxy has both risk-on / risk-off spells
    spy = 100.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.011, n)))
    hist["SPY"] = {d: (float(spy[i]), float(spy[i] * 1e7)) for i, d in enumerate(days)}
    for k in range(n_tickers):
        drift = rng.normal(0.0004, 0.0003)
        vol = rng.uniform(0.012, 0.022)
        px = 50.0 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
        dv = px * rng.uniform(5e6, 5e7)
        tk = f"T{k:03d}"
        hist[tk] = {d: (float(px[i]), float(dv[i])) for i, d in enumerate(days)}
    return hist


@pytest.fixture(scope="module")
def mod():
    return _load_runner()


@pytest.fixture(scope="module")
def synth():
    return _synth_price_history()


@pytest.fixture(scope="module")
def synth_industry(synth):
    # assign every synthetic ticker a sector so industry controls are exercised
    sectors = ["101", "102", "103", "104", "105"]
    return {t: sectors[i % len(sectors)]
            for i, t in enumerate(sorted(k for k in synth if k != "SPY"))}


@pytest.fixture(scope="module")
def full_report(mod, synth, synth_industry, tmp_path_factory):
    out = tmp_path_factory.mktemp("phase5f0b_out")
    rep = mod.run(price_history=synth, out_dir=out, industry_map=synth_industry,
                  weekly=True, write=True, verbose=False)
    return rep, out


# --------------------------------------------------------------------------- #
# Identity / vocabulary
# --------------------------------------------------------------------------- #
def test_phase_identifier(mod, full_report):
    rep, _ = full_report
    assert mod.PHASE == "5-F0B"
    assert rep["phase"] == "5-F0B"


def test_recommended_next_phase_is_shadow(full_report):
    rep, _ = full_report
    assert "shadow" in rep["recommended_next_phase"].lower()


def test_recommendation_vocabulary_exact(mod):
    assert set(mod.ALLOWED_RECOMMENDATIONS) == {
        "READY_FOR_PHASE5F1_SHADOW_CANDIDATE",
        "EDGE_PRESENT_BUT_NEEDS_SHADOW_ONLY",
        "NO_QUALITY_IMPROVEMENT",
        "DATA_BLOCKER",
        "ERROR",
    }


def test_recommendation_is_allowed(mod, full_report):
    rep, _ = full_report
    assert rep["recommendation"] in mod.ALLOWED_RECOMMENDATIONS


def test_recommendation_not_forced_ready(full_report):
    # On synthetic random-walk data there is no real edge; must NOT claim shadow-ready.
    rep, _ = full_report
    assert rep["recommendation"] != "READY_FOR_PHASE5F1_SHADOW_CANDIDATE"
    assert rep["any_candidate_ready_for_shadow"] is False


# --------------------------------------------------------------------------- #
# Safety contract
# --------------------------------------------------------------------------- #
def test_safety_flags(full_report):
    rep, _ = full_report
    assert rep["preview_only"] is True
    assert rep["shadow_only"] is True
    for flag in ("orders_enabled", "automation_enabled", "broker_execution_enabled",
                 "production_replacement", "network_used", "paid_apis_used", "deployed",
                 "binary_artifacts_created", "live_trading", "writes_to_d_drive",
                 "modifies_paper_trader", "modifies_gcp", "uses_simfin_fundamentals_as_alpha",
                 "provider_work", "packages_installed"):
        assert rep[flag] is False, flag


def test_no_api_key_required(full_report):
    rep, _ = full_report
    assert rep["paid_apis_used"] is False
    assert rep["network_used"] is False
    assert rep["provider_work"] is False


def test_source_has_no_network_or_provider_dependency():
    src = _RUNNER.read_text(encoding="utf-8")
    forbidden = [
        "import requests", "import urllib", "urllib.request", "http.client",
        "socket.socket", "import simfin", "from simfin", "set_api_key",
        "os.environ", ".getenv(", "financialmodelingprep", "alphavantage",
    ]
    for pat in forbidden:
        assert pat not in src, f"forbidden dependency/network pattern present: {pat!r}"


def test_no_fundamentals_used_as_alpha():
    """The optional local metadata file must be read for Ticker/IndustryId only —
    never for fundamentals statements or derived ratios."""
    src = _RUNNER.read_text(encoding="utf-8")
    # must not read SimFin statement files
    for pat in ["/pl.csv", "/bs.csv", "/cf.csv", "pl.csv", "bs.csv", "cf.csv"]:
        assert pat not in src, f"reads a SimFin statement file: {pat!r}"
    # must not compute / reference fundamental ratios as features
    for pat in ["gross_margin", "operating_margin", "net_margin", "roa_ttm",
                "earnings_yield", "book_to_price", "debt_to_assets"]:
        assert pat not in src, f"references a fundamental ratio: {pat!r}"
    # the report flag is explicit
    assert "uses_simfin_fundamentals_as_alpha" in src


def test_industry_map_uses_only_ticker_and_industryid(mod, tmp_path):
    # a metadata file with extra (fundamentals-adjacent) columns must be ignored
    p = tmp_path / "general.csv"
    p.write_text(
        "Ticker,SimFinId,Company Name,IndustryId,Revenue,NetIncome\n"
        "AAA,1,Alpha,101001.0,999,888\n"
        "BBB,2,Beta,102003.0,777,666\n",
        encoding="utf-8")
    m = mod.load_industry_map(p)
    assert m == {"AAA": "101", "BBB": "102"}  # sector = first 3 IndustryId digits


def test_missing_industryid_column_yields_empty_map(mod, tmp_path):
    p = tmp_path / "no_industry.csv"
    p.write_text("Ticker,Revenue\nAAA,5\n", encoding="utf-8")
    assert mod.load_industry_map(p) == {}


def test_d_drive_not_written(full_report):
    _, out = full_report
    assert "D:" not in str(out).upper()
    for f in out.iterdir():
        assert f.is_file()


# --------------------------------------------------------------------------- #
# Candidate set + quality controls
# --------------------------------------------------------------------------- #
def test_required_candidates_present(mod, full_report):
    rep, _ = full_report
    cm = rep["candidate_metrics"]
    for key in (mod.C_BASELINE, mod.C_ENSEMBLE, mod.C_HICONF, mod.C_RISKON,
                mod.C_WEEKLY, mod.C_BAND, mod.C_BLEND):
        assert key in cm, key
    # baseline + raw ensemble + the best candidate must be evaluable
    assert cm[mod.C_BASELINE]["evaluable"] is True
    assert cm[mod.C_ENSEMBLE]["evaluable"] is True


def test_confidence_gating_exists(mod, full_report):
    rep, _ = full_report
    assert "confidence_controls" in rep
    cc = rep["confidence_controls"]
    assert cc["dispersion_max"] == mod.CONF_DISPERSION_MAX
    assert cc["hi_rank"] == mod.CONF_HI_RANK and cc["lo_rank"] == mod.CONF_LO_RANK
    hc = rep["candidate_metrics"][mod.C_HICONF]
    assert hc["evaluable"] is True
    assert hc["has_explicit_gating"] is True
    # the no-trade zone removes part of the cross-section -> coverage < 1
    assert hc["rebalance_coverage"] is not None and hc["rebalance_coverage"] < 1.0


def test_regime_gating_exists(mod, full_report):
    rep, _ = full_report
    ro = rep["candidate_metrics"][mod.C_RISKON]
    # regime gate trades only in risk-on -> coverage strictly below 1.0
    assert ro["has_explicit_gating"] is True
    if ro["evaluable"]:
        assert ro["rebalance_coverage"] is not None and ro["rebalance_coverage"] < 1.0
    # base ensemble reports IC by regime (more than one regime label)
    assert len(rep["alpha_v3_reference_metrics"]["ic_by_regime"]) >= 1


def test_turnover_control_exists(mod, full_report):
    rep, _ = full_report
    cm = rep["candidate_metrics"]
    # entry/exit band must reduce turnover vs the raw ensemble (same ranking, less churn)
    band = cm[mod.C_BAND]
    ens = cm[mod.C_ENSEMBLE]
    assert band["evaluable"] and ens["evaluable"]
    assert band["top10_equal_avg_turnover"] < ens["top10_equal_avg_turnover"]
    # weekly cadence candidate is present (turnover/cost sensitivity)
    assert "weekly" in rep["rebalance_cadences"]
    assert cm[mod.C_WEEKLY]["cadence"] == "weekly"


def test_horizon_comparison_exists(full_report):
    rep, _ = full_report
    hz = rep["horizon_comparison"]
    assert isinstance(hz, list) and len(hz) >= 3
    signals = {row[0] for row in hz}
    assert any("ridge@5d" == s for s in signals)
    assert any("ridge@20d" == s for s in signals)
    assert any("blended" in s for s in signals)


def test_industry_control_available_on_synth(mod, full_report):
    rep, _ = full_report
    # with an injected industry map covering the universe, sector control is available
    assert rep["sector_control_available"] is True
    assert rep["sector_control_coverage"] >= mod.INDUSTRY_COVERAGE_MIN
    assert mod.C_INDCAP in rep["candidate_metrics"]


def test_industry_control_unavailable_without_map(mod, synth, tmp_path):
    rep = mod.run(price_history=synth, out_dir=tmp_path, industry_map={},
                  weekly=False, write=False, verbose=False)
    assert rep["sector_control_available"] is False
    assert mod.C_INDCAP in rep["candidates_unavailable"]


def test_risk_filters_reported(mod, full_report):
    rep, _ = full_report
    rf = rep["risk_filters"]
    assert rf["liquidity_floor_usd"] == mod.RISK_LIQUIDITY_FLOOR
    assert "ensemble_ic_before" in rf and "ensemble_ic_after" in rf
    assert rf["names_kept_fraction"] is not None and rf["names_kept_fraction"] <= 1.0


# --------------------------------------------------------------------------- #
# Phase 5-C comparison + best candidate
# --------------------------------------------------------------------------- #
def test_phase5c_comparison_exists(full_report):
    rep, _ = full_report
    assert rep["phase5c_reference_metrics"]["evaluable"] is True
    qi = rep["quality_improvement_vs_phase5c"]
    assert "delta_ic_best_minus_baseline" in qi
    assert "best_beats_baseline_on_ic" in qi
    assert qi["decision_metric"].startswith("top10")


def test_best_candidate_is_a_contender(mod, full_report):
    rep, _ = full_report
    best = rep["best_candidate"]
    # best is never the baseline (the baseline is the thing to beat)
    assert best != mod.C_BASELINE
    assert best in rep["candidate_metrics"]
    assert rep["best_candidate_metrics"]["evaluable"] is True


def test_decision_metric_is_not_compounded_total(full_report):
    """The cross-candidate decision must use the cadence-comparable annualized mean,
    not the survivorship-explosive compounded total return."""
    rep, _ = full_report
    qi = rep["quality_improvement_vs_phase5c"]
    assert "best_top10_net50_ann_mean" in qi
    assert "ann mean" in qi["note"].lower() or "annualized" in qi["decision_metric"].lower()


# --------------------------------------------------------------------------- #
# Leakage / placebo
# --------------------------------------------------------------------------- #
def test_placebo_present_and_collapses(full_report):
    rep, _ = full_report
    lc = rep["leakage_checks"]
    assert lc["labels_forward_only"] is True
    assert lc["features_use_past_only"] is True
    p = lc["placebo_mean_ic"]
    # placebo IC must collapse toward 0 under a within-date label shuffle
    assert p is None or abs(p) <= 0.05


def test_leakage_placebo_gate_pass(full_report):
    rep, _ = full_report
    g = {x["gate_name"]: x for x in rep["validation_gate_matrix"]}
    assert g["no_leakage_placebo_gate"]["status"] == "PASS"


# --------------------------------------------------------------------------- #
# Gate matrix integrity
# --------------------------------------------------------------------------- #
def test_gate_summary_counts_match(full_report):
    rep, _ = full_report
    gates = rep["validation_gate_matrix"]
    summ = rep["readiness_gate_summary"]
    assert sum(summ.values()) == len(gates)
    # the live safety gates must all PASS
    g = {x["gate_name"]: x["status"] for x in gates}
    for safe in ("preview_only_gate", "no_orders_gate", "no_broker_execution_gate",
                 "no_automation_gate", "no_binary_model_artifact_gate",
                 "no_fundamentals_as_alpha_gate", "shadow_only_not_live_gate"):
        assert g[safe] == "PASS", safe


# --------------------------------------------------------------------------- #
# Artifacts
# --------------------------------------------------------------------------- #
def test_nine_artifacts_written(full_report):
    _, out = full_report
    expected = [
        "phase5f0b_signal_quality_upgrade.json",
        "signal_quality_candidate_matrix.csv",
        "signal_quality_horizon_report.csv",
        "signal_quality_regime_report.csv",
        "signal_quality_turnover_cost_report.csv",
        "signal_quality_portfolio_report.csv",
        "signal_quality_industry_control_report.csv",
        "signal_quality_validation_gate_matrix.csv",
        "phase5f1_shadow_candidate_plan.json",
    ]
    for name in expected:
        assert (out / name).is_file(), name


def test_artifacts_are_text_not_binary(full_report):
    _, out = full_report
    for f in out.iterdir():
        head = f.read_bytes()[:4096]
        assert b"\x00" not in head, f"{f.name} looks binary"
        assert f.suffix in (".json", ".csv"), f.name


def test_shadow_plan_gated(mod, full_report):
    _, out = full_report
    import json
    plan = json.loads((out / "phase5f1_shadow_candidate_plan.json").read_text(encoding="utf-8"))
    rep, _ = full_report
    assert plan["proceed_to_shadow"] == (rep["recommendation"] == mod.REC_READY)
    assert plan["orders_enabled"] is False and plan["broker_execution_enabled"] is False


def test_candidate_matrix_has_ann_mean_column(full_report):
    _, out = full_report
    header = (out / "signal_quality_candidate_matrix.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "ann_mean_return" in header
    assert "survivorship_inflated" in header  # the total-return column is flagged


def test_default_output_paths_under_phase5f0b(mod):
    fresh = _load_runner()
    assert "phase5f0b_signal_quality_upgrade" in str(fresh._REPORT_OUT)
    assert fresh._REPORT_OUT.name == "phase5f0b_signal_quality_upgrade.json"


# --------------------------------------------------------------------------- #
# Data blocker + determinism
# --------------------------------------------------------------------------- #
def test_missing_price_history_data_blocker(mod, tmp_path):
    rep = mod.run(price_history={}, out_dir=tmp_path, industry_map={},
                  weekly=False, write=False, verbose=False)
    assert rep["recommendation"] == "DATA_BLOCKER"
    assert rep["universe_size"] == 0


def test_determinism(mod, synth, synth_industry, tmp_path):
    a = mod.run(price_history=synth, out_dir=tmp_path / "a", industry_map=synth_industry,
                weekly=False, write=False, verbose=False)
    b = mod.run(price_history=synth, out_dir=tmp_path / "b", industry_map=synth_industry,
                weekly=False, write=False, verbose=False)
    assert a["recommendation"] == b["recommendation"]
    assert a["best_candidate"] == b["best_candidate"]
    assert a["alpha_v3_reference_metrics"]["mean_rank_ic"] == \
        b["alpha_v3_reference_metrics"]["mean_rank_ic"]
