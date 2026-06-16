"""Phase 2A — point-in-time cross-sectional feature builder.

Builds, for each ``(ticker, as_of_date)``, the feature vector that the Phase 2B
ranking model will train on. Every feature is computed strictly from rows with
``date <= as_of_date`` (the leakage guarantee, enforced by the pure helpers and
covered by ``tests/test_phase2_features.py``).

Design (mirrors ``research/walk_forward_dataset.py``):
- Pure helpers take plain arrays / ``TickerSeries`` so the feature math runs
  without a database, a network, or the production model stack.
- DB access is confined to functions; nothing runs at import time.
- We reuse ``research.walk_forward_dataset`` for de-duplicated point-in-time
  series loading (``load_series``) and for the forward labels
  (``build_rows_for_ticker``) so features and labels share one source of truth.
- This module imports neither ``api_server`` nor Paper Trader and never writes to
  the database.

Feature families (v1):
  momentum       return_{5,10,21,63,126}d, momentum_12_1
  volatility     realized_vol_{21,63}d, downside_vol_21d
  spy_relative   excess_return_vs_spy_{21,63}d, rolling_beta_63d,
                 rolling_corr_spy_63d
  regime         spy_return_{21,63}d, spy_realized_vol_21d, spy_above_200d
  volume         avg_dollar_volume_21d, volume_zscore_21d   (OPTIONAL — emitted
                 only when a real, sufficiently-populated volume series is
                 supplied; otherwise ``None`` and the family is reported
                 unavailable; never fabricated)

Disabled-but-declared families (no honest point-in-time source today, so they
are never emitted as values): sector/industry, earnings/events, news/sentiment,
macro. See ``DISABLED_FEATURE_FAMILIES``.
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# Reuse the de-dup loader, the label builder, and the as-of helpers so features
# and labels share one point-in-time source of truth.
from research.walk_forward_dataset import (  # noqa: E402
    DEFAULT_BENCHMARK,
    DEFAULT_HORIZON,
    TickerSeries,
    _as_date,
    _asof_index,
    build_engine,
    build_rows_for_ticker,
    list_universe,
    load_series,
)

FEATURE_SET_VERSION = "v1"

TRADING_DAYS_PER_YEAR = 252
_ANNUALIZE = math.sqrt(TRADING_DAYS_PER_YEAR)

# Minimum history (sessions up to and including as_of) required before we emit a
# feature row at all. 200 lets the longest lookback (SPY 200d SMA / 126d return)
# resolve; shorter-lookback features still degrade to None individually when a
# specific window is unavailable.
DEFAULT_MIN_HISTORY = 200

# Feature-name groups (also the column order in the output dataset).
MOMENTUM_FEATURES = [
    "return_5d", "return_10d", "return_21d", "return_63d", "return_126d",
    "momentum_12_1",
]
VOLATILITY_FEATURES = ["realized_vol_21d", "realized_vol_63d", "downside_vol_21d"]
SPY_RELATIVE_FEATURES = [
    "excess_return_vs_spy_21d", "excess_return_vs_spy_63d",
    "rolling_beta_63d", "rolling_corr_spy_63d",
]
REGIME_FEATURES = [
    "spy_return_21d", "spy_return_63d", "spy_realized_vol_21d", "spy_above_200d",
]
VOLUME_FEATURES = ["avg_dollar_volume_21d", "volume_zscore_21d"]

# Families we deliberately do NOT fabricate in v1. Each maps to the reason it is
# disabled; the builder reports these via ``unavailable_feature_families``.
DISABLED_FEATURE_FAMILIES: Dict[str, str] = {
    "sector_industry": (
        "No timestamped sector/industry mapping is wired in. A point-in-time "
        "membership source is required before sector-relative features are honest."
    ),
    "earnings_events": (
        "No real earnings/event calendar is ingested. days-to/since-earnings and "
        "event flags would require synthetic dates, which are forbidden."
    ),
    "news_sentiment": (
        "No news/sentiment feed is available. Future optional module; never "
        "fabricated."
    ),
    "macro": (
        "No timestamped macro series (rates, CPI, etc.) is ingested. Future "
        "optional module; never fabricated."
    ),
}


def feature_names(include_volume: bool = True) -> List[str]:
    """Ordered list of v1 feature column names."""
    names = (MOMENTUM_FEATURES + VOLATILITY_FEATURES + SPY_RELATIVE_FEATURES
             + REGIME_FEATURES)
    if include_volume:
        names = names + VOLUME_FEATURES
    return names


# --------------------------------------------------------------------------- #
# Pure helpers (no DB). These carry the leakage-critical math and are tested.
# Convention: every helper indexed by ``i`` uses ONLY adj[.. i] (and the aligned
# benchmark up to the same anchor) — never an index > i.
# --------------------------------------------------------------------------- #
def trailing_return(adj: Sequence[float], i: int, window: int) -> Optional[float]:
    """``adj[i] / adj[i-window] - 1`` (past-only). None if window unavailable."""
    if window <= 0 or i - window < 0 or i >= len(adj):
        return None
    base = float(adj[i - window])
    if base <= 0:
        return None
    return float(adj[i]) / base - 1.0


def momentum_12_1(adj: Sequence[float], i: int) -> Optional[float]:
    """12-1 momentum: cumulative return from ~12m ago to ~1m ago.

    ``adj[i-21] / adj[i-252] - 1`` — skips the most recent 21 sessions (the
    classic short-term-reversal exclusion). Past-only; None before 252 history.
    """
    if i - 252 < 0:
        return None
    base = float(adj[i - 252])
    if base <= 0:
        return None
    return float(adj[i - 21]) / base - 1.0


def daily_returns(adj: Sequence[float]) -> np.ndarray:
    """Simple daily returns; element k is ``adj[k]/adj[k-1]-1`` for k>=1.

    Returned array is aligned to ``adj`` with index 0 = NaN (no prior close).
    Non-positive bases yield NaN at that position. Pure / past-consistent: the
    value at index k depends only on indices k-1 and k.
    """
    a = np.asarray(adj, dtype=float)
    out = np.full(a.shape, np.nan, dtype=float)
    if len(a) >= 2:
        prev = a[:-1]
        cur = a[1:]
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.where(prev > 0, cur / prev - 1.0, np.nan)
        out[1:] = r
    return out


def realized_vol(adj: Sequence[float], i: int, window: int,
                 annualize: bool = True) -> Optional[float]:
    """Sample std (ddof=1) of the last ``window`` daily returns ending at i.

    Uses returns at indices ``i-window+1 .. i`` (each needs its own prior close,
    so the earliest used close is index ``i-window``). None if unavailable.
    """
    if window < 2 or i - window < 0 or i >= len(adj):
        return None
    r = daily_returns(adj)[i - window + 1: i + 1]
    if len(r) < window or np.any(np.isnan(r)):
        return None
    vol = float(np.std(r, ddof=1))
    return vol * _ANNUALIZE if annualize else vol


def downside_vol(adj: Sequence[float], i: int, window: int = 21,
                 annualize: bool = True) -> Optional[float]:
    """Downside volatility: RMS of the negative daily returns in the window.

    ``sqrt(mean(r_neg**2))`` over the negative returns among the last ``window``
    daily returns ending at i (0.0 if there are no negatives). Annualized by
    default. Past-only; None if the window is unavailable.
    """
    if window < 1 or i - window < 0 or i >= len(adj):
        return None
    r = daily_returns(adj)[i - window + 1: i + 1]
    if len(r) < window or np.any(np.isnan(r)):
        return None
    neg = r[r < 0.0]
    ds = math.sqrt(float(np.mean(neg ** 2))) if len(neg) else 0.0
    return ds * _ANNUALIZE if annualize else ds


def align_benchmark(ticker_dates: Sequence, spy: Optional[TickerSeries]) -> np.ndarray:
    """Benchmark adj-close as-of each ticker date (last SPY close on/before).

    Returns an array parallel to ``ticker_dates``; positions with no SPY history
    on/before that date are NaN. As-of lookups make ticker/benchmark calendar
    mismatches safe and keep the result point-in-time.
    """
    out = np.full(len(ticker_dates), np.nan, dtype=float)
    if spy is None or not spy.dates:
        return out
    for k, d in enumerate(ticker_dates):
        idx = _asof_index(spy.dates, _as_date(d))
        if idx is not None:
            out[k] = float(spy.adj[idx])
    return out


def rolling_beta(ticker_rets: Sequence[float], spy_rets: Sequence[float],
                 i: int, window: int = 63) -> Optional[float]:
    """OLS beta of ticker vs benchmark daily returns over the trailing window.

    ``cov(tr, sr) / var(sr)`` on returns at indices ``i-window+1 .. i``. None if
    any pair is missing or benchmark variance is zero. Past-only.
    """
    pair = _trailing_return_pairs(ticker_rets, spy_rets, i, window)
    if pair is None:
        return None
    tr, sr = pair
    var = float(np.var(sr, ddof=1))
    if var <= 0.0:
        return None
    cov = float(np.cov(tr, sr, ddof=1)[0, 1])
    return cov / var


def rolling_corr(ticker_rets: Sequence[float], spy_rets: Sequence[float],
                 i: int, window: int = 63) -> Optional[float]:
    """Pearson correlation of ticker vs benchmark daily returns over the window."""
    pair = _trailing_return_pairs(ticker_rets, spy_rets, i, window)
    if pair is None:
        return None
    tr, sr = pair
    if np.std(tr) == 0.0 or np.std(sr) == 0.0:
        return None
    return float(np.corrcoef(tr, sr)[0, 1])


def _trailing_return_pairs(ticker_rets: Sequence[float], spy_rets: Sequence[float],
                           i: int, window: int):
    """Aligned trailing return windows, or None if incomplete/NaN."""
    if window < 2 or i - window + 1 < 0 or i >= len(ticker_rets):
        return None
    tr = np.asarray(ticker_rets, dtype=float)[i - window + 1: i + 1]
    sr = np.asarray(spy_rets, dtype=float)[i - window + 1: i + 1]
    if len(tr) < window or len(sr) < window:
        return None
    if np.any(np.isnan(tr)) or np.any(np.isnan(sr)):
        return None
    return tr, sr


def volume_window_available(volume: Optional[Sequence[float]], i: int,
                            window: int) -> bool:
    """True only if a real volume window (all finite, non-negative) is present.

    The gate behind every volume feature. When this is False we emit ``None`` and
    mark the family unavailable — we never synthesize volume.
    """
    if volume is None:
        return False
    if window < 1 or i - window + 1 < 0 or i >= len(volume):
        return False
    w = np.asarray(volume[i - window + 1: i + 1], dtype=float)
    if len(w) < window:
        return False
    return bool(np.all(np.isfinite(w)) and np.all(w >= 0.0))


def avg_dollar_volume(adj: Sequence[float], volume: Optional[Sequence[float]],
                      i: int, window: int = 21) -> Optional[float]:
    """Mean of ``adj_close * volume`` over the trailing window. None if no volume."""
    if not volume_window_available(volume, i, window):
        return None
    a = np.asarray(adj[i - window + 1: i + 1], dtype=float)
    v = np.asarray(volume[i - window + 1: i + 1], dtype=float)
    if len(a) < window:
        return None
    return float(np.mean(a * v))


def volume_zscore(volume: Optional[Sequence[float]], i: int,
                  window: int = 21) -> Optional[float]:
    """Z-score of today's volume vs the trailing window. None if no volume."""
    if not volume_window_available(volume, i, window):
        return None
    v = np.asarray(volume[i - window + 1: i + 1], dtype=float)
    sd = float(np.std(v, ddof=1))
    if sd <= 0.0:
        return None
    return (float(volume[i]) - float(np.mean(v))) / sd


def market_regime_features(spy: Optional[TickerSeries],
                           as_of: dt.date) -> Dict[str, Optional[float]]:
    """Same-for-all-names regime context computed on the SPY series at as_of.

    Uses SPY's own sessions and the last SPY close on/before ``as_of`` as the
    anchor (point-in-time). Each sub-feature degrades to None when its lookback
    is unavailable.
    """
    out: Dict[str, Optional[float]] = {k: None for k in REGIME_FEATURES}
    if spy is None or not spy.dates:
        return out
    j = _asof_index(spy.dates, _as_date(as_of))
    if j is None:
        return out
    adj = spy.adj
    out["spy_return_21d"] = trailing_return(adj, j, 21)
    out["spy_return_63d"] = trailing_return(adj, j, 63)
    out["spy_realized_vol_21d"] = realized_vol(adj, j, 21)
    if j - 200 >= 0:
        sma200 = float(np.mean(np.asarray(adj[j - 199: j + 1], dtype=float)))
        out["spy_above_200d"] = 1.0 if float(adj[j]) > sma200 else 0.0
    return out


# --------------------------------------------------------------------------- #
# Per-ticker feature assembly (pure given loaded series).
# --------------------------------------------------------------------------- #
def _compute_row_features(adj: Sequence[float], i: int,
                          ticker_rets: np.ndarray, spy_rets_aligned: np.ndarray,
                          spy_adj_aligned: np.ndarray, spy: Optional[TickerSeries],
                          as_of: dt.date,
                          volume: Optional[Sequence[float]]) -> Dict[str, Optional[float]]:
    """All v1 features for one (ticker, as_of=index i). Point-in-time (<= i)."""
    feats: Dict[str, Optional[float]] = {}

    # Momentum
    feats["return_5d"] = trailing_return(adj, i, 5)
    feats["return_10d"] = trailing_return(adj, i, 10)
    feats["return_21d"] = trailing_return(adj, i, 21)
    feats["return_63d"] = trailing_return(adj, i, 63)
    feats["return_126d"] = trailing_return(adj, i, 126)
    feats["momentum_12_1"] = momentum_12_1(adj, i)

    # Volatility
    feats["realized_vol_21d"] = realized_vol(adj, i, 21)
    feats["realized_vol_63d"] = realized_vol(adj, i, 63)
    feats["downside_vol_21d"] = downside_vol(adj, i, 21)

    # SPY-relative strength
    spy_ret_21 = trailing_return(spy_adj_aligned, i, 21)
    spy_ret_63 = trailing_return(spy_adj_aligned, i, 63)
    tr21 = trailing_return(adj, i, 21)
    tr63 = trailing_return(adj, i, 63)
    feats["excess_return_vs_spy_21d"] = (
        tr21 - spy_ret_21 if (tr21 is not None and spy_ret_21 is not None) else None)
    feats["excess_return_vs_spy_63d"] = (
        tr63 - spy_ret_63 if (tr63 is not None and spy_ret_63 is not None) else None)
    feats["rolling_beta_63d"] = rolling_beta(ticker_rets, spy_rets_aligned, i, 63)
    feats["rolling_corr_spy_63d"] = rolling_corr(ticker_rets, spy_rets_aligned, i, 63)

    # Market regime (computed on SPY's own calendar)
    feats.update(market_regime_features(spy, as_of))

    # Volume / liquidity (optional, presence-gated, never fabricated)
    feats["avg_dollar_volume_21d"] = avg_dollar_volume(adj, volume, i, 21)
    feats["volume_zscore_21d"] = volume_zscore(volume, i, 21)

    return feats


def build_feature_rows_for_ticker(series: TickerSeries, spy: Optional[TickerSeries],
                                  volume: Optional[Sequence[float]] = None,
                                  min_history: int = DEFAULT_MIN_HISTORY,
                                  start_date: Optional[dt.date] = None,
                                  end_date: Optional[dt.date] = None,
                                  feature_set_version: str = FEATURE_SET_VERSION
                                  ) -> List[Dict]:
    """Build all point-in-time feature rows for one ticker (pure).

    Each row is keyed ``(ticker, as_of_date, feature_set_version)`` plus the v1
    feature columns. ``volume`` (if given) must be parallel to ``series.adj``.
    """
    dates, adj = series.dates, series.adj
    n = len(dates)
    if volume is not None and len(volume) != n:
        raise ValueError("volume must be parallel to series.adj")
    spy_adj_aligned = align_benchmark(dates, spy)
    ticker_rets = daily_returns(adj)
    spy_rets_aligned = daily_returns(spy_adj_aligned)

    rows: List[Dict] = []
    for i in range(n):
        as_of = _as_date(dates[i])
        if start_date and as_of < start_date:
            continue
        if end_date and as_of > end_date:
            continue
        if (i + 1) < min_history:
            continue
        feats = _compute_row_features(
            adj, i, ticker_rets, spy_rets_aligned, spy_adj_aligned, spy, as_of, volume)
        row = {
            "ticker": series.ticker,
            "as_of_date": as_of,
            "feature_set_version": feature_set_version,
            "n_history": i + 1,
            "current_adj_close": float(adj[i]),
        }
        row.update(feats)
        rows.append(row)
    return rows


def cross_sectional_zscore(df: pd.DataFrame,
                           cols: Optional[Sequence[str]] = None,
                           suffix: str = "_z") -> pd.DataFrame:
    """Add within-date z-scored copies of ``cols`` (standardize per as_of_date).

    The ranking model consumes features standardized cross-sectionally within
    each ``as_of_date``. NaNs are ignored in the mean/std and stay NaN; a
    zero-variance date yields 0.0. Returns a new DataFrame; originals untouched.
    """
    if df.empty:
        return df.copy()
    if cols is None:
        cols = [c for c in feature_names() if c in df.columns]
    out = df.copy()

    def _z(s: pd.Series) -> pd.Series:
        mu = s.mean(skipna=True)
        sd = s.std(skipna=True, ddof=1)
        if not np.isfinite(sd) or sd == 0.0:
            return s.where(s.isna(), 0.0)
        return (s - mu) / sd

    for c in cols:
        out[c + suffix] = out.groupby("as_of_date")[c].transform(_z)
    return out


# --------------------------------------------------------------------------- #
# Labeled dataset (features joined to the existing forward labels).
# --------------------------------------------------------------------------- #
LABEL_COLUMNS = [
    "target_date", "realized_return_5d", "spy_return_5d",
    "realized_excess_return_5d_vs_spy", "positive_return_flag",
    "outperform_spy_flag",
]


def join_features_and_labels(feature_rows: Sequence[Dict],
                             label_rows: Sequence[Dict]) -> pd.DataFrame:
    """Inner-join feature rows to forward-label rows on (ticker, as_of_date).

    Labels come from ``research.walk_forward_dataset.build_rows_for_ticker`` so
    features and targets are guaranteed to share the same point-in-time anchor.
    Inner join: only as_of dates that have BOTH a full feature row and a realized
    forward label survive (rows in the last ``horizon`` sessions have no label).
    """
    fdf = pd.DataFrame(list(feature_rows))
    ldf = pd.DataFrame(list(label_rows))
    if fdf.empty or ldf.empty:
        return pd.DataFrame()
    label_keep = ["ticker", "as_of_date"] + [c for c in LABEL_COLUMNS if c in ldf.columns]
    merged = fdf.merge(ldf[label_keep], on=["ticker", "as_of_date"], how="inner")
    return merged


def build_labeled_dataset_for_ticker(series: TickerSeries, spy: Optional[TickerSeries],
                                     volume: Optional[Sequence[float]] = None,
                                     horizon: int = DEFAULT_HORIZON,
                                     min_history: int = DEFAULT_MIN_HISTORY,
                                     start_date: Optional[dt.date] = None,
                                     end_date: Optional[dt.date] = None
                                     ) -> pd.DataFrame:
    """Feature rows joined to 5-day forward labels for one ticker (pure)."""
    feats = build_feature_rows_for_ticker(
        series, spy, volume=volume, min_history=min_history,
        start_date=start_date, end_date=end_date)
    labels = build_rows_for_ticker(
        series, spy, horizon=horizon, min_history=min(min_history, 1),
        start_date=start_date, end_date=end_date)
    return join_features_and_labels(feats, labels)


def unavailable_feature_families(volume_present: bool) -> Dict[str, str]:
    """Report which families are disabled for the given inputs.

    Always includes ``DISABLED_FEATURE_FAMILIES``; adds ``volume_liquidity`` when
    no usable volume series is present.
    """
    out = dict(DISABLED_FEATURE_FAMILIES)
    if not volume_present:
        out["volume_liquidity"] = (
            "No usable volume series supplied (column absent or not populated for "
            "these rows). Volume features emitted as None; never fabricated."
        )
    return out


# --------------------------------------------------------------------------- #
# DB access (confined to functions; nothing runs at import time).
# Reads only — no writes, no schema migration.
# --------------------------------------------------------------------------- #
def load_series_with_volume(engine, ticker: str,
                            end_date: Optional[dt.date] = None
                            ) -> Tuple[TickerSeries, Optional[List[float]]]:
    """Load a de-duplicated (date, adj_close, volume) series, point-in-time.

    Mirrors ``walk_forward_dataset.load_series`` (``DISTINCT ON (date)`` keeping
    the highest ``id``) but also pulls ``volume`` when the column exists. Returns
    ``(TickerSeries, volume_list_or_None)``. ``volume`` is None when the column is
    absent OR fully null for this ticker — the honest "no volume" signal. Falls
    back cleanly (volume=None) if the column does not exist in the schema.
    """
    from sqlalchemy import text
    end_clause = "and date <= :end" if end_date else ""
    params: Dict[str, object] = {"t": ticker}
    if end_date:
        params["end"] = end_date
    q = (
        "select distinct on (date) date, adj_close, volume from stock_prices "
        "where ticker = :t {end} and adj_close is not null and adj_close > 0 "
        "order by date, id desc"
    ).format(end=end_clause)
    try:
        with engine.connect() as cx:
            df = pd.read_sql_query(text(q), cx, params=params)
    except Exception:
        # Column likely absent — degrade to adj-close-only, volume unavailable.
        return load_series(engine, ticker, end_date=end_date), None
    if df.empty:
        return TickerSeries(ticker=ticker), None
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date")
    series = TickerSeries(ticker=ticker,
                          dates=list(df["date"]),
                          adj=[float(x) for x in df["adj_close"]])
    if "volume" not in df.columns or df["volume"].isna().all():
        return series, None
    volume = [float(x) if pd.notna(x) else float("nan") for x in df["volume"]]
    return series, volume


def build_feature_dataset(engine=None, db_url: Optional[str] = None,
                          tickers: Optional[Sequence[str]] = None,
                          start_date: Optional[dt.date] = None,
                          end_date: Optional[dt.date] = None,
                          horizon: int = DEFAULT_HORIZON,
                          min_history: int = DEFAULT_MIN_HISTORY,
                          benchmark: str = DEFAULT_BENCHMARK,
                          max_tickers: Optional[int] = None,
                          with_labels: bool = True,
                          ) -> Tuple[pd.DataFrame, Dict[str, str]]:
    """Build the universe feature dataset from the database (read-only).

    Returns ``(DataFrame, unavailable_families)``. When ``with_labels`` the frame
    includes the joined 5-day forward labels (drops the unlabeled tail). The DB
    is only read; nothing is written and no schema is altered.
    """
    own_engine = False
    if engine is None:
        engine = build_engine(db_url)
        own_engine = True
    try:
        if tickers is None:
            tickers = list_universe(engine, max_tickers=max_tickers,
                                    exclude_benchmark=benchmark)
        else:
            tickers = [t for t in tickers if t != benchmark]
            if max_tickers:
                tickers = tickers[:max_tickers]

        spy, _ = load_series_with_volume(engine, benchmark, end_date=end_date)
        spy = spy if spy.dates else None

        frames: List[pd.DataFrame] = []
        any_volume = False
        for t in tickers:
            series, volume = load_series_with_volume(engine, t, end_date=end_date)
            if not series.dates:
                continue
            if volume is not None and np.isfinite(np.asarray(volume, dtype=float)).any():
                any_volume = True
            if with_labels:
                frame = build_labeled_dataset_for_ticker(
                    series, spy, volume=volume, horizon=horizon,
                    min_history=min_history, start_date=start_date, end_date=end_date)
            else:
                frame = pd.DataFrame(build_feature_rows_for_ticker(
                    series, spy, volume=volume, min_history=min_history,
                    start_date=start_date, end_date=end_date))
            if not frame.empty:
                frames.append(frame)

        df = (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame())
        return df, unavailable_feature_families(volume_present=any_volume)
    finally:
        if own_engine:
            engine.dispose()


# --------------------------------------------------------------------------- #
# Synthetic sample (no DB) — honest computation on synthetic prices, used to
# materialize a schema-demonstration sample and to keep the CLI runnable without
# a database. Clearly labeled as synthetic so it is never mistaken for real data.
# --------------------------------------------------------------------------- #
def _synthetic_series(ticker: str, n: int, seed: int,
                      start: dt.date = dt.date(2023, 1, 2),
                      with_volume: bool = True
                      ) -> Tuple[TickerSeries, Optional[List[float]]]:
    rng = np.random.default_rng(seed)
    dates: List[dt.date] = []
    d = start
    for _ in range(n):
        while d.weekday() > 4:
            d += dt.timedelta(days=1)
        dates.append(d)
        d += dt.timedelta(days=1)
    rets = rng.normal(0.0004, 0.012, n)
    adj = 100.0 * np.cumprod(1.0 + rets)
    series = TickerSeries(ticker=ticker, dates=dates, adj=[float(x) for x in adj])
    volume = ([float(x) for x in rng.integers(1_000_000, 5_000_000, n)]
              if with_volume else None)
    return series, volume


def build_synthetic_sample(n_tickers: int = 4, n_days: int = 320,
                           max_rows: int = 200) -> Tuple[pd.DataFrame, Dict[str, str]]:
    """Build a small labeled dataset from SYNTHETIC prices (no DB, no real data).

    Demonstrates the exact output schema and exercises the full feature+label
    path. Tickers are prefixed ``SYN_`` so the sample can never be confused with
    real market data.
    """
    spy, _ = _synthetic_series("SYN_SPY", n_days, seed=1000, with_volume=False)
    frames: List[pd.DataFrame] = []
    for k in range(n_tickers):
        # First ticker has no volume to exercise the disabled-volume path.
        with_volume = k != 0
        series, volume = _synthetic_series(
            f"SYN_{chr(ord('A') + k)}", n_days, seed=1 + k, with_volume=with_volume)
        frames.append(build_labeled_dataset_for_ticker(series, spy, volume=volume))
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    df = cross_sectional_zscore(df)
    if max_rows and len(df) > max_rows:
        df = df.sort_values(["as_of_date", "ticker"]).head(max_rows).reset_index(drop=True)
    return df, unavailable_feature_families(volume_present=True)


DEFAULT_SAMPLE_PATH = os.path.join("research", "output",
                                   "phase2a_feature_dataset_sample.csv")


def write_sample_csv(df: pd.DataFrame, path: str = DEFAULT_SAMPLE_PATH) -> str:
    """Write a sample dataset to CSV, creating the output directory if needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)
    return path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_date(s: Optional[str]) -> Optional[dt.date]:
    return dt.datetime.strptime(s, "%Y-%m-%d").date() if s else None


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Phase 2A feature builder + labeled dataset (offline/research). "
                    "Writes a small sample CSV. Reads the DB only when --db-url is "
                    "given; otherwise emits a clearly-labeled synthetic sample.")
    p.add_argument("--db-url", type=str, default=None,
                   help="Postgres URL. If omitted, a SYNTHETIC sample is written.")
    p.add_argument("--tickers", type=str, default=None,
                   help="Comma-separated tickers (DB mode). Default: whole universe.")
    p.add_argument("--max-tickers", type=int, default=10,
                   help="Cap tickers (DB mode) to keep the sample small.")
    p.add_argument("--start-date", type=str, default=None, help="YYYY-MM-DD.")
    p.add_argument("--end-date", type=str, default=None, help="YYYY-MM-DD.")
    p.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON)
    p.add_argument("--min-history", type=int, default=DEFAULT_MIN_HISTORY)
    p.add_argument("--max-rows", type=int, default=200,
                   help="Cap rows written to the sample CSV.")
    p.add_argument("--output", type=str, default=DEFAULT_SAMPLE_PATH)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.db_url:
        tickers = ([t.strip() for t in args.tickers.split(",") if t.strip()]
                   if args.tickers else None)
        df, unavailable = build_feature_dataset(
            db_url=args.db_url, tickers=tickers, max_tickers=args.max_tickers,
            start_date=_parse_date(args.start_date), end_date=_parse_date(args.end_date),
            horizon=args.horizon_days, min_history=args.min_history, with_labels=True)
        df = cross_sectional_zscore(df)
        if args.max_rows and len(df) > args.max_rows:
            df = df.sort_values(["as_of_date", "ticker"]).head(args.max_rows).reset_index(drop=True)
        source = "DATABASE"
    else:
        df, unavailable = build_synthetic_sample(max_rows=args.max_rows)
        source = "SYNTHETIC (no --db-url given)"

    path = write_sample_csv(df, args.output)
    print(f"[phase2a] source        : {source}")
    print(f"[phase2a] rows          : {len(df)}")
    print(f"[phase2a] columns       : {len(df.columns)}")
    print(f"[phase2a] feature_set   : {FEATURE_SET_VERSION}")
    print(f"[phase2a] sample written: {path}")
    print(f"[phase2a] unavailable feature families:")
    for fam, why in unavailable.items():
        print(f"            - {fam}: {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
