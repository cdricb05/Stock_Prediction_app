"""Phase 2L-A Walk-Forward Residual Model Research Design (offline, read-only).

Phase 2K-C ran the Phase 2I-B robustness battery on the Phase 2K-B residual
candidates. Exactly one candidate -- avg_dollar_volume_21d -- cleared the
conservative gate and was labelled KEEP_FOR_MODEL_RESEARCH; two others are research
leads only and one was dropped. Surviving that gate is NOT a production edge and NOT
permission to train or serve a model: it only earns the right to *design* an
out-of-sample, walk-forward validation protocol for the surviving residual signal.

This phase produces exactly that design. It is a planning / gating artifact, not a
model and not a backtest. It reads the upstream Phase 2K-C / 2K-B / 2K-A / 2J-A JSON
summaries plus the Phase 2G run summary, confirms the sole model-research candidate,
and writes one design JSON describing:

  * the walk-forward scheme (expanding-window and rolling-window training options,
    strictly sequential validation splits that are always chronologically after
    training, an embargo equal to the forward horizon because the 63d labels
    overlap, and no random cross-validation);
  * a model-free baseline that must be validated FIRST (a rank-only strategy on the
    single feature with no fitted model, scored on the residual label in each
    validation window);
  * a later, gated, single-feature monotonic-model option that this phase does NOT
    authorize (no multi-feature model, no neural network, no serving integration);
  * the walk-forward pass/fail gates;
  * the leakage controls (trailing-only features, point-in-time universe, embargo,
    train-only residualization fit, no validation-set threshold tuning);
  * the implementation plan for the next phase (2L-B), which itself trains nothing.

This phase trains no model, fits nothing, scores nothing, and creates no model
candidate. It reads JSON summaries and writes one design JSON. The machine-readable
safety flags are emitted in that JSON; the full guardrail rationale lives in
docs/phase2l_a_walk_forward_design_v1.md, not in this source.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from typing import Any, Dict, List, Optional

PHASE = "2L-A"

_OUTPUT_DIR = os.path.join("research", "output")

# Inputs (read-only).
INPUT_2KC_JSON = os.path.join(_OUTPUT_DIR, "phase2k_c_residual_robustness.json")
INPUT_2KB_JSON = os.path.join(_OUTPUT_DIR, "phase2k_b_residual_signal.json")
INPUT_2KA_JSON = os.path.join(_OUTPUT_DIR, "phase2k_alpha_backlog.json")
INPUT_2J_JSON = os.path.join(_OUTPUT_DIR, "phase2j_research_decision.json")
RUN_SUMMARY_JSON = os.path.join(
    _OUTPUT_DIR, "phase2g_c_real_data_run_summary.json")

# The single output this analyzer is allowed to write.
DESIGN_JSON = os.path.join(_OUTPUT_DIR, "phase2l_a_walk_forward_design.json")

# Recommendation vocabulary used upstream by Phase 2K-C.
REC_MODEL = "KEEP_FOR_MODEL_RESEARCH"
REC_LEAD = "KEEP_AS_RESEARCH_LEAD_ONLY"
REC_DROP = "DROP"
REC_NEED = "NEED_MORE_DATA"

# Design constants for the walk-forward protocol (sessions ~= trading days).
COMPARISON_HORIZONS_DAYS = [21, 63]   # primary is read from 2K-C; comparison only
MIN_TRAIN_SESSIONS = 252              # ~1 trading year minimum training span
ROLLING_TRAIN_SESSIONS = 504         # ~2 trading years for the rolling-window option
VALIDATION_SESSIONS = 63             # one validation block ~= the primary horizon
STEP_SESSIONS = 63                   # advance the walk-forward window one block at a time
# Gate thresholds (inherited from Phase 2I-A / 2K-B / 2K-C so nothing is tuned here).
RANK_IC_FLOOR = 0.03                 # validation mean residual IC must clear this
MAJORITY_SIGN_FRACTION = 0.50        # > this fraction of validation windows same sign
DEGRADATION_TOLERANCE_FRACTION = 0.50  # validation IC >= this * 2K-C full-sample IC
CONCENTRATION_TOP3_THRESHOLD = 0.50  # top-3 long-leg share above which -> concentrated
MIN_EFFECTIVE_OBS = 8.0              # (validation dates / horizon) floor after embargo


# --------------------------------------------------------------------------- #
# Read-only helpers
# --------------------------------------------------------------------------- #
def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _round(x: Optional[float], n: int = 6) -> Optional[float]:
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    if xf != xf or xf in (float("inf"), float("-inf")):
        return None
    return round(xf, n)


def load_upstream() -> Dict[str, Any]:
    """Read the Phase 2K-C decision plus the upstream context summaries."""
    twokc = _read_json(INPUT_2KC_JSON)
    rs = twokc.get("recommendation_summary", {})
    counts = rs.get("counts", {})
    survivors = list(rs.get("keep_for_model_research", []))
    leads = list(rs.get("keep_as_research_lead_only", []))
    dropped = list(rs.get("drop", []))
    need = list(rs.get("need_more_data", []))

    # Per-candidate detail for the survivor(s): horizon, direction, residual IC.
    by_feature: Dict[str, Any] = {}
    for c in twokc.get("candidates", []):
        by_feature[c.get("feature")] = {
            "horizon_days": c.get("horizon_days"),
            "residual_mean_rank_ic": c.get("overall", {}).get("mean_rank_ic"),
            "residual_information_ratio": c.get("overall", {}).get("information_ratio"),
            "direction": c.get("overall", {}).get("direction"),
            "n_dates": c.get("overall", {}).get("n_dates"),
            "n_rows": c.get("overall", {}).get("n_rows"),
        }

    # Best-effort context reads; absent files degrade to None rather than failing.
    run_summary: Dict[str, Any] = {}
    twoj: Dict[str, Any] = {}
    try:
        run_summary = _read_json(RUN_SUMMARY_JSON)
    except (OSError, ValueError):
        run_summary = {}
    try:
        twoj = _read_json(INPUT_2J_JSON)
    except (OSError, ValueError):
        twoj = {}

    return {
        "phase2k_c_phase": twokc.get("phase"),
        "phase2k_c_recommended_next_phase": twokc.get(
            "recommended_next_phase", {}).get("phase"),
        "counts": counts,
        "survivors_keep_for_model_research": survivors,
        "research_leads_only": leads,
        "dropped": dropped,
        "need_more_data": need,
        "candidate_detail": by_feature,
        "panel": twokc.get("panel", {}),
        "residualization_config": twokc.get("residualization_config", {}),
        "run_summary": run_summary,
        "phase2j": twoj,
    }


# --------------------------------------------------------------------------- #
# Candidate selection
# --------------------------------------------------------------------------- #
def select_candidate(upstream: Dict[str, Any]) -> Dict[str, Any]:
    """Confirm the sole Phase 2K-C model-research candidate and its parameters."""
    survivors = upstream["survivors_keep_for_model_research"]
    feature = survivors[0] if survivors else None
    detail = upstream["candidate_detail"].get(feature, {}) if feature else {}
    primary_horizon = detail.get("horizon_days") or 63
    return {
        "feature": feature,
        "is_sole_model_research_candidate": len(survivors) == 1,
        "n_model_research_candidates": len(survivors),
        "primary_horizon_days": primary_horizon,
        "direction": detail.get("direction"),
        "phase2k_c_residual_mean_rank_ic": detail.get("residual_mean_rank_ic"),
        "phase2k_c_residual_information_ratio": detail.get("residual_information_ratio"),
        "phase2k_c_n_dates": detail.get("n_dates"),
        "phase2k_c_n_rows": detail.get("n_rows"),
        "status": (
            "KEEP_FOR_MODEL_RESEARCH -- robust enough to scope walk-forward "
            "validation; not a production edge and not permission to train or serve "
            "a model"),
    }


# --------------------------------------------------------------------------- #
# Design sections
# --------------------------------------------------------------------------- #
def build_walk_forward_design(candidate: Dict[str, Any],
                              upstream: Dict[str, Any]) -> Dict[str, Any]:
    primary = candidate["primary_horizon_days"]
    resid_cfg = upstream.get("residualization_config", {})
    return {
        "target_label": {
            "name": "beta_vol_neutralized_residual_forward_return",
            "description": (
                "The Phase 2K-B / 2K-C residual label: forward excess-vs-SPY return "
                "neutralized cross-sectionally, per date, against the trailing risk "
                "controls via OLS (with a demean fallback). Reconstructed identically "
                "to upstream; no relabeling."),
            "base": resid_cfg.get("label_base", "forward_excess_return_vs_spy"),
            "controls": resid_cfg.get("controls", []),
            "primary_horizon_days": primary,
        },
        "candidate_signal": candidate["feature"],
        "primary_horizon_days": primary,
        "comparison_horizons_days": COMPARISON_HORIZONS_DAYS,
        "scheme": (
            "Strictly sequential walk-forward. The validation period is always "
            "chronologically after the training period. No random cross-validation "
            "and no shuffling of dates."),
        "no_random_cross_validation": True,
        "training_windows": {
            "expanding_window": {
                "type": "expanding",
                "min_train_sessions": MIN_TRAIN_SESSIONS,
                "description": (
                    "Anchored start; the training span grows by one step each fold. "
                    "Captures all history up to the embargo before each validation "
                    "block."),
            },
            "rolling_window": {
                "type": "rolling",
                "train_sessions": ROLLING_TRAIN_SESSIONS,
                "description": (
                    "Fixed-length trailing training span that slides forward each "
                    "fold; tests whether the residual signal is stationary or decays "
                    "as older history drops out."),
            },
        },
        "validation_windows": {
            "type": "sequential_time_splits",
            "validation_sessions": VALIDATION_SESSIONS,
            "step_sessions": STEP_SESSIONS,
            "test_period_always_after_training_period": True,
            "no_overlap_between_train_and_validation_label_windows": True,
            "embargo_sessions": primary,
            "embargo_equals_forward_horizon": True,
            "embargo_rationale": (
                "The forward label spans the horizon, so a training label that ends "
                "inside the validation window would peek across the boundary. An "
                "embargo equal to the horizon is dropped between the last training "
                "label and the first validation label to remove that overlap."),
            "no_random_cross_validation": True,
        },
        "fold_plan": {
            "advance": "one validation block per fold (step = validation length)",
            "min_train_sessions": MIN_TRAIN_SESSIONS,
            "validation_sessions": VALIDATION_SESSIONS,
            "embargo_sessions": primary,
            "panel_window": {
                "date_start": upstream.get("panel", {}).get("date_start"),
                "date_end": upstream.get("panel", {}).get("date_end"),
                "n_dates": upstream.get("panel", {}).get("n_dates"),
                "n_tickers": upstream.get("panel", {}).get("n_tickers"),
            },
            "note": (
                "Concrete fold boundaries are derived in Phase 2L-B from the actual "
                "session calendar; this design fixes the protocol, not literal dates. "
                "On the current ~3-year, ~40-name panel only a few non-overlapping "
                "63d validation blocks exist after the embargo, which itself is a "
                "go/no-go signal on whether the data can support model research."),
        },
    }


def build_model_free_baseline_design(candidate: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "run_first": True,
        "strategy": "rank_only",
        "fitted_model": False,
        "description": (
            "Cross-sectionally rank names each date by " + str(candidate["feature"])
            + " and read the residual label outcome in each out-of-sample validation "
            "window. The long/short direction is fixed from the training window "
            "(Phase 2K-C measured it bullish) and applied unchanged to validation. "
            "Nothing is fitted; this is a model-free signal check, not a model."),
        "direction_source": "fixed from training window; not re-fit on validation",
        "metrics_per_validation_window": [
            "residual_label_rank_ic",
            "top_minus_bottom_residual_spread",
            "top_decile_residual_hit_rate",
            "turnover_stability_proxy",
            "ticker_concentration_in_long_leg",
        ],
        "turnover_stability_proxy": (
            "Rank-stability of the top quintile across consecutive dates from the "
            "existing scored panel (a proxy for turnover); no transaction costs and "
            "no order simulation are modeled."),
        "aggregation": (
            "Aggregate the per-validation-window metrics across folds; report the "
            "distribution, not a single pooled number, so a single fold cannot carry "
            "the result."),
        "purpose": (
            "Establish whether the raw residual signal survives genuine out-of-sample "
            "walk-forward windows before any model is even considered. If the "
            "model-free baseline fails the gates, no model is built."),
    }


def build_later_model_candidate_design() -> Dict[str, Any]:
    return {
        "authorized_now": False,
        "gated_behind": (
            "Phase 2L-B model-free walk-forward validation must pass the gates "
            "first; only then may a later phase consider the simple model option."),
        "allowed_model_class_when_unlocked": (
            "univariate monotonic mapping or a rank transform on the single feature "
            "only (e.g. isotonic or a monotone rank-to-score map); no parameter-heavy "
            "fitting"),
        "single_feature_only": True,
        "multi_feature_model": False,
        "neural_network": False,
        "complex_ml": False,
        "production_scoring": False,
        "serving_integration": False,
        "model_v2_enablement": False,
        "note": (
            "Described here only to bound what a future, separately gated phase may "
            "attempt. This phase authorizes none of it and creates no model "
            "candidate."),
    }


def build_pass_fail_gates(candidate: Dict[str, Any]) -> List[Dict[str, Any]]:
    ic_2kc = candidate.get("phase2k_c_residual_mean_rank_ic")
    degr_floor = (_round(DEGRADATION_TOLERANCE_FRACTION * ic_2kc)
                  if isinstance(ic_2kc, (int, float)) else None)
    return [
        {
            "id": "validation_mean_ic_above_floor",
            "description": ("Mean residual rank IC across validation windows is "
                            "positive and clears the inherited IC floor."),
            "threshold": RANK_IC_FLOOR,
            "pass_condition": "validation_mean_residual_ic >= rank_ic_floor",
        },
        {
            "id": "majority_validation_windows_same_sign",
            "description": ("A majority of validation windows share the training-"
                            "window sign."),
            "threshold": MAJORITY_SIGN_FRACTION,
            "pass_condition": "frac_windows_same_sign > majority_sign_fraction",
        },
        {
            "id": "bootstrap_ci_excludes_zero_in_validation",
            "description": ("A moving-block bootstrap CI (block = horizon) of the "
                            "pooled validation per-date IC excludes zero."),
            "threshold": "95% CI excludes 0",
            "pass_condition": "ci_low and ci_high on the same side of zero",
        },
        {
            "id": "no_single_validation_period_drives_result",
            "description": ("Leaving any single validation window out preserves the "
                            "sign and the above-floor magnitude."),
            "threshold": "leave-one-window-out sign stable",
            "pass_condition": "sign preserved dropping each validation window",
        },
        {
            "id": "no_excessive_ticker_concentration",
            "description": "Top-3 long-leg share stays at or below the threshold.",
            "threshold": CONCENTRATION_TOP3_THRESHOLD,
            "pass_condition": "top3_long_leg_share <= concentration_top3_threshold",
        },
        {
            "id": "no_regime_only_dependence",
            "description": ("Validation residual IC does not flip sign between the "
                            "SPY-up and SPY-down forward windows."),
            "threshold": "no sign flip across SPY up/down split",
            "pass_condition": "sign stable across the market-direction regime split",
        },
        {
            "id": "no_degradation_vs_phase2k_c",
            "description": ("Out-of-sample validation IC does not collapse versus the "
                            "Phase 2K-C full-sample residual IC."),
            "threshold": degr_floor,
            "pass_condition": (
                "validation_mean_residual_ic >= degradation_tolerance_fraction * "
                "phase2k_c_residual_mean_rank_ic"),
        },
        {
            "id": "no_lookahead_leakage",
            "description": ("All leakage controls hold: trailing-only features, "
                            "point-in-time universe, train-only residualization fit, "
                            "and the horizon-length embargo between train and "
                            "validation labels."),
            "threshold": "all leakage_controls satisfied",
            "pass_condition": "every leakage control verified for every fold",
        },
        {
            "id": "enough_effective_observations_after_embargo",
            "description": ("Enough effectively independent validation observations "
                            "remain after the embargo (validation dates / horizon)."),
            "threshold": MIN_EFFECTIVE_OBS,
            "pass_condition": "effective_validation_obs >= min_effective_obs",
        },
    ]


def build_leakage_controls(candidate: Dict[str, Any]) -> Dict[str, Any]:
    primary = candidate["primary_horizon_days"]
    return {
        "trailing_only_features": (
            "Every feature, including the candidate signal and the residualization "
            "controls, is a trailing point-in-time value as of the row date; none "
            "uses forward data."),
        "point_in_time_universe": (
            "Names are included only if they were members of the universe as of each "
            "date; no survivorship or forward-membership injection."),
        "train_only_residualization_fit": (
            "The residualization is fit only from training / same-date cross-section "
            "information; no validation-window information enters any fit. The label "
            "is strictly forward; the per-date fit mixes only same-date data."),
        "embargo_between_train_and_validation": {
            "embargo_sessions": primary,
            "reason": (
                "The " + str(primary) + "d forward labels overlap, so an embargo "
                "equal to the horizon is dropped between the last training label and "
                "the first validation label to prevent boundary leakage."),
        },
        "no_current_date_membership_leakage_if_universe_expanded": (
            "If the universe is later broadened, membership must still be resolved "
            "point-in-time per date; today's membership must not be back-applied to "
            "historical dates."),
        "no_validation_set_threshold_tuning": (
            "Gate thresholds are fixed in advance (inherited from Phase 2I-A / 2K-B / "
            "2K-C) and are never tuned on validation results."),
    }


def build_implementation_plan(candidate: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "next_phase": "2L-B",
        "trains_a_model": False,
        "steps": [
            "Reconstruct the Phase 2K-B/2K-C residual label exactly (import the "
            "Phase 2K-B analyzer; no relabeling).",
            "Derive the concrete sequential walk-forward fold boundaries from the "
            "session calendar, applying the horizon-length embargo.",
            "For each fold, compute the model-free rank-only metrics on the "
            "validation window using the training-window direction.",
            "Aggregate per-fold metrics; run the moving-block bootstrap and the "
            "leave-one-window-out check on the pooled validation IC.",
            "Evaluate every pass/fail gate and record an explicit PASS/FAIL per gate.",
            "Emit a Phase 2L-B diagnostics JSON with the same safety flags; train, "
            "fit, score, and serve nothing.",
        ],
        "candidate_signal": candidate["feature"],
        "note": (
            "Phase 2L-B implements the model-free walk-forward validation only. It "
            "creates no model candidate and claims no production edge."),
    }


def build_recommended_next_phase(upstream: Dict[str, Any]) -> Dict[str, Any]:
    counts = upstream.get("counts", {})
    any_model = int(counts.get(REC_MODEL, 0)) >= 1
    if any_model:
        return {
            "phase": "2L-B",
            "title": "Model-Free Walk-Forward Residual Signal Validation",
            "purpose": (
                "Phase 2K-C produced at least one KEEP_FOR_MODEL_RESEARCH candidate, "
                "so the next phase implements the model-free walk-forward validation "
                "designed here -- a rank-only signal check on out-of-sample, "
                "chronologically-ordered validation windows with a horizon-length "
                "embargo. Phase 2L-B trains no model, fits nothing, serves nothing, "
                "and creates no model candidate; it only decides whether the "
                "surviving residual signal holds out of sample. No production edge is "
                "claimed."),
        }
    return {
        "phase": "2K-D",
        "title": "Extend Data / Acquire Alpha for New Hypothesis Families",
        "purpose": (
            "No Phase 2K-C model-research candidate exists, so there is nothing to "
            "validate walk-forward. Acquire longer history and a broader point-in-"
            "time universe, or move to new hypothesis families. No model candidate "
            "is created."),
    }


def build_interpretation(candidate: Dict[str, Any],
                         upstream: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "candidate_is_research_only": True,
        "candidate_is_production_edge": False,
        "authorized_to_train_model": False,
        "authorized_to_serve_model": False,
        "model_candidate_created": False,
        "narrative": (
            "Phase 2K-C labelled " + str(candidate["feature"]) + " "
            "KEEP_FOR_MODEL_RESEARCH at its " + str(candidate["primary_horizon_days"])
            + "d horizon -- it survived the overlap-aware bootstrap, the permutation "
            "null, leave-one-year-out, concentration, and regime checks on a single-"
            "regime ~40-name 2023-2026 export. That is enough to justify *designing* "
            "out-of-sample walk-forward validation, and nothing more. This phase "
            "writes the protocol, gates, and leakage controls; it builds no model, "
            "fits nothing, and claims no production edge. The model-free baseline "
            "must be validated first in Phase 2L-B; a simple single-feature model "
            "remains a separately gated future option, not an action authorized "
            "here."),
        "production_edge_claimed": False,
    }


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_design() -> Dict[str, Any]:
    upstream = load_upstream()
    candidate = select_candidate(upstream)

    walk_forward_design = build_walk_forward_design(candidate, upstream)
    model_free_baseline_design = build_model_free_baseline_design(candidate)
    later_model_candidate_design = build_later_model_candidate_design()
    pass_fail_gates = build_pass_fail_gates(candidate)
    leakage_controls = build_leakage_controls(candidate)
    implementation_plan = build_implementation_plan(candidate)
    recommended_next_phase = build_recommended_next_phase(upstream)
    interpretation = build_interpretation(candidate, upstream)

    rs = upstream.get("run_summary", {})
    twoj = upstream.get("phase2j", {})

    return {
        "phase": PHASE,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "phase2k_c_json": INPUT_2KC_JSON,
            "phase2k_b_json": INPUT_2KB_JSON,
            "phase2k_a_json": INPUT_2KA_JSON,
            "phase2j_json": INPUT_2J_JSON,
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
            "comparison_horizons_days": COMPARISON_HORIZONS_DAYS,
            "min_train_sessions": MIN_TRAIN_SESSIONS,
            "rolling_train_sessions": ROLLING_TRAIN_SESSIONS,
            "validation_sessions": VALIDATION_SESSIONS,
            "step_sessions": STEP_SESSIONS,
            "rank_ic_floor": RANK_IC_FLOOR,
            "majority_sign_fraction": MAJORITY_SIGN_FRACTION,
            "degradation_tolerance_fraction": DEGRADATION_TOLERANCE_FRACTION,
            "concentration_top3_threshold": CONCENTRATION_TOP3_THRESHOLD,
            "min_effective_obs": MIN_EFFECTIVE_OBS,
        },
        "upstream_summary": {
            "phase2k_c_recommended_next_phase": upstream[
                "phase2k_c_recommended_next_phase"],
            "recommendation_counts": upstream["counts"],
            "keep_for_model_research": upstream["survivors_keep_for_model_research"],
            "keep_as_research_lead_only": upstream["research_leads_only"],
            "dropped": upstream["dropped"],
            "need_more_data": upstream["need_more_data"],
            "panel": upstream["panel"],
            "provenance": {
                "source": rs.get("source"),
                "date_start": rs.get("date_start"),
                "date_end": rs.get("date_end"),
                "price_row_count": rs.get("row_count"),
                "ticker_count": rs.get("ticker_count"),
                "phase2j_phase": twoj.get("phase"),
                "phase2j_go_no_go": twoj.get("decision", {}).get("go_no_go"),
            },
        },
        "candidate_selected": candidate,
        "walk_forward_design": walk_forward_design,
        "model_free_baseline_design": model_free_baseline_design,
        "later_model_candidate_design": later_model_candidate_design,
        "pass_fail_gates": pass_fail_gates,
        "leakage_controls": leakage_controls,
        "implementation_plan": implementation_plan,
        "interpretation": interpretation,
        "recommended_next_phase": recommended_next_phase,
    }


def write_design(design: Dict[str, Any], path: str = DESIGN_JSON) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(design, f, indent=2, allow_nan=False)


def run(output_path: str = DESIGN_JSON) -> Dict[str, Any]:
    design = build_design()
    write_design(design, output_path)
    return design


def main() -> int:
    t0 = time.perf_counter()
    d = run()
    elapsed = time.perf_counter() - t0
    cs = d["candidate_selected"]
    nxt = d["recommended_next_phase"]
    print(f"[phase2l-a] candidate selected            : {cs['feature']} "
          f"(h={cs['primary_horizon_days']}d, {cs['direction']})")
    print(f"[phase2l-a] sole model-research candidate : "
          f"{cs['is_sole_model_research_candidate']}")
    print(f"[phase2l-a] upstream counts               : "
          f"{d['upstream_summary']['recommendation_counts']}")
    print(f"[phase2l-a] walk-forward horizons         : primary "
          f"{d['walk_forward_design']['primary_horizon_days']}d, comparison "
          f"{d['walk_forward_design']['comparison_horizons_days']}")
    print(f"[phase2l-a] pass/fail gates               : {len(d['pass_fail_gates'])}")
    print(f"[phase2l-a] model-free baseline first     : "
          f"{d['model_free_baseline_design']['run_first']}")
    print(f"[phase2l-a] later model authorized now    : "
          f"{d['later_model_candidate_design']['authorized_now']}")
    print(f"[phase2l-a] recommended next phase        : {nxt['phase']} "
          f"({nxt['title']})")
    print(f"[phase2l-a] design written                : {DESIGN_JSON}")
    print(f"[phase2l-a] elapsed seconds               : {elapsed:.3f}")
    print("[phase2l-a] design-only artifact; no model trained; safety flags in JSON.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
