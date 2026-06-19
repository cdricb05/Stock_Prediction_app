"""Phase 3-D - External Data Decision for Greenfield Modeling.

This is a **decision / planning** analyzer, not a training phase. After the Phase 3-C
price/volume-only kill switch tripped, this script answers one decisive question:

    "After the price/volume-only kill switch, which external data family should we add
     first to maximize the chance of a real predictive edge while keeping cost, complexity,
     and validation risk under control?"

It reads small, Git-tracked Phase 3-A / 3-B / 3-C result JSONs, the current-as-of sector
map CSV, and the repo-local Phase 2K-H manual-build tracking summary (which already carries
the D: data-quality / survivorship context), confirms that the Phase 3-C kill switch
triggered, scores a transparent matrix of candidate external-data families, and recommends
the first external-data track plus a backup. It writes three small files under
research/output.

Scope and safety. This analyzer trains no model, creates no production model candidate,
writes no deployable model artifact, fetches nothing from the network, calls no data vendor,
purchases no data, reads nothing on the D: drive, writes nothing to the D: drive, touches no
database, runs no migration, restarts no service, enables no serving flag, places no orders,
and trades nothing. It is read-only with respect to every prior artifact and only writes the
three small decision outputs listed below.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime

PHASE = "3-D"

# --------------------------------------------------------------------------- #
# Paths (repo-local only; nothing on the D: drive is read or written here)
# --------------------------------------------------------------------------- #
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUT_DIR = os.path.join(_REPO_ROOT, "research", "output")
_IN_DIR = os.path.join(_REPO_ROOT, "research", "input")

PHASE3C_JSON = os.path.join(_OUT_DIR, "phase3c_refined_greenfield_rerun.json")
PHASE3B_JSON = os.path.join(_OUT_DIR, "phase3b_greenfield_refinement.json")
PHASE3A_JSON = os.path.join(_OUT_DIR, "phase3a_greenfield_baseline.json")
SECTOR_MAP_CSV = os.path.join(_IN_DIR, "phase2k_p_sector_map_current.csv")
PHASE2KH_JSON = os.path.join(_OUT_DIR, "phase2k_h_manual_build_run_summary.json")

DECISION_JSON = os.path.join(_OUT_DIR, "phase3d_external_data_decision.json")
OPTION_MATRIX_CSV = os.path.join(_OUT_DIR, "phase3d_external_data_option_matrix.csv")
RECOMMENDED_TRACK_JSON = os.path.join(_OUT_DIR, "phase3d_recommended_data_track.json")

# --------------------------------------------------------------------------- #
# Recommendation vocabulary
# --------------------------------------------------------------------------- #
REC_FUNDAMENTALS = "PROCEED_TO_FUNDAMENTALS_EARNINGS_DATA_FEASIBILITY"
REC_OPTIONS = "PROCEED_TO_OPTIONS_DATA_FEASIBILITY"
REC_NEWS = "PROCEED_TO_NEWS_SENTIMENT_DATA_FEASIBILITY"
REC_VENDOR = "REQUIRE_VENDOR_RESEARCH_BEFORE_DATA_TRACK"
REC_STOP = "STOP_MODEL_RESEARCH_NO_FEASIBLE_DATA_PATH"
REC_BLOCKED = "EXTERNAL_DATA_DECISION_BLOCKED"
ALLOWED_RECOMMENDATIONS = [
    REC_FUNDAMENTALS, REC_OPTIONS, REC_NEWS, REC_VENDOR, REC_STOP, REC_BLOCKED,
]

# --------------------------------------------------------------------------- #
# Scoring scheme. Every criterion is on a 1-5 scale where 5 is the most favorable
# outcome for a small, careful research project. Two criteria are framed for
# positive polarity: implementation_simplicity (5 = low complexity) and
# signal_robustness (5 = low risk of a noisy / fragile signal).
# --------------------------------------------------------------------------- #
CRITERIA = [
    "expected_predictive_relevance",
    "point_in_time_feasibility",
    "cost_accessibility",
    "implementation_simplicity",
    "validation_cleanliness",
    "universe_coverage_128",
    "walk_forward_compatibility",
    "signal_robustness",
    "next_phase_suitability",
]
CRITERIA_DESCRIPTIONS = {
    "expected_predictive_relevance":
        "Plausible cross-sectional predictive power BEYOND what price/volume already captured.",
    "point_in_time_feasibility":
        "Can be evaluated point-in-time, or with clearly caveated historical timestamps, without look-ahead.",
    "cost_accessibility":
        "Affordable for a small project (5 = free / low cost; 1 = paid enterprise infrastructure).",
    "implementation_simplicity":
        "Ease of building trailing-only features (5 = simple structured fields; 1 = heavy NLP / engineering).",
    "validation_cleanliness":
        "How leakage-resistant and reproducible the historical validation is.",
    "universe_coverage_128":
        "Breadth across the current 128-ticker large-cap universe (5 = near-full; 1 = sparse).",
    "walk_forward_compatibility":
        "Fits the existing cross-sectional walk-forward framework as trailing features.",
    "signal_robustness":
        "Robustness vs a noisy / fragile signal (5 = robust / well-studied; 1 = fragile / unproven).",
    "next_phase_suitability":
        "Suitability as the immediate next feasibility phase right after the kill switch.",
}
CRITERIA_TASK_NAMES = {
    "implementation_simplicity": "implementation complexity (scored as ease: 5 = low complexity)",
    "signal_robustness": "risk of noisy / fragile signal (scored as robustness: 5 = low risk)",
}
WEIGHTS = {
    "expected_predictive_relevance": 2.0,
    "point_in_time_feasibility": 1.5,
    "validation_cleanliness": 1.5,
    "next_phase_suitability": 1.5,
    "cost_accessibility": 1.0,
    "implementation_simplicity": 1.0,
    "universe_coverage_128": 1.0,
    "walk_forward_compatibility": 1.0,
    "signal_robustness": 1.0,
}
# A family is recommendable only if it clears every gate below (the Phase 3-D decision logic).
DECISION_GATE = {
    "expected_predictive_relevance": 3,
    "point_in_time_feasibility": 3,
    "cost_accessibility": 3,
    "implementation_simplicity": 3,
    "universe_coverage_128": 3,
    "walk_forward_compatibility": 3,
}

# --------------------------------------------------------------------------- #
# Candidate external-data families with transparent 1-5 scores and rationale.
# scores tuple is in CRITERIA order.
# --------------------------------------------------------------------------- #
_FAMILIES = [
    {
        "data_family": "Fundamentals",
        "family_key": "fundamentals",
        "example_signals": "valuation, quality, growth, profitability, leverage, accruals",
        "scores": (4, 4, 4, 4, 4, 5, 5, 4, 5),
        "rationale": (
            "Structured company financials map directly onto cross-sectional equity ranking, "
            "add information price/volume does not contain, and are straightforward to convert "
            "into trailing-only valuation / quality / growth / profitability / leverage features."),
    },
    {
        "data_family": "Earnings events",
        "family_key": "earnings_events",
        "example_signals": "earnings surprise (SUE), post-earnings-announcement drift, event dates",
        "scores": (4, 4, 4, 4, 4, 5, 5, 4, 5),
        "rationale": (
            "Earnings dates and surprises are timestamped, point-in-time clean, and support "
            "well-studied SUE / post-earnings-announcement-drift features that fit the "
            "walk-forward framework directly."),
    },
    {
        "data_family": "Analyst estimates and revisions",
        "family_key": "analyst_estimates_revisions",
        "example_signals": "estimate revision momentum, dispersion, recommendation changes",
        "scores": (5, 3, 3, 4, 4, 5, 5, 4, 5),
        "rationale": (
            "Estimate-revision momentum is one of the more robust cross-sectional anomalies and "
            "is highly complementary to price/volume; the main care item is timestamping consensus "
            "snapshots correctly so revisions are strictly point-in-time."),
    },
    {
        "data_family": "Options / implied volatility",
        "family_key": "options_implied_vol",
        "example_signals": "IV level, IV term structure, put-call skew, realized-implied spread",
        "scores": (4, 4, 3, 3, 3, 3, 4, 3, 3),
        "rationale": (
            "Options data is natively timestamped (point-in-time clean) and carries forward-looking "
            "information, but coverage is limited to liquid optionable names, it is more expensive, "
            "and turning skew / term-structure into stable trailing features is more involved."),
    },
    {
        "data_family": "Insider transactions",
        "family_key": "insider_transactions",
        "example_signals": "Form 4 net buying / selling, cluster buys",
        "scores": (3, 4, 5, 3, 3, 3, 3, 3, 3),
        "rationale": (
            "Form 4 filings are free, public, and timestamped (point-in-time clean), but the signal "
            "is sparse and low-breadth; feasible but a weaker first step than the structured "
            "fundamentals / earnings / revisions bundle."),
    },
    {
        "data_family": "Company guidance / transcripts",
        "family_key": "guidance_transcripts",
        "example_signals": "guidance changes, management-tone NLP from call transcripts",
        "scores": (3, 4, 3, 2, 3, 4, 3, 3, 2),
        "rationale": (
            "Transcripts are timestamped but require heavy NLP to convert into features and are "
            "harder to validate cleanly; better pursued after the structured-data track is built."),
    },
    {
        "data_family": "Short interest / securities lending",
        "family_key": "short_interest_lending",
        "example_signals": "short interest ratio, days-to-cover, borrow fee / utilization",
        "scores": (3, 2, 3, 3, 3, 4, 4, 3, 2),
        "rationale": (
            "Exchange short interest is reported with a bi-monthly lag (weaker point-in-time "
            "alignment) and richer borrow/lending data is paid; usable later but not the cleanest "
            "first track."),
    },
    {
        "data_family": "News and sentiment",
        "family_key": "news_sentiment",
        "example_signals": "news volume, headline / article sentiment scores",
        "scores": (3, 3, 3, 2, 2, 4, 3, 2, 3),
        "rationale": (
            "Timestamped text is available but is noisy and fragile, demands NLP plumbing, and is "
            "the hardest of the candidate families to validate leakage-free; a plausible backup, "
            "not a first track."),
    },
    {
        "data_family": "Macro / rates / commodities",
        "family_key": "macro_rates_commodities",
        "example_signals": "rates level / slope, credit spreads, oil and commodity moves",
        "scores": (2, 4, 5, 4, 3, 2, 2, 2, 1),
        "rationale": (
            "Cheap and point-in-time accessible, but macro series are mostly market-level / "
            "time-series signals with little cross-sectional breadth across a 128-name ranking "
            "problem; poor fit as the immediate next cross-sectional phase."),
    },
    {
        "data_family": "Institutional ownership / 13F",
        "family_key": "institutional_ownership_13f",
        "example_signals": "13F holdings changes, ownership concentration",
        "scores": (2, 2, 4, 3, 2, 4, 3, 2, 1),
        "rationale": (
            "13F filings arrive ~45 days after quarter-end, so the data is badly stale and "
            "point-in-time-weak; low priority."),
    },
    {
        "data_family": "Alternative data",
        "family_key": "alternative_data",
        "example_signals": "credit-card panels, web traffic, satellite, app downloads",
        "scores": (4, 3, 1, 1, 2, 2, 2, 2, 1),
        "rationale": (
            "Potentially high relevance, but it requires paid enterprise infrastructure, has thin "
            "coverage on this universe, and is the hardest to validate; explicitly out of scope for "
            "a small project as a first track."),
    },
]


def _scores_dict(scores_tuple):
    return {k: int(v) for k, v in zip(CRITERIA, scores_tuple)}


def _weighted_score(scores):
    total = sum(scores[k] * WEIGHTS[k] for k in CRITERIA)
    return round(total / sum(WEIGHTS.values()), 4)


def _decision_gate_pass(scores):
    return all(scores[k] >= thr for k, thr in DECISION_GATE.items())


# --------------------------------------------------------------------------- #
# Input loading + Phase 3-C confirmation
# --------------------------------------------------------------------------- #
def _load_json(path):
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _sector_map_stats(path):
    if not os.path.isfile(path):
        return {"present": False, "ticker_count": 0, "sector_count": 0}
    tickers, sectors = set(), set()
    with open(path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            t = (row.get("ticker") or "").strip()
            s = (row.get("sector") or "").strip()
            if t:
                tickers.add(t)
            if s:
                sectors.add(s)
    return {
        "present": True,
        "ticker_count": len(tickers),
        "sector_count": len(sectors),
        "point_in_time": False,
        "membership_basis": "current_as_of",
    }


def confirm_phase3c(phase3c):
    """Confirm the Phase 3-C kill-switch contract that gates this phase."""
    if not phase3c:
        return {"all_confirmed": False, "phase3c_present": False}
    rec = phase3c.get("recommendation", {}) or {}
    nxt = phase3c.get("recommended_next_phase", {}) or {}
    checks = {
        "phase3c_present": True,
        "phase_is_3c": phase3c.get("phase") == "3-C",
        "recommendation_is_kill_switch":
            rec.get("recommendation") == "PRICE_VOLUME_ONLY_KILL_SWITCH_TRIGGERED",
        "kill_switch_triggered_true": rec.get("kill_switch_triggered") is True,
        "next_phase_is_3d": nxt.get("phase") == "3-D",
        "next_phase_title_matches":
            nxt.get("title") == "External Data Decision for Greenfield Modeling",
        "research_model_trained_true": phase3c.get("research_model_trained") is True,
        "production_model_trained_false": phase3c.get("production_model_trained") is False,
        "production_model_candidate_created_false":
            phase3c.get("production_model_candidate_created") is False,
        "deployable_model_artifact_written_false":
            phase3c.get("deployable_model_artifact_written") is False,
        "production_edge_claimed_false": phase3c.get("production_edge_claimed") is False,
    }
    checks["all_confirmed"] = all(checks.values())
    return checks


def build_kill_switch_summary(phase3c):
    """Summarize why price/volume-only modeling is stopped, from the Phase 3-C result."""
    ks = (phase3c or {}).get("kill_switch_evaluation", {}) or {}
    rec = (phase3c or {}).get("recommendation", {}) or {}
    return {
        "primary_model": ks.get("primary_model", "REFINED_RIDGE_LINEAR_RANK_MODEL"),
        "primary_horizon_days": ks.get("primary_horizon", 21),
        "primary_mean_rank_ic": ks.get("primary_mean_rank_ic"),
        "primary_worst_fold_rank_ic": ks.get("primary_worst_fold_rank_ic"),
        "primary_fold_win_rate": ks.get("primary_fold_win_rate"),
        "primary_catastrophic_fold_count": ks.get("primary_catastrophic_fold_count"),
        "primary_mean_top_minus_bottom_spread": ks.get("primary_mean_top_minus_bottom_spread"),
        "model_free_composite_ic_same_horizon": ks.get("model_free_composite_ic_same_horizon"),
        "kill_switch_triggered": rec.get("kill_switch_triggered", True),
        "risk_neutralization_removed_the_signal": True,
        "conclusion": (
            "After the Phase 3-B refinements, the primary 21d refined ridge had a near-zero mean "
            "rank IC (~0.004), a worst fold near -0.10, a sub-0.60 fold win rate (~0.53), and three "
            "catastrophic folds; risk-neutralizing the beta / volatility / correlation tilt removed "
            "the apparent signal. There is no robust residual price/volume alpha, so price/volume-"
            "only modeling is stopped and the next edge must come from external data."),
    }


# --------------------------------------------------------------------------- #
# Option matrix + decision
# --------------------------------------------------------------------------- #
def build_option_matrix():
    rows = []
    for fam in _FAMILIES:
        scores = _scores_dict(fam["scores"])
        rows.append({
            "data_family": fam["data_family"],
            "family_key": fam["family_key"],
            "example_signals": fam["example_signals"],
            "scores": scores,
            "equal_weight_total": int(sum(scores.values())),
            "weighted_priority_score": _weighted_score(scores),
            "decision_gate_pass": _decision_gate_pass(scores),
            "rationale": fam["rationale"],
        })
    rows.sort(key=lambda r: (-r["weighted_priority_score"], -r["equal_weight_total"]))
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return rows


def decide(matrix):
    """Apply the conservative decision logic and select the first external-data track."""
    by_key = {r["family_key"]: r for r in matrix}
    structured = ["fundamentals", "earnings_events", "analyst_estimates_revisions"]
    structured_rows = [by_key[k] for k in structured]
    structured_all_pass = all(r["decision_gate_pass"] for r in structured_rows)

    # The structured-company-data bundle is the best first step when all three components
    # clear the decision gate and rank at the top of the matrix.
    top3_keys = {r["family_key"] for r in matrix[:3]}
    structured_lead = structured_all_pass and set(structured) == top3_keys

    if structured_lead:
        recommendation = REC_FUNDAMENTALS
    else:
        # Fall back to the single best gate-passing family, else require vendor research.
        passers = [r for r in matrix if r["decision_gate_pass"]]
        if not passers:
            recommendation = REC_VENDOR
        else:
            best = passers[0]["family_key"]
            recommendation = {
                "options_implied_vol": REC_OPTIONS,
                "news_sentiment": REC_NEWS,
            }.get(best, REC_VENDOR)

    # Backup: the highest-scoring gate-passing family outside the selected structured bundle,
    # preferring options/IV, then news/sentiment.
    backup_candidates = [r for r in matrix
                         if r["family_key"] not in structured and r["decision_gate_pass"]]
    backup = None
    for pref in ("options_implied_vol", "news_sentiment"):
        for r in backup_candidates:
            if r["family_key"] == pref:
                backup = r
                break
        if backup:
            break
    if backup is None and backup_candidates:
        backup = backup_candidates[0]

    # Tag verdicts on the matrix rows.
    backup_key = backup["family_key"] if backup else None
    for r in matrix:
        if r["family_key"] in structured:
            r["verdict"] = "selected_primary_track_component"
        elif r["family_key"] == backup_key:
            r["verdict"] = "selected_backup_track"
        elif r["decision_gate_pass"]:
            r["verdict"] = "feasible_not_selected"
        else:
            r["verdict"] = "rejected"

    selected_track = {
        "track_name": "Fundamentals + Earnings + Estimate Revisions",
        "recommendation": recommendation,
        "component_families": structured,
        "component_data_families": [r["data_family"] for r in structured_rows],
        "all_components_pass_decision_gate": structured_all_pass,
        "combined_mean_weighted_score": round(
            sum(r["weighted_priority_score"] for r in structured_rows) / len(structured_rows), 4),
        "example_feature_blocks": [
            "valuation (e.g. earnings/book/sales yields)",
            "quality (profitability, margins, accruals)",
            "growth (sales / earnings growth)",
            "leverage and balance-sheet strength",
            "estimate revision momentum and dispersion",
            "earnings surprise (SUE) and post-earnings-announcement drift",
        ],
        "why_selected": (
            "Structured company fundamentals, earnings events, and analyst estimate revisions are "
            "the most practical first external-data track: they are highly relevant beyond "
            "price/volume, can be made point-in-time, map naturally onto cross-sectional equity "
            "ranking over the 128-ticker universe, convert cleanly into trailing-only features, fit "
            "the existing walk-forward framework, and are far easier to validate leakage-free than "
            "raw news, options, or alternative data. All three components clear every decision gate "
            "and occupy the top three ranks of the option matrix."),
    }
    backup_track = None
    if backup:
        backup_track = {
            "track_name": backup["data_family"],
            "family_key": backup["family_key"],
            "recommendation_if_chosen": (
                REC_OPTIONS if backup["family_key"] == "options_implied_vol"
                else REC_NEWS if backup["family_key"] == "news_sentiment" else REC_VENDOR),
            "weighted_priority_score": backup["weighted_priority_score"],
            "example_feature_blocks": [s.strip() for s in backup["example_signals"].split(",")],
            "why_backup": (
                "Options / implied-volatility data is the strongest gate-passing family outside the "
                "structured bundle: it is natively point-in-time and forward-looking, but it is more "
                "expensive, narrower in coverage, and harder to turn into stable trailing features, "
                "so it is the backup rather than the first track. News / sentiment is a secondary "
                "fallback with lower validation cleanliness and robustness."),
        }

    rejected_tracks = []
    for r in matrix:
        if r["verdict"] in ("rejected", "feasible_not_selected"):
            rejected_tracks.append({
                "data_family": r["data_family"],
                "family_key": r["family_key"],
                "rank": r["rank"],
                "weighted_priority_score": r["weighted_priority_score"],
                "decision_gate_pass": r["decision_gate_pass"],
                "verdict": r["verdict"],
                "reason": r["rationale"],
            })
    return recommendation, selected_track, backup_track, rejected_tracks


# --------------------------------------------------------------------------- #
# Narrative blocks
# --------------------------------------------------------------------------- #
def build_implementation_requirements():
    return [
        "Treat Phase 3-E as data feasibility only: identify the exact point-in-time source, "
        "schema, historical coverage, cost, and ingestion plan before any model training.",
        "Keep a vendor-agnostic feature schema: define the target fundamental / earnings / "
        "estimate-revision fields independently of which provider ultimately supplies them.",
        "Align every external field to the existing 128-ticker panel and trading calendar, and "
        "convert each into strictly trailing, point-in-time features (as-reported, with lag).",
        "Preserve reported-vs-restated discipline: use first-reported values with their true "
        "availability dates so no restated fundamental leaks into the past.",
        "Reuse the existing walk-forward harness (>=3y train, ~6mo validation, 63d embargo) and "
        "keep the model-free composite as a must-beat benchmark.",
        "No production integration, no model candidate, and no deployable artifact in the data "
        "feasibility phase; this remains research-only and preview-only.",
    ]


def build_validation_requirements():
    return [
        "Validate any external-data feature under the same chronological, embargoed, "
        "out-of-sample walk-forward used for price/volume, reporting rank IC by fold and regime.",
        "Require the external-data model to beat the model-free composite must-beat benchmark and "
        "to clear kill-switch-style stability gates (no catastrophic fold, fold win rate >= 0.60).",
        "Demonstrate point-in-time correctness explicitly: every feature must be computable only "
        "from information available on or before each scoring date.",
        "Continue to report all results as survivorship-biased / current-membership caveated until "
        "a clean point-in-time constituent build is funded.",
        "Quantify incremental value: show the external-data signal adds information beyond the "
        "existing price/volume features, not just re-expresses them.",
    ]


def build_risks_and_caveats():
    return [
        "Point-in-time / restatement risk: fundamentals are frequently restated; using restated "
        "values introduces look-ahead bias.",
        "Consensus-snapshot leakage: estimate revisions must be timestamped to when the consensus "
        "actually changed, not to the period they describe.",
        "Coverage gaps: some fields may be missing for parts of the 128-ticker universe or for "
        "earlier history, biasing cross-sectional ranks.",
        "Cost creep: clean point-in-time fundamentals / estimates history can require a paid "
        "provider; the feasibility phase must establish cost before committing.",
        "Survivorship bias persists: the underlying universe and sector map are current-as-of, so "
        "any external-data result remains survivorship-caveated.",
        "Licensing: provider terms may restrict storage or redistribution; this must be confirmed "
        "in the feasibility phase before any acquisition.",
    ]


def build_recommended_next_phase(recommendation):
    table = {
        REC_FUNDAMENTALS: (
            "Fundamentals and Earnings Data Feasibility",
            "Determine the exact point-in-time data source, schema, historical coverage, cost, and "
            "ingestion plan for fundamentals, earnings events, and analyst estimate revisions "
            "before any model training."),
        REC_OPTIONS: (
            "Options Data Feasibility",
            "Determine whether implied volatility, skew, realized/implied spreads, and options "
            "activity data can be sourced and tested point-in-time."),
        REC_NEWS: (
            "News and Sentiment Data Feasibility",
            "Determine whether timestamped news/sentiment can be sourced, stored, and transformed "
            "into leakage-safe features."),
        REC_VENDOR: (
            "External Data Vendor Research",
            "Compare current data vendors, cost, licensing, request limits, point-in-time support, "
            "and historical coverage before selecting a data family."),
        REC_STOP: (
            "Pause Model Research",
            "Stop modeling work until a feasible data source or budget is available."),
        REC_BLOCKED: (
            "Repair Phase 3-D Inputs",
            "Repair missing or inconsistent Phase 3-C kill-switch artifacts before deciding the "
            "next data track."),
    }
    title, purpose = table[recommendation]
    return {"phase": "3-E", "title": title, "purpose": purpose}


def build_recommendation(recommendation, selected_track, kill_summary, confirmed):
    selected = selected_track["track_name"] if recommendation == REC_FUNDAMENTALS else (
        "Options / Implied Volatility" if recommendation == REC_OPTIONS else
        "News and Sentiment" if recommendation == REC_NEWS else
        "Vendor comparison (no family chosen yet)" if recommendation == REC_VENDOR else
        "None" if recommendation == REC_STOP else "Blocked")
    if recommendation == REC_FUNDAMENTALS:
        reason = (
            "The Phase 3-C kill switch confirms there is no robust residual price/volume alpha, so "
            "the next edge must come from external data. Across a transparent 1-5 option matrix, "
            "structured company data - fundamentals, earnings events, and analyst estimate "
            "revisions - is the most practical first track: it is relevant beyond price/volume, can "
            "be made point-in-time, fits the 128-ticker cross-sectional walk-forward framework, "
            "converts cleanly into trailing-only features, and is far easier to validate than news, "
            "options, or alternative data. This recommendation selects a data feasibility track "
            "only: no model is trained, no production model candidate is created, no data is "
            "purchased, and no production edge is claimed.")
    elif recommendation == REC_BLOCKED:
        reason = (
            "The Phase 3-C kill-switch artifact is missing or did not record a triggered kill "
            "switch, so the external-data decision is blocked pending repaired inputs.")
    else:
        reason = (
            "Scoring favored an alternative track; see the option matrix. This selects a data "
            "feasibility track only, with no training, no model candidate, no data purchase, and "
            "no production edge claim.")
    return {
        "recommendation": recommendation,
        "allowed_values": ALLOWED_RECOMMENDATIONS,
        "create_production_model_candidate_now": False,
        "train_production_model_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "selected_track": selected,
        "phase3c_kill_switch_confirmed": confirmed.get("all_confirmed", False),
        "decision_phase_only": True,
        "reason": reason,
    }


def build_interpretation(recommendation):
    return {
        "price_volume_only_modeling_stopped": True,
        "decision_phase_only": True,
        "model_trained": False,
        "research_model_trained": False,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "deployable_model_artifact_written": False,
        "data_fetched_from_network": False,
        "data_purchased": False,
        "data_vendor_called": False,
        "read_from_d_drive": False,
        "wrote_to_d_drive": False,
        "production_edge_claimed": False,
        "results_context_remains_survivorship_biased": True,
        "narrative": (
            "Phase 3-D is a disciplined decision / planning artifact. It read the committed Phase "
            "3-A / 3-B / 3-C result JSONs, the current-as-of sector map, and the repo-local Phase "
            "2K-H build tracking summary, confirmed that the Phase 3-C price/volume-only kill switch "
            "triggered, scored eleven candidate external-data families on a transparent 1-5 matrix, "
            "and recommended a first external-data track plus a backup. It trained no model, created "
            "no production model candidate, wrote no deployable model artifact, fetched nothing from "
            "the network, called no data vendor, purchased no data, read nothing on the D: drive, "
            "wrote nothing to the D: drive, touched no database, ran no migration, restarted no "
            "service, enabled no serving flag, placed no orders, and traded nothing. The chosen "
            "track is a feasibility direction only and claims no production edge."),
    }


def build_safety_flags():
    return {
        "database_touched": False,
        "database_write_executed": False,
        "migration_executed": False,
        "deployment_executed": False,
        "model_v2_enabled": False,
        "production_edge_claimed": False,
        "no_trading": True,
        "no_orders": True,
        "no_automation": True,
        "research_model_trained": False,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "deployable_model_artifact_written": False,
        "d_drive_read": False,
        "d_drive_written": False,
        "network_used": False,
        "vendor_api_called": False,
        "data_purchase_made": False,
    }


def build_scoring_methodology():
    return {
        "scale": "Each criterion is scored 1-5 where 5 is the most favorable outcome for a small, "
                 "careful research project.",
        "positive_polarity_notes": CRITERIA_TASK_NAMES,
        "criteria": [{"key": k, "description": CRITERIA_DESCRIPTIONS[k], "weight": WEIGHTS[k]}
                     for k in CRITERIA],
        "weighted_score_formula":
            "weighted_priority_score = sum(score_i * weight_i) / sum(weight_i), on the 1-5 scale.",
        "decision_gate":
            {k: f">= {v}" for k, v in DECISION_GATE.items()},
        "decision_logic": [
            "Recommend a family only if it has plausible predictive relevance beyond price/volume.",
            "Recommend a family only if it can be evaluated point-in-time or with clearly caveated "
            "historical timestamps.",
            "Recommend a family only if it is feasible to test before any production integration.",
            "Recommend a family only if it does not require immediate paid enterprise infrastructure.",
            "Recommend a family only if it can be converted into trailing-only features.",
            "Recommend a family only if it fits the current 128-ticker walk-forward research framework.",
        ],
    }


# --------------------------------------------------------------------------- #
# Assembly + writers
# --------------------------------------------------------------------------- #
def build_decision(phase3c, phase3b, phase3a, kh_summary, sector_stats):
    confirmed = confirm_phase3c(phase3c)
    kill_summary = build_kill_switch_summary(phase3c)
    matrix = build_option_matrix()

    if not confirmed.get("phase3c_present") or not confirmed.get("recommendation_is_kill_switch") \
            or not confirmed.get("kill_switch_triggered_true"):
        recommendation = REC_BLOCKED
        selected_track = {"track_name": "Blocked", "recommendation": REC_BLOCKED,
                          "component_families": [], "all_components_pass_decision_gate": False}
        backup_track = None
        rejected_tracks = []
        for r in matrix:
            r["verdict"] = "not_evaluated_decision_blocked"
    else:
        recommendation, selected_track, backup_track, rejected_tracks = decide(matrix)

    data_context = {}
    if kh_summary:
        data_context = {
            "source": "research/output/phase2k_h_manual_build_run_summary.json (repo-local)",
            "universe_ticker_count": (kh_summary.get("survivorship_caveat_summary") or {})
                                     .get("universe_ticker_count"),
            "panel_ticker_count": (kh_summary.get("data_quality_summary") or {}).get("ticker_count"),
            "date_count": (kh_summary.get("data_quality_summary") or {}).get("date_count"),
            "years_span": (kh_summary.get("data_quality_summary") or {}).get("years_span"),
            "data_quality_status": (kh_summary.get("data_quality_summary") or {}).get("status"),
            "membership_basis": (kh_summary.get("survivorship_caveat_summary") or {})
                                .get("membership_basis"),
            "point_in_time_membership_claimed": (kh_summary.get("survivorship_caveat_summary") or {})
                                                .get("point_in_time_membership_claimed"),
            "note": "D: data-quality / survivorship context is read indirectly through this "
                    "repo-local Phase 2K-H summary; Phase 3-D reads nothing on the D: drive.",
        }

    result = {
        "phase": PHASE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "phase3c_json": "research/output/phase3c_refined_greenfield_rerun.json",
            "phase3b_json": "research/output/phase3b_greenfield_refinement.json",
            "phase3a_json": "research/output/phase3a_greenfield_baseline.json",
            "sector_map_csv": "research/input/phase2k_p_sector_map_current.csv",
            "phase2k_h_summary_json": "research/output/phase2k_h_manual_build_run_summary.json",
        },
        "outputs_written": {
            "decision_json": "research/output/phase3d_external_data_decision.json",
            "option_matrix_csv": "research/output/phase3d_external_data_option_matrix.csv",
            "recommended_data_track_json": "research/output/phase3d_recommended_data_track.json",
        },
        "d_drive_inputs_available_but_not_read": [
            "phase2k_g_data_quality_report.json",
            "phase2k_g_data_build_summary.json",
            "phase2k_g_survivorship_caveat.json",
        ],
        "phase3c_summary": {
            "phase3c_confirmed": confirmed,
            "phase3c_recommendation": (phase3c or {}).get("recommendation", {}).get("recommendation"),
            "phase3c_next_phase": (phase3c or {}).get("recommended_next_phase", {}),
            "phase3b_recommendation": (phase3b or {}).get("recommendation", {}).get("recommendation")
            if isinstance((phase3b or {}).get("recommendation"), dict) else None,
            "phase3a_recommendation": (phase3a or {}).get("recommendation", {}).get("recommendation")
            if isinstance((phase3a or {}).get("recommendation"), dict) else None,
            "data_context": data_context,
            "sector_map": sector_stats,
        },
        "price_volume_kill_switch_summary": kill_summary,
        "external_data_families_evaluated": [f["data_family"] for f in _FAMILIES],
        "scoring_methodology": build_scoring_methodology(),
        "option_matrix": matrix,
        "selected_data_track": selected_track,
        "backup_data_track": backup_track,
        "rejected_tracks": rejected_tracks,
        "implementation_requirements": build_implementation_requirements(),
        "validation_requirements": build_validation_requirements(),
        "risks_and_caveats": build_risks_and_caveats(),
        "recommendation": build_recommendation(recommendation, selected_track, kill_summary, confirmed),
        "interpretation": build_interpretation(recommendation),
        "recommended_next_phase": build_recommended_next_phase(recommendation),
    }
    result.update(build_safety_flags())
    return result


def write_option_matrix_csv(matrix, path):
    fieldnames = ["rank", "data_family", "family_key"] + list(CRITERIA) + [
        "equal_weight_total", "weighted_priority_score", "decision_gate_pass", "verdict",
        "example_signals"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in sorted(matrix, key=lambda x: x["rank"]):
            row = {
                "rank": r["rank"],
                "data_family": r["data_family"],
                "family_key": r["family_key"],
                "equal_weight_total": r["equal_weight_total"],
                "weighted_priority_score": r["weighted_priority_score"],
                "decision_gate_pass": r["decision_gate_pass"],
                "verdict": r.get("verdict", ""),
                "example_signals": r["example_signals"],
            }
            row.update(r["scores"])
            w.writerow(row)


def write_recommended_track_json(result, path):
    track = {
        "phase": PHASE,
        "generated_at": result["generated_at"],
        "recommendation": result["recommendation"]["recommendation"],
        "selected_track": result["selected_data_track"],
        "backup_track": result["backup_data_track"],
        "recommended_next_phase": result["recommended_next_phase"],
        "create_production_model_candidate_now": False,
        "train_production_model_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "data_purchase_made": False,
        "vendor_api_called": False,
        "network_used": False,
        "d_drive_read": False,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(track, f, indent=2)


def run(decision_path=DECISION_JSON, option_matrix_path=OPTION_MATRIX_CSV,
        recommended_track_path=RECOMMENDED_TRACK_JSON):
    phase3c = _load_json(PHASE3C_JSON)
    phase3b = _load_json(PHASE3B_JSON)
    phase3a = _load_json(PHASE3A_JSON)
    kh_summary = _load_json(PHASE2KH_JSON)
    sector_stats = _sector_map_stats(SECTOR_MAP_CSV)

    result = build_decision(phase3c, phase3b, phase3a, kh_summary, sector_stats)

    os.makedirs(os.path.dirname(decision_path), exist_ok=True)
    with open(decision_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    write_option_matrix_csv(result["option_matrix"], option_matrix_path)
    write_recommended_track_json(result, recommended_track_path)
    return result


def main():
    result = run()
    rec = result["recommendation"]
    nxt = result["recommended_next_phase"]
    sel = result["selected_data_track"]
    print(f"Phase {result['phase']} - External Data Decision")
    print(f"  phase3c kill switch confirmed : {result['phase3c_summary']['phase3c_confirmed']['all_confirmed']}")
    print(f"  families evaluated            : {len(result['option_matrix'])}")
    print(f"  recommendation                : {rec['recommendation']}")
    print(f"  selected track                : {sel.get('track_name')}")
    if result["backup_data_track"]:
        print(f"  backup track                  : {result['backup_data_track']['track_name']}")
    print(f"  recommended next phase        : {nxt['phase']} - {nxt['title']}")
    print("  top 3 families by weighted score:")
    for r in sorted(result["option_matrix"], key=lambda x: x["rank"])[:3]:
        print(f"    {r['rank']}. {r['data_family']:<32} {r['weighted_priority_score']:.3f}"
              f"  gate_pass={r['decision_gate_pass']}")
    return result


if __name__ == "__main__":
    main()
