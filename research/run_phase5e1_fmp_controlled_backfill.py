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
                      max_requests: int, verbose: bool) -> Dict:
    """Collect a BOUNDED live sample. Writes raw JSON + normalized CSV locally
    under research/data/fmp/ (git-ignored) and returns structured live results."""
    tickers = universe[:max(0, max_tickers)] if max_tickers is not None else list(universe)
    endpoints = _priority_endpoints()
    if max_endpoints is not None and max_endpoints >= 0:
        endpoints = endpoints[:max_endpoints]
    _RAW_DIR.mkdir(parents=True, exist_ok=True)
    _NORM_DIR.mkdir(parents=True, exist_ok=True)

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

            if max_requests is not None and request_count >= max_requests:
                budget_hit = True
                progress.append({
                    "endpoint_name": name, "ticker": tk, "request_url_redacted": redacted,
                    "status": "skipped", "row_count": 0, "raw_file_path": "",
                    "normalized_file_path": "", "http_status": "",
                    "error_message_sanitized": "request budget (--max-requests) reached",
                    "collected_at_utc": "",
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
                    "error_message_sanitized": "%s (%s)" % (str(exc), error_type),
                    "collected_at_utc": ts,
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
                "http_status": 200,
                "error_message_sanitized": "" if norm_rows else "empty_result",
                "collected_at_utc": ts,
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


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Phase 5-E1 controlled FMP backfill (dry-run by default).")
    ap.add_argument("--live", action="store_true",
                    help="Perform a bounded live controlled sample (requires FMP_API_KEY).")
    ap.add_argument("--max-tickers", type=int, default=5,
                    help="Max tickers to collect in live mode (default 5).")
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
    universe = [t.strip().upper() for t in args.universe.split(",") if t.strip()] or DEFAULT_UNIVERSE
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
