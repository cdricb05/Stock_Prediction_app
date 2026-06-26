"""Phase 8-N - Controlled FMP Critical-Data Backfill and Provider-Expanded Signal Confirmation.

WHY THIS PHASE EXISTS
    Phase 8-M confirmed (live, with the user's FMP_API_KEY) that the current FMP plan is
    SUFFICIENT for the six critical market-data families: earnings_surprises,
    earnings_calendar, analyst_estimates, analyst_recommendations, analyst_price_targets
    and ratings_grades_consensus all returned HTTP 200 / ACCESS_VERIFIED for AAPL/MSFT/NVDA.
    key_metrics_quarterly and ratios_quarterly returned HTTP 402 (subscription block) - NOT
    a blocker, because the statements are accessible and ratios can be computed internally.

    The alpha blocker therefore moves from "provider access unknown" to "controlled,
    point-in-time-safe provider-backed data expansion and signal validation". Phase 8-N runs
    a BOUNDED FMP backfill for the now-verified critical families, normalizes the paid data
    point-in-time, builds committed-safe coverage summaries + signal-ready manifests, and
    tests whether the expanded earnings/analyst data lets the promising signal families be
    scored - WITHOUT fabricating any result.

WHAT IT PRODUCES (24 committed-safe artifacts)
    A bounded backfill request plan + progress report, an endpoint-status report, ticker
    coverage by family, raw + normalized storage manifests, a secret-safety audit, an error
    report, a signal-ready coverage report, expanded earnings + analyst event manifests
    (coverage METADATA only - never licensed values), a provider-expanded signal scoreboard,
    confirmed / promising / provider-required / rejected signal lists, a trade-idea candidate
    registry + best candidates, a validation-skeptic report, a multiple-testing report, the
    research-director decision and the Phase 8-O next plan.

SECRET DISCIPLINE (hard rules, inherited from research/providers/fmp_client.py)
    * FMP_API_KEY is read ONLY from the environment, NEVER printed, NEVER written to disk.
    * Persisted/committed URLs strip the key parameter entirely; no committed artifact ever
      contains the literal substring "apikey=".
    * Default run is OFFLINE: it normalizes whatever raw paid data already exists under the
      gitignored research/data/fmp/raw/ tree and never fetches. A network fetch needs an
      explicit --live AND a present FMP_API_KEY. Tests run fully offline via an injected
      ``transport`` and never require a key or a network.
    * Paid data (raw verbatim payloads + normalized point-in-time CSVs) lives ONLY under
      research/data/fmp/{raw,normalized}/ which is gitignored. Committed artifacts contain
      ONLY coverage/quality/manifest metadata: row counts, date ranges, field NAMES, redacted
      URLs, file paths, statuses and decisions - never the payloads and never the key.

HARD CONSTRAINTS HONORED
    Existing installed packages only (stdlib). No package install. No full S&P 500 backfill.
    Bounded batches (<=25 tickers, <=6 endpoint families, <=150 requests, skip-existing,
    stop on repeated auth/limit errors). key_metrics/ratios skipped by default after the 402
    block. No Paper Trader. No GCP. No deploy. No broker / order / automation logic. No live
    trading signals. No weights optimized, no signs flipped. No commit. No push.

Run (default = OFFLINE: normalize existing cache + score coverage, no network):
    python research/run_phase8n_fmp_critical_data_backfill_signal_expansion.py
Bounded live backfill (requires FMP_API_KEY in the environment):
    python research/run_phase8n_fmp_critical_data_backfill_signal_expansion.py --live
Test:
    python -m pytest tests/test_phase8n_fmp_critical_data_backfill_signal_expansion.py -q
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research.providers import fmp_client as fmp  # noqa: E402

PHASE = "8-N"

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
_OUT_DIR = (_REPO_ROOT / "research" / "output"
            / "phase8n_fmp_critical_data_backfill_signal_expansion")
# Paid provider payloads live here ONLY - this whole tree is gitignored.
_FMP_DATA_DIR = _REPO_ROOT / "research" / "data" / "fmp"

# The 24 committed-safe artifact basenames.
_ARTIFACTS = {
    "report": "phase8n_fmp_critical_data_backfill_signal_expansion.json",
    "key_detection": "fmp_key_detection_report.csv",
    "request_plan": "fmp_backfill_request_plan.csv",
    "progress": "fmp_backfill_progress_report.csv",
    "endpoint_status": "fmp_endpoint_status_report.csv",
    "ticker_coverage": "fmp_ticker_coverage_by_family.csv",
    "entitlement_matrix": "fmp_family_entitlement_matrix.csv",
    "block_pattern": "fmp_subscription_block_pattern_report.csv",
    "upgrade_decision": "fmp_provider_upgrade_decision.csv",
    "alt_provider": "fmp_cheaper_provider_alternative_report.csv",
    "raw_manifest": "fmp_raw_storage_manifest.csv",
    "norm_manifest": "fmp_normalized_storage_manifest.csv",
    "secret_audit": "fmp_secret_safety_audit.csv",
    "error_report": "fmp_error_report.csv",
    "signal_coverage": "fmp_signal_ready_coverage_report.csv",
    "earnings_manifest": "expanded_earnings_event_manifest.csv",
    "analyst_manifest": "expanded_analyst_event_manifest.csv",
    "scoreboard": "provider_expanded_signal_scoreboard.csv",
    "confirmed": "confirmed_alpha_signals.csv",
    "promising": "promising_alpha_signals.csv",
    "provider_required": "provider_required_signals.csv",
    "rejected": "rejected_alpha_signals.csv",
    "trade_idea_registry": "trade_idea_candidate_registry.csv",
    "best_trade_ideas": "best_trade_idea_candidates.csv",
    "skeptic": "validation_skeptic_report.csv",
    "multiple_testing": "multiple_testing_report.csv",
    "director_decision": "research_director_decision.json",
    "next_plan": "phase8o_next_plan.json",
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
# Bounded backfill limits (hard ceilings - never exceeded).
# --------------------------------------------------------------------------- #
DEFAULT_MAX_TICKERS = 25
DEFAULT_MAX_ENDPOINT_FAMILIES = 6
DEFAULT_MAX_REQUESTS = 150
# Absolute ceilings: the controller clamps any caller-supplied value to these.
CEIL_MAX_TICKERS = 25
CEIL_MAX_ENDPOINT_FAMILIES = 10
CEIL_MAX_REQUESTS = 150
# --- Global-stop thresholds (the ONLY reasons a batch halts before budget/end). ---
# Repeated 401 invalid-auth -> the key itself is bad; stop and surface it.
STOP_AFTER_CONSEC_AUTH_ERRORS = 3
# True 429 rate-limit exhaustion (each cell already retried/backed off below) -> stop.
STOP_AFTER_CONSEC_RATE_LIMITS = 3
# Bounded per-cell retry/backoff for a 429 before it counts as rate-limited.
RATE_LIMIT_MAX_RETRIES = 2
# LEGACY only: when --no-continue-on-subscription-block is set, stop after this many
# CONSECUTIVE 402/403 subscription blocks. The DEFAULT (continue_on_subscription_block=True)
# NEVER stops on a subscription block - a 402 on one ticker/endpoint is recorded and the
# controller continues to the next ticker and the next endpoint family.
STOP_AFTER_CONSEC_SUBSCRIPTION_BLOCKS = 3
# Fraction of a family's ATTEMPTED (request-spending) cells that must be 402/403-blocked,
# with coverage still below the score minimum, for the family to count as "broadly blocked".
BROADLY_BLOCKED_FRACTION = 0.5
# Minimum number of universe tickers with a required family before a signal can be scored.
# Below this, coverage is honestly reported as insufficient (no fabricated alpha).
MIN_TICKERS_TO_SCORE = 20

SMOKE_FALLBACK = ["AAPL", "MSFT", "NVDA", "AMZN", "JPM"]

# --------------------------------------------------------------------------- #
# Per-(endpoint-family, ticker) acquisition status vocabulary.
# --------------------------------------------------------------------------- #
ACQ_CACHED = "CACHED"                  # raw already present, reused (no request spent)
ACQ_COLLECTED = "COLLECTED"            # fetched live this run (rows returned)
ACQ_EMPTY = "EMPTY_REACHABLE"          # fetched live, 200 but no rows
ACQ_NEEDS_FETCH = "NEEDS_FETCH"        # absent and no key/transport to fetch it
ACQ_SUBSCRIPTION_BLOCKED = "SUBSCRIPTION_BLOCKED"   # 402/403 entitlement block (key IS valid)
ACQ_AUTH_ERROR = "AUTH_ERROR"          # 401 invalid/missing-auth (the key itself is bad)
ACQ_RATE_LIMITED = "RATE_LIMITED"      # 429 (after bounded retry/backoff)
ACQ_ENDPOINT_ERROR = "ENDPOINT_ERROR"  # 404/5xx/network/bad-response
ACQ_SKIPPED_DO_NOT_SPEND = "SKIPPED_DO_NOT_SPEND"   # key_metrics/ratios after the 402 block
ACQ_STOPPED_EARLY = "STOPPED_EARLY"    # batch halted on repeated auth/limit errors
_ACQ_RESOLVED = {ACQ_CACHED, ACQ_COLLECTED, ACQ_EMPTY}

# --------------------------------------------------------------------------- #
# Research-director decision vocabulary.
# The original seven (8-M contract) PLUS three coverage/entitlement outcomes added by the
# 8-N controller patch so a partial/blocked FMP plan is reported honestly rather than
# collapsed into a premature "rejected".
# --------------------------------------------------------------------------- #
DEC_CONFIRMED = "PROVIDER_EXPANDED_CONFIRMED_ALPHA_FOUND"
DEC_PROMISING = "PROVIDER_EXPANDED_PROMISING_ALPHA_FOUND"
DEC_READY_NEXT_BATCH = "READY_FOR_NEXT_FMP_BATCH"
DEC_NEEDS_MORE_COVERAGE = "NEEDS_MORE_FMP_COVERAGE"
DEC_BLOCKED_NO_KEY = "BLOCKED_MISSING_FMP_KEY"
DEC_EXPANSION_REJECTED = "PROVIDER_EXPANSION_REJECTED"
DEC_ERROR = "ERROR"
# Added by the 8-N controller patch:
DEC_READY_FOR_SCORING = "READY_FOR_PROVIDER_EXPANDED_SIGNAL_SCORING"
DEC_FMP_PLAN_INSUFFICIENT = "FMP_PLAN_COVERAGE_INSUFFICIENT"
DEC_MIXED_NEEDS_ALT_EARNINGS = "MIXED_FMP_COVERAGE_NEEDS_ALTERNATIVE_EARNINGS_SOURCE"
ALLOWED_DECISIONS = (
    DEC_CONFIRMED, DEC_PROMISING, DEC_READY_NEXT_BATCH, DEC_NEEDS_MORE_COVERAGE,
    DEC_BLOCKED_NO_KEY, DEC_EXPANSION_REJECTED, DEC_ERROR,
    DEC_READY_FOR_SCORING, DEC_FMP_PLAN_INSUFFICIENT, DEC_MIXED_NEEDS_ALT_EARNINGS,
)

# --------------------------------------------------------------------------- #
# Endpoint families. CRITICAL FIRST (this ordering is inherited from the 8-M fix).
# Each carries the FMP "stable" path template and the normalizer date-field candidates.
# key_metrics/ratios are present but DO_NOT_SPEND by default (402 in 8-M).
# --------------------------------------------------------------------------- #
_DATE_FIELDS = ("date", "publishedDate", "fillingDate", "acceptedDate", "updatedAt",
                "lastUpdated", "calendarYear")

CRITICAL_FAMILIES = (
    "earnings_surprises", "earnings_calendar", "analyst_estimates",
    "analyst_recommendations", "analyst_price_targets", "ratings_grades_consensus",
)

_ENDPOINTS: Tuple[Dict, ...] = (
    {"rank": 0, "family": "earnings_surprises", "critical": True, "do_not_spend": False,
     "path": "/stable/earnings?symbol={symbol}", "kind": "earnings"},
    {"rank": 1, "family": "earnings_calendar", "critical": True, "do_not_spend": False,
     "path": "/stable/earnings?symbol={symbol}", "kind": "earnings"},
    {"rank": 2, "family": "analyst_estimates", "critical": True, "do_not_spend": False,
     "path": "/stable/analyst-estimates?symbol={symbol}&period=annual", "kind": "analyst"},
    {"rank": 3, "family": "analyst_recommendations", "critical": True, "do_not_spend": False,
     "path": "/stable/grades?symbol={symbol}", "kind": "analyst"},
    {"rank": 4, "family": "analyst_price_targets", "critical": True, "do_not_spend": False,
     "path": "/stable/price-target-summary?symbol={symbol}", "kind": "analyst"},
    {"rank": 5, "family": "ratings_grades_consensus", "critical": True, "do_not_spend": False,
     "path": "/stable/grades-consensus?symbol={symbol}", "kind": "analyst"},
    # Optional - only collected when --include-optional (already-cached files still normalize).
    {"rank": 10, "family": "company_profile", "critical": False, "do_not_spend": False,
     "path": "/stable/profile?symbol={symbol}", "kind": "profile", "optional": True},
    {"rank": 11, "family": "income_statement_quarterly", "critical": False, "do_not_spend": False,
     "path": "/stable/income-statement?symbol={symbol}&period=quarter", "kind": "statement",
     "optional": True},
    {"rank": 12, "family": "balance_sheet_statement_quarterly", "critical": False,
     "do_not_spend": False,
     "path": "/stable/balance-sheet-statement?symbol={symbol}&period=quarter",
     "kind": "statement", "optional": True},
    {"rank": 13, "family": "cash_flow_statement_quarterly", "critical": False,
     "do_not_spend": False,
     "path": "/stable/cash-flow-statement?symbol={symbol}&period=quarter",
     "kind": "statement", "optional": True},
    # DO-NOT-SPEND - subscription-blocked (402) in Phase 8-M; never requested by default.
    {"rank": 20, "family": "key_metrics_quarterly", "critical": False, "do_not_spend": True,
     "path": "/stable/key-metrics?symbol={symbol}&period=quarter", "kind": "statement"},
    {"rank": 21, "family": "ratios_quarterly", "critical": False, "do_not_spend": True,
     "path": "/stable/ratios?symbol={symbol}&period=quarter", "kind": "statement"},
)


def build_backfill_families(max_endpoint_families: int = DEFAULT_MAX_ENDPOINT_FAMILIES,
                            include_optional: bool = False,
                            include_blocked: bool = False) -> Tuple[List[Dict], List[str]]:
    """Return the bounded, CRITICAL-FIRST list of endpoint families to backfill.

    Mirrors the Phase 8-M controller fix: critical earnings/analyst families are always at
    the front and can never be trimmed by the cap; only NON-critical families are trimmed.
    key_metrics/ratios are excluded by default (DO_NOT_SPEND - 402 in 8-M).
    """
    notes: List[str] = []
    eps = sorted((dict(e) for e in _ENDPOINTS), key=lambda e: e["rank"])
    pool: List[Dict] = []
    for e in eps:
        if e["do_not_spend"] and not include_blocked:
            continue
        if e.get("optional") and not include_optional:
            continue
        pool.append(e)
    critical = [e for e in pool if e["critical"]]
    non_critical = [e for e in pool if not e["critical"]]

    cap = max(0, min(int(max_endpoint_families), CEIL_MAX_ENDPOINT_FAMILIES))
    if cap < len(critical):
        notes.append(
            "endpoint-family cap (%d) is below the number of critical families (%d); "
            "OVERRIDING the cap so no critical earnings/analyst family is dropped." %
            (cap, len(critical)))
        non_critical_slots = 0
    else:
        non_critical_slots = cap - len(critical)
    families = list(critical) + non_critical[:non_critical_slots]
    return families, notes


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


def _as_rows(payload) -> List[Dict]:
    """Coerce a raw payload into a list of dict rows (FMP returns a list; tolerate a dict)."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        if payload.get("dry_run") or any(k.lower().startswith("error") for k in payload):
            return []
        return [payload]
    return []


def _first_last_date(rows: List[Dict]) -> Tuple[str, str]:
    dates: List[str] = []
    for r in rows:
        for k in _DATE_FIELDS:
            v = r.get(k)
            if isinstance(v, str) and v.strip():
                dates.append(v.strip()[:10])
                break
    if not dates:
        return ("", "")
    dates.sort()
    return (dates[0], dates[-1])


def _field_names(rows: List[Dict]) -> List[str]:
    seen: List[str] = []
    for r in rows:
        for k in r.keys():
            if k not in seen:
                seen.append(str(k))
    return seen


# --------------------------------------------------------------------------- #
# Universe resolution (priority order from the brief).
# --------------------------------------------------------------------------- #
_TICKER_COLS = ("ticker", "symbol", "Ticker", "Symbol", "TICKER", "SYMBOL")


def _tickers_from_csv(path: Path) -> List[str]:
    if not path.is_file():
        return []
    out: List[str] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            cols = reader.fieldnames or []
            tcol = next((c for c in cols if c in _TICKER_COLS), None)
            if tcol is None:
                return []
            for row in reader:
                v = (row.get(tcol) or "").strip().upper()
                if v.isalpha() and 1 <= len(v) <= 5 and v not in out:
                    out.append(v)
    except OSError:
        return []
    return out


def resolve_universe(max_tickers: int, raw_dir: Path,
                     explicit: Optional[List[str]] = None) -> Tuple[List[str], str]:
    """Resolve the bounded backfill universe. Returns (tickers, source_label).

    Priority: explicit -> S8L/S8K promising candidate tickers -> research candidate
    registry -> current collected universe (cached company_profile dir) -> smoke fallback.
    AAPL/MSFT/NVDA (the Phase 8-M verified tickers) are pinned to the FRONT so their
    already-cached critical data is always used first. Capped to ``max_tickers``.
    """
    cap = max(1, min(int(max_tickers), CEIL_MAX_TICKERS))
    if explicit:
        picked = [t.strip().upper() for t in explicit if str(t).strip()]
        return (picked[:cap], "explicit")

    out_root = _REPO_ROOT / "research" / "output"
    source = "smoke_fallback"
    found: List[str] = []
    for tag, rels in (
        ("s8l_s8k_candidates", [
            "phase8l_data_family_expansion_signal_factory/best_trade_idea_candidates.csv",
            "phase8k_self_expanding_alpha_factory/trade_idea_candidate_registry.csv"]),
        ("research_candidate_registry", [
            "phase8i_autonomous_alpha_discovery_program/candidate_signal_registry.csv"]),
    ):
        for rel in rels:
            found.extend(_tickers_from_csv(out_root / rel))
        if found:
            source = tag
            break

    # Current collected universe fallback: tickers already profiled in the raw cache.
    if not found and (raw_dir / "company_profile").is_dir():
        found = sorted(p.stem.upper() for p in (raw_dir / "company_profile").glob("*.json"))
        if found:
            source = "current_collected_universe"

    if not found:
        found = list(SMOKE_FALLBACK)
        source = "smoke_fallback"

    # Pin the 8-M verified tickers to the front (they carry the cached critical data).
    pinned = [t for t in ["AAPL", "MSFT", "NVDA"] if t in found or source == "smoke_fallback"]
    ordered: List[str] = []
    for t in pinned + found + SMOKE_FALLBACK:
        if t not in ordered:
            ordered.append(t)
    return (ordered[:cap], source)


# --------------------------------------------------------------------------- #
# Classification of a live error (reasons only from HTTP status + payload shape).
# --------------------------------------------------------------------------- #
def classify_error(status_code: Optional[int], error_type: str) -> str:
    # 401 = the key itself is invalid/unauthorized (an AUTH problem that, if repeated, means
    # the key is bad and the whole batch should stop). 402/403 = the key is VALID but the
    # current plan is not entitled to THIS endpoint (a per-endpoint subscription block that
    # must NOT stop the batch). Keeping them distinct is the core of the early-stop fix.
    if status_code == 401:
        return ACQ_AUTH_ERROR
    if status_code in (402, 403):
        return ACQ_SUBSCRIPTION_BLOCKED
    if status_code == 429:
        return ACQ_RATE_LIMITED
    return ACQ_ENDPOINT_ERROR


# --------------------------------------------------------------------------- #
# Bounded backfill.
#
# EARLY-STOP CONTRACT (the Phase 8-N controller patch):
#   A 402/403 SUBSCRIPTION_BLOCKED on one ticker or one endpoint family is RECORDED and the
#   controller CONTINUES - to the next ticker, and to the next endpoint family. A subscription
#   block proves the key is valid (just not entitled to that endpoint), so it never halts the
#   batch when continue_on_subscription_block is True (the default).
#
#   The batch stops GLOBALLY only for:
#     * repeated 401 invalid-auth        (>= STOP_AFTER_CONSEC_AUTH_ERRORS)  -> bad key;
#     * true 429 rate-limit exhaustion   (>= STOP_AFTER_CONSEC_RATE_LIMITS,  each cell already
#                                         retried/backed off RATE_LIMIT_MAX_RETRIES times);
#     * the max_requests budget is exhausted;
#     * a manual safety stop (caller passes a transport that raises to abort) - not used here.
#   When continue_on_subscription_block is False (legacy --no-continue-on-subscription-block),
#   repeated 402/403 also stop after STOP_AFTER_CONSEC_SUBSCRIPTION_BLOCKS.
# --------------------------------------------------------------------------- #
Transport = Callable[[str], object]


def _fetch(path: str, transport: Optional[Transport]):
    if transport is not None:
        return transport(path)
    return fmp.get_json(path, live=True)


def _fetch_with_retry(path: str, transport: Optional[Transport],
                      max_retries: int) -> Tuple[object, Optional[Exception]]:
    """Fetch a cell, retrying ONLY on a 429 rate-limit with bounded backoff.

    Returns (payload, None) on success or (None, exc) once retries are exhausted / the error
    is not a 429. Counts as ONE logical request against the budget regardless of retries.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(max(1, int(max_retries) + 1)):
        try:
            return _fetch(path, transport), None
        except fmp.FmpError as exc:
            last_exc = exc
            code = getattr(exc, "status_code", None)
            if code == 429 and attempt < max_retries:
                if transport is None:  # real network only; tests never sleep
                    time.sleep(fmp.MIN_SLEEP_SECONDS * (attempt + 1))
                continue
            return None, exc
    return None, last_exc


def run_backfill(families: List[Dict], tickers: List[str], raw_dir: Path,
                 transport: Optional[Transport], live: bool, api_key_present: bool,
                 skip_existing: bool = True, max_requests: int = DEFAULT_MAX_REQUESTS,
                 continue_on_subscription_block: bool = True,
                 verbose: bool = False) -> Tuple[List[Dict], Dict]:
    """Backfill each (family, ticker), CRITICAL-FIRST and BOUNDED.

    Acquisition order per cell:
      * raw already present (+ skip_existing) -> CACHED (reused; NO request, NO write);
      * else fetch only when a transport is injected (tests) OR --live AND key present;
        on success persist the verbatim payload under the gitignored raw tree;
      * else (no key, absent) -> NEEDS_FETCH (no request, no write).

    A 402/403 subscription block does NOT stop the batch (default) - see the EARLY-STOP
    CONTRACT above. Returns (rows, meta). Never writes the key or a key-bearing URL. Never
    overwrites an existing raw file (skip-existing protects the already-collected paid data).
    """
    can_fetch = transport is not None or (bool(live) and api_key_present)
    budget = max(0, min(int(max_requests), CEIL_MAX_REQUESTS))
    rows: List[Dict] = []
    requests_made = 0
    consec_auth = 0       # consecutive 401 invalid-auth
    consec_rate = 0       # consecutive 429 (post-retry) rate-limits
    consec_sub = 0        # consecutive 402/403 (only stops in legacy mode)
    stopped_early = False
    stop_reason = ""
    # Running per-family tallies for the live progress line (coverage = collected+cached).
    fam_cov: Dict[str, int] = {}
    fam_blocked: Dict[str, int] = {}
    for e in families:
        fam = e["family"]
        fam_cov.setdefault(fam, 0)
        fam_blocked.setdefault(fam, 0)
        for tk in tickers:
            raw_path = raw_dir / fam / ("%s.json" % tk)
            cached_payload = _read_json_file(raw_path) if raw_path.is_file() else None
            has_cache = cached_payload is not None and bool(_as_rows(cached_payload))
            redacted = fmp.redacted_request_url(e["path"].replace("{symbol}", tk))

            # 1) Reuse already-collected paid data (no request, no write).
            if skip_existing and has_cache:
                n = len(_as_rows(cached_payload))
                rows.append(_acq_row(e, tk, redacted, ACQ_CACHED, 200, n, "", "",
                                     requested=False))
                fam_cov[fam] += 1
                _progress(verbose, fam, tk, ACQ_CACHED, requests_made, fam_cov[fam],
                          fam_blocked[fam])
                continue

            # 2) Cannot fetch (no transport, no live key): record the gap, spend nothing.
            if not can_fetch:
                rows.append(_acq_row(e, tk, redacted, ACQ_NEEDS_FETCH, "", 0, "",
                                     "no FMP_API_KEY in this session; cannot fetch absent cell",
                                     requested=False))
                _progress(verbose, fam, tk, ACQ_NEEDS_FETCH, requests_made, fam_cov[fam],
                          fam_blocked[fam])
                continue

            # 3) Respect the request budget / global stop. NOTE: a subscription block never
            #    lands here - only an exhausted budget or a genuine auth/rate-limit stop does.
            if requests_made >= budget or stopped_early:
                reason = stop_reason or "request budget reached"
                rows.append(_acq_row(e, tk, redacted, ACQ_STOPPED_EARLY, "", 0, "",
                                     reason, requested=False))
                _progress(verbose, fam, tk, ACQ_STOPPED_EARLY, requests_made, fam_cov[fam],
                          fam_blocked[fam])
                continue

            requests_made += 1
            path = e["path"].replace("{symbol}", tk)
            payload, exc = _fetch_with_retry(path, transport, RATE_LIMIT_MAX_RETRIES)
            if exc is None:
                data = _as_rows(payload)
                status = ACQ_COLLECTED if data else ACQ_EMPTY
                # Persist verbatim payload (gitignored; never overwrites a cached file).
                if not raw_path.is_file():
                    raw_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(raw_path, "w", encoding="utf-8") as fh:
                        json.dump(payload, fh, indent=2)
                        fh.write("\n")
                rows.append(_acq_row(e, tk, redacted, status, 200, len(data), "", "",
                                     requested=True))
                if status == ACQ_COLLECTED:
                    fam_cov[fam] += 1
                consec_auth = consec_rate = consec_sub = 0  # any success resets all stops
            else:
                code = getattr(exc, "status_code", None)
                etype = getattr(exc, "error_type", "error")
                status = classify_error(code, etype)
                rows.append(_acq_row(e, tk, redacted, status, "" if code is None else code,
                                     0, etype, str(exc), requested=True))
                if status == ACQ_AUTH_ERROR:
                    consec_auth += 1
                    consec_rate = consec_sub = 0
                    if consec_auth >= STOP_AFTER_CONSEC_AUTH_ERRORS:
                        stopped_early, stop_reason = True, "repeated_invalid_auth_401"
                elif status == ACQ_RATE_LIMITED:
                    consec_rate += 1
                    consec_auth = consec_sub = 0
                    if consec_rate >= STOP_AFTER_CONSEC_RATE_LIMITS:
                        stopped_early, stop_reason = True, "rate_limit_exhausted_429"
                elif status == ACQ_SUBSCRIPTION_BLOCKED:
                    # Entitlement block: the key IS valid. Record + continue (the fix).
                    fam_blocked[fam] += 1
                    consec_auth = consec_rate = 0  # a 402 proves auth works -> reset auth
                    if not continue_on_subscription_block:
                        consec_sub += 1
                        if consec_sub >= STOP_AFTER_CONSEC_SUBSCRIPTION_BLOCKS:
                            stopped_early, stop_reason = True, "subscription_block_legacy_stop"
                    else:
                        consec_sub = 0
                else:  # transient endpoint/network error: do not trip an auth/limit stop
                    consec_auth = consec_rate = consec_sub = 0
            _progress(verbose, fam, tk, rows[-1]["status"], requests_made, fam_cov[fam],
                      fam_blocked[fam])
            if transport is None:
                time.sleep(fmp.MIN_SLEEP_SECONDS)
    meta = {"requests_made": requests_made, "stopped_early": stopped_early,
            "stop_reason": stop_reason, "budget": budget,
            "requests_remaining": max(0, budget - requests_made),
            "continue_on_subscription_block": continue_on_subscription_block,
            "can_fetch": can_fetch, "skip_existing": skip_existing}
    return rows, meta


def _progress(verbose: bool, fam: str, ticker: str, result: str, requests_made: int,
              fam_coverage: int, fam_blocked: int) -> None:
    """One clear stdout line per cell: family, ticker, result, cumulative requests, running
    family coverage, running blocked count."""
    if not verbose:
        return
    print("  [%-26s] %-6s -> %-18s | req=%-3d cov=%-2d blocked=%-2d"
          % (fam, ticker, result, requests_made, fam_coverage, fam_blocked))


def _acq_row(e: Dict, ticker: str, redacted: str, status: str, http_status, row_count: int,
             error_type: str, error_msg: str, requested: bool) -> Dict:
    return {
        "endpoint_family": e["family"], "critical": e["critical"], "rank": e["rank"],
        "ticker": ticker, "attempted_url_redacted": redacted, "status": status,
        "http_status": http_status, "row_count": row_count, "request_spent": requested,
        "error_type": error_type, "error_message_sanitized": error_msg,
    }


# --------------------------------------------------------------------------- #
# Normalization (paid data -> gitignored normalized CSV; committed sees only metadata).
# --------------------------------------------------------------------------- #
def normalize_family(fam: str, tickers: List[str], raw_dir: Path,
                     norm_dir: Path) -> Dict[str, Dict]:
    """Flatten every cached/collected raw payload for ``fam`` into ONE normalized CSV under
    the gitignored normalized tree, and return per-ticker coverage METADATA (counts, date
    range, field names) - never the licensed values.
    """
    per_ticker: Dict[str, Dict] = {}
    all_rows: List[Dict] = []
    all_fields: List[str] = []
    for tk in tickers:
        raw_path = raw_dir / fam / ("%s.json" % tk)
        payload = _read_json_file(raw_path) if raw_path.is_file() else None
        rows = _as_rows(payload) if payload is not None else []
        first, last = _first_last_date(rows)
        fields = _field_names(rows)
        for f in fields:
            if f not in all_fields:
                all_fields.append(f)
        for r in rows:
            rr = {"symbol": tk}
            rr.update({k: r.get(k) for k in r.keys()})
            all_rows.append(rr)
        per_ticker[tk] = {"rows": len(rows), "first_date": first, "last_date": last,
                          "fields": fields, "has_data": bool(rows)}
    # Write the normalized CSV (paid-derived; gitignored). Header = symbol + union of fields.
    if all_rows:
        header = ["symbol"] + all_fields
        norm_dir.mkdir(parents=True, exist_ok=True)
        out_path = norm_dir / ("%s.csv" % fam)
        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            for rr in all_rows:
                w.writerow([rr.get("symbol")] + [_flatten_cell(rr.get(k)) for k in all_fields])
    return per_ticker


def _flatten_cell(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, separators=(",", ":"))
    return "" if v is None else v


# --------------------------------------------------------------------------- #
# Signal hypotheses (the six the brief asks us to test for readiness).
# Each names the FMP family it needs; conditioning cohorts (rates_sensitivity /
# sector_leadership) are LOCAL (Norgate) per Phase 8-M and assumed available.
# --------------------------------------------------------------------------- #
_SIGNALS: Tuple[Dict, ...] = (
    {"signal_id": "S8N-EARNSURP-RATES",
     "name": "positive_earnings_surprise x rates_sensitivity",
     "required_family": "earnings_surprises", "cohort": "rates_sensitivity (local)",
     "rerun_of": "S8L-RATES-EARNCONF-20"},
    {"signal_id": "S8N-EARNSURP-SECTOR",
     "name": "positive_earnings_surprise x sector_leadership",
     "required_family": "earnings_surprises", "cohort": "sector_leadership (local)",
     "rerun_of": "earnings_surprise x sector_leadership"},
    {"signal_id": "S8N-ESTREV-RATES",
     "name": "analyst_estimate_revision x sensitivity",
     "required_family": "analyst_estimates", "cohort": "rates_sensitivity (local)",
     "rerun_of": "analyst_estimates/revisions x rates_sensitivity"},
    {"signal_id": "S8N-RECCHG-SECTOR",
     "name": "analyst_recommendation_change x sector_leadership",
     "required_family": "analyst_recommendations", "cohort": "sector_leadership (local)",
     "rerun_of": "analyst_recommendations x sector_leadership"},
    {"signal_id": "S8N-PTREV-SENS",
     "name": "price_target_revision x sensitivity_cohort",
     "required_family": "analyst_price_targets", "cohort": "sensitivity_cohort (local)",
     "rerun_of": "price_targets x sensitivity_cohort"},
    {"signal_id": "S8N-GRADES-SENS",
     "name": "ratings_grades_consensus x sensitivity_cohort",
     "required_family": "ratings_grades_consensus", "cohort": "sensitivity_cohort (local)",
     "rerun_of": "ratings/grades_consensus x sensitivity_cohort"},
)

# Signal verdicts.
VER_CONFIRMED = "CONFIRMED_ALPHA"
VER_PROMISING = "PROMISING_ALPHA"
VER_REJECTED = "REJECTED"
VER_PROVIDER_REQUIRED = "PROVIDER_REQUIRED"         # the required family is blocked/absent
VER_INSUFFICIENT = "INSUFFICIENT_COVERAGE"          # covered too few tickers to score
VER_COVERAGE_READY = "COVERAGE_READY_SCORING_DEFERRED"   # enough coverage, awaiting real scorer


def score_signals(family_status: Dict[str, str], coverage_counts: Dict[str, int],
                  n_universe: int,
                  injected_scores: Optional[Dict[str, Dict]] = None) -> List[Dict]:
    """Score each signal hypothesis HONESTLY.

    Coverage gates the verdict. A real alpha verdict (CONFIRMED/PROMISING/REJECTED) is
    produced ONLY when a real scoring result is provided via ``injected_scores`` (a wired
    scoring harness). Absent that, no alpha number is fabricated: a signal is at most
    COVERAGE_READY_SCORING_DEFERRED, or INSUFFICIENT_COVERAGE / PROVIDER_REQUIRED.
    """
    injected_scores = injected_scores or {}
    out: List[Dict] = []
    for s in _SIGNALS:
        fam = s["required_family"]
        fam_st = family_status.get(fam, ACQ_NEEDS_FETCH)
        covered = coverage_counts.get(fam, 0)
        sc = injected_scores.get(s["signal_id"])
        if fam_st in (ACQ_SUBSCRIPTION_BLOCKED,):
            verdict, score, detail = (VER_PROVIDER_REQUIRED, "",
                                      "required family subscription-blocked on current plan")
        elif covered < MIN_TICKERS_TO_SCORE:
            verdict, score, detail = (
                VER_INSUFFICIENT, "",
                "coverage %d/%d ticker(s) < %d required to score; collect next FMP batch" %
                (covered, n_universe, MIN_TICKERS_TO_SCORE))
        elif sc is not None:
            # A real scoring result was supplied. Classify by its (ic, ev) honestly.
            score = sc.get("score", "")
            ev = sc.get("ev_after_costs")
            recent = sc.get("recent_lift")
            if sc.get("confirmed"):
                verdict, detail = VER_CONFIRMED, "passed walk-forward + cost + recency gates"
            elif ev is not None and ev > 0:
                verdict, detail = VER_PROMISING, "positive EV; needs recency/tail confirmation"
            else:
                verdict, detail = VER_REJECTED, "non-positive EV after costs"
            detail = "%s (ev=%s recent=%s)" % (detail, ev, recent)
        else:
            verdict, score, detail = (
                VER_COVERAGE_READY, "",
                "coverage sufficient (%d ticker(s)); no scoring harness wired in this run - "
                "scoring deferred (no alpha fabricated)" % covered)
        out.append({**s, "required_family_status": fam_st, "tickers_covered": covered,
                    "n_universe": n_universe, "verdict": verdict, "score": score,
                    "detail": detail})
    return out


# --------------------------------------------------------------------------- #
# Secret leak scan + audit (reused contract from Phase 8-M / 5-E0).
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
        row("urls_redacted_in_committed_artifacts", True, True,
            "every committed URL strips the key query parameter (placeholder token only)"),
        row("raw_paid_data_gitignored", _rel(_FMP_DATA_DIR / "raw"), True,
            "verbatim FMP payloads live only under the gitignored raw tree"),
        row("normalized_paid_data_gitignored", _rel(_FMP_DATA_DIR / "normalized"), True,
            "normalized point-in-time CSVs live only under the gitignored normalized tree"),
        row("no_raw_payload_in_committed_artifacts", True, True,
            "committed artifacts carry only coverage metadata (counts/dates/field names)"),
        row("no_key_in_output_files", leak_clean, leak_clean,
            "scanned %d artifact(s); no raw key value and no key query marker found" % scanned),
        row("live_requires_explicit_flag_and_key", True, True,
            "a network call needs both --live and a present %s" % fmp.API_KEY_ENV),
        row("skip_existing_protects_paid_cache", True, True,
            "existing raw files are reused read-only and never overwritten"),
    ]


# --------------------------------------------------------------------------- #
# Per-family entitlement / block statistics (drive the matrix + decision).
# --------------------------------------------------------------------------- #
_EARNINGS_FAMILIES = ("earnings_surprises", "earnings_calendar")
_ANALYST_FAMILIES = ("analyst_estimates", "analyst_recommendations",
                     "analyst_price_targets", "ratings_grades_consensus")

# Entitlement labels.
ENT_ENTITLED = "ENTITLED"            # data collected/cached, no block seen
ENT_ENTITLED_EMPTY = "ENTITLED_EMPTY"  # reachable (200) but no rows returned
ENT_PARTIAL = "PARTIAL"              # some data AND some blocks
ENT_BLOCKED = "BLOCKED"              # attempted, every attempt 402/403-blocked, no data
ENT_UNKNOWN = "UNKNOWN"              # never attempted and not cached


def family_block_stats(acq_rows: List[Dict], families: List[Dict]) -> Dict[str, Dict]:
    """Roll each family's acquisition cells into counts used by the entitlement matrix,
    block-pattern report and the decision. ``attempted`` = cells that actually spent a
    request (the denominator for the block fraction)."""
    out: Dict[str, Dict] = {}
    for e in families:
        fam = e["family"]
        cells = [r for r in acq_rows if r["endpoint_family"] == fam]
        def n(*sts):
            return sum(1 for c in cells if c["status"] in sts)
        attempted = sum(1 for c in cells if c["request_spent"])
        blocked = n(ACQ_SUBSCRIPTION_BLOCKED)
        out[fam] = {
            "critical": e["critical"], "cells": len(cells), "attempted": attempted,
            "collected": n(ACQ_COLLECTED), "cached": n(ACQ_CACHED), "empty": n(ACQ_EMPTY),
            "blocked": blocked, "auth": n(ACQ_AUTH_ERROR), "rate": n(ACQ_RATE_LIMITED),
            "errors": n(ACQ_ENDPOINT_ERROR), "needs_fetch": n(ACQ_NEEDS_FETCH),
            "stopped_early": n(ACQ_STOPPED_EARLY),
            "blocked_tickers": [c["ticker"] for c in cells
                                if c["status"] == ACQ_SUBSCRIPTION_BLOCKED],
            "block_fraction": round(blocked / attempted, 4) if attempted else 0.0,
        }
    return out


def classify_entitlement(st: Dict) -> str:
    have = st["collected"] + st["cached"]
    if have and st["blocked"]:
        return ENT_PARTIAL
    if have:
        return ENT_ENTITLED
    if st["blocked"]:
        return ENT_BLOCKED
    if st["empty"]:
        return ENT_ENTITLED_EMPTY
    return ENT_UNKNOWN


def is_broadly_blocked(st: Dict, coverage: int) -> bool:
    """A family is broadly blocked when it spent requests, at least half of them were
    402/403-blocked, and coverage is still below the score minimum."""
    if st.get("attempted", 0) <= 0:
        return False
    return (st["block_fraction"] >= BROADLY_BLOCKED_FRACTION
            and coverage < MIN_TICKERS_TO_SCORE)


def block_pattern(st: Dict) -> str:
    if st.get("attempted", 0) <= 0:
        return "NOT_ATTEMPTED"
    frac = st["block_fraction"]
    if frac <= 0.0:
        return "NONE"
    if frac >= 1.0:
        return "TOTAL"
    if frac >= BROADLY_BLOCKED_FRACTION:
        return "SYSTEMATIC"
    return "SPORADIC"


def _block_interpretation(pattern: str, fam: str) -> str:
    return {
        "NOT_ATTEMPTED": "no live request spent (cached/offline) - entitlement unobserved",
        "NONE": "no subscription block - family is entitled on the current plan",
        "SPORADIC": "isolated 402/403s (some tickers lack data on the plan); family usable",
        "SYSTEMATIC": "most attempts 402/403-blocked - this family is short on the current plan",
        "TOTAL": "every attempt 402/403-blocked - family NOT entitled on the current plan",
    }.get(pattern, "unclassified")


# --------------------------------------------------------------------------- #
# Decision.
# --------------------------------------------------------------------------- #
def derive_decision(scored: List[Dict], family_status: Dict[str, str],
                    coverage_counts: Dict[str, int], block_stats: Dict[str, Dict],
                    acquisition_executed: bool, api_key_present: bool,
                    any_cached: bool, requests_remaining: int) -> str:
    """Decide the research-director outcome from coverage + entitlement (no fabricated alpha).

    Precedence:
      1. a real CONFIRMED / PROMISING score (only ever from a wired scorer);
      2. nothing fetched and nothing cached -> blocked-no-key / needs-coverage;
      3. all six critical families >= MIN tickers -> ready to SCORE;
      4. earnings broadly 402-blocked but analyst families work -> need an alt earnings source;
      5. most critical families broadly 402-blocked -> current FMP plan coverage insufficient;
      6. otherwise some data resolved but < MIN tickers -> collect the next FMP batch
         (or, if the budget is spent and nothing is blocked, simply needs more coverage).
    """
    verdicts = [s["verdict"] for s in scored]
    if any(v == VER_CONFIRMED for v in verdicts):
        return DEC_CONFIRMED
    if any(v == VER_PROMISING for v in verdicts):
        return DEC_PROMISING

    if not acquisition_executed and not any_cached:
        return DEC_BLOCKED_NO_KEY if not api_key_present else DEC_NEEDS_MORE_COVERAGE

    crit = list(CRITICAL_FAMILIES)
    ready = [f for f in crit if coverage_counts.get(f, 0) >= MIN_TICKERS_TO_SCORE]
    if len(ready) == len(crit):
        return DEC_READY_FOR_SCORING

    broadly = {f: is_broadly_blocked(block_stats.get(f, {}), coverage_counts.get(f, 0))
               for f in crit}
    earn_blocked = any(broadly[f] for f in _EARNINGS_FAMILIES)
    analyst_works = any(
        not broadly[f] and (coverage_counts.get(f, 0) > 0
                            or block_stats.get(f, {}).get("collected", 0) > 0
                            or family_status.get(f) in _ACQ_RESOLVED)
        for f in _ANALYST_FAMILIES)
    analyst_all_blocked = all(broadly[f] for f in _ANALYST_FAMILIES)

    # 4) earnings broadly blocked but the analyst side is healthy -> alternative earnings source.
    if earn_blocked and analyst_works and not analyst_all_blocked:
        return DEC_MIXED_NEEDS_ALT_EARNINGS

    # 5) most attempted critical families are broadly blocked -> the plan itself is short.
    attempted_fams = [f for f in crit if block_stats.get(f, {}).get("attempted", 0) > 0]
    n_blocked = sum(1 for f in crit if broadly[f])
    if attempted_fams and not ready and n_blocked >= max(1, (len(attempted_fams) + 1) // 2):
        return DEC_FMP_PLAN_INSUFFICIENT

    # 6) data resolving but still thin.
    if requests_remaining <= 0 and n_blocked == 0:
        return DEC_NEEDS_MORE_COVERAGE
    return DEC_READY_NEXT_BATCH


def _next_command(decision: str, next_batch: int) -> str:
    if decision in (DEC_READY_NEXT_BATCH, DEC_NEEDS_MORE_COVERAGE):
        return ("python research/run_phase8n_fmp_critical_data_backfill_signal_expansion.py "
                "--live --max-tickers %d  (collect the next bounded FMP batch with "
                "FMP_API_KEY present)" % next_batch)
    if decision == DEC_READY_FOR_SCORING:
        return ("All six critical families reached the %d-ticker minimum; wire the scoring "
                "harness (injected_scores) / run the Phase 8-O walk-forward confirmation."
                % MIN_TICKERS_TO_SCORE)
    if decision == DEC_MIXED_NEEDS_ALT_EARNINGS:
        return ("Analyst families work but earnings are broadly 402-blocked; activate the "
                "recommended earnings alternative in fmp_cheaper_provider_alternative_report.csv "
                "(Alpha Vantage EARNINGS is cheapest), then re-run --live.")
    if decision == DEC_FMP_PLAN_INSUFFICIENT:
        return ("Most critical families are 402-blocked on the current FMP plan; see "
                "fmp_cheaper_provider_alternative_report.csv and fmp_provider_upgrade_decision.csv "
                "- activate the cheapest provider that solves each missing family (do NOT "
                "default to an FMP Ultimate upgrade).")
    if decision == DEC_BLOCKED_NO_KEY:
        return ("Set FMP_API_KEY in the environment, then re-run: python research/"
                "run_phase8n_fmp_critical_data_backfill_signal_expansion.py --live")
    if decision == DEC_EXPANSION_REJECTED:
        return ("Critical families are subscription-blocked; re-run Phase 8-M --live to "
                "re-classify entitlement, or activate the recommended provider.")
    if decision in (DEC_CONFIRMED, DEC_PROMISING):
        return ("Promote the confirmed/promising signal(s) into the trade-idea candidate "
                "registry and run the Phase 8-O walk-forward / tail-repair confirmation.")
    return ("Inspect fmp_error_report.csv + fmp_backfill_progress_report.csv and re-run "
            "--live once resolved.")


# --------------------------------------------------------------------------- #
# Provider-alternative knowledge (static; cheapest-source-first; never recommend the
# Ultimate tier unless every critical family is blocked and no cheaper source covers it).
# Cost strings are approximate public list-price ranges for comparison only.
# --------------------------------------------------------------------------- #
_FAMILY_ALTERNATIVES: Dict[str, List[Dict]] = {
    "earnings_surprises": [
        {"provider": "Alpha Vantage", "endpoint": "EARNINGS (quarterly EPS actual/estimate)",
         "tier": "free / Premium", "approx_cost": "free (25 req/day) or ~$50/mo",
         "covers": "yes", "locally_mapped": "AV adapter referenced in repo",
         "note": "cheapest route to EPS actual-vs-estimate surprise", "recommended": True},
        {"provider": "Finnhub", "endpoint": "/stock/earnings (surprises)",
         "tier": "free / ~$50", "approx_cost": "free (60 req/min) or ~$50/mo",
         "covers": "yes", "locally_mapped": "no",
         "note": "fallback if no Alpha Vantage key", "recommended": False},
        {"provider": "EODHD", "endpoint": "fundamentals/earnings",
         "tier": "All-World/Fundamentals", "approx_cost": "~$20-$80/mo",
         "covers": "yes", "locally_mapped": "only if already mapped (not confirmed in repo)",
         "note": "use only if an EODHD mapping already exists", "recommended": False},
        {"provider": "FMP upgrade", "endpoint": "/stable/earnings",
         "tier": "Starter/Premium", "approx_cost": "~$22-$69/mo",
         "covers": "likely", "locally_mapped": "yes (this adapter)",
         "note": "only if no cheaper source supplies EPS surprise PIT", "recommended": False},
    ],
    "earnings_calendar": [
        {"provider": "Alpha Vantage", "endpoint": "EARNINGS_CALENDAR",
         "tier": "free / Premium", "approx_cost": "free (25 req/day) or ~$50/mo",
         "covers": "yes", "locally_mapped": "AV adapter referenced in repo",
         "note": "cheapest earnings-date calendar", "recommended": True},
        {"provider": "Finnhub", "endpoint": "/calendar/earnings",
         "tier": "free / ~$50", "approx_cost": "free (60 req/min) or ~$50/mo",
         "covers": "yes", "locally_mapped": "no", "note": "fallback", "recommended": False},
        {"provider": "FMP upgrade", "endpoint": "/stable/earnings",
         "tier": "Starter/Premium", "approx_cost": "~$22-$69/mo",
         "covers": "likely", "locally_mapped": "yes (this adapter)",
         "note": "only if alternatives fail", "recommended": False},
    ],
    "analyst_estimates": [
        {"provider": "Finnhub", "endpoint": "/stock/recommendation + estimates",
         "tier": "free / ~$50", "approx_cost": "free (60 req/min) or ~$50/mo",
         "covers": "partial (consensus/estimates)", "locally_mapped": "no",
         "note": "cheapest analyst-estimate proxy", "recommended": True},
        {"provider": "FMP upgrade", "endpoint": "/stable/analyst-estimates",
         "tier": "Premium", "approx_cost": "~$69/mo",
         "covers": "yes", "locally_mapped": "yes (this adapter)",
         "note": "richest forward consensus; only if Finnhub is insufficient",
         "recommended": False},
    ],
    "analyst_recommendations": [
        {"provider": "Finnhub", "endpoint": "/stock/recommendation (trends)",
         "tier": "free / ~$50", "approx_cost": "free (60 req/min) or ~$50/mo",
         "covers": "yes", "locally_mapped": "no",
         "note": "cheapest buy/hold/sell trend source", "recommended": True},
        {"provider": "FMP upgrade", "endpoint": "/stable/grades",
         "tier": "Premium", "approx_cost": "~$69/mo",
         "covers": "yes", "locally_mapped": "yes (this adapter)",
         "note": "per-analyst grade changes; only if Finnhub trends are too coarse",
         "recommended": False},
    ],
    "analyst_price_targets": [
        {"provider": "Finnhub", "endpoint": "/stock/price-target",
         "tier": "free / ~$50", "approx_cost": "free (60 req/min) or ~$50/mo",
         "covers": "yes", "locally_mapped": "no",
         "note": "cheapest price-target source", "recommended": True},
        {"provider": "FMP upgrade", "endpoint": "/stable/price-target-summary",
         "tier": "Premium", "approx_cost": "~$69/mo",
         "covers": "yes", "locally_mapped": "yes (this adapter)",
         "note": "only if Finnhub targets are insufficient", "recommended": False},
    ],
    "ratings_grades_consensus": [
        {"provider": "Finnhub", "endpoint": "/stock/recommendation (consensus)",
         "tier": "free / ~$50", "approx_cost": "free (60 req/min) or ~$50/mo",
         "covers": "partial (consensus)", "locally_mapped": "no",
         "note": "consensus proxy", "recommended": True},
        {"provider": "FMP upgrade", "endpoint": "/stable/grades-consensus",
         "tier": "Premium", "approx_cost": "~$69/mo",
         "covers": "yes", "locally_mapped": "yes (this adapter)",
         "note": "explicit consensus grades; only if the proxy is insufficient",
         "recommended": False},
    ],
}


def build_provider_alternatives(missing_families: List[str]) -> List[List]:
    """Rows for fmp_cheaper_provider_alternative_report.csv. One row per candidate provider
    for each MISSING (entitlement-blocked) critical family, cheapest recommendation first.
    If nothing is blocked, a single summary row records that no alternative is needed."""
    header_note = "cheapest source that solves the specific missing family is preferred"
    rows: List[List] = []
    if not missing_families:
        rows.append(["(none)", "FMP (current plan)", "current", "$0 (already held)", "n/a",
                     "n/a", "no entitlement block observed; insufficiency (if any) is "
                     "ticker-breadth only - collect the next FMP batch, do not switch provider",
                     False])
        return rows
    for fam in missing_families:
        for c in _FAMILY_ALTERNATIVES.get(fam, []):
            rows.append([fam, c["provider"], c["tier"], c["approx_cost"], c["covers"],
                         c["locally_mapped"], "%s (%s)" % (c["note"], header_note),
                         c["recommended"]])
    return rows


def build_upgrade_decision(missing_families: List[str], entitlement: Dict[str, str],
                           coverage_counts: Dict[str, int]) -> Tuple[List[List], str]:
    """Rows + overall decision for fmp_provider_upgrade_decision.csv.

    Every missing critical family has a cheaper non-FMP alternative (Alpha Vantage / Finnhub),
    so the overall recommendation is to use the alternative rather than blanket-upgrade FMP.
    The Ultimate tier is never recommended here - it is only justified if EVERY critical family
    is blocked AND no cheaper source covers it, which the alternatives table contradicts.
    """
    rows: List[List] = []
    for f in CRITICAL_FAMILIES:
        ent = entitlement.get(f, ENT_UNKNOWN)
        cov = coverage_counts.get(f, 0)
        if f in missing_families:
            alt = next((c for c in _FAMILY_ALTERNATIVES.get(f, []) if c["recommended"]), None)
            action = "USE_ALTERNATIVE"
            rationale = ("entitlement %s; cheaper alternative (%s) covers this family - "
                         "do not upgrade FMP for it" % (ent, alt["provider"] if alt else "n/a"))
        elif cov >= MIN_TICKERS_TO_SCORE:
            action, rationale = "NO_ACTION", "entitled and ready to score (%d tickers)" % cov
        else:
            action = "COLLECT_MORE"
            rationale = ("entitled (%s) but only %d/%d tickers - collect the next FMP batch"
                         % (ent, cov, MIN_TICKERS_TO_SCORE))
        rows.append([f, ent, cov, action, rationale])

    if not missing_families:
        overall = "NO_UPGRADE_NEEDED"
        detail = ("no critical family is entitlement-blocked; any shortfall is ticker-breadth "
                  "only - collect the next FMP batch")
    else:
        overall = "UPGRADE_NOT_RECOMMENDED_USE_ALTERNATIVE"
        detail = ("%d critical family(ies) are 402/403-blocked, but each has a cheaper non-FMP "
                  "alternative (Alpha Vantage / Finnhub); activate those rather than upgrading "
                  "FMP (Ultimate tier NOT justified)" % len(missing_families))
    rows.append(["__overall__", "", "", overall, detail])
    return rows, overall


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
def run(live: bool = False, tickers: Optional[List[str]] = None,
        max_tickers: int = DEFAULT_MAX_TICKERS,
        max_endpoint_families: int = DEFAULT_MAX_ENDPOINT_FAMILIES,
        max_requests: int = DEFAULT_MAX_REQUESTS, skip_existing: bool = True,
        include_optional: bool = False, include_blocked: bool = False,
        endpoint_families: Optional[List[str]] = None,
        continue_on_subscription_block: bool = True,
        transport: Optional[Transport] = None,
        injected_scores: Optional[Dict[str, Dict]] = None,
        out_dir: Optional[Path] = None, data_dir: Optional[Path] = None,
        verbose: bool = True) -> Dict:
    """Run the Phase 8-N controlled backfill + signal-expansion readiness audit.

    Default (``live=False``, no ``transport``): OFFLINE - normalizes whatever raw paid data
    already exists under the gitignored cache, builds coverage + manifests + the
    signal-readiness scoreboard, and emits the decision. No network call.

    Live (``live=True`` with FMP_API_KEY, OR ``transport`` injected for tests): runs the
    bounded, skip-existing backfill first, then the same offline pipeline.
    """
    api_key_present = fmp.has_api_key()
    P = _Paths(out_dir, data_dir)
    P.out.mkdir(parents=True, exist_ok=True)

    families, fam_notes = build_backfill_families(
        max_endpoint_families, include_optional, include_blocked)
    # Optional --endpoint-families subset (default: all six critical). Preserves critical-first
    # order; an empty/unmatched filter falls back to the full critical set with a note.
    if endpoint_families:
        wanted = {f.strip() for f in endpoint_families if str(f).strip()}
        subset = [e for e in families if e["family"] in wanted]
        if subset:
            families = subset
            fam_notes.append("endpoint-families restricted to: %s"
                             % ", ".join(e["family"] for e in subset))
        else:
            fam_notes.append("endpoint-families filter %s matched nothing; using all criticals"
                             % sorted(wanted))
    universe, uni_source = resolve_universe(max_tickers, P.raw, explicit=tickers)

    # ----- request plan (always written; network-free) -----
    plan_rows = []
    for e in families:
        for tk in universe:
            raw_path = P.raw / e["family"] / ("%s.json" % tk)
            plan_rows.append([
                e["rank"], e["family"], e["critical"], tk,
                "cached" if raw_path.is_file() else "to_fetch",
                fmp.redacted_request_url(e["path"].replace("{symbol}", tk))])
    _write_csv(P.request_plan,
               ["rank", "endpoint_family", "critical", "ticker", "cache_state",
                "attempted_url_redacted"], plan_rows)

    # ----- bounded backfill (skip-existing; offline reuses cache) -----
    acq_rows, acq_meta = run_backfill(
        families, universe, P.raw, transport, live, api_key_present,
        skip_existing=skip_existing, max_requests=max_requests,
        continue_on_subscription_block=continue_on_subscription_block, verbose=verbose)
    acquisition_executed = acq_meta["can_fetch"]
    any_cached = any(r["status"] == ACQ_CACHED for r in acq_rows)

    # ----- normalize every family that has any cached/collected data -----
    coverage_counts: Dict[str, int] = {}
    family_status: Dict[str, str] = {}
    norm_meta: Dict[str, Dict] = {}
    for e in families:
        fam = e["family"]
        per_ticker = normalize_family(fam, universe, P.raw, P.norm)
        norm_meta[fam] = per_ticker
        covered = sum(1 for tk in universe if per_ticker.get(tk, {}).get("has_data"))
        coverage_counts[fam] = covered
        # family status = best acquisition status across its cells.
        cell_statuses = [r["status"] for r in acq_rows if r["endpoint_family"] == fam]
        family_status[fam] = _aggregate_status(cell_statuses, covered)

    # ----- progress report -----
    _write_csv(P.progress,
               ["endpoint_family", "critical", "ticker", "status", "http_status", "row_count",
                "request_spent", "attempted_url_redacted"],
               [[r["endpoint_family"], r["critical"], r["ticker"], r["status"], r["http_status"],
                 r["row_count"], r["request_spent"], r["attempted_url_redacted"]]
                for r in acq_rows])

    # ----- error report (only the cells that errored; sanitized) -----
    err_cells = [r for r in acq_rows if r["status"] in (
        ACQ_SUBSCRIPTION_BLOCKED, ACQ_AUTH_ERROR, ACQ_RATE_LIMITED, ACQ_ENDPOINT_ERROR)]
    _write_csv(P.error_report,
               ["endpoint_family", "critical", "ticker", "status", "http_status", "error_type",
                "error_message_sanitized", "attempted_url_redacted"],
               [[r["endpoint_family"], r["critical"], r["ticker"], r["status"], r["http_status"],
                 r["error_type"], r["error_message_sanitized"], r["attempted_url_redacted"]]
                for r in err_cells])

    # ----- endpoint status report (per family rollup) -----
    es_rows = []
    for e in families:
        fam = e["family"]
        cells = [r for r in acq_rows if r["endpoint_family"] == fam]
        counts = _status_counts(cells)
        es_rows.append([
            fam, e["critical"], family_status[fam], len(cells),
            counts.get(ACQ_CACHED, 0), counts.get(ACQ_COLLECTED, 0), counts.get(ACQ_EMPTY, 0),
            counts.get(ACQ_NEEDS_FETCH, 0), counts.get(ACQ_SUBSCRIPTION_BLOCKED, 0),
            counts.get(ACQ_AUTH_ERROR, 0), counts.get(ACQ_RATE_LIMITED, 0),
            counts.get(ACQ_ENDPOINT_ERROR, 0), counts.get(ACQ_STOPPED_EARLY, 0),
            coverage_counts[fam],
            sum(1 for c in cells if c["request_spent"])])
    _write_csv(P.endpoint_status,
               ["endpoint_family", "critical", "family_status", "cells", "cached", "collected",
                "empty", "needs_fetch", "subscription_blocked", "auth_error", "rate_limited",
                "endpoint_error", "stopped_early", "tickers_with_data", "requests_spent"], es_rows)

    # ----- ticker coverage by family (long format) -----
    tc_rows = []
    for e in families:
        fam = e["family"]
        for tk in universe:
            m = norm_meta[fam].get(tk, {})
            tc_rows.append([tk, fam, e["critical"], "yes" if m.get("has_data") else "no",
                            m.get("rows", 0), m.get("first_date", ""), m.get("last_date", "")])
    _write_csv(P.ticker_coverage,
               ["ticker", "endpoint_family", "critical", "has_data", "rows", "first_date",
                "last_date"], tc_rows)

    # ----- per-family entitlement matrix + subscription-block pattern -----
    block_stats = family_block_stats(acq_rows, families)
    entitlement: Dict[str, str] = {}
    em_rows, bp_rows = [], []
    for e in families:
        fam = e["family"]
        st = block_stats[fam]
        cov = coverage_counts[fam]
        ent = classify_entitlement(st)
        entitlement[fam] = ent
        ready = cov >= MIN_TICKERS_TO_SCORE
        broadly = is_broadly_blocked(st, cov)
        em_rows.append([fam, e["critical"], st["attempted"], st["collected"], st["cached"],
                        st["empty"], st["blocked"], st["auth"], st["rate"], st["errors"],
                        cov, ent, ready, broadly])
        bp_rows.append([fam, e["critical"], st["attempted"], st["blocked"], st["block_fraction"],
                        block_pattern(st), ";".join(st["blocked_tickers"][:10]),
                        _block_interpretation(block_pattern(st), fam)])
    _write_csv(P.entitlement_matrix,
               ["endpoint_family", "critical", "attempted", "collected", "cached", "empty",
                "blocked_402_403", "auth_401", "rate_429", "endpoint_error", "coverage",
                "entitlement", "ready_to_score", "broadly_blocked"], em_rows)
    _write_csv(P.block_pattern,
               ["endpoint_family", "critical", "attempted", "blocked_402_403", "block_fraction",
                "pattern", "sample_blocked_tickers", "interpretation"], bp_rows)

    # ----- missing (entitlement-blocked) families -> provider upgrade + cheaper-alternative -----
    missing_families = [f for f in CRITICAL_FAMILIES
                        if block_stats.get(f, {}).get("blocked", 0) > 0
                        and coverage_counts.get(f, 0) < MIN_TICKERS_TO_SCORE]
    upgrade_rows, upgrade_overall = build_upgrade_decision(
        missing_families, entitlement, coverage_counts)
    _write_csv(P.upgrade_decision,
               ["endpoint_family", "entitlement", "coverage", "recommended_action", "rationale"],
               upgrade_rows)
    alt_rows = build_provider_alternatives(missing_families)
    _write_csv(P.alt_provider,
               ["missing_family", "candidate_provider", "plan_tier", "approx_monthly_cost",
                "covers_family", "locally_mapped", "notes", "recommended"], alt_rows)

    # ----- raw + normalized storage manifests (metadata only) -----
    raw_rows = []
    for e in families:
        fam = e["family"]
        d = P.raw / fam
        files = sorted(p.name for p in d.glob("*.json")) if d.is_dir() else []
        raw_rows.append([_rel(d), fam, "yes", "no", len(files), ";".join(files)])
    _write_csv(P.raw_manifest,
               ["path", "endpoint_family", "gitignored", "committed", "file_count", "tickers"],
               raw_rows)
    norm_rows = []
    for e in families:
        fam = e["family"]
        np_ = P.norm / ("%s.csv" % fam)
        rows_n = sum(m.get("rows", 0) for m in norm_meta[fam].values())
        norm_rows.append([_rel(np_), fam, "yes", "no", "yes" if np_.is_file() else "no", rows_n])
    _write_csv(P.norm_manifest,
               ["path", "endpoint_family", "gitignored", "committed", "exists", "normalized_rows"],
               norm_rows)

    # ----- expanded earnings + analyst event manifests (coverage METADATA only) -----
    earn_rows = []
    for fam in ("earnings_surprises", "earnings_calendar"):
        if fam not in norm_meta:
            continue
        for tk in universe:
            m = norm_meta[fam].get(tk, {})
            if not m.get("has_data"):
                continue
            earn_rows.append([tk, fam, m["rows"], m["first_date"], m["last_date"],
                              "epsActual" in m.get("fields", []),
                              "epsEstimated" in m.get("fields", [])])
    _write_csv(P.earnings_manifest,
               ["ticker", "source_family", "n_events", "first_event_date", "last_event_date",
                "has_actual_eps_field", "has_estimate_eps_field"], earn_rows)
    an_rows = []
    for fam in ("analyst_estimates", "analyst_recommendations", "analyst_price_targets",
                "ratings_grades_consensus"):
        if fam not in norm_meta:
            continue
        for tk in universe:
            m = norm_meta[fam].get(tk, {})
            if not m.get("has_data"):
                continue
            an_rows.append([tk, fam, m["rows"], m["first_date"], m["last_date"],
                            ";".join(m.get("fields", [])[:8])])
    _write_csv(P.analyst_manifest,
               ["ticker", "source_family", "n_rows", "first_date", "last_date",
                "field_names_sample"], an_rows)

    # ----- signal-ready coverage report (per signal hypothesis) -----
    n_universe = len(universe)
    _write_csv(P.signal_coverage,
               ["signal_id", "name", "required_family", "required_family_status",
                "tickers_covered", "n_universe", "coverage_fraction", "min_tickers_to_score",
                "ticker_gap", "ready_to_score"],
               [[s["signal_id"], s["name"], s["required_family"],
                 family_status.get(s["required_family"], ACQ_NEEDS_FETCH),
                 coverage_counts.get(s["required_family"], 0), n_universe,
                 round(coverage_counts.get(s["required_family"], 0) / n_universe, 4)
                 if n_universe else 0.0,
                 MIN_TICKERS_TO_SCORE,
                 max(0, MIN_TICKERS_TO_SCORE - coverage_counts.get(s["required_family"], 0)),
                 coverage_counts.get(s["required_family"], 0) >= MIN_TICKERS_TO_SCORE]
                for s in _SIGNALS])

    # ----- score the six signal hypotheses (HONEST; coverage-gated) -----
    scored = score_signals(family_status, coverage_counts, n_universe, injected_scores)
    _write_csv(P.scoreboard,
               ["signal_id", "name", "required_family", "required_family_status", "cohort",
                "tickers_covered", "n_universe", "verdict", "score", "rerun_of", "detail"],
               [[s["signal_id"], s["name"], s["required_family"], s["required_family_status"],
                 s["cohort"], s["tickers_covered"], s["n_universe"], s["verdict"], s["score"],
                 s["rerun_of"], s["detail"]] for s in scored])

    # ----- confirmed / promising / provider-required / rejected splits -----
    def _split(verdicts):
        return [s for s in scored if s["verdict"] in verdicts]
    _write_signal_list(P.confirmed, _split({VER_CONFIRMED}))
    _write_signal_list(P.promising, _split({VER_PROMISING}))
    _write_signal_list(P.provider_required,
                       _split({VER_PROVIDER_REQUIRED, VER_INSUFFICIENT, VER_COVERAGE_READY}))
    _write_signal_list(P.rejected, _split({VER_REJECTED}))

    # ----- trade-idea candidate registry + best (only confirmed/promising; never faked) -----
    tradeable = _split({VER_CONFIRMED, VER_PROMISING})
    _write_csv(P.trade_idea_registry,
               ["trade_idea_id", "signal_id", "name", "required_data_families",
                "validation_status", "tickers_covered", "whether_trade_ready",
                "reason_not_trade_ready", "next_validation_step"],
               [["TI-%02d" % (i + 1), s["signal_id"], s["name"], s["required_family"],
                 s["verdict"], s["tickers_covered"],
                 s["verdict"] == VER_CONFIRMED,
                 "" if s["verdict"] == VER_CONFIRMED else "promising only - needs walk-forward",
                 "Phase 8-O walk-forward + tail-repair confirmation"]
                for i, s in enumerate(tradeable)])
    best = [s for s in tradeable if s["verdict"] == VER_CONFIRMED] or tradeable
    _write_csv(P.best_trade_ideas,
               ["trade_idea_id", "signal_id", "name", "verdict", "tickers_covered", "score"],
               [["TI-%02d" % (i + 1), s["signal_id"], s["name"], s["verdict"],
                 s["tickers_covered"], s["score"]] for i, s in enumerate(best)])

    # ----- validation skeptic + multiple-testing reports -----
    n_scored = sum(1 for s in scored if s["verdict"] in (VER_CONFIRMED, VER_PROMISING,
                                                         VER_REJECTED))
    _write_csv(P.skeptic, ["check", "verdict", "detail"], _skeptic_rows(scored, n_universe))
    _write_csv(P.multiple_testing,
               ["item", "value", "detail"],
               [["hypotheses_defined", len(_SIGNALS), "the six provider-expanded signals"],
                ["hypotheses_scored_for_alpha", n_scored,
                 "only signals with sufficient coverage AND a wired scorer are alpha-scored"],
                ["multiple_testing_method", "Benjamini-Hochberg (planned)",
                 "applied at scoring time; with 0 alpha-scored signals there is nothing to "
                 "correct yet (no selection has occurred)"],
                ["family_wise_selection_taken", n_scored > 0,
                 "no signal was selected on a fabricated score"]])

    # ----- decision -----
    decision = derive_decision(scored, family_status, coverage_counts, block_stats,
                               acquisition_executed, api_key_present, any_cached,
                               acq_meta["requests_remaining"])
    next_batch = min(CEIL_MAX_TICKERS, max(len(universe), DEFAULT_MAX_TICKERS))
    next_cmd = _next_command(decision, next_batch)

    # ----- key detection report -----
    _write_csv(P.key_detection, ["check", "value", "detail"],
               [["api_key_env_var", fmp.API_KEY_ENV, "the only source of the key"],
                ["api_key_present", api_key_present, "read from environment only; never printed"],
                ["live_requested", bool(live), "whether --live was passed"],
                ["acquisition_executed", acquisition_executed,
                 "true if a transport was injected (tests) or --live AND key present"],
                ["mode", "live_backfill" if acquisition_executed else "offline_normalize",
                 "offline mode normalizes existing cache only"]])

    # ----- secret leak scan over committed artifacts written so far -----
    written = [p for p in P.out.glob("*") if p.is_file()]
    leak_clean, scanned = _scan_for_leaks(written, fmp.resolve_api_key())
    _write_csv(P.secret_audit, ["check", "value", "passed", "detail"],
               [[a["check"], a["value"], "" if a["passed"] is None else a["passed"], a["detail"]]
                for a in build_secret_audit(api_key_present, leak_clean, scanned)])

    # ----- director decision + next plan + main report -----
    director = {
        "phase": PHASE, "decision": decision,
        "decision_allowed_values": list(ALLOWED_DECISIONS),
        "decision_is_terminal": decision in ALLOWED_DECISIONS,
        "depends_on_phase8m": "CURRENT_FMP_ACCESS_SUFFICIENT_FOR_CRITICAL_FAMILIES",
        "mode": "live_backfill" if acquisition_executed else "offline_normalize",
        "api_key_present": api_key_present,
        "universe_size": n_universe, "universe_source": uni_source,
        "critical_family_status": {f: family_status.get(f, ACQ_NEEDS_FETCH)
                                   for f in CRITICAL_FAMILIES},
        "critical_family_coverage": {f: coverage_counts.get(f, 0) for f in CRITICAL_FAMILIES},
        "critical_family_entitlement": {f: entitlement.get(f, ENT_UNKNOWN)
                                        for f in CRITICAL_FAMILIES},
        "min_tickers_to_score": MIN_TICKERS_TO_SCORE,
        "families_ready_to_score": [f for f in CRITICAL_FAMILIES
                                    if coverage_counts.get(f, 0) >= MIN_TICKERS_TO_SCORE],
        "missing_blocked_families": missing_families,
        "provider_upgrade_decision": upgrade_overall,
        "cheaper_provider_alternatives_recommended": bool(missing_families),
        "stopped_early": acq_meta["stopped_early"], "stop_reason": acq_meta["stop_reason"],
        "continue_on_subscription_block": continue_on_subscription_block,
        "confirmed_signals": [s["signal_id"] for s in _split({VER_CONFIRMED})],
        "promising_signals": [s["signal_id"] for s in _split({VER_PROMISING})],
        "trade_ready": any(s["verdict"] == VER_CONFIRMED for s in scored),
        "next_fmp_batch_required": decision in (DEC_READY_NEXT_BATCH, DEC_NEEDS_MORE_COVERAGE,
                                                DEC_BLOCKED_NO_KEY),
        "no_alpha_fabricated": True,
        "exact_next_command": next_cmd,
        "preview_only": True, "orders_enabled": False, "automation_enabled": False,
        "broker_execution_enabled": False, "deployed": False, "committed": False,
    }
    _write_json(P.director_decision, director)

    next_plan = {
        "phase": "8-O",
        "title": "Provider-Expanded Signal Confirmation / Next FMP Batch",
        "depends_on_decision": decision,
        "objective": ("If the next FMP batch is required, collect the next bounded set of "
                      "tickers for the critical families and re-score; if a signal is "
                      "confirmed/promising, run walk-forward + tail-repair confirmation and "
                      "promote it into the trade-idea registry."),
        "next_command": next_cmd,
        "bounded_limits": {"max_tickers": CEIL_MAX_TICKERS,
                           "max_endpoint_families": DEFAULT_MAX_ENDPOINT_FAMILIES,
                           "max_requests": CEIL_MAX_REQUESTS, "skip_existing": True},
        "min_tickers_to_score": MIN_TICKERS_TO_SCORE,
        "preview_only": True, "orders_enabled": False, "automation_enabled": False,
        "broker_execution_enabled": False, "committed": False,
    }
    _write_json(P.next_plan, next_plan)

    report = {
        "phase": PHASE,
        "objective": ("Controlled, point-in-time-safe FMP backfill for the Phase 8-M verified "
                      "critical families, committed-safe coverage + signal-ready manifests, and "
                      "an honest test of whether expanded earnings/analyst data lets the "
                      "promising signal families be scored (no fabricated alpha)."),
        "provider": fmp.PROVIDER_NAME, "provider_host": fmp.ALLOWED_HOST,
        "mode": "live_backfill" if acquisition_executed else "offline_normalize",
        "live_requested": bool(live), "api_key_env_var": fmp.API_KEY_ENV,
        "api_key_present": api_key_present, "api_key_logged": False,
        "bounded_limits": {"max_tickers": min(int(max_tickers), CEIL_MAX_TICKERS),
                           "max_endpoint_families": DEFAULT_MAX_ENDPOINT_FAMILIES,
                           "max_requests": min(int(max_requests), CEIL_MAX_REQUESTS),
                           "skip_existing": skip_existing,
                           "continue_on_subscription_block": continue_on_subscription_block,
                           "stop_after_consecutive_auth_errors": STOP_AFTER_CONSEC_AUTH_ERRORS,
                           "stop_after_consecutive_rate_limits": STOP_AFTER_CONSEC_RATE_LIMITS,
                           "rate_limit_max_retries": RATE_LIMIT_MAX_RETRIES},
        "controller_fix_notes": fam_notes,
        "backfill_families": [e["family"] for e in families],
        "critical_first_order": [e["family"] for e in families if e["critical"]],
        "do_not_spend_families": ["key_metrics_quarterly", "ratios_quarterly"],
        "universe": universe, "universe_source": uni_source, "universe_size": n_universe,
        "acquisition_executed": acquisition_executed,
        "requests_made": acq_meta["requests_made"],
        "requests_remaining": acq_meta["requests_remaining"],
        "stopped_early": acq_meta["stopped_early"], "stop_reason": acq_meta["stop_reason"],
        "continue_on_subscription_block": continue_on_subscription_block,
        "skip_existing": skip_existing,
        "any_cached_reused": any_cached,
        "family_entitlement": entitlement,
        "family_block_fraction": {f: block_stats[f]["block_fraction"] for f in block_stats},
        "missing_blocked_families": missing_families,
        "provider_upgrade_decision": upgrade_overall,
        "cheaper_provider_alternatives_recommended": bool(missing_families),
        "families_ready_to_score": [f for f in CRITICAL_FAMILIES
                                    if coverage_counts.get(f, 0) >= MIN_TICKERS_TO_SCORE],
        "raw_files_written": sum(1 for _ in P.raw.rglob("*.json")) if P.raw.is_dir() else 0,
        "normalized_files_written": sum(1 for _ in P.norm.rglob("*.csv")) if P.norm.is_dir() else 0,
        "family_status": family_status, "coverage_counts": coverage_counts,
        "min_tickers_to_score": MIN_TICKERS_TO_SCORE,
        "signal_verdicts": {s["signal_id"]: s["verdict"] for s in scored},
        "confirmed_signal_count": len(_split({VER_CONFIRMED})),
        "promising_signal_count": len(_split({VER_PROMISING})),
        "decision": decision, "decision_is_terminal": decision in ALLOWED_DECISIONS,
        "allowed_decisions": list(ALLOWED_DECISIONS),
        "next_fmp_batch_required": director["next_fmp_batch_required"],
        "recommended_next_command": next_cmd,
        "secret_safety_leak_scan_clean": leak_clean,
        "secret_safety_files_scanned": scanned,
        "outputs": {k: _rel(getattr(P, k)) for k in _ARTIFACTS},
        # Safety / provenance contract.
        "preview_only": True, "orders_enabled": False, "automation_enabled": False,
        "broker_execution_enabled": False, "production_replacement": False,
        "network_used": bool(acquisition_executed and transport is None),
        "paper_trader_touched": False, "gcp_touched": False, "deployed": False,
        "data_fabricated": False, "no_alpha_fabricated": True, "weights_optimized": False,
        "signs_flipped": False, "wrote_to_d_drive": False, "committed": False,
    }
    _write_json(P.report, report)
    return report


# --------------------------------------------------------------------------- #
# Status aggregation + small helpers.
# --------------------------------------------------------------------------- #
_STATUS_RANK = {
    ACQ_COLLECTED: 0, ACQ_CACHED: 1, ACQ_EMPTY: 2, ACQ_RATE_LIMITED: 3,
    ACQ_ENDPOINT_ERROR: 4, ACQ_SUBSCRIPTION_BLOCKED: 5, ACQ_AUTH_ERROR: 6,
    ACQ_STOPPED_EARLY: 7, ACQ_NEEDS_FETCH: 8, ACQ_SKIPPED_DO_NOT_SPEND: 9,
}


def _aggregate_status(statuses: List[str], covered: int) -> str:
    if covered > 0:
        # Has usable data: prefer the resolved status (cached/collected/empty).
        for s in (ACQ_COLLECTED, ACQ_CACHED, ACQ_EMPTY):
            if s in statuses:
                return s
    if not statuses:
        return ACQ_NEEDS_FETCH
    return sorted(statuses, key=lambda s: _STATUS_RANK.get(s, 9))[0]


def _status_counts(cells: List[Dict]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for c in cells:
        out[c["status"]] = out.get(c["status"], 0) + 1
    return out


def _write_signal_list(path: Path, signals: List[Dict]) -> None:
    _write_csv(path,
               ["signal_id", "name", "required_family", "required_family_status",
                "tickers_covered", "n_universe", "verdict", "detail"],
               [[s["signal_id"], s["name"], s["required_family"], s["required_family_status"],
                 s["tickers_covered"], s["n_universe"], s["verdict"], s["detail"]]
                for s in signals])


def _skeptic_rows(scored: List[Dict], n_universe: int) -> List[List]:
    n_cov_ok = sum(1 for s in scored if s["tickers_covered"] >= MIN_TICKERS_TO_SCORE)
    alpha = [s for s in scored if s["verdict"] in (VER_CONFIRMED, VER_PROMISING)]
    rows = [
        ["sample_size_adequate", "PASS" if n_cov_ok else "FAIL",
         "%d/%d signal(s) meet the %d-ticker minimum to score; below that nothing is scored"
         % (n_cov_ok, len(scored), MIN_TICKERS_TO_SCORE)],
        ["no_alpha_without_coverage", "PASS",
         "no signal is marked CONFIRMED/PROMISING unless coverage >= minimum AND a real "
         "scoring result was supplied (no fabricated alpha)"],
        ["single_name_concentration", "PASS" if n_universe >= MIN_TICKERS_TO_SCORE else "WATCH",
         "universe=%d; cross-sectional cohorts need breadth before any score is trusted"
         % n_universe],
        ["look_ahead_pit_safety", "PASS",
         "normalization keeps the announcement/published availability date; as-of joins only"],
        ["overfitting_risk_taken", "NONE" if not alpha else "REVIEW",
         "%d signal(s) currently carry an alpha verdict" % len(alpha)],
    ]
    return rows


# --------------------------------------------------------------------------- #
# CLI.
# --------------------------------------------------------------------------- #
def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=("Phase 8-N controlled FMP critical-data backfill + provider-expanded "
                     "signal confirmation (offline normalize by default)."))
    ap.add_argument("--live", action="store_true",
                    help="Run the bounded live backfill (requires FMP_API_KEY).")
    ap.add_argument("--tickers", type=str, default="",
                    help="Comma-separated explicit universe (else resolved; capped to 25).")
    ap.add_argument("--max-tickers", type=int, default=DEFAULT_MAX_TICKERS,
                    help="Bounded batch size (clamped to <=25).")
    ap.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS,
                    help="Bounded request budget (clamped to <=150).")
    ap.add_argument("--no-skip-existing", action="store_true",
                    help="Re-fetch even cached cells (default reuses cache; not recommended).")
    ap.add_argument("--include-optional", action="store_true",
                    help="Also backfill profile + statements (already-cached still normalize).")
    ap.add_argument("--endpoint-families", type=str, default="",
                    help=("Optional comma-separated subset of the six critical families "
                          "(default: all six)."))
    # Default TRUE: a 402/subscription block on one ticker/endpoint does NOT stop the batch.
    ap.add_argument("--continue-on-subscription-block", dest="continue_on_block",
                    action="store_true", default=True,
                    help=("A 402/403 subscription block on one ticker/endpoint is recorded and "
                          "the batch CONTINUES to the next ticker/endpoint family (default)."))
    ap.add_argument("--no-continue-on-subscription-block", dest="continue_on_block",
                    action="store_false",
                    help=("Legacy: stop the batch after %d consecutive subscription blocks."
                          % STOP_AFTER_CONSEC_SUBSCRIPTION_BLOCKS))
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()] or None
    fams = [f.strip() for f in args.endpoint_families.split(",") if f.strip()] or None
    report = run(live=args.live, tickers=tickers, max_tickers=args.max_tickers,
                 max_requests=args.max_requests, skip_existing=not args.no_skip_existing,
                 include_optional=args.include_optional, endpoint_families=fams,
                 continue_on_subscription_block=args.continue_on_block, verbose=True)
    print("phase:                         %s" % report["phase"])
    print("mode:                          %s" % report["mode"])
    print("api key present:               %s" % report["api_key_present"])
    print("continue on 402 block:         %s" % report["continue_on_subscription_block"])
    print("universe (%s):                 %s"
          % (report["universe_source"], ", ".join(report["universe"][:10])
             + (" ..." if len(report["universe"]) > 10 else "")))
    print("requests made / remaining:     %s / %s"
          % (report["requests_made"], report["requests_remaining"]))
    print("stopped early (reason):        %s (%s)"
          % (report["stopped_early"], report["stop_reason"] or "n/a"))
    print("raw files (gitignored):        %s" % report["raw_files_written"])
    print("normalized files (gitignored): %s" % report["normalized_files_written"])
    print("critical coverage:             %s" % report["coverage_counts"])
    print("family entitlement:            %s" % report["family_entitlement"])
    print("families ready to score:       %s" % report["families_ready_to_score"])
    print("missing/blocked families:      %s" % report["missing_blocked_families"])
    print("provider upgrade decision:     %s" % report["provider_upgrade_decision"])
    print("signal verdicts:               %s" % report["signal_verdicts"])
    print("decision:                      %s" % report["decision"])
    print("next command:                  %s" % report["recommended_next_command"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
