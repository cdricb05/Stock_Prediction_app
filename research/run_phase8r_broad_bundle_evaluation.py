"""Phase 8-R - Broad Earnings/Fundamentals Bundle Evaluation (EODHD test-first).

WHY THIS PHASE EXISTS
    Phase 8-Q (the market-data foundation decision gate) concluded MIXED_FREE_PLUS_PAID_CORE_STACK:
    keep FREE where it genuinely works (FRED macro, the OHLCV cache, ticker/sector identity), BUY
    one cheap broad earnings/fundamentals provider for the core signal, and DEFER expensive
    alt-data. The grounded evidence: 8-N proved the current FMP plan is INSUFFICIENT (every critical
    family PARTIAL at ~8/20, systematic 402); 8-P proved Alpha Vantage free HELPS but its 25 req/day
    throughput cannot cover 100-500 tickers in reasonable time. The test-first paid provider is
    EODHD (~$20-$80/mo, single bundle: earnings + fundamentals + analyst, bulk download, PIT dates);
    FMP Premium is the FALLBACK only if EODHD fails; FMP Ultimate is REJECTED.

    Phase 8-R executes exactly that evaluation. Using the user's EODHD_API_KEY (if present), it runs
    a CONTROLLED, BOUNDED live probe of the EODHD fundamentals + earnings-calendar endpoints for the
    FMP-blocked tickers first and the current research universe, classifies coverage / history depth
    / point-in-time safety per data family, scores EODHD against the vendor criteria, compares it to
    the FMP and Alpha Vantage evidence, and decides: ACCEPT EODHD as the core provider, keep it as
    PROMISING (needs a larger trial), or REJECT it (recommend the FMP Premium fallback). This is NOT
    a full S&P 500 backfill - it is a bounded coverage/PIT/cost evaluation to de-risk the purchase.

    If EODHD_API_KEY is absent (the current state), the phase still produces every committed-safe
    planning artifact and emits BLOCKED_MISSING_EODHD_KEY with the exact next step to set the key.

SECRET DISCIPLINE (hard rules)
    * EODHD_API_KEY is read ONLY from the environment, NEVER printed, NEVER written to disk. The
      key-detection report records PRESENCE ONLY (value_read=False).
    * Every persisted URL strips the secret query parameter (api_token/apikey) ENTIRELY and appends
      a placeholder, so no committed artifact contains "api_token=" / "apikey=" or a key value. A
      leak scan over the written committed artifacts confirms secret_safety_leak_scan_clean.
    * Default run is OFFLINE (no network). A live evaluation needs BOTH --live AND the key. Tests
      inject a ``transport`` and never touch a key or the network.
    * Raw + normalized EODHD payloads live ONLY under the gitignored
      research/data/eodhd/{raw,normalized}/ trees, which this phase force-gitignores (belt-and-
      braces ``*`` + ``!.gitignore``) BEFORE writing, so paid data can never be staged or committed.

HARD CONSTRAINTS HONORED
    Existing installed packages only (stdlib). No package install. No full S&P 500 backfill
    (bounded: max_tickers / max_requests, skip_existing, stop on invalid key / rate limit). No
    raw/normalized provider data committed. No Paper Trader. No GCP. No deploy. No broker / order /
    automation logic. No FMP Ultimate evaluation. No commit. No push.

Run (default = OFFLINE: build the plan + decision, no network):
    python research/run_phase8r_broad_bundle_evaluation.py
Bounded live evaluation (requires EODHD_API_KEY in the env):
    $env:EODHD_API_KEY = '<PASTE_EODHD_API_KEY_HERE>'
    python research/run_phase8r_broad_bundle_evaluation.py --live
Test (fully offline):
    python -m pytest tests/test_phase8r_broad_bundle_evaluation.py -q
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

PHASE = "8-R"
PROVIDER_NAME = "EODHD"
API_KEY_ENV = "EODHD_API_KEY"
ALLOWED_HOSTS = ("eodhd.com", "eodhistoricaldata.com")

# --------------------------------------------------------------------------- #
# Paths (read-only inputs + committed-safe output dir + gitignored data tree).
# --------------------------------------------------------------------------- #
_OUT_DIR = (_REPO_ROOT / "research" / "output"
            / "phase8r_broad_bundle_evaluation")
_PHASE8Q_DIR = (_REPO_ROOT / "research" / "output"
                / "phase8q_market_data_foundation_decision")
_PHASE8P_DIR = (_REPO_ROOT / "research" / "output"
                / "phase8p_alphavantage_earnings_expansion")
_PHASE8N_DIR = (_REPO_ROOT / "research" / "output"
                / "phase8n_fmp_critical_data_backfill_signal_expansion")
_DATA_ROOT = _REPO_ROOT / "research" / "data"
_EODHD_SLUG = "eodhd"
_FMP_COVERAGE_CSV = "fmp_ticker_coverage_by_family.csv"

# The 13 committed-safe artifact basenames.
_ARTIFACTS = {
    "report": "phase8r_broad_bundle_evaluation.json",
    "key_detection": "eodhd_key_detection_report.csv",
    "request_plan": "broad_bundle_request_plan.csv",
    "probe_results": "broad_bundle_probe_results.csv",
    "coverage_by_family": "eodhd_coverage_by_family.csv",
    "comparison": "eodhd_vs_fmp_av_coverage_comparison.csv",
    "pit_readiness": "eodhd_point_in_time_readiness.csv",
    "vendor_scorecard": "vendor_scorecard.csv",
    "cost_value_decision": "provider_cost_value_decision.csv",
    "fmp_fallback_plan": "fmp_premium_fallback_plan.csv",
    "procurement_questions": "broad_bundle_procurement_questions.csv",
    "next_plan": "phase8s_next_plan.json",
    "secret_audit": "secret_safety_audit.csv",
}


class _Paths:
    def __init__(self, out_dir: Optional[Path] = None, data_dir: Optional[Path] = None,
                 phase8q_dir: Optional[Path] = None, phase8p_dir: Optional[Path] = None,
                 phase8n_dir: Optional[Path] = None):
        self.out = Path(out_dir) if out_dir else _OUT_DIR
        self.data = Path(data_dir) if data_dir else _DATA_ROOT
        self.phase8q = Path(phase8q_dir) if phase8q_dir else _PHASE8Q_DIR
        self.phase8p = Path(phase8p_dir) if phase8p_dir else _PHASE8P_DIR
        self.phase8n = Path(phase8n_dir) if phase8n_dir else _PHASE8N_DIR
        for key, name in _ARTIFACTS.items():
            setattr(self, key, self.out / name)

    @property
    def eodhd_dir(self) -> Path:
        return self.data / _EODHD_SLUG

    @property
    def raw_dir(self) -> Path:
        return self.eodhd_dir / "raw"

    @property
    def normalized_csv(self) -> Path:
        return self.eodhd_dir / "normalized" / "earnings.csv"


# --------------------------------------------------------------------------- #
# Bounded-run defaults (from the brief). NOT a full S&P 500 backfill.
# --------------------------------------------------------------------------- #
DEFAULT_MAX_TICKERS = 50
DEFAULT_MAX_REQUESTS = 100
STOP_AFTER_CONSECUTIVE_RATE_LIMITS = 2
STOP_AFTER_CONSECUTIVE_INVALID_KEY = 1   # an invalid key is fatal immediately
STOP_AFTER_SYSTEMATIC_PLAN_BLOCKS = 3    # 3 consecutive 402s => systematic plan-block

MIN_SCORING_GATE = 20
BROAD_UNIVERSE_TARGET = 500              # the S&P 500-like near-term target

# Decision thresholds: fraction of attempted tickers with a backtest-usable core earnings panel.
ACCEPT_FRACTION = 0.80
PROMISING_FRACTION = 0.40
# History sufficiency (point-in-time backtests).
MIN_QUARTERS_FOR_DEPTH = 24             # ~6 years of quarterly earnings
MIN_YEARS_FOR_DEPTH = 8

# Deterministic fallback universe (same large-cap set used by 8-P). NOT an S&P 500 backfill.
FMP_BLOCKED_FALLBACK = ("ABT", "ACN", "ADI", "ADP", "AMAT", "AMGN", "AON", "APD", "APH")
FMP_COLLECTED_FALLBACK = ("AAPL", "MSFT", "NVDA", "ABBV", "ADBE", "AMD", "AMZN", "JPM")
PHASE5C_FALLBACK = ("GOOGL", "META", "TSLA", "AVGO", "COST", "PEP", "KO", "CSCO",
                    "INTC", "QCOM", "TXN", "ORCL", "CRM", "NFLX", "MU")

# --------------------------------------------------------------------------- #
# EODHD endpoints + data families.
#   One bounded fundamentals request yields FOUR families (earnings history, fundamentals
#   statements, analyst estimates, analyst ratings); the earnings-calendar endpoint is a second
#   request. So each ticker costs at most 2 requests - bounded and bulk-friendly.
# --------------------------------------------------------------------------- #
EP_FUNDAMENTALS = "fundamentals"
EP_EARNINGS_CALENDAR = "earnings_calendar"
_ENDPOINTS = (EP_FUNDAMENTALS, EP_EARNINGS_CALENDAR)

FAM_EARNINGS = "earnings_history"            # actual / estimate / surprise / report date
FAM_EARN_CAL = "earnings_calendar"
FAM_FUNDAMENTALS = "fundamentals_statements"
FAM_ANALYST_EST = "analyst_estimates"
FAM_ANALYST_RATINGS = "analyst_ratings"      # price targets / recommendations
_FAMILIES = (FAM_EARNINGS, FAM_EARN_CAL, FAM_FUNDAMENTALS, FAM_ANALYST_EST, FAM_ANALYST_RATINGS)
# The CORE family that decides whether EODHD can be the backbone.
_CORE_FAMILY = FAM_EARNINGS

# Endpoint URL templates (the live key is appended transiently, never persisted).
_FUNDAMENTALS_URL = "https://eodhd.com/api/fundamentals/{symbol}.US?fmt=json"
_EARN_CAL_URL = "https://eodhd.com/api/calendar/earnings?fmt=json&symbols={symbol}.US"
_USER_AGENT = "paper-trader-research-phase8r/1.0"
PROBE_TIMEOUT_SECONDS = 30.0
PROBE_MIN_SLEEP_SECONDS = 0.30

# --------------------------------------------------------------------------- #
# Decision vocabulary (exactly the values from the brief).
# --------------------------------------------------------------------------- #
DEC_ACCEPT = "EODHD_ACCEPT_AS_CORE_PROVIDER"
DEC_PROMISING = "EODHD_PROMISING_NEEDS_LARGER_TRIAL"
DEC_REJECTED = "EODHD_REJECTED_INSUFFICIENT_COVERAGE"
DEC_BLOCKED_NO_KEY = "BLOCKED_MISSING_EODHD_KEY"
DEC_FMP_FALLBACK = "FMP_PREMIUM_FALLBACK_RECOMMENDED"
DEC_NEEDS_QUOTES = "NEEDS_VENDOR_QUOTES"
DEC_ERROR = "ERROR"
ALLOWED_DECISIONS = (
    DEC_ACCEPT, DEC_PROMISING, DEC_REJECTED, DEC_BLOCKED_NO_KEY, DEC_FMP_FALLBACK,
    DEC_NEEDS_QUOTES, DEC_ERROR,
)

# Secret query-parameter names stripped from every persisted URL.
_SECRET_PARAMS = {"api_token", "apikey", "token", "api-token"}
REDACTED_KEY_PLACEHOLDER = "<API_KEY_REDACTED>"


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


def key_present(env_var: str = API_KEY_ENV) -> bool:
    """True iff ``env_var`` is set and non-empty. The value is never returned/printed/written."""
    v = os.environ.get(env_var)
    return isinstance(v, str) and bool(v.strip())


# --------------------------------------------------------------------------- #
# URL redaction (strips every secret query parameter ENTIRELY).
# --------------------------------------------------------------------------- #
def redact_url(url: str) -> str:
    """Return a persist-safe URL: every secret query parameter (api_token/apikey/...) is stripped
    ENTIRELY and a placeholder is appended, so the result never contains a key or "api_token=" /
    "apikey=" markers."""
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


def _plan_url(symbol: str, endpoint: str) -> str:
    base = (_FUNDAMENTALS_URL if endpoint == EP_FUNDAMENTALS else _EARN_CAL_URL)
    return redact_url(base.replace("{symbol}", symbol) + "&api_token=")


# --------------------------------------------------------------------------- #
# Phase 8-N FMP coverage ingestion (read-only; which tickers FMP covered vs blocked).
# --------------------------------------------------------------------------- #
def read_fmp_earnings_coverage(phase8n_dir: Path) -> Dict:
    """Read the Phase 8-N per-ticker coverage CSV and return the FMP earnings coverage split.
    Falls back to the deterministic Phase 8-N lists if the file is unavailable."""
    rows = _read_csv_file(phase8n_dir / _FMP_COVERAGE_CSV)
    covered: List[str] = []
    blocked: List[str] = []
    if rows:
        for r in rows:
            fam = (r.get("endpoint_family") or "").strip()
            if fam not in ("earnings_surprises", "earnings_calendar"):
                continue
            tk = (r.get("ticker") or "").strip().upper()
            if not tk:
                continue
            has = (r.get("has_data") or "").strip().lower() == "yes"
            if has and tk not in covered:
                covered.append(tk)
            elif not has and tk not in blocked:
                blocked.append(tk)
        blocked = [t for t in blocked if t not in covered]
        found = True
    else:
        covered = list(FMP_COLLECTED_FALLBACK)
        blocked = list(FMP_BLOCKED_FALLBACK)
        found = False
    return {"found": found, "fmp_covered": covered, "fmp_blocked": blocked}


def extract_current_universe(out_root: Path) -> List[str]:
    """Best-effort scan of the Phase 5C / 8K / 8L research-universe artifacts for tickers to
    prioritize alongside the FMP-blocked set. Never fatal: returns [] if nothing is extractable."""
    candidates: List[str] = []
    probe_files = [
        out_root / "phase8l_data_family_expansion_signal_factory"
        / "best_trade_idea_candidates.csv",
        out_root / "phase8l_data_family_expansion_signal_factory"
        / "trade_idea_candidate_registry.csv",
        out_root / "phase8k_self_expanding_alpha_factory" / "best_trade_idea_candidates.csv",
        out_root / "phase8k_self_expanding_alpha_factory"
        / "trade_idea_candidate_registry.csv",
        out_root / "phase8n_fmp_critical_data_backfill_signal_expansion"
        / "best_trade_idea_candidates.csv",
    ]
    for fp in probe_files:
        for r in _read_csv_file(fp):
            for key in ("ticker", "symbol", "Ticker", "Symbol"):
                tk = (r.get(key) or "").strip().upper()
                if tk and tk.isalpha() and len(tk) <= 5 and tk not in candidates:
                    candidates.append(tk)
    return candidates


# --------------------------------------------------------------------------- #
# Build the bounded, prioritized request plan (FMP-blocked first, then current universe).
#   Rows are at (ticker, endpoint) granularity: each ticker => 2 endpoints (<=2 requests).
# --------------------------------------------------------------------------- #
def build_request_plan(fmp: Dict, current_universe: List[str], already_cached: Sequence[str],
                       max_tickers: int, max_requests: int, skip_existing: bool) -> List[Dict]:
    cached = {str(t).strip().upper() for t in already_cached}
    tiers = (
        ("1_fmp_blocked", fmp["fmp_blocked"]),
        ("2_current_research_universe", current_universe),
        ("3_fmp_covered_crosscheck", fmp["fmp_covered"]),
        ("4_phase5c_fallback", PHASE5C_FALLBACK),
    )
    ordered: List[Tuple[str, str]] = []  # (ticker, priority)
    seen = set()
    for priority, tickers in tiers:
        for tk in tickers:
            tk = str(tk).strip().upper()
            if not tk or tk in seen:
                continue
            seen.add(tk)
            ordered.append((tk, priority))

    # Distinct tickers we will actually REQUEST (skip-existing ones do not count toward max_tickers).
    requestable_tickers = [tk for tk, _ in ordered if not (skip_existing and tk in cached)]
    request_set = set(requestable_tickers[:max_tickers])

    plan: List[Dict] = []
    requests_budgeted = 0
    for tk, priority in ordered:
        skipped = skip_existing and tk in cached
        for endpoint in _ENDPOINTS:
            will_request = (not skipped) and (tk in request_set) and (requests_budgeted < max_requests)
            if will_request:
                requests_budgeted += 1
            plan.append({
                "ticker": tk, "priority": priority, "endpoint": endpoint,
                "fmp_covered": tk in set(fmp["fmp_covered"]),
                "fmp_blocked": tk in set(fmp["fmp_blocked"]),
                "skip_existing": skipped,
                "will_request": will_request,
                "request_url_redacted": _plan_url(tk, endpoint),
            })
    return plan


# --------------------------------------------------------------------------- #
# Live probe (bounded; only with --live AND key, OR an injected transport).
# --------------------------------------------------------------------------- #
Transport = Callable[[str, str], object]


class EodhdError(Exception):
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


def _build_live_url(symbol: str, endpoint: str) -> str:
    base = (_FUNDAMENTALS_URL if endpoint == EP_FUNDAMENTALS else _EARN_CAL_URL)
    base = base.replace("{symbol}", urllib.parse.quote(symbol))
    key = os.environ.get(API_KEY_ENV, "") or ""
    return "%s&api_token=%s" % (base, urllib.parse.quote(key))


def _live_get(symbol: str, endpoint: str) -> object:
    """One bounded live GET to an allow-listed EODHD endpoint. The key-bearing URL is used
    transiently and never persisted; errors are sanitized so a key cannot leak through them."""
    url = _build_live_url(symbol, endpoint)
    if _host_of(url) not in ALLOWED_HOSTS:
        raise EodhdError("refusing non-allowlisted host", error_type="host_blocked")
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_SECONDS) as resp:  # nosec - allowlisted
            raw = resp.read()
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            raise EodhdError("non-JSON response: %s" % exc, error_type="bad_response")
    except urllib.error.HTTPError as exc:
        code = getattr(exc, "code", None)
        # Read the (non-secret) provider error text ONCE to tell a genuinely invalid key (401)
        # apart from a VALID key whose plan does not entitle the endpoint: EODHD returns HTTP 403
        # "Only EOD data allowed for free users" on the free tier. The body is used ONLY to
        # classify the error; it is never persisted and never contains the API key.
        body_low = ""
        try:
            body_low = exc.read().decode("utf-8", "replace").lower()
        except Exception:  # pragma: no cover - defensive
            body_low = ""
        free_tier = ("free users" in body_low) or ("only eod data" in body_low)
        if code == 402:
            etype = "plan_blocked"
        elif code == 403 and free_tier:
            etype = "free_tier_blocked"
        elif code in (401, 403):
            etype = "invalid_key"
        elif code == 429:
            etype = "rate_limited"
        else:
            etype = "http_error"
        raise EodhdError("provider returned HTTP %s" % code, status_code=code, error_type=etype)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EodhdError("network error: %s" % type(exc).__name__, error_type="network_error")


# --------------------------------------------------------------------------- #
# Payload classification per data family (metadata only - never emits raw values).
# --------------------------------------------------------------------------- #
def _span_years(first: str, last: str) -> float:
    def _year(d: str) -> Optional[int]:
        d = (d or "").strip()
        if len(d) >= 4 and d[:4].isdigit():
            return int(d[:4])
        return None
    y0, y1 = _year(first), _year(last)
    if y0 is None or y1 is None:
        return 0.0
    return float(abs(y1 - y0))


def _depth_sufficient(family: str, rows: int, first: str, last: str) -> bool:
    span = _span_years(first, last)
    if family in (FAM_EARNINGS, FAM_FUNDAMENTALS, FAM_EARN_CAL):
        return rows >= MIN_QUARTERS_FOR_DEPTH or span >= MIN_YEARS_FOR_DEPTH
    if family == FAM_ANALYST_EST:
        return rows >= 4
    return False  # analyst_ratings is a current snapshot - no historical depth


def _dates_minmax(values: Sequence[str]) -> Tuple[str, str]:
    ds = sorted(d for d in (str(v or "").strip() for v in values) if d)
    return (ds[0], ds[-1]) if ds else ("", "")


def _fam_result(family: str, status: str, rows: int, first: str, last: str,
                has_pit: bool, note: str) -> Dict:
    depth_ok = _depth_sufficient(family, rows, first, last) if status == "OK" else False
    usable = bool(status == "OK" and rows > 0 and has_pit and depth_ok)
    return {"family": family, "status": status, "rows": rows, "first_date": first,
            "last_date": last, "has_point_in_time_dates": has_pit,
            "historical_depth_sufficient": depth_ok, "usable_for_backtest": usable, "note": note}


def classify_fundamentals(payload) -> List[Dict]:
    """Classify an EODHD fundamentals payload into the four families it carries. Reads structure +
    counts + as-of dates only; never copies raw financial values into the returned metadata."""
    if not isinstance(payload, dict):
        return [_fam_result(f, "BAD_RESPONSE", 0, "", "", False, "non-dict payload")
                for f in (FAM_EARNINGS, FAM_FUNDAMENTALS, FAM_ANALYST_EST, FAM_ANALYST_RATINGS)]
    out: List[Dict] = []
    earnings = (payload.get("Earnings") or {}) if isinstance(payload.get("Earnings"), dict) else {}

    # --- earnings_history: actual / estimate / surprise / report date (PIT = announce date) ---
    hist = earnings.get("History") or {}
    if isinstance(hist, dict) and hist:
        report_dates = [(v.get("reportDate") if isinstance(v, dict) else "") for v in hist.values()]
        first, last = _dates_minmax(report_dates)
        has_pit = any(str(d or "").strip() for d in report_dates)
        out.append(_fam_result(FAM_EARNINGS, "OK", len(hist), first, last, has_pit,
                               "earnings history rows present"))
    else:
        out.append(_fam_result(FAM_EARNINGS, "EMPTY", 0, "", "", False, "no earnings history"))

    # --- fundamentals statements (PIT = filing_date) ---
    fins = (payload.get("Financials") or {}) if isinstance(payload.get("Financials"), dict) else {}
    inc = (fins.get("Income_Statement") or {}) if isinstance(fins.get("Income_Statement"), dict) else {}
    quarterly = inc.get("quarterly") or {}
    if isinstance(quarterly, dict) and quarterly:
        filing_dates = [(v.get("filing_date") if isinstance(v, dict) else "")
                        for v in quarterly.values()]
        keys = list(quarterly.keys())
        first, last = _dates_minmax(filing_dates or keys)
        has_pit = any(str(d or "").strip() for d in filing_dates)
        out.append(_fam_result(FAM_FUNDAMENTALS, "OK", len(quarterly), first, last, has_pit,
                               "quarterly income-statement rows present"))
    else:
        out.append(_fam_result(FAM_FUNDAMENTALS, "EMPTY", 0, "", "", False,
                               "no quarterly fundamentals"))

    # --- analyst estimates (revised; as-of dates are weak -> not PIT-safe) ---
    trend = earnings.get("Trend") or {}
    if isinstance(trend, dict) and trend:
        first, last = _dates_minmax(list(trend.keys()))
        out.append(_fam_result(FAM_ANALYST_EST, "OK", len(trend), first, last, False,
                               "analyst estimate trend present (no clean as-of history)"))
    else:
        out.append(_fam_result(FAM_ANALYST_EST, "EMPTY", 0, "", "", False, "no analyst estimates"))

    # --- analyst ratings: price targets / recommendations (current snapshot only) ---
    ratings = payload.get("AnalystRatings") or {}
    if isinstance(ratings, dict) and ratings:
        out.append(_fam_result(FAM_ANALYST_RATINGS, "OK", 1, "", "", False,
                               "current recommendation/price-target snapshot (not historical)"))
    else:
        out.append(_fam_result(FAM_ANALYST_RATINGS, "EMPTY", 0, "", "", False,
                               "no analyst ratings"))
    return out


def classify_calendar(payload) -> Dict:
    """Classify an EODHD earnings-calendar payload (PIT = report_date)."""
    rows = []
    if isinstance(payload, dict):
        rows = payload.get("earnings") or payload.get("Earnings") or []
    elif isinstance(payload, list):
        rows = payload
    if not isinstance(rows, list) or not rows:
        return _fam_result(FAM_EARN_CAL, "EMPTY", 0, "", "", False, "no earnings-calendar rows")
    report_dates = [(r.get("report_date") or r.get("reportDate") or "")
                    if isinstance(r, dict) else "" for r in rows]
    first, last = _dates_minmax(report_dates)
    has_pit = any(str(d or "").strip() for d in report_dates)
    return _fam_result(FAM_EARN_CAL, "OK", len(rows), first, last, has_pit,
                       "earnings-calendar rows present")


def _normalize_earnings(symbol: str, payload: Dict) -> List[Dict]:
    """Flatten EODHD Earnings::History into point-in-time rows (gitignored normalized cache only)."""
    rows: List[Dict] = []
    earnings = (payload.get("Earnings") or {}) if isinstance(payload, dict) else {}
    hist = earnings.get("History") or {}
    if isinstance(hist, dict):
        for fiscal, v in hist.items():
            if not isinstance(v, dict):
                continue
            rows.append({
                "symbol": symbol,
                "fiscal_date": (v.get("date") or fiscal or "").strip(),
                "report_date": (v.get("reportDate") or "").strip(),
                "eps_actual": (str(v.get("epsActual")) if v.get("epsActual") is not None else ""),
                "eps_estimate": (str(v.get("epsEstimate"))
                                 if v.get("epsEstimate") is not None else ""),
                "eps_difference": (str(v.get("epsDifference"))
                                   if v.get("epsDifference") is not None else ""),
                "surprise_percent": (str(v.get("surprisePercent"))
                                     if v.get("surprisePercent") is not None else ""),
            })
    return rows


_NORMALIZED_HEADER = ("symbol", "fiscal_date", "report_date", "eps_actual", "eps_estimate",
                      "eps_difference", "surprise_percent")


def collect_eodhd(plan: List[Dict], live: bool, transport: Optional[Transport], paths: _Paths,
                  max_requests: int, verbose: bool = False) -> Dict:
    """Run the bounded EODHD probe over the planned (ticker, endpoint) requests. Verbatim payloads
    are cached under the gitignored research/data/eodhd/raw tree and earnings rows are appended to
    the gitignored normalized CSV. Honors max_requests, skip_existing, and stops on an invalid key,
    a systematic plan-block (consecutive 402s), or consecutive rate-limit responses."""
    probe_rows: List[Dict] = []
    requests_made = 0
    consecutive_rl = 0
    consecutive_invalid = 0
    consecutive_block = 0
    stopped = False
    stop_reason = ""
    rate_limited = False
    invalid_key = False
    plan_blocked_systematic = False
    free_tier_block = False
    can_run = (transport is not None) or (bool(live) and key_present())

    targets = [p for p in plan if p.get("will_request") and not p["skip_existing"]]
    if not can_run:
        return {"probe_rows": probe_rows, "requests_made": 0, "stopped": False, "stop_reason": "",
                "rate_limited": False, "invalid_key": False,
                "plan_blocked_systematic": False, "free_tier_block": False, "executed": False}

    # Force-gitignore the EODHD data tree BEFORE any write so paid data can never be staged.
    _ensure_provider_gitignore(paths.eodhd_dir)
    normalized: Dict[str, List[Dict]] = {}

    for p in targets:
        tk, endpoint, redacted = p["ticker"], p["endpoint"], p["request_url_redacted"]
        if requests_made >= max_requests:
            stopped = True
            stop_reason = "max_requests (%d) reached" % max_requests
            break
        try:
            payload = transport(tk, endpoint) if transport is not None else _live_get(tk, endpoint)
            requests_made += 1
        except EodhdError as exc:
            requests_made += 1
            etype = getattr(exc, "error_type", "error")
            code = getattr(exc, "status_code", None)
            if etype == "invalid_key":
                consecutive_invalid += 1
                _append_probe(probe_rows, tk, endpoint, p["priority"], redacted,
                              [_fam_result(_endpoint_primary_family(endpoint), "INVALID_KEY", 0,
                                           "", "", False, "provider rejected the API key")], code)
                if consecutive_invalid >= STOP_AFTER_CONSECUTIVE_INVALID_KEY:
                    invalid_key = True
                    stopped = True
                    stop_reason = "invalid API key - stopped immediately"
                    break
                continue
            consecutive_invalid = 0
            if etype == "free_tier_blocked":
                # Valid key, but the active EODHD plan (free tier) serves EOD prices only and
                # rejects the earnings/fundamentals bundle. This is an account-level entitlement
                # block - systematic from the first response - so stop immediately and conserve the
                # tiny free-tier daily quota.
                free_tier_block = True
                _append_probe(probe_rows, tk, endpoint, p["priority"], redacted,
                              [_fam_result(_endpoint_primary_family(endpoint), "PLAN_BLOCKED", 0,
                                           "", "", False,
                                           "HTTP 403 - EODHD FREE tier: the API key is VALID but the "
                                           "plan serves EOD prices only; earnings/fundamentals "
                                           "require a paid EODHD plan")], code)
                stopped = True
                stop_reason = ("EODHD free-tier entitlement block (HTTP 403): the API key is valid "
                               "but the free plan allows EOD price data only; the earnings/"
                               "fundamentals bundle requires a paid EODHD plan")
                break
            if etype == "plan_blocked":
                consecutive_block += 1
                _append_probe(probe_rows, tk, endpoint, p["priority"], redacted,
                              [_fam_result(_endpoint_primary_family(endpoint), "PLAN_BLOCKED", 0,
                                           "", "", False, "HTTP 402 - endpoint not in plan")], code)
                if consecutive_block >= STOP_AFTER_SYSTEMATIC_PLAN_BLOCKS:
                    plan_blocked_systematic = True
                    stopped = True
                    stop_reason = ("systematic plan-block (%d consecutive HTTP 402)"
                                   % consecutive_block)
                    break
                continue
            consecutive_block = 0
            if etype == "rate_limited":
                consecutive_rl += 1
                _append_probe(probe_rows, tk, endpoint, p["priority"], redacted,
                              [_fam_result(_endpoint_primary_family(endpoint), "RATE_LIMITED", 0,
                                           "", "", False, "HTTP 429 - rate limited")], code)
                if consecutive_rl >= STOP_AFTER_CONSECUTIVE_RATE_LIMITS:
                    rate_limited = True
                    stopped = True
                    stop_reason = ("rate-limited %d times consecutively" % consecutive_rl)
                    break
                if transport is None:
                    time.sleep(PROBE_MIN_SLEEP_SECONDS)
                continue
            # generic http/network/host error
            _append_probe(probe_rows, tk, endpoint, p["priority"], redacted,
                          [_fam_result(_endpoint_primary_family(endpoint), "ERROR", 0, "", "",
                                       False, "%s (%s)" % (str(exc), etype))], code)
            if transport is None:
                time.sleep(PROBE_MIN_SLEEP_SECONDS)
            continue

        consecutive_rl = consecutive_invalid = consecutive_block = 0
        if endpoint == EP_FUNDAMENTALS:
            fam_results = classify_fundamentals(payload)
            nrows = _normalize_earnings(tk, payload)
            if nrows:
                normalized[tk] = nrows
            if any(fr["status"] == "OK" for fr in fam_results):
                _persist_raw(paths, tk, "fundamentals", payload)
        else:
            fam_results = [classify_calendar(payload)]
            if fam_results[0]["status"] == "OK":
                _persist_raw(paths, tk, "calendar", payload)
        _append_probe(probe_rows, tk, endpoint, p["priority"], redacted, fam_results, None)
        if verbose:
            ok = ", ".join("%s=%d" % (fr["family"], fr["rows"]) for fr in fam_results)
            print("  probe %-6s %-16s -> %s" % (tk, endpoint, ok))
        if transport is None:
            time.sleep(PROBE_MIN_SLEEP_SECONDS)

    if normalized:
        _append_normalized(paths, normalized)

    return {"probe_rows": probe_rows, "requests_made": requests_made, "stopped": stopped,
            "stop_reason": stop_reason, "rate_limited": rate_limited, "invalid_key": invalid_key,
            "plan_blocked_systematic": plan_blocked_systematic, "free_tier_block": free_tier_block,
            "executed": True}


def _endpoint_primary_family(endpoint: str) -> str:
    return FAM_EARN_CAL if endpoint == EP_EARNINGS_CALENDAR else FAM_EARNINGS


def _append_probe(probe_rows: List[Dict], ticker: str, endpoint: str, priority: str,
                  redacted: str, fam_results: List[Dict], http_status) -> None:
    for fr in fam_results:
        probe_rows.append({
            "endpoint_family": fr["family"], "ticker": ticker, "endpoint": endpoint,
            "priority": priority, "attempted": True, "status": fr["status"],
            "http_status": "" if http_status is None else http_status,
            "rows": fr["rows"], "first_date": fr["first_date"], "last_date": fr["last_date"],
            "has_point_in_time_dates": fr["has_point_in_time_dates"],
            "historical_depth_sufficient": fr["historical_depth_sufficient"],
            "usable_for_backtest": fr["usable_for_backtest"],
            "note": fr["note"], "endpoint_redacted": redacted})


def _persist_raw(paths: _Paths, ticker: str, kind: str, payload) -> None:
    raw_path = paths.raw_dir / kind / ("%s.json" % ticker)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with open(raw_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")


def _append_normalized(paths: _Paths, normalized: Dict[str, List[Dict]]) -> None:
    path = paths.normalized_csv
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.is_file()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if not existing:
            w.writerow(_NORMALIZED_HEADER)
        for tk in sorted(normalized):
            for r in normalized[tk]:
                w.writerow([r[c] for c in _NORMALIZED_HEADER])


def _cached_tickers(paths: _Paths) -> List[str]:
    tickers: List[str] = []
    for r in _read_csv_file(paths.normalized_csv):
        tk = (r.get("symbol") or "").strip().upper()
        if tk and tk not in tickers:
            tickers.append(tk)
    return tickers


def _ensure_provider_gitignore(provider_dir: Path) -> None:
    """Force-gitignore the EODHD local data dir (ignore EVERYTHING except the .gitignore itself)
    so licensed raw/normalized payloads can never be staged. Mirrors research/data/alphavantage."""
    provider_dir.mkdir(parents=True, exist_ok=True)
    gi = provider_dir / ".gitignore"
    if gi.is_file():
        return
    lines = [
        "# Phase 8-R - LOCAL EODHD data. DO NOT COMMIT.",
        "#",
        "# research/run_phase8r_broad_bundle_evaluation.py --live writes EODHD payloads here:",
        "#   raw/        verbatim provider JSON responses",
        "#   normalized/ flattened point-in-time earnings CSVs derived from the responses",
        "#",
        "# These are license-restricted provider responses and must never enter Git. Only",
        "# coverage / decision metadata (row counts, date spans, redacted URLs, file paths - never",
        "# the payloads, never the API key) is written to the committed artifacts under",
        "# research/output/phase8r_broad_bundle_evaluation/.",
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


# --------------------------------------------------------------------------- #
# Coverage aggregation + comparison.
# --------------------------------------------------------------------------- #
def aggregate_coverage(probe_rows: List[Dict]) -> Dict:
    """Aggregate per-family coverage from the probe rows, and compute the core-earnings stats that
    drive the decision (fraction of attempted tickers with a backtest-usable earnings panel)."""
    attempted_tickers = {r["ticker"] for r in probe_rows if r["attempted"]}
    by_family: Dict[str, Dict] = {}
    for fam in _FAMILIES:
        fam_rows = [r for r in probe_rows if r["endpoint_family"] == fam]
        attempted = {r["ticker"] for r in fam_rows}
        with_data = {r["ticker"] for r in fam_rows if r["status"] == "OK" and int(r["rows"]) > 0}
        pit = {r["ticker"] for r in fam_rows if str(r["has_point_in_time_dates"]) == "True"}
        usable = {r["ticker"] for r in fam_rows if str(r["usable_for_backtest"]) == "True"}
        by_family[fam] = {
            "tickers_attempted": len(attempted), "tickers_with_data": len(with_data),
            "tickers_pit_safe": len(pit), "tickers_backtest_usable": len(usable),
            "coverage_fraction": round(len(with_data) / len(attempted), 3) if attempted else 0.0,
        }
    core = by_family[_CORE_FAMILY]
    n_attempted = core["tickers_attempted"] or len(attempted_tickers)
    earnings_usable = core["tickers_backtest_usable"]
    stats = {
        "tickers_attempted": n_attempted,
        "earnings_tickers_with_data": core["tickers_with_data"],
        "earnings_tickers_backtest_usable": earnings_usable,
        "earnings_usable_fraction": round(earnings_usable / n_attempted, 3) if n_attempted else 0.0,
        "earnings_pit_safe": core["tickers_pit_safe"] > 0 and (
            core["tickers_pit_safe"] >= max(1, int(0.5 * n_attempted))) if n_attempted else False,
    }
    return {"by_family": by_family, "stats": stats}


def build_comparison(probe_rows: List[Dict], fmp: Dict, av_covered: List[str]) -> List[Dict]:
    """Per-ticker EODHD vs FMP vs Alpha Vantage earnings coverage comparison."""
    eodhd_covered = {r["ticker"] for r in probe_rows
                     if r["endpoint_family"] == _CORE_FAMILY and r["status"] == "OK"
                     and int(r["rows"]) > 0}
    fmp_set = set(fmp["fmp_covered"])
    av_set = {t.strip().upper() for t in av_covered}
    all_tickers = sorted(fmp_set | av_set | eodhd_covered | set(fmp["fmp_blocked"]))
    out = []
    for tk in all_tickers:
        out.append({
            "ticker": tk, "fmp_covered": tk in fmp_set, "alphavantage_covered": tk in av_set,
            "eodhd_covered": tk in eodhd_covered,
            "eodhd_newly_covered": tk in eodhd_covered and tk not in (fmp_set | av_set),
        })
    return out


# --------------------------------------------------------------------------- #
# Prior-phase evidence (read-only; cost tier from prior committed artifacts only).
# --------------------------------------------------------------------------- #
def read_prior_evidence(P: _Paths) -> Dict:
    ev: Dict = {}
    q = _read_json_file(P.phase8q / "phase8q_market_data_foundation_decision.json")
    ev["phase8q_read"] = bool(q)
    ev["phase8q_decision"] = q.get("decision", "MIXED_FREE_PLUS_PAID_CORE_STACK")
    ev["fmp_ultimate_rejected"] = bool(q.get("fmp_ultimate_rejected", True))
    ev["provider_category_first"] = q.get(
        "provider_category_to_evaluate_first", "earnings/fundamentals bundle (EODHD, ~$20-$80/mo)")

    p = _read_json_file(P.phase8p / "phase8p_alphavantage_earnings_expansion.json")
    ev["phase8p_read"] = bool(p)
    ev["av_combined_count"] = int(p.get("combined_covered_count", 9) or 9)
    ev["av_covered_tickers"] = list(p.get("alphavantage_covered_tickers", ["ABT"]) or ["ABT"])

    # Cost tiers from the 8-Q paid matrix ONLY (no new quotes; presence-safe defaults otherwise).
    eodhd_cost, fmp_premium_cost = "~$20-$80/mo", "~$69/mo"
    for r in _read_csv_file(P.phase8q / "paid_provider_decision_matrix.csv"):
        prov = (r.get("provider") or "").lower()
        if prov.startswith("eodhd"):
            eodhd_cost = r.get("monthly_cost", eodhd_cost) or eodhd_cost
        elif "premium" in prov:
            fmp_premium_cost = r.get("monthly_cost", fmp_premium_cost) or fmp_premium_cost
    ev["eodhd_cost_tier"] = eodhd_cost
    ev["fmp_premium_cost_tier"] = fmp_premium_cost
    ev["fmp_min_family_coverage"] = 8  # 8-N: every critical family PARTIAL at 8/20
    return ev


# --------------------------------------------------------------------------- #
# Decision logic.
# --------------------------------------------------------------------------- #
def derive_decision(present: bool, executed: bool, result: Dict, stats: Dict) -> Tuple[str, bool, str]:
    """Map the run state to (decision, fmp_premium_fallback_recommended, gate_reason)."""
    if not present and not executed:
        return (DEC_BLOCKED_NO_KEY, False,
                "EODHD_API_KEY is not set in this environment; set it and re-run --live to run the "
                "bounded coverage/PIT evaluation. Offline plan written; no network used.")
    if result.get("invalid_key"):
        return (DEC_BLOCKED_NO_KEY, False,
                "EODHD_API_KEY is present but the provider rejected it as invalid; supply a valid "
                "key and re-run. No usable coverage was collected.")
    if result.get("free_tier_block"):
        return (DEC_NEEDS_QUOTES, False,
                "EODHD_API_KEY is VALID but the account is on the FREE tier, which serves EOD price "
                "data only; the earnings/fundamentals/analyst bundle returned HTTP 403. Subscribe "
                "to a paid EODHD plan that includes the Fundamentals Data feed, then re-run --live "
                "to run the bounded coverage/PIT/cost evaluation. The FMP Premium fallback stays ON "
                "STANDBY - EODHD was not fairly tested, the data was simply not entitled on the "
                "free tier.")
    if result.get("plan_blocked_systematic"):
        return (DEC_REJECTED, True,
                "EODHD returned a systematic plan-block (consecutive HTTP 402): the active EODHD "
                "plan does not entitle the required endpoints. Evaluate the FMP Premium fallback.")
    attempted = stats["tickers_attempted"]
    frac = stats["earnings_usable_fraction"]
    if attempted == 0:
        if result.get("rate_limited"):
            return (DEC_PROMISING, False,
                    "EODHD rate-limited before any usable coverage was measured; re-run a larger "
                    "bounded trial after backoff before deciding.")
        return (DEC_REJECTED, True,
                "EODHD returned no usable earnings coverage for the probed tickers; evaluate the "
                "FMP Premium fallback.")
    if frac >= ACCEPT_FRACTION and stats["earnings_pit_safe"] and not result.get("rate_limited"):
        return (DEC_ACCEPT, False,
                "EODHD delivered backtest-usable, point-in-time earnings coverage for %.0f%% of the "
                "probed tickers (>= %.0f%% accept gate); accept it as the core data provider."
                % (frac * 100, ACCEPT_FRACTION * 100))
    if frac >= PROMISING_FRACTION or result.get("rate_limited"):
        return (DEC_PROMISING, False,
                "EODHD is promising (usable earnings coverage for %.0f%% of probed tickers) but "
                "below the %.0f%% accept gate or was throttled; run a larger bounded trial."
                % (frac * 100, ACCEPT_FRACTION * 100))
    return (DEC_REJECTED, True,
            "EODHD usable earnings coverage (%.0f%%) is below the %.0f%% promising gate; evaluate "
            "the FMP Premium fallback (Ultimate remains rejected)."
            % (frac * 100, PROMISING_FRACTION * 100))


# --------------------------------------------------------------------------- #
# Static report builders (vendor scorecard, fallback plan, procurement questions).
# --------------------------------------------------------------------------- #
def _vendor_scorecard(ev: Dict, by_family: Dict, stats: Dict, executed: bool) -> List[Dict]:
    """Score EODHD on the nine vendor axes. When the evaluation has not run live (no key), the
    measured axes read 'not_measured (no key)' and the documented expectations are recorded."""
    core = by_family.get(_CORE_FAMILY, {})
    measured = executed and stats["tickers_attempted"] > 0

    def m(value_if_measured: str) -> str:
        return value_if_measured if measured else "not_measured (set EODHD_API_KEY and --live)"

    return [
        {"axis": "data_family_breadth", "score": "strong_expected",
         "evidence": "single bundle covers earnings + fundamentals + analyst (4 families) + "
                     "earnings calendar; documented in 8-Q paid matrix"},
        {"axis": "coverage_100_500_tickers",
         "score": m("measured frac=%.2f over %d probed tickers"
                    % (stats["earnings_usable_fraction"], stats["tickers_attempted"])),
         "evidence": "bounded probe prioritizes FMP-blocked + current universe; target is %d, "
                     "min gate %d" % (BROAD_UNIVERSE_TARGET, MIN_SCORING_GATE)},
        {"axis": "history_depth",
         "score": m("earnings rows>=%d or span>=%dy required for depth" %
                    (MIN_QUARTERS_FOR_DEPTH, MIN_YEARS_FOR_DEPTH)),
         "evidence": "per-ticker depth recorded in probe results (historical_depth_sufficient)"},
        {"axis": "point_in_time_safety",
         "score": m("earnings reportDate + fundamentals filing_date = PIT-safe; analyst snapshot "
                    "NOT PIT"),
         "evidence": "PIT readiness per family in eodhd_point_in_time_readiness.csv"},
        {"axis": "update_frequency", "score": "real_time_expected",
         "evidence": "EODHD updates fundamentals/earnings near real-time (unlike delayed SimFin) - "
                     "confirm via procurement question"},
        {"axis": "schema_stability", "score": "documented_stable_expected",
         "evidence": "documented JSON schema; in-repo adapter maps earnings history / financial "
                     "statements / analyst ratings"},
        {"axis": "bulk_batch_support", "score": "yes_expected",
         "evidence": "fundamentals bulk endpoint avoids 500 throttled calls (vs AV 25/day)"},
        {"axis": "api_request_limits",
         "score": m("requests_made=%d within max_requests=%d this run"
                    % (stats.get("requests_made", 0), DEFAULT_MAX_REQUESTS)),
         "evidence": "bounded run; confirm daily/minute caps and bulk allowance via procurement"},
        {"axis": "estimated_cost_tier", "score": ev["eodhd_cost_tier"],
         "evidence": "from 8-Q paid matrix only (no new quote collected); cheapest broad bundle"},
    ]


def _fmp_premium_fallback_plan(ev: Dict, fallback_recommended: bool) -> List[Dict]:
    status = "RECOMMENDED" if fallback_recommended else "ON_STANDBY"
    return [
        {"item": "trigger_condition", "value": status,
         "detail": "evaluate FMP Premium ONLY if EODHD is rejected on coverage/PIT/history/plan-block"},
        {"item": "provider", "value": "FMP Premium",
         "detail": "adapter already mapped in-repo (8-N); %s" % ev["fmp_premium_cost_tier"]},
        {"item": "families_covered", "value": "earnings + fundamentals + analyst (6 critical)",
         "detail": "same critical families EODHD targets; live only if user upgrades FMP access"},
        {"item": "evaluation_mode", "value": "PLAN_ONLY_UNTIL_UPGRADE",
         "detail": "do NOT run FMP Premium live unless the user explicitly provides upgraded access"},
        {"item": "fmp_ultimate", "value": "REJECTED",
         "detail": "8-O/8-Q: no evidence requires the Ultimate tier; not evaluated"},
        {"item": "next_command_if_triggered", "value":
         "$env:FMP_API_KEY = '<UPGRADED_FMP_PREMIUM_KEY>'; "
         "python research/run_phase8r_broad_bundle_evaluation.py --live --provider fmp_premium",
         "detail": "fallback path; bounded, same coverage/PIT criteria"},
    ]


def _procurement_questions() -> List[Tuple[str, str]]:
    return [
        ("Does one EODHD subscription cover earnings, fundamentals, AND analyst data for the full "
         "S&P 500 (ideally 1000+ US tickers), not just a sample?",
         "one key for 100-500+ tickers avoids stitching multiple providers"),
        ("How many years of quarterly earnings actual/estimate/surprise history are included per "
         "ticker?", "backtests need >= 8-10y of history"),
        ("Do earnings records carry the original report/announcement date (reportDate) so a "
         "backtest only sees data available at the time?",
         "PIT report dates are mandatory to avoid look-ahead bias"),
        ("Do fundamentals statements include the SEC filing_date (not just the fiscal period end)?",
         "filing_date is the point-in-time availability date for fundamentals"),
        ("Are analyst estimates/price-targets available with historical as-of dates, or only the "
         "current snapshot?",
         "without as-of history the analyst families are not PIT-safe (the current analyst-ratings "
         "response is a snapshot only)"),
        ("What are the per-minute and per-day request limits, and is there a bulk/EOD fundamentals "
         "feed to pull the universe in one job?",
         "AV free (25/day) was unusable for broad coverage; we need a full refresh in <1 day"),
        ("What is the exact monthly cost for the tier that meets the above, and is there a cheaper "
         "tier still covering earnings + fundamentals?",
         "favor the cheapest tier covering the required families; reject over-provisioned tiers"),
        ("Do the license terms permit caching/storing normalized data locally for research (we "
         "never redistribute or commit payloads)?",
         "we cache locally under gitignored trees; storage rights must be explicit"),
        ("How quickly is data updated after an earnings release (real-time, T+1, or delayed)?",
         "delayed fundamentals are unusable as a clean real-time PIT signal source"),
    ]


def _next_command(decision: str) -> str:
    if decision == DEC_BLOCKED_NO_KEY:
        return ("$env:EODHD_API_KEY = '<PASTE_EODHD_API_KEY_HERE>'; "
                "python research/run_phase8r_broad_bundle_evaluation.py --live")
    if decision == DEC_PROMISING:
        return ("python research/run_phase8r_broad_bundle_evaluation.py --live --max-tickers 100 "
                "--max-requests 200   (larger bounded EODHD trial after backoff)")
    if decision == DEC_ACCEPT:
        return ("python research/run_phase8s_eodhd_broad_earnings_panel.py --live   "
                "(build the EODHD adapter + broad PIT earnings panel, then resume signal scoring)")
    if decision in (DEC_REJECTED, DEC_FMP_FALLBACK):
        return ("python research/run_phase8s_fmp_premium_fallback_eval.py   "
                "(evaluate FMP Premium fallback PLAN; live only if the user upgrades FMP access; "
                "Ultimate rejected)")
    if decision == DEC_NEEDS_QUOTES:
        return ("Subscribe to a paid EODHD plan that includes the Fundamentals Data feed (the free "
                "tier serves EOD prices only), then re-run: $env:EODHD_API_KEY = "
                "'<PAID_EODHD_KEY>'; python research/run_phase8r_broad_bundle_evaluation.py --live "
                "--max-tickers 150 --max-requests 500")
    return "python research/run_phase8r_broad_bundle_evaluation.py"


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
def run(live: bool = False, transport: Optional[Transport] = None,
        out_dir: Optional[Path] = None, data_dir: Optional[Path] = None,
        phase8q_dir: Optional[Path] = None, phase8p_dir: Optional[Path] = None,
        phase8n_dir: Optional[Path] = None, max_tickers: int = DEFAULT_MAX_TICKERS,
        max_requests: int = DEFAULT_MAX_REQUESTS, skip_existing: bool = True,
        verbose: bool = True) -> Dict:
    """Run Phase 8-R. Default (offline) builds the plan + decision with no network. A live bounded
    evaluation needs --live AND EODHD_API_KEY (or an injected ``transport`` in tests). Returns the
    main report dict."""
    P = _Paths(out_dir, data_dir, phase8q_dir, phase8p_dir, phase8n_dir)
    P.out.mkdir(parents=True, exist_ok=True)

    try:
        present = key_present()
        ev = read_prior_evidence(P)
        fmp = read_fmp_earnings_coverage(P.phase8n)
        current_universe = extract_current_universe(P.out.parent)
        cached = _cached_tickers(P)
        plan = build_request_plan(fmp, current_universe, cached, max_tickers, max_requests,
                                  skip_existing)

        # ----- key detection report (PRESENCE ONLY; value never read) -----
        _write_csv(P.key_detection,
                   ["env_var", "provider", "key_present", "value_read", "note"],
                   [[API_KEY_ENV, PROVIDER_NAME, present, False,
                     "presence-only; value never read, printed, or written to disk"]])

        # ----- request plan -----
        _write_csv(P.request_plan,
                   ["ticker", "priority", "endpoint", "fmp_covered", "fmp_blocked",
                    "skip_existing", "will_request", "request_url_redacted"],
                   [[p["ticker"], p["priority"], p["endpoint"], p["fmp_covered"], p["fmp_blocked"],
                     p["skip_existing"], p["will_request"], p["request_url_redacted"]]
                    for p in plan])

        # ----- bounded probe (only with --live+key OR an injected transport) -----
        result = collect_eodhd(plan, live, transport, P, max_requests, verbose)
        executed = result["executed"]
        probe_rows = result["probe_rows"]

        _write_csv(P.probe_results,
                   ["endpoint_family", "ticker", "endpoint", "priority", "attempted", "status",
                    "http_status", "rows", "first_date", "last_date", "has_point_in_time_dates",
                    "historical_depth_sufficient", "usable_for_backtest", "note",
                    "endpoint_redacted"],
                   [[r["endpoint_family"], r["ticker"], r["endpoint"], r["priority"],
                     r["attempted"], r["status"], r["http_status"], r["rows"], r["first_date"],
                     r["last_date"], r["has_point_in_time_dates"],
                     r["historical_depth_sufficient"], r["usable_for_backtest"], r["note"],
                     r["endpoint_redacted"]] for r in probe_rows])

        # ----- coverage aggregation + decision stats -----
        agg = aggregate_coverage(probe_rows)
        by_family, stats = agg["by_family"], agg["stats"]
        stats["requests_made"] = result["requests_made"]

        _write_csv(P.coverage_by_family,
                   ["endpoint_family", "tickers_attempted", "tickers_with_data",
                    "tickers_point_in_time_safe", "tickers_backtest_usable", "coverage_fraction"],
                   [[fam, by_family[fam]["tickers_attempted"], by_family[fam]["tickers_with_data"],
                     by_family[fam]["tickers_pit_safe"], by_family[fam]["tickers_backtest_usable"],
                     by_family[fam]["coverage_fraction"]] for fam in _FAMILIES])

        # ----- EODHD vs FMP vs Alpha Vantage comparison -----
        comparison = build_comparison(probe_rows, fmp, ev["av_covered_tickers"])
        eodhd_covered_count = sum(1 for c in comparison if c["eodhd_covered"])
        _write_csv(P.comparison,
                   ["ticker", "fmp_covered", "alphavantage_covered", "eodhd_covered",
                    "eodhd_newly_covered"],
                   [[c["ticker"], c["fmp_covered"], c["alphavantage_covered"], c["eodhd_covered"],
                     c["eodhd_newly_covered"]] for c in comparison])

        # ----- point-in-time readiness per family -----
        _pit_basis = {
            FAM_EARNINGS: "reportDate (earnings announcement date) = PIT-safe",
            FAM_EARN_CAL: "report_date (calendar) = PIT-safe",
            FAM_FUNDAMENTALS: "filing_date (SEC filing) = PIT-safe when present",
            FAM_ANALYST_EST: "estimate trend is revised; no clean as-of history = NOT PIT-safe",
            FAM_ANALYST_RATINGS: "current recommendation/target snapshot only = NOT PIT-safe",
        }
        _write_csv(P.pit_readiness,
                   ["endpoint_family", "point_in_time_basis", "pit_safe_design",
                    "tickers_point_in_time_safe", "tickers_attempted"],
                   [[fam, _pit_basis[fam],
                     fam in (FAM_EARNINGS, FAM_EARN_CAL, FAM_FUNDAMENTALS),
                     by_family[fam]["tickers_pit_safe"], by_family[fam]["tickers_attempted"]]
                    for fam in _FAMILIES])

        # ----- decision -----
        decision, fallback_recommended, gate_reason = derive_decision(
            present, executed, result, stats)

        # ----- vendor scorecard -----
        scorecard = _vendor_scorecard(ev, by_family, stats, executed)
        _write_csv(P.vendor_scorecard,
                   ["axis", "score", "evidence"],
                   [[s["axis"], s["score"], s["evidence"]] for s in scorecard])

        # ----- provider cost/value decision -----
        _write_csv(P.cost_value_decision,
                   ["provider", "role", "monthly_cost", "covers_broad_earnings", "point_in_time",
                    "decision", "rationale"],
                   [["EODHD", "core earnings/fundamentals/analyst bundle (test-first)",
                     ev["eodhd_cost_tier"], decision == DEC_ACCEPT,
                     stats["earnings_pit_safe"], decision, gate_reason],
                    ["FMP Premium", "fallback if EODHD fails", ev["fmp_premium_cost_tier"],
                     "yes (documented)", "yes",
                     DEC_FMP_FALLBACK if fallback_recommended else "ON_STANDBY",
                     "evaluate only if EODHD rejected; adapter already mapped in-repo"],
                    ["FMP Ultimate", "rejected", "most expensive", "yes (over-provisioned)", "yes",
                     "REJECTED", "no evidence requires the Ultimate tier (8-O/8-Q)"]])

        # ----- FMP Premium fallback plan -----
        _write_csv(P.fmp_fallback_plan,
                   ["item", "value", "detail"],
                   [[r["item"], r["value"], r["detail"]]
                    for r in _fmp_premium_fallback_plan(ev, fallback_recommended)])

        # ----- procurement questions -----
        _write_csv(P.procurement_questions,
                   ["question", "why_it_matters"],
                   [[q, w] for q, w in _procurement_questions()])

        next_cmd = _next_command(decision)

        # ----- Phase 8-S next plan -----
        _write_json(P.next_plan, {
            "phase": "8-S",
            "title": _next_title(decision),
            "depends_on_decision": decision,
            "fmp_premium_fallback_recommended": fallback_recommended,
            "fmp_ultimate_rejected": True,
            "objective": _next_objective(decision),
            "test_first_provider": "EODHD",
            "fallback_provider": "FMP Premium (plan-only until upgrade)",
            "do_not_run_full_sp500_backfill": True,
            "next_command": next_cmd,
            "preview_only": True, "orders_enabled": False, "automation_enabled": False,
            "broker_execution_enabled": False, "committed": False,
        })

        # ----- secret leak scan over the committed artifacts written this run -----
        written = [p for p in P.out.glob("*") if p.is_file()]
        leak_clean, scanned, marker_found = _scan_for_leaks(written)
        _write_csv(P.secret_audit,
                   ["check", "result", "detail"],
                   [["api_key_read_from_env_only", True, "EODHD_API_KEY via os.environ only"],
                    ["api_key_printed", False, "key value never printed"],
                    ["api_key_written_to_disk", False, "key value never written to any artifact"],
                    ["committed_artifacts_contain_key_query_param", marker_found,
                     "leak scan over %d committed files for key query params" % scanned],
                    ["secret_leak_scan_clean", leak_clean,
                     "no key value or key query param in artifacts"],
                    ["raw_normalized_gitignored", True,
                     "research/data/eodhd/{raw,normalized}/ force-gitignored before write"]])

        # ----- main report -----
        report = {
            "phase": PHASE,
            "objective": (
                "Run a bounded, key-gated evaluation of the cheapest broad paid earnings/"
                "fundamentals bundle (EODHD test-first) to decide whether it can serve as the core "
                "data backbone for 100-500 ticker point-in-time research - NOT a full backfill."),
            "provider": PROVIDER_NAME, "provider_hosts": list(ALLOWED_HOSTS),
            "api_key_env_var": API_KEY_ENV, "eodhd_key_present": present, "api_key_logged": False,
            "eodhd_key_invalid": bool(result.get("invalid_key")),
            "eodhd_free_tier_blocked": bool(result.get("free_tier_block")),
            "mode": "live_evaluation" if executed and transport is None else (
                "test_transport" if executed else "offline_plan"),
            "live_requested": bool(live),
            "bounded_limits": {
                "max_tickers": max_tickers, "max_requests": max_requests,
                "skip_existing": skip_existing,
                "stop_after_consecutive_rate_limits": STOP_AFTER_CONSECUTIVE_RATE_LIMITS,
                "stop_after_consecutive_invalid_key": STOP_AFTER_CONSECUTIVE_INVALID_KEY,
                "stop_after_systematic_plan_blocks": STOP_AFTER_SYSTEMATIC_PLAN_BLOCKS,
                "no_full_sp500_backfill": True},
            "phase8q_decision": ev["phase8q_decision"],
            "fmp_blocked_tickers": sorted(fmp["fmp_blocked"]),
            "current_universe_tickers": current_universe,
            "requests_made": result["requests_made"],
            "evaluation_executed": executed,
            "evaluation_stopped_early": result["stopped"],
            "stop_reason": result["stop_reason"],
            "rate_limited": result["rate_limited"],
            "plan_blocked_systematic": result["plan_blocked_systematic"],
            "tickers_attempted": stats["tickers_attempted"],
            "eodhd_covered_count": eodhd_covered_count,
            "coverage_by_family": by_family,
            "earnings_usable_fraction": stats["earnings_usable_fraction"],
            "earnings_point_in_time_safe": stats["earnings_pit_safe"],
            "min_scoring_gate": MIN_SCORING_GATE,
            "broad_universe_target": BROAD_UNIVERSE_TARGET,
            "tested_endpoint_families": list(_FAMILIES),
            "decision": decision, "decision_is_terminal": decision in ALLOWED_DECISIONS,
            "allowed_decisions": list(ALLOWED_DECISIONS),
            "gate_reason": gate_reason,
            "eodhd_accept_as_core": decision == DEC_ACCEPT,
            "eodhd_needs_larger_trial": decision == DEC_PROMISING,
            "eodhd_rejected": decision == DEC_REJECTED,
            "fmp_premium_fallback_recommended": fallback_recommended,
            "fmp_ultimate_rejected": True,
            "eodhd_cost_tier": ev["eodhd_cost_tier"],
            "fmp_premium_cost_tier": ev["fmp_premium_cost_tier"],
            "recommended_next_command": next_cmd,
            "secret_safety_leak_scan_clean": leak_clean,
            "secret_safety_files_scanned": scanned,
            "raw_dir_gitignored": _rel(P.eodhd_dir),
            "outputs": {k: _rel(getattr(P, k)) for k in _ARTIFACTS},
            # Safety / provenance contract.
            "preview_only": True, "orders_enabled": False, "automation_enabled": False,
            "broker_execution_enabled": False, "production_replacement": False,
            "network_used": bool(executed and transport is None),
            "full_sp500_backfill_run": False,
            "paper_trader_touched": False, "gcp_touched": False, "deployed": False,
            "data_fabricated": False, "no_alpha_fabricated": True,
            "raw_paid_data_in_artifacts": False, "wrote_to_d_drive": False, "committed": False,
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


def _next_title(decision: str) -> str:
    if decision == DEC_ACCEPT:
        return "Build the EODHD adapter + broad point-in-time earnings panel"
    if decision == DEC_PROMISING:
        return "Larger bounded EODHD trial (still not a full backfill)"
    if decision in (DEC_REJECTED, DEC_FMP_FALLBACK):
        return "Evaluate the FMP Premium fallback (plan-only until upgrade; Ultimate rejected)"
    if decision == DEC_BLOCKED_NO_KEY:
        return "Set EODHD_API_KEY and run the bounded evaluation"
    if decision == DEC_NEEDS_QUOTES:
        return "Subscribe to a paid EODHD fundamentals plan, then re-run the bounded evaluation"
    return "Re-run the broad-bundle evaluation"


def _next_objective(decision: str) -> str:
    if decision == DEC_ACCEPT:
        return ("EODHD met the accept gate: build the in-repo adapter and ingest a broad PIT "
                "earnings panel (bounded, gitignored), then resume earnings-surprise signal scoring.")
    if decision == DEC_PROMISING:
        return ("EODHD is promising but below the accept gate or throttled: run a larger bounded "
                "trial (more tickers/requests, backoff) before committing budget.")
    if decision in (DEC_REJECTED, DEC_FMP_FALLBACK):
        return ("EODHD did not meet the bar: evaluate the FMP Premium fallback as a PLAN; run it "
                "live only if the user provides upgraded FMP access. FMP Ultimate stays rejected.")
    if decision == DEC_BLOCKED_NO_KEY:
        return ("EODHD_API_KEY is missing (or invalid): set a valid key and re-run --live to run "
                "the bounded coverage/PIT/cost evaluation. No budget committed yet.")
    if decision == DEC_NEEDS_QUOTES:
        return ("EODHD_API_KEY is valid but on the FREE tier (EOD prices only): subscribe to a "
                "paid EODHD plan that includes the Fundamentals Data feed, then re-run --live to "
                "run the bounded coverage/PIT/cost evaluation. No budget committed beyond the "
                "EODHD subscription; FMP Premium stays on standby.")
    return "Re-run the bounded EODHD evaluation."


def _scan_for_leaks(paths: Sequence[Path]) -> Tuple[bool, int, bool]:
    """Confirm no committed artifact contains a key query param or any held key value."""
    markers = ("api" + "key=", "api_token=")
    secret = os.environ.get(API_KEY_ENV, "")
    secret = secret.strip() if isinstance(secret, str) else ""
    scanned = 0
    clean = True
    marker_found = False
    for p in paths:
        if not p.is_file():
            continue
        scanned += 1
        try:
            text = p.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
        if any(m in text for m in markers):
            clean = False
            marker_found = True
        if secret and secret.lower() in text:
            clean = False
    return clean, scanned, marker_found


def _print_summary(report: Dict) -> None:
    print("phase:                         %s" % report.get("phase"))
    print("mode:                          %s" % report.get("mode"))
    print("eodhd key present:             %s" % report.get("eodhd_key_present"))
    print("requests made:                 %s" % report.get("requests_made"))
    print("tickers attempted:             %s" % report.get("tickers_attempted"))
    print("eodhd covered count:           %s" % report.get("eodhd_covered_count"))
    print("earnings usable fraction:      %s" % report.get("earnings_usable_fraction"))
    print("earnings PIT-safe:             %s" % report.get("earnings_point_in_time_safe"))
    print("decision:                      %s" % report.get("decision"))
    print("fmp premium fallback recommended: %s" % report.get("fmp_premium_fallback_recommended"))
    print("fmp ultimate rejected:         %s" % report.get("fmp_ultimate_rejected"))
    print("next command:                  %s" % report.get("recommended_next_command"))


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=("Phase 8-R broad earnings/fundamentals bundle evaluation (offline plan by "
                     "default; bounded live evaluation with --live + EODHD_API_KEY)."))
    ap.add_argument("--live", action="store_true",
                    help="Run the bounded live EODHD evaluation (requires EODHD_API_KEY).")
    ap.add_argument("--max-tickers", type=int, default=DEFAULT_MAX_TICKERS,
                    help="Max distinct tickers to probe this run (default 50; not a full backfill).")
    ap.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS,
                    help="Hard cap on live requests this run (default 100).")
    ap.add_argument("--no-skip-existing", action="store_true",
                    help="Re-request tickers already present in the local EODHD cache.")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    run(live=args.live, max_tickers=args.max_tickers, max_requests=args.max_requests,
        skip_existing=not args.no_skip_existing, verbose=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
