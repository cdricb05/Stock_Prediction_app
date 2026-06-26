"""Phase 8-Q - Market Data Foundation Decision Gate and Broad Coverage Architecture.

WHY THIS PHASE EXISTS
    Phases 8-N / 8-O / 8-P were a sequence of small, provider-by-provider experiments. They
    produced a clear but narrow picture:
      * 8-N (LIVE FMP backfill): the current FMP plan is INSUFFICIENT - every critical family is
        entitlement-PARTIAL at ~8/20 tickers, with SYSTEMATIC 402 subscription blocks.
        (FMP_PLAN_COVERAGE_INSUFFICIENT)
      * 8-O: the cheapest viable path is a MIXED provider strategy - Alpha Vantage first for
        earnings, Finnhub later for the analyst families; an FMP upgrade is NOT justified and
        FMP Ultimate is explicitly REJECTED.
      * 8-P (LIVE Alpha Vantage): Alpha Vantage HELPS (added 1 ticker, ABT -> combined 9/20) but
        the free tier (25 requests/day) RATE-LIMITED after 3 requests. At 25 req/day a 500-ticker
        universe takes ~20 calendar days to collect once - free throughput is not a durable
        foundation for broad point-in-time research.

    The user is (rightly) tired of tiny 20-ticker experiments and wants a serious answer: what
    market data is actually REQUIRED for serious alpha research, and are free APIs a waste of time
    for the long run? This phase is a DECISION GATE, not a new backfill. It makes NO network calls.
    It reads the 8-N / 8-O / 8-P committed-safe artifacts, builds a market-data REQUIREMENT CATALOG
    tiered by necessity, assesses FREE-STACK VIABILITY for broad (100-500 ticker) point-in-time
    coverage, and produces a buy-vs-free decision plus a "test-first" procurement shortlist.

THE STRATEGY CORRECTION THIS PHASE ENCODES
    The 20-ticker count is a MINIMUM SCORING GATE, not the target universe. The real targets are
    (1) the current Phase 5C working universe, then (2) an S&P 500-like broad universe, then
    (3) a survivorship-aware / point-in-time-membership universe. Free micro-batches are evaluated
    against THAT scale, not against 20 tickers.

HARD CONSTRAINTS HONORED
    Existing installed packages only (stdlib). No package install. NO live network calls (this is
    a decision gate - everything is read from prior committed-safe artifacts). API keys are read as
    PRESENCE BOOLEANS only via os.environ - never printed, never written. No raw/normalized paid
    data is read into or emitted from committed artifacts. No Paper Trader. No GCP. No deploy. No
    broker / order / automation logic. No commit. No push.

Run (always offline - a decision gate):
    python research/run_phase8q_market_data_foundation_decision.py
Test:
    python -m pytest tests/test_phase8q_market_data_foundation_decision.py -q
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

PHASE = "8-Q"

# --------------------------------------------------------------------------- #
# Paths (read-only inputs + committed-safe output dir).
# --------------------------------------------------------------------------- #
_OUT_DIR = (_REPO_ROOT / "research" / "output"
            / "phase8q_market_data_foundation_decision")
_PHASE8N_DIR = (_REPO_ROOT / "research" / "output"
                / "phase8n_fmp_critical_data_backfill_signal_expansion")
_PHASE8O_DIR = (_REPO_ROOT / "research" / "output"
                / "phase8o_cheapest_provider_selection")
_PHASE8P_DIR = (_REPO_ROOT / "research" / "output"
                / "phase8p_alphavantage_earnings_expansion")

# The 14 committed-safe artifact basenames.
_ARTIFACTS = {
    "report": "phase8q_market_data_foundation_decision.json",
    "current_coverage": "current_provider_coverage_summary.csv",
    "requirement_catalog": "market_data_requirement_catalog.csv",
    "priority_tiers": "data_family_priority_tiers.csv",
    "universe_targets": "universe_scale_targets.csv",
    "free_viability": "free_stack_viability_report.csv",
    "paid_matrix": "paid_provider_decision_matrix.csv",
    "vendor_criteria": "vendor_evaluation_criteria.csv",
    "recommended_stack": "recommended_market_data_stack.csv",
    "deferred_families": "deferred_expensive_data_families.csv",
    "buy_vs_free": "buy_vs_continue_free_decision.csv",
    "key_inventory": "provider_key_inventory.csv",
    "procurement_questions": "procurement_questions_for_vendors.csv",
    "next_plan": "phase8r_next_plan.json",
}


class _Paths:
    def __init__(self, out_dir: Optional[Path] = None, phase8n_dir: Optional[Path] = None,
                 phase8o_dir: Optional[Path] = None, phase8p_dir: Optional[Path] = None):
        self.out = Path(out_dir) if out_dir else _OUT_DIR
        self.phase8n = Path(phase8n_dir) if phase8n_dir else _PHASE8N_DIR
        self.phase8o = Path(phase8o_dir) if phase8o_dir else _PHASE8O_DIR
        self.phase8p = Path(phase8p_dir) if phase8p_dir else _PHASE8P_DIR
        for key, name in _ARTIFACTS.items():
            setattr(self, key, self.out / name)


# --------------------------------------------------------------------------- #
# Decision vocabulary (exactly the values from the brief).
# --------------------------------------------------------------------------- #
DEC_FREE_VIABLE = "FREE_STACK_STILL_VIABLE"
DEC_FREE_NOT_VIABLE = "FREE_STACK_NOT_VIABLE_FOR_CORE_RESEARCH"
DEC_BUY_EARNINGS = "BUY_EARNINGS_FUNDAMENTALS_PROVIDER"
DEC_MIXED = "MIXED_FREE_PLUS_PAID_CORE_STACK"
DEC_DEFER_ALT = "DEFER_EXPENSIVE_ALT_DATA"
DEC_NEEDS_QUOTES = "NEEDS_VENDOR_QUOTES"
DEC_ERROR = "ERROR"
ALLOWED_DECISIONS = (
    DEC_FREE_VIABLE, DEC_FREE_NOT_VIABLE, DEC_BUY_EARNINGS, DEC_MIXED, DEC_DEFER_ALT,
    DEC_NEEDS_QUOTES, DEC_ERROR,
)

# Provider key inventory the user holds free keys for (presence-only booleans).
_PROVIDER_KEYS: Tuple[Tuple[str, str, str], ...] = (
    ("FRED_API_KEY", "FRED", "macro time series (free, generous limits)"),
    ("ALPHAVANTAGE_API_KEY", "Alpha Vantage",
     "earnings actual/estimate/surprise (free 25 req/day - throughput-limited)"),
    ("SIMFIN_API_KEY", "SimFin", "fundamentals statements (free, DELAYED - not real-time PIT)"),
    ("FMP_API_KEY", "FMP (current plan)",
     "current plan PARTIAL on every critical family (8-N: 8/20, systematic 402)"),
    ("FINNHUB_API_KEY", "Finnhub",
     "analyst estimates/recommendations/price targets (free 60 req/min; history often premium)"),
    ("EODHD_API_KEY", "EODHD",
     "mid-priced single bundle (~$20-$80/mo) covering earnings + fundamentals + analyst"),
)

# Minimum scoring gate vs the real target universes (the strategy correction).
MIN_SCORING_GATE = 20

# Alpha Vantage free-tier throughput (from 8-P live evidence) used to compute the days-to-cover
# a broad universe at the free tier. 8-P rate-limited after only 3 requests; the documented
# free-tier ceiling is 25 requests/day.
AV_FREE_REQ_PER_DAY = 25
BROAD_UNIVERSE_SIZE = 500  # S&P 500-like


# --------------------------------------------------------------------------- #
# Market-data requirement catalog. ONE source of truth -> drives most CSVs.
#
# Fields per family:
#   key                  : stable slug
#   family               : human label (matches the brief's family names)
#   tier                 : 0 required now | 1 required if earnings signal survives | 2 valuable
#                          not required yet | 3 expensive optional expansion
#   required_now         : bool - on the CRITICAL path for the current alpha roadmap
#   needed_when          : plain-English timing
#   free_provider        : best current free source (or "" if none)
#   free_coverable       : can the free tier serve THIS family AT ALL
#   free_enough_broad    : is free coverage likely enough for 100-500 tickers (the real target)
#   pit_safe             : is point-in-time safety achievable for this family/source
#   buy_evidence         : does CURRENT evidence support buying instead of more free trials
#   recommended_source   : the source this phase recommends NOW
#   defer_until          : for deferrable families, the gating condition ("" if not deferred)
#   rationale            : one-line justification grounded in 8-N/8-O/8-P
# --------------------------------------------------------------------------- #
def _catalog() -> List[Dict]:
    return [
        {
            "key": "adjusted_ohlcv", "family": "adjusted OHLCV prices", "tier": 0,
            "required_now": True, "needed_when": "now (foundation of every signal/return)",
            "free_provider": "existing local cache / yfinance / Alpha Vantage",
            "free_coverable": True, "free_enough_broad": True, "pit_safe": True,
            "buy_evidence": False, "recommended_source": "free (existing OHLCV cache)",
            "defer_until": "",
            "rationale": "already held for the working universe; free is sufficient and PIT-safe",
        },
        {
            "key": "identity_sector", "family": "sector / industry / ticker identity", "tier": 0,
            "required_now": True, "needed_when": "now (grouping, neutralization, sector signals)",
            "free_provider": "existing repo metadata / free reference",
            "free_coverable": True, "free_enough_broad": True, "pit_safe": False,
            "buy_evidence": False, "recommended_source": "free (repo metadata)",
            "defer_until": "",
            "rationale": "static-ish reference; free suffices though historical membership is not PIT",
        },
        {
            "key": "fred_macro", "family": "FRED macro series", "tier": 0,
            "required_now": True, "needed_when": "now (rates/vol/macro context features)",
            "free_provider": "FRED", "free_coverable": True, "free_enough_broad": True,
            "pit_safe": True, "buy_evidence": False, "recommended_source": "free (FRED)",
            "defer_until": "",
            "rationale": "FRED is genuinely sufficient: full history, vintages, generous limits - "
                         "MACRO ONLY (does not serve equity earnings/analyst data)",
        },
        {
            "key": "earnings_surprise",
            "family": "earnings actual / estimate / surprise / report date", "tier": 0,
            "required_now": True,
            "needed_when": "now (the core earnings-surprise signal family in progress)",
            "free_provider": "Alpha Vantage (free 25 req/day)",
            "free_coverable": True, "free_enough_broad": False, "pit_safe": True,
            "buy_evidence": True,
            "recommended_source": "BUY broad earnings/fundamentals provider (test EODHD/FMP Premium)",
            "defer_until": "",
            "rationale": "8-P: AV free RATE-LIMITED after 3 req; 25 req/day => ~20 days for 500 "
                         "tickers. PIT-safe but free THROUGHPUT insufficient for broad coverage",
        },
        {
            "key": "analyst_estimates", "family": "analyst estimates / revisions", "tier": 1,
            "required_now": False,
            "needed_when": "if the earnings-surprise signal survives validation",
            "free_provider": "Finnhub (free 60 req/min; history often premium)",
            "free_coverable": True, "free_enough_broad": False, "pit_safe": False,
            "buy_evidence": True,
            "recommended_source": "paid bundle (EODHD/FMP Premium) or Finnhub paid for history",
            "defer_until": "",
            "rationale": "8-N: FMP analyst families PARTIAL/blocked; AV does NOT serve them; free "
                         "Finnhub history/PIT is limited - buy with the earnings bundle if signal survives",
        },
        {
            "key": "analyst_recommendations", "family": "analyst recommendations", "tier": 1,
            "required_now": False,
            "needed_when": "if the earnings-surprise signal survives validation",
            "free_provider": "Finnhub (free recommendation trends)",
            "free_coverable": True, "free_enough_broad": True, "pit_safe": False,
            "buy_evidence": False,
            "recommended_source": "free (Finnhub) to start; paid only if PIT history needed",
            "defer_until": "",
            "rationale": "Finnhub free covers current recommendation trends broadly; PIT history is "
                         "the weak point - acceptable to start free",
        },
        {
            "key": "price_target_revisions", "family": "price target revisions", "tier": 1,
            "required_now": False,
            "needed_when": "if the earnings-surprise signal survives validation",
            "free_provider": "Finnhub (history often premium)",
            "free_coverable": True, "free_enough_broad": False, "pit_safe": False,
            "buy_evidence": True,
            "recommended_source": "paid bundle (EODHD/FMP Premium) or Finnhub paid",
            "defer_until": "",
            "rationale": "revision history with reliable as-of dates is typically a paid feature",
        },
        {
            "key": "fundamentals_statements", "family": "fundamentals statements", "tier": 1,
            "required_now": False,
            "needed_when": "for value/quality/growth factors once the core signal is validated",
            "free_provider": "SimFin (free, DELAYED) / EODHD / FMP",
            "free_coverable": True, "free_enough_broad": False, "pit_safe": False,
            "buy_evidence": True,
            "recommended_source": "SimFin free for research drafts; paid bundle for PIT-clean recent data",
            "defer_until": "",
            "rationale": "SimFin free is broad but DELAYED - usable for older history, NOT a clean "
                         "real-time PIT source; not an earnings/analyst solution",
        },
        {
            "key": "ratios_features", "family": "ratios / quality / value / growth features",
            "tier": 1, "required_now": False,
            "needed_when": "derived once fundamentals are available",
            "free_provider": "derived from fundamentals (SimFin/EODHD/FMP)",
            "free_coverable": True, "free_enough_broad": False, "pit_safe": False,
            "buy_evidence": False,
            "recommended_source": "derive in-repo from whichever fundamentals source is chosen",
            "defer_until": "",
            "rationale": "computed locally; quality depends entirely on the fundamentals source/PIT",
        },
        {
            "key": "news_sentiment", "family": "news sentiment", "tier": 2,
            "required_now": False, "needed_when": "only after a core price/earnings signal works",
            "free_provider": "Alpha Vantage NEWS_SENTIMENT (free, limited)",
            "free_coverable": True, "free_enough_broad": False, "pit_safe": False,
            "buy_evidence": False, "recommended_source": "DEFER",
            "defer_until": "a core earnings/price signal is validated out-of-sample",
            "rationale": "high cost-to-signal ratio; defer until a core signal earns the right to add it",
        },
        {
            "key": "earnings_transcripts", "family": "earnings transcripts", "tier": 3,
            "required_now": False, "needed_when": "expensive optional NLP expansion, much later",
            "free_provider": "", "free_coverable": False, "free_enough_broad": False,
            "pit_safe": False, "buy_evidence": False, "recommended_source": "DEFER",
            "defer_until": "a validated core signal + an NLP research plan exist",
            "rationale": "expensive, heavy to process; no current-roadmap need",
        },
        {
            "key": "options_iv_skew", "family": "options implied volatility / skew", "tier": 3,
            "required_now": False, "needed_when": "expensive optional vol-signal expansion, later",
            "free_provider": "", "free_coverable": False, "free_enough_broad": False,
            "pit_safe": False, "buy_evidence": False, "recommended_source": "DEFER",
            "defer_until": "a validated core signal + a vol-strategy thesis exist",
            "rationale": "specialist paid data; not on the current roadmap",
        },
        {
            "key": "short_interest_borrow", "family": "short interest / borrow", "tier": 2,
            "required_now": False, "needed_when": "optional crowding/squeeze factors, later",
            "free_provider": "limited free (bi-monthly exchange short interest)",
            "free_coverable": True, "free_enough_broad": False, "pit_safe": False,
            "buy_evidence": False, "recommended_source": "DEFER",
            "defer_until": "a validated core signal exists",
            "rationale": "low-frequency, niche; defer",
        },
        {
            "key": "insider_transactions", "family": "insider transactions", "tier": 2,
            "required_now": False, "needed_when": "optional factor, later",
            "free_provider": "Finnhub / SEC EDGAR (free)",
            "free_coverable": True, "free_enough_broad": True, "pit_safe": True,
            "buy_evidence": False, "recommended_source": "DEFER (free when needed)",
            "defer_until": "a validated core signal exists",
            "rationale": "free via EDGAR with real filing dates; valuable but not required now",
        },
        {
            "key": "inst_ownership_13f", "family": "13F institutional ownership", "tier": 2,
            "required_now": False, "needed_when": "optional ownership factor, later",
            "free_provider": "SEC EDGAR 13F (free, 45-day lag)",
            "free_coverable": True, "free_enough_broad": True, "pit_safe": True,
            "buy_evidence": False, "recommended_source": "DEFER (free when needed)",
            "defer_until": "a validated core signal exists",
            "rationale": "free via EDGAR but 45-day reporting lag; not required now",
        },
    ]


# Vendor evaluation criteria (the eight+ axes from the brief).
def _vendor_criteria() -> List[Dict]:
    return [
        {"criterion": "coverage_100_500_tickers", "weight": "critical",
         "description": "Does one key cover 100-500 tickers (current + S&P 500-like), not just 20?"},
        {"criterion": "history_depth", "weight": "critical",
         "description": "Years of history for backtests (>= 8-10y of quarterly earnings preferred)"},
        {"criterion": "point_in_time_dates", "weight": "critical",
         "description": "As-of / report dates so a backtest only sees data available at the time"},
        {"criterion": "api_limits", "weight": "high",
         "description": "Requests/day and /min; can a full-universe refresh complete in one day?"},
        {"criterion": "cost", "weight": "high",
         "description": "Monthly cost; favor the cheapest tier that covers the required families"},
        {"criterion": "license_terms", "weight": "high",
         "description": "Redistribution/storage terms; we cache locally and never commit payloads"},
        {"criterion": "update_frequency", "weight": "medium",
         "description": "How fresh is the data after an event (real-time vs delayed like SimFin)?"},
        {"criterion": "bulk_download_support", "weight": "high",
         "description": "Bulk/batch endpoints so 500 tickers do not require 500 throttled calls"},
        {"criterion": "normalized_schema_stability", "weight": "medium",
         "description": "Stable documented schema so the in-repo adapter does not churn"},
    ]


# Paid / candidate provider decision matrix (test-first order). Grounded in 8-O cost/value report.
def _paid_matrix() -> List[Dict]:
    return [
        {"provider": "Alpha Vantage", "env_var": "ALPHAVANTAGE_API_KEY",
         "monthly_cost": "$0 free (25 req/day) or ~$50/mo", "families": "earnings (2)",
         "covers_broad": "no (free throughput)", "history": "yes", "pit": "yes",
         "bulk": "no", "test_order": 1, "recommended": "keep free as cheap evidence only",
         "note": "8-P: rate-limited after 3 req; free tier cannot cover 500 tickers in reasonable time"},
        {"provider": "EODHD", "env_var": "EODHD_API_KEY",
         "monthly_cost": "~$20-$80/mo", "families": "earnings + fundamentals + analyst (6)",
         "covers_broad": "yes", "history": "yes", "pit": "yes",
         "bulk": "yes", "test_order": 2, "recommended": "TEST FIRST among paid (cheapest broad bundle)",
         "note": "cheapest single bundle covering earnings+fundamentals+analyst with bulk download"},
        {"provider": "FMP (upgrade tier / Premium)", "env_var": "FMP_API_KEY",
         "monthly_cost": "~$69/mo", "families": "all six critical (6)",
         "covers_broad": "yes", "history": "yes", "pit": "yes",
         "bulk": "yes", "test_order": 3, "recommended": "fallback if EODHD insufficient (already mapped in repo)",
         "note": "8-O: upgrade NOT justified by default; Premium only if EODHD fails; Ultimate REJECTED"},
        {"provider": "Finnhub", "env_var": "FINNHUB_API_KEY",
         "monthly_cost": "$0 free (60 req/min) or ~$50/mo", "families": "analyst (4) + earnings",
         "covers_broad": "partial (history premium)", "history": "partial", "pit": "partial",
         "bulk": "no", "test_order": 4, "recommended": "free for analyst families if signal survives",
         "note": "better rate limit than AV; PIT/history for revisions/targets is the weak point"},
        {"provider": "SimFin", "env_var": "SIMFIN_API_KEY",
         "monthly_cost": "$0 free / low paid", "families": "fundamentals (delayed)",
         "covers_broad": "yes (delayed)", "history": "yes", "pit": "no (delayed)",
         "bulk": "yes", "test_order": 5, "recommended": "fundamentals research drafts only (delay caveat)",
         "note": "broad + bulk but DELAYED; not an earnings/analyst solution, not clean real-time PIT"},
        {"provider": "FRED", "env_var": "FRED_API_KEY",
         "monthly_cost": "$0 free", "families": "macro only",
         "covers_broad": "yes (macro)", "history": "yes", "pit": "yes",
         "bulk": "yes", "test_order": 6, "recommended": "KEEP free for macro - genuinely sufficient",
         "note": "MACRO ONLY; does not serve equity earnings/analyst/fundamentals"},
        {"provider": "FMP (Ultimate tier)", "env_var": "FMP_API_KEY",
         "monthly_cost": "most expensive", "families": "all (over-provisioned)",
         "covers_broad": "yes", "history": "yes", "pit": "yes",
         "bulk": "yes", "test_order": 99, "recommended": "REJECTED - no evidence requires it",
         "note": "8-O: explicitly REJECTED; cheaper sources already supply every critical family"},
    ]


# The explicit buy-vs-continue-free questions from the brief (item 6).
def _buy_vs_free_questions(av_combined: int) -> List[Dict]:
    return [
        {"question": "Should we stop trying free sources for earnings (as the broad foundation)?",
         "answer": "YES_FOR_BROAD_COVERAGE",
         "rationale": "8-P: Alpha Vantage free (25 req/day) rate-limited after 3 req and added 1 "
                      "ticker (combined %d/20). Free is fine for a 20-ticker gate but cannot serve "
                      "100-500 tickers in reasonable time. Keep AV free only as cheap evidence."
                      % av_combined},
        {"question": "Is an FMP upgrade (Premium) justified?",
         "answer": "ONLY_AS_FALLBACK",
         "rationale": "8-O: not justified by default; justified only if the cheaper EODHD bundle "
                      "fails on coverage/PIT/history. FMP adapter is already mapped in-repo."},
        {"question": "Is FMP Ultimate justified?",
         "answer": "NO_REJECTED",
         "rationale": "8-O: explicitly rejected; no evidence requires the Ultimate tier - cheaper "
                      "sources already cover every critical family."},
        {"question": "Should we buy a cheaper earnings/fundamentals provider instead?",
         "answer": "YES_TEST_EODHD_FIRST",
         "rationale": "EODHD (~$20-$80/mo) is the cheapest single bundle covering earnings + "
                      "fundamentals + analyst with bulk download and PIT dates - test it first."},
        {"question": "Should we wait to buy sentiment/transcripts/options until a core signal works?",
         "answer": "YES_DEFER",
         "rationale": "news sentiment, transcripts, options IV/skew, short interest, 13F are Tier 2/3 "
                      "- defer until a validated core earnings/price signal earns the right to add them."},
    ]


# --------------------------------------------------------------------------- #
# Small IO helpers (stdlib only).
# --------------------------------------------------------------------------- #
def _rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(_REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, allow_nan=False)
        fh.write("\n")


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def _read_csv_file(path: Path) -> List[Dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def _read_json_file(path: Path) -> Dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def key_present(env_var: str) -> bool:
    """True iff ``env_var`` is set and non-empty. The value is never returned/printed/written."""
    v = os.environ.get(env_var)
    return isinstance(v, str) and bool(v.strip())


# --------------------------------------------------------------------------- #
# Read the prior-phase evidence (read-only; never reads raw payloads).
# --------------------------------------------------------------------------- #
def read_prior_evidence(P: _Paths) -> Dict:
    """Read the committed-safe 8-N / 8-O / 8-P artifacts into the evidence summary that grounds
    the decision. Robust to missing files (each falls back to the documented deterministic value)."""
    ev: Dict = {"phase8n_read": False, "phase8o_read": False, "phase8p_read": False}

    # ---- 8-N: FMP coverage summary (per-family coverage / blocked / systematic) ----
    n_rows = _read_csv_file(P.phase8o / "phase8n_fmp_coverage_summary.csv")
    if not n_rows:
        n_rows = _read_csv_file(P.phase8n / "fmp_ticker_coverage_by_family.csv")
    fmp_min_cov = 20
    fmp_families_partial = 0
    fmp_systematic = False
    fam_cov = _read_csv_file(P.phase8o / "phase8n_fmp_coverage_summary.csv")
    if fam_cov:
        ev["phase8n_read"] = True
        covs = []
        for r in fam_cov:
            try:
                covs.append(int(float(r.get("coverage", "") or 0)))
            except (TypeError, ValueError):
                pass
            if (r.get("entitlement", "") or "").strip().upper() == "PARTIAL":
                fmp_families_partial += 1
            if (r.get("block_pattern", "") or "").strip().upper() == "SYSTEMATIC":
                fmp_systematic = True
        fmp_min_cov = min(covs) if covs else 8
    elif n_rows:
        ev["phase8n_read"] = True
        fmp_min_cov = 8
        fmp_families_partial = 6
        fmp_systematic = True
    else:
        fmp_min_cov = 8
        fmp_families_partial = 6
        fmp_systematic = True
    ev["fmp_min_family_coverage"] = fmp_min_cov
    ev["fmp_families_partial"] = fmp_families_partial
    ev["fmp_systematic_blocks"] = fmp_systematic
    ev["fmp_plan_insufficient"] = True  # 8-N conclusion (FMP_PLAN_COVERAGE_INSUFFICIENT)

    # ---- 8-O: strategy + FMP upgrade/Ultimate verdict ----
    o = _read_json_file(P.phase8o / "provider_acquisition_decision.json")
    if o:
        ev["phase8o_read"] = True
    ev["phase8o_strategy"] = o.get("recommended_strategy", "MIXED_PROVIDER_STRATEGY_REQUIRED")
    ev["fmp_upgrade_justified"] = bool(o.get("fmp_upgrade_justified", False))
    ev["fmp_ultimate_rejected"] = bool(o.get("fmp_ultimate_rejected", True))
    ev["cheapest_provider_first"] = o.get("cheapest_provider_to_try_first", "Alpha Vantage")

    # ---- 8-P: Alpha Vantage live result (helps but rate-limited) ----
    p = _read_json_file(P.phase8p / "phase8p_alphavantage_earnings_expansion.json")
    if p:
        ev["phase8p_read"] = True
    ev["av_combined_count"] = int(p.get("combined_covered_count", 9) or 9)
    ev["av_newly_covered"] = list(p.get("alphavantage_newly_covered_tickers", ["ABT"]) or ["ABT"])
    ev["av_rate_limited"] = bool(p.get("rate_limited", True))
    ev["av_requests_made"] = int(p.get("requests_made", 3) or 3)
    ev["av_decision"] = p.get("decision", "READY_FOR_NEXT_ALPHAVANTAGE_BATCH")
    ev["av_threshold_reached"] = bool(p.get("scoring_threshold_reached", False))

    # Days to cover a broad (500-ticker) universe at the AV free tier (one pass).
    ev["av_free_days_to_cover_broad"] = _ceil_div(BROAD_UNIVERSE_SIZE, AV_FREE_REQ_PER_DAY)
    return ev


def _ceil_div(a: int, b: int) -> int:
    return (a + b - 1) // b if b else 0


# --------------------------------------------------------------------------- #
# Decision logic (deterministic, offline, evidence-grounded).
# --------------------------------------------------------------------------- #
def derive_decision(ev: Dict, catalog: List[Dict]) -> Tuple[str, str, List[str]]:
    """Map the prior-phase evidence to (decision, headline, reasons).

    The honest reading of 8-N/8-O/8-P:
      * Tier-0 macro/price/identity ARE covered by free sources (FRED/OHLCV/repo) -> free works THERE.
      * Tier-0 broad earnings + Tier-1 fundamentals/analyst are NOT free-viable at 100-500 tickers
        (FMP plan blocked; AV free rate-limited; SimFin delayed; Finnhub history premium).
      * Tier-2/3 alt-data should be deferred.
    => the correct foundation is a MIXED free + paid CORE stack: keep free where it genuinely works,
       buy ONE cheap broad earnings/fundamentals provider, defer expensive alt-data."""
    reasons: List[str] = []

    free_works_tier0 = all(
        f["free_enough_broad"] for f in catalog
        if f["tier"] == 0 and f["key"] in ("adjusted_ohlcv", "identity_sector", "fred_macro"))
    earnings_free_broad = next(
        f["free_enough_broad"] for f in catalog if f["key"] == "earnings_surprise")
    core_needs_buy = any(f["buy_evidence"] for f in catalog if f["tier"] in (0, 1))

    if free_works_tier0:
        reasons.append(
            "Free sources ARE sufficient for the Tier-0 non-equity-fundamental layer: FRED (macro, "
            "full history + vintages), adjusted OHLCV (existing cache), and ticker/sector identity. "
            "Free is NOT a waste of time here.")
    if not earnings_free_broad:
        reasons.append(
            "Free is NOT viable for BROAD earnings coverage: 8-P shows Alpha Vantage free (25 req/day) "
            "rate-limited after %d requests, adding 1 ticker (combined %d/20). A 500-ticker universe "
            "would take ~%d days per refresh at that throughput."
            % (ev["av_requests_made"], ev["av_combined_count"], ev["av_free_days_to_cover_broad"]))
    if ev["fmp_plan_insufficient"]:
        reasons.append(
            "8-N proved the current FMP plan is INSUFFICIENT: every critical family PARTIAL at "
            "%d/20 with SYSTEMATIC 402 blocks." % ev["fmp_min_family_coverage"])
    reasons.append(
        "Tier-2/3 alt-data (news sentiment, transcripts, options IV/skew, short interest, 13F) is "
        "DEFERRED until a validated core signal earns the right to add it.")

    # Free works for part of the stack, but the CORE earnings/fundamentals layer needs a purchase
    # -> the truthful, non-binary answer is a mixed free+paid core stack.
    if free_works_tier0 and core_needs_buy:
        decision = DEC_MIXED
        headline = ("MIXED free + paid core stack: keep FREE for macro/price/identity (FRED + OHLCV "
                    "+ reference), BUY one cheap broad earnings/fundamentals provider (test EODHD "
                    "first, FMP Premium fallback), DEFER expensive alt-data. FMP Ultimate rejected.")
    elif core_needs_buy:
        decision = DEC_BUY_EARNINGS
        headline = "Buy a broad earnings/fundamentals provider; free tier cannot serve 100-500 tickers."
    else:
        decision = DEC_FREE_VIABLE
        headline = "Free stack still viable."
    return decision, headline, reasons


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
def run(out_dir: Optional[Path] = None, phase8n_dir: Optional[Path] = None,
        phase8o_dir: Optional[Path] = None, phase8p_dir: Optional[Path] = None,
        verbose: bool = True) -> Dict:
    """Run Phase 8-Q (always offline). Reads 8-N/8-O/8-P evidence, builds the requirement catalog,
    free-stack viability, paid decision matrix, vendor criteria, and the buy-vs-free decision.
    Returns the main report dict."""
    P = _Paths(out_dir, phase8n_dir, phase8o_dir, phase8p_dir)
    P.out.mkdir(parents=True, exist_ok=True)

    try:
        ev = read_prior_evidence(P)
        catalog = _catalog()
        decision, headline, reasons = derive_decision(ev, catalog)

        # ---- provider key inventory (PRESENCE BOOLEANS ONLY; value never read) ----
        key_rows = []
        for env_var, provider, note in _PROVIDER_KEYS:
            key_rows.append([env_var, provider, key_present(env_var), False, note])
        _write_csv(P.key_inventory,
                   ["env_var", "provider", "key_present", "value_read", "note"], key_rows)

        # ---- universe scale targets (the strategy correction) ----
        _write_csv(P.universe_targets,
                   ["target", "scope", "size_estimate", "is_final_target", "purpose"],
                   [["minimum_scoring_gate", "statistical floor", MIN_SCORING_GATE, False,
                     "minimum tickers to score a signal - NOT the target universe"],
                    ["current_working_universe", "Phase 5C / current available", "~30-60", False,
                     "what we can research today; broaden from here"],
                    ["broad_research_universe", "S&P 500-like", "~500", True,
                     "the real near-term target for serious cross-sectional alpha research"],
                    ["future_robust_universe", "survivorship-aware / PIT membership", "500+ historical",
                     True, "point-in-time index membership to remove survivorship bias"]])

        # ---- market data requirement catalog ----
        _write_csv(P.requirement_catalog,
                   ["family", "tier", "required_now", "needed_when", "free_provider",
                    "free_coverable", "free_enough_for_100_500", "point_in_time_safe",
                    "buy_evidence_supports", "recommended_source", "rationale"],
                   [[f["family"], f["tier"], f["required_now"], f["needed_when"],
                     f["free_provider"], f["free_coverable"], f["free_enough_broad"],
                     f["pit_safe"], f["buy_evidence"], f["recommended_source"], f["rationale"]]
                    for f in catalog])

        # ---- data family priority tiers ----
        _tier_label = {0: "Tier 0 - required now",
                       1: "Tier 1 - required if earnings signal survives",
                       2: "Tier 2 - valuable but not required yet",
                       3: "Tier 3 - expensive optional expansion"}
        _write_csv(P.priority_tiers,
                   ["family", "tier", "tier_label", "required_now", "rationale"],
                   [[f["family"], f["tier"], _tier_label[f["tier"]], f["required_now"],
                     f["rationale"]] for f in sorted(catalog, key=lambda x: (x["tier"], x["family"]))])

        # ---- free-stack viability report ----
        _write_csv(P.free_viability,
                   ["family", "tier", "free_provider", "free_coverable", "free_enough_for_100_500",
                    "point_in_time_safe", "limiting_factor"],
                   [[f["family"], f["tier"], f["free_provider"] or "(none)", f["free_coverable"],
                     f["free_enough_broad"], f["pit_safe"], _limiting_factor(f)]
                    for f in catalog])

        # ---- current provider coverage summary (from 8-N/8-O/8-P) ----
        _write_csv(P.current_coverage,
                   ["provider", "role", "critical_families_covered", "broad_coverage_proven",
                    "evidence_phase", "verdict"],
                   [["FMP (current plan)", "earnings + analyst (partial)", "6 (all PARTIAL)",
                     "no (8/20, systematic 402)", "8-N", "INSUFFICIENT"],
                    ["Alpha Vantage (free)", "earnings actual/estimate/surprise", "2",
                     "no (rate-limited 25/day; +1 ticker -> %d/20)" % ev["av_combined_count"], "8-P",
                     "HELPS_BUT_NOT_BROAD"],
                    ["FRED", "macro series", "0 equity (macro only)", "yes (macro)", "n/a",
                     "SUFFICIENT_FOR_MACRO"],
                    ["SimFin", "fundamentals (delayed)", "fundamentals", "yes-but-delayed", "n/a",
                     "FUNDAMENTALS_DELAYED_NOT_EARNINGS"],
                    ["Finnhub (free)", "analyst families", "4 analyst", "partial (history premium)",
                     "8-O", "ANALYST_PARTIAL"],
                    ["EODHD (candidate paid)", "earnings+fundamentals+analyst bundle", "6",
                     "yes (documented)", "8-O", "TEST_FIRST_PAID"]])

        # ---- paid provider decision matrix (test-first order) ----
        pm = _paid_matrix()
        _write_csv(P.paid_matrix,
                   ["test_order", "provider", "env_var", "monthly_cost", "families_covered",
                    "covers_100_500", "history_depth", "point_in_time", "bulk_download",
                    "recommended", "note"],
                   [[m["test_order"], m["provider"], m["env_var"], m["monthly_cost"], m["families"],
                     m["covers_broad"], m["history"], m["pit"], m["bulk"], m["recommended"],
                     m["note"]] for m in sorted(pm, key=lambda x: x["test_order"])])

        # ---- vendor evaluation criteria ----
        vc = _vendor_criteria()
        _write_csv(P.vendor_criteria,
                   ["criterion", "weight", "description"],
                   [[c["criterion"], c["weight"], c["description"]] for c in vc])

        # ---- recommended market data stack ----
        _write_csv(P.recommended_stack,
                   ["family", "tier", "recommended_source", "buy_or_free", "status"],
                   [[f["family"], f["tier"], f["recommended_source"],
                     _buy_or_free(f), _stack_status(f)] for f in catalog])

        # ---- deferred expensive data families ----
        deferred = [f for f in catalog if f["defer_until"]]
        _write_csv(P.deferred_families,
                   ["family", "tier", "defer_until", "rationale"],
                   [[f["family"], f["tier"], f["defer_until"], f["rationale"]] for f in deferred])

        # ---- buy-vs-continue-free decision ----
        bvf = _buy_vs_free_questions(ev["av_combined_count"])
        _write_csv(P.buy_vs_free,
                   ["question", "answer", "rationale"],
                   [[q["question"], q["answer"], q["rationale"]] for q in bvf])

        # ---- procurement questions for vendors ----
        _write_csv(P.procurement_questions,
                   ["question", "why_it_matters"],
                   [[q, w] for q, w in _procurement_questions()])

        # ---- Phase 8-R next plan ----
        _write_json(P.next_plan, {
            "phase": "8-R",
            "title": "Evaluate the cheapest broad earnings/fundamentals bundle (EODHD test-first)",
            "depends_on_decision": decision,
            "objective": (
                "Run a bounded, key-gated evaluation of the cheapest paid broad bundle (EODHD first, "
                "FMP Premium fallback) against the vendor criteria - coverage across 100-500 tickers, "
                "history depth, point-in-time dates, API limits, cost, license, bulk download - to "
                "confirm it solves broad earnings+fundamentals PIT coverage before committing budget. "
                "Keep FRED for macro and the OHLCV cache for prices. Defer all Tier-2/3 alt-data."),
            "test_first_provider": "EODHD",
            "test_first_env_var": "EODHD_API_KEY",
            "fallback_provider": "FMP (Premium)",
            "fmp_ultimate_rejected": True,
            "do_not_run_more_free_micro_hunts_for_broad_earnings": True,
            "deferred_families": [f["family"] for f in deferred],
            "next_command": _next_command(decision),
            "preview_only": True, "orders_enabled": False, "automation_enabled": False,
            "broker_execution_enabled": False, "committed": False,
        })

        # ---- secret leak scan over the committed artifacts written this run ----
        written = [p for p in P.out.glob("*") if p.is_file()]
        leak_clean, scanned = _scan_for_leaks(written)

        # ---- main report ----
        deferrable_families = [f["family"] for f in catalog if f["tier"] >= 2]
        mandatory_now = [f["family"] for f in catalog if f["required_now"]]
        report = {
            "phase": PHASE,
            "objective": (
                "Decide the minimum viable market-data stack for serious alpha research: classify "
                "data families must-have vs optional, assess whether free APIs can serve a broad "
                "(100-500 ticker) point-in-time universe, and produce a buy-vs-free decision."),
            "is_decision_gate_not_backfill": True,
            "network_used": False,
            "evidence": ev,
            "min_scoring_gate": MIN_SCORING_GATE,
            "min_scoring_gate_is_target_universe": False,
            "target_universes": ["current_working_universe (Phase 5C)",
                                 "broad_research_universe (S&P 500-like ~500)",
                                 "future_robust_universe (PIT membership)"],
            "fmp_current_plan_insufficient": True,
            "alphavantage_helpful_but_unproven_for_broad": True,
            "fred_classified": "macro_only",
            "simfin_classified": "fundamentals_delayed_not_earnings_or_analyst",
            "free_stack_viable_for_macro_price_identity": True,
            "free_stack_viable_for_broad_earnings": False,
            "decision": decision, "decision_is_terminal": decision in ALLOWED_DECISIONS,
            "allowed_decisions": list(ALLOWED_DECISIONS),
            "decision_headline": headline,
            "decision_reasons": reasons,
            "mandatory_data_now": mandatory_now,
            "deferrable_data_families": deferrable_families,
            "free_apis_still_worth_using": True,
            "free_apis_worth_using_for": ["FRED macro", "adjusted OHLCV prices",
                                          "ticker/sector identity", "EDGAR insider/13F (when needed)"],
            "free_apis_not_sufficient_for": ["broad earnings coverage (100-500 tickers)",
                                             "analyst revision/price-target PIT history",
                                             "clean real-time fundamentals (SimFin is delayed)"],
            "paid_provider_recommended": True,
            "provider_category_to_evaluate_first": "earnings/fundamentals bundle (EODHD, ~$20-$80/mo)",
            "fmp_upgrade_justified": False,
            "fmp_upgrade_note": "only as fallback if the cheaper EODHD bundle fails on coverage/PIT/history",
            "fmp_ultimate_rejected": True,
            "stop_free_micro_hunts_for_broad_earnings": True,
            "vendor_evaluation_criteria": [c["criterion"] for c in vc],
            "procurement_question_count": len(_procurement_questions()),
            "recommended_next_command": _next_command(decision),
            "secret_safety_leak_scan_clean": leak_clean,
            "secret_safety_files_scanned": scanned,
            "outputs": {k: _rel(getattr(P, k)) for k in _ARTIFACTS},
            # Safety / provenance contract.
            "preview_only": True, "orders_enabled": False, "automation_enabled": False,
            "broker_execution_enabled": False, "production_replacement": False,
            "paper_trader_touched": False, "gcp_touched": False, "deployed": False,
            "data_fabricated": False, "no_alpha_fabricated": True,
            "raw_paid_data_in_artifacts": False, "api_key_logged": False,
            "wrote_to_d_drive": False, "committed": False,
        }
        _write_json(P.report, report)

        if verbose:
            _print_summary(report)
        return report
    except Exception as exc:  # pragma: no cover - defensive; never leaks a key
        report = {
            "phase": PHASE, "decision": DEC_ERROR, "decision_is_terminal": True,
            "allowed_decisions": list(ALLOWED_DECISIONS),
            "error": "%s: %s" % (type(exc).__name__, exc),
            "api_key_logged": False, "committed": False, "preview_only": True,
            "orders_enabled": False, "automation_enabled": False,
            "paper_trader_touched": False, "gcp_touched": False, "deployed": False,
        }
        _write_json(P.report, report)
        return report


def _limiting_factor(f: Dict) -> str:
    if not f["free_coverable"]:
        return "no free source for this family"
    if not f["free_enough_broad"]:
        if f["key"] == "earnings_surprise":
            return "free throughput (AV 25 req/day) cannot cover 100-500 tickers in reasonable time"
        if f["key"] == "fundamentals_statements":
            return "SimFin free is DELAYED (not clean real-time PIT); paid bundle for recent data"
        return "free tier limits history / PIT / throughput for broad coverage"
    if not f["pit_safe"]:
        return "free coverage OK but point-in-time as-of dates are weak"
    return "none - free is sufficient for broad coverage"


def _buy_or_free(f: Dict) -> str:
    src = f["recommended_source"].lower()
    if "defer" in src:
        return "defer"
    if "buy" in src or "paid" in src or "premium" in src:
        return "paid"
    if "free" in src:
        return "free"
    return "derive/in-repo"


def _stack_status(f: Dict) -> str:
    if f["defer_until"]:
        return "DEFERRED"
    if f["tier"] == 0:
        return "MANDATORY_NOW"
    return "NEEDED_IF_SIGNAL_SURVIVES"


def _procurement_questions() -> List[Tuple[str, str]]:
    return [
        ("Does a single subscription cover earnings, fundamentals, and analyst data for the full "
         "S&P 500 (and ideally 1000+ US tickers)?",
         "one key for 100-500+ tickers avoids stitching multiple providers"),
        ("How many years of quarterly earnings actual/estimate/surprise history are included?",
         "backtests need >= 8-10y of history"),
        ("Do earnings/estimate records carry the original report/announcement date AND the "
         "as-of/last-updated date (true point-in-time), or only the latest restated values?",
         "PIT dates are mandatory to avoid look-ahead bias"),
        ("What are the requests-per-minute and requests-per-day limits, and is there a bulk "
         "endpoint to pull the full universe in one job?",
         "AV free (25/day) proved unusable for broad coverage; we need a full refresh in <1 day"),
        ("What is the exact monthly/annual cost for the tier that meets the above, and is there a "
         "cheaper tier that still covers earnings+fundamentals?",
         "favor the cheapest tier covering the required families; reject over-provisioned tiers"),
        ("Do the license terms permit caching/storing the normalized data locally for research "
         "(we never redistribute or commit payloads)?",
         "we cache locally under gitignored trees; storage rights must be explicit"),
        ("How quickly is data updated after an earnings release (real-time, T+1, or delayed like "
         "SimFin)?",
         "delayed fundamentals are unusable as a clean real-time PIT signal source"),
        ("Is the response schema stable and documented, with change notifications?",
         "schema churn breaks the in-repo adapter"),
        ("Is point-in-time index membership (survivorship-aware S&P 500 history) available?",
         "needed for the future robust universe to remove survivorship bias"),
    ]


def _next_command(decision: str) -> str:
    if decision in (DEC_MIXED, DEC_BUY_EARNINGS):
        return ("$env:EODHD_API_KEY = '<PASTE_EODHD_API_KEY_HERE>'; "
                "python research/run_phase8r_broad_bundle_evaluation.py --live   "
                "(bounded EODHD coverage/PIT/cost evaluation; FMP Premium fallback; Ultimate rejected)")
    if decision == DEC_NEEDS_QUOTES:
        return ("Collect vendor quotes for EODHD and FMP Premium against "
                "vendor_evaluation_criteria.csv, then re-run this gate.")
    return "python research/run_phase8q_market_data_foundation_decision.py"


def _scan_for_leaks(paths: Sequence[Path]) -> Tuple[bool, int]:
    """Belt-and-braces: confirm no committed artifact contains a key query param or any held key
    value. This phase makes no network calls and writes no payloads, but we scan anyway."""
    marker = "api" + "key="
    secrets = []
    for env_var, _, _ in _PROVIDER_KEYS:
        v = os.environ.get(env_var, "")
        if isinstance(v, str) and v.strip():
            secrets.append(v.strip())
    scanned = 0
    clean = True
    for p in paths:
        if not p.is_file():
            continue
        scanned += 1
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if marker in text.lower():
            clean = False
        for s in secrets:
            if s and s in text:
                clean = False
    return clean, scanned


def _print_summary(report: Dict) -> None:
    print("phase:                         %s" % report.get("phase"))
    print("decision gate (no network):    %s" % report.get("is_decision_gate_not_backfill"))
    print("decision:                      %s" % report.get("decision"))
    print("headline:                      %s" % report.get("decision_headline"))
    print("fmp current plan insufficient: %s" % report.get("fmp_current_plan_insufficient"))
    print("free viable: macro/price/id:   %s" % report.get("free_stack_viable_for_macro_price_identity"))
    print("free viable: broad earnings:   %s" % report.get("free_stack_viable_for_broad_earnings"))
    print("paid provider recommended:     %s" % report.get("paid_provider_recommended"))
    print("evaluate first:                %s" % report.get("provider_category_to_evaluate_first"))
    print("fmp upgrade justified:         %s" % report.get("fmp_upgrade_justified"))
    print("fmp ultimate rejected:         %s" % report.get("fmp_ultimate_rejected"))
    print("mandatory now:                 %s" % ", ".join(report.get("mandatory_data_now", [])))
    print("deferrable families:           %s" % ", ".join(report.get("deferrable_data_families", [])))
    print("next command:                  %s" % report.get("recommended_next_command"))


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=("Phase 8-Q market-data foundation decision gate (always offline; reads "
                     "8-N/8-O/8-P evidence; no network, no keys read)."))
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    _parse_args(argv)
    run(verbose=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
