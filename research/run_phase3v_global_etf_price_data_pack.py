"""Phase 3-V - Global ETF Price Data Pack.

Acquire and normalize REAL, local historical daily price data for the 22 global cross-asset ETF /
proxy tickers defined in Phase 3-U, using free public Stooq CSV downloads ONLY. This is local
research data preparation - it trains no model, computes no predictions / scores / portfolio
weights / order instructions, writes no deployable model artifact, and never fabricates a price row.

Allowed network: Stooq public daily CSV download endpoint only
    https://stooq.com/q/d/l/?s=<lowercase_ticker>.us&i=d
No other network source is contacted. This phase does NOT use yfinance, does NOT call Alpha Vantage,
does NOT call FRED, and calls no paid vendor API. It reads no provider API key, touches no database,
runs no migration, restarts no service, enables no model-v2 serving flag, writes nothing to the D:
drive, places no orders, and trades nothing.

What it does:
  1. Reads the Phase 3-U result + target universe (confirms 3-U recommended acquiring local price
     data) and uses the Phase 3-U universe CSV as the source of target tickers.
  2. Downloads each target's Stooq daily CSV to research/input/global_assets/stooq/<ticker>.csv
     (urllib / standard library only; <=2 retries per ticker; small sleep between requests; HTTP /
     download errors are recorded, never crash the run; Stooq domain only).
  3. Normalizes to a common schema (ticker, date, open, high, low, close, adjusted_close, volume,
     source); adjusted_close uses close because Stooq daily CSV has no adjusted column; ISO dates;
     numeric OHLCV; invalid rows dropped; NO missing rows fabricated.
  4. Writes a manifest, a combined price panel, per-ticker coverage + quality, a missing/failed
     table, a cross-asset feature-readiness table, and a decision table - plus a result JSON.

Run:
    python -B research/run_phase3v_global_etf_price_data_pack.py
Test:
    python -B tests/test_phase3v_global_etf_price_data_pack.py
"""
from __future__ import annotations

import csv
import json
import math
import os
import time
import urllib.error
import urllib.request
from datetime import datetime

PHASE = "3-V"

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)

# --------------------------------------------------------------------------- #
# Paths (everything on C:; nothing is written to the D: data drive)
# --------------------------------------------------------------------------- #
_OUT_DIR = os.path.join(_REPO_ROOT, "research", "output")
_V_DIR = os.path.join(_OUT_DIR, "phase3v_global_etf_price_data_pack")
RESULT_JSON = os.path.join(_OUT_DIR, "phase3v_global_etf_price_data_pack.json")

# Raw downloaded CSVs land here (local input cache; allowed new directory).
_STOOQ_INPUT_DIR = os.path.join(_REPO_ROOT, "research", "input", "global_assets", "stooq")

# Phase 3-U inputs (read-only).
_U_DIR = os.path.join(_OUT_DIR, "phase3u_global_asset_universe_readiness")
PHASE3U_RESULT_JSON = os.path.join(_OUT_DIR, "phase3u_global_asset_universe_readiness.json")
PHASE3U_UNIVERSE_CSV = os.path.join(_U_DIR, "target_global_asset_universe.csv")

# Phase 3-M earnings outputs - referenced only so tests can prove they are never written.
_M_DIR = os.path.join(_OUT_DIR, "phase3m_earnings_estimates_signal_gate")

# --------------------------------------------------------------------------- #
# Network: Stooq ONLY
# --------------------------------------------------------------------------- #
STOOQ_DOMAIN = "stooq.com"
_STOOQ_URL_TEMPLATE = "https://stooq.com/q/d/l/?s=%s&i=d"
_HTTP_TIMEOUT_SECONDS = 30
_MAX_RETRIES = 2            # at most 2 retries (3 attempts total) per ticker
_SLEEP_BETWEEN_REQUESTS = 1.0
_REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (research data pack; local CSV fetch)",
    "Accept": "text/csv,text/plain,*/*",
}

# --------------------------------------------------------------------------- #
# Phase 3-U recommendations this phase legitimately follows.
# --------------------------------------------------------------------------- #
_ACCEPTED_PHASE3U_RECS = {
    "GLOBAL_ASSET_UNIVERSE_BLOCKED_NEEDS_LOCAL_PRICE_DATA",
    "GLOBAL_ASSET_UNIVERSE_PARTIAL_LOCAL_COVERAGE",
}

# --------------------------------------------------------------------------- #
# Decision values
# --------------------------------------------------------------------------- #
REC_READY = "GLOBAL_ETF_PRICE_DATA_READY"
REC_PARTIAL = "GLOBAL_ETF_PRICE_DATA_PARTIAL_DOWNLOAD"
REC_BLOCKED = "GLOBAL_ETF_PRICE_DATA_BLOCKED_DOWNLOAD_FAILURE"
ALLOWED_RECOMMENDATIONS = (REC_READY, REC_PARTIAL, REC_BLOCKED)

READY_MIN_TICKERS = 12
READY_MIN_ASSET_CLASSES = 5
PARTIAL_MIN_TICKERS = 5

# A download is "usable" only with a reasonable amount of clean daily history.
_MIN_USABLE_ROWS = 250          # ~1 trading year
_LONG_HISTORY_MIN_ROWS = 750    # ~3 trading years -> coverage_status OK
_HISTORY_START_CUTOFF = "2016-12-31"   # history begins in/before 2016 -> has_2016_start

# Fallback universe (only used if the Phase 3-U universe CSV cannot be read). Keeps the phase
# functional but the gate prefers the real Phase 3-U universe.
_FALLBACK_UNIVERSE = [
    ("SPY", "us_equity", "united_states"), ("QQQ", "us_equity", "united_states"),
    ("IWM", "us_equity", "united_states"), ("EFA", "intl_dev_equity", "developed_ex_us"),
    ("EEM", "intl_em_equity", "emerging_markets"), ("VGK", "intl_dev_equity", "europe"),
    ("EWJ", "intl_dev_equity", "japan"), ("FXI", "intl_em_equity", "china"),
    ("SHY", "rates", "united_states"), ("IEF", "rates", "united_states"),
    ("TLT", "rates", "united_states"), ("TIP", "rates", "united_states"),
    ("LQD", "credit", "united_states"), ("HYG", "credit", "united_states"),
    ("GLD", "commodity", "global"), ("SLV", "commodity", "global"),
    ("DBC", "commodity", "global"), ("USO", "commodity", "global"),
    ("UUP", "currency", "united_states"), ("FXE", "currency", "europe"),
    ("FXY", "currency", "japan"), ("VIXY", "volatility", "united_states"),
]


# --------------------------------------------------------------------------- #
# Cross-asset feature families (required tickers; same blueprint as Phase 3-U)
# --------------------------------------------------------------------------- #
def _feature_defs():
    return [
        ("equity_risk_appetite", ["SPY", "QQQ", "IWM"]),
        ("global_equity_breadth", ["SPY", "EFA", "EEM", "VGK", "EWJ", "FXI"]),
        ("rates_duration_regime", ["SHY", "IEF", "TLT", "TIP"]),
        ("credit_risk", ["HYG", "LQD"]),
        ("commodity_inflation", ["GLD", "SLV", "DBC", "USO"]),
        ("dollar_liquidity", ["UUP", "FXE", "FXY"]),
        ("volatility_risk", ["VIXY"]),
        ("cross_asset_regime", ["SPY", "HYG", "LQD", "TLT", "GLD", "UUP", "VIXY"]),
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
# 1. Confirm Phase 3-U + load target universe
# --------------------------------------------------------------------------- #
def load_target_universe(universe_csv=PHASE3U_UNIVERSE_CSV):
    """Load the Phase 3-U target universe CSV. Falls back to the embedded definition only if the
    CSV is unreadable. Returns a list of dicts with ticker / asset_class / region."""
    rows = []
    if os.path.isfile(universe_csv):
        try:
            with open(universe_csv, "r", encoding="utf-8", newline="") as f:
                for r in csv.DictReader(f):
                    tk = (r.get("ticker") or "").strip().upper()
                    if not tk:
                        continue
                    rows.append({
                        "ticker": tk,
                        "asset_class": (r.get("asset_class") or "").strip(),
                        "region": (r.get("region") or "").strip(),
                    })
        except OSError:
            rows = []
    if not rows:
        rows = [{"ticker": t, "asset_class": ac, "region": rg}
                for (t, ac, rg) in _FALLBACK_UNIVERSE]
    return rows


def confirm_phase3u(result_json=PHASE3U_RESULT_JSON):
    """Read the Phase 3-U result (read-only) and confirm it recommended acquiring local price
    data. Phase 3-U outputs are never modified here."""
    res = _load_json(result_json) or {}
    phase = res.get("phase")
    rec = ((res.get("recommendation") or {}).get("recommendation")
           if isinstance(res.get("recommendation"), dict) else res.get("recommendation"))
    return {
        "phase3u_present": bool(res),
        "phase3u_phase": phase,
        "phase3u_is_3u": phase == "3-U",
        "phase3u_recommendation": rec,
        "phase3u_recommendation_accepted": rec in _ACCEPTED_PHASE3U_RECS,
        "note": "Phase 3-U recommended acquiring local global ETF price data; Phase 3-V performs "
                "that acquisition from Stooq only. Phase 3-U outputs are read-only here.",
    }


# --------------------------------------------------------------------------- #
# 2. Stooq downloader (Stooq domain only; stdlib urllib; <=2 retries; never crashes the run)
# --------------------------------------------------------------------------- #
def _stooq_symbol(ticker):
    return ticker.strip().lower() + ".us"


def _stooq_url(ticker):
    return _STOOQ_URL_TEMPLATE % _stooq_symbol(ticker)


def _looks_like_price_csv(text):
    """A valid Stooq daily CSV starts with a Date,Open,High,Low,Close,Volume header. Rate-limit /
    error responses ("No data", "Exceeded the daily hits limit", HTML) do not."""
    if not text:
        return False
    first = text.lstrip().splitlines()[0].lower() if text.strip() else ""
    return first.startswith("date") and "close" in first


def _classify_non_price(text):
    """Turn a non-CSV Stooq response into a short, honest, actionable error label (no fabrication).
    Stooq currently gates the free CSV endpoint behind a browser-verification challenge and may also
    deny automated access outright."""
    body = (text or "").strip()
    low = body.lower()
    if not body:
        return "empty response body"
    if "access denied" in low:
        return "stooq access denied (server refused automated CSV download)"
    if "requires javascript" in low or "__verify" in low or "noindex,nofollow" in low:
        return "stooq returned a JavaScript browser-verification challenge instead of CSV"
    if "exceeded" in low and "limit" in low:
        return "stooq daily hit limit exceeded"
    if low.startswith("no data") or low == "no data":
        return "stooq reported no data for symbol"
    return "non-price response: %s" % (" ".join(body.split())[:120])


def _download_one(ticker, dest_dir, max_retries=_MAX_RETRIES, timeout=_HTTP_TIMEOUT_SECONDS,
                  sleep_between=_SLEEP_BETWEEN_REQUESTS):
    """Download a single ticker's Stooq CSV with <=max_retries retries. Returns (ok, local_file,
    error). Saves the raw CSV only when the response looks like a real price CSV. Never raises."""
    url = _stooq_url(ticker)
    last_err = ""
    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=_REQUEST_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (Stooq only)
                raw = resp.read()
            text = raw.decode("utf-8", errors="replace")
            if _looks_like_price_csv(text):
                os.makedirs(dest_dir, exist_ok=True)
                local_file = os.path.join(dest_dir, "%s.csv" % ticker.upper())
                with open(local_file, "w", encoding="utf-8", newline="") as f:
                    f.write(text)
                return True, local_file, ""
            last_err = _classify_non_price(text)
        except urllib.error.HTTPError as e:
            last_err = "HTTP %s" % getattr(e, "code", "error")
        except urllib.error.URLError as e:
            last_err = "URL error: %s" % getattr(e, "reason", e)
        except (OSError, ValueError) as e:
            last_err = "download error: %s" % e
        if attempt < max_retries:
            time.sleep(sleep_between)
    return False, "", last_err


# --------------------------------------------------------------------------- #
# 3. Normalize a downloaded Stooq CSV (no fabricated rows)
# --------------------------------------------------------------------------- #
_NORMALIZED_COLS = ["ticker", "date", "open", "high", "low", "close", "adjusted_close",
                    "volume", "source"]


def _to_float(val):
    try:
        f = float(val)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _iso_date(val):
    s = (val or "").strip()
    if not s:
        return None
    # Stooq daily dates are already ISO (YYYY-MM-DD); accept that and a couple of fallbacks.
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalize_stooq_csv(local_file, ticker):
    """Parse + normalize a Stooq daily CSV into the common schema. Returns (rows, quality).
    Drops invalid rows; fabricates nothing."""
    rows = []
    dup = missing_close = nonpos_close = missing_vol = 0
    seen_dates = set()
    try:
        with open(local_file, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            field_map = {(k or "").strip().lower(): k for k in (reader.fieldnames or [])}

            def col(*names):
                for n in names:
                    if n in field_map:
                        return field_map[n]
                return None

            c_date = col("date")
            c_open = col("open")
            c_high = col("high")
            c_low = col("low")
            c_close = col("close", "adj close", "adjusted_close")
            c_vol = col("volume", "vol")
            for raw in reader:
                d = _iso_date(raw.get(c_date) if c_date else None)
                close = _to_float(raw.get(c_close) if c_close else None)
                if close is None:
                    missing_close += 1
                    continue
                if close <= 0:
                    nonpos_close += 1
                    continue
                if d is None:
                    continue
                if d in seen_dates:
                    dup += 1
                    continue
                seen_dates.add(d)
                vol = _to_float(raw.get(c_vol) if c_vol else None)
                if vol is None:
                    missing_vol += 1
                rows.append({
                    "ticker": ticker.upper(),
                    "date": d,
                    "open": _to_float(raw.get(c_open) if c_open else None),
                    "high": _to_float(raw.get(c_high) if c_high else None),
                    "low": _to_float(raw.get(c_low) if c_low else None),
                    "close": close,
                    "adjusted_close": close,  # Stooq daily CSV has no adjusted column
                    "volume": vol,
                    "source": "stooq",
                })
    except OSError as e:
        return [], {"duplicate_date_count": 0, "missing_close_count": 0,
                    "nonpositive_close_count": 0, "missing_volume_count": 0,
                    "quality_status": "READ_ERROR", "error": str(e)}

    rows.sort(key=lambda r: r["date"])
    if not rows:
        quality_status = "NO_USABLE_ROWS"
    elif nonpos_close or dup:
        quality_status = "OK_WITH_WARNINGS"
    else:
        quality_status = "OK"
    quality = {
        "duplicate_date_count": dup,
        "missing_close_count": missing_close,
        "nonpositive_close_count": nonpos_close,
        "missing_volume_count": missing_vol,
        "quality_status": quality_status,
    }
    return rows, quality


# --------------------------------------------------------------------------- #
# 4. Orchestrate downloads + normalization across the universe
# --------------------------------------------------------------------------- #
def collect_price_pack(universe, dest_dir=_STOOQ_INPUT_DIR, allow_network=True,
                       max_retries=_MAX_RETRIES, sleep_between=_SLEEP_BETWEEN_REQUESTS,
                       verbose=False):
    """Download + normalize each target. Returns a dict with manifest_rows, panel_rows,
    coverage_rows, quality_rows, missing_rows, per_ticker, stooq_attempted."""
    manifest_rows, quality_rows, coverage_rows, missing_rows = [], [], [], []
    panel_rows = []
    per_ticker = {}
    stooq_attempted = False

    for u in universe:
        tk = u["ticker"]
        ac = u.get("asset_class", "")
        region = u.get("region", "")
        sym = _stooq_symbol(tk)
        attempted = bool(allow_network)
        downloaded = False
        local_file = ""
        error = ""
        ndays = 0
        dmin = dmax = ""
        quality = {"duplicate_date_count": 0, "missing_close_count": 0,
                   "nonpositive_close_count": 0, "missing_volume_count": 0,
                   "quality_status": "NOT_DOWNLOADED"}

        if allow_network:
            stooq_attempted = True
            if verbose:
                print("  downloading %-5s (%s) ..." % (tk, sym))
            ok, local_file, error = _download_one(
                tk, dest_dir, max_retries=max_retries, sleep_between=sleep_between)
            downloaded = ok
            if ok:
                rows, quality = normalize_stooq_csv(local_file, tk)
                ndays = len(rows)
                if rows:
                    dmin = rows[0]["date"]
                    dmax = rows[-1]["date"]
                    panel_rows.extend(rows)
                else:
                    error = error or "downloaded but no usable rows after normalization"
            if not stooq_attempted:  # never reached, kept for clarity
                pass
            if u is not universe[-1]:
                time.sleep(sleep_between)
        else:
            error = "network disabled (allow_network=False)"

        usable = downloaded and ndays >= _MIN_USABLE_ROWS
        has_2016 = bool(dmin) and dmin <= _HISTORY_START_CUTOFF
        if not downloaded:
            coverage_status = "MISSING"
            blocker = error or "download failed"
        elif not usable:
            coverage_status = "SHORT_HISTORY"
            blocker = "fewer than %d usable daily rows" % _MIN_USABLE_ROWS
        elif ndays >= _LONG_HISTORY_MIN_ROWS and has_2016:
            coverage_status = "OK"
            blocker = ""
        else:
            coverage_status = "OK_SHORT_HISTORY"
            blocker = "" if has_2016 else "history starts after 2016"

        manifest_rows.append({
            "ticker": tk, "stooq_symbol": sym, "url_domain": STOOQ_DOMAIN,
            "attempted": attempted, "downloaded": downloaded,
            "local_file": _rel(local_file) if local_file else "",
            "row_count": ndays, "date_min": dmin, "date_max": dmax, "error": error,
        })
        quality_rows.append({"ticker": tk, **{k: v for k, v in quality.items()}})
        coverage_rows.append({
            "ticker": tk, "asset_class": ac, "region": region, "downloaded": downloaded,
            "row_count": ndays, "date_min": dmin, "date_max": dmax,
            "has_2016_start": has_2016, "coverage_status": coverage_status, "blocker": blocker,
        })
        if not usable:
            missing_rows.append({
                "ticker": tk, "asset_class": ac,
                "reason": blocker or "not usable",
                "next_action": "retry Stooq download for %s in Phase 3-W (Stooq only)" % sym,
            })
        per_ticker[tk] = {"usable": usable, "asset_class": ac, "row_count": ndays,
                          "downloaded": downloaded}

    return {
        "manifest_rows": manifest_rows, "panel_rows": panel_rows,
        "coverage_rows": coverage_rows, "quality_rows": quality_rows,
        "missing_rows": missing_rows, "per_ticker": per_ticker,
        "stooq_attempted": stooq_attempted,
    }


# --------------------------------------------------------------------------- #
# 5. Cross-asset feature readiness
# --------------------------------------------------------------------------- #
def build_feature_readiness(per_ticker):
    usable = {tk for tk, info in per_ticker.items() if info.get("usable")}
    rows = []
    for family, req in _feature_defs():
        have = [t for t in req if t in usable]
        missing = [t for t in req if t not in usable]
        ready = len(missing) == 0
        rows.append({
            "feature_family": family,
            "required_tickers": ";".join(req),
            "available_tickers": ";".join(have),
            "missing_tickers": ";".join(missing),
            "ready": ready,
            "blocker": "" if ready else "missing usable local price history for: %s"
                                        % ", ".join(missing),
        })
    return rows


# --------------------------------------------------------------------------- #
# 6. Decision
# --------------------------------------------------------------------------- #
def decide(usable_count, asset_classes_covered):
    if usable_count >= READY_MIN_TICKERS and asset_classes_covered >= READY_MIN_ASSET_CLASSES:
        return REC_READY
    if usable_count >= PARTIAL_MIN_TICKERS:
        return REC_PARTIAL
    return REC_BLOCKED


_NEXT_PHASE = {
    REC_READY: ("3-W", "Build Cross-Asset Feature Factory",
                "Enough global ETF price history is now local; build the point-in-time cross-asset "
                "feature factory from the assembled panel."),
    REC_PARTIAL: ("3-W", "Repair Missing Global ETF Price Data",
                  "A usable core downloaded but some proxies are missing; repair the remaining "
                  "Stooq downloads before/with building the feature factory."),
    REC_BLOCKED: ("3-W", "Repair Phase 3-V Downloads",
                  "Too few proxies downloaded usably; repair the Stooq downloads (Stooq only) "
                  "before building the feature factory."),
}


def build_recommended_next_phase(rec):
    phase, title, purpose = _NEXT_PHASE[rec]
    return {"phase": phase, "title": title, "purpose": purpose}


def build_recommendation(rec, target_count, downloaded_count, usable_count, classes):
    reasons = {
        REC_READY:
            "%d of %d target proxies downloaded and usable across %d asset classes (>= %d tickers "
            "and >= %d classes); ready to build the cross-asset feature factory."
            % (usable_count, target_count, classes, READY_MIN_TICKERS, READY_MIN_ASSET_CLASSES),
        REC_PARTIAL:
            "%d of %d target proxies downloaded and usable (>= %d) but not enough for READY "
            "(>= %d tickers across >= %d classes); repair the remaining downloads."
            % (usable_count, target_count, PARTIAL_MIN_TICKERS, READY_MIN_TICKERS,
               READY_MIN_ASSET_CLASSES),
        REC_BLOCKED:
            "Only %d of %d target proxies downloaded usably (< %d); repair the Stooq downloads "
            "(Stooq only) before proceeding."
            % (usable_count, target_count, PARTIAL_MIN_TICKERS),
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
        "downloaded_count": downloaded_count,
        "usable_count": usable_count,
        "reason": reasons[rec],
    }


# --------------------------------------------------------------------------- #
# 7. Decision table / safety flags / interpretation
# --------------------------------------------------------------------------- #
def build_decision_table(p3u, target_count, downloaded_count, usable_count, classes, rec,
                         stooq_attempted):
    rows = []

    def add(item, value, passed, note):
        rows.append({"decision_item": item, "value": value,
                     "passed": "" if passed is None else bool(passed), "note": note})

    add("phase3u_present", p3u["phase3u_present"], p3u["phase3u_present"],
        "Phase 3-U result readable (read-only)")
    add("phase3u_is_3u", p3u["phase3u_is_3u"], p3u["phase3u_is_3u"], "Phase 3-U result phase == 3-U")
    add("phase3u_recommendation_accepted", p3u["phase3u_recommendation"],
        p3u["phase3u_recommendation_accepted"],
        "Phase 3-U recommended acquiring local price data")
    add("target_universe_size", target_count, target_count >= 20,
        "at least 20 cross-asset proxies targeted")
    add("stooq_only_network", STOOQ_DOMAIN, True, "downloads use the Stooq domain only")
    add("downloaded_count", downloaded_count, None, "proxies with a saved Stooq CSV")
    add("usable_count", usable_count, None, "proxies with >= %d usable daily rows" % _MIN_USABLE_ROWS)
    add("asset_classes_covered", classes, None, "distinct asset classes among usable proxies")
    add("ready_threshold_met", usable_count >= READY_MIN_TICKERS and classes >= READY_MIN_ASSET_CLASSES,
        usable_count >= READY_MIN_TICKERS and classes >= READY_MIN_ASSET_CLASSES,
        "READY needs >= %d usable proxies across >= %d asset classes"
        % (READY_MIN_TICKERS, READY_MIN_ASSET_CLASSES))
    add("no_price_data_faked", True, True, "invalid rows dropped; no rows fabricated")
    add("non_stooq_network_called", False, True, "no non-Stooq network source contacted")
    add("alpha_vantage_called", False, True, "no Alpha Vantage call")
    add("fred_called", False, True, "no FRED call")
    add("yfinance_called", False, True, "no yfinance use")
    add("d_drive_written", False, True, "nothing written to the data drive")
    add("phase3m_outputs_modified", False, True, "Phase 3-M outputs untouched")
    add("phase3u_outputs_modified", False, True, "Phase 3-U outputs untouched")
    add("no_production_model_candidate", True, True, "data preparation only")
    add("no_production_predictions_or_scores", True, True, "no predictions / scores computed")
    add("no_portfolio_weights", True, True, "no portfolio weights computed")
    add("no_deployable_artifact", True, True, "no serialized model file written")
    add("recommendation", rec, rec in ALLOWED_RECOMMENDATIONS, "allowed values only")
    return rows


def build_safety_flags(stooq_attempted):
    return {
        "research_only": True,
        "database_touched": False,
        "database_write_executed": False,
        "migration_executed": False,
        "deployment_executed": False,
        "model_v2_enabled": False,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "deployable_model_artifact_written": False,
        "production_predictions_computed": False,
        "production_scores_computed": False,
        "portfolio_weights_computed": False,
        "order_instructions_created": False,
        "production_edge_claimed": False,
        "no_trading": True,
        "no_orders": True,
        "no_automation": True,
        "d_drive_read": False,
        "d_drive_written": False,
        "alpha_vantage_called": False,
        "fred_called": False,
        "yfinance_called": False,
        "paid_vendor_api_called": False,
        "stooq_called": bool(stooq_attempted),
        "non_stooq_network_called": False,
        "data_faked": False,
        "labels_for_validation_only": True,
    }


def build_interpretation(rec, target_count, downloaded_count, usable_count, classes):
    return {
        "phase_is_research_only": True,
        "research_only": True,
        "local_data_preparation_only": True,
        "price_data_faked": False,
        "non_stooq_network_called": False,
        "provider_paid_api_called": False,
        "production_predictions_computed": False,
        "production_scores_computed": False,
        "portfolio_weights_computed": False,
        "deployable_model_artifact_written": False,
        "production_edge_claimed": False,
        "results_remain_survivorship_biased": True,
        "narrative":
            "Phase 3-V is the research-only, local data-preparation pack that acquires and "
            "normalizes real daily price history for the Phase 3-U global cross-asset ETF proxy "
            "universe using free public Stooq CSV downloads only (no yfinance, no Alpha Vantage, "
            "no FRED, no paid vendor API, no non-Stooq network). It downloaded %d of %d target "
            "proxies, %d of which are usable across %d asset classes, normalizes them to a common "
            "OHLCV schema (adjusted_close uses close because Stooq daily CSV has no adjusted "
            "column), drops invalid rows, and fabricates nothing. It trains no model, computes no "
            "predictions / scores / portfolio weights / order instructions, writes no deployable "
            "artifact, touches no database, runs no migration, restarts no service, enables no "
            "serving flag, and writes nothing to the data drive. Recommendation: %s. The universe "
            "is current-as-of, so downstream results remain survivorship-biased and claim no "
            "production edge."
            % (downloaded_count, target_count, usable_count, classes, rec),
    }


# --------------------------------------------------------------------------- #
# Output column specs
# --------------------------------------------------------------------------- #
_OUT_PATHS = {
    "manifest": "download_manifest.csv",
    "panel": "global_etf_price_panel.csv",
    "coverage": "global_etf_price_coverage.csv",
    "quality": "global_etf_price_quality.csv",
    "missing": "missing_or_failed_downloads.csv",
    "feature_readiness": "cross_asset_feature_readiness.csv",
    "decision": "readiness_decision_table.csv",
}
_MANIFEST_COLS = ["ticker", "stooq_symbol", "url_domain", "attempted", "downloaded", "local_file",
                  "row_count", "date_min", "date_max", "error"]
_PANEL_COLS = list(_NORMALIZED_COLS)
_COVERAGE_COLS = ["ticker", "asset_class", "region", "downloaded", "row_count", "date_min",
                  "date_max", "has_2016_start", "coverage_status", "blocker"]
_QUALITY_COLS = ["ticker", "duplicate_date_count", "missing_close_count", "nonpositive_close_count",
                 "missing_volume_count", "quality_status"]
_MISSING_COLS = ["ticker", "asset_class", "reason", "next_action"]
_FEATURE_COLS = ["feature_family", "required_tickers", "available_tickers", "missing_tickers",
                 "ready", "blocker"]
_DECISION_COLS = ["decision_item", "value", "passed", "note"]


def _out(o_dir, key):
    return os.path.join(o_dir, _OUT_PATHS[key])


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run(result_json_path=RESULT_JSON, o_dir=_V_DIR, universe_csv=PHASE3U_UNIVERSE_CSV,
        phase3u_result_json=PHASE3U_RESULT_JSON, dest_dir=_STOOQ_INPUT_DIR, allow_network=True,
        max_retries=_MAX_RETRIES, sleep_between=_SLEEP_BETWEEN_REQUESTS, verbose=True):
    p3u = confirm_phase3u(phase3u_result_json)
    universe = load_target_universe(universe_csv)
    target_count = len(universe)

    if verbose:
        print("  Phase 3-U recommendation: %s (accepted: %s)"
              % (p3u["phase3u_recommendation"], p3u["phase3u_recommendation_accepted"]))
        print("  target universe: %d proxies" % target_count)
        print("  acquiring Stooq daily CSVs (Stooq only) ..." if allow_network
              else "  network disabled; no downloads attempted")

    pack = collect_price_pack(universe, dest_dir=dest_dir, allow_network=allow_network,
                              max_retries=max_retries, sleep_between=sleep_between, verbose=verbose)

    per_ticker = pack["per_ticker"]
    feature_rows = build_feature_readiness(per_ticker)

    downloaded_count = sum(1 for info in per_ticker.values() if info["downloaded"])
    usable = [tk for tk, info in per_ticker.items() if info["usable"]]
    usable_count = len(usable)
    classes_covered = sorted({per_ticker[tk]["asset_class"] for tk in usable
                              if per_ticker[tk]["asset_class"]})
    classes = len(classes_covered)
    missing_count = target_count - usable_count

    rec = decide(usable_count, classes)
    decision_rows = build_decision_table(p3u, target_count, downloaded_count, usable_count, classes,
                                         rec, pack["stooq_attempted"])

    # Write CSV outputs (all under research/output/phase3v...; never to the data drive).
    _write_csv(_out(o_dir, "manifest"), _MANIFEST_COLS, pack["manifest_rows"])
    _write_csv(_out(o_dir, "panel"), _PANEL_COLS, pack["panel_rows"])
    _write_csv(_out(o_dir, "coverage"), _COVERAGE_COLS, pack["coverage_rows"])
    _write_csv(_out(o_dir, "quality"), _QUALITY_COLS, pack["quality_rows"])
    _write_csv(_out(o_dir, "missing"), _MISSING_COLS, pack["missing_rows"])
    _write_csv(_out(o_dir, "feature_readiness"), _FEATURE_COLS, feature_rows)
    _write_csv(_out(o_dir, "decision"), _DECISION_COLS, decision_rows)

    result = {
        "phase": PHASE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "phase3u_result_json": _rel(phase3u_result_json),
            "phase3u_universe_csv": _rel(universe_csv),
        },
        "outputs_written": {
            "result_json": _rel(result_json_path),
            **{k: _rel(_out(o_dir, k)) for k in _OUT_PATHS},
            "stooq_input_dir": _rel(dest_dir),
        },
        "phase3u_confirmation": p3u,
        "target_count": target_count,
        "downloaded_count": downloaded_count,
        "usable_count": usable_count,
        "missing_count": missing_count,
        "asset_classes_covered": classes,
        "asset_classes_covered_list": classes_covered,
        "usable_tickers": sorted(usable),
        "panel_row_count": len(pack["panel_rows"]),
        "feature_families_ready": [r["feature_family"] for r in feature_rows if r["ready"]],
        "feature_readiness_summary": {
            "ready": sum(1 for r in feature_rows if r["ready"]),
            "blocked": sum(1 for r in feature_rows if not r["ready"]),
            "total": len(feature_rows),
        },
        "readiness_decision_table": decision_rows,
        "recommendation": build_recommendation(rec, target_count, downloaded_count, usable_count,
                                               classes),
        "recommended_next_phase": build_recommended_next_phase(rec),
        "interpretation": build_interpretation(rec, target_count, downloaded_count, usable_count,
                                               classes),
    }
    result.update(build_safety_flags(pack["stooq_attempted"]))
    _dump_json(result_json_path, result)
    return result


def main():
    result = run()
    rec = result["recommendation"]["recommendation"]
    print("phase:                      %s" % result["phase"])
    print("target count:               %s" % result["target_count"])
    print("downloaded count:           %s" % result["downloaded_count"])
    print("usable count:               %s" % result["usable_count"])
    print("missing count:              %s" % result["missing_count"])
    print("asset classes covered:      %s (%s)"
          % (result["asset_classes_covered"], ", ".join(result["asset_classes_covered_list"])
             or "none"))
    print("panel rows:                 %s" % result["panel_row_count"])
    print("feature families ready:     %s" % (", ".join(result["feature_families_ready"]) or "none"))
    print("recommendation:             %s" % rec)
    print("recommended next:           %s - %s"
          % (result["recommended_next_phase"]["phase"], result["recommended_next_phase"]["title"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
