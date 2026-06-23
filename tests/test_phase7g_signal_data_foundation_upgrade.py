"""Tests for Phase 7-G — Signal Data Foundation Upgrade.

Unit tests for the de-cumulation / TTM readiness logic and the gate/recommendation
machinery, plus guarded end-to-end tests over the real local artifacts. No live data,
no network, no writes outside a tmp dir.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_RUNNER = _REPO / "research" / "run_phase7g_signal_data_foundation_upgrade.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("phase7g_runner_test", _RUNNER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


G = _load_module()

_REAL_DATA = G.FUNDAMENTALS_CSV.exists() and Path(str(G.PRICE_CSV)).exists()
_real = pytest.mark.skipif(not _REAL_DATA, reason="local price/fundamentals artifacts absent")


# --------------------------------------------------------------------------- #
# Static / vocabulary.
# --------------------------------------------------------------------------- #
def test_recommendation_vocabulary():
    assert set(G.ALLOWED_RECOMMENDATIONS) == {
        "READY_FOR_PHASE7H_RETEST_SIGNAL_ON_UPGRADED_DATA",
        "NEEDS_CONTROLLED_DATA_COLLECTION",
        "DATA_FOUNDATION_PARTIAL",
        "DATA_QUALITY_BLOCKED",
        "ERROR",
    }


def test_clean_frame_regex():
    assert G.CLEAN_FRAME_RE.fullmatch("CY2017Q1")
    assert G.CLEAN_FRAME_RE.fullmatch("CY2024Q3")
    assert not G.CLEAN_FRAME_RE.fullmatch("CY2017")      # annual frame
    assert not G.CLEAN_FRAME_RE.fullmatch("CY2017Q1X")


# --------------------------------------------------------------------------- #
# De-cumulation arithmetic (synthetic SEC artifact).
# --------------------------------------------------------------------------- #
def _synthetic_fundamentals(tmp_path) -> Path:
    """One ticker, one fiscal year of YTD revenue + an annual 10-K, no clean frames."""
    rows = [
        # Q1 YTD = 100 (== Q1 3-month)
        dict(ticker="TST", normalized_field="revenue", fiscal_period_end="2021-03-31",
             fiscal_year=2021, fiscal_period="Q1", form="10-Q", frame="",
             value=100.0, point_in_time_usable=True, availability_datetime="2021-04-30"),
        # Q2 YTD = 250 -> Q2 3-month = 150
        dict(ticker="TST", normalized_field="revenue", fiscal_period_end="2021-06-30",
             fiscal_year=2021, fiscal_period="Q2", form="10-Q", frame="",
             value=250.0, point_in_time_usable=True, availability_datetime="2021-07-30"),
        # Q3 YTD = 450 -> Q3 3-month = 200
        dict(ticker="TST", normalized_field="revenue", fiscal_period_end="2021-09-30",
             fiscal_year=2021, fiscal_period="Q3", form="10-Q", frame="",
             value=450.0, point_in_time_usable=True, availability_datetime="2021-10-30"),
        # FY annual = 700 -> Q4 3-month = 700 - 450 = 250
        dict(ticker="TST", normalized_field="revenue", fiscal_period_end="2021-12-31",
             fiscal_year=2021, fiscal_period="FY", form="10-K", frame="CY2021",
             value=700.0, point_in_time_usable=True, availability_datetime="2022-02-15"),
    ]
    df = pd.DataFrame(rows)
    p = tmp_path / "synthetic_fundamentals.csv"
    df.to_csv(p, index=False)
    return p


def test_build_3mo_quarters_decumulation(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "FUNDAMENTALS_CSV", _synthetic_fundamentals(tmp_path))
    qs = G.build_3mo_quarters("revenue", {"TST"})
    by_end = {q.quarter_end.strftime("%Y-%m-%d"): q for q in qs}
    assert by_end["2021-03-31"].value == pytest.approx(100.0)   # Q1 == YTD
    assert by_end["2021-03-31"].provenance == "q1_is_3mo"
    assert by_end["2021-06-30"].value == pytest.approx(150.0)   # 250 - 100
    assert by_end["2021-06-30"].provenance == "decumulated_ytd"
    assert by_end["2021-09-30"].value == pytest.approx(200.0)   # 450 - 250
    assert by_end["2021-12-31"].value == pytest.approx(250.0)   # 700 - 450
    assert by_end["2021-12-31"].provenance == "annual_minus_q3ytd"
    # Availability is the later (max) of the constituent legs — no leakage.
    assert by_end["2021-06-30"].avail == pd.Timestamp("2021-07-30")


def test_clean_q1_serves_as_ytd_baseline(tmp_path, monkeypatch):
    """If Q1 exists only as a clean 3-month frame, it still anchors Q2 de-cumulation."""
    rows = [
        dict(ticker="TST", normalized_field="revenue", fiscal_period_end="2021-03-31",
             fiscal_year=2021, fiscal_period="Q1", form="10-Q", frame="CY2021Q1",
             value=100.0, point_in_time_usable=True, availability_datetime="2021-04-30"),
        dict(ticker="TST", normalized_field="revenue", fiscal_period_end="2021-06-30",
             fiscal_year=2021, fiscal_period="Q2", form="10-Q", frame="",
             value=250.0, point_in_time_usable=True, availability_datetime="2021-07-30"),
    ]
    p = tmp_path / "f.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    monkeypatch.setattr(G, "FUNDAMENTALS_CSV", p)
    qs = G.build_3mo_quarters("revenue", {"TST"})
    by_end = {q.quarter_end.strftime("%Y-%m-%d"): q for q in qs}
    assert by_end["2021-03-31"].provenance == "clean_frame"
    assert by_end["2021-06-30"].value == pytest.approx(150.0)   # 250 - clean_Q1(100)
    assert by_end["2021-06-30"].provenance == "decumulated_ytd"


def test_decumulation_skips_non_contiguous_predecessor(tmp_path, monkeypatch):
    """A Q2 with no ~one-quarter-earlier Q1 cannot be de-cumulated (no leakage from a gap)."""
    rows = [
        dict(ticker="TST", normalized_field="revenue", fiscal_period_end="2020-03-31",
             fiscal_year=2020, fiscal_period="Q1", form="10-Q", frame="",
             value=100.0, point_in_time_usable=True, availability_datetime="2020-04-30"),
        # Q2 a full year later: predecessor gap ~365d, not 80-100d -> skipped.
        dict(ticker="TST", normalized_field="revenue", fiscal_period_end="2021-06-30",
             fiscal_year=2021, fiscal_period="Q2", form="10-Q", frame="",
             value=250.0, point_in_time_usable=True, availability_datetime="2021-07-30"),
    ]
    p = tmp_path / "f.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    monkeypatch.setattr(G, "FUNDAMENTALS_CSV", p)
    qs = G.build_3mo_quarters("revenue", {"TST"})
    ends = {q.quarter_end.strftime("%Y-%m-%d") for q in qs}
    assert "2020-03-31" in ends          # Q1 emitted
    assert "2021-06-30" not in ends      # Q2 cannot be de-cumulated


# --------------------------------------------------------------------------- #
# TTM windows.
# --------------------------------------------------------------------------- #
def test_ttm_windows_contiguity():
    def q(end, av, v):
        return G._Q3mo("TST", pd.Timestamp(end), pd.Timestamp(av), v, "decumulated_ytd")
    # Five contiguous quarters -> 2 trailing-4 windows.
    qs = [q("2021-03-31", "2021-05-01", 1), q("2021-06-30", "2021-08-01", 1),
          q("2021-09-30", "2021-11-01", 1), q("2021-12-31", "2022-02-01", 1),
          q("2022-03-31", "2022-05-01", 1)]
    wins = G.ttm_windows(qs)["TST"]
    assert len(wins) == 2
    # Window availability is the max of its 4 legs.
    assert wins[0][1] == pd.Timestamp("2022-02-01")


def test_ttm_windows_break_on_gap():
    def q(end, av):
        return G._Q3mo("TST", pd.Timestamp(end), pd.Timestamp(av), 1.0, "decumulated_ytd")
    # A missing quarter (gap) prevents any contiguous 4-window.
    qs = [q("2021-03-31", "2021-05-01"), q("2021-06-30", "2021-08-01"),
          q("2021-12-31", "2022-02-01"), q("2022-03-31", "2022-05-01")]
    assert "TST" not in G.ttm_windows(qs)


# --------------------------------------------------------------------------- #
# Gates + recommendation.
# --------------------------------------------------------------------------- #
def test_derive_recommendation_partial():
    rec = G.derive_recommendation({
        "safety_fail": False, "broader_universe_local": False,
        "pit_sector_local": False, "fundamental_density_upgradable": True})
    assert rec == G.REC_PARTIAL


def test_derive_recommendation_ready():
    rec = G.derive_recommendation({
        "safety_fail": False, "broader_universe_local": True,
        "pit_sector_local": True, "fundamental_density_upgradable": True})
    assert rec == G.REC_READY


def test_derive_recommendation_needs_collection():
    rec = G.derive_recommendation({
        "safety_fail": False, "broader_universe_local": False,
        "pit_sector_local": False, "fundamental_density_upgradable": False})
    assert rec == G.REC_NEEDS_COLLECTION


def test_derive_recommendation_blocked_on_safety():
    rec = G.derive_recommendation({
        "safety_fail": True, "broader_universe_local": True,
        "pit_sector_local": True, "fundamental_density_upgradable": True})
    assert rec == G.REC_BLOCKED


def test_gate_matrix_safety_all_pass():
    gates, summary = G.build_gate_matrix(
        {}, {"broader_local_universe_exists": False, "price_covered_tickers": 128,
             "sec_known_tickers": 10000, "addressable_but_uncollected": 9872},
        {"pit_sector_available_local": False},
        {"densification_feasible": True, "ttm_continuity_sufficient": True,
         "core_continuity_ready": {f: 50 for f in G.CORE_TTM_FIELDS},
         "field_summary": {f: {"total_3mo_quarters": 1000} for f in G.CORE_TTM_FIELDS}},
        {})
    assert summary["safety_fail"] is False
    safety = [g for g in gates if g["kind"] == "safety"]
    assert safety and all(g["status"] == "PASS" for g in safety)


# --------------------------------------------------------------------------- #
# Guarded end-to-end over the real local artifacts.
# --------------------------------------------------------------------------- #
@_real
def test_end_to_end_real_data(tmp_path):
    report = G.run(str(tmp_path))
    assert report["recommendation"] in G.ALLOWED_RECOMMENDATIONS
    assert report["recommendation"] != G.REC_ERROR

    # All 8 committed-safe artifacts exist.
    for name in ["phase7g_signal_data_foundation_upgrade.json", "universe_inventory.csv",
                 "local_data_source_inventory.csv", "sector_data_readiness.csv",
                 "quarterly_ttm_readiness.csv", "broader_universe_candidate.csv",
                 "data_foundation_gate_matrix.csv", "phase7h_next_plan.json"]:
        assert (tmp_path / name).exists(), name

    det = report["determinations"]
    # Honest local determinations for this dataset.
    assert det["broader_universe_exists_locally"] is False
    assert det["point_in_time_sectors_exist_locally"] is False
    assert det["denser_quarterly_ttm_feasible_locally"] is True
    assert det["phase7h_retest_allowed"] is True
    assert det["phase7h_retest_scope"] == "fundamental_density_only"

    # Safety contract.
    s = report["safety"]
    assert s["preview_only"] is True
    for k in ["live_data_used", "paid_api_used", "trading_order_automation",
              "factor_weights_optimized", "factor_signs_flipped",
              "regimes_used_to_select_or_weight", "wrote_to_price_drive",
              "committed", "pushed"]:
        assert s[k] is False, k

    gs = report["gate_matrix_summary"]
    assert gs["safety_fail"] is False
    assert gs["fail"] >= 3   # broader-universe, survivorship, PIT-sector all FAIL locally


@_real
def test_broader_universe_candidate_not_locally_expandable(tmp_path):
    G.run(str(tmp_path))
    df = pd.read_csv(tmp_path / "broader_universe_candidate.csv")
    assert len(df) > 0
    # No name is locally expandable (no broader local price source).
    assert (df["locally_expandable"].astype(str).str.lower() == "false").all()
    assert (df["has_local_price"].astype(str).str.lower() == "true").all()


@_real
def test_core_ttm_continuity_meets_min_names(tmp_path):
    report = G.run(str(tmp_path))
    ready = report["quarterly_ttm_readiness"]["core_continuity_ready"]
    for fld in G.CORE_TTM_FIELDS:
        assert ready[fld] >= G.MIN_NAMES, f"{fld} continuity below MIN_NAMES"


@_real
def test_quarterly_readiness_denser_than_clean_only(tmp_path):
    """De-cumulation must yield materially more 3-month quarters than clean frames alone."""
    report = G.run(str(tmp_path))
    fs = report["quarterly_ttm_readiness"]["field_summary"]
    for fld in G.CORE_TTM_FIELDS:
        prov = fs[fld]["provenance"]
        assert prov["decumulated_ytd"] > prov["clean_frame"]
