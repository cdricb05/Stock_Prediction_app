"""Tests for Phase 7-D — Risk Decomposition Foundation.

Pure-function unit tests on deterministic synthetic data, plus a guarded end-to-end
test against the real local price/sector data (skipped automatically if absent). No
network, no live data, no orders — measurement only.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNNER = _REPO_ROOT / "research" / "run_phase7d_risk_decomposition_foundation.py"


def _load_runner():
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    spec = importlib.util.spec_from_file_location("phase7d_runner_test", _RUNNER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase7d_runner_test"] = mod
    spec.loader.exec_module(mod)
    return mod


D = _load_runner()


# --------------------------------------------------------------------------- #
# Deterministic sample portfolio.
# --------------------------------------------------------------------------- #
def test_sample_portfolio_weights_sum_to_one_and_deterministic():
    universe = [f"T{i:02d}" for i in range(40)]
    smap = {t: ("SEC_A" if i % 2 == 0 else "SEC_B") for i, t in enumerate(universe)}
    p1 = D.build_sample_portfolio(universe, smap, n=15)
    p2 = D.build_sample_portfolio(universe, smap, n=15)
    assert len(p1) == 15
    assert abs(p1["weight"].sum() - 1.0) < 1e-12
    # Fully deterministic — identical names and weights on a second call.
    assert list(p1["ticker"]) == list(p2["ticker"])
    assert np.allclose(p1["weight"].values, p2["weight"].values)
    # Linear decay: first pick is the heaviest, weights strictly decreasing.
    w = p1["weight"].values
    assert all(w[i] > w[i + 1] for i in range(len(w) - 1))
    # Sectors are mapped.
    assert set(p1["sector"]) <= {"SEC_A", "SEC_B"}


def test_sample_portfolio_small_universe_caps_n():
    universe = ["A", "B", "C"]
    smap = {"A": "S1", "B": "S1", "C": "S2"}
    p = D.build_sample_portfolio(universe, smap, n=15)
    assert len(p) == 3
    assert abs(p["weight"].sum() - 1.0) < 1e-12


# --------------------------------------------------------------------------- #
# Returns / dollar-volume builders.
# --------------------------------------------------------------------------- #
def test_daily_returns_and_dollar_volume():
    dates = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"])
    prices = pd.DataFrame({
        "ticker": ["X", "X", "X"], "date": dates,
        "adj_close": [100.0, 110.0, 99.0], "volume": [10, 20, 30],
    })
    rets = D.daily_returns_wide(prices)
    assert math.isnan(rets["X"].iloc[0])
    assert abs(rets["X"].iloc[1] - 0.10) < 1e-12
    assert abs(rets["X"].iloc[2] - (99.0 / 110.0 - 1.0)) < 1e-12
    dv = D.dollar_volume_wide(prices)
    assert abs(dv["X"].iloc[1] - 110.0 * 20) < 1e-9


# --------------------------------------------------------------------------- #
# Per-position statistics: beta / vol / residual.
# --------------------------------------------------------------------------- #
def _synthetic_returns(n=200, seed=7):
    rng = np.random.default_rng(seed)
    spy = rng.normal(0.0, 0.01, n)
    # HI = 2*SPY exactly (beta 2, zero residual); LO = 0.5*SPY exactly (beta 0.5).
    hi = 2.0 * spy
    lo = 0.5 * spy
    idx = pd.RangeIndex(n)
    rets = pd.DataFrame({"HI": hi, "LO": lo, D.BENCHMARK: spy}, index=idx)
    return rets


def test_position_statistics_recovers_known_beta():
    rets = _synthetic_returns()
    spy = rets[D.BENCHMARK]
    dv = pd.DataFrame({"HI": np.full(len(rets), 1e9), "LO": np.full(len(rets), 1e9),
                       D.BENCHMARK: np.full(len(rets), 1e9)}, index=rets.index)
    portfolio = pd.DataFrame({"ticker": ["HI", "LO"], "sector": ["S1", "S2"], "weight": [0.6, 0.4]})
    pos = D.position_statistics(rets, spy, dv, portfolio)
    by = {r.ticker: r for r in pos.itertuples()}
    assert abs(by["HI"].beta_to_spy - 2.0) < 1e-9
    assert abs(by["LO"].beta_to_spy - 0.5) < 1e-9
    # Perfectly explained by the market -> ~zero residual vol.
    assert by["HI"].residual_vol_annual < 1e-6
    assert by["LO"].residual_vol_annual < 1e-6


def test_market_beta_exposure_net_beta_is_weighted_average():
    rets = _synthetic_returns()
    spy = rets[D.BENCHMARK]
    dv = pd.DataFrame({"HI": np.full(len(rets), 1e9), "LO": np.full(len(rets), 1e9),
                       D.BENCHMARK: np.full(len(rets), 1e9)}, index=rets.index)
    portfolio = pd.DataFrame({"ticker": ["HI", "LO"], "sector": ["S1", "S2"], "weight": [0.6, 0.4]})
    pos = D.position_statistics(rets, spy, dv, portfolio)
    beta_view = D.market_beta_exposure(pos, rets, spy)
    # 0.6*2 + 0.4*0.5 = 1.4
    assert abs(beta_view["net_beta"] - 1.4) < 1e-6
    # Both names are pure market exposure -> systematic share ~ 1.
    assert beta_view["systematic_variance_share"] > 0.99


# --------------------------------------------------------------------------- #
# Sector / concentration.
# --------------------------------------------------------------------------- #
def test_sector_exposure_aggregates_weights():
    pos = pd.DataFrame({"ticker": ["A", "B", "C"], "sector": ["S1", "S1", "S2"],
                        "weight": [0.5, 0.3, 0.2]})
    s = D.sector_exposure(pos)
    by = {r.sector: r for r in s.itertuples()}
    assert abs(by["S1"].weight - 0.8) < 1e-12
    assert by["S1"].n_names == 2
    assert abs(by["S1"].hhi_contrib - 0.64) < 1e-12
    # Sorted by weight descending.
    assert s.iloc[0]["sector"] == "S1"


def test_concentration_metrics_known_values():
    pos = pd.DataFrame({"ticker": ["A", "B"], "sector": ["S1", "S2"], "weight": [0.5, 0.5]})
    s = D.sector_exposure(pos)
    conc = D.concentration_metrics(pos, s)
    assert abs(conc["herfindahl_index"] - 0.5) < 1e-12
    assert abs(conc["effective_n_names"] - 2.0) < 1e-9
    assert abs(conc["max_name_weight"] - 0.5) < 1e-12
    assert conc["n_positions"] == 2
    assert conc["n_sectors"] == 2


# --------------------------------------------------------------------------- #
# Liquidity / idiosyncratic.
# --------------------------------------------------------------------------- #
def test_liquidity_report_days_to_liquidate():
    pos = pd.DataFrame({"ticker": ["A", "B"], "sector": ["S1", "S2"], "weight": [0.5, 0.5],
                        "avg_dollar_volume": [1e9, 1e5]})
    liq = D.liquidity_report(pos, book_usd=10_000_000.0, participation=0.10)
    by = {r.ticker: r for r in liq.itertuples()}
    # A: notional 5e6 / (0.1 * 1e9) = 0.05 days (deep/liquid).
    assert abs(by["A"].days_to_liquidate - (5_000_000.0 / (0.10 * 1e9))) < 1e-9
    # B: notional 5e6 / (0.1 * 1e5) = 500 days (thin).
    assert by["B"].days_to_liquidate > D.DTL_FLAG_DAYS
    assert by["B"].liquidity_bucket == "thin"


def test_liquidity_report_handles_missing_adv():
    pos = pd.DataFrame({"ticker": ["A"], "sector": ["S1"], "weight": [1.0],
                        "avg_dollar_volume": [None]})
    liq = D.liquidity_report(pos)
    assert liq.iloc[0]["days_to_liquidate"] is None
    assert liq.iloc[0]["liquidity_bucket"] == "unknown"


def test_diversifiable_idiosyncratic_vol():
    # Two names, equal weight 0.5, residual annual vol 0.2 each, zero residual corr:
    # sqrt(0.25*0.04 + 0.25*0.04) = sqrt(0.02) ~= 0.141421
    pos = pd.DataFrame({"ticker": ["A", "B"], "weight": [0.5, 0.5],
                        "residual_vol_annual": [0.2, 0.2]})
    v = D.diversifiable_idiosyncratic_vol(pos)
    assert abs(v - math.sqrt(0.02)) < 1e-9


# --------------------------------------------------------------------------- #
# Factor tilt pure core.
# --------------------------------------------------------------------------- #
def test_book_tilt_from_factor_z_weighted():
    # One factor, latest month "2026-06"; A z=+1, B z=-1; weights 0.75/0.25.
    factor_z = {
        "momentum": pd.DataFrame({"month": ["2026-05", "2026-06", "2026-06"],
                                  "ticker": ["A", "A", "B"], "z": [9.9, 1.0, -1.0]}),
        "value": pd.DataFrame(columns=["month", "ticker", "z"]),  # empty -> unavailable
    }
    portfolio = pd.DataFrame({"ticker": ["A", "B"], "sector": ["S1", "S2"], "weight": [0.75, 0.25]})
    out = D._book_tilt_from_factor_z(factor_z, portfolio)
    # Only latest month used: 0.75*1 + 0.25*(-1) = 0.5 (weights cover full book).
    assert abs(out["factors"]["momentum"]["book_tilt_z"] - 0.5) < 1e-9
    assert out["factors"]["momentum"]["coverage"] == 1.0
    assert out["factors"]["momentum"]["as_of_month"] == "2026-06"
    assert out["factors"]["value"]["available"] is False
    assert out["available"] is True


# --------------------------------------------------------------------------- #
# Gates / recommendation.
# --------------------------------------------------------------------------- #
def _good_inputs():
    pos = pd.DataFrame({"ticker": ["A", "B"], "sector": ["S1", "S2"], "weight": [0.5, 0.5],
                        "realized_vol_annual": [0.2, 0.25], "residual_vol_annual": [0.18, 0.2]})
    beta_view = {"net_beta": 1.0, "idiosyncratic_vol_annual": 0.15,
                 "systematic_variance_share": 0.5}
    sectors = D.sector_exposure(pos)
    conc = D.concentration_metrics(pos, sectors)
    liq = D.liquidity_report(pos.assign(avg_dollar_volume=[1e9, 1e9]))
    tilt = {"available": True, "factors": {}, "composite_tilt_z": 0.1}
    return pos, beta_view, conc, liq, tilt


def test_gate_matrix_all_pass_recommends_ready():
    pos, beta_view, conc, liq, tilt = _good_inputs()
    gates = D._build_gate_matrix(pos, beta_view, conc, liq, tilt, sector_pit=True)
    statuses = {g["gate_name"]: g["status"] for g in gates}
    # Capability + safety gates all PASS.
    for cap in ("weights_normalized_gate", "position_vol_gate", "beta_gate",
                "idiosyncratic_gate", "concentration_gate", "liquidity_gate"):
        assert statuses[cap] == "PASS"
    assert all(statuses[g] == "PASS" for g in statuses if g.startswith("no_") or g == "preview_only_gate")
    assert D._derive_recommendation(gates) == D.REC_READY


def test_recommendation_needs_review_when_beta_missing():
    pos, beta_view, conc, liq, tilt = _good_inputs()
    beta_view = dict(beta_view, net_beta=None)  # beta could not be computed
    gates = D._build_gate_matrix(pos, beta_view, conc, liq, tilt, sector_pit=True)
    assert any(g["gate_name"] == "beta_gate" and g["status"] == "FAIL" for g in gates)
    assert D._derive_recommendation(gates) == D.REC_NEEDS_REVIEW


def test_safety_gates_are_all_false_except_preview_only():
    pos, beta_view, conc, liq, tilt = _good_inputs()
    gates = D._build_gate_matrix(pos, beta_view, conc, liq, tilt, sector_pit=True)
    safety = {g["metric"]: g for g in gates if g["gate_name"].startswith(("no_", "preview_"))}
    for metric, g in safety.items():
        assert g["status"] == "PASS"
        if metric == "preview_only":
            assert g["value"] is True
        else:
            assert g["value"] is False
    # Explicit charter-forbidden capabilities are represented.
    for must in ("orders_enabled", "broker_execution_enabled", "automation_enabled",
                 "hedging_enabled", "order_sizing_enabled", "trade_recommendation_made",
                 "live_data_used", "paper_trader_touched"):
        assert must in safety


def test_concentration_flag_warns_on_heavy_name():
    pos = pd.DataFrame({"ticker": ["A", "B"], "sector": ["S1", "S2"], "weight": [0.9, 0.1],
                        "realized_vol_annual": [0.2, 0.25], "residual_vol_annual": [0.18, 0.2]})
    beta_view = {"net_beta": 1.0, "idiosyncratic_vol_annual": 0.15, "systematic_variance_share": 0.5}
    sectors = D.sector_exposure(pos)
    conc = D.concentration_metrics(pos, sectors)
    liq = D.liquidity_report(pos.assign(avg_dollar_volume=[1e9, 1e9]))
    gates = D._build_gate_matrix(pos, beta_view, conc, {**liq}, {"available": True, "factors": {}}, sector_pit=True)
    flag = next(g for g in gates if g["gate_name"] == "name_concentration_flag")
    assert flag["status"] == "WARNING"
    # An informational flag must NOT change the recommendation away from READY.
    assert D._derive_recommendation(gates) == D.REC_READY


# --------------------------------------------------------------------------- #
# Guarded end-to-end against real local data.
# --------------------------------------------------------------------------- #
_DATA_PRESENT = os.path.exists(D.PRICE_CSV) and os.path.exists(D.SECTOR_MAP_CSV)


@pytest.mark.skipif(not _DATA_PRESENT, reason="local price/sector data not present")
def test_end_to_end_real_data(tmp_path):
    report = D.run(out_dir=str(tmp_path))
    assert report["recommendation"] in D.ALLOWED_RECOMMENDATIONS
    # All artifacts written.
    for name in ("phase7d_risk_decomposition_foundation.json", "sample_portfolio.csv",
                 "position_risk_decomposition.csv", "sector_exposure.csv",
                 "factor_tilt_exposure.csv", "liquidity_risk_report.csv",
                 "concentration_report.csv", "risk_gate_matrix.csv", "phase7e_next_plan.json"):
        assert (tmp_path / name).exists(), name
    # JSON parses and safety flags hold.
    with open(tmp_path / "phase7d_risk_decomposition_foundation.json", encoding="utf-8") as fh:
        loaded = json.load(fh)
    assert loaded["preview_only"] is True
    for flag in ("network_used", "live_data_used", "orders_enabled", "broker_execution_enabled",
                 "automation_enabled", "hedging_enabled", "order_sizing_enabled",
                 "trade_recommendation_made", "paper_trader_touched", "gcp_touched",
                 "d_drive_written"):
        assert loaded[flag] is False, flag
    # Sample portfolio is explicitly not live holdings and sums to one.
    assert loaded["sample_portfolio"]["is_live_holdings"] is False
    wsum = sum(h["weight"] for h in loaded["sample_portfolio"]["holdings"])
    assert abs(wsum - 1.0) < 1e-6
    # Core risk lenses are populated.
    rv = loaded["risk_view"]
    assert rv["market_beta_exposure"]["net_beta"] is not None
    assert rv["idiosyncratic_concentration_risk"]["herfindahl_index"] is not None
    assert rv["liquidity_risk"]["worst_days_to_liquidate"] is not None
    # No capability gate failed.
    assert loaded["gate_summary"]["counts"]["FAIL"] == 0


@pytest.mark.skipif(not _DATA_PRESENT, reason="local price/sector data not present")
def test_factor_tilt_available_real_data():
    sector_map, _ = D.load_prices_sector_map()
    prices = D.load_prices_with_benchmark(universe=set(sector_map))
    universe = sorted((set(prices["ticker"].unique()) - {D.BENCHMARK}) & set(sector_map))
    portfolio = D.build_sample_portfolio(universe, sector_map, n=D.N_SAMPLE)
    tilt = D.factor_tilt_exposure(portfolio)
    assert tilt["available"] is True
    assert "momentum" in tilt["factors"]
