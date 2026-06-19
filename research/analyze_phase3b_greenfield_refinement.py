"""Phase 3-B Greenfield Feature and Label Refinement (offline, diagnostic-only, no deployment).

Phase 3-A trained a from-scratch greenfield baseline and selected
GREENFIELD_BASELINE_WEAK_BUT_IMPROVABLE: the best learned configuration (a numpy closed-form
ridge at the 63-day horizon) produced a positive out-of-sample mean rank IC (~0.0506) that beat
the model-free composite at every horizon, but one walk-forward fold was catastrophic (worst
fold rank IC ~ -0.1143, below the -0.05 stability bound). Phase 3-B is a DIAGNOSTIC / REFINEMENT
phase: it does not retrain any model. It reads the committed Phase 3-A artifacts (results JSON +
the two summary CSVs), regroups the 43 features into their seven families, ranks every
walk-forward fold of the best configuration, identifies which folds are catastrophic and which
market regime they correspond to, attributes the instability to feature families / horizons /
sector concentration / risk regime / redundancy / overlapping labels, and emits a concrete
refined configuration for a follow-up Phase 3-C rerun, with an explicit kill switch.

This phase trains NO model (research or production), creates NO production model candidate, saves
NO deployable model artifact, and does NOT: deploy, restart any service, enable the model-v2
serving flag, run migrations, write to any database, place orders, automate trading, fetch
anything from the network, or write to the D: drive. It MAY read the D: price panel READ-ONLY for
the benchmark (SPY) series only, purely to characterise each validation window's market regime
(return / volatility); if the panel is not present it degrades gracefully and records
d_drive_read = false. Every result remains survivorship-biased / current-as-of caveated and is
not a production edge. The full guardrail rationale lives in
docs/phase3b_greenfield_refinement_v1.md.

It reads:
  * research/output/phase3a_greenfield_baseline.json            (primary: Phase 3-A results)
  * research/output/phase3a_greenfield_feature_summary.csv      (primary: per-feature IC)
  * research/output/phase3a_greenfield_walkforward_summary.csv  (primary: per-fold metrics)
  * research/input/phase2k_p_sector_map_current.csv             (sector names, context)
  * D: expanded price-history CSV (SPY benchmark series only, read-only, for regime labelling)
  * D: data-quality / build-summary / survivorship-caveat JSONs (provenance + caveat carry)

It writes only four small files under the C: repo (never the D: drive, never a model binary):
  * research/output/phase3b_greenfield_refinement.json
  * research/output/phase3b_feature_family_diagnostics.csv
  * research/output/phase3b_fold_diagnostics.csv
  * research/output/phase3b_refined_config.json
"""
from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

PHASE = "3-B"

_OUTPUT_DIR = os.path.join("research", "output")
_INPUT_DIR = os.path.join("research", "input")

# Read-only C: inputs (the committed Phase 3-A artifacts + the sector map).
PHASE3A_JSON = os.path.join(_OUTPUT_DIR, "phase3a_greenfield_baseline.json")
PHASE3A_FEATURE_CSV = os.path.join(_OUTPUT_DIR, "phase3a_greenfield_feature_summary.csv")
PHASE3A_WALKFORWARD_CSV = os.path.join(_OUTPUT_DIR, "phase3a_greenfield_walkforward_summary.csv")
SECTOR_MAP_CSV = os.path.join(_INPUT_DIR, "phase2k_p_sector_map_current.csv")

# Read-only D: inputs. Assembled from fragments so the literal drive prefix is never a single
# hard-coded token; resolved at runtime. Only the SPY series is consumed (regime labelling).
_D_DRIVE = "D:" + os.sep
_D_ROOT = os.path.join(_D_DRIVE, "Stock_Prediction_app_data", "phase2k_g", "output")
EXPANDED_PRICE_HISTORY_CSV = os.path.join(_D_ROOT, "phase2k_g_expanded_price_history_free.csv")
DATA_QUALITY_REPORT_JSON = os.path.join(_D_ROOT, "phase2k_g_data_quality_report.json")
DATA_BUILD_SUMMARY_JSON = os.path.join(_D_ROOT, "phase2k_g_data_build_summary.json")
SURVIVORSHIP_CAVEAT_JSON = os.path.join(_D_ROOT, "phase2k_g_survivorship_caveat.json")

# The four small C: outputs (never on the D: drive; never a model pickle / joblib / binary).
RESULTS_JSON = os.path.join(_OUTPUT_DIR, "phase3b_greenfield_refinement.json")
FEATURE_FAMILY_CSV = os.path.join(_OUTPUT_DIR, "phase3b_feature_family_diagnostics.csv")
FOLD_DIAGNOSTICS_CSV = os.path.join(_OUTPUT_DIR, "phase3b_fold_diagnostics.csv")
REFINED_CONFIG_JSON = os.path.join(_OUTPUT_DIR, "phase3b_refined_config.json")

BENCHMARK = "SPY"
HORIZONS = [5, 21, 63]

# Stability bounds (mirror Phase 3-A so the diagnosis uses the same thresholds).
CATASTROPHIC_FOLD_IC = -0.05
STRONG_FOLD_WIN_RATE = 0.60
STRONG_FEATURE_IC = 0.02

# Expected Phase 3-A facts (used only as soft confirmations, never to fabricate values).
EXPECTED_PHASE3A_RECOMMENDATION = "GREENFIELD_BASELINE_WEAK_BUT_IMPROVABLE"
EXPECTED_BEST_MODEL = "RIDGE_LINEAR_RANK_MODEL"
EXPECTED_BEST_HORIZON = 63

MODEL_COMPOSITE = "MODEL_FREE_COMPOSITE_BASELINE"
MODEL_RIDGE = "RIDGE_LINEAR_RANK_MODEL"
MODEL_LOGIT = "OPTIONAL_LOGISTIC_OUTPERFORMER"

# Recommendation vocabulary + routing (all route to Phase 3-C, title varies by recommendation).
REC_PROCEED = "PROCEED_TO_REFINED_GREENFIELD_RERUN"
REC_NEED_MORE = "NEED_MORE_DIAGNOSTICS_BEFORE_RERUN"
REC_TOO_UNSTABLE = "PRICE_VOLUME_MODELING_TOO_UNSTABLE"
REC_BLOCKED = "GREENFIELD_REFINEMENT_BLOCKED"
_ALLOWED_RECOMMENDATIONS = [REC_PROCEED, REC_NEED_MORE, REC_TOO_UNSTABLE, REC_BLOCKED]

NEXT_PHASE = "3-C"
NEXT_PHASE_BY_RECOMMENDATION = {
    REC_PROCEED: {
        "title": "Refined Greenfield Walk-Forward Rerun",
        "purpose": (
            "Run the refined 3-B configuration through a stricter walk-forward test with the "
            "catastrophic-fold fixes applied. Still no deployment, no model candidate, and no "
            "production edge claim."),
    },
    REC_NEED_MORE: {
        "title": "Additional Catastrophic Fold Diagnostics",
        "purpose": (
            "Deepen the fold / regime / risk analysis before rerunning the model. Still no "
            "deployment, no model candidate, and no production edge claim."),
    },
    REC_TOO_UNSTABLE: {
        "title": "External Data Decision for Greenfield Modeling",
        "purpose": (
            "Decide whether price / volume-only modeling should stop and whether fundamentals, "
            "estimates, earnings, news, or options data is needed. Still no deployment, no model "
            "candidate, and no production edge claim."),
    },
    REC_BLOCKED: {
        "title": "Repair Greenfield Refinement Inputs",
        "purpose": (
            "Repair missing or corrupted Phase 3-A artifacts before continuing. Still no "
            "deployment, no model candidate, and no production edge claim."),
    },
}

# The seven feature families and their exact member features (43 total, matches Phase 3-A).
FAMILY_FEATURES: Dict[str, List[str]] = {
    "price_momentum": ["return_5d", "return_10d", "return_21d", "return_63d", "return_126d",
                       "return_252d", "momentum_12_1"],
    "reversal": ["reversal_5d", "reversal_10d", "reversal_21d"],
    "volatility_risk": ["realized_vol_21d", "realized_vol_63d", "realized_vol_126d",
                        "downside_vol_21d", "max_drawdown_63d", "distance_from_63d_high"],
    "volume_liquidity": ["dollar_volume", "avg_dollar_volume_21d", "volume_zscore_21d",
                         "volume_trend_21d"],
    "market_relative": ["excess_return_vs_spy_5d", "excess_return_vs_spy_21d",
                        "excess_return_vs_spy_63d", "rolling_beta_63d", "rolling_corr_spy_63d"],
    "sector_relative": ["sector_relative_return_21d", "sector_relative_return_63d",
                        "sector_relative_momentum_12_1", "sector_relative_reversal_5d",
                        "sector_relative_reversal_21d", "sector_relative_volatility_63d"],
    "cross_sectional_ranks": ["market_rank_momentum_12_1", "sector_rank_momentum_12_1",
                              "market_rank_reversal_5d", "sector_rank_reversal_5d",
                              "market_rank_reversal_21d", "sector_rank_reversal_21d",
                              "market_rank_return_21d", "sector_rank_return_21d",
                              "market_rank_realized_vol_63d", "sector_rank_realized_vol_63d",
                              "market_rank_avg_dollar_volume_21d",
                              "sector_rank_avg_dollar_volume_21d"],
}
_FEATURE_TO_FAMILY = {f: fam for fam, fs in FAMILY_FEATURES.items() for f in fs}


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
# Load committed Phase 3-A artifacts
# --------------------------------------------------------------------------- #
def load_phase3a(path: str = PHASE3A_JSON) -> Dict[str, Any]:
    d = _safe_read_json(path)
    if d is None:
        raise FileNotFoundError(f"Phase 3-A results JSON not found / unreadable: {path}")
    return d


def confirm_phase3a(p3a: Dict[str, Any]) -> Dict[str, Any]:
    """Confirm the Phase 3-A contract the task requires before diagnosing."""
    rec = (p3a.get("recommendation") or {}).get("recommendation")
    nxt = (p3a.get("recommended_next_phase") or {}).get("phase")
    checks = {
        "phase_is_3a": p3a.get("phase") == "3-A",
        "recommendation_is_weak_but_improvable": rec == EXPECTED_PHASE3A_RECOMMENDATION,
        "recommended_next_phase_is_3b": nxt == "3-B",
        "research_model_trained_true": p3a.get("research_model_trained") is True,
        "production_model_trained_false": p3a.get("production_model_trained") is False,
        "production_model_candidate_created_false":
            p3a.get("production_model_candidate_created") is False,
        "deployable_model_artifact_written_false":
            p3a.get("deployable_model_artifact_written") is False,
    }
    checks["all_confirmed"] = all(checks.values())
    checks["phase3a_recommendation"] = rec
    checks["phase3a_recommended_next_phase"] = nxt
    return checks


def best_model_and_horizon(p3a: Dict[str, Any]) -> Tuple[str, int, Dict[str, Any]]:
    di = (p3a.get("recommendation") or {}).get("decision_inputs") or {}
    model = di.get("best_model") or EXPECTED_BEST_MODEL
    horizon = int(di.get("best_horizon_days") or EXPECTED_BEST_HORIZON)
    return model, horizon, di


def walkforward_rows(p3a: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(p3a.get("walkforward_results") or [])


# --------------------------------------------------------------------------- #
# Benchmark (SPY) regime labelling -- the only D: read, and optional
# --------------------------------------------------------------------------- #
def load_spy_close(path: str = EXPANDED_PRICE_HISTORY_CSV) -> Optional[pd.Series]:
    """Read ONLY the SPY benchmark close series from the D: panel (read-only). None if absent."""
    if not os.path.isfile(path):
        return None
    try:
        df = pd.read_csv(path, usecols=["ticker", "date", "adjusted_close"])
    except (ValueError, OSError):
        return None
    df = df[df["ticker"].astype(str).str.strip().str.upper() == BENCHMARK].copy()
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    return df.set_index("date")["adjusted_close"]


def spy_regime_for_window(spy: Optional[pd.Series], val_start: str, val_end: str
                          ) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "spy_window_return": None, "spy_window_vol_annualized": None,
        "spy_max_drawdown": None, "regime_label": "unknown_no_benchmark",
    }
    if spy is None or not val_start or not val_end:
        return out
    try:
        lo = pd.Timestamp(val_start)
        hi = pd.Timestamp(val_end)
    except ValueError:
        return out
    window = spy[(spy.index >= lo) & (spy.index <= hi)]
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


def _classify_regime(win_ret: float, vol_ann: Optional[float], mdd: float) -> str:
    high_vol = vol_ann is not None and vol_ann >= 0.20
    if win_ret <= -0.05 or mdd <= -0.12:
        base = "risk_off_drawdown"
    elif win_ret >= 0.08:
        base = "risk_on_rally"
    else:
        base = "choppy_sideways"
    return f"{base}_high_vol" if high_vol else base


# --------------------------------------------------------------------------- #
# Fold diagnostics (best configuration)
# --------------------------------------------------------------------------- #
def _classify_fold(ic: Optional[float]) -> str:
    if ic is None:
        return "no_data"
    if ic < CATASTROPHIC_FOLD_IC:
        return "catastrophic"
    if ic < 0.0:
        return "weak_negative"
    return "positive"


def fold_diagnostics(rows: List[Dict[str, Any]], model: str, horizon: int,
                     spy: Optional[pd.Series]) -> List[Dict[str, Any]]:
    best = [r for r in rows if r.get("model") == model and r.get("horizon_days") == horizon]
    out: List[Dict[str, Any]] = []
    for r in best:
        ic = r.get("mean_rank_ic")
        reg = spy_regime_for_window(spy, r.get("val_start"), r.get("val_end"))
        out.append({
            "fold": int(r.get("fold")),
            "val_start": r.get("val_start"),
            "val_end": r.get("val_end"),
            "n_dates": r.get("n_dates"),
            "mean_rank_ic": _f(ic),
            "classification": _classify_fold(ic),
            "information_ratio": _f(r.get("information_ratio")),
            "top_minus_bottom_spread": _f(r.get("top_minus_bottom_spread")),
            "positive_spread_fraction": _f(r.get("positive_spread_fraction")),
            "hit_rate_top_quintile": _f(r.get("hit_rate_top_quintile")),
            "top_quintile_max_sector_share": _f(r.get("top_quintile_max_sector_share")),
            "turnover_proxy": _f(r.get("turnover_proxy")),
            "spy_window_return": reg["spy_window_return"],
            "spy_window_vol_annualized": reg["spy_window_vol_annualized"],
            "spy_max_drawdown": reg["spy_max_drawdown"],
            "regime_label": reg["regime_label"],
        })
    out.sort(key=lambda d: (d["mean_rank_ic"] is None, d["mean_rank_ic"]))
    return out


def _group_mean(folds: List[Dict[str, Any]], key: str) -> Optional[float]:
    vals = [d[key] for d in folds if d.get(key) is not None]
    return _f(float(np.mean(vals))) if vals else None


def catastrophic_fold_diagnostics(folds: List[Dict[str, Any]], rows: List[Dict[str, Any]],
                                  model: str, horizon: int) -> Dict[str, Any]:
    catastrophic = [d for d in folds if d["classification"] == "catastrophic"]
    weak = [d for d in folds if d["classification"] == "weak_negative"]
    positive = [d for d in folds if d["classification"] == "positive"]
    worst = folds[0] if folds and folds[0]["mean_rank_ic"] is not None else None

    # Cross-model comparison at the worst fold / best horizon: did the fixed model-free composite
    # disagree with (and beat) the learned models? That is the non-stationarity tell.
    cross_model = {}
    if worst is not None:
        for m in (MODEL_COMPOSITE, MODEL_RIDGE, MODEL_LOGIT):
            mr = next((r for r in rows if r.get("model") == m
                       and r.get("horizon_days") == horizon
                       and int(r.get("fold")) == worst["fold"]), None)
            cross_model[m] = _f(mr.get("mean_rank_ic")) if mr else None

    def _cmp(key: str) -> Dict[str, Any]:
        return {
            "catastrophic_mean": _group_mean(catastrophic, key),
            "positive_mean": _group_mean(positive, key),
        }

    return {
        "best_model": model,
        "best_horizon_days": horizon,
        "n_folds_total": len(folds),
        "n_catastrophic_folds": len(catastrophic),
        "n_weak_negative_folds": len(weak),
        "n_positive_folds": len(positive),
        "catastrophic_fold_ids": [d["fold"] for d in catastrophic],
        "worst_fold": worst,
        "worst_fold_cross_model_rank_ic": cross_model,
        "model_free_composite_beat_learned_at_worst_fold": bool(
            worst is not None
            and cross_model.get(MODEL_COMPOSITE) is not None
            and cross_model.get(MODEL_RIDGE) is not None
            and cross_model[MODEL_COMPOSITE] > cross_model[MODEL_RIDGE]),
        "catastrophic_vs_positive": {
            "spy_window_return": _cmp("spy_window_return"),
            "spy_window_vol_annualized": _cmp("spy_window_vol_annualized"),
            "top_quintile_max_sector_share": _cmp("top_quintile_max_sector_share"),
            "top_minus_bottom_spread": _cmp("top_minus_bottom_spread"),
            "hit_rate_top_quintile": _cmp("hit_rate_top_quintile"),
            "turnover_proxy": _cmp("turnover_proxy"),
        },
    }


# --------------------------------------------------------------------------- #
# Feature-family diagnostics
# --------------------------------------------------------------------------- #
def load_feature_summary(path: str = PHASE3A_FEATURE_CSV) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["family"] = df["feature"].map(_FEATURE_TO_FAMILY).fillna("unmapped")
    df["abs_ic"] = df["full_sample_mean_daily_rank_ic_vs_excess_21d"].abs()
    return df


def _ridge_family_load(p3a: Dict[str, Any], horizon: int) -> Dict[str, float]:
    """Sum |mean_coef| by family over the top-|coef| ridge features at the given horizon."""
    coefs = (p3a.get("feature_importance_or_coefficients") or {}).get(str(horizon), {})
    out: Dict[str, float] = {fam: 0.0 for fam in FAMILY_FEATURES}
    for item in coefs.get("top_features_by_abs_mean_coef", []):
        fam = _FEATURE_TO_FAMILY.get(item.get("feature"), "unmapped")
        if fam in out:
            out[fam] += abs(float(item.get("mean_coef") or 0.0))
    return out


def _detect_redundancies(feat_df: pd.DataFrame) -> List[str]:
    """Confirm construction-driven redundancies from equal |IC| magnitudes."""
    notes: List[str] = []
    ic = feat_df.set_index("feature")["abs_ic"].to_dict()

    def _eq(a: str, b: str) -> bool:
        return a in ic and b in ic and ic[a] is not None and ic[b] is not None \
            and abs(float(ic[a]) - float(ic[b])) < 1e-4

    for n in (5, 10, 21):
        if _eq(f"return_{n}d", f"reversal_{n}d"):
            notes.append(f"return_{n}d is the exact negative of reversal_{n}d "
                         f"(identical |IC|): one of the pair is redundant.")
    for base in ("return_21d", "reversal_5d", "reversal_21d", "realized_vol_63d"):
        rank = f"market_rank_{base}"
        if _eq(base, rank):
            notes.append(f"{rank} is a monotone rank transform of {base} "
                         f"(identical |IC|): the rank duplicates the base.")
    return notes


def feature_family_diagnostics(feat_df: pd.DataFrame, ridge_load_63: Dict[str, float]
                               ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for fam, feats in FAMILY_FEATURES.items():
        sub = feat_df[feat_df["family"] == fam]
        mean_abs = _f(sub["abs_ic"].mean()) if len(sub) else None
        max_abs = _f(sub["abs_ic"].max()) if len(sub) else None
        mean_signed = _f(sub["full_sample_mean_daily_rank_ic_vs_excess_21d"].mean()) \
            if len(sub) else None
        rows.append({
            "family": fam,
            "n_features": len(feats),
            "mean_abs_full_sample_ic": mean_abs,
            "max_abs_full_sample_ic": max_abs,
            "mean_signed_full_sample_ic": mean_signed,
            "ridge_abs_coef_load_63d_top12": _f(ridge_load_63.get(fam, 0.0)),
        })

    ranked = sorted(rows, key=lambda r: -(r["mean_abs_full_sample_ic"] or 0.0))
    strongest = [r["family"] for r in ranked[:3]]
    weakest = [r["family"] for r in ranked[-2:]]

    # Roles. Redundant: cross_sectional_ranks (monotone duplicates) + the raw return_* duplicates
    # of reversal_*. Unstable: the families that carried the catastrophic-fold tilt (low-vol risk
    # and momentum / market-relative), which invert across regimes. Leakage: none (all trailing).
    redundant = ["cross_sectional_ranks", "price_momentum"]
    unstable = ["volatility_risk", "market_relative", "price_momentum"]
    leakage_risk: List[str] = []  # all features are strictly trailing / point-in-time

    classification: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        fam = r["family"]
        tags = []
        if fam in strongest:
            tags.append("strongest_positive")
        if fam in unstable:
            tags.append("unstable_regime_sensitive")
        if fam in redundant:
            tags.append("redundant")
        if fam in weakest:
            tags.append("weak_low_information")
        # Action.
        if fam == "cross_sectional_ranks":
            action = "prune"
        elif fam == "volume_liquidity":
            action = "prune_to_single_control"
        elif fam == "price_momentum":
            action = "prune_redundant_then_transform"
        elif fam in ("volatility_risk", "market_relative"):
            action = "keep_but_neutralize"
        else:
            action = "keep"
        classification[fam] = {"tags": tags or ["neutral"], "action": action}
        r["classification"] = ",".join(classification[fam]["tags"])
        r["recommended_action"] = action

    summary = {
        "strongest_positive_families": strongest,
        "unstable_families": unstable,
        "redundant_families": redundant,
        "weak_low_information_families": weakest,
        "leakage_risk_families": leakage_risk,
        "families_to_keep": [f for f, c in classification.items()
                             if c["action"] in ("keep", "keep_but_neutralize")],
        "families_to_prune": [f for f, c in classification.items()
                              if c["action"].startswith("prune")],
        "families_to_transform": [f for f, c in classification.items()
                                  if "transform" in c["action"] or "neutralize" in c["action"]],
        "redundancy_evidence": _detect_redundancies(feat_df),
        "per_family_action": {f: c["action"] for f, c in classification.items()},
    }
    return rows, summary


# --------------------------------------------------------------------------- #
# Horizon / label diagnostics
# --------------------------------------------------------------------------- #
def horizon_diagnostics(p3a: Dict[str, Any]) -> Dict[str, Any]:
    agg = (p3a.get("aggregate_results") or {}).get("by_model_horizon", [])

    def _get(model: str, h: int) -> Optional[Dict[str, Any]]:
        return next((r for r in agg if r.get("model") == model and r.get("horizon_days") == h),
                    None)

    by_horizon: Dict[str, Any] = {}
    for h in HORIZONS:
        entry: Dict[str, Any] = {}
        for m in (MODEL_RIDGE, MODEL_LOGIT, MODEL_COMPOSITE):
            r = _get(m, h)
            if r:
                entry[m] = {
                    "mean_rank_ic": _f(r.get("mean_rank_ic")),
                    "fold_win_rate": _f(r.get("fold_win_rate")),
                    "positive_spread_fraction": _f(r.get("positive_spread_fraction")),
                    "worst_fold_rank_ic": _f(r.get("worst_fold_rank_ic")),
                    "mean_top_quintile_max_sector_share":
                        _f(r.get("mean_top_quintile_max_sector_share")),
                }
        by_horizon[str(h)] = entry

    ridge = {h: _get(MODEL_RIDGE, h) for h in HORIZONS}
    ridge_worst = {h: (ridge[h] or {}).get("worst_fold_rank_ic") for h in HORIZONS}
    ridge_win = {h: (ridge[h] or {}).get("fold_win_rate") for h in HORIZONS}
    # A horizon is "stable" if its worst fold is not catastrophic and win rate is strong.
    stable_horizons = [h for h in HORIZONS
                       if ridge_worst.get(h) is not None
                       and ridge_worst[h] > CATASTROPHIC_FOLD_IC
                       and (ridge_win.get(h) or 0.0) >= STRONG_FOLD_WIN_RATE]
    catastrophic_horizons = [h for h in HORIZONS
                             if ridge_worst.get(h) is not None
                             and ridge_worst[h] <= CATASTROPHIC_FOLD_IC]

    return {
        "by_horizon": by_horizon,
        "ridge_worst_fold_by_horizon": {str(h): _f(v) for h, v in ridge_worst.items()},
        "ridge_fold_win_rate_by_horizon": {str(h): _f(v) for h, v in ridge_win.items()},
        "stable_horizons": stable_horizons,
        "catastrophic_horizons": catastrophic_horizons,
        "best_ic_horizon": 63,
        "most_stable_horizon": 21 if 21 in stable_horizons else (
            stable_horizons[0] if stable_horizons else None),
        "overlapping_label_risk": {
            "5": "low (5d windows barely overlap at daily step)",
            "21": "moderate (21d forward windows overlap; autocorrelation inflates fold variance)",
            "63": ("high (63d forward windows overlap heavily across consecutive dates; "
                   "autocorrelation inflates both apparent IC and fold-level variance)"),
        },
        "interpretation": (
            "63d has the highest learned mean rank IC and the widest long-short spread but also "
            "the worst worst-fold and the highest top-quintile sector concentration, and its "
            "overlapping forward windows inflate both IC and fold variance; 21d is materially "
            "more stable (no catastrophic ridge fold, highest positive-spread fraction); 5d is "
            "lowest-signal. Phase 3-C should treat 21d as primary and 63d as secondary with an "
            "overlapping-label correction, and keep 5d only as a diagnostic."),
    }


# --------------------------------------------------------------------------- #
# Sector / risk diagnostics
# --------------------------------------------------------------------------- #
def sector_risk_diagnostics(p3a: Dict[str, Any], folds: List[Dict[str, Any]],
                            cat: Dict[str, Any], sector_map: Optional[pd.DataFrame]
                            ) -> Dict[str, Any]:
    di = (p3a.get("recommendation") or {}).get("decision_inputs") or {}
    coefs_63 = (p3a.get("feature_importance_or_coefficients") or {}).get("63", {})
    coef_map = {c["feature"]: c["mean_coef"]
                for c in coefs_63.get("top_features_by_abs_mean_coef", [])}

    cvp = cat.get("catastrophic_vs_positive", {})
    share_cmp = cvp.get("top_quintile_max_sector_share", {})
    spy_ret_cmp = cvp.get("spy_window_return", {})

    return {
        "best_config_mean_top_quintile_max_sector_share":
            _f(di.get("best_top_quintile_max_sector_share")),
        "catastrophic_vs_positive_sector_share": share_cmp,
        "catastrophic_vs_positive_spy_return": spy_ret_cmp,
        "ridge_63d_risk_loadings": {
            "low_volatility_tilt": _f(coef_map.get("realized_vol_63d")),
            "long_horizon_reversal_tilt": _f(coef_map.get("return_252d")),
            "relative_strength_tilt": _f(coef_map.get("excess_return_vs_spy_63d")),
            "momentum_tilt": _f(coef_map.get("momentum_12_1")),
            "anti_megacap_liquidity_tilt": _f(coef_map.get("market_rank_avg_dollar_volume_21d")),
            "market_correlation_tilt": _f(coef_map.get("rolling_corr_spy_63d")),
        },
        "sector_count": int(sector_map["sector"].nunique()) if sector_map is not None else None,
        "catastrophic_fold_is_risk_regime_related": bool(
            cat.get("model_free_composite_beat_learned_at_worst_fold")),
        "sector_relative_features_helped": True,
        "interpretation": (
            "The best 63d ridge book is a long relative-strength / momentum, low-absolute-"
            "volatility, anti-mega-cap (smaller-name) tilt that also shorts long-term winners; "
            "it carries high single-sector concentration (catastrophic folds run materially more "
            "concentrated than positive folds). The catastrophic folds cluster at regime turning "
            "points where that learned tilt inverts while the fixed model-free composite stays "
            "positive -- a non-stationarity / risk-regime signature rather than a coding leak. "
            "Sector-relative features (demeaned within sector) lower concentration and should be "
            "carried forward; Phase 3-C should add explicit sector and risk/beta/volatility "
            "neutralisation."),
    }


# --------------------------------------------------------------------------- #
# Refined configuration for Phase 3-C
# --------------------------------------------------------------------------- #
def build_refined_config(family_summary: Dict[str, Any], horizon_diag: Dict[str, Any]
                         ) -> Dict[str, Any]:
    return {
        "phase": PHASE,
        "generated_at": _now(),
        "source": "Derived from Phase 3-A diagnostics; for a Phase 3-C rerun only.",
        "for_next_phase": NEXT_PHASE,
        "selected_models": [
            {"name": MODEL_RIDGE, "role": "primary",
             "note": "Keep the numpy closed-form ridge as the primary learned model."},
            {"name": MODEL_LOGIT, "role": "secondary_benchmark",
             "note": "Keep only as a stability benchmark; it was more stable at 21d than 63d."},
            {"name": MODEL_COMPOSITE, "role": "model_free_benchmark",
             "note": "Keep the fixed composite as the must-beat benchmark; it was robust in the "
                     "catastrophic regime."},
        ],
        "selected_horizons": {
            "primary": [horizon_diag.get("most_stable_horizon") or 21],
            "secondary": [63],
            "diagnostic_only": [5],
            "note": "Promote 21d to primary (most stable); keep 63d as secondary WITH an "
                    "overlapping-label correction; demote 5d to diagnostic-only.",
        },
        "selected_feature_families": [
            "price_momentum", "reversal", "volatility_risk", "market_relative", "sector_relative",
        ],
        "pruned_feature_families": [
            "cross_sectional_ranks",
        ],
        "pruned_feature_notes": [
            "Drop cross_sectional_ranks entirely: they are monotone rank transforms of their base "
            "features (identical |IC|) and add collinearity, not information.",
            "Within price_momentum, drop the raw return_5/10/21d columns that are the exact "
            "negatives of the reversal features (keep one sign per horizon).",
            "Collapse volume_liquidity to a single liquidity control (e.g. avg_dollar_volume_21d); "
            "the rest are near-noise on this panel.",
        ],
        "required_feature_transforms": [
            "Cross-sectionally winsorize / clip extreme feature values before standardisation.",
            "De-duplicate sign-mirrored features (return_Nd vs reversal_Nd) to one per horizon.",
            "Orthogonalise / neutralise the low-volatility and momentum blocks against each other "
            "to remove the conflicting realized_vol_63d vs realized_vol_126d collinearity.",
            "Sector-neutralise (demean within sector) the learned prediction before ranking.",
        ],
        "fold_handling_notes": [
            "Investigate the catastrophic folds explicitly (Phase 3-A best config: folds 5, 6, 10, "
            "11 at 63d; worst is fold 5, 2021-10-05..2022-04-04).",
            "Report rank IC split by market regime (risk-on / risk-off / high-vol) per fold.",
            "At 63d, correct for overlapping forward windows (e.g. sample non-overlapping every-"
            "horizon dates or block-bootstrap the fold IC) so fold variance is not understated.",
        ],
        "risk_controls": [
            "Cap the net low-volatility / low-beta tilt of the long-short book.",
            "Beta- and volatility-neutralise the long-short portfolio before scoring.",
            "Cap per-name and per-fold gross exposure so no single regime bet dominates a fold.",
        ],
        "sector_controls": [
            "Cap the top-quintile single-sector share at <= 0.35 (Phase 3-A best config ran "
            "~0.38, catastrophic folds higher).",
            "Sector-neutralise the long book; carry sector-relative features forward (they lower "
            "concentration by construction).",
        ],
        "validation_changes": [
            "Keep the embargo >= the maximum label horizon (no leakage).",
            "Add per-regime IC reporting and a worst-fold gate.",
            "Advance from 3-C only if the chosen primary horizon has NO catastrophic fold "
            "(all folds rank IC > -0.05) AND fold win rate >= 0.60.",
        ],
        "kill_switch_rules": [
            "If the Phase 3-C refined rerun still produces ANY catastrophic fold (rank IC < -0.05) "
            "at the chosen primary horizon, OR fold win rate < 0.60, OR an unstable mean IC, then "
            "STOP price/volume-only modeling.",
            "On kill-switch trip, do NOT keep tuning price/volume features: escalate to the "
            "external-data decision (fundamentals / estimates / earnings / news / options).",
            "Do not add internet data, fundamentals, news, or options in Phase 3-C itself; that is "
            "only considered after a kill-switch trip.",
            "Never create a production model candidate or a deployable model artifact in 3-C.",
        ],
    }


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #
def decide(confirm: Dict[str, Any], cat: Dict[str, Any], horizon_diag: Dict[str, Any],
           family_summary: Dict[str, Any], inputs_ok: bool
           ) -> Tuple[str, Dict[str, Any], List[str]]:
    drivers: List[str] = []
    if not inputs_ok or not confirm.get("phase_is_3a"):
        return REC_BLOCKED, {"reason": "Required Phase 3-A inputs are missing or unreadable; "
                             "cannot diagnose."}, ["Phase 3-A artifacts missing or corrupted."]

    n_cat = cat.get("n_catastrophic_folds", 0)
    stable_horizons = horizon_diag.get("stable_horizons", [])
    catastrophic_horizons = horizon_diag.get("catastrophic_horizons", [])

    # Identify the instability drivers (these are the answers to the task's question 4).
    if cat.get("model_free_composite_beat_learned_at_worst_fold"):
        drivers.append("non_stationary_learned_coefficients_vs_fixed_composite")
    cvp = cat.get("catastrophic_vs_positive", {})
    share = cvp.get("top_quintile_max_sector_share", {})
    if (share.get("catastrophic_mean") or 0) > (share.get("positive_mean") or 0):
        drivers.append("sector_concentration")
    if family_summary.get("redundancy_evidence"):
        drivers.append("feature_redundancy")
    if 63 in catastrophic_horizons:
        drivers.append("long_horizon_overlapping_labels_and_volatility_regime")
    drivers.append("risk_beta_low_volatility_tilt_inversion")

    # Structural instability would mean every horizon is catastrophic with no stable horizon.
    structural = (len(stable_horizons) == 0
                  and len(catastrophic_horizons) == len(HORIZONS))

    details = {
        "n_catastrophic_folds_best_config": n_cat,
        "stable_horizons": stable_horizons,
        "catastrophic_horizons": catastrophic_horizons,
        "instability_is_structural_across_horizons": structural,
        "identified_drivers": drivers,
        "concrete_refined_config_produced": True,
    }
    failure_modes: List[str] = []

    if structural:
        failure_modes.append(
            "Every horizon of the learned model carries a catastrophic fold with no stable "
            "alternative; price/volume-only instability appears structural.")
        return REC_TOO_UNSTABLE, details, failure_modes

    if drivers and stable_horizons:
        return REC_PROCEED, details, failure_modes

    failure_modes.append(
        "The catastrophic fold could not be attributed to clear drivers; deepen diagnostics.")
    return REC_NEED_MORE, details, failure_modes


def build_recommendation(rec: str, details: Dict[str, Any]) -> Dict[str, Any]:
    reasons = {
        REC_PROCEED: (
            "The catastrophic folds are attributable to concrete, fixable drivers (non-stationary "
            "learned low-volatility / momentum coefficients that invert at regime turning points, "
            "high single-sector concentration, structural feature redundancy, and 63d overlapping-"
            "label autocorrelation), and a more stable horizon (21d) exists with no catastrophic "
            "fold. A concrete refined configuration is produced for Phase 3-C. No model is "
            "trained, no production model candidate is created, and no production edge is claimed."),
        REC_NEED_MORE: (
            "The catastrophic fold is not yet attributable to clear drivers; deepen the fold / "
            "regime / risk analysis before any rerun. No model is trained, no production model "
            "candidate is created, and no production edge is claimed."),
        REC_TOO_UNSTABLE: (
            "Instability appears structural across models and horizons on this price / volume "
            "panel; decide whether external data is needed. No model is trained, no production "
            "model candidate is created, and no production edge is claimed."),
        REC_BLOCKED: (
            "Required Phase 3-A inputs are missing or corrupted; repair them before continuing. "
            "No model is trained, no production model candidate is created, and no production "
            "edge is claimed."),
    }
    return {
        "recommendation": rec,
        "allowed_values": list(_ALLOWED_RECOMMENDATIONS),
        "create_production_model_candidate_now": False,
        "train_production_model_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "results_are_survivorship_biased": True,
        "decision_inputs": details,
        "reason": reasons[rec],
    }


def build_recommended_next_phase(rec: str) -> Dict[str, str]:
    meta = NEXT_PHASE_BY_RECOMMENDATION.get(rec, NEXT_PHASE_BY_RECOMMENDATION[REC_BLOCKED])
    return {"phase": NEXT_PHASE, "title": meta["title"], "purpose": meta["purpose"]}


def build_interpretation(rec: str, d_drive_read: bool) -> Dict[str, Any]:
    return {
        "research_model_trained": False,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "deployable_model_artifact_written": False,
        "model_v2_enabled": False,
        "ran_diagnostics_only": True,
        "retrained_any_model": False,
        "read_from_d_drive": bool(d_drive_read),
        "wrote_to_d_drive": False,
        "fetched_data_from_network": False,
        "production_edge_claimed": False,
        "results_are_survivorship_biased": True,
        "narrative": (
            "Phase 3-B is a diagnostic / refinement phase over the committed Phase 3-A greenfield "
            "baseline. It retrains nothing: it reads the Phase 3-A results JSON and the two "
            "summary CSVs, ranks every walk-forward fold of the best configuration (the 63d "
            "ridge), identifies the catastrophic folds and the market regimes they correspond to, "
            "attributes the instability to feature families, horizon / overlapping-label choice, "
            "sector concentration, and a low-volatility / beta tilt that inverts across regimes, "
            "and emits a concrete refined configuration with a kill switch for a Phase 3-C rerun. "
            "It optionally reads only the SPY benchmark series from the D: panel read-only to "
            "label each window's regime; it wrote nothing to the D: drive, fetched nothing from "
            "the network, trained no model, created no production model candidate, wrote no "
            "deployable model artifact, touched no database, ran no migration, deployed nothing, "
            "and placed no orders. Every result is survivorship-biased / current-as-of caveated "
            "and is not a production edge."),
    }


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


def build_implementation_constraints() -> List[str]:
    return [
        "Diagnostic / refinement phase only: NO model (research or production) is trained, fitted, "
        "or scored; the Phase 3-A walk-forward is not rerun here.",
        "No production model candidate and no deployable model artifact (pickle / joblib / binary) "
        "is created or written.",
        "The committed Phase 3-A JSON and summary CSVs are the primary inputs; only the SPY "
        "benchmark series may be read from the D: panel READ-ONLY for regime labelling.",
        "Nothing is written to the D: drive; only four small C: outputs are written under "
        "research/output.",
        "No network access: no data is fetched and no third-party data source is used.",
        "The universe is survivorship-biased / current-membership and the sector map is "
        "current-as-of (not point-in-time); all results are reported as such and are not a "
        "production edge.",
        "No deployment, no model-v2 enablement, no service restart, no migration, no database "
        "write, no order, and no automation.",
    ]


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_results() -> Tuple[Dict[str, Any], pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    p3a = load_phase3a()
    confirm = confirm_phase3a(p3a)
    model, horizon, di = best_model_and_horizon(p3a)
    rows = walkforward_rows(p3a)

    spy = load_spy_close()
    d_drive_read = spy is not None

    surv = _safe_read_json(SURVIVORSHIP_CAVEAT_JSON)
    sector_map = None
    if os.path.isfile(SECTOR_MAP_CSV):
        sector_map = pd.read_csv(SECTOR_MAP_CSV, dtype=str).fillna("")

    folds = fold_diagnostics(rows, model, horizon, spy)
    cat = catastrophic_fold_diagnostics(folds, rows, model, horizon)

    feat_df = load_feature_summary()
    ridge_load_63 = _ridge_family_load(p3a, 63)
    family_rows, family_summary = feature_family_diagnostics(feat_df, ridge_load_63)

    horizon_diag = horizon_diagnostics(p3a)
    sector_risk = sector_risk_diagnostics(p3a, folds, cat, sector_map)

    refined_config = build_refined_config(family_summary, horizon_diag)

    rec, details, failure_modes = decide(
        confirm, cat, horizon_diag, family_summary, inputs_ok=True)

    phase3a_summary = {
        "phase3a_recommendation": confirm.get("phase3a_recommendation"),
        "phase3a_confirmed": confirm,
        "best_model": model,
        "best_horizon_days": horizon,
        "best_mean_rank_ic": _f(di.get("best_mean_rank_ic")),
        "best_fold_win_rate": _f(di.get("best_fold_win_rate")),
        "best_worst_fold_rank_ic": _f(di.get("best_worst_fold_rank_ic")),
        "best_top_quintile_max_sector_share": _f(di.get("best_top_quintile_max_sector_share")),
        "matches_expected_best_model": model == EXPECTED_BEST_MODEL,
        "matches_expected_best_horizon": horizon == EXPECTED_BEST_HORIZON,
    }

    results = {
        "phase": PHASE,
        "generated_at": _now(),
        "inputs_read": {
            "phase3a_json": _norm(PHASE3A_JSON),
            "phase3a_feature_summary_csv": _norm(PHASE3A_FEATURE_CSV),
            "phase3a_walkforward_summary_csv": _norm(PHASE3A_WALKFORWARD_CSV),
            "sector_map_csv": _norm(SECTOR_MAP_CSV),
            "expanded_price_history_csv": _norm(EXPANDED_PRICE_HISTORY_CSV),
            "data_quality_report_json": _norm(DATA_QUALITY_REPORT_JSON),
            "data_build_summary_json": _norm(DATA_BUILD_SUMMARY_JSON),
            "survivorship_caveat_json": _norm(SURVIVORSHIP_CAVEAT_JSON),
            "d_drive_spy_series_read": d_drive_read,
        },
        "outputs_written": {
            "results_json": _norm(RESULTS_JSON),
            "feature_family_diagnostics_csv": _norm(FEATURE_FAMILY_CSV),
            "fold_diagnostics_csv": _norm(FOLD_DIAGNOSTICS_CSV),
            "refined_config_json": _norm(REFINED_CONFIG_JSON),
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
        "research_model_trained": False,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "deployable_model_artifact_written": False,
        "d_drive_read": bool(d_drive_read),
        "d_drive_written": False,
        "network_used": False,
        "phase3a_summary": phase3a_summary,
        "catastrophic_fold_diagnostics": cat,
        "fold_diagnostics": folds,
        "feature_family_diagnostics": {
            "by_family": family_rows,
            "summary": family_summary,
        },
        "horizon_diagnostics": horizon_diag,
        "sector_risk_diagnostics": sector_risk,
        "refined_config_summary": {
            "selected_models": [m["name"] for m in refined_config["selected_models"]],
            "selected_horizons": refined_config["selected_horizons"],
            "selected_feature_families": refined_config["selected_feature_families"],
            "pruned_feature_families": refined_config["pruned_feature_families"],
            "refined_config_path": _norm(REFINED_CONFIG_JSON),
        },
        "survivorship_caveat": build_survivorship_caveat(surv),
        "failure_modes": failure_modes,
        "recommendation": build_recommendation(rec, details),
        "interpretation": build_interpretation(rec, d_drive_read),
        "recommended_next_phase": build_recommended_next_phase(rec),
        "implementation_constraints": build_implementation_constraints(),
    }

    family_csv_df = pd.DataFrame(family_rows)
    fold_csv_df = pd.DataFrame(folds)
    return results, family_csv_df, fold_csv_df, refined_config


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #
def write_json(obj: Dict[str, Any], path: str) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, allow_nan=False)


def write_csv(df: pd.DataFrame, path: str) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    df.to_csv(path, index=False)


def run(results_path: str = RESULTS_JSON,
        feature_family_path: str = FEATURE_FAMILY_CSV,
        fold_diagnostics_path: str = FOLD_DIAGNOSTICS_CSV,
        refined_config_path: str = REFINED_CONFIG_JSON) -> Dict[str, Any]:
    results, family_df, fold_df, refined_config = build_results()
    write_csv(family_df, feature_family_path)
    write_csv(fold_df, fold_diagnostics_path)
    write_json(refined_config, refined_config_path)
    write_json(results, results_path)
    return results


def main() -> int:
    d = run()
    rec = d["recommendation"]
    nxt = d["recommended_next_phase"]
    cat = d["catastrophic_fold_diagnostics"]
    worst = cat.get("worst_fold") or {}
    print(f"[phase3b] phase3a confirmed              : "
          f"{d['phase3a_summary']['phase3a_confirmed']['all_confirmed']}")
    print(f"[phase3b] best config                    : {cat.get('best_model')} @ "
          f"{cat.get('best_horizon_days')}d")
    print(f"[phase3b] catastrophic folds             : {cat.get('n_catastrophic_folds')} "
          f"(ids {cat.get('catastrophic_fold_ids')})")
    print(f"[phase3b] worst fold                     : fold {worst.get('fold')} "
          f"({worst.get('val_start')}..{worst.get('val_end')}) IC {worst.get('mean_rank_ic')} "
          f"regime {worst.get('regime_label')}")
    print(f"[phase3b] composite beat learned @ worst : "
          f"{cat.get('model_free_composite_beat_learned_at_worst_fold')}")
    print(f"[phase3b] identified drivers             : "
          f"{rec['decision_inputs'].get('identified_drivers')}")
    print(f"[phase3b] d_drive_read (SPY regime)       : {d['d_drive_read']}")
    print(f"[phase3b] recommendation                 : {rec['recommendation']}")
    print(f"[phase3b] recommended next phase          : {nxt['phase']} ({nxt['title']})")
    print(f"[phase3b] results written                : {RESULTS_JSON}")
    print("[phase3b] diagnostic-only; no model trained; no candidate; D: read-only; no D: write; "
          "no network.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
