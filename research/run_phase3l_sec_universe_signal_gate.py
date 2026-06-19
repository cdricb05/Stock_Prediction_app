"""Phase 3-L - Full SEC Universe Expansion + End-to-End Fundamental Signal Gate.

Phase 3-K (FUNDAMENTAL_IC_DIAGNOSTIC_INCONCLUSIVE_SMALL_SAMPLE) found that the repaired 20-ticker
fundamental panel carried non-trivial raw IC magnitude but confined every dense cross-section to a
single recent regime (only 3 distinct calendar years, ~5 names per date), so it could not decide
whether the SEC fundamental feature families carry real signal and recommended expanding the SEC
universe before any modeling decision. This phase is the accelerated end-to-end answer: it expands
the SEC fundamentals pipeline from the 20-ticker prototype toward the full current 128-equity
universe, normalizes fundamentals point-in-time, builds the same trailing-only feature families,
aligns them to the existing D: price panel with staleness controls, generates validation-only
labels, and runs the same IC / feature-family / temporal-breadth signal gate on the larger sample.
It decides whether Phase 3-M may train a research-only model, or whether SEC-only fundamentals are
insufficient and richer data (analyst estimate revisions, earnings consensus, options, sentiment)
must be added first.

Network policy. Network is used ONLY for official SEC public JSON endpoints (www.sec.gov /
data.sec.gov): company_tickers.json once, then submissions + companyfacts per selected ticker, with
a declared User-Agent, a minimum 0.25s gap between requests, and a hard cap of 270 total requests.
No other domains, no paid vendor API, no third-party market-data vendor package, no data purchase.
Raw responses are pruned to the
mapped concepts and cached under research/output/phase3l_sec_universe_signal_gate/raw/ and re-read
from cache on subsequent runs (cache-first). If SEC access and cache both fail, a clearly-marked
BLOCKED result is written instead of crashing.

Scope and safety. This phase reads the D: price panel READ ONLY and writes nothing to the D: drive.
It generates forward-return labels for VALIDATION ONLY. It fits NO model (no regression / logistic /
ridge / lasso / tree / ML estimator), computes NO predictions, scores, trading rankings, or
portfolio weights, creates NO production model candidate, and writes NO deployable model artifact.
It touches no database, runs no migration, restarts no prediction service, enables no serving flag,
places no orders, and trades nothing. Membership is current-as-of, so every result remains
survivorship-caveated and claims no production edge.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import math
import os
import time
import urllib.error
import urllib.request
from datetime import datetime

PHASE = "3-L"

# --------------------------------------------------------------------------- #
# Paths (repo-local on the C: drive, except the READ-ONLY D: price panel)
# --------------------------------------------------------------------------- #
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUT_DIR = os.path.join(_REPO_ROOT, "research", "output")
_IN_DIR = os.path.join(_REPO_ROOT, "research", "input")
_L_DIR = os.path.join(_OUT_DIR, "phase3l_sec_universe_signal_gate")
_RAW_DIR = os.path.join(_L_DIR, "raw")

# Inputs.
PHASE3K_JSON = os.path.join(_OUT_DIR, "phase3k_tiny_fundamental_ic_readiness.json")
PHASE3J_JSON = os.path.join(_OUT_DIR, "phase3j_repaired_fundamental_price_alignment.json")
PHASE3H_FEATURE_DICT_CSV = os.path.join(
    _OUT_DIR, "phase3h_sec_fundamental_features", "feature_dictionary.csv")
PHASE3E_INGESTION_CONTRACT_JSON = os.path.join(_OUT_DIR, "phase3e_ingestion_contract.json")
SECTOR_MAP_CSV = os.path.join(_IN_DIR, "phase2k_p_sector_map_current.csv")

# Inputs - Phase 2K-G historical price panel (READ ONLY; on the D: drive).
PRICE_CSV = os.path.join(
    "D:", os.sep, "Stock_Prediction_app_data", "phase2k_g", "output",
    "phase2k_g_expanded_price_history_free.csv")
PRICE_QUALITY_JSON = os.path.join(
    "D:", os.sep, "Stock_Prediction_app_data", "phase2k_g", "output",
    "phase2k_g_data_quality_report.json")
PRICE_SURVIVORSHIP_JSON = os.path.join(
    "D:", os.sep, "Stock_Prediction_app_data", "phase2k_g", "output",
    "phase2k_g_survivorship_caveat.json")

# Outputs.
RESULT_JSON = os.path.join(_OUT_DIR, "phase3l_sec_universe_signal_gate.json")
COMPANY_IDENTITY_CSV = os.path.join(_L_DIR, "company_identity_universe.csv")
FUNDAMENTALS_CSV = os.path.join(_L_DIR, "fundamentals_universe.csv")
FEATURE_SNAPSHOT_CSV = os.path.join(_L_DIR, "feature_snapshot_universe.csv")
ALIGNED_PANEL_CSV = os.path.join(_L_DIR, "aligned_feature_price_panel_universe.csv")
DATA_QUALITY_JSON = os.path.join(_L_DIR, "data_quality_report.json")
FIELD_COVERAGE_CSV = os.path.join(_L_DIR, "field_coverage_by_ticker.csv")
FEATURE_COVERAGE_CSV = os.path.join(_L_DIR, "feature_coverage_by_ticker.csv")
STALENESS_SUMMARY_CSV = os.path.join(_L_DIR, "staleness_summary.csv")
LEAKAGE_CHECKS_CSV = os.path.join(_L_DIR, "leakage_checks.csv")
LABEL_SUMMARY_CSV = os.path.join(_L_DIR, "label_summary_by_horizon.csv")
FEATURE_IC_CSV = os.path.join(_L_DIR, "feature_ic_summary.csv")
FEATURE_FAMILY_IC_CSV = os.path.join(_L_DIR, "feature_family_ic_summary.csv")
HORIZON_READINESS_CSV = os.path.join(_L_DIR, "horizon_readiness_summary.csv")
YEARLY_IC_CSV = os.path.join(_L_DIR, "yearly_ic_summary.csv")
SECTOR_SANITY_CSV = os.path.join(_L_DIR, "sector_sanity_summary.csv")
DECISION_TABLE_CSV = os.path.join(_L_DIR, "decision_table.csv")

# --------------------------------------------------------------------------- #
# Upstream confirmation contract
# --------------------------------------------------------------------------- #
PHASE3K_EXPECTED_RECOMMENDATION = "FUNDAMENTAL_IC_DIAGNOSTIC_INCONCLUSIVE_SMALL_SAMPLE"

# --------------------------------------------------------------------------- #
# SEC access policy (official public endpoints only)
# --------------------------------------------------------------------------- #
SEC_USER_AGENT = "PaperTraderResearch/Phase3L cedric.binisti.research@example.com"
SEC_ACCEPT_ENCODING = "gzip, deflate"
ALLOWED_SEC_HOSTS = ("www.sec.gov", "data.sec.gov")
COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik10}.json"
COMPANYFACTS_URL_TEMPLATE = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
MIN_REQUEST_INTERVAL_S = 0.25
MAX_TOTAL_REQUESTS = 270

BENCHMARK = "SPY"

# Filing forms kept (periodic statements only) + cache caps (denser history than the prototype, but
# still pruned to the mapped concepts so the raw cache stays well under the size budget).
_KEEP_FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A"}
_CACHE_FACT_CAP = 80        # most-recent facts kept per concept-unit in the cached payload
_CACHE_SUBM_CAP = 80        # most-recent periodic filings kept in the cached submissions payload
_OUTPUT_PERIOD_CAP = 60     # distinct fiscal periods emitted per (ticker, normalized_field)

SOURCE_NAME = "SEC EDGAR (public companyfacts + submissions JSON)"

# XBRL us-gaap concept mappings (Phase 3-F / 3-G field mapping).
CONCEPT_MAP = {
    "revenue": ["Revenues", "SalesRevenueNet", "RevenueFromContractWithCustomerExcludingAssessedTax"],
    "net_income": ["NetIncomeLoss"],
    "operating_income": ["OperatingIncomeLoss"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "shareholder_equity": ["StockholdersEquity"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capital_expenditures": ["PaymentsToAcquirePropertyPlantAndEquipment"],
}
DERIVED_FIELDS = ["free_cash_flow"]
ALL_ATTEMPTED_FIELDS = list(CONCEPT_MAP.keys()) + DERIVED_FIELDS
_WANTED_CONCEPTS = sorted({c for cs in CONCEPT_MAP.values() for c in cs})

_RESTATEMENT_POLICY = (
    "first-reported preferred (earliest filed value kept per fiscal period); companyfacts may "
    "include later restated values - treated as caveated, never back-dated")

IDENTITY_COLUMNS = [
    "ticker", "company_name", "cik", "sector", "industry", "source",
    "effective_from", "effective_to",
]
FUNDAMENTALS_COLUMNS = [
    "ticker", "cik", "fiscal_period_end", "fiscal_year", "fiscal_period", "form", "filed",
    "frame", "source_concept", "normalized_field", "value", "unit", "source",
    "availability_datetime", "point_in_time_usable", "restatement_policy", "validation_note",
]
REQUIRED_SOURCE_FIELDS = ["revenue", "net_income", "total_assets", "operating_cash_flow"]

# Minimum processed universe.
SUCCESS_MIN_PROCESSED_TICKERS = 90
PARTIAL_MIN_PROCESSED_TICKERS = 60

# --------------------------------------------------------------------------- #
# Feature engineering (Phase 3-H definitions; raw pivot fields + emitted features)
# --------------------------------------------------------------------------- #
RAW_PIVOT_FIELDS = [
    "revenue", "net_income", "operating_income", "eps_diluted", "total_assets",
    "total_liabilities", "shareholder_equity", "operating_cash_flow", "capital_expenditures",
    "free_cash_flow",
]
_ANNUAL_FORMS = {"10-K", "10-K/A"}
_QUARTERLY_FORMS = {"10-Q", "10-Q/A"}
_QUARTERLY_FP = {"Q1", "Q2", "Q3", "Q4"}
EXTREME_RATIO_ABS = 10.0
EXTREME_GROWTH_ABS = 5.0

# (name, family) for every emitted feature, in dictionary order.  family is the Phase 3-H family.
EMITTED_FEATURE_DEFS = [
    ("operating_margin", "profitability"),
    ("net_margin", "profitability"),
    ("fcf_margin", "profitability"),
    ("operating_cash_flow_margin", "profitability"),
    ("debt_proxy_total_liabilities_to_assets", "balance_sheet"),
    ("equity_to_assets", "balance_sheet"),
    ("asset_turnover_proxy", "balance_sheet"),
    ("liability_to_equity", "balance_sheet"),
    ("revenue_yoy_growth", "growth"),
    ("net_income_yoy_growth", "growth"),
    ("operating_income_yoy_growth", "growth"),
    ("eps_diluted_yoy_growth", "growth"),
    ("total_assets_yoy_growth", "growth"),
    ("operating_cash_flow_yoy_growth", "growth"),
    ("free_cash_flow_yoy_growth", "growth"),
    ("cash_conversion", "quality"),
    ("fcf_to_net_income", "quality"),
    ("capex_intensity", "quality"),
    ("accrual_proxy", "quality"),
    ("log_total_assets", "size"),
    ("log_revenue_abs", "size"),
    ("log_total_liabilities_abs", "size"),
    ("filing_lag_days", "availability_metadata"),
]
EMITTED_FEATURES = [n for n, _ in EMITTED_FEATURE_DEFS]
SNAPSHOT_BASE_COLUMNS = [
    "ticker", "company_name", "sector", "industry", "cik", "fiscal_period_end", "fiscal_year",
    "fiscal_period", "form", "snapshot_type", "feature_asof_date", "source_field_count",
    "required_field_coverage_fraction", "point_in_time_usable", "is_annual_snapshot",
    "is_quarterly_snapshot",
]
SNAPSHOT_COLUMNS = SNAPSHOT_BASE_COLUMNS + EMITTED_FEATURES
FEATURE_COVERAGE_COLUMNS = [
    "ticker", "sector", "feature_name", "non_null_count", "total_snapshot_count",
    "coverage_fraction", "latest_feature_asof_date", "earliest_feature_asof_date",
]
FIELD_COVERAGE_COLUMNS = [
    "ticker", "sector", "field", "periods_present", "non_null_value_count", "coverage_note",
]

# --------------------------------------------------------------------------- #
# Alignment + labels (Phase 3-I definitions)
# --------------------------------------------------------------------------- #
HORIZONS = [21, 63, 126]
ACTIVE_META_COLUMNS = [
    "active_feature_asof_date", "feature_age_days", "active_fiscal_period_end",
    "active_fiscal_year", "active_fiscal_period", "active_form", "active_snapshot_type",
]


def _label_cols():
    cols = []
    for fam in ("forward_return", "forward_spy_return", "forward_excess_return_vs_spy",
                "binary_outperform_spy", "forward_return_rank_by_date"):
        for h in HORIZONS:
            cols.append("%s_%dd" % (fam, h))
    return cols


LABEL_COLUMNS = _label_cols()
PANEL_COLUMNS = (["ticker", "company_name", "sector", "industry", "scoring_date",
                  "adjusted_close"] + ACTIVE_META_COLUMNS + EMITTED_FEATURES + LABEL_COLUMNS)

# Staleness controls.
DEFAULT_CAP_DAYS = 365
SENSITIVITY_CAPS = [365, 540, 730]

# Label-coverage gates for PASS.
COV_GATE = {21: 0.80, 63: 0.70, 126: 0.60}

# --------------------------------------------------------------------------- #
# IC gate (Phase 3-K methodology, scaled to the universe)
# --------------------------------------------------------------------------- #
_META_COLUMNS = {
    "ticker", "company_name", "sector", "industry", "scoring_date", "adjusted_close",
    "active_feature_asof_date", "feature_age_days", "active_fiscal_period_end",
    "active_fiscal_year", "active_fiscal_period", "active_form", "active_snapshot_type",
}
_LABEL_PREFIXES = (
    "forward_return_", "forward_spy_return_", "forward_excess_return_vs_spy_",
    "binary_outperform_spy_", "forward_return_rank_by_date_",
)

FAM_PROFIT = "profitability_margin"
FAM_LEVERAGE = "balance_sheet_leverage"
FAM_GROWTH = "growth_change"
FAM_CASH = "cash_quality"
FAM_SIZE = "size_scale"
FAM_AVAIL = "availability_recency"
FAM_UNKNOWN = "unknown"
ALL_FAMILIES = [FAM_PROFIT, FAM_LEVERAGE, FAM_GROWTH, FAM_CASH, FAM_SIZE, FAM_AVAIL, FAM_UNKNOWN]
_RAW_FAMILY_MAP = {
    "profitability": FAM_PROFIT,
    "balance_sheet": FAM_LEVERAGE,
    "growth": FAM_GROWTH,
    "quality": FAM_CASH,
    "size": FAM_SIZE,
    "availability_metadata": FAM_AVAIL,
}

# A date contributes an IC at the partial floor; the dense floor drives the model-gate breadth.
IC_MIN_CROSS_SECTION = 15        # partial diagnostic floor
DENSE_MIN_CROSS_SECTION = 25     # full-success diagnostic density
QUINTILE_MIN_OBS = 10
TOPK = 10                        # top-10 vs bottom-10 bucket (universe scale)

MODERATE_ABS_IC = 0.03
MODERATE_MIN_DATES = 100
MODERATE_MIN_HIT = 0.52
MODERATE_MIN_NONNULL = 0.50
STRONG_ABS_IC = 0.05
STRONG_MIN_DATES = 100
STRONG_MIN_HIT = 0.55
STRONG_MIN_NONNULL = 0.60
STRONG_MIN_SPREAD_HIT = 0.55

READY_MIN_MODERATE = 5
READY_MIN_STRONG = 2
READY_MIN_STABLE_FAMILIES = 2
READY_MIN_DENSE_IC_DATES = 200

# Temporal breadth.
MIN_QUALIFYING_DATES_PER_YEAR = 20
FULL_MIN_YEARS = 6
PARTIAL_MIN_YEARS = 4
FAINT_SIGNAL_ABS_IC = 0.01

# Aligned-panel PASS thresholds.
PASS_MIN_ALIGNED_TICKERS = 75
PASS_MIN_ALIGNED_ROWS = 75000

# Recommendation vocabulary.
REC_PASSES = "SEC_UNIVERSE_SIGNAL_GATE_PASSES_RESEARCH_MODEL_ALLOWED"
REC_WEAK = "SEC_UNIVERSE_SIGNAL_GATE_WEAK_BUT_EXPAND_OR_ADD_REVISIONS"
REC_INCONCLUSIVE = "SEC_UNIVERSE_SIGNAL_GATE_INCONCLUSIVE_DATA_COVERAGE"
REC_FAILS = "SEC_UNIVERSE_SIGNAL_GATE_FAILS_ADD_RICHER_DATA"
REC_BLOCKED = "SEC_UNIVERSE_SIGNAL_GATE_BLOCKED"
ALLOWED_RECOMMENDATIONS = [REC_PASSES, REC_WEAK, REC_INCONCLUSIVE, REC_FAILS, REC_BLOCKED]

# CSV column schemas (IC gate).
FEATURE_IC_COLUMNS = [
    "feature", "feature_family", "horizon", "observation_count", "date_count",
    "non_null_feature_fraction", "mean_rank_ic_excess_return", "median_rank_ic_excess_return",
    "ic_hit_rate_excess_return", "ic_ir_excess_return", "mean_rank_ic_forward_rank",
    "median_rank_ic_forward_rank", "ic_hit_rate_forward_rank", "ic_ir_forward_rank",
    "absolute_mean_ic", "top_minus_bottom_spread_quintile", "positive_spread_fraction_quintile",
    "top_minus_bottom_spread_top10", "positive_spread_fraction_top10", "signal_direction",
    "diagnostic_strength",
]
FEATURE_FAMILY_IC_COLUMNS = [
    "feature_family", "horizon", "num_features", "best_feature", "best_feature_abs_mean_ic",
    "median_abs_mean_ic", "mean_abs_mean_ic", "moderate_or_better_count", "strong_count",
    "best_top_minus_bottom_spread", "stable_signal", "stability_note",
]
HORIZON_READINESS_COLUMNS = [
    "horizon", "label_coverage", "avg_daily_cross_section_size", "valid_ic_date_count",
    "dense_ic_date_count", "distinct_ic_years", "best_feature", "best_feature_abs_mean_ic",
    "moderate_or_better_count", "strong_count", "horizon_readiness",
]
YEARLY_IC_COLUMNS = [
    "feature", "horizon", "year", "ic_date_count", "mean_rank_ic_excess_return",
    "positive_ic_year", "small_sample_caveat",
]
SECTOR_SANITY_COLUMNS = [
    "sector", "ticker_count", "row_count", "row_fraction", "tickers",
    "operating_margin_non_null_fraction", "capex_intensity_non_null_fraction", "note",
]
DECISION_TABLE_COLUMNS = ["decision_item", "value", "passed", "note"]
STALENESS_SUMMARY_COLUMNS = [
    "cap_days", "is_applied_cap", "aligned_rows_after_cap", "retained_row_fraction",
    "aligned_ticker_count", "median_feature_age_days", "p90_feature_age_days",
    "max_feature_age_days", "label_coverage_21d", "label_coverage_63d", "label_coverage_126d",
    "dense_ic_dates_21d", "distinct_dense_years_21d", "leakage_failure_count",
]
LEAKAGE_CHECKS_COLUMNS = [
    "check_name", "severity", "rows_checked", "failures", "passed", "note",
]

_LEAKAGE_CHECK_DEFS = [
    ("active_feature_asof_date_present", "hard"),
    ("active_feature_asof_before_scoring_date", "hard"),
    ("active_feature_asof_after_or_equal_fiscal_period_end", "hard"),
    ("no_fiscal_period_end_as_availability_date", "hard"),
    ("label_date_after_scoring_date", "hard"),
    ("no_price_join_before_feature_available", "hard"),
]

_SOURCE_LIMITATIONS = [
    "Features come only from SEC as-reported fundamentals; SEC public data provides no earnings "
    "consensus or analyst estimate revisions, so surprise / revision-momentum signals cannot be "
    "built here - that provider gap stays open for a later phase.",
    "The historical price panel is the Phase 2K-G current-as-of free panel and the equity universe "
    "is current-as-of the 2026-06-18 sector map; membership is NOT point-in-time, so this gate and "
    "any downstream result must be reported as survivorship-biased.",
    "Forward-return labels are generated for VALIDATION ONLY (leakage proof, coverage, and IC "
    "measurement); they are never a model target in this phase and are never forward-filled.",
    "Daily scoring with overlapping 21/63/126-day forward windows means per-date ICs are serially "
    "correlated; IR and single-year magnitudes are read as diagnostic, and the temporal-breadth "
    "guard (distinct dense IC years) is what governs the model gate.",
    "companyfacts payloads are pruned to the mapped concepts and most-recent periods to keep the "
    "raw cache Git-small; this captures dense recent history but is not exhaustive deep history.",
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


def _to_int(x):
    f = _to_float(x)
    return int(f) if f is not None else None


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


def _percentile(sorted_vals, p):
    n = len(sorted_vals)
    if n == 0:
        return None
    if n == 1:
        return sorted_vals[0]
    idx = p * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


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


def _ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
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
    if len(xs) < 3:
        return None
    return _pearson(_ranks(xs), _ranks(ys))


# --------------------------------------------------------------------------- #
# Upstream confirmation
# --------------------------------------------------------------------------- #
def confirm_phase3k(phase3k):
    """Confirm the committed Phase 3-K diagnostic result that gates this phase."""
    if not phase3k:
        return {"all_confirmed": False, "phase3k_present": False}
    rec = phase3k.get("recommendation", {}) or {}
    nxt = phase3k.get("recommended_next_phase", {}) or {}
    checks = {
        "phase3k_present": True,
        "phase_is_3k": phase3k.get("phase") == "3-K",
        "recommendation_is_inconclusive_small_sample":
            rec.get("recommendation") == PHASE3K_EXPECTED_RECOMMENDATION,
        "tiny_research_model_allowed_next_false":
            phase3k.get("tiny_research_model_allowed_next") is False
            and rec.get("tiny_research_model_allowed_next") is False,
        "expand_sec_universe_next_true": rec.get("expand_sec_universe_next") is True,
        "next_phase_is_3l": nxt.get("phase") == "3-L",
        "model_trained_false": phase3k.get("model_trained") is False,
        "predictions_computed_false": phase3k.get("predictions_computed") is False,
        "portfolio_weights_computed_false": phase3k.get("portfolio_weights_computed") is False,
        "production_model_candidate_created_false":
            phase3k.get("production_model_candidate_created") is False,
    }
    checks["all_confirmed"] = all(checks.values())
    return checks


def confirm_ingestion_contract(contract):
    if not contract:
        return {"all_confirmed": False, "contract_present": False}
    tables = contract.get("normalized_tables", []) or []
    pit_cols = contract.get("point_in_time_columns", []) or []
    checks = {
        "contract_present": True,
        "has_company_identity_table": "company_identity" in tables,
        "has_fundamentals_table": "fundamentals" in tables,
        "no_database_write_by_default": contract.get("no_database_write_by_default") is True,
        "no_production_integration": contract.get("no_production_integration") is True,
        "pit_columns_include_availability_datetime": "availability_datetime" in pit_cols,
    }
    checks["all_confirmed"] = all(checks.values())
    return checks


def load_sector_map(path):
    """ticker -> {sector, industry, source, as_of_date}; SPY excluded as a benchmark, not equity."""
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            t = (row.get("ticker") or "").strip().upper()
            if not t or t == BENCHMARK:
                continue
            out[t] = {
                "sector": (row.get("sector") or "").strip(),
                "industry": (row.get("industry") or "").strip(),
                "source": (row.get("source") or "").strip(),
                "as_of_date": (row.get("as_of_date") or "").strip(),
            }
    return out


# --------------------------------------------------------------------------- #
# SEC client (throttled, capped, host-restricted, cache-first, pruned cache)
# --------------------------------------------------------------------------- #
class SecClient:
    def __init__(self):
        self.network_requests = 0
        self.cache_hits = 0
        self.request_count = 0
        self.errors = []
        self._last_request_time = 0.0

    @staticmethod
    def _host_allowed(url):
        for host in ALLOWED_SEC_HOSTS:
            if url.startswith("https://" + host + "/"):
                return True
        return False

    def _throttle(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < MIN_REQUEST_INTERVAL_S:
            time.sleep(MIN_REQUEST_INTERVAL_S - elapsed)

    def fetch_json(self, url):
        if not self._host_allowed(url):
            raise ValueError("refusing non-SEC URL: " + url)
        if self.request_count >= MAX_TOTAL_REQUESTS:
            raise RuntimeError("SEC request cap reached (%d)" % MAX_TOTAL_REQUESTS)
        self._throttle()
        req = urllib.request.Request(url, headers={
            "User-Agent": SEC_USER_AGENT,
            "Accept-Encoding": SEC_ACCEPT_ENCODING,
            "Accept": "application/json",
        })
        self.request_count += 1
        self._last_request_time = time.time()
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read()
            if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        self.network_requests += 1
        return json.loads(raw.decode("utf-8"))

    def get_cached_or_fetch(self, url, cache_path, prune_fn):
        if os.path.isfile(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                self.cache_hits += 1
                return json.load(f), "cache"
        full = self.fetch_json(url)
        pruned = prune_fn(full)
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(pruned, f, separators=(",", ":"))
        return pruned, "network"


def _prune_company_tickers(full):
    """Keep the full ticker->cik map (small, ~0.7 MB)."""
    out = {}
    i = 0
    for v in full.values():
        out[str(i)] = {
            "cik_str": v.get("cik_str"),
            "ticker": v.get("ticker"),
            "title": v.get("title"),
        }
        i += 1
    return out


def _prune_submissions(full):
    recent = (full.get("filings", {}) or {}).get("recent", {}) or {}
    keys = ["accessionNumber", "form", "filingDate", "acceptanceDateTime", "reportDate"]
    arrs = {k: recent.get(k, []) or [] for k in keys}
    n = len(arrs["accessionNumber"])
    forms = arrs["form"]
    idx = [i for i in range(n) if i < len(forms) and forms[i] in _KEEP_FORMS][:_CACHE_SUBM_CAP]
    pruned_recent = {k: [arrs[k][i] for i in idx if i < len(arrs[k])] for k in keys}
    return {
        "cik": full.get("cik"),
        "name": full.get("name"),
        "tickers": full.get("tickers"),
        "sicDescription": full.get("sicDescription"),
        "filings": {"recent": pruned_recent},
    }


def _prune_companyfacts(full):
    ug = (full.get("facts", {}) or {}).get("us-gaap", {}) or {}
    kept = {}
    for concept in _WANTED_CONCEPTS:
        node = ug.get(concept)
        if not node:
            continue
        pruned_units = {}
        for unit, facts in (node.get("units", {}) or {}).items():
            periodic = [f for f in facts if f.get("form") in _KEEP_FORMS]
            periodic.sort(key=lambda f: f.get("filed") or "")
            pruned_units[unit] = periodic[-_CACHE_FACT_CAP:]
        if pruned_units:
            kept[concept] = {"label": node.get("label"), "units": pruned_units}
    return {
        "cik": full.get("cik"),
        "entityName": full.get("entityName"),
        "facts": {"us-gaap": kept},
    }


def _company_ticker_index(company_tickers):
    idx = {}
    for v in (company_tickers or {}).values():
        t = (v.get("ticker") or "").upper()
        if t:
            idx[t] = (v.get("cik_str"), v.get("title"))
    return idx


def _accession_to_acceptance(submissions):
    recent = (submissions.get("filings", {}) or {}).get("recent", {}) or {}
    accns = recent.get("accessionNumber", []) or []
    accepted = recent.get("acceptanceDateTime", []) or []
    out = {}
    for i, accn in enumerate(accns):
        if i < len(accepted) and accepted[i]:
            out[accn] = accepted[i]
    return out


# --------------------------------------------------------------------------- #
# Normalization (Phase 3-F field mapping; first-reported, point-in-time)
# --------------------------------------------------------------------------- #
def _normalize_identity(ticker, cik_int, title, sector_info):
    as_of = sector_info.get("as_of_date", "")
    return {
        "ticker": ticker,
        "company_name": title or "",
        "cik": ("%010d" % cik_int) if cik_int is not None else "",
        "sector": sector_info.get("sector", ""),
        "industry": sector_info.get("industry", ""),
        "source": SOURCE_NAME,
        "effective_from": as_of or "",
        "effective_to": "",
    }


def _normalize_fundamentals_for_ticker(ticker, cik_int, companyfacts, acceptance_by_accn):
    cik10 = ("%010d" % cik_int) if cik_int is not None else ""
    ug = (companyfacts.get("facts", {}) or {}).get("us-gaap", {}) or {}
    rows = []
    fields_found = set()
    period_index = {}

    for normalized_field, concepts in CONCEPT_MAP.items():
        chosen_concept = None
        for concept in concepts:
            if concept in ug and (ug[concept].get("units") or {}):
                chosen_concept = concept
                break
        if not chosen_concept:
            continue
        units = ug[chosen_concept].get("units", {}) or {}
        unit = "USD" if "USD" in units else ("USD/shares" if "USD/shares" in units
                                             else next(iter(units), None))
        if unit is None:
            continue
        facts = units[unit]
        best = {}
        for f in facts:
            end = f.get("end")
            fp = f.get("fp")
            form = f.get("form")
            filed = f.get("filed")
            if not end or not fp or form not in _KEEP_FORMS:
                continue
            key = (end, fp, form)
            cur = best.get(key)
            if cur is None or (filed or "") < (cur.get("filed") or ""):
                best[key] = f
        selected = sorted(best.values(), key=lambda f: f.get("end") or "")[-_OUTPUT_PERIOD_CAP:]
        for f in selected:
            end = f.get("end")
            fp = f.get("fp")
            form = f.get("form")
            filed = f.get("filed")
            accn = f.get("accn")
            accepted = acceptance_by_accn.get(accn)
            availability = accepted or filed or ""
            pit_usable = bool(availability)
            note = ("availability_datetime from filing acceptance" if accepted
                    else ("conservative: filed date used as availability_datetime"
                          if filed else "no availability timestamp - quarantined"))
            rows.append({
                "ticker": ticker, "cik": cik10, "fiscal_period_end": end,
                "fiscal_year": f.get("fy"), "fiscal_period": fp, "form": form,
                "filed": filed or "", "frame": f.get("frame") or "",
                "source_concept": "us-gaap:" + chosen_concept,
                "normalized_field": normalized_field, "value": f.get("val"), "unit": unit,
                "source": SOURCE_NAME, "availability_datetime": availability,
                "point_in_time_usable": pit_usable, "restatement_policy": _RESTATEMENT_POLICY,
                "validation_note": note,
            })
            fields_found.add(normalized_field)
            period_index.setdefault((end, fp, form), {})[normalized_field] = (
                f.get("val"), availability, pit_usable)

    for (end, fp, form), fields in period_index.items():
        if "operating_cash_flow" in fields and "capital_expenditures" in fields:
            ocf_val, ocf_av, ocf_pit = fields["operating_cash_flow"]
            capex_val, capex_av, capex_pit = fields["capital_expenditures"]
            if ocf_val is None or capex_val is None:
                continue
            availability = max([a for a in (ocf_av, capex_av) if a] or [""])
            pit_usable = bool(availability) and ocf_pit and capex_pit
            rows.append({
                "ticker": ticker, "cik": cik10, "fiscal_period_end": end, "fiscal_year": None,
                "fiscal_period": fp, "form": form, "filed": "", "frame": "",
                "source_concept": ("derived:NetCashProvidedByUsedInOperatingActivities"
                                   "-PaymentsToAcquirePropertyPlantAndEquipment"),
                "normalized_field": "free_cash_flow", "value": ocf_val - capex_val, "unit": "USD",
                "source": SOURCE_NAME, "availability_datetime": availability,
                "point_in_time_usable": pit_usable, "restatement_policy": _RESTATEMENT_POLICY,
                "validation_note": "derived = operating_cash_flow - capital_expenditures; "
                                   "availability is the later of the two inputs",
            })
            fields_found.add("free_cash_flow")
    return rows, fields_found


# --------------------------------------------------------------------------- #
# Feature engineering (Phase 3-H snapshots + trailing features)
# --------------------------------------------------------------------------- #
def _classify_snapshot(form, fp):
    if fp in _QUARTERLY_FP or form in _QUARTERLY_FORMS:
        return "quarterly"
    if fp == "FY" or form in _ANNUAL_FORMS:
        return "annual"
    return "quarterly" if form in _QUARTERLY_FORMS else "annual"


def build_snapshots(fundamentals_rows, identity_by_ticker):
    groups = {}
    for r in fundamentals_rows:
        ticker = (r.get("ticker") or "").strip().upper()
        field = (r.get("normalized_field") or "").strip()
        if not ticker or field not in RAW_PIVOT_FIELDS:
            continue
        key = (ticker, r.get("fiscal_period_end") or "", r.get("fiscal_year") or "",
               r.get("fiscal_period") or "", r.get("form") or "")
        g = groups.setdefault(key, {
            "ticker": ticker, "fiscal_period_end": r.get("fiscal_period_end") or "",
            "fiscal_year": r.get("fiscal_year") or "", "fiscal_period": r.get("fiscal_period") or "",
            "form": r.get("form") or "", "cik": r.get("cik") or "", "pivot": {},
            "max_avail": "", "all_pit": True, "any_avail": False,
        })
        val = _to_float(r.get("value"))
        if val is not None:
            g["pivot"][field] = val
        avail = (r.get("availability_datetime") or "").strip()
        if avail:
            g["any_avail"] = True
            if avail > g["max_avail"]:
                g["max_avail"] = avail
        pit = str(r.get("point_in_time_usable")).strip().lower() == "true"
        if not pit:
            g["all_pit"] = False

    snapshots = []
    for key, g in groups.items():
        ident = identity_by_ticker.get(g["ticker"], {})
        snapshot_type = _classify_snapshot(g["form"], g["fiscal_period"])
        present = [f for f in RAW_PIVOT_FIELDS if f in g["pivot"]]
        req_present = [f for f in REQUIRED_SOURCE_FIELDS if f in g["pivot"]]
        snapshots.append({
            "ticker": g["ticker"], "company_name": ident.get("company_name", ""),
            "sector": ident.get("sector", ""), "industry": ident.get("industry", ""),
            "cik": ident.get("cik", "") or g["cik"], "fiscal_period_end": g["fiscal_period_end"],
            "fiscal_year": g["fiscal_year"], "fiscal_period": g["fiscal_period"], "form": g["form"],
            "snapshot_type": snapshot_type, "feature_asof_date": g["max_avail"],
            "source_field_count": len(present),
            "required_field_coverage_fraction":
                _round(len(req_present) / len(REQUIRED_SOURCE_FIELDS), 4),
            "point_in_time_usable": bool(g["any_avail"] and g["all_pit"]),
            "is_annual_snapshot": snapshot_type == "annual",
            "is_quarterly_snapshot": snapshot_type == "quarterly",
            "_pivot": g["pivot"], "_fy_int": _to_int(g["fiscal_year"]),
        })
    snapshots.sort(key=lambda s: (s["ticker"], s["fiscal_period_end"], s["fiscal_period"],
                                  s["form"]))
    return snapshots


def build_growth_indexes(snapshots):
    annual_best = {}
    quarterly_best = {}
    for s in snapshots:
        fy = s["_fy_int"]
        if fy is None:
            continue
        if s["is_annual_snapshot"]:
            k = (s["ticker"], fy)
            cur = annual_best.get(k)
            if cur is None or s["fiscal_period_end"] > cur["fiscal_period_end"]:
                annual_best[k] = s
        else:
            k = (s["ticker"], fy, s["fiscal_period"])
            cur = quarterly_best.get(k)
            if cur is None or s["fiscal_period_end"] > cur["fiscal_period_end"]:
                quarterly_best[k] = s
    annual_index = {k: v["_pivot"] for k, v in annual_best.items()}
    quarterly_index = {k: v["_pivot"] for k, v in quarterly_best.items()}
    return annual_index, quarterly_index


def compute_features(snapshots, annual_index, quarterly_index):
    """Populate engineered features on each snapshot; return leakage-warning count."""
    leakage_warnings = 0
    for s in snapshots:
        v = s["_pivot"]

        def get(field):
            return v.get(field)

        def ratio(num, den):
            if num is None or den is None or den == 0:
                return None
            return num / den

        def growth(field, prior_pivot):
            if prior_pivot is None:
                return None
            cur = v.get(field)
            prev = prior_pivot.get(field)
            if cur is None or prev is None or prev == 0:
                return None
            return (cur - prev) / abs(prev)

        # leakage guard on the snapshot itself
        if not s["feature_asof_date"]:
            leakage_warnings += 1
        else:
            asof_d = _parse_date(s["feature_asof_date"])
            end_d = _parse_date(s["fiscal_period_end"])
            if asof_d is not None and end_d is not None and asof_d < end_d:
                leakage_warnings += 1
        if not s["point_in_time_usable"]:
            leakage_warnings += 1

        s["operating_margin"] = ratio(get("operating_income"), get("revenue"))
        s["net_margin"] = ratio(get("net_income"), get("revenue"))
        s["fcf_margin"] = ratio(get("free_cash_flow"), get("revenue"))
        s["operating_cash_flow_margin"] = ratio(get("operating_cash_flow"), get("revenue"))
        s["debt_proxy_total_liabilities_to_assets"] = ratio(
            get("total_liabilities"), get("total_assets"))
        s["equity_to_assets"] = ratio(get("shareholder_equity"), get("total_assets"))
        s["asset_turnover_proxy"] = ratio(get("revenue"), get("total_assets"))
        s["liability_to_equity"] = ratio(get("total_liabilities"), get("shareholder_equity"))

        if s["is_annual_snapshot"] and s["_fy_int"] is not None:
            prior = annual_index.get((s["ticker"], s["_fy_int"] - 1))
        elif s["_fy_int"] is not None:
            prior = quarterly_index.get((s["ticker"], s["_fy_int"] - 1, s["fiscal_period"]))
        else:
            prior = None
        s["revenue_yoy_growth"] = growth("revenue", prior)
        s["net_income_yoy_growth"] = growth("net_income", prior)
        s["operating_income_yoy_growth"] = growth("operating_income", prior)
        s["eps_diluted_yoy_growth"] = growth("eps_diluted", prior)
        s["total_assets_yoy_growth"] = growth("total_assets", prior)
        s["operating_cash_flow_yoy_growth"] = growth("operating_cash_flow", prior)
        s["free_cash_flow_yoy_growth"] = growth("free_cash_flow", prior)

        s["cash_conversion"] = ratio(get("operating_cash_flow"), get("net_income"))
        s["fcf_to_net_income"] = ratio(get("free_cash_flow"), get("net_income"))
        capex = get("capital_expenditures")
        s["capex_intensity"] = ratio(abs(capex) if capex is not None else None, get("revenue"))
        ni, ocf, ta = get("net_income"), get("operating_cash_flow"), get("total_assets")
        s["accrual_proxy"] = ratio(ni - ocf, ta) if (ni is not None and ocf is not None) else None

        ta_v = get("total_assets")
        s["log_total_assets"] = math.log(ta_v) if (ta_v is not None and ta_v > 0) else None
        rev_v = get("revenue")
        s["log_revenue_abs"] = math.log(abs(rev_v)) if (rev_v not in (None, 0)) else None
        tl_v = get("total_liabilities")
        s["log_total_liabilities_abs"] = math.log(abs(tl_v)) if (tl_v not in (None, 0)) else None

        asof_d = _parse_date(s["feature_asof_date"])
        end_d = _parse_date(s["fiscal_period_end"])
        s["filing_lag_days"] = (asof_d - end_d).days if (asof_d and end_d) else None

        for fname in EMITTED_FEATURES:
            if fname == "filing_lag_days":
                continue
            if isinstance(s.get(fname), float):
                s[fname] = _round(s[fname])
    return leakage_warnings


def build_feature_coverage(snapshots):
    by_ticker = {}
    for s in snapshots:
        by_ticker.setdefault(s["ticker"], []).append(s)
    out = []
    for ticker in sorted(by_ticker):
        snaps = by_ticker[ticker]
        sector = snaps[0]["sector"]
        asofs = sorted(s["feature_asof_date"] for s in snaps if s["feature_asof_date"])
        total = len(snaps)
        for fname in EMITTED_FEATURES:
            non_null = sum(1 for s in snaps if s.get(fname) is not None)
            out.append({
                "ticker": ticker, "sector": sector, "feature_name": fname,
                "non_null_count": non_null, "total_snapshot_count": total,
                "coverage_fraction": _round(non_null / total, 4) if total else 0.0,
                "latest_feature_asof_date": asofs[-1] if asofs else "",
                "earliest_feature_asof_date": asofs[0] if asofs else "",
            })
    return out


def build_field_coverage(fundamentals_rows, identity_by_ticker):
    by = {}
    for r in fundamentals_rows:
        t = (r.get("ticker") or "").strip().upper()
        field = (r.get("normalized_field") or "").strip()
        if not t or field not in (RAW_PIVOT_FIELDS):
            continue
        d = by.setdefault((t, field), {"periods": set(), "non_null": 0})
        d["periods"].add(r.get("fiscal_period_end") or "")
        if _to_float(r.get("value")) is not None:
            d["non_null"] += 1
    out = []
    for (t, field) in sorted(by):
        d = by[(t, field)]
        sector = identity_by_ticker.get(t, {}).get("sector", "")
        out.append({
            "ticker": t, "sector": sector, "field": field,
            "periods_present": len(d["periods"]), "non_null_value_count": d["non_null"],
            "coverage_note": "bank/sector-specific fields (operating_income, capex, fcf) may be "
                             "legitimately absent" if field in (
                                 "operating_income", "capital_expenditures", "free_cash_flow")
                             else "",
        })
    return out


# --------------------------------------------------------------------------- #
# Price alignment + labels (Phase 3-I logic)
# --------------------------------------------------------------------------- #
def load_feature_snapshots_from_rows(snapshots):
    """Group emitted snapshots by ticker (ascending by feature_asof_date) for activation."""
    by_ticker = {}
    for s in snapshots:
        asof = (s.get("feature_asof_date") or "").strip()
        t = (s.get("ticker") or "").strip().upper()
        if not asof or not t:
            continue
        by_ticker.setdefault(t, []).append(s)
    for t, snaps in by_ticker.items():
        snaps.sort(key=lambda r: (
            r.get("feature_asof_date") or "", r.get("fiscal_period_end") or "",
            int(_to_float(r.get("source_field_count")) or 0)))
    return by_ticker


def load_price_panel(path, universe):
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
            prices.setdefault(t, []).append((d, close))
            rows_read += 1
    for t in prices:
        prices[t].sort(key=lambda x: x[0])
    return prices, rows_read


def align_ticker(ticker, identity, snaps, ticker_prices, spy_by_date, cap_days):
    rows = []
    if not snaps or not ticker_prices:
        return rows
    snap_asof_dates = [_date_part(s["feature_asof_date"]) for s in snaps]
    n_prices = len(ticker_prices)
    idx = -1
    for i, (scoring_date, close) in enumerate(ticker_prices):
        while idx + 1 < len(snaps) and snap_asof_dates[idx + 1] < scoring_date:
            idx += 1
        if idx < 0:
            continue
        active = snaps[idx]
        active_asof = active["feature_asof_date"]
        age = _days_between(scoring_date, active_asof)
        if age is None or age > cap_days:   # staleness cap applied during alignment
            continue
        row = {
            "ticker": ticker,
            "company_name": identity.get("company_name", active.get("company_name", "")),
            "sector": identity.get("sector", active.get("sector", "")),
            "industry": identity.get("industry", active.get("industry", "")),
            "scoring_date": scoring_date, "adjusted_close": _round(close, 6),
            "active_feature_asof_date": active_asof, "feature_age_days": age,
            "active_fiscal_period_end": active.get("fiscal_period_end", ""),
            "active_fiscal_year": active.get("fiscal_year", ""),
            "active_fiscal_period": active.get("fiscal_period", ""),
            "active_form": active.get("form", ""),
            "active_snapshot_type": active.get("snapshot_type", ""),
        }
        for c in EMITTED_FEATURES:
            row[c] = active.get(c)
        for h in HORIZONS:
            fr = spy_ret = excess = None
            binout = None
            j = i + h
            if j < n_prices and close not in (None, 0):
                fut_date, fut_close = ticker_prices[j]
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
            row["forward_return_rank_by_date_%dd" % h] = None
        rows.append(row)
    return rows


def assign_cross_sectional_ranks(rows):
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


def evaluate_leakage_summary(rows):
    """Aggregate the six per-row leakage checks into a compact pass/fail summary."""
    counts = {name: 0 for name, _ in _LEAKAGE_CHECK_DEFS}
    for r in rows:
        scoring = r["scoring_date"]
        asof = r["active_feature_asof_date"]
        asof_d = _date_part(asof)
        fpe = r["active_fiscal_period_end"]
        results = {
            "active_feature_asof_date_present": bool(asof),
            "active_feature_asof_before_scoring_date": bool(asof_d) and asof_d < scoring,
            "active_feature_asof_after_or_equal_fiscal_period_end":
                bool(asof_d) and bool(fpe) and asof_d >= fpe,
            "no_fiscal_period_end_as_availability_date": bool(asof_d) and asof_d != fpe,
            "label_date_after_scoring_date": True,
            "no_price_join_before_feature_available": bool(asof_d) and asof_d < scoring,
        }
        for name, _sev in _LEAKAGE_CHECK_DEFS:
            if not results[name]:
                counts[name] += 1
    n = len(rows)
    notes = {
        "active_feature_asof_date_present": "every aligned row carries an availability timestamp",
        "active_feature_asof_before_scoring_date":
            "feature became public strictly before the scoring day",
        "active_feature_asof_after_or_equal_fiscal_period_end":
            "availability is on/after the fiscal period end (no pre-dating)",
        "no_fiscal_period_end_as_availability_date":
            "availability is a filing timestamp, not the fiscal period end",
        "label_date_after_scoring_date":
            "forward labels use realized future closes h>0 trading days ahead",
        "no_price_join_before_feature_available":
            "the joined price row is the scoring day, strictly after the feature was public",
    }
    out = []
    total_failures = 0
    for name, sev in _LEAKAGE_CHECK_DEFS:
        total_failures += counts[name]
        out.append({
            "check_name": name, "severity": sev, "rows_checked": n,
            "failures": counts[name], "passed": counts[name] == 0, "note": notes[name],
        })
    return out, total_failures


# --------------------------------------------------------------------------- #
# IC gate (Phase 3-K methodology)
# --------------------------------------------------------------------------- #
def load_feature_family_map(dict_path):
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


def group_by_date(rows):
    groups = {}
    for r in rows:
        groups.setdefault((r.get("scoring_date") or "").strip(), []).append(r)
    return [(d, groups[d]) for d in sorted(groups) if d]


def daily_rank_ic(groups, feature, label_col):
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
        if len(xs) >= IC_MIN_CROSS_SECTION:
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
    return {"mean": _round(mean_ic, 6), "median": _round(_median(ics), 6),
            "hit_rate": _round(hit, 4), "ir": _round(ir, 4) if ir is not None else None,
            "date_count": len(ics)}


def bucket_spread_series(groups, feature, label_col, k_quintile=True):
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
    hit = sum(1 for s in spreads if _sign(s) == sgn) / len(spreads) if sgn != 0 else 0.0
    return {"mean": _round(mean_s, 6), "positive_fraction": _round(pos_frac, 4),
            "hit_rate": _round(hit, 4), "date_count": len(spreads)}


def _classify_strength(excess, spread_q, non_null_frac):
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
    return "moderate" if is_moderate else "weak"


def _signal_direction(excess):
    if excess["date_count"] < 30 or excess["mean"] is None:
        return "unstable"
    if (excess["hit_rate"] or 0.0) < 0.5 or abs(excess["mean"]) < 0.005:
        return "unstable"
    return "positive" if excess["mean"] > 0 else "negative"


def compute_feature_ic(groups, features, family_of, non_null_frac):
    rows = []
    excess_ic_series = {}
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
            rows.append({
                "feature": feat, "feature_family": fam, "horizon": "%dd" % h, "_horizon_days": h,
                "observation_count": obs, "date_count": ex["date_count"],
                "non_null_feature_fraction": _round(nnf, 4) if nnf is not None else None,
                "mean_rank_ic_excess_return": ex["mean"],
                "median_rank_ic_excess_return": ex["median"],
                "ic_hit_rate_excess_return": ex["hit_rate"], "ic_ir_excess_return": ex["ir"],
                "mean_rank_ic_forward_rank": rk["mean"],
                "median_rank_ic_forward_rank": rk["median"],
                "ic_hit_rate_forward_rank": rk["hit_rate"], "ic_ir_forward_rank": rk["ir"],
                "absolute_mean_ic": abs_mean_ic,
                "top_minus_bottom_spread_quintile": spread_q["mean"],
                "positive_spread_fraction_quintile": spread_q["positive_fraction"],
                "top_minus_bottom_spread_top10": spread_k["mean"],
                "positive_spread_fraction_top10": spread_k["positive_fraction"],
                "signal_direction": _signal_direction(ex),
                "diagnostic_strength": _classify_strength(ex, spread_q, nnf),
            })
    return rows, excess_ic_series


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
            mod_count = sum(1 for r in sub if r["diagnostic_strength"] in ("moderate", "strong"))
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
                "feature_family": fam, "horizon": "%dd" % h, "_horizon_days": h,
                "num_features": len(sub), "best_feature": best["feature"] if best else "",
                "best_feature_abs_mean_ic": best["absolute_mean_ic"] if best else None,
                "median_abs_mean_ic": _round(_median(abs_ics), 6) if abs_ics else None,
                "mean_abs_mean_ic": _round(_mean(abs_ics), 6) if abs_ics else None,
                "moderate_or_better_count": mod_count, "strong_count": strong_count,
                "best_top_minus_bottom_spread": _round(max(spreads), 6) if spreads else None,
                "stable_signal": stable, "stability_note": note,
            })
    return out


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
        cs_sizes, valid_ic_dates, dense_ic_dates = [], 0, 0
        dense_by_year = {}
        for date, rs in groups:
            labeled = sum(1 for r in rs if _to_float(r.get(ex_col)) is not None)
            if labeled >= 1:
                cs_sizes.append(labeled)
            if labeled >= IC_MIN_CROSS_SECTION:
                valid_ic_dates += 1
            if labeled >= DENSE_MIN_CROSS_SECTION:
                dense_ic_dates += 1
                dense_by_year[date[:4]] = dense_by_year.get(date[:4], 0) + 1
        distinct_ic_years = sum(1 for n in dense_by_year.values()
                                if n >= MIN_QUALIFYING_DATES_PER_YEAR)
        breadth_ok = distinct_ic_years >= FULL_MIN_YEARS
        if (mod_count >= 1 and dense_ic_dates >= READY_MIN_DENSE_IC_DATES and breadth_ok):
            readiness = "ready_for_research_model"
        elif valid_ic_dates >= MODERATE_MIN_DATES and judgeable:
            readiness = "diagnostic_only"
        else:
            readiness = "not_ready"
        out.append({
            "horizon": "%dd" % h, "_horizon_days": h, "label_coverage": label_coverage.get(h),
            "avg_daily_cross_section_size": _round(_mean(cs_sizes), 2) if cs_sizes else None,
            "valid_ic_date_count": valid_ic_dates, "dense_ic_date_count": dense_ic_dates,
            "distinct_ic_years": distinct_ic_years,
            "best_feature": best["feature"] if best else "",
            "best_feature_abs_mean_ic": best["absolute_mean_ic"] if best else None,
            "moderate_or_better_count": mod_count, "strong_count": strong_count,
            "horizon_readiness": readiness,
        })
    return out


def pick_best_features(feature_rows, top_n=5):
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
                by_year.setdefault(date[:4], []).append(ic)
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
                row = {
                    "feature": feat, "horizon": "%dd" % h, "year": yr,
                    "ic_date_count": len(ics), "mean_rank_ic_excess_return": _round(ymean, 6),
                    "positive_ic_year": positive, "small_sample_caveat": len(ics) < 12,
                }
                rows.append(row)
                year_rows.append(row)
            n_years = len(year_rows)
            summary.append({
                "feature": feat, "horizon": "%dd" % h, "year_count": n_years,
                "positive_ic_year_fraction": _round(pos_years / n_years, 4) if n_years else None,
                "bad_years": bad_years,
                "small_sample_caveat": "daily overlapping windows make single-year ICs serially "
                                       "correlated; treat year signs as directional checks only",
            })
    return rows, summary


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
            "sector": sec, "ticker_count": len(d["tickers"]), "row_count": d["rows"],
            "row_fraction": _round(d["rows"] / total, 4) if total else None,
            "tickers": "|".join(sorted(d["tickers"])),
            "operating_margin_non_null_fraction": _round(om_frac, 4),
            "capex_intensity_non_null_fraction": _round(capex_frac, 4),
            "note": "; ".join(notes),
        })
    summary = {
        "sector_count": len(by_sector), "best_features": list(best_features),
        "missing_features_concentrated_in_financials": financials_gap,
        "note": "Best-feature signal is measured cross-sectionally across all sectors; no sector "
                "neutralization or modeling is performed in this phase. Margin / capex families "
                "are structurally undefined for Financials, so any margin/cash-quality signal is "
                "effectively measured on non-financial names.",
    }
    return out, summary


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
# Decision
# --------------------------------------------------------------------------- #
def _decision_metrics(feature_rows, horizon_rows, family_rows, universe_summary, alignment_summary,
                      label_coverage, leakage_failures):
    mod_feats = {r["feature"] for r in feature_rows
                 if r["diagnostic_strength"] in ("moderate", "strong")}
    strong_feats = {r["feature"] for r in feature_rows if r["diagnostic_strength"] == "strong"}
    abs_ics = [r["absolute_mean_ic"] for r in feature_rows if r["absolute_mean_ic"] is not None
               and r["diagnostic_strength"] != "unusable"]
    any_ready = any(h["horizon_readiness"] == "ready_for_research_model" for h in horizon_rows)
    max_distinct_years = max((h.get("distinct_ic_years", 0) for h in horizon_rows), default=0)
    label_cov_ok = all(label_coverage.get(h, 0.0) >= COV_GATE[h] for h in HORIZONS)
    coverage_ok = (
        universe_summary["processed_ticker_count"] >= SUCCESS_MIN_PROCESSED_TICKERS
        and alignment_summary["aligned_ticker_count"] >= PASS_MIN_ALIGNED_TICKERS
        and alignment_summary["aligned_rows"] >= PASS_MIN_ALIGNED_ROWS
        and label_cov_ok)
    return {
        "moderate_feature_count": len(mod_feats),
        "strong_feature_count": len(strong_feats),
        "stable_family_count": sum(1 for f in family_rows if f["stable_signal"]),
        "any_horizon_ready": any_ready,
        "max_abs_ic": _round(max(abs_ics), 6) if abs_ics else None,
        "max_distinct_ic_years": max_distinct_years,
        "single_regime": max_distinct_years < PARTIAL_MIN_YEARS,
        "label_coverage_ok": label_cov_ok,
        "coverage_ok_for_decision": coverage_ok,
        "leakage_failure_count": leakage_failures,
        "moderate_features": sorted(mod_feats),
        "strong_features": sorted(strong_feats),
    }


def decide(confirmed, inputs_ok, sec_ok, metrics):
    if not confirmed.get("all_confirmed") or not inputs_ok or not sec_ok:
        return REC_BLOCKED
    if metrics["leakage_failure_count"] > 0:
        return REC_BLOCKED
    passes = (
        metrics["coverage_ok_for_decision"] and metrics["any_horizon_ready"]
        and metrics["moderate_feature_count"] >= READY_MIN_MODERATE
        and metrics["strong_feature_count"] >= READY_MIN_STRONG
        and metrics["stable_family_count"] >= READY_MIN_STABLE_FAMILIES
        and metrics["max_distinct_ic_years"] >= FULL_MIN_YEARS)
    if passes:
        return REC_PASSES
    if not metrics["coverage_ok_for_decision"] or metrics["max_distinct_ic_years"] < PARTIAL_MIN_YEARS:
        return REC_INCONCLUSIVE
    no_signal = (metrics["moderate_feature_count"] == 0
                 and (metrics["max_abs_ic"] is None or metrics["max_abs_ic"] < FAINT_SIGNAL_ABS_IC))
    if no_signal:
        return REC_FAILS
    return REC_WEAK


def build_recommendation(recommendation, metrics):
    reasons = {
        REC_PASSES: (
            "On the expanded SEC universe the gate clears every bar: coverage is sufficient "
            "(processed >= %d tickers, aligned >= %d tickers, >= %d aligned rows, label coverage "
            "above 0.80/0.70/0.60), the panel is leakage-free, %d feature(s) are moderate-or-better "
            "and %d are strong, at least %d feature families show a directionally consistent signal, "
            "at least one horizon is ready_for_research_model, and the qualifying dense "
            "cross-sections span >= %d distinct calendar years (multi-regime). A research-only "
            "walk-forward model on the cleared features/horizons is permitted next - still no "
            "production candidate, no deployment, and no production edge claim."
            % (SUCCESS_MIN_PROCESSED_TICKERS, PASS_MIN_ALIGNED_TICKERS, PASS_MIN_ALIGNED_ROWS,
               metrics["moderate_feature_count"], metrics["strong_feature_count"],
               READY_MIN_STABLE_FAMILIES, FULL_MIN_YEARS)),
        REC_WEAK: (
            "Coverage is sufficient and the panel is leakage-free, and some signal exists "
            "(%d moderate-or-better feature(s), %d strong, max |mean IC| = %s), but it does not "
            "clear the full research-model gate (needs >= %d moderate-or-better, >= %d strong, a "
            "ready horizon, >= %d stable families, and >= %d distinct dense IC years). SEC-only "
            "fundamentals are not enough on their own; expand the universe further or add analyst "
            "estimate revisions / earnings consensus before any training. No model is trained here "
            "and no production edge is claimed."
            % (metrics["moderate_feature_count"], metrics["strong_feature_count"],
               metrics["max_abs_ic"], READY_MIN_MODERATE, READY_MIN_STRONG,
               READY_MIN_STABLE_FAMILIES, FULL_MIN_YEARS)),
        REC_INCONCLUSIVE: (
            "SEC coverage or price-alignment coverage is insufficient to decide: either fewer than "
            "%d tickers processed / %d aligned / %d aligned rows / the label-coverage gates were "
            "not met, or the qualifying dense cross-sections span fewer than %d distinct calendar "
            "years (max %d) so the sample is still effectively single-regime. The data is not yet "
            "broad enough to judge whether the SEC fundamental feature families carry signal; "
            "repair/expand SEC universe coverage before deciding. Diagnostic only; no edge claimed."
            % (SUCCESS_MIN_PROCESSED_TICKERS, PASS_MIN_ALIGNED_TICKERS, PASS_MIN_ALIGNED_ROWS,
               PARTIAL_MIN_YEARS, metrics["max_distinct_ic_years"])),
        REC_FAILS: (
            "With sufficient, multi-regime coverage on the expanded SEC universe, no feature or "
            "family shows useful IC or bucket-spread signal (no moderate-or-better feature; max "
            "|mean IC| = %s below the faint-signal floor). SEC-only as-reported fundamentals do not "
            "carry a usable cross-sectional edge for these horizons; stop SEC-only modeling and add "
            "richer external data (analyst estimates / revisions / earnings consensus / options / "
            "sentiment) before any model training. No edge claimed." % (metrics["max_abs_ic"],)),
        REC_BLOCKED: (
            "The gate could not run: required Phase 3-K / Phase 3-J / contract inputs were missing "
            "or unconfirmed, SEC access and cache both failed to deliver a meaningful universe, or "
            "a leakage invariant failed on the aligned panel. Repair the inputs / SEC access before "
            "continuing. No data was purchased and no paid vendor API was called."),
    }
    research_allowed = recommendation == REC_PASSES
    add_richer = recommendation in (REC_WEAK, REC_FAILS)
    expand_or_repair = recommendation in (REC_WEAK, REC_INCONCLUSIVE, REC_BLOCKED)
    return {
        "recommendation": recommendation,
        "allowed_values": ALLOWED_RECOMMENDATIONS,
        "create_production_model_candidate_now": False,
        "train_production_model_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "research_model_allowed_next": research_allowed,
        "add_richer_data_next": add_richer,
        "expand_or_repair_data_next": expand_or_repair,
        "reason": reasons[recommendation],
    }


def build_recommended_next_phase(recommendation):
    table = {
        REC_PASSES: (
            "Research-Only Fundamental Model Walk-Forward",
            "Train a research-only walk-forward model using only the features and horizons cleared "
            "by this gate; no production candidate."),
        REC_WEAK: (
            "Add Estimate Revisions or Earnings Consensus",
            "Add richer structured data because SEC-only fundamentals have weak but insufficient "
            "signal."),
        REC_INCONCLUSIVE: (
            "Repair SEC Universe Coverage",
            "Improve ticker / filing / feature / price alignment coverage before the signal "
            "decision."),
        REC_FAILS: (
            "Add Richer External Data Before Modeling",
            "Stop SEC-only modeling and add revisions, consensus, options, or sentiment before any "
            "model training."),
        REC_BLOCKED: (
            "Repair Phase 3-L Inputs",
            "Repair missing/corrupted inputs or SEC access/cache failure."),
    }
    title, purpose = table[recommendation]
    return {"phase": "3-M", "title": title, "purpose": purpose}


def build_decision_table(confirmed, sec_ok, universe_summary, alignment_summary, label_coverage,
                         metrics, recommendation):
    rows = []

    def add(item, value, passed, note):
        rows.append({"decision_item": item, "value": value,
                     "passed": "" if passed is None else bool(passed), "note": note})

    add("phase3k_confirmed", confirmed.get("all_confirmed"), confirmed.get("all_confirmed"),
        "Phase 3-K confirmed as FUNDAMENTAL_IC_DIAGNOSTIC_INCONCLUSIVE_SMALL_SAMPLE, routed to 3-L")
    add("sec_access_ok", sec_ok, sec_ok,
        "SEC company_tickers + per-ticker facts reachable (network or cache)")
    add("processed_ticker_count", universe_summary["processed_ticker_count"],
        universe_summary["processed_ticker_count"] >= SUCCESS_MIN_PROCESSED_TICKERS,
        "PASS wants >= %d processed tickers (partial floor %d)"
        % (SUCCESS_MIN_PROCESSED_TICKERS, PARTIAL_MIN_PROCESSED_TICKERS))
    add("aligned_ticker_count", alignment_summary["aligned_ticker_count"],
        alignment_summary["aligned_ticker_count"] >= PASS_MIN_ALIGNED_TICKERS,
        "PASS wants >= %d aligned tickers" % PASS_MIN_ALIGNED_TICKERS)
    add("aligned_rows", alignment_summary["aligned_rows"],
        alignment_summary["aligned_rows"] >= PASS_MIN_ALIGNED_ROWS,
        "PASS wants >= %d aligned rows" % PASS_MIN_ALIGNED_ROWS)
    add("leakage_failure_count", metrics["leakage_failure_count"],
        metrics["leakage_failure_count"] == 0, "must be 0")
    add("label_coverage_21d", label_coverage.get(21),
        label_coverage.get(21, 0.0) >= COV_GATE[21], "PASS wants >= %.2f" % COV_GATE[21])
    add("label_coverage_63d", label_coverage.get(63),
        label_coverage.get(63, 0.0) >= COV_GATE[63], "PASS wants >= %.2f" % COV_GATE[63])
    add("label_coverage_126d", label_coverage.get(126),
        label_coverage.get(126, 0.0) >= COV_GATE[126], "PASS wants >= %.2f" % COV_GATE[126])
    add("moderate_or_better_feature_count", metrics["moderate_feature_count"],
        metrics["moderate_feature_count"] >= READY_MIN_MODERATE,
        "PASS wants >= %d moderate-or-better features" % READY_MIN_MODERATE)
    add("strong_feature_count", metrics["strong_feature_count"],
        metrics["strong_feature_count"] >= READY_MIN_STRONG,
        "PASS wants >= %d strong features" % READY_MIN_STRONG)
    add("stable_family_count", metrics["stable_family_count"],
        metrics["stable_family_count"] >= READY_MIN_STABLE_FAMILIES,
        "PASS wants >= %d stable feature families" % READY_MIN_STABLE_FAMILIES)
    add("any_horizon_ready_for_research_model", metrics["any_horizon_ready"],
        metrics["any_horizon_ready"], "at least one horizon must be ready_for_research_model")
    add("distinct_dense_ic_years_max", metrics["max_distinct_ic_years"],
        metrics["max_distinct_ic_years"] >= FULL_MIN_YEARS,
        "PASS wants >= %d distinct dense IC years (partial floor %d)"
        % (FULL_MIN_YEARS, PARTIAL_MIN_YEARS))
    add("single_regime_small_sample", metrics["single_regime"], not metrics["single_regime"],
        "True means qualifying dense cross-sections fall in < %d distinct years" % PARTIAL_MIN_YEARS)
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
def build_interpretation(recommendation, metrics, network_used):
    return {
        "sec_universe_signal_gate_only": True,
        "price_volume_only_modeling_stopped": True,
        "expanded_beyond_tiny_sample": True,
        "model_features_computed": True,
        "price_join_performed": True,
        "labels_computed": True,
        "labels_for_validation_only": True,
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
        "research_model_allowed_next": recommendation == REC_PASSES,
        "sec_public_data_used": True,
        "external_data_ingested": True,
        "production_data_ingested": False,
        "read_from_d_drive": True,
        "wrote_to_d_drive": False,
        "network_used": bool(network_used),
        "paid_vendor_called": False,
        "data_purchased": False,
        "results_context_remains_survivorship_biased": True,
        "production_edge_claimed": False,
        "moderate_or_better_feature_count": metrics["moderate_feature_count"],
        "strong_feature_count": metrics["strong_feature_count"],
        "max_absolute_mean_ic": metrics["max_abs_ic"],
        "max_distinct_ic_years": metrics["max_distinct_ic_years"],
        "single_regime_small_sample": metrics["single_regime"],
        "narrative": (
            "Phase 3-L is the accelerated, research-only SEC universe expansion + end-to-end "
            "fundamental signal gate. It confirmed Phase 3-K was inconclusive on the tiny sample "
            "and routed here, mapped the current 128-equity universe (SPY excluded as a benchmark) "
            "to CIKs via SEC company_tickers.json, fetched/cached submissions + companyfacts from "
            "official SEC public endpoints only (throttled, capped, cache-first), normalized "
            "fundamentals point-in-time using the Phase 3-F/3-G field mapping, built the Phase 3-H "
            "trailing feature families with a safe feature_asof_date (never the fiscal period end), "
            "read the Phase 2K-G price panel READ ONLY on the D: drive, aligned features to the "
            "trading calendar with a staleness cap, generated 21/63/126-day forward labels for "
            "validation only, ran leakage checks, and measured cross-sectional daily rank ICs, "
            "bucket spreads, feature-family / yearly / sector diagnostics, and a temporal-breadth "
            "guard. It fitted NO model, computed NO predictions / scores / trading rankings / "
            "portfolio weights, created NO production model candidate, wrote NO deployable model "
            "artifact, touched no database, ran no migration, restarted no prediction service, "
            "enabled no serving flag, wrote nothing to the D: drive, called no paid vendor API, "
            "used no third-party market-data vendor package, purchased no data, placed no orders, "
            "and traded nothing. The "
            "universe is current-as-of, so every result remains survivorship-biased and claims no "
            "production edge."),
    }


def build_safety_flags(recommendation, network_used):
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
        "sec_public_data_used": True,
        "vendor_api_called": False,
        "paid_vendor_api_called": False,
        "data_purchase_made": False,
        "d_drive_read": True,
        "d_drive_written": False,
        "network_used": bool(network_used),
        "external_data_ingested": True,
        "production_data_ingested": False,
        "model_features_computed": True,
        "price_join_performed": True,
        "labels_computed": True,
        "labels_for_validation_only": True,
        "model_trained": False,
        "predictions_computed": False,
        "portfolio_weights_computed": False,
        "research_model_allowed_next": recommendation == REC_PASSES,
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


def _age_stats(rows):
    ages = [_to_float(r.get("feature_age_days")) for r in rows]
    ages = [a for a in ages if a is not None]
    if not ages:
        return {"count": 0, "median": None, "p90": None, "max": None}
    vs = sorted(ages)
    return {"count": len(vs), "median": _round(_percentile(vs, 0.5), 2),
            "p90": _round(_percentile(vs, 0.90), 2), "max": _round(vs[-1], 2)}


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run(result_json_path=RESULT_JSON, l_dir=_L_DIR, raw_dir=_RAW_DIR, price_csv=PRICE_CSV,
        max_tickers=None, verbose=True):
    """Run the full Phase 3-L gate. max_tickers caps the universe (for a gated fast test)."""
    phase3k = _load_json(PHASE3K_JSON)
    phase3j = _load_json(PHASE3J_JSON)
    contract = _load_json(PHASE3E_INGESTION_CONTRACT_JSON)
    confirmed = confirm_phase3k(phase3k)
    confirmed_contract = confirm_ingestion_contract(contract)
    sector_map = load_sector_map(SECTOR_MAP_CSV)

    out_paths = {
        "identity": os.path.join(l_dir, "company_identity_universe.csv"),
        "fundamentals": os.path.join(l_dir, "fundamentals_universe.csv"),
        "snapshot": os.path.join(l_dir, "feature_snapshot_universe.csv"),
        "panel": os.path.join(l_dir, "aligned_feature_price_panel_universe.csv"),
        "data_quality": os.path.join(l_dir, "data_quality_report.json"),
        "field_cov": os.path.join(l_dir, "field_coverage_by_ticker.csv"),
        "feat_cov": os.path.join(l_dir, "feature_coverage_by_ticker.csv"),
        "staleness": os.path.join(l_dir, "staleness_summary.csv"),
        "leakage": os.path.join(l_dir, "leakage_checks.csv"),
        "label": os.path.join(l_dir, "label_summary_by_horizon.csv"),
        "feature_ic": os.path.join(l_dir, "feature_ic_summary.csv"),
        "family_ic": os.path.join(l_dir, "feature_family_ic_summary.csv"),
        "horizon": os.path.join(l_dir, "horizon_readiness_summary.csv"),
        "yearly": os.path.join(l_dir, "yearly_ic_summary.csv"),
        "sector": os.path.join(l_dir, "sector_sanity_summary.csv"),
        "decision": os.path.join(l_dir, "decision_table.csv"),
    }

    # ---- 1. SEC universe build (CIK map -> submissions + companyfacts -> normalize) ----
    universe_tickers = sorted(sector_map)
    if max_tickers is not None:
        universe_tickers = universe_tickers[:max_tickers]

    client = SecClient()
    raw_files = []
    identity_rows = []
    fundamentals_rows = []
    fields_found_by_ticker = {}
    processed, failed = [], []
    company_index = {}

    ct_cache = os.path.join(raw_dir, "company_tickers.json")
    try:
        company_tickers, _ = client.get_cached_or_fetch(
            COMPANY_TICKERS_URL, ct_cache, _prune_company_tickers)
        if os.path.isfile(ct_cache):
            raw_files.append("company_tickers.json")
        company_index = _company_ticker_index(company_tickers)
    except (urllib.error.URLError, OSError, ValueError, RuntimeError, json.JSONDecodeError) as e:
        client.errors.append("company_tickers fetch failed: %s" % e)

    sec_ok = bool(company_index)
    for n, t in enumerate(universe_tickers):
        if verbose and n and n % 10 == 0:
            print("  ... processed %d/%d tickers (rows=%d, reqs=%d)"
                  % (n, len(universe_tickers), len(fundamentals_rows), client.request_count))
        cik_int, title = company_index.get(t, (None, None))
        if cik_int is None:
            failed.append({"ticker": t, "reason": "CIK not found in SEC company_tickers"})
            continue
        cik10 = "%010d" % int(cik_int)
        sub_cache = os.path.join(raw_dir, "submissions_%s.json" % cik10)
        cf_cache = os.path.join(raw_dir, "companyfacts_%s.json" % cik10)
        try:
            submissions, _ = client.get_cached_or_fetch(
                SUBMISSIONS_URL_TEMPLATE.format(cik10=cik10), sub_cache, _prune_submissions)
            companyfacts, _ = client.get_cached_or_fetch(
                COMPANYFACTS_URL_TEMPLATE.format(cik10=cik10), cf_cache, _prune_companyfacts)
        except (urllib.error.URLError, OSError, ValueError, RuntimeError,
                json.JSONDecodeError) as e:
            client.errors.append("%s (CIK%s) fetch failed: %s" % (t, cik10, e))
            failed.append({"ticker": t, "reason": "SEC fetch/cache failed: %s" % e})
            continue
        if os.path.isfile(sub_cache):
            raw_files.append("submissions_%s.json" % cik10)
        if os.path.isfile(cf_cache):
            raw_files.append("companyfacts_%s.json" % cik10)
        acceptance = _accession_to_acceptance(submissions)
        sector_info = sector_map.get(t, {})
        identity_rows.append(_normalize_identity(
            t, int(cik_int), title or submissions.get("name"), sector_info))
        rows, found = _normalize_fundamentals_for_ticker(
            t, int(cik_int), companyfacts, acceptance)
        fundamentals_rows.extend(rows)
        fields_found_by_ticker[t] = found
        if rows:
            processed.append(t)
        else:
            failed.append({"ticker": t, "reason": "no mappable fundamentals concepts found"})

    network_used = client.network_requests > 0
    identity_by_ticker = {r["ticker"]: r for r in identity_rows}

    # ---- 2/3. Feature snapshots ----
    snapshots = build_snapshots(fundamentals_rows, identity_by_ticker)
    annual_index, quarterly_index = build_growth_indexes(snapshots)
    snapshot_leakage_warnings = compute_features(snapshots, annual_index, quarterly_index)
    feature_coverage = build_feature_coverage(snapshots)
    field_coverage = build_field_coverage(fundamentals_rows, identity_by_ticker)

    # ---- 4. Price alignment + labels (staleness cap applied) ----
    snaps_by_ticker = load_feature_snapshots_from_rows(snapshots)
    universe = set(snaps_by_ticker) | {BENCHMARK}
    prices_by_ticker, price_rows_read = load_price_panel(price_csv, universe)
    spy_rows = prices_by_ticker.get(BENCHMARK, [])
    spy_by_date = {d: c for d, c in spy_rows}
    spy_available = bool(spy_rows)
    inputs_ok = bool(snaps_by_ticker) and bool(
        [t for t in snaps_by_ticker if prices_by_ticker.get(t)])

    aligned_rows = []
    for t in sorted(snaps_by_ticker):
        tp = prices_by_ticker.get(t)
        snaps = snaps_by_ticker.get(t)
        if not tp or not snaps:
            continue
        aligned_rows.extend(align_ticker(
            t, identity_by_ticker.get(t, {}), snaps, tp, spy_by_date, DEFAULT_CAP_DAYS))
    assign_cross_sectional_ranks(aligned_rows)

    leakage_check_rows, leakage_failures = evaluate_leakage_summary(aligned_rows)
    guard_failures = leakage_guard_failures(aligned_rows)
    leakage_failures = max(leakage_failures, guard_failures)

    # ---- label coverage + label summary ----
    total_rows = len(aligned_rows)
    label_coverage = {}
    for h in HORIZONS:
        col = "forward_excess_return_vs_spy_%dd" % h
        nn = sum(1 for r in aligned_rows if _to_float(r.get(col)) is not None)
        label_coverage[h] = _round(nn / total_rows, 4) if total_rows else 0.0
    label_summary_rows = []
    for h in HORIZONS:
        fr = [r["forward_return_%dd" % h] for r in aligned_rows
              if r.get("forward_return_%dd" % h) is not None]
        ex = [r["forward_excess_return_vs_spy_%dd" % h] for r in aligned_rows
              if r.get("forward_excess_return_vs_spy_%dd" % h) is not None]
        bo = [r["binary_outperform_spy_%dd" % h] for r in aligned_rows
              if r.get("binary_outperform_spy_%dd" % h) is not None]
        rk = sum(1 for r in aligned_rows if r.get("forward_return_rank_by_date_%dd" % h) is not None)
        sd = [r["scoring_date"] for r in aligned_rows
              if r.get("forward_return_%dd" % h) is not None]
        label_summary_rows.append({
            "horizon": "%dd" % h, "total_rows": total_rows, "non_null_forward_return": len(fr),
            "non_null_forward_excess_return_vs_spy": len(ex),
            "non_null_binary_outperform_spy": len(bo), "non_null_rank": rk,
            "mean_forward_return": _round(_mean(fr), 6),
            "mean_forward_excess_return_vs_spy": _round(_mean(ex), 6),
            "positive_return_fraction":
                _round(sum(1 for v in fr if v > 0) / len(fr), 4) if fr else None,
            "outperform_spy_fraction": _round(_mean(bo), 4) if bo else None,
            "earliest_scoring_date": min(sd) if sd else "",
            "latest_scoring_date": max(sd) if sd else "",
        })
    label_summary_columns = ["horizon", "total_rows", "non_null_forward_return",
                             "non_null_forward_excess_return_vs_spy",
                             "non_null_binary_outperform_spy", "non_null_rank",
                             "mean_forward_return", "mean_forward_excess_return_vs_spy",
                             "positive_return_fraction", "outperform_spy_fraction",
                             "earliest_scoring_date", "latest_scoring_date"]

    # ---- 5. IC gate ----
    features = list(EMITTED_FEATURES)
    dict_map = load_feature_family_map(PHASE3H_FEATURE_DICT_CSV)
    family_of = {f: map_feature_to_family(f, dict_map) for f in features}
    non_null_frac = {}
    for f in features:
        nn = sum(1 for r in aligned_rows if _to_float(r.get(f)) is not None)
        non_null_frac[f] = (nn / total_rows) if total_rows else 0.0
    groups = group_by_date(aligned_rows)
    feature_rows, excess_ic_series = compute_feature_ic(groups, features, family_of, non_null_frac)
    family_rows = compute_family_summary(feature_rows)
    horizon_rows = compute_horizon_readiness(feature_rows, groups, label_coverage)
    best_features = pick_best_features(feature_rows, top_n=5)
    yearly_rows, yearly_summary = compute_yearly_diagnostics(
        best_features, excess_ic_series, feature_rows)
    sector_rows, sector_summary = compute_sector_sanity(aligned_rows, best_features)

    # ---- universe / alignment summaries ----
    aligned_tickers = sorted({r["ticker"] for r in aligned_rows})
    scoring_dates = [r["scoring_date"] for r in aligned_rows if r.get("scoring_date")]
    universe_summary = {
        "universe_source": "research/input/phase2k_p_sector_map_current.csv (current-as-of)",
        "universe_ticker_count": len(universe_tickers),
        "benchmark_excluded": BENCHMARK,
        "processed_ticker_count": len(processed),
        "processed_tickers": list(processed),
        "failed_ticker_count": len(failed),
        "failed_tickers": failed,
        "min_success_processed": SUCCESS_MIN_PROCESSED_TICKERS,
        "min_partial_processed": PARTIAL_MIN_PROCESSED_TICKERS,
        "survivorship_caveat": "current-as-of membership; survivorship-biased",
    }
    alignment_summary = {
        "aligned_rows": total_rows,
        "aligned_ticker_count": len(aligned_tickers),
        "aligned_tickers": aligned_tickers,
        "aligned_start_date": min(scoring_dates) if scoring_dates else "",
        "aligned_end_date": max(scoring_dates) if scoring_dates else "",
        "applied_staleness_cap_days": DEFAULT_CAP_DAYS,
        "feature_age_days_summary": _age_stats(aligned_rows),
        "price_rows_read": price_rows_read,
        "spy_available": spy_available,
        "scoring_grid": "every trading day with a fresh-enough (<= cap) active filing",
    }

    # ---- staleness sensitivity (365/540/730), reusing the aligned snapshots ----
    staleness_rows = []
    for cap in SENSITIVITY_CAPS:
        if cap == DEFAULT_CAP_DAYS:
            sub = aligned_rows
        else:
            sub = []
            for t in sorted(snaps_by_ticker):
                tp = prices_by_ticker.get(t)
                snaps = snaps_by_ticker.get(t)
                if not tp or not snaps:
                    continue
                sub.extend(align_ticker(
                    t, identity_by_ticker.get(t, {}), snaps, tp, spy_by_date, cap))
        st = _age_stats(sub)
        cov = {}
        for h in HORIZONS:
            col = "forward_excess_return_vs_spy_%dd" % h
            nn = sum(1 for r in sub if _to_float(r.get(col)) is not None)
            cov[h] = _round(nn / len(sub), 4) if sub else 0.0
        # dense IC dates + breadth at 21d for this cap
        g = group_by_date(sub)
        ex21 = "forward_excess_return_vs_spy_21d"
        dense, dense_by_year = 0, {}
        for date, rs in g:
            labeled = sum(1 for r in rs if _to_float(r.get(ex21)) is not None)
            if labeled >= DENSE_MIN_CROSS_SECTION:
                dense += 1
                dense_by_year[date[:4]] = dense_by_year.get(date[:4], 0) + 1
        distinct_years = sum(1 for v in dense_by_year.values()
                             if v >= MIN_QUALIFYING_DATES_PER_YEAR)
        staleness_rows.append({
            "cap_days": cap, "is_applied_cap": cap == DEFAULT_CAP_DAYS,
            "aligned_rows_after_cap": len(sub),
            "retained_row_fraction": _round(len(sub) / total_rows, 4) if total_rows else 0.0,
            "aligned_ticker_count": len({r["ticker"] for r in sub}),
            "median_feature_age_days": st["median"], "p90_feature_age_days": st["p90"],
            "max_feature_age_days": st["max"], "label_coverage_21d": cov[21],
            "label_coverage_63d": cov[63], "label_coverage_126d": cov[126],
            "dense_ic_dates_21d": dense, "distinct_dense_years_21d": distinct_years,
            "leakage_failure_count": leakage_guard_failures(sub),
        })

    # ---- decision ----
    metrics = _decision_metrics(feature_rows, horizon_rows, family_rows, universe_summary,
                                alignment_summary, label_coverage, leakage_failures)
    recommendation = decide(confirmed, inputs_ok, sec_ok, metrics)
    decision_table = build_decision_table(
        confirmed, sec_ok, universe_summary, alignment_summary, label_coverage, metrics,
        recommendation)

    # ---- data quality report ----
    fund_total = len(fundamentals_rows)
    with_avail = sum(1 for r in fundamentals_rows if r.get("availability_datetime"))
    pit_usable = sum(1 for r in fundamentals_rows if r.get("point_in_time_usable"))
    data_quality = {
        "phase": PHASE,
        "universe_ticker_count": len(universe_tickers),
        "processed_ticker_count": len(processed),
        "failed_ticker_count": len(failed),
        "identity_rows": len(identity_rows),
        "fundamentals_rows": fund_total,
        "fields_attempted": list(ALL_ATTEMPTED_FIELDS),
        "fields_found": sorted({f for s in fields_found_by_ticker.values() for f in s}),
        "availability_datetime_coverage": _round(with_avail / fund_total, 4) if fund_total else 0.0,
        "point_in_time_usable_fraction": _round(pit_usable / fund_total, 4) if fund_total else 0.0,
        "snapshot_rows": len(snapshots),
        "snapshot_leakage_warning_count": snapshot_leakage_warnings,
        "aligned_rows": total_rows,
        "aligned_ticker_count": len(aligned_tickers),
        "label_coverage_by_horizon": {("%dd" % h): label_coverage[h] for h in HORIZONS},
        "leakage_failure_count": leakage_failures,
        "sec_request_count": client.request_count,
        "network_request_count": client.network_requests,
        "used_cache_count": client.cache_hits,
        "network_used": network_used,
        "errors": client.errors,
        "source_limitations": list(_SOURCE_LIMITATIONS),
        "recommendation": recommendation,
    }

    # ---- write all artifacts ----
    _write_csv(out_paths["identity"], IDENTITY_COLUMNS, identity_rows)
    _write_csv(out_paths["fundamentals"], FUNDAMENTALS_COLUMNS, fundamentals_rows)
    _write_csv(out_paths["snapshot"], SNAPSHOT_COLUMNS, snapshots)
    _write_csv(out_paths["panel"], PANEL_COLUMNS, aligned_rows)
    _dump_json(out_paths["data_quality"], data_quality)
    _write_csv(out_paths["field_cov"], FIELD_COVERAGE_COLUMNS, field_coverage)
    _write_csv(out_paths["feat_cov"], FEATURE_COVERAGE_COLUMNS, feature_coverage)
    _write_csv(out_paths["staleness"], STALENESS_SUMMARY_COLUMNS, staleness_rows)
    _write_csv(out_paths["leakage"], LEAKAGE_CHECKS_COLUMNS, leakage_check_rows)
    _write_csv(out_paths["label"], label_summary_columns, label_summary_rows)
    _write_csv(out_paths["feature_ic"], FEATURE_IC_COLUMNS, feature_rows)
    _write_csv(out_paths["family_ic"], FEATURE_FAMILY_IC_COLUMNS, family_rows)
    _write_csv(out_paths["horizon"], HORIZON_READINESS_COLUMNS, horizon_rows)
    _write_csv(out_paths["yearly"], YEARLY_IC_COLUMNS, yearly_rows)
    _write_csv(out_paths["sector"], SECTOR_SANITY_COLUMNS, sector_rows)
    _write_csv(out_paths["decision"], DECISION_TABLE_COLUMNS, decision_table)

    fam_counts = {}
    for f in features:
        fam_counts[family_of[f]] = fam_counts.get(family_of[f], 0) + 1

    result = {
        "phase": PHASE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "phase3k_result_json": "research/output/phase3k_tiny_fundamental_ic_readiness.json",
            "phase3j_result_json":
                "research/output/phase3j_repaired_fundamental_price_alignment.json",
            "phase3h_feature_dictionary_csv":
                "research/output/phase3h_sec_fundamental_features/feature_dictionary.csv",
            "phase3e_ingestion_contract_json": "research/output/phase3e_ingestion_contract.json",
            "sector_map_csv": "research/input/phase2k_p_sector_map_current.csv",
            "price_history_csv": price_csv.replace("\\", "/"),
            "price_data_quality_json": PRICE_QUALITY_JSON.replace("\\", "/"),
            "price_survivorship_caveat_json": PRICE_SURVIVORSHIP_JSON.replace("\\", "/"),
        },
        "outputs_written": {
            "result_json": "research/output/phase3l_sec_universe_signal_gate.json",
            "company_identity_universe_csv":
                "research/output/phase3l_sec_universe_signal_gate/company_identity_universe.csv",
            "fundamentals_universe_csv":
                "research/output/phase3l_sec_universe_signal_gate/fundamentals_universe.csv",
            "feature_snapshot_universe_csv":
                "research/output/phase3l_sec_universe_signal_gate/feature_snapshot_universe.csv",
            "aligned_feature_price_panel_universe_csv":
                "research/output/phase3l_sec_universe_signal_gate/"
                "aligned_feature_price_panel_universe.csv",
            "data_quality_report_json":
                "research/output/phase3l_sec_universe_signal_gate/data_quality_report.json",
            "field_coverage_by_ticker_csv":
                "research/output/phase3l_sec_universe_signal_gate/field_coverage_by_ticker.csv",
            "feature_coverage_by_ticker_csv":
                "research/output/phase3l_sec_universe_signal_gate/feature_coverage_by_ticker.csv",
            "staleness_summary_csv":
                "research/output/phase3l_sec_universe_signal_gate/staleness_summary.csv",
            "leakage_checks_csv":
                "research/output/phase3l_sec_universe_signal_gate/leakage_checks.csv",
            "label_summary_by_horizon_csv":
                "research/output/phase3l_sec_universe_signal_gate/label_summary_by_horizon.csv",
            "feature_ic_summary_csv":
                "research/output/phase3l_sec_universe_signal_gate/feature_ic_summary.csv",
            "feature_family_ic_summary_csv":
                "research/output/phase3l_sec_universe_signal_gate/feature_family_ic_summary.csv",
            "horizon_readiness_summary_csv":
                "research/output/phase3l_sec_universe_signal_gate/horizon_readiness_summary.csv",
            "yearly_ic_summary_csv":
                "research/output/phase3l_sec_universe_signal_gate/yearly_ic_summary.csv",
            "sector_sanity_summary_csv":
                "research/output/phase3l_sec_universe_signal_gate/sector_sanity_summary.csv",
            "decision_table_csv":
                "research/output/phase3l_sec_universe_signal_gate/decision_table.csv",
            "raw_cache_dir": "research/output/phase3l_sec_universe_signal_gate/raw/",
        },
        "phase3k_summary": {
            "phase3k_confirmed": confirmed,
            "phase3k_recommendation": (phase3k or {}).get("recommendation", {}).get("recommendation"),
            "ingestion_contract_confirmed": confirmed_contract,
            "phase3j_recommendation": (phase3j or {}).get("recommendation", {}).get("recommendation"),
        },
        "universe_summary": universe_summary,
        "sec_access_summary": {
            "user_agent_declared": True,
            "allowed_hosts": list(ALLOWED_SEC_HOSTS),
            "min_request_interval_seconds": MIN_REQUEST_INTERVAL_S,
            "max_total_requests": MAX_TOTAL_REQUESTS,
            "sec_request_count": client.request_count,
            "network_request_count": client.network_requests,
            "used_cache_count": client.cache_hits,
            "cache_first": True,
            "network_used": network_used,
            "raw_files_written": sorted(set(raw_files)),
            "endpoints_used": [
                COMPANY_TICKERS_URL,
                SUBMISSIONS_URL_TEMPLATE.format(cik10="##########"),
                COMPANYFACTS_URL_TEMPLATE.format(cik10="##########"),
            ],
            "errors": client.errors,
        },
        "fundamentals_summary": {
            "fundamentals_rows": fund_total,
            "fields_attempted": list(ALL_ATTEMPTED_FIELDS),
            "fields_found": data_quality["fields_found"],
            "availability_datetime_coverage": data_quality["availability_datetime_coverage"],
            "point_in_time_usable_fraction": data_quality["point_in_time_usable_fraction"],
        },
        "feature_snapshot_summary": {
            "snapshot_rows": len(snapshots),
            "annual_snapshot_rows": sum(1 for s in snapshots if s["is_annual_snapshot"]),
            "quarterly_snapshot_rows": sum(1 for s in snapshots if s["is_quarterly_snapshot"]),
            "engineered_feature_count": len(EMITTED_FEATURES),
            "engineered_features": list(EMITTED_FEATURES),
            "feature_family_of": dict(family_of),
            "feature_family_counts": fam_counts,
            "snapshot_leakage_warning_count": snapshot_leakage_warnings,
            "non_null_feature_fraction": {f: _round(non_null_frac[f], 4) for f in features},
        },
        "alignment_summary": alignment_summary,
        "staleness_summary": {
            "applied_cap_days": DEFAULT_CAP_DAYS,
            "sensitivity_caps": list(SENSITIVITY_CAPS),
            "by_cap": staleness_rows,
        },
        "label_summary": {
            "horizons": HORIZONS,
            "excess_return_label": "forward_excess_return_vs_spy_{h}d",
            "rank_label": "forward_return_rank_by_date_{h}d",
            "label_coverage_by_horizon": {("%dd" % h): label_coverage[h] for h in HORIZONS},
            "labels_for_validation_only": True,
            "new_labels_created": True,
            "by_horizon": label_summary_rows,
        },
        "leakage_check_summary": {
            "leakage_failure_count": leakage_failures,
            "checks_evaluated": [name for name, _ in _LEAKAGE_CHECK_DEFS],
            "all_checks_passed": leakage_failures == 0,
            "by_check": leakage_check_rows,
        },
        "ic_methodology": {
            "ic_type": "cross-sectional daily Spearman rank information coefficient",
            "labels": ["forward_excess_return_vs_spy_{h}d", "forward_return_rank_by_date_{h}d"],
            "min_cross_section_per_date_partial": IC_MIN_CROSS_SECTION,
            "min_cross_section_per_date_full": DENSE_MIN_CROSS_SECTION,
            "grouping": "rows grouped by scoring_date; IC computed within each date, then "
                        "summarized across dates",
            "hit_rate_definition": "fraction of dates whose IC has the same sign as the mean IC",
            "ic_ir_definition": "mean IC divided by the sample standard deviation of per-date ICs",
            "bucket_diagnostic": "per date, rank by feature; top vs bottom quintile (>= %d obs) and "
                                 "top-%d vs bottom-%d; mean forward_excess_return_vs_spy spread"
                                 % (QUINTILE_MIN_OBS, TOPK, TOPK),
            "moderate_or_better_gate": {
                "abs_mean_rank_ic_at_least": MODERATE_ABS_IC,
                "date_count_at_least": MODERATE_MIN_DATES, "ic_hit_rate_at_least": MODERATE_MIN_HIT,
                "non_null_feature_fraction_at_least": MODERATE_MIN_NONNULL},
            "strong_gate": {
                "abs_mean_rank_ic_at_least": STRONG_ABS_IC, "date_count_at_least": STRONG_MIN_DATES,
                "ic_hit_rate_at_least": STRONG_MIN_HIT,
                "non_null_feature_fraction_at_least": STRONG_MIN_NONNULL,
                "bucket_spread_sign_matches_ic": True,
                "bucket_spread_hit_rate_at_least": STRONG_MIN_SPREAD_HIT},
            "temporal_breadth_gate": {
                "dense_cross_section_min_names": DENSE_MIN_CROSS_SECTION,
                "min_qualifying_dates_per_year": MIN_QUALIFYING_DATES_PER_YEAR,
                "full_min_distinct_years": FULL_MIN_YEARS,
                "partial_min_distinct_years": PARTIAL_MIN_YEARS},
            "no_model_fit": True,
            "survivorship_caveat": "current-as-of universe; daily overlapping windows; ICs are "
                                   "diagnostic only and claim no edge.",
        },
        "feature_ic_summary": _strip_private(feature_rows),
        "feature_family_summary": _strip_private(family_rows),
        "horizon_readiness_summary": _strip_private(horizon_rows),
        "yearly_stability_summary": {
            "best_features": best_features,
            "by_feature_horizon": yearly_summary,
            "by_year": yearly_rows,
        },
        "sector_sanity_summary": {"by_sector": sector_rows, "overall": sector_summary},
        "decision_summary": {
            "moderate_or_better_feature_count": metrics["moderate_feature_count"],
            "moderate_or_better_features": metrics["moderate_features"],
            "strong_feature_count": metrics["strong_feature_count"],
            "strong_features": metrics["strong_features"],
            "stable_family_count": metrics["stable_family_count"],
            "any_horizon_ready_for_research_model": metrics["any_horizon_ready"],
            "max_absolute_mean_ic": metrics["max_abs_ic"],
            "max_distinct_dense_ic_years": metrics["max_distinct_ic_years"],
            "single_regime_small_sample": metrics["single_regime"],
            "coverage_ok_for_decision": metrics["coverage_ok_for_decision"],
            "label_coverage_ok": metrics["label_coverage_ok"],
            "decision_table": decision_table,
        },
        "data_quality_report": data_quality,
        "source_limitations": list(_SOURCE_LIMITATIONS),
        "earnings_revisions_gap": {
            "sec_provides_earnings_consensus": False,
            "sec_provides_analyst_estimate_revisions": False,
            "gap_description": "SEC as-reported fundamentals provide no forward analyst consensus or "
                               "estimate revisions; a separate provider must be selected before "
                               "surprise / revision-momentum features can be built.",
            "provider_selection_required": True,
        },
        "recommendation": build_recommendation(recommendation, metrics),
        "interpretation": build_interpretation(recommendation, metrics, network_used),
        "recommended_next_phase": build_recommended_next_phase(recommendation),
    }
    result.update(build_safety_flags(recommendation, network_used))

    _dump_json(result_json_path, result)
    return result


def main():
    result = run()
    rec = result["recommendation"]
    nxt = result["recommended_next_phase"]
    us = result["universe_summary"]
    al = result["alignment_summary"]
    ds = result["decision_summary"]
    print("Phase %s - Full SEC Universe Expansion + End-to-End Fundamental Signal Gate"
          % result["phase"])
    print("  phase3k confirmed        : %s"
          % result["phase3k_summary"]["phase3k_confirmed"]["all_confirmed"])
    print("  universe tickers         : %s" % us["universe_ticker_count"])
    print("  processed tickers        : %s" % us["processed_ticker_count"])
    print("  network used             : %s (reqs %s, cache %s)"
          % (result["network_used"], result["sec_access_summary"]["network_request_count"],
             result["sec_access_summary"]["used_cache_count"]))
    print("  aligned tickers / rows   : %s / %s"
          % (al["aligned_ticker_count"], al["aligned_rows"]))
    print("  aligned date span        : %s -> %s"
          % (al["aligned_start_date"], al["aligned_end_date"]))
    print("  leakage failures         : %s"
          % result["leakage_check_summary"]["leakage_failure_count"])
    print("  label coverage           : %s" % result["label_summary"]["label_coverage_by_horizon"])
    print("  --- horizon readiness ---")
    for h in result["horizon_readiness_summary"]:
        print("    %-4s | cov %s | avg_cs %s | ic_dates %s | dense %s | years %s | best %s "
              "(|IC| %s) | mod %s | strong %s | %s"
              % (h["horizon"], h["label_coverage"], h["avg_daily_cross_section_size"],
                 h["valid_ic_date_count"], h["dense_ic_date_count"], h["distinct_ic_years"],
                 h["best_feature"], h["best_feature_abs_mean_ic"], h["moderate_or_better_count"],
                 h["strong_count"], h["horizon_readiness"]))
    print("  moderate-or-better feats : %s %s"
          % (ds["moderate_or_better_feature_count"], ds["moderate_or_better_features"]))
    print("  strong feats             : %s %s"
          % (ds["strong_feature_count"], ds["strong_features"]))
    print("  stable families          : %s" % ds["stable_family_count"])
    print("  max |mean IC|            : %s" % ds["max_absolute_mean_ic"])
    print("  max distinct dense years : %s" % ds["max_distinct_dense_ic_years"])
    print("  model trained            : %s" % result["model_trained"])
    print("  recommendation           : %s" % rec["recommendation"])
    print("  research model next      : %s" % rec["research_model_allowed_next"])
    print("  recommended next phase   : %s - %s" % (nxt["phase"], nxt["title"]))
    return result


if __name__ == "__main__":
    main()
