"""Phase 11-A - Orthogonal Data Acquisition Decision Package.

WHY THIS PHASE EXISTS
    The autonomous owned-data alpha search is complete through Phase 10-Q. Every avenue for an alpha
    STRONGER than the frozen 10-D quality baseline (reweighting 10-L-B, incremental fundamentals 10-M,
    transforms/interactions 10-N, regimes 10-O) failed out-of-sample. Phase 10-Q's honest conclusion:
    the OWNED / LOCAL data (EODHD fundamentals + prices, FRED macro, benchmark regimes) is EXHAUSTED for
    a stronger edge, and a genuinely stronger alpha realistically needs NEW ORTHOGONAL data. This phase
    turns that conclusion into a rigorous, evidence-based DECISION PACKAGE for which paid / trial data
    feed to acquire FIRST, so the eventual (user opt-in) spend is aimed at the highest-probability family.

    This is a RESEARCH / DESIGN phase ONLY. It builds no panel, fits nothing, acquires no data, probes no
    provider, calls no API, and requires no key. It scores six candidate orthogonal data families on a
    transparent multi-criteria scorecard, ranks them, recommends the first acquisition, enumerates the
    exact analyst-estimate-revision fields required, and specifies the Phase 11-B trial test plan and
    acceptance criteria.

PRIMARY CANDIDATE
    1. Analyst estimate revisions (expected first pick).
SECONDARY CANDIDATES
    2. Short interest / securities lending.
    3. Options-derived sentiment / implied-volatility skew.
    4. Insider transactions.
    5. Institutional ownership / 13F changes.
    6. News / event sentiment with historical point-in-time coverage.

DECISION
    ANALYST_REVISIONS_FIRST | SHORT_INTEREST_FIRST | OPTIONS_DATA_FIRST | SENTIMENT_DATA_FIRST |
    PAUSE_NO_DATA_BUDGET

CONSTRAINTS HONORED
    Fully offline. No API calls. No provider probing. No key required. Owned-data search already
    exhausted (10-Q). No new data acquired. No Paper Trader writes. No orders. No automation. No broker.
    No deploy. No GCP. No package install. Targeted tests only. Output is research metadata only.
    No commit unless targeted tests pass and only phase11a files are staged. No push.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PHASE = "11-A"
PHASE_NAME = "Orthogonal Data Acquisition Decision Package"
STEM = "phase11a_orthogonal_data_acquisition_decision_package"
PERFORMS_NETWORK = False
_OUT = _REPO_ROOT / "research" / "output"

DEC_ANALYST = "ANALYST_REVISIONS_FIRST"
DEC_SHORT = "SHORT_INTEREST_FIRST"
DEC_OPTIONS = "OPTIONS_DATA_FIRST"
DEC_SENTIMENT = "SENTIMENT_DATA_FIRST"
DEC_PAUSE = "PAUSE_NO_DATA_BUDGET"
ALLOWED_DECISIONS = (DEC_ANALYST, DEC_SHORT, DEC_OPTIONS, DEC_SENTIMENT, DEC_PAUSE)

# Map a family key -> the decision enum that selecting it FIRST would produce. Families with no enum
# (insider, institutional) can only ever be deferred; if one ranked #1 the phase pauses for budget.
FAMILY_TO_DECISION = {
    "analyst_estimate_revisions": DEC_ANALYST,
    "short_interest_securities_lending": DEC_SHORT,
    "options_iv_skew": DEC_OPTIONS,
    "news_event_sentiment_pit": DEC_SENTIMENT,
}

# ---------------------------------------------------------------------------------------------------
# Multi-criteria scorecard. Each axis is scored 1-5 (5 = most favourable for finding a STRONG,
# cost-robust, quality-orthogonal alpha at our primary 63d/quarterly horizon). Weights sum to 1.0 and
# deliberately over-weight orthogonality, economic rationale, prior-phase evidence, and 63d horizon fit.
# ---------------------------------------------------------------------------------------------------
AXES: Tuple[str, ...] = (
    "economic_rationale_strength",
    "orthogonality_to_baseline",
    "prior_phase_evidence",
    "horizon_fit_63d",
    "update_frequency_suitability",
    "coverage_sp500",
    "historical_depth_availability",
    "survivorship_risk_low",         # 5 = low survivorship/PIT-restatement risk
    "cost_entitlement_accessibility",  # 5 = cheap / retail-accessible
    "integration_simplicity",        # 5 = simple to normalize + PIT-join
    "data_quality_robustness",       # 5 = clean / low look-ahead risk
)

WEIGHTS: Dict[str, float] = {
    "economic_rationale_strength": 0.12,
    "orthogonality_to_baseline": 0.16,
    "prior_phase_evidence": 0.12,
    "horizon_fit_63d": 0.14,
    "update_frequency_suitability": 0.06,
    "coverage_sp500": 0.08,
    "historical_depth_availability": 0.06,
    "survivorship_risk_low": 0.06,
    "cost_entitlement_accessibility": 0.08,
    "integration_simplicity": 0.06,
    "data_quality_robustness": 0.06,
}

# A family must clear this weighted composite (1-5 scale) to be worth recommending an acquisition at all;
# otherwise the honest call is PAUSE_NO_DATA_BUDGET.
MIN_COMPELLING_COMPOSITE = 3.0

# ---------------------------------------------------------------------------------------------------
# The six candidate orthogonal data families, each fully evaluated across the required dimensions.
# ---------------------------------------------------------------------------------------------------
FAMILY_EVALS: Tuple[Dict, ...] = (
    {
        "key": "analyst_estimate_revisions",
        "name": "Analyst estimate revisions",
        "is_primary_candidate": True,
        "economic_rationale": (
            "Revisions momentum / post-earnings-announcement drift: sell-side estimate changes are a "
            "slow-diffusing, well-documented predictor of subsequent returns. Prices under-react to "
            "estimate revisions, so a long-rising / short-falling revisions book earns drift over weeks "
            "to a quarter."),
        "orthogonality_to_fcf_accruals": (
            "HIGH. fcf_to_assets and operating_accruals are REALIZED, backward-looking fundamental "
            "LEVELS; estimate revisions are FORWARD-looking EXPECTATION CHANGES. Expected low overlap - "
            "this is the most orthogonal of the six to the quality baseline."),
        "update_frequency": "Daily-to-weekly consensus updates; clusters around earnings and guidance.",
        "useful_horizons": {"5d": "moderate", "21d": "strong", "63d": "strong (primary fit)"},
        "required_pit_fields": [
            "consensus EPS estimate (CFY / NFY / current quarter)", "revenue estimate (if available)",
            "number of analysts", "upward revisions count", "downward revisions count",
            "7d / 30d / 60d estimate change", "estimate dispersion", "point-in-time effective date",
            "revision timestamp (if available)"],
        "required_historical_depth": (
            ">= 10 years of TRUE point-in-time snapshots (2013+ to cover both the pre-2020 and post-2020 "
            "subperiod guards) - restated-latest history is NOT acceptable."),
        "required_universe_coverage": (
            "Near-universal for S&P 500; expect >= 95% of the 545-name expanded universe (large caps are "
            "densely covered by the sell-side)."),
        "survivorship_bias_risks": (
            "MEDIUM. Vendors often serve restated / survivor-only consensus; must confirm PIT snapshots "
            "include names later delisted and un-restated estimates as-of each date."),
        "cost_entitlement_risk": (
            "MEDIUM. Affordable retail/mid options exist (EODHD estimates add-on, Zacks via Nasdaq Data "
            "Link, Finnhub); enterprise PIT (I/B/E/S, FactSet) is expensive but not required for a trial."),
        "integration_complexity": (
            "LOW-MEDIUM. Normalizes cleanly to ticker/available_date/value; main work is fiscal-period "
            "alignment and a genuine as-of PIT join (available_date <= entry_date)."),
        "data_quality_checks": [
            "no look-ahead: every value carries a vendor effective/as-of date <= panel entry_date",
            "restatement test: consensus as-of an old date is not silently the latest restated number",
            "fiscal-period mapping (CFY vs calendar) is explicit",
            "identifier crosswalk to Norgate permatickers has >= 90% match",
            "num_analysts >= k filter to drop thin/stale consensus"],
        "mvp_trial_acceptance": (
            "oriented net-revisions factor: 63d IC t >= 2.0, both cohorts +, both subperiods +, and it "
            "adds quarterly net-25bps over composite_sn without worsening net50 / turnover / concentration."),
        "scores": {
            "economic_rationale_strength": 5, "orthogonality_to_baseline": 5, "prior_phase_evidence": 5,
            "horizon_fit_63d": 5, "update_frequency_suitability": 4, "coverage_sp500": 5,
            "historical_depth_availability": 4, "survivorship_risk_low": 3,
            "cost_entitlement_accessibility": 4, "integration_simplicity": 4, "data_quality_robustness": 3},
    },
    {
        "key": "short_interest_securities_lending",
        "name": "Short interest / securities lending",
        "is_primary_candidate": False,
        "economic_rationale": (
            "Short-side crowding: high / rising short interest and rich borrow fees proxy informed "
            "negative views (predictive of underperformance) but also squeeze risk. Days-to-cover and "
            "utilization add a positioning dimension the quality baseline lacks."),
        "orthogonality_to_fcf_accruals": (
            "MEDIUM-HIGH. Positioning/flow rather than fundamental level; but short interest correlates "
            "with low-quality / high-accrual names, so partial overlap with the accruals short leg."),
        "update_frequency": (
            "FINRA settlement short interest ~twice a month WITH a reporting lag; daily securities-lending "
            "fee/utilization only via paid feeds."),
        "useful_horizons": {"5d": "weak", "21d": "moderate", "63d": "moderate"},
        "required_pit_fields": [
            "short interest shares", "short interest % of float", "days-to-cover", "borrow fee / rebate",
            "utilization", "settlement date", "publication/effective date"],
        "required_historical_depth": ">= 10 years; borrow-fee history is shorter and paid.",
        "required_universe_coverage": "Full for S&P 500 (FINRA); borrow-fee coverage thinner / paid.",
        "survivorship_bias_risks": "LOW-MEDIUM. FINRA files are dated; paid SL history may be survivor-only.",
        "cost_entitlement_risk": (
            "MEDIUM-HIGH. FINRA short interest is free but lagged/bimonthly; real-time securities-lending "
            "fee data is expensive. Polygon full-universe short interest already FAILED the gate in 10-A."),
        "integration_complexity": "MEDIUM. Bimonthly cadence must be forward-filled with lag; SL feeds messy.",
        "data_quality_checks": [
            "settlement-date vs publication-date lag applied (no look-ahead)",
            "% of float denominator source consistent", "corporate-action share adjustments",
            "borrow-fee outliers / hard-to-borrow flags handled"],
        "mvp_trial_acceptance": (
            "days-to-cover / SI%float factor: 63d cost-robust L/S positive, both eras +, and adds over "
            "composite_sn - but 10-A already showed price-derived short interest is weak (best t=1.56)."),
        "scores": {
            "economic_rationale_strength": 4, "orthogonality_to_baseline": 4, "prior_phase_evidence": 3,
            "horizon_fit_63d": 3, "update_frequency_suitability": 2, "coverage_sp500": 5,
            "historical_depth_availability": 4, "survivorship_risk_low": 4,
            "cost_entitlement_accessibility": 3, "integration_simplicity": 3, "data_quality_robustness": 3},
    },
    {
        "key": "options_iv_skew",
        "name": "Options-derived sentiment / implied-volatility skew",
        "is_primary_candidate": False,
        "economic_rationale": (
            "Option-implied risk-neutral distribution: put/call skew, IV term structure, and risk "
            "reversals encode informed positioning and crash-risk pricing that precede returns and "
            "realized-vol changes."),
        "orthogonality_to_fcf_accruals": (
            "HIGH. Forward-looking, market-microstructure driven; essentially unrelated to realized "
            "fundamental levels - very orthogonal to the quality baseline."),
        "update_frequency": "Intraday / daily.",
        "useful_horizons": {"5d": "strong", "21d": "moderate", "63d": "weak-moderate (decays fast)"},
        "required_pit_fields": [
            "implied vol by delta/strike/expiry", "25-delta put-call skew", "IV term-structure slope",
            "risk reversal", "put/call open interest & volume", "quote date"],
        "required_historical_depth": ">= 10 years of clean option surfaces (expensive).",
        "required_universe_coverage": (
            "Good for liquid large caps; THIN for parts of the expanded S&P universe where single-name "
            "options are illiquid or absent."),
        "survivorship_bias_risks": "MEDIUM. Surface history for delisted names often dropped.",
        "cost_entitlement_risk": (
            "HIGH. Historical option surfaces (ORATS, CBOE DataShop, OptionMetrics) are among the most "
            "expensive datasets - poor fit for a cheap first trial."),
        "integration_complexity": (
            "HIGH. Requires surface construction, greeks, expiry interpolation, liquidity filtering - the "
            "heaviest integration of the six."),
        "data_quality_checks": [
            "stale/crossed quotes filtered", "min open-interest / volume liquidity screen",
            "surface interpolation sanity", "IV outlier clipping", "corporate-action strike adjustment"],
        "mvp_trial_acceptance": (
            "skew factor: works best at 5-21d; must still show a cost-robust 63d edge to justify the "
            "quarterly cadence - unlikely given fast decay + heavy cost."),
        "scores": {
            "economic_rationale_strength": 4, "orthogonality_to_baseline": 5, "prior_phase_evidence": 3,
            "horizon_fit_63d": 2, "update_frequency_suitability": 5, "coverage_sp500": 4,
            "historical_depth_availability": 3, "survivorship_risk_low": 4,
            "cost_entitlement_accessibility": 2, "integration_simplicity": 2, "data_quality_robustness": 3},
    },
    {
        "key": "insider_transactions",
        "name": "Insider transactions (Form 4)",
        "is_primary_candidate": False,
        "economic_rationale": (
            "Officers/directors have an information edge; net insider buying (esp. cluster buys) predicts "
            "outperformance over subsequent months."),
        "orthogonality_to_fcf_accruals": (
            "MEDIUM-HIGH. Behavioural/informed-trading signal; largely orthogonal to fundamental levels."),
        "update_frequency": "Event-driven (Form 4 within ~2 business days of trade); SPARSE per name.",
        "useful_horizons": {"5d": "weak", "21d": "moderate", "63d": "moderate-strong"},
        "required_pit_fields": [
            "filer role", "transaction code (P/S)", "shares & price", "post-transaction holdings",
            "transaction date", "filing/acceptance date"],
        "required_historical_depth": ">= 10 years (SEC EDGAR full-text is available and cheap).",
        "required_universe_coverage": (
            "Universal filing requirement, but SPARSE: many names have no qualifying insider trade in a "
            "given quarter, so cross-sectional coverage per rebalance is low."),
        "survivorship_bias_risks": "LOW. EDGAR filings are permanent and dated.",
        "cost_entitlement_risk": "LOW. EDGAR is free; cost is parsing/normalization effort.",
        "integration_complexity": "MEDIUM. Form 4 parsing, clustering, and sparsity handling.",
        "data_quality_checks": [
            "10b5-1 planned-sale flagging", "cluster vs single-filer aggregation",
            "filing-date (not trade-date) used for availability", "share adjustments"],
        "mvp_trial_acceptance": (
            "net-insider-buying factor: months with signal too sparse for a full cross-section; likely a "
            "sub-universe overlay rather than a standalone 63d book."),
        "scores": {
            "economic_rationale_strength": 3, "orthogonality_to_baseline": 4, "prior_phase_evidence": 3,
            "horizon_fit_63d": 4, "update_frequency_suitability": 3, "coverage_sp500": 3,
            "historical_depth_availability": 4, "survivorship_risk_low": 4,
            "cost_entitlement_accessibility": 4, "integration_simplicity": 3, "data_quality_robustness": 3},
    },
    {
        "key": "institutional_ownership_13f",
        "name": "Institutional ownership / 13F changes",
        "is_primary_candidate": False,
        "economic_rationale": (
            "Smart-money accumulation: rising institutional / hedge-fund ownership can precede "
            "outperformance; concentration and breadth of holders add a positioning dimension."),
        "orthogonality_to_fcf_accruals": "MEDIUM. Positioning; partly driven by the same fundamentals.",
        "update_frequency": "QUARTERLY with a 45-day filing lag - the worst point-in-time timeliness.",
        "useful_horizons": {"5d": "weak", "21d": "weak", "63d": "weak-moderate"},
        "required_pit_fields": [
            "holder count", "shares held by institutions", "% ownership", "quarter-over-quarter change",
            "report (period-end) date", "filing date"],
        "required_historical_depth": ">= 10 years (EDGAR 13F).",
        "required_universe_coverage": "Broad, but reflects only long positions of >$100M managers.",
        "survivorship_bias_risks": "MEDIUM. Restated/amended 13Fs; no short positions.",
        "cost_entitlement_risk": "LOW-MEDIUM. EDGAR free; clean aggregation products are paid.",
        "integration_complexity": (
            "HIGH. 13F reconciliation (CUSIP mapping, amendments, double-counting) is notoriously messy; "
            "the 45-day lag severely limits PIT signal value."),
        "data_quality_checks": [
            "45-day filing lag enforced (no look-ahead)", "amendment handling",
            "CUSIP->ticker mapping accuracy", "long-only bias acknowledged"],
        "mvp_trial_acceptance": (
            "ownership-change factor at 63d after enforcing the 45-day lag - the lag likely kills most "
            "tradable signal; low expected value."),
        "scores": {
            "economic_rationale_strength": 3, "orthogonality_to_baseline": 4, "prior_phase_evidence": 2,
            "horizon_fit_63d": 3, "update_frequency_suitability": 1, "coverage_sp500": 5,
            "historical_depth_availability": 4, "survivorship_risk_low": 3,
            "cost_entitlement_accessibility": 4, "integration_simplicity": 2, "data_quality_robustness": 2},
    },
    {
        "key": "news_event_sentiment_pit",
        "name": "News / event sentiment (point-in-time)",
        "is_primary_candidate": False,
        "economic_rationale": (
            "Soft information: news-flow sentiment and event intensity capture information not yet in "
            "fundamentals; short-horizon drift after sentiment shocks."),
        "orthogonality_to_fcf_accruals": "MEDIUM-HIGH. Text-derived; orthogonal to fundamental levels.",
        "update_frequency": "Continuous / intraday.",
        "useful_horizons": {"5d": "strong", "21d": "moderate", "63d": "weak (decays fast)"},
        "required_pit_fields": [
            "article/event timestamp", "entity-mapped sentiment score", "event type/novelty",
            "source", "volume of coverage", "as-of ingestion date"],
        "required_historical_depth": (
            ">= 10 years of TRUE PIT text with timestamps - label leakage / look-ahead is the main hazard."),
        "required_universe_coverage": "Good for large caps; noisier for thin-coverage names.",
        "survivorship_bias_risks": "MEDIUM. Archives may drop delisted-name coverage.",
        "cost_entitlement_risk": "MEDIUM. PIT news history + NLP scoring is a paid product.",
        "integration_complexity": (
            "HIGH. Entity resolution, dedup, timestamp/timezone handling, and NLP normalization."),
        "data_quality_checks": [
            "publication timestamp (not crawl time) used for availability", "entity-mapping precision",
            "dedup of syndicated stories", "look-ahead / label-leakage audit"],
        "mvp_trial_acceptance": (
            "sentiment-momentum factor: strongest at 5d; must show a 63d cost-robust edge to fit quarterly "
            "cadence - the owned EODHD news sentiment was already WEAK in the 8-series."),
        "scores": {
            "economic_rationale_strength": 3, "orthogonality_to_baseline": 4, "prior_phase_evidence": 2,
            "horizon_fit_63d": 2, "update_frequency_suitability": 5, "coverage_sp500": 4,
            "historical_depth_availability": 3, "survivorship_risk_low": 3,
            "cost_entitlement_accessibility": 3, "integration_simplicity": 2, "data_quality_robustness": 2},
    },
)

# ---------------------------------------------------------------------------------------------------
# Exact analyst-estimate-revision fields required (the user-specified field list, made precise).
# columns: field_key, field_label, category, pit_required, cadence, priority, notes
# ---------------------------------------------------------------------------------------------------
ANALYST_REVISION_FIELDS: Tuple[Tuple[str, str, str, str, str, str, str], ...] = (
    ("eps_estimate_cfy", "Current fiscal year EPS estimate", "consensus_level", "yes", "daily-weekly",
     "required", "mean consensus for the current fiscal year"),
    ("eps_estimate_nfy", "Next fiscal year EPS estimate", "consensus_level", "yes", "daily-weekly",
     "required", "mean consensus for the next fiscal year"),
    ("eps_estimate_quarter", "Quarterly EPS estimate", "consensus_level", "yes", "daily-weekly",
     "required", "current-quarter consensus; anchors post-earnings drift"),
    ("revenue_estimate", "Revenue estimate (if available)", "consensus_level", "yes", "daily-weekly",
     "preferred", "sales consensus; enables revisions on a second line item"),
    ("num_analysts", "Number of analysts", "coverage", "yes", "daily-weekly", "required",
     "coverage count; normalizer for revision diffusion and dispersion"),
    ("up_revisions_count", "Upward revisions count", "revision_flow", "yes", "daily-weekly", "required",
     "e.g. estimates raised in last 30d; core numerator of net-revisions"),
    ("down_revisions_count", "Downward revisions count", "revision_flow", "yes", "daily-weekly",
     "required", "estimates cut in last 30d; core denominator of net-revisions"),
    ("estimate_change_7d", "7-day estimate change", "revision_momentum", "yes", "daily", "required",
     "short-window consensus change"),
    ("estimate_change_30d", "30-day estimate change", "revision_momentum", "yes", "daily", "required",
     "primary revision-momentum window"),
    ("estimate_change_60d", "60-day estimate change", "revision_momentum", "yes", "daily", "required",
     "aligns with the 63d primary horizon"),
    ("consensus_estimate_level", "Consensus estimate level", "consensus_level", "yes", "daily-weekly",
     "required", "mean estimate level; base for percentage revisions"),
    ("estimate_dispersion", "Estimate dispersion", "uncertainty", "yes", "daily-weekly", "preferred",
     "stdev/coeff-of-variation of estimates; conditions signal strength"),
    ("recommendation_changes", "Recommendation changes (if available)", "rating_flow", "yes", "event",
     "optional", "up/downgrades; secondary confirming signal"),
    ("price_target_changes", "Price target changes (if available)", "target_flow", "yes", "event",
     "optional", "target revisions; secondary confirming signal"),
    ("pit_effective_date", "Point-in-time effective date", "pit_key", "yes", "per-observation", "required",
     "THE as-of join key: available_date <= panel entry_date"),
    ("revision_timestamp", "Announcement / revision timestamp (if available)", "pit_key", "yes",
     "per-event", "preferred", "intraday timestamp; same-day treated as available next session"),
)

# ---------------------------------------------------------------------------------------------------
# Phase 11-B trial test plan (ordered) and its acceptance criteria.
# ---------------------------------------------------------------------------------------------------
PHASE11B_TEST_PLAN: Tuple[str, ...] = (
    "STEP 0 (zero-cost pre-check): inspect the OWNED EODHD raw fundamentals JSONs for an Earnings::Trend "
    "block (epsTrend / epsRevisions up/down) to gauge field shape and vendor conventions BEFORE any "
    "purchase - a current snapshot only, NOT usable as history, but confirms mapping and format for free.",
    "STEP 1: ingest ONE provider trial / export as a local offline file (no live API, no key) into "
    "research/data/analyst_revisions/raw/.",
    "STEP 2: normalize to ticker/available_date/value CSVs under research/data/analyst_revisions/"
    "normalized/<field>.csv (one file per revision field).",
    "STEP 3: align point-in-time to the existing 545-name earnings panel via an as-of join "
    "(available_date <= entry_date) - reuse the y8 orthogonal-feature attach pattern; zero look-ahead.",
    "STEP 4: build revision-momentum factors: net_revisions=(up-down)/num_analysts, standardized "
    "estimate_change_30d/60d, and a diffusion/breadth score.",
    "STEP 5: sector-neutral rank each factor (within-month z, then sector demean).",
    "STEP 6: test at 5d, 21d and 63d horizons (primary = 63d/quarterly).",
    "STEP 7: test STANDALONE (oriented IC, cohort/subperiod signs).",
    "STEP 8: test INCREMENTAL to composite_sn (does the blended book raise quarterly net-25bps at 63d "
    "without worsening net50 / turnover / concentration?).",
    "STEP 9: test COST-ADJUSTED long/short (quintile spread net-10/25/50bps at 63d).",
    "STEP 10: REJECT if no OOS stability - walk-forward pooled OOS IC must be positive, frac windows "
    "positive >= baseline, and the improvement must survive the subperiod-net25 generalization guard "
    "(favourable in BOTH pre-2020 and post-2020). Do NOT spin a single-period or single-sector result.",
)

# columns: criterion_id, phase11b_step, metric, pass_threshold, reject_condition
PHASE11B_ACCEPTANCE_CRITERIA: Tuple[Tuple[str, str, str, str, str], ...] = (
    ("AC1_ingest_mapping", "STEP 1-2", "trial tickers mapped to panel permatickers",
     ">= 90% mapped", "reject/fix crosswalk if < 90%"),
    ("AC2_normalize_integrity", "STEP 2", "duplicate (ticker, available_date) rows; date monotonicity",
     "0 duplicates; dates monotone", "reject if duplicates or unordered dates"),
    ("AC3_pit_no_lookahead", "STEP 3", "rows with available_date > entry_date",
     "0 look-ahead rows; panel-event coverage >= 60%", "reject if any look-ahead or coverage < 60%"),
    ("AC4_factor_nondegenerate", "STEP 4-5", "months with cross-sectional std > 0",
     ">= 90% of months non-degenerate", "reject if factor is mostly constant"),
    ("AC5_standalone_63d", "STEP 6-7", "oriented 63d IC t; cohort & subperiod signs",
     "IC t >= 2.0 AND both cohorts + AND both subperiods +", "reject if IC insignificant or sign-unstable"),
    ("AC6_incremental_net25", "STEP 8", "blended composite quarterly net-25bps vs composite_sn @63d",
     "net25 > baseline by a clear margin; net50 not worse; turnover <= 1.10x",
     "reject if it does not beat net25 or degrades net50/turnover"),
    ("AC7_cost_robust_ls", "STEP 9", "quintile L/S net-25bps and net-50bps @63d",
     "net25 > 0 AND net50 >= 0", "reject if cost-killed"),
    ("AC8_oos_stability", "STEP 10", "walk-forward pooled OOS IC; frac windows +; subperiod-net25 guard",
     "pooled OOS IC > 0 AND frac >= baseline AND favourable in BOTH eras",
     "REJECT_NO_OOS_STABILITY if any fails - do not spin"),
    ("AC9_concentration", "STEP 8-9", "top-sector share of the blended book",
     "not worse than composite_sn baseline", "reject if concentration materially worse"),
    ("AC10_no_overfit_guard", "STEP 4-10", "number of pre-registered revision-factor variants",
     "pre-registered factor list; subperiod guard applied to every candidate",
     "reject any post-hoc winner that fails the subperiod-net25 generalization guard"),
)

# columns: risk_id, risk, severity, likelihood, mitigation, phase_to_address
INTEGRATION_RISK_REGISTER: Tuple[Tuple[str, str, str, str, str, str], ...] = (
    ("R1_pit_lookahead", "Look-ahead leakage from using restated-latest instead of as-of values",
     "HIGH", "MEDIUM", "use only the vendor effective/as-of date; never the latest restated number",
     "11-B STEP 3"),
    ("R2_restatement_survivorship", "Vendor consensus history is survivor-only / silently restated",
     "HIGH", "MEDIUM", "require true PIT snapshots incl. delisted names; run a restatement test",
     "11-B STEP 1"),
    ("R3_fiscal_period_map", "CFY/NFY/quarter fiscal periods mis-aligned to calendar",
     "MEDIUM", "MEDIUM", "map fiscal-period-end dates explicitly per name", "11-B STEP 2"),
    ("R4_identifier_crosswalk", "Vendor ticker/CUSIP vs Norgate permaticker mismatch",
     "MEDIUM", "MEDIUM", "build and measure a crosswalk; require >= 90% match", "11-B STEP 1"),
    ("R5_currency_adr_scaling", "Currency / ADR EPS scaling inconsistencies",
     "LOW", "LOW", "normalize currency per listing; verify ADR ratios", "11-B STEP 2"),
    ("R6_coverage_gaps", "Thin coverage in the expanded S&P universe",
     "MEDIUM", "LOW", "measure coverage; restrict the test universe if too thin", "11-B STEP 3"),
    ("R7_corporate_actions", "Splits distort per-share estimate history",
     "MEDIUM", "MEDIUM", "use split-adjusted estimates; reconcile to price adjustments", "11-B STEP 2"),
    ("R8_entitlement_trial_expiry", "Trial entitlement lapses mid-test",
     "MEDIUM", "MEDIUM", "one bounded export snapshotted locally; no dependence on a live feed",
     "11-B STEP 1"),
    ("R9_timestamp_timezone", "Revision timestamp timezone / same-day ordering ambiguity",
     "LOW", "MEDIUM", "treat same-day revisions as available next session", "11-B STEP 3"),
    ("R10_cost_overrun", "Spend exceeds the trial budget",
     "MEDIUM", "LOW", "bounded trial + EXPLICIT user opt-in before any purchase", "pre-11-B"),
    ("R11_stale_consensus_noise", "Thin/stale consensus adds noise",
     "LOW", "MEDIUM", "require num_analysts >= k; drop thin names", "11-B STEP 4"),
    ("R12_variant_overfit", "Overfitting via many revision-factor variants",
     "HIGH", "MEDIUM", "pre-register the factor list; apply the subperiod-net25 guard to each",
     "11-B STEP 10"),
)

# Candidate vendors (DESIGN ASSUMPTIONS from prior-phase notes ONLY - no provider was probed or called).
# columns: vendor, data_family, pit_history_assumed, cost_tier, retail_accessible, requires_user_opt_in, notes
VENDOR_CANDIDATES: Tuple[Tuple[str, str, str, str, str, str, str], ...] = (
    ("EODHD (Fundamentals Earnings::Trend / Calendar)", "analyst_estimate_revisions",
     "partial-current-snapshot; PIT history = paid add-on (assumed)", "low-mid", "yes", "yes",
     "owned base vendor; recommended primary trial; owned JSONs may already carry Earnings::Trend "
     "revisions fields - check at zero cost (11-B STEP 0)"),
    ("Zacks (via Nasdaq Data Link EE)", "analyst_estimate_revisions", "yes (assumed)", "mid", "yes", "yes",
     "revisions-focused; classic academic estimate-revisions source"),
    ("Finnhub", "analyst_estimate_revisions", "partial (assumed)", "free-low", "yes", "yes",
     "mentioned in 8-Y; estimate/revision endpoints; verify PIT depth in a trial"),
    ("AlphaVantage (EARNINGS_ESTIMATES)", "analyst_estimate_revisions", "shallow (assumed)", "free", "yes",
     "yes", "rate-limited (10-A); likely too shallow for a 10-year PIT history"),
    ("LSEG / Refinitiv I/B/E/S", "analyst_estimate_revisions", "yes (gold-standard PIT)", "enterprise-high",
     "no", "yes", "best PIT estimates history; likely out of budget for a first trial"),
    ("FactSet Estimates", "analyst_estimate_revisions", "yes", "enterprise-high", "no", "yes",
     "enterprise-grade; expensive"),
    ("Polygon", "short_interest_securities_lending", "partial", "mid", "yes", "yes",
     "full-universe short interest already FAILED the 10-A gate (best t=1.56)"),
    ("ORATS / CBOE DataShop / OptionMetrics", "options_iv_skew", "yes", "high", "limited", "yes",
     "historical option surfaces are among the most expensive datasets"),
    ("SEC EDGAR (Form 4 / 13F)", "insider_transactions/institutional_ownership_13f", "yes (native filings)",
     "free", "yes", "yes", "free but parsing-heavy; 13F carries a 45-day lag that limits PIT value"),
)


# ---------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------
def _round(x, n: int = 4):
    try:
        return round(float(x), n)
    except (TypeError, ValueError):
        return x


def _write_json(path: Path, obj: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2)
        fh.write("\n")


def _write_csv(path: Path, header: Sequence, rows: Sequence[Sequence]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(list(header))
        for r in rows:
            w.writerow(list(r))


def _composite(scores: Dict[str, int]) -> float:
    return _round(sum(scores[a] * WEIGHTS[a] for a in AXES), 4)


def rank_families() -> List[Dict]:
    rows = []
    for fam in FAMILY_EVALS:
        comp = _composite(fam["scores"])
        rows.append({"key": fam["key"], "name": fam["name"],
                     "is_primary_candidate": fam["is_primary_candidate"],
                     "composite_score": comp, "scores": fam["scores"],
                     "maps_to_decision": FAMILY_TO_DECISION.get(fam["key"])})
    rows.sort(key=lambda r: (-r["composite_score"], r["name"]))
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return rows


def decide(ranked: List[Dict]) -> Tuple[str, str, Optional[Dict]]:
    # pick the highest-ranked family that maps to a decision enum and clears the compelling bar
    for r in ranked:
        dec = r.get("maps_to_decision")
        if dec and r["composite_score"] >= MIN_COMPELLING_COMPOSITE:
            rationale = (
                "%s ranks #1 of the six orthogonal families on the weighted scorecard (composite %.3f/5). "
                "It is the most orthogonal to the fcf_to_assets / operating_accruals quality baseline "
                "(forward-looking expectation changes vs realized levels), has the strongest economic "
                "rationale and prior-phase evidence (named the #1 new-data family in 8-Y / 10-A / 10-Q), "
                "fits the 63d/quarterly horizon, has near-universal S&P 500 coverage, and is accessible via "
                "an affordable trial. Acquire it FIRST (explicit user opt-in) and validate under the Phase "
                "11-B trial plan; reject if it fails the OOS / subperiod-net25 stability gate."
                % (r["name"], r["composite_score"]))
            return dec, rationale, r
    top = ranked[0] if ranked else None
    return DEC_PAUSE, ("no candidate family clears the compelling-evidence bar (composite >= %.1f/5) with a "
                       "mapped acquisition decision; pause and do not spend a data budget until the case is "
                       "stronger." % MIN_COMPELLING_COMPOSITE), top


def _family_block(fam: Dict, ranked_lookup: Dict[str, Dict]) -> Dict:
    r = ranked_lookup[fam["key"]]
    return {
        "key": fam["key"], "name": fam["name"], "is_primary_candidate": fam["is_primary_candidate"],
        "rank": r["rank"], "composite_score": r["composite_score"], "scores": fam["scores"],
        "maps_to_decision": FAMILY_TO_DECISION.get(fam["key"]),
        "economic_rationale": fam["economic_rationale"],
        "orthogonality_to_fcf_accruals": fam["orthogonality_to_fcf_accruals"],
        "update_frequency": fam["update_frequency"],
        "useful_horizons": fam["useful_horizons"],
        "required_pit_fields": fam["required_pit_fields"],
        "required_historical_depth": fam["required_historical_depth"],
        "required_universe_coverage": fam["required_universe_coverage"],
        "survivorship_bias_risks": fam["survivorship_bias_risks"],
        "cost_entitlement_risk": fam["cost_entitlement_risk"],
        "integration_complexity": fam["integration_complexity"],
        "data_quality_checks": fam["data_quality_checks"],
        "mvp_trial_acceptance": fam["mvp_trial_acceptance"],
    }


def write_artifacts(P: Path, ranked: List[Dict]) -> None:
    # data_family_scorecard.csv
    header = (["rank", "family_key", "family_name", "is_primary_candidate", "composite_score",
               "maps_to_decision"] + list(AXES))
    rows = [[r["rank"], r["key"], r["name"], r["is_primary_candidate"], r["composite_score"],
             r.get("maps_to_decision") or "-"] + [r["scores"][a] for a in AXES] for r in ranked]
    _write_csv(P / "data_family_scorecard.csv", header, rows)

    _write_csv(P / "vendor_candidate_scorecard.csv",
               ["vendor", "data_family", "pit_history_assumed", "cost_tier", "retail_accessible",
                "requires_user_opt_in", "no_probe_performed", "notes"],
               [[v[0], v[1], v[2], v[3], v[4], v[5], "true", v[6]] for v in VENDOR_CANDIDATES])

    _write_csv(P / "analyst_revisions_required_fields.csv",
               ["field_key", "field_label", "category", "pit_required", "cadence", "priority", "notes"],
               [list(f) for f in ANALYST_REVISION_FIELDS])

    _write_csv(P / "phase11b_trial_acceptance_criteria.csv",
               ["criterion_id", "phase11b_step", "metric", "pass_threshold", "reject_condition"],
               [list(c) for c in PHASE11B_ACCEPTANCE_CRITERIA])

    _write_csv(P / "integration_risk_register.csv",
               ["risk_id", "risk", "severity", "likelihood", "mitigation", "phase_to_address"],
               [list(r) for r in INTEGRATION_RISK_REGISTER])


def _baseline_context() -> Dict:
    # The modest incumbent that any new orthogonal factor must beat (frozen 10-D metrics). Best-effort
    # read of the owned 10-Q report for freshness; falls back to the known frozen values. No network.
    fallback = {"ic_t_63d": 2.665, "quarterly_net_25bps": 0.00401, "quarterly_net_50bps": 0.00095,
                "quarterly_turnover": 0.6115, "oos_frac_windows_positive": 0.5, "top_sector_share": 0.6262}
    try:
        p = (_OUT / "phase10q_owned_data_exhaustion_research_decision"
             / "phase10q_owned_data_exhaustion_research_decision.json")
        with open(p, "r", encoding="utf-8") as fh:
            b = (json.load(fh) or {}).get("baseline") or {}
        out = {k: b.get(k, fallback[k]) for k in fallback}
        return out
    except Exception:
        return dict(fallback)


def _build_report(decision: str, rationale: str, ranked: List[Dict], champion: Optional[Dict],
                  baseline: Dict, key_visible: bool) -> Dict:
    ranked_lookup = {r["key"]: r for r in ranked}
    family_blocks = [_family_block(fam, ranked_lookup) for fam in FAMILY_EVALS]
    family_blocks.sort(key=lambda b: b["rank"])
    deferred = [b for b in family_blocks if not champion or b["key"] != champion["key"]]
    return {
        "phase": PHASE, "phase_name": PHASE_NAME, "decision": decision,
        "decision_rationale": rationale, "allowed_decisions": list(ALLOWED_DECISIONS),
        "objective": ("rigorous, evidence-based decision package for which paid/trial orthogonal data "
                      "family to acquire FIRST to raise the chance of finding a stronger alpha - design "
                      "only; no acquisition, no probe, no API, no key"),
        "offline": True, "performs_network": PERFORMS_NETWORK,
        "eodhd_key_visible": bool(key_visible), "eodhd_key_required": False,
        "owned_data_search_status": ("EXHAUSTED - the owned/local EODHD fundamentals + prices, FRED macro "
                                     "and benchmark regimes were exhausted for a stronger edge through "
                                     "Phase 10-Q (10-L-B / 10-M / 10-N / 10-O all failed OOS)"),
        "input_inventory": [
            {"source": "Phase 10-Q owned-data-exhaustion decision", "role": "why new data is needed"},
            {"source": "Phase 10-D frozen quality baseline (composite_sn)", "role": "incumbent to beat"},
            {"source": "Phase 8-Y / 10-A orthogonal-data notes", "role": "prior evidence + vendor context"},
            {"source": "candidate data-family domain knowledge", "role": "scorecard inputs (no probing)"}],
        "scoring_model": {"axes": list(AXES), "weights": WEIGHTS,
                          "scale": "1-5 (5 = most favourable)",
                          "min_compelling_composite": MIN_COMPELLING_COMPOSITE,
                          "note": "weights over-weight orthogonality, rationale, prior evidence, and 63d fit"},
        "baseline": {"signal": "composite_sn",
                     "weighting": "equal (fcf+ / accruals-), sector-neutral, 63d quarterly",
                     "ic_t_63d": baseline.get("ic_t_63d"),
                     "quarterly_net_25bps": baseline.get("quarterly_net_25bps"),
                     "quarterly_net_50bps": baseline.get("quarterly_net_50bps"),
                     "quarterly_turnover": baseline.get("quarterly_turnover"),
                     "oos_frac_windows_positive": baseline.get("oos_frac_windows_positive"),
                     "top_sector_share": baseline.get("top_sector_share"),
                     "alpha_character": "REAL but MODEST / boundary (below the 3.0 strong bar); a new "
                                        "orthogonal factor must beat THIS to justify the spend"},
        "data_family_scorecard": [
            {"rank": r["rank"], "family_key": r["key"], "family_name": r["name"],
             "is_primary_candidate": r["is_primary_candidate"], "composite_score": r["composite_score"],
             "maps_to_decision": r.get("maps_to_decision"), "scores": r["scores"]} for r in ranked],
        "family_evaluations": family_blocks,
        "candidates_tested": [{"family": b["name"], "key": b["key"], "rank": b["rank"],
                               "composite_score": b["composite_score"],
                               "is_primary_candidate": b["is_primary_candidate"]}
                              for b in family_blocks],
        "variants_tested": [{"family": b["name"], "rank": b["rank"],
                             "composite_score": b["composite_score"]} for b in family_blocks],
        "rejected_candidates": [{"family": b["name"], "key": b["key"], "rank": b["rank"],
                                 "composite_score": b["composite_score"],
                                 "why": "deferred behind the #1 pick - lower scorecard composite "
                                        "(weaker orthogonality/horizon-fit/evidence/accessibility)"}
                                for b in deferred],
        "champion": {"recommended_first_acquisition": champion["name"] if champion else None,
                     "family_key": champion["key"] if champion else None,
                     "composite_score": champion["composite_score"] if champion else None,
                     "maps_to_decision": decision,
                     "is_new_orthogonal_paid_data": True,
                     "requires_explicit_user_opt_in": True,
                     "no_stronger_owned_alpha_found": True},
        "baseline_vs_champion": {
            "note": "the 'champion' here is a DATA-ACQUISITION recommendation, not a fitted alpha; the "
                    "modest composite_sn baseline remains the only usable alpha until a Phase 11-B trial "
                    "proves a new orthogonal factor beats it out-of-sample"},
        "recommended_data_family": champion["name"] if champion else None,
        "analyst_revisions_required_fields": [
            {"field_key": f[0], "field_label": f[1], "category": f[2], "pit_required": f[3],
             "cadence": f[4], "priority": f[5], "notes": f[6]} for f in ANALYST_REVISION_FIELDS],
        "vendor_candidates": [
            {"vendor": v[0], "data_family": v[1], "pit_history_assumed": v[2], "cost_tier": v[3],
             "retail_accessible": v[4], "requires_user_opt_in": v[5], "no_probe_performed": True,
             "notes": v[6]} for v in VENDOR_CANDIDATES],
        "phase11b_test_plan": list(PHASE11B_TEST_PLAN),
        "phase11b_acceptance_criteria": [
            {"criterion_id": c[0], "phase11b_step": c[1], "metric": c[2], "pass_threshold": c[3],
             "reject_condition": c[4]} for c in PHASE11B_ACCEPTANCE_CRITERIA],
        "integration_risk_register": [
            {"risk_id": r[0], "risk": r[1], "severity": r[2], "likelihood": r[3], "mitigation": r[4],
             "phase_to_address": r[5]} for r in INTEGRATION_RISK_REGISTER],
        "oos_stability_summary": {
            "note": "no fitting performed in 11-A; OOS stability is the Phase 11-B gate (AC8): walk-forward "
                    "pooled OOS IC positive, frac windows positive >= baseline, and the improvement must "
                    "survive the subperiod-net25 generalization guard (favourable in BOTH eras)"},
        "cohort_stability_summary": {
            "note": "Phase 11-B requires both earnings cohorts and both pre/post-2020 subperiods positive "
                    "before any new factor is productized (AC5)"},
        "sector_concentration_summary": {
            "baseline_top_sector_share": baseline.get("top_sector_share"),
            "note": "Phase 11-B AC9 requires the blended book's top-sector share not to worsen vs baseline"},
        "turnover_cost_summary": {
            "baseline_turnover": baseline.get("quarterly_turnover"),
            "note": "revision factors update daily-weekly but a 63d/quarterly rebalance keeps turnover "
                    "bounded; Phase 11-B AC6/AC7 require net25>0 and net50>=0 after 25/50bps costs"},
        "implementation_limits": [
            "design/decision phase only: no panel built, nothing fitted, no data acquired",
            "no provider was probed and no API was called - vendor rows are DESIGN ASSUMPTIONS from "
            "prior-phase notes, flagged no_probe_performed=true and requires_user_opt_in=true",
            "actual acquisition of any paid/trial feed requires EXPLICIT user opt-in and is out of scope",
            "scores are expert-judgement priors, not measured alpha; the Phase 11-B trial is the real test",
            "a zero-cost pre-check of the owned EODHD Earnings::Trend snapshot (11-B STEP 0) is recommended "
            "before any purchase to confirm field shape - but it is a current snapshot, NOT usable history"],
        "recommended_next_actions": [
            "acquire the #1 family (%s) as a bounded trial/export on EXPLICIT user opt-in, then run Phase "
            "11-B" % (champion["name"] if champion else "the top-ranked family"),
            "before purchase (zero cost): inspect the owned EODHD fundamentals Earnings::Trend block to "
            "confirm revision-field shape and identifier mapping",
            "in parallel, keep the modest 10-D/10-H book in PAPER review via the 10-I tracker (human gate; "
            "no orders; no automation)"],
        "next_recommended_phase": ("Phase 11-B - Analyst Estimate Revisions Trial Ingestion And Alpha Test "
                                   "(on explicit user opt-in to a bounded data trial)"),
        "safety": {"paper_only": True, "owned_local_data_only": True, "no_live_api_calls": True,
                   "no_orders": True, "no_automation": True, "no_broker": True, "no_deploy": True},
        "constraints_honored": ["offline (no network/key/provider probe)", "no API calls",
                                "no provider probing", "owned-data search already exhausted (10-Q)",
                                "no new data acquired", "no Paper Trader writes", "no orders",
                                "no automation", "no broker", "no deploy", "no GCP", "no package install",
                                "no full regression", "commit only phase11a files if tests pass", "no push"],
    }


def _print_summary(report: Dict) -> None:
    champ = report.get("champion", {})
    top3 = report.get("data_family_scorecard", [])[:3]
    print("[11-A] decision=%s | recommended_first_acquisition=%s (composite=%s)"
          % (report.get("decision"), champ.get("recommended_first_acquisition"),
             champ.get("composite_score")))
    for r in top3:
        print("       #%s %-42s composite=%s -> %s"
              % (r["rank"], r["family_name"], r["composite_score"], r.get("maps_to_decision") or "-"))


def run(out_dir: Optional[Path] = None, *, verbose: bool = True) -> Dict:
    P = Path(out_dir) if out_dir else (_OUT / STEM)
    P.mkdir(parents=True, exist_ok=True)
    try:
        key_visible = bool(os.environ.get("EODHD_API_KEY"))
        if verbose:
            print("[11-A] preflight OFFLINE - design/decision synthesis; no network; key_visible=%s"
                  % key_visible)
        ranked = rank_families()
        decision, rationale, champion = decide(ranked)
        baseline = _baseline_context()
        write_artifacts(P, ranked)
        report = _build_report(decision, rationale, ranked, champion, baseline, key_visible)
        _write_json(P / ("%s.json" % STEM), report)
        if verbose:
            _print_summary(report)
        return report
    except Exception as exc:  # pragma: no cover - defensive
        import traceback
        detail = "%s: %s" % (type(exc).__name__, exc)
        if verbose:
            print("[11-A] ERROR %s" % detail)
        report = {"phase": PHASE, "decision": DEC_PAUSE, "decision_rationale": detail,
                  "traceback": traceback.format_exc(),
                  "safety": {"paper_only": True, "owned_local_data_only": True, "no_live_api_calls": True,
                             "no_orders": True, "no_automation": True, "no_broker": True,
                             "no_deploy": True}}
        try:
            _write_json(P / ("%s.json" % STEM), report)
        except Exception:
            pass
        return report


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Phase 11-A - Orthogonal Data Acquisition Decision Package")
    p.add_argument("--out-dir", default=None)
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ns = _parse_args(argv)
    report = run(out_dir=ns.out_dir, verbose=not ns.quiet)
    return 0 if report.get("decision") in ALLOWED_DECISIONS else 1


if __name__ == "__main__":
    raise SystemExit(main())
