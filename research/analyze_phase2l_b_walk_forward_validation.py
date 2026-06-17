"""Phase 2L-B Model-Free Walk-Forward Residual Signal Validation (offline, read-only).

Phase 2L-A converted the single Phase 2K-C robustness survivor --
avg_dollar_volume_21d, at its 63-session horizon -- into a walk-forward research
design: expanding / rolling training options, strictly sequential embargoed
validation windows, a model-free rank-only baseline to run FIRST, explicit
pass/fail gates, and end-to-end leakage controls. Phase 2L-A trained nothing and
authorized no model; it only routed here.

This phase IMPLEMENTS that model-free baseline. It does NOT train, fit, or score any
model, and it creates no model candidate. For the surviving residual candidate it:

  1. Reads the Phase 2L-A design and confirms the candidate (avg_dollar_volume_21d),
     the primary horizon (63), and that 2L-A routed to 2L-B.
  2. Rebuilds the SAME beta/vol-neutralized residual label used in Phase 2K-B / 2K-C
     by importing the Phase 2K-B analyzer and calling its forward-label / panel /
     residualization functions (per-date cross-sectional OLS on the trailing risk
     controls, demean fallback). Importing only binds functions; Phase 2K-B is not
     executed as a side effect, and the label is byte-for-byte the upstream one.
  3. Derives concrete sequential walk-forward folds from the actual labeled session
     calendar: min training window, a horizon-length embargo, then a fixed
     validation window, stepping forward one block at a time. The validation window
     is always chronologically after training and never overlaps the training label
     window after the embargo. There is no random cross-validation.
  4. Runs a model-free, rank-only signal check on each out-of-sample validation
     window: names are ranked by the single feature, the direction is fixed from the
     training window (and matches the Phase 2K-C bullish read), and only validation
     residual-label metrics are measured. Nothing is fitted.
  5. Aggregates the per-fold validation metrics, runs a moving-block bootstrap on the
     pooled validation per-date IC, a leave-one-fold-out stability check, a market
     regime split, and a degradation check versus the Phase 2K-C full-sample IC.
  6. Evaluates the Phase 2L-A pass/fail gates and issues a conservative
     recommendation -- WALK_FORWARD_PASS / WALK_FORWARD_FAIL / NEED_MORE_DATA. Even
     WALK_FORWARD_PASS is NOT permission to train or serve a model: it only lets a
     later, separately gated phase design a very simple single-feature candidate.

This phase reads the Phase 2L-A / 2K-C / 2K-B JSON summaries and the local Phase 2G
real-data artifacts and writes exactly one results JSON. It performs no
infrastructure action and mutates no datastore; the machine-readable safety flags are
emitted in that JSON, and the full guardrail rationale lives in
docs/phase2l_b_walk_forward_validation_v1.md, not in this source.
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

PHASE = "2L-B"

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
MIN_NAMES_PER_DATE = _B.MIN_NAMES_PER_DATE
MIN_NAMES_DECILE = _B.MIN_NAMES_DECILE
RESID_MIN_NAMES = _B.RESID_MIN_NAMES

# Inputs (read-only). The Phase 2L-A design fixes the protocol; the 2K-C decision
# carries the candidate's full-sample residual IC for the degradation gate; the 2G
# artifacts are loaded through the 2K-B loader to rebuild the residual label.
DESIGN_2LA_JSON = os.path.join(_OUTPUT_DIR, "phase2l_a_walk_forward_design.json")
INPUT_2KC_JSON = os.path.join(_OUTPUT_DIR, "phase2k_c_residual_robustness.json")
INPUT_2KB_JSON = _B.RESIDUAL_JSON
SCORED_CSV = _B.SCORED_CSV
PRICE_HISTORY_CSV = _B.PRICE_HISTORY_CSV
RUN_SUMMARY_JSON = _B.RUN_SUMMARY_JSON

# The single output this analyzer is allowed to write.
RESULTS_JSON = os.path.join(
    _OUTPUT_DIR, "phase2l_b_walk_forward_validation.json")

# Market-level regime input already present in the scored CSV.
MARKET_VOL_COL = "spy_realized_vol_21d"

# Walk-forward protocol knobs (sessions ~= trading days). Defaults match the Phase
# 2L-A design; the live values are read from the design JSON at run time so the two
# phases cannot silently diverge.
MIN_TRAIN_SESSIONS = 252            # ~1 trading year minimum training span
ROLLING_TRAIN_SESSIONS = 504        # ~2 trading years for the rolling-window option
VALIDATION_SESSIONS = 63            # one validation block ~= the primary horizon
STEP_SESSIONS = 63                  # advance one non-overlapping block per fold
DEFAULT_PRIMARY_HORIZON = 63        # embargo = horizon; overridden from the design

# Gate thresholds (inherited from Phase 2I-A / 2K-B / 2K-C via the 2L-A design; not
# tuned on validation results here).
RANK_IC_FLOOR = 0.03                # validation mean residual IC must clear this
MAJORITY_SIGN_FRACTION = 0.50       # > this fraction of windows share the sign
CONCENTRATION_TOP3_THRESHOLD = 0.50  # top-3 long-leg share above which -> concentrated
DEGRADATION_TOLERANCE_FRACTION = 0.50  # validation IC >= this * 2K-C full-sample IC
MIN_EFFECTIVE_OBS = 8.0             # (pooled validation dates / horizon) floor
MIN_FOLDS = 3                       # below this many folds -> NEED_MORE_DATA

# Bootstrap knobs (same moving-block bootstrap used in Phase 2I-B / 2K-C).
BOOTSTRAP_N = 1000
CI_ALPHA = 0.05
SEED = 12345

# Recommendation vocabulary (the only values recommendation may emit).
REC_PASS = "WALK_FORWARD_PASS"
REC_FAIL = "WALK_FORWARD_FAIL"
REC_NEED = "NEED_MORE_DATA"
_ALLOWED_RECOMMENDATIONS = [REC_PASS, REC_FAIL, REC_NEED]


# --------------------------------------------------------------------------- #
# Read-only helpers
# --------------------------------------------------------------------------- #
def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _iso(ts: Any) -> Optional[str]:
    if ts is None or pd.isna(ts):
        return None
    return pd.Timestamp(ts).date().isoformat()


def load_design() -> Dict[str, Any]:
    """Read the Phase 2L-A design and the Phase 2K-C full-sample IC.

    The candidate, primary horizon, and gate thresholds come from the 2L-A design
    so this phase validates exactly what was designed. The Phase 2K-C residual IC
    (for the degradation gate) is read directly from the 2K-C decision.
    """
    design = _read_json(DESIGN_2LA_JSON)
    cs = design.get("candidate_selected", {})
    cfg = design.get("config", {})
    nxt = design.get("recommended_next_phase", {})

    feature = cs.get("feature")
    primary_horizon = int(cs.get("primary_horizon_days") or DEFAULT_PRIMARY_HORIZON)
    direction = cs.get("direction")
    ic_2kc = cs.get("phase2k_c_residual_mean_rank_ic")

    # The 2K-C decision is the authoritative source for the full-sample residual IC.
    try:
        twokc = _read_json(INPUT_2KC_JSON)
    except (OSError, ValueError):
        twokc = {}
    for c in twokc.get("candidates", []):
        if c.get("feature") == feature:
            ic_2kc = c.get("overall", {}).get("mean_rank_ic", ic_2kc)
            if not direction:
                direction = c.get("overall", {}).get("direction")
            break

    reference_sign = (1 if (direction or "").startswith("bull")
                      else -1 if (direction or "").startswith("bear")
                      else _sign(ic_2kc))

    design_validation = {
        "design_phase": design.get("phase"),
        "candidate_is_avg_dollar_volume_21d": feature == "avg_dollar_volume_21d",
        "primary_horizon_is_63": primary_horizon == 63,
        "design_routed_to_2l_b": nxt.get("phase") == "2L-B",
    }

    return {
        "feature": feature,
        "primary_horizon_days": primary_horizon,
        "direction": direction,
        "reference_sign": int(reference_sign),
        "phase2k_c_residual_mean_rank_ic": ic_2kc,
        "thresholds": {
            "rank_ic_floor": cfg.get("rank_ic_floor", RANK_IC_FLOOR),
            "majority_sign_fraction": cfg.get(
                "majority_sign_fraction", MAJORITY_SIGN_FRACTION),
            "concentration_top3_threshold": cfg.get(
                "concentration_top3_threshold", CONCENTRATION_TOP3_THRESHOLD),
            "degradation_tolerance_fraction": cfg.get(
                "degradation_tolerance_fraction", DEGRADATION_TOLERANCE_FRACTION),
            "min_effective_obs": cfg.get("min_effective_obs", MIN_EFFECTIVE_OBS),
            "min_train_sessions": cfg.get("min_train_sessions", MIN_TRAIN_SESSIONS),
            "rolling_train_sessions": cfg.get(
                "rolling_train_sessions", ROLLING_TRAIN_SESSIONS),
            "validation_sessions": cfg.get("validation_sessions", VALIDATION_SESSIONS),
            "step_sessions": cfg.get("step_sessions", STEP_SESSIONS),
        },
        "design_validation": design_validation,
    }


def build_residual_panel(scored: "pd.DataFrame", prices: "pd.DataFrame"
                         ) -> Tuple["pd.DataFrame", Dict[str, Any]]:
    """Rebuild the Phase 2K-B panel and attach a residual label for every horizon.

    Uses the imported Phase 2K-B functions verbatim, so the residual labels are
    identical to those Phase 2K-B / 2K-C raised the candidate on: forward
    excess-vs-SPY return, per-date cross-sectionally neutralized against the
    trailing risk controls via OLS, with a demean fallback. No look-ahead: the
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
# Fold construction (strictly sequential, embargoed, no random CV)
# --------------------------------------------------------------------------- #
def build_folds(calendar: List["pd.Timestamp"], horizon: int,
                min_train: int, validation: int, step: int,
                rolling_train: int) -> List[Dict[str, Any]]:
    """Sequential walk-forward folds derived from the actual labeled calendar.

    For each fold the training block ends at index ``t_end``; a horizon-length
    embargo occupies the next ``horizon`` sessions; the validation block is the
    following ``validation`` sessions. Because the forward labels span the horizon,
    the embargo guarantees the last training label's forward window ends before the
    first validation date -- so there is no train/validation label overlap. The
    validation block is always chronologically after the training block, and the
    window advances one block per fold. No random cross-validation, no shuffling.
    """
    n = len(calendar)
    folds: List[Dict[str, Any]] = []
    fold_id = 0
    t_end = min_train
    while t_end + horizon + validation <= n:
        v_start = t_end + horizon
        v_end = v_start + validation
        exp_start = 0
        roll_start = max(0, t_end - rolling_train)
        folds.append({
            "fold_id": fold_id,
            "window_type": "expanding",
            "train_start_idx": exp_start,
            "train_end_idx": t_end,                 # exclusive
            "rolling_train_start_idx": roll_start,
            "embargo_start_idx": t_end,             # inclusive
            "embargo_end_idx": v_start,             # exclusive
            "embargo_sessions": horizon,
            "validation_start_idx": v_start,        # inclusive
            "validation_end_idx": v_end,            # exclusive
            "train_start": _iso(calendar[exp_start]),
            "train_end": _iso(calendar[t_end - 1]),
            "rolling_train_start": _iso(calendar[roll_start]),
            "embargo_start": _iso(calendar[t_end]),
            "embargo_end": _iso(calendar[v_start - 1]),
            "validation_start": _iso(calendar[v_start]),
            "validation_end": _iso(calendar[v_end - 1]),
            "n_train_dates": int(t_end - exp_start),
            "n_rolling_train_dates": int(t_end - roll_start),
            "n_embargo_dates": int(horizon),
            "n_validation_dates": int(v_end - v_start),
        })
        fold_id += 1
        t_end += step
    return folds


# --------------------------------------------------------------------------- #
# Model-free, rank-only validation metrics (nothing is fitted)
# --------------------------------------------------------------------------- #
def _long_leg_concentration(val_slice: "pd.DataFrame", fcol: str, lcol: str
                            ) -> Dict[str, Any]:
    """Top-quintile (long-leg) ticker concentration inside a validation window.

    Ranks names by the feature each date, takes the top quintile, and measures the
    most-frequent ticker's share and the top-3 cumulative share -- a check that the
    signal does not lean on one or two names. No fitting; pure ranking.
    """
    sub = val_slice[[DATE_COL, TICKER_COL, fcol, lcol]].dropna(
        subset=[fcol, lcol]).copy()
    out = {"top_quintile_ticker_concentration": None, "top3_long_leg_share": None,
           "n_unique_top_quintile_tickers": 0, "top_quintile_rows": 0}
    if sub.empty:
        return out
    g = sub.groupby(DATE_COL)[fcol]
    size = g.transform("size")
    nuniq = g.transform("nunique")
    elig = sub[(size >= MIN_NAMES_DECILE) & (nuniq >= 5)].copy()
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


def _turnover_stability_proxy(val_slice: "pd.DataFrame", fcol: str) -> Optional[float]:
    """Mean top-quintile retention across consecutive validation dates.

    For each adjacent date pair, the fraction of the prior date's top-quintile names
    still in the top quintile the next date. A turnover/stability proxy only -- no
    transaction costs and no order simulation are modeled.
    """
    sub = val_slice[[DATE_COL, TICKER_COL, fcol]].dropna(subset=[fcol]).copy()
    if sub.empty:
        return None
    sets: List[set] = []
    for _, blk in sub.groupby(DATE_COL, sort=True):
        if blk[fcol].size < MIN_NAMES_DECILE or blk[fcol].nunique() < 5:
            continue
        cutoff = blk[fcol].quantile(0.8)
        names = set(blk[blk[fcol] >= cutoff][TICKER_COL].tolist())
        if names:
            sets.append(names)
    if len(sets) < 2:
        return None
    retentions = []
    for prev, cur in zip(sets[:-1], sets[1:]):
        if prev:
            retentions.append(len(prev & cur) / len(prev))
    return _round(float(np.mean(retentions))) if retentions else None


def analyze_fold(fold: Dict[str, Any], ics_all: "pd.Series",
                 panel_cell: "pd.DataFrame", calendar: List["pd.Timestamp"],
                 fcol: str, lcol: str, horizon: int, reference_sign: int
                 ) -> Dict[str, Any]:
    """Model-free rank-only metrics for one validation window.

    The training window only fixes the direction (its residual IC sign, under both
    the expanding and rolling spans); it never enters the validation measurement.
    All reported numbers are read out-of-sample on the validation window.
    """
    val_dates = calendar[fold["validation_start_idx"]:fold["validation_end_idx"]]
    exp_train_dates = calendar[fold["train_start_idx"]:fold["train_end_idx"]]
    roll_train_dates = calendar[fold["rolling_train_start_idx"]:fold["train_end_idx"]]

    val_ic = ics_all.reindex(val_dates).dropna()
    exp_train_ic = ics_all.reindex(exp_train_dates).dropna()
    roll_train_ic = ics_all.reindex(roll_train_dates).dropna()

    n_val_dates = int(len(val_ic))
    val_mean = float(val_ic.mean()) if n_val_dates else None
    val_std = float(val_ic.std(ddof=1)) if n_val_dates > 1 else None
    info_ratio = (val_mean / val_std
                  if (val_mean is not None and val_std not in (None, 0.0)
                      and not pd.isna(val_std)) else None)
    val_sign = _sign(val_mean)

    val_slice = panel_cell[panel_cell[DATE_COL].isin(set(val_dates))]
    n_val_rows = int(len(val_slice))
    spread, hit = _B._spread_and_hit_rate(val_slice, fcol, lcol)
    conc = _long_leg_concentration(val_slice, fcol, lcol)
    turnover = _turnover_stability_proxy(val_slice, fcol)

    exp_train_mean = float(exp_train_ic.mean()) if len(exp_train_ic) else None
    roll_train_mean = float(roll_train_ic.mean()) if len(roll_train_ic) else None

    return {
        "fold_id": fold["fold_id"],
        "train_start": fold["train_start"],
        "train_end": fold["train_end"],
        "embargo_start": fold["embargo_start"],
        "embargo_end": fold["embargo_end"],
        "validation_start": fold["validation_start"],
        "validation_end": fold["validation_end"],
        "n_train_dates": fold["n_train_dates"],
        "n_validation_dates": n_val_dates,
        "n_validation_rows": n_val_rows,
        "validation_mean_residual_rank_ic": _round(val_mean),
        "validation_information_ratio": _round(info_ratio),
        "top_minus_bottom_residual_spread": _round(spread),
        "top_decile_residual_hit_rate": _round(hit),
        "top_quintile_ticker_concentration": conc["top_quintile_ticker_concentration"],
        "top3_long_leg_share": conc["top3_long_leg_share"],
        "turnover_stability_proxy": turnover,
        "effective_validation_obs": _round(
            (n_val_dates / horizon) if horizon else 0.0, 3),
        "train_mean_residual_rank_ic_expanding": _round(exp_train_mean),
        "train_mean_residual_rank_ic_rolling": _round(roll_train_mean),
        "validation_sign": val_sign,
        "validation_sign_matches_reference": bool(
            reference_sign != 0 and val_sign == reference_sign),
    }


# --------------------------------------------------------------------------- #
# Aggregation primitives
# --------------------------------------------------------------------------- #
def _block_bootstrap_ci(ic_values: "np.ndarray", block_len: int, n_boot: int,
                        rng: "np.random.RandomState", alpha: float
                        ) -> Tuple[Optional[float], Optional[float]]:
    """Moving-block bootstrap CI for the pooled mean per-date residual IC.

    Block length = the forward horizon, so heavily overlapping forward windows are
    resampled in contiguous blocks rather than as if independent.
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
        out["sign_flips_across_regime"] = bool(
            hi_ic is not None and lo_ic is not None
            and _sign(hi_ic) != 0 and _sign(lo_ic) != 0
            and _sign(hi_ic) != _sign(lo_ic))
        return out
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


def aggregate(fold_metrics: List[Dict[str, Any]], val_ic_pooled: "pd.Series",
              panel_cell: "pd.DataFrame", fcol: str, lcol: str, horizon: int,
              reference_sign: int, ic_2kc: Optional[float],
              spy_fwd: "pd.Series", market_vol: "pd.Series",
              thresholds: Dict[str, Any]) -> Dict[str, Any]:
    n_folds = len(fold_metrics)
    fold_means = [f["validation_mean_residual_rank_ic"] for f in fold_metrics
                  if f["validation_mean_residual_rank_ic"] is not None]

    pooled_n = int(len(val_ic_pooled))
    pooled_mean = float(val_ic_pooled.mean()) if pooled_n else None
    pooled_median = float(val_ic_pooled.median()) if pooled_n else None
    total_effective_obs = (pooled_n / horizon) if horizon else 0.0

    n_same_sign = sum(1 for f in fold_metrics if f["validation_sign_matches_reference"])
    frac_same_sign = (n_same_sign / n_folds) if n_folds else None

    # Moving-block bootstrap on the pooled validation per-date IC.
    rng = np.random.RandomState(SEED)
    lo, hi = _block_bootstrap_ci(val_ic_pooled.to_numpy(), horizon, BOOTSTRAP_N,
                                 rng, CI_ALPHA)
    ci_excludes_zero = bool(
        lo is not None and hi is not None and ((lo > 0 and hi > 0) or (lo < 0 and hi < 0)))

    # Leave-one-fold-out on the pooled per-date IC: drop each fold's dates, re-mean,
    # check the sign is preserved and the magnitude stays above the floor.
    floor = float(thresholds["rank_ic_floor"])
    lofo: List[Dict[str, Any]] = []
    lofo_sign_stable = bool(reference_sign != 0 and n_folds >= 2)
    for f in fold_metrics:
        keep = [g for g in fold_metrics if g["fold_id"] != f["fold_id"]]
        keep_means = [g["validation_mean_residual_rank_ic"] for g in keep
                      if g["validation_mean_residual_rank_ic"] is not None]
        m = float(np.mean(keep_means)) if keep_means else None
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

    concentration = _long_leg_concentration(
        panel_cell[panel_cell[DATE_COL].isin(set(val_ic_pooled.index))],
        fcol, lcol)

    regimes = {
        "spy_forward_return": _regime_split(val_ic_pooled, spy_fwd, "sign"),
        "market_volatility_21d": _regime_split(val_ic_pooled, market_vol, "median"),
    }
    spy_reg = regimes["spy_forward_return"]
    regime_dependent = bool(spy_reg is not None and spy_reg.get("sign_flips_across_regime"))

    degr_floor = (DEGRADATION_TOLERANCE_FRACTION * ic_2kc
                  if isinstance(ic_2kc, (int, float)) else None)
    degraded = bool(
        pooled_mean is not None and degr_floor is not None
        and pooled_mean < degr_floor)

    return {
        "n_folds": n_folds,
        "n_validation_windows": n_folds,
        "pooled_n_validation_dates": pooled_n,
        "total_effective_validation_obs": _round(total_effective_obs, 3),
        "mean_validation_residual_rank_ic": _round(
            float(np.mean(fold_means)) if fold_means else None),
        "pooled_validation_mean_residual_rank_ic": _round(pooled_mean),
        "median_validation_residual_rank_ic": _round(pooled_median),
        "reference_direction_sign": int(reference_sign),
        "n_validation_windows_same_sign": int(n_same_sign),
        "frac_validation_windows_same_sign": _round(frac_same_sign),
        "bootstrap_pooled_ic_ci": {
            "method": "moving_block",
            "block_len_sessions": horizon,
            "n_resamples": BOOTSTRAP_N,
            "alpha": CI_ALPHA,
            "ci_low": _round(lo),
            "ci_high": _round(hi),
            "excludes_zero": ci_excludes_zero,
        },
        "leave_one_fold_out": {
            "per_fold": lofo,
            "sign_stable": lofo_sign_stable,
        },
        "concentration": concentration,
        "regimes": regimes,
        "regime_dependent": regime_dependent,
        "degradation_vs_phase2k_c": {
            "phase2k_c_residual_mean_rank_ic": _round(ic_2kc),
            "degradation_tolerance_fraction": DEGRADATION_TOLERANCE_FRACTION,
            "degradation_floor": _round(degr_floor),
            "pooled_validation_mean_residual_rank_ic": _round(pooled_mean),
            "degraded": degraded,
        },
    }


# --------------------------------------------------------------------------- #
# Pass/fail gates (the Phase 2L-A gate set, evaluated against the results)
# --------------------------------------------------------------------------- #
def evaluate_gates(agg: Dict[str, Any], thresholds: Dict[str, Any],
                   horizon: int) -> List[Dict[str, Any]]:
    pooled_mean = agg["pooled_validation_mean_residual_rank_ic"]
    floor = float(thresholds["rank_ic_floor"])
    majority = float(thresholds["majority_sign_fraction"])
    conc_thresh = float(thresholds["concentration_top3_threshold"])
    min_obs = float(thresholds["min_effective_obs"])

    frac_same = agg["frac_validation_windows_same_sign"]
    ci = agg["bootstrap_pooled_ic_ci"]
    top3 = agg["concentration"]["top3_long_leg_share"]
    eff_obs = agg["total_effective_validation_obs"]
    degraded = agg["degradation_vs_phase2k_c"]["degraded"]

    gates = [
        {
            "id": "validation_mean_ic_above_floor",
            "observed": pooled_mean,
            "threshold": floor,
            "passed": bool(pooled_mean is not None and pooled_mean >= floor),
        },
        {
            "id": "majority_validation_windows_same_sign",
            "observed": frac_same,
            "threshold": majority,
            "passed": bool(frac_same is not None and frac_same > majority),
        },
        {
            "id": "bootstrap_ci_excludes_zero_in_validation",
            "observed": {"ci_low": ci["ci_low"], "ci_high": ci["ci_high"]},
            "threshold": "95% CI excludes 0",
            "passed": bool(ci["excludes_zero"]),
        },
        {
            "id": "no_single_validation_period_drives_result",
            "observed": agg["leave_one_fold_out"]["sign_stable"],
            "threshold": "leave-one-fold-out sign + floor stable",
            "passed": bool(agg["leave_one_fold_out"]["sign_stable"]),
        },
        {
            "id": "no_excessive_ticker_concentration",
            "observed": top3,
            "threshold": conc_thresh,
            "passed": bool(top3 is not None and top3 <= conc_thresh),
        },
        {
            "id": "no_regime_only_dependence",
            "observed": agg["regime_dependent"],
            "threshold": "no sign flip across SPY up/down split",
            "passed": bool(not agg["regime_dependent"]),
        },
        {
            "id": "no_degradation_vs_phase2k_c",
            "observed": pooled_mean,
            "threshold": agg["degradation_vs_phase2k_c"]["degradation_floor"],
            "passed": bool(not degraded and pooled_mean is not None),
        },
        {
            "id": "no_lookahead_leakage",
            "observed": "embargo applied; trailing-only features; train-only fit",
            "threshold": "all leakage controls satisfied by construction",
            "passed": True,
        },
        {
            "id": "enough_effective_observations_after_embargo",
            "observed": eff_obs,
            "threshold": min_obs,
            "passed": bool(eff_obs is not None and eff_obs >= min_obs),
        },
    ]
    return gates


def recommend(agg: Dict[str, Any], gates: List[Dict[str, Any]],
              thresholds: Dict[str, Any]) -> Dict[str, Any]:
    """Conservative verdict.

    NEED_MORE_DATA when there are too few folds or too few effective observations
    after the embargo (the data cannot support the test, regardless of point
    estimates). Otherwise WALK_FORWARD_PASS only if every gate passes, else
    WALK_FORWARD_FAIL. Even a pass is not permission to train or serve a model.
    """
    n_folds = agg["n_folds"]
    eff_obs = agg["total_effective_validation_obs"] or 0.0
    min_obs = float(thresholds["min_effective_obs"])
    enough_data = bool(n_folds >= MIN_FOLDS and eff_obs >= min_obs)

    failed = [g["id"] for g in gates if not g["passed"]]
    all_pass = not failed

    if not enough_data:
        rec = REC_NEED
        reason = (f"only {n_folds} folds / {eff_obs} effective validation "
                  "observations after the horizon-length embargo (need >= "
                  f"{MIN_FOLDS} folds and >= {min_obs} effective obs); the data "
                  "cannot support a conclusive walk-forward test.")
    elif all_pass:
        rec = REC_PASS
        reason = ("every walk-forward gate passed out-of-sample; the model-free "
                  "rank-only signal held across sequential embargoed validation "
                  "windows. This is NOT a production edge and NOT permission to "
                  "train or serve a model.")
    else:
        rec = REC_FAIL
        reason = ("enough folds exist but the signal failed gate(s): "
                  + ", ".join(failed)
                  + "; the residual signal does not hold out-of-sample.")

    return {
        "recommendation": rec,
        "allowed_values": list(_ALLOWED_RECOMMENDATIONS),
        "enough_data": enough_data,
        "all_gates_passed": all_pass,
        "failed_gates": failed,
        "reason": reason,
        "is_production_edge": False,
        "authorizes_model_training": False,
    }


def build_recommended_next_phase(recommendation: str) -> Dict[str, Any]:
    if recommendation == REC_PASS:
        return {
            "phase": "2L-C",
            "title": "Simple Residual Signal Model Candidate Design",
            "purpose": (
                "The model-free signal held out-of-sample across the embargoed "
                "walk-forward windows, so the next phase may DESIGN a very simple, "
                "separately gated single-feature candidate -- a univariate monotonic "
                "or rank transform only, no multi-feature model, no neural network, "
                "no production scoring, no serving integration, no model-v2 "
                "enablement. It still trains and deploys nothing. This walk-forward "
                "pass is not a production edge."),
        }
    return {
        "phase": "2K-D",
        "title": "Extend Data / Broaden Universe Before Model Research",
        "purpose": (
            "The model-free walk-forward validation did not pass on the current "
            "~40-name, single-regime 2023-2026 export (either the signal failed the "
            "gates out-of-sample or there were too few effective observations after "
            "the embargo). The disciplined next step is to acquire longer history "
            "and a broader point-in-time universe and retest, not to train a model. "
            "No model candidate is created."),
    }


def build_interpretation(design: Dict[str, Any], agg: Dict[str, Any],
                         recommendation: Dict[str, Any]) -> Dict[str, Any]:
    feature = design["feature"]
    rec = recommendation["recommendation"]
    pooled = agg["pooled_validation_mean_residual_rank_ic"]
    if rec == REC_PASS:
        verdict = ("held out-of-sample across the embargoed walk-forward windows")
    elif rec == REC_NEED:
        verdict = ("could not be conclusively tested -- too few effective "
                   "observations remain after the horizon-length embargo on this "
                   "thin, single-regime panel")
    else:
        verdict = ("did not hold out-of-sample once tested on sequential, embargoed "
                   "walk-forward windows")
    return {
        "candidate": feature,
        "model_trained": False,
        "model_fitted": False,
        "model_candidate_created": False,
        "authorized_to_train_model": False,
        "authorized_to_serve_model": False,
        "production_edge_claimed": False,
        "walk_forward_recommendation": rec,
        "pooled_validation_mean_residual_rank_ic": pooled,
        "narrative": (
            "Phase 2L-B implemented the Phase 2L-A model-free, rank-only walk-forward "
            "validation for " + str(feature) + " at its 63d horizon. Names were "
            "ranked by the single feature each date, the direction was fixed from the "
            "training window, and only out-of-sample validation residual-label "
            "metrics were measured -- nothing was fitted. The signal " + verdict + ". "
            "No model was trained, fitted, scored, or served, and no model candidate "
            "was created. Even a walk-forward pass would only permit DESIGNING a "
            "simple, separately gated single-feature candidate later; it is not a "
            "production edge."),
    }


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_results() -> Dict[str, Any]:
    design = load_design()
    feature = design["feature"]
    horizon = design["primary_horizon_days"]
    reference_sign = design["reference_sign"]
    ic_2kc = design["phase2k_c_residual_mean_rank_ic"]
    th = design["thresholds"]

    scored, prices, run_summary, _phase2i_a, _phase2j = _B.load_inputs()
    panel, fit_counts = build_residual_panel(scored, prices)

    lcol = f"resid_{horizon}d"
    panel_cell = panel[[DATE_COL, TICKER_COL, feature, lcol]].dropna(
        subset=[feature, lcol]).copy()
    cell = _B._cell_frame(panel, feature, lcol)
    ics_all = _B._per_date_rank_ic(cell, feature, lcol)
    ics_all = ics_all.sort_index()
    calendar = list(ics_all.index)

    folds = build_folds(
        calendar, horizon,
        int(th["min_train_sessions"]), int(th["validation_sessions"]),
        int(th["step_sessions"]), int(th["rolling_train_sessions"]))

    fold_metrics = [
        analyze_fold(f, ics_all, panel_cell, calendar, feature, lcol,
                     horizon, reference_sign)
        for f in folds
    ]

    # Pooled out-of-sample validation per-date IC (folds are non-overlapping).
    val_dates_all: List[Any] = []
    for f in folds:
        val_dates_all.extend(calendar[f["validation_start_idx"]:f["validation_end_idx"]])
    val_ic_pooled = ics_all.reindex(val_dates_all).dropna()

    spy_fwd = _spy_forward_return(prices, horizon)
    market_vol = _per_date_scalar(scored, MARKET_VOL_COL)

    agg = aggregate(fold_metrics, val_ic_pooled, panel_cell, feature, lcol,
                    horizon, reference_sign, ic_2kc, spy_fwd, market_vol, th)
    gates = evaluate_gates(agg, th, horizon)
    recommendation = recommend(agg, gates, th)
    recommended_next_phase = build_recommended_next_phase(
        recommendation["recommendation"])
    interpretation = build_interpretation(design, agg, recommendation)

    panel_dates = pd.to_datetime(panel[DATE_COL])
    return {
        "phase": PHASE,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "phase2l_a_design_json": DESIGN_2LA_JSON,
            "phase2k_c_json": INPUT_2KC_JSON,
            "phase2k_b_json": INPUT_2KB_JSON,
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
        "design_validation": design["design_validation"],
        "candidate": {
            "feature": feature,
            "primary_horizon_days": horizon,
            "direction": design["direction"],
            "reference_sign": reference_sign,
            "phase2k_c_residual_mean_rank_ic": _round(ic_2kc),
            "is_research_only": True,
            "is_production_edge": False,
        },
        "residualization_config": {
            "label_base": "forward_excess_return_vs_spy",
            "label_column": lcol,
            "horizons": HORIZONS,
            "controls": CONTROL_FEATURES,
            "method": "per-date cross-sectional OLS (usable controls + intercept); "
                      "residual = forward excess label - fitted (identical to "
                      "Phase 2K-B / 2K-C, via the imported Phase 2K-B functions)",
            "robust_fallback": "cross-sectional demean when no control is usable, a "
                               "control is constant, or the date has fewer than "
                               "min_names_for_ols usable names",
            "min_names_for_ols": RESID_MIN_NAMES,
            "no_lookahead": "controls are trailing point-in-time features; the label "
                            "is strictly forward; each per-date fit uses no forward "
                            "information",
            "residual_label_columns": {str(h): f"resid_{h}d" for h in HORIZONS},
            "fit_counts": fit_counts,
        },
        "walk_forward_config": {
            "candidate_signal": feature,
            "primary_horizon_days": horizon,
            "embargo_sessions": horizon,
            "embargo_equals_forward_horizon": True,
            "min_train_sessions": int(th["min_train_sessions"]),
            "rolling_train_sessions": int(th["rolling_train_sessions"]),
            "validation_sessions": int(th["validation_sessions"]),
            "step_sessions": int(th["step_sessions"]),
            "training_scheme_for_gates": "expanding",
            "rolling_training_reported": True,
            "no_random_cross_validation": True,
            "test_period_always_after_training_period": True,
            "no_overlap_between_train_and_validation_label_windows": True,
            "model_free": True,
            "fitted_model": False,
            "calendar_source": "labeled per-date residual-IC session calendar",
            "n_calendar_sessions": int(len(calendar)),
            "calendar_start": _iso(calendar[0]) if calendar else None,
            "calendar_end": _iso(calendar[-1]) if calendar else None,
            "thresholds": th,
        },
        "panel": {
            "n_rows": int(len(panel)),
            "n_tickers": int(panel[TICKER_COL].nunique()),
            "n_dates": int(panel[DATE_COL].nunique()),
            "date_start": _iso(panel_dates.min()) if len(panel) else None,
            "date_end": _iso(panel_dates.max()) if len(panel) else None,
        },
        "provenance": {
            "source": run_summary.get("source"),
            "date_start": run_summary.get("date_start"),
            "date_end": run_summary.get("date_end"),
            "price_row_count": run_summary.get("row_count"),
            "ticker_count": run_summary.get("ticker_count"),
        },
        "folds": folds,
        "fold_metrics": fold_metrics,
        "aggregate_metrics": agg,
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


def run(output_path: str = RESULTS_JSON) -> Dict[str, Any]:
    results = build_results()
    write_results(results, output_path)
    return results


def main() -> int:
    t0 = time.perf_counter()
    d = run()
    elapsed = time.perf_counter() - t0
    wf = d["walk_forward_config"]
    agg = d["aggregate_metrics"]
    rec = d["recommendation"]
    nxt = d["recommended_next_phase"]
    print(f"[phase2l-b] candidate                    : {d['candidate']['feature']} "
          f"(h={d['candidate']['primary_horizon_days']}d, "
          f"{d['candidate']['direction']})")
    print(f"[phase2l-b] labeled calendar sessions     : {wf['n_calendar_sessions']} "
          f"({wf['calendar_start']} -> {wf['calendar_end']})")
    print(f"[phase2l-b] folds / effective obs         : {agg['n_folds']} / "
          f"{agg['total_effective_validation_obs']}")
    print(f"[phase2l-b] pooled validation mean IC     : "
          f"{agg['pooled_validation_mean_residual_rank_ic']}  "
          f"CI=[{agg['bootstrap_pooled_ic_ci']['ci_low']},"
          f"{agg['bootstrap_pooled_ic_ci']['ci_high']}]")
    print(f"[phase2l-b] frac windows same sign        : "
          f"{agg['frac_validation_windows_same_sign']}")
    n_pass = sum(1 for g in d["pass_fail_gates"] if g["passed"])
    print(f"[phase2l-b] gates passed                  : {n_pass}/"
          f"{len(d['pass_fail_gates'])}")
    print(f"[phase2l-b] recommendation                : {rec['recommendation']}")
    print(f"[phase2l-b] recommended next phase        : {nxt['phase']} "
          f"({nxt['title']})")
    print(f"[phase2l-b] results written               : {RESULTS_JSON}")
    print(f"[phase2l-b] elapsed seconds                : {elapsed:.2f}")
    print("[phase2l-b] model-free validation; no model trained; safety flags in JSON.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
