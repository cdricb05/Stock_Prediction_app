"""Phase 2K-O Decision Gate for Reconfirmed But Sub-Floor Leads (offline, read-only).

Phase 2K-N ran a narrow, pre-registered, model-free retest of the 3 Phase 2K-M
NARROW_RETEST_CANDIDATE pairs on the expanded, survivorship-caveated D: panel. All 3
reproduced a positive, above-zero, year-stable residual edge that is still below the
confirmation floor, so Phase 2K-N returned RESEARCH_LEADS_RECONFIRMED_BUT_NOT_CONFIRMABLE
and routed here. Phase 2K-O is a DECISION GATE, not another empirical screen: it reads the
small Phase 2K-N / 2K-M / 2K-K / 2K-J / 2K-H result summaries, diagnoses WHY the reconfirmed
leads are not confirmable, weighs the strategic paths, and chooses the next step.

It runs no empirical screen and trains, fits, and scores no model. It:

  1. Reads the Phase 2K-N result JSON and confirms it routed here (phase == "2K-N",
     retest_executed == true, overall recommendation
     RESEARCH_LEADS_RECONFIRMED_BUT_NOT_CONFIRMABLE -> 2K-O with the matching title, and the
     recommendation's create_model_candidate_now / train_model_now both false).
  2. Extracts the 3 reconfirmed leads and confirms every one is RESEARCH_LEAD_RECONFIRMED
     (none KEEP_FOR_CONFIRMATION_DESIGN, none DROP_AFTER_NARROW_RETEST, none
     NEED_POINT_IN_TIME_UNIVERSE), pulling each lead's residual rank IC, bootstrap CI,
     same-sign fraction, year-out stability, regime flags, concentration, failed keep gates,
     and prior Phase 2K-M / 2K-L numbers.
  3. Diagnoses the blockers: the primary blocker is sub-floor residual IC, and it records
     whether the bootstrap CI, year-out stability, regime dependence, concentration, or data
     sufficiency are also blockers, then classifies each lead (structurally weak but
     repeatable / sector-relative candidate / data-quality-or-PIT blocker / stop candidate).
  4. Weighs four strategic options -- SECTOR_RELATIVE_FEATURE_FEASIBILITY,
     POINT_IN_TIME_UNIVERSE_DECISION, STOP_AND_REFRESH_BACKLOG, and (always rejected unless a
     lead met KEEP_FOR_CONFIRMATION_DESIGN) CONTINUE_CONFIRMATION_ANYWAY -- each with pros,
     cons, requirements, risk, cost, and a next action.
  5. Applies conservative decision rules and selects one recommendation:
     PROCEED_TO_SECTOR_RELATIVE_FEASIBILITY / REQUIRE_POINT_IN_TIME_UNIVERSE_DECISION /
     STOP_RECONFIRMED_SUBFLOOR_LEADS / NO_ACTION_CONFIRMATION_BLOCKED, then routes to Phase
     2K-P with the title and purpose chosen by the decision.

This phase reads only the small Git-tracked Phase 2K-N / 2K-M / 2K-K / 2K-J / 2K-H JSON
summaries and writes exactly one small results JSON in the C: repo. It never reads the large
D: price-history CSV, never reads or writes the D: drive, performs no infrastructure action,
mutates no datastore, runs no broad alpha screen, and adds no candidate. The machine-readable
safety flags are emitted in the results JSON; the full guardrail rationale lives in
docs/phase2k_o_reconfirmed_lead_decision_gate_v1.md, not in this source.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from typing import Any, Dict, List, Optional

PHASE = "2K-O"

_OUTPUT_DIR = os.path.join("research", "output")

# Read-only upstream summaries (small, Git-tracked, in the C: repo). No D: drive paths.
PHASE2K_N_JSON = os.path.join(_OUTPUT_DIR, "phase2k_n_narrow_model_free_retest.json")
PHASE2K_M_JSON = os.path.join(_OUTPUT_DIR, "phase2k_m_targeted_lead_diagnostics.json")
PHASE2K_K_JSON = os.path.join(_OUTPUT_DIR, "phase2k_k_expanded_alpha_screen.json")
PHASE2K_J_JSON = os.path.join(_OUTPUT_DIR, "phase2k_j_alpha_backlog_refresh.json")
PHASE2K_H_JSON = os.path.join(_OUTPUT_DIR, "phase2k_h_manual_build_run_summary.json")

# The single small output this analyzer writes (stays in the C: repo, Git-tracked).
RESULTS_JSON = os.path.join(_OUTPUT_DIR, "phase2k_o_reconfirmed_lead_decision_gate.json")

# The 3 reconfirmed lead ids expected from Phase 2K-N (and ONLY these). The decision gate
# adds no candidate and reruns no retest.
PRE_REGISTERED_LEAD_IDS = [
    "residual_price_momentum_12_1@5d",
    "short_horizon_residual_reversal_5d@21d",
    "short_horizon_residual_reversal_21d@21d",
]

# Phase 2K-N's per-pair status vocabulary (mirrored here for confirmation only).
STATUS_KEEP = "KEEP_FOR_CONFIRMATION_DESIGN"
STATUS_RECONFIRMED = "RESEARCH_LEAD_RECONFIRMED"
STATUS_DROP = "DROP_AFTER_NARROW_RETEST"
STATUS_NEED_PIT = "NEED_POINT_IN_TIME_UNIVERSE"

# Phase 2K-N's expected overall recommendation that routes a reconfirmed-but-sub-floor
# outcome to this gate.
PHASE2K_N_RECONFIRMED = "RESEARCH_LEADS_RECONFIRMED_BUT_NOT_CONFIRMABLE"
PHASE2K_O_TITLE_FROM_PHASE2K_N = "Decision Gate for Reconfirmed But Sub-Floor Leads"

# The confirmation-design mean-residual-IC floor (inherited; used only to describe the
# sub-floor blocker -- this gate computes no new IC).
RANK_IC_FLOOR = 0.03
# A lead this weak (mean IC at/under this, or a CI that fails to clear zero) has no plausible
# cheap transformation left and is a stop / backlog candidate.
GIVE_UP_IC = 0.005

# Price / volume factor families whose leads admit a standard, cheap sector / industry
# neutralization (the sector-relative transformation considered here).
PRICE_VOLUME_CATEGORIES = {
    "price_momentum_alternatives",
    "short_term_reversal_mean_reversion",
    "price_volume_other",
}

# Strategic option ids.
OPT_SECTOR = "SECTOR_RELATIVE_FEATURE_FEASIBILITY"
OPT_PIT = "POINT_IN_TIME_UNIVERSE_DECISION"
OPT_STOP = "STOP_AND_REFRESH_BACKLOG"
OPT_CONTINUE = "CONTINUE_CONFIRMATION_ANYWAY"

# Recommendation vocabulary (the ONLY values this gate may emit).
REC_SECTOR = "PROCEED_TO_SECTOR_RELATIVE_FEASIBILITY"
REC_PIT = "REQUIRE_POINT_IN_TIME_UNIVERSE_DECISION"
REC_STOP = "STOP_RECONFIRMED_SUBFLOOR_LEADS"
REC_NOACTION = "NO_ACTION_CONFIRMATION_BLOCKED"
_ALLOWED_RECOMMENDATIONS = [REC_SECTOR, REC_PIT, REC_STOP, REC_NOACTION]

# Next-phase (always Phase 2K-P) titles + purposes, chosen by the recommendation.
NEXT_PHASE = "2K-P"
TITLE_SECTOR = "Sector-Relative Feature Feasibility for Reconfirmed Leads"
TITLE_PIT = "Point-in-Time Data Decision for Reconfirmed Leads"
TITLE_STOP = "Alpha Backlog Refresh v3 After Reconfirmed Sub-Floor Leads"
TITLE_NOACTION = "Repair or Rerun Narrow Retest Before Decision"


# --------------------------------------------------------------------------- #
# Small pure helpers
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


def _is_pos(x: Optional[float]) -> bool:
    return bool(x is not None and x > 0)


# --------------------------------------------------------------------------- #
# Upstream Phase 2K-N routing confirmation + chain context
# --------------------------------------------------------------------------- #
def summarize_phase2k_n(kn: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Read-only digest of the Phase 2K-N result plus the routing confirmation."""
    if kn is None:
        return {"present": False, "routing_confirmed": False}
    rec = kn.get("overall_recommendation", {}) or {}
    nxt = kn.get("recommended_next_phase", {}) or {}
    counts = (kn.get("gate_summary", {}) or {}).get("pair_recommendation_counts", {}) or {}
    n_keep = int(counts.get(STATUS_KEEP, 0) or 0)
    n_reconf = int(counts.get(STATUS_RECONFIRMED, 0) or 0)
    n_drop = int(counts.get(STATUS_DROP, 0) or 0)
    n_need = int(counts.get(STATUS_NEED_PIT, 0) or 0)

    phase_ok = kn.get("phase") == "2K-N"
    retest_ok = kn.get("retest_executed") is True
    rec_ok = rec.get("recommendation") == PHASE2K_N_RECONFIRMED
    routes_ok = nxt.get("phase") == "2K-O"
    title_ok = nxt.get("title") == PHASE2K_O_TITLE_FROM_PHASE2K_N
    create_ok = rec.get("create_model_candidate_now") is False
    train_ok = rec.get("train_model_now") is False
    all_three_reconfirmed = bool(n_reconf == 3 and n_keep == 0 and n_drop == 0 and n_need == 0)

    sc = kn.get("survivorship_caveat_summary", {}) or {}
    return {
        "present": True,
        "phase": kn.get("phase"),
        "retest_executed": kn.get("retest_executed"),
        "overall_recommendation": rec.get("recommendation"),
        "pair_recommendation_counts": {
            STATUS_KEEP: n_keep,
            STATUS_RECONFIRMED: n_reconf,
            STATUS_DROP: n_drop,
            STATUS_NEED_PIT: n_need,
        },
        "recommended_next_phase": nxt,
        "phase_is_2k_n": phase_ok,
        "retest_executed_true": retest_ok,
        "recommendation_is_reconfirmed_not_confirmable": rec_ok,
        "recommended_next_phase_is_2k_o": routes_ok,
        "recommended_next_phase_title_matches": title_ok,
        "create_model_candidate_now_false": create_ok,
        "train_model_now_false": train_ok,
        "all_three_leads_reconfirmed": all_three_reconfirmed,
        "results_are_survivorship_biased": bool(
            sc.get("all_results_must_be_reported_survivorship_biased")
            or kn.get("interpretation", {}).get("results_are_survivorship_biased")),
        "survivorship_membership_basis": sc.get("membership_basis"),
        "point_in_time_membership_claimed": sc.get("point_in_time_membership_claimed"),
        "routing_confirmed": bool(
            phase_ok and retest_ok and rec_ok and routes_ok and title_ok
            and create_ok and train_ok and all_three_reconfirmed),
    }


def summarize_upstream_chain(km: Optional[Dict[str, Any]],
                             kk: Optional[Dict[str, Any]],
                             kj: Optional[Dict[str, Any]],
                             kh: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Light read-only context: the prior phases' recommendations and build readiness."""
    def _rec(d: Optional[Dict[str, Any]]) -> Optional[str]:
        return ((d or {}).get("recommendation", {}) or {}).get("recommendation") if d else None

    kh_dec = (kh or {}).get("decision", {}) or {}
    return {
        "phase2k_m_recommendation": _rec(km),
        "phase2k_k_recommendation": _rec(kk),
        "phase2k_j_recommendation": _rec(kj),
        "phase2k_h_ready_for_retest": bool(kh_dec.get("ready_for_retest")),
        "phase2k_h_point_in_time_membership_claimed":
            kh_dec.get("point_in_time_membership_claimed"),
    }


# --------------------------------------------------------------------------- #
# Reconfirmed-lead extraction
# --------------------------------------------------------------------------- #
def extract_reconfirmed_leads(kn: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Join Phase 2K-N pair_recommendations with narrow_retest_results per lead_id."""
    if kn is None:
        return []
    results_by_id = {c.get("lead_id"): c for c in kn.get("narrow_retest_results", []) or []}
    out: List[Dict[str, Any]] = []
    for pr in kn.get("pair_recommendations", []) or []:
        lid = pr.get("lead_id")
        cell = results_by_id.get(lid, {}) or {}
        regime = cell.get("regime_summary", {}) or {}
        conc = cell.get("concentration", {}) or {}
        prior = cell.get("prior_phase2k_m", {}) or {}
        out.append({
            "lead_id": lid,
            "candidate": pr.get("candidate"),
            "feature": pr.get("feature"),
            "category": pr.get("category"),
            "horizon_days": pr.get("horizon_days"),
            "status": pr.get("status"),
            "mean_residual_rank_ic": pr.get("mean_residual_rank_ic"),
            "bootstrap_ci_low": pr.get("bootstrap_ci_low"),
            "bootstrap_ci_high": pr.get("bootstrap_ci_high"),
            "frac_dates_same_sign_as_reference": pr.get("frac_dates_same_sign_as_reference"),
            "information_ratio": cell.get("information_ratio"),
            "leave_one_year_out_sign_stable": pr.get("leave_one_year_out_sign_stable"),
            "spy_sign_flips_across_regime": regime.get("spy_sign_flips_across_regime"),
            "vol_sign_flips_across_regime": regime.get("vol_sign_flips_across_regime"),
            "top3_long_leg_share": conc.get("top3_long_leg_share"),
            "top_quintile_ticker_concentration": conc.get(
                "top_quintile_ticker_concentration"),
            "enough_data": pr.get("enough_data"),
            "failed_keep_gates": pr.get("failed_keep_gates", []) or [],
            "prior_phase2k_m_mean_ic": prior.get(
                "prior_mean_residual_rank_ic", pr.get("prior_phase2k_m_mean_ic")),
            "prior_phase2k_l_diagnostic_score": pr.get("prior_phase2k_l_diagnostic_score"),
            "reason": pr.get("reason"),
        })
    return out


# --------------------------------------------------------------------------- #
# Blocker diagnosis
# --------------------------------------------------------------------------- #
def _classify_lead(lead: Dict[str, Any]) -> Dict[str, Any]:
    """Per-lead blocker decomposition + classification."""
    ic = lead.get("mean_residual_rank_ic")
    ci_low = lead.get("bootstrap_ci_low")
    loyo = bool(lead.get("leave_one_year_out_sign_stable"))
    regime_dependent = bool(lead.get("spy_sign_flips_across_regime")
                            or lead.get("vol_sign_flips_across_regime"))
    top3 = lead.get("top3_long_leg_share")
    enough = bool(lead.get("enough_data"))

    ic_positive = _is_pos(ic)
    ic_above_floor = bool(ic is not None and ic >= RANK_IC_FLOOR)
    ci_above_zero = _is_pos(ci_low)
    concentration_ok = bool(top3 is None or top3 <= 0.50)

    sub_floor_blocker = bool(ic_positive and not ic_above_floor)
    ci_blocker = not ci_above_zero
    stability_blocker = not loyo
    regime_blocker = regime_dependent
    concentration_blocker = not concentration_ok
    data_blocker = not enough

    reproducible_above_zero = bool(
        ic_positive and ci_above_zero and loyo and not regime_dependent and concentration_ok
        and enough)
    too_weak = bool((ic is not None and ic <= GIVE_UP_IC) or ci_blocker)

    if reproducible_above_zero and sub_floor_blocker:
        classification = "structurally_weak_but_repeatable_sector_relative_candidate"
    elif data_blocker:
        classification = "data_quality_or_point_in_time_blocker"
    elif too_weak or stability_blocker or regime_blocker or concentration_blocker:
        classification = "stop_or_backlog_candidate"
    else:
        classification = "structurally_weak_but_repeatable_sector_relative_candidate"

    return {
        "lead_id": lead.get("lead_id"),
        "candidate": lead.get("candidate"),
        "category": lead.get("category"),
        "horizon_days": lead.get("horizon_days"),
        "mean_residual_rank_ic": ic,
        "primary_blocker": "sub_floor_residual_ic" if sub_floor_blocker else (
            "non_positive_or_unrepeatable_edge"),
        "blockers": {
            "sub_floor_residual_ic": sub_floor_blocker,
            "bootstrap_ci_not_above_zero": ci_blocker,
            "year_out_sign_unstable": stability_blocker,
            "regime_sign_dependence": regime_blocker,
            "excessive_concentration": concentration_blocker,
            "insufficient_data": data_blocker,
        },
        "reproducible_above_zero": reproducible_above_zero,
        "in_price_volume_family": lead.get("category") in PRICE_VOLUME_CATEGORIES,
        "classification": classification,
    }


def diagnose_blockers(leads: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate blocker diagnosis across the reconfirmed leads."""
    per_lead = [_classify_lead(l) for l in leads]
    n = len(per_lead)
    n_repro = sum(1 for c in per_lead if c["reproducible_above_zero"])
    n_sub_floor = sum(1 for c in per_lead if c["blockers"]["sub_floor_residual_ic"])
    n_price_family = sum(1 for c in per_lead if c["in_price_volume_family"])

    regime_dominant = any(c["blockers"]["regime_sign_dependence"] for c in per_lead)
    concentration_dominant = any(
        c["blockers"]["excessive_concentration"] for c in per_lead)
    stability_dominant = any(c["blockers"]["year_out_sign_unstable"] for c in per_lead)
    data_dominant = any(c["blockers"]["insufficient_data"] for c in per_lead)
    ci_dominant = any(c["blockers"]["bootstrap_ci_not_above_zero"] for c in per_lead)

    # The leads reproduce a positive, above-zero, year-stable, regime-stable, non-concentrated
    # edge; the survivorship / current-membership caveat is a standing caveat, NOT the
    # dominant blocker, unless the edge fails to reproduce above zero or year-out stability.
    survivorship_dominant = bool(n > 0 and (ci_dominant or stability_dominant)
                                 and n_repro < n)
    sector_plausible = bool(n > 0 and n_repro == n and n_price_family == n)

    primary = "sub_floor_residual_ic" if (n_sub_floor == n and n > 0) else (
        "mixed_or_non_reproducible")
    return {
        "n_leads": n,
        "n_leads_reproducible_above_zero": n_repro,
        "n_leads_sub_floor_residual_ic": n_sub_floor,
        "n_leads_in_price_volume_family": n_price_family,
        "primary_blocker_across_leads": primary,
        "regime_is_dominant_blocker": regime_dominant,
        "concentration_is_dominant_blocker": concentration_dominant,
        "stability_is_dominant_blocker": stability_dominant,
        "data_sufficiency_is_dominant_blocker": data_dominant,
        "survivorship_is_dominant_blocker": survivorship_dominant,
        "all_leads_reproducible_above_zero": bool(n > 0 and n_repro == n),
        "all_leads_in_price_volume_family": bool(n > 0 and n_price_family == n),
        "sector_relative_variant_plausible": sector_plausible,
        "per_lead": per_lead,
    }


# --------------------------------------------------------------------------- #
# Strategic options (each evaluated with pros / cons / requirements / risk / cost)
# --------------------------------------------------------------------------- #
def build_strategic_options(diag: Dict[str, Any], any_keep: bool) -> List[Dict[str, Any]]:
    return [
        {
            "option": OPT_SECTOR,
            "purpose": (
                "Check whether sector / industry-neutral or sector-relative versions could "
                "lift the reconfirmed but sub-floor price / reversal leads over the "
                "confirmation floor."),
            "pros": [
                "directly targets the single dominant blocker (sub-floor residual IC) without "
                "abandoning a reproducible, above-zero, year-stable edge",
                "still model-free and offline; cheaper and faster than buying point-in-time "
                "data",
                "all reconfirmed leads are price / reversal factors that admit a standard "
                "sector / industry neutralization",
            ],
            "cons": [
                "no point-in-time sector / industry map exists, so any sector mapping is "
                "current-as-of and usable only as caveated research metadata",
                "may not lift the IC over the floor; could itself end in another reconfirmed "
                "but sub-floor result",
            ],
            "requirements": [
                "a sector / industry mapping by ticker (point-in-time if available; otherwise "
                "the current map used only as explicitly caveated research metadata)",
                "no new candidate families and no model training; a feasibility assessment "
                "only",
            ],
            "risk": "low (research-only, model-free, no data purchase committed)",
            "cost": "low (reuses the existing offline panel and tooling)",
            "next_action": (
                "Phase 2K-P: assess sector-relative feasibility for the reconfirmed leads "
                "before any confirmation or model work."),
        },
        {
            "option": OPT_PIT,
            "purpose": (
                "Decide whether to obtain proper point-in-time membership / sector / "
                "fundamentals data before continuing alpha research."),
            "pros": [
                "removes the standing survivorship / current-membership caveat and would let "
                "sector / fundamental variants be tested responsibly",
            ],
            "cons": [
                "highest cost and implementation burden; a vendor / data commitment",
                "the dominant blocker here is sub-floor IC, not survivorship; clean "
                "point-in-time data would tighten, not rescue, a sub-floor edge",
            ],
            "requirements": [
                "a vendor / data decision, cost estimate, implementation burden, and expected "
                "quality gain",
            ],
            "risk": "medium (cost + effort committed before any confirmable edge exists)",
            "cost": "high (paid point-in-time vendor data and ingestion work)",
            "next_action": (
                "Phase 2K-P: a point-in-time data decision (defer paid data until a "
                "cheaper sector-relative variant is shown to be infeasible or also sub-floor)."),
        },
        {
            "option": OPT_STOP,
            "purpose": (
                "Stop the reconfirmed but sub-floor leads and redirect to new, orthogonal "
                "alpha families."),
            "pros": [
                "avoids sinking more effort into weak price / reversal leads",
            ],
            "cons": [
                "discards a reproducible, above-zero, year-stable edge before a cheap, "
                "plausible transformation (sector neutralization) has been tried",
            ],
            "requirements": [
                "an updated backlog and no further retest of these standalone leads",
            ],
            "risk": "low operationally, but forfeits a plausible cheap improvement path",
            "cost": "low",
            "next_action": (
                "Phase 2K-P: refresh the alpha backlog toward stronger non-price-volume "
                "factors."),
        },
        {
            "option": OPT_CONTINUE,
            "purpose": (
                "Proceed directly to confirmation design for the reconfirmed leads."),
            "pros": [],
            "cons": [
                "no lead met KEEP_FOR_CONFIRMATION_DESIGN in Phase 2K-N (0 KEEP, 3 "
                "RESEARCH_LEAD_RECONFIRMED); every lead is sub-floor",
                "would treat a sub-floor research lead as a confirmable edge and is not "
                "permitted",
            ],
            "requirements": [
                "at least one lead at KEEP_FOR_CONFIRMATION_DESIGN, which Phase 2K-N did not "
                "produce",
            ],
            "risk": "rejected by rule",
            "cost": "n/a",
            "next_action": "none; explicitly rejected",
            "rejected": True,
            "rejected_reason": (
                "Phase 2K-N produced 0 KEEP_FOR_CONFIRMATION_DESIGN leads; confirmation design "
                "is not authorized for a reconfirmed but sub-floor lead."),
            "eligible": bool(any_keep),
        },
    ]


# --------------------------------------------------------------------------- #
# Decision rules + selection
# --------------------------------------------------------------------------- #
def select_recommendation(kn_summary: Dict[str, Any],
                          diag: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the conservative decision rules and pick exactly one recommendation."""
    routing_ok = bool(kn_summary.get("routing_confirmed"))
    n = diag.get("n_leads", 0)
    all_repro = bool(diag.get("all_leads_reproducible_above_zero"))
    primary_sub_floor = diag.get("primary_blocker_across_leads") == "sub_floor_residual_ic"
    robustness_clean = not (diag.get("regime_is_dominant_blocker")
                            or diag.get("concentration_is_dominant_blocker")
                            or diag.get("stability_is_dominant_blocker")
                            or diag.get("data_sufficiency_is_dominant_blocker"))
    sector_plausible = bool(diag.get("sector_relative_variant_plausible"))
    pit_dominant = bool(diag.get("survivorship_is_dominant_blocker"))

    if not routing_ok or n == 0:
        rec = REC_NOACTION
    elif pit_dominant and not sector_plausible:
        rec = REC_PIT
    elif all_repro and primary_sub_floor and robustness_clean and sector_plausible:
        rec = REC_SECTOR
    elif not sector_plausible and not all_repro:
        rec = REC_STOP
    elif sector_plausible and primary_sub_floor:
        rec = REC_SECTOR
    else:
        rec = REC_NOACTION
    return {
        "recommendation": rec,
        "decision_inputs": {
            "routing_confirmed": routing_ok,
            "all_leads_reproducible_above_zero": all_repro,
            "primary_blocker_is_sub_floor_ic": primary_sub_floor,
            "robustness_gates_clean": robustness_clean,
            "sector_relative_variant_plausible": sector_plausible,
            "survivorship_is_dominant_blocker": pit_dominant,
        },
    }


def build_decision_matrix(selected: str, diag: Dict[str, Any],
                          any_keep: bool) -> List[Dict[str, Any]]:
    di = {
        "all_leads_reproducible_above_zero": bool(
            diag.get("all_leads_reproducible_above_zero")),
        "primary_blocker_is_sub_floor_ic":
            diag.get("primary_blocker_across_leads") == "sub_floor_residual_ic",
        "robustness_gates_clean": not (
            diag.get("regime_is_dominant_blocker")
            or diag.get("concentration_is_dominant_blocker")
            or diag.get("stability_is_dominant_blocker")
            or diag.get("data_sufficiency_is_dominant_blocker")),
        "sector_relative_variant_plausible": bool(
            diag.get("sector_relative_variant_plausible")),
        "survivorship_is_dominant_blocker": bool(
            diag.get("survivorship_is_dominant_blocker")),
    }
    return [
        {
            "option": OPT_SECTOR,
            "maps_to_recommendation": REC_SECTOR,
            "selected": selected == REC_SECTOR,
            "conditions": {
                "leads_reproducible_and_above_zero": di["all_leads_reproducible_above_zero"],
                "primary_blocker_is_sub_floor_ic": di["primary_blocker_is_sub_floor_ic"],
                "regime_concentration_stability_not_dominant": di["robustness_gates_clean"],
                "sector_relative_variant_plausible_and_cheaper_than_pit_data":
                    di["sector_relative_variant_plausible"],
            },
            "all_conditions_met": all([
                di["all_leads_reproducible_above_zero"],
                di["primary_blocker_is_sub_floor_ic"],
                di["robustness_gates_clean"],
                di["sector_relative_variant_plausible"],
            ]),
        },
        {
            "option": OPT_PIT,
            "maps_to_recommendation": REC_PIT,
            "selected": selected == REC_PIT,
            "conditions": {
                "survivorship_is_dominant_blocker": di["survivorship_is_dominant_blocker"],
                "sector_or_fundamental_data_untestable_without_pit":
                    not di["sector_relative_variant_plausible"],
            },
            "all_conditions_met": bool(
                di["survivorship_is_dominant_blocker"]
                and not di["sector_relative_variant_plausible"]),
        },
        {
            "option": OPT_STOP,
            "maps_to_recommendation": REC_STOP,
            "selected": selected == REC_STOP,
            "conditions": {
                "leads_too_weak_or_unrepeatable":
                    not di["all_leads_reproducible_above_zero"],
                "no_plausible_transformation_justified":
                    not di["sector_relative_variant_plausible"],
            },
            "all_conditions_met": bool(
                not di["all_leads_reproducible_above_zero"]
                and not di["sector_relative_variant_plausible"]),
        },
        {
            "option": OPT_CONTINUE,
            "maps_to_recommendation": None,
            "selected": False,
            "rejected": True,
            "conditions": {
                "at_least_one_lead_kept_for_confirmation_design": bool(any_keep),
            },
            "all_conditions_met": bool(any_keep),
        },
    ]


def build_rejected_paths(selected: str, diag: Dict[str, Any]) -> List[Dict[str, Any]]:
    rejected: List[Dict[str, Any]] = []
    if selected != REC_PIT:
        rejected.append({
            "option": OPT_PIT,
            "recommendation": REC_PIT,
            "reason": (
                "The survivorship / current-membership caveat is a standing caveat, not the "
                "dominant blocker: the leads reproduced a positive, above-zero, year-stable "
                "edge, so the dominant blocker is sub-floor residual IC. A cheaper "
                "sector-relative feasibility check should be tried before committing to paid "
                "point-in-time data; revisit point-in-time data only if the sector-relative "
                "variant is infeasible or also sub-floor."),
        })
    if selected != REC_STOP:
        rejected.append({
            "option": OPT_STOP,
            "recommendation": REC_STOP,
            "reason": (
                "Stopping is premature: the reconfirmed leads reproduced a positive, "
                "above-zero, year-stable edge and a cheap, plausible transformation "
                "(sector / industry neutralization) has not yet been tried. Stop only if the "
                "sector-relative feasibility check also fails."),
        })
    rejected.append({
        "option": OPT_CONTINUE,
        "recommendation": None,
        "reason": (
            "Explicitly rejected: no lead met KEEP_FOR_CONFIRMATION_DESIGN in Phase 2K-N "
            "(0 KEEP, 3 RESEARCH_LEAD_RECONFIRMED). Confirmation design is not authorized for "
            "a reconfirmed but sub-floor lead, and no model candidate may be created."),
    })
    return rejected


def build_implementation_constraints() -> List[str]:
    return [
        "This is a decision gate only: no empirical screen is run and no model is trained, "
        "fitted, scored, or created.",
        "No candidate is added and no Phase 2K-N retest is rerun; the large D: price-history "
        "CSV is not read.",
        "If Phase 2K-P assesses a sector-relative variant, any sector / industry map without "
        "point-in-time membership may be used only as explicitly caveated research metadata.",
        "All results remain survivorship-biased / current-as-of; a clean point-in-time "
        "universe would tighten, never rescue, a sub-floor edge.",
        "No deployment, no model-v2 enablement, no migration, no database write, no order, "
        "and no automation; nothing here is a production edge.",
    ]


# --------------------------------------------------------------------------- #
# Recommendation block + routing + interpretation
# --------------------------------------------------------------------------- #
def build_recommendation(selected: str, diag: Dict[str, Any]) -> Dict[str, Any]:
    n_reconf = diag.get("n_leads", 0)
    if selected == REC_SECTOR:
        reason = (
            f"All {n_reconf} reconfirmed leads reproduced a positive, above-zero, year-stable, "
            "regime-stable, and non-concentrated residual edge whose single dominant blocker "
            "is sub-floor residual IC. They are price / reversal factors that admit a "
            "standard sector / industry neutralization, which is cheaper and faster than "
            "committing to paid point-in-time data. Proceed to a model-free sector-relative "
            "feasibility assessment; no model candidate is created and no confirmation is "
            "authorized yet.")
    elif selected == REC_PIT:
        reason = (
            "The survivorship / current-membership caveat is the dominant blocker and the "
            "sector / fundamental variants cannot be tested responsibly without point-in-time "
            "data; a point-in-time data decision is required first. No model candidate is "
            "created.")
    elif selected == REC_STOP:
        reason = (
            "The reconfirmed leads are too weak or unrepeatable and no plausible cheap "
            "transformation is justified; stop these standalone leads and refresh the alpha "
            "backlog. No model candidate is created.")
    else:
        reason = (
            "Inputs are missing or Phase 2K-N did not run successfully (routing not "
            "confirmed); the strategic decision is blocked until the narrow-retest evidence is "
            "repaired or rerun. No model candidate is created.")
    return {
        "recommendation": selected,
        "allowed_values": list(_ALLOWED_RECOMMENDATIONS),
        "create_model_candidate_now": False,
        "train_model_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "results_are_survivorship_biased": True,
        "reason": reason,
    }


def build_recommended_next_phase(selected: str) -> Dict[str, str]:
    if selected == REC_SECTOR:
        return {
            "phase": NEXT_PHASE,
            "title": TITLE_SECTOR,
            "purpose": (
                "Determine whether sector / industry metadata can support sector-relative "
                "versions of the reconfirmed price / reversal leads, still with no model "
                "training and no model-candidate design. Results remain survivorship-biased "
                "and not a production edge."),
        }
    if selected == REC_PIT:
        return {
            "phase": NEXT_PHASE,
            "title": TITLE_PIT,
            "purpose": (
                "Decide whether to obtain point-in-time universe / sector / fundamental data "
                "before further alpha research, with a cost / burden / expected-quality "
                "assessment. Still no model training and no model candidate."),
        }
    if selected == REC_STOP:
        return {
            "phase": NEXT_PHASE,
            "title": TITLE_STOP,
            "purpose": (
                "Stop the reconfirmed sub-floor leads and refresh the backlog toward stronger "
                "non-price-volume factors. Still no model training and no model candidate."),
        }
    return {
        "phase": NEXT_PHASE,
        "title": TITLE_NOACTION,
        "purpose": (
            "Fix missing or incomplete Phase 2K-N evidence before making a strategic "
            "decision. Still no model training and no model candidate."),
    }


def build_interpretation(selected: str, diag: Dict[str, Any]) -> Dict[str, Any]:
    if selected == REC_SECTOR:
        verdict = ("routes to a model-free sector-relative feasibility assessment for the "
                   "reconfirmed but sub-floor leads")
    elif selected == REC_PIT:
        verdict = ("routes to a point-in-time data decision because the survivorship caveat "
                   "is the dominant blocker")
    elif selected == REC_STOP:
        verdict = ("stops the reconfirmed sub-floor leads and refreshes the alpha backlog")
    else:
        verdict = ("blocks the strategic decision until the narrow-retest evidence is "
                   "repaired or rerun")
    return {
        "model_trained": False,
        "model_fitted": False,
        "model_candidate_created": False,
        "authorized_to_train_model": False,
        "authorized_to_serve_model": False,
        "ran_broad_alpha_screen": False,
        "candidate_set_expanded": False,
        "reran_narrow_retest": False,
        "read_large_d_drive_csv": False,
        "production_edge_claimed": False,
        "results_are_survivorship_biased": True,
        "narrative": (
            "Phase 2K-O is a decision gate after the Phase 2K-N narrow model-free retest. All "
            "3 narrow-retest leads (residual_price_momentum_12_1 @ 5d, "
            "short_horizon_residual_reversal_5d @ 21d, short_horizon_residual_reversal_21d @ "
            "21d) were reconfirmed as research leads -- positive, above-zero by bootstrap, "
            "year-stable, regime-stable, and not over-concentrated -- but every one is below "
            "the confirmation floor, so the single dominant blocker is sub-floor residual IC, "
            "not regime dependence, concentration, instability, or the survivorship caveat. "
            "The gate " + verdict + ". It ran no empirical screen, read no large D: CSV, added "
            "no candidate, trained / fitted / scored no model, and created no model "
            "candidate; even the chosen path stays model-free and is not a production edge, "
            "and all results remain survivorship-biased / current-membership caveated."),
    }


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_results(*, phase2k_n_path: Optional[str] = None,
                  phase2k_m_path: Optional[str] = None,
                  phase2k_k_path: Optional[str] = None,
                  phase2k_j_path: Optional[str] = None,
                  phase2k_h_path: Optional[str] = None) -> Dict[str, Any]:
    phase2k_n_path = phase2k_n_path or PHASE2K_N_JSON
    phase2k_m_path = phase2k_m_path or PHASE2K_M_JSON
    phase2k_k_path = phase2k_k_path or PHASE2K_K_JSON
    phase2k_j_path = phase2k_j_path or PHASE2K_J_JSON
    phase2k_h_path = phase2k_h_path or PHASE2K_H_JSON

    kn = _safe_read_json(phase2k_n_path)
    km = _safe_read_json(phase2k_m_path)
    kk = _safe_read_json(phase2k_k_path)
    kj = _safe_read_json(phase2k_j_path)
    kh = _safe_read_json(phase2k_h_path)

    kn_summary = summarize_phase2k_n(kn)
    kn_summary["upstream_chain"] = summarize_upstream_chain(km, kk, kj, kh)

    leads = extract_reconfirmed_leads(kn)
    diag = diagnose_blockers(leads)

    any_keep = bool(kn_summary.get("pair_recommendation_counts", {}).get(STATUS_KEEP, 0))
    options = build_strategic_options(diag, any_keep)

    decision = select_recommendation(kn_summary, diag)
    selected = decision["recommendation"]
    decision_matrix = build_decision_matrix(selected, diag, any_keep)
    recommendation = build_recommendation(selected, diag)
    recommended_next_phase = build_recommended_next_phase(selected)
    interpretation = build_interpretation(selected, diag)

    selected_option = {
        REC_SECTOR: OPT_SECTOR,
        REC_PIT: OPT_PIT,
        REC_STOP: OPT_STOP,
        REC_NOACTION: None,
    }[selected]

    return {
        "phase": PHASE,
        "generated_at": _now(),
        "inputs_read": {
            "phase2k_n_json": _norm(phase2k_n_path),
            "phase2k_m_json": _norm(phase2k_m_path),
            "phase2k_k_json": _norm(phase2k_k_path),
            "phase2k_j_json": _norm(phase2k_j_path),
            "phase2k_h_json": _norm(phase2k_h_path),
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
        "model_trained": False,
        "model_candidate_created": False,
        "d_drive_read": False,
        "d_drive_written": False,
        "broad_alpha_screen_run": False,
        "upstream_phase2k_n_summary": kn_summary,
        "reconfirmed_leads": leads,
        "blocker_diagnosis": diag,
        "strategic_options": options,
        "decision_matrix": decision_matrix,
        "selected_path": {
            "recommendation": selected,
            "option": selected_option,
            "decision_inputs": decision["decision_inputs"],
        },
        "rejected_paths": build_rejected_paths(selected, diag),
        "implementation_constraints": build_implementation_constraints(),
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
    kn = d["upstream_phase2k_n_summary"]
    print(f"[phase2k-o] upstream 2K-N routing confirmed : {kn.get('routing_confirmed')}")
    print(f"[phase2k-o] reconfirmed leads               : "
          f"{[l['lead_id'] for l in d['reconfirmed_leads']]}")
    print(f"[phase2k-o] primary blocker                 : "
          f"{d['blocker_diagnosis'].get('primary_blocker_across_leads')}")
    print(f"[phase2k-o] selected path                   : {d['selected_path']['option']}")
    print(f"[phase2k-o] recommendation                  : {rec['recommendation']}")
    print(f"[phase2k-o] recommended next phase           : {nxt['phase']} ({nxt['title']})")
    print(f"[phase2k-o] results written                 : {RESULTS_JSON}")
    print(f"[phase2k-o] elapsed seconds                  : {elapsed:.2f}")
    print("[phase2k-o] read-only decision gate; no empirical screen; no D: CSV read; "
          "no model trained; no D: writes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
