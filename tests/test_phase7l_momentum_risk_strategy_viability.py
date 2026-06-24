"""Tests for Phase 7-L — Momentum / Risk Strategy Viability Test.

Offline and deterministic. The unit tests use small synthetic panels; the two
end-to-end tests are guarded and skip when the local price panels (on D:) are absent,
so the suite runs anywhere while still exercising the full pipeline when data exists.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research import run_phase7l_momentum_risk_strategy_viability as L


# --------------------------------------------------------------------------- #
# Helpers.
# --------------------------------------------------------------------------- #
def _scores_frame(month_to_scores):
    """month_to_scores: {period_str: {ticker: z}} -> long [month, ticker, z]."""
    rows = []
    for m, d in month_to_scores.items():
        for t, z in d.items():
            rows.append({"month": pd.Period(m, "M"), "ticker": t, "z": z})
    return pd.DataFrame(rows, columns=["month", "ticker", "z"])


def _realized_frame(month_to_fwd):
    rows = []
    for m, d in month_to_fwd.items():
        for t, fv in d.items():
            rows.append({"month": pd.Period(m, "M"), "ticker": t, "fwd": fv})
    return pd.DataFrame(rows, columns=["month", "ticker", "fwd"])


def _result(net_sharpe, net_ret=0.2, gross_sharpe=1.0, mdd=-0.2, turnover=0.3,
            universe="u296", variant="mom_12_1", selection="top_decile"):
    return L._StrategyResult(
        universe=universe, variant=variant, selection=selection,
        sim={"period_keys": [], "gross": [], "traded": [], "one_sided_turnover": [],
             "n_selected": [], "max_weight": [], "effective_n": [], "weights_by_month": {}},
        gross_metrics={"sharpe": gross_sharpe, "ann_return": 0.25, "max_drawdown": mdd,
                       "ann_vol": 0.2, "hit_rate": 0.6, "n_periods": 100},
        net_metrics={"sharpe": net_sharpe, "ann_return": net_ret, "max_drawdown": mdd},
        cost_curve={}, mean_one_sided_turnover=turnover, avg_names=20.0,
        avg_max_weight=0.05, avg_effective_n=20.0)


# --------------------------------------------------------------------------- #
# Vocabulary / constants.
# --------------------------------------------------------------------------- #
def test_recommendation_vocabulary_exact_and_ordered():
    assert L.ALLOWED_RECOMMENDATIONS == (
        "MOMENTUM_STRATEGY_VIABLE_FOR_PAPER_RESEARCH",
        "MOMENTUM_SIGNAL_ONLY_WEAK",
        "MOMENTUM_RESEARCH_STOP",
        "ERROR",
    )


def test_config_constants():
    assert L.QUANTILES == {"top_decile": 10, "top_quintile": 5}
    assert L.COST_BPS_GRID == (0.0, 10.0, 25.0, 50.0)
    assert L.PRIMARY_COST_BPS == 25.0
    assert L.MOMENTUM_VARIANTS == ("mom_12_1", "mom_6_1", "mom_riskadj", "momentum_bucket")
    assert 0.0 < L.MAX_POSITION <= 1.0
    assert L.DD_FLOOR < 0 and 0 < L.DD_REL_SPY < 1 and 0 < L.TURNOVER_CEIL < 1


# --------------------------------------------------------------------------- #
# Capped equal weights.
# --------------------------------------------------------------------------- #
def test_capped_equal_weights_no_cap_binding():
    w = L._capped_equal_weights([f"T{i}" for i in range(20)], 0.10)
    assert pytest.approx(sum(w.values()), abs=1e-9) == 1.0
    assert all(abs(v - 0.05) < 1e-9 for v in w.values())


def test_capped_equal_weights_cap_binds_and_sums_to_one():
    # 4 names, cap 0.10 -> each would be 0.25 > cap; cap forces 0.10 each but cannot
    # reach 1.0 -> all hit the cap, sum is min(1, n*cap)=0.4 (no leverage, no >cap).
    w = L._capped_equal_weights(["A", "B", "C", "D"], 0.10)
    assert all(v <= 0.10 + 1e-9 for v in w.values())
    # 5 names, cap 0.30: equal 0.20 < cap -> plain equal weight summing to 1.
    w2 = L._capped_equal_weights(["A", "B", "C", "D", "E"], 0.30)
    assert pytest.approx(sum(w2.values()), abs=1e-9) == 1.0
    assert all(abs(v - 0.20) < 1e-9 for v in w2.values())


def test_capped_equal_weights_empty():
    assert L._capped_equal_weights([], 0.10) == {}


# --------------------------------------------------------------------------- #
# Long-only simulation.
# --------------------------------------------------------------------------- #
def test_simulate_long_only_selects_top_quantile_and_prices_returns():
    # 60 names so the 10% cap does NOT bind: q=5 -> top 12 names, weight 1/12 < 0.10.
    names = [f"T{i:02d}" for i in range(60)]
    scores = _scores_frame({"2020-01": {t: i for i, t in enumerate(names)},
                            "2020-02": {t: i for i, t in enumerate(names)}})
    # Forward returns: 0.10 for the top-12 names (index >= 48), 0.0 otherwise.
    fwd = {t: (0.10 if i >= 48 else 0.0) for i, t in enumerate(names)}
    realized = _realized_frame({"2020-01": fwd, "2020-02": fwd})
    sim = L.simulate_long_only(scores, realized, q=5)
    assert sim["n_selected"] == [12, 12]
    # Fully invested (cap not binding), each top name returns 0.10 -> portfolio 0.10.
    assert all(abs(g - 0.10) < 1e-9 for g in sim["gross"])
    assert all(abs(w - 1.0 / 12) < 1e-9 for w in sim["max_weight"])
    # Same holdings month to month -> second-period traded fraction is 0.
    assert sim["traded"][1] == pytest.approx(0.0, abs=1e-9)
    # First period buys the whole book -> traded fraction 1.0.
    assert sim["traded"][0] == pytest.approx(1.0, abs=1e-9)


def test_simulate_long_only_position_cap_binds_leaving_cash():
    # 20 names, q=5 -> 4 names; equal weight 0.25 would breach the 0.10 cap, so each is
    # capped at 0.10 and the book is only 40% invested (no leverage, cash drag).
    names = [f"T{i:02d}" for i in range(20)]
    scores = _scores_frame({"2020-01": {t: i for i, t in enumerate(names)}})
    fwd = {t: (0.10 if i >= 16 else 0.0) for i, t in enumerate(names)}
    realized = _realized_frame({"2020-01": fwd})
    sim = L.simulate_long_only(scores, realized, q=5)
    assert sim["n_selected"] == [4]
    assert sim["max_weight"][0] == pytest.approx(L.MAX_POSITION)
    # 4 names * 0.10 weight * 0.10 return = 0.04 (the uninvested 0.60 earns nothing).
    assert sim["gross"][0] == pytest.approx(0.04, abs=1e-9)


def test_simulate_long_only_skips_thin_cross_sections():
    names = [f"T{i}" for i in range(5)]  # below MIN_NAMES_PORT (20)
    scores = _scores_frame({"2020-01": {t: i for i, t in enumerate(names)}})
    realized = _realized_frame({"2020-01": {t: 0.01 for t in names}})
    sim = L.simulate_long_only(scores, realized, q=5)
    assert sim["gross"] == []


def test_simulate_long_only_turnover_on_full_rotation():
    names = [f"T{i:02d}" for i in range(60)]  # cap does not bind (q=5 -> 12 names)
    # Month 1 ranks ascending, month 2 ranks reversed -> top-quintile fully rotates.
    asc = {t: i for i, t in enumerate(names)}
    desc = {t: (59 - i) for i, t in enumerate(names)}
    scores = _scores_frame({"2020-01": asc, "2020-02": desc})
    realized = _realized_frame({"2020-01": {t: 0.0 for t in names},
                                "2020-02": {t: 0.0 for t in names}})
    sim = L.simulate_long_only(scores, realized, q=5)
    # Disjoint top-12 sets -> traded fraction = buy (1.0) + sell (1.0) = 2.0.
    assert sim["traded"][1] == pytest.approx(2.0, abs=1e-9)
    assert sim["one_sided_turnover"][1] == pytest.approx(1.0, abs=1e-9)


# --------------------------------------------------------------------------- #
# Metrics.
# --------------------------------------------------------------------------- #
def test_perf_metrics_basic():
    m = L.perf_metrics([0.10, -0.05, 0.10, -0.05] * 6)  # 24 months
    assert m["n_periods"] == 24
    assert m["ann_return"] is not None and m["ann_vol"] > 0
    assert m["sharpe"] is not None
    assert m["max_drawdown"] <= 0
    assert 0.0 <= m["hit_rate"] <= 1.0


def test_perf_metrics_empty():
    m = L.perf_metrics([])
    assert m["n_periods"] == 0 and m["ann_return"] is None and m["sharpe"] is None


def test_net_returns_subtracts_cost_proportional_to_traded():
    gross = [0.02, 0.02]
    traded = [1.0, 0.5]
    net = L.net_returns(gross, traded, 25.0)  # 25 bps = 0.0025
    assert net[0] == pytest.approx(0.02 - 1.0 * 0.0025)
    assert net[1] == pytest.approx(0.02 - 0.5 * 0.0025)
    # Zero cost is a no-op.
    assert L.net_returns(gross, traded, 0.0) == pytest.approx(gross)


def test_yearly_returns_groups_and_compounds():
    keys = ["2020-01", "2020-02", "2021-01"]
    rets = [0.10, 0.10, 0.05]
    yr = L.yearly_returns(keys, rets)
    assert yr["2020"] == pytest.approx(1.10 * 1.10 - 1.0)
    assert yr["2021"] == pytest.approx(0.05)


# --------------------------------------------------------------------------- #
# Regime partition + sector exposure.
# --------------------------------------------------------------------------- #
def test_returns_by_regime_partitions():
    keys = ["2020-01", "2020-02", "2020-03", "2020-04"]
    rets = [0.10, -0.10, 0.10, -0.10]
    labels = {"2020-01": "risk_on", "2020-02": "risk_off",
              "2020-03": "risk_on", "2020-04": "risk_off"}
    out = L.returns_by_regime(keys, rets, labels)
    assert out["risk_on"]["n_months"] == 2 and out["risk_off"]["n_months"] == 2
    assert out["risk_on"]["mean_monthly"] == pytest.approx(0.10)
    assert out["risk_off"]["mean_monthly"] == pytest.approx(-0.10)


def test_sector_exposure_coverage_and_top():
    weights = {"2020-01": {"AAA": 0.5, "BBB": 0.5},
               "2020-02": {"AAA": 0.5, "CCC": 0.5}}
    smap = {"AAA": "Tech", "BBB": "Tech", "CCC": "Energy"}  # full coverage
    se = L.sector_exposure(weights, smap)
    assert se["sector_map_coverage_fraction"] == pytest.approx(1.0)
    assert se["top_sector"] == "Tech"  # AAA(0.5)*2 + BBB(0.5) dominates


def test_sector_exposure_partial_coverage():
    weights = {"2020-01": {"AAA": 0.5, "ZZZ": 0.5}}
    smap = {"AAA": "Tech"}  # ZZZ unmapped
    se = L.sector_exposure(weights, smap)
    assert se["sector_map_coverage_fraction"] == pytest.approx(0.5)


# --------------------------------------------------------------------------- #
# Drawdown acceptability + decision rule.
# --------------------------------------------------------------------------- #
def test_dd_acceptable():
    assert L._dd_acceptable(-0.20, -0.25) is True            # shallower than SPY
    assert L._dd_acceptable(-0.61, -0.25) is False           # breaches absolute floor
    assert L._dd_acceptable(-0.45, -0.25) is False           # >15pp deeper than SPY
    assert L._dd_acceptable(None, -0.25) is False


def test_derive_recommendation_viable():
    best = _result(net_sharpe=1.4, mdd=-0.20, turnover=0.30)
    spy = {"sharpe": 0.9, "max_drawdown": -0.25}
    eqw = {"sharpe": 1.1, "max_drawdown": -0.24}
    rec, viable, detail = L.derive_recommendation(best, spy, eqw, [best])
    assert rec == L.REC_VIABLE and viable == "YES"
    assert detail["configs_independently_viable"] == 1


def test_derive_recommendation_stop_after_costs():
    best = _result(net_sharpe=-0.1, net_ret=-0.02)
    spy = {"sharpe": 0.9, "max_drawdown": -0.25}
    eqw = {"sharpe": 1.1, "max_drawdown": -0.24}
    rec, viable, _ = L.derive_recommendation(best, spy, eqw, [best])
    assert rec == L.REC_STOP and viable == "NO"


def test_derive_recommendation_stop_underperforms_benchmarks():
    # Positive net but does not beat the equal-weight universe -> STOP (underperforms).
    best = _result(net_sharpe=1.0, mdd=-0.20, turnover=0.30)
    spy = {"sharpe": 0.9, "max_drawdown": -0.25}
    eqw = {"sharpe": 1.2, "max_drawdown": -0.24}
    rec, viable, _ = L.derive_recommendation(best, spy, eqw, [best])
    assert rec == L.REC_STOP and viable == "NO"


def test_derive_recommendation_weak_when_beats_benchmarks_but_unattractive():
    # Positive net, beats both benchmarks, but drawdown is too deep -> WEAK.
    best = _result(net_sharpe=1.4, mdd=-0.61, turnover=0.30)  # breaches DD floor
    spy = {"sharpe": 0.9, "max_drawdown": -0.55}
    eqw = {"sharpe": 1.1, "max_drawdown": -0.50}
    rec, viable, detail = L.derive_recommendation(best, spy, eqw, [best])
    assert rec == L.REC_WEAK and viable == "NO"
    assert detail["drawdown_acceptable"] is False


def test_derive_recommendation_weak_when_turnover_too_high():
    best = _result(net_sharpe=1.4, mdd=-0.20, turnover=0.80)  # turnover over ceiling
    spy = {"sharpe": 0.9, "max_drawdown": -0.25}
    eqw = {"sharpe": 1.1, "max_drawdown": -0.24}
    rec, viable, _ = L.derive_recommendation(best, spy, eqw, [best])
    assert rec == L.REC_WEAK and viable == "NO"


def test_derive_recommendation_no_best():
    rec, viable, _ = L.derive_recommendation(None, {}, {}, [])
    assert rec == L.REC_STOP and viable == "NO"


def test_config_passes_matches_viability():
    spy = {"sharpe": 0.9, "max_drawdown": -0.25}
    eqw = {"sharpe": 1.1, "max_drawdown": -0.24}
    assert L._config_passes(_result(net_sharpe=1.4), spy, eqw) is True
    assert L._config_passes(_result(net_sharpe=1.0), spy, eqw) is False  # < eqw sharpe
    assert L._config_passes(_result(net_sharpe=-0.1, net_ret=-0.1), spy, eqw) is False


def test_pick_best_returns_max_net_sharpe():
    a = _result(net_sharpe=0.5, variant="mom_12_1")
    b = _result(net_sharpe=1.3, variant="mom_6_1")
    assert L.pick_best([a, b]) is b
    assert L.pick_best([]) is None


# --------------------------------------------------------------------------- #
# Gate matrix.
# --------------------------------------------------------------------------- #
def test_gate_matrix_safety_gates_present_and_clean():
    best = _result(net_sharpe=1.4)
    spy = {"sharpe": 0.9, "max_drawdown": -0.25}
    eqw = {"sharpe": 1.1, "max_drawdown": -0.24}
    _rec, _v, detail = L.derive_recommendation(best, spy, eqw, [best])
    gates, counts = L.build_gate_matrix(best, detail, spy, eqw, 16)
    names = {g["gate"] for g in gates}
    for required in ("no_factor_weight_optimization", "no_new_alpha_factors",
                     "failed_composite_excluded", "no_regime_selection_or_weighting",
                     "no_leverage", "no_trading_order_automation", "not_committed", "not_pushed"):
        assert required in names
    assert counts["safety_fail"] is False
    # Survivorship and history caveats must be surfaced (WARN, not hidden).
    warn_names = {g["gate"] for g in gates if g["status"] == "WARN"}
    assert "survivorship_caveat" in warn_names


# --------------------------------------------------------------------------- #
# Momentum score construction on a synthetic price panel.
# --------------------------------------------------------------------------- #
def _synthetic_prices(n_names=25, n_days=520, seed=3):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2018-01-01", periods=n_days)
    rows = []
    for i in range(n_names):
        drift = 0.0003 + 0.0002 * (i / n_names)  # cross-sectional dispersion in trend
        rets = rng.normal(drift, 0.01, size=n_days)
        px = 100.0 * np.cumprod(1.0 + rets)
        for d, p in zip(dates, px):
            rows.append({"ticker": f"S{i:02d}", "date": d, "adj_close": p, "volume": 1000})
    return pd.DataFrame(rows)


def test_build_momentum_scores_produces_four_variants():
    prices = _synthetic_prices()
    _pf, realized, variant_z = L.build_momentum_scores(prices)
    assert not realized.empty
    for k in ("mom_12_1", "mom_6_1", "mom_riskadj", "momentum_bucket"):
        assert k in variant_z
        assert list(variant_z[k].columns) == ["month", "ticker", "z"]
    assert not variant_z["momentum_bucket"].empty


def test_end_to_end_on_synthetic_panel_is_gradable():
    prices = _synthetic_prices(n_names=30, n_days=560)
    _pf, realized, variant_z = L.build_momentum_scores(prices)
    results = L.evaluate_strategies("usynth", realized, variant_z)
    assert results, "expected at least one gradable strategy"
    for r in results:
        assert r.gross_metrics["n_periods"] > 0
        assert r.net_metrics["sharpe"] is not None or r.gross_metrics["n_periods"] < 2


# --------------------------------------------------------------------------- #
# Guarded end-to-end (skips when the local price panels are absent).
# --------------------------------------------------------------------------- #
def _panels_present():
    return os.path.isfile(L.PRICE_CSV_128) and os.path.isfile(L.BROAD_PRICE_PANEL_CSV)


@pytest.mark.skipif(not _panels_present(), reason="local price panels (D:) not present")
def test_full_run_emits_all_artifacts_and_is_safe(tmp_path):
    report = L.run(out_dir=str(tmp_path))
    assert report.get("recommendation") in L.ALLOWED_RECOMMENDATIONS
    # All ten committed-safe artifacts exist.
    for name in ("phase7l_momentum_risk_strategy_viability.json",
                 "momentum_strategy_scoreboard.csv", "momentum_cost_sensitivity.csv",
                 "momentum_turnover_report.csv", "momentum_drawdown_report.csv",
                 "momentum_yearly_performance.csv", "momentum_regime_diagnostics.csv",
                 "momentum_risk_exposure_report.csv", "momentum_strategy_gate_matrix.csv",
                 "phase7m_next_plan.json"):
        assert (tmp_path / name).is_file(), name
    # Safety contract.
    s = report["safety"]
    assert s["research_only"] is True
    for flag in ("is_paper_trader", "is_production", "is_deployment", "live_data_used",
                 "network_used", "paid_api_used", "packages_installed", "new_data_collected",
                 "factor_weight_optimization", "new_alpha_factors",
                 "failed_composite_used_as_candidate", "regime_selection_or_weighting",
                 "leverage_used", "wrote_to_data_drive", "trading_order_automation",
                 "paper_trader_gcp_broker_touched", "committed", "pushed"):
        assert s[flag] is False, flag
    assert report["gate_matrix_summary"]["safety_fail"] is False


@pytest.mark.skipif(not _panels_present(), reason="local price panels (D:) not present")
def test_full_run_writes_nothing_to_data_drive(tmp_path):
    before = {}
    root = Path("D:/Stock_Prediction_app_data")
    if root.exists():
        before = {p: p.stat().st_mtime for p in root.rglob("*") if p.is_file()}
    L.run(out_dir=str(tmp_path))
    if root.exists():
        after = {p: p.stat().st_mtime for p in root.rglob("*") if p.is_file()}
        # No new files and no modified files on the data drive.
        assert set(after) == set(before)
        assert all(after[p] == before[p] for p in before)
