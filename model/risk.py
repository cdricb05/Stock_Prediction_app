"""Phase 2C — risk band + return interval layer.

Adds an honest **return interval** (`lo_return_5d`, `hi_return_5d`,
`interval_width`) and a **risk band** (`low` / `medium` / `high`) to the Phase 2B
out-of-sample predictions, using split-conformal residual quantiles. This is
**offline / research only**: it imports neither ``api_server`` nor Paper Trader,
never writes to the production database, and does not touch the live service.
numpy/pandas only — no sklearn, no xgboost.

Method
------
The interval is **split-conformal**: for fold ``k`` we take the signed residuals
``realized_excess - predicted_excess`` from folds ``0..k-1`` **only** and use
their empirical quantiles as the band added to each point prediction. Because
only prior-fold residuals are used there is **no future-residual leakage**; the
first fold has no prior residuals so its interval is left empty (never
fabricated).

``risk_band`` is derived from the interval width relative to the predicted-return
magnitude (``width / (|predicted| + eps)``), split at terciles: a wider band per
unit of signal means a noisier, higher-risk call. The assignment is deterministic
given the data and uses no realized outcomes.
"""
from __future__ import annotations

import math
from typing import Dict, List, Sequence

import numpy as np
import pandas as pd

from research import metrics as M

# Default target coverage of the conformal interval.
DEFAULT_COVERAGE = 0.80
# Gate 5 acceptance tolerance around the nominal coverage (in proportion).
COVERAGE_TOLERANCE = 0.10
# Minimum prior residuals before we trust a conformal interval for a fold.
MIN_RESIDUALS = 20

RISK_BANDS = ("low", "medium", "high")
_EPS = 1e-6

_PRED = "predicted_excess_return_5d"
_REALIZED = "realized_excess_return_5d_vs_spy"


# --------------------------------------------------------------------------- #
# Core interval / band primitives (pure, testable)
# --------------------------------------------------------------------------- #
def conformal_quantiles(residuals: Sequence[float],
                        coverage: float = DEFAULT_COVERAGE):
    """Signed-residual quantiles for a two-sided ``coverage`` interval.

    Returns ``(lo_q, hi_q)`` such that ``[pred+lo_q, pred+hi_q]`` is the
    conformal band. NaN residuals are dropped; returns ``(nan, nan)`` if empty.
    """
    r = np.asarray(residuals, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) == 0:
        return float("nan"), float("nan")
    tail = (1.0 - coverage) / 2.0
    lo = float(np.quantile(r, tail))
    hi = float(np.quantile(r, 1.0 - tail))
    return lo, hi


def assign_risk_band(values: Sequence[float]) -> List[str]:
    """Map a risk score (higher = riskier) to low/medium/high by terciles.

    Deterministic: split points are the 1/3 and 2/3 empirical quantiles of the
    finite values. Non-finite entries map to ``""`` (no band). Heavy ties widen a
    band rather than erroring.
    """
    v = np.asarray(values, dtype=float)
    finite = v[np.isfinite(v)]
    out = [""] * len(v)
    if len(finite) == 0:
        return out
    q1 = float(np.quantile(finite, 1.0 / 3.0))
    q2 = float(np.quantile(finite, 2.0 / 3.0))
    for i, x in enumerate(v):
        if not math.isfinite(x):
            continue
        if x <= q1:
            out[i] = "low"
        elif x <= q2:
            out[i] = "medium"
        else:
            out[i] = "high"
    return out


# --------------------------------------------------------------------------- #
# Walk-forward, leakage-safe intervals (prior-fold residuals only)
# --------------------------------------------------------------------------- #
def walk_forward_intervals(preds: pd.DataFrame, coverage: float = DEFAULT_COVERAGE,
                           min_residuals: int = MIN_RESIDUALS) -> pd.DataFrame:
    """Add ``lo_return_5d``/``hi_return_5d``/``interval_width``/``risk_band``.

    Fold ``k``'s interval uses residuals from folds ``0..k-1`` only. Returns a
    copy of ``preds`` with the four columns added; the first fold (no prior
    residuals) gets NaN interval and ``""`` band.
    """
    out = preds.copy()
    if out.empty:
        for c in ("lo_return_5d", "hi_return_5d", "interval_width", "risk_band"):
            out[c] = pd.Series(dtype=float if c != "risk_band" else object)
        return out

    out["lo_return_5d"] = np.nan
    out["hi_return_5d"] = np.nan
    out["interval_width"] = np.nan
    out["risk_band"] = ""

    resid_all = out[_REALIZED].to_numpy(dtype=float) - out[_PRED].to_numpy(dtype=float)
    out["_resid"] = resid_all

    for k in sorted(out["fold_id"].unique()):
        cur = out["fold_id"] == k
        prior = out[out["fold_id"] < k]
        res = prior["_resid"].to_numpy(dtype=float)
        res = res[np.isfinite(res)]
        if len(res) < min_residuals:
            continue
        lo_q, hi_q = conformal_quantiles(res, coverage)
        pred = out.loc[cur, _PRED].to_numpy(dtype=float)
        out.loc[cur, "lo_return_5d"] = pred + lo_q
        out.loc[cur, "hi_return_5d"] = pred + hi_q
        out.loc[cur, "interval_width"] = (hi_q - lo_q)

    # Risk score = interval width per unit of predicted signal magnitude.
    width = out["interval_width"].to_numpy(dtype=float)
    pred_mag = np.abs(out[_PRED].to_numpy(dtype=float)) + _EPS
    risk_score = np.where(np.isfinite(width), width / pred_mag, np.nan)
    out["risk_band"] = assign_risk_band(risk_score)
    out = out.drop(columns=["_resid"])
    return out


# --------------------------------------------------------------------------- #
# Interval / risk metrics
# --------------------------------------------------------------------------- #
def _finite_interval_rows(preds: pd.DataFrame) -> pd.DataFrame:
    if preds.empty:
        return preds
    m = (np.isfinite(preds["lo_return_5d"].to_numpy(dtype=float))
         & np.isfinite(preds["hi_return_5d"].to_numpy(dtype=float)))
    return preds[m]


def interval_coverage(preds: pd.DataFrame) -> float:
    """Fraction of rows whose realized excess return falls inside [lo, hi]."""
    rows = _finite_interval_rows(preds)
    if rows.empty:
        return float("nan")
    realized = rows[_REALIZED].to_numpy(dtype=float)
    lo = rows["lo_return_5d"].to_numpy(dtype=float)
    hi = rows["hi_return_5d"].to_numpy(dtype=float)
    mask = np.isfinite(realized)
    if not np.any(mask):
        return float("nan")
    inside = (realized[mask] >= lo[mask]) & (realized[mask] <= hi[mask])
    return float(np.mean(inside))


def coverage_by_score_bucket(preds: pd.DataFrame, n_bins: int = 5) -> List[Dict]:
    """Coverage within score quantile buckets (is the band honest everywhere?)."""
    rows = _finite_interval_rows(preds)
    if rows.empty:
        return []
    scores = rows["score"].to_numpy(dtype=float)
    realized = rows[_REALIZED].to_numpy(dtype=float)
    lo = rows["lo_return_5d"].to_numpy(dtype=float)
    hi = rows["hi_return_5d"].to_numpy(dtype=float)
    inside = ((realized >= lo) & (realized <= hi)).astype(float)
    # Reuse the metrics bucket engine: value=score, "realized"=inside flag, then
    # read hit_rate as coverage.
    table = M.bucket_stats(scores, inside, n_bins=n_bins)
    for r in table:
        r["coverage"] = r.pop("hit_rate")
    return table


def band_stats(preds: pd.DataFrame) -> Dict[str, Dict]:
    """Per risk_band: realized 5d-return downside summary."""
    out: Dict[str, Dict] = {}
    if preds.empty:
        return out
    for band in RISK_BANDS:
        sub = preds[preds["risk_band"] == band]
        if sub.empty:
            continue
        dd = M.drawdown_summary(sub["realized_return_5d"].to_numpy(dtype=float))
        out[band] = dd
    return out


def evaluate_intervals(preds: pd.DataFrame, coverage: float = DEFAULT_COVERAGE
                       ) -> Dict:
    """Coverage, width, per-bucket coverage, band counts, downside (OOS)."""
    rows = _finite_interval_rows(preds)
    if rows.empty:
        return {"n_finite": 0, "coverage": float("nan"), "avg_width": float("nan"),
                "target_coverage": coverage, "coverage_by_bucket": [],
                "band_counts": {}, "band_stats": {}, "drawdown": {}}
    return {
        "n_finite": int(len(rows)),
        "target_coverage": coverage,
        "coverage": interval_coverage(preds),
        "avg_width": float(np.nanmean(rows["interval_width"].to_numpy(dtype=float))),
        "coverage_by_bucket": coverage_by_score_bucket(preds),
        "band_counts": rows["risk_band"].value_counts().to_dict(),
        "band_stats": band_stats(rows),
        "drawdown": M.drawdown_summary(rows["realized_return_5d"].to_numpy(dtype=float)),
    }


def gate5(preds: pd.DataFrame, coverage: float = DEFAULT_COVERAGE,
          tolerance: float = COVERAGE_TOLERANCE) -> Dict:
    """Plan §8 gate 5: honest interval coverage near nominal + measured drawdown.

    Evaluable only once at least one fold carries a conformal interval (needs
    >= 2 folds). Passes when empirical coverage is within ``tolerance`` of the
    nominal target and a drawdown was actually measured.
    """
    iv = evaluate_intervals(preds, coverage=coverage)
    if iv["n_finite"] == 0:
        return {"name": "Honest interval coverage + measured drawdown",
                "evaluable": False, "passed": None, **iv, "n_drawdown": 0}
    cov = iv["coverage"]
    dd_n = int(iv["drawdown"].get("n", 0))
    near = math.isfinite(cov) and abs(cov - coverage) <= tolerance
    passed = bool(near and dd_n > 0)
    return {"name": "Honest interval coverage + measured drawdown",
            "evaluable": True, "passed": passed, "n_drawdown": dd_n, **iv}
