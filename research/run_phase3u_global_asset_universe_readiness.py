"""Phase 3-U - Global Cross-Asset ETF Proxy Universe Readiness Gate.

This is a research-only READINESS and DATA-PLANNING gate. While the Phase 3-T earnings collection
is blocked by the provider's daily quota, Phase 3-U plans the next *orthogonal* data expansion:
moving the research model from a US-equity-only universe toward a global, multi-asset view using
liquid, free ETF price proxies (broad equity, international equity, rates, credit, commodities,
currencies, volatility).

Phase 3-U answers four questions WITHOUT training anything, WITHOUT computing predictions, and
WITHOUT calling any provider or network:

  1. What is a practical global cross-asset proxy universe (ticker, asset class, region, role)?
  2. Which of those proxies already have a *local* daily price history (repo or the allowed
     read-only D: price panel), and what is each one's date coverage?
  3. For the proxies we do NOT have locally, what exact price data would be required (fields,
     history start, frequency, point-in-time rule, file format) - described only, never fetched?
  4. Given current local coverage, are we ready to build a cross-asset feature factory, or do we
     first need to acquire local global ETF price data?

It reads only already-written local artifacts (the Phase 3-T / Phase 3-M collection progress and a
few prior Phase 3 result JSONs), inspects local repo CSVs, and inspects the single allowed
read-only D: price panel for ticker availability. It NEVER fakes a price series: when a proxy is
not locally available it records the exact data requirement instead of inventing values. It
touches no database, runs no migration, restarts no service, enables no model-v2 serving flag,
WRITES nothing to the D: drive (it may only READ the one allowed D: price panel), calls no
provider / paid-vendor / Alpha Vantage / FRED API, uses no yfinance, reads no provider API key
value, places no orders, trades nothing, creates no production model candidate, computes no
production predictions / scores / portfolio weights / order instructions, and writes no deployable
model artifact.

Run:
    python -B research/run_phase3u_global_asset_universe_readiness.py
Test:
    python -B tests/test_phase3u_global_asset_universe_readiness.py
"""
from __future__ import annotations

import csv
import json
import math
import os
from datetime import datetime

PHASE = "3-U"

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)

# --------------------------------------------------------------------------- #
# Paths (outputs all local, on C:; the D: price panel is READ-ONLY, never written)
# --------------------------------------------------------------------------- #
_OUT_DIR = os.path.join(_REPO_ROOT, "research", "output")
_U_DIR = os.path.join(_OUT_DIR, "phase3u_global_asset_universe_readiness")
RESULT_JSON = os.path.join(_OUT_DIR, "phase3u_global_asset_universe_readiness.json")

_M_DIR = os.path.join(_OUT_DIR, "phase3m_earnings_estimates_signal_gate")
PHASE3M_PROGRESS_JSON = os.path.join(_M_DIR, "collection_progress.json")
PHASE3M_RAW_DIR = os.path.join(_M_DIR, "raw")

PHASE3S_RESULT_JSON = os.path.join(_OUT_DIR, "phase3s_event_signal_readiness_gate.json")
PHASE3R_RESULT_JSON = os.path.join(_OUT_DIR, "phase3r_macro_regime_walkforward.json")
PHASE3P_RESULT_JSON = os.path.join(_OUT_DIR, "phase3p_multisignal_walkforward_model.json")
PHASE3O_RESULT_JSON = os.path.join(_OUT_DIR, "phase3o_multisignal_feature_factory.json")

# The single allowed read-only local price panel. It is on the data drive and is only ever opened
# for reading (price-availability inspection). Nothing on that drive is written by this phase.
_DATA_DRIVE_PRICE_PANEL = os.path.join(
    "D:" + os.sep, "Stock_Prediction_app_data", "phase2k_g", "output",
    "phase2k_g_expanded_price_history_free.csv",
)

# Local repo CSV scan scope (no internet, no provider). The phase3u output dir is excluded so the
# gate never inspects its own coverage CSVs as if they were price panels.
_SCAN_ROOTS = [
    os.path.join(_REPO_ROOT, "research", "input"),
    os.path.join(_REPO_ROOT, "research", "output"),
    os.path.join(_REPO_ROOT, "data"),
    _REPO_ROOT,  # top-level CSVs only (non-recursive at the repo root)
]

# Earnings coverage thresholds (read-only context from Phase 3-T / 3-M).
SIGNAL_GATE_MIN_TICKERS = 75
EARNINGS_MIN_CACHED_EXPECTED = 50

# --------------------------------------------------------------------------- #
# Decision values
# --------------------------------------------------------------------------- #
REC_READY = "GLOBAL_ASSET_UNIVERSE_READY_FOR_FEATURE_FACTORY"
REC_PARTIAL = "GLOBAL_ASSET_UNIVERSE_PARTIAL_LOCAL_COVERAGE"
REC_BLOCKED_DATA = "GLOBAL_ASSET_UNIVERSE_BLOCKED_NEEDS_LOCAL_PRICE_DATA"
REC_BLOCKED_INPUTS = "GLOBAL_ASSET_UNIVERSE_BLOCKED_INPUTS"
ALLOWED_RECOMMENDATIONS = (REC_READY, REC_PARTIAL, REC_BLOCKED_DATA, REC_BLOCKED_INPUTS)

# Decision-rule thresholds.
READY_MIN_TICKERS = 12
READY_MIN_ASSET_CLASSES = 5
PARTIAL_MIN_TICKERS = 5

# Column-name fragments used to recognise a local price panel and locate its columns.
_TICKER_COL_HINTS = ["ticker", "symbol", "secid", "permno"]
_DATE_COL_HINTS = ["date", "timestamp", "datetime", "as_of", "asof"]
_PRICE_COL_HINTS = ["adjusted_close", "adj_close", "adjclose", "close", "price", "adjusted_open"]

# Minimum daily rows for a "long enough" local history (about 3 trading years).
_LONG_HISTORY_MIN_ROWS = 750


# --------------------------------------------------------------------------- #
# Target global cross-asset proxy universe (definition only; no prices fabricated)
# --------------------------------------------------------------------------- #
def build_target_universe():
    """Return the practical global cross-asset ETF proxy universe. Definition only - this assigns
    each liquid proxy an asset class / region / role; it fabricates no price data."""
    u = [
        ("SPY", "us_equity", "united_states", "us_large_cap",
         "equity_risk_appetite_core", 1, "S&P 500 broad-market risk-on/off anchor"),
        ("QQQ", "us_equity", "united_states", "us_large_cap_growth",
         "equity_risk_appetite_growth", 1, "Nasdaq-100 growth/tech beta"),
        ("IWM", "us_equity", "united_states", "us_small_cap",
         "equity_risk_appetite_breadth", 1, "Russell 2000 small-cap breadth"),
        ("EFA", "intl_dev_equity", "developed_ex_us", "developed_intl_equity",
         "global_equity_breadth", 2, "Developed ex-US equity (EAFE)"),
        ("EEM", "intl_em_equity", "emerging_markets", "emerging_market_equity",
         "global_equity_breadth", 2, "Emerging-market equity risk appetite"),
        ("VGK", "intl_dev_equity", "europe", "european_equity",
         "global_equity_breadth", 2, "European developed equity"),
        ("EWJ", "intl_dev_equity", "japan", "japanese_equity",
         "global_equity_breadth", 2, "Japanese equity"),
        ("FXI", "intl_em_equity", "china", "china_large_cap_equity",
         "global_equity_breadth", 2, "China large-cap equity"),
        ("SHY", "rates", "united_states", "ust_1_3y",
         "rates_duration_regime_short", 1, "1-3y US Treasury (front-end / cash proxy)"),
        ("IEF", "rates", "united_states", "ust_7_10y",
         "rates_duration_regime_intermediate", 1, "7-10y US Treasury (intermediate duration)"),
        ("TLT", "rates", "united_states", "ust_20y_plus",
         "rates_duration_regime_long", 1, "20y+ US Treasury (long duration)"),
        ("TIP", "rates", "united_states", "us_tips",
         "rates_inflation_breakeven", 2, "Inflation-protected Treasury (breakeven proxy)"),
        ("LQD", "credit", "united_states", "investment_grade_credit",
         "credit_risk_ig", 1, "Investment-grade corporate credit"),
        ("HYG", "credit", "united_states", "high_yield_credit",
         "credit_risk_hy", 1, "High-yield corporate credit (risk appetite)"),
        ("GLD", "commodity", "global", "gold",
         "commodity_inflation_gold", 1, "Gold (inflation / safe-haven)"),
        ("SLV", "commodity", "global", "silver",
         "commodity_inflation_silver", 2, "Silver (inflation / industrial)"),
        ("DBC", "commodity", "global", "broad_commodity",
         "commodity_inflation_broad", 2, "Broad commodity basket"),
        ("USO", "commodity", "global", "crude_oil",
         "commodity_inflation_energy", 2, "WTI crude oil (energy / inflation)"),
        ("UUP", "currency", "united_states", "us_dollar_index",
         "dollar_liquidity_usd", 1, "US dollar index (global liquidity)"),
        ("FXE", "currency", "europe", "euro",
         "dollar_liquidity_eur", 2, "Euro currency"),
        ("FXY", "currency", "japan", "japanese_yen",
         "dollar_liquidity_jpy", 2, "Japanese yen (funding currency / risk-off)"),
        ("VIXY", "volatility", "united_states", "vix_short_term_futures",
         "volatility_risk", 1, "Short-term VIX futures (volatility regime / shock)"),
    ]
    cols = ["ticker", "asset_class", "region", "exposure", "role_in_model", "priority", "notes"]
    return [dict(zip(cols, row)) for row in u]


# Cross-asset feature blueprint definitions (feature engineering described only; not implemented).
def _feature_defs():
    return [
        ("equity_risk_appetite", "us_equity_trend",
         ["SPY", "QQQ", "IWM"],
         "Multi-horizon (20/60/120d) price momentum across large-, growth-, and small-cap US "
         "equity to gauge equity risk appetite.", "HIGH"),
        ("equity_risk_appetite", "us_equity_realized_vol",
         ["SPY", "QQQ", "IWM"],
         "Trailing realized volatility of the core US equity proxies (risk regime).",
         "MEDIUM_HIGH"),
        ("global_equity_breadth", "intl_vs_us_relative_strength",
         ["SPY", "EFA", "EEM", "VGK", "EWJ", "FXI"],
         "Relative strength of international (developed + emerging) equity versus US equity.",
         "MEDIUM"),
        ("global_equity_breadth", "emerging_vs_developed_spread",
         ["EEM", "EFA"],
         "Emerging-minus-developed return spread as a global risk-appetite proxy.", "MEDIUM"),
        ("rates_duration_regime", "treasury_duration_returns",
         ["SHY", "IEF", "TLT"],
         "Front/intermediate/long Treasury returns to characterise the duration / curve regime.",
         "MEDIUM_HIGH"),
        ("rates_duration_regime", "breakeven_inflation_proxy",
         ["TIP", "IEF"],
         "TIP-versus-nominal Treasury spread as a market inflation-breakeven proxy.", "MEDIUM"),
        ("credit_risk", "hy_minus_ig_spread_proxy",
         ["HYG", "LQD"],
         "High-yield minus investment-grade return spread as a credit risk-on/off proxy.", "HIGH"),
        ("commodity_inflation", "commodity_momentum",
         ["GLD", "SLV", "DBC", "USO"],
         "Cross-commodity momentum (metals, broad basket, energy) for the inflation impulse.",
         "MEDIUM"),
        ("commodity_inflation", "gold_oil_ratio",
         ["GLD", "USO"],
         "Gold/oil ratio as a growth-versus-inflation / safe-haven signal.", "MEDIUM"),
        ("dollar_liquidity", "dollar_trend",
         ["UUP", "FXE", "FXY"],
         "US-dollar trend versus euro and yen as a global-liquidity tightening/easing proxy.",
         "MEDIUM_HIGH"),
        ("volatility_risk", "vix_trend_and_shock",
         ["VIXY"],
         "Short-term VIX-futures trend and shock indicators for the volatility regime.", "HIGH"),
        ("cross_asset_regime", "risk_on_off_composite",
         ["SPY", "HYG", "LQD", "TLT", "GLD", "UUP", "VIXY"],
         "Combined cross-asset risk-on/risk-off flag from equity, credit, rates, gold, dollar, "
         "and volatility.", "HIGH"),
    ]


# --------------------------------------------------------------------------- #
# Small IO helpers
# --------------------------------------------------------------------------- #
def _load_json(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _rel(path):
    try:
        return os.path.relpath(path, _REPO_ROOT).replace("\\", "/")
    except ValueError:
        # Different drive (the read-only data-drive price panel) - return a forward-slashed label.
        return path.replace("\\", "/")


def _write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            out = {}
            for c in columns:
                v = r.get(c, "")
                if isinstance(v, list):
                    v = ";".join(str(x) for x in v)
                out[c] = "" if v is None or (isinstance(v, float) and math.isnan(v)) else v
            w.writerow(out)


def _json_default(o):
    if hasattr(o, "isoformat"):
        return o.isoformat()
    if hasattr(o, "item"):
        try:
            return o.item()
        except (ValueError, TypeError):
            pass
    return str(o)


def _dump_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, default=_json_default)


# --------------------------------------------------------------------------- #
# 1. Confirm current Phase 3-T / 3-M earnings state (read-only; Phase 3-M not modified)
# --------------------------------------------------------------------------- #
def _count_cached_tickers(raw_dir=PHASE3M_RAW_DIR):
    """Count cached earnings tickers from the local Phase 3-M raw cache directory listing only.
    Read-only directory listing - no file content parsed, no network."""
    if not os.path.isdir(raw_dir):
        return 0
    return sum(1 for n in os.listdir(raw_dir)
               if n.lower().endswith(".json") and os.path.isfile(os.path.join(raw_dir, n)))


def confirm_phase3t_state(progress_json=PHASE3M_PROGRESS_JSON, raw_dir=PHASE3M_RAW_DIR):
    """Read the Phase 3-T / 3-M collection progress (read-only) and summarise the earnings state.
    Phase 3-M outputs are never modified here."""
    prog = _load_json(progress_json) or {}
    cached_from_dir = _count_cached_tickers(raw_dir)
    cached_from_prog = prog.get("cached_ticker_count_after_run")
    cached = cached_from_dir if cached_from_dir else int(cached_from_prog or 0)
    universe = int(prog.get("universe_ticker_count") or 0)
    missing = max(0, universe - cached) if universe else None
    min_tickers = int(prog.get("signal_gate_min_tickers") or SIGNAL_GATE_MIN_TICKERS)
    gate_allowed = bool(prog.get("signal_gate_allowed_now")) or (cached >= min_tickers)
    provider_limit_hit = bool(prog.get("provider_limit_hit"))
    guard_active = bool(prog.get("same_day_limit_guard_active"))

    if cached >= min_tickers:
        state = "EARNINGS_COVERAGE_SUFFICIENT"
    elif provider_limit_hit or guard_active:
        state = "EARNINGS_PROVIDER_LIMITED_WAITING"
    else:
        state = "EARNINGS_COLLECTION_IN_PROGRESS"

    return {
        "progress_present": bool(prog),
        "universe_ticker_count": universe,
        "cached_ticker_count": cached,
        "missing_ticker_count": missing,
        "cached_at_least_50": cached >= EARNINGS_MIN_CACHED_EXPECTED,
        "signal_gate_min_tickers": min_tickers,
        "signal_gate_allowed_now": gate_allowed,
        "provider_limit_hit": provider_limit_hit,
        "same_day_limit_guard_active": guard_active,
        "earnings_collection_state": state,
        "note": "Read-only Phase 3-T / 3-M state. Phase 3-U does not modify Phase 3-M outputs and "
                "makes no provider call; earnings collection is provider-limited / waiting when the "
                "daily quota has been hit.",
    }


# --------------------------------------------------------------------------- #
# 2. Local price availability inspection (repo CSVs + the one read-only D: price panel)
# --------------------------------------------------------------------------- #
def _first_index(low_header, hints):
    for i, c in enumerate(low_header):
        if any(h in c for h in hints):
            return i
    return None


def _iter_local_csvs(scan_roots):
    seen = set()
    u_dir_norm = os.path.normcase(os.path.abspath(_U_DIR))
    for root in scan_roots:
        if not os.path.isdir(root):
            continue
        recursive = (root != _REPO_ROOT)
        if recursive:
            for dirpath, _dirs, files in os.walk(root):
                # Never inspect this phase's own output directory as a price panel.
                if os.path.normcase(os.path.abspath(dirpath)).startswith(u_dir_norm):
                    continue
                for name in files:
                    if name.lower().endswith(".csv"):
                        p = os.path.join(dirpath, name)
                        if p not in seen:
                            seen.add(p)
                            yield p
        else:
            for name in os.listdir(root):
                p = os.path.join(root, name)
                if name.lower().endswith(".csv") and os.path.isfile(p) and p not in seen:
                    seen.add(p)
                    yield p


def _scan_price_panel(path, targets):
    """Single read pass over a CSV. If it looks like a daily price panel (ticker + date + price
    columns) collect, for each TARGET ticker only, its row_count and date min/max. Returns
    (found_dict, is_price_panel). No price values are fabricated or modified - read-only."""
    found = {}
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                return {}, False
            low = [c.strip().lower() for c in header]
            tcol = _first_index(low, _TICKER_COL_HINTS)
            dcol = _first_index(low, _DATE_COL_HINTS)
            pcol = _first_index(low, _PRICE_COL_HINTS)
            if tcol is None or dcol is None or pcol is None:
                return {}, False  # not a price panel (e.g. a universe membership list)
            for r in reader:
                if tcol >= len(r):
                    continue
                tk = (r[tcol] or "").strip().upper()
                if tk not in targets:
                    continue
                dt = (r[dcol] or "").strip() if dcol < len(r) else ""
                rec = found.get(tk)
                if rec is None:
                    found[tk] = {"row_count": 1, "date_min": dt or None, "date_max": dt or None}
                else:
                    rec["row_count"] += 1
                    if dt:
                        if rec["date_min"] is None or dt < rec["date_min"]:
                            rec["date_min"] = dt
                        if rec["date_max"] is None or dt > rec["date_max"]:
                            rec["date_max"] = dt
        return found, True
    except OSError:
        return {}, False


def inspect_local_price_coverage(universe, data_drive_panel=_DATA_DRIVE_PRICE_PANEL,
                                 scan_roots=None):
    """Detect which target tickers have a local daily price history (repo or the one allowed
    read-only D: price panel). Returns (coverage_rows, summary, d_drive_read)."""
    if scan_roots is None:
        scan_roots = _SCAN_ROOTS
    targets = {row["ticker"] for row in universe}
    found = {}  # ticker -> {source_file, row_count, date_min, date_max}
    panels_scanned = []
    d_drive_read = False

    # (a) the single allowed read-only data-drive price panel
    if os.path.isfile(data_drive_panel):
        d_drive_read = True
        d_found, is_panel = _scan_price_panel(data_drive_panel, targets)
        if is_panel:
            panels_scanned.append(_rel(data_drive_panel))
            for tk, rec in d_found.items():
                found[tk] = {"source_file": _rel(data_drive_panel), **rec}

    # (b) local repo CSV price panels (no network, no D: write)
    for path in _iter_local_csvs(scan_roots):
        l_found, is_panel = _scan_price_panel(path, targets)
        if is_panel and l_found:
            panels_scanned.append(_rel(path))
        for tk, rec in l_found.items():
            if tk not in found:
                found[tk] = {"source_file": _rel(path), **rec}

    coverage_rows = []
    by_ticker = {row["ticker"]: row for row in universe}
    for tk in [row["ticker"] for row in universe]:
        ac = by_ticker[tk]["asset_class"]
        rec = found.get(tk)
        if rec and rec["row_count"] > 0:
            status = ("AVAILABLE" if rec["row_count"] >= _LONG_HISTORY_MIN_ROWS
                      else "AVAILABLE_SHORT_HISTORY")
            coverage_rows.append({
                "ticker": tk, "asset_class": ac, "locally_available": True,
                "source_file": rec["source_file"], "date_min": rec["date_min"] or "",
                "date_max": rec["date_max"] or "", "row_count": rec["row_count"],
                "coverage_status": status, "blocker": "",
            })
        else:
            coverage_rows.append({
                "ticker": tk, "asset_class": ac, "locally_available": False,
                "source_file": "", "date_min": "", "date_max": "", "row_count": 0,
                "coverage_status": "MISSING",
                "blocker": "no local daily price history in the repo or the allowed read-only "
                           "price panel",
            })

    available = [r for r in coverage_rows if r["locally_available"]]
    classes_local = sorted({r["asset_class"] for r in available})
    summary = {
        "target_ticker_count": len(universe),
        "locally_available_count": len(available),
        "missing_count": len(coverage_rows) - len(available),
        "asset_classes_total": len(sorted({r["asset_class"] for r in universe})),
        "asset_classes_covered_locally": classes_local,
        "asset_classes_covered_locally_count": len(classes_local),
        "available_tickers": [r["ticker"] for r in available],
        "missing_tickers": [r["ticker"] for r in coverage_rows if not r["locally_available"]],
        "price_panels_scanned": panels_scanned,
        "data_drive_price_panel_read": d_drive_read,
    }
    return coverage_rows, summary, d_drive_read


# --------------------------------------------------------------------------- #
# 3. Data requirements for the missing proxies (described only - never fetched / faked)
# --------------------------------------------------------------------------- #
def build_data_requirements(coverage_rows, universe):
    by_ticker = {row["ticker"]: row for row in universe}
    rows = []
    for cov in coverage_rows:
        if cov["locally_available"]:
            continue
        tk = cov["ticker"]
        u = by_ticker[tk]
        rows.append({
            "ticker": tk,
            "asset_class": u["asset_class"],
            "required_fields": "ticker;date;adjusted_open;adjusted_high;adjusted_low;"
                               "adjusted_close;volume",
            "min_history_start": "2016-01-04",
            "required_frequency": "daily",
            "point_in_time_rule": "split/dividend-adjusted close as-of the trade date; corporate "
                                  "actions pre-adjusted; no lookahead",
            "priority": u["priority"],
            "why_needed": "%s proxy (%s) feeding the %s cross-asset feature family"
                          % (u["asset_class"], u["exposure"], u["role_in_model"]),
            "acceptable_local_file_format": "local CSV with the same schema as the existing price "
                                            "panel: ticker,date,adjusted_open,adjusted_high,"
                                            "adjusted_low,adjusted_close,volume",
        })
    return rows


# --------------------------------------------------------------------------- #
# 4. Cross-asset feature blueprint (described only - not implemented, not computed)
# --------------------------------------------------------------------------- #
def build_feature_blueprint(coverage_rows):
    avail = {r["ticker"]: r["locally_available"] for r in coverage_rows}
    rows = []
    for family, name, req, desc, impact in _feature_defs():
        have = [t for t in req if avail.get(t)]
        if len(have) == len(req):
            status = "READY_TO_BUILD"
        elif have:
            status = "PARTIAL_NEEDS_PRICE_DATA"
        else:
            status = "BLOCKED_NEEDS_PRICE_DATA"
        rows.append({
            "feature_family": family,
            "feature_name": name,
            "required_tickers": ";".join(req),
            "description": desc,
            "expected_impact": impact,
            "point_in_time_safe": True,
            "dependency": "local daily price history for: %s" % ", ".join(req),
            "implementation_status": status,
        })
    return rows


# --------------------------------------------------------------------------- #
# 5. Global model roadmap
# --------------------------------------------------------------------------- #
def build_global_model_roadmap():
    return [
        {"phase": "3-V", "title": "Acquire or assemble local global ETF price panel",
         "description": "Obtain real, local daily price history for the missing global ETF proxies "
                        "(same schema as the existing price panel); never faked.",
         "depends_on": "local global ETF price CSVs", "status": "NEXT"},
        {"phase": "3-W", "title": "Build cross-asset feature factory",
         "description": "Engineer the point-in-time cross-asset regime features from the assembled "
                        "global ETF panel.",
         "depends_on": "3-V local global ETF price panel", "status": "PLANNED"},
        {"phase": "3-X", "title": "Re-run walk-forward model with earnings + macro + cross-asset",
         "description": "Re-run the research walk-forward with earnings, macro/regime, and the new "
                        "cross-asset feature families; measure robustness. No production model.",
         "depends_on": "3-W cross-asset features + Phase 3-T earnings coverage", "status": "PLANNED"},
        {"phase": "3-Y", "title": "Turnover-aware portfolio simulation and risk budget",
         "description": "Research-only turnover-aware risk simulation and risk budgeting; no "
                        "portfolio weights deployed, no orders.",
         "depends_on": "3-X robustness-passing research signal", "status": "PLANNED"},
        {"phase": "3-Z", "title": "Non-production candidate packaging",
         "description": "Package a research candidate (no deployable artifact, no production edge).",
         "depends_on": "3-Y risk simulation", "status": "PLANNED"},
        {"phase": "4-A", "title": "Paper Trader preview integration only",
         "description": "Preview-only integration; no orders, no automation, manual review only.",
         "depends_on": "a reviewed non-production candidate", "status": "PLANNED"},
    ]


# --------------------------------------------------------------------------- #
# 6. Decision
# --------------------------------------------------------------------------- #
def decide(inputs_ok, coverage_summary):
    if not inputs_ok:
        return REC_BLOCKED_INPUTS
    avail = coverage_summary["locally_available_count"]
    classes = coverage_summary["asset_classes_covered_locally_count"]
    if avail >= READY_MIN_TICKERS and classes >= READY_MIN_ASSET_CLASSES:
        return REC_READY
    if avail >= PARTIAL_MIN_TICKERS:
        return REC_PARTIAL
    return REC_BLOCKED_DATA


_NEXT_PHASE = {
    REC_READY: ("3-V", "Build Cross-Asset Feature Factory",
                "Local coverage is sufficient; build the point-in-time cross-asset feature factory "
                "from the assembled global ETF price panel."),
    REC_PARTIAL: ("3-V", "Acquire Missing Global Asset Price Data",
                  "Some proxies are locally available but not enough; acquire local price history "
                  "for the remaining proxies before building the feature factory."),
    REC_BLOCKED_DATA: ("3-V", "Acquire Local Global ETF Price Data",
                       "Too few proxies are locally available; acquire real local daily price "
                       "history for the global ETF proxy universe (never faked)."),
    REC_BLOCKED_INPUTS: ("3-V", "Repair Phase 3-U Inputs",
                         "Required local inspection inputs could not be read; repair them first."),
}


def build_recommended_next_phase(rec):
    phase, title, purpose = _NEXT_PHASE[rec]
    return {"phase": phase, "title": title, "purpose": purpose}


def build_recommendation(rec, coverage_summary):
    avail = coverage_summary["locally_available_count"]
    total = coverage_summary["target_ticker_count"]
    classes = coverage_summary["asset_classes_covered_locally_count"]
    reasons = {
        REC_READY:
            "%d of %d target proxies are locally available across %d asset classes (>= %d tickers "
            "and >= %d classes), so the cross-asset feature factory can be built next."
            % (avail, total, classes, READY_MIN_TICKERS, READY_MIN_ASSET_CLASSES),
        REC_PARTIAL:
            "%d of %d target proxies are locally available (>= %d) but not enough for the "
            "READY bar (>= %d tickers across >= %d classes); acquire the remaining local price "
            "data next." % (avail, total, PARTIAL_MIN_TICKERS, READY_MIN_TICKERS,
                            READY_MIN_ASSET_CLASSES),
        REC_BLOCKED_DATA:
            "Only %d of %d target proxies are locally available (< %d), so the next step is to "
            "acquire real local daily price history for the global ETF proxy universe (never "
            "faked)." % (avail, total, PARTIAL_MIN_TICKERS),
        REC_BLOCKED_INPUTS:
            "Required local inspection inputs could not be read; repair them before proceeding.",
    }
    return {
        "recommendation": rec,
        "allowed_values": list(ALLOWED_RECOMMENDATIONS),
        "research_only": True,
        "build_feature_factory_now": rec == REC_READY,
        "train_production_model_now": False,
        "create_production_model_candidate_now": False,
        "compute_production_predictions_now": False,
        "compute_portfolio_weights_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "price_data_faked": False,
        "reason": reasons[rec],
    }


# --------------------------------------------------------------------------- #
# 7. Decision table + safety flags + interpretation
# --------------------------------------------------------------------------- #
def build_decision_table(phase3t, coverage_summary, rec):
    rows = []

    def add(item, value, passed, note):
        rows.append({"decision_item": item, "value": value,
                     "passed": "" if passed is None else bool(passed), "note": note})

    avail = coverage_summary["locally_available_count"]
    classes = coverage_summary["asset_classes_covered_locally_count"]
    add("phase3t_inputs_present", phase3t["progress_present"], phase3t["progress_present"],
        "Phase 3-T / 3-M collection progress readable (read-only)")
    add("earnings_cached_at_least_50", phase3t["cached_at_least_50"], phase3t["cached_at_least_50"],
        "Phase 3-T cached earnings tickers >= 50")
    add("earnings_signal_gate_allowed_now", phase3t["signal_gate_allowed_now"], None,
        "informational; earnings coverage still below the %d-ticker gate when False"
        % phase3t["signal_gate_min_tickers"])
    add("earnings_collection_state", phase3t["earnings_collection_state"], None,
        "provider-limited / waiting when the daily quota has been hit")
    add("target_universe_size", coverage_summary["target_ticker_count"],
        coverage_summary["target_ticker_count"] >= 20, "at least 20 cross-asset proxies defined")
    add("locally_available_proxies", avail, None, "target proxies with a local daily price history")
    add("asset_classes_covered_locally", classes, None,
        "distinct asset classes with at least one locally available proxy")
    add("ready_threshold_met", avail >= READY_MIN_TICKERS and classes >= READY_MIN_ASSET_CLASSES,
        avail >= READY_MIN_TICKERS and classes >= READY_MIN_ASSET_CLASSES,
        "READY needs >= %d proxies across >= %d asset classes"
        % (READY_MIN_TICKERS, READY_MIN_ASSET_CLASSES))
    add("no_price_data_faked", True, True, "missing proxies recorded as requirements, not invented")
    add("provider_or_alpha_vantage_called", False, True,
        "no provider / Alpha Vantage / FRED API / yfinance call by this gate")
    add("d_drive_written", False, True, "the data-drive price panel is read-only; nothing written")
    add("phase3m_outputs_modified", False, True, "Phase 3-M outputs are only read, never written")
    add("no_production_model_candidate", True, True, "readiness / data-planning gate only")
    add("no_production_predictions_or_scores", True, True, "no predictions / scores computed")
    add("no_portfolio_weights", True, True, "no portfolio weights computed")
    add("no_deployable_artifact", True, True, "no serialized model file written")
    add("recommendation", rec, rec in ALLOWED_RECOMMENDATIONS, "allowed values only")
    return rows


def build_safety_flags(d_drive_read):
    return {
        "database_touched": False,
        "database_write_executed": False,
        "migration_executed": False,
        "deployment_executed": False,
        "model_v2_enabled": False,
        "production_edge_claimed": False,
        "no_trading": True,
        "no_orders": True,
        "no_automation": True,
        "research_only": True,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "deployable_model_artifact_written": False,
        "production_predictions_computed": False,
        "production_scores_computed": False,
        "portfolio_weights_computed": False,
        "order_instructions_created": False,
        "d_drive_read": bool(d_drive_read),
        "d_drive_written": False,
        "provider_api_called": False,
        "alpha_vantage_called": False,
        "fred_called": False,
        "paid_vendor_api_called": False,
        "yfinance_called": False,
        "data_faked": False,
        "labels_for_validation_only": True,
    }


def build_interpretation(rec, coverage_summary):
    return {
        "phase_is_research_only": True,
        "research_only": True,
        "readiness_and_data_planning_gate_only": True,
        "price_data_collected": False,
        "provider_api_called": False,
        "price_data_faked": False,
        "production_predictions_computed": False,
        "production_scores_computed": False,
        "portfolio_weights_computed": False,
        "deployable_model_artifact_written": False,
        "production_edge_claimed": False,
        "results_remain_survivorship_biased": True,
        "narrative":
            "Phase 3-U is the research-only readiness and data-planning gate for expanding the "
            "model from a US-equity universe toward a global cross-asset view while Phase 3-T "
            "earnings collection waits on the provider's daily quota. It defines a practical global "
            "cross-asset ETF proxy universe, inspects local price availability (repo CSVs plus the "
            "one allowed read-only price panel), classifies the missing proxies with exact "
            "(non-faked) data requirements, blueprints the cross-asset features, and recommends %s. "
            "It trains no model, computes no predictions / scores / portfolio weights / order "
            "instructions, writes no deployable artifact, touches no database, runs no migration, "
            "restarts no service, enables no serving flag, writes nothing to the data drive, calls "
            "no provider / paid-vendor / Alpha Vantage / FRED API, and uses no yfinance library. "
            "The equity universe is current-as-of, so results remain survivorship-biased and claim "
            "no "
            "production edge." % rec,
    }


# --------------------------------------------------------------------------- #
# Output column specs
# --------------------------------------------------------------------------- #
_OUT_PATHS = {
    "universe": "target_global_asset_universe.csv",
    "coverage": "local_price_coverage.csv",
    "requirements": "global_asset_data_requirements.csv",
    "blueprint": "cross_asset_feature_blueprint.csv",
    "roadmap": "global_model_roadmap.csv",
    "decision": "readiness_decision_table.csv",
}
_UNIVERSE_COLS = ["ticker", "asset_class", "region", "exposure", "role_in_model", "priority",
                  "notes"]
_COVERAGE_COLS = ["ticker", "asset_class", "locally_available", "source_file", "date_min",
                  "date_max", "row_count", "coverage_status", "blocker"]
_REQUIREMENTS_COLS = ["ticker", "asset_class", "required_fields", "min_history_start",
                      "required_frequency", "point_in_time_rule", "priority", "why_needed",
                      "acceptable_local_file_format"]
_BLUEPRINT_COLS = ["feature_family", "feature_name", "required_tickers", "description",
                   "expected_impact", "point_in_time_safe", "dependency", "implementation_status"]
_ROADMAP_COLS = ["phase", "title", "description", "depends_on", "status"]
_DECISION_COLS = ["decision_item", "value", "passed", "note"]


def _out(o_dir, key):
    return os.path.join(o_dir, _OUT_PATHS[key])


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run(result_json_path=RESULT_JSON, o_dir=_U_DIR, progress_json=PHASE3M_PROGRESS_JSON,
        raw_dir=PHASE3M_RAW_DIR, data_drive_panel=_DATA_DRIVE_PRICE_PANEL, scan_roots=None,
        verbose=True):
    if verbose:
        print("  confirming Phase 3-T / 3-M earnings state (read-only) ...")
    phase3t = confirm_phase3t_state(progress_json, raw_dir)

    universe = build_target_universe()

    if verbose:
        print("  inspecting local price availability (repo + read-only price panel) ...")
    coverage_rows, coverage_summary, d_drive_read = inspect_local_price_coverage(
        universe, data_drive_panel=data_drive_panel, scan_roots=scan_roots)

    requirements_rows = build_data_requirements(coverage_rows, universe)
    blueprint_rows = build_feature_blueprint(coverage_rows)
    roadmap_rows = build_global_model_roadmap()

    inputs_ok = phase3t["progress_present"]
    rec = decide(inputs_ok, coverage_summary)
    decision_rows = build_decision_table(phase3t, coverage_summary, rec)

    # Write CSV outputs (all under research/output/phase3u...; never to the data drive).
    _write_csv(_out(o_dir, "universe"), _UNIVERSE_COLS, universe)
    _write_csv(_out(o_dir, "coverage"), _COVERAGE_COLS, coverage_rows)
    _write_csv(_out(o_dir, "requirements"), _REQUIREMENTS_COLS, requirements_rows)
    _write_csv(_out(o_dir, "blueprint"), _BLUEPRINT_COLS, blueprint_rows)
    _write_csv(_out(o_dir, "roadmap"), _ROADMAP_COLS, roadmap_rows)
    _write_csv(_out(o_dir, "decision"), _DECISION_COLS, decision_rows)

    blueprint_summary = {
        "feature_count": len(blueprint_rows),
        "feature_families": sorted({r["feature_family"] for r in blueprint_rows}),
        "ready_to_build": sum(1 for r in blueprint_rows
                              if r["implementation_status"] == "READY_TO_BUILD"),
        "partial": sum(1 for r in blueprint_rows
                       if r["implementation_status"] == "PARTIAL_NEEDS_PRICE_DATA"),
        "blocked": sum(1 for r in blueprint_rows
                       if r["implementation_status"] == "BLOCKED_NEEDS_PRICE_DATA"),
    }
    target_universe_summary = {
        "ticker_count": len(universe),
        "asset_classes": sorted({r["asset_class"] for r in universe}),
        "asset_class_count": len(sorted({r["asset_class"] for r in universe})),
        "regions": sorted({r["region"] for r in universe}),
    }
    missing_data_summary = {
        "missing_count": len(requirements_rows),
        "missing_tickers": [r["ticker"] for r in requirements_rows],
        "required_fields": "ticker;date;adjusted_open;adjusted_high;adjusted_low;adjusted_close;"
                           "volume",
        "required_frequency": "daily",
        "point_in_time_rule": "adjusted close as-of the trade date; no lookahead",
    }

    result = {
        "phase": PHASE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "phase3m_collection_progress_json": _rel(progress_json),
            "phase3m_raw_cache_dir": _rel(raw_dir),
            "phase3s_result_json": _rel(PHASE3S_RESULT_JSON),
            "phase3r_result_json": _rel(PHASE3R_RESULT_JSON),
            "phase3p_result_json": _rel(PHASE3P_RESULT_JSON),
            "phase3o_result_json": _rel(PHASE3O_RESULT_JSON),
            "data_drive_price_panel_read_only": _rel(data_drive_panel),
        },
        "outputs_written": {
            "result_json": _rel(result_json_path),
            **{k: _rel(_out(o_dir, k)) for k in _OUT_PATHS},
        },
        "phase3t_status_summary": phase3t,
        "target_universe_summary": target_universe_summary,
        "target_global_asset_universe": universe,
        "local_price_coverage_summary": coverage_summary,
        "missing_data_summary": missing_data_summary,
        "feature_blueprint_summary": blueprint_summary,
        "global_model_roadmap": roadmap_rows,
        "readiness_decision_table": decision_rows,
        "recommendation": build_recommendation(rec, coverage_summary),
        "recommended_next_phase": build_recommended_next_phase(rec),
        "interpretation": build_interpretation(rec, coverage_summary),
    }
    result.update(build_safety_flags(d_drive_read))
    _dump_json(result_json_path, result)
    return result


def main():
    result = run()
    rec = result["recommendation"]["recommendation"]
    cov = result["local_price_coverage_summary"]
    p3t = result["phase3t_status_summary"]
    print("phase 3-T earnings state:   %s" % p3t["earnings_collection_state"])
    print("  cached earnings tickers:  %s (signal gate allowed: %s)"
          % (p3t["cached_ticker_count"], p3t["signal_gate_allowed_now"]))
    print("target universe count:      %s" % cov["target_ticker_count"])
    print("locally available proxies:  %s" % cov["locally_available_count"])
    print("missing proxies:            %s" % cov["missing_count"])
    print("asset classes covered:      %s of %s  (%s)"
          % (cov["asset_classes_covered_locally_count"], cov["asset_classes_total"],
             ", ".join(cov["asset_classes_covered_locally"]) or "none"))
    print("data-drive panel read-only: %s" % result["d_drive_read"])
    print("recommendation:             %s" % rec)
    print("recommended next:           %s - %s"
          % (result["recommended_next_phase"]["phase"], result["recommended_next_phase"]["title"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
