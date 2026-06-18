"""Phase 2K-I Expanded-Dataset Residual Signal Retest (offline, read-only).

Phase 2K-H produced the manual, free, survivorship-caveated expanded price/volume
panel on the D: data root (D:\\Stock_Prediction_app_data\\phase2k_g\\output) and the
2K-H tracking analyzer confirmed it was ready for retest. Phase 2K-I REPLAYS the
model-free residual-signal research that Phase 2K-B / 2K-C / 2L-B ran on the thin
~40-name 2023-2026 export, but now on the larger, longer, survivorship-caveated panel,
and decides whether the expanded data supports continuing to a later, separately gated
walk-forward / model-candidate DESIGN phase.

It does NOT train, fit, or score any model and creates no model candidate. For the one
Phase 2K-C survivor -- avg_dollar_volume_21d -- it:

  1. Reads the Phase 2K-H summary and confirms the manual build is present and ready
     (manual_build_detected, ready_for_retest, recommended 2K-I, data-quality PASS /
     PASS_WITH_CAVEAT, point_in_time_membership_claimed == false, the D: data root).
  2. Reads the D: expanded price-history CSV (read-only, with pandas) and COMPUTES the
     trailing point-in-time features and risk controls the prior phases consumed from a
     richer scored CSV: dollar_volume, avg_dollar_volume_21d, daily_return,
     rolling_beta_63d, realized_vol_21d / 63d, downside_vol_21d, rolling_corr_spy_63d,
     plus a market-level SPY realized-vol regime scalar. Every feature is a trailing
     window; nothing uses forward information.
  3. Builds the SAME beta/vol-neutralized residual label (forward excess-vs-SPY return,
     per-date cross-sectional OLS on the trailing controls, demean fallback) for the
     21d and 63d horizons. The label is strictly forward; the controls are trailing; the
     per-date fit uses no forward information.
  4. Re-runs the model-free residual rank-IC battery on the expanded panel: per-date
     residual rank IC, mean / median, information ratio, moving-block bootstrap CI,
     top-minus-bottom spread, top-decile hit rate, top-quintile concentration,
     leave-one-year-out stability, SPY up/down and market-vol regime splits, and a
     degradation check versus the Phase 2K-C full-sample IC.
  5. Runs a sequential, embargoed, model-free walk-forward (min train 252, validation
     63, step 63, embargo 63) and measures validation residual IC per fold -- rank-only,
     nothing fitted.
  6. Issues a conservative verdict -- EXPANDED_RETEST_PASS / EXPANDED_RETEST_FAIL /
     NEED_MORE_DATA_OR_PIT_UNIVERSE -- and routes to Phase 2K-J. Every result is reported
     as survivorship-biased / current-membership caveated; even a pass is NOT a
     production edge and NOT permission to train or serve a model.

This phase reads the Phase 2K-H summary plus the Phase 2K-C / 2K-B / 2L-B JSON summaries
and the D: expanded CSVs / JSONs, and writes exactly one small results JSON in the C:
repo. It never writes to the D: drive, performs no infrastructure action, and mutates no
datastore; the machine-readable safety flags are emitted in the results JSON, and the
full guardrail rationale lives in docs/phase2k_i_expanded_residual_retest_v1.md, not in
this source.
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

PHASE = "2K-I"

_OUTPUT_DIR = os.path.join("research", "output")

# Read-only upstream summaries (small, Git-tracked, in the C: repo).
UPSTREAM_2KH_JSON = os.path.join(_OUTPUT_DIR, "phase2k_h_manual_build_run_summary.json")
INPUT_2KC_JSON = os.path.join(_OUTPUT_DIR, "phase2k_c_residual_robustness.json")
INPUT_2KB_JSON = os.path.join(_OUTPUT_DIR, "phase2k_b_residual_signal.json")
INPUT_2LB_JSON = os.path.join(_OUTPUT_DIR, "phase2l_b_walk_forward_validation.json")

# External data root on the D: drive (mirrors the Phase 2K-G builder DEFAULT_DATA_ROOT).
# The manual 2K-H build wrote the large outputs here, never under the C: repo.
DATA_ROOT = r"D:\Stock_Prediction_app_data\phase2k_g"

PRICE_HISTORY_FILENAME = "phase2k_g_expanded_price_history_free.csv"
SCORED_FILENAME = "phase2k_g_expanded_scored_free.csv"
DATA_QUALITY_FILENAME = "phase2k_g_data_quality_report.json"
BUILD_SUMMARY_FILENAME = "phase2k_g_data_build_summary.json"
SURVIVORSHIP_FILENAME = "phase2k_g_survivorship_caveat.json"

_D_OUTPUT_DIR = os.path.join(DATA_ROOT, "output")
PRICE_HISTORY_CSV = os.path.join(_D_OUTPUT_DIR, PRICE_HISTORY_FILENAME)
SCORED_CSV = os.path.join(_D_OUTPUT_DIR, SCORED_FILENAME)
DATA_QUALITY_JSON = os.path.join(_D_OUTPUT_DIR, DATA_QUALITY_FILENAME)
BUILD_SUMMARY_JSON = os.path.join(_D_OUTPUT_DIR, BUILD_SUMMARY_FILENAME)
SURVIVORSHIP_JSON = os.path.join(_D_OUTPUT_DIR, SURVIVORSHIP_FILENAME)

# The single small output this analyzer writes (stays in the C: repo, Git-tracked).
RESULTS_JSON = os.path.join(_OUTPUT_DIR, "phase2k_i_expanded_residual_retest.json")

DATE_COL = "date"
TICKER_COL = "ticker"
BENCHMARK = "SPY"

# The single required candidate (the lone Phase 2K-C robustness survivor).
CANDIDATE = "avg_dollar_volume_21d"

# Trailing risk controls regressed out to form the residual label (identical set to
# Phase 2K-B / 2K-C / 2L-B).
CONTROL_FEATURES: List[str] = [
    "rolling_beta_63d",
    "realized_vol_21d",
    "realized_vol_63d",
    "downside_vol_21d",
    "rolling_corr_spy_63d",
]

# Forward residual-label horizons. 63 is primary; 21 is the optional comparison.
HORIZONS: List[int] = [21, 63]
PRIMARY_HORIZON = 63

# Metric / gate thresholds (inherited from Phase 2I-A / 2K-B / 2K-C / 2L-B; not tuned).
RANK_IC_FLOOR = 0.03
MAJORITY_SIGN_FRACTION = 0.50
CONCENTRATION_TOP3_THRESHOLD = 0.50
DEGRADATION_TOLERANCE_FRACTION = 0.50
MIN_EFFECTIVE_OBS = 8.0
MIN_FOLDS = 3

MIN_NAMES_PER_DATE = 5
MIN_NAMES_DECILE = 10
RESID_MIN_NAMES = 10

# Walk-forward protocol (sessions ~= trading days).
MIN_TRAIN_SESSIONS = 252
VALIDATION_SESSIONS = 63
STEP_SESSIONS = 63

# Moving-block bootstrap knobs (same family as Phase 2I-B / 2K-C / 2L-B).
BOOTSTRAP_N = 1000
CI_ALPHA = 0.05
SEED = 12345

# Recommendation vocabulary (the only values the verdict may emit).
REC_PASS = "EXPANDED_RETEST_PASS"
REC_FAIL = "EXPANDED_RETEST_FAIL"
REC_NEED = "NEED_MORE_DATA_OR_PIT_UNIVERSE"
_ALLOWED_RECOMMENDATIONS = [REC_PASS, REC_FAIL, REC_NEED]


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
    return round(float(x), n)


def _sign(x: Optional[float]) -> int:
    if x is None or pd.isna(x) or x == 0:
        return 0
    return 1 if x > 0 else -1


def _iso(ts: Any) -> Optional[str]:
    if ts is None or (isinstance(ts, float) and pd.isna(ts)):
        return None
    try:
        return pd.Timestamp(ts).date().isoformat()
    except (ValueError, TypeError):
        return None


def _safe_read_json(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return None


def _resolve_paths(data_root: str) -> Dict[str, str]:
    out = os.path.join(data_root, "output")
    return {
        "output_dir": out,
        "price_history_csv": os.path.join(out, PRICE_HISTORY_FILENAME),
        "scored_csv": os.path.join(out, SCORED_FILENAME),
        "data_quality_report_json": os.path.join(out, DATA_QUALITY_FILENAME),
        "data_build_summary_json": os.path.join(out, BUILD_SUMMARY_FILENAME),
        "survivorship_caveat_json": os.path.join(out, SURVIVORSHIP_FILENAME),
    }


# --------------------------------------------------------------------------- #
# Upstream-readiness summary (Phase 2K-H)
# --------------------------------------------------------------------------- #
def summarize_upstream_2kh(data: Optional[Dict[str, Any]],
                           path: str) -> Dict[str, Any]:
    """Read-only digest of the Phase 2K-H summary plus the readiness checks."""
    if data is None:
        return {
            "present": False,
            "path": _norm(path),
            "manual_build_detected": False,
            "ready_for_retest": False,
            "recommended_next_phase": None,
            "data_quality_status": None,
            "point_in_time_membership_claimed": None,
            "data_root": None,
            "ready_for_2k_i": False,
        }
    dec = data.get("decision", {}) or {}
    nxt = data.get("recommended_next_phase", {}) or {}
    dq = data.get("data_quality_summary", {}) or {}
    sc = data.get("survivorship_caveat_summary", {}) or {}
    status = dq.get("status")
    pit = sc.get("point_in_time_membership_claimed")
    ready_for_2k_i = bool(
        dec.get("manual_build_detected") is True
        and dec.get("ready_for_retest") is True
        and nxt.get("phase") == "2K-I"
        and status in ("PASS", "PASS_WITH_CAVEAT")
        and pit is False
    )
    return {
        "present": True,
        "path": _norm(path),
        "phase": data.get("phase"),
        "manual_build_detected": bool(dec.get("manual_build_detected")),
        "ready_for_retest": bool(dec.get("ready_for_retest")),
        "recommended_next_phase": nxt.get("phase"),
        "data_quality_status": status,
        "point_in_time_membership_claimed": pit,
        "data_root": data.get("data_root"),
        "ready_for_2k_i": ready_for_2k_i,
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


def phase2k_c_reference_ic(twokc: Optional[Dict[str, Any]]) -> Optional[float]:
    """The Phase 2K-C full-sample residual mean rank IC for the candidate."""
    if not twokc:
        return None
    for c in twokc.get("candidates", []):
        if c.get("feature") == CANDIDATE:
            return c.get("overall", {}).get("mean_rank_ic")
    return None


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
    """Guarantee `ticker` and `date` are ordinary columns (never stuck in the index).

    pandas `groupby(...).apply(...)` behavior changed across versions: the grouping key
    can be excluded from the applied frame and/or pushed into the index, which would make
    a later `df[[ticker, date, ...]]` selection raise ``KeyError: ['ticker'] not in
    index``. This normalizes the frame shape defensively: any of the two keys that ended
    up only in the index is reset back to a column, duplicate-named columns are collapsed
    (first occurrence kept), and a clean RangeIndex is restored. It is a no-op when the
    frame is already well-formed.
    """
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
    """Derive the candidate + trailing risk controls from adjusted OHLCV.

    Every column is a trailing (point-in-time) rolling statistic: it uses only data
    up to and including the as-of date, never forward information. Rolling windows are
    computed per ticker so no window bleeds across names.

    The returned frame always carries `ticker`, `date`, `adjusted_close`, `volume`,
    `dollar_volume`, `daily_return`, `spy_return`, `avg_dollar_volume_21d`,
    `rolling_beta_63d`, `realized_vol_21d`, `realized_vol_63d`, `downside_vol_21d`, and
    `rolling_corr_spy_63d` as ordinary columns.
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
        sub["avg_dollar_volume_21d"] = sub["dollar_volume"].rolling(21, min_periods=21).mean()
        sub["realized_vol_21d"] = r.rolling(21, min_periods=21).std()
        sub["realized_vol_63d"] = r.rolling(63, min_periods=63).std()
        sub["downside_vol_21d"] = r.clip(upper=0.0).rolling(21, min_periods=21).std()
        cov = r.rolling(63, min_periods=63).cov(m)
        var = m.rolling(63, min_periods=63).var()
        sub["rolling_beta_63d"] = cov / var.where(var > 0)
        sub["rolling_corr_spy_63d"] = r.rolling(63, min_periods=63).corr(m)
        return sub

    # Iterate groups explicitly and concat. Unlike `groupby(...).apply(...)`, group
    # iteration always yields the grouping column inside each sub-frame, so `ticker`
    # can never be silently dropped or pushed into the index regardless of the pandas
    # version. `_ensure_date_ticker_columns` is a final belt-and-suspenders guard.
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
    """Per (ticker, date) forward h-session excess-vs-SPY return for each horizon.

    Forward return is adjusted_close shifted -h within each ticker; SPY's own forward
    return over the same horizon is subtracted. Rows whose forward window runs past the
    data end become NaN and drop out downstream. Labels are never forward filled.
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
    cross-sectional demean. The fit uses no forward information, so there is no
    look-ahead.
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
        return {"per_year": [], "sign_stable": False}
    years = pd.Index(ics.index).year
    per_year: List[Dict[str, Any]] = []
    sign_stable = bool(reference_sign != 0)
    uniq = sorted(set(int(y) for y in years))
    if len(uniq) < 2:
        sign_stable = False
    for y in uniq:
        remaining = ics[years != y]
        m = float(remaining.mean()) if len(remaining) else None
        ok = bool(m is not None and reference_sign != 0 and _sign(m) == reference_sign)
        per_year.append({
            "year_left_out": int(y),
            "rank_ic_remaining": _round(m),
            "n_dates_remaining": int(len(remaining)),
            "sign_preserved": ok,
        })
        if not ok:
            sign_stable = False
    return {"per_year": per_year, "sign_stable": sign_stable, "years_present": len(uniq)}


# --------------------------------------------------------------------------- #
# Retest metrics per horizon
# --------------------------------------------------------------------------- #
def horizon_retest(panel: "pd.DataFrame", h: int, reference_sign: int,
                   ic_2kc: Optional[float]) -> Dict[str, Any]:
    lcol = f"resid_{h}d"
    sub = panel[[DATE_COL, TICKER_COL, CANDIDATE, lcol]].dropna(
        subset=[CANDIDATE, lcol]).copy()
    ics = per_date_rank_ic(sub[[DATE_COL, CANDIDATE, lcol]], CANDIDATE, lcol)
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
    spread, hit = spread_and_hit_rate(sub, CANDIDATE, lcol)
    conc = long_leg_concentration(sub, CANDIDATE, lcol)
    degr_floor = (DEGRADATION_TOLERANCE_FRACTION * ic_2kc
                  if isinstance(ic_2kc, (int, float)) else None)
    degraded = bool(mean_ic is not None and degr_floor is not None
                    and mean_ic < degr_floor)
    return {
        "horizon_days": h,
        "n_dates": n_dates,
        "n_rows": int(len(sub)),
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
        "degradation_vs_phase2k_c": {
            "phase2k_c_residual_mean_rank_ic": _round(ic_2kc),
            "degradation_tolerance_fraction": DEGRADATION_TOLERANCE_FRACTION,
            "degradation_floor": _round(degr_floor),
            "expanded_mean_residual_rank_ic": _round(mean_ic),
            "degraded": degraded,
        },
        "_ics": ics,  # internal; stripped before serialization
    }


# --------------------------------------------------------------------------- #
# Walk-forward (sequential, embargoed, rank-only; nothing fitted)
# --------------------------------------------------------------------------- #
def build_folds(calendar: List[Any], horizon: int, min_train: int,
                validation: int, step: int) -> List[Dict[str, Any]]:
    n = len(calendar)
    folds: List[Dict[str, Any]] = []
    fold_id = 0
    t_end = min_train
    while t_end + horizon + validation <= n:
        v_start = t_end + horizon
        v_end = v_start + validation
        folds.append({
            "fold_id": fold_id,
            "train_start_idx": 0,
            "train_end_idx": t_end,
            "embargo_start_idx": t_end,
            "embargo_end_idx": v_start,
            "embargo_sessions": horizon,
            "validation_start_idx": v_start,
            "validation_end_idx": v_end,
            "train_start": _iso(calendar[0]),
            "train_end": _iso(calendar[t_end - 1]),
            "embargo_start": _iso(calendar[t_end]),
            "embargo_end": _iso(calendar[v_start - 1]),
            "validation_start": _iso(calendar[v_start]),
            "validation_end": _iso(calendar[v_end - 1]),
            "n_train_dates": int(t_end),
            "n_validation_dates": int(v_end - v_start),
        })
        fold_id += 1
        t_end += step
    return folds


def empty_walk_forward_metrics() -> Dict[str, Any]:
    """Default walk-forward block for the not-ready / missing-input / too-little-data
    paths, where the heavy compute path is skipped.

    Carries every key that ``walk_forward`` produces and that ``build_interpretation``,
    ``evaluate_gates``, ``recommend`` and the output JSON read, so those paths never
    raise ``KeyError`` (e.g. ``pooled_validation_mean_residual_rank_ic``).
    """
    return {
        "config": {
            "candidate_signal": CANDIDATE,
            "primary_horizon_days": PRIMARY_HORIZON,
            "embargo_sessions": PRIMARY_HORIZON,
            "embargo_equals_forward_horizon": True,
            "min_train_sessions": MIN_TRAIN_SESSIONS,
            "validation_sessions": VALIDATION_SESSIONS,
            "step_sessions": STEP_SESSIONS,
            "no_random_cross_validation": True,
            "model_free": True,
            "fitted_model": False,
            "n_calendar_sessions": 0,
            "calendar_start": None,
            "calendar_end": None,
        },
        "n_folds": 0,
        "pooled_n_validation_dates": 0,
        "total_effective_validation_obs": 0.0,
        "mean_validation_residual_rank_ic": None,
        "pooled_validation_mean_residual_rank_ic": None,
        "median_validation_residual_rank_ic": None,
        "n_validation_windows_same_sign": 0,
        "n_validation_windows_positive": 0,
        "frac_validation_windows_same_sign": None,
        "majority_validation_windows_same_sign": False,
        "bootstrap_pooled_ic_ci": {
            "method": "moving_block",
            "block_len_sessions": PRIMARY_HORIZON,
            "n_resamples": BOOTSTRAP_N,
            "alpha": CI_ALPHA,
            "ci_low": None,
            "ci_high": None,
            "excludes_zero": False,
        },
        "leave_one_fold_out": {"per_fold": [], "sign_stable": False},
        "fold_metrics": [],
    }


def walk_forward(ics_all: "pd.Series", horizon: int,
                 reference_sign: int) -> Dict[str, Any]:
    ics_all = ics_all.sort_index()
    calendar = list(ics_all.index)
    folds = build_folds(calendar, horizon, MIN_TRAIN_SESSIONS,
                        VALIDATION_SESSIONS, STEP_SESSIONS)
    fold_metrics: List[Dict[str, Any]] = []
    val_dates_all: List[Any] = []
    for f in folds:
        val_dates = calendar[f["validation_start_idx"]:f["validation_end_idx"]]
        val_ic = ics_all.reindex(val_dates).dropna()
        train_dates = calendar[f["train_start_idx"]:f["train_end_idx"]]
        train_ic = ics_all.reindex(train_dates).dropna()
        n_val = int(len(val_ic))
        val_mean = float(val_ic.mean()) if n_val else None
        val_std = float(val_ic.std(ddof=1)) if n_val > 1 else None
        info_ratio = (val_mean / val_std
                      if (val_mean is not None and val_std not in (None, 0.0)
                          and not pd.isna(val_std)) else None)
        val_sign = _sign(val_mean)
        val_dates_all.extend(val_dates)
        fold_metrics.append({
            "fold_id": f["fold_id"],
            "train_start": f["train_start"],
            "train_end": f["train_end"],
            "embargo_start": f["embargo_start"],
            "embargo_end": f["embargo_end"],
            "validation_start": f["validation_start"],
            "validation_end": f["validation_end"],
            "n_train_dates": f["n_train_dates"],
            "n_validation_dates": n_val,
            "validation_mean_residual_rank_ic": _round(val_mean),
            "validation_information_ratio": _round(info_ratio),
            "train_mean_residual_rank_ic": _round(
                float(train_ic.mean()) if len(train_ic) else None),
            "validation_sign": val_sign,
            "validation_sign_matches_reference": bool(
                reference_sign != 0 and val_sign == reference_sign),
        })

    n_folds = len(folds)
    val_ic_pooled = ics_all.reindex(val_dates_all).dropna()
    pooled_n = int(len(val_ic_pooled))
    pooled_mean = float(val_ic_pooled.mean()) if pooled_n else None
    pooled_median = float(val_ic_pooled.median()) if pooled_n else None
    eff_obs = (pooled_n / horizon) if horizon else 0.0
    fold_means = [f["validation_mean_residual_rank_ic"] for f in fold_metrics
                  if f["validation_mean_residual_rank_ic"] is not None]
    n_same_sign = sum(1 for f in fold_metrics
                      if f["validation_sign_matches_reference"])
    n_positive = sum(1 for f in fold_metrics
                     if (f["validation_mean_residual_rank_ic"] or 0.0) > 0)
    frac_same_sign = (n_same_sign / n_folds) if n_folds else None
    lo, hi = block_bootstrap_ci(val_ic_pooled.to_numpy(), horizon)
    ci_excludes_zero = bool(lo is not None and hi is not None
                            and ((lo > 0 and hi > 0) or (lo < 0 and hi < 0)))

    # Leave-one-fold-out on the per-fold validation means.
    floor = RANK_IC_FLOOR
    lofo: List[Dict[str, Any]] = []
    lofo_sign_stable = bool(reference_sign != 0 and n_folds >= 2)
    for f in fold_metrics:
        keep = [g["validation_mean_residual_rank_ic"] for g in fold_metrics
                if g["fold_id"] != f["fold_id"]
                and g["validation_mean_residual_rank_ic"] is not None]
        m = float(np.mean(keep)) if keep else None
        sign_ok = bool(m is not None and reference_sign != 0
                       and _sign(m) == reference_sign)
        above_floor = bool(m is not None and abs(m) >= floor)
        lofo.append({
            "fold_left_out": f["fold_id"],
            "mean_validation_ic_remaining": _round(m),
            "sign_preserved": sign_ok,
            "above_floor": above_floor,
        })
        if not (sign_ok and above_floor):
            lofo_sign_stable = False

    return {
        "config": {
            "candidate_signal": CANDIDATE,
            "primary_horizon_days": horizon,
            "embargo_sessions": horizon,
            "embargo_equals_forward_horizon": True,
            "min_train_sessions": MIN_TRAIN_SESSIONS,
            "validation_sessions": VALIDATION_SESSIONS,
            "step_sessions": STEP_SESSIONS,
            "no_random_cross_validation": True,
            "model_free": True,
            "fitted_model": False,
            "n_calendar_sessions": int(len(calendar)),
            "calendar_start": _iso(calendar[0]) if calendar else None,
            "calendar_end": _iso(calendar[-1]) if calendar else None,
        },
        "n_folds": n_folds,
        "pooled_n_validation_dates": pooled_n,
        "total_effective_validation_obs": _round(eff_obs, 3),
        "mean_validation_residual_rank_ic": _round(
            float(np.mean(fold_means)) if fold_means else None),
        "pooled_validation_mean_residual_rank_ic": _round(pooled_mean),
        "median_validation_residual_rank_ic": _round(pooled_median),
        "n_validation_windows_same_sign": int(n_same_sign),
        "n_validation_windows_positive": int(n_positive),
        "frac_validation_windows_same_sign": _round(frac_same_sign),
        "majority_validation_windows_same_sign": bool(
            frac_same_sign is not None and frac_same_sign > MAJORITY_SIGN_FRACTION),
        "bootstrap_pooled_ic_ci": {
            "method": "moving_block",
            "block_len_sessions": horizon,
            "n_resamples": BOOTSTRAP_N,
            "alpha": CI_ALPHA,
            "ci_low": _round(lo),
            "ci_high": _round(hi),
            "excludes_zero": ci_excludes_zero,
        },
        "leave_one_fold_out": {"per_fold": lofo, "sign_stable": lofo_sign_stable},
        "fold_metrics": fold_metrics,
    }


# --------------------------------------------------------------------------- #
# Gates + conservative verdict
# --------------------------------------------------------------------------- #
def evaluate_gates(primary: Dict[str, Any], wf: Dict[str, Any],
                   leave_year: Dict[str, Any], regimes: Dict[str, Any],
                   survivorship: Dict[str, Any]) -> List[Dict[str, Any]]:
    mean_ic = primary["mean_residual_rank_ic"]
    frac_dates = primary["frac_dates_same_sign_as_reference"]
    ci = primary["bootstrap_mean_ic_ci"]
    top3 = primary["concentration"]["top3_long_leg_share"]
    degraded = primary["degradation_vs_phase2k_c"]["degraded"]
    eff_obs = wf["total_effective_validation_obs"] or 0.0
    spy_reg = regimes.get("spy_forward_return") or {}
    vol_reg = regimes.get("market_volatility_21d") or {}
    regime_dependent = bool(spy_reg.get("sign_flips_across_regime")
                            or vol_reg.get("sign_flips_across_regime"))
    caveat_ok = bool(survivorship.get("present")
                     and survivorship.get("point_in_time_membership_claimed") is False)

    return [
        {
            "id": "mean_residual_ic_positive",
            "observed": mean_ic,
            "threshold": "> 0",
            "passed": bool(mean_ic is not None and mean_ic > 0),
        },
        {
            "id": "mean_residual_ic_above_floor",
            "observed": mean_ic,
            "threshold": RANK_IC_FLOOR,
            "passed": bool(mean_ic is not None and mean_ic >= RANK_IC_FLOOR),
        },
        {
            "id": "majority_dates_same_sign",
            "observed": frac_dates,
            "threshold": MAJORITY_SIGN_FRACTION,
            "passed": bool(frac_dates is not None and frac_dates > MAJORITY_SIGN_FRACTION),
        },
        {
            "id": "majority_validation_folds_same_sign",
            "observed": wf["frac_validation_windows_same_sign"],
            "threshold": MAJORITY_SIGN_FRACTION,
            "passed": bool(wf["majority_validation_windows_same_sign"]),
        },
        {
            "id": "bootstrap_ci_excludes_zero",
            "observed": {"ci_low": ci["ci_low"], "ci_high": ci["ci_high"]},
            "threshold": "95% CI excludes 0 (materially above zero)",
            "passed": bool(ci["excludes_zero"]),
        },
        {
            "id": "no_single_year_drives_result",
            "observed": leave_year.get("sign_stable"),
            "threshold": "leave-one-year-out sign stable",
            "passed": bool(leave_year.get("sign_stable")),
        },
        {
            "id": "no_regime_only_dependence",
            "observed": regime_dependent,
            "threshold": "no sign flip across SPY up/down or market-vol split",
            "passed": bool(not regime_dependent),
        },
        {
            "id": "no_excessive_ticker_concentration",
            "observed": top3,
            "threshold": CONCENTRATION_TOP3_THRESHOLD,
            "passed": bool(top3 is not None and top3 <= CONCENTRATION_TOP3_THRESHOLD),
        },
        {
            "id": "no_material_degradation_vs_phase2k_c",
            "observed": mean_ic,
            "threshold": primary["degradation_vs_phase2k_c"]["degradation_floor"],
            "passed": bool(not degraded and mean_ic is not None),
        },
        {
            "id": "enough_effective_observations_after_embargo",
            "observed": eff_obs,
            "threshold": MIN_EFFECTIVE_OBS,
            "passed": bool(eff_obs is not None and eff_obs >= MIN_EFFECTIVE_OBS),
        },
        {
            "id": "survivorship_caveat_carried_forward",
            "observed": caveat_ok,
            "threshold": "survivorship caveat present and point-in-time not claimed",
            "passed": caveat_ok,
        },
    ]


def recommend(gates: List[Dict[str, Any]], wf: Dict[str, Any],
              dq_ok: bool, upstream_ready: bool,
              caveat_ok: bool) -> Dict[str, Any]:
    """Conservative verdict.

    NEED_MORE_DATA_OR_PIT_UNIVERSE when the upstream build is not ready, the data
    quality is inadequate, the survivorship/current-membership caveat is missing or a
    point-in-time claim was made, or there are too few folds / effective observations
    to support a conclusion. Otherwise EXPANDED_RETEST_PASS only if every gate passes,
    else EXPANDED_RETEST_FAIL. Even a pass is survivorship-biased -- it is NOT a
    production edge and NOT permission to train or serve a model.
    """
    n_folds = wf.get("n_folds", 0)
    eff_obs = wf.get("total_effective_validation_obs") or 0.0
    enough_data = bool(upstream_ready and dq_ok and caveat_ok
                       and n_folds >= MIN_FOLDS and eff_obs >= MIN_EFFECTIVE_OBS)
    failed = [g["id"] for g in gates if not g["passed"]]
    all_pass = not failed

    if not enough_data:
        rec = REC_NEED
        if not upstream_ready:
            reason = ("the Phase 2K-H manual build is not present or not ready for "
                      "retest (manual_build_detected / ready_for_retest / "
                      "recommended 2K-I / PASS or PASS_WITH_CAVEAT / "
                      "point_in_time_membership_claimed == false were not all "
                      "satisfied); run the manual build and re-run the 2K-H analyzer "
                      "first.")
        elif not dq_ok:
            reason = ("the expanded dataset's data-quality status is not PASS / "
                      "PASS_WITH_CAVEAT, so the retest cannot draw a conclusion.")
        elif not caveat_ok:
            reason = ("the survivorship caveat is missing or a point-in-time "
                      "membership claim was made; a clean point-in-time universe is "
                      "needed before any further retest conclusion.")
        else:
            reason = (f"only {n_folds} walk-forward folds / {eff_obs} effective "
                      f"validation observations after the horizon-length embargo "
                      f"(need >= {MIN_FOLDS} folds and >= {MIN_EFFECTIVE_OBS} effective "
                      "obs); the survivorship-caveated panel still cannot support a "
                      "conclusive retest.")
    elif all_pass:
        rec = REC_PASS
        reason = ("on the expanded, survivorship-caveated panel the model-free "
                  "residual signal cleared every retest and walk-forward gate; the "
                  "result is still survivorship-biased and is NOT a production edge "
                  "and NOT permission to train or serve a model.")
    else:
        rec = REC_FAIL
        reason = ("the expanded panel has enough data but the residual signal failed "
                  "gate(s): " + ", ".join(failed)
                  + "; it does not hold up on the larger survivorship-caveated panel.")

    return {
        "recommendation": rec,
        "allowed_values": list(_ALLOWED_RECOMMENDATIONS),
        "enough_data": enough_data,
        "all_gates_passed": all_pass,
        "failed_gates": failed,
        "create_model_candidate_now": False,
        "train_model_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "results_are_survivorship_biased": True,
        "reason": reason,
    }


def build_recommended_next_phase(recommendation: str) -> Dict[str, str]:
    if recommendation == REC_PASS:
        return {
            "phase": "2K-J",
            "title": "Survivorship-Caveated Walk-Forward Residual Signal Confirmation",
            "purpose": (
                "Confirm the expanded-data retest with stricter walk-forward gates "
                "(longer training, more embargoed folds, tighter degradation / "
                "concentration tolerances) before any model-candidate DESIGN. The "
                "result remains survivorship-biased and is not a production edge; "
                "nothing is trained, served, or deployed."),
        }
    if recommendation == REC_FAIL:
        return {
            "phase": "2K-J",
            "title": "Alpha Backlog Refresh After Expanded Retest Failure",
            "purpose": (
                "Stop the avg_dollar_volume_21d liquidity candidate and refresh the "
                "alpha backlog using the expanded-data diagnostics (where and why the "
                "residual signal broke down on the larger panel). No model candidate "
                "is created."),
        }
    return {
        "phase": "2K-J",
        "title": "Point-in-Time Universe Decision Before Further Retests",
        "purpose": (
            "Decide whether to obtain a proper point-in-time membership universe "
            "before any further retest. The current panel is survivorship-caveated / "
            "current-as-of only, or there is too little usable data to conclude. No "
            "model candidate is created."),
    }


def build_interpretation(recommendation: Dict[str, Any], primary: Dict[str, Any],
                         wf: Dict[str, Any]) -> Dict[str, Any]:
    rec = recommendation["recommendation"]
    if rec == REC_PASS:
        verdict = ("held up on the larger, longer, survivorship-caveated panel across "
                   "the full-sample retest and the embargoed walk-forward windows")
    elif rec == REC_NEED:
        verdict = ("could not be conclusively retested -- the upstream build was not "
                   "ready, the data quality was inadequate, the universe is "
                   "survivorship-caveated / current-as-of only, or too few effective "
                   "observations remain after the embargo")
    else:
        verdict = ("did not hold up on the larger survivorship-caveated panel once "
                   "retested across full-sample and walk-forward gates")
    return {
        "candidate": CANDIDATE,
        "model_trained": False,
        "model_fitted": False,
        "model_candidate_created": False,
        "authorized_to_train_model": False,
        "authorized_to_serve_model": False,
        "production_edge_claimed": False,
        "results_are_survivorship_biased": True,
        "expanded_retest_recommendation": rec,
        "primary_horizon_days": PRIMARY_HORIZON,
        "primary_mean_residual_rank_ic": primary.get("mean_residual_rank_ic"),
        "pooled_validation_mean_residual_rank_ic": wf.get(
            "pooled_validation_mean_residual_rank_ic"),
        "narrative": (
            "Phase 2K-I retested the lone Phase 2K-C survivor, " + CANDIDATE + ", at "
            "its 63d primary horizon on the expanded free panel produced by the manual "
            "Phase 2K-H build. The trailing features and risk controls were recomputed "
            "from the expanded adjusted-OHLCV history, the same beta/vol-neutralized "
            "residual label was rebuilt, and only model-free rank-only metrics were "
            "measured across the full sample and sequential embargoed walk-forward "
            "windows -- nothing was fitted. The signal " + verdict + ". Every result "
            "is reported as survivorship-biased / current-membership caveated. No model "
            "was trained, fitted, scored, or served, and no model candidate was "
            "created. Even an expanded-retest pass only permits a later, separately "
            "gated confirmation phase; it is not a production edge."),
    }


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_results(*, data_root: Optional[str] = None,
                  upstream_2kh_path: Optional[str] = None,
                  phase2k_c_path: Optional[str] = None,
                  phase2k_b_path: Optional[str] = None,
                  phase2l_b_path: Optional[str] = None,
                  price_history_path: Optional[str] = None) -> Dict[str, Any]:
    data_root = data_root or DATA_ROOT
    upstream_2kh_path = upstream_2kh_path or UPSTREAM_2KH_JSON
    phase2k_c_path = phase2k_c_path or INPUT_2KC_JSON
    phase2k_b_path = phase2k_b_path or INPUT_2KB_JSON
    phase2l_b_path = phase2l_b_path or INPUT_2LB_JSON
    paths = _resolve_paths(data_root)
    price_path = price_history_path or paths["price_history_csv"]

    upstream = summarize_upstream_2kh(_safe_read_json(upstream_2kh_path),
                                      upstream_2kh_path)
    twokc = _safe_read_json(phase2k_c_path)
    ic_2kc = phase2k_c_reference_ic(twokc)

    dq = _safe_read_json(paths["data_quality_report_json"])
    bs = _safe_read_json(paths["data_build_summary_json"])
    sc = _safe_read_json(paths["survivorship_caveat_json"])
    dq_sum = summarize_data_quality(dq)
    sc_sum = summarize_survivorship(sc)
    # Fall back to the 2K-H digest of these summaries when the D: JSONs are absent.
    if not dq_sum.get("present") and upstream.get("present"):
        raw = _safe_read_json(upstream_2kh_path) or {}
        dq_sum = {**raw.get("data_quality_summary", {}), "present": bool(
            raw.get("data_quality_summary"))} or dq_sum
        dq_sum["passed"] = dq_sum.get("status") in ("PASS", "PASS_WITH_CAVEAT")
    if not sc_sum.get("present") and upstream.get("present"):
        raw = _safe_read_json(upstream_2kh_path) or {}
        sc_raw = raw.get("survivorship_caveat_summary", {})
        if sc_raw:
            sc_sum = {**sc_raw, "present": True,
                      "all_results_must_be_reported_survivorship_biased": True}

    upstream_ready = bool(upstream.get("ready_for_2k_i"))
    dq_ok = bool(dq_sum.get("passed"))
    caveat_ok = bool(sc_sum.get("present")
                     and sc_sum.get("point_in_time_membership_claimed") is False)

    reference_sign = _sign(ic_2kc) or 1  # Phase 2K-C read the candidate bullish.

    feature_summary: Dict[str, Any] = {
        "source_csv": _norm(price_path),
        "source_used": "expanded_price_history_csv",
        "computed_features": [CANDIDATE] + CONTROL_FEATURES,
        "all_features_trailing_point_in_time": True,
        "labels_forward_filled": False,
        "forward_label_base": "forward_excess_return_vs_spy",
        "rows_loaded": 0,
        "n_tickers": 0,
        "n_dates": 0,
        "computed": False,
        "note": None,
    }
    dataset_summary: Dict[str, Any] = {
        "data_quality": dq_sum,
        "build_summary": {
            "row_count": (bs or {}).get("row_count"),
            "ticker_count": (bs or {}).get("ticker_count"),
            "benchmark": (bs or {}).get("benchmark"),
            "date_start": (bs or {}).get("date_start"),
            "date_end": (bs or {}).get("date_end"),
            "years_span": (bs or {}).get("years_span"),
            "data_quality_status": (bs or {}).get("data_quality_status"),
            "model_trained": (bs or {}).get("model_trained"),
            "model_candidate_created": (bs or {}).get("model_candidate_created"),
        } if bs else {"present": False},
    }

    residualization_config = {
        "label_base": "forward_excess_return_vs_spy",
        "horizons": HORIZONS,
        "primary_horizon_days": PRIMARY_HORIZON,
        "controls": CONTROL_FEATURES,
        "method": ("per-date cross-sectional OLS (usable controls + intercept); "
                   "residual = forward excess label - fitted (same construction as "
                   "Phase 2K-B / 2K-C / 2L-B)"),
        "robust_fallback": ("cross-sectional demean when no control is usable, a "
                            "control is constant, or the date has fewer than "
                            "min_names_for_ols usable names"),
        "min_names_for_ols": RESID_MIN_NAMES,
        "no_lookahead": ("controls are trailing point-in-time features computed from "
                         "the expanded price history; the label is strictly forward; "
                         "each per-date fit uses no forward information"),
        "residual_label_columns": {str(h): f"resid_{h}d" for h in HORIZONS},
        "fit_counts": {},
    }

    retest_metrics: Dict[str, Any] = {
        "candidate": CANDIDATE,
        "primary_horizon_days": PRIMARY_HORIZON,
        "by_horizon": {},
    }
    walk_forward_metrics: Dict[str, Any] = empty_walk_forward_metrics()
    regime_metrics: Dict[str, Any] = {}
    stability_metrics: Dict[str, Any] = {}

    # Only run the heavy compute path when the upstream build is ready, the data is
    # adequate, and the expanded price history is actually present and non-trivial.
    can_compute = bool(upstream_ready and dq_ok and caveat_ok
                       and os.path.isfile(price_path))
    if can_compute:
        try:
            prices = _read_price_history(price_path)
        except (OSError, ValueError):
            prices = pd.DataFrame()
        if len(prices) and prices[TICKER_COL].nunique() >= MIN_NAMES_PER_DATE + 1:
            feats = compute_features(prices)
            labels = build_forward_excess_labels(prices)
            feat_cols = [TICKER_COL, DATE_COL, CANDIDATE] + CONTROL_FEATURES
            panel = feats[feat_cols].merge(labels, on=[TICKER_COL, DATE_COL],
                                           how="inner")
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

            by_horizon: Dict[str, Any] = {}
            primary_ics: Optional["pd.Series"] = None
            for h in HORIZONS:
                cell = horizon_retest(panel, h, reference_sign, ic_2kc)
                ics = cell.pop("_ics")
                if h == PRIMARY_HORIZON:
                    primary_ics = ics
                by_horizon[str(h)] = cell
            retest_metrics["by_horizon"] = by_horizon

            if primary_ics is None:
                primary_ics = pd.Series(dtype=float)
            primary = by_horizon[str(PRIMARY_HORIZON)]

            wf = walk_forward(primary_ics, PRIMARY_HORIZON, reference_sign)
            walk_forward_metrics = wf

            spy_fwd = spy_forward_return(prices, PRIMARY_HORIZON)
            market_vol = build_market_vol(feats)
            regime_metrics = {
                "primary_horizon_days": PRIMARY_HORIZON,
                "spy_forward_return": _regime_split(primary_ics, spy_fwd, "sign"),
                "market_volatility_21d": _regime_split(primary_ics, market_vol, "median"),
            }
            leave_year = leave_one_year_out(primary_ics, reference_sign)
            stability_metrics = {
                "leave_one_year_out": leave_year,
                "leave_one_fold_out": wf["leave_one_fold_out"],
                "degradation_vs_phase2k_c": primary["degradation_vs_phase2k_c"],
            }
            gates = evaluate_gates(primary, wf, leave_year, regime_metrics, sc_sum)
            recommendation = recommend(gates, wf, dq_ok, upstream_ready, caveat_ok)
        else:
            feature_summary["note"] = ("expanded price history present but too few "
                                       "tickers / rows to retest")
            gates = []
            recommendation = recommend([], walk_forward_metrics, dq_ok, upstream_ready,
                                       caveat_ok)
    else:
        feature_summary["note"] = ("retest not computed: upstream build not ready, data "
                                   "quality inadequate, or expanded price history "
                                   "absent")
        gates = []
        recommendation = recommend([], walk_forward_metrics, dq_ok, upstream_ready,
                                   caveat_ok)

    recommended_next_phase = build_recommended_next_phase(
        recommendation["recommendation"])
    primary_for_interp = retest_metrics["by_horizon"].get(
        str(PRIMARY_HORIZON), {"mean_residual_rank_ic": None})
    interpretation = build_interpretation(recommendation, primary_for_interp,
                                          walk_forward_metrics)

    return {
        "phase": PHASE,
        "generated_at": _now(),
        "inputs_read": {
            "phase2k_h_summary_json": _norm(upstream_2kh_path),
            "phase2k_c_json": _norm(phase2k_c_path),
            "phase2k_b_json": _norm(phase2k_b_path),
            "phase2l_b_json": _norm(phase2l_b_path),
            "expanded_price_history_csv": _norm(price_path),
            "expanded_scored_csv": _norm(paths["scored_csv"]),
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
        "upstream_phase2k_h_summary": upstream,
        "dataset_summary": dataset_summary,
        "survivorship_caveat_summary": sc_sum,
        "feature_engineering_summary": feature_summary,
        "residualization_config": residualization_config,
        "candidate": {
            "feature": CANDIDATE,
            "primary_horizon_days": PRIMARY_HORIZON,
            "comparison_horizon_days": 21,
            "direction": "bullish",
            "reference_sign": int(reference_sign),
            "phase2k_c_residual_mean_rank_ic": _round(ic_2kc),
            "is_research_only": True,
            "is_production_edge": False,
        },
        "retest_metrics": retest_metrics,
        "walk_forward_metrics": walk_forward_metrics,
        "regime_metrics": regime_metrics,
        "stability_metrics": stability_metrics,
        "pass_fail_gates": gates,
        "recommendation": recommendation,
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
    rec = d["recommendation"]
    nxt = d["recommended_next_phase"]
    up = d["upstream_phase2k_h_summary"]
    primary = d["retest_metrics"]["by_horizon"].get(str(PRIMARY_HORIZON), {})
    wf = d["walk_forward_metrics"]
    print(f"[phase2k-i] candidate                    : {CANDIDATE} (h={PRIMARY_HORIZON}d)")
    print(f"[phase2k-i] upstream 2K-H ready           : {up.get('ready_for_2k_i')}")
    print(f"[phase2k-i] expanded retest computed      : "
          f"{d['feature_engineering_summary'].get('computed')}")
    print(f"[phase2k-i] primary residual mean IC      : "
          f"{primary.get('mean_residual_rank_ic')}")
    print(f"[phase2k-i] walk-forward folds / eff obs  : {wf.get('n_folds')} / "
          f"{wf.get('total_effective_validation_obs')}")
    print(f"[phase2k-i] recommendation                : {rec['recommendation']}")
    print(f"[phase2k-i] recommended next phase        : {nxt['phase']} ({nxt['title']})")
    print(f"[phase2k-i] results written               : {RESULTS_JSON}")
    print(f"[phase2k-i] elapsed seconds                : {elapsed:.2f}")
    print("[phase2k-i] read-only; survivorship-biased; no model trained; no D: writes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
