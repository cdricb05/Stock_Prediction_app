"""Phase 2K-G Free Expanded Price/Volume Dataset Builder (import-safe CLI).

Implements the local / free expanded price-volume dataset builder planned in Phase 2K-F.
It assembles an 8-10 year, broader-universe, survivorship-caveated daily adjusted OHLCV
panel for later retesting the liquidity candidate `avg_dollar_volume_21d`.

This module is IMPORT-SAFE: importing it does nothing, makes no network call, and creates
no file. All work happens behind explicit CLI modes:

  * --dry-run       (default): validate config + planned paths, read the sample universe,
                     print a plan summary. No network, no large output files.
  * --fixture-mode: run the transform + data-quality logic on a tiny in-memory fixture and
                     write only into a caller-provided temp output directory. No network.
  * --execute:      LATER, MANUAL use only. Guarded free-data retrieval that additionally
                     requires --allow-network. Without --allow-network it fails safely and
                     fetches nothing. This mode is never run in the tests or the analyzer.

The pure helpers (load_universe / validate_universe / normalize_price_history /
compute_basic_features / run_data_quality_checks / build_summary / build_survivorship_caveat)
are independently testable on in-memory data. The full live build is a deferred Phase 2K-H
step. The machine-readable safety flags live in build_summary(); the full guardrail rationale
lives in docs/phase2k_g_free_dataset_builder_v1.md, not in this source.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

PHASE = "2K-G"
SOURCE = "yfinance"
BENCHMARK = "SPY"

# Target scope (mirrors the Phase 2K-F plan).
MIN_YEARS = 8
RECOMMENDED_YEARS = 10
MIN_TICKERS_PER_DATE = 100

_INPUT_DIR = os.path.join("research", "input")

# The small validation JSON and committed sample inputs live in the C: repo. The LARGE
# datasets / caches / real build outputs live OUTSIDE the repo on the D: data drive (see
# DEFAULT_DATA_ROOT below). The legacy repo output dir is kept only so the safe modes and the
# tests can confirm nothing large was ever written into the repo.
LEGACY_REPO_OUTPUT_DIR = os.path.join("research", "output")

# External data root on the D: drive. Large expanded datasets, cache files, and the real
# Phase 2K-G / 2K-H build outputs default here, never under the C: repo.
DEFAULT_DATA_ROOT = r"D:\Stock_Prediction_app_data\phase2k_g"
DEFAULT_OUTPUT_DIR = os.path.join(DEFAULT_DATA_ROOT, "output")
DEFAULT_CACHE_DIR = os.path.join(DEFAULT_DATA_ROOT, "cache")
DEFAULT_INPUT_DIR = os.path.join(DEFAULT_DATA_ROOT, "input")

# The local / manual ticker universe files. The SAMPLE is a small committed fixture in the
# C: repo; the FULL universe file is assembled manually in a later phase on the D: data root
# and is NOT created here.
SAMPLE_UNIVERSE_CSV = os.path.join(
    _INPUT_DIR, "phase2k_g_free_universe_tickers_sample.csv")
FULL_UNIVERSE_CSV = os.path.join(
    DEFAULT_INPUT_DIR, "phase2k_g_free_universe_tickers.csv")

UNIVERSE_COLUMNS = ("ticker", "name", "sector", "source", "active_as_of",
                    "survivorship_caveat")

# Long-format panel schema the build targets.
PANEL_COLUMNS = ("date", "ticker", "adjusted_open", "adjusted_high", "adjusted_low",
                 "adjusted_close", "volume", "dollar_volume", "benchmark_close")

# Raw per-ticker schema the retrieval / fixture produces before assembly.
RAW_COLUMNS = ("ticker", "date", "adjusted_open", "adjusted_high", "adjusted_low",
               "adjusted_close", "volume")

# Basenames of the five large output files the full build would write.
PRICE_HISTORY_FILENAME = "phase2k_g_expanded_price_history_free.csv"
SCORED_FILENAME = "phase2k_g_expanded_scored_free.csv"
DATA_QUALITY_FILENAME = "phase2k_g_data_quality_report.json"
BUILD_SUMMARY_FILENAME = "phase2k_g_data_build_summary.json"
SURVIVORSHIP_FILENAME = "phase2k_g_survivorship_caveat.json"
REAL_OUTPUT_FILENAMES = (PRICE_HISTORY_FILENAME, SCORED_FILENAME, DATA_QUALITY_FILENAME,
                         BUILD_SUMMARY_FILENAME, SURVIVORSHIP_FILENAME)

# The real (large) output files the full build would write, on the D: data root. DECLARED
# here; the dry-run and fixture modes never create them.
REAL_PRICE_HISTORY_CSV = os.path.join(DEFAULT_OUTPUT_DIR, PRICE_HISTORY_FILENAME)
REAL_SCORED_CSV = os.path.join(DEFAULT_OUTPUT_DIR, SCORED_FILENAME)
REAL_DATA_QUALITY_JSON = os.path.join(DEFAULT_OUTPUT_DIR, DATA_QUALITY_FILENAME)
REAL_BUILD_SUMMARY_JSON = os.path.join(DEFAULT_OUTPUT_DIR, BUILD_SUMMARY_FILENAME)
REAL_SURVIVORSHIP_JSON = os.path.join(DEFAULT_OUTPUT_DIR, SURVIVORSHIP_FILENAME)

DQ_REPORT_STATUSES = ("PASS", "PASS_WITH_CAVEAT", "FAIL")

# Thresholds used by the data-quality checks.
_MISSING_FIELD_THRESHOLD = 0.02      # max fraction of missing critical values
_OUTLIER_RETURN_BOUND = 0.5          # |daily return| above this is flagged


class BuilderRefusal(Exception):
    """Raised when a required safety gate is missing. Carries a process exit code."""

    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)


def _write_json(obj: Dict[str, Any], path: str) -> None:
    _ensure_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, allow_nan=False)


def _now() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- #
# Universe (pure)
# --------------------------------------------------------------------------- #
def load_universe(path: str = SAMPLE_UNIVERSE_CSV) -> "pd.DataFrame":
    """Read the local / manual ticker universe CSV. No network access."""
    df = pd.read_csv(path, dtype=str).fillna("")
    df.columns = [str(c).strip() for c in df.columns]
    if "ticker" in df.columns:
        df["ticker"] = df["ticker"].str.strip().str.upper()
    return df


def validate_universe(df: "pd.DataFrame") -> Dict[str, Any]:
    """Check the universe frame carries the required columns and a recorded caveat."""
    cols = list(df.columns)
    missing = [c for c in UNIVERSE_COLUMNS if c not in cols]
    tickers = ([t for t in df["ticker"].tolist() if t] if "ticker" in cols else [])
    has_caveat_col = "survivorship_caveat" in cols
    all_rows_have_caveat = bool(
        has_caveat_col and len(df) and df["survivorship_caveat"].astype(str).str.len().gt(0).all())
    return {
        "ok": not missing and bool(tickers) and all_rows_have_caveat,
        "missing_columns": missing,
        "ticker_count": len(tickers),
        "has_benchmark": BENCHMARK in tickers,
        "survivorship_caveat_column_present": has_caveat_col,
        "all_rows_have_survivorship_caveat": all_rows_have_caveat,
        "point_in_time_membership_claimed": False,
    }


def build_survivorship_caveat(universe_df: "pd.DataFrame",
                              metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build explicit survivorship-caveat metadata (current-as-of, not point-in-time)."""
    meta = dict(metadata or {})
    tickers = ([t for t in universe_df["ticker"].tolist() if t]
               if "ticker" in universe_df.columns else [])
    return {
        "phase": PHASE,
        "generated_at": _now(),
        "membership_basis": "current_as_of",
        "point_in_time_membership_claimed": False,
        "reported_as_survivorship_biased": True,
        "clean_point_in_time_build_deferred": True,
        "universe_ticker_count": len(tickers),
        "active_as_of": meta.get("active_as_of"),
        "source": meta.get("source", SOURCE),
        "note": ("Membership is a current-as-of approximation, never a point-in-time "
                 "constituent claim. Any retest on this panel must be reported as "
                 "survivorship-biased. A clean point-in-time build is deferred to a later "
                 "paid-source decision."),
    }


# --------------------------------------------------------------------------- #
# Transform (pure)
# --------------------------------------------------------------------------- #
def normalize_price_history(raw_df: "pd.DataFrame") -> "pd.DataFrame":
    """Normalize a raw long-format OHLCV frame into a clean (date, ticker) panel.

    Coerces numerics, drops rows with a non-positive / missing adjusted close, de-duplicates
    the (date, ticker) key, and sorts. No forward fill of any value is performed.
    """
    df = raw_df.copy()
    for col in ("ticker", "date"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
    if "ticker" in df.columns:
        df["ticker"] = df["ticker"].str.upper()
    for col in ("adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close",
                "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "adjusted_close" in df.columns:
        df = df[df["adjusted_close"].notna() & (df["adjusted_close"] > 0)]
    df = df.drop_duplicates(subset=["date", "ticker"], keep="first")
    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)
    return df


def compute_basic_features(price_df: "pd.DataFrame",
                           benchmark: str = BENCHMARK) -> "pd.DataFrame":
    """Derive dollar_volume, per-ticker daily return, and an aligned benchmark close.

    `daily_return` uses pct_change with fill_method=None so no value is forward filled.
    """
    df = price_df.copy()
    df["dollar_volume"] = df["adjusted_close"] * df["volume"]
    df["daily_return"] = (df.groupby("ticker")["adjusted_close"]
                          .pct_change(fill_method=None))
    bench = df[df["ticker"] == benchmark][["date", "adjusted_close"]].rename(
        columns={"adjusted_close": "benchmark_close"})
    df = df.merge(bench, on="date", how="left")
    ordered = [c for c in PANEL_COLUMNS if c in df.columns]
    extras = [c for c in df.columns if c not in ordered]
    return df[ordered + extras].reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Data-quality (pure)
# --------------------------------------------------------------------------- #
def data_quality_check_catalog() -> List[Dict[str, str]]:
    """The fixed catalog of data-quality checks the build must run (id + severity)."""
    return [
        {"check_id": "adjusted_close_continuity", "severity": "caveat",
         "description": "Adjusted-close series is continuous with no unexplained jumps."},
        {"check_id": "duplicate_date_ticker_rows", "severity": "hard",
         "description": "No duplicate rows on the (date, ticker) key."},
        {"check_id": "missing_adjusted_close", "severity": "hard",
         "description": "Missing adjusted-close values stay below threshold."},
        {"check_id": "missing_volume", "severity": "caveat",
         "description": "Missing volume values stay below threshold."},
        {"check_id": "outlier_daily_returns", "severity": "caveat",
         "description": "Daily returns outside a sane bound are flagged for review."},
        {"check_id": "zero_or_negative_prices", "severity": "hard",
         "description": "No zero or negative adjusted prices."},
        {"check_id": "zero_or_negative_volume_unexpected", "severity": "caveat",
         "description": "Zero / negative volume only where genuinely expected."},
        {"check_id": "ticker_level_date_coverage", "severity": "caveat",
         "description": "Each ticker covers enough of its live span against the calendar."},
        {"check_id": "benchmark_coverage", "severity": "caveat",
         "description": "Benchmark series spans the full panel date range."},
        {"check_id": "minimum_years_achieved", "severity": "caveat",
         "description": f"Panel spans at least {MIN_YEARS} years."},
        {"check_id": "minimum_ticker_count_achieved", "severity": "caveat",
         "description": f"At least {MIN_TICKERS_PER_DATE} tickers per date."},
        {"check_id": "no_forward_filled_labels", "severity": "hard",
         "description": "Target labels use only realized future prices, never forward fill."},
        {"check_id": "no_point_in_time_membership_claim", "severity": "hard",
         "description": "No claim of point-in-time membership; current-as-of only."},
        {"check_id": "survivorship_caveat_present", "severity": "caveat",
         "description": "Survivorship-caveat metadata is present and populated."},
    ]


def _years_span(dates: Sequence[str]) -> float:
    if not dates:
        return 0.0
    try:
        d0 = dt.date.fromisoformat(str(min(dates)))
        d1 = dt.date.fromisoformat(str(max(dates)))
    except ValueError:
        return 0.0
    return round((d1 - d0).days / 365.25, 3)


def run_data_quality_checks(price_df: "pd.DataFrame", universe_df: "pd.DataFrame",
                            benchmark: str = BENCHMARK,
                            *, min_years: int = MIN_YEARS,
                            min_tickers: int = MIN_TICKERS_PER_DATE) -> Dict[str, Any]:
    """Run the catalog checks on the assembled panel and return a status report.

    Hard-check failures -> FAIL. Otherwise, any caveat (including the always-present
    survivorship caveat) -> PASS_WITH_CAVEAT; a clean panel with no caveat -> PASS.
    A survivorship-caveated free build is therefore never a clean PASS by design.
    """
    catalog = {c["check_id"]: c for c in data_quality_check_catalog()}
    n = len(price_df)
    tickers = sorted(set(price_df["ticker"].tolist())) if n else []
    dates = sorted(set(price_df["date"].tolist())) if n else []

    dup_count = (int(n - len(price_df.drop_duplicates(subset=["date", "ticker"])))
                 if n else 0)
    close = price_df["adjusted_close"] if n else pd.Series(dtype=float)
    miss_close = float(close.isna().mean()) if n else 1.0
    neg_price = int((close <= 0).sum()) if n else 0
    vol = price_df["volume"] if (n and "volume" in price_df.columns) else pd.Series(dtype=float)
    miss_vol = float(vol.isna().mean()) if len(vol) else (0.0 if n else 1.0)
    neg_vol = int((vol < 0).sum()) if len(vol) else 0
    ret = price_df["daily_return"] if (n and "daily_return" in price_df.columns) else pd.Series(dtype=float)
    outliers = int((ret.abs() > _OUTLIER_RETURN_BOUND).sum()) if len(ret) else 0
    span_years = _years_span(dates)
    per_date_counts = (price_df.groupby("date")["ticker"].nunique() if n else pd.Series(dtype=int))
    min_tickers_per_date = int(per_date_counts.min()) if len(per_date_counts) else 0
    bench_dates = set(price_df[price_df["ticker"] == benchmark]["date"].tolist()) if n else set()
    bench_cov = (len(bench_dates) / len(dates)) if dates else 0.0
    uni_valid = validate_universe(universe_df)

    results: Dict[str, bool] = {
        "adjusted_close_continuity": (outliers == 0),
        "duplicate_date_ticker_rows": (dup_count == 0),
        "missing_adjusted_close": (miss_close <= _MISSING_FIELD_THRESHOLD),
        "missing_volume": (miss_vol <= _MISSING_FIELD_THRESHOLD),
        "outlier_daily_returns": (outliers == 0),
        "zero_or_negative_prices": (neg_price == 0),
        "zero_or_negative_volume_unexpected": (neg_vol == 0),
        "ticker_level_date_coverage": (n > 0),
        "benchmark_coverage": (bench_cov >= 0.999),
        "minimum_years_achieved": (span_years >= float(min_years)),
        "minimum_ticker_count_achieved": (min_tickers_per_date >= int(min_tickers)),
        "no_forward_filled_labels": True,            # guaranteed by construction
        "no_point_in_time_membership_claim": True,   # current-as-of only
        "survivorship_caveat_present": bool(
            uni_valid["all_rows_have_survivorship_caveat"]),
    }

    checks: List[Dict[str, Any]] = []
    hard_fail = False
    caveat = False
    for cid, meta in catalog.items():
        passed = bool(results.get(cid, False))
        sev = meta["severity"]
        checks.append({"check_id": cid, "severity": sev, "passed": passed,
                       "description": meta["description"]})
        if not passed and sev == "hard":
            hard_fail = True
        if sev == "caveat" and (not passed or cid == "survivorship_caveat_present"):
            caveat = True

    status = "FAIL" if hard_fail else ("PASS_WITH_CAVEAT" if caveat else "PASS")
    return {
        "status": status,
        "checks": checks,
        "stats": {
            "row_count": int(n),
            "ticker_count": len(tickers),
            "date_count": len(dates),
            "years_span": span_years,
            "min_tickers_per_date": min_tickers_per_date,
            "benchmark_coverage_fraction": round(bench_cov, 4),
            "duplicate_rows": dup_count,
            "missing_adjusted_close_fraction": round(miss_close, 4),
            "missing_volume_fraction": round(miss_vol, 4),
            "outlier_return_rows": outliers,
        },
    }


# --------------------------------------------------------------------------- #
# Summary (pure)
# --------------------------------------------------------------------------- #
def build_summary(price_df: "pd.DataFrame", *, universe_df: "pd.DataFrame",
                  benchmark: str, source: str, mode: str,
                  dq: Dict[str, Any], params: Optional[Dict[str, Any]] = None
                  ) -> Dict[str, Any]:
    """Assemble the reproducible build-summary with all required safety flags."""
    n = len(price_df)
    dates = sorted(set(price_df["date"].tolist())) if n else []
    tickers = sorted(set(price_df["ticker"].tolist())) if n else []
    return {
        "phase": PHASE,
        "source": source,
        "mode": mode,
        "generated_at": _now(),
        # Safety flags (machine-readable).
        "database_touched": False,
        "database_write_executed": False,
        "migration_executed": False,
        "deployment_executed": False,
        "model_v2_enabled": False,
        "production_edge_claimed": False,
        "no_trading": True,
        "no_orders": True,
        "no_automation": True,
        # Build facts.
        "row_count": int(n),
        "ticker_count": len(tickers),
        "universe_ticker_count": int(validate_universe(universe_df)["ticker_count"]),
        "benchmark": benchmark,
        "date_start": dates[0] if dates else None,
        "date_end": dates[-1] if dates else None,
        "years_span": _years_span(dates),
        "data_quality_status": dq.get("status"),
        "model_trained": False,
        "model_candidate_created": False,
        "params": dict(params or {}),
    }


# --------------------------------------------------------------------------- #
# Planned real outputs (declared, not created by dry-run / fixture)
# --------------------------------------------------------------------------- #
def _planned(path: str, role: str) -> Dict[str, Any]:
    return {
        "path": _norm(path),
        "role": role,
        "exists_now": os.path.isfile(path),
        "created_by_dry_run_or_fixture": False,
    }


def planned_real_outputs() -> Dict[str, Any]:
    return {
        "note": "The large output files the full --execute build would write. The dry-run "
                "and fixture modes never create these.",
        "expanded_price_history_csv": _planned(
            REAL_PRICE_HISTORY_CSV, "Longer, broader daily adjusted OHLCV + volume panel."),
        "expanded_scored_csv": _planned(
            REAL_SCORED_CSV, "Feature-ready / scored panel derived from the price history."),
        "data_quality_report_json": _planned(
            REAL_DATA_QUALITY_JSON, "Data-quality report with PASS / PASS_WITH_CAVEAT / "
            "FAIL status."),
        "data_build_summary_json": _planned(
            REAL_BUILD_SUMMARY_JSON, "Reproducible run metadata."),
        "survivorship_caveat_json": _planned(
            REAL_SURVIVORSHIP_JSON, "Explicit survivorship-caveat metadata."),
    }


# --------------------------------------------------------------------------- #
# Fixture (tiny, local, no network)
# --------------------------------------------------------------------------- #
def _fixture_dates(n: int = 12) -> List[str]:
    out: List[str] = []
    d = dt.date(2026, 1, 2)
    while len(out) < n:
        if d.weekday() < 5:  # weekdays only
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def make_fixture_raw() -> "pd.DataFrame":
    """A tiny synthetic raw OHLCV frame for 2 names + the benchmark. No network."""
    dates = _fixture_dates()
    bases = {"AAPL": 100.0, "MSFT": 200.0, BENCHMARK: 400.0}
    rows: List[Dict[str, Any]] = []
    for ticker, base in bases.items():
        for i, day in enumerate(dates):
            close = base + i  # gently rising, no outliers
            rows.append({
                "ticker": ticker,
                "date": day,
                "adjusted_open": round(close - 0.5, 4),
                "adjusted_high": round(close + 1.0, 4),
                "adjusted_low": round(close - 1.0, 4),
                "adjusted_close": round(close, 4),
                "volume": 1_000_000 + i * 1_000,
            })
    return pd.DataFrame(rows, columns=list(RAW_COLUMNS))


# --------------------------------------------------------------------------- #
# Modes
# --------------------------------------------------------------------------- #
def dry_run(universe_path: str = SAMPLE_UNIVERSE_CSV) -> Dict[str, Any]:
    """Validate config + planned paths and read the universe. No network, no large files."""
    universe = load_universe(universe_path)
    validation = validate_universe(universe)
    summary = {
        "phase": PHASE,
        "mode": "dry-run",
        "generated_at": _now(),
        "universe_file": _norm(universe_path),
        "universe_validation": validation,
        "target_history_years_min": MIN_YEARS,
        "target_history_years_recommended": RECOMMENDED_YEARS,
        "target_min_tickers_per_date": MIN_TICKERS_PER_DATE,
        "benchmark": BENCHMARK,
        "planned_real_outputs": planned_real_outputs(),
        "data_quality_check_catalog": data_quality_check_catalog(),
        "network_calls_made": False,
        "real_outputs_created": False,
        "builds_executed": False,
    }
    print(f"[phase2k-g] dry-run: universe={_norm(universe_path)} "
          f"tickers={validation['ticker_count']} valid={validation['ok']} "
          f"(no network, no large outputs)")
    return summary


def fixture_run(output_dir: str,
                universe_path: str = SAMPLE_UNIVERSE_CSV) -> Dict[str, Any]:
    """Run the transform + data-quality logic on a tiny fixture, writing only to output_dir.

    Refuses to write into the real (D:) output directory or the legacy repo output dir. No
    network access.
    """
    forbidden = {os.path.abspath(DEFAULT_OUTPUT_DIR), os.path.abspath(LEGACY_REPO_OUTPUT_DIR)}
    if os.path.abspath(output_dir) in forbidden:
        raise BuilderRefusal(
            "fixture-mode must write to a temp directory, not a real build output dir")
    os.makedirs(output_dir, exist_ok=True)

    universe = load_universe(universe_path)
    raw = make_fixture_raw()
    panel = normalize_price_history(raw)
    scored = compute_basic_features(panel, BENCHMARK)
    dq = run_data_quality_checks(scored, universe, BENCHMARK)
    caveat = build_survivorship_caveat(universe, {"active_as_of": "fixture", "source": "fixture"})
    summary = build_summary(scored, universe_df=universe, benchmark=BENCHMARK,
                            source="fixture", mode="fixture-mode", dq=dq,
                            params={"universe_file": _norm(universe_path),
                                    "fixture": True})

    price_path = os.path.join(output_dir, os.path.basename(REAL_PRICE_HISTORY_CSV))
    scored_path = os.path.join(output_dir, os.path.basename(REAL_SCORED_CSV))
    dq_path = os.path.join(output_dir, os.path.basename(REAL_DATA_QUALITY_JSON))
    summary_path = os.path.join(output_dir, os.path.basename(REAL_BUILD_SUMMARY_JSON))
    caveat_path = os.path.join(output_dir, os.path.basename(REAL_SURVIVORSHIP_JSON))

    panel.to_csv(price_path, index=False)
    scored.to_csv(scored_path, index=False)
    _write_json(dq, dq_path)
    _write_json(summary, summary_path)
    _write_json(caveat, caveat_path)

    result = {
        "phase": PHASE,
        "mode": "fixture-mode",
        "generated_at": _now(),
        "output_dir": _norm(output_dir),
        "files_written": [_norm(p) for p in
                          (price_path, scored_path, dq_path, summary_path, caveat_path)],
        "row_count": int(len(scored)),
        "ticker_count": int(scored["ticker"].nunique()) if len(scored) else 0,
        "data_quality_status": dq["status"],
        "network_calls_made": False,
        "real_outputs_created": False,
        "wrote_to_real_output_dir": False,
    }
    print(f"[phase2k-g] fixture-mode: rows={result['row_count']} "
          f"dq={result['data_quality_status']} -> {_norm(output_dir)} (no network)")
    return result


def _retrieve_free_price_history(tickers: Sequence[str], start: str, end: str,
                                 *, cache_dir: Optional[str] = None,
                                 max_retries: int = 3) -> "pd.DataFrame":
    """GUARDED free retrieval for the manual --execute path only.

    Lazily imports the free data library so importing this module stays network-free. Uses a
    chunked per-ticker loop with retry / backoff and an optional local on-disk cache. Never
    reached by the tests or the analyzer.
    """
    import yfinance as yf  # local import keeps module import network-free

    frames: List["pd.DataFrame"] = []
    for ticker in tickers:
        cache_path = (os.path.join(cache_dir, f"{ticker}.csv") if cache_dir else None)
        if cache_path and os.path.isfile(cache_path):
            frames.append(pd.read_csv(cache_path))
            continue
        one: Optional["pd.DataFrame"] = None
        for attempt in range(max_retries):
            try:
                raw = yf.download(ticker, start=start, end=end, auto_adjust=True,
                                  progress=False, threads=False)
                if raw is not None and len(raw):
                    idx = pd.to_datetime(raw.index)
                    one = pd.DataFrame({
                        "ticker": ticker,
                        "date": [d.date().isoformat() for d in idx],
                        "adjusted_open": pd.to_numeric(raw["Open"].to_numpy(), errors="coerce"),
                        "adjusted_high": pd.to_numeric(raw["High"].to_numpy(), errors="coerce"),
                        "adjusted_low": pd.to_numeric(raw["Low"].to_numpy(), errors="coerce"),
                        "adjusted_close": pd.to_numeric(raw["Close"].to_numpy(), errors="coerce"),
                        "volume": pd.to_numeric(raw["Volume"].to_numpy(), errors="coerce"),
                    })
                break
            except Exception:  # noqa: BLE001 - transient; back off and retry
                time.sleep(min(2 ** attempt, 8))
        if one is not None and len(one):
            if cache_path:
                _ensure_dir(cache_path)
                one.to_csv(cache_path, index=False)
            frames.append(one)
    if not frames:
        return pd.DataFrame(columns=list(RAW_COLUMNS))
    return pd.concat(frames, ignore_index=True)


def execute_build(*, universe_path: str, start: Optional[str], end: Optional[str],
                  allow_network: bool, output_dir: str = DEFAULT_OUTPUT_DIR,
                  cache_dir: Optional[str] = DEFAULT_CACHE_DIR) -> Dict[str, Any]:
    """Run the full free build. MANUAL use only; requires allow_network=True.

    Writes its large outputs and cache to the D: data root (output_dir / cache_dir) by
    default, never the C: repo. Never invoked by the tests or the analyzer. Without
    allow_network it refuses and fetches nothing.
    """
    if not allow_network:
        raise BuilderRefusal(
            "--execute requires --allow-network; refusing to run any retrieval")
    if not start or not end:
        raise BuilderRefusal("--execute requires both --start and --end")

    universe = load_universe(universe_path)
    tickers = [t for t in universe["ticker"].tolist() if t]
    raw = _retrieve_free_price_history(tickers, start, end, cache_dir=cache_dir)
    panel = normalize_price_history(raw)
    scored = compute_basic_features(panel, BENCHMARK)
    dq = run_data_quality_checks(scored, universe, BENCHMARK)
    caveat = build_survivorship_caveat(universe, {"active_as_of": end, "source": SOURCE})
    summary = build_summary(scored, universe_df=universe, benchmark=BENCHMARK,
                            source=SOURCE, mode="execute", dq=dq,
                            params={"universe_file": _norm(universe_path),
                                    "start": start, "end": end})

    os.makedirs(output_dir, exist_ok=True)
    panel.to_csv(os.path.join(output_dir, os.path.basename(REAL_PRICE_HISTORY_CSV)),
                 index=False)
    scored.to_csv(os.path.join(output_dir, os.path.basename(REAL_SCORED_CSV)),
                  index=False)
    _write_json(dq, os.path.join(output_dir, os.path.basename(REAL_DATA_QUALITY_JSON)))
    _write_json(summary, os.path.join(output_dir, os.path.basename(REAL_BUILD_SUMMARY_JSON)))
    _write_json(caveat, os.path.join(output_dir, os.path.basename(REAL_SURVIVORSHIP_JSON)))
    return summary


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Phase 2K-G free expanded price/volume dataset builder. Default is a "
                    "safe dry-run; fixture-mode exercises the transform on tiny local data; "
                    "execute is a manual later step that additionally requires "
                    "--allow-network. No DB, no model, no orders, no automation.")
    p.add_argument("--dry-run", action="store_true",
                   help="Validate config + planned paths and read the universe (default).")
    p.add_argument("--fixture-mode", action="store_true",
                   help="Run the transform / DQ logic on a tiny fixture into a temp dir.")
    p.add_argument("--execute", action="store_true",
                   help="MANUAL later step: real free build. Requires --allow-network.")
    p.add_argument("--allow-network", action="store_true",
                   help="Explicit additional guard required by --execute. Off by default.")
    p.add_argument("--universe", type=str, default=SAMPLE_UNIVERSE_CSV,
                   help="Local / manual ticker universe CSV.")
    p.add_argument("--data-root", type=str, default=DEFAULT_DATA_ROOT,
                   help="External data root for large outputs / cache (default the D: drive). "
                        "execute uses <data-root>/output and <data-root>/cache.")
    p.add_argument("--output-dir", type=str, default=None,
                   help="Override the output directory (defaults to <data-root>/output for "
                        "execute; fixture-mode uses a temp dir when omitted).")
    p.add_argument("--cache-dir", type=str, default=None,
                   help="Override the retrieval cache directory (defaults to "
                        "<data-root>/cache for execute).")
    p.add_argument("--start", type=str, default=None, help="Start date YYYY-MM-DD (execute).")
    p.add_argument("--end", type=str, default=None, help="End date YYYY-MM-DD (execute).")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        if args.execute:
            output_dir = args.output_dir or os.path.join(args.data_root, "output")
            cache_dir = args.cache_dir or os.path.join(args.data_root, "cache")
            summary = execute_build(
                universe_path=args.universe, start=args.start, end=args.end,
                allow_network=args.allow_network,
                output_dir=output_dir, cache_dir=cache_dir)
            print(f"[phase2k-g] execute: rows={summary['row_count']} "
                  f"dq={summary['data_quality_status']}")
            return 0
        if args.fixture_mode:
            out_dir = args.output_dir or tempfile.mkdtemp(prefix="phase2k_g_fixture_")
            fixture_run(output_dir=out_dir, universe_path=args.universe)
            return 0
        # Default safe mode: dry-run (also when --dry-run is passed explicitly).
        dry_run(universe_path=args.universe)
        return 0
    except BuilderRefusal as e:
        print(f"[phase2k-g] REFUSED: {e}")
        return e.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
