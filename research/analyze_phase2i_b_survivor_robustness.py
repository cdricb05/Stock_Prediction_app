"""Phase 2I-B Survivor Robustness & Regime Validation analyzer.

Phase 2I-A found six KEEP features, all strongest at the 63d horizon and all
bullish (a volatility / beta / correlation tilt). Before any Phase 2J model is
designed, this phase asks one question per survivor: **is the 63d signal stable,
or is it a regime / small-sample artifact?**

For each Phase 2I-A survivor at its best (63d) horizon this computes, off the
already-vectorized 2I-A panel and per-date rank-IC machinery:

  * stability   -- yearly & quarterly IC, rolling 6-month IC, sign stability by
                   year/quarter, IC information ratio by year, top-minus-bottom
                   spread and top-decile hit rate by year, with n_dates / n_rows
                   per split;
  * regimes     -- IC in SPY-up vs SPY-down forward windows, high vs low market
                   volatility, and high vs low cross-sectional feature dispersion;
  * robustness  -- a moving-block bootstrap CI for the mean rank IC (block length
                   = horizon, so overlapping 63d windows are respected), a
                   sign-flip permutation null for IC significance, leave-one-year-out
                   stability, and a leave-one-ticker-out check on the dominant name;
  * concentration -- per-ticker top-quintile membership share and the top
                   contributors to the long leg, with an overly-concentrated flag;
  * recommendation -- KEEP_FOR_MODEL / KEEP_AS_RISK_FILTER_ONLY / DROP /
                   NEED_MORE_DATA per survivor; and
  * interpretation -- whether the edge reads as a volatility / risk premium, beta
                   exposure, correlation regime, or momentum effect, and whether it
                   is robust enough to seed Phase 2J long-horizon candidates.

This phase is research only. It imports the Phase 2I-A analyzer to reuse its
read-only loaders and vectorized helpers, reads the local Phase 2G real-data
artifacts (via that analyzer) plus the Phase 2I-A diagnostics JSON, and writes
exactly one diagnostics JSON. It performs no infrastructure action and mutates no
datastore. Machine-readable safety flags are emitted in the diagnostics JSON; the
full guardrail rationale lives in docs/phase2i_b_survivor_robustness_v1.md.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

PHASE = "2I-B"

_HERE = os.path.dirname(os.path.abspath(__file__))
_OUTPUT_DIR = os.path.join("research", "output")

# The Phase 2I-A analyzer is the single source of truth for the panel build and
# the vectorized per-date rank-IC / spread helpers. Import it by path so this
# phase reuses (rather than re-implements) that logic. Importing only binds its
# functions and constants -- it runs nothing.
_ANALYZER_2IA = os.path.join(_HERE, "analyze_phase2i_feature_ic.py")


def _load_2ia():
    spec = importlib.util.spec_from_file_location("phase2i_a_analyzer", _ANALYZER_2IA)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_A = _load_2ia()

# Reused, single-sourced from the Phase 2I-A analyzer.
_round = _A._round
_sign = _A._sign
DATE_COL = _A.DATE_COL
TICKER_COL = _A.TICKER_COL
SPY = _A.SPY
MIN_NAMES_DECILE = _A.MIN_NAMES_DECILE

# Inputs (read-only). The 2G artifacts are read through the 2I-A loader; the
# 2I-A diagnostics JSON is read directly to discover the survivor shortlist.
INPUT_2IA_JSON = _A.DIAGNOSTICS_JSON
SCORED_CSV = _A.SCORED_CSV
PRICE_HISTORY_CSV = _A.PRICE_HISTORY_CSV
RUN_SUMMARY_JSON = _A.RUN_SUMMARY_JSON

# The single output this analyzer is allowed to write.
DIAGNOSTICS_JSON = os.path.join(_OUTPUT_DIR, "phase2i_b_survivor_robustness.json")

# Market-level regime inputs already present in the scored CSV.
MARKET_VOL_COL = "spy_realized_vol_21d"

# Risk-premium features: a surviving edge in any of these reads as exposure
# (vol / downside / beta / correlation) rather than name-selection alpha, so it
# is at best a risk filter, not model alpha.
RISK_FEATURES = frozenset({
    "realized_vol_21d", "realized_vol_63d", "downside_vol_21d",
    "rolling_beta_63d", "rolling_corr_spy_63d",
})

# Robustness / recommendation knobs.
BOOTSTRAP_N = 1000          # moving-block bootstrap resamples for the mean-IC CI
PERMUTATION_N = 1000        # sign-flip permutations for the IC significance null
CI_ALPHA = 0.05             # 95% bootstrap CI
PERM_ALPHA = 0.05           # permutation significance threshold
SEED = 12345                # deterministic RandomState seed (per survivor)
ROLL_WINDOW_SESSIONS = 126  # ~6 months of sessions for the rolling-IC stability view
MIN_EFFECTIVE_OBS = 8.0     # n_dates / horizon floor below which we say NEED_MORE_DATA
CONCENTRATION_TOP3_THRESHOLD = 0.50  # top-3 long-leg share above which -> concentrated


# --------------------------------------------------------------------------- #
# Read-only helpers
# --------------------------------------------------------------------------- #
def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_survivors(input_json: str = INPUT_2IA_JSON) -> List[Dict[str, Any]]:
    """The Phase 2I-A KEEP shortlist (feature + best horizon), read at runtime."""
    twoia = _read_json(input_json)
    keep = twoia.get("keep_drop", {}).get("keep_ranked", [])
    return [{"feature": r["feature"],
             "best_horizon_days": int(r["best_horizon_days"]),
             "phase2i_a_mean_rank_ic": r.get("mean_rank_ic"),
             "phase2i_a_information_ratio": r.get("information_ratio"),
             "phase2i_a_direction": r.get("direction")}
            for r in keep]


def _spy_forward_return(prices: "pd.DataFrame", h: int) -> "pd.Series":
    """SPY's own forward h-session return per date (regime label for the window)."""
    px = prices.sort_values([TICKER_COL, "date"])
    spy = px[px[TICKER_COL] == SPY].copy()
    spy["_fwd"] = spy["adj_close"].shift(-h) / spy["adj_close"] - 1.0
    s = spy.set_index("date")["_fwd"].dropna()
    s.index = pd.to_datetime(s.index)
    return s


def _per_date_scalar(scored: "pd.DataFrame", col: str) -> "pd.Series":
    """A market-level per-date scalar (e.g. SPY realized vol) keyed by as_of_date."""
    if col not in scored.columns:
        return pd.Series(dtype=float)
    df = scored[[DATE_COL, col]].dropna()
    s = df.groupby(DATE_COL)[col].first()
    s.index = pd.to_datetime(s.index)
    return s


# --------------------------------------------------------------------------- #
# Stability views (per survivor, all off the same per-date IC series)
# --------------------------------------------------------------------------- #
def _bucket_stat(ic: "pd.Series") -> Dict[str, Any]:
    return {"rank_ic": _round(ic.mean()) if len(ic) else None,
            "n_dates": int(len(ic))}


def _yearly_detail(ics: "pd.Series", sub: "pd.DataFrame", fcol: str, lcol: str,
                   overall_sign: int) -> List[Dict[str, Any]]:
    sub_years = pd.to_datetime(sub[DATE_COL]).dt.year
    out: List[Dict[str, Any]] = []
    for y in sorted(set(ics.index.year)):
        yic = ics[ics.index.year == y]
        mean = yic.mean()
        std = yic.std(ddof=1) if len(yic) > 1 else None
        ir = (mean / std if std not in (None, 0.0) and not pd.isna(std) else None)
        suby = sub[sub_years == y]
        spread, hit = _A._spread_and_hit_rate(suby, fcol, lcol)
        out.append({
            "year": int(y),
            "rank_ic": _round(mean),
            "information_ratio": _round(ir),
            "n_dates": int(len(yic)),
            "n_rows": int(len(suby)),
            "sign_matches_overall": bool(overall_sign and _sign(mean) == overall_sign),
            "top_minus_bottom_spread": _round(spread),
            "top_decile_hit_rate": _round(hit),
        })
    return out


def _quarterly_detail(ics: "pd.Series", overall_sign: int) -> List[Dict[str, Any]]:
    rows = _A._calendar_ic(ics, "quarter")
    for r in rows:
        r["sign_matches_overall"] = bool(
            overall_sign and _sign(r["rank_ic"]) == overall_sign)
    return rows


def _rolling_6m(ics: "pd.Series", overall_sign: int,
                window: int = ROLL_WINDOW_SESSIONS) -> Dict[str, Any]:
    s = ics.sort_index()
    if len(s) < window:
        return {"window_sessions": window, "available": False, "n_windows": 0,
                "reason": "fewer per-date observations than one 6-month window"}
    roll = s.rolling(window).mean().dropna()
    same = int((roll.map(_sign) == overall_sign).sum()) if overall_sign else 0
    n = int(len(roll))
    return {
        "window_sessions": window,
        "available": True,
        "n_windows": n,
        "min_rank_ic": _round(roll.min()),
        "max_rank_ic": _round(roll.max()),
        "mean_rank_ic": _round(roll.mean()),
        "frac_windows_same_sign_as_overall": _round(same / n) if n else None,
    }


def _sign_stability(yearly: List[Dict[str, Any]],
                    quarterly: List[Dict[str, Any]]) -> Dict[str, Any]:
    yr_match = sum(1 for y in yearly if y["sign_matches_overall"])
    q_match = sum(1 for q in quarterly if q["sign_matches_overall"])
    return {
        "years_present": len(yearly),
        "years_matching_sign": yr_match,
        "frac_years_matching_sign": _round(yr_match / len(yearly)) if yearly else None,
        "quarters_present": len(quarterly),
        "quarters_matching_sign": q_match,
        "frac_quarters_matching_sign": _round(q_match / len(quarterly)) if quarterly else None,
    }


# --------------------------------------------------------------------------- #
# Regime buckets
# --------------------------------------------------------------------------- #
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
        out = {"threshold": 0.0,
               "positive": _bucket_stat(hi), "negative": _bucket_stat(lo)}
        out["sign_flips_across_regime"] = bool(
            out["positive"]["rank_ic"] is not None
            and out["negative"]["rank_ic"] is not None
            and _sign(out["positive"]["rank_ic"]) != _sign(out["negative"]["rank_ic"])
            and _sign(out["positive"]["rank_ic"]) != 0
            and _sign(out["negative"]["rank_ic"]) != 0)
        return out
    med = float(aligned["v"].median())
    hi = aligned[aligned["v"] >= med]["ic"]
    lo = aligned[aligned["v"] < med]["ic"]
    return {"median_split": _round(med),
            "high": _bucket_stat(hi), "low": _bucket_stat(lo)}


def _regimes(ics: "pd.Series", sub: "pd.DataFrame", fcol: str,
             spy_fwd: "pd.Series", market_vol: "pd.Series") -> Dict[str, Any]:
    disp = sub.groupby(DATE_COL)[fcol].std(ddof=1).dropna()
    disp.index = pd.to_datetime(disp.index)
    return {
        "spy_forward_return": _regime_split(ics, spy_fwd, "sign"),
        "market_volatility_21d": _regime_split(ics, market_vol, "median"),
        "feature_cross_sectional_dispersion": _regime_split(ics, disp, "median"),
    }


# --------------------------------------------------------------------------- #
# Robustness
# --------------------------------------------------------------------------- #
def _block_bootstrap_ci(ic_values: "np.ndarray", block_len: int, n_boot: int,
                        rng: "np.random.RandomState", alpha: float
                        ) -> Tuple[Optional[float], Optional[float]]:
    """Moving-block bootstrap CI for the mean per-date IC.

    Block length = the forward horizon, so the highly autocorrelated overlapping
    63d windows are resampled in contiguous blocks rather than as if independent.
    """
    arr = np.asarray(ic_values, dtype=float)
    n = arr.size
    if n < 2:
        return None, None
    bl = max(1, min(block_len, n))
    n_blocks = int(np.ceil(n / bl))
    max_start = n - bl
    means = np.empty(n_boot, dtype=float)
    span = np.arange(bl)
    for b in range(n_boot):
        starts = rng.randint(0, max_start + 1, size=n_blocks)
        idx = (starts[:, None] + span[None, :]).ravel()[:n]
        means[b] = arr[idx].mean()
    lo = float(np.percentile(means, 100 * alpha / 2.0))
    hi = float(np.percentile(means, 100 * (1.0 - alpha / 2.0)))
    return lo, hi


def _sign_flip_p(ic_values: "np.ndarray", n_perm: int,
                 rng: "np.random.RandomState") -> Optional[float]:
    """Two-sided sign-flip permutation p for H0: per-date IC is centered at zero."""
    arr = np.asarray(ic_values, dtype=float)
    n = arr.size
    if n < 2:
        return None
    observed = abs(float(arr.mean()))
    signs = rng.randint(0, 2, size=(n_perm, n)) * 2 - 1
    perm_means = np.abs((signs * arr[None, :]).mean(axis=1))
    return float((perm_means >= observed).mean())


def _leave_one_year_out(ics: "pd.Series", overall_sign: int
                        ) -> Tuple[List[Dict[str, Any]], bool]:
    years = sorted(set(ics.index.year))
    out: List[Dict[str, Any]] = []
    stable = bool(overall_sign != 0 and len(years) >= 2)
    for y in years:
        rem = ics[ics.index.year != y]
        m = rem.mean() if len(rem) else None
        out.append({"year_left_out": int(y), "rank_ic_remaining": _round(m),
                    "n_dates_remaining": int(len(rem))})
        if overall_sign == 0 or m is None or _sign(m) != overall_sign:
            stable = False
    return out, stable


def _robustness(ics: "pd.Series", panel: "pd.DataFrame", fcol: str, lcol: str,
                horizon: int, overall_sign: int, dominant_ticker: Optional[str]
                ) -> Dict[str, Any]:
    rng = np.random.RandomState(SEED)
    ic_values = ics.to_numpy()
    lo, hi = _block_bootstrap_ci(ic_values, horizon, BOOTSTRAP_N, rng, CI_ALPHA)
    ci_excludes_zero = bool(
        lo is not None and hi is not None and ((lo > 0 and hi > 0) or (lo < 0 and hi < 0)))
    perm_p = _sign_flip_p(ic_values, PERMUTATION_N, rng)
    perm_significant = bool(perm_p is not None and perm_p < PERM_ALPHA)
    loyo, loyo_stable = _leave_one_year_out(ics, overall_sign)

    # Leave-one-ticker-out: drop the dominant long-leg name and re-measure.
    loto: Dict[str, Any] = {"dropped_ticker": dominant_ticker}
    if dominant_ticker is not None:
        sub_excl = panel[panel[TICKER_COL] != dominant_ticker]
        cell_excl = _A._cell_frame(sub_excl, fcol, lcol)
        ics_excl = _A._per_date_rank_ic(cell_excl, fcol, lcol)
        m_excl = float(ics_excl.mean()) if len(ics_excl) else None
        full_mean = float(ics.mean()) if len(ics) else None
        loto.update({
            "mean_rank_ic_excluding": _round(m_excl),
            "delta_vs_full": _round(
                (m_excl - full_mean) if (m_excl is not None and full_mean is not None) else None),
            "sign_preserved": bool(
                m_excl is not None and overall_sign and _sign(m_excl) == overall_sign),
        })

    return {
        "bootstrap_mean_ic_ci": {
            "method": "moving_block",
            "block_len_sessions": horizon,
            "n_resamples": BOOTSTRAP_N,
            "alpha": CI_ALPHA,
            "ci_low": _round(lo),
            "ci_high": _round(hi),
            "excludes_zero": ci_excludes_zero,
        },
        "permutation_null": {
            "method": "sign_flip",
            "n_permutations": PERMUTATION_N,
            "alpha": PERM_ALPHA,
            "p_value": _round(perm_p),
            "significant": perm_significant,
        },
        "leave_one_year_out": {"per_year": loyo, "sign_stable": loyo_stable},
        "leave_one_ticker_out": loto,
    }


# --------------------------------------------------------------------------- #
# Concentration
# --------------------------------------------------------------------------- #
def _concentration(panel: "pd.DataFrame", fcol: str, lcol: str
                   ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    sub2 = panel[[DATE_COL, TICKER_COL, fcol, lcol]].dropna(subset=[fcol, lcol]).copy()
    if sub2.empty:
        return None, None
    g = sub2.groupby(DATE_COL)[fcol]
    size = g.transform("size")
    nuniq = g.transform("nunique")
    elig = sub2[(size >= MIN_NAMES_DECILE) & (nuniq >= 5)].copy()
    if elig.empty:
        return None, None
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
        return None, None
    counts = top[TICKER_COL].value_counts()
    shares = counts / n_top
    mean_excess_top = top.groupby(TICKER_COL)[lcol].mean()
    contributors = [{
        "ticker": str(tkr),
        "top_quintile_share": _round(float(shares[tkr])),
        "n_dates_in_top_quintile": int(counts[tkr]),
        "mean_forward_excess_in_top": _round(float(mean_excess_top[tkr])),
    } for tkr in counts.index[:5]]
    top3_share = float(shares.iloc[:3].sum())
    overly = bool(top3_share > CONCENTRATION_TOP3_THRESHOLD)
    summary = {
        "top_quintile_rows": n_top,
        "n_unique_top_quintile_tickers": int(counts.size),
        "top3_long_leg_share": _round(top3_share),
        "overly_concentrated": overly,
        "concentration_threshold": CONCENTRATION_TOP3_THRESHOLD,
        "top_contributors": contributors,
    }
    return summary, str(counts.index[0])


# --------------------------------------------------------------------------- #
# Recommendation + interpretation
# --------------------------------------------------------------------------- #
def _classify_feature(fcol: str) -> str:
    if fcol in ("realized_vol_21d", "realized_vol_63d", "downside_vol_21d"):
        return "volatility_risk_premium"
    if fcol == "rolling_beta_63d":
        return "beta_exposure"
    if fcol == "rolling_corr_spy_63d":
        return "correlation_regime"
    if fcol == "momentum_12_1":
        return "momentum"
    return "unclassified"


def _recommend(mean_ic: Optional[float], n_dates: int, horizon: int,
               robustness: Dict[str, Any], concentration: Optional[Dict[str, Any]],
               fcol: str) -> Dict[str, Any]:
    effective_obs = (n_dates / horizon) if horizon else 0.0
    ci_excludes_zero = robustness["bootstrap_mean_ic_ci"]["excludes_zero"]
    perm_significant = robustness["permutation_null"]["significant"]
    loyo_stable = robustness["leave_one_year_out"]["sign_stable"]
    overly = bool(concentration and concentration["overly_concentrated"])
    is_risk = fcol in RISK_FEATURES

    reasons: List[str] = []
    if mean_ic is None or n_dates < 2:
        rec = "NEED_MORE_DATA"
        reasons.append("insufficient per-date observations")
    elif effective_obs < MIN_EFFECTIVE_OBS:
        rec = "NEED_MORE_DATA"
        reasons.append(
            f"effective_obs {effective_obs:.1f} < {MIN_EFFECTIVE_OBS} "
            "(overlapping 63d windows thin the independent sample)")
    elif not ci_excludes_zero or not perm_significant or not loyo_stable:
        rec = "DROP"
        if not ci_excludes_zero:
            reasons.append("bootstrap CI for mean IC includes zero")
        if not perm_significant:
            reasons.append("sign-flip permutation null not rejected at alpha")
        if not loyo_stable:
            reasons.append("IC sign not stable leaving any single year out")
    elif overly or is_risk:
        rec = "KEEP_AS_RISK_FILTER_ONLY"
        if overly:
            reasons.append("long leg concentrated in a few tickers")
        if is_risk:
            reasons.append("feature is a risk/exposure measure, not name-selection alpha")
    else:
        rec = "KEEP_FOR_MODEL"
        reasons.append("survives CI, permutation, and leave-one-year-out, and is "
                       "not a pure risk-exposure feature")

    return {
        "recommendation": rec,
        "effective_obs_est": _round(effective_obs, 2),
        "min_effective_obs": MIN_EFFECTIVE_OBS,
        "reasons": reasons,
    }


# --------------------------------------------------------------------------- #
# Per-survivor analysis
# --------------------------------------------------------------------------- #
def analyze_survivor(panel: "pd.DataFrame", fcol: str, horizon: int,
                     spy_fwd: "pd.Series", market_vol: "pd.Series",
                     phase2i_a: Dict[str, Any]) -> Dict[str, Any]:
    lcol = f"excess_{horizon}d"
    sub = _A._cell_frame(panel, fcol, lcol)
    ics = _A._per_date_rank_ic(sub, fcol, lcol)
    n_dates = int(len(ics))
    n_rows = int(len(sub))
    mean_ic = float(ics.mean()) if n_dates else None
    std_ic = float(ics.std(ddof=1)) if n_dates > 1 else None
    info_ratio = (mean_ic / std_ic
                  if (mean_ic is not None and std_ic not in (None, 0.0)
                      and not pd.isna(std_ic)) else None)
    overall_sign = _sign(mean_ic)

    yearly = _yearly_detail(ics, sub, fcol, lcol, overall_sign)
    quarterly = _quarterly_detail(ics, overall_sign)
    rolling = _rolling_6m(ics, overall_sign)
    sign_stability = _sign_stability(yearly, quarterly)
    regimes = _regimes(ics, sub, fcol, spy_fwd, market_vol)
    concentration, dominant = _concentration(panel, fcol, lcol)
    robustness = _robustness(ics, panel, fcol, lcol, horizon, overall_sign, dominant)
    recommendation = _recommend(mean_ic, n_dates, horizon, robustness,
                                concentration, fcol)

    regime_dependent = bool(
        regimes["spy_forward_return"] is not None
        and regimes["spy_forward_return"].get("sign_flips_across_regime"))

    return {
        "feature": fcol,
        "horizon_days": horizon,
        "edge_type": _classify_feature(fcol),
        "phase2i_a": phase2i_a,
        "overall": {
            "mean_rank_ic": _round(mean_ic),
            "std_rank_ic": _round(std_ic),
            "information_ratio": _round(info_ratio),
            "direction": ("bullish" if overall_sign > 0
                          else "bearish" if overall_sign < 0 else "flat"),
            "n_dates": n_dates,
            "n_rows": n_rows,
        },
        "stability": {
            "yearly": yearly,
            "quarterly": quarterly,
            "rolling_6m": rolling,
            "sign_stability": sign_stability,
        },
        "regimes": regimes,
        "regime_dependent": regime_dependent,
        "robustness": robustness,
        "concentration": concentration,
        "recommendation": recommendation,
    }


# --------------------------------------------------------------------------- #
# Interpretation roll-up
# --------------------------------------------------------------------------- #
def _interpretation(survivors: List[Dict[str, Any]]) -> Dict[str, Any]:
    recs = [s["recommendation"]["recommendation"] for s in survivors]
    n_total = len(survivors)
    n_keep_model = recs.count("KEEP_FOR_MODEL")
    n_keep_filter = recs.count("KEEP_AS_RISK_FILTER_ONLY")
    n_drop = recs.count("DROP")
    n_need = recs.count("NEED_MORE_DATA")

    by_type: Dict[str, List[str]] = {}
    for s in survivors:
        by_type.setdefault(s["edge_type"], []).append(s["feature"])

    regime_dependent = [s["feature"] for s in survivors if s["regime_dependent"]]
    concentrated = [s["feature"] for s in survivors
                    if s["concentration"] and s["concentration"]["overly_concentrated"]]

    # All Phase 2I-A survivors are strongest at 63d, so any surviving edge is a
    # long-horizon effect; a short-horizon model is not supported by this set.
    supports_long_horizon_model = all(s["horizon_days"] >= 63 for s in survivors)

    # Conservative gate: at least one feature genuinely model-worthy, and fewer
    # than half of the set dropped or starved of data.
    robust_enough_for_phase2j = bool(
        n_keep_model >= 1 and (n_drop + n_need) < (n_total / 2.0))

    dominant_character = max(by_type.items(), key=lambda kv: len(kv[1]))[0] if by_type else None

    return {
        "edge_character_dominant": dominant_character,
        "edge_types": by_type,
        "regime_dependent_features": regime_dependent,
        "overly_concentrated_features": concentrated,
        "recommendation_counts": {
            "KEEP_FOR_MODEL": n_keep_model,
            "KEEP_AS_RISK_FILTER_ONLY": n_keep_filter,
            "DROP": n_drop,
            "NEED_MORE_DATA": n_need,
        },
        "supports_long_horizon_model": supports_long_horizon_model,
        "supports_short_horizon_model": False,
        "robust_enough_for_phase2j": robust_enough_for_phase2j,
        "notes": [
            "The Phase 2I-A KEEP set is dominated by volatility / beta / "
            "correlation exposure; a surviving edge there is a risk premium that "
            "is expected to invert in a drawdown, not market-neutral alpha.",
            "63d forward windows overlap heavily, so the independent sample is far "
            "smaller than the raw date count; the block bootstrap and effective_obs "
            "guard are the honest read of significance.",
            "No production edge is claimed; this is observational research to decide "
            "whether Phase 2J long-horizon candidates are worth designing.",
        ],
    }


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_diagnostics(input_json: str = INPUT_2IA_JSON) -> Dict[str, Any]:
    survivor_list = load_survivors(input_json)
    scored, prices, run_summary = _A.load_inputs()
    labels = _A.build_forward_labels(prices)
    panel = _A.build_panel(scored, labels)
    panel = panel.assign(**{DATE_COL: pd.to_datetime(panel[DATE_COL])})

    horizons = sorted({s["best_horizon_days"] for s in survivor_list})
    spy_fwd = {h: _spy_forward_return(prices, h) for h in horizons}
    market_vol = _per_date_scalar(scored, MARKET_VOL_COL)

    analyzed: List[Dict[str, Any]] = []
    for s in survivor_list:
        fcol = s["feature"]
        h = s["best_horizon_days"]
        phase2i_a = {
            "mean_rank_ic": s["phase2i_a_mean_rank_ic"],
            "information_ratio": s["phase2i_a_information_ratio"],
            "direction": s["phase2i_a_direction"],
        }
        analyzed.append(analyze_survivor(panel, fcol, h, spy_fwd[h],
                                         market_vol, phase2i_a))

    interpretation = _interpretation(analyzed)

    panel_dates = pd.to_datetime(panel[DATE_COL])
    return {
        "phase": PHASE,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "phase2i_a_json": input_json,
            "scored_csv": SCORED_CSV,
            "price_history_csv": PRICE_HISTORY_CSV,
            "run_summary_json": RUN_SUMMARY_JSON,
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
        "config": {
            "survivor_horizons": horizons,
            "bootstrap_n": BOOTSTRAP_N,
            "permutation_n": PERMUTATION_N,
            "ci_alpha": CI_ALPHA,
            "perm_alpha": PERM_ALPHA,
            "seed": SEED,
            "rolling_window_sessions": ROLL_WINDOW_SESSIONS,
            "min_effective_obs": MIN_EFFECTIVE_OBS,
            "concentration_top3_threshold": CONCENTRATION_TOP3_THRESHOLD,
            "risk_features": sorted(RISK_FEATURES),
            "market_vol_col": MARKET_VOL_COL,
            "recommendation_rule": (
                "NEED_MORE_DATA if n_dates/horizon < min_effective_obs; else DROP if "
                "the bootstrap mean-IC CI includes zero OR the sign-flip permutation "
                "null is not rejected OR IC sign is unstable leaving any year out; "
                "else KEEP_AS_RISK_FILTER_ONLY if overly concentrated or a risk/exposure "
                "feature; else KEEP_FOR_MODEL."),
        },
        "provenance": {
            "source": run_summary.get("source"),
            "date_start": run_summary.get("date_start"),
            "date_end": run_summary.get("date_end"),
            "price_row_count": run_summary.get("row_count"),
            "ticker_count": run_summary.get("ticker_count"),
        },
        "panel": {
            "n_rows": int(len(panel)),
            "n_tickers": int(panel[TICKER_COL].nunique()),
            "n_dates": int(panel[DATE_COL].nunique()),
            "date_start": panel_dates.min().date().isoformat() if len(panel) else None,
            "date_end": panel_dates.max().date().isoformat() if len(panel) else None,
        },
        "survivors_tested": [s["feature"] for s in survivor_list],
        "survivors": analyzed,
        "interpretation": interpretation,
    }


def write_diagnostics(diagnostics: Dict[str, Any],
                      path: str = DIAGNOSTICS_JSON) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(diagnostics, f, indent=2, allow_nan=False)


def run(output_path: str = DIAGNOSTICS_JSON,
        input_json: str = INPUT_2IA_JSON) -> Dict[str, Any]:
    diagnostics = build_diagnostics(input_json)
    write_diagnostics(diagnostics, output_path)
    return diagnostics


def main() -> int:
    t0 = time.perf_counter()
    d = run()
    elapsed = time.perf_counter() - t0
    p = d["panel"]
    print(f"[phase2i-b] panel rows / tickers / dates : {p['n_rows']} / "
          f"{p['n_tickers']} / {p['n_dates']}")
    print(f"[phase2i-b] window                       : {p['date_start']} -> "
          f"{p['date_end']}")
    print(f"[phase2i-b] survivors tested ({len(d['survivors'])}) :")
    for s in d["survivors"]:
        o = s["overall"]
        r = s["recommendation"]
        b = s["robustness"]["bootstrap_mean_ic_ci"]
        perm = s["robustness"]["permutation_null"]
        print(f"             {s['feature']:<28} h={s['horizon_days']:>2}d  "
              f"IC={o['mean_rank_ic']}  CI=[{b['ci_low']},{b['ci_high']}]  "
              f"p={perm['p_value']}  -> {r['recommendation']}")
    it = d["interpretation"]
    print(f"[phase2i-b] recommendation counts        : {it['recommendation_counts']}")
    print(f"[phase2i-b] dominant edge character      : {it['edge_character_dominant']}")
    print(f"[phase2i-b] supports long-horizon model  : {it['supports_long_horizon_model']}")
    print(f"[phase2i-b] robust enough for Phase 2J   : {it['robust_enough_for_phase2j']}")
    print(f"[phase2i-b] diagnostics written          : {DIAGNOSTICS_JSON}")
    print(f"[phase2i-b] elapsed seconds              : {elapsed:.2f}")
    print("[phase2i-b] research-only diagnostics; safety flags emitted in JSON.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
