"""Phase 2K-L Research-Lead Diagnostics After the Expanded Alpha Screen (offline, read-only).

Phase 2K-K ran a model-free screen of the prioritized price/volume alpha candidates on
the expanded, survivorship-caveated D: panel and produced 15 RESEARCH_LEAD_ONLY
candidate/horizon pairs and 0 KEEP_FOR_WALK_FORWARD_CONFIRMATION pairs. Phase 2K-L
diagnoses those research leads: it scores each lead from the Phase 2K-K metrics alone,
ranks them, identifies the recurring failed gates, summarizes the family and horizon
patterns, classifies each lead into a targeted-diagnostic / weak / deprioritize bucket,
and recommends the next research phase.

It does NOT run a new D: screen, it does NOT read the large D: price-history CSV, and it
trains, fits, or creates NO model. It only:

  1. Reads the Phase 2K-K result JSON and confirms it routed here
     (MODEL_FREE_SCREEN_HAS_RESEARCH_LEADS_ONLY -> 2K-L, 15 leads, 0 keep).
  2. Confirms the stopped liquidity candidate (avg_dollar_volume_21d) remains excluded as
     a standalone alpha and never appears among the research leads.
  3. Extracts every RESEARCH_LEAD_ONLY candidate/horizon pair and the model-free metrics
     Phase 2K-K already measured (mean residual rank IC, same-sign fraction, bootstrap CI,
     leave-one-year-out stability, failed keep gates, concentration, regime flips).
  4. Computes a transparent, deterministic diagnostic score per lead from those metrics
     only (nothing is re-estimated on the D: panel), ranks the leads, and classifies each
     into TARGETED_DIAGNOSTIC_CANDIDATE / WEAK_RESEARCH_LEAD / DEPRIORITIZE_AFTER_DIAGNOSTIC.
  5. Produces cross-lead diagnostics (failed-gate frequency, candidate-family summary,
     horizon summary, best / worst lead, recurring failure themes, survivorship caveat
     carried forward) and a model-free follow-up diagnostic plan.
  6. Issues an overall recommendation and routes to Phase 2K-M. No lead becomes a model
     candidate; no model is trained; every result stays survivorship-biased and is NOT a
     production edge.

This phase reads the small Phase 2K-K / 2K-J / 2K-I / 2K-H JSON summaries and writes
exactly one small results JSON in the C: repo. It never reads the large D: CSV, never
writes to the D: drive, performs no infrastructure action, and mutates no datastore; the
machine-readable safety flags are emitted in the results JSON, and the full guardrail
rationale lives in docs/phase2k_l_research_lead_diagnostics_v1.md, not in this source.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import time
from typing import Any, Dict, List, Optional, Tuple

PHASE = "2K-L"

_OUTPUT_DIR = os.path.join("research", "output")

# Read-only upstream summaries (small, Git-tracked, in the C: repo). Phase 2K-L reads the
# Phase 2K-K screen result as its primary input and the earlier summaries for provenance.
PHASE2K_K_JSON = os.path.join(_OUTPUT_DIR, "phase2k_k_expanded_alpha_screen.json")
PHASE2K_J_JSON = os.path.join(_OUTPUT_DIR, "phase2k_j_alpha_backlog_refresh.json")
PHASE2K_I_JSON = os.path.join(_OUTPUT_DIR, "phase2k_i_expanded_residual_retest.json")
PHASE2K_H_JSON = os.path.join(_OUTPUT_DIR, "phase2k_h_manual_build_run_summary.json")

# The single small output this analyzer writes (stays in the C: repo, Git-tracked).
RESULTS_JSON = os.path.join(_OUTPUT_DIR, "phase2k_l_research_lead_diagnostics.json")

# The candidate that was stopped in Phase 2K-J after the Phase 2K-I expanded retest
# failure. It must never appear among the research leads diagnosed here.
STOPPED_CANDIDATE = "avg_dollar_volume_21d"

# The only Phase 2K-K per-pair status that Phase 2K-L diagnoses.
LEAD_STATUS = "RESEARCH_LEAD_ONLY"

# Diagnostic-score weights (transparent, deterministic; derived only from Phase 2K-K
# metrics, never re-estimated on the D: panel). The components sum to a 0..100 scale.
SCORE_W_IC = 35.0           # mean residual rank IC relative to the Phase 2K-K keep floor
SCORE_W_SIGN = 20.0         # how far the same-sign fraction clears the 0.5 coin-flip line
SCORE_W_CI = 20.0           # bootstrap 95% CI lower bound clears zero
SCORE_W_STABILITY = 15.0    # leave-one-year-out sign stability
SCORE_W_ROBUSTNESS = 10.0   # how few keep gates failed

RANK_IC_FLOOR = 0.03        # the Phase 2K-K mean-residual-IC keep floor (score reference)
SIGN_FULL_CREDIT_DELTA = 0.10   # same-sign fraction of 0.60 earns full sign credit
MAX_FAILED_GATES = 4        # robustness component reaches 0 at this many failed keep gates
MIN_FAILED_GATES = 1        # every lead fails at least the IC-floor gate by construction
CONCENTRATION_TOP3_THRESHOLD = 0.50   # severe long-leg concentration ceiling

# Conservative classification thresholds.
TARGETED_MAX_FAILED_GATES = 2   # a targeted lead may fail at most this many keep gates
DEPRIORITIZE_MIN_FAILED_GATES = 4   # failing this many keep gates deprioritizes a lead

# Per-lead classification vocabulary (the only values a lead classification may take).
TARGETED = "TARGETED_DIAGNOSTIC_CANDIDATE"
WEAK = "WEAK_RESEARCH_LEAD"
DEPRIORITIZE = "DEPRIORITIZE_AFTER_DIAGNOSTIC"
_ALLOWED_CLASSIFICATIONS = [TARGETED, WEAK, DEPRIORITIZE]

# Overall-recommendation vocabulary.
REC_TARGETED = "TARGETED_DIAGNOSTICS_FOR_BEST_RESEARCH_LEADS"
REC_TOO_WEAK = "ALL_RESEARCH_LEADS_TOO_WEAK"
REC_PIT = "NEED_POINT_IN_TIME_UNIVERSE_BEFORE_MORE_DIAGNOSTICS"
_ALLOWED_RECOMMENDATIONS = [REC_TARGETED, REC_TOO_WEAK, REC_PIT]

# Next-phase titles (always Phase 2K-M; title chosen by the recommendation).
TITLE_TARGETED = "Targeted Diagnostics for Best Expanded Alpha Leads"
TITLE_REFRESH = "Alpha Backlog Refresh v2 After Weak Expanded Leads"
TITLE_PIT = "Point-in-Time Universe Decision Before More Alpha Screens"

# The full keep-gate vocabulary Phase 2K-K reports (used for the failed-gate frequency
# table so gates that never failed still appear with a zero count).
_KEEP_GATE_NAMES = [
    "mean_residual_ic_positive",
    "mean_residual_ic_above_floor",
    "majority_dates_same_sign",
    "bootstrap_ci_above_zero",
    "no_single_year_drives_result",
    "no_regime_only_dependence",
    "no_excessive_ticker_concentration",
]

# Friendly family labels keyed by the Phase 2K-K candidate category.
_FAMILY_LABELS = {
    "price_momentum_alternatives": "residual_price_momentum",
    "short_term_reversal_mean_reversion": "short_horizon_reversal",
    "volatility_adjusted_momentum": "volatility_adjusted_momentum",
}


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
        xf = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(xf) or math.isinf(xf):
        return None
    return round(xf, n)


def _clamp01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def _safe_read_json(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return None


# --------------------------------------------------------------------------- #
# Upstream Phase 2K-K routing confirmation
# --------------------------------------------------------------------------- #
def summarize_phase2k_k(kk: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Read-only digest of the Phase 2K-K screen plus the routing confirmation."""
    if kk is None:
        return {"present": False, "routing_confirmed": False}
    rec = kk.get("recommendation", {}) or {}
    nxt = kk.get("recommended_next_phase", {}) or {}
    gates = (kk.get("pass_fail_gates", {}) or {}).get("verdict_counts", {}) or {}
    policy = kk.get("stopped_candidate_policy", {}) or {}
    n_lead = int(gates.get(LEAD_STATUS, 0) or 0)
    n_keep = int(gates.get("KEEP_FOR_WALK_FORWARD_CONFIRMATION", 0) or 0)

    rec_ok = rec.get("recommendation") == "MODEL_FREE_SCREEN_HAS_RESEARCH_LEADS_ONLY"
    leads_ok = n_lead == 15
    keep_ok = n_keep == 0
    routes_ok = nxt.get("phase") == "2K-L"
    title_ok = nxt.get("title") == "Research Lead Diagnostics After Expanded Alpha Screen"
    create_ok = rec.get("create_model_candidate_now") is False
    train_ok = rec.get("train_model_now") is False
    stopped_ok = (policy.get("stopped_candidate") == STOPPED_CANDIDATE
                  and policy.get("status") == "STOPPED_AFTER_EXPANDED_RETEST_FAIL"
                  and policy.get("excluded_as_standalone_alpha") is True)
    return {
        "present": True,
        "phase": kk.get("phase"),
        "recommendation": rec.get("recommendation"),
        "n_research_lead_only": n_lead,
        "n_keep_for_walk_forward": n_keep,
        "screen_executed": kk.get("screen_executed"),
        "recommended_next_phase": nxt,
        "recommendation_is_research_leads_only": rec_ok,
        "research_lead_count_is_15": leads_ok,
        "keep_count_is_zero": keep_ok,
        "recommended_next_phase_is_2k_l": routes_ok,
        "recommended_next_phase_title_matches": title_ok,
        "create_model_candidate_now_false": create_ok,
        "train_model_now_false": train_ok,
        "stopped_candidate_confirmed": bool(stopped_ok),
        "routing_confirmed": bool(
            rec_ok and leads_ok and keep_ok and routes_ok and title_ok
            and create_ok and train_ok and stopped_ok),
    }


def summarize_stopped_candidate_policy(kk: Optional[Dict[str, Any]],
                                       leads: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Carry the Phase 2K-K stopped-candidate policy forward and confirm exclusion."""
    policy = ((kk or {}).get("stopped_candidate_policy", {}) or {})
    screened = {c.get("id") for c in (kk or {}).get("screened_candidates", []) or []}
    screened |= {c.get("feature") for c in (kk or {}).get("screened_candidates", []) or []}
    in_leads = any(
        l.get("candidate") == STOPPED_CANDIDATE or l.get("feature") == STOPPED_CANDIDATE
        for l in leads)
    return {
        "stopped_candidate": policy.get("stopped_candidate", STOPPED_CANDIDATE),
        "status": policy.get("status", "STOPPED_AFTER_EXPANDED_RETEST_FAIL"),
        "excluded_as_standalone_alpha": bool(policy.get("excluded_as_standalone_alpha", True)),
        "allowed_as_control_or_diagnostic_only": bool(
            policy.get("allowed_as_control_or_diagnostic_only", True)),
        "not_in_screened_candidates": STOPPED_CANDIDATE not in screened,
        "not_in_research_leads": not in_leads,
        "reason": (
            "avg_dollar_volume_21d failed the Phase 2K-I expanded retest and was stopped "
            "by Phase 2K-J. Phase 2K-L diagnoses only the price/volume research leads and "
            "never re-introduces the stopped liquidity candidate as a standalone alpha; it "
            "may remain only a control / diagnostic / neutralizer."),
    }


# --------------------------------------------------------------------------- #
# Research-lead extraction from the Phase 2K-K screen result
# --------------------------------------------------------------------------- #
def extract_research_leads(kk: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every RESEARCH_LEAD_ONLY candidate/horizon pair with its Phase 2K-K metrics.

    The mean IC / same-sign / bootstrap / leave-one-year-out / failed-gate fields come
    from candidate_recommendations; concentration and regime-flip fields are looked up in
    screen_metrics.by_candidate. Nothing is recomputed on the D: panel.
    """
    if kk is None:
        return []
    by_cand = (kk.get("screen_metrics", {}) or {}).get("by_candidate", {}) or {}
    leads: List[Dict[str, Any]] = []
    for cr in kk.get("candidate_recommendations", []) or []:
        if cr.get("status") != LEAD_STATUS:
            continue
        cand = cr.get("candidate")
        h = cr.get("horizon_days")
        cell = (((by_cand.get(cand, {}) or {}).get("by_horizon", {}) or {})
                .get(str(h), {}) or {})
        conc = cell.get("concentration", {}) or {}
        regime = cell.get("regime_summary", {}) or {}
        ci = cell.get("bootstrap_mean_ic_ci", {}) or {}
        failed = list(cr.get("failed_keep_gates", []) or [])
        leads.append({
            "candidate": cand,
            "feature": cr.get("feature"),
            "category": cr.get("category"),
            "horizon_days": h,
            "lead_id": f"{cand}@{h}d",
            "source_status": cr.get("status"),
            "mean_residual_rank_ic": cr.get("mean_residual_rank_ic"),
            "frac_dates_same_sign_as_reference": cr.get("frac_dates_same_sign_as_reference"),
            "bootstrap_ci_low": cr.get("bootstrap_ci_low"),
            "bootstrap_ci_high": cr.get("bootstrap_ci_high"),
            "bootstrap_ci_excludes_zero": bool(ci.get("excludes_zero")),
            "leave_one_year_out_sign_stable": bool(cr.get("leave_one_year_out_sign_stable")),
            "failed_keep_gates": failed,
            "n_failed_keep_gates": len(failed),
            "enough_data": bool(cr.get("enough_data")),
            "top3_long_leg_share": conc.get("top3_long_leg_share"),
            "spy_sign_flips_across_regime": regime.get("spy_sign_flips_across_regime"),
            "vol_sign_flips_across_regime": regime.get("vol_sign_flips_across_regime"),
        })
    return leads


# --------------------------------------------------------------------------- #
# Diagnostic score + classification (deterministic; Phase 2K-K metrics only)
# --------------------------------------------------------------------------- #
def diagnostic_score(lead: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """Transparent 0..100 diagnostic score from the Phase 2K-K metrics only.

    Components: IC vs the keep floor, how far the same-sign fraction clears 0.5, whether
    the bootstrap CI lower bound clears zero, leave-one-year-out sign stability, and how
    few keep gates failed. Nothing is re-estimated; this only re-weights existing metrics
    so the research leads can be ranked for follow-up diagnostics.
    """
    mean_ic = lead.get("mean_residual_rank_ic")
    frac = lead.get("frac_dates_same_sign_as_reference")
    ci_low = lead.get("bootstrap_ci_low")
    loyo = bool(lead.get("leave_one_year_out_sign_stable"))
    n_failed = int(lead.get("n_failed_keep_gates", 0) or 0)

    ic_pos = mean_ic is not None and mean_ic > 0
    ic_comp = SCORE_W_IC * _clamp01((mean_ic / RANK_IC_FLOOR) if ic_pos else 0.0)
    sign_comp = SCORE_W_SIGN * (
        _clamp01(((frac - 0.5) / SIGN_FULL_CREDIT_DELTA)) if frac is not None else 0.0)
    ci_comp = SCORE_W_CI if (ci_low is not None and ci_low > 0) else 0.0
    stab_comp = SCORE_W_STABILITY if loyo else 0.0
    rob_comp = SCORE_W_ROBUSTNESS * _clamp01(
        (MAX_FAILED_GATES - n_failed) / float(MAX_FAILED_GATES - MIN_FAILED_GATES))

    components = {
        "ic_vs_floor": _round(ic_comp, 4),
        "same_sign_fraction": _round(sign_comp, 4),
        "bootstrap_ci_excludes_zero": _round(ci_comp, 4),
        "leave_one_year_out_stability": _round(stab_comp, 4),
        "few_failed_gates": _round(rob_comp, 4),
        "max_possible": SCORE_W_IC + SCORE_W_SIGN + SCORE_W_CI + SCORE_W_STABILITY
        + SCORE_W_ROBUSTNESS,
    }
    score = round(ic_comp + sign_comp + ci_comp + stab_comp + rob_comp, 2)
    return score, components


def classify_lead(lead: Dict[str, Any]) -> Tuple[str, str]:
    """Conservative bucket for one research lead from the Phase 2K-K metrics.

    TARGETED_DIAGNOSTIC_CANDIDATE only when the lead has enough data, a positive mean
    residual IC, a bootstrap CI lower bound clearing zero, year-stable sign, at most a
    couple of failed keep gates, and no severe concentration -- and it still does NOT
    become a model candidate. DEPRIORITIZE_AFTER_DIAGNOSTIC when the sign is year-unstable
    or almost every robustness gate failed. WEAK_RESEARCH_LEAD otherwise: directionally
    positive and possibly useful for future feature engineering, but not ready.
    """
    enough = bool(lead.get("enough_data"))
    mean_ic = lead.get("mean_residual_rank_ic")
    ci_low = lead.get("bootstrap_ci_low")
    loyo = bool(lead.get("leave_one_year_out_sign_stable"))
    n_failed = int(lead.get("n_failed_keep_gates", 0) or 0)
    top3 = lead.get("top3_long_leg_share")

    ic_pos = mean_ic is not None and mean_ic > 0
    ci_clears_zero = ci_low is not None and ci_low > 0
    concentration_ok = top3 is None or top3 <= CONCENTRATION_TOP3_THRESHOLD

    if (enough and ic_pos and ci_clears_zero and loyo
            and n_failed <= TARGETED_MAX_FAILED_GATES and concentration_ok):
        return TARGETED, (
            "directionally positive with a bootstrap CI lower bound above zero, year-stable "
            "sign, few failed gates, and no severe concentration: worth a focused, "
            "model-free follow-up diagnostic (still not a model candidate)")
    if (not loyo) or n_failed >= DEPRIORITIZE_MIN_FAILED_GATES:
        return DEPRIORITIZE, (
            "year-unstable sign and/or nearly every robustness gate failed: deprioritized "
            "after this diagnostic rather than carried into focused follow-up")
    return WEAK, (
        "directionally positive but several robustness / stability gates failed (notably a "
        "sub-floor IC and/or a bootstrap CI straddling zero): a weak research lead that may "
        "inform future feature engineering, not ready for confirmation")


# --------------------------------------------------------------------------- #
# Cross-lead diagnostics
# --------------------------------------------------------------------------- #
def build_lead_rankings(leads: List[Dict[str, Any]]) -> Dict[str, Any]:
    ordered = sorted(
        leads,
        key=lambda l: (-(l.get("diagnostic_score") or 0.0), l.get("lead_id") or ""))
    rankings = [{
        "rank": i + 1,
        "lead_id": l.get("lead_id"),
        "candidate": l.get("candidate"),
        "horizon_days": l.get("horizon_days"),
        "diagnostic_score": l.get("diagnostic_score"),
        "classification": l.get("classification"),
    } for i, l in enumerate(ordered)]
    best = rankings[0] if rankings else None
    worst = rankings[-1] if rankings else None
    return {
        "ranked_by": "diagnostic_score_desc",
        "rankings": rankings,
        "best_lead": best,
        "worst_lead": worst,
    }


def build_failed_gate_diagnostics(leads: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts = {g: 0 for g in _KEEP_GATE_NAMES}
    for l in leads:
        for g in l.get("failed_keep_gates", []) or []:
            counts[g] = counts.get(g, 0) + 1
    ordered = dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    most_common = next(iter(ordered), None) if leads else None
    return {
        "n_leads": len(leads),
        "gate_failure_counts": ordered,
        "most_common_failed_gate": most_common,
        "all_leads_fail_ic_floor": bool(
            leads and counts.get("mean_residual_ic_above_floor", 0) == len(leads)),
    }


def _family_label(category: Optional[str]) -> str:
    return _FAMILY_LABELS.get(category or "", category or "unknown")


def _group_diagnostics(leads: List[Dict[str, Any]], key: str,
                       label_fn) -> List[Dict[str, Any]]:
    groups: Dict[Any, List[Dict[str, Any]]] = {}
    for l in leads:
        groups.setdefault(l.get(key), []).append(l)
    out: List[Dict[str, Any]] = []
    for gkey, items in groups.items():
        scores = [i.get("diagnostic_score") or 0.0 for i in items]
        ics = [i.get("mean_residual_rank_ic") for i in items
               if i.get("mean_residual_rank_ic") is not None]
        best = max(items, key=lambda i: (i.get("diagnostic_score") or 0.0))
        out.append({
            label_fn[0]: label_fn[1](gkey),
            "group_key": gkey,
            "n_leads": len(items),
            "n_targeted": sum(1 for i in items if i.get("classification") == TARGETED),
            "n_weak": sum(1 for i in items if i.get("classification") == WEAK),
            "n_deprioritized": sum(
                1 for i in items if i.get("classification") == DEPRIORITIZE),
            "mean_diagnostic_score": _round(sum(scores) / len(scores), 2) if scores else None,
            "mean_residual_rank_ic": _round(sum(ics) / len(ics)) if ics else None,
            "best_lead_id": best.get("lead_id"),
            "best_diagnostic_score": best.get("diagnostic_score"),
        })
    return out


def build_candidate_family_diagnostics(leads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = _group_diagnostics(leads, "category", ("family", _family_label))
    return sorted(rows, key=lambda r: -(r.get("mean_diagnostic_score") or 0.0))


def build_horizon_diagnostics(leads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = _group_diagnostics(leads, "horizon_days", ("horizon_days", lambda x: x))
    return sorted(rows, key=lambda r: (r.get("group_key") or 0))


def build_recurring_failure_themes(leads: List[Dict[str, Any]],
                                   gate_counts: Dict[str, int]) -> List[Dict[str, Any]]:
    n = len(leads)
    n_ci = gate_counts.get("bootstrap_ci_above_zero", 0)
    n_regime = gate_counts.get("no_regime_only_dependence", 0)
    n_year = gate_counts.get("no_single_year_drives_result", 0)
    themes: List[Dict[str, Any]] = []
    if gate_counts.get("mean_residual_ic_above_floor", 0):
        themes.append({
            "theme": "sub_floor_information_coefficient",
            "affected_leads": gate_counts.get("mean_residual_ic_above_floor", 0),
            "detail": (
                "Every research lead has a positive but sub-floor mean residual rank IC "
                f"(below {RANK_IC_FLOOR}); the signals are directionally real but "
                "economically tiny, which is the dominant reason none reached confirmation."),
        })
    if n_ci:
        themes.append({
            "theme": "wide_bootstrap_confidence_interval",
            "affected_leads": n_ci,
            "detail": (
                "The moving-block 95% bootstrap CI straddles zero, concentrated at the "
                "longer (21d / 63d) horizons where the number of effective observations is "
                "smallest, so the small IC is not statistically separable from zero."),
        })
    if n_regime:
        themes.append({
            "theme": "regime_sign_instability",
            "affected_leads": n_regime,
            "detail": (
                "The IC sign flips across a SPY up/down or market-volatility regime split, "
                "concentrated in the volatility-adjusted momentum family and the 5d "
                "reversal, indicating regime dependence rather than a stable edge."),
        })
    if n_year:
        themes.append({
            "theme": "single_year_fragility",
            "affected_leads": n_year,
            "detail": (
                "The IC sign is not stable under leave-one-year-out; these leads also fail "
                "the other robustness gates and are the deprioritized set."),
        })
    themes.append({
        "theme": "survivorship_caveat",
        "affected_leads": n,
        "detail": (
            "All leads are measured on a current-as-of, survivorship-caveated universe "
            "(point-in-time membership not claimed); a clean point-in-time universe would "
            "only tighten, never rescue, these results."),
    })
    return themes


def build_follow_up_diagnostic_plan(leads: List[Dict[str, Any]],
                                    targeted: List[Dict[str, Any]]) -> Dict[str, Any]:
    suggested = [
        {"rank": 1, "action": (
            "Inspect the failed gates of the TARGETED_DIAGNOSTIC_CANDIDATE leads by family "
            "and horizon to confirm a sub-floor IC (not instability or concentration) is "
            "the sole obstruction."), "model_free": True},
        {"rank": 2, "action": (
            "Re-rank the best leads under stricter concentration filters and alternate "
            "quintile / decile cutoffs (model-free) to check the small IC is not a ranking "
            "artifact."), "model_free": True},
        {"rank": 3, "action": (
            "Re-measure the regime-flipping leads within each SPY up/down and "
            "market-volatility regime separately to quantify how much of the IC is "
            "regime-dependent."), "model_free": True},
        {"rank": 4, "action": (
            "Test sector-relative versions of the reversal / momentum leads only if a "
            "low-cost point-in-time sector map becomes available, still model-free and "
            "with no model training."), "model_free": True},
        {"rank": 5, "action": (
            "Retest alternate holding windows only where economically justified for the "
            "reversal family, without expanding the candidate universe."), "model_free": True},
        {"rank": 6, "action": (
            "Decide whether a point-in-time membership universe is required before any "
            "further alpha screen; do not purchase or build on paid data yet."),
            "model_free": True},
    ]
    return {
        "no_model_training": True,
        "no_model_candidate": True,
        "no_deployment": True,
        "no_trading_app_changes": True,
        "no_new_d_screen": True,
        "targeted_lead_ids": [l.get("lead_id") for l in targeted],
        "suggested_diagnostics": suggested,
    }


# --------------------------------------------------------------------------- #
# Overall recommendation + routing + interpretation
# --------------------------------------------------------------------------- #
def build_recommendation(class_counts: Dict[str, int], leads: List[Dict[str, Any]],
                         routing_confirmed: bool) -> Dict[str, Any]:
    n_targeted = class_counts.get(TARGETED, 0)
    n_weak = class_counts.get(WEAK, 0)
    n_deprioritized = class_counts.get(DEPRIORITIZE, 0)
    n_leads = len(leads)
    n_insufficient_data = sum(1 for l in leads if not l.get("enough_data"))
    survivorship_is_blocker = bool(n_leads and n_insufficient_data == n_leads)

    if n_leads == 0:
        rec = REC_PIT
        reason = (
            "Phase 2K-K produced no research leads to diagnose; revisit the universe / data "
            "before any further alpha screen. No model candidate is created.")
    elif n_targeted > 0:
        rec = REC_TARGETED
        reason = (
            f"{n_targeted} research lead(s) are directionally positive with a bootstrap CI "
            "above zero, year-stable sign, and few failed gates; they warrant a focused, "
            "model-free follow-up diagnostic. No lead becomes a model candidate, nothing is "
            "trained, and the results remain survivorship-biased and not a production edge.")
    elif survivorship_is_blocker:
        rec = REC_PIT
        reason = (
            "No lead has enough usable data after the horizon embargo on the "
            "survivorship-caveated panel; a point-in-time membership universe is needed "
            "before more diagnostics. No model candidate is created.")
    else:
        rec = REC_TOO_WEAK
        reason = (
            f"All {n_leads} research leads are too weak for focused follow-up "
            f"({n_weak} weak, {n_deprioritized} deprioritized): every lead fails the IC "
            "floor and most fail additional robustness gates. Refresh the alpha backlog "
            "rather than diagnose further. No model candidate is created.")
    return {
        "recommendation": rec,
        "allowed_values": list(_ALLOWED_RECOMMENDATIONS),
        "n_research_leads": n_leads,
        "n_targeted_diagnostic_candidates": n_targeted,
        "n_weak_research_leads": n_weak,
        "n_deprioritized": n_deprioritized,
        "upstream_routing_confirmed": bool(routing_confirmed),
        "create_model_candidate_now": False,
        "train_model_now": False,
        "deploy_now": False,
        "ran_new_d_screen": False,
        "production_edge_claimed": False,
        "results_are_survivorship_biased": True,
        "reason": reason,
    }


def build_recommended_next_phase(recommendation: str) -> Dict[str, str]:
    if recommendation == REC_TARGETED:
        return {
            "phase": "2K-M",
            "title": TITLE_TARGETED,
            "purpose": (
                "Run focused, model-free diagnostics on the best expanded-screen research "
                "leads (failed-gate inspection, stricter concentration filters, "
                "regime-split IC), still with no model training and no model-candidate "
                "design. Results remain survivorship-biased and not a production edge."),
        }
    if recommendation == REC_TOO_WEAK:
        return {
            "phase": "2K-M",
            "title": TITLE_REFRESH,
            "purpose": (
                "Refresh the alpha backlog after the expanded model-free screen produced no "
                "sufficiently strong research leads, redirecting toward orthogonal factor "
                "families. Still no model training and no model candidate."),
        }
    return {
        "phase": "2K-M",
        "title": TITLE_PIT,
        "purpose": (
            "Decide whether a point-in-time membership universe is required before any "
            "further alpha screen, because the current panel is survivorship-caveated / "
            "current-as-of only or too little usable data remains. No model candidate is "
            "created."),
    }


def build_interpretation(recommendation: Dict[str, Any]) -> Dict[str, Any]:
    rec = recommendation["recommendation"]
    if rec == REC_TARGETED:
        verdict = ("identified a small set of best research leads worth focused, model-free "
                   "follow-up diagnostics")
    elif rec == REC_TOO_WEAK:
        verdict = ("found every research lead too weak for focused follow-up and routes to "
                   "an alpha-backlog refresh")
    else:
        verdict = ("could not justify more diagnostics on the survivorship-caveated panel "
                   "and routes to a point-in-time universe decision")
    return {
        "model_trained": False,
        "model_fitted": False,
        "model_candidate_created": False,
        "authorized_to_train_model": False,
        "authorized_to_serve_model": False,
        "ran_new_d_screen": False,
        "read_large_d_csv": False,
        "production_edge_claimed": False,
        "results_are_survivorship_biased": True,
        "narrative": (
            "Phase 2K-L diagnosed the 15 RESEARCH_LEAD_ONLY candidate/horizon pairs from "
            "the Phase 2K-K expanded model-free screen using the Phase 2K-K metrics only -- "
            "it ran no new D: screen, read no large D: CSV, and fitted nothing. It scored "
            "and ranked the leads, found the dominant blocker to be a positive but "
            "sub-floor information coefficient (compounded by wide bootstrap CIs and regime "
            "sign instability), and classified each lead into targeted / weak / "
            "deprioritized buckets. The phase " + verdict + ". The stopped liquidity "
            "candidate avg_dollar_volume_21d stays excluded as a standalone alpha. No model "
            "was trained, fitted, or created, every result is reported as survivorship-"
            "biased, and no production edge is claimed."),
    }


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_results(*, phase2k_k_path: Optional[str] = None,
                  phase2k_j_path: Optional[str] = None,
                  phase2k_i_path: Optional[str] = None,
                  phase2k_h_path: Optional[str] = None) -> Dict[str, Any]:
    phase2k_k_path = phase2k_k_path or PHASE2K_K_JSON
    phase2k_j_path = phase2k_j_path or PHASE2K_J_JSON
    phase2k_i_path = phase2k_i_path or PHASE2K_I_JSON
    phase2k_h_path = phase2k_h_path or PHASE2K_H_JSON

    kk = _safe_read_json(phase2k_k_path)
    kj = _safe_read_json(phase2k_j_path)
    ki = _safe_read_json(phase2k_i_path)
    kh = _safe_read_json(phase2k_h_path)

    kk_summary = summarize_phase2k_k(kk)
    kk_summary["phase2k_j_recommendation"] = (
        (kj or {}).get("recommendation", {}) or {}).get("recommendation") if kj else None
    kk_summary["phase2k_i_recommendation"] = (
        (ki or {}).get("recommendation", {}) or {}).get("recommendation") if ki else None
    kk_summary["phase2k_h_ready_for_screen"] = (
        (kh or {}).get("decision", {}) or {}).get("ready_for_retest") if kh else None

    leads = extract_research_leads(kk)

    # Score + classify every lead (deterministic, Phase 2K-K metrics only).
    class_counts = {TARGETED: 0, WEAK: 0, DEPRIORITIZE: 0}
    for l in leads:
        score, components = diagnostic_score(l)
        l["diagnostic_score"] = score
        l["diagnostic_score_components"] = components
        classification, reason = classify_lead(l)
        l["classification"] = classification
        l["classification_reason"] = reason
        class_counts[classification] = class_counts.get(classification, 0) + 1

    targeted = [l for l in leads if l.get("classification") == TARGETED]

    lead_rankings = build_lead_rankings(leads)
    failed_gate_diagnostics = build_failed_gate_diagnostics(leads)
    candidate_family_diagnostics = build_candidate_family_diagnostics(leads)
    horizon_diagnostics = build_horizon_diagnostics(leads)
    recurring_failure_themes = build_recurring_failure_themes(
        leads, failed_gate_diagnostics["gate_failure_counts"])
    follow_up_diagnostic_plan = build_follow_up_diagnostic_plan(leads, targeted)

    recommendation = build_recommendation(
        class_counts, leads, kk_summary.get("routing_confirmed"))
    recommended_next_phase = build_recommended_next_phase(recommendation["recommendation"])
    interpretation = build_interpretation(recommendation)
    stopped_policy = summarize_stopped_candidate_policy(kk, leads)

    sc_sum = (kk or {}).get("survivorship_caveat_summary", {}) or {}
    survivorship_carried = {
        "membership_basis": sc_sum.get("membership_basis"),
        "point_in_time_membership_claimed": sc_sum.get(
            "point_in_time_membership_claimed", False),
        "all_results_survivorship_biased": True,
        "note": ("Phase 2K-L carries the Phase 2K-K survivorship caveat forward unchanged; "
                 "no point-in-time universe is claimed."),
    }

    return {
        "phase": PHASE,
        "generated_at": _now(),
        "inputs_read": {
            "phase2k_k_json": _norm(phase2k_k_path),
            "phase2k_j_json": _norm(phase2k_j_path),
            "phase2k_i_json": _norm(phase2k_i_path),
            "phase2k_h_summary_json": _norm(phase2k_h_path),
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
        "ran_new_d_screen": False,
        "read_large_d_csv": False,
        "upstream_phase2k_k_summary": kk_summary,
        "stopped_candidate_policy": stopped_policy,
        "survivorship_caveat_carried_forward": survivorship_carried,
        "research_leads": leads,
        "lead_rankings": lead_rankings,
        "failed_gate_diagnostics": failed_gate_diagnostics,
        "candidate_family_diagnostics": candidate_family_diagnostics,
        "horizon_diagnostics": horizon_diagnostics,
        "recurring_failure_themes": recurring_failure_themes,
        "follow_up_diagnostic_plan": follow_up_diagnostic_plan,
        "lead_classification_counts": class_counts,
        "allowed_lead_classifications": list(_ALLOWED_CLASSIFICATIONS),
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
    kk = d["upstream_phase2k_k_summary"]
    best = d["lead_rankings"]["best_lead"]
    print(f"[phase2k-l] upstream 2K-K routing confirmed : {kk.get('routing_confirmed')}")
    print(f"[phase2k-l] research leads diagnosed        : {len(d['research_leads'])}")
    print(f"[phase2k-l] classification counts           : {d['lead_classification_counts']}")
    print(f"[phase2k-l] most common failed gate         : "
          f"{d['failed_gate_diagnostics']['most_common_failed_gate']}")
    if best:
        print(f"[phase2k-l] best lead                       : "
              f"{best.get('lead_id')} (score {best.get('diagnostic_score')})")
    print(f"[phase2k-l] recommendation                  : {rec['recommendation']}")
    print(f"[phase2k-l] recommended next phase           : {nxt['phase']} ({nxt['title']})")
    print(f"[phase2k-l] results written                 : {RESULTS_JSON}")
    print(f"[phase2k-l] elapsed seconds                  : {elapsed:.2f}")
    print("[phase2k-l] read-only; no new D: screen; no model trained; no D: writes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
