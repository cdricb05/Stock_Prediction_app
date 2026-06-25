"""Phase 8-L — Autonomous Data-Family Expansion, Provider Acquisition Decision & Signal-Confirmation Factory.

**Track A (quant brain) research only.** 8-K became a self-expanding alpha factory that resolves every
missing data family to a concrete status. 8-L answers the next question explicitly:

    WHICH DATA SOURCES MUST WE ACTIVATE OR SUBSCRIBE TO, AND WHICH SIGNAL FAMILIES BECOME TESTABLE OR
    CONFIRMABLE WHEN WE DO?

It does NOT consume the 8-K output and stop. It rebuilds the 8-K state, then runs 12 NEW waves whose work
is (a) auditing every missing data family, (b) inspecting local caches, (c) attempting free/no-key
activation, (d) detecting provider keys by name, (e) ranking the SMALLEST provider set that unlocks the
MOST missing high-value families, (f) producing per-family acquisition DECISIONS (provider, env var,
endpoint, cost, unlocked signals, expected event/quality gain), and (g) continuing to TEST local signal
families (tail-risk repair on the clean macro leads, earnings-confirmed candidates) while data discovery
runs — plus placeholder experiment SPECS (never fake results) for every provider-gated signal family.

Terminal stop conditions (the ONLY reasons it halts)
----------------------------------------------------
  CONFIRMED_ALPHA_SIGNAL_FOUND · PROVIDER_ONLY_BLOCKER (with --stop-on-provider-only) ·
  TIME_BUDGET_EXHAUSTED · EXPERIMENT_BUDGET_EXHAUSTED · WAVE_BUDGET_EXHAUSTED ·
  MANUAL_STOP_FILE_DETECTED · SAFETY_OR_LEAKAGE_BLOCKER
An empty hypothesis bank with waves remaining does NOT stop — the factory refills the next wave.

Reuse (no re-implementation of scoring/gates/controls/promotion/validation)
---------------------------------------------------------------------------
8-L -> 8-K -> 8-J -> 8-I -> 8-H -> 8-G -> 8-F -> 8-E. The whole validated scoring/aggregation/decision/
report stack is imported from 8-K (which imports the rest). 8-L adds: the 12-wave data-family-expansion
loop, a 14-family acquisition matrix (7 statuses incl. ERROR), local-cache discovery, free-no-key
activation reporting, provider key inventory + decision matrix + priority ranking + bundle recommendation
+ activation order + cost/value + free-trial plan + acquisition .ps1, placeholder unlocked-signal specs,
tail-risk-repair + provider-expansion-required scoreboards, and a trade-idea registry that records
whether each idea is trade-ready and exactly why not.

Hard safety contract (unchanged from 8-E..8-K)
----------------------------------------------
Local data first; Norgate + on-disk FRED for price/macro/sector/cross-asset; no package install. Provider
keys detected by NAME/presence only, never printed. Large runtime state under
D:\\Stock_Prediction_app_data\\data_family_expansion_signal_factory; repo gets summaries/snapshots/decision
artifacts only. Every experiment pre-registered before scoring; thresholds fixed a priori; >=30%
challenges/placebos per scoring wave. External data NEVER faked (provider-gated families emit SPEC-ONLY
requirements, never synthetic results; the revision PROXY stays labelled and capped below CONFIRMED). No
threshold tuning after results, no factor-sign flipping, no weight optimization, no regime activation, no
ML fit, no hidden failures, no secrets printed. No Paper Trader, no GCP, no deployment, no broker/orders/
automation, no live trading signals. No commit, no push.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _load_module(name: str, rel: str):
    path = _REPO_ROOT / rel
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# Reuse the whole validated stack via 8-K. No re-implementation of scoring/gates/controls/promotion.
P8K = _load_module("phase8k_engine_for_8l", "research/run_phase8k_self_expanding_alpha_factory.py")
P8J = P8K.P8J
P8I = P8K.P8I
P8G = P8K.P8G
P8F = P8K.P8F
P8E = P8K.P8E

# IO + scoring primitives (verbatim).
_write_json = P8E._write_json
_write_csv = P8E._write_csv
_utc_now_iso = P8E._utc_now_iso
SensPanel = P8E.SensPanel
SensSetup = P8E.SensSetup
SHOCK_Z = P8E.SHOCK_Z
_mk = P8G._mk

# Alpha promotion + recommendation vocab (reused unchanged).
ST_ALPHA_CONFIRMED = P8I.ST_ALPHA_CONFIRMED
ST_ALPHA_PROMISING = P8I.ST_ALPHA_PROMISING
ST_ALPHA_PROVIDER_REQUIRED = P8I.ST_ALPHA_PROVIDER_REQUIRED
ST_REJECTED = P8I.ST_REJECTED
ALLOWED_ALPHA_STATUSES = P8I.ALLOWED_ALPHA_STATUSES
ALLOWED_RECOMMENDATIONS = P8I.ALLOWED_RECOMMENDATIONS
_ALPHA_SCORE_COLS = P8I._ALPHA_SCORE_COLS
REC_ERROR = P8I.REC_ERROR

# Families + agents + roles (reuse 8-J via 8-K).
FAM_EARNINGS = P8K.FAM_EARNINGS
FAM_REVISION = P8K.FAM_REVISION
FAM_NEWS = P8K.FAM_NEWS
FAM_OPTIONS = P8K.FAM_OPTIONS
FAM_SHORT = P8K.FAM_SHORT
FAM_S8E011_EXT = P8K.FAM_S8E011_EXT
FAM_MACRO = P8K.FAM_MACRO
FAM_FILINGS = P8K.FAM_FILINGS
SENS_A, VAL_A, RSK_A, MODEL_A = P8K.SENS_A, P8K.VAL_A, P8K.RSK_A, P8K.MODEL_A
EARN_A, REV_A, NEWS_A, OPT_A, SHORT_A, EXT_A, DIR_A = (
    P8K.EARN_A, P8K.REV_A, P8K.NEWS_A, P8K.OPT_A, P8K.SHORT_A, P8K.EXT_A, P8K.DIR_A)
DAEMON_ROLES = P8K.DAEMON_ROLES
_FAMILY_ROLE = P8K._FAMILY_ROLE
ROLE_HYPGEN = P8K.ROLE_HYPGEN

# Scoring / aggregation / decision primitives (reuse 8-J via 8-K verbatim).
_interleave_challenges = P8K._interleave_challenges
_score_batch = P8K._score_batch
_aggregate = P8K._aggregate
_rejected_families = P8K._rejected_families
decide_next_action = P8K.decide_next_action
provider_blocker_rows = P8K.provider_blocker_rows
validation_skeptic_rows = P8K.validation_skeptic_rows
agent_task_board_rows = P8K.agent_task_board_rows
agent_cycle_summary_rows = P8K.agent_cycle_summary_rows
_result_row = P8K._result_row
_promotion_log_rows = P8K._promotion_log_rows
_graveyard_rows = P8K._graveyard_rows
_hypothesis_row = P8K._hypothesis_row
P8H_safe_filings = P8K.P8H_safe_filings
ACT_CONTINUE_LOCAL = P8K.ACT_CONTINUE_LOCAL
ALLOWED_ACTIONS = P8K.ALLOWED_ACTIONS

# Reuse 8-J durable-state column orders for the shared snapshots.
_RESULT_COLS = P8K._RESULT_COLS
_REG_COLS = P8K._REG_COLS
_PROMO_COLS = P8K._PROMO_COLS
_GRAVE_COLS = P8K._GRAVE_COLS
_BLOCKER_COLS = P8K._BLOCKER_COLS
_HYP_COLS = P8K._HYP_COLS
_BOARD_COLS = P8K._BOARD_COLS
_CYCLESUM_COLS = P8K._CYCLESUM_COLS
_SKEPTIC_COLS = P8K._SKEPTIC_COLS

# Reuse 8-K shared condition tuples (column-legal against the persisted 8-E grid).
POS, NEG, LARGE = P8K.POS, P8K.NEG, P8K.LARGE
REVUP, RECENT_POS = P8K.REVUP, P8K.RECENT_POS
STRONG, SECTOR_STRONG, VOLCOMP, LOWBETA = P8K.STRONG, P8K.SECTOR_STRONG, P8K.VOLCOMP, P8K.LOWBETA
RATES_SELLOFF, RATES_NEG = P8K.RATES_SELLOFF, P8K.RATES_NEG

# Paths.
DATA_ROOT = P8F.DATA_ROOT
STATE_ROOT_DEFAULT = DATA_ROOT / "data_family_expansion_signal_factory"
PHASE8K_STATE_DIR = DATA_ROOT / "self_expanding_alpha_factory"
DEFAULT_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase8l_data_family_expansion_signal_factory"
STOP_FILE_NAME = "STOP_FACTORY.txt"

PHASE = "8-L"
OBJECTIVE = (
    "Operate and upgrade the alpha factory so it keeps expanding across EVERY missing data family: audit "
    "families, inspect local caches, attempt free/no-key activation, detect provider keys by name, rank "
    "the smallest provider set that unlocks the most high-value families, decide what to subscribe to, "
    "and continue testing local signals (tail-risk repair, earnings-confirmation) — answering which data "
    "sources to activate/subscribe to and which signal families become testable or confirmable when we do.")

HYPOTHESES_PER_CYCLE = 12

# --------------------------------------------------------------------------- #
# Stop conditions (the ONLY reasons the factory halts) — reuse 8-K vocab.
# --------------------------------------------------------------------------- #
STOP_CONFIRMED = P8K.STOP_CONFIRMED
STOP_PROVIDER_ONLY = P8K.STOP_PROVIDER_ONLY
STOP_SAFETY = P8K.STOP_SAFETY
STOP_EXPERIMENT_BUDGET = P8K.STOP_EXPERIMENT_BUDGET
STOP_TIME_BUDGET = P8K.STOP_TIME_BUDGET
STOP_WAVE_BUDGET = P8K.STOP_WAVE_BUDGET
STOP_MANUAL = P8K.STOP_MANUAL
ALLOWED_STOPS = P8K.ALLOWED_STOPS
evaluate_factory_stop = P8K.evaluate_factory_stop

# --------------------------------------------------------------------------- #
# Research waves (Phase 8-L). Executed in this order.
# --------------------------------------------------------------------------- #
WAVE_PHASE8K_STATE_REBUILD = "WAVE_PHASE8K_STATE_REBUILD"
WAVE_MISSING_DATA_FAMILY_AUDIT = "WAVE_MISSING_DATA_FAMILY_AUDIT"
WAVE_LOCAL_CACHE_DISCOVERY = "WAVE_LOCAL_CACHE_DISCOVERY"
WAVE_FREE_NO_KEY_ACTIVATION = "WAVE_FREE_NO_KEY_ACTIVATION"
WAVE_PROVIDER_KEY_ACTIVATION = "WAVE_PROVIDER_KEY_ACTIVATION"
WAVE_PROVIDER_SUBSCRIPTION_RANKING = "WAVE_PROVIDER_SUBSCRIPTION_RANKING"
WAVE_EARNINGS_ANALYST_PROVIDER_DECISION = "WAVE_EARNINGS_ANALYST_PROVIDER_DECISION"
WAVE_NEWS_SENTIMENT_PROVIDER_DECISION = "WAVE_NEWS_SENTIMENT_PROVIDER_DECISION"
WAVE_OPTIONS_IV_SKEW_PROVIDER_DECISION = "WAVE_OPTIONS_IV_SKEW_PROVIDER_DECISION"
WAVE_SHORT_INTEREST_BORROW_ACTIVATION = "WAVE_SHORT_INTEREST_BORROW_ACTIVATION"
WAVE_TAIL_RISK_REPAIR_SIGNAL_TESTS = "WAVE_TAIL_RISK_REPAIR_SIGNAL_TESTS"
WAVE_TRADE_IDEA_PROMOTION = "WAVE_TRADE_IDEA_PROMOTION"

WAVES = [
    WAVE_PHASE8K_STATE_REBUILD, WAVE_MISSING_DATA_FAMILY_AUDIT, WAVE_LOCAL_CACHE_DISCOVERY,
    WAVE_FREE_NO_KEY_ACTIVATION, WAVE_PROVIDER_KEY_ACTIVATION, WAVE_PROVIDER_SUBSCRIPTION_RANKING,
    WAVE_EARNINGS_ANALYST_PROVIDER_DECISION, WAVE_NEWS_SENTIMENT_PROVIDER_DECISION,
    WAVE_OPTIONS_IV_SKEW_PROVIDER_DECISION, WAVE_SHORT_INTEREST_BORROW_ACTIVATION,
    WAVE_TAIL_RISK_REPAIR_SIGNAL_TESTS, WAVE_TRADE_IDEA_PROMOTION,
]

# Waves that produce scoreable grid experiments (the rest are audit/acquisition/decision only).
_SCORING_WAVES = {WAVE_PHASE8K_STATE_REBUILD, WAVE_EARNINGS_ANALYST_PROVIDER_DECISION,
                  WAVE_TAIL_RISK_REPAIR_SIGNAL_TESTS}
_NON_SCORING_WAVES = [w for w in WAVES if w not in _SCORING_WAVES]
# Acquisition/decision waves that explicitly log a provider requirement.
_PROVIDER_DECISION_WAVES = {WAVE_NEWS_SENTIMENT_PROVIDER_DECISION, WAVE_OPTIONS_IV_SKEW_PROVIDER_DECISION,
                            WAVE_SHORT_INTEREST_BORROW_ACTIVATION}

WAVE_META = {
    WAVE_PHASE8K_STATE_REBUILD: {
        "active_focus": "rebuild the 8-K clean macro leads (S8E-011 anchor, sector-leadership, earnings-"
                        "confirmed F20) on the fixed 8-E gate for continuity",
        "data_families_targeted": ["macro_cross_asset_context",
                                    "liquidity_volume_volatility_positioning"],
        "next_wave_reason": "leads anchored; audit every missing data family next"},
    WAVE_MISSING_DATA_FAMILY_AUDIT: {
        "active_focus": "resolve all 14 data families to a concrete status (no vague entries)",
        "data_families_targeted": ["broad_earnings_surprise", "analyst_estimates_revisions",
                                   "historical_news_sentiment"],
        "next_wave_reason": "families classified; inspect local caches for each next"},
    WAVE_LOCAL_CACHE_DISCOVERY: {
        "active_focus": "inventory local caches on disk (earnings, filings, Norgate panel, FRED)",
        "data_families_targeted": ["broad_earnings_surprise", "sec_filings_event_classification",
                                   "macro_cross_asset_context"],
        "next_wave_reason": "local caches mapped; attempt free/no-key activation next"},
    WAVE_FREE_NO_KEY_ACTIVATION: {
        "active_focus": "attempt SEC EDGAR / GDELT / FINRA free no-key sources; record outcomes honestly",
        "data_families_targeted": ["sec_filings_event_classification", "historical_news_sentiment",
                                   "short_interest_borrow_utilization"],
        "next_wave_reason": "free sources exhausted; detect existing provider keys next"},
    WAVE_PROVIDER_KEY_ACTIVATION: {
        "active_focus": "detect provider env vars by name/presence only (never read values)",
        "data_families_targeted": ["broad_earnings_surprise", "analyst_estimates_revisions"],
        "next_wave_reason": "keys inventoried; rank the smallest unlocking provider set next"},
    WAVE_PROVIDER_SUBSCRIPTION_RANKING: {
        "active_focus": "rank the smallest provider set that unlocks the most high-value families",
        "data_families_targeted": ["broad_earnings_surprise", "analyst_estimates_revisions",
                                   "historical_news_sentiment"],
        "next_wave_reason": "ranking ready; commit the earnings/analyst provider decision next"},
    WAVE_EARNINGS_ANALYST_PROVIDER_DECISION: {
        "active_focus": "test earnings-confirmed candidates AND decide the earnings/analyst provider (FMP)",
        "data_families_targeted": ["broad_earnings_surprise", "analyst_estimates_revisions"],
        "next_wave_reason": "earnings/analyst decided; decide the news/sentiment provider next"},
    WAVE_NEWS_SENTIMENT_PROVIDER_DECISION: {
        "active_focus": "exhaust GDELT free window; decide the deep-history news/sentiment provider",
        "data_families_targeted": ["historical_news_sentiment"],
        "next_wave_reason": "news decided (GDELT first, paid for history); decide options next"},
    WAVE_OPTIONS_IV_SKEW_PROVIDER_DECISION: {
        "active_focus": "no local options data; decide the IV/skew provider (only if a signal needs it)",
        "data_families_targeted": ["options_iv_skew_putcall_unusual_activity"],
        "next_wave_reason": "options deferred unless a candidate needs IV/skew; activate FINRA short next"},
    WAVE_SHORT_INTEREST_BORROW_ACTIVATION: {
        "active_focus": "attempt FINRA free short interest before any paid short/borrow source",
        "data_families_targeted": ["short_interest_borrow_utilization"],
        "next_wave_reason": "short interest activated/decided; repair tail risk on the best leads next"},
    WAVE_TAIL_RISK_REPAIR_SIGNAL_TESTS: {
        "active_focus": "apply fixed beta-tail / vol-quintile / sector-cap filters to repair the clean "
                        "macro leads (no tuning after scoring)",
        "data_families_targeted": ["liquidity_volume_volatility_positioning",
                                   "macro_cross_asset_context"],
        "next_wave_reason": "tail filters scored; promote paper-review trade-idea candidates last"},
    WAVE_TRADE_IDEA_PROMOTION: {
        "active_focus": "promote promising leads to paper-review-only trade ideas (with trade-ready reason)",
        "data_families_targeted": ["macro_cross_asset_context", "broad_earnings_surprise"],
        "next_wave_reason": "all waves complete — terminal on WAVE_BUDGET_EXHAUSTED unless bounded sooner"},
}

# --------------------------------------------------------------------------- #
# Mandatory data-family acquisition vocabulary (7 statuses incl. ERROR).
# --------------------------------------------------------------------------- #
DF_LOCAL = "LOCAL_DATA_FOUND"
DF_FREE_ACTIVATED = "FREE_NO_KEY_SOURCE_ACTIVATED"
DF_FREE_INSUFFICIENT = "FREE_NO_KEY_SOURCE_ATTEMPTED_BUT_INSUFFICIENT"
DF_KEY_ACTIVATED = "EXISTING_PROVIDER_KEY_ACTIVATED"
DF_PAID_REQUIRED = "PAID_PROVIDER_REQUIRED"
DF_NOT_RELEVANT = "NOT_RELEVANT_AFTER_TESTING"
DF_ERROR = "ERROR"
ALLOWED_FAMILY_STATUSES = (DF_LOCAL, DF_FREE_ACTIVATED, DF_FREE_INSUFFICIENT, DF_KEY_ACTIVATED,
                           DF_PAID_REQUIRED, DF_NOT_RELEVANT, DF_ERROR)

# 14 data families investigated every run.
F_EARN = "broad_earnings_surprise"
F_REV = "analyst_estimates_revisions"
F_NEWS = "historical_news_sentiment"
F_TRANSCRIPT = "earnings_call_transcripts_tone"
F_OPTIONS = "options_iv_skew_putcall_unusual_activity"
F_SHORT = "short_interest_borrow_utilization"
F_INSIDER = "insider_transactions"
F_13F = "institutional_ownership_13f"
F_FILINGS = "sec_filings_event_classification"
F_GUIDANCE = "guidance_press_releases"
F_FUND = "company_fundamentals_valuation_quality_leverage"
F_SECTOR = "sector_industry_context"
F_MACRO = "macro_cross_asset_context"
F_LIQ = "liquidity_volume_volatility_positioning"
DATA_FAMILIES = [F_EARN, F_REV, F_NEWS, F_TRANSCRIPT, F_OPTIONS, F_SHORT, F_INSIDER, F_13F, F_FILINGS,
                 F_GUIDANCE, F_FUND, F_SECTOR, F_MACRO, F_LIQ]

# Provider env vars inspected by NAME/presence only — never printed (11 keys).
KEY_NAMES = ["ALPHAVANTAGE_API_KEY", "FINNHUB_API_KEY", "FMP_API_KEY", "POLYGON_API_KEY",
             "EODHD_API_KEY", "INTRINIO_API_KEY", "TIINGO_API_KEY", "NEWSAPI_KEY",
             "BENZINGA_API_KEY", "SEC_API_KEY", "QUANDL_API_KEY"]

# Provider catalogue. cost_usd_month_approx is an APPROXIMATE public list tier (verify on site);
# free == no key / free public endpoint. env_var None == no key required.
PROVIDERS = {
    "FMP": {"env_var": "FMP_API_KEY", "free": False, "free_trial": True,
            "cost_usd_month_approx": "~22-29 (Starter)",
            "families": [F_EARN, F_REV, F_TRANSCRIPT, F_NEWS, F_GUIDANCE, F_FUND, F_FILINGS],
            "endpoints_or_docs": "financialmodelingprep.com/developer/docs : /earnings-surprises, "
                                 "/analyst-estimates, /earning_calendar, /press-releases, /transcript"},
    "Finnhub": {"env_var": "FINNHUB_API_KEY", "free": False, "free_trial": True,
                "cost_usd_month_approx": "~0 (free tier) / paid for history",
                "families": [F_REV, F_NEWS, F_TRANSCRIPT, F_FUND, F_SHORT],
                "endpoints_or_docs": "finnhub.io/docs/api : /stock/earnings, /company-news, "
                                     "/news-sentiment, /stock/transcripts, /stock/short-interest"},
    "AlphaVantage": {"env_var": "ALPHAVANTAGE_API_KEY", "free": False, "free_trial": True,
                     "cost_usd_month_approx": "~0 (free tier, 25/day) / ~50 premium",
                     "families": [F_EARN, F_FUND, F_NEWS, F_OPTIONS],
                     "endpoints_or_docs": "alphavantage.co/documentation : EARNINGS, NEWS_SENTIMENT, "
                                          "HISTORICAL_OPTIONS"},
    "EODHD": {"env_var": "EODHD_API_KEY", "free": False, "free_trial": True,
              "cost_usd_month_approx": "~20-80 by bundle",
              "families": [F_EARN, F_REV, F_FUND],
              "endpoints_or_docs": "eodhd.com/financial-apis : /calendar/earnings, /fundamentals"},
    "Benzinga": {"env_var": "BENZINGA_API_KEY", "free": False, "free_trial": True,
                 "cost_usd_month_approx": "enterprise (quote required)",
                 "families": [F_NEWS, F_REV, F_GUIDANCE],
                 "endpoints_or_docs": "docs.benzinga.io : /news, /calendar/earnings, /calendar/ratings"},
    "Intrinio": {"env_var": "INTRINIO_API_KEY", "free": False, "free_trial": True,
                 "cost_usd_month_approx": "options packages (quote required)",
                 "families": [F_OPTIONS, F_REV, F_FUND],
                 "endpoints_or_docs": "docs.intrinio.com : /options/prices, /options/expirations (IV/Greeks)"},
    "Polygon": {"env_var": "POLYGON_API_KEY", "free": False, "free_trial": True,
                "cost_usd_month_approx": "~29-199 by tier",
                "families": [F_OPTIONS, F_NEWS],
                "endpoints_or_docs": "polygon.io/docs : /v3/snapshot/options, /v2/reference/news"},
    "Tiingo": {"env_var": "TIINGO_API_KEY", "free": False, "free_trial": True,
               "cost_usd_month_approx": "~10",
               "families": [F_NEWS, F_FUND],
               "endpoints_or_docs": "tiingo.com/documentation : /tiingo/news, /tiingo/fundamentals"},
    "Quandl": {"env_var": "QUANDL_API_KEY", "free": False, "free_trial": True,
               "cost_usd_month_approx": "dataset-dependent",
               "families": [F_SHORT],
               "endpoints_or_docs": "data.nasdaq.com (Nasdaq Data Link) : short-interest datasets"},
    "NewsAPI": {"env_var": "NEWSAPI_KEY", "free": False, "free_trial": True,
                "cost_usd_month_approx": "~0 dev (no history) / ~449 business",
                "families": [F_NEWS],
                "endpoints_or_docs": "newsapi.org/docs : /v2/everything (recent window only)"},
    "FINRA": {"env_var": None, "free": True, "free_trial": False, "cost_usd_month_approx": "free",
              "families": [F_SHORT],
              "endpoints_or_docs": "cdn.finra.org/equity/regsho (daily) ; "
                                   "finra.org consolidated short interest (biweekly)"},
    "GDELT": {"env_var": None, "free": True, "free_trial": False, "cost_usd_month_approx": "free",
              "families": [F_NEWS],
              "endpoints_or_docs": "api.gdeltproject.org/api/v2/doc/doc (recent window, entity-level)"},
    "SEC_EDGAR": {"env_var": None, "free": True, "free_trial": False, "cost_usd_month_approx": "free",
                  "families": [F_FILINGS, F_INSIDER, F_13F, F_GUIDANCE, F_FUND],
                  "endpoints_or_docs": "data.sec.gov : /submissions, Form 4 (insider), 13F, 8-K/10-Q/10-K"},
    "SimFin": {"env_var": None, "free": True, "free_trial": False, "cost_usd_month_approx": "free/local",
               "families": [F_FUND],
               "endpoints_or_docs": "simfin.com (bulk fundamentals; local CSV)"},
}

# Per-family acquisition spec. best_provider is the first paid/preferred candidate; base_status is the
# no-key baseline (overridden to EXISTING_PROVIDER_KEY_ACTIVATED if a candidate key is present).
FAMILY_SPECS = {
    F_EARN: {
        "base_status": DF_FREE_INSUFFICIENT,
        "local_files_found": "Norgate-derived earnings cache (~75 tickers) via 8-G load_earnings_events",
        "free_sources_attempted": "local cache only; no broad free no-key earnings-surprise history",
        "providers": ["FMP", "AlphaVantage", "EODHD", "Finnhub"], "best_provider": "FMP",
        "subscription_likely": True, "signals_unlocked": "earnings x context family; lifts F20>=1000 events",
        "blocker_addressed": "earnings feed covers only ~75 tickers",
        "expected_event_count_gain": "high (75 -> S&P 500/1500 names)",
        "expected_validation_quality_gain": "high (clears the >=1000-event gate broadly)",
        "next_action": "subscribe FMP Starter; backfill earnings surprises across S&P 1500; rebuild grid"},
    F_REV: {
        "base_status": DF_FREE_INSUFFICIENT,
        "local_files_found": "labelled surprise-acceleration revision PROXY only (capped < CONFIRMED)",
        "free_sources_attempted": "proxy derived from earnings cache; no true consensus revisions free",
        "providers": ["FMP", "Finnhub", "EODHD", "Intrinio"], "best_provider": "FMP",
        "subscription_likely": True, "signals_unlocked": "true revision x sensitivity (uncaps the proxy)",
        "blocker_addressed": "revision signal is a proxy, never CONFIRMED",
        "expected_event_count_gain": "high", "expected_validation_quality_gain": "high (real, uncapped)",
        "next_action": "subscribe FMP/Finnhub analyst-estimates; replace proxy with consensus revisions"},
    F_NEWS: {
        "base_status": DF_FREE_INSUFFICIENT,
        "local_files_found": "none (no PIT news store on disk)",
        "free_sources_attempted": "GDELT connector probed (live HTTP 200) but only a recent window",
        "providers": ["Benzinga", "Finnhub", "Tiingo", "Polygon", "AlphaVantage", "NewsAPI"],
        "best_provider": "Benzinga", "subscription_likely": True,
        "signals_unlocked": "sentiment shock x sensitivity; macro-lead news confirmation",
        "blocker_addressed": "no deep PIT news/sentiment history",
        "expected_event_count_gain": "medium-high", "expected_validation_quality_gain": "medium",
        "next_action": "exhaust GDELT/Finnhub free window first; trial Benzinga for deep history"},
    F_TRANSCRIPT: {
        "base_status": DF_PAID_REQUIRED,
        "local_files_found": "none", "free_sources_attempted": "none viable no-key",
        "providers": ["FMP", "Finnhub"], "best_provider": "FMP", "subscription_likely": True,
        "signals_unlocked": "management-tone x earnings-surprise confirmation",
        "blocker_addressed": "no transcript text/tone source",
        "expected_event_count_gain": "medium", "expected_validation_quality_gain": "medium",
        "next_action": "FMP transcripts (bundled with earnings sub) — defer until earnings feed proves out"},
    F_OPTIONS: {
        "base_status": DF_PAID_REQUIRED,
        "local_files_found": "none", "free_sources_attempted": "none viable no-key for deep IV/skew",
        "providers": ["Intrinio", "Polygon", "AlphaVantage"], "best_provider": "Intrinio",
        "subscription_likely": True, "signals_unlocked": "IV-percentile / skew / put-call x sensitivity",
        "blocker_addressed": "no options IV/skew history",
        "expected_event_count_gain": "high (daily per name)", "expected_validation_quality_gain": "medium",
        "next_action": "do NOT subscribe first — only after a candidate signal specifically needs IV/skew"},
    F_SHORT: {
        "base_status": DF_FREE_INSUFFICIENT,
        "local_files_found": "none",
        "free_sources_attempted": "FINRA reg-SHO daily reachable no-key (single settlement window)",
        "providers": ["Finnhub", "Quandl"], "best_provider": "Finnhub", "subscription_likely": True,
        "signals_unlocked": "short-interest increase / days-to-cover x liquidity/beta context",
        "blocker_addressed": "no deep biweekly short-interest / borrow / utilization history",
        "expected_event_count_gain": "medium", "expected_validation_quality_gain": "medium",
        "next_action": "build FINRA biweekly consolidated short-interest history (free) before any paid key"},
    F_INSIDER: {
        "base_status": DF_FREE_INSUFFICIENT,
        "local_files_found": "none",
        "free_sources_attempted": "SEC EDGAR Form 4 reachable no-key (collector not yet built)",
        "providers": ["SEC_EDGAR", "FMP"], "best_provider": "SEC_EDGAR", "subscription_likely": False,
        "signals_unlocked": "insider-buy cluster x sensitivity",
        "blocker_addressed": "no insider-transaction event store",
        "expected_event_count_gain": "medium", "expected_validation_quality_gain": "medium",
        "next_action": "build a no-key SEC EDGAR Form 4 collector on D: (free) before any paid source"},
    F_13F: {
        "base_status": DF_FREE_INSUFFICIENT,
        "local_files_found": "none",
        "free_sources_attempted": "SEC EDGAR 13F reachable no-key (quarterly, low frequency)",
        "providers": ["SEC_EDGAR", "FMP"], "best_provider": "SEC_EDGAR", "subscription_likely": False,
        "signals_unlocked": "ownership-change x sensitivity (low-frequency overlay only)",
        "blocker_addressed": "no 13F ownership store; quarterly cadence limits short-horizon drift use",
        "expected_event_count_gain": "low (quarterly)", "expected_validation_quality_gain": "low",
        "next_action": "deprioritise — quarterly cadence is weak for 5-60d drift; revisit if breadth needed"},
    F_FILINGS: {
        "base_status": DF_FREE_ACTIVATED,
        "local_files_found": "filing_event column live via 8-G/8-H no-key SEC EDGAR",
        "free_sources_attempted": "SEC EDGAR submissions activated (no-key); 8-K/10-Q/10-K acceptance",
        "providers": ["SEC_EDGAR", "FMP"], "best_provider": "SEC_EDGAR",
        "subscription_likely": False, "signals_unlocked": "filing x sensitivity family",
        "blocker_addressed": "filing events already mapped; finer 8-K item classification is optional",
        "expected_event_count_gain": "low (already active)",
        "expected_validation_quality_gain": "low-medium (item-level classification)",
        "next_action": "optionally add 8-K item-code classification on the existing no-key EDGAR feed"},
    F_GUIDANCE: {
        "base_status": DF_FREE_INSUFFICIENT,
        "local_files_found": "8-K press-release filings reachable via the no-key EDGAR feed (unclassified)",
        "free_sources_attempted": "SEC EDGAR 8-K Item 2.02 reachable no-key but guidance text unparsed",
        "providers": ["FMP", "Benzinga"], "best_provider": "FMP", "subscription_likely": True,
        "signals_unlocked": "guidance raise/cut x earnings/macro sensitivity",
        "blocker_addressed": "no structured guidance / press-release event store",
        "expected_event_count_gain": "medium", "expected_validation_quality_gain": "medium",
        "next_action": "FMP press-releases (bundled with earnings sub); classify guidance raise/cut"},
    F_FUND: {
        "base_status": DF_FREE_INSUFFICIENT,
        "local_files_found": "Norgate price-derived context (beta/vol); no fundamentals statements store",
        "free_sources_attempted": "SimFin bulk / SEC EDGAR XBRL possible no-key (not yet built)",
        "providers": ["FMP", "EODHD", "Intrinio", "SimFin"], "best_provider": "FMP",
        "subscription_likely": False, "signals_unlocked": "quality/leverage/valuation conditioning",
        "blocker_addressed": "no fundamentals statements for quality/leverage context",
        "expected_event_count_gain": "context (per-name overlay)",
        "expected_validation_quality_gain": "medium",
        "next_action": "build a free SimFin/SEC-XBRL fundamentals overlay before any paid key"},
    F_SECTOR: {
        "base_status": DF_LOCAL,
        "local_files_found": "Norgate GICS / sector_rel_str_60 + cohort_sector_lead live in the grid",
        "free_sources_attempted": "local Norgate (sufficient)",
        "providers": [], "best_provider": "", "subscription_likely": False,
        "signals_unlocked": "sector-leadership x macro driver family",
        "blocker_addressed": "none — fully local",
        "expected_event_count_gain": "n/a (active)", "expected_validation_quality_gain": "n/a (active)",
        "next_action": "none — continue mining the sector-context matrix"},
    F_MACRO: {
        "base_status": DF_LOCAL,
        "local_files_found": "Norgate cross-asset + on-disk FRED -> drv_* shocks live in the grid",
        "free_sources_attempted": "local Norgate/FRED (sufficient)",
        "providers": [], "best_provider": "", "subscription_likely": False,
        "signals_unlocked": "rates/oil/usd/credit/vix/commodity/market shock families",
        "blocker_addressed": "none — fully local",
        "expected_event_count_gain": "n/a (active)", "expected_validation_quality_gain": "n/a (active)",
        "next_action": "none — continue mining the existing macro-sensitivity matrix"},
    F_LIQ: {
        "base_status": DF_LOCAL,
        "local_files_found": "Norgate price/volume -> beta/vol cohorts + vol_compress live in the grid",
        "free_sources_attempted": "local Norgate (sufficient)",
        "providers": [], "best_provider": "", "subscription_likely": False,
        "signals_unlocked": "beta/vol/liquidity conditioning + tail-risk repair filters",
        "blocker_addressed": "none — fully local",
        "expected_event_count_gain": "n/a (active)", "expected_validation_quality_gain": "n/a (active)",
        "next_action": "none — use as the structural tail/vol filter layer"},
}

# Top blockers the first paid subscription should attack.
_TOP_BLOCKERS = {F_EARN, F_REV}

# Output artifacts (committed-safe) — 31 files.
ARTIFACTS = [
    "phase8l_data_family_expansion_signal_factory.json", "factory_state_summary.json",
    "factory_run_log.csv", "wave_registry.csv", "missing_data_family_matrix.csv",
    "local_cache_discovery_report.csv", "free_no_key_activation_report.csv", "provider_key_inventory.csv",
    "provider_discovery_log.csv", "provider_decision_matrix.csv", "provider_priority_ranking.csv",
    "provider_bundle_recommendation.csv", "provider_expected_signal_impact.csv",
    "provider_cost_value_report.csv", "provider_free_trial_plan.csv", "provider_activation_order.csv",
    "provider_acquisition_commands.ps1", "data_family_unlocked_signal_specs.csv",
    "tail_risk_repair_scoreboard.csv", "provider_expansion_required_scoreboard.csv",
    "autonomous_signal_scoreboard.csv", "confirmed_alpha_signals.csv", "promising_alpha_signals.csv",
    "provider_required_signals.csv", "rejected_alpha_signals.csv", "trade_idea_candidate_registry.csv",
    "best_trade_idea_candidates.csv", "validation_skeptic_report.csv", "multiple_testing_report.csv",
    "research_director_decision.json", "phase8m_next_plan.json",
]

# Durable runtime-state filenames (live on D:).
STATE_FILES = {
    "factory_state": "factory_state.json", "research_memory": "research_memory.json",
    "wave_registry": "wave_registry.csv", "hypothesis_bank": "hypothesis_bank_registry.csv",
    "experiment_results": "experiment_results.csv", "candidate_registry": "candidate_signal_registry.csv",
    "trade_ideas": "trade_idea_candidate_registry.csv", "promotion_log": "signal_promotion_log.csv",
    "graveyard": "rejected_hypothesis_graveyard.csv", "provider_blockers": "provider_blocker_registry.csv",
    "data_family_matrix": "missing_data_family_matrix.csv", "run_log": "factory_run_log.csv",
    "next_action": "next_action_decision.json",
}


# =========================================================================== #
# Provider discovery — keys by name/presence only, never printed.
# =========================================================================== #
def detect_keys() -> Dict[str, bool]:
    """Presence-only inventory of the provider env vars. Values are NEVER read into output."""
    return {k: bool(os.environ.get(k)) for k in KEY_NAMES}


def provider_discovery_rows(readiness: Dict[str, bool]) -> List[dict]:
    rows = []
    for k in KEY_NAMES:
        serves = sorted({f for p in PROVIDERS.values() if p["env_var"] == k for f in p["families"]})
        rows.append({"env_var": k, "present": bool(readiness.get(k)),
                     "value_read": False, "families_served": ";".join(serves) or "(none mapped)",
                     "note": "presence-only check; key value never read or printed"})
    return rows


def provider_key_inventory_rows(readiness: Dict[str, bool]) -> List[dict]:
    """Per-key inventory: which providers each env var unlocks and which families they serve."""
    rows = []
    for k in KEY_NAMES:
        provs = sorted({n for n, p in PROVIDERS.items() if p["env_var"] == k})
        serves = sorted({f for p in PROVIDERS.values() if p["env_var"] == k for f in p["families"]})
        rows.append({"env_var": k, "key_present": bool(readiness.get(k)), "value_read": False,
                     "providers": ";".join(provs) or "(none)",
                     "families_served": ";".join(serves) or "(none)",
                     "note": "presence-only; value never read or printed"})
    return rows


def _family_provider_candidates(family: str) -> List[str]:
    """Providers serving a family, free sources FIRST (FINRA/GDELT/SEC EDGAR before paid)."""
    free = [n for n, p in PROVIDERS.items() if family in p["families"] and p["free"]]
    paid = [n for n, p in PROVIDERS.items() if family in p["families"] and not p["free"]]
    spec = FAMILY_SPECS.get(family, {})
    pref = [n for n in spec.get("providers", []) if n in paid]
    paid_ordered = pref + [n for n in paid if n not in pref]
    return free + paid_ordered


def provider_decision_matrix_rows(readiness: Dict[str, bool]) -> List[dict]:
    """Per (family, candidate provider) ranked: free sources rank ahead of paid; the spec's preferred
    paid provider is the first paid entry. FMP ranks #1 paid for broad earnings + analyst revisions;
    FINRA ranks #1 for short interest; GDELT ranks #1 for news — all before any paid key."""
    rows = []
    for family in DATA_FAMILIES:
        cands = _family_provider_candidates(family)
        for rank, name in enumerate(cands, 1):
            p = PROVIDERS[name]
            env = p["env_var"]
            rows.append({"data_family": family, "rank_in_family": rank, "provider": name,
                         "is_free": p["free"], "env_var": env or "(none)",
                         "key_present": bool(env and readiness.get(env)),
                         "cost_usd_month_approx": p["cost_usd_month_approx"],
                         "recommended_first": rank == 1,
                         "endpoints_or_docs": p["endpoints_or_docs"]})
    return rows


def provider_priority_ranking_rows(readiness: Dict[str, bool]) -> List[dict]:
    """Overall provider priority. A provider that solves MORE high-priority families ranks above a
    specialist; the FIRST paid subscription should attack the current top blocker (broad earnings +
    analyst revisions) -> FMP. Free sources are listed as priority 0 (always attempt first)."""
    scored = []
    for name, p in PROVIDERS.items():
        fams = set(p["families"])
        blocker_hits = len(fams & _TOP_BLOCKERS)
        score = blocker_hits * 100 + len(fams)
        scored.append((name, p, score, blocker_hits))
    free = sorted([s for s in scored if s[1]["free"]], key=lambda s: -s[2])
    paid = sorted([s for s in scored if not s[1]["free"]], key=lambda s: -s[2])
    rows = []
    for i, (name, p, score, hits) in enumerate(free, 1):
        rows.append({"priority": 0, "tier": "FREE_ATTEMPT_FIRST", "order_within_tier": i,
                     "provider": name, "env_var": p["env_var"] or "(none)",
                     "n_families": len(p["families"]), "attacks_top_blocker": hits > 0,
                     "cost_usd_month_approx": p["cost_usd_month_approx"],
                     "rationale": "free/no-key — exhaust before any paid subscription"})
    for i, (name, p, score, hits) in enumerate(paid, 1):
        first = (i == 1)
        rows.append({"priority": i, "tier": "PAID", "order_within_tier": i, "provider": name,
                     "env_var": p["env_var"] or "(none)", "n_families": len(p["families"]),
                     "attacks_top_blocker": hits > 0,
                     "cost_usd_month_approx": p["cost_usd_month_approx"],
                     "rationale": ("FIRST SUBSCRIPTION: solves the top blocker (broad earnings + "
                                   "analyst revisions) and the most families at once"
                                   if first else
                                   "specialist/secondary — subscribe only if a candidate signal needs it")})
    return rows


def provider_expected_signal_impact_rows() -> List[dict]:
    rows = []
    for family in DATA_FAMILIES:
        s = FAMILY_SPECS[family]
        rows.append({"data_family": family, "best_provider": s["best_provider"] or "(local/free)",
                     "signals_unlocked": s["signals_unlocked"],
                     "expected_event_count_gain": s["expected_event_count_gain"],
                     "expected_validation_quality_gain": s["expected_validation_quality_gain"],
                     "current_blocker_addressed": s["blocker_addressed"]})
    return rows


def provider_cost_value_rows() -> List[dict]:
    """Cost vs families-unlocked vs top-blocker coverage. Free sources are infinite value (attempt first)."""
    rows = []
    for name, p in PROVIDERS.items():
        fams = p["families"]
        hits = len(set(fams) & _TOP_BLOCKERS)
        if p["free"]:
            value = "INFINITE (free)"
        elif hits and len(fams) >= 4:
            value = "HIGH"
        elif hits or len(fams) >= 3:
            value = "MEDIUM"
        else:
            value = "LOW/SPECIALIST"
        rows.append({"provider": name, "is_free": p["free"],
                     "cost_usd_month_approx": p["cost_usd_month_approx"],
                     "n_families": len(fams), "attacks_top_blocker": hits > 0,
                     "families": ";".join(fams), "value_rating": value,
                     "cost_note": "approximate public list tier — verify on provider site"})
    return rows


def provider_free_trial_rows() -> List[dict]:
    rows = []
    for name, p in PROVIDERS.items():
        if not (p["free"] or p["free_trial"]):
            continue
        rows.append({"provider": name, "is_free": p["free"], "has_free_trial": p["free_trial"],
                     "env_var": p["env_var"] or "(none)",
                     "trial_plan": ("use the free/no-key endpoint directly" if p["free"]
                                    else "register for the free key/trial; backfill a 1-2yr sample"),
                     "validate_during_trial": "history depth + coverage breadth vs the >=1000-event gate",
                     "families": ";".join(p["families"]),
                     "endpoints_or_docs": p["endpoints_or_docs"]})
    return rows


def provider_recommendation(readiness: Dict[str, bool]) -> dict:
    """The mandatory provider recommendation: smallest set, free-first, FMP as the first subscription."""
    free_first = [n for n, p in PROVIDERS.items() if p["free"]]
    fmp_present = bool(readiness.get("FMP_API_KEY"))
    # do-not-buy-yet: paid specialists that don't attack the top blocker (esp. options/news-only).
    do_not_buy_yet = sorted([n for n, p in PROVIDERS.items()
                             if not p["free"] and not (set(p["families"]) & _TOP_BLOCKERS)])
    return {
        "recommended_first_provider": "FMP",
        "recommended_first_provider_reason": (
            "FMP is the smallest single subscription that attacks BOTH top blockers (broad earnings "
            "surprise + analyst estimate revisions) and additionally unlocks transcripts, guidance/press "
            "releases, fundamentals and a filings feed — the most high-value families per dollar."
            + (" NOTE: an FMP key is already present — activate the backfill adapter, no purchase needed."
               if fmp_present else "")),
        "recommended_provider_bundle": ["FINRA(free)", "GDELT(free)", "SEC_EDGAR(free)", "FMP(paid)"],
        "do_not_buy_yet_list": do_not_buy_yet,
        "free_sources_to_exhaust_first": ["FINRA short interest", "GDELT news", "SEC EDGAR filings/insider/"
                                          "13F/8-K guidance", "SimFin/SEC-XBRL fundamentals"],
        "provider_activation_order": ["FINRA(free)", "GDELT(free)", "SEC_EDGAR(free)", "SimFin(free)",
                                      "FMP(first paid)", "Intrinio/Polygon options (only if a signal needs "
                                      "IV/skew)", "Benzinga/Tiingo/Polygon news (only for deep PIT history)"],
        "exact_env_vars_needed": ["FMP_API_KEY"],
        "free_sources_no_key_required": free_first}


def provider_bundle_recommendation_rows(readiness: Dict[str, bool]) -> List[dict]:
    rec = provider_recommendation(readiness)
    rows = []
    order = 1
    for name in [n for n, p in PROVIDERS.items() if p["free"]]:
        p = PROVIDERS[name]
        rows.append({"bundle_order": order, "provider": name, "tier": "FREE_EXHAUST_FIRST",
                     "env_var": p["env_var"] or "(none)", "cost_usd_month_approx": "free",
                     "families_unlocked": ";".join(p["families"]),
                     "in_recommended_bundle": True,
                     "rationale": "no-key free source — must be exhausted before any paid subscription"})
        order += 1
    fmp = PROVIDERS["FMP"]
    rows.append({"bundle_order": order, "provider": "FMP", "tier": "FIRST_PAID_SUBSCRIPTION",
                 "env_var": "FMP_API_KEY", "cost_usd_month_approx": fmp["cost_usd_month_approx"],
                 "families_unlocked": ";".join(fmp["families"]), "in_recommended_bundle": True,
                 "rationale": rec["recommended_first_provider_reason"]})
    order += 1
    for name in rec["do_not_buy_yet_list"]:
        p = PROVIDERS[name]
        rows.append({"bundle_order": order, "provider": name, "tier": "DO_NOT_BUY_YET",
                     "env_var": p["env_var"] or "(none)",
                     "cost_usd_month_approx": p["cost_usd_month_approx"],
                     "families_unlocked": ";".join(p["families"]), "in_recommended_bundle": False,
                     "rationale": "specialist — subscribe only if a candidate signal specifically needs it"})
        order += 1
    return rows


def provider_activation_order_rows(readiness: Dict[str, bool]) -> List[dict]:
    """Ordered activation steps with the exact command to run AFTER the user obtains a key."""
    steps = [
        ("FINRA", None, "build FINRA biweekly consolidated short-interest history (no key)",
         "ALWAYS — free", "python research/run_phase8l_data_family_expansion_signal_factory.py "
         "--resume --activate-live"),
        ("GDELT", None, "exhaust the GDELT free news/sentiment window (no key)",
         "ALWAYS — free", "python research/run_phase8l_data_family_expansion_signal_factory.py "
         "--resume --activate-live"),
        ("SEC_EDGAR", None, "scale the no-key SEC EDGAR filings/insider/13F/8-K feed",
         "ALWAYS — free", "python research/run_phase8l_data_family_expansion_signal_factory.py "
         "--resume --activate-live"),
        ("SimFin", None, "load free SimFin / SEC-XBRL fundamentals overlay",
         "ALWAYS — free", "python research/run_phase8l_data_family_expansion_signal_factory.py --resume"),
        ("FMP", "FMP_API_KEY", "subscribe FMP Starter; backfill earnings surprises + analyst revisions "
         "across S&P 1500; rebuild grid", "FIRST PAID — clears the top blocker",
         '$env:FMP_API_KEY = "<your_key>" ; python research/run_phase8l_data_family_expansion_signal_'
         'factory.py --resume --activate-live --stop-on-confirmed'),
        ("Intrinio", "INTRINIO_API_KEY", "trial Intrinio options (IV/skew)",
         "ONLY if a candidate signal needs IV/skew", '$env:INTRINIO_API_KEY = "<your_key>"'),
        ("Benzinga", "BENZINGA_API_KEY", "trial Benzinga for deep PIT news history",
         "ONLY for deep news history after GDELT", '$env:BENZINGA_API_KEY = "<your_key>"'),
    ]
    rows = []
    for i, (name, env, action, gating, cmd) in enumerate(steps, 1):
        rows.append({"activation_order": i, "provider": name, "env_var": env or "(none)",
                     "key_present": bool(env and readiness.get(env)),
                     "action": action, "gating_condition": gating, "activation_command": cmd})
    return rows


def provider_acquisition_ps1(readiness: Dict[str, bool]) -> str:
    """Committed-safe PowerShell: placeholder env-var assignments + signup pointers. NEVER a real key."""
    lines = [
        "# Phase 8-L provider acquisition commands (Windows PowerShell).",
        "# COMMITTED-SAFE: placeholders only. Replace <your_key> with your own key in YOUR shell.",
        "# Never commit a real key. Set the variable in your session, then re-run the factory.",
        "#",
        "# First subscription recommendation: FMP (broad earnings surprise + analyst revisions).",
        "# Always exhaust the FREE sources (FINRA short interest, GDELT news, SEC EDGAR filings) first.",
        "",
    ]
    for name, p in PROVIDERS.items():
        env = p["env_var"]
        if not env:
            lines.append(f"# {name}: FREE / no key required ({p['endpoints_or_docs']}).")
            continue
        present = "ALREADY PRESENT" if readiness.get(env) else "not set"
        lines.append(f"# {name} ({p['cost_usd_month_approx']}) — {p['endpoints_or_docs']}")
        lines.append(f"#   status: {present}")
        lines.append(f'$env:{env} = "<your_key>"')
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# =========================================================================== #
# Mandatory data-family acquisition matrix.
# =========================================================================== #
def _family_status(family: str, readiness: Dict[str, bool]) -> Tuple[str, bool, str]:
    """Resolve a family to a concrete status. A present candidate key promotes it to
    EXISTING_PROVIDER_KEY_ACTIVATED; otherwise the spec's no-key baseline holds."""
    spec = FAMILY_SPECS[family]
    cand = _family_provider_candidates(family)
    env_vars = [PROVIDERS[n]["env_var"] for n in cand if PROVIDERS[n]["env_var"]]
    present = next((e for e in env_vars if readiness.get(e)), None)
    if present:
        return DF_KEY_ACTIVATED, True, present
    best_env = PROVIDERS.get(spec["best_provider"], {}).get("env_var") if spec["best_provider"] else None
    return spec["base_status"], False, (best_env or "(none)")


def missing_data_family_matrix_rows(readiness: Dict[str, bool], inventory: dict) -> List[dict]:
    rows = []
    for family in DATA_FAMILIES:
        spec = FAMILY_SPECS[family]
        status, key_present, env_var = _family_status(family, readiness)
        cands = _family_provider_candidates(family)
        sub_required = spec["subscription_likely"] and not key_present
        hard_decision = ("ACTIVATE_LOCAL" if status == DF_LOCAL else
                         "ACTIVATE_FREE_NO_KEY" if status == DF_FREE_ACTIVATED else
                         "ACTIVATE_EXISTING_KEY" if status == DF_KEY_ACTIVATED else
                         "SUBSCRIBE_PAID_PROVIDER" if (status == DF_PAID_REQUIRED or sub_required) else
                         "EXHAUST_FREE_THEN_DECIDE")
        rows.append({
            "data_family": family, "current_status": status,
            "local_files_found": spec["local_files_found"],
            "free_no_key_sources_attempted": spec["free_sources_attempted"],
            "provider_keys_detected": ("yes:" + env_var) if key_present else "none",
            "providers_considered": ";".join(cands) or "(local only)",
            "best_provider": spec["best_provider"] or "(local/free)",
            "required_env_var": env_var,
            "subscription_likely_required": sub_required,
            "approximate_cost_if_known_or_unknown": (PROVIDERS.get(spec["best_provider"], {})
                                                     .get("cost_usd_month_approx", "UNKNOWN")
                                                     if spec["best_provider"] else "free/local"),
            "exact_endpoint_or_doc_reference_if_known": (PROVIDERS.get(spec["best_provider"], {})
                                                         .get("endpoints_or_docs", "UNKNOWN")
                                                         if spec["best_provider"]
                                                         else "local Norgate/FRED/SEC EDGAR"),
            "signals_unlocked": spec["signals_unlocked"],
            "current_blocker_addressed": spec["blocker_addressed"],
            "expected_event_count_gain": spec["expected_event_count_gain"],
            "expected_validation_quality_gain": spec["expected_validation_quality_gain"],
            "next_action": spec["next_action"],
            "hard_decision": hard_decision})
    return rows


def local_cache_discovery_rows(inventory: dict, aug_diag: dict) -> List[dict]:
    """Inventory the local on-disk caches actually discovered this run (no secrets, no raw paths beyond
    the well-known research data roots)."""
    n_earn_tk = inventory.get("n_earn_tickers", 0)
    n_earn_ev = inventory.get("n_earn_events", 0)
    n_filing = inventory.get("n_filing_events", 0)
    n_sym = inventory.get("n_panel_symbols", 0)
    n_obs = inventory.get("n_panel_obs", 0)
    return [
        {"cache": "earnings_event_cache", "data_family": F_EARN,
         "found": n_earn_ev > 0, "rows_or_symbols": f"{n_earn_tk} tickers / {n_earn_ev} events",
         "location_hint": "8-G load_earnings_events (Norgate-derived cache)",
         "status": DF_FREE_INSUFFICIENT if n_earn_tk and n_earn_tk < 200 else DF_LOCAL,
         "note": "thin coverage (~75 tickers) — broad earnings needs a provider"},
        {"cache": "sec_filing_event_cache", "data_family": F_FILINGS,
         "found": n_filing > 0, "rows_or_symbols": f"{n_filing} filing-event obs",
         "location_hint": "8-G/8-H no-key SEC EDGAR submissions cache",
         "status": DF_FREE_ACTIVATED if n_filing > 0 else DF_FREE_INSUFFICIENT,
         "note": "filing_event column live in the grid"},
        {"cache": "norgate_sensitivity_panel", "data_family": F_MACRO,
         "found": n_sym > 0, "rows_or_symbols": f"{n_sym} symbols / {n_obs} obs",
         "location_hint": "8-F persisted 8-E grid (Norgate + FRED)",
         "status": DF_LOCAL if n_sym > 0 else DF_ERROR,
         "note": "drv_* macro shocks + sensitivity cohorts live in the grid"},
        {"cache": "fred_macro_series", "data_family": F_MACRO,
         "found": n_sym > 0, "rows_or_symbols": "on-disk FRED series (rates/credit/usd/vix proxies)",
         "location_hint": "on-disk FRED cache used by 8-E driver construction",
         "status": DF_LOCAL if n_sym > 0 else DF_ERROR,
         "note": "fully local; no provider needed"},
        {"cache": "news_sentiment_store", "data_family": F_NEWS, "found": False,
         "rows_or_symbols": "0 (no PIT news store)", "location_hint": "(none on disk)",
         "status": DF_FREE_INSUFFICIENT, "note": "GDELT recent window only; deep history needs a provider"},
        {"cache": "options_iv_store", "data_family": F_OPTIONS, "found": False,
         "rows_or_symbols": "0", "location_hint": "(none on disk)", "status": DF_PAID_REQUIRED,
         "note": "no local options IV/skew data"},
        {"cache": "short_interest_store", "data_family": F_SHORT, "found": False,
         "rows_or_symbols": "0 (FINRA not yet backfilled)", "location_hint": "(none on disk)",
         "status": DF_FREE_INSUFFICIENT, "note": "FINRA free biweekly history collectable no-key"},
        {"cache": "fundamentals_store", "data_family": F_FUND, "found": False,
         "rows_or_symbols": "0 (price-derived context only)", "location_hint": "(none on disk)",
         "status": DF_FREE_INSUFFICIENT, "note": "SimFin/SEC-XBRL collectable free"},
    ]


def free_no_key_activation_rows(readiness: Dict[str, bool], inventory: dict) -> List[dict]:
    """What was attempted against free/no-key sources this run + the honest outcome."""
    gdelt = inventory.get("gdelt", {}) or {}
    finra = inventory.get("finra", {}) or {}
    edgar = inventory.get("edgar", {}) or {}
    n_filing = inventory.get("n_filing_events", 0)
    rows = [
        {"source": "SEC_EDGAR", "data_family": F_FILINGS, "key_required": False,
         "attempted": True, "reachable": n_filing > 0 or bool(edgar.get("n_from_cache")),
         "rows_returned": n_filing,
         "outcome": DF_FREE_ACTIVATED if n_filing > 0 else DF_FREE_INSUFFICIENT,
         "note": "no-key submissions feed; filing_event live in the grid"},
        {"source": "GDELT", "data_family": F_NEWS, "key_required": False,
         "attempted": True, "reachable": bool(gdelt.get("reachable", gdelt.get("ok", False))),
         "rows_returned": int(gdelt.get("n_rows", gdelt.get("n", 0)) or 0),
         "outcome": DF_FREE_INSUFFICIENT,
         "note": "connector live but only a recent window — no deep PIT history without a provider"},
        {"source": "FINRA", "data_family": F_SHORT, "key_required": False,
         "attempted": True, "reachable": bool(finra.get("reachable", finra.get("ok", False))),
         "rows_returned": int(finra.get("n_rows", finra.get("n", 0)) or 0),
         "outcome": DF_FREE_INSUFFICIENT,
         "note": "reg-SHO daily reachable; biweekly consolidated history collectable but not yet backfilled"},
        {"source": "SEC_EDGAR_FORM4", "data_family": F_INSIDER, "key_required": False,
         "attempted": False, "reachable": True, "rows_returned": 0, "outcome": DF_FREE_INSUFFICIENT,
         "note": "Form 4 reachable no-key; collector not yet built"},
        {"source": "SEC_EDGAR_13F", "data_family": F_13F, "key_required": False,
         "attempted": False, "reachable": True, "rows_returned": 0, "outcome": DF_FREE_INSUFFICIENT,
         "note": "13F reachable no-key; quarterly cadence weak for short-horizon drift"},
        {"source": "SimFin_SEC_XBRL", "data_family": F_FUND, "key_required": False,
         "attempted": False, "reachable": True, "rows_returned": 0, "outcome": DF_FREE_INSUFFICIENT,
         "note": "bulk fundamentals collectable free; overlay not yet built"},
    ]
    return rows


# =========================================================================== #
# Placeholder unlocked-signal specs (Part C) — specs ONLY, never fake results.
# =========================================================================== #
def data_family_unlocked_signal_specs_rows() -> List[dict]:
    specs = [
        {"spec_id": "SPEC-REV-SENS-20", "hypothesis": "true upward analyst revision in a rate-sensitive / "
         "sector-leading name drifts up 20d", "required_data_family": F_REV, "required_provider": "FMP",
         "required_columns_not_yet_in_grid": "analyst_revision_up (consensus EPS delta sign)",
         "sensitivity_cohort": "cohort_rates_neg / cohort_sector_lead", "horizon_days": 20,
         "gate": ">=1000 events, matched-control + recent + walk-forward, cost-stressed"},
        {"spec_id": "SPEC-NEWS-SENS-20", "hypothesis": "positive news-sentiment shock x sensitivity cohort "
         "drifts up 20d", "required_data_family": F_NEWS, "required_provider": "GDELT(free)->Benzinga(deep)",
         "required_columns_not_yet_in_grid": "news_sentiment_shock_z (PIT, entity-level)",
         "sensitivity_cohort": "cohort_surprise_sensitive", "horizon_days": 20,
         "gate": ">=1000 events, matched-control + recent + walk-forward, cost-stressed"},
        {"spec_id": "SPEC-OPT-DOWNSIDE-20", "hypothesis": "IV-skew / put-call extreme x downside-sensitive "
         "cohort precedes 20d move", "required_data_family": F_OPTIONS, "required_provider": "Intrinio",
         "required_columns_not_yet_in_grid": "iv_percentile, put_call_ratio, skew_25d",
         "sensitivity_cohort": "cohort_vol_spike_sens / cohort_high_beta", "horizon_days": 20,
         "gate": ">=1000 events, matched-control + recent + walk-forward, cost-stressed"},
        {"spec_id": "SPEC-SHORT-LIQ-20", "hypothesis": "rising short interest / days-to-cover x liquidity & "
         "earnings-confirmation drifts 20d", "required_data_family": F_SHORT,
         "required_provider": "FINRA(free)->Finnhub(deep)",
         "required_columns_not_yet_in_grid": "short_interest_increase, days_to_cover",
         "sensitivity_cohort": "cohort_low_beta + earn_recent_pos", "horizon_days": 20,
         "gate": ">=1000 events, matched-control + recent + walk-forward, cost-stressed"},
        {"spec_id": "SPEC-TONE-CONF-20", "hypothesis": "positive transcript / guidance tone x earnings or "
         "macro sensitivity confirms 20d drift", "required_data_family": F_TRANSCRIPT,
         "required_provider": "FMP", "required_columns_not_yet_in_grid": "transcript_tone_z, guidance_raise",
         "sensitivity_cohort": "cohort_surprise_sensitive / cohort_rates_neg", "horizon_days": 20,
         "gate": ">=1000 events, matched-control + recent + walk-forward, cost-stressed"},
        {"spec_id": "SPEC-OWNERSHIP-EVENT-20", "hypothesis": "insider-buy cluster / 13F ownership increase x "
         "event confirmation drifts 20d", "required_data_family": F_INSIDER,
         "required_provider": "SEC_EDGAR(free)",
         "required_columns_not_yet_in_grid": "insider_buy_cluster, inst_ownership_increase",
         "sensitivity_cohort": "cohort_sector_lead + filing_event", "horizon_days": 20,
         "gate": ">=1000 events, matched-control + recent + walk-forward, cost-stressed"},
    ]
    for s in specs:
        s["status"] = "SPEC_ONLY_DATA_REQUIRED"
        s["results_faked"] = False
        s["note"] = "placeholder experiment spec — NOT scored; requires the named data/provider first"
    return specs


# =========================================================================== #
# Dynamic hypothesis generator — one pre-registered bank per scoring wave.
# All conditions use columns that already exist in the persisted 8-E grid.
# =========================================================================== #
def generate_wave_bank(wave_id: str) -> List[SensSetup]:
    """Pre-register the hypothesis bank for one wave. Deterministic; fixed thresholds; no tuning; no
    sign flipping; >=30% challenges/placebos per scoring wave. Audit / acquisition / provider-decision
    waves return no scoreable setups — their work is data-family analysis, not grid scoring."""
    s: List[SensSetup] = []

    if wave_id == WAVE_PHASE8K_STATE_REBUILD:
        base = [RATES_SELLOFF, RATES_NEG, STRONG]
        s.append(_mk("S8L-RATES-MACRO-20", FAM_MACRO, SENS_A, "rates", "cohort_rates_neg", 20, base,
                     "Rebuild 8-K/S8E-011 anchor: rates sell-off x rate-sensitive cohort with positive "
                     "trend, 20d (full-coverage macro lead, no earnings confirmation)."))
        s.append(_mk("S8L-RATES-MACRO-SECLEAD-20", FAM_MACRO, SENS_A, "rates", "cohort_sector_lead", 20,
                     [RATES_SELLOFF, RATES_NEG, ("cohort_sector_lead", "ge", 1.0)],
                     "Rebuild best clean lead: S8E-011 restricted to sector leaders, 20d."))
        s.append(_mk("S8L-RATES-MACRO-VOLCOMP-20", FAM_MACRO, SENS_A, "rates", "cohort_rates_neg", 20,
                     base + [VOLCOMP],
                     "Rebuild S8E-011 + volatility-compression (calm-vol regime) filter, 20d."))
        s.append(_mk("S8L-RATES-EARNCONF-20", FAM_S8E011_EXT, SENS_A, "rates", "cohort_rates_neg", 20,
                     base + [RECENT_POS],
                     "Rebuild earnings-confirmed F20: S8E-011 + recent positive earnings surprise, 20d."))
        s.append(_mk("S8L-RATES-MACRO-LOWBETA-20", FAM_MACRO, SENS_A, "rates", "cohort_rates_neg", 20,
                     base + [LOWBETA],
                     "Rebuild S8E-011 + fixed low-beta filter (beta-tail repair preview), 20d."))
        s.append(_mk("S8L-CH-REBUILD-WRONGDIR-20", FAM_MACRO, VAL_A, "rates", "cohort_rates_neg", 20,
                     [("drv_rates_shock_z", "ge", SHOCK_Z), RATES_NEG, STRONG],
                     "CHALLENGE: rates RALLY (wrong shock sign) x rate-negative cohort — must not drift up.",
                     is_challenge=True))
        s.append(_mk("S8L-CH-REBUILD-NOCOHORT-20", FAM_MACRO, VAL_A, "rates", "", 20,
                     [RATES_SELLOFF, STRONG],
                     "CHALLENGE/placebo: rates sell-off, NO rate cohort — isolates the cohort lift.",
                     is_challenge=True, placebo=True))
        s.append(_mk("S8L-CH-REBUILD-EARNCONF-NOCOHORT-20", FAM_MACRO, VAL_A, "rates", "", 20,
                     [RATES_SELLOFF, STRONG, RECENT_POS],
                     "CHALLENGE/placebo: rates sell-off + earnings confirm, NO rate cohort — isolates cohort.",
                     is_challenge=True, placebo=True))

    elif wave_id == WAVE_EARNINGS_ANALYST_PROVIDER_DECISION:
        s.append(_mk("S8L-EARNCONF-RATES-20", FAM_S8E011_EXT, EARN_A, "rates", "cohort_rates_neg", 20,
                     [RATES_SELLOFF, RATES_NEG, STRONG, RECENT_POS],
                     "Earnings-confirmed macro lead (F20): does a recent positive surprise confirm the "
                     "rate-sensitive macro drift, 20d?"))
        s.append(_mk("S8L-EARN-VOLSENS-20", FAM_EARNINGS, EARN_A, "earnings_surprise",
                     "cohort_vol_spike_sens", 20, [POS, ("cohort_vol_spike_sens", "ge", 1.0)],
                     "Positive EPS surprise in a volatility-sensitive name drifts up 20d "
                     "(highest-EV provider-limited earnings candidate)."))
        s.append(_mk("S8L-EARN-SECLEAD-20", FAM_EARNINGS, EARN_A, "earnings_surprise",
                     "cohort_sector_lead", 20, [POS, ("cohort_sector_lead", "ge", 1.0)],
                     "Positive EPS surprise in a sector-leading name drifts up 20d."))
        s.append(_mk("S8L-EARN-HIGHBETA-20", FAM_EARNINGS, EARN_A, "earnings_surprise",
                     "cohort_high_beta", 20, [POS, ("cohort_high_beta", "ge", 1.0)],
                     "Positive EPS surprise in a high-beta name drifts up 20d."))
        s.append(_mk("S8L-REV-PROXY-RATES-20", FAM_REVISION, REV_A, "analyst_revision_proxy",
                     "cohort_rates_neg", 20, [REVUP, RATES_NEG],
                     "Improving surprise (revision PROXY, capped < CONFIRMED) in a rate-sensitive name, 20d "
                     "— provider decision: replace with true FMP consensus revisions."))
        s.append(_mk("S8L-CH-EARN-NEG-VOLSENS-20", FAM_EARNINGS, VAL_A, "earnings_surprise",
                     "cohort_vol_spike_sens", 20, [NEG, ("cohort_vol_spike_sens", "ge", 1.0)],
                     "CHALLENGE: NEGATIVE surprise in a volatility-sensitive name — wrong sign.",
                     is_challenge=True))
        s.append(_mk("S8L-CH-EARN-NEG-SECLEAD-20", FAM_EARNINGS, VAL_A, "earnings_surprise",
                     "cohort_sector_lead", 20, [NEG, ("cohort_sector_lead", "ge", 1.0)],
                     "CHALLENGE: NEGATIVE surprise in a sector leader — wrong sign, must not drift up.",
                     is_challenge=True))
        s.append(_mk("S8L-CH-EARN-NOCOHORT-20", FAM_EARNINGS, VAL_A, "earnings_surprise", "", 20, [POS],
                     "CHALLENGE/placebo: positive surprise, NO cohort — isolates the cohort lift.",
                     is_challenge=True, placebo=True))

    elif wave_id == WAVE_TAIL_RISK_REPAIR_SIGNAL_TESTS:
        base = [RATES_SELLOFF, RATES_NEG, STRONG]
        s.append(_mk("S8L-TAIL-MACRO-LOWBETA-20", FAM_MACRO, RSK_A, "rates", "cohort_rates_neg", 20,
                     base + [LOWBETA],
                     "Tail repair: S8E-011 + fixed extreme-beta exclusion (low-beta only) for worst-decile, "
                     "20d."))
        s.append(_mk("S8L-TAIL-MACRO-VOLCOMP-LOWBETA-20", FAM_MACRO, RSK_A, "rates", "cohort_rates_neg", 20,
                     base + [LOWBETA, VOLCOMP],
                     "Tail repair: S8E-011 + low-beta + top-volatility-quintile exclusion (vol-compress) for "
                     "worst-decile, 20d."))
        s.append(_mk("S8L-TAIL-SECLEAD-LOWBETA-20", FAM_S8E011_EXT, RSK_A, "rates", "cohort_sector_lead", 20,
                     [RATES_SELLOFF, RATES_NEG, ("cohort_sector_lead", "ge", 1.0), LOWBETA],
                     "Tail repair: best clean lead (sector leaders) + extreme-beta exclusion, 20d."))
        s.append(_mk("S8L-TAIL-EARNCONF-LOWBETA-20", FAM_S8E011_EXT, RSK_A, "rates", "cohort_rates_neg", 20,
                     base + [RECENT_POS, LOWBETA],
                     "Tail repair: F20 (earnings-confirmed) + extreme-beta exclusion for worst-decile, 20d."))
        s.append(_mk("S8L-TAIL-SECSTRONG-LOWBETA-20", FAM_S8E011_EXT, RSK_A, "rates", "cohort_rates_neg", 20,
                     [RATES_SELLOFF, RATES_NEG, SECTOR_STRONG, LOWBETA],
                     "Tail repair: S8E-011 in a strong sector (sector cap) + extreme-beta exclusion, 20d."))
        s.append(_mk("S8L-CH-TAIL-WRONGDIR-LOWBETA-20", FAM_MACRO, VAL_A, "rates", "cohort_rates_neg", 20,
                     [("drv_rates_shock_z", "ge", SHOCK_Z), RATES_NEG, STRONG, LOWBETA],
                     "CHALLENGE: rates RALLY (wrong shock sign) + low-beta filter — must not drift up.",
                     is_challenge=True))
        s.append(_mk("S8L-CH-TAIL-NOCOHORT-LOWBETA-20", FAM_MACRO, VAL_A, "rates", "", 20,
                     [RATES_SELLOFF, STRONG, LOWBETA],
                     "CHALLENGE/placebo: tail filter without the rate cohort — isolates the cohort lift.",
                     is_challenge=True, placebo=True))
        s.append(_mk("S8L-CH-TAIL-HIGHBETA-20", FAM_MACRO, VAL_A, "rates", "cohort_rates_neg", 20,
                     [RATES_SELLOFF, RATES_NEG, STRONG, ("cohort_high_beta", "ge", 1.0)],
                     "CHALLENGE: keep ONLY high-beta names (opposite of the tail filter) — should worsen the "
                     "tail, not repair it.", is_challenge=True))

    return _interleave_challenges(s) if s else []


# =========================================================================== #
# Trade-idea candidate registry (with trade-ready reason).
# =========================================================================== #
_FAMILY_DATA_DEP = {
    FAM_MACRO: [F_MACRO, F_LIQ],
    FAM_S8E011_EXT: [F_MACRO, F_EARN],
    FAM_EARNINGS: [F_EARN, F_SECTOR],
    FAM_REVISION: [F_REV],
    FAM_FILINGS: [F_FILINGS],
    FAM_NEWS: [F_NEWS], FAM_OPTIONS: [F_OPTIONS], FAM_SHORT: [F_SHORT],
}


def _trigger_text(cand_id: str, bank_by_id: Dict[str, SensSetup]) -> str:
    setup = bank_by_id.get(cand_id)
    if not setup:
        return "(see candidate registry conditions)"
    return " AND ".join(f"{c[0]} {c[1]} {c[2]}" for c in setup.conditions)


def trade_idea_candidate_rows(registry_rows: List[dict], bank_by_id: Dict[str, SensSetup]) -> List[dict]:
    """Promote promising/confirmed signals to paper-review-only trade ideas. Records whether each idea is
    trade-ready and exactly why not. No orders, no automation, no optimized weights."""
    leads = [r for r in registry_rows if not r.get("is_challenge")
             and r.get("alpha_promotion") in (ST_ALPHA_CONFIRMED, ST_ALPHA_PROMISING)]
    leads.sort(key=lambda r: (r.get("alpha_promotion") == ST_ALPHA_CONFIRMED,
                              (r.get("ev_after_25bps") or -9)), reverse=True)
    rows = []
    for i, r in enumerate(leads, 1):
        fam = r.get("family")
        limited = bool(r.get("provider_limited"))
        confirmed = r.get("alpha_promotion") == ST_ALPHA_CONFIRMED
        wd = r.get("worst_decile_mean")
        trade_ready = bool(confirmed and not limited)
        if trade_ready:
            reason_not = ""
        else:
            bits = []
            if not confirmed:
                bits.append("not CONFIRMED (promising only) — needs walk-forward + tail-repair confirmation")
            if limited:
                bits.append("coverage/provider-limited — acquire "
                            + ";".join(_FAMILY_DATA_DEP.get(fam, [])))
            reason_not = " ; ".join(bits) or "preview-only: manual review required, no orders"
        rows.append({
            "trade_idea_id": f"TI-{i:02d}", "source_signal_ids": r["candidate_id"],
            "thesis": r.get("reason"),
            "trigger_conditions": _trigger_text(r["candidate_id"], bank_by_id),
            "required_data_families": ";".join(_FAMILY_DATA_DEP.get(fam, [])),
            "current_validation_status": ("CLEAN_FULL_COVERAGE" if not limited
                                          else "COVERAGE_OR_PROVIDER_LIMITED"),
            "event_count": r.get("n_events"), "recent_event_count": r.get("n_recent_events"),
            "EV_after_25bps": r.get("ev_after_25bps"),
            "matched_control_lift": r.get("lift_vs_control"),
            "recent_lift": r.get("recent_lift_vs_control"), "tail_risk": wd,
            "current_blocker": ("none (local, full coverage)" if not limited
                                else "event coverage / provider history"),
            "provider_dependency": ";".join(_FAMILY_DATA_DEP.get(fam, [])) if limited else "none",
            "next_validation_step": ("apply fixed beta-tail/vol filter and re-validate stability + "
                                     "walk-forward" if not limited else
                                     "acquire broad earnings+revision provider feed; rebuild grid"),
            "promotion_status": r.get("alpha_promotion"),
            "whether_trade_ready": trade_ready, "reason_not_trade_ready": reason_not})
    return rows


# =========================================================================== #
# The factory.
# =========================================================================== #
class DataFamilyExpansionFactory:
    def __init__(self, out_dir: Path, state_dir: Path, *, dry_run: bool = False,
                 activate_live: bool = False, continue_when_bank_exhausted: bool = True):
        self.out_dir = out_dir
        self.state_dir = state_dir
        self.dry_run = dry_run
        self.activate_live = activate_live
        self.continue_when_bank_exhausted = continue_when_bank_exhausted
        self.bank: List[SensSetup] = []
        self.bank_by_id: Dict[str, SensSetup] = {}
        self.wave_assign: Dict[str, str] = {}
        self.activated_waves: List[str] = []
        self._next_wave_index = 0
        self.tested_ids: List[str] = []
        self.results_rows: List[dict] = []
        self.registry_rows: List[dict] = []
        self.promotion_rows: List[dict] = []
        self.graveyard_rows: List[dict] = []
        self.run_log_rows: List[dict] = []
        self.experiments: List["P8G.Experiment"] = []
        self.cycles_completed = 0
        self.created_utc = _utc_now_iso()
        self._panel: Optional[SensPanel] = None
        self._grid: Optional[pd.DataFrame] = None
        self._aug_diag: dict = {}
        self._batch_assign: Dict[str, int] = {}
        self._wave_limit = len(WAVES)
        self._next_action_doc: dict = {}

    # ---- persistence -------------------------------------------------------- #
    def _sp(self, key: str) -> Path:
        return self.state_dir / STATE_FILES[key]

    def _read_csv_rows(self, key: str) -> List[dict]:
        df = P8I._read_csv(self._sp(key))
        return df.to_dict("records") if not df.empty else []

    def _load_state(self) -> bool:
        st = P8I._read_json(self._sp("factory_state"))
        if not st:
            return False
        self.tested_ids = list(st.get("tested_ids", []))
        self.cycles_completed = int(st.get("cycles_completed", 0))
        self.created_utc = st.get("created_utc", self.created_utc)
        self._next_wave_index = int(st.get("next_wave_index", 0))
        for w in list(st.get("activated_waves", [])):
            self._append_wave_bank(w)
        self.results_rows = self._read_csv_rows("experiment_results")
        self.registry_rows = self._read_csv_rows("candidate_registry")
        self.promotion_rows = self._read_csv_rows("promotion_log")
        self.graveyard_rows = self._read_csv_rows("graveyard")
        self.run_log_rows = self._read_csv_rows("run_log")
        self.experiments = [P8J.AlphaResearchDaemon._experiment_from_reg(r) for r in self.registry_rows
                            if str(r.get("needs_provider")).strip().lower() not in ("true", "1", "1.0")]
        for r in self.results_rows:
            cyc = r.get("cycle")
            if r.get("exp_id") is not None:
                self._batch_assign[r.get("exp_id")] = (None if pd.isna(cyc) else int(cyc))
        return True

    def _full_ledger(self) -> List["P8G.Experiment"]:
        seen = {e.exp_id for e in self.experiments}
        blocked = [e for e in P8G._blocked_family_experiments() if e.exp_id not in seen]
        return self.experiments + blocked

    # ---- waves -------------------------------------------------------------- #
    def _append_wave_bank(self, wave_id: str) -> int:
        if wave_id not in self.activated_waves:
            self.activated_waves.append(wave_id)
        added = 0
        for setup in generate_wave_bank(wave_id):
            if setup.setup_id in self.bank_by_id:
                continue
            self.bank.append(setup)
            self.bank_by_id[setup.setup_id] = setup
            self.wave_assign[setup.setup_id] = wave_id
            added += 1
        return added

    def _activate_next_wave(self) -> Optional[str]:
        if self._next_wave_index >= self._wave_limit:
            return None
        wave_id = WAVES[self._next_wave_index]
        self._next_wave_index += 1
        self._append_wave_bank(wave_id)
        return wave_id

    def _all_waves_done(self) -> bool:
        return self._next_wave_index >= self._wave_limit and not self._untested()

    def _wave_registry_rows(self) -> List[dict]:
        rows = []
        for i, wave_id in enumerate(self.activated_waves):
            ids = [sid for sid, w in self.wave_assign.items() if w == wave_id]
            scored = [sid for sid in ids if sid in set(self.tested_ids)]
            meta = WAVE_META.get(wave_id, {})
            if wave_id in _PROVIDER_DECISION_WAVES:
                stop_reason = "PROVIDER_REQUIREMENT_LOGGED"
            elif wave_id not in _SCORING_WAVES:
                stop_reason = "ANALYSIS_ONLY"
            else:
                stop_reason = "BANK_SCORED" if len(scored) == len(ids) else "IN_PROGRESS"
            rows.append({
                "wave_index": i, "wave_id": wave_id, "generated_from": "self_refill_director",
                "active_focus": meta.get("active_focus", ""),
                "data_families_targeted": ";".join(meta.get("data_families_targeted", [])),
                "hypothesis_bank_id": f"BANK-{i:02d}-{wave_id}",
                "experiments_generated": len(ids), "experiments_scored": len(scored),
                "stop_reason": stop_reason, "next_wave_reason": meta.get("next_wave_reason", "")})
        return rows

    def _hypothesis_bank_registry_rows(self) -> List[dict]:
        rows = []
        for i, wave_id in enumerate(self.activated_waves):
            ids = [sid for sid, w in self.wave_assign.items() if w == wave_id]
            chals = sum(1 for sid in ids if self.bank_by_id[sid].is_challenge)
            rows.append({"hypothesis_bank_id": f"BANK-{i:02d}-{wave_id}", "wave_id": wave_id,
                         "n_hypotheses": len(ids), "n_challenges": chals,
                         "challenge_fraction": (round(chals / len(ids), 3) if ids else 0.0),
                         "scoreable": wave_id in _SCORING_WAVES})
        return rows

    # ---- panel -------------------------------------------------------------- #
    def _ensure_panel(self) -> bool:
        if self._grid is not None:
            return not self._grid.empty
        panel = P8F.load_persisted_panel()
        if panel is None or not panel.ok or panel.grid.empty:
            self._panel = None
            self._grid = pd.DataFrame()
            self._aug_diag = {"error": "persisted 8-E panel unavailable"}
            return False
        earn = P8G.load_earnings_events()
        if self.activate_live and not self.dry_run:
            want = list(earn["ticker"].value_counts().index) if not earn.empty else []
            filings, _meta = P8H_safe_filings(want)
        else:
            filings, _meta = P8G.load_sec_filing_events(activate_live=False)
        grid, diag = P8G.augment_grid(panel.grid, earn, filings)
        self._panel, self._grid, self._aug_diag = panel, grid, diag
        return True

    # ---- queues ------------------------------------------------------------- #
    def _untested(self) -> List[SensSetup]:
        done = set(self.tested_ids)
        return [setup for setup in self.bank if setup.setup_id not in done]

    def _next_batch(self, size: int) -> List[SensSetup]:
        return self._untested()[:size]

    def _hyp_rows(self) -> List[dict]:
        done = set(self.tested_ids)
        rows = []
        for setup in self.bank:
            r = _hypothesis_row(setup, done, self._batch_assign)
            r["wave_id"] = self.wave_assign.get(setup.setup_id, "")
            rows.append(r)
        return rows

    # ---- one cycle ---------------------------------------------------------- #
    def _run_cycle(self, cycle_no: int, readiness, activation_rows, blockers, batch_size: int) -> dict:
        batch = self._next_batch(batch_size)
        scored = _score_batch(batch, self._grid, self._panel, cycle_no)
        for setup in batch:
            self.tested_ids.append(setup.setup_id)
            self._batch_assign[setup.setup_id] = cycle_no
        self.experiments.extend(scored)
        self.results_rows.extend(_result_row(e) for e in scored)
        self.promotion_rows.extend(_promotion_log_rows(scored, cycle_no))
        self.graveyard_rows.extend(_graveyard_rows(scored, cycle_no))
        self.registry_rows = P8I.candidate_registry_rows(self._full_ledger(),
                                                         {"coverage_or_provider_blocked": []})
        agg = _aggregate(self.registry_rows)
        rejected_fams = _rejected_families(self.registry_rows)
        queue_remaining = len(self._untested())
        action, action_reason = decide_next_action(agg, queue_remaining, readiness, rejected_fams)
        board = agent_task_board_rows(cycle_no, batch, scored, activation_rows, blockers)
        summary = agent_cycle_summary_rows(board)
        cur_wave = self.wave_assign.get(batch[0].setup_id, "") if batch else ""
        self.run_log_rows.append({
            "cycle": cycle_no, "utc": _utc_now_iso(), "wave_id": cur_wave, "batch_size": len(batch),
            "experiments_scored_total": len(self.tested_ids), "queue_remaining": queue_remaining,
            "n_confirmed": agg["n_confirmed"], "n_promising": agg["n_promising"],
            "n_provider_required": agg["n_provider_required"], "n_rejected": agg["n_rejected"],
            "next_action": action, "note": action_reason[:160]})
        self.cycles_completed = cycle_no
        return {"batch": batch, "scored": scored, "agg": agg, "action": action,
                "action_reason": action_reason, "board": board, "summary": summary,
                "rejected_fams": rejected_fams, "queue_remaining": queue_remaining}

    # ---- main loop ---------------------------------------------------------- #
    def run(self, *, once=False, max_cycles=None, max_experiments=None, max_waves=None,
            time_budget_minutes=None, resume=False, stop_on_confirmed=False,
            stop_on_provider_only=False, continue_when_bank_exhausted=None,
            heartbeat_seconds=0) -> dict:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        if continue_when_bank_exhausted is not None:
            self.continue_when_bank_exhausted = continue_when_bank_exhausted
        if max_waves is not None:
            self._wave_limit = max(1, min(len(WAVES), max_waves))
        started_mono = time.monotonic()
        started_utc = _utc_now_iso()
        if resume:
            self._load_state()
        if self._next_wave_index == 0 and not self.bank:
            self._activate_next_wave()
        if once:
            max_cycles = self.cycles_completed + 1

        readiness = detect_keys()
        blockers = provider_blocker_rows(readiness)
        panel_ok = self._ensure_panel()
        activation_rows, inventory = self._activate(readiness)
        safety_ok = True
        stop_reason: Optional[str] = None
        last_cycle: dict = {}

        if not panel_ok:
            agg = _aggregate(self.registry_rows)
            stop_reason = STOP_WAVE_BUDGET
            self._next_action_doc = {"action": "BUILD_BROADER_PANEL",
                                     "reason": "persisted 8-E panel unavailable; cannot score",
                                     "allowed_actions": list(ALLOWED_ACTIONS)}
            last_cycle = {"agg": agg, "action": "BUILD_BROADER_PANEL",
                          "action_reason": self._next_action_doc["reason"], "board": [], "summary": [],
                          "rejected_fams": [], "queue_remaining": len(self._untested()),
                          "batch": [], "scored": []}
        else:
            cycle_no = self.cycles_completed
            while True:
                stop_file = self._stop_file_present()
                agg = _aggregate(self.registry_rows)
                elapsed_min = (time.monotonic() - started_mono) / 60.0
                time_exhausted = bool(time_budget_minutes and elapsed_min >= time_budget_minutes)
                provider_only = self._provider_only(agg)
                stop_reason = evaluate_factory_stop(
                    agg, all_waves_done=self._all_waves_done(), cycles_done=self.cycles_completed,
                    experiments_scored=len(self.tested_ids), max_cycles=max_cycles,
                    max_experiments=max_experiments, time_exhausted=time_exhausted, stop_file=stop_file,
                    stop_on_confirmed=stop_on_confirmed, stop_on_provider_only=stop_on_provider_only,
                    provider_only=provider_only, safety_ok=safety_ok)
                if stop_reason is not None:
                    break
                if not self._untested():
                    if self.continue_when_bank_exhausted and self._next_wave_index < self._wave_limit:
                        w = self._activate_next_wave()
                        if heartbeat_seconds:
                            print(f"[{PHASE}] refill -> wave {self._next_wave_index}/{self._wave_limit} "
                                  f"{w} (+{len(self._untested())} queued)")
                        continue
                    if not (self._next_wave_index < self._wave_limit):
                        stop_reason = STOP_WAVE_BUDGET
                        break
                    stop_reason = STOP_EXPERIMENT_BUDGET
                    break
                cycle_no += 1
                if heartbeat_seconds:
                    print(f"[{PHASE}] heartbeat cycle={cycle_no} elapsed={elapsed_min:.2f}min "
                          f"wave={self._next_wave_index}/{self._wave_limit} "
                          f"tested={len(self.tested_ids)} queue={len(self._untested())}")
                last_cycle = self._run_cycle(cycle_no, readiness, activation_rows, blockers,
                                             HYPOTHESES_PER_CYCLE)
                self._next_action_doc = {"cycle": cycle_no, "action": last_cycle["action"],
                                         "reason": last_cycle["action_reason"],
                                         "allowed_actions": list(ALLOWED_ACTIONS)}

            if not last_cycle:
                agg = _aggregate(self.registry_rows)
                board = agent_task_board_rows(self.cycles_completed, [], [], activation_rows, blockers)
                last_cycle = {"agg": agg, "action": ACT_CONTINUE_LOCAL,
                              "action_reason": "no new cycle this run (resume/immediate stop)",
                              "board": board, "summary": agent_cycle_summary_rows(board),
                              "rejected_fams": _rejected_families(self.registry_rows),
                              "queue_remaining": len(self._untested()), "batch": [], "scored": []}
                self._next_action_doc = {"action": last_cycle["action"],
                                         "reason": last_cycle["action_reason"],
                                         "allowed_actions": list(ALLOWED_ACTIONS)}

        if panel_ok and not last_cycle.get("scored"):
            self.registry_rows = P8I.candidate_registry_rows(self._full_ledger(),
                                                             {"coverage_or_provider_blocked": []})
        full_ledger = self._full_ledger()
        rec, detail = P8I.derive_recommendation(panel_ok, full_ledger, readiness)
        options = P8I.ranked_next_options(full_ledger, readiness, {})
        report = self._assemble_report(started_utc, panel_ok, rec, detail, readiness, activation_rows,
                                       blockers, stop_reason, last_cycle, options, inventory, once,
                                       max_cycles, max_experiments, max_waves, time_budget_minutes)
        self._persist(readiness, blockers, inventory)
        self._emit_snapshots(report, readiness, activation_rows, blockers, last_cycle, options, rec,
                             inventory)
        return report

    def _provider_only(self, agg: dict) -> bool:
        local_left = any(self.wave_assign.get(setup.setup_id, "") in _SCORING_WAVES
                         for setup in self._untested())
        return (not agg["n_confirmed"] and not agg["n_clean_promising"] and not local_left
                and (agg["n_provider_required"] or agg["n_provider_limited"]))

    # ---- data activation (honest; reuse 8-I builders) ----------------------- #
    def _activate(self, readiness):
        earn = P8G.load_earnings_events()
        live = self.activate_live and not self.dry_run
        if live:
            want = list(earn["ticker"].value_counts().index) if not earn.empty else []
            filings, edgar_meta = P8H_safe_filings(want)
        else:
            filings, _f = P8G.load_sec_filing_events(activate_live=False)
            edgar_meta = {"cap": 0, "n_requested": 0, "n_from_cache": 0, "n_fetched": 0,
                          "error": ("dry-run" if self.dry_run else "live off")}
        news_rows, news_meta = P8I.P8H.news_sentiment_activation(activate_live=live)
        finra_rows, finra_meta = P8I.finra_short_interest_activation(activate_live=live)
        activation_rows = P8I.data_source_activation_log(earn, filings, edgar_meta, news_meta,
                                                         finra_meta, readiness, self._aug_diag)
        g = self._grid if self._grid is not None else pd.DataFrame()
        inventory = {"n_earn_tickers": int(earn["ticker"].nunique()) if not earn.empty else 0,
                     "n_earn_events": int(len(earn)),
                     "n_filing_events": int(self._aug_diag.get("n_filing_event_obs", 0)),
                     "n_panel_symbols": int(g["symbol"].nunique()) if not g.empty else 0,
                     "n_panel_obs": int(len(g)),
                     "gdelt": news_meta, "finra": finra_meta, "edgar": edgar_meta}
        return activation_rows, inventory

    def _stop_file_present(self) -> bool:
        try:
            return (self.state_dir / STOP_FILE_NAME).exists()
        except Exception:
            return False

    # ---- persistence -------------------------------------------------------- #
    def _persist(self, readiness, blockers, inventory) -> None:
        if self.dry_run:
            return
        self.state_dir.mkdir(parents=True, exist_ok=True)
        _write_json(self._sp("factory_state"), self._state_summary(readiness))
        _write_json(self._sp("research_memory"), self._research_memory(readiness, inventory))
        _write_json(self._sp("next_action"), self._next_action_doc)
        _write_csv(self._sp("wave_registry"), self._wave_registry_rows(), _WAVE_COLS)
        _write_csv(self._sp("hypothesis_bank"), self._hypothesis_bank_registry_rows(), _BANKREG_COLS)
        _write_csv(self._sp("experiment_results"), self.results_rows, _RESULT_COLS)
        _write_csv(self._sp("candidate_registry"), self.registry_rows, _REG_COLS)
        _write_csv(self._sp("trade_ideas"),
                   trade_idea_candidate_rows(self.registry_rows, self.bank_by_id) or [{"status": "NONE"}],
                   _TI_COLS if self.registry_rows else ["status"])
        _write_csv(self._sp("promotion_log"), self.promotion_rows or [{"status": "EMPTY"}],
                   _PROMO_COLS if self.promotion_rows else ["status"])
        _write_csv(self._sp("graveyard"), self.graveyard_rows or [{"status": "EMPTY"}],
                   _GRAVE_COLS if self.graveyard_rows else ["status"])
        _write_csv(self._sp("provider_blockers"), blockers, _BLOCKER_COLS)
        _write_csv(self._sp("data_family_matrix"),
                   missing_data_family_matrix_rows(readiness, inventory), _DFM_COLS)
        _write_csv(self._sp("run_log"), self.run_log_rows or [{"status": "NO_CYCLES"}],
                   _RUNLOG_COLS if self.run_log_rows else ["status"])

    # ---- summaries / memory ------------------------------------------------- #
    def _state_summary(self, readiness) -> dict:
        agg = _aggregate(self.registry_rows)
        return {"phase": PHASE, "created_utc": self.created_utc, "last_run_utc": _utc_now_iso(),
                "cycles_completed": self.cycles_completed, "experiments_scored": len(self.tested_ids),
                "bank_size": len(self.bank), "queue_remaining": len(self._untested()),
                "activated_waves": self.activated_waves, "next_wave_index": self._next_wave_index,
                "wave_limit": self._wave_limit, "n_waves_total": len(WAVES),
                "continue_when_bank_exhausted": self.continue_when_bank_exhausted,
                "tested_ids": self.tested_ids, "aggregate": agg,
                "any_provider_key": any(readiness.values()), "dry_run": self.dry_run}

    def _research_memory(self, readiness, inventory) -> dict:
        agg = _aggregate(self.registry_rows)
        return {"phase": PHASE, "generated_utc": _utc_now_iso(),
                "thesis": "data-family expansion: resolve every missing family to a concrete acquisition "
                          "decision while continuing to test local signals (tail-repair, earnings-confirm)",
                "confirmed_alpha_signals": agg["confirmed_ids"],
                "clean_promising_signals": agg["clean_promising_ids"],
                "provider_limited_signals": agg["provider_limited_ids"],
                "binding_constraint": ("event-data BREADTH (earnings feed ~75 tickers; no key for true "
                                       "revision/news/options/short) — the Norgate panel is NOT binding"),
                "data_inventory": {"n_earn_tickers": inventory.get("n_earn_tickers"),
                                   "n_filing_events": inventory.get("n_filing_events"),
                                   "n_panel_symbols": inventory.get("n_panel_symbols")},
                "first_subscription_recommendation": "FMP (broad earnings surprise + analyst revisions)",
                "free_before_paid": ["FINRA short interest", "GDELT news", "SEC EDGAR filings/insider/13F"],
                "provider_readiness": readiness}

    # ---- report ------------------------------------------------------------- #
    def _assemble_report(self, started_utc, panel_ok, rec, detail, readiness, activation_rows, blockers,
                         stop_reason, last_cycle, options, inventory, once, max_cycles, max_experiments,
                         max_waves, time_budget_minutes) -> dict:
        g = self._grid if self._grid is not None else pd.DataFrame()
        agg = _aggregate(self.registry_rows)
        full_ledger = self._full_ledger()
        budget = P8G._budget(full_ledger) if full_ledger else {"challenge_fraction": 0.0}
        matrix = missing_data_family_matrix_rows(readiness, inventory)
        fam_status_counts: Dict[str, int] = {}
        for r in matrix:
            fam_status_counts[r["current_status"]] = fam_status_counts.get(r["current_status"], 0) + 1
        rec_block = provider_recommendation(readiness)
        return {
            "phase": PHASE, "objective": OBJECTIVE, "generated_utc": started_utc,
            "stop_reason": stop_reason, "allowed_stop_conditions": list(ALLOWED_STOPS),
            "recommendation": rec, "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
            "decision_detail": detail, "daemon_roles": DAEMON_ROLES,
            "next_action": last_cycle.get("action"), "next_action_reason": last_cycle.get("action_reason"),
            "allowed_actions": list(ALLOWED_ACTIONS),
            "run_config": {"once": once, "max_cycles": max_cycles, "max_experiments": max_experiments,
                           "max_waves": max_waves, "time_budget_minutes": time_budget_minutes,
                           "dry_run": self.dry_run, "activate_live": self.activate_live,
                           "continue_when_bank_exhausted": self.continue_when_bank_exhausted,
                           "hypotheses_per_cycle": HYPOTHESES_PER_CYCLE},
            "waves": {"n_waves_total": len(WAVES), "n_waves_activated": len(self.activated_waves),
                      "wave_limit": self._wave_limit, "activated_waves": self.activated_waves,
                      "n_scoring_waves": len(_SCORING_WAVES), "registry": self._wave_registry_rows()},
            "loop": {"cycles_completed": self.cycles_completed,
                     "experiments_scored": len(self.tested_ids), "bank_size": len(self.bank),
                     "queue_remaining": len(self._untested()),
                     "hypothesis_banks_generated": len(self.activated_waves),
                     "challenge_fraction": budget.get("challenge_fraction")},
            "panel": {"panel_ok": panel_ok,
                      "n_symbols": int(g["symbol"].nunique()) if not g.empty else 0,
                      "n_obs": int(len(g)),
                      "date_range": ([str(g["date"].min())[:10], str(g["date"].max())[:10]]
                                     if not g.empty else [])},
            "candidates": {"n_real_tested": agg["n_real"], "n_testable": agg["n_testable"],
                           "n_confirmed": agg["n_confirmed"], "n_promising": agg["n_promising"],
                           "n_clean_promising": agg["n_clean_promising"],
                           "n_provider_limited": agg["n_provider_limited"],
                           "n_provider_required": agg["n_provider_required"],
                           "n_rejected": agg["n_rejected"], "confirmed_ids": agg["confirmed_ids"],
                           "clean_promising_ids": agg["clean_promising_ids"],
                           "provider_limited_ids": agg["provider_limited_ids"]},
            "data_families": {"n_families": len(DATA_FAMILIES),
                              "status_counts": fam_status_counts,
                              "allowed_statuses": list(ALLOWED_FAMILY_STATUSES),
                              "first_subscription_recommendation": "FMP",
                              "families_requiring_subscription": [
                                  r["data_family"] for r in matrix
                                  if r["subscription_likely_required"]],
                              "families_local_or_free": [
                                  r["data_family"] for r in matrix
                                  if r["current_status"] in (DF_LOCAL, DF_FREE_ACTIVATED,
                                                             DF_KEY_ACTIVATED)]},
            "provider_recommendation": rec_block,
            "rejected_families": last_cycle.get("rejected_fams", []),
            "best_current_path": (options[0]["option"] if options else ""),
            "top_next_options": options,
            "provider": {"any_key_present": any(readiness.values()), "n_keys_checked": len(KEY_NAMES),
                         "n_blocked_families": sum(1 for b in blockers if b["blocker_active"]),
                         "first_subscription": "FMP", "readiness": readiness},
            "trade_ready": {"any_trade_ready": False,
                            "reason": ("no CONFIRMED full-coverage signal yet; promising leads need "
                                       "tail-repair + walk-forward confirmation or provider data")},
            "how_to_run_longer": {
                "validation_once": "python research/run_phase8l_data_family_expansion_signal_factory.py "
                                   "--once",
                "full_factory": ("python research/run_phase8l_data_family_expansion_signal_factory.py "
                                 "--resume --stop-on-confirmed"),
                "long_campaign": ("python research/run_phase8l_data_family_expansion_signal_factory.py "
                                  "--time-budget-minutes 180 --max-experiments 400 --resume "
                                  "--stop-on-confirmed --heartbeat-seconds 30"),
                "manual_stop": f"create {self.state_dir / STOP_FILE_NAME} to halt before the next cycle"},
            "safety": self._safety_block(readiness),
        }

    def _safety_block(self, readiness) -> dict:
        return {
            "research_only": True, "local_first": True,
            "provider_keys_detected": any(readiness.values()), "provider_keys_value_read": False,
            "secrets_printed": False, "external_data_faked": False, "news_sentiment_faked": False,
            "short_interest_faked": False, "revision_is_labelled_proxy_not_confirmed": True,
            "unlocked_signal_specs_are_spec_only": True, "mock_fixtures_excluded": True,
            "point_in_time_join": True, "thresholds_fixed_a_priori": True,
            "thresholds_modified_after_results": False, "factor_signs_modified_after_results": False,
            "all_pre_registered": True, "packages_installed": False, "large_state_only_on_d": True,
            "optimized_weights": False, "regime_activation": False, "ml_fit": False,
            "failed_experiments_hidden": False, "live_trading_signals": False, "broker_or_orders": False,
            "automation_of_orders": False, "paper_trader_touched": False, "gcp_touched": False,
            "deployment": False, "committed": False, "pushed": False}

    # ---- snapshots (committed-safe) ----------------------------------------- #
    def _emit_snapshots(self, report, readiness, activation_rows, blockers, last_cycle, options, rec,
                        inventory) -> None:
        p = lambda n: self.out_dir / n
        ledger = self._full_ledger()
        matrix = missing_data_family_matrix_rows(readiness, inventory)
        ti_rows = trade_idea_candidate_rows(self.registry_rows, self.bank_by_id)
        # primary report + state + decisions
        _write_json(p("phase8l_data_family_expansion_signal_factory.json"), report)
        _write_json(p("factory_state_summary.json"), self._state_summary(readiness))
        _write_json(p("research_director_decision.json"), self._director_decision(report, options, rec))
        _write_json(p("phase8m_next_plan.json"), self._phase8m_plan(report, readiness, options, matrix))
        _write_csv(p("factory_run_log.csv"), self.run_log_rows or [{"status": "NO_CYCLES_RUN"}],
                   _RUNLOG_COLS if self.run_log_rows else ["status"])
        _write_csv(p("wave_registry.csv"), self._wave_registry_rows(), _WAVE_COLS)
        # data-family + local cache + free activation
        _write_csv(p("missing_data_family_matrix.csv"), matrix, _DFM_COLS)
        _write_csv(p("local_cache_discovery_report.csv"),
                   local_cache_discovery_rows(inventory, self._aug_diag), _LCD_COLS)
        _write_csv(p("free_no_key_activation_report.csv"),
                   free_no_key_activation_rows(readiness, inventory), _FNK_COLS)
        # provider discovery / decision artifacts
        _write_csv(p("provider_key_inventory.csv"), provider_key_inventory_rows(readiness), _PKI_COLS)
        _write_csv(p("provider_discovery_log.csv"), provider_discovery_rows(readiness), _PDISC_COLS)
        _write_csv(p("provider_decision_matrix.csv"), provider_decision_matrix_rows(readiness), _PDM_COLS)
        _write_csv(p("provider_priority_ranking.csv"), provider_priority_ranking_rows(readiness), _PPR_COLS)
        _write_csv(p("provider_bundle_recommendation.csv"),
                   provider_bundle_recommendation_rows(readiness), _PBR_COLS)
        _write_csv(p("provider_expected_signal_impact.csv"), provider_expected_signal_impact_rows(),
                   _PESI_COLS)
        _write_csv(p("provider_cost_value_report.csv"), provider_cost_value_rows(), _PCV_COLS)
        _write_csv(p("provider_free_trial_plan.csv"), provider_free_trial_rows(), _PFT_COLS)
        _write_csv(p("provider_activation_order.csv"), provider_activation_order_rows(readiness), _PAO_COLS)
        (self.out_dir / "provider_acquisition_commands.ps1").write_text(
            provider_acquisition_ps1(readiness), encoding="utf-8")
        # placeholder unlocked-signal specs (spec only, never fake results)
        _write_csv(p("data_family_unlocked_signal_specs.csv"),
                   data_family_unlocked_signal_specs_rows(), _SPEC_COLS)
        # scoreboards
        sb = P8I.alpha_scoreboard_rows(ledger)
        _write_csv(p("autonomous_signal_scoreboard.csv"), sb or [{"status": "EMPTY"}],
                   _ALPHA_SCORE_COLS if sb else ["status"])
        tail_ids = {sid for sid, w in self.wave_assign.items() if w == WAVE_TAIL_RISK_REPAIR_SIGNAL_TESTS}
        tail_sb = [r for r in sb if r.get("exp_id") in tail_ids]
        _write_csv(p("tail_risk_repair_scoreboard.csv"),
                   tail_sb or [{"status": "NO_TAIL_REPAIR_SCORED"}],
                   _ALPHA_SCORE_COLS if tail_sb else ["status"])
        prov_req = [r for r in sb if r.get("alpha_promotion") == ST_ALPHA_PROVIDER_REQUIRED]
        _write_csv(p("provider_expansion_required_scoreboard.csv"),
                   self._provider_expansion_rows(prov_req) or [{"status": "NO_PROVIDER_REQUIRED_SIGNAL"}],
                   _PEXP_COLS if prov_req else ["status"])
        confirmed = [r for r in sb if r.get("alpha_promotion") == ST_ALPHA_CONFIRMED]
        promising = [r for r in sb if r.get("alpha_promotion") == ST_ALPHA_PROMISING]
        rejected = [r for r in sb if r.get("alpha_promotion") == ST_REJECTED and not r.get("is_challenge")]
        _write_csv(p("confirmed_alpha_signals.csv"), confirmed or [{"status": "NO_CONFIRMED_ALPHA_SIGNAL"}],
                   _ALPHA_SCORE_COLS if confirmed else ["status"])
        _write_csv(p("promising_alpha_signals.csv"), promising or [{"status": "NO_PROMISING_ALPHA_SIGNAL"}],
                   _ALPHA_SCORE_COLS if promising else ["status"])
        _write_csv(p("provider_required_signals.csv"),
                   prov_req or [{"status": "NO_PROVIDER_REQUIRED_SIGNAL"}],
                   _ALPHA_SCORE_COLS if prov_req else ["status"])
        _write_csv(p("rejected_alpha_signals.csv"), rejected or [{"status": "NO_REJECTED_ALPHA_SIGNAL"}],
                   _ALPHA_SCORE_COLS if rejected else ["status"])
        # trade ideas
        _write_csv(p("trade_idea_candidate_registry.csv"), ti_rows or [{"status": "NO_TRADE_IDEA"}],
                   _TI_COLS if ti_rows else ["status"])
        best = ti_rows
        _write_csv(p("best_trade_idea_candidates.csv"), best or [{"status": "NO_TRADE_IDEA_CANDIDATE"}],
                   _TI_COLS if best else ["status"])
        # validation + multiple testing
        _write_csv(p("validation_skeptic_report.csv"),
                   validation_skeptic_rows(self.registry_rows) or [{"status": "NO_TESTABLE"}],
                   _SKEPTIC_COLS if validation_skeptic_rows(self.registry_rows) else ["status"])
        mt = P8I._multiple_testing(ledger)
        _write_csv(p("multiple_testing_report.csv"),
                   [{"metric": k, "value": (";".join(map(str, v)) if isinstance(v, list) else v)}
                    for k, v in mt.items()], ["metric", "value"])

    def _provider_expansion_rows(self, prov_req: List[dict]) -> List[dict]:
        rows = []
        for r in prov_req:
            fam = r.get("family")
            dep = _FAMILY_DATA_DEP.get(fam, [])
            best = next((FAMILY_SPECS[d]["best_provider"] for d in dep if d in FAMILY_SPECS
                         and FAMILY_SPECS[d]["best_provider"]), "FMP")
            rows.append({"candidate_id": r.get("exp_id"), "family": fam,
                         "alpha_promotion": r.get("alpha_promotion"),
                         "required_data_families": ";".join(dep),
                         "provider_to_unlock": best,
                         "env_var": PROVIDERS.get(best, {}).get("env_var", "(none)") or "(none)",
                         "expected_event_gain": (FAMILY_SPECS.get(dep[0], {}).get(
                             "expected_event_count_gain", "unknown") if dep else "unknown"),
                         "note": "provider data required to make this family testable/confirmable"})
        return rows

    def _director_decision(self, report, options, rec) -> dict:
        rec_block = report["provider_recommendation"]
        return {"phase": PHASE, "generated_utc": report["generated_utc"], "recommendation": rec,
                "stop_reason": report["stop_reason"], "next_action": report["next_action"],
                "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
                "allowed_alpha_statuses": list(ALLOWED_ALPHA_STATUSES),
                "allowed_actions": list(ALLOWED_ACTIONS),
                "allowed_stop_conditions": list(ALLOWED_STOPS),
                "allowed_family_statuses": list(ALLOWED_FAMILY_STATUSES),
                "decision_detail": report["decision_detail"],
                "waves_activated": report["waves"]["n_waves_activated"],
                "provider_recommendation": rec_block,
                "first_subscription_recommendation": "FMP (broad earnings surprise + analyst revisions)",
                "free_before_paid": rec_block["free_sources_to_exhaust_first"],
                "do_not_buy_yet": rec_block["do_not_buy_yet_list"],
                "best_current_path": report["best_current_path"], "top_next_options": options,
                "any_trade_ready": report["trade_ready"]["any_trade_ready"],
                "binding_constraint": "event-data BREADTH and provider history; NOT the Norgate panel",
                "anti_p_hacking": {"all_pre_registered": True, "thresholds_fixed_a_priori": True,
                                   "thresholds_modified_after_results": False,
                                   "factor_signs_modified_after_results": False,
                                   "challenge_fraction": report["loop"]["challenge_fraction"],
                                   "external_data_never_faked": True,
                                   "unlocked_signal_specs_are_spec_only": True,
                                   "revision_proxy_capped_below_confirmed": True,
                                   "optimized_weights": False,
                                   "combinations_use_only_existing_real_columns": True},
                "stop_conditions_honored": [
                    "local data first; Norgate for price/macro; no package install",
                    "no threshold change to rescue a result", "no factor-sign flipping",
                    "no weight optimization", "no regime activation", "no ML fitting",
                    "external data never faked", "revision proxy labelled + capped",
                    "no secrets printed (keys by name/presence only)", "no live trading signals",
                    "no broker/orders/automation", "no Paper Trader / GCP / deployment",
                    "failed experiments not hidden", "no commit", "no push"]}

    def _phase8m_plan(self, report, readiness, options, matrix) -> dict:
        needs_sub = [r["data_family"] for r in matrix if r["subscription_likely_required"]]
        rec_block = report["provider_recommendation"]
        return {"from_phase": PHASE, "next_phase": "8-M", "recommendation": report["recommendation"],
                "stop_reason": report["stop_reason"], "next_action": report["next_action"],
                "best_current_path": report["best_current_path"], "ranked_next_options": options,
                "first_subscription_recommendation": "FMP",
                "recommended_provider_bundle": rec_block["recommended_provider_bundle"],
                "do_not_buy_yet": rec_block["do_not_buy_yet_list"],
                "free_sources_to_exhaust_first": rec_block["free_sources_to_exhaust_first"],
                "provider_activation_order": rec_block["provider_activation_order"],
                "exact_env_vars_needed": rec_block["exact_env_vars_needed"],
                "families_requiring_subscription": needs_sub,
                "binding_constraint": "event-data breadth + provider history (earnings/revision/news/short)",
                "next_steps": [o["option"] for o in options],
                "provider_readiness": readiness,
                "resume_command": ("python research/run_phase8l_data_family_expansion_signal_factory.py "
                                   "--resume --stop-on-confirmed"),
                "after_fmp_command": ('$env:FMP_API_KEY = "<your_key>" ; python research/'
                                      "run_phase8l_data_family_expansion_signal_factory.py --resume "
                                      "--activate-live --stop-on-confirmed"),
                "hard_constraints": [
                    "local data first; Norgate + FRED for price/macro", "do not install packages",
                    "large state on D: only; repo gets summaries/snapshots", "never print secrets",
                    "bounded no-key collection; point-in-time joins only", "thresholds fixed a priori",
                    "no Paper Trader / GCP / deployment", "no broker/order/automation",
                    "no live trading signals", "no weight optimization", "no factor-sign flipping",
                    "no regime activation", "external data never faked",
                    "do not hide failed experiments", "do not commit", "do not push"]}


# Column orders for the artifacts.
_WAVE_COLS = ["wave_index", "wave_id", "generated_from", "active_focus", "data_families_targeted",
              "hypothesis_bank_id", "experiments_generated", "experiments_scored", "stop_reason",
              "next_wave_reason"]
_BANKREG_COLS = ["hypothesis_bank_id", "wave_id", "n_hypotheses", "n_challenges", "challenge_fraction",
                 "scoreable"]
_TI_COLS = ["trade_idea_id", "source_signal_ids", "thesis", "trigger_conditions",
            "required_data_families", "current_validation_status", "event_count", "recent_event_count",
            "EV_after_25bps", "matched_control_lift", "recent_lift", "tail_risk", "current_blocker",
            "provider_dependency", "next_validation_step", "promotion_status", "whether_trade_ready",
            "reason_not_trade_ready"]
_DFM_COLS = ["data_family", "current_status", "local_files_found", "free_no_key_sources_attempted",
             "provider_keys_detected", "providers_considered", "best_provider", "required_env_var",
             "subscription_likely_required", "approximate_cost_if_known_or_unknown",
             "exact_endpoint_or_doc_reference_if_known", "signals_unlocked", "current_blocker_addressed",
             "expected_event_count_gain", "expected_validation_quality_gain", "next_action",
             "hard_decision"]
_LCD_COLS = ["cache", "data_family", "found", "rows_or_symbols", "location_hint", "status", "note"]
_FNK_COLS = ["source", "data_family", "key_required", "attempted", "reachable", "rows_returned",
             "outcome", "note"]
_PKI_COLS = ["env_var", "key_present", "value_read", "providers", "families_served", "note"]
_PDISC_COLS = ["env_var", "present", "value_read", "families_served", "note"]
_PDM_COLS = ["data_family", "rank_in_family", "provider", "is_free", "env_var", "key_present",
             "cost_usd_month_approx", "recommended_first", "endpoints_or_docs"]
_PPR_COLS = ["priority", "tier", "order_within_tier", "provider", "env_var", "n_families",
             "attacks_top_blocker", "cost_usd_month_approx", "rationale"]
_PBR_COLS = ["bundle_order", "provider", "tier", "env_var", "cost_usd_month_approx", "families_unlocked",
             "in_recommended_bundle", "rationale"]
_PESI_COLS = ["data_family", "best_provider", "signals_unlocked", "expected_event_count_gain",
              "expected_validation_quality_gain", "current_blocker_addressed"]
_PCV_COLS = ["provider", "is_free", "cost_usd_month_approx", "n_families", "attacks_top_blocker",
             "families", "value_rating", "cost_note"]
_PFT_COLS = ["provider", "is_free", "has_free_trial", "env_var", "trial_plan", "validate_during_trial",
             "families", "endpoints_or_docs"]
_PAO_COLS = ["activation_order", "provider", "env_var", "key_present", "action", "gating_condition",
             "activation_command"]
_SPEC_COLS = ["spec_id", "hypothesis", "required_data_family", "required_provider",
              "required_columns_not_yet_in_grid", "sensitivity_cohort", "horizon_days", "gate", "status",
              "results_faked", "note"]
_PEXP_COLS = ["candidate_id", "family", "alpha_promotion", "required_data_families", "provider_to_unlock",
              "env_var", "expected_event_gain", "note"]
_RUNLOG_COLS = ["cycle", "utc", "wave_id", "batch_size", "experiments_scored_total", "queue_remaining",
                "n_confirmed", "n_promising", "n_provider_required", "n_rejected", "next_action", "note"]


# =========================================================================== #
# CLI.
# =========================================================================== #
def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Phase 8-L Data-Family Expansion & Signal Factory")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--state-dir", default=str(STATE_ROOT_DEFAULT))
    ap.add_argument("--once", action="store_true", help="run exactly one scoring cycle (validation)")
    ap.add_argument("--max-cycles", type=int, default=None, help="stop after N cycles")
    ap.add_argument("--max-experiments", type=int, default=None,
                    help="stop after N experiments scored (cumulative)")
    ap.add_argument("--max-waves", type=int, default=None,
                    help="generate at most N research waves (default: all 12)")
    ap.add_argument("--time-budget-minutes", type=float, default=None,
                    help="stop once wall-clock minutes are exhausted")
    ap.add_argument("--resume", action="store_true", help="resume durable state from the state dir")
    ap.add_argument("--dry-run", action="store_true",
                    help="do not persist runtime state on D: and do not do any network collection")
    ap.add_argument("--activate-live", action="store_true",
                    help="scale no-key SEC EDGAR + retry GDELT + probe FINRA (cached on D:)")
    ap.add_argument("--stop-on-confirmed", action="store_true",
                    help="halt as soon as a CONFIRMED alpha signal is found")
    ap.add_argument("--stop-on-provider-only", action="store_true",
                    help="halt when the only remaining levers are provider-gated")
    ap.add_argument("--stop-when-bank-exhausted", action="store_true",
                    help="DISABLE self-refill: stop when the current bank empties (default: keep refilling)")
    ap.add_argument("--heartbeat-seconds", type=int, default=0,
                    help="emit a heartbeat line each cycle / refill (liveness for long campaigns)")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    factory = DataFamilyExpansionFactory(
        Path(args.out_dir), Path(args.state_dir), dry_run=args.dry_run,
        activate_live=args.activate_live,
        continue_when_bank_exhausted=not args.stop_when_bank_exhausted)
    try:
        report = factory.run(once=args.once, max_cycles=args.max_cycles,
                             max_experiments=args.max_experiments, max_waves=args.max_waves,
                             time_budget_minutes=args.time_budget_minutes, resume=args.resume,
                             stop_on_confirmed=args.stop_on_confirmed,
                             stop_on_provider_only=args.stop_on_provider_only,
                             heartbeat_seconds=args.heartbeat_seconds)
    except Exception as exc:  # pragma: no cover - top-level guard
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        _write_json(out / "phase8l_data_family_expansion_signal_factory.json",
                    {"phase": PHASE, "recommendation": REC_ERROR, "error": repr(exc),
                     "generated_utc": _utc_now_iso()})
        print(f"[{PHASE}] ERROR: {exc!r}")
        return 1
    _print_summary(report)
    return 0


def _print_summary(report: dict) -> None:
    loop, cand, waves, fam = (report["loop"], report["candidates"], report["waves"],
                              report["data_families"])
    print(f"[{PHASE}] stop_reason = {report['stop_reason']} | recommendation = {report['recommendation']}")
    print(f"[{PHASE}] waves={waves['n_waves_activated']}/{waves['n_waves_total']} "
          f"cycles={loop['cycles_completed']} scored={loop['experiments_scored']}/{loop['bank_size']} "
          f"banks={loop['hypothesis_banks_generated']} challenge_frac={loop['challenge_fraction']}")
    print(f"[{PHASE}] candidates: confirmed={cand['n_confirmed']} promising={cand['n_promising']} "
          f"(clean={cand['n_clean_promising']} limited={cand['n_provider_limited']}) "
          f"provider_required={cand['n_provider_required']} rejected={cand['n_rejected']}")
    print(f"[{PHASE}] data-families: {fam['status_counts']} ; first subscription = "
          f"{fam['first_subscription_recommendation']}")
    print(f"[{PHASE}] next_action = {report['next_action']} :: {str(report['next_action_reason'])[:88]}")
    print(f"[{PHASE}] best path: {report['best_current_path'][:96]}")


if __name__ == "__main__":
    raise SystemExit(main())
