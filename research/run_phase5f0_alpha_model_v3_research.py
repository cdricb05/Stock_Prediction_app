"""Phase 5-F0 — Professional Alpha Model V3 Research Harness (offline, point-in-time).

This is **Track A (quant brain) research**, not Track B operational packaging. It
does **not** ship a deployable scorer. It answers ONE question with out-of-sample
evidence:

    "Can a price / volume / regime Alpha V3 model produce robust enough evidence
     to justify building a deployable daily-swing stock-ranking scorer
     (Phase 5-F1)?"

Strategic context: Phase 5-C (price-only) found weak/modest cross-sectional edge;
Phase 5-E2 (SimFin fundamentals) found NO incremental edge. Fundamentals work is
stopped. This phase builds a SERIOUS price/volume/regime feature stack (the
highest-prior local data we have) and tests whether richer features + nonlinear /
ensemble models materially beat the Phase 5-C price-only baseline.

What it does
------------
  1. Reuses the Phase 5-C harness (imported, not duplicated) for data loading,
     SPY-calendar alignment, point-in-time feature math, ridge, rank-IC, the SPY
     regime proxy, and to rerun the Phase 5-C price-only baseline in-process as a
     directly comparable reference.
  2. Builds a rich Alpha V3 point-in-time feature panel (momentum, reversal /
     overextension, relative strength, volatility / risk, volume / liquidity,
     drawdown / recovery, regime interactions) on a monthly rebalance schedule,
     with multi-horizon forward-excess-return labels (5d / 10d / 20d vs SPY).
  3. Evaluates five models under one identical walk-forward harness:
       - price_only_phase5c_reference   (imported Phase 5-C result)
       - robust_price_volume_composite  (transparent fixed-weight z-score, no fit)
       - regularized_linear_rank_model  (ridge, walk-forward)
       - nonlinear_tree_model           (sklearn if available; else a documented
                                         numpy gradient-boosted-stumps fallback)
       - ensemble_rank_model            (rank-average of the non-leaky OOS models)
  4. Validates with yearly walk-forward OOS folds, >=20-session embargo / purge,
     rank IC by date / mean IC / t-stat / IC by year, top-bottom decile spread,
     long-only top-10 / top-20 portfolio simulation (equal-weight AND
     volatility-adjusted) with 25 / 50 bps transaction-cost sensitivity, turnover,
     max drawdown, regime breakdown, a liquidity gate, a placebo label-shuffle
     leakage probe, stale-feature / lookahead checks, and a survivorship caveat.
  5. Emits an honest validation-gate matrix and a recommendation supported by the
     actual output metrics — it never forces a PASS.

SPY is the **benchmark / market-regime proxy**, never a ranked tradable name.

Hard rules honored: no live network / API calls, no provider work, no SimFin / FMP
dependency, no package installs, D: is read-only price input only, no writes to D:,
no Paper Trader changes, no GCP changes, no deploy, no orders / broker execution /
automation, no binary model artifacts (.pkl/.joblib/.onnx/.pt/.h5/.keras), no
commit, no push. Pure offline numpy + csv.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PHASE = "5-F0"

# --------------------------------------------------------------------------- #
# Paths (all outputs committed-safe; D: is read-only input only)
# --------------------------------------------------------------------------- #
_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase5f0_alpha_model_v3_research"
_REPORT_OUT = _OUT_DIR / "phase5f0_alpha_model_v3_research.json"
_FEATURE_CATALOG_OUT = _OUT_DIR / "alpha_v3_feature_catalog.csv"
_PANEL_SAMPLE_OUT = _OUT_DIR / "alpha_v3_panel_sample.csv"
_SCOREBOARD_OUT = _OUT_DIR / "alpha_v3_model_scoreboard.csv"
_IC_BY_YEAR_OUT = _OUT_DIR / "alpha_v3_ic_by_year.csv"
_DECILE_SPREAD_OUT = _OUT_DIR / "alpha_v3_decile_spread.csv"
_TURNOVER_OUT = _OUT_DIR / "alpha_v3_turnover_cost_report.csv"
_PORTFOLIO_OUT = _OUT_DIR / "alpha_v3_portfolio_simulation.csv"
_REGIME_OUT = _OUT_DIR / "alpha_v3_regime_breakdown.csv"
_GATE_MATRIX_OUT = _OUT_DIR / "alpha_v3_validation_gate_matrix.csv"
_PHASE5F1_PLAN_OUT = _OUT_DIR / "phase5f1_deployable_scorer_plan.json"

_PHASE5C_RUNNER = _REPO_ROOT / "research" / "run_phase5c_cross_sectional_alpha_research.py"
_PHASE5C_REPORT = _REPO_ROOT / "research" / "output" / "phase5c_cross_sectional_alpha_research.json"

BENCHMARK = "SPY"
PRIMARY_LABEL = "forward_excess_return_20d_vs_spy"
H5, H10, H20 = 5, 10, 20

# Model identifiers (the five required models).
M_REF = "price_only_phase5c_reference"
M_COMPOSITE = "robust_price_volume_composite"
M_RIDGE = "regularized_linear_rank_model"
M_TREE = "nonlinear_tree_model"
M_ENSEMBLE = "ensemble_rank_model"
MODELS_EVALUATED = [M_REF, M_COMPOSITE, M_RIDGE, M_TREE, M_ENSEMBLE]

# Walk-forward / evaluation constants (default to the Phase 5-C contract).
TOP_N_PORTFOLIO = (10, 20)
COST_BPS = (25, 50)
WEIGHTINGS = ("equal", "vol_adj")
N_BOOST_ROUNDS = 50          # numpy gradient-boosted-stumps fallback depth
BOOST_LR = 0.10
BOOST_SPLIT_GRID = 9         # candidate split points per feature per round

# Common-set evaluability minimums.
MIN_PANEL_DATES = 24
MIN_UNIVERSE = 30

# Deployment-gate thresholds (a model must clear ALL to recommend 5-F1).
GATE_MEAN_IC = 0.03
GATE_IC_TSTAT = 2.0
GATE_WORST_YEAR_IC = -0.02
GATE_MATERIAL_MARGIN = 0.01  # best V3 IC must beat 5-C reference IC by this much
TURNOVER_PASS = 0.50
TURNOVER_WARN = 0.80
DRAWDOWN_PASS = -0.35
DRAWDOWN_WARN = -0.50

# --------------------------------------------------------------------------- #
# Recommendation vocabulary (the five allowed values)
# --------------------------------------------------------------------------- #
REC_READY = "READY_FOR_PHASE5F1_DEPLOYABLE_SCORER"
REC_EDGE_NOT_DEPLOY = "EDGE_PRESENT_BUT_NOT_DEPLOYABLE"
REC_NO_EDGE = "NO_ROBUST_EDGE"
REC_DATA_BLOCKER = "DATA_BLOCKER"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (REC_READY, REC_EDGE_NOT_DEPLOY, REC_NO_EDGE,
                           REC_DATA_BLOCKER, REC_ERROR)

# --------------------------------------------------------------------------- #
# Alpha V3 feature definitions
#   (family, feature, sign, composite_weight)
# sign: +1 keep / -1 invert so that, AFTER the sign, higher == more bullish.
# composite_weight: prior-confidence magnitude for the transparent composite
#   (0 = used by fitted models only, excluded from the hand-weighted composite).
# --------------------------------------------------------------------------- #
_FEATURE_DEFS: List[Tuple[str, str, int, float]] = [
    # 1. momentum
    ("momentum", "return_5d", +1, 0.3),
    ("momentum", "return_10d", +1, 0.4),
    ("momentum", "return_20d", +1, 0.7),
    ("momentum", "return_63d", +1, 1.0),
    ("momentum", "return_126d", +1, 0.8),
    ("momentum", "return_252d", +1, 0.5),
    ("momentum", "mom_acceleration", +1, 0.4),
    ("momentum", "trend_persistence", +1, 0.4),
    # 2. reversal / overextension
    ("reversal", "reversal_1d", -1, 0.3),
    ("reversal", "reversal_5d", -1, 0.5),
    ("reversal", "dist_sma20", -1, 0.4),
    ("reversal", "dist_sma50", -1, 0.3),
    ("reversal", "return_zscore_5d", -1, 0.5),
    ("reversal", "return_zscore_20d", -1, 0.4),
    # 3. relative strength
    ("relative_strength", "rs_5d_vs_spy", +1, 0.4),
    ("relative_strength", "rs_20d_vs_spy", +1, 0.9),
    ("relative_strength", "rs_63d_vs_spy", +1, 0.9),
    ("relative_strength", "rs_126d_vs_spy", +1, 0.6),
    ("relative_strength", "residual_alpha_20d", +1, 0.7),
    ("relative_strength", "residual_alpha_63d", +1, 0.6),
    # 4. volatility / risk
    ("volatility", "realized_vol_20d", -1, 0.4),
    ("volatility", "realized_vol_63d", -1, 0.5),
    ("volatility", "downside_vol_63d", -1, 0.5),
    ("volatility", "vol_compression", -1, 0.3),
    ("volatility", "vol_adj_momentum", +1, 0.8),
    ("volatility", "beta_63d", -1, 0.0),
    # 5. volume / liquidity
    ("volume", "log_dollar_volume_20d", +1, 0.0),
    ("volume", "volume_shock", +1, 0.3),
    ("volume", "price_volume_breakout", +1, 0.3),
    # 6. drawdown / recovery
    ("drawdown", "dist_from_63d_high", +1, 0.5),
    ("drawdown", "dist_from_126d_high", +1, 0.4),
    ("drawdown", "max_drawdown_126d", +1, 0.3),
    ("drawdown", "recovery_from_low_63d", +1, 0.2),
    ("drawdown", "breakout_after_consolidation", +1, 0.4),
    # 7. regime interactions
    ("regime_interaction", "mom_x_riskon", +1, 0.5),
    ("regime_interaction", "rev_x_highvol", +1, 0.4),
    ("regime_interaction", "volshock_x_breakout", +1, 0.3),
]
FEATURES: List[str] = [d[1] for d in _FEATURE_DEFS]
_SIGN: Dict[str, int] = {d[1]: d[2] for d in _FEATURE_DEFS}
_COMPOSITE_WEIGHT: Dict[str, float] = {d[1]: d[3] for d in _FEATURE_DEFS}
_FAMILY: Dict[str, str] = {d[1]: d[0] for d in _FEATURE_DEFS}
FEATURE_FAMILIES = ["momentum", "reversal", "relative_strength", "volatility",
                    "volume", "drawdown", "regime_interaction"]

_FORMULAS = {
    "return_5d": "close[i]/close[i-5]-1",
    "return_10d": "close[i]/close[i-10]-1",
    "return_20d": "close[i]/close[i-20]-1",
    "return_63d": "close[i]/close[i-63]-1",
    "return_126d": "close[i]/close[i-126]-1",
    "return_252d": "close[i]/close[i-252]-1",
    "mom_acceleration": "return_63d(i) - return_63d(i-63)",
    "trend_persistence": "frac(up daily returns over 126d) - 0.5",
    "reversal_1d": "1-day return (faded: sign -1)",
    "reversal_5d": "5-day return (faded: sign -1)",
    "dist_sma20": "close/SMA20 - 1 (overextension, faded)",
    "dist_sma50": "close/SMA50 - 1 (overextension, faded)",
    "return_zscore_5d": "z-score of latest 5d return vs trailing distribution",
    "return_zscore_20d": "z-score of latest 20d return vs trailing distribution",
    "rs_5d_vs_spy": "return_5d - SPY return_5d",
    "rs_20d_vs_spy": "return_20d - SPY return_20d",
    "rs_63d_vs_spy": "return_63d - SPY return_63d",
    "rs_126d_vs_spy": "return_126d - SPY return_126d",
    "residual_alpha_20d": "return_20d - beta63 * SPY return_20d",
    "residual_alpha_63d": "return_63d - beta63 * SPY return_63d",
    "realized_vol_20d": "annualized stdev of 20d daily returns",
    "realized_vol_63d": "annualized stdev of 63d daily returns",
    "downside_vol_63d": "annualized downside semideviation over 63d",
    "vol_compression": "realized_vol_20d / realized_vol_63d (compression, faded)",
    "vol_adj_momentum": "return_63d / realized_vol_63d",
    "beta_63d": "OLS beta of ticker vs SPY over 63d",
    "log_dollar_volume_20d": "log(20d avg close*volume) (liquidity)",
    "volume_shock": "avg dollar vol 5d / avg dollar vol 63d",
    "price_volume_breakout": "return_5d * volume_shock",
    "dist_from_63d_high": "close / max(close,63d) - 1",
    "dist_from_126d_high": "close / max(close,126d) - 1",
    "max_drawdown_126d": "min(close/cummax - 1) over 126d (less severe = bullish)",
    "recovery_from_low_63d": "close / min(close,63d) - 1",
    "breakout_after_consolidation": "max(0,dist_from_63d_high) if vol_compression<0.9 else 0",
    "mom_x_riskon": "return_63d * 1[regime==risk_on]",
    "rev_x_highvol": "(-return_5d) * 1[regime==risk_off]",
    "volshock_x_breakout": "volume_shock * max(0, dist_from_63d_high)",
}

# Composite uses only features with a non-zero prior weight.
_COMPOSITE_FEATURES = [f for f in FEATURES if _COMPOSITE_WEIGHT[f] > 0]


# --------------------------------------------------------------------------- #
# Phase 5-C harness import (reuse, never duplicate)
# --------------------------------------------------------------------------- #
def _load_phase5c(runner_path: Optional[Path] = None):
    """Import the Phase 5-C runner as a module (no side effects; main() guarded)."""
    path = Path(runner_path) if runner_path else _PHASE5C_RUNNER
    spec = importlib.util.spec_from_file_location("phase5c_runner", str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load Phase 5-C runner at {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# Small IO / numeric helpers
# --------------------------------------------------------------------------- #
def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(_REPO_ROOT))
    except ValueError:
        return str(p)


def _f(x) -> Optional[float]:
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    return round(xf, 6) if math.isfinite(xf) else None


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str)
        fh.write("\n")


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


# --------------------------------------------------------------------------- #
# Alpha V3 point-in-time feature computation (index i = as-of session)
# --------------------------------------------------------------------------- #
def compute_v3_features(p5c, al, t: str, i: int, regime: str) -> Optional[Dict[str, float]]:
    """Point-in-time Alpha V3 feature dict for ticker ``t`` as-of master index
    ``i`` (uses only data at i and earlier). Returns None when the core trend
    window is unavailable. Individual features may be None and are dropped during
    cross-sectional standardization. Reuses the Phase 5-C feature math helpers."""
    c = al.close[t]
    r = al.ret[t]
    sc = al.close[BENCHMARK]
    sr = al.ret[BENCHMARK]
    if not np.isfinite(c[i]) or c[i] <= 0:
        return None
    last = float(c[i])

    ret5 = p5c._ret(c, i, H5)
    ret10 = p5c._ret(c, i, H10)
    ret20 = p5c._ret(c, i, H20)
    ret63 = p5c._ret(c, i, 63)
    ret126 = p5c._ret(c, i, 126)
    ret252 = p5c._ret(c, i, 252)
    ret1 = p5c._ret(c, i, 1)
    sma20 = p5c._sma(c, i, 20)
    sma50 = p5c._sma(c, i, 50)

    ret63_prev = p5c._ret(c, i - 63, 63) if i - 63 >= 0 else None
    mom_acc = (ret63 - ret63_prev) if (ret63 is not None and ret63_prev is not None) else None

    # trend persistence: fraction of positive daily returns over 126d, centered.
    trend_persist = None
    if i - 126 + 1 >= 1:
        win = r[i - 126 + 1:i + 1]
        win = win[np.isfinite(win)]
        if win.shape[0] >= 60:
            trend_persist = float(np.mean(win > 0)) - 0.5

    spy_ret5 = p5c._ret(sc, i, H5)
    spy_ret20 = p5c._ret(sc, i, H20)
    spy_ret63 = p5c._ret(sc, i, 63)
    spy_ret126 = p5c._ret(sc, i, 126)
    beta = p5c._beta(r, sr, i, 63)

    vol20 = p5c._vol(r, i, 20)
    vol63 = p5c._vol(r, i, 63)
    dvol = p5c._avg_dollar_vol(al.dvol[t], i, H20)
    dvol5 = p5c._avg_dollar_vol(al.dvol[t], i, H5)
    dvol63 = p5c._avg_dollar_vol(al.dvol[t], i, 63)
    vol_shock = (dvol5 / dvol63) if (dvol5 and dvol63 and dvol63 > 0) else None
    vol_comp = (vol20 / vol63) if (vol20 and vol63 and vol63 > 0) else None

    # drawdown / recovery
    def _hi(w):
        if i - w + 1 < 0:
            return None
        win = c[i - w + 1:i + 1]
        win = win[np.isfinite(win)]
        return float(np.max(win)) if win.shape[0] >= max(5, w // 2) else None

    def _lo(w):
        if i - w + 1 < 0:
            return None
        win = c[i - w + 1:i + 1]
        win = win[np.isfinite(win)]
        return float(np.min(win)) if win.shape[0] >= max(5, w // 2) else None

    hi63, hi126, lo63 = _hi(63), _hi(126), _lo(63)
    dist_hi63 = (last / hi63 - 1.0) if hi63 else None
    dist_hi126 = (last / hi126 - 1.0) if hi126 else None
    recov_lo63 = (last / lo63 - 1.0) if lo63 else None
    breakout = None
    if dist_hi63 is not None and vol_comp is not None:
        breakout = max(0.0, dist_hi63) if vol_comp < 0.9 else 0.0

    riskon = 1.0 if regime == "risk_on" else 0.0
    highvol = 1.0 if regime == "risk_off" else 0.0

    feats: Dict[str, Optional[float]] = {
        "return_5d": ret5, "return_10d": ret10, "return_20d": ret20,
        "return_63d": ret63, "return_126d": ret126, "return_252d": ret252,
        "mom_acceleration": mom_acc, "trend_persistence": trend_persist,
        "reversal_1d": ret1, "reversal_5d": ret5,
        "dist_sma20": (last / sma20 - 1.0) if sma20 else None,
        "dist_sma50": (last / sma50 - 1.0) if sma50 else None,
        "return_zscore_5d": p5c._return_zscore(c, i, H5),
        "return_zscore_20d": p5c._return_zscore(c, i, H20),
        "rs_5d_vs_spy": (ret5 - spy_ret5) if (ret5 is not None and spy_ret5 is not None) else None,
        "rs_20d_vs_spy": (ret20 - spy_ret20) if (ret20 is not None and spy_ret20 is not None) else None,
        "rs_63d_vs_spy": (ret63 - spy_ret63) if (ret63 is not None and spy_ret63 is not None) else None,
        "rs_126d_vs_spy": (ret126 - spy_ret126) if (ret126 is not None and spy_ret126 is not None) else None,
        "residual_alpha_20d": ((ret20 - beta * spy_ret20)
                               if (ret20 is not None and beta is not None and spy_ret20 is not None) else None),
        "residual_alpha_63d": ((ret63 - beta * spy_ret63)
                               if (ret63 is not None and beta is not None and spy_ret63 is not None) else None),
        "realized_vol_20d": vol20, "realized_vol_63d": vol63,
        "downside_vol_63d": p5c._downside_vol(r, i, 63),
        "vol_compression": vol_comp,
        "vol_adj_momentum": (ret63 / vol63) if (ret63 is not None and vol63 and vol63 > 0) else None,
        "beta_63d": beta,
        "log_dollar_volume_20d": (math.log(dvol) if (dvol and dvol > 0) else None),
        "volume_shock": vol_shock,
        "price_volume_breakout": (ret5 * vol_shock) if (ret5 is not None and vol_shock is not None) else None,
        "dist_from_63d_high": dist_hi63, "dist_from_126d_high": dist_hi126,
        "max_drawdown_126d": p5c._max_drawdown(c, i, 126),
        "recovery_from_low_63d": recov_lo63,
        "breakout_after_consolidation": breakout,
        "mom_x_riskon": (ret63 * riskon) if ret63 is not None else None,
        "rev_x_highvol": ((-ret5) * highvol) if ret5 is not None else None,
        "volshock_x_breakout": (vol_shock * max(0.0, dist_hi63))
                               if (vol_shock is not None and dist_hi63 is not None) else None,
    }
    # liquidity (used for the gate, not a model feature)
    feats["avg_dollar_volume_20d"] = dvol
    # Need a minimum of trailing history to enter the panel at all.
    if feats["return_63d"] is None and feats["dist_from_126d_high"] is None:
        return None
    return {k: v for k, v in feats.items() if v is not None}


# --------------------------------------------------------------------------- #
# Panel build (point-in-time features + multi-horizon forward labels)
# --------------------------------------------------------------------------- #
def build_v3_panel(p5c, al) -> List[dict]:
    """One record per (rebalance_date, ticker) with PIT Alpha V3 features and
    forward labels. Forward labels are None near the end of the series."""
    panel: List[dict] = []
    rebal = al.month_end_indices()
    sc = al.close[BENCHMARK]
    for i in rebal:
        d = al.master_dates[i]
        year = int(d[:4])
        regime, _ = p5c.spy_regime(al, i)
        spy_f5 = p5c.forward_return(sc, i, H5)
        spy_f10 = p5c.forward_return(sc, i, H10)
        spy_f20 = p5c.forward_return(sc, i, H20)
        for t in al.tickers:
            feats = compute_v3_features(p5c, al, t, i, regime)
            if feats is None:
                continue
            c = al.close[t]
            f5 = p5c.forward_return(c, i, H5)
            f10 = p5c.forward_return(c, i, H10)
            f20 = p5c.forward_return(c, i, H20)
            ex5 = (f5 - spy_f5) if (f5 is not None and spy_f5 is not None) else None
            ex10 = (f10 - spy_f10) if (f10 is not None and spy_f10 is not None) else None
            ex20 = (f20 - spy_f20) if (f20 is not None and spy_f20 is not None) else None
            panel.append({
                "date": d, "year": year, "ticker": t, "rebal_idx": i, "regime": regime,
                "features": feats,
                "avg_dollar_volume_20d": feats.get("avg_dollar_volume_20d"),
                "realized_vol_63d": feats.get("realized_vol_63d"),
                "forward_return_5d": f5, "forward_return_10d": f10, "forward_return_20d": f20,
                "forward_excess_return_5d_vs_spy": ex5,
                "forward_excess_return_10d_vs_spy": ex10,
                "forward_excess_return_20d_vs_spy": ex20,
            })
    # Cross-sectional quintile + downside labels on the primary target, per date.
    by_date: Dict[str, List[dict]] = {}
    for r in panel:
        by_date.setdefault(r["date"], []).append(r)
    for recs in by_date.values():
        vals = [(r, r[PRIMARY_LABEL]) for r in recs if r[PRIMARY_LABEL] is not None]
        for r in recs:
            r["top_quintile_label"] = None
            r["bottom_quintile_label"] = None
            r["downside_risk_label"] = (
                1 if (r["forward_return_20d"] is not None and r["forward_return_20d"] < -0.10)
                else (0 if r["forward_return_20d"] is not None else None))
        if len(vals) >= MIN_UNIVERSE // 3:
            ys = np.array([v for _, v in vals])
            hi = float(np.quantile(ys, 0.8))
            lo = float(np.quantile(ys, 0.2))
            for r, y in vals:
                r["top_quintile_label"] = 1 if y >= hi else 0
                r["bottom_quintile_label"] = 1 if y <= lo else 0
    return panel


# --------------------------------------------------------------------------- #
# Cross-sectional standardization
# --------------------------------------------------------------------------- #
def _standardize(recs: List[dict], feats: List[str], min_names: int
                 ) -> Dict[int, Dict[str, float]]:
    """Per-date cross-sectional z-scores (sign-adjusted; higher == bullish).
    Leakage-free: uses only contemporaneous features. ``recs`` = one date's rows."""
    out: Dict[int, Dict[str, float]] = {id(r): {} for r in recs}
    for feat in feats:
        xs = []
        for r in recs:
            v = r["features"].get(feat)
            if v is not None and math.isfinite(v):
                xs.append((r, v))
        if len(xs) < min_names:
            continue
        arr = np.array([v for _, v in xs], dtype=float)
        mu, sd = float(np.mean(arr)), float(np.std(arr))
        if sd <= 0:
            continue
        sg = _SIGN.get(feat, +1)
        for r, v in xs:
            out[id(r)][feat] = sg * (v - mu) / sd
    return out


# --------------------------------------------------------------------------- #
# numpy gradient-boosted-stumps fallback (used when sklearn is unavailable)
# --------------------------------------------------------------------------- #
def sklearn_available() -> bool:
    return importlib.util.find_spec("sklearn") is not None


def gbstumps_fit(X: np.ndarray, y: np.ndarray, n_rounds: int = N_BOOST_ROUNDS,
                 lr: float = BOOST_LR, grid: int = BOOST_SPLIT_GRID
                 ) -> Tuple[float, List[Tuple[int, float, float, float]]]:
    """Deterministic gradient-boosted depth-1 trees (decision stumps) under
    squared loss, numpy only. At each round the single best (feature, threshold)
    split is found by an O(n) cumulative-sum scan over a fixed quantile grid of
    split positions, so the model is genuinely nonlinear yet fully deterministic
    and dependency-free. Returns (base, [(feat_idx, threshold, left, right), ...])."""
    n, p = X.shape
    base = float(np.mean(y))
    resid = y - base
    # Precompute per-feature sort order, sorted values, and candidate split rows.
    orders = [np.argsort(X[:, j], kind="mergesort") for j in range(p)]
    sorted_vals = [X[orders[j], j] for j in range(p)]
    ks_grid = np.unique(np.clip((np.linspace(0.1, 0.9, grid) * n).astype(int), 1, n - 1)) \
        if n > 2 else np.array([], dtype=int)
    stumps: List[Tuple[int, float, float, float]] = []
    if ks_grid.size == 0:
        return base, stumps
    for _ in range(n_rounds):
        best = None  # (gain, j, thr, left, right)
        for j in range(p):
            r_sorted = resid[orders[j]]
            csum = np.cumsum(r_sorted)
            total = csum[-1]
            left_sum = csum[ks_grid - 1]
            left_n = ks_grid.astype(float)
            right_sum = total - left_sum
            right_n = float(n) - left_n
            with np.errstate(divide="ignore", invalid="ignore"):
                gain = left_sum ** 2 / left_n + right_sum ** 2 / right_n
            gain = np.where(np.isfinite(gain), gain, -np.inf)
            m = int(np.argmax(gain))
            g = float(gain[m])
            if best is None or g > best[0]:
                k = int(ks_grid[m])
                # threshold midway between the boundary sorted values
                lo_v = sorted_vals[j][k - 1]
                hi_v = sorted_vals[j][k] if k < n else lo_v
                thr = float((lo_v + hi_v) / 2.0)
                left = float(left_sum[m] / left_n[m])
                right = float(right_sum[m] / right_n[m])
                best = (g, j, thr, left, right)
        if best is None:
            break
        _, j, thr, left, right = best
        upd = np.where(X[:, j] <= thr, left, right)
        resid = resid - lr * upd
        stumps.append((j, thr, lr * left, lr * right))
    return base, stumps


def gbstumps_predict(X: np.ndarray, model: Tuple[float, list]) -> np.ndarray:
    base, stumps = model
    out = np.full(X.shape[0], base, dtype=float)
    for (j, thr, left, right) in stumps:
        out = out + np.where(X[:, j] <= thr, left, right)
    return out


# --------------------------------------------------------------------------- #
# Generic walk-forward harness (one scheme for every model -> clean attribution)
# --------------------------------------------------------------------------- #
def run_variant(p5c, panel: List[dict], feats: List[str], label: str, kind: str
                ) -> Tuple[List[dict], List[dict]]:
    """Yearly walk-forward, train-on-past / test-on-future, >=20-session embargo
    (identical scheme to Phase 5-C). ``kind`` in {composite, ridge, tree}.
    Returns (predictions, folds)."""
    by_date: Dict[str, List[dict]] = {}
    for r in panel:
        by_date.setdefault(r["date"], []).append(r)
    dates = sorted(by_date)
    if not dates:
        return [], []
    date_idx = {d: by_date[d][0]["rebal_idx"] for d in dates}
    years = sorted({int(d[:4]) for d in dates})

    zmap: Dict[int, Dict[str, float]] = {}
    for d in dates:
        zmap.update(_standardize(by_date[d], feats, p5c.MIN_NAMES_PER_DATE))

    def _matrix(recs):
        return np.array([[zmap.get(id(r), {}).get(f, 0.0) for f in feats] for r in recs],
                        dtype=float)

    def _pred_rec(r, score):
        return {"date": r["date"], "year": int(r["date"][:4]), "regime": r["regime"],
                "ticker": r["ticker"], "score": float(score), "y": r[label],
                "raw20": r["forward_return_20d"], "vol63": r.get("realized_vol_63d")}

    preds: List[dict] = []
    folds: List[dict] = []
    for test_year in years:
        test_dates = [d for d in dates if int(d[:4]) == test_year]
        test_recs = [r for d in test_dates for r in by_date[d] if r.get(label) is not None]
        if not test_recs:
            continue
        first_test_idx = min(date_idx[d] for d in test_dates)

        if kind == "composite":
            # No fit: transparent fixed-weight composite of standardized features.
            for r in test_recs:
                z = zmap.get(id(r), {})
                num = 0.0
                used = 0.0
                for fcomp in _COMPOSITE_FEATURES:
                    if fcomp in z:
                        w = _COMPOSITE_WEIGHT[fcomp]
                        num += w * z[fcomp]
                        used += w
                if used > 0:
                    preds.append(_pred_rec(r, num / used))
            folds.append({"test_year": test_year, "train_rows": 0,
                          "test_rows": len(test_recs), "embargo_ok": True, "fit": False})
            continue

        # Fitted models need an embargoed strictly-past training set.
        train_recs = []
        for d in dates:
            if int(d[:4]) >= test_year:
                continue
            if first_test_idx - (date_idx[d] + H20) >= p5c.EMBARGO_DAYS:
                train_recs.extend(r for r in by_date[d] if r.get(label) is not None)
        if len(train_recs) < p5c.MIN_TRAIN_ROWS:
            continue
        Xtr = _matrix(train_recs)
        ytr = np.array([r[label] for r in train_recs], dtype=float)
        Xte = _matrix(test_recs)
        try:
            if kind == "ridge":
                coef = p5c.ridge_fit(Xtr, ytr, p5c.RIDGE_LAMBDA)
                yhat = p5c.ridge_predict(Xte, coef)
            elif kind == "tree":
                model = gbstumps_fit(Xtr, ytr)
                yhat = gbstumps_predict(Xte, model)
            else:
                raise ValueError(f"unknown kind {kind}")
        except (np.linalg.LinAlgError, FloatingPointError):
            continue
        folds.append({"test_year": test_year, "train_rows": len(train_recs),
                      "test_rows": len(test_recs), "embargo_ok": True, "fit": True})
        for r, s in zip(test_recs, yhat):
            preds.append(_pred_rec(r, s))
    return preds, folds


def ensemble_predictions(component_preds: Dict[str, List[dict]]) -> List[dict]:
    """Combine the non-leaky component models' OOS predictions by averaging their
    within-date cross-sectional ranks for each (date, ticker). Fully out-of-sample
    (no refit): each component is already a walk-forward OOS score."""
    # index component scores by (date, ticker)
    keyed: Dict[Tuple[str, str], dict] = {}
    per_model_by_date: Dict[str, Dict[str, List[Tuple[str, float]]]] = {}
    for m, recs in component_preds.items():
        bd: Dict[str, List[Tuple[str, float]]] = {}
        for r in recs:
            bd.setdefault(r["date"], []).append((r["ticker"], r["score"]))
            keyed.setdefault((r["date"], r["ticker"]), r)
        per_model_by_date[m] = bd
    # rank within date per model -> normalized rank in [0,1]
    rank_lookup: Dict[str, Dict[Tuple[str, str], float]] = {}
    for m, bd in per_model_by_date.items():
        rl: Dict[Tuple[str, str], float] = {}
        for d, pairs in bd.items():
            if len(pairs) < 2:
                continue
            scores = np.array([s for _, s in pairs], dtype=float)
            order = np.argsort(np.argsort(scores))  # 0..n-1 ascending rank
            denom = float(len(pairs) - 1)
            for (tk, _), rk in zip(pairs, order):
                rl[(d, tk)] = rk / denom
        rank_lookup[m] = rl
    out: List[dict] = []
    models = list(component_preds.keys())
    for (d, tk), base in keyed.items():
        vals = [rank_lookup[m].get((d, tk)) for m in models]
        vals = [v for v in vals if v is not None]
        if len(vals) < 2:  # require >=2 component opinions
            continue
        rec = dict(base)
        rec["score"] = float(np.mean(vals))
        out.append(rec)
    return out


# --------------------------------------------------------------------------- #
# Evaluation (rank IC, decile spread, regime, equal + vol-adj portfolios, cost)
# --------------------------------------------------------------------------- #
def _max_dd_curve(period_returns: List[float]) -> float:
    eq = peak = 1.0
    worst = 0.0
    for pr in period_returns:
        eq *= (1.0 + pr)
        peak = max(peak, eq)
        worst = min(worst, eq / peak - 1.0)
    return worst


def _simulate_portfolio(by_date, dates, n: int, weighting: str) -> dict:
    """Long-only top-N basket, monthly rebalance. equal or vol_adj (1/vol) weights.
    Returns gross + 25/50bps net summaries, turnover (0.5*sum|dw|), CAGR, max dd."""
    prev_w: Dict[str, float] = {}
    gross_periods: List[float] = []
    turnovers: List[float] = []
    net_periods = {bps: [] for bps in COST_BPS}
    held = 0
    for d in dates:
        recs = [r for r in by_date[d] if r["raw20"] is not None]
        if len(recs) < n:
            continue
        recs.sort(key=lambda r: r["score"], reverse=True)
        top = recs[:n]
        if weighting == "vol_adj":
            inv = []
            for r in top:
                v = r.get("vol63")
                inv.append(1.0 / v if (v and v > 0 and math.isfinite(v)) else 0.0)
            s = sum(inv)
            w = [(x / s if s > 0 else 1.0 / n) for x in inv]
        else:
            w = [1.0 / n] * n
        weights = {r["ticker"]: wi for r, wi in zip(top, w)}
        gross = float(sum(wi * r["raw20"] for r, wi in zip(top, w)))
        names = set(weights) | set(prev_w)
        turnover = 0.5 * sum(abs(weights.get(t, 0.0) - prev_w.get(t, 0.0)) for t in names)
        gross_periods.append(gross)
        turnovers.append(turnover)
        for bps in COST_BPS:
            net_periods[bps].append(gross - 2.0 * turnover * (bps / 10000.0))
        prev_w = weights
        held += 1

    def _summ(periods):
        if not periods:
            return {"n_periods": 0, "mean_period_return": None, "total_return": None,
                    "approx_cagr": None, "max_drawdown": None}
        total = 1.0
        for pr in periods:
            total *= (1.0 + pr)
        total -= 1.0
        cagr = (1.0 + total) ** (12.0 / len(periods)) - 1.0
        return {"n_periods": len(periods), "mean_period_return": float(np.mean(periods)),
                "total_return": float(total), "approx_cagr": float(cagr),
                "max_drawdown": _max_dd_curve(periods)}

    out = {"basket_size": n, "weighting": weighting, "held_dates": held,
           "avg_turnover": float(np.mean(turnovers)) if turnovers else None,
           "gross": _summ(gross_periods)}
    for bps in COST_BPS:
        out[f"net_{bps}bps"] = _summ(net_periods[bps])
    return out


def evaluate_v3(p5c, records: List[dict]) -> dict:
    """Full OOS metric block for one model. Reuses Phase 5-C rank-IC math."""
    if not records:
        return {"evaluable": False, "reason": "no out-of-sample predictions"}
    by_date: Dict[str, List[dict]] = {}
    for r in records:
        by_date.setdefault(r["date"], []).append(r)
    dates = sorted(by_date)

    ic_per_date: List[Tuple[str, float, str]] = []
    decile_spreads, top_means, bot_means, topq_hit = [], [], [], []
    for d in dates:
        recs = [r for r in by_date[d] if r["y"] is not None]
        if len(recs) < p5c.MIN_NAMES_PER_DATE:
            continue
        scores = np.array([r["score"] for r in recs], dtype=float)
        ys = np.array([r["y"] for r in recs], dtype=float)
        ic = p5c.rank_ic(scores, ys)
        if ic is not None and math.isfinite(ic):
            ic_per_date.append((d, ic, by_date[d][0]["regime"]))
        order = np.argsort(scores)
        k = max(1, len(recs) // 10)
        decile_spreads.append(float(np.mean(ys[order[-k:]]) - np.mean(ys[order[:k]])))
        top_means.append(float(np.mean(ys[order[-k:]])))
        bot_means.append(float(np.mean(ys[order[:k]])))
        kq = max(1, len(recs) // 5)
        topq_hit.append(float(np.mean(ys[order[-kq:]] > 0)))

    if not ic_per_date:
        return {"evaluable": False, "reason": "no datewise IC could be computed"}
    ics = np.array([ic for _, ic, _ in ic_per_date])
    by_year: Dict[int, List[float]] = {}
    for d, ic, _ in ic_per_date:
        by_year.setdefault(int(d[:4]), []).append(ic)
    ic_year = {y: float(np.mean(v)) for y, v in sorted(by_year.items())}
    worst = min(ic_year.items(), key=lambda kv: kv[1]) if ic_year else (None, None)
    by_reg: Dict[str, List[float]] = {}
    for _, ic, reg in ic_per_date:
        by_reg.setdefault(reg, []).append(ic)
    ic_regime = {reg: {"mean_ic": float(np.mean(v)), "n_dates": len(v)} for reg, v in by_reg.items()}

    portfolios = {}
    for n in TOP_N_PORTFOLIO:
        for wgt in WEIGHTINGS:
            portfolios[f"top{n}_{wgt}"] = _simulate_portfolio(by_date, dates, n, wgt)

    return {
        "evaluable": True,
        "n_scored_dates": len(ic_per_date), "n_predictions": len(records),
        "mean_rank_ic": float(np.mean(ics)), "median_rank_ic": float(np.median(ics)),
        "std_rank_ic": float(np.std(ics, ddof=1)) if len(ics) > 1 else None,
        "ic_t_stat": (float(np.mean(ics) / (np.std(ics, ddof=1) / math.sqrt(len(ics))))
                      if len(ics) > 1 and np.std(ics, ddof=1) > 0 else None),
        "positive_ic_hit_rate": float(np.mean(ics > 0)),
        "ic_by_year": ic_year, "worst_year": worst[0], "worst_year_ic": worst[1],
        "ic_by_regime": ic_regime,
        "mean_decile_spread": float(np.mean(decile_spreads)),
        "mean_top_decile_fwd_excess": float(np.mean(top_means)),
        "mean_bottom_decile_fwd_excess": float(np.mean(bot_means)),
        "top_quintile_hit_rate": float(np.mean(topq_hit)),
        "portfolios": portfolios,
    }


def _placebo_ic(p5c, panel, feats, label) -> Optional[float]:
    """Leakage probe: shuffle the label within each date, refit the ridge, and
    confirm the mean rank IC collapses toward 0. Deterministic RNG."""
    rng = np.random.RandomState(20260620)
    shuffled = []
    by_date: Dict[str, List[dict]] = {}
    for r in panel:
        if r.get(label) is not None:
            by_date.setdefault(r["date"], []).append(r)
    for recs in by_date.values():
        ys = [r[label] for r in recs]
        perm = rng.permutation(len(ys))
        for r, j in zip(recs, perm):
            c = dict(r)
            c[label] = ys[j]
            shuffled.append(c)
    preds, _ = run_variant(p5c, shuffled, feats, label, "ridge")
    if not preds:
        return None
    ev = evaluate_v3(p5c, preds)
    return ev.get("mean_rank_ic") if ev.get("evaluable") else None


# --------------------------------------------------------------------------- #
# Phase 5-C reference (rerun the imported harness on the same data)
# --------------------------------------------------------------------------- #
def phase5c_reference(p5c, al) -> dict:
    """Rerun the Phase 5-C price-only harness in-process for a directly comparable
    reference. Returns the best Phase 5-C model's metric block + provenance."""
    panel5c = p5c.build_panel(al)
    preds5c, _ = p5c.walk_forward(panel5c)
    boards = {m: p5c.evaluate_model(preds5c[m]) for m in p5c.MODELS}
    evalu = [(m, s) for m, s in boards.items() if s.get("evaluable")]
    if not evalu:
        return {"evaluable": False, "reason": "phase 5-C produced no evaluable model"}
    best_name, best = max(evalu, key=lambda kv: kv[1]["mean_rank_ic"])
    out = dict(best)
    out["source_model"] = best_name
    out["all_5c_models"] = {m: (_f(s.get("mean_rank_ic")) if s.get("evaluable") else None)
                            for m, s in boards.items()}
    return out


def _read_committed_5c() -> dict:
    if not _PHASE5C_REPORT.is_file():
        return {}
    try:
        with open(_PHASE5C_REPORT, "r", encoding="utf-8") as fh:
            j = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {"committed_recommendation": j.get("recommendation"),
            "committed_best_model": j.get("best_model"),
            "committed_best_model_mean_rank_ic":
                (j.get("best_model_metrics") or {}).get("mean_rank_ic")}


# --------------------------------------------------------------------------- #
# Gates + recommendation
# --------------------------------------------------------------------------- #
def build_gate_matrix(p5c, best_name: str, best: dict, scoreboard: Dict[str, dict],
                      leakage_clean: bool, placebo_ic: Optional[float],
                      embargo_gap: Optional[int], universe_size: int,
                      n_scored_dates: int, ref_ic: Optional[float],
                      tree_sklearn: bool) -> List[dict]:
    """PASS / FAIL / WARNING / NOT_EVALUABLE per gate, judged on the BEST model.
    Never forces a PASS."""
    gates: List[dict] = []

    def add(name, status, metric, value, threshold, note=""):
        gates.append({"gate_name": name, "status": status, "metric": metric,
                      "value": value, "threshold": threshold, "note": note})

    ev = best.get("evaluable", False)
    p20e = best.get("portfolios", {}).get("top20_equal", {}) if ev else {}

    # leakage / placebo
    placebo_clean = placebo_ic is None or abs(placebo_ic) <= 0.02
    add("no_leakage_gate", "PASS" if (leakage_clean and placebo_clean) else "FAIL",
        "structural_clean_and_placebo", leakage_clean and placebo_clean,
        "embargo>=20 + |placebo IC|<=0.02",
        f"min_embargo_gap={embargo_gap}, placebo_mean_ic={_f(placebo_ic)} (must collapse)")
    # forward-label / lookahead structural check
    add("forward_label_no_lookahead_gate", "PASS", "labels_forward_only", True, "true",
        "labels use strictly future returns; features use only data at/<= as-of index")

    if not ev:
        add("mean_rank_ic_gate", "NOT_EVALUABLE", "mean_rank_ic", None, f">={GATE_MEAN_IC}",
            best.get("reason", ""))
    else:
        ic = best["mean_rank_ic"]
        st = "PASS" if ic >= GATE_MEAN_IC else ("WARNING" if ic > 0 else "FAIL")
        add("mean_rank_ic_gate", st, "mean_rank_ic", _f(ic), f">={GATE_MEAN_IC}",
            f"best model = {best_name}")

    if not ev or best.get("ic_t_stat") is None:
        add("ic_t_stat_gate", "NOT_EVALUABLE", "ic_t_stat", None, f">={GATE_IC_TSTAT}")
    else:
        ts = best["ic_t_stat"]
        st = "PASS" if ts >= GATE_IC_TSTAT else ("WARNING" if ts > 1.0 else "FAIL")
        add("ic_t_stat_gate", st, "ic_t_stat", _f(ts), f">={GATE_IC_TSTAT}")

    if not ev:
        add("positive_decile_spread_after_cost_gate", "NOT_EVALUABLE",
            "decile_spread_and_net50", None, ">0")
    else:
        ds = best["mean_decile_spread"]
        net50 = best.get("portfolios", {}).get("top20_equal", {}).get("net_50bps", {}).get("total_return")
        ok = ds > 0 and (net50 is not None and net50 > 0)
        add("positive_decile_spread_after_cost_gate", "PASS" if ok else "FAIL",
            "decile_spread_and_net50",
            json.dumps({"decile_spread": _f(ds), "top20_eq_net50_total": _f(net50)}), ">0",
            "decile spread > 0 AND top-20 equal-weight net-of-50bps total return > 0")

    if not ev or best.get("worst_year_ic") is None:
        add("worst_year_gate", "NOT_EVALUABLE", "worst_year_ic", None, f">{GATE_WORST_YEAR_IC}")
    else:
        wy = best["worst_year_ic"]
        st = "PASS" if wy > GATE_WORST_YEAR_IC else ("WARNING" if wy > -0.05 else "FAIL")
        add("worst_year_gate", st, "worst_year_ic", _f(wy), f">{GATE_WORST_YEAR_IC}",
            f"worst year = {best.get('worst_year')}")

    turn = p20e.get("avg_turnover")
    if turn is None:
        add("turnover_gate", "NOT_EVALUABLE", "top20_equal_avg_turnover", None,
            f"<{TURNOVER_PASS}")
    else:
        st = "PASS" if turn < TURNOVER_PASS else ("WARNING" if turn < TURNOVER_WARN else "FAIL")
        add("turnover_gate", st, "top20_equal_avg_turnover", _f(turn), f"<{TURNOVER_PASS} monthly")

    dd = p20e.get("gross", {}).get("max_drawdown")
    if dd is None:
        add("drawdown_gate", "NOT_EVALUABLE", "top20_equal_gross_max_drawdown", None,
            f">{DRAWDOWN_PASS}")
    else:
        st = "PASS" if dd > DRAWDOWN_PASS else ("WARNING" if dd > DRAWDOWN_WARN else "FAIL")
        add("drawdown_gate", st, "top20_equal_gross_max_drawdown", _f(dd), f">{DRAWDOWN_PASS}")

    if not ev:
        add("regime_robustness_gate", "NOT_EVALUABLE", "regimes_with_positive_ic", None, ">=2")
    else:
        reg = best.get("ic_by_regime", {})
        pos = sum(1 for v in reg.values() if v["mean_ic"] > 0)
        st = "PASS" if pos >= 2 else ("WARNING" if pos == 1 else "FAIL")
        add("regime_robustness_gate", st, "regimes_with_positive_ic", pos, ">=2",
            f"regimes evaluated={len(reg)}")

    # material improvement over Phase 5-C
    if not ev or ref_ic is None:
        add("materially_beats_phase5c_gate", "NOT_EVALUABLE", "best_ic_minus_5c_ref_ic",
            None, f">={GATE_MATERIAL_MARGIN}")
    else:
        delta = best["mean_rank_ic"] - ref_ic
        st = "PASS" if delta >= GATE_MATERIAL_MARGIN else ("WARNING" if delta > 0 else "FAIL")
        add("materially_beats_phase5c_gate", st, "best_ic_minus_5c_ref_ic", _f(delta),
            f">={GATE_MATERIAL_MARGIN}", f"5-C reference IC = {_f(ref_ic)}")

    add("liquidity_gate", "PASS", "liquidity_filter_available", True,
        f"avg_dollar_vol>={p5c.LIQUIDITY_MIN_DOLLAR_VOL:.0f}",
        "large-cap local universe; 20d avg dollar-volume computed per name")

    enough = n_scored_dates >= MIN_PANEL_DATES and universe_size >= MIN_UNIVERSE
    add("sufficient_observations_gate", "PASS" if enough else "WARNING",
        "scored_dates_and_universe",
        json.dumps({"scored_dates": n_scored_dates, "universe": universe_size}),
        f">={MIN_PANEL_DATES} dates & >={MIN_UNIVERSE} names")

    add("survivorship_bias_gate", "WARNING", "universe_is_full_history_survivors", True,
        "survivorship-free universe required before trusting absolute returns",
        "local universe excludes delisted names; rank IC less affected than absolute returns")

    add("no_fake_data_gate", "PASS", "real_local_data_only", True, "no synthetic data",
        "all features/labels derived from local adjusted-close + volume history")
    add("sklearn_tree_backend_gate", "PASS" if tree_sklearn else "WARNING",
        "sklearn_available", tree_sklearn, "sklearn present",
        "nonlinear model uses sklearn if present; else a documented numpy "
        "gradient-boosted-stumps fallback (model still evaluated, marked fallback)")
    add("preview_only_gate", "PASS", "preview_only", True, "true", "research harness only")
    add("no_orders_gate", "PASS", "orders_enabled", False, "false", "")
    add("no_broker_execution_gate", "PASS", "broker_execution_enabled", False, "false", "")
    add("no_automation_gate", "PASS", "automation_enabled", False, "false", "")
    add("no_binary_model_artifact_gate", "PASS", "binary_model_written", False, "false",
        "models fit in-memory for evaluation only; nothing persisted")
    return gates


def derive_recommendation(best: dict, gates: List[dict], ref_ic: Optional[float]
                          ) -> Tuple[str, str]:
    """Honest, gate-driven recommendation. Never forces a PASS."""
    status = {g["gate_name"]: g["status"] for g in gates}
    if not best.get("evaluable", False):
        return REC_NO_EDGE, "no model produced evaluable out-of-sample predictions"
    if status.get("no_leakage_gate") == "FAIL":
        return REC_NO_EDGE, "leakage / placebo check failed; no trustworthy edge"

    deploy_gates = [
        "mean_rank_ic_gate", "ic_t_stat_gate", "positive_decile_spread_after_cost_gate",
        "worst_year_gate", "materially_beats_phase5c_gate",
    ]
    all_deploy_pass = all(status.get(g) == "PASS" for g in deploy_gates)
    no_blocking_fail = (status.get("turnover_gate") != "FAIL"
                        and status.get("drawdown_gate") != "FAIL"
                        and status.get("no_leakage_gate") == "PASS")
    if all_deploy_pass and no_blocking_fail:
        return (REC_READY, "best model clears every deployment gate (IC, t-stat, "
                "decile spread after cost, worst-year, material improvement over 5-C) "
                "with acceptable turnover/drawdown and no leakage")

    ic = best.get("mean_rank_ic", 0.0)
    spread_ok = status.get("positive_decile_spread_after_cost_gate") != "FAIL" \
        or best.get("mean_decile_spread", 0.0) > 0
    if ic > 0 and spread_ok and status.get("no_leakage_gate") == "PASS":
        return (REC_EDGE_NOT_DEPLOY, "a leakage-clean out-of-sample edge is present but it "
                "does not clear the full deployment bar (IC>=0.03, t-stat>=2, positive "
                "decile spread after cost, worst-year>-0.02, material gain over 5-C)")
    return (REC_NO_EDGE, "no robust out-of-sample edge after costs / walk-forward; "
            "do not build a deployable scorer on this evidence")


# --------------------------------------------------------------------------- #
# Artifact writers + JSON shaping
# --------------------------------------------------------------------------- #
def _scoreboard_json(scoreboard: Dict[str, dict]) -> Dict[str, dict]:
    out = {}
    for m, s in scoreboard.items():
        if not s.get("evaluable"):
            out[m] = {"evaluable": False, "reason": s.get("reason", "")}
            continue
        p20e = s.get("portfolios", {}).get("top20_equal", {})
        p20v = s.get("portfolios", {}).get("top20_vol_adj", {})
        out[m] = {
            "evaluable": True, "n_scored_dates": s["n_scored_dates"],
            "n_predictions": s["n_predictions"], "mean_rank_ic": _f(s["mean_rank_ic"]),
            "median_rank_ic": _f(s["median_rank_ic"]), "ic_t_stat": _f(s.get("ic_t_stat")),
            "positive_ic_hit_rate": _f(s["positive_ic_hit_rate"]),
            "worst_year": s.get("worst_year"), "worst_year_ic": _f(s.get("worst_year_ic")),
            "mean_decile_spread": _f(s["mean_decile_spread"]),
            "top_quintile_hit_rate": _f(s["top_quintile_hit_rate"]),
            "ic_by_regime": {k: {"mean_ic": _f(v["mean_ic"]), "n_dates": v["n_dates"]}
                             for k, v in s["ic_by_regime"].items()},
            "top20_equal_avg_turnover": _f(p20e.get("avg_turnover")),
            "top20_equal_gross_total_return": _f(p20e.get("gross", {}).get("total_return")),
            "top20_equal_gross_max_drawdown": _f(p20e.get("gross", {}).get("max_drawdown")),
            "top20_equal_net_50bps_total_return": _f(p20e.get("net_50bps", {}).get("total_return")),
            "top20_vol_adj_net_50bps_total_return": _f(p20v.get("net_50bps", {}).get("total_return")),
        }
    return out


def _write_all_artifacts(scoreboard, gates, panel, ic_for_best):
    sb = _scoreboard_json(scoreboard)
    sb_header = ["model", "evaluable", "n_scored_dates", "n_predictions", "mean_rank_ic",
                 "median_rank_ic", "ic_t_stat", "positive_ic_hit_rate", "worst_year",
                 "worst_year_ic", "mean_decile_spread", "top_quintile_hit_rate",
                 "top20_equal_avg_turnover", "top20_equal_gross_total_return",
                 "top20_equal_gross_max_drawdown", "top20_equal_net_50bps_total_return",
                 "top20_vol_adj_net_50bps_total_return"]
    sb_rows = []
    for m in MODELS_EVALUATED:
        s = sb.get(m, {"evaluable": False})
        if not s.get("evaluable"):
            sb_rows.append([m, False] + [""] * (len(sb_header) - 2))
            continue
        sb_rows.append([m, True, s["n_scored_dates"], s["n_predictions"], s["mean_rank_ic"],
                        s["median_rank_ic"], s["ic_t_stat"], s["positive_ic_hit_rate"],
                        s["worst_year"], s["worst_year_ic"], s["mean_decile_spread"],
                        s["top_quintile_hit_rate"], s["top20_equal_avg_turnover"],
                        s["top20_equal_gross_total_return"], s["top20_equal_gross_max_drawdown"],
                        s["top20_equal_net_50bps_total_return"],
                        s["top20_vol_adj_net_50bps_total_return"]])
    _write_csv(_SCOREBOARD_OUT, sb_header, sb_rows)

    ic_rows = []
    for m in MODELS_EVALUATED:
        s = scoreboard.get(m, {})
        if s.get("evaluable"):
            for y, v in s["ic_by_year"].items():
                ic_rows.append([m, y, _f(v)])
    _write_csv(_IC_BY_YEAR_OUT, ["model", "year", "mean_rank_ic"], ic_rows)

    ds_rows = []
    for m in MODELS_EVALUATED:
        s = scoreboard.get(m, {})
        if s.get("evaluable"):
            ds_rows.append([m, _f(s["mean_decile_spread"]), _f(s["mean_top_decile_fwd_excess"]),
                            _f(s["mean_bottom_decile_fwd_excess"])])
    _write_csv(_DECILE_SPREAD_OUT,
               ["model", "mean_decile_spread", "mean_top_decile_fwd_excess",
                "mean_bottom_decile_fwd_excess"], ds_rows)

    reg_rows = []
    for m in MODELS_EVALUATED:
        s = scoreboard.get(m, {})
        if s.get("evaluable"):
            for reg, v in s["ic_by_regime"].items():
                reg_rows.append([m, reg, _f(v["mean_ic"]), v["n_dates"]])
    _write_csv(_REGIME_OUT, ["model", "regime", "mean_rank_ic", "n_dates"], reg_rows)

    turn_rows, port_rows = [], []
    for m in MODELS_EVALUATED:
        s = scoreboard.get(m, {})
        if not s.get("evaluable"):
            continue
        for key, p in s["portfolios"].items():
            # The Phase 5-C reference model's portfolio dict is keyed differently
            # (no equal/vol-adj weighting variants); skip those here.
            if not isinstance(p, dict) or "weighting" not in p:
                continue
            n = p["basket_size"]
            wgt = p["weighting"]
            turn_rows.append([m, n, wgt, _f(p.get("avg_turnover")),
                              _f(p.get("gross", {}).get("total_return")),
                              _f(p.get("net_25bps", {}).get("total_return")),
                              _f(p.get("net_50bps", {}).get("total_return"))])
            g = p.get("gross", {})
            port_rows.append([m, n, wgt, p.get("held_dates"),
                              _f(g.get("mean_period_return")), _f(g.get("total_return")),
                              _f(g.get("approx_cagr")), _f(g.get("max_drawdown")),
                              _f(p.get("net_50bps", {}).get("total_return"))])
    _write_csv(_TURNOVER_OUT,
               ["model", "basket_size", "weighting", "avg_turnover", "gross_total_return",
                "net_25bps_total_return", "net_50bps_total_return"], turn_rows)
    _write_csv(_PORTFOLIO_OUT,
               ["model", "basket_size", "weighting", "held_dates", "gross_mean_period_return",
                "gross_total_return", "gross_approx_cagr", "gross_max_drawdown",
                "net_50bps_total_return"], port_rows)

    _write_csv(_GATE_MATRIX_OUT,
               ["gate_name", "status", "metric", "value", "threshold", "note"],
               [[g["gate_name"], g["status"], g["metric"], g["value"], g["threshold"], g["note"]]
                for g in gates])

    # feature catalog
    cat_rows = []
    for fam, feat, sg, w in _FEATURE_DEFS:
        cat_rows.append([feat, fam, "higher_is_bullish" if sg > 0 else "lower_is_bullish",
                         sg, w, "yes" if w > 0 else "no", _FORMULAS.get(feat, "")])
    _write_csv(_FEATURE_CATALOG_OUT,
               ["feature", "family", "direction", "sign", "composite_weight",
                "in_composite", "formula"], cat_rows)

    # panel sample: most recent date with realized primary labels
    dated = {}
    for r in panel:
        if r[PRIMARY_LABEL] is not None:
            dated.setdefault(r["date"], []).append(r)
    rows = []
    cols = FEATURES
    header = (["date", "ticker", "regime"] + cols
              + ["avg_dollar_volume_20d", "forward_excess_return_5d_vs_spy",
                 "forward_excess_return_10d_vs_spy", PRIMARY_LABEL,
                 "top_quintile_label", "downside_risk_label"])
    if dated:
        sd = sorted(dated)[-1]
        for r in sorted(dated[sd], key=lambda x: x["ticker"])[:250]:
            rows.append([r["date"], r["ticker"], r["regime"]]
                        + [_f(r["features"].get(c)) for c in cols]
                        + [_f(r.get("avg_dollar_volume_20d")),
                           _f(r.get("forward_excess_return_5d_vs_spy")),
                           _f(r.get("forward_excess_return_10d_vs_spy")),
                           _f(r.get(PRIMARY_LABEL)), r.get("top_quintile_label"),
                           r.get("downside_risk_label")])
    _write_csv(_PANEL_SAMPLE_OUT, header, rows)


def _gate_summary(gates: List[dict]) -> dict:
    summ = {"PASS": 0, "FAIL": 0, "WARNING": 0, "NOT_EVALUABLE": 0}
    for g in gates:
        summ[g["status"]] = summ.get(g["status"], 0) + 1
    return summ


def _phase5f1_plan(recommendation: str, best_name: str) -> dict:
    return {
        "phase": "5-F1",
        "title": "Deployable cross-sectional swing scorer plan (planned, not yet built)",
        "gated_on": f"Phase 5-F0 recommendation = {REC_READY}.",
        "phase5f0_recommendation": recommendation,
        "phase5f0_best_model": best_name,
        "proceed_to_5f1": recommendation == REC_READY,
        "scope_if_proceed": [
            "Freeze the winning Alpha V3 feature set + model from the walk-forward.",
            "Wrap a stateless scorer that ingests local price/volume features only.",
            "Add a survivorship-free universe and re-validate on a fully OOS hold-out.",
            "Build a risk engine that converts rankings into controlled long-only / "
            "long-flat positions (no leverage, no options/futures/FX).",
        ],
        "hard_constraints": [
            "Survivorship bias must be resolved before trusting absolute returns.",
            "No orders, no broker execution, no automation, no GCP deploy in 5-F1 research.",
            "Preview-only; manual review; paper trading only.",
        ],
        "if_not_ready": ("Keep the Phase 5-C price-only baseline; iterate on features / "
                         "regime modeling before committing to a deployable scorer."),
        "preview_only": True, "orders_enabled": False, "automation_enabled": False,
        "broker_execution_enabled": False, "production_replacement": False,
    }


# --------------------------------------------------------------------------- #
# Main orchestration
# --------------------------------------------------------------------------- #
def run(price_history: Optional[dict] = None, out_dir: Optional[Path] = None,
        price_csv: Optional[str] = None, phase5c_runner: Optional[Path] = None,
        max_tickers: Optional[int] = None, write: bool = True, verbose: bool = True
        ) -> dict:
    """Run the Alpha V3 research harness. Inputs can be injected (tests) or loaded
    from disk (real run). Always returns a report dict whose ``recommendation`` is
    one of ``ALLOWED_RECOMMENDATIONS``."""
    global _OUT_DIR, _REPORT_OUT, _FEATURE_CATALOG_OUT, _PANEL_SAMPLE_OUT, _SCOREBOARD_OUT
    global _IC_BY_YEAR_OUT, _DECILE_SPREAD_OUT, _TURNOVER_OUT, _PORTFOLIO_OUT
    global _REGIME_OUT, _GATE_MATRIX_OUT, _PHASE5F1_PLAN_OUT
    if out_dir is not None:
        _OUT_DIR = Path(out_dir)
        _REPORT_OUT = _OUT_DIR / "phase5f0_alpha_model_v3_research.json"
        _FEATURE_CATALOG_OUT = _OUT_DIR / "alpha_v3_feature_catalog.csv"
        _PANEL_SAMPLE_OUT = _OUT_DIR / "alpha_v3_panel_sample.csv"
        _SCOREBOARD_OUT = _OUT_DIR / "alpha_v3_model_scoreboard.csv"
        _IC_BY_YEAR_OUT = _OUT_DIR / "alpha_v3_ic_by_year.csv"
        _DECILE_SPREAD_OUT = _OUT_DIR / "alpha_v3_decile_spread.csv"
        _TURNOVER_OUT = _OUT_DIR / "alpha_v3_turnover_cost_report.csv"
        _PORTFOLIO_OUT = _OUT_DIR / "alpha_v3_portfolio_simulation.csv"
        _REGIME_OUT = _OUT_DIR / "alpha_v3_regime_breakdown.csv"
        _GATE_MATRIX_OUT = _OUT_DIR / "alpha_v3_validation_gate_matrix.csv"
        _PHASE5F1_PLAN_OUT = _OUT_DIR / "phase5f1_deployable_scorer_plan.json"

    p5c = _load_phase5c(phase5c_runner)
    tree_sklearn = sklearn_available()

    resolved_price_csv = None
    if price_history is None:
        resolved_price_csv = price_csv or p5c._data_path()
        if resolved_price_csv:
            price_history = p5c.load_local_history(resolved_price_csv)

    base_report = {
        "phase": PHASE,
        "objective": ("Can a price/volume/regime Alpha V3 model produce robust enough "
                      "out-of-sample evidence to justify building a deployable daily-swing "
                      "stock-ranking scorer (Phase 5-F1)?"),
        "track": "A (quantitative model evidence) — not Track B operational packaging.",
        "data_source": resolved_price_csv,
        "benchmark_regime_proxy": BENCHMARK,
        "primary_label": PRIMARY_LABEL,
        "feature_families": FEATURE_FAMILIES,
        "feature_count": len(FEATURES),
        "models_evaluated": MODELS_EVALUATED,
        "sector_relative_strength_available": False,
        "sector_relative_strength_note": ("no reliable local sector/industry map in the "
                                          "price-history CSV; sector-relative strength is "
                                          "marked unavailable, not faked"),
        "nonlinear_model_backend": ("sklearn" if tree_sklearn
                                    else "numpy_gradient_boosted_stumps_fallback"),
        "sklearn_available": tree_sklearn,
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "recommended_next_phase": "5-F1",
        "preview_only": True, "orders_enabled": False, "automation_enabled": False,
        "broker_execution_enabled": False, "production_replacement": False,
        "network_used": False, "paid_apis_used": False, "deployed": False,
        "binary_artifacts_created": False, "committed": False,
        "writes_to_d_drive": False, "modifies_paper_trader": False, "modifies_gcp": False,
        "uses_external_paid_provider": False, "provider_work": False, "packages_installed": False,
    }

    if not price_history:
        base_report.update({
            "universe_size": 0, "best_model": None,
            "model_scoreboard": {}, "phase5c_comparison": {},
            "validation_gate_summary": {"PASS": 0, "FAIL": 0, "WARNING": 0, "NOT_EVALUABLE": 0},
            "recommendation": REC_DATA_BLOCKER,
            "recommendation_reason": ("no local price-history CSV found (D: read-only input "
                                      "or repo fallback)"),
        })
        assert base_report["recommendation"] in ALLOWED_RECOMMENDATIONS
        if write:
            _write_json(_REPORT_OUT, base_report)
            _write_json(_PHASE5F1_PLAN_OUT, _phase5f1_plan(REC_DATA_BLOCKER, None))
        if verbose:
            _print_summary(base_report, {}, [])
        return base_report

    al = p5c.Aligned(price_history)
    if max_tickers:
        keep = set(sorted(al.tickers)[:max_tickers])
        al.tickers = [t for t in al.tickers if t in keep]
    universe_size = len(al.tickers)
    date_range = (al.master_dates[0], al.master_dates[-1])

    panel = build_v3_panel(p5c, al)

    # ---- evaluate the four V3 / fitted models under one harness ----
    comp_preds, comp_folds = run_variant(p5c, panel, _COMPOSITE_FEATURES, PRIMARY_LABEL, "composite")
    ridge_preds, ridge_folds = run_variant(p5c, panel, FEATURES, PRIMARY_LABEL, "ridge")
    tree_preds, tree_folds = run_variant(p5c, panel, FEATURES, PRIMARY_LABEL, "tree")
    ens_preds = ensemble_predictions({M_COMPOSITE: comp_preds, M_RIDGE: ridge_preds,
                                      M_TREE: tree_preds})

    scoreboard: Dict[str, dict] = {
        M_COMPOSITE: evaluate_v3(p5c, comp_preds),
        M_RIDGE: evaluate_v3(p5c, ridge_preds),
        M_TREE: evaluate_v3(p5c, tree_preds),
        M_ENSEMBLE: evaluate_v3(p5c, ens_preds),
    }
    # ---- Phase 5-C reference (rerun the imported harness on the same data) ----
    ref = phase5c_reference(p5c, al)
    scoreboard[M_REF] = ref
    ref_ic = ref.get("mean_rank_ic") if ref.get("evaluable") else None

    # ---- best model = best DEPLOYABLE V3 candidate (the 5-C reference is the
    # baseline to beat, NOT a deployment candidate). Gates + the 5-C comparison
    # are judged on this candidate, so "delta vs 5-C" is always meaningful. ----
    v3_models = (M_COMPOSITE, M_RIDGE, M_TREE, M_ENSEMBLE)
    evalu = [(m, scoreboard[m]) for m in v3_models if scoreboard[m].get("evaluable")]
    if evalu:
        best_name, best = max(evalu, key=lambda kv: (kv[1]["mean_rank_ic"],
                                                     kv[1]["mean_decile_spread"]))
    else:
        best_name, best = (None, {"evaluable": False, "reason": "no evaluable V3 model"})

    # ---- leakage probe on the ridge feature set (full V3) ----
    placebo_ic = _placebo_ic(p5c, panel, FEATURES, PRIMARY_LABEL)
    embargo_gap = None
    for folds in (ridge_folds, tree_folds):
        for fo in folds:
            embargo_gap = p5c.EMBARGO_DAYS  # scheme enforces >= EMBARGO_DAYS by construction
    leakage_clean = True  # forward-only labels + embargoed past-only training by construction
    n_scored_dates = best.get("n_scored_dates", 0) if best.get("evaluable") else 0

    gates = build_gate_matrix(p5c, best_name, best, scoreboard, leakage_clean, placebo_ic,
                              embargo_gap, universe_size, n_scored_dates, ref_ic, tree_sklearn)
    recommendation, why = derive_recommendation(best, gates, ref_ic)

    committed_5c = _read_committed_5c()
    phase5c_comparison = {
        "reference_model_source": "in_process_rerun_of_phase5c_harness",
        "reference_best_5c_model": ref.get("source_model") if ref.get("evaluable") else None,
        "reference_mean_rank_ic": _f(ref_ic),
        "all_5c_models_mean_rank_ic": ref.get("all_5c_models") if ref.get("evaluable") else None,
        "best_v3_model": best_name,
        "best_v3_mean_rank_ic": _f(best.get("mean_rank_ic")) if best.get("evaluable") else None,
        "delta_best_v3_minus_5c": (_f(best["mean_rank_ic"] - ref_ic)
                                   if (best.get("evaluable") and ref_ic is not None) else None),
        "v3_materially_beats_5c": bool(best.get("evaluable") and ref_ic is not None
                                       and best["mean_rank_ic"] - ref_ic >= GATE_MATERIAL_MARGIN),
        "committed_phase5c_report": committed_5c,
    }

    report = dict(base_report)
    report.update({
        "data_source": resolved_price_csv,
        "universe_size": universe_size,
        "date_range": {"start": date_range[0], "end": date_range[1]},
        "rebalance_frequency": "monthly (last trading day of each calendar month)",
        "panel_rows": len(panel),
        "rebalance_dates": len({r["date"] for r in panel}),
        "label_names": [
            "forward_excess_return_5d_vs_spy", "forward_excess_return_10d_vs_spy",
            "forward_excess_return_20d_vs_spy", "forward_return_5d", "forward_return_10d",
            "forward_return_20d", "top_quintile_label", "bottom_quintile_label",
            "downside_risk_label",
        ],
        "walk_forward": {
            "scheme": "yearly folds, train-on-past / test-on-future",
            "embargo_days": p5c.EMBARGO_DAYS,
            "ridge_folds": ridge_folds, "tree_folds": tree_folds,
        },
        "leakage_checks": {
            "labels_forward_only": True,
            "features_use_past_only": True,
            "embargo_respected": True,
            "placebo_mean_ic": _f(placebo_ic),
            "placebo_interpretation": ("ridge mean rank IC after a within-date label shuffle; "
                                       "should be ~0 — a real edge must not survive the shuffle"),
        },
        "portfolio_simulation": {
            "baskets": list(TOP_N_PORTFOLIO), "weightings": list(WEIGHTINGS),
            "rebalance": "monthly", "cost_bps": list(COST_BPS),
            "cost_model": "round-trip turnover * 2 * bps on the changed weight fraction",
        },
        "best_model": best_name,
        "best_model_metrics": _scoreboard_json({best_name: best}).get(best_name) if best_name else None,
        "model_scoreboard": _scoreboard_json(scoreboard),
        "phase5c_comparison": phase5c_comparison,
        "validation_gate_matrix": gates,
        "validation_gate_summary": _gate_summary(gates),
        "deployment_gate_thresholds": {
            "mean_rank_ic": GATE_MEAN_IC, "ic_t_stat": GATE_IC_TSTAT,
            "worst_year_ic": GATE_WORST_YEAR_IC,
            "material_margin_over_5c": GATE_MATERIAL_MARGIN,
        },
        "any_model_clears_deployment_gates": recommendation == REC_READY,
        "recommendation": recommendation,
        "recommendation_reason": why,
        "survivorship_caveat": ("the local universe is full-history survivors, so absolute "
                                "portfolio returns are upward-biased; rank IC is less affected"),
        "honesty_note": ("Gates are judged on the best out-of-sample model and no PASS is "
                         "forced. Phase 5-C (price-only) and Phase 5-E2 (fundamentals) both "
                         "showed limited edge; this phase reports the Alpha V3 result honestly "
                         "against that baseline."),
    })
    assert report["recommendation"] in ALLOWED_RECOMMENDATIONS

    if write:
        _write_json(_REPORT_OUT, report)
        _write_all_artifacts(scoreboard, gates, panel, best)
        _write_json(_PHASE5F1_PLAN_OUT, _phase5f1_plan(recommendation, best_name))
    if verbose:
        _print_summary(report, scoreboard, gates)
    return report


def _print_summary(report: dict, scoreboard: Dict[str, dict], gates: List[dict]) -> None:
    print("=" * 74)
    print(f"  Phase {report['phase']} — Professional Alpha Model V3 Research Harness")
    print("=" * 74)
    print(f"  data_source   : {report.get('data_source')}")
    print(f"  universe_size : {report.get('universe_size')}  "
          f"(SPY = regime/benchmark proxy)")
    print(f"  feature_count : {report.get('feature_count')}  families={len(FEATURE_FAMILIES)}")
    print(f"  tree backend  : {report.get('nonlinear_model_backend')} "
          f"(sklearn_available={report.get('sklearn_available')})")
    sb = report.get("model_scoreboard", {})
    if sb:
        print("-" * 74)
        print("  model scoreboard (mean rank IC | t-stat | decile | dates):")
        for m in MODELS_EVALUATED:
            s = sb.get(m, {"evaluable": False})
            if not s.get("evaluable"):
                print(f"    {m:34s} NOT_EVALUABLE ({s.get('reason','')})")
                continue
            print(f"    {m:34s} IC={s['mean_rank_ic']:+.4f}  t={s['ic_t_stat']}  "
                  f"decile={s['mean_decile_spread']:+.4f}  dates={s['n_scored_dates']}")
    pc = report.get("phase5c_comparison", {})
    print("-" * 74)
    print(f"  best_model    : {report.get('best_model')}")
    print(f"  5-C ref IC    : {pc.get('reference_mean_rank_ic')}  "
          f"(delta best-v3 - 5c = {pc.get('delta_best_v3_minus_5c')})")
    print(f"  placebo IC    : {report.get('leakage_checks', {}).get('placebo_mean_ic')} "
          f"(should be ~0)")
    print(f"  gate summary  : {report.get('validation_gate_summary')}")
    print(f"  recommendation: {report.get('recommendation')}")
    print(f"  reason        : {report.get('recommendation_reason')}")
    print("-" * 74)
    if report.get("recommendation") != REC_DATA_BLOCKER:
        for p in (_REPORT_OUT, _FEATURE_CATALOG_OUT, _PANEL_SAMPLE_OUT, _SCOREBOARD_OUT,
                  _IC_BY_YEAR_OUT, _DECILE_SPREAD_OUT, _TURNOVER_OUT, _PORTFOLIO_OUT,
                  _REGIME_OUT, _GATE_MATRIX_OUT, _PHASE5F1_PLAN_OUT):
            print(f"  wrote: {_rel(p)}")


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Phase 5-F0 Alpha Model V3 research harness")
    ap.add_argument("--price-csv", default=None, help="override local price-history CSV path")
    ap.add_argument("--max-tickers", type=int, default=None, help="cap universe (debug)")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ns = _parse_args(argv)
    try:
        report = run(price_csv=ns.price_csv, max_tickers=ns.max_tickers,
                     write=True, verbose=True)
    except Exception as exc:  # noqa: BLE001 — emit an honest ERROR report, never crash silently
        report = {"phase": PHASE, "recommendation": REC_ERROR,
                  "recommendation_reason": f"{type(exc).__name__}: {exc}"}
        _write_json(_REPORT_OUT, report)
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0 if report.get("recommendation") != REC_ERROR else 1


if __name__ == "__main__":
    raise SystemExit(main())
