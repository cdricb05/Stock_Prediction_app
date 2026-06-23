"""Tests for Phase 7-J — Broad Universe Fundamentals Collection and Signal Retest.

Offline by default: the decision rule, gate matrix, prune helpers, ticker/CIK loading,
collection-status parsing, and the env/no-network gate are all exercised without any
network call. A guarded end-to-end test runs only when the broad price panel exists on D:.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from research import run_phase7j_broad_universe_signal_retest as P

_BROAD_PANEL_EXISTS = Path(P.BROAD_PRICE_PANEL_CSV).is_file()
_SEC_DIR_EXISTS = Path(P.SEC_COMPANY_TICKERS_JSON).is_file()


# --------------------------------------------------------------------------- #
# Recommendation vocabulary + thresholds.
# --------------------------------------------------------------------------- #
def test_recommendation_vocabulary_exact():
    assert P.ALLOWED_RECOMMENDATIONS == (
        "BROAD_UNIVERSE_SIGNAL_SURVIVED", "BROAD_UNIVERSE_SIGNAL_WEAK",
        "BROAD_UNIVERSE_SIGNAL_FAILED", "FUNDAMENTAL_COVERAGE_BLOCKED", "ERROR")


def test_thresholds_are_task_specified():
    assert P.IC_T_GATE == 1.5
    assert P.INCREMENTAL_GATE == 0.005
    assert P.MIN_USABLE_PRICE_TICKERS == 250
    assert P.MIN_FUND_COVERAGE_TICKERS == 200


def test_factor_buckets_match_phase7h():
    assert P.MOMENTUM_SUBFACTORS == ("mom_12_1", "mom_6_1", "mom_riskadj")
    assert set(P._VALUE_KEYS) == {"ttm_sales_yield", "ttm_earnings_yield",
                                  "ttm_operating_cashflow_yield", "ttm_fcf_yield"}
    assert set(P._QUALITY_KEYS) == {"ttm_net_margin", "ttm_operating_margin", "ttm_ocf_margin"}
    assert set(P._GROWTH_KEYS) == {"ttm_revenue_growth", "ttm_net_income_growth", "ttm_ocf_growth"}


# --------------------------------------------------------------------------- #
# Strict decision rule (borderline never rounded up).
# --------------------------------------------------------------------------- #
def _sig(ic, t, price_only_ic, fund_cov=250):
    incr = (ic - price_only_ic) if (ic is not None and price_only_ic is not None) else None
    return {"composite_ic": ic, "composite_t": t, "price_only_ic": price_only_ic,
            "incremental_ic": incr, "fund_coverage_tickers": fund_cov}


def test_decision_survived_all_bars_met():
    rec, yn = P.derive_recommendation(_sig(0.020, 1.6, 0.010), price_threshold_met=True)
    assert rec == "BROAD_UNIVERSE_SIGNAL_SURVIVED" and yn == "YES"


def test_decision_survived_t_stat_exact_boundary():
    # t exactly 1.5 (the >= bar) with incremental clearly above +0.005 -> survives.
    rec, yn = P.derive_recommendation(_sig(0.016, 1.5, 0.0095), price_threshold_met=True)
    assert rec == "BROAD_UNIVERSE_SIGNAL_SURVIVED" and yn == "YES"


def test_decision_weak_t_just_below():
    rec, yn = P.derive_recommendation(_sig(0.020, 1.49, 0.010), price_threshold_met=True)
    assert rec == "BROAD_UNIVERSE_SIGNAL_WEAK" and yn == "NO"


def test_decision_weak_incremental_just_below():
    rec, yn = P.derive_recommendation(_sig(0.0149, 1.8, 0.010), price_threshold_met=True)
    assert rec == "BROAD_UNIVERSE_SIGNAL_WEAK" and yn == "NO"


def test_decision_failed_nonpositive_ic():
    rec, yn = P.derive_recommendation(_sig(0.0, 0.1, 0.010), price_threshold_met=True)
    assert rec == "BROAD_UNIVERSE_SIGNAL_FAILED" and yn == "NO"
    rec2, _ = P.derive_recommendation(_sig(-0.01, -0.5, 0.010), price_threshold_met=True)
    assert rec2 == "BROAD_UNIVERSE_SIGNAL_FAILED"


def test_decision_coverage_blocked_low_fundamentals():
    # Great IC but only 100 tickers with fundamentals -> blocked, never a pass.
    rec, yn = P.derive_recommendation(_sig(0.05, 5.0, 0.010, fund_cov=100), price_threshold_met=True)
    assert rec == "FUNDAMENTAL_COVERAGE_BLOCKED" and yn == "NO"


def test_decision_coverage_blocked_price_precondition():
    rec, _ = P.derive_recommendation(_sig(0.05, 5.0, 0.010), price_threshold_met=False)
    assert rec == "FUNDAMENTAL_COVERAGE_BLOCKED"


def test_decision_blocked_when_ic_none():
    rec, _ = P.derive_recommendation(_sig(None, None, None), price_threshold_met=True)
    assert rec == "FUNDAMENTAL_COVERAGE_BLOCKED"


# --------------------------------------------------------------------------- #
# Gate matrix.
# --------------------------------------------------------------------------- #
def test_gate_matrix_safety_all_pass_on_survived():
    price_summary = {"usable_price_tickers": 296, "threshold_met": True}
    fund_summary = {"fundamental_rows": 9000, "tickers_with_any_fundamental": 280}
    sig = {"composite_ic": 0.02, "composite_t": 1.7, "incremental_ic": 0.01,
           "fund_coverage_tickers": 260, "approved_buckets": ["momentum", "value_ttm", "quality_ttm"],
           "strictly_forward": {"strictly_forward": True}, "placebo_collapsed": True}
    gates, counts = P.build_gate_matrix(price_summary, fund_summary, sig, "BROAD_UNIVERSE_SIGNAL_SURVIVED")
    assert counts["safety_fail"] is False
    # Every safety gate present and PASS.
    by = {g["gate"]: g["status"] for g in gates}
    for sg in P._SAFETY_GATES:
        assert by.get(sg) == "PASS", sg
    # Result gates reflect a pass.
    assert by["composite_ic_positive"] == "PASS"
    assert by["composite_t_stat_gate"] == "PASS"
    assert by["beats_price_only_baseline"] == "PASS"


def test_gate_matrix_result_gates_fail_honestly_on_weak():
    price_summary = {"usable_price_tickers": 296, "threshold_met": True}
    fund_summary = {"fundamental_rows": 9000, "tickers_with_any_fundamental": 280}
    sig = {"composite_ic": 0.012, "composite_t": 0.9, "incremental_ic": 0.001,
           "fund_coverage_tickers": 260, "approved_buckets": ["momentum", "value_ttm"],
           "strictly_forward": {"strictly_forward": True}, "placebo_collapsed": True}
    gates, counts = P.build_gate_matrix(price_summary, fund_summary, sig, "BROAD_UNIVERSE_SIGNAL_WEAK")
    by = {g["gate"]: g["status"] for g in gates}
    assert by["composite_t_stat_gate"] == "FAIL"
    assert by["beats_price_only_baseline"] == "FAIL"
    # A weak result must NOT trip any safety gate.
    assert counts["safety_fail"] is False


# --------------------------------------------------------------------------- #
# SEC payload prune helpers (keep full history; no prototype cap).
# --------------------------------------------------------------------------- #
def test_keep_companyfacts_keeps_all_periodic_facts():
    # 30 periodic facts (> the 3-F prototype cap of 24) must all survive.
    facts = [{"form": "10-Q", "end": "2020-%02d-30" % ((i % 12) + 1), "filed": "2020-01-01", "val": i}
             for i in range(30)]
    facts.append({"form": "8-K", "end": "2020-01-01", "filed": "2020-01-01", "val": 999})  # dropped
    full = {"cik": 1, "entityName": "X",
            "facts": {"us-gaap": {"Revenues": {"label": "Rev", "units": {"USD": facts}}}}}
    pruned = P._keep_companyfacts(full)
    kept = pruned["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
    assert len(kept) == 30  # all periodic kept, the 8-K dropped
    assert all(f["form"] in P.SEC._KEEP_FORMS for f in kept)


def test_keep_submissions_preserves_accession_acceptance_alignment():
    full = {"cik": 1, "name": "X", "filings": {"recent": {
        "accessionNumber": ["a1", "a2", "a3"],
        "form": ["10-K", "8-K", "10-Q"],
        "filingDate": ["2020-01-01", "2020-02-01", "2020-03-01"],
        "acceptanceDateTime": ["2020-01-01T17:00:00", "2020-02-01T10:00:00", "2020-03-01T16:00:00"],
        "reportDate": ["2019-12-31", "", "2020-02-29"]}}}
    pruned = P._keep_submissions(full)
    acc = P.SEC._accession_to_acceptance(pruned)
    assert acc.get("a1") == "2020-01-01T17:00:00"   # 10-K kept
    assert acc.get("a3") == "2020-03-01T16:00:00"   # 10-Q kept
    assert "a2" not in acc                            # 8-K dropped


# --------------------------------------------------------------------------- #
# Phase 7-I usable-ticker parsing.
# --------------------------------------------------------------------------- #
def test_read_phase7i_usable_tickers_filters_and_excludes_benchmark(tmp_path, monkeypatch):
    csv_path = tmp_path / "collection_status.csv"
    csv_path.write_text(
        "ticker,status,rows,usable,source\n"
        "NVDA,COLLECTED,2512,true,yfinance\n"
        "AAPL,CACHED,2512,true,cache\n"
        "SPY,COLLECTED,2512,true,yfinance\n"        # benchmark excluded
        "FOO,COLLECTED,100,false,yfinance\n"        # not usable
        "BAR,FAILED,0,false,yfinance\n"             # failed
        "NVDA,COLLECTED,2512,true,yfinance\n",      # duplicate -> deduped
        encoding="utf-8")
    monkeypatch.setattr(P, "PHASE7I_STATUS_CSV", csv_path)
    usable, summary = P.read_phase7i_usable_tickers()
    assert usable == ["AAPL", "NVDA"]
    assert summary["usable_price_tickers"] == 2
    assert summary["present"] is True


def test_read_phase7i_usable_tickers_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "PHASE7I_STATUS_CSV", tmp_path / "nope.csv")
    usable, summary = P.read_phase7i_usable_tickers()
    assert usable == [] and summary["present"] is False


# --------------------------------------------------------------------------- #
# Collection gate: no network + no cache makes no calls and collects nothing.
# --------------------------------------------------------------------------- #
def test_collect_no_network_no_cache_makes_no_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "DATA_ROOT", str(tmp_path / "d"))
    monkeypatch.setattr(P, "DATA_COMPANYFACTS_DIR", str(tmp_path / "d" / "cf"))
    monkeypatch.setattr(P, "DATA_SUBMISSIONS_DIR", str(tmp_path / "d" / "sub"))
    monkeypatch.setattr(P, "BROAD_FUNDAMENTALS_CSV", str(tmp_path / "d" / "broad.csv"))
    monkeypatch.setattr(P, "BROAD_FUND_SUMMARY_JSON", str(tmp_path / "d" / "summary.json"))
    fund_rows, status_rows, summary = P.collect_broad_fundamentals(
        ["AAA", "BBB"], {"AAA": 123, "BBB": 456}, network=False)
    assert fund_rows == []
    assert summary["network_requests"] == 0
    assert summary["network_used"] is False
    assert all(r["status"] == "SKIPPED_NO_NETWORK" for r in status_rows)


def test_collect_no_cik_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "DATA_ROOT", str(tmp_path / "d"))
    monkeypatch.setattr(P, "DATA_COMPANYFACTS_DIR", str(tmp_path / "d" / "cf"))
    monkeypatch.setattr(P, "DATA_SUBMISSIONS_DIR", str(tmp_path / "d" / "sub"))
    monkeypatch.setattr(P, "BROAD_FUNDAMENTALS_CSV", str(tmp_path / "d" / "broad.csv"))
    monkeypatch.setattr(P, "BROAD_FUND_SUMMARY_JSON", str(tmp_path / "d" / "summary.json"))
    _, status_rows, summary = P.collect_broad_fundamentals(["ZZZ"], {}, network=False)
    assert summary["no_cik"] == 1
    assert status_rows[0]["status"] == "NO_CIK"


# --------------------------------------------------------------------------- #
# Regime diagnostic is descriptive only.
# --------------------------------------------------------------------------- #
def test_regime_diagnostic_marks_diagnostic_only():
    graded = {"yearly_ic": {"2020": 0.01, "2021": -0.02}, "mean_rank_ic": 0.0}
    rows = P._regime_diagnostic(graded)
    assert len(rows) == 2
    assert all("diagnostic only" in r["note"] for r in rows)


def test_regime_diagnostic_handles_none():
    rows = P._regime_diagnostic(None)
    assert rows and rows[0]["regime"] == "n/a"


# --------------------------------------------------------------------------- #
# CIK map (guarded on the local SEC directory).
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not _SEC_DIR_EXISTS, reason="local SEC company_tickers.json not present")
def test_load_ticker_cik_map_real():
    cik_map = P.load_ticker_cik_map()
    assert isinstance(cik_map, dict) and len(cik_map) > 1000
    # market-cap leaders are present with integer CIKs.
    assert isinstance(cik_map.get("AAPL"), int)
    assert isinstance(cik_map.get("MSFT"), int)


# --------------------------------------------------------------------------- #
# Guarded end-to-end (no network; uses cache if present). Asserts all artifacts + valid rec.
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not _BROAD_PANEL_EXISTS, reason="broad price panel not present on D:")
def test_end_to_end_no_network(tmp_path):
    report = P.run(out_dir=str(tmp_path), network=False)
    assert report["recommendation"] in P.ALLOWED_RECOMMENDATIONS
    assert report["broad_universe_signal_survived"] in ("YES", "NO")
    paths = P._OutPaths(base=tmp_path)
    for art in (paths.report, paths.data_coverage, paths.collection_status, paths.factor_catalog,
                paths.factor_scoreboard, paths.bucket_scoreboard, paths.composite_scoreboard,
                paths.regime_scoreboard, paths.gate_matrix, paths.next_plan):
        assert Path(art).is_file(), art
    # The report on disk parses and matches.
    with open(paths.report, "r", encoding="utf-8") as fh:
        on_disk = json.load(fh)
    assert on_disk["recommendation"] == report["recommendation"]
