"""Phase 2K-A New Alpha Hypothesis Backlog & Data Requirements analyzer.

Phase 2J-A formalized a NO_GO: Phase 2I-A surfaced six 63d survivors, Phase 2I-B
dropped all six under its out-of-sample robustness battery, and the only
measurable signal was a regime-dependent volatility / beta / correlation risk
premium -- not market-neutral alpha. The disciplined conclusion was: keep the
research platform, reject the current signal, build no model candidate, and leave
model-v2 disabled.

This phase turns that NO_GO into a structured, disciplined next-research roadmap.
It does not train a model, it does not deploy anything, and it asserts no
production edge. It reads three local JSON summaries (the Phase 2J-A decision, the
Phase 2I-B survivor robustness diagnostics, and the Phase 2I-A feature-IC sweep)
and writes exactly one backlog JSON describing:

  * the upstream NO_GO it is built on (confirmed, not re-decided);
  * an alpha-hypothesis backlog of eight hypothesis families, each with its
    thesis, the failed-2I signal it is meant to replace, required data, a
    minimum-viable test, expected horizon / direction, leakage risks, a
    validation gate, availability / complexity / priority, and whether it is
    cheap enough to test now (go_to_test);
  * the data requirements those hypotheses imply, each with point-in-time and
    look-ahead notes, low-cost sources to investigate, and blockers;
  * a ranked research sequence that starts with the cheapest current-data tests
    before any paid-data acquisition;
  * a model-candidate gate whose explicit rule is that no model candidate may be
    created until a feature family passes IC + robustness on real, multi-regime
    data with no leakage; and
  * the recommended next phase (2K-B: the cheapest current-data residual test).

This phase is research planning only. It performs no infrastructure action and
mutates no datastore. Machine-readable safety flags are emitted in the backlog
JSON; the full guardrail rationale lives in
docs/phase2k_alpha_hypothesis_backlog_v1.md.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from typing import Any, Dict, List

PHASE = "2K-A"

_OUTPUT_DIR = os.path.join("research", "output")

# Inputs (read-only): the upstream decision and its evidence base.
INPUT_2J_JSON = os.path.join(_OUTPUT_DIR, "phase2j_research_decision.json")
INPUT_2IB_JSON = os.path.join(_OUTPUT_DIR, "phase2i_b_survivor_robustness.json")
INPUT_2IA_JSON = os.path.join(_OUTPUT_DIR, "phase2i_feature_ic_horizon_sweep.json")

# The single output this analyzer is allowed to write.
BACKLOG_JSON = os.path.join(_OUTPUT_DIR, "phase2k_alpha_backlog.json")


# --------------------------------------------------------------------------- #
# Read-only helpers
# --------------------------------------------------------------------------- #
def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _bool(x: Any) -> bool:
    return bool(x)


# --------------------------------------------------------------------------- #
# Upstream decision summary (confirmed, not re-decided)
# --------------------------------------------------------------------------- #
def _upstream_decision_summary(twoj: Dict[str, Any],
                               twoib: Dict[str, Any]) -> Dict[str, Any]:
    """Summarize the Phase 2J-A decision this backlog is built on.

    The NO_GO is read from the upstream artifact, not re-litigated here.
    no_go_confirmed is derived (go_no_go == NO_GO and both promotion flags
    false) so the backlog stays honest if the upstream artifact ever changes.
    """
    dec = twoj.get("decision", {}) or {}
    ev = twoj.get("evidence_summary", {}) or {}
    interp = twoib.get("interpretation", {}) or {}

    go_no_go = dec.get("go_no_go")
    promote = _bool(dec.get("promote_model_v2"))
    build = _bool(dec.get("build_phase2j_model_candidate"))

    # robust_enough_for_phase2j: prefer the formalized decision artifact, fall
    # back to the Phase 2I-B interpretation that produced it.
    if "robust_enough_for_phase2j" in ev:
        robust_enough = _bool(ev.get("robust_enough_for_phase2j"))
    else:
        robust_enough = _bool(interp.get("robust_enough_for_phase2j"))

    no_go_confirmed = bool(go_no_go == "NO_GO" and not promote and not build)

    return {
        "phase2j_go_no_go": go_no_go,
        "promote_model_v2": promote,
        "build_phase2j_model_candidate": build,
        "robust_enough_for_phase2j": robust_enough,
        "reason": dec.get("reason"),
        "no_go_confirmed": no_go_confirmed,
    }


# --------------------------------------------------------------------------- #
# Alpha hypothesis backlog
# --------------------------------------------------------------------------- #
def _alpha_hypothesis_backlog() -> List[Dict[str, Any]]:
    """Eight hypothesis families that replace the failed 2I risk-premium tilt.

    Families testable on the existing 2G real-data export are marked
    go_to_test=true and ranked first; those needing new (point-in-time
    fundamentals, estimates, earnings, sector) data are go_to_test=false until
    the data requirements below are satisfied.
    """
    return [
        {
            "hypothesis_id": "beta_vol_neutralized_residual_signal",
            "title": "Beta/Vol-Neutralized Residual Ranking Signal",
            "thesis": "After neutralizing forward excess return against beta and "
                      "realized volatility, a residual cross-sectional ranking "
                      "signal may survive that is genuine name-selection alpha "
                      "rather than a risk-premium tilt.",
            "why_it_addresses_failed_2I_signal": "Phase 2I/2J showed the only edge "
                      "was a 63d vol / beta / correlation tilt. Explicitly "
                      "regressing out beta and vol isolates whatever ranking "
                      "information remains after the risk premium is removed.",
            "required_data": ["longer_price_history", "benchmark_and_factor_data"],
            "minimum_viable_test": "On the existing 2G export, compute per-name "
                      "rolling beta and realized vol, residualize forward excess "
                      "return against them per date, then run the 2I-A rank-IC "
                      "sweep on candidate features against the residual label.",
            "expected_horizon": "21d-63d",
            "expected_direction": "unknown_to_be_estimated",
            "leakage_risks": "Beta/vol estimated on the same window as the label "
                      "leaks; betas and vols must be trailing-only and the "
                      "residualization fit per date with no forward data.",
            "validation_gate": "Residual rank-IC clears the |IC| floor and "
                      "survives the 2I-B overlap-aware bootstrap, permutation "
                      "null, and leave-one-year-out sign stability.",
            "data_availability": "available_now",
            "implementation_complexity": "low",
            "priority": "high",
            "go_to_test": True,
        },
        {
            "hypothesis_id": "cross_sectional_mean_reversion",
            "title": "Short-Horizon Residual Mean Reversion",
            "thesis": "Names that are extended versus their beta-neutral peers "
                      "over a short window tend to revert, giving a market-neutral "
                      "reversal ranking signal.",
            "why_it_addresses_failed_2I_signal": "It is explicitly the residual "
                      "(beta-neutral) reversal hypothesis, kept distinct from the "
                      "raw-return momentum hypothesis that already FAILED in 2I-A.",
            "required_data": ["longer_price_history", "benchmark_and_factor_data"],
            "minimum_viable_test": "On the 2G export, rank names by short-horizon "
                      "residual return (excess of beta-predicted), then measure "
                      "rank-IC against forward residual return at 5/10/21d.",
            "expected_horizon": "5d-21d",
            "expected_direction": "negative_reversal",
            "leakage_risks": "Overlapping short windows inflate significance; the "
                      "block bootstrap must use the holding horizon as block "
                      "length. Bid/ask bounce can masquerade as reversal.",
            "validation_gate": "Reversal rank-IC is sign-stable across years and "
                      "clears the 2I-B robustness battery net of plausible costs.",
            "data_availability": "available_now",
            "implementation_complexity": "low",
            "priority": "high",
            "go_to_test": True,
        },
        {
            "hypothesis_id": "fundamental_quality_value",
            "title": "Cross-Sectional Quality / Value",
            "thesis": "Quality (profitability, low accruals, stable margins) and "
                      "value (earnings/cash-flow yield) factors rank forward "
                      "returns through a channel that is not pure market beta.",
            "why_it_addresses_failed_2I_signal": "It introduces a fundamentally "
                      "different return driver than the price-only vol/beta tilt, "
                      "so its edge should not collapse in the same regime split.",
            "required_data": ["point_in_time_fundamentals", "broader_universe"],
            "minimum_viable_test": "Join point-in-time fundamentals to the panel, "
                      "build a small quality/value composite, and run the 2I-A "
                      "rank-IC sweep at 63d-126d.",
            "expected_horizon": "63d-252d",
            "expected_direction": "positive_for_quality_and_value",
            "leakage_risks": "Restated / as-reported financials introduce "
                      "look-ahead; only point-in-time, as-first-reported data with "
                      "correct report-date lags is admissible. Survivorship bias "
                      "if delisted names are dropped.",
            "validation_gate": "Composite rank-IC survives 2I-B robustness on a "
                      "universe broad enough that no handful of names carries it.",
            "data_availability": "needs_acquisition",
            "implementation_complexity": "high",
            "priority": "medium",
            "go_to_test": False,
        },
        {
            "hypothesis_id": "estimate_revisions_and_surprise",
            "title": "Analyst Estimate Revisions & Surprise",
            "thesis": "The direction and breadth of analyst estimate revisions, "
                      "and recent earnings surprise, rank forward returns as an "
                      "information-diffusion signal.",
            "why_it_addresses_failed_2I_signal": "Revision momentum is an "
                      "information-flow signal largely orthogonal to a "
                      "volatility/beta risk premium, so it is unlikely to be the "
                      "same regime artifact.",
            "required_data": ["analyst_estimates", "earnings_calendar_and_surprise",
                              "broader_universe"],
            "minimum_viable_test": "Construct point-in-time revision-ratio and "
                      "standardized-surprise features, then run the rank-IC sweep "
                      "at 21d-63d.",
            "expected_horizon": "21d-63d",
            "expected_direction": "positive_for_upgrades_and_beats",
            "leakage_risks": "Revisions must be timestamped at the moment they "
                      "became public; using the final consensus instead of the "
                      "as-of consensus is forward-looking. Earnings dates must be "
                      "actual, not estimated.",
            "validation_gate": "Revision/surprise rank-IC clears the floor and the "
                      "2I-B battery, with event timing verified leakage-free.",
            "data_availability": "needs_acquisition",
            "implementation_complexity": "high",
            "priority": "medium",
            "go_to_test": False,
        },
        {
            "hypothesis_id": "earnings_event_drift",
            "title": "Post-Earnings-Announcement Drift",
            "thesis": "Returns drift in the direction of the earnings surprise for "
                      "several weeks after the announcement (PEAD).",
            "why_it_addresses_failed_2I_signal": "It is an event-conditioned, "
                      "short-window effect tied to a discrete catalyst rather than "
                      "a continuous market-beta exposure.",
            "required_data": ["earnings_calendar_and_surprise", "longer_price_history",
                              "broader_universe"],
            "minimum_viable_test": "Align returns to actual earnings dates, sort by "
                      "standardized surprise, and measure forward drift over "
                      "1-13 weeks event-time.",
            "expected_horizon": "5d-63d_event_time",
            "expected_direction": "positive_drift_with_surprise",
            "leakage_risks": "Using the announcement-day return inside the signal "
                      "window double-counts; event windows must start strictly "
                      "after the announcement. Estimated earnings dates leak.",
            "validation_gate": "Drift is monotone in surprise quantile and survives "
                      "the 2I-B battery in event time, net of costs.",
            "data_availability": "needs_acquisition",
            "implementation_complexity": "medium",
            "priority": "medium",
            "go_to_test": False,
        },
        {
            "hypothesis_id": "liquidity_breadth_and_microstructure",
            "title": "Liquidity, Breadth & Microstructure",
            "thesis": "Illiquidity (Amihud), turnover, and breadth/dispersion "
                      "measures rank forward returns as a liquidity risk / "
                      "attention signal.",
            "why_it_addresses_failed_2I_signal": "It is built from volume and "
                      "dispersion rather than the vol/beta tilt, and is testable on "
                      "the volume history already exported in 2G.",
            "required_data": ["liquidity_and_volume_history", "longer_price_history"],
            "minimum_viable_test": "On the 2G export, build Amihud illiquidity and "
                      "turnover features and run the rank-IC sweep at 21d-63d, "
                      "checking the signal is not a small-cap proxy.",
            "expected_horizon": "21d-63d",
            "expected_direction": "positive_illiquidity_premium",
            "leakage_risks": "Illiquidity correlates with size and price level; the "
                      "feature must be neutralized for those or it re-expresses a "
                      "known risk premium. Volume spikes around events leak if not "
                      "trailing.",
            "validation_gate": "Liquidity feature rank-IC survives 2I-B robustness "
                      "after size/price neutralization.",
            "data_availability": "available_now",
            "implementation_complexity": "medium",
            "priority": "medium",
            "go_to_test": True,
        },
        {
            "hypothesis_id": "sector_relative_strength",
            "title": "Sector-Relative Strength",
            "thesis": "Strength measured relative to a name's own sector (rather "
                      "than the whole market) ranks forward sector-relative "
                      "returns, removing the market-beta component.",
            "why_it_addresses_failed_2I_signal": "De-meaning by sector strips out "
                      "the broad market move that the 63d tilt was riding, so any "
                      "remaining edge is intra-sector selection.",
            "required_data": ["sector_industry_mapping", "longer_price_history"],
            "minimum_viable_test": "Attach a sector map, de-mean returns within "
                      "sector per date, and run the rank-IC sweep on "
                      "sector-relative momentum and reversal at 21d-63d.",
            "expected_horizon": "21d-63d",
            "expected_direction": "unknown_to_be_estimated",
            "leakage_risks": "Sector membership must be point-in-time (no current "
                      "GICS applied to history). Thin sectors give unstable "
                      "cross-sections.",
            "validation_gate": "Sector-relative rank-IC clears the floor and the "
                      "2I-B battery with no single sector carrying the result.",
            "data_availability": "low_cost",
            "implementation_complexity": "low",
            "priority": "high",
            "go_to_test": True,
        },
        {
            "hypothesis_id": "regime_conditional_signals",
            "title": "Regime-Conditional Signal Activation",
            "thesis": "A signal that fails unconditionally may be robust within a "
                      "specific volatility or trend regime; conditioning on a "
                      "trailing regime indicator could surface a stable subset.",
            "why_it_addresses_failed_2I_signal": "It directly confronts the 2I-B "
                      "finding that the edge was regime-dependent by treating "
                      "regime as an explicit conditioning variable instead of "
                      "averaging across regimes.",
            "required_data": ["longer_price_history", "benchmark_and_factor_data"],
            "minimum_viable_test": "Define a trailing regime label (market vol / "
                      "trend), then re-measure candidate-feature rank-IC within "
                      "each regime and test whether any regime-conditioned signal "
                      "is stable out-of-sample.",
            "expected_horizon": "21d-63d",
            "expected_direction": "regime_dependent",
            "leakage_risks": "Choosing the regime split after seeing results is "
                      "overfitting; the regime rule must be fixed in advance and "
                      "the indicator trailing-only. Few independent regime spans "
                      "in a short sample.",
            "validation_gate": "A pre-registered regime rule yields a signal that "
                      "survives the 2I-B battery within-regime across more than one "
                      "market cycle.",
            "data_availability": "available_now",
            "implementation_complexity": "medium",
            "priority": "medium",
            "go_to_test": True,
        },
    ]


# --------------------------------------------------------------------------- #
# Data requirements
# --------------------------------------------------------------------------- #
def _data_requirements() -> Dict[str, Any]:
    return {
        "longer_price_history": {
            "description": "Daily OHLCV reaching well before 2023, ideally a full "
                           "market cycle including a sustained drawdown, so a 63d "
                           "signal can be tested out of a single bull regime.",
            "required_for_hypotheses": [
                "beta_vol_neutralized_residual_signal",
                "cross_sectional_mean_reversion", "earnings_event_drift",
                "liquidity_breadth_and_microstructure", "sector_relative_strength",
                "regime_conditional_signals"],
            "must_be_point_in_time": False,
            "lookahead_risk": "Low for raw prices, but corporate-action adjustment "
                              "must use only data available as of each date.",
            "free_or_low_cost_sources_to_investigate": [
                "public end-of-day price-history exports",
                "exchange-published historical daily bars",
                "the existing 2G real-data export extended backward"],
            "blockers": "Adjusted-close consistency across the longer span and "
                        "delisted-name coverage to avoid survivorship bias.",
            "priority": "high",
        },
        "broader_universe": {
            "description": "More than the current ~40 mega-caps (toward the full "
                           "S&P 500 or broader) to reduce long-leg ticker "
                           "concentration and widen each cross-section.",
            "required_for_hypotheses": [
                "fundamental_quality_value", "estimate_revisions_and_surprise",
                "earnings_event_drift", "sector_relative_strength"],
            "must_be_point_in_time": True,
            "lookahead_risk": "Using today's index membership for history is "
                              "survivorship bias; membership must be as-of-date.",
            "free_or_low_cost_sources_to_investigate": [
                "point-in-time index-constituent histories",
                "exchange listing archives"],
            "blockers": "Point-in-time constituent lists are the scarce piece; "
                        "naive current-membership lists are not admissible.",
            "priority": "high",
        },
        "point_in_time_fundamentals": {
            "description": "As-first-reported quarterly/annual fundamentals with "
                           "correct report-date lags (profitability, accruals, "
                           "margins, yields).",
            "required_for_hypotheses": ["fundamental_quality_value"],
            "must_be_point_in_time": True,
            "lookahead_risk": "High. Restated figures and report-date misalignment "
                              "are the dominant leakage source in factor research.",
            "free_or_low_cost_sources_to_investigate": [
                "regulatory filing archives with filing timestamps",
                "low-cost fundamentals datasets that expose as-reported snapshots"],
            "blockers": "True point-in-time history is often paid; as-reported "
                        "snapshots with filing dates are the minimum bar.",
            "priority": "medium",
        },
        "analyst_estimates": {
            "description": "Point-in-time consensus estimates and their revision "
                           "history (as-of timestamps, not final consensus).",
            "required_for_hypotheses": ["estimate_revisions_and_surprise"],
            "must_be_point_in_time": True,
            "lookahead_risk": "High. Using the final or current consensus for a "
                              "past date imports the future.",
            "free_or_low_cost_sources_to_investigate": [
                "low-cost estimate datasets that retain revision timestamps"],
            "blockers": "Revision-timestamped estimate history is largely a paid "
                        "data product; cost/coverage must be scoped first.",
            "priority": "medium",
        },
        "earnings_calendar_and_surprise": {
            "description": "Actual (not estimated) earnings announcement dates and "
                           "realized-vs-expected surprise.",
            "required_for_hypotheses": [
                "estimate_revisions_and_surprise", "earnings_event_drift"],
            "must_be_point_in_time": True,
            "lookahead_risk": "Estimated/forward earnings dates and post-hoc "
                              "surprise revisions both leak; dates must be actual.",
            "free_or_low_cost_sources_to_investigate": [
                "exchange / regulator earnings-date archives",
                "low-cost earnings-calendar feeds"],
            "blockers": "Historical accuracy of announcement timestamps; many free "
                        "calendars only cover the recent forward window.",
            "priority": "medium",
        },
        "sector_industry_mapping": {
            "description": "Point-in-time sector / industry classification per name "
                           "for sector-relative and within-sector neutralization.",
            "required_for_hypotheses": [
                "sector_relative_strength", "fundamental_quality_value"],
            "must_be_point_in_time": True,
            "lookahead_risk": "Low-to-medium: applying current classification to "
                              "history misclassifies names that reclassified.",
            "free_or_low_cost_sources_to_investigate": [
                "public sector classification listings",
                "exchange-published industry tags"],
            "blockers": "Point-in-time reclassification history is harder to obtain "
                        "than a current snapshot, but the current snapshot is a "
                        "usable starting approximation.",
            "priority": "medium",
        },
        "liquidity_and_volume_history": {
            "description": "Daily volume and dollar-volume history for Amihud "
                           "illiquidity, turnover, and microstructure features.",
            "required_for_hypotheses": ["liquidity_breadth_and_microstructure"],
            "must_be_point_in_time": False,
            "lookahead_risk": "Low for raw volume; features must be trailing-only "
                              "and neutralized for size/price level.",
            "free_or_low_cost_sources_to_investigate": [
                "the volume already in the 2G export",
                "public end-of-day volume histories"],
            "blockers": "Mostly already available; intraday microstructure detail "
                        "would require a separate, costlier feed.",
            "priority": "low",
        },
        "benchmark_and_factor_data": {
            "description": "Benchmark (e.g. SPY) and standard factor / risk-model "
                           "series for beta estimation and residualization.",
            "required_for_hypotheses": [
                "beta_vol_neutralized_residual_signal",
                "cross_sectional_mean_reversion", "regime_conditional_signals"],
            "must_be_point_in_time": False,
            "lookahead_risk": "Low; betas/factor loadings must be trailing-only.",
            "free_or_low_cost_sources_to_investigate": [
                "the SPY series already in the 2G export",
                "public research factor-return libraries"],
            "blockers": "Largely available; a full commercial risk model is "
                        "optional and out of scope for the cheap first tests.",
            "priority": "low",
        },
    }


# --------------------------------------------------------------------------- #
# Research sequence (cheapest current-data tests first)
# --------------------------------------------------------------------------- #
def _research_sequence() -> List[Dict[str, Any]]:
    return [
        {
            "rank": 1,
            "action": "Beta/vol-neutralized residual rank-IC test on current data.",
            "uses_data": "current_2g_export",
            "hypotheses": ["beta_vol_neutralized_residual_signal"],
        },
        {
            "rank": 2,
            "action": "Sector-relative and residual mean-reversion rank-IC tests on "
                      "current data (sector map is low-cost).",
            "uses_data": "current_2g_export_plus_sector_map",
            "hypotheses": ["sector_relative_strength", "cross_sectional_mean_reversion",
                           "liquidity_breadth_and_microstructure",
                           "regime_conditional_signals"],
        },
        {
            "rank": 3,
            "action": "Acquire and validate longer price history and a broader, "
                      "point-in-time universe.",
            "uses_data": "longer_price_history,broader_universe",
            "hypotheses": [],
        },
        {
            "rank": 4,
            "action": "Investigate point-in-time fundamentals and estimate-revision "
                      "data (cost, coverage, leakage controls).",
            "uses_data": "point_in_time_fundamentals,analyst_estimates",
            "hypotheses": ["fundamental_quality_value",
                           "estimate_revisions_and_surprise"],
        },
        {
            "rank": 5,
            "action": "Run event-based earnings-drift tests once actual earnings "
                      "dates and surprise are available.",
            "uses_data": "earnings_calendar_and_surprise",
            "hypotheses": ["earnings_event_drift"],
        },
        {
            "rank": 6,
            "action": "Only after a feature family passes 2I-A IC + 2I-B robustness "
                      "on real, multi-regime data, scope a walk-forward model "
                      "candidate under the model-candidate gate.",
            "uses_data": "validated_feature_family",
            "hypotheses": [],
        },
    ]


# --------------------------------------------------------------------------- #
# Model-candidate gate
# --------------------------------------------------------------------------- #
def _model_candidate_gate() -> Dict[str, Any]:
    return {
        "may_create_model_candidate": False,
        "required_before_model_candidate": [
            "feature family passes the 2I-A IC sweep above the |IC| floor",
            "feature family passes the 2I-B robustness battery",
            "no single-regime artifact (sign-stable across market regimes)",
            "no excessive ticker concentration in the long leg",
            "no lookahead leakage in features or labels",
            "out-of-sample validation passes on more than one market regime",
        ],
        "explicit_rule": "No model candidate may be created until a feature family "
                         "passes the IC sweep and the out-of-sample robustness "
                         "battery on real data spanning more than one market "
                         "regime, with no single-regime artifact, no excessive "
                         "ticker concentration, and no lookahead leakage. The "
                         "model-v2 serving flag stays disabled until then.",
    }


def _recommended_next_phase() -> Dict[str, str]:
    return {
        "phase": "2K-B",
        "title": "Beta/Vol-Neutralized Residual Signal Test",
        "purpose": "Run the cheapest next alpha test using current data -- "
                   "residualize forward excess return against beta and realized "
                   "vol and re-measure cross-sectional rank-IC -- before acquiring "
                   "any new paid data. No model candidate is created in 2K-B.",
    }


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_backlog(input_2j: str = INPUT_2J_JSON,
                  input_2ib: str = INPUT_2IB_JSON,
                  input_2ia: str = INPUT_2IA_JSON) -> Dict[str, Any]:
    twoj = _read_json(input_2j)
    twoib = _read_json(input_2ib)
    twoia = _read_json(input_2ia)

    upstream = _upstream_decision_summary(twoj, twoib)

    return {
        "phase": PHASE,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "phase2j_json": input_2j,
            "phase2i_b_json": input_2ib,
            "phase2i_a_json": input_2ia,
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
        "provenance": {
            "phase2j_phase": twoj.get("phase"),
            "phase2i_b_phase": twoib.get("phase"),
            "phase2i_a_phase": twoia.get("phase"),
        },
        "upstream_decision_summary": upstream,
        "alpha_hypothesis_backlog": _alpha_hypothesis_backlog(),
        "data_requirements": _data_requirements(),
        "research_sequence": _research_sequence(),
        "model_candidate_gate": _model_candidate_gate(),
        "recommended_next_phase": _recommended_next_phase(),
    }


def write_backlog(backlog: Dict[str, Any], path: str = BACKLOG_JSON) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(backlog, f, indent=2, allow_nan=False)


def run(output_path: str = BACKLOG_JSON,
        input_2j: str = INPUT_2J_JSON,
        input_2ib: str = INPUT_2IB_JSON,
        input_2ia: str = INPUT_2IA_JSON) -> Dict[str, Any]:
    backlog = build_backlog(input_2j, input_2ib, input_2ia)
    write_backlog(backlog, output_path)
    return backlog


def main() -> int:
    t0 = time.perf_counter()
    d = run()
    elapsed = time.perf_counter() - t0
    up = d["upstream_decision_summary"]
    backlog = d["alpha_hypothesis_backlog"]
    n_now = sum(1 for h in backlog if h["go_to_test"])
    print(f"[phase2k-a] upstream decision               : {up['phase2j_go_no_go']}  "
          f"(no_go_confirmed={up['no_go_confirmed']})")
    print(f"[phase2k-a] hypothesis families             : {len(backlog)}")
    print(f"[phase2k-a] families testable on current data: {n_now}")
    print(f"[phase2k-a] data requirements               : "
          f"{len(d['data_requirements'])}")
    print(f"[phase2k-a] research sequence steps          : "
          f"{len(d['research_sequence'])}")
    print(f"[phase2k-a] may create model candidate       : "
          f"{d['model_candidate_gate']['may_create_model_candidate']}")
    print(f"[phase2k-a] recommended next phase           : "
          f"{d['recommended_next_phase']['phase']} - "
          f"{d['recommended_next_phase']['title']}")
    print(f"[phase2k-a] backlog written                  : {BACKLOG_JSON}")
    print(f"[phase2k-a] elapsed seconds                  : {elapsed:.2f}")
    print("[phase2k-a] research-planning only; safety flags emitted in JSON.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
