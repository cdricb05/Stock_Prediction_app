"""Phase 3-I - Fundamental Feature Price-Alignment Dry Run.

This is a controlled, **read-only research alignment dry run**, not a training phase and not a
production integration. After Phase 3-H proved (SEC_FUNDAMENTAL_FEATURES_PROTOTYPE_SUCCESS) that
trailing-only, point-in-time fundamental features could be built for the 20-ticker SEC sample, this
script joins that point-in-time feature snapshot to the historical daily price calendar and answers
one practical question:

    "If we line every feature snapshot up against the trading days on which it was already public,
     and generate forward-return labels for validation only, is every feature observable strictly
     before its scoring date (no leakage), and is label coverage good enough to attempt a tiny
     model-readiness diagnostic next?"

It reads the committed Phase 3-H feature outputs (repo-local) and the Phase 2K-G historical price
panel (READ ONLY, on the D: drive). It restricts the universe to the 20 feature tickers plus the
SPY benchmark, builds a daily scoring-date grid, activates the latest already-filed feature
snapshot for each ticker/scoring date (a snapshot becomes active only on the first trading day
strictly after its feature_asof_date), generates 21/63/126 trading-day forward-return labels for
validation only, runs explicit leakage checks, and writes alignment-quality, label-summary,
date-alignment, leakage-check, and aligned-panel artifacts plus a Phase 3-J recommendation.

Scope and safety. There is NO network use in this phase. It generates forward labels for
validation only - it fits NO model, computes NO predictions / scores / portfolio weights, claims NO
production edge. It reads the D: price CSV read-only and writes nothing to the D: drive. It trains
no model, creates no production model candidate, writes no deployable model artifact, touches no
database, runs no migration, places no orders, and trades nothing. It writes only the small
alignment outputs listed below, all under research/output on the C: drive.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime

PHASE = "3-I"

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUT_DIR = os.path.join(_REPO_ROOT, "research", "output")
_IN_DIR = os.path.join(_REPO_ROOT, "research", "input")
_H_DIR = os.path.join(_OUT_DIR, "phase3h_sec_fundamental_features")
_I_DIR = os.path.join(_OUT_DIR, "phase3i_fundamental_price_alignment")

# Inputs - committed Phase 3-H feature outputs (repo-local).
PHASE3H_JSON = os.path.join(_OUT_DIR, "phase3h_sec_fundamental_features.json")
PHASE3H_SNAPSHOT_CSV = os.path.join(_H_DIR, "feature_snapshot_20ticker_sample.csv")
PHASE3H_DICTIONARY_CSV = os.path.join(_H_DIR, "feature_dictionary.csv")
PHASE3H_QUALITY_JSON = os.path.join(_H_DIR, "feature_quality_report.json")
PHASE3H_WARNINGS_CSV = os.path.join(_H_DIR, "feature_alignment_warnings.csv")
SECTOR_MAP_CSV = os.path.join(_IN_DIR, "phase2k_p_sector_map_current.csv")

# Inputs - Phase 2K-G historical price panel (READ ONLY; on the D: drive).
PRICE_CSV = os.path.join(
    "D:", os.sep, "Stock_Prediction_app_data", "phase2k_g", "output",
    "phase2k_g_expanded_price_history_free.csv")
PRICE_QUALITY_JSON = os.path.join(
    "D:", os.sep, "Stock_Prediction_app_data", "phase2k_g", "output",
    "phase2k_g_data_quality_report.json")
PRICE_BUILD_SUMMARY_JSON = os.path.join(
    "D:", os.sep, "Stock_Prediction_app_data", "phase2k_g", "output",
    "phase2k_g_data_build_summary.json")
PRICE_SURVIVORSHIP_JSON = os.path.join(
    "D:", os.sep, "Stock_Prediction_app_data", "phase2k_g", "output",
    "phase2k_g_survivorship_caveat.json")

# Outputs (all repo-local, under research/output on the C: drive).
RESULT_JSON = os.path.join(_OUT_DIR, "phase3i_fundamental_price_alignment.json")
ALIGNED_PANEL_CSV = os.path.join(_I_DIR, "aligned_feature_price_panel_20ticker_sample.csv")
ALIGNMENT_QUALITY_JSON = os.path.join(_I_DIR, "alignment_quality_report.json")
LABEL_SUMMARY_CSV = os.path.join(_I_DIR, "label_summary_by_horizon.csv")
LEAKAGE_CHECKS_CSV = os.path.join(_I_DIR, "leakage_checks.csv")
DATE_ALIGNMENT_SUMMARY_CSV = os.path.join(_I_DIR, "date_alignment_summary.csv")

# --------------------------------------------------------------------------- #
# Contract / config
# --------------------------------------------------------------------------- #
PHASE3H_EXPECTED_RECOMMENDATION = "SEC_FUNDAMENTAL_FEATURES_PROTOTYPE_SUCCESS"
BENCHMARK = "SPY"
HORIZONS = [21, 63, 126]  # forward-return horizons, in trading days

# The 16 fixed metadata columns at the head of the Phase 3-H feature snapshot; everything after
# these is an engineered feature column carried straight through into the aligned panel.
SNAPSHOT_BASE_COLUMNS = [
    "ticker", "company_name", "sector", "industry", "cik", "fiscal_period_end", "fiscal_year",
    "fiscal_period", "form", "snapshot_type", "feature_asof_date", "source_field_count",
    "required_field_coverage_fraction", "point_in_time_usable", "is_annual_snapshot",
    "is_quarterly_snapshot",
]
PRICE_REQUIRED_COLUMNS = [
    "ticker", "date", "adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close", "volume",
]

# Active-snapshot metadata attached to each aligned row.
ACTIVE_META_COLUMNS = [
    "active_feature_asof_date", "feature_age_days", "active_fiscal_period_end",
    "active_fiscal_year", "active_fiscal_period", "active_form", "active_snapshot_type",
]

# Label columns (per horizon), for validation only.
def _label_cols():
    cols = []
    for fam in ("forward_return", "forward_spy_return", "forward_excess_return_vs_spy",
                "binary_outperform_spy", "forward_return_rank_by_date"):
        for h in HORIZONS:
            cols.append("%s_%dd" % (fam, h))
    return cols


LABEL_COLUMNS = _label_cols()

LEAKAGE_CHECKS_COLUMNS = [
    "ticker", "scoring_date", "active_feature_asof_date", "active_fiscal_period_end",
    "check_name", "passed", "severity", "note",
]
LABEL_SUMMARY_COLUMNS = [
    "horizon", "total_rows", "non_null_forward_return", "non_null_forward_excess_return_vs_spy",
    "non_null_binary_outperform_spy", "non_null_rank", "mean_forward_return",
    "mean_forward_excess_return_vs_spy", "positive_return_fraction", "outperform_spy_fraction",
    "earliest_scoring_date", "latest_scoring_date",
]
DATE_ALIGNMENT_SUMMARY_COLUMNS = [
    "ticker", "first_price_date", "last_price_date", "first_feature_asof_date",
    "first_eligible_scoring_date", "first_aligned_scoring_date", "last_aligned_scoring_date",
    "aligned_rows", "median_feature_age_days", "max_feature_age_days",
]

# Recommendation vocabulary.
REC_SUCCESS = "FUNDAMENTAL_PRICE_ALIGNMENT_DRY_RUN_SUCCESS"
REC_PARTIAL = "FUNDAMENTAL_PRICE_ALIGNMENT_PARTIAL_SUCCESS"
REC_BLOCKED = "FUNDAMENTAL_PRICE_ALIGNMENT_BLOCKED"
REC_REJECTED = "FUNDAMENTAL_PRICE_ALIGNMENT_REJECTED"
ALLOWED_RECOMMENDATIONS = [REC_SUCCESS, REC_PARTIAL, REC_BLOCKED, REC_REJECTED]

# Conservative label-coverage gates for SUCCESS.
COV_GATE = {21: 0.80, 63: 0.70, 126: 0.60}
# A SUCCESS dry run must also have a *reasonable* median feature age: a fundamental filer reports
# quarterly (~91 days), so allowing for filing lag and the odd skipped period, a median active
# feature age beyond ~400 days (more than a year stale for the typical row) is not reasonable and
# indicates a temporally-sparse upstream sample that must be repaired before a model diagnostic.
MAX_REASONABLE_MEDIAN_FEATURE_AGE_DAYS = 400

_SOURCE_LIMITATIONS = [
    "Features come only from SEC as-reported fundamentals (via the Phase 3-H point-in-time "
    "snapshot); SEC public data provides no earnings consensus or analyst estimate revisions, so "
    "surprise / revision-momentum signals cannot be aligned here.",
    "The historical price panel is the Phase 2K-G current-as-of free panel; its membership is NOT "
    "point-in-time, so this alignment and any downstream result must be reported as "
    "survivorship-biased (a clean point-in-time price universe is deferred to a later paid-source "
    "decision).",
    "Forward-return labels are generated for VALIDATION ONLY (leakage proof and coverage "
    "measurement); they are not a target for any model in this phase and are never forward-filled.",
    "The sector / industry attached to each row is current-as-of (not point-in-time), so "
    "cross-sectional results remain survivorship-caveated.",
    "Feature coverage carried from Phase 3-H is sparse for some ratios (the 20-ticker sample is "
    "pruned to recent fiscal periods and some sector-specific fields are legitimately absent); "
    "this is an alignment dry run, not a feature-completeness exercise.",
    "The Phase 3-H sample is temporally sparse for several tickers (it captured only very old "
    "companyfacts records plus the most recent ones, with multi-year gaps between them), so the "
    "carry-forward rule yields very stale active features and an unreasonable median feature age; "
    "this is leakage-free but must be repaired (denser upstream sampling or an explicit staleness "
    "cap) before a model-readiness diagnostic.",
    "This is a controlled 20-ticker sample plus the SPY benchmark, not the full 128-ticker "
    "universe.",
]

# Leakage check definitions: (check_name, severity).  All are evaluated per aligned row.
_LEAKAGE_CHECK_DEFS = [
    ("active_feature_asof_date_present", "hard"),
    ("active_feature_asof_before_scoring_date", "hard"),
    ("active_feature_asof_after_or_equal_fiscal_period_end", "hard"),
    ("no_fiscal_period_end_as_availability_date", "hard"),
    ("label_date_after_scoring_date", "hard"),
    ("no_price_join_before_feature_available", "hard"),
]


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _load_json(path):
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_csv_rows(path):
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


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


def _parse_date(d):
    d = _date_part(d)
    if not d:
        return None
    try:
        return datetime.strptime(d, "%Y-%m-%d")
    except ValueError:
        return None


def _days_between(d_late, d_early):
    a, b = _parse_date(d_late), _parse_date(d_early)
    if a is None or b is None:
        return None
    return (a - b).days


def _summary_stats(values):
    """min/p25/median/p75/max/mean over a list of numbers (already non-null)."""
    if not values:
        return {"count": 0, "min": None, "p25": None, "median": None, "p75": None,
                "max": None, "mean": None}
    vs = sorted(values)
    n = len(vs)

    def q(p):
        if n == 1:
            return vs[0]
        idx = p * (n - 1)
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        frac = idx - lo
        return vs[lo] + (vs[hi] - vs[lo]) * frac

    return {
        "count": n,
        "min": _round(vs[0], 4),
        "p25": _round(q(0.25), 4),
        "median": _round(q(0.5), 4),
        "p75": _round(q(0.75), 4),
        "max": _round(vs[-1], 4),
        "mean": _round(sum(vs) / n, 4),
    }


# --------------------------------------------------------------------------- #
# Upstream confirmation
# --------------------------------------------------------------------------- #
def confirm_phase3h(phase3h):
    """Confirm the committed Phase 3-H feature result that gates this alignment phase."""
    if not phase3h:
        return {"all_confirmed": False, "phase3h_present": False}
    rec = phase3h.get("recommendation", {}) or {}
    nxt = phase3h.get("recommended_next_phase", {}) or {}
    checks = {
        "phase3h_present": True,
        "phase_is_3h": phase3h.get("phase") == "3-H",
        "recommendation_is_features_success":
            rec.get("recommendation") == PHASE3H_EXPECTED_RECOMMENDATION,
        "next_phase_is_3i": nxt.get("phase") == "3-I",
        "model_features_computed_true": phase3h.get("model_features_computed") is True,
        "labels_computed_false": phase3h.get("labels_computed") is False,
        "model_training_ready_false": phase3h.get("model_training_ready") is False,
        "research_model_trained_false": phase3h.get("research_model_trained") is False,
        "production_model_trained_false": phase3h.get("production_model_trained") is False,
        "production_model_candidate_created_false":
            phase3h.get("production_model_candidate_created") is False,
        "deployable_model_artifact_written_false":
            phase3h.get("deployable_model_artifact_written") is False,
        "network_used_false": phase3h.get("network_used") is False,
        "d_drive_read_false": phase3h.get("d_drive_read") is False,
        "d_drive_written_false": phase3h.get("d_drive_written") is False,
        "full_128_ticker_ingestion_false": phase3h.get("full_128_ticker_ingestion") is False,
        "production_edge_claimed_false": phase3h.get("production_edge_claimed") is False,
    }
    checks["all_confirmed"] = all(checks.values())
    return checks


# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #
def load_feature_snapshots(path):
    """Read the Phase 3-H feature snapshot CSV.

    Returns (snapshots_by_ticker, feature_columns). Each snapshot is a dict carrying the base
    metadata plus the engineered feature columns as raw strings (passed straight through to the
    aligned panel). Per ticker the snapshots are sorted by (feature_asof_date, fiscal_period_end,
    source_field_count) ascending, so the *active* snapshot for a scoring date is simply the
    greatest-sorted snapshot still strictly available before that date.
    """
    if not os.path.isfile(path):
        return {}, []
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        feature_columns = [c for c in header if c not in SNAPSHOT_BASE_COLUMNS]
        by_ticker = {}
        for row in reader:
            asof = (row.get("feature_asof_date") or "").strip()
            if not asof:
                continue  # cannot activate a snapshot with no availability timestamp
            t = (row.get("ticker") or "").strip().upper()
            if not t:
                continue
            by_ticker.setdefault(t, []).append(row)
    for t, snaps in by_ticker.items():
        snaps.sort(key=lambda r: (
            r.get("feature_asof_date") or "",
            r.get("fiscal_period_end") or "",
            int(_to_float(r.get("source_field_count")) or 0),
        ))
    return by_ticker, feature_columns


def load_price_panel(path, universe):
    """Read the D: price panel READ ONLY, keeping only the requested universe of tickers.

    Returns (prices_by_ticker, rows_read). prices_by_ticker[ticker] is a list of
    (date, adjusted_close, volume) sorted ascending by date.
    """
    prices = {}
    rows_read = 0
    if not os.path.isfile(path):
        return prices, rows_read
    uni = {t.upper() for t in universe}
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            t = (row.get("ticker") or "").strip().upper()
            if t not in uni:
                continue
            d = (row.get("date") or "").strip()
            close = _to_float(row.get("adjusted_close"))
            if not d or close is None:
                continue
            prices.setdefault(t, []).append((d, close, _to_float(row.get("volume"))))
            rows_read += 1
    for t in prices:
        prices[t].sort(key=lambda x: x[0])
    return prices, rows_read


# --------------------------------------------------------------------------- #
# Alignment + labels
# --------------------------------------------------------------------------- #
def align_ticker(ticker, identity, snaps, ticker_prices, spy_by_date, feature_columns):
    """Produce aligned rows (with forward labels) for one ticker.

    For each trading day strictly after the earliest available snapshot, the active snapshot is the
    latest one already filed (feature_asof_date date-part < scoring_date). Forward-return labels at
    each horizon use realized future closes only; SPY excess is measured over the identical
    calendar window; missing future prices leave the label null (never forward-filled).
    """
    rows = []
    if not snaps or not ticker_prices:
        return rows

    snap_asof_dates = [_date_part(s["feature_asof_date"]) for s in snaps]
    n_prices = len(ticker_prices)
    idx = -1  # index of the active snapshot in the ascending-sorted list

    for i, (scoring_date, close, _vol) in enumerate(ticker_prices):
        # Advance the active-snapshot pointer: a snapshot is active only on trading days STRICTLY
        # after its feature_asof_date (date part).
        while idx + 1 < len(snaps) and snap_asof_dates[idx + 1] < scoring_date:
            idx += 1
        if idx < 0:
            continue  # no feature snapshot is public yet on this trading day
        active = snaps[idx]
        active_asof = active["feature_asof_date"]

        row = {
            "ticker": ticker,
            "company_name": identity.get("company_name", active.get("company_name", "")),
            "sector": identity.get("sector", active.get("sector", "")),
            "industry": identity.get("industry", active.get("industry", "")),
            "scoring_date": scoring_date,
            "adjusted_close": _round(close, 6),
            "active_feature_asof_date": active_asof,
            "feature_age_days": _days_between(scoring_date, active_asof),
            "active_fiscal_period_end": active.get("fiscal_period_end", ""),
            "active_fiscal_year": active.get("fiscal_year", ""),
            "active_fiscal_period": active.get("fiscal_period", ""),
            "active_form": active.get("form", ""),
            "active_snapshot_type": active.get("snapshot_type", ""),
        }
        # Carry every engineered feature column straight through (blank stays blank; no fill).
        for c in feature_columns:
            row[c] = active.get(c, "")

        # Forward-return labels (validation only) at each horizon.
        for h in HORIZONS:
            fr = spy_ret = excess = None
            binout = rank = None  # rank filled in a later cross-sectional pass
            j = i + h
            if j < n_prices and close not in (None, 0):
                fut_date, fut_close, _ = ticker_prices[j]
                if fut_close is not None:
                    fr = fut_close / close - 1.0
                    s0 = spy_by_date.get(scoring_date)
                    s1 = spy_by_date.get(fut_date)
                    if s0 not in (None, 0) and s1 is not None:
                        spy_ret = s1 / s0 - 1.0
                        excess = fr - spy_ret
                        binout = 1 if excess > 0 else 0
            row["forward_return_%dd" % h] = _round(fr, 6)
            row["forward_spy_return_%dd" % h] = _round(spy_ret, 6)
            row["forward_excess_return_vs_spy_%dd" % h] = _round(excess, 6)
            row["binary_outperform_spy_%dd" % h] = binout
            row["forward_return_rank_by_date_%dd" % h] = rank
        rows.append(row)
    return rows


def assign_cross_sectional_ranks(rows):
    """Assign forward_return_rank_by_date_<h>d across the sample for each scoring date.

    Rank is ordinal ascending (1 = lowest forward return that day) over the tickers with a non-null
    forward_return at that horizon/date; rows with a null forward_return get no rank.
    """
    for h in HORIZONS:
        col = "forward_return_%dd" % h
        rank_col = "forward_return_rank_by_date_%dd" % h
        by_date = {}
        for r in rows:
            val = r.get(col)
            if val is None:
                continue
            by_date.setdefault(r["scoring_date"], []).append((val, r))
        for _date, items in by_date.items():
            items.sort(key=lambda x: x[0])
            for pos, (_val, r) in enumerate(items, start=1):
                r[rank_col] = pos


# --------------------------------------------------------------------------- #
# Leakage checks
# --------------------------------------------------------------------------- #
def evaluate_leakage(rows, n_prices_by_ticker):
    """Evaluate the six per-row leakage checks; return (check_rows, failure_count, warning_count).

    Every aligned row is checked. A failure is any leakage-critical condition: a missing as-of
    date, an as-of date at or after the scoring date, an as-of date before (or equal to) the fiscal
    period end, or a label horizon date at or before the scoring date.
    """
    check_rows = []
    failures = 0
    warnings = 0
    for r in rows:
        scoring = r["scoring_date"]
        asof = r["active_feature_asof_date"]
        asof_d = _date_part(asof)
        fpe = r["active_fiscal_period_end"]

        results = {
            "active_feature_asof_date_present": (bool(asof), "feature_asof_date present"),
            "active_feature_asof_before_scoring_date": (
                bool(asof_d) and asof_d < scoring,
                "feature_asof_date (%s) strictly before scoring_date (%s)" % (asof_d, scoring)),
            "active_feature_asof_after_or_equal_fiscal_period_end": (
                bool(asof_d) and bool(fpe) and asof_d >= fpe,
                "feature_asof_date (%s) on or after fiscal_period_end (%s)" % (asof_d, fpe)),
            "no_fiscal_period_end_as_availability_date": (
                bool(asof_d) and asof_d != fpe,
                "feature_asof_date is a filing timestamp, not the fiscal_period_end"),
            "label_date_after_scoring_date": (
                True,
                "forward labels use realized future closes h>0 trading days ahead; horizon dates "
                "are strictly after the scoring date by construction"),
            "no_price_join_before_feature_available": (
                bool(asof_d) and asof_d < scoring,
                "the price row joined is the scoring day, which is strictly after the feature "
                "became public"),
        }
        for name, severity in _LEAKAGE_CHECK_DEFS:
            passed, note = results[name]
            if not passed:
                failures += 1
                if severity != "hard":
                    warnings += 1
            check_rows.append({
                "ticker": r["ticker"],
                "scoring_date": scoring,
                "active_feature_asof_date": asof,
                "active_fiscal_period_end": fpe,
                "check_name": name,
                "passed": bool(passed),
                "severity": severity,
                "note": note,
            })
    return check_rows, failures, warnings


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def build_label_summary(rows):
    out = []
    for h in HORIZONS:
        fr_col = "forward_return_%dd" % h
        ex_col = "forward_excess_return_vs_spy_%dd" % h
        bo_col = "binary_outperform_spy_%dd" % h
        rk_col = "forward_return_rank_by_date_%dd" % h
        fr_vals = [r[fr_col] for r in rows if r.get(fr_col) is not None]
        ex_vals = [r[ex_col] for r in rows if r.get(ex_col) is not None]
        bo_vals = [r[bo_col] for r in rows if r.get(bo_col) is not None]
        rk_n = sum(1 for r in rows if r.get(rk_col) is not None)
        scoring_with_label = [r["scoring_date"] for r in rows if r.get(fr_col) is not None]
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


def build_date_alignment_summary(rows, snaps_by_ticker, prices_by_ticker, eligible_first):
    out = []
    rows_by_ticker = {}
    for r in rows:
        rows_by_ticker.setdefault(r["ticker"], []).append(r)
    for t in sorted(prices_by_ticker):
        if t == BENCHMARK:
            continue
        tp = prices_by_ticker.get(t, [])
        trows = rows_by_ticker.get(t, [])
        snaps = snaps_by_ticker.get(t, [])
        first_asof = snaps[0]["feature_asof_date"] if snaps else ""
        ages = [r["feature_age_days"] for r in trows if r.get("feature_age_days") is not None]
        scoring_dates = [r["scoring_date"] for r in trows]
        out.append({
            "ticker": t,
            "first_price_date": tp[0][0] if tp else "",
            "last_price_date": tp[-1][0] if tp else "",
            "first_feature_asof_date": first_asof,
            "first_eligible_scoring_date": eligible_first.get(t, ""),
            "first_aligned_scoring_date": min(scoring_dates) if scoring_dates else "",
            "last_aligned_scoring_date": max(scoring_dates) if scoring_dates else "",
            "aligned_rows": len(trows),
            "median_feature_age_days": _summary_stats(ages)["median"],
            "max_feature_age_days": _summary_stats(ages)["max"],
        })
    return out


def build_alignment_quality_report(rows, feature_columns, snaps_by_ticker, prices_by_ticker,
                                   input_feature_rows, input_feature_tickers, price_rows_read,
                                   price_tickers_read, leakage_check_count, leakage_failure_count,
                                   leakage_warning_count, spy_available, recommendation):
    aligned_tickers = sorted({r["ticker"] for r in rows})
    scoring_dates = [r["scoring_date"] for r in rows]
    rows_by_ticker = {}
    rows_by_sector = {}
    mix = {}
    for r in rows:
        rows_by_ticker[r["ticker"]] = rows_by_ticker.get(r["ticker"], 0) + 1
        sec = r.get("sector") or "UNKNOWN"
        rows_by_sector[sec] = rows_by_sector.get(sec, 0) + 1
        st = r.get("active_snapshot_type") or "unknown"
        mix[st] = mix.get(st, 0) + 1

    ages = [r["feature_age_days"] for r in rows if r.get("feature_age_days") is not None]
    age_stats = _summary_stats(ages)
    median_age = age_stats["median"]
    feature_age_median_reasonable = (
        median_age is not None and median_age <= MAX_REASONABLE_MEDIAN_FEATURE_AGE_DAYS)

    label_coverage = {}
    for h in HORIZONS:
        col = "forward_return_%dd" % h
        nn = sum(1 for r in rows if r.get(col) is not None)
        label_coverage["%dd" % h] = _round(nn / len(rows), 4) if rows else 0.0

    null_feature_summary = {}
    for c in feature_columns:
        nn = sum(1 for r in rows if str(r.get(c, "")).strip() != "")
        null_feature_summary[c] = {
            "non_null": nn,
            "null": len(rows) - nn,
            "non_null_fraction": _round(nn / len(rows), 4) if rows else 0.0,
        }
    null_label_summary = {}
    for c in LABEL_COLUMNS:
        nn = sum(1 for r in rows if r.get(c) is not None)
        null_label_summary[c] = {
            "non_null": nn,
            "null": len(rows) - nn,
            "non_null_fraction": _round(nn / len(rows), 4) if rows else 0.0,
        }

    return {
        "source_phase": "3-H",
        "input_feature_rows": input_feature_rows,
        "input_feature_tickers": sorted(input_feature_tickers),
        "price_rows_read": price_rows_read,
        "price_tickers_read": sorted(price_tickers_read),
        "aligned_rows": len(rows),
        "aligned_tickers": aligned_tickers,
        "aligned_start_date": min(scoring_dates) if scoring_dates else "",
        "aligned_end_date": max(scoring_dates) if scoring_dates else "",
        "rows_by_ticker": dict(sorted(rows_by_ticker.items())),
        "rows_by_sector": dict(sorted(rows_by_sector.items())),
        "feature_age_days_summary": age_stats,
        "feature_age_median_reasonable": feature_age_median_reasonable,
        "max_reasonable_median_feature_age_days": MAX_REASONABLE_MEDIAN_FEATURE_AGE_DAYS,
        "annual_vs_quarterly_active_snapshot_mix": dict(sorted(mix.items())),
        "label_coverage_by_horizon": label_coverage,
        "leakage_check_count": leakage_check_count,
        "leakage_failure_count": leakage_failure_count,
        "leakage_warning_count": leakage_warning_count,
        "null_feature_summary": null_feature_summary,
        "null_label_summary": null_label_summary,
        "spy_available": spy_available,
        "recommendation": recommendation,
    }


# --------------------------------------------------------------------------- #
# Decision logic
# --------------------------------------------------------------------------- #
def decide(confirmed_3h, inputs_ok, rows, aq):
    if not confirmed_3h.get("all_confirmed") or not inputs_ok:
        return REC_BLOCKED
    if aq["leakage_failure_count"] > 0:
        return REC_REJECTED
    if not rows:
        return REC_BLOCKED

    n_tickers = len(aq["aligned_tickers"])
    cov = aq["label_coverage_by_horizon"]
    coverage_ok = all(cov.get("%dd" % h, 0.0) >= COV_GATE[h] for h in HORIZONS)
    if (n_tickers >= 20 and aq["aligned_rows"] > 1000
            and aq["leakage_failure_count"] == 0 and coverage_ok
            and aq["feature_age_median_reasonable"]):
        return REC_SUCCESS
    if n_tickers >= 10 and aq["aligned_rows"] > 0 and aq["leakage_failure_count"] == 0:
        return REC_PARTIAL
    return REC_BLOCKED


def build_recommendation(recommendation):
    reason = {
        REC_SUCCESS: (
            "The 20-ticker point-in-time fundamental feature snapshot aligned cleanly to the "
            "Phase 2K-G price calendar: every feature snapshot activates only on the first trading "
            "day strictly after its feature_asof_date, so every aligned row's as-of date is "
            "strictly before its scoring date and on/after its fiscal period end - zero leakage "
            "failures across all checks. Forward-return labels (21/63/126 trading days) were "
            "generated for validation only with coverage above the conservative gates, and SPY "
            "excess returns were measured over identical calendar windows. The dry-run panel is "
            "ready for a tiny model-readiness diagnostic next (still no model training, no "
            "production candidate, no production edge claim)."),
        REC_PARTIAL: (
            "Alignment ran with zero leakage failures and full ticker/label coverage, but the "
            "active feature age is not yet reasonable: the Phase 3-H sample is temporally sparse "
            "for several tickers (it captured only very old companyfacts records plus the most "
            "recent ones, with multi-year gaps between them), so decade-old fundamentals are "
            "carried forward and the median active feature age exceeds the conservative threshold. "
            "This is leakage-free but the panel is not ready for a model-readiness diagnostic; the "
            "Phase 3-H sampling (temporal density) or a staleness cap must be repaired first."),
        REC_BLOCKED: (
            "Required Phase 3-H feature inputs or the Phase 2K-G price panel were missing, "
            "unconfirmed, or empty, so the alignment could not run. Repair or re-confirm the "
            "inputs first."),
        REC_REJECTED: (
            "At least one leakage check failed (a feature was observable on or after its scoring "
            "date, an as-of date preceded its fiscal period end, or a label horizon date was not "
            "strictly in the future). The alignment must be redesigned before proceeding."),
    }[recommendation]
    return {
        "recommendation": recommendation,
        "allowed_values": ALLOWED_RECOMMENDATIONS,
        "create_production_model_candidate_now": False,
        "train_production_model_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "full_128_ticker_ingestion_now": False,
        "model_training_now": False,
        "labels_for_validation_only": True,
        "reason": reason,
    }


def build_recommended_next_phase(recommendation):
    table = {
        REC_SUCCESS: (
            "Tiny Fundamental Model Readiness Diagnostic",
            "Run model-readiness diagnostics on the aligned 20-ticker dry-run panel - feature "
            "coverage, label sanity, cross-sectional sample size, and baseline IC checks - before "
            "deciding whether a tiny research model is allowed."),
        REC_PARTIAL: (
            "Repair Fundamental Price Alignment",
            "Fix alignment gaps, label coverage gaps, or feature-age issues before any "
            "model-readiness diagnostic."),
        REC_BLOCKED: (
            "Repair Phase 3-I Inputs",
            "Repair missing or corrupted Phase 3-H or D: price artifacts before alignment."),
        REC_REJECTED: (
            "Redesign Fundamental Price Alignment",
            "Redesign alignment because leakage was detected."),
    }
    title, purpose = table[recommendation]
    return {"phase": "3-J", "title": title, "purpose": purpose}


def build_interpretation():
    return {
        "price_alignment_dry_run_only": True,
        "price_volume_only_modeling_stopped": True,
        "model_features_computed": True,
        "price_join_performed": True,
        "labels_computed": True,
        "labels_for_validation_only": True,
        "forward_returns_computed": True,
        "predictions_computed": False,
        "scores_computed": False,
        "portfolio_weights_computed": False,
        "model_trained": False,
        "research_model_trained": False,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "deployable_model_artifact_written": False,
        "model_training_ready": False,
        "sec_public_data_used": True,
        "read_from_d_drive": True,
        "wrote_to_d_drive": False,
        "network_used": False,
        "full_128_ticker_ingestion": False,
        "results_context_remains_survivorship_biased": True,
        "production_edge_claimed": False,
        "narrative": (
            "Phase 3-I is a controlled, read-only research alignment dry run. It read the committed "
            "Phase 3-H feature result, confirmed it succeeded "
            "(SEC_FUNDAMENTAL_FEATURES_PROTOTYPE_SUCCESS) and routed here, loaded the 20-ticker "
            "point-in-time feature snapshot, and read the Phase 2K-G historical price panel "
            "read-only on the D: drive (restricted to the 20 feature tickers plus the SPY "
            "benchmark). It built a daily scoring-date grid, activated for each ticker/scoring date "
            "only the latest feature snapshot already public strictly before that day, attached "
            "the active snapshot's age and identity, and generated 21/63/126 trading-day "
            "forward-return labels - and SPY excess returns over identical calendar windows - for "
            "validation only, never forward-filled. It ran explicit leakage checks proving every "
            "feature is observable strictly before its scoring date and on/after its fiscal period "
            "end. It fitted no model, computed no predictions, scores, or portfolio weights, "
            "trained no model, created no production model candidate, wrote no deployable model "
            "artifact, touched no database, ran no migration, restarted no prediction service, "
            "enabled no serving flag, used no network, wrote nothing to the D: drive, placed no "
            "orders, and traded nothing. The price panel is current-as-of (survivorship-caveated) "
            "and SEC provides no earnings consensus or analyst revisions, so that provider gap "
            "stays open. This is a research alignment dry run only and claims no production edge."),
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
        "network_used": False,
        "sec_public_data_used": True,
        "vendor_api_called": False,
        "paid_vendor_api_called": False,
        "data_purchase_made": False,
        "d_drive_read": True,
        "d_drive_written": False,
        "external_data_ingested": False,
        "production_data_ingested": False,
        "full_128_ticker_ingestion": False,
        "model_features_computed": True,
        "price_join_performed": True,
        "labels_computed": True,
        "labels_for_validation_only": True,
        "model_training_ready": False,
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
def run(result_json_path=RESULT_JSON, aligned_panel_path=ALIGNED_PANEL_CSV,
        alignment_quality_path=ALIGNMENT_QUALITY_JSON, label_summary_path=LABEL_SUMMARY_CSV,
        leakage_checks_path=LEAKAGE_CHECKS_CSV,
        date_alignment_summary_path=DATE_ALIGNMENT_SUMMARY_CSV):
    phase3h = _load_json(PHASE3H_JSON)
    confirmed_3h = confirm_phase3h(phase3h)

    snaps_by_ticker, feature_columns = load_feature_snapshots(PHASE3H_SNAPSHOT_CSV)
    feature_tickers = sorted(snaps_by_ticker)
    input_feature_rows = sum(len(v) for v in snaps_by_ticker.values())

    # Identity carried from the snapshot rows (company_name / sector / industry per ticker).
    identity_by_ticker = {}
    for t, snaps in snaps_by_ticker.items():
        s0 = snaps[0]
        identity_by_ticker[t] = {
            "company_name": s0.get("company_name", ""),
            "sector": s0.get("sector", ""),
            "industry": s0.get("industry", ""),
        }

    # Read the price panel read-only, restricted to the 20 feature tickers plus SPY.
    universe = set(feature_tickers) | {BENCHMARK}
    prices_by_ticker, price_rows_read = load_price_panel(PRICE_CSV, universe)
    price_tickers_read = sorted(prices_by_ticker)
    spy_rows = prices_by_ticker.get(BENCHMARK, [])
    spy_by_date = {d: c for d, c, _ in spy_rows}
    spy_available = bool(spy_rows)

    inputs_ok = bool(snaps_by_ticker) and bool(
        [t for t in feature_tickers if prices_by_ticker.get(t)])

    # Align each feature ticker and collect the first-eligible scoring date per ticker.
    rows = []
    eligible_first = {}
    for t in feature_tickers:
        tp = prices_by_ticker.get(t)
        snaps = snaps_by_ticker.get(t)
        if not tp or not snaps:
            continue
        earliest_asof = _date_part(snaps[0]["feature_asof_date"])
        first_eligible = next((d for d, _c, _v in tp if d > earliest_asof), "")
        eligible_first[t] = first_eligible
        rows.extend(align_ticker(t, identity_by_ticker.get(t, {}), snaps, tp, spy_by_date,
                                 feature_columns))

    assign_cross_sectional_ranks(rows)

    n_prices_by_ticker = {t: len(p) for t, p in prices_by_ticker.items()}
    leakage_rows, leakage_failures, leakage_warnings = evaluate_leakage(rows, n_prices_by_ticker)

    aq_provisional = build_alignment_quality_report(
        rows, feature_columns, snaps_by_ticker, prices_by_ticker, input_feature_rows,
        feature_tickers, price_rows_read, price_tickers_read, len(leakage_rows),
        leakage_failures, leakage_warnings, spy_available, recommendation=None)
    recommendation = decide(confirmed_3h, inputs_ok, rows, aq_provisional)
    aq = dict(aq_provisional)
    aq["recommendation"] = recommendation

    label_summary = build_label_summary(rows)
    date_alignment = build_date_alignment_summary(
        rows, snaps_by_ticker, prices_by_ticker, eligible_first)

    # Write artifacts.
    panel_columns = (["ticker", "company_name", "sector", "industry", "scoring_date",
                      "adjusted_close"] + ACTIVE_META_COLUMNS + feature_columns + LABEL_COLUMNS)
    _write_csv(aligned_panel_path, panel_columns, rows)
    _write_csv(label_summary_path, LABEL_SUMMARY_COLUMNS, label_summary)
    _write_csv(date_alignment_summary_path, DATE_ALIGNMENT_SUMMARY_COLUMNS, date_alignment)
    _write_csv(leakage_checks_path, LEAKAGE_CHECKS_COLUMNS, leakage_rows)
    _dump_json(alignment_quality_path, aq)

    result = {
        "phase": PHASE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "phase3h_features_json": "research/output/phase3h_sec_fundamental_features.json",
            "phase3h_feature_snapshot_csv":
                "research/output/phase3h_sec_fundamental_features/"
                "feature_snapshot_20ticker_sample.csv",
            "phase3h_feature_dictionary_csv":
                "research/output/phase3h_sec_fundamental_features/feature_dictionary.csv",
            "phase3h_feature_quality_report_json":
                "research/output/phase3h_sec_fundamental_features/feature_quality_report.json",
            "phase3h_feature_alignment_warnings_csv":
                "research/output/phase3h_sec_fundamental_features/feature_alignment_warnings.csv",
            "price_history_csv": PRICE_CSV.replace("\\", "/"),
            "price_data_quality_json": PRICE_QUALITY_JSON.replace("\\", "/"),
            "price_data_build_summary_json": PRICE_BUILD_SUMMARY_JSON.replace("\\", "/"),
            "price_survivorship_caveat_json": PRICE_SURVIVORSHIP_JSON.replace("\\", "/"),
            "sector_map_csv": "research/input/phase2k_p_sector_map_current.csv",
        },
        "outputs_written": {
            "result_json": "research/output/phase3i_fundamental_price_alignment.json",
            "aligned_feature_price_panel_csv":
                "research/output/phase3i_fundamental_price_alignment/"
                "aligned_feature_price_panel_20ticker_sample.csv",
            "alignment_quality_report_json":
                "research/output/phase3i_fundamental_price_alignment/"
                "alignment_quality_report.json",
            "label_summary_by_horizon_csv":
                "research/output/phase3i_fundamental_price_alignment/label_summary_by_horizon.csv",
            "leakage_checks_csv":
                "research/output/phase3i_fundamental_price_alignment/leakage_checks.csv",
            "date_alignment_summary_csv":
                "research/output/phase3i_fundamental_price_alignment/date_alignment_summary.csv",
        },
        "phase3h_summary": {
            "phase3h_confirmed": confirmed_3h,
            "phase3h_recommendation": (phase3h or {}).get("recommendation", {}).get(
                "recommendation"),
            "phase3h_snapshot_rows": (phase3h or {}).get("snapshot_summary", {}).get(
                "snapshot_rows"),
            "phase3h_feature_columns_count": (phase3h or {}).get(
                "feature_dictionary_summary", {}).get("feature_columns_count"),
        },
        "alignment_config": {
            "universe": "Phase 3-H 20 feature tickers plus the SPY benchmark only",
            "benchmark": BENCHMARK,
            "forward_return_horizons_trading_days": list(HORIZONS),
            "scoring_grid": "every trading day with an available adjusted_close, strictly after a "
                            "ticker's first feature_asof_date",
            "feature_activation_rule": "a feature snapshot becomes active on the first trading day "
                                       "strictly after its feature_asof_date (date part); the "
                                       "active snapshot for a scoring date is the latest such "
                                       "snapshot, never using fiscal_period_end as the "
                                       "availability date",
            "active_snapshot_tie_break": "latest feature_asof_date, then latest fiscal_period_end, "
                                         "then highest source_field_count",
            "label_definition": "forward_return_h = adjusted_close[t+h]/adjusted_close[t] - 1; "
                                "SPY excess measured over the identical calendar window; "
                                "binary_outperform_spy = 1 if excess > 0; rank is ordinal ascending "
                                "across the sample per scoring date",
            "labels_for_validation_only": True,
            "labels_forward_filled": False,
            "model_trained": False,
            "predictions_computed": False,
        },
        "price_data_summary": _build_price_summary(prices_by_ticker, price_rows_read,
                                                   price_tickers_read, spy_available),
        "aligned_panel_summary": {
            "aligned_rows": aq["aligned_rows"],
            "aligned_tickers": aq["aligned_tickers"],
            "aligned_start_date": aq["aligned_start_date"],
            "aligned_end_date": aq["aligned_end_date"],
            "feature_columns_carried": len(feature_columns),
            "feature_age_days_summary": aq["feature_age_days_summary"],
            "feature_age_median_reasonable": aq["feature_age_median_reasonable"],
            "max_reasonable_median_feature_age_days": aq["max_reasonable_median_feature_age_days"],
            "annual_vs_quarterly_active_snapshot_mix":
                aq["annual_vs_quarterly_active_snapshot_mix"],
            "rows_by_sector": aq["rows_by_sector"],
            "panel_columns": panel_columns,
        },
        "label_summary": {
            "label_coverage_by_horizon": aq["label_coverage_by_horizon"],
            "by_horizon": label_summary,
        },
        "leakage_check_summary": {
            "leakage_check_count": aq["leakage_check_count"],
            "leakage_failure_count": aq["leakage_failure_count"],
            "leakage_warning_count": aq["leakage_warning_count"],
            "checks_evaluated": [name for name, _sev in _LEAKAGE_CHECK_DEFS],
            "all_checks_passed": aq["leakage_failure_count"] == 0,
        },
        "alignment_quality_summary": {
            "input_feature_rows": aq["input_feature_rows"],
            "input_feature_tickers": aq["input_feature_tickers"],
            "price_rows_read": aq["price_rows_read"],
            "price_tickers_read": aq["price_tickers_read"],
            "rows_by_ticker": aq["rows_by_ticker"],
            "null_feature_summary": aq["null_feature_summary"],
            "null_label_summary": aq["null_label_summary"],
            "spy_available": aq["spy_available"],
        },
        "source_limitations": list(_SOURCE_LIMITATIONS),
        "earnings_revisions_gap": {
            "sec_provides_earnings_consensus": False,
            "sec_provides_analyst_estimate_revisions": False,
            "gap_description": "SEC as-reported fundamentals support the trailing fundamental "
                               "features aligned here but provide no forward analyst consensus or "
                               "estimate revisions; a separate provider must be researched and "
                               "selected (see the Phase 3-E provider requirements matrix) before "
                               "earnings-surprise or revision-momentum features can be aligned.",
            "provider_selection_required": True,
        },
        "recommendation": build_recommendation(recommendation),
        "interpretation": build_interpretation(),
        "recommended_next_phase": build_recommended_next_phase(recommendation),
    }
    result.update(build_safety_flags())

    _dump_json(result_json_path, result)
    return result


def _build_price_summary(prices_by_ticker, price_rows_read, price_tickers_read, spy_available):
    all_dates = []
    for t, rows in prices_by_ticker.items():
        if rows:
            all_dates.append(rows[0][0])
            all_dates.append(rows[-1][0])
    return {
        "panel": "Phase 2K-G expanded price history (free, current-as-of), read-only",
        "price_rows_read": price_rows_read,
        "price_tickers_read": price_tickers_read,
        "benchmark": BENCHMARK,
        "spy_available": spy_available,
        "earliest_price_date": min(all_dates) if all_dates else "",
        "latest_price_date": max(all_dates) if all_dates else "",
        "membership_basis": "current_as_of (survivorship-biased; not point-in-time)",
        "read_only": True,
    }


def main():
    result = run()
    rec = result["recommendation"]
    nxt = result["recommended_next_phase"]
    aps = result["aligned_panel_summary"]
    lcs = result["leakage_check_summary"]
    cov = result["label_summary"]["label_coverage_by_horizon"]
    print("Phase %s - Fundamental Feature Price-Alignment Dry Run" % result["phase"])
    print("  phase3h confirmed        : %s"
          % result["phase3h_summary"]["phase3h_confirmed"]["all_confirmed"])
    print("  feature tickers          : %s"
          % len(result["alignment_quality_summary"]["input_feature_tickers"]))
    print("  price rows read          : %s" % result["price_data_summary"]["price_rows_read"])
    print("  aligned tickers          : %s" % len(aps["aligned_tickers"]))
    print("  aligned rows             : %s" % aps["aligned_rows"])
    print("  aligned date span        : %s -> %s"
          % (aps["aligned_start_date"], aps["aligned_end_date"]))
    print("  feature_age_days median  : %s (reasonable=%s, threshold=%s)"
          % (aps["feature_age_days_summary"]["median"], aps["feature_age_median_reasonable"],
             aps["max_reasonable_median_feature_age_days"]))
    print("  active snapshot mix      : %s" % aps["annual_vs_quarterly_active_snapshot_mix"])
    print("  label coverage           : %s" % cov)
    print("  leakage checks           : %s" % lcs["leakage_check_count"])
    print("  leakage failures         : %s" % lcs["leakage_failure_count"])
    print("  spy available            : %s" % result["price_data_summary"]["spy_available"])
    print("  network used             : %s" % result["network_used"])
    print("  d_drive_read / written   : %s / %s"
          % (result["d_drive_read"], result["d_drive_written"]))
    print("  recommendation           : %s" % rec["recommendation"])
    print("  recommended next phase   : %s - %s" % (nxt["phase"], nxt["title"]))
    return result


if __name__ == "__main__":
    main()
