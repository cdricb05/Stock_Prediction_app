"""Tests for Phase 7-H — TTM Fundamental Signal Retest.

Unit tests cover the TTM-window construction (summation + max-availability leakage
guard), the point-in-time as-of / year-over-year lookups, the decision logic, and the
safety gates. A guarded end-to-end test runs the real offline pipeline when the local
price panel and SEC fundamentals are present.
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

from research import run_phase7h_ttm_fundamental_signal_retest as P
from research import run_phase7g_signal_data_foundation_upgrade as G
from research import run_phase7c_multifactor_ranking_engine as C

_REAL_DATA = os.path.exists(C.FUNDAMENTALS_CSV) and os.path.exists(C.PRICE_CSV)
_real = pytest.mark.skipif(not _REAL_DATA, reason="local price/fundamentals artifacts not present")

TS = pd.Timestamp


def _q(tk, qend, avail, val, prov="clean_frame"):
    return G._Q3mo(ticker=tk, quarter_end=TS(qend), avail=TS(avail), value=val, provenance=prov)


# --------------------------------------------------------------------------- #
# Recommendation vocabulary.
# --------------------------------------------------------------------------- #
def test_recommendation_vocabulary_exact():
    assert P.ALLOWED_RECOMMENDATIONS == (
        "TTM_SIGNAL_RELIABILITY_IMPROVED", "TTM_SIGNAL_RELIABILITY_WEAK",
        "NEEDS_BROADER_UNIVERSE_COLLECTION", "NEEDS_REVIEW", "ERROR")


def test_absolute_gate_is_baseline_plus_improvement():
    assert P.ABSOLUTE_IC_GATE == pytest.approx(P.PHASE7F_BASELINE_IC + P.IMPROVEMENT_GATE)
    assert P.ABSOLUTE_IC_GATE == pytest.approx(0.0227)


# --------------------------------------------------------------------------- #
# TTM-window construction: trailing-4 contiguous quarters -> sum, max availability.
# --------------------------------------------------------------------------- #
def test_build_ttm_value_series_sums_four_legs_max_avail(monkeypatch):
    # Five contiguous ~quarterly 3-month observations; availability deliberately
    # out of order so the test proves avail = MAX of the four legs (no leakage).
    quarters = [
        _q("AAA", "2021-03-31", "2021-05-10", 10.0),
        _q("AAA", "2021-06-30", "2021-08-12", 11.0),
        _q("AAA", "2021-09-30", "2021-11-09", 12.0),
        _q("AAA", "2021-12-31", "2022-02-20", 13.0),  # latest leg of window #1
        _q("AAA", "2022-03-31", "2022-05-15", 14.0),  # latest leg of window #2
    ]
    monkeypatch.setattr(G, "build_3mo_quarters", lambda field, universe: quarters)
    series = P.build_ttm_value_series("revenue", {"AAA"})
    wins = series["AAA"]
    assert len(wins) == 2
    (we1, av1, val1), (we2, av2, val2) = wins
    assert we1 == TS("2021-12-31") and val1 == pytest.approx(10 + 11 + 12 + 13)
    assert av1 == TS("2022-02-20")                     # max availability of the 4 legs
    assert we2 == TS("2022-03-31") and val2 == pytest.approx(11 + 12 + 13 + 14)
    assert av2 == TS("2022-05-15")


def test_build_ttm_value_series_breaks_on_gap(monkeypatch):
    # A >100-day hole between Q2 and the next observation: no 4-contiguous window forms.
    quarters = [
        _q("BBB", "2021-03-31", "2021-05-10", 10.0),
        _q("BBB", "2021-06-30", "2021-08-12", 11.0),
        _q("BBB", "2022-06-30", "2022-08-12", 12.0),   # ~1yr gap
        _q("BBB", "2022-09-30", "2022-11-09", 13.0),
    ]
    monkeypatch.setattr(G, "build_3mo_quarters", lambda field, universe: quarters)
    series = P.build_ttm_value_series("revenue", {"BBB"})
    assert "BBB" not in series                          # never four contiguous quarters


# --------------------------------------------------------------------------- #
# As-of lookups: latest window available strictly before cutoff; YoY pairing.
# --------------------------------------------------------------------------- #
def test_ttm_asof_respects_availability_cutoff():
    wins = [(TS("2021-12-31"), TS("2022-02-20"), 46.0),
            (TS("2022-03-31"), TS("2022-05-15"), 50.0)]
    # Before the second window is available, only the first is usable.
    got = P._ttm_asof(wins, cutoff=TS("2022-04-01"))
    assert got == (TS("2021-12-31"), 46.0)
    # After both, the latest window wins.
    got = P._ttm_asof(wins, cutoff=TS("2022-06-01"))
    assert got == (TS("2022-03-31"), 50.0)
    # Nothing available yet.
    assert P._ttm_asof(wins, cutoff=TS("2022-01-01")) is None


def test_ttm_asof_yoy_matches_year_ago_window():
    wins = [(TS("2021-03-31"), TS("2021-05-10"), 40.0),
            (TS("2021-06-30"), TS("2021-08-10"), 42.0),
            (TS("2021-09-30"), TS("2021-11-10"), 44.0),
            (TS("2021-12-31"), TS("2022-02-10"), 46.0),
            (TS("2022-03-31"), TS("2022-05-10"), 50.0)]
    yoy = P._ttm_asof_yoy(wins, cutoff=TS("2022-06-01"))
    assert yoy is not None
    cur, prev = yoy
    assert cur == pytest.approx(50.0)                   # latest window end 2022-03-31
    assert prev == pytest.approx(40.0)                  # ~1yr earlier window end 2021-03-31


def test_ttm_asof_yoy_none_without_year_ago_window():
    # Only two windows a quarter apart: no window within tolerance of (end - 1yr).
    wins = [(TS("2022-09-30"), TS("2022-11-10"), 12.0),
            (TS("2022-12-31"), TS("2023-02-10"), 13.0)]
    assert P._ttm_asof_yoy(wins, cutoff=TS("2023-06-01")) is None


# --------------------------------------------------------------------------- #
# Decision logic.
# --------------------------------------------------------------------------- #
def _passing_safety_gates():
    # Minimal gate set: a passing leakage check and all safety gates PASS.
    gates = [{"gate_name": "no_lookahead_gate", "status": "PASS"}]
    for nm in ("no_orders_gate", "no_live_data_gate", "no_paid_api_gate",
               "no_universe_broadening_gate", "no_data_collection_gate",
               "no_regime_throttle_gate", "no_paper_trader_gate", "no_gcp_gate",
               "no_d_drive_write_gate", "preview_only_gate"):
        gates.append({"gate_name": nm, "status": "PASS"})
    return gates


def test_derive_recommendation_improved():
    rec = P._derive_recommendation(
        _passing_safety_gates(), approved_buckets=["momentum", "value_ttm", "quality_ttm"],
        ttm_ic=0.025, ttm_t=1.6, incremental_ic=0.0073, t_change=0.35,
        ttm_coverage_sufficient=True, strictly_forward={"strictly_forward": True},
        placebo_collapsed=True)
    assert rec == P.REC_IMPROVED


def test_derive_recommendation_weak_when_positive_but_below_gate():
    rec = P._derive_recommendation(
        _passing_safety_gates(), approved_buckets=["momentum", "value_ttm", "quality_ttm", "growth_ttm"],
        ttm_ic=0.013954, ttm_t=0.92, incremental_ic=-0.003772, t_change=-0.32,
        ttm_coverage_sufficient=True, strictly_forward={"strictly_forward": True},
        placebo_collapsed=True)
    assert rec == P.REC_WEAK


def test_derive_recommendation_needs_universe_when_nonpositive():
    rec = P._derive_recommendation(
        _passing_safety_gates(), approved_buckets=["momentum", "value_ttm"],
        ttm_ic=-0.004, ttm_t=-0.3, incremental_ic=-0.021, t_change=-1.5,
        ttm_coverage_sufficient=True, strictly_forward={"strictly_forward": True},
        placebo_collapsed=True)
    assert rec == P.REC_NEEDS_UNIVERSE


def test_derive_recommendation_needs_universe_when_coverage_insufficient():
    rec = P._derive_recommendation(
        _passing_safety_gates(), approved_buckets=["momentum"],
        ttm_ic=0.03, ttm_t=2.1, incremental_ic=0.02, t_change=1.0,
        ttm_coverage_sufficient=False, strictly_forward={"strictly_forward": True},
        placebo_collapsed=True)
    assert rec == P.REC_NEEDS_UNIVERSE


def test_derive_recommendation_needs_review_on_placebo_or_leakage():
    base = dict(approved_buckets=["momentum", "value_ttm", "quality_ttm"],
                ttm_ic=0.05, ttm_t=3.0, incremental_ic=0.03, t_change=1.5,
                ttm_coverage_sufficient=True)
    # Placebo did not collapse -> NEEDS_REVIEW regardless of strong IC.
    assert P._derive_recommendation(_passing_safety_gates(),
                                    strictly_forward={"strictly_forward": True},
                                    placebo_collapsed=False, **base) == P.REC_NEEDS_REVIEW
    # Forward-label violation -> NEEDS_REVIEW.
    assert P._derive_recommendation(_passing_safety_gates(),
                                    strictly_forward={"strictly_forward": False},
                                    placebo_collapsed=True, **base) == P.REC_NEEDS_REVIEW


def test_derive_recommendation_needs_review_on_safety_fail():
    gates = _passing_safety_gates()
    gates.append({"gate_name": "no_orders_gate", "status": "FAIL"})
    rec = P._derive_recommendation(
        gates, approved_buckets=["momentum", "value_ttm", "quality_ttm"],
        ttm_ic=0.05, ttm_t=3.0, incremental_ic=0.03, t_change=1.5,
        ttm_coverage_sufficient=True, strictly_forward={"strictly_forward": True},
        placebo_collapsed=True)
    assert rec == P.REC_NEEDS_REVIEW


# --------------------------------------------------------------------------- #
# Gate matrix: all safety gates PASS; improvement gates reflect the metrics.
# --------------------------------------------------------------------------- #
def test_gate_matrix_safety_all_pass_and_improvement_reflects_metrics():
    gates, counts = P._build_gate_matrix(
        strictly_forward={"strictly_forward": True, "n_violations": 0},
        placebo_collapsed=True, comp_graded={"ic_t_stat": 0.9},
        approved_buckets=["momentum", "value_ttm", "quality_ttm", "growth_ttm"],
        ttm_ic=0.013954, ttm_t=0.92, incremental_ic=-0.003772, t_change=-0.32,
        ttm_coverage_sufficient=True, core_ready={"revenue": 68, "net_income": 106,
                                                   "operating_cash_flow": 116},
        baseline_consistent=True, sector_pit=False)
    by = {g["gate_name"]: g["status"] for g in gates}
    # Safety gates all PASS.
    for nm in ("no_orders_gate", "no_live_data_gate", "no_universe_broadening_gate",
               "no_data_collection_gate", "no_regime_throttle_gate", "preview_only_gate"):
        assert by[nm] == "PASS"
    # Improvement gates FAIL given a below-bar weak result.
    assert by["improvement_gate"] == "FAIL"
    assert by["absolute_ic_gate"] == "FAIL"
    assert by["t_stat_improves_gate"] == "FAIL"
    assert by["nonnegative_ic_gate"] == "PASS"          # 0.0139 >= 0
    assert by["ttm_coverage_sufficient_gate"] == "PASS"
    assert counts["FAIL"] >= 3


# --------------------------------------------------------------------------- #
# Attribution table shape.
# --------------------------------------------------------------------------- #
def test_attribution_table_rows_and_incremental():
    base = {"mean_rank_ic": 0.0177, "ic_t_stat": 1.25, "n_periods": 100}
    buckets = {"momentum": {"mean_rank_ic": 0.0079, "ic_t_stat": 0.5, "n_periods": 100},
               "value_ttm": {"mean_rank_ic": 0.023, "ic_t_stat": 1.4, "n_periods": 90},
               "quality_ttm": {"mean_rank_ic": -0.0127, "ic_t_stat": -0.8, "n_periods": 90},
               "growth_ttm": {"mean_rank_ic": -0.0035, "ic_t_stat": -0.2, "n_periods": 80}}
    comp = {"mean_rank_ic": 0.0139, "ic_t_stat": 0.92, "n_periods": 100}
    table = P._build_attribution(base, buckets, comp, baseline_ic=0.0177)
    names = [r["series"] for r in table]
    assert names == ["phase7f_baseline_composite", "phase7h_momentum_only",
                     "phase7h_value_ttm_bucket", "phase7h_quality_ttm_bucket",
                     "phase7h_growth_ttm_bucket", "phase7h_final_composite"]
    final = table[-1]
    assert final["incremental_vs_7f"] == pytest.approx(0.0139 - 0.0177, abs=1e-9)
    assert table[0]["incremental_vs_7f"] == pytest.approx(0.0, abs=1e-9)


# --------------------------------------------------------------------------- #
# End-to-end (guarded): real offline pipeline.
# --------------------------------------------------------------------------- #
@_real
def test_end_to_end_real_data(tmp_path):
    report = P.run(out_dir=str(tmp_path))
    assert report["recommendation"] in P.ALLOWED_RECOMMENDATIONS
    assert report["recommendation"] != P.REC_ERROR

    # The recomputed 7-F baseline must reproduce the published +0.0177 / t~1.25.
    bl = report["phase7f_baseline"]
    assert bl["recompute_consistent_with_published"] is True
    assert abs(bl["recomputed_ic"] - P.PHASE7F_BASELINE_IC) <= P.BASELINE_RECOMPUTE_TOL

    # The phase answers the YES/NO question and it is internally consistent.
    ans = report["did_dense_ttm_improve_signal_quality"]
    assert ans in ("YES", "NO")
    assert (ans == "YES") == (report["recommendation"] == P.REC_IMPROVED)
    assert report["signal_quality_improved"] == (report["recommendation"] == P.REC_IMPROVED)

    # Core TTM continuity is sufficient (Phase 7-G result carried forward).
    core = report["core_ttm_continuity_ready"]
    for fld in ("revenue", "net_income", "operating_cash_flow"):
        assert core[fld] >= P.MIN_NAMES

    # Leakage / placebo safety.
    assert report["leakage_checks"]["strictly_forward_labels"]["strictly_forward"] is True
    assert report["leakage_checks"]["placebo_collapsed"] is True

    # Safety flags all false except preview_only.
    for flag in ("live_data_used", "paid_apis_used", "orders_enabled", "automation_enabled",
                 "universe_broadened", "data_collected", "regime_throttle_activated",
                 "factor_weights_optimized", "factor_signs_flipped",
                 "regimes_used_to_select_or_weight", "d_drive_written",
                 "paper_trader_touched", "gcp_touched"):
        assert report[flag] is False
    assert report["preview_only"] is True

    # All ten committed-safe artifacts exist.
    for name in ("phase7h_ttm_fundamental_signal_retest.json", "ttm_panel_coverage.csv",
                 "ttm_factor_catalog.csv", "ttm_factor_scoreboard.csv", "ttm_bucket_scoreboard.csv",
                 "ttm_composite_scoreboard.csv", "ttm_attribution_table.csv",
                 "ttm_regime_diagnostic_scoreboard.csv", "ttm_signal_gate_matrix.csv",
                 "phase7i_next_plan.json"):
        assert (Path(tmp_path) / name).exists(), name

    # Attribution table contains the six required rows.
    assert len(report["attribution_table"]) == 6


@_real
def test_end_to_end_value_bucket_present(tmp_path):
    # The value_ttm bucket must be built (TTM yields), i.e. the dense panel fed factors.
    report = P.run(out_dir=str(tmp_path))
    assert "value_ttm" in report["approved_buckets"]
    assert report["bucket_ic"]["value_ttm"] is not None
