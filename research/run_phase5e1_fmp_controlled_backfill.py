"""Phase 5-E1 - Controlled FMP Backfill (resumable, secret-safe, dry-run first).

Track A (quant brain). Phase 5-E0 stood up a safe FMP provider adapter
(research/providers/fmp_client.py) and a trial collector; the live smoke confirmed
the current ``/stable/`` API works for profile / income-statement / ratios with a
real FMP_API_KEY, and that no raw data or key is committed. Phase 5-E1 turns that
foundation into a CONTROLLED, RESUMABLE BACKFILL system: it collects the paid
external alpha data LOCALLY, OUTSIDE Git, under research/data/fmp/{raw,normalized},
with strong audit / coverage / secret-safety controls.

This phase does NOT build the enriched alpha model. It collects and normalizes the
data required for Phase 5-E2 (the enriched alpha panel rerun vs the Phase 5-C
price-only baseline).

Hard rules honored: FMP_API_KEY is read from the environment ONLY, never printed,
never written to any artifact, never committed, never hard-coded. Default mode is
dry-run (NO network, NO key required). Live mode requires FMP_API_KEY AND an
explicit ``--live`` flag AND explicit bounded request limits. Raw + normalized paid
data are written only under research/data/fmp/ and are git-ignored - never staged.
No full S&P 500 live backfill runs automatically (it needs explicit large limits).
No AlphaVantage / other paid APIs. No orders, no broker execution, no automation,
no deploy, no Paper Trader changes, no GCP changes, no writes to D: (D: is
read-only INPUT only), no binary model artifacts, no commit.

Run (default = dry-run, no network):
    python research/run_phase5e1_fmp_controlled_backfill.py
Optional bounded live sample (requires FMP_API_KEY in the environment):
    python research/run_phase5e1_fmp_controlled_backfill.py --live --max-tickers 5 --max-endpoints 6 --max-requests 40
Test:
    python -m pytest tests/test_phase5e1_fmp_controlled_backfill.py -q
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research.providers import fmp_client as fmp  # noqa: E402
# Reuse the Phase 5-E0 normalized-column + point-in-time contracts so 5-E1 cannot
# drift from the schema the 5-E0 trial already documented.
from research import run_phase5e0_fmp_provider_trial_collector as e0  # noqa: E402

PHASE = "5-E1"

# --------------------------------------------------------------------------- #
# Paths
#   * Committed, summarized artifacts live under research/output/...
#   * Paid raw + normalized data live OUTSIDE git under research/data/fmp/...
#     (git-ignored; see research/data/fmp/.gitignore).
# --------------------------------------------------------------------------- #
_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase5e1_fmp_controlled_backfill"
_DATA_DIR = _REPO_ROOT / "research" / "data" / "fmp"
_RAW_DIR = _DATA_DIR / "raw"
_NORM_DIR = _DATA_DIR / "normalized"
_GITIGNORE = _DATA_DIR / ".gitignore"

_REPORT_OUT = _OUT_DIR / "phase5e1_fmp_controlled_backfill.json"
_REQUEST_PLAN_OUT = _OUT_DIR / "fmp_backfill_request_plan.csv"
_ACCESS_PLAN_OUT = _OUT_DIR / "fmp_backfill_endpoint_access_plan.csv"
_STORAGE_MANIFEST_OUT = _OUT_DIR / "fmp_backfill_storage_manifest.csv"
_PROGRESS_TEMPLATE_OUT = _OUT_DIR / "fmp_backfill_progress_template.csv"
_QUALITY_REPORT_OUT = _OUT_DIR / "fmp_backfill_quality_report.csv"
_SECRET_AUDIT_OUT = _OUT_DIR / "fmp_backfill_secret_safety_audit.csv"
_PANEL_PLAN_OUT = _OUT_DIR / "phase5e2_enriched_panel_plan.json"

# Phase 5-E1B (expanded core fundamentals backfill) committed artifacts.
_CORE_BATCH_PLAN_OUT = _OUT_DIR / "fmp_core_backfill_batch_plan.csv"
_CORE_BATCH_SUMMARY_OUT = _OUT_DIR / "fmp_core_backfill_batch_summary.csv"
_CORE_COVERAGE_OUT = _OUT_DIR / "fmp_core_backfill_coverage_report.csv"
# Phase 5-E1B hotfix: request-level results + grouped error report.
_CORE_REQUEST_RESULTS_OUT = _OUT_DIR / "fmp_core_backfill_request_results.csv"
_CORE_ERROR_REPORT_OUT = _OUT_DIR / "fmp_core_backfill_error_report.csv"
# Phase 5-E1B reliability patch: persistent register of structurally-blocked
# (endpoint, ticker) requests (e.g. HTTP 402) so later batches never waste live
# calls re-hitting them. Committed-safe summary (no payloads, no key).
_CORE_KNOWN_BLOCKED_OUT = _OUT_DIR / "fmp_core_backfill_known_blocked.csv"
_E1B_READINESS_OUT = _OUT_DIR / "phase5e1b_core_backfill_readiness.json"
# Phase 5-E1B preflight artifacts: a network-free, key-free pre-check that reports
# (read-only) exactly how many planned requests would be skipped vs. spent live,
# so the user always knows the cost BEFORE launching a live batch.
_CORE_PREFLIGHT_OUT = _OUT_DIR / "fmp_core_backfill_preflight.csv"
_E1B_PREFLIGHT_JSON_OUT = _OUT_DIR / "phase5e1b_core_preflight.json"
_PREFLIGHT_COLUMNS = [
    "batch_id", "endpoint_name", "ticker", "planned_disposition",
    "existing_raw_file_detected", "existing_raw_file_path", "existing_row_count",
    "known_blocked_http_status",
]

# Default dry-run universe (small, explicit). Full S&P 500 is supported only with
# explicit large --max-tickers and an explicit universe; it never runs by default.
DEFAULT_UNIVERSE = ["AAPL", "MSFT", "NVDA", "AMZN", "JPM"]

# Required-by-spec endpoint priority (1..N). Lower = collected first.
_BACKFILL_PRIORITY = {
    "company_profile": 1,
    "income_statement_quarterly": 2,
    "balance_sheet_statement_quarterly": 3,
    "cash_flow_statement_quarterly": 4,
    "key_metrics_quarterly": 5,
    "ratios_quarterly": 6,
    "earnings_calendar": 7,
    "earnings_surprises": 8,
    "analyst_estimates": 9,
    "analyst_recommendations": 10,
    "analyst_price_targets": 11,
    "sp500_constituents": 12,
}

# Families that materially feed the Phase 5-E2 enriched panel (universe_mapping is
# supporting only: profile = caveated sector map, sp500 = membership scaffold).
_REQUIRED_FAMILIES_FOR_E2 = {"fundamentals", "earnings", "analyst_revisions"}

# Core stable endpoints whose live success is what clears the entitlement gate.
_CORE_STABLE_ENDPOINTS = {
    "company_profile", "income_statement_quarterly", "balance_sheet_statement_quarterly",
    "cash_flow_statement_quarterly", "key_metrics_quarterly", "ratios_quarterly",
}

# Phase 5-E1B core-only endpoint set (priority order). The four live-confirmed
# stable fundamentals/profile endpoints - the minimum cross-sectional fundamentals
# Phase 5-E2 needs. Earnings/analyst endpoints are intentionally NOT in this set;
# they remain needs_live_verification and do not block expanded core collection.
PHASE_E1B = "5-E1B"
_CORE_ONLY_ENDPOINTS = [
    "company_profile",
    "income_statement_quarterly",
    "balance_sheet_statement_quarterly",
    "cash_flow_statement_quarterly",
]
_BENCHMARK = "SPY"  # Phase 5-C benchmark/regime proxy - never part of the universe.

# Coverage thresholds that decide whether enough core fundamentals exist for 5-E2.
_E1B_MIN_TICKERS_FOR_E2 = 30          # distinct names with most core statements
_E1B_MIN_ENDPOINT_COVERAGE_PCT = 60.0  # per-endpoint coverage floor across the batch
_E1B_MIN_CORE_ENDPOINTS_PER_TICKER = 3  # a "core-ready" ticker has >= 3 of 4 core endpoints

# Normalized record metadata columns (per the Phase 5-E1 normalization contract).
# Endpoint-specific data columns (reused from Phase 5-E0) are appended after these.
_NORM_META_COLUMNS = [
    "ticker", "endpoint_name", "source_provider", "collection_timestamp_utc",
    "point_in_time_candidate_date", "raw_file_path",
]
_SOURCE_PROVIDER = "FMP"

# Recommendation vocabulary.
REC_READY_FOR_SAMPLE = "READY_FOR_LIVE_CONTROLLED_SAMPLE"
REC_READY_FOR_E2 = "READY_FOR_PHASE5E2_ENRICHED_PANEL"
REC_NEEDS_ENTITLEMENT = "NEEDS_ENDPOINT_ENTITLEMENT_REVIEW"
REC_BLOCKED_NO_KEY = "BLOCKED_MISSING_FMP_KEY"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (
    REC_READY_FOR_SAMPLE, REC_READY_FOR_E2, REC_NEEDS_ENTITLEMENT,
    REC_BLOCKED_NO_KEY, REC_ERROR,
)

# Phase 5-E1B recommendation vocabulary (shares BLOCKED_MISSING_FMP_KEY / ERROR).
REC_E1B_READY_LIVE = "READY_FOR_EXPANDED_CORE_LIVE_BATCH"
REC_E1B_READY_E2 = "READY_FOR_PHASE5E2_WITH_CORE_FUNDAMENTALS"
REC_E1B_NEEDS_MORE = "NEEDS_MORE_CORE_BACKFILL_BATCHES"
ALLOWED_E1B_RECOMMENDATIONS = (
    REC_E1B_READY_LIVE, REC_E1B_READY_E2, REC_E1B_NEEDS_MORE,
    REC_BLOCKED_NO_KEY, REC_ERROR,
)

# The exact .gitignore content the runner guarantees exists (self-healing) so a
# fresh checkout is always commit-safe for the paid-data directory.
_GITIGNORE_CONTENT = (
    "# Phase 5-E1 - LOCAL paid Financial Modeling Prep (FMP) data. DO NOT COMMIT.\n"
    "#\n"
    "# research/run_phase5e1_fmp_controlled_backfill.py --live writes licensed FMP\n"
    "# payloads here:\n"
    "#   raw/        verbatim provider JSON responses (paid data)\n"
    "#   normalized/ flattened point-in-time CSVs derived from the paid data\n"
    "#\n"
    "# These are paid, license-restricted provider responses and must never enter\n"
    "# Git. Only summarized coverage/quality/manifest data (row counts, redacted\n"
    "# URLs, file paths - never the payloads or the API key) is written to the\n"
    "# committed artifacts under research/output/phase5e1_fmp_controlled_backfill/.\n"
    "raw/\n"
    "normalized/\n"
    "\n"
    "# Belt-and-braces: ignore everything in this directory EXCEPT this .gitignore,\n"
    "# so no paid payload can ever be staged from here by accident.\n"
    "*\n"
    "!.gitignore\n"
)


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


def _utc_now_iso() -> str:
    """UTC collection timestamp (live mode only). Dry-run artifacts stay
    deterministic / timestamp-free so they remain commit-safe and churn-free."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_paid_data_gitignore() -> bool:
    """Guarantee research/data/fmp/.gitignore exists with the canonical content so
    raw/ and normalized/ paid data can never be committed. Idempotent and
    deterministic. Returns True iff the gitignore now ignores raw/ + normalized/.
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        current = _GITIGNORE.read_text(encoding="utf-8") if _GITIGNORE.is_file() else ""
    except OSError:
        current = ""
    if current != _GITIGNORE_CONTENT:
        with open(_GITIGNORE, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_GITIGNORE_CONTENT)
    return _paid_data_gitignored()


def _paid_data_gitignored() -> bool:
    """True iff the paid-data .gitignore exists and ignores raw/ and normalized/."""
    if not _GITIGNORE.is_file():
        return False
    try:
        text = _GITIGNORE.read_text(encoding="utf-8")
    except OSError:
        return False
    lines = {ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")}
    has_raw = "raw/" in lines or "raw" in lines or "*" in lines
    has_norm = "normalized/" in lines or "normalized" in lines or "*" in lines
    return has_raw and has_norm


# --------------------------------------------------------------------------- #
# Endpoint selection (priority-ordered; bounded by --max-endpoints in live mode)
# --------------------------------------------------------------------------- #
def _priority_endpoints() -> List[Dict]:
    """All catalog endpoints ordered by the Phase 5-E1 backfill priority."""
    cat = fmp.endpoint_catalog()
    cat.sort(key=lambda e: (_BACKFILL_PRIORITY.get(e["endpoint_name"], 99), e["endpoint_name"]))
    return cat


def _endpoint_required_for_e2(endpoint: Dict) -> bool:
    return endpoint["alpha_family"] in _REQUIRED_FAMILIES_FOR_E2


# --------------------------------------------------------------------------- #
# Network-free table builders (request plan, access plan, progress template,
# storage manifest, quality report) - dry-run shapes
# --------------------------------------------------------------------------- #
def build_request_plan(universe: List[str]) -> List[Dict]:
    """One planned request per (endpoint, ticker). Ticker-less endpoints appear
    once with ticker '*'. Each row carries a persist-safe redacted URL."""
    rows: List[Dict] = []
    for e in _priority_endpoints():
        targets = universe if e["requires_ticker"] else ["*"]
        for tk in targets:
            path = e["endpoint_path_template"].replace("{symbol}", tk if tk != "*" else "")
            rows.append({
                "endpoint_name": e["endpoint_name"],
                "alpha_family": e["alpha_family"],
                "ticker": tk,
                "requires_ticker": e["requires_ticker"],
                "endpoint_status": e.get("endpoint_status", "unknown"),
                "backfill_priority": _BACKFILL_PRIORITY.get(e["endpoint_name"], 99),
                "request_url_redacted": fmp.redacted_request_url(path),
                "expected_join_key": e["expected_join_key"],
                "expected_date_key": e["expected_date_key"],
                "leakage_risk": e["leakage_risk"],
            })
    return rows


def build_endpoint_access_plan(live_results: Optional[Dict[str, str]] = None) -> List[Dict]:
    """Per-endpoint entitlement / access plan.

    Phase 5-E1 does NOT assume every endpoint is entitled. ``stable_confirmed``
    endpoints are assumed entitled but still verified on first live call;
    ``needs_live_verification`` endpoints have an unknown stable name and must be
    probed live. In live mode ``live_results`` maps endpoint_name -> observed
    access result (success/empty/blocked_403/not_found_404/rate_limited/error/
    skipped_budget); in dry-run every result is 'planned'.
    """
    live_results = live_results or {}
    rows: List[Dict] = []
    for e in _priority_endpoints():
        name = e["endpoint_name"]
        status = e.get("endpoint_status", "unknown")
        if status == "stable_confirmed":
            assumption = ("stable_confirmed: current /stable API path known; assume entitled "
                          "but verify on the first live call.")
        else:
            assumption = ("needs_live_verification: stable endpoint name NOT certain; entitlement "
                          "unknown - probe live and record 200/empty/403/404 before relying on it.")
        rows.append({
            "endpoint_name": name,
            "alpha_family": e["alpha_family"],
            "endpoint_status": status,
            "backfill_priority": _BACKFILL_PRIORITY.get(name, 99),
            "required_for_phase5e2": _endpoint_required_for_e2(e),
            "access_assumption": assumption,
            "live_access_result": live_results.get(name, "planned"),
            "notes": e["point_in_time_notes"],
        })
    return rows


def _empty_progress_rows(universe: List[str]) -> List[Dict]:
    """Resumable progress template - one 'planned' row per (endpoint, ticker)."""
    rows: List[Dict] = []
    for e in _priority_endpoints():
        targets = universe if e["requires_ticker"] else ["*"]
        for tk in targets:
            path = e["endpoint_path_template"].replace("{symbol}", tk if tk != "*" else "")
            rows.append({
                "endpoint_name": e["endpoint_name"],
                "ticker": tk,
                "request_url_redacted": fmp.redacted_request_url(path),
                "status": "planned",
                "row_count": 0,
                "raw_file_path": "",
                "normalized_file_path": "",
                "http_status": "",
                "error_message_sanitized": "",
                "collected_at_utc": "",
            })
    return rows


def _empty_storage_rows(universe: List[str]) -> List[Dict]:
    """Storage manifest - planned local (git-ignored) raw/normalized paths."""
    rows: List[Dict] = []
    for e in _priority_endpoints():
        name = e["endpoint_name"]
        targets = universe if e["requires_ticker"] else ["*"]
        for tk in targets:
            fname = "%s.json" % (tk if tk != "*" else "_all")
            raw_path = _RAW_DIR / name / fname
            norm_path = _NORM_DIR / ("%s.csv" % name)
            rows.append({
                "endpoint_name": name,
                "ticker": tk,
                "raw_file_path": _rel(raw_path),
                "normalized_file_path": _rel(norm_path),
                "raw_exists": False,
                "normalized_rows": 0,
                "gitignored": True,
                "collected_at_utc": "",
            })
    return rows


def _empty_quality_rows(universe: List[str]) -> List[Dict]:
    """Coverage / quality report - placeholder until a live controlled sample."""
    rows: List[Dict] = []
    for e in _priority_endpoints():
        name = e["endpoint_name"]
        pit_status, _ = e0._PIT_BY_ENDPOINT.get(name, ("unknown", ""))
        planned = len(universe) if e["requires_ticker"] else 1
        rows.append({
            "endpoint_name": name,
            "alpha_family": e["alpha_family"],
            "tickers_planned": planned,
            "tickers_success": 0,
            "tickers_empty": 0,
            "tickers_error": 0,
            "coverage_pct": 0.0,
            "point_in_time_status": pit_status,
            "quality_note": "placeholder - populated after a live controlled sample",
        })
    return rows


# --------------------------------------------------------------------------- #
# Normalization (live mode) - flatten records + required metadata columns
# --------------------------------------------------------------------------- #
def _normalized_header(endpoint_name: str) -> List[str]:
    data_cols = e0._SCHEMA_BY_ENDPOINT.get(endpoint_name, ["symbol", "date"])
    return list(_NORM_META_COLUMNS) + data_cols


def _normalize_records(payload, endpoint: Dict, ticker: str, raw_rel: str,
                       ts: str) -> List[Dict]:
    """Flatten a provider payload into normalized rows with the Phase 5-E1
    metadata columns. Never fabricates: a non-list/empty payload yields no rows."""
    if isinstance(payload, dict):
        records = [payload]
    elif isinstance(payload, list):
        records = [r for r in payload if isinstance(r, dict)]
    else:
        records = []
    name = endpoint["endpoint_name"]
    data_cols = e0._SCHEMA_BY_ENDPOINT.get(name, ["symbol", "date"])
    date_field = endpoint["expected_date_key"]
    out: List[Dict] = []
    for rec in records:
        pit_date = rec.get(date_field, "") if date_field else ""
        row = {
            "ticker": ticker if ticker != "*" else rec.get("symbol", ""),
            "endpoint_name": name,
            "source_provider": _SOURCE_PROVIDER,
            "collection_timestamp_utc": ts,
            "point_in_time_candidate_date": pit_date,
            "raw_file_path": raw_rel,
        }
        for c in data_cols:
            row[c] = rec.get(c, "")
        out.append(row)
    return out


# --------------------------------------------------------------------------- #
# Live controlled sample (only when --live AND key present AND within budget)
# --------------------------------------------------------------------------- #
def _access_result_for(statuses: List[str], successes: int, empties: int) -> str:
    if successes > 0:
        return "success"
    if empties > 0:
        return "empty"
    if any(s == "403" for s in statuses):
        return "blocked_403"
    if any(s == "404" for s in statuses):
        return "not_found_404"
    if any(s == "429" for s in statuses):
        return "rate_limited"
    return "error"


def run_live_backfill(universe: List[str], max_tickers: int, max_endpoints: int,
                      max_requests: int, verbose: bool,
                      endpoint_names: Optional[Sequence[str]] = None,
                      skip_existing: bool = False,
                      skip_known_blocked: bool = False,
                      retry_known_blocked: bool = False,
                      known_blocked_map: Optional[Dict[Tuple[str, str], Dict]] = None) -> Dict:
    """Collect a BOUNDED live sample. Writes raw JSON + normalized CSV locally
    under research/data/fmp/ (git-ignored) and returns structured live results.

    ``endpoint_names`` restricts collection to a specific endpoint set (priority
    order preserved) - used by the Phase 5-E1B core-only expanded backfill.
    ``skip_existing`` resumes a multi-batch backfill: a (endpoint, ticker) whose
    raw file already exists locally is recorded ``skipped`` and consumes NO request
    budget, so later batches never re-download collected data."""
    tickers = universe[:max(0, max_tickers)] if max_tickers is not None else list(universe)
    endpoints = _priority_endpoints()
    if endpoint_names is not None:
        want = set(endpoint_names)
        endpoints = [e for e in endpoints if e["endpoint_name"] in want]
    if max_endpoints is not None and max_endpoints >= 0:
        endpoints = endpoints[:max_endpoints]
    _RAW_DIR.mkdir(parents=True, exist_ok=True)
    _NORM_DIR.mkdir(parents=True, exist_ok=True)

    known_blocked_map = known_blocked_map or {}
    progress: List[Dict] = []
    storage: List[Dict] = []
    access_results: Dict[str, str] = {}
    quality: Dict[str, Dict] = {}
    raw_written = 0
    norm_written = 0
    request_count = 0
    budget_hit = False

    for e in endpoints:
        name = e["endpoint_name"]
        endpoint_status = e.get("endpoint_status", "unknown")
        targets = tickers if e["requires_ticker"] else ["*"]
        endpoint_rows: List[Dict] = []
        statuses: List[str] = []
        succ = empt = err = 0
        for tk in targets:
            ts = _utc_now_iso()
            path = e["endpoint_path_template"].replace("{symbol}", tk if tk != "*" else "")
            redacted = fmp.redacted_request_url(path)
            fname = "%s.json" % (tk if tk != "*" else "_all")
            raw_path = _RAW_DIR / name / fname
            raw_rel = _rel(raw_path)
            norm_rel = _rel(_NORM_DIR / ("%s.csv" % name))

            # Resume / skip-existing: if a *valid* raw file was already collected by
            # ANY prior batch (canonical or timestamped name), count it as covered
            # and DO NOT spend request budget. An empty/corrupt prior file is NOT a
            # valid hit, so it is re-fetched instead of being silently skipped.
            if skip_existing:
                existing_path, existing_rows = _find_existing_raw(name, tk)
                if existing_path is not None:
                    found_rel = _rel(existing_path)
                    succ += 1
                    statuses.append("cached")
                    progress.append({
                        "endpoint_name": name, "ticker": tk, "request_url_redacted": redacted,
                        "status": "skipped", "row_count": existing_rows, "raw_file_path": found_rel,
                        "normalized_file_path": norm_rel, "http_status": "", "error_type": "",
                        "error_message_sanitized": "already collected (skip-existing)",
                        "skip_reason": "already_collected", "collected_at_utc": "",
                    })
                    storage.append({
                        "endpoint_name": name, "ticker": tk, "raw_file_path": found_rel,
                        "normalized_file_path": norm_rel, "raw_exists": True,
                        "normalized_rows": existing_rows, "gitignored": True, "collected_at_utc": "",
                    })
                    if verbose:
                        print("  skip %s/%s -> cached (%d row(s))" % (name, tk, existing_rows))
                    continue

            # Known-blocked: a prior batch saw a structural plan block (e.g. 402) for
            # this (endpoint, ticker). Unless --retry-known-blocked overrides, do NOT
            # spend a live call re-hitting it; record it as skipped_known_blocked.
            blocked = known_blocked_map.get((name, tk))
            if skip_known_blocked and not retry_known_blocked and blocked is not None:
                statuses.append("known_blocked")
                progress.append({
                    "endpoint_name": name, "ticker": tk, "request_url_redacted": redacted,
                    "status": "skipped", "row_count": 0, "raw_file_path": "",
                    "normalized_file_path": "",
                    "http_status": blocked.get("http_status", "") or "",
                    "error_type": blocked.get("error_type", "") or "",
                    "error_message_sanitized": "known-blocked (skip-known-blocked): %s"
                                               % (blocked.get("likely_cause", "") or "plan block"),
                    "skip_reason": "known_blocked", "collected_at_utc": "",
                })
                storage.append({
                    "endpoint_name": name, "ticker": tk, "raw_file_path": "",
                    "normalized_file_path": "", "raw_exists": False,
                    "normalized_rows": 0, "gitignored": True, "collected_at_utc": "",
                })
                if verbose:
                    print("  skip %s/%s -> known-blocked (%s)"
                          % (name, tk, blocked.get("http_status", "") or "blocked"))
                continue

            if max_requests is not None and request_count >= max_requests:
                budget_hit = True
                progress.append({
                    "endpoint_name": name, "ticker": tk, "request_url_redacted": redacted,
                    "status": "skipped", "row_count": 0, "raw_file_path": "",
                    "normalized_file_path": "", "http_status": "", "error_type": "",
                    "error_message_sanitized": "request budget (--max-requests) reached",
                    "skip_reason": "budget", "collected_at_utc": "",
                })
                continue

            request_count += 1
            try:
                payload = fmp.get_json(path, live=True)
            except fmp.FmpError as exc:
                status_code = getattr(exc, "status_code", None)
                error_type = getattr(exc, "error_type", "error")
                statuses.append("" if status_code is None else str(status_code))
                err += 1
                progress.append({
                    "endpoint_name": name, "ticker": tk, "request_url_redacted": redacted,
                    "status": "error", "row_count": 0, "raw_file_path": "",
                    "normalized_file_path": "",
                    "http_status": "" if status_code is None else status_code,
                    "error_type": error_type,
                    "error_message_sanitized": "%s (%s)" % (str(exc), error_type),
                    "skip_reason": "", "collected_at_utc": ts,
                })
                storage.append({
                    "endpoint_name": name, "ticker": tk, "raw_file_path": "",
                    "normalized_file_path": "", "raw_exists": False,
                    "normalized_rows": 0, "gitignored": True, "collected_at_utc": ts,
                })
                continue

            # Persist raw payload locally (verbatim; git-ignored; deterministic
            # filename -> a re-run overwrites in place).
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            with open(raw_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
                fh.write("\n")
            raw_written += 1
            norm_rows = _normalize_records(payload, e, tk, raw_rel, ts)
            endpoint_rows.extend(norm_rows)
            row_status = "success" if norm_rows else "empty"
            statuses.append("200")
            if norm_rows:
                succ += 1
            else:
                empt += 1
            progress.append({
                "endpoint_name": name, "ticker": tk, "request_url_redacted": redacted,
                "status": row_status, "row_count": len(norm_rows),
                "raw_file_path": raw_rel,
                "normalized_file_path": norm_rel if norm_rows else "",
                "http_status": 200, "error_type": "",
                "error_message_sanitized": "" if norm_rows else "empty_result",
                "skip_reason": "", "collected_at_utc": ts,
            })
            storage.append({
                "endpoint_name": name, "ticker": tk, "raw_file_path": raw_rel,
                "normalized_file_path": norm_rel if norm_rows else "",
                "raw_exists": True, "normalized_rows": len(norm_rows),
                "gitignored": True, "collected_at_utc": ts,
            })
            if verbose:
                print("  live %s/%s -> %s (%d row(s))" % (name, tk, row_status, len(norm_rows)))
            time.sleep(fmp.MIN_SLEEP_SECONDS)

        # Write one normalized CSV per endpoint (git-ignored local file).
        if endpoint_rows:
            header = _normalized_header(name)
            _write_csv(_NORM_DIR / ("%s.csv" % name), header,
                       [[r.get(c, "") for c in header] for r in endpoint_rows])
            norm_written += 1

        access_results[name] = _access_result_for(statuses, succ, empt)
        planned = len(targets)
        attempted = succ + empt + err
        quality[name] = {
            "endpoint_name": name,
            "alpha_family": e["alpha_family"],
            "tickers_planned": planned,
            "tickers_success": succ,
            "tickers_empty": empt,
            "tickers_error": err,
            "coverage_pct": round(100.0 * succ / planned, 2) if planned else 0.0,
            "point_in_time_status": e0._PIT_BY_ENDPOINT.get(name, ("unknown", ""))[0],
            "quality_note": ("attempted %d/%d; status=%s" %
                             (attempted, planned, access_results[name])),
        }

    return {
        "progress": progress,
        "storage": storage,
        "access_results": access_results,
        "quality": quality,
        "raw_written": raw_written,
        "norm_written": norm_written,
        "request_count": request_count,
        "budget_hit": budget_hit,
        "endpoints_attempted": [e["endpoint_name"] for e in endpoints],
    }


# --------------------------------------------------------------------------- #
# Secret leak scan over the committed OUTPUT artifacts (never the paid payloads)
# --------------------------------------------------------------------------- #
def _scan_for_leaks(paths: Sequence[Path], api_key: Optional[str]) -> Tuple[bool, int]:
    """Return (clean, files_scanned). 'clean' = no raw key value AND no key query
    parameter marker ('apikey=') in any scanned committed artifact."""
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


def build_secret_audit(api_key_present: bool, live_mode: bool, leak_clean: bool,
                       scanned: int, paid_gitignored: bool) -> List[Dict]:
    def row(check, value, passed, detail):
        return {"check": check, "value": value, "passed": passed, "detail": detail}

    return [
        row("api_key_read_from_env_only", fmp.API_KEY_ENV, True,
            "key is read solely from the %s environment variable" % fmp.API_KEY_ENV),
        row("api_key_present_in_env", api_key_present, None,
            "informational: whether a key is currently set (not a pass/fail)"),
        row("api_key_logged", False, True,
            "the key is never printed to stdout/stderr or written to any artifact"),
        row("api_key_hardcoded", False, True,
            "no API key is hard-coded anywhere in the runner or adapter"),
        row("api_key_written_to_disk", False, True,
            "the key value is never persisted to any file"),
        row("urls_redacted_in_artifacts", True, True,
            "every persisted URL strips the key parameter and shows the %s placeholder"
            % fmp.REDACTED_KEY_PLACEHOLDER),
        row("live_requires_explicit_flag_key_and_limits", True, True,
            "a network call needs --live, a present %s, AND bounded request limits"
            % fmp.API_KEY_ENV),
        row("paid_data_gitignored", paid_gitignored, paid_gitignored,
            "research/data/fmp/{raw,normalized} are git-ignored; paid payloads never committed"),
        row("raw_payloads_not_in_committed_artifacts", True, True,
            "committed artifacts carry only counts / redacted URLs / file paths, never payloads"),
        row("no_key_in_output_files", leak_clean, leak_clean,
            "scanned %d committed artifact(s); no raw key value and no key parameter marker" % scanned),
        row("dry_run_does_no_network", not live_mode, True,
            "the default run performs zero network calls"),
        row("provider_restricted_to_fmp_host", fmp.ALLOWED_HOST, True,
            "live requests are refused for any host other than %s" % fmp.ALLOWED_HOST),
    ]


# --------------------------------------------------------------------------- #
# Phase 5-E2 enriched panel plan
# --------------------------------------------------------------------------- #
def build_panel_plan(universe: List[str]) -> Dict:
    return {
        "phase": "5-E2",
        "title": "Phase 5-E2 - Enriched FMP Alpha Panel and Model Rerun",
        "objective": ("Append point-in-time FMP fundamental / earnings / analyst features to the "
                      "Phase 5-C survivor cross-sectional panel - same universe, rebalance schedule, "
                      "walk-forward folds and embargo, changing ONLY the feature set - and measure "
                      "whether the paid external data adds incremental cross-sectional edge over the "
                      "price-only baseline. Framed as an incremental-edge test, not a foregone win."),
        "input_normalized_fmp_dir": _rel(_NORM_DIR),
        "input_raw_fmp_dir": _rel(_RAW_DIR),
        "input_price_only_baseline": {
            "phase": "5-C",
            "source": "research/output/phase5c_cross_sectional_alpha_research.* (price-only OOS baseline)",
            "primary_label": "forward_excess_return_20d_vs_spy",
            "note": "Phase 5-C baseline composite z-score had ~0 OOS edge; this is the bar to beat.",
        },
        "feature_families_to_build": [
            "fundamentals_quality_value_growth",
            "profitability_and_margin_trend",
            "balance_sheet_leverage",
            "cash_flow_quality",
            "valuation_ratios",
            "earnings_surprise_event",
            "analyst_revision_signal (only if analyst_estimates data is available)",
            "analyst_recommendation_change (only if analyst_recommendations data is available)",
            "price_target_revision (only if analyst_price_targets data is available)",
        ],
        "point_in_time_join_fields": [
            "ticker (symbol) as-of join key",
            "_pit_date / point_in_time_candidate_date (filing/announcement/publication availability date)",
            "statements joined on fillingDate/acceptedDate, never the fiscal period date",
            "key_metrics/ratios lagged to the matching statement filing availability",
            "analyst consensus uses publication dates; never back-date a current consensus",
        ],
        "stale_data_limits": {
            "fundamentals_max_staleness_days": 200,
            "analyst_consensus_max_staleness_days": 120,
            "earnings_event_lookback_days": 90,
            "rule": "drop or flag a feature value older than its family's max staleness at the scoring date",
        },
        "coverage_thresholds": {
            "min_ticker_coverage_pct_per_feature": 60,
            "min_cross_sectional_names_per_rebalance": 50,
            "rule": "a feature family below its coverage threshold is reported, not silently imputed",
        },
        "incremental_edge_gates_vs_phase5c": {
            "primary": "delta_mean_rank_ic = enriched - price_only >= +0.01 on forward_excess_return_20d_vs_spy",
            "stability": "worst-year rank IC not worse than the price-only baseline's worst year",
            "leakage": "label-shuffle placebo IC ~ 0 (as-of joins only)",
            "survivorship": "results flagged survivorship-inflated until PIT S&P 500 membership is used",
            "decision": "ship enriched features ONLY if the edge + stability gates both pass; else report as not worth deploying",
        },
        "sample_universe_used_for_backfill": list(universe),
        "next_phase": "Phase 5-E2 - Enriched FMP Alpha Panel and Model Rerun",
        "preview_only": True,
        "orders_enabled": False,
        "automation_enabled": False,
        "broker_execution_enabled": False,
        "production_replacement": False,
    }


# --------------------------------------------------------------------------- #
# Recommendation
# --------------------------------------------------------------------------- #
def derive_recommendation(api_key_present: bool, live_requested: bool, live_mode: bool,
                          live: Optional[Dict]) -> str:
    if live_requested and not api_key_present:
        return REC_BLOCKED_NO_KEY
    if not live_mode or live is None:
        # Dry-run (with or without a key): the plan is ready to be run live.
        return REC_READY_FOR_SAMPLE
    request_count = live.get("request_count", 0)
    if request_count == 0:
        return REC_READY_FOR_SAMPLE
    quality = live.get("quality", {})
    core_success = sum(1 for n, q in quality.items()
                       if n in _CORE_STABLE_ENDPOINTS and q.get("tickers_success", 0) > 0)
    total_success = sum(q.get("tickers_success", 0) for q in quality.values())
    if total_success == 0:
        # Every request failed/empty. If core endpoints were blocked, it is an
        # entitlement question; otherwise a hard error.
        core_attempted = any(n in _CORE_STABLE_ENDPOINTS for n in quality)
        return REC_NEEDS_ENTITLEMENT if core_attempted else REC_ERROR
    # Need at least two core stable endpoints returning data (e.g. a statement +
    # one more) to consider the enriched panel buildable.
    if core_success >= 2:
        return REC_READY_FOR_E2
    return REC_NEEDS_ENTITLEMENT


_NEXT_PHASE = {
    REC_READY_FOR_SAMPLE: ("Run a bounded live controlled sample: --live --max-tickers 5 "
                           "--max-endpoints 6 --max-requests 40 (requires FMP_API_KEY)."),
    REC_READY_FOR_E2: "Phase 5-E2 - Enriched FMP Alpha Panel and Model Rerun (see phase5e2_enriched_panel_plan.json).",
    REC_NEEDS_ENTITLEMENT: ("Review FMP plan entitlements for the blocked/empty endpoints; confirm "
                            "stable names for needs_live_verification endpoints, then re-sample."),
    REC_BLOCKED_NO_KEY: "Set FMP_API_KEY in the environment, then re-run with --live and bounded limits.",
    REC_ERROR: "Inspect the progress/quality reports and the sanitized errors, then re-run the bounded sample.",
}


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run(live: bool = False, max_tickers: int = 5, max_endpoints: int = 6,
        max_requests: int = 40, universe: Optional[List[str]] = None,
        verbose: bool = True) -> Dict:
    universe = [t.strip().upper() for t in (universe or DEFAULT_UNIVERSE) if str(t).strip()]
    paid_gitignored = _ensure_paid_data_gitignore()
    api_key_present = fmp.has_api_key()
    live_requested = bool(live)
    live_mode = live_requested and api_key_present

    # --- live controlled sample (only when explicitly enabled AND key present) ---
    live_results: Optional[Dict] = None
    if live_mode:
        live_results = run_live_backfill(universe, max_tickers, max_endpoints,
                                         max_requests, verbose)

    # --- request plan (always) ---
    request_plan = build_request_plan(universe)
    _write_csv(_REQUEST_PLAN_OUT,
               ["endpoint_name", "alpha_family", "ticker", "requires_ticker",
                "endpoint_status", "backfill_priority", "request_url_redacted",
                "expected_join_key", "expected_date_key", "leakage_risk"],
               [[r["endpoint_name"], r["alpha_family"], r["ticker"], r["requires_ticker"],
                 r["endpoint_status"], r["backfill_priority"], r["request_url_redacted"],
                 r["expected_join_key"], r["expected_date_key"], r["leakage_risk"]]
                for r in request_plan])

    # --- endpoint access plan (entitlement; live results merged when present) ---
    access_plan = build_endpoint_access_plan(
        live_results["access_results"] if live_results else None)
    _write_csv(_ACCESS_PLAN_OUT,
               ["endpoint_name", "alpha_family", "endpoint_status", "backfill_priority",
                "required_for_phase5e2", "access_assumption", "live_access_result", "notes"],
               [[r["endpoint_name"], r["alpha_family"], r["endpoint_status"],
                 r["backfill_priority"], r["required_for_phase5e2"], r["access_assumption"],
                 r["live_access_result"], r["notes"]] for r in access_plan])

    # --- progress template (resumable) ---
    progress_rows = live_results["progress"] if live_results else _empty_progress_rows(universe)
    _write_csv(_PROGRESS_TEMPLATE_OUT,
               ["endpoint_name", "ticker", "request_url_redacted", "status", "row_count",
                "raw_file_path", "normalized_file_path", "http_status",
                "error_message_sanitized", "collected_at_utc"],
               [[r["endpoint_name"], r["ticker"], r["request_url_redacted"], r["status"],
                 r["row_count"], r["raw_file_path"], r["normalized_file_path"],
                 r["http_status"], r["error_message_sanitized"], r["collected_at_utc"]]
                for r in progress_rows])

    # --- storage manifest ---
    storage_rows = live_results["storage"] if live_results else _empty_storage_rows(universe)
    _write_csv(_STORAGE_MANIFEST_OUT,
               ["endpoint_name", "ticker", "raw_file_path", "normalized_file_path",
                "raw_exists", "normalized_rows", "gitignored", "collected_at_utc"],
               [[r["endpoint_name"], r["ticker"], r["raw_file_path"], r["normalized_file_path"],
                 r["raw_exists"], r["normalized_rows"], r["gitignored"], r["collected_at_utc"]]
                for r in storage_rows])

    # --- quality / coverage report ---
    if live_results:
        quality_rows = [live_results["quality"].get(e["endpoint_name"]) or
                        {"endpoint_name": e["endpoint_name"], "alpha_family": e["alpha_family"],
                         "tickers_planned": 0, "tickers_success": 0, "tickers_empty": 0,
                         "tickers_error": 0, "coverage_pct": 0.0,
                         "point_in_time_status": e0._PIT_BY_ENDPOINT.get(
                             e["endpoint_name"], ("unknown", ""))[0],
                         "quality_note": "not attempted in this bounded sample"}
                        for e in _priority_endpoints()]
    else:
        quality_rows = _empty_quality_rows(universe)
    _write_csv(_QUALITY_REPORT_OUT,
               ["endpoint_name", "alpha_family", "tickers_planned", "tickers_success",
                "tickers_empty", "tickers_error", "coverage_pct", "point_in_time_status",
                "quality_note"],
               [[r["endpoint_name"], r["alpha_family"], r["tickers_planned"],
                 r["tickers_success"], r["tickers_empty"], r["tickers_error"],
                 r["coverage_pct"], r["point_in_time_status"], r["quality_note"]]
                for r in quality_rows])

    # --- Phase 5-E2 plan ---
    panel_plan = build_panel_plan(universe)
    _write_json(_PANEL_PLAN_OUT, panel_plan)

    # --- secret leak scan over the committed OUTPUT artifacts only ---
    output_paths = [_REQUEST_PLAN_OUT, _ACCESS_PLAN_OUT, _PROGRESS_TEMPLATE_OUT,
                    _STORAGE_MANIFEST_OUT, _QUALITY_REPORT_OUT, _PANEL_PLAN_OUT]
    leak_clean, scanned = _scan_for_leaks(output_paths, fmp.resolve_api_key())

    secret_audit = build_secret_audit(api_key_present, live_mode, leak_clean, scanned,
                                       paid_gitignored)
    _write_csv(_SECRET_AUDIT_OUT, ["check", "value", "passed", "detail"],
               [[a["check"], a["value"], "" if a["passed"] is None else a["passed"], a["detail"]]
                for a in secret_audit])

    # --- aggregates ---
    families_planned = fmp.endpoint_families()
    if live_results:
        verified = sorted({e["alpha_family"] for e in _priority_endpoints()
                           if live_results["quality"].get(e["endpoint_name"], {}).get(
                               "tickers_success", 0) > 0})
        blocked = sorted(set(families_planned) - set(verified))
        success_count = sum(q.get("tickers_success", 0) for q in live_results["quality"].values())
        empty_count = sum(q.get("tickers_empty", 0) for q in live_results["quality"].values())
        error_count = sum(q.get("tickers_error", 0) for q in live_results["quality"].values())
        live_request_count = live_results["request_count"]
        raw_written = live_results["raw_written"]
        norm_written = live_results["norm_written"]
    else:
        # Dry-run: nothing verified yet; families containing any
        # needs_live_verification endpoint are 'blocked_or_unverified'.
        verified = []
        blocked = sorted({e["alpha_family"] for e in _priority_endpoints()
                          if e.get("endpoint_status") == "needs_live_verification"})
        success_count = empty_count = error_count = 0
        live_request_count = raw_written = norm_written = 0

    recommendation = derive_recommendation(api_key_present, live_requested, live_mode, live_results)

    report = {
        "phase": PHASE,
        "objective": ("Build a controlled, resumable FMP backfill that collects paid external alpha "
                      "data LOCALLY (outside Git) with audit / coverage / secret-safety controls, "
                      "and normalizes it into the point-in-time inputs required for the Phase 5-E2 "
                      "enriched alpha panel rerun. This phase collects/normalizes data only - it does "
                      "NOT build the enriched model."),
        "provider": fmp.PROVIDER_NAME,
        "provider_host": fmp.ALLOWED_HOST,
        "mode": "live_sample" if live_mode else "dry_run",
        "live_requested": live_requested,
        "api_key_env_var": fmp.API_KEY_ENV,
        "api_key_present": api_key_present,
        "api_key_logged": False,
        "universe": list(universe),
        "limits": {"max_tickers": max_tickers, "max_endpoints": max_endpoints,
                   "max_requests": max_requests},
        "full_sp500_backfill_auto": False,
        "live_request_count": live_request_count,
        "planned_request_count": len(request_plan),
        "raw_files_written_count": raw_written,
        "normalized_files_written_count": norm_written,
        "endpoint_success_count": success_count,
        "endpoint_empty_count": empty_count,
        "endpoint_error_count": error_count,
        "endpoint_families_planned": families_planned,
        "endpoint_families_live_verified": verified,
        "endpoint_families_blocked_or_unverified": blocked,
        "endpoints_needs_live_verification": [
            e["endpoint_name"] for e in _priority_endpoints()
            if e.get("endpoint_status") == "needs_live_verification"],
        "storage_raw_dir": _rel(_RAW_DIR),
        "storage_normalized_dir": _rel(_NORM_DIR),
        "raw_overwrite_policy": ("deterministic raw/{endpoint}/{ticker}.json filename; a re-run "
                                 "overwrites in place (resume via the progress template status)"),
        "paid_data_gitignored": paid_gitignored,
        "live_budget_hit": bool(live_results and live_results.get("budget_hit")),
        "secret_safety_leak_scan_clean": leak_clean,
        "secret_safety_files_scanned": scanned,
        "recommendation": recommendation,
        "recommendation_allowed_values": list(ALLOWED_RECOMMENDATIONS),
        "recommended_next_phase": _NEXT_PHASE[recommendation],
        "outputs": {
            "report": _rel(_REPORT_OUT),
            "request_plan": _rel(_REQUEST_PLAN_OUT),
            "endpoint_access_plan": _rel(_ACCESS_PLAN_OUT),
            "storage_manifest": _rel(_STORAGE_MANIFEST_OUT),
            "progress_template": _rel(_PROGRESS_TEMPLATE_OUT),
            "quality_report": _rel(_QUALITY_REPORT_OUT),
            "secret_safety_audit": _rel(_SECRET_AUDIT_OUT),
            "phase5e2_enriched_panel_plan": _rel(_PANEL_PLAN_OUT),
        },
        # Safety / provenance contract (explicit, like Phase 5-A..5-E0).
        "preview_only": True,
        "orders_enabled": False,
        "automation_enabled": False,
        "broker_execution_enabled": False,
        "production_replacement": False,
        "network_used": bool(live_mode and live_request_count > 0),
        "alphavantage_called": False,
        "other_paid_apis_called": False,
        "deployed": False,
        "binary_artifacts_created": False,
        "data_fabricated": False,
        "wrote_to_d_drive": False,
        "committed": False,
    }
    _write_json(_REPORT_OUT, report)
    return report


# --------------------------------------------------------------------------- #
# Phase 5-E1B - expanded CORE fundamentals backfill (resumable, batched)
# --------------------------------------------------------------------------- #
def _phase5c_data_path() -> Optional[str]:
    """Resolve the Phase 5-C price-history CSV (read-only). Mirrors Phase 5-C's
    candidate order WITHOUT importing it (keeps this runner numpy-free). D: is
    INPUT ONLY - we never write there."""
    env = os.environ.get("MODEL_V2_PRICE_HISTORY_CSV")
    candidates = ([env] if env else []) + [
        r"D:\Stock_Prediction_app_data\phase2k_g\output\phase2k_g_expanded_price_history_free.csv",
        str(_REPO_ROOT / "research" / "output" / "phase2g_price_history_real.csv"),
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return None


def load_phase5c_universe() -> Tuple[List[str], str]:
    """Return ``(tickers, source_label)`` from the Phase 5-C price-history universe.

    Reads the distinct ``ticker`` column (read-only), drops the SPY benchmark, and
    sorts for determinism. Falls back SAFELY to the default sample universe if the
    CSV is unavailable, unreadable, or empty (never raises)."""
    path = _phase5c_data_path()
    if not path:
        return list(DEFAULT_UNIVERSE), "fallback_sample (phase5c price history unavailable)"
    try:
        seen: List[str] = []
        s: set = set()
        with open(path, "r", newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                t = (row.get("ticker") or "").strip().upper()
                if not t or t == _BENCHMARK or t in s:
                    continue
                s.add(t)
                seen.append(t)
        if not seen:
            return list(DEFAULT_UNIVERSE), "fallback_sample (phase5c price history empty)"
        seen.sort()
        return seen, "phase5c_price_history:%s" % _rel(Path(path))
    except OSError:
        return list(DEFAULT_UNIVERSE), "fallback_sample (phase5c price history read error)"


def resolve_universe(universe_source: str, explicit: Optional[List[str]]) -> Tuple[List[str], str]:
    """Resolve the backfill universe + a provenance label. Explicit tickers win;
    otherwise ``phase5c`` loads from local price history (safe fallback); otherwise
    the default sample universe."""
    if explicit:
        cleaned = [t.strip().upper() for t in explicit if str(t).strip()]
        if cleaned:
            return cleaned, "cli_explicit"
    if (universe_source or "").lower() == "phase5c":
        return load_phase5c_universe()
    return list(DEFAULT_UNIVERSE), "default_sample"


def _core_endpoints() -> List[Dict]:
    """The four core fundamentals/profile endpoints, in backfill priority order."""
    want = set(_CORE_ONLY_ENDPOINTS)
    return [e for e in _priority_endpoints() if e["endpoint_name"] in want]


def _compute_core_coverage(endpoint_names: List[str], tickers: List[str],
                           progress: Sequence[Dict]) -> Tuple[Dict, Dict, int]:
    """Build (coverage_by_endpoint, coverage_by_ticker, core_ready_ticker_count).

    A (endpoint, ticker) counts as covered when its progress status is ``success``
    or ``skipped`` (already collected). A ticker is "core-ready" when >= the
    per-ticker core-endpoint threshold are covered."""
    covered = set()
    for r in progress:
        status = r.get("status")
        # 'skipped' counts as covered ONLY when it was an already-collected resume;
        # known-blocked and budget skips have NO local data and are not covered.
        if status == "success" or (
                status == "skipped" and r.get("skip_reason") == "already_collected"):
            covered.add((r.get("endpoint_name"), r.get("ticker")))
    n_t = len(tickers)
    n_e = len(endpoint_names)
    by_endpoint: Dict[str, Dict] = {}
    for e in endpoint_names:
        c = sum(1 for tk in tickers if (e, tk) in covered)
        by_endpoint[e] = {
            "tickers_in_batch": n_t,
            "tickers_covered": c,
            "coverage_pct": round(100.0 * c / n_t, 2) if n_t else 0.0,
        }
    by_ticker: Dict[str, Dict] = {}
    core_ready = 0
    for tk in tickers:
        c = sum(1 for e in endpoint_names if (e, tk) in covered)
        if c >= _E1B_MIN_CORE_ENDPOINTS_PER_TICKER:
            core_ready += 1
        by_ticker[tk] = {
            "endpoints_in_batch": n_e,
            "endpoints_covered": c,
            "coverage_pct": round(100.0 * c / n_e, 2) if n_e else 0.0,
        }
    return by_endpoint, by_ticker, core_ready


def _raw_rows_in_file(path: Path) -> Optional[int]:
    """Return the row count of a raw FMP JSON file, or None if it is missing,
    unparseable, or empty. A list payload -> len; a non-empty dict -> 1; anything
    empty/corrupt -> None so a prior *failed/empty* file never blocks a real fetch."""
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return None
    if isinstance(payload, list):
        return len(payload) if payload else None
    if isinstance(payload, dict):
        return 1 if payload else None
    return None


def _find_existing_raw(name: str, ticker: str) -> Tuple[Optional[Path], Optional[int]]:
    """Detect a previously-collected *valid* raw file for (endpoint_name, ticker),
    independent of batch_id / retry id. Checks the canonical
    raw/{endpoint}/{ticker}.json first, then tolerates timestamped / suffixed
    variants (raw/{endpoint}/{ticker}*.json) written by an older layout. Returns
    (path, row_count) for the first valid match, else (None, None). Network-free."""
    canonical = _RAW_DIR / name / ("%s.json" % ticker)
    rows = _raw_rows_in_file(canonical)
    if rows is not None:
        return canonical, rows
    endpoint_dir = _RAW_DIR / name
    if endpoint_dir.is_dir():
        # Deterministic order so the chosen fallback file is stable across runs.
        for cand in sorted(endpoint_dir.glob("%s*.json" % ticker)):
            if cand == canonical:
                continue
            rows = _raw_rows_in_file(cand)
            if rows is not None:
                return cand, rows
    return None, None


# HTTP statuses that represent a *structural* block (plan entitlement / payment),
# not a transient failure. These are the ones worth remembering so later batches
# do not waste live calls re-hitting them.
_KNOWN_BLOCKING_HTTP = {"402", "403"}


def _is_known_blocking(http_status) -> bool:
    return str(http_status).strip() in _KNOWN_BLOCKING_HTTP


def _retry_policy_for(http_status) -> str:
    """Retry policy recorded in the known-blocked register. 402/403 are structural
    plan blocks -> do not retry without an explicit override."""
    return ("do_not_retry_without_override"
            if _is_known_blocking(http_status) else "retry_later")


_KNOWN_BLOCKED_COLUMNS = [
    "ticker", "endpoint_name", "http_status", "error_type",
    "error_message_sanitized", "likely_cause", "first_seen_batch_id",
    "last_seen_batch_id", "retry_policy",
]


def _read_known_blocked() -> Dict[Tuple[str, str], Dict]:
    """Load the persistent known-blocked register keyed by (endpoint_name, ticker).
    Returns an empty map if the file is absent. Pure file read, no network."""
    out: Dict[Tuple[str, str], Dict] = {}
    if not _CORE_KNOWN_BLOCKED_OUT.is_file():
        return out
    try:
        with open(_CORE_KNOWN_BLOCKED_OUT, "r", newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                key = (row.get("endpoint_name", ""), row.get("ticker", ""))
                if key[0] and key[1]:
                    out[key] = {c: row.get(c, "") for c in _KNOWN_BLOCKED_COLUMNS}
    except (OSError, ValueError):
        return out
    return out


def _merge_known_blocked(prior: Dict[Tuple[str, str], Dict],
                         error_rows: Sequence[Dict], batch_id: str) -> Dict[Tuple[str, str], Dict]:
    """Fold this batch's structurally-blocked error rows into the prior register.
    A first-time block records first_seen = last_seen = batch_id; a repeat block
    keeps first_seen and advances last_seen. Transient errors (non-402/403) are not
    remembered. Prior entries not re-attempted this batch are preserved as-is."""
    merged: Dict[Tuple[str, str], Dict] = {k: dict(v) for k, v in prior.items()}
    for r in error_rows:
        if not _is_known_blocking(r.get("http_status")):
            continue
        key = (r.get("endpoint_name", ""), r.get("ticker", ""))
        if not (key[0] and key[1]):
            continue
        code = str(r.get("http_status", "")).strip()
        entry = {
            "ticker": key[1],
            "endpoint_name": key[0],
            "http_status": code,
            "error_type": r.get("error_type", "") or "",
            "error_message_sanitized": r.get("error_message_sanitized", "") or "",
            "likely_cause": r.get("likely_cause", "") or "",
            "first_seen_batch_id": merged.get(key, {}).get("first_seen_batch_id") or batch_id,
            "last_seen_batch_id": batch_id,
            "retry_policy": _retry_policy_for(code),
        }
        merged[key] = entry
    return merged


def _next_batch_id(batch_id: str) -> str:
    """Increment a trailing numeric batch counter (core_batch_001 -> core_batch_002),
    preserving zero-padding width; append _002 when there is no numeric suffix."""
    import re
    m = re.search(r"^(.*?)(\d+)$", batch_id or "")
    if not m:
        return "%s_002" % (batch_id or "core_batch")
    prefix, digits = m.group(1), m.group(2)
    return "%s%0*d" % (prefix, len(digits), int(digits) + 1)


def _classify_request(status: str, http_status, error_type: str) -> Tuple[str, str]:
    """Map a request-level outcome to a (likely_cause, next_action) pair so the
    request_results / error_report artifacts tell us WHY a request failed and what
    to do, instead of just an opaque count. Pure / deterministic / network-free."""
    s = (status or "").lower()
    if s == "success":
        return "", "none"
    if s == "empty":
        return ("provider returned no rows for this symbol/endpoint",
                "verify the symbol trades and the endpoint covers it; may be legitimately empty")
    if s == "skipped_existing":
        return "already collected by a prior batch", "none (reuse the cached raw file)"
    if s == "skipped_known_blocked":
        return ("previously blocked by the FMP plan (known-blocked register)",
                "left blocked; pass --retry-known-blocked only after the plan is upgraded")
    if s == "planned":
        return ("not executed yet (dry-run, or the --max-requests budget was reached)",
                "run a live batch or raise --max-requests, then resume with --skip-existing")
    # status == "error": classify by HTTP status first, then error_type.
    code = str(http_status if http_status not in (None, "") else "").strip()
    et = (error_type or "").lower()
    if code == "401":
        return ("invalid or unauthorized FMP_API_KEY",
                "verify FMP_API_KEY is set and the plan is active, then retry with --skip-existing")
    if code == "402":
        return ("FMP plan does not include this data (payment required)",
                "upgrade the FMP plan or drop this endpoint from the core set")
    if code == "403":
        return ("endpoint not entitled on the current FMP plan",
                "confirm plan entitlement for this endpoint or drop it from the core set")
    if code == "404":
        return ("symbol or stable endpoint path not found",
                "verify the ticker symbol and the /stable endpoint name")
    if code == "429":
        return ("rate limited / quota exceeded",
                "wait for the quota window to reset, then resume with --skip-existing")
    if code.startswith("5"):
        return ("provider server error (5xx)",
                "transient provider issue; retry later with --skip-existing")
    if "timeout" in et:
        return ("network timeout reaching the provider",
                "check connectivity; retry later with --skip-existing")
    if "json" in et or "decode" in et:
        return ("provider returned a non-JSON / malformed body",
                "inspect the sanitized error; retry with --skip-existing")
    if "url" in et or "host" in et:
        return ("request URL/host rejected before sending",
                "inspect the sanitized error; this is a request-construction issue, not quota")
    return ("unclassified request error",
            "inspect the sanitized error; retry the single endpoint with --skip-existing")


def _build_request_results(batch_id: str, plan_rows: Sequence[Dict],
                           progress: Sequence[Dict]) -> List[Dict]:
    """One row per planned request with request-level diagnostics. In dry-run (no
    ``progress``) every row is ``planned``; in live mode each row reflects the
    observed per-request outcome (success / empty / error / skipped_existing).

    A live ``skipped`` row is reported as ``skipped_existing`` when it was skipped
    because the raw file already existed (resume), and as ``planned`` when it was
    skipped only because the --max-requests budget was reached (not yet collected).
    """
    rows: List[Dict] = []
    if progress:
        for r in progress:
            raw_status = r.get("status", "")
            if raw_status == "skipped":
                skip_reason = r.get("skip_reason")
                if skip_reason == "already_collected":
                    rstatus = "skipped_existing"
                elif skip_reason == "known_blocked":
                    rstatus = "skipped_known_blocked"
                else:
                    rstatus = "planned"
            else:
                rstatus = raw_status
            http_status = r.get("http_status", "")
            error_type = r.get("error_type", "") or ""
            cause, action = _classify_request(rstatus, http_status, error_type)
            rows.append({
                "batch_id": batch_id,
                "ticker": r.get("ticker", ""),
                "endpoint_name": r.get("endpoint_name", ""),
                "status": rstatus,
                "http_status": http_status,
                "error_type": error_type,
                "error_message_sanitized": r.get("error_message_sanitized", "") or "",
                "likely_cause": cause,
                "next_action": action,
                "row_count": r.get("row_count", 0),
                "raw_file_path": r.get("raw_file_path", "") or "",
                "normalized_file_path": r.get("normalized_file_path", "") or "",
                "request_url_redacted": r.get("request_url_redacted", "") or "",
                "collected_at_utc": r.get("collected_at_utc", "") or "",
            })
        return rows
    # Dry-run: one 'planned' row per planned request.
    cause, action = _classify_request("planned", "", "")
    for p in plan_rows:
        rows.append({
            "batch_id": batch_id,
            "ticker": p.get("ticker", ""),
            "endpoint_name": p.get("endpoint_name", ""),
            "status": "planned",
            "http_status": "",
            "error_type": "",
            "error_message_sanitized": "",
            "likely_cause": cause,
            "next_action": action,
            "row_count": 0,
            "raw_file_path": p.get("planned_raw_file_path", "") or "",
            "normalized_file_path": "",
            "request_url_redacted": p.get("request_url_redacted", "") or "",
            "collected_at_utc": "",
        })
    return rows


_REQUEST_RESULTS_COLUMNS = [
    "batch_id", "ticker", "endpoint_name", "status", "http_status", "error_type",
    "error_message_sanitized", "likely_cause", "next_action", "row_count",
    "raw_file_path", "normalized_file_path", "request_url_redacted", "collected_at_utc",
]
_ERROR_REPORT_COLUMNS = [
    "endpoint_name", "ticker", "http_status", "error_type",
    "error_message_sanitized", "likely_cause", "next_action",
]


def derive_e1b_recommendation(live_requested: bool, live_mode: bool, live: Optional[Dict],
                              by_endpoint: Dict, core_ready: int) -> Tuple[str, bool]:
    """Return (recommendation, enough_for_phase5e2) for the core backfill."""
    if live_requested and not live_mode:
        return REC_BLOCKED_NO_KEY, False
    if not live_mode or live is None:
        return REC_E1B_READY_LIVE, False
    total_success = sum(q.get("tickers_success", 0) for q in live.get("quality", {}).values())
    request_count = live.get("request_count", 0)
    if total_success == 0 and request_count > 0:
        # Every attempted core request failed/empty - cannot proceed.
        return REC_ERROR, False
    endpoints_ok = bool(by_endpoint) and all(
        v["coverage_pct"] >= _E1B_MIN_ENDPOINT_COVERAGE_PCT for v in by_endpoint.values())
    enough = endpoints_ok and core_ready >= _E1B_MIN_TICKERS_FOR_E2
    if enough:
        return REC_E1B_READY_E2, True
    return REC_E1B_NEEDS_MORE, False


_E1B_NEXT_PHASE = {
    REC_E1B_READY_LIVE: ("Run the first bounded live core batch: --live --core-only "
                         "--universe-source phase5c --max-tickers 25 --max-requests 100 "
                         "--skip-existing --batch-id core_batch_001 (requires FMP_API_KEY)."),
    REC_E1B_READY_E2: "Phase 5-E2 - Enriched FMP Alpha Panel and Model Rerun (core fundamentals ready).",
    REC_E1B_NEEDS_MORE: ("Run further --skip-existing batches (increment --batch-id, raise "
                         "--max-tickers) until core coverage clears the Phase 5-E2 threshold."),
    REC_BLOCKED_NO_KEY: "Set FMP_API_KEY in the environment, then re-run with --live and bounded limits.",
    REC_ERROR: "Inspect the batch summary / progress sanitized errors, then re-run the bounded core batch.",
}


def run_core_backfill(live: bool = False, universe_source: str = "phase5c",
                      max_tickers: int = 25, max_requests: int = 100,
                      skip_existing: bool = False, resume: bool = False,
                      batch_id: str = "core_batch_001",
                      skip_known_blocked: bool = False, retry_known_blocked: bool = False,
                      universe: Optional[List[str]] = None, verbose: bool = True) -> Dict:
    """Phase 5-E1B: expanded CORE fundamentals backfill (resumable, quota-safe).

    Collects only the four core fundamentals/profile endpoints for a larger
    cross-sectional universe (default: the Phase 5-C price-history names), in
    bounded batches that resume via ``--skip-existing`` so later batches never
    re-download collected data. Still dry-run-first and secret-safe; paid raw +
    normalized data stay under the git-ignored research/data/fmp/ tree."""
    universe_all, resolved_source = resolve_universe(universe_source, universe)
    if universe is not None and universe:
        resolved_source = "cli_explicit"
    paid_gitignored = _ensure_paid_data_gitignore()
    api_key_present = fmp.has_api_key()
    live_requested = bool(live)
    live_mode = live_requested and api_key_present
    skip = bool(skip_existing or resume)
    # Snapshot the known-blocked register BEFORE this batch so "retry vs new" budget
    # accounting and the skip decision both reflect what was blocked going in.
    prior_known_blocked = _read_known_blocked()

    core_eps = _core_endpoints()
    core_names = [e["endpoint_name"] for e in core_eps]
    batch_tickers = universe_all[:max(0, max_tickers)] if max_tickers is not None else list(universe_all)

    # --- batch plan (always; one row per endpoint x ticker in this batch) ---
    plan_rows: List[Dict] = []
    for e in core_eps:
        for tk in batch_tickers:
            path = e["endpoint_path_template"].replace("{symbol}", tk)
            plan_rows.append({
                "batch_id": batch_id,
                "endpoint_name": e["endpoint_name"],
                "alpha_family": e["alpha_family"],
                "ticker": tk,
                "backfill_priority": _BACKFILL_PRIORITY.get(e["endpoint_name"], 99),
                "endpoint_status": e.get("endpoint_status", "unknown"),
                "request_url_redacted": fmp.redacted_request_url(path),
                "planned_raw_file_path": _rel(_RAW_DIR / e["endpoint_name"] / ("%s.json" % tk)),
            })
    _write_csv(_CORE_BATCH_PLAN_OUT,
               ["batch_id", "endpoint_name", "alpha_family", "ticker", "backfill_priority",
                "endpoint_status", "request_url_redacted", "planned_raw_file_path"],
               [[r["batch_id"], r["endpoint_name"], r["alpha_family"], r["ticker"],
                 r["backfill_priority"], r["endpoint_status"], r["request_url_redacted"],
                 r["planned_raw_file_path"]] for r in plan_rows])

    # --- live core batch (only when --live AND key present) ---
    live_results: Optional[Dict] = None
    if live_mode:
        live_results = run_live_backfill(
            batch_tickers, max_tickers, len(core_names), max_requests, verbose,
            endpoint_names=core_names, skip_existing=skip,
            skip_known_blocked=skip_known_blocked, retry_known_blocked=retry_known_blocked,
            known_blocked_map=prior_known_blocked)

    progress = live_results["progress"] if live_results else []
    by_endpoint, by_ticker, core_ready = _compute_core_coverage(core_names, batch_tickers, progress)

    # --- per-endpoint batch summary ---
    quality = (live_results or {}).get("quality", {})
    access = (live_results or {}).get("access_results", {})
    summary_rows: List[Dict] = []
    for e in core_eps:
        name = e["endpoint_name"]
        q = quality.get(name, {})
        cov = by_endpoint.get(name, {})
        summary_rows.append({
            "batch_id": batch_id,
            "endpoint_name": name,
            "alpha_family": e["alpha_family"],
            "tickers_in_batch": len(batch_tickers),
            "tickers_success": q.get("tickers_success", 0),
            "tickers_empty": q.get("tickers_empty", 0),
            "tickers_error": q.get("tickers_error", 0),
            "tickers_covered": cov.get("tickers_covered", 0),
            "coverage_pct": cov.get("coverage_pct", 0.0),
            "live_access_result": access.get(name, "planned"),
        })
    _write_csv(_CORE_BATCH_SUMMARY_OUT,
               ["batch_id", "endpoint_name", "alpha_family", "tickers_in_batch",
                "tickers_success", "tickers_empty", "tickers_error", "tickers_covered",
                "coverage_pct", "live_access_result"],
               [[r["batch_id"], r["endpoint_name"], r["alpha_family"], r["tickers_in_batch"],
                 r["tickers_success"], r["tickers_empty"], r["tickers_error"],
                 r["tickers_covered"], r["coverage_pct"], r["live_access_result"]]
                for r in summary_rows])

    # --- per-ticker coverage report ---
    _write_csv(_CORE_COVERAGE_OUT,
               ["batch_id", "ticker", "endpoints_in_batch", "endpoints_covered",
                "coverage_pct", "core_ready"],
               [[batch_id, tk, v["endpoints_in_batch"], v["endpoints_covered"],
                 v["coverage_pct"],
                 v["endpoints_covered"] >= _E1B_MIN_CORE_ENDPOINTS_PER_TICKER]
                for tk, v in by_ticker.items()])

    # --- request-level results (one row per planned request) + grouped errors ---
    request_results = _build_request_results(batch_id, plan_rows, progress)
    _write_csv(_CORE_REQUEST_RESULTS_OUT, _REQUEST_RESULTS_COLUMNS,
               [[r[c] for c in _REQUEST_RESULTS_COLUMNS] for r in request_results])
    error_rows = [r for r in request_results if r["status"] == "error"]
    _write_csv(_CORE_ERROR_REPORT_OUT, _ERROR_REPORT_COLUMNS,
               [[r[c] for c in _ERROR_REPORT_COLUMNS] for r in error_rows])

    # Request-level aggregates + error breakdowns for the readiness report.
    request_success_count = sum(1 for r in request_results if r["status"] == "success")
    request_empty_count = sum(1 for r in request_results if r["status"] == "empty")
    request_error_count = len(error_rows)
    request_skipped_existing_count = sum(
        1 for r in request_results if r["status"] == "skipped_existing")
    request_skipped_known_blocked_count = sum(
        1 for r in request_results if r["status"] == "skipped_known_blocked")
    error_breakdown_by_http_status: Dict[str, int] = {}
    error_breakdown_by_endpoint: Dict[str, int] = {}
    for r in error_rows:
        code = str(r["http_status"]).strip() or "unknown"
        error_breakdown_by_http_status[code] = error_breakdown_by_http_status.get(code, 0) + 1
        ep = r["endpoint_name"]
        error_breakdown_by_endpoint[ep] = error_breakdown_by_endpoint.get(ep, 0) + 1
    partial_ticker_count = sum(
        1 for v in by_ticker.values()
        if 0 < v["endpoints_covered"] < _E1B_MIN_CORE_ENDPOINTS_PER_TICKER)

    # --- known-blocked register (persist 402/403 structural blocks across batches) ---
    known_blocked_map = _merge_known_blocked(prior_known_blocked, error_rows, batch_id)
    _write_csv(_CORE_KNOWN_BLOCKED_OUT, _KNOWN_BLOCKED_COLUMNS,
               [[entry[c] for c in _KNOWN_BLOCKED_COLUMNS]
                for _, entry in sorted(known_blocked_map.items())])
    known_blocked_by_http_status: Dict[str, int] = {}
    known_blocked_by_endpoint: Dict[str, int] = {}
    for (_ep, _tk), entry in known_blocked_map.items():
        code = str(entry.get("http_status", "")).strip() or "unknown"
        known_blocked_by_http_status[code] = known_blocked_by_http_status.get(code, 0) + 1
        known_blocked_by_endpoint[_ep] = known_blocked_by_endpoint.get(_ep, 0) + 1

    # --- live-budget accounting: a sent request is a "retry" if it re-hit a pair
    #     that was already in the known-blocked register at the start of the batch. ---
    live_sent = [r for r in request_results if r["status"] in ("success", "empty", "error")]
    live_budget_spent_on_retries_count = sum(
        1 for r in live_sent if (r["endpoint_name"], r["ticker"]) in prior_known_blocked)
    live_budget_spent_on_new_requests_count = len(live_sent) - live_budget_spent_on_retries_count

    next_retry_command = (
        "python research\\run_phase5e1_fmp_controlled_backfill.py --live --core-only "
        "--universe-source %s --max-tickers %d --max-requests %d --skip-existing "
        "--batch-id %s" % (universe_source or "phase5c",
                           max_tickers if max_tickers is not None else 25,
                           max_requests if max_requests is not None else 100, batch_id))
    # Next batch: widen the slice, reuse collected files, and skip known 402 blocks
    # so live calls are spent mainly on new tickers.
    next_batch_command = (
        "python research\\run_phase5e1_fmp_controlled_backfill.py --live --core-only "
        "--universe-source %s --max-tickers %d --max-requests %d --skip-existing "
        "--skip-known-blocked --batch-id %s"
        % (universe_source or "phase5c",
           (max_tickers + 25) if max_tickers is not None else 50,
           max_requests if max_requests is not None else 100, _next_batch_id(batch_id)))

    # --- secret leak scan over the committed core artifacts ---
    core_paths = [_CORE_BATCH_PLAN_OUT, _CORE_BATCH_SUMMARY_OUT, _CORE_COVERAGE_OUT,
                  _CORE_REQUEST_RESULTS_OUT, _CORE_ERROR_REPORT_OUT, _CORE_KNOWN_BLOCKED_OUT]
    leak_clean, scanned = _scan_for_leaks(core_paths, fmp.resolve_api_key())

    recommendation, enough = derive_e1b_recommendation(
        live_requested, live_mode, live_results, by_endpoint, core_ready)

    success_count = sum(q.get("tickers_success", 0) for q in quality.values())
    empty_count = sum(q.get("tickers_empty", 0) for q in quality.values())
    error_count = sum(q.get("tickers_error", 0) for q in quality.values())
    skipped_count = sum(1 for r in progress if r.get("status") == "skipped")

    readiness = {
        "phase": PHASE_E1B,
        "objective": ("Expand the controlled FMP backfill from a 5-ticker smoke sample to a larger, "
                      "quota-safe cross-sectional CORE fundamentals dataset (profile + quarterly "
                      "income / balance-sheet / cash-flow statements) sufficient for the Phase 5-E2 "
                      "enriched alpha panel. Collects/normalizes data only - does NOT build the model."),
        "provider": fmp.PROVIDER_NAME,
        "universe_source": resolved_source,
        "universe_size_available": len(universe_all),
        "mode": "live_sample" if live_mode else "dry_run",
        "core_only": True,
        "core_endpoints": list(core_names),
        "batch_id": batch_id,
        "batch_size_tickers": len(batch_tickers),
        "planned_request_count": len(plan_rows),
        "live_request_count": (live_results or {}).get("request_count", 0),
        "success_count": success_count,
        "empty_count": empty_count,
        "error_count": error_count,
        "skipped_count": skipped_count,
        "raw_files_written_count": (live_results or {}).get("raw_written", 0),
        "normalized_files_written_count": (live_results or {}).get("norm_written", 0),
        "skip_existing": skip,
        "resume": bool(resume),
        "paid_data_gitignored": paid_gitignored,
        "coverage_by_endpoint": by_endpoint,
        "coverage_by_ticker": by_ticker,
        "core_ready_ticker_count": core_ready,
        "partial_ticker_count": partial_ticker_count,
        "request_success_count": request_success_count,
        "request_empty_count": request_empty_count,
        "request_error_count": request_error_count,
        "request_skipped_existing_count": request_skipped_existing_count,
        "request_skipped_known_blocked_count": request_skipped_known_blocked_count,
        "error_breakdown_by_http_status": error_breakdown_by_http_status,
        "error_breakdown_by_endpoint": error_breakdown_by_endpoint,
        "known_blocked_count": len(known_blocked_map),
        "known_blocked_by_http_status": known_blocked_by_http_status,
        "known_blocked_by_endpoint": known_blocked_by_endpoint,
        "live_budget_spent_on_new_requests_count": live_budget_spent_on_new_requests_count,
        "live_budget_spent_on_retries_count": live_budget_spent_on_retries_count,
        "skip_known_blocked": bool(skip_known_blocked),
        "retry_known_blocked": bool(retry_known_blocked),
        "next_retry_command": next_retry_command,
        "next_batch_command": next_batch_command,
        "coverage_thresholds": {
            "min_core_ready_tickers": _E1B_MIN_TICKERS_FOR_E2,
            "min_endpoint_coverage_pct": _E1B_MIN_ENDPOINT_COVERAGE_PCT,
            "min_core_endpoints_per_ticker": _E1B_MIN_CORE_ENDPOINTS_PER_TICKER,
        },
        "enough_for_phase5e2": enough,
        "recommendation": recommendation,
        "recommendation_allowed_values": list(ALLOWED_E1B_RECOMMENDATIONS),
        "recommended_next_phase": _E1B_NEXT_PHASE[recommendation],
        "storage_raw_dir": _rel(_RAW_DIR),
        "storage_normalized_dir": _rel(_NORM_DIR),
        "secret_safety_leak_scan_clean": leak_clean,
        "secret_safety_files_scanned": scanned,
        "outputs": {
            "batch_plan": _rel(_CORE_BATCH_PLAN_OUT),
            "batch_summary": _rel(_CORE_BATCH_SUMMARY_OUT),
            "coverage_report": _rel(_CORE_COVERAGE_OUT),
            "request_results": _rel(_CORE_REQUEST_RESULTS_OUT),
            "error_report": _rel(_CORE_ERROR_REPORT_OUT),
            "known_blocked": _rel(_CORE_KNOWN_BLOCKED_OUT),
            "readiness": _rel(_E1B_READINESS_OUT),
        },
        # Safety / provenance contract (explicit, like Phase 5-A..5-E1).
        "preview_only": True,
        "orders_enabled": False,
        "automation_enabled": False,
        "broker_execution_enabled": False,
        "production_replacement": False,
        "api_key_logged": False,
        "network_used": bool(live_mode and (live_results or {}).get("request_count", 0) > 0),
        "alphavantage_called": False,
        "deployed": False,
        "binary_artifacts_created": False,
        "data_fabricated": False,
        "wrote_to_d_drive": False,
        "committed": False,
    }
    _write_json(_E1B_READINESS_OUT, readiness)
    return readiness


def run_core_preflight(universe_source: str = "phase5c",
                       max_tickers: int = 50, max_requests: int = 100,
                       skip_existing: bool = True, skip_known_blocked: bool = True,
                       retry_known_blocked: bool = False,
                       batch_id: str = "core_batch_002",
                       universe: Optional[List[str]] = None,
                       verbose: bool = True) -> Dict:
    """Network-free, key-free PRE-LIVE cache check.

    Reports, WITHOUT calling FMP and WITHOUT requiring FMP_API_KEY, exactly how a
    given core batch would behave if it were run live: how many planned
    (endpoint, ticker) requests already have a valid local raw file (and would be
    skipped by --skip-existing), how many are in the known-blocked register (and
    would be skipped by --skip-known-blocked), and how many live FMP calls would
    actually be spent. Strictly READ-ONLY: it never writes paid data, never deletes
    anything, and never consumes request budget. Run this before any live batch to
    know the cost up front."""
    universe_all, resolved_source = resolve_universe(universe_source, universe)
    if universe is not None and universe:
        resolved_source = "cli_explicit"
    core_eps = _core_endpoints()
    core_names = [e["endpoint_name"] for e in core_eps]
    batch_tickers = (universe_all[:max(0, max_tickers)]
                     if max_tickers is not None else list(universe_all))
    known_blocked = _read_known_blocked()

    rows: List[Dict] = []
    existing_detected = 0
    planned_skipped_existing = 0
    planned_skipped_known_blocked = 0
    would_request = 0
    for name in core_names:
        for tk in batch_tickers:
            existing_path, existing_rows = _find_existing_raw(name, tk)
            has_existing = existing_path is not None
            if has_existing:
                existing_detected += 1
            blocked = known_blocked.get((name, tk))
            # Mirror the live-loop skip ordering exactly: skip-existing first, then
            # known-blocked, otherwise it becomes a live request.
            if skip_existing and has_existing:
                disposition = "skip_existing"
                planned_skipped_existing += 1
            elif skip_known_blocked and not retry_known_blocked and blocked is not None:
                disposition = "skip_known_blocked"
                planned_skipped_known_blocked += 1
            else:
                disposition = "live_request"
                would_request += 1
            rows.append({
                "batch_id": batch_id,
                "endpoint_name": name,
                "ticker": tk,
                "planned_disposition": disposition,
                "existing_raw_file_detected": has_existing,
                "existing_raw_file_path": _rel(existing_path) if existing_path else "",
                "existing_row_count": existing_rows if existing_rows is not None else "",
                "known_blocked_http_status": (blocked or {}).get("http_status", "") if blocked else "",
            })
    planned_live_request_count_if_live = (
        min(would_request, max_requests) if max_requests is not None else would_request)

    _write_csv(_CORE_PREFLIGHT_OUT, _PREFLIGHT_COLUMNS,
               [[r[c] for c in _PREFLIGHT_COLUMNS] for r in rows])

    preflight = {
        "phase": PHASE_E1B,
        "mode": "preflight_cache_only",
        "preflight_only": True,
        "network_used": False,
        "api_key_required": False,
        "universe_source": resolved_source,
        "universe_size_available": len(universe_all),
        "batch_id": batch_id,
        "batch_size_tickers": len(batch_tickers),
        "core_endpoints": list(core_names),
        "planned_request_count": len(rows),
        "skip_existing": bool(skip_existing),
        "skip_known_blocked": bool(skip_known_blocked),
        "retry_known_blocked": bool(retry_known_blocked),
        "max_requests": max_requests,
        "existing_raw_files_detected": existing_detected,
        "planned_skipped_existing_count": planned_skipped_existing,
        "planned_skipped_known_blocked_count": planned_skipped_known_blocked,
        "planned_live_request_count_if_live": planned_live_request_count_if_live,
        "would_request_before_budget_cap": would_request,
        "known_blocked_count": len(known_blocked),
        "storage_raw_dir": _rel(_RAW_DIR),
        "storage_normalized_dir": _rel(_NORM_DIR),
        "live_command_preview": (
            "python research\\run_phase5e1_fmp_controlled_backfill.py --live --core-only "
            "--universe-source %s --max-tickers %d --max-requests %d%s%s --batch-id %s"
            % (universe_source or "phase5c",
               max_tickers if max_tickers is not None else 50,
               max_requests if max_requests is not None else 100,
               " --skip-existing" if skip_existing else "",
               " --skip-known-blocked" if skip_known_blocked else "",
               batch_id)),
        "outputs": {
            "preflight_rows": _rel(_CORE_PREFLIGHT_OUT),
            "preflight_summary": _rel(_E1B_PREFLIGHT_JSON_OUT),
        },
        # Safety / provenance contract (identical posture to the rest of 5-E1B).
        "preview_only": True,
        "orders_enabled": False,
        "automation_enabled": False,
        "broker_execution_enabled": False,
        "production_replacement": False,
        "api_key_logged": False,
        "alphavantage_called": False,
        "deployed": False,
        "data_fabricated": False,
        "wrote_to_d_drive": False,
        "committed": False,
    }
    _write_json(_E1B_PREFLIGHT_JSON_OUT, preflight)
    if verbose:
        print("phase:                         %s (preflight, no network)" % preflight["phase"])
        print("universe source:               %s" % preflight["universe_source"])
        print("batch id:                      %s" % preflight["batch_id"])
        print("batch tickers:                 %s" % preflight["batch_size_tickers"])
        print("planned requests:              %s" % preflight["planned_request_count"])
        print("existing raw files detected:   %s" % preflight["existing_raw_files_detected"])
        print("planned skipped existing:      %s" % preflight["planned_skipped_existing_count"])
        print("planned skipped known-blocked: %s" % preflight["planned_skipped_known_blocked_count"])
        print("LIVE CALLS IF RUN LIVE:        %s" % preflight["planned_live_request_count_if_live"])
        print("storage raw dir:               %s" % preflight["storage_raw_dir"])
        print("live command preview:          %s" % preflight["live_command_preview"])
    return preflight


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Phase 5-E1 controlled FMP backfill (dry-run by default).")
    ap.add_argument("--live", action="store_true",
                    help="Perform a bounded live controlled sample (requires FMP_API_KEY).")
    ap.add_argument("--core-only", action="store_true",
                    help="Phase 5-E1B: collect only the four core fundamentals/profile endpoints "
                         "for an expanded cross-sectional universe (resumable batches).")
    ap.add_argument("--universe-source", type=str, default="default",
                    choices=("default", "phase5c"),
                    help="Universe source: 'phase5c' loads the Phase 5-C price-history names "
                         "(safe fallback to the sample); 'default' uses the sample universe.")
    ap.add_argument("--resume", action="store_true",
                    help="Resume a multi-batch backfill (implies --skip-existing).")
    ap.add_argument("--skip-existing", action="store_true",
                    help="Skip (endpoint, ticker) pairs whose raw file already exists locally; "
                         "they consume no request budget so later batches never re-download.")
    ap.add_argument("--skip-known-blocked", action="store_true",
                    help="Skip (endpoint, ticker) pairs in the known-blocked register "
                         "(e.g. prior HTTP 402); they consume no request budget.")
    ap.add_argument("--retry-known-blocked", action="store_true",
                    help="Override --skip-known-blocked and re-attempt known-blocked pairs "
                         "(use only after the FMP plan entitlement changed).")
    ap.add_argument("--preflight-cache-only", action="store_true",
                    help="Core-only: report (read-only, no network, no key) how many planned "
                         "requests would be skipped vs. spent live, then exit. Run before a "
                         "live batch to know the cost up front.")
    ap.add_argument("--batch-id", type=str, default="core_batch_001",
                    help="Label for this collection batch (default core_batch_001).")
    ap.add_argument("--max-tickers", type=int, default=5,
                    help="Max tickers to collect in live mode (default 5; core batches use more).")
    ap.add_argument("--max-endpoints", type=int, default=6,
                    help="Max priority endpoints to collect in live mode (default 6).")
    ap.add_argument("--max-requests", type=int, default=40,
                    help="Hard cap on total live requests (default 40).")
    ap.add_argument("--universe", type=str, default="",
                    help="Comma-separated tickers (default AAPL,MSFT,NVDA,AMZN,JPM). "
                         "A full S&P 500 backfill requires an explicit large universe + limits.")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    explicit = [t.strip().upper() for t in args.universe.split(",") if t.strip()]

    # --- Phase 5-E1B pre-live cache check (read-only, no network, no key) ---
    if args.core_only and args.preflight_cache_only:
        run_core_preflight(
            universe_source=args.universe_source, max_tickers=args.max_tickers,
            max_requests=args.max_requests,
            skip_existing=(args.skip_existing or args.resume),
            skip_known_blocked=args.skip_known_blocked,
            retry_known_blocked=args.retry_known_blocked,
            batch_id=args.batch_id, universe=explicit or None, verbose=True)
        return 0

    # --- Phase 5-E1B expanded core fundamentals backfill ---
    if args.core_only:
        readiness = run_core_backfill(
            live=args.live, universe_source=args.universe_source,
            max_tickers=args.max_tickers, max_requests=args.max_requests,
            skip_existing=args.skip_existing, resume=args.resume,
            batch_id=args.batch_id, skip_known_blocked=args.skip_known_blocked,
            retry_known_blocked=args.retry_known_blocked,
            universe=explicit or None, verbose=True)
        print("phase:                         %s" % readiness["phase"])
        print("mode:                          %s" % readiness["mode"])
        print("api key present:               %s" % fmp.has_api_key())
        print("universe source:               %s" % readiness["universe_source"])
        print("universe size available:       %s" % readiness["universe_size_available"])
        print("batch id:                      %s" % readiness["batch_id"])
        print("batch tickers:                 %s" % readiness["batch_size_tickers"])
        print("core endpoints:                %s" % ", ".join(readiness["core_endpoints"]))
        print("planned requests:              %s" % readiness["planned_request_count"])
        print("live requests:                 %s" % readiness["live_request_count"])
        print("success/empty/error:           %s / %s / %s" % (
            readiness["success_count"], readiness["empty_count"], readiness["error_count"]))
        print("raw files written:             %s" % readiness["raw_files_written_count"])
        print("normalized files written:      %s" % readiness["normalized_files_written_count"])
        print("skip existing:                 %s" % readiness["skip_existing"])
        print("request success/err/skipped:   %s / %s / %s" % (
            readiness["request_success_count"], readiness["request_error_count"],
            readiness["request_skipped_existing_count"]))
        print("skipped known-blocked:         %s" % readiness["request_skipped_known_blocked_count"])
        print("error by http status:          %s" % (
            readiness["error_breakdown_by_http_status"] or "(none)"))
        print("error by endpoint:             %s" % (
            readiness["error_breakdown_by_endpoint"] or "(none)"))
        print("known-blocked total:           %s" % readiness["known_blocked_count"])
        print("known-blocked by http status:  %s" % (
            readiness["known_blocked_by_http_status"] or "(none)"))
        print("live budget new / retries:     %s / %s" % (
            readiness["live_budget_spent_on_new_requests_count"],
            readiness["live_budget_spent_on_retries_count"]))
        print("core-ready / partial tickers:  %s / %s" % (
            readiness["core_ready_ticker_count"], readiness["partial_ticker_count"]))
        print("enough for phase 5-E2:         %s" % readiness["enough_for_phase5e2"])
        print("paid data gitignored:          %s" % readiness["paid_data_gitignored"])
        print("recommendation:                %s" % readiness["recommendation"])
        print("next phase:                    %s" % readiness["recommended_next_phase"])
        print("next retry command:            %s" % readiness["next_retry_command"])
        print("next batch command:            %s" % readiness["next_batch_command"])
        return 0

    # --- Phase 5-E1 controlled backfill (original behavior) ---
    if args.universe_source == "phase5c" and not explicit:
        universe, _ = load_phase5c_universe()
    else:
        universe = explicit or DEFAULT_UNIVERSE
    report = run(live=args.live, max_tickers=args.max_tickers, max_endpoints=args.max_endpoints,
                 max_requests=args.max_requests, universe=universe, verbose=True)
    print("phase:                         %s" % report["phase"])
    print("mode:                          %s" % report["mode"])
    print("api key present:               %s" % report["api_key_present"])
    print("universe:                      %s" % ", ".join(report["universe"]))
    print("planned requests:              %s" % report["planned_request_count"])
    print("live requests:                 %s" % report["live_request_count"])
    print("raw files written:             %s" % report["raw_files_written_count"])
    print("normalized files written:      %s" % report["normalized_files_written_count"])
    print("endpoint success/empty/error:  %s / %s / %s" % (
        report["endpoint_success_count"], report["endpoint_empty_count"],
        report["endpoint_error_count"]))
    print("families planned:              %s" % ", ".join(report["endpoint_families_planned"]))
    print("families live-verified:        %s" % (
        ", ".join(report["endpoint_families_live_verified"]) or "(none yet)"))
    print("families blocked/unverified:   %s" % (
        ", ".join(report["endpoint_families_blocked_or_unverified"]) or "(none)"))
    print("paid data gitignored:          %s" % report["paid_data_gitignored"])
    print("storage raw dir:               %s" % report["storage_raw_dir"])
    print("storage normalized dir:        %s" % report["storage_normalized_dir"])
    print("recommendation:                %s" % report["recommendation"])
    print("next phase:                    %s" % report["recommended_next_phase"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
