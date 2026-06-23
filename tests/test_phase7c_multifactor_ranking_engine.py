"""Tests for Phase 7-C — Multi-Factor Ranking Engine Foundation.

Pure-function tests run on deterministic synthetic in-memory frames (no file I/O):
point-in-time fundamental as-of discipline, cross-sectional normalization,
equal-weight composite, harness grading, the robust K-shuffle placebo, the
portfolio spread/turnover series, the gate matrix, and the recommendation logic.

One guarded end-to-end test runs the real engine only if the local price panel and
SEC fundamentals are present; it asserts the recommendation is in the allowed set,
all eight artifacts are written, the JSON parses, and every safety flag is off.
"""
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

_RUNNER = _REPO_ROOT / "research" / "run_phase7c_multifactor_ranking_engine.py"


def _load():
    spec = importlib.util.spec_from_file_location("phase7c_runner_test", _RUNNER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase7c_runner_test"] = mod  # register before exec (dataclass annotations)
    spec.loader.exec_module(mod)
    return mod


R = _load()


# --------------------------------------------------------------------------- #
# Synthetic builders.
# --------------------------------------------------------------------------- #
def _signal_panel(n_months=8, n_names=30, beta=0.8, seed=1):
    """A factor whose per-month cross-section correlates with the forward label."""
    rng = np.random.default_rng(seed)
    z_rows, fwd_rows = [], []
    for mi in range(n_months):
        month = f"2020-{mi + 1:02d}"
        z = rng.normal(0, 1, n_names)
        fwd = beta * z + rng.normal(0, 1, n_names)
        for ni in range(n_names):
            tk = f"T{ni:02d}"
            z_rows.append((month, tk, float(z[ni])))
            fwd_rows.append((month, tk, float(fwd[ni])))
    scores = pd.DataFrame(z_rows, columns=["month", "ticker", "z"])
    realized = pd.DataFrame(fwd_rows, columns=["month", "ticker", "fwd"])
    return scores, realized


# --------------------------------------------------------------------------- #
# Point-in-time fundamental discipline.
# --------------------------------------------------------------------------- #
def test_asof_excludes_same_day_and_future():
    ts = pd.Timestamp
    obs = [(ts("2019-12-31"), ts("2020-02-01"), 10.0),
           (ts("2020-12-31"), ts("2021-02-01"), 20.0)]
    # cutoff strictly after the first availability only -> first value.
    got = R._asof(obs, ts("2020-06-01"))
    assert got is not None and got[1] == 10.0
    # cutoff equal to availability is NOT strictly before -> excluded (no same-day leak).
    assert R._asof(obs, ts("2020-02-01")) is None
    # cutoff after both -> latest value.
    assert R._asof(obs, ts("2021-06-01"))[1] == 20.0
    # cutoff before any availability -> None.
    assert R._asof(obs, ts("2019-01-01")) is None


def test_asof_two_growth_needs_two_prior_years():
    ts = pd.Timestamp
    obs = [(ts("2019-12-31"), ts("2020-02-01"), 100.0),
           (ts("2020-12-31"), ts("2021-02-01"), 120.0)]
    assert R._asof_two(obs, ts("2020-06-01")) is None        # only one available
    cur, prev = R._asof_two(obs, ts("2021-06-01"))           # both available
    assert cur == 120.0 and prev == 100.0


# --------------------------------------------------------------------------- #
# Cross-sectional normalization.
# --------------------------------------------------------------------------- #
def test_normalize_zscores_clean_cross_section():
    rows = [("2020-01", f"T{i:02d}", float(v)) for i, v in enumerate(range(1, 31))]
    raw = pd.DataFrame(rows, columns=["month", "ticker", "raw"])
    z = R.normalize_cross_sectional(raw, sector_map={}, sector_neutralize=False)
    assert abs(z["z"].mean()) < 1e-9                  # standardized to mean 0
    assert z["z"].std(ddof=0) == pytest.approx(1.0, abs=1e-6)


def test_normalize_winsorizes_outlier():
    rows = [("2020-01", f"T{i:02d}", float(v)) for i, v in enumerate(range(1, 31))]
    rows.append(("2020-01", "OUT", 1e6))              # extreme outlier must be clipped to +3
    raw = pd.DataFrame(rows, columns=["month", "ticker", "raw"])
    z = R.normalize_cross_sectional(raw, sector_map={}, sector_neutralize=False)
    assert z["z"].max() <= R.WINSOR_Z + 1e-9 and z["z"].min() >= -R.WINSOR_Z - 1e-9
    assert z.loc[z["ticker"] == "OUT", "z"].iloc[0] == pytest.approx(R.WINSOR_Z)


def test_normalize_sector_neutralizes():
    # Two sectors with a large level offset; sector-demeaning must remove it so the
    # within-sector ordering — not the sector level — drives the z-scores.
    rows = []
    for i in range(15):
        rows.append(("2020-01", f"A{i:02d}", 100.0 + i))   # sector A, high level
    for i in range(15):
        rows.append(("2020-01", f"B{i:02d}", 0.0 + i))     # sector B, low level
    raw = pd.DataFrame(rows, columns=["month", "ticker", "raw"])
    smap = {**{f"A{i:02d}": "secA" for i in range(15)}, **{f"B{i:02d}": "secB" for i in range(15)}}
    z = R.normalize_cross_sectional(raw, smap, sector_neutralize=True)
    top_a = z.loc[z["ticker"] == "A14", "z"].iloc[0]
    top_b = z.loc[z["ticker"] == "B14", "z"].iloc[0]
    # the highest-ranked name within each sector lands at the same z after neutralization.
    assert top_a == pytest.approx(top_b, abs=1e-9)


# --------------------------------------------------------------------------- #
# Harness grading + placebo.
# --------------------------------------------------------------------------- #
def test_grade_series_recovers_known_signal():
    scores, realized = _signal_panel(beta=0.9, seed=3)
    g = R.grade_series(scores, realized)
    assert g["mean_rank_ic"] is not None and g["mean_rank_ic"] > 0.3
    assert g["n_periods"] == 8
    assert g["quintile_spread"] is not None and g["quintile_spread"] > 0


def test_placebo_collapses_for_real_signal():
    # Enough periods/names/shuffles that the averaged shuffled IC is at sampling-noise
    # scale (the 0.005 bound is calibrated for a panel of this size, like the real run).
    scores, realized = _signal_panel(n_months=24, n_names=40, beta=0.9, seed=4)
    g = R.grade_series(scores, realized)
    placebo = R.placebo_test(g["_sbp"], g["_rbp"], n_shuffles=120)
    assert placebo["n_shuffles"] == 120
    assert abs(placebo["placebo_mean_ic"]) <= R.PLACEBO_MAX_ABS_MEAN
    assert R._placebo_collapsed(placebo) is True
    # the honest signal itself is far from the shuffled null.
    assert g["mean_rank_ic"] > 10 * abs(placebo["placebo_mean_ic"])


def test_coverage_fraction():
    realized = pd.DataFrame([("2020-01", "A"), ("2020-01", "B"), ("2020-02", "A")],
                            columns=["month", "ticker"]).assign(fwd=0.0)
    z = pd.DataFrame([("2020-01", "A", 0.0)], columns=["month", "ticker", "z"])
    assert R.coverage_fraction(z, realized) == pytest.approx(1 / 3)
    assert R.coverage_fraction(z.iloc[0:0], realized) == 0.0


# --------------------------------------------------------------------------- #
# Equal-weight composite + portfolio spread.
# --------------------------------------------------------------------------- #
def test_equal_weight_composite_is_plain_average():
    fa = pd.DataFrame([("2020-01", "A", 1.0), ("2020-01", "B", -1.0)], columns=["month", "ticker", "z"])
    fb = pd.DataFrame([("2020-01", "A", 3.0), ("2020-01", "B", 1.0)], columns=["month", "ticker", "z"])
    comp = R.equal_weight_composite_long({"fa": fa, "fb": fb}, ["fa", "fb"])
    a = comp.loc[comp["ticker"] == "A", "z"].iloc[0]
    b = comp.loc[comp["ticker"] == "B", "z"].iloc[0]
    assert a == pytest.approx(2.0) and b == pytest.approx(0.0)  # plain mean, no learned weights


def test_spread_series_positive_for_monotone_signal():
    scores, realized = _signal_panel(beta=1.2, seed=5)
    port = R.spread_series_from_frame(scores.rename(columns={"z": "z"}), realized, q=5)
    assert len(port["spreads"]) == 8
    assert np.mean(port["spreads"]) > 0                 # top quintile beats bottom
    assert all(0.0 <= t <= 1.0 for t in port["turnovers"])


# --------------------------------------------------------------------------- #
# Forward-label leakage check + recommendation logic.
# --------------------------------------------------------------------------- #
def test_forward_label_check_clean_for_next_month_labels():
    scores, realized = _signal_panel(seed=6)
    chk = R._forward_label_check(scores, realized)
    assert chk["strictly_forward"] is True and chk["n_violations"] == 0


def _gates_for(strictly=True, placebo=True, incremental=0.01, approved=("m", "v"),
               base=-0.01, comp=0.01, sector_pit=False):
    sf = {"strictly_forward": strictly, "n_violations": 0 if strictly else 3}
    cg = {"ic_t_stat": 2.5, "mean_rank_ic": comp}
    return R._build_gate_matrix(sf, placebo, incremental, list(approved), base, comp, cg, sector_pit), sf


def test_recommendation_confirmed_when_gate_cleared():
    gates, sf = _gates_for(incremental=0.01, base=-0.01, comp=0.02)
    rec = R._derive_recommendation(gates, ["m", "v"], ["v"], -0.01, 0.02, 0.01, True, sf)
    assert rec == R.REC_CONFIRMED


def test_recommendation_weak_when_below_gate():
    gates, sf = _gates_for(incremental=0.001, base=-0.012, comp=-0.007)
    rec = R._derive_recommendation(gates, ["m", "v"], ["v"], -0.012, -0.007, 0.001, True, sf)
    assert rec == R.REC_WEAK


def test_recommendation_needs_data_with_too_few_buckets():
    gates, sf = _gates_for(approved=("m",))
    rec = R._derive_recommendation(gates, ["m"], [], -0.01, 0.0, None, True, sf)
    assert rec == R.REC_NEEDS_DATA


def test_recommendation_needs_review_on_placebo_or_leak():
    gates, sf = _gates_for(placebo=False)
    assert R._derive_recommendation(gates, ["m", "v"], ["v"], -0.01, 0.02, 0.03, False, sf) == R.REC_NEEDS_REVIEW
    gates2, sf2 = _gates_for(strictly=False)
    assert R._derive_recommendation(gates2, ["m", "v"], ["v"], -0.01, 0.02, 0.03, True, sf2) == R.REC_NEEDS_REVIEW


# --------------------------------------------------------------------------- #
# Gate matrix + factor definitions.
# --------------------------------------------------------------------------- #
def test_safety_gates_all_pass_and_present():
    gates, _ = _gates_for()
    by = {g["gate_name"]: g for g in gates}
    for nm in ("no_orders_gate", "no_broker_execution_gate", "no_automation_gate",
               "no_network_gate", "no_live_data_gate", "no_paid_api_gate", "no_deploy_gate",
               "no_production_model_gate", "no_weight_optimization_gate",
               "no_future_fundamentals_gate", "no_paper_trader_gate", "no_gcp_gate",
               "no_d_drive_write_gate", "preview_only_gate"):
        assert by[nm]["status"] == "PASS", nm


def test_factor_defs_sources_and_unavailable_bucket():
    by = {f.key: f for f in R.FACTOR_DEFS}
    assert by["momentum"].source == "price" and by["low_volatility"].source == "price"
    for k in ("value", "quality", "growth"):
        assert by[k].source == "fundamentals"
    assert any(u["bucket"] == "revisions_sentiment" for u in R.UNAVAILABLE_BUCKETS)


# --------------------------------------------------------------------------- #
# Guarded end-to-end run against real local data (skips if data absent).
# --------------------------------------------------------------------------- #
_DATA_PRESENT = Path(R.PRICE_CSV).exists() and Path(R.FUNDAMENTALS_CSV).exists()


@pytest.mark.skipif(not _DATA_PRESENT, reason="local price panel / SEC fundamentals not present")
def test_end_to_end_real_data(tmp_path):
    report = R.run(out_dir=str(tmp_path))
    assert report["recommendation"] in R.ALLOWED_RECOMMENDATIONS
    assert report["recommendation"] != R.REC_ERROR, report.get("error")
    # all eight committed-safe artifacts written + JSON parses.
    for name in ("phase7c_multifactor_ranking_engine.json", "factor_data_inventory.csv",
                 "factor_catalog.csv", "factor_scoreboard.csv", "composite_scoreboard.csv",
                 "validation_gate_matrix.csv", "multiple_testing_tracker.csv", "phase7d_next_plan.json"):
        assert (tmp_path / name).exists(), name
    json.loads((tmp_path / "phase7c_multifactor_ranking_engine.json").read_text())
    # every safety flag off; preview-only on.
    for flag in ("network_used", "paid_apis_used", "live_data_used", "orders_enabled",
                 "broker_execution_enabled", "automation_enabled", "deployed",
                 "production_model_built", "factor_weights_optimized", "future_fundamentals_used",
                 "paper_trader_touched", "gcp_touched", "d_drive_written"):
        assert report[flag] is False, flag
    assert report["preview_only"] is True
    # leakage discipline held; composite was built and compared to the baseline.
    assert report["leakage_checks"]["strictly_forward_labels"]["strictly_forward"] is True
    assert report["leakage_checks"]["placebo_collapsed"] is True
    assert report["incremental_mean_rank_ic"] is not None
    assert "revisions_sentiment" in [u["bucket"] for u in report["unavailable_buckets"]]
