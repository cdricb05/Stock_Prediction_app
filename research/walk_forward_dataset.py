"""Point-in-time walk-forward evaluation dataset builder.

Builds, for each (ticker, as_of_date), the realized forward outcome that the
current model is graded against:

    realized_return_5d              future adj-close return over `horizon` sessions
    spy_return_5d                   SPY return over the SAME calendar interval
    realized_excess_return_5d_vs_spy
    positive_return_flag
    outperform_spy_flag

Leakage guarantees (enforced by the pure helpers, covered by tests):
- Features/model inputs only ever see rows with date <= as_of_date (the builder
  here produces *targets only*; the replay module slices inputs at <= as_of).
- The target uses sessions strictly in the future (index i+horizon).
- Rows without `horizon` future sessions, or without `min_history` past
  sessions, are excluded.

Trading sessions are taken from the actual rows present in `stock_prices`
(not calendar arithmetic), so "5 business days" means "5 available sessions".

This module imports neither api_server nor Paper Trader. DB access is confined
to functions (no work at import time) so the pure helpers are testable without
a database.
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

DEFAULT_HORIZON = 5
DEFAULT_MIN_HISTORY = 60
DEFAULT_BENCHMARK = "SPY"


# --------------------------------------------------------------------------- #
# Pure helpers (no DB) — these carry the leakage-critical logic and are tested.
# --------------------------------------------------------------------------- #
def forward_return(adj: Sequence[float], i: int, horizon: int) -> Optional[float]:
    """Return adj[i+horizon]/adj[i] - 1, or None if the future session is absent.

    Pure and future-only: never references indices < i for the target.
    """
    n = len(adj)
    j = i + horizon
    if j >= n:
        return None
    base = float(adj[i])
    if base <= 0:
        return None
    fut = float(adj[j])
    return fut / base - 1.0


def benchmark_return_over(spy_dates: Sequence, spy_adj: Sequence[float],
                          as_of: dt.date, target_date: dt.date) -> Optional[float]:
    """Benchmark return over the interval (as_of, target_date].

    Uses as-of-or-before lookups on the SPY series so ticker/benchmark calendar
    mismatches are handled: base = last SPY close on/before as_of; future = last
    SPY close on/before target_date. Returns None if either side is unavailable
    or the interval does not actually move forward in the SPY calendar.
    """
    base = _asof_value(spy_dates, spy_adj, as_of)
    fut = _asof_value(spy_dates, spy_adj, target_date)
    if base is None or fut is None or base <= 0:
        return None
    base_d = _asof_date(spy_dates, as_of)
    fut_d = _asof_date(spy_dates, target_date)
    if base_d is None or fut_d is None or fut_d <= base_d:
        return None
    return fut / base - 1.0


def _asof_value(dates: Sequence, adj: Sequence[float], when: dt.date) -> Optional[float]:
    idx = _asof_index(dates, when)
    if idx is None:
        return None
    return float(adj[idx])


def _asof_date(dates: Sequence, when: dt.date):
    idx = _asof_index(dates, when)
    if idx is None:
        return None
    return _as_date(dates[idx])


def _asof_index(dates: Sequence, when: dt.date) -> Optional[int]:
    """Index of the last date <= `when` (dates assumed ascending). None if none."""
    lo, hi, res = 0, len(dates) - 1, None
    when = _as_date(when)
    while lo <= hi:
        mid = (lo + hi) // 2
        if _as_date(dates[mid]) <= when:
            res = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return res


def _as_date(x) -> dt.date:
    if isinstance(x, dt.datetime):
        return x.date()
    if isinstance(x, dt.date):
        return x
    return pd.Timestamp(x).date()


@dataclass
class TickerSeries:
    """Ascending, de-duplicated price series for one ticker."""
    ticker: str
    dates: List[dt.date] = field(default_factory=list)
    adj: List[float] = field(default_factory=list)


def build_rows_for_ticker(series: TickerSeries, spy: Optional[TickerSeries],
                          horizon: int = DEFAULT_HORIZON,
                          min_history: int = DEFAULT_MIN_HISTORY,
                          start_date: Optional[dt.date] = None,
                          end_date: Optional[dt.date] = None) -> List[Dict]:
    """Build all valid walk-forward target rows for a single ticker (pure)."""
    dates, adj = series.dates, series.adj
    n = len(dates)
    rows: List[Dict] = []
    spy_dates = spy.dates if spy else []
    spy_adj = spy.adj if spy else []
    for i in range(n):
        as_of = _as_date(dates[i])
        if start_date and as_of < start_date:
            continue
        if end_date and as_of > end_date:
            continue
        # Need at least `min_history` sessions up to and including as_of.
        if (i + 1) < min_history:
            continue
        ret = forward_return(adj, i, horizon)
        if ret is None:
            continue  # no future target -> excluded
        target_date = _as_date(dates[i + horizon])
        spy_ret = benchmark_return_over(spy_dates, spy_adj, as_of, target_date) if spy else None
        excess = (ret - spy_ret) if spy_ret is not None else None
        rows.append({
            "ticker": series.ticker,
            "as_of_date": as_of,
            "target_date": target_date,
            "n_history": i + 1,
            "current_adj_close": float(adj[i]),
            "future_adj_close": float(adj[i + horizon]),
            "realized_return_5d": ret,
            "spy_return_5d": spy_ret,
            "realized_excess_return_5d_vs_spy": excess,
            "positive_return_flag": bool(ret > 0),
            "outperform_spy_flag": (bool(excess > 0) if excess is not None else None),
        })
    return rows


# --------------------------------------------------------------------------- #
# DB access (confined to functions; nothing runs at import time).
# --------------------------------------------------------------------------- #
def effective_db_url(db_url: Optional[str] = None) -> str:
    raw = db_url or os.environ.get("DB_URL")
    if not raw:
        raise SystemExit("DB_URL not set. Pass --db-url or source api.env.")
    return raw.replace("@localhost:", "@127.0.0.1:")


def build_engine(db_url: Optional[str] = None):
    from sqlalchemy import create_engine
    return create_engine(effective_db_url(db_url), pool_pre_ping=True)


def list_universe(engine, max_tickers: Optional[int] = None,
                  exclude_benchmark: str = DEFAULT_BENCHMARK) -> List[str]:
    from sqlalchemy import text
    with engine.connect() as cx:
        rows = cx.execute(text(
            "select ticker from stock_prices group by ticker order by ticker")).all()
    tickers = [r[0] for r in rows if r[0] != exclude_benchmark]
    if max_tickers:
        tickers = tickers[:max_tickers]
    return tickers


def load_series(engine, ticker: str, end_date: Optional[dt.date] = None) -> TickerSeries:
    """Load one ascending, de-duplicated (date, adj_close) series.

    De-duplication: ``DISTINCT ON (date)`` keeping the highest ``id`` — the
    stock_prices table contains 1.6k duplicate (ticker,date) groups, which would
    otherwise corrupt both the model's lookback window and the target.
    """
    from sqlalchemy import text
    q = (
        "select distinct on (date) date, adj_close from stock_prices "
        "where ticker = :t {end} and adj_close is not null and adj_close > 0 "
        "order by date, id desc"
    ).format(end="and date <= :end" if end_date else "")
    params = {"t": ticker}
    if end_date:
        params["end"] = end_date
    with engine.connect() as cx:
        df = pd.read_sql_query(text(q), cx, params=params)
    if df.empty:
        return TickerSeries(ticker=ticker)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date")
    return TickerSeries(ticker=ticker,
                        dates=list(df["date"]),
                        adj=[float(x) for x in df["adj_close"]])


def build_walk_forward_dataset(engine=None, db_url: Optional[str] = None,
                               tickers: Optional[Sequence[str]] = None,
                               start_date: Optional[dt.date] = None,
                               end_date: Optional[dt.date] = None,
                               horizon_days: int = DEFAULT_HORIZON,
                               min_history: int = DEFAULT_MIN_HISTORY,
                               benchmark: str = DEFAULT_BENCHMARK,
                               max_tickers: Optional[int] = None,
                               series_cache: Optional[Dict[str, TickerSeries]] = None,
                               ) -> Tuple[pd.DataFrame, Dict[str, TickerSeries]]:
    """Build the full point-in-time target dataset.

    Returns (DataFrame, series_cache). The cache (ticker -> TickerSeries) is
    returned so the replay step can reuse the loaded, de-duplicated series
    without re-querying the database.
    """
    own_engine = False
    if engine is None:
        engine = build_engine(db_url)
        own_engine = True
    cache: Dict[str, TickerSeries] = series_cache if series_cache is not None else {}
    try:
        if tickers is None:
            tickers = list_universe(engine, max_tickers=max_tickers, exclude_benchmark=benchmark)
        else:
            tickers = [t for t in tickers if t != benchmark]
            if max_tickers:
                tickers = tickers[:max_tickers]

        if benchmark not in cache:
            cache[benchmark] = load_series(engine, benchmark, end_date=end_date)
        spy = cache[benchmark]
        spy = spy if spy.dates else None

        all_rows: List[Dict] = []
        for t in tickers:
            if t not in cache:
                cache[t] = load_series(engine, t, end_date=end_date)
            series = cache[t]
            if not series.dates:
                continue
            all_rows.extend(build_rows_for_ticker(
                series, spy, horizon=horizon_days, min_history=min_history,
                start_date=start_date, end_date=end_date))
        cols = ["ticker", "as_of_date", "target_date", "n_history",
                "current_adj_close", "future_adj_close", "realized_return_5d",
                "spy_return_5d", "realized_excess_return_5d_vs_spy",
                "positive_return_flag", "outperform_spy_flag"]
        df = pd.DataFrame(all_rows, columns=cols)
        return df, cache
    finally:
        if own_engine:
            engine.dispose()
