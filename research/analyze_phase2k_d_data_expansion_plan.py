"""Phase 2K-D Data Expansion / Universe Broadening Plan (offline, read-only).

Phase 2L-B ran the Phase 2L-A model-free, rank-only walk-forward validation for the
sole Phase 2K-C survivor -- avg_dollar_volume_21d, at its 63-session horizon. On the
current ~40-name, single-regime 2023-2026 export the validation did NOT pass: it
either failed the out-of-sample gates (WALK_FORWARD_FAIL) or there were too few
effectively independent observations after the horizon-length embargo
(NEED_MORE_DATA). Phase 2L-B trained nothing and created no model candidate; it routed
here.

This phase produces a disciplined DATA-EXPANSION PLAN, not a model and not a backtest.
It reads the upstream Phase 2L-B / 2L-A / 2K-C / 2K-A JSON summaries plus the Phase 2G
run summary, explains exactly why the surviving residual signal cannot advance to model
research on the current data, and defines the data requirements, minimum viable
expanded dataset, target validation capacity, data-quality / leakage gates, and the
acquisition options needed before any further model research. It writes one planning
JSON.

It does NOT continue model research, does NOT create a model candidate, fits nothing,
serves nothing, touches no datastore, and performs no infrastructure action. The
machine-readable safety flags are emitted in the JSON; the full guardrail rationale
lives in docs/phase2k_d_data_expansion_plan_v1.md, not in this source.

If Phase 2L-B had instead PASSED, this 2K-D artifact is not the right next step --
Phase 2L-C (simple residual signal model candidate design) would be -- so the analyzer
refuses to write a data-expansion plan in that case and says so.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import time
from typing import Any, Dict, List, Optional

PHASE = "2K-D"

_HERE = os.path.dirname(os.path.abspath(__file__))
_OUTPUT_DIR = os.path.join("research", "output")

# Inputs (read-only).
INPUT_2LB_JSON = os.path.join(_OUTPUT_DIR, "phase2l_b_walk_forward_validation.json")
INPUT_2LA_JSON = os.path.join(_OUTPUT_DIR, "phase2l_a_walk_forward_design.json")
INPUT_2KC_JSON = os.path.join(_OUTPUT_DIR, "phase2k_c_residual_robustness.json")
INPUT_2KA_JSON = os.path.join(_OUTPUT_DIR, "phase2k_alpha_backlog.json")
RUN_SUMMARY_JSON = os.path.join(_OUTPUT_DIR, "phase2g_c_real_data_run_summary.json")

# The single output this analyzer is allowed to write.
RESULTS_JSON = os.path.join(_OUTPUT_DIR, "phase2k_d_data_expansion_plan.json")

# Upstream walk-forward verdict vocabulary (must match Phase 2L-B exactly).
WF_PASS = "WALK_FORWARD_PASS"
WF_FAIL = "WALK_FORWARD_FAIL"
WF_NEED = "NEED_MORE_DATA"

# Planning targets. ~252 trading sessions per calendar year. The hard floor on
# effective validation observations is inherited from the upstream gate; the broader
# regime / universe targets below are the disciplined recommendation, not a gate.
SESSIONS_PER_YEAR = 252
MIN_YEARS_TARGET = 8                # at least one full market cycle incl. a drawdown
RECOMMENDED_YEARS_TARGET = 10
MIN_TICKERS_PER_DATE_TARGET = 100   # materially broader than the current ~40 names
RECOMMENDED_FOLD_BUFFER = 2.0       # recommend ~2x the effective-obs floor for margin


# --------------------------------------------------------------------------- #
# Read-only helpers
# --------------------------------------------------------------------------- #
def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _years_between(start: Optional[str], end: Optional[str]) -> Optional[float]:
    if not start or not end:
        return None
    try:
        d0 = dt.date.fromisoformat(str(start)[:10])
        d1 = dt.date.fromisoformat(str(end)[:10])
    except ValueError:
        return None
    return round((d1 - d0).days / 365.25, 2)


def _round(x: Any, n: int = 6) -> Optional[float]:
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(xf) or math.isinf(xf):
        return None
    return round(xf, n)


# --------------------------------------------------------------------------- #
# Upstream extraction
# --------------------------------------------------------------------------- #
def load_upstream() -> Dict[str, Any]:
    """Read the upstream summaries and extract the fields Phase 2K-D reasons over."""
    twolb = _read_json(INPUT_2LB_JSON)
    twola = _read_json(INPUT_2LA_JSON)
    twokc = _read_json(INPUT_2KC_JSON)
    twoka = _read_json(INPUT_2KA_JSON)
    run_summary = _read_json(RUN_SUMMARY_JSON)

    rec_block = twolb.get("recommendation", {}) or {}
    wf = twolb.get("walk_forward_config", {}) or {}
    agg = twolb.get("aggregate_metrics", {}) or {}
    gates = twolb.get("pass_fail_gates", []) or []
    cand = twolb.get("candidate", {}) or {}
    folds = twolb.get("folds", []) or []
    thresholds = wf.get("thresholds", {}) or {}

    failed_gates = [g["id"] for g in gates if g.get("passed") is False]

    horizon = int(cand.get("primary_horizon_days")
                  or wf.get("primary_horizon_days") or 63)
    embargo = int(wf.get("embargo_sessions") or horizon)
    min_train = int(thresholds.get("min_train_sessions")
                    or wf.get("min_train_sessions") or 252)
    validation = int(thresholds.get("validation_sessions")
                     or wf.get("validation_sessions") or 63)
    min_eff = float(thresholds.get("min_effective_obs")
                    or twola.get("config", {}).get("min_effective_obs") or 8.0)

    n_calendar = wf.get("n_calendar_sessions")
    panel = twolb.get("panel", {}) or twokc.get("panel", {}) or {}

    return {
        "twolb": twolb,
        "twola": twola,
        "twokc": twokc,
        "twoka": twoka,
        "run_summary": run_summary,
        "recommendation": rec_block.get("recommendation"),
        "recommendation_reason": rec_block.get("reason"),
        "recommended_next_phase_2lb": (twolb.get("recommended_next_phase", {}) or {})
        .get("phase"),
        "candidate": cand.get("feature"),
        "candidate_horizon_days": horizon,
        "embargo_sessions": embargo,
        "min_train_sessions": min_train,
        "validation_sessions": validation,
        "min_effective_obs": min_eff,
        "n_folds": agg.get("n_folds") if agg.get("n_folds") is not None else len(folds),
        "effective_validation_obs": agg.get("total_effective_validation_obs"),
        "pooled_validation_mean_ic": agg.get("pooled_validation_mean_residual_rank_ic"),
        "frac_windows_same_sign": agg.get("frac_validation_windows_same_sign"),
        "gates": gates,
        "failed_gates": failed_gates,
        "n_calendar_sessions": n_calendar,
        "calendar_start": wf.get("calendar_start"),
        "calendar_end": wf.get("calendar_end"),
        "panel_n_tickers": panel.get("n_tickers"),
        "panel_n_dates": panel.get("n_dates"),
        "panel_date_start": panel.get("date_start"),
        "panel_date_end": panel.get("date_end"),
    }


# --------------------------------------------------------------------------- #
# Plan sections
# --------------------------------------------------------------------------- #
def build_upstream_summary(u: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "phase": "2L-B",
        "recommendation": u["recommendation"],
        "reason": u["recommendation_reason"],
        "recommended_next_phase": u["recommended_next_phase_2lb"],
        "candidate": u["candidate"],
        "primary_horizon_days": u["candidate_horizon_days"],
        "embargo_sessions": u["embargo_sessions"],
        "walk_forward_config": {
            "min_train_sessions": u["min_train_sessions"],
            "validation_sessions": u["validation_sessions"],
            "embargo_sessions": u["embargo_sessions"],
            "n_calendar_sessions": u["n_calendar_sessions"],
            "calendar_start": u["calendar_start"],
            "calendar_end": u["calendar_end"],
            "min_effective_obs": u["min_effective_obs"],
        },
        "aggregate_metrics": {
            "n_folds": u["n_folds"],
            "total_effective_validation_obs": u["effective_validation_obs"],
            "pooled_validation_mean_residual_rank_ic":
                _round(u["pooled_validation_mean_ic"]),
            "frac_validation_windows_same_sign": _round(u["frac_windows_same_sign"]),
        },
        "pass_fail_gates": [
            {"id": g.get("id"), "passed": bool(g.get("passed"))} for g in u["gates"]
        ],
        "failed_gates": u["failed_gates"],
        "n_folds": u["n_folds"],
        "effective_validation_obs": u["effective_validation_obs"],
    }


def build_decision(u: Dict[str, Any]) -> Dict[str, Any]:
    """Why no model research and no model candidate yet, derived from the 2L-B result."""
    rec = u["recommendation"]
    cand = u["candidate"]
    years = (_years_between(u["calendar_start"], u["calendar_end"])
             or _years_between(u["panel_date_start"], u["panel_date_end"]))
    n_names = u["panel_n_tickers"]
    embargo = u["embargo_sessions"]
    eff = u["effective_validation_obs"]
    min_eff = u["min_effective_obs"]
    n_folds = u["n_folds"]

    if rec == WF_NEED:
        reason = (
            f"Phase 2L-B returned NEED_MORE_DATA for {cand}: only {n_folds} "
            f"non-overlapping walk-forward folds / {eff} effective validation "
            f"observations remained after the {embargo}-session embargo, below the "
            f"required {min_eff}. A ~{n_names}-name, ~{years}-year single-regime panel "
            f"cannot support a conclusive out-of-sample test, so no model research and "
            f"no model candidate are authorized; the disciplined next step is to "
            f"acquire longer history and a broader point-in-time universe and retest."
        )
    elif rec == WF_FAIL:
        reason = (
            f"Phase 2L-B returned WALK_FORWARD_FAIL for {cand}: enough folds existed "
            f"but the signal failed these out-of-sample gates: "
            f"{', '.join(u['failed_gates']) or 'one or more gates'}. The model-free "
            f"baseline did not hold up walk-forward on the current ~{n_names}-name, "
            f"~{years}-year single-regime panel, so no model is built and no model "
            f"candidate is created; the disciplined next step is to broaden the data "
            f"and retest, not to fit a model to a thin, single-regime sample."
        )
    else:
        # Defensive: this branch should be unreachable because run() refuses to emit a
        # 2K-D plan when the upstream verdict is a pass.
        reason = (
            f"Phase 2L-B recommendation was {rec!r}; a data-expansion plan is only the "
            f"correct next step after WALK_FORWARD_FAIL or NEED_MORE_DATA."
        )

    return {
        "continue_model_research_now": False,
        "create_model_candidate_now": False,
        "reason": reason,
        "upstream_recommendation": rec,
        "decision_basis": rec,
    }


def build_data_gap_analysis(u: Dict[str, Any]) -> Dict[str, Any]:
    rec = u["recommendation"]
    years = (_years_between(u["calendar_start"], u["calendar_end"])
             or _years_between(u["panel_date_start"], u["panel_date_end"]))
    gap: Dict[str, Any] = {
        "decision_basis": rec,
        "candidate": u["candidate"],
        "current_panel": {
            "n_tickers": u["panel_n_tickers"],
            "n_dates": u["panel_n_dates"],
            "approx_years": years,
            "labeled_calendar_sessions": u["n_calendar_sessions"],
            "calendar_start": u["calendar_start"],
            "calendar_end": u["calendar_end"],
            "single_regime": True,
            "single_regime_note": (
                "The 2023-2026 export is essentially one extended bull regime; it lacks "
                "a sustained drawdown, so a 63-session signal cannot be tested out of a "
                "single regime."
            ),
        },
    }
    if rec == WF_NEED:
        eff = u["effective_validation_obs"]
        min_eff = u["min_effective_obs"]
        shortfall = None
        if isinstance(eff, (int, float)) and isinstance(min_eff, (int, float)):
            shortfall = _round(max(0.0, float(min_eff) - float(eff)), 2)
        gap["why_cannot_advance"] = (
            "NEED_MORE_DATA: after the horizon-length embargo the labeled calendar "
            "yields too few effectively independent validation observations to draw a "
            "conclusion. The panel is both too short (only one regime) and too narrow "
            "to support model research."
        )
        gap["insufficient_folds_or_observations"] = {
            "n_folds": u["n_folds"],
            "effective_validation_obs": eff,
            "min_effective_obs_required": min_eff,
            "effective_obs_shortfall": shortfall,
            "embargo_sessions": u["embargo_sessions"],
            "explanation": (
                "Each non-overlapping validation block contributes ~validation_dates / "
                "horizon effective observations; with validation == horizon that is ~1 "
                "per fold, so clearing the floor requires materially more labeled "
                "history after the embargo than the current export provides."
            ),
        }
    elif rec == WF_FAIL:
        gate_lookup = {g.get("id"): g for g in u["gates"]}
        failed_detail = []
        for gid in u["failed_gates"]:
            g = gate_lookup.get(gid, {})
            failed_detail.append({
                "id": gid,
                "observed": g.get("observed"),
                "threshold": g.get("threshold"),
                "interpretation": _gate_interpretation(gid),
            })
        gap["why_cannot_advance"] = (
            "WALK_FORWARD_FAIL: enough folds existed, but the model-free signal failed "
            "one or more out-of-sample gates. The candidate cannot advance to model "
            "research because the raw signal did not survive genuine, chronologically "
            "ordered validation on the current single-regime data."
        )
        gap["failed_gates_detail"] = failed_detail
    return gap


def _gate_interpretation(gate_id: str) -> str:
    return {
        "validation_mean_ic_above_floor":
            "Out-of-sample mean residual rank IC did not clear the inherited IC floor.",
        "majority_validation_windows_same_sign":
            "Fewer than a majority of validation windows shared the training-window "
            "sign -- the signal flipped too often out-of-sample.",
        "bootstrap_ci_excludes_zero_in_validation":
            "The moving-block bootstrap CI of the pooled validation IC included zero -- "
            "the out-of-sample edge is not distinguishable from noise.",
        "no_single_validation_period_drives_result":
            "Leaving one validation window out broke the sign / above-floor magnitude -- "
            "a single window carried the result.",
        "no_excessive_ticker_concentration":
            "The long leg was too concentrated in a few names.",
        "no_regime_only_dependence":
            "The validation IC flipped sign across the market-direction regime split.",
        "no_degradation_vs_phase2k_c":
            "Out-of-sample validation IC collapsed versus the Phase 2K-C full-sample IC.",
        "no_lookahead_leakage":
            "A leakage control was not satisfied for every fold.",
        "enough_effective_observations_after_embargo":
            "Too few effectively independent validation observations remained after the "
            "embargo to draw a conclusion.",
    }.get(gate_id, "Gate failed out-of-sample.")


def build_expanded_dataset_requirements(u: Dict[str, Any]) -> Dict[str, Any]:
    """Data needed before retesting. Items not required to retest the price/volume
    liquidity candidate (avg_dollar_volume_21d) are marked future-phase."""
    cand = u["candidate"]
    return {
        "candidate_being_retested": cand,
        "note": (
            f"{cand} is a price/volume liquidity feature, so fundamentals and analyst "
            f"estimates are NOT required to retest it -- they are marked as future-phase "
            f"data that only later, different hypotheses would need."
        ),
        "requirements": {
            "longer_price_history": {
                "description": "Daily adjusted OHLCV reaching well before 2023, ideally "
                               "a full market cycle including a sustained drawdown.",
                "required_for_retesting_candidate": True,
                "must_be_point_in_time": False,
                "leakage_risk": "Corporate-action adjustment must use only data "
                                "available as of each date; no future split/dividend "
                                "adjustment applied to past prices.",
                "priority": "high",
            },
            "broader_equity_universe": {
                "description": "Materially more than the current ~40 mega-caps (toward "
                               "the full S&P 500 / a broad liquid universe) to widen each "
                               "cross-section and reduce long-leg ticker concentration.",
                "required_for_retesting_candidate": True,
                "must_be_point_in_time": True,
                "leakage_risk": "Using today's index membership for history is "
                                "survivorship bias; membership must be resolved as-of-date.",
                "priority": "high",
            },
            "point_in_time_universe_membership": {
                "description": "As-of-date index / universe constituent history so each "
                               "date's cross-section uses only names that were members "
                               "then.",
                "required_for_retesting_candidate": True,
                "must_be_point_in_time": True,
                "leakage_risk": "Back-applying current membership injects look-ahead and "
                                "survivorship bias.",
                "priority": "high",
            },
            "survivorship_bias_controls": {
                "description": "Coverage of delisted / merged / renamed names over the "
                               "history so the universe is not silently restricted to "
                               "today's survivors.",
                "required_for_retesting_candidate": True,
                "must_be_point_in_time": True,
                "leakage_risk": "Dropping delisted names inflates measured returns and "
                                "any signal computed on the survivors.",
                "priority": "high",
            },
            "liquidity_and_volume_history": {
                "description": "Daily volume / dollar-volume history -- the direct input "
                               "to avg_dollar_volume_21d -- across the longer span and "
                               "broader universe.",
                "required_for_retesting_candidate": True,
                "must_be_point_in_time": False,
                "leakage_risk": "Features must stay trailing-only; volume spikes around "
                                "events leak if not lagged.",
                "priority": "high",
                "mostly_available_now": True,
            },
            "benchmark_and_factor_data": {
                "description": "Benchmark (SPY) and trailing risk series over the longer "
                               "span for the beta / vol residualization controls.",
                "required_for_retesting_candidate": True,
                "must_be_point_in_time": False,
                "leakage_risk": "Betas / vols must be trailing-only.",
                "priority": "medium",
                "mostly_available_now": True,
            },
            "sector_industry_classification": {
                "description": "Point-in-time sector / industry tags to support "
                               "neutralization and concentration control on the broader "
                               "universe.",
                "required_for_retesting_candidate": False,
                "must_be_point_in_time": True,
                "leakage_risk": "Applying current classification to history misclassifies "
                                "reclassified names.",
                "priority": "medium",
                "future_phase": False,
            },
            "point_in_time_fundamentals": {
                "description": "As-first-reported fundamentals with correct report-date "
                               "lags.",
                "required_for_retesting_candidate": False,
                "must_be_point_in_time": True,
                "leakage_risk": "Restated figures and report-date misalignment are the "
                                "dominant factor-research leakage source.",
                "priority": "low",
                "future_phase": True,
                "future_phase_note": "Only needed by later fundamental / quality-value "
                                     "hypotheses, not by the liquidity candidate.",
            },
            "analyst_estimates_and_surprise": {
                "description": "Point-in-time consensus estimates, revision timestamps, "
                               "actual earnings dates and surprise.",
                "required_for_retesting_candidate": False,
                "must_be_point_in_time": True,
                "leakage_risk": "Using final/current consensus for a past date imports "
                                "the future; earnings dates must be actual.",
                "priority": "low",
                "future_phase": True,
                "future_phase_note": "Only needed by later revision / drift hypotheses.",
            },
        },
    }


def build_minimum_viable_dataset(u: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "daily_adjusted_ohlcv_years_min": MIN_YEARS_TARGET,
        "daily_adjusted_ohlcv_years_recommended": RECOMMENDED_YEARS_TARGET,
        "universe_breadth_min_tickers_per_date": MIN_TICKERS_PER_DATE_TARGET,
        "universe_breadth_note": "Materially broader than the current ~40 names; the "
                                 "full liquid S&P 500 is the ideal target.",
        "point_in_time_membership_required": True,
        "survivorship_caveat_if_not_point_in_time": (
            "If true point-in-time membership cannot be sourced, the dataset must carry "
            "an explicit survivorship caveat and the retest must report results as "
            "survivorship-biased rather than treating them as clean."
        ),
        "enough_walk_forward_folds_after_embargo": True,
        "regimes_required": [
            "market_up",
            "market_down",
            "high_volatility",
            "low_volatility",
            "at_least_one_sustained_drawdown",
        ],
    }


def build_target_validation_capacity(u: Dict[str, Any]) -> Dict[str, Any]:
    horizon = u["candidate_horizon_days"]
    embargo = u["embargo_sessions"]
    min_train = u["min_train_sessions"]
    validation = u["validation_sessions"]
    min_eff = u["min_effective_obs"]

    # Each non-overlapping validation block ~= validation/horizon effective obs.
    eff_per_fold = (validation / horizon) if horizon else 1.0
    floor_folds = int(math.ceil(min_eff / eff_per_fold)) if eff_per_fold else None
    recommended_folds = (int(math.ceil(floor_folds * RECOMMENDED_FOLD_BUFFER))
                         if floor_folds else None)

    def _raw_sessions(folds: Optional[int]) -> Optional[int]:
        if folds is None:
            return None
        # min_train + embargo + folds * validation labeled sessions, plus a horizon
        # tail of raw sessions whose forward labels run off the end of the data.
        labeled = min_train + embargo + folds * validation
        return labeled + horizon

    def _years(raw: Optional[int]) -> Optional[float]:
        return round(raw / SESSIONS_PER_YEAR, 2) if raw else None

    floor_raw = _raw_sessions(floor_folds)
    rec_raw = _raw_sessions(recommended_folds)

    return {
        "min_nonoverlapping_validation_folds": floor_folds,
        "recommended_validation_folds": recommended_folds,
        "min_effective_validation_obs": min_eff,
        "recommended_effective_validation_obs": (
            _round(recommended_folds * eff_per_fold, 2) if recommended_folds else None),
        "effective_obs_per_fold": _round(eff_per_fold, 4),
        "min_tickers_per_date": MIN_TICKERS_PER_DATE_TARGET,
        "min_years_history": MIN_YEARS_TARGET,
        "recommended_years_history": RECOMMENDED_YEARS_TARGET,
        "raw_sessions_for_floor": floor_raw,
        "approx_years_for_floor": _years(floor_raw),
        "raw_sessions_for_recommended": rec_raw,
        "approx_years_for_recommended": _years(rec_raw),
        "must_include_at_least_one_stressed_period": True,
        "arithmetic_vs_regime_note": (
            "The session arithmetic above is only the minimum to clear the "
            "effective-observation gate; the binding constraint is REGIME COVERAGE. The "
            "8-10 year target exists so the walk-forward spans more than one market "
            "regime, not merely to accumulate enough non-overlapping blocks."
        ),
        "current_vs_target": {
            "current_n_tickers": u["panel_n_tickers"],
            "current_effective_validation_obs": u["effective_validation_obs"],
            "current_n_folds": u["n_folds"],
        },
    }


def build_data_quality_gates() -> List[Dict[str, str]]:
    return [
        {"id": "adjusted_close_consistency",
         "description": "Adjusted closes are internally consistent across the longer "
                        "span; no discontinuities at the join between the old and the "
                        "extended history.",
         "check": "Reconcile adjusted vs raw closes and verify total-return continuity."},
        {"id": "split_dividend_adjustment_sanity",
         "description": "Split and dividend adjustments are correct and applied with no "
                        "look-ahead.",
         "check": "Spot-check known splits/dividends; confirm adjustments use only "
                  "as-of-date information."},
        {"id": "missing_date_handling",
         "description": "Trading-calendar gaps are handled explicitly; no silent "
                        "forward-fill of prices across non-trading days.",
         "check": "Validate each name's date index against the exchange calendar."},
        {"id": "ticker_history_continuity",
         "description": "Each ticker's history is continuous over its membership span "
                        "with no unexplained gaps.",
         "check": "Flag names with interior gaps exceeding a tolerance."},
        {"id": "delisting_survivorship_treatment",
         "description": "Delisted / merged names are retained over their live span so the "
                        "universe is not restricted to survivors.",
         "check": "Confirm delisted names appear up to their delist date and not after."},
        {"id": "duplicate_row_detection",
         "description": "No duplicate (date, ticker) rows.",
         "check": "Assert uniqueness on the (date, ticker) key."},
        {"id": "no_forward_filled_target_labels",
         "description": "Forward labels are never forward-filled or imputed; a name with "
                        "no genuine forward window is dropped, not filled.",
         "check": "Verify labels are computed only from realized future prices."},
        {"id": "no_lookahead_universe_membership",
         "description": "Universe membership is resolved point-in-time; today's "
                        "membership is never back-applied to historical dates.",
         "check": "Confirm each date's cross-section uses as-of-date membership only."},
    ]


def build_implementation_options(u: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {
            "option": "extend_current_free_local_data",
            "data_scope": "Extend the existing free end-of-day export backward "
                          "(more years) and modestly widen the ticker list.",
            "cost": "free",
            "point_in_time_membership": False,
            "pros": ["No new spend; fastest to attempt; reuses the existing pipeline."],
            "risks": ["No point-in-time membership (survivorship bias); free history "
                      "depth and delisted-name coverage are limited and may not reach a "
                      "full market cycle."],
            "recommended_first": True,
        },
        {
            "option": "broader_free_low_cost_sources",
            "data_scope": "Investigate broader free / low-cost end-of-day price and "
                          "volume archives and a low-cost point-in-time constituent "
                          "history.",
            "cost": "low",
            "point_in_time_membership": "partial",
            "pros": ["Wider universe and longer depth than the current export; "
                     "low spend."],
            "risks": ["Point-in-time constituent lists remain the scarce piece; "
                      "coverage and adjustment quality must be validated against the "
                      "data-quality gates before use."],
            "recommended_first": False,
        },
        {
            "option": "paid_point_in_time_dataset",
            "data_scope": "A paid point-in-time universe + price (+ optionally "
                          "fundamentals) dataset with delisted-name coverage.",
            "cost": "paid",
            "point_in_time_membership": True,
            "pros": ["Clean point-in-time membership and survivorship handling; the "
                     "soundest basis for out-of-sample research."],
            "risks": ["Cost and procurement lead time; decision deferred until the free "
                      "/ low-cost feasibility check (Phase 2K-E) is done."],
            "recommended_first": False,
            "decision": "later",
        },
    ]


def build_research_retest_plan(u: Dict[str, Any]) -> Dict[str, Any]:
    cand = u["candidate"]
    return {
        "preconditions": [
            "Expanded dataset assembled and passing every data_quality_gate.",
            "Universe resolved point-in-time (or carrying an explicit survivorship "
            "caveat).",
            "Coverage spans more than one market regime, including a sustained drawdown.",
        ],
        "steps": [
            f"Re-run the Phase 2K-B beta/vol-neutralized residual rank-IC test for "
            f"{cand} on the expanded multi-regime panel.",
            "Re-run the Phase 2K-C robustness battery (overlap-aware bootstrap, "
            "permutation null, leave-one-year-out, concentration, regime splits) on the "
            "expanded data.",
            "Re-run the Phase 2L-A walk-forward design / Phase 2L-B model-free "
            "walk-forward validation, which now has enough folds and regimes to be "
            "conclusive.",
            "Only if the model-free baseline passes walk-forward on multi-regime data may "
            "a later, separately gated phase design a simple single-feature candidate "
            "under the model-candidate gate.",
        ],
        "still_no_model_until": (
            "A feature family passes the IC sweep and the out-of-sample robustness / "
            "walk-forward battery on real data spanning more than one market regime, "
            "with no single-regime artifact, no excessive ticker concentration, and no "
            "look-ahead leakage. The model-v2 serving flag stays disabled until then."
        ),
        "creates_model_candidate": False,
    }


def build_recommended_next_phase() -> Dict[str, str]:
    return {
        "phase": "2K-E",
        "title": "Expanded Historical Universe Data Feasibility Check",
        "purpose": (
            "Before retesting any residual signal, verify whether the required expanded "
            "dataset -- 8-10 years of daily adjusted OHLCV across a materially broader, "
            "point-in-time universe with survivorship coverage -- can actually be "
            "assembled locally / free / low-cost. The feasibility check scopes source "
            "depth, universe breadth, point-in-time membership availability, and the "
            "data-quality gates, and decides whether a paid point-in-time dataset is "
            "warranted. It acquires no paid data, trains no model, and creates no model "
            "candidate."
        ),
    }


def build_interpretation(u: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "candidate": u["candidate"],
        "upstream_walk_forward_recommendation": u["recommendation"],
        "continue_model_research_now": False,
        "create_model_candidate_now": False,
        "model_trained": False,
        "model_fitted": False,
        "model_candidate_created": False,
        "authorized_to_train_model": False,
        "authorized_to_serve_model": False,
        "production_edge_claimed": False,
        "narrative": (
            f"Phase 2L-B could not advance {u['candidate']} to model research on the "
            f"current ~{u['panel_n_tickers']}-name, single-regime 2023-2026 export "
            f"({u['recommendation']}). Phase 2K-D therefore plans data expansion and "
            f"universe broadening rather than model work: it defines the longer history, "
            f"broader point-in-time universe, survivorship controls, target validation "
            f"capacity, data-quality / leakage gates, and acquisition options needed "
            f"before any retest. No model is trained, fitted, scored, or served, and no "
            f"model candidate is created. No production edge is claimed."
        ),
    }


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_results() -> Dict[str, Any]:
    u = load_upstream()
    return {
        "phase": PHASE,
        "generated_at": dt.datetime.now().replace(microsecond=0).isoformat(),
        "inputs_read": {
            "phase2l_b_json": INPUT_2LB_JSON,
            "phase2l_a_json": INPUT_2LA_JSON,
            "phase2k_c_json": INPUT_2KC_JSON,
            "phase2k_a_json": INPUT_2KA_JSON,
            "run_summary_json": RUN_SUMMARY_JSON,
        },
        "database_touched": False,
        "database_write_executed": False,
        "migration_executed": False,
        "deployment_executed": False,
        "model_v2_enabled": False,
        "production_edge_claimed": False,
        "no_trading": True,
        "no_orders": True,
        "no_automation": True,
        "upstream_phase2l_b_summary": build_upstream_summary(u),
        "decision": build_decision(u),
        "data_gap_analysis": build_data_gap_analysis(u),
        "expanded_dataset_requirements": build_expanded_dataset_requirements(u),
        "minimum_viable_expanded_dataset": build_minimum_viable_dataset(u),
        "target_validation_capacity": build_target_validation_capacity(u),
        "data_quality_gates": build_data_quality_gates(),
        "implementation_options": build_implementation_options(u),
        "research_retest_plan": build_research_retest_plan(u),
        "interpretation": build_interpretation(u),
        "recommended_next_phase": build_recommended_next_phase(),
    }


def write_results(results: Dict[str, Any], path: str = RESULTS_JSON) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, allow_nan=False)


def run(output_path: str = RESULTS_JSON) -> Dict[str, Any]:
    """Build and write the data-expansion plan.

    If the upstream Phase 2L-B verdict was a pass, a data-expansion plan is NOT the
    correct next step (Phase 2L-C is), so no artifact is written and a skip record is
    returned instead.
    """
    u = load_upstream()
    if u["recommendation"] == WF_PASS:
        return {
            "phase": PHASE,
            "skipped": True,
            "reason": (
                "Phase 2L-B returned WALK_FORWARD_PASS, so the correct next phase is "
                "2L-C (Simple Residual Signal Model Candidate Design), not a 2K-D data "
                "expansion plan. No data-expansion artifact was written."
            ),
            "required_next_phase": "2L-C",
        }
    results = build_results()
    write_results(results, output_path)
    return results


def main() -> int:
    t0 = time.perf_counter()
    d = run()
    elapsed = time.perf_counter() - t0
    if d.get("skipped"):
        print(f"[phase2k-d] SKIPPED: {d['reason']}")
        print(f"[phase2k-d] elapsed seconds : {elapsed:.2f}")
        return 0
    up = d["upstream_phase2l_b_summary"]
    cap = d["target_validation_capacity"]
    nxt = d["recommended_next_phase"]
    print(f"[phase2k-d] upstream 2L-B verdict     : {up['recommendation']}")
    print(f"[phase2k-d] candidate                 : {up['candidate']}")
    print(f"[phase2k-d] current folds / eff obs    : {up['n_folds']} / "
          f"{up['effective_validation_obs']}")
    print(f"[phase2k-d] target folds (floor/rec)   : "
          f"{cap['min_nonoverlapping_validation_folds']} / "
          f"{cap['recommended_validation_folds']}")
    print(f"[phase2k-d] target years (floor/rec)   : "
          f"{cap['approx_years_for_floor']} / {cap['approx_years_for_recommended']} "
          f"(regime target {cap['min_years_history']}-{cap['recommended_years_history']}y)")
    print(f"[phase2k-d] continue model research    : "
          f"{d['decision']['continue_model_research_now']}")
    print(f"[phase2k-d] create model candidate     : "
          f"{d['decision']['create_model_candidate_now']}")
    print(f"[phase2k-d] recommended next phase     : {nxt['phase']} ({nxt['title']})")
    print(f"[phase2k-d] results written            : {RESULTS_JSON}")
    print(f"[phase2k-d] elapsed seconds            : {elapsed:.2f}")
    print("[phase2k-d] planning only; no model, no data acquisition; safety flags in JSON.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
