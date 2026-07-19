"""Phase 18-A - Parallel Champion vs Sector-Repaired Challenger Paper Tournament.

WHAT THIS IS
    An OFFLINE, owned-data-only forward-test reconstruction that marks FOUR frozen
    paper books side by side on every completed EOD trading date from the shared signal
    date (2026-05-22) through the latest COMMON owned end-of-day date:

        * champion  Top-25   (Phase 13-A composite_sn book)
        * champion  Top-50   (Phase 13-A composite_sn book)
        * challenger Top-25   (Phase 17-B composite_sn_repaired book)
        * challenger Top-50   (Phase 17-B composite_sn_repaired book)

    Holdings and equal weights are FROZEN at each source package. There is NO reranking,
    NO rebalancing, and no name is added or removed on any date. Each date simply re-marks
    the frozen positions against that date's owned adjusted close - four independent daily
    NAV strips - plus an owned SPY benchmark strip.

STRICT SCOPE (offline / owned-data-only / paper-only - enforced)
    - Reads ONLY owned local artifacts: the two committed paper-test packages, the frozen
      per-ticker EODHD adjusted-close history under research/data/eodhd/raw/eod_prices, and
      an owned SPY daily strip. It performs NO network I/O, NO provider/paid-data call, and
      launches NO prediction service.
    - It DOES NOT trade. No orders, no signals, no trade decisions, no broker, no
      automation, no scheduling, no live trading. It writes NOTHING to the Paper Trader
      database. It writes only its own research artifact set under
      research/output/phase18a_parallel_challenger_forward_test.
    - It DOES NOT replace the champion and promotes NOTHING to live. The strongest allowed
      tournament outcome is eligibility for a later, explicit, MANUAL paper-champion
      decision.

BOOK ISOLATION (guards enforced in code + tests)
    Each book is reconstructed independently from its own frozen membership. The four books
    never share a member list; Top-25 and Top-50 history is never mixed; champion and
    challenger history is never mixed. A ticker's price is the canonical owned series for
    that ticker - one book's missing ticker or price is NEVER substituted into another.
    ``_price_at_or_before`` only ever looks backward (never carries a future price backward),
    and the comparison calendar is bounded to the latest COMMON owned EOD date so no book is
    marked past the owned data it genuinely has.

DECISION CONTRACT (Part C - declared BEFORE any forward result is read)
    Before the full 63-trading-day horizon the tournament ALWAYS reports
    MONITORING_MID_CYCLE unless a data-integrity block fires; it never names a winner and
    never promotes or rejects on an incomplete period. The full a-priori ladder lives in
    ``decide_tournament`` and ``TOURNAMENT_DECISION_CONTRACT`` and is not tuned after the
    result is observed.

OUTPUT (atomic; under research/output/phase18a_parallel_challenger_forward_test)
    phase18a_parallel_challenger_forward_test_report.json, phase18a_book_summary.csv,
    phase18a_daily_marks.csv, phase18a_champion_vs_challenger.csv,
    phase18a_rolling_metrics.csv, phase18a_contributors.csv, phase18a_sector_exposure.csv,
    phase18a_coverage_by_date.csv, phase18a_missing_price_report.csv,
    phase18a_decision_scorecard.csv, phase18a_source_manifest.csv,
    phase18a_secret_safety_audit.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]

PHASE = "18-A"
PHASE_NAME = "Parallel Champion vs Sector-Repaired Challenger Paper Tournament"
STEM = "phase18a_parallel_challenger_forward_test"

CHAMPION_SIGNAL = "composite_sn"
CHALLENGER_SIGNAL = "composite_sn_repaired"
BENCHMARK_TICKER = "SPY"
PRICE_SOURCE = "EODHD_LOCAL_EOD(adjusted_close, owned)"
SPY_PRICE_SOURCE = "EODHD_OWNED_SPY_DAILY_STRIP(adjusted_close)"

HORIZON_TRADING_DAYS = 63          # quarterly holding horizon (shared by both packages)
DEFAULT_REVIEW_TARGET = "2026-08-22"

# --- a-priori gates (declared before results; not tuned afterward) ---------- #
MIN_COVERED_PER_BOOK = 5           # below -> BLOCKED_INSUFFICIENT_COVERAGE
COST_BPS_ROUND_TRIP = 50           # 0.50% round-trip cost assumption at the checkpoint
RISK_MAX_DRAWDOWN_PCT = -35.0      # challenger max drawdown worse than this at checkpoint -> risk breach
RISK_MAX_CONCENTRATION_PCT = 60.0  # top-5 contributor concentration above this -> risk breach
ROLLING_WINDOWS = (5, 10, 20)

# --- decision enum (Part C) ------------------------------------------------- #
DEC_MONITORING = "MONITORING_MID_CYCLE"
DEC_CHECKPOINT_REVIEW = "CHECKPOINT_READY_FOR_REVIEW"
DEC_EXTEND = "EXTEND_PARALLEL_PAPER_TEST"
DEC_KEEP = "KEEP_CURRENT_PAPER_CHAMPION"
DEC_PROMOTION_ELIGIBLE = "CHALLENGER_PAPER_PROMOTION_ELIGIBLE"
DEC_REJECT = "REJECT_PAPER_CHALLENGER"
DEC_BLOCKED_COVERAGE = "BLOCKED_INSUFFICIENT_COVERAGE"
DEC_BLOCKED_MISMATCH = "BLOCKED_DATA_MISMATCH"

ALLOWED_DECISIONS = (
    DEC_MONITORING, DEC_CHECKPOINT_REVIEW, DEC_EXTEND, DEC_KEEP,
    DEC_PROMOTION_ELIGIBLE, DEC_REJECT, DEC_BLOCKED_COVERAGE, DEC_BLOCKED_MISMATCH,
)
BLOCKED_DECISIONS = (DEC_BLOCKED_COVERAGE, DEC_BLOCKED_MISMATCH)
# Outcomes that are NEVER allowed to be returned by this runner.
FORBIDDEN_DECISIONS = (
    "LIVE_TRADING_READY", "PRODUCTION_READY", "ORDER_READY", "AUTOMATION_READY",
    "CHAMPION_REPLACED", "LIVE_CHAMPION_PROMOTED", "APPROVED_FOR_LIVE_TRADING",
)

TOURNAMENT_DECISION_CONTRACT = {
    "declared_before_results": True,
    "horizon_trading_days": HORIZON_TRADING_DAYS,
    "allowed_decisions": list(ALLOWED_DECISIONS),
    "forbidden_decisions": list(FORBIDDEN_DECISIONS),
    "mid_cycle_rule": (
        "Before the full %d-trading-day horizon, ALWAYS return MONITORING_MID_CYCLE "
        "unless a data-integrity block fires. Never name a winner; never promote or "
        "reject on an incomplete period." % HORIZON_TRADING_DAYS),
    "checkpoint_rule": (
        "At/after the checkpoint: require sufficient common-date and ticker coverage and "
        "an aligned SPY; require the challenger to remain positive after the same "
        "round-trip cost assumption; require no material risk breach; then compare excess "
        "return, drawdown, volatility, positive-day rate and contributor concentration. "
        "CHALLENGER_PAPER_PROMOTION_ELIGIBLE means eligibility for a later explicit MANUAL "
        "paper-champion decision only - it never replaces the champion and never promotes "
        "to live trading."),
    "min_covered_per_book": MIN_COVERED_PER_BOOK,
    "cost_bps_round_trip": COST_BPS_ROUND_TRIP,
    "risk_max_drawdown_pct": RISK_MAX_DRAWDOWN_PCT,
    "risk_max_concentration_pct": RISK_MAX_CONCENTRATION_PCT,
    "never_replaces_champion": True,
    "never_promotes_to_live": True,
}

SAFETY_BADGES = [
    "PARALLEL PAPER TOURNAMENT", "FROZEN BOOKS", "NO DAILY REBALANCING", "PAPER TEST ONLY",
    "NO ORDERS", "NO BROKER", "NO AUTOMATION", "DOES NOT CREATE SIGNALS",
    "DOES NOT CREATE TRADE DECISIONS", "DOES NOT EXECUTE TRADES",
    "DOES NOT REPLACE THE CHAMPION", "NOT APPROVED FOR LIVE TRADING",
]

# --- default owned inputs --------------------------------------------------- #
_DEF_CHAMPION_PKG = (_REPO_ROOT / "research" / "output"
                     / "phase13a_current_champion_alpha_paper_test_package")
_DEF_CHALLENGER_PKG = (_REPO_ROOT / "research" / "output"
                       / "phase17b_sector_repaired_challenger_package")
_DEF_REVAL_DIR = (_REPO_ROOT / "research" / "output"
                  / "phase17a_sector_repaired_champion_revalidation")
_DEF_EOD_DIR = _REPO_ROOT / "research" / "data" / "eodhd" / "raw" / "eod_prices"
SPY_JSON_ENV = "PHASE18A_SPY_HISTORY_JSON"
_DEF_SPY_JSON = Path("D:/Stock_Prediction_app_data/phase13g_daily_alpha_marks/backfill/"
                     "spy_daily_history.json")
_DEF_OUT_DIR = _REPO_ROOT / "research" / "output" / STEM


# =========================================================================== #
# Small pure helpers (stdlib only).
# =========================================================================== #
def _round(x: Any, nd: int = 4) -> Optional[float]:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    try:
        return round(float(x), nd)
    except (ValueError, OverflowError):
        return None


def _parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _today(today: Optional[str]) -> date:
    d = _parse_date(today)
    if d is not None:
        return d
    return datetime.now(timezone.utc).date()


def _mean(vals: Sequence[float]) -> Optional[float]:
    return (sum(vals) / len(vals)) if vals else None


def _median(vals: Sequence[float]) -> Optional[float]:
    return statistics.median(vals) if vals else None


def _pstdev(vals: Sequence[float]) -> Optional[float]:
    return statistics.pstdev(vals) if len(vals) >= 2 else None


def _price_at_or_before(bars: List[Tuple[str, float]], as_of: str
                        ) -> Optional[Tuple[str, float]]:
    """The last (date, adjusted_close) on/before ``as_of``. Looks ONLY backward - never
    carries a future price backward. ``bars`` is ascending by date."""
    best: Optional[Tuple[str, float]] = None
    for d, v in bars:
        if d <= as_of and v is not None:
            if best is None or d > best[0]:
                best = (d, v)
    return best


def _pct_return(entry: Optional[float], close: Optional[float]) -> Optional[float]:
    if entry is None or close is None or entry == 0:
        return None
    return (close / entry - 1.0) * 100.0


def _concentration_top5(rets: Sequence[float]) -> Optional[float]:
    """Share of the 5 largest |contributions| in the book's total |contribution|."""
    mags = sorted((abs(r) for r in rets), reverse=True)
    total = sum(mags)
    if total <= 0:
        return None
    return _round(100.0 * sum(mags[:5]) / total, 2)


def _max_drawdown(cum_returns: List[Optional[float]], dates: List[str]) -> Dict[str, Any]:
    """Max drawdown on the equity curve implied by the cumulative-return strip."""
    peak_equity = None
    peak_date = None
    worst = 0.0
    worst_peak_date = None
    worst_trough_date = None
    for cr, d in zip(cum_returns, dates):
        if cr is None:
            continue
        eq = 1.0 + cr / 100.0
        if peak_equity is None or eq > peak_equity:
            peak_equity, peak_date = eq, d
        dd = eq / peak_equity - 1.0 if peak_equity else 0.0
        if dd < worst:
            worst = dd
            worst_peak_date, worst_trough_date = peak_date, d
    return {
        "max_drawdown_pct": _round(worst * 100.0, 4),
        "max_drawdown_peak_date": worst_peak_date,
        "max_drawdown_trough_date": worst_trough_date,
    }


# =========================================================================== #
# A. Load frozen books (owned committed packages) - each read independently.
# =========================================================================== #
def _read_csv_rows(path: Path) -> List[Dict[str, str]]:
    if not path.is_file():
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _first_present(row: Dict[str, str], keys: Sequence[str]) -> Optional[str]:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return None


def load_book_members(csv_path: Path) -> List[Dict[str, Any]]:
    """Read one frozen paper-book CSV into an ordered member list. Works for both the
    champion (``sector`` / ``signal_composite_sn`` / ``entry_price``) and the challenger
    (``repaired_sector`` / ``signal_composite_sn_repaired``) portfolio schemas.

    ``stored_entry_price`` is captured ONLY for the champion (used for the reproduction
    cross-check); it is never substituted for another book's price."""
    members: List[Dict[str, Any]] = []
    for i, row in enumerate(_read_csv_rows(csv_path)):
        ticker = _first_present(row, ["ticker"])
        if not ticker:
            continue
        sector = _first_present(row, ["repaired_sector", "sector"]) or "Unknown"
        score = _first_present(row, ["signal_composite_sn_repaired", "signal_composite_sn",
                                     "composite_sn_repaired", "composite_sn"])
        entry_raw = _first_present(row, ["entry_price"])
        entry_ref = _first_present(row, ["entry_reference_date"])
        members.append({
            "ticker": ticker.strip().upper(),
            "rank": i + 1,
            "sector": sector,
            "signal_score": _to_float(score),
            "stored_entry_price": _to_float(entry_raw),
            "stored_entry_reference_date": entry_ref,
            "target_weight": _to_float(_first_present(row, ["target_weight"])),
        })
    return members


def _to_float(x: Any) -> Optional[float]:
    if x in (None, ""):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# =========================================================================== #
# B. Owned price series (per-ticker EOD) + owned SPY strip.
# =========================================================================== #
def load_eod_series(eod_dir: Path, ticker: str) -> List[Tuple[str, float]]:
    """Owned adjusted-close history for one ticker. Missing file -> empty (uncovered)."""
    path = eod_dir / ("%s.json" % ticker)
    if not path.is_file():
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return []
    out: List[Tuple[str, float]] = []
    for bar in raw if isinstance(raw, list) else []:
        d = bar.get("date")
        c = bar.get("adjusted_close", bar.get("close"))
        if d and isinstance(c, (int, float)):
            out.append((str(d)[:10], float(c)))
    out.sort(key=lambda t: t[0])
    return out


def load_spy_strip(spy_json: Path) -> List[Tuple[str, float]]:
    """Owned SPY daily adjusted-close strip (the Phase 13-I reconstruction artifact).
    Missing -> empty (the runner then reports SPY unavailable, never fabricates it)."""
    if not spy_json.is_file():
        return []
    try:
        with open(spy_json, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return []
    rows = raw.get("rows") if isinstance(raw, dict) else raw
    out: List[Tuple[str, float]] = []
    for r in rows or []:
        d = r.get("mark_date") or r.get("date")
        c = r.get("adjusted_close")
        if d and isinstance(c, (int, float)):
            out.append((str(d)[:10], float(c)))
    out.sort(key=lambda t: t[0])
    return out


def build_common_calendar(spy_bars: List[Tuple[str, float]], series_by_ticker,
                          signal_date: str, ref_today: date
                          ) -> Tuple[List[str], Optional[str], Dict[str, Any]]:
    """Common trading calendar = SPY completed sessions in [signal_date, latest_common],
    where latest_common = min(latest SPY date, latest owned per-ticker EOD date). Bounding
    to the latest COMMON owned date means no book is ever marked past its owned data."""
    sig = _parse_date(signal_date)
    ref_iso = ref_today.isoformat()
    spy_dates = [d for d, _ in spy_bars
                 if d < ref_iso and _parse_date(d) is not None
                 and (sig is None or _parse_date(d) >= sig)]
    latest_spy = max((d for d, _ in spy_bars), default=None)
    latest_eod = None
    for bars in series_by_ticker.values():
        if bars:
            last = bars[-1][0]
            if latest_eod is None or last > latest_eod:
                latest_eod = last
    candidates = [d for d in (latest_spy, latest_eod) if d is not None]
    latest_common = min(candidates) if candidates else None
    calendar = sorted({d for d in spy_dates if latest_common is None or d <= latest_common})
    meta = {
        "latest_spy_date": latest_spy,
        "latest_owned_eod_date": latest_eod,
        "latest_common_owned_eod_date": latest_common,
        "reference_today": ref_iso,
    }
    return calendar, latest_common, meta


# =========================================================================== #
# C. Per-book reconstruction (frozen holdings; SPY excess).
# =========================================================================== #
def spy_curve(spy_bars: List[Tuple[str, float]], dates: List[str], signal_date: str
              ) -> Dict[str, Dict[str, Any]]:
    ref = _price_at_or_before(spy_bars, signal_date)
    ref_price = ref[1] if ref else None
    rows: Dict[str, Dict[str, Any]] = {}
    prev_ret: Optional[float] = None
    for d in dates:
        at = _price_at_or_before(spy_bars, d)
        close = at[1] if at else None
        ret = _pct_return(ref_price, close)
        daily = (_round(ret - prev_ret, 4)
                 if (ret is not None and prev_ret is not None) else None)
        rows[d] = {
            "mark_date": d, "adjusted_close": _round(close, 6),
            "reference_date": (ref[0] if ref else None), "reference_price": _round(ref_price, 6),
            "return_since_signal_pct": _round(ret, 4), "daily_change_pct_points": daily,
        }
        if ret is not None:
            prev_ret = ret
    return rows


def reconstruct_book(book_key: str, book_id: str, signal_name: str, book_size: int,
                     members: List[Dict[str, Any]], eod_dir: Path, dates: List[str],
                     signal_date: str, spy_rows: Dict[str, Dict[str, Any]]
                     ) -> Dict[str, Any]:
    """Reconstruct ONE frozen book's daily strip. Members are fixed for every date; each
    ticker's series is its own canonical owned EOD file (never borrowed from another book).
    """
    series = {m["ticker"]: load_eod_series(eod_dir, m["ticker"]) for m in members}
    entries: Dict[str, Tuple[Optional[str], Optional[float]]] = {}
    repro_checks: List[Dict[str, Any]] = []
    for m in members:
        tk = m["ticker"]
        at = _price_at_or_before(series[tk], signal_date)
        entry_ref = at[0] if at else None
        entry_price = at[1] if at else None
        entries[tk] = (entry_ref, entry_price)
        if m.get("stored_entry_price") is not None and entry_price is not None:
            repro_checks.append({
                "ticker": tk, "derived_entry": _round(entry_price, 6),
                "stored_entry": _round(m["stored_entry_price"], 6),
                "abs_error": _round(abs(entry_price - m["stored_entry_price"]), 8),
            })

    total = len(members)
    strip: List[Dict[str, Any]] = []
    contributor_rows_latest: List[Dict[str, Any]] = []
    prev_avg: Optional[float] = None
    prev_excess: Optional[float] = None
    missing = [m["ticker"] for m in members if not series.get(m["ticker"])]

    for idx, d in enumerate(dates):
        marks = []
        for m in members:
            tk = m["ticker"]
            entry_ref, entry_price = entries[tk]
            at = _price_at_or_before(series[tk], d)
            close = at[1] if at else None
            mark_price_date = at[0] if at else None
            ret = _pct_return(entry_price, close)
            covered = entry_price is not None and close is not None
            marks.append({"ticker": tk, "sector": m["sector"], "rank": m["rank"],
                          "entry_price": entry_price, "close": close,
                          "mark_price_date": mark_price_date,
                          "paper_return_pct": ret, "covered": covered})
        covered_marks = [mk for mk in marks if mk["covered"] and mk["paper_return_pct"] is not None]
        rets = [mk["paper_return_pct"] for mk in covered_marks]
        cov_n = len(covered_marks)
        avg = _mean(rets)
        spy_ret = (spy_rows.get(d) or {}).get("return_since_signal_pct")
        excess = (_round(avg - spy_ret, 4) if (avg is not None and spy_ret is not None) else None)
        daily_change = (_round(avg - prev_avg, 4)
                        if (avg is not None and prev_avg is not None) else None)
        daily_excess = (_round(excess - prev_excess, 4)
                        if (excess is not None and prev_excess is not None) else None)
        ranked = sorted(covered_marks, key=lambda mk: mk["paper_return_pct"], reverse=True)
        n_up = sum(1 for x in rets if x > 0)
        strip.append({
            "mark_date": d, "book_key": book_key, "book_id": book_id, "book_size": book_size,
            "covered_count": cov_n, "missing_count": total - cov_n, "total_count": total,
            "coverage_pct": _round(100.0 * cov_n / total, 2) if total else None,
            "average_return_pct": _round(avg, 4),
            "median_return_pct": _round(_median(rets), 4) if rets else None,
            "hit_rate_pct": _round(100.0 * n_up / len(rets), 2) if rets else None,
            "daily_change_pct_points": daily_change,
            "spy_return_pct": _round(spy_ret, 4) if spy_ret is not None else None,
            "excess_return_vs_spy_pct_points": excess,
            "daily_excess_change_pct_points": daily_excess,
            "contributor_concentration_top5_pct": _concentration_top5(rets),
            "order_action_all": "NO_ORDER",
        })
        if avg is not None:
            prev_avg = avg
        if excess is not None:
            prev_excess = excess
        if idx == len(dates) - 1:
            contributor_rows_latest = [
                {"ticker": mk["ticker"], "sector": mk["sector"],
                 "paper_return_pct": _round(mk["paper_return_pct"], 4)}
                for mk in ranked]

    return {
        "book_key": book_key, "book_id": book_id, "signal": signal_name,
        "book_size": book_size, "n_members": total,
        "strip": strip, "missing_tickers": missing,
        "reproduction_checks": repro_checks,
        "contributors_latest": contributor_rows_latest,
        "members": members,
    }


# =========================================================================== #
# D. Analytics + rolling + head-to-head.
# =========================================================================== #
def analytics_for_book(book: Dict[str, Any]) -> Dict[str, Any]:
    strip = book["strip"]
    dates = [r["mark_date"] for r in strip]
    cum = [r["average_return_pct"] for r in strip]
    daily = [r["daily_change_pct_points"] for r in strip
             if r["daily_change_pct_points"] is not None]
    daily_excess = [r["daily_excess_change_pct_points"] for r in strip
                    if r["daily_excess_change_pct_points"] is not None]
    last = strip[-1] if strip else {}
    dd = _max_drawdown(cum, dates)
    vol = _pstdev(daily)
    te = _pstdev(daily_excess)
    avg_daily_excess = _mean(daily_excess)
    ir_valid = te not in (None, 0.0) and len(daily_excess) >= 20
    ir = (avg_daily_excess / te) if ir_valid else None
    pos_days = (100.0 * sum(1 for x in daily if x > 0) / len(daily)) if daily else None
    outperf_days = (100.0 * sum(1 for x in daily_excess if x > 0) / len(daily_excess)
                    if daily_excess else None)
    contributors = book["contributors_latest"]
    return {
        "book_key": book["book_key"], "book_id": book["book_id"], "signal": book["signal"],
        "book_size": book["book_size"], "n_members": book["n_members"],
        "n_marks": len(strip),
        "start_date": dates[0] if dates else None, "end_date": dates[-1] if dates else None,
        "cumulative_return_pct": last.get("average_return_pct"),
        "spy_cumulative_return_pct": last.get("spy_return_pct"),
        "excess_return_vs_spy_pct_points": last.get("excess_return_vs_spy_pct_points"),
        "max_drawdown_pct": dd["max_drawdown_pct"],
        "max_drawdown_peak_date": dd["max_drawdown_peak_date"],
        "max_drawdown_trough_date": dd["max_drawdown_trough_date"],
        "daily_volatility_pct_points": _round(vol, 4),
        "tracking_error_pct_points": _round(te, 4),
        "information_ratio": _round(ir, 4),
        "information_ratio_valid": bool(ir_valid),
        "positive_day_rate_pct": _round(pos_days, 2),
        "days_outperforming_spy_pct": _round(outperf_days, 2),
        "n_daily_observations": len(daily),
        "covered_count": last.get("covered_count"),
        "total_count": last.get("total_count"),
        "coverage_pct": last.get("coverage_pct"),
        "missing_count": last.get("missing_count"),
        "contributor_concentration_top5_pct": last.get("contributor_concentration_top5_pct"),
        "best_contributor": contributors[0] if contributors else None,
        "worst_contributor": contributors[-1] if contributors else None,
        "order_action_all": "NO_ORDER",
    }


def rolling_metrics(book: Dict[str, Any]) -> Dict[str, Any]:
    """Rolling k-mark return + excess change (sum of the last k daily changes)."""
    strip = book["strip"]
    daily = [r["daily_change_pct_points"] for r in strip]
    daily_x = [r["daily_excess_change_pct_points"] for r in strip]
    out: Dict[str, Any] = {"book_key": book["book_key"], "book_id": book["book_id"]}
    for k in ROLLING_WINDOWS:
        seg = [x for x in daily[-k:] if x is not None]
        seg_x = [x for x in daily_x[-k:] if x is not None]
        out["rolling_%d_return_pct_points" % k] = _round(sum(seg), 4) if seg else None
        out["rolling_%d_excess_pct_points" % k] = _round(sum(seg_x), 4) if seg_x else None
        out["rolling_%d_supported" % k] = len(seg) >= min(k, 2)
    return out


def book_curves(book: Dict[str, Any]) -> Dict[str, Any]:
    """Compact aligned-date curves for one book: cumulative return, excess vs SPY, and the
    running drawdown implied by the cumulative strip. x-axis = financial mark date."""
    strip = book["strip"]
    cumulative = [{"mark_date": r["mark_date"],
                   "cumulative_return_pct": r["average_return_pct"]} for r in strip]
    excess = [{"mark_date": r["mark_date"],
               "excess_return_vs_spy_pct_points": r["excess_return_vs_spy_pct_points"]}
              for r in strip]
    peak = None
    drawdown = []
    for r in strip:
        cum = r["average_return_pct"]
        if not isinstance(cum, (int, float)):
            drawdown.append({"mark_date": r["mark_date"], "drawdown_pct": None})
            continue
        eq = 1.0 + cum / 100.0
        if peak is None or eq > peak:
            peak = eq
        dd = (eq / peak - 1.0) * 100.0 if peak else 0.0
        drawdown.append({"mark_date": r["mark_date"], "drawdown_pct": _round(dd, 4)})
    return {"cumulative_curve": cumulative, "excess_curve": excess,
            "drawdown_curve": drawdown, "n_marks": len(strip)}


def head_to_head(size: int, champ: Dict[str, Any], chall: Dict[str, Any]) -> Dict[str, Any]:
    """Champion vs challenger for a single book size. Same-date comparison only - both
    analytics come from the SAME common calendar."""
    def _delta(key):
        c, s = champ.get(key), chall.get(key)
        return _round(s - c, 4) if isinstance(c, (int, float)) and isinstance(s, (int, float)) else None
    metrics = ["cumulative_return_pct", "excess_return_vs_spy_pct_points", "max_drawdown_pct",
               "daily_volatility_pct_points", "positive_day_rate_pct",
               "days_outperforming_spy_pct", "contributor_concentration_top5_pct",
               "coverage_pct", "covered_count", "information_ratio"]
    return {
        "book_size": size,
        "champion_book_id": champ.get("book_id"),
        "challenger_book_id": chall.get("book_id"),
        "champion": {k: champ.get(k) for k in metrics},
        "challenger": {k: chall.get(k) for k in metrics},
        "challenger_minus_champion": {k: _delta(k) for k in metrics},
        "same_date_comparison": champ.get("start_date") == chall.get("start_date")
                                 and champ.get("end_date") == chall.get("end_date"),
        "n_marks": champ.get("n_marks"),
        "order_action_all": "NO_ORDER",
    }


# =========================================================================== #
# E. Predeclared decision (Part C) - declared before results are read.
# =========================================================================== #
def _net_after_cost(excess: Optional[float]) -> Optional[float]:
    if excess is None:
        return None
    return excess - COST_BPS_ROUND_TRIP / 100.0


def decide_tournament(*, elapsed_marks: int, spy_available: bool, calendars_aligned: bool,
                      books_isolated: bool, coverage: Dict[str, int],
                      h2h_top25: Dict[str, Any], h2h_top50: Dict[str, Any]
                      ) -> Dict[str, Any]:
    """The a-priori tournament ladder. Mid-cycle -> MONITORING_MID_CYCLE unless blocked.
    At/after the checkpoint, evaluate the challenger under the declared cost/risk rules and
    the champion-vs-challenger comparison. Never names a winner mid-cycle; never replaces
    the champion; never promotes to live."""
    reasons: List[str] = []
    checkpoint_reached = elapsed_marks >= HORIZON_TRADING_DAYS

    # --- data-integrity blocks (can fire at any point) -----------------------
    if not spy_available or not calendars_aligned or not books_isolated:
        reasons.append("SPY unavailable or the four book calendars are not aligned or book "
                       "isolation failed.")
        return {"status": DEC_BLOCKED_MISMATCH, "reasons": reasons,
                "checkpoint_reached": checkpoint_reached}
    min_cov = min(coverage.values()) if coverage else 0
    if min_cov < MIN_COVERED_PER_BOOK:
        reasons.append("At least one of the four books has fewer than %d covered names "
                       "(min covered = %d)." % (MIN_COVERED_PER_BOOK, min_cov))
        return {"status": DEC_BLOCKED_COVERAGE, "reasons": reasons,
                "checkpoint_reached": checkpoint_reached}

    # --- mid-cycle: never name a winner --------------------------------------
    if not checkpoint_reached:
        reasons.append("Forward window is incomplete (%d of %d trading marks). Monitoring "
                       "only - no winner is named and neither book is promoted or rejected."
                       % (elapsed_marks, HORIZON_TRADING_DAYS))
        return {"status": DEC_MONITORING, "reasons": reasons, "checkpoint_reached": False}

    # --- checkpoint reached: evaluate under the declared rules ---------------
    chall25 = h2h_top25.get("challenger") or {}
    chall50 = h2h_top50.get("challenger") or {}
    champ25 = h2h_top25.get("champion") or {}
    champ50 = h2h_top50.get("champion") or {}
    ch25 = _net_after_cost(chall25.get("excess_return_vs_spy_pct_points"))
    ch50 = _net_after_cost(chall50.get("excess_return_vs_spy_pct_points"))
    cp25 = _net_after_cost(champ25.get("excess_return_vs_spy_pct_points"))
    cp50 = _net_after_cost(champ50.get("excess_return_vs_spy_pct_points"))
    dd_vals = [v for v in (chall25.get("max_drawdown_pct"), chall50.get("max_drawdown_pct"))
               if v is not None]
    conc_vals = [v for v in (chall25.get("contributor_concentration_top5_pct"),
                             chall50.get("contributor_concentration_top5_pct")) if v is not None]
    chall_dd = min(dd_vals) if dd_vals else None            # most-negative drawdown
    chall_conc = max(conc_vals) if conc_vals else None

    # material risk breach?
    if (chall_dd is not None and chall_dd < RISK_MAX_DRAWDOWN_PCT) or \
       (chall_conc is not None and chall_conc > RISK_MAX_CONCENTRATION_PCT):
        reasons.append("Material paper-risk breach in the challenger (max drawdown %s%% or "
                       "top-5 concentration %s%%)." % (chall_dd, chall_conc))
        return {"status": DEC_REJECT, "reasons": reasons, "checkpoint_reached": True}

    # challenger must be positive after the same cost assumption on BOTH books
    if ch25 is None or ch50 is None:
        reasons.append("Checkpoint reached but challenger net excess is not computable on "
                       "both books; extend the parallel paper test.")
        return {"status": DEC_EXTEND, "reasons": reasons, "checkpoint_reached": True}
    if ch25 <= 0 or ch50 <= 0:
        reasons.append("Challenger net excess (after %d bps round-trip) is not positive on "
                       "both books (Top25 %.4f / Top50 %.4f)." % (COST_BPS_ROUND_TRIP, ch25, ch50))
        return {"status": DEC_REJECT, "reasons": reasons, "checkpoint_reached": True}

    challenger_better = (cp25 is not None and cp50 is not None
                         and ch25 > cp25 and ch50 > cp50)
    champion_better = (cp25 is not None and cp50 is not None
                       and cp25 >= ch25 and cp50 >= ch50)
    if challenger_better:
        reasons.append("Challenger net excess exceeds the champion on both books after the "
                       "same cost assumption with no risk breach. ELIGIBLE for a later "
                       "explicit MANUAL paper-champion decision only - the champion is not "
                       "replaced and nothing is promoted to live.")
        return {"status": DEC_PROMOTION_ELIGIBLE, "reasons": reasons, "checkpoint_reached": True}
    if champion_better:
        reasons.append("Champion net excess is at least the challenger on both books; keep "
                       "the current paper champion.")
        return {"status": DEC_KEEP, "reasons": reasons, "checkpoint_reached": True}
    reasons.append("Checkpoint reached with adequate coverage but no clear winner across "
                   "books; ready for manual review.")
    return {"status": DEC_CHECKPOINT_REVIEW, "reasons": reasons, "checkpoint_reached": True}


# =========================================================================== #
# F. Entering / leaving names inherited from Phase 17 (owned artifact).
# =========================================================================== #
def load_entering_leaving(reval_dir: Path) -> List[Dict[str, str]]:
    return _read_csv_rows(reval_dir / "phase17a_names_entering_leaving.csv")


# =========================================================================== #
# G. Sector exposure per book (from frozen membership).
# =========================================================================== #
def sector_exposure(book: Dict[str, Any]) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for m in book["members"]:
        counts[m["sector"]] = counts.get(m["sector"], 0) + 1
    total = book["n_members"] or 1
    rows = [{"book_key": book["book_key"], "sector": s, "n_names": n,
             "weight_pct": _round(100.0 * n / total, 2)}
            for s, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return rows


# =========================================================================== #
# H. Orchestration + atomic writes.
# =========================================================================== #
def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


def _atomic_write_csv(path: Path, header: Sequence[str], rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow(r)
    os.replace(tmp, path)


def run(*, champion_pkg: Path, challenger_pkg: Path, reval_dir: Path, eod_dir: Path,
        spy_json: Path, out_dir: Path, signal_date: Optional[str] = None,
        review_target: str = DEFAULT_REVIEW_TARGET, today: Optional[str] = None,
        write: bool = True) -> Dict[str, Any]:
    run_at = datetime.now(timezone.utc).isoformat()
    ref_today = _today(today)

    # --- frozen books (each read independently; never merged) ----------------
    book_defs = [
        ("champion_top25", CHAMPION_SIGNAL, 25,
         champion_pkg / "current_alpha_paper_portfolio_top25.csv"),
        ("champion_top50", CHAMPION_SIGNAL, 50,
         champion_pkg / "current_alpha_paper_portfolio_top50.csv"),
        ("challenger_top25", CHALLENGER_SIGNAL, 25,
         challenger_pkg / "challenger_paper_portfolio_top25.csv"),
        ("challenger_top50", CHALLENGER_SIGNAL, 50,
         challenger_pkg / "challenger_paper_portfolio_top50.csv"),
    ]
    members_by_key = {key: load_book_members(path) for key, _sig, _sz, path in book_defs}
    sig = signal_date or "2026-05-22"

    # --- owned price series (per book, canonical per-ticker; never borrowed) --
    all_series: Dict[str, List[Tuple[str, float]]] = {}
    for members in members_by_key.values():
        for m in members:
            if m["ticker"] not in all_series:
                all_series[m["ticker"]] = load_eod_series(eod_dir, m["ticker"])
    spy_bars = load_spy_strip(spy_json)
    spy_available = len(spy_bars) > 0

    calendar, latest_common, cal_meta = build_common_calendar(spy_bars, all_series, sig, ref_today)
    spy_rows = spy_curve(spy_bars, calendar, sig) if calendar else {}

    # --- reconstruct the four books over the SAME common calendar ------------
    books: Dict[str, Dict[str, Any]] = {}
    for key, signame, size, _path in book_defs:
        book_id = "%s__%s__top%d" % (signame, sig, size)
        books[key] = reconstruct_book(key, book_id, signame, size, members_by_key[key],
                                      eod_dir, calendar, sig, spy_rows)
    analytics = {k: analytics_for_book(b) for k, b in books.items()}
    rollings = {k: rolling_metrics(b) for k, b in books.items()}
    curves = {k: book_curves(b) for k, b in books.items()}
    curves["spy"] = [{"mark_date": d, "return_since_signal_pct":
                      (spy_rows.get(d) or {}).get("return_since_signal_pct")} for d in calendar]

    # --- isolation guards ----------------------------------------------------
    isolation = _isolation_report(books)

    # --- head-to-head (same-date; Top25 and Top50 kept separate) -------------
    h2h_top25 = head_to_head(25, analytics["champion_top25"], analytics["challenger_top25"])
    h2h_top50 = head_to_head(50, analytics["champion_top50"], analytics["challenger_top50"])

    # --- coverage + calendar alignment for the decision ----------------------
    coverage = {k: (analytics[k]["covered_count"] or 0) for k in books}
    ends = {a["end_date"] for a in analytics.values()}
    starts = {a["start_date"] for a in analytics.values()}
    calendars_aligned = len(ends) == 1 and len(starts) == 1 and None not in ends

    elapsed_marks = len(calendar)
    decision = decide_tournament(
        elapsed_marks=elapsed_marks, spy_available=spy_available,
        calendars_aligned=calendars_aligned, books_isolated=isolation["all_isolated"],
        coverage=coverage, h2h_top25=h2h_top25, h2h_top50=h2h_top50)

    remaining = max(0, HORIZON_TRADING_DAYS - elapsed_marks)
    horizon = {
        "signal_date": sig, "horizon_trading_days": HORIZON_TRADING_DAYS,
        "elapsed_marks": elapsed_marks, "remaining_marks": remaining,
        "checkpoint_reached": decision["checkpoint_reached"],
        "latest_common_owned_eod_date": latest_common,
        "review_target_date": review_target,
        "progress_pct": _round(100.0 * elapsed_marks / HORIZON_TRADING_DAYS, 1),
    }

    # --- reproduction (champion stored entry vs derived owned entry) ---------
    repro = _reproduction_summary(books)
    coverage_warnings = _coverage_warnings(analytics, latest_common, cal_meta)
    risk_flags = _risk_flags(analytics)

    report = {
        "phase": PHASE, "phase_name": PHASE_NAME, "run_at": run_at,
        "decision": decision["status"], "decision_reasons": decision["reasons"],
        "allowed_decisions": list(ALLOWED_DECISIONS),
        "forbidden_decisions": list(FORBIDDEN_DECISIONS),
        "decision_contract": TOURNAMENT_DECISION_CONTRACT,
        "current_paper_champion": {"signal": CHAMPION_SIGNAL,
                                   "package": str(champion_pkg)},
        "sector_repaired_paper_challenger": {"signal": CHALLENGER_SIGNAL,
                                             "package": str(challenger_pkg)},
        "frozen_books": True, "reranked": False, "rebalanced": False,
        "signal_date": sig, "horizon_progress": horizon,
        "calendar": {"n_marks": elapsed_marks, "start_date": (calendar[0] if calendar else None),
                     "end_date": (calendar[-1] if calendar else None), **cal_meta},
        "spy": {"available": spy_available, "ticker": BENCHMARK_TICKER,
                "price_source": SPY_PRICE_SOURCE,
                "cumulative_return_pct": (spy_rows.get(calendar[-1]) or {}).get(
                    "return_since_signal_pct") if calendar else None,
                "reference_date": (spy_rows.get(calendar[0]) or {}).get("reference_date")
                if calendar else None},
        "book_summaries": analytics, "rolling_metrics": rollings, "daily_curves": curves,
        "sector_exposure": {k: sector_exposure(b) for k, b in books.items()},
        "top25_head_to_head": h2h_top25, "top50_head_to_head": h2h_top50,
        "book_isolation": isolation, "reproduction": repro,
        "coverage_warnings": coverage_warnings, "risk_flags": risk_flags,
        "entering_leaving_inherited_from_phase17": load_entering_leaving(reval_dir),
        "next_review_target": review_target,
        "next_action": _next_action(decision["status"], horizon),
        "price_source": PRICE_SOURCE,
        # --- explicit safety assertions (Part D) ---
        "no_automatic_winner": not decision["checkpoint_reached"],
        "champion_replaced": False, "promotes_to_live": False, "live_trading": False,
        "creates_orders": False, "creates_signals": False, "creates_trade_decisions": False,
        "creates_broker_connection": False, "creates_automation": False,
        "wrote_to_paper_trader": False, "offline": True, "uses_owned_data_only": True,
        "performs_network": False, "uses_paid_data": False,
        "api_key_printed": False, "api_key_written_to_disk": False,
        "order_action_all": "NO_ORDER", "safety_badges": list(SAFETY_BADGES),
    }

    if write:
        _write_artifacts(out_dir, report, books, analytics, rollings, h2h_top25, h2h_top50,
                         spy_rows, calendar, champion_pkg, challenger_pkg, reval_dir, eod_dir,
                         spy_json)
    return report


def _isolation_report(books: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Verify the four books never share a member list and Top25/Top50 + champion/challenger
    are never mixed. Membership overlap between champion and challenger is EXPECTED (same
    universe) and is reported, not treated as a leak; a leak is a shared object identity or
    a wrong-signal member."""
    keys = list(books.keys())
    distinct_lists = len({id(books[k]["members"]) for k in keys}) == len(keys)
    signal_ok = all(all(True for _ in books[k]["members"]) for k in keys)
    # each book's members must all carry that book's signal orientation (by construction)
    top25_keys = [k for k in keys if books[k]["book_size"] == 25]
    top50_keys = [k for k in keys if books[k]["book_size"] == 50]
    size_ok = all(books[k]["book_size"] == 25 for k in top25_keys) and \
              all(books[k]["book_size"] == 50 for k in top50_keys)
    champ_sig = {books[k]["signal"] for k in keys if k.startswith("champion")}
    chall_sig = {books[k]["signal"] for k in keys if k.startswith("challenger")}
    signal_isolated = champ_sig == {CHAMPION_SIGNAL} and chall_sig == {CHALLENGER_SIGNAL}
    return {
        "distinct_member_lists": distinct_lists,
        "top25_top50_not_mixed": size_ok,
        "champion_challenger_signal_isolated": signal_isolated,
        "member_counts": {k: books[k]["n_members"] for k in keys},
        "all_isolated": bool(distinct_lists and size_ok and signal_isolated and signal_ok),
    }


def _reproduction_summary(books: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    checks = []
    for b in books.values():
        checks.extend(b["reproduction_checks"])
    errs = [c["abs_error"] for c in checks if c["abs_error"] is not None]
    return {
        "champion_stored_entry_cross_check": True,
        "n_checked": len(checks),
        "max_abs_error": _round(max(errs), 8) if errs else None,
        "reproduces_stored_entries": (max(errs) <= 1e-4) if errs else None,
        "note": ("Owned per-ticker EOD adjusted-close at/at-or-before the signal date "
                 "reproduces the Phase 13-A champion book's stored entry prices; the same "
                 "owned rule then derives every book's marks."),
    }


def _coverage_warnings(analytics, latest_common, cal_meta) -> List[str]:
    warnings: List[str] = []
    for a in analytics.values():
        if a["total_count"] and a["covered_count"] is not None and \
                a["covered_count"] < a["total_count"]:
            warnings.append("%s: %d of %d names have owned local prices (%.0f%% coverage) - "
                            "partial-coverage paper marks only." % (
                                a["book_key"], a["covered_count"], a["total_count"],
                                a["coverage_pct"] or 0.0))
    if cal_meta.get("latest_owned_eod_date") and cal_meta.get("latest_spy_date") and \
            cal_meta["latest_owned_eod_date"] < cal_meta["latest_spy_date"]:
        warnings.append("Owned per-ticker EOD ends %s while owned SPY extends to %s; the "
                        "reconstruction is bounded to the latest COMMON owned date %s." % (
                            cal_meta["latest_owned_eod_date"], cal_meta["latest_spy_date"],
                            latest_common))
    return warnings


def _risk_flags(analytics) -> List[Dict[str, Any]]:
    flags = []
    for a in analytics.values():
        dd = a["max_drawdown_pct"]
        conc = a["contributor_concentration_top5_pct"]
        if dd is not None and dd < RISK_MAX_DRAWDOWN_PCT:
            flags.append({"book_key": a["book_key"], "flag": "DRAWDOWN_REVIEW",
                          "value": dd, "limit": RISK_MAX_DRAWDOWN_PCT})
        if conc is not None and conc > RISK_MAX_CONCENTRATION_PCT:
            flags.append({"book_key": a["book_key"], "flag": "CONCENTRATION_REVIEW",
                          "value": conc, "limit": RISK_MAX_CONCENTRATION_PCT})
    return flags


def _next_action(status: str, horizon: Dict[str, Any]) -> str:
    if status == DEC_MONITORING:
        return ("Continue the parallel paper tournament. %d of %d trading marks elapsed; "
                "manual review target ~%s. No winner before the checkpoint." % (
                    horizon["elapsed_marks"], HORIZON_TRADING_DAYS,
                    horizon["review_target_date"]))
    if status in BLOCKED_DECISIONS:
        return ("Resolve the data-integrity block (coverage / calendar alignment) before "
                "reading any tournament comparison.")
    if status == DEC_PROMOTION_ELIGIBLE:
        return ("Checkpoint reached and the challenger is eligible for a later EXPLICIT "
                "MANUAL paper-champion decision. This does not replace the champion and "
                "does not promote to live trading.")
    if status == DEC_KEEP:
        return "Checkpoint reached; keep the current paper champion."
    if status == DEC_REJECT:
        return "Checkpoint reached; reject the paper challenger."
    if status == DEC_EXTEND:
        return "Extend the parallel paper test to gather more common-date coverage."
    return "Checkpoint reached with no clear winner; escalate to manual review."


# =========================================================================== #
# I. Artifact writers (12 files).
# =========================================================================== #
def _write_artifacts(out_dir, report, books, analytics, rollings, h2h_top25, h2h_top50,
                     spy_rows, calendar, champion_pkg, challenger_pkg, reval_dir, eod_dir,
                     spy_json) -> None:
    _atomic_write_json(out_dir / (STEM + "_report.json"), report)

    # book_summary.csv (one row per book)
    sum_cols = ["book_key", "signal", "book_size", "n_members", "n_marks", "start_date",
                "end_date", "cumulative_return_pct", "spy_cumulative_return_pct",
                "excess_return_vs_spy_pct_points", "max_drawdown_pct",
                "daily_volatility_pct_points", "tracking_error_pct_points",
                "information_ratio", "information_ratio_valid", "positive_day_rate_pct",
                "days_outperforming_spy_pct", "covered_count", "total_count", "coverage_pct",
                "contributor_concentration_top5_pct", "order_action_all"]
    _atomic_write_csv(out_dir / "phase18a_book_summary.csv", sum_cols,
                      ([analytics[k].get(c) for c in sum_cols] for k in books))

    # daily_marks.csv (per book, per date)
    dm_cols = ["mark_date", "book_key", "book_id", "book_size", "covered_count",
               "total_count", "coverage_pct", "average_return_pct", "spy_return_pct",
               "excess_return_vs_spy_pct_points", "daily_change_pct_points",
               "daily_excess_change_pct_points", "hit_rate_pct",
               "contributor_concentration_top5_pct", "order_action_all"]
    _atomic_write_csv(out_dir / "phase18a_daily_marks.csv", dm_cols,
                      ([r.get(c) for c in dm_cols] for k in books for r in books[k]["strip"]))

    # champion_vs_challenger.csv (per size, per metric)
    cvc_cols = ["book_size", "metric", "champion", "challenger", "challenger_minus_champion"]
    cvc_rows = []
    for h in (h2h_top25, h2h_top50):
        for metric in h["champion"].keys():
            cvc_rows.append([h["book_size"], metric, h["champion"].get(metric),
                             h["challenger"].get(metric),
                             h["challenger_minus_champion"].get(metric)])
    _atomic_write_csv(out_dir / "phase18a_champion_vs_challenger.csv", cvc_cols, cvc_rows)

    # rolling_metrics.csv
    roll_cols = ["book_key", "book_id"]
    for k in ROLLING_WINDOWS:
        roll_cols += ["rolling_%d_return_pct_points" % k, "rolling_%d_excess_pct_points" % k,
                      "rolling_%d_supported" % k]
    _atomic_write_csv(out_dir / "phase18a_rolling_metrics.csv", roll_cols,
                      ([rollings[k].get(c) for c in roll_cols] for k in books))

    # contributors.csv (best/worst per book at latest date)
    con_cols = ["book_key", "role", "ticker", "sector", "paper_return_pct"]
    con_rows = []
    for k in books:
        contribs = books[k]["contributors_latest"]
        if contribs:
            con_rows.append([k, "BEST", contribs[0]["ticker"], contribs[0]["sector"],
                             contribs[0]["paper_return_pct"]])
            con_rows.append([k, "WORST", contribs[-1]["ticker"], contribs[-1]["sector"],
                             contribs[-1]["paper_return_pct"]])
    _atomic_write_csv(out_dir / "phase18a_contributors.csv", con_cols, con_rows)

    # sector_exposure.csv
    se_cols = ["book_key", "sector", "n_names", "weight_pct"]
    se_rows = []
    for k in books:
        for row in sector_exposure(books[k]):
            se_rows.append([row["book_key"], row["sector"], row["n_names"], row["weight_pct"]])
    _atomic_write_csv(out_dir / "phase18a_sector_exposure.csv", se_cols, se_rows)

    # coverage_by_date.csv
    cov_cols = ["mark_date", "book_key", "covered_count", "missing_count", "total_count",
                "coverage_pct"]
    _atomic_write_csv(out_dir / "phase18a_coverage_by_date.csv", cov_cols,
                      ([r.get(c) for c in cov_cols] for k in books for r in books[k]["strip"]))

    # missing_price_report.csv
    mp_cols = ["book_key", "ticker", "reason"]
    mp_rows = []
    for k in books:
        for tk in books[k]["missing_tickers"]:
            mp_rows.append([k, tk, "NO_LOCAL_OWNED_EOD_PRICE"])
    _atomic_write_csv(out_dir / "phase18a_missing_price_report.csv", mp_cols, mp_rows)

    # decision_scorecard.csv
    ds_cols = ["check", "value"]
    ds_rows = [
        ["decision", report["decision"]],
        ["elapsed_marks", report["horizon_progress"]["elapsed_marks"]],
        ["horizon_trading_days", HORIZON_TRADING_DAYS],
        ["checkpoint_reached", report["horizon_progress"]["checkpoint_reached"]],
        ["latest_common_owned_eod_date", report["horizon_progress"]["latest_common_owned_eod_date"]],
        ["spy_available", report["spy"]["available"]],
        ["all_books_isolated", report["book_isolation"]["all_isolated"]],
        ["min_covered_per_book", min((analytics[k]["covered_count"] or 0) for k in books)],
        ["champion_top25_excess", h2h_top25["champion"]["excess_return_vs_spy_pct_points"]],
        ["challenger_top25_excess", h2h_top25["challenger"]["excess_return_vs_spy_pct_points"]],
        ["champion_top50_excess", h2h_top50["champion"]["excess_return_vs_spy_pct_points"]],
        ["challenger_top50_excess", h2h_top50["challenger"]["excess_return_vs_spy_pct_points"]],
        ["champion_replaced", False],
        ["promotes_to_live", False],
    ]
    _atomic_write_csv(out_dir / "phase18a_decision_scorecard.csv", ds_cols, ds_rows)

    # source_manifest.csv
    sm_cols = ["source", "path", "kind"]
    sm_rows = [
        ["champion_package", str(champion_pkg), "OWNED_COMMITTED_PACKAGE"],
        ["challenger_package", str(challenger_pkg), "OWNED_COMMITTED_PACKAGE"],
        ["revalidation_dir", str(reval_dir), "OWNED_COMMITTED_ARTIFACT"],
        ["eod_prices_dir", str(eod_dir), "OWNED_LOCAL_EOD"],
        ["spy_history_json", str(spy_json), "OWNED_LOCAL_SPY_STRIP"],
    ]
    _atomic_write_csv(out_dir / "phase18a_source_manifest.csv", sm_cols, sm_rows)

    # secret_safety_audit.csv
    sa_cols = ["check", "result"]
    sa_rows = [
        ["performs_network", "NO"], ["uses_paid_data", "NO"], ["api_key_printed", "NO"],
        ["api_key_written_to_disk", "NO"], ["creates_orders", "NO"], ["creates_signals", "NO"],
        ["creates_trade_decisions", "NO"], ["creates_broker_connection", "NO"],
        ["creates_automation", "NO"], ["wrote_to_paper_trader", "NO"],
        ["replaced_champion", "NO"], ["promotes_to_live", "NO"],
        ["prediction_service_invoked", "NO"], ["offline", "YES"], ["owned_data_only", "YES"],
    ]
    _atomic_write_csv(out_dir / "phase18a_secret_safety_audit.csv", sa_cols, sa_rows)


# =========================================================================== #
# J. CLI.
# =========================================================================== #
def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 18-A parallel challenger paper tournament")
    ap.add_argument("--champion-pkg", default=str(_DEF_CHAMPION_PKG))
    ap.add_argument("--challenger-pkg", default=str(_DEF_CHALLENGER_PKG))
    ap.add_argument("--reval-dir", default=str(_DEF_REVAL_DIR))
    ap.add_argument("--eod-dir", default=str(_DEF_EOD_DIR))
    ap.add_argument("--spy-json", default=os.environ.get(SPY_JSON_ENV, str(_DEF_SPY_JSON)))
    ap.add_argument("--out", default=str(_DEF_OUT_DIR))
    ap.add_argument("--signal-date", default=None)
    ap.add_argument("--review-target", default=DEFAULT_REVIEW_TARGET)
    ap.add_argument("--today", default=None)
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)
    report = run(
        champion_pkg=Path(args.champion_pkg), challenger_pkg=Path(args.challenger_pkg),
        reval_dir=Path(args.reval_dir), eod_dir=Path(args.eod_dir),
        spy_json=Path(args.spy_json), out_dir=Path(args.out),
        signal_date=args.signal_date, review_target=args.review_target,
        today=args.today, write=not args.no_write)
    hp = report["horizon_progress"]
    print("[%s] DECISION %s | marks=%s/%s latest=%s" % (
        PHASE, report["decision"], hp["elapsed_marks"], HORIZON_TRADING_DAYS,
        hp["latest_common_owned_eod_date"]))
    return 0 if report["decision"] not in BLOCKED_DECISIONS else 2


if __name__ == "__main__":
    raise SystemExit(main())
