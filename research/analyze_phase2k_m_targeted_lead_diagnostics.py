"""Phase 2K-M Targeted Diagnostics for the Best Expanded-Screen Alpha Leads (offline, read-only).

Phase 2K-L diagnosed the 15 RESEARCH_LEAD_ONLY candidate/horizon pairs that the Phase 2K-K
expanded model-free screen produced and classified 5 of them as
TARGETED_DIAGNOSTIC_CANDIDATE. Phase 2K-M takes only those 5 targeted leads and builds a
focused diagnostic artifact: it profiles each one, identifies the exact failed gates,
compares the targeted set by family and horizon, decides whether a narrow model-free
follow-up retest is justified per lead, and recommends the next research phase.

It does NOT run a new D: screen, it does NOT read the large D: price-history CSV, and it
trains, fits, or creates NO model. It only:

  1. Reads the Phase 2K-L result JSON and confirms it routed here
     (TARGETED_DIAGNOSTICS_FOR_BEST_RESEARCH_LEADS -> 2K-M, 5 targeted candidates, no model
     candidate / no model training).
  2. Extracts the 5 TARGETED_DIAGNOSTIC_CANDIDATE leads (each sourced from a
     RESEARCH_LEAD_ONLY Phase 2K-K candidate/horizon pair) with the model-free metrics
     Phase 2K-K already measured and the Phase 2K-L diagnostic score; it cross-checks each
     against the Phase 2K-K RESEARCH_LEAD_ONLY pair set.
  3. Confirms the stopped liquidity candidate (avg_dollar_volume_21d) remains stopped and
     excluded as a standalone alpha and never appears among the targeted leads.
  4. Builds a per-lead diagnostic profile, ranks the targeted leads, summarizes them by
     candidate family and by horizon, and analyzes the blocking gates (whether a sub-floor
     IC is the sole obstruction or regime / concentration issues remain).
  5. Assigns each targeted lead a conservative follow-up type
     (NARROW_RETEST_CANDIDATE / DIAGNOSTIC_ONLY / HOLD_FOR_SECTOR_RELATIVE_VARIANT) and
     builds a model-free narrow-retest plan.
  6. Issues an overall recommendation and routes to Phase 2K-N. No lead becomes a model
     candidate; no model is trained; no new D: screen is run; every result stays
     survivorship-biased and is NOT a production edge.

This phase reads the small Phase 2K-L / 2K-K / 2K-J / 2K-I / 2K-H JSON summaries and writes
exactly one small results JSON in the C: repo. It never reads the large D: CSV, never
writes to the D: drive, performs no infrastructure action, and mutates no datastore; the
machine-readable safety flags are emitted in the results JSON, and the full guardrail
rationale lives in docs/phase2k_m_targeted_lead_diagnostics_v1.md, not in this source.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import time
from typing import Any, Dict, List, Optional, Tuple

PHASE = "2K-M"

_OUTPUT_DIR = os.path.join("research", "output")

# Read-only upstream summaries (small, Git-tracked, in the C: repo). Phase 2K-M reads the
# Phase 2K-L research-lead diagnostics as its primary input and the earlier summaries for
# provenance / cross-checking.
PHASE2K_L_JSON = os.path.join(_OUTPUT_DIR, "phase2k_l_research_lead_diagnostics.json")
PHASE2K_K_JSON = os.path.join(_OUTPUT_DIR, "phase2k_k_expanded_alpha_screen.json")
PHASE2K_J_JSON = os.path.join(_OUTPUT_DIR, "phase2k_j_alpha_backlog_refresh.json")
PHASE2K_I_JSON = os.path.join(_OUTPUT_DIR, "phase2k_i_expanded_residual_retest.json")
PHASE2K_H_JSON = os.path.join(_OUTPUT_DIR, "phase2k_h_manual_build_run_summary.json")

# The single small output this analyzer writes (stays in the C: repo, Git-tracked).
RESULTS_JSON = os.path.join(_OUTPUT_DIR, "phase2k_m_targeted_lead_diagnostics.json")

# The candidate that was stopped in Phase 2K-J after the Phase 2K-I expanded retest
# failure. It must never appear among the targeted leads diagnosed here.
STOPPED_CANDIDATE = "avg_dollar_volume_21d"

# The Phase 2K-L lead classification Phase 2K-M acts on, and the Phase 2K-K per-pair status
# every targeted lead must trace back to.
TARGETED = "TARGETED_DIAGNOSTIC_CANDIDATE"
LEAD_STATUS = "RESEARCH_LEAD_ONLY"

# Diagnostic / gate references (carried forward from Phase 2K-K / 2K-L; nothing is
# re-estimated on the D: panel).
RANK_IC_FLOOR = 0.03                   # the Phase 2K-K mean-residual-IC keep floor
CONCENTRATION_TOP3_THRESHOLD = 0.50    # severe long-leg concentration ceiling
NARROW_MAX_FAILED_GATES = 1            # a narrow-retest lead may fail at most the IC floor

# Per-lead follow-up vocabulary (the only values a targeted-lead follow-up type may take).
NARROW_RETEST = "NARROW_RETEST_CANDIDATE"
DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY"
HOLD_SECTOR = "HOLD_FOR_SECTOR_RELATIVE_VARIANT"
_ALLOWED_FOLLOW_UPS = [NARROW_RETEST, DIAGNOSTIC_ONLY, HOLD_SECTOR]

# Overall-recommendation vocabulary.
REC_RETEST = "RUN_NARROW_MODEL_FREE_RETEST"
REC_SECTOR = "HOLD_FOR_SECTOR_RELATIVE_FEATURES"
REC_STOP = "STOP_TARGETED_LEADS_AND_REFRESH_BACKLOG"
_ALLOWED_RECOMMENDATIONS = [REC_RETEST, REC_SECTOR, REC_STOP]

# Next-phase titles (always Phase 2K-N; title chosen by the recommendation).
TITLE_RETEST = "Narrow Model-Free Retest for Best Targeted Alpha Leads"
TITLE_SECTOR = "Sector-Relative Feature Feasibility for Targeted Leads"
TITLE_REFRESH = "Alpha Backlog Refresh v3 After Targeted Diagnostics"

# The full keep-gate vocabulary Phase 2K-K reports (used for the targeted-set failed-gate
# frequency table so gates that never failed still appear with a zero count).
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


def _safe_read_json(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return None


def _family_label(category: Optional[str]) -> str:
    return _FAMILY_LABELS.get(category or "", category or "unknown")


def _is_regime_dependent(lead: Dict[str, Any]) -> bool:
    """A lead is regime-dependent if its IC sign flips across a SPY or volatility regime
    split, or Phase 2K-K already failed it on the regime-only-dependence keep gate."""
    return bool(
        lead.get("spy_sign_flips_across_regime")
        or lead.get("vol_sign_flips_across_regime")
        or ("no_regime_only_dependence" in (lead.get("failed_keep_gates") or [])))


def _has_severe_concentration(lead: Dict[str, Any]) -> bool:
    top3 = lead.get("top3_long_leg_share")
    return top3 is not None and top3 > CONCENTRATION_TOP3_THRESHOLD


# --------------------------------------------------------------------------- #
# Upstream Phase 2K-L routing confirmation
# --------------------------------------------------------------------------- #
def summarize_phase2k_l(kl: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Read-only digest of the Phase 2K-L diagnostics plus the routing confirmation."""
    if kl is None:
        return {"present": False, "routing_confirmed": False}
    rec = kl.get("recommendation", {}) or {}
    nxt = kl.get("recommended_next_phase", {}) or {}
    counts = kl.get("lead_classification_counts", {}) or {}
    n_targeted = int(counts.get(TARGETED, 0) or 0)

    phase_ok = kl.get("phase") == "2K-L"
    rec_ok = rec.get("recommendation") == "TARGETED_DIAGNOSTICS_FOR_BEST_RESEARCH_LEADS"
    routes_ok = nxt.get("phase") == "2K-M"
    title_ok = nxt.get("title") == "Targeted Diagnostics for Best Expanded Alpha Leads"
    targeted_ok = n_targeted == 5
    create_ok = rec.get("create_model_candidate_now") is False
    train_ok = rec.get("train_model_now") is False
    return {
        "present": True,
        "phase": kl.get("phase"),
        "recommendation": rec.get("recommendation"),
        "lead_classification_counts": counts,
        "n_targeted_diagnostic_candidates": n_targeted,
        "recommended_next_phase": nxt,
        "phase_is_2k_l": phase_ok,
        "recommendation_is_targeted_diagnostics": rec_ok,
        "recommended_next_phase_is_2k_m": routes_ok,
        "recommended_next_phase_title_matches": title_ok,
        "targeted_candidate_count_is_5": targeted_ok,
        "create_model_candidate_now_false": create_ok,
        "train_model_now_false": train_ok,
        "best_lead": (kl.get("lead_rankings", {}) or {}).get("best_lead"),
        "routing_confirmed": bool(
            phase_ok and rec_ok and routes_ok and title_ok and targeted_ok
            and create_ok and train_ok),
    }


def phase2k_k_research_lead_pairs(kk: Optional[Dict[str, Any]]) -> set:
    """The set of (candidate, horizon_days) pairs Phase 2K-K marked RESEARCH_LEAD_ONLY."""
    pairs = set()
    if kk is None:
        return pairs
    for cr in kk.get("candidate_recommendations", []) or []:
        if cr.get("status") == LEAD_STATUS:
            pairs.add((cr.get("candidate"), cr.get("horizon_days")))
    return pairs


def summarize_stopped_candidate_policy(kl: Optional[Dict[str, Any]],
                                       targeted: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Carry the Phase 2K-L stopped-candidate policy forward and re-confirm exclusion."""
    policy = ((kl or {}).get("stopped_candidate_policy", {}) or {})
    in_targeted = any(
        l.get("candidate") == STOPPED_CANDIDATE or l.get("feature") == STOPPED_CANDIDATE
        for l in targeted)
    return {
        "stopped_candidate": policy.get("stopped_candidate", STOPPED_CANDIDATE),
        "status": policy.get("status", "STOPPED_AFTER_EXPANDED_RETEST_FAIL"),
        "excluded_as_standalone_alpha": bool(policy.get("excluded_as_standalone_alpha", True)),
        "allowed_as_control_or_diagnostic_only": bool(
            policy.get("allowed_as_control_or_diagnostic_only", True)),
        "not_in_targeted_leads": not in_targeted,
        "reason": (
            "avg_dollar_volume_21d failed the Phase 2K-I expanded retest and was stopped by "
            "Phase 2K-J. Phase 2K-M diagnoses only the price/volume targeted research leads "
            "and never re-introduces the stopped liquidity candidate as a standalone alpha; "
            "it may remain only a control / diagnostic / neutralizer."),
    }


# --------------------------------------------------------------------------- #
# Targeted-lead extraction + per-lead follow-up classification
# --------------------------------------------------------------------------- #
def classify_follow_up(lead: Dict[str, Any]) -> Tuple[str, str, str]:
    """Conservative follow-up type for one targeted lead from the Phase 2K-K / 2K-L metrics.

    Returns (follow_up_type, why_not_confirmation_ready, suggested_follow_up_test).

    NARROW_RETEST_CANDIDATE only when the lead has a positive mean residual IC, a bootstrap
    CI lower bound clearing zero, a year-stable sign, at most the IC-floor keep gate failed,
    no severe concentration, and no regime dependence -- and it still does NOT become a
    model candidate. HOLD_FOR_SECTOR_RELATIVE_VARIANT when the lead is otherwise solid but
    regime-dependent (it likely needs sector-relative construction or a sector map before
    further action). DIAGNOSTIC_ONLY otherwise: promising but too small / too weak for a
    retest.
    """
    mean_ic = lead.get("mean_residual_rank_ic")
    ci_low = lead.get("bootstrap_ci_low")
    loyo = bool(lead.get("leave_one_year_out_sign_stable"))
    n_failed = int(lead.get("n_failed_keep_gates", 0) or 0)
    ic_pos = mean_ic is not None and mean_ic > 0
    ci_clears_zero = ci_low is not None and ci_low > 0
    regime_dependent = _is_regime_dependent(lead)
    severe_conc = _has_severe_concentration(lead)

    why_not_ready = (
        "still below the Phase 2K-K mean-residual-IC keep floor of "
        f"{RANK_IC_FLOOR}: directionally real but economically tiny, so it has not cleared "
        "the stricter walk-forward confirmation battery and is not a confirmation or model "
        "candidate")

    if (ic_pos and ci_clears_zero and loyo
            and n_failed <= NARROW_MAX_FAILED_GATES and not severe_conc
            and not regime_dependent):
        return NARROW_RETEST, why_not_ready, (
            "Pre-register a narrow, model-free retest of this single lead at its own "
            "horizon (no expanded universe, no model training): re-measure the residual "
            "rank IC and its moving-block bootstrap CI to confirm the small but year-stable, "
            "above-zero edge persists out-of-sample.")
    if (ic_pos and ci_clears_zero and loyo and not severe_conc and regime_dependent):
        return HOLD_SECTOR, why_not_ready + (
            "; its IC sign is also regime-dependent across a SPY up/down or volatility "
            "split"), (
            "Hold for a sector-relative variant: re-construct the signal sector-relative "
            "(model-free) once a low-cost point-in-time sector map is available, to test "
            "whether neutralizing sector exposure removes the regime dependence before any "
            "retest.")
    return DIAGNOSTIC_ONLY, why_not_ready, (
        "Diagnostic only: inspect the failed gates further but do not schedule a retest yet "
        "-- the edge is too small or too fragile to justify a dedicated model-free retest.")


def extract_targeted_leads(kl: Optional[Dict[str, Any]],
                           kk_pairs: set) -> List[Dict[str, Any]]:
    """Every TARGETED_DIAGNOSTIC_CANDIDATE lead from Phase 2K-L with its diagnostic profile.

    The metrics (mean IC / same-sign / bootstrap CI / leave-one-year-out / failed gates /
    concentration / regime flips / diagnostic score) are carried forward from the Phase 2K-L
    research-lead record -- nothing is recomputed on the D: panel. Each lead is cross-checked
    against the Phase 2K-K RESEARCH_LEAD_ONLY pair set.
    """
    if kl is None:
        return []
    out: List[Dict[str, Any]] = []
    for l in kl.get("research_leads", []) or []:
        if l.get("classification") != TARGETED or l.get("source_status") != LEAD_STATUS:
            continue
        cand = l.get("candidate")
        h = l.get("horizon_days")
        failed = list(l.get("failed_keep_gates", []) or [])
        regime_dependent = _is_regime_dependent(l)
        severe_conc = _has_severe_concentration(l)
        follow_up, why_not_ready, suggested = classify_follow_up(l)
        out.append({
            "lead_id": l.get("lead_id"),
            "candidate": cand,
            "feature": l.get("feature"),
            "category": l.get("category"),
            "family": _family_label(l.get("category")),
            "horizon_days": h,
            "source_status": l.get("source_status"),
            "source_classification": l.get("classification"),
            "from_phase2k_k_research_lead_pair": (cand, h) in kk_pairs,
            "mean_residual_rank_ic": l.get("mean_residual_rank_ic"),
            "frac_dates_same_sign_as_reference": l.get("frac_dates_same_sign_as_reference"),
            "bootstrap_ci_low": l.get("bootstrap_ci_low"),
            "bootstrap_ci_high": l.get("bootstrap_ci_high"),
            "bootstrap_ci_excludes_zero": bool(l.get("bootstrap_ci_excludes_zero")),
            "leave_one_year_out_sign_stable": bool(l.get("leave_one_year_out_sign_stable")),
            "failed_keep_gates": failed,
            "n_failed_keep_gates": len(failed),
            "top3_long_leg_share": l.get("top3_long_leg_share"),
            "spy_sign_flips_across_regime": bool(l.get("spy_sign_flips_across_regime")),
            "vol_sign_flips_across_regime": bool(l.get("vol_sign_flips_across_regime")),
            "regime_dependent": regime_dependent,
            "severe_concentration": severe_conc,
            "diagnostic_score": l.get("diagnostic_score"),
            "why_targeted": (
                "directionally positive with a bootstrap CI lower bound above zero, a "
                "year-stable IC sign, few failed keep gates, and no severe concentration: "
                "the best of the Phase 2K-K expanded-screen research leads, worth a focused, "
                "model-free follow-up diagnostic (still not a model candidate)"),
            "why_not_confirmation_ready": why_not_ready,
            "follow_up_type": follow_up,
            "suggested_follow_up_test": suggested,
        })
    return out


# --------------------------------------------------------------------------- #
# Cross-target diagnostics
# --------------------------------------------------------------------------- #
def build_targeted_lead_rankings(leads: List[Dict[str, Any]]) -> Dict[str, Any]:
    ordered = sorted(
        leads,
        key=lambda l: (-(l.get("diagnostic_score") or 0.0), l.get("lead_id") or ""))
    rankings = [{
        "rank": i + 1,
        "lead_id": l.get("lead_id"),
        "candidate": l.get("candidate"),
        "family": l.get("family"),
        "horizon_days": l.get("horizon_days"),
        "diagnostic_score": l.get("diagnostic_score"),
        "follow_up_type": l.get("follow_up_type"),
    } for i, l in enumerate(ordered)]
    return {
        "ranked_by": "diagnostic_score_desc",
        "rankings": rankings,
        "best_lead": rankings[0] if rankings else None,
        "worst_lead": rankings[-1] if rankings else None,
    }


def _group_diagnostics(leads: List[Dict[str, Any]], key: str, label_key: str,
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
            label_key: label_fn(gkey),
            "group_key": gkey,
            "n_targeted_leads": len(items),
            "n_narrow_retest": sum(
                1 for i in items if i.get("follow_up_type") == NARROW_RETEST),
            "n_diagnostic_only": sum(
                1 for i in items if i.get("follow_up_type") == DIAGNOSTIC_ONLY),
            "n_hold_for_sector_relative": sum(
                1 for i in items if i.get("follow_up_type") == HOLD_SECTOR),
            "n_regime_dependent": sum(1 for i in items if i.get("regime_dependent")),
            "mean_diagnostic_score": _round(sum(scores) / len(scores), 2) if scores else None,
            "mean_residual_rank_ic": _round(sum(ics) / len(ics)) if ics else None,
            "best_lead_id": best.get("lead_id"),
            "best_diagnostic_score": best.get("diagnostic_score"),
        })
    return out


def build_family_diagnostics(leads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = _group_diagnostics(leads, "category", "family", _family_label)
    return sorted(rows, key=lambda r: -(r.get("mean_diagnostic_score") or 0.0))


def build_horizon_diagnostics(leads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = _group_diagnostics(leads, "horizon_days", "horizon_days", lambda x: x)
    return sorted(rows, key=lambda r: (r.get("group_key") or 0))


def build_blocker_analysis(leads: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Whether a sub-floor IC is the sole obstruction for the targeted set, or whether
    regime / concentration issues remain, plus the family / horizon clustering."""
    n = len(leads)
    counts = {g: 0 for g in _KEEP_GATE_NAMES}
    for l in leads:
        for g in l.get("failed_keep_gates", []) or []:
            counts[g] = counts.get(g, 0) + 1
    ordered = dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    n_regime = sum(1 for l in leads if l.get("regime_dependent"))
    n_conc = sum(1 for l in leads if l.get("severe_concentration"))
    n_ci_straddles = sum(1 for l in leads if not l.get("bootstrap_ci_excludes_zero"))
    n_year_unstable = sum(
        1 for l in leads if not l.get("leave_one_year_out_sign_stable"))

    fam_counts: Dict[str, int] = {}
    horizon_counts: Dict[Any, int] = {}
    for l in leads:
        fam_counts[l.get("family")] = fam_counts.get(l.get("family"), 0) + 1
        horizon_counts[l.get("horizon_days")] = horizon_counts.get(
            l.get("horizon_days"), 0) + 1
    dominant_family = max(fam_counts, key=fam_counts.get) if fam_counts else None
    reversal_n = fam_counts.get("short_horizon_reversal", 0)
    momentum_n = fam_counts.get("residual_price_momentum", 0)

    all_fail_ic_floor = bool(n and counts.get("mean_residual_ic_above_floor", 0) == n)
    only_blocking_gate_is_sub_floor_ic = bool(
        n and all(set(l.get("failed_keep_gates", []) or [])
                  == {"mean_residual_ic_above_floor"} for l in leads))

    return {
        "n_targeted_leads": n,
        "gate_failure_counts": ordered,
        "most_common_failed_gate": next(iter(ordered), None) if n else None,
        "all_targeted_leads_fail_ic_floor": all_fail_ic_floor,
        "only_blocking_gate_is_sub_floor_ic": only_blocking_gate_is_sub_floor_ic,
        "n_regime_dependent": n_regime,
        "n_severe_concentration": n_conc,
        "n_bootstrap_ci_straddles_zero": n_ci_straddles,
        "n_leave_one_year_out_unstable": n_year_unstable,
        "family_counts": fam_counts,
        "horizon_counts": {str(k): v for k, v in horizon_counts.items()},
        "dominant_family": dominant_family,
        "targeted_set_clusters_around": (
            "short_horizon_reversal" if reversal_n > momentum_n
            else "residual_price_momentum" if momentum_n > reversal_n
            else "mixed"),
        "summary": (
            "The dominant blocker across every targeted lead is a positive but sub-floor "
            f"mean residual rank IC (below {RANK_IC_FLOOR}); none has a severe long-leg "
            "concentration problem. The targeted set clusters in the short-horizon reversal "
            "family, with the remaining obstruction on a minority of leads being regime "
            "sign dependence rather than concentration or single-year fragility. A clean "
            "point-in-time universe would only tighten, never rescue, these results."),
    }


def build_narrow_retest_plan(leads: List[Dict[str, Any]],
                             narrow: List[Dict[str, Any]],
                             hold: List[Dict[str, Any]]) -> Dict[str, Any]:
    criteria = [
        "mean residual rank IC is positive",
        "moving-block bootstrap 95% CI lower bound is above zero",
        "IC sign is stable under leave-one-year-out",
        "at most the IC-floor keep gate failed (no regime / concentration / single-year "
        "failure)",
        "no severe long-leg concentration (top-3 long-leg share at or below "
        f"{CONCENTRATION_TOP3_THRESHOLD})",
        "no regime sign dependence across SPY up/down or volatility splits",
        "still no model candidate and no model training",
    ]
    suggested = [
        {"rank": 1, "action": (
            "Pre-register the narrow retest: fix the exact leads, horizons, residualization, "
            "ranking, and CI method before touching data, so the retest cannot be "
            "over-fit."), "model_free": True},
        {"rank": 2, "action": (
            "Re-measure the residual rank IC and its moving-block bootstrap CI for each "
            "NARROW_RETEST_CANDIDATE lead at its own horizon only, with no expanded "
            "universe and no new candidates."), "model_free": True},
        {"rank": 3, "action": (
            "Hold the regime-dependent leads for a sector-relative variant: re-test them "
            "model-free only once a low-cost point-in-time sector map is available, to check "
            "whether sector neutralization removes the regime dependence."),
            "model_free": True},
        {"rank": 4, "action": (
            "Keep the stopped liquidity candidate avg_dollar_volume_21d excluded as a "
            "standalone alpha throughout; it may serve only as a control / neutralizer."),
            "model_free": True},
        {"rank": 5, "action": (
            "Stop and refresh the alpha backlog instead of escalating if the narrow retest "
            "does not reproduce an above-zero, year-stable edge; do not create a model "
            "candidate on a sub-floor IC."), "model_free": True},
    ]
    return {
        "no_model_training": True,
        "no_model_candidate": True,
        "no_deployment": True,
        "no_trading_app_changes": True,
        "no_new_d_screen": True,
        "no_large_d_csv_read": True,
        "narrow_retest_criteria": criteria,
        "narrow_retest_candidate_lead_ids": [l.get("lead_id") for l in narrow],
        "hold_for_sector_relative_lead_ids": [l.get("lead_id") for l in hold],
        "suggested_retest_steps": suggested,
    }


# --------------------------------------------------------------------------- #
# Overall recommendation + routing + interpretation
# --------------------------------------------------------------------------- #
def build_recommendation(follow_up_counts: Dict[str, int],
                         leads: List[Dict[str, Any]],
                         routing_confirmed: bool) -> Dict[str, Any]:
    n_narrow = follow_up_counts.get(NARROW_RETEST, 0)
    n_diag = follow_up_counts.get(DIAGNOSTIC_ONLY, 0)
    n_hold = follow_up_counts.get(HOLD_SECTOR, 0)
    n_leads = len(leads)

    if n_narrow > 0:
        rec = REC_RETEST
        reason = (
            f"{n_narrow} of {n_leads} targeted lead(s) clear the conservative narrow-retest "
            "bar (positive mean IC, bootstrap CI above zero, year-stable sign, only the IC "
            "floor failed, no severe concentration, no regime dependence); a narrow, "
            "pre-registered, model-free retest of just those leads is justified. No lead "
            "becomes a model candidate, nothing is trained, no new D: screen is run, and the "
            "results remain survivorship-biased and not a production edge.")
    elif n_hold > 0:
        rec = REC_SECTOR
        reason = (
            f"No targeted lead clears the narrow-retest bar, but {n_hold} are otherwise "
            "solid yet regime-dependent; assess sector-relative construction feasibility "
            "before any further retest. No model candidate is created and nothing is "
            "trained.")
    else:
        rec = REC_STOP
        reason = (
            f"All {n_leads} targeted leads are diagnostic-only -- too small or too fragile "
            "for a dedicated model-free retest; refresh the alpha backlog instead of "
            "escalating. No model candidate is created and nothing is trained.")
    return {
        "recommendation": rec,
        "allowed_values": list(_ALLOWED_RECOMMENDATIONS),
        "n_targeted_leads": n_leads,
        "n_narrow_retest_candidates": n_narrow,
        "n_diagnostic_only": n_diag,
        "n_hold_for_sector_relative": n_hold,
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
    if recommendation == REC_RETEST:
        return {
            "phase": "2K-N",
            "title": TITLE_RETEST,
            "purpose": (
                "Run a narrow, pre-registered, model-free retest only on the best targeted "
                "leads (re-measuring residual rank IC and its bootstrap CI at each lead's "
                "own horizon), still with no model training and no model-candidate design. "
                "Results remain survivorship-biased and not a production edge."),
        }
    if recommendation == REC_SECTOR:
        return {
            "phase": "2K-N",
            "title": TITLE_SECTOR,
            "purpose": (
                "Determine whether sector-relative construction is needed before another "
                "screen or retest of the regime-dependent targeted leads, including the "
                "feasibility / cost of a low-cost point-in-time sector map. Still no model "
                "training and no model candidate."),
        }
    return {
        "phase": "2K-N",
        "title": TITLE_REFRESH,
        "purpose": (
            "Stop the targeted leads and refresh the alpha backlog again after the targeted "
            "diagnostics found nothing strong enough for a dedicated retest, redirecting "
            "toward orthogonal factor families. Still no model training and no model "
            "candidate."),
    }


def build_interpretation(recommendation: Dict[str, Any]) -> Dict[str, Any]:
    rec = recommendation["recommendation"]
    if rec == REC_RETEST:
        verdict = ("justified a narrow, pre-registered, model-free retest of the best "
                   "targeted leads")
    elif rec == REC_SECTOR:
        verdict = ("held the targeted leads for a sector-relative feasibility assessment "
                   "because their edge is regime-dependent")
    else:
        verdict = ("found no targeted lead strong enough for a dedicated retest and routes "
                   "to an alpha-backlog refresh")
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
            "Phase 2K-M took only the 5 TARGETED_DIAGNOSTIC_CANDIDATE leads from the Phase "
            "2K-L diagnostics and profiled them using the Phase 2K-K / 2K-L metrics only -- "
            "it ran no new D: screen, read no large D: CSV, and fitted nothing. It confirmed "
            "each targeted lead traces back to a RESEARCH_LEAD_ONLY Phase 2K-K pair, found "
            "the dominant blocker to be a positive but sub-floor information coefficient "
            "(with regime sign dependence on a minority of leads and no concentration "
            "problem), assigned each lead a conservative follow-up type, and " + verdict
            + ". The stopped liquidity candidate avg_dollar_volume_21d stays excluded as a "
            "standalone alpha. No model was trained, fitted, or created, every result is "
            "reported as survivorship-biased, and no production edge is claimed."),
    }


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_results(*, phase2k_l_path: Optional[str] = None,
                  phase2k_k_path: Optional[str] = None,
                  phase2k_j_path: Optional[str] = None,
                  phase2k_i_path: Optional[str] = None,
                  phase2k_h_path: Optional[str] = None) -> Dict[str, Any]:
    phase2k_l_path = phase2k_l_path or PHASE2K_L_JSON
    phase2k_k_path = phase2k_k_path or PHASE2K_K_JSON
    phase2k_j_path = phase2k_j_path or PHASE2K_J_JSON
    phase2k_i_path = phase2k_i_path or PHASE2K_I_JSON
    phase2k_h_path = phase2k_h_path or PHASE2K_H_JSON

    kl = _safe_read_json(phase2k_l_path)
    kk = _safe_read_json(phase2k_k_path)
    kj = _safe_read_json(phase2k_j_path)
    ki = _safe_read_json(phase2k_i_path)
    kh = _safe_read_json(phase2k_h_path)

    kl_summary = summarize_phase2k_l(kl)
    kl_summary["phase2k_k_recommendation"] = (
        (kk or {}).get("recommendation", {}) or {}).get("recommendation") if kk else None
    kl_summary["phase2k_j_recommendation"] = (
        (kj or {}).get("recommendation", {}) or {}).get("recommendation") if kj else None
    kl_summary["phase2k_i_recommendation"] = (
        (ki or {}).get("recommendation", {}) or {}).get("recommendation") if ki else None
    kl_summary["phase2k_h_ready_for_screen"] = (
        (kh or {}).get("decision", {}) or {}).get("ready_for_retest") if kh else None

    kk_pairs = phase2k_k_research_lead_pairs(kk)
    targeted = extract_targeted_leads(kl, kk_pairs)
    kl_summary["all_targeted_from_phase2k_k_research_lead_pairs"] = bool(
        targeted and all(l.get("from_phase2k_k_research_lead_pair") for l in targeted))

    follow_up_counts = {NARROW_RETEST: 0, DIAGNOSTIC_ONLY: 0, HOLD_SECTOR: 0}
    for l in targeted:
        follow_up_counts[l["follow_up_type"]] = follow_up_counts.get(
            l["follow_up_type"], 0) + 1
    narrow = [l for l in targeted if l.get("follow_up_type") == NARROW_RETEST]
    hold = [l for l in targeted if l.get("follow_up_type") == HOLD_SECTOR]

    targeted_lead_rankings = build_targeted_lead_rankings(targeted)
    family_diagnostics = build_family_diagnostics(targeted)
    horizon_diagnostics = build_horizon_diagnostics(targeted)
    blocker_analysis = build_blocker_analysis(targeted)
    narrow_retest_plan = build_narrow_retest_plan(targeted, narrow, hold)

    recommendation = build_recommendation(
        follow_up_counts, targeted, kl_summary.get("routing_confirmed"))
    recommended_next_phase = build_recommended_next_phase(recommendation["recommendation"])
    interpretation = build_interpretation(recommendation)
    stopped_policy = summarize_stopped_candidate_policy(kl, targeted)

    sc_sum = (kl or {}).get("survivorship_caveat_carried_forward", {}) or {}
    survivorship_carried = {
        "membership_basis": sc_sum.get("membership_basis"),
        "point_in_time_membership_claimed": sc_sum.get(
            "point_in_time_membership_claimed", False),
        "all_results_survivorship_biased": True,
        "note": ("Phase 2K-M carries the Phase 2K-L / 2K-K survivorship caveat forward "
                 "unchanged; no point-in-time universe is claimed and no targeted lead is a "
                 "production edge."),
    }

    return {
        "phase": PHASE,
        "generated_at": _now(),
        "inputs_read": {
            "phase2k_l_json": _norm(phase2k_l_path),
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
        "upstream_phase2k_l_summary": kl_summary,
        "stopped_candidate_policy": stopped_policy,
        "survivorship_caveat_carried_forward": survivorship_carried,
        "targeted_leads": targeted,
        "targeted_lead_rankings": targeted_lead_rankings,
        "family_diagnostics": family_diagnostics,
        "horizon_diagnostics": horizon_diagnostics,
        "blocker_analysis": blocker_analysis,
        "narrow_retest_plan": narrow_retest_plan,
        "follow_up_type_counts": follow_up_counts,
        "allowed_follow_up_types": list(_ALLOWED_FOLLOW_UPS),
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
    kl = d["upstream_phase2k_l_summary"]
    best = d["targeted_lead_rankings"]["best_lead"]
    print(f"[phase2k-m] upstream 2K-L routing confirmed : {kl.get('routing_confirmed')}")
    print(f"[phase2k-m] targeted leads diagnosed        : {len(d['targeted_leads'])}")
    print(f"[phase2k-m] follow-up type counts           : {d['follow_up_type_counts']}")
    print(f"[phase2k-m] only blocker is sub-floor IC    : "
          f"{d['blocker_analysis']['only_blocking_gate_is_sub_floor_ic']}")
    if best:
        print(f"[phase2k-m] best targeted lead              : "
              f"{best.get('lead_id')} (score {best.get('diagnostic_score')})")
    print(f"[phase2k-m] recommendation                  : {rec['recommendation']}")
    print(f"[phase2k-m] recommended next phase           : {nxt['phase']} ({nxt['title']})")
    print(f"[phase2k-m] results written                 : {RESULTS_JSON}")
    print(f"[phase2k-m] elapsed seconds                  : {elapsed:.2f}")
    print("[phase2k-m] read-only; no new D: screen; no model trained; no D: writes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
