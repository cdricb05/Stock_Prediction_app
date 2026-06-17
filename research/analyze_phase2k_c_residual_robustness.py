"""Phase 2K-C Residual Alpha Robustness Validation (offline, read-only).

Phase 2K-B neutralized the failed 63-session volatility / beta / correlation risk
premium and asked whether any candidate feature still ranks a *residual* forward
return. A handful cleared the IC floor with a stable yearly sign and were flagged
KEEP_FOR_ROBUSTNESS_TEST. A surviving rank IC on a ~40-name, single-regime
2023-2026 export is a lead, not an edge -- exactly the kind of apparent signal the
Phase 2I-B battery already demolished once. This phase runs that same battery on
the Phase 2K-B residual candidates to decide whether any of them is robust enough
to scope walk-forward model research, or whether the disciplined next move is more
data / a new hypothesis family.

Method, per Phase 2K-B residual candidate (validated at its best 2K-B horizon):
  1. Read the Phase 2K-B output and take the candidates from
     keep_drop.keep_for_robustness_test, with each candidate's best horizon.
  2. Rebuild the *same* residual labels used in Phase 2K-B by importing the Phase
     2K-B analyzer and calling its forward-label / panel / residualization
     functions (per-date cross-sectional OLS of forward excess-vs-SPY return on the
     trailing risk controls, with a cross-sectional demean fallback). Importing
     only binds functions; Phase 2K-B is not executed as a side effect.
  3. Measure stability (yearly / quarterly / rolling-6-month residual IC, sign
     stability, top-minus-bottom spread and top-decile residual hit rate by year).
  4. Stress the residual edge: a moving-block bootstrap CI for the mean residual
     rank IC (block length = horizon, respecting overlapping windows), a sign-flip
     permutation null, leave-one-year-out and leave-one-ticker-out stability, a
     market-regime split (SPY-up vs SPY-down forward windows, high vs low market
     volatility, and feature cross-sectional dispersion), and a long-leg ticker
     concentration check.
  5. Recommend per candidate -- KEEP_FOR_MODEL_RESEARCH / KEEP_AS_RESEARCH_LEAD_ONLY
     / DROP / NEED_MORE_DATA -- under a conservative rule: a candidate may be
     KEEP_FOR_MODEL_RESEARCH only if the bootstrap CI excludes zero, the permutation
     null is significant, the leave-one-year-out sign is stable, the long leg is not
     overly concentrated, the edge does not depend on a single market regime, and
     there are enough effective observations. Even then it only permits the next
     research phase to scope walk-forward validation -- it is not permission to
     train or deploy a model.

This phase is research only. It reads the Phase 2K-B / 2K-A / 2J-A JSON summaries
and the local Phase 2G real-data artifacts and writes exactly one diagnostics JSON.
It performs no infrastructure action and mutates no datastore; the machine-readable
safety flags are emitted in the diagnostics JSON, and the full guardrail rationale
lives in docs/phase2k_c_residual_robustness_v1.md, not in this source. No model
candidate is created here.
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

PHASE = "2K-C"

_HERE = os.path.dirname(os.path.abspath(__file__))
_OUTPUT_DIR = os.path.join("research", "output")

# The Phase 2K-B analyzer is the single source of truth for the residual-label
# construction and the vectorized per-date rank-IC / spread helpers. Import it by
# path so this phase rebuilds *exactly* the same residual labels rather than
# re-implementing them. Importing only binds functions and constants -- it runs
# nothing, so Phase 2K-B is not executed as a side effect.
_ANALYZER_2KB = os.path.join(_HERE, "analyze_phase2k_b_residual_signal.py")


def _load_2kb():
    spec = importlib.util.spec_from_file_location(
        "phase2k_b_analyzer", _ANALYZER_2KB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_B = _load_2kb()

# Reused, single-sourced from the Phase 2K-B analyzer.
_round = _B._round
_sign = _B._sign
DATE_COL = _B.DATE_COL
TICKER_COL = _B.TICKER_COL
SPY = _B.SPY
CONTROL_FEATURES = _B.CONTROL_FEATURES
HORIZONS = _B.HORIZONS
MIN_NAMES_DECILE = _B.MIN_NAMES_DECILE
RESID_MIN_NAMES = _B.RESID_MIN_NAMES
RANK_IC_FLOOR = _B.RANK_IC_FLOOR

# Inputs (read-only). The 2G artifacts are read through the 2K-B loader; the
# Phase 2K-B diagnostics JSON is read directly to discover the residual candidate
# shortlist and each candidate's best horizon.
INPUT_2KB_JSON = os.path.join(_OUTPUT_DIR, "phase2k_b_residual_signal.json")
INPUT_2KA_JSON = _B.INPUT_2KA_JSON
INPUT_2J_JSON = _B.INPUT_2J_JSON
SCORED_CSV = _B.SCORED_CSV
PRICE_HISTORY_CSV = _B.PRICE_HISTORY_CSV
RUN_SUMMARY_JSON = _B.RUN_SUMMARY_JSON

# The single output this analyzer is allowed to write.
ROBUSTNESS_JSON = os.path.join(_OUTPUT_DIR, "phase2k_c_residual_robustness.json")

# Market-level regime input already present in the scored CSV.
MARKET_VOL_COL = "spy_realized_vol_21d"

# Robustness / recommendation knobs (the same battery used in Phase 2I-B).
BOOTSTRAP_N = 1000          # moving-block bootstrap resamples for the mean-IC CI
PERMUTATION_N = 1000        # sign-flip permutations for the IC significance null
CI_ALPHA = 0.05             # 95% bootstrap CI
PERM_ALPHA = 0.05           # permutation significance threshold
SEED = 12345                # deterministic RandomState seed (per candidate)
ROLL_WINDOW_SESSIONS = 126  # ~6 months of sessions for the rolling-IC stability view
MIN_EFFECTIVE_OBS = 8.0     # n_dates / horizon floor below which we say NEED_MORE_DATA
CONCENTRATION_TOP3_THRESHOLD = 0.50  # top-3 long-leg share above which -> concentrated

# Recommendation vocabulary (the only values recommendation_summary may emit).
REC_MODEL = "KEEP_FOR_MODEL_RESEARCH"
REC_LEAD = "KEEP_AS_RESEARCH_LEAD_ONLY"
REC_DROP = "DROP"
REC_NEED = "NEED_MORE_DATA"


# --------------------------------------------------------------------------- #
# Read-only helpers
# --------------------------------------------------------------------------- #
def load_candidates(input_json: str = INPUT_2KB_JSON) -> List[Dict[str, Any]]:
    """The Phase 2K-B residual shortlist, read at runtime.

    Candidates are exactly keep_drop.keep_for_robustness_test; each carries its
    best Phase 2K-B horizon and the residual IC / direction measured there so this
    phase validates the same cell the lead was raised on.
    """
    twokb = _B._read_json(input_json)
    keep = twokb.get("keep_drop", {}).get("keep_for_robustness_test", [])
    features = twokb.get("features", {})
    out: List[Dict[str, Any]] = []
    for f in keep:
        blk = features.get(f, {})
        out.append({
            "feature": f,
            "best_horizon_days": int(blk.get("best_horizon_days") or 0),
            "phase2k_b_best_mean_rank_ic": blk.get("best_mean_rank_ic"),
            "phase2k_b_best_information_ratio": blk.get("best_information_ratio"),
            "phase2k_b_direction": blk.get("direction"),
        })
    return out


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


def build_residual_panel(scored: "pd.DataFrame", prices: "pd.DataFrame"
                         ) -> Tuple["pd.DataFrame", Dict[str, Any]]:
    """Rebuild the Phase 2K-B panel and attach a residual label for every horizon.

    Uses the imported Phase 2K-B functions verbatim, so the residual labels are
    identical to those Phase 2K-B raised the candidates on: forward excess-vs-SPY
    return, per-date cross-sectionally neutralized against the trailing risk
    controls via OLS, with a cross-sectional demean fallback. No look-ahead: the
    controls are trailing, the label is forward, and each fit is same-date only.
    """
    labels = _B.build_forward_labels(prices)
    panel = _B.build_panel(scored, labels)
    fit_counts: Dict[str, Any] = {}
    for h in HORIZONS:
        res = _B.residualize_label(panel, f"excess_{h}d", CONTROL_FEATURES)
        panel[f"resid_{h}d"] = res["resid"]
        fit_counts[str(h)] = {"ols_dates": res["ols_dates"],
                              "fallback_dates": res["fallback_dates"]}
    panel = panel.assign(**{DATE_COL: pd.to_datetime(panel[DATE_COL])})
    return panel, fit_counts


# --------------------------------------------------------------------------- #
# Stability views (per candidate, off the same residual per-date IC series)
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
        spread, hit = _B._spread_and_hit_rate(suby, fcol, lcol)
        out.append({
            "year": int(y),
            "rank_ic": _round(mean),
            "information_ratio": _round(ir),
            "n_dates": int(len(yic)),
            "n_rows": int(len(suby)),
            "sign_matches_overall": bool(overall_sign and _sign(mean) == overall_sign),
            "top_minus_bottom_spread": _round(spread),
            "top_decile_residual_hit_rate": _round(hit),
        })
    return out


def _quarterly_detail(ics: "pd.Series", overall_sign: int) -> List[Dict[str, Any]]:
    rows = _B._calendar_ic(ics, "quarter")
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
    out = {"median_split": _round(med),
           "high": _bucket_stat(hi), "low": _bucket_stat(lo)}
    out["sign_flips_across_regime"] = bool(
        out["high"]["rank_ic"] is not None
        and out["low"]["rank_ic"] is not None
        and _sign(out["high"]["rank_ic"]) != _sign(out["low"]["rank_ic"])
        and _sign(out["high"]["rank_ic"]) != 0
        and _sign(out["low"]["rank_ic"]) != 0)
    return out


def _regimes(ics: "pd.Series", sub: "pd.DataFrame", fcol: str,
             spy_fwd: "pd.Series", market_vol: "pd.Series") -> Dict[str, Any]:
    disp = sub.groupby(DATE_COL)[fcol].std(ddof=1).dropna()
    disp.index = pd.to_datetime(disp.index)
    return {
        "spy_forward_return": _regime_split(ics, spy_fwd, "sign"),
        "market_volatility_21d": _regime_split(ics, market_vol, "median"),
        "feature_cross_sectional_dispersion": _regime_split(ics, disp, "median"),
    }


def _regime_dependent(regimes: Dict[str, Any]) -> bool:
    """A candidate is regime-dependent if its residual IC sign flips between the
    SPY-up and SPY-down forward windows -- the edge then lives in only one market
    direction rather than holding across regimes."""
    spy = regimes.get("spy_forward_return")
    return bool(spy is not None and spy.get("sign_flips_across_regime"))


# --------------------------------------------------------------------------- #
# Robustness primitives (the same moving-block bootstrap / sign-flip permutation /
# leave-one-out battery used in Phase 2I-B, applied to the residual label)
# --------------------------------------------------------------------------- #
def _block_bootstrap_ci(ic_values: "np.ndarray", block_len: int, n_boot: int,
                        rng: "np.random.RandomState", alpha: float
                        ) -> Tuple[Optional[float], Optional[float]]:
    """Moving-block bootstrap CI for the mean per-date residual IC.

    Block length = the forward horizon, so the highly autocorrelated overlapping
    forward windows are resampled in contiguous blocks rather than as if
    independent (an honest read given heavily overlapping 21d / 63d labels).
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
    """Two-sided sign-flip permutation p for H0: per-date residual IC centered at 0."""
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
        cell_excl = _B._cell_frame(sub_excl, fcol, lcol)
        ics_excl = _B._per_date_rank_ic(cell_excl, fcol, lcol)
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
    mean_resid_top = top.groupby(TICKER_COL)[lcol].mean()
    contributors = [{
        "ticker": str(tkr),
        "top_quintile_share": _round(float(shares[tkr])),
        "n_dates_in_top_quintile": int(counts[tkr]),
        "mean_residual_in_top": _round(float(mean_resid_top[tkr])),
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
# Recommendation (conservative gate)
# --------------------------------------------------------------------------- #
def _recommend(mean_ic: Optional[float], n_dates: int, horizon: int,
               robustness: Dict[str, Any], concentration: Optional[Dict[str, Any]],
               regime_dependent: bool) -> Dict[str, Any]:
    """KEEP_FOR_MODEL_RESEARCH only when every robustness condition holds.

    Conditions: bootstrap CI excludes zero; permutation null significant;
    leave-one-year-out sign stable; long leg not overly concentrated; not
    regime-dependent; and enough effective (overlap-discounted) observations.
    """
    effective_obs = (n_dates / horizon) if horizon else 0.0
    ci_excludes_zero = robustness["bootstrap_mean_ic_ci"]["excludes_zero"]
    perm_significant = robustness["permutation_null"]["significant"]
    loyo_stable = robustness["leave_one_year_out"]["sign_stable"]
    overly = bool(concentration and concentration["overly_concentrated"])

    reasons: List[str] = []
    if mean_ic is None or n_dates < 2:
        rec = REC_NEED
        reasons.append("insufficient per-date observations")
    elif effective_obs < MIN_EFFECTIVE_OBS:
        rec = REC_NEED
        reasons.append(
            f"effective_obs {effective_obs:.1f} < {MIN_EFFECTIVE_OBS} "
            "(overlapping forward windows thin the independent sample)")
    elif not (ci_excludes_zero and perm_significant and loyo_stable):
        rec = REC_DROP
        if not ci_excludes_zero:
            reasons.append("bootstrap CI for mean residual IC includes zero")
        if not perm_significant:
            reasons.append("sign-flip permutation null not rejected at alpha")
        if not loyo_stable:
            reasons.append("residual IC sign not stable leaving any single year out")
    elif overly or regime_dependent:
        rec = REC_LEAD
        if overly:
            reasons.append("residual long leg concentrated in a few tickers")
        if regime_dependent:
            reasons.append("residual IC sign flips across the SPY up/down regime split")
    else:
        rec = REC_MODEL
        reasons.append("survives bootstrap CI, permutation null, and "
                       "leave-one-year-out, is not overly concentrated, and does "
                       "not depend on a single market regime")

    return {
        "recommendation": rec,
        "effective_obs_est": _round(effective_obs, 2),
        "min_effective_obs": MIN_EFFECTIVE_OBS,
        "ci_excludes_zero": ci_excludes_zero,
        "permutation_significant": perm_significant,
        "leave_one_year_out_sign_stable": loyo_stable,
        "overly_concentrated": overly,
        "regime_dependent": regime_dependent,
        "reasons": reasons,
    }


# --------------------------------------------------------------------------- #
# Per-candidate analysis
# --------------------------------------------------------------------------- #
def analyze_candidate(panel: "pd.DataFrame", meta: Dict[str, Any],
                      spy_fwd: "pd.Series", market_vol: "pd.Series"
                      ) -> Dict[str, Any]:
    fcol = meta["feature"]
    horizon = meta["best_horizon_days"]
    lcol = f"resid_{horizon}d"
    sub = _B._cell_frame(panel, fcol, lcol)
    ics = _B._per_date_rank_ic(sub, fcol, lcol)
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
    regime_dependent = _regime_dependent(regimes)
    concentration, dominant = _concentration(panel, fcol, lcol)
    robustness = _robustness(ics, panel, fcol, lcol, horizon, overall_sign, dominant)
    recommendation = _recommend(mean_ic, n_dates, horizon, robustness,
                                concentration, regime_dependent)

    # Optional context: the residual mean rank IC at every horizon, not just best.
    all_horizons: Dict[str, Any] = {}
    for h in HORIZONS:
        lc = f"resid_{h}d"
        s = _B._cell_frame(panel, fcol, lc)
        ic = _B._per_date_rank_ic(s, fcol, lc)
        all_horizons[str(h)] = {
            "mean_rank_ic": _round(float(ic.mean()) if len(ic) else None),
            "n_dates": int(len(ic)),
        }

    return {
        "feature": fcol,
        "horizon_days": horizon,
        "phase2k_b": {
            "best_mean_rank_ic": meta["phase2k_b_best_mean_rank_ic"],
            "best_information_ratio": meta["phase2k_b_best_information_ratio"],
            "direction": meta["phase2k_b_direction"],
        },
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
        "all_horizons_residual_ic": all_horizons,
        "regimes": regimes,
        "regime_dependent": regime_dependent,
        "robustness": robustness,
        "concentration": concentration,
        "recommendation": recommendation,
    }


# --------------------------------------------------------------------------- #
# Interpretation roll-up
# --------------------------------------------------------------------------- #
def _interpretation(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    recs = [c["recommendation"]["recommendation"] for c in candidates]
    n_total = len(candidates)
    n_model = recs.count(REC_MODEL)
    n_lead = recs.count(REC_LEAD)
    n_drop = recs.count(REC_DROP)
    n_need = recs.count(REC_NEED)

    survivors_model = [c["feature"] for c in candidates
                       if c["recommendation"]["recommendation"] == REC_MODEL]
    leads = [c["feature"] for c in candidates
             if c["recommendation"]["recommendation"] == REC_LEAD]
    concentrated = [c["feature"] for c in candidates
                    if c["concentration"] and c["concentration"]["overly_concentrated"]]
    regime_dependent = [c["feature"] for c in candidates if c["regime_dependent"]]

    any_model = n_model >= 1
    any_lead = n_lead >= 1

    if any_model:
        narrative = (
            "At least one Phase 2K-B residual candidate survived the full "
            "robustness battery (bootstrap CI excluding zero, significant "
            "permutation null, stable leave-one-year-out sign, acceptable "
            "concentration, and no single-regime dependence): "
            + ", ".join(survivors_model)
            + ". This is robust enough to scope walk-forward residual model "
            "research -- it is NOT permission to train or deploy a model, and no "
            "model candidate is created here. No production edge is claimed.")
    elif any_lead:
        narrative = (
            "No residual candidate cleared the full robustness gate, but at least "
            "one is a defensible research lead that is either concentrated in a few "
            "tickers or regime-dependent: " + ", ".join(leads)
            + ". The honest next step is to extend data / broaden the universe "
            "before any model research, not to train a model. No production edge is "
            "claimed.")
    else:
        narrative = (
            "No Phase 2K-B residual candidate survived robustness on the current "
            "~40-name, single-regime 2023-2026 export; every candidate either failed "
            "the bootstrap / permutation / leave-one-year-out significance checks or "
            "lacked enough effective observations. There is no residual alpha to "
            "model here; the disciplined next move is data acquisition for new "
            "hypothesis families, not model training. No production edge is claimed.")

    return {
        "any_candidate_survives_robustness": any_model,
        "survivors_keep_for_model_research": survivors_model,
        "research_leads_only": leads,
        "edge_concentrated_in_few_tickers": bool(concentrated),
        "concentrated_features": concentrated,
        "edge_regime_dependent": bool(regime_dependent),
        "regime_dependent_features": regime_dependent,
        "recommendation_counts": {
            REC_MODEL: n_model,
            REC_LEAD: n_lead,
            REC_DROP: n_drop,
            REC_NEED: n_need,
        },
        "n_candidates_tested": n_total,
        "narrative": narrative,
        "production_edge_claimed": False,
    }


def _recommended_next_phase(interpretation: Dict[str, Any]) -> Dict[str, Any]:
    counts = interpretation["recommendation_counts"]
    if counts[REC_MODEL] >= 1:
        return {
            "phase": "2L-A",
            "title": "Walk-Forward Residual Model Research Design",
            "purpose": "At least one residual candidate survived robustness. The "
                       "next phase scopes a walk-forward (expanding/rolling, "
                       "out-of-sample) validation design for the surviving residual "
                       "signal(s) before any model candidate is built. Surviving "
                       "robustness is not permission to train or deploy; no model "
                       "candidate is created until walk-forward validation passes.",
        }
    if counts[REC_LEAD] >= 1:
        return {
            "phase": "2K-D",
            "title": "Extend Data / Broaden Universe Before Model Research",
            "purpose": "No candidate cleared the full robustness gate, but a "
                       "research lead remains that is concentrated or "
                       "regime-dependent. Acquire longer price history and a broader "
                       "point-in-time universe so the lead can be retested out of a "
                       "single regime before any model research. This is data work, "
                       "not model training; no model candidate is created.",
        }
    return {
        "phase": "2K-D",
        "title": "Alpha Data Acquisition for New Hypothesis Families",
        "purpose": "No residual candidate survived robustness on the current data. "
                   "Acquire longer price history, a broader point-in-time universe, "
                   "and point-in-time fundamentals/estimates to test the remaining "
                   "Phase 2K-A backlog families. This is data acquisition, not model "
                   "training; no model candidate is created.",
    }


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_diagnostics(input_json: str = INPUT_2KB_JSON) -> Dict[str, Any]:
    candidates_meta = load_candidates(input_json)
    scored, prices, run_summary, _phase2i_a, phase2j = _B.load_inputs()
    panel, fit_counts = build_residual_panel(scored, prices)

    horizons_used = sorted({c["best_horizon_days"] for c in candidates_meta})
    spy_fwd = {h: _spy_forward_return(prices, h) for h in horizons_used}
    market_vol = _per_date_scalar(scored, MARKET_VOL_COL)

    analyzed: List[Dict[str, Any]] = []
    for meta in candidates_meta:
        h = meta["best_horizon_days"]
        analyzed.append(analyze_candidate(panel, meta, spy_fwd.get(h, pd.Series(dtype=float)),
                                          market_vol))

    # Top-level views keyed by candidate feature (asserted by the tests).
    robustness = {c["feature"]: c["robustness"] for c in analyzed}
    concentration = {c["feature"]: c["concentration"] for c in analyzed}
    regime_tests = {c["feature"]: c["regimes"] for c in analyzed}

    by_feature_rec = {c["feature"]: c["recommendation"]["recommendation"]
                      for c in analyzed}
    buckets = {REC_MODEL: [], REC_LEAD: [], REC_DROP: [], REC_NEED: []}
    for f, rec in by_feature_rec.items():
        buckets[rec].append(f)
    recommendation_summary = {
        "by_feature": by_feature_rec,
        "keep_for_model_research": sorted(buckets[REC_MODEL]),
        "keep_as_research_lead_only": sorted(buckets[REC_LEAD]),
        "drop": sorted(buckets[REC_DROP]),
        "need_more_data": sorted(buckets[REC_NEED]),
        "counts": {
            REC_MODEL: len(buckets[REC_MODEL]),
            REC_LEAD: len(buckets[REC_LEAD]),
            REC_DROP: len(buckets[REC_DROP]),
            REC_NEED: len(buckets[REC_NEED]),
        },
        "allowed_values": [REC_MODEL, REC_LEAD, REC_DROP, REC_NEED],
        "conservative_rule": (
            "KEEP_FOR_MODEL_RESEARCH requires ALL of: bootstrap mean-IC CI excludes "
            "zero; sign-flip permutation null significant; leave-one-year-out sign "
            "stable; long leg not overly concentrated; not regime-dependent; and "
            "effective_obs (n_dates/horizon) >= min_effective_obs. Else NEED_MORE_DATA "
            "if the sample is too thin; DROP if significance fails; "
            "KEEP_AS_RESEARCH_LEAD_ONLY if significant but concentrated or "
            "regime-dependent. KEEP_FOR_MODEL_RESEARCH is not permission to train or "
            "deploy a model."),
    }

    interpretation = _interpretation(analyzed)
    recommended_next_phase = _recommended_next_phase(interpretation)

    panel_dates = pd.to_datetime(panel[DATE_COL])
    return {
        "phase": PHASE,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "phase2k_b_json": input_json,
            "phase2k_a_json": INPUT_2KA_JSON,
            "phase2j_json": INPUT_2J_JSON,
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
            "bootstrap_n": BOOTSTRAP_N,
            "permutation_n": PERMUTATION_N,
            "ci_alpha": CI_ALPHA,
            "perm_alpha": PERM_ALPHA,
            "seed": SEED,
            "rolling_window_sessions": ROLL_WINDOW_SESSIONS,
            "min_effective_obs": MIN_EFFECTIVE_OBS,
            "concentration_top3_threshold": CONCENTRATION_TOP3_THRESHOLD,
            "rank_ic_floor": RANK_IC_FLOOR,
            "market_vol_col": MARKET_VOL_COL,
            "candidate_horizons": horizons_used,
        },
        "residualization_config": {
            "label_base": "forward_excess_return_vs_spy",
            "horizons": HORIZONS,
            "controls": CONTROL_FEATURES,
            "method": "per-date cross-sectional OLS (usable controls + intercept); "
                      "residual = forward excess label - fitted (identical to "
                      "Phase 2K-B, via the imported Phase 2K-B functions)",
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
        "provenance": {
            "source": run_summary.get("source"),
            "date_start": run_summary.get("date_start"),
            "date_end": run_summary.get("date_end"),
            "price_row_count": run_summary.get("row_count"),
            "ticker_count": run_summary.get("ticker_count"),
            "phase2j_phase": phase2j.get("phase"),
            "phase2j_go_no_go": phase2j.get("decision", {}).get("go_no_go"),
        },
        "panel": {
            "n_rows": int(len(panel)),
            "n_tickers": int(panel[TICKER_COL].nunique()),
            "n_dates": int(panel[DATE_COL].nunique()),
            "date_start": panel_dates.min().date().isoformat() if len(panel) else None,
            "date_end": panel_dates.max().date().isoformat() if len(panel) else None,
        },
        "candidates_tested": sorted(c["feature"] for c in candidates_meta),
        "candidates": analyzed,
        "robustness": robustness,
        "concentration": concentration,
        "regime_tests": regime_tests,
        "recommendation_summary": recommendation_summary,
        "interpretation": interpretation,
        "recommended_next_phase": recommended_next_phase,
    }


def write_diagnostics(diagnostics: Dict[str, Any],
                      path: str = ROBUSTNESS_JSON) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(diagnostics, f, indent=2, allow_nan=False)


def run(output_path: str = ROBUSTNESS_JSON,
        input_json: str = INPUT_2KB_JSON) -> Dict[str, Any]:
    diagnostics = build_diagnostics(input_json)
    write_diagnostics(diagnostics, output_path)
    return diagnostics


def main() -> int:
    t0 = time.perf_counter()
    d = run()
    elapsed = time.perf_counter() - t0
    p = d["panel"]
    rs = d["recommendation_summary"]
    it = d["interpretation"]
    print(f"[phase2k-c] panel rows / tickers / dates : {p['n_rows']} / "
          f"{p['n_tickers']} / {p['n_dates']}")
    print(f"[phase2k-c] window                       : {p['date_start']} -> "
          f"{p['date_end']}")
    print(f"[phase2k-c] candidates tested ({len(d['candidates'])}) :")
    for c in d["candidates"]:
        o = c["overall"]
        r = c["recommendation"]
        b = c["robustness"]["bootstrap_mean_ic_ci"]
        perm = c["robustness"]["permutation_null"]
        print(f"             {c['feature']:<28} h={c['horizon_days']:>2}d  "
              f"residIC={o['mean_rank_ic']}  CI=[{b['ci_low']},{b['ci_high']}]  "
              f"p={perm['p_value']}  -> {r['recommendation']}")
    print(f"[phase2k-c] recommendation counts        : {rs['counts']}")
    print(f"[phase2k-c] any candidate survives       : "
          f"{it['any_candidate_survives_robustness']}")
    print(f"[phase2k-c] edge concentrated / regime   : "
          f"{it['edge_concentrated_in_few_tickers']} / "
          f"{it['edge_regime_dependent']}")
    print(f"[phase2k-c] recommended next phase       : "
          f"{d['recommended_next_phase']['phase']} "
          f"({d['recommended_next_phase']['title']})")
    print(f"[phase2k-c] diagnostics written          : {ROBUSTNESS_JSON}")
    print(f"[phase2k-c] elapsed seconds              : {elapsed:.2f}")
    print("[phase2k-c] research-only diagnostics; safety flags emitted in JSON.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
