"""Financial Modeling Prep (FMP) provider adapter - offline-first, stdlib only.

Phase 5-E0 foundation. After Phase 5-C/5-D showed the price-only model is not
deployment-grade and the strongest missing alpha families (analyst estimate
revisions, earnings actual-vs-estimate, ratios, price targets, recommendations)
are absent locally, this adapter is the reliable ingestion path for a paid
provider trial. FMP is the first provider tested because a small monthly trial
covers fundamentals, ratios/key-metrics, the earnings calendar, earnings
surprises, analyst estimates, recommendations, price targets, and (plan
permitting) S&P 500 constituents from a single REST API.

SECRET DISCIPLINE (hard rules):
  * The API key is read ONLY from the FMP_API_KEY environment variable.
  * It is NEVER printed, NEVER written to any artifact, NEVER committed, and
    NEVER hard-coded. Persisted URLs strip the key parameter entirely and show
    a placeholder instead, so no artifact ever contains the substring "apikey=".
  * Importing or constructing this module performs NO network call. A live call
    happens only when a caller passes ``live=True`` and the key is present.
  * Unit tests must run fully in dry-run mode without a key and without network.

Stdlib only (urllib/json/csv/os/pathlib/datetime/time) - no third-party deps.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple, Union

# --------------------------------------------------------------------------- #
# Provider policy
# --------------------------------------------------------------------------- #
PROVIDER_NAME = "Financial Modeling Prep"
API_KEY_ENV = "FMP_API_KEY"
BASE_URL = "https://financialmodelingprep.com"
ALLOWED_HOST = "financialmodelingprep.com"
REDACTED = "REDACTED"
# Persist-safe placeholder for the key-bearing query parameter. Persisted /
# committed artifacts must NEVER contain the literal substring "apikey="; the
# key parameter is stripped entirely and replaced by this token instead.
REDACTED_KEY_PLACEHOLDER = "<API_KEY_REDACTED>"
_USER_AGENT = "paper-trader-research-phase5e0/1.0"
_APIKEY_PARAM = "apikey"

# Conservative trial-friendly request settings (a paid trial still has a budget).
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_BACKOFF_SECONDS = 1.5
# Minimum spacing between live requests (politeness; not a free-tier hard cap).
MIN_SLEEP_SECONDS = 0.30


# --------------------------------------------------------------------------- #
# Key handling (env only; never printed / persisted)
# --------------------------------------------------------------------------- #
def resolve_api_key() -> Optional[str]:
    """Return the FMP key from the environment only, or None. Never logged."""
    key = os.environ.get(API_KEY_ENV)
    return key.strip() if isinstance(key, str) and key.strip() else None


def has_api_key() -> bool:
    """True iff FMP_API_KEY is present and non-empty in the environment."""
    return resolve_api_key() is not None


# --------------------------------------------------------------------------- #
# URL building + redaction
# --------------------------------------------------------------------------- #
def sanitize_url(url: str) -> str:
    """Return ``url`` with any apikey query-parameter value replaced by REDACTED.

    This is the ONLY URL form that may be persisted, logged, or returned to a
    caller for storage. It never reveals the key.
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except (ValueError, AttributeError):
        return str(url)
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    redacted = [(k, REDACTED if k.lower() == _APIKEY_PARAM else v) for k, v in pairs]
    # If there was an apikey marker but parse dropped it (edge cases), force one.
    if _APIKEY_PARAM in parts.query.lower() and not any(
            k.lower() == _APIKEY_PARAM for k, _ in redacted):
        redacted.append((_APIKEY_PARAM, REDACTED))
    new_query = urllib.parse.urlencode(redacted)
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


def redacted_request_url(endpoint: str, params: Optional[Dict] = None) -> str:
    """Return a PERSIST-SAFE display URL for ``endpoint`` (+ ``params``).

    The key-bearing ``apikey`` query parameter is stripped ENTIRELY and replaced
    by the ``REDACTED_KEY_PLACEHOLDER`` token, so the result never contains the
    literal substring ``apikey=`` and is safe to write to committed / generated
    non-raw artifacts. Non-secret query parameters (e.g. ``period=quarter``) are
    preserved so the artifact still documents the real request shape.

    Example::

        https://financialmodelingprep.com/stable/income-statement?symbol=AAPL&period=quarter&<API_KEY_REDACTED>
    """
    url = build_fmp_url(endpoint, params)
    try:
        parts = urllib.parse.urlsplit(url)
    except (ValueError, AttributeError):
        return str(url)
    pairs = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() != _APIKEY_PARAM]
    query = urllib.parse.urlencode(pairs)
    # Append the placeholder literally (urlencode would percent-escape the <>).
    query = (query + "&" + REDACTED_KEY_PLACEHOLDER) if query else REDACTED_KEY_PLACEHOLDER
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _split_endpoint(endpoint: str) -> Tuple[str, List[Tuple[str, str]]]:
    """Split an endpoint path (optionally carrying its own query) into
    (path, [(query_key, query_value), ...])."""
    endpoint = endpoint or ""
    if "?" in endpoint:
        path, _, raw_q = endpoint.partition("?")
    else:
        path, raw_q = endpoint, ""
    pairs = urllib.parse.parse_qsl(raw_q, keep_blank_values=True)
    if not path.startswith("/"):
        path = "/" + path
    return path, pairs


def build_fmp_url(endpoint: str, params: Optional[Dict] = None) -> str:
    """Build a full FMP request URL for ``endpoint`` with ``params`` merged in
    and the apikey appended from the environment.

    The returned URL CARRIES THE KEY and is for transient live use only - it must
    NEVER be persisted. Use ``sanitize_url()`` (or ``request_description()``)
    before logging or writing anything. If no key is set, an empty apikey value
    is used so the structure is still inspectable in dry-run.
    """
    path, endpoint_pairs = _split_endpoint(endpoint)
    pairs: List[Tuple[str, str]] = list(endpoint_pairs)
    for k, v in (params or {}).items():
        pairs.append((str(k), "" if v is None else str(v)))
    key = resolve_api_key() or ""
    pairs.append((_APIKEY_PARAM, key))
    query = urllib.parse.urlencode(pairs)
    return "%s%s?%s" % (BASE_URL, path, query)


def request_description(endpoint: str, params: Optional[Dict] = None) -> Dict:
    """Return sanitized, network-free request metadata for ``endpoint``.

    Safe to persist: the ``sanitized_url`` always has apikey=REDACTED and the
    raw key never appears.
    """
    sanitized = sanitize_url(build_fmp_url(endpoint, params))
    path, _ = _split_endpoint(endpoint)
    return {
        "provider": PROVIDER_NAME,
        "host": ALLOWED_HOST,
        "endpoint_path": path,
        "params": dict(params or {}),
        "sanitized_url": sanitized,
        "api_key_present": has_api_key(),
        "api_key_logged": False,
    }


def _host_allowed(url: str) -> bool:
    try:
        return urllib.parse.urlsplit(url).netloc == ALLOWED_HOST
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# Live fetch (only when explicitly requested AND a key exists)
# --------------------------------------------------------------------------- #
class FmpError(Exception):
    """A live FMP request failed (after retries) or the response was unusable.

    Carries an optional sanitized HTTP ``status_code`` (when the provider
    returned one) and an ``error_type`` tag so the collector can write a
    structured, key-free live error report. The message itself is constructed
    locally and never contains the API key.
    """

    def __init__(self, message: str, *, status_code: Optional[int] = None,
                 error_type: str = "error") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type


def _http_get_json(url: str, timeout: float, max_retries: int,
                   backoff: float) -> Union[dict, list]:
    """Perform a single live GET with simple retry/backoff on transient errors.

    The raw key-bearing ``url`` is used transiently and never persisted. Errors
    raised here are sanitized so the key cannot leak through an exception string.
    """
    if not _host_allowed(url):
        raise FmpError("refusing non-FMP host", error_type="host_blocked")
    last_err = ""
    attempts = max(1, int(max_retries) + 1)
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - host allow-listed
                raw = resp.read()
            text = raw.decode("utf-8", "replace")
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                raise FmpError("non-JSON response from provider: %s" % exc,
                               error_type="bad_response")
        except urllib.error.HTTPError as exc:
            # 4xx (except 429) are not retried; 429/5xx are transient.
            code = getattr(exc, "code", 0)
            last_err = "HTTP %s" % code
            if code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise FmpError("provider returned %s" % last_err,
                               status_code=code, error_type="http_error")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_err = type(exc).__name__
            if attempt == attempts - 1:
                raise FmpError("network error after %d attempt(s): %s" % (attempts, last_err),
                               error_type="network_error")
        time.sleep(max(0.0, backoff) * (attempt + 1))
    raise FmpError("exhausted retries: %s" % last_err, error_type="network_error")


def get_json(endpoint: str, params: Optional[Dict] = None, *, live: bool = False,
             timeout: float = DEFAULT_TIMEOUT_SECONDS,
             max_retries: int = DEFAULT_MAX_RETRIES,
             backoff: float = DEFAULT_BACKOFF_SECONDS) -> Union[dict, list]:
    """Fetch JSON from an FMP endpoint.

    Dry-run (default, ``live=False``): performs NO network call and returns
    request metadata (sanitized URL + params + key-presence). Live
    (``live=True``): requires FMP_API_KEY; performs one GET with retry/backoff
    and returns the parsed JSON payload (list or dict).
    """
    if not live:
        meta = request_description(endpoint, params)
        meta["dry_run"] = True
        meta["note"] = "dry-run: no network call performed"
        return meta
    if not has_api_key():
        raise FmpError("live request requested but %s is not set" % API_KEY_ENV)
    url = build_fmp_url(endpoint, params)
    return _http_get_json(url, timeout=timeout, max_retries=max_retries, backoff=backoff)


# --------------------------------------------------------------------------- #
# Endpoint catalog (configurable path templates; do not assume every plan
# supports every endpoint)
# --------------------------------------------------------------------------- #
# alpha_family vocabulary aligns with Phase 5-D families.
#
# Endpoint paths use the CURRENT FMP "stable" API (https://financialmodelingprep.com/stable/...)
# with the symbol passed as a query parameter (?symbol=...). The legacy /api/v3 and
# /api/v4 paths return HTTP 403 for keys provisioned on the current stable plan
# (this was the Phase 5-E0 live-sample failure).
#
# ``endpoint_status`` is one of:
#   * "stable_confirmed"        - the stable path/shape is known and stable; safe to use.
#   * "needs_live_verification" - the stable equivalent name is NOT certain; a best-effort
#                                 path is provided but it is EXCLUDED from the first live
#                                 smoke and must be confirmed against live responses (or FMP
#                                 docs) before Phase 5-E1 relies on it. (We do not guess
#                                 silently - the uncertainty is recorded here and in the CSV.)
# ``smoke_safe`` / ``smoke_priority`` control the first --live smoke set (safest endpoints
# only: profile auth-canary, income statement, then ratios/key-metrics).
_CATALOG: Tuple[Dict, ...] = (
    {
        "endpoint_name": "company_profile",
        "alpha_family": "universe_mapping",
        "purpose": "Sector/industry/identity + listing metadata for ticker->sector mapping.",
        "endpoint_path_template": "/stable/profile?symbol={symbol}",
        "requires_ticker": True,
        "expected_join_key": "symbol",
        "expected_date_key": "",
        "point_in_time_notes": "Current-as-of snapshot (no history) - NOT point-in-time membership; "
                               "use only for caveated static sector mapping.",
        "leakage_risk": "high",
        "phase5e1_priority": 4,
        "trial_safe": True,
        "endpoint_status": "stable_confirmed",
        "smoke_safe": True,
        "smoke_priority": 1,
    },
    {
        "endpoint_name": "income_statement_quarterly",
        "alpha_family": "fundamentals",
        "purpose": "Quarterly income statement (revenue, margins, EPS) for value/quality/growth.",
        "endpoint_path_template": "/stable/income-statement?symbol={symbol}&period=quarter",
        "requires_ticker": True,
        "expected_join_key": "symbol",
        "expected_date_key": "fillingDate",
        "point_in_time_notes": "Use fillingDate/acceptedDate (filing availability), NEVER the fiscal "
                               "period 'date'; as-of join latest filing on/before scoring date.",
        "leakage_risk": "low",
        "phase5e1_priority": 1,
        "trial_safe": True,
        "endpoint_status": "stable_confirmed",
        "smoke_safe": True,
        "smoke_priority": 2,
    },
    {
        "endpoint_name": "balance_sheet_statement_quarterly",
        "alpha_family": "fundamentals",
        "purpose": "Quarterly balance sheet (assets, debt, equity) for leverage/quality.",
        "endpoint_path_template": "/stable/balance-sheet-statement?symbol={symbol}&period=quarter",
        "requires_ticker": True,
        "expected_join_key": "symbol",
        "expected_date_key": "fillingDate",
        "point_in_time_notes": "Use fillingDate/acceptedDate (filing availability), never fiscal "
                               "period 'date'; as-of join.",
        "leakage_risk": "low",
        "phase5e1_priority": 1,
        "trial_safe": True,
        "endpoint_status": "stable_confirmed",
        "smoke_safe": False,
        "smoke_priority": 5,
    },
    {
        "endpoint_name": "cash_flow_statement_quarterly",
        "alpha_family": "fundamentals",
        "purpose": "Quarterly cash-flow (FCF, capex, accruals proxy) for quality.",
        "endpoint_path_template": "/stable/cash-flow-statement?symbol={symbol}&period=quarter",
        "requires_ticker": True,
        "expected_join_key": "symbol",
        "expected_date_key": "fillingDate",
        "point_in_time_notes": "Use fillingDate/acceptedDate; never fiscal period 'date'; as-of join.",
        "leakage_risk": "low",
        "phase5e1_priority": 2,
        "trial_safe": True,
        "endpoint_status": "stable_confirmed",
        "smoke_safe": False,
        "smoke_priority": 6,
    },
    {
        "endpoint_name": "key_metrics_quarterly",
        "alpha_family": "fundamentals",
        "purpose": "Derived per-share/valuation metrics (PE, PB, ROIC, FCF yield).",
        "endpoint_path_template": "/stable/key-metrics?symbol={symbol}&period=quarter",
        "requires_ticker": True,
        "expected_join_key": "symbol",
        "expected_date_key": "date",
        "point_in_time_notes": "Period 'date' only (no filing date on this endpoint) - must be lagged "
                               "to the corresponding statement filing availability before joining.",
        "leakage_risk": "medium",
        "phase5e1_priority": 2,
        "trial_safe": True,
        "endpoint_status": "stable_confirmed",
        "smoke_safe": True,
        "smoke_priority": 4,
    },
    {
        "endpoint_name": "ratios_quarterly",
        "alpha_family": "fundamentals",
        "purpose": "Quarterly financial ratios (margins, turnover, liquidity, leverage).",
        "endpoint_path_template": "/stable/ratios?symbol={symbol}&period=quarter",
        "requires_ticker": True,
        "expected_join_key": "symbol",
        "expected_date_key": "date",
        "point_in_time_notes": "Period 'date' only - lag to the statement filing availability before "
                               "joining (same caveat as key_metrics).",
        "leakage_risk": "medium",
        "phase5e1_priority": 2,
        "trial_safe": True,
        "endpoint_status": "stable_confirmed",
        "smoke_safe": True,
        "smoke_priority": 3,
    },
    {
        "endpoint_name": "earnings_calendar",
        "alpha_family": "earnings",
        "purpose": "Per-ticker historical earnings dates + estimate/actual EPS & revenue.",
        "endpoint_path_template": "/stable/earnings?symbol={symbol}",
        "requires_ticker": True,
        "expected_join_key": "symbol",
        "expected_date_key": "date",
        "point_in_time_notes": "Announcement 'date' is the availability date; as-of join the latest "
                               "announced event on/before the scoring date. STABLE ENDPOINT NAME "
                               "(/stable/earnings) not yet confirmed live on this plan.",
        "leakage_risk": "low",
        "phase5e1_priority": 3,
        "trial_safe": True,
        "endpoint_status": "needs_live_verification",
        "smoke_safe": False,
        "smoke_priority": 90,
    },
    {
        "endpoint_name": "earnings_surprises",
        "alpha_family": "earnings",
        "purpose": "Actual vs estimate EPS surprises per reporting date.",
        "endpoint_path_template": "/stable/earnings?symbol={symbol}",
        "requires_ticker": True,
        "expected_join_key": "symbol",
        "expected_date_key": "date",
        "point_in_time_notes": "Reporting 'date' is availability; as-of join; flag restatements. In "
                               "the stable API surprises appear to be folded into /stable/earnings "
                               "(actual vs estimated EPS) - endpoint shape NOT yet confirmed live.",
        "leakage_risk": "low",
        "phase5e1_priority": 3,
        "trial_safe": True,
        "endpoint_status": "needs_live_verification",
        "smoke_safe": False,
        "smoke_priority": 91,
    },
    {
        "endpoint_name": "analyst_estimates",
        "alpha_family": "analyst_revisions",
        "purpose": "Forward consensus estimates (revenue/EPS) - basis for revision breadth.",
        "endpoint_path_template": "/stable/analyst-estimates?symbol={symbol}&period=annual",
        "requires_ticker": True,
        "expected_join_key": "symbol",
        "expected_date_key": "date",
        "point_in_time_notes": "Snapshot consensus per 'date'; true revision PIT needs successive "
                               "snapshots (capture publication date; do not back-date a current "
                               "consensus onto past scoring dates). STABLE NAME/period not yet "
                               "confirmed live.",
        "leakage_risk": "medium",
        "phase5e1_priority": 5,
        "trial_safe": True,
        "endpoint_status": "needs_live_verification",
        "smoke_safe": False,
        "smoke_priority": 92,
    },
    {
        "endpoint_name": "analyst_recommendations",
        "alpha_family": "analyst_revisions",
        "purpose": "Analyst buy/hold/sell consensus grades over time.",
        "endpoint_path_template": "/stable/grades?symbol={symbol}",
        "requires_ticker": True,
        "expected_join_key": "symbol",
        "expected_date_key": "date",
        "point_in_time_notes": "Each grade carries its 'date'; as-of join the latest grade on/before "
                               "the scoring date. Stable equivalent (/stable/grades or "
                               "/stable/grades-consensus) NOT yet confirmed live.",
        "leakage_risk": "low",
        "phase5e1_priority": 5,
        "trial_safe": True,
        "endpoint_status": "needs_live_verification",
        "smoke_safe": False,
        "smoke_priority": 93,
    },
    {
        "endpoint_name": "analyst_price_targets",
        "alpha_family": "analyst_revisions",
        "purpose": "Analyst price-target changes (target vs price; revision direction).",
        "endpoint_path_template": "/stable/price-target-summary?symbol={symbol}",
        "requires_ticker": True,
        "expected_join_key": "symbol",
        "expected_date_key": "publishedDate",
        "point_in_time_notes": "publishedDate is the availability timestamp; as-of join; never "
                               "back-date a later target onto an earlier scoring date. Stable "
                               "equivalent (/stable/price-target-summary or -news) NOT yet "
                               "confirmed live.",
        "leakage_risk": "low",
        "phase5e1_priority": 5,
        "trial_safe": True,
        "endpoint_status": "needs_live_verification",
        "smoke_safe": False,
        "smoke_priority": 94,
    },
    {
        "endpoint_name": "sp500_constituents",
        "alpha_family": "universe_mapping",
        "purpose": "Current S&P 500 membership (and, via historical endpoint, add/drop history).",
        "endpoint_path_template": "/stable/sp500-constituent",
        "requires_ticker": False,
        "expected_join_key": "symbol",
        "expected_date_key": "dateFirstAdded",
        "point_in_time_notes": "Current list is NOT point-in-time; the companion historical "
                               "constituents endpoint (add/drop dates) is what removes survivorship - "
                               "collect that to build PIT membership.",
        "leakage_risk": "high",
        "phase5e1_priority": 4,
        "trial_safe": True,
        "endpoint_status": "stable_confirmed",
        "smoke_safe": False,
        "smoke_priority": 7,
    },
)


def endpoint_catalog() -> List[Dict]:
    """Return the FMP endpoint catalog (a fresh list of plain dict copies)."""
    return [dict(e) for e in _CATALOG]


def endpoint_families() -> List[str]:
    """Distinct alpha families covered by the catalog, in first-seen order."""
    seen: List[str] = []
    for e in _CATALOG:
        if e["alpha_family"] not in seen:
            seen.append(e["alpha_family"])
    return seen


def trial_collection_plan(sample_tickers: List[str]) -> List[Dict]:
    """Build a per-(endpoint, ticker) trial collection plan for ``sample_tickers``.

    No network: this only enumerates the small set of requests a live trial WOULD
    make, each with a persist-safe redacted URL (key parameter stripped, never
    the literal ``apikey=``). Ticker-less endpoints (e.g. sp500 constituents)
    appear once with ticker "*".
    """
    tickers = [str(t).strip().upper() for t in (sample_tickers or []) if str(t).strip()]
    plan: List[Dict] = []
    for e in _CATALOG:
        targets = tickers if e["requires_ticker"] else ["*"]
        for tk in targets:
            path = e["endpoint_path_template"].replace("{symbol}", tk if tk != "*" else "")
            plan.append({
                "endpoint_name": e["endpoint_name"],
                "alpha_family": e["alpha_family"],
                "ticker": tk,
                "requires_ticker": e["requires_ticker"],
                "trial_safe": e["trial_safe"],
                "phase5e1_priority": e["phase5e1_priority"],
                # Persist-safe: key parameter stripped, placeholder only (no "apikey=").
                "request_url_redacted": redacted_request_url(path),
                "expected_join_key": e["expected_join_key"],
                "expected_date_key": e["expected_date_key"],
                "leakage_risk": e["leakage_risk"],
            })
    return plan


def format_endpoint_path(template: str, symbol: str) -> str:
    """Substitute {symbol} in a catalog path template with a concrete ticker."""
    return template.replace("{symbol}", urllib.parse.quote(str(symbol)))
