"""Phase 2K-N Narrow Model-Free Retest for the Best Targeted Alpha Leads (offline, read-only).

Phase 2K-M diagnosed the 5 Phase 2K-L TARGETED_DIAGNOSTIC_CANDIDATE leads, classified
3 of them as NARROW_RETEST_CANDIDATE (and 2 as HOLD_FOR_SECTOR_RELATIVE_VARIANT), and
returned RUN_NARROW_MODEL_FREE_RETEST, routing here. Phase 2K-N runs a narrow,
pre-registered, model-free retest of ONLY those 3 approved candidate / horizon pairs on
the expanded, survivorship-caveated D: panel. It recomputes the trailing signals and the
forward residual labels from the D: price history, re-measures strict residual rank-IC /
bootstrap / regime / concentration / stability diagnostics, and decides whether any lead
deserves a later confirmation phase.

It does NOT train, fit, or score any model and creates no model candidate. It:

  1. Reads the Phase 2K-M result JSON and confirms it routed here
     (RUN_NARROW_MODEL_FREE_RETEST -> 2K-N, exactly 3 NARROW_RETEST_CANDIDATE leads, no
     model candidate / no model training / no new D: screen). The 3 pre-registered
     candidate / horizon pairs are fixed in this source and cross-checked against the
     Phase 2K-M narrow-retest lead ids; the candidate set is never expanded.
  2. Reads the Phase 2K-H summary and the D: data-quality / build / survivorship JSONs and
     confirms the manual build is present and ready (PASS / PASS_WITH_CAVEAT,
     point_in_time_membership_claimed == false, the D: data root).
  3. Reads the D: expanded price-history CSV (read-only, with pandas) and COMPUTES trailing
     point-in-time signals + the same trailing risk controls the prior phases used. Every
     feature is a trailing window; nothing uses forward information.
  4. Retests ONLY the 3 pre-registered pairs -- residual_price_momentum_12_1 @ 5d,
     short_horizon_residual_reversal_5d @ 21d, short_horizon_residual_reversal_21d @ 21d --
     each at its own horizon. The two HOLD_FOR_SECTOR_RELATIVE_VARIANT leads
     (short_horizon_residual_reversal_5d @ 5d, short_horizon_residual_reversal_21d @ 5d)
     and the stopped liquidity candidate avg_dollar_volume_21d are explicitly excluded and
     never retested or ranked as standalone alpha; no broad alpha screen is run.
  5. For each pair it measures only model-free, rank-only metrics on the beta/vol-neutralized
     residual forward-excess label: per-date residual rank IC, mean / median, information
     ratio, same-sign fraction, moving-block bootstrap CI, top-minus-bottom spread,
     top-decile hit rate, top-quintile concentration, leave-one-year-out stability, and SPY
     up/down and market-vol regime splits, comparing each against the prior Phase 2K-K /
     2K-M numbers. Nothing is fitted.
  6. Issues a conservative per-pair recommendation -- KEEP_FOR_CONFIRMATION_DESIGN /
     RESEARCH_LEAD_RECONFIRMED / DROP_AFTER_NARROW_RETEST / NEED_POINT_IN_TIME_UNIVERSE --
     and an overall recommendation, then routes to Phase 2K-O. Every result is reported as
     survivorship-biased / current-membership caveated; a KEEP is NOT a production edge and
     NOT permission to train or serve a model.

This phase reads the small Phase 2K-M / 2K-L / 2K-K / 2K-H JSON summaries and the D:
expanded CSV / JSONs, and writes exactly one small results JSON in the C: repo. It never
writes to the D: drive, performs no infrastructure action, and mutates no datastore; the
machine-readable safety flags are emitted in the results JSON, and the full guardrail
rationale lives in docs/phase2k_n_narrow_model_free_retest_v1.md, not in this source.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

PHASE = "2K-N"

_OUTPUT_DIR = os.path.join("research", "output")

# Read-only upstream summaries (small, Git-tracked, in the C: repo).
PHASE2K_M_JSON = os.path.join(_OUTPUT_DIR, "phase2k_m_targeted_lead_diagnostics.json")
PHASE2K_L_JSON = os.path.join(_OUTPUT_DIR, "phase2k_l_research_lead_diagnostics.json")
PHASE2K_K_JSON = os.path.join(_OUTPUT_DIR, "phase2k_k_expanded_alpha_screen.json")
PHASE2K_H_JSON = os.path.join(_OUTPUT_DIR, "phase2k_h_manual_build_run_summary.json")

# External data root on the D: drive (mirrors the Phase 2K-G / 2K-I / 2K-K DATA_ROOT).
DATA_ROOT = r"D:\Stock_Prediction_app_data\phase2k_g"

PRICE_HISTORY_FILENAME = "phase2k_g_expanded_price_history_free.csv"
DATA_QUALITY_FILENAME = "phase2k_g_data_quality_report.json"
BUILD_SUMMARY_FILENAME = "phase2k_g_data_build_summary.json"
SURVIVORSHIP_FILENAME = "phase2k_g_survivorship_caveat.json"

_D_OUTPUT_DIR = os.path.join(DATA_ROOT, "output")
PRICE_HISTORY_CSV = os.path.join(_D_OUTPUT_DIR, PRICE_HISTORY_FILENAME)
DATA_QUALITY_JSON = os.path.join(_D_OUTPUT_DIR, DATA_QUALITY_FILENAME)
BUILD_SUMMARY_JSON = os.path.join(_D_OUTPUT_DIR, BUILD_SUMMARY_FILENAME)
SURVIVORSHIP_JSON = os.path.join(_D_OUTPUT_DIR, SURVIVORSHIP_FILENAME)

# The single small output this analyzer writes (stays in the C: repo, Git-tracked).
RESULTS_JSON = os.path.join(_OUTPUT_DIR, "phase2k_n_narrow_model_free_retest.json")

DATE_COL = "date"
TICKER_COL = "ticker"
BENCHMARK = "SPY"

# The candidate stopped in Phase 2K-J after the Phase 2K-I expanded retest failure. It is
# EXCLUDED as a standalone alpha; it may appear only as a control / diagnostic, never
# retested or ranked.
STOPPED_CANDIDATE = "avg_dollar_volume_21d"

# Trailing risk controls regressed out to form the residual label (identical set to
# Phase 2K-B / 2K-C / 2L-B / 2K-I / 2K-K).
CONTROL_FEATURES: List[str] = [
    "rolling_beta_63d",
    "realized_vol_21d",
    "realized_vol_63d",
    "downside_vol_21d",
    "rolling_corr_spy_63d",
]

# The 3 PRE-REGISTERED candidate / horizon pairs (and ONLY these). Each is the Phase 2K-M
# NARROW_RETEST_CANDIDATE set; the candidate set is never expanded. Every pair is bullish
# (reference_sign +1): a positive residual rank IC is the hypothesised direction.
PRE_REGISTERED_PAIRS: List[Dict[str, Any]] = [
    {
        "candidate": "residual_price_momentum_12_1",
        "name": "Residual Price Momentum (12-1)",
        "feature": "momentum_12_1",
        "category": "price_momentum_alternatives",
        "reference_sign": 1,
        "horizon_days": 5,
    },
    {
        "candidate": "short_horizon_residual_reversal_5d",
        "name": "Short-Horizon Residual Reversal (5d)",
        "feature": "reversal_5d",
        "category": "short_term_reversal_mean_reversion",
        "reference_sign": 1,
        "horizon_days": 21,
    },
    {
        "candidate": "short_horizon_residual_reversal_21d",
        "name": "Short-Horizon Residual Reversal (21d)",
        "feature": "reversal_21d",
        "category": "short_term_reversal_mean_reversion",
        "reference_sign": 1,
        "horizon_days": 21,
    },
]

# Pairs deliberately NOT retested in this phase (carried from Phase 2K-M).
EXCLUDED_PAIRS: List[Dict[str, Any]] = [
    {
        "lead_id": "short_horizon_residual_reversal_5d@5d",
        "candidate": "short_horizon_residual_reversal_5d",
        "horizon_days": 5,
        "role": "hold_for_sector_relative_variant",
        "reason": (
            "Phase 2K-M classified this lead HOLD_FOR_SECTOR_RELATIVE_VARIANT (its IC sign "
            "is regime-dependent at the 5d horizon); it is NOT retested in Phase 2K-N and "
            "must wait for a sector-relative feasibility assessment."),
    },
    {
        "lead_id": "short_horizon_residual_reversal_21d@5d",
        "candidate": "short_horizon_residual_reversal_21d",
        "horizon_days": 5,
        "role": "hold_for_sector_relative_variant",
        "reason": (
            "Phase 2K-M classified this lead HOLD_FOR_SECTOR_RELATIVE_VARIANT (its IC sign "
            "is regime-dependent at the 5d horizon); it is NOT retested in Phase 2K-N and "
            "must wait for a sector-relative feasibility assessment."),
    },
    {
        "lead_id": STOPPED_CANDIDATE,
        "candidate": STOPPED_CANDIDATE,
        "horizon_days": None,
        "role": "control_or_diagnostic_only",
        "reason": (
            "avg_dollar_volume_21d failed the Phase 2K-I expanded retest and was stopped by "
            "Phase 2K-J as a standalone alpha. It is excluded from the retest as a ranking "
            "signal and may only ever be computed as a size / liquidity control or "
            "diagnostic, never retested or ranked here."),
    },
]

# Forward residual-label horizons exercised (the union of the pre-registered horizons only).
HORIZONS: List[int] = sorted({p["horizon_days"] for p in PRE_REGISTERED_PAIRS})

# Metric / gate thresholds (inherited from Phase 2I-A / 2K-B / 2K-C / 2L-B / 2K-I / 2K-K).
RANK_IC_FLOOR = 0.03               # the confirmation-design mean-residual-IC floor
RANK_IC_NEAR_FLOOR = 0.025         # "convincingly close" floor (needs strong support)
MAJORITY_SIGN_FRACTION = 0.52      # same-sign date fraction must clear this for a KEEP
SUPPORT_INFO_RATIO = 0.10          # supporting information ratio for the near-floor path
SUPPORT_SIGN_FRACTION = 0.55       # supporting same-sign fraction for the near-floor path
CONCENTRATION_TOP3_THRESHOLD = 0.50
MIN_EFFECTIVE_OBS = 8.0
MIN_IC_DATES = 60

MIN_NAMES_PER_DATE = 5
MIN_NAMES_DECILE = 10
RESID_MIN_NAMES = 10

# Moving-block bootstrap knobs (same family as Phase 2I-B / 2K-C / 2L-B / 2K-I / 2K-K).
BOOTSTRAP_N = 1000
CI_ALPHA = 0.05
SEED = 12345

# Per-pair recommendation vocabulary (the ONLY values a pair recommendation may emit).
KEEP = "KEEP_FOR_CONFIRMATION_DESIGN"
RECONFIRMED = "RESEARCH_LEAD_RECONFIRMED"
DROP = "DROP_AFTER_NARROW_RETEST"
NEED_PIT = "NEED_POINT_IN_TIME_UNIVERSE"
_ALLOWED_PAIR_RECS = [KEEP, RECONFIRMED, DROP, NEED_PIT]

# Overall-recommendation vocabulary.
REC_PROCEED = "PROCEED_TO_CONFIRMATION_DESIGN"
REC_RECONFIRMED = "RESEARCH_LEADS_RECONFIRMED_BUT_NOT_CONFIRMABLE"
REC_DROP = "DROP_NARROW_RETEST_LEADS"
REC_NEED_PIT = "NEED_POINT_IN_TIME_UNIVERSE_BEFORE_CONFIRMATION"
_ALLOWED_RECOMMENDATIONS = [REC_PROCEED, REC_RECONFIRMED, REC_DROP, REC_NEED_PIT]

# Next-phase titles (always Phase 2K-O; title chosen by the recommendation).
TITLE_PROCEED = "Confirmation Design for Narrow Retest Survivors"
TITLE_RECONFIRMED = "Decision Gate for Reconfirmed But Sub-Floor Leads"
TITLE_DROP = "Alpha Backlog Refresh v3 After Narrow Retest Failure"
TITLE_NEED_PIT = "Point-in-Time Universe Decision Before Confirmation"


# --------------------------------------------------------------------------- #
# Small pure helpers
# --------------------------------------------------------------------------- #
def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _now() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def _round(x: Optional[float], n: int = 6) -> Optional[float]:
    if x is None:
        return None
    try:
        if pd.isna(x) or (isinstance(x, float) and math.isinf(x)):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return round(float(x), n)
    except (TypeError, ValueError):
        return None


def _sign(x: Optional[float]) -> int:
    if x is None or pd.isna(x) or x == 0:
        return 0
    return 1 if x > 0 else -1


def _safe_read_json(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return None


def _resolve_d_paths(data_root: str) -> Dict[str, str]:
    out = os.path.join(data_root, "output")
    return {
        "output_dir": out,
        "price_history_csv": os.path.join(out, PRICE_HISTORY_FILENAME),
        "data_quality_report_json": os.path.join(out, DATA_QUALITY_FILENAME),
        "data_build_summary_json": os.path.join(out, BUILD_SUMMARY_FILENAME),
        "survivorship_caveat_json": os.path.join(out, SURVIVORSHIP_FILENAME),
    }


def _lead_id(candidate: str, horizon: int) -> str:
    return f"{candidate}@{horizon}d"


# --------------------------------------------------------------------------- #
# Upstream Phase 2K-M routing confirmation + Phase 2K-H readiness
# --------------------------------------------------------------------------- #
def summarize_phase2k_m(km: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Read-only digest of the Phase 2K-M diagnostics plus the routing confirmation."""
    if km is None:
        return {"present": False, "routing_confirmed": False}
    rec = km.get("recommendation", {}) or {}
    nxt = km.get("recommended_next_phase", {}) or {}
    counts = km.get("follow_up_type_counts", {}) or {}
    plan = km.get("narrow_retest_plan", {}) or {}
    n_narrow = int(counts.get("NARROW_RETEST_CANDIDATE", 0) or 0)
    narrow_ids = list(plan.get("narrow_retest_candidate_lead_ids", []) or [])
    pre_reg_ids = [_lead_id(p["candidate"], p["horizon_days"]) for p in PRE_REGISTERED_PAIRS]

    phase_ok = km.get("phase") == "2K-M"
    rec_ok = rec.get("recommendation") == "RUN_NARROW_MODEL_FREE_RETEST"
    routes_ok = nxt.get("phase") == "2K-N"
    title_ok = nxt.get("title") == "Narrow Model-Free Retest for Best Targeted Alpha Leads"
    narrow_count_ok = n_narrow == 3
    create_ok = rec.get("create_model_candidate_now") is False
    train_ok = rec.get("train_model_now") is False
    screen_ok = rec.get("ran_new_d_screen") is False
    ids_match = set(narrow_ids) == set(pre_reg_ids)
    return {
        "present": True,
        "phase": km.get("phase"),
        "recommendation": rec.get("recommendation"),
        "follow_up_type_counts": counts,
        "n_narrow_retest_candidates": n_narrow,
        "narrow_retest_candidate_lead_ids": narrow_ids,
        "pre_registered_lead_ids": pre_reg_ids,
        "recommended_next_phase": nxt,
        "phase_is_2k_m": phase_ok,
        "recommendation_is_run_narrow_retest": rec_ok,
        "recommended_next_phase_is_2k_n": routes_ok,
        "recommended_next_phase_title_matches": title_ok,
        "narrow_retest_candidate_count_is_3": narrow_count_ok,
        "pre_registered_pairs_match_phase2k_m_narrow_leads": ids_match,
        "create_model_candidate_now_false": create_ok,
        "train_model_now_false": train_ok,
        "ran_new_d_screen_false": screen_ok,
        "routing_confirmed": bool(
            phase_ok and rec_ok and routes_ok and title_ok and narrow_count_ok
            and create_ok and train_ok and screen_ok and ids_match),
    }


def summarize_phase2k_h(kh: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Read-only digest of the Phase 2K-H summary plus the readiness checks."""
    if kh is None:
        return {
            "present": False,
            "manual_build_detected": False,
            "ready_for_retest": False,
            "data_root": None,
            "ready_for_screen": False,
        }
    dec = kh.get("decision", {}) or {}
    dq = kh.get("data_quality_summary", {}) or {}
    sc = kh.get("survivorship_caveat_summary", {}) or {}
    status = dq.get("status")
    pit = sc.get("point_in_time_membership_claimed")
    ready = bool(
        dec.get("manual_build_detected") is True
        and dec.get("ready_for_retest") is True
        and status in ("PASS", "PASS_WITH_CAVEAT")
        and pit is False)
    return {
        "present": True,
        "phase": kh.get("phase"),
        "manual_build_detected": bool(dec.get("manual_build_detected")),
        "ready_for_retest": bool(dec.get("ready_for_retest")),
        "data_quality_status": status,
        "point_in_time_membership_claimed": pit,
        "data_root": kh.get("data_root"),
        "ready_for_screen": ready,
    }


def summarize_data_quality(dq: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if dq is None:
        return {"present": False, "status": None, "passed": False}
    stats = dq.get("stats", {}) or {}
    checks = dq.get("checks", []) or []
    status = dq.get("status")
    return {
        "present": True,
        "status": status,
        "passed": status in ("PASS", "PASS_WITH_CAVEAT"),
        "row_count": stats.get("row_count"),
        "ticker_count": stats.get("ticker_count"),
        "date_count": stats.get("date_count"),
        "years_span": stats.get("years_span"),
        "min_tickers_per_date": stats.get("min_tickers_per_date"),
        "benchmark_coverage_fraction": stats.get("benchmark_coverage_fraction"),
        "hard_checks_failed": [c.get("check_id") for c in checks
                               if c.get("severity") == "hard" and not c.get("passed")],
    }


def summarize_survivorship(sc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if sc is None:
        return {
            "present": False,
            "membership_basis": None,
            "point_in_time_membership_claimed": None,
            "reported_as_survivorship_biased": None,
            "all_results_must_be_reported_survivorship_biased": True,
        }
    return {
        "present": True,
        "membership_basis": sc.get("membership_basis"),
        "point_in_time_membership_claimed": sc.get("point_in_time_membership_claimed"),
        "reported_as_survivorship_biased": sc.get("reported_as_survivorship_biased"),
        "clean_point_in_time_build_deferred": sc.get("clean_point_in_time_build_deferred"),
        "universe_ticker_count": sc.get("universe_ticker_count"),
        "all_results_must_be_reported_survivorship_biased": True,
    }


def phase2k_m_prior_metrics(km: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Prior (pre-retest) metrics per lead_id carried from Phase 2K-M for comparison."""
    out: Dict[str, Dict[str, Any]] = {}
    if km is None:
        return out
    for l in km.get("targeted_leads", []) or []:
        lid = l.get("lead_id")
        if lid:
            out[lid] = {
                "prior_mean_residual_rank_ic": l.get("mean_residual_rank_ic"),
                "prior_bootstrap_ci_low": l.get("bootstrap_ci_low"),
                "prior_diagnostic_score": l.get("diagnostic_score"),
                "prior_failed_keep_gates": l.get("failed_keep_gates"),
            }
    return out


# --------------------------------------------------------------------------- #
# Feature engineering from the expanded price history (all trailing; no look-ahead)
# --------------------------------------------------------------------------- #
def _read_price_history(path: str) -> "pd.DataFrame":
    df = pd.read_csv(path)
    if DATE_COL in df.columns:
        df[DATE_COL] = pd.to_datetime(df[DATE_COL], errors="coerce")
    for col in ("adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close",
                "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if TICKER_COL in df.columns:
        df[TICKER_COL] = df[TICKER_COL].astype(str).str.upper()
    df = df.dropna(subset=[DATE_COL, TICKER_COL, "adjusted_close"])
    df = df[df["adjusted_close"] > 0]
    df = df.drop_duplicates(subset=[DATE_COL, TICKER_COL], keep="first")
    return df.sort_values([TICKER_COL, DATE_COL]).reset_index(drop=True)


def _ensure_date_ticker_columns(df: "pd.DataFrame") -> "pd.DataFrame":
    """Guarantee `ticker` and `date` are ordinary columns (never stuck in the index)."""
    out = df
    index_names = list(out.index.names) if out.index.names is not None else []
    from_index = [c for c in (TICKER_COL, DATE_COL)
                  if c not in out.columns and c in index_names]
    if from_index:
        out = out.reset_index(level=from_index)
    if out.columns.duplicated().any():
        out = out.loc[:, ~out.columns.duplicated()]
    return out.reset_index(drop=True)


def compute_features(prices: "pd.DataFrame",
                     benchmark: str = BENCHMARK) -> "pd.DataFrame":
    """Derive the 3 pre-registered signals + trailing risk controls from adjusted OHLCV.

    Every column is a trailing (point-in-time) rolling statistic computed per ticker: it
    uses only data up to and including the as-of date, never forward information. The signal
    columns are momentum_12_1 (residual_price_momentum_12_1), reversal_5d
    (short_horizon_residual_reversal_5d), and reversal_21d
    (short_horizon_residual_reversal_21d). avg_dollar_volume_21d is also computed, but only
    as a control / diagnostic -- it is never retested or ranked as an alpha.
    """
    df = prices.copy()
    df["dollar_volume"] = df["adjusted_close"] * df["volume"]
    df["daily_return"] = (df.groupby(TICKER_COL)["adjusted_close"]
                          .pct_change(fill_method=None))

    # Market (benchmark) daily return per date, merged back onto every row.
    spy = df[df[TICKER_COL] == benchmark][[DATE_COL, "daily_return"]].rename(
        columns={"daily_return": "spy_return"})
    spy = spy.dropna(subset=[DATE_COL]).drop_duplicates(subset=[DATE_COL])
    df = df.merge(spy, on=DATE_COL, how="left")

    def _per_ticker(sub: "pd.DataFrame") -> "pd.DataFrame":
        sub = sub.sort_values(DATE_COL).copy()
        r = sub["daily_return"]
        m = sub["spy_return"]
        c = sub["adjusted_close"]

        # Trailing risk controls (diagnostic-only liquidity control included).
        sub["avg_dollar_volume_21d"] = sub["dollar_volume"].rolling(21, min_periods=21).mean()
        sub["realized_vol_21d"] = r.rolling(21, min_periods=21).std()
        sub["realized_vol_63d"] = r.rolling(63, min_periods=63).std()
        sub["downside_vol_21d"] = r.clip(upper=0.0).rolling(21, min_periods=21).std()
        cov = r.rolling(63, min_periods=63).cov(m)
        var = m.rolling(63, min_periods=63).var()
        sub["rolling_beta_63d"] = cov / var.where(var > 0)
        sub["rolling_corr_spy_63d"] = r.rolling(63, min_periods=63).corr(m)

        # Trailing cumulative returns (close / close.shift(h) - 1).
        ret_5d = c / c.shift(5) - 1.0
        ret_21d = c / c.shift(21) - 1.0

        # Pre-registered signals. momentum_12_1 is the 252d return excluding the most recent
        # 21 sessions (the classic 12-1 momentum, formation skips the last month); the
        # reversal signals are the negated trailing returns (bullish reversal orientation).
        sub["momentum_12_1"] = c.shift(21) / c.shift(252) - 1.0
        sub["reversal_5d"] = -ret_5d
        sub["reversal_21d"] = -ret_21d
        return sub

    parts = [_per_ticker(sub) for _, sub in df.groupby(TICKER_COL, sort=False)]
    out = pd.concat(parts, axis=0, ignore_index=True) if parts else df.copy()
    return _ensure_date_ticker_columns(out)


def build_market_vol(prices_with_feats: "pd.DataFrame") -> "pd.Series":
    """SPY trailing 21-session realized vol per date (the market-vol regime scalar)."""
    spy = prices_with_feats[prices_with_feats[TICKER_COL] == BENCHMARK]
    if spy.empty:
        return pd.Series(dtype=float)
    s = spy.dropna(subset=[DATE_COL]).drop_duplicates(subset=[DATE_COL]).set_index(
        DATE_COL)["realized_vol_21d"].dropna()
    s.index = pd.to_datetime(s.index)
    return s


def build_forward_excess_labels(prices: "pd.DataFrame") -> "pd.DataFrame":
    """Per (ticker, date) forward h-session excess-vs-SPY return for each needed horizon.

    Forward return is adjusted_close shifted -h within each ticker; SPY's own forward return
    over the same horizon is subtracted. Rows whose forward window runs past the data end
    become NaN and drop out downstream. Labels are never forward filled.
    """
    px = prices.sort_values([TICKER_COL, DATE_COL]).copy()
    for h in HORIZONS:
        px[f"_fwd_{h}"] = (px.groupby(TICKER_COL)["adjusted_close"].shift(-h)
                           / px["adjusted_close"] - 1.0)
    spy_cols = {f"_fwd_{h}": f"_spy_fwd_{h}" for h in HORIZONS}
    spy = (px[px[TICKER_COL] == BENCHMARK][[DATE_COL] + list(spy_cols)]
           .rename(columns=spy_cols).drop_duplicates(subset=[DATE_COL]))
    names = px[px[TICKER_COL] != BENCHMARK].merge(spy, on=DATE_COL, how="left")
    for h in HORIZONS:
        names[f"excess_{h}d"] = names[f"_fwd_{h}"] - names[f"_spy_fwd_{h}"]
    keep = [TICKER_COL, DATE_COL] + [f"excess_{h}d" for h in HORIZONS]
    return names[keep]


def spy_forward_return(prices: "pd.DataFrame", h: int) -> "pd.Series":
    """SPY's own forward h-session return per date (the SPY up/down regime label)."""
    spy = prices[prices[TICKER_COL] == BENCHMARK].sort_values(DATE_COL).copy()
    if spy.empty:
        return pd.Series(dtype=float)
    spy["_fwd"] = spy["adjusted_close"].shift(-h) / spy["adjusted_close"] - 1.0
    s = spy.dropna(subset=[DATE_COL]).drop_duplicates(subset=[DATE_COL]).set_index(
        DATE_COL)["_fwd"].dropna()
    s.index = pd.to_datetime(s.index)
    return s


# --------------------------------------------------------------------------- #
# Residualization (per-date cross-sectional OLS; demean fallback; no look-ahead)
# --------------------------------------------------------------------------- #
def _ols_residual(y: "np.ndarray", xc: "np.ndarray") -> "np.ndarray":
    """Residual of y on controls xc with an intercept via least squares (no scipy)."""
    design = np.column_stack([np.ones(len(y)), xc])
    beta, _res, _rank, _sv = np.linalg.lstsq(design, y, rcond=None)
    return y - design @ beta


def residualize_label(panel: "pd.DataFrame", lcol: str,
                      controls: List[str]) -> Dict[str, Any]:
    """Cross-sectional residual of a forward excess label on trailing controls.

    Per date the forward excess label is regressed on the usable controls (finite,
    non-constant, enough names) plus an intercept; the residual is the risk-neutralized
    label. When a date has no usable control or too few names, the robust fallback is a
    cross-sectional demean. The least-squares solve uses no forward information, so there is
    no look-ahead (it is not a fitted, persisted, or scored model).
    """
    resid = pd.Series(np.nan, index=panel.index, dtype=float)
    ols_dates = 0
    fallback_dates = 0
    for _, block in panel.groupby(DATE_COL, sort=False):
        yfin = block[lcol].notna()
        if int(yfin.sum()) < MIN_NAMES_PER_DATE:
            continue
        usable = [c for c in controls
                  if c in block.columns
                  and int(block.loc[yfin, c].notna().sum()) >= RESID_MIN_NAMES
                  and block.loc[yfin, c].dropna().nunique() > 1]
        if usable:
            rows = yfin & block[usable].notna().all(axis=1)
            if int(rows.sum()) >= RESID_MIN_NAMES:
                y = block.loc[rows, lcol].to_numpy(dtype=float)
                xc = block.loc[rows, usable].to_numpy(dtype=float)
                resid.loc[block.index[rows.to_numpy()]] = _ols_residual(y, xc)
                ols_dates += 1
                continue
        yv = block.loc[yfin, lcol]
        resid.loc[yv.index] = (yv - yv.mean()).to_numpy()
        fallback_dates += 1
    return {"resid": resid, "ols_dates": ols_dates, "fallback_dates": fallback_dates}


# --------------------------------------------------------------------------- #
# Per-date rank IC and cross-sectional summary statistics (vectorized; pure)
# --------------------------------------------------------------------------- #
def per_date_rank_ic(sub: "pd.DataFrame", fcol: str, lcol: str) -> "pd.Series":
    """Cross-sectional Spearman(feature, residual label) per date (Pearson on ranks)."""
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
    return ics.sort_index()


def spread_and_hit_rate(sub: "pd.DataFrame", fcol: str, lcol: str):
    """Top-minus-bottom quintile residual spread and top-decile residual hit rate."""
    if sub.empty:
        return None, None
    g = sub.groupby(DATE_COL)[fcol]
    size = g.transform("size")
    nuniq = g.transform("nunique")
    elig = sub[(size >= MIN_NAMES_DECILE) & (nuniq >= 5)]
    spread = None
    if not elig.empty:
        ge = elig.groupby(DATE_COL)[fcol]
        rf = ge.rank(method="first")
        ne = ge.transform("size")
        bucket = ((rf > 1 + 0.2 * (ne - 1)).astype(int)
                  + (rf > 1 + 0.4 * (ne - 1)).astype(int)
                  + (rf > 1 + 0.6 * (ne - 1)).astype(int)
                  + (rf > 1 + 0.8 * (ne - 1)).astype(int))
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


def long_leg_concentration(sub: "pd.DataFrame", fcol: str, lcol: str) -> Dict[str, Any]:
    """Top-quintile (long-leg) ticker concentration; pure ranking, nothing fitted."""
    out = {"top_quintile_ticker_concentration": None, "top3_long_leg_share": None,
           "n_unique_top_quintile_tickers": 0, "top_quintile_rows": 0}
    s = sub[[DATE_COL, TICKER_COL, fcol, lcol]].dropna(subset=[fcol, lcol]).copy()
    if s.empty:
        return out
    g = s.groupby(DATE_COL)[fcol]
    size = g.transform("size")
    nuniq = g.transform("nunique")
    elig = s[(size >= MIN_NAMES_DECILE) & (nuniq >= 5)].copy()
    if elig.empty:
        return out
    ge = elig.groupby(DATE_COL)[fcol]
    rf = ge.rank(method="first")
    ne = ge.transform("size")
    bucket = ((rf > 1 + 0.2 * (ne - 1)).astype(int)
              + (rf > 1 + 0.4 * (ne - 1)).astype(int)
              + (rf > 1 + 0.6 * (ne - 1)).astype(int)
              + (rf > 1 + 0.8 * (ne - 1)).astype(int))
    elig["_b"] = bucket
    top = elig[elig["_b"] == 4]
    n_top = int(len(top))
    if n_top == 0:
        return out
    counts = top[TICKER_COL].value_counts()
    shares = counts / n_top
    out.update({
        "top_quintile_ticker_concentration": _round(float(shares.iloc[0])),
        "top3_long_leg_share": _round(float(shares.iloc[:3].sum())),
        "n_unique_top_quintile_tickers": int(counts.size),
        "top_quintile_rows": n_top,
    })
    return out


def block_bootstrap_ci(ic_values: "np.ndarray", block_len: int,
                       alpha: float = CI_ALPHA, n_boot: int = BOOTSTRAP_N
                       ) -> Tuple[Optional[float], Optional[float]]:
    """Moving-block bootstrap CI for the mean per-date residual IC."""
    arr = np.asarray(ic_values, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = arr.size
    if n < 2:
        return None, None
    rng = np.random.RandomState(SEED)
    bl = max(1, min(block_len, n))
    n_blocks = int(np.ceil(n / bl))
    max_start = n - bl
    span = np.arange(bl)
    means = np.empty(n_boot, dtype=float)
    for b in range(n_boot):
        starts = rng.randint(0, max_start + 1, size=n_blocks)
        idx = (starts[:, None] + span[None, :]).ravel()[:n]
        means[b] = arr[idx].mean()
    lo = float(np.percentile(means, 100 * alpha / 2.0))
    hi = float(np.percentile(means, 100 * (1.0 - alpha / 2.0)))
    return lo, hi


def _regime_split(ics: "pd.Series", value_by_date: "pd.Series",
                  mode: str) -> Optional[Dict[str, Any]]:
    aligned = pd.DataFrame({"ic": ics})
    aligned["v"] = value_by_date.reindex(ics.index)
    aligned = aligned.dropna()
    if aligned.empty:
        return None
    if mode == "sign":
        hi = aligned[aligned["v"] > 0]["ic"]
        lo = aligned[aligned["v"] <= 0]["ic"]
        hi_ic = _round(hi.mean()) if len(hi) else None
        lo_ic = _round(lo.mean()) if len(lo) else None
        out = {
            "threshold": 0.0,
            "positive": {"rank_ic": hi_ic, "n_dates": int(len(hi))},
            "negative": {"rank_ic": lo_ic, "n_dates": int(len(lo))},
        }
    else:
        med = float(aligned["v"].median())
        hi = aligned[aligned["v"] >= med]["ic"]
        lo = aligned[aligned["v"] < med]["ic"]
        hi_ic = _round(hi.mean()) if len(hi) else None
        lo_ic = _round(lo.mean()) if len(lo) else None
        out = {
            "median_split": _round(med),
            "high": {"rank_ic": hi_ic, "n_dates": int(len(hi))},
            "low": {"rank_ic": lo_ic, "n_dates": int(len(lo))},
        }
    out["sign_flips_across_regime"] = bool(
        hi_ic is not None and lo_ic is not None
        and _sign(hi_ic) != 0 and _sign(lo_ic) != 0
        and _sign(hi_ic) != _sign(lo_ic))
    return out


def leave_one_year_out(ics: "pd.Series", reference_sign: int) -> Dict[str, Any]:
    """Drop each calendar year, re-mean the rest, check the IC sign is preserved."""
    if not len(ics):
        return {"sign_stable": False, "years_present": 0}
    years = pd.Index(ics.index).year
    sign_stable = bool(reference_sign != 0)
    uniq = sorted(set(int(y) for y in years))
    if len(uniq) < 2:
        sign_stable = False
    for y in uniq:
        remaining = ics[years != y]
        m = float(remaining.mean()) if len(remaining) else None
        ok = bool(m is not None and reference_sign != 0 and _sign(m) == reference_sign)
        if not ok:
            sign_stable = False
    return {"sign_stable": sign_stable, "years_present": len(uniq)}


# --------------------------------------------------------------------------- #
# Per-pair retest metrics + conservative recommendation
# --------------------------------------------------------------------------- #
def retest_pair(panel: "pd.DataFrame", fcol: str, h: int, reference_sign: int,
                spy_fwd: "pd.Series", market_vol: "pd.Series") -> Dict[str, Any]:
    """Model-free, rank-only retest metrics for one pre-registered candidate / horizon pair."""
    lcol = f"resid_{h}d"
    sub = panel[[DATE_COL, TICKER_COL, fcol, lcol]].dropna(subset=[fcol, lcol]).copy()
    ics = per_date_rank_ic(sub[[DATE_COL, fcol, lcol]], fcol, lcol)
    n_dates = int(len(ics))
    mean_ic = float(ics.mean()) if n_dates else None
    median_ic = float(ics.median()) if n_dates else None
    std_ic = float(ics.std(ddof=1)) if n_dates > 1 else None
    info_ratio = (mean_ic / std_ic
                  if (mean_ic is not None and std_ic not in (None, 0.0)
                      and not pd.isna(std_ic)) else None)
    overall_sign = _sign(mean_ic)
    n_same_sign = int(sum(1 for v in ics if _sign(v) == reference_sign)) if n_dates else 0
    frac_same_sign = (n_same_sign / n_dates) if n_dates else None
    lo, hi = block_bootstrap_ci(ics.to_numpy(), h)
    ci_excludes_zero = bool(lo is not None and hi is not None
                            and ((lo > 0 and hi > 0) or (lo < 0 and hi < 0)))
    spread, hit = spread_and_hit_rate(sub, fcol, lcol)
    conc = long_leg_concentration(sub, fcol, lcol)
    spy_regime = _regime_split(ics, spy_fwd, "sign")
    vol_regime = _regime_split(ics, market_vol, "median")
    loyo = leave_one_year_out(ics, reference_sign)
    eff_obs = (n_dates / h) if h else 0.0
    enough_data = bool(n_dates >= MIN_IC_DATES and eff_obs >= MIN_EFFECTIVE_OBS)
    n_tickers = int(sub[TICKER_COL].nunique()) if not sub.empty else 0
    return {
        "horizon_days": h,
        "n_dates": n_dates,
        "n_tickers": n_tickers,
        "n_obs": int(len(sub)),
        "effective_obs": _round(eff_obs, 3),
        "enough_data": enough_data,
        "mean_residual_rank_ic": _round(mean_ic),
        "median_residual_rank_ic": _round(median_ic),
        "std_residual_rank_ic": _round(std_ic),
        "information_ratio": _round(info_ratio),
        "direction": ("bullish" if overall_sign > 0
                      else "bearish" if overall_sign < 0 else "flat"),
        "n_dates_same_sign_as_reference": n_same_sign,
        "frac_dates_same_sign_as_reference": _round(frac_same_sign),
        "bootstrap_mean_ic_ci": {
            "method": "moving_block",
            "block_len_sessions": h,
            "n_resamples": BOOTSTRAP_N,
            "alpha": CI_ALPHA,
            "ci_low": _round(lo),
            "ci_high": _round(hi),
            "excludes_zero": ci_excludes_zero,
        },
        "top_minus_bottom_residual_spread": _round(spread),
        "top_decile_residual_hit_rate": _round(hit),
        "concentration": conc,
        "regime_summary": {
            "spy_up_rank_ic": (spy_regime or {}).get("positive", {}).get("rank_ic")
            if spy_regime else None,
            "spy_down_rank_ic": (spy_regime or {}).get("negative", {}).get("rank_ic")
            if spy_regime else None,
            "spy_sign_flips_across_regime": (spy_regime or {}).get(
                "sign_flips_across_regime"),
            "high_vol_rank_ic": (vol_regime or {}).get("high", {}).get("rank_ic")
            if vol_regime else None,
            "low_vol_rank_ic": (vol_regime or {}).get("low", {}).get("rank_ic")
            if vol_regime else None,
            "vol_sign_flips_across_regime": (vol_regime or {}).get(
                "sign_flips_across_regime"),
        },
        "leave_one_year_out_sign_stable": loyo["sign_stable"],
        "years_present": loyo["years_present"],
    }


def _empty_pair_cell(h: int) -> Dict[str, Any]:
    """Not-computed metric stub for a pre-registered pair (snapshot / no-data path)."""
    return {
        "horizon_days": h,
        "n_dates": 0,
        "n_tickers": 0,
        "n_obs": 0,
        "effective_obs": 0.0,
        "enough_data": False,
        "mean_residual_rank_ic": None,
        "median_residual_rank_ic": None,
        "std_residual_rank_ic": None,
        "information_ratio": None,
        "direction": "flat",
        "n_dates_same_sign_as_reference": 0,
        "frac_dates_same_sign_as_reference": None,
        "bootstrap_mean_ic_ci": {
            "method": "moving_block",
            "block_len_sessions": h,
            "n_resamples": BOOTSTRAP_N,
            "alpha": CI_ALPHA,
            "ci_low": None,
            "ci_high": None,
            "excludes_zero": False,
        },
        "top_minus_bottom_residual_spread": None,
        "top_decile_residual_hit_rate": None,
        "concentration": {"top_quintile_ticker_concentration": None,
                          "top3_long_leg_share": None,
                          "n_unique_top_quintile_tickers": 0, "top_quintile_rows": 0},
        "regime_summary": {
            "spy_up_rank_ic": None, "spy_down_rank_ic": None,
            "spy_sign_flips_across_regime": None, "high_vol_rank_ic": None,
            "low_vol_rank_ic": None, "vol_sign_flips_across_regime": None},
        "leave_one_year_out_sign_stable": False,
        "years_present": 0,
    }


def recommend_pair(cell: Dict[str, Any], computed: bool) -> Dict[str, Any]:
    """Conservative per-pair recommendation from the narrow-retest metrics.

    KEEP_FOR_CONFIRMATION_DESIGN only when the mean residual IC is positive and at or above
    the floor (or convincingly close with strong supporting diagnostics), a majority of dates
    share the reference sign, the bootstrap CI lower bound clears zero, the IC sign is
    year-stable, there is no regime sign dependence, no severe concentration, and enough
    observations remain. RESEARCH_LEAD_RECONFIRMED when the positive, above-zero, year-stable
    edge is reproduced but the IC is still sub-floor (the expected dominant blocker).
    DROP_AFTER_NARROW_RETEST when the edge weakens / flips sign, the CI does not clear zero,
    the sign is year-unstable, or a regime / concentration robustness gate fails.
    NEED_POINT_IN_TIME_UNIVERSE when the retest was not executed or too little usable data
    remains (the survivorship / current-membership caveat is the main blocker). The
    survivorship caveat is always carried forward; a KEEP is NOT a production edge.
    """
    enough = bool(cell.get("enough_data"))
    mean_ic = cell.get("mean_residual_rank_ic")
    frac = cell.get("frac_dates_same_sign_as_reference")
    info_ratio = cell.get("information_ratio")
    ci = cell.get("bootstrap_mean_ic_ci", {}) or {}
    ci_low = ci.get("ci_low")
    ci_high = ci.get("ci_high")
    top3 = (cell.get("concentration", {}) or {}).get("top3_long_leg_share")
    regime = cell.get("regime_summary", {}) or {}
    loyo_stable = bool(cell.get("leave_one_year_out_sign_stable"))

    ic_positive = bool(mean_ic is not None and mean_ic > 0)
    ic_above_floor = bool(mean_ic is not None and mean_ic >= RANK_IC_FLOOR)
    ci_above_zero = bool(ci_low is not None and ci_high is not None
                         and ci_low > 0 and ci_high > 0)
    majority_same_sign = bool(frac is not None and frac > MAJORITY_SIGN_FRACTION)
    regime_dependent = bool(regime.get("spy_sign_flips_across_regime")
                            or regime.get("vol_sign_flips_across_regime"))
    concentration_ok = bool(top3 is None or top3 <= CONCENTRATION_TOP3_THRESHOLD)
    # "Convincingly close" to the floor only with strong supporting diagnostics.
    near_floor_with_support = bool(
        mean_ic is not None and RANK_IC_NEAR_FLOOR <= mean_ic < RANK_IC_FLOOR
        and ci_above_zero
        and frac is not None and frac >= SUPPORT_SIGN_FRACTION
        and info_ratio is not None and info_ratio >= SUPPORT_INFO_RATIO
        and loyo_stable and not regime_dependent and concentration_ok)
    ic_meets_floor_or_close = bool(ic_above_floor or near_floor_with_support)

    gates = {
        "enough_data": enough,
        "mean_residual_ic_positive": ic_positive,
        "mean_residual_ic_at_or_above_floor_or_close": ic_meets_floor_or_close,
        "majority_dates_same_sign": majority_same_sign,
        "bootstrap_ci_above_zero": ci_above_zero,
        "no_single_year_drives_result": loyo_stable,
        "no_regime_only_dependence": not regime_dependent,
        "no_excessive_ticker_concentration": concentration_ok,
    }
    keep_gates = [k for k, v in gates.items() if k != "enough_data" and not v]

    if not computed or not enough:
        status = NEED_PIT
        reason = (
            "the narrow retest was not executed against the D: panel in this snapshot, or "
            "too few effective observations remained after the horizon embargo to draw a "
            "conclusion on the survivorship-caveated panel; run the analyzer where the D: "
            "dataset is mounted to populate the data-driven verdict")
    elif (ic_positive and ci_above_zero and loyo_stable and not regime_dependent
          and concentration_ok and majority_same_sign and ic_meets_floor_or_close):
        status = KEEP
        reason = (
            "the narrow model-free retest reproduced a positive residual rank IC that is at "
            "or convincingly close to the confirmation floor, majority-sign-consistent, "
            "materially above zero by bootstrap, year- and regime-stable, and not "
            "over-concentrated; it earns a later, separately gated confirmation-design phase "
            "(still survivorship-biased and NOT a production edge)")
    elif ic_positive and ci_above_zero and loyo_stable and not regime_dependent \
            and concentration_ok:
        status = RECONFIRMED
        reason = (
            "the narrow retest reproduced a positive, above-zero, year-stable residual rank "
            "IC, but it is still below the confirmation floor (and/or short of the same-sign "
            "majority); the lead is reconfirmed as a research lead but is not confirmable "
            "yet and is not a model candidate (failed gate(s): "
            + (", ".join(keep_gates) if keep_gates else "sub-floor information coefficient")
            + ")")
    else:
        status = DROP
        reason = (
            "the narrow retest did not hold up: the residual edge is non-positive, the "
            "bootstrap CI does not clear zero, the sign is year-unstable, or a regime / "
            "concentration robustness gate failed (failed gate(s): "
            + (", ".join(keep_gates) if keep_gates else "directional / stability failure")
            + "); dropped after the narrow retest")
    return {"status": status, "gates": gates, "failed_keep_gates": keep_gates,
            "reason": reason}


# --------------------------------------------------------------------------- #
# Static (no-D:-data) descriptive blocks
# --------------------------------------------------------------------------- #
def build_pre_registered_candidate_set() -> List[Dict[str, Any]]:
    return [
        {
            "lead_id": _lead_id(p["candidate"], p["horizon_days"]),
            "candidate": p["candidate"],
            "name": p["name"],
            "feature": p["feature"],
            "category": p["category"],
            "reference_sign": p["reference_sign"],
            "horizon_days": p["horizon_days"],
            "standalone_alpha": True,
            "production_allowed": False,
        }
        for p in PRE_REGISTERED_PAIRS
    ]


def build_explicitly_excluded_candidates() -> List[Dict[str, Any]]:
    out = [dict(e) for e in EXCLUDED_PAIRS]
    out.append({
        "lead_id": "all_other_candidates_and_horizons",
        "candidate": "ALL_OTHERS",
        "horizon_days": None,
        "role": "out_of_scope",
        "reason": (
            "Phase 2K-N is a narrow, pre-registered retest of exactly 3 candidate / horizon "
            "pairs; no other candidate or horizon is retested and the candidate set is never "
            "expanded."),
    })
    return out


def build_feature_engineering_summary(price_path: str) -> Dict[str, Any]:
    return {
        "source_csv": _norm(price_path),
        "source_used": "expanded_price_history_csv",
        "signal_features": ["momentum_12_1", "reversal_5d", "reversal_21d"],
        "trailing_return_windows": ["return_5d", "return_21d", "return_63d", "return_252d"],
        "control_features": list(CONTROL_FEATURES),
        "diagnostic_only_features": [STOPPED_CANDIDATE],
        "benchmark_feature": "spy_return",
        "all_features_trailing_point_in_time": True,
        "labels_forward_filled": False,
        "forward_label_base": "forward_excess_return_vs_spy",
        "horizons": list(HORIZONS),
        "rows_loaded": 0,
        "n_tickers": 0,
        "n_dates": 0,
        "computed": False,
        "note": None,
    }


def build_residualization_config() -> Dict[str, Any]:
    return {
        "label_base": "forward_excess_return_vs_spy",
        "horizons": list(HORIZONS),
        "controls": list(CONTROL_FEATURES),
        "method": ("per-date cross-sectional ordinary-least-squares residual (usable "
                   "controls + intercept) via numpy.linalg.lstsq; residual = forward excess "
                   "label - fitted values (same construction as Phase 2K-B / 2K-C / 2L-B / "
                   "2K-I / 2K-K). This is a per-date linear projection, not a trained, "
                   "persisted, or scored model."),
        "robust_fallback": ("cross-sectional demean when no control is usable, a control is "
                            "constant, or the date has fewer than min_names usable names"),
        "min_names_for_ols": RESID_MIN_NAMES,
        "no_lookahead": ("controls are trailing point-in-time features; the label is strictly "
                         "forward; each per-date projection uses no forward information"),
        "residual_label_columns": {str(h): f"resid_{h}d" for h in HORIZONS},
        "fit_counts": {},
    }


def build_gate_summary(rec_counts: Dict[str, int]) -> Dict[str, Any]:
    return {
        "keep_gate_definitions": {
            "mean_residual_ic_positive": "mean residual rank IC > 0",
            "mean_residual_ic_at_or_above_floor_or_close": (
                f"mean residual rank IC >= {RANK_IC_FLOOR}, or >= {RANK_IC_NEAR_FLOOR} with "
                "strong supporting diagnostics (CI above zero, same-sign fraction >= "
                f"{SUPPORT_SIGN_FRACTION}, information ratio >= {SUPPORT_INFO_RATIO}, "
                "year- and regime-stable, not over-concentrated)"),
            "majority_dates_same_sign": (
                f"fraction of dates same sign as reference > {MAJORITY_SIGN_FRACTION}"),
            "bootstrap_ci_above_zero": "moving-block 95% CI lower bound > 0",
            "no_single_year_drives_result": "leave-one-year-out sign stable",
            "no_regime_only_dependence": (
                "no sign flip across SPY up/down or market-vol split"),
            "no_excessive_ticker_concentration": (
                f"top-3 long-leg share <= {CONCENTRATION_TOP3_THRESHOLD}"),
            "enough_data": (
                f">= {MIN_IC_DATES} IC dates and >= {MIN_EFFECTIVE_OBS} effective obs"),
        },
        "pair_recommendation_counts": rec_counts,
        "allowed_pair_recommendations": list(_ALLOWED_PAIR_RECS),
    }


# --------------------------------------------------------------------------- #
# Overall recommendation + routing + interpretation
# --------------------------------------------------------------------------- #
def build_recommendation(rec_counts: Dict[str, int], computed: bool) -> Dict[str, Any]:
    n_keep = rec_counts.get(KEEP, 0)
    n_reconf = rec_counts.get(RECONFIRMED, 0)
    n_drop = rec_counts.get(DROP, 0)
    n_need = rec_counts.get(NEED_PIT, 0)
    n_decided = n_keep + n_reconf + n_drop

    if not computed or n_decided == 0:
        rec = REC_NEED_PIT
        reason = (
            "The narrow model-free retest was not executed against the D: panel in this "
            "snapshot (or too little usable data remained to conclude). Run the analyzer on "
            "the host where the D: dataset is mounted to populate the retest metrics and the "
            "data-driven recommendation; until then no lead proceeds to confirmation design, "
            "no model candidate is created, and no production edge is claimed.")
    elif n_keep > 0:
        rec = REC_PROCEED
        reason = (
            f"{n_keep} pre-registered lead(s) reproduced a positive, above-zero, year- and "
            "regime-stable residual edge at or convincingly close to the confirmation floor "
            "and earn a stricter, separately gated confirmation-design phase. The result is "
            "still survivorship-biased and is NOT a production edge and NOT permission to "
            "train or serve a model.")
    elif n_reconf > 0:
        rec = REC_RECONFIRMED
        reason = (
            f"No lead cleared the confirmation bar, but {n_reconf} reproduced a positive, "
            "above-zero, year-stable but sub-floor residual edge; they are reconfirmed as "
            "research leads that are not confirmable yet and need sector-relative variants, "
            "better data, or backlog refresh. No model candidate is created.")
    elif n_drop > 0 and n_need == 0:
        rec = REC_DROP
        reason = (
            "Every narrow-retest lead failed to reproduce a confirmable edge on the expanded "
            "survivorship-caveated panel; stop these leads and refresh the alpha backlog "
            "rather than escalate. No model candidate is created.")
    else:
        rec = REC_NEED_PIT
        reason = (
            "The survivorship / current-membership caveat is the main blocker to a "
            "conclusion for the narrow-retest leads; a point-in-time membership universe is "
            "needed before any further confirmation work. No model candidate is created.")
    return {
        "recommendation": rec,
        "allowed_values": list(_ALLOWED_RECOMMENDATIONS),
        "n_keep_for_confirmation_design": n_keep,
        "n_research_lead_reconfirmed": n_reconf,
        "n_drop_after_narrow_retest": n_drop,
        "n_need_point_in_time_universe": n_need,
        "create_model_candidate_now": False,
        "train_model_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "results_are_survivorship_biased": True,
        "reason": reason,
    }


def build_recommended_next_phase(recommendation: str) -> Dict[str, str]:
    if recommendation == REC_PROCEED:
        return {
            "phase": "2K-O",
            "title": TITLE_PROCEED,
            "purpose": (
                "Design a stricter walk-forward confirmation phase for the surviving "
                "model-free signal(s), still with no model training and no deployment. "
                "Results remain survivorship-biased and not a production edge."),
        }
    if recommendation == REC_RECONFIRMED:
        return {
            "phase": "2K-O",
            "title": TITLE_RECONFIRMED,
            "purpose": (
                "Decide whether the sub-floor but reproducible leads deserve sector-relative "
                "variants, better data, or a backlog refresh, before any confirmation work. "
                "Still no model training and no model candidate."),
        }
    if recommendation == REC_DROP:
        return {
            "phase": "2K-O",
            "title": TITLE_DROP,
            "purpose": (
                "Stop the narrow-retest leads and refresh the alpha backlog again after the "
                "retest failed to reproduce a confirmable edge, redirecting toward orthogonal "
                "factor families. Still no model training and no model candidate."),
        }
    return {
        "phase": "2K-O",
        "title": TITLE_NEED_PIT,
        "purpose": (
            "Decide whether proper point-in-time membership data is required before any "
            "further confirmation work, because the current panel is survivorship-caveated / "
            "current-as-of only or too little usable data remains to conclude. Still no model "
            "training and no model candidate."),
    }


def build_interpretation(recommendation: Dict[str, Any], computed: bool) -> Dict[str, Any]:
    rec = recommendation["recommendation"]
    if not computed:
        verdict = ("was not executed against the D: panel in this snapshot; the metrics and "
                   "data-driven verdict are populated when the analyzer is run")
    elif rec == REC_PROCEED:
        verdict = ("reproduced a confirmable model-free edge on at least one lead that earns "
                   "a stricter confirmation-design phase")
    elif rec == REC_RECONFIRMED:
        verdict = ("reconfirmed a positive but sub-floor research-lead edge that is not "
                   "confirmable yet")
    elif rec == REC_DROP:
        verdict = ("failed to reproduce a confirmable edge and routes to an alpha-backlog "
                   "refresh")
    else:
        verdict = ("could not draw a conclusion from the available survivorship-caveated "
                   "data and needs a point-in-time universe decision")
    return {
        "model_trained": False,
        "model_fitted": False,
        "model_candidate_created": False,
        "authorized_to_train_model": False,
        "authorized_to_serve_model": False,
        "ran_broad_alpha_screen": False,
        "candidate_set_expanded": False,
        "production_edge_claimed": False,
        "results_are_survivorship_biased": True,
        "narrative": (
            "Phase 2K-N ran a narrow, pre-registered, model-free retest of ONLY the 3 Phase "
            "2K-M NARROW_RETEST_CANDIDATE pairs (residual_price_momentum_12_1 @ 5d, "
            "short_horizon_residual_reversal_5d @ 21d, short_horizon_residual_reversal_21d @ "
            "21d) on the expanded, survivorship-caveated D: panel, each at its own horizon. "
            "It recomputed the trailing signals and the beta/vol-neutralized residual "
            "forward-excess labels and measured only model-free, rank-only metrics -- nothing "
            "was fitted, no broad alpha screen was run, the candidate set was not expanded, "
            "the two HOLD_FOR_SECTOR_RELATIVE_VARIANT leads were not retested, and "
            "avg_dollar_volume_21d stayed excluded as a standalone alpha. The retest "
            + verdict + ". Every result is reported as survivorship-biased / "
            "current-membership caveated. No model was trained, fitted, scored, or served, "
            "and no model candidate was created; even a KEEP only permits a later, separately "
            "gated confirmation-design phase and is not a production edge."),
    }


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_results(*, data_root: Optional[str] = None,
                  phase2k_m_path: Optional[str] = None,
                  phase2k_l_path: Optional[str] = None,
                  phase2k_k_path: Optional[str] = None,
                  phase2k_h_path: Optional[str] = None,
                  price_history_path: Optional[str] = None) -> Dict[str, Any]:
    data_root = data_root or DATA_ROOT
    phase2k_m_path = phase2k_m_path or PHASE2K_M_JSON
    phase2k_l_path = phase2k_l_path or PHASE2K_L_JSON
    phase2k_k_path = phase2k_k_path or PHASE2K_K_JSON
    phase2k_h_path = phase2k_h_path or PHASE2K_H_JSON
    paths = _resolve_d_paths(data_root)
    price_path = price_history_path or paths["price_history_csv"]

    km = _safe_read_json(phase2k_m_path)
    kl = _safe_read_json(phase2k_l_path)
    kk = _safe_read_json(phase2k_k_path)
    km_summary = summarize_phase2k_m(km)
    km_summary["phase2k_l_recommendation"] = (
        (kl or {}).get("recommendation", {}) or {}).get("recommendation") if kl else None
    km_summary["phase2k_k_recommendation"] = (
        (kk or {}).get("recommendation", {}) or {}).get("recommendation") if kk else None
    prior = phase2k_m_prior_metrics(km)

    kh_summary = summarize_phase2k_h(_safe_read_json(phase2k_h_path))
    dq = _safe_read_json(paths["data_quality_report_json"])
    sc = _safe_read_json(paths["survivorship_caveat_json"])
    dq_sum = summarize_data_quality(dq)
    sc_sum = summarize_survivorship(sc)

    upstream_ready = bool(km_summary.get("routing_confirmed")
                          and kh_summary.get("ready_for_screen"))
    dq_ok = bool(dq_sum.get("passed"))
    caveat_ok = bool(sc_sum.get("present")
                     and sc_sum.get("point_in_time_membership_claimed") is False)

    feature_summary = build_feature_engineering_summary(price_path)
    residualization_config = build_residualization_config()

    narrow_retest_results: List[Dict[str, Any]] = []
    pair_recommendations: List[Dict[str, Any]] = []
    rec_counts = {KEEP: 0, RECONFIRMED: 0, DROP: 0, NEED_PIT: 0}

    can_compute = bool(upstream_ready and dq_ok and caveat_ok
                       and os.path.isfile(price_path))
    computed = False

    if can_compute:
        try:
            prices = _read_price_history(price_path)
        except (OSError, ValueError):
            prices = pd.DataFrame()
        if len(prices) and prices[TICKER_COL].nunique() >= MIN_NAMES_PER_DATE + 1:
            feats = compute_features(prices)
            labels = build_forward_excess_labels(prices)
            signal_cols = ["momentum_12_1", "reversal_5d", "reversal_21d"]
            feat_cols = [TICKER_COL, DATE_COL] + signal_cols + CONTROL_FEATURES
            panel = feats[feat_cols].merge(labels, on=[TICKER_COL, DATE_COL], how="inner")

            fit_counts: Dict[str, Any] = {}
            for h in HORIZONS:
                res = residualize_label(panel, f"excess_{h}d", CONTROL_FEATURES)
                panel[f"resid_{h}d"] = res["resid"]
                fit_counts[str(h)] = {"ols_dates": res["ols_dates"],
                                      "fallback_dates": res["fallback_dates"]}
            residualization_config["fit_counts"] = fit_counts

            feature_summary.update({
                "rows_loaded": int(len(prices)),
                "n_tickers": int(panel[TICKER_COL].nunique()),
                "n_dates": int(panel[DATE_COL].nunique()),
                "computed": True,
            })

            spy_fwd_by_h = {h: spy_forward_return(prices, h) for h in HORIZONS}
            market_vol = build_market_vol(feats)

            for p in PRE_REGISTERED_PAIRS:
                h = p["horizon_days"]
                lid = _lead_id(p["candidate"], h)
                cell = retest_pair(panel, p["feature"], h, p["reference_sign"],
                                   spy_fwd_by_h[h], market_vol)
                rec = recommend_pair(cell, True)
                cell["lead_id"] = lid
                cell["candidate"] = p["candidate"]
                cell["feature"] = p["feature"]
                cell["category"] = p["category"]
                cell["prior_phase2k_m"] = prior.get(lid, {})
                cell["recommendation"] = rec
                narrow_retest_results.append(cell)
                rec_counts[rec["status"]] = rec_counts.get(rec["status"], 0) + 1
                pair_recommendations.append({
                    "lead_id": lid,
                    "candidate": p["candidate"],
                    "feature": p["feature"],
                    "category": p["category"],
                    "horizon_days": h,
                    "status": rec["status"],
                    "mean_residual_rank_ic": cell["mean_residual_rank_ic"],
                    "frac_dates_same_sign_as_reference":
                        cell["frac_dates_same_sign_as_reference"],
                    "bootstrap_ci_low": cell["bootstrap_mean_ic_ci"]["ci_low"],
                    "bootstrap_ci_high": cell["bootstrap_mean_ic_ci"]["ci_high"],
                    "leave_one_year_out_sign_stable": cell["leave_one_year_out_sign_stable"],
                    "failed_keep_gates": rec["failed_keep_gates"],
                    "enough_data": cell["enough_data"],
                    "prior_phase2k_m_mean_ic": prior.get(lid, {}).get(
                        "prior_mean_residual_rank_ic"),
                    "prior_phase2k_l_diagnostic_score": prior.get(lid, {}).get(
                        "prior_diagnostic_score"),
                    "reason": rec["reason"],
                })
            computed = True
        else:
            feature_summary["note"] = ("expanded price history present but too few tickers / "
                                       "rows to retest")
    else:
        feature_summary["note"] = ("narrow retest not computed: upstream routing not "
                                   "confirmed, build / data quality not ready, or expanded "
                                   "price history absent")

    # Snapshot / no-data path: emit a complete, null-metric structure so the artifact is
    # schema-valid even when the heavy D: compute was not run.
    if not computed:
        for p in PRE_REGISTERED_PAIRS:
            h = p["horizon_days"]
            lid = _lead_id(p["candidate"], h)
            cell = _empty_pair_cell(h)
            rec = recommend_pair(cell, False)
            cell["lead_id"] = lid
            cell["candidate"] = p["candidate"]
            cell["feature"] = p["feature"]
            cell["category"] = p["category"]
            cell["prior_phase2k_m"] = prior.get(lid, {})
            cell["recommendation"] = rec
            narrow_retest_results.append(cell)
            rec_counts[NEED_PIT] += 1
            pair_recommendations.append({
                "lead_id": lid,
                "candidate": p["candidate"],
                "feature": p["feature"],
                "category": p["category"],
                "horizon_days": h,
                "status": NEED_PIT,
                "mean_residual_rank_ic": None,
                "frac_dates_same_sign_as_reference": None,
                "bootstrap_ci_low": None,
                "bootstrap_ci_high": None,
                "leave_one_year_out_sign_stable": False,
                "failed_keep_gates": [],
                "enough_data": False,
                "prior_phase2k_m_mean_ic": prior.get(lid, {}).get(
                    "prior_mean_residual_rank_ic"),
                "prior_phase2k_l_diagnostic_score": prior.get(lid, {}).get(
                    "prior_diagnostic_score"),
                "reason": rec["reason"],
            })

    recommendation = build_recommendation(rec_counts, computed)
    recommended_next_phase = build_recommended_next_phase(recommendation["recommendation"])
    interpretation = build_interpretation(recommendation, computed)

    survivorship_summary = dict(sc_sum)
    survivorship_summary["carried_forward_from"] = ["phase2k_k", "phase2k_l", "phase2k_m"]
    survivorship_summary["note"] = (
        "Phase 2K-N carries the survivorship / current-membership caveat forward unchanged; "
        "no point-in-time universe is claimed and no narrow-retest lead is a production edge.")

    return {
        "phase": PHASE,
        "generated_at": _now(),
        "inputs_read": {
            "phase2k_m_json": _norm(phase2k_m_path),
            "phase2k_l_json": _norm(phase2k_l_path),
            "phase2k_k_json": _norm(phase2k_k_path),
            "phase2k_h_summary_json": _norm(phase2k_h_path),
            "expanded_price_history_csv": _norm(price_path),
            "data_quality_report_json": _norm(paths["data_quality_report_json"]),
            "data_build_summary_json": _norm(paths["data_build_summary_json"]),
            "survivorship_caveat_json": _norm(paths["survivorship_caveat_json"]),
        },
        "data_root": _norm(data_root),
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
        "model_trained": False,
        "model_candidate_created": False,
        "ran_broad_alpha_screen": False,
        "candidate_set_expanded": False,
        "d_drive_written": False,
        "retest_executed": computed,
        "upstream_phase2k_m_summary": km_summary,
        "data_quality_summary": dq_sum,
        "survivorship_caveat_summary": survivorship_summary,
        "pre_registered_candidate_set": build_pre_registered_candidate_set(),
        "explicitly_excluded_candidates": build_explicitly_excluded_candidates(),
        "feature_engineering_summary": feature_summary,
        "residualization_config": residualization_config,
        "narrow_retest_results": narrow_retest_results,
        "pair_recommendations": pair_recommendations,
        "gate_summary": build_gate_summary(rec_counts),
        "overall_recommendation": recommendation,
        "interpretation": interpretation,
        "recommended_next_phase": recommended_next_phase,
    }


def write_results(results: Dict[str, Any], path: str = RESULTS_JSON) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, allow_nan=False)


def run(output_path: str = RESULTS_JSON, **kwargs) -> Dict[str, Any]:
    results = build_results(**kwargs)
    write_results(results, output_path)
    return results


def main() -> int:
    t0 = time.perf_counter()
    d = run()
    elapsed = time.perf_counter() - t0
    rec = d["overall_recommendation"]
    nxt = d["recommended_next_phase"]
    km = d["upstream_phase2k_m_summary"]
    print(f"[phase2k-n] upstream 2K-M routing confirmed : {km.get('routing_confirmed')}")
    print(f"[phase2k-n] retest executed                : {d['retest_executed']}")
    print(f"[phase2k-n] pre-registered pairs            : "
          f"{[c['lead_id'] for c in d['pre_registered_candidate_set']]}")
    print(f"[phase2k-n] pair recommendation counts      : "
          f"{d['gate_summary']['pair_recommendation_counts']}")
    print(f"[phase2k-n] recommendation                  : {rec['recommendation']}")
    print(f"[phase2k-n] recommended next phase           : {nxt['phase']} ({nxt['title']})")
    print(f"[phase2k-n] results written                 : {RESULTS_JSON}")
    print(f"[phase2k-n] elapsed seconds                  : {elapsed:.2f}")
    print("[phase2k-n] read-only; narrow pre-registered retest; no model trained; "
          "no D: writes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
