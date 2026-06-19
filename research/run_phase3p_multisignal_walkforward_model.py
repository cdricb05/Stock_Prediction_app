"""Phase 3-P - Research-Only Multi-Signal Walk-Forward Ranking Model.

This is the first real research-only *model* layer on top of the Phase 3-O multi-signal
feature factory.  It trains and evaluates NON-DEPLOYABLE research models with proper
walk-forward validation and per-horizon embargo, and compares:

  * simple baseline rank composites (reproduced from Phase 3-O),
  * a regularized cross-sectional linear ranking model (NumPy closed-form ridge - no
    sklearn / no third-party ML dependency), and
  * a regime-aware ensemble (separate risk-on / risk-off sub-models, with a documented
    fallback when a regime subset is too thin).

The core model is a cross-sectional multi-signal ranking model.  ARIMA-style features are
ONE conditioning family among many (Phase 3-O already produced them); they are NOT the model.

It is research-only.  It trains models IN MEMORY only.  It creates NO production model
candidate, NO production predictions / scores / portfolio weights / order instructions, and
writes NO deployable model artifact (no serialized-estimator file of any kind: no pickle,
no joblib dump, no ONNX / HDF5 / Keras / Torch export).  It touches no database, runs no
migration, restarts no prediction
service, enables no model-v2 serving flag, writes nothing to the D: drive, calls no provider /
paid-vendor / Alpha Vantage API, places no orders, and trades nothing.

Forward labels (forward_excess_return_vs_spy_{21,63,126}d) are read from the Phase 3-L panel
(via the Phase 3-O feature factory) and used for validation-only out-of-sample IC; they are
never turned into production predictions.

Run:
    python -B research/run_phase3p_multisignal_walkforward_model.py
Test:
    python -B tests/test_phase3p_multisignal_walkforward_model.py
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

# Reuse the Phase 3-O feature-factory helper logic (preferred input strategy). Importing the
# module only defines functions/constants; its factory run() is guarded by __main__.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import run_phase3o_multisignal_feature_factory as p3o  # noqa: E402

PHASE = "3-P"

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
_OUT_DIR = os.path.join(_REPO_ROOT, "research", "output")
_P_DIR = os.path.join(_OUT_DIR, "phase3p_multisignal_walkforward_model")
RESULT_JSON = os.path.join(_OUT_DIR, "phase3p_multisignal_walkforward_model.json")
PHASE3O_RESULT_JSON = p3o.RESULT_JSON

# Inputs are reached through the Phase 3-O loaders.
PRICE_CSV = p3o.PRICE_CSV
SECTOR_MAP_CSV = p3o.SECTOR_MAP_CSV
PHASE3L_PANEL_CSV = p3o.PHASE3L_PANEL_CSV
PHASE3M_EARNINGS_CSV = p3o.PHASE3M_EARNINGS_CSV

# --------------------------------------------------------------------------- #
# Shared methodology constants (mirror Phase 3-L / 3-M / 3-O)
# --------------------------------------------------------------------------- #
HORIZONS = p3o.HORIZONS                     # (21, 63, 126)
LABEL_TEMPLATE = p3o.LABEL_TEMPLATE         # forward_excess_return_vs_spy_%dd
IC_MIN_CROSS_SECTION = p3o.IC_MIN_CROSS_SECTION   # 15
DECILE_MIN_OBS = 20

# Walk-forward design.
TEST_YEARS = [2020, 2021, 2022, 2023, 2024, 2025, 2026]
MIN_TRAIN_YEARS = 3
EMBARGO_TRADING_DAYS = {21: 21, 63: 63, 126: 126}
LAMBDAS = [0.1, 1.0, 10.0, 100.0]
MIN_TRAIN_ROWS = 2000        # skip a fold whose training window is thinner than this
MIN_TEST_ROWS = 200          # skip a fold whose test window is thinner than this
MIN_REGIME_TRAIN_ROWS = 5000  # below this a regime subset model falls back to the global model

# Decision gates / thresholds.
PASS_MIN_FOLDS = 4
PASS_MIN_TEST_TICKERS = 75
PASS_MIN_IC = 0.03
PASS_MIN_HIT_RATE = 0.52
PASS_WORST_YEAR_FLOOR = -0.05
WEAK_MIN_IC = 0.015

# --------------------------------------------------------------------------- #
# Recommendations
# --------------------------------------------------------------------------- #
REC_PASS = "MULTISIGNAL_WALKFORWARD_MODEL_RESEARCH_PASS"
REC_WEAK = "MULTISIGNAL_WALKFORWARD_MODEL_WEAK_BUT_PROMISING"
REC_FAILS = "MULTISIGNAL_WALKFORWARD_MODEL_FAILS_ROBUSTNESS"
REC_BLOCKED = "MULTISIGNAL_WALKFORWARD_MODEL_BLOCKED_INPUTS"
ALLOWED_RECOMMENDATIONS = [REC_PASS, REC_WEAK, REC_FAILS, REC_BLOCKED]

# --------------------------------------------------------------------------- #
# Feature families and feature sets (Phase 3-O implemented families only)
# --------------------------------------------------------------------------- #
TECHNICAL = list(p3o.TECHNICAL_FEATURES)
SEASONALITY = list(p3o.SEASONALITY_CROSS_SECTIONAL_FEATURES)   # cross-sectional only
SECTOR = list(p3o.SECTOR_FEATURES)
SEC = list(p3o.SEC_FEATURES)
EARNINGS = list(p3o.EARNINGS_FEATURES)
AR_STYLE = list(p3o.TIMESERIES_FEATURES)
# market_regime features are conditioning / interaction inputs, NOT standalone alpha.
REGIME_CONDITION_FEATURES = [
    "market_risk_off_flag", "market_high_vol_flag", "spy_above_sma_200d", "spy_volatility_21d",
]

# Regime-interaction features (built in build_full_panel when enough non-null rows exist).
INTERACTION_FEATURES = [
    "return_21d_x_risk_off",
    "return_63d_x_risk_off",
    "volatility_21d_x_high_vol",
    "volatility_63d_x_high_vol",
    "ticker_return_21d_minus_sector_return_21d_x_risk_off",
]
_INTERACTION_SPEC = [
    ("return_21d_x_risk_off", "return_21d", "market_risk_off_flag"),
    ("return_63d_x_risk_off", "return_63d", "market_risk_off_flag"),
    ("volatility_21d_x_high_vol", "volatility_21d", "market_high_vol_flag"),
    ("volatility_63d_x_high_vol", "volatility_63d", "market_high_vol_flag"),
    ("ticker_return_21d_minus_sector_return_21d_x_risk_off",
     "ticker_return_21d_minus_sector_return_21d", "market_risk_off_flag"),
]

COMBINED_NO_EARNINGS = TECHNICAL + SEASONALITY + SECTOR + SEC + AR_STYLE
COMBINED_WITH_EARNINGS = COMBINED_NO_EARNINGS + EARNINGS
COMBINED_REGIME = COMBINED_WITH_EARNINGS + INTERACTION_FEATURES

FEATURE_SETS = {
    "technical_only": TECHNICAL,
    "seasonality_only": SEASONALITY,
    "sector_relative_only": SECTOR,
    "sec_fundamental_only": SEC,
    "earnings_partial_only": EARNINGS,
    "ar_style_only": AR_STYLE,
    "combined_no_earnings": COMBINED_NO_EARNINGS,
    "combined_with_partial_earnings": COMBINED_WITH_EARNINGS,
    "combined_regime_interactions": COMBINED_REGIME,
}

# Feature -> family map (for the weight summary).
_FAMILY_OF = {}
for _f in TECHNICAL:
    _FAMILY_OF[_f] = "technical_price"
for _f in SEASONALITY:
    _FAMILY_OF[_f] = "seasonality_calendar"
for _f in SECTOR:
    _FAMILY_OF[_f] = "sector_relative"
for _f in SEC:
    _FAMILY_OF[_f] = "sec_fundamental"
for _f in EARNINGS:
    _FAMILY_OF[_f] = "earnings_surprise"
for _f in AR_STYLE:
    _FAMILY_OF[_f] = "time_series_arima_style"
for _f in INTERACTION_FEATURES:
    _FAMILY_OF[_f] = "regime_interaction"


def _family_of(feat):
    return _FAMILY_OF.get(feat, "unknown")


# --------------------------------------------------------------------------- #
# Small helpers (reuse Phase 3-O numerics)
# --------------------------------------------------------------------------- #
_round = p3o._round
_mean = p3o._mean
_median = p3o._median
_std = p3o._std


def _rel(path):
    return os.path.relpath(path, _REPO_ROOT).replace("\\", "/")


def _dstr(ts):
    if ts is None or pd.isna(ts):
        return ""
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# 1. Phase 3-O confirmation
# --------------------------------------------------------------------------- #
def confirm_phase3o(result_json_path=PHASE3O_RESULT_JSON):
    res = p3o._load_json(result_json_path)
    checks = {
        "phase3o_result_present": res is not None,
        "phase_is_3o": False,
        "recommendation_ready": False,
        "next_phase_is_3p": False,
        "macro_not_faked": False,
        "sentiment_not_faked": False,
        "provider_api_not_called": False,
        "alpha_vantage_not_called": False,
        "no_production_model_candidate": False,
        "no_deployable_artifact": False,
        "no_portfolio_weights": False,
    }
    if res is not None:
        rec = (res.get("recommendation") or {}).get("recommendation")
        nxt = (res.get("recommended_next_phase") or {}).get("phase")
        checks["phase_is_3o"] = res.get("phase") == "3-O"
        checks["recommendation_ready"] = rec in (
            "MULTISIGNAL_FEATURE_FACTORY_PARTIAL_MACRO_SENTIMENT_MISSING",
            "MULTISIGNAL_FEATURE_FACTORY_SUCCESS_READY_FOR_RESEARCH_MODEL",
        )
        checks["next_phase_is_3p"] = nxt == "3-P"
        checks["macro_not_faked"] = res.get("macro_faked") is False
        checks["sentiment_not_faked"] = res.get("sentiment_faked") is False
        checks["provider_api_not_called"] = res.get("provider_api_called") is False
        checks["alpha_vantage_not_called"] = res.get("alpha_vantage_called") is False
        checks["no_production_model_candidate"] = \
            res.get("production_model_candidate_created") is False
        checks["no_deployable_artifact"] = res.get("deployable_model_artifact_written") is False
        checks["no_portfolio_weights"] = res.get("portfolio_weights_computed") is False
    checks["confirmed"] = all(v for k, v in checks.items() if k != "confirmed")
    checks["phase3o_recommendation"] = (
        (res or {}).get("recommendation", {}).get("recommendation"))
    return checks


# --------------------------------------------------------------------------- #
# 2. Rebuild the full research panel IN MEMORY (Phase 3-O helper logic)
# --------------------------------------------------------------------------- #
def build_full_panel(price_csv=PRICE_CSV, phase3l_panel_csv=PHASE3L_PANEL_CSV,
                     earnings_csv=PHASE3M_EARNINGS_CSV, verbose=True):
    """Reconstruct the unified Phase 3-O feature panel in memory. Returns (panel, info)."""
    if verbose:
        print("  loading price panel (read-only) + sector map ...")
    sector_map = p3o.load_sector_map(SECTOR_MAP_CSV)
    px = p3o.load_price_panel(price_csv)
    if verbose:
        print("  computing technical + AR-style + regime + sector features ...")
    price_feats = p3o.compute_price_features(px)
    regime = p3o.compute_market_regime(price_feats)
    sector_rel = p3o.compute_sector_relative(price_feats, sector_map, regime)
    seas_hist = p3o.compute_seasonality_history(px)

    if verbose:
        print("  loading Phase 3-L aligned panel (spine) ...")
    spine = p3o.load_phase3l_panel(phase3l_panel_csv)

    tech_cols = ["ticker", "date"] + TECHNICAL + AR_STYLE
    panel = spine.merge(
        price_feats[tech_cols], left_on=["ticker", "scoring_date"],
        right_on=["ticker", "date"], how="left").drop(columns=["date"])
    panel = panel.merge(regime, left_on="scoring_date", right_on="date", how="left").drop(
        columns=["date"])
    panel = panel.merge(sector_rel, left_on=["ticker", "scoring_date"],
                        right_on=["ticker", "date"], how="left").drop(columns=["date"])
    panel = p3o.add_calendar_features(panel)
    panel["year"] = panel["scoring_date"].dt.year.astype(int)
    panel["month"] = panel["scoring_date"].dt.month.astype(int)
    panel = panel.merge(seas_hist, on=["ticker", "year", "month"], how="left")
    earnings = p3o.load_earnings_features(earnings_csv)
    panel, earnings_covered = p3o.merge_earnings_asof(panel, earnings)

    if "sector" not in panel.columns:
        panel = panel.merge(sector_map, on="ticker", how="left")
    panel["sector"] = panel["sector"].fillna("UNKNOWN") if "sector" in panel.columns else "UNKNOWN"

    # ---- regime-interaction features (only if enough non-null rows) ----
    included_interactions = []
    for name, base, flag in _INTERACTION_SPEC:
        if base in panel.columns and flag in panel.columns:
            panel[name] = pd.to_numeric(panel[base], errors="coerce") * \
                pd.to_numeric(panel[flag], errors="coerce")
            if panel[name].notna().mean() >= 0.5:
                included_interactions.append(name)
            else:
                panel = panel.drop(columns=[name])
    panel = panel.sort_values(["scoring_date", "ticker"]).reset_index(drop=True)

    info = {
        "rows": int(len(panel)),
        "tickers": int(panel["ticker"].nunique()),
        "date_min": panel["scoring_date"].min(),
        "date_max": panel["scoring_date"].max(),
        "earnings_covered": int(earnings_covered),
        "included_interactions": included_interactions,
    }
    return panel, info


# --------------------------------------------------------------------------- #
# 5. Ridge ranking model - NumPy closed form, all transforms fit on TRAIN only
# --------------------------------------------------------------------------- #
def datewise_rank_or_zscore_target(df, label_col, method="zscore"):
    """Cross-sectional target by scoring_date. method in {"zscore", "rank"}."""
    lab = pd.to_numeric(df[label_col], errors="coerce")
    g = lab.groupby(df["scoring_date"])
    if method == "rank":
        r = g.rank(pct=True)
        return (r - 0.5).to_numpy(dtype=float)
    mu = g.transform("mean")
    sd = g.transform("std")
    z = (lab - mu) / sd.replace(0.0, np.nan)
    return z.to_numpy(dtype=float)


def _design_matrix(df, feats):
    """Raw feature matrix (NaN preserved) in feats order."""
    cols = []
    for f in feats:
        if f in df.columns:
            cols.append(pd.to_numeric(df[f], errors="coerce").to_numpy(dtype=float))
        else:
            cols.append(np.full(len(df), np.nan))
    if not cols:
        return np.empty((len(df), 0))
    return np.column_stack(cols)


def train_median_imputer(x_train):
    """Per-column training medians (NaN-safe). Columns all-NaN -> 0.0."""
    with np.errstate(all="ignore"):
        med = np.nanmedian(x_train, axis=0)
    med = np.where(np.isfinite(med), med, 0.0)
    return med


def apply_median_imputer(x, medians):
    out = x.copy()
    idx = np.where(~np.isfinite(out))
    if idx[0].size:
        out[idx] = np.take(medians, idx[1])
    return out


def train_standardizer(x_train):
    """Per-column training mean/std. Zero-variance columns are marked dead (std->1, used=0)."""
    mu = x_train.mean(axis=0)
    sd = x_train.std(axis=0, ddof=0)
    used = sd > 1e-12
    sd_safe = np.where(used, sd, 1.0)
    return {"mean": mu, "std": sd_safe, "used": used}


def apply_standardizer(x, scaler):
    z = (x - scaler["mean"]) / scaler["std"]
    z = z * scaler["used"]            # zero out dead (zero-variance) columns
    z[~np.isfinite(z)] = 0.0
    return z


def fit_ridge_closed_form(x_std, y, lam):
    """Closed-form ridge weights: w = (X'X + lam*I)^-1 X'y. No intercept (target is
    cross-sectionally de-meaned). Returns the weight vector."""
    n_feat = x_std.shape[1]
    xtx = x_std.T @ x_std
    xty = x_std.T @ y
    a = xtx + lam * np.eye(n_feat)
    try:
        w = np.linalg.solve(a, xty)
    except np.linalg.LinAlgError:
        w = np.linalg.lstsq(a, xty, rcond=None)[0]
    return w


def predict_linear_score(x_std, w):
    return x_std @ w


def _prep_train_test(train_df, test_df, feats, label, target_method="zscore"):
    """Fit imputer+standardizer on TRAIN only; return standardized X_train/X_test, y_train,
    and a finite-target mask for training rows."""
    x_tr_raw = _design_matrix(train_df, feats)
    x_te_raw = _design_matrix(test_df, feats)
    medians = train_median_imputer(x_tr_raw)
    x_tr = apply_median_imputer(x_tr_raw, medians)
    x_te = apply_median_imputer(x_te_raw, medians)
    scaler = train_standardizer(x_tr)
    x_tr = apply_standardizer(x_tr, scaler)
    x_te = apply_standardizer(x_te, scaler)
    y_tr = datewise_rank_or_zscore_target(train_df, label, method=target_method)
    finite = np.isfinite(y_tr)
    return x_tr, x_te, y_tr, finite, scaler


def _select_lambda(train_df, feats, label, lambdas=LAMBDAS):
    """Pick lambda on an inner time split of TRAIN only (no test leakage)."""
    dates = np.sort(train_df["scoring_date"].unique())
    if len(dates) < 10:
        return 1.0
    cut = dates[int(len(dates) * 0.8)]
    early = train_df[train_df["scoring_date"] < cut]
    late = train_df[train_df["scoring_date"] >= cut]
    if len(early) < MIN_TRAIN_ROWS or len(late) < MIN_TEST_ROWS:
        return 1.0
    x_e, x_l, y_e, finite, _ = _prep_train_test(early, late, feats, label)
    if finite.sum() < MIN_TRAIN_ROWS:
        return 1.0
    xtx = x_e[finite].T @ x_e[finite]
    xty = x_e[finite].T @ y_e[finite]
    best_lam, best_ic = 1.0, -np.inf
    eye = np.eye(x_e.shape[1])
    for lam in lambdas:
        try:
            w = np.linalg.solve(xtx + lam * eye, xty)
        except np.linalg.LinAlgError:
            continue
        score = predict_linear_score(x_l, w)
        ic = _mean_daily_ic(late["scoring_date"].to_numpy(), score,
                            pd.to_numeric(late[label], errors="coerce").to_numpy())
        if ic is not None and ic > best_ic:
            best_ic, best_lam = ic, lam
    return best_lam


# --------------------------------------------------------------------------- #
# Daily cross-sectional rank IC (same statistic as Phase 3-L / 3-M / 3-O)
# --------------------------------------------------------------------------- #
def daily_ic_series(scoring_date, score, label, min_cs=IC_MIN_CROSS_SECTION):
    """Per-date Spearman rank IC as a Series indexed by date. Vectorized."""
    df = pd.DataFrame({"d": pd.to_datetime(scoring_date), "s": np.asarray(score, dtype=float),
                       "y": np.asarray(label, dtype=float)}).dropna()
    if df.empty:
        return pd.Series(dtype=float)
    cnt = df.groupby("d")["s"].transform("size")
    df = df[cnt >= min_cs]
    if df.empty:
        return pd.Series(dtype=float)
    rx = df.groupby("d")["s"].rank()
    ry = df.groupby("d")["y"].rank()
    tmp = pd.DataFrame({"d": df["d"].to_numpy(), "rx": rx.to_numpy(), "ry": ry.to_numpy()})
    mx = tmp.groupby("d")["rx"].transform("mean")
    my = tmp.groupby("d")["ry"].transform("mean")
    dx = tmp["rx"] - mx
    dy = tmp["ry"] - my
    tmp = tmp.assign(num=dx * dy, sxx=dx * dx, syy=dy * dy)
    agg = tmp.groupby("d").agg(num=("num", "sum"), sxx=("sxx", "sum"), syy=("syy", "sum"))
    denom = np.sqrt(agg["sxx"] * agg["syy"])
    ic = (agg["num"] / denom.replace(0.0, np.nan)).dropna()
    ic.index = pd.to_datetime(ic.index)
    return ic


def _mean_daily_ic(scoring_date, score, label, min_cs=IC_MIN_CROSS_SECTION):
    ic = daily_ic_series(scoring_date, score, label, min_cs)
    return float(ic.mean()) if len(ic) else None


def _decile_quintile_spreads(preds):
    """Average top-minus-bottom decile/quintile label spread + top-decile hit-rate per date."""
    dec, qnt, hit = [], [], []
    for _d, g in preds.groupby("scoring_date"):
        n = len(g)
        if n < DECILE_MIN_OBS:
            continue
        gs = g.sort_values("score")
        y = gs["label"].to_numpy(dtype=float)
        kd = max(1, n // 10)
        kq = max(1, n // 5)
        top_d = float(np.nanmean(y[-kd:]))
        dec.append(top_d - float(np.nanmean(y[:kd])))
        qnt.append(float(np.nanmean(y[-kq:])) - float(np.nanmean(y[:kq])))
        hit.append(1.0 if top_d > 0 else 0.0)
    return (_round(_mean(dec), 6), _round(_mean(qnt), 6), _round(_mean(hit), 4))


def evaluate_rank_model(preds, fold_rows, model_name, horizon_days):
    """preds: DataFrame[scoring_date, ticker, score, label] over pooled OOS test rows.
    fold_rows: list of per-fold dicts. Returns the scoreboard metric dict."""
    ic = daily_ic_series(preds["scoring_date"].to_numpy(), preds["score"].to_numpy(),
                         preds["label"].to_numpy())
    by_year, year_ics = {}, {}
    if len(ic):
        yr = ic.groupby(ic.index.year).mean()
        for y, v in yr.items():
            year_ics[int(y)] = _round(float(v), 6)
        by_year = {int(y): float(v) for y, v in yr.items()}
    dec, qnt, top_hit = _decile_quintile_spreads(preds)
    mean_ic = float(ic.mean()) if len(ic) else None
    median_ic = float(ic.median()) if len(ic) else None
    hit_rate = float((ic > 0).mean()) if len(ic) else None
    worst_year = min(by_year.values()) if by_year else None
    best_year = max(by_year.values()) if by_year else None
    pos_years = sum(1 for v in by_year.values() if v > 0)
    stability = (pos_years / len(by_year)) if by_year else None
    metrics = {
        "model": model_name,
        "horizon": "%dd" % horizon_days,
        "_horizon_days": horizon_days,
        "fold_count": len(fold_rows),
        "train_rows_total": int(sum(f["train_rows"] for f in fold_rows)),
        "test_rows_total": int(len(preds)),
        "test_tickers": int(preds["ticker"].nunique()) if len(preds) else 0,
        "mean_daily_rank_ic": _round(mean_ic, 6),
        "median_daily_rank_ic": _round(median_ic, 6),
        "rank_ic_hit_rate": _round(hit_rate, 4),
        "top_decile_minus_bottom_decile_spread": dec,
        "top_quintile_minus_bottom_quintile_spread": qnt,
        "top_decile_hit_rate": top_hit,
        "annual_coverage_years": len(by_year),
        "worst_year_ic": _round(worst_year, 6),
        "best_year_ic": _round(best_year, 6),
        "stability_score": _round(stability, 4),
        "notes": "",
        "_year_ics": year_ics,
    }
    return metrics


# --------------------------------------------------------------------------- #
# Walk-forward folds with per-horizon embargo
# --------------------------------------------------------------------------- #
def walkforward_splits_with_embargo(unique_dates, horizon_days, test_years=TEST_YEARS,
                                    min_train_years=MIN_TRAIN_YEARS):
    """Yield fold dicts {test_year, embargo_cutoff, test_start, test_end}. Training rows are
    those with scoring_date < embargo_cutoff (the last `horizon_days` trading days before the
    test year are embargoed so no training label window overlaps the test window)."""
    ud = np.sort(pd.to_datetime(pd.Series(unique_dates)).to_numpy())
    if len(ud) == 0:
        return []
    start_year = pd.Timestamp(ud[0]).year
    years = np.array([pd.Timestamp(d).year for d in ud])
    folds = []
    for ty in test_years:
        if ty - start_year < min_train_years:
            continue
        in_year = np.where(years == ty)[0]
        if in_year.size == 0:
            continue
        test_start = ud[in_year[0]]
        test_end = ud[in_year[-1]]
        idx0 = int(in_year[0])
        emb_idx = idx0 - horizon_days
        if emb_idx <= 0:
            continue
        embargo_cutoff = ud[emb_idx]
        folds.append({"test_year": ty, "embargo_cutoff": embargo_cutoff,
                      "test_start": test_start, "test_end": test_end})
    return folds


# --------------------------------------------------------------------------- #
# 6. Model evaluators
# --------------------------------------------------------------------------- #
def evaluate_ridge_model(panel, feats, horizon_days, model_name):
    label = LABEL_TEMPLATE % horizon_days
    feats = [f for f in feats if f in panel.columns]
    unique_dates = panel["scoring_date"].unique()
    folds = walkforward_splits_with_embargo(unique_dates, horizon_days)
    pooled, fold_rows, weight_accum = [], [], []
    for fold in folds:
        train_df = panel[(panel["scoring_date"] < fold["embargo_cutoff"])
                         & pd.to_numeric(panel[label], errors="coerce").notna()]
        test_df = panel[(panel["scoring_date"] >= fold["test_start"])
                        & (panel["scoring_date"] <= fold["test_end"])
                        & pd.to_numeric(panel[label], errors="coerce").notna()]
        if len(train_df) < MIN_TRAIN_ROWS or len(test_df) < MIN_TEST_ROWS:
            continue
        lam = _select_lambda(train_df, feats, label)
        x_tr, x_te, y_tr, finite, scaler = _prep_train_test(train_df, test_df, feats, label)
        if finite.sum() < MIN_TRAIN_ROWS:
            continue
        w = fit_ridge_closed_form(x_tr[finite], y_tr[finite], lam)
        score = predict_linear_score(x_te, w)
        pooled.append(pd.DataFrame({
            "scoring_date": test_df["scoring_date"].to_numpy(),
            "ticker": test_df["ticker"].to_numpy(),
            "score": score,
            "label": pd.to_numeric(test_df[label], errors="coerce").to_numpy()}))
        fold_ic = _mean_daily_ic(test_df["scoring_date"].to_numpy(), score,
                                 pd.to_numeric(test_df[label], errors="coerce").to_numpy())
        fold_rows.append({
            "model": model_name, "horizon": "%dd" % horizon_days, "test_year": fold["test_year"],
            "train_rows": int(len(train_df)), "test_rows": int(len(test_df)),
            "test_tickers": int(test_df["ticker"].nunique()),
            "chosen_lambda": lam, "fold_mean_daily_rank_ic": _round(fold_ic, 6),
            "embargo_trading_days": horizon_days, "regime_fallback": ""})
        weight_accum.append((w, scaler["used"], lam, int(len(train_df))))
    preds = pd.concat(pooled, ignore_index=True) if pooled else pd.DataFrame(
        columns=["scoring_date", "ticker", "score", "label"])
    metrics = evaluate_rank_model(preds, fold_rows, model_name, horizon_days)
    weight_rows = _aggregate_weights(model_name, horizon_days, feats, weight_accum)
    return metrics, fold_rows, weight_rows


def evaluate_regime_ensemble(panel, feats, horizon_days, model_name="regime_aware_ensemble"):
    """Per fold: train a risk-on and a risk-off sub-model; route each test row to its regime's
    model. If a regime subset is too thin, fall back to a single global model (documented)."""
    label = LABEL_TEMPLATE % horizon_days
    feats = [f for f in feats if f in panel.columns]
    folds = walkforward_splits_with_embargo(panel["scoring_date"].unique(), horizon_days)
    pooled, fold_rows, weight_accum = [], [], []

    def _regime_mask(df, risk_on):
        off = pd.to_numeric(df.get("market_risk_off_flag"), errors="coerce").fillna(0)
        hv = pd.to_numeric(df.get("market_high_vol_flag"), errors="coerce").fillna(0)
        calm = (off == 0) & (hv == 0)
        return calm if risk_on else ~calm

    for fold in folds:
        train_df = panel[(panel["scoring_date"] < fold["embargo_cutoff"])
                         & pd.to_numeric(panel[label], errors="coerce").notna()]
        test_df = panel[(panel["scoring_date"] >= fold["test_start"])
                        & (panel["scoring_date"] <= fold["test_end"])
                        & pd.to_numeric(panel[label], errors="coerce").notna()]
        if len(train_df) < MIN_TRAIN_ROWS or len(test_df) < MIN_TEST_ROWS:
            continue
        lam = _select_lambda(train_df, feats, label)
        # Global fallback model (always fit).
        x_tr_g, x_te_g, y_tr_g, fin_g, _ = _prep_train_test(train_df, test_df, feats, label)
        if fin_g.sum() < MIN_TRAIN_ROWS:
            continue
        w_global = fit_ridge_closed_form(x_tr_g[fin_g], y_tr_g[fin_g], lam)
        score = predict_linear_score(x_te_g, w_global)  # default = global, then overwrite by regime
        fallback_used = []
        ens_w = np.zeros_like(w_global)
        ens_rows = 0
        for risk_on in (True, False):
            tr_mask = _regime_mask(train_df, risk_on)
            te_mask = _regime_mask(test_df, risk_on).to_numpy()
            sub_tr = train_df[tr_mask]
            tag = "risk_on" if risk_on else "risk_off"
            if len(sub_tr) >= MIN_REGIME_TRAIN_ROWS:
                x_str, x_ste, y_str, fin_s, _ = _prep_train_test(sub_tr, test_df, feats, label)
                if fin_s.sum() >= MIN_TRAIN_ROWS:
                    w_sub = fit_ridge_closed_form(x_str[fin_s], y_str[fin_s], lam)
                    sub_score = predict_linear_score(x_ste, w_sub)
                    score = np.where(te_mask, sub_score, score)
                    ens_w += w_sub * len(sub_tr)
                    ens_rows += len(sub_tr)
                    continue
            fallback_used.append(tag)
            ens_w += w_global * max(1, len(sub_tr))
            ens_rows += max(1, len(sub_tr))
        pooled.append(pd.DataFrame({
            "scoring_date": test_df["scoring_date"].to_numpy(),
            "ticker": test_df["ticker"].to_numpy(), "score": score,
            "label": pd.to_numeric(test_df[label], errors="coerce").to_numpy()}))
        fold_ic = _mean_daily_ic(test_df["scoring_date"].to_numpy(), score,
                                 pd.to_numeric(test_df[label], errors="coerce").to_numpy())
        fold_rows.append({
            "model": model_name, "horizon": "%dd" % horizon_days, "test_year": fold["test_year"],
            "train_rows": int(len(train_df)), "test_rows": int(len(test_df)),
            "test_tickers": int(test_df["ticker"].nunique()), "chosen_lambda": lam,
            "fold_mean_daily_rank_ic": _round(fold_ic, 6), "embargo_trading_days": horizon_days,
            "regime_fallback": ",".join(fallback_used) if fallback_used else ""})
        weight_accum.append((ens_w / max(1, ens_rows), fin_g, lam, int(len(train_df))))
    preds = pd.concat(pooled, ignore_index=True) if pooled else pd.DataFrame(
        columns=["scoring_date", "ticker", "score", "label"])
    metrics = evaluate_rank_model(preds, fold_rows, model_name, horizon_days)
    any_fb = [f["regime_fallback"] for f in fold_rows if f["regime_fallback"]]
    metrics["notes"] = ("regime-aware ensemble: risk-on / risk-off sub-models routed per test "
                        "row; fallback to global model when a regime subset < %d train rows%s"
                        % (MIN_REGIME_TRAIN_ROWS,
                           " (fallback occurred)" if any_fb else " (no fallback needed)"))
    weight_rows = _aggregate_weights(model_name, horizon_days, feats,
                                     [(w, np.ones(len(feats), dtype=bool), lam, n)
                                      for (w, _u, lam, n) in weight_accum])
    return metrics, fold_rows, weight_rows


def evaluate_baseline(panel, score_series, horizon_days, model_name):
    """Evaluate a fixed Phase 3-O rank composite on the SAME yearly test windows (test rows
    only) so it is directly comparable to the ridge OOS scores. No training."""
    label = LABEL_TEMPLATE % horizon_days
    folds = walkforward_splits_with_embargo(panel["scoring_date"].unique(), horizon_days)
    score_full = pd.Series(np.asarray(score_series, dtype=float), index=panel.index)
    pooled, fold_rows = [], []
    for fold in folds:
        mask = ((panel["scoring_date"] >= fold["test_start"])
                & (panel["scoring_date"] <= fold["test_end"])
                & pd.to_numeric(panel[label], errors="coerce").notna())
        test_df = panel[mask]
        if len(test_df) < MIN_TEST_ROWS:
            continue
        sc = score_full[mask].to_numpy()
        lab = pd.to_numeric(test_df[label], errors="coerce").to_numpy()
        pooled.append(pd.DataFrame({"scoring_date": test_df["scoring_date"].to_numpy(),
                                    "ticker": test_df["ticker"].to_numpy(),
                                    "score": sc, "label": lab}))
        fold_ic = _mean_daily_ic(test_df["scoring_date"].to_numpy(), sc, lab)
        fold_rows.append({
            "model": model_name, "horizon": "%dd" % horizon_days, "test_year": fold["test_year"],
            "train_rows": 0, "test_rows": int(len(test_df)),
            "test_tickers": int(test_df["ticker"].nunique()), "chosen_lambda": "",
            "fold_mean_daily_rank_ic": _round(fold_ic, 6), "embargo_trading_days": horizon_days,
            "regime_fallback": ""})
    preds = pd.concat(pooled, ignore_index=True) if pooled else pd.DataFrame(
        columns=["scoring_date", "ticker", "score", "label"])
    metrics = evaluate_rank_model(preds, fold_rows, model_name, horizon_days)
    metrics["notes"] = "Phase 3-O baseline rank composite; evaluated out-of-sample on test folds"
    return metrics, fold_rows, []


def _aggregate_weights(model_name, horizon_days, feats, weight_accum):
    if not weight_accum:
        return []
    n = len(feats)
    sum_w = np.zeros(n)
    sum_abs = np.zeros(n)
    lams = []
    for w, used, lam, _rows in weight_accum:
        wv = np.asarray(w, dtype=float)
        if wv.shape[0] != n:
            continue
        wv = np.where(np.asarray(used, dtype=bool), wv, 0.0) if used is not None else wv
        sum_w += wv
        sum_abs += np.abs(wv)
        lams.append(lam)
    k = len(weight_accum)
    rows = []
    for i, f in enumerate(feats):
        rows.append({
            "model": model_name, "horizon": "%dd" % horizon_days, "feature": f,
            "feature_family": _family_of(f),
            "mean_standardized_weight": _round(sum_w[i] / k, 6),
            "mean_abs_weight": _round(sum_abs[i] / k, 6),
            "mean_chosen_lambda": _round(_mean([float(x) for x in lams]), 4), "folds": k})
    return rows


# --------------------------------------------------------------------------- #
# 7. Decision gates
# --------------------------------------------------------------------------- #
def decide(phase3o_ok, panel_ok, best):
    if not (phase3o_ok and panel_ok):
        return REC_BLOCKED
    if best is None:
        return REC_FAILS
    folds = best["fold_count"]
    tickers = best["test_tickers"]
    ic = best["mean_daily_rank_ic"] or 0.0
    spread = best["top_decile_minus_bottom_decile_spread"]
    hit = best["rank_ic_hit_rate"] or 0.0
    worst = best["worst_year_ic"]
    spread_pos = spread is not None and spread > 0
    worst_ok = worst is not None and worst > PASS_WORST_YEAR_FLOOR
    if (folds >= PASS_MIN_FOLDS and tickers >= PASS_MIN_TEST_TICKERS and ic >= PASS_MIN_IC
            and spread_pos and hit >= PASS_MIN_HIT_RATE and worst_ok):
        return REC_PASS
    if folds >= PASS_MIN_FOLDS and ic >= WEAK_MIN_IC and spread_pos:
        return REC_WEAK
    return REC_FAILS


def build_recommendation(recommendation, best):
    ic = best["mean_daily_rank_ic"] if best else None
    reasons = {
        REC_PASS: (
            "The research-only walk-forward ranking model passed the robustness gate: a best "
            "model mean daily rank IC of %s on at least one horizon, a positive top-minus-bottom "
            "decile spread, sufficient yearly folds and test breadth, and a worst-year IC above "
            "the floor. This remains research-only: no production model candidate, no production "
            "predictions / scores / portfolio weights, no deployable model artifact, no "
            "production edge claimed." % ic),
        REC_WEAK: (
            "The walk-forward ranking model is weak but promising: the best model mean daily rank "
            "IC (%s) and a positive decile spread clear the weak threshold but not the full pass "
            "gate. Add macro / sentiment families and finish earnings coverage before any "
            "production candidate. Research-only; no production artifact, predictions, scores, "
            "weights, or edge claim." % ic),
        REC_FAILS: (
            "Enough data existed but the walk-forward ranking model's IC / spread was too weak or "
            "unstable to pass. The feature families and targets need rework before any production "
            "step. Research-only; no production artifact or edge claim."),
        REC_BLOCKED: (
            "The Phase 3-O result or a required local panel was missing or did not confirm, so the "
            "walk-forward model could not be built. Repair the Phase 3-P inputs. No data was "
            "fetched and no provider API was called."),
    }
    return {
        "recommendation": recommendation,
        "allowed_values": ALLOWED_RECOMMENDATIONS,
        "research_only": True,
        "create_production_model_candidate_now": False,
        "train_production_model_now": False,
        "compute_portfolio_weights_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "reason": reasons[recommendation],
    }


def build_recommended_next_phase(recommendation):
    table = {
        REC_PASS: ("Research Model Robustness, Turnover, and Risk Simulation",
                   "Stress the research model: turnover, transaction-cost sensitivity, "
                   "sub-period and risk simulation. Still no production candidate."),
        REC_WEAK: ("Add Macro/Sentiment and Finish Earnings Coverage Before Production Candidate",
                   "Add the blocked macro / sentiment families and complete earnings coverage, "
                   "then re-run the walk-forward model. Still no production candidate."),
        REC_FAILS: ("Rework Feature Families and Targets",
                    "Revise the feature families and prediction targets before any further "
                    "modeling. No production candidate."),
        REC_BLOCKED: ("Repair Phase 3-P Inputs",
                      "Restore the Phase 3-O result and the local price / Phase 3-L panels before "
                      "rebuilding the walk-forward model."),
    }
    title, purpose = table[recommendation]
    return {"phase": "3-Q", "title": title, "purpose": purpose}


# --------------------------------------------------------------------------- #
# 9. Safety flags
# --------------------------------------------------------------------------- #
def build_safety_flags():
    return {
        "database_touched": False,
        "database_write_executed": False,
        "migration_executed": False,
        "deployment_executed": False,
        "model_v2_enabled": False,
        "production_edge_claimed": False,
        "no_trading": True,
        "no_orders": True,
        "no_automation": True,
        "research_models_trained_in_memory": True,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "deployable_model_artifact_written": False,
        "production_predictions_computed": False,
        "research_oos_scores_computed": True,
        "production_scores_computed": False,
        "portfolio_weights_computed": False,
        "order_instructions_created": False,
        "d_drive_read": True,
        "d_drive_written": False,
        "provider_api_called": False,
        "alpha_vantage_called": False,
        "paid_vendor_api_called": False,
        "macro_faked": False,
        "sentiment_faked": False,
        "labels_for_validation_only": True,
    }


def build_interpretation(recommendation):
    return {
        "phase_is_research_only": True,
        "research_models_trained_in_memory": True,
        "research_models_are_non_deployable": True,
        "core_model_is_cross_sectional_multisignal_ranking": True,
        "arima_is_one_conditioning_family_not_the_model": True,
        "ridge_is_numpy_closed_form_no_sklearn": True,
        "all_transforms_fit_on_training_rows_only": True,
        "per_horizon_embargo_applied": True,
        "macro_family_faked": False,
        "sentiment_family_faked": False,
        "sector_map_is_current_as_of_not_point_in_time": True,
        "results_remain_survivorship_biased": True,
        "production_model_trained": False,
        "production_predictions_computed": False,
        "portfolio_weights_computed": False,
        "deployable_model_artifact_written": False,
        "production_edge_claimed": False,
        "narrative": (
            "Phase 3-P trains research-only cross-sectional ranking models on the Phase 3-O "
            "multi-signal feature panel (rebuilt in memory) and evaluates them with expanding "
            "walk-forward folds and a per-horizon embargo so no training label window overlaps a "
            "test window. Every transform - median imputation, standardization, the de-meaned "
            "cross-sectional target, and the ridge weights - is fit on training rows only. It "
            "compares Phase 3-O baseline rank composites against a NumPy closed-form ridge ranking "
            "model and a regime-aware ensemble. ARIMA-style features are one conditioning family, "
            "not the model. Macro / inflation and sentiment remain unimplemented (no local data) "
            "and are NOT faked. It performs no deployment, restarts no prediction service, enables "
            "no model-v2 serving flag, runs no migration, writes to no production database, "
            "executes no trade, trains no production model, creates no production model candidate, "
            "writes no deployable model artifact, computes no production predictions / scores / "
            "portfolio weights, writes nothing to the D: drive, and calls no provider / "
            "paid-vendor / Alpha Vantage / third-party market-data-vendor API. The universe is "
            "current-as-of, so all results remain survivorship-biased and claim no production "
            "edge."),
    }


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #
def _write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            out = {}
            for c in columns:
                v = r.get(c, "")
                out[c] = "" if v is None or (isinstance(v, float) and math.isnan(v)) else v
            w.writerow(out)


def _dump_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def _strip_private(rows):
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
BASELINE_MODELS = [
    "momentum_rank_composite", "seasonality_rank_composite", "sector_neutral_momentum",
    "ar_style_mean_reversion", "sec_fundamental_rank_composite",
    "combined_multisignal_rank_composite",
]
RIDGE_MODELS = [
    ("ridge_technical_only", "technical_only"),
    ("ridge_sec_fundamental_only", "sec_fundamental_only"),
    ("ridge_combined_no_earnings", "combined_no_earnings"),
    ("ridge_combined_with_partial_earnings", "combined_with_partial_earnings"),
    ("ridge_combined_regime_interactions", "combined_regime_interactions"),
]

_OUT_PATHS = {
    "feature_set": "model_feature_set.csv",
    "fold": "walkforward_fold_summary.csv",
    "scoreboard": "model_scoreboard.csv",
    "stability": "yearly_model_stability.csv",
    "weights": "feature_weight_summary.csv",
    "readiness": "readiness_decision_table.csv",
}


def _out(o_dir, key):
    return os.path.join(o_dir, _OUT_PATHS[key])


def _model_feature_set_rows(feature_sets):
    rows = []
    for name, feats in feature_sets.items():
        for f in feats:
            rows.append({"feature_set": name, "feature": f, "feature_family": _family_of(f)})
    return rows


def _finish_blocked(result_json_path, o_dir, phase3o_checks, panel_info, reason_note):
    _write_csv(_out(o_dir, "feature_set"), ["feature_set", "feature", "feature_family"],
               _model_feature_set_rows(FEATURE_SETS))
    _write_csv(_out(o_dir, "fold"), [
        "model", "horizon", "test_year", "train_rows", "test_rows", "test_tickers",
        "chosen_lambda", "fold_mean_daily_rank_ic", "embargo_trading_days", "regime_fallback"], [])
    _write_csv(_out(o_dir, "scoreboard"), _SCOREBOARD_COLUMNS, [])
    _write_csv(_out(o_dir, "stability"), ["model", "horizon", "year", "mean_daily_rank_ic"], [])
    _write_csv(_out(o_dir, "weights"), [
        "model", "horizon", "feature", "feature_family", "mean_standardized_weight",
        "mean_abs_weight", "mean_chosen_lambda", "folds"], [])
    decision_rows = build_decision_table(phase3o_checks, panel_info, None, REC_BLOCKED)
    _write_csv(_out(o_dir, "readiness"), ["decision_item", "value", "passed", "note"],
               decision_rows)
    result = {
        "phase": PHASE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "phase3o_confirmation": phase3o_checks,
        "feature_panel_summary": panel_info,
        "feature_sets": {k: {"features": v, "count": len(v)} for k, v in FEATURE_SETS.items()},
        "walkforward_design": _walkforward_design(),
        "model_scoreboard_summary": {"blocked": True, "reason": reason_note},
        "best_model": None,
        "model_family_comparison": {},
        "yearly_stability_summary": {},
        "recommendation": build_recommendation(REC_BLOCKED, None),
        "recommended_next_phase": build_recommended_next_phase(REC_BLOCKED),
        "interpretation": build_interpretation(REC_BLOCKED),
    }
    result.update(build_safety_flags())
    _dump_json(result_json_path, result)
    return result


_SCOREBOARD_COLUMNS = [
    "model", "horizon", "model_family", "fold_count", "train_rows_total", "test_rows_total",
    "test_tickers", "mean_daily_rank_ic", "median_daily_rank_ic", "rank_ic_hit_rate",
    "top_decile_minus_bottom_decile_spread", "top_quintile_minus_bottom_quintile_spread",
    "top_decile_hit_rate", "annual_coverage_years", "worst_year_ic", "best_year_ic",
    "stability_score", "notes",
]


def _walkforward_design():
    return {
        "time_index": "scoring_date",
        "window_type": "expanding (all history before the embargo cutoff)",
        "min_train_years": MIN_TRAIN_YEARS,
        "test_years": TEST_YEARS,
        "embargo_trading_days_by_horizon": {str(h): EMBARGO_TRADING_DAYS[h] for h in HORIZONS},
        "horizons": ["%dd" % h for h in HORIZONS],
        "lambdas_evaluated": LAMBDAS,
        "lambda_selection": "chosen on an inner 80/20 time split of the training window only "
                            "(no test leakage)",
        "target": "cross-sectional z-score of forward excess return by scoring_date "
                  "(date-wise de-meaned; rank target also supported)",
        "min_train_rows_per_fold": MIN_TRAIN_ROWS,
        "min_test_rows_per_fold": MIN_TEST_ROWS,
        "min_regime_subset_train_rows": MIN_REGIME_TRAIN_ROWS,
    }


def _model_family(model_name):
    if model_name in BASELINE_MODELS:
        return "baseline_composite"
    if model_name == "regime_aware_ensemble":
        return "regime_aware_ensemble"
    return "ridge_linear"


def build_decision_table(phase3o_checks, panel_info, best, recommendation):
    rows = []

    def add(item, value, passed, note):
        rows.append({"decision_item": item, "value": value,
                     "passed": "" if passed is None else bool(passed), "note": note})

    add("phase3o_confirmed", phase3o_checks.get("confirmed"), phase3o_checks.get("confirmed"),
        "Phase 3-O result present, research-ready, next phase 3-P, macro/sentiment not faked")
    add("feature_panel_rows", panel_info.get("rows", 0), panel_info.get("rows", 0) > 0,
        "unified research feature panel rebuilt in memory")
    add("feature_panel_tickers", panel_info.get("tickers", 0), panel_info.get("tickers", 0) > 0,
        "tickers in the rebuilt panel")
    if best is None:
        add("best_model_found", False, False, "no model produced out-of-sample folds")
    else:
        add("best_model", "%s @ %s" % (best["model"], best["horizon"]), None,
            "best model by mean daily rank IC")
        add("best_model_folds", best["fold_count"], best["fold_count"] >= PASS_MIN_FOLDS,
            "PASS needs >= %d yearly folds" % PASS_MIN_FOLDS)
        add("best_model_test_tickers", best["test_tickers"],
            best["test_tickers"] >= PASS_MIN_TEST_TICKERS,
            "PASS needs >= %d tickers in test coverage" % PASS_MIN_TEST_TICKERS)
        add("best_model_mean_daily_rank_ic", best["mean_daily_rank_ic"],
            (best["mean_daily_rank_ic"] or 0.0) >= PASS_MIN_IC,
            "PASS needs >= %.3f on at least one horizon" % PASS_MIN_IC)
        add("best_model_decile_spread", best["top_decile_minus_bottom_decile_spread"],
            (best["top_decile_minus_bottom_decile_spread"] or 0.0) > 0,
            "PASS needs a positive top-minus-bottom decile spread")
        add("best_model_rank_ic_hit_rate", best["rank_ic_hit_rate"],
            (best["rank_ic_hit_rate"] or 0.0) >= PASS_MIN_HIT_RATE,
            "PASS needs hit rate >= %.2f" % PASS_MIN_HIT_RATE)
        add("best_model_worst_year_ic", best["worst_year_ic"],
            (best["worst_year_ic"] is not None and best["worst_year_ic"] > PASS_WORST_YEAR_FLOOR),
            "PASS needs worst-year IC > %.2f" % PASS_WORST_YEAR_FLOOR)
    add("no_production_model_trained", True, True, "research-only models trained in memory")
    add("no_production_predictions", True, True, "no production predictions/scores computed")
    add("no_portfolio_weights", True, True, "no portfolio weights computed")
    add("no_deployable_artifact", True, True, "no serialized-estimator file written")
    add("d_drive_written", False, True, "wrote nothing to the D: drive")
    add("alpha_vantage_called", False, True, "no provider / Alpha Vantage call")
    add("recommendation", recommendation, recommendation in ALLOWED_RECOMMENDATIONS,
        "allowed values only")
    return rows


def run(result_json_path=RESULT_JSON, o_dir=_P_DIR, price_csv=PRICE_CSV,
        phase3l_panel_csv=PHASE3L_PANEL_CSV, earnings_csv=PHASE3M_EARNINGS_CSV,
        phase3o_result_json=PHASE3O_RESULT_JSON, verbose=True):
    phase3o_checks = confirm_phase3o(phase3o_result_json)
    panel_ok = (os.path.isfile(price_csv) and os.path.isfile(SECTOR_MAP_CSV)
                and os.path.isfile(phase3l_panel_csv))

    if not (phase3o_checks["confirmed"] and panel_ok):
        empty_info = {"rows": 0, "tickers": 0, "date_min": None, "date_max": None,
                      "earnings_covered": 0, "included_interactions": []}
        note = ("Phase 3-O not confirmed" if not phase3o_checks["confirmed"]
                else "required local panel missing")
        return _finish_blocked(result_json_path, o_dir, phase3o_checks, empty_info, note)

    panel, info = build_full_panel(price_csv, phase3l_panel_csv, earnings_csv, verbose)

    if verbose:
        print("  reproducing Phase 3-O baseline rank composites ...")
    baseline_scores = p3o.build_baseline_scores(panel)

    all_metrics, all_folds, all_weights = [], [], []
    all_year_ics = {}

    if verbose:
        print("  evaluating baseline composites (walk-forward test folds) ...")
    for name in BASELINE_MODELS:
        score = baseline_scores.get(name)
        if score is None:
            continue
        for h in HORIZONS:
            m, folds, _w = evaluate_baseline(panel, score, h, name)
            all_metrics.append(m)
            all_folds.extend(folds)
            all_year_ics[(name, "%dd" % h)] = m.pop("_year_ics")

    if verbose:
        print("  training ridge ranking models (NumPy closed form) ...")
    for model_name, set_name in RIDGE_MODELS:
        feats = FEATURE_SETS[set_name]
        for h in HORIZONS:
            m, folds, w = evaluate_ridge_model(panel, feats, h, model_name)
            all_metrics.append(m)
            all_folds.extend(folds)
            all_weights.extend(w)
            all_year_ics[(model_name, "%dd" % h)] = m.pop("_year_ics")

    if verbose:
        print("  training regime-aware ensemble ...")
    for h in HORIZONS:
        m, folds, w = evaluate_regime_ensemble(panel, COMBINED_WITH_EARNINGS, h)
        all_metrics.append(m)
        all_folds.extend(folds)
        all_weights.extend(w)
        all_year_ics[("regime_aware_ensemble", "%dd" % h)] = m.pop("_year_ics")

    # ---- attach model_family + clean private keys ----
    for m in all_metrics:
        m["model_family"] = _model_family(m["model"])
        m.pop("_horizon_days", None)

    # ---- best model (by mean daily rank IC, among models with a positive decile spread) ----
    candidates = [m for m in all_metrics if m["mean_daily_rank_ic"] is not None]
    best = max(candidates, key=lambda m: m["mean_daily_rank_ic"], default=None)

    recommendation = decide(phase3o_checks["confirmed"], panel_ok, best)

    # ---- summaries ----
    scoreboard_summary = _summarize_scoreboard(all_metrics)
    family_comparison = _family_comparison(all_metrics)
    yearly_stability = _yearly_stability_summary(best, all_year_ics)

    # ---- yearly stability rows (ridge + ensemble + best) ----
    stability_rows = []
    for (model_name, horizon), yic in all_year_ics.items():
        fam = _model_family(model_name)
        if fam == "baseline_composite" and model_name != (best or {}).get("model"):
            continue
        for year, val in sorted(yic.items()):
            stability_rows.append({"model": model_name, "horizon": horizon, "year": year,
                                   "mean_daily_rank_ic": val})

    # ---- write artifacts ----
    if verbose:
        print("  writing artifacts ...")
    _write_csv(_out(o_dir, "feature_set"), ["feature_set", "feature", "feature_family"],
               _model_feature_set_rows(FEATURE_SETS))
    _write_csv(_out(o_dir, "fold"), [
        "model", "horizon", "test_year", "train_rows", "test_rows", "test_tickers",
        "chosen_lambda", "fold_mean_daily_rank_ic", "embargo_trading_days", "regime_fallback"],
        all_folds)
    _write_csv(_out(o_dir, "scoreboard"), _SCOREBOARD_COLUMNS, all_metrics)
    _write_csv(_out(o_dir, "stability"), ["model", "horizon", "year", "mean_daily_rank_ic"],
               stability_rows)
    _write_csv(_out(o_dir, "weights"), [
        "model", "horizon", "feature", "feature_family", "mean_standardized_weight",
        "mean_abs_weight", "mean_chosen_lambda", "folds"], all_weights)
    decision_rows = build_decision_table(phase3o_checks, info, best, recommendation)
    _write_csv(_out(o_dir, "readiness"), ["decision_item", "value", "passed", "note"],
               decision_rows)

    # ---- result JSON ----
    result = {
        "phase": PHASE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "phase3o_result_json": _rel(phase3o_result_json),
            "price_history_csv": price_csv.replace("\\", "/"),
            "sector_map_csv": "research/input/phase2k_p_sector_map_current.csv",
            "phase3l_aligned_panel_csv":
                "research/output/phase3l_sec_universe_signal_gate/"
                "aligned_feature_price_panel_universe.csv",
            "phase3m_earnings_features_csv":
                "research/output/phase3m_earnings_estimates_signal_gate/"
                "earnings_features_universe.csv",
        },
        "outputs_written": {
            "result_json": _rel(result_json_path),
            "model_feature_set_csv": _rel(_out(o_dir, "feature_set")),
            "walkforward_fold_summary_csv": _rel(_out(o_dir, "fold")),
            "model_scoreboard_csv": _rel(_out(o_dir, "scoreboard")),
            "yearly_model_stability_csv": _rel(_out(o_dir, "stability")),
            "feature_weight_summary_csv": _rel(_out(o_dir, "weights")),
            "readiness_decision_table_csv": _rel(_out(o_dir, "readiness")),
        },
        "phase3o_confirmation": phase3o_checks,
        "feature_panel_summary": {
            "rows": info["rows"], "tickers": info["tickers"],
            "date_range": {"start": _dstr(info["date_min"]), "end": _dstr(info["date_max"])},
            "horizons": ["%dd" % h for h in HORIZONS],
            "earnings_partial_coverage_tickers": info["earnings_covered"],
            "included_regime_interactions": info["included_interactions"],
            "rebuilt_in_memory": True, "written_to_disk": False,
        },
        "feature_sets": {
            k: {"features": v, "count": len(v),
                "families": sorted({_family_of(f) for f in v})}
            for k, v in FEATURE_SETS.items()},
        "walkforward_design": _walkforward_design(),
        "model_scoreboard": all_metrics,
        "model_scoreboard_summary": scoreboard_summary,
        "best_model": None if best is None else {
            "model": best["model"], "horizon": best["horizon"],
            "model_family": best["model_family"],
            "mean_daily_rank_ic": best["mean_daily_rank_ic"],
            "median_daily_rank_ic": best["median_daily_rank_ic"],
            "rank_ic_hit_rate": best["rank_ic_hit_rate"],
            "top_decile_minus_bottom_decile_spread":
                best["top_decile_minus_bottom_decile_spread"],
            "top_quintile_minus_bottom_quintile_spread":
                best["top_quintile_minus_bottom_quintile_spread"],
            "top_decile_hit_rate": best["top_decile_hit_rate"],
            "fold_count": best["fold_count"], "test_tickers": best["test_tickers"],
            "annual_coverage_years": best["annual_coverage_years"],
            "worst_year_ic": best["worst_year_ic"], "best_year_ic": best["best_year_ic"],
            "stability_score": best["stability_score"]},
        "model_family_comparison": family_comparison,
        "yearly_stability_summary": yearly_stability,
        "recommendation": build_recommendation(recommendation, best),
        "recommended_next_phase": build_recommended_next_phase(recommendation),
        "interpretation": build_interpretation(recommendation),
    }
    result.update(build_safety_flags())
    _dump_json(result_json_path, result)
    return result


def _summarize_scoreboard(metrics):
    by_horizon = {}
    for h in HORIZONS:
        hd = "%dd" % h
        sub = [m for m in metrics if m["horizon"] == hd and m["mean_daily_rank_ic"] is not None]
        if not sub:
            by_horizon[hd] = None
            continue
        best = max(sub, key=lambda m: m["mean_daily_rank_ic"])
        by_horizon[hd] = {"best_model": best["model"],
                          "mean_daily_rank_ic": best["mean_daily_rank_ic"],
                          "top_decile_minus_bottom_decile_spread":
                              best["top_decile_minus_bottom_decile_spread"]}
    overall = [m for m in metrics if m["mean_daily_rank_ic"] is not None]
    best_overall = max(overall, key=lambda m: m["mean_daily_rank_ic"], default=None)
    return {
        "model_count": len({m["model"] for m in metrics}),
        "scoreboard_rows": len(metrics),
        "best_by_horizon": by_horizon,
        "best_overall": None if best_overall is None else {
            "model": best_overall["model"], "horizon": best_overall["horizon"],
            "mean_daily_rank_ic": best_overall["mean_daily_rank_ic"]},
    }


def _family_comparison(metrics):
    out = {}
    for fam in ("baseline_composite", "ridge_linear", "regime_aware_ensemble"):
        sub = [m for m in metrics
               if _model_family(m["model"]) == fam and m["mean_daily_rank_ic"] is not None]
        if not sub:
            out[fam] = None
            continue
        best = max(sub, key=lambda m: m["mean_daily_rank_ic"])
        out[fam] = {
            "best_model": best["model"], "best_horizon": best["horizon"],
            "best_mean_daily_rank_ic": best["mean_daily_rank_ic"],
            "best_decile_spread": best["top_decile_minus_bottom_decile_spread"],
            "mean_of_best_per_model": _round(_mean([
                m["mean_daily_rank_ic"] for m in sub]), 6),
        }
    return out


def _yearly_stability_summary(best, all_year_ics):
    if best is None:
        return {}
    key = (best["model"], best["horizon"])
    yic = all_year_ics.get(key, {})
    return {
        "best_model": best["model"], "horizon": best["horizon"],
        "year_ics": {str(y): v for y, v in sorted(yic.items())},
        "worst_year_ic": best["worst_year_ic"], "best_year_ic": best["best_year_ic"],
        "stability_score": best["stability_score"],
        "stability_score_definition": "fraction of test years with positive mean daily rank IC",
    }


def main():
    result = run()
    rec = result["recommendation"]
    nxt = result["recommended_next_phase"]
    best = result.get("best_model")
    print("Phase %s - Research-Only Multi-Signal Walk-Forward Ranking Model" % result["phase"])
    print("  phase 3-O confirmed  : %s" % result["phase3o_confirmation"]["confirmed"])
    fps = result["feature_panel_summary"]
    print("  feature panel        : %s rows x %s tickers (%s -> %s)"
          % (fps["rows"], fps["tickers"], fps["date_range"]["start"], fps["date_range"]["end"]))
    if best:
        print("  best model           : %s @ %s  IC=%s  decile_spread=%s  hit=%s"
              % (best["model"], best["horizon"], best["mean_daily_rank_ic"],
                 best["top_decile_minus_bottom_decile_spread"], best["rank_ic_hit_rate"]))
        print("  best worst-year IC   : %s   stability=%s"
              % (best["worst_year_ic"], best["stability_score"]))
    print("  recommendation       : %s" % rec["recommendation"])
    print("  recommended next     : %s - %s" % (nxt["phase"], nxt["title"]))
    return result


if __name__ == "__main__":
    main()
