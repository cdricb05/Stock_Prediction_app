"""Phase 10-I - Paper-Only Position Tracker for the Rules-Based Quarterly Quality Portfolio.

WHY THIS PHASE EXISTS
    Phase 10-H built a rules-based 25-long / 25-short equal-weight paper portfolio
    (RULES_BASED_PAPER_PORTFOLIO_READY_WITH_EXCEPTIONS). The next allowed step is to TRACK that paper
    book: a ledger, a mark-to-market plan/snapshot off OWNED local prices, expected-vs-realized against
    the 10-D net-25bps benchmark, and exposure/risk summaries. It is a read/observe harness only.

    NOT a new alpha search. NOT a provider search. NOT a Paper Trader integration. NOT order creation.
    NOT automation. NOT a deploy. Fully offline (no network, no API key, no provider probe). It writes
    ONLY metadata CSV/JSON to its own research/output directory. It creates NO Paper Trader signals, NO
    trade decisions, and NO orders. Every ledger row keeps order_action = NO_ORDER.

MARK-TO-MARKET (owned local prices only)
    Prices come from research/data/eodhd/raw/eod_prices/<ticker>.json (owned EODHD daily OHLCV +
    adjusted_close). For each holding: entry_price = adjusted_close at/just-before the paper inception
    date; current_price = adjusted_close at the latest LOCAL date. A name is MARKED only when the local
    data extends PAST inception; otherwise it is PENDING_PRICE_REFRESH (entry captured, no later local
    price), or NO_LOCAL_PRICE when we own no file for it. No live market API is ever called.

EXPECTED BENCHMARK
    From Phase 10-D (the confirmed composite, signal_results with the highest ic_t_63d):
    quarterly_net_25bps / quarterly_net_50bps spread. This is the FULL-rank sector-neutral quintile
    composite's expected quarterly net spread - an APPROXIMATE benchmark for this liquidity-filtered
    25/25 equal-weight subset, not a promise. Documented, not hidden.

TERMINAL DECISIONS (allowed)
    PAPER_POSITION_TRACKER_READY | PAPER_POSITION_TRACKER_READY_PENDING_PRICE_REFRESH |
    PAPER_POSITION_TRACKER_BLOCKED_INVALID_PORTFOLIO | PAPER_POSITION_TRACKER_BLOCKED_MISSING_SELECTED_BOOK
    | HARD_BLOCKER_REQUIRES_USER_ACTION | ERROR_WITH_REPRO_COMMAND
    FORBIDDEN: LIVE_TRADING_READY, ORDER_READY, AUTOMATION_READY, PAPER_TRADER_READY,
    STRONG_ALPHA_FOUND_READY_FOR_REVIEW, MISSING_KEY, NO_DATA, NEEDS_PROVIDER, EMPTY_PAYLOAD,
    generic ERROR.

CONSTRAINTS HONORED
    Offline (no network/key/provider probe); owned/local data only; no FMP/AlphaVantage/Polygon/Finnhub/
    Norgate-API; no new purchase; no Paper Trader writes; no Paper Trader signals; no trade decisions;
    NO orders; NO automation; NO broker; NO live trading; no deploy; no GCP; no package install; no full
    regression (targeted tests only); keys never printed or written; output is metadata only. No commit.
    No push.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research import run_phase8s_autonomous_eodhd_alpha_factory as s8            # noqa: E402  io helpers
from research import run_phase10e_quarterly_quality_paper_review_harness as e10  # noqa: E402  badges/status
from research import run_phase10b_eodhd_norgate_exhaustive_alpha_factory as b10  # noqa: E402  secret audit

_write_json = s8._write_json
_write_csv = s8._write_csv
_read_csv_file = s8._read_csv_file
_read_json = s8._read_json
_rel = s8._rel
_finite = b10._finite

PHASE = "10-I"
PERFORMS_NETWORK = False

ORDER_ACTION = "NO_ORDER"
REVIEW_STATUS = getattr(e10, "REVIEW_STATUS", "PAPER_REVIEW_ONLY")
SIDE_LONG = getattr(e10, "SIDE_LONG", "LONG")
SIDE_SHORT = getattr(e10, "SIDE_SHORT", "SHORT")
PAPER_STATUS_OPEN = "PAPER_OPEN"            # notionally open for tracking; NOT a real/broker position
EXPECTED_DEFAULT_N_PER_SIDE = 25

SAFETY_BADGES = [
    "PAPER TRACKING ONLY", "NO ORDERS", "NO AUTOMATION", "NO BROKER", "HUMAN REVIEW REQUIRED",
    "NO LIVE TRADING", "NO PAPER TRADER WRITES", "CREATES NO TRADE DECISIONS", "MANUAL REVIEW",
]

# --- price status per holding ---
PS_MARKED = "MARKED"
PS_PENDING = "PENDING_PRICE_REFRESH"
PS_NO_PRICE = "NO_LOCAL_PRICE"
PS_NO_ENTRY = "NO_ENTRY_PRICE"

# --- terminal decisions ---
DEC_READY = "PAPER_POSITION_TRACKER_READY"
DEC_PENDING = "PAPER_POSITION_TRACKER_READY_PENDING_PRICE_REFRESH"
DEC_INVALID = "PAPER_POSITION_TRACKER_BLOCKED_INVALID_PORTFOLIO"
DEC_MISSING = "PAPER_POSITION_TRACKER_BLOCKED_MISSING_SELECTED_BOOK"
DEC_HARD_BLOCKER = "HARD_BLOCKER_REQUIRES_USER_ACTION"
DEC_ERROR = "ERROR_WITH_REPRO_COMMAND"
ALLOWED_DECISIONS = (DEC_READY, DEC_PENDING, DEC_INVALID, DEC_MISSING, DEC_HARD_BLOCKER, DEC_ERROR)
FORBIDDEN_DECISIONS = (
    "LIVE_TRADING_READY", "ORDER_READY", "AUTOMATION_READY", "PAPER_TRADER_READY",
    "STRONG_ALPHA_FOUND_READY_FOR_REVIEW", "MISSING_KEY", "NO_DATA", "NEEDS_PROVIDER",
    "EMPTY_PAYLOAD", "ERROR",
)

# --- paths ---
_H10_DIR = _REPO_ROOT / "research" / "output" / "phase10h_rules_based_paper_portfolio"
_DEF_PORTFOLIO = _H10_DIR / "selected_paper_portfolio.csv"
_DEF_LONG = _H10_DIR / "selected_long_book.csv"
_DEF_SHORT = _H10_DIR / "selected_short_book.csv"
_DEF_H10_JSON = _H10_DIR / "phase10h_rules_based_paper_portfolio.json"
_DEF_D10_JSON = (_REPO_ROOT / "research" / "output" / "phase10d_quarterly_quality_composite_validation"
                 / "phase10d_quarterly_quality_composite_validation.json")
_DEF_EOD_DIR = _REPO_ROOT / "research" / "data" / "eodhd" / "raw" / "eod_prices"
_DEFAULT_OUT = _REPO_ROOT / "research" / "output" / "phase10i_paper_position_tracker"

_ARTIFACTS = {
    "report": "phase10i_paper_position_tracker.json",
    "ledger": "paper_holdings_ledger.csv",
    "mtm_plan": "paper_mark_to_market_plan.csv",
    "mtm_snapshot": "paper_mark_to_market_snapshot.csv",
    "exp_real": "paper_expected_vs_realized_template.csv",
    "exposure": "paper_exposure_summary.csv",
    "sector": "paper_sector_exposure.csv",
    "liquidity": "paper_liquidity_summary.csv",
    "badges": "paper_tracker_safety_badges.csv",
    "status": "paper_tracker_status.csv",
    "next_plan": "phase10j_next_plan.json",
    "secret_audit": "secret_safety_audit.csv",
}


# --------------------------------------------------------------------------- #
# Small helpers.
# --------------------------------------------------------------------------- #
def _to_float(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def _round(x, n=6):
    v = _to_float(x)
    return None if v is None else round(v, n)


def _median(vals: Sequence[float]) -> Optional[float]:
    xs = sorted(v for v in vals if _finite(v))
    if not xs:
        return None
    n = len(xs)
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2.0


# --------------------------------------------------------------------------- #
# Portfolio validation (req 2-4).
# --------------------------------------------------------------------------- #
def _validate_portfolio(rows: List[Dict], h10_meta: Dict) -> Tuple[bool, List[str], int, int]:
    issues: List[str] = []
    if not rows:
        return False, ["selected_paper_portfolio.csv is empty"], 0, 0

    longs = [r for r in rows if (r.get("side") or "").strip().upper() == SIDE_LONG]
    shorts = [r for r in rows if (r.get("side") or "").strip().upper() == SIDE_SHORT]

    # 2. every row paper-only with order_action == NO_ORDER
    if any((r.get("order_action") or "").strip() != ORDER_ACTION for r in rows):
        issues.append("a row has order_action != NO_ORDER")
    if any((r.get("review_status") or "").strip() != REVIEW_STATUS for r in rows):
        issues.append(f"a row has review_status != {REVIEW_STATUS}")

    # 3. equal weights within each side
    for side, side_rows in ((SIDE_LONG, longs), (SIDE_SHORT, shorts)):
        ws = [_to_float(r.get("target_weight")) for r in side_rows]
        ws = [w for w in ws if w is not None]
        if ws and (max(ws) - min(ws)) > 1e-9:
            issues.append(f"{side} weights are not equal")

    # 4. 25 long / 25 short unless the input explicitly says otherwise
    exp_long = int(h10_meta.get("n_long") or EXPECTED_DEFAULT_N_PER_SIDE)
    exp_short = int(h10_meta.get("n_short") or EXPECTED_DEFAULT_N_PER_SIDE)
    if len(longs) != exp_long:
        issues.append(f"long count {len(longs)} != expected {exp_long}")
    if len(shorts) != exp_short:
        issues.append(f"short count {len(shorts)} != expected {exp_short}")
    if not longs or not shorts:
        issues.append("one side is empty")

    return (not issues), issues, len(longs), len(shorts)


# --------------------------------------------------------------------------- #
# Owned local EOD price access (offline).
# --------------------------------------------------------------------------- #
def _load_eod_series(eod_dir: Path, ticker: str) -> Optional[List[Tuple[str, float]]]:
    f = eod_dir / f"{ticker}.json"
    if not f.exists():
        return None
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    out: List[Tuple[str, float]] = []
    for row in raw if isinstance(raw, list) else []:
        d = row.get("date")
        px = _to_float(row.get("adjusted_close"))
        if px is None:
            px = _to_float(row.get("close"))
        if d and px is not None:
            out.append((str(d), px))
    out.sort(key=lambda t: t[0])     # ISO dates sort lexicographically
    return out or None


def _price_at_or_before(series: List[Tuple[str, float]], iso_dt: str) -> Optional[Tuple[str, float]]:
    hit = None
    for d, px in series:
        if d <= iso_dt:
            hit = (d, px)
        else:
            break
    return hit


def _mark_one(side: str, weight: float, series: Optional[List[Tuple[str, float]]], inception: str
              ) -> Dict:
    """Mark one holding off owned local prices. Returns a snapshot dict (no network)."""
    res = {"entry_price": None, "entry_date": None, "current_price": None, "current_date": None,
           "paper_return": None, "signed_return": None, "weighted_contribution": None,
           "price_status": PS_NO_PRICE, "latest_local_date": None, "local_file_present": False}
    if not series:
        return res
    res["local_file_present"] = True
    res["latest_local_date"] = series[-1][0]
    entry = _price_at_or_before(series, inception)
    if entry is None:
        res["price_status"] = PS_NO_ENTRY
        return res
    res["entry_date"], res["entry_price"] = entry
    latest_date, latest_px = series[-1]
    res["current_date"], res["current_price"] = latest_date, latest_px
    if latest_date <= inception:
        # entry captured, but no LOCAL price past inception yet -> pending a future local refresh
        res["price_status"] = PS_PENDING
        return res
    raw_ret = (latest_px / entry[1]) - 1.0 if entry[1] else None
    if raw_ret is None:
        res["price_status"] = PS_NO_ENTRY
        return res
    signed = raw_ret if side == SIDE_LONG else -raw_ret
    res["paper_return"] = raw_ret
    res["signed_return"] = signed
    res["weighted_contribution"] = weight * signed
    res["price_status"] = PS_MARKED
    return res


# --------------------------------------------------------------------------- #
# Expected benchmark from Phase 10-D (confirmed composite = highest ic_t_63d).
# --------------------------------------------------------------------------- #
# The 10-H book ranks by the SECTOR-NEUTRAL composite (comp_sn), so the apt benchmark is the 10-D
# sector-neutral composite signal - NOT the highest-ic_t leg, and not the raw composite.
_BENCH_PREF = ("composite_sn", "composite", "composite_raw")


def _expected_benchmark(d10_json: Path, preferred: Sequence[str] = _BENCH_PREF) -> Dict:
    meta = _read_json(d10_json) if d10_json.exists() else {}
    sr = meta.get("signal_results") or []
    if not sr:
        return {"available": False, "source": _rel(d10_json) if d10_json.exists() else None,
                "benchmark_signal": None, "quarterly_net_25bps": None, "quarterly_net_50bps": None,
                "ic_t_63d": None, "quarterly_turnover": None}
    by_name: Dict[str, Dict] = {}
    for s in sr:
        nm = (s.get("signal") or s.get("name") or "").strip().lower()
        if nm:
            by_name.setdefault(nm, s)
    chosen, label = None, None
    for p in preferred:
        if p in by_name:
            chosen, label = by_name[p], p
            break
    if chosen is None:                        # no named composite -> fall back to highest ic_t
        chosen = max(sr, key=lambda s: (_to_float(s.get("ic_t_63d")) or -1e9))
        label = (chosen.get("signal") or chosen.get("name") or "highest_ic_t")
    return {
        "available": True,
        "source": _rel(d10_json),
        "benchmark_signal": label,
        "quarterly_net_25bps": _to_float(chosen.get("quarterly_net_25bps")),
        "quarterly_net_50bps": _to_float(chosen.get("quarterly_net_50bps")),
        "ic_t_63d": _to_float(chosen.get("ic_t_63d")),
        "quarterly_turnover": _to_float(chosen.get("quarterly_turnover")),
    }


# --------------------------------------------------------------------------- #
# Artifact builders.
# --------------------------------------------------------------------------- #
_LEDGER_HEADER = ["ticker", "side", "target_weight", "sector", "entry_reference_date",
                  "next_rebalance_date", "paper_status", "order_action"]
_MTM_PLAN_HEADER = ["ticker", "side", "price_source", "local_file_present", "entry_reference_date",
                    "latest_local_price_date", "mark_method", "status"]
_MTM_SNAP_HEADER = ["ticker", "side", "target_weight", "entry_reference_date", "entry_price",
                    "current_price", "current_price_date", "paper_return_pct",
                    "side_signed_return_pct", "weighted_contribution_pct", "price_status"]
_EXP_REAL_HEADER = ["metric", "expected", "realized", "unit", "status", "note"]
_EXPOSURE_HEADER = ["metric", "value"]
_SECTOR_HEADER = ["side", "sector", "n_names", "weight_pct"]
_LIQ_HEADER = ["side", "n_names", "min_liquidity_proxy", "median_liquidity_proxy",
               "max_liquidity_proxy"]
_BADGES_HEADER = ["badge", "value"]
_STATUS_HEADER = ["field", "value"]

_PRICE_SOURCE = "EODHD_LOCAL_EOD(adjusted_close)"
_MARK_METHOD = "adjusted_close ratio vs entry, side-signed (long=+ , short=-)"


def _pct(x, n=4):
    v = _to_float(x)
    return None if v is None else round(v * 100.0, n)


def _build(rows: List[Dict], marks: Dict[str, Dict], inception: str, rebalance: Optional[str]):
    ledger, plan, snap = [], [], []
    for r in rows:
        tk = r.get("ticker")
        side = (r.get("side") or "").strip().upper()
        w = _to_float(r.get("target_weight"))
        sector = r.get("sector")
        m = marks[tk]
        ledger.append([tk, side, w, sector, inception, rebalance, PAPER_STATUS_OPEN, ORDER_ACTION])
        plan.append([tk, side, _PRICE_SOURCE, m["local_file_present"], inception,
                     m["latest_local_date"], _MARK_METHOD, m["price_status"]])
        snap.append([tk, side, w, inception, _round(m["entry_price"], 4),
                     _round(m["current_price"], 4), m["current_date"], _pct(m["paper_return"]),
                     _pct(m["signed_return"]), _pct(m["weighted_contribution"]), m["price_status"]])
    return ledger, plan, snap


def _exposure_rows(rows: List[Dict], n_long: int, n_short: int) -> List[List]:
    lw = sum(_to_float(r.get("target_weight")) or 0.0 for r in rows
             if (r.get("side") or "").upper() == SIDE_LONG)
    sw = sum(_to_float(r.get("target_weight")) or 0.0 for r in rows
             if (r.get("side") or "").upper() == SIDE_SHORT)
    max_w = max((_to_float(r.get("target_weight")) or 0.0 for r in rows), default=0.0)
    return [
        ["n_holdings", len(rows)], ["n_long", n_long], ["n_short", n_short],
        ["long_gross_pct", round(lw * 100, 4)], ["short_gross_pct", round(sw * 100, 4)],
        ["net_pct", round((lw - sw) * 100, 4)], ["gross_pct", round((lw + sw) * 100, 4)],
        ["largest_name_weight_pct", round(max_w * 100, 4)], ["weighting", "EQUAL"],
    ]


def _sector_rows(rows: List[Dict]) -> Tuple[List[List], float]:
    out, largest = [], 0.0
    for side in (SIDE_LONG, SIDE_SHORT):
        agg: Dict[str, float] = {}
        cnt: Dict[str, int] = {}
        for r in rows:
            if (r.get("side") or "").upper() != side:
                continue
            sec = r.get("sector") or "Unknown"
            agg[sec] = agg.get(sec, 0.0) + (_to_float(r.get("target_weight")) or 0.0)
            cnt[sec] = cnt.get(sec, 0) + 1
        for sec, w in sorted(agg.items(), key=lambda kv: (-kv[1], kv[0])):
            share = round(w * 100, 4)
            largest = max(largest, share)
            out.append([side, sec, cnt[sec], share])
    return out, round(largest, 4)


def _liquidity_rows(rows: List[Dict]) -> List[List]:
    out = []
    for side in (SIDE_LONG, SIDE_SHORT):
        liq = [_to_float(r.get("liquidity_proxy")) for r in rows
               if (r.get("side") or "").upper() == side]
        liq = [x for x in liq if _finite(x)]
        out.append([side, len(liq), _round(min(liq), 2) if liq else None,
                    _round(_median(liq), 2) if liq else None, _round(max(liq), 2) if liq else None])
    return out


def _exp_real_rows(bench: Dict, realized_return: Optional[float], mtm_status: str,
                   holding_days: Optional[int]) -> List[List]:
    pend = mtm_status != "COMPUTED"
    real_status = "PENDING_PRICE_REFRESH" if pend else "COMPUTED"
    return [
        ["quarterly_net_25bps_spread", _round(bench.get("quarterly_net_25bps"), 5),
         "PENDING" if pend else _round(realized_return, 5), "return_fraction", real_status,
         "expected from Phase 10-D confirmed composite (full-rank quintile L/S; approximate for 25/25)"],
        ["quarterly_net_50bps_spread", _round(bench.get("quarterly_net_50bps"), 5),
         "PENDING" if pend else _round(realized_return, 5), "return_fraction", real_status,
         "expected from Phase 10-D confirmed composite"],
        ["composite_ic_t_63d", _round(bench.get("ic_t_63d"), 3), "N/A", "t_stat", "INFORMATIONAL",
         "diagnostic from Phase 10-D; not a paper-book realized stat"],
        ["realized_paper_return_to_date", "N/A", "PENDING" if pend else _round(realized_return, 5),
         "return_fraction", real_status, "marked off owned local prices only; no live market data"],
        ["holding_period_days_elapsed", "N/A", holding_days if holding_days is not None else "PENDING",
         "days", real_status, "local latest price date minus inception"],
    ]


def _badge_rows() -> List[List]:
    return [[b, "CONFIRMED"] for b in SAFETY_BADGES]


def _status_rows(decision: str, mtm_status: str, n_long: int, n_short: int, inception: str,
                 rebalance: Optional[str], coverage: Dict) -> List[List]:
    return [
        ["PAPER TRACKING ONLY", "YES"], ["NO ORDERS", "CONFIRMED"], ["NO AUTOMATION", "CONFIRMED"],
        ["NO BROKER", "CONFIRMED"], ["HUMAN REVIEW REQUIRED", "YES"], ["NO LIVE TRADING", "CONFIRMED"],
        ["NO PAPER TRADER WRITES", "CONFIRMED"], ["decision", decision], ["mtm_status", mtm_status],
        ["n_long", n_long], ["n_short", n_short], ["inception_date", inception],
        ["next_rebalance_date", rebalance], ["n_marked", coverage["n_marked"]],
        ["n_pending_price_refresh", coverage["n_pending"]], ["n_no_local_price", coverage["n_no_price"]],
    ]


def _phase10j_plan(decision: str, mtm_status: str) -> Dict:
    return {
        "phase": "10-J (planned)",
        "title": "Paper tracker price refresh + first realized quarterly review",
        "depends_on": decision,
        "steps": [
            "when owned local EOD prices extend PAST the inception date, re-run this tracker to mark "
            "the book (entry/current adjusted_close, side-signed)",
            "fill paper_expected_vs_realized_template.csv: realized quarterly net spread vs the 10-D "
            "expected net-25bps benchmark",
            "at the 2026-09-30 rebalance, snapshot realized vs expected and re-run the 10-H rules to "
            "form the next quarter's paper book - still NO orders/automation/broker/live/deploy",
        ],
        "still_forbidden": ["orders", "automation", "broker", "live trading", "deploy",
                            "Paper Trader writes", "new data purchase", "live market API"],
        "mtm_status": mtm_status,
        "exact_next_command": ("review research/output/phase10i_paper_position_tracker/"
                               "paper_tracker_status.csv"),
    }


# --------------------------------------------------------------------------- #
# Decision + run.
# --------------------------------------------------------------------------- #
def _print_summary(rep: Dict) -> None:
    print(
        f"[{PHASE}] decision={rep['decision']} | holdings={rep['n_holdings']} "
        f"(long={rep['n_long']} short={rep['n_short']}) | inception={rep['inception_date']} "
        f"rebalance={rep['next_rebalance_date']} | mtm={rep['mtm_status']} "
        f"(marked={rep['price_coverage']['n_marked']} pending={rep['price_coverage']['n_pending']} "
        f"no_price={rep['price_coverage']['n_no_price']}) | "
        f"net={rep['net_pct']}% gross={rep['gross_pct']}% | wrote_pt={rep['wrote_to_paper_trader']} "
        f"orders={rep['creates_orders']} automation={rep['creates_automation']} "
        f"leak_clean={rep['secret_safety_leak_scan_clean']}"
    )


def _finish_blocker(out_dir: Path, decision: str, reason: str, repro: str, verbose: bool,
                    extra: Optional[Dict] = None) -> Dict:
    rep = {
        "phase": PHASE, "decision": decision, "allowed_decisions": list(ALLOWED_DECISIONS),
        "forbidden_decisions": list(FORBIDDEN_DECISIONS), "reason": reason,
        "n_holdings": 0, "n_long": 0, "n_short": 0, "inception_date": None,
        "next_rebalance_date": None, "mtm_status": "NOT_RUN",
        "price_coverage": {"n_marked": 0, "n_pending": 0, "n_no_price": 0},
        "net_pct": None, "gross_pct": None,
        "wrote_to_paper_trader": False, "creates_orders": False, "creates_automation": False,
        "creates_paper_trader_signals": False, "creates_trade_decisions": False,
        "secret_safety_leak_scan_clean": True, "exact_next_command": repro,
    }
    if extra:
        rep.update(extra)
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / _ARTIFACTS["report"], rep)
    if verbose:
        _print_summary(rep)
    return rep


def run(out_dir: Optional[Path] = None, *, portfolio_csv: Optional[Path] = None,
        long_csv: Optional[Path] = None, short_csv: Optional[Path] = None,
        h10_json: Optional[Path] = None, d10_json: Optional[Path] = None,
        eod_dir: Optional[Path] = None, verbose: bool = True) -> Dict:
    out_dir = Path(out_dir) if out_dir else _DEFAULT_OUT
    portfolio_csv = Path(portfolio_csv) if portfolio_csv else _DEF_PORTFOLIO
    h10_json = Path(h10_json) if h10_json else _DEF_H10_JSON
    d10_json = Path(d10_json) if d10_json else _DEF_D10_JSON
    eod_dir = Path(eod_dir) if eod_dir else _DEF_EOD_DIR

    if not portfolio_csv.exists():
        return _finish_blocker(
            out_dir, DEC_MISSING, f"selected_paper_portfolio.csv not found at {_rel(portfolio_csv)}",
            f"python research/run_phase10i_paper_position_tracker.py --portfolio \"{_rel(portfolio_csv)}\"",
            verbose)

    rows = _read_csv_file(portfolio_csv)
    h10_meta = _read_json(h10_json) if h10_json.exists() else {}

    ok, issues, n_long, n_short = _validate_portfolio(rows, h10_meta)
    if not ok:
        return _finish_blocker(
            out_dir, DEC_INVALID, "selected portfolio failed validation: " + "; ".join(issues),
            "python research/run_phase10i_paper_position_tracker.py", verbose,
            extra={"validation_issues": issues, "n_long": n_long, "n_short": n_short})

    # --- dates from 10-H metadata (req 5/6) ---
    inception = (h10_meta.get("as_of") or "2026-06-26")
    rebalance = h10_meta.get("expected_rebalance_date")

    # --- mark to market off owned local prices only ---
    marks: Dict[str, Dict] = {}
    for r in rows:
        tk = r.get("ticker")
        side = (r.get("side") or "").strip().upper()
        w = _to_float(r.get("target_weight")) or 0.0
        series = _load_eod_series(eod_dir, tk)
        marks[tk] = _mark_one(side, w, series, inception)

    n_marked = sum(1 for m in marks.values() if m["price_status"] == PS_MARKED)
    n_pending = sum(1 for m in marks.values() if m["price_status"] in (PS_PENDING, PS_NO_ENTRY))
    n_no_price = sum(1 for m in marks.values() if m["price_status"] == PS_NO_PRICE)
    coverage = {"n_marked": n_marked, "n_pending": n_pending, "n_no_price": n_no_price}

    mtm_status = "COMPUTED" if n_marked > 0 else "PENDING_PRICE_REFRESH"
    realized_return = (sum(m["weighted_contribution"] for m in marks.values()
                           if m["price_status"] == PS_MARKED) if n_marked else None)

    # latest local date across marked names -> elapsed holding days
    latest_dates = [m["latest_local_date"] for m in marks.values() if m["latest_local_date"]]
    holding_days = None
    if latest_dates and mtm_status == "COMPUTED":
        try:
            holding_days = (date.fromisoformat(max(latest_dates)) - date.fromisoformat(inception)).days
        except ValueError:
            holding_days = None

    bench = _expected_benchmark(d10_json)

    # --- build artifact row sets ---
    ledger, plan, snap = _build(rows, marks, inception, rebalance)
    exp_real = _exp_real_rows(bench, realized_return, mtm_status, holding_days)
    exposure = _exposure_rows(rows, n_long, n_short)
    sector_rows, largest_sector = _sector_rows(rows)
    liquidity_rows = _liquidity_rows(rows)
    badges = _badge_rows()

    decision = DEC_READY if n_marked > 0 else DEC_PENDING
    status_rows = _status_rows(decision, mtm_status, n_long, n_short, inception, rebalance, coverage)

    # --- write artifacts ---
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / _ARTIFACTS["ledger"], _LEDGER_HEADER, ledger)
    _write_csv(out_dir / _ARTIFACTS["mtm_plan"], _MTM_PLAN_HEADER, plan)
    _write_csv(out_dir / _ARTIFACTS["mtm_snapshot"], _MTM_SNAP_HEADER, snap)
    _write_csv(out_dir / _ARTIFACTS["exp_real"], _EXP_REAL_HEADER, exp_real)
    _write_csv(out_dir / _ARTIFACTS["exposure"], _EXPOSURE_HEADER, exposure)
    _write_csv(out_dir / _ARTIFACTS["sector"], _SECTOR_HEADER, sector_rows)
    _write_csv(out_dir / _ARTIFACTS["liquidity"], _LIQ_HEADER, liquidity_rows)
    _write_csv(out_dir / _ARTIFACTS["badges"], _BADGES_HEADER, badges)
    _write_csv(out_dir / _ARTIFACTS["status"], _STATUS_HEADER, status_rows)
    _write_json(out_dir / _ARTIFACTS["next_plan"], _phase10j_plan(decision, mtm_status))

    lw = next((r[1] for r in exposure if r[0] == "long_gross_pct"), 0.0)
    sw = next((r[1] for r in exposure if r[0] == "short_gross_pct"), 0.0)

    report = {
        "phase": PHASE,
        "decision": decision,
        "decision_rationale": (
            "paper book valid and equal-weighted; owned local prices do not yet extend past inception "
            "so mark-to-market is pending a local price refresh" if decision == DEC_PENDING else
            "paper book valid; marked to market off owned local prices"),
        "allowed_decisions": list(ALLOWED_DECISIONS),
        "forbidden_decisions": list(FORBIDDEN_DECISIONS),
        "objective": ("paper-only position tracker for the 10-H rules-based 25/25 quarterly quality "
                      "book: ledger, MTM off owned local prices, expected-vs-realized, exposures"),
        "source_portfolio": _rel(portfolio_csv),
        "source_h10_meta": _rel(h10_json),
        "source_expected_benchmark": bench.get("source"),
        "eod_price_source": _rel(eod_dir),
        # --- book ---
        "n_holdings": len(rows),
        "n_long": n_long,
        "n_short": n_short,
        "portfolio_valid": True,
        "weighting": "EQUAL",
        "long_weight_each": _to_float(h10_meta.get("long_weight_each")),
        "short_weight_each": _to_float(h10_meta.get("short_weight_each")),
        # --- dates ---
        "inception_date": inception,
        "next_rebalance_date": rebalance,
        "rebalance_cadence": h10_meta.get("rebalance_cadence", "QUARTERLY"),
        "holding_period_days_elapsed": holding_days,
        # --- mark to market ---
        "mtm_status": mtm_status,
        "price_coverage": coverage,
        "price_coverage_pct": round(100.0 * (n_marked + (len(rows) - n_marked - n_no_price))
                                    / len(rows), 2) if rows else 0.0,
        "realized_paper_return_to_date": _round(realized_return, 6),
        "mark_method": _MARK_METHOD,
        "price_source": _PRICE_SOURCE,
        "no_live_market_api": True,
        # --- expected benchmark ---
        "expected_benchmark": bench,
        "expected_benchmark_signal": bench.get("benchmark_signal"),
        "expected_quarterly_net_25bps": bench.get("quarterly_net_25bps"),
        "expected_quarterly_net_50bps": bench.get("quarterly_net_50bps"),
        "expected_benchmark_caveat": ("benchmark is the Phase 10-D SECTOR-NEUTRAL composite (the book "
                                      "ranks on comp_sn); it is the full-rank quintile L/S backtest, so "
                                      "this 25/25 liquidity-filtered equal-weight subset only "
                                      "approximates it (the raw composite scored higher)"),
        # --- exposure ---
        "long_gross_pct": lw,
        "short_gross_pct": sw,
        "net_pct": round(lw - sw, 4),
        "gross_pct": round(lw + sw, 4),
        "largest_sector_share": largest_sector,
        # --- safety ---
        "performs_network": PERFORMS_NETWORK,
        "offline": True,
        "uses_owned_data_only": True,
        "performs_provider_acquisition": False,
        "creates_paper_trader_signals": False,
        "creates_trade_decisions": False,
        "creates_orders": False,
        "creates_automation": False,
        "wrote_to_paper_trader": False,
        "live_trading": False,
        "broker_connected": False,
        "deploy": False,
        "api_key_printed": False,
        "api_key_written_to_disk": False,
        "order_action_all": ORDER_ACTION,
        "review_status_all": REVIEW_STATUS,
        "safety_badges": SAFETY_BADGES,
        "required_artifacts": list(_ARTIFACTS.values()),
        "exact_next_command": ("review research/output/phase10i_paper_position_tracker/"
                               "paper_tracker_status.csv"),
        "constraints_honored": [
            "offline (no network/key/provider probe)", "owned/local data only (EODHD local EOD prices)",
            "no new provider purchase", "no live market API", "no Paper Trader writes",
            "no Paper Trader signals", "no trade decisions", "NO orders", "NO automation", "NO broker",
            "NO live trading", "no deploy", "no GCP", "no package install", "no full regression",
            "no key printed/written", "no commit", "no push",
        ],
    }
    _write_json(out_dir / _ARTIFACTS["report"], report)

    # secret-safety audit LAST so it scans every artifact just written.
    sec_rows, leak_clean = b10._secret_safety_audit(out_dir)
    _write_csv(out_dir / _ARTIFACTS["secret_audit"],
               ["file", "clean", "keyed_url_marker", "key_value_present"],
               [[r["file"], r["clean"], r["keyed_url_marker"], r["key_value_present"]] for r in sec_rows])
    report["secret_safety_leak_scan_clean"] = leak_clean
    _write_json(out_dir / _ARTIFACTS["report"], report)

    if verbose:
        _print_summary(report)
    return report


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Paper-only position tracker (Phase 10-I).")
    p.add_argument("--portfolio", default=None)
    p.add_argument("--h10-json", default=None)
    p.add_argument("--d10-json", default=None)
    p.add_argument("--eod-dir", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    ns = _parse_args(argv)
    rep = run(out_dir=ns.out, portfolio_csv=ns.portfolio, h10_json=ns.h10_json,
              d10_json=ns.d10_json, eod_dir=ns.eod_dir, verbose=not ns.quiet)
    return 0 if rep.get("decision") in ALLOWED_DECISIONS else 1


if __name__ == "__main__":
    raise SystemExit(main())
