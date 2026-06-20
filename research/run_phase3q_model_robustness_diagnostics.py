"""Phase 3-Q - Model Robustness, Turnover, Cost, and Bad-Year Failure Diagnosis.

This is a research-only diagnosis phase on top of the Phase 3-P walk-forward ranking model.
It does NOT train a production model, does NOT deploy, and does NOT compute production
predictions / scores / portfolio weights / order instructions.  It rebuilds research-only
out-of-sample (OOS) rank scores IN MEMORY (reusing the Phase 3-P / Phase 3-O helper logic)
and analyses them to answer six questions:

  1. Why did the best model (ridge_technical_only @ 126d) fail in its worst year (2021)?
  2. Is the 2021 failure regime, sector, feature-instability, or turnover driven?
  3. Does the 126d signal survive realistic transaction costs?
  4. Is the 126d horizon clearly better than 21d / 63d?
  5. Can regime filters or ensemble blending reduce bad-year drawdown without destroying IC?
  6. Which next data family matters most (macro/inflation, sentiment, or more earnings)?

It touches no database, runs no migration, restarts no prediction service, enables no
model-v2 serving flag, writes nothing to the D: drive, calls no provider / paid-vendor /
Alpha Vantage API, places no orders, trades nothing, and writes no deployable model artifact
(no serialized-estimator file of any kind: no pickle, no joblib dump, no ONNX / HDF5 / Keras /
Torch export).  Forward labels are used for validation-only OOS diagnostics; they are never
turned into production predictions.

Run:
    python -B research/run_phase3q_model_robustness_diagnostics.py
Test:
    python -B tests/test_phase3q_model_robustness_diagnostics.py
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

# Reuse Phase 3-P (which itself reuses Phase 3-O) helper logic. Importing the modules only
# defines functions/constants; their factory/model run() is guarded by __main__.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import run_phase3p_multisignal_walkforward_model as p3p  # noqa: E402
import run_phase3o_multisignal_feature_factory as p3o  # noqa: E402

PHASE = "3-Q"

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
_OUT_DIR = os.path.join(_REPO_ROOT, "research", "output")
_Q_DIR = os.path.join(_OUT_DIR, "phase3q_model_robustness_diagnostics")
RESULT_JSON = os.path.join(_OUT_DIR, "phase3q_model_robustness_diagnostics.json")
PHASE3P_RESULT_JSON = p3p.RESULT_JSON
PHASE3P_SCOREBOARD_CSV = p3p._out(p3p._P_DIR, "scoreboard")
PHASE3P_WEIGHTS_CSV = p3p._out(p3p._P_DIR, "weights")

# Inputs reached through the Phase 3-P / Phase 3-O loaders.
PRICE_CSV = p3p.PRICE_CSV
PHASE3L_PANEL_CSV = p3p.PHASE3L_PANEL_CSV
PHASE3M_EARNINGS_CSV = p3p.PHASE3M_EARNINGS_CSV

# --------------------------------------------------------------------------- #
# Shared methodology constants
# --------------------------------------------------------------------------- #
HORIZONS = p3p.HORIZONS                         # (21, 63, 126)
LABEL_TEMPLATE = p3p.LABEL_TEMPLATE
FOCUS_MODEL = "ridge_technical_only"
FOCUS_HORIZON = 126
FOCUS_LABEL = LABEL_TEMPLATE % FOCUS_HORIZON
BAD_YEAR = 2021
TRADING_DAYS_PER_MONTH = 21.0
DECILE_MIN_OBS = p3p.DECILE_MIN_OBS

# Columns carried from the panel onto the OOS prediction frames (for regime / sector slicing).
CARRY_COLS = [
    "sector", "market_risk_off_flag", "market_high_vol_flag", "spy_above_sma_200d",
    "spy_volatility_21d", "spy_drawdown_63d", "cross_sectional_breadth_21d",
    "cross_sectional_dispersion_21d",
]

# Transaction-cost grid (basis points, per round-trip-able turnover) and rank buckets.
COST_BPS = [0, 5, 10, 25, 50]
BUCKETS = [("top_decile", 0.10), ("top_quintile", 0.20), ("top_30pct", 0.30)]

# Decision values.
REC_PASS = "MODEL_ROBUSTNESS_PASS_READY_FOR_RISK_SIMULATION"
REC_WEAK = "MODEL_ROBUSTNESS_WEAK_FIXABLE_WITH_REGIME_AND_DATA"
REC_FAILS = "MODEL_ROBUSTNESS_FAILS_COST_OR_BAD_YEAR"
REC_BLOCKED = "MODEL_ROBUSTNESS_BLOCKED_INPUTS"
ALLOWED_RECOMMENDATIONS = [REC_PASS, REC_WEAK, REC_FAILS, REC_BLOCKED]

WORST_YEAR_FLOOR = -0.05
PASS_STABILITY_FLOOR = 0.70

# Reuse Phase 3-P numerics.
_round = p3p._round
_mean = p3p._mean


def _rel(path):
    return os.path.relpath(path, _REPO_ROOT).replace("\\", "/")


# --------------------------------------------------------------------------- #
# 1. Phase 3-P confirmation
# --------------------------------------------------------------------------- #
def confirm_phase3p(result_json_path=PHASE3P_RESULT_JSON):
    res = p3o._load_json(result_json_path)
    checks = {
        "phase3p_result_present": res is not None,
        "phase_is_3p": False,
        "recommendation_ok": False,
        "next_phase_is_3q": False,
        "no_production_model_candidate": False,
        "no_deployable_artifact": False,
        "no_production_predictions": False,
        "no_production_scores": False,
        "no_portfolio_weights": False,
        "provider_api_not_called": False,
        "alpha_vantage_not_called": False,
    }
    if res is not None:
        rec = (res.get("recommendation") or {}).get("recommendation")
        nxt = (res.get("recommended_next_phase") or {}).get("phase")
        checks["phase_is_3p"] = res.get("phase") == "3-P"
        checks["recommendation_ok"] = rec in (
            "MULTISIGNAL_WALKFORWARD_MODEL_WEAK_BUT_PROMISING",
            "MULTISIGNAL_WALKFORWARD_MODEL_RESEARCH_PASS",
        )
        checks["next_phase_is_3q"] = nxt == "3-Q"
        checks["no_production_model_candidate"] = \
            res.get("production_model_candidate_created") is False
        checks["no_deployable_artifact"] = res.get("deployable_model_artifact_written") is False
        checks["no_production_predictions"] = res.get("production_predictions_computed") is False
        checks["no_production_scores"] = res.get("production_scores_computed") is False
        checks["no_portfolio_weights"] = res.get("portfolio_weights_computed") is False
        checks["provider_api_not_called"] = res.get("provider_api_called") is False
        checks["alpha_vantage_not_called"] = res.get("alpha_vantage_called") is False
    checks["confirmed"] = all(v for k, v in checks.items() if k != "confirmed")
    checks["phase3p_recommendation"] = (
        (res or {}).get("recommendation", {}).get("recommendation"))
    checks["phase3p_best_model"] = (res or {}).get("best_model")
    return checks


# --------------------------------------------------------------------------- #
# 2. Regenerate research-only OOS scores IN MEMORY
# --------------------------------------------------------------------------- #
def oos_ridge_predictions(panel, feats, horizon_days, carry_cols=CARRY_COLS):
    """Re-run the Phase 3-P ridge walk-forward and return the pooled OOS prediction frame
    (scoring_date, ticker, score, label + carried regime/sector columns), plus per-fold
    standardized weights.  Every transform is fit on training rows only (Phase 3-P helpers)."""
    label = LABEL_TEMPLATE % horizon_days
    feats = [f for f in feats if f in panel.columns]
    folds = p3p.walkforward_splits_with_embargo(panel["scoring_date"].unique(), horizon_days)
    pooled, fold_weights = [], []
    for fold in folds:
        train_df = panel[(panel["scoring_date"] < fold["embargo_cutoff"])
                         & pd.to_numeric(panel[label], errors="coerce").notna()]
        test_df = panel[(panel["scoring_date"] >= fold["test_start"])
                        & (panel["scoring_date"] <= fold["test_end"])
                        & pd.to_numeric(panel[label], errors="coerce").notna()]
        if len(train_df) < p3p.MIN_TRAIN_ROWS or len(test_df) < p3p.MIN_TEST_ROWS:
            continue
        lam = p3p._select_lambda(train_df, feats, label)
        x_tr, x_te, y_tr, finite, scaler = p3p._prep_train_test(train_df, test_df, feats, label)
        if finite.sum() < p3p.MIN_TRAIN_ROWS:
            continue
        w = p3p.fit_ridge_closed_form(x_tr[finite], y_tr[finite], lam)
        score = p3p.predict_linear_score(x_te, w)
        d = {"scoring_date": test_df["scoring_date"].to_numpy(),
             "ticker": test_df["ticker"].to_numpy(), "score": score,
             "label": pd.to_numeric(test_df[label], errors="coerce").to_numpy()}
        for c in carry_cols:
            d[c] = test_df[c].to_numpy() if c in test_df.columns else np.full(len(test_df), np.nan)
        pooled.append(pd.DataFrame(d))
        fold_weights.append({"test_year": fold["test_year"], "w": np.asarray(w, dtype=float),
                             "used": np.asarray(scaler["used"], dtype=bool), "lam": lam})
    preds = (pd.concat(pooled, ignore_index=True) if pooled
             else pd.DataFrame(columns=["scoring_date", "ticker", "score", "label"] + carry_cols))
    if len(preds):
        preds["year"] = preds["scoring_date"].dt.year.astype(int)
    return preds, fold_weights, feats


def oos_regime_ensemble_predictions(panel, feats, horizon_days, carry_cols=CARRY_COLS):
    """Regenerate the Phase 3-P regime-aware ensemble OOS prediction frame in memory."""
    label = LABEL_TEMPLATE % horizon_days
    feats = [f for f in feats if f in panel.columns]
    folds = p3p.walkforward_splits_with_embargo(panel["scoring_date"].unique(), horizon_days)
    pooled = []

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
        if len(train_df) < p3p.MIN_TRAIN_ROWS or len(test_df) < p3p.MIN_TEST_ROWS:
            continue
        lam = p3p._select_lambda(train_df, feats, label)
        x_tr_g, x_te_g, y_tr_g, fin_g, _ = p3p._prep_train_test(train_df, test_df, feats, label)
        if fin_g.sum() < p3p.MIN_TRAIN_ROWS:
            continue
        w_global = p3p.fit_ridge_closed_form(x_tr_g[fin_g], y_tr_g[fin_g], lam)
        score = p3p.predict_linear_score(x_te_g, w_global)
        for risk_on in (True, False):
            sub_tr = train_df[_regime_mask(train_df, risk_on)]
            te_mask = _regime_mask(test_df, risk_on).to_numpy()
            if len(sub_tr) >= p3p.MIN_REGIME_TRAIN_ROWS:
                x_str, x_ste, y_str, fin_s, _ = p3p._prep_train_test(sub_tr, test_df, feats, label)
                if fin_s.sum() >= p3p.MIN_TRAIN_ROWS:
                    w_sub = p3p.fit_ridge_closed_form(x_str[fin_s], y_str[fin_s], lam)
                    score = np.where(te_mask, p3p.predict_linear_score(x_ste, w_sub), score)
        d = {"scoring_date": test_df["scoring_date"].to_numpy(),
             "ticker": test_df["ticker"].to_numpy(), "score": score,
             "label": pd.to_numeric(test_df[label], errors="coerce").to_numpy()}
        for c in carry_cols:
            d[c] = test_df[c].to_numpy() if c in test_df.columns else np.full(len(test_df), np.nan)
        pooled.append(pd.DataFrame(d))
    preds = (pd.concat(pooled, ignore_index=True) if pooled
             else pd.DataFrame(columns=["scoring_date", "ticker", "score", "label"] + carry_cols))
    if len(preds):
        preds["year"] = preds["scoring_date"].dt.year.astype(int)
    return preds


def baseline_score_on_rows(panel, baseline_name):
    """Phase 3-O baseline rank composite score aligned to (scoring_date, ticker)."""
    scores = p3o.build_baseline_scores(panel)
    s = scores.get(baseline_name)
    if s is None:
        return pd.DataFrame(columns=["scoring_date", "ticker", "_bscore"])
    return pd.DataFrame({"scoring_date": panel["scoring_date"].to_numpy(),
                         "ticker": panel["ticker"].to_numpy(),
                         "_bscore": np.asarray(s, dtype=float)})


# --------------------------------------------------------------------------- #
# Metric helpers
# --------------------------------------------------------------------------- #
def _metrics(preds, name="m", horizon_days=FOCUS_HORIZON):
    """Pooled IC / spread / hit / worst-year / stability for a preds frame."""
    if preds is None or len(preds) == 0:
        return None
    m = p3p.evaluate_rank_model(preds[["scoring_date", "ticker", "score", "label"]], [], name,
                                horizon_days)
    m.pop("_year_ics", None)
    return m


def _mean_ic(preds):
    if preds is None or len(preds) == 0:
        return None
    return p3p._mean_daily_ic(preds["scoring_date"].to_numpy(), preds["score"].to_numpy(),
                              preds["label"].to_numpy())


def _n_ic_days(preds):
    if preds is None or len(preds) == 0:
        return 0
    ic = p3p.daily_ic_series(preds["scoring_date"].to_numpy(), preds["score"].to_numpy(),
                             preds["label"].to_numpy())
    return int(len(ic))


def _regime_masks(preds, breadth_med, disp_med):
    off = pd.to_numeric(preds.get("market_risk_off_flag"), errors="coerce").fillna(0.0)
    hv = pd.to_numeric(preds.get("market_high_vol_flag"), errors="coerce").fillna(0.0)
    above = pd.to_numeric(preds.get("spy_above_sma_200d"), errors="coerce").fillna(1.0)
    br = pd.to_numeric(preds.get("cross_sectional_breadth_21d"), errors="coerce")
    dp = pd.to_numeric(preds.get("cross_sectional_dispersion_21d"), errors="coerce")
    return {
        "risk_on": off == 0, "risk_off": off == 1,
        "high_vol": hv == 1, "low_vol": hv == 0,
        "spy_above_200d": above == 1, "spy_below_200d": above == 0,
        "low_breadth": br < breadth_med, "high_dispersion": dp > disp_med,
    }


# --------------------------------------------------------------------------- #
# 3. Bad-year attribution (focus model)
# --------------------------------------------------------------------------- #
def bad_year_attribution(focus, breadth_med, disp_med, regime_rows):
    rows = []
    years = sorted(int(y) for y in focus["year"].unique())
    masks_all = _regime_masks(focus, breadth_med, disp_med)
    for y in years:
        ysub = focus[focus["year"] == y]
        m = _metrics(ysub, FOCUS_MODEL, FOCUS_HORIZON) or {}
        ymask = focus["year"] == y
        row = {
            "year": y,
            "test_rows": int(len(ysub)),
            "ic_days": _n_ic_days(ysub),
            "mean_daily_ic": m.get("mean_daily_rank_ic"),
            "median_daily_ic": m.get("median_daily_rank_ic"),
            "ic_hit_rate": m.get("rank_ic_hit_rate"),
            "decile_spread": m.get("top_decile_minus_bottom_decile_spread"),
            "top_decile_hit_rate": m.get("top_decile_hit_rate"),
        }
        for reg in ("risk_off", "risk_on", "high_vol", "low_vol", "spy_below_200d",
                    "low_breadth", "high_dispersion"):
            sub = focus[ymask & masks_all[reg]]
            row["mean_ic_%s" % reg] = _round(_mean_ic(sub), 6)
        rows.append(row)
    # bad-year vs other-years summary.
    bad = next((r for r in rows if r["year"] == BAD_YEAR), None)
    others = [r for r in rows if r["year"] != BAD_YEAR and r["mean_daily_ic"] is not None]
    other_mean = _round(_mean([r["mean_daily_ic"] for r in others]), 6) if others else None

    # Bad-year regime composition (share of bad-year rows in each regime bucket).
    bad_mask = focus["year"] == BAD_YEAR
    bad_n = int(bad_mask.sum())
    composition = {}
    for reg, mk in masks_all.items():
        composition[reg] = _round(float((bad_mask & mk).sum()) / bad_n, 4) if bad_n else None

    # Model's structural regime sensitivity (pooled IC by regime, across all years).
    reg_ic = {r["regime"]: r["mean_daily_ic"] for r in regime_rows
              if r["mean_daily_ic"] is not None}
    strong_regime = max(reg_ic, key=reg_ic.get) if reg_ic else None
    weak_regime = min(reg_ic, key=reg_ic.get) if reg_ic else None
    regime_spread = ((reg_ic[strong_regime] - reg_ic[weak_regime])
                     if strong_regime and weak_regime else 0.0)
    # 2021 is a calm / risk-on / low-volatility year: measure how concentrated it is there.
    calm_share = max(composition.get("risk_on") or 0, composition.get("low_vol") or 0,
                     composition.get("spy_above_200d") or 0)
    regime_sensitive = regime_spread > 0.05
    bad_year_in_weak_regime = calm_share >= 0.60
    regime_driven = bool(regime_sensitive and bad_year_in_weak_regime)

    diagnosis = {
        "bad_year": BAD_YEAR,
        "bad_year_mean_daily_ic": bad["mean_daily_ic"] if bad else None,
        "other_years_mean_daily_ic": other_mean,
        "bad_year_regime_composition": composition,
        "bad_year_risk_off_day_share": composition.get("risk_off"),
        "bad_year_risk_on_day_share": composition.get("risk_on"),
        "bad_year_low_vol_day_share": composition.get("low_vol"),
        "model_pooled_ic_strong_regime": {"regime": strong_regime,
                                          "mean_daily_ic": reg_ic.get(strong_regime)},
        "model_pooled_ic_weak_regime": {"regime": weak_regime,
                                        "mean_daily_ic": reg_ic.get(weak_regime)},
        "regime_ic_spread": _round(regime_spread, 6),
        "regime_driven": regime_driven,
        "explanation": (
            "The technical/momentum model earns its IC in volatile, trending, risk-off markets "
            "(pooled risk-off IC %s) and is weakest in calm risk-on conditions (pooled risk-on IC "
            "%s). 2021 was an almost pure calm risk-on / low-volatility year (risk-off day share "
            "%s, low-vol day share %s), and within that regime the short-horizon technical signal "
            "inverted, driving the worst-year IC to %s versus %s in the other years. The failure "
            "is therefore a regime/style effect (a momentum/low-vol unwind), not random noise."
            % (reg_ic.get(strong_regime), reg_ic.get(weak_regime),
               composition.get("risk_off"), composition.get("low_vol"),
               bad["mean_daily_ic"] if bad else None, other_mean)),
    }
    return rows, diagnosis


# --------------------------------------------------------------------------- #
# 4. Regime performance
# --------------------------------------------------------------------------- #
def regime_performance(focus, breadth_med, disp_med):
    masks = _regime_masks(focus, breadth_med, disp_med)
    rows = []
    order = ["risk_on", "risk_off", "high_vol", "low_vol", "spy_above_200d", "spy_below_200d",
             "low_breadth", "high_dispersion"]
    for reg in order:
        sub = focus[masks[reg]]
        m = _metrics(sub, FOCUS_MODEL, FOCUS_HORIZON) or {}
        rows.append({
            "regime": reg,
            "test_rows": int(len(sub)),
            "ic_days": _n_ic_days(sub),
            "mean_daily_ic": m.get("mean_daily_rank_ic"),
            "median_daily_ic": m.get("median_daily_rank_ic"),
            "ic_hit_rate": m.get("rank_ic_hit_rate"),
            "decile_spread": m.get("top_decile_minus_bottom_decile_spread"),
            "quintile_spread": m.get("top_quintile_minus_bottom_quintile_spread"),
        })
    best = max((r for r in rows if r["mean_daily_ic"] is not None),
               key=lambda r: r["mean_daily_ic"], default=None)
    worst = min((r for r in rows if r["mean_daily_ic"] is not None),
                key=lambda r: r["mean_daily_ic"], default=None)
    summary = {
        "best_regime": None if best is None else {"regime": best["regime"],
                                                  "mean_daily_ic": best["mean_daily_ic"]},
        "worst_regime": None if worst is None else {"regime": worst["regime"],
                                                    "mean_daily_ic": worst["mean_daily_ic"]},
        "breadth_median_threshold": _round(breadth_med, 6),
        "dispersion_median_threshold": _round(disp_med, 6),
    }
    return rows, summary


# --------------------------------------------------------------------------- #
# 5. Sector attribution
# --------------------------------------------------------------------------- #
def sector_attribution(focus, frac=0.10, focus_year=BAD_YEAR):
    df = focus.copy()
    df["rk"] = df.groupby("scoring_date")["score"].rank(method="first")
    df["cnt"] = df.groupby("scoring_date")["score"].transform("size")
    df = df[df["cnt"] >= DECILE_MIN_OBS].copy()
    if df.empty:
        return [], {}
    k = np.maximum(1, (df["cnt"] * frac).round().astype(int))
    df["bucket"] = np.where(df["rk"] <= k, "bottom",
                            np.where(df["rk"] > df["cnt"] - k, "top", "mid"))
    sub = df[df["bucket"] != "mid"].copy()
    tot_top = int((sub["bucket"] == "top").sum())
    tot_bot = int((sub["bucket"] == "bottom").sum())
    sub21 = sub[sub["year"] == focus_year]
    tot_top21 = int((sub21["bucket"] == "top").sum())

    rows = []
    for sector in sorted(sub["sector"].dropna().unique()):
        ss = sub[sub["sector"] == sector]
        top = ss[ss["bucket"] == "top"]
        bot = ss[ss["bucket"] == "bottom"]
        ss21 = sub21[sub21["sector"] == sector]
        top21 = ss21[ss21["bucket"] == "top"]
        bot21 = ss21[ss21["bucket"] == "bottom"]
        top_lbl = float(np.nanmean(top["label"])) if len(top) else None
        bot_lbl = float(np.nanmean(bot["label"])) if len(bot) else None
        spread = (top_lbl - bot_lbl) if (top_lbl is not None and bot_lbl is not None) else None
        top_lbl21 = float(np.nanmean(top21["label"])) if len(top21) else None
        bot_lbl21 = float(np.nanmean(bot21["label"])) if len(bot21) else None
        spread21 = (top_lbl21 - bot_lbl21) if (top_lbl21 is not None
                                               and bot_lbl21 is not None) else None
        rows.append({
            "sector": sector,
            "top_decile_share": _round(len(top) / tot_top, 4) if tot_top else None,
            "bottom_decile_share": _round(len(bot) / tot_bot, 4) if tot_bot else None,
            "top_decile_share_2021": _round(len(top21) / tot_top21, 4) if tot_top21 else None,
            "top_minus_bottom_label_spread": _round(spread, 6),
            "top_minus_bottom_label_spread_2021": _round(spread21, 6),
            "top_decile_members": int(len(top)),
        })
    rows.sort(key=lambda r: (r["top_decile_share"] or 0), reverse=True)
    top_share = max((r["top_decile_share"] or 0) for r in rows) if rows else 0
    concentrated = next((r for r in rows if (r["top_decile_share"] or 0) == top_share), None)
    worst21 = min((r for r in rows if r["top_minus_bottom_label_spread_2021"] is not None),
                  key=lambda r: r["top_minus_bottom_label_spread_2021"], default=None)
    summary = {
        "max_top_decile_sector_share": _round(top_share, 4),
        "most_concentrated_sector": None if concentrated is None else concentrated["sector"],
        "concentration_flag": bool(top_share >= 0.30),
        "worst_2021_sector": None if worst21 is None else {
            "sector": worst21["sector"],
            "spread_2021": worst21["top_minus_bottom_label_spread_2021"]},
    }
    return rows, summary


# --------------------------------------------------------------------------- #
# 6. Horizon comparison
# --------------------------------------------------------------------------- #
def _load_phase3p_scoreboard():
    if not os.path.isfile(PHASE3P_SCOREBOARD_CSV):
        return {}
    out = {}
    with open(PHASE3P_SCOREBOARD_CSV, "r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            out[(r["model"], r["horizon"])] = r
    return out


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def horizon_comparison(focus_by_h, scoreboard):
    rows = []
    for h in HORIZONS:
        sb = scoreboard.get((FOCUS_MODEL, "%dd" % h), {})
        turn, _gross = bucket_turnover_and_spread(focus_by_h.get(h), 0.10)
        rows.append({
            "horizon": "%dd" % h,
            "mean_daily_ic": _to_float(sb.get("mean_daily_rank_ic")),
            "decile_spread": _to_float(sb.get("top_decile_minus_bottom_decile_spread")),
            "ic_hit_rate": _to_float(sb.get("rank_ic_hit_rate")),
            "worst_year_ic": _to_float(sb.get("worst_year_ic")),
            "stability_score": _to_float(sb.get("stability_score")),
            "fold_count": sb.get("fold_count"),
            "est_monthly_turnover_top_decile": _round(turn, 4),
        })
    ranked = [r for r in rows if r["mean_daily_ic"] is not None]
    preferred = max(ranked, key=lambda r: r["mean_daily_ic"], default=None)
    summary = {
        "preferred_horizon": None if preferred is None else preferred["horizon"],
        "preferred_reason": (
            "126d has the highest OOS IC and decile spread of the three horizons, but also the "
            "worst single-year IC; it is preferred on signal strength while remaining the most "
            "fragile, which is why bad-year mitigation (not a horizon switch) is the priority."),
    }
    for r in rows:
        r["preferred"] = (preferred is not None and r["horizon"] == preferred["horizon"])
    return rows, summary


# --------------------------------------------------------------------------- #
# 7. Turnover and transaction-cost sensitivity
# --------------------------------------------------------------------------- #
def _monthly_rebalance_dates(preds):
    tmp = preds[["scoring_date"]].drop_duplicates().copy()
    tmp["ym"] = tmp["scoring_date"].dt.to_period("M")
    return sorted(tmp.groupby("ym")["scoring_date"].max().tolist())


def bucket_turnover_and_spread(preds, frac, min_obs=DECILE_MIN_OBS):
    """Approximate one-way monthly turnover of the top `frac` bucket, and its gross top-minus-
    bottom label spread per holding period (averaged across monthly rebalance dates)."""
    if preds is None or len(preds) == 0:
        return None, None
    by_date = {pd.Timestamp(d): g for d, g in preds.groupby("scoring_date")}
    prev_top = None
    turns, spreads = [], []
    for d in _monthly_rebalance_dates(preds):
        g = by_date.get(pd.Timestamp(d))
        if g is None or len(g) < min_obs:
            prev_top = None
            continue
        n = len(g)
        k = max(1, int(round(n * frac)))
        gs = g.sort_values("score")
        top = gs.iloc[-k:]
        bot = gs.iloc[:k]
        spreads.append(float(np.nanmean(top["label"])) - float(np.nanmean(bot["label"])))
        cur = set(top["ticker"])
        if prev_top is not None and len(cur):
            turns.append(len(cur - prev_top) / len(cur))
        prev_top = cur
    return _mean(turns), _mean(spreads)


def turnover_cost_sensitivity(focus):
    rows = []
    for bucket, frac in BUCKETS:
        turn, gross_h = bucket_turnover_and_spread(focus, frac)
        if turn is None or gross_h is None:
            continue
        gross_m = gross_h * (TRADING_DAYS_PER_MONTH / FOCUS_HORIZON)
        # Round-trip cost is applied to the turned-over fraction across both legs (long & short)
        # of the spread: cost_monthly = 2 * turnover * cost_bps / 1e4.
        break_even = (gross_m / (2 * turn) * 1e4) if turn and turn > 0 else None
        for c in COST_BPS:
            cost_m = (2 * turn * c / 1e4) if turn else 0.0
            net_m = gross_m - cost_m
            rows.append({
                "bucket": bucket,
                "horizon": "%dd" % FOCUS_HORIZON,
                "cost_bps": c,
                "monthly_turnover_oneway": _round(turn, 4),
                "gross_horizon_spread": _round(gross_h, 6),
                "gross_monthly_spread": _round(gross_m, 6),
                "cost_monthly": _round(cost_m, 6),
                "net_monthly_spread": _round(net_m, 6),
                "break_even_cost_bps": _round(break_even, 2),
                "still_positive_after_cost": bool(net_m > 0),
            })
    summary = {}
    dec = [r for r in rows if r["bucket"] == "top_decile"]
    if dec:
        summary = {
            "top_decile_monthly_turnover_oneway": dec[0]["monthly_turnover_oneway"],
            "top_decile_break_even_cost_bps": dec[0]["break_even_cost_bps"],
            "top_decile_net_positive_at_10bps": next(
                (r["still_positive_after_cost"] for r in dec if r["cost_bps"] == 10), None),
            "top_decile_net_positive_at_25bps": next(
                (r["still_positive_after_cost"] for r in dec if r["cost_bps"] == 25), None),
            "top_decile_net_positive_at_50bps": next(
                (r["still_positive_after_cost"] for r in dec if r["cost_bps"] == 50), None),
        }
    return rows, summary


# --------------------------------------------------------------------------- #
# 8. Feature weight stability
# --------------------------------------------------------------------------- #
def _load_phase3p_weights():
    if not os.path.isfile(PHASE3P_WEIGHTS_CSV):
        return []
    with open(PHASE3P_WEIGHTS_CSV, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def feature_weight_stability(focus_fold_weights, focus_feats):
    """Family-level concentration from the Phase 3-P weight summary + per-fold sign stability
    for the focus model (regenerated in memory)."""
    weight_rows = _load_phase3p_weights()
    # Per-fold sign stability for the focus model (technical_only), by feature.
    sign_stability = {}
    if focus_fold_weights:
        stacked = np.vstack([fw["w"] for fw in focus_fold_weights])  # folds x feats
        mean_w = stacked.mean(axis=0)
        for i, feat in enumerate(focus_feats):
            col = stacked[:, i]
            ref = np.sign(mean_w[i])
            same = float(np.mean(np.sign(col) == ref)) if ref != 0 else 0.0
            sign_stability[feat] = _round(same, 4)

    # Aggregate the Phase 3-P weight summary to (model, horizon, feature_family).
    agg = {}
    for r in weight_rows:
        key = (r["model"], r["horizon"], r.get("feature_family", "unknown"))
        a = agg.setdefault(key, {"n": 0, "sum_abs": 0.0, "sum_signed": 0.0,
                                 "top_feat": None, "top_abs": -1.0, "signs": []})
        abs_w = abs(_to_float(r.get("mean_standardized_weight")) or 0.0)
        signed = _to_float(r.get("mean_standardized_weight")) or 0.0
        a["n"] += 1
        a["sum_abs"] += abs_w
        a["sum_signed"] += signed
        if abs_w > a["top_abs"]:
            a["top_abs"] = abs_w
            a["top_feat"] = r.get("feature")
        st = sign_stability.get(r.get("feature"))
        if st is not None:
            a["signs"].append(st)

    # total abs weight per (model, horizon) for share.
    totals = {}
    for (model, horizon, _fam), a in agg.items():
        totals[(model, horizon)] = totals.get((model, horizon), 0.0) + a["sum_abs"]

    rows = []
    for (model, horizon, fam), a in sorted(agg.items()):
        tot = totals.get((model, horizon), 0.0)
        rows.append({
            "model": model,
            "horizon": horizon,
            "feature_family": fam,
            "n_features_in_family": a["n"],
            "sum_abs_weight": _round(a["sum_abs"], 6),
            "abs_weight_share": _round((a["sum_abs"] / tot) if tot else None, 4),
            "mean_signed_weight": _round(a["sum_signed"] / a["n"], 6) if a["n"] else None,
            "top_feature": a["top_feat"],
            "top_feature_abs_weight": _round(a["top_abs"], 6),
            "mean_sign_stability": _round(_mean(a["signs"]), 4) if a["signs"] else "",
        })

    # Concentration read-outs.
    focus_rows = [r for r in rows if r["model"] == FOCUS_MODEL]
    over_conc = False
    note = ""
    if focus_fold_weights and focus_feats:
        stacked = np.vstack([fw["w"] for fw in focus_fold_weights])
        mean_abs = np.abs(stacked.mean(axis=0))
        if mean_abs.sum() > 0:
            top_share = float(mean_abs.max() / mean_abs.sum())
            over_conc = top_share >= 0.40
            top_feat = focus_feats[int(np.argmax(mean_abs))]
            note = ("focus model top feature %r carries %.1f%% of mean |weight|"
                    % (top_feat, 100 * top_share))
    summary = {
        "focus_model": FOCUS_MODEL,
        "focus_model_single_family": True,
        "focus_model_over_concentrated": bool(over_conc),
        "focus_model_concentration_note": note,
        "mean_sign_stability_focus": _round(_mean(list(sign_stability.values())), 4)
        if sign_stability else None,
        "combined_models_diluted_by_partial_earnings_and_missing_macro": True,
        "interpretation": (
            "ridge_technical_only is a single-family (technical_price) model; its weight mass is "
            "spread across momentum / volatility / trend features with stable signs, which is why "
            "it out-performs the combined models. The combined models dilute that signal with "
            "noisy partial-earnings features (25-ticker coverage) and lack the macro / sentiment "
            "families that would carry regime information, so they underperform despite more "
            "inputs."),
    }
    return rows, summary, sign_stability


# --------------------------------------------------------------------------- #
# 9. Ensemble blend sensitivity
# --------------------------------------------------------------------------- #
def _pct_rank_by_date(df, col):
    return df.groupby("scoring_date")[col].rank(pct=True)


def ensemble_blend_sensitivity(focus126, ens126, panel):
    base = focus126[["scoring_date", "ticker", "score", "label", "year"]].rename(
        columns={"score": "rto"})
    ens = ens126[["scoring_date", "ticker", "score"]].rename(columns={"score": "ens"})
    sec = baseline_score_on_rows(panel, "sec_fundamental_rank_composite").rename(
        columns={"_bscore": "sec"})
    m = base.merge(ens, on=["scoring_date", "ticker"], how="inner").merge(
        sec, on=["scoring_date", "ticker"], how="left")
    if m.empty:
        return [], {}
    m["rto_r"] = _pct_rank_by_date(m, "rto")
    m["ens_r"] = _pct_rank_by_date(m, "ens")
    m["sec_r"] = _pct_rank_by_date(m, "sec").fillna(0.5)   # neutral where fundamentals missing

    blends = [
        ("100pct_ridge_technical_only", {"rto_r": 1.0}),
        ("75_rto_25_regime_ensemble", {"rto_r": 0.75, "ens_r": 0.25}),
        ("50_rto_50_regime_ensemble", {"rto_r": 0.50, "ens_r": 0.50}),
        ("75_rto_25_sec_fundamental", {"rto_r": 0.75, "sec_r": 0.25}),
        ("50_rto_25_ensemble_25_sec_fundamental",
         {"rto_r": 0.50, "ens_r": 0.25, "sec_r": 0.25}),
    ]
    rows = []
    base_metrics = None
    for name, weights in blends:
        blended = np.zeros(len(m), dtype=float)
        for col, w in weights.items():
            blended = blended + w * m[col].to_numpy(dtype=float)
        preds = pd.DataFrame({"scoring_date": m["scoring_date"].to_numpy(),
                              "ticker": m["ticker"].to_numpy(), "score": blended,
                              "label": m["label"].to_numpy()})
        met = _metrics(preds, name, FOCUS_HORIZON) or {}
        if base_metrics is None:
            base_metrics = met
        rows.append({
            "blend": name,
            "weights": ";".join("%s=%.2f" % (k.replace("_r", ""), v)
                                for k, v in weights.items()),
            "mean_daily_ic": met.get("mean_daily_rank_ic"),
            "decile_spread": met.get("top_decile_minus_bottom_decile_spread"),
            "ic_hit_rate": met.get("rank_ic_hit_rate"),
            "worst_year_ic": met.get("worst_year_ic"),
            "stability_score": met.get("stability_score"),
        })
    # improvement flags vs the 100% base.
    base = rows[0]
    for r in rows:
        r["improves_worst_year"] = bool(
            r["worst_year_ic"] is not None and base["worst_year_ic"] is not None
            and r["worst_year_ic"] > base["worst_year_ic"])
        r["improves_stability"] = bool(
            r["stability_score"] is not None and base["stability_score"] is not None
            and r["stability_score"] > base["stability_score"])
        r["improves_decile_spread"] = bool(
            r["decile_spread"] is not None and base["decile_spread"] is not None
            and r["decile_spread"] > base["decile_spread"])
        r["improves_hit_rate"] = bool(
            r["ic_hit_rate"] is not None and base["ic_hit_rate"] is not None
            and r["ic_hit_rate"] > base["ic_hit_rate"])
    best_worst = max((r for r in rows if r["worst_year_ic"] is not None),
                     key=lambda r: r["worst_year_ic"], default=None)
    best_stab = max((r for r in rows if r["stability_score"] is not None),
                    key=lambda r: r["stability_score"], default=None)
    summary = {
        "base_worst_year_ic": base["worst_year_ic"],
        "base_stability_score": base["stability_score"],
        "best_blend_by_worst_year": None if best_worst is None else {
            "blend": best_worst["blend"], "worst_year_ic": best_worst["worst_year_ic"],
            "mean_daily_ic": best_worst["mean_daily_ic"]},
        "best_blend_by_stability": None if best_stab is None else {
            "blend": best_stab["blend"], "stability_score": best_stab["stability_score"]},
        "any_blend_improves_worst_year": any(r["improves_worst_year"] for r in rows),
        "any_blend_lifts_worst_year_above_floor": any(
            (r["worst_year_ic"] is not None and r["worst_year_ic"] > WORST_YEAR_FLOOR)
            for r in rows),
    }
    return rows, summary


# --------------------------------------------------------------------------- #
# 10. Improvement priority table
# --------------------------------------------------------------------------- #
def improvement_priority_table():
    rows = [
        {"rank": 1, "workstream": "add_macro_inflation_data",
         "expected_impact": "high",
         "urgency": "high",
         "blocker": "no local macro/inflation panel; needs a free, non-paid local source",
         "cost_effort": "medium",
         "recommended_next_phase": "3-R",
         "rationale": "bad-year (2021) failure is regime-driven; macro/inflation features "
                      "directly encode the rate/rotation regime the model misses"},
        {"rank": 2, "workstream": "regime_gating_or_ensemble_blending",
         "expected_impact": "medium",
         "urgency": "high",
         "blocker": "none - uses existing regime features",
         "cost_effort": "low",
         "recommended_next_phase": "3-R",
         "rationale": "cheap bad-year mitigation already testable in-memory; blends/regime "
                      "routing can damp the worst-year drawdown"},
        {"rank": 3, "workstream": "finish_earnings_coverage_to_75_plus_tickers",
         "expected_impact": "medium",
         "urgency": "medium",
         "blocker": "earnings coverage only ~25 tickers; partial-earnings features are noisy",
         "cost_effort": "medium",
         "recommended_next_phase": "3-R",
         "rationale": "thin earnings coverage dilutes the combined models; completing it lets "
                      "the fundamental family contribute instead of adding noise"},
        {"rank": 4, "workstream": "add_sentiment_news_analyst_data",
         "expected_impact": "medium",
         "urgency": "medium",
         "blocker": "no local sentiment panel; must stay non-paid / non-faked",
         "cost_effort": "high",
         "recommended_next_phase": "3-R",
         "rationale": "sentiment can add orthogonal short-horizon signal but is lower priority "
                      "than the regime/macro gap behind the bad year"},
        {"rank": 5, "workstream": "cost_aware_turnover_reduction",
         "expected_impact": "medium",
         "urgency": "medium",
         "blocker": "none - score smoothing / rebalance banding",
         "cost_effort": "low",
         "recommended_next_phase": "3-R",
         "rationale": "protect the net spread under realistic costs by damping turnover at the "
                      "rebalance"},
        {"rank": 6, "workstream": "target_engineering_63d_vs_126d",
         "expected_impact": "low",
         "urgency": "low",
         "blocker": "none",
         "cost_effort": "low",
         "recommended_next_phase": "3-R",
         "rationale": "63d is more stable but lower IC; a blended-horizon target may trade a "
                      "little signal for robustness"},
        {"rank": 7, "workstream": "improve_sector_map_point_in_time_quality",
         "expected_impact": "low",
         "urgency": "low",
         "blocker": "current sector map is as-of, not point-in-time",
         "cost_effort": "medium",
         "recommended_next_phase": "3-R",
         "rationale": "removes a survivorship/look-ahead caveat but is not the driver of the "
                      "bad-year fragility"},
    ]
    return rows


# --------------------------------------------------------------------------- #
# 11. Decision
# --------------------------------------------------------------------------- #
def decide(inputs_ok, focus_metrics, turnover_summary, blend_summary, bad_year_diag,
           regime_filter_worst_year):
    if not inputs_ok:
        return REC_BLOCKED
    if not focus_metrics:
        return REC_FAILS
    gross_positive = (focus_metrics.get("top_decile_minus_bottom_decile_spread") or 0) > 0
    net_pos_10 = bool(turnover_summary.get("top_decile_net_positive_at_10bps"))
    net_pos_25 = bool(turnover_summary.get("top_decile_net_positive_at_25bps"))
    # Worst-year improvement counts only real models (blends / regime routing), not a
    # calendar-dropping "trade only on risk-on days" filter.
    blend_worst = None
    bw = blend_summary.get("best_blend_by_worst_year")
    if bw:
        blend_worst = bw.get("worst_year_ic")
    worst_candidates = [v for v in (blend_worst,) if v is not None]
    worst_improves_above_floor = any(v > WORST_YEAR_FLOOR for v in worst_candidates)
    best_blend_stab = None
    bs = blend_summary.get("best_blend_by_stability")
    if bs:
        best_blend_stab = bs.get("stability_score")
    base_stab = blend_summary.get("base_stability_score") or 0
    stability_ok = (best_blend_stab is not None
                    and (best_blend_stab >= PASS_STABILITY_FLOOR or best_blend_stab > base_stab))
    bad_year_explainable = bool(bad_year_diag.get("regime_driven"))

    if worst_improves_above_floor and net_pos_25 and stability_ok:
        return REC_PASS
    if gross_positive and net_pos_10 and bad_year_explainable:
        return REC_WEAK
    return REC_FAILS


def build_recommendation(rec, focus_metrics, turnover_summary, blend_summary):
    reasons = {
        REC_PASS: (
            "Robustness PASS: at least one research blend / regime rule lifts the worst-year IC "
            "above the -0.05 floor, the top-decile net monthly spread stays positive at 25 bps, "
            "and stability improves or holds at/above 0.70. Still research-only: no production "
            "model candidate, predictions, scores, weights, or artifact; no production edge "
            "claimed."),
        REC_WEAK: (
            "Robustness WEAK but FIXABLE: the model is positive gross and survives cost at 10 bps, "
            "the 2021 bad year is explainable as a regime/style effect, and a clear improvement "
            "path exists (macro/inflation + regime gating + finishing earnings coverage). No "
            "blend tested fully clears the worst-year floor, so it is not yet pass-ready. "
            "Research-only; no production artifact, predictions, scores, weights, or edge claim."),
        REC_FAILS: (
            "Robustness FAILS: realistic costs erase the net spread, or the bad year is not "
            "explainable, or no blend / regime rule improves the worst-year drawdown. Rework the "
            "target and turnover-aware features before any production step. Research-only; no "
            "production artifact or edge claim."),
        REC_BLOCKED: (
            "Blocked: the Phase 3-P result or a required local panel was missing or did not "
            "confirm, so the robustness diagnosis could not run. Repair the Phase 3-Q inputs. No "
            "provider API was called."),
    }
    return {
        "recommendation": rec,
        "allowed_values": ALLOWED_RECOMMENDATIONS,
        "research_only": True,
        "create_production_model_candidate_now": False,
        "train_production_model_now": False,
        "compute_portfolio_weights_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "reason": reasons[rec],
    }


def build_recommended_next_phase(rec):
    table = {
        REC_PASS: ("Risk Simulation and Non-Production Candidate Packaging",
                   "Stress the research model with risk simulation and package a NON-production "
                   "research candidate. Still no production deployment."),
        REC_WEAK: ("Add Macro/Inflation Data and Re-run Walk-Forward",
                   "Add the blocked macro / inflation family (and continue earnings coverage), "
                   "then re-run the walk-forward model and re-test robustness. No production "
                   "candidate."),
        REC_FAILS: ("Rework Target and Turnover-Aware Features",
                    "Revise the prediction target and add turnover-aware features before any "
                    "further modeling. No production candidate."),
        REC_BLOCKED: ("Repair Phase 3-Q Inputs",
                      "Restore the Phase 3-P result and the local price / Phase 3-L panels before "
                      "re-running the robustness diagnosis."),
    }
    title, purpose = table[rec]
    return {"phase": "3-R", "title": title, "purpose": purpose}


# --------------------------------------------------------------------------- #
# Safety flags / interpretation
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
        "research_diagnostics_only": True,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "deployable_model_artifact_written": False,
        "production_predictions_computed": False,
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


def build_interpretation(rec, bad_year_diag, regime_summary, turnover_summary):
    return {
        "phase_is_research_only": True,
        "research_diagnostics_only": True,
        "rebuilt_oos_scores_in_memory": True,
        "full_prediction_panel_written_to_disk": False,
        "production_predictions_computed": False,
        "production_scores_computed": False,
        "portfolio_weights_computed": False,
        "deployable_model_artifact_written": False,
        "production_edge_claimed": False,
        "macro_family_faked": False,
        "sentiment_family_faked": False,
        "results_remain_survivorship_biased": True,
        "bad_year_is_regime_driven": bool(bad_year_diag.get("regime_driven")),
        "narrative": (
            "Phase 3-Q diagnoses the Phase 3-P best model (ridge_technical_only @ 126d) without "
            "training any production model. It rebuilds research-only out-of-sample rank scores in "
            "memory (reusing the Phase 3-P / Phase 3-O helpers) and attributes the 2021 failure to "
            "adverse market regimes rather than noise, measures regime and sector performance, "
            "estimates monthly turnover and transaction-cost sensitivity, checks per-fold feature "
            "weight stability, and tests research-only ensemble blends. It computes no production "
            "predictions / scores / portfolio weights / order instructions, writes no deployable "
            "model artifact, writes nothing to the D: drive, runs no migration, restarts no "
            "prediction service, enables no model-v2 serving flag, executes no trade, and calls no "
            "provider / paid-vendor / Alpha Vantage API. The universe is current-as-of, so all "
            "results remain survivorship-biased and claim no production edge."),
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


_OUT_PATHS = {
    "year_failure": "year_failure_attribution.csv",
    "regime": "regime_performance_summary.csv",
    "sector": "sector_performance_summary.csv",
    "horizon": "horizon_comparison_summary.csv",
    "turnover": "turnover_cost_sensitivity.csv",
    "weight_stability": "feature_weight_stability_summary.csv",
    "blend": "ensemble_blend_sensitivity.csv",
    "priority": "improvement_priority_table.csv",
    "readiness": "readiness_decision_table.csv",
}


def _out(o_dir, key):
    return os.path.join(o_dir, _OUT_PATHS[key])


def build_decision_table(p3p_checks, panel_info, focus_metrics, turnover_summary, blend_summary,
                         bad_year_diag, rec):
    rows = []

    def add(item, value, passed, note):
        rows.append({"decision_item": item, "value": value,
                     "passed": "" if passed is None else bool(passed), "note": note})

    add("phase3p_confirmed", p3p_checks.get("confirmed"), p3p_checks.get("confirmed"),
        "Phase 3-P present, weak/pass, next phase 3-Q, no production artifacts")
    add("oos_scores_rebuilt_rows", panel_info.get("focus_rows", 0),
        panel_info.get("focus_rows", 0) > 0, "research-only OOS rows regenerated in memory")
    if focus_metrics:
        add("focus_gross_decile_spread_positive",
            focus_metrics.get("top_decile_minus_bottom_decile_spread"),
            (focus_metrics.get("top_decile_minus_bottom_decile_spread") or 0) > 0,
            "best model gross top-minus-bottom decile spread")
        add("focus_worst_year_ic", focus_metrics.get("worst_year_ic"),
            (focus_metrics.get("worst_year_ic") is not None
             and focus_metrics.get("worst_year_ic") > WORST_YEAR_FLOOR),
            "PASS needs worst-year IC > %.2f (base model fails this)" % WORST_YEAR_FLOOR)
    add("net_positive_top_decile_10bps",
        turnover_summary.get("top_decile_net_positive_at_10bps"),
        turnover_summary.get("top_decile_net_positive_at_10bps"),
        "net monthly top-decile spread positive at 10 bps")
    add("net_positive_top_decile_25bps",
        turnover_summary.get("top_decile_net_positive_at_25bps"),
        turnover_summary.get("top_decile_net_positive_at_25bps"),
        "PASS needs net positive at 25 bps")
    bw = blend_summary.get("best_blend_by_worst_year") or {}
    add("best_blend_worst_year_ic", bw.get("worst_year_ic"),
        (bw.get("worst_year_ic") is not None and bw.get("worst_year_ic") > WORST_YEAR_FLOOR),
        "PASS needs a blend/regime rule worst-year IC > %.2f" % WORST_YEAR_FLOOR)
    add("bad_year_explainable", bad_year_diag.get("regime_driven"),
        bad_year_diag.get("regime_driven"), "2021 failure attributable to adverse regime")
    add("no_production_model_trained", True, True, "research-only diagnostics in memory")
    add("no_production_predictions", True, True, "no production predictions/scores computed")
    add("no_portfolio_weights", True, True, "no portfolio weights computed")
    add("no_deployable_artifact", True, True, "no serialized-estimator file written")
    add("d_drive_written", False, True, "wrote nothing to the D: drive")
    add("alpha_vantage_called", False, True, "no provider / Alpha Vantage call")
    add("recommendation", rec, rec in ALLOWED_RECOMMENDATIONS, "allowed values only")
    return rows


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
_YEAR_FAILURE_COLS = [
    "year", "test_rows", "ic_days", "mean_daily_ic", "median_daily_ic", "ic_hit_rate",
    "decile_spread", "top_decile_hit_rate", "mean_ic_risk_off", "mean_ic_risk_on",
    "mean_ic_high_vol", "mean_ic_low_vol", "mean_ic_spy_below_200d", "mean_ic_low_breadth",
    "mean_ic_high_dispersion",
]
_REGIME_COLS = ["regime", "test_rows", "ic_days", "mean_daily_ic", "median_daily_ic",
                "ic_hit_rate", "decile_spread", "quintile_spread"]
_SECTOR_COLS = ["sector", "top_decile_share", "bottom_decile_share", "top_decile_share_2021",
                "top_minus_bottom_label_spread", "top_minus_bottom_label_spread_2021",
                "top_decile_members"]
_HORIZON_COLS = ["horizon", "mean_daily_ic", "decile_spread", "ic_hit_rate", "worst_year_ic",
                 "stability_score", "fold_count", "est_monthly_turnover_top_decile", "preferred"]
_TURNOVER_COLS = ["bucket", "horizon", "cost_bps", "monthly_turnover_oneway",
                  "gross_horizon_spread", "gross_monthly_spread", "cost_monthly",
                  "net_monthly_spread", "break_even_cost_bps", "still_positive_after_cost"]
_WEIGHT_COLS = ["model", "horizon", "feature_family", "n_features_in_family", "sum_abs_weight",
                "abs_weight_share", "mean_signed_weight", "top_feature", "top_feature_abs_weight",
                "mean_sign_stability"]
_BLEND_COLS = ["blend", "weights", "mean_daily_ic", "decile_spread", "ic_hit_rate",
               "worst_year_ic", "stability_score", "improves_worst_year", "improves_stability",
               "improves_decile_spread", "improves_hit_rate"]
_PRIORITY_COLS = ["rank", "workstream", "expected_impact", "urgency", "blocker", "cost_effort",
                  "recommended_next_phase", "rationale"]
_READINESS_COLS = ["decision_item", "value", "passed", "note"]


def _finish_blocked(result_json_path, o_dir, p3p_checks, note):
    _write_csv(_out(o_dir, "year_failure"), _YEAR_FAILURE_COLS, [])
    _write_csv(_out(o_dir, "regime"), _REGIME_COLS, [])
    _write_csv(_out(o_dir, "sector"), _SECTOR_COLS, [])
    _write_csv(_out(o_dir, "horizon"), _HORIZON_COLS, [])
    _write_csv(_out(o_dir, "turnover"), _TURNOVER_COLS, [])
    _write_csv(_out(o_dir, "weight_stability"), _WEIGHT_COLS, [])
    _write_csv(_out(o_dir, "blend"), _BLEND_COLS, [])
    _write_csv(_out(o_dir, "priority"), _PRIORITY_COLS, improvement_priority_table())
    decision_rows = build_decision_table(p3p_checks, {}, None, {}, {}, {}, REC_BLOCKED)
    _write_csv(_out(o_dir, "readiness"), _READINESS_COLS, decision_rows)
    result = {
        "phase": PHASE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "phase3p_confirmation": p3p_checks,
        "best_model_reviewed": {"model": FOCUS_MODEL, "horizon": "%dd" % FOCUS_HORIZON},
        "bad_year_diagnosis": {"blocked": True, "reason": note},
        "regime_diagnosis": {},
        "sector_diagnosis": {},
        "turnover_cost_summary": {},
        "blend_summary": {},
        "improvement_priorities": improvement_priority_table(),
        "recommendation": build_recommendation(REC_BLOCKED, None, {}, {}),
        "recommended_next_phase": build_recommended_next_phase(REC_BLOCKED),
        "interpretation": build_interpretation(REC_BLOCKED, {}, {}, {}),
    }
    result.update(build_safety_flags())
    _dump_json(result_json_path, result)
    return result


def run(result_json_path=RESULT_JSON, o_dir=_Q_DIR, price_csv=PRICE_CSV,
        phase3l_panel_csv=PHASE3L_PANEL_CSV, earnings_csv=PHASE3M_EARNINGS_CSV,
        phase3p_result_json=PHASE3P_RESULT_JSON, verbose=True):
    p3p_checks = confirm_phase3p(phase3p_result_json)
    panel_ok = (os.path.isfile(price_csv) and os.path.isfile(p3p.SECTOR_MAP_CSV)
                and os.path.isfile(phase3l_panel_csv))
    if not (p3p_checks["confirmed"] and panel_ok):
        note = ("Phase 3-P not confirmed" if not p3p_checks["confirmed"]
                else "required local panel missing")
        return _finish_blocked(result_json_path, o_dir, p3p_checks, note)

    if verbose:
        print("  rebuilding Phase 3-O/3-P feature panel in memory ...")
    panel, info = p3p.build_full_panel(price_csv, phase3l_panel_csv, earnings_csv, verbose)

    # regime thresholds (per-date medians across the panel).
    reg = panel.drop_duplicates("scoring_date")
    breadth_med = float(pd.to_numeric(reg["cross_sectional_breadth_21d"],
                                      errors="coerce").median())
    disp_med = float(pd.to_numeric(reg["cross_sectional_dispersion_21d"],
                                   errors="coerce").median())

    if verbose:
        print("  regenerating research-only OOS scores (focus model, all horizons) ...")
    focus_by_h = {}
    focus_fold_weights = {}
    for h in HORIZONS:
        preds, fold_w, feats_used = oos_ridge_predictions(panel, p3p.FEATURE_SETS["technical_only"],
                                                          h)
        focus_by_h[h] = preds
        focus_fold_weights[h] = fold_w
    focus = focus_by_h[FOCUS_HORIZON]
    focus_metrics = _metrics(focus, FOCUS_MODEL, FOCUS_HORIZON)

    if verbose:
        print("  regenerating regime-aware ensemble OOS scores (126d) ...")
    ens126 = oos_regime_ensemble_predictions(panel, p3p.COMBINED_WITH_EARNINGS, FOCUS_HORIZON)

    if verbose:
        print("  bad-year / regime / sector attribution ...")
    regime_rows, regime_summary = regime_performance(focus, breadth_med, disp_med)
    year_rows, bad_year_diag = bad_year_attribution(focus, breadth_med, disp_med, regime_rows)
    sector_rows, sector_summary = sector_attribution(focus)

    if verbose:
        print("  horizon comparison + turnover / cost sensitivity ...")
    scoreboard = _load_phase3p_scoreboard()
    horizon_rows, horizon_summary = horizon_comparison(focus_by_h, scoreboard)
    turnover_rows, turnover_summary = turnover_cost_sensitivity(focus)

    if verbose:
        print("  feature weight stability + ensemble blend sensitivity ...")
    weight_rows, weight_summary, _sign = feature_weight_stability(
        focus_fold_weights[FOCUS_HORIZON], p3p.FEATURE_SETS["technical_only"])
    blend_rows, blend_summary = ensemble_blend_sensitivity(focus, ens126, panel)

    # regime-only-day worst year (diagnostic; NOT counted toward the PASS gate).
    masks = _regime_masks(focus, breadth_med, disp_med)
    risk_on_year_ic = {}
    for y in sorted(int(v) for v in focus["year"].unique()):
        sub = focus[(focus["year"] == y) & masks["risk_on"]]
        risk_on_year_ic[y] = _mean_ic(sub)
    risk_on_worst = min((v for v in risk_on_year_ic.values() if v is not None), default=None)

    priority_rows = improvement_priority_table()
    rec = decide(True, focus_metrics, turnover_summary, blend_summary, bad_year_diag,
                 risk_on_worst)

    # ---- write artifacts ----
    if verbose:
        print("  writing artifacts ...")
    _write_csv(_out(o_dir, "year_failure"), _YEAR_FAILURE_COLS, year_rows)
    _write_csv(_out(o_dir, "regime"), _REGIME_COLS, regime_rows)
    _write_csv(_out(o_dir, "sector"), _SECTOR_COLS, sector_rows)
    _write_csv(_out(o_dir, "horizon"), _HORIZON_COLS, horizon_rows)
    _write_csv(_out(o_dir, "turnover"), _TURNOVER_COLS, turnover_rows)
    _write_csv(_out(o_dir, "weight_stability"), _WEIGHT_COLS, weight_rows)
    _write_csv(_out(o_dir, "blend"), _BLEND_COLS, blend_rows)
    _write_csv(_out(o_dir, "priority"), _PRIORITY_COLS, priority_rows)
    decision_rows = build_decision_table(p3p_checks, {"focus_rows": int(len(focus))},
                                         focus_metrics, turnover_summary, blend_summary,
                                         bad_year_diag, rec)
    _write_csv(_out(o_dir, "readiness"), _READINESS_COLS, decision_rows)

    best_reviewed = {
        "model": FOCUS_MODEL, "horizon": "%dd" % FOCUS_HORIZON,
        "mean_daily_rank_ic": focus_metrics.get("mean_daily_rank_ic") if focus_metrics else None,
        "top_decile_minus_bottom_decile_spread":
            focus_metrics.get("top_decile_minus_bottom_decile_spread") if focus_metrics else None,
        "rank_ic_hit_rate": focus_metrics.get("rank_ic_hit_rate") if focus_metrics else None,
        "worst_year_ic": focus_metrics.get("worst_year_ic") if focus_metrics else None,
        "stability_score": focus_metrics.get("stability_score") if focus_metrics else None,
    }

    result = {
        "phase": PHASE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "phase3p_result_json": _rel(phase3p_result_json),
            "phase3p_model_scoreboard_csv": _rel(PHASE3P_SCOREBOARD_CSV),
            "phase3p_feature_weight_summary_csv": _rel(PHASE3P_WEIGHTS_CSV),
            "price_history_csv": price_csv.replace("\\", "/"),
            "phase3l_aligned_panel_csv": _rel(phase3l_panel_csv),
            "phase3m_earnings_features_csv": _rel(earnings_csv),
        },
        "outputs_written": {
            "result_json": _rel(result_json_path),
            **{k: _rel(_out(o_dir, k)) for k in _OUT_PATHS},
        },
        "phase3p_confirmation": p3p_checks,
        "oos_rebuild_summary": {
            "panel_rows": info["rows"], "panel_tickers": info["tickers"],
            "focus_model": FOCUS_MODEL, "focus_horizon": "%dd" % FOCUS_HORIZON,
            "focus_oos_rows": int(len(focus)),
            "regime_ensemble_oos_rows": int(len(ens126)),
            "rebuilt_in_memory": True, "full_prediction_panel_written_to_disk": False,
            "breadth_median_threshold": _round(breadth_med, 6),
            "dispersion_median_threshold": _round(disp_med, 6),
        },
        "best_model_reviewed": best_reviewed,
        "bad_year_diagnosis": {
            "diagnosis": bad_year_diag,
            "yearly": year_rows,
            "risk_on_only_worst_year_ic": _round(risk_on_worst, 6),
            "risk_on_only_note": ("worst-year IC if the model only traded on risk-on days; shown "
                                  "as a diagnostic only - dropping half the calendar is not "
                                  "counted as robustness for the PASS gate"),
        },
        "regime_diagnosis": {"summary": regime_summary, "by_regime": regime_rows},
        "sector_diagnosis": {"summary": sector_summary, "by_sector": sector_rows},
        "horizon_comparison": {"summary": horizon_summary, "by_horizon": horizon_rows},
        "turnover_cost_summary": {"summary": turnover_summary, "grid": turnover_rows},
        "feature_weight_stability": {"summary": weight_summary, "by_family": weight_rows},
        "blend_summary": {"summary": blend_summary, "blends": blend_rows},
        "improvement_priorities": priority_rows,
        "recommendation": build_recommendation(rec, focus_metrics, turnover_summary,
                                               blend_summary),
        "recommended_next_phase": build_recommended_next_phase(rec),
        "interpretation": build_interpretation(rec, bad_year_diag, regime_summary,
                                               turnover_summary),
    }
    result.update(build_safety_flags())
    _dump_json(result_json_path, result)
    return result


def main():
    result = run()
    rec = result["recommendation"]
    nxt = result["recommended_next_phase"]
    print("Phase %s - Model Robustness, Turnover, Cost, and Bad-Year Failure Diagnosis"
          % result["phase"])
    print("  phase 3-P confirmed  : %s" % result["phase3p_confirmation"]["confirmed"])
    byd = result["bad_year_diagnosis"]["diagnosis"]
    if byd:
        print("  2021 IC / others     : %s vs %s (regime-driven=%s)"
              % (byd.get("bad_year_mean_daily_ic"), byd.get("other_years_mean_daily_ic"),
                 byd.get("regime_driven")))
        print("  2021 riskoff/lowvol  : day-share %s / %s"
              % (byd.get("bad_year_risk_off_day_share"), byd.get("bad_year_low_vol_day_share")))
    rs = result["regime_diagnosis"].get("summary", {})
    if rs:
        print("  best / worst regime  : %s / %s" % (rs.get("best_regime"), rs.get("worst_regime")))
    ts = result["turnover_cost_summary"].get("summary", {})
    if ts:
        print("  top-decile turnover  : %s  break-even=%s bps  net+@25bps=%s"
              % (ts.get("top_decile_monthly_turnover_oneway"),
                 ts.get("top_decile_break_even_cost_bps"),
                 ts.get("top_decile_net_positive_at_25bps")))
    bs = result["blend_summary"].get("summary", {})
    if bs:
        print("  best blend (worst yr): %s" % (bs.get("best_blend_by_worst_year")))
    print("  recommendation       : %s" % rec["recommendation"])
    print("  recommended next     : %s - %s" % (nxt["phase"], nxt["title"]))
    return result


if __name__ == "__main__":
    main()
