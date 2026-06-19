"""Phase 3-J - Repair Fundamental Price Alignment.

Phase 3-I (FUNDAMENTAL_PRICE_ALIGNMENT_PARTIAL_SUCCESS) proved the 20-ticker point-in-time
fundamental feature snapshot joins to the historical price calendar with **zero leakage** and full
label coverage, but it also surfaced a defect: the Phase 3-H sample is **temporally sparse** for
several tickers (e.g. HD and MSFT each had only two distinct feature_asof_dates, one circa 2009/2010
and one circa 2024/2025), so the carry-forward rule propagated decade-old fundamentals and the
median active feature age was ~819 days - far beyond the conservative reasonableness threshold.

This phase repairs that panel **without re-running any alignment, fetching any data, or training any
model**. It reads only the repo-local Phase 3-I and Phase 3-H outputs, diagnoses staleness per
ticker, evaluates explicit maximum feature-age caps (365 / 400 / 540 / 730 calendar days), selects
the strictest defensible cap, and writes a clean staleness-filtered repaired panel plus sensitivity,
stale-rows, decision-table, quality, and label-summary artifacts and a Phase 3-K recommendation.

Scope and safety. There is NO network use, NO D: read or write, and NO SEC fetch in this phase. It
reads only repo-local Phase 3-I / Phase 3-H outputs on the C: drive. It filters the already-generated
Phase 3-I validation labels (it creates no new labels). It fits NO model, computes NO predictions /
scores / portfolio weights, creates NO production model candidate, writes NO deployable model
artifact, touches no database, runs no migration, places no orders, trades nothing, and claims NO
production edge.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime

PHASE = "3-J"

# --------------------------------------------------------------------------- #
# Paths (all repo-local on the C: drive; no D:, no network)
# --------------------------------------------------------------------------- #
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUT_DIR = os.path.join(_REPO_ROOT, "research", "output")
_H_DIR = os.path.join(_OUT_DIR, "phase3h_sec_fundamental_features")
_I_DIR = os.path.join(_OUT_DIR, "phase3i_fundamental_price_alignment")
_J_DIR = os.path.join(_OUT_DIR, "phase3j_repaired_fundamental_price_alignment")

# Inputs - committed Phase 3-I alignment outputs (repo-local).
PHASE3I_JSON = os.path.join(_OUT_DIR, "phase3i_fundamental_price_alignment.json")
PHASE3I_PANEL_CSV = os.path.join(_I_DIR, "aligned_feature_price_panel_20ticker_sample.csv")
PHASE3I_QUALITY_JSON = os.path.join(_I_DIR, "alignment_quality_report.json")
PHASE3I_LABEL_SUMMARY_CSV = os.path.join(_I_DIR, "label_summary_by_horizon.csv")
PHASE3I_LEAKAGE_CSV = os.path.join(_I_DIR, "leakage_checks.csv")
PHASE3I_DATE_ALIGNMENT_CSV = os.path.join(_I_DIR, "date_alignment_summary.csv")
# Inputs - committed Phase 3-H feature outputs (repo-local), for source density / context.
PHASE3H_JSON = os.path.join(_OUT_DIR, "phase3h_sec_fundamental_features.json")
PHASE3H_SNAPSHOT_CSV = os.path.join(_H_DIR, "feature_snapshot_20ticker_sample.csv")
PHASE3H_QUALITY_JSON = os.path.join(_H_DIR, "feature_quality_report.json")

# Outputs (all repo-local, under research/output on the C: drive).
RESULT_JSON = os.path.join(_OUT_DIR, "phase3j_repaired_fundamental_price_alignment.json")
REPAIRED_PANEL_CSV = os.path.join(_J_DIR, "repaired_aligned_panel_20ticker_sample.csv")
STALENESS_SENSITIVITY_CSV = os.path.join(_J_DIR, "staleness_cap_sensitivity.csv")
REPAIRED_QUALITY_JSON = os.path.join(_J_DIR, "repaired_alignment_quality_report.json")
REPAIRED_LABEL_SUMMARY_CSV = os.path.join(_J_DIR, "repaired_label_summary_by_horizon.csv")
REPAIR_DECISION_TABLE_CSV = os.path.join(_J_DIR, "repair_decision_table.csv")
STALE_ROWS_BY_TICKER_CSV = os.path.join(_J_DIR, "stale_rows_by_ticker.csv")

# --------------------------------------------------------------------------- #
# Contract / config
# --------------------------------------------------------------------------- #
PHASE3I_EXPECTED_RECOMMENDATION = "FUNDAMENTAL_PRICE_ALIGNMENT_PARTIAL_SUCCESS"
HORIZONS = [21, 63, 126]
CANDIDATE_CAPS = [365, 400, 540, 730]  # maximum feature-age caps, in calendar days

# Conservative success gates (same spirit as the Phase 3-I gates).
COV_GATE = {21: 0.80, 63: 0.70, 126: 0.60}
SUCCESS_MIN_TICKERS = 15
SUCCESS_MIN_ROWS = 5000
PARTIAL_MIN_TICKERS = 10
# A ticker is flagged as a staleness problem if it ever carried a feature older than two years
# (multi-year carry-forward gap) or if its source history is very sparse.
STALENESS_MAX_AGE_FLAG_DAYS = 730
STALENESS_MIN_DISTINCT_ASOF = 4

# Recommendation vocabulary.
REC_SUCCESS = "FUNDAMENTAL_ALIGNMENT_REPAIR_SUCCESS"
REC_PARTIAL = "FUNDAMENTAL_ALIGNMENT_REPAIR_PARTIAL_SUCCESS"
REC_DENSER = "FUNDAMENTAL_ALIGNMENT_NEEDS_DENSER_SEC_HISTORY"
REC_BLOCKED = "FUNDAMENTAL_ALIGNMENT_REPAIR_BLOCKED"
REC_REJECTED = "FUNDAMENTAL_ALIGNMENT_REPAIR_REJECTED"
ALLOWED_RECOMMENDATIONS = [REC_SUCCESS, REC_PARTIAL, REC_DENSER, REC_BLOCKED, REC_REJECTED]

STALENESS_SENSITIVITY_COLUMNS = [
    "cap_days", "aligned_rows_after_cap", "retained_row_fraction", "aligned_ticker_count",
    "aligned_sector_count", "median_feature_age_days", "p75_feature_age_days",
    "p90_feature_age_days", "max_feature_age_days", "label_coverage_21d", "label_coverage_63d",
    "label_coverage_126d", "min_rows_by_ticker", "median_rows_by_ticker", "max_rows_by_ticker",
    "tickers_dropped", "sectors_dropped", "leakage_failure_count", "recommendation_for_cap",
]
STALE_ROWS_BY_TICKER_COLUMNS = [
    "ticker", "sector", "total_rows", "rows_over_365", "rows_over_400", "rows_over_540",
    "rows_over_730", "max_feature_age_days", "median_feature_age_days",
    "distinct_feature_asof_dates", "staleness_problem",
]
REPAIR_DECISION_TABLE_COLUMNS = ["decision_item", "value", "passed", "note"]
REPAIRED_LABEL_SUMMARY_COLUMNS = [
    "horizon", "total_rows", "non_null_forward_return", "non_null_forward_excess_return_vs_spy",
    "non_null_binary_outperform_spy", "non_null_rank", "mean_forward_return",
    "mean_forward_excess_return_vs_spy", "positive_return_fraction", "outperform_spy_fraction",
    "earliest_scoring_date", "latest_scoring_date",
]

_SOURCE_LIMITATIONS = [
    "This phase reads only repo-local Phase 3-I and Phase 3-H outputs; it re-runs no D: price "
    "alignment, fetches no new SEC data, and creates no new labels (it only filters the "
    "already-generated Phase 3-I validation labels).",
    "The repair is a feature-staleness cap, not a fix for the underlying source sparsity: capping "
    "feature age removes stale rows but cannot manufacture intermediate filings that the Phase 3-H "
    "sample never captured, so a tight cap concentrates retained rows in the recent periods where "
    "each ticker has a fresh filing.",
    "Forward-return labels remain for VALIDATION ONLY (leakage proof and coverage measurement); "
    "they are not a target for any model in this phase and are never forward-filled. A tight "
    "staleness cap drops the older rows first, so label coverage shifts toward the recent end of "
    "the price series where the forward window can run past the last available date.",
    "The historical price panel inherited from Phase 3-I is the Phase 2K-G current-as-of free "
    "panel; its membership is NOT point-in-time, so this repaired alignment and any downstream "
    "result must still be reported as survivorship-biased.",
    "The sector / industry attached to each row is current-as-of (not point-in-time), so "
    "cross-sectional results remain survivorship-caveated.",
    "This is a controlled 20-ticker sample, not the full 128-ticker universe.",
]


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _load_json(path):
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _to_float(x):
    if x is None:
        return None
    s = str(x).strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _round(x, n=6):
    if x is None:
        return None
    try:
        return round(float(x), n)
    except (TypeError, ValueError):
        return None


def _date_part(dt_str):
    if not dt_str:
        return ""
    return str(dt_str).split("T")[0].strip()


def _percentile(sorted_vals, p):
    """Linear-interpolated percentile (p in [0,1]) over an already-sorted non-empty list."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    idx = p * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def _age_stats(values):
    """count/min/median/p75/p90/p95/max/mean over feature_age_days (non-null numbers)."""
    if not values:
        return {"count": 0, "min": None, "median": None, "p75": None, "p90": None,
                "p95": None, "max": None, "mean": None}
    vs = sorted(values)
    n = len(vs)
    return {
        "count": n,
        "min": _round(vs[0], 4),
        "median": _round(_percentile(vs, 0.5), 4),
        "p75": _round(_percentile(vs, 0.75), 4),
        "p90": _round(_percentile(vs, 0.90), 4),
        "p95": _round(_percentile(vs, 0.95), 4),
        "max": _round(vs[-1], 4),
        "mean": _round(sum(vs) / n, 4),
    }


# --------------------------------------------------------------------------- #
# Upstream confirmation
# --------------------------------------------------------------------------- #
def confirm_phase3i(phase3i):
    """Confirm the committed Phase 3-I alignment result that gates this repair phase."""
    if not phase3i:
        return {"all_confirmed": False, "phase3i_present": False}
    rec = phase3i.get("recommendation", {}) or {}
    nxt = phase3i.get("recommended_next_phase", {}) or {}
    leak = phase3i.get("leakage_check_summary", {}) or {}
    checks = {
        "phase3i_present": True,
        "phase_is_3i": phase3i.get("phase") == "3-I",
        "recommendation_is_partial_success":
            rec.get("recommendation") == PHASE3I_EXPECTED_RECOMMENDATION,
        "next_phase_is_3j": nxt.get("phase") == "3-J",
        "price_join_performed_true": phase3i.get("price_join_performed") is True,
        "labels_computed_true": phase3i.get("labels_computed") is True,
        "labels_for_validation_only_true": phase3i.get("labels_for_validation_only") is True,
        "model_training_ready_false": phase3i.get("model_training_ready") is False,
        "research_model_trained_false": phase3i.get("research_model_trained") is False,
        "production_model_candidate_created_false":
            phase3i.get("production_model_candidate_created") is False,
        "deployable_model_artifact_written_false":
            phase3i.get("deployable_model_artifact_written") is False,
        "leakage_failure_count_zero": leak.get("leakage_failure_count") == 0,
        "d_drive_read_true": phase3i.get("d_drive_read") is True,
        "d_drive_written_false": phase3i.get("d_drive_written") is False,
        "network_used_false": phase3i.get("network_used") is False,
    }
    checks["all_confirmed"] = all(checks.values())
    return checks


# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #
def load_panel(path):
    """Read the Phase 3-I aligned panel CSV in full; return (rows, header_columns)."""
    if not os.path.isfile(path):
        return [], []
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        rows = list(reader)
    return rows, header


def distinct_asof_by_ticker(snapshot_path):
    """Distinct feature_asof_date (date-part) count per ticker, from the Phase 3-H snapshot.

    This is the authoritative source-density metric: how many distinct filing-availability dates
    the upstream Phase 3-H sample actually captured for each ticker.
    """
    out = {}
    if not os.path.isfile(snapshot_path):
        return out
    with open(snapshot_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        seen = {}
        for row in reader:
            t = (row.get("ticker") or "").strip().upper()
            asof = _date_part(row.get("feature_asof_date"))
            if not t or not asof:
                continue
            seen.setdefault(t, set()).add(asof)
    for t, s in seen.items():
        out[t] = len(s)
    return out


# --------------------------------------------------------------------------- #
# Leakage re-check (filtering can never introduce leakage, but we prove it)
# --------------------------------------------------------------------------- #
def count_leakage_failures(rows):
    """Re-verify the hard point-in-time invariants on a subset of aligned rows.

    A failure is a row whose active_feature_asof_date is missing, not strictly before the scoring
    date, before its fiscal period end, or equal to its fiscal period end.
    """
    failures = 0
    for r in rows:
        asof_d = _date_part(r.get("active_feature_asof_date"))
        scoring = (r.get("scoring_date") or "").strip()
        fpe = (r.get("active_fiscal_period_end") or "").strip()
        if not asof_d or not scoring:
            failures += 1
            continue
        if not (asof_d < scoring):
            failures += 1
        elif fpe and asof_d < fpe:
            failures += 1
        elif fpe and asof_d == fpe:
            failures += 1
    return failures


# --------------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------------- #
def diagnose_staleness(rows, distinct_asof):
    """Per-ticker staleness diagnostics and the stale-rows-by-ticker table."""
    by_ticker = {}
    for r in rows:
        by_ticker.setdefault((r.get("ticker") or "").strip().upper(), []).append(r)

    table = []
    for t in sorted(by_ticker):
        trows = by_ticker[t]
        ages = [_to_float(r.get("feature_age_days")) for r in trows]
        ages = [a for a in ages if a is not None]
        sector = (trows[0].get("sector") or "").strip() or "UNKNOWN"
        n_distinct = distinct_asof.get(t, len({_date_part(r.get("active_feature_asof_date"))
                                               for r in trows if r.get("active_feature_asof_date")}))
        stats = _age_stats(ages)
        max_age = stats["max"]
        staleness_problem = bool(
            (max_age is not None and max_age > STALENESS_MAX_AGE_FLAG_DAYS)
            or (n_distinct < STALENESS_MIN_DISTINCT_ASOF))
        table.append({
            "ticker": t,
            "sector": sector,
            "total_rows": len(trows),
            "rows_over_365": sum(1 for a in ages if a > 365),
            "rows_over_400": sum(1 for a in ages if a > 400),
            "rows_over_540": sum(1 for a in ages if a > 540),
            "rows_over_730": sum(1 for a in ages if a > 730),
            "max_feature_age_days": max_age,
            "median_feature_age_days": stats["median"],
            "distinct_feature_asof_dates": n_distinct,
            "staleness_problem": staleness_problem,
        })
    return table


def _label_coverage(rows):
    cov = {}
    n = len(rows)
    for h in HORIZONS:
        col = "forward_return_%dd" % h
        nn = sum(1 for r in rows if str(r.get(col, "")).strip() != "")
        cov[h] = _round(nn / n, 4) if n else 0.0
    return cov


def _rows_by_ticker_stats(rows):
    counts = {}
    for r in rows:
        counts[(r.get("ticker") or "").strip().upper()] = counts.get(
            (r.get("ticker") or "").strip().upper(), 0) + 1
    vals = sorted(counts.values())
    if not vals:
        return {"min": 0, "median": 0, "max": 0, "ticker_count": 0, "counts": {}}
    return {
        "min": vals[0],
        "median": int(round(_percentile(vals, 0.5))),
        "max": vals[-1],
        "ticker_count": len(vals),
        "counts": dict(sorted(counts.items())),
    }


def _filter_by_cap(rows, cap_days):
    out = []
    for r in rows:
        age = _to_float(r.get("feature_age_days"))
        if age is not None and age <= cap_days:
            out.append(r)
    return out


def _cap_metrics(rows, cap_days, original_total, all_tickers, all_sectors):
    sub = _filter_by_cap(rows, cap_days)
    ages = [_to_float(r.get("feature_age_days")) for r in sub]
    ages = [a for a in ages if a is not None]
    stats = _age_stats(ages)
    cov = _label_coverage(sub)
    rbt = _rows_by_ticker_stats(sub)
    tickers_present = set(rbt["counts"].keys())
    sectors_present = {(r.get("sector") or "").strip() or "UNKNOWN" for r in sub}
    leak = count_leakage_failures(sub)

    success_ok = (
        rbt["ticker_count"] >= SUCCESS_MIN_TICKERS and len(sub) >= SUCCESS_MIN_ROWS
        and leak == 0 and cov[21] >= COV_GATE[21] and cov[63] >= COV_GATE[63]
        and cov[126] >= COV_GATE[126])
    partial_ok = rbt["ticker_count"] >= PARTIAL_MIN_TICKERS and len(sub) > 0 and leak == 0
    if leak > 0:
        rec_for_cap = "rejected_leakage"
    elif success_ok:
        rec_for_cap = "passes_success_gate"
    elif partial_ok:
        rec_for_cap = "passes_partial_gate_only"
    else:
        rec_for_cap = "insufficient"

    return {
        "cap_days": cap_days,
        "rows": sub,
        "aligned_rows_after_cap": len(sub),
        "retained_row_fraction": _round(len(sub) / original_total, 4) if original_total else 0.0,
        "aligned_ticker_count": rbt["ticker_count"],
        "aligned_sector_count": len(sectors_present),
        "median_feature_age_days": stats["median"],
        "p75_feature_age_days": stats["p75"],
        "p90_feature_age_days": stats["p90"],
        "max_feature_age_days": stats["max"],
        "label_coverage": {21: cov[21], 63: cov[63], 126: cov[126]},
        "min_rows_by_ticker": rbt["min"],
        "median_rows_by_ticker": rbt["median"],
        "max_rows_by_ticker": rbt["max"],
        "tickers_dropped": sorted(all_tickers - tickers_present),
        "sectors_dropped": sorted(all_sectors - sectors_present),
        "leakage_failure_count": leak,
        "success_ok": success_ok,
        "partial_ok": partial_ok,
        "recommendation_for_cap": rec_for_cap,
    }


def _sensitivity_row(m):
    return {
        "cap_days": m["cap_days"],
        "aligned_rows_after_cap": m["aligned_rows_after_cap"],
        "retained_row_fraction": m["retained_row_fraction"],
        "aligned_ticker_count": m["aligned_ticker_count"],
        "aligned_sector_count": m["aligned_sector_count"],
        "median_feature_age_days": m["median_feature_age_days"],
        "p75_feature_age_days": m["p75_feature_age_days"],
        "p90_feature_age_days": m["p90_feature_age_days"],
        "max_feature_age_days": m["max_feature_age_days"],
        "label_coverage_21d": m["label_coverage"][21],
        "label_coverage_63d": m["label_coverage"][63],
        "label_coverage_126d": m["label_coverage"][126],
        "min_rows_by_ticker": m["min_rows_by_ticker"],
        "median_rows_by_ticker": m["median_rows_by_ticker"],
        "max_rows_by_ticker": m["max_rows_by_ticker"],
        "tickers_dropped": "|".join(m["tickers_dropped"]),
        "sectors_dropped": "|".join(m["sectors_dropped"]),
        "leakage_failure_count": m["leakage_failure_count"],
        "recommendation_for_cap": m["recommendation_for_cap"],
    }


# --------------------------------------------------------------------------- #
# Selection + decision
# --------------------------------------------------------------------------- #
def select_cap(metrics_by_cap):
    """Pick the strictest cap satisfying the success gate; else the strictest passing the partial
    gate; else None. Returns (selected_cap_days, selection_basis)."""
    for cap in CANDIDATE_CAPS:  # strictest first
        if metrics_by_cap[cap]["success_ok"]:
            return cap, "success_gate"
    for cap in CANDIDATE_CAPS:
        if metrics_by_cap[cap]["partial_ok"]:
            return cap, "partial_gate"
    return None, "none_sufficient"


def decide(confirmed_3i, inputs_ok, selected_cap, selection_basis, selected_metrics):
    if not confirmed_3i.get("all_confirmed") or not inputs_ok:
        return REC_BLOCKED
    if selected_cap is not None and selected_metrics["leakage_failure_count"] > 0:
        return REC_REJECTED
    if selected_cap is None:
        return REC_DENSER
    if selection_basis == "success_gate" and selected_metrics["success_ok"]:
        return REC_SUCCESS
    if selected_metrics["partial_ok"]:
        return REC_PARTIAL
    return REC_DENSER


def build_recommendation(recommendation, selected_cap):
    reason = {
        REC_SUCCESS: (
            "Applying the strictest defensible feature-staleness cap (%s calendar days) yields a "
            "repaired panel that is leakage-free, retains at least %d tickers and %d aligned rows, "
            "and clears the conservative label-coverage gates. Stale carry-forward rows have been "
            "removed and every retained row's active feature is no older than the cap. The repaired "
            "panel is ready for a tiny fundamental IC / model-readiness diagnostic next (still no "
            "model training, no production candidate, no production edge claim)."
            % (selected_cap, SUCCESS_MIN_TICKERS, SUCCESS_MIN_ROWS)),
        REC_PARTIAL: (
            "A feature-staleness cap (%s calendar days) produces a leakage-free repaired panel with "
            "at least %d tickers and a usable row count, but it stays below the full success gate "
            "(ticker count, row count, or label coverage). The partially repaired panel may support "
            "a limited IC diagnostic, but a decision on expanding the SEC historical sampling should "
            "be taken first." % (selected_cap, PARTIAL_MIN_TICKERS)),
        REC_DENSER: (
            "No feature-staleness cap (365 / 400 / 540 / 730 days) can retain enough tickers and "
            "rows without stale features: the Phase 3-H sample is too temporally sparse (multi-year "
            "gaps between the only captured filings), so capping age removes the bulk of the panel. "
            "The SEC historical fundamental sampling for these 20 tickers must be expanded (denser "
            "Phase 3-G / 3-H history) before price alignment can become model-ready."),
        REC_BLOCKED: (
            "The required Phase 3-I alignment result / panel (or its Phase 3-H feature inputs) was "
            "missing, unconfirmed, or empty, so the repair could not run. Repair or re-confirm the "
            "Phase 3-I and Phase 3-H artifacts first."),
        REC_REJECTED: (
            "At least one leakage invariant failed on the repaired panel (an active feature was "
            "observable on or after its scoring date, or before / equal to its fiscal period end). "
            "Filtering cannot introduce leakage, so this indicates corrupted inputs; the alignment "
            "repair must be redesigned before proceeding."),
    }[recommendation]
    return {
        "recommendation": recommendation,
        "allowed_values": ALLOWED_RECOMMENDATIONS,
        "selected_cap_days": selected_cap,
        "create_production_model_candidate_now": False,
        "train_production_model_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "full_128_ticker_ingestion_now": False,
        "model_training_now": False,
        "labels_for_validation_only": True,
        "proceed_to_ic_diagnostic": recommendation in (REC_SUCCESS, REC_PARTIAL),
        "reason": reason,
    }


def build_recommended_next_phase(recommendation):
    table = {
        REC_SUCCESS: (
            "Tiny Fundamental IC and Model-Readiness Diagnostic",
            "Run univariate and feature-family IC diagnostics on the repaired 20-ticker panel, test "
            "sample size and label sanity, and decide whether a tiny research model is allowed."),
        REC_PARTIAL: (
            "Limited Fundamental IC Diagnostic or SEC History Expansion Decision",
            "Decide whether the partially repaired panel is sufficient for a limited IC diagnostic "
            "or whether SEC historical sampling must be expanded first."),
        REC_DENSER: (
            "Expand SEC Historical Fundamental Sampling",
            "Rebuild Phase 3-G / 3-H with denser historical SEC facts for the same 20 tickers "
            "before price alignment can become model-ready."),
        REC_BLOCKED: (
            "Repair Phase 3-J Inputs",
            "Repair missing or corrupted Phase 3-I / Phase 3-H artifacts."),
        REC_REJECTED: (
            "Redesign Fundamental Alignment Repair",
            "Redesign alignment because leakage or invalid feature activation remained after "
            "repair."),
    }
    title, purpose = table[recommendation]
    return {"phase": "3-K", "title": title, "purpose": purpose}


# --------------------------------------------------------------------------- #
# Repaired label summary
# --------------------------------------------------------------------------- #
def build_label_summary(rows):
    out = []
    for h in HORIZONS:
        fr_col = "forward_return_%dd" % h
        ex_col = "forward_excess_return_vs_spy_%dd" % h
        bo_col = "binary_outperform_spy_%dd" % h
        rk_col = "forward_return_rank_by_date_%dd" % h
        fr_vals = [_to_float(r.get(fr_col)) for r in rows if str(r.get(fr_col, "")).strip() != ""]
        fr_vals = [v for v in fr_vals if v is not None]
        ex_vals = [_to_float(r.get(ex_col)) for r in rows if str(r.get(ex_col, "")).strip() != ""]
        ex_vals = [v for v in ex_vals if v is not None]
        bo_vals = [_to_float(r.get(bo_col)) for r in rows if str(r.get(bo_col, "")).strip() != ""]
        bo_vals = [v for v in bo_vals if v is not None]
        rk_n = sum(1 for r in rows if str(r.get(rk_col, "")).strip() != "")
        scoring_with_label = [r["scoring_date"] for r in rows
                              if str(r.get(fr_col, "")).strip() != ""]
        out.append({
            "horizon": "%dd" % h,
            "total_rows": len(rows),
            "non_null_forward_return": len(fr_vals),
            "non_null_forward_excess_return_vs_spy": len(ex_vals),
            "non_null_binary_outperform_spy": len(bo_vals),
            "non_null_rank": rk_n,
            "mean_forward_return": _round(sum(fr_vals) / len(fr_vals), 6) if fr_vals else None,
            "mean_forward_excess_return_vs_spy":
                _round(sum(ex_vals) / len(ex_vals), 6) if ex_vals else None,
            "positive_return_fraction":
                _round(sum(1 for v in fr_vals if v > 0) / len(fr_vals), 4) if fr_vals else None,
            "outperform_spy_fraction":
                _round(sum(bo_vals) / len(bo_vals), 4) if bo_vals else None,
            "earliest_scoring_date": min(scoring_with_label) if scoring_with_label else "",
            "latest_scoring_date": max(scoring_with_label) if scoring_with_label else "",
        })
    return out


# --------------------------------------------------------------------------- #
# Decision table
# --------------------------------------------------------------------------- #
def build_decision_table(confirmed_3i, selected_cap, selected_metrics, recommendation):
    rows = []

    def add(item, value, passed, note):
        rows.append({"decision_item": item, "value": value,
                     "passed": "" if passed is None else bool(passed), "note": note})

    add("phase3i_confirmed", confirmed_3i.get("all_confirmed"),
        confirmed_3i.get("all_confirmed"),
        "Phase 3-I result confirmed as FUNDAMENTAL_PRICE_ALIGNMENT_PARTIAL_SUCCESS, leakage-free, "
        "routed to 3-J")
    add("selected_cap_days", selected_cap, selected_cap is not None,
        "strictest cap satisfying the gates; null means no cap was sufficient")
    if selected_metrics is None:
        add("recommendation", recommendation, None, "no cap selected")
        return rows
    m = selected_metrics
    add("repaired_aligned_ticker_count", m["aligned_ticker_count"],
        m["aligned_ticker_count"] >= SUCCESS_MIN_TICKERS,
        "success gate requires >= %d tickers" % SUCCESS_MIN_TICKERS)
    add("repaired_aligned_rows", m["aligned_rows_after_cap"],
        m["aligned_rows_after_cap"] >= SUCCESS_MIN_ROWS,
        "success gate requires >= %d aligned rows" % SUCCESS_MIN_ROWS)
    add("leakage_failure_count_after_repair", m["leakage_failure_count"],
        m["leakage_failure_count"] == 0, "must be 0")
    add("label_coverage_21d", m["label_coverage"][21],
        m["label_coverage"][21] >= COV_GATE[21], "success gate requires >= %.2f" % COV_GATE[21])
    add("label_coverage_63d", m["label_coverage"][63],
        m["label_coverage"][63] >= COV_GATE[63], "success gate requires >= %.2f" % COV_GATE[63])
    add("label_coverage_126d", m["label_coverage"][126],
        m["label_coverage"][126] >= COV_GATE[126], "success gate requires >= %.2f" % COV_GATE[126])
    add("max_feature_age_days_after_repair", m["max_feature_age_days"],
        (m["max_feature_age_days"] is None or selected_cap is None
         or m["max_feature_age_days"] <= selected_cap),
        "every retained row's active feature must be no older than the selected cap")
    add("no_model_training", True, True, "this phase trains no model")
    add("no_production_model_candidate", True, True, "this phase creates no production candidate")
    add("recommendation", recommendation, recommendation in ALLOWED_RECOMMENDATIONS,
        "allowed values only")
    return rows


# --------------------------------------------------------------------------- #
# Repaired quality report
# --------------------------------------------------------------------------- #
def build_repaired_quality_report(phase3i_rec, selected_cap, original_total, selected_metrics,
                                   repaired_rows, label_summary, sensitivity_rows):
    rbt = _rows_by_ticker_stats(repaired_rows)
    ages = [_to_float(r.get("feature_age_days")) for r in repaired_rows]
    ages = [a for a in ages if a is not None]
    age_stats = _age_stats(ages)
    scoring = [r["scoring_date"] for r in repaired_rows] if repaired_rows else []
    sectors_present = sorted({(r.get("sector") or "").strip() or "UNKNOWN"
                              for r in repaired_rows})
    cov = _label_coverage(repaired_rows)
    return {
        "source_phase": "3-I",
        "phase3i_recommendation": phase3i_rec,
        "selected_cap_days": selected_cap,
        "original_aligned_rows": original_total,
        "repaired_aligned_rows": len(repaired_rows),
        "retained_row_fraction": _round(len(repaired_rows) / original_total, 4)
        if original_total else 0.0,
        "repaired_aligned_tickers": sorted(rbt["counts"].keys()),
        "repaired_aligned_ticker_count": rbt["ticker_count"],
        "repaired_aligned_sectors": sectors_present,
        "repaired_aligned_sector_count": len(sectors_present),
        "repaired_start_date": min(scoring) if scoring else "",
        "repaired_end_date": max(scoring) if scoring else "",
        "feature_age_days_summary_after_repair": age_stats,
        "label_coverage_by_horizon_after_repair": {
            "21d": cov[21], "63d": cov[63], "126d": cov[126]},
        "leakage_failure_count_after_repair": count_leakage_failures(repaired_rows),
        "rows_by_ticker_after_repair": rbt["counts"],
        "tickers_dropped_by_selected_cap":
            selected_metrics["tickers_dropped"] if selected_metrics else [],
        "sectors_dropped_by_selected_cap":
            selected_metrics["sectors_dropped"] if selected_metrics else [],
        "staleness_cap_sensitivity_summary": sensitivity_rows,
        "recommendation": None,  # set by the caller after the decision is made
    }


# --------------------------------------------------------------------------- #
# Interpretation + safety flags
# --------------------------------------------------------------------------- #
def build_interpretation(recommendation, selected_cap):
    return {
        "repair_of_phase3i_alignment_only": True,
        "price_volume_only_modeling_stopped": True,
        "model_features_computed": True,
        "price_join_performed": True,
        "labels_computed": True,
        "labels_for_validation_only": True,
        "new_labels_created": False,
        "labels_filtered_only": True,
        "predictions_computed": False,
        "scores_computed": False,
        "portfolio_weights_computed": False,
        "model_trained": False,
        "research_model_trained": False,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "deployable_model_artifact_written": False,
        "model_training_ready": recommendation == REC_SUCCESS,
        "sec_public_data_used": True,
        "read_from_d_drive": False,
        "wrote_to_d_drive": False,
        "network_used": False,
        "full_128_ticker_ingestion": False,
        "results_context_remains_survivorship_biased": True,
        "production_edge_claimed": False,
        "selected_cap_days": selected_cap,
        "narrative": (
            "Phase 3-J is a controlled, repo-local repair of the Phase 3-I aligned panel. It read "
            "the committed Phase 3-I alignment result, confirmed it succeeded partially "
            "(FUNDAMENTAL_PRICE_ALIGNMENT_PARTIAL_SUCCESS) with zero leakage and routed here, and "
            "read the Phase 3-I aligned panel plus the Phase 3-H feature snapshot for source "
            "density - all repo-local on the C: drive. It re-ran no D: price alignment, fetched no "
            "SEC data, used no network, and created no new labels (it only filtered the existing "
            "Phase 3-I validation labels). It diagnosed feature staleness per ticker, evaluated "
            "maximum feature-age caps of 365 / 400 / 540 / 730 calendar days, selected the strictest "
            "defensible cap, and wrote a clean staleness-filtered repaired panel and supporting "
            "sensitivity / stale-rows / decision / quality / label artifacts. It fitted no model, "
            "computed no predictions, scores, or portfolio weights, trained no model, created no "
            "production model candidate, wrote no deployable model artifact, touched no database, "
            "ran no migration, restarted no prediction service, enabled no serving flag, read or "
            "wrote nothing on the D: drive, placed no orders, and traded nothing. The price panel "
            "remains current-as-of (survivorship-caveated) and SEC provides no earnings consensus "
            "or analyst revisions, so that provider gap stays open. This is a research alignment "
            "repair only and claims no production edge."),
    }


def build_safety_flags(recommendation):
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
        "network_used": False,
        "sec_public_data_used": True,
        "vendor_api_called": False,
        "paid_vendor_api_called": False,
        "data_purchase_made": False,
        "d_drive_read": False,
        "d_drive_written": False,
        "external_data_ingested": False,
        "production_data_ingested": False,
        "full_128_ticker_ingestion": False,
        "model_features_computed": True,
        "price_join_performed": True,
        "labels_computed": True,
        "labels_for_validation_only": True,
        "model_training_ready": recommendation == REC_SUCCESS,
        "model_trained": False,
    }


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #
def _write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in rows:
            out = {}
            for c in columns:
                val = r.get(c, "")
                out[c] = "" if val is None else val
            w.writerow(out)


def _dump_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run(result_json_path=RESULT_JSON, repaired_panel_path=REPAIRED_PANEL_CSV,
        staleness_sensitivity_path=STALENESS_SENSITIVITY_CSV,
        repaired_quality_path=REPAIRED_QUALITY_JSON,
        repaired_label_summary_path=REPAIRED_LABEL_SUMMARY_CSV,
        repair_decision_table_path=REPAIR_DECISION_TABLE_CSV,
        stale_rows_by_ticker_path=STALE_ROWS_BY_TICKER_CSV):
    phase3i = _load_json(PHASE3I_JSON)
    confirmed_3i = confirm_phase3i(phase3i)
    phase3i_rec = (phase3i or {}).get("recommendation", {}).get("recommendation")

    panel_rows, panel_header = load_panel(PHASE3I_PANEL_CSV)
    distinct_asof = distinct_asof_by_ticker(PHASE3H_SNAPSHOT_CSV)
    original_total = len(panel_rows)
    inputs_ok = bool(panel_rows) and bool(panel_header)

    all_tickers = {(r.get("ticker") or "").strip().upper() for r in panel_rows}
    all_sectors = {(r.get("sector") or "").strip() or "UNKNOWN" for r in panel_rows}

    # Per-ticker staleness diagnostics.
    stale_rows_table = diagnose_staleness(panel_rows, distinct_asof)

    # Staleness cap sensitivity.
    metrics_by_cap = {
        cap: _cap_metrics(panel_rows, cap, original_total, all_tickers, all_sectors)
        for cap in CANDIDATE_CAPS}
    sensitivity_rows = [_sensitivity_row(metrics_by_cap[cap]) for cap in CANDIDATE_CAPS]

    # Select the repair cap and decide.
    selected_cap, selection_basis = select_cap(metrics_by_cap)
    selected_metrics = metrics_by_cap[selected_cap] if selected_cap is not None else None
    recommendation = decide(confirmed_3i, inputs_ok, selected_cap, selection_basis,
                            selected_metrics)

    # Repaired panel rows (only on success/partial, i.e. a cap was selected).
    repaired_rows = selected_metrics["rows"] if selected_metrics is not None else []
    label_summary = build_label_summary(repaired_rows) if repaired_rows else []

    # Reporting artifacts.
    repaired_quality = build_repaired_quality_report(
        phase3i_rec, selected_cap, original_total, selected_metrics, repaired_rows,
        label_summary, sensitivity_rows)
    repaired_quality["recommendation"] = recommendation
    decision_table = build_decision_table(
        confirmed_3i, selected_cap, selected_metrics, recommendation)

    # Write artifacts.
    if repaired_rows:
        _write_csv(repaired_panel_path, panel_header, repaired_rows)
        _write_csv(repaired_label_summary_path, REPAIRED_LABEL_SUMMARY_COLUMNS, label_summary)
    _write_csv(staleness_sensitivity_path, STALENESS_SENSITIVITY_COLUMNS, sensitivity_rows)
    _write_csv(stale_rows_by_ticker_path, STALE_ROWS_BY_TICKER_COLUMNS, stale_rows_table)
    _write_csv(repair_decision_table_path, REPAIR_DECISION_TABLE_COLUMNS, decision_table)
    _dump_json(repaired_quality_path, repaired_quality)

    # Staleness diagnostics summary for the result JSON.
    panel_ages = [_to_float(r.get("feature_age_days")) for r in panel_rows]
    panel_ages = [a for a in panel_ages if a is not None]
    staleness_diagnostics = {
        "original_aligned_rows": original_total,
        "original_feature_age_days_summary": _age_stats(panel_ages),
        "rows_over_365": sum(1 for a in panel_ages if a > 365),
        "rows_over_400": sum(1 for a in panel_ages if a > 400),
        "rows_over_540": sum(1 for a in panel_ages if a > 540),
        "rows_over_730": sum(1 for a in panel_ages if a > 730),
        "fraction_over_365": _round(sum(1 for a in panel_ages if a > 365) / original_total, 4)
        if original_total else 0.0,
        "fraction_over_730": _round(sum(1 for a in panel_ages if a > 730) / original_total, 4)
        if original_total else 0.0,
        "tickers_with_staleness_problem":
            sorted(r["ticker"] for r in stale_rows_table if r["staleness_problem"]),
        "per_ticker": stale_rows_table,
    }

    selected_repair = {
        "selected_cap_days": selected_cap,
        "selection_basis": selection_basis,
        "candidate_caps": list(CANDIDATE_CAPS),
        "success_gate": {
            "min_tickers": SUCCESS_MIN_TICKERS, "min_rows": SUCCESS_MIN_ROWS,
            "label_coverage_gates": {"21d": COV_GATE[21], "63d": COV_GATE[63],
                                     "126d": COV_GATE[126]}},
        "selected_cap_metrics": _sensitivity_row(selected_metrics)
        if selected_metrics is not None else None,
    }

    repaired_panel_summary = {
        "repaired_aligned_rows": len(repaired_rows),
        "repaired_aligned_tickers": repaired_quality["repaired_aligned_tickers"],
        "repaired_aligned_ticker_count": repaired_quality["repaired_aligned_ticker_count"],
        "repaired_aligned_sectors": repaired_quality["repaired_aligned_sectors"],
        "repaired_aligned_sector_count": repaired_quality["repaired_aligned_sector_count"],
        "repaired_start_date": repaired_quality["repaired_start_date"],
        "repaired_end_date": repaired_quality["repaired_end_date"],
        "retained_row_fraction": repaired_quality["retained_row_fraction"],
        "feature_age_days_summary_after_repair":
            repaired_quality["feature_age_days_summary_after_repair"],
        "rows_by_ticker_after_repair": repaired_quality["rows_by_ticker_after_repair"],
        "panel_columns": panel_header,
    }

    result = {
        "phase": PHASE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "phase3i_result_json": "research/output/phase3i_fundamental_price_alignment.json",
            "phase3i_aligned_panel_csv":
                "research/output/phase3i_fundamental_price_alignment/"
                "aligned_feature_price_panel_20ticker_sample.csv",
            "phase3i_alignment_quality_report_json":
                "research/output/phase3i_fundamental_price_alignment/"
                "alignment_quality_report.json",
            "phase3i_label_summary_by_horizon_csv":
                "research/output/phase3i_fundamental_price_alignment/label_summary_by_horizon.csv",
            "phase3i_leakage_checks_csv":
                "research/output/phase3i_fundamental_price_alignment/leakage_checks.csv",
            "phase3i_date_alignment_summary_csv":
                "research/output/phase3i_fundamental_price_alignment/date_alignment_summary.csv",
            "phase3h_features_json": "research/output/phase3h_sec_fundamental_features.json",
            "phase3h_feature_snapshot_csv":
                "research/output/phase3h_sec_fundamental_features/"
                "feature_snapshot_20ticker_sample.csv",
            "phase3h_feature_quality_report_json":
                "research/output/phase3h_sec_fundamental_features/feature_quality_report.json",
        },
        "outputs_written": {
            "result_json": "research/output/phase3j_repaired_fundamental_price_alignment.json",
            "repaired_aligned_panel_csv":
                "research/output/phase3j_repaired_fundamental_price_alignment/"
                "repaired_aligned_panel_20ticker_sample.csv" if repaired_rows else None,
            "staleness_cap_sensitivity_csv":
                "research/output/phase3j_repaired_fundamental_price_alignment/"
                "staleness_cap_sensitivity.csv",
            "repaired_alignment_quality_report_json":
                "research/output/phase3j_repaired_fundamental_price_alignment/"
                "repaired_alignment_quality_report.json",
            "repaired_label_summary_by_horizon_csv":
                "research/output/phase3j_repaired_fundamental_price_alignment/"
                "repaired_label_summary_by_horizon.csv" if repaired_rows else None,
            "repair_decision_table_csv":
                "research/output/phase3j_repaired_fundamental_price_alignment/"
                "repair_decision_table.csv",
            "stale_rows_by_ticker_csv":
                "research/output/phase3j_repaired_fundamental_price_alignment/"
                "stale_rows_by_ticker.csv",
        },
        "phase3i_summary": {
            "phase3i_confirmed": confirmed_3i,
            "phase3i_recommendation": phase3i_rec,
            "phase3i_aligned_rows": original_total,
            "phase3i_leakage_failure_count":
                (phase3i or {}).get("leakage_check_summary", {}).get("leakage_failure_count"),
            "phase3i_median_feature_age_days":
                (phase3i or {}).get("aligned_panel_summary", {}).get(
                    "feature_age_days_summary", {}).get("median"),
        },
        "repair_config": {
            "candidate_caps_calendar_days": list(CANDIDATE_CAPS),
            "selection_rule": "strictest cap satisfying the success gate (>= %d tickers, >= %d "
                              "rows, leakage-free, label coverage >= 0.80/0.70/0.60 at 21/63/126d); "
                              "else strictest cap satisfying the partial gate (>= %d tickers, > 0 "
                              "rows, leakage-free); else no cap is sufficient"
                              % (SUCCESS_MIN_TICKERS, SUCCESS_MIN_ROWS, PARTIAL_MIN_TICKERS),
            "labels_filtered_only": True,
            "new_labels_created": False,
            "model_trained": False,
            "predictions_computed": False,
        },
        "staleness_diagnostics": staleness_diagnostics,
        "staleness_cap_sensitivity": sensitivity_rows,
        "selected_repair": selected_repair,
        "repaired_panel_summary": repaired_panel_summary,
        "repaired_label_summary": {
            "label_coverage_by_horizon_after_repair":
                repaired_quality["label_coverage_by_horizon_after_repair"],
            "by_horizon": label_summary,
        },
        "leakage_check_summary": {
            "leakage_failure_count_after_repair":
                repaired_quality["leakage_failure_count_after_repair"],
            "phase3i_leakage_failure_count":
                (phase3i or {}).get("leakage_check_summary", {}).get("leakage_failure_count"),
            "invariants_rechecked": [
                "active_feature_asof_date_present",
                "active_feature_asof_before_scoring_date",
                "active_feature_asof_after_or_equal_fiscal_period_end",
                "no_fiscal_period_end_as_availability_date",
            ],
            "all_checks_passed":
                repaired_quality["leakage_failure_count_after_repair"] == 0,
        },
        "repair_decision_summary": {
            "selected_cap_days": selected_cap,
            "decision_table": decision_table,
        },
        "source_limitations": list(_SOURCE_LIMITATIONS),
        "earnings_revisions_gap": {
            "sec_provides_earnings_consensus": False,
            "sec_provides_analyst_estimate_revisions": False,
            "gap_description": "SEC as-reported fundamentals support the trailing fundamental "
                               "features repaired here but provide no forward analyst consensus or "
                               "estimate revisions; a separate provider must be researched and "
                               "selected (see the Phase 3-E provider requirements matrix) before "
                               "earnings-surprise or revision-momentum features can be aligned.",
            "provider_selection_required": True,
        },
        "recommendation": build_recommendation(recommendation, selected_cap),
        "interpretation": build_interpretation(recommendation, selected_cap),
        "recommended_next_phase": build_recommended_next_phase(recommendation),
    }
    result.update(build_safety_flags(recommendation))

    _dump_json(result_json_path, result)
    return result


def main():
    result = run()
    rec = result["recommendation"]
    nxt = result["recommended_next_phase"]
    sr = result["selected_repair"]
    rps = result["repaired_panel_summary"]
    sd = result["staleness_diagnostics"]
    print("Phase %s - Repair Fundamental Price Alignment" % result["phase"])
    print("  phase3i confirmed        : %s"
          % result["phase3i_summary"]["phase3i_confirmed"]["all_confirmed"])
    print("  original aligned rows    : %s" % sd["original_aligned_rows"])
    print("  original median age      : %s"
          % sd["original_feature_age_days_summary"]["median"])
    print("  rows over 365/730        : %s / %s" % (sd["rows_over_365"], sd["rows_over_730"]))
    print("  staleness-problem tickers: %s" % sd["tickers_with_staleness_problem"])
    print("  --- cap sensitivity ---")
    for row in result["staleness_cap_sensitivity"]:
        print("    cap %4sd | rows %6s | tickers %2s | cov %s/%s/%s | leak %s | %s"
              % (row["cap_days"], row["aligned_rows_after_cap"], row["aligned_ticker_count"],
                 row["label_coverage_21d"], row["label_coverage_63d"], row["label_coverage_126d"],
                 row["leakage_failure_count"], row["recommendation_for_cap"]))
    print("  selected cap days        : %s (%s)" % (sr["selected_cap_days"], sr["selection_basis"]))
    print("  repaired aligned rows    : %s" % rps["repaired_aligned_rows"])
    print("  repaired tickers         : %s" % rps["repaired_aligned_ticker_count"])
    print("  repaired median age      : %s"
          % rps["feature_age_days_summary_after_repair"]["median"])
    print("  repaired date span       : %s -> %s"
          % (rps["repaired_start_date"], rps["repaired_end_date"]))
    print("  leakage after repair     : %s"
          % result["leakage_check_summary"]["leakage_failure_count_after_repair"])
    print("  network / d_drive r / w  : %s / %s / %s"
          % (result["network_used"], result["d_drive_read"], result["d_drive_written"]))
    print("  recommendation           : %s" % rec["recommendation"])
    print("  recommended next phase   : %s - %s" % (nxt["phase"], nxt["title"]))
    return result


if __name__ == "__main__":
    main()
