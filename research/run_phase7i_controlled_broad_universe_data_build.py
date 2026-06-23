"""Phase 7-I — Controlled Broad Universe Data Foundation Build.

RESEARCH / DATA-BUILD EXERCISE ONLY. This module designs (and, only under an explicit
environment-variable approval, runs a small controlled pilot of) a free-data pipeline to
expand the research universe beyond the current ~128-name, current-constituent panel so a
LATER phase (7-J) can retest the Phase 7-F / 7-H signal on a materially broader, more
realistic cross-section.

This is a DATA-BUILD phase, not a modelling phase. It contains NO logic for:
  * a trading system / production model / order or execution automation
  * factor construction, factor-weight optimization, factor-sign flipping
  * regime-throttle activation
  * paid data-provider APIs (every planned source is free: SEC EDGAR + yfinance)
  * installing packages (missing dependencies are reported, never installed)
  * Paper Trader / GCP / broker integration / deployment

Default behaviour is a SAFE DRY-RUN: it inventories local sources, builds a deterministic
candidate universe from the local SEC ticker directory, excludes obvious non-common-stock
symbols, emits a controlled collection plan + dependency check + storage manifest + the exact
future PowerShell live-run command, and makes NO network call and writes NOTHING to the D:
data drive.

A controlled pilot live collection runs ONLY when the environment variable
``PHASE7I_LIVE_APPROVED=YES`` is set. In that case it collects daily adjusted prices for at
most 300 candidate tickers (rate-limited, cache-backed, never overwriting existing files
without backup), writes the large data under D:\\Stock_Prediction_app_data\\phase7i_broad_universe\\,
and writes only committed-safe summaries/manifests into the repo.

Reproducible and offline by default. Writes only under
research/output/phase7i_controlled_broad_universe_data_build/ (committed-safe summaries) and,
only on an approved live run, under the D: data root. Does not commit or push.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from research import run_phase7c_multifactor_ranking_engine as C
from research import run_phase7g_signal_data_foundation_upgrade as G
from research import build_phase2k_g_free_expanded_dataset as B

PHASE = "7-I"
OBJECTIVE = ("Build a controlled, reproducible, free-data pipeline that can expand the research "
             "universe from the current ~128 names to a materially larger local universe with "
             "daily prices and point-in-time SEC fundamentals, so Phase 7-F/7-H can later be "
             "retested honestly. This phase plans the build and (only under explicit env "
             "approval) runs a small pilot; it does not model or retest.")

# --------------------------------------------------------------------------- #
# Recommendation vocabulary (exactly the allowed set).
# --------------------------------------------------------------------------- #
REC_READY = "READY_FOR_PHASE7J_BROAD_UNIVERSE_SIGNAL_RETEST"
REC_APPROVAL_REQUIRED = "LIVE_COLLECTION_APPROVAL_REQUIRED"
REC_PARTIAL = "BROAD_UNIVERSE_DATA_PARTIAL"
REC_BLOCKED = "DATA_COLLECTION_BLOCKED"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (REC_READY, REC_APPROVAL_REQUIRED, REC_PARTIAL, REC_BLOCKED, REC_ERROR)

# --------------------------------------------------------------------------- #
# Approval gate — live collection runs ONLY when this exact env var is set.
# --------------------------------------------------------------------------- #
LIVE_ENV_VAR = "PHASE7I_LIVE_APPROVED"
LIVE_ENV_VALUE = "YES"

# --------------------------------------------------------------------------- #
# Paths. Candidate source + repo summaries are local/committed-safe; large data -> D:.
# --------------------------------------------------------------------------- #
_REPO_ROOT = C._REPO_ROOT
SEC_COMPANY_TICKERS_JSON = G.SEC_COMPANY_TICKERS_JSON
PRICE_CSV = C.PRICE_CSV                  # existing 128-name panel (read-only overlap check)
FUNDAMENTALS_CSV = C.FUNDAMENTALS_CSV     # existing SEC fundamentals panel
BENCHMARK = B.BENCHMARK                   # "SPY" — index proxy needed for feature alignment

# Large raw / normalized data live OUTSIDE the repo, on the D: data drive (NEW dir; the
# existing phase2k_g panel is never touched).
DATA_ROOT = os.path.join("D:", os.sep, "Stock_Prediction_app_data", "phase7i_broad_universe")
DATA_PRICES_DIR = os.path.join(DATA_ROOT, "prices")
DATA_PRICE_CACHE_DIR = os.path.join(DATA_ROOT, "cache_prices")
DATA_SEC_DIR = os.path.join(DATA_ROOT, "sec_companyfacts")
DATA_COMBINED_PANEL_CSV = os.path.join(DATA_PRICES_DIR, "phase7i_broad_price_history_free.csv")
DATA_DQ_JSON = os.path.join(DATA_PRICES_DIR, "phase7i_broad_data_quality_report.json")
DATA_BUILD_SUMMARY_JSON = os.path.join(DATA_PRICES_DIR, "phase7i_broad_build_summary.json")
DATA_CIK_MAP_CSV = os.path.join(DATA_ROOT, "phase7i_cik_map_full.csv")

_OUT_DIR_DEFAULT = _REPO_ROOT / "research" / "output" / "phase7i_controlled_broad_universe_data_build"

# --------------------------------------------------------------------------- #
# Config (fixed, documented; no tuning loop).
# --------------------------------------------------------------------------- #
PILOT_MAX_TICKERS = 300            # controlled pilot cap (task requirement)
MIN_USABLE_PRICE_TICKERS = 250     # >= this many usable tickers => Phase 7-J retest allowed
MIN_USABLE_PRICE_ROWS = 252        # >= ~1 trading year of rows to call a ticker "usable"
DEFAULT_HISTORY_YEARS = 10         # target daily-price history span for the live build
RATE_LIMIT_SECONDS = 1.0           # min gap between live network requests (politeness)
MAX_RETRIES = 3

# Dependencies the planned collection relies on.
REQUIRED_DEPS = ("pandas", "yfinance")     # yfinance = free daily-price collector
STDLIB_DEPS = ("urllib.request", "json", "csv")  # SEC EDGAR collector uses only stdlib


def _f(x):
    return C._f(x)


def _round(x, n=6):
    return C._round(x, n)


# --------------------------------------------------------------------------- #
# Output paths (committed-safe summaries / manifests only).
# --------------------------------------------------------------------------- #
@dataclass
class _OutPaths:
    base: Path

    def __post_init__(self):
        self.report = self.base / "phase7i_controlled_broad_universe_data_build.json"
        self.candidate_universe = self.base / "candidate_universe.csv"
        self.collection_plan = self.base / "collection_plan.csv"
        self.dependency_check = self.base / "dependency_check.csv"
        self.storage_manifest = self.base / "data_storage_manifest.csv"
        self.live_command = self.base / "live_run_command.ps1"
        self.collection_status = self.base / "collection_status.csv"
        self.next_plan = self.base / "phase7j_next_plan.json"


def _rel(p) -> str:
    try:
        return str(Path(p).relative_to(_REPO_ROOT)).replace(os.sep, "/")
    except Exception:
        return str(p).replace(os.sep, "/")


def _norm(p) -> str:
    return str(p).replace(os.sep, "/")


def _write_json(path: Path, obj: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str)


def _write_csv(path: Path, rows: List[Dict], columns: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)
    df.to_csv(path, index=False)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


# --------------------------------------------------------------------------- #
# 1. Dependency detection (NEVER imports the heavy module; never installs).
# --------------------------------------------------------------------------- #
def _detect_dependency(name: str) -> Tuple[bool, Optional[str]]:
    """Return (available, version) without importing the module's runtime.

    Uses importlib.util.find_spec so detecting yfinance does not import it (and so triggers
    no network / heavy import). Version is read from package metadata when resolvable.
    """
    top = name.split(".")[0]
    try:
        spec = importlib.util.find_spec(top)
        available = spec is not None
    except Exception:
        available = False
    version = None
    if available:
        try:
            from importlib.metadata import version as _v, PackageNotFoundError
            try:
                version = _v(top)
            except PackageNotFoundError:
                version = None
        except Exception:
            version = None
    return available, version


def build_dependency_check() -> Tuple[List[Dict], Dict]:
    rows: List[Dict] = []
    purpose = {
        "pandas": "Tabular assembly / data-quality of the price panel.",
        "yfinance": "Free daily adjusted OHLCV price collector (the only price dependency).",
    }
    missing_required: List[str] = []
    for dep in REQUIRED_DEPS:
        ok, ver = _detect_dependency(dep)
        if not ok:
            missing_required.append(dep)
        rows.append({
            "dependency": dep,
            "kind": "required",
            "available": str(ok).lower(),
            "version": ver or "",
            "purpose": purpose.get(dep, ""),
            "note": ("present" if ok else
                     f"MISSING — required for collection; will NOT be installed. "
                     f"Install manually (e.g. `python -m pip install {dep}`) then re-run."),
        })
    for dep in STDLIB_DEPS:
        ok, _ = _detect_dependency(dep)
        rows.append({
            "dependency": dep,
            "kind": "stdlib",
            "available": str(ok).lower(),
            "version": "stdlib",
            "purpose": "SEC EDGAR companyfacts/submissions collector (no third-party dep).",
            "note": "Python standard library; always present.",
        })
    summary = {
        "missing_required": missing_required,
        "price_collector_available": "yfinance" not in missing_required,
        "sec_collector_available": True,  # stdlib urllib
        "pandas_available": "pandas" not in missing_required,
        "all_required_available": not missing_required,
    }
    return rows, summary


# --------------------------------------------------------------------------- #
# 2-3. Deterministic candidate universe from the local SEC ticker directory,
#       excluding obvious non-common-stock symbols (heuristic, clearly caveated).
# --------------------------------------------------------------------------- #
_VALID_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
_WARRANT_RE = re.compile(r"\bWARRANTS?\b")
_UNIT_RE = re.compile(r"\bUNITS?\b")
_RIGHT_RE = re.compile(r"\bRIGHTS?\b")
_PREF_RE = re.compile(r"\bPREFERRED\b|\bDEPOSITARY\b|\bDEPOSITORY\b|\bPFD\b")
_FUND_RE = re.compile(r"\bETF\b|\bETFS\b|\bETN\b|\bEXCHANGE TRADED\b|\bINDEX FUND\b|"
                      r"\bFUND\b|\bFUNDS\b|\bPORTFOLIO\b|\bTRUST FUND\b")
_FUND_ISSUER_RE = re.compile(r"\bISHARES\b|\bPROSHARES\b|\bSPDR\b|\bINVESCO\b QQQ|\bDIREXION\b|"
                             r"\bVANGUARD\b.*\bETF\b|\bWISDOMTREE\b|\bGLOBAL X\b")


def _classify_symbol(ticker: str, title: str) -> str:
    """Return an exclusion reason, or "" if the symbol looks like common stock.

    Heuristic only: authoritative common-stock classification needs share-class / security-
    type metadata that is NOT present in the local SEC ticker directory. These rules are
    deliberately conservative (drop suspected non-common names) and are reported per row so a
    later phase can audit / refine them. The pilot draws from the largest names (market-cap
    ordered), where these edge cases are rare.
    """
    t = (ticker or "").strip().upper()
    u = (title or "").strip().upper()
    if not t:
        return "invalid_empty_ticker"
    if not _VALID_TICKER_RE.fullmatch(t):
        return "invalid_characters"  # digits, dots, dashes, or length > 5
    if _WARRANT_RE.search(u):
        return "title_warrant"
    if _UNIT_RE.search(u):
        return "title_unit"
    if _RIGHT_RE.search(u):
        return "title_right"
    if _PREF_RE.search(u):
        return "title_preferred"
    if _FUND_ISSUER_RE.search(u) or _FUND_RE.search(u):
        return "title_fund_or_etf"
    # Nasdaq-style 5th-letter convention (only applied to exactly-5-letter symbols).
    if len(t) == 5:
        if t.endswith("W"):
            return "suffix_warrant"
        if t.endswith("U"):
            return "suffix_unit"
        if t.endswith("R"):
            return "suffix_rights"
    return ""


def _load_sec_directory() -> List[Dict]:
    """Read the local SEC ticker->CIK directory, preserving its (market-cap) ordering."""
    with open(SEC_COMPANY_TICKERS_JSON, encoding="utf-8") as fh:
        raw = json.load(fh)
    items = raw.values() if isinstance(raw, dict) else raw
    out: List[Dict] = []
    rank = 0
    for rec in items:
        if not isinstance(rec, dict):
            continue
        ticker = str(rec.get("ticker", "")).strip().upper()
        cik = rec.get("cik_str", rec.get("cik", ""))
        title = str(rec.get("title", "")).strip()
        out.append({"sec_rank": rank, "ticker": ticker,
                    "cik": int(cik) if str(cik).isdigit() else "", "title": title})
        rank += 1
    return out


def _load_local_price_tickers() -> set:
    """Best-effort read of tickers already in the local price panel (read-only). Empty on
    any failure (e.g. the D: panel is absent in a test environment)."""
    try:
        df = pd.read_csv(PRICE_CSV, usecols=["ticker"], dtype={"ticker": str})
        return set(df["ticker"].dropna().str.upper().unique())
    except Exception:
        return set()


def build_candidate_universe() -> Tuple[List[Dict], Dict]:
    directory = _load_sec_directory()
    already_local = _load_local_price_tickers()
    rows: List[Dict] = []
    seen = set()
    for rec in directory:
        ticker = rec["ticker"]
        reason = _classify_symbol(ticker, rec["title"])
        # De-duplicate tickers (the directory can list the same symbol twice).
        dup = ticker in seen
        if not dup:
            seen.add(ticker)
        included = (reason == "" and not dup)
        rows.append({
            "sec_rank": rec["sec_rank"],
            "ticker": ticker,
            "cik": rec["cik"],
            "title": rec["title"],
            "included": str(included).lower(),
            "exclusion_reason": ("duplicate_ticker" if (dup and not reason) else reason),
            "already_collected_local": str(ticker in already_local).lower(),
        })
    included_rows = [r for r in rows if r["included"] == "true"]
    excluded = len(rows) - len(included_rows)
    reasons: Dict[str, int] = {}
    for r in rows:
        if r["included"] == "false":
            reasons[r["exclusion_reason"]] = reasons.get(r["exclusion_reason"], 0) + 1
    summary = {
        "directory_size": len(rows),
        "candidate_universe_size": len(included_rows),
        "excluded_count": excluded,
        "exclusion_reason_counts": reasons,
        "already_collected_local_count": sum(
            1 for r in included_rows if r["already_collected_local"] == "true"),
        "local_price_panel_tickers": len(already_local),
    }
    return rows, summary


def _pilot_tickers(candidate_rows: List[Dict]) -> List[Dict]:
    """The controlled pilot subset: the top-N included candidates by SEC (market-cap) rank."""
    included = [r for r in candidate_rows if r["included"] == "true"]
    included.sort(key=lambda r: r["sec_rank"])
    return included[:PILOT_MAX_TICKERS]


# --------------------------------------------------------------------------- #
# 4. Controlled collection plan (prices / SEC fundamentals / CIK mapping).
# --------------------------------------------------------------------------- #
def build_collection_plan(cand_s: Dict, dep_s: Dict, pilot: List[Dict]) -> Tuple[List[Dict], Dict]:
    pilot_n = len(pilot)
    rows = [
        {
            "track": "daily_adjusted_prices",
            "source": "yfinance (free)",
            "method": "per-ticker yf.download(auto_adjust=True), retry+backoff, on-disk cache",
            "reuses": "build_phase2k_g_free_expanded_dataset (pure transform / DQ helpers)",
            "target_count": pilot_n,
            "history_years": DEFAULT_HISTORY_YEARS,
            "rate_limit_seconds": RATE_LIMIT_SECONDS,
            "storage_location": _norm(DATA_COMBINED_PANEL_CSV),
            "dependency_available": str(dep_s["price_collector_available"]).lower(),
            "status": "PLANNED",
            "note": ("The pilot collects daily prices for the top "
                     f"{pilot_n} candidates by market-cap rank. >= {MIN_USABLE_PRICE_TICKERS} "
                     "usable price series unlocks the Phase 7-J retest."),
        },
        {
            "track": "sec_companyfacts_fundamentals",
            "source": "SEC EDGAR data.sec.gov (free, stdlib urllib)",
            "method": "companyfacts/CIK{cik10}.json + submissions, declared User-Agent, >=0.25s gap",
            "reuses": "prototype_phase3f_sec_fundamentals / build_phase3h_sec_fundamental_features",
            "target_count": pilot_n,
            "history_years": "all available (point-in-time by filing date)",
            "rate_limit_seconds": 0.25,
            "storage_location": _norm(DATA_SEC_DIR),
            "dependency_available": str(dep_s["sec_collector_available"]).lower(),
            "status": "PLANNED_FOLLOW_ON",
            "note": ("Fundamentals reuse the existing SEC client (own guards). Deferred to a "
                     "follow-on run after price coverage is confirmed; not in the price pilot."),
        },
        {
            "track": "cik_identity_mapping",
            "source": "local SEC company_tickers.json (already collected)",
            "method": "deterministic ticker->CIK->title map from the local directory",
            "reuses": _rel(SEC_COMPANY_TICKERS_JSON),
            "target_count": cand_s["candidate_universe_size"],
            "history_years": "n/a",
            "rate_limit_seconds": 0.0,
            "storage_location": _norm(DATA_CIK_MAP_CSV),
            "dependency_available": "true",
            "status": "AVAILABLE_LOCAL",
            "note": "No network needed; CIK mapping is already present in candidate_universe.csv.",
        },
    ]
    summary = {
        "pilot_ticker_count": pilot_n,
        "pilot_cap": PILOT_MAX_TICKERS,
        "price_track_ready": dep_s["price_collector_available"],
        "sec_track_ready": dep_s["sec_collector_available"],
    }
    return rows, summary


# --------------------------------------------------------------------------- #
# 5. Data storage manifest (declares D: targets; dry-run creates none).
# --------------------------------------------------------------------------- #
def build_storage_manifest(wrote_live: bool, written_paths: set) -> List[Dict]:
    def row(path, kind, large, note):
        p = str(path)
        return {
            "artifact": os.path.basename(p) or p,
            "location": _norm(p),
            "drive": "D:" if p.upper().startswith("D:") else "repo",
            "kind": kind,
            "large_data": str(large).lower(),
            "exists_now": str(os.path.exists(p)).lower(),
            "written_this_run": str(p in written_paths).lower(),
            "note": note,
        }
    return [
        row(DATA_ROOT, "directory", True,
            "Root for all Phase 7-I large data (NEW dir; existing phase2k_g panel untouched)."),
        row(DATA_PRICES_DIR, "directory", True, "Normalized broad daily-price panel + reports."),
        row(DATA_PRICE_CACHE_DIR, "directory", True,
            "Per-ticker price cache (prevents re-download and overwrite on re-run)."),
        row(DATA_SEC_DIR, "directory", True, "Raw SEC companyfacts/submissions JSON (follow-on)."),
        row(DATA_COMBINED_PANEL_CSV, "price_panel_csv", True,
            "Combined long-format adjusted OHLCV panel for the pilot universe."),
        row(DATA_DQ_JSON, "data_quality_json", False, "Data-quality report for the broad panel."),
        row(DATA_BUILD_SUMMARY_JSON, "build_summary_json", False, "Reproducible live-run metadata."),
        row(DATA_CIK_MAP_CSV, "cik_map_csv", False, "ticker->CIK->title map (derived locally)."),
    ]


# --------------------------------------------------------------------------- #
# 6/13/14. Live-run PowerShell command (committed-safe; the user runs it to approve).
# --------------------------------------------------------------------------- #
def build_live_run_command() -> str:
    return (
        "# Phase 7-I - APPROVED controlled pilot live collection (Windows PowerShell).\n"
        "# Review the dry-run candidate universe + collection plan FIRST. This script sets the\n"
        "# one approval gate and runs the bounded pilot (<= 300 tickers, rate-limited, free\n"
        "# sources only). Large data is written under D:\\Stock_Prediction_app_data\\phase7i_broad_universe\\;\n"
        "# only committed-safe summaries land in the repo. It does NOT commit or push.\n"
        "#\n"
        "# Pre-req: the price collector dependency must be present (see dependency_check.csv).\n"
        "#   python -c \"import yfinance\"   # must succeed; if not, install it manually first.\n"
        "\n"
        "Set-Location 'C:\\Users\\binis\\Stock_Prediction_app_push'\n"
        f"$env:{LIVE_ENV_VAR} = '{LIVE_ENV_VALUE}'\n"
        "python -m research.run_phase7i_controlled_broad_universe_data_build\n"
        f"Remove-Item Env:\\{LIVE_ENV_VAR}\n"
    )


# --------------------------------------------------------------------------- #
# 15. Controlled pilot live price collection (ONLY when env-approved).
# --------------------------------------------------------------------------- #
def _history_range() -> Tuple[str, str]:
    import datetime as _dt
    end = _dt.date.today()
    start = end.replace(year=end.year - DEFAULT_HISTORY_YEARS)
    return start.isoformat(), end.isoformat()


def _backup_if_exists(path: str) -> Optional[str]:
    if os.path.isfile(path):
        bak = path + ".prev"
        try:
            if os.path.isfile(bak):
                os.remove(bak)
            os.replace(path, bak)
            return bak
        except Exception:
            return None
    return None


def run_pilot_live_collection(pilot: List[Dict]) -> Tuple[List[Dict], Dict, set]:
    """Collect daily adjusted prices for the pilot universe via yfinance. Rate-limited,
    cache-backed, never overwriting an existing combined panel without backing it up.

    Reuses the proven pure transform / data-quality helpers from build_phase2k_g. Returns
    (per-ticker status rows, summary, set-of-written-paths). Network happens here only."""
    written: set = set()
    import yfinance as yf  # local import — keeps module import network-free

    os.makedirs(DATA_PRICES_DIR, exist_ok=True)
    os.makedirs(DATA_PRICE_CACHE_DIR, exist_ok=True)
    start, end = _history_range()

    # Always include the benchmark for feature alignment / DQ.
    tickers = [r["ticker"] for r in pilot]
    fetch_list = list(dict.fromkeys(tickers + [BENCHMARK]))

    status_rows: List[Dict] = []
    frames: List[pd.DataFrame] = []
    by_ticker_rows: Dict[str, int] = {}
    n_net = 0
    for tk in fetch_list:
        cache_path = os.path.join(DATA_PRICE_CACHE_DIR, f"{tk}.csv")
        if os.path.isfile(cache_path):
            try:
                cached = pd.read_csv(cache_path)
            except Exception:
                cached = None
            if cached is not None and len(cached):
                frames.append(cached)
                by_ticker_rows[tk] = len(cached)
                status_rows.append({"ticker": tk, "status": "CACHED", "rows": len(cached),
                                    "usable": str(len(cached) >= MIN_USABLE_PRICE_ROWS).lower(),
                                    "source": "cache"})
                continue
        one = None
        errored = False
        for attempt in range(MAX_RETRIES):
            errored = False
            try:
                if n_net > 0:
                    time.sleep(RATE_LIMIT_SECONDS)
                raw = yf.download(tk, start=start, end=end, auto_adjust=True,
                                  progress=False, threads=False)
                n_net += 1
                one = B._normalize_yf_frame(raw, tk)
                break
            except Exception:
                errored = True
                time.sleep(min(2 ** attempt, 8))
        if errored and one is None:
            status_rows.append({"ticker": tk, "status": "FAILED", "rows": 0,
                                "usable": "false", "source": "yfinance"})
            continue
        if one is None or len(one) == 0:
            status_rows.append({"ticker": tk, "status": "EMPTY", "rows": 0,
                                "usable": "false", "source": "yfinance"})
            continue
        try:
            one.to_csv(cache_path, index=False)
        except Exception:
            pass
        frames.append(one)
        by_ticker_rows[tk] = len(one)
        status_rows.append({"ticker": tk, "status": "COLLECTED", "rows": len(one),
                            "usable": str(len(one) >= MIN_USABLE_PRICE_ROWS).lower(),
                            "source": "yfinance"})

    # Assemble + data-quality via the proven pure helpers.
    combined_raw = (pd.concat(frames, ignore_index=True) if frames
                    else pd.DataFrame(columns=list(B.RAW_COLUMNS)))
    panel = B.normalize_price_history(combined_raw)
    scored = B.compute_basic_features(panel, BENCHMARK)

    _backup_if_exists(DATA_COMBINED_PANEL_CSV)
    scored.to_csv(DATA_COMBINED_PANEL_CSV, index=False)
    written.add(DATA_COMBINED_PANEL_CSV)

    # Lightweight DQ + build summary (no universe-frame dependency).
    dq = {
        "row_count": int(len(scored)),
        "ticker_count": int(scored["ticker"].nunique()) if len(scored) else 0,
        "date_start": str(scored["date"].min()) if len(scored) else None,
        "date_end": str(scored["date"].max()) if len(scored) else None,
    }
    B._write_json(dq, DATA_DQ_JSON)
    written.add(DATA_DQ_JSON)
    summary_obj = {
        "phase": PHASE, "mode": "pilot_live", "source": "yfinance",
        "requested_tickers": len(fetch_list), "history_start": start, "history_end": end,
        "rate_limit_seconds": RATE_LIMIT_SECONDS, "no_orders": True, "no_automation": True,
        "no_paid_api": True,
    }
    B._write_json(summary_obj, DATA_BUILD_SUMMARY_JSON)
    written.add(DATA_BUILD_SUMMARY_JSON)

    # Usable price tickers = pilot names (excl. benchmark) with >= MIN_USABLE_PRICE_ROWS rows.
    usable = sum(1 for r in status_rows
                 if r["ticker"] != BENCHMARK and r["usable"] == "true")
    collected = sum(1 for r in status_rows if r["status"] in ("COLLECTED", "CACHED"))
    failed = sum(1 for r in status_rows if r["status"] == "FAILED")
    empty = sum(1 for r in status_rows if r["status"] == "EMPTY")
    summary = {
        "ran": True,
        "requested_tickers": len(fetch_list),
        "pilot_tickers": len(pilot),
        "collected_tickers": collected,
        "failed_tickers": failed,
        "empty_tickers": empty,
        "usable_price_tickers": usable,
        "panel_rows": int(len(scored)),
        "combined_panel_csv": _norm(DATA_COMBINED_PANEL_CSV),
        "network_requests": n_net,
    }
    return status_rows, summary, written


def _planned_collection_status(pilot: List[Dict]) -> List[Dict]:
    """Per-ticker status when no live run occurred: everything is PLANNED."""
    return [{"ticker": r["ticker"], "status": "PLANNED", "rows": 0, "usable": "false",
             "source": "not_run"} for r in pilot]


# --------------------------------------------------------------------------- #
# Gate matrix.
# --------------------------------------------------------------------------- #
_SAFETY_GATES = {
    "no_network_calls_when_not_approved", "no_paid_api", "no_packages_installed",
    "no_trading_order_automation", "no_model_or_retest_in_this_phase",
    "repo_outputs_are_summaries_only", "large_data_on_data_drive",
    "existing_files_not_overwritten_without_backup", "no_paper_trader_gcp_broker",
    "not_committed", "not_pushed",
}


def _is_safety_gate(name: str) -> bool:
    return name in _SAFETY_GATES


def build_gate_matrix(dep_s, cand_s, plan_s, live_s, approved: bool) -> Tuple[List[Dict], Dict]:
    gates: List[Dict] = []

    def g(name, status, detail):
        gates.append({"gate": name, "status": status,
                      "kind": "safety" if _is_safety_gate(name) else "capability",
                      "detail": detail})

    ran = bool(live_s.get("ran"))
    usable = int(live_s.get("usable_price_tickers", 0))

    # ---- Capability / results gates ----
    g("candidate_universe_built", "PASS" if cand_s["candidate_universe_size"] > 0 else "FAIL",
      f"included candidates={cand_s['candidate_universe_size']} of {cand_s['directory_size']} "
      f"SEC directory names; excluded={cand_s['excluded_count']}.")
    g("exclusion_filters_applied", "PASS",
      "warrant/unit/right/preferred/fund + invalid-character + 5th-letter heuristics applied "
      "(conservative; share-class metadata not local — see candidate_universe.csv).")
    g("collection_plan_built", "PASS",
      f"prices(yfinance)+SEC companyfacts(stdlib)+CIK map planned; pilot={plan_s['pilot_ticker_count']}.")
    g("price_collector_dependency_available",
      "PASS" if dep_s["price_collector_available"] else "FAIL",
      "yfinance present." if dep_s["price_collector_available"]
      else f"yfinance MISSING — collection blocked; will not install. {dep_s['missing_required']}")
    g("sec_collector_dependency_available", "PASS", "SEC collector uses only stdlib urllib.")
    g("pandas_available", "PASS" if dep_s["pandas_available"] else "FAIL", "pandas present.")
    g("default_mode_is_dry_run", "PASS",
      f"Live collection runs only when {LIVE_ENV_VAR}={LIVE_ENV_VALUE}; "
      f"approved this run={approved}.")
    g("live_collection_ran", "INFO" if not ran else "PASS",
      f"ran={ran}" + (f", usable_price_tickers={usable}" if ran else " (dry-run default)."))
    g("usable_price_coverage_sufficient",
      ("PASS" if usable >= MIN_USABLE_PRICE_TICKERS else "FAIL") if ran else "N/A",
      f"usable={usable} vs required {MIN_USABLE_PRICE_TICKERS}" if ran
      else "no live run; coverage not yet established.")

    # ---- Safety gates (all PASS) ----
    g("no_network_calls_when_not_approved", "PASS",
      "Dry-run makes no network call; network only inside the env-approved pilot.")
    g("no_paid_api", "PASS", "Only free sources planned (SEC EDGAR + yfinance). No paid API.")
    g("no_packages_installed", "PASS",
      "Missing dependencies are reported, never installed.")
    g("no_trading_order_automation", "PASS", "No order/execution/automation logic present.")
    g("no_model_or_retest_in_this_phase", "PASS",
      "No factor / model / signal retest here; this is a data-build phase.")
    g("repo_outputs_are_summaries_only", "PASS",
      "Repo gets only committed-safe summaries/manifests; large data -> D:.")
    g("large_data_on_data_drive", "PASS", f"Large data root = {_norm(DATA_ROOT)}.")
    g("existing_files_not_overwritten_without_backup", "PASS",
      "Per-ticker cache + .prev backup of the combined panel; phase2k_g panel never touched.")
    g("no_paper_trader_gcp_broker", "PASS", "No Paper Trader / GCP / broker / deployment logic.")
    g("not_committed", "PASS", "This phase does not commit.")
    g("not_pushed", "PASS", "This phase does not push.")

    n_pass = sum(1 for x in gates if x["status"] == "PASS")
    n_fail = sum(1 for x in gates if x["status"] == "FAIL")
    safety_fail = any(x["kind"] == "safety" and x["status"] != "PASS" for x in gates)
    summary = {
        "pass": n_pass, "fail": n_fail,
        "n_info": sum(1 for x in gates if x["status"] == "INFO"),
        "n_na": sum(1 for x in gates if x["status"] == "N/A"),
        "safety_fail": safety_fail,
        "price_dependency_missing": not dep_s["price_collector_available"],
        "live_ran": ran,
        "usable_price_tickers": usable,
    }
    return gates, summary


# --------------------------------------------------------------------------- #
# Recommendation + next-phase plan.
# --------------------------------------------------------------------------- #
def derive_recommendation(gate_s: Dict, approved: bool) -> str:
    if gate_s["safety_fail"]:
        return REC_BLOCKED
    if gate_s["price_dependency_missing"]:
        # Collection cannot proceed even if approved; report the missing dep, do not install.
        return REC_BLOCKED
    if not gate_s["live_ran"]:
        return REC_APPROVAL_REQUIRED
    if gate_s["usable_price_tickers"] >= MIN_USABLE_PRICE_TICKERS:
        return REC_READY
    return REC_PARTIAL


def build_next_plan(rec: str, cand_s, plan_s, live_s) -> Dict:
    retest_allowed = rec == REC_READY
    steps = []
    if rec == REC_APPROVAL_REQUIRED:
        steps.append({
            "step": "run_approved_pilot_price_collection",
            "scope": "ENV_APPROVAL_REQUIRED",
            "detail": (f"Set {LIVE_ENV_VAR}={LIVE_ENV_VALUE} and re-run (see live_run_command.ps1). "
                       f"Collects daily prices for up to {PILOT_MAX_TICKERS} top candidates."),
        })
    elif rec == REC_BLOCKED:
        steps.append({
            "step": "install_missing_price_dependency_manually",
            "scope": "MANUAL",
            "detail": ("The free price collector dependency is missing. Install it manually "
                       "(no auto-install in this phase), then re-run the dry-run to re-check."),
        })
    elif rec == REC_PARTIAL:
        steps.append({
            "step": "expand_pilot_or_diagnose_failures",
            "scope": "ENV_APPROVAL_REQUIRED",
            "detail": (f"Usable price coverage {live_s.get('usable_price_tickers')} < "
                       f"{MIN_USABLE_PRICE_TICKERS}. Inspect collection_status.csv failures, then "
                       "raise the pilot cap or retry failed tickers before a 7-J retest."),
        })
    elif rec == REC_READY:
        steps.append({
            "step": "collect_sec_companyfacts_for_pilot",
            "scope": "FOLLOW_ON_FREE",
            "detail": ("Collect SEC companyfacts for the price-covered universe via the existing "
                       "stdlib EDGAR client, then build the same de-cumulated TTM panel (7-G/7-H)."),
        })
        steps.append({
            "step": "phase7j_broad_universe_signal_retest",
            "scope": "LOCAL_RETEST",
            "detail": ("Re-grade the Phase 7-F/7-H composite through the UNMODIFIED 7-B harness on "
                       "the broad universe. Equal weight, no sign flipping, no optimization, regimes "
                       "diagnostic only. Report survivorship caveat (current-as-of membership)."),
        })
    return {
        "from_phase": PHASE,
        "recommendation": rec,
        "phase7j_retest_allowed": retest_allowed,
        "candidate_universe_size": cand_s["candidate_universe_size"],
        "pilot_ticker_count": plan_s["pilot_ticker_count"],
        "usable_price_tickers": live_s.get("usable_price_tickers", 0),
        "next_steps": steps,
        "hard_constraints": [
            "free sources only", "no paid APIs", "do not install packages",
            "large data on D: only", "repo outputs are summaries only",
            "no factor/model/retest in the data-build phase", "no trading/order/automation",
            "no Paper Trader/GCP/broker/deployment", "do not commit", "do not push",
        ],
    }


# --------------------------------------------------------------------------- #
# Report assembly + orchestration.
# --------------------------------------------------------------------------- #
def _safety_flags(written_paths: set) -> Dict:
    wrote_to_d = any(str(p).upper().startswith("D:") for p in written_paths)
    return {
        "preview_only_data_build": True,
        "live_data_used": wrote_to_d,            # only true on an approved live run
        "paid_api_used": False,
        "packages_installed": False,
        "trading_order_automation": False,
        "model_or_retest_performed": False,
        "repo_received_large_data": False,
        "wrote_to_data_drive": wrote_to_d,
        "wrote_to_existing_phase2k_g_panel": False,
        "paper_trader_gcp_broker_touched": False,
        "committed": False,
        "pushed": False,
    }


def _assemble_report(rec, approved, paths, dep_s, cand_s, plan_s, live_s, gates, gate_s,
                     next_plan, written_paths) -> Dict:
    return {
        "phase": PHASE,
        "objective": OBJECTIVE,
        "recommendation": rec,
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "live_approval": {
            "env_var": LIVE_ENV_VAR,
            "required_value": LIVE_ENV_VALUE,
            "approved_this_run": approved,
            "live_collection_ran": bool(live_s.get("ran")),
        },
        "determinations": {
            "candidate_universe_size": cand_s["candidate_universe_size"],
            "directory_size": cand_s["directory_size"],
            "price_collector_dependency_available": dep_s["price_collector_available"],
            "sec_collector_dependency_available": dep_s["sec_collector_available"],
            "missing_required_dependencies": dep_s["missing_required"],
            "live_collection_ran": bool(live_s.get("ran")),
            "usable_price_tickers": live_s.get("usable_price_tickers", 0),
            "tickers_collected": live_s.get("collected_tickers", 0),
            "phase7j_retest_allowed": rec == REC_READY,
            "large_data_storage_root": _norm(DATA_ROOT),
        },
        "dependency_check": dep_s,
        "candidate_universe": cand_s,
        "collection_plan": plan_s,
        "live_collection": live_s,
        "gate_matrix_summary": gate_s,
        "phase7j_next_plan": next_plan,
        "artifacts": {
            "report": _rel(paths.report),
            "candidate_universe": _rel(paths.candidate_universe),
            "collection_plan": _rel(paths.collection_plan),
            "dependency_check": _rel(paths.dependency_check),
            "data_storage_manifest": _rel(paths.storage_manifest),
            "live_run_command": _rel(paths.live_command),
            "collection_status": _rel(paths.collection_status),
            "phase7j_next_plan": _rel(paths.next_plan),
        },
        "safety": _safety_flags(written_paths),
    }


def _error_report(msg: str, tb: str) -> Dict:
    return {"phase": PHASE, "recommendation": REC_ERROR, "error": msg, "traceback": tb,
            "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS)}


def _run(paths: _OutPaths, *, approved: bool, force_skip_live: bool) -> Dict:
    dep_rows, dep_s = build_dependency_check()
    cand_rows, cand_s = build_candidate_universe()
    pilot = _pilot_tickers(cand_rows)
    plan_rows, plan_s = build_collection_plan(cand_s, dep_s, pilot)

    written_paths: set = set()
    live_s: Dict = {"ran": False, "usable_price_tickers": 0, "collected_tickers": 0}
    status_rows = _planned_collection_status(pilot)

    can_collect = approved and dep_s["price_collector_available"] and not force_skip_live
    if can_collect:
        status_rows, live_s, written_paths = run_pilot_live_collection(pilot)

    storage_rows = build_storage_manifest(bool(live_s.get("ran")), written_paths)
    gates, gate_s = build_gate_matrix(dep_s, cand_s, plan_s, live_s, approved)
    rec = derive_recommendation(gate_s, approved)
    next_plan = build_next_plan(rec, cand_s, plan_s, live_s)

    # Committed-safe artifacts (summaries / manifests only).
    _write_csv(paths.candidate_universe, cand_rows,
               ["sec_rank", "ticker", "cik", "title", "included", "exclusion_reason",
                "already_collected_local"])
    _write_csv(paths.collection_plan, plan_rows,
               ["track", "source", "method", "reuses", "target_count", "history_years",
                "rate_limit_seconds", "storage_location", "dependency_available", "status", "note"])
    _write_csv(paths.dependency_check, dep_rows,
               ["dependency", "kind", "available", "version", "purpose", "note"])
    _write_csv(paths.storage_manifest, storage_rows,
               ["artifact", "location", "drive", "kind", "large_data", "exists_now",
                "written_this_run", "note"])
    _write_text(paths.live_command, build_live_run_command())
    _write_csv(paths.collection_status, status_rows,
               ["ticker", "status", "rows", "usable", "source"])
    _write_json(paths.next_plan, next_plan)

    return _assemble_report(rec, approved, paths, dep_s, cand_s, plan_s, live_s, gates, gate_s,
                            next_plan, written_paths)


def run(out_dir: Optional[str] = None, *, force_skip_live: bool = False) -> Dict:
    paths = _OutPaths(Path(out_dir) if out_dir else _OUT_DIR_DEFAULT)
    approved = os.environ.get(LIVE_ENV_VAR, "").strip().upper() == LIVE_ENV_VALUE
    try:
        report = _run(paths, approved=approved, force_skip_live=force_skip_live)
    except Exception as exc:  # pragma: no cover - defensive
        import traceback
        report = _error_report(str(exc), traceback.format_exc())
        _write_json(paths.report, report)
        return report
    _write_json(paths.report, report)
    return report


def _print_summary(report: Dict) -> None:
    print(f"[{PHASE}] recommendation = {report['recommendation']}")
    d = report.get("determinations", {})
    la = report.get("live_approval", {})
    print(f"  candidate universe size         : {d.get('candidate_universe_size')} "
          f"(of {d.get('directory_size')} SEC names)")
    print(f"  price collector (yfinance)      : {d.get('price_collector_dependency_available')}")
    print(f"  missing required dependencies   : {d.get('missing_required_dependencies')}")
    print(f"  {LIVE_ENV_VAR}={la.get('required_value')} approved : {la.get('approved_this_run')}")
    print(f"  live collection ran             : {d.get('live_collection_ran')}")
    print(f"  usable price tickers            : {d.get('usable_price_tickers')} "
          f"(need {MIN_USABLE_PRICE_TICKERS})")
    print(f"  Phase 7-J retest allowed        : {d.get('phase7j_retest_allowed')}")
    print(f"  large data storage root         : {d.get('large_data_storage_root')}")
    gs = report.get("gate_matrix_summary", {})
    print(f"  gates: PASS={gs.get('pass')} FAIL={gs.get('fail')} "
          f"INFO={gs.get('n_info')} N/A={gs.get('n_na')} safety_fail={gs.get('safety_fail')}")


def _parse_args(argv):
    p = argparse.ArgumentParser(
        description="Phase 7-I controlled broad-universe data foundation build. Default is a "
                    "safe dry-run (no network, nothing written to D:). A controlled pilot live "
                    f"collection runs ONLY when {LIVE_ENV_VAR}={LIVE_ENV_VALUE}. No paid APIs, no "
                    "package installs, no model/retest, no orders/automation.")
    p.add_argument("--out-dir", type=str, default=None, help="Override committed-safe output dir.")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    report = run(args.out_dir)
    _print_summary(report)
    rec = report.get("recommendation")
    return 0 if rec in ALLOWED_RECOMMENDATIONS and rec != REC_ERROR else 1


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
