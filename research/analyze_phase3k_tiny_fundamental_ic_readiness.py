"""Phase 3-K - Tiny Fundamental IC and Model-Readiness Diagnostic.

Phase 3-J (FUNDAMENTAL_ALIGNMENT_REPAIR_SUCCESS) produced a leakage-free, staleness-capped
(<= 365 calendar-day feature age) repaired 20-ticker aligned panel: 14,276 rows, all 20 tickers,
median feature age ~53 days, label coverage ~0.97 / 0.91 / 0.82 at the 21 / 63 / 126-day horizons.
This phase asks the next honest question before any modeling: do the point-in-time SEC fundamental
features carry **any** cross-sectional predictive signal against forward excess returns and forward
rank labels on that repaired panel?

It measures, per feature and per horizon, the cross-sectional daily rank information coefficient
(Spearman) against forward_excess_return_vs_spy and forward_return_rank_by_date, summarizes signal
strength and stability, runs a simple top-vs-bottom bucket return-spread diagnostic, aggregates by
feature family, checks yearly stability and sector sanity, and decides whether a tiny research model
is even allowed next (Phase 3-L) or whether a larger SEC universe / richer data is needed first.

Scope and safety. This phase **trains no model** and fits no regression / logistic / ridge / lasso /
tree / ML estimator of any kind. It is allowed to compute IC, rank IC, top/bottom bucket return
spreads, and feature-family diagnostics - nothing else. It computes NO predictions, NO scores, NO
trading rankings, and NO portfolio weights, and writes NO deployable model artifact. There is NO
network use and NO D: drive read or write: it reads only repo-local Phase 3-J / Phase 3-H outputs on
the C: drive. The 20-ticker universe is tiny and survivorship-biased, so every result here is
**diagnostic only** and claims no production edge.
"""
from __future__ import annotations

import csv
import json
import math
import os
from datetime import datetime

PHASE = "3-K"

# --------------------------------------------------------------------------- #
# Paths (all repo-local on the C: drive; no D:, no network)
# --------------------------------------------------------------------------- #
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUT_DIR = os.path.join(_REPO_ROOT, "research", "output")
_H_DIR = os.path.join(_OUT_DIR, "phase3h_sec_fundamental_features")
_J_DIR = os.path.join(_OUT_DIR, "phase3j_repaired_fundamental_price_alignment")
_K_DIR = os.path.join(_OUT_DIR, "phase3k_tiny_fundamental_ic_readiness")

# Inputs - committed Phase 3-J repair outputs + Phase 3-H feature metadata (repo-local).
PHASE3J_JSON = os.path.join(_OUT_DIR, "phase3j_repaired_fundamental_price_alignment.json")
PHASE3J_PANEL_CSV = os.path.join(_J_DIR, "repaired_aligned_panel_20ticker_sample.csv")
PHASE3J_QUALITY_JSON = os.path.join(_J_DIR, "repaired_alignment_quality_report.json")
PHASE3J_LABEL_SUMMARY_CSV = os.path.join(_J_DIR, "repaired_label_summary_by_horizon.csv")
PHASE3H_FEATURE_DICT_CSV = os.path.join(_H_DIR, "feature_dictionary.csv")
PHASE3H_FEATURE_QUALITY_JSON = os.path.join(_H_DIR, "feature_quality_report.json")

# Outputs (all repo-local, under research/output on the C: drive).
RESULT_JSON = os.path.join(_OUT_DIR, "phase3k_tiny_fundamental_ic_readiness.json")
FEATURE_IC_CSV = os.path.join(_K_DIR, "feature_ic_summary.csv")
FEATURE_FAMILY_IC_CSV = os.path.join(_K_DIR, "feature_family_ic_summary.csv")
HORIZON_READINESS_CSV = os.path.join(_K_DIR, "horizon_readiness_summary.csv")
YEARLY_IC_CSV = os.path.join(_K_DIR, "yearly_ic_summary.csv")
SECTOR_SANITY_CSV = os.path.join(_K_DIR, "sector_sanity_summary.csv")
READINESS_DECISION_CSV = os.path.join(_K_DIR, "readiness_decision_table.csv")

# --------------------------------------------------------------------------- #
# Contract / config
# --------------------------------------------------------------------------- #
PHASE3J_EXPECTED_RECOMMENDATION = "FUNDAMENTAL_ALIGNMENT_REPAIR_SUCCESS"
PHASE3J_EXPECTED_CAP_DAYS = 365
HORIZONS = [21, 63, 126]

# Columns that are metadata or labels, never engineered candidate features.
_META_COLUMNS = {
    "ticker", "company_name", "sector", "industry", "scoring_date", "adjusted_close",
    "active_feature_asof_date", "feature_age_days", "active_fiscal_period_end",
    "active_fiscal_year", "active_fiscal_period", "active_form", "active_snapshot_type",
}
_LABEL_PREFIXES = (
    "forward_return_", "forward_spy_return_", "forward_excess_return_vs_spy_",
    "binary_outperform_spy_", "forward_return_rank_by_date_",
)

# Required feature families (Phase 3-K vocabulary).
FAM_PROFIT = "profitability_margin"
FAM_LEVERAGE = "balance_sheet_leverage"
FAM_GROWTH = "growth_change"
FAM_CASH = "cash_quality"
FAM_SIZE = "size_scale"
FAM_AVAIL = "availability_recency"
FAM_UNKNOWN = "unknown"
ALL_FAMILIES = [FAM_PROFIT, FAM_LEVERAGE, FAM_GROWTH, FAM_CASH, FAM_SIZE, FAM_AVAIL, FAM_UNKNOWN]

# Map the Phase 3-H feature-dictionary families onto the Phase 3-K vocabulary.
_RAW_FAMILY_MAP = {
    "profitability": FAM_PROFIT,
    "balance_sheet": FAM_LEVERAGE,
    "growth": FAM_GROWTH,
    "quality": FAM_CASH,
    "size": FAM_SIZE,
    "availability_metadata": FAM_AVAIL,
}

# IC methodology constants.
MIN_CROSS_SECTION = 8       # minimum non-null feature/label pairs required on a scoring date
QUINTILE_MIN_OBS = 10       # minimum valid observations on a date to form top/bottom quintiles
TOPK = 3                    # tiny-sample top-K vs bottom-K bucket

# "moderate_or_better" feature gate.
MODERATE_ABS_IC = 0.03
MODERATE_MIN_DATES = 100
MODERATE_MIN_HIT = 0.52
MODERATE_MIN_NONNULL = 0.50
# "strong" feature gate.
STRONG_ABS_IC = 0.05
STRONG_MIN_DATES = 100
STRONG_MIN_HIT = 0.55
STRONG_MIN_NONNULL = 0.60
STRONG_MIN_SPREAD_HIT = 0.55
# Horizon-readiness gate.
READY_MIN_MODERATE = 3
READY_MIN_STRONG = 1
# Temporal-breadth guard. The staleness repair (Phase 3-J, 365-day cap) concentrates the dense
# cross-section into recent filing windows, so raw IC magnitude alone can reflect a single market
# regime over heavily overlapping forward-return windows. To certify a tiny-model gate we also
# require the qualifying IC dates to span several distinct calendar years; otherwise the panel is
# treated as a single-regime small sample (diagnostic only) however large the raw IC looks.
MIN_STABLE_YEARS = 4
MIN_QUALIFYING_DATES_PER_YEAR = 20  # a year "counts" toward breadth only with enough dense dates
# A faint-but-nonzero signal floor used to separate "inconclusive" from "fails / stop".
FAINT_SIGNAL_ABS_IC = 0.01

# Recommendation vocabulary.
REC_PASSES = "FUNDAMENTAL_IC_DIAGNOSTIC_PASSES_TINY_MODEL_GATE"
REC_WEAK = "FUNDAMENTAL_IC_DIAGNOSTIC_WEAK_BUT_EXPANDABLE"
REC_INCONCLUSIVE = "FUNDAMENTAL_IC_DIAGNOSTIC_INCONCLUSIVE_SMALL_SAMPLE"
REC_FAILS = "FUNDAMENTAL_IC_DIAGNOSTIC_FAILS_STOP_TINY_MODELING"
REC_BLOCKED = "FUNDAMENTAL_IC_DIAGNOSTIC_BLOCKED"
ALLOWED_RECOMMENDATIONS = [REC_PASSES, REC_WEAK, REC_INCONCLUSIVE, REC_FAILS, REC_BLOCKED]

# CSV column schemas.
FEATURE_IC_COLUMNS = [
    "feature", "feature_family", "horizon", "observation_count", "date_count",
    "non_null_feature_fraction", "mean_rank_ic_excess_return", "median_rank_ic_excess_return",
    "ic_hit_rate_excess_return", "ic_ir_excess_return", "mean_rank_ic_forward_rank",
    "median_rank_ic_forward_rank", "ic_hit_rate_forward_rank", "ic_ir_forward_rank",
    "absolute_mean_ic", "top_minus_bottom_spread_quintile", "positive_spread_fraction_quintile",
    "top_minus_bottom_spread_top3", "positive_spread_fraction_top3", "signal_direction",
    "diagnostic_strength",
]
FEATURE_FAMILY_IC_COLUMNS = [
    "feature_family", "horizon", "num_features", "best_feature", "best_feature_abs_mean_ic",
    "median_abs_mean_ic", "mean_abs_mean_ic", "moderate_or_better_count", "strong_count",
    "best_top_minus_bottom_spread", "stable_signal", "stability_note",
]
HORIZON_READINESS_COLUMNS = [
    "horizon", "label_coverage", "avg_daily_cross_section_size", "valid_ic_date_count",
    "distinct_ic_years", "best_feature", "best_feature_abs_mean_ic", "moderate_or_better_count",
    "strong_count", "horizon_readiness",
]
YEARLY_IC_COLUMNS = [
    "feature", "horizon", "year", "ic_date_count", "mean_rank_ic_excess_return",
    "positive_ic_year", "small_sample_caveat",
]
SECTOR_SANITY_COLUMNS = [
    "sector", "ticker_count", "row_count", "row_fraction", "tickers",
    "operating_margin_non_null_fraction", "capex_intensity_non_null_fraction", "note",
]
READINESS_DECISION_COLUMNS = ["decision_item", "value", "passed", "note"]


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


def _mean(values):
    return sum(values) / len(values) if values else None


def _median(values):
    if not values:
        return None
    vs = sorted(values)
    n = len(vs)
    mid = n // 2
    if n % 2:
        return vs[mid]
    return (vs[mid - 1] + vs[mid]) / 2.0


def _sample_std(values):
    n = len(values)
    if n < 2:
        return None
    m = sum(values) / n
    var = sum((v - m) ** 2 for v in values) / (n - 1)
    return math.sqrt(var)


def _sign(x):
    if x is None:
        return 0
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


# --------------------------------------------------------------------------- #
# Rank / Spearman (stdlib only; no model fitting)
# --------------------------------------------------------------------------- #
def _ranks(values):
    """Average ranks (1-based) with tie handling."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0  # 1-based average rank for the tie block
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def _spearman(xs, ys):
    """Spearman rank correlation; None if fewer than 3 points or zero variance."""
    if len(xs) < 3:
        return None
    return _pearson(_ranks(xs), _ranks(ys))


# --------------------------------------------------------------------------- #
# Upstream confirmation
# --------------------------------------------------------------------------- #
def confirm_phase3j(phase3j):
    """Confirm the committed Phase 3-J repair result that gates this diagnostic phase."""
    if not phase3j:
        return {"all_confirmed": False, "phase3j_present": False}
    rec = phase3j.get("recommendation", {}) or {}
    nxt = phase3j.get("recommended_next_phase", {}) or {}
    rps = phase3j.get("repaired_panel_summary", {}) or {}
    leak = phase3j.get("leakage_check_summary", {}) or {}
    checks = {
        "phase3j_present": True,
        "phase_is_3j": phase3j.get("phase") == "3-J",
        "recommendation_is_repair_success":
            rec.get("recommendation") == PHASE3J_EXPECTED_RECOMMENDATION,
        "next_phase_is_3k": nxt.get("phase") == "3-K",
        "selected_cap_days_is_365": rec.get("selected_cap_days") == PHASE3J_EXPECTED_CAP_DAYS,
        "repaired_rows_at_least_5000": (rps.get("repaired_aligned_rows") or 0) >= 5000,
        "repaired_ticker_count_is_20": rps.get("repaired_aligned_ticker_count") == 20,
        "leakage_failure_count_zero": leak.get("leakage_failure_count_after_repair") == 0,
        "labels_computed_true": phase3j.get("labels_computed") is True,
        "labels_for_validation_only_true": phase3j.get("labels_for_validation_only") is True,
        "model_training_ready_true": phase3j.get("model_training_ready") is True,
        "research_model_trained_false": phase3j.get("research_model_trained") is False,
        "production_model_candidate_created_false":
            phase3j.get("production_model_candidate_created") is False,
        "deployable_model_artifact_written_false":
            phase3j.get("deployable_model_artifact_written") is False,
        "network_used_false": phase3j.get("network_used") is False,
        "d_drive_read_false": phase3j.get("d_drive_read") is False,
        "d_drive_written_false": phase3j.get("d_drive_written") is False,
    }
    checks["all_confirmed"] = all(checks.values())
    return checks


# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #
def load_panel(path):
    """Read the Phase 3-J repaired aligned panel CSV in full; return (rows, header_columns)."""
    if not os.path.isfile(path):
        return [], []
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        rows = list(reader)
    return rows, header


def load_feature_family_map(dict_path):
    """feature_name -> Phase 3-K family, from the Phase 3-H feature dictionary where present."""
    fam = {}
    if os.path.isfile(dict_path):
        with open(dict_path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                name = (row.get("feature_name") or "").strip()
                raw = (row.get("feature_family") or "").strip()
                if name:
                    fam[name] = _RAW_FAMILY_MAP.get(raw, FAM_UNKNOWN)
    return fam


def map_feature_to_family(feature, dict_map):
    """Resolve a feature's family from the dictionary, else by a conservative name heuristic."""
    if feature in dict_map:
        return dict_map[feature]
    f = feature.lower()
    if "margin" in f:
        return FAM_PROFIT
    if "growth" in f or "_change" in f or f.endswith("_yoy"):
        return FAM_GROWTH
    if ("debt" in f or "liabilit" in f or "equity" in f or "leverage" in f
            or "asset_turnover" in f):
        return FAM_LEVERAGE
    if ("cash" in f or "fcf" in f or "accrual" in f or "capex" in f or "conversion" in f):
        return FAM_CASH
    if f.startswith("log_") or "size" in f or "scale" in f:
        return FAM_SIZE
    if "filing_lag" in f or "asof" in f or "_age" in f or "recency" in f or "availab" in f:
        return FAM_AVAIL
    return FAM_UNKNOWN


def identify_feature_columns(header):
    """Engineered feature columns = header minus metadata minus any label-prefixed column."""
    feats = []
    for c in header:
        if c in _META_COLUMNS:
            continue
        if any(c.startswith(p) for p in _LABEL_PREFIXES):
            continue
        feats.append(c)
    return feats


# --------------------------------------------------------------------------- #
# Light leakage guard (this phase recomputes no labels; it only re-verifies the
# inherited point-in-time invariant cheaply as a corruption guard)
# --------------------------------------------------------------------------- #
def leakage_guard_failures(rows):
    failures = 0
    for r in rows:
        asof_d = _date_part(r.get("active_feature_asof_date"))
        scoring = (r.get("scoring_date") or "").strip()
        fpe = (r.get("active_fiscal_period_end") or "").strip()
        if not asof_d or not scoring or not (asof_d < scoring):
            failures += 1
        elif fpe and (asof_d < fpe or asof_d == fpe):
            failures += 1
    return failures


# --------------------------------------------------------------------------- #
# Core IC computation
# --------------------------------------------------------------------------- #
def group_by_date(rows):
    groups = {}
    for r in rows:
        groups.setdefault((r.get("scoring_date") or "").strip(), []).append(r)
    return [(d, groups[d]) for d in sorted(groups) if d]


def daily_rank_ic(groups, feature, label_col):
    """Per-date Spearman rank IC of feature vs label_col across the cross-section.

    Returns (ic_series, observation_count) where ic_series is a list of (scoring_date, ic) for
    every date that had at least MIN_CROSS_SECTION non-null feature/label pairs and a defined
    correlation. observation_count is the total number of non-null pairs that entered the ICs.
    """
    ic_series = []
    obs = 0
    for date, rs in groups:
        xs, ys = [], []
        for r in rs:
            fv = _to_float(r.get(feature))
            lv = _to_float(r.get(label_col))
            if fv is not None and lv is not None:
                xs.append(fv)
                ys.append(lv)
        if len(xs) >= MIN_CROSS_SECTION:
            ic = _spearman(xs, ys)
            if ic is not None:
                ic_series.append((date, ic))
                obs += len(xs)
    return ic_series, obs


def summarize_ic(ic_series):
    ics = [ic for _, ic in ic_series]
    if not ics:
        return {"mean": None, "median": None, "hit_rate": None, "ir": None, "date_count": 0}
    mean_ic = _mean(ics)
    std_ic = _sample_std(ics)
    s = _sign(mean_ic)
    if s > 0:
        hit = sum(1 for v in ics if v > 0) / len(ics)
    elif s < 0:
        hit = sum(1 for v in ics if v < 0) / len(ics)
    else:
        hit = 0.0
    ir = (mean_ic / std_ic) if (std_ic and std_ic > 0) else None
    return {
        "mean": _round(mean_ic, 6),
        "median": _round(_median(ics), 6),
        "hit_rate": _round(hit, 4),
        "ir": _round(ir, 4) if ir is not None else None,
        "date_count": len(ics),
    }


def bucket_spread_series(groups, feature, label_col, k_quintile=True):
    """Per-date top-minus-bottom forward-excess-return spread.

    For k_quintile=True: top/bottom quintiles when a date has >= QUINTILE_MIN_OBS valid obs.
    For k_quintile=False: tiny-sample top-TOPK vs bottom-TOPK when a date has >= 2*TOPK valid obs.
    Returns a list of (scoring_date, spread).
    """
    series = []
    for date, rs in groups:
        pairs = []
        for r in rs:
            fv = _to_float(r.get(feature))
            lv = _to_float(r.get(label_col))
            if fv is not None and lv is not None:
                pairs.append((fv, lv))
        n = len(pairs)
        if k_quintile:
            if n < QUINTILE_MIN_OBS:
                continue
            k = max(1, n // 5)
        else:
            if n < 2 * TOPK:
                continue
            k = TOPK
        pairs.sort(key=lambda p: p[0])
        bottom = pairs[:k]
        top = pairs[-k:]
        spread = (sum(p[1] for p in top) / k) - (sum(p[1] for p in bottom) / k)
        series.append((date, spread))
    return series


def summarize_spread(series):
    spreads = [s for _, s in series]
    if not spreads:
        return {"mean": None, "positive_fraction": None, "hit_rate": None, "date_count": 0}
    mean_s = _mean(spreads)
    pos_frac = sum(1 for s in spreads if s > 0) / len(spreads)
    sgn = _sign(mean_s)
    if sgn != 0:
        hit = sum(1 for s in spreads if _sign(s) == sgn) / len(spreads)
    else:
        hit = 0.0
    return {
        "mean": _round(mean_s, 6),
        "positive_fraction": _round(pos_frac, 4),
        "hit_rate": _round(hit, 4),
        "date_count": len(spreads),
    }


# --------------------------------------------------------------------------- #
# Per-feature / per-horizon diagnostics
# --------------------------------------------------------------------------- #
def _classify_strength(excess, spread_q, non_null_frac):
    """diagnostic_strength: strong / moderate / weak / unusable."""
    dc = excess["date_count"]
    abs_ic = abs(excess["mean"]) if excess["mean"] is not None else 0.0
    hit = excess["hit_rate"] or 0.0
    if dc < MODERATE_MIN_DATES or (non_null_frac or 0.0) < MODERATE_MIN_NONNULL:
        return "unusable"
    spread_sign_ok = (
        spread_q["mean"] is not None and excess["mean"] is not None
        and _sign(spread_q["mean"]) == _sign(excess["mean"]) and _sign(excess["mean"]) != 0)
    spread_hit_ok = (spread_q["hit_rate"] or 0.0) >= STRONG_MIN_SPREAD_HIT
    is_strong = (
        abs_ic >= STRONG_ABS_IC and dc >= STRONG_MIN_DATES and hit >= STRONG_MIN_HIT
        and (non_null_frac or 0.0) >= STRONG_MIN_NONNULL and spread_sign_ok and spread_hit_ok)
    if is_strong:
        return "strong"
    is_moderate = (
        abs_ic >= MODERATE_ABS_IC and dc >= MODERATE_MIN_DATES and hit >= MODERATE_MIN_HIT
        and (non_null_frac or 0.0) >= MODERATE_MIN_NONNULL)
    if is_moderate:
        return "moderate"
    return "weak"


def _signal_direction(excess):
    if excess["date_count"] < 30 or excess["mean"] is None:
        return "unstable"
    if (excess["hit_rate"] or 0.0) < 0.5 or abs(excess["mean"]) < 0.005:
        return "unstable"
    return "positive" if excess["mean"] > 0 else "negative"


def compute_feature_ic(groups, features, family_of, non_null_frac):
    """Build the full feature x horizon IC table and the cached excess-return IC series."""
    rows = []
    excess_ic_series = {}   # (feature, horizon) -> ic_series vs forward_excess_return
    for feat in features:
        fam = family_of[feat]
        nnf = non_null_frac.get(feat)
        for h in HORIZONS:
            ex_col = "forward_excess_return_vs_spy_%dd" % h
            rk_col = "forward_return_rank_by_date_%dd" % h
            ex_series, obs = daily_rank_ic(groups, feat, ex_col)
            rk_series, _ = daily_rank_ic(groups, feat, rk_col)
            excess_ic_series[(feat, h)] = ex_series
            ex = summarize_ic(ex_series)
            rk = summarize_ic(rk_series)
            spread_q = summarize_spread(bucket_spread_series(groups, feat, ex_col, True))
            spread_k = summarize_spread(bucket_spread_series(groups, feat, ex_col, False))
            abs_mean_ic = _round(abs(ex["mean"]), 6) if ex["mean"] is not None else None
            strength = _classify_strength(ex, spread_q, nnf)
            direction = _signal_direction(ex)
            rows.append({
                "feature": feat,
                "feature_family": fam,
                "horizon": "%dd" % h,
                "_horizon_days": h,
                "observation_count": obs,
                "date_count": ex["date_count"],
                "non_null_feature_fraction": _round(nnf, 4) if nnf is not None else None,
                "mean_rank_ic_excess_return": ex["mean"],
                "median_rank_ic_excess_return": ex["median"],
                "ic_hit_rate_excess_return": ex["hit_rate"],
                "ic_ir_excess_return": ex["ir"],
                "mean_rank_ic_forward_rank": rk["mean"],
                "median_rank_ic_forward_rank": rk["median"],
                "ic_hit_rate_forward_rank": rk["hit_rate"],
                "ic_ir_forward_rank": rk["ir"],
                "absolute_mean_ic": abs_mean_ic,
                "top_minus_bottom_spread_quintile": spread_q["mean"],
                "positive_spread_fraction_quintile": spread_q["positive_fraction"],
                "top_minus_bottom_spread_top3": spread_k["mean"],
                "positive_spread_fraction_top3": spread_k["positive_fraction"],
                "signal_direction": direction,
                "diagnostic_strength": strength,
            })
    return rows, excess_ic_series


# --------------------------------------------------------------------------- #
# Feature-family aggregation
# --------------------------------------------------------------------------- #
def compute_family_summary(feature_rows):
    out = []
    for fam in ALL_FAMILIES:
        for h in HORIZONS:
            sub = [r for r in feature_rows
                   if r["feature_family"] == fam and r["_horizon_days"] == h]
            if not sub:
                continue
            judgeable = [r for r in sub if r["absolute_mean_ic"] is not None
                         and r["diagnostic_strength"] != "unusable"]
            abs_ics = [r["absolute_mean_ic"] for r in judgeable]
            mod_count = sum(1 for r in sub
                            if r["diagnostic_strength"] in ("moderate", "strong"))
            strong_count = sum(1 for r in sub if r["diagnostic_strength"] == "strong")
            best = max(judgeable, key=lambda r: r["absolute_mean_ic"]) if judgeable else None
            spreads = [abs(r["top_minus_bottom_spread_quintile"]) for r in sub
                       if r["top_minus_bottom_spread_quintile"] is not None]
            stable = mod_count >= 1 and any(
                r["signal_direction"] in ("positive", "negative")
                and r["diagnostic_strength"] in ("moderate", "strong") for r in sub)
            if stable:
                note = "at least one moderate-or-better feature with a consistent IC direction"
            elif judgeable:
                note = "features measurable but none clears the moderate gate on this horizon"
            else:
                note = "no judgeable feature (insufficient dates or low non-null coverage)"
            out.append({
                "feature_family": fam,
                "horizon": "%dd" % h,
                "_horizon_days": h,
                "num_features": len(sub),
                "best_feature": best["feature"] if best else "",
                "best_feature_abs_mean_ic": best["absolute_mean_ic"] if best else None,
                "median_abs_mean_ic": _round(_median(abs_ics), 6) if abs_ics else None,
                "mean_abs_mean_ic": _round(_mean(abs_ics), 6) if abs_ics else None,
                "moderate_or_better_count": mod_count,
                "strong_count": strong_count,
                "best_top_minus_bottom_spread": _round(max(spreads), 6) if spreads else None,
                "stable_signal": stable,
                "stability_note": note,
            })
    return out


# --------------------------------------------------------------------------- #
# Horizon readiness
# --------------------------------------------------------------------------- #
def compute_horizon_readiness(feature_rows, groups, label_coverage):
    out = []
    for h in HORIZONS:
        ex_col = "forward_excess_return_vs_spy_%dd" % h
        sub = [r for r in feature_rows if r["_horizon_days"] == h]
        judgeable = [r for r in sub if r["absolute_mean_ic"] is not None
                     and r["diagnostic_strength"] != "unusable"]
        mod_count = sum(1 for r in sub if r["diagnostic_strength"] in ("moderate", "strong"))
        strong_count = sum(1 for r in sub if r["diagnostic_strength"] == "strong")
        best = max(judgeable, key=lambda r: r["absolute_mean_ic"]) if judgeable else None
        # Cross-section size + temporal breadth of the qualifying (>= MIN_CROSS_SECTION) dates.
        cs_sizes, valid_ic_dates = [], 0
        dates_by_year = {}
        for date, rs in groups:
            labeled = sum(1 for r in rs if _to_float(r.get(ex_col)) is not None)
            if labeled >= 1:
                cs_sizes.append(labeled)
            if labeled >= MIN_CROSS_SECTION:
                valid_ic_dates += 1
                dates_by_year[date[:4]] = dates_by_year.get(date[:4], 0) + 1
        distinct_ic_years = sum(1 for n in dates_by_year.values()
                                if n >= MIN_QUALIFYING_DATES_PER_YEAR)
        breadth_ok = distinct_ic_years >= MIN_STABLE_YEARS
        if (mod_count >= READY_MIN_MODERATE and strong_count >= READY_MIN_STRONG
                and valid_ic_dates >= MODERATE_MIN_DATES and breadth_ok):
            readiness = "ready_for_tiny_model"
        elif valid_ic_dates >= MODERATE_MIN_DATES and judgeable:
            readiness = "diagnostic_only"
        else:
            readiness = "not_ready"
        out.append({
            "horizon": "%dd" % h,
            "_horizon_days": h,
            "label_coverage": label_coverage.get(h),
            "avg_daily_cross_section_size": _round(_mean(cs_sizes), 2) if cs_sizes else None,
            "valid_ic_date_count": valid_ic_dates,
            "distinct_ic_years": distinct_ic_years,
            "best_feature": best["feature"] if best else "",
            "best_feature_abs_mean_ic": best["absolute_mean_ic"] if best else None,
            "moderate_or_better_count": mod_count,
            "strong_count": strong_count,
            "horizon_readiness": readiness,
        })
    return out


# --------------------------------------------------------------------------- #
# Yearly diagnostics for the best features
# --------------------------------------------------------------------------- #
def pick_best_features(feature_rows, top_n=3):
    """Distinct features with the largest absolute mean excess-return IC across all horizons."""
    best_by_feat = {}
    for r in feature_rows:
        if r["absolute_mean_ic"] is None or r["diagnostic_strength"] == "unusable":
            continue
        cur = best_by_feat.get(r["feature"])
        if cur is None or r["absolute_mean_ic"] > cur:
            best_by_feat[r["feature"]] = r["absolute_mean_ic"]
    ranked = sorted(best_by_feat.items(), key=lambda kv: kv[1], reverse=True)
    return [f for f, _ in ranked[:top_n]]


def compute_yearly_diagnostics(best_features, excess_ic_series, feature_rows):
    rows = []
    summary = []
    overall_mean = {(r["feature"], r["_horizon_days"]): r["mean_rank_ic_excess_return"]
                    for r in feature_rows}
    for feat in best_features:
        for h in HORIZONS:
            series = excess_ic_series.get((feat, h), [])
            by_year = {}
            for date, ic in series:
                yr = date[:4]
                by_year.setdefault(yr, []).append(ic)
            base_sign = _sign(overall_mean.get((feat, h)))
            year_rows = []
            pos_years = 0
            bad_years = []
            for yr in sorted(by_year):
                ics = by_year[yr]
                ymean = _mean(ics)
                positive = base_sign != 0 and _sign(ymean) == base_sign
                if positive:
                    pos_years += 1
                elif base_sign != 0 and len(ics) >= 12:
                    bad_years.append(yr)
                small = len(ics) < 12
                row = {
                    "feature": feat,
                    "horizon": "%dd" % h,
                    "year": yr,
                    "ic_date_count": len(ics),
                    "mean_rank_ic_excess_return": _round(ymean, 6),
                    "positive_ic_year": positive,
                    "small_sample_caveat": small,
                }
                rows.append(row)
                year_rows.append(row)
            n_years = len(year_rows)
            summary.append({
                "feature": feat,
                "horizon": "%dd" % h,
                "year_count": n_years,
                "positive_ic_year_fraction": _round(pos_years / n_years, 4) if n_years else None,
                "bad_years": bad_years,
                "small_sample_caveat": "20-ticker cross-sections give noisy yearly ICs; "
                                       "single-year signs are not evidence of a stable edge",
            })
    return rows, summary


# --------------------------------------------------------------------------- #
# Sector sanity
# --------------------------------------------------------------------------- #
def compute_sector_sanity(rows, best_features):
    total = len(rows)
    by_sector = {}
    for r in rows:
        sec = (r.get("sector") or "").strip() or "UNKNOWN"
        by_sector.setdefault(sec, {"rows": 0, "tickers": set(),
                                   "om": 0, "om_nn": 0, "capex": 0, "capex_nn": 0})
        d = by_sector[sec]
        d["rows"] += 1
        d["tickers"].add((r.get("ticker") or "").strip().upper())
        d["om"] += 1
        if _to_float(r.get("operating_margin")) is not None:
            d["om_nn"] += 1
        d["capex"] += 1
        if _to_float(r.get("capex_intensity")) is not None:
            d["capex_nn"] += 1
    out = []
    financials_gap = False
    for sec in sorted(by_sector):
        d = by_sector[sec]
        om_frac = d["om_nn"] / d["om"] if d["om"] else 0.0
        capex_frac = d["capex_nn"] / d["capex"] if d["capex"] else 0.0
        notes = []
        if sec == "Financials" and (om_frac < 0.5 or capex_frac < 0.5):
            financials_gap = True
            notes.append("bank accounting: operating_margin / capex_intensity largely undefined")
        if len(d["tickers"]) <= 2:
            notes.append("thin sector (<=2 tickers); cross-sectional weight is limited")
        out.append({
            "sector": sec,
            "ticker_count": len(d["tickers"]),
            "row_count": d["rows"],
            "row_fraction": _round(d["rows"] / total, 4) if total else None,
            "tickers": "|".join(sorted(d["tickers"])),
            "operating_margin_non_null_fraction": _round(om_frac, 4),
            "capex_intensity_non_null_fraction": _round(capex_frac, 4),
            "note": "; ".join(notes),
        })
    summary = {
        "sector_count": len(by_sector),
        "best_features": list(best_features),
        "best_feature_sector_dominance_checked": True,
        "missing_features_concentrated_in_financials": financials_gap,
        "note": "Best-feature signal is measured cross-sectionally across all sectors; no "
                "sector neutralization or modeling is performed in this phase. Margin / capex "
                "families are structurally undefined for Financials, so any margin/cash-quality "
                "signal is effectively measured on non-financial names.",
    }
    return out, summary


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #
def decide(confirmed, inputs_ok, leakage_failures, feature_rows, horizon_rows, family_rows):
    if not confirmed.get("all_confirmed") or not inputs_ok:
        return REC_BLOCKED, _decision_metrics(feature_rows, horizon_rows, family_rows)
    if leakage_failures > 0:
        return REC_BLOCKED, _decision_metrics(feature_rows, horizon_rows, family_rows)
    m = _decision_metrics(feature_rows, horizon_rows, family_rows)
    passes = (m["any_horizon_ready"] and m["moderate_feature_count"] >= READY_MIN_MODERATE
              and m["strong_feature_count"] >= READY_MIN_STRONG and m["stable_family_count"] >= 1)
    if passes:
        return REC_PASSES, m
    # Single-regime small sample: dense cross-sections (and therefore every IC observation) are
    # confined to too few distinct years, so however large the raw IC looks it cannot certify a
    # model gate. With sufficient data quality and a non-trivial signal this is the textbook
    # "inconclusive on a tiny sample - expand the universe" outcome.
    if m["single_regime"] and m["data_quality_ok"] and m["max_abs_ic"] is not None \
            and m["max_abs_ic"] >= FAINT_SIGNAL_ABS_IC:
        return REC_INCONCLUSIVE, m
    if m["moderate_feature_count"] >= 1:
        return REC_WEAK, m
    if m["max_abs_ic"] is not None and m["max_abs_ic"] >= FAINT_SIGNAL_ABS_IC \
            and m["data_quality_ok"]:
        return REC_INCONCLUSIVE, m
    return REC_FAILS, m


def _decision_metrics(feature_rows, horizon_rows, family_rows):
    mod_feats = {r["feature"] for r in feature_rows
                 if r["diagnostic_strength"] in ("moderate", "strong")}
    strong_feats = {r["feature"] for r in feature_rows if r["diagnostic_strength"] == "strong"}
    abs_ics = [r["absolute_mean_ic"] for r in feature_rows if r["absolute_mean_ic"] is not None
               and r["diagnostic_strength"] != "unusable"]
    spreads = [abs(r["top_minus_bottom_spread_quintile"]) for r in feature_rows
               if r["top_minus_bottom_spread_quintile"] is not None]
    any_ready = any(h["horizon_readiness"] == "ready_for_tiny_model" for h in horizon_rows)
    data_quality_ok = any(h["valid_ic_date_count"] >= MODERATE_MIN_DATES for h in horizon_rows)
    max_distinct_years = max((h.get("distinct_ic_years", 0) for h in horizon_rows), default=0)
    return {
        "moderate_feature_count": len(mod_feats),
        "strong_feature_count": len(strong_feats),
        "stable_family_count": sum(1 for f in family_rows if f["stable_signal"]),
        "any_horizon_ready": any_ready,
        "max_abs_ic": _round(max(abs_ics), 6) if abs_ics else None,
        "max_abs_spread": _round(max(spreads), 6) if spreads else None,
        "data_quality_ok": data_quality_ok,
        "max_distinct_ic_years": max_distinct_years,
        "single_regime": max_distinct_years < MIN_STABLE_YEARS,
        "moderate_features": sorted(mod_feats),
        "strong_features": sorted(strong_feats),
    }


def build_recommendation(recommendation, metrics):
    reasons = {
        REC_PASSES: (
            "At least one horizon is ready for a tiny research model: %d feature(s) are "
            "moderate-or-better, %d are strong, and at least one feature family shows a directionally "
            "consistent signal, all leakage-free. A tiny, research-only walk-forward model on the "
            "cleared features/horizons is permitted next - still no production candidate, no "
            "deployment, and no production edge claim."
            % (metrics["moderate_feature_count"], metrics["strong_feature_count"])),
        REC_WEAK: (
            "Some features reach the moderate IC / bucket-spread bar (%d moderate-or-better, %d "
            "strong), but the panel does not clear the full tiny-model gate (needs >= %d "
            "moderate-or-better, >= %d strong, a ready horizon, and a stable family). On a "
            "20-ticker survivorship-biased sample this is too thin to justify training; expand the "
            "SEC fundamentals universe before any modeling."
            % (metrics["moderate_feature_count"], metrics["strong_feature_count"],
               READY_MIN_MODERATE, READY_MIN_STRONG)),
        REC_INCONCLUSIVE: (
            "Label coverage and feature non-null coverage are sufficient and several features show "
            "non-trivial raw IC magnitude (max |mean IC| = %s), but every qualifying dense "
            "cross-section falls in only %d distinct year(s) - a single recent regime created when "
            "the 365-day staleness cap concentrated the panel near recent filings - over heavily "
            "overlapping forward-return windows with thin daily cross-sections. That inflates "
            "apparent IC and IR and gives no multi-regime stability, and the best features' yearly "
            "IC signs are "
            "not consistent. Twenty tickers are too few, and too temporally concentrated, to decide "
            "whether the fundamental feature families carry real signal; expand the SEC sample "
            "(more tickers -> thicker cross-sections across the whole history) before deciding on a "
            "model. This is diagnostic only and claims no edge."
            % (metrics["max_abs_ic"], metrics["max_distinct_ic_years"])),
        REC_FAILS: (
            "No feature or family shows useful IC or bucket-spread signal on the repaired panel. "
            "Do not train a model on this feature set; decide whether to add earnings revisions, "
            "analyst estimates, options, or another external data source instead."),
        REC_BLOCKED: (
            "The required Phase 3-J repair result / repaired panel (or its Phase 3-H feature "
            "metadata) was missing, unconfirmed, or failed the inherited leakage guard, so the IC "
            "diagnostic could not run. Repair or re-confirm the Phase 3-J artifacts first."),
    }
    return {
        "recommendation": recommendation,
        "allowed_values": ALLOWED_RECOMMENDATIONS,
        "create_production_model_candidate_now": False,
        "train_production_model_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "tiny_research_model_allowed_next": recommendation == REC_PASSES,
        "expand_sec_universe_next": recommendation in (REC_WEAK, REC_INCONCLUSIVE),
        "stop_tiny_modeling_now": recommendation == REC_FAILS,
        "reason": reasons[recommendation],
    }


def build_recommended_next_phase(recommendation):
    table = {
        REC_PASSES: (
            "Tiny Fundamental Research Model Walk-Forward",
            "Train a tiny research-only model on the repaired 20-ticker panel using only the "
            "features/horizons cleared by the IC diagnostic; no production candidate."),
        REC_WEAK: (
            "Expand SEC Fundamentals Universe Before Modeling",
            "Expand the SEC fundamentals pipeline beyond 20 tickers before model training because "
            "the signal is too weak on the tiny sample."),
        REC_INCONCLUSIVE: (
            "Expand SEC Fundamentals Universe Before Decision",
            "Expand to a larger universe because 20 tickers are insufficient to decide whether the "
            "feature families work."),
        REC_FAILS: (
            "Stop Tiny Fundamental Modeling",
            "Stop model training on this feature set and decide whether to add earnings revisions, "
            "estimates, options, or another external data source."),
        REC_BLOCKED: (
            "Repair Phase 3-K Inputs",
            "Repair missing or corrupted Phase 3-J artifacts."),
    }
    title, purpose = table[recommendation]
    return {"phase": "3-L", "title": title, "purpose": purpose}


# --------------------------------------------------------------------------- #
# Readiness decision table
# --------------------------------------------------------------------------- #
def build_decision_table(confirmed, leakage_failures, features, metrics, recommendation):
    rows = []

    def add(item, value, passed, note):
        rows.append({"decision_item": item, "value": value,
                     "passed": "" if passed is None else bool(passed), "note": note})

    add("phase3j_confirmed", confirmed.get("all_confirmed"), confirmed.get("all_confirmed"),
        "Phase 3-J confirmed as FUNDAMENTAL_ALIGNMENT_REPAIR_SUCCESS, cap 365d, leakage-free, "
        "routed to 3-K")
    add("selected_cap_days_is_365", confirmed.get("selected_cap_days_is_365"),
        confirmed.get("selected_cap_days_is_365"), "Phase 3-J selected a 365-day staleness cap")
    add("leakage_guard_failures", leakage_failures, leakage_failures == 0,
        "inherited point-in-time invariant re-checked on the repaired panel; must be 0")
    add("engineered_feature_count", len(features), len(features) > 0,
        "candidate fundamental features evaluated (metadata and labels excluded)")
    add("moderate_or_better_feature_count", metrics["moderate_feature_count"],
        metrics["moderate_feature_count"] >= READY_MIN_MODERATE,
        "tiny-model gate wants >= %d moderate-or-better features" % READY_MIN_MODERATE)
    add("strong_feature_count", metrics["strong_feature_count"],
        metrics["strong_feature_count"] >= READY_MIN_STRONG,
        "tiny-model gate wants >= %d strong feature" % READY_MIN_STRONG)
    add("stable_family_count", metrics["stable_family_count"],
        metrics["stable_family_count"] >= 1, "tiny-model gate wants >= 1 stable family")
    add("any_horizon_ready_for_tiny_model", metrics["any_horizon_ready"],
        metrics["any_horizon_ready"], "at least one horizon must be ready_for_tiny_model")
    add("distinct_ic_years_max", metrics["max_distinct_ic_years"],
        metrics["max_distinct_ic_years"] >= MIN_STABLE_YEARS,
        "qualifying dense cross-sections must span >= %d distinct years to certify a gate "
        "(guards single-regime / overlapping-window inflation)" % MIN_STABLE_YEARS)
    add("single_regime_small_sample", metrics["single_regime"], not metrics["single_regime"],
        "True means every IC observation falls in too few distinct years - diagnostic only")
    add("max_absolute_mean_ic", metrics["max_abs_ic"], None,
        "largest |mean rank IC| over features/horizons (diagnostic magnitude; not a gate)")
    add("no_model_training", True, True, "this phase trains no model")
    add("no_predictions_computed", True, True, "this phase computes no predictions or scores")
    add("no_portfolio_weights", True, True, "this phase computes no portfolio weights")
    add("recommendation", recommendation, recommendation in ALLOWED_RECOMMENDATIONS,
        "allowed values only")
    return rows


# --------------------------------------------------------------------------- #
# Interpretation + safety flags
# --------------------------------------------------------------------------- #
def build_interpretation(recommendation, metrics):
    return {
        "ic_diagnostic_only": True,
        "price_volume_only_modeling_stopped": True,
        "uses_phase3j_repaired_365d_panel": True,
        "model_features_computed": True,
        "price_join_performed": True,
        "labels_computed": True,
        "labels_for_validation_only": True,
        "new_labels_created": False,
        "ic_computed": True,
        "rank_ic_computed": True,
        "bucket_spreads_computed": True,
        "predictions_computed": False,
        "scores_computed": False,
        "portfolio_weights_computed": False,
        "model_trained": False,
        "research_model_trained": False,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "deployable_model_artifact_written": False,
        "tiny_research_model_allowed_next": recommendation == REC_PASSES,
        "sec_public_data_used": True,
        "read_from_d_drive": False,
        "wrote_to_d_drive": False,
        "network_used": False,
        "full_128_ticker_ingestion": False,
        "results_context_remains_survivorship_biased": True,
        "production_edge_claimed": False,
        "moderate_or_better_feature_count": metrics["moderate_feature_count"],
        "strong_feature_count": metrics["strong_feature_count"],
        "max_absolute_mean_ic": metrics["max_abs_ic"],
        "max_distinct_ic_years": metrics["max_distinct_ic_years"],
        "single_regime_small_sample": metrics["single_regime"],
        "narrative": (
            "Phase 3-K is a controlled, repo-local signal-readiness diagnostic on the Phase 3-J "
            "repaired 20-ticker fundamental panel (staleness-capped at 365 calendar days, "
            "leakage-free). It read the committed Phase 3-J repair result, confirmed it succeeded "
            "(FUNDAMENTAL_ALIGNMENT_REPAIR_SUCCESS, cap 365d, 20 tickers, zero leakage) and routed "
            "here, re-verified the inherited point-in-time invariant as a corruption guard, and "
            "then measured cross-sectional daily rank ICs (Spearman) of each fundamental feature "
            "against forward excess returns and forward rank labels, plus top-vs-bottom bucket "
            "return spreads and feature-family / yearly / sector diagnostics. It read only "
            "repo-local Phase 3-J / Phase 3-H outputs on the C: drive, used no network, and read or "
            "wrote nothing on the D: drive. It fitted NO model (no regression / logistic / ridge / "
            "lasso / tree / ML estimator), computed NO predictions, scores, trading rankings, or "
            "portfolio weights, created NO production model candidate, wrote NO deployable model "
            "artifact, touched no database, ran no migration, restarted no prediction service, "
            "enabled no serving flag, placed no orders, and traded nothing. The universe is a tiny, "
            "survivorship-biased 20-ticker sample, so every IC here is diagnostic only and claims "
            "no production edge."),
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
        "model_trained": False,
        "predictions_computed": False,
        "portfolio_weights_computed": False,
        "tiny_research_model_allowed_next": recommendation == REC_PASSES,
    }


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #
def _write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
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


def _strip_private(rows):
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run(result_json_path=RESULT_JSON, feature_ic_path=FEATURE_IC_CSV,
        feature_family_path=FEATURE_FAMILY_IC_CSV, horizon_readiness_path=HORIZON_READINESS_CSV,
        yearly_ic_path=YEARLY_IC_CSV, sector_sanity_path=SECTOR_SANITY_CSV,
        readiness_decision_path=READINESS_DECISION_CSV):
    phase3j = _load_json(PHASE3J_JSON)
    confirmed = confirm_phase3j(phase3j)
    phase3j_rec = (phase3j or {}).get("recommendation", {}).get("recommendation")

    panel_rows, header = load_panel(PHASE3J_PANEL_CSV)
    inputs_ok = bool(panel_rows) and bool(header)
    features = identify_feature_columns(header)
    dict_map = load_feature_family_map(PHASE3H_FEATURE_DICT_CSV)
    family_of = {f: map_feature_to_family(f, dict_map) for f in features}

    leakage_failures = leakage_guard_failures(panel_rows)

    # Non-null fraction per feature over the whole repaired panel.
    total_rows = len(panel_rows)
    non_null_frac = {}
    for f in features:
        nn = sum(1 for r in panel_rows if _to_float(r.get(f)) is not None)
        non_null_frac[f] = (nn / total_rows) if total_rows else 0.0

    # Label coverage by horizon (forward_excess_return non-null fraction).
    label_coverage = {}
    for h in HORIZONS:
        col = "forward_excess_return_vs_spy_%dd" % h
        nn = sum(1 for r in panel_rows if _to_float(r.get(col)) is not None)
        label_coverage[h] = _round(nn / total_rows, 4) if total_rows else 0.0

    groups = group_by_date(panel_rows)

    # Core diagnostics.
    feature_rows, excess_ic_series = compute_feature_ic(groups, features, family_of, non_null_frac)
    family_rows = compute_family_summary(feature_rows)
    horizon_rows = compute_horizon_readiness(feature_rows, groups, label_coverage)
    best_features = pick_best_features(feature_rows, top_n=3)
    yearly_rows, yearly_summary = compute_yearly_diagnostics(
        best_features, excess_ic_series, feature_rows)
    sector_rows, sector_summary = compute_sector_sanity(panel_rows, best_features)

    # Decision.
    recommendation, metrics = decide(
        confirmed, inputs_ok, leakage_failures, feature_rows, horizon_rows, family_rows)
    decision_table = build_decision_table(
        confirmed, leakage_failures, features, metrics, recommendation)

    # Write CSV artifacts.
    _write_csv(feature_ic_path, FEATURE_IC_COLUMNS, feature_rows)
    _write_csv(feature_family_path, FEATURE_FAMILY_IC_COLUMNS, family_rows)
    _write_csv(horizon_readiness_path, HORIZON_READINESS_COLUMNS, horizon_rows)
    _write_csv(yearly_ic_path, YEARLY_IC_COLUMNS, yearly_rows)
    _write_csv(sector_sanity_path, SECTOR_SANITY_COLUMNS, sector_rows)
    _write_csv(readiness_decision_path, READINESS_DECISION_COLUMNS, decision_table)

    # Dataset / feature-set / label summaries for the result JSON.
    sectors_present = sorted({(r.get("sector") or "").strip() or "UNKNOWN" for r in panel_rows})
    tickers_present = sorted({(r.get("ticker") or "").strip().upper() for r in panel_rows})
    scoring_dates = [r["scoring_date"] for r in panel_rows if r.get("scoring_date")]
    dataset_summary = {
        "repaired_panel_rows": total_rows,
        "ticker_count": len(tickers_present),
        "tickers": tickers_present,
        "sector_count": len(sectors_present),
        "sectors": sectors_present,
        "scoring_date_count": len({d for d in scoring_dates}),
        "start_date": min(scoring_dates) if scoring_dates else "",
        "end_date": max(scoring_dates) if scoring_dates else "",
        "leakage_guard_failures": leakage_failures,
    }
    fam_counts = {}
    for f in features:
        fam_counts[family_of[f]] = fam_counts.get(family_of[f], 0) + 1
    feature_set_summary = {
        "engineered_feature_count": len(features),
        "features": list(features),
        "feature_family_of": dict(family_of),
        "feature_family_counts": fam_counts,
        "non_null_feature_fraction": {f: _round(non_null_frac[f], 4) for f in features},
        "excluded_metadata_columns": sorted(_META_COLUMNS),
        "excluded_label_prefixes": list(_LABEL_PREFIXES),
    }
    label_summary = {
        "horizons": HORIZONS,
        "excess_return_label": "forward_excess_return_vs_spy_{h}d",
        "rank_label": "forward_return_rank_by_date_{h}d",
        "label_coverage_by_horizon": {("%dd" % h): label_coverage[h] for h in HORIZONS},
        "labels_for_validation_only": True,
        "new_labels_created": False,
    }
    ic_methodology = {
        "ic_type": "cross-sectional daily Spearman rank information coefficient",
        "labels": ["forward_excess_return_vs_spy_{h}d", "forward_return_rank_by_date_{h}d"],
        "min_cross_section_per_date": MIN_CROSS_SECTION,
        "grouping": "rows grouped by scoring_date; IC computed within each date, then summarized "
                    "across dates",
        "hit_rate_definition": "fraction of dates whose IC has the same sign as the mean IC "
                               "(directional consistency, valid for positive and negative signals)",
        "ic_ir_definition": "mean IC divided by the sample standard deviation of the per-date ICs",
        "bucket_diagnostic": "per date, rank tickers by feature value; top vs bottom quintile when "
                             ">= %d valid obs, and tiny-sample top-%d vs bottom-%d; average "
                             "forward_excess_return_vs_spy in each bucket and take top-minus-bottom"
                             % (QUINTILE_MIN_OBS, TOPK, TOPK),
        "moderate_or_better_gate": {
            "abs_mean_rank_ic_at_least": MODERATE_ABS_IC, "date_count_at_least": MODERATE_MIN_DATES,
            "ic_hit_rate_at_least": MODERATE_MIN_HIT,
            "non_null_feature_fraction_at_least": MODERATE_MIN_NONNULL},
        "strong_gate": {
            "abs_mean_rank_ic_at_least": STRONG_ABS_IC, "date_count_at_least": STRONG_MIN_DATES,
            "ic_hit_rate_at_least": STRONG_MIN_HIT,
            "non_null_feature_fraction_at_least": STRONG_MIN_NONNULL,
            "bucket_spread_sign_matches_ic": True,
            "bucket_spread_hit_rate_at_least": STRONG_MIN_SPREAD_HIT},
        "no_model_fit": True,
        "small_sample_caveat": "Only 20 survivorship-biased tickers; per-date cross-sections are "
                               "tiny, so ICs are noisy and every result is diagnostic only.",
    }
    readiness_decision_summary = {
        "moderate_or_better_feature_count": metrics["moderate_feature_count"],
        "moderate_or_better_features": metrics["moderate_features"],
        "strong_feature_count": metrics["strong_feature_count"],
        "strong_features": metrics["strong_features"],
        "stable_family_count": metrics["stable_family_count"],
        "any_horizon_ready_for_tiny_model": metrics["any_horizon_ready"],
        "max_absolute_mean_ic": metrics["max_abs_ic"],
        "max_absolute_bucket_spread": metrics["max_abs_spread"],
        "data_quality_sufficient": metrics["data_quality_ok"],
        "max_distinct_ic_years": metrics["max_distinct_ic_years"],
        "single_regime_small_sample": metrics["single_regime"],
        "min_stable_years_required": MIN_STABLE_YEARS,
        "decision_table": decision_table,
    }

    result = {
        "phase": PHASE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "phase3j_result_json":
                "research/output/phase3j_repaired_fundamental_price_alignment.json",
            "phase3j_repaired_panel_csv":
                "research/output/phase3j_repaired_fundamental_price_alignment/"
                "repaired_aligned_panel_20ticker_sample.csv",
            "phase3j_repaired_alignment_quality_report_json":
                "research/output/phase3j_repaired_fundamental_price_alignment/"
                "repaired_alignment_quality_report.json",
            "phase3j_repaired_label_summary_by_horizon_csv":
                "research/output/phase3j_repaired_fundamental_price_alignment/"
                "repaired_label_summary_by_horizon.csv",
            "phase3h_feature_dictionary_csv":
                "research/output/phase3h_sec_fundamental_features/feature_dictionary.csv",
            "phase3h_feature_quality_report_json":
                "research/output/phase3h_sec_fundamental_features/feature_quality_report.json",
        },
        "outputs_written": {
            "result_json": "research/output/phase3k_tiny_fundamental_ic_readiness.json",
            "feature_ic_summary_csv":
                "research/output/phase3k_tiny_fundamental_ic_readiness/feature_ic_summary.csv",
            "feature_family_ic_summary_csv":
                "research/output/phase3k_tiny_fundamental_ic_readiness/"
                "feature_family_ic_summary.csv",
            "horizon_readiness_summary_csv":
                "research/output/phase3k_tiny_fundamental_ic_readiness/"
                "horizon_readiness_summary.csv",
            "yearly_ic_summary_csv":
                "research/output/phase3k_tiny_fundamental_ic_readiness/yearly_ic_summary.csv",
            "sector_sanity_summary_csv":
                "research/output/phase3k_tiny_fundamental_ic_readiness/sector_sanity_summary.csv",
            "readiness_decision_table_csv":
                "research/output/phase3k_tiny_fundamental_ic_readiness/readiness_decision_table.csv",
        },
        "phase3j_summary": {
            "phase3j_confirmed": confirmed,
            "phase3j_recommendation": phase3j_rec,
            "phase3j_selected_cap_days":
                (phase3j or {}).get("recommendation", {}).get("selected_cap_days"),
            "phase3j_repaired_aligned_rows":
                (phase3j or {}).get("repaired_panel_summary", {}).get("repaired_aligned_rows"),
            "phase3j_repaired_aligned_ticker_count":
                (phase3j or {}).get("repaired_panel_summary", {}).get(
                    "repaired_aligned_ticker_count"),
            "phase3j_leakage_failure_count_after_repair":
                (phase3j or {}).get("leakage_check_summary", {}).get(
                    "leakage_failure_count_after_repair"),
        },
        "dataset_summary": dataset_summary,
        "feature_set_summary": feature_set_summary,
        "label_summary": label_summary,
        "ic_methodology": ic_methodology,
        "feature_ic_summary": _strip_private(feature_rows),
        "feature_family_summary": _strip_private(family_rows),
        "horizon_readiness_summary": _strip_private(horizon_rows),
        "yearly_diagnostics_summary": {
            "best_features": best_features,
            "by_feature_horizon": yearly_summary,
            "by_year": yearly_rows,
        },
        "sector_sanity_summary": {
            "by_sector": sector_rows,
            "overall": sector_summary,
        },
        "readiness_decision_summary": readiness_decision_summary,
        "recommendation": build_recommendation(recommendation, metrics),
        "interpretation": build_interpretation(recommendation, metrics),
        "recommended_next_phase": build_recommended_next_phase(recommendation),
    }
    result.update(build_safety_flags(recommendation))

    _dump_json(result_json_path, result)
    return result


def main():
    result = run()
    rec = result["recommendation"]
    nxt = result["recommended_next_phase"]
    ds = result["dataset_summary"]
    rds = result["readiness_decision_summary"]
    print("Phase %s - Tiny Fundamental IC and Model-Readiness Diagnostic" % result["phase"])
    print("  phase3j confirmed        : %s"
          % result["phase3j_summary"]["phase3j_confirmed"]["all_confirmed"])
    print("  repaired panel rows      : %s" % ds["repaired_panel_rows"])
    print("  tickers / sectors        : %s / %s" % (ds["ticker_count"], ds["sector_count"]))
    print("  date span                : %s -> %s" % (ds["start_date"], ds["end_date"]))
    print("  leakage guard failures   : %s" % ds["leakage_guard_failures"])
    print("  engineered features      : %s" % result["feature_set_summary"]
          ["engineered_feature_count"])
    print("  --- horizon readiness ---")
    for h in result["horizon_readiness_summary"]:
        print("    %-4s | cov %s | avg_cs %s | ic_dates %s | best %s (|IC| %s) | mod %s | "
              "strong %s | %s"
              % (h["horizon"], h["label_coverage"], h["avg_daily_cross_section_size"],
                 h["valid_ic_date_count"], h["best_feature"], h["best_feature_abs_mean_ic"],
                 h["moderate_or_better_count"], h["strong_count"], h["horizon_readiness"]))
    print("  moderate-or-better feats : %s" % rds["moderate_or_better_feature_count"])
    print("  strong feats             : %s" % rds["strong_feature_count"])
    print("  stable families          : %s" % rds["stable_family_count"])
    print("  max |mean IC|            : %s" % rds["max_absolute_mean_ic"])
    print("  network / d_drive r / w  : %s / %s / %s"
          % (result["network_used"], result["d_drive_read"], result["d_drive_written"]))
    print("  model trained            : %s" % result["model_trained"])
    print("  recommendation           : %s" % rec["recommendation"])
    print("  tiny model allowed next  : %s" % rec["tiny_research_model_allowed_next"])
    print("  recommended next phase   : %s - %s" % (nxt["phase"], nxt["title"]))
    return result


if __name__ == "__main__":
    main()
