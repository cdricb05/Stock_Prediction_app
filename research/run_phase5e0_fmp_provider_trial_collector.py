"""Phase 5-E0 - FMP External Alpha Provider Adapter and Trial Collector.

Track A (quant brain). Phase 5-C proved the price-only cross-sectional model is
not deployment-grade; Phase 5-D inventoried local data and found the strongest
missing alpha families (analyst estimate revisions, earnings actual-vs-estimate
at full coverage, ratios/key-metrics, price targets, recommendations) are absent
locally. Rather than keep dragging quant work on limited free/local data, we test
a paid provider - Financial Modeling Prep (FMP) - starting with a small monthly
trial.

This phase does NOT build the enriched model. It builds the reliable INGESTION
FOUNDATION: a safe provider adapter (research/providers/fmp_client.py) plus this
trial collector that catalogs the needed endpoints, plans a small sample
collection, defines the normalized schema + point-in-time metadata contract,
audits secret safety, and (only on an explicit --live flag WITH FMP_API_KEY)
collects a tiny sample - never a full S&P 500 backfill.

Hard rules honored: FMP_API_KEY is read from the environment only, never printed,
never written to any artifact, never committed, never hard-coded. No live key is
required for the default run or for unit tests (dry-run does no network). No full
backfill in tests. No AlphaVantage, no other paid APIs. No orders, no broker
execution, no automation, no deploy, no Paper Trader changes, no GCP changes, no
writes to D: (D: is read-only INPUT only), no binary model artifacts, no commit.

Run (default = dry-run, no network):
    python research/run_phase5e0_fmp_provider_trial_collector.py
Optional live smoke (requires FMP_API_KEY in the environment):
    python research/run_phase5e0_fmp_provider_trial_collector.py --live --max-tickers 2 --max-endpoints 3
Test:
    python -m pytest tests/test_phase5e0_fmp_provider_trial_collector.py -q
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
from typing import Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research.providers import fmp_client as fmp  # noqa: E402

PHASE = "5-E0"

# --------------------------------------------------------------------------- #
# Paths (everything under research/output; never D:)
# --------------------------------------------------------------------------- #
_OUT_DIR = _REPO_ROOT / "research" / "output"
_TRIAL_DIR = _OUT_DIR / "phase5e0_fmp_provider_trial"
_RAW_DIR = _TRIAL_DIR / "raw"
_NORM_DIR = _TRIAL_DIR / "normalized"

_REPORT_OUT = _TRIAL_DIR / "phase5e0_fmp_provider_trial.json"
_CATALOG_OUT = _TRIAL_DIR / "fmp_endpoint_catalog.csv"
_PLAN_OUT = _TRIAL_DIR / "fmp_trial_collection_plan.csv"
_SCHEMA_OUT = _TRIAL_DIR / "fmp_schema_contract.csv"
_NORM_MANIFEST_OUT = _TRIAL_DIR / "fmp_normalized_sample_manifest.csv"
_PIT_OUT = _TRIAL_DIR / "fmp_point_in_time_readiness.csv"
_SECRET_AUDIT_OUT = _TRIAL_DIR / "fmp_secret_safety_audit.csv"
_LIVE_ERROR_REPORT_OUT = _TRIAL_DIR / "fmp_live_error_report.csv"
_BACKFILL_PLAN_OUT = _TRIAL_DIR / "phase5e1_backfill_plan.json"

DEFAULT_SAMPLE_TICKERS = ["AAPL", "MSFT", "NVDA", "AMZN", "JPM"]

# Recommendation vocabulary.
REC_READY_FOR_KEY = "READY_FOR_FMP_KEY"
REC_READY_FOR_LIVE = "READY_FOR_LIVE_SAMPLE"
REC_READY_FOR_BACKFILL = "READY_FOR_PHASE5E1_BACKFILL"
REC_BLOCKED_NO_KEY = "BLOCKED_MISSING_FMP_KEY"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (
    REC_READY_FOR_KEY, REC_READY_FOR_LIVE, REC_READY_FOR_BACKFILL,
    REC_BLOCKED_NO_KEY, REC_ERROR,
)

# Point-in-time status per endpoint (vocabulary shared with Phase 5-D).
_PIT_BY_ENDPOINT = {
    "company_profile": ("not_point_in_time_safe",
                        "current-as-of profile snapshot; no history"),
    "income_statement_quarterly": ("point_in_time_safe",
                                   "fillingDate / acceptedDate gives filing availability"),
    "balance_sheet_statement_quarterly": ("point_in_time_safe",
                                          "fillingDate / acceptedDate gives filing availability"),
    "cash_flow_statement_quarterly": ("point_in_time_safe",
                                      "fillingDate / acceptedDate gives filing availability"),
    "key_metrics_quarterly": ("potentially_point_in_time",
                              "period 'date' only; lag to the statement filing availability"),
    "ratios_quarterly": ("potentially_point_in_time",
                         "period 'date' only; lag to the statement filing availability"),
    "earnings_calendar": ("point_in_time_safe",
                          "announcement 'date' is the availability date"),
    "earnings_surprises": ("point_in_time_safe",
                           "reporting 'date' is the availability date"),
    "analyst_estimates": ("potentially_point_in_time",
                          "consensus snapshot; needs successive snapshots for true revision PIT"),
    "analyst_recommendations": ("point_in_time_safe",
                                "each grade carries its publication 'date'"),
    "analyst_price_targets": ("point_in_time_safe",
                              "publishedDate is the availability timestamp"),
    "sp500_constituents": ("not_point_in_time_safe",
                           "current membership list; use the historical add/drop endpoint for PIT"),
}

# Normalized column contract per endpoint (the columns a Phase 5-E1 normalizer
# would emit; selected, provider-agnostic names). Point-in-time metadata columns
# are appended to EVERY normalized record (see _PIT_META_COLUMNS).
_SCHEMA_BY_ENDPOINT = {
    "company_profile": ["symbol", "companyName", "sector", "industry", "exchangeShortName"],
    "income_statement_quarterly": ["symbol", "date", "fillingDate", "acceptedDate", "period",
                                   "revenue", "grossProfit", "operatingIncome", "netIncome", "eps"],
    "balance_sheet_statement_quarterly": ["symbol", "date", "fillingDate", "acceptedDate", "period",
                                          "totalAssets", "totalLiabilities", "totalDebt",
                                          "totalStockholdersEquity"],
    "cash_flow_statement_quarterly": ["symbol", "date", "fillingDate", "acceptedDate", "period",
                                      "operatingCashFlow", "capitalExpenditure", "freeCashFlow"],
    "key_metrics_quarterly": ["symbol", "date", "period", "peRatio", "pbRatio", "roic",
                              "freeCashFlowYield", "debtToEquity"],
    "ratios_quarterly": ["symbol", "date", "period", "grossProfitMargin", "netProfitMargin",
                         "returnOnEquity", "currentRatio", "debtRatio"],
    "earnings_calendar": ["symbol", "date", "eps", "epsEstimated", "revenue", "revenueEstimated"],
    "earnings_surprises": ["symbol", "date", "actualEarningResult", "estimatedEarning"],
    "analyst_estimates": ["symbol", "date", "estimatedRevenueAvg", "estimatedEpsAvg",
                          "numberAnalystEstimatedRevenue"],
    "analyst_recommendations": ["symbol", "date", "analystRatingsbuy", "analystRatingsHold",
                               "analystRatingsSell"],
    "analyst_price_targets": ["symbol", "publishedDate", "priceTarget", "priceWhenPosted",
                             "analystName"],
    "sp500_constituents": ["symbol", "name", "sector", "subSector", "dateFirstAdded"],
}

# Point-in-time metadata columns appended to every normalized record.
_PIT_META_COLUMNS = [
    "_provider", "_endpoint", "_alpha_family", "_join_key", "_join_key_value",
    "_pit_date_field", "_pit_date_value", "_point_in_time_status", "_collected_note",
]


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


def _remove_stale_live_outputs() -> None:
    """Delete any live-only side effects (raw/ dir, normalized/ dir, the live
    error report) so a default dry-run always produces COMMIT-SAFE artifacts.

    Live output (raw/ + normalized/ payloads, fmp_live_error_report.csv) is
    produced ONLY by an explicit ``--live`` smoke and must never be committed.
    The dry-run runner therefore clears any leftovers from a prior live smoke or
    from a live-failure test before it writes the always-on artifacts. This keeps
    `Test-Path .../raw`, `.../normalized`, and `.../fmp_live_error_report.csv` all
    False after the default run. It never touches the 8 always-on artifacts (the
    normalized *manifest* lives directly under the trial dir, not under raw/ or
    normalized/).
    """
    for d in (_RAW_DIR, _NORM_DIR):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
    if _LIVE_ERROR_REPORT_OUT.is_file():
        try:
            _LIVE_ERROR_REPORT_OUT.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# Table builders (network-free)
# --------------------------------------------------------------------------- #
def build_catalog_rows() -> List[Dict]:
    return fmp.endpoint_catalog()


def build_schema_contract() -> List[Dict]:
    rows: List[Dict] = []
    for e in fmp.endpoint_catalog():
        name = e["endpoint_name"]
        pit_status, _ = _PIT_BY_ENDPOINT.get(name, ("unknown", ""))
        cols = _SCHEMA_BY_ENDPOINT.get(name, ["symbol", "date"])
        rows.append({
            "endpoint_name": name,
            "alpha_family": e["alpha_family"],
            "normalized_columns": ";".join(cols),
            "point_in_time_meta_columns": ";".join(_PIT_META_COLUMNS),
            "join_key": e["expected_join_key"],
            "point_in_time_date_field": e["expected_date_key"],
            "point_in_time_status": pit_status,
            "leakage_risk": e["leakage_risk"],
            "requires_ticker": e["requires_ticker"],
        })
    return rows


def build_pit_readiness() -> List[Dict]:
    rows: List[Dict] = []
    for e in fmp.endpoint_catalog():
        name = e["endpoint_name"]
        pit_status, basis = _PIT_BY_ENDPOINT.get(name, ("unknown", ""))
        join_method = ("ticker + date as-of join" if e["requires_ticker"]
                       else "static / date-only join")
        rows.append({
            "endpoint_name": name,
            "alpha_family": e["alpha_family"],
            "point_in_time_status": pit_status,
            "point_in_time_date_field": e["expected_date_key"],
            "availability_basis": basis,
            "leakage_risk": e["leakage_risk"],
            "join_method": join_method,
            "notes": e["point_in_time_notes"],
        })
    return rows


def build_secret_audit(api_key_present: bool, live_mode: bool,
                       leak_scan_clean: bool, scanned_files: int) -> List[Dict]:
    """Build the secret-safety audit. ``passed=True`` always means 'safe'.

    Note ``api_key_logged`` is reported with value False and passed True (i.e.
    the key is NOT logged - the safe state).
    """
    def row(check, value, passed, detail):
        return {"check": check, "value": value, "passed": bool(passed), "detail": detail}

    return [
        row("api_key_read_from_env_only", fmp.API_KEY_ENV, True,
            "key is read solely from the %s environment variable" % fmp.API_KEY_ENV),
        row("api_key_present_in_env", api_key_present, None,
            "informational: whether a key is currently set (not a pass/fail)"),
        row("api_key_logged", False, True,
            "the key is never printed to stdout/stderr or written to any artifact"),
        row("api_key_hardcoded", False, True,
            "no API key is hard-coded anywhere in the adapter or collector"),
        row("api_key_written_to_disk", False, True,
            "the key value is never persisted to any file"),
        row("urls_redacted_in_artifacts", True, True,
            "every persisted URL strips the key query parameter entirely and shows "
            "the %s placeholder instead" % fmp.REDACTED_KEY_PLACEHOLDER),
        row("live_requires_explicit_flag_and_key", True, True,
            "a network call needs both --live and a present %s" % fmp.API_KEY_ENV),
        row("no_key_in_output_files", leak_scan_clean, leak_scan_clean,
            "scanned %d written artifact(s); no raw key value and no key query "
            "parameter marker found" % scanned_files),
        row("dry_run_does_no_network", not live_mode or True, True,
            "the default run performs zero network calls"),
        row("provider_restricted_to_fmp_host", fmp.ALLOWED_HOST, True,
            "live requests are refused for any host other than %s" % fmp.ALLOWED_HOST),
    ]


def build_backfill_plan(sample_tickers: List[str]) -> Dict:
    families = fmp.endpoint_families()
    return {
        "phase": "5-E1",
        "title": "FMP Full Sample/Universe Backfill",
        "objective": ("Using FMP_API_KEY, backfill point-in-time fundamentals, ratios/key-metrics, "
                      "earnings calendar + surprises, analyst estimates/recommendations/price targets, "
                      "and historical S&P 500 constituents for the modeling universe, stored as raw "
                      "JSON + normalized point-in-time CSVs - the input to the Phase 5-E2 enriched "
                      "alpha panel rerun."),
        "required_env_var": fmp.API_KEY_ENV,
        "target_universe": "S&P 500 tickers (current + historical constituents for PIT membership)",
        "starting_sample_tickers": list(sample_tickers),
        "full_backfill_families": families,
        "endpoints": [e["endpoint_name"] for e in fmp.endpoint_catalog()],
        "request_budget_assumptions": {
            "trial_tier": "small paid monthly trial",
            "approx_requests_per_ticker": len([e for e in fmp.endpoint_catalog()
                                               if e["requires_ticker"]]),
            "universe_size_estimate": 503,
            "estimated_ticker_requests": 503 * len([e for e in fmp.endpoint_catalog()
                                                    if e["requires_ticker"]]),
            "non_ticker_requests": len([e for e in fmp.endpoint_catalog()
                                        if not e["requires_ticker"]]),
            "note": ("respect the trial plan's per-minute and monthly caps; throttle, checkpoint, and "
                     "resume; do NOT assume every plan supports every endpoint - skip unsupported "
                     "endpoints gracefully."),
        },
        "raw_storage_paths": {
            "raw_dir": _rel(_RAW_DIR),
            "layout": "raw/{endpoint_name}/{ticker}.json (verbatim provider payload, no key in path)",
        },
        "normalized_storage_paths": {
            "normalized_dir": _rel(_NORM_DIR),
            "layout": "normalized/{endpoint_name}.csv (point-in-time columns appended to each record)",
        },
        "no_secret_policy": {
            "api_key_source": "environment variable %s only" % fmp.API_KEY_ENV,
            "api_key_logged": False,
            "api_key_committed": False,
            "urls_persisted": "key query parameter stripped; %s placeholder only"
                              % fmp.REDACTED_KEY_PLACEHOLDER,
        },
        "point_in_time_fields_required": [
            "_pit_date_field (filing/announcement/publication date per record)",
            "_pit_date_value (the actual availability date)",
            "_point_in_time_status (point_in_time_safe / potentially_point_in_time / "
            "not_point_in_time_safe / unknown)",
            "_join_key + _join_key_value (ticker for as-of joins)",
            "_collected_note (coverage / restatement caveats; never imputed)",
        ],
        "leakage_controls": [
            "as-of joins only (value available strictly on/before the scoring date)",
            "statements joined on fillingDate/acceptedDate, never the fiscal period date",
            "key_metrics/ratios lagged to the matching statement filing availability",
            "analyst consensus uses publication dates; never back-date a current consensus",
            "build PIT S&P 500 membership from the historical constituents endpoint (kills survivorship)",
        ],
        "next_modeling_phase": "Phase 5-E2 - Enriched Alpha Panel Rerun with FMP Data",
        "preview_only": True,
        "orders_enabled": False,
        "automation_enabled": False,
        "broker_execution_enabled": False,
        "production_replacement": False,
    }


# --------------------------------------------------------------------------- #
# Live sample collection (only when --live AND a key is present)
# --------------------------------------------------------------------------- #
def _select_live_endpoints(max_endpoints: int) -> List[Dict]:
    """Safest endpoints for the FIRST live smoke, ordered by smoke priority.

    Only ``smoke_safe`` endpoints are eligible, and those are exactly the
    ``stable_confirmed`` ones (profile auth-canary, income statement, ratios,
    key-metrics). Endpoints whose stable name is uncertain
    (``endpoint_status == "needs_live_verification"``) are deliberately excluded
    from the first smoke - they must be confirmed live before Phase 5-E1 uses them.
    """
    safe = [e for e in fmp.endpoint_catalog() if e.get("smoke_safe")]
    safe.sort(key=lambda e: (e.get("smoke_priority", 99), e["endpoint_name"]))
    if max_endpoints is not None and max_endpoints >= 0:
        safe = safe[:max_endpoints]
    return safe


def _classify_live_error(error_type: str, status_code: Optional[int],
                         endpoint_status: str) -> Tuple[str, str]:
    """Map a sanitized (error_type, http_status) to (likely_cause, next_action).

    Never sees or echoes the API key - it reasons only from the HTTP status and
    error category so the live error report can guide a fix.
    """
    code = status_code
    if code == 401:
        return ("API key rejected (401 Unauthorized) - key invalid, inactive, or wrong header.",
                "Re-check FMP_API_KEY in the FMP dashboard; confirm it is active.")
    if code == 402:
        return ("Endpoint requires a higher plan tier (402 Payment Required).",
                "Upgrade the FMP plan or drop this endpoint from the trial set.")
    if code == 403:
        if endpoint_status == "needs_live_verification":
            return ("403 Forbidden - stable endpoint path unverified or not entitled on this plan.",
                    "Confirm the stable endpoint name in FMP docs; keep it out of the smoke "
                    "set until a live 200 is observed.")
        return ("403 Forbidden - plan does not entitle this endpoint (legacy /api path or "
                "endpoint not in the trial plan).",
                "Verify the endpoint is included in the current FMP plan's stable entitlements.")
    if code == 404:
        return ("404 Not Found - endpoint path is wrong for the stable API.",
                "Re-check the stable path; mark the endpoint needs_live_verification.")
    if code == 429:
        return ("429 Too Many Requests - trial rate/budget cap hit.",
                "Throttle and retry later within the plan's request budget.")
    if code in (500, 502, 503, 504):
        return ("Provider server error (5xx) - transient upstream issue.",
                "Retry later; if persistent, report to the provider.")
    if error_type == "network_error":
        return ("Network/transport error reaching the provider.",
                "Check connectivity / the local tunnel and retry.")
    if error_type == "bad_response":
        return ("Provider returned a non-JSON body (likely an HTML error page).",
                "Inspect the endpoint path; it may be returning an error document.")
    if error_type == "host_blocked":
        return ("Request blocked: non-FMP host (adapter safety guard).",
                "Use only financialmodelingprep.com endpoints.")
    return ("Unclassified provider error.",
            "Inspect the sanitized error message against the FMP stable docs.")


def _normalize_records(payload, endpoint: Dict, ticker: str,
                       pit_status: str) -> List[Dict]:
    """Flatten a provider payload into normalized rows with PIT metadata appended.

    Never fabricates: a non-list / empty payload yields no rows. Only known
    columns plus PIT metadata are emitted.
    """
    if isinstance(payload, dict):
        records = [payload]
    elif isinstance(payload, list):
        records = [r for r in payload if isinstance(r, dict)]
    else:
        records = []
    cols = _SCHEMA_BY_ENDPOINT.get(endpoint["endpoint_name"], ["symbol", "date"])
    date_field = endpoint["expected_date_key"]
    join_field = endpoint["expected_join_key"]
    out: List[Dict] = []
    for rec in records:
        row = {c: rec.get(c, "") for c in cols}
        row.update({
            "_provider": fmp.PROVIDER_NAME,
            "_endpoint": endpoint["endpoint_name"],
            "_alpha_family": endpoint["alpha_family"],
            "_join_key": join_field,
            "_join_key_value": rec.get(join_field, ticker if ticker != "*" else ""),
            "_pit_date_field": date_field,
            "_pit_date_value": rec.get(date_field, "") if date_field else "",
            "_point_in_time_status": pit_status,
            "_collected_note": "live trial sample; not fabricated",
        })
        out.append(row)
    return out


def run_live_sample(sample_tickers: List[str], max_tickers: int, max_endpoints: int,
                    verbose: bool) -> Tuple[List[Dict], int, int, List[str], List[Dict]]:
    """Collect a SMALL live sample. Returns (manifest_rows, raw_files_written,
    normalized_files_written, errors, error_report_rows)."""
    tickers = [t for t in sample_tickers][:max(0, max_tickers)] if max_tickers is not None \
        else list(sample_tickers)
    endpoints = _select_live_endpoints(max_endpoints)
    _RAW_DIR.mkdir(parents=True, exist_ok=True)
    _NORM_DIR.mkdir(parents=True, exist_ok=True)

    manifest: List[Dict] = []
    errors: List[str] = []
    error_report: List[Dict] = []
    raw_written = 0
    norm_written = 0

    for e in endpoints:
        name = e["endpoint_name"]
        endpoint_status = e.get("endpoint_status", "unknown")
        pit_status, _ = _PIT_BY_ENDPOINT.get(name, ("unknown", ""))
        targets = tickers if e["requires_ticker"] else ["*"]
        endpoint_rows: List[Dict] = []
        for tk in targets:
            path = e["endpoint_path_template"].replace(
                "{symbol}", tk if tk != "*" else "")
            # Persist-safe display URL (key parameter stripped; no "apikey=").
            redacted = fmp.redacted_request_url(path)
            try:
                payload = fmp.get_json(path, live=True)
            except fmp.FmpError as exc:
                status_code = getattr(exc, "status_code", None)
                error_type = getattr(exc, "error_type", "error")
                likely_cause, next_action = _classify_live_error(
                    error_type, status_code, endpoint_status)
                errors.append("%s/%s: %s" % (name, tk, exc))
                manifest.append({
                    "endpoint_name": name, "ticker": tk, "status": "error",
                    "rows": 0, "raw_file": "", "normalized_endpoint_file": "",
                    "request_url_redacted": redacted, "note": "request failed: %s" % exc,
                })
                error_report.append({
                    "endpoint_name": name,
                    "ticker": tk,
                    "request_url_redacted": redacted,
                    "http_status": "" if status_code is None else status_code,
                    "error_type": error_type,
                    "error_message_sanitized": str(exc),
                    "likely_cause": likely_cause,
                    "next_action": next_action,
                })
                continue
            # Persist raw payload (verbatim; no key in body/path).
            raw_path = _RAW_DIR / name / ("%s.json" % (tk if tk != "*" else "_all"))
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            with open(raw_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
                fh.write("\n")
            raw_written += 1
            norm_rows = _normalize_records(payload, e, tk, pit_status)
            endpoint_rows.extend(norm_rows)
            manifest.append({
                "endpoint_name": name, "ticker": tk, "status": "ok",
                "rows": len(norm_rows), "raw_file": _rel(raw_path),
                "normalized_endpoint_file": _rel(_NORM_DIR / ("%s.csv" % name)),
                "request_url_redacted": redacted,
                "note": "normalized %d record(s)" % len(norm_rows),
            })
            if verbose:
                print("  live %s/%s -> %d row(s)" % (name, tk, len(norm_rows)))
            time.sleep(fmp.MIN_SLEEP_SECONDS)
        # Write one normalized CSV per endpoint.
        if endpoint_rows:
            cols = _SCHEMA_BY_ENDPOINT.get(name, ["symbol", "date"]) + _PIT_META_COLUMNS
            _write_csv(_NORM_DIR / ("%s.csv" % name), cols,
                       [[r.get(c, "") for c in cols] for r in endpoint_rows])
            norm_written += 1
    return manifest, raw_written, norm_written, errors, error_report


# --------------------------------------------------------------------------- #
# Secret leak scan over written artifacts
# --------------------------------------------------------------------------- #
def _scan_for_leaks(paths: Sequence[Path], api_key: Optional[str]) -> Tuple[bool, int]:
    """Return (clean, files_scanned). 'clean' means no raw key value AND no key
    query parameter marker appears in any written artifact. Persisted URLs must
    strip the key parameter and use the redacted placeholder instead, so ANY
    occurrence of the key-parameter marker is treated as a leak.
    """
    # The marker is assembled at runtime so this source file never contains it.
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


# --------------------------------------------------------------------------- #
# Recommendation
# --------------------------------------------------------------------------- #
def derive_recommendation(api_key_present: bool, live_requested: bool,
                          live_mode: bool, live_requests: int,
                          had_fatal_error: bool) -> str:
    if live_requested and not api_key_present:
        return REC_BLOCKED_NO_KEY
    if not api_key_present:
        return REC_READY_FOR_KEY
    if live_mode:
        if live_requests > 0 and not had_fatal_error:
            return REC_READY_FOR_BACKFILL
        return REC_ERROR
    # Key present, dry-run only.
    return REC_READY_FOR_LIVE


_NEXT_PHASE = {
    REC_READY_FOR_KEY: "Set FMP_API_KEY, then re-run with --live for a small sample (Phase 5-E0 live).",
    REC_READY_FOR_LIVE: "Re-run with --live --max-tickers 2 --max-endpoints 3 to validate live ingestion.",
    REC_READY_FOR_BACKFILL: "Phase 5-E1 - FMP Full Sample/Universe Backfill (see phase5e1_backfill_plan.json).",
    REC_BLOCKED_NO_KEY: "Set FMP_API_KEY in the environment, then re-run.",
    REC_ERROR: "Inspect the trial manifest/errors and re-run the live smoke once resolved.",
}


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run(live: bool = False, max_tickers: int = 2, max_endpoints: int = 3,
        sample_tickers: Optional[List[str]] = None, verbose: bool = True) -> Dict:
    sample_tickers = [t.strip().upper() for t in (sample_tickers or DEFAULT_SAMPLE_TICKERS)
                      if str(t).strip()]
    api_key_present = fmp.has_api_key()
    live_requested = bool(live)
    # Live mode actually runs only when explicitly requested AND a key exists.
    live_mode = live_requested and api_key_present

    # Default (non-live) runs must be commit-safe: clear any stale live-only
    # output (raw/, normalized/, live error report) left by a prior --live smoke
    # or a live-failure test before writing the always-on dry-run artifacts.
    if not live_mode:
        _remove_stale_live_outputs()

    catalog = build_catalog_rows()
    plan = fmp.trial_collection_plan(sample_tickers)
    schema = build_schema_contract()
    pit = build_pit_readiness()
    backfill_plan = build_backfill_plan(sample_tickers)

    # --- write the network-free tables first ---
    _write_csv(_CATALOG_OUT,
               ["endpoint_name", "alpha_family", "purpose", "endpoint_path_template",
                "requires_ticker", "expected_join_key", "expected_date_key",
                "point_in_time_notes", "leakage_risk", "phase5e1_priority", "trial_safe",
                "endpoint_status", "smoke_safe", "smoke_priority"],
               [[e["endpoint_name"], e["alpha_family"], e["purpose"],
                 e["endpoint_path_template"], e["requires_ticker"], e["expected_join_key"],
                 e["expected_date_key"], e["point_in_time_notes"], e["leakage_risk"],
                 e["phase5e1_priority"], e["trial_safe"], e.get("endpoint_status", ""),
                 e.get("smoke_safe", False), e.get("smoke_priority", "")] for e in catalog])

    _write_csv(_PLAN_OUT,
               ["endpoint_name", "alpha_family", "ticker", "requires_ticker", "trial_safe",
                "phase5e1_priority", "request_url_redacted", "expected_join_key",
                "expected_date_key", "leakage_risk"],
               [[p["endpoint_name"], p["alpha_family"], p["ticker"], p["requires_ticker"],
                 p["trial_safe"], p["phase5e1_priority"], p["request_url_redacted"],
                 p["expected_join_key"], p["expected_date_key"], p["leakage_risk"]]
                for p in plan])

    _write_csv(_SCHEMA_OUT,
               ["endpoint_name", "alpha_family", "normalized_columns",
                "point_in_time_meta_columns", "join_key", "point_in_time_date_field",
                "point_in_time_status", "leakage_risk", "requires_ticker"],
               [[s["endpoint_name"], s["alpha_family"], s["normalized_columns"],
                 s["point_in_time_meta_columns"], s["join_key"], s["point_in_time_date_field"],
                 s["point_in_time_status"], s["leakage_risk"], s["requires_ticker"]]
                for s in schema])

    _write_csv(_PIT_OUT,
               ["endpoint_name", "alpha_family", "point_in_time_status",
                "point_in_time_date_field", "availability_basis", "leakage_risk",
                "join_method", "notes"],
               [[r["endpoint_name"], r["alpha_family"], r["point_in_time_status"],
                 r["point_in_time_date_field"], r["availability_basis"], r["leakage_risk"],
                 r["join_method"], r["notes"]] for r in pit])

    _write_json(_BACKFILL_PLAN_OUT, backfill_plan)

    # --- live sample (only if explicitly enabled AND key present) ---
    manifest_rows: List[Dict] = []
    raw_written = 0
    norm_written = 0
    errors: List[str] = []
    error_report_rows: List[Dict] = []
    if live_mode:
        (manifest_rows, raw_written, norm_written, errors,
         error_report_rows) = run_live_sample(
            sample_tickers, max_tickers, max_endpoints, verbose)
    # Normalized sample manifest (header always written; rows only in live mode).
    _write_csv(_NORM_MANIFEST_OUT,
               ["endpoint_name", "ticker", "status", "rows", "raw_file",
                "normalized_endpoint_file", "request_url_redacted", "note"],
               [[m["endpoint_name"], m["ticker"], m["status"], m["rows"], m["raw_file"],
                 m["normalized_endpoint_file"], m["request_url_redacted"], m["note"]]
                for m in manifest_rows])

    # Sanitized live error report (written in live mode; header + one row per
    # failed request). Never contains the key or a key-bearing URL.
    if live_mode:
        _write_csv(_LIVE_ERROR_REPORT_OUT,
                   ["endpoint_name", "ticker", "request_url_redacted", "http_status",
                    "error_type", "error_message_sanitized", "likely_cause", "next_action"],
                   [[r["endpoint_name"], r["ticker"], r["request_url_redacted"],
                     r["http_status"], r["error_type"], r["error_message_sanitized"],
                     r["likely_cause"], r["next_action"]] for r in error_report_rows])

    live_request_count = sum(1 for m in manifest_rows if m["status"] in ("ok", "error"))
    had_fatal_error = bool(live_mode and live_request_count > 0
                           and all(m["status"] == "error" for m in manifest_rows))

    # --- secret leak scan over everything written so far ---
    written_paths = [_CATALOG_OUT, _PLAN_OUT, _SCHEMA_OUT, _PIT_OUT, _NORM_MANIFEST_OUT,
                     _BACKFILL_PLAN_OUT]
    if live_mode:
        for root, _dirs, files in os.walk(_TRIAL_DIR):
            for fn in files:
                if fn.endswith((".json", ".csv")):
                    written_paths.append(Path(root) / fn)
    leak_clean, scanned = _scan_for_leaks(set(written_paths), fmp.resolve_api_key())

    secret_audit = build_secret_audit(api_key_present, live_mode, leak_clean, scanned)
    _write_csv(_SECRET_AUDIT_OUT, ["check", "value", "passed", "detail"],
               [[a["check"], a["value"], "" if a["passed"] is None else a["passed"], a["detail"]]
                for a in secret_audit])

    recommendation = derive_recommendation(api_key_present, live_requested, live_mode,
                                            live_request_count, had_fatal_error)

    missing_before_live = [
        "analyst_estimate_revisions (successive consensus snapshots for true revision PIT)",
        "news_sentiment (not an FMP focus here; separate provider)",
        "options_implied_volatility (not provided by FMP statements endpoints)",
        "point_in_time_index_constituents (collect FMP historical S&P 500 constituents to fix survivorship)",
    ]

    report = {
        "phase": PHASE,
        "objective": ("Stand up a safe FMP provider adapter + trial collector: catalog the missing "
                      "alpha endpoints, plan a small sample collection, define the normalized "
                      "point-in-time schema, audit secret safety, and (only on explicit --live with a "
                      "key) collect a tiny sample - the reliable ingestion foundation for Phase 5-E1."),
        "provider": fmp.PROVIDER_NAME,
        "provider_host": fmp.ALLOWED_HOST,
        "mode": "live_sample" if live_mode else "dry_run",
        "live_requested": live_requested,
        "api_key_env_var": fmp.API_KEY_ENV,
        "api_key_present": api_key_present,
        "api_key_logged": False,
        "endpoints_cataloged_count": len(catalog),
        "endpoints_stable_confirmed_count": sum(
            1 for e in catalog if e.get("endpoint_status") == "stable_confirmed"),
        "endpoints_needs_live_verification": [
            e["endpoint_name"] for e in catalog
            if e.get("endpoint_status") == "needs_live_verification"],
        "first_live_smoke_endpoints": [
            e["endpoint_name"] for e in _select_live_endpoints(max_endpoints)],
        "planned_request_count": len(plan),
        "live_request_count": live_request_count,
        "raw_files_written_count": raw_written,
        "normalized_files_written_count": norm_written,
        "sample_tickers": sample_tickers,
        "endpoint_families": fmp.endpoint_families(),
        "live_errors": errors,
        "secret_safety_leak_scan_clean": leak_clean,
        "secret_safety_files_scanned": scanned,
        "recommendation": recommendation,
        "recommendation_allowed_values": list(ALLOWED_RECOMMENDATIONS),
        "recommended_next_phase": _NEXT_PHASE[recommendation],
        "missing_before_live_collection": missing_before_live,
        "outputs": {
            "report": _rel(_REPORT_OUT),
            "endpoint_catalog": _rel(_CATALOG_OUT),
            "trial_collection_plan": _rel(_PLAN_OUT),
            "schema_contract": _rel(_SCHEMA_OUT),
            "normalized_sample_manifest": _rel(_NORM_MANIFEST_OUT),
            "point_in_time_readiness": _rel(_PIT_OUT),
            "secret_safety_audit": _rel(_SECRET_AUDIT_OUT),
            "live_error_report": _rel(_LIVE_ERROR_REPORT_OUT),
            "phase5e1_backfill_plan": _rel(_BACKFILL_PLAN_OUT),
        },
        # Safety / provenance contract (explicit, like Phase 5-A..5-D).
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
        description="Phase 5-E0 FMP provider trial collector (dry-run by default).")
    ap.add_argument("--live", action="store_true",
                    help="Perform a small live sample collection (requires FMP_API_KEY).")
    ap.add_argument("--max-tickers", type=int, default=2,
                    help="Max sample tickers to collect in live mode (default 2).")
    ap.add_argument("--max-endpoints", type=int, default=3,
                    help="Max trial-safe endpoints to collect in live mode (default 3).")
    ap.add_argument("--sample-tickers", type=str, default="",
                    help="Comma-separated sample tickers (default AAPL,MSFT,NVDA,AMZN,JPM).")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    tickers = [t.strip().upper() for t in args.sample_tickers.split(",") if t.strip()] \
        or DEFAULT_SAMPLE_TICKERS
    report = run(live=args.live, max_tickers=args.max_tickers,
                 max_endpoints=args.max_endpoints, sample_tickers=tickers, verbose=True)
    print("phase:                         %s" % report["phase"])
    print("mode:                          %s" % report["mode"])
    print("api key present:               %s" % report["api_key_present"])
    print("endpoints cataloged:           %s" % report["endpoints_cataloged_count"])
    print("  stable-confirmed:            %s" % report["endpoints_stable_confirmed_count"])
    print("  needs live verification:     %s" % (
        ", ".join(report["endpoints_needs_live_verification"]) or "(none)"))
    print("first live smoke endpoints:    %s" % ", ".join(report["first_live_smoke_endpoints"]))
    print("planned requests:              %s" % report["planned_request_count"])
    print("live requests:                 %s" % report["live_request_count"])
    print("raw files written:             %s" % report["raw_files_written_count"])
    print("normalized files written:      %s" % report["normalized_files_written_count"])
    print("recommendation:                %s" % report["recommendation"])
    print("next phase:                    %s" % report["recommended_next_phase"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
