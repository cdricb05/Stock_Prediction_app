"""Phase 3-Z - Research-Only Out-of-Sample Score Export and Signal Audit.

This phase builds a research-only out-of-sample (OOS) date/ticker *score panel* and a set of
signal-audit diagnostics on top of the existing local Phase 3-O / 3-P / 3-Q / 3-R outputs.
Its purpose is to make the walk-forward research model's per-name scores inspectable and to
prove (or disprove) whether those scores are actually predictive out-of-sample.

No exact OOS score panel was persisted by Phase 3-P (it scored in memory only and wrote
aggregate scoreboards). So this phase RECONSTRUCTS the panel faithfully by importing the
Phase 3-P walk-forward machinery (panel build + per-horizon embargo + NumPy closed-form ridge,
all transforms fit on training rows only) and capturing the per-row OOS scores it produces.
Nothing is faked: if reconstruction is impossible, the phase emits a BLOCKED result that names
the missing source and the exact next code change required.

It is research-only.  It needs NO network, NO ETF data, and NO provider / Alpha Vantage / Yahoo
/ Stooq / FRED / yfinance / paid-vendor call.  It trains NO production model, creates NO
production model candidate, writes NO deployable model artifact, computes NO production
predictions / scores / portfolio weights / order instructions, touches NO database, runs NO
migration, restarts NO service, enables NO model-v2 serving flag, writes nothing to the D:
drive (it only READS the local price panel, read-only, through the Phase 3-P loader), places NO
orders, and trades NOTHING.  Forward labels are read for validation-only OOS IC; they are never
turned into production predictions.

Run:
    python -B research/run_phase3z_oos_score_export_signal_audit.py
Test:
    python -B tests/test_phase3z_oos_score_export_signal_audit.py
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

# Reuse the Phase 3-P walk-forward machinery (panel build, embargo splits, ridge, IC). Importing
# the module only defines functions/constants; its run() is guarded by __main__.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import run_phase3p_multisignal_walkforward_model as p3p  # noqa: E402

PHASE = "3-Z"

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
_OUT_DIR = os.path.join(_REPO_ROOT, "research", "output")
_Z_DIR = os.path.join(_OUT_DIR, "phase3z_oos_score_export_signal_audit")
RESULT_JSON = os.path.join(_OUT_DIR, "phase3z_oos_score_export_signal_audit.json")

PHASE3O_RESULT_JSON = os.path.join(_OUT_DIR, "phase3o_multisignal_feature_factory.json")
PHASE3P_RESULT_JSON = os.path.join(_OUT_DIR, "phase3p_multisignal_walkforward_model.json")
PHASE3Q_RESULT_JSON = os.path.join(_OUT_DIR, "phase3q_model_robustness_diagnostics.json")
PHASE3R_RESULT_JSON = os.path.join(_OUT_DIR, "phase3r_macro_regime_walkforward.json")
PHASE3P_WEIGHTS_CSV = os.path.join(
    _OUT_DIR, "phase3p_multisignal_walkforward_model", "feature_weight_summary.csv")

# Inputs reached through the Phase 3-P / 3-O loaders (all local, read-only).
PRICE_CSV = p3p.PRICE_CSV
SECTOR_MAP_CSV = p3p.SECTOR_MAP_CSV
PHASE3L_PANEL_CSV = p3p.PHASE3L_PANEL_CSV
PHASE3M_EARNINGS_CSV = p3p.PHASE3M_EARNINGS_CSV

# --------------------------------------------------------------------------- #
# Reconstruction candidates (priority order). Each is a research ridge model from Phase 3-P.
# The panel exports as many blocks as fit under SIZE_BUDGET_BYTES (Git-safe < 50 MB); any
# block that does not fit is LOGGED as omitted (never silently dropped). The combined
# regime-interaction model is first because it carries all feature families (richest audit)
# and clears the readiness gate; the single strongest family model (technical_only) follows.
# --------------------------------------------------------------------------- #
CANDIDATES = [
    {"model_name": "ridge_combined_regime_interactions",
     "feature_set": "combined_regime_interactions", "horizon": 126},
    {"model_name": "ridge_technical_only", "feature_set": "technical_only", "horizon": 126},
    {"model_name": "ridge_combined_regime_interactions",
     "feature_set": "combined_regime_interactions", "horizon": 63},
    {"model_name": "ridge_technical_only", "feature_set": "technical_only", "horizon": 63},
]

SRC_RECONSTRUCTED = "reconstructed_from_phase3p_walkforward"

# Git-safety: keep every output file comfortably under the 50 MB hard limit.
HARD_FILE_LIMIT_BYTES = 50 * 1024 * 1024
SIZE_BUDGET_BYTES = 46 * 1024 * 1024

# --------------------------------------------------------------------------- #
# Readiness thresholds and decision values
# --------------------------------------------------------------------------- #
READY_MIN_ROWS = 100_000
READY_MIN_TICKERS = 50
READY_MIN_DATES = 500

REC_READY = "OOS_SCORE_EXPORT_READY_FOR_PORTFOLIO_SIMULATION"
REC_PARTIAL = "OOS_SCORE_EXPORT_PARTIAL_NEEDS_REPAIR"
REC_BLOCKED_NO_SOURCE = "OOS_SCORE_EXPORT_BLOCKED_NO_SCORE_SOURCE"
REC_BLOCKED_INPUTS = "OOS_SCORE_EXPORT_BLOCKED_INPUTS"
ALLOWED_RECOMMENDATIONS = (REC_READY, REC_PARTIAL, REC_BLOCKED_NO_SOURCE, REC_BLOCKED_INPUTS)

# --------------------------------------------------------------------------- #
# Output schema
# --------------------------------------------------------------------------- #
PANEL_COLUMNS = [
    "date", "ticker", "horizon", "model_name", "oos_score", "cross_sectional_rank",
    "rank_percentile", "decile", "forward_return", "label_available", "train_window_start",
    "train_window_end", "test_window_start", "test_window_end", "embargo_days",
    "feature_family_count", "source_status",
]

_OUT_PATHS = {
    "panel": "oos_score_panel.csv",
    "summary": "oos_score_summary.csv",
    "decile": "decile_forward_return_summary.csv",
    "yearly": "yearly_score_diagnostics.csv",
    "regime": "regime_score_diagnostics.csv",
    "sector": "sector_score_diagnostics.csv",
    "family": "feature_family_contribution_summary.csv",
    "leakage": "leakage_and_embargo_checks.csv",
    "readiness": "readiness_decision_table.csv",
}


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _round(x, n=6):
    if x is None:
        return None
    try:
        if isinstance(x, float) and not math.isfinite(x):
            return None
        return round(float(x), n)
    except (TypeError, ValueError):
        return None


def _mean(xs):
    xs = [x for x in xs if x is not None and (not isinstance(x, float) or math.isfinite(x))]
    return float(sum(xs) / len(xs)) if xs else None


def _rel(path):
    try:
        return os.path.relpath(path, _REPO_ROOT).replace("\\", "/")
    except ValueError:
        return path.replace("\\", "/")


def _out(o_dir, key):
    return os.path.join(o_dir, _OUT_PATHS[key])


def _write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _dump_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str)


# --------------------------------------------------------------------------- #
# Input confirmation
# --------------------------------------------------------------------------- #
def confirm_inputs(phase3o=PHASE3O_RESULT_JSON, phase3p=PHASE3P_RESULT_JSON,
                   price_csv=PRICE_CSV, phase3l=PHASE3L_PANEL_CSV):
    """Return (ok, checks). Inputs are required Phase 3-O/3-P result JSONs plus the local
    feature/price panels that Phase 3-P reconstructs the model panel from."""
    checks = {
        "phase3o_result_present": os.path.isfile(phase3o),
        "phase3p_result_present": os.path.isfile(phase3p),
        "phase3q_result_present": os.path.isfile(PHASE3Q_RESULT_JSON),
        "phase3r_result_present": os.path.isfile(PHASE3R_RESULT_JSON),
        "price_panel_present": os.path.isfile(price_csv),
        "sector_map_present": os.path.isfile(SECTOR_MAP_CSV),
        "phase3l_panel_present": os.path.isfile(phase3l),
        "phase3p_walkforward_present": False,
    }
    if checks["phase3p_result_present"]:
        try:
            with open(phase3p, "r", encoding="utf-8") as fh:
                p = json.load(fh)
            checks["phase3p_walkforward_present"] = bool(p.get("model_scoreboard"))
            checks["phase3p_oos_scores_in_memory"] = bool(p.get("research_oos_scores_computed"))
            checks["phase3p_panel_written_to_disk"] = bool(
                (p.get("feature_panel_summary") or {}).get("written_to_disk"))
        except (json.JSONDecodeError, OSError):
            checks["phase3p_result_present"] = False
    required = ["phase3o_result_present", "phase3p_result_present", "price_panel_present",
                "sector_map_present", "phase3l_panel_present", "phase3p_walkforward_present"]
    checks["all_required_present"] = all(checks.get(k) for k in required)
    return checks["all_required_present"], checks


# --------------------------------------------------------------------------- #
# OOS score reconstruction (faithful re-run of the Phase 3-P walk-forward, capturing per-row
# scores). Mirrors evaluate_ridge_model but emits the date/ticker score panel.
# --------------------------------------------------------------------------- #
def score_ridge_oos(panel, feature_set, horizon_days, model_name):
    """Re-run the Phase 3-P expanding walk-forward with per-horizon embargo for one ridge
    model/horizon and return (rows_df, fold_meta). Every transform is fit on TRAIN rows only;
    test rows are scored out-of-sample. NaN-safe."""
    label = p3p.LABEL_TEMPLATE % horizon_days
    feats = [f for f in p3p.FEATURE_SETS[feature_set] if f in panel.columns]
    fam_count = len({p3p._family_of(f) for f in feats})
    folds = p3p.walkforward_splits_with_embargo(panel["scoring_date"].unique(), horizon_days)
    blocks, fold_meta = [], []
    for fold in folds:
        train_df = panel[(panel["scoring_date"] < fold["embargo_cutoff"])
                         & pd.to_numeric(panel[label], errors="coerce").notna()]
        test_df = panel[(panel["scoring_date"] >= fold["test_start"])
                        & (panel["scoring_date"] <= fold["test_end"])
                        & pd.to_numeric(panel[label], errors="coerce").notna()]
        if len(train_df) < p3p.MIN_TRAIN_ROWS or len(test_df) < p3p.MIN_TEST_ROWS:
            continue
        lam = p3p._select_lambda(train_df, feats, label)
        x_tr, x_te, y_tr, finite, _scaler = p3p._prep_train_test(train_df, test_df, feats, label)
        if finite.sum() < p3p.MIN_TRAIN_ROWS:
            continue
        w = p3p.fit_ridge_closed_form(x_tr[finite], y_tr[finite], lam)
        score = p3p.predict_linear_score(x_te, w)
        fwd = pd.to_numeric(test_df[label], errors="coerce").to_numpy(dtype=float)
        tw_start, tw_end = train_df["scoring_date"].min(), train_df["scoring_date"].max()
        sec = test_df["sector"] if "sector" in test_df.columns else pd.Series(
            ["UNKNOWN"] * len(test_df), index=test_df.index)
        block = pd.DataFrame({
            "date": test_df["scoring_date"].dt.strftime("%Y-%m-%d").to_numpy(),
            "ticker": test_df["ticker"].astype(str).to_numpy(),
            "horizon": "%dd" % horizon_days,
            "model_name": model_name,
            "oos_score": np.round(np.asarray(score, dtype=float), 6),
            "forward_return": np.round(fwd, 6),
            "label_available": np.isfinite(fwd).astype(int),
            "train_window_start": p3p._dstr(tw_start),
            "train_window_end": p3p._dstr(tw_end),
            "test_window_start": p3p._dstr(fold["test_start"]),
            "test_window_end": p3p._dstr(fold["test_end"]),
            "embargo_days": horizon_days,
            "feature_family_count": fam_count,
            "source_status": SRC_RECONSTRUCTED,
            "_year": test_df["scoring_date"].dt.year.to_numpy(),
            "_sector": sec.fillna("UNKNOWN").astype(str).to_numpy(),
            "_risk_off": pd.to_numeric(test_df.get("market_risk_off_flag"), errors="coerce")
            .fillna(0).astype(int).to_numpy(),
            "_high_vol": pd.to_numeric(test_df.get("market_high_vol_flag"), errors="coerce")
            .fillna(0).astype(int).to_numpy(),
        })
        blocks.append(block)
        # Embargo gap in trading days between last train date and first test date.
        gap = int(np.busday_count(np.datetime64(pd.Timestamp(tw_end).date()),
                                  np.datetime64(pd.Timestamp(fold["test_start"]).date())))
        fold_meta.append({
            "model_name": model_name, "horizon": "%dd" % horizon_days,
            "test_year": fold["test_year"], "train_rows": int(len(train_df)),
            "test_rows": int(len(test_df)), "embargo_days": horizon_days,
            "train_window_end": p3p._dstr(tw_end),
            "test_window_start": p3p._dstr(fold["test_start"]),
            "embargo_gap_trading_days": gap})
    if not blocks:
        return pd.DataFrame(columns=PANEL_COLUMNS), fold_meta
    out = pd.concat(blocks, ignore_index=True)
    # Cross-sectional rank / percentile / decile by (date, horizon, model).
    grp = out.groupby(["date", "horizon", "model_name"], sort=False)["oos_score"]
    out["cross_sectional_rank"] = grp.rank(method="first", ascending=True).astype(int)
    out["rank_percentile"] = grp.rank(pct=True).round(6)
    out["decile"] = np.clip(np.floor(out["rank_percentile"] * 10).astype(int), 0, 9)
    return out, fold_meta


# --------------------------------------------------------------------------- #
# Diagnostics computed from the EXPORTED panel (so panel and summaries always agree)
# --------------------------------------------------------------------------- #
def _preds(df):
    """preds frame for the Phase 3-P IC / spread helpers."""
    return pd.DataFrame({
        "scoring_date": pd.to_datetime(df["date"]),
        "ticker": df["ticker"].to_numpy(),
        "score": df["oos_score"].to_numpy(dtype=float),
        "label": df["forward_return"].to_numpy(dtype=float)})


def _ic_stats(df):
    p = _preds(df)
    ic = p3p.daily_ic_series(p["scoring_date"].to_numpy(), p["score"].to_numpy(),
                             p["label"].to_numpy())
    dec, qnt, top_hit = p3p._decile_quintile_spreads(p)
    by_year = ic.groupby(ic.index.year).mean() if len(ic) else pd.Series(dtype=float)
    return {
        "mean_daily_rank_ic": _round(float(ic.mean()) if len(ic) else None),
        "median_daily_rank_ic": _round(float(ic.median()) if len(ic) else None),
        "rank_ic_hit_rate": _round(float((ic > 0).mean()) if len(ic) else None, 4),
        "top_minus_bottom_decile_spread": dec,
        "top_minus_bottom_quintile_spread": qnt,
        "top_decile_hit_rate": top_hit,
        "worst_year_ic": _round(float(by_year.min()) if len(by_year) else None),
        "best_year_ic": _round(float(by_year.max()) if len(by_year) else None),
        "stability_score": _round(float((by_year > 0).mean()) if len(by_year) else None, 4),
        "ic_days": int(len(ic)),
    }


def build_summary(panel):
    rows = []
    for (model, horizon), g in panel.groupby(["model_name", "horizon"], sort=False):
        s = _ic_stats(g)
        rows.append({
            "model_name": model, "horizon": horizon, "rows": int(len(g)),
            "tickers": int(g["ticker"].nunique()), "dates": int(g["date"].nunique()),
            "ic_days": s["ic_days"], "mean_daily_rank_ic": s["mean_daily_rank_ic"],
            "median_daily_rank_ic": s["median_daily_rank_ic"],
            "rank_ic_hit_rate": s["rank_ic_hit_rate"],
            "top_minus_bottom_decile_spread": s["top_minus_bottom_decile_spread"],
            "top_minus_bottom_quintile_spread": s["top_minus_bottom_quintile_spread"],
            "top_decile_hit_rate": s["top_decile_hit_rate"],
            "worst_year_ic": s["worst_year_ic"], "best_year_ic": s["best_year_ic"],
            "stability_score": s["stability_score"],
            "positive_ic_and_spread": int(
                (s["mean_daily_rank_ic"] or 0) > 0
                and (s["top_minus_bottom_decile_spread"] or 0) > 0)})
    return rows


def build_decile_summary(panel):
    rows = []
    for (model, horizon), g in panel.groupby(["model_name", "horizon"], sort=False):
        for dec in range(10):
            d = g[g["decile"] == dec]
            if d.empty:
                continue
            fr = pd.to_numeric(d["forward_return"], errors="coerce")
            rows.append({
                "model_name": model, "horizon": horizon, "decile": dec, "rows": int(len(d)),
                "mean_forward_return": _round(float(fr.mean()) if fr.notna().any() else None),
                "median_forward_return": _round(float(fr.median()) if fr.notna().any() else None),
                "mean_oos_score": _round(float(pd.to_numeric(d["oos_score"]).mean())),
                "mean_rank_percentile": _round(float(pd.to_numeric(d["rank_percentile"]).mean()))})
    return rows


def build_yearly(panel):
    rows = []
    for (model, horizon), g in panel.groupby(["model_name", "horizon"], sort=False):
        for year, gy in g.groupby("_year"):
            s = _ic_stats(gy)
            rows.append({
                "model_name": model, "horizon": horizon, "year": int(year),
                "rows": int(len(gy)), "tickers": int(gy["ticker"].nunique()),
                "mean_daily_rank_ic": s["mean_daily_rank_ic"],
                "rank_ic_hit_rate": s["rank_ic_hit_rate"],
                "top_minus_bottom_decile_spread": s["top_minus_bottom_decile_spread"]})
    return rows


def build_regime(panel):
    rows = []
    regimes = [
        ("risk_on", lambda g: g["_risk_off"] == 0),
        ("risk_off", lambda g: g["_risk_off"] == 1),
        ("low_vol", lambda g: g["_high_vol"] == 0),
        ("high_vol", lambda g: g["_high_vol"] == 1),
    ]
    for (model, horizon), g in panel.groupby(["model_name", "horizon"], sort=False):
        for name, mask_fn in regimes:
            sub = g[mask_fn(g)]
            if sub.empty:
                continue
            s = _ic_stats(sub)
            rows.append({
                "model_name": model, "horizon": horizon, "regime": name,
                "rows": int(len(sub)), "ic_days": s["ic_days"],
                "mean_daily_rank_ic": s["mean_daily_rank_ic"],
                "rank_ic_hit_rate": s["rank_ic_hit_rate"],
                "top_minus_bottom_decile_spread": s["top_minus_bottom_decile_spread"]})
    return rows


def build_sector(panel):
    rows = []
    for (model, horizon), g in panel.groupby(["model_name", "horizon"], sort=False):
        for sector, gs in g.groupby("_sector"):
            s = _ic_stats(gs)
            rows.append({
                "model_name": model, "horizon": horizon, "sector": str(sector),
                "rows": int(len(gs)), "tickers": int(gs["ticker"].nunique()),
                "mean_daily_rank_ic": s["mean_daily_rank_ic"],
                "top_minus_bottom_decile_spread": s["top_minus_bottom_decile_spread"]})
    return rows


def build_family_contribution(exported_keys, weights_csv=PHASE3P_WEIGHTS_CSV):
    """Per-family standardized-weight contribution for the exported (model, horizon) pairs,
    read from the Phase 3-P feature_weight_summary.csv (no model retrain). share = family
    summed mean_abs_weight / total mean_abs_weight for that model/horizon."""
    rows = []
    if not os.path.isfile(weights_csv):
        return rows
    try:
        wdf = pd.read_csv(weights_csv)
    except (OSError, pd.errors.ParserError):
        return rows
    wdf["mean_abs_weight"] = pd.to_numeric(wdf.get("mean_abs_weight"), errors="coerce").fillna(0.0)
    for (model, horizon) in exported_keys:
        sub = wdf[(wdf["model"] == model) & (wdf["horizon"] == horizon)]
        if sub.empty:
            continue
        total = float(sub["mean_abs_weight"].sum()) or 1.0
        fam = sub.groupby("feature_family")["mean_abs_weight"].agg(["sum", "count"])
        for family, r in fam.iterrows():
            rows.append({
                "model_name": model, "horizon": horizon, "feature_family": str(family),
                "feature_count": int(r["count"]),
                "sum_mean_abs_weight": _round(float(r["sum"])),
                "contribution_share": _round(float(r["sum"]) / total, 6)})
    return rows


def build_leakage_checks(fold_meta):
    """Per (model, horizon) embargo / leakage checks derived from the captured folds. Each row
    is decisionable: passed in {True, False}."""
    rows = []
    by_key = {}
    for f in fold_meta:
        by_key.setdefault((f["model_name"], f["horizon"]), []).append(f)
    for (model, horizon), folds in by_key.items():
        hd = int(str(horizon).replace("d", ""))
        # 1. embargo length equals the forward-return horizon for every fold
        emb_ok = all(f["embargo_days"] == hd for f in folds)
        rows.append({
            "model_name": model, "horizon": horizon, "check": "embargo_days_equals_horizon",
            "value": ",".join(sorted({str(f["embargo_days"]) for f in folds})),
            "passed": bool(emb_ok),
            "note": "per-horizon embargo equals the label horizon in trading days"})
        # 2. every fold's training window ends strictly before its test window starts
        order_ok = all(f["train_window_end"] < f["test_window_start"] for f in folds)
        rows.append({
            "model_name": model, "horizon": horizon,
            "check": "train_window_ends_before_test_window_starts",
            "value": str(sum(1 for f in folds if f["train_window_end"] < f["test_window_start"]))
            + "/" + str(len(folds)),
            "passed": bool(order_ok),
            "note": "no training row is dated on or after the test window start"})
        # 3. embargo gap (trading days between last train date and first test date) >= horizon
        gap_ok = all(f["embargo_gap_trading_days"] >= hd for f in folds)
        rows.append({
            "model_name": model, "horizon": horizon,
            "check": "embargo_gap_ge_horizon_trading_days",
            "value": str(min(f["embargo_gap_trading_days"] for f in folds)) + " min gap",
            "passed": bool(gap_ok),
            "note": "label window of the last train row cannot overlap the test window"})
        # 4. methodology guarantees (documented, always true by construction)
        rows.append({
            "model_name": model, "horizon": horizon,
            "check": "transforms_fit_on_train_rows_only", "value": "True", "passed": True,
            "note": "median imputer, standardizer, lambda and ridge weights fit on train rows"})
        rows.append({
            "model_name": model, "horizon": horizon,
            "check": "labels_used_for_validation_only", "value": "True", "passed": True,
            "note": "forward returns scored OOS for IC; never emitted as production predictions"})
    return rows


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #
def decide(inputs_ok, panel_rows, tickers, dates, leakage_failed, any_positive):
    if not inputs_ok:
        return REC_BLOCKED_INPUTS
    if panel_rows <= 0:
        return REC_BLOCKED_NO_SOURCE
    ready = (panel_rows >= READY_MIN_ROWS and tickers >= READY_MIN_TICKERS
             and dates >= READY_MIN_DATES and leakage_failed == 0 and any_positive)
    return REC_READY if ready else REC_PARTIAL


_NEXT_PHASE_TITLE = {
    REC_READY: "Turnover-Aware Portfolio Simulation and Risk Budget",
    REC_PARTIAL: "Repair OOS Score Export and Diagnostics",
    REC_BLOCKED_NO_SOURCE: "Add OOS Score Export to Walk-Forward Model Runner",
    REC_BLOCKED_INPUTS: "Repair Phase 3-Z Inputs",
}


def build_recommended_next_phase(rec):
    return {"phase": "4-A", "title": _NEXT_PHASE_TITLE.get(rec, _NEXT_PHASE_TITLE[REC_PARTIAL]),
            "purpose": ("Proceed to turnover-aware portfolio simulation on the exported OOS "
                        "score panel." if rec == REC_READY else
                        "Repair / extend the OOS score export before portfolio simulation.")}


def build_recommendation(rec, summary, panel_rows, tickers, dates, leakage_failed):
    best = None
    if summary:
        cand = [s for s in summary if s["mean_daily_rank_ic"] is not None]
        best = max(cand, key=lambda s: s["mean_daily_rank_ic"], default=None)
    return {
        "recommendation": rec,
        "allowed_values": list(ALLOWED_RECOMMENDATIONS),
        "research_only": True,
        "create_production_model_candidate_now": False,
        "train_production_model_now": False,
        "compute_portfolio_weights_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "panel_rows": int(panel_rows), "panel_tickers": int(tickers),
        "panel_dates": int(dates), "leakage_checks_failed": int(leakage_failed),
        "best_model_horizon": None if best is None else {
            "model_name": best["model_name"], "horizon": best["horizon"],
            "mean_daily_rank_ic": best["mean_daily_rank_ic"],
            "top_minus_bottom_decile_spread": best["top_minus_bottom_decile_spread"],
            "worst_year_ic": best["worst_year_ic"], "stability_score": best["stability_score"]},
        "reason": _decision_reason(rec, best),
    }


def _decision_reason(rec, best):
    if rec == REC_READY:
        b = best or {}
        return ("Reconstructed OOS score panel clears all readiness thresholds (>=100k rows, "
                ">=50 tickers, >=500 dates, no failed leakage check) and at least one "
                "model/horizon has positive mean daily rank IC and a positive top-minus-bottom "
                "decile spread (best: %s %s, IC %s). Research-only; no production candidate, "
                "predictions, scores, weights, or edge claim." % (
                    b.get("model_name"), b.get("horizon"), b.get("mean_daily_rank_ic")))
    if rec == REC_PARTIAL:
        return ("OOS score panel was reconstructed but does not clear every readiness threshold "
                "(row/ticker/date coverage, a failed leakage check, or no positive "
                "model/horizon). Repair the export before portfolio simulation.")
    if rec == REC_BLOCKED_NO_SOURCE:
        return ("No OOS score panel exists on disk and none could be reconstructed. Phase 3-P "
                "scored in memory only; add an OOS score export to the walk-forward runner "
                "(persist per-row test scores from evaluate_ridge_model) and re-run this phase.")
    return ("Required local inputs are missing or unreadable (Phase 3-O / 3-P result JSON, the "
            "Phase 3-L aligned panel, the sector map, or the local price panel). Restore them.")


# --------------------------------------------------------------------------- #
# Safety flags + interpretation
# --------------------------------------------------------------------------- #
def build_safety_flags(d_drive_read):
    return {
        "research_only": True,
        "database_touched": False,
        "database_write_executed": False,
        "migration_executed": False,
        "deployment_executed": False,
        "model_v2_enabled": False,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "deployable_model_artifact_written": False,
        "production_predictions_computed": False,
        "production_scores_computed": False,
        "portfolio_weights_computed": False,
        "order_instructions_created": False,
        "d_drive_read": bool(d_drive_read),
        "d_drive_written": False,
        "provider_api_called": False,
        "alpha_vantage_called": False,
        "fred_called": False,
        "stooq_called": False,
        "yahoo_called": False,
        "yfinance_called": False,
        "paid_vendor_api_called": False,
        "network_called": False,
        "data_faked": False,
        "labels_for_validation_only": True,
        "no_trading": True,
        "no_orders": True,
        "no_automation": True,
        "research_oos_scores_reconstructed": True,
    }


def build_interpretation(rec, panel_rows):
    return {
        "phase_is_research_only": True,
        "oos_score_panel_reconstructed": panel_rows > 0,
        "reconstruction_source": "phase3p_walkforward_in_memory_rerun",
        "scores_are_research_oos_not_production": True,
        "labels_used_for_validation_only": True,
        "results_remain_survivorship_biased": True,
        "production_model_trained": False,
        "production_predictions_computed": False,
        "portfolio_weights_computed": False,
        "deployable_model_artifact_written": False,
        "production_edge_claimed": False,
        "narrative": (
            "Phase 3-Z exports a research-only out-of-sample date/ticker score panel and signal "
            "audit. No OOS score panel was persisted by Phase 3-P (it scored in memory and wrote "
            "only aggregate scoreboards), so Phase 3-Z reconstructs the panel by re-running the "
            "Phase 3-P expanding walk-forward with per-horizon embargo and the same NumPy "
            "closed-form ridge ranking model - every transform fit on training rows only - and "
            "captures the per-row OOS test scores. It then computes cross-sectional rank / decile, "
            "forward-return-by-decile, yearly, regime and sector diagnostics, a feature-family "
            "contribution summary, and explicit leakage / embargo checks. It needs no network and "
            "no ETF data; it only reads the local price panel read-only through the Phase 3-P "
            "loader. It performs no deployment, runs no migration, writes to no database, restarts "
            "no service, enables no model-v2 serving flag, trains no production model, creates no "
            "production model candidate, writes no deployable model artifact, computes no "
            "production predictions / scores / portfolio weights / order instructions, writes "
            "nothing to the D: drive, and calls no provider / Alpha Vantage / Yahoo / Stooq / FRED "
            "/ yfinance / paid-vendor API. Forward labels are used for validation-only OOS IC and "
            "are never turned into production predictions. The universe is current-as-of, so all "
            "results remain survivorship-biased and claim no production edge.")
        if rec != REC_BLOCKED_NO_SOURCE else (
            "Phase 3-Z could not locate or reconstruct an out-of-sample score panel. No data was "
            "faked. The next code change is to persist per-row OOS test scores in the Phase 3-P "
            "walk-forward runner."),
    }


# --------------------------------------------------------------------------- #
# Readiness decision table
# --------------------------------------------------------------------------- #
def build_decision_table(rec, panel_rows, tickers, dates, leakage_failed, any_positive):
    rows = []

    def add(item, value, passed, note):
        rows.append({"decision_item": item, "value": value,
                     "passed": "" if passed is None else bool(passed), "note": note})

    add("recommendation", rec, rec == REC_READY, "READY only when every threshold passes")
    add("oos_score_panel_rows", panel_rows, panel_rows >= READY_MIN_ROWS,
        "READY needs >= %d rows" % READY_MIN_ROWS)
    add("distinct_tickers", tickers, tickers >= READY_MIN_TICKERS,
        "READY needs >= %d tickers" % READY_MIN_TICKERS)
    add("distinct_dates", dates, dates >= READY_MIN_DATES,
        "READY needs >= %d dates" % READY_MIN_DATES)
    add("leakage_checks_failed", leakage_failed, leakage_failed == 0,
        "no failed embargo / leakage check allowed")
    add("positive_model_horizon_exists", any_positive, any_positive,
        "at least one model/horizon with positive mean IC and positive decile spread")
    add("next_phase", "4-A", None, _NEXT_PHASE_TITLE.get(rec))
    return rows


# --------------------------------------------------------------------------- #
# Panel writer with Git-safe size budget
# --------------------------------------------------------------------------- #
def _estimate_block_bytes(block):
    sample = block.head(min(1000, len(block)))
    txt = sample.to_csv(index=False)
    header_len = len(txt.split("\n", 1)[0]) + 1
    per_row = (len(txt) - header_len) / max(1, len(sample))
    return per_row * len(block)


def select_within_budget(scored_blocks, budget=SIZE_BUDGET_BYTES, verbose=True):
    """Greedily keep candidate blocks (priority order) that fit under the byte budget. Returns
    (kept_panel_df, kept_keys, omitted_keys). Logs any omitted block (never silent)."""
    kept, kept_keys, omitted_keys = [], [], []
    total = 0.0
    header_overhead = len(",".join(PANEL_COLUMNS)) + 1
    for block, key in scored_blocks:
        if block.empty:
            omitted_keys.append({"key": key, "reason": "no_oos_rows"})
            continue
        b = _estimate_block_bytes(block)
        if kept and total + b + header_overhead > budget:
            omitted_keys.append({"key": key, "reason": "size_budget", "est_bytes": int(b)})
            if verbose:
                print("  [size] omitting %s (~%.1f MB) to keep panel < %d MB"
                      % (key, b / 1e6, budget // (1024 * 1024)))
            continue
        kept.append(block)
        kept_keys.append(key)
        total += b
    panel = pd.concat(kept, ignore_index=True) if kept else pd.DataFrame(columns=PANEL_COLUMNS)
    return panel, kept_keys, omitted_keys


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _finish_blocked(result_json_path, o_dir, rec, checks, note):
    for key in ("panel", "summary", "decile", "yearly", "regime", "sector", "family"):
        _write_csv(_out(o_dir, key), PANEL_COLUMNS if key == "panel" else ["note"],
                   [] if key == "panel" else [{"note": note}])
    _write_csv(_out(o_dir, "leakage"),
               ["model_name", "horizon", "check", "value", "passed", "note"],
               [{"model_name": "", "horizon": "", "check": "inputs_present", "value": "False",
                 "passed": False, "note": note}])
    _write_csv(_out(o_dir, "readiness"), ["decision_item", "value", "passed", "note"],
               build_decision_table(rec, 0, 0, 0, 1, False))
    result = {
        "phase": PHASE, "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_confirmation": checks, "blocked_reason": note,
        "oos_score_panel_exists_on_disk": False, "oos_score_panel_reconstructed": False,
        "oos_score_panel_rows": 0, "distinct_tickers": 0, "distinct_dates": 0,
        "exported_model_horizons": [], "omitted_model_horizons": [],
        "leakage_checks_failed": 1,
        "oos_score_summary": [], "best_model_horizon": None,
        "recommendation": build_recommendation(rec, [], 0, 0, 0, 1),
        "recommended_next_phase": build_recommended_next_phase(rec),
        "interpretation": build_interpretation(rec, 0),
        "outputs_written": {k: _rel(_out(o_dir, k)) for k in _OUT_PATHS},
    }
    result.update(build_safety_flags(d_drive_read=False))
    _dump_json(result_json_path, result)
    return result


def run(result_json_path=RESULT_JSON, o_dir=_Z_DIR, candidates=None,
        size_budget=SIZE_BUDGET_BYTES, verbose=True):
    candidates = CANDIDATES if candidates is None else candidates
    inputs_ok, checks = confirm_inputs()
    if not inputs_ok:
        if verbose:
            print("  inputs missing -> BLOCKED_INPUTS")
        return _finish_blocked(result_json_path, o_dir, REC_BLOCKED_INPUTS, checks,
                               "required Phase 3-O/3-P inputs or local panels missing")

    if verbose:
        print("  reconstructing OOS scores via the Phase 3-P walk-forward (read-only) ...")
    try:
        panel_full, _info = p3p.build_full_panel(
            PRICE_CSV, PHASE3L_PANEL_CSV, PHASE3M_EARNINGS_CSV, verbose=verbose)
    except Exception as exc:  # noqa: BLE001 - any panel-build failure is an input problem
        if verbose:
            print("  panel build failed -> BLOCKED_INPUTS: %r" % exc)
        return _finish_blocked(result_json_path, o_dir, REC_BLOCKED_INPUTS, checks,
                               "Phase 3-P panel reconstruction failed: %r" % exc)

    scored_blocks, all_fold_meta = [], []
    for c in candidates:
        key = "%s@%dd" % (c["model_name"], c["horizon"])
        if verbose:
            print("  scoring %s ..." % key)
        block, fold_meta = score_ridge_oos(
            panel_full, c["feature_set"], c["horizon"], c["model_name"])
        scored_blocks.append((block, key))
        all_fold_meta.extend(fold_meta)

    panel, kept_keys, omitted_keys = select_within_budget(scored_blocks, size_budget, verbose)

    if panel.empty:
        if verbose:
            print("  reconstruction produced no OOS rows -> BLOCKED_NO_SCORE_SOURCE")
        return _finish_blocked(result_json_path, o_dir, REC_BLOCKED_NO_SOURCE, checks,
                               "walk-forward reconstruction produced zero OOS test rows")

    # Diagnostics over the exported panel.
    exported_pairs = sorted({(m, h) for m, h in
                             zip(panel["model_name"], panel["horizon"])})
    summary = build_summary(panel)
    decile_rows = build_decile_summary(panel)
    yearly_rows = build_yearly(panel)
    regime_rows = build_regime(panel)
    sector_rows = build_sector(panel)
    family_rows = build_family_contribution(exported_pairs)
    leakage_rows = build_leakage_checks(
        [f for f in all_fold_meta
         if (f["model_name"], f["horizon"]) in set(exported_pairs)])
    leakage_failed = sum(1 for r in leakage_rows if r["passed"] is False)

    panel_rows = int(len(panel))
    tickers = int(panel["ticker"].nunique())
    dates = int(panel["date"].nunique())
    any_positive = any(s["positive_ic_and_spread"] == 1 for s in summary)
    rec = decide(inputs_ok, panel_rows, tickers, dates, leakage_failed, any_positive)

    # ---- write outputs ----
    if verbose:
        print("  writing OOS score panel + diagnostics ...")
    _write_csv(_out(o_dir, "panel"), PANEL_COLUMNS, panel[PANEL_COLUMNS].to_dict("records"))
    _write_csv(_out(o_dir, "summary"), [
        "model_name", "horizon", "rows", "tickers", "dates", "ic_days", "mean_daily_rank_ic",
        "median_daily_rank_ic", "rank_ic_hit_rate", "top_minus_bottom_decile_spread",
        "top_minus_bottom_quintile_spread", "top_decile_hit_rate", "worst_year_ic",
        "best_year_ic", "stability_score", "positive_ic_and_spread"], summary)
    _write_csv(_out(o_dir, "decile"), [
        "model_name", "horizon", "decile", "rows", "mean_forward_return",
        "median_forward_return", "mean_oos_score", "mean_rank_percentile"], decile_rows)
    _write_csv(_out(o_dir, "yearly"), [
        "model_name", "horizon", "year", "rows", "tickers", "mean_daily_rank_ic",
        "rank_ic_hit_rate", "top_minus_bottom_decile_spread"], yearly_rows)
    _write_csv(_out(o_dir, "regime"), [
        "model_name", "horizon", "regime", "rows", "ic_days", "mean_daily_rank_ic",
        "rank_ic_hit_rate", "top_minus_bottom_decile_spread"], regime_rows)
    _write_csv(_out(o_dir, "sector"), [
        "model_name", "horizon", "sector", "rows", "tickers", "mean_daily_rank_ic",
        "top_minus_bottom_decile_spread"], sector_rows)
    _write_csv(_out(o_dir, "family"), [
        "model_name", "horizon", "feature_family", "feature_count", "sum_mean_abs_weight",
        "contribution_share"], family_rows)
    _write_csv(_out(o_dir, "leakage"), [
        "model_name", "horizon", "check", "value", "passed", "note"], leakage_rows)
    _write_csv(_out(o_dir, "readiness"), ["decision_item", "value", "passed", "note"],
               build_decision_table(rec, panel_rows, tickers, dates, leakage_failed,
                                     any_positive))

    panel_bytes = os.path.getsize(_out(o_dir, "panel"))

    result = {
        "phase": PHASE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "phase3o_result_json": _rel(PHASE3O_RESULT_JSON),
            "phase3p_result_json": _rel(PHASE3P_RESULT_JSON),
            "phase3q_result_json": _rel(PHASE3Q_RESULT_JSON),
            "phase3r_result_json": _rel(PHASE3R_RESULT_JSON),
            "phase3p_feature_weight_summary_csv": _rel(PHASE3P_WEIGHTS_CSV),
            "price_history_csv": PRICE_CSV.replace("\\", "/"),
            "sector_map_csv": _rel(SECTOR_MAP_CSV),
            "phase3l_aligned_panel_csv": _rel(PHASE3L_PANEL_CSV),
            "phase3m_earnings_features_csv": _rel(PHASE3M_EARNINGS_CSV),
        },
        "outputs_written": {k: _rel(_out(o_dir, k)) for k in _OUT_PATHS},
        "input_confirmation": checks,
        "oos_score_panel_exists_on_disk": False,
        "oos_score_panel_reconstructed": True,
        "reconstruction_method": (
            "re-ran Phase 3-P expanding walk-forward (per-horizon embargo, NumPy closed-form "
            "ridge, transforms fit on train rows only) and captured per-row OOS test scores"),
        "oos_score_panel_rows": panel_rows,
        "oos_score_panel_bytes": int(panel_bytes),
        "distinct_tickers": tickers,
        "distinct_dates": dates,
        "horizons_exported": sorted({h for _m, h in exported_pairs}),
        "exported_model_horizons": ["%s@%s" % (m, h) for m, h in exported_pairs],
        "omitted_model_horizons": omitted_keys,
        "panel_columns": PANEL_COLUMNS,
        "leakage_checks_failed": int(leakage_failed),
        "oos_score_summary": summary,
        "best_model_horizon": build_recommendation(
            rec, summary, panel_rows, tickers, dates, leakage_failed)["best_model_horizon"],
        "recommendation": build_recommendation(
            rec, summary, panel_rows, tickers, dates, leakage_failed),
        "recommended_next_phase": build_recommended_next_phase(rec),
        "interpretation": build_interpretation(rec, panel_rows),
    }
    result.update(build_safety_flags(d_drive_read=True))
    _dump_json(result_json_path, result)
    if verbose:
        print("  phase %s | rec %s | rows %d | tickers %d | dates %d | panel %.1f MB"
              % (PHASE, rec, panel_rows, tickers, dates, panel_bytes / 1e6))
    return result


def main():
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
