"""Phase 5-A — self-contained feature math for the model_v2 pricing engine.

Every feature is computed strictly from a trailing price window (rows with
``date <= as_of``) so there is no look-ahead. The math is intentionally pure
(numpy only, no pandas, no database, no network) so this module can be lifted
into the GCP prediction service unchanged in Phase 5-B.

Feature families (v1, price-only):
  momentum    return_21d, return_63d, return_126d
  trend       sma_50, sma_200, price_vs_sma_50, price_vs_sma_200,
              sma_50_vs_200  (moving-average trend / golden-cross proxy)
  volatility  realized_vol_21d, realized_vol_63d  (annualized)
  drawdown    drawdown_from_peak_252d
  meanrev     return_zscore_21d  (today's 21d return vs its trailing distribution)
  volume      volume_zscore_21d  (OPTIONAL — only when a real volume series is
              supplied; never fabricated)

Anything we cannot honestly compute from the supplied window degrades to
``None`` and is reported in ``missing_features``; it never blocks scoring.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

FEATURE_SET_VERSION = "model_v2_v1"

TRADING_DAYS_PER_YEAR = 252
_ANNUALIZE = math.sqrt(TRADING_DAYS_PER_YEAR)

# Ordered feature names (also the column order callers should expect).
MOMENTUM_FEATURES = ["return_21d", "return_63d", "return_126d"]
TREND_FEATURES = [
    "sma_50", "sma_200", "price_vs_sma_50", "price_vs_sma_200", "sma_50_vs_200",
]
VOLATILITY_FEATURES = ["realized_vol_21d", "realized_vol_63d"]
DRAWDOWN_FEATURES = ["drawdown_from_peak_252d"]
MEANREV_FEATURES = ["return_zscore_21d"]
VOLUME_FEATURES = ["volume_zscore_21d"]

ALL_FEATURES = (
    MOMENTUM_FEATURES + TREND_FEATURES + VOLATILITY_FEATURES
    + DRAWDOWN_FEATURES + MEANREV_FEATURES + VOLUME_FEATURES
)

# Minimum trailing sessions required before the longest lookback (200d SMA /
# 252d drawdown) resolves. Shorter-window features still degrade individually.
DEFAULT_MIN_HISTORY = 252


# --------------------------------------------------------------------------- #
# Pure helpers. Convention: ``close`` is an ascending-by-date array and every
# helper uses ONLY the trailing window ending at the last element.
# --------------------------------------------------------------------------- #
def trailing_return(close: Sequence[float], window: int) -> Optional[float]:
    """``close[-1] / close[-1-window] - 1`` (past-only). None if unavailable."""
    n = len(close)
    if window <= 0 or n <= window:
        return None
    base = float(close[n - 1 - window])
    if base <= 0:
        return None
    return float(close[-1]) / base - 1.0


def daily_returns(close: Sequence[float]) -> np.ndarray:
    """Simple daily returns aligned to ``close`` (index 0 = NaN, no prior close)."""
    a = np.asarray(close, dtype=float)
    out = np.full(a.shape, np.nan, dtype=float)
    if len(a) >= 2:
        prev, cur = a[:-1], a[1:]
        with np.errstate(divide="ignore", invalid="ignore"):
            out[1:] = np.where(prev > 0, cur / prev - 1.0, np.nan)
    return out


def sma(close: Sequence[float], window: int) -> Optional[float]:
    """Simple moving average of the last ``window`` closes. None if too short."""
    n = len(close)
    if window < 1 or n < window:
        return None
    w = np.asarray(close[n - window:], dtype=float)
    if np.any(~np.isfinite(w)):
        return None
    return float(np.mean(w))


def realized_vol(close: Sequence[float], window: int,
                 annualize: bool = True) -> Optional[float]:
    """Sample std (ddof=1) of the last ``window`` daily returns. None if short."""
    n = len(close)
    if window < 2 or n <= window:
        return None
    r = daily_returns(close)[n - window:]
    if len(r) < window or np.any(np.isnan(r)):
        return None
    vol = float(np.std(r, ddof=1))
    return vol * _ANNUALIZE if annualize else vol


def max_drawdown(close: Sequence[float], window: int = 252) -> Optional[float]:
    """Worst peak-to-trough decline over the trailing window (<= 0.0).

    Computed on the last ``window`` closes: ``min(close/running_peak - 1)``.
    None if the window is unavailable.
    """
    n = len(close)
    if window < 2 or n < window:
        return None
    w = np.asarray(close[n - window:], dtype=float)
    if np.any(~np.isfinite(w)) or np.any(w <= 0):
        return None
    peak = np.maximum.accumulate(w)
    dd = w / peak - 1.0
    return float(np.min(dd))


def return_zscore(close: Sequence[float], window: int = 21) -> Optional[float]:
    """Z-score of the most recent ``window``-day return vs its trailing history.

    Anchors on the latest ``window``-day return and standardizes it against the
    distribution of overlapping ``window``-day returns observed across the prior
    ``window`` sessions. A transparent mean-reversion / stretch gauge. None if
    insufficient history or zero dispersion.
    """
    n = len(close)
    if window < 2 or n < 2 * window + 1:
        return None
    a = np.asarray(close, dtype=float)
    # Overlapping window-day returns over the trailing 2*window span.
    rets = []
    for end in range(n - window, n):
        base = a[end - window]
        if base <= 0:
            return None
        rets.append(a[end] / base - 1.0)
    hist = np.asarray(rets, dtype=float)
    latest = float(a[-1] / a[n - 1 - window] - 1.0)
    mu, sd = float(np.mean(hist)), float(np.std(hist, ddof=1))
    if not np.isfinite(sd) or sd <= 0.0:
        return None
    return (latest - mu) / sd


def volume_zscore(volume: Optional[Sequence[float]], window: int = 21) -> Optional[float]:
    """Z-score of today's volume vs the trailing window. None if no real volume."""
    if volume is None:
        return None
    n = len(volume)
    if window < 2 or n < window:
        return None
    w = np.asarray(volume[n - window:], dtype=float)
    if np.any(~np.isfinite(w)) or np.any(w < 0):
        return None
    sd = float(np.std(w, ddof=1))
    if sd <= 0.0:
        return None
    return (float(volume[-1]) - float(np.mean(w))) / sd


# --------------------------------------------------------------------------- #
# Feature-vector assembly (pure given a loaded window).
# --------------------------------------------------------------------------- #
def compute_feature_vector(close: Sequence[float],
                           volume: Optional[Sequence[float]] = None
                           ) -> Tuple[Dict[str, Optional[float]], List[str], List[str]]:
    """Compute the v1 feature vector for the last session in ``close``.

    Returns ``(features, used, missing)`` where ``used`` lists the features that
    resolved to a finite value and ``missing`` lists those that degraded to
    ``None`` (unavailable window or no volume). Never raises on short history.
    """
    feats: Dict[str, Optional[float]] = {}

    last = float(close[-1]) if len(close) and np.isfinite(close[-1]) else None

    feats["return_21d"] = trailing_return(close, 21)
    feats["return_63d"] = trailing_return(close, 63)
    feats["return_126d"] = trailing_return(close, 126)

    sma50 = sma(close, 50)
    sma200 = sma(close, 200)
    feats["sma_50"] = sma50
    feats["sma_200"] = sma200
    feats["price_vs_sma_50"] = (last / sma50 - 1.0) if (last and sma50) else None
    feats["price_vs_sma_200"] = (last / sma200 - 1.0) if (last and sma200) else None
    feats["sma_50_vs_200"] = (sma50 / sma200 - 1.0) if (sma50 and sma200) else None

    feats["realized_vol_21d"] = realized_vol(close, 21)
    feats["realized_vol_63d"] = realized_vol(close, 63)

    feats["drawdown_from_peak_252d"] = max_drawdown(close, 252)

    feats["return_zscore_21d"] = return_zscore(close, 21)

    feats["volume_zscore_21d"] = volume_zscore(volume, 21)

    used = [k for k in ALL_FEATURES if feats.get(k) is not None]
    missing = [k for k in ALL_FEATURES if feats.get(k) is None]
    return feats, used, missing


def feature_schema() -> Dict[str, Dict[str, str]]:
    """Machine-readable description of each v1 feature (drives feature_schema.json)."""
    return {
        "return_21d": {"family": "momentum", "type": "float", "unit": "fraction",
                       "description": "Trailing 21-session price return."},
        "return_63d": {"family": "momentum", "type": "float", "unit": "fraction",
                       "description": "Trailing 63-session price return."},
        "return_126d": {"family": "momentum", "type": "float", "unit": "fraction",
                        "description": "Trailing 126-session price return."},
        "sma_50": {"family": "trend", "type": "float", "unit": "price",
                   "description": "50-session simple moving average of close."},
        "sma_200": {"family": "trend", "type": "float", "unit": "price",
                    "description": "200-session simple moving average of close."},
        "price_vs_sma_50": {"family": "trend", "type": "float", "unit": "fraction",
                            "description": "Last close relative to its 50d SMA."},
        "price_vs_sma_200": {"family": "trend", "type": "float", "unit": "fraction",
                             "description": "Last close relative to its 200d SMA."},
        "sma_50_vs_200": {"family": "trend", "type": "float", "unit": "fraction",
                          "description": "50d SMA relative to 200d SMA (golden/death-cross proxy)."},
        "realized_vol_21d": {"family": "volatility", "type": "float", "unit": "annualized",
                             "description": "Annualized std of 21d daily returns."},
        "realized_vol_63d": {"family": "volatility", "type": "float", "unit": "annualized",
                             "description": "Annualized std of 63d daily returns."},
        "drawdown_from_peak_252d": {"family": "drawdown", "type": "float", "unit": "fraction",
                                    "description": "Worst peak-to-trough decline over trailing 252 sessions (<= 0)."},
        "return_zscore_21d": {"family": "meanrev", "type": "float", "unit": "zscore",
                              "description": "Latest 21d return standardized vs its trailing distribution."},
        "volume_zscore_21d": {"family": "volume", "type": "float", "unit": "zscore",
                              "description": "Today's volume vs the trailing 21d window (None when no real volume)."},
    }
