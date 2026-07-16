"""Phase 13-I - Historical Daily Mark Backfill + Paper Performance Analytics.

WHAT THIS IS
    A one-shot HISTORICAL mark-to-market reconstruction of the SAME frozen Phase 13-A
    champion paper books (composite_sn Top-25 / Top-50, signal date 2026-05-22) on
    every completed EOD trading date from entry to the latest completed session, using
    the user's OWNED EODHD adjusted-close history.

    THIS IS NOT A BACKTEST WITH CHANGING HOLDINGS. Holdings and entry prices are frozen
    at the Phase 13-A book. There is NO reranking, NO rebalancing, and NO name is added
    or removed on any historical date. Each date simply re-marks the frozen positions
    against that date's adjusted close - the paper-book equivalent of a daily NAV strip.

    IT DOES NOT TRADE. No orders, no signals, no trade decisions, no broker, no
    automation, no scheduling, no live trading, and it writes NOTHING to the Paper
    Trader database. It only fetches read-only market data and writes a dynamic
    reconstruction artifact OUTSIDE both git repositories.

REUSE (single provider client - no second, incompatible transport)
    All ticker normalization, EODHD transport, adjusted-close selection, completed-EOD
    filtering, entry-price rule, and secret handling are IMPORTED from Phase 13-G
    (``run_phase13g_daily_alpha_mark_refresh``): ``_clean_symbol``, ``_normalize_bars``,
    ``_completed_bars``, ``_price_at_or_before``, ``load_source_universe``,
    ``live_transport``, ``_fetch_one``, ``probe_entitlement``, and the atomic writers.
    EODHD_API_KEY is read only from the environment by the reused client, passed only as
    the api_token query param, and is NEVER printed and NEVER persisted.

COMPLETED-EOD RULE (identical to Phase 13-G)
    A bar dated on the reference calendar day (``--today``) is treated as potentially
    incomplete and is never used as a completed mark. The common trading calendar is the
    set of SPY completed sessions on/after the signal date.

RECONCILIATION (integrity gate)
    The backfill's reconstructed row AT the Phase 13-G latest valid mark date must match
    the live Phase 13-G book/benchmark marks within tolerance (same frozen entries + same
    price rule -> near-exact). If the reference diverges beyond tolerance, the run is
    REJECTED and NO analytics are published.

    DECISION: BACKFILL_RECONCILED | BACKFILL_RECONCILIATION_WARNING
              | BACKFILL_REJECTED_INTEGRITY_FAILURE
              | BLOCKED_EODHD_KEY | BLOCKED_EODHD_ENTITLEMENT | BLOCKED_EODHD_RATE_LIMIT
              | BLOCKED_PROVIDER_ERROR | BLOCKED_SCHEMA_ERROR

OUTPUT (atomic; OUTSIDE git; under <daily-mark-dir>/backfill)
    backfill_manifest.json, top25_daily_history.{json,csv}, top50_daily_history.{json,csv},
    spy_daily_history.{json,csv}, position_daily_marks.csv, paper_performance_summary.json
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Single reused provider client + shared rules (NO second transport).
from research import run_phase13g_daily_alpha_mark_refresh as g13  # noqa: E402

PHASE = "13-I"
PHASE_NAME = "Historical Daily Mark Backfill + Paper Performance Analytics"
STEM = "phase13i_historical_daily_mark_backfill"

ALPHA_NAME = g13.ALPHA_NAME
BENCHMARK_TICKER = g13.BENCHMARK_TICKER
EODHD_KEY_ENV = g13.EODHD_KEY_ENV
PRICE_SOURCE = g13.PRICE_SOURCE
DEFAULT_FETCH_START = g13.DEFAULT_FETCH_START

# --- inputs (owned / local) ------------------------------------------------- #
_DEFAULT_PACKAGE_DIR = (_REPO_ROOT / "research" / "output"
                        / "phase13a_current_champion_alpha_paper_test_package")

# --- output (OUTSIDE git; dynamic data) ------------------------------------- #
DAILY_MARK_DIR_ENV = g13.DAILY_MARK_DIR_ENV
_DEFAULT_DAILY_MARK_DIR = g13._DEFAULT_DAILY_MARK_DIR
BACKFILL_SUBDIR = "backfill"

# --- decision enum ---------------------------------------------------------- #
DEC_RECONCILED = "BACKFILL_RECONCILED"
DEC_WARNING = "BACKFILL_RECONCILIATION_WARNING"
DEC_REJECTED = "BACKFILL_REJECTED_INTEGRITY_FAILURE"
DEC_BLOCKED_KEY = g13.RES_BLOCKED_KEY
DEC_BLOCKED_ENTITLEMENT = g13.RES_BLOCKED_ENTITLEMENT
DEC_BLOCKED_RATE = g13.RES_BLOCKED_RATE
DEC_BLOCKED_PROVIDER = g13.RES_BLOCKED_PROVIDER
DEC_BLOCKED_SCHEMA = g13.RES_BLOCKED_SCHEMA
_BLOCKED = {DEC_BLOCKED_KEY, DEC_BLOCKED_ENTITLEMENT, DEC_BLOCKED_RATE,
            DEC_BLOCKED_PROVIDER, DEC_BLOCKED_SCHEMA}
ALLOWED_DECISIONS = [DEC_RECONCILED, DEC_WARNING, DEC_REJECTED, DEC_BLOCKED_KEY,
                     DEC_BLOCKED_ENTITLEMENT, DEC_BLOCKED_RATE, DEC_BLOCKED_PROVIDER,
                     DEC_BLOCKED_SCHEMA]

# --- reconciliation tolerances (percentage points; same-date, same inputs) -- #
RECON_TIGHT_PP = 0.05     # <= tight on every metric  -> RECONCILED
RECON_LOOSE_PP = 1.00     # <= loose (but > tight)     -> WARNING; else REJECTED

# --- analytics gates -------------------------------------------------------- #
MIN_STABILITY_OBS = 5      # fewer daily changes -> INSUFFICIENT_DAILY_HISTORY
MIN_IR_OBS = 20            # information ratio is only reported at/above this many obs

# --- coverage enums (reused) ------------------------------------------------ #
COV_FULL = g13.COV_FULL
COV_PARTIAL = g13.COV_PARTIAL
COV_INSUFFICIENT = g13.COV_INSUFFICIENT

SAFETY_BADGES = [
    "HISTORICAL PAPER MARK RECONSTRUCTION", "FROZEN HOLDINGS", "NO DAILY REBALANCING",
    "PAPER TEST ONLY", "NO ORDERS", "NO BROKER", "NO AUTOMATION",
    "DOES NOT CREATE SIGNALS", "DOES NOT CREATE TRADE DECISIONS", "DOES NOT EXECUTE TRADES",
]

_BOOK_HISTORY_CSV_HEADER = [
    "mark_date", "book_id", "book_size", "covered_count", "missing_count", "total_count",
    "coverage_pct", "coverage_status", "average_return_pct", "median_return_pct",
    "hit_rate_pct", "daily_change_pct_points", "cumulative_return_pct", "previous_mark_date",
    "spy_return_pct", "daily_spy_change_pct_points", "excess_return_vs_spy_pct_points",
    "daily_excess_change_pct_points", "contributor_concentration_top5_pct",
    "best_5", "worst_5", "order_action_all",
]
_SPY_HISTORY_CSV_HEADER = [
    "mark_date", "ticker", "reference_date", "reference_price", "adjusted_close",
    "return_since_signal_pct", "daily_change_pct_points", "price_source",
]
_POSITION_CSV_HEADER = [
    "mark_date", "ticker", "source_rank", "book_id", "book_size", "entry_reference_date",
    "entry_price", "adjusted_close", "paper_return_pct", "daily_return_pct", "covered",
    "price_source", "order_action",
]


# --------------------------------------------------------------------------- #
# Small helpers (delegating math to the reused module where possible).
# --------------------------------------------------------------------------- #
def _round(x, nd: int = 4) -> Optional[float]:
    return g13._round(x, nd)


def _book_id(signal_date: str, book_size: int) -> str:
    return "%s__%s__top%d" % (ALPHA_NAME, signal_date, book_size)


def _pct_return(entry: Optional[float], close: Optional[float]) -> Optional[float]:
    if entry is None or close is None or entry == 0:
        return None
    return (close / entry - 1.0) * 100.0


def _mean(vals: Sequence[float]) -> Optional[float]:
    return (sum(vals) / len(vals)) if vals else None


def _resolve_backfill_dir(mark_dir: Path, backfill_dir: Optional[Path]) -> Path:
    return Path(backfill_dir) if backfill_dir is not None else (mark_dir / BACKFILL_SUBDIR)


# --------------------------------------------------------------------------- #
# A. Live (or injected) acquisition of the full owned history.
# --------------------------------------------------------------------------- #
def acquire_series(transport: g13.Transport, universe: Sequence[str], start: str,
                   ref_today: date, log: g13._Log
                   ) -> Tuple[Dict[str, List[Tuple[str, float]]], Dict[str, str], Optional[str]]:
    """Fetch the full adjusted-close history for every ticker exactly once (reusing the
    Phase 13-G per-ticker fetch + the bounded entitlement probe). Returns
    (series_by_ticker, acquisition_status, blocked_enum_or_None)."""
    candidates = [t for t in universe if t != BENCHMARK_TICKER][:3]
    block = g13.probe_entitlement(transport, candidates, start, ref_today, log)
    if block is not None:
        return {}, {}, block
    series: Dict[str, List[Tuple[str, float]]] = {}
    acq: Dict[str, str] = {}
    for tk in universe:
        try:
            bars, status = g13._fetch_one(transport, tk, start)
        except g13._AcqError as exc:
            log.step("acquire", "BLOCKED", "%s on %s" % (exc.result_enum, tk))
            return {}, {}, exc.result_enum
        series[tk] = bars
        acq[tk] = status
    log.step("acquire", "DONE", "history fetched", tickers=len(universe),
             ok=sum(1 for v in acq.values() if v == "OK"))
    return series, acq, None


def trading_dates(spy_bars: List[Tuple[str, float]], signal_date: str, ref_today: date
                  ) -> List[str]:
    """The common trading calendar = SPY completed sessions on/after the signal date.
    Excludes weekends, holidays (absent from the SPY series), and the incomplete
    current-day bar (via the reused completed-EOD rule). De-duplicated + ascending."""
    completed = g13._completed_bars(spy_bars, ref_today)
    sig = g13._parse_date(signal_date)
    out: List[str] = []
    seen = set()
    for d, _v in completed:
        pd = g13._parse_date(d)
        if pd is None or (sig is not None and pd < sig):
            continue
        if d not in seen:
            seen.add(d)
            out.append(d)
    out.sort()
    return out


# --------------------------------------------------------------------------- #
# B. Frozen entry + per-date mark (SAME entry-price rule as Phase 13-G).
# --------------------------------------------------------------------------- #
def frozen_entry(pos: Dict[str, Any], bars: List[Tuple[str, float]], signal_date: str
                 ) -> Tuple[Optional[str], Optional[float]]:
    """Frozen entry: the Phase 13-A book entry price when present; otherwise the
    point-in-time adjusted close at/at-or-before the signal date (identical to the
    Phase 13-G ``mark_ticker`` fallback). Computed ONCE and held fixed across all dates."""
    entry_price = pos.get("frozen_entry_price")
    entry_ref = pos.get("frozen_entry_reference_date")
    if entry_price is None:
        at = g13._price_at_or_before(bars, signal_date)
        if at is not None:
            entry_ref, entry_price = at[0], at[1]
    return entry_ref, entry_price


def position_mark(pos: Dict[str, Any], bars: List[Tuple[str, float]], entry_ref: Optional[str],
                  entry_price: Optional[float], as_of: str, prev_as_of: Optional[str],
                  book_size: int, signal_date: str) -> Dict[str, Any]:
    """Mark ONE frozen position at ``as_of`` using the last adjusted close on/before that
    date (carry-forward on idiosyncratic non-trading days; never look-ahead)."""
    at = g13._price_at_or_before(bars, as_of)
    close = at[1] if at else None
    ret = _pct_return(entry_price, close)
    daily = None
    if prev_as_of is not None:
        prev_at = g13._price_at_or_before(bars, prev_as_of)
        prev_close = prev_at[1] if prev_at else None
        if close is not None and prev_close not in (None, 0):
            daily = (close / prev_close - 1.0) * 100.0
    covered = entry_price is not None and close is not None
    return {
        "mark_date": as_of,
        "ticker": pos["ticker"],
        "source_rank": pos.get("source_rank"),
        "book_id": _book_id(signal_date, book_size),
        "book_size": book_size,
        "entry_reference_date": entry_ref,
        "entry_price": _round(entry_price, 6),
        "adjusted_close": _round(close, 6),
        "paper_return_pct": _round(ret, 4),
        "daily_return_pct": _round(daily, 4),
        "covered": covered,
        "price_source": PRICE_SOURCE,
        "order_action": "NO_ORDER",
    }


# --------------------------------------------------------------------------- #
# C. Per-date book summary + SPY curve.
# --------------------------------------------------------------------------- #
def _concentration_top5(rets: Sequence[float]) -> Optional[float]:
    """Equal-weight gross-PnL concentration: share of the 5 largest |contributions| in
    the book's total |contribution|. Bounded [0, 100]; None when the book is empty."""
    mags = sorted((abs(r) for r in rets), reverse=True)
    total = sum(mags)
    if total <= 0:
        return None
    return _round(100.0 * sum(mags[:5]) / total, 2)


def _top5_signed_pnl_share(rets: Sequence[float]) -> Optional[float]:
    """Signed share of net book PnL from the 5 largest-|contribution| names. May exceed
    100 (or flip) when the net is near zero; reported with a validity flag in analytics."""
    ordered = sorted(rets, key=lambda x: abs(x), reverse=True)
    net = sum(rets)
    if abs(net) < 1e-9:
        return None
    return _round(100.0 * sum(ordered[:5]) / net, 2)


def spy_curve(spy_bars: List[Tuple[str, float]], dates: List[str], signal_date: str
              ) -> Tuple[Dict[str, Dict[str, Any]], Optional[str], Optional[float]]:
    """SPY cumulative return per date vs a reference at/at-or-before the signal date
    (same point-in-time rule as the frozen book entries). Returns
    (rows_by_date, reference_date, reference_price)."""
    ref = g13._price_at_or_before(spy_bars, signal_date)
    ref_date = ref[0] if ref else None
    ref_price = ref[1] if ref else None
    rows: Dict[str, Dict[str, Any]] = {}
    prev_ret: Optional[float] = None
    for d in dates:
        at = g13._price_at_or_before(spy_bars, d)
        close = at[1] if at else None
        ret = _pct_return(ref_price, close)
        daily = (_round(ret - prev_ret, 4) if (ret is not None and prev_ret is not None) else None)
        rows[d] = {
            "mark_date": d, "ticker": BENCHMARK_TICKER, "reference_date": ref_date,
            "reference_price": _round(ref_price, 6), "adjusted_close": _round(close, 6),
            "return_since_signal_pct": _round(ret, 4), "daily_change_pct_points": daily,
            "price_source": PRICE_SOURCE,
        }
        if ret is not None:
            prev_ret = ret
    return rows, ref_date, _round(ref_price, 6)


def book_history(positions: List[Dict[str, Any]], series: Dict[str, List[Tuple[str, float]]],
                 dates: List[str], book_size: int, signal_date: str,
                 spy_rows: Dict[str, Dict[str, Any]]
                 ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Reconstruct the per-date book summary series + the flat per-position mark rows for
    ONE book. Holdings are frozen: ``members`` is fixed for every date."""
    members = [p for p in positions if p.get("in_top%d" % book_size)]
    entries = {p["ticker"]: frozen_entry(p, series.get(p["ticker"], []), signal_date)
               for p in members}
    total = len(members)
    book_rows: List[Dict[str, Any]] = []
    position_rows: List[Dict[str, Any]] = []
    prev_avg: Optional[float] = None
    prev_date: Optional[str] = None
    prev_excess: Optional[float] = None
    for d in dates:
        prev_as_of = prev_date
        marks = []
        for p in members:
            entry_ref, entry_price = entries[p["ticker"]]
            mk = position_mark(p, series.get(p["ticker"], []), entry_ref, entry_price,
                               d, prev_as_of, book_size, signal_date)
            marks.append(mk)
            position_rows.append(mk)
        covered = [mk for mk in marks if mk["covered"] and mk["paper_return_pct"] is not None]
        rets = [mk["paper_return_pct"] for mk in covered]
        cov_n = len(covered)
        avg = _mean(rets)
        ranked = sorted(covered, key=lambda mk: mk["paper_return_pct"], reverse=True)

        def _slim(items):
            return [{"ticker": mk["ticker"], "paper_return_pct": mk["paper_return_pct"]}
                    for mk in items]

        spy_ret = (spy_rows.get(d) or {}).get("return_since_signal_pct")
        spy_daily = (spy_rows.get(d) or {}).get("daily_change_pct_points")
        excess = (_round(avg - spy_ret, 4) if (avg is not None and spy_ret is not None) else None)
        daily_change = (_round(avg - prev_avg, 4)
                        if (avg is not None and prev_avg is not None) else None)
        daily_excess = (_round(excess - prev_excess, 4)
                        if (excess is not None and prev_excess is not None) else None)
        n_up = sum(1 for x in rets if x > 0)
        best = _slim(ranked[:5])
        worst = _slim(list(reversed(ranked))[:5])
        row = {
            "mark_date": d,
            "book_id": _book_id(signal_date, book_size),
            "book_size": book_size,
            "covered_count": cov_n,
            "missing_count": total - cov_n,
            "total_count": total,
            "coverage_pct": _round(100.0 * cov_n / total, 2) if total else None,
            "coverage_status": g13._coverage_status(cov_n, total),
            "average_return_pct": _round(avg, 4),
            "median_return_pct": _round(g13._median(rets), 4) if rets else None,
            "hit_rate_pct": _round(100.0 * n_up / len(rets), 2) if rets else None,
            "daily_change_pct_points": daily_change,
            "cumulative_return_pct": _round(avg, 4),  # equal-weight, no rebalancing
            "previous_mark_date": prev_date,
            "spy_return_pct": _round(spy_ret, 4) if spy_ret is not None else None,
            "daily_spy_change_pct_points": spy_daily,
            "excess_return_vs_spy_pct_points": excess,
            "daily_excess_change_pct_points": daily_excess,
            "contributor_concentration_top5_pct": _concentration_top5(rets),
            "top5_signed_pnl_share_pct": _top5_signed_pnl_share(rets),
            "best_5": best,
            "worst_5": worst,
            "order_action_all": "NO_ORDER",
        }
        book_rows.append(row)
        if avg is not None:
            prev_avg = avg
        if excess is not None:
            prev_excess = excess
        prev_date = d
    return book_rows, position_rows


# --------------------------------------------------------------------------- #
# D. Part B - paper performance analytics.
# --------------------------------------------------------------------------- #
def _max_drawdown(cum_returns: List[Optional[float]], dates: List[str]) -> Dict[str, Any]:
    """Max drawdown on the equity curve implied by the cumulative-return strip
    (equity = 1 + cum/100). Returns depth (pp of equity), the peak/trough dates, the
    episode duration in observations, and whether it later recovered."""
    peak_equity = None
    peak_idx = None
    peak_date = None
    worst = 0.0
    worst_peak_date = None
    worst_trough_date = None
    worst_peak_idx = None
    worst_trough_idx = None
    for i, (cr, d) in enumerate(zip(cum_returns, dates)):
        if cr is None:
            continue
        eq = 1.0 + cr / 100.0
        if peak_equity is None or eq > peak_equity:
            peak_equity, peak_idx, peak_date = eq, i, d
        dd = eq / peak_equity - 1.0 if peak_equity else 0.0
        if dd < worst:
            worst = dd
            worst_peak_date, worst_trough_date = peak_date, d
            worst_peak_idx, worst_trough_idx = peak_idx, i
    recovered = False
    if worst_trough_idx is not None and worst_peak_idx is not None:
        peak_eq = 1.0 + (cum_returns[worst_peak_idx] or 0.0) / 100.0
        for j in range(worst_trough_idx + 1, len(cum_returns)):
            cj = cum_returns[j]
            if cj is None:
                continue
            if 1.0 + cj / 100.0 >= peak_eq:
                recovered = True
                break
    duration = ((worst_trough_idx - worst_peak_idx)
                if (worst_trough_idx is not None and worst_peak_idx is not None) else 0)
    return {
        "max_drawdown_pct": _round(worst * 100.0, 4),
        "max_drawdown_peak_date": worst_peak_date,
        "max_drawdown_trough_date": worst_trough_date,
        "max_drawdown_duration_obs": duration,
        "max_drawdown_recovered": recovered,
    }


def analytics_for_book(book_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Part B analytics for one book computed over its reconstructed daily strip."""
    n = len(book_rows)
    dates = [r["mark_date"] for r in book_rows]
    cum = [r["average_return_pct"] for r in book_rows]
    daily_changes = [r["daily_change_pct_points"] for r in book_rows
                     if r["daily_change_pct_points"] is not None]
    daily_excess = [r["daily_excess_change_pct_points"] for r in book_rows
                    if r["daily_excess_change_pct_points"] is not None]
    last = book_rows[-1] if book_rows else {}

    dd = _max_drawdown(cum, dates)
    best_daily = max(daily_changes) if daily_changes else None
    worst_daily = min(daily_changes) if daily_changes else None
    vol = statistics.pstdev(daily_changes) if len(daily_changes) >= 2 else None
    pct_pos_days = (100.0 * sum(1 for x in daily_changes if x > 0) / len(daily_changes)
                    if daily_changes else None)
    pct_outperf = (100.0 * sum(1 for x in daily_excess if x > 0) / len(daily_excess)
                   if daily_excess else None)
    avg_daily_excess = _mean(daily_excess)
    tracking_error = statistics.pstdev(daily_excess) if len(daily_excess) >= 2 else None
    ir_valid = (len(daily_excess) >= MIN_IR_OBS and tracking_error not in (None, 0.0))
    information_ratio = (avg_daily_excess / tracking_error) if ir_valid else None

    n_cov_warn = sum(1 for r in book_rows if r["coverage_status"] == COV_PARTIAL)
    n_cov_insuff = sum(1 for r in book_rows if r["coverage_status"] == COV_INSUFFICIENT)

    return {
        "book_id": last.get("book_id"),
        "book_size": last.get("book_size"),
        "n_observations": n,
        "start_date": dates[0] if dates else None,
        "end_date": dates[-1] if dates else None,
        "current_cumulative_return_pct": last.get("average_return_pct"),
        "spy_cumulative_return_pct": last.get("spy_return_pct"),
        "current_excess_return_pct_points": last.get("excess_return_vs_spy_pct_points"),
        "max_drawdown_pct": dd["max_drawdown_pct"],
        "max_drawdown_peak_date": dd["max_drawdown_peak_date"],
        "max_drawdown_trough_date": dd["max_drawdown_trough_date"],
        "max_drawdown_duration_obs": dd["max_drawdown_duration_obs"],
        "max_drawdown_recovered": dd["max_drawdown_recovered"],
        "best_daily_change_pct_points": _round(best_daily, 4),
        "worst_daily_change_pct_points": _round(worst_daily, 4),
        "daily_change_volatility_pct_points": _round(vol, 4),
        "pct_positive_daily_changes": _round(pct_pos_days, 2),
        "pct_days_outperforming_spy": _round(pct_outperf, 2),
        "average_daily_excess_change_pct_points": _round(avg_daily_excess, 4),
        "tracking_error_pct_points": _round(tracking_error, 4),
        "information_ratio": _round(information_ratio, 4),
        "information_ratio_valid": bool(ir_valid),
        "information_ratio_note": (
            "reported (per-observation excess mean / tracking error)" if ir_valid
            else "not reported: fewer than %d daily excess observations "
                 "(short forward period)" % MIN_IR_OBS),
        "contributor_concentration_top5_pct": last.get("contributor_concentration_top5_pct"),
        "pct_book_pnl_top5_contributors_signed": last.get("top5_signed_pnl_share_pct"),
        "n_coverage_warning_dates": n_cov_warn,
        "n_insufficient_coverage_dates": n_cov_insuff,
        "n_daily_change_observations": len(daily_changes),
        "short_forward_period_caveat": (
            "This is a short forward paper period; these operating statistics are NOT "
            "alpha validation and do not promote either book to live trading."),
        "order_action_all": "NO_ORDER",
    }


def stability_comparison(a25: Dict[str, Any], a50: Dict[str, Any]) -> Dict[str, Any]:
    """Preliminary OPERATING stability comparison (Top-25 vs Top-50). Not alpha
    validation; never promotes a book. Enum: TOP25_MORE_STABLE / TOP50_MORE_STABLE /
    NO_CLEAR_STABILITY_WINNER / INSUFFICIENT_DAILY_HISTORY."""
    obs = min(a25.get("n_daily_change_observations") or 0,
              a50.get("n_daily_change_observations") or 0)
    if obs < MIN_STABILITY_OBS:
        return {
            "assessment": "INSUFFICIENT_DAILY_HISTORY",
            "reason": "fewer than %d daily-change observations in at least one book" % MIN_STABILITY_OBS,
            "min_daily_change_observations": obs,
            "promotes_to_live": False,
        }
    v25, v50 = a25.get("daily_change_volatility_pct_points"), a50.get("daily_change_volatility_pct_points")
    dd25, dd50 = a25.get("max_drawdown_pct"), a50.get("max_drawdown_pct")
    votes25 = votes50 = 0
    # lower volatility is more stable
    if v25 is not None and v50 is not None and v25 != v50:
        if v25 < v50:
            votes25 += 1
        else:
            votes50 += 1
    # shallower (closer to 0, i.e. greater) drawdown is more stable
    if dd25 is not None and dd50 is not None and dd25 != dd50:
        if dd25 > dd50:
            votes25 += 1
        else:
            votes50 += 1
    if votes25 > votes50:
        assessment = "TOP25_MORE_STABLE"
    elif votes50 > votes25:
        assessment = "TOP50_MORE_STABLE"
    else:
        assessment = "NO_CLEAR_STABILITY_WINNER"
    return {
        "assessment": assessment,
        "top25_daily_volatility_pct_points": v25,
        "top50_daily_volatility_pct_points": v50,
        "top25_max_drawdown_pct": dd25,
        "top50_max_drawdown_pct": dd50,
        "min_daily_change_observations": obs,
        "note": "Operational paper-book comparison only. Does not change the champion "
                "and does not promote either book to live trading.",
        "promotes_to_live": False,
    }


# --------------------------------------------------------------------------- #
# E. Reconciliation vs the live Phase 13-G latest valid mark.
# --------------------------------------------------------------------------- #
def _abs_diff(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return abs(a - b)


def reconcile(book25_rows: List[Dict[str, Any]], book50_rows: List[Dict[str, Any]],
              spy_rows: Dict[str, Dict[str, Any]], mark_dir: Path) -> Dict[str, Any]:
    """Compare the reconstructed row AT the Phase 13-G latest valid mark date to the live
    13-G book/benchmark marks. Same frozen entries + same price rule -> near-exact."""
    manifest = g13._read_json(mark_dir / "latest" / "refresh_manifest.json")
    summaries = g13._read_json(mark_dir / "latest" / "book_summaries.json")
    if not isinstance(manifest, dict) or manifest.get("blocked") or not manifest.get("mark_date"):
        return {
            "status": DEC_WARNING,
            "reference_available": False,
            "reference_mark_date": None,
            "reason": "no Phase 13-G latest valid financial mark on disk to reconcile "
                      "against; analytics are published with a reconciliation warning.",
            "checks": [],
        }
    ref_date = manifest.get("mark_date")
    ref_top25 = ((summaries or {}).get("top25") or {})
    ref_top50 = ((summaries or {}).get("top50") or {})
    ref_bench = ((summaries or {}).get("benchmark") or manifest.get("benchmark_summary_preview") or {})

    row25 = next((r for r in book25_rows if r["mark_date"] == ref_date), None)
    row50 = next((r for r in book50_rows if r["mark_date"] == ref_date), None)
    spy_row = spy_rows.get(ref_date)

    checks: List[Dict[str, Any]] = []

    def _add(name, backfill_val, ref_val):
        diff = _abs_diff(backfill_val, ref_val)
        checks.append({
            "metric": name, "backfill": backfill_val, "reference": ref_val,
            "abs_diff": _round(diff, 6) if diff is not None else None,
            "within_tight": (diff is not None and diff <= RECON_TIGHT_PP),
            "within_loose": (diff is not None and diff <= RECON_LOOSE_PP),
            "comparable": diff is not None,
        })

    _add("top25_average_return_pct", (row25 or {}).get("average_return_pct"),
         ref_top25.get("average_return_pct"))
    _add("top50_average_return_pct", (row50 or {}).get("average_return_pct"),
         ref_top50.get("average_return_pct"))
    _add("spy_return_since_signal_pct", (spy_row or {}).get("return_since_signal_pct"),
         ref_bench.get("return_since_signal_pct"))

    date_match = (row25 is not None and row50 is not None
                  and (row25 or {}).get("mark_date") == ref_date)
    cov25_match = (row25 or {}).get("covered_count") == ref_top25.get("covered_count")
    cov50_match = (row50 or {}).get("covered_count") == ref_top50.get("covered_count")

    comparable = [c for c in checks if c["comparable"]]
    if not date_match or not comparable:
        status = DEC_WARNING
        reason = ("the Phase 13-G reference mark date is not present in the reconstructed "
                  "strip (or no metric was comparable); published with a warning.")
    elif all(c["within_tight"] for c in comparable):
        status = DEC_RECONCILED
        reason = "reconstructed marks reproduce the live Phase 13-G marks within tight tolerance."
    elif all(c["within_loose"] for c in comparable):
        status = DEC_WARNING
        reason = ("reconstructed marks match the live Phase 13-G marks within a loose "
                  "tolerance but not tight; published with a warning.")
    else:
        status = DEC_REJECTED
        reason = ("reconstructed marks diverge from the live Phase 13-G marks beyond the "
                  "loose tolerance; analytics are NOT published.")
    return {
        "status": status,
        "reference_available": True,
        "reference_mark_date": ref_date,
        "reference_source": "PHASE13G_DAILY_REFRESH",
        "date_present_in_backfill": date_match,
        "coverage_top25_match": cov25_match,
        "coverage_top50_match": cov50_match,
        "tight_tolerance_pct_points": RECON_TIGHT_PP,
        "loose_tolerance_pct_points": RECON_LOOSE_PP,
        "checks": checks,
        "reason": reason,
    }


# --------------------------------------------------------------------------- #
# F. Orchestration + atomic writes.
# --------------------------------------------------------------------------- #
def _blocked_manifest(decision: str, message: str, run_at: str, ref_today: date,
                      signal_date: str, uni_meta: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "phase": PHASE, "phase_name": PHASE_NAME, "decision": decision,
        "allowed_decisions": ALLOWED_DECISIONS, "blocked": True, "blocked_message": message,
        "reference_today": ref_today.isoformat(), "alpha_name": ALPHA_NAME,
        "signal_date": signal_date, "universe": uni_meta, "run_at": run_at,
        "analytics_published": False,
        "price_source": PRICE_SOURCE,
        "creates_orders": False, "creates_signals": False, "creates_trade_decisions": False,
        "creates_automation": False, "creates_broker_connection": False, "live_trading": False,
        "daily_rebalancing": False, "reranking": False, "wrote_to_paper_trader": False,
        "api_key_printed": False, "api_key_persisted": False, "order_action_all": "NO_ORDER",
        "safety_badges": list(SAFETY_BADGES),
    }


def backfill(package_dir: Path, mark_dir: Path, *, backfill_dir: Optional[Path] = None,
             transport: Optional[g13.Transport] = None, today: Optional[str] = None,
             start: str = DEFAULT_FETCH_START, log: Optional[g13._Log] = None) -> Dict[str, Any]:
    """Run the full historical mark backfill + analytics and (unless blocked/rejected)
    persist the reconstruction artifact set. Returns the backfill manifest dict."""
    log = log or g13._Log()
    ref_today = g13._today(today)
    run_at = datetime.now(timezone.utc).isoformat()
    out_dir = _resolve_backfill_dir(mark_dir, backfill_dir)

    # --- frozen universe (Top-50 book + SPY; NO shadow, NO rerank) ------------
    positions, uni_meta = g13.load_source_universe(package_dir, None)
    signal_date = uni_meta["signal_date"]
    universe: List[str] = [BENCHMARK_TICKER]
    for p in positions:
        if p["ticker"] not in universe:
            universe.append(p["ticker"])
    log.step("universe", "DONE", "frozen book union", tickers=len(universe),
             signal=signal_date)

    # --- transport (live client reused; key from env only) --------------------
    tr = transport
    if tr is None:
        if not os.environ.get(EODHD_KEY_ENV):
            return _blocked_manifest(DEC_BLOCKED_KEY, "EODHD_API_KEY is not set", run_at,
                                     ref_today, signal_date, uni_meta)
        tr = g13.live_transport()

    series, acq, block = acquire_series(tr, universe, start, ref_today, log)
    if block is not None:
        return _blocked_manifest(block, "provider stop during acquisition", run_at,
                                 ref_today, signal_date, uni_meta)

    # --- common trading calendar (SPY completed sessions, on/after signal) ----
    spy_bars = series.get(BENCHMARK_TICKER, [])
    dates = trading_dates(spy_bars, signal_date, ref_today)
    if not dates:
        return _blocked_manifest(DEC_BLOCKED_SCHEMA,
                                 "no completed SPY trading dates on/after the signal date",
                                 run_at, ref_today, signal_date, uni_meta)
    log.step("calendar", "DONE", "trading dates", n=len(dates),
             first=dates[0], last=dates[-1])

    # --- SPY curve + per-book reconstruction (frozen holdings) ----------------
    spy_rows, spy_ref_date, spy_ref_price = spy_curve(spy_bars, dates, signal_date)
    book25_rows, pos25_rows = book_history(positions, series, dates, 25, signal_date, spy_rows)
    book50_rows, pos50_rows = book_history(positions, series, dates, 50, signal_date, spy_rows)

    # --- reconciliation vs the live Phase 13-G latest valid mark --------------
    recon = reconcile(book25_rows, book50_rows, spy_rows, mark_dir)
    decision = recon["status"]

    # --- Part B analytics (only computed/published when not rejected) ---------
    a25 = analytics_for_book(book25_rows)
    a50 = analytics_for_book(book50_rows)
    stability = stability_comparison(a25, a50)
    analytics_published = decision != DEC_REJECTED

    latest = dates[-1]
    manifest = {
        "phase": PHASE, "phase_name": PHASE_NAME,
        "decision": decision, "allowed_decisions": ALLOWED_DECISIONS,
        "blocked": False, "analytics_published": analytics_published,
        "alpha_name": ALPHA_NAME, "signal_date": signal_date,
        "reference_today": ref_today.isoformat(),
        "backfill_start_date": dates[0], "backfill_end_date": latest,
        "n_observations": len(dates),
        "frozen_holdings": True, "reranking": False, "daily_rebalancing": False,
        "universe": uni_meta,
        "benchmark": {
            "ticker": BENCHMARK_TICKER, "reference_date": spy_ref_date,
            "reference_price": spy_ref_price,
            "latest_return_since_signal_pct": (spy_rows.get(latest) or {}).get("return_since_signal_pct"),
        },
        "latest_marks": {
            "mark_date": latest,
            "top25_average_return_pct": (book25_rows[-1] if book25_rows else {}).get("average_return_pct"),
            "top50_average_return_pct": (book50_rows[-1] if book50_rows else {}).get("average_return_pct"),
            "top25_excess_return_pct_points": (book25_rows[-1] if book25_rows else {}).get("excess_return_vs_spy_pct_points"),
            "top50_excess_return_pct_points": (book50_rows[-1] if book50_rows else {}).get("excess_return_vs_spy_pct_points"),
            "top25_coverage": (book25_rows[-1] if book25_rows else {}).get("coverage_pct"),
            "top50_coverage": (book50_rows[-1] if book50_rows else {}).get("coverage_pct"),
        },
        "reconciliation": recon,
        "acquisition": {tk: acq.get(tk) for tk in universe},
        "price_source": PRICE_SOURCE,
        "run_at": run_at,
        # --- safety ---
        "creates_orders": False, "creates_signals": False, "creates_trade_decisions": False,
        "creates_automation": False, "creates_broker_connection": False, "live_trading": False,
        "wrote_to_paper_trader": False, "api_key_printed": False, "api_key_persisted": False,
        "order_action_all": "NO_ORDER", "safety_badges": list(SAFETY_BADGES),
    }

    performance_summary = {
        "phase": PHASE, "decision": decision, "analytics_published": analytics_published,
        "alpha_name": ALPHA_NAME, "signal_date": signal_date,
        "backfill_start_date": dates[0], "backfill_end_date": latest,
        "n_observations": len(dates),
        "top25": a25, "top50": a50, "stability_comparison": stability,
        "reconciliation_status": decision,
        "not_alpha_validation": (
            "This reconstruction is a paper mark-to-market of frozen holdings over a short "
            "forward window. It is NOT alpha validation and promotes no book to live trading."),
        "run_at": run_at, "order_action_all": "NO_ORDER", "safety_badges": list(SAFETY_BADGES),
    }

    if decision == DEC_REJECTED:
        # Publish ONLY the manifest (with the failed reconciliation) — NO analytics/history.
        g13._atomic_write_json(out_dir / "backfill_manifest.json", manifest)
        log.step("reject", "STOP", "reconciliation rejected; analytics NOT published")
        return manifest

    # --- atomic writes (reconciled or warning) --------------------------------
    _write_backfill_artifacts(out_dir, manifest, book25_rows, book50_rows, spy_rows, dates,
                              pos25_rows, pos50_rows, performance_summary)
    log.step("write", "DONE", "%s obs=%d latest=%s" % (decision, len(dates), latest))
    return manifest


def _write_backfill_artifacts(out_dir: Path, manifest: Dict[str, Any],
                              book25_rows, book50_rows, spy_rows, dates, pos25_rows,
                              pos50_rows, performance_summary) -> None:
    g13._atomic_write_json(out_dir / "backfill_manifest.json", manifest)
    g13._atomic_write_json(out_dir / "top25_daily_history.json",
                           {"book_size": 25, "n_observations": len(book25_rows), "rows": book25_rows})
    g13._atomic_write_json(out_dir / "top50_daily_history.json",
                           {"book_size": 50, "n_observations": len(book50_rows), "rows": book50_rows})
    spy_list = [spy_rows[d] for d in dates if d in spy_rows]
    g13._atomic_write_json(out_dir / "spy_daily_history.json",
                           {"ticker": BENCHMARK_TICKER, "n_observations": len(spy_list), "rows": spy_list})
    g13._atomic_write_json(out_dir / "paper_performance_summary.json", performance_summary)

    g13._atomic_write_csv(out_dir / "top25_daily_history.csv", _BOOK_HISTORY_CSV_HEADER,
                          (_book_row_csv(r) for r in book25_rows))
    g13._atomic_write_csv(out_dir / "top50_daily_history.csv", _BOOK_HISTORY_CSV_HEADER,
                          (_book_row_csv(r) for r in book50_rows))
    g13._atomic_write_csv(out_dir / "spy_daily_history.csv", _SPY_HISTORY_CSV_HEADER,
                          ([spy_rows[d][c] for c in _SPY_HISTORY_CSV_HEADER]
                           for d in dates if d in spy_rows))
    g13._atomic_write_csv(out_dir / "position_daily_marks.csv", _POSITION_CSV_HEADER,
                          ([r[c] for c in _POSITION_CSV_HEADER] for r in (pos25_rows + pos50_rows)))


def _fmt_slim(items) -> str:
    return "|".join("%s:%s" % (i["ticker"], i["paper_return_pct"]) for i in (items or []))


def _book_row_csv(r: Dict[str, Any]) -> List[Any]:
    out = []
    for c in _BOOK_HISTORY_CSV_HEADER:
        if c == "best_5":
            out.append(_fmt_slim(r.get("best_5")))
        elif c == "worst_5":
            out.append(_fmt_slim(r.get("worst_5")))
        else:
            out.append(r.get(c))
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 13-I historical daily mark backfill")
    ap.add_argument("--package-dir", default=str(_DEFAULT_PACKAGE_DIR))
    ap.add_argument("--mark-dir", default=os.environ.get(DAILY_MARK_DIR_ENV,
                                                         str(_DEFAULT_DAILY_MARK_DIR)))
    ap.add_argument("--backfill-dir", default=None,
                    help="explicit output dir (default: <mark-dir>/backfill)")
    ap.add_argument("--start", default=DEFAULT_FETCH_START)
    ap.add_argument("--today", default=None, help="reference calendar day (YYYY-MM-DD) for the "
                                                  "completed-EOD rule; defaults to UTC today")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)
    log = g13._Log(verbose=not args.quiet)
    manifest = backfill(Path(args.package_dir), Path(args.mark_dir),
                        backfill_dir=(Path(args.backfill_dir) if args.backfill_dir else None),
                        today=args.today, start=args.start, log=log)
    print("[%s] DECISION %s obs=%s latest=%s" % (
        PHASE, manifest.get("decision"), manifest.get("n_observations"),
        (manifest.get("latest_marks") or {}).get("mark_date")))
    return 0 if manifest.get("decision") not in _BLOCKED else 2


if __name__ == "__main__":
    raise SystemExit(main())
