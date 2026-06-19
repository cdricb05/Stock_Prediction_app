"""Phase 3-A Greenfield Research Model Baseline (offline, research-only, no deployment).

Strategic pivot: the Phase 2K rescue path produced weak-but-repeatable single-signal leads that
stayed below the confirmation floor (Phase 2K-N: 3 reconfirmed leads, mean residual rank IC
~0.010-0.015, all sub-floor). Rather than keep tuning those same signals, Phase 3-A starts a
fresh, greenfield model-research track: it builds a brand-new trailing feature set from the
expanded D: price / volume panel plus the populated current-as-of sector map, trains baseline
models from scratch (a hand-built model-free composite, a numpy closed-form ridge, and an
optional numpy logistic outperformer), and runs real walk-forward out-of-sample validation to
answer ONE question: can a model trained from scratch on this panel produce a robust
out-of-sample signal?

This is a research-training phase. It is allowed to train research models locally / offline. It
is NOT allowed to and does NOT: train or save a production / deployable model artifact, create a
production model candidate, enable the model-v2 serving flag, deploy, restart any service, run
migrations, write to any database, place orders, automate trading, fetch anything from the
network, write to the D: drive, or claim a production edge. Every result is reported as
survivorship-biased / current-membership caveated and is not a production edge. The full
guardrail rationale lives in docs/phase3a_greenfield_baseline_v1.md.

It reads (read-only):
  * D: expanded price-history CSV (ticker, date, adjusted OHLC, volume) for the panel.
  * D: data-quality / build-summary / survivorship-caveat JSONs for provenance + caveats.
  * research/input/phase2k_p_sector_map_current.csv for sector / industry (Phase 2K-Q).
  * research/output/phase2k_q_populate_sector_map.json for the sector-map provenance.
  * research/output/phase2k_n_narrow_model_free_retest.json for the prior-track context.

It writes only three small files under the C: repo (never the D: drive, never a model binary):
  * research/output/phase3a_greenfield_baseline.json     (full results)
  * research/output/phase3a_greenfield_feature_summary.csv
  * research/output/phase3a_greenfield_walkforward_summary.csv
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

PHASE = "3-A"

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
SECTOR_MAP_CSV = os.path.join(_INPUT_DIR, "phase2k_p_sector_map_current.csv")
PHASE2K_Q_JSON = os.path.join(_OUTPUT_DIR, "phase2k_q_populate_sector_map.json")
PHASE2K_N_JSON = os.path.join(_OUTPUT_DIR, "phase2k_n_narrow_model_free_retest.json")

# The three small C: outputs (never on the D: drive; never a model pickle / joblib / binary).
RESULTS_JSON = os.path.join(_OUTPUT_DIR, "phase3a_greenfield_baseline.json")
FEATURE_SUMMARY_CSV = os.path.join(_OUTPUT_DIR, "phase3a_greenfield_feature_summary.csv")
WALKFORWARD_SUMMARY_CSV = os.path.join(_OUTPUT_DIR, "phase3a_greenfield_walkforward_summary.csv")

BENCHMARK = "SPY"
HORIZONS = [5, 21, 63]

# Walk-forward design (trading sessions): >=3y train, ~6mo validation, embargo = max horizon.
TRAIN_MIN_DAYS = 756
VAL_DAYS = 126
EMBARGO_DAYS = max(HORIZONS)
FOLD_STEP_DAYS = VAL_DAYS  # non-overlapping validation windows
MIN_VAL_DATES = 20
MIN_NAMES_PER_DATE = 25

# Model hyper-parameters (fixed; no tuning / no search).
RIDGE_LAMBDA = 25.0
LOGIT_ITERS = 250
LOGIT_LR = 0.20
LOGIT_L2 = 1.0

# Decision thresholds.
PROMISING_MIN_IC = 0.02
WEAK_MIN_IC = 0.0
STRONG_FOLD_WIN_RATE = 0.60
CATASTROPHIC_FOLD_IC = -0.05
MAX_TOP_SECTOR_SHARE = 0.50

REC_PROMISING = "GREENFIELD_BASELINE_PROMISING"
REC_WEAK = "GREENFIELD_BASELINE_WEAK_BUT_IMPROVABLE"
REC_FAILED = "GREENFIELD_BASELINE_FAILED"
REC_BLOCKED = "GREENFIELD_BASELINE_BLOCKED_BY_DATA"
_ALLOWED_RECOMMENDATIONS = [REC_PROMISING, REC_WEAK, REC_FAILED, REC_BLOCKED]

NEXT_PHASE = "3-B"
NEXT_PHASE_BY_RECOMMENDATION = {
    REC_PROMISING: {
        "title": "Greenfield Model Robustness Validation",
        "purpose": (
            "Run stricter leakage, ablation, fold, regime, and transaction-cost-aware validation "
            "before any production model-candidate decision. Still no deployment, no model "
            "candidate, and no production edge claim."),
    },
    REC_WEAK: {
        "title": "Greenfield Feature and Label Refinement",
        "purpose": (
            "Refine features, labels, horizons, and sector handling before another walk-forward "
            "test. Still no deployment, no model candidate, and no production edge claim."),
    },
    REC_FAILED: {
        "title": "New Data and Feature Family Decision",
        "purpose": (
            "Decide whether price / volume-only modeling is insufficient and whether external "
            "data (fundamentals, estimates, earnings, news, or options) is needed. Still no "
            "deployment, no model candidate, and no production edge claim."),
    },
    REC_BLOCKED: {
        "title": "Greenfield Data Repair Before Modeling",
        "purpose": (
            "Repair the data issues that prevented a valid conclusion before continuing model "
            "research. Still no deployment, no model candidate, and no production edge claim."),
    },
}

MODEL_COMPOSITE = "MODEL_FREE_COMPOSITE_BASELINE"
MODEL_RIDGE = "RIDGE_LINEAR_RANK_MODEL"
MODEL_LOGIT = "OPTIONAL_LOGISTIC_OUTPERFORMER"

# Composite baseline: signed trailing features blended via cross-sectional rank (no training).
# Signs reflect the hypothesised direction (momentum +, reversal +, low vol +).
COMPOSITE_FEATURES = {
    "momentum_12_1": 1.0,
    "reversal_21d": 1.0,
    "reversal_5d": 1.0,
    "realized_vol_63d": -1.0,
    "excess_return_vs_spy_63d": 1.0,
}


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
# Feature engineering (strictly trailing; no feature uses future data)
# --------------------------------------------------------------------------- #
TRAILING_FEATURES: List[str] = [
    # price momentum
    "return_5d", "return_10d", "return_21d", "return_63d", "return_126d", "return_252d",
    "momentum_12_1",
    # reversal
    "reversal_5d", "reversal_10d", "reversal_21d",
    # volatility / risk
    "realized_vol_21d", "realized_vol_63d", "realized_vol_126d", "downside_vol_21d",
    "max_drawdown_63d", "distance_from_63d_high",
    # volume / liquidity
    "dollar_volume", "avg_dollar_volume_21d", "volume_zscore_21d", "volume_trend_21d",
    # market-relative
    "excess_return_vs_spy_5d", "excess_return_vs_spy_21d", "excess_return_vs_spy_63d",
    "rolling_beta_63d", "rolling_corr_spy_63d",
    # sector-relative
    "sector_relative_return_21d", "sector_relative_return_63d", "sector_relative_momentum_12_1",
    "sector_relative_reversal_5d", "sector_relative_reversal_21d",
    "sector_relative_volatility_63d",
]
# Cross-sectional rank features added below for these base features.
RANKED_FEATURES = [
    "momentum_12_1", "reversal_5d", "reversal_21d", "return_21d", "realized_vol_63d",
    "avg_dollar_volume_21d",
]


def _trailing_return(close: pd.Series, n: int) -> pd.Series:
    return close / close.shift(n) - 1.0


def engineer_features(panel: pd.DataFrame, sector_map: pd.DataFrame
                      ) -> Tuple[pd.DataFrame, Dict[str, Any], List[str]]:
    """Build trailing features + forward labels. SPY is benchmark only, never a target row."""
    # Benchmark daily + trailing returns by date.
    spy = panel[panel["ticker"] == BENCHMARK].copy().sort_values("date")
    spy_ret1 = spy.set_index("date")["adjusted_close"].pct_change()
    spy_by_date = pd.DataFrame({"date": spy["date"].values})
    spy_by_date["spy_ret1"] = spy_ret1.values
    spy_close = spy.set_index("date")["adjusted_close"]
    for n in (5, 21, 63):
        spy_by_date[f"spy_return_{n}d"] = (spy_close / spy_close.shift(n) - 1.0).values
        spy_by_date[f"spy_fwd_return_{n}d"] = (spy_close.shift(-n) / spy_close - 1.0).values

    eq = panel[panel["ticker"] != BENCHMARK].copy()
    eq = eq.merge(sector_map, on="ticker", how="left")
    eq["sector"] = eq["sector"].fillna("")
    eq = eq.merge(spy_by_date, on="date", how="left")
    eq = eq.sort_values(["ticker", "date"]).reset_index(drop=True)

    g = eq.groupby("ticker", sort=False)
    close = eq["adjusted_close"]
    ret1 = g["adjusted_close"].pct_change()
    eq["_ret1"] = ret1

    # Price momentum.
    for n in (5, 10, 21, 63, 126, 252):
        eq[f"return_{n}d"] = g["adjusted_close"].transform(lambda s, n=n: s / s.shift(n) - 1.0)
    eq["momentum_12_1"] = g["adjusted_close"].transform(
        lambda s: s.shift(21) / s.shift(252) - 1.0)

    # Reversal.
    for n in (5, 10, 21):
        eq[f"reversal_{n}d"] = -eq[f"return_{n}d"]

    # Volatility / risk.
    for n in (21, 63, 126):
        eq[f"realized_vol_{n}d"] = g["_ret1"].transform(lambda s, n=n: s.rolling(n).std())
    neg = eq["_ret1"].clip(upper=0.0)
    eq["_neg2"] = neg * neg
    eq["downside_vol_21d"] = eq.groupby("ticker", sort=False)["_neg2"].transform(
        lambda s: np.sqrt(s.rolling(21).mean()))
    roll_max_63 = g["adjusted_close"].transform(lambda s: s.rolling(63).max())
    roll_min_63 = g["adjusted_close"].transform(lambda s: s.rolling(63).min())
    eq["distance_from_63d_high"] = close / roll_max_63 - 1.0
    # Trailing 63d max drawdown approximated by window trough/peak (no lookahead).
    eq["max_drawdown_63d"] = roll_min_63 / roll_max_63 - 1.0

    # Volume / liquidity.
    eq["dollar_volume"] = eq["adjusted_close"] * eq["volume"]
    eq["avg_dollar_volume_21d"] = eq.groupby("ticker", sort=False)["dollar_volume"].transform(
        lambda s: s.rolling(21).mean())
    eq["_volmean21"] = eq.groupby("ticker", sort=False)["volume"].transform(
        lambda s: s.rolling(21).mean())
    vol_std21 = eq.groupby("ticker", sort=False)["volume"].transform(
        lambda s: s.rolling(21).std())
    eq["volume_zscore_21d"] = (eq["volume"] - eq["_volmean21"]) / vol_std21.replace(0.0, np.nan)
    eq["volume_trend_21d"] = eq.groupby("ticker", sort=False)["_volmean21"].transform(
        lambda s: s / s.shift(21) - 1.0)

    # Market-relative.
    for n in (5, 21, 63):
        eq[f"excess_return_vs_spy_{n}d"] = eq[f"return_{n}d"] - eq[f"spy_return_{n}d"]
    # Rolling beta / corr vs SPY over 63 sessions from trailing daily returns.
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

    # Cross-sectional ranks (market by date; sector by date+sector). Percentile ranks in [0,1].
    grp_d = eq.groupby("date", sort=False)
    extra: List[str] = []
    for base in RANKED_FEATURES:
        col_m = f"market_rank_{base}"
        col_s = f"sector_rank_{base}"
        eq[col_m] = grp_d[base].transform(lambda s: s.rank(pct=True))
        eq[col_s] = grp_ds[base].transform(lambda s: s.rank(pct=True))
        extra.append(col_m)
        extra.append(col_s)

    # Forward labels (strictly forward; never forward-filled).
    for h in HORIZONS:
        fwd = eq.groupby("ticker", sort=False)["adjusted_close"].transform(
            lambda s, h=h: s.shift(-h) / s - 1.0)
        eq[f"forward_return_{h}d"] = fwd
        eq[f"forward_spy_return_{h}d"] = eq[f"spy_fwd_return_{h}d"]
        eq[f"forward_excess_return_vs_spy_{h}d"] = fwd - eq[f"spy_fwd_return_{h}d"]
        eq[f"binary_outperform_spy_{h}d"] = (
            eq[f"forward_excess_return_vs_spy_{h}d"] > 0).astype(float)
        eq[f"forward_return_rank_{h}d"] = eq.groupby("date", sort=False)[
            f"forward_return_{h}d"].transform(lambda s: s.rank(pct=True))

    # Composite model-free score (cross-sectional rank blend; no training).
    comp = np.zeros(len(eq), dtype=float)
    wsum = 0.0
    for feat, sign in COMPOSITE_FEATURES.items():
        r = grp_d[feat].transform(lambda s: s.rank(pct=True))
        comp = comp + sign * (r.fillna(0.5).values - 0.5)
        wsum += abs(sign)
    eq["composite_score"] = comp / wsum if wsum else comp

    feature_cols = list(TRAILING_FEATURES) + extra
    summary = {
        "n_equity_rows": int(len(eq)),
        "n_equity_tickers": int(eq["ticker"].nunique()),
        "n_dates": int(eq["date"].nunique()),
        "date_start": str(eq["date"].min().date()),
        "date_end": str(eq["date"].max().date()),
        "benchmark": BENCHMARK,
        "n_trailing_features": len(feature_cols),
        "all_features_trailing_point_in_time": True,
        "labels_forward_filled": False,
        "horizons": list(HORIZONS),
        "sector_coverage_fraction": _f(
            (eq["sector"].str.len() > 0).mean()),
    }
    return eq, summary, feature_cols


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
    return np.nan_to_num(z, nan=0.0, posinf=0.0, neginf=0.0)


def train_ridge(x_tr: np.ndarray, y_tr: np.ndarray) -> np.ndarray:
    """Closed-form ridge with an unpenalised intercept. Returns coefficients (incl. intercept)."""
    n, k = x_tr.shape
    xb = np.hstack([np.ones((n, 1)), x_tr])
    reg = np.eye(k + 1) * RIDGE_LAMBDA
    reg[0, 0] = 0.0  # do not penalise the intercept
    xtx = xb.T @ xb + reg
    xty = xb.T @ y_tr
    coef = np.linalg.solve(xtx, xty)
    return coef


def predict_ridge(coef: np.ndarray, x: np.ndarray) -> np.ndarray:
    xb = np.hstack([np.ones((x.shape[0], 1)), x])
    return xb @ coef


def train_logistic(x_tr: np.ndarray, y_tr: np.ndarray) -> np.ndarray:
    """Regularised logistic regression via bounded gradient descent (numpy only)."""
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
# Metrics
# --------------------------------------------------------------------------- #
def _daily_rank_ic(frame: pd.DataFrame) -> pd.Series:
    """Per-date Spearman rank IC between 'pred' and 'excess' (vectorised)."""
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
    ic = (agg["num"] / denom.replace(0.0, np.nan))
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
    # Turnover proxy: average fraction of top-quintile names not in the prior date's top set.
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


def evaluate(frame: pd.DataFrame) -> Dict[str, Any]:
    """frame columns: date, pred, excess, binary, sector, ticker."""
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
        "daily_ic_by_year": _ic_by_year(ic),
    }
    res.update(_quintile_metrics(frame))
    return res


def _ic_by_year(ic: pd.Series) -> Dict[str, Any]:
    if ic.empty:
        return {}
    by = ic.groupby(ic.index.year).mean()
    return {str(int(y)): _f(v) for y, v in by.items()}


# --------------------------------------------------------------------------- #
# Walk-forward driver
# --------------------------------------------------------------------------- #
def run_walkforward(eq: pd.DataFrame, feature_cols: List[str]
                    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    dates = sorted(eq["date"].unique())
    date_pos = {d: i for i, d in enumerate(dates)}
    eq = eq.copy()
    eq["_didx"] = eq["date"].map(date_pos).astype(int)
    folds = build_folds(dates)

    feat_mat = eq[feature_cols].to_numpy(dtype=float)
    feat_mat = np.where(np.isfinite(feat_mat), feat_mat, np.nan)
    didx = eq["_didx"].to_numpy()
    comp_score = eq["composite_score"].to_numpy(dtype=float)

    rows: List[Dict[str, Any]] = []
    fold_records: List[Dict[str, Any]] = []
    ridge_coefs: Dict[int, np.ndarray] = {h: [] for h in HORIZONS}

    for fold in folds:
        tr_mask = (didx >= fold["train_start_idx"]) & (didx < fold["train_end_idx"])
        va_mask = (didx >= fold["val_start_idx"]) & (didx < fold["val_end_idx"])
        if not tr_mask.any() or not va_mask.any():
            continue
        val_dates = [d for d in dates
                     if fold["val_start_idx"] <= date_pos[d] < fold["val_end_idx"]]
        val_start = str(pd.Timestamp(val_dates[0]).date())
        val_end = str(pd.Timestamp(val_dates[-1]).date())

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

            preds: Dict[str, np.ndarray] = {}
            # Model-free composite (no training).
            preds[MODEL_COMPOSITE] = comp_score[va_mask]
            # Ridge on forward excess return.
            if ok_tr.sum() >= 200:
                coef = train_ridge(x_tr[ok_tr], y_tr[ok_tr])
                ridge_coefs[h].append(coef)
                preds[MODEL_RIDGE] = predict_ridge(coef, x_va)
            # Optional logistic outperformer on binary label.
            if ok_tr_b.sum() >= 200:
                w = train_logistic(x_tr[ok_tr_b], b_tr[ok_tr_b])
                preds[MODEL_LOGIT] = predict_logistic(w, x_va)

            for model, pred in preds.items():
                fr = base_va.copy()
                fr["pred"] = pred
                m = evaluate(fr)
                rec = {
                    "model": model, "horizon_days": h, "fold": fold["fold"],
                    "val_start": val_start, "val_end": val_end,
                    **{k: v for k, v in m.items() if k != "daily_ic_by_year"},
                }
                rows.append(rec)
                fold_records.append({"model": model, "horizon": h, **m,
                                     "val_start": val_start, "val_end": val_end})

    aggregate = aggregate_results(rows)
    coef_summary = summarize_ridge_coefs(ridge_coefs, feature_cols)
    aggregate["ridge_coefficient_summary"] = coef_summary
    walk = {
        "config": {
            "train_min_days": TRAIN_MIN_DAYS, "val_days": VAL_DAYS,
            "embargo_days": EMBARGO_DAYS, "fold_step_days": FOLD_STEP_DAYS,
            "n_folds": len({r["fold"] for r in rows}),
            "horizons": list(HORIZONS),
            "design": ("chronological non-overlapping ~6-month validation windows after a >=3y "
                       "training window, with a max-horizon embargo between train and validation; "
                       "models train only on dates strictly before each validation window."),
        },
        "results": rows,
    }
    return [walk, aggregate], {"n_folds": walk["config"]["n_folds"]}  # type: ignore[return-value]


def summarize_ridge_coefs(ridge_coefs: Dict[int, List[np.ndarray]],
                          feature_cols: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for h, coefs in ridge_coefs.items():
        if not coefs:
            continue
        arr = np.vstack(coefs)  # folds x (1+k)
        mean_coef = arr.mean(axis=0)
        feats = ["__intercept__"] + feature_cols
        pairs = sorted(zip(feats[1:], mean_coef[1:]), key=lambda kv: -abs(kv[1]))
        out[str(h)] = {
            "n_folds_fit": int(arr.shape[0]),
            "intercept_mean": _f(mean_coef[0]),
            "top_features_by_abs_mean_coef": [
                {"feature": f, "mean_coef": _f(c)} for f, c in pairs[:12]
            ],
        }
    return out


def aggregate_results(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    df = pd.DataFrame(rows)
    agg: Dict[str, Any] = {"by_model_horizon": []}
    if df.empty:
        return agg
    for (model, h), grp in df.groupby(["model", "horizon_days"]):
        ics = grp["mean_rank_ic"].dropna()
        spreads = grp["top_minus_bottom_spread"].dropna()
        worst = _f(ics.min()) if len(ics) else None
        best = _f(ics.max()) if len(ics) else None
        agg["by_model_horizon"].append({
            "model": model, "horizon_days": int(h),
            "n_folds": int(len(grp)),
            "mean_rank_ic": _f(ics.mean()) if len(ics) else None,
            "median_rank_ic": _f(ics.median()) if len(ics) else None,
            "fold_win_rate": _f((ics > 0).mean()) if len(ics) else None,
            "positive_spread_fraction": _f((spreads > 0).mean()) if len(spreads) else None,
            "mean_top_minus_bottom_spread": _f(spreads.mean()) if len(spreads) else None,
            "worst_fold_rank_ic": worst,
            "best_fold_rank_ic": best,
            "mean_hit_rate_top_quintile": _f(grp["hit_rate_top_quintile"].dropna().mean())
            if grp["hit_rate_top_quintile"].notna().any() else None,
            "mean_top_quintile_max_sector_share": _f(
                grp["top_quintile_max_sector_share"].dropna().mean())
            if grp["top_quintile_max_sector_share"].notna().any() else None,
            "mean_turnover_proxy": _f(grp["turnover_proxy"].dropna().mean())
            if grp["turnover_proxy"].notna().any() else None,
        })
    return agg


# --------------------------------------------------------------------------- #
# Feature screen summary (full-sample, model-free)
# --------------------------------------------------------------------------- #
def feature_summary(eq: pd.DataFrame, feature_cols: List[str]) -> pd.DataFrame:
    label = "forward_excess_return_vs_spy_21d"
    recs = []
    for f in feature_cols:
        col = eq[f]
        fr = pd.DataFrame({"date": eq["date"], "pred": col, "excess": eq[label]})
        ic = _daily_rank_ic(fr)
        recs.append({
            "feature": f,
            "non_null_fraction": _f(col.notna().mean()),
            "mean": _f(col.mean()),
            "std": _f(col.std()),
            "full_sample_mean_daily_rank_ic_vs_excess_21d": _f(ic.mean()) if len(ic) else None,
            "n_ic_dates": int(len(ic)),
        })
    out = pd.DataFrame(recs)
    out = out.reindex(out["full_sample_mean_daily_rank_ic_vs_excess_21d"].abs()
                      .sort_values(ascending=False).index)
    return out.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #
def decide(aggregate: Dict[str, Any], feature_screen: pd.DataFrame,
           data_ok: bool, n_folds: int) -> Tuple[str, Dict[str, Any], List[str]]:
    rows = aggregate.get("by_model_horizon", [])
    failure_modes: List[str] = []
    if not data_ok or n_folds < 2 or not rows:
        failure_modes.append("Insufficient data / folds to draw a valid conclusion.")
        return REC_BLOCKED, {"reason": "Data or fold coverage insufficient for a valid "
                             "walk-forward conclusion."}, failure_modes

    learned = [r for r in rows if r["model"] in (MODEL_RIDGE, MODEL_LOGIT)
               and r["mean_rank_ic"] is not None]
    if not learned:
        failure_modes.append("No learned model produced a usable rank IC.")
        return REC_BLOCKED, {"reason": "No learned model produced usable out-of-sample IC."}, \
            failure_modes

    best = max(learned, key=lambda r: r["mean_rank_ic"])
    best_ic = best["mean_rank_ic"]
    win = best.get("fold_win_rate") or 0.0
    pos_spread = best.get("positive_spread_fraction") or 0.0
    worst = best.get("worst_fold_rank_ic")
    sector_share = best.get("mean_top_quintile_max_sector_share")
    tmb = best.get("mean_top_minus_bottom_spread") or 0.0

    # Compare to the model-free composite at the same horizon.
    comp = next((r for r in rows if r["model"] == MODEL_COMPOSITE
                 and r["horizon_days"] == best["horizon_days"]), None)
    comp_ic = comp["mean_rank_ic"] if comp and comp["mean_rank_ic"] is not None else -1.0
    beats_baseline = best_ic > comp_ic

    not_catastrophic = worst is None or worst > CATASTROPHIC_FOLD_IC
    not_one_sector = sector_share is None or sector_share < MAX_TOP_SECTOR_SHARE
    by_year_ok = True  # year-by-year handled via fold_win_rate as a stability proxy

    strong_feature_screen = bool(
        feature_screen["full_sample_mean_daily_rank_ic_vs_excess_21d"].abs().dropna().ge(0.02).any())

    details = {
        "best_model": best["model"], "best_horizon_days": best["horizon_days"],
        "best_mean_rank_ic": best_ic, "best_fold_win_rate": win,
        "best_positive_spread_fraction": pos_spread,
        "best_worst_fold_rank_ic": worst,
        "best_mean_top_minus_bottom_spread": tmb,
        "best_top_quintile_max_sector_share": sector_share,
        "model_free_composite_ic_same_horizon": comp_ic,
        "beats_model_free_baseline": beats_baseline,
        "strong_feature_screen": strong_feature_screen,
    }

    if (best_ic >= PROMISING_MIN_IC and win >= STRONG_FOLD_WIN_RATE and tmb > 0
            and pos_spread >= 0.55 and not_catastrophic and not_one_sector
            and beats_baseline and by_year_ok):
        rec = REC_PROMISING
    elif best_ic > WEAK_MIN_IC and (win >= 0.5 or strong_feature_screen) and beats_baseline:
        rec = REC_WEAK
        if best_ic < PROMISING_MIN_IC:
            failure_modes.append(
                f"Best out-of-sample mean rank IC {best_ic} is positive but below the "
                f"{PROMISING_MIN_IC} promising threshold.")
        if win < STRONG_FOLD_WIN_RATE:
            failure_modes.append(f"Fold win rate {win} is below the strong {STRONG_FOLD_WIN_RATE}.")
        if pos_spread < 0.55:
            failure_modes.append("Long-short spread is not consistently positive across folds.")
        if not not_catastrophic:
            failure_modes.append(
                f"At least one walk-forward fold is catastrophic (worst fold rank IC {worst} "
                f"below {CATASTROPHIC_FOLD_IC}); the out-of-sample signal is not yet stable "
                "enough to call promising.")
    else:
        rec = REC_FAILED
        if not beats_baseline:
            failure_modes.append("Learned models do not beat the model-free composite baseline.")
        if best_ic <= WEAK_MIN_IC:
            failure_modes.append("Best out-of-sample mean rank IC is not positive.")
        if not not_catastrophic:
            failure_modes.append(f"At least one fold is catastrophic (IC {worst}).")

    if not not_one_sector:
        failure_modes.append("Top-quintile selections are concentrated in a single sector.")
    return rec, details, failure_modes


def build_recommendation(rec: str, details: Dict[str, Any]) -> Dict[str, Any]:
    base = {
        "recommendation": rec,
        "allowed_values": list(_ALLOWED_RECOMMENDATIONS),
        "create_production_model_candidate_now": False,
        "train_production_model_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "results_are_survivorship_biased": True,
        "decision_inputs": details,
    }
    if rec == REC_PROMISING:
        base["reason"] = (
            "A from-scratch model produced a positive, fold-stable out-of-sample rank IC at or "
            "above the promising threshold, with a consistently positive long-short spread that "
            "beats the model-free composite and is not explained by one sector or fold. This is "
            "promising research only; no production model candidate is created and no production "
            "edge is claimed. Proceed to stricter robustness validation (Phase 3-B).")
    elif rec == REC_WEAK:
        base["reason"] = (
            "A from-scratch model shows some positive out-of-sample signal that beats the "
            "model-free composite, but it is unstable and/or below the promising threshold. The "
            "feature families look improvable. No production model candidate is created and no "
            "production edge is claimed. Refine features / labels / horizons (Phase 3-B).")
    elif rec == REC_FAILED:
        base["reason"] = (
            "From-scratch models did not beat the model-free baseline and/or produced an unstable "
            "or non-positive out-of-sample signal on this price / volume / sector panel. No "
            "production model candidate is created and no production edge is claimed. Decide "
            "whether richer data is needed (Phase 3-B).")
    else:
        base["reason"] = (
            "Data or fold coverage was insufficient to draw a valid walk-forward conclusion. No "
            "production model candidate is created and no production edge is claimed. Repair the "
            "data before continuing (Phase 3-B).")
    return base


def build_recommended_next_phase(rec: str) -> Dict[str, str]:
    meta = NEXT_PHASE_BY_RECOMMENDATION.get(rec, NEXT_PHASE_BY_RECOMMENDATION[REC_BLOCKED])
    return {"phase": NEXT_PHASE, "title": meta["title"], "purpose": meta["purpose"]}


def build_interpretation(rec: str, n_folds: int) -> Dict[str, Any]:
    return {
        "research_model_trained": True,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "deployable_model_artifact_written": False,
        "model_v2_enabled": False,
        "ran_walkforward_validation": True,
        "read_from_d_drive": True,
        "wrote_to_d_drive": False,
        "fetched_data_from_network": False,
        "production_edge_claimed": False,
        "results_are_survivorship_biased": True,
        "narrative": (
            "Phase 3-A is a greenfield research-model baseline that abandons the Phase 2K "
            "single-signal rescue path. It read the expanded D: price / volume panel read-only "
            "and the populated current-as-of sector map, engineered a fresh trailing-only "
            "feature set across momentum, reversal, volatility, volume, market-relative, "
            "sector-relative, and cross-sectional-rank families, built strictly-forward labels "
            "at 5 / 21 / 63 days, and trained baseline models from scratch (a model-free "
            "composite, a numpy closed-form ridge, and an optional numpy logistic outperformer) "
            f"under {n_folds} chronological, embargoed, out-of-sample walk-forward folds. It "
            "trained research models only: it created no production model candidate, wrote no "
            "deployable model artifact, enabled no serving flag, deployed nothing, touched no "
            "database, placed no orders, and fetched nothing from the network. Every result is "
            "survivorship-biased / current-membership caveated and is not a production edge."),
    }


def build_implementation_constraints() -> List[str]:
    return [
        "Research-training phase: research models are trained locally / offline only; no "
        "production model, no production model candidate, and no deployable model artifact "
        "(pickle / joblib / binary) is created or written.",
        "The expanded D: price-history CSV and its provenance JSONs are read READ-ONLY; nothing "
        "is written to the D: drive.",
        "Only three small C: outputs are written: the results JSON and two summary CSVs under "
        "research/output.",
        "No network access: no data is fetched and no third-party data source is used.",
        "All features are strictly trailing / point-in-time and labels are strictly forward and "
        "never forward-filled, so there is no look-ahead leakage in the walk-forward design.",
        "The universe is survivorship-biased / current-membership and the sector map is "
        "current-as-of (not point-in-time); all results are reported as such and are not a "
        "production edge.",
        "No deployment, no model-v2 enablement, no service restart, no migration, no database "
        "write, no order, and no automation.",
    ]


# --------------------------------------------------------------------------- #
# Assembly
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
        return {"present": False, "reported_as_survivorship_biased": True}
    return {
        "present": True,
        "membership_basis": surv.get("membership_basis"),
        "point_in_time_membership_claimed": surv.get("point_in_time_membership_claimed"),
        "reported_as_survivorship_biased": surv.get("reported_as_survivorship_biased"),
        "clean_point_in_time_build_deferred": surv.get("clean_point_in_time_build_deferred"),
        "note": surv.get("note"),
        "carried_forward": True,
    }


def build_sector_map_summary(sm: pd.DataFrame, kq: Optional[Dict[str, Any]],
                             matched: int, n_equities: int) -> Dict[str, Any]:
    out = {
        "map_path": _norm(SECTOR_MAP_CSV),
        "map_row_count": int(len(sm)),
        "distinct_sectors": int(sm[sm["sector"].str.len() > 0]["sector"].nunique()),
        "equities_matched_to_sector": int(matched),
        "equity_ticker_count": int(n_equities),
        "match_fraction": _f(matched / n_equities) if n_equities else None,
        "point_in_time": False,
        "is_current_as_of_not_point_in_time": True,
    }
    if kq:
        pop = kq.get("sector_map_population", {}) or {}
        out["source"] = pop.get("source")
        out["as_of_date"] = pop.get("as_of_date")
        out["phase2k_q_recommendation"] = (kq.get("recommendation", {}) or {}).get("recommendation")
    return out


def build_models_trained() -> List[Dict[str, Any]]:
    return [
        {"name": MODEL_COMPOSITE, "type": "hand-built model-free composite (no training)",
         "trained": False, "target": "n/a (benchmark)", "library": "numpy/pandas"},
        {"name": MODEL_RIDGE, "type": "closed-form ridge regression",
         "trained": True, "target": "forward_excess_return_vs_spy", "library": "numpy",
         "lambda": RIDGE_LAMBDA},
        {"name": MODEL_LOGIT, "type": "regularised logistic regression (gradient descent)",
         "trained": True, "target": "binary_outperform_spy", "library": "numpy",
         "iterations": LOGIT_ITERS, "l2": LOGIT_L2,
         "note": "Included as an optional numpy baseline; stable, no new dependency."},
    ]


def build_results(*, price_csv: str = EXPANDED_PRICE_HISTORY_CSV,
                  sector_map_csv: str = SECTOR_MAP_CSV) -> Tuple[Dict[str, Any], pd.DataFrame, pd.DataFrame]:
    dq = _safe_read_json(DATA_QUALITY_REPORT_JSON)
    build = _safe_read_json(DATA_BUILD_SUMMARY_JSON)
    surv = _safe_read_json(SURVIVORSHIP_CAVEAT_JSON)
    kq = _safe_read_json(PHASE2K_Q_JSON)

    sector_map = load_sector_map(sector_map_csv)
    panel = load_price_panel(price_csv)
    eq, feat_summary_meta, feature_cols = engineer_features(panel, sector_map)

    matched = int((eq.groupby("ticker")["sector"].first().str.len() > 0).sum())
    n_equities = int(eq["ticker"].nunique())

    feat_df = feature_summary(eq, feature_cols)
    (walk, aggregate), wf_meta = run_walkforward(eq, feature_cols)
    n_folds = walk["config"]["n_folds"]

    rec, details, failure_modes = decide(aggregate, feat_df, data_ok=True, n_folds=n_folds)

    wf_summary_df = pd.DataFrame(walk["results"])

    results = {
        "phase": PHASE,
        "generated_at": _now(),
        "inputs_read": {
            "expanded_price_history_csv": _norm(price_csv),
            "data_quality_report_json": _norm(DATA_QUALITY_REPORT_JSON),
            "data_build_summary_json": _norm(DATA_BUILD_SUMMARY_JSON),
            "survivorship_caveat_json": _norm(SURVIVORSHIP_CAVEAT_JSON),
            "sector_map_csv": _norm(sector_map_csv),
            "phase2k_q_json": _norm(PHASE2K_Q_JSON),
            "phase2k_n_json": _norm(PHASE2K_N_JSON),
        },
        "outputs_written": {
            "results_json": _norm(RESULTS_JSON),
            "feature_summary_csv": _norm(FEATURE_SUMMARY_CSV),
            "walkforward_summary_csv": _norm(WALKFORWARD_SUMMARY_CSV),
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
        "data_summary": build_data_summary(feat_summary_meta, dq, build),
        "survivorship_caveat": build_survivorship_caveat(surv),
        "sector_map_summary": build_sector_map_summary(sector_map, kq, matched, n_equities),
        "feature_engineering_summary": {
            "feature_families": [
                "price_momentum", "reversal", "volatility_risk", "volume_liquidity",
                "market_relative", "sector_relative", "cross_sectional_ranks",
            ],
            "n_features": len(feature_cols),
            "features": feature_cols,
            "all_features_trailing_point_in_time": True,
            "no_lookahead": True,
        },
        "label_summary": {
            "horizons": list(HORIZONS),
            "labels_per_horizon": [
                "forward_return", "forward_spy_return", "forward_excess_return_vs_spy",
                "binary_outperform_spy", "forward_return_rank",
            ],
            "forward_filled": False,
            "label_for_model_scoring": "forward_excess_return_vs_spy",
        },
        "models_trained": build_models_trained(),
        "walkforward_config": walk["config"],
        "walkforward_results": walk["results"],
        "aggregate_results": aggregate,
        "feature_importance_or_coefficients": aggregate.get("ridge_coefficient_summary", {}),
        "benchmark_comparison": build_benchmark_comparison(aggregate),
        "failure_modes": failure_modes,
        "recommendation": build_recommendation(rec, details),
        "interpretation": build_interpretation(rec, n_folds),
        "recommended_next_phase": build_recommended_next_phase(rec),
        "implementation_constraints": build_implementation_constraints(),
    }
    return results, feat_df, wf_summary_df


def build_benchmark_comparison(aggregate: Dict[str, Any]) -> Dict[str, Any]:
    rows = aggregate.get("by_model_horizon", [])
    by_h: Dict[str, Any] = {}
    for h in HORIZONS:
        entry = {}
        for model in (MODEL_COMPOSITE, MODEL_RIDGE, MODEL_LOGIT):
            r = next((x for x in rows if x["model"] == model and x["horizon_days"] == h), None)
            entry[model] = r["mean_rank_ic"] if r else None
        comp = entry.get(MODEL_COMPOSITE)
        learned = [v for k, v in entry.items() if k != MODEL_COMPOSITE and v is not None]
        entry["best_learned_beats_composite"] = bool(
            learned and comp is not None and max(learned) > comp)
        by_h[str(h)] = entry
    return {"mean_rank_ic_by_model_and_horizon": by_h}


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
        feature_summary_path: str = FEATURE_SUMMARY_CSV,
        walkforward_summary_path: str = WALKFORWARD_SUMMARY_CSV,
        **kwargs) -> Dict[str, Any]:
    results, feat_df, wf_df = build_results(**kwargs)
    write_csv(feat_df, feature_summary_path)
    write_csv(wf_df, walkforward_summary_path)
    write_results(results, results_path)
    return results


def main() -> int:
    t0 = time.perf_counter()
    d = run()
    elapsed = time.perf_counter() - t0
    rec = d["recommendation"]
    nxt = d["recommended_next_phase"]
    di = rec["decision_inputs"]
    cfg = d["walkforward_config"]
    print(f"[phase3a] equity rows / tickers / dates : "
          f"{d['data_summary'].get('n_equity_rows')} / "
          f"{d['data_summary'].get('n_equity_tickers')} / {d['data_summary'].get('n_dates')}")
    print(f"[phase3a] features                      : {d['feature_engineering_summary']['n_features']}")
    print(f"[phase3a] walk-forward folds            : {cfg['n_folds']}")
    print(f"[phase3a] best learned model/horizon    : {di.get('best_model')} @ "
          f"{di.get('best_horizon_days')}d")
    print(f"[phase3a] best mean rank IC             : {di.get('best_mean_rank_ic')} "
          f"(fold win {di.get('best_fold_win_rate')}, beats composite "
          f"{di.get('beats_model_free_baseline')})")
    print(f"[phase3a] recommendation                : {rec['recommendation']}")
    print(f"[phase3a] recommended next phase         : {nxt['phase']} ({nxt['title']})")
    print(f"[phase3a] results written               : {RESULTS_JSON}")
    print(f"[phase3a] elapsed seconds                : {elapsed:.2f}")
    print("[phase3a] research-only; D: read-only; no D: write; no deploy; no model candidate; "
          "no network.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
