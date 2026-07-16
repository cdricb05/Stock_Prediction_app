"""Targeted tests for Phase 13-I - Historical Daily Mark Backfill + Analytics.

Every test injects a FAKE transport (deterministic in-memory EODHD payloads), so the
suite makes ZERO network calls and needs NO EODHD_API_KEY. It proves: frozen holdings
never change across dates (no rerank / no rebalance), the completed-EOD rule, one
observation per common trading date, Top-25/Top-50 isolation, SPY reference-price math,
daily + cumulative + excess return math, drawdown math, coverage thresholds, contributor
concentration math, latest-date reconciliation against the live Phase 13-G mark, that a
rejected reconciliation blocks analytics, that the API key is never printed/persisted,
and that dynamic D: data lives outside git.
"""
import json
import os
import py_compile
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

STEM = "phase13i_historical_daily_mark_backfill"
RUNNER = REPO / "research" / f"run_{STEM}.py"
DOCS = REPO / "docs" / f"{STEM}_v1.md"
PACKAGE_DIR = (REPO / "research" / "output"
               / "phase13a_current_champion_alpha_paper_test_package")

from research import run_phase13i_historical_daily_mark_backfill as m  # noqa: E402
from research import run_phase13g_daily_alpha_mark_refresh as g13      # noqa: E402


# --------------------------------------------------------------------------- #
# Fake EODHD transport with a multi-date history.
# --------------------------------------------------------------------------- #
def _bars(*rows):
    return [{"date": d, "close": c, "adjusted_close": c, "volume": 1000} for d, c in rows]


def _make_transport(series_by_symbol, *, raise_for=None):
    def transport(symbol, start):
        if raise_for and symbol in raise_for:
            raise raise_for[symbol]
        return series_by_symbol.get(symbol, [])
    return transport


class _FakeEodhdError(Exception):
    def __init__(self, error_type):
        super().__init__(error_type)
        self.error_type = error_type


# SPY defines the trading calendar. Book names all move by a shared factor per date,
# so every covered name has an identical cumulative return -> the book average equals
# (factor-1)*100 and every quantity is hand-checkable.
_ENTRY_DATE = "2026-05-22"
_FACTORS = [("2026-05-22", 1.00), ("2026-05-26", 1.10), ("2026-05-27", 1.05)]
_SPY = _bars(("2026-05-22", 500.0), ("2026-05-26", 505.0), ("2026-05-27", 510.0),
             ("2026-05-28", 999.0))  # 2026-05-28 == reference-today -> incomplete, excluded


def _universe_series(entry_default=100.0, drop=(), factors=_FACTORS, spy=_SPY):
    positions, _ = g13.load_source_universe(PACKAGE_DIR, None)
    series = {}
    for p in positions:
        sym = g13._clean_symbol(p["ticker"])
        if p["ticker"] in drop:
            series[sym] = []
            continue
        e = p["frozen_entry_price"] or entry_default
        series[sym] = _bars(*[(d, e * f) for d, f in factors])
    series[g13._clean_symbol("SPY")] = spy
    return series


def _run(tmp_path, transport=None, today="2026-05-28", write_ref=True):
    """Run backfill; optionally first write a live Phase 13-G mark to reconcile against."""
    tr = transport or _make_transport(_universe_series())
    if write_ref:
        g13.refresh(PACKAGE_DIR, None, tmp_path, transport=tr, today=today,
                    log=g13._Log(verbose=False))
    return m.backfill(PACKAGE_DIR, tmp_path, transport=tr, today=today,
                      log=g13._Log(verbose=False))


def _read(tmp_path, name):
    return json.loads((tmp_path / "backfill" / name).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Structural
# --------------------------------------------------------------------------- #
def test_runner_compiles():
    py_compile.compile(str(RUNNER), doraise=True)


def test_docs_exist():
    assert DOCS.exists()
    t = DOCS.read_text(encoding="utf-8").lower()
    for token in ("frozen", "spy", "no orders", "reconcil", "drawdown"):
        assert token in t


def test_dynamic_data_dir_is_outside_git():
    assert str(m._DEFAULT_DAILY_MARK_DIR).replace("\\", "/").startswith("D:/")
    assert "Stock_Prediction_app_push" not in str(m._DEFAULT_DAILY_MARK_DIR)
    assert "paper_trader" not in str(m._DEFAULT_DAILY_MARK_DIR)


def test_reuses_phase13g_provider_client():
    # The transport, ticker cleaning, and price rules are the SAME objects (no second,
    # incompatible provider client).
    assert m.g13 is g13
    assert m.PRICE_SOURCE == g13.PRICE_SOURCE


# --------------------------------------------------------------------------- #
# Trading calendar / completed-EOD rule
# --------------------------------------------------------------------------- #
def test_completed_eod_and_one_obs_per_date():
    spy = g13._normalize_bars(_SPY)
    dates = m.trading_dates(spy, "2026-05-22", date(2026, 5, 28))
    assert dates == ["2026-05-22", "2026-05-26", "2026-05-27"]  # 05-28 excluded (incomplete)
    assert len(dates) == len(set(dates))                        # one observation per date


def test_dates_before_signal_excluded():
    spy = g13._normalize_bars(_bars(("2026-05-18", 480.0), ("2026-05-22", 500.0),
                                    ("2026-05-26", 505.0)))
    dates = m.trading_dates(spy, "2026-05-22", date(2026, 5, 27))
    assert dates == ["2026-05-22", "2026-05-26"]                # pre-signal 05-18 excluded


# --------------------------------------------------------------------------- #
# Full offline backfill: frozen holdings, isolation, math, coverage
# --------------------------------------------------------------------------- #
def test_backfill_reconciled_and_written(tmp_path):
    manifest = _run(tmp_path)
    assert manifest["decision"] == m.DEC_RECONCILED
    assert manifest["analytics_published"] is True
    assert manifest["backfill_start_date"] == "2026-05-22"
    assert manifest["backfill_end_date"] == "2026-05-27"
    assert manifest["n_observations"] == 3
    for f in ("backfill_manifest.json", "top25_daily_history.json", "top50_daily_history.json",
              "spy_daily_history.json", "paper_performance_summary.json"):
        assert (tmp_path / "backfill" / f).is_file()
    for f in ("top25_daily_history.csv", "top50_daily_history.csv", "spy_daily_history.csv",
              "position_daily_marks.csv"):
        assert (tmp_path / "backfill" / f).is_file()


def test_frozen_holdings_never_change_across_dates(tmp_path):
    _run(tmp_path)
    hist = _read(tmp_path, "top50_daily_history.json")["rows"]
    pos = _read(tmp_path, "top25_daily_history.json")["rows"]
    # book membership + totals are constant on every date (no rerank, no rebalance)
    assert {r["total_count"] for r in hist} == {50}
    assert {r["total_count"] for r in pos} == {25}
    # the position ledger holds the SAME 25/50 tickers on every date, in frozen order
    marks = list(csv_rows(tmp_path / "backfill" / "position_daily_marks.csv"))
    top25 = [r for r in marks if r["book_size"] == "25"]
    by_date = {}
    for r in top25:
        by_date.setdefault(r["mark_date"], []).append(r["ticker"])
    orders = list(by_date.values())
    assert all(o == orders[0] for o in orders)                 # identical holdings each date
    assert len(orders[0]) == 25


def test_top25_top50_isolated(tmp_path):
    _run(tmp_path)
    b25 = _read(tmp_path, "top25_daily_history.json")["rows"][-1]
    b50 = _read(tmp_path, "top50_daily_history.json")["rows"][-1]
    assert b25["book_id"].endswith("top25") and b50["book_id"].endswith("top50")
    assert b25["book_id"] != b50["book_id"]
    assert b25["total_count"] == 25 and b50["total_count"] == 50


def test_cumulative_daily_excess_and_spy_math(tmp_path):
    _run(tmp_path)
    rows = _read(tmp_path, "top25_daily_history.json")["rows"]
    d = {r["mark_date"]: r for r in rows}
    # cumulative book return = (factor-1)*100
    assert d["2026-05-22"]["average_return_pct"] == pytest.approx(0.0, abs=1e-6)
    assert d["2026-05-26"]["average_return_pct"] == pytest.approx(10.0, abs=1e-6)
    assert d["2026-05-27"]["average_return_pct"] == pytest.approx(5.0, abs=1e-6)
    assert d["2026-05-27"]["cumulative_return_pct"] == pytest.approx(5.0, abs=1e-6)
    # daily change: +10 then -5
    assert d["2026-05-26"]["daily_change_pct_points"] == pytest.approx(10.0, abs=1e-6)
    assert d["2026-05-27"]["daily_change_pct_points"] == pytest.approx(-5.0, abs=1e-6)
    # SPY cumulative 0 / +1 / +2 (505/500, 510/500)
    assert d["2026-05-26"]["spy_return_pct"] == pytest.approx(1.0, abs=1e-6)
    assert d["2026-05-27"]["spy_return_pct"] == pytest.approx(2.0, abs=1e-6)
    # excess = book - spy
    assert d["2026-05-26"]["excess_return_vs_spy_pct_points"] == pytest.approx(9.0, abs=1e-6)
    assert d["2026-05-27"]["excess_return_vs_spy_pct_points"] == pytest.approx(3.0, abs=1e-6)
    # daily excess change: +9 then -6
    assert d["2026-05-27"]["daily_excess_change_pct_points"] == pytest.approx(-6.0, abs=1e-6)


def test_spy_reference_price_math(tmp_path):
    _run(tmp_path)
    spy = _read(tmp_path, "spy_daily_history.json")
    assert spy["rows"][0]["reference_date"] == "2026-05-22"
    assert spy["rows"][0]["reference_price"] == pytest.approx(500.0)
    assert spy["rows"][-1]["return_since_signal_pct"] == pytest.approx(2.0, abs=1e-6)


def test_contributor_concentration_math(tmp_path):
    _run(tmp_path)
    b25 = _read(tmp_path, "top25_daily_history.json")["rows"][-1]
    b50 = _read(tmp_path, "top50_daily_history.json")["rows"][-1]
    # every name equal -> top5 share = 5/N * 100
    assert b25["contributor_concentration_top5_pct"] == pytest.approx(20.0, abs=1e-6)  # 5/25
    assert b50["contributor_concentration_top5_pct"] == pytest.approx(10.0, abs=1e-6)  # 5/50


def test_coverage_thresholds(tmp_path):
    # empty the series for 2 of the 25 Top-25 names -> 23/25 = 92% -> PARTIAL
    positions, _ = g13.load_source_universe(PACKAGE_DIR, None)
    top25 = [p["ticker"] for p in positions if p["in_top25"]][:2]
    tr = _make_transport(_universe_series(drop=tuple(top25)))
    m.backfill(PACKAGE_DIR, tmp_path, transport=tr, today="2026-05-28",
               log=g13._Log(verbose=False))
    last = _read(tmp_path, "top25_daily_history.json")["rows"][-1]
    assert last["covered_count"] == 23 and last["total_count"] == 25
    assert last["coverage_status"] == g13.COV_PARTIAL


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #
def test_latest_date_reconciliation_matches_live_13g(tmp_path):
    manifest = _run(tmp_path)
    recon = manifest["reconciliation"]
    assert recon["status"] == m.DEC_RECONCILED
    assert recon["reference_available"] is True
    assert recon["reference_mark_date"] == "2026-05-27"
    assert all(c["within_tight"] for c in recon["checks"] if c["comparable"])


def test_no_reference_is_warning_but_publishes(tmp_path):
    # no live 13-G mark on disk -> WARNING, analytics still published
    manifest = _run(tmp_path, write_ref=False)
    assert manifest["decision"] == m.DEC_WARNING
    assert manifest["analytics_published"] is True
    assert (tmp_path / "backfill" / "paper_performance_summary.json").is_file()


def test_rejected_reconciliation_blocks_analytics(tmp_path):
    # Seed a live 13-G mark whose Top-25 average is wildly different at the same date.
    tr = _make_transport(_universe_series())
    g13.refresh(PACKAGE_DIR, None, tmp_path, transport=tr, today="2026-05-28",
                log=g13._Log(verbose=False))
    books_path = tmp_path / "latest" / "book_summaries.json"
    books = json.loads(books_path.read_text(encoding="utf-8"))
    books["top25"]["average_return_pct"] = 99.0   # far beyond the loose tolerance
    books_path.write_text(json.dumps(books), encoding="utf-8")

    manifest = m.backfill(PACKAGE_DIR, tmp_path, transport=tr, today="2026-05-28",
                          log=g13._Log(verbose=False))
    assert manifest["decision"] == m.DEC_REJECTED
    assert manifest["analytics_published"] is False
    # manifest is written, but NO analytics/history artifacts are published
    assert (tmp_path / "backfill" / "backfill_manifest.json").is_file()
    assert not (tmp_path / "backfill" / "paper_performance_summary.json").exists()
    assert not (tmp_path / "backfill" / "top25_daily_history.json").exists()


# --------------------------------------------------------------------------- #
# Blocked provider states (reuse the 13-G taxonomy)
# --------------------------------------------------------------------------- #
def test_blocked_invalid_key(tmp_path):
    tr = _make_transport(_universe_series(),
                         raise_for={g13._clean_symbol("SPY"): _FakeEodhdError("invalid_key")})
    manifest = m.backfill(PACKAGE_DIR, tmp_path, transport=tr, today="2026-05-28",
                          log=g13._Log(verbose=False))
    assert manifest["decision"] == m.DEC_BLOCKED_KEY
    assert manifest["blocked"] is True
    assert manifest["analytics_published"] is False
    assert not (tmp_path / "backfill" / "top25_daily_history.json").exists()


def test_blocked_missing_key_env(tmp_path, monkeypatch):
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    manifest = m.backfill(PACKAGE_DIR, tmp_path, today="2026-05-28",  # no transport -> live path
                          log=g13._Log(verbose=False))
    assert manifest["decision"] == m.DEC_BLOCKED_KEY


# --------------------------------------------------------------------------- #
# Part B analytics as pure functions (deterministic hand-built strips)
# --------------------------------------------------------------------------- #
def _row(d, cum, spy, daily=None, daily_excess=None, cov=m.COV_FULL, conc=10.0):
    return {
        "mark_date": d, "book_id": "composite_sn__2026-05-22__top25", "book_size": 25,
        "average_return_pct": cum, "spy_return_pct": spy,
        "excess_return_vs_spy_pct_points": (None if cum is None or spy is None else cum - spy),
        "daily_change_pct_points": daily, "daily_excess_change_pct_points": daily_excess,
        "coverage_status": cov, "contributor_concentration_top5_pct": conc,
        "top5_signed_pnl_share_pct": None,
    }


def test_analytics_drawdown_and_daily_stats():
    rows = [_row("2026-05-22", 0.0, 0.0, None, None),
            _row("2026-05-26", 10.0, 1.0, 10.0, 9.0),
            _row("2026-05-27", 5.0, 2.0, -5.0, -6.0)]
    a = m.analytics_for_book(rows)
    assert a["n_observations"] == 3
    assert a["current_cumulative_return_pct"] == 5.0
    assert a["spy_cumulative_return_pct"] == 2.0
    assert a["current_excess_return_pct_points"] == 3.0
    # equity 1.00 -> 1.10 -> 1.05 : max drawdown = 1.05/1.10 - 1 = -4.5455%
    assert a["max_drawdown_pct"] == pytest.approx(-4.5455, abs=1e-3)
    assert a["max_drawdown_peak_date"] == "2026-05-26"
    assert a["max_drawdown_trough_date"] == "2026-05-27"
    assert a["best_daily_change_pct_points"] == 10.0
    assert a["worst_daily_change_pct_points"] == -5.0
    assert a["daily_change_volatility_pct_points"] == pytest.approx(7.5, abs=1e-6)
    assert a["pct_positive_daily_changes"] == pytest.approx(50.0, abs=1e-6)
    assert a["pct_days_outperforming_spy"] == pytest.approx(50.0, abs=1e-6)
    # only 2 daily-excess observations -> IR not reported (short forward period)
    assert a["information_ratio_valid"] is False
    assert a["information_ratio"] is None


def test_analytics_coverage_date_counts():
    rows = [_row("2026-05-22", 0.0, 0.0, None, None, cov=m.COV_FULL),
            _row("2026-05-26", 1.0, 1.0, 1.0, 0.0, cov=m.COV_PARTIAL),
            _row("2026-05-27", 2.0, 2.0, 1.0, 0.0, cov=m.COV_INSUFFICIENT)]
    a = m.analytics_for_book(rows)
    assert a["n_coverage_warning_dates"] == 1
    assert a["n_insufficient_coverage_dates"] == 1


def test_stability_insufficient_history():
    a = m.analytics_for_book([_row("2026-05-22", 0.0, 0.0)])
    st = m.stability_comparison(a, a)
    assert st["assessment"] == "INSUFFICIENT_DAILY_HISTORY"
    assert st["promotes_to_live"] is False


def test_stability_winner_is_lower_vol_and_shallower_drawdown():
    a25 = {"n_daily_change_observations": 6, "daily_change_volatility_pct_points": 1.0,
           "max_drawdown_pct": -2.0}
    a50 = {"n_daily_change_observations": 6, "daily_change_volatility_pct_points": 3.0,
           "max_drawdown_pct": -8.0}
    st = m.stability_comparison(a25, a50)
    assert st["assessment"] == "TOP25_MORE_STABLE"
    assert st["promotes_to_live"] is False


# --------------------------------------------------------------------------- #
# Secret discipline + no staged dynamic data
# --------------------------------------------------------------------------- #
def test_api_key_never_written_to_artifacts(tmp_path, monkeypatch):
    sentinel = "SENTINEL_KEY_SHOULD_NOT_APPEAR_13I_987654"
    monkeypatch.setenv("EODHD_API_KEY", sentinel)
    _run(tmp_path)
    for path in (tmp_path / "backfill").rglob("*"):
        if path.is_file():
            assert sentinel not in path.read_text(encoding="utf-8", errors="ignore")


def test_backfill_output_is_under_dynamic_root(tmp_path):
    # The backfill artifacts are written under <mark-dir>/backfill, never into the repo.
    _run(tmp_path)
    out = tmp_path / "backfill"
    assert out.is_dir()
    assert "Stock_Prediction_app_push" not in str(m.BACKFILL_SUBDIR)


# --------------------------------------------------------------------------- #
# tiny CSV reader (stdlib only)
# --------------------------------------------------------------------------- #
def csv_rows(path):
    import csv
    with open(path, encoding="utf-8", newline="") as fh:
        yield from csv.DictReader(fh)
