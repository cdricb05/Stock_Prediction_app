"""Phase 8-O - Cheapest Viable Provider Selection and Alternative-Data Entitlement Audit.

WHY THIS PHASE EXISTS
    Phase 8-N patched the FMP controller (a 402 no longer stops the batch) and then ran
    LIVE with the user's FMP_API_KEY. The verdict was unambiguous: the CURRENT FMP plan is
    INSUFFICIENT for broad signal research. All six critical families came back PARTIAL -
    only 8/20 tickers covered, with ~64-75% of attempted cells 402/403 subscription-blocked -
    so Phase 8-N's decision was FMP_PLAN_COVERAGE_INSUFFICIENT and its upgrade decision was
    UPGRADE_NOT_RECOMMENDED_USE_ALTERNATIVE. The bottleneck is now the DATA SOURCE, not the
    controller.

    Phase 8-O answers exactly one question, decisively and cheaply: which data-source path
    gives enough coverage for the critical alpha families - especially broad earnings
    surprises/calendar and analyst revisions/recommendations - at the LOWEST cost? It reads
    the Phase 8-N artifacts, summarizes which families are blocked and why, builds a provider
    decision matrix across FMP / FMP-upgrade / Alpha Vantage / Finnhub / EODHD (+ free
    sources where relevant), inventories which provider keys are actually present in the
    environment, optionally runs a BOUNDED probe of ONLY the providers whose keys are present,
    and emits a single honest recommendation: the cheapest provider to try first, the second
    provider only if needed, whether an FMP upgrade is justified, and an explicit statement
    that FMP Ultimate is NOT justified.

WHAT IT PRODUCES (13 committed-safe artifacts)
    A Phase-8-N coverage summary + a critical-family blocker summary, a provider decision
    matrix, a cheapest-viable-provider ranking, a provider key inventory (presence ONLY), a
    provider probe plan + probe results, a provider cost/value report, a placeholder env-var
    setup .ps1, a structured provider-acquisition decision, a signal-family unlock map, and
    the Phase 8-P next plan.

SECRET DISCIPLINE (hard rules)
    * Provider API keys are read ONLY from their env vars, NEVER printed, NEVER written to disk.
    * The key inventory records PRESENCE ONLY (True/False); no key value is ever read into an
      artifact. Persisted/committed URLs strip every secret query parameter entirely (apikey /
      token / api_token) and append a placeholder, so no committed artifact contains "apikey=".
    * Default run is OFFLINE: it reads the Phase 8-N artifacts + static provider knowledge and
      writes the decision with NO network call. A probe needs an explicit --live AND a present
      key for that specific provider. Tests run fully offline via an injected ``transport`` and
      never require a key or a network.
    * Paid data (verbatim probe payloads) lives ONLY under the gitignored
      research/data/<provider>/{raw,normalized}/ trees. Committed artifacts carry only
      coverage / entitlement / decision METADATA - never payloads and never a key.

HARD CONSTRAINTS HONORED
    Existing installed packages only (stdlib). No package install. No full S&P 500 backfill
    (a probe is at most 1 ticker x 1 endpoint per keyed provider). No Paper Trader. No GCP.
    No deploy. No broker / order / automation logic. No commit. No push. FMP Ultimate is never
    recommended by default - the cheapest source that solves each missing family is preferred.

Run (default = OFFLINE: read 8-N + decide, no network):
    python research/run_phase8o_cheapest_provider_selection.py
Bounded live probe of keyed alternative providers (requires that provider's key in the env):
    python research/run_phase8o_cheapest_provider_selection.py --live
Test:
    python -m pytest tests/test_phase8o_cheapest_provider_selection.py -q
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PHASE = "8-O"

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase8o_cheapest_provider_selection"
# Where the Phase 8-N committed-safe artifacts live (read-only input to this phase).
_PHASE8N_DIR = (_REPO_ROOT / "research" / "output"
                / "phase8n_fmp_critical_data_backfill_signal_expansion")
# Probe payloads (if a keyed provider is probed) live ONLY under these gitignored trees.
_DATA_ROOT = _REPO_ROOT / "research" / "data"

# The 13 committed-safe artifact basenames (fixed names; tests resolve under a tmp dir).
_ARTIFACTS = {
    "report": "phase8o_cheapest_provider_selection.json",
    "fmp_coverage": "phase8n_fmp_coverage_summary.csv",
    "blocker_summary": "critical_family_blocker_summary.csv",
    "decision_matrix": "provider_decision_matrix.csv",
    "ranking": "cheapest_viable_provider_ranking.csv",
    "key_inventory": "provider_key_inventory.csv",
    "probe_plan": "provider_probe_plan.csv",
    "probe_results": "provider_probe_results.csv",
    "cost_value": "provider_cost_value_report.csv",
    "env_setup": "provider_env_var_setup_commands.ps1",
    "acq_decision": "provider_acquisition_decision.json",
    "signal_unlock": "signal_family_unlock_map.csv",
    "next_plan": "phase8p_next_plan.json",
}


class _Paths:
    def __init__(self, out_dir: Optional[Path] = None, data_dir: Optional[Path] = None,
                 phase8n_dir: Optional[Path] = None):
        self.out = Path(out_dir) if out_dir else _OUT_DIR
        self.data = Path(data_dir) if data_dir else _DATA_ROOT
        self.phase8n = Path(phase8n_dir) if phase8n_dir else _PHASE8N_DIR
        for key, name in _ARTIFACTS.items():
            setattr(self, key, self.out / name)


# --------------------------------------------------------------------------- #
# The six critical alpha families (inherited verbatim from Phase 8-N).
# --------------------------------------------------------------------------- #
CRITICAL_FAMILIES = (
    "earnings_surprises", "earnings_calendar", "analyst_estimates",
    "analyst_recommendations", "analyst_price_targets", "ratings_grades_consensus",
)
_EARNINGS_FAMILIES = ("earnings_surprises", "earnings_calendar")
_ANALYST_FAMILIES = ("analyst_estimates", "analyst_recommendations",
                     "analyst_price_targets", "ratings_grades_consensus")
DEFAULT_MIN_TICKERS_TO_SCORE = 20

# --------------------------------------------------------------------------- #
# Provider registry. env_var is the ONLY source of each provider's key. The
# current-FMP and FMP-upgrade rows share FMP_API_KEY (an upgrade re-uses the key).
# Free sources (SEC EDGAR / FINRA / GDELT) need no key but do NOT serve the
# earnings-surprise / analyst families, so they are recorded as not-applicable here.
# --------------------------------------------------------------------------- #
PROV_FMP = "FMP (current plan)"
PROV_FMP_UPGRADE = "FMP (upgrade tier)"
PROV_AV = "Alpha Vantage"
PROV_FINNHUB = "Finnhub"
PROV_EODHD = "EODHD"

# Secret query-parameter names per probe host (stripped from every persisted URL).
_SECRET_PARAMS = {"apikey", "token", "api_token", "api-token"}

_PROVIDERS: Tuple[Dict, ...] = (
    {"provider": PROV_FMP, "env_var": "FMP_API_KEY", "is_free": False,
     "cost_tier": "current plan (already held)", "monthly_cost": "$0 (already held)",
     "host": "financialmodelingprep.com",
     "note": "current plan - Phase 8-N proved it is entitlement-PARTIAL on every critical "
             "family (8/20 covered, ~64-75% 402-blocked)."},
    {"provider": PROV_FMP_UPGRADE, "env_var": "FMP_API_KEY", "is_free": False,
     "cost_tier": "Premium", "monthly_cost": "~$69/mo (Premium)", "host": "financialmodelingprep.com",
     "note": "one key unlocks all six families, but it is the MOST EXPENSIVE route and is only "
             "justified if the cheaper Alpha Vantage + Finnhub combo proves insufficient. "
             "Ultimate tier is NOT justified."},
    {"provider": PROV_AV, "env_var": "ALPHAVANTAGE_API_KEY", "is_free": False,
     "cost_tier": "free / Premium", "monthly_cost": "$0 (free 25 req/day) or ~$50/mo",
     "host": "www.alphavantage.co",
     "note": "EARNINGS / EARNINGS_CALENDAR give broad quarterly EPS actual/estimate/surprise + "
             "report dates - the cheapest route to the marquee earnings-surprise family."},
    {"provider": PROV_FINNHUB, "env_var": "FINNHUB_API_KEY", "is_free": False,
     "cost_tier": "free / paid history", "monthly_cost": "$0 (free 60 req/min) or ~$50/mo",
     "host": "finnhub.io",
     "note": "recommendation trends / price targets / EPS surprises - the cheapest route to the "
             "analyst families (some depth is premium)."},
    {"provider": PROV_EODHD, "env_var": "EODHD_API_KEY", "is_free": False,
     "cost_tier": "All-World / Fundamentals", "monthly_cost": "~$20-$80/mo",
     "host": "eodhd.com",
     "note": "calendar/earnings + fundamentals; only worth it if a single mid-priced bundle is "
             "preferred over the free AV+Finnhub combo."},
)
_PROVIDER_BY_NAME = {p["provider"]: p for p in _PROVIDERS}
PROBE_PROVIDERS = (PROV_AV, PROV_FINNHUB, PROV_EODHD)  # only non-FMP, key-gated providers

# --------------------------------------------------------------------------- #
# Per-(family, provider) capability knowledge (offline; costs are public list-price
# RANGES for comparison only). expected_coverage in {full, broad, partial, none}.
# pit = supports point-in-time backtest (a per-event availability date). hist =
# supports sufficient multi-year history. mapping = endpoint mapping status.
# --------------------------------------------------------------------------- #
def _cap(coverage, cost, hist, pit, suff_hist, mapping, recommended):
    return {"expected_coverage": coverage, "cost_tier": cost, "supports_historical_data": hist,
            "supports_point_in_time_backtest": pit, "supports_sufficient_history": suff_hist,
            "endpoint_mapping_status": mapping, "recommended_for_family": recommended}


# mapping vocabulary
MAP_MAPPED = "mapped_in_repo"             # an adapter for this provider already exists
MAP_DOCUMENTED = "documented_not_mapped"  # public endpoint known; adapter must be added
MAP_PARTIAL = "partial_mapping"
MAP_NONE = "no_mapping"

# The cheapest provider that SOLVES each critical family (drives the ranking + decision).
# earnings_* -> Alpha Vantage (free EARNINGS); analyst_* -> Finnhub (free rec-trends).
_FAMILY_CAPABILITY: Dict[str, Dict[str, Dict]] = {
    "earnings_surprises": {
        PROV_FMP: _cap("partial", "current", "yes", "yes", "partial", MAP_MAPPED, False),
        PROV_FMP_UPGRADE: _cap("full", "~$69/mo", "yes", "yes", "yes", MAP_MAPPED, False),
        PROV_AV: _cap("broad", "$0 free / ~$50", "yes", "yes", "yes", MAP_DOCUMENTED, True),
        PROV_FINNHUB: _cap("broad", "$0 free / ~$50", "yes", "yes", "partial", MAP_DOCUMENTED, False),
        PROV_EODHD: _cap("broad", "~$20-$80/mo", "yes", "yes", "yes", MAP_DOCUMENTED, False),
    },
    "earnings_calendar": {
        PROV_FMP: _cap("partial", "current", "yes", "yes", "partial", MAP_MAPPED, False),
        PROV_FMP_UPGRADE: _cap("full", "~$69/mo", "yes", "yes", "yes", MAP_MAPPED, False),
        PROV_AV: _cap("broad", "$0 free / ~$50", "yes", "yes", "yes", MAP_DOCUMENTED, True),
        PROV_FINNHUB: _cap("broad", "$0 free / ~$50", "yes", "yes", "partial", MAP_DOCUMENTED, False),
        PROV_EODHD: _cap("broad", "~$20-$80/mo", "yes", "yes", "yes", MAP_DOCUMENTED, False),
    },
    "analyst_estimates": {
        PROV_FMP: _cap("partial", "current", "yes", "partial", "partial", MAP_MAPPED, False),
        PROV_FMP_UPGRADE: _cap("full", "~$69/mo", "yes", "yes", "yes", MAP_MAPPED, False),
        PROV_AV: _cap("none", "n/a", "no", "no", "no", MAP_NONE, False),
        PROV_FINNHUB: _cap("partial", "$0 free / ~$50", "yes", "partial", "partial",
                           MAP_DOCUMENTED, True),
        PROV_EODHD: _cap("broad", "~$20-$80/mo", "yes", "partial", "yes", MAP_DOCUMENTED, False),
    },
    "analyst_recommendations": {
        PROV_FMP: _cap("partial", "current", "yes", "yes", "partial", MAP_MAPPED, False),
        PROV_FMP_UPGRADE: _cap("full", "~$69/mo", "yes", "yes", "yes", MAP_MAPPED, False),
        PROV_AV: _cap("none", "n/a", "no", "no", "no", MAP_NONE, False),
        PROV_FINNHUB: _cap("broad", "$0 free / ~$50", "yes", "yes", "partial", MAP_DOCUMENTED, True),
        PROV_EODHD: _cap("partial", "~$20-$80/mo", "partial", "partial", "partial",
                         MAP_DOCUMENTED, False),
    },
    "analyst_price_targets": {
        PROV_FMP: _cap("partial", "current", "yes", "yes", "partial", MAP_MAPPED, False),
        PROV_FMP_UPGRADE: _cap("full", "~$69/mo", "yes", "yes", "yes", MAP_MAPPED, False),
        PROV_AV: _cap("none", "n/a", "no", "no", "no", MAP_NONE, False),
        PROV_FINNHUB: _cap("broad", "$0 free / ~$50 (history premium)", "yes", "yes", "partial",
                           MAP_DOCUMENTED, True),
        PROV_EODHD: _cap("partial", "~$20-$80/mo", "partial", "partial", "partial",
                         MAP_DOCUMENTED, False),
    },
    "ratings_grades_consensus": {
        PROV_FMP: _cap("partial", "current", "yes", "yes", "partial", MAP_MAPPED, False),
        PROV_FMP_UPGRADE: _cap("full", "~$69/mo", "yes", "yes", "yes", MAP_MAPPED, False),
        PROV_AV: _cap("none", "n/a", "no", "no", "no", MAP_NONE, False),
        PROV_FINNHUB: _cap("partial", "$0 free / ~$50", "yes", "yes", "partial", MAP_DOCUMENTED, True),
        PROV_EODHD: _cap("partial", "~$20-$80/mo", "partial", "partial", "partial",
                         MAP_DOCUMENTED, False),
    },
}

# The cheapest provider recommended to SOLVE each family (single source of truth).
_FAMILY_RECOMMENDED_PROVIDER: Dict[str, str] = {
    "earnings_surprises": PROV_AV,
    "earnings_calendar": PROV_AV,
    "analyst_estimates": PROV_FINNHUB,
    "analyst_recommendations": PROV_FINNHUB,
    "analyst_price_targets": PROV_FINNHUB,
    "ratings_grades_consensus": PROV_FINNHUB,
}

# The six provider-expanded signal hypotheses (mirror Phase 8-N) -> required family.
_SIGNALS: Tuple[Dict, ...] = (
    {"signal_id": "S8N-EARNSURP-RATES",
     "name": "positive_earnings_surprise x rates_sensitivity",
     "required_family": "earnings_surprises"},
    {"signal_id": "S8N-EARNSURP-SECTOR",
     "name": "positive_earnings_surprise x sector_leadership",
     "required_family": "earnings_surprises"},
    {"signal_id": "S8N-ESTREV-RATES",
     "name": "analyst_estimate_revision x sensitivity",
     "required_family": "analyst_estimates"},
    {"signal_id": "S8N-RECCHG-SECTOR",
     "name": "analyst_recommendation_change x sector_leadership",
     "required_family": "analyst_recommendations"},
    {"signal_id": "S8N-PTREV-SENS",
     "name": "price_target_revision x sensitivity_cohort",
     "required_family": "analyst_price_targets"},
    {"signal_id": "S8N-GRADES-SENS",
     "name": "ratings_grades_consensus x sensitivity_cohort",
     "required_family": "ratings_grades_consensus"},
)

# --------------------------------------------------------------------------- #
# Bounded live probe (one representative endpoint per keyed provider, 1 ticker).
# Each carries the secret query-parameter NAME so the redactor strips it.
# --------------------------------------------------------------------------- #
_PROBE_ENDPOINTS: Dict[str, Dict] = {
    PROV_AV: {"family": "earnings_surprises", "host": "www.alphavantage.co",
              "url": "https://www.alphavantage.co/query?function=EARNINGS&symbol={symbol}",
              "secret_param": "apikey",
              "doc": "alphavantage.co/documentation : EARNINGS (quarterly EPS actual/estimate)"},
    PROV_FINNHUB: {"family": "analyst_recommendations", "host": "finnhub.io",
                   "url": "https://finnhub.io/api/v1/stock/recommendation?symbol={symbol}",
                   "secret_param": "token",
                   "doc": "finnhub.io/docs/api : /stock/recommendation (buy/hold/sell trends)"},
    PROV_EODHD: {"family": "earnings_calendar", "host": "eodhd.com",
                 "url": "https://eodhd.com/api/calendar/earnings?fmt=json",
                 "secret_param": "api_token",
                 "doc": "eodhd.com/financial-apis : /calendar/earnings"},
}
PROBE_TICKER = "AAPL"
PROBE_TIMEOUT_SECONDS = 30.0
PROBE_MIN_SLEEP_SECONDS = 0.30

# --------------------------------------------------------------------------- #
# Decision vocabulary (exactly the eight from the brief).
# --------------------------------------------------------------------------- #
DEC_AV_FIRST = "ALPHA_VANTAGE_FIRST"
DEC_FINNHUB_FIRST = "FINNHUB_FIRST"
DEC_EODHD_FIRST = "EODHD_FIRST"
DEC_FMP_UPGRADE = "FMP_UPGRADE_REQUIRED"
DEC_MIXED = "MIXED_PROVIDER_STRATEGY_REQUIRED"
DEC_BLOCKED_NO_KEY = "BLOCKED_MISSING_ALTERNATIVE_PROVIDER_KEY"
DEC_NO_CHEAP = "NO_PROVIDER_CAN_SOLVE_CHEAPLY"
DEC_ERROR = "ERROR"
ALLOWED_DECISIONS = (
    DEC_AV_FIRST, DEC_FINNHUB_FIRST, DEC_EODHD_FIRST, DEC_FMP_UPGRADE, DEC_MIXED,
    DEC_BLOCKED_NO_KEY, DEC_NO_CHEAP, DEC_ERROR,
)
# Non-terminal marker for the (unexpected) case where Phase 8-N reported NO blocked family:
# there is no provider problem to solve. Never one of the eight terminal decisions.
DEC_FMP_SUFFICIENT = "FMP_SUFFICIENT_NO_ALTERNATIVE_NEEDED"


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


def _read_json_file(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _read_csv_file(path: Path) -> List[Dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


# --------------------------------------------------------------------------- #
# Key handling (presence ONLY; the value is never read into any artifact).
# --------------------------------------------------------------------------- #
def key_present(env_var: str) -> bool:
    """True iff ``env_var`` is set and non-empty. The value is never returned."""
    if not env_var:
        return False
    v = os.environ.get(env_var)
    return isinstance(v, str) and bool(v.strip())


# --------------------------------------------------------------------------- #
# URL redaction (generic; strips every secret query parameter entirely).
# --------------------------------------------------------------------------- #
REDACTED_KEY_PLACEHOLDER = "<API_KEY_REDACTED>"


def redact_url(url: str) -> str:
    """Return a persist-safe URL: every secret query parameter (apikey/token/api_token)
    is stripped ENTIRELY and a placeholder token is appended, so the result never contains
    the literal substring ``apikey=`` (or a raw key)."""
    try:
        parts = urllib.parse.urlsplit(url)
    except (ValueError, AttributeError):
        return str(url)
    pairs = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in _SECRET_PARAMS]
    query = urllib.parse.urlencode(pairs)
    query = (query + "&" + REDACTED_KEY_PLACEHOLDER) if query else REDACTED_KEY_PLACEHOLDER
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, query, parts.fragment))


# --------------------------------------------------------------------------- #
# Phase 8-N ingestion (read-only; tolerant of a missing/offline-regenerated set).
# --------------------------------------------------------------------------- #
def read_phase8n(phase8n_dir: Path) -> Dict:
    """Read the Phase 8-N committed-safe artifacts and extract the coverage / entitlement /
    block facts Phase 8-O needs. Never reads raw payloads. Returns a dict with ``found``."""
    report = _read_json_file(phase8n_dir / "phase8n_fmp_critical_data_backfill_signal_expansion.json")
    matrix = _read_csv_file(phase8n_dir / "fmp_family_entitlement_matrix.csv")
    block = _read_csv_file(phase8n_dir / "fmp_subscription_block_pattern_report.csv")
    upgrade = _read_csv_file(phase8n_dir / "fmp_provider_upgrade_decision.csv")

    found = report is not None or bool(matrix)
    min_tickers = DEFAULT_MIN_TICKERS_TO_SCORE
    coverage: Dict[str, int] = {}
    entitlement: Dict[str, str] = {}
    block_fraction: Dict[str, float] = {}
    broadly_blocked: Dict[str, bool] = {}
    pattern: Dict[str, str] = {}

    if isinstance(report, dict):
        min_tickers = int(report.get("min_tickers_to_score", DEFAULT_MIN_TICKERS_TO_SCORE))
        coverage = {f: int(v) for f, v in (report.get("coverage_counts") or {}).items()}
        entitlement = dict(report.get("family_entitlement") or {})
        block_fraction = {f: float(v) for f, v in (report.get("family_block_fraction") or {}).items()}

    for row in matrix:
        fam = row.get("endpoint_family", "")
        if not fam:
            continue
        coverage.setdefault(fam, int(row.get("coverage") or 0))
        entitlement.setdefault(fam, row.get("entitlement", "UNKNOWN"))
        block_fraction.setdefault(fam, float(row.get("block_fraction") or 0.0)
                                  if row.get("block_fraction") else 0.0)
        broadly_blocked[fam] = str(row.get("broadly_blocked", "")).strip().lower() == "true"
    for row in block:
        fam = row.get("endpoint_family", "")
        if fam:
            pattern[fam] = row.get("pattern", "")

    # Which critical families are blocked (entitlement insufficient on the current FMP plan).
    missing: List[str] = []
    if isinstance(report, dict) and report.get("missing_blocked_families"):
        missing = [f for f in report["missing_blocked_families"] if f in CRITICAL_FAMILIES]
    else:
        for fam in CRITICAL_FAMILIES:
            ent = entitlement.get(fam, "UNKNOWN")
            cov = coverage.get(fam, 0)
            if broadly_blocked.get(fam) or (ent in ("PARTIAL", "BLOCKED") and cov < min_tickers):
                missing.append(fam)

    upgrade_overall = ""
    for row in upgrade:
        if row.get("endpoint_family") == "__overall__":
            upgrade_overall = row.get("recommended_action", "")

    fmp_decision = report.get("decision", "") if isinstance(report, dict) else ""
    return {
        "found": found,
        "phase8n_dir": _rel(phase8n_dir),
        "min_tickers_to_score": min_tickers,
        "coverage": coverage,
        "entitlement": entitlement,
        "block_fraction": block_fraction,
        "broadly_blocked": broadly_blocked,
        "pattern": pattern,
        "missing_families": missing,
        "fmp_upgrade_decision": upgrade_overall,
        "fmp_decision": fmp_decision,
        "universe_size": int(report.get("universe_size", 0)) if isinstance(report, dict) else 0,
        "requests_remaining": int(report.get("requests_remaining", 0))
        if isinstance(report, dict) else 0,
    }


# --------------------------------------------------------------------------- #
# Strategy: map blocked families -> the cheapest provider(s) that solve them.
# --------------------------------------------------------------------------- #
def plan_provider_strategy(missing_families: List[str]) -> Dict:
    """Decide, from the blocked families, the cheapest provider to try FIRST, the second
    provider only if needed, and the strategic recommendation. earnings_* -> Alpha Vantage,
    analyst_* -> Finnhub. FMP Ultimate is never recommended; an FMP upgrade is recommended
    only if a blocked family has NO cheaper provider (never the case for these six)."""
    blocked = [f for f in CRITICAL_FAMILIES if f in set(missing_families)]
    blocked_earnings = [f for f in blocked if f in _EARNINGS_FAMILIES]
    blocked_analyst = [f for f in blocked if f in _ANALYST_FAMILIES]

    # Each blocked family maps to its cheapest solving provider.
    providers_needed: List[str] = []
    family_to_provider: Dict[str, str] = {}
    unsolved: List[str] = []
    for fam in blocked:
        prov = _FAMILY_RECOMMENDED_PROVIDER.get(fam)
        if prov is None:
            unsolved.append(fam)
            continue
        family_to_provider[fam] = prov
        if prov not in providers_needed:
            providers_needed.append(prov)

    # Cheapest-first priority: earnings (Alpha Vantage, FREE) is the marquee alpha and is
    # tried before the analyst side (Finnhub, FREE trends).
    ordered: List[str] = []
    if blocked_earnings:
        ordered.append(PROV_AV)
    if blocked_analyst:
        ordered.append(PROV_FINNHUB)
    ordered = [p for p in ordered if p in providers_needed] or providers_needed

    if not blocked:
        strategy = DEC_FMP_SUFFICIENT
    elif unsolved:
        strategy = DEC_NO_CHEAP
    elif len(providers_needed) >= 2:
        strategy = DEC_MIXED
    elif providers_needed == [PROV_AV]:
        strategy = DEC_AV_FIRST
    elif providers_needed == [PROV_FINNHUB]:
        strategy = DEC_FINNHUB_FIRST
    elif providers_needed == [PROV_EODHD]:
        strategy = DEC_EODHD_FIRST
    else:
        strategy = DEC_MIXED

    first = ordered[0] if ordered else ""
    second = ordered[1] if len(ordered) > 1 else ""
    return {
        "blocked": blocked,
        "blocked_earnings": blocked_earnings,
        "blocked_analyst": blocked_analyst,
        "providers_needed": providers_needed,
        "family_to_provider": family_to_provider,
        "unsolved_families": unsolved,
        "strategy": strategy,
        "cheapest_first_provider": first,
        "second_provider": second,
    }


def derive_decision(strategy: Dict, key_inventory: Dict[str, bool]) -> Tuple[str, str]:
    """Apply the env-key gate to the strategy. If the recommended alternative providers have
    NO key present, the terminal decision is BLOCKED_MISSING_ALTERNATIVE_PROVIDER_KEY (the
    user must set the cheapest provider's key first); the strategy still names what to try.
    Returns (decision, gate_reason)."""
    strat = strategy["strategy"]
    if strat == DEC_FMP_SUFFICIENT:
        return (DEC_FMP_SUFFICIENT, "Phase 8-N reported no blocked critical family; no "
                "alternative provider is needed - collect a wider FMP batch instead.")
    if strat == DEC_NO_CHEAP:
        return (DEC_NO_CHEAP, "at least one blocked family has no cheap provider that solves "
                "it; an FMP upgrade or a specialist source is required.")
    needed = strategy["providers_needed"]
    any_needed_key = any(key_present_for(p, key_inventory) for p in needed)
    if not any_needed_key:
        first = strategy["cheapest_first_provider"]
        env = _PROVIDER_BY_NAME.get(first, {}).get("env_var", "")
        return (DEC_BLOCKED_NO_KEY,
                "no alternative-provider key is present in the environment; set %s (%s) first, "
                "then re-run --live to probe." % (first, env))
    return (strat, "recommended provider key(s) present; the strategy can be probed/activated.")


def key_present_for(provider: str, key_inventory: Dict[str, bool]) -> bool:
    env = _PROVIDER_BY_NAME.get(provider, {}).get("env_var", "")
    return bool(key_inventory.get(env, False))


# --------------------------------------------------------------------------- #
# Bounded live probe (only keyed alternative providers; 1 ticker; 1 endpoint).
# --------------------------------------------------------------------------- #
Transport = Callable[[str], object]


class ProbeError(Exception):
    def __init__(self, message: str, *, status_code: Optional[int] = None,
                 error_type: str = "error") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type


def _host_of(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).netloc
    except ValueError:
        return ""


def _live_get(url: str, allowed_host: str) -> object:
    """One bounded live GET. The key-bearing ``url`` is used transiently and never persisted;
    errors are sanitized so a key cannot leak through an exception string."""
    if _host_of(url) != allowed_host:
        raise ProbeError("refusing non-allowlisted host", error_type="host_blocked")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "paper-trader-research-phase8o/1.0",
                                                   "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_SECONDS) as resp:  # nosec - allowlisted
            raw = resp.read()
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            raise ProbeError("non-JSON response: %s" % exc, error_type="bad_response")
    except urllib.error.HTTPError as exc:
        raise ProbeError("provider returned HTTP %s" % getattr(exc, "code", 0),
                         status_code=getattr(exc, "code", None), error_type="http_error")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProbeError("network error: %s" % type(exc).__name__, error_type="network_error")


def _build_probe_url(provider: str) -> str:
    spec = _PROBE_ENDPOINTS[provider]
    base = spec["url"].replace("{symbol}", PROBE_TICKER)
    env = _PROVIDER_BY_NAME[provider]["env_var"]
    key = os.environ.get(env, "") or ""
    sep = "&" if "?" in base else "?"
    return "%s%s%s=%s" % (base, sep, spec["secret_param"], urllib.parse.quote(key))


def _classify_probe(payload) -> Tuple[str, int, str]:
    """Map a 200 payload to (result, rows, note). Alpha Vantage returns a rate-limit/info
    envelope as a dict with a 'Note'/'Information' key even on HTTP 200."""
    if isinstance(payload, list):
        n = len([r for r in payload if isinstance(r, (dict, list))]) or len(payload)
        return ("ok", n, "list payload") if payload else ("empty", 0, "empty list")
    if isinstance(payload, dict):
        low = {k.lower(): k for k in payload.keys()}
        for marker in ("note", "information", "error message"):
            if marker in low:
                return ("rate_limited_or_info", 0,
                        "provider returned a %s envelope (rate-limit/entitlement)" % marker)
        # Count the largest list field (e.g. AV "quarterlyEarnings").
        best = 0
        for v in payload.values():
            if isinstance(v, list):
                best = max(best, len(v))
        return ("ok", best, "dict payload") if payload else ("empty", 0, "empty dict")
    return ("empty", 0, "no payload")


def run_probes(providers: List[str], key_inventory: Dict[str, bool], live: bool,
               transport: Optional[Transport], data_root: Path,
               verbose: bool = False) -> List[Dict]:
    """Probe ONLY the given providers whose key is present (or via an injected transport in
    tests). Bounded: one ticker, one representative endpoint each. Persists verbatim payloads
    under the gitignored research/data/<provider>/raw/ tree. Never writes a key/keyed URL."""
    results: List[Dict] = []
    for provider in providers:
        if provider not in _PROBE_ENDPOINTS:
            continue
        spec = _PROBE_ENDPOINTS[provider]
        env = _PROVIDER_BY_NAME[provider]["env_var"]
        present = bool(key_inventory.get(env, False))
        redacted = redact_url(spec["url"].replace("{symbol}", PROBE_TICKER)
                              + "&%s=" % spec["secret_param"])
        can_probe = transport is not None or (bool(live) and present)
        if not can_probe:
            results.append(_probe_row(provider, spec["family"], env, present, False, "",
                                      "NOT_PROBED_NO_KEY", 0,
                                      "no %s in this session; cannot probe" % env, redacted))
            continue
        try:
            if transport is not None:
                payload = transport(spec["url"].replace("{symbol}", PROBE_TICKER))
            else:
                payload = _live_get(_build_probe_url(provider), spec["host"])
            result, rows, note = _classify_probe(payload)
            status = "PROBED_OK" if result == "ok" else "PROBED_%s" % result.upper()
            # Persist verbatim payload under the gitignored provider raw tree. The provider
            # data dir is force-gitignored first so no paid payload can ever be staged.
            slug = provider.split()[0].lower()
            _ensure_provider_gitignore(data_root / slug, provider)
            raw_path = data_root / slug / "raw" / ("%s_%s.json" % (spec["family"], PROBE_TICKER))
            if not raw_path.is_file():
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                with open(raw_path, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, indent=2)
                    fh.write("\n")
            results.append(_probe_row(provider, spec["family"], env, present, True, 200,
                                      status, rows, note, redacted))
        except ProbeError as exc:
            code = getattr(exc, "status_code", None)
            etype = getattr(exc, "error_type", "error")
            status = "PROBE_BLOCKED" if code in (401, 402, 403) else "PROBE_ERROR"
            results.append(_probe_row(provider, spec["family"], env, present, True,
                                      "" if code is None else code, status, 0,
                                      "%s (%s)" % (str(exc), etype), redacted))
        if verbose:
            print("  probe %-16s -> %s" % (provider, results[-1]["status"]))
        if transport is None:
            time.sleep(PROBE_MIN_SLEEP_SECONDS)
    return results


def _ensure_provider_gitignore(provider_dir: Path, provider: str) -> None:
    """Force-gitignore an alternative provider's local data dir (belt-and-braces: ignore
    EVERYTHING except the .gitignore itself), so licensed probe payloads under raw/ and
    normalized/ can never be staged. Mirrors research/data/fmp/.gitignore."""
    provider_dir.mkdir(parents=True, exist_ok=True)
    gi = provider_dir / ".gitignore"
    if gi.is_file():
        return
    lines = [
        "# Phase 8-O - LOCAL paid %s data. DO NOT COMMIT." % provider,
        "#",
        "# A bounded Phase 8-O probe (run_phase8o_cheapest_provider_selection.py --live) may",
        "# write licensed provider payloads here:",
        "#   raw/        verbatim provider JSON responses (paid data)",
        "#   normalized/ flattened point-in-time CSVs derived from the paid data",
        "#",
        "# These are license-restricted provider responses and must never enter Git. Only",
        "# coverage/decision metadata (never payloads, never the API key) is written to the",
        "# committed artifacts under research/output/phase8o_cheapest_provider_selection/.",
        "raw/",
        "normalized/",
        "",
        "# Belt-and-braces: ignore everything here EXCEPT this .gitignore.",
        "*",
        "!.gitignore",
        "",
    ]
    with open(gi, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _probe_row(provider, family, env, key_present_, probed, http_status, status, rows, note,
               redacted) -> Dict:
    return {"provider": provider, "family_probed": family, "env_var": env,
            "key_present": key_present_, "probed": probed, "http_status": http_status,
            "status": status, "rows": rows, "note": note, "endpoint_redacted": redacted}


# --------------------------------------------------------------------------- #
# Secret leak scan over the committed artifacts written this run.
# --------------------------------------------------------------------------- #
def _scan_for_leaks(paths: Sequence[Path]) -> Tuple[bool, int]:
    marker = "api" + "key="
    # Read present key VALUES only to confirm none leaked (never printed/persisted).
    secrets = [os.environ.get(p["env_var"], "") for p in _PROVIDERS]
    secrets = [s.strip() for s in secrets if isinstance(s, str) and s.strip()]
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
        if any(s and s in text for s in secrets):
            clean = False
    return clean, scanned


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
def run(live: bool = False, transport: Optional[Transport] = None,
        out_dir: Optional[Path] = None, data_dir: Optional[Path] = None,
        phase8n_dir: Optional[Path] = None, verbose: bool = True) -> Dict:
    """Run the Phase 8-O cheapest-viable-provider selection.

    Default (``live=False``, no ``transport``): OFFLINE - reads the Phase 8-N artifacts +
    static provider knowledge, inventories provider keys (presence only), and writes the
    decision with NO network call. A keyed provider is probed only with --live AND its key
    present, OR via an injected ``transport`` (tests). Returns the main report dict.
    """
    P = _Paths(out_dir, data_dir, phase8n_dir)
    P.out.mkdir(parents=True, exist_ok=True)

    s8n = read_phase8n(P.phase8n)
    missing_families = s8n["missing_families"]
    strategy = plan_provider_strategy(missing_families)

    # ----- provider key inventory (PRESENCE ONLY; value never read into an artifact) -----
    key_inventory: Dict[str, bool] = {}
    inv_rows = []
    for p in _PROVIDERS:
        env = p["env_var"]
        present = key_present(env)
        key_inventory[env] = present
        served = sorted(fam for fam in CRITICAL_FAMILIES
                        if _FAMILY_CAPABILITY.get(fam, {}).get(p["provider"], {})
                        .get("expected_coverage", "none") not in ("none",))
        inv_rows.append([env, p["provider"], present, False, ";".join(served),
                         "presence-only; value never read or printed"])
    _write_csv(P.key_inventory,
               ["env_var", "provider", "key_present", "value_read", "critical_families_served",
                "note"], inv_rows)

    decision, gate_reason = derive_decision(strategy, key_inventory)

    # ----- Phase 8-N FMP coverage summary -----
    _write_csv(P.fmp_coverage,
               ["critical_family", "entitlement", "coverage", "min_tickers_to_score",
                "block_fraction", "block_pattern", "broadly_blocked"],
               [[fam, s8n["entitlement"].get(fam, "UNKNOWN"), s8n["coverage"].get(fam, 0),
                 s8n["min_tickers_to_score"], s8n["block_fraction"].get(fam, 0.0),
                 s8n["pattern"].get(fam, ""), s8n["broadly_blocked"].get(fam, False)]
                for fam in CRITICAL_FAMILIES])

    # ----- critical-family blocker summary (which families are blocked and why) -----
    blocker_rows = []
    for fam in CRITICAL_FAMILIES:
        blocked = fam in strategy["blocked"]
        prov = strategy["family_to_provider"].get(fam, "")
        why = ("entitlement %s on current FMP plan; coverage %d/%d; %.0f%% of attempts "
               "402-blocked (pattern %s)"
               % (s8n["entitlement"].get(fam, "UNKNOWN"), s8n["coverage"].get(fam, 0),
                  s8n["min_tickers_to_score"], 100.0 * s8n["block_fraction"].get(fam, 0.0),
                  s8n["pattern"].get(fam, "n/a"))) if blocked else "entitled / sufficient on FMP"
        blocker_rows.append([fam, blocked, why, prov,
                             key_present_for(prov, key_inventory) if prov else False])
    _write_csv(P.blocker_summary,
               ["critical_family", "blocked_on_fmp", "why_blocked",
                "recommended_unlock_provider", "unlock_key_present"], blocker_rows)

    # ----- provider decision matrix (per provider x family) -----
    dm_rows = []
    for fam in CRITICAL_FAMILIES:
        for p in _PROVIDERS:
            cap = _FAMILY_CAPABILITY.get(fam, {}).get(p["provider"])
            if cap is None:
                continue
            dm_rows.append([
                p["provider"], fam, p["env_var"], key_inventory.get(p["env_var"], False),
                cap["expected_coverage"], cap["cost_tier"], cap["supports_historical_data"],
                cap["supports_point_in_time_backtest"], cap["supports_sufficient_history"],
                cap["endpoint_mapping_status"], cap["recommended_for_family"],
            ])
    _write_csv(P.decision_matrix,
               ["provider", "data_family", "current_key_env_var", "key_present",
                "expected_coverage", "cost_tier_if_known", "supports_historical_data",
                "supports_point_in_time_backtest", "supports_sufficient_history",
                "endpoint_mapping_status", "recommended_for_family"], dm_rows)

    # ----- cheapest viable provider ranking (try-order across the blocked families) -----
    ranking = _build_ranking(strategy, key_inventory)
    _write_csv(P.ranking,
               ["rank", "provider", "env_var", "key_present", "families_it_unlocks",
                "approx_monthly_cost", "free_tier", "try_order", "rationale"],
               [[r["rank"], r["provider"], r["env_var"], r["key_present"],
                 ";".join(r["families"]), r["monthly_cost"], r["free_tier"], r["try_order"],
                 r["rationale"]] for r in ranking])

    # ----- provider probe plan (always written; network-free) -----
    plan_rows = []
    for provider in PROBE_PROVIDERS:
        spec = _PROBE_ENDPOINTS[provider]
        env = _PROVIDER_BY_NAME[provider]["env_var"]
        present = key_inventory.get(env, False)
        enabled = bool(present and (provider in strategy["providers_needed"]))
        plan_rows.append([
            provider, spec["family"], env, present, enabled,
            redact_url(spec["url"].replace("{symbol}", PROBE_TICKER) + "&%s=" % spec["secret_param"]),
            spec["doc"],
            "probe runs only with --live AND %s present (or an injected transport in tests)" % env])
    _write_csv(P.probe_plan,
               ["provider", "family", "env_var", "key_present", "probe_enabled",
                "endpoint_redacted", "doc", "note"], plan_rows)

    # ----- bounded probe of ONLY the needed, keyed alternative providers -----
    probe_targets = [p for p in strategy["providers_needed"] if p in PROBE_PROVIDERS]
    if transport is not None and not probe_targets:
        probe_targets = list(PROBE_PROVIDERS)  # tests exercise the probe path explicitly
    probe_results = run_probes(probe_targets, key_inventory, live, transport, P.data, verbose)
    _write_csv(P.probe_results,
               ["provider", "family_probed", "env_var", "key_present", "probed", "http_status",
                "status", "rows", "note", "endpoint_redacted"],
               [[r["provider"], r["family_probed"], r["env_var"], r["key_present"], r["probed"],
                 r["http_status"], r["status"], r["rows"], r["note"], r["endpoint_redacted"]]
                for r in probe_results])
    any_probe_executed = any(r["probed"] for r in probe_results)

    # ----- provider cost / value report -----
    _write_csv(P.cost_value,
               ["provider", "env_var", "approx_monthly_cost", "critical_families_covered",
                "covers_count", "value_note"],
               _cost_value_rows(key_inventory))

    # ----- provider env-var setup commands (PLACEHOLDER only; NEVER a real key) -----
    _write_env_setup_commands(P.env_setup, strategy, decision)

    # ----- signal-family unlock map -----
    su_rows = []
    for sig in _SIGNALS:
        fam = sig["required_family"]
        blocked = fam in strategy["blocked"]
        prov = strategy["family_to_provider"].get(fam, "")
        present = key_present_for(prov, key_inventory) if prov else False
        status = ("UNLOCK_VIA_%s_NEEDS_KEY" % _slug(prov) if blocked and not present
                  else "UNLOCK_VIA_%s_KEY_PRESENT" % _slug(prov) if blocked and present
                  else "FMP_SUFFICIENT")
        su_rows.append([sig["signal_id"], sig["name"], fam, blocked, prov, present, status])
    _write_csv(P.signal_unlock,
               ["signal_id", "signal_name", "required_family", "blocked_on_fmp",
                "unlock_provider", "unlock_key_present", "unlock_status"], su_rows)

    # ----- secret leak scan over everything written so far -----
    written = [p for p in P.out.glob("*") if p.is_file()]
    leak_clean, scanned = _scan_for_leaks(written)

    # ----- the structured acquisition decision -----
    first = strategy["cheapest_first_provider"]
    second = strategy["second_provider"]
    next_cmd = _next_acquisition_command(strategy, decision, key_inventory)
    acq = {
        "phase": PHASE,
        "decision": decision,
        "decision_allowed_values": list(ALLOWED_DECISIONS),
        "decision_is_terminal": decision in ALLOWED_DECISIONS,
        "gate_reason": gate_reason,
        "recommended_strategy": strategy["strategy"],
        "fmp_coverage_conclusion": (
            "Current FMP plan is INSUFFICIENT for broad signal research: all six critical "
            "families are entitlement-PARTIAL (Phase 8-N: ~8/%d tickers, most attempts "
            "402-blocked). %s" % (s8n["min_tickers_to_score"], s8n["fmp_decision"] or "")),
        "cheapest_provider_to_try_first": first,
        "first_provider_env_var": _PROVIDER_BY_NAME.get(first, {}).get("env_var", ""),
        "first_provider_unlocks": [f for f in strategy["blocked"]
                                   if strategy["family_to_provider"].get(f) == first],
        "second_provider_only_if_needed": second,
        "second_provider_env_var": _PROVIDER_BY_NAME.get(second, {}).get("env_var", ""),
        "second_provider_unlocks": [f for f in strategy["blocked"]
                                    if strategy["family_to_provider"].get(f) == second],
        "fmp_upgrade_justified": decision == DEC_FMP_UPGRADE,
        "fmp_upgrade_reason": (
            "NOT justified: every blocked family has a cheaper (free-tier) alternative - "
            "Alpha Vantage for earnings, Finnhub for analyst data. An FMP Premium upgrade "
            "(~$69/mo) is the fallback only if that free combo proves insufficient on "
            "coverage/history/PIT."),
        "fmp_ultimate_rejected": True,
        "fmp_ultimate_reason": (
            "Explicitly NOT justified: no evidence requires the Ultimate tier; the six "
            "critical families are each covered by a cheaper source, so paying for Ultimate "
            "would buy entitlement that free/low-cost providers already supply."),
        "env_vars_needed": sorted({_PROVIDER_BY_NAME[p]["env_var"]
                                   for p in strategy["providers_needed"]
                                   if p in _PROVIDER_BY_NAME}),
        "exact_next_command": next_cmd,
        "any_probe_executed": any_probe_executed,
        "preview_only": True, "orders_enabled": False, "automation_enabled": False,
        "broker_execution_enabled": False, "deployed": False, "committed": False,
    }
    _write_json(P.acq_decision, acq)

    # ----- Phase 8-P next plan -----
    next_plan = {
        "phase": "8-P",
        "title": "Alternative-Provider Controlled Earnings/Analyst Backfill",
        "depends_on_decision": decision,
        "objective": (
            "Activate the cheapest provider named here (%s first%s), add a secret-safe stdlib "
            "adapter mirroring research/providers/fmp_client.py, run a bounded point-in-time "
            "backfill of the blocked critical families, and re-score the six signal "
            "hypotheses once coverage reaches the %d-ticker minimum (no fabricated alpha)."
            % (first or "n/a", (" then %s" % second) if second else "",
               s8n["min_tickers_to_score"])),
        "next_command": next_cmd,
        "blocked_families": strategy["blocked"],
        "provider_order": [p for p in [first, second] if p],
        "do_not_default_to_fmp_ultimate": True,
        "min_tickers_to_score": s8n["min_tickers_to_score"],
        "preview_only": True, "orders_enabled": False, "automation_enabled": False,
        "broker_execution_enabled": False, "committed": False,
    }
    _write_json(P.next_plan, next_plan)

    # ----- main report -----
    report = {
        "phase": PHASE,
        "objective": ("Find and name the cheapest data-source path that gives enough coverage "
                      "for the critical alpha families after Phase 8-N proved the current FMP "
                      "plan insufficient; probe only providers whose key is already present."),
        "mode": "live_probe" if any_probe_executed else "offline_decision",
        "live_requested": bool(live),
        "phase8n_found": s8n["found"], "phase8n_dir": s8n["phase8n_dir"],
        "phase8n_fmp_decision": s8n["fmp_decision"],
        "phase8n_fmp_upgrade_decision": s8n["fmp_upgrade_decision"],
        "min_tickers_to_score": s8n["min_tickers_to_score"],
        "critical_family_coverage": {f: s8n["coverage"].get(f, 0) for f in CRITICAL_FAMILIES},
        "critical_family_entitlement": {f: s8n["entitlement"].get(f, "UNKNOWN")
                                        for f in CRITICAL_FAMILIES},
        "blocked_families": strategy["blocked"],
        "blocked_earnings_families": strategy["blocked_earnings"],
        "blocked_analyst_families": strategy["blocked_analyst"],
        "providers_compared": [p["provider"] for p in _PROVIDERS],
        "provider_key_inventory": key_inventory,
        "providers_needed": strategy["providers_needed"],
        "recommended_strategy": strategy["strategy"],
        "decision": decision, "decision_is_terminal": decision in ALLOWED_DECISIONS,
        "allowed_decisions": list(ALLOWED_DECISIONS),
        "gate_reason": gate_reason,
        "cheapest_provider_to_try_first": first,
        "second_provider_only_if_needed": second,
        "fmp_upgrade_justified": decision == DEC_FMP_UPGRADE,
        "fmp_ultimate_rejected": True,
        "env_vars_needed": acq["env_vars_needed"],
        "any_probe_executed": any_probe_executed,
        "recommended_next_command": next_cmd,
        "secret_safety_leak_scan_clean": leak_clean,
        "secret_safety_files_scanned": scanned,
        "outputs": {k: _rel(getattr(P, k)) for k in _ARTIFACTS},
        # Safety / provenance contract.
        "preview_only": True, "orders_enabled": False, "automation_enabled": False,
        "broker_execution_enabled": False, "production_replacement": False,
        "network_used": bool(any_probe_executed and transport is None),
        "paper_trader_touched": False, "gcp_touched": False, "deployed": False,
        "data_fabricated": False, "wrote_to_d_drive": False, "committed": False,
    }
    _write_json(P.report, report)
    return report


# --------------------------------------------------------------------------- #
# Ranking + cost/value + commands.
# --------------------------------------------------------------------------- #
def _slug(provider: str) -> str:
    return provider.split()[0].upper() if provider else "NONE"


def _build_ranking(strategy: Dict, key_inventory: Dict[str, bool]) -> List[Dict]:
    """Cheapest-first ranking of the providers that SOLVE the blocked families, plus the
    FMP-upgrade fallback. Free-tier earnings (Alpha Vantage) ranks above free-tier analyst
    (Finnhub); the FMP Premium upgrade ranks last (most expensive) and Ultimate is omitted."""
    rows: List[Dict] = []
    order: List[str] = []
    if strategy["cheapest_first_provider"]:
        order.append(strategy["cheapest_first_provider"])
    if strategy["second_provider"] and strategy["second_provider"] not in order:
        order.append(strategy["second_provider"])
    # EODHD as a single mid-priced bundle alternative if both earnings+analyst are blocked.
    if PROV_EODHD not in order and strategy["blocked"]:
        order.append(PROV_EODHD)
    if PROV_FMP_UPGRADE not in order and strategy["blocked"]:
        order.append(PROV_FMP_UPGRADE)

    for i, provider in enumerate(order):
        p = _PROVIDER_BY_NAME[provider]
        fams = [f for f in strategy["blocked"]
                if _FAMILY_CAPABILITY.get(f, {}).get(provider, {})
                .get("expected_coverage", "none") not in ("none",)]
        if provider == PROV_EODHD:
            try_order = "alternative_single_bundle"
            rationale = ("one mid-priced bundle that can cover both earnings and analyst "
                         "families; choose over AV+Finnhub only if a single paid source is "
                         "preferred")
        elif provider == PROV_FMP_UPGRADE:
            try_order = "last_resort_fallback"
            rationale = ("FMP Premium (~$69/mo) unlocks all six from the existing key, but is "
                         "the most expensive route - use only if the free AV+Finnhub combo is "
                         "insufficient. FMP Ultimate is NOT justified.")
        else:
            try_order = "try_first" if i == 0 else "try_second"
            rationale = p["note"]
        rows.append({
            "rank": i + 1, "provider": provider, "env_var": p["env_var"],
            "key_present": key_inventory.get(p["env_var"], False), "families": fams,
            "monthly_cost": p["monthly_cost"],
            "free_tier": "yes" if "$0" in p["monthly_cost"] else "no",
            "try_order": try_order, "rationale": rationale})
    return rows


def _cost_value_rows(key_inventory: Dict[str, bool]) -> List[List]:
    rows = []
    for p in _PROVIDERS:
        covers = [f for f in CRITICAL_FAMILIES
                  if _FAMILY_CAPABILITY.get(f, {}).get(p["provider"], {})
                  .get("expected_coverage", "none") in ("full", "broad", "partial")]
        # Value note: free-tier providers that cover real families are the best $/family.
        if p["provider"] == PROV_AV:
            value = "best value for EARNINGS (free tier covers the marquee surprise family)"
        elif p["provider"] == PROV_FINNHUB:
            value = "best value for the analyst families (free recommendation trends)"
        elif p["provider"] == PROV_EODHD:
            value = "mid-priced single bundle; covers earnings + fundamentals if mapped"
        elif p["provider"] == PROV_FMP_UPGRADE:
            value = "covers all six from one key but most expensive; Ultimate NOT justified"
        else:
            value = "current plan - entitlement PARTIAL on every critical family (Phase 8-N)"
        rows.append([p["provider"], p["env_var"], p["monthly_cost"],
                     ";".join(covers), len(covers), value])
    return rows


def _next_acquisition_command(strategy: Dict, decision: str,
                              key_inventory: Dict[str, bool]) -> str:
    first = strategy["cheapest_first_provider"]
    if decision == DEC_FMP_SUFFICIENT:
        return ("No alternative provider needed; collect a wider FMP batch: python research/"
                "run_phase8n_fmp_critical_data_backfill_signal_expansion.py --live --max-tickers 25")
    if decision == DEC_NO_CHEAP:
        return ("No cheap provider solves a blocked family; price an FMP Premium upgrade or a "
                "specialist source, then re-run this phase.")
    env = _PROVIDER_BY_NAME.get(first, {}).get("env_var", "ALPHAVANTAGE_API_KEY")
    return ("$env:%s = '<PASTE_%s_HERE>'; python research/"
            "run_phase8o_cheapest_provider_selection.py --live   "
            "(probe %s first; then set FINNHUB_API_KEY for the analyst families)"
            % (env, env, first))


def _write_env_setup_commands(path: Path, strategy: Dict, decision: str) -> None:
    """Write a PLACEHOLDER PowerShell helper. NEVER contains a real key - only paste-here
    placeholders + guidance. Sets keys in the CURRENT session only; nothing is written to disk."""
    first = strategy["cheapest_first_provider"] or PROV_AV
    second = strategy["second_provider"]
    lines = [
        "# Phase 8-O provider env-var setup (PLACEHOLDER ONLY - never paste a real key into a",
        "# committed file). Set the key in your CURRENT PowerShell session only; this phase",
        "# reads presence from the environment and never writes a key to disk.",
        "#",
        "# Phase 8-N proved the current FMP plan is INSUFFICIENT (all six critical families",
        "# entitlement-PARTIAL). Cheapest path = free-tier alternatives, earnings first:",
        "#",
        "# 1) CHEAPEST FIRST - %s (earnings surprises/calendar; free tier 25 req/day):" % first,
        "#   $env:ALPHAVANTAGE_API_KEY = '<PASTE_ALPHAVANTAGE_KEY_HERE>'",
        "#",
        "# 2) SECOND - %s (analyst estimates/recommendations/price targets/grades; free trends):"
        % (second or PROV_FINNHUB),
        "#   $env:FINNHUB_API_KEY = '<PASTE_FINNHUB_KEY_HERE>'",
        "#",
        "# Optional single-bundle alternative (only if a single paid source is preferred):",
        "#   $env:EODHD_API_KEY = '<PASTE_EODHD_KEY_HERE>'",
        "#",
        "# FMP Premium upgrade is the LAST-RESORT fallback (re-uses FMP_API_KEY). FMP Ultimate",
        "# is NOT justified - do not buy it.",
        "#",
        "# After setting a key, re-run the bounded live probe of the keyed provider(s):",
        "#   python research/run_phase8o_cheapest_provider_selection.py --live",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


# --------------------------------------------------------------------------- #
# CLI.
# --------------------------------------------------------------------------- #
def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=("Phase 8-O cheapest viable provider selection (offline decision by "
                     "default; bounded probe of keyed providers with --live)."))
    ap.add_argument("--live", action="store_true",
                    help="Probe the keyed alternative providers (requires that provider's key).")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    report = run(live=args.live, verbose=True)
    print("phase:                         %s" % report["phase"])
    print("mode:                          %s" % report["mode"])
    print("phase 8-N found:               %s" % report["phase8n_found"])
    print("phase 8-N fmp decision:        %s" % report["phase8n_fmp_decision"])
    print("blocked families:              %s" % ", ".join(report["blocked_families"]))
    print("provider key inventory:        %s"
          % {k: v for k, v in report["provider_key_inventory"].items()})
    print("recommended strategy:          %s" % report["recommended_strategy"])
    print("decision:                      %s" % report["decision"])
    print("cheapest provider first:       %s" % report["cheapest_provider_to_try_first"])
    print("second provider (if needed):   %s" % report["second_provider_only_if_needed"])
    print("fmp upgrade justified:         %s" % report["fmp_upgrade_justified"])
    print("fmp ultimate rejected:         %s" % report["fmp_ultimate_rejected"])
    print("env vars needed:               %s" % ", ".join(report["env_vars_needed"]))
    print("next command:                  %s" % report["recommended_next_command"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
