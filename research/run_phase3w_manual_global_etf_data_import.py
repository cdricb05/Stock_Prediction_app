"""Phase 3-W - Manual Global ETF Price Data Intake and Repair Gate.

Phase 3-V tried to acquire the 22 global cross-asset ETF / proxy price histories from free public
Stooq CSV downloads and was BLOCKED (Stooq gated its free CSV endpoint behind a browser-verification
challenge / access denial). Phase 3-W replaces the network path with a LOCAL, MANUAL intake path:
the user manually downloads or exports real CSV price files from a legitimate browser / broker /
vendor source, drops them into a local folder, and this phase normalizes, validates, and scores
readiness for cross-asset feature engineering.

This phase calls NO network at all. It does NOT use Stooq, yfinance, Alpha Vantage, FRED, or any paid
API. It reads no provider API key, touches no database, runs no migration, restarts no service,
enables no model-v2 serving flag, writes nothing to the D: drive, places no orders, trades nothing,
trains no model, creates no production model candidate, computes no predictions / scores / portfolio
weights, writes no deployable model artifact, and never fabricates a price row.

What it does:
  1. Reads the Phase 3-V result (read-only) + the Phase 3-U target universe (read-only); confirms
     Phase 3-V recommended a manual repair path.
  2. Ensures the manual input folder, a README, and a clearly-labelled SAMPLE template exist.
  3. Inventories any manual CSV files the user has placed (ignoring the README / template).
  4. Normalizes accepted formats into a common schema (ticker, date, open, high, low, close,
     adjusted_close, volume, source_file, source_type); invalid rows dropped; nothing fabricated.
  5. Writes inventory, the combined panel, per-ticker coverage + quality, a missing/bad-format
     table, a manual download-instructions table, a cross-asset feature-readiness table, and a
     decision table - plus a result JSON.

Run:
    python -B research/run_phase3w_manual_global_etf_data_import.py
Test:
    python -B tests/test_phase3w_manual_global_etf_data_import.py
"""
from __future__ import annotations

import csv
import json
import math
import os
from datetime import datetime

PHASE = "3-W"

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)

# --------------------------------------------------------------------------- #
# Paths (everything on C:; nothing is read from or written to the D: data drive)
# --------------------------------------------------------------------------- #
_OUT_DIR = os.path.join(_REPO_ROOT, "research", "output")
_W_DIR = os.path.join(_OUT_DIR, "phase3w_manual_global_etf_data_import")
RESULT_JSON = os.path.join(_OUT_DIR, "phase3w_manual_global_etf_data_import.json")

# Manual CSV drop folder (local input; allowed new directory). The user places files here.
_MANUAL_INPUT_DIR = os.path.join(_REPO_ROOT, "research", "input", "global_assets", "manual")
_MANUAL_README = os.path.join(_MANUAL_INPUT_DIR, "README.md")
_MANUAL_TEMPLATE = os.path.join(_MANUAL_INPUT_DIR, "manual_global_etf_price_template.csv")
_COMBINED_FILENAME = "global_etf_prices.csv"

# Phase 3-V inputs (read-only).
_V_DIR = os.path.join(_OUT_DIR, "phase3v_global_etf_price_data_pack")
PHASE3V_RESULT_JSON = os.path.join(_OUT_DIR, "phase3v_global_etf_price_data_pack.json")

# Phase 3-U inputs (read-only).
_U_DIR = os.path.join(_OUT_DIR, "phase3u_global_asset_universe_readiness")
PHASE3U_UNIVERSE_CSV = os.path.join(_U_DIR, "target_global_asset_universe.csv")

# Phase 3-M earnings outputs - referenced only so tests can prove they are never written.
_M_DIR = os.path.join(_OUT_DIR, "phase3m_earnings_estimates_signal_gate")

# --------------------------------------------------------------------------- #
# Phase 3-V recommendations this phase legitimately follows.
# --------------------------------------------------------------------------- #
_ACCEPTED_PHASE3V_RECS = {
    "GLOBAL_ETF_PRICE_DATA_BLOCKED_DOWNLOAD_FAILURE",
    "GLOBAL_ETF_PRICE_DATA_PARTIAL_DOWNLOAD",
}

# --------------------------------------------------------------------------- #
# Decision values
# --------------------------------------------------------------------------- #
REC_READY = "MANUAL_GLOBAL_ETF_DATA_READY"
REC_PARTIAL = "MANUAL_GLOBAL_ETF_DATA_PARTIAL"
REC_WAITING = "MANUAL_GLOBAL_ETF_DATA_WAITING_FOR_FILES"
REC_BAD_FORMAT = "MANUAL_GLOBAL_ETF_DATA_BAD_FORMAT"
ALLOWED_RECOMMENDATIONS = (REC_READY, REC_PARTIAL, REC_WAITING, REC_BAD_FORMAT)

READY_MIN_TICKERS = 12
READY_MIN_ASSET_CLASSES = 5
PARTIAL_MIN_TICKERS = 5

# A ticker is "usable" only with a reasonable amount of clean daily history.
_MIN_USABLE_ROWS = 250          # ~1 trading year
_LONG_HISTORY_MIN_ROWS = 750    # ~3 trading years -> coverage_status OK
_HISTORY_START_CUTOFF = "2016-12-31"   # history begins in/before 2016 -> has_2016_start

# Fixed 22-ticker fallback universe (only used if the Phase 3-U universe CSV cannot be read).
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
# Cross-asset feature families (required tickers; same blueprint as Phase 3-V)
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


def _write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


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


def _to_float(val):
    try:
        f = float(str(val).replace(",", "").strip())
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _iso_date(val):
    s = (str(val) if val is not None else "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
# 1. Confirm Phase 3-V + load target universe
# --------------------------------------------------------------------------- #
def confirm_phase3v(result_json=PHASE3V_RESULT_JSON):
    """Read the Phase 3-V result (read-only) and confirm it recommended a manual repair path.
    Phase 3-V outputs are never modified here."""
    res = _load_json(result_json) or {}
    phase = res.get("phase")
    rec = ((res.get("recommendation") or {}).get("recommendation")
           if isinstance(res.get("recommendation"), dict) else res.get("recommendation"))
    return {
        "phase3v_present": bool(res),
        "phase3v_phase": phase,
        "phase3v_is_3v": phase == "3-V",
        "phase3v_recommendation": rec,
        "phase3v_recommendation_accepted": rec in _ACCEPTED_PHASE3V_RECS,
        "note": "Phase 3-V's Stooq downloads were blocked / partial; Phase 3-W provides a local "
                "manual CSV intake path. Phase 3-V and Phase 3-U outputs are read-only here.",
    }


def load_target_universe(universe_csv=PHASE3U_UNIVERSE_CSV):
    """Load the Phase 3-U target universe CSV. Returns (rows, fallback_used). Falls back to the
    embedded 22-ticker definition only if the CSV is unreadable."""
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
                        "notes": (r.get("notes") or "").strip(),
                    })
        except OSError:
            rows = []
    if rows:
        return rows, False
    rows = [{"ticker": t, "asset_class": ac, "region": rg, "notes": ""}
            for (t, ac, rg) in _FALLBACK_UNIVERSE]
    return rows, True


# --------------------------------------------------------------------------- #
# 2. Manual scaffolding (README + clearly-labelled SAMPLE template; no real ticker data)
# --------------------------------------------------------------------------- #
def _readme_text(target_tickers):
    listed = " ".join(target_tickers)
    return (
        "# Manual Global ETF Price Data Intake (Phase 3-W)\n\n"
        "Phase 3-V could not download these price histories automatically (the free Stooq CSV\n"
        "endpoint blocked automated access). Phase 3-W lets you supply the data **manually** from a\n"
        "legitimate source you already have access to.\n\n"
        "This phase calls **no network**. It only reads CSV files you place in this folder. All\n"
        "price data must be **real** - do not fabricate rows.\n\n"
        "## Where to put files\n\n"
        "Place CSV files directly in this folder:\n\n"
        "    research/input/global_assets/manual/\n\n"
        "## Accepted file layouts\n\n"
        "1. **One file per ticker**, named `<TICKER>.csv` - e.g. `SPY.csv`, `QQQ.csv`, `TLT.csv`.\n"
        "2. **One combined file** named `global_etf_prices.csv` with a `ticker` column.\n\n"
        "The `README.md` and `manual_global_etf_price_template.csv` files are ignored by the scan.\n\n"
        "## Accepted column variants (header row, case-insensitive)\n\n"
        "    Date, Open, High, Low, Close, Adj Close, Volume\n"
        "    date, open, high, low, close, adjusted_close, volume\n"
        "    ticker, date, open, high, low, close, adjusted_close, volume\n"
        "    ticker, date, adjusted_open, adjusted_high, adjusted_low, adjusted_close, volume\n\n"
        "`adjusted_close` uses `adjusted_close`/`Adj Close` if present, otherwise `close`.\n\n"
        "## Acceptable sources (manual only)\n\n"
        "- A browser-downloaded CSV from a legitimate historical price page.\n"
        "- A broker export.\n"
        "- A data-vendor export you already have access to.\n\n"
        "Do not use paid API automation or any network fetch - this phase is manual intake only.\n\n"
        "## Target tickers (22)\n\n"
        "    " + listed + "\n\n"
        "After adding files, re-run:\n\n"
        "    python -B research/run_phase3w_manual_global_etf_data_import.py\n"
    )


def _template_text():
    # Headers + two clearly-labelled SAMPLE rows only. No real ticker data is fabricated.
    return (
        "ticker,date,open,high,low,close,adjusted_close,volume\n"
        "SAMPLE,2016-01-04,100.0,101.0,99.5,100.5,100.5,1000000\n"
        "SAMPLE,2016-01-05,100.5,102.0,100.0,101.75,101.75,1200000\n"
    )


def ensure_manual_scaffolding(manual_dir=_MANUAL_INPUT_DIR, target_tickers=None):
    """Create the manual input folder, README, and SAMPLE template if missing. Returns the list of
    scaffolding basenames (always ignored by the inventory scan)."""
    os.makedirs(manual_dir, exist_ok=True)
    target_tickers = target_tickers or [t for (t, _ac, _rg) in _FALLBACK_UNIVERSE]
    readme = os.path.join(manual_dir, "README.md")
    template = os.path.join(manual_dir, "manual_global_etf_price_template.csv")
    if not os.path.isfile(readme):
        _write_text(readme, _readme_text(target_tickers))
    if not os.path.isfile(template):
        _write_text(template, _template_text())
    return ["README.md", "manual_global_etf_price_template.csv"]


_SCAFFOLDING_BASENAMES = {"readme.md", "manual_global_etf_price_template.csv"}


# --------------------------------------------------------------------------- #
# 3 + 4. Parse / normalize a manual CSV (no fabricated rows)
# --------------------------------------------------------------------------- #
_NORMALIZED_COLS = ["ticker", "date", "open", "high", "low", "close", "adjusted_close",
                    "volume", "source_file", "source_type"]


def _classify_format(cols_lower):
    has = cols_lower.__contains__
    if "date" not in cols_lower:
        return "unknown"
    if has("ticker") and has("adjusted_open"):
        return "ticker_adjusted_ohlc"
    if has("ticker"):
        return "ticker_ohlc_adjusted"
    if has("adjusted_close"):
        return "ohlc_adjusted_lowercase"
    if "adj close" in cols_lower:
        return "ohlc_adj_close_titlecase"
    if has("close"):
        return "ohlc_basic"
    return "unknown"


def _ticker_from_filename(path):
    base = os.path.splitext(os.path.basename(path))[0]
    return base.strip().upper()


def parse_manual_file(path):
    """Parse a single manual CSV into normalized rows + per-file metadata. Returns a dict with:
    columns, detected_format, rows (normalized, sorted), tickers (sorted list), source_type,
    counters {duplicate, missing_adjusted_close, nonpositive_adjusted_close, missing_volume},
    raw_row_count, blocker. Drops invalid rows; fabricates nothing. Never raises."""
    meta = {
        "columns": [], "detected_format": "unknown", "rows": [], "tickers": [],
        "source_type": "per_ticker",
        "counters": {"duplicate": 0, "missing_adjusted_close": 0,
                     "nonpositive_adjusted_close": 0, "missing_volume": 0},
        "raw_row_count": 0, "blocker": "",
    }
    base = os.path.basename(path)
    file_ticker = _ticker_from_filename(path)
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            header = list(reader.fieldnames or [])
            meta["columns"] = header
            cols_lower = {(c or "").strip().lower() for c in header}
            field_map = {(c or "").strip().lower(): c for c in header}
            meta["detected_format"] = _classify_format(cols_lower)

            def col(*names):
                for n in names:
                    if n in field_map:
                        return field_map[n]
                return None

            c_ticker = col("ticker", "symbol")
            c_date = col("date")
            c_open = col("open", "adjusted_open")
            c_high = col("high", "adjusted_high")
            c_low = col("low", "adjusted_low")
            c_close = col("close")
            c_adj = col("adjusted_close", "adj close", "adjclose")
            c_vol = col("volume", "vol")

            if c_date is None or (c_close is None and c_adj is None):
                meta["blocker"] = "missing required date and/or close/adjusted_close columns"
                return meta

            seen = {}            # ticker -> set(dates)
            counters = meta["counters"]
            tickers = set()
            raw = 0
            for row in reader:
                raw += 1
                tk = (row.get(c_ticker) if c_ticker else None)
                tk = (tk or "").strip().upper() if tk else file_ticker
                if not tk:
                    tk = file_ticker
                d = _iso_date(row.get(c_date))
                raw_close = _to_float(row.get(c_close)) if c_close else None
                raw_adj = _to_float(row.get(c_adj)) if c_adj else None
                adjusted_close = raw_adj if raw_adj is not None else raw_close
                close = raw_close if raw_close is not None else raw_adj
                if adjusted_close is None:
                    counters["missing_adjusted_close"] += 1
                    continue
                if adjusted_close <= 0:
                    counters["nonpositive_adjusted_close"] += 1
                    continue
                if d is None:
                    continue
                dates = seen.setdefault(tk, set())
                if d in dates:
                    counters["duplicate"] += 1
                    continue
                dates.add(d)
                vol = _to_float(row.get(c_vol)) if c_vol else None
                if vol is None:
                    counters["missing_volume"] += 1
                tickers.add(tk)
                meta["rows"].append({
                    "ticker": tk, "date": d,
                    "open": _to_float(row.get(c_open)) if c_open else None,
                    "high": _to_float(row.get(c_high)) if c_high else None,
                    "low": _to_float(row.get(c_low)) if c_low else None,
                    "close": close, "adjusted_close": adjusted_close, "volume": vol,
                    "source_file": base, "source_type": "",  # filled after source_type known
                })
            meta["raw_row_count"] = raw
    except OSError as e:
        meta["blocker"] = "read error: %s" % e
        return meta
    except (csv.Error, ValueError) as e:
        meta["blocker"] = "parse error: %s" % e
        return meta

    meta["tickers"] = sorted(tickers)
    meta["source_type"] = "combined" if len(tickers) > 1 else "per_ticker"
    for r in meta["rows"]:
        r["source_type"] = meta["source_type"]
    meta["rows"].sort(key=lambda r: (r["ticker"], r["date"]))
    if not meta["rows"] and not meta["blocker"]:
        meta["blocker"] = "no usable rows after normalization"
    return meta


def _list_manual_files(manual_dir):
    if not os.path.isdir(manual_dir):
        return []
    out = []
    for name in sorted(os.listdir(manual_dir)):
        p = os.path.join(manual_dir, name)
        if not os.path.isfile(p):
            continue
        if name.lower() in _SCAFFOLDING_BASENAMES:
            continue
        if not name.lower().endswith(".csv"):
            continue
        out.append(p)
    return out


def inventory_manual_files(manual_dir=_MANUAL_INPUT_DIR):
    """Scan the manual folder (ignoring README / template), parse each CSV, and return
    (inventory_rows, parsed) where parsed maps file path -> parse_manual_file() metadata."""
    inventory_rows = []
    parsed = {}
    for p in _list_manual_files(manual_dir):
        meta = parse_manual_file(p)
        parsed[p] = meta
        rows = meta["rows"]
        dmin = rows[0]["date"] if rows else ""
        dmax = max((r["date"] for r in rows), default="") if rows else ""
        if meta["source_type"] == "combined":
            detected_ticker = "MULTIPLE(%d)" % len(meta["tickers"])
        else:
            detected_ticker = meta["tickers"][0] if meta["tickers"] else _ticker_from_filename(p)
        inventory_rows.append({
            "file_path": _rel(p),
            "detected_ticker": detected_ticker,
            "detected_format": meta["detected_format"],
            "columns": ";".join(meta["columns"]),
            "row_count": len(rows),
            "date_min": dmin,
            "date_max": dmax,
            "usable": bool(rows) and not meta["blocker"],
            "blocker": meta["blocker"],
        })
    return inventory_rows, parsed


# --------------------------------------------------------------------------- #
# 5. Coverage + quality (per target ticker)
# --------------------------------------------------------------------------- #
def normalize_manual_files(parsed):
    """Aggregate parsed per-file rows into a combined panel + per-ticker aggregates. Returns
    (panel_rows, by_ticker) where by_ticker maps ticker -> {row_count, date_min, date_max,
    duplicate, missing_adjusted_close, nonpositive_adjusted_close, missing_volume}."""
    panel_rows = []
    by_ticker = {}
    # Per-ticker dedup across files (same date from two files counts once).
    seen = {}
    for meta in parsed.values():
        # Attribute file-level quality counters to each ticker present in the file.
        for r in meta["rows"]:
            tk = r["ticker"]
            dates = seen.setdefault(tk, set())
            agg = by_ticker.setdefault(tk, {
                "row_count": 0, "date_min": "", "date_max": "",
                "duplicate": 0, "missing_adjusted_close": 0,
                "nonpositive_adjusted_close": 0, "missing_volume": 0,
            })
            if r["date"] in dates:
                agg["duplicate"] += 1
                continue
            dates.add(r["date"])
            panel_rows.append(r)
            agg["row_count"] += 1
            if r["volume"] is None:
                agg["missing_volume"] += 1
    # File-level missing/nonpositive counters (rows that never made it into a ticker) folded in.
    for meta in parsed.values():
        c = meta["counters"]
        file_tickers = meta["tickers"] or []
        if len(file_tickers) == 1:
            agg = by_ticker.get(file_tickers[0])
            if agg is not None:
                agg["missing_adjusted_close"] += c["missing_adjusted_close"]
                agg["nonpositive_adjusted_close"] += c["nonpositive_adjusted_close"]
    panel_rows.sort(key=lambda r: (r["ticker"], r["date"]))
    for tk, agg in by_ticker.items():
        dates = sorted(seen.get(tk, set()))
        agg["date_min"] = dates[0] if dates else ""
        agg["date_max"] = dates[-1] if dates else ""
    return panel_rows, by_ticker


def build_coverage_quality(universe, by_ticker):
    coverage_rows, quality_rows, per_ticker = [], [], {}
    for u in universe:
        tk = u["ticker"]
        ac = u.get("asset_class", "")
        region = u.get("region", "")
        agg = by_ticker.get(tk)
        n = agg["row_count"] if agg else 0
        dmin = agg["date_min"] if agg else ""
        dmax = agg["date_max"] if agg else ""
        locally_available = n > 0
        has_2016 = bool(dmin) and dmin <= _HISTORY_START_CUTOFF
        usable = locally_available and n >= _MIN_USABLE_ROWS
        if not locally_available:
            coverage_status, blocker = "MISSING", "no manual CSV supplied for this ticker"
        elif not usable:
            coverage_status = "SHORT_HISTORY"
            blocker = "fewer than %d usable daily rows" % _MIN_USABLE_ROWS
        elif n >= _LONG_HISTORY_MIN_ROWS and has_2016:
            coverage_status, blocker = "OK", ""
        else:
            coverage_status = "OK_SHORT_HISTORY"
            blocker = "" if has_2016 else "history starts after 2016"
        coverage_rows.append({
            "ticker": tk, "asset_class": ac, "region": region,
            "locally_available": locally_available, "row_count": n,
            "date_min": dmin, "date_max": dmax, "has_2016_start": has_2016,
            "coverage_status": coverage_status, "blocker": blocker,
        })
        dup = agg["duplicate"] if agg else 0
        miss_adj = agg["missing_adjusted_close"] if agg else 0
        nonpos = agg["nonpositive_adjusted_close"] if agg else 0
        miss_vol = agg["missing_volume"] if agg else 0
        if not locally_available:
            quality_status = "NO_FILE"
        elif nonpos or dup or miss_adj:
            quality_status = "OK_WITH_WARNINGS"
        else:
            quality_status = "OK"
        quality_rows.append({
            "ticker": tk, "duplicate_date_count": dup,
            "missing_adjusted_close_count": miss_adj,
            "nonpositive_adjusted_close_count": nonpos,
            "missing_volume_count": miss_vol, "quality_status": quality_status,
        })
        per_ticker[tk] = {"usable": usable, "asset_class": ac, "row_count": n,
                          "locally_available": locally_available,
                          "coverage_status": coverage_status}
    return coverage_rows, quality_rows, per_ticker


# --------------------------------------------------------------------------- #
# 6. Missing / bad-format table
# --------------------------------------------------------------------------- #
def build_missing_or_bad_format(universe, per_ticker, inventory_rows):
    rows = []
    for u in universe:
        tk = u["ticker"]
        info = per_ticker.get(tk, {})
        if info.get("usable"):
            continue
        if not info.get("locally_available"):
            rows.append({
                "ticker_or_file": tk, "issue_type": "missing_file",
                "reason": "no manual CSV supplied for %s" % tk,
                "next_action": "place %s.csv under research/input/global_assets/manual/" % tk,
            })
        else:
            rows.append({
                "ticker_or_file": tk, "issue_type": "short_history",
                "reason": "only %d usable daily rows (< %d)"
                          % (info.get("row_count", 0), _MIN_USABLE_ROWS),
                "next_action": "supply a longer history CSV for %s "
                               "(ideally back to 2016 or earlier)" % tk,
            })
    for inv in inventory_rows:
        if inv["blocker"]:
            rows.append({
                "ticker_or_file": inv["file_path"], "issue_type": "bad_format",
                "reason": inv["blocker"],
                "next_action": "fix the CSV header/columns to an accepted variant and re-run",
            })
    return rows


# --------------------------------------------------------------------------- #
# 7. Manual download instructions (generic / manual sources only; no paid APIs, no automation)
# --------------------------------------------------------------------------- #
_ACCEPTABLE_SOURCES = ("browser-downloaded CSV from a legitimate historical price page; "
                       "broker export; data-vendor export already available to you")
_REQUIRED_COLUMNS_HINT = "date,open,high,low,close,adjusted_close,volume"


def build_manual_instructions(universe, per_ticker):
    rows = []
    for u in universe:
        tk = u["ticker"]
        info = per_ticker.get(tk, {})
        status = "already usable" if info.get("usable") else (
            "short history - extend" if info.get("locally_available") else "needs file")
        note = u.get("notes") or ""
        rows.append({
            "ticker": tk, "asset_class": u.get("asset_class", ""),
            "target_filename": "%s.csv" % tk,
            "required_columns": _REQUIRED_COLUMNS_HINT,
            "acceptable_sources": _ACCEPTABLE_SOURCES,
            "notes": ("%s [%s]" % (note, status)).strip(),
        })
    return rows


# --------------------------------------------------------------------------- #
# 8. Cross-asset feature readiness
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
# 9. Decision
# --------------------------------------------------------------------------- #
def decide(manual_file_count, usable_count, asset_classes_covered):
    if manual_file_count <= 0:
        return REC_WAITING
    if usable_count >= READY_MIN_TICKERS and asset_classes_covered >= READY_MIN_ASSET_CLASSES:
        return REC_READY
    if usable_count >= PARTIAL_MIN_TICKERS:
        return REC_PARTIAL
    return REC_BAD_FORMAT


_NEXT_PHASE = {
    REC_READY: ("3-X", "Build Cross-Asset Feature Factory",
                "Enough manual global ETF price history is local; build the point-in-time "
                "cross-asset feature factory from the assembled panel."),
    REC_PARTIAL: ("3-X", "Complete Manual Global ETF Data Pack",
                  "A usable core is present but some proxies are missing; complete the manual "
                  "CSV intake before/with building the feature factory."),
    REC_WAITING: ("3-X", "Add Manual Global ETF Price CSVs",
                  "No manual CSV files were found; add real price CSVs to the manual folder, then "
                  "re-run this phase."),
    REC_BAD_FORMAT: ("3-X", "Repair Manual Global ETF CSV Formats",
                     "Files are present but too few tickers parsed usably; repair the CSV "
                     "headers/columns to an accepted variant and re-run."),
}


def build_recommended_next_phase(rec):
    phase, title, purpose = _NEXT_PHASE[rec]
    return {"phase": phase, "title": title, "purpose": purpose}


def build_recommendation(rec, target_count, manual_file_count, usable_count, classes):
    reasons = {
        REC_READY:
            "%d of %d target proxies are usable across %d asset classes (>= %d tickers and >= %d "
            "classes); ready to build the cross-asset feature factory."
            % (usable_count, target_count, classes, READY_MIN_TICKERS, READY_MIN_ASSET_CLASSES),
        REC_PARTIAL:
            "%d of %d target proxies usable (>= %d) but not enough for READY (>= %d tickers across "
            ">= %d classes); complete the manual intake."
            % (usable_count, target_count, PARTIAL_MIN_TICKERS, READY_MIN_TICKERS,
               READY_MIN_ASSET_CLASSES),
        REC_WAITING:
            "No manual CSV files found in the intake folder; add real price CSVs and re-run.",
        REC_BAD_FORMAT:
            "%d manual file(s) present but only %d of %d target proxies parsed usably (< %d); "
            "repair the CSV formats."
            % (manual_file_count, usable_count, target_count, PARTIAL_MIN_TICKERS),
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
        "manual_file_count": manual_file_count,
        "usable_count": usable_count,
        "reason": reasons[rec],
    }


# --------------------------------------------------------------------------- #
# Decision table / safety flags / interpretation
# --------------------------------------------------------------------------- #
def build_decision_table(p3v, target_count, manual_file_count, usable_count, classes, rec):
    rows = []

    def add(item, value, passed, note):
        rows.append({"decision_item": item, "value": value,
                     "passed": "" if passed is None else bool(passed), "note": note})

    add("phase3v_present", p3v["phase3v_present"], p3v["phase3v_present"],
        "Phase 3-V result readable (read-only)")
    add("phase3v_is_3v", p3v["phase3v_is_3v"], p3v["phase3v_is_3v"], "Phase 3-V result phase == 3-V")
    add("phase3v_recommendation_accepted", p3v["phase3v_recommendation"],
        p3v["phase3v_recommendation_accepted"],
        "Phase 3-V recommended a manual repair path (blocked / partial download)")
    add("target_universe_size", target_count, target_count >= 20,
        "at least 20 cross-asset proxies targeted")
    add("network_called", False, True, "no network is contacted; manual local CSV intake only")
    add("manual_file_count", manual_file_count, None, "non-template CSV files found in the folder")
    add("usable_count", usable_count, None, "target proxies with >= %d usable daily rows"
        % _MIN_USABLE_ROWS)
    add("asset_classes_covered", classes, None, "distinct asset classes among usable proxies")
    add("ready_threshold_met",
        usable_count >= READY_MIN_TICKERS and classes >= READY_MIN_ASSET_CLASSES,
        usable_count >= READY_MIN_TICKERS and classes >= READY_MIN_ASSET_CLASSES,
        "READY needs >= %d usable proxies across >= %d asset classes"
        % (READY_MIN_TICKERS, READY_MIN_ASSET_CLASSES))
    add("no_price_data_faked", True, True, "invalid rows dropped; no rows fabricated")
    add("alpha_vantage_called", False, True, "no Alpha Vantage call")
    add("fred_called", False, True, "no FRED call")
    add("stooq_called", False, True, "no Stooq call (manual intake only)")
    add("yfinance_called", False, True, "no yfinance use")
    add("d_drive_read", False, True, "nothing read from the data drive")
    add("d_drive_written", False, True, "nothing written to the data drive")
    add("phase3m_outputs_modified", False, True, "Phase 3-M outputs untouched")
    add("phase3u_outputs_modified", False, True, "Phase 3-U outputs untouched")
    add("phase3v_outputs_modified", False, True, "Phase 3-V outputs untouched")
    add("no_production_model_candidate", True, True, "data preparation only")
    add("no_production_predictions_or_scores", True, True, "no predictions / scores computed")
    add("no_portfolio_weights", True, True, "no portfolio weights computed")
    add("no_deployable_artifact", True, True, "no serialized model file written")
    add("recommendation", rec, rec in ALLOWED_RECOMMENDATIONS, "allowed values only")
    return rows


def build_safety_flags():
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
        "provider_api_called": False,
        "alpha_vantage_called": False,
        "fred_called": False,
        "stooq_called": False,
        "yfinance_called": False,
        "paid_vendor_api_called": False,
        "network_called": False,
        "data_faked": False,
        "labels_for_validation_only": True,
    }


def build_interpretation(rec, target_count, manual_file_count, usable_count, classes):
    return {
        "phase_is_research_only": True,
        "research_only": True,
        "local_data_preparation_only": True,
        "manual_intake_only": True,
        "price_data_faked": False,
        "network_called": False,
        "provider_paid_api_called": False,
        "production_predictions_computed": False,
        "production_scores_computed": False,
        "portfolio_weights_computed": False,
        "deployable_model_artifact_written": False,
        "production_edge_claimed": False,
        "results_remain_survivorship_biased": True,
        "narrative":
            "Phase 3-W is the research-only, local, MANUAL data-intake path that replaces Phase "
            "3-V's blocked network downloads. It calls no network: it only reads CSV files the "
            "user places in research/input/global_assets/manual/, normalizes accepted formats to a "
            "common OHLCV schema (adjusted_close uses adjusted_close/Adj Close if present, else "
            "close), drops invalid rows, and fabricates nothing. It found %d manual file(s), "
            "yielding %d of %d target proxies usable across %d asset classes. It trains no model, "
            "computes no predictions / scores / portfolio weights / order instructions, writes no "
            "deployable artifact, touches no database, runs no migration, restarts no service, "
            "enables no serving flag, and reads/writes nothing on the data drive. It does not call "
            "Stooq, yfinance, Alpha Vantage, FRED, or any paid API. Recommendation: %s. The "
            "universe is current-as-of, so downstream results remain survivorship-biased and claim "
            "no production edge."
            % (manual_file_count, usable_count, target_count, classes, rec),
    }


# --------------------------------------------------------------------------- #
# Output column specs
# --------------------------------------------------------------------------- #
_OUT_PATHS = {
    "inventory": "manual_file_inventory.csv",
    "panel": "normalized_global_etf_price_panel.csv",
    "coverage": "global_etf_price_coverage.csv",
    "quality": "global_etf_price_quality.csv",
    "missing": "missing_or_bad_format_files.csv",
    "instructions": "manual_download_instructions.csv",
    "feature_readiness": "cross_asset_feature_readiness.csv",
    "decision": "readiness_decision_table.csv",
}
_INVENTORY_COLS = ["file_path", "detected_ticker", "detected_format", "columns", "row_count",
                   "date_min", "date_max", "usable", "blocker"]
_PANEL_COLS = list(_NORMALIZED_COLS)
_COVERAGE_COLS = ["ticker", "asset_class", "region", "locally_available", "row_count", "date_min",
                  "date_max", "has_2016_start", "coverage_status", "blocker"]
_QUALITY_COLS = ["ticker", "duplicate_date_count", "missing_adjusted_close_count",
                 "nonpositive_adjusted_close_count", "missing_volume_count", "quality_status"]
_MISSING_COLS = ["ticker_or_file", "issue_type", "reason", "next_action"]
_INSTRUCTIONS_COLS = ["ticker", "asset_class", "target_filename", "required_columns",
                      "acceptable_sources", "notes"]
_FEATURE_COLS = ["feature_family", "required_tickers", "available_tickers", "missing_tickers",
                 "ready", "blocker"]
_DECISION_COLS = ["decision_item", "value", "passed", "note"]


def _out(o_dir, key):
    return os.path.join(o_dir, _OUT_PATHS[key])


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run(result_json_path=RESULT_JSON, o_dir=_W_DIR, manual_dir=_MANUAL_INPUT_DIR,
        universe_csv=PHASE3U_UNIVERSE_CSV, phase3v_result_json=PHASE3V_RESULT_JSON,
        create_scaffolding=True, verbose=True):
    p3v = confirm_phase3v(phase3v_result_json)
    universe, fallback_used = load_target_universe(universe_csv)
    target_count = len(universe)
    target_tickers = [u["ticker"] for u in universe]

    if create_scaffolding:
        ensure_manual_scaffolding(manual_dir, target_tickers)

    inventory_rows, parsed = inventory_manual_files(manual_dir)
    manual_file_count = len(inventory_rows)
    panel_rows, by_ticker = normalize_manual_files(parsed)
    coverage_rows, quality_rows, per_ticker = build_coverage_quality(universe, by_ticker)
    feature_rows = build_feature_readiness(per_ticker)
    missing_rows = build_missing_or_bad_format(universe, per_ticker, inventory_rows)
    instruction_rows = build_manual_instructions(universe, per_ticker)

    usable = [tk for tk, info in per_ticker.items() if info["usable"]]
    usable_count = len(usable)
    classes_covered = sorted({per_ticker[tk]["asset_class"] for tk in usable
                              if per_ticker[tk]["asset_class"]})
    classes = len(classes_covered)
    missing_count = target_count - usable_count

    rec = decide(manual_file_count, usable_count, classes)
    decision_rows = build_decision_table(p3v, target_count, manual_file_count, usable_count,
                                         classes, rec)

    if verbose:
        print("  Phase 3-V recommendation: %s (accepted: %s)"
              % (p3v["phase3v_recommendation"], p3v["phase3v_recommendation_accepted"]))
        print("  target universe: %d proxies (fallback_used=%s)" % (target_count, fallback_used))
        print("  manual files found: %d" % manual_file_count)

    # Write CSV outputs (all under research/output/phase3w...; never to the data drive).
    _write_csv(_out(o_dir, "inventory"), _INVENTORY_COLS, inventory_rows)
    _write_csv(_out(o_dir, "panel"), _PANEL_COLS, panel_rows)
    _write_csv(_out(o_dir, "coverage"), _COVERAGE_COLS, coverage_rows)
    _write_csv(_out(o_dir, "quality"), _QUALITY_COLS, quality_rows)
    _write_csv(_out(o_dir, "missing"), _MISSING_COLS, missing_rows)
    _write_csv(_out(o_dir, "instructions"), _INSTRUCTIONS_COLS, instruction_rows)
    _write_csv(_out(o_dir, "feature_readiness"), _FEATURE_COLS, feature_rows)
    _write_csv(_out(o_dir, "decision"), _DECISION_COLS, decision_rows)

    result = {
        "phase": PHASE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "phase3v_result_json": _rel(phase3v_result_json),
            "phase3u_universe_csv": _rel(universe_csv),
            "manual_input_dir": _rel(manual_dir),
        },
        "outputs_written": {
            "result_json": _rel(result_json_path),
            **{k: _rel(_out(o_dir, k)) for k in _OUT_PATHS},
        },
        "phase3v_confirmation": p3v,
        "target_count": target_count,
        "fallback_used": fallback_used,
        "manual_file_count": manual_file_count,
        "usable_ticker_count": usable_count,
        "missing_ticker_count": missing_count,
        "asset_classes_covered": classes,
        "asset_classes_covered_list": classes_covered,
        "usable_tickers": sorted(usable),
        "panel_row_count": len(panel_rows),
        "feature_families_ready": [r["feature_family"] for r in feature_rows if r["ready"]],
        "feature_readiness_summary": {
            "ready": sum(1 for r in feature_rows if r["ready"]),
            "blocked": sum(1 for r in feature_rows if not r["ready"]),
            "total": len(feature_rows),
        },
        "manual_input_instructions": {
            "where_to_place_files": _rel(manual_dir),
            "per_ticker_filename": "<TICKER>.csv",
            "combined_filename": _COMBINED_FILENAME,
            "accepted_sources": _ACCEPTABLE_SOURCES,
        },
        "readiness_decision_table": decision_rows,
        "recommendation": build_recommendation(rec, target_count, manual_file_count, usable_count,
                                                classes),
        "recommended_next_phase": build_recommended_next_phase(rec),
        "interpretation": build_interpretation(rec, target_count, manual_file_count, usable_count,
                                               classes),
    }
    result.update(build_safety_flags())
    _dump_json(result_json_path, result)
    return result


def main():
    result = run()
    rec = result["recommendation"]["recommendation"]
    print("phase:                      %s" % result["phase"])
    print("target count:               %s" % result["target_count"])
    print("manual file count:          %s" % result["manual_file_count"])
    print("usable ticker count:        %s" % result["usable_ticker_count"])
    print("missing ticker count:       %s" % result["missing_ticker_count"])
    print("asset classes covered:      %s (%s)"
          % (result["asset_classes_covered"], ", ".join(result["asset_classes_covered_list"])
             or "none"))
    print("panel rows:                 %s" % result["panel_row_count"])
    print("feature families ready:     %s" % (", ".join(result["feature_families_ready"]) or "none"))
    print("recommendation:             %s" % rec)
    print("recommended next:           %s - %s"
          % (result["recommended_next_phase"]["phase"], result["recommended_next_phase"]["title"]))
    print("place manual CSVs in:       %s"
          % result["manual_input_instructions"]["where_to_place_files"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
