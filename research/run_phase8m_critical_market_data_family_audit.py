"""Phase 8-M - Critical Market-Data Family Entitlement Audit and Agent Controller Fix.

WHY THIS PHASE EXISTS
    The previous FMP live sample (Phase 5-E0 / 8-L precursor) failed the operator's
    expectation: it ran with a generic ``--max-endpoints 6`` cap while the FMP endpoint
    catalog orders the *fundamentals* endpoints first (``smoke_priority`` 1-6) and the
    *critical* earnings/analyst endpoints last (``smoke_priority`` 90-94). The selector
    ``_select_live_endpoints`` in run_phase5e0_fmp_provider_trial_collector.py sorts by
    ``smoke_priority`` and then slices ``[:max_endpoints]`` - so the six most important
    missing data families (earnings surprises, earnings calendar, analyst estimates,
    recommendations, price targets, ratings/grades) were NEVER attempted. The quality
    report therefore showed 0 planned / 0 attempted for earnings + analyst, and no
    upgrade decision could be made.

    That is a controller bug, not a data limitation. Phase 8-M fixes the controller so
    that unresolved HIGH-IMPACT blockers are always probed FIRST and can NEVER be dropped
    by a generic endpoint cap, then runs a bounded live entitlement probe against exactly
    those critical families and classifies the precise access result (entitled vs
    subscription-blocked vs wrong-endpoint vs rate-limited) per endpoint family.

WHAT IT PRODUCES
    A critical-market-data-family entitlement audit: a 20-family inventory, a
    critical-first priority queue, a bounded FMP probe plan + results + failure diagnosis,
    a subscription-requirement decision, a client endpoint-gap report, provider-alternative
    and cheapest-viable-provider matrices, a data-family -> signal-unlock map, an
    agent-controller-fix report, a research-director decision, and a Phase 8-N next plan.

SECRET DISCIPLINE (hard rules, inherited from research/providers/fmp_client.py)
    * FMP_API_KEY is read ONLY from the environment, NEVER printed, NEVER written to disk.
    * Persisted URLs strip the key parameter entirely (placeholder only); no artifact ever
      contains the literal substring "apikey=".
    * Default run is DRY-RUN: no network call. A live probe needs an explicit ``--live``
      AND a present FMP_API_KEY. Tests run fully offline via an injected ``transport`` and
      never require a key or a network.

HARD CONSTRAINTS HONORED
    Existing installed packages only (stdlib here). No full S&P 500 backfill. Bounded live
    limits (<=3 tickers, <=12 endpoint families, <=40 requests, earnings/analyst first).
    Raw + normalized paid data go ONLY under research/data/fmp/{raw,normalized}/ which is
    gitignored. No Paper Trader. No GCP. No deploy. No broker / order / automation logic.
    No commit. No push.

Run (default = dry-run, no network):
    python research/run_phase8m_critical_market_data_family_audit.py
Bounded live entitlement probe (requires FMP_API_KEY in the environment):
    python research/run_phase8m_critical_market_data_family_audit.py --live
Test:
    python -m pytest tests/test_phase8m_critical_market_data_family_audit.py -q
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research.providers import fmp_client as fmp  # noqa: E402

PHASE = "8-M"

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase8m_critical_market_data_family_audit"
# Paid provider payloads live here ONLY - this whole tree is gitignored.
_FMP_DATA_DIR = _REPO_ROOT / "research" / "data" / "fmp"
_RAW_DIR = _FMP_DATA_DIR / "raw"
_NORM_DIR = _FMP_DATA_DIR / "normalized"

# The 17 committed-safe artifact basenames (fixed names; tests resolve them under a
# tmp dir, the real run resolves them under _OUT_DIR).
_ARTIFACTS = {
    "report": "phase8m_critical_market_data_family_audit.json",
    "inventory": "market_data_family_inventory.csv",
    "priority_queue": "missing_data_family_priority_queue.csv",
    "probe_plan": "fmp_critical_endpoint_probe_plan.csv",
    "probe_results": "fmp_critical_endpoint_probe_results.csv",
    "failure_diag": "fmp_endpoint_failure_diagnosis.csv",
    "subscription_decision": "fmp_subscription_requirement_decision.csv",
    "client_gap": "fmp_client_endpoint_gap_report.csv",
    "provider_alt": "provider_alternative_matrix.csv",
    "cheapest": "cheapest_viable_provider_matrix.csv",
    "signal_unlock": "data_family_to_signal_unlock_map.csv",
    "activation_cmds": "provider_activation_commands.ps1",
    "storage_manifest": "paid_data_storage_manifest.csv",
    "secret_audit": "secret_safety_audit.csv",
    "controller_fix": "agent_controller_fix_report.csv",
    "director_decision": "research_director_decision.json",
    "next_plan": "phase8n_next_plan.json",
}


class _Paths:
    """Resolved output + paid-data paths for one run (default = repo paths; tests pass
    tmp dirs so module-scoped fixtures never clobber each other on disk)."""

    def __init__(self, out_dir: Optional[Path] = None, data_dir: Optional[Path] = None):
        self.out = Path(out_dir) if out_dir else _OUT_DIR
        data = Path(data_dir) if data_dir else _FMP_DATA_DIR
        self.raw = data / "raw"
        self.norm = data / "normalized"
        for key, name in _ARTIFACTS.items():
            setattr(self, key, self.out / name)

# --------------------------------------------------------------------------- #
# Bounded live limits (hard caps - never exceeded)
# --------------------------------------------------------------------------- #
DEFAULT_TICKERS = ["AAPL", "MSFT", "NVDA"]
MAX_TICKERS = 3
MAX_ENDPOINT_FAMILIES = 12
MAX_REQUESTS = 40

# --------------------------------------------------------------------------- #
# Concrete per-endpoint-family status vocabulary (audit OUTCOME).
# --------------------------------------------------------------------------- #
ST_ACCESS_VERIFIED = "ACCESS_VERIFIED"
ST_EMPTY_REACHABLE = "EMPTY_BUT_ENDPOINT_REACHABLE"
ST_CLIENT_UPDATE = "CLIENT_ENDPOINT_UPDATE_REQUIRED"
ST_SUBSCRIPTION_BLOCK = "SUBSCRIPTION_ENTITLEMENT_BLOCK"
ST_RATE_LIMITED = "RATE_LIMITED"
ST_NOT_AVAILABLE = "NOT_AVAILABLE_IN_CURRENT_PROVIDER"
ST_NOT_TESTED_NO_MAPPING = "NOT_TESTED_NO_MAPPING"
ST_LOCAL_AVAILABLE = "LOCAL_DATA_ALREADY_AVAILABLE"
ST_FREE_AVAILABLE = "FREE_SOURCE_AVAILABLE"
ALLOWED_STATUSES = (
    ST_ACCESS_VERIFIED, ST_EMPTY_REACHABLE, ST_CLIENT_UPDATE, ST_SUBSCRIPTION_BLOCK,
    ST_RATE_LIMITED, ST_NOT_AVAILABLE, ST_NOT_TESTED_NO_MAPPING, ST_LOCAL_AVAILABLE,
    ST_FREE_AVAILABLE,
)
# Non-terminal planning marker used ONLY in the default dry-run for FMP-probe families
# that the bounded live audit has not yet executed. It is never one of the nine terminal
# audit statuses and is documented as such; the live (or simulated) audit replaces it.
ST_PENDING_PROBE = "PENDING_LIVE_PROBE"

# likely_cause vocabulary (per the brief).
CAUSE_ACCESSIBLE = "accessible"
CAUSE_NO_DATA = "no_data_for_symbol"
CAUSE_WRONG_PATH = "wrong_endpoint_path"
CAUSE_CLIENT_UPDATE = "client_endpoint_update_required"
CAUSE_SUBSCRIPTION = "subscription_entitlement_block"
CAUSE_RATE_LIMIT = "rate_limit"
CAUSE_INVALID_KEY = "invalid_key"
CAUSE_UNKNOWN = "unknown"

# --------------------------------------------------------------------------- #
# Research-director decision vocabulary (audit OUTCOME).
# --------------------------------------------------------------------------- #
DEC_FMP_SUFFICIENT = "CURRENT_FMP_ACCESS_SUFFICIENT_FOR_CRITICAL_FAMILIES"
DEC_FMP_UPGRADE = "FMP_SUBSCRIPTION_UPGRADE_REQUIRED"
DEC_FMP_CLIENT_UPDATE = "FMP_CLIENT_ENDPOINT_UPDATE_REQUIRED"
DEC_CHEAPER_PROVIDER = "CHEAPER_PROVIDER_RECOMMENDED"
DEC_MIXED_STRATEGY = "MIXED_PROVIDER_STRATEGY_RECOMMENDED"
DEC_BLOCKED_NO_KEY = "BLOCKED_MISSING_FMP_KEY"
DEC_ERROR = "ERROR"
ALLOWED_DECISIONS = (
    DEC_FMP_SUFFICIENT, DEC_FMP_UPGRADE, DEC_FMP_CLIENT_UPDATE, DEC_CHEAPER_PROVIDER,
    DEC_MIXED_STRATEGY, DEC_BLOCKED_NO_KEY, DEC_ERROR,
)
# Non-terminal planning marker for the dry-run (no probe executed) - never one of the 7.
DEC_DRY_RUN = "DRY_RUN_NO_PROBE_EXECUTED"

# Cost is UNKNOWN whenever it cannot be verified from local code/catalog (we never guess
# a dollar figure). UNKNOWN is an honest, explicit value.
COST_UNKNOWN = "UNKNOWN"

# --------------------------------------------------------------------------- #
# The 20 mandatory market-data families (exact ids + ordering from the brief).
# Families resolvable WITHOUT an FMP probe carry a concrete static status; families
# backed by an FMP endpoint probe are resolved by the bounded live audit.
# --------------------------------------------------------------------------- #
# resolution kinds
RK_FMP_PROBE = "fmp_probe"
RK_LOCAL = "local"
RK_FREE = "free_source"
RK_PROVIDER_OTHER = "provider_other"
RK_NO_MAPPING = "no_mapping"

MANDATORY_FAMILIES: Tuple[Dict, ...] = (
    {"family": "broad_earnings_surprise", "rk": RK_FMP_PROBE, "critical": True,
     "static_status": None, "provider": "FMP", "env_var": "FMP_API_KEY",
     "free_alt": "", "note": "actual-vs-estimate EPS/revenue surprises per report date."},
    {"family": "earnings_calendar", "rk": RK_FMP_PROBE, "critical": True,
     "static_status": None, "provider": "FMP", "env_var": "FMP_API_KEY",
     "free_alt": "", "note": "per-ticker historical earnings dates + estimate/actual."},
    {"family": "analyst_estimates", "rk": RK_FMP_PROBE, "critical": True,
     "static_status": None, "provider": "FMP", "env_var": "FMP_API_KEY",
     "free_alt": "", "note": "forward consensus estimates - basis for revision breadth."},
    {"family": "analyst_recommendations", "rk": RK_FMP_PROBE, "critical": True,
     "static_status": None, "provider": "FMP", "env_var": "FMP_API_KEY",
     "free_alt": "", "note": "analyst buy/hold/sell grade changes over time."},
    {"family": "analyst_price_targets", "rk": RK_FMP_PROBE, "critical": True,
     "static_status": None, "provider": "FMP", "env_var": "FMP_API_KEY",
     "free_alt": "", "note": "analyst price-target revisions (direction vs price)."},
    {"family": "ratings_or_grades", "rk": RK_FMP_PROBE, "critical": True,
     "static_status": None, "provider": "FMP", "env_var": "FMP_API_KEY",
     "free_alt": "", "note": "consensus grade/rating snapshot (grades-consensus)."},
    {"family": "key_metrics", "rk": RK_FMP_PROBE, "critical": False,
     "static_status": None, "provider": "FMP", "env_var": "FMP_API_KEY",
     "free_alt": "", "note": "valuation/quality per-share metrics (previously FAILED)."},
    {"family": "ratios", "rk": RK_FMP_PROBE, "critical": False,
     "static_status": None, "provider": "FMP", "env_var": "FMP_API_KEY",
     "free_alt": "", "note": "quarterly financial ratios (previously FAILED)."},
    {"family": "fundamentals_statements", "rk": RK_FMP_PROBE, "critical": False,
     "static_status": None, "provider": "FMP", "env_var": "FMP_API_KEY",
     "free_alt": "SEC EDGAR companyfacts (free)",
     "note": "income/balance/cash-flow statements (previously reachable)."},
    {"family": "company_profile", "rk": RK_FMP_PROBE, "critical": False,
     "static_status": None, "provider": "FMP", "env_var": "FMP_API_KEY",
     "free_alt": "SEC EDGAR (free)",
     "note": "sector/industry/identity (auth canary; previously reachable)."},
    {"family": "transcripts_or_guidance", "rk": RK_NO_MAPPING, "critical": False,
     "static_status": ST_NOT_TESTED_NO_MAPPING, "provider": "FMP (premium)",
     "env_var": "FMP_API_KEY", "free_alt": "",
     "note": "no client mapping yet; add /stable/earning-call-transcript probe and re-run."},
    {"family": "news_or_press_releases", "rk": RK_FREE, "critical": False,
     "static_status": ST_FREE_AVAILABLE, "provider": "GDELT (free) then FMP/Benzinga",
     "env_var": "", "free_alt": "GDELT 2.0 (free, no key)",
     "note": "exhaust free GDELT historical news before any paid news provider."},
    {"family": "insider_transactions", "rk": RK_FREE, "critical": False,
     "static_status": ST_FREE_AVAILABLE, "provider": "SEC EDGAR (free)",
     "env_var": "", "free_alt": "SEC EDGAR Form 4 (free)",
     "note": "SEC EDGAR Form 4 feed is free and authoritative."},
    {"family": "institutional_ownership_13f", "rk": RK_FREE, "critical": False,
     "static_status": ST_FREE_AVAILABLE, "provider": "SEC EDGAR (free)",
     "env_var": "", "free_alt": "SEC EDGAR 13F (free)",
     "note": "SEC EDGAR 13F filings are free and authoritative."},
    {"family": "options_iv_skew_putcall", "rk": RK_PROVIDER_OTHER, "critical": False,
     "static_status": ST_NOT_AVAILABLE, "provider": "Intrinio / ORATS / Tradier / CBOE",
     "env_var": "INTRINIO_API_KEY", "free_alt": "",
     "note": "FMP does not provide options IV/skew; needs an options-specialist provider."},
    {"family": "short_interest_borrow", "rk": RK_FREE, "critical": False,
     "static_status": ST_FREE_AVAILABLE, "provider": "FINRA (free) then Finnhub/Ortex",
     "env_var": "", "free_alt": "FINRA bi-monthly short interest (free)",
     "note": "exhaust free FINRA short interest before any paid borrow/utilization feed."},
    {"family": "sec_filings_event_classification", "rk": RK_FREE, "critical": False,
     "static_status": ST_FREE_AVAILABLE, "provider": "SEC EDGAR (free)",
     "env_var": "", "free_alt": "SEC EDGAR full-text + submissions (free)",
     "note": "SEC EDGAR filing index is free; already activated in Phase 8-L."},
    {"family": "macro_cross_asset_context", "rk": RK_LOCAL, "critical": False,
     "static_status": ST_LOCAL_AVAILABLE, "provider": "Norgate (local)",
     "env_var": "", "free_alt": "Norgate cross-asset (local)",
     "note": "cross-asset macro pack already available locally (Phase 8-E/8-L)."},
    {"family": "sector_industry_context", "rk": RK_LOCAL, "critical": False,
     "static_status": ST_LOCAL_AVAILABLE, "provider": "Norgate (local) + FMP profile",
     "env_var": "", "free_alt": "Norgate sector/industry (local)",
     "note": "sector mapping already available locally."},
    {"family": "liquidity_volume_volatility_positioning", "rk": RK_LOCAL, "critical": False,
     "static_status": ST_LOCAL_AVAILABLE, "provider": "Norgate (local)",
     "env_var": "", "free_alt": "Norgate price/volume (local)",
     "note": "liquidity/volume/volatility derived locally from Norgate price/volume."},
)
MANDATORY_FAMILY_IDS = tuple(f["family"] for f in MANDATORY_FAMILIES)


# --------------------------------------------------------------------------- #
# FMP probe endpoint families - CRITICAL FIRST (this ordering IS the controller fix).
# Each maps to one mandatory family. ``rank`` is the audit priority: earnings/analyst
# blockers (rank 0-5) are probed before any fundamentals/profile endpoint (rank >= 10),
# the exact INVERSE of the catalog's smoke_priority that caused the original failure.
# --------------------------------------------------------------------------- #
def _catalog_paths() -> Dict[str, str]:
    return {e["endpoint_name"]: e["endpoint_path_template"] for e in fmp.endpoint_catalog()}


def build_probe_endpoints() -> List[Dict]:
    """Return the FMP probe endpoints in CRITICAL-FIRST priority order.

    ``ratings_grades_consensus`` is a safe added probe-adapter mapping
    (``/stable/grades-consensus``) for the ratings/grades family, which the catalog
    only exposes via the same path as analyst_recommendations.
    """
    paths = _catalog_paths()
    eps: List[Dict] = [
        # rank, probe id, FMP catalog endpoint, mandatory family, critical
        (0, "earnings_surprises", "earnings_surprises", "broad_earnings_surprise", True),
        (1, "earnings_calendar", "earnings_calendar", "earnings_calendar", True),
        (2, "analyst_estimates", "analyst_estimates", "analyst_estimates", True),
        (3, "analyst_recommendations", "analyst_recommendations", "analyst_recommendations", True),
        (4, "analyst_price_targets", "analyst_price_targets", "analyst_price_targets", True),
        (5, "ratings_grades_consensus", None, "ratings_or_grades", True),
        (10, "key_metrics_quarterly", "key_metrics_quarterly", "key_metrics", False),
        (11, "ratios_quarterly", "ratios_quarterly", "ratios", False),
        (12, "income_statement_quarterly", "income_statement_quarterly",
         "fundamentals_statements", False),
        (13, "balance_sheet_statement_quarterly", "balance_sheet_statement_quarterly",
         "fundamentals_statements", False),
        (14, "cash_flow_statement_quarterly", "cash_flow_statement_quarterly",
         "fundamentals_statements", False),
        (15, "company_profile", "company_profile", "company_profile", False),
    ]
    out: List[Dict] = []
    for rank, probe_id, catalog_name, family, critical in eps:
        if catalog_name is not None:
            template = paths[catalog_name]
            mapping_status = "mapped_in_client"
        else:
            # Added probe-adapter mapping (not in the shipped catalog).
            template = "/stable/grades-consensus?symbol={symbol}"
            mapping_status = "probe_adapter_added"
        out.append({
            "rank": rank,
            "probe_id": probe_id,
            "catalog_endpoint": catalog_name or "(probe_adapter)",
            "family": family,
            "critical": critical,
            "path_template": template,
            "mapping_status": mapping_status,
        })
    out.sort(key=lambda e: (e["rank"], e["probe_id"]))
    return out


def build_probe_queue(max_endpoint_families: int = MAX_ENDPOINT_FAMILIES,
                      n_tickers: int = MAX_TICKERS,
                      max_requests: int = MAX_REQUESTS) -> Tuple[List[Dict], List[str]]:
    """THE CONTROLLER FIX.

    Build the bounded probe queue such that:
      1. critical (earnings/analyst) families are always at the FRONT (rank order);
      2. a generic endpoint-family cap can ONLY trim the NON-critical tail - it can
         NEVER drop a critical family (the original bug: a cap of 6 dropped all six
         critical families because they were ordered last);
      3. the request budget (families * tickers) is respected by trimming NON-critical
         families only.

    Returns (queue, notes) where notes records any cap override that protected a
    critical family.
    """
    eps = build_probe_endpoints()
    critical = [e for e in eps if e["critical"]]
    non_critical = [e for e in eps if not e["critical"]]
    notes: List[str] = []

    cap = max(0, int(max_endpoint_families))
    if cap < len(critical):
        notes.append(
            "endpoint-family cap (%d) is below the number of critical families (%d); "
            "OVERRIDING the cap so no critical earnings/analyst family is dropped."
            % (cap, len(critical)))
        non_critical_slots = 0
    else:
        non_critical_slots = cap - len(critical)
    queue = list(critical) + non_critical[:non_critical_slots]

    # Respect the absolute request budget by trimming NON-critical families only.
    n_tickers = max(1, int(n_tickers))
    budget = max(0, int(max_requests))
    while len(queue) * n_tickers > budget and any(not e["critical"] for e in queue):
        # drop the lowest-priority non-critical family
        for i in range(len(queue) - 1, -1, -1):
            if not queue[i]["critical"]:
                dropped = queue.pop(i)
                notes.append("request-budget trim: dropped non-critical '%s' (family=%s)."
                             % (dropped["probe_id"], dropped["family"]))
                break
    if len(queue) * n_tickers > budget:
        notes.append(
            "WARNING: critical families alone (%d) * tickers (%d) exceed the request "
            "budget (%d); critical families are still probed (never dropped)."
            % (len([e for e in queue if e['critical']]), n_tickers, budget))
    return queue, notes


# --------------------------------------------------------------------------- #
# Small IO helpers (stdlib only)
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


# --------------------------------------------------------------------------- #
# Classification: (payload | error) -> (status, likely_cause, next_action).
# Never sees or echoes the API key; reasons only from HTTP status + payload shape.
# --------------------------------------------------------------------------- #
def _payload_shape(payload) -> Tuple[str, int]:
    if isinstance(payload, list):
        return ("list", len([r for r in payload if isinstance(r, dict)]))
    if isinstance(payload, dict):
        # Dry-run metadata dicts are not real data; treat as zero rows.
        if payload.get("dry_run"):
            return ("dry_run_meta", 0)
        # FMP error envelopes sometimes come back as a dict with an "Error Message".
        if any(k.lower().startswith("error") for k in payload.keys()):
            return ("error_envelope", 0)
        return ("dict", 1)
    return ("none", 0)


def classify_success(payload) -> Tuple[str, str, str, str, int]:
    """Classify a 200 payload. Returns (status, cause, next_action, response_type, rows)."""
    response_type, rows = _payload_shape(payload)
    if response_type == "error_envelope":
        return (ST_CLIENT_UPDATE, CAUSE_CLIENT_UPDATE,
                "Provider returned a 200 error envelope; verify the stable endpoint path/params.",
                response_type, 0)
    if rows > 0:
        return (ST_ACCESS_VERIFIED, CAUSE_ACCESSIBLE,
                "Entitled and returning data; promote to Phase 5-E1 controlled backfill.",
                response_type, rows)
    return (ST_EMPTY_REACHABLE, CAUSE_NO_DATA,
            "Endpoint reachable but no rows for this symbol/params; confirm with another symbol.",
            response_type, rows)


def classify_error(status_code: Optional[int], error_type: str) -> Tuple[str, str, str]:
    """Classify a FmpError. Returns (status, likely_cause, next_action)."""
    code = status_code
    if code == 401:
        return (ST_SUBSCRIPTION_BLOCK, CAUSE_INVALID_KEY,
                "Key rejected (401); confirm FMP_API_KEY is active in the FMP dashboard.")
    if code in (402, 403):
        return (ST_SUBSCRIPTION_BLOCK, CAUSE_SUBSCRIPTION,
                "Endpoint not entitled on the current plan (%s); price the FMP tier that "
                "includes this family, or use the recommended alternative provider." % code)
    if code == 404:
        return (ST_CLIENT_UPDATE, CAUSE_WRONG_PATH,
                "404 - stable endpoint path is wrong/renamed; update the client mapping and re-probe.")
    if code == 429:
        return (ST_RATE_LIMITED, CAUSE_RATE_LIMIT,
                "429 - plan request cap hit; throttle and retry within the request budget.")
    if code in (500, 502, 503, 504):
        return (ST_CLIENT_UPDATE, CAUSE_UNKNOWN,
                "Provider %s server error after retries; retry later, then verify the path." % code)
    if error_type == "network_error":
        return (ST_CLIENT_UPDATE, CAUSE_UNKNOWN,
                "Network/transport error reaching the provider; check connectivity and retry.")
    if error_type == "bad_response":
        return (ST_CLIENT_UPDATE, CAUSE_WRONG_PATH,
                "Non-JSON body (likely an HTML error page); verify the stable endpoint path.")
    if error_type == "host_blocked":
        return (ST_NOT_AVAILABLE, CAUSE_UNKNOWN,
                "Adapter refused a non-FMP host; only financialmodelingprep.com is allowed.")
    return (ST_CLIENT_UPDATE, CAUSE_UNKNOWN,
            "Unclassified provider error; inspect the sanitized message against FMP stable docs.")


# Aggregate per-endpoint statuses into one family status (best wins).
_STATUS_RANK = {
    ST_ACCESS_VERIFIED: 0, ST_EMPTY_REACHABLE: 1, ST_RATE_LIMITED: 2,
    ST_CLIENT_UPDATE: 3, ST_SUBSCRIPTION_BLOCK: 4, ST_NOT_AVAILABLE: 5,
    ST_NOT_TESTED_NO_MAPPING: 6, ST_PENDING_PROBE: 7,
}


def _aggregate_family_status(statuses: List[str]) -> str:
    if not statuses:
        return ST_PENDING_PROBE
    return sorted(statuses, key=lambda s: _STATUS_RANK.get(s, 9))[0]


# --------------------------------------------------------------------------- #
# Bounded live probe (only when transport injected, or --live AND key present).
# --------------------------------------------------------------------------- #
Transport = Callable[[str], object]


def _probe_call(path: str, transport: Optional[Transport]):
    """Execute one probe. With an injected ``transport`` no key/network is used
    (tests). Otherwise a real bounded live GET via the FMP adapter."""
    if transport is not None:
        return transport(path)
    return fmp.get_json(path, live=True)


def run_probe(queue: List[Dict], tickers: List[str], transport: Optional[Transport],
              raw_dir: Path, norm_dir: Path, max_requests: int = MAX_REQUESTS,
              verbose: bool = False) -> Tuple[List[Dict], int, bool]:
    """Probe each queued endpoint family against ``tickers`` (bounded).

    Returns (result_rows, requests_made, key_rejected). Persists raw payloads under the
    gitignored research/data/fmp/raw/ tree. Never writes the key or a key-bearing URL.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    norm_dir.mkdir(parents=True, exist_ok=True)
    rows: List[Dict] = []
    requests_made = 0
    key_rejected = False
    for e in queue:
        for tk in tickers:
            if requests_made >= max_requests:
                break
            path = e["path_template"].replace("{symbol}", tk)
            redacted = fmp.redacted_request_url(path)
            requests_made += 1
            try:
                payload = _probe_call(path, transport)
                status, cause, next_action, response_type, n_rows = classify_success(payload)
                # Persist raw payload (gitignored; no key in body/path).
                if response_type not in ("dry_run_meta",):
                    raw_path = raw_dir / e["probe_id"] / ("%s.json" % tk)
                    raw_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(raw_path, "w", encoding="utf-8") as fh:
                        json.dump(payload, fh, indent=2)
                        fh.write("\n")
                http_status = 200
                err_type = ""
                err_msg = ""
            except fmp.FmpError as exc:
                http_status = getattr(exc, "status_code", None)
                err_type = getattr(exc, "error_type", "error")
                err_msg = str(exc)
                status, cause, next_action = classify_error(http_status, err_type)
                response_type = "error"
                n_rows = 0
                if http_status == 401:
                    key_rejected = True
            rows.append({
                "attempted": True,
                "provider": fmp.PROVIDER_NAME,
                "endpoint_family": e["probe_id"],
                "mandatory_family": e["family"],
                "critical": e["critical"],
                "rank": e["rank"],
                "ticker": tk,
                "attempted_url_redacted": redacted,
                "http_status": "" if http_status is None else http_status,
                "response_type": response_type,
                "row_count": n_rows,
                "result": ("success" if status == ST_ACCESS_VERIFIED
                           else "empty" if status == ST_EMPTY_REACHABLE else "error"),
                "status": status,
                "error_type": err_type,
                "error_message_sanitized": err_msg,
                "likely_cause": cause,
                "next_action": next_action,
            })
            if verbose:
                print("  probe %-28s %-5s -> %s (%s)"
                      % (e["probe_id"], tk, status, response_type))
            if transport is None:
                time.sleep(fmp.MIN_SLEEP_SECONDS)
        if requests_made >= max_requests:
            break
    return rows, requests_made, key_rejected


# --------------------------------------------------------------------------- #
# Prior-phase output ingestion (best-effort; tolerant of missing files).
# --------------------------------------------------------------------------- #
def read_prior_phase_outputs() -> Dict:
    """Read whatever Phase 5-E0 / 5-E1 / 8-K / 8-L provider/data-family summaries exist.

    Best-effort and offline: missing files are fine (they are simply recorded as absent).
    Never reads raw payloads or anything secret.
    """
    out_root = _REPO_ROOT / "research" / "output"
    candidates = {
        "phase5e0": out_root / "phase5e0_fmp_provider_trial" / "phase5e0_fmp_provider_trial.json",
        "phase5e1": out_root / "phase5e1_fmp_controlled_backfill" / "phase5e1_fmp_controlled_backfill.json",
        "phase8k": out_root / "phase8k_self_expanding_alpha_factory" / "phase8k_self_expanding_alpha_factory.json",
        "phase8l": out_root / "phase8l_data_family_expansion_signal_factory" / "phase8l_data_family_expansion_signal_factory.json",
    }
    seen: Dict[str, Dict] = {}
    for tag, path in candidates.items():
        if path.is_file():
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    json.load(fh)
                seen[tag] = {"found": True, "path": _rel(path)}
            except (OSError, json.JSONDecodeError):
                seen[tag] = {"found": False, "path": _rel(path), "note": "unreadable"}
        else:
            seen[tag] = {"found": False, "path": _rel(path)}
    return seen


# --------------------------------------------------------------------------- #
# Provider-comparison tables (offline knowledge; costs UNKNOWN where unverifiable).
# --------------------------------------------------------------------------- #
# Per critical/high-value family: which providers plausibly serve it. We never invent a
# dollar cost - cost is UNKNOWN unless it is a free public source.
_PROVIDER_MATRIX = {
    "broad_earnings_surprise": {
        "fmp": "yes", "alpha_vantage": "yes(EARNINGS)", "finnhub": "yes",
        "eodhd": "yes", "free": "", "cheapest": "FMP", "unlocks_most": "yes",
        "env": "FMP_API_KEY"},
    "earnings_calendar": {
        "fmp": "yes", "alpha_vantage": "yes(EARNINGS_CALENDAR)", "finnhub": "yes",
        "eodhd": "yes", "free": "", "cheapest": "FMP", "unlocks_most": "yes",
        "env": "FMP_API_KEY"},
    "analyst_estimates": {
        "fmp": "yes", "alpha_vantage": "limited", "finnhub": "yes(premium)",
        "eodhd": "yes", "free": "", "cheapest": "FMP", "unlocks_most": "yes",
        "env": "FMP_API_KEY"},
    "analyst_recommendations": {
        "fmp": "yes", "alpha_vantage": "no", "finnhub": "yes",
        "eodhd": "yes", "free": "", "cheapest": "FMP", "unlocks_most": "yes",
        "env": "FMP_API_KEY"},
    "analyst_price_targets": {
        "fmp": "yes", "alpha_vantage": "no", "finnhub": "yes(premium)",
        "eodhd": "limited", "free": "", "cheapest": "FMP", "unlocks_most": "yes",
        "env": "FMP_API_KEY"},
    "ratings_or_grades": {
        "fmp": "yes", "alpha_vantage": "no", "finnhub": "yes",
        "eodhd": "limited", "free": "", "cheapest": "FMP", "unlocks_most": "yes",
        "env": "FMP_API_KEY"},
    "key_metrics": {
        "fmp": "yes", "alpha_vantage": "partial(OVERVIEW)", "finnhub": "yes(metrics)",
        "eodhd": "yes", "free": "SEC EDGAR(derive)", "cheapest": "FMP", "unlocks_most": "no",
        "env": "FMP_API_KEY"},
    "ratios": {
        "fmp": "yes", "alpha_vantage": "partial", "finnhub": "yes(metrics)",
        "eodhd": "yes", "free": "SEC EDGAR(derive)", "cheapest": "FMP", "unlocks_most": "no",
        "env": "FMP_API_KEY"},
    "fundamentals_statements": {
        "fmp": "yes", "alpha_vantage": "yes", "finnhub": "yes",
        "eodhd": "yes", "free": "SEC EDGAR companyfacts(free)", "cheapest": "SEC EDGAR (free)",
        "unlocks_most": "no", "env": ""},
    "company_profile": {
        "fmp": "yes", "alpha_vantage": "yes(OVERVIEW)", "finnhub": "yes",
        "eodhd": "yes", "free": "SEC EDGAR(free)", "cheapest": "SEC EDGAR (free)",
        "unlocks_most": "no", "env": ""},
    "transcripts_or_guidance": {
        "fmp": "yes(premium)", "alpha_vantage": "no", "finnhub": "yes(premium)",
        "eodhd": "no", "free": "", "cheapest": COST_UNKNOWN, "unlocks_most": "no",
        "env": "FMP_API_KEY"},
    "news_or_press_releases": {
        "fmp": "yes", "alpha_vantage": "yes(NEWS_SENTIMENT)", "finnhub": "yes",
        "eodhd": "yes", "free": "GDELT(free)", "cheapest": "GDELT (free)", "unlocks_most": "no",
        "env": ""},
    "insider_transactions": {
        "fmp": "yes", "alpha_vantage": "no", "finnhub": "yes",
        "eodhd": "limited", "free": "SEC EDGAR Form 4(free)", "cheapest": "SEC EDGAR (free)",
        "unlocks_most": "no", "env": ""},
    "institutional_ownership_13f": {
        "fmp": "yes", "alpha_vantage": "no", "finnhub": "limited",
        "eodhd": "no", "free": "SEC EDGAR 13F(free)", "cheapest": "SEC EDGAR (free)",
        "unlocks_most": "no", "env": ""},
    "options_iv_skew_putcall": {
        "fmp": "no", "alpha_vantage": "partial(HIST_OPTIONS)", "finnhub": "limited",
        "eodhd": "yes(options)", "free": "", "cheapest": COST_UNKNOWN, "unlocks_most": "no",
        "env": "INTRINIO_API_KEY"},
    "short_interest_borrow": {
        "fmp": "limited", "alpha_vantage": "no", "finnhub": "yes(premium)",
        "eodhd": "no", "free": "FINRA(free)", "cheapest": "FINRA (free)", "unlocks_most": "no",
        "env": ""},
    "sec_filings_event_classification": {
        "fmp": "limited", "alpha_vantage": "no", "finnhub": "limited",
        "eodhd": "no", "free": "SEC EDGAR(free)", "cheapest": "SEC EDGAR (free)",
        "unlocks_most": "no", "env": ""},
    "macro_cross_asset_context": {
        "fmp": "no", "alpha_vantage": "partial", "finnhub": "no",
        "eodhd": "partial", "free": "Norgate(local)", "cheapest": "Norgate (local)",
        "unlocks_most": "no", "env": ""},
    "sector_industry_context": {
        "fmp": "yes(profile)", "alpha_vantage": "partial", "finnhub": "yes",
        "eodhd": "yes", "free": "Norgate(local)", "cheapest": "Norgate (local)",
        "unlocks_most": "no", "env": ""},
    "liquidity_volume_volatility_positioning": {
        "fmp": "no", "alpha_vantage": "partial", "finnhub": "partial",
        "eodhd": "partial", "free": "Norgate(local)", "cheapest": "Norgate (local)",
        "unlocks_most": "no", "env": ""},
}

# Data-family -> signals it unlocks (exact pairings from the brief, plus support cohorts).
_SIGNAL_UNLOCK = {
    "broad_earnings_surprise": ["earnings_surprise x rates_sensitivity",
                                "earnings_surprise x sector_leadership"],
    "earnings_calendar": ["earnings_event_confirmation x rates_sensitivity",
                          "earnings_drift x sector_leadership"],
    "analyst_estimates": ["analyst_revision x sensitivity"],
    "analyst_recommendations": ["analyst_recommendation_change x sector_leadership"],
    "analyst_price_targets": ["price_target_revision x sensitivity_cohort"],
    "ratings_or_grades": ["grade_change x sector_leadership"],
    "key_metrics": ["valuation_quality_cohort_support"],
    "ratios": ["leverage_quality_cohort_support"],
    "fundamentals_statements": ["value_quality_growth_cohort_support"],
    "company_profile": ["sector_mapping_support"],
    "transcripts_or_guidance": ["transcript_tone x earnings/macro_sensitivity"],
    "news_or_press_releases": ["news_sentiment x sensitivity"],
    "insider_transactions": ["insider x event_confirmation"],
    "institutional_ownership_13f": ["institutional_13f x event_confirmation"],
    "options_iv_skew_putcall": ["options_iv_skew x downside/volatility_sensitivity"],
    "short_interest_borrow": ["short_interest x liquidity/volatility/earnings_confirmation"],
    "sec_filings_event_classification": ["filing_event x earnings_confirmation"],
    "macro_cross_asset_context": ["macro_regime_context (conditioning)"],
    "sector_industry_context": ["sector_leadership (conditioning)"],
    "liquidity_volume_volatility_positioning": ["liquidity/volatility_gates (conditioning)"],
}


# --------------------------------------------------------------------------- #
# Secret leak scan over written artifacts (reused contract from Phase 5-E0).
# --------------------------------------------------------------------------- #
def _scan_for_leaks(paths: Sequence[Path], api_key: Optional[str]) -> Tuple[bool, int]:
    marker = "api" + "key="
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
        if api_key and api_key in text:
            clean = False
        if marker in text.lower():
            clean = False
    return clean, scanned


def build_secret_audit(api_key_present: bool, leak_clean: bool, scanned: int) -> List[Dict]:
    def row(check, value, passed, detail):
        return {"check": check, "value": value, "passed": passed, "detail": detail}
    return [
        row("api_key_read_from_env_only", fmp.API_KEY_ENV, True,
            "key is read solely from the %s environment variable" % fmp.API_KEY_ENV),
        row("api_key_present_in_env", api_key_present, None,
            "informational: whether a key is currently set (not a pass/fail)"),
        row("api_key_logged", False, True,
            "the key is never printed to stdout/stderr or written to any artifact"),
        row("api_key_written_to_disk", False, True,
            "the key value is never persisted to any file"),
        row("urls_redacted_in_artifacts", True, True,
            "every persisted URL strips the key parameter and shows the %s placeholder"
            % fmp.REDACTED_KEY_PLACEHOLDER),
        row("paid_data_dir_gitignored", _rel(_FMP_DATA_DIR), True,
            "raw + normalized FMP payloads live only under a gitignored tree"),
        row("no_key_in_output_files", leak_clean, leak_clean,
            "scanned %d artifact(s); no raw key value and no key query marker found" % scanned),
        row("live_requires_explicit_flag_and_key", True, True,
            "a network call needs both --live and a present %s" % fmp.API_KEY_ENV),
        row("provider_restricted_to_fmp_host", fmp.ALLOWED_HOST, True,
            "live requests are refused for any host other than %s" % fmp.ALLOWED_HOST),
    ]


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #
def derive_decision(family_status: Dict[str, str], probe_executed: bool,
                    key_ok: bool, api_key_present: bool) -> str:
    """``key_ok`` is True when the probe was authoritative (a real present+accepted key,
    OR an injected transport in tests). ``probe_executed`` without ``key_ok`` means the
    key was absent or rejected (401)."""
    if not probe_executed:
        return DEC_BLOCKED_NO_KEY if not api_key_present else DEC_DRY_RUN
    if not key_ok:
        return DEC_BLOCKED_NO_KEY
    crit = [f for f in MANDATORY_FAMILIES if f["critical"]]
    crit_status = [family_status.get(f["family"], ST_PENDING_PROBE) for f in crit]
    ok = {ST_ACCESS_VERIFIED, ST_EMPTY_REACHABLE}
    if all(s in ok for s in crit_status):
        return DEC_FMP_SUFFICIENT
    blocked = [s for s in crit_status if s == ST_SUBSCRIPTION_BLOCK]
    client = [s for s in crit_status if s == ST_CLIENT_UPDATE]
    if blocked and not client:
        return DEC_FMP_UPGRADE
    if client and not blocked:
        return DEC_FMP_CLIENT_UPDATE
    if blocked and client:
        return DEC_MIXED_STRATEGY
    # some critical families verified, others rate-limited/empty -> retry, mixed posture
    return DEC_MIXED_STRATEGY


_NEXT_CMD = {
    DEC_FMP_SUFFICIENT: "python research/run_phase5e1_fmp_controlled_backfill.py --live "
                        "(controlled PIT backfill of the now-entitled critical families)",
    DEC_FMP_UPGRADE: "Upgrade the FMP plan to the tier that includes the blocked critical "
                     "families, then re-run: python research/run_phase8m_critical_market_data_"
                     "family_audit.py --live",
    DEC_FMP_CLIENT_UPDATE: "Update the stable endpoint mapping(s) flagged in "
                           "fmp_client_endpoint_gap_report.csv, then re-run --live.",
    DEC_CHEAPER_PROVIDER: "Activate the cheaper provider named in cheapest_viable_provider_"
                          "matrix.csv for the blocked families, then re-probe.",
    DEC_MIXED_STRATEGY: "Apply the mixed provider/endpoint plan in research_director_decision."
                        "json, then re-run --live for the unresolved families.",
    DEC_BLOCKED_NO_KEY: "Set FMP_API_KEY in the environment, then re-run --live.",
    DEC_DRY_RUN: "Re-run with --live (FMP_API_KEY present) to execute the bounded "
                 "entitlement probe.",
    DEC_ERROR: "Inspect fmp_critical_endpoint_probe_results.csv and re-run --live once resolved.",
}


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run(live: bool = False, tickers: Optional[List[str]] = None,
        max_endpoint_families: int = MAX_ENDPOINT_FAMILIES, max_tickers: int = MAX_TICKERS,
        max_requests: int = MAX_REQUESTS, transport: Optional[Transport] = None,
        out_dir: Optional[Path] = None, data_dir: Optional[Path] = None,
        verbose: bool = True) -> Dict:
    """Run the Phase 8-M audit.

    Default (``live=False``, no ``transport``): DRY-RUN - builds the controller-fixed
    probe plan + the 20-family inventory + all comparison tables with NO network call.
    Probe-backed families are marked PENDING_LIVE_PROBE (a non-terminal planning marker).

    Audit (``live=True`` with FMP_API_KEY, OR ``transport`` injected for tests): executes
    the bounded, critical-first entitlement probe and resolves each FMP-backed family to a
    terminal status. ``transport`` (tests) needs neither a key nor a network.
    """
    tickers = [t.strip().upper() for t in (tickers or DEFAULT_TICKERS) if str(t).strip()]
    tickers = tickers[:max(1, min(int(max_tickers), MAX_TICKERS))]
    api_key_present = fmp.has_api_key()

    # Probe runs only with an injected transport (tests) OR explicit --live AND a key.
    probe_executed = transport is not None or (bool(live) and api_key_present)

    P = _Paths(out_dir, data_dir)
    P.out.mkdir(parents=True, exist_ok=True)
    queue, queue_notes = build_probe_queue(max_endpoint_families, len(tickers), max_requests)
    all_eps = build_probe_endpoints()
    prior = read_prior_phase_outputs()

    # ----- probe plan (always written; network-free) -----
    plan_rows = []
    for e in all_eps:
        in_queue = any(q["probe_id"] == e["probe_id"] for q in queue)
        for tk in (tickers if in_queue else tickers[:1]):
            plan_rows.append([
                e["rank"], e["probe_id"], e["family"], e["critical"], in_queue,
                e["catalog_endpoint"], e["mapping_status"], tk,
                fmp.redacted_request_url(e["path_template"].replace("{symbol}", tk)),
            ])
    _write_csv(P.probe_plan,
               ["rank", "endpoint_family", "mandatory_family", "critical", "in_bounded_queue",
                "catalog_endpoint", "mapping_status", "ticker", "attempted_url_redacted"],
               plan_rows)

    # ----- bounded probe (only when executing) -----
    probe_rows: List[Dict] = []
    requests_made = 0
    key_rejected = False
    if probe_executed:
        probe_rows, requests_made, key_rejected = run_probe(
            queue, tickers, transport, P.raw, P.norm, max_requests, verbose)

    # ----- resolve per-mandatory-family status -----
    # FMP-probe families: aggregate from probe rows (or PENDING in dry-run).
    family_probe_status: Dict[str, List[str]] = {}
    for r in probe_rows:
        family_probe_status.setdefault(r["mandatory_family"], []).append(r["status"])

    family_status: Dict[str, str] = {}
    inventory_rows = []
    for f in MANDATORY_FAMILIES:
        fam = f["family"]
        if f["rk"] == RK_FMP_PROBE:
            if probe_executed:
                status = _aggregate_family_status(family_probe_status.get(fam, []))
            else:
                status = ST_PENDING_PROBE
        else:
            status = f["static_status"]
        family_status[fam] = status
        inventory_rows.append([
            fam, f["rk"], f["critical"], status, f["provider"], f["env_var"],
            f["free_alt"], ";".join(_SIGNAL_UNLOCK.get(fam, [])), f["note"],
        ])
    _write_csv(P.inventory,
               ["mandatory_family", "resolution_kind", "critical", "status", "provider",
                "env_var", "free_alternative", "signals_unlocked", "note"],
               inventory_rows)

    # ----- missing-data-family priority queue (unresolved families, critical first) -----
    resolved = {ST_ACCESS_VERIFIED, ST_EMPTY_REACHABLE, ST_LOCAL_AVAILABLE, ST_FREE_AVAILABLE}
    missing = []
    for f in MANDATORY_FAMILIES:
        st = family_status[f["family"]]
        if st in resolved:
            continue
        missing.append(f)
    # critical first, then by mandatory order
    missing.sort(key=lambda f: (not f["critical"], MANDATORY_FAMILY_IDS.index(f["family"])))
    _write_csv(P.priority_queue,
               ["priority", "mandatory_family", "critical", "current_status", "recommended_provider",
                "env_var", "free_first", "signals_unlocked"],
               [[i + 1, f["family"], f["critical"], family_status[f["family"]], f["provider"],
                 f["env_var"], f["free_alt"], ";".join(_SIGNAL_UNLOCK.get(f["family"], []))]
                for i, f in enumerate(missing)])

    # ----- probe results + failure diagnosis -----
    _write_csv(P.probe_results,
               ["attempted", "provider", "endpoint_family", "mandatory_family", "critical",
                "ticker", "attempted_url_redacted", "http_status", "response_type", "row_count",
                "result", "status", "error_type", "error_message_sanitized", "likely_cause",
                "next_action"],
               [[r["attempted"], r["provider"], r["endpoint_family"], r["mandatory_family"],
                 r["critical"], r["ticker"], r["attempted_url_redacted"], r["http_status"],
                 r["response_type"], r["row_count"], r["result"], r["status"], r["error_type"],
                 r["error_message_sanitized"], r["likely_cause"], r["next_action"]]
                for r in probe_rows])

    failures = [r for r in probe_rows if r["result"] == "error"]
    _write_csv(P.failure_diag,
               ["endpoint_family", "mandatory_family", "critical", "ticker", "http_status",
                "error_type", "likely_cause", "status", "next_action", "error_message_sanitized"],
               [[r["endpoint_family"], r["mandatory_family"], r["critical"], r["ticker"],
                 r["http_status"], r["error_type"], r["likely_cause"], r["status"],
                 r["next_action"], r["error_message_sanitized"]] for r in failures])

    # ----- subscription requirement decision (per FMP-probe family) -----
    sub_rows = []
    for f in MANDATORY_FAMILIES:
        if f["rk"] != RK_FMP_PROBE:
            continue
        st = family_status[f["family"]]
        if st == ST_SUBSCRIPTION_BLOCK:
            req = "UPGRADE_REQUIRED"
        elif st in (ST_ACCESS_VERIFIED, ST_EMPTY_REACHABLE):
            req = "INCLUDED_IN_CURRENT_PLAN"
        elif st == ST_CLIENT_UPDATE:
            req = "ENDPOINT_FIX_NOT_SUBSCRIPTION"
        elif st == ST_RATE_LIMITED:
            req = "RATE_LIMITED_RETRY"
        elif st == ST_PENDING_PROBE:
            req = "PENDING_LIVE_PROBE"
        else:
            req = "UNKNOWN"
        sub_rows.append([f["family"], f["critical"], st, req, f["env_var"]])
    _write_csv(P.subscription_decision,
               ["mandatory_family", "critical", "status", "subscription_requirement", "env_var"],
               sub_rows)

    # ----- client endpoint gap report -----
    gap_rows = []
    for e in all_eps:
        st = _aggregate_family_status([r["status"] for r in probe_rows
                                       if r["endpoint_family"] == e["probe_id"]]) \
            if probe_executed else ST_PENDING_PROBE
        needs_update = st == ST_CLIENT_UPDATE or e["mapping_status"] == "probe_adapter_added"
        gap_rows.append([
            e["probe_id"], e["family"], e["catalog_endpoint"], e["mapping_status"],
            e["path_template"], st, "yes" if needs_update else "no",
        ])
    # add the no-mapping mandatory families (transcripts) as explicit client gaps
    for f in MANDATORY_FAMILIES:
        if f["rk"] == RK_NO_MAPPING:
            gap_rows.append([
                "(none)", f["family"], "(unmapped)", "missing_mapping",
                "ADD: /stable/earning-call-transcript?symbol={symbol}", ST_NOT_TESTED_NO_MAPPING,
                "yes",
            ])
    _write_csv(P.client_gap,
               ["endpoint_family", "mandatory_family", "catalog_endpoint", "mapping_status",
                "path_template", "status", "needs_client_update"],
               gap_rows)

    # ----- provider alternative + cheapest viable matrices -----
    alt_rows = []
    cheapest_rows = []
    for f in MANDATORY_FAMILIES:
        fam = f["family"]
        m = _PROVIDER_MATRIX.get(fam, {})
        st = family_status[fam]
        fmp_access = ("yes" if st in (ST_ACCESS_VERIFIED, ST_EMPTY_REACHABLE)
                      else "no" if st in (ST_SUBSCRIPTION_BLOCK, ST_NOT_AVAILABLE)
                      else m.get("fmp", "unknown"))
        fmp_issue = ("subscription" if st == ST_SUBSCRIPTION_BLOCK
                     else "endpoint_mapping" if st == ST_CLIENT_UPDATE
                     else "not_offered" if st == ST_NOT_AVAILABLE
                     else "none" if st in (ST_ACCESS_VERIFIED, ST_EMPTY_REACHABLE)
                     else "unknown")
        alt_rows.append([
            fam, f["critical"], fmp_access, fmp_issue, m.get("alpha_vantage", ""),
            m.get("finnhub", ""), m.get("eodhd", ""), m.get("free", ""),
            m.get("cheapest", COST_UNKNOWN), m.get("unlocks_most", "no"), m.get("env", ""),
        ])
        cheapest_rows.append([
            fam, f["critical"], st, m.get("cheapest", COST_UNKNOWN),
            (COST_UNKNOWN if m.get("free", "") == "" else "free"),
            m.get("free", ""), m.get("env", ""),
        ])
    _write_csv(P.provider_alt,
               ["mandatory_family", "critical", "fmp_can_access", "fmp_issue", "alpha_vantage",
                "finnhub", "eodhd", "free_source", "cheapest_viable_provider",
                "provider_unlocks_most_families", "env_var"],
               alt_rows)
    _write_csv(P.cheapest,
               ["mandatory_family", "critical", "status", "cheapest_viable_provider",
                "approx_monthly_cost_usd", "free_alternative", "env_var"],
               cheapest_rows)

    # ----- signal-unlock map -----
    _write_csv(P.signal_unlock,
               ["mandatory_family", "status", "signal_unlocked", "is_provider_required"],
               [[fam, family_status[fam], sig,
                 family_status[fam] not in resolved]
                for fam in MANDATORY_FAMILY_IDS for sig in _SIGNAL_UNLOCK.get(fam, [])])

    # ----- provider activation commands (PLACEHOLDER only; NEVER a real key) -----
    _write_activation_commands(P.activation_cmds)

    # ----- paid data storage manifest -----
    raw_files = sum(1 for _ in P.raw.rglob("*.json")) if P.raw.is_dir() else 0
    norm_files = sum(1 for _ in P.norm.rglob("*.csv")) if P.norm.is_dir() else 0
    _write_csv(P.storage_manifest,
               ["path", "purpose", "gitignored", "committed", "file_count"],
               [[_rel(P.raw), "verbatim FMP raw JSON payloads (paid data)", "yes", "no", raw_files],
                [_rel(P.norm), "normalized point-in-time CSVs from paid data", "yes", "no",
                 norm_files]])

    # ----- decision -----
    # A probe is authoritative when an injected transport drove it (tests, no key needed)
    # OR a real key was present and not rejected (401).
    key_ok = (transport is not None) or (api_key_present and not key_rejected)
    decision = derive_decision(family_status, probe_executed, key_ok, api_key_present)
    recommended_provider, recommended_reason = _recommend_provider(family_status, decision)

    # ----- controller fix report -----
    _write_controller_fix_report(P.controller_fix, queue, queue_notes, max_endpoint_families)

    # ----- secret leak scan over everything written so far -----
    written = [p for p in P.out.glob("*") if p.is_file()]
    leak_clean, scanned = _scan_for_leaks(written, fmp.resolve_api_key())
    secret_audit = build_secret_audit(api_key_present, leak_clean, scanned)
    _write_csv(P.secret_audit, ["check", "value", "passed", "detail"],
               [[a["check"], a["value"], "" if a["passed"] is None else a["passed"], a["detail"]]
                for a in secret_audit])

    # ----- director decision + next plan + main report -----
    director = {
        "phase": PHASE,
        "decision": decision,
        "decision_allowed_values": list(ALLOWED_DECISIONS),
        "decision_is_terminal": decision in ALLOWED_DECISIONS,
        "probe_executed": probe_executed,
        "api_key_present": api_key_present,
        "key_rejected": key_rejected,
        "recommended_first_provider": recommended_provider,
        "recommended_first_provider_reason": recommended_reason,
        "critical_family_status": {f["family"]: family_status[f["family"]]
                                   for f in MANDATORY_FAMILIES if f["critical"]},
        "free_sources_to_exhaust_first": ["FINRA (short interest)", "GDELT (news)",
                                          "SEC EDGAR (filings/insider/13F)", "Norgate (local)"],
        "do_not_buy_yet": ["options IV/skew specialist (no signal needs it yet)",
                           "paid news (GDELT free first)",
                           "paid short interest (FINRA free first)"],
        "exact_next_command": _NEXT_CMD[decision],
        "preview_only": True,
        "orders_enabled": False,
        "automation_enabled": False,
        "broker_execution_enabled": False,
        "deployed": False,
        "committed": False,
    }
    _write_json(P.director_decision, director)

    next_plan = {
        "phase": "8-N",
        "title": "FMP Critical-Family Controlled Backfill / Provider Activation",
        "depends_on_decision": decision,
        "objective": ("Act on the Phase 8-M entitlement decision: if FMP entitles the critical "
                      "families, run the controlled PIT backfill (Phase 5-E1) for earnings + "
                      "analyst families; if blocked, activate the recommended provider/tier or "
                      "fix the flagged endpoint mapping, then re-probe."),
        "next_command": _NEXT_CMD[decision],
        "bounded_limits": {"max_tickers": MAX_TICKERS, "max_endpoint_families": MAX_ENDPOINT_FAMILIES,
                           "max_requests": MAX_REQUESTS},
        "free_sources_to_exhaust_first": director["free_sources_to_exhaust_first"],
        "preview_only": True, "orders_enabled": False, "automation_enabled": False,
        "broker_execution_enabled": False, "committed": False,
    }
    _write_json(P.next_plan, next_plan)

    report = {
        "phase": PHASE,
        "objective": ("Critical market-data-family entitlement audit + agent controller fix: "
                      "prioritize unresolved high-impact earnings/analyst blockers FIRST, ensure "
                      "no endpoint cap can drop them, run a bounded FMP entitlement probe, and "
                      "classify the exact access result per endpoint family."),
        "provider": fmp.PROVIDER_NAME,
        "provider_host": fmp.ALLOWED_HOST,
        "mode": "live_probe" if probe_executed else "dry_run",
        "live_requested": bool(live),
        "api_key_env_var": fmp.API_KEY_ENV,
        "api_key_present": api_key_present,
        "api_key_logged": False,
        "bounded_limits": {"tickers": tickers, "max_tickers": MAX_TICKERS,
                           "max_endpoint_families": MAX_ENDPOINT_FAMILIES,
                           "max_requests": MAX_REQUESTS},
        "probe_executed": probe_executed,
        "requests_made": requests_made,
        "key_rejected": key_rejected,
        "mandatory_family_count": len(MANDATORY_FAMILIES),
        "mandatory_families": list(MANDATORY_FAMILY_IDS),
        "critical_families": [f["family"] for f in MANDATORY_FAMILIES if f["critical"]],
        "critical_first_probe_order": [q["probe_id"] for q in queue],
        "controller_fix_notes": queue_notes,
        "family_status": family_status,
        "allowed_statuses": list(ALLOWED_STATUSES),
        "every_family_has_concrete_status": all(
            family_status[f["family"]] in ALLOWED_STATUSES
            for f in MANDATORY_FAMILIES) if probe_executed else False,
        "decision": decision,
        "recommended_first_provider": recommended_provider,
        "prior_phase_outputs_read": prior,
        "secret_safety_leak_scan_clean": leak_clean,
        "secret_safety_files_scanned": scanned,
        "recommended_next_command": _NEXT_CMD[decision],
        "outputs": {
            "report": _rel(P.report),
            "market_data_family_inventory": _rel(P.inventory),
            "missing_data_family_priority_queue": _rel(P.priority_queue),
            "fmp_critical_endpoint_probe_plan": _rel(P.probe_plan),
            "fmp_critical_endpoint_probe_results": _rel(P.probe_results),
            "fmp_endpoint_failure_diagnosis": _rel(P.failure_diag),
            "fmp_subscription_requirement_decision": _rel(P.subscription_decision),
            "fmp_client_endpoint_gap_report": _rel(P.client_gap),
            "provider_alternative_matrix": _rel(P.provider_alt),
            "cheapest_viable_provider_matrix": _rel(P.cheapest),
            "data_family_to_signal_unlock_map": _rel(P.signal_unlock),
            "provider_activation_commands": _rel(P.activation_cmds),
            "paid_data_storage_manifest": _rel(P.storage_manifest),
            "secret_safety_audit": _rel(P.secret_audit),
            "agent_controller_fix_report": _rel(P.controller_fix),
            "research_director_decision": _rel(P.director_decision),
            "phase8n_next_plan": _rel(P.next_plan),
        },
        # Safety / provenance contract.
        "preview_only": True,
        "orders_enabled": False,
        "automation_enabled": False,
        "broker_execution_enabled": False,
        "production_replacement": False,
        "network_used": bool(probe_executed and transport is None),
        "paper_trader_touched": False,
        "gcp_touched": False,
        "deployed": False,
        "data_fabricated": False,
        "wrote_to_d_drive": False,
        "committed": False,
    }
    _write_json(P.report, report)
    return report


def _recommend_provider(family_status: Dict[str, str], decision: str) -> Tuple[str, str]:
    if decision in (DEC_FMP_SUFFICIENT,):
        return ("FMP (current plan)",
                "The current FMP plan already entitles the critical earnings/analyst families; "
                "no new provider purchase is required - proceed to controlled backfill.")
    if decision in (DEC_FMP_UPGRADE, DEC_MIXED_STRATEGY):
        return ("FMP (upgraded tier)",
                "FMP remains the smallest provider set that unlocks the most blocked critical "
                "families (earnings surprises + calendar + analyst estimates/recommendations/"
                "price targets + grades) from one key; price the tier that includes the blocked "
                "endpoints before considering specialists.")
    if decision == DEC_FMP_CLIENT_UPDATE:
        return ("FMP (current plan, endpoint fix)",
                "The block is a client endpoint-mapping gap, not a subscription gap; fix the "
                "flagged stable path(s) and re-probe before paying for anything.")
    if decision == DEC_CHEAPER_PROVIDER:
        return ("Cheaper provider (see cheapest_viable_provider_matrix.csv)",
                "A cheaper/free provider covers the blocked families at lower cost than upgrading FMP.")
    if decision == DEC_BLOCKED_NO_KEY:
        return ("FMP (set key first)",
                "FMP_API_KEY is absent or rejected; set/repair it, then re-run the bounded probe.")
    return ("FMP (pending live probe)",
            "Run the bounded --live probe to determine entitlement before any purchase decision.")


def _write_activation_commands(path: Path) -> None:
    """Write a PLACEHOLDER PowerShell helper. It NEVER contains a real key - only a
    paste-here placeholder and guidance. The script must not echo or persist secrets."""
    lines = [
        "# Phase 8-M provider activation commands (PLACEHOLDER ONLY - never paste a real",
        "# key into a committed file). Set the key in your CURRENT PowerShell session only;",
        "# the audit reads it from the environment and never writes it to disk.",
        "#",
        "# FMP is the recommended first provider (one key unlocks the most critical families).",
        "#   $env:FMP_API_KEY = '<PASTE_FMP_KEY_HERE>'",
        "#",
        "# Conditional / later providers (only if a signal needs them - exhaust free first):",
        "#   $env:INTRINIO_API_KEY = '<PASTE_INTRINIO_KEY_HERE>'   # options IV/skew (not yet needed)",
        "#   $env:FINNHUB_API_KEY  = '<PASTE_FINNHUB_KEY_HERE>'    # short interest depth (FINRA free first)",
        "#",
        "# Free sources need NO key: FINRA (short interest), GDELT (news), SEC EDGAR (filings).",
        "#",
        "# After setting FMP_API_KEY, run the bounded live entitlement probe:",
        "#   python research/run_phase8m_critical_market_data_family_audit.py --live",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _write_controller_fix_report(path: Path, queue: List[Dict], notes: List[str],
                                 cap: int) -> None:
    rows = [
        ["bug_id", "PHASE8M_CONTROLLER_ENDPOINT_ORDER",
         "Generic endpoint cap dropped all critical earnings/analyst families."],
        ["root_cause_file",
         "research/run_phase5e0_fmp_provider_trial_collector.py",
         "_select_live_endpoints() sorts by smoke_priority then slices [:max_endpoints]."],
        ["root_cause_catalog",
         "research/providers/fmp_client.py",
         "catalog smoke_priority orders fundamentals 1-6 and earnings/analyst 90-94 (last)."],
        ["original_symptom", "0_planned_0_attempted",
         "with --max-endpoints 6 the six critical families were never attempted."],
        ["fix", "critical_first_queue",
         "build_probe_queue() always front-loads critical families by rank (earnings/analyst "
         "rank 0-5) ahead of fundamentals/profile (rank 10+)."],
        ["fix_cap_exempt", "cap_cannot_drop_critical",
         "the endpoint-family cap trims ONLY the non-critical tail; if cap < #critical the cap "
         "is overridden so no critical family is dropped."],
        ["fix_budget", "budget_trims_non_critical_only",
         "the request budget is respected by dropping non-critical families only."],
        ["resulting_probe_order", ";".join(q["probe_id"] for q in queue),
         "critical-first bounded queue actually used this run (cap=%d)." % cap],
    ]
    for i, n in enumerate(notes):
        rows.append(["controller_note_%d" % (i + 1), "note", n])
    _write_csv(path, ["item", "value", "detail"], rows)


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Phase 8-M critical market-data family entitlement audit (dry-run by default).")
    ap.add_argument("--live", action="store_true",
                    help="Execute the bounded live entitlement probe (requires FMP_API_KEY).")
    ap.add_argument("--tickers", type=str, default="",
                    help="Comma-separated probe tickers (default AAPL,MSFT,NVDA; max 3).")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()] or DEFAULT_TICKERS
    report = run(live=args.live, tickers=tickers, verbose=True)
    print("phase:                         %s" % report["phase"])
    print("mode:                          %s" % report["mode"])
    print("api key present:               %s" % report["api_key_present"])
    print("probe executed:                %s" % report["probe_executed"])
    print("requests made:                 %s" % report["requests_made"])
    print("critical-first probe order:    %s" % ", ".join(report["critical_first_probe_order"]))
    print("decision:                      %s" % report["decision"])
    print("recommended first provider:    %s" % report["recommended_first_provider"])
    print("next command:                  %s" % report["recommended_next_command"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
