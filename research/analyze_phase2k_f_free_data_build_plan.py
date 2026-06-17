"""Phase 2K-F Free Expanded Price/Volume Dataset Build Plan (offline, read-only).

Phase 2K-E checked whether the Phase 2K-D expanded dataset can be assembled locally /
free / low-cost. It found the price/volume inputs needed to retest the liquidity
candidate (longer adjusted OHLCV, a broader current universe, volume, benchmark) feasible
on the free path, identified clean point-in-time membership / survivorship coverage as the
only paid-leaning gaps (covered for a first retest by an explicit survivorship caveat),
decided paid data is not required now, and routed here.

This phase is a BUILD PLAN, not the data build. It reads the Phase 2K-E feasibility result
plus the Phase 2K-D plan, the Phase 2K-A backlog, and the Phase 2G run summary, and writes
one planning JSON describing exactly how a LATER phase (2K-G) would assemble an 8-10 year,
broader-universe, survivorship-caveated daily adjusted OHLCV dataset for retesting the
candidate: the build scope, the future build script's inputs / outputs, the local ticker
universe plan, the free retrieval design, the data-quality checks, the pass/fail gates, the
survivorship-caveat policy, and the implementation sequence.

It builds NOTHING. It inspects only local files via path / directory checks. It makes no
network call, fetches no data, acquires no paid data, creates no build script, creates no
future data file, touches no datastore, trains no model, and creates no model candidate.
The machine-readable safety flags are emitted in the JSON; the full guardrail rationale
lives in docs/phase2k_f_free_data_build_plan_v1.md, not in this source.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import time
from typing import Any, Dict, List, Optional

PHASE = "2K-F"

_OUTPUT_DIR = os.path.join("research", "output")
_INPUT_DIR = os.path.join("research", "input")

# Inputs (read-only).
INPUT_2KE_JSON = os.path.join(_OUTPUT_DIR, "phase2k_e_data_feasibility.json")
INPUT_2KD_JSON = os.path.join(_OUTPUT_DIR, "phase2k_d_data_expansion_plan.json")
INPUT_2KA_JSON = os.path.join(_OUTPUT_DIR, "phase2k_alpha_backlog.json")
RUN_SUMMARY_JSON = os.path.join(_OUTPUT_DIR, "phase2g_c_real_data_run_summary.json")

# Future artifacts the NEXT phase (2K-G) would create. They are DECLARED here only; this
# planning phase does not create any of them.
FUTURE_PRICE_HISTORY_CSV = os.path.join(
    _OUTPUT_DIR, "phase2k_g_expanded_price_history_free.csv")
FUTURE_SCORED_CSV = os.path.join(_OUTPUT_DIR, "phase2k_g_expanded_scored_free.csv")
FUTURE_DATA_QUALITY_JSON = os.path.join(_OUTPUT_DIR, "phase2k_g_data_quality_report.json")
FUTURE_BUILD_SUMMARY_JSON = os.path.join(_OUTPUT_DIR, "phase2k_g_data_build_summary.json")
FUTURE_SURVIVORSHIP_JSON = os.path.join(_OUTPUT_DIR, "phase2k_g_survivorship_caveat.json")
FUTURE_BUILD_SCRIPT = os.path.join("research", "build_phase2k_g_free_expanded_dataset.py")
FUTURE_UNIVERSE_CSV = os.path.join(_INPUT_DIR, "phase2k_g_free_universe_tickers.csv")

# The single output this analyzer is allowed to write.
RESULTS_JSON = os.path.join(_OUTPUT_DIR, "phase2k_f_free_data_build_plan.json")

# Allowed status vocabulary for the future build's data-quality report.
DQ_REPORT_STATUSES = ("PASS", "PASS_WITH_CAVEAT", "FAIL")


# --------------------------------------------------------------------------- #
# Read-only helpers
# --------------------------------------------------------------------------- #
def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _exists(path: str) -> bool:
    return os.path.isfile(path)


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _round(x: Any, n: int = 2) -> Optional[float]:
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
    twoke = _read_json(INPUT_2KE_JSON)
    twokd = _read_json(INPUT_2KD_JSON)
    twoka = _read_json(INPUT_2KA_JSON)
    run_summary = _read_json(RUN_SUMMARY_JSON)

    ke_decision = twoke.get("decision", {}) or {}
    ke_inventory = twoke.get("local_data_inventory", {}) or {}
    ke_feasibility = twoke.get("feasibility_by_requirement", {}) or {}
    ke_summary = twoke.get("feasibility_summary", {}) or {}
    ke_scope = twoke.get("proposed_data_build_scope", {}) or {}
    ke_preflight = twoke.get("data_quality_preflight_plan", {}) or {}
    ke_source_strategy = twoke.get("recommended_source_strategy", {}) or {}
    ke_next = twoke.get("recommended_next_phase", {}) or {}
    ke_interp = twoke.get("interpretation", {}) or {}

    candidate = (ke_interp.get("candidate")
                 or (twoke.get("upstream_phase2k_d_summary", {}) or {}).get("candidate"))

    # Target capacity: prefer the 2K-E scope, fall back to the 2K-D capacity block.
    kd_cap = twokd.get("target_validation_capacity", {}) or {}
    min_years = ke_scope.get("target_years_min", kd_cap.get("min_years_history"))
    rec_years = ke_scope.get("target_years_recommended",
                             kd_cap.get("recommended_years_history"))
    min_tickers = ke_scope.get("target_min_tickers_per_date",
                               kd_cap.get("min_tickers_per_date"))

    return {
        "twoke": twoke,
        "twokd": twokd,
        "twoka": twoka,
        "run_summary": run_summary,
        "candidate": candidate,
        "ke_decision": ke_decision,
        "ke_inventory": ke_inventory,
        "ke_feasibility": ke_feasibility,
        "ke_summary": ke_summary,
        "ke_scope": ke_scope,
        "ke_preflight": ke_preflight,
        "ke_source_strategy": ke_source_strategy,
        "ke_next": ke_next,
        "min_years": min_years,
        "recommended_years": rec_years,
        "min_tickers": min_tickers,
        "columns": (ke_scope.get("columns") if isinstance(ke_scope.get("columns"), list)
                    else ["date", "ticker", "adjusted_open", "adjusted_high",
                          "adjusted_low", "adjusted_close", "volume", "dollar_volume",
                          "benchmark_close"]),
        "must_span_regimes": (ke_scope.get("must_span_regimes")
                              if isinstance(ke_scope.get("must_span_regimes"), list)
                              else ["market_up", "market_down", "high_volatility",
                                    "low_volatility", "at_least_one_sustained_drawdown"]),
        "rs_benchmark": run_summary.get("benchmark") or "SPY",
        "rs_date_start": run_summary.get("date_start"),
        "rs_date_end": run_summary.get("date_end"),
        "rs_ticker_count": run_summary.get("ticker_count")
        or run_summary.get("universe_ticker_count"),
    }


# --------------------------------------------------------------------------- #
# Upstream summary + routing confirmation (research method step 1 & 2)
# --------------------------------------------------------------------------- #
def build_upstream_summary(u: Dict[str, Any]) -> Dict[str, Any]:
    dec = u["ke_decision"]
    summ = u["ke_summary"]
    nxt = u["ke_next"]
    routed_to_2k_f = (nxt.get("phase") == "2K-F")
    return {
        "phase": "2K-E",
        "candidate": u["candidate"],
        "can_build_expanded_dataset_without_paid_data":
            dec.get("can_build_expanded_dataset_without_paid_data"),
        "can_retest_avg_dollar_volume_without_paid_data":
            dec.get("can_retest_avg_dollar_volume_without_paid_data"),
        "paid_data_required_now": dec.get("paid_data_required_now"),
        "proceed_to_data_build": dec.get("proceed_to_data_build"),
        "clean_build_requires_paid_point_in_time_data":
            dec.get("clean_build_requires_paid_point_in_time_data"),
        "clean_retest_status": summ.get("clean_retest_status"),
        "caveated_free_retest_status": summ.get("caveated_free_retest_status"),
        "ke_recommended_next_phase": nxt.get("phase"),
        "ke_recommended_next_phase_title": nxt.get("title"),
        "routed_to_2k_f": routed_to_2k_f,
        "target_min_years_history": u["min_years"],
        "target_recommended_years_history": u["recommended_years"],
        "target_min_tickers_per_date": u["min_tickers"],
        "current_panel": {
            "date_range": u["ke_inventory"].get("current_date_range"),
            "ticker_count": u["ke_inventory"].get("current_ticker_count"),
            "satisfies_2kd_target_capacity":
                u["ke_inventory"].get("satisfies_2kd_target_capacity"),
        },
    }


# --------------------------------------------------------------------------- #
# Build scope (research method step 3)
# --------------------------------------------------------------------------- #
def build_scope(u: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "note": "Concrete scope a LATER phase (2K-G) would build to. Nothing is built, "
                "fetched, or acquired here.",
        "target_history_years_min": u["min_years"],
        "target_history_years_recommended": u["recommended_years"],
        "target_min_tickers_per_date": u["min_tickers"],
        "target_universe": "Broad liquid U.S. equities (S&P 500-style), at least 100 "
                           "tickers per date.",
        "frequency": "daily",
        "adjusted_ohlcv": True,
        "columns": u["columns"],
        "benchmark_ticker": u["rs_benchmark"],
        "must_span_regimes": u["must_span_regimes"],
        "survivorship_caveat": {
            "explicitly_documented": True,
            "no_false_point_in_time_membership_claim": True,
            "clean_point_in_time_build_deferred": True,
            "reported_as_survivorship_biased": True,
        },
        "builds_executed_now": False,
        "data_fetched_now": False,
        "paid_data_acquired_now": False,
    }


# --------------------------------------------------------------------------- #
# Future build-script inputs / outputs (research method steps 3 & 4)
# --------------------------------------------------------------------------- #
def build_future_inputs(u: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "note": "Arguments the future 2K-G build script would accept. The ticker list is "
                "a LOCAL / MANUAL file, never a network source.",
        "ticker_list_file": _norm(FUTURE_UNIVERSE_CSV),
        "start_date": "earliest date reaching >=8 (ideally 10) years before today",
        "end_date": u["rs_date_end"] or "most recent completed trading day",
        "benchmark_ticker": u["rs_benchmark"],
        "output_directory": _norm(_OUTPUT_DIR),
    }


def _future_output(path: str, role: str) -> Dict[str, Any]:
    return {
        "path": _norm(path),
        "role": role,
        "exists_now": _exists(path),
        "created_by_this_phase": False,
    }


def build_future_outputs() -> Dict[str, Any]:
    return {
        "note": "Files the future 2K-G build script would write. They are DECLARED here "
                "only; this planning phase creates none of them (exists_now must be false "
                "for all unless a later phase has already run).",
        "expanded_price_history_csv": _future_output(
            FUTURE_PRICE_HISTORY_CSV,
            "Longer, broader daily adjusted OHLCV + volume panel."),
        "expanded_scored_csv": _future_output(
            FUTURE_SCORED_CSV,
            "Feature-ready / scored panel derived from the expanded price history."),
        "data_quality_report_json": _future_output(
            FUTURE_DATA_QUALITY_JSON,
            "Result of the data-quality checks with an overall PASS / PASS_WITH_CAVEAT / "
            "FAIL status."),
        "data_build_summary_json": _future_output(
            FUTURE_BUILD_SUMMARY_JSON,
            "Reproducible run metadata: date range, ticker count, row count, source, "
            "parameters."),
        "survivorship_caveat_json": _future_output(
            FUTURE_SURVIVORSHIP_JSON,
            "Explicit survivorship-caveat metadata documenting that membership is "
            "current-as-of, not point-in-time."),
    }


def build_future_script_plan(u: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "note": "Plan for the future build script. The script itself is NOT created in "
                "this phase.",
        "path": _norm(FUTURE_BUILD_SCRIPT),
        "exists_now": _exists(FUTURE_BUILD_SCRIPT),
        "created_by_this_phase": False,
        "responsibilities": [
            "Read the local ticker universe file and the run parameters.",
            "Retrieve free daily adjusted OHLCV + volume per ticker in chunks.",
            "Assemble a single long-format panel keyed on (date, ticker).",
            "Derive dollar_volume and align the benchmark series.",
            "Run the data-quality checks and emit the data-quality report.",
            "Write the survivorship-caveat metadata file.",
            "Write a reproducible build-summary JSON with explicit run metadata.",
        ],
        "must_not": [
            "write to any database",
            "integrate with the live service",
            "score or serve any model",
            "create orders or automation",
            "acquire any paid data",
            "claim point-in-time universe membership",
        ],
        "reads": {"ticker_universe_file": _norm(FUTURE_UNIVERSE_CSV)},
        "writes": [
            _norm(FUTURE_PRICE_HISTORY_CSV),
            _norm(FUTURE_SCORED_CSV),
            _norm(FUTURE_DATA_QUALITY_JSON),
            _norm(FUTURE_BUILD_SUMMARY_JSON),
            _norm(FUTURE_SURVIVORSHIP_JSON),
        ],
    }


# --------------------------------------------------------------------------- #
# Ticker universe plan (research method step 6)
# --------------------------------------------------------------------------- #
def build_ticker_universe_plan() -> Dict[str, Any]:
    return {
        "note": "The next phase sources tickers from a LOCAL / MANUAL file, never from a "
                "network call. A current-membership list is acceptable ONLY with an "
                "explicit survivorship caveat.",
        "source_must_be_local_or_manual": True,
        "network_ticker_source_allowed": False,
        "proposed_universe_file": _norm(FUTURE_UNIVERSE_CSV),
        "proposed_universe_file_exists_now": _exists(FUTURE_UNIVERSE_CSV),
        "created_by_this_phase": False,
        "columns": ["ticker", "name", "sector", "source", "active_as_of",
                    "survivorship_caveat"],
        "current_membership_acceptable_with_caveat_only": True,
        "membership_caveat": "Using a current-membership list back-applied to history is "
                             "survivorship biased; the active_as_of and survivorship_"
                             "caveat columns must record this explicitly.",
    }


# --------------------------------------------------------------------------- #
# Free data retrieval design (research method step 7)
# --------------------------------------------------------------------------- #
def build_free_data_retrieval_design() -> Dict[str, Any]:
    return {
        "note": "Design for the future free retrieval. Not executed here; no network call "
                "is made by this planning phase.",
        "principles": [
            "chunked per-ticker retrieval in batches",
            "retry with exponential backoff on transient failures",
            "local on-disk cache keyed by ticker so reruns avoid refetching",
            "idempotent output writes (same inputs reproduce the same files)",
            "no database writes",
            "no live-service integration",
            "no model scoring",
            "reproducible build-summary JSON",
            "explicit run metadata (parameters, timestamps, source label, counts)",
        ],
        "executed_now": False,
        "network_calls_made": False,
        "paid_data_acquired": False,
    }


# --------------------------------------------------------------------------- #
# Data-quality checks (research method step 8) — carries the 2K-E preflight gates forward
# --------------------------------------------------------------------------- #
def build_data_quality_checks(u: Dict[str, Any]) -> List[Dict[str, Any]]:
    # The build-specific checks the future builder must run on the assembled panel.
    checks: List[Dict[str, Any]] = [
        {"check_id": "adjusted_close_continuity",
         "description": "Adjusted-close series is continuous with no unexplained jumps "
                        "across the join."},
        {"check_id": "duplicate_date_ticker_rows",
         "description": "No duplicate rows on the (date, ticker) key."},
        {"check_id": "missing_adjusted_close",
         "description": "Missing adjusted-close values stay below threshold per ticker."},
        {"check_id": "missing_volume",
         "description": "Missing volume values stay below threshold per ticker."},
        {"check_id": "outlier_daily_returns",
         "description": "Daily returns outside a sane bound are flagged for review."},
        {"check_id": "zero_or_negative_prices",
         "description": "No zero or negative adjusted prices."},
        {"check_id": "zero_or_negative_volume_unexpected",
         "description": "Zero / negative volume only where genuinely expected (e.g. halts)."},
        {"check_id": "ticker_level_date_coverage",
         "description": "Each ticker covers enough of its live span against the calendar."},
        {"check_id": "benchmark_coverage",
         "description": "Benchmark series spans the full panel date range."},
        {"check_id": "minimum_years_achieved",
         "description": f"Panel spans at least {u['min_years']} years."},
        {"check_id": "minimum_ticker_count_achieved",
         "description": f"At least {u['min_tickers']} tickers per date."},
        {"check_id": "no_forward_filled_labels",
         "description": "Target labels use only realized future prices, never forward fill."},
        {"check_id": "no_point_in_time_membership_claim",
         "description": "No claim of point-in-time membership; current-as-of only."},
        {"check_id": "survivorship_caveat_present",
         "description": "Survivorship-caveat metadata file is present and populated."},
    ]
    # Carry the Phase 2K-E preflight gate ids forward so the plan stays linked to them.
    pre = u["ke_preflight"].get("checks", []) if isinstance(u["ke_preflight"], dict) else []
    carried = sorted({c.get("gate_id") for c in pre if c.get("gate_id")})
    for c in checks:
        if c["check_id"] in carried or any(c["check_id"].startswith(g) for g in carried):
            c["derived_from_2k_e_gate"] = True
    return checks


# --------------------------------------------------------------------------- #
# Pass/fail gates for the future build (research method step 9)
# --------------------------------------------------------------------------- #
def build_future_build_pass_fail_gates(u: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "note": "Gates the future 2K-G build must satisfy before its output is usable for "
                "any retest. Evaluated by the build phase, not here.",
        "gates": [
            {"gate_id": "required_files_created",
             "rule": "All five future output files are written."},
            {"gate_id": "min_years",
             "rule": f"Panel spans at least {u['min_years']} years of daily bars."},
            {"gate_id": "min_tickers",
             "rule": f"At least {u['min_tickers']} tickers per date."},
            {"gate_id": "benchmark_coverage_complete",
             "rule": "Benchmark coverage is complete enough across the panel range."},
            {"gate_id": "duplicate_rate_zero",
             "rule": "Duplicate (date, ticker) rate is exactly zero."},
            {"gate_id": "missing_critical_fields_below_threshold",
             "rule": "Missing adjusted-close / volume rates stay below threshold."},
            {"gate_id": "survivorship_caveat_file_present",
             "rule": "The survivorship-caveat metadata file exists and is populated."},
            {"gate_id": "data_quality_report_status",
             "rule": "data_quality_report.status is one of PASS / PASS_WITH_CAVEAT / FAIL.",
             "allowed_status": list(DQ_REPORT_STATUSES)},
        ],
    }


def build_survivorship_caveat_policy() -> Dict[str, Any]:
    return {
        "explicitly_documented": True,
        "no_false_point_in_time_membership_claim": True,
        "clean_point_in_time_build_deferred": True,
        "reported_as_survivorship_biased": True,
        "caveat_metadata_file": _norm(FUTURE_SURVIVORSHIP_JSON),
        "policy": "The free build uses current-as-of membership and whatever delisted "
                  "coverage a free source provides. Any retest run on it must be reported "
                  "as survivorship-biased, never as a clean point-in-time result. A clean "
                  "point-in-time build is deferred to a later paid-source decision.",
    }


def build_implementation_sequence() -> List[Dict[str, Any]]:
    return [
        {"step": 1, "action": "Phase 2K-G: create the local ticker-universe file with the "
                              "planned columns and the survivorship caveat recorded."},
        {"step": 2, "action": "Phase 2K-G: implement the free build script per this plan "
                              "(chunked retrieval, retry/backoff, local cache, idempotent "
                              "writes)."},
        {"step": 3, "action": "Phase 2K-G: assemble the expanded price-history panel and "
                              "derive dollar-volume + benchmark alignment."},
        {"step": 4, "action": "Phase 2K-G: run the data-quality checks and emit the "
                              "data-quality report and survivorship-caveat metadata."},
        {"step": 5, "action": "Phase 2K-G: evaluate the pass/fail gates; only a passing "
                              "(or PASS_WITH_CAVEAT) panel is eligible for retesting."},
        {"step": 6, "action": "Later: re-run the Phase 2K-B / 2K-C / 2L residual-signal "
                              "battery on the expanded panel. Still no model candidate "
                              "and no production integration."},
    ]


# --------------------------------------------------------------------------- #
# Decision + routing (research method steps 11 & 12)
# --------------------------------------------------------------------------- #
def build_decision(u: Dict[str, Any]) -> Dict[str, Any]:
    cand = u["candidate"]
    return {
        "create_build_script_now": False,
        "execute_data_build_now": False,
        "download_data_now": False,
        "acquire_paid_data_now": False,
        "create_model_candidate_now": False,
        "reason": (
            f"Phase 2K-F only produces the build plan for a free, survivorship-caveated "
            f"expanded price/volume dataset to later retest {cand}. The actual build "
            f"script, the data fetch, and the dataset are deferred to Phase 2K-G. No data "
            f"is fetched, no paid data is acquired, no model candidate is created, and no "
            f"production integration occurs here."
        ),
    }


def build_recommended_next_phase() -> Dict[str, str]:
    return {
        "phase": "2K-G",
        "title": "Free Expanded Price/Volume Dataset Builder",
        "purpose": (
            "Implement the local / free expanded dataset builder using this Phase 2K-F "
            "plan: assemble the 8-10 year, broader-universe, survivorship-caveated daily "
            "adjusted OHLCV panel, run the data-quality checks and pass/fail gates, and "
            "emit the build summary and survivorship-caveat metadata. Still no model "
            "training, no model candidate, and no production integration."),
    }


def build_interpretation(u: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "candidate": u["candidate"],
        "create_build_script_now": False,
        "execute_data_build_now": False,
        "download_data_now": False,
        "acquire_paid_data_now": False,
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
        "future_files_created_now": False,
        "narrative": (
            f"Phase 2K-F turns the Phase 2K-E feasibility result into a concrete, "
            f"deferred build plan for a free, survivorship-caveated expanded price/volume "
            f"dataset to later retest {u['candidate']}. It writes one planning JSON, "
            f"declares (but does not create) the future build script, the future ticker "
            f"universe file, and the five future output files, and specifies the "
            f"data-quality checks and pass/fail gates the later build must satisfy. It "
            f"builds nothing, fetches nothing, acquires no paid data, trains no model, "
            f"and creates no model candidate. No production edge is claimed."
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
            "phase2k_e_json": INPUT_2KE_JSON,
            "phase2k_d_json": INPUT_2KD_JSON,
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
        "upstream_phase2k_e_summary": build_upstream_summary(u),
        "build_scope": build_scope(u),
        "future_inputs": build_future_inputs(u),
        "future_outputs": build_future_outputs(),
        "future_script_plan": build_future_script_plan(u),
        "ticker_universe_plan": build_ticker_universe_plan(),
        "free_data_retrieval_design": build_free_data_retrieval_design(),
        "data_quality_checks": build_data_quality_checks(u),
        "future_build_pass_fail_gates": build_future_build_pass_fail_gates(u),
        "survivorship_caveat_policy": build_survivorship_caveat_policy(),
        "implementation_sequence": build_implementation_sequence(),
        "decision": build_decision(u),
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
    """Build and write the build-plan artifact. Creates only the plan JSON."""
    results = build_results()
    write_results(results, output_path)
    return results


def main() -> int:
    t0 = time.perf_counter()
    d = run()
    elapsed = time.perf_counter() - t0
    up = d["upstream_phase2k_e_summary"]
    dec = d["decision"]
    nxt = d["recommended_next_phase"]
    fo = d["future_outputs"]
    print(f"[phase2k-f] candidate                  : {up['candidate']}")
    print(f"[phase2k-f] 2K-E routed to 2K-F         : {up['routed_to_2k_f']} "
          f"({up['ke_recommended_next_phase']} / {up['ke_recommended_next_phase_title']})")
    print(f"[phase2k-f] target years (min/rec)      : "
          f"{d['build_scope']['target_history_years_min']} / "
          f"{d['build_scope']['target_history_years_recommended']}")
    print(f"[phase2k-f] target min tickers/date     : "
          f"{d['build_scope']['target_min_tickers_per_date']}")
    print(f"[phase2k-f] future build script exists  : "
          f"{d['future_script_plan']['exists_now']} "
          f"({d['future_script_plan']['path']})")
    print(f"[phase2k-f] future outputs exist now    : "
          f"{[v['exists_now'] for v in fo.values() if isinstance(v, dict)]}")
    print(f"[phase2k-f] create build script now     : {dec['create_build_script_now']}")
    print(f"[phase2k-f] execute data build now      : {dec['execute_data_build_now']}")
    print(f"[phase2k-f] download data now           : {dec['download_data_now']}")
    print(f"[phase2k-f] acquire paid data now       : {dec['acquire_paid_data_now']}")
    print(f"[phase2k-f] recommended next phase      : {nxt['phase']} ({nxt['title']})")
    print(f"[phase2k-f] results written             : {RESULTS_JSON}")
    print(f"[phase2k-f] elapsed seconds             : {elapsed:.2f}")
    print("[phase2k-f] plan only; nothing built/fetched, no model; safety flags in JSON.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
