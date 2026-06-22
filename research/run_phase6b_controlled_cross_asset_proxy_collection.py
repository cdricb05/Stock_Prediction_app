"""Phase 6-B - Controlled Cross-Asset Proxy Data Collection.

Phase 6-A tested whether a cross-asset / macro context signal improves 5-20 day equity selection
over the Phase 5-C price-only champion. The honest finding was that the *partial* macro pack that
could be built from purely local data (WTI, broad USD, 10Y/2Y, CPI, Fed Funds, SPY) did NOT help,
and the data inventory showed the real blocker: the full cross-asset ETF / sector / credit /
volatility / commodity proxy universe is NOT collected locally (0 usable proxies). Phase 6-A's
recommendation was therefore NEEDS_CONTROLLED_CROSS_ASSET_DATA_COLLECTION.

Phase 6-B is that controlled collection step. It assembles the daily adjusted-close + volume
history for the broad cross-asset proxy universe (equity structure, global / regional risk, rates /
duration, credit, dollar / FX, commodities, volatility, sector rotation) needed to rerun the macro
context harness PROPERLY in a later Phase 6-C. It is a data-collection phase only: it runs no
strategy / shadow test, trains no model, and does NOT rerun Phase 6-A.

Provider / safety discipline (hard rules):
  * DRY-RUN BY DEFAULT. With no flags the run contacts NO network at all: it emits the collection
    plan, validates whatever normalized files already exist locally, and reports what a live run
    WOULD do. A network request is made only when the operator passes --live explicitly.
  * FREE Alpha Vantage ONLY (https://www.alphavantage.co), reusing the audited Phase 3-Y collector
    (TIME_SERIES_DAILY_ADJUSTED, outputsize=full, datatype=csv). It does NOT call Yahoo, Stooq,
    FRED, yfinance, FMP, SimFin, or any paid vendor API.
  * The API key is read ONLY from the ALPHAVANTAGE_API_KEY environment variable. It is never
    printed and never persisted - every saved artifact uses a REDACTED endpoint template. If the
    key is missing the run STOPS before any network access (even under --live) and reports the
    exact PowerShell command to set it.
  * Free-tier friendly: >= 13 s between requests (Phase 3-Y's MIN_SLEEP_SECONDS) and a configurable
    per-run request cap. On a rate-limit / premium response the run STOPS immediately and PRESERVES
    every file already downloaded.
  * Raw provider payloads (when --live) are written ONLY to a gitignored raw/ cache directory
    (research/output/*/raw/ is ignored by the repo .gitignore). Committed-safe artifacts (plan /
    status / quality CSVs + JSON) carry row counts, date ranges, and redacted URLs only - never a
    payload body or an API key.
  * Existing valid normalized <TICKER>.csv files are PRESERVED (skipped, not re-downloaded). A file
    that exists but is invalid / too short is backed up into the raw/ cache before any rewrite -
    nothing valid is overwritten without a backup.
  * Price rows are NEVER fabricated: invalid rows are dropped and missing history stays missing.

This phase touches no database, runs no migration, restarts no service, enables no model-v2 serving
flag, places no orders, trades nothing, computes no predictions / scores / portfolio weights /
order instructions, writes no deployable model artifact, and writes nothing to the D: data drive.

Run (DRY-RUN, no network - safe default):
    python -B research/run_phase6b_controlled_cross_asset_proxy_collection.py
Run (LIVE - only meaningful when ALPHAVANTAGE_API_KEY is set in the same shell):
    $env:ALPHAVANTAGE_API_KEY = "<key>"; python -B research/run_phase6b_controlled_cross_asset_proxy_collection.py --live
Test:
    python -B tests/test_phase6b_controlled_cross_asset_proxy_collection.py
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import os
import shutil
from datetime import datetime

PHASE = "6-B"

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)

# --------------------------------------------------------------------------- #
# Paths (everything on C:; nothing is read from or written to the D: data drive)
# --------------------------------------------------------------------------- #
_OUT_DIR = os.path.join(_REPO_ROOT, "research", "output")
_B_DIR = os.path.join(_OUT_DIR, "phase6b_controlled_cross_asset_proxy_collection")
RESULT_JSON = os.path.join(_OUT_DIR, "phase6b_controlled_cross_asset_proxy_collection.json")

# Gitignored raw payload cache (research/output/*/raw/ is ignored by the repo .gitignore).
_RAW_DIR = os.path.join(_B_DIR, "raw")

# Normalized per-ticker CSVs share the Phase 3-W / 3-Y manual intake folder.
_MANUAL_DIR = os.path.join(_REPO_ROOT, "research", "input", "global_assets", "manual")

# The audited Phase 3-Y Alpha Vantage collector is reused for the (only) network layer.
_Y_RUNNER = os.path.join(_HERE, "run_phase3y_alphavantage_global_etf_price_collector.py")

# --------------------------------------------------------------------------- #
# Provider policy
# --------------------------------------------------------------------------- #
API_KEY_ENV = "ALPHAVANTAGE_API_KEY"
MAX_REQUESTS_ENV = "PHASE6B_MAX_NETWORK_REQUESTS_PER_RUN"
DEFAULT_MAX_NETWORK_REQUESTS = 46  # one per target proxy; the free-tier daily cap will bite first.
ALPHAVANTAGE_HOST = "www.alphavantage.co"

# Redacted endpoint template - the ONLY endpoint form ever persisted (the key stays a placeholder).
ENDPOINT_TEMPLATE = ("https://www.alphavantage.co/query?function=TIME_SERIES_DAILY_ADJUSTED"
                     "&symbol={symbol}&outputsize=full&datatype=csv&apikey=REDACTED")

NORMALIZED_COLUMNS = ["date", "open", "high", "low", "close", "adjusted_close", "volume"]

# --------------------------------------------------------------------------- #
# Cross-asset proxy universe (Phase 6-A candidate universe), grouped into the 8
# macro-context layers the harness conditions on.
# --------------------------------------------------------------------------- #
PROXY_UNIVERSE = [
    # equity structure
    ("QQQ", "equity_structure"), ("IWM", "equity_structure"), ("DIA", "equity_structure"),
    # global / regional risk
    ("EFA", "global_regional"), ("EEM", "global_regional"), ("ACWI", "global_regional"),
    ("VT", "global_regional"), ("EWJ", "global_regional"), ("EWU", "global_regional"),
    ("FXI", "global_regional"), ("EWZ", "global_regional"), ("INDA", "global_regional"),
    # rates / duration
    ("TLT", "rates_duration"), ("IEF", "rates_duration"), ("SHY", "rates_duration"),
    ("TIP", "rates_duration"),
    # credit
    ("HYG", "credit"), ("LQD", "credit"),
    # dollar / FX
    ("UUP", "dollar_fx"), ("FXE", "dollar_fx"), ("FXY", "dollar_fx"), ("FXB", "dollar_fx"),
    ("FXA", "dollar_fx"), ("FXC", "dollar_fx"),
    # commodities
    ("GLD", "commodity"), ("SLV", "commodity"), ("DBC", "commodity"), ("GSG", "commodity"),
    ("USO", "commodity"), ("BNO", "commodity"), ("UNG", "commodity"), ("CPER", "commodity"),
    # volatility
    ("VIXY", "volatility"),
    # sector rotation
    ("XLE", "sector_rotation"), ("XLK", "sector_rotation"), ("XLF", "sector_rotation"),
    ("XLI", "sector_rotation"), ("XLV", "sector_rotation"), ("XLY", "sector_rotation"),
    ("XLP", "sector_rotation"), ("XLU", "sector_rotation"), ("XLRE", "sector_rotation"),
    ("XLB", "sector_rotation"), ("XLC", "sector_rotation"), ("SMH", "sector_rotation"),
    ("KRE", "sector_rotation"),
]
TARGET_TICKERS = [t for t, _ in PROXY_UNIVERSE]

# --------------------------------------------------------------------------- #
# Readiness thresholds (Phase 6-B success gate)
# --------------------------------------------------------------------------- #
MIN_USABLE_ROWS = 250          # ~1 trading year -> a proxy counts as "collected / usable".
FIVE_YEAR_ROWS = 1259          # ~5 * 251.8 trading days -> "at least 5 years of history".
READY_MIN_PROXIES = 12         # >= 12 usable proxies
READY_MIN_ASSET_CLASSES = 5    # across >= 5 distinct asset classes
READY_MIN_FIVE_YEAR = 12       # >= 12 of the usable proxies carry >= 5 years where available

# --------------------------------------------------------------------------- #
# Decision values
# --------------------------------------------------------------------------- #
REC_READY = "READY_FOR_PHASE6C_MACRO_CONTEXT_RERUN"
REC_NEEDS_MORE = "NEEDS_ADDITIONAL_PROXY_COLLECTION"
REC_LIMIT = "PROVIDER_LIMIT_BLOCKED"
REC_QUALITY = "DATA_QUALITY_BLOCKED"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (REC_READY, REC_NEEDS_MORE, REC_LIMIT, REC_QUALITY, REC_ERROR)


# --------------------------------------------------------------------------- #
# Small IO helpers (stdlib only)
# --------------------------------------------------------------------------- #
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


def _to_float(v):
    try:
        f = float(str(v).strip())
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Reuse the audited Phase 3-Y collector for the network layer (import is network-free)
# --------------------------------------------------------------------------- #
def _import_phase3y():
    spec = importlib.util.spec_from_file_location("phase3y_for_6b", _Y_RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def resolve_api_key():
    """Read the Alpha Vantage key from the environment only. Never printed, never persisted."""
    key = os.environ.get(API_KEY_ENV)
    return key.strip() if isinstance(key, str) and key.strip() else None


def resolve_max_requests(explicit=None):
    if explicit is not None:
        try:
            return max(0, int(explicit))
        except (TypeError, ValueError):
            return DEFAULT_MAX_NETWORK_REQUESTS
    raw = os.environ.get(MAX_REQUESTS_ENV)
    if raw is None or str(raw).strip() == "":
        return DEFAULT_MAX_NETWORK_REQUESTS
    try:
        return max(0, int(str(raw).strip()))
    except ValueError:
        return DEFAULT_MAX_NETWORK_REQUESTS


def set_key_powershell_command():
    """Exact PowerShell command for the operator to set the key and run the live collection.
    The key itself is never echoed - this returns a placeholder template only."""
    return ('$env:ALPHAVANTAGE_API_KEY = "<your-free-alpha-vantage-key>"; '
            'python -B research\\run_phase6b_controlled_cross_asset_proxy_collection.py --live')


# --------------------------------------------------------------------------- #
# Read + score an existing normalized <TICKER>.csv
# --------------------------------------------------------------------------- #
def read_normalized_csv(path):
    """Read a normalized <TICKER>.csv and return quality facts. Never raises; fabricates nothing."""
    facts = {
        "present": False, "rows": 0, "date_min": "", "date_max": "",
        "adjusted_close_available": False, "volume_available": False,
        "valid_format": False, "blocker": "",
    }
    if not os.path.isfile(path):
        return facts
    facts["present"] = True
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fields = [(c or "").strip().lower() for c in (reader.fieldnames or [])]
            if "date" not in fields or "adjusted_close" not in fields:
                facts["blocker"] = "missing required date/adjusted_close columns"
                return facts
            facts["valid_format"] = True
            dates, n_adj, n_vol, rows = [], 0, 0, 0
            for r in reader:
                rl = {(k or "").strip().lower(): v for k, v in r.items()}
                d = (rl.get("date") or "").strip()
                adj = _to_float(rl.get("adjusted_close"))
                if not d or adj is None or adj <= 0:
                    continue
                rows += 1
                dates.append(d)
                n_adj += 1
                if _to_float(rl.get("volume")) is not None:
                    n_vol += 1
            facts["rows"] = rows
            if dates:
                facts["date_min"] = min(dates)
                facts["date_max"] = max(dates)
            facts["adjusted_close_available"] = n_adj > 0
            facts["volume_available"] = n_vol > 0
            if rows == 0:
                facts["blocker"] = "no usable rows"
    except OSError as e:
        facts["blocker"] = "read error: %s" % e
    except (csv.Error, ValueError) as e:
        facts["blocker"] = "parse error: %s" % e
    return facts


def _years_span(date_min, date_max):
    if not date_min or not date_max:
        return 0.0
    try:
        a = datetime.strptime(date_min[:10], "%Y-%m-%d").date()
        b = datetime.strptime(date_max[:10], "%Y-%m-%d").date()
        return round((b - a).days / 365.25, 2)
    except ValueError:
        return 0.0


# --------------------------------------------------------------------------- #
# Plan
# --------------------------------------------------------------------------- #
_PLAN_COLS = ["ticker", "asset_class", "target_filename", "already_present", "present_rows",
              "planned_action", "provider", "endpoint_template_redacted"]


def build_plan(manual_dir, live):
    """Build the per-ticker collection plan. In dry-run, planned_action is what a --live run WOULD
    do; in live, it is what this run intends to do."""
    rows = []
    for ticker, asset_class in PROXY_UNIVERSE:
        path = os.path.join(manual_dir, "%s.csv" % ticker)
        facts = read_normalized_csv(path)
        present_valid = facts["present"] and facts["valid_format"] and facts["rows"] >= MIN_USABLE_ROWS
        if present_valid:
            action = "skip_existing_valid"
        elif facts["present"]:
            action = "backup_then_download" if live else "would_backup_then_download"
        else:
            action = "download" if live else "would_download"
        rows.append({
            "ticker": ticker, "asset_class": asset_class,
            "target_filename": "%s.csv" % ticker,
            "already_present": facts["present"],
            "present_rows": facts["rows"],
            "planned_action": action,
            "provider": "alpha_vantage",
            "endpoint_template_redacted": ENDPOINT_TEMPLATE.format(symbol=ticker),
        })
    return rows


# --------------------------------------------------------------------------- #
# Live collection (only under --live, only when key present). Reuses Phase 3-Y.
# --------------------------------------------------------------------------- #
def _backup_existing(path, raw_dir):
    """Back up an existing-but-invalid normalized file into the gitignored raw cache before any
    rewrite, so nothing is overwritten without a copy. Returns the backup path or ''."""
    if not os.path.isfile(path):
        return ""
    os.makedirs(raw_dir, exist_ok=True)
    base = os.path.basename(path)
    backup = os.path.join(raw_dir, "%s.backup" % base)
    try:
        shutil.copy2(path, backup)
        return backup
    except OSError:
        return ""


def collect_live(plan_rows, manual_dir, raw_dir, api_key, max_requests, sleep_seconds=None):
    """Download each not-yet-valid proxy via the audited Phase 3-Y Alpha Vantage collector,
    preserving existing valid files and stopping cleanly on a provider limit. Writes the raw CSV
    body to the gitignored raw/ cache and the normalized CSV to the manual intake folder.

    Returns (status_by_ticker, provider_limit_hit, unexpected_error, requests_attempted,
    downloaded_count)."""
    y = _import_phase3y()
    sleep_s = y.MIN_SLEEP_SECONDS if sleep_seconds is None else max(y.MIN_SLEEP_SECONDS,
                                                                    float(sleep_seconds))
    collector = y.AlphaVantageCollector(api_key, sleep_seconds=sleep_s, max_requests=max_requests)
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(manual_dir, exist_ok=True)

    status = {}
    provider_limit_hit = False
    unexpected_error = None
    downloaded = 0

    for row in plan_rows:
        ticker = row["ticker"]
        target_path = os.path.join(manual_dir, "%s.csv" % ticker)
        if row["planned_action"] == "skip_existing_valid":
            status[ticker] = {"action": "skipped_existing", "rows_written": row["present_rows"],
                              "note": "valid file already present; preserved (not re-downloaded)"}
            continue
        if provider_limit_hit:
            status[ticker] = {"action": "not_attempted", "rows_written": "",
                              "note": "stopped after a provider limit; re-run after the quota resets"}
            continue
        if not collector.can_request():
            status[ticker] = {"action": "not_attempted", "rows_written": "",
                              "note": "max requests per run reached; re-run to continue"}
            continue

        try:
            text, cls = collector.fetch_daily_adjusted_csv(ticker)
        except y._ProviderLimited as e:  # noqa: SLF001 - reusing the audited sentinel.
            provider_limit_hit = True
            status[ticker] = {"action": "provider_limited", "rows_written": "",
                              "note": "provider limit: %s; stopping and preserving downloads" % e}
            continue
        except Exception as e:  # noqa: BLE001 - network/parse error for one ticker; keep going.
            unexpected_error = str(e)
            status[ticker] = {"action": "error", "rows_written": "",
                              "note": "download error: %s" % e}
            continue

        if cls != "ok":
            status[ticker] = {"action": "error", "rows_written": "",
                              "note": "provider returned a non-price response for this symbol"}
            continue

        # Persist the raw payload only to the gitignored raw cache.
        try:
            with open(os.path.join(raw_dir, "%s.csv" % ticker), "w",
                      encoding="utf-8", newline="") as f:
                f.write(text)
        except OSError:
            pass

        rows = y.normalize_alphavantage_csv(text)
        if not rows:
            status[ticker] = {"action": "error", "rows_written": 0,
                              "note": "no usable rows parsed; nothing written (not fabricated)"}
            continue

        backup = _backup_existing(target_path, raw_dir) if os.path.isfile(target_path) else ""
        _write_csv(target_path, NORMALIZED_COLUMNS, rows)
        downloaded += 1
        note = "normalized to %s" % ",".join(NORMALIZED_COLUMNS)
        if backup:
            note += "; previous file backed up to %s" % _rel(backup)
        status[ticker] = {"action": "downloaded", "rows_written": len(rows), "note": note}

    return status, provider_limit_hit, unexpected_error, collector.requests_attempted, downloaded


# --------------------------------------------------------------------------- #
# Validation / status / quality
# --------------------------------------------------------------------------- #
_STATUS_COLS = ["ticker", "asset_class", "action", "rows", "date_min", "date_max",
                "years_span", "adjusted_close_available", "volume_available", "usable",
                "has_five_year_history", "note"]
_QUALITY_COLS = ["ticker", "asset_class", "rows", "date_min", "date_max", "years_span",
                 "has_five_year_history", "adjusted_close_available", "volume_available",
                 "usable", "quality_status", "blocker"]


def validate(manual_dir, live_status):
    """Validate every proxy against the normalized files on disk. Returns (status_rows,
    quality_rows, metrics)."""
    status_rows, quality_rows = [], []
    usable_tickers, classes_usable, five_year_tickers = [], set(), []
    failed, missing, quality_blocked = [], [], 0

    for ticker, asset_class in PROXY_UNIVERSE:
        path = os.path.join(manual_dir, "%s.csv" % ticker)
        facts = read_normalized_csv(path)
        years = _years_span(facts["date_min"], facts["date_max"])
        usable = (facts["present"] and facts["valid_format"]
                  and facts["rows"] >= MIN_USABLE_ROWS and facts["adjusted_close_available"])
        has_5yr = usable and facts["rows"] >= FIVE_YEAR_ROWS

        ls = live_status.get(ticker, {})
        action = ls.get("action", "")
        if not action:
            action = "present" if usable else ("present_invalid" if facts["present"] else "not_collected")
        note = ls.get("note", "") or facts["blocker"]

        if usable:
            usable_tickers.append(ticker)
            if asset_class:
                classes_usable.add(asset_class)
            if has_5yr:
                five_year_tickers.append(ticker)
        elif facts["present"]:
            quality_blocked += 1
        else:
            missing.append(ticker)
        if action in ("error", "provider_limited"):
            failed.append(ticker)

        if not facts["present"]:
            quality_status = "NOT_COLLECTED"
        elif not facts["valid_format"]:
            quality_status = "BAD_FORMAT"
        elif not usable:
            quality_status = "SHORT_HISTORY"
        elif not has_5yr:
            quality_status = "OK_UNDER_5YR"
        else:
            quality_status = "OK"

        status_rows.append({
            "ticker": ticker, "asset_class": asset_class, "action": action,
            "rows": facts["rows"], "date_min": facts["date_min"], "date_max": facts["date_max"],
            "years_span": years, "adjusted_close_available": facts["adjusted_close_available"],
            "volume_available": facts["volume_available"], "usable": usable,
            "has_five_year_history": has_5yr, "note": note,
        })
        quality_rows.append({
            "ticker": ticker, "asset_class": asset_class, "rows": facts["rows"],
            "date_min": facts["date_min"], "date_max": facts["date_max"], "years_span": years,
            "has_five_year_history": has_5yr,
            "adjusted_close_available": facts["adjusted_close_available"],
            "volume_available": facts["volume_available"], "usable": usable,
            "quality_status": quality_status, "blocker": facts["blocker"],
        })

    metrics = {
        "target_count": len(PROXY_UNIVERSE),
        "usable_count": len(usable_tickers),
        "usable_tickers": sorted(usable_tickers),
        "asset_classes_covered": len(classes_usable),
        "asset_classes_covered_list": sorted(classes_usable),
        "five_year_count": len(five_year_tickers),
        "missing_count": len(missing),
        "missing_tickers": sorted(missing),
        "failed_count": len(failed),
        "failed_tickers": sorted(failed),
        "quality_blocked_count": quality_blocked,
    }
    return status_rows, quality_rows, metrics


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #
def decide(metrics, provider_limit_hit, unexpected_error):
    """READY only if >= 12 usable proxies across >= 5 asset classes with >= 12 carrying 5 years of
    history; else PROVIDER_LIMIT_BLOCKED if a quota halted collection short; else DATA_QUALITY_BLOCKED
    if files are present but parse unusably with nothing else collected; else ERROR on an unexpected
    failure; else NEEDS_ADDITIONAL_PROXY_COLLECTION (the default for dry-run / no-key / partial)."""
    ready = (metrics["usable_count"] >= READY_MIN_PROXIES
             and metrics["asset_classes_covered"] >= READY_MIN_ASSET_CLASSES
             and metrics["five_year_count"] >= READY_MIN_FIVE_YEAR)
    if ready:
        return REC_READY
    if provider_limit_hit:
        return REC_LIMIT
    if metrics["usable_count"] == 0 and metrics["quality_blocked_count"] > 0:
        return REC_QUALITY
    if unexpected_error and metrics["usable_count"] == 0:
        return REC_ERROR
    return REC_NEEDS_MORE


def ready_now(metrics):
    return (metrics["usable_count"] >= READY_MIN_PROXIES
            and metrics["asset_classes_covered"] >= READY_MIN_ASSET_CLASSES
            and metrics["five_year_count"] >= READY_MIN_FIVE_YEAR)


# --------------------------------------------------------------------------- #
# Gate / decision table
# --------------------------------------------------------------------------- #
_GATE_COLS = ["gate", "value", "passed", "note"]


def build_gate_table(metrics, rec, has_key, live, provider_limit_hit, network_called):
    rows = []

    def add(gate, value, passed, note):
        rows.append({"gate": gate, "value": value,
                     "passed": "" if passed is None else bool(passed), "note": note})

    add("dry_run_default", not live, None,
        "no network unless --live is passed explicitly; this run live=%s" % live)
    add("alphavantage_api_key_present", has_key, None,
        "ALPHAVANTAGE_API_KEY read from environment only (never printed/persisted)")
    add("network_called", network_called, None,
        "True only if >= 1 Alpha Vantage request was attempted under --live with a key")
    add("provider_restricted_to_alphavantage", ALPHAVANTAGE_HOST, True,
        "the only network host is www.alphavantage.co (reuses the audited Phase 3-Y collector)")
    add("yahoo_called", False, True, "Yahoo Finance is never called")
    add("stooq_called", False, True, "Stooq is never called")
    add("fred_called", False, True, "FRED is never called")
    add("fmp_called", False, True, "FMP is never called")
    add("simfin_called", False, True, "SimFin is never called")
    add("paid_vendor_api_called", False, True, "no paid vendor API is called")
    add("raw_payloads_gitignored", True, True,
        "raw bodies go only to research/output/*/raw/ (ignored by the repo .gitignore)")
    add("existing_valid_files_preserved", True, True,
        "valid <TICKER>.csv files are skipped; invalid ones are backed up before any rewrite")
    add("usable_proxy_count", metrics["usable_count"], None,
        "proxies with >= %d usable daily rows and an adjusted close" % MIN_USABLE_ROWS)
    add("asset_classes_covered", metrics["asset_classes_covered"], None,
        "distinct asset classes among usable proxies")
    add("five_year_history_count", metrics["five_year_count"], None,
        "usable proxies with >= %d rows (~5 years)" % FIVE_YEAR_ROWS)
    add("provider_limit_hit", provider_limit_hit, None,
        "True if Alpha Vantage returned a rate-limit / premium response")
    add("ready_threshold_met", ready_now(metrics), ready_now(metrics),
        "READY needs >= %d usable proxies across >= %d classes with >= %d carrying 5 years"
        % (READY_MIN_PROXIES, READY_MIN_ASSET_CLASSES, READY_MIN_FIVE_YEAR))
    add("decision_rule_consistent", rec,
        (rec == REC_READY) == ready_now(metrics) and rec in ALLOWED_RECOMMENDATIONS,
        "READY iff the readiness gate is met; recommendation is always an allowed value")
    add("no_price_data_faked", True, True, "invalid rows dropped; no rows fabricated")
    add("no_strategy_or_shadow_test", True, True, "collection only; no strategy / shadow test run")
    add("phase6a_not_rerun", True, True, "Phase 6-A is not rerun by this phase")
    add("no_database_write", True, True, "no database write")
    add("no_deployment", True, True, "no deployment")
    add("no_orders_or_automation", True, True, "no orders / automation")
    add("d_drive_written", False, True, "nothing written to the data drive")
    add("recommendation", rec, rec in ALLOWED_RECOMMENDATIONS, "allowed values only")
    return rows


# --------------------------------------------------------------------------- #
# Recommendation / next-phase / safety
# --------------------------------------------------------------------------- #
def build_recommendation(rec, metrics, has_key, live, provider_limit_hit):
    reasons = {
        REC_READY:
            "%d of %d proxies usable across %d asset classes with %d carrying >= 5 years; the "
            "cross-asset proxy pack is ready to rerun the macro context harness (Phase 6-C)."
            % (metrics["usable_count"], metrics["target_count"], metrics["asset_classes_covered"],
               metrics["five_year_count"]),
        REC_NEEDS_MORE:
            ("Dry-run / no live collection: %d usable proxies on disk (need >= %d across >= %d "
             "classes with >= %d carrying 5 years). Set the key and run --live to collect."
             % (metrics["usable_count"], READY_MIN_PROXIES, READY_MIN_ASSET_CLASSES,
                READY_MIN_FIVE_YEAR)) if not (live and has_key) else
            ("%d usable proxies collected so far (need >= %d across >= %d classes with >= %d "
             "carrying 5 years); re-run --live to collect the rest."
             % (metrics["usable_count"], READY_MIN_PROXIES, READY_MIN_ASSET_CLASSES,
                READY_MIN_FIVE_YEAR)),
        REC_LIMIT:
            "Alpha Vantage returned a rate-limit / premium response; downloaded files are preserved. "
            "Re-run --live after the free-tier daily quota resets to collect the remaining proxies.",
        REC_QUALITY:
            "%d file(s) are present but parse unusably and no proxy reached the usable threshold; "
            "repair the CSV formats." % metrics["quality_blocked_count"],
        REC_ERROR:
            "An unexpected error occurred during collection and no usable proxy was produced; "
            "inspect the status table and re-run.",
    }
    return {
        "recommendation": rec,
        "allowed_values": list(ALLOWED_RECOMMENDATIONS),
        "research_only": True,
        "pack_ready_for_phase6c": rec == REC_READY,
        "rerun_phase6a_now": False,
        "proceed_to_phase6c_now": rec == REC_READY,
        "run_strategy_or_shadow_test_now": False,
        "train_production_model_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "price_data_faked": False,
        "provider": "alpha_vantage",
        "api_key_present": has_key,
        "live_collection_run": bool(live and has_key),
        "usable_proxy_count": metrics["usable_count"],
        "asset_classes_covered": metrics["asset_classes_covered"],
        "five_year_history_count": metrics["five_year_count"],
        "reason": reasons[rec],
    }


def build_next_phase(rec, has_key):
    if rec == REC_READY:
        return {
            "phase": "6-C", "title": "Rerun Cross-Asset Macro Context Harness on the Full Pack",
            "proceed_to_phase6c": True,
            "purpose": "The cross-asset proxy pack passed the readiness gate; rerun the Phase 6-A "
                       "macro context harness on the full multi-layer pack (still research-only, "
                       "walk-forward, no strategy / shadow test).",
        }
    base = ("Collect the remaining cross-asset proxies, then re-validate. Phase 6-A is NOT rerun "
            "until the readiness gate passes.")
    if not has_key:
        base = ("Set ALPHAVANTAGE_API_KEY in the shell and run this collector with --live, then "
                "re-validate. No network was contacted on this dry run.")
    return {
        "phase": "6-B", "title": "Continue Controlled Cross-Asset Proxy Collection",
        "proceed_to_phase6c": False, "purpose": base,
    }


def build_safety_flags(network_called):
    return {
        "research_only": True,
        "dry_run_default": True,
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
        "strategy_or_shadow_test_run": False,
        "phase6a_rerun": False,
        "no_trading": True,
        "no_orders": True,
        "no_automation": True,
        "d_drive_read": False,
        "d_drive_written": False,
        "provider_api_called": bool(network_called),
        "network_called": bool(network_called),
        "alpha_vantage_called": bool(network_called),
        "yahoo_called": False,
        "stooq_called": False,
        "fred_called": False,
        "fmp_called": False,
        "simfin_called": False,
        "yfinance_called": False,
        "paid_vendor_api_called": False,
        "data_faked": False,
    }


def build_phase6c_plan(rec, metrics, has_key):
    """The gated Phase 6-C rerun plan. proceed_to_phase6c is True only when the pack is READY. The
    commands are recorded, NOT run."""
    return {
        "phase": "6-C",
        "title": "Cross-Asset Macro Context Harness Rerun (full pack)",
        "proceed_to_phase6c": rec == REC_READY,
        "rerun_phase6a_now": False,
        "run_strategy_or_shadow_test_now": False,
        "readiness": {
            "usable_proxy_count": metrics["usable_count"],
            "asset_classes_covered": metrics["asset_classes_covered"],
            "five_year_history_count": metrics["five_year_count"],
            "ready_threshold": "%d usable / %d classes / %d with 5yr"
                               % (READY_MIN_PROXIES, READY_MIN_ASSET_CLASSES, READY_MIN_FIVE_YEAR),
            "ready_now": rec == REC_READY,
        },
        "future_commands_not_run": ([
            set_key_powershell_command(),
        ] if not has_key else [
            "python -B research\\run_phase6b_controlled_cross_asset_proxy_collection.py --live",
        ]) + [
            "python -B research\\run_phase6a_cross_asset_macro_context_alpha.py   # only AFTER READY",
        ],
        "note": "Phase 6-C is gated on READY. Until then this plan is informational only; no "
                "strategy / shadow test is run and Phase 6-A is not rerun.",
    }


# --------------------------------------------------------------------------- #
# Output path spec
# --------------------------------------------------------------------------- #
_OUT_PATHS = {
    "plan": "cross_asset_proxy_collection_plan.csv",
    "status": "cross_asset_proxy_collection_status.csv",
    "quality": "cross_asset_proxy_quality_report.csv",
}
_PHASE6C_PLAN_JSON = "phase6c_macro_context_rerun_plan.json"


def _out(o_dir, key):
    return os.path.join(o_dir, _OUT_PATHS[key])


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run(result_json_path=RESULT_JSON, o_dir=_B_DIR, manual_dir=_MANUAL_DIR, raw_dir=_RAW_DIR,
        live=False, max_requests=None, sleep_seconds=None, verbose=True):
    api_key = resolve_api_key()
    has_key = api_key is not None
    max_req = resolve_max_requests(max_requests)

    live_status = {}
    provider_limit_hit = False
    unexpected_error = None
    requests_attempted = 0
    downloaded = 0
    network_called = False
    stopped_no_key = False

    plan_rows = build_plan(manual_dir, live and has_key)

    if live and not has_key:
        # Step 2 / 4: STOP before any network access; report the exact set-key command.
        stopped_no_key = True
        if verbose:
            print("  --live requested but %s is not set; STOPPING before any network access."
                  % API_KEY_ENV)
            print("  Set the key and re-run with this exact PowerShell command:")
            print("    %s" % set_key_powershell_command())
    elif live and has_key:
        (live_status, provider_limit_hit, unexpected_error,
         requests_attempted, downloaded) = collect_live(
            plan_rows, manual_dir, raw_dir, api_key, max_req, sleep_seconds)
        network_called = requests_attempted > 0

    status_rows, quality_rows, metrics = validate(manual_dir, live_status)
    rec = decide(metrics, provider_limit_hit, bool(unexpected_error))
    recommendation = build_recommendation(rec, metrics, has_key, live, provider_limit_hit)
    next_phase = build_next_phase(rec, has_key)
    gate_rows = build_gate_table(metrics, rec, has_key, live, provider_limit_hit, network_called)
    phase6c_plan = build_phase6c_plan(rec, metrics, has_key)

    _write_csv(_out(o_dir, "plan"), _PLAN_COLS, plan_rows)
    _write_csv(_out(o_dir, "status"), _STATUS_COLS, status_rows)
    _write_csv(_out(o_dir, "quality"), _QUALITY_COLS, quality_rows)
    _dump_json(os.path.join(o_dir, _PHASE6C_PLAN_JSON), phase6c_plan)

    result = {
        "phase": PHASE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "provider": "alpha_vantage",
        "provider_host": ALPHAVANTAGE_HOST,
        "endpoint_template": ENDPOINT_TEMPLATE,
        "reuses_collector": _rel(_Y_RUNNER),
        "mode": "live" if (live and has_key) else "dry_run",
        "live_requested": live,
        "live_collection_run": bool(live and has_key),
        "stopped_no_key": stopped_no_key,
        "set_key_powershell_command": set_key_powershell_command(),
        "api_key_present": has_key,
        "api_key_source": "environment:%s" % API_KEY_ENV,
        "max_requests_per_run": max_req,
        "requests_attempted": requests_attempted,
        "tickers_downloaded_this_run": downloaded,
        "provider_limit_hit": provider_limit_hit,
        "unexpected_error": unexpected_error,
        "network_called": network_called,
        "inputs_read": {
            "manual_intake_dir": _rel(manual_dir),
        },
        "outputs_written": {
            "result_json": _rel(result_json_path),
            **{k: _rel(_out(o_dir, k)) for k in _OUT_PATHS},
            "phase6c_plan_json": _rel(os.path.join(o_dir, _PHASE6C_PLAN_JSON)),
            "raw_cache_dir_gitignored": _rel(raw_dir),
            "normalized_csv_dir": _rel(manual_dir),
        },
        "target_count": metrics["target_count"],
        "usable_proxy_count": metrics["usable_count"],
        "usable_tickers": metrics["usable_tickers"],
        "asset_classes_covered": metrics["asset_classes_covered"],
        "asset_classes_covered_list": metrics["asset_classes_covered_list"],
        "five_year_history_count": metrics["five_year_count"],
        "missing_count": metrics["missing_count"],
        "missing_tickers": metrics["missing_tickers"],
        "failed_count": metrics["failed_count"],
        "failed_tickers": metrics["failed_tickers"],
        "quality_blocked_count": metrics["quality_blocked_count"],
        "ready_threshold": {
            "min_usable_proxies": READY_MIN_PROXIES,
            "min_asset_classes": READY_MIN_ASSET_CLASSES,
            "min_five_year_history": READY_MIN_FIVE_YEAR,
            "min_usable_rows_per_proxy": MIN_USABLE_ROWS,
            "five_year_rows": FIVE_YEAR_ROWS,
        },
        "ready_threshold_met": ready_now(metrics),
        "collection_plan": plan_rows,
        "gate_table": gate_rows,
        "recommendation": recommendation,
        "recommended_next_phase": next_phase,
        "phase6c_macro_context_rerun_plan": phase6c_plan,
    }
    result.update(build_safety_flags(network_called))
    _dump_json(result_json_path, result)
    return result


def _print_summary(result):
    rec = result["recommendation"]["recommendation"]
    print("phase:                      %s" % result["phase"])
    print("mode:                       %s" % result["mode"])
    print("api key present:            %s" % result["api_key_present"])
    print("live collection run:        %s" % result["live_collection_run"])
    print("requests attempted:         %s" % result["requests_attempted"])
    print("tickers downloaded:         %s" % result["tickers_downloaded_this_run"])
    print("provider limit hit:         %s" % result["provider_limit_hit"])
    print("usable proxies:             %s / %s" % (result["usable_proxy_count"],
                                                   result["target_count"]))
    print("asset classes covered:      %s (%s)"
          % (result["asset_classes_covered"],
             ", ".join(result["asset_classes_covered_list"]) or "none"))
    print("proxies with >= 5yr:        %s" % result["five_year_history_count"])
    print("missing proxies:            %s" % result["missing_count"])
    print("failed proxies:             %s" % result["failed_count"])
    print("ready threshold met:        %s" % result["ready_threshold_met"])
    print("recommendation:             %s" % rec)
    print("recommended next:           %s - %s"
          % (result["recommended_next_phase"]["phase"], result["recommended_next_phase"]["title"]))
    if result["stopped_no_key"]:
        print("set key + run live:         %s" % result["set_key_powershell_command"])


def _parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Phase 6-B controlled cross-asset proxy data collection (dry-run by default).")
    p.add_argument("--live", action="store_true",
                   help="Make live Alpha Vantage requests (requires ALPHAVANTAGE_API_KEY). Without "
                        "this flag the run is a dry-run and contacts no network.")
    p.add_argument("--max-requests", type=int, default=None,
                   help="Override the per-run network request cap (default %d / %s)."
                        % (DEFAULT_MAX_NETWORK_REQUESTS, MAX_REQUESTS_ENV))
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    result = run(live=args.live, max_requests=args.max_requests)
    _print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
