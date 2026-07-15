"""Targeted tests for Phase 13-G Part B - Daily Alpha EOD Mark Refresh.

Every test injects a FAKE transport (a deterministic in-memory EODHD payload), so the
suite makes ZERO network calls and needs NO EODHD_API_KEY. It verifies: the
completed-EOD rule (an incomplete current-day bar can never become a completed mark),
same-mark-date de-duplication, mark/PnL/SPY math, coverage thresholds, atomic output,
that the API key is never printed, and that dynamic D: data is not staged into git.
"""
import json
import os
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

STEM = "phase13g_daily_alpha_mark_refresh"
RUNNER = REPO / "research" / f"run_{STEM}.py"
DOCS = REPO / "docs" / f"{STEM}_v1.md"
PACKAGE_DIR = (REPO / "research" / "output"
               / "phase13a_current_champion_alpha_paper_test_package")

from research import run_phase13g_daily_alpha_mark_refresh as m  # noqa: E402


# --------------------------------------------------------------------------- #
# Fake EODHD transport: a per-ticker series of daily bars.
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


def _full_universe_series(pct=0.10, entry_default=100.0, entry_date="2026-05-22",
                          latest_date="2026-07-14"):
    """Every real Top-50 ticker + SPY gets an entry-date bar and a completed latest bar.

    The entry-date bar is set to each name's FROZEN 13-A entry price (or a default when
    the package has none), and the latest bar is entry*(1+pct), so every book name has a
    clean +pct% paper return whether its entry comes from the frozen book or the series.
    SPY is fixed to +1%."""
    positions, _ = m.load_source_universe(PACKAGE_DIR, None)
    series = {}
    for p in positions:
        e = p["frozen_entry_price"] or entry_default
        sym = m._clean_symbol(p["ticker"])
        series[sym] = _bars((entry_date, e), (latest_date, e * (1.0 + pct)))
    series[m._clean_symbol("SPY")] = _bars((entry_date, 500.0), (latest_date, 505.0))
    return series


def test_runner_compiles():
    py_compile.compile(str(RUNNER), doraise=True)


def test_docs_exist():
    assert DOCS.exists()
    t = DOCS.read_text(encoding="utf-8").lower()
    assert "eod" in t and "spy" in t and "no orders" in t


# --- pure helpers ---------------------------------------------------------- #
def test_completed_bar_rule_excludes_current_day():
    from datetime import date
    bars = m._normalize_bars(_bars(("2026-07-13", 10.0), ("2026-07-14", 11.0),
                                   ("2026-07-15", 12.0)))
    completed = m._completed_bars(bars, date(2026, 7, 15))
    # the 2026-07-15 (== reference today) bar must NOT be treated as completed
    assert completed[-1][0] == "2026-07-14"
    assert all(d < "2026-07-15" for d, _ in completed)


def test_price_at_or_before():
    bars = m._normalize_bars(_bars(("2026-05-20", 9.0), ("2026-05-22", 10.0),
                                   ("2026-05-26", 11.0)))
    assert m._price_at_or_before(bars, "2026-05-22") == ("2026-05-22", 10.0)
    assert m._price_at_or_before(bars, "2026-05-23") == ("2026-05-22", 10.0)
    assert m._price_at_or_before(bars, "2026-05-19") is None


def test_clean_symbol_dot_to_dash():
    assert m._clean_symbol("BRK.B") == "BRK-B"
    assert m._clean_symbol("aapl") == "AAPL"


def test_coverage_thresholds():
    assert m._coverage_status(10, 10) == m.COV_FULL
    assert m._coverage_status(9, 10) == m.COV_PARTIAL          # 90%
    assert m._coverage_status(8, 10) == m.COV_INSUFFICIENT     # 80%
    assert m._coverage_status(0, 0) == m.COV_INSUFFICIENT


def test_mark_ticker_math():
    from datetime import date
    pos = {"ticker": "AAA", "alpha_name": "composite_sn", "signal_date": "2026-05-22",
           "source_rank": 1, "in_top25": True, "in_top50": True, "in_shadow_top50": False,
           "frozen_entry_price": 100.0, "frozen_entry_reference_date": "2026-05-22"}
    bars = m._normalize_bars(_bars(("2026-05-22", 100.0), ("2026-07-14", 110.0),
                                   ("2026-07-15", 999.0)))
    mk = m.mark_ticker(pos, bars, "OK", date(2026, 7, 15))
    assert mk["latest_completed_eod_date"] == "2026-07-14"
    assert mk["latest_adjusted_close"] == 110.0
    assert mk["paper_return_pct"] == 10.0     # 110/100 - 1
    assert mk["price_status"] == "MARKED"


# --- full offline refresh -------------------------------------------------- #
def test_refresh_full_coverage_new_mark(tmp_path):
    tr = _make_transport(_full_universe_series())
    manifest = m.refresh(PACKAGE_DIR, None, tmp_path, transport=tr,
                         today="2026-07-15", log=m._Log(verbose=False))
    assert manifest["refresh_result"] == m.RES_OK
    assert manifest["mark_date"] == "2026-07-14"
    assert manifest["new_mark_date"] is True
    # artifacts written to latest/ and history/<mark_date>/
    assert (tmp_path / "latest" / "refresh_manifest.json").is_file()
    assert (tmp_path / "latest" / "daily_alpha_marks.csv").is_file()
    assert (tmp_path / "history" / "2026-07-14" / "refresh_manifest.json").is_file()
    books = json.loads((tmp_path / "latest" / "book_summaries.json").read_text(encoding="utf-8"))
    assert books["top25"]["book_id"] == "composite_sn__2026-05-22__top25"
    assert books["top50"]["book_id"] == "composite_sn__2026-05-22__top50"
    assert books["top25"]["coverage_status"] == m.COV_FULL
    # book vs SPY: names +10%, SPY 505/500-1 = +1% -> excess ~ +9pp
    assert books["top25"]["average_return_pct"] == pytest.approx(10.0, abs=1e-6)
    assert books["top25"]["benchmark_return_pct"] == pytest.approx(1.0, abs=1e-6)
    assert books["top25"]["excess_return_vs_spy_pct_points"] == pytest.approx(9.0, abs=1e-6)


def test_same_mark_date_is_deduped(tmp_path):
    tr = _make_transport(_full_universe_series())
    first = m.refresh(PACKAGE_DIR, None, tmp_path, transport=tr, today="2026-07-15",
                      log=m._Log(verbose=False))
    assert first["refresh_result"] == m.RES_OK
    # a repeat run on the SAME completed mark date must not add a new observation
    second = m.refresh(PACKAGE_DIR, None, tmp_path, transport=tr, today="2026-07-15",
                       log=m._Log(verbose=False))
    assert second["refresh_result"] == m.RES_NO_NEW
    assert second["new_mark_date"] is False
    # exactly one history dir
    hist = list((tmp_path / "history").iterdir())
    assert [h.name for h in hist] == ["2026-07-14"]


def test_new_price_date_advances_and_change_computed(tmp_path):
    m.refresh(PACKAGE_DIR, None, tmp_path,
              transport=_make_transport(_full_universe_series(pct=0.10,
                                                              latest_date="2026-07-14")),
              today="2026-07-15", log=m._Log(verbose=False))
    # a later completed session at a higher price -> new mark date + positive change
    second = m.refresh(PACKAGE_DIR, None, tmp_path,
                       transport=_make_transport(_full_universe_series(pct=0.21,
                                                                       latest_date="2026-07-16")),
                       today="2026-07-17", log=m._Log(verbose=False))
    assert second["refresh_result"] == m.RES_OK
    assert second["mark_date"] == "2026-07-16"
    assert second["previous_mark_date"] == "2026-07-14"
    b25 = second["book_summaries_preview"][0]
    # avg 21% now vs 10% previously -> +11pp change
    assert b25["change_since_previous_mark_pct_points"] == pytest.approx(11.0, abs=1e-3)
    assert {h.name for h in (tmp_path / "history").iterdir()} == {"2026-07-14", "2026-07-16"}


def test_partial_and_insufficient_coverage(tmp_path):
    # drop entry/latest for enough Top-25 names to fall below thresholds
    series = _full_universe_series()
    positions, _ = m.load_source_universe(PACKAGE_DIR, None)
    top25 = [p["ticker"] for p in positions if p["in_top25"]]
    # return an EMPTY series for 2 of 25 -> no completed latest bar -> not covered ->
    # 23/25 = 92% -> PARTIAL_COVERAGE.
    for tk in top25[:2]:
        series[m._clean_symbol(tk)] = []
    manifest = m.refresh(PACKAGE_DIR, None, tmp_path, transport=_make_transport(series),
                         today="2026-07-15", log=m._Log(verbose=False))
    assert manifest["refresh_result"] in (m.RES_PARTIAL, m.RES_INSUFFICIENT)
    b25 = manifest["book_summaries_preview"][0]
    assert b25["coverage_status"] in (m.COV_PARTIAL, m.COV_INSUFFICIENT)


def test_blocked_invalid_key(tmp_path):
    tr = _make_transport(_full_universe_series(),
                         raise_for={m._clean_symbol("SPY"): _FakeEodhdError("invalid_key")})
    manifest = m.refresh(PACKAGE_DIR, None, tmp_path, transport=tr, today="2026-07-15",
                         log=m._Log(verbose=False))
    assert manifest["refresh_result"] == m.RES_BLOCKED_KEY
    assert manifest["blocked"] is True
    # a blocked run does not create a latest/ artifact set
    assert not (tmp_path / "latest" / "refresh_manifest.json").is_file()


def test_blocked_entitlement_and_rate(tmp_path):
    tr402 = _make_transport(_full_universe_series(),
                            raise_for={m._clean_symbol("SPY"): _FakeEodhdError("plan_blocked")})
    assert m.refresh(PACKAGE_DIR, None, tmp_path, transport=tr402, today="2026-07-15",
                     log=m._Log(verbose=False))["refresh_result"] == m.RES_BLOCKED_ENTITLEMENT
    tr429 = _make_transport(_full_universe_series(),
                            raise_for={m._clean_symbol("SPY"): _FakeEodhdError("rate_limited")})
    assert m.refresh(PACKAGE_DIR, None, tmp_path, transport=tr429, today="2026-07-15",
                     log=m._Log(verbose=False))["refresh_result"] == m.RES_BLOCKED_RATE


def test_manifest_safety_flags(tmp_path):
    tr = _make_transport(_full_universe_series())
    manifest = m.refresh(PACKAGE_DIR, None, tmp_path, transport=tr, today="2026-07-15",
                         log=m._Log(verbose=False))
    assert manifest["creates_orders"] is False
    assert manifest["creates_automation"] is False
    assert manifest["live_trading"] is False
    assert manifest["wrote_to_paper_trader"] is False
    assert manifest["api_key_printed"] is False
    assert manifest["order_action_all"] == "NO_ORDER"


def test_benchmark_summary_math(tmp_path):
    tr = _make_transport(_full_universe_series())
    manifest = m.refresh(PACKAGE_DIR, None, tmp_path, transport=tr, today="2026-07-15",
                         log=m._Log(verbose=False))
    bench = manifest["benchmark_summary_preview"]
    assert bench["ticker"] == "SPY"
    assert bench["latest_completed_eod_date"] == "2026-07-14"
    assert bench["return_since_signal_pct"] == pytest.approx(1.0, abs=1e-6)


def test_api_key_never_written_to_artifacts(tmp_path, monkeypatch):
    # Offline (fake transport): even with a sentinel key in the environment, the runner
    # never reads it (the key lives only inside the reused client), so it can appear in
    # NONE of the written artifacts.
    sentinel = "SENTINEL_KEY_SHOULD_NOT_APPEAR_1234567"
    monkeypatch.setenv("EODHD_API_KEY", sentinel)
    tr = _make_transport(_full_universe_series())
    m.refresh(PACKAGE_DIR, None, tmp_path, transport=tr, today="2026-07-15",
              log=m._Log(verbose=False))
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert sentinel not in path.read_text(encoding="utf-8", errors="ignore")


def test_dynamic_data_dir_is_outside_git():
    # The default dynamic-data root is on D:, outside both git working trees.
    assert str(m._DEFAULT_DAILY_MARK_DIR).replace("\\", "/").startswith("D:/")
    assert "Stock_Prediction_app_push" not in str(m._DEFAULT_DAILY_MARK_DIR)
    assert "paper_trader" not in str(m._DEFAULT_DAILY_MARK_DIR)
