"""Fully-offline targeted tests for Phase 18-A - parallel champion vs sector-repaired
challenger paper tournament.

Proves: (a) the four frozen books are reconstructed in isolation (distinct member lists,
Top25/Top50 never mixed, champion/challenger signals never mixed); (b) comparisons are
same-date only and SPY-aligned; (c) no future-price leakage (``_price_at_or_before`` looks
only backward) and no synthetic prices (a missing ticker is uncovered, never fabricated);
(d) no reranking / rebalancing (reconstructed members == frozen input); (e) de-duplication
by book and financial date; (f) the a-priori decision ladder across the mid-cycle,
checkpoint (KEEP / PROMOTION_ELIGIBLE / REJECT / EXTEND / CHECKPOINT_REVIEW) and both
blocked branches; (g) no champion replacement and no live promotion in any decision, and no
network keys in the artifacts; and (h) an integration run over the REAL committed packages
(skipped if absent) that reproduces the frozen entries, returns an ALLOWED decision, leaves
the source packages byte-for-byte unchanged, and writes nothing to the Paper Trader DB.
"""
from __future__ import annotations

import csv
import hashlib
import importlib
import json
from pathlib import Path

import pytest

MOD = importlib.import_module("research.run_phase18a_parallel_challenger_forward_test")

# A short, deterministic business-day calendar (Mon-Fri), signal on the first date.
_SIGNAL = "2026-01-05"
_DATES = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09",
          "2026-01-12", "2026-01-13", "2026-01-14"]


# --------------------------------------------------------------------------- #
# Synthetic owned-data fixtures.
# --------------------------------------------------------------------------- #
def _write_eod(eod_dir: Path, ticker: str, closes):
    eod_dir.mkdir(parents=True, exist_ok=True)
    bars = [{"date": d, "adjusted_close": c} for d, c in zip(_DATES, closes)]
    (eod_dir / ("%s.json" % ticker)).write_text(json.dumps(bars), encoding="utf-8")


def _write_spy(path: Path, closes, dates=None):
    dates = dates or _DATES
    rows = [{"mark_date": d, "adjusted_close": c} for d, c in zip(dates, closes)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ticker": "SPY", "rows": rows}), encoding="utf-8")


def _write_champion_book(path: Path, members):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "side", "target_weight", "sector", "signal_composite_sn",
                    "signal_date", "price_source", "entry_reference_date", "entry_price",
                    "order_action", "review_status"])
        for tk, sec, score, entry in members:
            w.writerow([tk, "LONG", 0.04, sec, score, _SIGNAL, "EODHD_LOCAL",
                        _SIGNAL, ("" if entry is None else entry), "NO_ORDER",
                        "PAPER_REVIEW_ONLY"])


def _write_challenger_book(path: Path, members):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "side", "target_weight", "repaired_sector",
                    "signal_composite_sn_repaired", "signal_date", "price_source",
                    "has_local_price", "order_action", "review_status", "live_weight_status"])
        for tk, sec, score, has in members:
            w.writerow([tk, "LONG", 0.04, sec, score, _SIGNAL, "EODHD_LOCAL(owned)",
                        has, "NO_ORDER", "PAPER_REVIEW_ONLY", "NO_LIVE_WEIGHTS"])


def _make_synthetic(tmp: Path, *, champ_extra=(), chall_extra=()):
    """Build a self-contained owned-data world with enough covered names to clear the
    coverage floor. Champion = AAA/BBB/CCC/EEE/FFF/GGG; challenger = AAA/BBB/DDD/EEE/FFF/HHH
    (AAA/BBB/EEE/FFF overlap expected; CCC/GGG champion-only; DDD/HHH challenger-only). A
    book can carry an extra member with NO eod file to exercise missing-price handling."""
    eod = tmp / "eod"
    for i, (tk, base) in enumerate((("AAA", 100.0), ("BBB", 50.0), ("CCC", 200.0),
                                    ("DDD", 25.0), ("EEE", 80.0), ("FFF", 120.0),
                                    ("GGG", 60.0), ("HHH", 40.0))):
        # distinct per-ticker drift so best/worst contributors are well-defined
        drift = 0.005 + 0.002 * i
        _write_eod(eod, tk, [base * (1 + drift * j) for j in range(len(_DATES))])
    spy = tmp / "spy.json"
    _write_spy(spy, [400.0 * (1 + 0.002 * i) for i in range(len(_DATES))])

    champ_pkg = tmp / "champion_pkg"
    chm = [("AAA", "Unknown", 9.0, 100.0), ("BBB", "Unknown", 8.0, 50.0),
           ("CCC", "Unknown", 7.0, 200.0), ("EEE", "Unknown", 6.0, 80.0),
           ("FFF", "Unknown", 5.0, 120.0), ("GGG", "Unknown", 4.0, 60.0)] + list(champ_extra)
    _write_champion_book(champ_pkg / "current_alpha_paper_portfolio_top25.csv", chm)
    _write_champion_book(champ_pkg / "current_alpha_paper_portfolio_top50.csv", chm)

    chall_pkg = tmp / "challenger_pkg"
    chl = [("AAA", "Information Technology", 8.5, "True"),
           ("BBB", "Health Care", 7.5, "True"), ("DDD", "Materials", 6.5, "True"),
           ("EEE", "Industrials", 6.0, "True"), ("FFF", "Energy", 5.5, "True"),
           ("HHH", "Financials", 4.5, "True")] + list(chall_extra)
    _write_challenger_book(chall_pkg / "challenger_paper_portfolio_top25.csv", chl)
    _write_challenger_book(chall_pkg / "challenger_paper_portfolio_top50.csv", chl)

    reval = tmp / "reval"
    reval.mkdir(parents=True, exist_ok=True)
    with open(reval / "phase17a_names_entering_leaving.csv", "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["book", "direction", "ticker", "repaired_sector", "composite_sn"])
        w.writerow(["TOP25", "ENTERING", "DDD", "Materials", "6.5"])
        w.writerow(["TOP25", "LEAVING", "CCC", "Unknown", "7.0"])
    return {"eod": eod, "spy": spy, "champ": champ_pkg, "chall": chall_pkg, "reval": reval}


def _run(tmp: Path, *, today="2026-01-15", write=True, **extra):
    f = _make_synthetic(tmp, **extra)
    return MOD.run(champion_pkg=f["champ"], challenger_pkg=f["chall"], reval_dir=f["reval"],
                   eod_dir=f["eod"], spy_json=f["spy"], out_dir=tmp / "out",
                   signal_date=_SIGNAL, today=today, write=write), f


# --------------------------------------------------------------------------- #
# Pure helpers.
# --------------------------------------------------------------------------- #
def test_price_at_or_before_never_looks_forward():
    bars = [("2026-01-05", 10.0), ("2026-01-06", 11.0), ("2026-01-08", 12.0)]
    assert MOD._price_at_or_before(bars, "2026-01-07") == ("2026-01-06", 11.0)
    assert MOD._price_at_or_before(bars, "2026-01-08") == ("2026-01-08", 12.0)
    # a date before the first bar -> nothing (never a future price carried backward)
    assert MOD._price_at_or_before(bars, "2026-01-01") is None


def test_concentration_and_drawdown():
    assert MOD._concentration_top5([1, 1, 1, 1, 1, 1]) == pytest.approx(83.33, abs=0.01)
    dd = MOD._max_drawdown([0.0, 5.0, -3.0, 2.0], ["a", "b", "c", "d"])
    assert dd["max_drawdown_pct"] < 0
    assert dd["max_drawdown_trough_date"] == "c"


# --------------------------------------------------------------------------- #
# Book isolation + Top25/Top50 + champion/challenger.
# --------------------------------------------------------------------------- #
def test_four_books_isolated(tmp_path):
    report, _ = _run(tmp_path)
    iso = report["book_isolation"]
    assert iso["all_isolated"] is True
    assert iso["distinct_member_lists"] is True
    assert iso["top25_top50_not_mixed"] is True
    assert iso["champion_challenger_signal_isolated"] is True
    # champion books carry composite_sn; challenger books carry composite_sn_repaired
    assert report["book_summaries"]["champion_top25"]["signal"] == MOD.CHAMPION_SIGNAL
    assert report["book_summaries"]["challenger_top25"]["signal"] == MOD.CHALLENGER_SIGNAL


def test_no_reranking_or_rebalancing(tmp_path):
    report, f = _run(tmp_path)
    # reconstructed membership equals the frozen CSV order/names, unchanged across dates
    members = MOD.load_book_members(f["champ"] / "current_alpha_paper_portfolio_top25.csv")
    assert [m["ticker"] for m in members] == ["AAA", "BBB", "CCC", "EEE", "FFF", "GGG"]
    assert report["frozen_books"] is True and report["reranked"] is False \
        and report["rebalanced"] is False


def test_missing_ticker_is_uncovered_not_synthetic(tmp_path):
    # a champion member with no eod file must be uncovered (never a fabricated price)
    report, _ = _run(tmp_path, champ_extra=[("ZZZ", "Unknown", 1.0, None)])
    a = report["book_summaries"]["champion_top25"]
    assert a["total_count"] == 7 and a["covered_count"] == 6  # ZZZ excluded, not invented
    assert any("champion_top25" in w for w in report["coverage_warnings"])
    # ZZZ is surfaced in the missing-price report with an honest reason (no fabricated price)
    rows = list(csv.DictReader(
        open(tmp_path / "out" / "phase18a_missing_price_report.csv", encoding="utf-8")))
    zzz = [r for r in rows if r["ticker"] == "ZZZ" and r["book_key"] == "champion_top25"]
    assert zzz and zzz[0]["reason"] == "NO_LOCAL_OWNED_EOD_PRICE"


def test_never_substitutes_price_across_books(tmp_path):
    # DDD is challenger-only; CCC is champion-only. Neither leaks into the other's marks.
    report, _ = _run(tmp_path)
    champ = report["book_summaries"]["champion_top25"]
    chall = report["book_summaries"]["challenger_top25"]
    # champion best/worst never reference DDD; challenger never references CCC
    champ_names = {champ["best_contributor"]["ticker"], champ["worst_contributor"]["ticker"]}
    chall_names = {chall["best_contributor"]["ticker"], chall["worst_contributor"]["ticker"]}
    assert "DDD" not in champ_names
    assert "CCC" not in chall_names


# --------------------------------------------------------------------------- #
# Same-date comparison + SPY alignment + dedup.
# --------------------------------------------------------------------------- #
def test_same_date_comparison_and_spy_alignment(tmp_path):
    report, _ = _run(tmp_path)
    assert report["top25_head_to_head"]["same_date_comparison"] is True
    assert report["top50_head_to_head"]["same_date_comparison"] is True
    # excess == book cumulative - SPY cumulative at the latest common date
    a = report["book_summaries"]["champion_top50"]
    assert a["excess_return_vs_spy_pct_points"] == pytest.approx(
        a["cumulative_return_pct"] - a["spy_cumulative_return_pct"], abs=1e-3)
    assert report["spy"]["available"] is True


def test_calendar_dedup_by_financial_date(tmp_path):
    # a duplicated SPY date must collapse to a single mark on the common calendar
    f = _make_synthetic(tmp_path)
    series = {"AAA": MOD.load_eod_series(f["eod"], "AAA")}
    dup_bars = [(d, 400.0) for d in (_DATES + [_DATES[-1]])]  # last date duplicated
    cal, latest, _meta = MOD.build_common_calendar(
        dup_bars, series, _SIGNAL, MOD._today("2026-01-15"))
    assert len(cal) == len(set(cal))         # unique per financial date
    assert cal == sorted(cal)                # ascending
    assert latest is not None


# --------------------------------------------------------------------------- #
# Decision ladder (Part C) - declared before results.
# --------------------------------------------------------------------------- #
def _h2h(size, ch_excess, cp_excess, ch_dd=-5.0, ch_conc=40.0):
    return {
        "book_size": size,
        "champion": {"excess_return_vs_spy_pct_points": cp_excess, "max_drawdown_pct": -5.0,
                     "contributor_concentration_top5_pct": 40.0},
        "challenger": {"excess_return_vs_spy_pct_points": ch_excess, "max_drawdown_pct": ch_dd,
                       "contributor_concentration_top5_pct": ch_conc},
    }


def _decide(**kw):
    base = dict(elapsed_marks=70, spy_available=True, calendars_aligned=True,
                books_isolated=True, coverage={"a": 20, "b": 20, "c": 20, "d": 20},
                h2h_top25=_h2h(25, 3.0, 1.0), h2h_top50=_h2h(50, 3.0, 1.0))
    base.update(kw)
    return MOD.decide_tournament(**base)["status"]


def test_mid_cycle_is_always_monitoring(tmp_path):
    report, _ = _run(tmp_path)
    assert report["decision"] == MOD.DEC_MONITORING
    assert report["no_automatic_winner"] is True
    # even with a strong challenger, mid-cycle never names a winner
    assert _decide(elapsed_marks=10) == MOD.DEC_MONITORING


def test_checkpoint_promotion_eligible():
    assert _decide(elapsed_marks=63, h2h_top25=_h2h(25, 3.0, 1.0),
                   h2h_top50=_h2h(50, 3.0, 1.0)) == MOD.DEC_PROMOTION_ELIGIBLE


def test_checkpoint_keep_champion():
    assert _decide(h2h_top25=_h2h(25, 1.0, 3.0),
                   h2h_top50=_h2h(50, 1.0, 3.0)) == MOD.DEC_KEEP


def test_checkpoint_reject_not_cost_positive():
    # challenger excess below the round-trip cost -> not positive after cost -> reject
    assert _decide(h2h_top25=_h2h(25, 0.1, 1.0),
                   h2h_top50=_h2h(50, 0.1, 1.0)) == MOD.DEC_REJECT


def test_checkpoint_reject_risk_breach():
    assert _decide(h2h_top25=_h2h(25, 3.0, 1.0, ch_dd=-40.0),
                   h2h_top50=_h2h(50, 3.0, 1.0)) == MOD.DEC_REJECT


def test_checkpoint_mixed_is_review():
    # cost-positive on both, but champion better on one and challenger on the other
    assert _decide(h2h_top25=_h2h(25, 3.0, 1.0),
                   h2h_top50=_h2h(50, 1.0, 3.0)) == MOD.DEC_CHECKPOINT_REVIEW


def test_checkpoint_extend_when_excess_missing():
    assert _decide(h2h_top25=_h2h(25, None, 1.0),
                   h2h_top50=_h2h(50, 3.0, 1.0)) == MOD.DEC_EXTEND


def test_blocked_insufficient_coverage():
    assert _decide(coverage={"a": 2, "b": 20, "c": 20, "d": 20}) == MOD.DEC_BLOCKED_COVERAGE


def test_blocked_data_mismatch():
    assert _decide(spy_available=False) == MOD.DEC_BLOCKED_MISMATCH
    assert _decide(calendars_aligned=False) == MOD.DEC_BLOCKED_MISMATCH
    assert _decide(books_isolated=False) == MOD.DEC_BLOCKED_MISMATCH


# --------------------------------------------------------------------------- #
# Safety: never replaces the champion, never promotes to live, no key leak.
# --------------------------------------------------------------------------- #
def test_decision_always_allowed_never_forbidden(tmp_path):
    report, _ = _run(tmp_path)
    assert report["decision"] in MOD.ALLOWED_DECISIONS
    for bad in MOD.FORBIDDEN_DECISIONS:
        assert bad not in MOD.ALLOWED_DECISIONS
        assert report["decision"] != bad


def test_no_champion_replacement_no_live_promotion(tmp_path):
    report, _ = _run(tmp_path)
    for k in ("champion_replaced", "promotes_to_live", "live_trading", "creates_orders",
              "creates_signals", "creates_trade_decisions", "creates_broker_connection",
              "creates_automation", "wrote_to_paper_trader", "performs_network",
              "uses_paid_data"):
        assert report[k] is False
    assert report["offline"] is True and report["uses_owned_data_only"] is True
    assert report["order_action_all"] == "NO_ORDER"


def test_required_artifacts_written_and_no_key_leak(tmp_path):
    _run(tmp_path)
    out = tmp_path / "out"
    required = [
        "phase18a_parallel_challenger_forward_test_report.json", "phase18a_book_summary.csv",
        "phase18a_daily_marks.csv", "phase18a_champion_vs_challenger.csv",
        "phase18a_rolling_metrics.csv", "phase18a_contributors.csv",
        "phase18a_sector_exposure.csv", "phase18a_coverage_by_date.csv",
        "phase18a_missing_price_report.csv", "phase18a_decision_scorecard.csv",
        "phase18a_source_manifest.csv", "phase18a_secret_safety_audit.csv",
    ]
    for name in required:
        assert (out / name).is_file(), name
    blob = "".join((out / n).read_text(encoding="utf-8") for n in required).lower()
    for token in ("api_token", "api_key=", "authorization", "secret", "bearer "):
        assert token not in blob, token


# --------------------------------------------------------------------------- #
# Integration over the REAL committed packages (skipped if absent).
# --------------------------------------------------------------------------- #
def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_real_packages_integration_and_immutability(tmp_path):
    champ = MOD._DEF_CHAMPION_PKG
    chall = MOD._DEF_CHALLENGER_PKG
    eod = MOD._DEF_EOD_DIR
    spy = Path(MOD._DEF_SPY_JSON)
    if not (champ.is_dir() and chall.is_dir() and eod.is_dir() and spy.is_file()):
        pytest.skip("real owned packages / SPY strip not present")
    watched = [champ / "current_alpha_paper_portfolio_top25.csv",
               champ / "current_alpha_paper_portfolio_top50.csv",
               chall / "challenger_paper_portfolio_top25.csv",
               chall / "challenger_paper_portfolio_top50.csv"]
    before = {p: _sha(p) for p in watched}
    report = MOD.run(champion_pkg=champ, challenger_pkg=chall, reval_dir=MOD._DEF_REVAL_DIR,
                     eod_dir=eod, spy_json=spy, out_dir=tmp_path / "out",
                     today="2026-07-18", write=True)
    assert report["decision"] in MOD.ALLOWED_DECISIONS
    assert report["decision"] == MOD.DEC_MONITORING  # <63 marks in the owned window
    assert report["reproduction"]["reproduces_stored_entries"] is True
    assert report["reproduction"]["max_abs_error"] == 0.0
    assert report["book_isolation"]["all_isolated"] is True
    assert report["spy"]["available"] is True
    assert report["champion_replaced"] is False and report["promotes_to_live"] is False
    # source packages are byte-for-byte unchanged
    assert {p: _sha(p) for p in watched} == before
