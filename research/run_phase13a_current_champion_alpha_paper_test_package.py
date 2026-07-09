"""Phase 13-A - Current Champion Alpha Paper-Test Package.

WHY THIS PHASE EXISTS
    Paid analyst-revision data is still pending (Phase 12-A ended NEEDS_CONTACT_SALES_OR_TRIAL).
    Rather than wait idle, this phase PACKAGES the alpha we already have - the modest but validated
    Phase 10-D sector-neutral quality composite `composite_sn` (long sector-neutral free-cash-flow-to
    -assets, short sector-neutral operating accruals; quarterly / 63-trading-day cross-sectional rank;
    IC t 2.665, net-25bps quarterly spread +0.00401) - into a disciplined, preview-only paper-test
    candidate list, conservative rules, a tracking framework, and a go/no-go scorecard.

    This is NOT a new alpha search, NOT a retune, NOT a reweighting. It does not use analyst-revision
    or any unavailable paid data. It reads the frozen Phase 10-L historical scored panel, takes the
    LATEST fully-scored cross-section (the panel's within-month cross-section unit), ranks by
    composite_sn, and emits a paper-test package. If OWNED local EOD prices extend to/just-before the
    signal date it also INITIALIZES entry prices for the covered names - purely off local data, no
    provider call. It writes ONLY metadata CSV/JSON to its own research/output directory. It creates NO
    Paper Trader signals, NO trade decisions, and NO orders. Every portfolio row keeps
    order_action = NO_ORDER.

CROSS-SECTION NOTE
    In the frozen panel the per-observation `rebalance_date` is a per-ticker daily event date, but the
    signal is standardized WITHIN a calendar month (`within_month_z`), so the honest cross-section unit
    is the calendar MONTH. The latest usable signal cross-section is the most recent calendar month with
    valid composite_sn scores; the reported signal date is the max rebalance_date inside that month.

ENTRY / MARK (owned local prices only)
    Prices come from research/data/eodhd/raw/eod_prices/<ticker>.json (owned EODHD daily OHLCV +
    adjusted_close). Paper entry is taken at/just-before the signal date (the price when the signal
    fired); the current mark is the latest LOCAL adjusted_close. A name is MARKED when local data
    extends PAST the entry date, otherwise PENDING_PRICE_REFRESH, or NO_LOCAL_PRICE when no local file
    exists. No live market API is ever called. SPY is not owned locally, so the benchmark plan falls
    back to an equal-weight-universe reference (documented, not hidden).

TERMINAL DECISIONS (allowed)
    CURRENT_ALPHA_READY_FOR_PAPER_TEST | CURRENT_ALPHA_PACKAGE_READY_PANEL_ONLY |
    CURRENT_ALPHA_NEEDS_FRESH_PRICES | CURRENT_ALPHA_REJECTED_DUE_STALENESS |
    BLOCKED_DATA_MISSING | BLOCKED_RUNNER_ERROR
    FORBIDDEN: LIVE_TRADING_READY, ORDER_READY, AUTOMATION_READY, PAPER_TRADER_READY,
    STRONG_ALPHA_FOUND_READY_FOR_REVIEW.

CONSTRAINTS HONORED
    Offline (no network/key/provider probe); owned/local data only; no new alpha search; no retune; no
    reweight; no analyst-revision/paid data; no Paper Trader writes; no Paper Trader signals; no trade
    decisions; NO orders; NO automation; NO broker; NO live trading; no deploy; no GCP; no package
    install; keys never printed or written; output is metadata only. No commit inside the runner.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------- #
# Self-contained stdlib helpers (vendored so Phase 13-A has ZERO third-party or
# cross-phase imports - it must run under a bare Windows Python with no numpy /
# pandas / research package on sys.path). Behaviour matches the phase8s IO
# helpers and the phase10b secret-audit / _finite helpers verbatim.
# --------------------------------------------------------------------------- #
# Only these env vars are scanned for accidental leakage (mirrors phase10b's
# REQUIRED_VISIBLE_KEYS + CONTEXT_ONLY_KEYS). This runner never reads or uses
# them - the audit just proves no key value landed in an output file.
_SECRET_ENV_VARS = ("EODHD_API_KEY", "FMP_API_KEY")


def _rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(_REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, allow_nan=False, default=str)
        fh.write("\n")


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow(["" if v is None else v for v in r])


def _read_csv_file(path: Path) -> List[Dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def _read_json(path: Path) -> Dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _finite(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def _secret_safety_audit(out_dir: Path) -> Tuple[List[Dict], bool]:
    """Scan every output file for keyed-URL markers or a live key value.

    Read-only over the runner's own out_dir; never prints or stores a key.
    """
    markers = ["apikey=", "api_token=", "token=", "api_key=", "&apikey", "?apikey", "&token"]
    present_values = []
    for env in _SECRET_ENV_VARS:
        v = os.environ.get(env)
        if isinstance(v, str) and v.strip():
            present_values.append(v.strip())
    rows: List[Dict] = []
    clean = True
    for p in sorted(out_dir.glob("*")):
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        low = text.lower()
        marker_hit = next((m for m in markers if m in low), "")
        value_hit = any(val in text for val in present_values)
        file_clean = not marker_hit and not value_hit
        clean = clean and file_clean
        rows.append({"file": p.name, "clean": file_clean,
                     "keyed_url_marker": marker_hit or "none", "key_value_present": value_hit})
    return rows, clean


PHASE = "13-A"
PERFORMS_NETWORK = False

# --- signal definition (fixed; NOT retuned here) ---
SIGNAL_COL = "composite_sn"
FCF_LEG_COL = "fcf_to_assets_sector_neutral_z"
ACC_LEG_COL = "operating_accruals_sector_neutral_z"
FCF_RAW_COL = "fcf_to_assets"
ACC_RAW_COL = "operating_accruals"

# --- book sizes / horizon (targets, not optimised) ---
TOP_N_SMALL = 25
TOP_N_LARGE = 50
BOTTOM_N = 25
HOLDING_DAYS_TRADING = 63           # quarterly holding horizon (trading days)
HOLDING_DAYS_CAL_APPROX = 92        # ~calendar-day equivalent for date arithmetic / staleness

# --- conservative risk suggestions ---
TOP25_WEIGHT_EACH = round(1.0 / TOP_N_SMALL, 6)     # 0.04
TOP50_WEIGHT_EACH = round(1.0 / TOP_N_LARGE, 6)     # 0.02
HARD_POSITION_CAP = 0.05                            # 5% max single-name suggestion
SECTOR_CONCENTRATION_WARN = 0.30                    # warn if a book's sector share > 30%
LIQUIDITY_FLOOR_PCTL = 0.25                         # warn names below the universe 25th pctile

# --- staleness thresholds (calendar days from signal to package date) ---
STALE_WARN_DAYS = 30                # older than ~a month -> warn
STALE_REJECT_DAYS = 120            # older than a full quarter horizon -> reject as basis for a NEW test

SIDE_LONG = "LONG"
SIDE_SHORT = "SHORT"
ORDER_ACTION = "NO_ORDER"
REVIEW_STATUS = "PAPER_REVIEW_ONLY"

SAFETY_BADGES = [
    "PREVIEW ONLY", "NO ORDERS", "NO BROKER", "MANUAL REVIEW", "NO AUTOMATION",
    "NO LIVE TRADING", "NO PAPER TRADER WRITES", "CREATES NO TRADE DECISIONS", "PAPER TEST ONLY",
]

# --- price status per holding ---
PS_MARKED = "MARKED"
PS_PENDING = "PENDING_PRICE_REFRESH"
PS_NO_PRICE = "NO_LOCAL_PRICE"
PS_NO_ENTRY = "NO_ENTRY_PRICE"

# --- terminal decisions ---
DEC_READY = "CURRENT_ALPHA_READY_FOR_PAPER_TEST"
DEC_PANEL_ONLY = "CURRENT_ALPHA_PACKAGE_READY_PANEL_ONLY"
DEC_NEEDS_PRICES = "CURRENT_ALPHA_NEEDS_FRESH_PRICES"
DEC_STALE = "CURRENT_ALPHA_REJECTED_DUE_STALENESS"
DEC_BLOCKED_DATA = "BLOCKED_DATA_MISSING"
DEC_BLOCKED_ERROR = "BLOCKED_RUNNER_ERROR"
ALLOWED_DECISIONS = (DEC_READY, DEC_PANEL_ONLY, DEC_NEEDS_PRICES, DEC_STALE,
                     DEC_BLOCKED_DATA, DEC_BLOCKED_ERROR)
FORBIDDEN_DECISIONS = (
    "LIVE_TRADING_READY", "ORDER_READY", "AUTOMATION_READY", "PAPER_TRADER_READY",
    "STRONG_ALPHA_FOUND_READY_FOR_REVIEW",
)

# --- paths ---
_DEF_PANEL = (_REPO_ROOT / "research" / "output"
              / "phase10l_historical_sector_neutral_scored_panel_reconstruction"
              / "historical_sector_neutral_scored_panel.csv")
_DEF_D10_JSON = (_REPO_ROOT / "research" / "output" / "phase10d_quarterly_quality_composite_validation"
                 / "phase10d_quarterly_quality_composite_validation.json")
_DEF_EOD_DIR = _REPO_ROOT / "research" / "data" / "eodhd" / "raw" / "eod_prices"
_DEFAULT_OUT = _REPO_ROOT / "research" / "output" / "phase13a_current_champion_alpha_paper_test_package"

_ARTIFACTS = {
    "report": "phase13a_current_champion_alpha_paper_test_package.json",
    "full": "current_alpha_full_ranked_universe.csv",
    "top25": "current_alpha_top25_candidates.csv",
    "top50": "current_alpha_top50_candidates.csv",
    "bottom25": "current_alpha_bottom25_avoid_list.csv",
    "sector": "current_alpha_sector_exposure.csv",
    "missing": "current_alpha_missing_data_report.csv",
    "port25": "current_alpha_paper_portfolio_top25.csv",
    "port50": "current_alpha_paper_portfolio_top50.csv",
    "tracking": "current_alpha_tracking_template.csv",
    "risk": "current_alpha_risk_limits.csv",
    "scorecard": "current_alpha_go_no_go_scorecard.csv",
    "secret_audit": "secret_safety_audit.csv",
}

_PRICE_SOURCE = "EODHD_LOCAL_EOD(adjusted_close)"
_MARK_METHOD = "adjusted_close ratio, entry at/just-before signal date vs latest local mark"


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


def _pct(x, n=4):
    v = _to_float(x)
    return None if v is None else round(v * 100.0, n)


def _pctile(sorted_vals: Sequence[float], q: float) -> Optional[float]:
    xs = [v for v in sorted_vals if _finite(v)]
    if not xs:
        return None
    xs = sorted(xs)
    idx = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return xs[idx]


def _add_cal_days(iso: str, days: int) -> Optional[str]:
    try:
        return date.fromordinal(date.fromisoformat(iso[:10]).toordinal() + days).isoformat()
    except (ValueError, TypeError):
        return None


def _days_between(a: str, b: str) -> Optional[int]:
    try:
        return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# Panel -> latest cross-section.
# --------------------------------------------------------------------------- #
def _latest_cross_section(panel_rows: List[Dict]) -> Tuple[List[Dict], str, str, int]:
    """Return (ranked rows of the latest month, signal_date, cross_section_month, n_months).

    The cross-section unit is the calendar MONTH (within_month_z). Dedup to one row per ticker,
    keeping the latest rebalance_date inside the month that has a valid composite_sn.
    """
    by_month: Dict[str, List[Dict]] = {}
    for r in panel_rows:
        rb = (r.get("rebalance_date") or "").strip()
        if len(rb) >= 7:
            by_month.setdefault(rb[:7], []).append(r)
    if not by_month:
        return [], "", "", 0
    months = sorted(by_month)
    latest_month = months[-1]
    cs = by_month[latest_month]
    by_ticker: Dict[str, Dict] = {}
    for r in sorted(cs, key=lambda x: (x.get("rebalance_date") or "")):
        if _to_float(r.get(SIGNAL_COL)) is not None and (r.get("ticker") or "").strip():
            by_ticker[r["ticker"].strip()] = r
    ranked = sorted(by_ticker.values(),
                    key=lambda r: (-_to_float(r.get(SIGNAL_COL)), r.get("ticker") or ""))
    signal_date = max((r.get("rebalance_date") or "" for r in cs), default=latest_month)
    return ranked, signal_date, latest_month, len(months)


def _sector_coverage(ranked: List[Dict]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in ranked:
        sec = (r.get("sector") or "Unknown").strip() or "Unknown"
        out[sec] = out.get(sec, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


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
    out.sort(key=lambda t: t[0])
    return out or None


def _price_at_or_before(series: List[Tuple[str, float]], iso_dt: str) -> Optional[Tuple[str, float]]:
    hit = None
    for d, px in series:
        if d <= iso_dt:
            hit = (d, px)
        else:
            break
    return hit


def _mark_one(side: str, series: Optional[List[Tuple[str, float]]], signal_date: str) -> Dict:
    res = {"entry_price": None, "entry_date": None, "current_price": None, "current_date": None,
           "paper_return": None, "signed_return": None, "price_status": PS_NO_PRICE,
           "latest_local_date": None, "local_file_present": False}
    if not series:
        return res
    res["local_file_present"] = True
    res["latest_local_date"] = series[-1][0]
    entry = _price_at_or_before(series, signal_date)
    if entry is None:
        res["price_status"] = PS_NO_ENTRY
        return res
    res["entry_date"], res["entry_price"] = entry
    latest_date, latest_px = series[-1]
    res["current_date"], res["current_price"] = latest_date, latest_px
    if latest_date <= entry[0]:
        res["price_status"] = PS_PENDING
        return res
    raw_ret = (latest_px / entry[1]) - 1.0 if entry[1] else None
    if raw_ret is None:
        res["price_status"] = PS_NO_ENTRY
        return res
    res["paper_return"] = raw_ret
    res["signed_return"] = raw_ret if side == SIDE_LONG else -raw_ret
    res["price_status"] = PS_MARKED
    return res


# --------------------------------------------------------------------------- #
# Expected benchmark from Phase 10-D (the confirmed composite_sn).
# --------------------------------------------------------------------------- #
def _expected_benchmark(d10_json: Path) -> Dict:
    meta = _read_json(d10_json) if d10_json.exists() else {}
    sr = meta.get("signal_results") or []
    chosen = None
    for s in sr:
        if (s.get("signal") or s.get("name") or "").strip().lower() == SIGNAL_COL:
            chosen = s
            break
    if chosen is None:
        return {"available": False, "source": _rel(d10_json) if d10_json.exists() else None,
                "benchmark_signal": None, "ic_t_63d": None, "quarterly_net_25bps": None,
                "quarterly_net_50bps": None, "quarterly_turnover": None}
    return {
        "available": True,
        "source": _rel(d10_json),
        "benchmark_signal": SIGNAL_COL,
        "ic_t_63d": _to_float(chosen.get("ic_t_63d")),
        "quarterly_net_25bps": _to_float(chosen.get("quarterly_net_25bps")),
        "quarterly_net_50bps": _to_float(chosen.get("quarterly_net_50bps")),
        "quarterly_turnover": _to_float(chosen.get("quarterly_turnover")),
    }


# --------------------------------------------------------------------------- #
# Candidate / portfolio row builders.
# --------------------------------------------------------------------------- #
def _bucket(rank: int, n_total: int) -> str:
    if rank <= TOP_N_SMALL:
        return "TOP25"
    if rank <= TOP_N_LARGE:
        return "TOP50"
    if rank > n_total - BOTTOM_N:
        return "BOTTOM25"
    return "MIDDLE"


_FULL_HEADER = ["rank", "ticker", "sector", "cohort", "signal_bucket", SIGNAL_COL,
                "fcf_leg_z", "accruals_leg_z", "fcf_to_assets", "operating_accruals",
                "liquidity_proxy", "has_local_price"]
_CAND_HEADER = ["rank", "ticker", "sector", SIGNAL_COL, "fcf_leg_z", "accruals_leg_z",
                "liquidity_proxy", "has_local_price"]
_AVOID_HEADER = ["rank_from_bottom", "ticker", "sector", SIGNAL_COL, "fcf_leg_z", "accruals_leg_z",
                 "liquidity_proxy", "has_local_price", "note"]


def _full_rows(ranked: List[Dict], has_px, n_total: int) -> List[List]:
    out = []
    for i, r in enumerate(ranked, start=1):
        out.append([i, r.get("ticker"), r.get("sector"), r.get("cohort"), _bucket(i, n_total),
                    _round(r.get(SIGNAL_COL), 6), _round(r.get(FCF_LEG_COL), 6),
                    _round(r.get(ACC_LEG_COL), 6), _round(r.get(FCF_RAW_COL), 6),
                    _round(r.get(ACC_RAW_COL), 6), _round(r.get("liquidity_proxy"), 2),
                    has_px(r.get("ticker"))])
    return out


def _cand_rows(ranked: List[Dict], has_px) -> List[List]:
    out = []
    for i, r in enumerate(ranked, start=1):
        out.append([i, r.get("ticker"), r.get("sector"), _round(r.get(SIGNAL_COL), 6),
                    _round(r.get(FCF_LEG_COL), 6), _round(r.get(ACC_LEG_COL), 6),
                    _round(r.get("liquidity_proxy"), 2), has_px(r.get("ticker"))])
    return out


def _avoid_rows(bottom_asc: List[Dict], has_px) -> List[List]:
    out = []
    for i, r in enumerate(bottom_asc, start=1):
        out.append([i, r.get("ticker"), r.get("sector"), _round(r.get(SIGNAL_COL), 6),
                    _round(r.get(FCF_LEG_COL), 6), _round(r.get(ACC_LEG_COL), 6),
                    _round(r.get("liquidity_proxy"), 2), has_px(r.get("ticker")),
                    "AVOID / short-only diagnostic (NOT a live recommendation)"])
    return out


_SECTOR_HEADER = ["book", "sector", "n_names", "weight_pct", "flag"]


def _sector_exposure_rows(books: Dict[str, List[Dict]]) -> Tuple[List[List], Dict[str, float]]:
    out, largest = [], {}
    for book, rows in books.items():
        n = len(rows)
        agg: Dict[str, int] = {}
        for r in rows:
            sec = (r.get("sector") or "Unknown").strip() or "Unknown"
            agg[sec] = agg.get(sec, 0) + 1
        book_largest = 0.0
        for sec, cnt in sorted(agg.items(), key=lambda kv: (-kv[1], kv[0])):
            share = round(100.0 * cnt / n, 4) if n else 0.0
            book_largest = max(book_largest, share)
            flag = "CONCENTRATED" if share > SECTOR_CONCENTRATION_WARN * 100 else "OK"
            out.append([book, sec, cnt, share, flag])
        largest[book] = round(book_largest, 4)
    return out, largest


_MISSING_HEADER = ["issue", "scope", "n_names", "tickers", "note"]


def _missing_rows(ranked: List[Dict], top50: List[Dict], top25: List[Dict], bottom25: List[Dict],
                  has_px, liq_floor: Optional[float], dropped_no_composite: List[str]) -> List[List]:
    def _cap(names: List[str], k: int = 40) -> str:
        return ";".join(names[:k]) + (f" (+{len(names) - k} more)" if len(names) > k else "")

    out: List[List] = []
    unk = [r.get("ticker") for r in top50 if (r.get("sector") or "Unknown").strip() in ("", "Unknown")]
    out.append(["UNKNOWN_SECTOR", "top50", len(unk), _cap(unk),
                "sector metadata unmapped (owned-metadata gap carried from 10-F); sector-neutrality "
                "and sector caps are approximate for these names"])
    for label, rows in (("top25", top25), ("top50", top50), ("bottom25", bottom25)):
        nopx = [r.get("ticker") for r in rows if not has_px(r.get("ticker"))]
        out.append(["NO_LOCAL_PRICE", label, len(nopx), _cap(nopx),
                    "no owned local EOD file; entry price cannot be initialized until prices are "
                    "refreshed (no provider call is made here)"])
    if liq_floor is not None:
        lowliq = [r.get("ticker") for r in top50
                  if (_to_float(r.get("liquidity_proxy")) or 0.0) < liq_floor]
        out.append(["LOW_LIQUIDITY", "top50", len(lowliq), _cap(lowliq),
                    f"liquidity_proxy below universe {int(LIQUIDITY_FLOOR_PCTL*100)}th pctile "
                    f"({_round(liq_floor,2)}); size down or exclude"])
    out.append(["MISSING_COMPOSITE_LEG", "universe", len(dropped_no_composite),
                _cap(dropped_no_composite),
                "names in the latest month with no valid composite_sn were excluded from ranking"])
    return out


_PORT_HEADER = ["ticker", "side", "target_weight", "sector", "signal_composite_sn", "signal_date",
                "price_source", "entry_reference_date", "entry_price", "current_price",
                "current_price_date", "paper_return_pct", "price_status", "order_action",
                "review_status"]


def _portfolio_rows(book: List[Dict], weight_each: float, marks: Dict[str, Dict],
                    signal_date: str) -> List[List]:
    out = []
    for r in book:
        tk = r.get("ticker")
        m = marks.get(tk, {})
        out.append([tk, SIDE_LONG, weight_each, r.get("sector"), _round(r.get(SIGNAL_COL), 6),
                    signal_date, _PRICE_SOURCE, m.get("entry_date"), _round(m.get("entry_price"), 4),
                    _round(m.get("current_price"), 4), m.get("current_date"),
                    _pct(m.get("paper_return")), m.get("price_status", PS_NO_PRICE),
                    ORDER_ACTION, REVIEW_STATUS])
    return out


_TRACK_HEADER = ["ticker", "side", "sector", "signal_date", "paper_test_start_date",
                 "entry_price", "benchmark_spy", "benchmark_ew_universe",
                 "chk_1w_return", "chk_1w_bench_rel", "chk_1m_return", "chk_1m_bench_rel",
                 "chk_2m_return", "chk_2m_bench_rel", "chk_63d_return", "chk_63d_bench_rel",
                 "max_drawdown", "hit_win_loss", "sector_attribution"]


def _tracking_rows(book: List[Dict], marks: Dict[str, Dict], signal_date: str,
                   start_date: str, spy_available: bool) -> List[List]:
    out = []
    spy_cell = "" if not spy_available else "SPY_LOCAL"
    for r in book:
        tk = r.get("ticker")
        m = marks.get(tk, {})
        out.append([tk, SIDE_LONG, r.get("sector"), signal_date, start_date,
                    _round(m.get("entry_price"), 4), spy_cell, "EW_UNIVERSE_LOCAL",
                    "", "", "", "", "", "", "", "", "", "", ""])
    # portfolio-level summary row (placeholders for the equal-weight top-N book)
    out.append(["_EW_TOP%d_PORTFOLIO" % len(book), SIDE_LONG, "ALL", signal_date, start_date,
                "", spy_cell, "EW_UNIVERSE_LOCAL", "", "", "", "", "", "", "", "", "", "", ""])
    return out


_RISK_HEADER = ["limit", "value", "enforcement", "note"]


def _risk_rows(liq_floor: Optional[float], next_rebalance: Optional[str]) -> List[List]:
    return [
        ["PREVIEW ONLY", "YES", "HARD", "candidate list only; nothing is executed"],
        ["NO ORDERS", "CONFIRMED", "HARD", "runner creates no orders; order_action=NO_ORDER on every row"],
        ["NO BROKER", "CONFIRMED", "HARD", "no broker connection of any kind"],
        ["MANUAL REVIEW", "REQUIRED", "HARD", "a human must review before any action outside this tool"],
        ["NO AUTOMATION", "CONFIRMED", "HARD", "no scheduling / no automated re-run"],
        ["NO LIVE TRADING", "CONFIRMED", "HARD", "this alpha is modest; paper review only, not live"],
        ["rebalance_cadence", "QUARTERLY", "TARGET", "re-rank at the next quarter boundary"],
        ["holding_horizon_trading_days", HOLDING_DAYS_TRADING, "TARGET", "63 trading-day hold"],
        ["next_rebalance_target", next_rebalance or "PENDING", "TARGET",
         "approx signal_date + one quarter (63 trading days ~ 92 calendar days)"],
        ["weighting", "EQUAL_WEIGHT_LONG_ONLY", "TARGET", "top25 and top50 books are equal-weight"],
        ["max_position_size_top25", TOP25_WEIGHT_EACH, "SUGGESTION", "1/25 equal weight"],
        ["max_position_size_top50", TOP50_WEIGHT_EACH, "SUGGESTION", "1/50 equal weight"],
        ["hard_position_cap", HARD_POSITION_CAP, "SUGGESTION", "never exceed 5% in a single name"],
        ["sector_concentration_warn", SECTOR_CONCENTRATION_WARN, "WARNING",
         "flag / trim any book sector share above 30%"],
        ["liquidity_floor_proxy", _round(liq_floor, 2) if liq_floor is not None else "N/A", "WARNING",
         f"names below the universe {int(LIQUIDITY_FLOOR_PCTL*100)}th pctile dollar-volume proxy: "
         "size down or exclude"],
        ["no_averaging_down", "ENFORCED", "RULE", "do not add to a losing paper position"],
        ["long_short_diagnostic", "DIAGNOSTIC_ONLY", "RULE",
         "top25 long / bottom25 short spread is a diagnostic, NOT a live book"],
        ["no_live_trading_recommendation", "CONFIRMED", "RULE",
         "nothing here is a recommendation to trade real capital"],
    ]


_SCORECARD_HEADER = ["criterion", "status", "value", "threshold", "note"]


def _scorecard_rows(*, panel_ok: bool, n_valid: int, n_total_month: int, days_stale: Optional[int],
                    px_cov_top25: int, px_cov_top50: int, largest_sector: Dict[str, float],
                    bench: Dict, spy_available: bool) -> Tuple[List[List], str]:
    rows: List[List] = []

    def add(crit, status, value, thr, note):
        rows.append([crit, status, value, thr, note])

    add("panel_loaded", "PASS" if panel_ok else "FAIL", panel_ok, "required",
        "frozen 10-L historical scored panel")
    add("composite_sn_coverage", "PASS" if n_valid > 0 else "FAIL",
        f"{n_valid}/{n_total_month}", ">0", "valid composite_sn in the latest month cross-section")

    if days_stale is None:
        fresh = "WARN"
    elif days_stale > STALE_REJECT_DAYS:
        fresh = "FAIL"
    elif days_stale > STALE_WARN_DAYS:
        fresh = "WARN"
    else:
        fresh = "PASS"
    add("signal_freshness", fresh, f"{days_stale}d" if days_stale is not None else "unknown",
        f"warn>{STALE_WARN_DAYS}d, reject>{STALE_REJECT_DAYS}d",
        "calendar days from signal date to package date (freshest owned price date)")

    add("price_coverage_top25", "PASS" if px_cov_top25 >= TOP_N_SMALL else
        ("WARN" if px_cov_top25 > 0 else "FAIL"),
        f"{px_cov_top25}/{TOP_N_SMALL}", "full=25", "owned local EOD entry prices available")
    add("price_coverage_top50", "PASS" if px_cov_top50 >= TOP_N_LARGE else
        ("WARN" if px_cov_top50 > 0 else "FAIL"),
        f"{px_cov_top50}/{TOP_N_LARGE}", "full=50", "owned local EOD entry prices available")

    big25 = largest_sector.get("TOP25", 0.0)
    add("sector_diversification_top25",
        "WARN" if big25 > SECTOR_CONCENTRATION_WARN * 100 else "PASS",
        f"{big25}%", f"<={int(SECTOR_CONCENTRATION_WARN*100)}%",
        "largest sector share in the top-25 book (Unknown-sector caveat applies)")

    add("benchmark_spy_local", "WARN" if not spy_available else "PASS",
        spy_available, "SPY.json present",
        "SPY not owned locally -> benchmark falls back to equal-weight universe")

    ic_t = bench.get("ic_t_63d")
    add("historical_ic_t_63d", "PASS" if (ic_t or 0) >= 2.0 else "WARN", ic_t, ">=2.0 (modest)",
        "Phase 10-D validated t-stat; modest edge, not strong")
    add("historical_net25_spread", "PASS" if (bench.get("quarterly_net_25bps") or 0) > 0 else "WARN",
        bench.get("quarterly_net_25bps"), ">0", "quarterly net-25bps spread (approx quintile L/S)")

    statuses = [r[1] for r in rows]
    if "FAIL" in statuses:
        overall = "NO_GO_BLOCKED"
    elif "WARN" in statuses:
        overall = "GO_PAPER_ONLY_WITH_CAVEATS_NOT_LIVE"
    else:
        overall = "GO_PAPER_ONLY_NOT_LIVE"
    add("OVERALL_RECOMMENDATION", overall, overall, "paper only",
        "this is a preview/paper-test recommendation ONLY; never a live-trading signal")
    return rows, overall


# --------------------------------------------------------------------------- #
# Run.
# --------------------------------------------------------------------------- #
def _print_summary(rep: Dict) -> None:
    print(
        f"[{PHASE}] decision={rep['decision']} | signal_date={rep['signal_date']} "
        f"month={rep['cross_section_month']} | ranked={rep['n_ranked']} "
        f"(valid_cs={rep['n_valid_composite']}) | stale={rep['days_since_signal']}d "
        f"warn={rep['stale_warning']} | px_top25={rep['price_coverage']['top25']}/25 "
        f"px_top50={rep['price_coverage']['top50']}/50 | overall={rep['go_no_go']} | "
        f"orders={rep['creates_orders']} automation={rep['creates_automation']} "
        f"wrote_pt={rep['wrote_to_paper_trader']} leak_clean={rep['secret_safety_leak_scan_clean']}"
    )


def _finish_blocker(out_dir: Path, decision: str, reason: str, repro: str, verbose: bool) -> Dict:
    rep = {
        "phase": PHASE, "decision": decision, "allowed_decisions": list(ALLOWED_DECISIONS),
        "forbidden_decisions": list(FORBIDDEN_DECISIONS), "reason": reason,
        "signal_date": None, "cross_section_month": None, "n_ranked": 0, "n_valid_composite": 0,
        "days_since_signal": None, "stale_warning": None,
        "price_coverage": {"top25": 0, "top50": 0, "bottom25": 0},
        "paper_tracking_initialized": False, "go_no_go": "NO_GO_BLOCKED",
        "performs_network": PERFORMS_NETWORK, "offline": True, "uses_owned_data_only": True,
        "creates_orders": False, "creates_automation": False, "creates_broker_connection": False,
        "creates_paper_trader_signals": False, "creates_trade_decisions": False,
        "wrote_to_paper_trader": False, "live_trading": False, "deploy": False,
        "api_key_printed": False, "api_key_written_to_disk": False,
        "secret_safety_leak_scan_clean": True, "safety_badges": SAFETY_BADGES,
        "exact_next_command": repro,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / _ARTIFACTS["report"], rep)
    if verbose:
        _print_summary(rep)
    return rep


def run(out_dir: Optional[Path] = None, *, panel_csv: Optional[Path] = None,
        d10_json: Optional[Path] = None, eod_dir: Optional[Path] = None,
        as_of: Optional[str] = None, verbose: bool = True) -> Dict:
    out_dir = Path(out_dir) if out_dir else _DEFAULT_OUT
    panel_csv = Path(panel_csv) if panel_csv else _DEF_PANEL
    d10_json = Path(d10_json) if d10_json else _DEF_D10_JSON
    eod_dir = Path(eod_dir) if eod_dir else _DEF_EOD_DIR

    if not panel_csv.exists():
        return _finish_blocker(
            out_dir, DEC_BLOCKED_DATA,
            f"frozen scored panel not found at {_rel(panel_csv)}",
            f"python research/run_phase13a_current_champion_alpha_paper_test_package.py "
            f"--panel \"{_rel(panel_csv)}\"", verbose)

    panel_rows = _read_csv_file(panel_csv)
    if not panel_rows:
        return _finish_blocker(
            out_dir, DEC_BLOCKED_DATA, f"panel at {_rel(panel_csv)} is empty",
            "python research/run_phase13a_current_champion_alpha_paper_test_package.py", verbose)

    ranked, signal_date, month, n_months = _latest_cross_section(panel_rows)
    if not ranked:
        return _finish_blocker(
            out_dir, DEC_BLOCKED_DATA, "no valid composite_sn rows in the latest month cross-section",
            "python research/run_phase13a_current_champion_alpha_paper_test_package.py", verbose)

    # names in the latest month with no valid composite (for the missing-data report)
    latest_month_rows = [r for r in panel_rows if (r.get("rebalance_date") or "")[:7] == month]
    ranked_tickers = {r.get("ticker") for r in ranked}
    dropped_no_composite = sorted({(r.get("ticker") or "") for r in latest_month_rows
                                   if (r.get("ticker") or "") and r.get("ticker") not in ranked_tickers})

    n_total = len(ranked)
    top25 = ranked[:TOP_N_SMALL]
    top50 = ranked[:TOP_N_LARGE]
    bottom25 = sorted(ranked[-BOTTOM_N:], key=lambda r: (_to_float(r.get(SIGNAL_COL)), r.get("ticker")))

    # local price availability
    series_cache: Dict[str, Optional[List[Tuple[str, float]]]] = {}

    def _series(tk):
        if tk not in series_cache:
            series_cache[tk] = _load_eod_series(eod_dir, tk)
        return series_cache[tk]

    def has_px(tk) -> bool:
        return _series(tk) is not None

    # package (as-of) date = explicit override, else freshest owned price date across candidates,
    # else today. Anchoring to owned data keeps the run reproducible and honest about staleness.
    cand_latest_dates = [s[-1][0] for tk in ranked_tickers
                         if (s := _series(tk)) is not None]
    latest_local_price_date = max(cand_latest_dates) if cand_latest_dates else None
    if as_of:
        package_date = as_of[:10]
    elif latest_local_price_date:
        package_date = latest_local_price_date
    else:
        package_date = date.today().isoformat()

    days_since_signal = _days_between(signal_date, package_date)
    stale_warning = bool(days_since_signal is not None and days_since_signal > STALE_WARN_DAYS)
    stale_reject = bool(days_since_signal is not None and days_since_signal > STALE_REJECT_DAYS)

    # mark every ranked name at the signal date (entry) vs latest local (current)
    marks: Dict[str, Dict] = {}
    for r in ranked:
        tk = r.get("ticker")
        side = SIDE_LONG  # ranking book is long-only; sign is handled per-book
        marks[tk] = _mark_one(side, _series(tk), signal_date)

    px_cov_top25 = sum(1 for r in top25 if has_px(r.get("ticker")))
    px_cov_top50 = sum(1 for r in top50 if has_px(r.get("ticker")))
    px_cov_bottom25 = sum(1 for r in bottom25 if has_px(r.get("ticker")))
    any_entry_initialized = any(marks[r.get("ticker")]["entry_price"] is not None for r in ranked)

    # liquidity floor over the ranked universe
    liq_vals = sorted(v for r in ranked if (v := _to_float(r.get("liquidity_proxy"))) is not None)
    liq_floor = _pctile(liq_vals, LIQUIDITY_FLOOR_PCTL)

    bench = _expected_benchmark(d10_json)
    spy_available = (eod_dir / "SPY.json").exists()

    # equal-weight universe realized benchmark (covered names only; informational)
    covered_returns = [marks[r.get("ticker")]["paper_return"] for r in ranked
                       if marks[r.get("ticker")]["price_status"] == PS_MARKED]
    ew_universe_return = (sum(covered_returns) / len(covered_returns)) if covered_returns else None
    top25_covered = [marks[r.get("ticker")]["paper_return"] for r in top25
                     if marks[r.get("ticker")]["price_status"] == PS_MARKED]
    ew_top25_return = (sum(top25_covered) / len(top25_covered)) if top25_covered else None

    next_rebalance = _add_cal_days(signal_date, HOLDING_DAYS_CAL_APPROX)

    # ---- decision ----
    if stale_reject:
        decision = DEC_STALE
    elif any_entry_initialized:
        decision = DEC_READY
    elif latest_local_price_date is None:
        decision = DEC_NEEDS_PRICES
    else:
        decision = DEC_PANEL_ONLY
    paper_tracking_initialized = decision == DEC_READY

    # ---- build artifacts ----
    books = {"TOP25": top25, "TOP50": top50, "BOTTOM25": bottom25}
    sector_rows, largest_sector = _sector_exposure_rows(books)
    scorecard_rows, overall = _scorecard_rows(
        panel_ok=True, n_valid=n_total, n_total_month=len({r.get("ticker") for r in latest_month_rows}),
        days_stale=days_since_signal, px_cov_top25=px_cov_top25, px_cov_top50=px_cov_top50,
        largest_sector=largest_sector, bench=bench, spy_available=spy_available)

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / _ARTIFACTS["full"], _FULL_HEADER, _full_rows(ranked, has_px, n_total))
    _write_csv(out_dir / _ARTIFACTS["top25"], _CAND_HEADER, _cand_rows(top25, has_px))
    _write_csv(out_dir / _ARTIFACTS["top50"], _CAND_HEADER, _cand_rows(top50, has_px))
    _write_csv(out_dir / _ARTIFACTS["bottom25"], _AVOID_HEADER, _avoid_rows(bottom25, has_px))
    _write_csv(out_dir / _ARTIFACTS["sector"], _SECTOR_HEADER, sector_rows)
    _write_csv(out_dir / _ARTIFACTS["missing"], _MISSING_HEADER,
               _missing_rows(ranked, top50, top25, bottom25, has_px, liq_floor, dropped_no_composite))
    _write_csv(out_dir / _ARTIFACTS["port25"], _PORT_HEADER,
               _portfolio_rows(top25, TOP25_WEIGHT_EACH, marks, signal_date))
    _write_csv(out_dir / _ARTIFACTS["port50"], _PORT_HEADER,
               _portfolio_rows(top50, TOP50_WEIGHT_EACH, marks, signal_date))
    _write_csv(out_dir / _ARTIFACTS["tracking"], _TRACK_HEADER,
               _tracking_rows(top25, marks, signal_date, signal_date, spy_available))
    _write_csv(out_dir / _ARTIFACTS["risk"], _RISK_HEADER, _risk_rows(liq_floor, next_rebalance))
    _write_csv(out_dir / _ARTIFACTS["scorecard"], _SCORECARD_HEADER, scorecard_rows)

    report = {
        "phase": PHASE,
        "decision": decision,
        "decision_rationale": {
            DEC_READY: ("panel loaded; latest cross-section ranked; owned local EOD prices initialize "
                        "entry prices for the covered names (partial coverage + staleness noted)"),
            DEC_PANEL_ONLY: "panel packaged and ranked, but no entry prices were initialized",
            DEC_NEEDS_PRICES: "package ready but no owned local prices are available to initialize entries",
            DEC_STALE: (f"latest signal is {days_since_signal}d old (> {STALE_REJECT_DAYS}d); a fresh "
                        "rebalance is required before a new paper test"),
        }.get(decision, "see reason"),
        "allowed_decisions": list(ALLOWED_DECISIONS),
        "forbidden_decisions": list(FORBIDDEN_DECISIONS),
        "objective": ("package the validated champion composite_sn alpha into a preview-only paper-test "
                      "candidate list, conservative rules, tracking framework, and go/no-go scorecard"),
        "champion_signal": SIGNAL_COL,
        "champion_definition": ("sector-neutral equal-weight composite = within-month z of oriented "
                                "sector-neutral fcf_to_assets (+) plus oriented sector-neutral "
                                "operating_accruals (-, Sloan-negated); quarterly / 63-trading-day rank"),
        "not_this_phase": ["new alpha search", "factor retune", "weight optimization",
                           "analyst-revision/paid data", "Paper Trader modification", "orders",
                           "automation", "live trading"],
        # --- sources ---
        "source_panel": _rel(panel_csv),
        "source_expected_benchmark": bench.get("source"),
        "eod_price_source": _rel(eod_dir),
        # --- signal identification (req 3) ---
        "signal_date": signal_date,
        "cross_section_month": month,
        "cross_section_unit": "calendar_month (within_month_z)",
        "n_months_in_panel": n_months,
        "n_ranked": n_total,
        "n_valid_composite": n_total,
        "n_latest_month_tickers": len({r.get("ticker") for r in latest_month_rows}),
        "n_dropped_no_composite": len(dropped_no_composite),
        "sector_coverage": _sector_coverage(ranked),
        "package_date": package_date,
        "latest_local_price_date": latest_local_price_date,
        "days_since_signal": days_since_signal,
        "stale_warning": stale_warning,
        "stale_rejected": stale_reject,
        "stale_thresholds": {"warn_days": STALE_WARN_DAYS, "reject_days": STALE_REJECT_DAYS},
        "signal_is_stale": stale_warning,
        # --- prices (req 3/7/8) ---
        "fresh_prices_available_locally": bool(latest_local_price_date is not None),
        "price_coverage": {"top25": px_cov_top25, "top50": px_cov_top50, "bottom25": px_cov_bottom25},
        "paper_tracking_initialized": paper_tracking_initialized,
        "price_source": _PRICE_SOURCE,
        "mark_method": _MARK_METHOD,
        "no_live_market_api": True,
        "spy_benchmark_available_locally": spy_available,
        # --- book construction (targets, not optimized) ---
        "book_sizes": {"top25": len(top25), "top50": len(top50), "bottom25_avoid": len(bottom25)},
        "weighting": "EQUAL_WEIGHT_LONG_ONLY",
        "top25_weight_each": TOP25_WEIGHT_EACH,
        "top50_weight_each": TOP50_WEIGHT_EACH,
        "holding_horizon_trading_days": HOLDING_DAYS_TRADING,
        "rebalance_cadence": "QUARTERLY",
        "next_rebalance_target": next_rebalance,
        "largest_sector_share_by_book": largest_sector,
        # --- benchmark (req 6) ---
        "expected_benchmark": bench,
        "expected_benchmark_caveat": ("Phase 10-D composite_sn is a full-rank sector-neutral quintile "
                                      "L/S backtest; a 25/50-name equal-weight long-only book only "
                                      "approximates it. SPY unavailable locally -> equal-weight-universe "
                                      "reference used instead."),
        "informational_realized": {
            "ew_universe_return_signal_to_latest": _round(ew_universe_return, 6),
            "ew_top25_return_signal_to_latest": _round(ew_top25_return, 6),
            "ew_top25_excess_vs_universe": _round(
                (ew_top25_return - ew_universe_return)
                if (ew_top25_return is not None and ew_universe_return is not None) else None, 6),
            "note": ("realized over signal_date->latest local price date off owned prices only, covered "
                     "names only; illustrative, NOT a validated backtest result"),
        },
        # --- go/no-go ---
        "go_no_go": overall,
        "go_no_go_note": "paper-only recommendation; never a live-trading signal",
        # --- safety ---
        "performs_network": PERFORMS_NETWORK,
        "offline": True,
        "uses_owned_data_only": True,
        "performs_provider_acquisition": False,
        "uses_paid_data": False,
        "uses_analyst_revision_data": False,
        "creates_paper_trader_signals": False,
        "creates_trade_decisions": False,
        "creates_orders": False,
        "creates_automation": False,
        "creates_broker_connection": False,
        "wrote_to_paper_trader": False,
        "live_trading": False,
        "deploy": False,
        "api_key_printed": False,
        "api_key_written_to_disk": False,
        "order_action_all": ORDER_ACTION,
        "review_status_all": REVIEW_STATUS,
        "safety_badges": SAFETY_BADGES,
        "required_artifacts": list(_ARTIFACTS.values()),
        "exact_next_command": ("review research/output/phase13a_current_champion_alpha_paper_test_"
                               "package/current_alpha_go_no_go_scorecard.csv"),
        "constraints_honored": [
            "offline (no network/key/provider probe)", "owned/local data only",
            "no new alpha search", "no factor retune", "no reweight",
            "no analyst-revision/paid data", "no Paper Trader writes", "no Paper Trader signals",
            "no trade decisions", "NO orders", "NO automation", "NO broker", "NO live trading",
            "no deploy", "no GCP", "no package install", "no key printed/written",
            "no commit inside runner", "no push",
        ],
    }
    _write_json(out_dir / _ARTIFACTS["report"], report)

    # secret-safety audit LAST so it scans every artifact just written.
    sec_rows, leak_clean = _secret_safety_audit(out_dir)
    _write_csv(out_dir / _ARTIFACTS["secret_audit"],
               ["file", "clean", "keyed_url_marker", "key_value_present"],
               [[r["file"], r["clean"], r["keyed_url_marker"], r["key_value_present"]] for r in sec_rows])
    report["secret_safety_leak_scan_clean"] = leak_clean
    _write_json(out_dir / _ARTIFACTS["report"], report)

    if verbose:
        _print_summary(report)
    return report


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Current champion alpha paper-test package (Phase 13-A).")
    p.add_argument("--panel", default=None)
    p.add_argument("--d10-json", default=None)
    p.add_argument("--eod-dir", default=None)
    p.add_argument("--as-of", default=None, help="package/staleness anchor date YYYY-MM-DD "
                                                 "(default: freshest owned price date)")
    p.add_argument("--out", default=None)
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    ns = _parse_args(argv)
    try:
        rep = run(out_dir=ns.out, panel_csv=ns.panel, d10_json=ns.d10_json, eod_dir=ns.eod_dir,
                  as_of=ns.as_of, verbose=not ns.quiet)
    except Exception as exc:  # noqa: BLE001 - surface a repro, never a crash
        out_dir = Path(ns.out) if ns.out else _DEFAULT_OUT
        rep = _finish_blocker(
            out_dir, DEC_BLOCKED_ERROR, f"unhandled error: {type(exc).__name__}: {exc}",
            "python research/run_phase13a_current_champion_alpha_paper_test_package.py",
            not ns.quiet)
    return 0 if rep.get("decision") in ALLOWED_DECISIONS else 1


if __name__ == "__main__":
    raise SystemExit(main())
