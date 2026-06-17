"""Phase 2G-C real-data price-history export (yfinance only, file-based).

Guardrail rationale lives in docs/phase2g_c_real_data_results_v1.md, not here.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

PHASE = "2G-C"
SOURCE = "yfinance"
BENCHMARK = "SPY"

# Required output schema. Volume is emitted as a fourth column when available.
SCHEMA_REQUIRED = ("ticker", "date", "adj_close")
VOLUME_COLUMN = "volume"

# Curated 40-name liquid, sector-diversified universe (benchmark added below).
CURATED_40 = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO",
    "JPM", "V", "MA", "UNH", "HD", "PG", "JNJ", "XOM",
    "CVX", "KO", "PEP", "COST", "WMT", "BAC", "ABBV", "CRM",
    "ADBE", "NFLX", "AMD", "INTC", "CSCO", "ORCL", "QCOM", "TXN",
    "LIN", "MRK", "PFE", "TMO", "ACN", "NKE", "DIS", "MCD",
]

DEFAULT_OUTPUT_CSV = os.path.join(
    "research", "output", "phase2g_price_history_real.csv")
DEFAULT_SUMMARY_JSON = os.path.join(
    "research", "output", "phase2g_c_real_data_run_summary.json")


class ExportRefusal(Exception):
    """Raised when a required gate is missing. Carries a process exit code."""

    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


# --------------------------------------------------------------------------- #
# Universe + schema (pure)
# --------------------------------------------------------------------------- #
def curated_universe(benchmark: str = BENCHMARK) -> List[str]:
    """The 40-name curated list with the benchmark guaranteed present, deduped."""
    out: List[str] = []
    seen = set()
    for t in list(CURATED_40) + [benchmark]:
        t = str(t).strip().upper()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def validate_schema(columns: Sequence[str]) -> Tuple[bool, List[str]]:
    """Return (ok, missing). A valid export must carry ticker, date, adj_close."""
    cols = set(columns)
    missing = [c for c in SCHEMA_REQUIRED if c not in cols]
    return (not missing), missing


# --------------------------------------------------------------------------- #
# Run-request gates (pure)
# --------------------------------------------------------------------------- #
def validate_run_request(*, source: Optional[str], confirm_network: bool,
                         start: Optional[str], end: Optional[str]) -> None:
    """Refuse the run unless every explicit gate is supplied. Pure / no I/O."""
    if source != SOURCE:
        raise ExportRefusal(
            f"refusing to run: --source {SOURCE} is required (got {source!r})")
    if not confirm_network:
        raise ExportRefusal(
            "refusing to run: --confirm-network is required for a network fetch")
    if not start or not end:
        raise ExportRefusal(
            "refusing to run: both --start and --end are required")


# --------------------------------------------------------------------------- #
# yfinance fetch (network, read-only)
# --------------------------------------------------------------------------- #
def _series_for(raw: "pd.DataFrame", field: str, ticker: str) -> Optional["pd.Series"]:
    """Pull a single price field as a Series, handling flat or grouped columns."""
    cols = raw.columns
    if isinstance(cols, pd.MultiIndex):
        if (field, ticker) in cols:
            return raw[(field, ticker)]
        level0 = cols.get_level_values(0)
        if field in set(level0):
            sub = raw[field]
            if isinstance(sub, pd.DataFrame):
                if ticker in sub.columns:
                    return sub[ticker]
                return sub.iloc[:, 0]
            return sub
        return None
    if field in cols:
        return raw[field]
    return None


def fetch_one(ticker: str, start: str, end: str) -> Optional["pd.DataFrame"]:
    """Fetch one ticker's adjusted-close (and volume when present) via yfinance.

    Returns a long-format frame (ticker, date, adj_close[, volume]) or None when
    nothing came back. Adjusted close falls back to close only if adj is absent.
    """
    import yfinance as yf  # local import keeps module import network-free

    raw = yf.download(ticker, start=start, end=end, auto_adjust=False,
                      progress=False, threads=False)
    if raw is None or len(raw) == 0:
        return None

    adj = _series_for(raw, "Adj Close", ticker)
    if adj is None:
        adj = _series_for(raw, "Close", ticker)
    if adj is None:
        return None
    vol = _series_for(raw, "Volume", ticker)

    idx = pd.to_datetime(raw.index)
    frame = pd.DataFrame({
        "ticker": ticker,
        "date": [d.date().isoformat() for d in idx],
        "adj_close": pd.to_numeric(pd.Series(adj).to_numpy(), errors="coerce"),
    })
    if vol is not None:
        frame[VOLUME_COLUMN] = pd.to_numeric(
            pd.Series(vol).to_numpy(), errors="coerce")
    frame = frame[frame["adj_close"].notna() & (frame["adj_close"] > 0)]
    return frame if len(frame) else None


def fetch_universe(tickers: Sequence[str], start: str, end: str
                   ) -> Tuple["pd.DataFrame", List[str]]:
    """Fetch every ticker; return the concatenated frame and the missing list."""
    frames: List["pd.DataFrame"] = []
    missing: List[str] = []
    for t in tickers:
        one = fetch_one(t, start, end)
        if one is None or one.empty:
            missing.append(t)
            continue
        frames.append(one)
    if not frames:
        cols = list(SCHEMA_REQUIRED) + [VOLUME_COLUMN]
        return pd.DataFrame(columns=cols), missing
    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values(["ticker", "date"]).reset_index(drop=True)
    return out, missing


# --------------------------------------------------------------------------- #
# Summary (pure)
# --------------------------------------------------------------------------- #
def build_summary(frame: "pd.DataFrame", *, universe: Sequence[str],
                  benchmark: str, start: str, end: str,
                  missing: Sequence[str]) -> Dict[str, Any]:
    """Assemble the JSON-safe run summary with all required safety flags."""
    has_volume = VOLUME_COLUMN in frame.columns
    volume_present = bool(
        has_volume and len(frame) and frame[VOLUME_COLUMN].notna().any())

    tickers = (set(str(t) for t in frame["ticker"].unique())
               if len(frame) else set())
    dates = (sorted(str(d) for d in frame["date"].unique())
             if len(frame) else [])

    return {
        "phase": PHASE,
        "source": SOURCE,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        # Safety flags (machine-readable; asserted by the tests).
        "database_touched": False,
        "database_write_executed": False,
        "migration_executed": False,
        "deployment_executed": False,
        "model_v2_enabled": False,
        "production_edge_claimed": False,
        "no_trading": True,
        "no_orders": True,
        "no_automation": True,
        # Export facts.
        "row_count": int(len(frame)),
        "ticker_count": int(len(tickers)),
        "universe_ticker_count": int(len(universe)),
        "date_start": dates[0] if dates else None,
        "date_end": dates[-1] if dates else None,
        "benchmark": benchmark,
        "benchmark_present": benchmark in tickers,
        "volume_present": volume_present,
        "requested_start": start,
        "requested_end": end,
        "missing_tickers": list(missing),
    }


# --------------------------------------------------------------------------- #
# I/O
# --------------------------------------------------------------------------- #
def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)


def write_csv(frame: "pd.DataFrame", path: str) -> None:
    ok, missing = validate_schema(frame.columns)
    if not ok:
        raise ExportRefusal(
            f"refusing to write: export frame missing columns {missing}")
    _ensure_dir(path)
    frame.to_csv(path, index=False)


def write_summary(summary: Dict[str, Any], path: str) -> None:
    _ensure_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, allow_nan=False)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run_export(*, source: Optional[str], confirm_network: bool,
               start: Optional[str], end: Optional[str],
               output_csv: str = DEFAULT_OUTPUT_CSV,
               summary_json: str = DEFAULT_SUMMARY_JSON,
               benchmark: str = BENCHMARK) -> Dict[str, Any]:
    """Validate the gates, fetch the universe, write the CSV and the summary."""
    validate_run_request(source=source, confirm_network=confirm_network,
                          start=start, end=end)
    universe = curated_universe(benchmark)
    frame, missing = fetch_universe(universe, start, end)
    write_csv(frame, output_csv)
    summary = build_summary(frame, universe=universe, benchmark=benchmark,
                            start=start, end=end, missing=missing)
    summary["output_csv"] = output_csv
    summary["summary_json"] = summary_json
    write_summary(summary, summary_json)
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Phase 2G-C real-data price-history export via yfinance. "
                    "Read-only fetch to local CSV; no DB, no deploy, no flag "
                    "enable, no trading.")
    p.add_argument("--source", type=str, default=None,
                   help="Data source. Must be 'yfinance'. REQUIRED.")
    p.add_argument("--confirm-network", action="store_true",
                   help="Explicitly confirm an outbound network fetch. REQUIRED.")
    p.add_argument("--start", type=str, default=None,
                   help="Inclusive start date YYYY-MM-DD. REQUIRED.")
    p.add_argument("--end", type=str, default=None,
                   help="Exclusive end date YYYY-MM-DD. REQUIRED.")
    p.add_argument("--output-csv", type=str, default=DEFAULT_OUTPUT_CSV)
    p.add_argument("--summary-json", type=str, default=DEFAULT_SUMMARY_JSON)
    p.add_argument("--benchmark", type=str, default=BENCHMARK)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        summary = run_export(
            source=args.source, confirm_network=args.confirm_network,
            start=args.start, end=args.end, output_csv=args.output_csv,
            summary_json=args.summary_json, benchmark=args.benchmark)
    except ExportRefusal as e:
        print(f"[phase2g-c] REFUSED: {e}")
        return e.exit_code

    print(f"[phase2g-c] source            : {summary['source']}")
    print(f"[phase2g-c] universe tickers  : {summary['universe_ticker_count']}")
    print(f"[phase2g-c] rows / tickers    : {summary['row_count']} / "
          f"{summary['ticker_count']}")
    print(f"[phase2g-c] date range        : {summary['date_start']} -> "
          f"{summary['date_end']}")
    print(f"[phase2g-c] benchmark_present : {summary['benchmark_present']}")
    print(f"[phase2g-c] volume_present    : {summary['volume_present']}")
    print(f"[phase2g-c] missing tickers   : {summary['missing_tickers']}")
    print(f"[phase2g-c] database_touched  : {summary['database_touched']}")
    print(f"[phase2g-c] output csv        : {summary['output_csv']}")
    print(f"[phase2g-c] summary json      : {summary['summary_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
