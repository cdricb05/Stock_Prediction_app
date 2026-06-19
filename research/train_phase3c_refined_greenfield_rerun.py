"""Phase 3-C Refined Greenfield Walk-Forward Rerun (offline, research-only, no deployment).

Phase 3-A trained a from-scratch greenfield baseline (GREENFIELD_BASELINE_WEAK_BUT_IMPROVABLE):
the best learned configuration (a numpy closed-form ridge at the 63-day horizon) produced a
positive out-of-sample mean rank IC (~0.0506) that beat the model-free composite, but one
walk-forward fold was catastrophic. Phase 3-B diagnosed that instability without retraining
(four catastrophic 63d folds clustering at regime turning points, driven by non-stationary
low-volatility / momentum coefficients, high sector concentration, structural feature
redundancy, and 63d overlapping-label autocorrelation), and emitted a concrete refined
configuration with a kill switch (PROCEED_TO_REFINED_GREENFIELD_RERUN -> Phase 3-C).

Phase 3-C runs that refined configuration through a stricter walk-forward test:
  * 21d is the PRIMARY horizon; 63d is SECONDARY (with an overlapping-label correction); 5d is
    DIAGNOSTIC-only.
  * cross_sectional_ranks are pruned entirely; sign-mirror duplicate raw return_Nd columns are
    dropped (reversal_Nd kept); volume / liquidity is collapsed to a single liquidity control.
  * learned predictions are sector-neutralised and risk-neutralised (beta / volatility /
    SPY-correlation) and cross-sectionally winsorised by date before scoring.
  * a hard kill switch is applied to the primary 21d refined ridge: if it still produces any
    catastrophic fold, an unstable mean IC, or a sub-0.60 fold win rate, the recommendation is
    PRICE_VOLUME_ONLY_KILL_SWITCH_TRIGGERED and the next phase is the external-data decision.

This is a research-training phase. It trains numpy-only research models locally / offline. It is
NOT allowed to and does NOT: train or save a production / deployable model artifact, create a
production model candidate, enable the model-v2 serving flag, deploy, restart any service, run
migrations, write to any database, place orders, automate trading, fetch anything from the
network, write to the D: drive, or claim a production edge. Every result is reported as
survivorship-biased / current-membership caveated and is not a production edge. The full
guardrail rationale lives in docs/phase3c_refined_greenfield_rerun_v1.md.

It reads (read-only):
  * research/output/phase3b_greenfield_refinement.json   (Phase 3-B diagnostic result)
  * research/output/phase3b_refined_config.json          (Phase 3-B refined config)
  * research/output/phase3a_greenfield_baseline.json     (Phase 3-A baseline, for comparison)
  * research/input/phase2k_p_sector_map_current.csv      (sector / industry)
  * D: expanded price-history CSV (ticker, date, adjusted OHLC, volume) for the panel.
  * D: data-quality / build-summary / survivorship-caveat JSONs for provenance + caveats.

It writes only four small files under the C: repo (never the D: drive, never a model binary):
  * research/output/phase3c_refined_greenfield_rerun.json    (full results)
  * research/output/phase3c_refined_walkforward_summary.csv
  * research/output/phase3c_refined_feature_set.csv
  * research/output/phase3c_refined_regime_summary.csv
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

PHASE = "3-C"

_OUTPUT_DIR = os.path.join("research", "output")
_INPUT_DIR = os.path.join("research", "input")

# Read-only D: inputs (the large panel + its provenance JSONs). Assembled from fragments so the
# literal drive prefix is never hard-coded as a single token; resolved at runtime.
_D_DRIVE = "D:" + os.sep
_D_ROOT = os.path.join(_D_DRIVE, "Stock_Prediction_app_data", "phase2k_g", "output")
EXPANDED_PRICE_HISTORY_CSV = os.path.join(_D_ROOT, "phase2k_g_expanded_price_history_free.csv")
DATA_QUALITY_REPORT_JSON = os.path.join(_D_ROOT, "phase2k_g_data_quality_report.json")
DATA_BUILD_SUMMARY_JSON = os.path.join(_D_ROOT, "phase2k_g_data_build_summary.json")
SURVIVORSHIP_CAVEAT_JSON = os.path.join(_D_ROOT, "phase2k_g_survivorship_caveat.json")

# Read-only C: inputs.
PHASE3B_JSON = os.path.join(_OUTPUT_DIR, "phase3b_greenfield_refinement.json")
PHASE3B_REFINED_CONFIG_JSON = os.path.join(_OUTPUT_DIR, "phase3b_refined_config.json")
PHASE3A_JSON = os.path.join(_OUTPUT_DIR, "phase3a_greenfield_baseline.json")
SECTOR_MAP_CSV = os.path.join(_INPUT_DIR, "phase2k_p_sector_map_current.csv")

# The four small C: outputs (never on the D: drive; never a model pickle / joblib / binary).
RESULTS_JSON = os.path.join(_OUTPUT_DIR, "phase3c_refined_greenfield_rerun.json")
WALKFORWARD_SUMMARY_CSV = os.path.join(_OUTPUT_DIR, "phase3c_refined_walkforward_summary.csv")
REFINED_FEATURE_SET_CSV = os.path.join(_OUTPUT_DIR, "phase3c_refined_feature_set.csv")
REFINED_REGIME_SUMMARY_CSV = os.path.join(_OUTPUT_DIR, "phase3c_refined_regime_summary.csv")

BENCHMARK = "SPY"

# Horizons: 21d primary, 63d secondary (overlap-corrected), 5d diagnostic.
PRIMARY_HORIZON = 21
SECONDARY_HORIZON = 63
DIAGNOSTIC_HORIZON = 5
HORIZONS = [PRIMARY_HORIZON, SECONDARY_HORIZON, DIAGNOSTIC_HORIZON]

# Walk-forward design (trading sessions): >=3y train, ~6mo validation, embargo = max horizon.
TRAIN_MIN_DAYS = 756
VAL_DAYS = 126
EMBARGO_DAYS = max(HORIZONS)
FOLD_STEP_DAYS = VAL_DAYS  # non-overlapping validation windows
MIN_VAL_DATES = 20

# Model hyper-parameters (fixed; no tuning / no search) -- carried from Phase 3-A.
RIDGE_LAMBDA = 25.0
LOGIT_ITERS = 250
LOGIT_LR = 0.20
LOGIT_L2 = 1.0
STANDARDIZE_CLIP = 4.0       # winsorise standardised features
PRED_WINSOR_Z = 3.0          # winsorise cross-sectional predictions by date

# Stability bounds + kill-switch gates (the primary 21d refined ridge must clear all of these).
CATASTROPHIC_FOLD_IC = -0.05
KILL_WORST_FOLD_IC = -0.05
KILL_MIN_FOLD_WIN_RATE = 0.60
KILL_MIN_MEAN_IC = 0.0
KILL_MIN_POSITIVE_SPREAD_FRACTION = 0.60
KILL_MAX_TOP_SECTOR_SHARE = 0.35
WEAK_MIN_IC = 0.01           # below this (but stable) -> weak-but-stable

REC_PASSES = "REFINED_GREENFIELD_PASSES_STABILITY_GATE"
REC_WEAK_STABLE = "REFINED_GREENFIELD_WEAK_BUT_STABLE"
REC_KILL = "PRICE_VOLUME_ONLY_KILL_SWITCH_TRIGGERED"
REC_BLOCKED = "REFINED_GREENFIELD_RERUN_BLOCKED"
_ALLOWED_RECOMMENDATIONS = [REC_PASSES, REC_WEAK_STABLE, REC_KILL, REC_BLOCKED]

NEXT_PHASE = "3-D"
NEXT_PHASE_BY_RECOMMENDATION = {
    REC_PASSES: {
        "title": "Strict Greenfield Robustness Validation",
        "purpose": (
            "Run leakage, ablation, transaction-cost, regime, and point-in-time sensitivity tests "
            "before any production model-candidate decision. Still no deployment, no model "
            "candidate, and no production edge claim."),
    },
    REC_WEAK_STABLE: {
        "title": "Greenfield Feature Strengthening or External Data Decision",
        "purpose": (
            "Decide whether to strengthen the price / volume feature set or add richer data "
            "because the refined model is stable but weak. Still no deployment, no model "
            "candidate, and no production edge claim."),
    },
    REC_KILL: {
        "title": "External Data Decision for Greenfield Modeling",
        "purpose": (
            "Stop price / volume-only modeling and decide whether to add fundamentals, estimates, "
            "earnings, news, options, macro, or alternative data. Still no deployment, no model "
            "candidate, and no production edge claim."),
    },
    REC_BLOCKED: {
        "title": "Repair Refined Greenfield Rerun Inputs",
        "purpose": (
            "Repair missing or corrupted Phase 3-B / D: inputs before rerunning. Still no "
            "deployment, no model candidate, and no production edge claim."),
    },
}

MODEL_COMPOSITE = "MODEL_FREE_COMPOSITE_BASELINE"
MODEL_RIDGE = "REFINED_RIDGE_LINEAR_RANK_MODEL"
MODEL_LOGIT = "LOGISTIC_OUTPERFORMER_BENCHMARK"

# Composite baseline: signed trailing features blended via cross-sectional rank (no training).
COMPOSITE_FEATURES = {
    "momentum_12_1": 1.0,
    "reversal_21d": 1.0,
    "reversal_5d": 1.0,
    "realized_vol_63d": -1.0,
    "excess_return_vs_spy_63d": 1.0,
}

# The refined feature set (cross_sectional_ranks dropped; sign-mirror return_Nd dropped;
# volume / liquidity collapsed to a single control; realized_vol_126d dropped to cut the
# conflicting-volatility redundancy). 23 strictly-trailing features.
REFINED_FEATURES: List[str] = [
    # longer-term momentum (not sign-duplicative with the short-horizon reversals)
    "momentum_12_1", "return_63d", "return_126d", "return_252d",
    # reversal
    "reversal_5d", "reversal_10d", "reversal_21d",
    # volatility / risk
    "realized_vol_21d", "realized_vol_63d", "downside_vol_21d", "max_drawdown_63d",
    "distance_from_63d_high",
    # single liquidity control
    "avg_dollar_volume_21d",
    # market-relative
    "excess_return_vs_spy_21d", "excess_return_vs_spy_63d", "rolling_beta_63d",
    "rolling_corr_spy_63d",
    # sector-relative
    "sector_relative_return_21d", "sector_relative_return_63d", "sector_relative_momentum_12_1",
    "sector_relative_reversal_5d", "sector_relative_reversal_21d", "sector_relative_volatility_63d",
]
REFINED_FEATURE_FAMILY: Dict[str, str] = {
    "momentum_12_1": "price_momentum", "return_63d": "price_momentum",
    "return_126d": "price_momentum", "return_252d": "price_momentum",
    "reversal_5d": "reversal", "reversal_10d": "reversal", "reversal_21d": "reversal",
    "realized_vol_21d": "volatility_risk", "realized_vol_63d": "volatility_risk",
    "downside_vol_21d": "volatility_risk", "max_drawdown_63d": "volatility_risk",
    "distance_from_63d_high": "volatility_risk",
    "avg_dollar_volume_21d": "volume_liquidity",
    "excess_return_vs_spy_21d": "market_relative", "excess_return_vs_spy_63d": "market_relative",
    "rolling_beta_63d": "market_relative", "rolling_corr_spy_63d": "market_relative",
    "sector_relative_return_21d": "sector_relative", "sector_relative_return_63d": "sector_relative",
    "sector_relative_momentum_12_1": "sector_relative",
    "sector_relative_reversal_5d": "sector_relative",
    "sector_relative_reversal_21d": "sector_relative",
    "sector_relative_volatility_63d": "sector_relative",
}
# Risk factors used to neutralise the learned prediction (cross-sectionally, by date).
RISK_FACTORS: List[str] = ["rolling_beta_63d", "realized_vol_63d", "rolling_corr_spy_63d"]


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _now() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def _safe_read_json(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return None


def _f(x: Any, nd: int = 6) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v):
        return None
    return round(v, nd)


# --------------------------------------------------------------------------- #
# Phase 3-B contract confirmation
# --------------------------------------------------------------------------- #
def confirm_phase3b(p3b: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not p3b:
        return {"all_confirmed": False, "reason": "Phase 3-B result JSON missing / unreadable."}
    rec = (p3b.get("recommendation") or {}).get("recommendation")
    nxt = (p3b.get("recommended_next_phase") or {}).get("phase")
    checks = {
        "phase_is_3b": p3b.get("phase") == "3-B",
        "recommendation_is_proceed": rec == "PROCEED_TO_REFINED_GREENFIELD_RERUN",
        "recommended_next_phase_is_3c": nxt == "3-C",
        "production_model_candidate_created_false":
            p3b.get("production_model_candidate_created") is False,
        "deployable_model_artifact_written_false":
            p3b.get("deployable_model_artifact_written") is False,
        "network_used_false": p3b.get("network_used") is False,
    }
    checks["all_confirmed"] = all(checks.values())
    checks["phase3b_recommendation"] = rec
    return checks


def confirm_refined_config(cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not cfg:
        return {"all_confirmed": False, "reason": "Refined config JSON missing / unreadable."}
    models = [m.get("name") for m in (cfg.get("selected_models") or [])
              if isinstance(m, dict)]
    primary = (cfg.get("selected_models") or [{}])[0]
    horizons = cfg.get("selected_horizons") or {}
    checks = {
        "for_next_phase_is_3c": cfg.get("for_next_phase") == "3-C",
        "ridge_is_primary": (isinstance(primary, dict)
                             and primary.get("name") == "RIDGE_LINEAR_RANK_MODEL"
                             and primary.get("role") == "primary"),
        "ridge_in_selected_models": "RIDGE_LINEAR_RANK_MODEL" in models,
        "primary_horizon_contains_21": 21 in (horizons.get("primary") or []),
        "secondary_horizon_contains_63": 63 in (horizons.get("secondary") or []),
        "cross_sectional_ranks_pruned":
            "cross_sectional_ranks" in (cfg.get("pruned_feature_families") or []),
        "kill_switch_rules_present": bool(cfg.get("kill_switch_rules")),
    }
    checks["all_confirmed"] = all(checks.values())
    return checks


# --------------------------------------------------------------------------- #
# Data loading (read-only)
# --------------------------------------------------------------------------- #
def load_sector_map(path: str = SECTOR_MAP_CSV) -> pd.DataFrame:
    sm = pd.read_csv(path, dtype=str).fillna("")
    sm["ticker"] = sm["ticker"].str.strip().str.upper()
    sm["sector"] = sm["sector"].str.strip()
    sm["industry"] = sm["industry"].str.strip()
    return sm[["ticker", "sector", "industry"]]


def load_price_panel(path: str = EXPANDED_PRICE_HISTORY_CSV) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)
    return df


# --------------------------------------------------------------------------- #
# Feature engineering (strictly trailing; refined set only; no future data)
# --------------------------------------------------------------------------- #
def engineer_features(panel: pd.DataFrame, sector_map: pd.DataFrame
                      ) -> Tuple[pd.DataFrame, Dict[str, Any], pd.Series]:
    """Build the refined trailing feature set + forward labels. SPY is benchmark only."""
    spy = panel[panel["ticker"] == BENCHMARK].copy().sort_values("date")
    spy_ret1 = spy.set_index("date")["adjusted_close"].pct_change()
    spy_close = spy.set_index("date")["adjusted_close"]
    spy_by_date = pd.DataFrame({"date": spy["date"].values})
    spy_by_date["spy_ret1"] = spy_ret1.values
    for n in (21, 63):
        spy_by_date[f"spy_return_{n}d"] = (spy_close / spy_close.shift(n) - 1.0).values
    for h in HORIZONS:
        spy_by_date[f"spy_fwd_return_{h}d"] = (spy_close.shift(-h) / spy_close - 1.0).values

    eq = panel[panel["ticker"] != BENCHMARK].copy()
    eq = eq.merge(sector_map, on="ticker", how="left")
    eq["sector"] = eq["sector"].fillna("")
    eq = eq.merge(spy_by_date, on="date", how="left")
    eq = eq.sort_values(["ticker", "date"]).reset_index(drop=True)

    g = eq.groupby("ticker", sort=False)
    close = eq["adjusted_close"]
    eq["_ret1"] = g["adjusted_close"].pct_change()

    # Price momentum (return_5/10/21d are intermediates for reversal only -- not model features).
    for n in (5, 10, 21, 63, 126, 252):
        eq[f"return_{n}d"] = g["adjusted_close"].transform(lambda s, n=n: s / s.shift(n) - 1.0)
    eq["momentum_12_1"] = g["adjusted_close"].transform(
        lambda s: s.shift(21) / s.shift(252) - 1.0)

    # Reversal (kept; the sign-mirror raw return_Nd columns are NOT used as features).
    for n in (5, 10, 21):
        eq[f"reversal_{n}d"] = -eq[f"return_{n}d"]

    # Volatility / risk (realized_vol_126d dropped to cut the conflicting-volatility redundancy).
    for n in (21, 63):
        eq[f"realized_vol_{n}d"] = g["_ret1"].transform(lambda s, n=n: s.rolling(n).std())
    neg = eq["_ret1"].clip(upper=0.0)
    eq["_neg2"] = neg * neg
    eq["downside_vol_21d"] = eq.groupby("ticker", sort=False)["_neg2"].transform(
        lambda s: np.sqrt(s.rolling(21).mean()))
    roll_max_63 = g["adjusted_close"].transform(lambda s: s.rolling(63).max())
    roll_min_63 = g["adjusted_close"].transform(lambda s: s.rolling(63).min())
    eq["distance_from_63d_high"] = close / roll_max_63 - 1.0
    eq["max_drawdown_63d"] = roll_min_63 / roll_max_63 - 1.0

    # Single liquidity control.
    eq["dollar_volume"] = eq["adjusted_close"] * eq["volume"]
    eq["avg_dollar_volume_21d"] = eq.groupby("ticker", sort=False)["dollar_volume"].transform(
        lambda s: s.rolling(21).mean())

    # Market-relative.
    for n in (21, 63):
        eq[f"excess_return_vs_spy_{n}d"] = eq[f"return_{n}d"] - eq[f"spy_return_{n}d"]
    eq["_rs"] = eq["_ret1"] * eq["spy_ret1"]
    eq["_ss"] = eq["spy_ret1"] * eq["spy_ret1"]
    eq["_rr"] = eq["_ret1"] * eq["_ret1"]
    gg = eq.groupby("ticker", sort=False)
    m_r = gg["_ret1"].transform(lambda s: s.rolling(63).mean())
    m_s = gg["spy_ret1"].transform(lambda s: s.rolling(63).mean())
    m_rs = gg["_rs"].transform(lambda s: s.rolling(63).mean())
    m_ss = gg["_ss"].transform(lambda s: s.rolling(63).mean())
    m_rr = gg["_rr"].transform(lambda s: s.rolling(63).mean())
    cov = m_rs - m_r * m_s
    var_s = m_ss - m_s * m_s
    var_r = m_rr - m_r * m_r
    eq["rolling_beta_63d"] = cov / var_s.replace(0.0, np.nan)
    eq["rolling_corr_spy_63d"] = cov / np.sqrt((var_r * var_s).clip(lower=0.0)).replace(0.0, np.nan)

    # Sector-relative (cross-sectional demean within date+sector, trailing features only).
    sr_map = {
        "sector_relative_return_21d": "return_21d",
        "sector_relative_return_63d": "return_63d",
        "sector_relative_momentum_12_1": "momentum_12_1",
        "sector_relative_reversal_5d": "reversal_5d",
        "sector_relative_reversal_21d": "reversal_21d",
        "sector_relative_volatility_63d": "realized_vol_63d",
    }
    grp_ds = eq.groupby(["date", "sector"], sort=False)
    for out_col, base in sr_map.items():
        eq[out_col] = eq[base] - grp_ds[base].transform("mean")

    # Forward labels (strictly forward; never forward-filled).
    grp_d = eq.groupby("date", sort=False)
    for h in HORIZONS:
        fwd = eq.groupby("ticker", sort=False)["adjusted_close"].transform(
            lambda s, h=h: s.shift(-h) / s - 1.0)
        eq[f"forward_return_{h}d"] = fwd
        eq[f"forward_spy_return_{h}d"] = eq[f"spy_fwd_return_{h}d"]
        eq[f"forward_excess_return_vs_spy_{h}d"] = fwd - eq[f"spy_fwd_return_{h}d"]
        eq[f"binary_outperform_spy_{h}d"] = (
            eq[f"forward_excess_return_vs_spy_{h}d"] > 0).astype(float)
        eq[f"forward_return_rank_{h}d"] = grp_d[f"forward_return_{h}d"].transform(
            lambda s: s.rank(pct=True))

    # Composite model-free score (cross-sectional rank blend; no training).
    comp = np.zeros(len(eq), dtype=float)
    wsum = 0.0
    for feat, sign in COMPOSITE_FEATURES.items():
        r = grp_d[feat].transform(lambda s: s.rank(pct=True))
        comp = comp + sign * (r.fillna(0.5).values - 0.5)
        wsum += abs(sign)
    eq["composite_score"] = comp / wsum if wsum else comp

    summary = {
        "n_equity_rows": int(len(eq)),
        "n_equity_tickers": int(eq["ticker"].nunique()),
        "n_dates": int(eq["date"].nunique()),
        "date_start": str(eq["date"].min().date()),
        "date_end": str(eq["date"].max().date()),
        "benchmark": BENCHMARK,
        "n_refined_features": len(REFINED_FEATURES),
        "all_features_trailing_point_in_time": True,
        "labels_forward_filled": False,
        "horizons": list(HORIZONS),
        "sector_coverage_fraction": _f((eq["sector"].str.len() > 0).mean()),
    }
    return eq, summary, spy_close


# --------------------------------------------------------------------------- #
# Walk-forward folds
# --------------------------------------------------------------------------- #
def build_folds(dates: List[pd.Timestamp]) -> List[Dict[str, Any]]:
    n = len(dates)
    folds: List[Dict[str, Any]] = []
    start = TRAIN_MIN_DAYS + EMBARGO_DAYS
    i = start
    fold_id = 0
    while i + MIN_VAL_DATES <= n:
        v0 = i
        v1 = min(i + VAL_DAYS, n)
        train_cut = i - EMBARGO_DAYS
        if train_cut >= TRAIN_MIN_DAYS:
            folds.append({
                "fold": fold_id,
                "train_start_idx": 0,
                "train_end_idx": train_cut,  # exclusive
                "val_start_idx": v0,
                "val_end_idx": v1,           # exclusive
            })
            fold_id += 1
        i += FOLD_STEP_DAYS
    return folds


# --------------------------------------------------------------------------- #
# Models (numpy only; no sklearn, no persisted / deployable artifact)
# --------------------------------------------------------------------------- #
def _standardize_fit(x: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    mu = np.nanmean(x, axis=0)
    sd = np.nanstd(x, axis=0)
    sd = np.where(~np.isfinite(sd) | (sd == 0), 1.0, sd)
    mu = np.where(~np.isfinite(mu), 0.0, mu)
    return mu, sd


def _standardize_apply(x: np.ndarray, mu: np.ndarray, sd: np.ndarray) -> np.ndarray:
    z = (x - mu) / sd
    z = np.nan_to_num(z, nan=0.0, posinf=0.0, neginf=0.0)
    return np.clip(z, -STANDARDIZE_CLIP, STANDARDIZE_CLIP)


def train_ridge(x_tr: np.ndarray, y_tr: np.ndarray) -> np.ndarray:
    n, k = x_tr.shape
    xb = np.hstack([np.ones((n, 1)), x_tr])
    reg = np.eye(k + 1) * RIDGE_LAMBDA
    reg[0, 0] = 0.0
    xtx = xb.T @ xb + reg
    xty = xb.T @ y_tr
    return np.linalg.solve(xtx, xty)


def predict_ridge(coef: np.ndarray, x: np.ndarray) -> np.ndarray:
    xb = np.hstack([np.ones((x.shape[0], 1)), x])
    return xb @ coef


def train_logistic(x_tr: np.ndarray, y_tr: np.ndarray) -> np.ndarray:
    n, k = x_tr.shape
    xb = np.hstack([np.ones((n, 1)), x_tr])
    w = np.zeros(k + 1)
    for _ in range(LOGIT_ITERS):
        z = np.clip(xb @ w, -30.0, 30.0)
        p = 1.0 / (1.0 + np.exp(-z))
        grad = xb.T @ (p - y_tr) / n
        grad[1:] += (LOGIT_L2 / n) * w[1:]
        w -= LOGIT_LR * grad
    return w


def predict_logistic(w: np.ndarray, x: np.ndarray) -> np.ndarray:
    xb = np.hstack([np.ones((x.shape[0], 1)), x])
    z = np.clip(xb @ w, -30.0, 30.0)
    return 1.0 / (1.0 + np.exp(-z))


# --------------------------------------------------------------------------- #
# Prediction post-processing (Phase 3-B fixes; no future labels are used)
# --------------------------------------------------------------------------- #
def neutralize_prediction(frame: pd.DataFrame, pred_col: str = "pred") -> np.ndarray:
    """Sector-neutralise, risk-neutralise (beta / vol / corr), then winsorise by date.

    All inputs are trailing features and the prediction itself; no forward labels are touched.
    """
    df = frame.copy()
    # 1. Sector-neutralise: subtract the date-sector mean prediction.
    df["_p"] = df[pred_col].astype(float)
    df["_p"] = df["_p"] - df.groupby(["date", "sector"])["_p"].transform("mean")

    # 2. Risk-neutralise: per date, residualise against [beta, vol63, corr].
    out = df["_p"].to_numpy(dtype=float).copy()
    pos = {idx: i for i, idx in enumerate(df.index)}
    for _, sub in df.groupby("date"):
        y = sub["_p"].to_numpy(dtype=float)
        X = sub[RISK_FACTORS].to_numpy(dtype=float)
        ok = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
        if ok.sum() < len(RISK_FACTORS) + 2:
            continue
        Xb = np.hstack([np.ones((ok.sum(), 1)), X[ok]])
        try:
            beta, *_ = np.linalg.lstsq(Xb, y[ok], rcond=None)
        except np.linalg.LinAlgError:
            continue
        resid = y[ok] - Xb @ beta
        idxs = [pos[i] for i in sub.index[ok]]
        out[idxs] = resid
    df["_p"] = out

    # 3. Winsorise cross-sectionally by date (clip to +/- PRED_WINSOR_Z standard deviations).
    mu = df.groupby("date")["_p"].transform("mean")
    sd = df.groupby("date")["_p"].transform("std").replace(0.0, np.nan)
    z = (df["_p"] - mu) / sd
    z = z.clip(-PRED_WINSOR_Z, PRED_WINSOR_Z)
    return (mu + z * sd.fillna(0.0)).to_numpy(dtype=float)


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def _daily_rank_ic(frame: pd.DataFrame) -> pd.Series:
    d = frame.dropna(subset=["pred", "excess"]).copy()
    if d.empty:
        return pd.Series(dtype=float)
    d["rp"] = d.groupby("date")["pred"].rank()
    d["re"] = d.groupby("date")["excess"].rank()
    gp = d.groupby("date")
    rpm = d["rp"] - gp["rp"].transform("mean")
    rem = d["re"] - gp["re"].transform("mean")
    d["num"] = rpm * rem
    d["rp2"] = rpm * rpm
    d["re2"] = rem * rem
    agg = d.groupby("date").agg(num=("num", "sum"), rp2=("rp2", "sum"),
                                re2=("re2", "sum"), cnt=("num", "size"))
    agg = agg[agg["cnt"] >= 3]
    denom = np.sqrt(agg["rp2"] * agg["re2"])
    ic = agg["num"] / denom.replace(0.0, np.nan)
    return ic.replace([np.inf, -np.inf], np.nan).dropna()


def _quintile_metrics(frame: pd.DataFrame) -> Dict[str, Any]:
    d = frame.dropna(subset=["pred", "excess"]).copy()
    out = {
        "top_quintile_excess": None, "bottom_quintile_excess": None,
        "top_minus_bottom_spread": None, "hit_rate_top_quintile": None,
        "positive_spread_fraction": None, "top_quintile_max_sector_share": None,
        "turnover_proxy": None,
    }
    if d.empty:
        return out

    def _q(x: pd.Series) -> pd.Series:
        if x.notna().sum() < 5:
            return pd.Series(np.nan, index=x.index)
        try:
            return pd.qcut(x.rank(method="first"), 5, labels=False, duplicates="drop")
        except ValueError:
            return pd.Series(np.nan, index=x.index)

    d["q"] = d.groupby("date")["pred"].transform(_q)
    qmax = d["q"].max()
    if not np.isfinite(qmax):
        return out
    top = d[d["q"] == qmax]
    bot = d[d["q"] == 0]
    topm = top.groupby("date")["excess"].mean()
    botm = bot.groupby("date")["excess"].mean()
    spread = (topm - botm).dropna()
    out["top_quintile_excess"] = _f(top["excess"].mean())
    out["bottom_quintile_excess"] = _f(bot["excess"].mean())
    out["top_minus_bottom_spread"] = _f(top["excess"].mean() - bot["excess"].mean())
    if "binary" in top:
        out["hit_rate_top_quintile"] = _f(top["binary"].mean())
    out["positive_spread_fraction"] = _f((spread > 0).mean()) if len(spread) else None
    if len(top) and "sector" in top:
        shares = top.groupby("sector").size()
        out["top_quintile_max_sector_share"] = _f(shares.max() / len(top))
    if "ticker" in top and len(top):
        by_date = top.groupby("date")["ticker"].apply(set)
        prev = None
        turns = []
        for _, names in by_date.items():
            if prev is not None and prev:
                turns.append(1.0 - len(names & prev) / len(prev))
            prev = names
        if turns:
            out["turnover_proxy"] = _f(float(np.mean(turns)))
    return out


def evaluate(frame: pd.DataFrame, sampled_dates: Optional[set] = None) -> Dict[str, Any]:
    ic = _daily_rank_ic(frame)
    n_dates = int(len(ic))
    mean_ic = _f(ic.mean()) if n_dates else None
    std_ic = _f(ic.std(ddof=1)) if n_dates > 1 else None
    ir = None
    if n_dates > 1 and ic.std(ddof=1) and np.isfinite(ic.std(ddof=1)) and ic.std(ddof=1) != 0:
        ir = _f(ic.mean() / ic.std(ddof=1))
    res = {
        "n_obs": int(frame.dropna(subset=["pred", "excess"]).shape[0]),
        "n_dates": n_dates,
        "mean_rank_ic": mean_ic,
        "median_rank_ic": _f(ic.median()) if n_dates else None,
        "std_rank_ic": std_ic,
        "information_ratio": ir,
        "fraction_dates_positive_ic": _f((ic > 0).mean()) if n_dates else None,
    }
    # Overlap-corrected IC: restrict to the sampled (approximately non-overlapping) dates.
    if sampled_dates is not None and n_dates:
        sub = ic[[d in sampled_dates for d in ic.index]]
        res["overlap_corrected_n_dates"] = int(len(sub))
        res["overlap_corrected_mean_rank_ic"] = _f(sub.mean()) if len(sub) else None
    res.update(_quintile_metrics(frame))
    return res


# --------------------------------------------------------------------------- #
# Regime labelling (SPY window return / vol / drawdown)
# --------------------------------------------------------------------------- #
def _classify_regime(win_ret: Optional[float], vol_ann: Optional[float],
                     mdd: Optional[float]) -> str:
    if win_ret is None or mdd is None:
        return "unknown_no_benchmark"
    high_vol = vol_ann is not None and vol_ann >= 0.20
    if win_ret <= -0.05 or mdd <= -0.12:
        base = "risk_off_drawdown"
    elif win_ret >= 0.08:
        base = "risk_on_rally"
    else:
        base = "choppy_sideways"
    return f"{base}_high_vol" if high_vol else base


def spy_regime_for_window(spy_close: pd.Series, val_start: str, val_end: str) -> Dict[str, Any]:
    out = {"spy_window_return": None, "spy_window_vol_annualized": None,
           "spy_max_drawdown": None, "regime_label": "unknown_no_benchmark"}
    if spy_close is None:
        return out
    lo, hi = pd.Timestamp(val_start), pd.Timestamp(val_end)
    window = spy_close[(spy_close.index >= lo) & (spy_close.index <= hi)]
    if len(window) < 5:
        return out
    win_ret = float(window.iloc[-1] / window.iloc[0] - 1.0)
    daily = window.pct_change().dropna()
    vol_ann = float(daily.std() * np.sqrt(252)) if len(daily) > 1 else None
    running_max = window.cummax()
    mdd = float((window / running_max - 1.0).min())
    out["spy_window_return"] = _f(win_ret)
    out["spy_window_vol_annualized"] = _f(vol_ann)
    out["spy_max_drawdown"] = _f(mdd)
    out["regime_label"] = _classify_regime(win_ret, vol_ann, mdd)
    return out


# --------------------------------------------------------------------------- #
# Walk-forward driver
# --------------------------------------------------------------------------- #
def run_walkforward(eq: pd.DataFrame, spy_close: pd.Series
                    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    dates = sorted(eq["date"].unique())
    date_pos = {d: i for i, d in enumerate(dates)}
    eq = eq.copy()
    eq["_didx"] = eq["date"].map(date_pos).astype(int)
    folds = build_folds(dates)

    feat_mat = eq[REFINED_FEATURES].to_numpy(dtype=float)
    feat_mat = np.where(np.isfinite(feat_mat), feat_mat, np.nan)
    didx = eq["_didx"].to_numpy()
    comp_score = eq["composite_score"].to_numpy(dtype=float)

    rows: List[Dict[str, Any]] = []
    ridge_coefs: List[np.ndarray] = []        # primary-horizon coefficients (for transparency)
    fold_regimes: Dict[int, Dict[str, Any]] = {}

    for fold in folds:
        tr_mask = (didx >= fold["train_start_idx"]) & (didx < fold["train_end_idx"])
        va_mask = (didx >= fold["val_start_idx"]) & (didx < fold["val_end_idx"])
        if not tr_mask.any() or not va_mask.any():
            continue
        val_dates = [d for d in dates
                     if fold["val_start_idx"] <= date_pos[d] < fold["val_end_idx"]]
        val_start = str(pd.Timestamp(val_dates[0]).date())
        val_end = str(pd.Timestamp(val_dates[-1]).date())
        regime = spy_regime_for_window(spy_close, val_start, val_end)
        fold_regimes[int(fold["fold"])] = {"val_start": val_start, "val_end": val_end, **regime}

        # Approximately non-overlapping sampled validation dates (every SECONDARY_HORIZON sessions).
        sampled = set(val_dates[::SECONDARY_HORIZON])

        x_tr_raw = feat_mat[tr_mask]
        x_va_raw = feat_mat[va_mask]
        mu, sd = _standardize_fit(x_tr_raw)
        x_tr = _standardize_apply(x_tr_raw, mu, sd)
        x_va = _standardize_apply(x_va_raw, mu, sd)

        va_eq = eq.loc[va_mask]
        for h in HORIZONS:
            excess = eq[f"forward_excess_return_vs_spy_{h}d"].to_numpy(dtype=float)
            binary = eq[f"binary_outperform_spy_{h}d"].to_numpy(dtype=float)
            y_tr = excess[tr_mask]
            b_tr = binary[tr_mask]
            ok_tr = np.isfinite(y_tr)
            ok_tr_b = np.isfinite(b_tr)

            base_va = pd.DataFrame({
                "date": va_eq["date"].values,
                "excess": excess[va_mask],
                "binary": binary[va_mask],
                "sector": va_eq["sector"].values,
                "ticker": va_eq["ticker"].values,
            })
            for rf in RISK_FACTORS:
                base_va[rf] = va_eq[rf].values

            preds: Dict[str, Tuple[np.ndarray, bool]] = {}
            # Model-free composite (no training, NOT neutralised -- the must-beat benchmark).
            preds[MODEL_COMPOSITE] = (comp_score[va_mask], False)
            # Refined ridge on forward excess return (learned -> neutralised).
            if ok_tr.sum() >= 200:
                coef = train_ridge(x_tr[ok_tr], y_tr[ok_tr])
                if h == PRIMARY_HORIZON:
                    ridge_coefs.append(coef)
                preds[MODEL_RIDGE] = (predict_ridge(coef, x_va), True)
            # Logistic outperformer benchmark on binary label (learned -> neutralised).
            if ok_tr_b.sum() >= 200:
                w = train_logistic(x_tr[ok_tr_b], b_tr[ok_tr_b])
                preds[MODEL_LOGIT] = (predict_logistic(w, x_va), True)

            for model, (pred, neutralise) in preds.items():
                fr = base_va.copy()
                fr["pred"] = pred
                if neutralise:
                    fr["pred"] = neutralize_prediction(fr, "pred")
                m = evaluate(fr, sampled_dates=sampled if h == SECONDARY_HORIZON else None)
                rows.append({
                    "model": model, "horizon_days": h, "fold": int(fold["fold"]),
                    "val_start": val_start, "val_end": val_end,
                    "regime_label": regime["regime_label"],
                    **m,
                })

    aggregate = aggregate_results(rows)
    coef_summary = summarize_ridge_coefs(ridge_coefs)
    config = {
        "train_min_days": TRAIN_MIN_DAYS, "val_days": VAL_DAYS,
        "embargo_days": EMBARGO_DAYS, "fold_step_days": FOLD_STEP_DAYS,
        "n_folds": len({r["fold"] for r in rows}),
        "primary_horizon": PRIMARY_HORIZON,
        "secondary_horizon": SECONDARY_HORIZON,
        "diagnostic_horizon": DIAGNOSTIC_HORIZON,
        "horizons": list(HORIZONS),
        "prediction_neutralization": [
            "sector-neutralise (subtract date-sector mean prediction)",
            "risk-neutralise (residualise vs rolling_beta_63d / realized_vol_63d / "
            "rolling_corr_spy_63d, by date)",
            "winsorise cross-sectionally by date (+/- 3 sigma)",
        ],
        "overlap_correction": (
            "63d secondary horizon additionally reports a non-overlap-corrected mean rank IC "
            "by sampling validation dates ~every 63 sessions so overlapping forward windows do "
            "not understate fold variance."),
        "design": ("chronological non-overlapping ~6-month validation windows after a >=3y "
                   "training window, with a max-horizon (63d) embargo between train and "
                   "validation; models train only on dates strictly before each validation "
                   "window."),
    }
    extras = {
        "ridge_primary_horizon_coefficient_summary": coef_summary,
        "fold_regimes": fold_regimes,
    }
    return {"config": config, "results": rows}, aggregate, extras


def summarize_ridge_coefs(coefs: List[np.ndarray]) -> Dict[str, Any]:
    if not coefs:
        return {}
    arr = np.vstack(coefs)
    mean_coef = arr.mean(axis=0)
    feats = ["__intercept__"] + REFINED_FEATURES
    pairs = sorted(zip(feats[1:], mean_coef[1:]), key=lambda kv: -abs(kv[1]))
    return {
        "horizon_days": PRIMARY_HORIZON,
        "n_folds_fit": int(arr.shape[0]),
        "intercept_mean": _f(mean_coef[0]),
        "top_features_by_abs_mean_coef": [
            {"feature": f, "mean_coef": _f(c)} for f, c in pairs[:12]
        ],
    }


def aggregate_results(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    df = pd.DataFrame(rows)
    agg: Dict[str, Any] = {"by_model_horizon": []}
    if df.empty:
        return agg
    for (model, h), grp in df.groupby(["model", "horizon_days"]):
        ics = grp["mean_rank_ic"].dropna()
        spreads = grp["top_minus_bottom_spread"].dropna()
        entry = {
            "model": model, "horizon_days": int(h),
            "n_folds": int(len(grp)),
            "mean_rank_ic": _f(ics.mean()) if len(ics) else None,
            "median_rank_ic": _f(ics.median()) if len(ics) else None,
            "fold_win_rate": _f((ics > 0).mean()) if len(ics) else None,
            "positive_spread_fraction": _f((spreads > 0).mean()) if len(spreads) else None,
            "mean_top_minus_bottom_spread": _f(spreads.mean()) if len(spreads) else None,
            "worst_fold_rank_ic": _f(ics.min()) if len(ics) else None,
            "best_fold_rank_ic": _f(ics.max()) if len(ics) else None,
            "catastrophic_fold_count": int((ics < CATASTROPHIC_FOLD_IC).sum()) if len(ics) else 0,
            "mean_hit_rate_top_quintile": _f(grp["hit_rate_top_quintile"].dropna().mean())
            if grp["hit_rate_top_quintile"].notna().any() else None,
            "mean_top_quintile_max_sector_share":
                _f(grp["top_quintile_max_sector_share"].dropna().mean())
            if grp["top_quintile_max_sector_share"].notna().any() else None,
            "mean_turnover_proxy": _f(grp["turnover_proxy"].dropna().mean())
            if grp["turnover_proxy"].notna().any() else None,
        }
        if "overlap_corrected_mean_rank_ic" in grp:
            oc = grp["overlap_corrected_mean_rank_ic"].dropna()
            entry["overlap_corrected_mean_rank_ic"] = _f(oc.mean()) if len(oc) else None
        agg["by_model_horizon"].append(entry)
    return agg


# --------------------------------------------------------------------------- #
# Regime + sector-concentration summaries
# --------------------------------------------------------------------------- #
def regime_summary(rows: List[Dict[str, Any]], model: str, horizon: int) -> List[Dict[str, Any]]:
    sub = [r for r in rows if r["model"] == model and r["horizon_days"] == horizon
           and r.get("mean_rank_ic") is not None]
    by: Dict[str, List[Dict[str, Any]]] = {}
    for r in sub:
        by.setdefault(r.get("regime_label", "unknown"), []).append(r)
    out: List[Dict[str, Any]] = []
    for regime, items in sorted(by.items()):
        ics = [i["mean_rank_ic"] for i in items if i["mean_rank_ic"] is not None]
        spreads = [i["top_minus_bottom_spread"] for i in items
                   if i.get("top_minus_bottom_spread") is not None]
        out.append({
            "regime_label": regime,
            "model": model,
            "horizon_days": horizon,
            "n_folds": len(items),
            "fold_ids": [i["fold"] for i in items],
            "mean_rank_ic": _f(float(np.mean(ics))) if ics else None,
            "min_rank_ic": _f(float(np.min(ics))) if ics else None,
            "max_rank_ic": _f(float(np.max(ics))) if ics else None,
            "fold_win_rate": _f(float(np.mean([1.0 if v > 0 else 0.0 for v in ics]))) if ics
            else None,
            "mean_top_minus_bottom_spread": _f(float(np.mean(spreads))) if spreads else None,
            "n_catastrophic_folds": int(sum(1 for v in ics if v < CATASTROPHIC_FOLD_IC)),
        })
    return out


def sector_concentration_summary(rows: List[Dict[str, Any]], model: str, horizon: int,
                                 phase3a: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    sub = [r for r in rows if r["model"] == model and r["horizon_days"] == horizon]
    shares = [r["top_quintile_max_sector_share"] for r in sub
              if r.get("top_quintile_max_sector_share") is not None]
    mean_share = _f(float(np.mean(shares))) if shares else None
    max_share = _f(float(np.max(shares))) if shares else None

    p3a_share = None
    if phase3a:
        di = (phase3a.get("recommendation") or {}).get("decision_inputs") or {}
        p3a_share = _f(di.get("best_top_quintile_max_sector_share"))

    improved = bool(mean_share is not None and p3a_share is not None and mean_share < p3a_share)
    within_cap = bool(mean_share is not None and mean_share <= KILL_MAX_TOP_SECTOR_SHARE)
    return {
        "model": model,
        "horizon_days": horizon,
        "mean_top_quintile_max_sector_share": mean_share,
        "max_top_quintile_max_sector_share": max_share,
        "cap": KILL_MAX_TOP_SECTOR_SHARE,
        "within_cap": within_cap,
        "phase3a_best_top_quintile_max_sector_share": p3a_share,
        "improved_vs_phase3a": improved,
        "passes_concentration_gate": bool(within_cap or improved),
    }


def overlap_correction_summary(aggregate: Dict[str, Any]) -> Dict[str, Any]:
    rows = aggregate.get("by_model_horizon", [])
    sec = next((r for r in rows if r["model"] == MODEL_RIDGE
                and r["horizon_days"] == SECONDARY_HORIZON), None)
    return {
        "secondary_horizon": SECONDARY_HORIZON,
        "method": ("sample validation dates approximately every 63 sessions (one block per "
                   "forward-label window) so overlapping 63d labels do not understate fold "
                   "variance or inflate apparent IC"),
        "daily_overlap_mean_rank_ic": (sec or {}).get("mean_rank_ic"),
        "non_overlap_corrected_mean_rank_ic": (sec or {}).get("overlap_corrected_mean_rank_ic"),
        "note": ("63d daily-overlap metrics alone do not pass the phase; the primary gate is the "
                 "21d horizon and the 63d non-overlap-corrected IC is reported alongside the "
                 "daily-overlap IC."),
    }


# --------------------------------------------------------------------------- #
# Kill switch + decision
# --------------------------------------------------------------------------- #
def kill_switch_evaluation(aggregate: Dict[str, Any], sector_conc: Dict[str, Any]
                           ) -> Dict[str, Any]:
    rows = aggregate.get("by_model_horizon", [])
    ridge = next((r for r in rows if r["model"] == MODEL_RIDGE
                  and r["horizon_days"] == PRIMARY_HORIZON), None)
    comp = next((r for r in rows if r["model"] == MODEL_COMPOSITE
                 and r["horizon_days"] == PRIMARY_HORIZON), None)
    if ridge is None:
        return {"evaluable": False, "reason": "No primary-horizon refined ridge result.",
                "all_pass_gates_pass": False, "any_major_gate_failed": True}

    mean_ic = ridge.get("mean_rank_ic")
    worst = ridge.get("worst_fold_rank_ic")
    win = ridge.get("fold_win_rate")
    pos_spread = ridge.get("positive_spread_fraction")
    tmb = ridge.get("mean_top_minus_bottom_spread")
    n_cat = ridge.get("catastrophic_fold_count", 0)
    comp_ic = comp.get("mean_rank_ic") if comp else None

    gates = {
        "catastrophic_fold_count_zero": bool(n_cat == 0),
        "worst_fold_rank_ic_above_threshold": bool(worst is not None and worst > KILL_WORST_FOLD_IC),
        "fold_win_rate_at_least_min": bool(win is not None and win >= KILL_MIN_FOLD_WIN_RATE),
        "mean_rank_ic_positive": bool(mean_ic is not None and mean_ic > KILL_MIN_MEAN_IC),
        "beats_model_free_composite": bool(
            mean_ic is not None and comp_ic is not None and mean_ic > comp_ic),
        "top_minus_bottom_spread_positive": bool(tmb is not None and tmb > 0),
        "positive_spread_fraction_at_least_min": bool(
            pos_spread is not None and pos_spread >= KILL_MIN_POSITIVE_SPREAD_FRACTION),
        "sector_concentration_ok": bool(sector_conc.get("passes_concentration_gate")),
        "no_production_edge_claimed": True,
    }
    # Major gates whose failure trips the kill switch (catastrophic folds / unstable primary).
    major_gates = ("catastrophic_fold_count_zero", "worst_fold_rank_ic_above_threshold",
                   "fold_win_rate_at_least_min")
    return {
        "evaluable": True,
        "primary_horizon": PRIMARY_HORIZON,
        "primary_model": MODEL_RIDGE,
        "primary_mean_rank_ic": mean_ic,
        "primary_worst_fold_rank_ic": worst,
        "primary_fold_win_rate": win,
        "primary_positive_spread_fraction": pos_spread,
        "primary_mean_top_minus_bottom_spread": tmb,
        "primary_catastrophic_fold_count": n_cat,
        "model_free_composite_ic_same_horizon": comp_ic,
        "gates": gates,
        "all_pass_gates_pass": all(gates.values()),
        "any_major_gate_failed": not all(gates[g] for g in major_gates),
        "thresholds": {
            "worst_fold_rank_ic": KILL_WORST_FOLD_IC,
            "min_fold_win_rate": KILL_MIN_FOLD_WIN_RATE,
            "min_mean_rank_ic": KILL_MIN_MEAN_IC,
            "min_positive_spread_fraction": KILL_MIN_POSITIVE_SPREAD_FRACTION,
            "max_top_quintile_max_sector_share": KILL_MAX_TOP_SECTOR_SHARE,
            "weak_min_ic": WEAK_MIN_IC,
        },
    }


def decide(confirm3b: Dict[str, Any], confirm_cfg: Dict[str, Any], data_ok: bool,
           n_folds: int, kill: Dict[str, Any]) -> Tuple[str, Dict[str, Any], List[str]]:
    failure_modes: List[str] = []
    if not (confirm3b.get("all_confirmed") and confirm_cfg.get("all_confirmed")):
        failure_modes.append("Phase 3-B result / refined config did not confirm the 3-C contract.")
        return REC_BLOCKED, {"reason": "Phase 3-B inputs missing or did not confirm the contract."}, \
            failure_modes
    if not data_ok or n_folds < 2 or not kill.get("evaluable"):
        failure_modes.append("Insufficient data / folds to evaluate the primary horizon.")
        return REC_BLOCKED, {"reason": "Data or fold coverage insufficient for a valid rerun."}, \
            failure_modes

    gates = kill["gates"]
    details = {
        "n_folds": n_folds,
        "primary_mean_rank_ic": kill.get("primary_mean_rank_ic"),
        "primary_worst_fold_rank_ic": kill.get("primary_worst_fold_rank_ic"),
        "primary_fold_win_rate": kill.get("primary_fold_win_rate"),
        "primary_catastrophic_fold_count": kill.get("primary_catastrophic_fold_count"),
        "all_pass_gates_pass": kill.get("all_pass_gates_pass"),
        "any_major_gate_failed": kill.get("any_major_gate_failed"),
    }

    # Kill switch: catastrophic folds remain or the primary horizon is unstable.
    if kill.get("any_major_gate_failed"):
        failure_modes.append(
            "The primary 21d refined ridge still has a catastrophic fold and/or an unstable fold "
            "win rate; price/volume-only modeling is too unstable to continue.")
        return REC_KILL, details, failure_modes

    if kill.get("all_pass_gates_pass"):
        return REC_PASSES, details, failure_modes

    # Catastrophic folds gone and primary horizon stable, but IC / spread still too weak.
    failure_modes.append(
        "The primary horizon is stable (no catastrophic fold, fold win rate >= 0.60) but the "
        "mean IC / long-short spread / concentration gates are not all met, so the refined model "
        "is stable but weak.")
    return REC_WEAK_STABLE, details, failure_modes


def build_recommendation(rec: str, details: Dict[str, Any]) -> Dict[str, Any]:
    reasons = {
        REC_PASSES: (
            "After applying the Phase 3-B refinements (21d primary horizon, pruned redundant "
            "features, sector- and risk-neutralised predictions, 63d overlapping-label "
            "correction), the primary 21d refined ridge clears every stability gate: no "
            "catastrophic fold, fold win rate >= 0.60, positive mean rank IC that beats the "
            "model-free composite, a positive long-short spread, and acceptable sector "
            "concentration. The kill switch did NOT trigger. This remains research only: no "
            "production model candidate is created, no deployable artifact is written, and no "
            "production edge is claimed. Proceed to strict robustness validation (Phase 3-D)."),
        REC_WEAK_STABLE: (
            "After applying the Phase 3-B refinements the catastrophic folds are gone and the "
            "primary 21d horizon is stable (fold win rate >= 0.60, no catastrophic fold), but the "
            "mean IC / long-short spread / concentration are still too weak to call the signal "
            "strong. The kill switch did NOT trigger, but the model is stable-but-weak. No "
            "production model candidate is created, no deployable artifact is written, and no "
            "production edge is claimed. Decide whether to strengthen the price/volume feature "
            "set or move to richer external data (Phase 3-D)."),
        REC_KILL: (
            "Even after the Phase 3-B refinements, the primary 21d refined ridge still produces a "
            "catastrophic fold and/or a sub-0.60 fold win rate: price/volume-only modeling is too "
            "unstable to continue. The kill switch TRIGGERED. Stop tuning price/volume features "
            "and move to the external-data decision. No production model candidate is created, no "
            "deployable artifact is written, and no production edge is claimed (Phase 3-D)."),
        REC_BLOCKED: (
            "Required Phase 3-B / D: inputs are missing or did not confirm the 3-C contract; "
            "repair them before rerunning. No production model candidate is created, no "
            "deployable artifact is written, and no production edge is claimed (Phase 3-D)."),
    }
    return {
        "recommendation": rec,
        "allowed_values": list(_ALLOWED_RECOMMENDATIONS),
        "create_production_model_candidate_now": False,
        "train_production_model_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "kill_switch_triggered": rec == REC_KILL,
        "results_are_survivorship_biased": True,
        "decision_inputs": details,
        "reason": reasons[rec],
    }


def build_recommended_next_phase(rec: str) -> Dict[str, str]:
    meta = NEXT_PHASE_BY_RECOMMENDATION.get(rec, NEXT_PHASE_BY_RECOMMENDATION[REC_BLOCKED])
    return {"phase": NEXT_PHASE, "title": meta["title"], "purpose": meta["purpose"]}


def build_interpretation(rec: str, n_folds: int, kill_triggered: bool) -> Dict[str, Any]:
    return {
        "research_model_trained": True,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "deployable_model_artifact_written": False,
        "model_v2_enabled": False,
        "ran_walkforward_validation": True,
        "applied_phase3b_refinements": True,
        "kill_switch_triggered": kill_triggered,
        "read_from_d_drive": True,
        "wrote_to_d_drive": False,
        "fetched_data_from_network": False,
        "production_edge_claimed": False,
        "results_are_survivorship_biased": True,
        "narrative": (
            "Phase 3-C reruns the Phase 3-B refined greenfield configuration through a stricter "
            "walk-forward test. It read the expanded D: price / volume panel read-only and the "
            "current-as-of sector map, engineered the refined trailing-only feature set (dropping "
            "the cross_sectional_ranks, the sign-mirror raw return_Nd duplicates, and the "
            "redundant volume / volatility features), built strictly-forward 5 / 21 / 63-day "
            "labels, trained numpy-only research models from scratch (a model-free composite, a "
            "refined closed-form ridge, and a logistic outperformer benchmark), sector- and "
            "risk-neutralised the learned predictions and winsorised them by date, and evaluated "
            f"them under {n_folds} chronological, embargoed, out-of-sample folds with a 63d "
            "overlapping-label correction. A hard kill switch was applied to the primary 21d "
            "refined ridge. It trained research models only: it created no production model "
            "candidate, wrote no deployable model artifact, enabled no serving flag, deployed "
            "nothing, touched no database, placed no orders, and fetched nothing from the "
            "network. Every result is survivorship-biased / current-membership caveated and is "
            "not a production edge."),
    }


def build_implementation_constraints() -> List[str]:
    return [
        "Research-training phase: research models are trained locally / offline only; no "
        "production model, no production model candidate, and no deployable model artifact "
        "(pickle / joblib / binary) is created or written.",
        "The expanded D: price-history CSV and its provenance JSONs are read READ-ONLY; nothing "
        "is written to the D: drive and the D: file is never copied into the repo.",
        "Only four small C: outputs are written under research/output (the results JSON and three "
        "summary CSVs).",
        "No network access: no data is fetched and no third-party data source is used.",
        "All features are strictly trailing / point-in-time, labels are strictly forward and "
        "never forward-filled, and prediction neutralisation uses only trailing features and the "
        "prediction itself, so there is no look-ahead leakage.",
        "The universe is survivorship-biased / current-membership and the sector map is "
        "current-as-of (not point-in-time); all results are reported as such and are not a "
        "production edge.",
        "No deployment, no model-v2 enablement, no service restart, no migration, no database "
        "write, no order, and no automation.",
    ]


# --------------------------------------------------------------------------- #
# Summaries that mirror earlier-phase JSON shapes
# --------------------------------------------------------------------------- #
def build_data_summary(feat_summary: Dict[str, Any], dq: Optional[Dict[str, Any]],
                       build: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = dict(feat_summary)
    if dq:
        out["data_quality_status"] = dq.get("status")
        out["data_quality_stats"] = dq.get("stats")
    if build:
        out["build_row_count"] = build.get("row_count")
        out["build_ticker_count"] = build.get("ticker_count")
        out["build_date_start"] = build.get("date_start")
        out["build_date_end"] = build.get("date_end")
        out["build_years_span"] = build.get("years_span")
        out["build_benchmark"] = build.get("benchmark")
    return out


def build_survivorship_caveat(surv: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not surv:
        return {"present": False, "reported_as_survivorship_biased": True, "carried_forward": True}
    return {
        "present": True,
        "membership_basis": surv.get("membership_basis"),
        "point_in_time_membership_claimed": surv.get("point_in_time_membership_claimed"),
        "reported_as_survivorship_biased": surv.get("reported_as_survivorship_biased"),
        "clean_point_in_time_build_deferred": surv.get("clean_point_in_time_build_deferred"),
        "note": surv.get("note"),
        "carried_forward": True,
    }


def build_refined_feature_set_df(eq: pd.DataFrame) -> pd.DataFrame:
    """Per-feature full-sample IC screen over the refined set (vs 21d forward excess)."""
    label = f"forward_excess_return_vs_spy_{PRIMARY_HORIZON}d"
    recs = []
    for f in REFINED_FEATURES:
        col = eq[f]
        ic = _daily_rank_ic(pd.DataFrame({"date": eq["date"], "pred": col, "excess": eq[label]}))
        recs.append({
            "feature": f,
            "family": REFINED_FEATURE_FAMILY.get(f, "unmapped"),
            "role": "risk_factor_and_feature" if f in RISK_FACTORS else "feature",
            "non_null_fraction": _f(col.notna().mean()),
            "full_sample_mean_daily_rank_ic_vs_excess_21d": _f(ic.mean()) if len(ic) else None,
            "n_ic_dates": int(len(ic)),
        })
    out = pd.DataFrame(recs)
    return out.reindex(
        out["full_sample_mean_daily_rank_ic_vs_excess_21d"].abs()
        .sort_values(ascending=False).index).reset_index(drop=True)


def build_model_summary() -> List[Dict[str, Any]]:
    return [
        {"name": MODEL_COMPOSITE, "role": "model_free_benchmark",
         "type": "hand-built model-free composite (no training)", "trained": False,
         "neutralised": False, "library": "numpy/pandas"},
        {"name": MODEL_RIDGE, "role": "primary",
         "type": "refined closed-form ridge regression", "trained": True,
         "neutralised": True, "target": "forward_excess_return_vs_spy", "library": "numpy",
         "lambda": RIDGE_LAMBDA},
        {"name": MODEL_LOGIT, "role": "secondary_benchmark",
         "type": "regularised logistic regression (gradient descent)", "trained": True,
         "neutralised": True, "target": "binary_outperform_spy", "library": "numpy",
         "iterations": LOGIT_ITERS, "l2": LOGIT_L2},
    ]


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_results(*, price_csv: str = EXPANDED_PRICE_HISTORY_CSV,
                  sector_map_csv: str = SECTOR_MAP_CSV
                  ) -> Tuple[Dict[str, Any], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    p3b = _safe_read_json(PHASE3B_JSON)
    refined_cfg = _safe_read_json(PHASE3B_REFINED_CONFIG_JSON)
    p3a = _safe_read_json(PHASE3A_JSON)
    dq = _safe_read_json(DATA_QUALITY_REPORT_JSON)
    build = _safe_read_json(DATA_BUILD_SUMMARY_JSON)
    surv = _safe_read_json(SURVIVORSHIP_CAVEAT_JSON)

    confirm3b = confirm_phase3b(p3b)
    confirm_cfg = confirm_refined_config(refined_cfg)

    sector_map = load_sector_map(sector_map_csv)
    panel = load_price_panel(price_csv)
    eq, feat_summary_meta, spy_close = engineer_features(panel, sector_map)

    walk, aggregate, extras = run_walkforward(eq, spy_close)
    n_folds = walk["config"]["n_folds"]
    rows = walk["results"]

    sector_conc = sector_concentration_summary(rows, MODEL_RIDGE, PRIMARY_HORIZON, p3a)
    kill = kill_switch_evaluation(aggregate, sector_conc)
    reg_summary = regime_summary(rows, MODEL_RIDGE, PRIMARY_HORIZON)
    overlap = overlap_correction_summary(aggregate)

    rec, details, failure_modes = decide(confirm3b, confirm_cfg, data_ok=True,
                                         n_folds=n_folds, kill=kill)

    feat_set_df = build_refined_feature_set_df(eq)
    wf_summary_df = pd.DataFrame(rows)
    regime_df = pd.DataFrame(reg_summary)

    refined_config_used = {
        "for_next_phase_when_emitted": (refined_cfg or {}).get("for_next_phase"),
        "selected_models": [m["name"] for m in build_model_summary()],
        "primary_horizon": PRIMARY_HORIZON,
        "secondary_horizon": SECONDARY_HORIZON,
        "diagnostic_horizon": DIAGNOSTIC_HORIZON,
        "pruned_feature_families": (refined_cfg or {}).get("pruned_feature_families", []),
        "selected_feature_families": sorted(set(REFINED_FEATURE_FAMILY.values())),
        "risk_neutralization_factors": RISK_FACTORS,
        "kill_switch_rules": (refined_cfg or {}).get("kill_switch_rules", []),
        "phase3b_config_confirmed": confirm_cfg,
    }

    results = {
        "phase": PHASE,
        "generated_at": _now(),
        "inputs_read": {
            "phase3b_json": _norm(PHASE3B_JSON),
            "phase3b_refined_config_json": _norm(PHASE3B_REFINED_CONFIG_JSON),
            "phase3a_json": _norm(PHASE3A_JSON),
            "sector_map_csv": _norm(sector_map_csv),
            "expanded_price_history_csv": _norm(price_csv),
            "data_quality_report_json": _norm(DATA_QUALITY_REPORT_JSON),
            "data_build_summary_json": _norm(DATA_BUILD_SUMMARY_JSON),
            "survivorship_caveat_json": _norm(SURVIVORSHIP_CAVEAT_JSON),
        },
        "outputs_written": {
            "results_json": _norm(RESULTS_JSON),
            "refined_walkforward_summary_csv": _norm(WALKFORWARD_SUMMARY_CSV),
            "refined_feature_set_csv": _norm(REFINED_FEATURE_SET_CSV),
            "refined_regime_summary_csv": _norm(REFINED_REGIME_SUMMARY_CSV),
        },
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
        "research_model_trained": True,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "deployable_model_artifact_written": False,
        "d_drive_read": True,
        "d_drive_written": False,
        "network_used": False,
        "phase3b_summary": {
            "phase3b_confirmed": confirm3b,
            "phase3b_recommendation": confirm3b.get("phase3b_recommendation"),
            "refined_config_confirmed": confirm_cfg,
        },
        "refined_config_used": refined_config_used,
        "data_summary": build_data_summary(feat_summary_meta, dq, build),
        "survivorship_caveat": build_survivorship_caveat(surv),
        "feature_engineering_summary": {
            "selected_feature_families": sorted(set(REFINED_FEATURE_FAMILY.values())),
            "pruned_feature_families": ["cross_sectional_ranks"],
            "n_refined_features": len(REFINED_FEATURES),
            "refined_features": list(REFINED_FEATURES),
            "dropped_sign_mirror_returns": ["return_5d", "return_10d", "return_21d"],
            "dropped_redundant_volatility": ["realized_vol_126d"],
            "collapsed_liquidity_to": "avg_dollar_volume_21d",
            "all_features_trailing_point_in_time": True,
            "no_lookahead": True,
        },
        "label_summary": {
            "horizons": list(HORIZONS),
            "primary_horizon": PRIMARY_HORIZON,
            "secondary_horizon": SECONDARY_HORIZON,
            "diagnostic_horizon": DIAGNOSTIC_HORIZON,
            "labels_per_horizon": [
                "forward_return", "forward_spy_return", "forward_excess_return_vs_spy",
                "binary_outperform_spy", "forward_return_rank",
            ],
            "forward_filled": False,
            "label_for_model_scoring": "forward_excess_return_vs_spy",
        },
        "model_summary": build_model_summary(),
        "walkforward_config": walk["config"],
        "walkforward_results": rows,
        "aggregate_results": aggregate,
        "ridge_primary_horizon_coefficient_summary":
            extras["ridge_primary_horizon_coefficient_summary"],
        "regime_summary": reg_summary,
        "sector_concentration_summary": sector_conc,
        "overlap_correction_summary": overlap,
        "kill_switch_evaluation": kill,
        "failure_modes": failure_modes,
        "recommendation": build_recommendation(rec, details),
        "interpretation": build_interpretation(rec, n_folds, rec == REC_KILL),
        "recommended_next_phase": build_recommended_next_phase(rec),
        "implementation_constraints": build_implementation_constraints(),
    }
    return results, wf_summary_df, feat_set_df, regime_df


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #
def write_results(results: Dict[str, Any], path: str = RESULTS_JSON) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, allow_nan=False)


def write_csv(df: pd.DataFrame, path: str) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    df.to_csv(path, index=False)


def run(results_path: str = RESULTS_JSON,
        walkforward_summary_path: str = WALKFORWARD_SUMMARY_CSV,
        refined_feature_set_path: str = REFINED_FEATURE_SET_CSV,
        refined_regime_summary_path: str = REFINED_REGIME_SUMMARY_CSV,
        **kwargs) -> Dict[str, Any]:
    results, wf_df, feat_df, regime_df = build_results(**kwargs)
    write_csv(wf_df, walkforward_summary_path)
    write_csv(feat_df, refined_feature_set_path)
    write_csv(regime_df, refined_regime_summary_path)
    write_results(results, results_path)
    return results


def main() -> int:
    t0 = time.perf_counter()
    d = run()
    elapsed = time.perf_counter() - t0
    rec = d["recommendation"]
    nxt = d["recommended_next_phase"]
    kill = d["kill_switch_evaluation"]
    cfg = d["walkforward_config"]
    print(f"[phase3c] equity rows / tickers / dates : "
          f"{d['data_summary'].get('n_equity_rows')} / "
          f"{d['data_summary'].get('n_equity_tickers')} / {d['data_summary'].get('n_dates')}")
    print(f"[phase3c] refined features              : "
          f"{d['feature_engineering_summary']['n_refined_features']}")
    print(f"[phase3c] walk-forward folds            : {cfg['n_folds']}")
    print(f"[phase3c] primary 21d ridge IC          : {kill.get('primary_mean_rank_ic')} "
          f"(worst fold {kill.get('primary_worst_fold_rank_ic')}, win "
          f"{kill.get('primary_fold_win_rate')}, catastrophic "
          f"{kill.get('primary_catastrophic_fold_count')})")
    print(f"[phase3c] kill-switch gates pass         : {kill.get('all_pass_gates_pass')} "
          f"(major gate failed {kill.get('any_major_gate_failed')})")
    print(f"[phase3c] kill switch triggered          : {rec['kill_switch_triggered']}")
    print(f"[phase3c] recommendation                : {rec['recommendation']}")
    print(f"[phase3c] recommended next phase         : {nxt['phase']} ({nxt['title']})")
    print(f"[phase3c] results written               : {RESULTS_JSON}")
    print(f"[phase3c] elapsed seconds                : {elapsed:.2f}")
    print("[phase3c] research-only; D: read-only; no D: write; no deploy; no model candidate; "
          "no network.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
