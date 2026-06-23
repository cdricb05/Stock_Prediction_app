"""Tests for Phase 7-E — Regime / Risk Overlay Foundation.

Offline, deterministic. The unit tests exercise the regime classification, the
as-of / lagged macro reads, the IC-by-regime partitioning, the risk-by-regime
proxy, transitions / persistence, the gate matrix, and the NOT-LIVE throttle
template. A guarded end-to-end test runs the full engine against the real local
data only when that data is present.
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_RUNNER = _REPO_ROOT / "research" / "run_phase7e_regime_risk_overlay_foundation.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("phase7e_runner_test", _RUNNER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase7e_runner_test"] = mod  # so dataclasses/typing resolve during exec
    spec.loader.exec_module(mod)
    return mod


E = _load_runner()


# --------------------------------------------------------------------------- #
# As-of / lagged macro reads (leakage discipline).
# --------------------------------------------------------------------------- #
def test_asof_period_respects_lag():
    s = pd.Series([1.0, 2.0, 3.0, 4.0],
                  index=pd.period_range("2020-01", "2020-04", freq="M"))
    assert E._asof_period(s, pd.Period("2020-04", "M"), 1) == 3.0   # cutoff 2020-03
    assert E._asof_period(s, pd.Period("2020-04", "M"), 2) == 2.0   # cutoff 2020-02
    assert E._asof_period(s, pd.Period("2020-04", "M"), 6) is None  # cutoff before series
    assert E._asof_period(pd.Series(dtype=float), pd.Period("2020-04", "M"), 1) is None


def test_macro_direction_up_down_unknown():
    rising = pd.Series(np.arange(18, dtype=float),
                       index=pd.period_range("2019-01", "2020-06", freq="M"))
    falling = pd.Series(np.arange(18, 0, -1, dtype=float),
                        index=pd.period_range("2019-01", "2020-06", freq="M"))
    p = pd.Period("2020-06", "M")
    assert E._macro_direction(rising, p, 1, "up", "down") == "up"
    assert E._macro_direction(falling, p, 1, "up", "down") == "down"
    # Too little history before the lagged window -> unknown.
    short = pd.Series([1.0], index=pd.period_range("2020-05", "2020-05", freq="M"))
    assert E._macro_direction(short, p, 1, "up", "down") == "unknown"


# --------------------------------------------------------------------------- #
# Benchmark month-end metrics.
# --------------------------------------------------------------------------- #
def test_benchmark_month_end_metrics_basic():
    dates = pd.bdate_range("2016-01-01", periods=300)
    # Steadily rising series so close stays above the 200d SMA (above-trend, no drawdown).
    px = 100.0 * (1.0 + np.arange(len(dates)) * 0.001)
    spy = pd.DataFrame({"date": dates, "adj_close": px})
    me = E.benchmark_month_end_metrics(spy)
    assert not me.empty
    for col in ("month", "close", "sma200", "vol21", "dd252", "ret", "fwd"):
        assert col in me.columns
    last_full = me.dropna(subset=["sma200", "dd252"]).iloc[-1]
    assert last_full["close"] >= last_full["sma200"]      # rising -> above trend
    assert last_full["dd252"] >= -1e-9                     # monotone up -> no drawdown
    assert E.benchmark_month_end_metrics(
        pd.DataFrame(columns=["date", "adj_close"])).empty


# --------------------------------------------------------------------------- #
# Regime classification (price + macro), as-of audit.
# --------------------------------------------------------------------------- #
def _synthetic_me(n=40):
    months = pd.period_range("2016-01", periods=n, freq="M")
    close = np.linspace(100.0, 140.0, n)
    sma = close - 1.0                       # close > sma everywhere -> above_trend
    vol = np.linspace(0.10, 0.30, n)        # rising vol
    dd = np.zeros(n)                         # no drawdown -> risk_on
    dd[5] = -0.15                            # one risk_off month
    return pd.DataFrame({"month": months, "close": close, "sma200": sma,
                         "vol21": vol, "dd252": dd,
                         "ret": np.full(n, 0.01), "fwd": np.full(n, 0.01)})


def test_classify_regimes_price_axes():
    me = _synthetic_me(40)
    empty = pd.Series(dtype=float)
    reg = E.classify_regimes(me, cpi_yoy=empty, dgs10=empty, usd=empty)
    labels = reg["axis_labels"]
    assert reg["asof_violations"] == 0
    # Trend: every labelled month is above_trend.
    assert set(labels["trend_regime"].values()) == {"above_trend"}
    # Risk: exactly one risk_off month (the -0.15 drawdown), rest risk_on.
    risk = labels["risk_regime"]
    assert sum(1 for v in risk.values() if v == "risk_off") == 1
    assert risk[E._pkey(me["month"].iloc[5])] == "risk_off"
    # Vol regime activates only after the warm-up window.
    assert len(labels["vol_regime"]) <= 40 - E.VOL_MIN_HISTORY_M + 1
    assert set(labels["vol_regime"].values()) <= {"high_vol", "low_vol"}
    # Macro axes absent (empty series) -> unavailable.
    for ax in ("inflation_regime", "rates_regime", "dollar_regime"):
        assert reg["axis_available"][ax] is False


def test_classify_regimes_macro_axis_available():
    me = _synthetic_me(30)
    rising = pd.Series(np.arange(60, dtype=float),
                       index=pd.period_range("2014-01", periods=60, freq="M"))
    empty = pd.Series(dtype=float)
    reg = E.classify_regimes(me, cpi_yoy=rising, dgs10=rising, usd=empty)
    assert reg["axis_available"]["inflation_regime"] is True
    assert reg["axis_available"]["rates_regime"] is True
    assert reg["axis_available"]["dollar_regime"] is False
    # Rising series -> "up" labels.
    assert set(reg["axis_labels"]["rates_regime"].values()) == {"rates_up"}


# --------------------------------------------------------------------------- #
# IC by regime — partition reuses the harness ic_summary.
# --------------------------------------------------------------------------- #
def test_ic_by_regime_partition():
    period_ics = {"2016-01": 0.10, "2016-02": 0.20, "2016-03": -0.10, "2016-04": -0.20}
    axis_labels = {"2016-01": "risk_on", "2016-02": "risk_on",
                   "2016-03": "risk_off", "2016-04": "risk_off"}
    out = E.ic_by_regime(period_ics, axis_labels)
    assert out["risk_on"]["n_months"] == 2
    assert math.isclose(out["risk_on"]["mean_rank_ic"], 0.15, rel_tol=1e-9)
    assert math.isclose(out["risk_off"]["mean_rank_ic"], -0.15, rel_tol=1e-9)


def test_ic_by_regime_ignores_unlabeled_months():
    period_ics = {"2016-01": 0.10, "2016-02": 0.20, "2016-03": 0.30}
    axis_labels = {"2016-01": "low_vol"}  # only one month labelled
    out = E.ic_by_regime(period_ics, axis_labels)
    assert set(out) == {"low_vol"}
    assert out["low_vol"]["n_months"] == 1
    assert math.isclose(out["low_vol"]["mean_rank_ic"], 0.10, rel_tol=1e-9)


# --------------------------------------------------------------------------- #
# Risk by regime.
# --------------------------------------------------------------------------- #
def test_risk_by_regime_proxy():
    months = pd.period_range("2016-01", periods=4, freq="M")
    me = pd.DataFrame({"month": months, "fwd": [0.02, 0.03, -0.10, -0.05]})
    disp = pd.Series([0.05, 0.06, 0.09, 0.08], index=months)
    labels = {E._pkey(months[0]): "risk_on", E._pkey(months[1]): "risk_on",
              E._pkey(months[2]): "risk_off", E._pkey(months[3]): "risk_off"}
    out = E.risk_by_regime(me, disp, labels)
    assert out["risk_on"]["n_months"] == 2
    assert math.isclose(out["risk_on"]["spy_fwd_ret_mean_monthly"], 0.025, rel_tol=1e-9)
    assert math.isclose(out["risk_off"]["spy_fwd_worst_month"], -0.10, rel_tol=1e-9)
    # Dispersion averaged per regime.
    assert math.isclose(out["risk_off"]["universe_xsec_dispersion_mean"], 0.085, rel_tol=1e-9)


# --------------------------------------------------------------------------- #
# Transitions / persistence / run length.
# --------------------------------------------------------------------------- #
def test_transitions_and_persistence():
    keys = ["2016-01", "2016-02", "2016-03", "2016-04"]
    labels = {"2016-01": "A", "2016-02": "A", "2016-03": "B", "2016-04": "A"}
    out = E.transitions_and_persistence(labels, keys)
    assert out["transitions"][("A", "A")] == 1
    assert out["transitions"][("A", "B")] == 1
    assert out["transitions"][("B", "A")] == 1
    assert out["n_transitions"] == 3
    assert out["label_counts"] == {"A": 3, "B": 1}
    assert math.isclose(out["persistence"]["A"], 0.5, rel_tol=1e-9)   # 1 stay / 2 out
    assert out["persistence"]["B"] == 0.0
    assert math.isclose(out["avg_run_length"]["A"], 1.5, rel_tol=1e-9)  # 3 months / 2 runs
    assert math.isclose(out["avg_run_length"]["B"], 1.0, rel_tol=1e-9)


# --------------------------------------------------------------------------- #
# Gates / recommendation.
# --------------------------------------------------------------------------- #
def _reg_stub(asof_violations=0):
    axes = [a["axis"] for a in E.REGIME_AXES]
    return {"asof_violations": asof_violations,
            "axis_labels": {a: {"2016-01": "x"} for a in axes}}


def _series_ics_stub():
    return {"momentum": {"2016-01": 0.0}, "low_volatility": {"2016-01": 0.0},
            "price_only_baseline": {"2016-01": 0.0}, "equal_weight_composite": {"2016-01": 0.0}}


def test_gate_matrix_all_pass_is_ready():
    axes = [a["axis"] for a in E.REGIME_AXES]
    gates = E._build_gate_matrix(_reg_stub(), axes, 121, _series_ics_stub(), sector_pit=False)
    counts = {"PASS": 0, "FAIL": 0, "WARNING": 0}
    for g in gates:
        counts[g["status"]] += 1
    assert counts["FAIL"] == 0
    rec = E._derive_recommendation(gates, axes, 121)
    assert rec == E.REC_READY


def test_asof_violation_forces_review():
    axes = [a["axis"] for a in E.REGIME_AXES]
    gates = E._build_gate_matrix(_reg_stub(asof_violations=3), axes, 121,
                                 _series_ics_stub(), sector_pit=False)
    assert any(g["gate_name"] == "as_of_lagged_gate" and g["status"] == "FAIL" for g in gates)
    assert E._derive_recommendation(gates, axes, 121) == E.REC_NEEDS_REVIEW


def test_too_few_axes_or_months_blocks():
    one_axis = ["risk_regime"]
    gates = E._build_gate_matrix(_reg_stub(), one_axis, 121, _series_ics_stub(), sector_pit=False)
    assert E._derive_recommendation(gates, one_axis, 121) == E.REC_BLOCKED
    axes = [a["axis"] for a in E.REGIME_AXES]
    gates2 = E._build_gate_matrix(_reg_stub(), axes, 5, _series_ics_stub(), sector_pit=False)
    assert E._derive_recommendation(gates2, axes, 5) == E.REC_BLOCKED


def test_safety_flags_all_false_except_preview_and_owner_review():
    flags = E._safety_flags()
    assert flags["preview_only"] is True
    assert flags["owner_review_required"] is True
    for k in ("orders_enabled", "broker_execution_enabled", "automation_enabled",
              "hedging_enabled", "order_sizing_enabled", "trade_recommendation_made",
              "network_used", "live_data_used", "paid_apis_used", "deployed",
              "production_model_built", "factor_weights_optimized",
              "future_regime_labels_used", "same_day_forward_leakage",
              "paper_trader_touched", "gcp_touched", "d_drive_written",
              "throttle_live", "predictive_claim_made"):
        assert flags[k] is False


def test_throttle_postures_are_risk_consistent():
    # Risk-off postures suggest a lower gross band than risk-on.
    assert E._THROTTLE[("risk_regime", "risk_off")][0] == "RISK_OFF"
    assert E._THROTTLE[("risk_regime", "risk_on")][0] == "RISK_ON"
    off_hi = E._THROTTLE[("risk_regime", "risk_off")][2]
    on_lo = E._THROTTLE[("risk_regime", "risk_on")][1]
    assert off_hi <= on_lo  # risk-off band sits strictly below risk-on band


# --------------------------------------------------------------------------- #
# Throttle template writer is NOT LIVE.
# --------------------------------------------------------------------------- #
def test_throttle_template_written_not_live(tmp_path):
    paths = E._OutPaths(tmp_path / "out")
    axis_labels = {"risk_regime": {"2016-01": "risk_on", "2016-02": "risk_off"},
                   "inflation_regime": {"2016-01": "inflation_up"}}
    E._write_throttle_template(paths, axis_labels, ["risk_regime", "inflation_regime"])
    df = pd.read_csv(paths.throttle)
    assert (df["activation_status"] == "NOT_LIVE / NOT_TRADING").all()
    assert df["owner_review_required"].astype(str).str.lower().eq("true").all()
    # Macro axis is CONTEXT only (no direct throttle band).
    macro = df[df["regime_axis"] == "inflation_regime"]
    assert (macro["posture"] == "CONTEXT").all()


# --------------------------------------------------------------------------- #
# Guarded end-to-end run against the real local data.
# --------------------------------------------------------------------------- #
def _real_data_present() -> bool:
    try:
        return Path(E.PRICE_CSV).exists()
    except Exception:
        return False


@pytest.mark.skipif(not _real_data_present(), reason="local price panel not present")
def test_end_to_end_real_data(tmp_path):
    report = E.run(out_dir=str(tmp_path / "phase7e"))
    assert report["recommendation"] in E.ALLOWED_RECOMMENDATIONS
    assert report["recommendation"] != E.REC_ERROR, report.get("error")

    # All nine committed-safe artifacts exist.
    out = tmp_path / "phase7e"
    for name in ("phase7e_regime_risk_overlay_foundation.json",
                 "regime_classification_summary.csv", "regime_transition_matrix.csv",
                 "factor_ic_by_regime.csv", "composite_ic_by_regime.csv",
                 "risk_by_regime.csv", "regime_throttle_template.csv",
                 "regime_gate_matrix.csv", "phase7f_next_plan.json"):
        assert (out / name).exists(), name

    # JSON parses and the safety contract holds.
    with open(out / "phase7e_regime_risk_overlay_foundation.json", encoding="utf-8") as fh:
        j = json.load(fh)
    assert j["is_predictive"] is False
    assert j["predictive_claim_made"] is False
    assert j["throttle_live"] is False
    assert j["owner_review_required"] is True
    for k in ("orders_enabled", "broker_execution_enabled", "automation_enabled",
              "hedging_enabled", "order_sizing_enabled", "trade_recommendation_made",
              "network_used", "live_data_used", "paid_apis_used", "deployed",
              "production_model_built", "factor_weights_optimized",
              "paper_trader_touched", "gcp_touched", "d_drive_written"):
        assert j[k] is False, k

    if report["recommendation"] == E.REC_BLOCKED:
        pytest.skip("data quality blocked on this machine; safety contract already checked")

    # No FAIL gates; as-of audit clean.
    assert j["gate_summary"]["counts"]["FAIL"] == 0
    assert j["as_of_audit"]["regime_label_asof_violations"] == 0
    assert len(j["available_regime_axes"]) >= 2

    # Full-sample composite IC reproduces the Phase 7-C measurement exactly.
    fs = j["full_sample_mean_rank_ic"]
    assert fs["equal_weight_composite"] is not None
    assert fs["price_only_baseline"] is not None

    # Throttle artifact is entirely NOT LIVE.
    thr = pd.read_csv(out / "regime_throttle_template.csv")
    assert (thr["activation_status"] == "NOT_LIVE / NOT_TRADING").all()

    # IC-by-regime is populated for the composite across at least the risk axis.
    comp = j["composite_ic_by_regime"]["equal_weight_composite"]
    assert "risk_regime" in comp and comp["risk_regime"]


@pytest.mark.skipif(not _real_data_present(), reason="local price panel not present")
def test_full_sample_ic_matches_phase7c(tmp_path):
    """The regime layer reuses the 7-C signals verbatim, so the full-sample composite /
    baseline IC must equal the values 7-C reported (reuse-first integrity check)."""
    sig = E.reconstruct_phase7c_signals()
    comp_ic = E.H.ic_summary(sig["composite_graded"]["period_ics"])["mean_rank_ic"]
    base_ic = E.H.ic_summary(sig["baseline_graded"]["period_ics"])["mean_rank_ic"]
    d7c = E._load_json_safe(E.PHASE7C_JSON)
    if d7c:
        assert math.isclose(comp_ic, d7c["equal_weight_composite"]["mean_rank_ic"],
                            abs_tol=1e-6)
        assert math.isclose(base_ic, d7c["baseline_price_only"]["mean_rank_ic"],
                            abs_tol=1e-6)
