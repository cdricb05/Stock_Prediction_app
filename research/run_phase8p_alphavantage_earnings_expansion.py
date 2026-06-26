"""Phase 8-P - Alpha Vantage Earnings Coverage Expansion and Signal Readiness Gate.

WHY THIS PHASE EXISTS
    Phase 8-N ran a LIVE bounded FMP backfill and proved the CURRENT FMP plan is INSUFFICIENT:
    only 8/20 tickers covered on every critical family (the rest 402-subscription-blocked).
    Phase 8-O then selected the cheapest viable provider path: Alpha Vantage FIRST for broad
    earnings actual/estimate/surprise data (free EARNINGS endpoint), Finnhub later only for the
    analyst families; an FMP upgrade is NOT recommended and FMP Ultimate is explicitly rejected.

    Phase 8-P executes exactly that earnings step. Using the user's ALPHAVANTAGE_API_KEY (if
    present in the environment), it runs a CONTROLLED, BOUNDED live probe + collection of the
    Alpha Vantage EARNINGS endpoint for the FMP-blocked tickers first, normalizes the quarterly
    actual/estimate/surprise rows, and answers one question decisively: does Alpha Vantage solve
    the broad earnings-surprise coverage bottleneck that FMP could not? It then gates: if the
    combined FMP + Alpha Vantage coverage reaches the 20-ticker scoring threshold, it emits
    signal-ready earnings manifests; otherwise it says exactly what the next batch / provider is.

SECRET DISCIPLINE (hard rules)
    * ALPHAVANTAGE_API_KEY is read ONLY from the environment, NEVER printed, NEVER written to
      disk. The key-detection report records PRESENCE ONLY (value_read=False).
    * Every persisted URL strips the secret query parameter (apikey) entirely and appends a
      placeholder, so no committed artifact contains "apikey=". A leak scan over the written
      committed artifacts confirms secret_safety_leak_scan_clean.
    * Default run is OFFLINE (no network). A live collection needs BOTH --live AND the key.
      Tests inject a ``transport`` and never touch a key or the network.
    * Raw + normalized Alpha Vantage payloads live ONLY under the gitignored
      research/data/alphavantage/{raw,normalized}/ trees, which this phase force-gitignores
      (belt-and-braces ``*`` + ``!.gitignore``) BEFORE writing, so paid data can never be staged.

HARD CONSTRAINTS HONORED
    Existing installed packages only (stdlib). No package install. No full S&P 500 backfill
    (bounded: max_tickers / max_requests, skip_existing, stop on invalid key / rate limit). No
    raw/normalized provider data committed. No Paper Trader. No GCP. No deploy. No broker / order
    / automation logic. No live trading signals. No commit. No push.

Run (default = OFFLINE: build the plan + decision, no network):
    python research/run_phase8p_alphavantage_earnings_expansion.py
Bounded live collection (requires ALPHAVANTAGE_API_KEY in the env):
    $env:ALPHAVANTAGE_API_KEY = '<PASTE_ALPHAVANTAGE_API_KEY_HERE>'
    python research/run_phase8p_alphavantage_earnings_expansion.py --live
Test (fully offline):
    python -m pytest tests/test_phase8p_alphavantage_earnings_expansion.py -q
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

PHASE = "8-P"
PROVIDER_NAME = "Alpha Vantage"
API_KEY_ENV = "ALPHAVANTAGE_API_KEY"
ALLOWED_HOST = "www.alphavantage.co"

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
_OUT_DIR = (_REPO_ROOT / "research" / "output"
            / "phase8p_alphavantage_earnings_expansion")
# Phase 8-N committed-safe coverage (read-only input: which tickers FMP covered/blocked).
_PHASE8N_DIR = (_REPO_ROOT / "research" / "output"
                / "phase8n_fmp_critical_data_backfill_signal_expansion")
_FMP_COVERAGE_CSV = "fmp_ticker_coverage_by_family.csv"
# Alpha Vantage raw + normalized payloads live ONLY under this gitignored tree.
_DATA_ROOT = _REPO_ROOT / "research" / "data"
_AV_SLUG = "alphavantage"

# The 13 committed-safe artifact basenames.
_ARTIFACTS = {
    "report": "phase8p_alphavantage_earnings_expansion.json",
    "key_detection": "alphavantage_key_detection_report.csv",
    "request_plan": "alphavantage_request_plan.csv",
    "progress": "alphavantage_progress_report.csv",
    "error_report": "alphavantage_error_report.csv",
    "storage_manifest": "alphavantage_storage_manifest.csv",
    "secret_audit": "alphavantage_secret_safety_audit.csv",
    "coverage": "alphavantage_earnings_coverage_report.csv",
    "comparison": "fmp_vs_alphavantage_coverage_comparison.csv",
    "combined": "combined_earnings_coverage_report.csv",
    "signal_manifest": "signal_ready_earnings_manifest.csv",
    "provider_decision": "provider_decision_after_alphavantage.csv",
    "next_plan": "phase8q_next_plan.json",
}


class _Paths:
    def __init__(self, out_dir: Optional[Path] = None, data_dir: Optional[Path] = None,
                 phase8n_dir: Optional[Path] = None):
        self.out = Path(out_dir) if out_dir else _OUT_DIR
        self.data = Path(data_dir) if data_dir else _DATA_ROOT
        self.phase8n = Path(phase8n_dir) if phase8n_dir else _PHASE8N_DIR
        for key, name in _ARTIFACTS.items():
            setattr(self, key, self.out / name)

    @property
    def av_dir(self) -> Path:
        return self.data / _AV_SLUG

    @property
    def raw_dir(self) -> Path:
        return self.av_dir / "raw" / "earnings"

    @property
    def normalized_csv(self) -> Path:
        return self.av_dir / "normalized" / "earnings_quarterly.csv"


# --------------------------------------------------------------------------- #
# Universe priority (from the brief). Alpha Vantage is asked for the FMP-blocked
# tickers FIRST, then promising signal tickers (best-effort), then the tickers FMP
# already collected (cross-check), then a Phase-5C-style large-cap fallback. The
# concrete blocked/covered split is read from the Phase 8-N coverage CSV at run
# time; these lists are the deterministic fallback if that file is unavailable.
# --------------------------------------------------------------------------- #
FMP_BLOCKED_FALLBACK = ("ABT", "ACN", "ADI", "ADP", "AMAT", "AMGN", "AON", "APD", "APH")
FMP_COLLECTED_FALLBACK = ("AAPL", "MSFT", "NVDA", "ABBV", "ADBE", "AMD", "AMZN", "JPM")
# Phase-5C-style large-cap fallback (all Alpha-Vantage-coverable) to fill the plan to the
# scoring threshold when the priority lists do not reach it. NOT an S&P 500 backfill.
PHASE5C_FALLBACK = ("GOOGL", "META", "TSLA", "AVGO", "COST", "PEP", "KO", "CSCO",
                    "INTC", "QCOM", "TXN", "ORCL", "CRM", "NFLX", "MU")

DEFAULT_MIN_TICKERS_TO_SCORE = 20
DEFAULT_MAX_TICKERS = 25
DEFAULT_MAX_REQUESTS = 25
STOP_AFTER_CONSECUTIVE_RATE_LIMITS = 2
STOP_AFTER_CONSECUTIVE_INVALID_KEY = 1  # an invalid key is fatal immediately

# The three earnings-surprise signal families this phase gates (all earnings-only; they do
# NOT require analyst data, so Alpha Vantage alone can make them score-ready).
_SIGNAL_FAMILIES: Tuple[Dict, ...] = (
    {"signal_id": "S8P-EARNSURP-RATES",
     "name": "earnings_surprise x rates_sensitivity", "required_family": "earnings_surprise"},
    {"signal_id": "S8P-EARNSURP-SECTOR",
     "name": "earnings_surprise x sector_leadership", "required_family": "earnings_surprise"},
    {"signal_id": "S8P-EARNSURP-VOL",
     "name": "earnings_surprise x volatility_sensitivity", "required_family": "earnings_surprise"},
)

# --------------------------------------------------------------------------- #
# Decision vocabulary (exactly the values from the brief).
# --------------------------------------------------------------------------- #
DEC_SOLVES = "ALPHAVANTAGE_EARNINGS_SOLVES_COVERAGE"
DEC_READY_SCORING = "READY_FOR_PROVIDER_EXPANDED_SIGNAL_SCORING"
DEC_NEXT_BATCH = "READY_FOR_NEXT_ALPHAVANTAGE_BATCH"
DEC_NEEDS_SECOND = "NEEDS_SECOND_PROVIDER_FOR_ANALYST_DATA"
DEC_BLOCKED_NO_KEY = "BLOCKED_MISSING_ALPHAVANTAGE_KEY"
DEC_RATE_LIMITED = "ALPHAVANTAGE_RATE_LIMITED"
DEC_REJECTED = "ALPHAVANTAGE_REJECTED_INSUFFICIENT_COVERAGE"
DEC_ERROR = "ERROR"
ALLOWED_DECISIONS = (
    DEC_SOLVES, DEC_READY_SCORING, DEC_NEXT_BATCH, DEC_NEEDS_SECOND, DEC_BLOCKED_NO_KEY,
    DEC_RATE_LIMITED, DEC_REJECTED, DEC_ERROR,
)

# Secret query-parameter names stripped from every persisted URL.
_SECRET_PARAMS = {"apikey", "token", "api_token", "api-token"}
REDACTED_KEY_PLACEHOLDER = "<API_KEY_REDACTED>"
_AV_QUERY_URL = "https://www.alphavantage.co/query?function=EARNINGS&symbol={symbol}"
_USER_AGENT = "paper-trader-research-phase8p/1.0"
PROBE_TIMEOUT_SECONDS = 30.0
PROBE_MIN_SLEEP_SECONDS = 0.30


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


# --------------------------------------------------------------------------- #
# Key handling (presence ONLY; the value is never read into any artifact).
# --------------------------------------------------------------------------- #
def key_present(env_var: str = API_KEY_ENV) -> bool:
    """True iff ``env_var`` is set and non-empty. The value is never returned/printed."""
    v = os.environ.get(env_var)
    return isinstance(v, str) and bool(v.strip())


# --------------------------------------------------------------------------- #
# URL redaction (strips every secret query parameter entirely).
# --------------------------------------------------------------------------- #
def redact_url(url: str) -> str:
    """Return a persist-safe URL: every secret query parameter (apikey/token/...) is stripped
    ENTIRELY and a placeholder is appended, so the result never contains ``apikey=`` or a key."""
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


def _redacted_plan_url(symbol: str) -> str:
    return redact_url(_AV_QUERY_URL.replace("{symbol}", symbol) + "&apikey=")


# --------------------------------------------------------------------------- #
# Phase 8-N FMP coverage ingestion (read-only; which tickers FMP covered vs blocked).
# --------------------------------------------------------------------------- #
def read_fmp_earnings_coverage(phase8n_dir: Path) -> Dict:
    """Read the Phase 8-N per-ticker coverage CSV and return the FMP earnings coverage split.

    FMP-covered earnings tickers are those with has_data=='yes' on earnings_surprises (or
    earnings_calendar); FMP-blocked are the critical tickers with has_data=='no'. Falls back to
    the deterministic Phase 8-N lists if the file is unavailable."""
    rows = _read_csv_file(phase8n_dir / _FMP_COVERAGE_CSV)
    covered: List[str] = []
    blocked: List[str] = []
    universe: List[str] = []
    if rows:
        for r in rows:
            fam = (r.get("endpoint_family") or "").strip()
            if fam not in ("earnings_surprises", "earnings_calendar"):
                continue
            tk = (r.get("ticker") or "").strip().upper()
            if not tk:
                continue
            if tk not in universe:
                universe.append(tk)
            has = (r.get("has_data") or "").strip().lower() == "yes"
            if has and tk not in covered:
                covered.append(tk)
            elif not has and tk not in blocked:
                blocked.append(tk)
        # A ticker covered on either earnings family counts as covered (drop from blocked).
        blocked = [t for t in blocked if t not in covered]
        found = True
    else:
        covered = list(FMP_COLLECTED_FALLBACK)
        blocked = list(FMP_BLOCKED_FALLBACK)
        universe = covered + blocked
        found = False
    return {"found": found, "fmp_covered": covered, "fmp_blocked": blocked,
            "fmp_universe": universe}


# --------------------------------------------------------------------------- #
# Best-effort extraction of promising signal tickers from prior phases (S8K/S8L/S8N).
# Never fatal: returns [] if nothing is extractable.
# --------------------------------------------------------------------------- #
def extract_promising_tickers(out_root: Path) -> List[str]:
    """Best-effort scan of a few prior-phase artifacts for promising tickers. The brief asks
    for these 'if extractable'; on any failure this returns []. No network, read-only."""
    candidates: List[str] = []
    probe_files = [
        out_root / "phase8n_fmp_critical_data_backfill_signal_expansion"
        / "best_trade_idea_candidates.csv",
        out_root / "phase8l_data_family_expansion_signal_factory"
        / "promising_alpha_signals.csv",
        out_root / "phase8k_self_expanding_alpha_factory" / "promising_alpha_signals.csv",
    ]
    for fp in probe_files:
        for r in _read_csv_file(fp):
            for key in ("ticker", "symbol", "Ticker", "Symbol"):
                tk = (r.get(key) or "").strip().upper()
                if tk and tk.isalpha() and len(tk) <= 5 and tk not in candidates:
                    candidates.append(tk)
    return candidates


# --------------------------------------------------------------------------- #
# Build the bounded, prioritized request plan.
# --------------------------------------------------------------------------- #
def build_request_plan(fmp: Dict, promising: List[str], already_cached: Sequence[str],
                       max_tickers: int, skip_existing: bool) -> List[Dict]:
    """Build the prioritized Alpha Vantage request plan: FMP-blocked first, then promising,
    then FMP-collected (cross-check), then the Phase-5C fallback. Deduped, skip-existing
    applied, capped at ``max_tickers``."""
    cached = {str(t).strip().upper() for t in already_cached}
    tiers = (
        ("1_fmp_blocked", fmp["fmp_blocked"]),
        ("2_promising_prior_signal", promising),
        ("3_fmp_collected_crosscheck", fmp["fmp_covered"]),
        ("4_phase5c_fallback", PHASE5C_FALLBACK),
    )
    plan: List[Dict] = []
    seen = set()
    for priority, tickers in tiers:
        for tk in tickers:
            tk = str(tk).strip().upper()
            if not tk or tk in seen:
                continue
            seen.add(tk)
            skipped = skip_existing and tk in cached
            plan.append({
                "ticker": tk, "priority": priority,
                "fmp_covered": tk in set(fmp["fmp_covered"]),
                "fmp_blocked": tk in set(fmp["fmp_blocked"]),
                "skip_existing": skipped,
                "request_url_redacted": _redacted_plan_url(tk),
            })
    # Cap the number of tickers we will actually REQUEST (skip-existing ones do not count).
    to_request = [p for p in plan if not p["skip_existing"]]
    for i, p in enumerate(to_request):
        p["will_request"] = i < max_tickers
    for p in plan:
        p.setdefault("will_request", False)
    return plan


# --------------------------------------------------------------------------- #
# Live collection (bounded; only with --live AND key, OR an injected transport).
# --------------------------------------------------------------------------- #
Transport = Callable[[str], object]


class AvError(Exception):
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


def _build_live_url(symbol: str) -> str:
    base = _AV_QUERY_URL.replace("{symbol}", urllib.parse.quote(symbol))
    key = os.environ.get(API_KEY_ENV, "") or ""
    return "%s&apikey=%s" % (base, urllib.parse.quote(key))


def _live_get(symbol: str) -> object:
    """One bounded live GET to the Alpha Vantage EARNINGS endpoint. The key-bearing URL is used
    transiently and never persisted; errors are sanitized so a key cannot leak through them."""
    url = _build_live_url(symbol)
    if _host_of(url) != ALLOWED_HOST:
        raise AvError("refusing non-allowlisted host", error_type="host_blocked")
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_SECONDS) as resp:  # nosec - allowlisted
            raw = resp.read()
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            raise AvError("non-JSON response: %s" % exc, error_type="bad_response")
    except urllib.error.HTTPError as exc:
        raise AvError("provider returned HTTP %s" % getattr(exc, "code", 0),
                      status_code=getattr(exc, "code", None), error_type="http_error")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AvError("network error: %s" % type(exc).__name__, error_type="network_error")


def classify_av_payload(payload) -> Tuple[str, int, str]:
    """Classify an Alpha Vantage EARNINGS payload into (result, quarterly_rows, note).

    result in {ok, empty, rate_limited, invalid_key, bad_response}. Alpha Vantage returns
    rate-limit / informational / error envelopes as a dict with a 'Note' / 'Information' /
    'Error Message' key even on HTTP 200, so a 200 alone is not success."""
    if not isinstance(payload, dict):
        return ("bad_response", 0, "non-dict payload")
    low = {k.lower(): k for k in payload.keys()}
    if "error message" in low:
        return ("bad_response", 0, "provider Error Message envelope (bad symbol/call)")
    for marker in ("note", "information"):
        if marker in low:
            info_text = str(payload.get(low[marker], "")).lower()
            # Rate-limit / throttle signals take PRECEDENCE: the current free-tier rate-limit
            # message mentions "your API key" too, so we must not misread it as an invalid key.
            if ("rate limit" in info_text or "requests per day" in info_text
                    or "requests per minute" in info_text or "premium" in info_text
                    or "thank you for using" in info_text):
                return ("rate_limited", 0,
                        "provider returned a %s envelope (rate-limit/throttle)" % marker)
            # Only a genuine invalid/missing-key message is FATAL.
            if "invalid" in info_text or "missing" in info_text:
                return ("invalid_key", 0, "provider rejected the API key (invalid/missing)")
            # Any other informational envelope is treated as a throttle (safe, non-fatal).
            return ("rate_limited", 0,
                    "provider returned a %s envelope (informational)" % marker)
    quarterly = payload.get("quarterlyEarnings")
    if isinstance(quarterly, list) and quarterly:
        return ("ok", len(quarterly), "quarterly earnings rows present")
    if "symbol" in low and not quarterly:
        return ("empty", 0, "symbol present but no quarterly earnings rows")
    return ("empty", 0, "no quarterly earnings rows")


def _normalize_quarterly(symbol: str, payload: Dict) -> List[Dict]:
    """Flatten Alpha Vantage quarterlyEarnings into point-in-time rows. reportedDate is the
    announcement availability date (point-in-time safe); fiscalDateEnding is the period end."""
    rows: List[Dict] = []
    for q in (payload.get("quarterlyEarnings") or []):
        if not isinstance(q, dict):
            continue
        rows.append({
            "symbol": symbol,
            "fiscal_date_ending": (q.get("fiscalDateEnding") or "").strip(),
            "reported_date": (q.get("reportedDate") or "").strip(),
            "reported_eps": (q.get("reportedEPS") or "").strip(),
            "estimated_eps": (q.get("estimatedEPS") or "").strip(),
            "surprise": (q.get("surprise") or "").strip(),
            "surprise_percentage": (q.get("surprisePercentage") or "").strip(),
            "report_time": (q.get("reportTime") or "").strip(),
        })
    return rows


def collect_alphavantage(plan: List[Dict], live: bool, transport: Optional[Transport],
                         paths: _Paths, max_requests: int,
                         verbose: bool = False) -> Dict:
    """Run the bounded Alpha Vantage EARNINGS collection over the planned tickers.

    Each requested ticker's verbatim payload is cached under the gitignored
    research/data/alphavantage/raw/earnings/ tree and its quarterly rows are appended to the
    gitignored normalized CSV. Honors max_requests, skip_existing, and stops on an invalid key
    or after consecutive rate-limit envelopes. Returns progress + per-ticker normalized data."""
    progress: List[Dict] = []
    errors: List[Dict] = []
    normalized: Dict[str, List[Dict]] = {}
    requests_made = 0
    consecutive_rl = 0
    consecutive_invalid = 0
    stopped = False
    stop_reason = ""
    can_run = (transport is not None) or (bool(live) and key_present())

    targets = [p for p in plan if p.get("will_request") and not p["skip_existing"]]
    if not can_run:
        return {"progress": progress, "errors": errors, "normalized": normalized,
                "requests_made": 0, "stopped": False, "stop_reason": "",
                "rate_limited": False, "invalid_key": False, "executed": False}

    # Force-gitignore the AV data tree BEFORE any write so paid data can never be staged.
    _ensure_provider_gitignore(paths.av_dir)

    for p in targets:
        tk = p["ticker"]
        if requests_made >= max_requests:
            stopped = True
            stop_reason = "max_requests (%d) reached" % max_requests
            break
        redacted = p["request_url_redacted"]
        try:
            if transport is not None:
                payload = transport(tk)
            else:
                payload = _live_get(tk)
            requests_made += 1
        except AvError as exc:
            requests_made += 1
            code = getattr(exc, "status_code", None)
            etype = getattr(exc, "error_type", "error")
            errors.append({"ticker": tk, "status": "HTTP_ERROR",
                           "http_status": "" if code is None else code,
                           "note": "%s (%s)" % (str(exc), etype),
                           "endpoint_redacted": redacted})
            progress.append(_progress_row(tk, p["priority"], "ERROR", 0, redacted, str(exc)))
            if transport is None:
                time.sleep(PROBE_MIN_SLEEP_SECONDS)
            continue

        result, rows, note = classify_av_payload(payload)
        if result == "invalid_key":
            consecutive_invalid += 1
            errors.append({"ticker": tk, "status": "INVALID_KEY", "http_status": 200,
                           "note": note, "endpoint_redacted": redacted})
            progress.append(_progress_row(tk, p["priority"], "INVALID_KEY", 0, redacted, note))
            if consecutive_invalid >= STOP_AFTER_CONSECUTIVE_INVALID_KEY:
                stopped = True
                stop_reason = "invalid API key - stopped immediately"
                break
            continue
        consecutive_invalid = 0
        if result == "rate_limited":
            consecutive_rl += 1
            errors.append({"ticker": tk, "status": "RATE_LIMITED", "http_status": 200,
                           "note": note, "endpoint_redacted": redacted})
            progress.append(_progress_row(tk, p["priority"], "RATE_LIMITED", 0, redacted, note))
            if consecutive_rl >= STOP_AFTER_CONSECUTIVE_RATE_LIMITS:
                stopped = True
                stop_reason = ("rate-limited %d times consecutively (free tier 25 req/day)"
                               % consecutive_rl)
                break
            if transport is None:
                time.sleep(PROBE_MIN_SLEEP_SECONDS)
            continue
        consecutive_rl = 0
        if result == "bad_response":
            errors.append({"ticker": tk, "status": "BAD_RESPONSE", "http_status": 200,
                           "note": note, "endpoint_redacted": redacted})
            progress.append(_progress_row(tk, p["priority"], "BAD_RESPONSE", 0, redacted, note))
            if transport is None:
                time.sleep(PROBE_MIN_SLEEP_SECONDS)
            continue

        # ok or empty -> persist verbatim raw payload + normalized rows.
        _persist_raw(paths, tk, payload)
        nrows = _normalize_quarterly(tk, payload) if result == "ok" else []
        if nrows:
            normalized[tk] = nrows
        status = "OK" if result == "ok" else "EMPTY"
        progress.append(_progress_row(tk, p["priority"], status, len(nrows), redacted, note))
        if verbose:
            print("  collect %-6s -> %s (%d quarters)" % (tk, status, len(nrows)))
        if transport is None:
            time.sleep(PROBE_MIN_SLEEP_SECONDS)

    # Append all normalized rows to the gitignored normalized CSV (single consolidated file).
    if normalized:
        _append_normalized(paths, normalized)

    return {"progress": progress, "errors": errors, "normalized": normalized,
            "requests_made": requests_made, "stopped": stopped, "stop_reason": stop_reason,
            "rate_limited": consecutive_rl >= STOP_AFTER_CONSECUTIVE_RATE_LIMITS,
            "invalid_key": consecutive_invalid >= STOP_AFTER_CONSECUTIVE_INVALID_KEY,
            "executed": True}


def _progress_row(ticker, priority, status, rows, redacted, note) -> Dict:
    return {"ticker": ticker, "priority": priority, "status": status, "quarterly_rows": rows,
            "endpoint_redacted": redacted, "note": note}


def _persist_raw(paths: _Paths, ticker: str, payload) -> None:
    raw_path = paths.raw_dir / ("EARNINGS_%s.json" % ticker)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with open(raw_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")


_NORMALIZED_HEADER = ("symbol", "fiscal_date_ending", "reported_date", "reported_eps",
                      "estimated_eps", "surprise", "surprise_percentage", "report_time")


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
    """Tickers already present in the AV normalized cache (drives skip_existing)."""
    tickers: List[str] = []
    for r in _read_csv_file(paths.normalized_csv):
        tk = (r.get("symbol") or "").strip().upper()
        if tk and tk not in tickers:
            tickers.append(tk)
    return tickers


def _ensure_provider_gitignore(provider_dir: Path) -> None:
    """Force-gitignore the Alpha Vantage local data dir (ignore EVERYTHING except the
    .gitignore itself) so licensed raw/normalized payloads can never be staged. Mirrors
    research/data/fmp/.gitignore."""
    provider_dir.mkdir(parents=True, exist_ok=True)
    gi = provider_dir / ".gitignore"
    if gi.is_file():
        return
    lines = [
        "# Phase 8-P - LOCAL Alpha Vantage data. DO NOT COMMIT.",
        "#",
        "# research/run_phase8p_alphavantage_earnings_expansion.py --live writes Alpha Vantage",
        "# payloads here:",
        "#   raw/        verbatim provider JSON responses",
        "#   normalized/ flattened point-in-time earnings CSVs derived from the responses",
        "#",
        "# These are license-restricted provider responses and must never enter Git. Only",
        "# coverage / decision metadata (row counts, redacted URLs, file paths - never the",
        "# payloads, never the API key) is written to the committed artifacts under",
        "# research/output/phase8p_alphavantage_earnings_expansion/.",
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
# Coverage reports (from the normalized cache + this run's collection).
# --------------------------------------------------------------------------- #
def build_av_coverage(normalized: Dict[str, List[Dict]], paths: _Paths) -> List[Dict]:
    """Per-ticker Alpha Vantage earnings coverage from the union of this run's normalized rows
    and any prior cached rows. Records has_data / row_count / date span / field presence /
    point_in_time_safe (a reportedDate is the announcement availability date)."""
    by_ticker: Dict[str, List[Dict]] = {tk: list(rows) for tk, rows in normalized.items()}
    # Fold in any previously cached rows so coverage reflects the full local cache.
    for r in _read_csv_file(paths.normalized_csv):
        tk = (r.get("symbol") or "").strip().upper()
        if not tk:
            continue
        by_ticker.setdefault(tk, [])
        if not any(x.get("fiscal_date_ending") == r.get("fiscal_date_ending")
                   for x in by_ticker[tk]):
            by_ticker[tk].append({
                "fiscal_date_ending": r.get("fiscal_date_ending", ""),
                "reported_date": r.get("reported_date", ""),
                "reported_eps": r.get("reported_eps", ""),
                "estimated_eps": r.get("estimated_eps", ""),
                "surprise": r.get("surprise", ""),
                "surprise_percentage": r.get("surprise_percentage", ""),
            })
    coverage: List[Dict] = []
    for tk in sorted(by_ticker):
        rows = by_ticker[tk]
        reported_dates = sorted(d for d in (r.get("reported_date") or "" for r in rows) if d)
        has_actual = any((r.get("reported_eps") or "").strip() not in ("", "None") for r in rows)
        has_est = any((r.get("estimated_eps") or "").strip() not in ("", "None") for r in rows)
        has_surprise = any((r.get("surprise") or "").strip() not in ("", "None") for r in rows)
        coverage.append({
            "ticker": tk, "has_data": bool(rows), "row_count": len(rows),
            "first_reported_date": reported_dates[0] if reported_dates else "",
            "last_reported_date": reported_dates[-1] if reported_dates else "",
            "has_actual_eps": has_actual, "has_estimated_eps": has_est,
            "has_surprise": has_surprise,
            "point_in_time_safe": bool(reported_dates),
        })
    return coverage


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
def run(live: bool = False, transport: Optional[Transport] = None,
        out_dir: Optional[Path] = None, data_dir: Optional[Path] = None,
        phase8n_dir: Optional[Path] = None, max_tickers: int = DEFAULT_MAX_TICKERS,
        max_requests: int = DEFAULT_MAX_REQUESTS, skip_existing: bool = True,
        min_tickers_to_score: int = DEFAULT_MIN_TICKERS_TO_SCORE,
        verbose: bool = True) -> Dict:
    """Run Phase 8-P. Default (offline) builds the plan + decision with no network. A live
    bounded collection needs --live AND ALPHAVANTAGE_API_KEY (or an injected ``transport`` in
    tests). Returns the main report dict."""
    P = _Paths(out_dir, data_dir, phase8n_dir)
    P.out.mkdir(parents=True, exist_ok=True)
    error_text = ""

    try:
        present = key_present()
        fmp = read_fmp_earnings_coverage(P.phase8n)
        promising = extract_promising_tickers(P.out.parent)
        cached = _cached_tickers(P)
        plan = build_request_plan(fmp, promising, cached, max_tickers, skip_existing)

        # ----- key detection report (PRESENCE ONLY; value never read) -----
        _write_csv(P.key_detection,
                   ["env_var", "provider", "key_present", "value_read", "note"],
                   [[API_KEY_ENV, PROVIDER_NAME, present, False,
                     "presence-only; value never read, printed, or written to disk"]])

        # ----- request plan -----
        _write_csv(P.request_plan,
                   ["ticker", "priority", "fmp_covered", "fmp_blocked", "skip_existing",
                    "will_request", "request_url_redacted"],
                   [[p["ticker"], p["priority"], p["fmp_covered"], p["fmp_blocked"],
                     p["skip_existing"], p["will_request"], p["request_url_redacted"]]
                    for p in plan])

        # ----- bounded collection (only with --live+key OR an injected transport) -----
        result = collect_alphavantage(plan, live, transport, P, max_requests, verbose)
        executed = result["executed"]

        _write_csv(P.progress,
                   ["ticker", "priority", "status", "quarterly_rows", "endpoint_redacted", "note"],
                   [[r["ticker"], r["priority"], r["status"], r["quarterly_rows"],
                     r["endpoint_redacted"], r["note"]] for r in result["progress"]])
        _write_csv(P.error_report,
                   ["ticker", "status", "http_status", "note", "endpoint_redacted"],
                   [[e["ticker"], e["status"], e["http_status"], e["note"],
                     e["endpoint_redacted"]] for e in result["errors"]])

        # ----- storage manifest (committed-safe: gitignored paths + row counts, NO payloads) -----
        persisted = [r for r in result["progress"] if r["status"] in ("OK", "EMPTY")]
        _write_csv(P.storage_manifest,
                   ["ticker", "raw_file", "normalized_rows", "in_normalized_csv", "gitignored",
                    "committed"],
                   [[r["ticker"],
                     "research/data/alphavantage/raw/earnings/EARNINGS_%s.json" % r["ticker"],
                     r["quarterly_rows"], r["quarterly_rows"] > 0, True, False]
                    for r in persisted])

        # ----- Alpha Vantage earnings coverage report -----
        av_coverage = build_av_coverage(result["normalized"], P)
        av_covered = [c["ticker"] for c in av_coverage if c["has_data"]]
        _write_csv(P.coverage,
                   ["ticker", "has_data", "row_count", "first_reported_date",
                    "last_reported_date", "has_actual_eps", "has_estimated_eps", "has_surprise",
                    "point_in_time_safe"],
                   [[c["ticker"], c["has_data"], c["row_count"], c["first_reported_date"],
                     c["last_reported_date"], c["has_actual_eps"], c["has_estimated_eps"],
                     c["has_surprise"], c["point_in_time_safe"]] for c in av_coverage])

        # ----- FMP vs Alpha Vantage comparison + combined coverage -----
        fmp_set = set(fmp["fmp_covered"])
        av_set = set(av_covered)
        combined = sorted(fmp_set | av_set)
        still_missing = sorted(t for t in fmp["fmp_blocked"] if t not in av_set)
        av_new = sorted(av_set - fmp_set)

        all_tickers = sorted(fmp_set | av_set | set(fmp["fmp_blocked"]))
        _write_csv(P.comparison,
                   ["ticker", "fmp_covered", "alphavantage_covered", "combined_covered",
                    "newly_covered_by_alphavantage"],
                   [[tk, tk in fmp_set, tk in av_set, tk in (fmp_set | av_set),
                     tk in av_set and tk not in fmp_set] for tk in all_tickers])
        _write_csv(P.combined,
                   ["ticker", "source", "fmp_covered", "alphavantage_covered"],
                   [[tk, _source_label(tk in fmp_set, tk in av_set), tk in fmp_set, tk in av_set]
                    for tk in combined])

        combined_count = len(combined)
        threshold_reached = combined_count >= min_tickers_to_score

        # ----- decision -----
        decision, signal_decision, gate_reason = derive_decision(
            present, executed, result, av_new, combined_count, min_tickers_to_score, plan,
            skip_existing)

        # ----- signal-ready earnings manifest (only when the threshold is reached) -----
        if threshold_reached:
            manifest_rows = []
            for sig in _SIGNAL_FAMILIES:
                for tk in combined:
                    manifest_rows.append([
                        sig["signal_id"], sig["name"], sig["required_family"], tk,
                        tk in fmp_set, tk in av_set, "earnings_quarterly (PIT: reported_date)"])
            _write_csv(P.signal_manifest,
                       ["signal_id", "signal_name", "required_family", "ticker", "fmp_source",
                        "alphavantage_source", "data_basis"], manifest_rows)
        else:
            # Still write the (empty-but-headed) manifest so the artifact always exists.
            _write_csv(P.signal_manifest,
                       ["signal_id", "signal_name", "required_family", "ticker", "fmp_source",
                        "alphavantage_source", "data_basis"], [])

        # ----- provider decision after Alpha Vantage -----
        _write_csv(P.provider_decision,
                   ["provider", "role", "env_var", "key_present", "solves_earnings",
                    "still_needed_for", "decision"],
                   [["FMP (current plan)", "earnings (partial) - 8/20 only", "FMP_API_KEY",
                     key_present("FMP_API_KEY"), False, "broad earnings coverage", decision],
                    ["Alpha Vantage", "earnings actual/estimate/surprise (primary)", API_KEY_ENV,
                     present, threshold_reached and bool(av_new), "", decision],
                    ["Finnhub", "analyst estimates/recommendations/price targets (later)",
                     "FINNHUB_API_KEY", key_present("FINNHUB_API_KEY"), False,
                     "analyst families (NOT solved by Alpha Vantage)", DEC_NEEDS_SECOND]])

        # ----- secret leak scan over the committed artifacts written this run -----
        written = [p for p in P.out.glob("*") if p.is_file()]
        leak_clean, scanned, key_in_files = _scan_for_leaks(written)
        _write_csv(P.secret_audit,
                   ["check", "result", "detail"],
                   [["api_key_read_from_env_only", True, "ALPHAVANTAGE_API_KEY via os.environ only"],
                    ["api_key_printed", False, "key value never printed"],
                    ["api_key_written_to_disk", False, "key value never written to any artifact"],
                    ["committed_artifacts_contain_key_query_param", key_in_files,
                     "leak scan over %d committed files" % scanned],
                    ["secret_leak_scan_clean", leak_clean,
                     "no key value or key query param in artifacts"],
                    ["raw_normalized_gitignored", True,
                     "research/data/alphavantage/{raw,normalized}/ force-gitignored before write"]])

        next_cmd = _next_command(decision, present)

        # ----- Phase 8-Q next plan -----
        _write_json(P.next_plan, {
            "phase": "8-Q",
            "title": "Earnings-Surprise Signal Scoring (or next Alpha Vantage batch / Finnhub)",
            "depends_on_decision": decision,
            "signal_readiness_decision": signal_decision,
            "objective": (
                "If READY_FOR_PROVIDER_EXPANDED_SIGNAL_SCORING: score the three earnings-surprise "
                "signal families on the combined FMP + Alpha Vantage point-in-time earnings panel "
                "(no fabricated alpha). If READY_FOR_NEXT_ALPHAVANTAGE_BATCH: collect the next "
                "bounded Alpha Vantage batch (free tier resets daily, 25 req/day). If "
                "NEEDS_SECOND_PROVIDER_FOR_ANALYST_DATA: add Finnhub for the analyst families."),
            "combined_covered_count": combined_count,
            "min_tickers_to_score": min_tickers_to_score,
            "threshold_reached": threshold_reached,
            "still_missing_tickers": still_missing,
            "next_command": next_cmd,
            "do_not_default_to_fmp_ultimate": True,
            "preview_only": True, "orders_enabled": False, "automation_enabled": False,
            "broker_execution_enabled": False, "committed": False,
        })

        # ----- main report -----
        report = {
            "phase": PHASE,
            "objective": ("Use the Alpha Vantage EARNINGS endpoint (key-gated, bounded) to expand "
                          "earnings actual/estimate/surprise coverage beyond the 8 tickers the FMP "
                          "plan could reach, and gate whether combined coverage is score-ready."),
            "provider": PROVIDER_NAME, "provider_host": ALLOWED_HOST,
            "api_key_env_var": API_KEY_ENV, "api_key_present": present, "api_key_logged": False,
            "mode": "live_collection" if executed and transport is None else (
                "test_transport" if executed else "offline_plan"),
            "live_requested": bool(live),
            "bounded_limits": {
                "max_tickers": max_tickers, "max_requests": max_requests,
                "skip_existing": skip_existing,
                "stop_after_consecutive_rate_limits": STOP_AFTER_CONSECUTIVE_RATE_LIMITS,
                "stop_after_consecutive_invalid_key": STOP_AFTER_CONSECUTIVE_INVALID_KEY,
                "no_full_sp500_backfill": True},
            "fmp_coverage_found": fmp["found"],
            "fmp_covered_tickers": sorted(fmp_set),
            "fmp_blocked_tickers": sorted(fmp["fmp_blocked"]),
            "promising_prior_tickers": promising,
            "requests_made": result["requests_made"],
            "collection_executed": executed,
            "collection_stopped_early": result["stopped"],
            "stop_reason": result["stop_reason"],
            "rate_limited": result["rate_limited"],
            "invalid_key": result["invalid_key"],
            "alphavantage_covered_tickers": sorted(av_set),
            "alphavantage_newly_covered_tickers": av_new,
            "combined_covered_tickers": combined,
            "combined_covered_count": combined_count,
            "still_missing_tickers": still_missing,
            "min_tickers_to_score": min_tickers_to_score,
            "scoring_threshold_reached": threshold_reached,
            "decision": decision, "decision_is_terminal": decision in ALLOWED_DECISIONS,
            "allowed_decisions": list(ALLOWED_DECISIONS),
            "signal_readiness_decision": signal_decision,
            "second_provider_needed_for_analyst": True,
            "second_provider": "Finnhub", "second_provider_env_var": "FINNHUB_API_KEY",
            "gate_reason": gate_reason,
            "recommended_next_command": next_cmd,
            "secret_safety_leak_scan_clean": leak_clean,
            "secret_safety_files_scanned": scanned,
            "raw_dir_gitignored": _rel(P.av_dir),
            "outputs": {k: _rel(getattr(P, k)) for k in _ARTIFACTS},
            # Safety / provenance contract.
            "preview_only": True, "orders_enabled": False, "automation_enabled": False,
            "broker_execution_enabled": False, "production_replacement": False,
            "network_used": bool(executed and transport is None),
            "paper_trader_touched": False, "gcp_touched": False, "deployed": False,
            "data_fabricated": False, "no_alpha_fabricated": True,
            "wrote_to_d_drive": False, "committed": False,
        }
        _write_json(P.report, report)
        return report
    except Exception as exc:  # pragma: no cover - defensive; never leaks a key
        error_text = "%s: %s" % (type(exc).__name__, exc)
        report = {
            "phase": PHASE, "decision": DEC_ERROR, "decision_is_terminal": True,
            "allowed_decisions": list(ALLOWED_DECISIONS), "error": error_text,
            "api_key_logged": False, "committed": False, "preview_only": True,
            "orders_enabled": False, "automation_enabled": False,
            "paper_trader_touched": False, "gcp_touched": False, "deployed": False,
        }
        _write_json(P.report, report)
        return report


def _source_label(in_fmp: bool, in_av: bool) -> str:
    if in_fmp and in_av:
        return "fmp+alphavantage"
    if in_fmp:
        return "fmp"
    if in_av:
        return "alphavantage"
    return "none"


def derive_decision(present: bool, executed: bool, result: Dict, av_new: List[str],
                    combined_count: int, min_tickers: int, plan: List[Dict],
                    skip_existing: bool) -> Tuple[str, str, str]:
    """Map the run state to (decision, signal_readiness_decision, gate_reason).

    The primary ``decision`` is one of the eight allowed values; ``signal_readiness_decision``
    is the gating verdict for the three earnings-surprise signal families."""
    threshold_reached = combined_count >= min_tickers
    if not present and not executed:
        return (DEC_BLOCKED_NO_KEY, DEC_BLOCKED_NO_KEY,
                "ALPHAVANTAGE_API_KEY is not set in this environment; set it and re-run --live to "
                "collect. Offline plan written; no network used.")
    if result.get("invalid_key"):
        return (DEC_REJECTED, DEC_REJECTED,
                "Alpha Vantage rejected the API key as invalid; no usable earnings data collected.")
    if result.get("rate_limited") and not av_new:
        return (DEC_RATE_LIMITED, DEC_RATE_LIMITED,
                "Alpha Vantage rate-limited (free tier 25 req/day) before any new coverage; "
                "resume the bounded batch after the daily reset.")
    if threshold_reached:
        # Combined FMP + Alpha Vantage reaches the scoring threshold -> earnings solved & ready.
        return (DEC_SOLVES, DEC_READY_SCORING,
                "Combined FMP + Alpha Vantage earnings coverage (%d) reaches the %d-ticker "
                "scoring threshold; the three earnings-surprise signal families are score-ready."
                % (combined_count, min_tickers))
    # Below threshold: are there more tickers we could still collect?
    requestable = [p for p in plan if not p["skip_existing"]]
    more_available = len(requestable) > result["requests_made"]
    if av_new or more_available:
        return (DEC_NEXT_BATCH, DEC_NEXT_BATCH,
                "Alpha Vantage added %d new ticker(s) but combined coverage (%d) is below the "
                "%d-ticker threshold; collect the next bounded Alpha Vantage batch."
                % (len(av_new), combined_count, min_tickers))
    if executed and not av_new:
        return (DEC_REJECTED, DEC_REJECTED,
                "Alpha Vantage returned no new usable earnings coverage; insufficient to reach the "
                "scoring threshold.")
    return (DEC_NEEDS_SECOND, DEC_NEEDS_SECOND,
            "Earnings coverage still short and no further Alpha Vantage tickers remain; a second "
            "provider (Finnhub) is required for the analyst families regardless.")


def _next_command(decision: str, present: bool) -> str:
    if decision == DEC_BLOCKED_NO_KEY:
        return ("$env:ALPHAVANTAGE_API_KEY = '<PASTE_ALPHAVANTAGE_API_KEY_HERE>'; "
                "python research/run_phase8p_alphavantage_earnings_expansion.py --live")
    if decision in (DEC_RATE_LIMITED, DEC_NEXT_BATCH):
        return ("python research/run_phase8p_alphavantage_earnings_expansion.py --live "
                "--max-tickers 25   (resume after the free-tier daily reset; skip_existing on)")
    if decision in (DEC_SOLVES, DEC_READY_SCORING):
        return ("python research/run_phase8q_earnings_surprise_signal_scoring.py   "
                "(score the three earnings-surprise families on the combined PIT earnings panel)")
    if decision == DEC_NEEDS_SECOND:
        return ("$env:FINNHUB_API_KEY = '<PASTE_FINNHUB_API_KEY_HERE>'; "
                "python research/run_phase8q_finnhub_analyst_expansion.py --live   "
                "(add the analyst families; Alpha Vantage does not serve them)")
    return ("python research/run_phase8p_alphavantage_earnings_expansion.py --live")


# --------------------------------------------------------------------------- #
# Secret leak scan over the committed artifacts written this run.
# --------------------------------------------------------------------------- #
def _scan_for_leaks(paths: Sequence[Path]) -> Tuple[bool, int, bool]:
    marker = "api" + "key="
    secret = os.environ.get(API_KEY_ENV, "")
    secret = secret.strip() if isinstance(secret, str) else ""
    scanned = 0
    clean = True
    apikey_eq_found = False
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
            apikey_eq_found = True
        if secret and secret in text:
            clean = False
    return clean, scanned, apikey_eq_found


# --------------------------------------------------------------------------- #
# CLI.
# --------------------------------------------------------------------------- #
def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=("Phase 8-P Alpha Vantage earnings coverage expansion (offline plan by "
                     "default; bounded live collection with --live + ALPHAVANTAGE_API_KEY)."))
    ap.add_argument("--live", action="store_true",
                    help="Run the bounded live collection (requires ALPHAVANTAGE_API_KEY).")
    ap.add_argument("--max-tickers", type=int, default=DEFAULT_MAX_TICKERS,
                    help="Max tickers to REQUEST this run (default 25; not a full backfill).")
    ap.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS,
                    help="Hard cap on live requests this run (default 25).")
    ap.add_argument("--no-skip-existing", action="store_true",
                    help="Re-request tickers already present in the local AV cache.")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    report = run(live=args.live, max_tickers=args.max_tickers, max_requests=args.max_requests,
                 skip_existing=not args.no_skip_existing, verbose=True)
    print("phase:                         %s" % report.get("phase"))
    print("mode:                          %s" % report.get("mode"))
    print("alphavantage key present:      %s" % report.get("api_key_present"))
    print("requests made:                 %s" % report.get("requests_made"))
    print("fmp covered tickers:           %s" % len(report.get("fmp_covered_tickers", [])))
    print("alphavantage covered tickers:  %s" % len(report.get("alphavantage_covered_tickers", [])))
    print("combined covered count:        %s / %s"
          % (report.get("combined_covered_count"), report.get("min_tickers_to_score")))
    print("still missing tickers:         %s" % ", ".join(report.get("still_missing_tickers", [])))
    print("scoring threshold reached:     %s" % report.get("scoring_threshold_reached"))
    print("decision:                      %s" % report.get("decision"))
    print("signal readiness decision:     %s" % report.get("signal_readiness_decision"))
    print("second provider needed:        %s" % report.get("second_provider_needed_for_analyst"))
    print("next command:                  %s" % report.get("recommended_next_command"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
