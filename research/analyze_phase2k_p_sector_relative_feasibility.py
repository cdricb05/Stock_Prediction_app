"""Phase 2K-P Sector-Relative Feature Feasibility for Reconfirmed Leads (offline, read-only
except for two small C: outputs).

Phase 2K-O was a decision gate over the Phase 2K-N narrow model-free retest. All 3 reconfirmed
leads reproduced a positive, above-zero, year-stable, regime-stable, non-concentrated residual
edge whose single dominant blocker is sub-floor residual IC; Phase 2K-O therefore chose
PROCEED_TO_SECTOR_RELATIVE_FEASIBILITY and routed here. Phase 2K-P is a FEASIBILITY phase, not
a retest and not model training: it determines whether sector / industry metadata can support
sector-relative versions of the 3 reconfirmed leads.

It:

  1. Reads the Phase 2K-O result JSON and confirms it routed here (phase == "2K-O",
     recommendation.recommendation == PROCEED_TO_SECTOR_RELATIVE_FEASIBILITY,
     selected_path.option == SECTOR_RELATIVE_FEATURE_FEASIBILITY, recommended_next_phase.phase
     == "2K-P" with the matching title, and create_model_candidate_now / train_model_now both
     false).
  2. Reads the Phase 2K-N result JSON and extracts the 3 reconfirmed leads (and ONLY these).
  3. Reads the expanded D: price-history CSV READ-ONLY and uses it ONLY to extract the ticker
     universe and date coverage (ticker / date columns only). It computes no alpha signal, no
     label, and no sector-relative retest.
  4. Excludes SPY from the equity universe (kept as benchmark metadata) and records
     ticker_count, date_count, start_date, end_date, rows_loaded, universe_source.
  5. Validates an optional local sector map (research/input/phase2k_p_sector_map_current.csv)
     if present; otherwise generates a small one-row-per-equity-ticker template at
     research/output/phase2k_p_sector_map_template.csv.
  6. Applies conservative feasibility rules and emits exactly one recommendation
     (SECTOR_MAP_READY_FOR_CAVEATED_RETEST / SECTOR_MAP_TEMPLATE_CREATED /
     SECTOR_MAP_INCOMPLETE / REQUIRE_POINT_IN_TIME_SECTOR_DATA /
     NO_ACTION_FEASIBILITY_BLOCKED), then routes to Phase 2K-Q with the title and purpose
     chosen by the decision.

It runs no broad alpha screen, runs no sector-relative retest, trains / fits / scores / creates
no model, fetches nothing from the network (no third-party data fetch), writes nothing to the
D: drive, and performs no infrastructure action. It writes only two small files in the C: repo: the results
JSON and (when no local sector map exists) the sector-map template CSV. The machine-readable
safety flags are emitted in the results JSON; the full guardrail rationale lives in
docs/phase2k_p_sector_relative_feasibility_v1.md, not in this source.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

PHASE = "2K-P"

_OUTPUT_DIR = os.path.join("research", "output")
_INPUT_DIR = os.path.join("research", "input")

# Read-only upstream summaries (small, Git-tracked, in the C: repo).
PHASE2K_O_JSON = os.path.join(_OUTPUT_DIR, "phase2k_o_reconfirmed_lead_decision_gate.json")
PHASE2K_N_JSON = os.path.join(_OUTPUT_DIR, "phase2k_n_narrow_model_free_retest.json")
PHASE2K_H_JSON = os.path.join(_OUTPUT_DIR, "phase2k_h_manual_build_run_summary.json")

# Read-only expanded D: dataset (the large price-history CSV plus its small metadata JSONs).
# The CSV is read ONLY for the ticker universe and date coverage (ticker / date columns only).
_D_DATA_DIR = os.path.join("D:\\", "Stock_Prediction_app_data", "phase2k_g", "output")
D_PRICE_HISTORY_CSV = os.path.join(_D_DATA_DIR, "phase2k_g_expanded_price_history_free.csv")
D_DATA_QUALITY_JSON = os.path.join(_D_DATA_DIR, "phase2k_g_data_quality_report.json")
D_DATA_BUILD_SUMMARY_JSON = os.path.join(_D_DATA_DIR, "phase2k_g_data_build_summary.json")
D_SURVIVORSHIP_JSON = os.path.join(_D_DATA_DIR, "phase2k_g_survivorship_caveat.json")

# Optional local sector map (validated if present; never fetched from the network).
SECTOR_MAP_INPUT = os.path.join(_INPUT_DIR, "phase2k_p_sector_map_current.csv")

# The two small outputs this analyzer writes (both stay in the C: repo, never on D:).
RESULTS_JSON = os.path.join(_OUTPUT_DIR, "phase2k_p_sector_relative_feasibility.json")
SECTOR_MAP_TEMPLATE = os.path.join(_OUTPUT_DIR, "phase2k_p_sector_map_template.csv")

# The benchmark ticker, excluded from the equity universe but kept as benchmark metadata.
BENCHMARK = "SPY"

# The 3 reconfirmed lead ids expected from Phase 2K-N (and ONLY these). This phase adds no
# candidate and runs no retest.
PRE_REGISTERED_LEAD_IDS = [
    "residual_price_momentum_12_1@5d",
    "short_horizon_residual_reversal_5d@21d",
    "short_horizon_residual_reversal_21d@21d",
]

# Phase 2K-O routing values that send a reconfirmed-but-sub-floor outcome here.
PHASE2K_O_RECOMMENDATION = "PROCEED_TO_SECTOR_RELATIVE_FEASIBILITY"
PHASE2K_O_SELECTED_OPTION = "SECTOR_RELATIVE_FEATURE_FEASIBILITY"
PHASE2K_P_TITLE_FROM_PHASE2K_O = "Sector-Relative Feature Feasibility for Reconfirmed Leads"

# Required schema for an optional local sector map.
SECTOR_MAP_REQUIRED_COLUMNS = [
    "ticker", "sector", "industry", "source", "as_of_date", "point_in_time", "notes",
]

# Conservative feasibility thresholds for a caveated sector-relative retest.
SECTOR_COVERAGE_FLOOR = 0.95
MIN_DISTINCT_SECTORS = 5
MIN_TICKERS_PER_SECTOR = 3

# Recommendation vocabulary (the ONLY values this phase may emit).
REC_READY = "SECTOR_MAP_READY_FOR_CAVEATED_RETEST"
REC_TEMPLATE = "SECTOR_MAP_TEMPLATE_CREATED"
REC_INCOMPLETE = "SECTOR_MAP_INCOMPLETE"
REC_REQUIRE_PIT = "REQUIRE_POINT_IN_TIME_SECTOR_DATA"
REC_NOACTION = "NO_ACTION_FEASIBILITY_BLOCKED"
_ALLOWED_RECOMMENDATIONS = [
    REC_READY, REC_TEMPLATE, REC_INCOMPLETE, REC_REQUIRE_PIT, REC_NOACTION,
]

# Next-phase (always Phase 2K-Q) titles + purposes, chosen by the recommendation.
NEXT_PHASE = "2K-Q"
NEXT_PHASE_BY_RECOMMENDATION = {
    REC_READY: {
        "title": "Sector-Relative Retest for Reconfirmed Leads",
        "purpose": (
            "Run a narrow, model-free sector-relative retest of the 3 reconfirmed leads using "
            "the validated sector map, still with no model training and no model-candidate "
            "design. Results remain survivorship-biased and not a production edge."),
    },
    REC_TEMPLATE: {
        "title": "Populate Sector Map for Reconfirmed Leads",
        "purpose": (
            "Fill or source the sector / industry map (the generated template) before any "
            "sector-relative retesting, still with no model training and no model candidate. "
            "Results will remain survivorship-biased and not a production edge."),
    },
    REC_INCOMPLETE: {
        "title": "Repair Sector Map Before Sector-Relative Retest",
        "purpose": (
            "Repair missing sector / industry coverage or columns before retesting, still with "
            "no model training and no model candidate. Results remain survivorship-biased and "
            "not a production edge."),
    },
    REC_REQUIRE_PIT: {
        "title": "Point-in-Time Sector Data Decision",
        "purpose": (
            "Decide whether to obtain point-in-time sector / industry metadata before further "
            "research, with a cost / burden / expected-quality assessment. Still no model "
            "training and no model candidate."),
    },
    REC_NOACTION: {
        "title": "Repair Sector Feasibility Inputs",
        "purpose": (
            "Fix missing Phase 2K-O routing or D: universe access before proceeding, still "
            "with no model training and no model candidate."),
    },
}


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


def _frac(num: int, den: int) -> float:
    return round(num / den, 6) if den else 0.0


# --------------------------------------------------------------------------- #
# Upstream Phase 2K-O routing confirmation + Phase 2K-N lead extraction
# --------------------------------------------------------------------------- #
def summarize_phase2k_o(ko: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Read-only digest of the Phase 2K-O result plus the routing confirmation."""
    if ko is None:
        return {"present": False, "routing_confirmed": False}
    rec = ko.get("recommendation", {}) or {}
    sel = ko.get("selected_path", {}) or {}
    nxt = ko.get("recommended_next_phase", {}) or {}

    phase_ok = ko.get("phase") == "2K-O"
    rec_ok = rec.get("recommendation") == PHASE2K_O_RECOMMENDATION
    option_ok = sel.get("option") == PHASE2K_O_SELECTED_OPTION
    routes_ok = nxt.get("phase") == "2K-P"
    title_ok = nxt.get("title") == PHASE2K_P_TITLE_FROM_PHASE2K_O
    create_ok = rec.get("create_model_candidate_now") is False
    train_ok = rec.get("train_model_now") is False

    return {
        "present": True,
        "phase": ko.get("phase"),
        "recommendation": rec.get("recommendation"),
        "selected_option": sel.get("option"),
        "recommended_next_phase": nxt,
        "phase_is_2k_o": phase_ok,
        "recommendation_is_proceed_to_sector_relative": rec_ok,
        "selected_option_is_sector_relative": option_ok,
        "recommended_next_phase_is_2k_p": routes_ok,
        "recommended_next_phase_title_matches": title_ok,
        "create_model_candidate_now_false": create_ok,
        "train_model_now_false": train_ok,
        "results_are_survivorship_biased": bool(
            rec.get("results_are_survivorship_biased")),
        "routing_confirmed": bool(
            phase_ok and rec_ok and option_ok and routes_ok and title_ok
            and create_ok and train_ok),
    }


def extract_reconfirmed_leads(kn: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract the 3 Phase 2K-N reconfirmed leads (lead identity + headline metric only)."""
    if kn is None:
        return []
    out: List[Dict[str, Any]] = []
    for pr in kn.get("pair_recommendations", []) or []:
        out.append({
            "lead_id": pr.get("lead_id"),
            "candidate": pr.get("candidate"),
            "feature": pr.get("feature"),
            "category": pr.get("category"),
            "horizon_days": pr.get("horizon_days"),
            "status": pr.get("status"),
            "mean_residual_rank_ic": pr.get("mean_residual_rank_ic"),
        })
    return out


# --------------------------------------------------------------------------- #
# D: universe extraction (read-only; ticker / date columns only)
# --------------------------------------------------------------------------- #
def extract_universe(csv_path: str = D_PRICE_HISTORY_CSV,
                     benchmark: str = BENCHMARK) -> Optional[Dict[str, Any]]:
    """Read the expanded D: price-history CSV READ-ONLY for the ticker universe + dates only.

    Returns None if the CSV is absent. Loads only the 'ticker' and 'date' columns; computes no
    alpha signal, no label, and no retest. SPY is excluded from the equity universe and kept as
    benchmark metadata.
    """
    if not os.path.isfile(csv_path):
        return None
    import pandas as pd  # lazy: only needed for the offline D: universe read

    frame = pd.read_csv(csv_path, usecols=["ticker", "date"], dtype=str)
    rows_loaded = int(len(frame))
    tickers = sorted({str(t).strip().upper() for t in frame["ticker"].dropna().tolist()})
    dates = [str(d).strip() for d in frame["date"].dropna().tolist()]
    distinct_dates = sorted(set(dates))
    benchmark_present = benchmark in tickers
    equity_universe = [t for t in tickers if t != benchmark]

    return {
        "universe_source": "expanded D: price history (phase2k_g_expanded_price_history_free.csv)",
        "csv_path": _norm(csv_path),
        "all_ticker_count": len(tickers),
        "benchmark": benchmark,
        "benchmark_present": benchmark_present,
        "benchmark_excluded_from_equity_universe": benchmark_present,
        "ticker_count": len(equity_universe),
        "equity_universe": equity_universe,
        "date_count": len(distinct_dates),
        "start_date": distinct_dates[0] if distinct_dates else None,
        "end_date": distinct_dates[-1] if distinct_dates else None,
        "rows_loaded": rows_loaded,
    }


def summarize_d_metadata(quality: Optional[Dict[str, Any]],
                         build: Optional[Dict[str, Any]],
                         surv: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Light read-only digest of the small D: metadata JSONs (survivorship caveat etc.)."""
    q_stats = (quality or {}).get("stats", {}) or {}
    return {
        "data_quality_status": (quality or {}).get("status") or (build or {}).get(
            "data_quality_status"),
        "membership_basis": (surv or {}).get("membership_basis"),
        "point_in_time_membership_claimed": (surv or {}).get(
            "point_in_time_membership_claimed"),
        "reported_as_survivorship_biased": bool(
            (surv or {}).get("reported_as_survivorship_biased")),
        "active_as_of": (surv or {}).get("active_as_of"),
        "build_ticker_count": (build or {}).get("ticker_count"),
        "build_universe_ticker_count": (build or {}).get("universe_ticker_count"),
        "quality_date_count": q_stats.get("date_count"),
    }


# --------------------------------------------------------------------------- #
# Optional sector-map validation
# --------------------------------------------------------------------------- #
def read_sector_map(path: str = SECTOR_MAP_INPUT) -> Optional[List[Dict[str, str]]]:
    """Read an optional local sector map (CSV) read-only. Returns None if absent/unreadable."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            return [dict(row) for row in csv.DictReader(f)]
    except (OSError, ValueError):
        return None


def validate_sector_map(rows: Optional[List[Dict[str, str]]],
                        equity_universe: List[str]) -> Dict[str, Any]:
    """Validate an optional local sector map against the D: equity universe (read-only)."""
    if rows is None:
        return {
            "applicable": False,
            "reason": "no local sector map present; a template was generated instead",
        }
    cols = list(rows[0].keys()) if rows else []
    has_required = all(c in cols for c in SECTOR_MAP_REQUIRED_COLUMNS)

    tickers = [str(r.get("ticker", "")).strip() for r in rows]
    non_empty = [t for t in tickers if t]
    all_upper = bool(non_empty) and all(t == t.upper() for t in non_empty)
    unique = len(non_empty) == len(set(non_empty))

    by_ticker = {str(r.get("ticker", "")).strip().upper(): r for r in rows}
    equity_set = set(equity_universe)
    covered = [t for t in equity_universe if t in by_ticker]
    missing = [t for t in equity_universe if t not in by_ticker]
    extra = sorted(t for t in by_ticker if t and t not in equity_set and t != BENCHMARK)

    def _non_null(field: str) -> int:
        return sum(1 for t in covered if str(by_ticker[t].get(field, "")).strip())

    sectors = sorted({str(by_ticker[t].get("sector", "")).strip()
                      for t in covered if str(by_ticker[t].get("sector", "")).strip()})
    per_sector: Dict[str, int] = {}
    for t in covered:
        s = str(by_ticker[t].get("sector", "")).strip()
        if s:
            per_sector[s] = per_sector.get(s, 0) + 1
    min_per_sector = min(per_sector.values()) if per_sector else 0

    pit_vals = [str(r.get("point_in_time", "")).strip().lower() for r in rows]
    pit_all = bool(pit_vals) and all(v == "true" for v in pit_vals)

    return {
        "applicable": True,
        "input_path": _norm(SECTOR_MAP_INPUT),
        "row_count": len(rows),
        "columns_present": cols,
        "has_required_columns": has_required,
        "all_tickers_uppercase": all_upper,
        "tickers_unique": unique,
        "equity_universe_size": len(equity_universe),
        "equity_universe_covered_count": len(covered),
        "equity_universe_coverage_fraction": _frac(len(covered), len(equity_universe)),
        "sector_non_null_fraction": _frac(_non_null("sector"), len(equity_universe)),
        "industry_non_null_fraction": _frac(_non_null("industry"), len(equity_universe)),
        "missing_ticker_count": len(missing),
        "missing_tickers": missing[:25],
        "extra_ticker_count": len(extra),
        "extra_tickers": extra[:25],
        "distinct_sector_count": len(sectors),
        "sectors": sectors,
        "min_tickers_per_sector": min_per_sector,
        "point_in_time_all_rows": pit_all,
        "current_as_of_only": not pit_all,
    }


def build_sector_map_template_rows(equity_universe: List[str]) -> List[Dict[str, str]]:
    """One row per equity ticker; sector / industry blank for manual fill."""
    return [
        {
            "ticker": t,
            "sector": "",
            "industry": "",
            "source": "manual_required",
            "as_of_date": "",
            "point_in_time": "false",
            "notes": "fill sector/industry before sector-relative retest",
        }
        for t in equity_universe
    ]


def write_sector_map_template(rows: List[Dict[str, str]],
                              path: str = SECTOR_MAP_TEMPLATE) -> None:
    """Write the small sector-map template CSV (C: repo only; never the D: drive)."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(SECTOR_MAP_REQUIRED_COLUMNS)
        for r in rows:
            writer.writerow([r[c] for c in SECTOR_MAP_REQUIRED_COLUMNS])


# --------------------------------------------------------------------------- #
# Feasibility decision
# --------------------------------------------------------------------------- #
def select_recommendation(routing_ok: bool, universe: Optional[Dict[str, Any]],
                          map_present: bool,
                          validation: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the conservative feasibility rules and pick exactly one recommendation."""
    universe_ok = bool(universe and universe.get("ticker_count", 0) > 0)

    ready_conditions = {
        "sector_map_present": map_present,
        "has_required_columns": bool(validation.get("has_required_columns")),
        "coverage_at_or_above_floor": bool(
            validation.get("equity_universe_coverage_fraction", 0.0) >= SECTOR_COVERAGE_FLOOR),
        "at_least_min_sectors": bool(
            validation.get("distinct_sector_count", 0) >= MIN_DISTINCT_SECTORS),
        "enough_tickers_per_sector": bool(
            validation.get("min_tickers_per_sector", 0) >= MIN_TICKERS_PER_SECTOR),
        "low_missing_ticker_count": bool(
            validation.get("missing_ticker_count", 10**9)
            <= max(1, int(round((1.0 - SECTOR_COVERAGE_FLOOR)
                                * validation.get("equity_universe_size", 0))))),
    }
    all_ready = all(ready_conditions.values())

    if not routing_ok or not universe_ok:
        rec = REC_NOACTION
    elif not map_present:
        rec = REC_TEMPLATE
    elif not validation.get("has_required_columns"):
        rec = REC_INCOMPLETE
    elif all_ready:
        # Coverage / structure are sufficient; usable as caveated current-as-of metadata.
        rec = REC_READY
    elif validation.get("equity_universe_coverage_fraction", 0.0) < SECTOR_COVERAGE_FLOOR:
        rec = REC_INCOMPLETE
    else:
        rec = REC_INCOMPLETE

    return {
        "recommendation": rec,
        "decision_inputs": {
            "routing_confirmed": routing_ok,
            "universe_readable": universe_ok,
            "sector_map_present": map_present,
            "ready_conditions": ready_conditions,
            "all_ready_conditions_met": all_ready,
        },
    }


def build_feasibility_decision(selected: str, universe: Optional[Dict[str, Any]],
                               validation: Dict[str, Any],
                               map_present: bool) -> Dict[str, Any]:
    equity_size = (universe or {}).get("ticker_count", 0)
    return {
        "recommendation": selected,
        "allowed_values": list(_ALLOWED_RECOMMENDATIONS),
        "sector_map_present": map_present,
        "equity_universe_size": equity_size,
        "coverage_floor": SECTOR_COVERAGE_FLOOR,
        "min_distinct_sectors": MIN_DISTINCT_SECTORS,
        "min_tickers_per_sector": MIN_TICKERS_PER_SECTOR,
        "sector_coverage_fraction": validation.get("equity_universe_coverage_fraction"),
        "distinct_sector_count": validation.get("distinct_sector_count"),
        "point_in_time_all_rows": validation.get("point_in_time_all_rows"),
        "what_sector_relative_testing_would_mean": (
            "Re-rank each reconfirmed price / reversal lead WITHIN its sector (or against a "
            "sector-neutral benchmark) so the residual edge is measured cross-sectionally "
            "inside sectors. This targets the sub-floor residual IC without adding a candidate "
            "or training a model. It is a feasibility assessment here; the actual retest is "
            "deferred to Phase 2K-Q."),
    }


# --------------------------------------------------------------------------- #
# Recommendation block + routing + interpretation
# --------------------------------------------------------------------------- #
def build_recommendation(selected: str, universe: Optional[Dict[str, Any]],
                         map_present: bool, validation: Dict[str, Any]) -> Dict[str, Any]:
    equity_size = (universe or {}).get("ticker_count", 0)
    if selected == REC_TEMPLATE:
        reason = (
            f"No local sector map was found, so a template with one row per equity ticker "
            f"({equity_size} tickers, SPY excluded as benchmark) was generated at "
            f"{_norm(SECTOR_MAP_TEMPLATE)}. Fill the sector / industry columns (offline; no "
            "network fetch) before any sector-relative retest. No retest is run here, no model "
            "is trained, and no model candidate is created.")
    elif selected == REC_READY:
        reason = (
            "A local sector map is present, covers at least 95% of the equity universe across "
            "enough sectors with enough tickers per sector, and can be used as explicitly "
            "caveated current-as-of metadata. A narrow, model-free sector-relative retest is "
            "feasible (Phase 2K-Q). No retest is run here and no model candidate is created.")
    elif selected == REC_INCOMPLETE:
        reason = (
            "A local sector map is present but its coverage or columns are insufficient for a "
            "sector-relative retest; repair the map before retesting. No retest is run here "
            "and no model candidate is created.")
    elif selected == REC_REQUIRE_PIT:
        reason = (
            "A current-as-of sector map would be misleading or insufficient for the next test; "
            "a point-in-time sector-data decision is required first. No retest is run here and "
            "no model candidate is created.")
    else:
        reason = (
            "Phase 2K-O did not route here or the D: universe could not be read; the "
            "feasibility decision is blocked until the inputs are repaired. No retest is run "
            "here and no model candidate is created.")
    return {
        "recommendation": selected,
        "allowed_values": list(_ALLOWED_RECOMMENDATIONS),
        "create_model_candidate_now": False,
        "train_model_now": False,
        "deploy_now": False,
        "run_sector_relative_retest_now": False,
        "production_edge_claimed": False,
        "results_are_survivorship_biased": True,
        "reason": reason,
    }


def build_recommended_next_phase(selected: str) -> Dict[str, str]:
    meta = NEXT_PHASE_BY_RECOMMENDATION.get(selected, NEXT_PHASE_BY_RECOMMENDATION[REC_NOACTION])
    return {"phase": NEXT_PHASE, "title": meta["title"], "purpose": meta["purpose"]}


def build_interpretation(selected: str, universe: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    equity_size = (universe or {}).get("ticker_count", 0)
    if selected == REC_TEMPLATE:
        verdict = ("generates a sector-map template (one row per equity ticker) to populate "
                   "before any sector-relative retest")
    elif selected == REC_READY:
        verdict = ("finds the sector map usable as caveated metadata and clears a narrow, "
                   "model-free sector-relative retest to proceed in Phase 2K-Q")
    elif selected == REC_INCOMPLETE:
        verdict = "requires the sector map to be repaired before a sector-relative retest"
    elif selected == REC_REQUIRE_PIT:
        verdict = "requires a point-in-time sector-data decision before further research"
    else:
        verdict = "blocks the feasibility decision until the inputs are repaired"
    return {
        "model_trained": False,
        "model_fitted": False,
        "model_candidate_created": False,
        "authorized_to_train_model": False,
        "authorized_to_serve_model": False,
        "ran_broad_alpha_screen": False,
        "ran_sector_relative_retest": False,
        "candidate_set_expanded": False,
        "fetched_sector_data_from_network": False,
        "wrote_to_d_drive": False,
        "production_edge_claimed": False,
        "results_are_survivorship_biased": True,
        "narrative": (
            "Phase 2K-P is a sector-relative feasibility phase after the Phase 2K-O decision "
            "gate. It read the expanded D: price-history CSV READ-ONLY for the ticker universe "
            f"and date coverage only ({equity_size} equity tickers, SPY excluded as "
            "benchmark), checked for a local sector map, and " + verdict + ". It ran no broad "
            "alpha screen, ran no sector-relative retest, computed no alpha signal or label, "
            "trained / fitted / scored no model, created no model candidate, fetched nothing "
            "from the network, and wrote nothing to the D: drive; results remain "
            "survivorship-biased / current-membership caveated and are not a production edge."),
    }


def build_implementation_constraints() -> List[str]:
    return [
        "This is a feasibility phase only: no sector-relative retest is run, no broad alpha "
        "screen is run, and no model is trained, fitted, scored, or created.",
        "The large D: price-history CSV is read READ-ONLY and only for the ticker universe and "
        "date coverage (ticker / date columns); no alpha signal or label is computed.",
        "No sector data is fetched from the network; only a local sector map is read, and only "
        "a small template is written when none exists.",
        "Any current-as-of sector map may be used only as explicitly caveated research "
        "metadata; results remain survivorship-biased and a clean point-in-time universe would "
        "tighten, never rescue, a sub-floor edge.",
        "Nothing is written to the D: drive; only two small C: outputs are written (the results "
        "JSON and, when no local map exists, the sector-map template CSV).",
        "No deployment, no model-v2 enablement, no migration, no database write, no order, and "
        "no automation; nothing here is a production edge.",
    ]


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_results(*, phase2k_o_path: Optional[str] = None,
                  phase2k_n_path: Optional[str] = None,
                  phase2k_h_path: Optional[str] = None,
                  price_history_csv: Optional[str] = None,
                  sector_map_input: Optional[str] = None,
                  template_path: Optional[str] = None
                  ) -> Tuple[Dict[str, Any], List[Dict[str, str]], bool]:
    phase2k_o_path = phase2k_o_path or PHASE2K_O_JSON
    phase2k_n_path = phase2k_n_path or PHASE2K_N_JSON
    phase2k_h_path = phase2k_h_path or PHASE2K_H_JSON
    price_history_csv = price_history_csv or D_PRICE_HISTORY_CSV
    sector_map_input = sector_map_input or SECTOR_MAP_INPUT
    template_path = template_path or SECTOR_MAP_TEMPLATE

    ko = _safe_read_json(phase2k_o_path)
    kn = _safe_read_json(phase2k_n_path)
    kh = _safe_read_json(phase2k_h_path)
    quality = _safe_read_json(D_DATA_QUALITY_JSON)
    build = _safe_read_json(D_DATA_BUILD_SUMMARY_JSON)
    surv = _safe_read_json(D_SURVIVORSHIP_JSON)

    ko_summary = summarize_phase2k_o(ko)
    ko_summary["phase2k_h_ready_for_retest"] = bool(
        (kh or {}).get("decision", {}).get("ready_for_retest"))
    leads = extract_reconfirmed_leads(kn)

    universe = extract_universe(price_history_csv)
    equity_universe = (universe or {}).get("equity_universe", []) or []
    universe_summary = dict(universe) if universe else {
        "universe_source": "expanded D: price history (phase2k_g_expanded_price_history_free.csv)",
        "csv_path": _norm(price_history_csv),
        "readable": False,
        "ticker_count": 0,
    }
    universe_summary["d_metadata"] = summarize_d_metadata(quality, build, surv)

    sector_map_rows = read_sector_map(sector_map_input)
    map_present = sector_map_rows is not None
    validation = validate_sector_map(sector_map_rows, equity_universe)

    template_rows = build_sector_map_template_rows(equity_universe) if not map_present else []
    template_needed = bool(not map_present and equity_universe)

    routing_ok = bool(ko_summary.get("routing_confirmed"))
    decision = select_recommendation(routing_ok, universe, map_present, validation)
    selected = decision["recommendation"]

    sector_map_input_status = {
        "input_path": _norm(sector_map_input),
        "present": map_present,
        "checked": True,
        "required_columns": list(SECTOR_MAP_REQUIRED_COLUMNS),
        "network_fetch_used": False,
    }
    sector_map_template = {
        "generated": template_needed,
        "path": _norm(template_path) if template_needed else None,
        "row_count": len(template_rows) if template_needed else 0,
        "columns": list(SECTOR_MAP_REQUIRED_COLUMNS),
        "fill_instructions": (
            "Fill sector and industry for every ticker, set a real source and as_of_date, and "
            "set point_in_time true only for a genuine point-in-time map; then proceed to the "
            "sector-relative retest decision."),
        "note": (
            "Helper template only (one row per equity ticker, SPY excluded). sector / industry "
            "left blank, source=manual_required, point_in_time=false. No large file is written "
            "and nothing is written to the D: drive."),
    }

    results = {
        "phase": PHASE,
        "generated_at": _now(),
        "inputs_read": {
            "phase2k_o_json": _norm(phase2k_o_path),
            "phase2k_n_json": _norm(phase2k_n_path),
            "phase2k_h_json": _norm(phase2k_h_path),
            "d_price_history_csv": _norm(price_history_csv),
            "d_data_quality_json": _norm(D_DATA_QUALITY_JSON),
            "d_data_build_summary_json": _norm(D_DATA_BUILD_SUMMARY_JSON),
            "d_survivorship_caveat_json": _norm(D_SURVIVORSHIP_JSON),
            "sector_map_input_csv": _norm(sector_map_input),
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
        "d_drive_read": True,
        "d_drive_written": False,
        "broad_alpha_screen_run": False,
        "sector_relative_retest_run": False,
        "network_used": False,
        "upstream_phase2k_o_summary": ko_summary,
        "reconfirmed_leads": leads,
        "universe_summary": universe_summary,
        "sector_map_input_status": sector_map_input_status,
        "sector_map_validation": validation,
        "sector_map_template": sector_map_template,
        "feasibility_decision": build_feasibility_decision(
            selected, universe, validation, map_present),
        "implementation_constraints": build_implementation_constraints(),
        "recommendation": build_recommendation(selected, universe, map_present, validation),
        "interpretation": build_interpretation(selected, universe),
        "recommended_next_phase": build_recommended_next_phase(selected),
        "selected_path": {
            "recommendation": selected,
            "decision_inputs": decision["decision_inputs"],
        },
    }
    return results, template_rows, template_needed


def write_results(results: Dict[str, Any], path: str = RESULTS_JSON) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, allow_nan=False)


def run(output_path: str = RESULTS_JSON, template_path: str = SECTOR_MAP_TEMPLATE,
        **kwargs) -> Dict[str, Any]:
    results, template_rows, template_needed = build_results(
        template_path=template_path, **kwargs)
    if template_needed:
        write_sector_map_template(template_rows, template_path)
    write_results(results, output_path)
    return results


def main() -> int:
    t0 = time.perf_counter()
    d = run()
    elapsed = time.perf_counter() - t0
    rec = d["recommendation"]
    nxt = d["recommended_next_phase"]
    ko = d["upstream_phase2k_o_summary"]
    uni = d["universe_summary"]
    print(f"[phase2k-p] upstream 2K-O routing confirmed : {ko.get('routing_confirmed')}")
    print(f"[phase2k-p] reconfirmed leads               : "
          f"{[l['lead_id'] for l in d['reconfirmed_leads']]}")
    print(f"[phase2k-p] equity universe (ex-SPY)         : {uni.get('ticker_count')} tickers, "
          f"{uni.get('date_count')} dates, {uni.get('start_date')}..{uni.get('end_date')}")
    print(f"[phase2k-p] sector map present               : "
          f"{d['sector_map_input_status']['present']}")
    print(f"[phase2k-p] template generated               : "
          f"{d['sector_map_template']['generated']}")
    print(f"[phase2k-p] recommendation                  : {rec['recommendation']}")
    print(f"[phase2k-p] recommended next phase           : {nxt['phase']} ({nxt['title']})")
    print(f"[phase2k-p] results written                 : {RESULTS_JSON}")
    print(f"[phase2k-p] elapsed seconds                  : {elapsed:.2f}")
    print("[phase2k-p] feasibility only; D: CSV read-only for universe; no retest; no model "
          "trained; no network; no D: writes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
