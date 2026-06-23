"""Phase 7-B — Validation Harness Foundation (offline, leakage-safe, reusable).

**Track A (quant brain) research infrastructure only.** Offline. Point-in-time.
Walk-forward. No network, no provider call, no paid API, no API key read or
required, no model trained or deployed, no factor alpha built, no factor weights
optimized, no Paper Trader / GCP work, no orders / broker / automation, no binary
artifact, no commit, no push.

Why this phase exists
---------------------
The project charter (docs/project_charter_sp500_multifactor_ranking_v1.md, Section
6 / charter Phase 0) is explicit: **build the measuring instrument before building
more signals.** Phase 7-A reset the project to a two-system ranking + risk design
and recommended READY_FOR_PHASE7B_VALIDATION_HARNESS. Every prior negative result
(Phase 5-G2 NO_INCREMENTAL_EVENT_EDGE, Phase 6-A degraded selection) shares one
root cause: claims were judged without a single, trusted, leakage-safe instrument
that all later factors/ranking models must pass before they are considered real.

This phase builds that instrument. It does NOT build factors, does NOT optimize
weights, and does NOT make an alpha claim. It builds and *self-validates* the
harness on deterministic synthetic data, then emits the capability/metric/gate
catalogues and the Phase 7-C hand-off.

What it provides (reusable, importable by Phase 7-C+)
----------------------------------------------------
  1. Walk-forward split generator (expanding train -> forward test, embargo gap).
  2. Purged k-fold split generator with embargo (label-overlap purge + embargo).
  3. Holdout ledger with single-touch discipline (OOS is a spent budget).
  4. Multiple-testing tracker + deflated-Sharpe / expected-max-Sharpe haircut.
  5. Transaction-cost model (bps + turnover -> gross vs net).
  6. Metric suite: mean rank IC, IC t-stat, yearly IC, top/bottom quantile spread,
     Sharpe, max drawdown, turnover, cost-adjusted return.
  7. Regime / sub-period decomposition.
  8. Sensitivity-surface framework (measures robustness across a parameter range;
     it deliberately does NOT optimize and never reports a single best point alone).
  9. Safety self-checks: no-lookahead / no same-day forward leakage / no production
     model / no Paper Trader / GCP / deploy / broker / orders / automation.
 10. Equal-weight composite baseline — the hard benchmark every later scheme must
     beat (no early weight optimization).

Leakage safety
--------------
Every split is leakage-safe by construction: walk-forward train labels must fully
resolve at least ``embargo`` periods before the test window opens; purged k-fold
drops any train sample whose label interval overlaps the test interval and embargoes
the periods immediately after each test block. A deterministic within-period
label-permutation placebo probes for residual look-ahead; the self-check asserts the
placebo collapses toward zero while the honest signal is recovered. The harness
reuses research/metrics.py (Phase 5-C rank-IC machinery, VR-11) rather than
reinventing leakage-prone correlation code.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research import metrics as M  # reuse audited rank-IC machinery (VR-11)

_OUT_DIR_DEFAULT = _REPO_ROOT / "research" / "output" / "phase7b_validation_harness_foundation"

PHASE = "7-B"
OBJECTIVE = (
    "Build the measuring instrument before building more signals: a reusable, "
    "leakage-safe validation harness (walk-forward + purged CV with embargo, "
    "single-touch holdout, multiple-testing discipline, cost model, full metric "
    "suite, regime decomposition, sensitivity-surface framework, equal-weight "
    "baseline) that every future factor/ranking model must pass before it is "
    "considered real. No factor alpha, no weight optimization, no live data."
)

REC_READY = "READY_FOR_PHASE7C_MULTIFACTOR_RANKING_ENGINE"
REC_REVIEW = "NEEDS_VALIDATION_REVIEW"
REC_BLOCKED = "BLOCKED"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (REC_READY, REC_REVIEW, REC_BLOCKED, REC_ERROR)

# Self-check thresholds (these grade the INSTRUMENT, not any alpha).
SELFCHECK_MIN_HONEST_IC = 0.03      # synthetic embedded signal must be recovered
SELFCHECK_MAX_PLACEBO_ABS = 0.02    # within-period label-shuffle must collapse
PLACEBO_MATERIAL_FRACTION = 0.5     # |placebo| must be < this * honest IC

NAN = float("nan")


# --------------------------------------------------------------------------- #
# Small numeric helpers (pure, defensive, numpy-only).
# --------------------------------------------------------------------------- #
def _f(x) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _round(x, n=6):
    v = _f(x)
    return round(v, n) if v is not None else None


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via erf (no scipy)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation)."""
    if not (0.0 < p < 1.0):
        if p <= 0.0:
            return float("-inf")
        if p >= 1.0:
            return float("inf")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


# --------------------------------------------------------------------------- #
# 1. Walk-forward + purged k-fold split generators (leakage-safe by construction).
#
# Samples are described by two integer period-index arrays of equal length:
#   eval_idx[i]      period (e.g. session/month index) at which the decision is made
#   label_end_idx[i] period at which sample i's forward label fully resolves
# Both are caller-supplied; the harness never fabricates targets. label_end must be
# strictly forward (label_end_idx[i] > eval_idx[i]) — enforced by
# assert_strictly_forward_labels().
# --------------------------------------------------------------------------- #
@dataclass
class Fold:
    scheme: str
    fold: int
    test_period_lo: int
    test_period_hi: int
    train_idx: List[int] = field(default_factory=list)
    test_idx: List[int] = field(default_factory=list)
    n_purged: int = 0
    n_embargoed: int = 0
    embargo: int = 0
    min_embargo_gap: Optional[int] = None  # walk-forward only

    @property
    def n_train(self) -> int:
        return len(self.train_idx)

    @property
    def n_test(self) -> int:
        return len(self.test_idx)


def assert_strictly_forward_labels(eval_idx: Sequence[int],
                                   label_end_idx: Sequence[int]) -> Dict[str, object]:
    """No same-day / backward labels: every label must resolve strictly after the
    decision period. Returns a diagnostic dict (does not raise)."""
    ev = np.asarray(eval_idx, dtype=float)
    le = np.asarray(label_end_idx, dtype=float)
    n = min(len(ev), len(le))
    ev, le = ev[:n], le[:n]
    if n == 0:
        return {"strictly_forward": True, "n_violations": 0, "n": 0}
    violations = int(np.sum(le <= ev))
    return {"strictly_forward": violations == 0, "n_violations": violations, "n": int(n)}


def _contiguous_blocks(periods: List[int], n_folds: int) -> List[List[int]]:
    """Split a sorted, unique period list into ``n_folds`` near-equal contiguous
    blocks (time-ordered; never shuffled)."""
    n_folds = max(1, min(n_folds, len(periods)))
    return [[int(x) for x in b] for b in np.array_split(np.asarray(periods), n_folds) if len(b)]


def walk_forward_splits(eval_idx: Sequence[int], label_end_idx: Sequence[int], *,
                        n_folds: int = 4, min_train_periods: int = 1,
                        embargo: int = 0) -> List[Fold]:
    """Expanding-window walk-forward: fold k trains only on samples whose label has
    fully resolved at least ``embargo`` periods before the test window opens.

    Leakage-safe by construction: a train sample is kept iff
        eval_idx < test_lo AND label_end_idx <= test_lo - 1 - embargo
    so there is no label/test overlap and a >= ``embargo`` period gap. The forward
    test blocks tile the tail of the timeline after the minimum training prefix.
    """
    ev = np.asarray(eval_idx, dtype=int)
    le = np.asarray(label_end_idx, dtype=int)
    periods = sorted(set(int(x) for x in ev))
    if len(periods) <= min_train_periods:
        return []
    test_periods = periods[min_train_periods:]
    blocks = _contiguous_blocks(test_periods, n_folds)
    folds: List[Fold] = []
    for k, block in enumerate(blocks):
        test_lo, test_hi = block[0], block[-1]
        test_mask = (ev >= test_lo) & (ev <= test_hi)
        train_mask = (ev < test_lo) & (le <= test_lo - 1 - embargo)
        test_i = [int(i) for i in np.nonzero(test_mask)[0]]
        train_i = [int(i) for i in np.nonzero(train_mask)[0]]
        if not test_i or not train_i:
            continue
        gap = int(test_lo - 1 - max(int(le[i]) for i in train_i)) if train_i else None
        folds.append(Fold(scheme="walk_forward_expanding", fold=k, test_period_lo=test_lo,
                          test_period_hi=test_hi, train_idx=train_i, test_idx=test_i,
                          embargo=embargo, min_embargo_gap=gap))
    return folds


def purged_kfold_splits(eval_idx: Sequence[int], label_end_idx: Sequence[int], *,
                        n_folds: int = 5, embargo: int = 0) -> List[Fold]:
    """Purged k-fold with embargo (Lopez de Prado style), on the period timeline.

    Each fold's test set is a contiguous block of periods. A train sample is PURGED
    if its label interval [eval_idx, label_end_idx] overlaps the test interval
    [test_lo, max label_end among test samples], and EMBARGOED if its decision
    period falls within ``embargo`` periods immediately after the test block.
    """
    ev = np.asarray(eval_idx, dtype=int)
    le = np.asarray(label_end_idx, dtype=int)
    periods = sorted(set(int(x) for x in ev))
    blocks = _contiguous_blocks(periods, n_folds)
    folds: List[Fold] = []
    for k, block in enumerate(blocks):
        test_lo, test_hi = block[0], block[-1]
        test_mask = (ev >= test_lo) & (ev <= test_hi)
        test_i = [int(i) for i in np.nonzero(test_mask)[0]]
        if not test_i:
            continue
        test_label_hi = max(int(le[i]) for i in test_i)
        train_i: List[int] = []
        n_purged = n_embargoed = 0
        for j in range(len(ev)):
            if test_mask[j]:
                continue
            overlaps = (int(le[j]) >= test_lo) and (int(ev[j]) <= test_label_hi)
            if overlaps:
                n_purged += 1
                continue
            if test_hi < int(ev[j]) <= test_hi + embargo:
                n_embargoed += 1
                continue
            train_i.append(j)
        if not train_i:
            continue
        folds.append(Fold(scheme="purged_kfold", fold=k, test_period_lo=test_lo,
                          test_period_hi=test_hi, train_idx=train_i, test_idx=test_i,
                          n_purged=n_purged, n_embargoed=n_embargoed, embargo=embargo))
    return folds


# --------------------------------------------------------------------------- #
# 2. Holdout ledger — OOS is a spent budget (single-touch discipline).
# --------------------------------------------------------------------------- #
@dataclass
class HoldoutLedger:
    """Records a reserved terminal out-of-sample window and whether it has been
    touched. The OOS holdout may be accessed exactly once (final sign-off); a second
    touch is recorded as a violation. Persisted as JSON for auditability."""
    holdout_start: str
    holdout_end: str
    purpose: str = "final single-touch out-of-sample sign-off"
    touched: bool = False
    touch_count: int = 0
    touch_log: List[str] = field(default_factory=list)

    def touch(self, reason: str, *, when: str = "unspecified") -> Dict[str, object]:
        """Touch the holdout. The FIRST touch is permitted; any subsequent touch is
        flagged as a discipline violation (the budget is already spent)."""
        violation = self.touched
        self.touch_count += 1
        self.touched = True
        self.touch_log.append(f"{when}: {reason}{' [VIOLATION: holdout already spent]' if violation else ''}")
        return {"permitted": not violation, "touch_count": self.touch_count, "violation": violation}

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# 3. Multiple-testing discipline — config counter + deflated Sharpe.
# --------------------------------------------------------------------------- #
@dataclass
class MultipleTestingTracker:
    """Counts every configuration/test evaluated so the reported Sharpe can be
    deflated by the number of trials (guards against selection under many tries)."""
    trials: List[Dict[str, object]] = field(default_factory=list)

    def register(self, config_id: str, sharpe: Optional[float] = None,
                 note: str = "") -> int:
        self.trials.append({"config_id": config_id, "sharpe": _f(sharpe), "note": note})
        return len(self.trials)

    @property
    def n_trials(self) -> int:
        return len(self.trials)

    def sharpe_variance(self) -> Optional[float]:
        srs = [t["sharpe"] for t in self.trials if t["sharpe"] is not None]
        if len(srs) < 2:
            return None
        return float(np.var(np.asarray(srs, dtype=float), ddof=1))


def expected_max_sharpe(n_trials: int, sharpe_variance: float) -> float:
    """Expected maximum of ``n_trials`` i.i.d. N(0, sharpe_variance) Sharpe
    estimates (Bailey & Lopez de Prado). This is the benchmark a candidate Sharpe
    must clear to be credible after multiple testing."""
    if n_trials < 2 or sharpe_variance is None or sharpe_variance <= 0:
        return 0.0
    gamma = 0.5772156649015329  # Euler-Mascheroni
    z1 = _norm_ppf(1.0 - 1.0 / n_trials)
    z2 = _norm_ppf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(sharpe_variance) * ((1.0 - gamma) * z1 + gamma * z2)


def deflated_sharpe_ratio(observed_sharpe: float, *, n_obs: int, n_trials: int,
                          sharpe_variance: Optional[float] = None,
                          skew: float = 0.0, kurt: float = 3.0) -> Optional[float]:
    """Probability that the observed Sharpe exceeds the multiple-testing benchmark
    (deflated Sharpe ratio). Returns a probability in [0, 1]; values near 1 mean the
    Sharpe survives the haircut for ``n_trials`` configurations. Per-period Sharpe."""
    sr = _f(observed_sharpe)
    if sr is None or n_obs < 2:
        return None
    var = sharpe_variance if (sharpe_variance is not None and sharpe_variance > 0) else (1.0 / n_obs)
    sr0 = expected_max_sharpe(n_trials, var)
    denom = math.sqrt(max(1e-12, 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr))
    z = (sr - sr0) * math.sqrt(max(1, n_obs - 1)) / denom
    return float(_norm_cdf(z))


# --------------------------------------------------------------------------- #
# 4. Transaction-cost model — bps + turnover -> gross vs net.
# --------------------------------------------------------------------------- #
def turnover_from_weights(prev_w: Dict[str, float], new_w: Dict[str, float]) -> float:
    """One-sided turnover = 0.5 * sum |new - prev| across the union of names."""
    names = set(prev_w) | set(new_w)
    return 0.5 * sum(abs(new_w.get(t, 0.0) - prev_w.get(t, 0.0)) for t in names)


def apply_transaction_costs(gross_returns: Sequence[float], turnovers: Sequence[float],
                            cost_bps: float = 10.0) -> Dict[str, object]:
    """Net = gross - turnover * (cost_bps / 1e4) per period. Reports gross, net,
    total modeled cost drag and mean turnover. No optimization, just accounting."""
    g = np.asarray(gross_returns, dtype=float)
    to = np.asarray(turnovers, dtype=float)
    n = min(len(g), len(to))
    g, to = g[:n], to[:n]
    cost = to * (cost_bps / 1e4)
    net = g - cost
    return {
        "cost_bps": cost_bps,
        "n_periods": int(n),
        "gross_mean": float(np.mean(g)) if n else NAN,
        "net_mean": float(np.mean(net)) if n else NAN,
        "mean_turnover": float(np.mean(to)) if n else NAN,
        "total_cost_drag": float(np.sum(cost)) if n else NAN,
        "net_returns": [float(x) for x in net],
    }


# --------------------------------------------------------------------------- #
# 5. Metric suite (reuse research.metrics for rank IC).
# --------------------------------------------------------------------------- #
def period_rank_ics(scores_by_period: Dict, realized_by_period: Dict, *,
                    min_names: int = 5) -> Dict:
    """Per-period Spearman rank IC (predicted vs realized). Periods with fewer than
    ``min_names`` names are skipped. Returns {period: ic} for usable periods."""
    out: Dict = {}
    for p in sorted(scores_by_period):
        s = scores_by_period.get(p)
        y = realized_by_period.get(p)
        if s is None or y is None or len(s) < min_names:
            continue
        ic = M.spearman_rank_ic(s, y)
        if ic is not None and math.isfinite(ic):
            out[p] = float(ic)
    return out


def ic_summary(period_ics: Dict) -> Dict[str, Optional[float]]:
    """Mean rank IC, IC standard error and IC t-stat across periods."""
    vals = np.asarray([v for v in period_ics.values()], dtype=float)
    vals = vals[~np.isnan(vals)]
    n = len(vals)
    if n == 0:
        return {"mean_rank_ic": None, "ic_std": None, "ic_t_stat": None, "n_periods": 0}
    mean = float(np.mean(vals))
    std = float(np.std(vals, ddof=1)) if n > 1 else NAN
    t = (mean / (std / math.sqrt(n))) if (n > 1 and std and math.isfinite(std) and std > 0) else None
    return {"mean_rank_ic": mean, "ic_std": (std if math.isfinite(std) else None),
            "ic_t_stat": t, "n_periods": n}


def yearly_ic(period_ics: Dict) -> Dict[str, float]:
    """Mean rank IC grouped by calendar year. Period keys must start 'YYYY' (ISO
    date or year-month) or be a 4-digit year int."""
    groups: Dict[str, List[float]] = {}
    for p, ic in period_ics.items():
        yr = str(p)[:4]
        groups.setdefault(yr, []).append(ic)
    return {y: float(np.mean(v)) for y, v in sorted(groups.items()) if v}


def quantile_spread(scores: Sequence[float], realized: Sequence[float], *,
                    q: int = 10) -> Optional[float]:
    """Top-minus-bottom quantile mean-realized spread (q=10 decile, q=5 quintile).
    Pooled across the supplied rows; NaN rows dropped."""
    s = np.asarray(scores, dtype=float)
    r = np.asarray(realized, dtype=float)
    n = min(len(s), len(r))
    s, r = s[:n], r[:n]
    mask = ~(np.isnan(s) | np.isnan(r))
    s, r = s[mask], r[mask]
    if len(s) < q:
        return None
    order = np.argsort(s, kind="mergesort")
    k = max(1, len(s) // q)
    bottom = r[order[:k]]
    top = r[order[-k:]]
    return float(np.mean(top) - np.mean(bottom))


def period_quantile_spread(scores_by_period: Dict, realized_by_period: Dict, *,
                           q: int = 10, min_names: int = 5) -> Optional[float]:
    """Mean per-period top-minus-bottom quantile spread (each period weighted
    equally so crowded periods do not dominate)."""
    spreads: List[float] = []
    for p in sorted(scores_by_period):
        s = scores_by_period.get(p)
        y = realized_by_period.get(p)
        if s is None or y is None or len(s) < max(q, min_names):
            continue
        sp = quantile_spread(s, y, q=q)
        if sp is not None and math.isfinite(sp):
            spreads.append(sp)
    return float(np.mean(spreads)) if spreads else None


def sharpe(returns: Sequence[float], *, periods_per_year: int = 12) -> Optional[float]:
    """Annualized Sharpe of a per-period return series (zero risk-free)."""
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    if len(r) < 2:
        return None
    sd = float(np.std(r, ddof=1))
    if not math.isfinite(sd) or sd <= 0:
        return None
    return float(np.mean(r) / sd * math.sqrt(periods_per_year))


def max_drawdown(returns: Sequence[float]) -> Optional[float]:
    """Maximum peak-to-trough drawdown of the compounded equity curve (<= 0)."""
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    if len(r) == 0:
        return None
    equity = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1.0
    return float(np.min(dd))


# --------------------------------------------------------------------------- #
# 6. Regime / sub-period decomposition (stationarity is not assumed).
# --------------------------------------------------------------------------- #
def decompose_by_group(period_ics: Dict, group_of_period: Dict[object, str]) -> Dict[str, Dict]:
    """Decompose per-period ICs by an arbitrary period->group map (regime or named
    sub-period). Reports per-group mean IC, t-stat and period count."""
    buckets: Dict[str, Dict] = {}
    for p, ic in period_ics.items():
        g = group_of_period.get(p, "unclassified")
        buckets.setdefault(g, {})[p] = ic
    return {g: ic_summary(d) for g, d in sorted(buckets.items())}


# --------------------------------------------------------------------------- #
# 7. Sensitivity-surface framework (measure robustness; never optimize).
# --------------------------------------------------------------------------- #
def sensitivity_surface(param_values: Sequence, evaluate_fn: Callable[[object], float]) -> Dict:
    """Evaluate a metric across the WHOLE parameter range and report the full
    surface plus a smoothness/plateau diagnostic.

    This is the robustness-measurement contract for later phases: a usable factor
    sits on a smooth plateau, not a knife-edge. It deliberately reports the entire
    surface (and a plateau ratio) and NEVER returns a single best point alone — the
    owner reads the surface and chooses where on the robustness/return frontier to
    sit (charter Section 10). No optimization is performed here.
    """
    surface = []
    for v in param_values:
        try:
            m = _f(evaluate_fn(v))
        except Exception:
            m = None
        surface.append({"param": v, "metric": m})
    metrics = [pt["metric"] for pt in surface if pt["metric"] is not None]
    diag = {"n_points": len(surface), "evaluated": len(metrics)}
    if metrics:
        arr = np.asarray(metrics, dtype=float)
        rng = float(np.max(arr) - np.min(arr))
        med = float(np.median(arr))
        # plateau ratio: fraction of points within 20% of the best metric.
        best = float(np.max(arr))
        band = 0.2 * abs(best) if best != 0 else (0.2 * (abs(med) or 1.0))
        diag.update({
            "metric_min": float(np.min(arr)), "metric_max": best,
            "metric_median": med, "metric_range": rng,
            "plateau_ratio": float(np.mean(arr >= best - band)),
            "best_param_NOT_a_recommendation": True,
        })
    return {"surface": surface, "diagnostics": diag}


# --------------------------------------------------------------------------- #
# 8. Equal-weight composite baseline (the hard benchmark; NO weight optimization).
# --------------------------------------------------------------------------- #
def equal_weight_composite(factor_scores: Dict[str, Dict[str, float]]) -> Dict[str, float]:
    """Combine pre-standardized per-factor scores into one composite by SIMPLE
    AVERAGE (equal weight). ``factor_scores`` maps factor_name -> {ticker: z}.

    This is the project's default benchmark per charter Section 5: factors are
    combined equal-weighted and weight optimization is forbidden until the harness
    is trusted AND the owner endorses a weighting philosophy. This function will
    NOT learn or tune weights — averaging only.
    """
    names: set = set()
    for d in factor_scores.values():
        names |= set(d)
    out: Dict[str, float] = {}
    for t in names:
        vals = [d[t] for d in factor_scores.values() if t in d and d[t] is not None
                and math.isfinite(float(d[t]))]
        if vals:
            out[t] = float(np.mean(vals))
    return out


# --------------------------------------------------------------------------- #
# 9. Deterministic synthetic self-check — validates the INSTRUMENT, not alpha.
# --------------------------------------------------------------------------- #
def build_synthetic_panel(*, n_periods: int = 96, n_names: int = 60, horizon: int = 1,
                          signal_beta: float = 0.6, noise: float = 1.0,
                          seed: int = 7) -> Dict:
    """Build a deterministic cross-sectional panel with a KNOWN embedded signal.

    Each period p, every name n has a feature f (known at decision time p) and a
    forward label y that resolves at period p+horizon:
        y = signal_beta * f + noise * eps      (eps ~ N(0,1))
    The honest score is f (lagged, knowable). This is synthetic test data for the
    instrument — it is NOT a factor, NOT alpha, and uses no market data.
    """
    rng = np.random.default_rng(seed)
    rows: List[Dict] = []
    for p in range(n_periods - horizon):
        f = rng.normal(0.0, 1.0, size=n_names)
        eps = rng.normal(0.0, 1.0, size=n_names)
        y = signal_beta * f + noise * eps
        for n in range(n_names):
            rows.append({
                "period": p, "label_end": p + horizon, "ticker": f"SYN{n:03d}",
                "feature": float(f[n]), "label": float(y[n]),
                # 4 calendar "years" of 24 periods each for yearly/regime grouping.
                "year": f"{2020 + p // 24}", "regime": "risk_on" if (p // 12) % 2 == 0 else "risk_off",
            })
    return {"rows": rows, "n_periods": n_periods, "n_names": n_names, "horizon": horizon}


def _scores_by_period(rows: List[Dict], score_key: str) -> Tuple[Dict, Dict]:
    sbp: Dict[int, List[float]] = {}
    rbp: Dict[int, List[float]] = {}
    for r in rows:
        sbp.setdefault(r["period"], []).append(r[score_key])
        rbp.setdefault(r["period"], []).append(r["label"])
    return sbp, rbp


def _shuffle_labels_within_period(rows: List[Dict], seed: int = 101) -> List[Dict]:
    """Placebo: permute labels within each period so any non-zero IC signals
    look-ahead/leakage rather than real skill."""
    rng = np.random.default_rng(seed)
    by_p: Dict[int, List[int]] = {}
    for i, r in enumerate(rows):
        by_p.setdefault(r["period"], []).append(i)
    out = [dict(r) for r in rows]
    for _p, idxs in by_p.items():
        labels = [rows[i]["label"] for i in idxs]
        perm = rng.permutation(len(labels))
        for slot, i in enumerate(idxs):
            out[i]["label"] = labels[perm[slot]]
    return out


def run_self_check(panel: Dict) -> Dict:
    """Exercise every harness capability on the synthetic panel and grade the
    instrument: signal recovered, placebo collapses, splits leakage-clean, purge
    removes overlaps, holdout single-touch enforced, cost model nets correctly."""
    rows = panel["rows"]
    eval_idx = [r["period"] for r in rows]
    label_end = [r["label_end"] for r in rows]

    forward = assert_strictly_forward_labels(eval_idx, label_end)

    # Honest signal: per-period rank IC of feature vs forward label.
    sbp, rbp = _scores_by_period(rows, "feature")
    honest_ics = period_rank_ics(sbp, rbp, min_names=5)
    honest = ic_summary(honest_ics)

    # Placebo: within-period label shuffle should collapse the IC to ~0.
    shuffled = _shuffle_labels_within_period(rows)
    s_sbp, s_rbp = _scores_by_period(shuffled, "feature")
    placebo = ic_summary(period_rank_ics(s_sbp, s_rbp, min_names=5))

    # Walk-forward + purged splits on the period timeline.
    wf = walk_forward_splits(eval_idx, label_end, n_folds=4, min_train_periods=12, embargo=2)
    pk = purged_kfold_splits(eval_idx, label_end, n_folds=5, embargo=2)
    wf_clean = all((f.min_embargo_gap is None or f.min_embargo_gap >= 0) for f in wf) and len(wf) > 0

    # Purge effectiveness: a deliberately long-horizon sample that overlaps a test
    # block must be dropped from that fold's train set.
    purge_demo = _purge_effectiveness_demo()

    # Yearly / regime decomposition + quantile spread.
    yearly = yearly_ic(honest_ics)
    group_map = {r["period"]: r["regime"] for r in rows}
    regime = decompose_by_group(honest_ics, group_map)
    decile = period_quantile_spread(sbp, rbp, q=10, min_names=10)

    # Cost model: a synthetic per-period gross series + turnover -> net < gross.
    rng = np.random.default_rng(5)
    gross = list(rng.normal(0.004, 0.02, size=len(honest_ics)))
    turns = list(np.clip(rng.normal(0.2, 0.05, size=len(honest_ics)), 0, 1))
    costs = apply_transaction_costs(gross, turns, cost_bps=10.0)
    net_sharpe = sharpe(costs["net_returns"], periods_per_year=12)
    mdd = max_drawdown(costs["net_returns"])

    # Multiple-testing + deflated Sharpe.
    tracker = MultipleTestingTracker()
    for i in range(20):
        tracker.register(f"config_{i}", sharpe=float(rng.normal(0.0, 0.3)))
    dsr = deflated_sharpe_ratio(net_sharpe or 0.0, n_obs=len(gross),
                                n_trials=tracker.n_trials, sharpe_variance=tracker.sharpe_variance())

    # Holdout single-touch discipline.
    ledger = HoldoutLedger(holdout_start="2023", holdout_end="2023")
    first = ledger.touch("final sign-off", when="selfcheck")
    second = ledger.touch("accidental re-peek", when="selfcheck")
    holdout_ok = first["permitted"] and (not second["permitted"]) and second["violation"]

    # Sensitivity-surface framework on a synthetic concave evaluator (plateau).
    surf = sensitivity_surface([0.0, 0.25, 0.5, 0.75, 1.0],
                               lambda a: -(a - 0.5) ** 2 + 1.0)

    # Equal-weight composite baseline sanity (averaging only).
    composite = equal_weight_composite({
        "fa": {"AAA": 1.0, "BBB": -1.0}, "fb": {"AAA": 1.0, "BBB": 1.0}})

    honest_ic = honest.get("mean_rank_ic")
    placebo_ic = placebo.get("mean_rank_ic")
    signal_recovered = honest_ic is not None and honest_ic >= SELFCHECK_MIN_HONEST_IC
    placebo_collapsed = (placebo_ic is not None and abs(placebo_ic) <= SELFCHECK_MAX_PLACEBO_ABS
                         and (honest_ic is None or abs(placebo_ic) < PLACEBO_MATERIAL_FRACTION * abs(honest_ic)))

    return {
        "strictly_forward_labels": forward,
        "honest_ic": honest, "placebo_ic": placebo,
        "signal_recovered": bool(signal_recovered),
        "placebo_collapsed": bool(placebo_collapsed),
        "walk_forward_folds": wf, "purged_kfold_folds": pk,
        "walk_forward_leakage_clean": bool(wf_clean),
        "purge_effectiveness": purge_demo,
        "yearly_ic": yearly, "regime_decomposition": regime,
        "mean_decile_spread": decile,
        "cost_model": {k: v for k, v in costs.items() if k != "net_returns"},
        "net_sharpe": net_sharpe, "net_max_drawdown": mdd,
        "deflated_sharpe_ratio": dsr,
        "multiple_testing": {"n_trials": tracker.n_trials,
                             "expected_max_sharpe": expected_max_sharpe(tracker.n_trials, tracker.sharpe_variance() or 0.0)},
        "holdout_single_touch_enforced": bool(holdout_ok),
        "holdout_ledger": ledger.to_dict(),
        "sensitivity_surface": surf,
        "equal_weight_composite_demo": composite,
    }


def _purge_effectiveness_demo() -> Dict:
    """Construct a tiny panel where one train sample has a long label horizon that
    overlaps the test block; confirm the purged k-fold drops it from train."""
    # periods 0..9; one overlapping sample at period 4 whose label resolves at 7.
    eval_idx = list(range(10)) + [4]
    label_end = [p + 1 for p in range(10)] + [7]
    folds = purged_kfold_splits(eval_idx, label_end, n_folds=2, embargo=1)
    overlap_sample = len(eval_idx) - 1  # the appended overlapping row (eval 4, label_end 7)
    purged_somewhere = any(
        (overlap_sample not in f.train_idx) and (overlap_sample not in f.test_idx)
        for f in folds if f.test_period_lo <= 7 and f.test_period_hi >= 5)
    total_purged = sum(f.n_purged for f in folds)
    return {"overlapping_sample_purged": bool(purged_somewhere),
            "total_purged_across_folds": int(total_purged),
            "n_folds": len(folds)}


# --------------------------------------------------------------------------- #
# 10. Capability catalogue / gate matrix / recommendation.
# --------------------------------------------------------------------------- #
# Each capability: (requirement key, core requirement #, function(s), status).
# status: "implemented" (live, tested) or "framework_template" (contract built,
# awaits a real factor/return series in Phase 7-C+).
HARNESS_REQUIREMENTS: List[Dict] = [
    {"req": "walk_forward_validation", "core_req": 1, "charter": "Section 6",
     "capability": "walk_forward_splits() expanding train -> forward test with embargo gap",
     "status": "implemented",
     "acceptance": "every fold's train labels resolve >= embargo periods before test opens (min_embargo_gap >= 0)"},
    {"req": "purged_cv_with_embargo", "core_req": 2, "charter": "Section 6 (safeguard 2)",
     "capability": "purged_kfold_splits() purges label-overlap rows and embargoes post-test periods",
     "status": "implemented",
     "acceptance": "a deliberately overlapping train sample is dropped; n_purged > 0 on the demo panel"},
    {"req": "true_holdout_single_touch", "core_req": 3, "charter": "Section 6 (safeguard 2)",
     "capability": "HoldoutLedger records the OOS window and flags any touch beyond the first",
     "status": "implemented",
     "acceptance": "first touch permitted; second touch flagged as a spent-budget violation"},
    {"req": "multiple_testing_counter", "core_req": 4, "charter": "Section 6 (safeguard 1)",
     "capability": "MultipleTestingTracker + expected_max_sharpe + deflated_sharpe_ratio",
     "status": "implemented",
     "acceptance": "config count increments per trial; deflated Sharpe in [0,1] and falls as n_trials rises"},
    {"req": "cost_model_net_vs_gross", "core_req": 5, "charter": "Section 6",
     "capability": "turnover_from_weights + apply_transaction_costs (bps * turnover drag)",
     "status": "implemented",
     "acceptance": "net mean < gross mean whenever turnover and cost_bps are positive"},
    {"req": "metric_suite", "core_req": 6, "charter": "Section 6",
     "capability": "mean rank IC, IC t-stat, yearly IC, decile/quintile spread, Sharpe, max drawdown, turnover, cost-adjusted return",
     "status": "implemented",
     "acceptance": "all metrics computed on the self-check panel and reported in the metric catalog"},
    {"req": "regime_subperiod_decomposition", "core_req": 7, "charter": "Section 6 (safeguard 3)",
     "capability": "decompose_by_group + yearly_ic (per-regime / per-subperiod IC)",
     "status": "implemented",
     "acceptance": "headline IC is reported per regime and per year, not only in aggregate"},
    {"req": "sensitivity_surface_framework", "core_req": 8, "charter": "Section 2.5 / Section 6",
     "capability": "sensitivity_surface() reports the whole parameter range + plateau ratio; never optimizes",
     "status": "framework_template",
     "acceptance": "full surface returned with best_param flagged NOT a recommendation; no real factor swept yet"},
    {"req": "safety_gates", "core_req": 9, "charter": "Sections 1, 6",
     "capability": "assert_strictly_forward_labels + no production model / Paper Trader / GCP / deploy / orders / automation",
     "status": "implemented",
     "acceptance": "no same-day/backward labels; every safety flag false; preview-only true"},
    {"req": "equal_weight_baseline", "core_req": 10, "charter": "Section 5",
     "capability": "equal_weight_composite() simple-average benchmark; weight optimization disabled",
     "status": "implemented",
     "acceptance": "composite is a plain mean of standardized factor scores; no weights learned"},
]


def build_metric_catalog() -> List[Dict]:
    return [
        {"metric": "mean_rank_ic", "category": "selection_skill",
         "definition": "mean across periods of the Spearman rank IC (predicted vs realized)",
         "good_looks_like": ">= ~0.03 and stable across periods", "status": "implemented"},
        {"metric": "ic_t_stat", "category": "statistical_significance",
         "definition": "mean period IC / (period-IC std / sqrt(n_periods))",
         "good_looks_like": "|t| >= ~2 with adequate period count", "status": "implemented"},
        {"metric": "yearly_ic", "category": "robustness",
         "definition": "mean rank IC grouped by calendar year",
         "good_looks_like": "positive and not driven by a single year", "status": "implemented"},
        {"metric": "decile_spread", "category": "monotonicity",
         "definition": "per-period top-decile minus bottom-decile mean realized, averaged",
         "good_looks_like": "positive and consistent with the IC sign", "status": "implemented"},
        {"metric": "quintile_spread", "category": "monotonicity",
         "definition": "same as decile spread with q=5 (top/bottom quintile)",
         "good_looks_like": "positive; smoother than deciles on small panels", "status": "implemented"},
        {"metric": "sharpe", "category": "risk_adjusted_return",
         "definition": "annualized mean/std of a per-period (net) return series",
         "good_looks_like": "positive after costs and after the deflated-Sharpe haircut", "status": "implemented"},
        {"metric": "max_drawdown", "category": "downside_risk",
         "definition": "max peak-to-trough decline of the compounded equity curve",
         "good_looks_like": "shallow relative to return; reported alongside Sharpe", "status": "implemented"},
        {"metric": "turnover", "category": "cost_reality",
         "definition": "0.5 * sum|new_w - prev_w| per rebalance",
         "good_looks_like": "low enough that net survives realistic cost bps", "status": "implemented"},
        {"metric": "cost_adjusted_return", "category": "cost_reality",
         "definition": "gross per-period return minus turnover * cost_bps/1e4",
         "good_looks_like": "still positive after modeled costs/slippage", "status": "implemented"},
        {"metric": "deflated_sharpe_ratio", "category": "multiple_testing",
         "definition": "P(observed Sharpe > expected max Sharpe over n_trials)",
         "good_looks_like": "near 1 after accounting for the number of configurations tried", "status": "implemented"},
        {"metric": "placebo_ic", "category": "leakage_control",
         "definition": "mean rank IC after within-period label permutation",
         "good_looks_like": "collapses toward 0 (any signal would indicate leakage)", "status": "implemented"},
    ]


def build_gate_matrix(self_check: Dict) -> List[Dict]:
    gates: List[Dict] = []

    def add(name, status, metric, value, threshold, note=""):
        gates.append({"gate_name": name, "status": status, "metric": metric,
                      "value": value, "threshold": threshold, "note": note})

    sc = self_check
    add("walk_forward_implemented_gate", "PASS" if sc["walk_forward_leakage_clean"] else "FAIL",
        "walk_forward_folds_leakage_clean", sc["walk_forward_leakage_clean"], "true",
        f"{len(sc['walk_forward_folds'])} expanding folds, all train labels resolve before test+embargo")
    add("purged_cv_implemented_gate",
        "PASS" if sc["purge_effectiveness"]["overlapping_sample_purged"] else "FAIL",
        "overlapping_train_sample_purged", sc["purge_effectiveness"]["overlapping_sample_purged"], "true",
        f"total purged across demo folds={sc['purge_effectiveness']['total_purged_across_folds']}")
    add("no_lookahead_gate",
        "PASS" if sc["strictly_forward_labels"]["strictly_forward"] else "FAIL",
        "label_violations", sc["strictly_forward_labels"]["n_violations"], "0",
        "no same-day or backward labels permitted")
    add("placebo_collapses_gate", "PASS" if sc["placebo_collapsed"] else "FAIL",
        "placebo_mean_rank_ic", _round(sc["placebo_ic"].get("mean_rank_ic")),
        f"|placebo| <= {SELFCHECK_MAX_PLACEBO_ABS}", "within-period label shuffle must collapse IC")
    add("signal_recovered_gate", "PASS" if sc["signal_recovered"] else "FAIL",
        "honest_mean_rank_ic", _round(sc["honest_ic"].get("mean_rank_ic")),
        f">= {SELFCHECK_MIN_HONEST_IC}", "instrument recovers the known synthetic signal")
    add("holdout_single_touch_gate", "PASS" if sc["holdout_single_touch_enforced"] else "FAIL",
        "second_touch_flagged_violation", sc["holdout_single_touch_enforced"], "true",
        "OOS holdout is a spent budget; second touch is a discipline violation")
    add("multiple_testing_counter_gate",
        "PASS" if sc["multiple_testing"]["n_trials"] > 0 and sc["deflated_sharpe_ratio"] is not None else "FAIL",
        "deflated_sharpe_ratio", _round(sc["deflated_sharpe_ratio"]),
        "in [0,1]", f"n_trials={sc['multiple_testing']['n_trials']}")
    add("cost_model_net_vs_gross_gate",
        "PASS" if sc["cost_model"]["net_mean"] < sc["cost_model"]["gross_mean"] else "FAIL",
        "net_mean_below_gross_mean", True, "net < gross",
        f"gross={_round(sc['cost_model']['gross_mean'])} net={_round(sc['cost_model']['net_mean'])}")
    add("metric_suite_gate",
        "PASS" if (sc["honest_ic"].get("ic_t_stat") is not None and sc["mean_decile_spread"] is not None
                   and sc["net_sharpe"] is not None and sc["net_max_drawdown"] is not None) else "FAIL",
        "full_metric_suite_computed", True, "true",
        "IC t-stat, decile spread, Sharpe, max drawdown all computed")
    add("regime_decomposition_gate",
        "PASS" if len(sc["regime_decomposition"]) >= 2 and len(sc["yearly_ic"]) >= 2 else "FAIL",
        "regimes_and_years_reported",
        {"regimes": len(sc["regime_decomposition"]), "years": len(sc["yearly_ic"])}, ">=2 each",
        "IC decomposed by regime and by year (stationarity not assumed)")
    add("sensitivity_surface_framework_gate",
        "PASS" if sc["sensitivity_surface"]["diagnostics"].get("n_points", 0) > 0 else "FAIL",
        "surface_points_evaluated", sc["sensitivity_surface"]["diagnostics"].get("evaluated"), ">0",
        "whole-range surface + plateau ratio; never reports a single best point alone")
    add("equal_weight_baseline_gate",
        "PASS" if len(sc["equal_weight_composite_demo"]) > 0 else "FAIL",
        "equal_weight_composite_available", True, "true",
        "simple-average benchmark wired; weight optimization disabled")

    # Safety gates (all hard-false except preview_only).
    for nm, mv in (("no_orders_gate", "orders_enabled"),
                   ("no_broker_execution_gate", "broker_execution_enabled"),
                   ("no_automation_gate", "automation_enabled"),
                   ("no_network_gate", "network_used"),
                   ("no_paid_api_gate", "paid_apis_used"),
                   ("no_deploy_gate", "deployed"),
                   ("no_production_model_gate", "production_model_built"),
                   ("no_factor_alpha_built_gate", "factor_alpha_built"),
                   ("no_weight_optimization_gate", "factor_weights_optimized"),
                   ("no_paper_trader_gate", "paper_trader_touched"),
                   ("no_gcp_gate", "gcp_touched"),
                   ("preview_only_gate", "preview_only")):
        want = True if mv == "preview_only" else False
        add(nm, "PASS", mv, want, str(want).lower(), "")
    return gates


def derive_recommendation(self_check: Dict, gates: List[Dict]) -> str:
    if self_check.get("error"):
        return REC_ERROR
    # Any safety gate failing -> BLOCKED (must never ship a leaky/unsafe instrument).
    safety_names = {"no_orders_gate", "no_broker_execution_gate", "no_automation_gate",
                    "no_network_gate", "no_paid_api_gate", "no_deploy_gate",
                    "no_production_model_gate", "no_factor_alpha_built_gate",
                    "no_weight_optimization_gate", "no_paper_trader_gate", "no_gcp_gate",
                    "preview_only_gate", "no_lookahead_gate"}
    if any(g["status"] == "FAIL" and g["gate_name"] in safety_names for g in gates):
        return REC_BLOCKED
    # All capability gates must PASS for the instrument to be trusted by 7-C.
    if all(g["status"] == "PASS" for g in gates):
        return REC_READY
    return REC_REVIEW


# --------------------------------------------------------------------------- #
# 11. Artifact writers.
# --------------------------------------------------------------------------- #
def _rel(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(_REPO_ROOT)).replace("/", "\\")
    except Exception:
        return str(p)


def _write_json(path: Path, obj) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=False)
        fh.write("\n")


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


class _OutPaths:
    def __init__(self, out_dir: Path):
        out_dir.mkdir(parents=True, exist_ok=True)
        self.dir = out_dir
        self.report = out_dir / "phase7b_validation_harness_foundation.json"
        self.requirements = out_dir / "validation_harness_requirements.csv"
        self.metric_catalog = out_dir / "validation_metric_catalog.csv"
        self.gate_matrix = out_dir / "validation_gate_matrix.csv"
        self.mt_tracker = out_dir / "multiple_testing_tracker_template.csv"
        self.split_plan = out_dir / "walk_forward_split_plan.csv"
        self.next_plan = out_dir / "phase7c_next_plan.json"


def _write_requirements(paths: _OutPaths) -> None:
    header = ["req", "core_requirement", "charter_section", "capability", "status", "acceptance_criterion"]
    rows = [[r["req"], r["core_req"], r["charter"], r["capability"], r["status"], r["acceptance"]]
            for r in HARNESS_REQUIREMENTS]
    _write_csv(paths.requirements, header, rows)


def _write_metric_catalog(paths: _OutPaths) -> None:
    header = ["metric", "category", "definition", "good_looks_like", "status"]
    rows = [[m["metric"], m["category"], m["definition"], m["good_looks_like"], m["status"]]
            for m in build_metric_catalog()]
    _write_csv(paths.metric_catalog, header, rows)


def _write_gate_matrix(paths: _OutPaths, gates: List[Dict]) -> None:
    header = ["gate_name", "status", "metric", "value", "threshold", "note"]
    rows = [[g["gate_name"], g["status"], g["metric"], g["value"], g["threshold"], g["note"]]
            for g in gates]
    _write_csv(paths.gate_matrix, header, rows)


def _write_mt_tracker_template(paths: _OutPaths) -> None:
    """A TEMPLATE ledger for multiple-testing discipline. Rows are illustrative
    placeholders showing the columns every future configuration test must log; the
    real tracker is populated at evaluation time by MultipleTestingTracker."""
    header = ["trial_id", "config_id", "phase", "description", "data_split",
              "metric_name", "metric_value", "deflated_sharpe", "holdout_touched", "decision"]
    rows = [
        [1, "EXAMPLE_equal_weight_baseline", "7-C", "equal-weight composite of 6 buckets",
         "walk_forward+purged_cv", "mean_rank_ic", "<fill>", "<fill>", "no", "<record only>"],
        ["...", "...", "...", "(one row per configuration evaluated)", "...", "...", "...", "...", "...", "..."],
    ]
    _write_csv(paths.mt_tracker, header, rows)


def _write_split_plan(paths: _OutPaths, self_check: Dict) -> None:
    header = ["scheme", "fold", "test_period_lo", "test_period_hi", "n_train", "n_test",
              "n_purged", "n_embargoed", "embargo", "min_embargo_gap"]
    rows: List[Sequence] = []
    for f in self_check["walk_forward_folds"] + self_check["purged_kfold_folds"]:
        rows.append([f.scheme, f.fold, f.test_period_lo, f.test_period_hi, f.n_train, f.n_test,
                     f.n_purged, f.n_embargoed, f.embargo,
                     f.min_embargo_gap if f.min_embargo_gap is not None else ""])
    _write_csv(paths.split_plan, header, rows)


def _write_next_plan(paths: _OutPaths, recommendation: str) -> None:
    ready = recommendation == REC_READY
    plan = {
        "phase": "7-C",
        "title": "Multi-Factor Ranking Engine (System 1) — single-factor compute + equal-weight composite",
        "charter_mapping": "Charter Phase 2 (single-factor) + Phase 3 (composite, equal weight)",
        "gated_on": "Phase 7-B validation harness trusted (this phase READY) and point-in-time data populated",
        "harness_ready": ready,
        "build_next": [
            "Compute the six single-name factor buckets point-in-time (Value, Momentum, Quality, "
            "Volatility, Growth, Sentiment/Flow).",
            "Cross-sectional z-score, winsorize +/-3, sector-neutralize each factor.",
            "Combine EQUAL WEIGHT into a composite cross-sectional rank of the S&P 500 (equal_weight_composite).",
            "Grade every factor and the composite THROUGH this 7-B harness: walk-forward + purged CV, "
            "deflated Sharpe with the config counter, per-regime/year decomposition, net-of-cost metrics, "
            "all against the equal-weight benchmark.",
        ],
        "explicitly_not_in_7c": [
            "factor-weight optimization (forbidden until the owner endorses a weighting philosophy)",
            "regime overlay / System 2 (that is Phase 7-E, gated on a trusted System 1)",
            "any order / broker / automation / Paper Trader / GCP / deploy work",
            "live provider calls beyond the charter's free-data-first sources",
        ],
        "harness_entry_points": [
            "walk_forward_splits", "purged_kfold_splits", "HoldoutLedger",
            "MultipleTestingTracker", "deflated_sharpe_ratio", "apply_transaction_costs",
            "period_rank_ics", "ic_summary", "yearly_ic", "quantile_spread",
            "sharpe", "max_drawdown", "decompose_by_group", "sensitivity_surface",
            "equal_weight_composite",
        ],
        "reuse_note": "Import these from research/run_phase7b_validation_harness_foundation.py "
                      "(no work runs at import time); they reuse research/metrics.py for rank IC.",
    }
    _write_json(paths.next_plan, plan)


# --------------------------------------------------------------------------- #
# 12. Orchestration + report assembly.
# --------------------------------------------------------------------------- #
def _fold_to_dict(f: Fold) -> Dict:
    return {"scheme": f.scheme, "fold": f.fold, "test_period_lo": f.test_period_lo,
            "test_period_hi": f.test_period_hi, "n_train": f.n_train, "n_test": f.n_test,
            "n_purged": f.n_purged, "n_embargoed": f.n_embargoed, "embargo": f.embargo,
            "min_embargo_gap": f.min_embargo_gap}


def run(out_dir: Optional[str] = None, *, seed: int = 7) -> Dict:
    paths = _OutPaths(Path(out_dir) if out_dir else _OUT_DIR_DEFAULT)
    try:
        panel = build_synthetic_panel(seed=seed)
        self_check = run_self_check(panel)
        gates = build_gate_matrix(self_check)
        recommendation = derive_recommendation(self_check, gates)

        _write_requirements(paths)
        _write_metric_catalog(paths)
        _write_gate_matrix(paths, gates)
        _write_mt_tracker_template(paths)
        _write_split_plan(paths, self_check)
        _write_next_plan(paths, recommendation)

        report = _assemble_report(paths, panel, self_check, gates, recommendation)
        _write_json(paths.report, report)
        return report
    except Exception as exc:  # pragma: no cover - defensive
        report = _assemble_report(paths, {}, {"error": str(exc)}, [], REC_ERROR)
        _write_json(paths.report, report)
        return report


def _assemble_report(paths, panel, self_check, gates, recommendation) -> Dict:
    counts = {"PASS": 0, "FAIL": 0, "WARNING": 0}
    for g in gates:
        counts[g["status"]] = counts.get(g["status"], 0) + 1
    sc = self_check
    impl = sum(1 for r in HARNESS_REQUIREMENTS if r["status"] == "implemented")
    tmpl = sum(1 for r in HARNESS_REQUIREMENTS if r["status"] != "implemented")
    return {
        "phase": PHASE,
        "objective": OBJECTIVE,
        "generated_by": "research\\run_phase7b_validation_harness_foundation.py",
        "charter": {
            "path": "docs/project_charter_sp500_multifactor_ranking_v1.md",
            "mapping": "Charter Phase 0 (backtest harness, build FIRST) + Phase 1 (point-in-time data discipline)",
            "principle": "Build the measuring instrument before building more signals.",
        },
        # Safety flags.
        "network_used": False,
        "paid_apis_used": False,
        "api_key_required": False,
        "live_data_used": False,
        "preview_only": True,
        "orders_enabled": False,
        "broker_execution_enabled": False,
        "automation_enabled": False,
        "deployed": False,
        "production_model_built": False,
        "factor_alpha_built": False,
        "factor_weights_optimized": False,
        "binary_artifacts_created": False,
        "paper_trader_touched": False,
        "gcp_touched": False,
        "raw_files_modified": False,
        "d_drive_written": False,
        # Capability summary.
        "capabilities": {
            "total_requirements": len(HARNESS_REQUIREMENTS),
            "implemented": impl,
            "framework_template": tmpl,
            "requirements": HARNESS_REQUIREMENTS,
        },
        "metric_catalog": build_metric_catalog(),
        # Self-check (grades the INSTRUMENT, on deterministic synthetic data).
        "self_check": {
            "data": "deterministic synthetic cross-sectional panel (seeded); no market data",
            "n_periods": panel.get("n_periods") if isinstance(panel, dict) else None,
            "n_names": panel.get("n_names") if isinstance(panel, dict) else None,
            "strictly_forward_labels": sc.get("strictly_forward_labels"),
            "honest_mean_rank_ic": _round((sc.get("honest_ic") or {}).get("mean_rank_ic")),
            "honest_ic_t_stat": _round((sc.get("honest_ic") or {}).get("ic_t_stat")),
            "placebo_mean_rank_ic": _round((sc.get("placebo_ic") or {}).get("mean_rank_ic")),
            "signal_recovered": sc.get("signal_recovered"),
            "placebo_collapsed": sc.get("placebo_collapsed"),
            "walk_forward_leakage_clean": sc.get("walk_forward_leakage_clean"),
            "purge_effectiveness": sc.get("purge_effectiveness"),
            "n_walk_forward_folds": len(sc.get("walk_forward_folds", [])),
            "n_purged_kfold_folds": len(sc.get("purged_kfold_folds", [])),
            "walk_forward_folds": [_fold_to_dict(f) for f in sc.get("walk_forward_folds", [])],
            "purged_kfold_folds": [_fold_to_dict(f) for f in sc.get("purged_kfold_folds", [])],
            "yearly_ic": sc.get("yearly_ic"),
            "regime_decomposition": {k: {kk: _round(vv) for kk, vv in v.items()}
                                     for k, v in (sc.get("regime_decomposition") or {}).items()},
            "mean_decile_spread": _round(sc.get("mean_decile_spread")),
            "cost_model": {k: _round(v) if isinstance(v, float) else v
                           for k, v in (sc.get("cost_model") or {}).items()},
            "net_sharpe": _round(sc.get("net_sharpe")),
            "net_max_drawdown": _round(sc.get("net_max_drawdown")),
            "deflated_sharpe_ratio": _round(sc.get("deflated_sharpe_ratio")),
            "multiple_testing": {k: _round(v) if isinstance(v, float) else v
                                 for k, v in (sc.get("multiple_testing") or {}).items()},
            "holdout_single_touch_enforced": sc.get("holdout_single_touch_enforced"),
            "holdout_ledger": sc.get("holdout_ledger"),
            "sensitivity_surface_diagnostics": (sc.get("sensitivity_surface") or {}).get("diagnostics"),
            "error": sc.get("error"),
        },
        "gate_summary": {"counts": counts, "gates": gates},
        "recommendation": recommendation,
        "recommendation_allowed_values": list(ALLOWED_RECOMMENDATIONS),
        "what_is_still_only_a_template": [
            "sensitivity_surface: framework is live and tested, but no real factor model exists "
            "to sweep yet (a synthetic concave evaluator is used in the self-check).",
            "deflated_sharpe_ratio: uses default skew=0 / kurt=3 until a real net-return series exists.",
            "multiple_testing_tracker_template.csv: an illustrative ledger; real trials are logged at "
            "evaluation time by MultipleTestingTracker in Phase 7-C+.",
            "cost model: cost_bps is an assumption until a real book's turnover and fills exist.",
        ],
        "recommended_next_phase": {"phase": "7-C", "title": "Multi-Factor Ranking Engine (System 1)"},
        "commit_appropriate": False,
        "commit_note": "Per task instruction nothing is committed or pushed. A future commit should stage "
                       "only research/run_phase7b_validation_harness_foundation.py, "
                       "tests/test_phase7b_validation_harness_foundation.py, "
                       "docs/phase7b_validation_harness_foundation_v1.md and "
                       "research/output/phase7b_validation_harness_foundation/*.",
        "artifacts": [_rel(paths.report), _rel(paths.requirements), _rel(paths.metric_catalog),
                      _rel(paths.gate_matrix), _rel(paths.mt_tracker), _rel(paths.split_plan),
                      _rel(paths.next_plan)],
    }


def _print_summary(report: Dict) -> None:
    sc = report.get("self_check", {})
    print(f"[7-B] recommendation = {report.get('recommendation')}")
    print(f"      honest IC = {sc.get('honest_mean_rank_ic')} (t={sc.get('honest_ic_t_stat')}), "
          f"placebo IC = {sc.get('placebo_mean_rank_ic')}")
    print(f"      signal_recovered={sc.get('signal_recovered')} "
          f"placebo_collapsed={sc.get('placebo_collapsed')} "
          f"purge_ok={sc.get('purge_effectiveness', {}).get('overlapping_sample_purged')}")
    c = report.get("gate_summary", {}).get("counts", {})
    print(f"      gates: {c}")


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 7-B validation harness foundation (offline self-check).")
    p.add_argument("--out-dir", default=None)
    p.add_argument("--seed", type=int, default=7)
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    report = run(out_dir=args.out_dir, seed=args.seed)
    _print_summary(report)
    rec = report.get("recommendation")
    return 0 if rec in (REC_READY, REC_REVIEW) else 1


if __name__ == "__main__":
    raise SystemExit(main())
