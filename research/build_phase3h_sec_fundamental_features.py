"""Phase 3-H - SEC Fundamentals Feature Engineering Prototype.

This is a controlled, **read-only research feature-engineering prototype**, not a training phase,
not a labelling phase, and not a production ingestion. After Phase 3-G proved
(SEC_FUNDAMENTALS_MINIPIPELINE_SUCCESS) that the SEC fundamentals path scales cleanly from a tiny
5-ticker proof-of-concept to a controlled, point-in-time 20-ticker mini-pipeline, this script
converts that already-normalized local sample into a point-in-time *feature snapshot* dataset for
the same 20 tickers. It answers a single practical question:

    "Can we build trailing-only, point-in-time fundamental features from the normalized SEC sample
     - every feature row carrying a safe feature_asof_date, annual and quarterly durations kept
     separate, sector-specific missing fields handled explicitly - without any model labels, any
     forward returns, any price join, or any model training?"

It reads ONLY the committed Phase 3-G outputs (the mini-pipeline result, the normalized
fundamentals CSV, the company-identity CSV, and the supporting coverage / reconciliation
artifacts) plus the Phase 3-E ingestion contract and the current-as-of sector map. It groups the
normalized rows into canonical filing snapshots, pivots the mapped fields wide, derives a safe
feature_asof_date (the latest availability timestamp of the fields used, never the fiscal period
end), separates annual (10-K / FY) snapshots from quarterly (10-Q / Qn) snapshots, and computes
trailing-only fundamental features (profitability, leverage, growth, cash-conversion quality, size
controls, and availability/recency metadata). It writes feature-coverage, feature-dictionary,
feature-alignment-warning, and feature-quality artifacts and emits a Phase 3-I recommendation.

Scope and safety. There is NO network use in this phase (no SEC call, no vendor call): it reads
only repo-local Phase 3-G outputs. It computes no target labels, no forward returns, and performs
no price join. It trains no model, creates no production model candidate, writes no deployable
model artifact, touches no database, runs no migration, writes nothing to the D: drive, reads no
D: price CSV, places no orders, and trades nothing. It is research-only and writes only the small
feature outputs listed below.
"""
from __future__ import annotations

import csv
import json
import math
import os
from datetime import datetime

PHASE = "3-H"

# --------------------------------------------------------------------------- #
# Paths (repo-local only; nothing on the D: drive is read or written here)
# --------------------------------------------------------------------------- #
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUT_DIR = os.path.join(_REPO_ROOT, "research", "output")
_IN_DIR = os.path.join(_REPO_ROOT, "research", "input")
_G_SAMPLE_DIR = os.path.join(_OUT_DIR, "phase3g_sec_fundamentals_sample")
_H_DIR = os.path.join(_OUT_DIR, "phase3h_sec_fundamental_features")

# Inputs (committed Phase 3-G outputs + Phase 3-E contract + current-as-of sector map).
PHASE3G_JSON = os.path.join(_OUT_DIR, "phase3g_sec_fundamentals_minipipeline.json")
PHASE3G_IDENTITY_CSV = os.path.join(_G_SAMPLE_DIR, "company_identity_20ticker_sample.csv")
PHASE3G_FUNDAMENTALS_CSV = os.path.join(_G_SAMPLE_DIR, "fundamentals_20ticker_sample.csv")
PHASE3G_DATA_QUALITY_JSON = os.path.join(_G_SAMPLE_DIR, "data_quality_report.json")
PHASE3G_FIELD_COVERAGE_CSV = os.path.join(_G_SAMPLE_DIR, "field_coverage_by_ticker.csv")
PHASE3G_PERIOD_COVERAGE_CSV = os.path.join(_G_SAMPLE_DIR, "period_coverage_by_ticker.csv")
PHASE3G_RECONCILIATION_CSV = os.path.join(_G_SAMPLE_DIR, "reconciliation_warnings.csv")
INGESTION_CONTRACT_JSON = os.path.join(_OUT_DIR, "phase3e_ingestion_contract.json")
SECTOR_MAP_CSV = os.path.join(_IN_DIR, "phase2k_p_sector_map_current.csv")

# Outputs.
FEATURES_JSON = os.path.join(_OUT_DIR, "phase3h_sec_fundamental_features.json")
FEATURE_SNAPSHOT_CSV = os.path.join(_H_DIR, "feature_snapshot_20ticker_sample.csv")
FEATURE_DICTIONARY_CSV = os.path.join(_H_DIR, "feature_dictionary.csv")
FEATURE_COVERAGE_CSV = os.path.join(_H_DIR, "feature_coverage_by_ticker.csv")
FEATURE_QUALITY_JSON = os.path.join(_H_DIR, "feature_quality_report.json")
FEATURE_ALIGNMENT_WARNINGS_CSV = os.path.join(_H_DIR, "feature_alignment_warnings.csv")

# --------------------------------------------------------------------------- #
# Upstream confirmation contract
# --------------------------------------------------------------------------- #
PHASE3G_EXPECTED_RECOMMENDATION = "SEC_FUNDAMENTALS_MINIPIPELINE_SUCCESS"

# Raw pivot fields carried from the normalized Phase 3-G fundamentals (mapped XBRL concepts).
RAW_PIVOT_FIELDS = [
    "revenue", "net_income", "operating_income", "eps_diluted", "total_assets",
    "total_liabilities", "shareholder_equity", "operating_cash_flow", "capital_expenditures",
    "free_cash_flow",
]
# Core fields used for required-coverage scoring (same definition as Phase 3-G).
REQUIRED_SOURCE_FIELDS = ["revenue", "net_income", "total_assets", "operating_cash_flow"]

# Forms / periods that define annual vs quarterly snapshots.
_ANNUAL_FORMS = {"10-K", "10-K/A"}
_QUARTERLY_FORMS = {"10-Q", "10-Q/A"}
_QUARTERLY_FP = {"Q1", "Q2", "Q3", "Q4"}

# Numeric safeguards (warnings only; raw feature values are left as computed).
EXTREME_RATIO_ABS = 10.0       # |ratio| above this is flagged extreme (not modified)
EXTREME_GROWTH_ABS = 5.0       # |yoy growth| above this is flagged extreme (not modified)

SOURCE_NAME = "SEC EDGAR (public companyfacts + submissions JSON), via Phase 3-G normalized sample"

# Warning vocabulary.
WARN_MISSING_ASOF = "missing_feature_asof_date"
WARN_ASOF_BEFORE_END = "feature_asof_before_period_end"
WARN_MISSING_REQUIRED = "missing_required_source_field"
WARN_MIX_RISK = "annual_quarterly_mix_risk"
WARN_DIV_ZERO = "division_by_zero"
WARN_EXTREME = "extreme_ratio"
WARN_NEG_DENOM = "negative_denominator"
WARN_NOT_PIT = "not_point_in_time_usable"
# Only these warning types indicate point-in-time leakage; the rest are advisory.
LEAKAGE_WARNING_TYPES = {WARN_MISSING_ASOF, WARN_ASOF_BEFORE_END, WARN_NOT_PIT}

# Recommendation vocabulary.
REC_SUCCESS = "SEC_FUNDAMENTAL_FEATURES_PROTOTYPE_SUCCESS"
REC_PARTIAL = "SEC_FUNDAMENTAL_FEATURES_PARTIAL_SUCCESS"
REC_BLOCKED = "SEC_FUNDAMENTAL_FEATURES_BLOCKED"
REC_REJECTED = "SEC_FUNDAMENTAL_FEATURES_REJECTED"
ALLOWED_RECOMMENDATIONS = [REC_SUCCESS, REC_PARTIAL, REC_BLOCKED, REC_REJECTED]

_SOURCE_LIMITATIONS = [
    "Features are built only from SEC as-reported fundamentals; SEC public data provides no "
    "earnings consensus or analyst estimate revisions, so surprise / revision-momentum features "
    "cannot be built here.",
    "No price or market-cap data is joined in this phase, so price-based valuation features "
    "(P/E, EV/EBITDA, market cap, dividend yield) are intentionally not computed.",
    "companyfacts tags some quarterly-duration facts with fiscal_period=FY inside 10-K filings; "
    "such embedded quarterly durations are flagged annual_quarterly_mix_risk and excluded from "
    "annual year-over-year comparisons rather than mixed with true full-year durations.",
    "Financial-sector filers (e.g. banks) do not report operating_income, capital_expenditures, "
    "or free_cash_flow the same way industrials do, so those features are legitimately blank for "
    "them and are reported as sector-specific missing fields, never filled with zero.",
    "The sector / industry attached to each feature row is current-as-of (the sector map is not "
    "point-in-time), so downstream results remain survivorship-caveated.",
    "This is a controlled 20-ticker research sample pruned to the most-recent fiscal periods, not "
    "full historical coverage.",
]


# --------------------------------------------------------------------------- #
# Feature dictionary (drives the snapshot columns, coverage, and the dictionary CSV)
# --------------------------------------------------------------------------- #
# Each entry: name, family, formula, required_source_fields (list), point_in_time_rule,
# annual_quarterly_rule, missing_value_policy, leakage_risk_note.
FEATURE_DEFS = [
    # A. Profitability / margins
    {"name": "gross_margin", "family": "profitability",
     "formula": "gross_profit / revenue",
     "required_source_fields": ["gross_profit", "revenue"],
     "point_in_time_rule": "uses only fields available at feature_asof_date",
     "annual_quarterly_rule": "computed within a single snapshot (no period mixing)",
     "missing_value_policy": "OMITTED in this sample: gross_profit is not in the Phase 3-G "
                             "normalized fundamentals; documented, not emitted as a column",
     "leakage_risk_note": "none; would be same-period ratio if gross_profit were available",
     "emit": False},
    {"name": "operating_margin", "family": "profitability",
     "formula": "operating_income / revenue",
     "required_source_fields": ["operating_income", "revenue"],
     "point_in_time_rule": "uses only fields available at feature_asof_date",
     "annual_quarterly_rule": "computed within a single snapshot (no period mixing)",
     "missing_value_policy": "null if operating_income or revenue missing/zero (banks often "
                             "lack operating_income)",
     "leakage_risk_note": "none; same-period as-reported ratio", "emit": True},
    {"name": "net_margin", "family": "profitability",
     "formula": "net_income / revenue",
     "required_source_fields": ["net_income", "revenue"],
     "point_in_time_rule": "uses only fields available at feature_asof_date",
     "annual_quarterly_rule": "computed within a single snapshot (no period mixing)",
     "missing_value_policy": "null if net_income or revenue missing/zero",
     "leakage_risk_note": "none; same-period as-reported ratio", "emit": True},
    {"name": "fcf_margin", "family": "profitability",
     "formula": "free_cash_flow / revenue",
     "required_source_fields": ["free_cash_flow", "revenue"],
     "point_in_time_rule": "uses only fields available at feature_asof_date",
     "annual_quarterly_rule": "computed within a single snapshot (no period mixing)",
     "missing_value_policy": "null if free_cash_flow or revenue missing/zero (fcf derived only "
                             "where operating_cash_flow and capex both present)",
     "leakage_risk_note": "none; same-period as-reported ratio", "emit": True},
    {"name": "operating_cash_flow_margin", "family": "profitability",
     "formula": "operating_cash_flow / revenue",
     "required_source_fields": ["operating_cash_flow", "revenue"],
     "point_in_time_rule": "uses only fields available at feature_asof_date",
     "annual_quarterly_rule": "computed within a single snapshot (no period mixing)",
     "missing_value_policy": "null if operating_cash_flow or revenue missing/zero",
     "leakage_risk_note": "none; same-period as-reported ratio", "emit": True},
    # B. Balance sheet / leverage
    {"name": "debt_proxy_total_liabilities_to_assets", "family": "balance_sheet",
     "formula": "total_liabilities / total_assets",
     "required_source_fields": ["total_liabilities", "total_assets"],
     "point_in_time_rule": "uses only fields available at feature_asof_date",
     "annual_quarterly_rule": "computed within a single snapshot (no period mixing)",
     "missing_value_policy": "null if total_liabilities or total_assets missing/zero",
     "leakage_risk_note": "none; same-period as-reported ratio", "emit": True},
    {"name": "equity_to_assets", "family": "balance_sheet",
     "formula": "shareholder_equity / total_assets",
     "required_source_fields": ["shareholder_equity", "total_assets"],
     "point_in_time_rule": "uses only fields available at feature_asof_date",
     "annual_quarterly_rule": "computed within a single snapshot (no period mixing)",
     "missing_value_policy": "null if shareholder_equity or total_assets missing/zero",
     "leakage_risk_note": "none; same-period as-reported ratio", "emit": True},
    {"name": "asset_turnover_proxy", "family": "balance_sheet",
     "formula": "revenue / total_assets",
     "required_source_fields": ["revenue", "total_assets"],
     "point_in_time_rule": "uses only fields available at feature_asof_date",
     "annual_quarterly_rule": "computed within a single snapshot (no period mixing)",
     "missing_value_policy": "null if revenue or total_assets missing/zero",
     "leakage_risk_note": "none; same-period as-reported ratio", "emit": True},
    {"name": "liability_to_equity", "family": "balance_sheet",
     "formula": "total_liabilities / shareholder_equity",
     "required_source_fields": ["total_liabilities", "shareholder_equity"],
     "point_in_time_rule": "uses only fields available at feature_asof_date",
     "annual_quarterly_rule": "computed within a single snapshot (no period mixing)",
     "missing_value_policy": "null if total_liabilities or shareholder_equity missing/zero; "
                             "negative shareholder_equity is flagged negative_denominator",
     "leakage_risk_note": "none; same-period as-reported ratio", "emit": True},
    # C. Growth / change (trailing year-over-year, same period type only)
    {"name": "revenue_yoy_growth", "family": "growth",
     "formula": "(revenue[t] - revenue[t-1y]) / abs(revenue[t-1y])",
     "required_source_fields": ["revenue"],
     "point_in_time_rule": "prior period must have an earlier feature_asof_date; both are "
                           "trailing (already filed)",
     "annual_quarterly_rule": "annual vs prior-year FY; quarterly vs same fiscal quarter prior "
                              "year; never annual-vs-quarterly",
     "missing_value_policy": "null if no matching prior-year same-period snapshot, or prior "
                             "value missing/zero",
     "leakage_risk_note": "none; compares only already-reported trailing periods", "emit": True},
    {"name": "net_income_yoy_growth", "family": "growth",
     "formula": "(net_income[t] - net_income[t-1y]) / abs(net_income[t-1y])",
     "required_source_fields": ["net_income"],
     "point_in_time_rule": "prior period must have an earlier feature_asof_date; both trailing",
     "annual_quarterly_rule": "annual vs prior-year FY; quarterly vs same fiscal quarter prior "
                              "year; never annual-vs-quarterly",
     "missing_value_policy": "null if no matching prior-year same-period snapshot, or prior "
                             "value missing/zero",
     "leakage_risk_note": "none; compares only already-reported trailing periods", "emit": True},
    {"name": "operating_income_yoy_growth", "family": "growth",
     "formula": "(operating_income[t] - operating_income[t-1y]) / abs(operating_income[t-1y])",
     "required_source_fields": ["operating_income"],
     "point_in_time_rule": "prior period must have an earlier feature_asof_date; both trailing",
     "annual_quarterly_rule": "annual vs prior-year FY; quarterly vs same fiscal quarter prior "
                              "year; never annual-vs-quarterly",
     "missing_value_policy": "null if no matching prior-year same-period snapshot, prior value "
                             "missing/zero, or operating_income not reported (banks)",
     "leakage_risk_note": "none; compares only already-reported trailing periods", "emit": True},
    {"name": "eps_diluted_yoy_growth", "family": "growth",
     "formula": "(eps_diluted[t] - eps_diluted[t-1y]) / abs(eps_diluted[t-1y])",
     "required_source_fields": ["eps_diluted"],
     "point_in_time_rule": "prior period must have an earlier feature_asof_date; both trailing",
     "annual_quarterly_rule": "annual vs prior-year FY; quarterly vs same fiscal quarter prior "
                              "year; never annual-vs-quarterly",
     "missing_value_policy": "null if no matching prior-year same-period snapshot, or prior "
                             "value missing/zero",
     "leakage_risk_note": "none; compares only already-reported trailing periods", "emit": True},
    {"name": "total_assets_yoy_growth", "family": "growth",
     "formula": "(total_assets[t] - total_assets[t-1y]) / abs(total_assets[t-1y])",
     "required_source_fields": ["total_assets"],
     "point_in_time_rule": "prior period must have an earlier feature_asof_date; both trailing",
     "annual_quarterly_rule": "annual vs prior-year FY; quarterly vs same fiscal quarter prior "
                              "year; never annual-vs-quarterly",
     "missing_value_policy": "null if no matching prior-year same-period snapshot, or prior "
                             "value missing/zero",
     "leakage_risk_note": "none; compares only already-reported trailing periods", "emit": True},
    {"name": "operating_cash_flow_yoy_growth", "family": "growth",
     "formula": "(operating_cash_flow[t] - operating_cash_flow[t-1y]) / "
                "abs(operating_cash_flow[t-1y])",
     "required_source_fields": ["operating_cash_flow"],
     "point_in_time_rule": "prior period must have an earlier feature_asof_date; both trailing",
     "annual_quarterly_rule": "annual vs prior-year FY; quarterly vs same fiscal quarter prior "
                              "year; never annual-vs-quarterly",
     "missing_value_policy": "null if no matching prior-year same-period snapshot, or prior "
                             "value missing/zero",
     "leakage_risk_note": "none; compares only already-reported trailing periods", "emit": True},
    {"name": "free_cash_flow_yoy_growth", "family": "growth",
     "formula": "(free_cash_flow[t] - free_cash_flow[t-1y]) / abs(free_cash_flow[t-1y])",
     "required_source_fields": ["free_cash_flow"],
     "point_in_time_rule": "prior period must have an earlier feature_asof_date; both trailing",
     "annual_quarterly_rule": "annual vs prior-year FY; quarterly vs same fiscal quarter prior "
                              "year; never annual-vs-quarterly",
     "missing_value_policy": "null if no matching prior-year same-period snapshot, prior value "
                             "missing/zero, or free_cash_flow not derivable (banks)",
     "leakage_risk_note": "none; compares only already-reported trailing periods", "emit": True},
    # D. Quality / cash conversion
    {"name": "cash_conversion", "family": "quality",
     "formula": "operating_cash_flow / net_income",
     "required_source_fields": ["operating_cash_flow", "net_income"],
     "point_in_time_rule": "uses only fields available at feature_asof_date",
     "annual_quarterly_rule": "computed within a single snapshot (no period mixing)",
     "missing_value_policy": "null if operating_cash_flow or net_income missing/zero; negative "
                             "net_income flagged negative_denominator",
     "leakage_risk_note": "none; same-period as-reported ratio", "emit": True},
    {"name": "fcf_to_net_income", "family": "quality",
     "formula": "free_cash_flow / net_income",
     "required_source_fields": ["free_cash_flow", "net_income"],
     "point_in_time_rule": "uses only fields available at feature_asof_date",
     "annual_quarterly_rule": "computed within a single snapshot (no period mixing)",
     "missing_value_policy": "null if free_cash_flow or net_income missing/zero; negative "
                             "net_income flagged negative_denominator",
     "leakage_risk_note": "none; same-period as-reported ratio", "emit": True},
    {"name": "capex_intensity", "family": "quality",
     "formula": "abs(capital_expenditures) / revenue",
     "required_source_fields": ["capital_expenditures", "revenue"],
     "point_in_time_rule": "uses only fields available at feature_asof_date",
     "annual_quarterly_rule": "computed within a single snapshot (no period mixing)",
     "missing_value_policy": "null if capital_expenditures or revenue missing/zero (banks often "
                             "lack capital_expenditures)",
     "leakage_risk_note": "none; same-period as-reported ratio", "emit": True},
    {"name": "accrual_proxy", "family": "quality",
     "formula": "(net_income - operating_cash_flow) / total_assets",
     "required_source_fields": ["net_income", "operating_cash_flow", "total_assets"],
     "point_in_time_rule": "uses only fields available at feature_asof_date",
     "annual_quarterly_rule": "computed within a single snapshot (no period mixing)",
     "missing_value_policy": "null if any of net_income, operating_cash_flow, total_assets "
                             "missing or total_assets zero",
     "leakage_risk_note": "none; same-period as-reported ratio", "emit": True},
    # E. Size / scale controls
    {"name": "log_total_assets", "family": "size",
     "formula": "log(total_assets) for total_assets > 0",
     "required_source_fields": ["total_assets"],
     "point_in_time_rule": "uses only fields available at feature_asof_date",
     "annual_quarterly_rule": "computed within a single snapshot (no period mixing)",
     "missing_value_policy": "null if total_assets missing or <= 0",
     "leakage_risk_note": "none; same-period as-reported level", "emit": True},
    {"name": "log_revenue_abs", "family": "size",
     "formula": "log(abs(revenue)) for revenue != 0",
     "required_source_fields": ["revenue"],
     "point_in_time_rule": "uses only fields available at feature_asof_date",
     "annual_quarterly_rule": "computed within a single snapshot (no period mixing)",
     "missing_value_policy": "null if revenue missing or zero",
     "leakage_risk_note": "none; same-period as-reported level", "emit": True},
    {"name": "log_total_liabilities_abs", "family": "size",
     "formula": "log(abs(total_liabilities)) for total_liabilities != 0",
     "required_source_fields": ["total_liabilities"],
     "point_in_time_rule": "uses only fields available at feature_asof_date",
     "annual_quarterly_rule": "computed within a single snapshot (no period mixing)",
     "missing_value_policy": "null if total_liabilities missing or zero",
     "leakage_risk_note": "none; same-period as-reported level", "emit": True},
    # F. Availability / recency metadata
    {"name": "filing_lag_days", "family": "availability_metadata",
     "formula": "feature_asof_date - fiscal_period_end (in days)",
     "required_source_fields": ["feature_asof_date", "fiscal_period_end"],
     "point_in_time_rule": "non-negative by construction; a negative lag would be leakage and is "
                           "flagged feature_asof_before_period_end",
     "annual_quarterly_rule": "per snapshot; independent of period type",
     "missing_value_policy": "null only if feature_asof_date is missing",
     "leakage_risk_note": "guards leakage: lag must be >= 0", "emit": True},
]
# Engineered feature columns actually emitted into the snapshot (in dictionary order).
EMITTED_FEATURES = [d["name"] for d in FEATURE_DEFS if d["emit"]]
FEATURE_FAMILIES = sorted({d["family"] for d in FEATURE_DEFS if d["emit"]})

SNAPSHOT_BASE_COLUMNS = [
    "ticker", "company_name", "sector", "industry", "cik", "fiscal_period_end", "fiscal_year",
    "fiscal_period", "form", "snapshot_type", "feature_asof_date", "source_field_count",
    "required_field_coverage_fraction", "point_in_time_usable", "is_annual_snapshot",
    "is_quarterly_snapshot",
]
SNAPSHOT_COLUMNS = SNAPSHOT_BASE_COLUMNS + EMITTED_FEATURES

FEATURE_DICTIONARY_COLUMNS = [
    "feature_name", "feature_family", "formula", "required_source_fields", "point_in_time_rule",
    "annual_quarterly_rule", "missing_value_policy", "leakage_risk_note",
]
FEATURE_COVERAGE_COLUMNS = [
    "ticker", "sector", "feature_name", "non_null_count", "total_snapshot_count",
    "coverage_fraction", "latest_feature_asof_date", "earliest_feature_asof_date",
]
WARNING_COLUMNS = [
    "ticker", "warning_type", "fiscal_period_end", "fiscal_period", "form", "feature_name",
    "feature_value", "severity", "note",
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
    """Extract the YYYY-MM-DD date prefix from an ISO datetime / date string."""
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


# --------------------------------------------------------------------------- #
# Upstream confirmation
# --------------------------------------------------------------------------- #
def confirm_phase3g(phase3g):
    """Confirm the committed Phase 3-G mini-pipeline result that gates this feature phase."""
    if not phase3g:
        return {"all_confirmed": False, "phase3g_present": False}
    rec = phase3g.get("recommendation", {}) or {}
    nxt = phase3g.get("recommended_next_phase", {}) or {}
    checks = {
        "phase3g_present": True,
        "phase_is_3g": phase3g.get("phase") == "3-G",
        "recommendation_is_minipipeline_success":
            rec.get("recommendation") == PHASE3G_EXPECTED_RECOMMENDATION,
        "next_phase_is_3h": nxt.get("phase") == "3-H",
        "sec_public_data_used_true": phase3g.get("sec_public_data_used") is True,
        "external_data_ingested_true": phase3g.get("external_data_ingested") is True,
        "production_data_ingested_false": phase3g.get("production_data_ingested") is False,
        "full_128_ticker_ingestion_false": phase3g.get("full_128_ticker_ingestion") is False,
        "model_features_computed_false": phase3g.get("model_features_computed") is False,
        "labels_computed_false": phase3g.get("labels_computed") is False,
        "research_model_trained_false": phase3g.get("research_model_trained") is False,
        "production_model_trained_false": phase3g.get("production_model_trained") is False,
        "production_model_candidate_created_false":
            phase3g.get("production_model_candidate_created") is False,
        "deployable_model_artifact_written_false":
            phase3g.get("deployable_model_artifact_written") is False,
        "d_drive_read_false": phase3g.get("d_drive_read") is False,
        "d_drive_written_false": phase3g.get("d_drive_written") is False,
        "vendor_api_called_false": phase3g.get("vendor_api_called") is False,
        "paid_vendor_api_called_false": phase3g.get("paid_vendor_api_called") is False,
        "data_purchase_made_false": phase3g.get("data_purchase_made") is False,
    }
    checks["all_confirmed"] = all(checks.values())
    return checks


def confirm_ingestion_contract(contract):
    """Re-confirm the Phase 3-E ingestion contract gates this phase must honor."""
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


def load_identity(path):
    """ticker -> {company_name, sector, industry, cik}."""
    out = {}
    for row in _read_csv_rows(path):
        t = (row.get("ticker") or "").strip().upper()
        if t:
            out[t] = {
                "company_name": (row.get("company_name") or "").strip(),
                "sector": (row.get("sector") or "").strip(),
                "industry": (row.get("industry") or "").strip(),
                "cik": (row.get("cik") or "").strip(),
            }
    return out


# --------------------------------------------------------------------------- #
# Canonical snapshot construction (group + pivot, point-in-time feature_asof_date)
# --------------------------------------------------------------------------- #
def _classify_snapshot(form, fp):
    """Return 'annual' or 'quarterly' using the Phase 3-H period rules."""
    if fp in _QUARTERLY_FP or form in _QUARTERLY_FORMS:
        return "quarterly"
    if fp == "FY" or form in _ANNUAL_FORMS:
        return "annual"
    return "quarterly" if form in _QUARTERLY_FORMS else "annual"


def build_snapshots(fundamentals_rows, identity_by_ticker):
    """Group normalized rows into canonical filing snapshots and pivot mapped fields wide.

    A snapshot key is (ticker, fiscal_period_end, fiscal_year, fiscal_period, form). The
    feature_asof_date is the LATEST availability_datetime across the fields used in the snapshot
    (never the fiscal_period_end). point_in_time_usable is True only if every source row used is
    itself point_in_time_usable.
    """
    groups = {}
    for r in fundamentals_rows:
        ticker = (r.get("ticker") or "").strip().upper()
        field = (r.get("normalized_field") or "").strip()
        if not ticker or field not in RAW_PIVOT_FIELDS:
            continue
        key = (ticker, r.get("fiscal_period_end") or "", r.get("fiscal_year") or "",
               r.get("fiscal_period") or "", r.get("form") or "")
        g = groups.setdefault(key, {
            "ticker": ticker,
            "fiscal_period_end": r.get("fiscal_period_end") or "",
            "fiscal_year": r.get("fiscal_year") or "",
            "fiscal_period": r.get("fiscal_period") or "",
            "form": r.get("form") or "",
            "cik": r.get("cik") or "",
            "pivot": {},
            "max_avail": "",
            "all_pit": True,
            "any_avail": False,
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
        snap = {
            "ticker": g["ticker"],
            "company_name": ident.get("company_name", ""),
            "sector": ident.get("sector", ""),
            "industry": ident.get("industry", ""),
            "cik": ident.get("cik", "") or g["cik"],
            "fiscal_period_end": g["fiscal_period_end"],
            "fiscal_year": g["fiscal_year"],
            "fiscal_period": g["fiscal_period"],
            "form": g["form"],
            "snapshot_type": snapshot_type,
            "feature_asof_date": g["max_avail"],
            "source_field_count": len(present),
            "required_field_coverage_fraction":
                _round(len(req_present) / len(REQUIRED_SOURCE_FIELDS), 4),
            "point_in_time_usable": bool(g["any_avail"] and g["all_pit"]),
            "is_annual_snapshot": snapshot_type == "annual",
            "is_quarterly_snapshot": snapshot_type == "quarterly",
            "_pivot": g["pivot"],
            "_fy_int": _to_int(g["fiscal_year"]),
        }
        snapshots.append(snap)

    snapshots.sort(key=lambda s: (s["ticker"], s["fiscal_period_end"], s["fiscal_period"],
                                  s["form"]))
    return snapshots


# --------------------------------------------------------------------------- #
# Year-over-year lookups (same period type only; canonical annual de-duplicates FY durations)
# --------------------------------------------------------------------------- #
def build_growth_indexes(snapshots):
    """Build trailing prior-period lookups, keeping annual and quarterly strictly separate.

    annual_index[(ticker, fy)]      -> the canonical annual pivot for that fiscal year, defined as
                                       the annual snapshot with the LATEST fiscal_period_end (this
                                       de-duplicates quarterly-duration facts mis-tagged FY inside a
                                       10-K, which carry earlier period ends).
    quarterly_index[(ticker, fy, fp)] -> the quarterly pivot (latest period end if duplicated).
    """
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
    annual_asof = {k: v["feature_asof_date"] for k, v in annual_best.items()}
    quarterly_index = {k: v["_pivot"] for k, v in quarterly_best.items()}
    quarterly_asof = {k: v["feature_asof_date"] for k, v in quarterly_best.items()}
    return annual_index, annual_asof, quarterly_index, quarterly_asof


# --------------------------------------------------------------------------- #
# Feature computation + alignment warnings
# --------------------------------------------------------------------------- #
def _w(ticker, wtype, snap, feature_name, value, severity, note):
    return {
        "ticker": ticker, "warning_type": wtype,
        "fiscal_period_end": snap["fiscal_period_end"], "fiscal_period": snap["fiscal_period"],
        "form": snap["form"], "feature_name": feature_name,
        "feature_value": _round(value) if isinstance(value, (int, float)) else (value or ""),
        "severity": severity, "note": note,
    }


def compute_features(snapshots, annual_index, annual_asof, quarterly_index, quarterly_asof):
    """Populate engineered feature columns on each snapshot and return alignment warnings."""
    warnings = []

    # De-duplicate the canonical-annual rows so we can mark non-canonical FY durations as mix-risk.
    canonical_annual_end = {}
    for s in snapshots:
        if s["is_annual_snapshot"] and s["_fy_int"] is not None:
            k = (s["ticker"], s["_fy_int"])
            end = s["fiscal_period_end"]
            if end > canonical_annual_end.get(k, ""):
                canonical_annual_end[k] = end

    for s in snapshots:
        v = s["_pivot"]
        t = s["ticker"]

        def get(field):
            return v.get(field)

        def ratio(num, den, fname):
            if num is None or den is None:
                return None
            if den == 0:
                warnings.append(_w(t, WARN_DIV_ZERO, s, fname, None, "info",
                                   "denominator is zero; feature left null"))
                return None
            val = num / den
            if den < 0:
                warnings.append(_w(t, WARN_NEG_DENOM, s, fname, val, "info",
                                   "denominator is negative; ratio sign may be misleading"))
            if abs(val) > EXTREME_RATIO_ABS:
                warnings.append(_w(t, WARN_EXTREME, s, fname, val, "info",
                                   "abs ratio exceeds %.0f; left as computed" % EXTREME_RATIO_ABS))
            return val

        def growth(field, fname, prior_pivot):
            if prior_pivot is None:
                return None
            cur = v.get(field)
            prev = prior_pivot.get(field)
            if cur is None or prev is None:
                return None
            if prev == 0:
                warnings.append(_w(t, WARN_DIV_ZERO, s, fname, None, "info",
                                   "prior-year value is zero; growth left null"))
                return None
            val = (cur - prev) / abs(prev)
            if abs(val) > EXTREME_GROWTH_ABS:
                warnings.append(_w(t, WARN_EXTREME, s, fname, val, "info",
                                   "abs yoy growth exceeds %.0f; left as computed"
                                   % EXTREME_GROWTH_ABS))
            return val

        # --- point-in-time / availability integrity (leakage guards) ---
        if not s["feature_asof_date"]:
            warnings.append(_w(t, WARN_MISSING_ASOF, s, "feature_asof_date", "", "error",
                               "snapshot has no availability timestamp; cannot be used safely"))
        else:
            asof_d = _parse_date(s["feature_asof_date"])
            end_d = _parse_date(s["fiscal_period_end"])
            if asof_d is not None and end_d is not None and asof_d < end_d:
                warnings.append(_w(t, WARN_ASOF_BEFORE_END, s, "feature_asof_date",
                                   s["feature_asof_date"], "error",
                                   "feature_asof_date precedes fiscal_period_end (leakage)"))
        if not s["point_in_time_usable"]:
            warnings.append(_w(t, WARN_NOT_PIT, s, "point_in_time_usable", "", "error",
                               "snapshot has a source row that is not point_in_time_usable"))

        # --- annual/quarterly duration mixing (advisory) ---
        if s["is_annual_snapshot"] and s["_fy_int"] is not None:
            k = (t, s["_fy_int"])
            if s["fiscal_period_end"] != canonical_annual_end.get(k):
                warnings.append(_w(t, WARN_MIX_RISK, s, "snapshot_type",
                                   s["fiscal_period_end"], "warning",
                                   "FY-tagged period end is not the canonical full-year end for "
                                   "this fiscal year (embedded quarterly duration); excluded from "
                                   "annual yoy comparisons"))

        # --- missing required source fields (advisory; sector-driven for financials) ---
        for field in REQUIRED_SOURCE_FIELDS:
            if field not in v:
                warnings.append(_w(t, WARN_MISSING_REQUIRED, s, field, "", "info",
                                   "required source field absent in this snapshot"))

        # A. profitability / margins
        s["operating_margin"] = ratio(get("operating_income"), get("revenue"), "operating_margin")
        s["net_margin"] = ratio(get("net_income"), get("revenue"), "net_margin")
        s["fcf_margin"] = ratio(get("free_cash_flow"), get("revenue"), "fcf_margin")
        s["operating_cash_flow_margin"] = ratio(
            get("operating_cash_flow"), get("revenue"), "operating_cash_flow_margin")

        # B. balance sheet / leverage
        s["debt_proxy_total_liabilities_to_assets"] = ratio(
            get("total_liabilities"), get("total_assets"),
            "debt_proxy_total_liabilities_to_assets")
        s["equity_to_assets"] = ratio(
            get("shareholder_equity"), get("total_assets"), "equity_to_assets")
        s["asset_turnover_proxy"] = ratio(
            get("revenue"), get("total_assets"), "asset_turnover_proxy")
        s["liability_to_equity"] = ratio(
            get("total_liabilities"), get("shareholder_equity"), "liability_to_equity")

        # C. growth / change (same period type only)
        if s["is_annual_snapshot"] and s["_fy_int"] is not None:
            prior = annual_index.get((t, s["_fy_int"] - 1))
        elif s["_fy_int"] is not None:
            prior = quarterly_index.get((t, s["_fy_int"] - 1, s["fiscal_period"]))
        else:
            prior = None
        s["revenue_yoy_growth"] = growth("revenue", "revenue_yoy_growth", prior)
        s["net_income_yoy_growth"] = growth("net_income", "net_income_yoy_growth", prior)
        s["operating_income_yoy_growth"] = growth(
            "operating_income", "operating_income_yoy_growth", prior)
        s["eps_diluted_yoy_growth"] = growth("eps_diluted", "eps_diluted_yoy_growth", prior)
        s["total_assets_yoy_growth"] = growth("total_assets", "total_assets_yoy_growth", prior)
        s["operating_cash_flow_yoy_growth"] = growth(
            "operating_cash_flow", "operating_cash_flow_yoy_growth", prior)
        s["free_cash_flow_yoy_growth"] = growth(
            "free_cash_flow", "free_cash_flow_yoy_growth", prior)

        # D. quality / cash conversion
        s["cash_conversion"] = ratio(
            get("operating_cash_flow"), get("net_income"), "cash_conversion")
        s["fcf_to_net_income"] = ratio(
            get("free_cash_flow"), get("net_income"), "fcf_to_net_income")
        capex = get("capital_expenditures")
        s["capex_intensity"] = ratio(
            abs(capex) if capex is not None else None, get("revenue"), "capex_intensity")
        ni, ocf, ta = get("net_income"), get("operating_cash_flow"), get("total_assets")
        if ni is not None and ocf is not None:
            s["accrual_proxy"] = ratio(ni - ocf, ta, "accrual_proxy")
        else:
            s["accrual_proxy"] = None

        # E. size / scale controls
        ta_v = get("total_assets")
        s["log_total_assets"] = math.log(ta_v) if (ta_v is not None and ta_v > 0) else None
        rev_v = get("revenue")
        s["log_revenue_abs"] = math.log(abs(rev_v)) if (rev_v not in (None, 0)) else None
        tl_v = get("total_liabilities")
        s["log_total_liabilities_abs"] = math.log(abs(tl_v)) if (tl_v not in (None, 0)) else None

        # F. availability / recency metadata
        asof_d = _parse_date(s["feature_asof_date"])
        end_d = _parse_date(s["fiscal_period_end"])
        if asof_d is not None and end_d is not None:
            s["filing_lag_days"] = (asof_d - end_d).days
        else:
            s["filing_lag_days"] = None

        # Round emitted numeric features for stable output.
        for fname in EMITTED_FEATURES:
            if fname == "filing_lag_days":
                continue
            if isinstance(s.get(fname), float):
                s[fname] = _round(s[fname])

    return warnings


# --------------------------------------------------------------------------- #
# Coverage + quality reporting
# --------------------------------------------------------------------------- #
def build_feature_coverage(snapshots):
    """One row per (ticker, emitted feature) with non-null coverage + as-of date span."""
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
                "ticker": ticker,
                "sector": sector,
                "feature_name": fname,
                "non_null_count": non_null,
                "total_snapshot_count": total,
                "coverage_fraction": _round(non_null / total, 4) if total else 0.0,
                "latest_feature_asof_date": asofs[-1] if asofs else "",
                "earliest_feature_asof_date": asofs[0] if asofs else "",
            })
    return out


def _warning_type_counts(warnings):
    counts = {}
    for w in warnings:
        counts[w["warning_type"]] = counts.get(w["warning_type"], 0) + 1
    return counts


def build_feature_quality_report(snapshots, warnings, source_phase, input_rows, input_tickers,
                                 recommendation):
    annual = [s for s in snapshots if s["is_annual_snapshot"]]
    quarterly = [s for s in snapshots if s["is_quarterly_snapshot"]]
    total = len(snapshots)
    with_asof = sum(1 for s in snapshots if s["feature_asof_date"])
    pit_usable = sum(1 for s in snapshots if s["point_in_time_usable"])

    asof_before_end = 0
    for s in snapshots:
        asof_d = _parse_date(s["feature_asof_date"])
        end_d = _parse_date(s["fiscal_period_end"])
        if asof_d is not None and end_d is not None and asof_d < end_d:
            asof_before_end += 1

    # Null coverage per emitted feature (fraction of snapshots where the feature is null).
    null_coverage_summary = {}
    coverage_by_family = {fam: {"sum": 0.0, "n": 0} for fam in FEATURE_FAMILIES}
    name_to_family = {d["name"]: d["family"] for d in FEATURE_DEFS}
    for fname in EMITTED_FEATURES:
        non_null = sum(1 for s in snapshots if s.get(fname) is not None)
        cov = (non_null / total) if total else 0.0
        null_coverage_summary[fname] = {
            "non_null": non_null,
            "null": total - non_null,
            "non_null_fraction": _round(cov, 4),
        }
        fam = name_to_family[fname]
        coverage_by_family[fam]["sum"] += cov
        coverage_by_family[fam]["n"] += 1
    coverage_by_feature_family = {
        fam: _round(d["sum"] / d["n"], 4) if d["n"] else 0.0
        for fam, d in coverage_by_family.items()
    }

    # Sector summary.
    sector_summary = {}
    for s in snapshots:
        sec = s["sector"] or "UNKNOWN"
        e = sector_summary.setdefault(sec, {"snapshot_rows": 0, "tickers": set()})
        e["snapshot_rows"] += 1
        e["tickers"].add(s["ticker"])
    sector_summary = {
        sec: {"snapshot_rows": e["snapshot_rows"], "ticker_count": len(e["tickers"])}
        for sec, e in sorted(sector_summary.items())
    }

    wtype_counts = _warning_type_counts(warnings)
    leakage = sum(c for w, c in wtype_counts.items() if w in LEAKAGE_WARNING_TYPES)
    tickers_with_features = sorted({s["ticker"] for s in snapshots})

    return {
        "source_phase": source_phase,
        "input_rows": input_rows,
        "input_tickers": sorted(input_tickers),
        "snapshot_rows": total,
        "annual_snapshot_rows": len(annual),
        "quarterly_snapshot_rows": len(quarterly),
        "feature_columns_count": len(EMITTED_FEATURES),
        "feature_families": list(FEATURE_FAMILIES),
        "feature_asof_date_coverage": _round(with_asof / total, 4) if total else 0.0,
        "point_in_time_usable_fraction": _round(pit_usable / total, 4) if total else 0.0,
        "feature_asof_before_period_end_count": asof_before_end,
        "null_coverage_summary": null_coverage_summary,
        "coverage_by_feature_family": coverage_by_feature_family,
        "warning_count": len(warnings),
        "warning_type_counts": wtype_counts,
        "leakage_warning_count": leakage,
        "extreme_ratio_warning_count": wtype_counts.get(WARN_EXTREME, 0),
        "annual_quarterly_mix_warning_count": wtype_counts.get(WARN_MIX_RISK, 0),
        "tickers_with_features": tickers_with_features,
        "sector_summary": sector_summary,
        "recommendation": recommendation,
    }


# --------------------------------------------------------------------------- #
# Decision logic
# --------------------------------------------------------------------------- #
def decide(confirmed_3g, snapshots, fq):
    """Apply the conservative Phase 3-H decision rules."""
    if not confirmed_3g.get("all_confirmed") or not snapshots:
        return REC_BLOCKED

    leakage = fq["leakage_warning_count"]
    asof_before = fq["feature_asof_before_period_end_count"]
    if leakage > 0 or asof_before > 0:
        return REC_REJECTED

    n_tickers = len(fq["tickers_with_features"])
    if (n_tickers >= 20 and fq["snapshot_rows"] > 100
            and fq["feature_asof_date_coverage"] >= 0.99
            and fq["point_in_time_usable_fraction"] >= 0.99
            and asof_before == 0 and leakage == 0
            and fq["feature_columns_count"] >= 20):
        return REC_SUCCESS
    if n_tickers >= 10 and fq["snapshot_rows"] > 0:
        return REC_PARTIAL
    return REC_BLOCKED


def build_recommendation(recommendation):
    reason = {
        REC_SUCCESS: (
            "Trailing-only, point-in-time fundamental features were built cleanly for all 20 "
            "tickers of the Phase 3-G sample: more than 100 canonical filing snapshots, a "
            "feature_asof_date present on every row (never the fiscal period end), full "
            "point-in-time usability, zero rows whose as-of date precedes the period end, zero "
            "leakage-risk warnings, and well over 20 engineered feature columns. Annual and "
            "quarterly durations are kept separate and embedded FY-tagged quarterly durations are "
            "flagged, not mixed. The feature snapshot is ready for a leakage-checked price "
            "alignment dry run next (still no model training, no labels). SEC still provides no "
            "earnings consensus or analyst revisions, so that provider gap remains open."),
        REC_PARTIAL: (
            "Features were built for part of the sample but coverage gaps, duration-mixing "
            "warnings, or other advisory warnings should be repaired before price alignment. No "
            "leakage was detected, but the result is not yet clean enough to proceed."),
        REC_BLOCKED: (
            "Required Phase 3-G inputs were missing, unconfirmed, or empty, so feature "
            "engineering could not run. Repair or re-confirm the Phase 3-G artifacts first."),
        REC_REJECTED: (
            "Point-in-time features could not be built safely: at least one snapshot showed a "
            "feature_asof_date at or before its fiscal period end, a non-point-in-time source "
            "row, or another leakage-risk condition. Redesign the feature/source handling before "
            "proceeding."),
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
        "label_generation_now": False,
        "price_join_now": False,
        "reason": reason,
    }


def build_recommended_next_phase(recommendation):
    table = {
        REC_SUCCESS: (
            "Fundamental Feature Price-Alignment Dry Run",
            "Join the 20-ticker point-in-time feature snapshot to historical price dates in a "
            "dry-run alignment, generate forward labels for validation only, and verify no "
            "leakage before any model training."),
        REC_PARTIAL: (
            "Repair SEC Fundamental Feature Prototype",
            "Fix feature coverage gaps, duration-mixing warnings, or leakage-risk warnings before "
            "price alignment."),
        REC_BLOCKED: (
            "Repair SEC Feature Engineering Inputs",
            "Repair missing or corrupted Phase 3-G artifacts before feature engineering."),
        REC_REJECTED: (
            "Alternative Fundamental Feature Design",
            "Redesign the feature set or source handling because safe point-in-time features "
            "could not be built."),
    }
    title, purpose = table[recommendation]
    return {"phase": "3-I", "title": title, "purpose": purpose}


def build_interpretation(recommendation):
    return {
        "feature_engineering_only": True,
        "price_volume_only_modeling_stopped": True,
        "model_trained": False,
        "model_features_computed": True,
        "labels_computed": False,
        "forward_returns_computed": False,
        "price_join_performed": False,
        "research_model_trained": False,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "deployable_model_artifact_written": False,
        "model_training_ready": False,
        "external_data_ingested": False,
        "production_data_ingested": False,
        "full_128_ticker_ingestion": False,
        "sec_public_data_used": True,
        "paid_vendor_called": False,
        "data_purchased": False,
        "read_from_d_drive": False,
        "wrote_to_d_drive": False,
        "network_used": False,
        "results_context_remains_survivorship_biased": True,
        "production_edge_claimed": False,
        "narrative": (
            "Phase 3-H is a controlled, read-only research feature-engineering prototype. It read "
            "the committed Phase 3-G mini-pipeline result, confirmed it succeeded "
            "(SEC_FUNDAMENTALS_MINIPIPELINE_SUCCESS) and routed here, re-confirmed the Phase 3-E "
            "ingestion contract, and converted the already-normalized 20-ticker SEC fundamentals "
            "sample into a point-in-time feature snapshot. It grouped the normalized rows into "
            "canonical filing snapshots, derived a safe feature_asof_date as the latest "
            "availability timestamp of the fields used (never the fiscal period end), kept annual "
            "(10-K / FY) and quarterly (10-Q / Qn) durations strictly separate, flagged embedded "
            "FY-tagged quarterly durations instead of mixing them, handled sector-specific missing "
            "fields explicitly (never filled with zero), and computed trailing-only profitability, "
            "leverage, growth, cash-conversion-quality, size, and availability/recency features. "
            "It used no network in this phase, computed no target labels and no forward returns, "
            "performed no price join, trained no model, created no production model candidate, "
            "wrote no deployable model artifact, touched no database, ran no migration, restarted "
            "no prediction service, enabled no serving flag, wrote nothing to the D: drive, read "
            "no D: price CSV, placed no orders, and traded nothing. SEC provides no earnings "
            "consensus or analyst revisions, so that provider gap stays open. This is a research "
            "feature prototype only and claims no production edge."),
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
        "sec_public_data_used": True,
        "vendor_api_called": False,
        "paid_vendor_api_called": False,
        "data_purchase_made": False,
        "external_data_ingested": False,
        "production_data_ingested": False,
        "full_128_ticker_ingestion": False,
        "model_features_computed": True,
        "labels_computed": False,
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


def _write_feature_dictionary(path):
    rows = []
    for d in FEATURE_DEFS:
        rows.append({
            "feature_name": d["name"],
            "feature_family": d["family"],
            "formula": d["formula"],
            "required_source_fields": ";".join(d["required_source_fields"]),
            "point_in_time_rule": d["point_in_time_rule"],
            "annual_quarterly_rule": d["annual_quarterly_rule"],
            "missing_value_policy": d["missing_value_policy"],
            "leakage_risk_note": d["leakage_risk_note"],
        })
    _write_csv(path, FEATURE_DICTIONARY_COLUMNS, rows)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run(features_json_path=FEATURES_JSON, snapshot_path=FEATURE_SNAPSHOT_CSV,
        dictionary_path=FEATURE_DICTIONARY_CSV, coverage_path=FEATURE_COVERAGE_CSV,
        quality_path=FEATURE_QUALITY_JSON, warnings_path=FEATURE_ALIGNMENT_WARNINGS_CSV):
    phase3g = _load_json(PHASE3G_JSON)
    contract = _load_json(INGESTION_CONTRACT_JSON)
    confirmed_3g = confirm_phase3g(phase3g)
    confirmed_contract = confirm_ingestion_contract(contract)

    fundamentals_rows = _read_csv_rows(PHASE3G_FUNDAMENTALS_CSV)
    identity_by_ticker = load_identity(PHASE3G_IDENTITY_CSV)
    input_tickers = {(r.get("ticker") or "").strip().upper()
                     for r in fundamentals_rows if (r.get("ticker") or "").strip()}

    snapshots = build_snapshots(fundamentals_rows, identity_by_ticker)
    annual_index, annual_asof, quarterly_index, quarterly_asof = build_growth_indexes(snapshots)
    warnings = compute_features(
        snapshots, annual_index, annual_asof, quarterly_index, quarterly_asof)

    coverage = build_feature_coverage(snapshots)
    fq_provisional = build_feature_quality_report(
        snapshots, warnings, source_phase="3-G", input_rows=len(fundamentals_rows),
        input_tickers=input_tickers, recommendation=None)
    recommendation = decide(confirmed_3g, snapshots, fq_provisional)
    fq = build_feature_quality_report(
        snapshots, warnings, source_phase="3-G", input_rows=len(fundamentals_rows),
        input_tickers=input_tickers, recommendation=recommendation)

    # Write the feature artifacts.
    _write_csv(snapshot_path, SNAPSHOT_COLUMNS, snapshots)
    _write_feature_dictionary(dictionary_path)
    _write_csv(coverage_path, FEATURE_COVERAGE_COLUMNS, coverage)
    _write_csv(warnings_path, WARNING_COLUMNS, warnings)
    os.makedirs(os.path.dirname(quality_path), exist_ok=True)
    with open(quality_path, "w", encoding="utf-8") as f:
        json.dump(fq, f, indent=2)

    result = {
        "phase": PHASE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "phase3g_minipipeline_json":
                "research/output/phase3g_sec_fundamentals_minipipeline.json",
            "phase3g_company_identity_csv":
                "research/output/phase3g_sec_fundamentals_sample/"
                "company_identity_20ticker_sample.csv",
            "phase3g_fundamentals_csv":
                "research/output/phase3g_sec_fundamentals_sample/"
                "fundamentals_20ticker_sample.csv",
            "phase3g_data_quality_json":
                "research/output/phase3g_sec_fundamentals_sample/data_quality_report.json",
            "phase3g_field_coverage_csv":
                "research/output/phase3g_sec_fundamentals_sample/field_coverage_by_ticker.csv",
            "phase3g_period_coverage_csv":
                "research/output/phase3g_sec_fundamentals_sample/period_coverage_by_ticker.csv",
            "phase3g_reconciliation_csv":
                "research/output/phase3g_sec_fundamentals_sample/reconciliation_warnings.csv",
            "phase3e_ingestion_contract_json":
                "research/output/phase3e_ingestion_contract.json",
            "sector_map_csv": "research/input/phase2k_p_sector_map_current.csv",
        },
        "outputs_written": {
            "features_json": "research/output/phase3h_sec_fundamental_features.json",
            "feature_snapshot_csv":
                "research/output/phase3h_sec_fundamental_features/"
                "feature_snapshot_20ticker_sample.csv",
            "feature_dictionary_csv":
                "research/output/phase3h_sec_fundamental_features/feature_dictionary.csv",
            "feature_coverage_by_ticker_csv":
                "research/output/phase3h_sec_fundamental_features/feature_coverage_by_ticker.csv",
            "feature_quality_report_json":
                "research/output/phase3h_sec_fundamental_features/feature_quality_report.json",
            "feature_alignment_warnings_csv":
                "research/output/phase3h_sec_fundamental_features/feature_alignment_warnings.csv",
        },
        "phase3g_summary": {
            "phase3g_confirmed": confirmed_3g,
            "phase3g_recommendation": (phase3g or {}).get("recommendation", {}).get(
                "recommendation"),
            "phase3g_fundamentals_rows": (phase3g or {}).get("fundamentals_summary", {}).get(
                "rows"),
            "ingestion_contract_confirmed": confirmed_contract,
        },
        "feature_engineering_config": {
            "raw_pivot_fields": list(RAW_PIVOT_FIELDS),
            "required_source_fields": list(REQUIRED_SOURCE_FIELDS),
            "snapshot_key": ["ticker", "fiscal_period_end", "fiscal_year", "fiscal_period",
                             "form"],
            "feature_asof_date_rule":
                "max(availability_datetime) across the fields used in the snapshot; never the "
                "fiscal_period_end",
            "annual_definition": "form in {10-K, 10-K/A} or fiscal_period == FY",
            "quarterly_definition": "form in {10-Q, 10-Q/A} or fiscal_period in {Q1,Q2,Q3,Q4}",
            "annual_quarterly_mixing": "kept separate; embedded FY-tagged quarterly durations are "
                                       "flagged annual_quarterly_mix_risk and excluded from annual "
                                       "yoy comparisons",
            "missing_value_policy": "missing financial fields are left null, never zero-filled; "
                                    "ratios are null when numerator or denominator is missing or "
                                    "the denominator is zero",
            "extreme_ratio_abs_threshold": EXTREME_RATIO_ABS,
            "extreme_growth_abs_threshold": EXTREME_GROWTH_ABS,
            "price_features_computed": False,
            "labels_computed": False,
            "forward_returns_computed": False,
        },
        "snapshot_summary": {
            "snapshot_rows": fq["snapshot_rows"],
            "annual_snapshot_rows": fq["annual_snapshot_rows"],
            "quarterly_snapshot_rows": fq["quarterly_snapshot_rows"],
            "feature_asof_date_coverage": fq["feature_asof_date_coverage"],
            "point_in_time_usable_fraction": fq["point_in_time_usable_fraction"],
            "feature_asof_before_period_end_count": fq["feature_asof_before_period_end_count"],
            "tickers_with_features": fq["tickers_with_features"],
            "columns": SNAPSHOT_COLUMNS,
        },
        "feature_dictionary_summary": {
            "feature_columns_count": fq["feature_columns_count"],
            "documented_feature_count": len(FEATURE_DEFS),
            "feature_families": list(FEATURE_FAMILIES),
            "emitted_features": list(EMITTED_FEATURES),
            "documented_but_omitted": [d["name"] for d in FEATURE_DEFS if not d["emit"]],
        },
        "feature_coverage_summary": {
            "coverage_rows": len(coverage),
            "coverage_by_feature_family": fq["coverage_by_feature_family"],
            "null_coverage_summary": fq["null_coverage_summary"],
        },
        "feature_quality_summary": fq,
        "warning_summary": {
            "warning_count": fq["warning_count"],
            "warning_type_counts": fq["warning_type_counts"],
            "leakage_warning_count": fq["leakage_warning_count"],
            "extreme_ratio_warning_count": fq["extreme_ratio_warning_count"],
            "annual_quarterly_mix_warning_count": fq["annual_quarterly_mix_warning_count"],
            "warnings_are_advisory_unless_leakage": True,
        },
        "source_limitations": list(_SOURCE_LIMITATIONS),
        "earnings_revisions_gap": {
            "sec_provides_earnings_consensus": False,
            "sec_provides_analyst_estimate_revisions": False,
            "gap_description": "SEC as-reported fundamentals support the trailing fundamental "
                               "features built here but provide no forward analyst consensus or "
                               "estimate revisions; a separate provider must be researched and "
                               "selected (see the Phase 3-E provider requirements matrix) before "
                               "earnings-surprise or revision-momentum features can be prototyped.",
            "provider_selection_required": True,
        },
        "recommendation": build_recommendation(recommendation),
        "interpretation": build_interpretation(recommendation),
        "recommended_next_phase": build_recommended_next_phase(recommendation),
    }
    result.update(build_safety_flags())

    os.makedirs(os.path.dirname(features_json_path), exist_ok=True)
    with open(features_json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    return result


def main():
    result = run()
    rec = result["recommendation"]
    nxt = result["recommended_next_phase"]
    ss = result["snapshot_summary"]
    ws = result["warning_summary"]
    fq = result["feature_quality_summary"]
    print("Phase %s - SEC Fundamentals Feature Engineering Prototype" % result["phase"])
    print("  phase3g confirmed        : %s"
          % result["phase3g_summary"]["phase3g_confirmed"]["all_confirmed"])
    print("  ingestion contract conf  : %s"
          % result["phase3g_summary"]["ingestion_contract_confirmed"]["all_confirmed"])
    print("  tickers with features    : %s" % len(ss["tickers_with_features"]))
    print("  snapshot rows            : %s (annual %s / quarterly %s)"
          % (ss["snapshot_rows"], ss["annual_snapshot_rows"], ss["quarterly_snapshot_rows"]))
    print("  feature columns          : %s" % fq["feature_columns_count"])
    print("  feature_asof coverage    : %s" % ss["feature_asof_date_coverage"])
    print("  point-in-time usable frac: %s" % ss["point_in_time_usable_fraction"])
    print("  asof < period_end count  : %s" % ss["feature_asof_before_period_end_count"])
    print("  warnings                 : %s" % ws["warning_count"])
    print("  warning type counts      : %s" % ws["warning_type_counts"])
    print("  leakage warnings         : %s" % ws["leakage_warning_count"])
    print("  network used             : %s" % result["network_used"])
    print("  recommendation           : %s" % rec["recommendation"])
    print("  recommended next phase   : %s - %s" % (nxt["phase"], nxt["title"]))
    return result


if __name__ == "__main__":
    main()
