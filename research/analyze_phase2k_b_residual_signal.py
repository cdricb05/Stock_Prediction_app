"""Phase 2K-B Beta/Vol-Neutralized Residual Signal Test (offline, read-only).

The cheapest next alpha test from the Phase 2K-A backlog, run on the existing
Phase 2G real-data export only. Phase 2I-A / 2J-A found the only measurable edge
was a 63-session volatility / beta / correlation tilt -- a regime-dependent risk
premium, not market-neutral alpha. This phase asks a sharper question: once that
risk premium is explicitly regressed out, does any candidate feature still rank a
*residual* forward return?

Method, per forward horizon (5 / 10 / 21 / 63 sessions):
  1. Build the forward excess-vs-SPY return label from local price history.
  2. Form a residual label by neutralizing that excess return, cross-sectionally
     and per date, against trailing risk controls (rolling beta, realized vol,
     downside vol, SPY correlation) via same-date OLS. When controls are missing
     / constant / too few names, fall back to a cross-sectional demean. Controls
     are trailing point-in-time features; the label is strictly forward; the fit
     uses no forward information, so there is no look-ahead.
  3. Re-run the Phase 2I-A rank-IC machinery (per-date Spearman, information
     ratio, yearly / quarterly stability, top-minus-bottom spread, top-decile hit
     rate, sign stability, low-confidence guard) against the residual label.
  4. Compare residual-label IC to the Phase 2I-A raw-excess IC, flag whether
     neutralization removed the 63d risk-premium signal, and recommend per
     feature: KEEP_FOR_ROBUSTNESS_TEST / DROP / NEED_MORE_DATA / CONTROL_ONLY.

This phase is research only. It reads the Phase 2G-C artifacts plus the upstream
2I-A / 2J-A / 2K-A JSON summaries and writes exactly one diagnostics JSON. It
performs no infrastructure actions and mutates no datastore; the machine-readable
safety flags are emitted in the diagnostics JSON, and the full guardrail rationale
lives in docs/phase2k_b_residual_signal_test_v1.md, not in this source. No model
candidate is created here.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import time
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

PHASE = "2K-B"

_OUTPUT_DIR = os.path.join("research", "output")

# Inputs (read-only).
SCORED_CSV = os.path.join(_OUTPUT_DIR, "phase2g_real_data_scored.csv")
PRICE_HISTORY_CSV = os.path.join(_OUTPUT_DIR, "phase2g_price_history_real.csv")
RUN_SUMMARY_JSON = os.path.join(_OUTPUT_DIR, "phase2g_c_real_data_run_summary.json")
INPUT_2IA_JSON = os.path.join(_OUTPUT_DIR, "phase2i_feature_ic_horizon_sweep.json")
INPUT_2J_JSON = os.path.join(_OUTPUT_DIR, "phase2j_research_decision.json")
INPUT_2KA_JSON = os.path.join(_OUTPUT_DIR, "phase2k_alpha_backlog.json")

# The single output this analyzer is allowed to write.
RESIDUAL_JSON = os.path.join(_OUTPUT_DIR, "phase2k_b_residual_signal.json")

# Candidate alpha features tested against the residual label (all in scored CSV).
CANDIDATE_FEATURES: List[str] = [
    "return_5d",
    "return_10d",
    "return_21d",
    "return_63d",
    "momentum_12_1",
    "excess_return_vs_spy_21d",
    "excess_return_vs_spy_63d",
    "volume_zscore_21d",
    "avg_dollar_volume_21d",
]

# Trailing risk controls regressed out to build the residual label. These are NOT
# alpha candidates: tested against the residual they should collapse toward zero
# (we removed them), so they are reported CONTROL_ONLY.
CONTROL_FEATURES: List[str] = [
    "rolling_beta_63d",
    "realized_vol_21d",
    "realized_vol_63d",
    "downside_vol_21d",
    "rolling_corr_spy_63d",
]

ALL_FEATURES: List[str] = CANDIDATE_FEATURES + CONTROL_FEATURES

# Forward horizons (sessions). All four residual labels are built and swept.
HORIZONS: List[int] = [5, 10, 21, 63]

# Decision thresholds (identical to Phase 2I-A so results are comparable).
RANK_IC_FLOOR = 0.03        # |mean per-date rank IC| KEEP floor (at best horizon)
YEAR_SIGN_MIN = 3           # IC sign must match the overall sign in >= this many years
MIN_DATES = 120             # per (feature, horizon) low-confidence guard
MIN_NAMES_PER_DATE = 5      # min cross-section to compute a date's rank IC / quintiles
MIN_NAMES_DECILE = 10       # min cross-section to compute a date's decile / spread
RESID_MIN_NAMES = 10        # min usable names to fit a per-date control regression

DATE_COL = "as_of_date"
TICKER_COL = "ticker"
SPY = "SPY"

# Recommendation vocabulary (the only values keep_drop may emit).
REC_KEEP = "KEEP_FOR_ROBUSTNESS_TEST"
REC_DROP = "DROP"
REC_NEED_MORE = "NEED_MORE_DATA"
REC_CONTROL = "CONTROL_ONLY"


# --------------------------------------------------------------------------- #
# Loading (read-only)
# --------------------------------------------------------------------------- #
def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_inputs():
    scored = pd.read_csv(SCORED_CSV, parse_dates=[DATE_COL])
    prices = pd.read_csv(PRICE_HISTORY_CSV, parse_dates=["date"])
    run_summary = _read_json(RUN_SUMMARY_JSON)
    phase2i_a = _read_json(INPUT_2IA_JSON)
    phase2j = _read_json(INPUT_2J_JSON)
    return scored, prices, run_summary, phase2i_a, phase2j


def build_forward_labels(prices: "pd.DataFrame") -> "pd.DataFrame":
    """Per (ticker, date) forward h-session excess-vs-SPY return for every horizon.

    Forward return is adj_close shifted -h within each ticker; rows whose forward
    window runs past the data end become NaN and drop out downstream. SPY's
    forward return over the same horizon is subtracted to form the excess label.
    The 5-session price-derived excess was confirmed identical to the scored
    label in Phase 2I-A, so all four horizons are derived uniformly here.
    """
    px = prices.sort_values([TICKER_COL, "date"]).copy()
    for h in HORIZONS:
        px[f"_fwd_{h}"] = (
            px.groupby(TICKER_COL)["adj_close"].shift(-h) / px["adj_close"] - 1.0)

    spy_cols = {f"_fwd_{h}": f"_spy_fwd_{h}" for h in HORIZONS}
    spy = (px[px[TICKER_COL] == SPY][["date"] + list(spy_cols)]
           .rename(columns=spy_cols))

    names = px[px[TICKER_COL] != SPY].merge(spy, on="date", how="left")
    for h in HORIZONS:
        names[f"excess_{h}d"] = names[f"_fwd_{h}"] - names[f"_spy_fwd_{h}"]

    keep = [TICKER_COL, "date"] + [f"excess_{h}d" for h in HORIZONS]
    return names[keep]


def build_panel(scored: "pd.DataFrame", labels: "pd.DataFrame") -> "pd.DataFrame":
    """Point-in-time features + controls joined to multi-horizon forward labels."""
    cols = [TICKER_COL, DATE_COL] + ALL_FEATURES
    feat = scored[cols].copy()
    panel = feat.merge(labels, left_on=[TICKER_COL, DATE_COL],
                       right_on=[TICKER_COL, "date"], how="inner")
    return panel


# --------------------------------------------------------------------------- #
# Residualization (per-date cross-sectional OLS; no look-ahead)
# --------------------------------------------------------------------------- #
def _ols_residual(y: "np.ndarray", xc: "np.ndarray") -> "np.ndarray":
    """Residual of y on controls xc with an intercept (least squares, no scipy).

    np.linalg.lstsq returns the minimum-norm solution even when the control
    matrix is rank-deficient, so the residual is always well defined.
    """
    design = np.column_stack([np.ones(len(y)), xc])
    beta, _res, _rank, _sv = np.linalg.lstsq(design, y, rcond=None)
    return y - design @ beta


def residualize_label(panel: "pd.DataFrame", lcol: str,
                      controls: List[str]) -> Dict[str, Any]:
    """Cross-sectional residual of a forward excess label on trailing controls.

    For each as_of_date the forward excess label is regressed on the usable
    controls (finite, non-constant) plus an intercept; the residual is the
    market/risk-neutralized label. When a date has no usable control or too few
    names, the robust fallback is a cross-sectional demean (subtract the per-date
    mean of the label). Returns the residual Series plus per-date fit counts.
    """
    resid = pd.Series(np.nan, index=panel.index, dtype=float)
    ols_dates = 0
    fallback_dates = 0
    for _, block in panel.groupby(DATE_COL, sort=False):
        yfin = block[lcol].notna()
        if int(yfin.sum()) < MIN_NAMES_PER_DATE:
            continue
        usable = [c for c in controls
                  if int(block.loc[yfin, c].notna().sum()) >= RESID_MIN_NAMES
                  and block.loc[yfin, c].dropna().nunique() > 1]
        if usable:
            rows = yfin & block[usable].notna().all(axis=1)
            if int(rows.sum()) >= RESID_MIN_NAMES:
                y = block.loc[rows, lcol].to_numpy(dtype=float)
                xc = block.loc[rows, usable].to_numpy(dtype=float)
                resid.loc[block.index[rows.to_numpy()]] = _ols_residual(y, xc)
                ols_dates += 1
                continue
        # Robust fallback: cross-sectional demean of the finite-label rows.
        yv = block.loc[yfin, lcol]
        resid.loc[yv.index] = (yv - yv.mean()).to_numpy()
        fallback_dates += 1
    return {"resid": resid, "ols_dates": ols_dates,
            "fallback_dates": fallback_dates}


# --------------------------------------------------------------------------- #
# Metric helpers (pure; ported from Phase 2I-A for comparability)
# --------------------------------------------------------------------------- #
def _round(x: Optional[float], n: int = 6) -> Optional[float]:
    if x is None:
        return None
    try:
        if pd.isna(x) or (isinstance(x, float) and math.isinf(x)):
            return None
    except (TypeError, ValueError):
        pass
    return round(float(x), n)


def _sign(x: Optional[float]) -> int:
    if x is None or pd.isna(x) or x == 0:
        return 0
    return 1 if x > 0 else -1


def _cell_frame(panel: "pd.DataFrame", fcol: str, lcol: str) -> "pd.DataFrame":
    return panel[[DATE_COL, fcol, lcol]].dropna().copy()


def _per_date_rank_ic(sub: "pd.DataFrame", fcol: str, lcol: str) -> "pd.Series":
    """Cross-sectional Spearman(feature, residual label) per as_of_date.

    Spearman is Pearson on within-date ranks (no scipy), computed for every date
    in one vectorized pass. Dates with fewer than MIN_NAMES_PER_DATE names, or
    with zero rank variance, yield no IC.
    """
    if sub.empty:
        return pd.Series(dtype=float)
    size = sub.groupby(DATE_COL)[fcol].transform("size")
    s = sub[size >= MIN_NAMES_PER_DATE]
    if s.empty:
        return pd.Series(dtype=float)
    gs = s.groupby(DATE_COL)
    tmp = pd.DataFrame({"d": s[DATE_COL].to_numpy()})
    tmp["fr"] = gs[fcol].rank().to_numpy()
    tmp["lr"] = gs[lcol].rank().to_numpy()
    tmp["frlr"] = tmp["fr"] * tmp["lr"]
    tmp["frfr"] = tmp["fr"] * tmp["fr"]
    tmp["lrlr"] = tmp["lr"] * tmp["lr"]
    agg = tmp.groupby("d").agg(
        n=("fr", "size"), sf=("fr", "sum"), sl=("lr", "sum"),
        sfl=("frlr", "sum"), sff=("frfr", "sum"), sll=("lrlr", "sum"))
    cov = agg["sfl"] - agg["sf"] * agg["sl"] / agg["n"]
    var_f = agg["sff"] - agg["sf"] ** 2 / agg["n"]
    var_l = agg["sll"] - agg["sl"] ** 2 / agg["n"]
    denom = (var_f * var_l) ** 0.5
    ics = (cov / denom).where(denom > 0).dropna()
    ics.index = pd.to_datetime(ics.index)
    return ics


def _calendar_ic(ics: "pd.Series", how: str) -> List[Dict[str, Any]]:
    if not len(ics):
        return []
    if how == "year":
        keys = ics.index.year.astype(str)
    else:
        keys = (ics.index.year.astype(str) + "-Q"
                + ics.index.quarter.astype(str))
    out: List[Dict[str, Any]] = []
    for k, grp in ics.groupby(keys):
        out.append({"period": str(k), "rank_ic": _round(grp.mean()),
                    "n_dates": int(len(grp))})
    out.sort(key=lambda r: r["period"])
    return out


def _quintile_monotonic(sub: "pd.DataFrame", fcol: str, lcol: str
                        ) -> Optional[bool]:
    if len(sub) < 5 * MIN_NAMES_PER_DATE or sub[fcol].nunique() < 5:
        return None
    q = pd.qcut(sub[fcol].rank(method="first"), 5, labels=False)
    means = sub.groupby(q)[lcol].mean().sort_index()
    return bool(means.is_monotonic_increasing or means.is_monotonic_decreasing)


def _spread_and_hit_rate(sub: "pd.DataFrame", fcol: str, lcol: str):
    """Top-minus-bottom quintile spread and top-decile hit rate on the residual
    label, both per-date statistics averaged across dates (vectorized)."""
    if sub.empty:
        return None, None
    g = sub.groupby(DATE_COL)[fcol]
    size = g.transform("size")
    nuniq = g.transform("nunique")

    elig = sub[(size >= MIN_NAMES_DECILE) & (nuniq >= 5)]
    spread = None
    if not elig.empty:
        ge = elig.groupby(DATE_COL)[fcol]
        rank_first = ge.rank(method="first")
        ne = ge.transform("size")
        bucket = ((rank_first > 1 + 0.2 * (ne - 1)).astype(int)
                  + (rank_first > 1 + 0.4 * (ne - 1)).astype(int)
                  + (rank_first > 1 + 0.6 * (ne - 1)).astype(int)
                  + (rank_first > 1 + 0.8 * (ne - 1)).astype(int))
        elig = elig.assign(_bucket=bucket)
        top_q = elig[elig["_bucket"] == 4].groupby(DATE_COL)[lcol].mean()
        bot_q = elig[elig["_bucket"] == 0].groupby(DATE_COL)[lcol].mean()
        per_date = (top_q - bot_q).dropna()
        spread = float(per_date.mean()) if len(per_date) else None

    dec = sub[size >= MIN_NAMES_DECILE]
    hit_rate = None
    if not dec.empty:
        cutoffs = dec.groupby(DATE_COL)[fcol].quantile(0.9)
        cut = dec[DATE_COL].map(cutoffs)
        top = dec[dec[fcol] >= cut]
        if not top.empty:
            pos = (top[lcol] > 0).astype(float)
            rate_by_date = pos.groupby(top[DATE_COL]).mean()
            hit_rate = float(rate_by_date.mean()) if len(rate_by_date) else None

    return spread, hit_rate


# --------------------------------------------------------------------------- #
# Per (feature, horizon) cell + per-feature roll-up (vs residual label)
# --------------------------------------------------------------------------- #
def horizon_cell(panel: "pd.DataFrame", fcol: str, h: int) -> Dict[str, Any]:
    lcol = f"resid_{h}d"
    sub = _cell_frame(panel, fcol, lcol)
    n_rows = int(len(sub))

    ics = _per_date_rank_ic(sub, fcol, lcol)
    n_dates = int(len(ics))
    mean_ic = float(ics.mean()) if n_dates else None
    std_ic = float(ics.std(ddof=1)) if n_dates > 1 else None
    info_ratio = (mean_ic / std_ic
                  if (mean_ic is not None and std_ic not in (None, 0.0)
                      and not pd.isna(std_ic))
                  else None)

    yearly = _calendar_ic(ics, "year")
    quarterly = _calendar_ic(ics, "quarter")

    overall_sign = _sign(mean_ic)
    years_present = len(yearly)
    years_matching = sum(1 for y in yearly
                         if _sign(y["rank_ic"]) == overall_sign and overall_sign)

    spread, hit_rate = _spread_and_hit_rate(sub, fcol, lcol)
    monotonic = _quintile_monotonic(sub, fcol, lcol)
    low_confidence = n_dates < MIN_DATES

    return {
        "horizon_days": h,
        "mean_rank_ic": _round(mean_ic),
        "std_rank_ic": _round(std_ic),
        "information_ratio": _round(info_ratio),
        "n_dates": n_dates,
        "n_rows": n_rows,
        "low_confidence": bool(low_confidence),
        "direction": ("bullish" if overall_sign > 0
                      else "bearish" if overall_sign < 0 else "flat"),
        "top_minus_bottom_spread": _round(spread),
        "quintile_monotonic": monotonic,
        "top_decile_residual_hit_rate": _round(hit_rate),
        "years_present": years_present,
        "years_matching_sign": years_matching,
        "year_sign_consistent": bool(years_matching >= YEAR_SIGN_MIN),
        "yearly_rank_ic": yearly,
        "quarterly_rank_ic": quarterly,
    }


def _best_cell(cells: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    def _abs_ic(c: Dict[str, Any]) -> float:
        v = c["mean_rank_ic"]
        return abs(v) if v is not None else -1.0

    confident = [c for c in cells.values()
                 if not c["low_confidence"] and c["mean_rank_ic"] is not None]
    pool = confident or [c for c in cells.values() if c["mean_rank_ic"] is not None]
    return max(pool, key=_abs_ic) if pool else None


def _recommendation(feature: str, best: Optional[Dict[str, Any]],
                    keep_criteria: Dict[str, bool]) -> str:
    if feature in CONTROL_FEATURES:
        return REC_CONTROL
    if best is None:
        return REC_NEED_MORE
    if (keep_criteria["magnitude_pass"] and keep_criteria["stability_pass"]
            and keep_criteria["sample_pass"]):
        return REC_KEEP
    if not keep_criteria["sample_pass"]:
        return REC_NEED_MORE
    return REC_DROP


def feature_block(panel: "pd.DataFrame", fcol: str) -> Dict[str, Any]:
    cells = {str(h): horizon_cell(panel, fcol, h) for h in HORIZONS}
    best = _best_cell(cells)

    magnitude_pass = bool(best and abs(best["mean_rank_ic"]) >= RANK_IC_FLOOR)
    stability_pass = bool(best and best["year_sign_consistent"])
    sample_pass = bool(best and not best["low_confidence"])
    keep_criteria = {
        "magnitude_pass": magnitude_pass,
        "stability_pass": stability_pass,
        "sample_pass": sample_pass,
        "rank_ic_floor": RANK_IC_FLOOR,
        "year_sign_min": YEAR_SIGN_MIN,
    }
    role = "control" if fcol in CONTROL_FEATURES else "candidate"
    recommendation = _recommendation(fcol, best, keep_criteria)

    return {
        "role": role,
        "recommendation": recommendation,
        "best_horizon_days": best["horizon_days"] if best else None,
        "best_mean_rank_ic": best["mean_rank_ic"] if best else None,
        "best_information_ratio": best["information_ratio"] if best else None,
        "direction": best["direction"] if best else "flat",
        "keep_for_robustness_test": bool(recommendation == REC_KEEP),
        "keep_criteria": keep_criteria,
        "horizons": cells,
    }


# --------------------------------------------------------------------------- #
# Comparison to Phase 2I-A (raw forward-excess IC)
# --------------------------------------------------------------------------- #
def _abs_or_none(x: Optional[float]) -> Optional[float]:
    return abs(x) if isinstance(x, (int, float)) else None


def build_comparison(features: Dict[str, Any],
                     phase2i_a: Dict[str, Any]) -> Dict[str, Any]:
    """Per feature: raw best |IC| (Phase 2I-A, vs forward excess) against the
    residual best |IC| measured here, and whether neutralization shrank it."""
    raw_features = phase2i_a.get("features", {})
    by_feature: Dict[str, Any] = {}
    for f in ALL_FEATURES:
        raw = raw_features.get(f, {})
        raw_ic = raw.get("best_mean_rank_ic")
        res_ic = features[f]["best_mean_rank_ic"]
        raw_abs = _abs_or_none(raw_ic)
        res_abs = _abs_or_none(res_ic)
        shrunk = (raw_abs is not None and res_abs is not None
                  and res_abs < raw_abs)
        by_feature[f] = {
            "role": features[f]["role"],
            "raw_best_horizon_days": raw.get("best_horizon_days"),
            "raw_best_mean_rank_ic": raw_ic,
            "residual_best_horizon_days": features[f]["best_horizon_days"],
            "residual_best_mean_rank_ic": res_ic,
            "abs_ic_delta_residual_minus_raw": _round(
                (res_abs - raw_abs) if (raw_abs is not None
                                        and res_abs is not None) else None),
            "ic_shrunk_toward_zero": bool(shrunk),
        }

    # Did neutralization remove the 63d risk-premium signal? The Phase 2I-A KEEP
    # survivors were exactly the vol / beta / correlation controls; the premium is
    # judged removed when, on the residual label, no control still clears the IC
    # floor (i.e. each control's residual best |IC| < RANK_IC_FLOOR).
    controls_collapsed = {
        c: {
            "raw_best_abs_ic": _round(_abs_or_none(
                raw_features.get(c, {}).get("best_mean_rank_ic"))),
            "residual_best_abs_ic": _round(_abs_or_none(
                features[c]["best_mean_rank_ic"])),
            "below_floor_on_residual": bool(
                (_abs_or_none(features[c]["best_mean_rank_ic"]) or 0.0)
                < RANK_IC_FLOOR),
        }
        for c in CONTROL_FEATURES
    }
    removed = all(v["below_floor_on_residual"] for v in controls_collapsed.values())

    surviving = [f for f in CANDIDATE_FEATURES
                 if features[f]["recommendation"] == REC_KEEP]

    return {
        "raw_label": "phase2i_a.forward_excess_return_vs_spy",
        "residual_label": "phase2k_b.beta_vol_neutralized_residual",
        "by_feature": by_feature,
        "control_features_ic_collapse": controls_collapsed,
        "neutralization_removed_63d_risk_premium": bool(removed),
        "newly_surviving_residual_candidates": surviving,
    }


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_diagnostics(scored: "pd.DataFrame", prices: "pd.DataFrame",
                      run_summary: Dict[str, Any], phase2i_a: Dict[str, Any],
                      phase2j: Dict[str, Any]) -> Dict[str, Any]:
    labels = build_forward_labels(prices)
    panel = build_panel(scored, labels)

    # Build the per-date residual label for each horizon and attach it.
    fit_counts: Dict[str, Any] = {}
    for h in HORIZONS:
        res = residualize_label(panel, f"excess_{h}d", CONTROL_FEATURES)
        panel[f"resid_{h}d"] = res["resid"]
        fit_counts[str(h)] = {"ols_dates": res["ols_dates"],
                              "fallback_dates": res["fallback_dates"]}

    features = {f: feature_block(panel, f) for f in ALL_FEATURES}
    comparison = build_comparison(features, phase2i_a)

    # keep_drop roll-up (the only place recommendations are summarized).
    by_feature_rec = {f: features[f]["recommendation"] for f in ALL_FEATURES}
    buckets = {REC_KEEP: [], REC_DROP: [], REC_NEED_MORE: [], REC_CONTROL: []}
    for f, rec in by_feature_rec.items():
        buckets[rec].append(f)
    keep_drop = {
        "by_feature": by_feature_rec,
        "keep_for_robustness_test": sorted(buckets[REC_KEEP]),
        "drop": sorted(buckets[REC_DROP]),
        "need_more_data": sorted(buckets[REC_NEED_MORE]),
        "control_only": sorted(buckets[REC_CONTROL]),
        "n_keep_for_robustness_test": len(buckets[REC_KEEP]),
        "n_drop": len(buckets[REC_DROP]),
        "n_need_more_data": len(buckets[REC_NEED_MORE]),
        "n_control_only": len(buckets[REC_CONTROL]),
        "allowed_values": [REC_KEEP, REC_DROP, REC_NEED_MORE, REC_CONTROL],
    }

    survivors = keep_drop["keep_for_robustness_test"]
    any_survives = bool(survivors)
    removed = comparison["neutralization_removed_63d_risk_premium"]

    if any_survives:
        narrative = (
            "Beta/vol neutralization "
            + ("removed" if removed else "did not fully remove")
            + " the Phase 2I-A 63d volatility/beta/correlation tilt, and at least "
            "one candidate feature still ranks the residual forward return above "
            "the IC floor with stable yearly sign: "
            + ", ".join(survivors)
            + ". These residual candidates are not yet validated -- they must pass "
            "the Phase 2I-B robustness battery before any model candidate. No "
            "production edge is claimed.")
    else:
        narrative = (
            "Beta/vol neutralization "
            + ("removed" if removed else "did not fully remove")
            + " the Phase 2I-A 63d volatility/beta/correlation tilt, and no "
            "candidate feature clears the IC floor with stable sign against the "
            "residual label. There is no residual alpha to validate on the current "
            "~40-name, single-regime 2023-2026 export; the disciplined next step is "
            "data acquisition (longer history / broader universe / point-in-time "
            "fundamentals), not model training. No production edge is claimed.")

    interpretation = {
        "beta_vol_neutralization_eliminates_63d_signal": bool(removed),
        "any_residual_alpha_candidate_survives": any_survives,
        "surviving_candidates": survivors,
        "narrative": narrative,
        "production_edge_claimed": False,
    }

    if any_survives:
        recommended_next_phase = {
            "phase": "2K-C",
            "title": "Residual Alpha Robustness Validation",
            "purpose": "Run the Phase 2I-B overlap-aware bootstrap, permutation "
                       "null, and leave-one-year-out / leave-one-ticker-out battery "
                       "on the surviving residual candidate(s). No model candidate "
                       "is created until they pass.",
        }
    else:
        recommended_next_phase = {
            "phase": "2K-D",
            "title": "Alpha Data Acquisition for New Hypothesis Families",
            "purpose": "No residual candidate survived on the current data. Acquire "
                       "longer price history, a broader point-in-time universe, and "
                       "point-in-time fundamentals/estimates to test the remaining "
                       "Phase 2K-A backlog families. This is data acquisition, not "
                       "model training; no model candidate is created.",
        }

    panel_dates = pd.to_datetime(panel[DATE_COL])
    return {
        "phase": PHASE,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "scored_csv": SCORED_CSV,
            "price_history_csv": PRICE_HISTORY_CSV,
            "run_summary_json": RUN_SUMMARY_JSON,
            "phase2i_a_json": INPUT_2IA_JSON,
            "phase2j_json": INPUT_2J_JSON,
            "phase2k_a_json": INPUT_2KA_JSON,
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
        "provenance": {
            "source": run_summary.get("source"),
            "date_start": run_summary.get("date_start"),
            "date_end": run_summary.get("date_end"),
            "price_row_count": run_summary.get("row_count"),
            "ticker_count": run_summary.get("ticker_count"),
            "phase2i_a_phase": phase2i_a.get("phase"),
            "phase2j_phase": phase2j.get("phase"),
        },
        "upstream_decision_summary": {
            "phase2j_go_no_go": phase2j.get("decision", {}).get("go_no_go"),
            "phase2j_dominant_edge_character": phase2j.get(
                "evidence_summary", {}).get("phase2i_b_dominant_edge_character"),
        },
        "residualization_config": {
            "label_base": "forward_excess_return_vs_spy",
            "horizons": HORIZONS,
            "controls": CONTROL_FEATURES,
            "method": "per-date cross-sectional OLS (usable controls + intercept); "
                      "residual = forward excess label - fitted",
            "robust_fallback": "cross-sectional demean (subtract the per-date mean "
                               "of the label) when no control is usable, a control "
                               "is constant, or the date has fewer than "
                               "min_names_for_ols usable names",
            "min_names_for_ols": RESID_MIN_NAMES,
            "no_lookahead": "controls are trailing point-in-time features from the "
                            "scored CSV; the label is strictly forward; the per-date "
                            "fit uses no forward information",
            "residual_label_columns": {str(h): f"resid_{h}d" for h in HORIZONS},
            "fit_counts": fit_counts,
        },
        "panel": {
            "n_rows": int(len(panel)),
            "n_tickers": int(panel[TICKER_COL].nunique()),
            "n_dates": int(panel[DATE_COL].nunique()),
            "date_start": panel_dates.min().date().isoformat() if len(panel) else None,
            "date_end": panel_dates.max().date().isoformat() if len(panel) else None,
        },
        "candidate_features": CANDIDATE_FEATURES,
        "control_features": CONTROL_FEATURES,
        "features": features,
        "comparison_to_phase2i_a": comparison,
        "keep_drop": keep_drop,
        "interpretation": interpretation,
        "recommended_next_phase": recommended_next_phase,
    }


def write_diagnostics(diagnostics: Dict[str, Any],
                      path: str = RESIDUAL_JSON) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(diagnostics, f, indent=2, allow_nan=False)


def run(output_path: str = RESIDUAL_JSON) -> Dict[str, Any]:
    scored, prices, run_summary, phase2i_a, phase2j = load_inputs()
    diagnostics = build_diagnostics(scored, prices, run_summary, phase2i_a, phase2j)
    write_diagnostics(diagnostics, output_path)
    return diagnostics


def main() -> int:
    t0 = time.perf_counter()
    d = run()
    elapsed = time.perf_counter() - t0
    p = d["panel"]
    kd = d["keep_drop"]
    cmp = d["comparison_to_phase2i_a"]
    print(f"[phase2k-b] panel rows / tickers / dates : {p['n_rows']} / "
          f"{p['n_tickers']} / {p['n_dates']}")
    print(f"[phase2k-b] window                       : {p['date_start']} -> "
          f"{p['date_end']}")
    print(f"[phase2k-b] horizons (residual labels)   : "
          f"{d['residualization_config']['horizons']}")
    print(f"[phase2k-b] 63d risk premium removed     : "
          f"{cmp['neutralization_removed_63d_risk_premium']}")
    print(f"[phase2k-b] KEEP_FOR_ROBUSTNESS_TEST ({kd['n_keep_for_robustness_test']}) : "
          f"{kd['keep_for_robustness_test']}")
    print(f"[phase2k-b] NEED_MORE_DATA ({kd['n_need_more_data']})           : "
          f"{kd['need_more_data']}")
    print(f"[phase2k-b] DROP ({kd['n_drop']})                     : {kd['drop']}")
    print(f"[phase2k-b] CONTROL_ONLY ({kd['n_control_only']})             : "
          f"{kd['control_only']}")
    print(f"[phase2k-b] recommended next phase       : "
          f"{d['recommended_next_phase']['phase']} "
          f"({d['recommended_next_phase']['title']})")
    print(f"[phase2k-b] diagnostics written          : {RESIDUAL_JSON}")
    print(f"[phase2k-b] elapsed seconds              : {elapsed:.2f}")
    print("[phase2k-b] research-only diagnostics; safety flags emitted in JSON.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
