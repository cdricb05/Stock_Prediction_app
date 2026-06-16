"""Phase 2C — calibrated probability of outperforming SPY.

Turns the Phase 2B out-of-sample ranking score into a **calibrated probability**
``prob_outperform_spy`` in ``[0, 1]``, plus the calibration-quality report. This
is **offline / research only**: it imports neither ``api_server`` nor Paper
Trader, never writes to the production database, and does not touch the live
service. numpy/pandas only — no sklearn, no xgboost.

Method
------
- Primary calibrator: a numpy-only **isotonic regression** (pool-adjacent-
  violators) mapping raw score -> P(outperform SPY). Isotonic is monotone
  **non-decreasing by construction**, so a higher raw score can never produce a
  lower probability (plan §8 gate 3 requirement). If the relationship is flat or
  inverted, isotonic collapses to the base rate rather than inventing a slope.
- Fallback: a numpy-only **Platt / logistic** calibrator (IRLS) with the slope
  clamped non-negative for the same monotonicity guarantee; used when isotonic
  cannot be fit (degenerate calibration set). A final **base-rate constant**
  guards the case of a single-class calibration set.
- First fold has **no prior fold** to calibrate on, so it uses a label-free
  **rank** transform of the score (monotone, in (0,1), uses no outcomes) — it is
  honestly *not* outcome-calibrated and is reported as such.

Leakage control
---------------
``walk_forward_calibrate`` fits the calibrator for fold ``k`` on the pooled
out-of-sample predictions of folds ``0..k-1`` **only** (expanding, prior folds).
It never fits on the fold being scored, so the calibrated probability for a row
never saw that row's label (directly or via its fold).
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from research import metrics as M
from model import train as T
from model import risk as R

PROB_COL = "prob_outperform_spy"
LABEL_COL = "outperform_spy_flag"
SCORE_COL = "score"

# Minimum calibration rows (with both classes present) before we trust a fitted
# calibrator; below this we fall back to the base rate.
MIN_CALIB_ROWS = 30
# Plan §8 gate 3 acceptance heuristics.
GATE3_MIN_BINS = 3
GATE3_MIN_SPREAD = 0.05

# Final calibrated-prediction schema (also the sample CSV column order).
CALIBRATED_COLUMNS = [
    "ticker", "as_of_date", "target_date", "fold_id", "model_name",
    "score", "predicted_excess_return_5d", "prob_outperform_spy",
    "lo_return_5d", "hi_return_5d", "interval_width", "risk_band",
    "realized_excess_return_5d_vs_spy", "realized_return_5d", "spy_return_5d",
    "outperform_spy_flag", "positive_return_flag",
]

REQUIRED_SECTIONS = [
    "## Objective",
    "## Data source",
    "## Calibration method",
    "## Calibration table",
    "## Brier score",
    "## Monotonicity",
    "## Interval method",
    "## Interval coverage",
    "## Risk bands",
    "## Downside / drawdown",
    "## Decision gate 3 (calibration)",
    "## Decision gate 5 (intervals + drawdown)",
    "## Running this on the real GCP DB",
    "## API unchanged",
]


# --------------------------------------------------------------------------- #
# Numpy-only calibrators
# --------------------------------------------------------------------------- #
def _to_float_labels(labels: Sequence) -> np.ndarray:
    return np.asarray([np.nan if v is None else float(v) for v in labels],
                      dtype=float)


def _pava(y: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Pool-adjacent-violators: least-squares isotonic (non-decreasing) fit.

    ``y`` must already be ordered by the predictor ``x`` ascending. Returns the
    fitted value at each input position (a non-decreasing step function).
    """
    vals: List[float] = []
    wts: List[float] = []
    cnts: List[int] = []
    for i in range(len(y)):
        v = float(y[i])
        ww = float(w[i])
        c = 1
        # Merge with the previous block while it violates monotonicity.
        while vals and vals[-1] > v:
            pv, pw, pc = vals.pop(), wts.pop(), cnts.pop()
            v = (pv * pw + v * ww) / (pw + ww)
            ww = pw + ww
            c = pc + c
        vals.append(v)
        wts.append(ww)
        cnts.append(c)
    fitted = np.empty(len(y), dtype=float)
    idx = 0
    for v, c in zip(vals, cnts):
        fitted[idx:idx + c] = v
        idx += c
    return fitted


@dataclass
class IsotonicCalibrator:
    """Monotone (non-decreasing) isotonic map from raw score to probability."""
    name: str
    x: np.ndarray   # increasing unique score anchors
    y: np.ndarray   # fitted probabilities at the anchors (non-decreasing, [0,1])

    def predict(self, scores: Sequence[float]) -> np.ndarray:
        s = np.asarray(scores, dtype=float)
        if len(self.x) == 1:
            out = np.full(len(s), float(self.y[0]))
        else:
            out = np.interp(s, self.x, self.y, left=self.y[0], right=self.y[-1])
        out = np.clip(out, 0.0, 1.0)
        # Non-finite scores -> base rate (mean of anchors' range midpoint is not
        # meaningful; use the overall fitted mean which stays in range).
        out[~np.isfinite(s)] = float(np.clip(self.y.mean(), 0.0, 1.0))
        return out


@dataclass
class PlattCalibrator:
    """Logistic map ``sigmoid(a*z + b)`` with ``z=(x-mu)/sd`` and ``a >= 0``."""
    name: str
    mu: float
    sd: float
    a: float
    b: float

    def predict(self, scores: Sequence[float]) -> np.ndarray:
        s = np.asarray(scores, dtype=float)
        z = (np.where(np.isfinite(s), s, self.mu) - self.mu) / self.sd
        p = 1.0 / (1.0 + np.exp(-(self.a * z + self.b)))
        return np.clip(p, 0.0, 1.0)


@dataclass
class ConstantCalibrator:
    """Degenerate fallback: always the (prior) base rate. Trivially monotone."""
    name: str
    p: float

    def predict(self, scores: Sequence[float]) -> np.ndarray:
        return np.full(len(np.asarray(scores)), float(np.clip(self.p, 0.0, 1.0)))


@dataclass
class RankFallbackCalibrator:
    """Label-free monotone transform for the first fold (no prior data).

    Maps each score to its within-call rank in ``(0, 1)``. Monotone in score and
    uses **no outcomes**, so it cannot leak — but it is *not* outcome-calibrated,
    only an ordering-preserving placeholder until prior folds exist.
    """
    name: str = "rank_fallback"

    def predict(self, scores: Sequence[float]) -> np.ndarray:
        s = np.asarray(scores, dtype=float)
        n = len(s)
        if n == 0:
            return s
        ranks = M._rankdata(np.where(np.isfinite(s), s, np.nanmin(s)))
        return ranks / (n + 1.0)


def _fit_isotonic(scores: np.ndarray, labels: np.ndarray, name="isotonic"
                  ) -> Optional[IsotonicCalibrator]:
    mask = np.isfinite(scores) & np.isfinite(labels)
    s, y = scores[mask], labels[mask]
    if len(s) < 2 or len(np.unique(y)) < 2:
        return None
    order = np.argsort(s, kind="mergesort")
    s_sorted, y_sorted = s[order], y[order]
    fitted = _pava(y_sorted, np.ones_like(y_sorted))
    # Collapse to unique x anchors (mean fitted value per unique score) so the
    # interpolation grid is strictly increasing; monotonicity is preserved.
    ux = np.unique(s_sorted)
    fy = np.array([fitted[s_sorted == u].mean() for u in ux], dtype=float)
    fy = np.clip(fy, 0.0, 1.0)
    return IsotonicCalibrator(name=name, x=ux, y=fy)


def _fit_platt(scores: np.ndarray, labels: np.ndarray, iters: int = 50,
               name="platt") -> Optional[PlattCalibrator]:
    mask = np.isfinite(scores) & np.isfinite(labels)
    s, y = scores[mask], labels[mask]
    if len(s) < 2 or len(np.unique(y)) < 2:
        return None
    mu = float(s.mean())
    sd = float(s.std(ddof=0)) or 1.0
    z = (s - mu) / sd
    X = np.column_stack([z, np.ones_like(z)])
    beta = np.zeros(2, dtype=float)
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-(X @ beta)))
        W = p * (1.0 - p)
        W = np.where(W < 1e-6, 1e-6, W)
        grad = X.T @ (y - p)
        H = X.T @ (X * W[:, None]) + 1e-6 * np.eye(2)
        try:
            beta = beta + np.linalg.solve(H, grad)
        except np.linalg.LinAlgError:
            return None
        if not np.all(np.isfinite(beta)):
            return None
    a, b = float(beta[0]), float(beta[1])
    if a < 0:
        # Inverted relationship -> drop to the base rate to stay monotone.
        return None
    return PlattCalibrator(name=name, mu=mu, sd=sd, a=a, b=b)


def fit_calibrator(scores: Sequence[float], labels: Sequence,
                   min_rows: int = MIN_CALIB_ROWS):
    """Fit the best available monotone calibrator on (score, label) pairs.

    isotonic (primary) -> Platt/logistic (fallback) -> base-rate constant
    (final guard). Always returns an object with ``.predict`` and ``.name``.
    """
    s = np.asarray(scores, dtype=float)
    y = _to_float_labels(labels)
    mask = np.isfinite(s) & np.isfinite(y)
    s, y = s[mask], y[mask]
    base = float(y.mean()) if len(y) else 0.5
    if len(s) >= min_rows and len(np.unique(y)) >= 2:
        iso = _fit_isotonic(s, y)
        if iso is not None:
            return iso
    platt = _fit_platt(s, y)
    if platt is not None:
        return platt
    return ConstantCalibrator(name="base_rate", p=base)


# --------------------------------------------------------------------------- #
# Walk-forward, leakage-safe calibration (prior folds only)
# --------------------------------------------------------------------------- #
def walk_forward_calibrate(preds: pd.DataFrame, score_col: str = SCORE_COL,
                           label_col: str = LABEL_COL,
                           min_rows: int = MIN_CALIB_ROWS
                           ) -> pd.DataFrame:
    """Add ``prob_outperform_spy`` by calibrating each fold on PRIOR folds only.

    Fold ``k`` is scored by a calibrator fit on folds ``0..k-1``. The first fold
    (no prior data) gets the label-free rank fallback. Returns a copy of
    ``preds`` with the probability column added (and a ``calibrator_name`` helper
    column, dropped from the final schema by the caller).
    """
    out = preds.copy()
    if out.empty:
        out[PROB_COL] = pd.Series(dtype=float)
        out["calibrator_name"] = pd.Series(dtype=object)
        return out
    out[PROB_COL] = np.nan
    out["calibrator_name"] = ""
    fold_ids = sorted(out["fold_id"].unique())
    for k in fold_ids:
        cur = out["fold_id"] == k
        prior = out[out["fold_id"] < k]
        if prior.empty:
            cal = RankFallbackCalibrator()
        else:
            cal = fit_calibrator(prior[score_col].to_numpy(),
                                 prior[label_col].tolist(), min_rows=min_rows)
        probs = cal.predict(out.loc[cur, score_col].to_numpy())
        out.loc[cur, PROB_COL] = probs
        out.loc[cur, "calibrator_name"] = cal.name
    return out


# --------------------------------------------------------------------------- #
# Calibration metrics
# --------------------------------------------------------------------------- #
def brier_score(prob: Sequence[float], labels: Sequence) -> float:
    """Mean squared error between predicted probability and 0/1 outcome."""
    p = np.asarray(prob, dtype=float)
    y = _to_float_labels(labels)
    n = min(len(p), len(y))
    p, y = p[:n], y[:n]
    mask = np.isfinite(p) & np.isfinite(y)
    p, y = p[mask], y[mask]
    if len(p) == 0:
        return float("nan")
    return float(np.mean((p - y) ** 2))


def reliability_table(preds: pd.DataFrame, n_bins: int = 10) -> List[Dict]:
    """Calibration-by-decile of ``prob_outperform_spy`` vs realized outperform.

    Reuses ``research.metrics.calibration_table`` (mean predicted prob vs actual
    hit rate per probability bucket).
    """
    if preds.empty or PROB_COL not in preds:
        return []
    rows = preds[np.isfinite(preds[PROB_COL].to_numpy())]
    return M.calibration_table(rows[PROB_COL].to_numpy(),
                               rows[LABEL_COL].tolist(), n_bins=n_bins)


def buckets_are_monotone(table: List[Dict]) -> bool:
    """True if realized hit rate is non-decreasing across probability buckets."""
    if len(table) < 2:
        return False
    hr = [r["hit_rate"] for r in table]
    return all(b >= a - 1e-12 for a, b in zip(hr, hr[1:]))


def outcome_calibrated_rows(preds: pd.DataFrame) -> pd.DataFrame:
    """Rows whose probability was calibrated against outcomes (prior folds).

    The first fold has no prior data and uses the label-free rank fallback, so it
    is not outcome-calibrated and is excluded from calibration-quality metrics.
    """
    if preds.empty or "fold_id" not in preds.columns:
        return preds
    folds = sorted(preds["fold_id"].unique())
    if len(folds) <= 1:
        return preds
    return preds[preds["fold_id"] != folds[0]]


def evaluate_calibration(preds: pd.DataFrame) -> Dict:
    """Brier, reliability table, monotonicity and base-rate comparison (OOS).

    Measured on the **outcome-calibrated** rows only (the first, label-free
    fallback fold is excluded — see ``outcome_calibrated_rows``).
    """
    preds = outcome_calibrated_rows(preds)
    rows = preds[np.isfinite(preds[PROB_COL].to_numpy())] if not preds.empty \
        else preds
    if rows.empty:
        return {"n": 0, "brier": float("nan"), "brier_base_rate": float("nan"),
                "base_rate": float("nan"), "table": [], "monotone": False,
                "is_calibrated": False}
    y = _to_float_labels(rows[LABEL_COL].tolist())
    base = float(np.nanmean(y))
    table = reliability_table(rows)
    return {
        "n": int(len(rows)),
        "base_rate": base,
        "brier": brier_score(rows[PROB_COL].to_numpy(), rows[LABEL_COL].tolist()),
        "brier_base_rate": brier_score(np.full(len(rows), base),
                                       rows[LABEL_COL].tolist()),
        "table": table,
        "monotone": buckets_are_monotone(table),
        "is_calibrated": M.is_calibrated(table, min_bins=GATE3_MIN_BINS,
                                         min_spread=GATE3_MIN_SPREAD),
    }


def gate3(preds: pd.DataFrame) -> Dict:
    """Plan §8 gate 3: calibrated probability with monotone buckets / Brier.

    Passes when the reliability buckets trend up with confidence (calibrated-ish)
    AND the calibrated Brier beats the constant base-rate Brier (the probability
    carries information beyond the unconditional rate).
    """
    c = evaluate_calibration(preds)
    if c["n"] == 0:
        return {"name": "Calibrated probability with monotone buckets / Brier",
                "evaluable": False, "passed": None, **c}
    informative = (math.isfinite(c["brier"]) and math.isfinite(c["brier_base_rate"])
                   and c["brier"] <= c["brier_base_rate"] + 1e-12)
    passed = bool(c["is_calibrated"] and informative)
    return {"name": "Calibrated probability with monotone buckets / Brier",
            "evaluable": True, "passed": passed, "informative": informative, **c}


# --------------------------------------------------------------------------- #
# Orchestration: Phase 2B preds -> calibrated + risk/interval frame
# --------------------------------------------------------------------------- #
def build_calibrated_frame(preds: pd.DataFrame, coverage: float = R.DEFAULT_COVERAGE
                           ) -> pd.DataFrame:
    """Full Phase 2C frame: probabilities + conformal intervals + risk bands."""
    if preds.empty:
        return pd.DataFrame(columns=CALIBRATED_COLUMNS)
    cal = walk_forward_calibrate(preds)
    cal = R.walk_forward_intervals(cal, coverage=coverage)
    for col in CALIBRATED_COLUMNS:
        if col not in cal.columns:
            cal[col] = np.nan
    return cal[CALIBRATED_COLUMNS]


# --------------------------------------------------------------------------- #
# Markdown report
# --------------------------------------------------------------------------- #
def _fmt(x, nd: int = 4) -> str:
    if x is None:
        return "n/a"
    try:
        if isinstance(x, float) and math.isnan(x):
            return "n/a"
        return f"{x:.{nd}f}"
    except (TypeError, ValueError):
        return str(x)


def build_report_markdown(frame: pd.DataFrame, meta: Dict) -> str:
    """Render the Phase 2C calibration + risk/interval report (markdown)."""
    is_syn = meta.get("is_synthetic", True)
    src = meta.get("source", "unknown")
    cov = meta.get("coverage", R.DEFAULT_COVERAGE)
    lines: List[str] = []
    a = lines.append

    a("# Phase 2C — Calibrated Probability + Risk / Interval Layer (v1)")
    a("")
    a("_Offline / research only. This layer changes **no** live behavior: it "
      "does not modify `api_server.py`, does not write to the production "
      "database, does not deploy, and is not on the request path. It converts "
      "the Phase 2B out-of-sample scores into a calibrated probability and an "
      "honest return interval._")
    a("")
    if is_syn:
        a("> **DATA SOURCE: SYNTHETIC SAMPLE.** Built on the same synthetic "
          "`SYN_*` series as Phase 2B (a *planted* signal, not market data). "
          "**These calibration/interval numbers are NOT a market result and do "
          "NOT constitute evidence of a production edge** — they only prove the "
          "calibration and interval machinery works end-to-end.")
    else:
        a(f"> **DATA SOURCE: REAL GCP DB (read-only).** `{src}`. Genuine "
          "out-of-sample calibration and interval results on real market data.")
    a("")

    # Objective
    a("## Objective")
    a("")
    a("Take the Phase 2B out-of-sample model score and produce, per prediction:")
    a("")
    a("1. a **calibrated probability** of outperforming SPY (`prob_outperform_spy`),")
    a("2. an **expected risk band** (`risk_band`),")
    a("3. a **lower/upper return interval** (`lo_return_5d` / `hi_return_5d`), and")
    a("4. a **calibration + interval quality** assessment against plan §8 gates 3 and 5.")
    a("")

    # Data source
    a("## Data source")
    a("")
    a(f"- Source: `{src}`")
    a(f"- Generated: {meta.get('generated_on', '')}")
    a(f"- Out-of-sample predictions consumed: {meta.get('n_rows', 0)} rows, "
      f"{meta.get('n_folds', 0)} folds, {meta.get('n_tickers', 0)} tickers "
      "(from `model.train.run_walk_forward`).")
    a(f"- Raw signal calibrated: `{SCORE_COL}`; probability label: `{LABEL_COL}`.")
    if is_syn:
        a("- **Synthetic/sample results are not production edge.**")
    a("")

    cal = evaluate_calibration(frame) if not frame.empty else {"n": 0}

    # Calibration method
    a("## Calibration method")
    a("")
    a("- **Isotonic regression (pool-adjacent-violators), numpy-only.** Monotone "
      "non-decreasing by construction, so a higher score can never map to a "
      "lower probability. If the relationship is flat/inverted it collapses to "
      "the base rate rather than fabricating a slope.")
    a("- **Fallback: Platt / logistic (IRLS)** with the slope clamped "
      "non-negative; used only when isotonic cannot be fit. A base-rate constant "
      "is the final guard for a single-class calibration set.")
    a("- **Leakage control:** fold *k* is calibrated on the pooled out-of-sample "
      "predictions of folds *0..k-1* only — never on the fold being scored. The "
      "first fold has no prior data and uses a label-free rank transform "
      "(monotone, uses no outcomes), reported separately and not outcome-calibrated.")
    if not frame.empty and "calibrator_name" in frame.columns:
        used = sorted(frame["calibrator_name"].dropna().unique().tolist())
        if used:
            a(f"- Calibrators used across folds: {used}")
    a("")

    # Calibration table
    a("## Calibration table")
    a("")
    if cal.get("n", 0) and cal.get("table"):
        a("Reliability by predicted-probability bucket (mean predicted vs actual "
          "outperform rate), measured on the outcome-calibrated rows only (the "
          "first, label-free fallback fold is excluded). Reuses "
          "`research.metrics.calibration_table`.")
        a("")
        a("| bucket | n | mean predicted prob | actual outperform rate |")
        a("|---|---|---|---|")
        for r in cal["table"]:
            a(f"| {r['bin']} | {r['n']} | {_fmt(r['mean_confidence'], 3)} | "
              f"{_fmt(r['hit_rate'], 3)} |")
    else:
        a("_No calibration table — empty out-of-sample set._")
    a("")

    # Brier
    a("## Brier score")
    a("")
    if cal.get("n", 0):
        a(f"- Calibrated Brier: **{_fmt(cal['brier'])}** "
          f"(n={cal['n']}, base rate={_fmt(cal['base_rate'], 3)})")
        a(f"- Base-rate Brier (predict the unconditional rate): "
          f"{_fmt(cal['brier_base_rate'])}")
        better = (math.isfinite(cal['brier']) and math.isfinite(cal['brier_base_rate'])
                  and cal['brier'] <= cal['brier_base_rate'] + 1e-12)
        a(f"- Calibrated probability {'beats' if better else 'does not beat'} the "
          "base-rate baseline.")
    else:
        a("_No Brier score — empty out-of-sample set._")
    a("")

    # Monotonicity
    a("## Monotonicity")
    a("")
    if cal.get("n", 0):
        a(f"- Reliability buckets monotone non-decreasing: "
          f"**{cal.get('monotone')}**")
        a(f"- Calibrated-ish (metrics heuristic `is_calibrated`): "
          f"**{cal.get('is_calibrated')}**")
        a("- The calibrator itself is monotone by construction (isotonic / "
          "non-negative-slope Platt), so `prob_outperform_spy` is a "
          "non-decreasing function of the raw score.")
    else:
        a("_No monotonicity assessment — empty out-of-sample set._")
    a("")

    iv = R.evaluate_intervals(frame) if not frame.empty else {"n_finite": 0}

    # Interval method
    a("## Interval method")
    a("")
    a(f"- **Split-conformal**, numpy-only. For fold *k* we take the signed "
      f"residuals `realized_excess - predicted_excess` from folds *0..k-1* only "
      f"and use their empirical {int(cov*100)}% quantiles as the band added to "
      "each point prediction (`lo_return_5d`, `hi_return_5d`).")
    a("- Only **prior-fold** residuals are used, so there is no future-residual "
      "leakage. The first fold has no prior residuals, so its interval is left "
      "empty (reported, not fabricated).")
    a("- `risk_band` (low / medium / high) is assigned from the **interval width "
      "relative to the predicted-return magnitude** (`width / (|predicted| + eps)`) "
      "split at terciles — wider band per unit of signal = higher risk. "
      "Deterministic given the data.")
    a("")

    # Interval coverage
    a("## Interval coverage")
    a("")
    if iv.get("n_finite", 0):
        a(f"- Target coverage: {int(cov*100)}%")
        a(f"- Empirical coverage (realized excess inside [lo, hi]): "
          f"**{_fmt(iv['coverage'], 3)}** over {iv['n_finite']} rows with a "
          "conformal interval.")
        a(f"- Average interval width: {_fmt(iv['avg_width'])}")
        if iv.get("coverage_by_bucket"):
            a("")
            a("Coverage by score bucket:")
            a("")
            a("| bucket | n | mean score | coverage |")
            a("|---|---|---|---|")
            for r in iv["coverage_by_bucket"]:
                a(f"| {r['bin']} | {r['n']} | {_fmt(r['mean_value'])} | "
                  f"{_fmt(r['coverage'], 3)} |")
    else:
        a("_No conformal intervals were produced (need >= 2 folds so a later "
          "fold has prior residuals)._")
    a("")

    # Risk bands
    a("## Risk bands")
    a("")
    if iv.get("band_counts"):
        a("| risk_band | n | mean realized 5d return | worst | pct negative |")
        a("|---|---|---|---|---|")
        for band in R.RISK_BANDS:
            b = iv["band_stats"].get(band, {})
            if not b.get("n"):
                continue
            a(f"| {band} | {b['n']} | {_fmt(b['mean'])} | {_fmt(b['worst'])} | "
              f"{_fmt(b['pct_negative'], 3)} |")
        a("")
        a("Higher `risk_band` = wider interval per unit of predicted signal "
          "(more uncertainty relative to the call).")
    else:
        a("_No risk bands — no conformal intervals available on this sample._")
    a("")

    # Downside / drawdown
    a("## Downside / drawdown")
    a("")
    if iv.get("drawdown"):
        dd = iv["drawdown"]
        a("Realized 5d-return downside over rows that carry an interval "
          "(reuses `research.metrics.drawdown_summary`):")
        a("")
        a(f"- n: {dd.get('n')}")
        a(f"- worst: {_fmt(dd.get('worst'))}")
        a(f"- mean: {_fmt(dd.get('mean'))}")
        a(f"- pct negative: {_fmt(dd.get('pct_negative'), 3)}")
        a(f"- mean loss (negative rows only): {_fmt(dd.get('mean_loss'))}")
    else:
        a("_No drawdown summary — no interval rows on this sample._")
    a("")

    g3 = gate3(frame) if not frame.empty else {"evaluable": False, "passed": None}
    g5 = R.gate5(frame, coverage=cov) if not frame.empty else \
        {"evaluable": False, "passed": None}

    # Gate 3
    a("## Decision gate 3 (calibration)")
    a("")
    a(f"- {g3.get('name', 'Calibrated probability with monotone buckets / Brier')}")
    if g3.get("evaluable"):
        a(f"- Monotone buckets: {g3.get('monotone')}; calibrated-ish: "
          f"{g3.get('is_calibrated')}; Brier beats base rate: "
          f"{g3.get('informative')}")
        a(f"- Result: **{'PASS' if g3.get('passed') else 'FAIL'}**"
          + (" _(synthetic — not a promotion signal)_" if is_syn else ""))
    else:
        a("- Result: **NOT EVALUABLE** (empty out-of-sample set).")
    a("")

    # Gate 5
    a("## Decision gate 5 (intervals + drawdown)")
    a("")
    a("- Honest interval coverage near nominal + a measured drawdown.")
    if g5.get("evaluable"):
        a(f"- Empirical coverage {_fmt(g5.get('coverage'), 3)} vs target "
          f"{int(cov*100)}% (tolerance ±{int(R.COVERAGE_TOLERANCE*100)} pts); "
          f"drawdown measured over {g5.get('n_drawdown', 0)} rows.")
        a(f"- Result: **{'PASS' if g5.get('passed') else 'FAIL'}**"
          + (" _(synthetic — not a promotion signal)_" if is_syn else ""))
    else:
        a("- Result: **NOT EVALUABLE / DEFERRED** — need >= 2 folds so a later "
          "fold has prior residuals to form a conformal interval. Gate 4 "
          "(net-of-cost basket) remains deferred to Phase 2D.")
    a("")

    # Running on real DB
    a("## Running this on the real GCP DB")
    a("")
    a("This layer consumes the same Phase 2B walk-forward output, so it runs "
      "unchanged on production data — only the input source differs:")
    a("")
    a("```bash")
    a("# Real, read-only DB run; writes the calibrated CSV + this report:")
    a("python -m model.calibrate --db-url \"$DB_URL\" --max-tickers 50 \\")
    a("    --start-date 2022-01-01 --end-date 2025-01-01 \\")
    a("    --output research/output/phase2c_calibrated_predictions.csv \\")
    a("    --report-output docs/phase2c_calibration_risk_real.md")
    a("```")
    a("")
    a("- With `--db-url`, predictions come from `model.train.run_walk_forward` on "
      "`model.features.build_feature_dataset(..., with_labels=True)` (read-only).")
    a("- On the real universe there are many folds, so every fold after the first "
      "has prior out-of-sample rows to calibrate on and prior residuals to form "
      "conformal intervals — gates 3 and 5 become fully evaluable.")
    a("- **No write-back, no deploy.** Persisting calibrated probabilities to "
      "`prediction_outputs` and wiring the API read path are Phase 2D/2E and stay "
      "behind the `PREDICTOR_USE_MODEL_V2` flag.")
    a("")

    # API unchanged
    a("## API unchanged")
    a("")
    a("`api_server.py` is **not modified** by this work. `/predict` and "
      "`/predict_all_models/` behave exactly as before; this Phase 2C layer is "
      "pure offline research and is not on the live request path. "
      "This synthetic/sample report is not production edge.")
    a("")

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
DEFAULT_OUTPUT_PATH = os.path.join(
    "research", "output", "phase2c_calibrated_predictions_sample.csv")
DEFAULT_REPORT_PATH = os.path.join(
    "docs", "phase2c_calibration_risk_v1.md")


def _parse_date(s: Optional[str]) -> Optional[dt.date]:
    return dt.datetime.strptime(s, "%Y-%m-%d").date() if s else None


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Phase 2C calibration + risk/interval layer "
                    "(offline/research). Builds Phase 2B walk-forward "
                    "predictions, calibrates probabilities, adds conformal "
                    "intervals + risk bands, and writes a sample CSV and a "
                    "markdown report. Reads the DB only with --db-url; otherwise "
                    "uses a clearly-labeled SYNTHETIC sample.")
    p.add_argument("--db-url", type=str, default=None,
                   help="Postgres URL. If omitted, a SYNTHETIC sample is used.")
    p.add_argument("--tickers", type=str, default=None)
    p.add_argument("--max-tickers", type=int, default=30)
    p.add_argument("--start-date", type=str, default=None)
    p.add_argument("--end-date", type=str, default=None)
    p.add_argument("--horizon-days", type=int, default=T.DEFAULT_HORIZON)
    p.add_argument("--min-history", type=int, default=T.DEFAULT_MIN_HISTORY)
    p.add_argument("--n-folds", type=int, default=T.DEFAULT_N_FOLDS)
    p.add_argument("--embargo", type=int, default=T.DEFAULT_EMBARGO)
    p.add_argument("--min-train-dates", type=int, default=T.DEFAULT_MIN_TRAIN_DATES)
    p.add_argument("--alpha", type=float, default=T.DEFAULT_ALPHA)
    p.add_argument("--coverage", type=float, default=R.DEFAULT_COVERAGE,
                   help="Target conformal interval coverage (e.g. 0.8).")
    p.add_argument("--output", type=str, default=DEFAULT_OUTPUT_PATH)
    p.add_argument("--report-output", type=str, default=DEFAULT_REPORT_PATH)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    embargo = args.embargo
    if embargo < args.horizon_days:
        print(f"[phase2c] WARNING: embargo ({embargo}) < horizon "
              f"({args.horizon_days}); raising to horizon to prevent leakage.")
        embargo = args.horizon_days

    if args.db_url:
        tickers = ([t.strip() for t in args.tickers.split(",") if t.strip()]
                   if args.tickers else None)
        df, _ = T.build_feature_dataset(
            db_url=args.db_url, tickers=tickers, max_tickers=args.max_tickers,
            start_date=_parse_date(args.start_date),
            end_date=_parse_date(args.end_date),
            horizon=args.horizon_days, min_history=args.min_history,
            with_labels=True)
        df = T.cross_sectional_zscore(df)
        source = "DATABASE (real GCP DB, read-only)"
        is_synthetic = False
    else:
        df, _ = T.build_synthetic_signal_dataset(horizon=args.horizon_days)
        source = ("SYNTHETIC (Phase 2B planted-signal SYN_* sample; no --db-url "
                  "given — NOT real market data)")
        is_synthetic = True

    preds, fold_meta = T.run_walk_forward(
        df, target_col=T.PRIMARY_TARGET, n_folds=args.n_folds, embargo=embargo,
        min_train_dates=args.min_train_dates, alpha=args.alpha)

    frame = build_calibrated_frame(preds, coverage=args.coverage)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    frame.to_csv(args.output, index=False)

    meta = {
        "source": source, "is_synthetic": is_synthetic,
        "coverage": args.coverage,
        "n_rows": int(len(frame)),
        "n_folds": int(frame["fold_id"].nunique()) if not frame.empty else 0,
        "n_tickers": int(frame["ticker"].nunique()) if not frame.empty else 0,
        "generated_on": dt.date.today().isoformat(),
    }
    report_md = build_report_markdown(frame, meta)
    os.makedirs(os.path.dirname(args.report_output), exist_ok=True)
    with open(args.report_output, "w", encoding="utf-8") as f:
        f.write(report_md)

    g3 = gate3(frame) if not frame.empty else {"passed": None}
    g5 = R.gate5(frame, coverage=args.coverage) if not frame.empty else {"passed": None}
    cal = evaluate_calibration(frame) if not frame.empty else {"brier": float("nan")}
    iv = R.evaluate_intervals(frame) if not frame.empty else {"coverage": float("nan")}

    print(f"[phase2c] source            : {source}")
    print(f"[phase2c] OOS rows          : {len(preds)}")
    print(f"[phase2c] calibrated rows   : {len(frame)}")
    print(f"[phase2c] Brier (calibrated): {_fmt(cal.get('brier'))} "
          f"(base rate {_fmt(cal.get('brier_base_rate'))})")
    print(f"[phase2c] interval coverage : {_fmt(iv.get('coverage'), 3)} "
          f"(target {args.coverage})")
    print(f"[phase2c] gate 3 (calib)    : {g3.get('passed')}")
    print(f"[phase2c] gate 5 (interval) : {g5.get('passed')}")
    print(f"[phase2c] predictions CSV   : {args.output}")
    print(f"[phase2c] report markdown   : {args.report_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
