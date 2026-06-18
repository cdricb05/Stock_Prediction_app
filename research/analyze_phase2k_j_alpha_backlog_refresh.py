"""Phase 2K-J alpha backlog refresh after the expanded retest failure (offline, read-only).

Phase 2K-I retested the lone Phase 2K-C survivor, ``avg_dollar_volume_21d``, on the expanded,
survivorship-caveated free panel produced by the manual Phase 2K-H build and returned
``EXPANDED_RETEST_FAIL``: the residual signal flipped sign and turned negative on the larger
panel. Phase 2K-J is the disciplined response. It is a small, read-only PLANNING / DIAGNOSTIC
artifact: it reads the small JSON summaries from prior phases (never the large D: CSVs), stops
the failed liquidity candidate, extracts the diagnostic lessons from the expanded retest,
refreshes the alpha backlog, and recommends the next model-free research phase. It writes one
small Git-tracked JSON in the C: repo.

It computes no new alpha signals, reads no D: drive dataset, makes no network call, trains and
fits nothing, creates no model candidate, and claims no production edge. The full guardrail
rationale lives in docs/phase2k_j_alpha_backlog_refresh_v1.md, not in this source.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from typing import Any, Dict, List, Optional

PHASE = "2K-J"

_OUTPUT_DIR = os.path.join("research", "output")

# Read-only input summaries (all small JSON in the C: repo; never the large D: CSVs).
PHASE2K_I_JSON = os.path.join(_OUTPUT_DIR, "phase2k_i_expanded_residual_retest.json")
PHASE2K_H_JSON = os.path.join(_OUTPUT_DIR, "phase2k_h_manual_build_run_summary.json")
PHASE2K_BACKLOG_JSON = os.path.join(_OUTPUT_DIR, "phase2k_alpha_backlog.json")
PHASE2K_C_JSON = os.path.join(_OUTPUT_DIR, "phase2k_c_residual_robustness.json")
PHASE2L_B_JSON = os.path.join(_OUTPUT_DIR, "phase2l_b_walk_forward_validation.json")
PHASE2K_D_JSON = os.path.join(_OUTPUT_DIR, "phase2k_d_data_expansion_plan.json")
PHASE2K_E_JSON = os.path.join(_OUTPUT_DIR, "phase2k_e_data_feasibility.json")
PHASE2K_F_JSON = os.path.join(_OUTPUT_DIR, "phase2k_f_free_data_build_plan.json")

# The single small output this analyzer writes (stays in the C: repo, Git-tracked).
RESULTS_JSON = os.path.join(_OUTPUT_DIR, "phase2k_j_alpha_backlog_refresh.json")

# The candidate that failed the expanded retest and is stopped as a standalone alpha.
STOPPED_CANDIDATE = "avg_dollar_volume_21d"


# --------------------------------------------------------------------------- #
# Read-only helpers
# --------------------------------------------------------------------------- #
def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _now() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def _safe_read_json(path: str) -> Optional[Dict[str, Any]]:
    """Read a JSON file if it exists and parses; otherwise return None. Never raises."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return None


def _resolve_paths(inputs_dir: str) -> Dict[str, str]:
    """Resolve the eight read-only input summary paths under <inputs_dir>."""
    return {
        "phase2k_i_json": os.path.join(inputs_dir, "phase2k_i_expanded_residual_retest.json"),
        "phase2k_h_summary_json": os.path.join(
            inputs_dir, "phase2k_h_manual_build_run_summary.json"),
        "phase2k_alpha_backlog_json": os.path.join(inputs_dir, "phase2k_alpha_backlog.json"),
        "phase2k_c_json": os.path.join(inputs_dir, "phase2k_c_residual_robustness.json"),
        "phase2l_b_json": os.path.join(inputs_dir, "phase2l_b_walk_forward_validation.json"),
        "phase2k_d_json": os.path.join(inputs_dir, "phase2k_d_data_expansion_plan.json"),
        "phase2k_e_json": os.path.join(inputs_dir, "phase2k_e_data_feasibility.json"),
        "phase2k_f_json": os.path.join(inputs_dir, "phase2k_f_free_data_build_plan.json"),
    }


# --------------------------------------------------------------------------- #
# Phase 2K-I extraction + routing confirmation
# --------------------------------------------------------------------------- #
def summarize_phase2k_i(ki: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract the upstream Phase 2K-I facts this phase reasons about (read-only)."""
    if ki is None:
        return {"present": False}
    rec = ki.get("recommendation", {}) or {}
    cand = ki.get("candidate", {}) or {}
    wf = ki.get("walk_forward_metrics", {}) or {}
    retest = (ki.get("retest_metrics", {}) or {}).get("by_horizon", {}) or {}
    primary_h = cand.get("primary_horizon_days")
    primary = retest.get(str(primary_h), {}) or {}
    surv = ki.get("survivorship_caveat_summary", {}) or {}
    nxt = ki.get("recommended_next_phase", {}) or {}
    interp = ki.get("interpretation", {}) or {}

    # Compact regime / stability summaries (the full per-fold / per-year blocks are not
    # duplicated here -- this is a small planning artifact, not a copy of the 2K-I results).
    regime = ki.get("regime_metrics", {}) or {}
    spy = regime.get("spy_forward_return", {}) or {}
    mvol = regime.get("market_volatility_21d", {}) or {}
    stability = ki.get("stability_metrics", {}) or {}
    loyo = stability.get("leave_one_year_out", {}) or {}
    lofo = stability.get("leave_one_fold_out", {}) or {}
    degr = stability.get("degradation_vs_phase2k_c", {}) or {}
    regime_summary = {
        "spy_up_rank_ic": (spy.get("positive", {}) or {}).get("rank_ic"),
        "spy_down_rank_ic": (spy.get("negative", {}) or {}).get("rank_ic"),
        "spy_sign_flips_across_regime": spy.get("sign_flips_across_regime"),
        "high_vol_rank_ic": (mvol.get("high", {}) or {}).get("rank_ic"),
        "low_vol_rank_ic": (mvol.get("low", {}) or {}).get("rank_ic"),
        "vol_sign_flips_across_regime": mvol.get("sign_flips_across_regime"),
    }
    stability_summary = {
        "leave_one_year_out_sign_stable": loyo.get("sign_stable"),
        "leave_one_fold_out_sign_stable": lofo.get("sign_stable"),
        "degraded_vs_phase2k_c": degr.get("degraded"),
        "phase2k_c_residual_mean_rank_ic": degr.get("phase2k_c_residual_mean_rank_ic"),
    }
    return {
        "present": True,
        "phase": ki.get("phase"),
        "recommendation": rec.get("recommendation"),
        "recommendation_reason": rec.get("reason"),
        "failed_gates": list(rec.get("failed_gates", []) or []),
        "candidate_feature": cand.get("feature"),
        "primary_horizon_days": primary_h,
        "primary_residual_mean_rank_ic": primary.get(
            "mean_residual_rank_ic", interp.get("primary_mean_residual_rank_ic")),
        "n_walk_forward_folds": wf.get("n_folds"),
        "total_effective_validation_obs": wf.get("total_effective_validation_obs"),
        "pooled_validation_mean_residual_rank_ic": wf.get(
            "pooled_validation_mean_residual_rank_ic"),
        "majority_validation_windows_same_sign": wf.get("majority_validation_windows_same_sign"),
        "regime_summary": regime_summary,
        "stability_summary": stability_summary,
        "survivorship_caveat_summary": surv,
        "recommended_next_phase": nxt,
        "results_are_survivorship_biased": rec.get("results_are_survivorship_biased"),
    }


def confirm_routing(ki_summary: Dict[str, Any]) -> Dict[str, Any]:
    """Confirm Phase 2K-I routed here (EXPANDED_RETEST_FAIL -> 2K-J / this title)."""
    nxt = ki_summary.get("recommended_next_phase", {}) or {}
    rec_is_fail = ki_summary.get("recommendation") == "EXPANDED_RETEST_FAIL"
    routes_to_2k_j = nxt.get("phase") == "2K-J"
    title_matches = nxt.get("title") == "Alpha Backlog Refresh After Expanded Retest Failure"
    return {
        "phase2k_i_present": bool(ki_summary.get("present")),
        "recommendation_is_expanded_retest_fail": rec_is_fail,
        "recommended_next_phase_is_2k_j": routes_to_2k_j,
        "recommended_next_phase_title_matches": title_matches,
        "routing_confirmed": bool(rec_is_fail and routes_to_2k_j and title_matches),
    }


# --------------------------------------------------------------------------- #
# Failed-candidate decision + failure diagnostics
# --------------------------------------------------------------------------- #
def build_failed_candidate_decision(ki_summary: Dict[str, Any]) -> Dict[str, Any]:
    candidate = ki_summary.get("candidate_feature") or STOPPED_CANDIDATE
    ic = ki_summary.get("primary_residual_mean_rank_ic")
    reason = (
        f"{candidate} was the lone Phase 2K-C residual survivor but failed the Phase 2K-I "
        f"expanded retest on the larger, longer, survivorship-caveated free panel: its primary "
        f"residual rank IC turned negative (mean ~{ic}) and the model-free walk-forward did not "
        f"hold the reference sign. It is stopped as a standalone alpha. No model candidate is "
        f"created, no model is trained, and no production edge is claimed.")
    return {
        "candidate": candidate,
        "status": "STOPPED_AFTER_EXPANDED_RETEST_FAIL",
        "stopped_as_standalone_alpha": True,
        "create_model_candidate_now": False,
        "train_model_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "no_model_candidate_allowed": True,
        "no_model_training_allowed": True,
        "reason": reason,
    }


def build_failure_diagnostics(ki_summary: Dict[str, Any], kc: Optional[Dict[str, Any]],
                              klb: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    primary_ic = ki_summary.get("primary_residual_mean_rank_ic")
    pooled_ic = ki_summary.get("pooled_validation_mean_residual_rank_ic")
    failed_gates = ki_summary.get("failed_gates", [])

    # Prior, thin-data results for the same candidate (positive on the ~40-name 2023-26 export).
    prior_kc_ic = None
    if kc is not None:
        cand_list = kc.get("candidates", []) or []
        for c in cand_list:
            if c.get("feature") == STOPPED_CANDIDATE:
                prior_kc_ic = (c.get("overall", {}) or {}).get("mean_rank_ic")
                break
    prior_klb_ic = None
    if klb is not None:
        prior_klb_ic = (klb.get("interpretation", {}) or {}).get(
            "pooled_validation_mean_residual_rank_ic")

    full_sample_negative = isinstance(primary_ic, (int, float)) and primary_ic < 0
    wf_failed = (isinstance(pooled_ic, (int, float)) and pooled_ic <= 0) or (
        "majority_validation_folds_same_sign" in failed_gates)
    direction_reversed = (
        isinstance(prior_kc_ic, (int, float)) and prior_kc_ic > 0
        and isinstance(primary_ic, (int, float)) and primary_ic < 0)

    regime = ki_summary.get("regime_summary", {}) or {}
    stability = ki_summary.get("stability_summary", {}) or {}
    loyo_sign_stable = stability.get("leave_one_year_out_sign_stable")
    degraded = stability.get("degraded_vs_phase2k_c")

    # No single regime / year rescues the signal: it is negative across SPY up/down and
    # high/low market-vol splits and across leave-one-year-out, so the failure is pervasive.
    no_regime_rescue = (regime.get("spy_sign_flips_across_regime") is False
                        and regime.get("vol_sign_flips_across_regime") is False)

    return {
        "full_sample_residual_ic_negative": bool(full_sample_negative),
        "full_sample_primary_residual_mean_rank_ic": primary_ic,
        "walk_forward_validation_failed": bool(wf_failed),
        "walk_forward_pooled_validation_mean_residual_rank_ic": pooled_ic,
        "n_walk_forward_folds": ki_summary.get("n_walk_forward_folds"),
        "effective_validation_obs": ki_summary.get("total_effective_validation_obs"),
        "signal_direction_reversed_vs_prior_thin_data": bool(direction_reversed),
        "prior_thin_data_phase2k_c_residual_mean_rank_ic": prior_kc_ic,
        "prior_thin_data_phase2l_b_pooled_validation_ic": prior_klb_ic,
        "expanded_primary_residual_mean_rank_ic": primary_ic,
        "failure_is_pervasive_not_single_regime_or_year": bool(no_regime_rescue),
        "leave_one_year_out_sign_stable": loyo_sign_stable,
        "no_single_regime_split_rescues_signal": bool(no_regime_rescue),
        "regime_spy_up_rank_ic": regime.get("spy_up_rank_ic"),
        "regime_spy_down_rank_ic": regime.get("spy_down_rank_ic"),
        "regime_high_vol_rank_ic": regime.get("high_vol_rank_ic"),
        "regime_low_vol_rank_ic": regime.get("low_vol_rank_ic"),
        "degraded_vs_phase2k_c": degraded,
        "failed_gates": list(failed_gates),
        "survivorship_caveat_implications": (
            "The expanded universe is a current-as-of (not point-in-time) membership "
            "approximation, so every result is reported as survivorship-biased. The thin-data "
            "positive IC was likely an artifact of the small single-regime mega-cap sample; on "
            "the broader, longer panel the apparent liquidity edge does not survive even before "
            "the survivorship caveat is resolved. A clean point-in-time universe would only "
            "tighten, not rescue, this negative result."),
        "data_expansion_lesson": (
            "Acquiring more, longer, broader data -- rather than fitting a model to the thin "
            "sample -- correctly overturned the earlier conclusion. The Phase 2K-C / 2L-B "
            "positive residual IC for the liquidity candidate was a small-sample, single-regime "
            "artifact: it reversed sign and turned negative once retested across ~10 years and "
            "~129 names. Broader data changed the conclusion, which is exactly why the data was "
            "expanded before any model was built."),
    }


# --------------------------------------------------------------------------- #
# Alpha backlog refresh
# --------------------------------------------------------------------------- #
def _preserved_prior_backlog(backlog_json: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Preserve a light view of the prior Phase 2K-A alpha hypothesis backlog (read-only)."""
    if backlog_json is None:
        return []
    items = backlog_json.get("alpha_hypothesis_backlog", []) or []
    out: List[Dict[str, Any]] = []
    for h in items:
        out.append({
            "hypothesis_id": h.get("hypothesis_id"),
            "title": h.get("title"),
            "priority": h.get("priority"),
            "prior_go_to_test": h.get("go_to_test"),
            "data_availability": h.get("data_availability"),
        })
    return out


# The refreshed backlog. Every item carries: id, name, category, rationale, required_data,
# leakage_risks, expected_horizon, priority, status, next_test, production_allowed (always
# false). ``standalone_alpha`` marks whether the item is a standalone alpha candidate; the
# stopped liquidity candidate is diagnostic-only and is NOT a standalone alpha candidate.
def build_refreshed_backlog() -> List[Dict[str, Any]]:
    return [
        {
            "id": "residual_price_momentum_12_1",
            "name": "Residual Price Momentum (12-1)",
            "category": "price_momentum_alternatives",
            "primary_feature": "momentum_12_1",
            "standalone_alpha": True,
            "rationale": (
                "Price momentum (12-1) was DROPped in Phase 2K-C on thin data because its "
                "bootstrap CI included zero, not because the mechanism was disproven. Retest it "
                "model-free as a beta/vol-neutralized residual signal on the expanded multi-regime "
                "panel, where more effective observations and a real drawdown are available."),
            "required_data": ["expanded_price_volume_panel"],
            "leakage_risks": (
                "Formation window must be trailing-only and skip the most recent month; "
                "overlapping holding windows require a block bootstrap at the holding horizon."),
            "expected_horizon": "63d-252d",
            "priority": "high",
            "status": "QUEUED_MODEL_FREE_SCREEN",
            "next_test": "2K-K residual rank-IC screen on the expanded price/volume panel",
            "production_allowed": False,
        },
        {
            "id": "short_horizon_residual_reversal",
            "name": "Short-Horizon Residual Mean Reversion",
            "category": "short_term_reversal_mean_reversion",
            "primary_feature": "residual_reversal_5_21d",
            "standalone_alpha": True,
            "rationale": (
                "Names extended versus their beta-neutral peers over a short window tend to "
                "revert. This is the residual (beta-neutral) reversal hypothesis from Phase 2K-A, "
                "kept distinct from the raw-return momentum that already failed, and it is testable "
                "model-free on the expanded panel."),
            "required_data": ["expanded_price_volume_panel"],
            "leakage_risks": (
                "Overlapping short windows inflate significance (block-bootstrap at the holding "
                "horizon); bid/ask bounce can masquerade as reversal."),
            "expected_horizon": "5d-21d",
            "priority": "high",
            "status": "QUEUED_MODEL_FREE_SCREEN",
            "next_test": "2K-K residual rank-IC screen on the expanded price/volume panel",
            "production_allowed": False,
        },
        {
            "id": "volatility_adjusted_momentum",
            "name": "Volatility-Adjusted Momentum",
            "category": "volatility_adjusted_momentum",
            "primary_feature": "vol_adjusted_momentum_63d",
            "standalone_alpha": True,
            "rationale": (
                "Scaling trailing momentum by realized volatility targets selection that is not "
                "merely the 63d vol/beta risk tilt that failed in Phase 2I/2J. The trailing vol "
                "controls are already computed on the expanded panel, so this is a cheap "
                "model-free residual screen."),
            "required_data": ["expanded_price_volume_panel"],
            "leakage_risks": (
                "Volatility and beta must be trailing-only; the residualization must regress out "
                "the same risk controls per date so the signal is not a repackaged vol premium."),
            "expected_horizon": "63d-126d",
            "priority": "medium",
            "status": "QUEUED_MODEL_FREE_SCREEN",
            "next_test": "2K-K residual rank-IC screen on the expanded price/volume panel",
            "production_allowed": False,
        },
        {
            "id": "sector_relative_momentum",
            "name": "Sector-Relative Momentum",
            "category": "sector_relative_momentum",
            "primary_feature": "sector_relative_momentum_21_63d",
            "standalone_alpha": True,
            "rationale": (
                "De-meaning returns within a name's own sector strips the broad market move that "
                "the failed 63d tilt was riding, isolating intra-sector selection. Needs only a "
                "low-cost sector map on top of the expanded price/volume panel."),
            "required_data": ["expanded_price_volume_panel", "point_in_time_sector_industry_mapping"],
            "leakage_risks": (
                "Sector membership must be point-in-time (no current GICS applied to history); "
                "thin sectors give unstable cross-sections."),
            "expected_horizon": "21d-63d",
            "priority": "medium",
            "status": "DEFERRED_NEEDS_SECTOR_MAP",
            "next_test": (
                "model-free residual rank-IC screen once a low-cost point-in-time sector map is "
                "attached (after the price/volume-only 2K-K screen)"),
            "production_allowed": False,
        },
        {
            "id": "liquidity_illiquidity_diagnostic",
            "name": "Liquidity / Illiquidity (Amihud, dollar volume) -- Diagnostic Only",
            "category": "liquidity_deprioritized_diagnostic_only",
            "primary_feature": STOPPED_CANDIDATE,
            "standalone_alpha": False,
            "rationale": (
                "The standalone liquidity candidate avg_dollar_volume_21d FAILED the expanded "
                "retest (residual IC reversed negative). Liquidity / illiquidity is therefore "
                "deprioritized to a diagnostic-only role: usable as a size/liquidity neutralizer "
                "or a control when paired with another mechanism, but NOT as a standalone alpha. "
                "It is not reintroduced as a standalone alpha candidate."),
            "required_data": ["expanded_price_volume_panel"],
            "leakage_risks": (
                "Liquidity correlates with size and price level; only ever used trailing-only and "
                "size/price-neutralized as a control, never as a standalone ranking signal."),
            "expected_horizon": "n/a (diagnostic control only)",
            "priority": "deprioritized",
            "status": "DEPRIORITIZED_DIAGNOSTIC_ONLY",
            "next_test": "no standalone retest; available only as a neutralizer / control",
            "production_allowed": False,
        },
        {
            "id": "estimate_revisions_and_surprise",
            "name": "Analyst Estimate Revisions & Earnings Surprise",
            "category": "earnings_fundamentals_estimate_revision_future_data",
            "primary_feature": "estimate_revision_ratio_and_standardized_surprise",
            "standalone_alpha": True,
            "rationale": (
                "An information-diffusion signal largely orthogonal to a price-only liquidity or "
                "vol/beta premium, so it is unlikely to be the same artifact that just failed. "
                "Requires point-in-time estimate and earnings-date data, so it is a future-data "
                "backlog item, not part of the immediate price/volume screen."),
            "required_data": [
                "point_in_time_analyst_estimates", "earnings_calendar_and_surprise",
                "broader_point_in_time_universe"],
            "leakage_risks": (
                "Revisions must be timestamped at the moment they became public; using the final "
                "consensus for a past date imports the future; earnings dates must be actual."),
            "expected_horizon": "21d-63d",
            "priority": "medium",
            "status": "FUTURE_DATA_BACKLOG",
            "next_test": "deferred until point-in-time estimate / earnings data is scoped",
            "production_allowed": False,
        },
        {
            "id": "quality_profitability",
            "name": "Quality / Profitability Factor",
            "category": "quality_profitability_future_data",
            "primary_feature": "quality_profitability_composite",
            "standalone_alpha": True,
            "rationale": (
                "Quality / profitability ranks forward returns through a channel that is not pure "
                "market beta or liquidity, diversifying away from the failed price-only tilt. "
                "Requires point-in-time fundamentals, so it is a future-data backlog item."),
            "required_data": ["point_in_time_fundamentals", "broader_point_in_time_universe"],
            "leakage_risks": (
                "Restated / as-reported financials and report-date misalignment are the dominant "
                "leakage source; only as-first-reported data with correct report-date lags is "
                "admissible. Survivorship bias if delisted names are dropped."),
            "expected_horizon": "63d-252d",
            "priority": "medium",
            "status": "FUTURE_DATA_BACKLOG",
            "next_test": "deferred until point-in-time fundamentals are scoped",
            "production_allowed": False,
        },
    ]


def build_alpha_backlog_refresh(backlog_json: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    refreshed = build_refreshed_backlog()
    categories = sorted({item["category"] for item in refreshed})
    return {
        "preserved_prior_backlog_source": _norm(PHASE2K_BACKLOG_JSON),
        "preserved_prior_backlog": _preserved_prior_backlog(backlog_json),
        "refreshed_backlog": refreshed,
        "refreshed_categories": categories,
        "stopped_standalone_candidates": [STOPPED_CANDIDATE],
        "liquidity_status": "deprioritized_diagnostic_only",
        "required_item_fields": [
            "id", "name", "category", "rationale", "required_data", "leakage_risks",
            "expected_horizon", "priority", "status", "next_test", "production_allowed"],
        "note": (
            "Liquidity is deprioritized to diagnostic-only after the expanded retest failure; "
            f"{STOPPED_CANDIDATE} is not reintroduced as a standalone alpha candidate. All items "
            "are research-only with production_allowed = false."),
    }


# --------------------------------------------------------------------------- #
# Research plan, data requirements, leakage controls
# --------------------------------------------------------------------------- #
def build_research_priorities() -> List[Dict[str, Any]]:
    return [
        {
            "rank": 1,
            "action": (
                "Run a model-free residual rank-IC screen of prioritized price/volume alpha "
                "candidates (residual momentum, short-horizon residual reversal, "
                "volatility-adjusted momentum) on the expanded survivorship-caveated panel."),
            "uses_data": "expanded_price_volume_panel",
            "model_free": True,
            "excludes_standalone_avg_dollar_volume": True,
        },
        {
            "rank": 2,
            "action": (
                "Attach a low-cost point-in-time sector map and screen sector-relative momentum / "
                "reversal model-free; still no model training."),
            "uses_data": "expanded_price_volume_panel + point_in_time_sector_industry_mapping",
            "model_free": True,
            "excludes_standalone_avg_dollar_volume": True,
        },
        {
            "rank": 3,
            "action": (
                "Only if a price/volume feature family clears the model-free screen across more "
                "than one regime, scope (do not yet build) a separately gated walk-forward "
                "single-feature candidate."),
            "uses_data": "validated_price_volume_feature_family",
            "model_free": True,
            "excludes_standalone_avg_dollar_volume": True,
        },
        {
            "rank": 4,
            "action": (
                "Scope point-in-time fundamentals, estimate-revision, and earnings data (cost, "
                "coverage, leakage controls) for the future-data backlog items; do not purchase "
                "or build on paid data yet."),
            "uses_data": "future_data_scoping_only",
            "model_free": True,
            "excludes_standalone_avg_dollar_volume": True,
        },
    ]


def build_next_model_free_tests() -> List[Dict[str, Any]]:
    return [
        {
            "id": "residual_price_momentum_12_1",
            "name": "Residual Price Momentum (12-1)",
            "uses_data": "expanded_price_volume_panel",
            "model_free": True,
            "trains_model": False,
        },
        {
            "id": "short_horizon_residual_reversal",
            "name": "Short-Horizon Residual Mean Reversion",
            "uses_data": "expanded_price_volume_panel",
            "model_free": True,
            "trains_model": False,
        },
        {
            "id": "volatility_adjusted_momentum",
            "name": "Volatility-Adjusted Momentum",
            "uses_data": "expanded_price_volume_panel",
            "model_free": True,
            "trains_model": False,
        },
    ]


def build_data_requirements() -> Dict[str, Any]:
    return {
        "expanded_price_volume_panel": {
            "description": (
                "The Phase 2K-H expanded adjusted-OHLCV + volume panel on the D: data root "
                "(~10.4 years, ~129 names incl. SPY), used for price/volume-only model-free "
                "screens. Read by the screen analyzer, never modified."),
            "availability": "available_now",
            "must_be_point_in_time": False,
            "is_paid_data": False,
            "survivorship_caveated": True,
        },
        "point_in_time_sector_industry_mapping": {
            "description": "Low-cost point-in-time sector / industry map for sector-relative tests.",
            "availability": "low_cost",
            "must_be_point_in_time": True,
            "is_paid_data": False,
            "survivorship_caveated": True,
        },
        "point_in_time_analyst_estimates": {
            "description": "Revision-timestamped consensus estimates (future-data backlog only).",
            "availability": "needs_acquisition",
            "must_be_point_in_time": True,
            "is_paid_data": True,
            "survivorship_caveated": True,
        },
        "earnings_calendar_and_surprise": {
            "description": "Actual earnings dates and realized-vs-expected surprise (future-data).",
            "availability": "needs_acquisition",
            "must_be_point_in_time": True,
            "is_paid_data": True,
            "survivorship_caveated": True,
        },
        "point_in_time_fundamentals": {
            "description": "As-first-reported fundamentals with report-date lags (future-data).",
            "availability": "needs_acquisition",
            "must_be_point_in_time": True,
            "is_paid_data": True,
            "survivorship_caveated": True,
        },
        "broader_point_in_time_universe": {
            "description": (
                "A point-in-time constituent universe to replace the current-as-of approximation "
                "and remove the survivorship caveat; the scarce, deferred piece."),
            "availability": "needs_acquisition",
            "must_be_point_in_time": True,
            "is_paid_data": True,
            "survivorship_caveated": True,
        },
    }


def build_leakage_controls() -> Dict[str, Any]:
    return {
        "features_trailing_point_in_time_only": True,
        "labels_strictly_forward": True,
        "labels_forward_filled": False,
        "per_date_cross_sectional_residualization": True,
        "embargo_equals_forward_horizon": True,
        "block_bootstrap_at_holding_horizon": True,
        "no_random_cross_validation": True,
        "survivorship_caveat_carried_forward": True,
        "point_in_time_membership_claimed": False,
        "notes": (
            "Every next-phase screen keeps controls trailing-only and labels strictly forward, "
            "residualizes per date and cross-sectionally, embargoes by the forward horizon, and "
            "reports every result as survivorship-biased until a point-in-time universe is "
            "acquired. No look-ahead and no forward-filled labels."),
    }


# --------------------------------------------------------------------------- #
# Recommendation + routing
# --------------------------------------------------------------------------- #
def build_recommendation(failed_decision: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "recommendation": "REFRESH_ALPHA_BACKLOG",
        "allowed_values": ["REFRESH_ALPHA_BACKLOG"],
        "standalone_liquidity_candidate_stopped": True,
        "continue_model_research_now": False,
        "create_model_candidate_now": False,
        "train_model_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "reason": (
            "The standalone liquidity candidate failed the expanded survivorship-caveated retest, "
            "so it is stopped and the alpha backlog is refreshed toward diversified, model-free "
            "price/volume hypotheses (and future-data factor families). No model candidate is "
            "created and no model is trained now; the disciplined next step is a model-free "
            "screen on the expanded panel, not model training. No production edge is claimed."),
    }


def build_recommended_next_phase() -> Dict[str, str]:
    return {
        "phase": "2K-K",
        "title": "Expanded Dataset Model-Free Alpha Screen",
        "purpose": (
            "Run a new model-free screen of prioritized price/volume alpha candidates "
            "(residual momentum, short-horizon residual reversal, volatility-adjusted momentum) "
            "on the expanded survivorship-caveated dataset, explicitly excluding standalone "
            "avg_dollar_volume_21d as an alpha candidate. Still no model training, no model "
            "candidate, and no production integration."),
    }


def build_interpretation(ki_summary: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "model_trained": False,
        "model_fitted": False,
        "model_candidate_created": False,
        "authorized_to_train_model": False,
        "authorized_to_serve_model": False,
        "production_edge_claimed": False,
        "results_are_survivorship_biased": True,
        "narrative": (
            "Phase 2K-J is the disciplined response to the Phase 2K-I expanded retest failure. "
            "The liquidity candidate avg_dollar_volume_21d, the lone Phase 2K-C survivor, "
            "reversed sign and turned negative on the broader ~10-year, ~129-name "
            "survivorship-caveated panel, so it is stopped as a standalone alpha. The earlier "
            "thin-data positive IC was a small-sample, single-regime artifact -- expanding the "
            "data, rather than fitting a model, correctly overturned it. The alpha backlog is "
            "refreshed toward diversified, model-free price/volume hypotheses now and "
            "future-data factor families later. This artifact computes no new alpha, reads no D: "
            "dataset, trains nothing, fits nothing, creates no model candidate, and claims no "
            "production edge."),
    }


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_results(*, inputs_dir: Optional[str] = None) -> Dict[str, Any]:
    inputs_dir = inputs_dir or _OUTPUT_DIR
    paths = _resolve_paths(inputs_dir)

    ki = _safe_read_json(paths["phase2k_i_json"])
    kh = _safe_read_json(paths["phase2k_h_summary_json"])
    backlog_json = _safe_read_json(paths["phase2k_alpha_backlog_json"])
    kc = _safe_read_json(paths["phase2k_c_json"])
    klb = _safe_read_json(paths["phase2l_b_json"])

    ki_summary = summarize_phase2k_i(ki)
    routing = confirm_routing(ki_summary)
    failed_decision = build_failed_candidate_decision(ki_summary)
    diagnostics = build_failure_diagnostics(ki_summary, kc, klb)

    upstream = dict(ki_summary)
    upstream["routing_confirmation"] = routing
    upstream["phase2k_h_present"] = bool(kh is not None)

    return {
        "phase": PHASE,
        "generated_at": _now(),
        "inputs_read": {k: _norm(v) for k, v in paths.items()},
        # Safety flags (machine-readable).
        "database_touched": False,
        "database_write_executed": False,
        "migration_executed": False,
        "deployment_executed": False,
        "model_v2_enabled": False,
        "production_edge_claimed": False,
        "no_trading": True,
        "no_orders": True,
        "no_automation": True,
        "upstream_phase2k_i_summary": upstream,
        "failed_candidate_decision": failed_decision,
        "failure_diagnostics": diagnostics,
        "alpha_backlog_refresh": build_alpha_backlog_refresh(backlog_json),
        "research_priorities": build_research_priorities(),
        "next_model_free_tests": build_next_model_free_tests(),
        "data_requirements": build_data_requirements(),
        "leakage_controls": build_leakage_controls(),
        "interpretation": build_interpretation(ki_summary),
        "recommendation": build_recommendation(failed_decision),
        "recommended_next_phase": build_recommended_next_phase(),
    }


def write_results(results: Dict[str, Any], path: str = RESULTS_JSON) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, allow_nan=False)


def run(output_path: str = RESULTS_JSON, *, inputs_dir: Optional[str] = None) -> Dict[str, Any]:
    """Read the small prior-phase summaries (read-only) and write the refresh JSON."""
    results = build_results(inputs_dir=inputs_dir)
    write_results(results, output_path)
    return results


def main() -> int:
    t0 = time.perf_counter()
    d = run()
    elapsed = time.perf_counter() - t0
    up = d["upstream_phase2k_i_summary"]
    dec = d["failed_candidate_decision"]
    rec = d["recommendation"]
    nxt = d["recommended_next_phase"]
    print(f"[phase2k-j] upstream 2K-I recommendation   : {up.get('recommendation')}")
    print(f"[phase2k-j] routing confirmed              : "
          f"{up.get('routing_confirmation', {}).get('routing_confirmed')}")
    print(f"[phase2k-j] failed candidate               : {dec['candidate']}")
    print(f"[phase2k-j] candidate status               : {dec['status']}")
    print(f"[phase2k-j] create model candidate now     : {dec['create_model_candidate_now']}")
    print(f"[phase2k-j] train model now                : {dec['train_model_now']}")
    print(f"[phase2k-j] backlog items                  : "
          f"{len(d['alpha_backlog_refresh']['refreshed_backlog'])}")
    print(f"[phase2k-j] recommendation                 : {rec['recommendation']}")
    print(f"[phase2k-j] recommended next phase          : {nxt['phase']} ({nxt['title']})")
    print(f"[phase2k-j] results written                : {RESULTS_JSON}")
    print(f"[phase2k-j] elapsed seconds                : {elapsed:.2f}")
    print("[phase2k-j] read-only; no new alpha compute, no D: read, no model, no production edge.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
