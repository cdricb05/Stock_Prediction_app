"""Phase 8-X - Autonomous Strong Alpha Discovery Campaign.

WHY THIS PHASE EXISTS
    The earnings-surprise alpha family has now been chased to its limit on the EODHD data:
    Phase 8-T promoted four surprise signals on the original 299-ticker cross-section; Phase 8-V's
    matched 299->545 universe expansion DILUTED them (4 promoted -> 0); Phase 8-W attributed the
    decay to a low-information new cohort and found the alpha SURVIVES ONLY CONSTRAINED to the old
    cohort / high-liquidity / single-sector slices (CONSTRAINED_ALPHA_SURVIVES). A constrained,
    exploratory signal is NOT a strong, robust, money-making alpha.

    Phase 8-X is the autonomous discovery campaign that keeps searching the EXPANDED 545-ticker
    universe - across the full breadth of alpha families AND transparent walk-forward factor-ensemble
    MODELS - until it either finds a genuinely STRONG broad alpha (high t-stat, survives the old/new
    cohort split AND the pre/post-2020 split, sector-diversified, BH-significant, net-of-25bps
    positive) or proves that the current EODHD data families are exhausted and a genuinely NEW data
    family is required. It does NOT salvage constrained signals, does NOT productize them, and does
    NOT touch Paper Trader. Constrained / weak candidates are logged as CONSTRAINED_NOT_GOOD_ENOUGH
    and the search continues.

REUSE
    The cross-sectional scoring core is reused verbatim from Phase 8-T (`evaluate_ext`, `gate_reasons`,
    `scenario_battery`, `prepare_signals_ext`) over Phase 8-S's point-in-time `build_event_table` and
    Benjamini-Hochberg control, and the Phase 8-W event-table / cohort-tag / liquidity-proxy builders.
    8-X adds: a STRICTER strong-alpha gate, a durable autonomous research loop (resume state, promoted
    + rejected + constrained registries, no repeated scenario testing unless inputs changed), and a
    transparent walk-forward factor-ensemble MODEL layer with out-of-sample decile spreads.

TERMINAL DECISIONS
    STRONG_ALPHA_FOUND | NO_STRONG_ALPHA_FOUND_CURRENT_DATA | NEEDS_NEW_DATA_FAMILY |
    HARD_BLOCKER_REQUIRES_USER_ACTION | ERROR

Run (offline; reuses the gitignored 8-V expanded panel + EODHD earnings cache; no network, no key):
    python research/run_phase8x_autonomous_strong_alpha_discovery.py
Test (fully offline; synthetic strong / flat cohorts, no key, no network):
    python -m pytest tests/test_phase8x_autonomous_strong_alpha_discovery.py -q
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Reuse the Phase 8-W attribution helpers (event table / cohort tag / liquidity proxy), which in turn
# reuse the Phase 8-T scoring core (which reuses the 8-S data layer + 8-R secret discipline).
from research import run_phase8w_expanded_universe_failure_attribution as w8  # noqa: E402

t8 = w8.t8
s8 = w8.s8
r8 = w8.r8

PHASE = "8-X"

# Reused IO helpers (single source of truth).
_write_json = s8._write_json
_write_csv = s8._write_csv
_read_csv_file = s8._read_csv_file
_read_json = s8._read_json
_round = s8._round
_rel = s8._rel

# --------------------------------------------------------------------------- #
# Paths / inputs.
# --------------------------------------------------------------------------- #
_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase8x_autonomous_strong_alpha_discovery"
_PHASE8V_DIR = _REPO_ROOT / "research" / "output" / "phase8v_combined_eodhd_universe_expansion"
_PHASE8V_REPORT = "phase8v_combined_eodhd_universe_expansion.json"
_PHASE8W_DIR = _REPO_ROOT / "research" / "output" / "phase8w_expanded_universe_failure_attribution"
_PHASE8W_REPORT = "phase8w_expanded_universe_failure_attribution.json"
_PHASE8W_DECISION = "final_research_decision.json"
_EXPANDED_PANEL = (_REPO_ROOT / "research" / "data" / "eodhd" / "normalized" / "eod_prices"
                   / "expanded_price_panel.csv")

# Autonomous-loop defaults (from the brief).
DEFAULT_MAX_CYCLES = 30
DEFAULT_MAX_SCENARIOS = 500

# Reused scoring config.
PRIMARY_HORIZON = t8.PRIMARY_HORIZON
RET_COL = "fwd_exc_%d" % PRIMARY_HORIZON
MIN_NAMES_PER_MONTH = t8.MIN_NAMES_PER_MONTH
MIN_MONTHS = t8.MIN_MONTHS
MIN_EVENTS = t8.MIN_EVENTS
GATE_BH_Q = t8.GATE_BH_Q
_PRE2020 = "2020-01-01"
_RNG_SEED = 8675309

# ---- STRONG-alpha promotion standard (stricter than the 8-T gate) ---- #
STRONG_MIN_TICKERS = 500          # broad cross-section, not a constrained subset
STRONG_MIN_EVENTS = 30000         # enough point-in-time observations
STRONG_MIN_IC_T = 3.0             # IC t-stat (signed positive)
STRONG_MIN_HIT_RATE = 0.58        # monthly spread hit-rate ...
STRONG_HIT_EXEMPT_SPREAD = 0.006  # ... OR a very strong monthly net spread compensates
STRONG_MAX_SECTOR_SHARE = t8.GATE_MAX_SECTOR_SHARE   # 0.60 - not single-sector-driven
# A candidate that is strong on every count EXCEPT it is cohort-constrained or single-sector is
# "constrained, not good enough" (logged, search continues), never promoted as strong.
_CONSTRAINING_REASONS = ("old-cohort-only / fails old-new cohort split",
                         "single-sector-dominated (>%.0f%% one sector)" % (STRONG_MAX_SECTOR_SHARE * 100))

# Walk-forward model defaults.
WF_TRAIN_MONTHS = 24
WF_TEST_MONTHS = 6
WF_STEP_MONTHS = 6
N_DECILES = 10

DEC_STRONG = "STRONG_ALPHA_FOUND"
DEC_NO_STRONG = "NO_STRONG_ALPHA_FOUND_CURRENT_DATA"
DEC_NEW_DATA = "NEEDS_NEW_DATA_FAMILY"
DEC_BLOCKER = "HARD_BLOCKER_REQUIRES_USER_ACTION"
DEC_ERROR = "ERROR"
ALLOWED_DECISIONS = (DEC_STRONG, DEC_NO_STRONG, DEC_NEW_DATA, DEC_BLOCKER, DEC_ERROR)

_ARTIFACTS = {
    "report": "phase8x_autonomous_strong_alpha_discovery.json",
    "cycle_log": "autonomous_cycle_log.csv",
    "hypothesis_registry": "hypothesis_registry.csv",
    "scenario_scoreboard": "scenario_scoreboard.csv",
    "strong_candidates": "strong_alpha_candidates.csv",
    "rejected": "rejected_hypotheses.csv",
    "constrained": "constrained_not_good_enough.csv",
    "model_scoreboard": "model_candidate_scoreboard.csv",
    "walk_forward": "walk_forward_results.csv",
    "decile_spread": "decile_spread_report.csv",
    "tcost": "transaction_cost_report.csv",
    "turnover": "turnover_report.csv",
    "cohort_stability": "cohort_stability_report.csv",
    "sector_conc": "sector_concentration_report.csv",
    "subperiod": "subperiod_stability_report.csv",
    "multiple_testing": "multiple_testing_report.csv",
    "data_exhaustion": "data_family_exhaustion_report.csv",
    "next_plan": "phase8y_next_plan.json",
    "secret_audit": "secret_safety_audit.csv",
    # durable resume state + run log (committed-safe extras, not in the required-18 list)
    "resume_state": "autonomous_resume_state.json",
    "run_log": "phase8x_run_log.csv",
}
# The 18 committed-safe artifacts the brief requires (plus phase8x report = required as well).
_REQUIRED_ARTIFACTS = (
    "report", "cycle_log", "hypothesis_registry", "scenario_scoreboard", "strong_candidates",
    "rejected", "constrained", "model_scoreboard", "walk_forward", "decile_spread", "tcost",
    "turnover", "cohort_stability", "sector_conc", "subperiod", "multiple_testing",
    "data_exhaustion", "next_plan", "secret_audit")

# Map each scenario `family` (and the model layer) onto a broad alpha-family label for the
# data-family-exhaustion report + the new-data-family recommendation.
_FAMILY_LABELS = {
    "earnings": "earnings_surprise_and_drift",
    "fundamentals": "fundamentals_quality_and_improvement",
    "valuation": "valuation",
    "price": "price_momentum_reversal_lowvol_beta_liquidity",
    "regime": "macro_regime_conditioned",
    "interaction": "cross_factor_interactions",
    "model": "transparent_factor_ensembles",
}


def _g(r, k, n=4):
    return _round(r.get(k), n)


def _mk_rng():
    import numpy as np
    return np.random.default_rng(_RNG_SEED)


def _n_tickers(ev_df) -> int:
    try:
        return int(ev_df["ticker"].nunique())
    except Exception:
        return 0


def _safe_eval(ev_df, sc: Dict, rng) -> Dict:
    """Run the 8-T extended evaluation on an ev slice; never abort the whole campaign on one slice."""
    try:
        return t8.evaluate_ext(ev_df, sc, rng)
    except Exception as exc:  # pragma: no cover - defensive
        row = dict(sc)
        row.update({"n_events": 0, "n_months": 0, "mean_ic": float("nan"), "ic_t": float("nan"),
                    "ic_p": float("nan"), "mean_spread": float("nan"),
                    "net_spread_10bps": float("nan"), "net_spread_25bps": float("nan"),
                    "net_spread_50bps": float("nan"), "spread_hit_rate": float("nan"),
                    "subperiod_stable": False, "top_sector_share": float("nan"),
                    "regimes_right_sign": 0, "eval_error": "%s: %s" % (type(exc).__name__, exc)})
        return row


# --------------------------------------------------------------------------- #
# A. Hypothesis space: scenarios (reuse the 8-T battery + a few broad additions) + models.
# --------------------------------------------------------------------------- #
def scenario_hypotheses() -> List[Dict]:
    """The full broad scenario battery. Reuse the 8-T battery (earnings / fundamentals / valuation /
    price / regime / interaction families) and add the broad families the brief calls out that the
    8-T battery did not already include: price reversal, sector-relative strength, beta, low-vol
    sector-neutral."""
    S = [dict(sc) for sc in t8.scenario_battery()]
    extra = []

    def add(cycle, family, scenario, signal, sector_neutral=False, regime_filter=None,
            hypothesis="", exploratory=False):
        extra.append({"cycle": cycle, "family": family, "scenario": scenario, "signal": signal,
                      "sector_neutral": sector_neutral, "regime_filter": regime_filter,
                      "hypothesis": hypothesis, "exploratory": exploratory})

    # price reversal: a CHALLENGE probe (momentum tested with the reversal hypothesis) - exploratory,
    # so it is reported but can never be promoted as strong alpha.
    add(3, "price", "price_reversal_21", "mom_pre_21", False, None,
        "CHALLENGE: short-horizon reversal (negate momentum)", exploratory=True)
    add(3, "price", "beta_signal", "low_beta", False, None,
        "low-beta defensive premium across the full cross-section")
    add(3, "price", "low_vol_sector_neutral", "low_vol", True, None,
        "within-sector low-volatility premium")
    add(3, "price", "sector_relative_strength", "mom_pre_63", True, None,
        "within-sector 63d relative strength leadership")
    add(2, "fundamentals", "margin_expansion", "gm_chg", True, None,
        "within-sector gross-margin expansion -> drift")
    return S + extra


def model_hypotheses() -> List[Dict]:
    """Transparent walk-forward factor-ensemble MODELS. Each is an additive, within-month z-scored
    blend of oriented signals (higher == better); weights are estimated out-of-sample on a rolling
    train window and applied to the held-out test window. No black boxes - the weights are either
    equal, the sign of the trailing IC, or the trailing IC itself."""
    sets = {
        "earnings_blend": ["surprise_pct", "sue", "magnitude_score", "earnings_accel"],
        "quality_blend": ["roa_x", "fcf_margin_x", "quality_composite", "bs_strength"],
        "value_price_blend": ["earnings_yield", "mom_pre_63", "low_vol"],
        "kitchen_sink_blend": ["surprise_pct", "sue", "magnitude_score", "earnings_accel",
                               "roa_x", "fcf_margin_x", "quality_composite", "bs_strength",
                               "earnings_yield", "mom_pre_63", "low_vol", "low_beta"],
    }
    weightings = ["equal", "ic_sign", "ic_weighted"]
    out, cyc = [], 7
    for set_name, cols in sets.items():
        for w in weightings:
            out.append({"cycle": cyc, "family": "model", "scenario": "%s__%s" % (set_name, w),
                        "signals": cols, "weighting": w, "sector_neutral": (set_name == "kitchen_sink_blend"),
                        "regime_filter": None, "exploratory": False,
                        "hypothesis": "walk-forward %s ensemble of %d oriented factors (%s weights)"
                        % (set_name, len(cols), w)})
        cyc += 1
    return out


# --------------------------------------------------------------------------- #
# B. Walk-forward transparent factor-ensemble evaluation.
# --------------------------------------------------------------------------- #
def _within_month_z(ev, col):
    """Within-month z-score of a column (PIT-neutral per cross-section)."""
    import numpy as np
    import pandas as pd
    g = ev.groupby("month")[col]
    mu = g.transform("mean")
    sd = g.transform("std")
    z = (ev[col] - mu) / sd.replace(0, np.nan)
    return z.replace([np.inf, -np.inf], np.nan)


def _monthly_signal_ic(ev, zcol, ret_col):
    """month -> rank-IC of a single z column (for estimating ensemble weights out-of-sample)."""
    out = {}
    sub = ev[ev[zcol].notna() & ev[ret_col].notna()]
    for mth, chunk in sub.groupby("month"):
        if len(chunk) < MIN_NAMES_PER_MONTH:
            continue
        ic = s8._spearman(chunk[zcol].to_numpy(), chunk[ret_col].to_numpy())
        if not math.isnan(ic):
            out[mth] = ic
    return out


def build_walk_forward_ensemble(ev, signals, weighting, ret_col,
                                train=WF_TRAIN_MONTHS, test=WF_TEST_MONTHS, step=WF_STEP_MONTHS):
    """Score every event with an OUT-OF-SAMPLE ensemble: for each rolling test window, estimate the
    per-signal weights on the preceding train window, then apply them to the held-out test window.
    Returns (ev_oos, window_rows) where ev_oos is the subset of events that received an OOS score in a
    new column `__ens__`, and window_rows documents each train/test split."""
    import numpy as np
    import pandas as pd
    work = ev.copy()
    work["month"] = work["entry_date"].dt.to_period("M")
    present = [c for c in signals if c in work.columns and work[c].notna().any()]
    if not present:
        return work.iloc[0:0].copy(), []
    zcols = {}
    for c in present:
        zc = "__z_%s__" % c
        work[zc] = _within_month_z(work, c).fillna(0.0)
        zcols[c] = zc
    ic_by_signal = {c: _monthly_signal_ic(work, zcols[c], ret_col) for c in present}

    months = sorted(work["month"].unique())
    if len(months) < train + test:
        # shrink the windows to fit short (test-fixture) samples but keep a real OOS split
        train = max(3, len(months) // 2)
        test = max(1, (len(months) - train) // 2)
        step = max(1, test)
    work["__ens__"] = np.nan
    window_rows = []
    start = train
    widx = 0
    while start < len(months):
        train_m = months[max(0, start - train):start]
        test_m = months[start:start + test]
        if not test_m:
            break
        weights = {}
        for c in present:
            ics = [ic_by_signal[c].get(m) for m in train_m]
            ics = [x for x in ics if x is not None]
            mean_ic = float(np.mean(ics)) if ics else 0.0
            if weighting == "equal":
                weights[c] = 1.0
            elif weighting == "ic_sign":
                weights[c] = float(np.sign(mean_ic))
            else:  # ic_weighted
                weights[c] = mean_ic
        test_mask = work["month"].isin(test_m)
        score = np.zeros(int(test_mask.sum()))
        block = work[test_mask]
        for c in present:
            score = score + weights[c] * block[zcols[c]].to_numpy()
        work.loc[test_mask, "__ens__"] = score
        oos_ic = _window_oos_ic(block.assign(__ens__=score), ret_col)
        window_rows.append({"window": widx, "train_start": str(train_m[0]) if train_m else "",
                            "train_end": str(train_m[-1]) if train_m else "",
                            "test_start": str(test_m[0]), "test_end": str(test_m[-1]),
                            "n_test_months": len(test_m), "oos_mean_ic": oos_ic})
        start += step
        widx += 1
    ev_oos = work[work["__ens__"].notna()].copy()
    return ev_oos, window_rows


def _window_oos_ic(block, ret_col):
    sub = block[block["__ens__"].notna() & block[ret_col].notna()]
    ics = []
    for _, chunk in sub.groupby("month"):
        if len(chunk) < MIN_NAMES_PER_MONTH:
            continue
        ic = s8._spearman(chunk["__ens__"].to_numpy(), chunk[ret_col].to_numpy())
        if not math.isnan(ic):
            ics.append(ic)
    import numpy as np
    return float(np.mean(ics)) if ics else float("nan")


def decile_spread(ev, sigcol, ret_col, n_deciles=N_DECILES):
    """Monthly top-minus-bottom decile long-short spread + hit rate for a signal column."""
    import numpy as np
    import pandas as pd
    sub = ev[ev[sigcol].notna() & ev[ret_col].notna()].copy()
    if sub.empty:
        return {"mean_decile_spread": float("nan"), "decile_hit_rate": float("nan"), "n_months": 0,
                "top_decile_ret": float("nan"), "bottom_decile_ret": float("nan")}
    if "month" not in sub.columns:
        sub["month"] = sub["entry_date"].dt.to_period("M")
    spreads, tops, bots = [], [], []
    for _, chunk in sub.groupby("month"):
        if len(chunk) < n_deciles:
            continue
        try:
            q = pd.qcut(chunk[sigcol].rank(method="first"), n_deciles, labels=False)
        except ValueError:
            continue
        top = chunk[q == n_deciles - 1][ret_col].mean()
        bot = chunk[q == 0][ret_col].mean()
        if math.isnan(top) or math.isnan(bot):
            continue
        spreads.append(float(top - bot))
        tops.append(float(top))
        bots.append(float(bot))
    if not spreads:
        return {"mean_decile_spread": float("nan"), "decile_hit_rate": float("nan"), "n_months": 0,
                "top_decile_ret": float("nan"), "bottom_decile_ret": float("nan")}
    arr = np.asarray(spreads)
    sgn = np.sign(arr.mean())
    return {"mean_decile_spread": float(arr.mean()),
            "decile_hit_rate": float(np.mean(np.sign(arr) == sgn)), "n_months": len(spreads),
            "top_decile_ret": float(np.mean(tops)), "bottom_decile_ret": float(np.mean(bots))}


# --------------------------------------------------------------------------- #
# C. Candidate evaluation (unify scenarios + models into one record shape).
# --------------------------------------------------------------------------- #
def _cohort_ics(ev, sc, rng):
    """(ic_old, t_old, ic_new, t_new) of a scenario/model spec on the old vs new cohort slices."""
    ev_old = ev[ev["cohort"] == "old"]
    ev_new = ev[ev["cohort"] == "new"]
    ro = _safe_eval(ev_old.copy(), sc, rng)
    rn = _safe_eval(ev_new.copy(), sc, rng)
    return (_g(ro, "mean_ic"), _g(ro, "ic_t", 2), _g(rn, "mean_ic"), _g(rn, "ic_t", 2))


def evaluate_scenario(ev, sc, rng) -> Dict:
    work = ev
    if "month" not in work.columns:
        work = ev.copy()
        work["month"] = work["entry_date"].dt.to_period("M")
    m = _safe_eval(work, sc, rng)
    ic_old, t_old, ic_new, t_new = _cohort_ics(work, sc, rng)
    dec = decile_spread(work, sc["signal"], RET_COL) if sc["signal"] in work.columns else \
        decile_spread(work.assign(_missing=float("nan")), "_missing", RET_COL)
    return {
        "kind": "scenario", "name": sc["scenario"], "family": sc["family"], "signal": sc["signal"],
        "sector_neutral": sc.get("sector_neutral", False), "regime_filter": sc.get("regime_filter"),
        "exploratory": sc.get("exploratory", False), "weighting": "", "metrics": m,
        "ic_old": ic_old, "t_old": t_old, "ic_new": ic_new, "t_new": t_new, "decile": dec,
    }


def evaluate_model(ev, spec, rng) -> Tuple[Dict, List[Dict]]:
    ev_oos, window_rows = build_walk_forward_ensemble(ev, spec["signals"], spec["weighting"], RET_COL)
    sc = {"scenario": spec["scenario"], "family": "model", "signal": "__ens__",
          "sector_neutral": spec.get("sector_neutral", False), "regime_filter": None,
          "exploratory": False, "cycle": spec["cycle"]}
    if getattr(ev_oos, "empty", True):
        m = _safe_eval(ev.iloc[0:0].assign(__ens__=[]) if False else ev_oos, sc, rng)
        cand = {"kind": "model", "name": spec["scenario"], "family": "model", "signal": "ensemble",
                "sector_neutral": sc["sector_neutral"], "regime_filter": None, "exploratory": False,
                "weighting": spec["weighting"], "metrics": m, "ic_old": None, "t_old": None,
                "ic_new": None, "t_new": None, "decile": decile_spread(ev_oos, "__ens__", RET_COL),
                "n_oos_windows": 0}
        return cand, window_rows
    if "cohort" not in ev_oos.columns:
        ev_oos["cohort"] = "old"
    m = _safe_eval(ev_oos, sc, rng)
    ic_old, t_old, ic_new, t_new = _cohort_ics(ev_oos, sc, rng)
    dec = decile_spread(ev_oos, "__ens__", RET_COL)
    cand = {"kind": "model", "name": spec["scenario"], "family": "model", "signal": "ensemble(%s)"
            % "+".join(s for s in spec["signals"][:4]) + ("+..." if len(spec["signals"]) > 4 else ""),
            "sector_neutral": sc["sector_neutral"], "regime_filter": None, "exploratory": False,
            "weighting": spec["weighting"], "metrics": m, "ic_old": ic_old, "t_old": t_old,
            "ic_new": ic_new, "t_new": t_new, "decile": dec, "n_oos_windows": len(window_rows)}
    return cand, window_rows


# --------------------------------------------------------------------------- #
# D. The STRONG-alpha gate.
# --------------------------------------------------------------------------- #
def strong_gate_reasons(cand: Dict, n_tickers: int, n_events: int,
                        min_tickers: int, min_events: int) -> List[str]:
    """Why a candidate is NOT a strong, broad, robust alpha. Empty list == STRONG. The size checks
    use the dataset totals (a strong alpha must live on the broad universe); the quality checks use
    the candidate's own out-of-sample / cross-sectional metrics."""
    m = cand["metrics"]
    reasons = []
    if cand.get("exploratory"):
        reasons.append("exploratory/challenge scenario - reported, never promoted as strong alpha")
    if n_tickers < min_tickers:
        reasons.append("universe too small (%d<%d scoreable tickers)" % (n_tickers, min_tickers))
    if n_events < min_events:
        reasons.append("too few PIT events (%d<%d)" % (n_events, min_events))
    if m.get("n_events", 0) < MIN_EVENTS:
        reasons.append("candidate slice has insufficient events (%s<%d)"
                       % (m.get("n_events", 0), MIN_EVENTS))
    if m.get("n_months", 0) < MIN_MONTHS:
        reasons.append("candidate slice has insufficient months (%s<%d)"
                       % (m.get("n_months", 0), MIN_MONTHS))
    ic = m.get("mean_ic")
    t = m.get("ic_t")
    if ic is None or (isinstance(ic, float) and math.isnan(ic)) or ic <= 0:
        reasons.append("non-positive mean IC")
    if t is None or (isinstance(t, float) and math.isnan(t)) or t < STRONG_MIN_IC_T:
        reasons.append("IC t-stat below +%.1f" % STRONG_MIN_IC_T)
    if not cand.get("bh_significant"):
        reasons.append("fails Benjamini-Hochberg q=%.2f (multiple testing)" % GATE_BH_Q)
    net25 = m.get("net_spread_25bps")
    if net25 is None or (isinstance(net25, float) and math.isnan(net25)) or net25 <= 0:
        reasons.append("does not survive 25bps cost")
    sh = m.get("spread_hit_rate")
    strong_spread = (net25 is not None and not (isinstance(net25, float) and math.isnan(net25))
                     and net25 >= STRONG_HIT_EXEMPT_SPREAD)
    if (sh is None or (isinstance(sh, float) and math.isnan(sh)) or sh < STRONG_MIN_HIT_RATE) \
            and not strong_spread:
        reasons.append("spread hit-rate below %.2f and net spread not strongly compensating"
                       % STRONG_MIN_HIT_RATE)
    # cohort split: BOTH the old and new cohorts must carry a positive edge (not old-cohort-only)
    ic_old, ic_new = cand.get("ic_old"), cand.get("ic_new")
    both_cohorts = (ic_old is not None and ic_new is not None and ic_old > 0 and ic_new > 0)
    if not both_cohorts:
        reasons.append(_CONSTRAINING_REASONS[0])
    # subperiod split: positive in both halves of the sample
    if not m.get("subperiod_stable"):
        reasons.append("fails pre/post-2020 (subperiod) stability")
    # sector concentration: not dominated by one sector
    share = m.get("top_sector_share")
    if share is not None and not (isinstance(share, float) and math.isnan(share)) \
            and share > STRONG_MAX_SECTOR_SHARE:
        reasons.append(_CONSTRAINING_REASONS[1])
    return reasons


def classify(cand: Dict, reasons: List[str]) -> str:
    """STRONG (no reasons) | CONSTRAINED (only cohort/sector constraints stand between it and STRONG)
    | REJECTED (any substantive failure)."""
    if not reasons:
        return "strong"
    non_constraining = [r for r in reasons if r not in _CONSTRAINING_REASONS]
    # constrained == would be strong but for an old-cohort-only / single-sector restriction, and the
    # raw statistical strength is genuinely there (high t, BH-significant, survives cost).
    m = cand["metrics"]
    t = m.get("ic_t")
    strong_stat = (t is not None and not (isinstance(t, float) and math.isnan(t))
                   and t >= STRONG_MIN_IC_T and cand.get("bh_significant"))
    if not non_constraining and strong_stat:
        return "constrained"
    return "rejected"


# --------------------------------------------------------------------------- #
# E. Resume state (durable autonomous loop).
# --------------------------------------------------------------------------- #
def _input_hash(spec: Dict, n_tickers: int, n_events: int, as_of: str) -> str:
    key = "|".join(str(x) for x in (
        spec.get("scenario"), spec.get("signal") or "", spec.get("weighting") or "",
        spec.get("sector_neutral"), spec.get("regime_filter") or "",
        n_tickers, n_events, as_of))
    # a stable, deterministic short hash (no salted builtins)
    h = 0
    for ch in key:
        h = (h * 131 + ord(ch)) & 0xFFFFFFFFFFFF
    return "%012x" % h


def load_resume_state(path: Path, fresh: bool) -> Dict:
    if fresh or not Path(path).is_file():
        return {"tested": {}, "cycles_completed": 0}
    try:
        st = _read_json(path)
        st.setdefault("tested", {})
        st.setdefault("cycles_completed", 0)
        return st
    except Exception:
        return {"tested": {}, "cycles_completed": 0}


# --------------------------------------------------------------------------- #
# F. The autonomous campaign loop.
# --------------------------------------------------------------------------- #
def run_campaign(ev, n_tickers, n_events, as_of, max_cycles, max_scenarios,
                 min_tickers, min_events, resume_state, persist_state, log) -> Dict:
    """Cycle-by-cycle autonomous loop. Each cycle tests one batch (a scenario family or a model set)
    of not-yet-tested hypotheses, accumulates registries, persists resume state, and continues until a
    STRONG alpha is found, the hypothesis space is exhausted, or a bound (max_cycles/max_scenarios) is
    hit. Constrained/weak candidates do NOT stop the search."""
    import numpy as np
    rng = _mk_rng()
    ev = ev.copy()
    ev["month"] = ev["entry_date"].dt.to_period("M")

    scen = scenario_hypotheses()
    models = model_hypotheses()
    # assign every hypothesis to a cycle; scenarios keep their 8-T cycle, models continue after.
    all_cycles = sorted({h["cycle"] for h in scen} | {h["cycle"] for h in models})

    tested = dict(resume_state.get("tested", {}))
    candidates: List[Dict] = []
    window_rows_all: List[Dict] = []
    cycle_rows: List[Dict] = []
    scenarios_tested = 0
    cycles_run = 0
    exhausted = True
    strong_found = False

    for cyc in all_cycles:
        if cycles_run >= max_cycles or scenarios_tested >= max_scenarios:
            exhausted = False
            break
        batch_scen = [h for h in scen if h["cycle"] == cyc]
        batch_model = [h for h in models if h["cycle"] == cyc]
        tested_this, new_this = 0, 0
        for sc in batch_scen:
            ih = _input_hash(sc, n_tickers, n_events, as_of)
            if tested.get(sc["scenario"]) == ih:
                continue
            cand = evaluate_scenario(ev, sc, rng)
            cand["input_hash"] = ih
            cand["cycle"] = cyc
            candidates.append(cand)
            tested[sc["scenario"]] = ih
            tested_this += 1
            new_this += 1
            scenarios_tested += 1
            if scenarios_tested >= max_scenarios:
                break
        for sp in batch_model:
            if scenarios_tested >= max_scenarios:
                break
            ih = _input_hash(sp, n_tickers, n_events, as_of)
            if tested.get(sp["scenario"]) == ih:
                continue
            cand, wrows = evaluate_model(ev, sp, rng)
            cand["input_hash"] = ih
            cand["cycle"] = cyc
            for w in wrows:
                w["model"] = sp["scenario"]
            window_rows_all.extend(wrows)
            candidates.append(cand)
            tested[sp["scenario"]] = ih
            tested_this += 1
            new_this += 1
            scenarios_tested += 1
        if tested_this == 0:
            cycle_rows.append({"cycle": cyc, "tested_this_cycle": 0, "cumulative_tested": scenarios_tested,
                               "note": "all hypotheses already tested (unchanged inputs) - skipped"})
            log.step("cycle", "SKIP", "cycle %d: all hypotheses already tested (resume)" % cyc)
            continue
        cycles_run += 1

        # finalize BH multiple-testing across everything tested so far, then gate.
        _finalize_gates(candidates, n_tickers, n_events, min_tickers, min_events)
        strong = [c for c in candidates if c.get("status") == "strong"]
        n_constrained = sum(1 for c in candidates if c.get("status") == "constrained")
        cycle_rows.append({"cycle": cyc, "tested_this_cycle": new_this,
                           "cumulative_tested": scenarios_tested,
                           "note": "strong=%d constrained=%d" % (len(strong), n_constrained)})
        log.step("cycle", "DONE", "cycle %d: +%d tested (cum %d); strong=%d constrained=%d"
                 % (cyc, new_this, scenarios_tested, len(strong), n_constrained), count=scenarios_tested)
        # persist durable resume state every cycle
        persist_state({"tested": tested, "cycles_completed": cycles_run})
        if strong:
            strong_found = True
            exhausted = False
            log.step("campaign", "STOP", "STRONG alpha found - halting the autonomous search")
            break

    _finalize_gates(candidates, n_tickers, n_events, min_tickers, min_events)
    return {"candidates": candidates, "window_rows": window_rows_all, "cycle_rows": cycle_rows,
            "scenarios_tested": scenarios_tested, "cycles_run": cycles_run,
            "exhausted": exhausted, "strong_found": strong_found, "tested": tested}


def _finalize_gates(candidates, n_tickers, n_events, min_tickers, min_events):
    """Apply BH multiple-testing across ALL candidates' IC p-values, then run the strong gate +
    classify each candidate."""
    pvals = [c["metrics"].get("ic_p") if c["metrics"].get("ic_p") is not None else float("nan")
             for c in candidates]
    bh = s8.benjamini_hochberg(pvals, GATE_BH_Q)
    for c, sig in zip(candidates, bh):
        c["bh_significant"] = bool(sig)
    for c in candidates:
        reasons = strong_gate_reasons(c, n_tickers, n_events, min_tickers, min_events)
        c["strong_reasons"] = reasons
        c["status"] = classify(c, reasons)


# --------------------------------------------------------------------------- #
# G. Data-family exhaustion + decision.
# --------------------------------------------------------------------------- #
def data_family_exhaustion(candidates: List[Dict], exhausted: bool) -> Tuple[List[List], List[str]]:
    """Per broad alpha family: best broad t-stat achieved, best constrained t-stat, and whether the
    family is exhausted with no strong broad alpha. Returns (rows, exhausted_family_labels)."""
    fams: Dict[str, Dict] = {}
    for c in candidates:
        lbl = _FAMILY_LABELS.get(c["family"], c["family"])
        d = fams.setdefault(lbl, {"n": 0, "best_t": None, "best_t_constrained": None,
                                  "best_name": "", "any_strong": False})
        d["n"] += 1
        t = c["metrics"].get("ic_t")
        if t is not None and not (isinstance(t, float) and math.isnan(t)):
            if d["best_t"] is None or t > d["best_t"]:
                d["best_t"] = t
                d["best_name"] = c["name"]
            if c["status"] in ("constrained", "strong"):
                if d["best_t_constrained"] is None or t > d["best_t_constrained"]:
                    d["best_t_constrained"] = t
        if c["status"] == "strong":
            d["any_strong"] = True
    rows, exhausted_labels = [], []
    for lbl in sorted(fams):
        d = fams[lbl]
        fam_exhausted = bool(exhausted and not d["any_strong"]
                             and (d["best_t"] is None or d["best_t"] < STRONG_MIN_IC_T))
        if fam_exhausted:
            exhausted_labels.append(lbl)
        rows.append([lbl, d["n"], _round(d["best_t"], 2), _round(d["best_t_constrained"], 2),
                     d["best_name"], d["any_strong"], fam_exhausted])
    return rows, exhausted_labels


_NEW_DATA_FAMILY = ("analyst_estimate_revisions + short_interest + options_implied_vol_skew + "
                    "news_social_sentiment")
_NEW_DATA_RATIONALE = (
    "The EODHD earnings / fundamentals / price families are now exhausted for BROAD strong alpha: "
    "Phase 8-T promoted earnings-surprise signals on the original 299-name cross-section; Phase 8-V's "
    "matched 299->545 universe expansion DILUTED them; Phase 8-W showed the alpha survives only "
    "CONSTRAINED to the old cohort / high-liquidity / single sector; and Phase 8-X's broad scenario "
    "battery PLUS transparent walk-forward factor-ensemble models found no candidate clearing the "
    "strong gate (t>=3.0, positive IC in BOTH cohorts and BOTH pre/post-2020 halves, sector-"
    "diversified, BH-significant, net-of-25bps positive) on the full 545-name universe. The binding "
    "constraint is the INFORMATION CONTENT of these families, not scenario design or model class - so "
    "a genuinely NEW, orthogonal data family is required next: analyst estimate-revision breadth "
    "(captures the drift the realized surprise only partially proxies), short interest / days-to-cover "
    "(crowding + squeeze risk), options-implied volatility & skew (forward-looking risk repricing), "
    "and news / social sentiment (event flow between earnings). These are orthogonal to realized "
    "earnings + accounting fundamentals + price, which is exactly why they can add NEW signal.")


def derive_decision(camp: Dict, exhausted_labels: List[str], n_tickers, n_events) -> Tuple[str, str]:
    candidates = camp["candidates"]
    strong = [c for c in candidates if c["status"] == "strong"]
    constrained = [c for c in candidates if c["status"] == "constrained"]
    if strong:
        names = ", ".join(sorted(c["name"] for c in strong))
        return (DEC_STRONG,
                "%d candidate(s) cleared the FULL strong-alpha gate (>=%d tickers, >=%d events, IC "
                "t>=%.1f, BH-significant, net-of-25bps positive, positive IC in BOTH cohorts and BOTH "
                "pre/post-2020 halves, sector-diversified): %s. Strong, broad, robust alpha - carry to "
                "out-of-sample paper-trading validation (still preview-only, no orders)."
                % (len(strong), STRONG_MIN_TICKERS, STRONG_MIN_EVENTS, STRONG_MIN_IC_T, names))
    if camp["exhausted"]:
        why_constrained = ""
        if constrained:
            why_constrained = (" %d candidate(s) were statistically strong but CONSTRAINED "
                               "(old-cohort-only / single-sector) and are explicitly rejected for this "
                               "phase as not good enough: %s."
                               % (len(constrained), ", ".join(sorted(c["name"] for c in constrained))))
        return (DEC_NEW_DATA,
                "The current EODHD data families are EXHAUSTED with no strong broad alpha.%s "
                "Recommended NEW data family: %s. %s"
                % (why_constrained, _NEW_DATA_FAMILY, _NEW_DATA_RATIONALE))
    return (DEC_NO_STRONG,
            "No strong broad alpha found within the bounded search (max_cycles/max_scenarios reached "
            "before the hypothesis space was exhausted). Re-run with higher bounds to finish the "
            "remaining families before concluding the data is exhausted. Constrained/weak candidates "
            "are logged but NOT promoted.")


def _next_command(decision: str) -> Tuple[str, str]:
    if decision == DEC_STRONG:
        return ("Validate the strong candidate out-of-sample in the Paper Trader daily-review cockpit "
                "as a PREVIEW-ONLY ranking signal (manual review, no orders) before any reliance.",
                "Out-of-sample preview validation of the strong alpha")
    if decision == DEC_NEW_DATA:
        return ("Phase 8-Y: acquire a NEW orthogonal data family (%s) under the same bounded, "
                "secret-safe, gitignored-payload discipline, then re-run the discovery campaign. Do "
                "NOT productize the constrained earnings-surprise signal." % _NEW_DATA_FAMILY,
                "Acquire a new orthogonal data family (current families exhausted)")
    if decision == DEC_NO_STRONG:
        return ("python research/run_phase8x_autonomous_strong_alpha_discovery.py --max-cycles 30 "
                "--max-scenarios 500   (finish the remaining hypothesis space)",
                "Continue the autonomous search to exhaust the hypothesis space")
    if decision == DEC_BLOCKER:
        return ("Restore the gitignored Phase 8-V expanded panel + EODHD earnings cache (re-run "
                "Phase 8-V --live with a PAID EODHD_API_KEY), then re-run this campaign.",
                "Clear the hard blocker (missing expanded data), then re-run")
    return ("python research/run_phase8x_autonomous_strong_alpha_discovery.py", "Re-run the campaign")


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
class _Paths:
    def __init__(self, out_dir=None, phase8v_dir=None, phase8w_dir=None):
        self.out = Path(out_dir) if out_dir else _OUT_DIR
        self.phase8v = Path(phase8v_dir) if phase8v_dir else _PHASE8V_DIR
        self.phase8w = Path(phase8w_dir) if phase8w_dir else _PHASE8W_DIR
        for key, name in _ARTIFACTS.items():
            setattr(self, key, self.out / name)


def run(out_dir: Optional[Path] = None, phase8v_dir: Optional[Path] = None,
        phase8w_dir: Optional[Path] = None, price_csv: Optional[Path] = None,
        data_dir: Optional[Path] = None, sector_csv: Optional[Path] = None,
        macro: Optional[Dict[str, Path]] = None, phase8n_dir: Optional[Path] = None,
        phase8r_dir: Optional[Path] = None, as_of: str = s8.DEFAULT_AS_OF,
        max_cycles: int = DEFAULT_MAX_CYCLES, max_scenarios: int = DEFAULT_MAX_SCENARIOS,
        min_tickers: int = STRONG_MIN_TICKERS, min_events: int = STRONG_MIN_EVENTS,
        fresh: bool = True, verbose: bool = True) -> Dict:
    P = _Paths(out_dir, phase8v_dir, phase8w_dir)
    P.out.mkdir(parents=True, exist_ok=True)
    log = t8._Log(verbose)
    try:
        import pandas as pd  # noqa: F401

        # 1. Read the Phase 8-V (expansion verdict + new cohort) and Phase 8-W (constrained finding).
        v8 = _read_json(P.phase8v / _PHASE8V_REPORT)
        newly = [str(t).upper() for t in (v8.get("newly_scoreable_tickers") or [])]
        new_set = set(newly)
        v8_decision = v8.get("decision", "(none read)")
        w8rep = _read_json(P.phase8w / _PHASE8W_REPORT)
        w8dec = _read_json(P.phase8w / _PHASE8W_DECISION)
        w8_decision = w8rep.get("decision") or w8dec.get("decision", "(none read)")
        w8_constrained = (w8rep.get("constrained_promoted_variants")
                          or w8dec.get("constrained_promoted_variants") or [])
        w8_weak_sectors = w8rep.get("weak_sectors") or w8dec.get("weak_sectors") or []
        log.step("read_prior", "DONE",
                 "8-V=%s (newly-scoreable=%d); 8-W=%s (constrained variants=%d)"
                 % (v8_decision, len(newly), w8_decision, len(w8_constrained)), count=len(newly))

        # 2. Rebuild the expanded event table (same gitignored panel + earnings cache 8-V/8-W used).
        panel = Path(price_csv) if price_csv else _EXPANDED_PANEL
        P_score = s8._Paths(out_dir=P.out, data_dir=data_dir, phase8n_dir=phase8n_dir,
                            price_csv=panel, sector_csv=sector_csv, macro=macro,
                            phase8r_dir=phase8r_dir)
        ev, stats, _audit = w8.build_expanded_ev(P_score, as_of, log)
        if getattr(ev, "empty", True) or stats.get("events_usable", 0) == 0:
            return _finish_blocker(P, log, v8_decision, w8_decision,
                                   "No usable point-in-time events on the expanded panel (%s). The "
                                   "gitignored 8-V expanded panel + EODHD earnings cache must be "
                                   "present to run the discovery campaign." % _rel(panel), verbose)
        ev = w8.tag_cohort(ev, new_set)
        _eod_norm = Path(P_score.eodhd_dir) / "normalized" / "eod_prices"
        ev = w8.attach_liquidity_proxy(ev, panel, extra_csvs=[s8._PRICE_CACHE_CSV],
                                       eod_norm_dir=_eod_norm)
        n_tickers = stats.get("tickers_usable", 0) or _n_tickers(ev)
        n_events = stats.get("events_usable", 0) or int(len(ev))
        n_old = int((ev["cohort"] == "old").sum())
        n_new = int((ev["cohort"] == "new").sum())
        log.step("universe", "DONE", "%d scoreable tickers / %d events (old %d / new %d)"
                 % (n_tickers, n_events, n_old, n_new), count=n_tickers)

        # 3. Autonomous discovery loop.
        resume = load_resume_state(P.resume_state, fresh)

        def _persist(state):
            _write_json(P.resume_state, state)

        camp = run_campaign(ev, n_tickers, n_events, as_of, max_cycles, max_scenarios,
                            min_tickers, min_events, resume, _persist, log)
        candidates = camp["candidates"]
        scen_cands = [c for c in candidates if c["kind"] == "scenario"]
        model_cands = [c for c in candidates if c["kind"] == "model"]
        strong = [c for c in candidates if c["status"] == "strong"]
        constrained = [c for c in candidates if c["status"] == "constrained"]
        rejected = [c for c in candidates if c["status"] == "rejected"]

        # 4. Data-family exhaustion + decision.
        exh_rows, exhausted_labels = data_family_exhaustion(candidates, camp["exhausted"])
        decision, rationale = derive_decision(camp, exhausted_labels, n_tickers, n_events)
        log.step("decision", "DONE", decision)

        # 5. Write all artifacts.
        _write_artifacts(P, camp, scen_cands, model_cands, strong, constrained, rejected, exh_rows,
                        log, w8_decision, w8_constrained)

        next_cmd, next_title = _next_command(decision)
        _write_json(P.next_plan, {
            "phase": "8-Y", "title": next_title, "depends_on_decision": decision,
            "next_command": next_cmd,
            "recommended_new_data_family": (_NEW_DATA_FAMILY if decision == DEC_NEW_DATA else None),
            "preview_only": True, "orders_enabled": False, "automation_enabled": False,
            "broker_execution_enabled": False, "paper_trader_touched": False, "committed": False})

        _write_csv(P.run_log, ["step", "status", "detail", "count"], log.rows)

        # 6. Secret/safety audit (this phase reads only cached data; no key, no network).
        written = [p for p in P.out.glob("*") if p.is_file()]
        leak_clean, scanned, marker_found = r8._scan_for_leaks(written)
        _write_csv(P.secret_audit, ["check", "result", "detail"],
                   [["api_key_used", False, "campaign reads only the cached expanded panel + earnings"],
                    ["network_used", False, "no external API call in this phase"],
                    ["api_key_printed", False, "key value never referenced"],
                    ["committed_artifacts_contain_key_query_param", marker_found,
                     "leak scan over %d committed files" % scanned],
                    ["secret_leak_scan_clean", leak_clean, "no key value or key query param"]])

        best = _best_candidate(candidates)
        report = {
            "phase": PHASE,
            "objective": ("Autonomous strong-alpha discovery on the expanded 545-ticker EODHD "
                          "universe: search broad alpha families AND transparent walk-forward factor-"
                          "ensemble models until a genuinely STRONG broad alpha is found or the current "
                          "data families are proven exhausted. Preview-only; no orders; no data "
                          "acquisition; constrained signals are NOT productized."),
            "builds_on_phase8v_decision": v8_decision, "builds_on_phase8w_decision": w8_decision,
            "phase8w_constrained_variants": w8_constrained, "phase8w_weak_sectors": w8_weak_sectors,
            "as_of": as_of,
            "strong_alpha_standard": {
                "min_scoreable_tickers": min_tickers, "min_pit_events": min_events,
                "min_ic_t": STRONG_MIN_IC_T, "bh_q": GATE_BH_Q,
                "min_spread_hit_rate": STRONG_MIN_HIT_RATE, "hard_cost_bps": 25,
                "max_sector_share": STRONG_MAX_SECTOR_SHARE,
                "requires_both_cohorts_positive": True, "requires_subperiod_stable": True},
            "universe": {"scoreable_tickers": n_tickers, "usable_events": n_events,
                         "old_cohort_events": n_old, "new_cohort_events": n_new,
                         "newly_scoreable_from_8v": len(newly),
                         "meets_strong_universe_floor": bool(n_tickers >= min_tickers
                                                             and n_events >= min_events)},
            "autonomous_loop": {"max_cycles": max_cycles, "max_scenarios": max_scenarios,
                                "cycles_run": camp["cycles_run"],
                                "scenarios_tested": camp["scenarios_tested"],
                                "hypothesis_space_exhausted": camp["exhausted"],
                                "resume_state": _rel(P.resume_state)},
            "scenarios_tested": len(scen_cands), "models_tested": len(model_cands),
            "model_candidates_tested": len(model_cands),
            "strong_alpha_candidates": sorted(c["name"] for c in strong),
            "strong_alpha_found": bool(strong),
            "constrained_not_good_enough": sorted(c["name"] for c in constrained),
            "rejected_count": len(rejected),
            "best_candidate": (best or {}).get("name"),
            "best_candidate_ic": _g((best or {}).get("metrics", {}), "mean_ic"),
            "best_candidate_ic_t": _g((best or {}).get("metrics", {}), "ic_t", 2),
            "best_candidate_status": (best or {}).get("status"),
            "data_families_exhausted": exhausted_labels,
            "current_data_exhausted": camp["exhausted"] and not strong,
            "recommended_new_data_family": (_NEW_DATA_FAMILY if decision == DEC_NEW_DATA else None),
            "decision": decision, "decision_is_terminal": True,
            "allowed_decisions": list(ALLOWED_DECISIONS), "decision_rationale": rationale,
            "recommended_next_command": next_cmd,
            "secret_safety_leak_scan_clean": leak_clean, "secret_safety_files_scanned": scanned,
            "outputs": {k: _rel(getattr(P, k)) for k in _ARTIFACTS},
            "required_artifacts": [_ARTIFACTS[k] for k in _REQUIRED_ARTIFACTS],
            # safety / provenance contract
            "preview_only": True, "orders_enabled": False, "automation_enabled": False,
            "broker_execution_enabled": False, "production_replacement": False,
            "network_used": False, "data_acquired": False, "constrained_signal_productized": False,
            "paper_trader_touched": False, "gcp_touched": False, "deployed": False,
            "full_regression_invoked": False, "data_fabricated": False, "alpha_fabricated": False,
            "raw_paid_data_in_artifacts": False, "wrote_to_d_drive": False, "committed": False,
        }
        _write_json(P.report, report)
        if verbose:
            _print_summary(report)
        return report
    except Exception as exc:  # pragma: no cover - defensive; never leaks a key
        report = {"phase": PHASE, "decision": DEC_ERROR, "decision_is_terminal": True,
                  "allowed_decisions": list(ALLOWED_DECISIONS),
                  "error": "%s: %s" % (type(exc).__name__, exc),
                  "committed": False, "preview_only": True, "orders_enabled": False,
                  "automation_enabled": False, "paper_trader_touched": False, "gcp_touched": False}
        try:
            _write_json(P.report, report)
            _write_csv(P.run_log, ["step", "status", "detail", "count"], log.rows)
        except Exception:
            pass
        if verbose:
            print("ERROR: %s" % report["error"])
        return report


def _best_candidate(candidates):
    ranked = [c for c in candidates
              if c["metrics"].get("ic_t") is not None
              and not (isinstance(c["metrics"].get("ic_t"), float)
                       and math.isnan(c["metrics"].get("ic_t")))]
    return max(ranked, key=lambda c: c["metrics"]["ic_t"], default=None)


# --------------------------------------------------------------------------- #
# Artifact writers.
# --------------------------------------------------------------------------- #
def _cand_row_common(c):
    m = c["metrics"]
    return [c["name"], c["kind"], c["family"], c.get("weighting", ""), c.get("sector_neutral"),
            m.get("n_events", 0), m.get("n_months", 0), _g(m, "mean_ic"), _g(m, "ic_t", 2),
            _g(m, "ic_p"), c.get("bh_significant", False), _g(m, "mean_spread"),
            _g(m, "spread_hit_rate"), _g(m, "net_spread_25bps"), _g(m, "net_spread_50bps"),
            m.get("subperiod_stable", False), c.get("ic_old"), c.get("ic_new"),
            _g(m, "top_sector_share"), c["status"]]


_CAND_HDR = ["name", "kind", "family", "weighting", "sector_neutral", "n_events", "n_months",
             "mean_ic", "ic_t", "ic_p", "bh_significant", "mean_spread", "spread_hit_rate",
             "net_spread_25bps", "net_spread_50bps", "subperiod_stable", "ic_old", "ic_new",
             "top_sector_share", "status"]


def _write_artifacts(P, camp, scen_cands, model_cands, strong, constrained, rejected, exh_rows,
                     log, w8_decision, w8_constrained):
    candidates = camp["candidates"]

    _write_csv(P.cycle_log, ["cycle", "tested_this_cycle", "cumulative_tested", "note"],
               [[c["cycle"], c["tested_this_cycle"], c["cumulative_tested"], c["note"]]
                for c in camp["cycle_rows"]])

    _write_csv(P.hypothesis_registry,
               ["name", "kind", "family", "cycle", "input_hash", "ic_t", "bh_significant", "status",
                "reject_reasons"],
               [[c["name"], c["kind"], c["family"], c.get("cycle"), c.get("input_hash", ""),
                 _g(c["metrics"], "ic_t", 2), c.get("bh_significant", False), c["status"],
                 "; ".join(c.get("strong_reasons", []))] for c in candidates])

    _write_csv(P.scenario_scoreboard, _CAND_HDR, [_cand_row_common(c) for c in scen_cands])
    _write_csv(P.model_scoreboard, _CAND_HDR, [_cand_row_common(c) for c in model_cands])

    _write_csv(P.strong_candidates,
               _CAND_HDR + ["all_strong_gate_checks_passed"],
               [_cand_row_common(c) + [True] for c in strong])
    _write_csv(P.constrained,
               _CAND_HDR + ["constraint_reasons"],
               [_cand_row_common(c) + ["; ".join(c.get("strong_reasons", []))] for c in constrained])
    _write_csv(P.rejected,
               ["name", "kind", "family", "ic_t", "bh_significant", "reject_reasons"],
               [[c["name"], c["kind"], c["family"], _g(c["metrics"], "ic_t", 2),
                 c.get("bh_significant", False), "; ".join(c.get("strong_reasons", []))]
                for c in rejected])

    _write_csv(P.walk_forward,
               ["model", "window", "train_start", "train_end", "test_start", "test_end",
                "n_test_months", "oos_mean_ic"],
               [[w.get("model", ""), w["window"], w["train_start"], w["train_end"], w["test_start"],
                 w["test_end"], w["n_test_months"], _round(w["oos_mean_ic"])]
                for w in camp["window_rows"]])

    _write_csv(P.decile_spread,
               ["name", "kind", "family", "mean_decile_spread", "decile_hit_rate", "n_months",
                "top_decile_ret", "bottom_decile_ret"],
               [[c["name"], c["kind"], c["family"], _round(c["decile"]["mean_decile_spread"]),
                 _round(c["decile"]["decile_hit_rate"]), c["decile"]["n_months"],
                 _round(c["decile"]["top_decile_ret"]), _round(c["decile"]["bottom_decile_ret"])]
                for c in candidates])

    _write_csv(P.tcost,
               ["name", "kind", "gross_spread", "avg_turnover", "net_10bps", "net_25bps", "net_50bps"],
               [[c["name"], c["kind"], _g(c["metrics"], "mean_spread"), _g(c["metrics"], "avg_turnover"),
                 _g(c["metrics"], "net_spread_10bps"), _g(c["metrics"], "net_spread_25bps"),
                 _g(c["metrics"], "net_spread_50bps")] for c in candidates])

    _write_csv(P.turnover,
               ["name", "kind", "avg_top_quintile_turnover", "interpretation"],
               [[c["name"], c["kind"], _g(c["metrics"], "avg_turnover"),
                 "fraction of top-quintile names replaced month-over-month"] for c in candidates])

    _write_csv(P.cohort_stability,
               ["name", "kind", "family", "ic_old", "t_old", "ic_new", "t_new",
                "both_cohorts_positive", "old_cohort_only"],
               [[c["name"], c["kind"], c["family"], c.get("ic_old"), c.get("t_old"), c.get("ic_new"),
                 c.get("t_new"),
                 bool(c.get("ic_old") is not None and c.get("ic_new") is not None
                      and c.get("ic_old") > 0 and c.get("ic_new") > 0),
                 bool(c.get("ic_old") is not None and (c.get("ic_old") or 0) > 0
                      and (c.get("ic_new") is None or (c.get("ic_new") or 0) <= 0))]
                for c in candidates])

    _write_csv(P.sector_conc,
               ["name", "kind", "top_sector", "top_sector_share", "hhi", "single_sector_dominated"],
               [[c["name"], c["kind"], c["metrics"].get("top_sector", ""),
                 _g(c["metrics"], "top_sector_share"), _g(c["metrics"], "hhi"),
                 bool(c["metrics"].get("top_sector_share") is not None
                      and not (isinstance(c["metrics"].get("top_sector_share"), float)
                               and math.isnan(c["metrics"].get("top_sector_share")))
                      and c["metrics"].get("top_sector_share") > STRONG_MAX_SECTOR_SHARE)]
                for c in candidates])

    _write_csv(P.subperiod,
               ["name", "kind", "first_half_ic", "second_half_ic", "subperiod_stable", "n_months"],
               [[c["name"], c["kind"], _g(c["metrics"], "h1_ic"), _g(c["metrics"], "h2_ic"),
                 c["metrics"].get("subperiod_stable", False), c["metrics"].get("n_months", 0)]
                for c in candidates])

    _write_csv(P.multiple_testing,
               ["name", "kind", "ic_p", "bh_q", "bh_significant", "exploratory"],
               [[c["name"], c["kind"], _g(c["metrics"], "ic_p"), GATE_BH_Q,
                 c.get("bh_significant", False), c.get("exploratory", False)] for c in candidates])

    _write_csv(P.data_exhaustion,
               ["alpha_family", "hypotheses_tested", "best_ic_t", "best_ic_t_constrained_or_strong",
                "best_hypothesis", "any_strong_broad_alpha", "family_exhausted_no_strong_alpha"],
               exh_rows)
    log.step("artifacts", "DONE", "wrote %d required artifacts" % len(_REQUIRED_ARTIFACTS))


def _finish_blocker(P, log, v8_decision, w8_decision, detail, verbose) -> Dict:
    """Write the required artifacts as empty-but-present shells + a terminal HARD_BLOCKER when the
    gitignored expanded panel is absent. Keeps the contract: a terminal decision + all required
    artifacts, never an order or a key."""
    log.step("build_ev", "ABORT", detail)
    empties = {
        "cycle_log": ["cycle", "tested_this_cycle", "cumulative_tested", "note"],
        "hypothesis_registry": ["name", "kind", "family", "cycle", "input_hash", "ic_t",
                                "bh_significant", "status", "reject_reasons"],
        "scenario_scoreboard": _CAND_HDR, "model_scoreboard": _CAND_HDR,
        "strong_candidates": _CAND_HDR + ["all_strong_gate_checks_passed"],
        "constrained": _CAND_HDR + ["constraint_reasons"],
        "rejected": ["name", "kind", "family", "ic_t", "bh_significant", "reject_reasons"],
        "walk_forward": ["model", "window", "train_start", "train_end", "test_start", "test_end",
                         "n_test_months", "oos_mean_ic"],
        "decile_spread": ["name", "kind", "family", "mean_decile_spread", "decile_hit_rate",
                          "n_months", "top_decile_ret", "bottom_decile_ret"],
        "tcost": ["name", "kind", "gross_spread", "avg_turnover", "net_10bps", "net_25bps",
                  "net_50bps"],
        "turnover": ["name", "kind", "avg_top_quintile_turnover", "interpretation"],
        "cohort_stability": ["name", "kind", "family", "ic_old", "t_old", "ic_new", "t_new",
                             "both_cohorts_positive", "old_cohort_only"],
        "sector_conc": ["name", "kind", "top_sector", "top_sector_share", "hhi",
                        "single_sector_dominated"],
        "subperiod": ["name", "kind", "first_half_ic", "second_half_ic", "subperiod_stable",
                      "n_months"],
        "multiple_testing": ["name", "kind", "ic_p", "bh_q", "bh_significant", "exploratory"],
        "data_exhaustion": ["alpha_family", "hypotheses_tested", "best_ic_t",
                            "best_ic_t_constrained_or_strong", "best_hypothesis",
                            "any_strong_broad_alpha", "family_exhausted_no_strong_alpha"],
    }
    for key, hdr in empties.items():
        _write_csv(getattr(P, key), hdr, [])
    next_cmd, next_title = _next_command(DEC_BLOCKER)
    _write_json(P.next_plan, {
        "phase": "8-Y", "title": next_title, "depends_on_decision": DEC_BLOCKER,
        "next_command": next_cmd, "recommended_new_data_family": None,
        "preview_only": True, "orders_enabled": False, "automation_enabled": False,
        "paper_trader_touched": False, "committed": False})
    _write_csv(P.run_log, ["step", "status", "detail", "count"], log.rows)
    written = [p for p in P.out.glob("*") if p.is_file()]
    leak_clean, scanned, marker_found = r8._scan_for_leaks(written)
    _write_csv(P.secret_audit, ["check", "result", "detail"],
               [["api_key_used", False, "reads only cached data"],
                ["network_used", False, "no external API call"],
                ["secret_leak_scan_clean", leak_clean, "leak scan over %d files" % scanned]])
    report = {
        "phase": PHASE,
        "objective": "Autonomous strong-alpha discovery (aborted: no event table on the expanded panel).",
        "builds_on_phase8v_decision": v8_decision, "builds_on_phase8w_decision": w8_decision,
        "decision": DEC_BLOCKER, "decision_is_terminal": True,
        "allowed_decisions": list(ALLOWED_DECISIONS), "decision_rationale": detail,
        "universe": {"scoreable_tickers": 0, "usable_events": 0, "meets_strong_universe_floor": False},
        "autonomous_loop": {"cycles_run": 0, "scenarios_tested": 0,
                            "hypothesis_space_exhausted": False},
        "scenarios_tested": 0, "models_tested": 0, "model_candidates_tested": 0,
        "strong_alpha_candidates": [], "strong_alpha_found": False,
        "constrained_not_good_enough": [], "rejected_count": 0,
        "current_data_exhausted": False, "recommended_new_data_family": None,
        "data_families_exhausted": [],
        "recommended_next_command": next_cmd,
        "secret_safety_leak_scan_clean": leak_clean, "secret_safety_files_scanned": scanned,
        "outputs": {k: _rel(getattr(P, k)) for k in _ARTIFACTS},
        "required_artifacts": [_ARTIFACTS[k] for k in _REQUIRED_ARTIFACTS],
        "preview_only": True, "orders_enabled": False, "automation_enabled": False,
        "broker_execution_enabled": False, "production_replacement": False, "network_used": False,
        "data_acquired": False, "constrained_signal_productized": False,
        "paper_trader_touched": False, "gcp_touched": False, "deployed": False,
        "full_regression_invoked": False, "data_fabricated": False, "alpha_fabricated": False,
        "raw_paid_data_in_artifacts": False, "wrote_to_d_drive": False, "committed": False,
    }
    _write_json(P.report, report)
    if verbose:
        _print_summary(report)
    return report


def _print_summary(report: Dict) -> None:
    print("phase:                 %s" % report.get("phase"))
    uni = report.get("universe", {})
    print("scoreable / events:    %s / %s" % (uni.get("scoreable_tickers"), uni.get("usable_events")))
    loop = report.get("autonomous_loop", {})
    print("cycles / scenarios:    %s / %s" % (loop.get("cycles_run"), loop.get("scenarios_tested")))
    print("models tested:         %s" % report.get("models_tested"))
    print("best candidate:        %s (ic_t %s, %s)"
          % (report.get("best_candidate"), report.get("best_candidate_ic_t"),
             report.get("best_candidate_status")))
    print("strong candidates:     %s" % report.get("strong_alpha_candidates"))
    print("constrained (rejected):%s" % report.get("constrained_not_good_enough"))
    print("data exhausted:        %s" % report.get("current_data_exhausted"))
    print("new data family:       %s" % report.get("recommended_new_data_family"))
    print("decision:              %s" % report.get("decision"))
    print("next command:          %s" % report.get("recommended_next_command"))


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=("Phase 8-X autonomous strong-alpha discovery campaign (offline; reuses the "
                     "gitignored 8-V expanded panel + EODHD earnings cache; no network, no key)."))
    ap.add_argument("--as-of", default=s8.DEFAULT_AS_OF,
                    help="Score only events announced on/before this date (default %s)." % s8.DEFAULT_AS_OF)
    ap.add_argument("--max-cycles", type=int, default=DEFAULT_MAX_CYCLES)
    ap.add_argument("--max-scenarios", type=int, default=DEFAULT_MAX_SCENARIOS)
    ap.add_argument("--resume", action="store_true",
                    help="Resume from the durable autonomous_resume_state.json (skip already-tested "
                         "hypotheses with unchanged inputs).")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    run(as_of=args.as_of, max_cycles=args.max_cycles, max_scenarios=args.max_scenarios,
        fresh=not args.resume, verbose=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
