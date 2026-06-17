"""Phase 2K-E Expanded Historical Universe Data Feasibility Check (offline, read-only).

Phase 2K-D turned the Phase 2L-B walk-forward result (NEED_MORE_DATA / WALK_FORWARD_FAIL
on the current ~40-name, single-regime 2023-2026 export) into a data-expansion and
universe-broadening plan: it defined longer history, a broader point-in-time universe,
survivorship controls, the target validation capacity, data-quality / leakage gates, and
the acquisition options needed before any retest. Phase 2K-D created no model candidate
and routed here.

This phase is a FEASIBILITY CHECK, not a data build and not a model. It reads the Phase
2K-D plan plus the Phase 2L-B / 2K-A summaries and the Phase 2G run summary, inventories
what local data already exists, assesses each expanded-dataset requirement against a
free / low-cost / paid path, and decides whether the expanded dataset (or at least a
survivorship-caveated free version sufficient to retest the price/volume liquidity
candidate) can be assembled without paid data. It writes one feasibility JSON.

It inspects only local files via path / directory checks. It makes no network call,
downloads nothing, acquires no paid data, touches no datastore, trains no model, creates
no model candidate, and performs no infrastructure action. The machine-readable safety
flags are emitted in the JSON; the full guardrail rationale lives in
docs/phase2k_e_data_feasibility_v1.md, not in this source.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import time
from typing import Any, Dict, List, Optional

PHASE = "2K-E"

_OUTPUT_DIR = os.path.join("research", "output")

# Inputs (read-only).
INPUT_2KD_JSON = os.path.join(_OUTPUT_DIR, "phase2k_d_data_expansion_plan.json")
INPUT_2LB_JSON = os.path.join(_OUTPUT_DIR, "phase2l_b_walk_forward_validation.json")
INPUT_2KA_JSON = os.path.join(_OUTPUT_DIR, "phase2k_alpha_backlog.json")
RUN_SUMMARY_JSON = os.path.join(_OUTPUT_DIR, "phase2g_c_real_data_run_summary.json")

# Known local data artifacts whose presence this phase reports on.
PRICE_HISTORY_CSV = os.path.join(_OUTPUT_DIR, "phase2g_price_history_real.csv")
SCORED_CSV = os.path.join(_OUTPUT_DIR, "phase2g_real_data_scored.csv")

# The single output this analyzer is allowed to write.
RESULTS_JSON = os.path.join(_OUTPUT_DIR, "phase2k_e_data_feasibility.json")

# Allowed feasibility statuses (closed vocabulary).
FEASIBLE_NOW = "FEASIBLE_NOW"
FEASIBLE_WITH_FREE_EXTENSION = "FEASIBLE_WITH_FREE_EXTENSION"
FEASIBLE_WITH_LOW_COST_SOURCE = "FEASIBLE_WITH_LOW_COST_SOURCE"
REQUIRES_PAID_POINT_IN_TIME_DATA = "REQUIRES_PAID_POINT_IN_TIME_DATA"
NOT_FEASIBLE_YET = "NOT_FEASIBLE_YET"

ALLOWED_STATUSES = (
    FEASIBLE_NOW,
    FEASIBLE_WITH_FREE_EXTENSION,
    FEASIBLE_WITH_LOW_COST_SOURCE,
    REQUIRES_PAID_POINT_IN_TIME_DATA,
    NOT_FEASIBLE_YET,
)

SESSIONS_PER_YEAR = 252


# --------------------------------------------------------------------------- #
# Read-only helpers
# --------------------------------------------------------------------------- #
def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _exists(path: str) -> bool:
    return os.path.isfile(path)


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


def _backlog_sources(backlog: Dict[str, Any], key: str) -> List[str]:
    """Pull the free/low-cost source hints the Phase 2K-A backlog recorded for a
    requirement, if present. Read-only passthrough; no network."""
    dr = backlog.get("data_requirements", {}) or {}
    item = dr.get(key, {}) or {}
    src = item.get("free_or_low_cost_sources_to_investigate")
    if isinstance(src, list):
        return [str(s) for s in src]
    return []


# --------------------------------------------------------------------------- #
# Upstream extraction
# --------------------------------------------------------------------------- #
def load_upstream() -> Dict[str, Any]:
    twokd = _read_json(INPUT_2KD_JSON)
    twolb = _read_json(INPUT_2LB_JSON)
    twoka = _read_json(INPUT_2KA_JSON)
    run_summary = _read_json(RUN_SUMMARY_JSON)

    req = (twokd.get("expanded_dataset_requirements", {}) or {}).get("requirements", {}) \
        or {}
    cap = twokd.get("target_validation_capacity", {}) or {}
    gaps = twokd.get("data_gap_analysis", {}) or {}
    panel = gaps.get("current_panel", {}) or {}
    rec_block = twolb.get("recommendation", {}) or {}
    cand_block = twolb.get("candidate", {}) or {}

    return {
        "twokd": twokd,
        "twolb": twolb,
        "twoka": twoka,
        "run_summary": run_summary,
        "candidate": (cand_block.get("feature")
                      or (twokd.get("decision", {}) or {}).get("candidate")
                      or gaps.get("candidate")),
        "upstream_2lb_recommendation": rec_block.get("recommendation"),
        "kd_requirements": req,
        "kd_target_capacity": cap,
        "kd_data_quality_gates": twokd.get("data_quality_gates", []) or [],
        "kd_min_years": cap.get("min_years_history"),
        "kd_recommended_years": cap.get("recommended_years_history"),
        "kd_min_tickers": cap.get("min_tickers_per_date"),
        "kd_panel_n_tickers": panel.get("n_tickers"),
        "kd_recommended_next_phase": (twokd.get("recommended_next_phase", {}) or {}),
        "kd_decision": twokd.get("decision", {}) or {},
        # Phase 2G run summary facts (the existing local export).
        "rs_row_count": run_summary.get("row_count"),
        "rs_ticker_count": run_summary.get("ticker_count")
        or run_summary.get("universe_ticker_count"),
        "rs_date_start": run_summary.get("date_start"),
        "rs_date_end": run_summary.get("date_end"),
        "rs_benchmark": run_summary.get("benchmark"),
        "rs_benchmark_present": run_summary.get("benchmark_present"),
        "rs_volume_present": run_summary.get("volume_present"),
        "rs_price_csv": run_summary.get("output_csv"),
    }


# --------------------------------------------------------------------------- #
# Local data inventory
# --------------------------------------------------------------------------- #
def build_local_data_inventory(u: Dict[str, Any]) -> Dict[str, Any]:
    existing: List[str] = []
    if os.path.isdir(_OUTPUT_DIR):
        for name in sorted(os.listdir(_OUTPUT_DIR)):
            if name.lower().endswith((".json", ".csv")):
                existing.append(name)

    current_years = _years_between(u["rs_date_start"], u["rs_date_end"])
    min_years = u["kd_min_years"]
    min_tickers = u["kd_min_tickers"]
    n_tickers = u["rs_ticker_count"]

    satisfies = False
    capacity_gap = {}
    if isinstance(current_years, (int, float)) and isinstance(min_years, (int, float)):
        years_ok = current_years >= float(min_years)
        tickers_ok = (isinstance(n_tickers, (int, float))
                      and isinstance(min_tickers, (int, float))
                      and float(n_tickers) >= float(min_tickers))
        satisfies = bool(years_ok and tickers_ok)
        capacity_gap = {
            "current_years": current_years,
            "target_min_years": min_years,
            "years_short_by": _round(max(0.0, float(min_years) - current_years), 2),
            "current_tickers": n_tickers,
            "target_min_tickers_per_date": min_tickers,
            "tickers_short_by": (int(max(0, int(min_tickers) - int(n_tickers)))
                                 if isinstance(n_tickers, (int, float))
                                 and isinstance(min_tickers, (int, float)) else None),
        }

    return {
        "output_dir": _OUTPUT_DIR.replace("\\", "/"),
        "existing_output_files_found": existing,
        "existing_output_file_count": len(existing),
        "current_scored_csv_exists": _exists(SCORED_CSV),
        "current_scored_csv": SCORED_CSV.replace("\\", "/"),
        "current_price_history_csv_exists": _exists(PRICE_HISTORY_CSV),
        "current_price_history_csv": PRICE_HISTORY_CSV.replace("\\", "/"),
        "current_run_summary_exists": _exists(RUN_SUMMARY_JSON),
        "current_date_range": (
            {"start": u["rs_date_start"], "end": u["rs_date_end"],
             "approx_years": current_years}
            if u["rs_date_start"] and u["rs_date_end"] else None),
        "current_ticker_count": n_tickers,
        "current_row_count": u["rs_row_count"],
        "benchmark_present": bool(u["rs_benchmark_present"]),
        "volume_present": bool(u["rs_volume_present"]),
        "satisfies_2kd_target_capacity": satisfies,
        "capacity_gap": capacity_gap,
    }


# --------------------------------------------------------------------------- #
# Feasibility by requirement
# --------------------------------------------------------------------------- #
def _required_for_candidate(u: Dict[str, Any], key: str, default: bool) -> bool:
    item = u["kd_requirements"].get(key, {}) or {}
    val = item.get("required_for_retesting_candidate")
    return bool(val) if isinstance(val, bool) else default


def build_feasibility_by_requirement(u: Dict[str, Any]) -> Dict[str, Any]:
    bk = u["twoka"]
    vol_now = bool(u["rs_volume_present"])
    bench_now = bool(u["rs_benchmark_present"])

    def entry(status, evidence, gap, risk, next_check, kd_key, default_required,
              backlog_key=None, future_phase=False):
        out = {
            "status": status,
            "evidence": evidence,
            "gap": gap,
            "risk": risk,
            "next_check": next_check,
            "required_for_retesting_candidate": _required_for_candidate(
                u, kd_key, default_required),
        }
        if future_phase:
            out["future_phase"] = True
        if backlog_key:
            src = _backlog_sources(bk, backlog_key)
            if src:
                out["candidate_sources_to_investigate"] = src
        return out

    return {
        "longer_price_history": entry(
            FEASIBLE_WITH_FREE_EXTENSION,
            "The existing free end-of-day export already supplies daily adjusted OHLCV "
            f"from {u['rs_date_start']} to {u['rs_date_end']}; the same free source can "
            "be pulled further back for listed names.",
            "Current history is only ~3 years (one regime); free extension must reach "
            "8-10 years including a sustained drawdown.",
            "Adjusted-close consistency across the join and corporate-action adjustment "
            "with no look-ahead must be verified on the longer span.",
            "Confirm how far back the free source returns adjusted OHLCV for the target "
            "universe and validate adjusted-close continuity.",
            "longer_price_history", True, backlog_key="longer_price_history"),

        "broader_equity_universe": entry(
            FEASIBLE_WITH_FREE_EXTENSION,
            f"The current export covers ~{u['rs_ticker_count']} mega-caps; a free source "
            "can return a materially broader current ticker list (toward the full liquid "
            "S&P 500).",
            "Breadth alone is free, but the broader list must be the right set per date, "
            "which depends on point-in-time membership (assessed separately).",
            "A naive current-membership list back-applied to history is survivorship "
            "biased and not admissible without an explicit caveat.",
            "Scope a broad liquid universe (>=100 names) the free source can supply and "
            "decide whether to carry a survivorship caveat.",
            "broader_equity_universe", True, backlog_key="broader_universe"),

        "point_in_time_universe_membership": entry(
            REQUIRES_PAID_POINT_IN_TIME_DATA,
            "No point-in-time constituent history exists locally; the current export is a "
            "fixed present-day ticker set.",
            "As-of-date membership is the scarce piece; clean point-in-time constituent "
            "history is generally a paid product.",
            "Back-applying today's membership injects look-ahead and survivorship bias "
            "into every historical cross-section.",
            "Compare free/low-cost point-in-time constituent options; otherwise plan an "
            "explicit survivorship caveat for a free build.",
            "point_in_time_universe_membership", True, backlog_key="broader_universe"),

        "survivorship_bias_controls": entry(
            REQUIRES_PAID_POINT_IN_TIME_DATA,
            "Free end-of-day sources typically drop delisted / merged names, so local "
            "free data cannot fully cover names over their live span.",
            "Clean delisted-name coverage is generally paid; the free path can only "
            "approximate it with an explicit survivorship caveat.",
            "Restricting to today's survivors inflates measured returns and any signal "
            "computed on the survivors.",
            "Check whether any free archive retains delisted bars; otherwise document the "
            "survivorship caveat the free build will carry.",
            "survivorship_bias_controls", True, backlog_key="longer_price_history"),

        "liquidity_and_volume_history": entry(
            FEASIBLE_WITH_FREE_EXTENSION if not vol_now else FEASIBLE_WITH_FREE_EXTENSION,
            "Daily volume is already present in the current export"
            + (" (volume_present=true)" if vol_now else "")
            + "; dollar-volume is derivable, and the same free source extends it over the "
            "longer span and broader universe.",
            "Must extend over the longer history and broader universe alongside the price "
            "history.",
            "Features must stay trailing-only; event-driven volume spikes leak if not "
            "lagged.",
            "Confirm volume coverage over the extended span for the broader universe.",
            "liquidity_and_volume_history", True,
            backlog_key="liquidity_and_volume_history"),

        "benchmark_and_factor_data": entry(
            FEASIBLE_NOW if bench_now else FEASIBLE_WITH_FREE_EXTENSION,
            f"The {u['rs_benchmark']} benchmark series is already present"
            + (" (benchmark_present=true)" if bench_now else "")
            + " and deep free history for a broad-market benchmark is readily available.",
            "Only needs extension back over the longer span for the residualization "
            "controls.",
            "Betas / vols must be trailing-only; factor libraries must be date-aligned.",
            "Confirm benchmark history depth and any free factor-return library coverage.",
            "benchmark_and_factor_data", True, backlog_key="benchmark_and_factor_data"),

        "sector_industry_classification": entry(
            FEASIBLE_WITH_FREE_EXTENSION,
            "A current free sector / industry snapshot is a usable starting "
            "approximation for neutralization and concentration control.",
            "Point-in-time reclassification history is harder to obtain than the current "
            "snapshot.",
            "Applying current classification to history misclassifies reclassified names.",
            "Decide whether the current snapshot is acceptable or a point-in-time mapping "
            "is needed for the candidate.",
            "sector_industry_classification", False,
            backlog_key="sector_industry_mapping"),

        "point_in_time_fundamentals": entry(
            REQUIRES_PAID_POINT_IN_TIME_DATA,
            "Not present locally and not required to retest a price/volume liquidity "
            "feature.",
            "True point-in-time / as-reported fundamentals are largely a paid product.",
            "Restated figures and report-date misalignment are the dominant factor-"
            "research leakage source.",
            "Defer until a later fundamental / quality-value hypothesis needs it.",
            "point_in_time_fundamentals", False,
            backlog_key="point_in_time_fundamentals", future_phase=True),

        "analyst_estimates_and_surprise": entry(
            REQUIRES_PAID_POINT_IN_TIME_DATA,
            "Not present locally and not required to retest the liquidity candidate.",
            "Revision-timestamped estimate history is largely a paid data product.",
            "Using final / current consensus for a past date imports the future.",
            "Defer until a later revision / drift hypothesis needs it.",
            "analyst_estimates_and_surprise", False,
            backlog_key="analyst_estimates", future_phase=True),
    }


# --------------------------------------------------------------------------- #
# Roll-ups
# --------------------------------------------------------------------------- #
def build_feasibility_summary(feas: Dict[str, Any]) -> Dict[str, Any]:
    counts: Dict[str, int] = {s: 0 for s in ALLOWED_STATUSES}
    for item in feas.values():
        counts[item["status"]] = counts.get(item["status"], 0) + 1

    required = {k: v for k, v in feas.items()
                if v["required_for_retesting_candidate"]}
    required_paid = [k for k, v in required.items()
                     if v["status"] == REQUIRES_PAID_POINT_IN_TIME_DATA]
    required_free_or_now = [
        k for k, v in required.items()
        if v["status"] in (FEASIBLE_NOW, FEASIBLE_WITH_FREE_EXTENSION,
                            FEASIBLE_WITH_LOW_COST_SOURCE)]

    # A strictly clean (non-caveated) retest needs the paid point-in-time pieces.
    clean_retest_status = (REQUIRES_PAID_POINT_IN_TIME_DATA if required_paid
                           else FEASIBLE_WITH_FREE_EXTENSION)
    # A survivorship-caveated free retest is attemptable when every *required* item is
    # either feasible free/now or only blocked by the point-in-time membership /
    # survivorship pieces (which the caveat explicitly covers).
    caveat_covered = {"point_in_time_universe_membership", "survivorship_bias_controls"}
    caveated_blockers = [k for k in required_paid if k not in caveat_covered]
    caveated_free_retest_status = (FEASIBLE_WITH_FREE_EXTENSION
                                   if not caveated_blockers else NOT_FEASIBLE_YET)

    return {
        "status_counts": counts,
        "required_for_candidate": sorted(required.keys()),
        "required_feasible_free_or_now": sorted(required_free_or_now),
        "required_requiring_paid_for_clean_build": sorted(required_paid),
        "clean_retest_status": clean_retest_status,
        "caveated_free_retest_status": caveated_free_retest_status,
        "headline": (
            "The price/volume pieces required to retest the liquidity candidate (longer "
            "adjusted OHLCV, broader current universe, volume, benchmark) are feasible "
            "on the free path. Clean point-in-time membership and survivorship coverage "
            "are the only paid-leaning gaps; a survivorship-caveated free build is "
            "attemptable now, while a strictly clean build would require paid "
            "point-in-time data."
        ),
    }


def build_blockers(feas: Dict[str, Any]) -> List[Dict[str, Any]]:
    blockers = []
    for key, item in feas.items():
        if (item["status"] == REQUIRES_PAID_POINT_IN_TIME_DATA
                and item["required_for_retesting_candidate"]):
            blockers.append({
                "requirement": key,
                "why": item["gap"],
                "severity": "blocks_clean_build_only",
                "workaround": (
                    "Carry an explicit survivorship caveat and report the retest as "
                    "survivorship-biased; reserve a paid point-in-time dataset for a "
                    "later clean confirmation."),
            })
    return blockers


def build_recommended_source_strategy(u: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ordered_preference": [
            {
                "step": 1,
                "action": "Extend the existing free end-of-day export backward to 8-10 "
                          "years and widen it to a broad liquid universe (>=100 names).",
                "cost": "free",
                "point_in_time_membership": False,
                "carries_survivorship_caveat": True,
            },
            {
                "step": 2,
                "action": "Investigate a free / low-cost point-in-time constituent "
                          "history and any free archive that retains delisted bars.",
                "cost": "low",
                "point_in_time_membership": "partial",
                "carries_survivorship_caveat": True,
            },
            {
                "step": 3,
                "action": "Reserve a paid point-in-time + survivorship dataset for a "
                          "later clean confirmation, only if a caveated free result is "
                          "promising. Decision deferred; no paid data acquired now.",
                "cost": "paid",
                "point_in_time_membership": True,
                "carries_survivorship_caveat": False,
                "decision": "later",
            },
        ],
        "rationale": (
            "The candidate is a price/volume liquidity feature, so the free path can "
            "supply every required input except clean point-in-time membership and "
            "survivorship coverage, which a documented caveat covers for a first retest."
        ),
    }


def build_proposed_data_build_scope(u: Dict[str, Any]) -> Dict[str, Any]:
    cap = u["kd_target_capacity"]
    return {
        "note": "This is the SCOPE the next phase (2K-F) would design a free build for. "
                "No data is built, downloaded, or acquired in this feasibility phase.",
        "target_years_min": u["kd_min_years"],
        "target_years_recommended": u["kd_recommended_years"],
        "target_min_tickers_per_date": u["kd_min_tickers"],
        "columns": [
            "date", "ticker", "adjusted_open", "adjusted_high", "adjusted_low",
            "adjusted_close", "volume", "dollar_volume", "benchmark_close",
        ],
        "universe_definition": "Broad liquid universe (toward the full S&P 500); current "
                               "membership used as a free approximation with an explicit "
                               "survivorship caveat.",
        "survivorship_handling": "Retain any delisted bars a free source provides; where "
                                 "unavailable, document the survivorship caveat rather "
                                 "than silently restricting to survivors.",
        "must_span_regimes": (cap.get("regimes_required")
                              if isinstance(cap.get("regimes_required"), list)
                              else ["market_up", "market_down", "high_volatility",
                                    "low_volatility", "at_least_one_sustained_drawdown"]),
        "builds_executed_now": False,
        "network_calls_made": False,
        "paid_data_acquired": False,
    }


def build_data_quality_preflight_plan(u: Dict[str, Any]) -> Dict[str, Any]:
    checks = []
    for g in u["kd_data_quality_gates"]:
        gid = g.get("id")
        if not gid:
            continue
        checks.append({
            "gate_id": gid,
            "preflight_action": g.get("check") or g.get("description")
            or "Validate this gate on the assembled dataset before any retest.",
        })
    checks.append({
        "gate_id": "survivorship_caveat_documented",
        "preflight_action": "If point-in-time membership is not sourced, assert that the "
                            "dataset metadata records an explicit survivorship caveat so "
                            "the retest is reported as survivorship-biased.",
    })
    return {
        "note": "These preflight checks must pass on the assembled dataset BEFORE any "
                "residual-signal retest. They are gates on the data itself, run by the "
                "later build phase, not executed here.",
        "checks": checks,
    }


def build_decision(u: Dict[str, Any], summary: Dict[str, Any]) -> Dict[str, Any]:
    caveated_ok = summary["caveated_free_retest_status"] != NOT_FEASIBLE_YET
    clean_needs_paid = (summary["clean_retest_status"]
                        == REQUIRES_PAID_POINT_IN_TIME_DATA)
    cand = u["candidate"]

    can_build = caveated_ok          # a survivorship-caveated free build is assemblable
    can_retest_candidate = caveated_ok  # liquidity feature needs no paid fundamentals
    paid_required_now = False        # paid PIT reserved for a later clean confirmation
    proceed = can_build

    reason = (
        f"The price/volume inputs required to retest {cand} (longer adjusted OHLCV, a "
        f"broader current universe, volume, benchmark) are feasible on the free path, so "
        f"a survivorship-caveated expanded dataset can be assembled without paid data. "
        f"Clean point-in-time membership and survivorship coverage are the only "
        f"paid-leaning gaps"
        + (" (a strictly clean build would require paid point-in-time data)"
           if clean_needs_paid else "")
        + f"; that gap is covered for a first retest by an explicit survivorship caveat. "
        f"Paid data is therefore not required now and is reserved for a later clean "
        f"confirmation. Proceeding to a free data-build plan is warranted; no model "
        f"research and no model candidate are authorized here."
    )

    return {
        "can_build_expanded_dataset_without_paid_data": bool(can_build),
        "can_retest_avg_dollar_volume_without_paid_data": bool(can_retest_candidate),
        "paid_data_required_now": bool(paid_required_now),
        "proceed_to_data_build": bool(proceed),
        "clean_build_requires_paid_point_in_time_data": bool(clean_needs_paid),
        "reason": reason,
    }


def build_recommended_next_phase(decision: Dict[str, Any]) -> Dict[str, str]:
    """Route to 2K-F. If the free route can plausibly retest the candidate (even with a
    survivorship caveat), design the free build; otherwise resolve the point-in-time
    source question first."""
    if (decision["can_retest_avg_dollar_volume_without_paid_data"]
            and decision["proceed_to_data_build"]):
        return {
            "phase": "2K-F",
            "title": "Free Expanded Price/Volume Dataset Build Plan",
            "purpose": (
                "Design a local / free data-build script plan for longer daily adjusted "
                "OHLCV and a broader universe (carrying an explicit survivorship caveat), "
                "with the data-quality preflight checks, so the Phase 2K-B / 2K-C / 2L "
                "battery can be re-run on a multi-regime panel. Still no model training "
                "and no model candidate."),
        }
    return {
        "phase": "2K-F",
        "title": "Point-in-Time Universe Source Decision",
        "purpose": (
            "Compare free / low-cost / paid options for point-in-time universe "
            "membership and survivorship coverage before any data build, because clean "
            "membership gaps would otherwise block a meaningful retest. Acquires no paid "
            "data, trains no model, and creates no model candidate."),
    }


def build_upstream_summary(u: Dict[str, Any]) -> Dict[str, Any]:
    dec = u["kd_decision"]
    return {
        "phase": "2K-D",
        "upstream_2lb_recommendation": u["upstream_2lb_recommendation"],
        "candidate": u["candidate"],
        "continue_model_research_now": dec.get("continue_model_research_now"),
        "create_model_candidate_now": dec.get("create_model_candidate_now"),
        "kd_recommended_next_phase": u["kd_recommended_next_phase"].get("phase"),
        "target_min_years_history": u["kd_min_years"],
        "target_recommended_years_history": u["kd_recommended_years"],
        "target_min_tickers_per_date": u["kd_min_tickers"],
        "required_requirements": sorted(
            k for k, v in u["kd_requirements"].items()
            if (v or {}).get("required_for_retesting_candidate") is True),
    }


def build_interpretation(u: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "candidate": u["candidate"],
        "continue_model_research_now": False,
        "create_model_candidate_now": False,
        "model_trained": False,
        "model_fitted": False,
        "model_candidate_created": False,
        "authorized_to_train_model": False,
        "authorized_to_serve_model": False,
        "production_edge_claimed": False,
        "data_acquired": False,
        "paid_data_acquired": False,
        "network_calls_made": False,
        "builds_executed_now": False,
        "narrative": (
            f"Phase 2K-E checks only whether the Phase 2K-D expanded dataset can be "
            f"assembled locally / free / low-cost. It inventories the existing free "
            f"export, finds the price/volume inputs needed to retest {u['candidate']} "
            f"feasible on the free path, and identifies clean point-in-time membership / "
            f"survivorship coverage as the only paid-leaning gaps (covered for a first "
            f"retest by an explicit survivorship caveat). No data is downloaded or "
            f"acquired, no paid data is purchased, no model is trained or served, and no "
            f"model candidate is created. No production edge is claimed."
        ),
    }


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_results() -> Dict[str, Any]:
    u = load_upstream()
    inventory = build_local_data_inventory(u)
    feasibility = build_feasibility_by_requirement(u)
    summary = build_feasibility_summary(feasibility)
    blockers = build_blockers(feasibility)
    decision = build_decision(u, summary)
    return {
        "phase": PHASE,
        "generated_at": dt.datetime.now().replace(microsecond=0).isoformat(),
        "inputs_read": {
            "phase2k_d_json": INPUT_2KD_JSON,
            "phase2l_b_json": INPUT_2LB_JSON,
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
        "upstream_phase2k_d_summary": build_upstream_summary(u),
        "local_data_inventory": inventory,
        "feasibility_by_requirement": feasibility,
        "feasibility_summary": summary,
        "blockers": blockers,
        "recommended_source_strategy": build_recommended_source_strategy(u),
        "proposed_data_build_scope": build_proposed_data_build_scope(u),
        "data_quality_preflight_plan": build_data_quality_preflight_plan(u),
        "decision": decision,
        "interpretation": build_interpretation(u, decision),
        "recommended_next_phase": build_recommended_next_phase(decision),
    }


def write_results(results: Dict[str, Any], path: str = RESULTS_JSON) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, allow_nan=False)


def run(output_path: str = RESULTS_JSON) -> Dict[str, Any]:
    """Build and write the feasibility artifact."""
    results = build_results()
    write_results(results, output_path)
    return results


def main() -> int:
    t0 = time.perf_counter()
    d = run()
    elapsed = time.perf_counter() - t0
    inv = d["local_data_inventory"]
    summ = d["feasibility_summary"]
    dec = d["decision"]
    nxt = d["recommended_next_phase"]
    print(f"[phase2k-e] candidate                 : "
          f"{d['upstream_phase2k_d_summary']['candidate']}")
    print(f"[phase2k-e] local price/scored/summary : "
          f"{inv['current_price_history_csv_exists']} / "
          f"{inv['current_scored_csv_exists']} / {inv['current_run_summary_exists']}")
    print(f"[phase2k-e] current range / tickers     : "
          f"{inv.get('current_date_range')} / {inv['current_ticker_count']}")
    print(f"[phase2k-e] satisfies 2K-D capacity      : "
          f"{inv['satisfies_2kd_target_capacity']}")
    print(f"[phase2k-e] clean / caveated retest      : "
          f"{summ['clean_retest_status']} / {summ['caveated_free_retest_status']}")
    print(f"[phase2k-e] can build w/o paid data      : "
          f"{dec['can_build_expanded_dataset_without_paid_data']}")
    print(f"[phase2k-e] paid data required now       : {dec['paid_data_required_now']}")
    print(f"[phase2k-e] proceed to data build        : {dec['proceed_to_data_build']}")
    print(f"[phase2k-e] recommended next phase       : {nxt['phase']} ({nxt['title']})")
    print(f"[phase2k-e] results written              : {RESULTS_JSON}")
    print(f"[phase2k-e] elapsed seconds              : {elapsed:.2f}")
    print("[phase2k-e] feasibility only; no data acquired, no model; safety flags in JSON.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
