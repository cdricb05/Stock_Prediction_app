"""Pure evaluation metrics for the walk-forward baseline harness.

Design constraints:
- numpy-only (no scipy/pandas) so these are portable and unit-testable anywhere,
  including machines without the full prediction stack.
- Every function is defensive: empty input / all-NaN input returns float('nan')
  or an empty structure rather than raising.
- No I/O, no global state, no imports from api_server or Paper Trader.

These functions back the metrics report in
``research/run_walk_forward_baseline.py``.
"""
from __future__ import annotations

import math
from typing import Dict, List, Sequence

import numpy as np

NAN = float("nan")

# Recommendation strings emitted by api_server.make_recommendation().
_BUY = {"Strong Buy", "Buy"}
_SELL = {"Strong Sell", "Sell"}


def bucket_recommendation(rec: str | None) -> str:
    """Collapse the 5-level recommendation into BUY / SELL / HOLD."""
    if rec in _BUY:
        return "BUY"
    if rec in _SELL:
        return "SELL"
    return "HOLD"


def _clean_pairs(a: Sequence[float], b: Sequence[float]):
    """Return aligned arrays with rows where either value is NaN removed."""
    x = np.asarray(a, dtype=float)
    y = np.asarray(b, dtype=float)
    n = min(len(x), len(y))
    x, y = x[:n], y[:n]
    mask = ~(np.isnan(x) | np.isnan(y))
    return x[mask], y[mask]


def _rankdata(a: np.ndarray) -> np.ndarray:
    """Average-tie ranks (1-based), equivalent to scipy.stats.rankdata('average')."""
    a = np.asarray(a, dtype=float)
    n = len(a)
    if n == 0:
        return a
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(n, dtype=float)
    ranks[order] = np.arange(1, n + 1, dtype=float)
    sorted_a = a[order]
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sorted_a[j + 1] == sorted_a[i]:
            j += 1
        if j > i:
            avg = ((i + 1) + (j + 1)) / 2.0
            ranks[order[i:j + 1]] = avg
        i = j + 1
    return ranks


def _pearson(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 2:
        return NAN
    xc = x - x.mean()
    yc = y - y.mean()
    denom = math.sqrt(float((xc * xc).sum()) * float((yc * yc).sum()))
    if denom == 0.0:
        return NAN
    return float((xc * yc).sum() / denom)


def spearman_rank_ic(predicted: Sequence[float], realized: Sequence[float]) -> float:
    """Rank IC = Spearman correlation between predicted and realized returns.

    NaN pairs are dropped. Returns NaN if fewer than 2 valid pairs remain or if
    either side has zero variance (e.g. all-equal predictions).
    """
    x, y = _clean_pairs(predicted, realized)
    if len(x) < 2:
        return NAN
    return _pearson(_rankdata(x), _rankdata(y))


def pearson_ic(predicted: Sequence[float], realized: Sequence[float]) -> float:
    """Plain (Pearson) information coefficient, NaN-safe."""
    x, y = _clean_pairs(predicted, realized)
    if len(x) < 2:
        return NAN
    return _pearson(x, y)


def hit_rate(flags: Sequence) -> float:
    """Fraction of True among non-NaN boolean/0-1 flags."""
    a = np.asarray([np.nan if f is None else float(f) for f in flags], dtype=float)
    a = a[~np.isnan(a)]
    if len(a) == 0:
        return NAN
    return float(np.mean(a > 0.5))


def proportion_stderr(p: float, n: int) -> float:
    """Standard error of a proportion (sqrt(p(1-p)/n)); context for hit rates."""
    if n <= 0 or p is None or math.isnan(p):
        return NAN
    return math.sqrt(max(0.0, p * (1.0 - p)) / n)


def precision_at_k(scores: Sequence[float], labels: Sequence, k: int) -> float:
    """Fraction of positive labels among the top-k items by descending score.

    NaN scores are dropped first. k is clamped to the number of valid rows.
    Stable (mergesort) ordering so ties are deterministic. Returns NaN for
    empty input or k <= 0.
    """
    s = np.asarray(scores, dtype=float)
    lab = np.asarray([np.nan if v is None else float(v) for v in labels], dtype=float)
    n0 = min(len(s), len(lab))
    s, lab = s[:n0], lab[:n0]
    mask = ~(np.isnan(s) | np.isnan(lab))
    s, lab = s[mask], lab[mask]
    n = len(s)
    if n == 0 or k <= 0:
        return NAN
    k = min(k, n)
    order = np.argsort(-s, kind="mergesort")
    top = order[:k]
    return float(np.mean(lab[top] > 0.5))


def avg_by_recommendation(recs: Sequence[str], values: Sequence[float]) -> Dict[str, Dict[str, float]]:
    """Mean/std/n of ``values`` grouped by BUY/HOLD/SELL bucket."""
    out: Dict[str, Dict[str, float]] = {}
    recs = list(recs)
    vals = np.asarray(values, dtype=float)
    for bucket in ("BUY", "HOLD", "SELL"):
        idx = [i for i, r in enumerate(recs) if bucket_recommendation(r) == bucket]
        v = vals[idx] if idx else np.asarray([], dtype=float)
        v = v[~np.isnan(v)]
        if len(v):
            out[bucket] = {"n": int(len(v)), "mean": float(np.mean(v)), "std": float(np.std(v, ddof=1)) if len(v) > 1 else NAN}
        else:
            out[bucket] = {"n": 0, "mean": NAN, "std": NAN}
    return out


def confusion_matrix_rec(recs: Sequence[str], positive_flags: Sequence) -> Dict[str, Dict[str, int]]:
    """BUY/HOLD/SELL x realized {positive, non_positive} counts (+ per-bucket hit rate)."""
    table = {b: {"positive": 0, "non_positive": 0} for b in ("BUY", "HOLD", "SELL")}
    for rec, flag in zip(recs, positive_flags):
        if flag is None or (isinstance(flag, float) and math.isnan(flag)):
            continue
        bucket = bucket_recommendation(rec)
        key = "positive" if float(flag) > 0.5 else "non_positive"
        table[bucket][key] += 1
    for b, row in table.items():
        tot = row["positive"] + row["non_positive"]
        row["n"] = tot
        row["positive_rate"] = (row["positive"] / tot) if tot else NAN
    return table


def quantile_bin_edges(values: np.ndarray, n_bins: int) -> np.ndarray:
    """Unique quantile edges for binning; collapses to fewer bins on heavy ties."""
    qs = np.linspace(0.0, 1.0, n_bins + 1)
    edges = np.quantile(values, qs)
    return np.unique(edges)


def calibration_table(confidence: Sequence[float], positive_flags: Sequence,
                      n_bins: int = 10) -> List[Dict[str, float]]:
    """Calibration by confidence decile.

    Buckets rows into ``n_bins`` quantile bins of confidence and reports, per bin,
    the count, the confidence range, the mean confidence, and the realized
    positive-return hit rate. NaN rows (either field) are dropped. Heavy ties may
    yield fewer than ``n_bins`` rows (quantile edges collapse) — this is expected.
    """
    c = np.asarray(confidence, dtype=float)
    f = np.asarray([np.nan if v is None else float(v) for v in positive_flags], dtype=float)
    n0 = min(len(c), len(f))
    c, f = c[:n0], f[:n0]
    mask = ~(np.isnan(c) | np.isnan(f))
    c, f = c[mask], f[mask]
    if len(c) == 0:
        return []
    edges = quantile_bin_edges(c, n_bins)
    if len(edges) < 2:
        # All confidences identical -> single bucket.
        return [{
            "bin": 1, "n": int(len(c)), "conf_min": float(c.min()),
            "conf_max": float(c.max()), "mean_confidence": float(c.mean()),
            "hit_rate": float(np.mean(f > 0.5)),
        }]
    # np.digitize with right=True; clamp into [1, len(edges)-1].
    idx = np.digitize(c, edges[1:-1], right=True)
    rows: List[Dict[str, float]] = []
    for b in range(len(edges) - 1):
        sel = idx == b
        if not np.any(sel):
            continue
        cb, fb = c[sel], f[sel]
        rows.append({
            "bin": b + 1,
            "n": int(len(cb)),
            "conf_min": float(cb.min()),
            "conf_max": float(cb.max()),
            "mean_confidence": float(cb.mean()),
            "hit_rate": float(np.mean(fb > 0.5)),
        })
    return rows


def is_calibrated(table: List[Dict[str, float]], min_bins: int = 3,
                  min_spread: float = 0.05) -> bool:
    """Heuristic: confidence is 'calibrated-ish' if hit rate trends up with
    confidence. Requires >= ``min_bins`` bins, a positive Spearman between mean
    confidence and hit rate, and a hit-rate spread of at least ``min_spread``.
    This is a weak screen, NOT a calibration proof.
    """
    if len(table) < min_bins:
        return False
    conf = [r["mean_confidence"] for r in table]
    hr = [r["hit_rate"] for r in table]
    rho = spearman_rank_ic(conf, hr)
    spread = max(hr) - min(hr)
    return (not math.isnan(rho)) and rho > 0.0 and spread >= min_spread


def universe_baseline(realized: Sequence[float]) -> Dict[str, float]:
    """Equal-weight universe baseline: mean/median/n of all realized returns.

    This is the 'buy everything eligible' reference. The model only adds value if
    its *selected* names (BUY bucket, or a top-K slice) beat this number.
    """
    v = np.asarray(realized, dtype=float)
    v = v[~np.isnan(v)]
    if len(v) == 0:
        return {"n": 0, "mean": NAN, "median": NAN, "hit_rate": NAN}
    return {
        "n": int(len(v)),
        "mean": float(np.mean(v)),
        "median": float(np.median(v)),
        "hit_rate": float(np.mean(v > 0)),
    }


def bucket_stats(values: Sequence[float], realized: Sequence[float],
                 n_bins: int = 5) -> List[Dict[str, float]]:
    """Per quantile-bucket of ``values``: n, value range, mean realized return,
    and positive-return hit rate.

    Generic engine behind both the score-bucket (values = predicted return) and
    confidence-bucket (values = confidence) tables. NaN rows (either field) are
    dropped. Heavy ties collapse to fewer buckets — expected, not an error.
    """
    x = np.asarray(values, dtype=float)
    y = np.asarray(realized, dtype=float)
    n0 = min(len(x), len(y))
    x, y = x[:n0], y[:n0]
    mask = ~(np.isnan(x) | np.isnan(y))
    x, y = x[mask], y[mask]
    if len(x) == 0:
        return []
    edges = quantile_bin_edges(x, n_bins)
    if len(edges) < 2:
        return [{
            "bin": 1, "n": int(len(x)), "val_min": float(x.min()),
            "val_max": float(x.max()), "mean_value": float(x.mean()),
            "mean_realized": float(y.mean()), "hit_rate": float(np.mean(y > 0)),
        }]
    idx = np.digitize(x, edges[1:-1], right=True)
    rows: List[Dict[str, float]] = []
    for b in range(len(edges) - 1):
        sel = idx == b
        if not np.any(sel):
            continue
        xb, yb = x[sel], y[sel]
        rows.append({
            "bin": b + 1,
            "n": int(len(xb)),
            "val_min": float(xb.min()),
            "val_max": float(xb.max()),
            "mean_value": float(xb.mean()),
            "mean_realized": float(yb.mean()),
            "hit_rate": float(np.mean(yb > 0)),
        })
    return rows


def topn_simulation(dates: Sequence, scores: Sequence[float], realized: Sequence[float],
                    ns: Sequence) -> Dict:
    """Simulate a top-N selection policy, rebalanced per as-of date.

    For each unique as-of date, rank the candidates by ``scores`` (descending)
    and take the top ``n`` (``n=None`` means 'all eligible'). The per-date return
    is the equal-weight mean realized return of that slice; the reported figure is
    the mean across dates (each date weighted equally, so a few crowded dates do
    not dominate). NaN score/realized rows are dropped before ranking.

    Returns ``{n: {"n_dates", "mean_return", "avg_picks", "hit_rate"}}`` plus the
    key ``"_universe"`` (the n=None 'all eligible' row) for excess-return context.
    """
    from collections import defaultdict
    s = np.asarray(scores, dtype=float)
    r = np.asarray(realized, dtype=float)
    idx_by_date = defaultdict(list)
    n0 = min(len(dates), len(s), len(r))
    for i in range(n0):
        if np.isnan(s[i]) or np.isnan(r[i]):
            continue
        idx_by_date[dates[i]].append(i)

    out: Dict = {}
    for n in ns:
        per_date_ret: List[float] = []
        per_date_hit: List[float] = []
        pick_counts: List[int] = []
        for _d, idxs in idx_by_date.items():
            order = sorted(idxs, key=lambda i: -s[i])
            top = order if n is None else order[:n]
            if not top:
                continue
            rr = np.asarray([r[i] for i in top], dtype=float)
            per_date_ret.append(float(rr.mean()))
            per_date_hit.append(float(np.mean(rr > 0)))
            pick_counts.append(len(top))
        key = "all" if n is None else n
        out[key] = {
            "n_dates": len(per_date_ret),
            "mean_return": float(np.mean(per_date_ret)) if per_date_ret else NAN,
            "avg_picks": float(np.mean(pick_counts)) if pick_counts else NAN,
            "hit_rate": float(np.mean(per_date_hit)) if per_date_hit else NAN,
        }
    out["_universe"] = out.get("all", {"mean_return": NAN})
    return out


def tally(reasons: Sequence[str]) -> List[Dict[str, float]]:
    """Count occurrences of each reason string, sorted by descending count, with
    shares. Used for the actionability-gate failure breakdown.
    """
    from collections import Counter
    items = [r if r else "unknown" for r in reasons]
    total = len(items)
    c = Counter(items)
    rows = [{"reason": k, "count": v, "share": (v / total if total else NAN)}
            for k, v in c.most_common()]
    return rows


def drawdown_summary(returns: Sequence[float], loss_threshold: float = -0.02) -> Dict[str, float]:
    """Downside summary for a set of realized returns (e.g. BUY-rec rows).

    Reports n, worst (min), mean, fraction below 0, fraction below
    ``loss_threshold``, and the mean of the negative returns (average loss).
    """
    v = np.asarray(returns, dtype=float)
    v = v[~np.isnan(v)]
    if len(v) == 0:
        return {"n": 0, "worst": NAN, "mean": NAN, "pct_negative": NAN,
                "pct_below_threshold": NAN, "mean_loss": NAN, "loss_threshold": loss_threshold}
    neg = v[v < 0]
    return {
        "n": int(len(v)),
        "worst": float(v.min()),
        "mean": float(v.mean()),
        "pct_negative": float(np.mean(v < 0)),
        "pct_below_threshold": float(np.mean(v < loss_threshold)),
        "mean_loss": float(neg.mean()) if len(neg) else 0.0,
        "loss_threshold": loss_threshold,
    }
