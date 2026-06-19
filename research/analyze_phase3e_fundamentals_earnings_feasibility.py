"""Phase 3-E - Fundamentals and Earnings Data Feasibility.

This is a **feasibility / planning** analyzer, not a training phase. After the Phase 3-D
external-data decision selected the structured-company-data track, this script answers one
question:

    "What exact fundamentals / earnings / analyst-revisions data contract do we need before
     external-data model research can start, and what is the safest next implementation step?"

It reads small, Git-tracked Phase 3-D and Phase 3-C result artifacts plus the current-as-of
sector map, confirms that Phase 3-D routed here and that the Phase 3-C kill switch triggered,
then produces a vendor-agnostic blueprint: a schema catalog, point-in-time rules, minimum
coverage gates, a provider requirements / evaluation matrix, an ingestion contract, a
feasibility score, and a Phase 3-F recommendation. It writes four small files under
research/output.

Scope and safety. This analyzer fetches nothing from the network, calls no data vendor,
purchases no data, ingests no external data, trains no model, creates no production model
candidate, writes no deployable model artifact, reads nothing on the D: drive, writes nothing
to the D: drive, touches no database, runs no migration, restarts no prediction service,
enables no serving flag, places no orders, and trades nothing. It is read-only with respect to
every prior artifact and only writes the four small feasibility outputs listed below. The
provider matrix is a requirements / evaluation template only: it asserts no current vendor
pricing or availability and is explicit about where manual vendor research is still required.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime

PHASE = "3-E"

# --------------------------------------------------------------------------- #
# Paths (repo-local only; nothing on the D: drive is read or written here)
# --------------------------------------------------------------------------- #
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUT_DIR = os.path.join(_REPO_ROOT, "research", "output")
_IN_DIR = os.path.join(_REPO_ROOT, "research", "input")

PHASE3D_DECISION_JSON = os.path.join(_OUT_DIR, "phase3d_external_data_decision.json")
PHASE3D_TRACK_JSON = os.path.join(_OUT_DIR, "phase3d_recommended_data_track.json")
PHASE3D_MATRIX_CSV = os.path.join(_OUT_DIR, "phase3d_external_data_option_matrix.csv")
PHASE3C_JSON = os.path.join(_OUT_DIR, "phase3c_refined_greenfield_rerun.json")
SECTOR_MAP_CSV = os.path.join(_IN_DIR, "phase2k_p_sector_map_current.csv")

FEASIBILITY_JSON = os.path.join(_OUT_DIR, "phase3e_fundamentals_earnings_feasibility.json")
SCHEMA_CATALOG_CSV = os.path.join(_OUT_DIR, "phase3e_external_data_schema_catalog.csv")
PROVIDER_MATRIX_CSV = os.path.join(_OUT_DIR, "phase3e_provider_requirements_matrix.csv")
INGESTION_CONTRACT_JSON = os.path.join(_OUT_DIR, "phase3e_ingestion_contract.json")

# --------------------------------------------------------------------------- #
# Recommendation vocabulary
# --------------------------------------------------------------------------- #
REC_VENDOR_FREE = "PROCEED_TO_VENDOR_SELECTION_AND_FREE_SOURCE_PROTOTYPE"
REC_SEC_ONLY = "PROCEED_TO_FREE_SEC_FUNDAMENTALS_PROTOTYPE_ONLY"
REC_PAID_PIT = "REQUIRE_PAID_POINT_IN_TIME_VENDOR_BEFORE_MODELING"
REC_MANUAL = "REQUIRE_MANUAL_VENDOR_RESEARCH_BEFORE_IMPLEMENTATION"
REC_BLOCKED = "FUNDAMENTALS_EARNINGS_FEASIBILITY_BLOCKED"
ALLOWED_RECOMMENDATIONS = [
    REC_VENDOR_FREE, REC_SEC_ONLY, REC_PAID_PIT, REC_MANUAL, REC_BLOCKED,
]

# Phase 3-D contract this phase depends on.
PHASE3D_EXPECTED_RECOMMENDATION = "PROCEED_TO_FUNDAMENTALS_EARNINGS_DATA_FEASIBILITY"
PHASE3D_EXPECTED_TRACK_NAME = "Fundamentals + Earnings + Estimate Revisions"

# --------------------------------------------------------------------------- #
# Schema catalog. Vendor-agnostic field definitions across five data families.
# Each field carries the per-field metadata required by the task.
# Columns: field_name, data_family, table_name, required_or_optional, data_type,
#          periodicity, point_in_time_required, availability_timestamp_required,
#          restatement_sensitive, can_be_lagged, example_feature_use, validation_note
# --------------------------------------------------------------------------- #
SCHEMA_COLUMNS = [
    "field_name", "data_family", "table_name", "required_or_optional", "data_type",
    "periodicity", "point_in_time_required", "availability_timestamp_required",
    "restatement_sensitive", "can_be_lagged", "example_feature_use", "validation_note",
]


def _row(field_name, data_family, table_name, required, dtype, periodicity, pit, avail_ts,
         restate, lag, feature_use, note):
    return {
        "field_name": field_name,
        "data_family": data_family,
        "table_name": table_name,
        "required_or_optional": required,
        "data_type": dtype,
        "periodicity": periodicity,
        "point_in_time_required": pit,
        "availability_timestamp_required": avail_ts,
        "restatement_sensitive": restate,
        "can_be_lagged": lag,
        "example_feature_use": feature_use,
        "validation_note": note,
    }


def _build_company_identity_rows():
    fam, tbl = "company_identity", "company_identity"
    spec = [
        ("ticker", "required", "string", "exchange/sector grouping key", "primary join key to the 128-ticker panel"),
        ("company_name", "optional", "string", "human-readable label", "non-feature; descriptive only"),
        ("cik", "required", "string", "join key to SEC filings", "stable SEC identifier; preferred filing join"),
        ("figi", "optional", "string", "cross-vendor instrument mapping", "stable instrument id when ticker changes"),
        ("exchange", "optional", "string", "listing/liquidity grouping", "used for venue-level filtering"),
        ("sector", "required", "string", "sector-neutralization grouping", "must match the current-as-of sector map"),
        ("industry", "optional", "string", "finer peer grouping", "optional finer neutralization bucket"),
    ]
    rows = []
    for fn, req, dt, use, note in spec:
        rows.append(_row(fn, fam, tbl, req, dt, "static", "yes", "no", "no", "yes", use, note))
    # Effective-dating + source carry availability semantics.
    rows.append(_row("effective_from", fam, tbl, "required", "date", "static", "yes", "yes",
                     "yes", "yes", "membership/identity as-of windowing",
                     "identity edits must be effective-dated so historical joins are point-in-time"))
    rows.append(_row("effective_to", fam, tbl, "optional", "date", "static", "yes", "yes",
                     "yes", "yes", "membership/identity as-of windowing",
                     "open-ended (null) means currently effective"))
    rows.append(_row("source", fam, tbl, "required", "string", "static", "no", "no",
                     "no", "yes", "provenance / audit",
                     "records which provider supplied the identity row"))
    return rows


def _build_fundamentals_rows():
    fam, tbl = "fundamentals", "fundamentals"
    # Keys / timing columns first.
    key_rows = [
        _row("ticker", fam, tbl, "required", "string", "quarterly_or_annual", "yes", "no",
             "no", "yes", "join key", "panel join key"),
        _row("statement_type", fam, tbl, "required", "string", "quarterly_or_annual", "no", "no",
             "no", "yes", "income_statement / balance_sheet / cash_flow grouping",
             "tags the originating statement; enables per-statement validation"),
        _row("periodicity", fam, tbl, "required", "string", "quarterly_or_annual", "no", "no",
             "no", "yes", "quarterly vs annual selection",
             "explicit quarterly/annual flag; never mix periodicities in one feature"),
        _row("fiscal_period_end", fam, tbl, "required", "date", "quarterly_or_annual", "yes", "no",
             "no", "yes", "period alignment", "the period the statement describes; NOT an availability date"),
        _row("fiscal_year", fam, tbl, "required", "int", "quarterly_or_annual", "no", "no",
             "no", "yes", "period alignment", "fiscal-year label"),
        _row("fiscal_quarter", fam, tbl, "optional", "int", "quarterly_or_annual", "no", "no",
             "no", "yes", "period alignment", "null for annual rows"),
        _row("filing_date", fam, tbl, "required", "date", "quarterly_or_annual", "yes", "yes",
             "no", "yes", "filing recency", "calendar date of the filing"),
        _row("accepted_datetime", fam, tbl, "required", "datetime", "quarterly_or_annual", "yes", "yes",
             "no", "yes", "earliest safe availability timestamp",
             "filing acceptance time is the earliest point-in-time a filing value may be used"),
        _row("availability_datetime", fam, tbl, "required", "datetime", "quarterly_or_annual", "yes", "yes",
             "no", "yes", "feature_asof gate", "no feature may use this row before availability_datetime"),
        _row("report_date", fam, tbl, "optional", "date", "quarterly_or_annual", "yes", "yes",
             "no", "yes", "vendor availability fallback", "vendor-provided availability when accepted_datetime is absent"),
    ]
    # Line items - all share the same point-in-time / restatement profile.
    line_items = [
        ("revenue", "required", "valuation/growth (sales yield, sales growth)"),
        ("gross_profit", "optional", "quality (gross margin)"),
        ("operating_income", "required", "quality/profitability (operating margin)"),
        ("net_income", "required", "valuation/quality (earnings yield, ROE)"),
        ("eps_basic", "optional", "valuation (earnings yield)"),
        ("eps_diluted", "required", "valuation (diluted earnings yield)"),
        ("shares_basic", "optional", "per-share normalization"),
        ("shares_diluted", "required", "per-share normalization / market cap"),
        ("total_assets", "required", "leverage/quality (asset turnover, ROA)"),
        ("total_liabilities", "required", "leverage (liabilities/assets)"),
        ("shareholder_equity", "required", "valuation/leverage (book yield, debt/equity)"),
        ("cash_and_equivalents", "optional", "balance-sheet strength (net debt)"),
        ("short_term_debt", "optional", "leverage (net debt)"),
        ("long_term_debt", "optional", "leverage (debt/equity, net debt)"),
        ("operating_cash_flow", "required", "quality (accruals, cash conversion)"),
        ("capital_expenditures", "optional", "free-cash-flow construction"),
        ("free_cash_flow", "required", "valuation (FCF yield)"),
        ("dividends_paid", "optional", "capital returns (payout / shareholder yield)"),
        ("stock_repurchases", "optional", "capital returns (buyback yield)"),
        ("depreciation_amortization", "optional", "accruals / EBITDA construction"),
        ("research_and_development", "optional", "quality / intensity ratios"),
        ("sg_and_a", "optional", "operating-efficiency ratios"),
        ("interest_expense", "optional", "leverage / coverage ratios"),
    ]
    item_rows = []
    for fn, req, use in line_items:
        item_rows.append(_row(
            fn, fam, tbl, req, "decimal", "quarterly_or_annual", "yes", "yes", "yes", "yes", use,
            "restatement-sensitive: use first-reported value with its true availability date"))
    return key_rows + item_rows


def _build_earnings_rows():
    fam, tbl = "earnings_events", "earnings_events"
    spec = [
        ("ticker", "required", "string", "event", "yes", "no", "no", "yes",
         "join key", "panel join key"),
        ("fiscal_period_end", "required", "date", "event", "yes", "no", "no", "yes",
         "period alignment", "period the announcement covers"),
        ("fiscal_year", "required", "int", "event", "no", "no", "no", "yes",
         "period alignment", "fiscal-year label"),
        ("fiscal_quarter", "optional", "int", "event", "no", "no", "no", "yes",
         "period alignment", "quarter label when applicable"),
        ("earnings_announcement_datetime", "required", "datetime", "event", "yes", "yes", "no", "yes",
         "event-window features", "exact announcement timestamp anchors the event study"),
        ("announcement_timing", "required", "string", "event", "yes", "yes", "no", "yes",
         "first-tradable-date logic",
         "before_open / after_close / intraday / unknown determines the first tradable session"),
        ("reported_eps", "required", "decimal", "event", "yes", "yes", "no", "yes",
         "earnings surprise (SUE)", "as-reported actual EPS"),
        ("consensus_eps_before_announcement", "required", "decimal", "event", "yes", "yes", "no", "yes",
         "earnings surprise (SUE)",
         "consensus snapshot strictly BEFORE the announcement; no post-event consensus"),
        ("eps_surprise", "optional", "decimal", "event", "yes", "yes", "no", "yes",
         "earnings surprise (SUE)", "reported minus pre-announcement consensus"),
        ("eps_surprise_pct", "optional", "decimal", "event", "yes", "yes", "no", "yes",
         "standardized surprise", "percentage / standardized surprise"),
        ("reported_revenue", "optional", "decimal", "event", "yes", "yes", "no", "yes",
         "revenue surprise", "as-reported actual revenue"),
        ("consensus_revenue_before_announcement", "optional", "decimal", "event", "yes", "yes", "no", "yes",
         "revenue surprise", "pre-announcement consensus revenue"),
        ("revenue_surprise", "optional", "decimal", "event", "yes", "yes", "no", "yes",
         "revenue surprise", "reported minus consensus revenue"),
        ("revenue_surprise_pct", "optional", "decimal", "event", "yes", "yes", "no", "yes",
         "standardized revenue surprise", "percentage revenue surprise"),
        ("availability_datetime", "required", "datetime", "event", "yes", "yes", "no", "yes",
         "feature_asof gate", "no event feature may be used before availability_datetime"),
        ("source", "required", "string", "event", "no", "no", "no", "yes",
         "provenance / audit", "records the earnings-data provider"),
    ]
    rows = []
    for (fn, req, dt, per, pit, ats, rs, lag, use, note) in spec:
        rows.append(_row(fn, fam, tbl, req, dt, per, pit, ats, rs, lag, use, note))
    return rows


def _build_estimates_rows():
    fam, tbl = "analyst_estimates_revisions", "analyst_estimates_revisions"
    spec = [
        ("ticker", "required", "string", "snapshot", "yes", "no", "no", "yes",
         "join key", "panel join key"),
        ("estimate_date", "required", "date", "snapshot", "yes", "yes", "no", "yes",
         "revision-momentum windowing", "the date this consensus snapshot was known"),
        ("fiscal_period_end", "required", "date", "snapshot", "yes", "no", "no", "yes",
         "period alignment", "the period the estimate targets"),
        ("fiscal_year", "required", "int", "snapshot", "no", "no", "no", "yes",
         "period alignment", "fiscal-year label"),
        ("fiscal_quarter", "optional", "int", "snapshot", "no", "no", "no", "yes",
         "period alignment", "quarter label when applicable"),
        ("estimate_type", "required", "string", "snapshot", "no", "no", "no", "yes",
         "estimate selection", "EPS / revenue / EBITDA / price_target / recommendation"),
        ("consensus_mean", "required", "decimal", "snapshot", "yes", "yes", "no", "yes",
         "revision level / change", "mean consensus at the snapshot"),
        ("consensus_median", "optional", "decimal", "snapshot", "yes", "yes", "no", "yes",
         "robust consensus level", "median consensus at the snapshot"),
        ("consensus_high", "optional", "decimal", "snapshot", "yes", "yes", "no", "yes",
         "dispersion", "high estimate"),
        ("consensus_low", "optional", "decimal", "snapshot", "yes", "yes", "no", "yes",
         "dispersion", "low estimate"),
        ("analyst_count", "optional", "int", "snapshot", "yes", "yes", "no", "yes",
         "coverage / confidence", "number of contributing analysts"),
        ("standard_deviation", "optional", "decimal", "snapshot", "yes", "yes", "no", "yes",
         "estimate dispersion", "cross-analyst dispersion of estimates"),
        ("up_revision_count_30d", "optional", "int", "snapshot", "yes", "yes", "no", "yes",
         "estimate revision momentum", "count of upward revisions in the trailing 30d"),
        ("down_revision_count_30d", "optional", "int", "snapshot", "yes", "yes", "no", "yes",
         "estimate revision momentum", "count of downward revisions in the trailing 30d"),
        ("net_revision_ratio_30d", "optional", "decimal", "snapshot", "yes", "yes", "no", "yes",
         "estimate revision momentum", "(up - down) / total over the trailing 30d"),
        ("consensus_change_7d", "optional", "decimal", "snapshot", "yes", "yes", "no", "yes",
         "revision momentum", "trailing 7d consensus change"),
        ("consensus_change_30d", "optional", "decimal", "snapshot", "yes", "yes", "no", "yes",
         "revision momentum", "trailing 30d consensus change"),
        ("consensus_change_90d", "optional", "decimal", "snapshot", "yes", "yes", "no", "yes",
         "revision momentum", "trailing 90d consensus change"),
        ("availability_datetime", "required", "datetime", "snapshot", "yes", "yes", "no", "yes",
         "feature_asof gate",
         "each snapshot must be timestamped to when the estimate changed or was known"),
        ("source", "required", "string", "snapshot", "no", "no", "no", "yes",
         "provenance / audit", "records the estimates provider"),
    ]
    rows = []
    for (fn, req, dt, per, pit, ats, rs, lag, use, note) in spec:
        rows.append(_row(fn, fam, tbl, req, dt, per, pit, ats, rs, lag, use, note))
    return rows


def _build_derived_block_rows():
    fam, tbl = "derived_feature_blocks", "derived_features"
    spec = [
        ("valuation", "earnings/book/sales/FCF yields", "built from fundamentals; trailing as-reported only"),
        ("quality", "margins, ROE/ROA, accruals", "ratios of trailing as-reported fundamentals"),
        ("profitability", "operating/net margins", "trailing as-reported only"),
        ("growth", "sales / earnings growth", "year-over-year on first-reported values"),
        ("leverage", "debt/equity, net debt, coverage", "balance-sheet ratios, as-reported"),
        ("accruals", "cash vs accrual earnings gap", "operating cash flow vs net income"),
        ("capital_returns", "payout / buyback / shareholder yield", "dividends and repurchases over market cap"),
        ("earnings_surprise", "standardized unexpected earnings (SUE)", "uses pre-announcement consensus only"),
        ("post_earnings_announcement_drift", "drift after the event window", "anchored to announcement timing"),
        ("estimate_revision_momentum", "net / directional revisions", "uses point-in-time consensus snapshots"),
        ("estimate_dispersion", "cross-analyst dispersion", "std-dev / high-low of consensus"),
        ("recommendation_momentum", "rating upgrades / downgrades", "point-in-time recommendation changes"),
    ]
    rows = []
    for (fn, use, note) in spec:
        rows.append(_row(fn, fam, tbl, "optional", "feature_block", "derived", "yes", "no",
                         "no", "yes", use, note))
    return rows


def build_schema_catalog():
    return (_build_company_identity_rows()
            + _build_fundamentals_rows()
            + _build_earnings_rows()
            + _build_estimates_rows()
            + _build_derived_block_rows())


def build_schema_catalog_summary(catalog):
    families = {}
    for r in catalog:
        families.setdefault(r["data_family"], 0)
        families[r["data_family"]] += 1
    required = sum(1 for r in catalog if r["required_or_optional"] == "required")
    return {
        "total_fields": len(catalog),
        "fields_by_data_family": families,
        "required_field_count": required,
        "data_families": sorted(families.keys()),
        "tables": sorted({r["table_name"] for r in catalog}),
        "note": "Vendor-agnostic schema: field definitions are independent of any specific "
                "provider. Every fundamentals / earnings / estimate field carries an explicit "
                "availability_datetime so features can be gated point-in-time.",
    }


# --------------------------------------------------------------------------- #
# Point-in-time rules and coverage gates
# --------------------------------------------------------------------------- #
def build_point_in_time_rules():
    return [
        "No feature may use a value before its availability_datetime; feature_asof_date must be "
        "<= scoring_date for every row used on that date.",
        "For SEC filings, accepted_datetime (filing acceptance time) is the earliest safe "
        "availability_datetime; the fiscal_period_end is NOT an availability date.",
        "For earnings, the earnings_announcement_datetime plus announcement_timing "
        "(before_open / after_close / intraday / unknown) determines the first tradable date.",
        "For analyst revisions, each consensus snapshot must carry an availability_datetime equal "
        "to when the estimate changed or first became known; never back-date a revision.",
        "Restated fundamentals must not be used historically unless the provider offers "
        "as-reported point-in-time history; if only restated values exist, mark the track as "
        "caveated / prototype only.",
        "Every target label must remain strictly forward of the feature_asof_date, preserving the "
        "embargo used by the existing walk-forward harness.",
        "Consensus used for an earnings surprise must be the pre-announcement consensus snapshot, "
        "never a post-event consensus.",
    ]


def build_minimum_coverage_gates():
    return [
        {"gate": "ticker_coverage", "threshold": ">= 95% of the 128-ticker universe",
         "rationale": "cross-sectional ranks degrade if too many names are missing"},
        {"gate": "history_start", "threshold": ">= 2016-01-01 to latest available, or documented gaps",
         "rationale": "needs enough history for >=3y train + multiple walk-forward folds"},
        {"gate": "quarterly_fundamentals_coverage", "threshold": ">= 90% of ticker-quarter pairs",
         "rationale": "sparse fundamentals bias the cross-section"},
        {"gate": "earnings_dates_coverage", "threshold": ">= 90% of ticker-quarter pairs",
         "rationale": "event studies require reliable announcement timing"},
        {"gate": "estimates_revisions_coverage",
         "threshold": ">= 80% of ticker-quarter pairs if revisions are in the first model",
         "rationale": "revision momentum needs broad coverage to rank across names"},
        {"gate": "no_duplicate_records",
         "threshold": "no duplicate ticker-period-source records unless explicitly versioned",
         "rationale": "duplicates corrupt joins and double-count features"},
        {"gate": "availability_timestamp_present",
         "threshold": "100% of fundamentals / earnings / estimate records have availability_datetime",
         "rationale": "point-in-time gating is impossible without it"},
        {"gate": "restatement_handling_documented",
         "threshold": "first-reported-vs-restated policy explicitly documented",
         "rationale": "restated values silently introduce look-ahead bias"},
        {"gate": "license_permits_local_research_storage",
         "threshold": "provider license permits local research storage",
         "rationale": "storage / redistribution terms must be cleared before acquisition"},
        {"gate": "no_production_db_write_required",
         "threshold": "ingestion can run without production database writes",
         "rationale": "feasibility and prototyping stay isolated from production state"},
    ]


# --------------------------------------------------------------------------- #
# Provider requirements / evaluation matrix (template only; no live vendor research)
# --------------------------------------------------------------------------- #
PROVIDER_COLUMNS = [
    "provider_category", "examples_to_research_manually", "expected_data_families",
    "point_in_time_support_required", "as_reported_support_required",
    "estimate_revision_support_required", "earnings_timestamp_support_required",
    "historical_coverage_requirement", "expected_cost_band_placeholder",
    "implementation_complexity_estimate", "licensing_questions", "data_quality_questions",
    "fit_for_phase3f", "notes",
]

_PROVIDERS = [
    {
        "provider_category": "SEC EDGAR / company-facts style fundamentals",
        "examples_to_research_manually": "SEC EDGAR company facts; SEC financial statement data sets",
        "expected_data_families": "fundamentals; company_identity",
        "point_in_time_support_required": "yes",
        "as_reported_support_required": "yes",
        "estimate_revision_support_required": "no",
        "earnings_timestamp_support_required": "no",
        "historical_coverage_requirement": ">= 2016 to present for the 128-ticker universe",
        "expected_cost_band_placeholder": "free",
        "implementation_complexity_estimate": "medium",
        "licensing_questions": "confirm permitted use / attribution for local research storage",
        "data_quality_questions": "are accepted datetimes and amended filings fully captured?",
        "fit_for_phase3f": "yes",
        "notes": "Free public filings with filing-acceptance timestamps; strongest first prototype "
                 "source for fundamentals. Does not provide consensus estimates or revisions.",
    },
    {
        "provider_category": "low-cost market-data APIs with fundamentals",
        "examples_to_research_manually": "low-cost fundamentals API tiers (research current options manually)",
        "expected_data_families": "fundamentals; earnings_events (sometimes)",
        "point_in_time_support_required": "yes",
        "as_reported_support_required": "yes",
        "estimate_revision_support_required": "no",
        "earnings_timestamp_support_required": "maybe",
        "historical_coverage_requirement": ">= 2016 to present; verify depth per field",
        "expected_cost_band_placeholder": "low_cost",
        "implementation_complexity_estimate": "low",
        "licensing_questions": "do terms permit storing history locally for research?",
        "data_quality_questions": "is the history as-reported or restated? are timestamps real?",
        "fit_for_phase3f": "maybe",
        "notes": "Convenient but frequently restated rather than as-reported; verify point-in-time "
                 "fidelity before relying on it.",
    },
    {
        "provider_category": "dedicated fundamentals / estimates APIs",
        "examples_to_research_manually": "specialist fundamentals + estimates providers (research current options manually)",
        "expected_data_families": "fundamentals; earnings_events; analyst_estimates_revisions",
        "point_in_time_support_required": "yes",
        "as_reported_support_required": "yes",
        "estimate_revision_support_required": "yes",
        "earnings_timestamp_support_required": "yes",
        "historical_coverage_requirement": ">= 2016 to present with point-in-time snapshots",
        "expected_cost_band_placeholder": "paid_vendor",
        "implementation_complexity_estimate": "medium",
        "licensing_questions": "redistribution / storage limits; per-seat or per-call licensing?",
        "data_quality_questions": "are consensus snapshots timestamped to revision events?",
        "fit_for_phase3f": "maybe",
        "notes": "Most likely single source for the full track including revisions; cost and "
                 "point-in-time fidelity must be confirmed before any commitment.",
    },
    {
        "provider_category": "professional institutional providers",
        "examples_to_research_manually": "institutional fundamentals / estimates platforms (research current options manually)",
        "expected_data_families": "fundamentals; earnings_events; analyst_estimates_revisions",
        "point_in_time_support_required": "yes",
        "as_reported_support_required": "yes",
        "estimate_revision_support_required": "yes",
        "earnings_timestamp_support_required": "yes",
        "historical_coverage_requirement": "deep point-in-time history",
        "expected_cost_band_placeholder": "institutional",
        "implementation_complexity_estimate": "high",
        "licensing_questions": "enterprise licensing, entitlement, and storage restrictions",
        "data_quality_questions": "gold-standard point-in-time, but is the cost justified for 128 names?",
        "fit_for_phase3f": "no",
        "notes": "Highest quality and highest cost; out of scope for a small research project unless "
                 "a free / low-cost prototype proves the edge first.",
    },
    {
        "provider_category": "earnings-calendar providers",
        "examples_to_research_manually": "earnings-calendar / event-date providers (research current options manually)",
        "expected_data_families": "earnings_events",
        "point_in_time_support_required": "yes",
        "as_reported_support_required": "no",
        "estimate_revision_support_required": "no",
        "earnings_timestamp_support_required": "yes",
        "historical_coverage_requirement": "historical announcement dates + before/after-market timing",
        "expected_cost_band_placeholder": "low_cost",
        "implementation_complexity_estimate": "low",
        "licensing_questions": "history retention vs forward-calendar-only terms",
        "data_quality_questions": "is announcement timing (before_open / after_close) reliable historically?",
        "fit_for_phase3f": "maybe",
        "notes": "Can supply event dates / timing, but usually not the pre-announcement consensus "
                 "needed for surprise; pair with an estimates source.",
    },
    {
        "provider_category": "estimate-revision providers",
        "examples_to_research_manually": "consensus / estimate-revision providers (research current options manually)",
        "expected_data_families": "analyst_estimates_revisions",
        "point_in_time_support_required": "yes",
        "as_reported_support_required": "no",
        "estimate_revision_support_required": "yes",
        "earnings_timestamp_support_required": "no",
        "historical_coverage_requirement": "point-in-time consensus snapshots back to >= 2016",
        "expected_cost_band_placeholder": "paid_vendor",
        "implementation_complexity_estimate": "medium",
        "licensing_questions": "snapshot redistribution / storage; per-analyst entitlement",
        "data_quality_questions": "are snapshots timestamped to the revision event, not the period?",
        "fit_for_phase3f": "maybe",
        "notes": "The hardest and most likely-paid component; estimate revisions rarely have a clean "
                 "free point-in-time source.",
    },
]


def build_provider_summary():
    free_or_low = [p["provider_category"] for p in _PROVIDERS
                   if p["expected_cost_band_placeholder"] in ("free", "low_cost")]
    full_track = [p["provider_category"] for p in _PROVIDERS
                  if p["estimate_revision_support_required"] == "yes"
                  and p["as_reported_support_required"] == "yes"]
    return {
        "provider_categories_evaluated": len(_PROVIDERS),
        "is_requirements_template_only": True,
        "asserts_current_vendor_pricing_or_availability": False,
        "free_or_low_cost_fundamentals_candidates": free_or_low,
        "categories_that_could_cover_full_track": full_track,
        "fundamentals_free_prototype_source": "SEC EDGAR / company-facts style fundamentals",
        "estimates_revisions_provider_selection_required": True,
        "note": "This matrix is a requirements / evaluation template, not a live vendor research "
                "result. It does not browse the web, assert current prices, or claim provider "
                "features are currently available; manual vendor research is required before any "
                "acquisition.",
    }


# --------------------------------------------------------------------------- #
# Ingestion contract
# --------------------------------------------------------------------------- #
def build_ingestion_contract():
    return {
        "source_name": "TO_BE_SELECTED_IN_PHASE_3F",
        "source_version": "TO_BE_RECORDED_AT_INGESTION_TIME",
        "extraction_timestamp": "TO_BE_RECORDED_AT_INGESTION_TIME",
        "raw_file_location": "research/output/ (repo-local research area only; never the D: drive)",
        "normalized_tables": [
            "company_identity", "fundamentals", "earnings_events", "analyst_estimates_revisions",
        ],
        "required_table_schemas": {
            "company_identity": [c["field_name"] for c in _build_company_identity_rows()],
            "fundamentals": [c["field_name"] for c in _build_fundamentals_rows()],
            "earnings_events": [c["field_name"] for c in _build_earnings_rows()],
            "analyst_estimates_revisions": [c["field_name"] for c in _build_estimates_rows()],
        },
        "primary_keys": {
            "company_identity": ["ticker", "effective_from"],
            "fundamentals": ["ticker", "fiscal_period_end", "periodicity", "statement_type"],
            "earnings_events": ["ticker", "fiscal_period_end"],
            "analyst_estimates_revisions": ["ticker", "estimate_date", "fiscal_period_end", "estimate_type"],
        },
        "unique_constraints": {
            "fundamentals": ["ticker + fiscal_period_end + periodicity + statement_type + source"],
            "earnings_events": ["ticker + fiscal_period_end + source"],
            "analyst_estimates_revisions": ["ticker + estimate_date + fiscal_period_end + estimate_type + source"],
        },
        "point_in_time_columns": [
            "accepted_datetime", "availability_datetime", "earnings_announcement_datetime",
            "estimate_date", "filing_date", "effective_from",
        ],
        "restatement_policy": "Store first-reported values with their true availability_datetime. "
                              "Keep restated values only in a separate versioned column / row; never "
                              "overwrite the as-reported history used for features.",
        "availability_datetime_policy": "Every fundamentals / earnings / estimate record must carry "
                                        "an availability_datetime; rows without one are quarantined "
                                        "and excluded from features.",
        "ticker_mapping_policy": "Map provider identifiers (cik / figi / ticker) to the panel ticker "
                                 "via the effective-dated company_identity table.",
        "currency_policy": "Normalize all monetary fields to reporting currency (USD for this "
                           "universe); record the original currency when not USD.",
        "split_adjustment_policy": "Keep per-share fundamentals on an as-reported basis; apply split "
                                   "adjustments consistently with the existing price panel only when "
                                   "constructing per-share features, never retroactively to filings.",
        "data_quality_checks": [
            "no duplicate primary keys unless versioned",
            "monetary fields within sane bounds; flag sign flips vs prior period",
            "fundamentals reconcile across statements where applicable",
            "announcement_timing in {before_open, after_close, intraday, unknown}",
        ],
        "coverage_checks": [
            "ticker coverage >= 95% of the 128-ticker universe",
            "quarterly fundamentals >= 90% of ticker-quarter pairs",
            "earnings dates >= 90% of ticker-quarter pairs",
            "estimates / revisions >= 80% of ticker-quarter pairs if included",
        ],
        "leakage_checks": [
            "feature_asof_date <= scoring_date for every feature row",
            "no consensus dated on/after its earnings announcement used for that surprise",
            "no restated value used before its restatement availability_datetime",
            "target labels remain strictly forward of the feature_asof_date",
        ],
        "allowed_output_locations": ["research/output/"],
        "forbidden_actions": [
            "no network fetch, no vendor API call, no data purchase in the feasibility phase",
            "no production database write",
            "no migration",
            "no deployment and no prediction-service restart",
            "no model training, no production model candidate, no deployable model artifact",
            "no writes to the D: drive",
            "no orders, no trading, no automation",
        ],
        "no_database_write_by_default": True,
        "no_production_integration": True,
    }


def build_ingestion_contract_summary(contract):
    return {
        "normalized_tables": contract["normalized_tables"],
        "point_in_time_columns": contract["point_in_time_columns"],
        "no_database_write_by_default": contract["no_database_write_by_default"],
        "no_production_integration": contract["no_production_integration"],
        "allowed_output_locations": contract["allowed_output_locations"],
        "leakage_check_count": len(contract["leakage_checks"]),
        "coverage_check_count": len(contract["coverage_checks"]),
    }


# --------------------------------------------------------------------------- #
# Feasibility scoring (1-5, 5 = most favorable; risk/complexity framed as positive)
# --------------------------------------------------------------------------- #
FEASIBILITY_CRITERIA = [
    "point_in_time_feasibility",
    "schema_completeness",
    "coverage_feasibility",
    "cost_risk",
    "implementation_complexity",
    "leakage_risk",
    "compatibility_with_existing_walkforward",
    "expected_incremental_predictive_value",
    "readiness_for_ingestion_prototype",
]
FEASIBILITY_POLARITY_NOTES = {
    "cost_risk": "cost risk (scored as low-cost: 5 = low cost risk, 1 = high cost risk)",
    "implementation_complexity": "implementation complexity (scored as ease: 5 = low complexity)",
    "leakage_risk": "leakage risk (scored as safety: 5 = low leakage risk, 1 = high leakage risk)",
}
_FEASIBILITY_SCORES = {
    "point_in_time_feasibility": 4,
    "schema_completeness": 5,
    "coverage_feasibility": 4,
    "cost_risk": 3,
    "implementation_complexity": 4,
    "leakage_risk": 4,
    "compatibility_with_existing_walkforward": 5,
    "expected_incremental_predictive_value": 4,
    "readiness_for_ingestion_prototype": 4,
}
_FEASIBILITY_RATIONALE = {
    "point_in_time_feasibility":
        "SEC filing-acceptance timestamps make fundamentals and earnings point-in-time clean; "
        "estimate revisions need a provider that timestamps consensus snapshots.",
    "schema_completeness":
        "The vendor-agnostic schema covers identity, full financial statements, earnings events, "
        "and analyst revisions with explicit availability timestamps.",
    "coverage_feasibility":
        "The 128 large-cap names are well covered by SEC fundamentals and earnings; revisions "
        "coverage depends on the chosen estimates provider.",
    "cost_risk":
        "Fundamentals are free via SEC, but clean point-in-time earnings consensus and analyst "
        "revisions usually require a paid provider, creating moderate cost risk.",
    "implementation_complexity":
        "Structured fields are straightforward; SEC normalization and reported-vs-restated "
        "handling are moderate, not heavy NLP.",
    "leakage_risk":
        "Leakage hazards (restatement, consensus snapshot timing) are well understood and handled "
        "by explicit availability_datetime gating.",
    "compatibility_with_existing_walkforward":
        "All fields convert to strictly trailing cross-sectional features that drop directly into "
        "the existing embargoed walk-forward harness.",
    "expected_incremental_predictive_value":
        "Fundamentals, earnings surprise / drift, and estimate-revision momentum are well-studied "
        "cross-sectional anomalies complementary to price/volume.",
    "readiness_for_ingestion_prototype":
        "A free SEC fundamentals prototype can be built immediately; the earnings/revisions "
        "provider still needs selection before that part can be prototyped.",
}


def build_feasibility_scores():
    scores = {k: int(_FEASIBILITY_SCORES[k]) for k in FEASIBILITY_CRITERIA}
    mean = round(sum(scores.values()) / len(scores), 4)
    return {
        "scale": "Each criterion is scored 1-5 where 5 is the most favorable feasibility outcome.",
        "positive_polarity_notes": FEASIBILITY_POLARITY_NOTES,
        "scores": scores,
        "rationale": _FEASIBILITY_RATIONALE,
        "mean_score": mean,
        "min_score": min(scores.values()),
        "free_fundamentals_prototype_possible": True,
        "estimates_revisions_need_provider": True,
        "as_reported_paid_only_blocker": False,
        "provider_capability_too_uncertain_to_prototype": False,
    }


# --------------------------------------------------------------------------- #
# Input loading + confirmations
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


def confirm_phase3d(phase3d):
    """Confirm the Phase 3-D decision contract that gates this phase."""
    if not phase3d:
        return {"all_confirmed": False, "phase3d_present": False}
    rec = phase3d.get("recommendation", {}) or {}
    nxt = phase3d.get("recommended_next_phase", {}) or {}
    sel = phase3d.get("selected_data_track", {}) or {}
    checks = {
        "phase3d_present": True,
        "phase_is_3d": phase3d.get("phase") == "3-D",
        "recommendation_is_fundamentals":
            rec.get("recommendation") == PHASE3D_EXPECTED_RECOMMENDATION,
        "next_phase_is_3e": nxt.get("phase") == "3-E",
        "selected_track_name_matches": sel.get("track_name") == PHASE3D_EXPECTED_TRACK_NAME,
        "research_model_trained_false": phase3d.get("research_model_trained") is False,
        "production_model_trained_false": phase3d.get("production_model_trained") is False,
        "production_model_candidate_created_false":
            phase3d.get("production_model_candidate_created") is False,
        "deployable_model_artifact_written_false":
            phase3d.get("deployable_model_artifact_written") is False,
        "d_drive_read_false": phase3d.get("d_drive_read") is False,
        "network_used_false": phase3d.get("network_used") is False,
        "vendor_api_called_false": phase3d.get("vendor_api_called") is False,
        "data_purchase_made_false": phase3d.get("data_purchase_made") is False,
    }
    checks["all_confirmed"] = all(checks.values())
    return checks


def confirm_phase3c(phase3c):
    """Confirm the upstream Phase 3-C kill switch (why this track is needed)."""
    if not phase3c:
        return {"all_confirmed": False, "phase3c_present": False}
    rec = phase3c.get("recommendation", {}) or {}
    checks = {
        "phase3c_present": True,
        "phase_is_3c": phase3c.get("phase") == "3-C",
        "recommendation_is_kill_switch":
            rec.get("recommendation") == "PRICE_VOLUME_ONLY_KILL_SWITCH_TRIGGERED",
        "kill_switch_triggered_true": rec.get("kill_switch_triggered") is True,
    }
    checks["all_confirmed"] = all(checks.values())
    return checks


def build_phase3c_kill_switch_summary(phase3c, confirmed_3c):
    ks = (phase3c or {}).get("kill_switch_evaluation", {}) or {}
    rec = (phase3c or {}).get("recommendation", {}) or {}
    return {
        "phase3c_confirmed": confirmed_3c,
        "recommendation": rec.get("recommendation"),
        "kill_switch_triggered": rec.get("kill_switch_triggered"),
        "primary_horizon_days": ks.get("primary_horizon", 21),
        "primary_mean_rank_ic": ks.get("primary_mean_rank_ic"),
        "primary_catastrophic_fold_count": ks.get("primary_catastrophic_fold_count"),
        "why_this_track_is_necessary": (
            "Phase 3-C's kill switch triggered: the refined price/volume-only ridge had a near-zero "
            "mean rank IC, a sub-0.60 fold win rate, and catastrophic folds once the risk tilt was "
            "neutralized. There is no robust residual price/volume alpha, so the next edge must come "
            "from structured external data - fundamentals, earnings, and analyst revisions."),
    }


def build_selected_track_summary(phase3d):
    sel = (phase3d or {}).get("selected_data_track", {}) or {}
    return {
        "track_name": sel.get("track_name", PHASE3D_EXPECTED_TRACK_NAME),
        "components": ["fundamentals", "earnings_events", "analyst_estimates_revisions"],
        "component_data_families": sel.get(
            "component_data_families",
            ["Fundamentals", "Earnings events", "Analyst estimates and revisions"]),
        "includes_fundamentals": True,
        "includes_earnings": True,
        "includes_estimate_revisions": True,
        "source": "Phase 3-D selected_data_track",
    }


# --------------------------------------------------------------------------- #
# Decision logic
# --------------------------------------------------------------------------- #
def decide(confirmed_3d, feasibility):
    """Apply the conservative Phase 3-E decision logic."""
    if not confirmed_3d.get("all_confirmed"):
        return REC_BLOCKED

    schema_clear = feasibility["scores"]["schema_completeness"] >= 4
    pit_clear = feasibility["scores"]["point_in_time_feasibility"] >= 3
    free_proto = feasibility["free_fundamentals_prototype_possible"]
    revisions_pending = feasibility["estimates_revisions_need_provider"]
    paid_only_blocker = feasibility["as_reported_paid_only_blocker"]
    too_uncertain = feasibility["provider_capability_too_uncertain_to_prototype"]

    if too_uncertain:
        return REC_MANUAL
    if paid_only_blocker:
        return REC_PAID_PIT
    if schema_clear and pit_clear and free_proto and revisions_pending:
        # Fundamentals can be prototyped free; earnings/revisions provider still needs selection.
        return REC_VENDOR_FREE
    if schema_clear and pit_clear and free_proto and not revisions_pending:
        return REC_SEC_ONLY
    return REC_MANUAL


def build_recommendation(recommendation, selected_summary):
    selected_track = selected_summary["track_name"]
    fields = {
        REC_VENDOR_FREE: dict(
            provider_selection_required=True, free_source_prototype_allowed=True,
            reason=(
                "The schema and point-in-time rules are clear and a free fundamentals prototype "
                "can be built from SEC filing-acceptance data, but clean point-in-time earnings "
                "consensus and analyst revisions still need a selected provider. Proceed to Phase "
                "3-F to choose the first no/low-cost fundamentals source and build a read-only local "
                "prototype for a tiny sample, while separately documenting what is still missing for "
                "earnings and analyst revisions. No model is trained, no data is purchased, no "
                "vendor API is called, and no production edge is claimed.")),
        REC_SEC_ONLY: dict(
            provider_selection_required=False, free_source_prototype_allowed=True,
            reason=(
                "Fundamentals can be prototyped from free SEC data, but earnings consensus and "
                "analyst revisions are not feasible without paid data, so the first prototype is "
                "SEC fundamentals only, explicitly excluding consensus and revisions until a "
                "provider is selected.")),
        REC_PAID_PIT: dict(
            provider_selection_required=True, free_source_prototype_allowed=False,
            reason=(
                "As-reported point-in-time fundamentals and revisions are mandatory before any "
                "useful model can be built, so a paid point-in-time vendor must be compared and "
                "chosen before writing ingestion code or training anything.")),
        REC_MANUAL: dict(
            provider_selection_required=True, free_source_prototype_allowed=False,
            reason=(
                "Provider capability, pricing, and licensing are too uncertain to write an "
                "ingestion prototype, so manual vendor research must come first.")),
        REC_BLOCKED: dict(
            provider_selection_required=False, free_source_prototype_allowed=False,
            reason=(
                "Phase 3-D did not route here or a required input is missing, so the fundamentals / "
                "earnings feasibility is blocked pending repaired inputs.")),
    }[recommendation]
    return {
        "recommendation": recommendation,
        "allowed_values": ALLOWED_RECOMMENDATIONS,
        "create_production_model_candidate_now": False,
        "train_production_model_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "selected_track": selected_track,
        "provider_selection_required": fields["provider_selection_required"],
        "free_source_prototype_allowed": fields["free_source_prototype_allowed"],
        "feasibility_phase_only": True,
        "reason": fields["reason"],
    }


def build_recommended_next_phase(recommendation):
    table = {
        REC_VENDOR_FREE: (
            "Fundamentals Source Selection and Free Prototype",
            "Choose the first no/low-cost fundamentals source, build a read-only local prototype "
            "downloader / normalizer for a tiny sample only, and separately document what is still "
            "missing for earnings and analyst revisions."),
        REC_SEC_ONLY: (
            "SEC Fundamentals Prototype",
            "Build a small, read-only local SEC-style fundamentals prototype for a tiny ticker "
            "sample, while explicitly excluding earnings consensus and analyst revisions until a "
            "provider is selected."),
        REC_PAID_PIT: (
            "Paid Point-in-Time Vendor Decision",
            "Compare vendor options and cost before writing any ingestion code or training any "
            "model."),
        REC_MANUAL: (
            "Manual Vendor Research for Fundamentals and Revisions",
            "Research current providers, pricing, licensing, point-in-time support, and historical "
            "coverage before building ingestion code."),
        REC_BLOCKED: (
            "Repair Phase 3-E Inputs",
            "Repair missing Phase 3-D / Phase 3-C artifacts before continuing."),
    }
    title, purpose = table[recommendation]
    return {"phase": "3-F", "title": title, "purpose": purpose}


def build_implementation_plan(recommendation):
    return [
        "Phase 3-F (free prototype): build a read-only local downloader / normalizer for SEC "
        "fundamentals on a tiny ticker sample; write only to research/output; no production DB.",
        "Normalize SEC filings into the company_identity + fundamentals tables with accepted_datetime "
        "as the availability_datetime; preserve first-reported-vs-restated values.",
        "Separately document the earnings-events and analyst-revisions gap and the candidate "
        "providers to research manually (see the provider requirements matrix).",
        "Only after a provider is selected and licensed, extend ingestion to earnings_events and "
        "analyst_estimates_revisions with point-in-time consensus snapshots.",
        "Build trailing-only cross-sectional features (valuation, quality, growth, leverage, "
        "earnings surprise / drift, revision momentum / dispersion) aligned to the 128-ticker panel.",
        "Validate any feature under the existing embargoed walk-forward harness against the "
        "model-free composite must-beat benchmark before any model is considered.",
        "Keep every step research-only and preview-only: no model training, no production model "
        "candidate, no deployable artifact, and no production integration in this track.",
    ]


def build_risks_and_caveats():
    return [
        "Restatement / point-in-time risk: fundamentals are frequently restated; only first-reported "
        "values with true availability_datetime are safe historically.",
        "Consensus-snapshot leakage: earnings surprise must use the pre-announcement consensus, and "
        "revision snapshots must be timestamped to when the estimate changed.",
        "Cost risk: clean point-in-time earnings consensus and analyst revisions usually require a "
        "paid provider; the free SEC prototype covers fundamentals only.",
        "Coverage gaps: some fields or earlier history may be missing for parts of the 128-ticker "
        "universe, biasing cross-sectional ranks.",
        "Licensing: provider terms may restrict local storage or redistribution and must be cleared "
        "before any acquisition.",
        "Survivorship bias persists: the universe and sector map are current-as-of, so any "
        "downstream result remains survivorship-caveated.",
        "Provider-availability uncertainty: this blueprint asserts no current vendor pricing or "
        "availability; those must be confirmed by manual research in Phase 3-F.",
    ]


def build_interpretation(recommendation):
    return {
        "feasibility_phase_only": True,
        "price_volume_only_modeling_stopped": True,
        "model_trained": False,
        "research_model_trained": False,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "deployable_model_artifact_written": False,
        "external_data_ingested": False,
        "data_fetched_from_network": False,
        "data_purchased": False,
        "data_vendor_called": False,
        "read_from_d_drive": False,
        "wrote_to_d_drive": False,
        "production_edge_claimed": False,
        "results_context_remains_survivorship_biased": True,
        "narrative": (
            "Phase 3-E is a disciplined feasibility / planning artifact. It read the committed Phase "
            "3-D decision and Phase 3-C kill-switch result plus the current-as-of sector map, "
            "confirmed Phase 3-D routed here and the Phase 3-C kill switch triggered, and produced a "
            "vendor-agnostic schema catalog, point-in-time rules, minimum coverage gates, a provider "
            "requirements / evaluation template, an ingestion contract, a feasibility score, and a "
            "Phase 3-F recommendation. It fetched nothing from the network, called no data vendor, "
            "purchased no data, ingested no external data, trained no model, created no production "
            "model candidate, wrote no deployable model artifact, read nothing on the D: drive, wrote "
            "nothing to the D: drive, touched no database, ran no migration, restarted no prediction "
            "service, enabled no serving flag, placed no orders, and traded nothing. The blueprint is "
            "a feasibility direction only and claims no production edge."),
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
        "external_data_ingested": False,
    }


# --------------------------------------------------------------------------- #
# Assembly + writers
# --------------------------------------------------------------------------- #
def build_feasibility(phase3d, phase3c, sector_stats):
    confirmed_3d = confirm_phase3d(phase3d)
    confirmed_3c = confirm_phase3c(phase3c)

    catalog = build_schema_catalog()
    feasibility = build_feasibility_scores()
    contract = build_ingestion_contract()

    recommendation = decide(confirmed_3d, feasibility)

    result = {
        "phase": PHASE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "phase3d_decision_json": "research/output/phase3d_external_data_decision.json",
            "phase3d_recommended_track_json": "research/output/phase3d_recommended_data_track.json",
            "phase3d_option_matrix_csv": "research/output/phase3d_external_data_option_matrix.csv",
            "phase3c_json": "research/output/phase3c_refined_greenfield_rerun.json",
            "sector_map_csv": "research/input/phase2k_p_sector_map_current.csv",
        },
        "outputs_written": {
            "feasibility_json": "research/output/phase3e_fundamentals_earnings_feasibility.json",
            "schema_catalog_csv": "research/output/phase3e_external_data_schema_catalog.csv",
            "provider_requirements_matrix_csv":
                "research/output/phase3e_provider_requirements_matrix.csv",
            "ingestion_contract_json": "research/output/phase3e_ingestion_contract.json",
        },
        "phase3d_summary": {
            "phase3d_confirmed": confirmed_3d,
            "phase3d_recommendation": (phase3d or {}).get("recommendation", {}).get("recommendation"),
            "phase3d_selected_track": (phase3d or {}).get("selected_data_track", {}).get("track_name"),
            "phase3d_next_phase": (phase3d or {}).get("recommended_next_phase", {}),
            "sector_map": sector_stats,
        },
        "phase3c_kill_switch_summary": build_phase3c_kill_switch_summary(phase3c, confirmed_3c),
        "selected_track_summary": build_selected_track_summary(phase3d),
        "schema_catalog_summary": build_schema_catalog_summary(catalog),
        "point_in_time_rules": build_point_in_time_rules(),
        "minimum_coverage_gates": build_minimum_coverage_gates(),
        "provider_requirements_summary": build_provider_summary(),
        "ingestion_contract_summary": build_ingestion_contract_summary(contract),
        "feasibility_scores": feasibility,
        "implementation_plan": build_implementation_plan(recommendation),
        "risks_and_caveats": build_risks_and_caveats(),
        "recommendation": build_recommendation(recommendation, build_selected_track_summary(phase3d)),
        "interpretation": build_interpretation(recommendation),
        "recommended_next_phase": build_recommended_next_phase(recommendation),
        # Internal handles for the writers (not part of the public contract test set).
        "_schema_catalog": catalog,
        "_ingestion_contract": contract,
    }
    result.update(build_safety_flags())
    return result


def write_schema_catalog_csv(catalog, path):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SCHEMA_COLUMNS)
        w.writeheader()
        for r in catalog:
            w.writerow(r)


def write_provider_matrix_csv(path):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PROVIDER_COLUMNS)
        w.writeheader()
        for r in _PROVIDERS:
            w.writerow(r)


def write_ingestion_contract_json(contract, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(contract, f, indent=2)


def run(feasibility_path=FEASIBILITY_JSON, schema_catalog_path=SCHEMA_CATALOG_CSV,
        provider_matrix_path=PROVIDER_MATRIX_CSV, ingestion_contract_path=INGESTION_CONTRACT_JSON):
    phase3d = _load_json(PHASE3D_DECISION_JSON)
    phase3c = _load_json(PHASE3C_JSON)
    sector_stats = _sector_map_stats(SECTOR_MAP_CSV)

    result = build_feasibility(phase3d, phase3c, sector_stats)

    catalog = result.pop("_schema_catalog")
    contract = result.pop("_ingestion_contract")

    os.makedirs(os.path.dirname(feasibility_path), exist_ok=True)
    write_schema_catalog_csv(catalog, schema_catalog_path)
    write_provider_matrix_csv(provider_matrix_path)
    write_ingestion_contract_json(contract, ingestion_contract_path)
    with open(feasibility_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    return result


def main():
    result = run()
    rec = result["recommendation"]
    nxt = result["recommended_next_phase"]
    scs = result["schema_catalog_summary"]
    print(f"Phase {result['phase']} - Fundamentals and Earnings Data Feasibility")
    print(f"  phase3d confirmed        : {result['phase3d_summary']['phase3d_confirmed']['all_confirmed']}")
    print(f"  phase3c kill switch conf : {result['phase3c_kill_switch_summary']['phase3c_confirmed']['all_confirmed']}")
    print(f"  schema fields            : {scs['total_fields']} across {len(scs['data_families'])} families")
    print(f"  feasibility mean score   : {result['feasibility_scores']['mean_score']}")
    print(f"  recommendation           : {rec['recommendation']}")
    print(f"  provider selection req'd : {rec['provider_selection_required']}")
    print(f"  free prototype allowed   : {rec['free_source_prototype_allowed']}")
    print(f"  recommended next phase   : {nxt['phase']} - {nxt['title']}")
    return result


if __name__ == "__main__":
    main()
