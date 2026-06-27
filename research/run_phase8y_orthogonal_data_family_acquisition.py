"""Phase 8-Y - Orthogonal Data Family Acquisition + Strong Alpha Re-run.

WHY THIS PHASE EXISTS
    Phase 8-X proved the EODHD earnings / fundamentals / price families are EXHAUSTED for broad
    strong alpha on the expanded 545-ticker universe (best broad t=2.24; 0 strong, 0 even
    constrained; decision NEEDS_NEW_DATA_FAMILY). The binding constraint is the INFORMATION CONTENT
    of those families, not scenario design or model class. The only honest next move is a genuinely
    NEW data family that is ORTHOGONAL to realized earnings, accounting fundamentals, and historical
    prices.

    Phase 8-Y behaves like an autonomous data-acquisition agent: it confirms the 8-X verdict, loads
    the 545-universe metadata, AUDITS which provider endpoints are reachable with the keys actually
    present in the environment, classifies entitlement per (provider, family), SELECTS the
    highest-value accessible orthogonal family in strict priority order, acquires a BOUNDED batch,
    normalizes it POINT-IN-TIME, builds NEW features (not duplicates of the EODHD earnings /
    fundamentals / price features), and re-runs the Phase 8-X strong-alpha discovery logic on the
    augmented dataset. It never promotes a weak/constrained signal, never touches Paper Trader / GCP /
    orders, never installs packages, never prints or writes an API key, and force-gitignores every raw
    and normalized provider payload.

PRIORITY ORDER (highest value first)
    analyst estimate revisions -> recommendation changes -> price target revisions ->
    short interest -> options IV/skew -> news/social sentiment.

ENTITLEMENT CLASSIFICATION (per provider/family)
    ACCESS_VERIFIED | ENTITLEMENT_BLOCKED | RATE_LIMITED | MISSING_KEY | ENDPOINT_NOT_FOUND |
    PARSE_FAILED. One blocked/missing provider never stops the phase.

TERMINAL DECISIONS
    STRONG_ALPHA_FOUND | NEW_DATA_FAMILY_EXHAUSTED | NEEDS_PROVIDER_PURCHASE |
    READY_FOR_NEXT_ORTHOGONAL_BATCH | PARSER_OR_NORMALIZATION_BLOCKER |
    HARD_BLOCKER_REQUIRES_USER_ACTION | ERROR

    PARSER_OR_NORMALIZATION_BLOCKER is returned when raw provider payloads ARE present but the
    normalizer produced zero point-in-time records - that is a parser/normalizer defect, never a
    data-exhaustion result, so it must NOT be reported as READY_FOR_NEXT_ORTHOGONAL_BATCH.

Run (probes only providers whose key is present; bounded acquire + re-run if a family is accessible):
    python research/run_phase8y_orthogonal_data_family_acquisition.py
Offline decision only (no network; classify keys + emit the purchase/next-batch decision):
    python research/run_phase8y_orthogonal_data_family_acquisition.py --offline
Re-normalize CACHED raw payloads + re-run alpha WITHOUT any provider API call (no key required):
    python -B -m research.run_phase8y_orthogonal_data_family_acquisition \
        --family news_social_sentiment --normalize-existing-only --max-tickers 0 --max-requests 0
Test (fully offline; injected transport + synthetic normalized data; no key, no network):
    python -m pytest tests/test_phase8y_orthogonal_data_family_acquisition.py -q
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Reuse the Phase 8-X discovery machinery (gate, classify, scenario/model evaluation, walk-forward
# ensemble) and, through it, the 8-W/8-T/8-S/8-R scoring + secret stack.
from research import run_phase8x_autonomous_strong_alpha_discovery as x8  # noqa: E402
# Reuse the Phase 8-O provider-probe + entitlement + secret-discipline machinery.
from research import run_phase8o_cheapest_provider_selection as o8  # noqa: E402

w8 = x8.w8
t8 = x8.t8
s8 = x8.s8
r8 = x8.r8

PHASE = "8-Y"

# Reused IO helpers (single source of truth via the 8-S data layer).
_write_json = s8._write_json
_write_csv = s8._write_csv
_read_csv_file = s8._read_csv_file
_read_json = s8._read_json
_round = s8._round
_rel = s8._rel

# --------------------------------------------------------------------------- #
# Paths / inputs.
# --------------------------------------------------------------------------- #
_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase8y_orthogonal_data_family_acquisition"
_PHASE8X_DIR = _REPO_ROOT / "research" / "output" / "phase8x_autonomous_strong_alpha_discovery"
_PHASE8X_REPORT = "phase8x_autonomous_strong_alpha_discovery.json"
_PHASE8V_DIR = x8._PHASE8V_DIR
_PHASE8V_REPORT = x8._PHASE8V_REPORT
_PHASE8W_DIR = x8._PHASE8W_DIR
_EXPANDED_PANEL = x8._EXPANDED_PANEL
# Raw + normalized provider payloads (if acquired) live ONLY under these gitignored trees.
_DATA_ROOT = _REPO_ROOT / "research" / "data"

# The 8-X decision that must hold for this phase to proceed.
REQUIRED_PHASE8X_DECISION = x8.DEC_NEW_DATA  # NEEDS_NEW_DATA_FAMILY

# Acquisition bounds (from the brief).
DEFAULT_MAX_TICKERS = 250
DEFAULT_MAX_REQUESTS = 1000
ACQUIRE_TIMEOUT_SECONDS = 30.0
ACQUIRE_MIN_SLEEP_SECONDS = 0.30

# Strong-alpha standard reused verbatim from Phase 8-X (a NEW family must clear the SAME bar).
STRONG_MIN_TICKERS = x8.STRONG_MIN_TICKERS
STRONG_MIN_EVENTS = x8.STRONG_MIN_EVENTS
STRONG_MIN_IC_T = x8.STRONG_MIN_IC_T
RET_COL = x8.RET_COL

# ---- Entitlement classification vocabulary (per provider/family) ---- #
ENT_VERIFIED = "ACCESS_VERIFIED"
ENT_BLOCKED = "ENTITLEMENT_BLOCKED"
ENT_RATE = "RATE_LIMITED"
ENT_MISSING = "MISSING_KEY"
ENT_NOTFOUND = "ENDPOINT_NOT_FOUND"
ENT_PARSE = "PARSE_FAILED"
ENT_NOT_PROBED = "NOT_PROBED"  # offline mode / not reached (never a terminal state per provider)

# ---- Terminal decisions ---- #
DEC_STRONG = "STRONG_ALPHA_FOUND"
DEC_EXHAUSTED = "NEW_DATA_FAMILY_EXHAUSTED"
DEC_PURCHASE = "NEEDS_PROVIDER_PURCHASE"
DEC_NEXT_BATCH = "READY_FOR_NEXT_ORTHOGONAL_BATCH"
# raw payloads present but normalizer produced 0 PIT records -> a parser/normalizer defect, NOT an
# exhaustion result; never reported as READY_FOR_NEXT_ORTHOGONAL_BATCH.
DEC_PARSER_BLOCKER = "PARSER_OR_NORMALIZATION_BLOCKER"
DEC_BLOCKER = "HARD_BLOCKER_REQUIRES_USER_ACTION"
DEC_ERROR = "ERROR"
ALLOWED_DECISIONS = (DEC_STRONG, DEC_EXHAUSTED, DEC_PURCHASE, DEC_NEXT_BATCH, DEC_PARSER_BLOCKER,
                     DEC_BLOCKER, DEC_ERROR)

# Minimum coverage (tickers with a usable new-family feature on a scored event) to even attempt a
# strong-alpha conclusion on the new family. Below this we ask for the next batch rather than
# declaring the family exhausted.
DEFAULT_MIN_FEATURE_COVERAGE = 200

_ARTIFACTS = {
    "report": "phase8y_orthogonal_data_family_acquisition.json",
    "entitlement_matrix": "provider_entitlement_matrix.csv",
    "selected_family": "selected_data_family_decision.csv",
    "acq_universe": "acquisition_universe.csv",
    "acq_progress": "acquisition_progress.csv",
    "raw_manifest": "raw_storage_manifest.csv",
    "norm_manifest": "normalized_storage_manifest.csv",
    "pit_audit": "point_in_time_normalization_audit.csv",
    "feature_catalog": "new_feature_catalog.csv",
    "aug_scenario_scoreboard": "augmented_scenario_scoreboard.csv",
    "aug_model_scoreboard": "augmented_model_candidate_scoreboard.csv",
    "strong_candidates": "strong_alpha_candidates.csv",
    "rejected": "rejected_augmented_hypotheses.csv",
    "data_exhaustion": "data_family_exhaustion_report.csv",
    "next_plan": "phase8z_next_plan.json",
    "secret_audit": "secret_safety_audit.csv",
}
# The 16 committed-safe artifacts the brief requires (all of them).
_REQUIRED_ARTIFACTS = tuple(_ARTIFACTS.keys())


# --------------------------------------------------------------------------- #
# Orthogonal data-family registry (strict priority order). Each family lists its candidate
# providers cheapest-first; each provider carries a representative endpoint whose secret query
# parameter is named so the redactor strips it. `feature` is the NEW point-in-time feature the
# normalizer produces - none of these duplicate the EODHD earnings/fundamentals/price features.
# --------------------------------------------------------------------------- #
def _ep(provider, env_var, host, url, secret_param, doc):
    return {"provider": provider, "env_var": env_var, "host": host, "url": url,
            "secret_param": secret_param, "doc": doc}


# Provider display names reused from Phase 8-O where they overlap.
PROV_FMP = "FMP"
PROV_FINNHUB = "Finnhub"
PROV_AV = "Alpha Vantage"
PROV_EODHD = "EODHD"

ORTHOGONAL_FAMILIES: Tuple[Dict, ...] = (
    {
        "family": "analyst_estimate_revisions",
        "priority": 1,
        "feature": "est_rev_breadth",
        "feature_desc": "net upward EPS-estimate revision breadth (up-minus-down analyst revisions, "
                        "trailing window) - forward-looking, orthogonal to the realized surprise",
        "orthogonal_to": "realized earnings surprise (this is the PRE-report estimate drift)",
        "cheapest_unlock": "Finnhub free tier (revenue/eps estimates) or FMP Premium",
        "providers": (
            _ep(PROV_FINNHUB, "FINNHUB_API_KEY", "finnhub.io",
                "https://finnhub.io/api/v1/stock/eps-estimate?symbol={symbol}&freq=quarterly",
                "token", "finnhub.io/docs/api : /stock/eps-estimate (analyst EPS estimates)"),
            _ep(PROV_FMP, "FMP_API_KEY", "financialmodelingprep.com",
                "https://financialmodelingprep.com/api/v3/analyst-estimates/{symbol}",
                "apikey", "financialmodelingprep.com : /v3/analyst-estimates (estimate history)"),
        ),
    },
    {
        "family": "analyst_recommendation_changes",
        "priority": 2,
        "feature": "rec_change_net",
        "feature_desc": "net recommendation change (buy-minus-sell consensus shift vs prior month) - "
                        "captures analyst sentiment momentum, orthogonal to accounting fundamentals",
        "orthogonal_to": "accounting fundamentals / realized earnings",
        "cheapest_unlock": "Finnhub free recommendation trends",
        "providers": (
            _ep(PROV_FINNHUB, "FINNHUB_API_KEY", "finnhub.io",
                "https://finnhub.io/api/v1/stock/recommendation?symbol={symbol}",
                "token", "finnhub.io/docs/api : /stock/recommendation (buy/hold/sell trends)"),
            _ep(PROV_FMP, "FMP_API_KEY", "financialmodelingprep.com",
                "https://financialmodelingprep.com/api/v4/upgrades-downgrades?symbol={symbol}",
                "apikey", "financialmodelingprep.com : /v4/upgrades-downgrades"),
        ),
    },
    {
        "family": "price_target_revisions",
        "priority": 3,
        "feature": "pt_revision",
        "feature_desc": "consensus price-target revision vs price (implied upside change) - "
                        "forward-looking, orthogonal to realized price momentum",
        "orthogonal_to": "historical price momentum / reversal",
        "cheapest_unlock": "Finnhub price target (free), FMP price-target consensus",
        "providers": (
            _ep(PROV_FINNHUB, "FINNHUB_API_KEY", "finnhub.io",
                "https://finnhub.io/api/v1/stock/price-target?symbol={symbol}",
                "token", "finnhub.io/docs/api : /stock/price-target (consensus target)"),
            _ep(PROV_FMP, "FMP_API_KEY", "financialmodelingprep.com",
                "https://financialmodelingprep.com/api/v4/price-target?symbol={symbol}",
                "apikey", "financialmodelingprep.com : /v4/price-target"),
        ),
    },
    {
        "family": "short_interest",
        "priority": 4,
        "feature": "short_interest_ratio",
        "feature_desc": "short interest / float (days-to-cover) - crowding + squeeze risk, "
                        "orthogonal to every fundamentals / price feature already in the panel",
        "orthogonal_to": "fundamentals + price (positioning, not value)",
        "cheapest_unlock": "FMP short-interest (premium) or FINRA biweekly (free, needs an adapter)",
        "providers": (
            _ep(PROV_FMP, "FMP_API_KEY", "financialmodelingprep.com",
                "https://financialmodelingprep.com/api/v4/short_interest?symbol={symbol}",
                "apikey", "financialmodelingprep.com : /v4/short_interest"),
        ),
    },
    {
        "family": "options_iv_skew",
        "priority": 5,
        "feature": "iv_skew",
        "feature_desc": "implied-volatility skew (put-minus-call IV / term slope) - forward-looking "
                        "risk repricing, orthogonal to all realized data",
        "orthogonal_to": "all realized earnings/fundamentals/price (forward-looking risk)",
        "cheapest_unlock": "no cheap source - ORATS / CBOE / Polygon options are paid specialist data",
        "providers": (),  # no cheap/free provider maps to options IV in the repo stack
    },
    {
        "family": "news_social_sentiment",
        "priority": 6,
        "feature": "news_sentiment",
        "feature_desc": "aggregated news/social sentiment score (trailing window) - event flow "
                        "between earnings, orthogonal to the quarterly earnings cadence",
        "orthogonal_to": "the quarterly earnings cadence / accounting fundamentals",
        "cheapest_unlock": "Alpha Vantage NEWS_SENTIMENT (free tier) or EODHD sentiment",
        "providers": (
            _ep(PROV_AV, "ALPHAVANTAGE_API_KEY", "www.alphavantage.co",
                "https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers={symbol}",
                "apikey", "alphavantage.co/documentation : NEWS_SENTIMENT (free tier)"),
            _ep(PROV_EODHD, "EODHD_API_KEY", "eodhd.com",
                "https://eodhd.com/api/sentiments?s={symbol}.US&fmt=json",
                "api_token", "eodhd.com/financial-apis : /sentiments"),
        ),
    },
)
_FAMILY_BY_NAME = {f["family"]: f for f in ORTHOGONAL_FAMILIES}
# Every provider env var this phase ever reads (presence only) - for the leak scan + key inventory.
_ALL_ENV_VARS = sorted({p["env_var"] for f in ORTHOGONAL_FAMILIES for p in f["providers"]}
                       | {"EODHD_API_KEY", "FMP_API_KEY", "ALPHAVANTAGE_API_KEY",
                          "FRED_API_KEY", "SIMFIN_API_KEY", "FINNHUB_API_KEY"})


# --------------------------------------------------------------------------- #
# A. Provider probing + entitlement classification.
# --------------------------------------------------------------------------- #
Transport = Callable[[str], object]


def _provider_slug(provider: str) -> str:
    return provider.split()[0].lower()


def classify_entitlement(payload_or_exc, key_present: bool) -> Tuple[str, int, str]:
    """Map a probe outcome to one of the six entitlement states + (rows, note). A present payload is
    routed through the 8-O classifier (handles AV rate-limit envelopes); a ProbeError is mapped by
    HTTP status. Missing key never reaches here (classified MISSING_KEY before probing)."""
    if isinstance(payload_or_exc, o8.ProbeError):
        code = getattr(payload_or_exc, "status_code", None)
        etype = getattr(payload_or_exc, "error_type", "error")
        if code in (401, 402, 403):
            return ENT_BLOCKED, 0, "HTTP %s subscription/entitlement block (%s)" % (code, etype)
        if code in (429,):
            return ENT_RATE, 0, "HTTP 429 rate limited"
        if code in (404,):
            return ENT_NOTFOUND, 0, "HTTP 404 endpoint not found"
        if etype == "bad_response":
            return ENT_PARSE, 0, "non-JSON / unparseable response"
        return ENT_NOTFOUND, 0, "%s (%s)" % (str(payload_or_exc), etype)
    result, rows, note = o8._classify_probe(payload_or_exc)
    if result == "ok":
        return ENT_VERIFIED, rows, note
    if result == "rate_limited_or_info":
        return ENT_RATE, 0, note
    if result == "empty":
        # reachable + parseable but no data for this symbol: treat as verified-but-empty access
        return ENT_VERIFIED, 0, "reachable, empty payload for probe symbol"
    return ENT_PARSE, 0, note


def probe_provider(family: Dict, provider: Dict, key_present: bool, live: bool,
                   transport: Optional[Transport]) -> Dict:
    """Bounded 1-ticker probe of one (family, provider). Never probes without a key (unless a test
    transport is injected). Returns an entitlement row; never raises, never persists a key/keyed URL."""
    env = provider["env_var"]
    redacted = _redacted_endpoint(provider)
    base = {"family": family["family"], "priority": family["priority"], "provider": provider["provider"],
            "env_var": env, "key_present": key_present, "endpoint_redacted": redacted,
            "doc": provider["doc"], "rows": 0}
    if not key_present and transport is None:
        base.update({"entitlement": ENT_MISSING, "probed": False,
                     "note": "no %s in this session - classified MISSING_KEY, not invalid" % env})
        return base
    if transport is None and not live:
        base.update({"entitlement": ENT_NOT_PROBED, "probed": False,
                     "note": "key present but offline mode; re-run without --offline to probe"})
        return base
    try:
        if transport is not None:
            payload = transport(provider["url"].replace("{symbol}", o8.PROBE_TICKER))
        else:
            payload = o8._live_get(_build_url(provider, o8.PROBE_TICKER), provider["host"])
        ent, rows, note = classify_entitlement(payload, key_present)
    except o8.ProbeError as exc:
        ent, rows, note = classify_entitlement(exc, key_present)
    base.update({"entitlement": ent, "probed": True, "rows": rows, "note": note})
    if transport is None:
        time.sleep(ACQUIRE_MIN_SLEEP_SECONDS)
    return base


def _build_url(provider: Dict, symbol: str) -> str:
    """Build the key-bearing request URL transiently (never persisted)."""
    base = provider["url"].replace("{symbol}", symbol)
    key = os.environ.get(provider["env_var"], "") or ""
    sep = "&" if "?" in base else "?"
    return "%s%s%s=%s" % (base, sep, provider["secret_param"], urllib.parse.quote(key))


def _redacted_endpoint(provider: Dict) -> str:
    """Persist-safe endpoint string: place the secret query parameter in the QUERY (correct
    separator for path-style URLs that lack a '?') so redact_url strips it entirely - the result
    never contains a literal ``apikey=`` / ``token=`` / a key value."""
    base = provider["url"].replace("{symbol}", o8.PROBE_TICKER)
    sep = "&" if "?" in base else "?"
    return o8.redact_url("%s%s%s=" % (base, sep, provider["secret_param"]))


def audit_entitlement(live: bool, transport: Optional[Transport],
                      transport_map: Optional[Dict[str, Transport]], log) -> List[Dict]:
    """Probe every (family, provider) in strict priority order; classify entitlement. One blocked or
    missing provider NEVER stops the audit. Returns the full entitlement matrix (list of rows)."""
    rows: List[Dict] = []
    for family in ORTHOGONAL_FAMILIES:
        if not family["providers"]:
            rows.append({"family": family["family"], "priority": family["priority"],
                         "provider": "(none mapped)", "env_var": "", "key_present": False,
                         "entitlement": ENT_NOTFOUND, "probed": False, "rows": 0,
                         "endpoint_redacted": "",
                         "note": "no cheap/free provider maps to this family in the repo stack: %s"
                         % family["cheapest_unlock"], "doc": ""})
            continue
        for provider in family["providers"]:
            present = o8.key_present(provider["env_var"])
            tp = transport
            if transport_map is not None:
                tp = transport_map.get(provider["provider"])
            row = probe_provider(family, provider, present, live, tp)
            rows.append(row)
            log.step("probe", row["entitlement"],
                     "%s / %s -> %s" % (family["family"], provider["provider"], row["entitlement"]))
    return rows


def select_family(entitlement_rows: List[Dict]) -> Tuple[Optional[Dict], Optional[Dict], List[Dict]]:
    """Select the HIGHEST-PRIORITY family that has at least one ACCESS_VERIFIED provider. Returns
    (family_spec, provider_row, selection_rows). Strict priority order: a verified low-priority family
    never wins over a verified higher-priority one."""
    by_family: Dict[str, List[Dict]] = {}
    for r in entitlement_rows:
        by_family.setdefault(r["family"], []).append(r)
    sel_rows: List[Dict] = []
    chosen_family = None
    chosen_provider = None
    for family in ORTHOGONAL_FAMILIES:
        frows = by_family.get(family["family"], [])
        verified = [r for r in frows if r["entitlement"] == ENT_VERIFIED]
        accessible = bool(verified)
        status = "ACCESSIBLE" if accessible else "NOT_ACCESSIBLE"
        if accessible and chosen_family is None:
            chosen_family = family
            # cheapest verified provider = first in the family's declared (cheapest-first) order
            order = {p["provider"]: i for i, p in enumerate(family["providers"])}
            chosen_provider = sorted(verified, key=lambda r: order.get(r["provider"], 99))[0]
            status = "SELECTED"
        best_ent = _best_entitlement([r["entitlement"] for r in frows])
        sel_rows.append({
            "family": family["family"], "priority": family["priority"], "status": status,
            "best_entitlement": best_ent,
            "selected_provider": (chosen_provider["provider"]
                                  if (status == "SELECTED" and chosen_provider) else ""),
            "feature": family["feature"], "orthogonal_to": family["orthogonal_to"],
            "cheapest_unlock": family["cheapest_unlock"]})
    return chosen_family, chosen_provider, sel_rows


def _best_entitlement(states: Sequence[str]) -> str:
    rank = {ENT_VERIFIED: 0, ENT_RATE: 1, ENT_BLOCKED: 2, ENT_NOTFOUND: 3, ENT_PARSE: 3,
            ENT_NOT_PROBED: 4, ENT_MISSING: 5}
    if not states:
        return ENT_MISSING
    return sorted(states, key=lambda s: rank.get(s, 9))[0]


# --------------------------------------------------------------------------- #
# B. Bounded acquisition (only the selected, accessible family/provider).
# --------------------------------------------------------------------------- #
def _ensure_gitignore(provider_dir: Path, provider: str) -> None:
    o8._ensure_provider_gitignore(provider_dir, provider)


def acquire_family(family: Dict, provider: Dict, universe: List[str], max_tickers: int,
                   max_requests: int, skip_existing: bool, live: bool,
                   transport: Optional[Transport], data_root: Path, log) -> Dict:
    """Acquire a BOUNDED batch for the selected family/provider over the expanded universe. Writes
    verbatim payloads to the force-gitignored research/data/<provider>/raw/ tree. Returns progress +
    raw-manifest rows. A test transport drives this fully offline. Never writes a key/keyed URL."""
    slug = _provider_slug(provider["provider"])
    provider_dir = data_root / slug
    _ensure_gitignore(provider_dir, provider["provider"])
    raw_dir = provider_dir / "raw" / family["family"]
    progress: List[Dict] = []
    raw_manifest: List[Dict] = []
    requests_made = 0
    acquired = 0
    targets = list(universe)[:max(0, max_tickers)]
    for sym in targets:
        if requests_made >= max_requests:
            progress.append({"ticker": sym, "status": "STOPPED_MAX_REQUESTS", "rows": 0,
                             "requests_made": requests_made})
            break
        raw_path = raw_dir / ("%s.json" % sym)
        if skip_existing and raw_path.is_file():
            progress.append({"ticker": sym, "status": "SKIPPED_EXISTING", "rows": 0,
                             "requests_made": requests_made})
            continue
        requests_made += 1
        try:
            if transport is not None:
                payload = transport(provider["url"].replace("{symbol}", sym))
            else:
                payload = o8._live_get(_build_url(provider, sym), provider["host"])
            ent, rows, note = classify_entitlement(payload, True)
        except o8.ProbeError as exc:
            ent, rows, note = classify_entitlement(exc, True)
            payload = None
        if ent == ENT_VERIFIED and payload is not None:
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            import json
            with open(raw_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
                fh.write("\n")
            acquired += 1
            raw_manifest.append({"ticker": sym, "family": family["family"],
                                 "raw_path": _rel(raw_path), "rows": rows,
                                 "gitignored": True})
            progress.append({"ticker": sym, "status": "ACQUIRED", "rows": rows,
                             "requests_made": requests_made})
        else:
            progress.append({"ticker": sym, "status": ent, "rows": 0,
                             "requests_made": requests_made})
            if ent in (ENT_BLOCKED, ENT_MISSING):
                # entitlement lost mid-batch: stop spending requests, report what we have
                log.step("acquire", "STOP", "%s mid-batch (%s) - halting acquisition" % (ent, sym))
                break
        if transport is None:
            time.sleep(ACQUIRE_MIN_SLEEP_SECONDS)
    log.step("acquire", "DONE", "%s/%s: acquired %d, %d requests"
             % (family["family"], provider["provider"], acquired, requests_made),
             count=acquired)
    return {"progress": progress, "raw_manifest": raw_manifest, "requests_made": requests_made,
            "tickers_acquired": acquired, "raw_dir": raw_dir, "provider_dir": provider_dir}


# --------------------------------------------------------------------------- #
# C. Point-in-time normalization + feature build.
# --------------------------------------------------------------------------- #
def normalize_pit(family: Dict, raw_dir: Path, provider_dir: Path, as_of: str,
                  log, provider_name: Optional[str] = None) -> Tuple[Path, List[Dict], List[Dict]]:
    """Flatten verbatim raw payloads into a NORMALIZED point-in-time CSV. Every record carries an
    `available_date` = the date the datum was first publicly known; the feature is later as-of joined
    onto an event only if available_date <= entry_date (no lookahead). Writes under the gitignored
    normalized/ tree. Returns (normalized_csv_path, norm_manifest_rows, pit_audit_rows).

    For the news_social_sentiment family the EODHD payload is a top-level dict keyed by provider
    symbol (e.g. ``{"A.US": [{"date":..., "count":..., "normalized":...}, ...]}``); it is flattened
    into the richer schema (ticker, provider_symbol, available_date, news_sentiment, news_count,
    source_family, source_provider). For every other family the generic
    (ticker, available_date, <feature>) schema is used."""
    import json
    norm_dir = provider_dir / "normalized" / family["family"]
    norm_dir.mkdir(parents=True, exist_ok=True)
    feat = family["feature"]
    is_news = family["family"] == "news_social_sentiment"
    out_csv = norm_dir / ("%s.csv" % feat)
    src_provider = provider_name or _provider_display_from_dir(provider_dir)
    records: List[List] = []
    audit: List[Dict] = []
    if raw_dir.is_dir():
        for raw_path in sorted(raw_dir.glob("*.json")):
            sym = raw_path.stem
            try:
                with open(raw_path, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
            except (OSError, json.JSONDecodeError):
                audit.append({"ticker": sym, "status": "PARSE_FAILED", "available_date": "",
                              "value": "", "pit_ok": False})
                continue
            if is_news:
                for r in _extract_news_sentiment_records(payload, sym):
                    avail = r["available_date"]
                    sent = r["news_sentiment"]
                    status, pit_ok = _pit_status(avail, sent, as_of)
                    if pit_ok:
                        cnt = r["news_count"]
                        records.append([r["ticker"], r["provider_symbol"], avail, _round(sent, 6),
                                        _round(cnt, 6) if cnt is not None else "",
                                        family["family"], src_provider])
                    audit.append({"ticker": r["ticker"], "status": status, "available_date": avail,
                                  "value": _round(sent, 6) if sent is not None else "",
                                  "pit_ok": pit_ok})
                continue
            for rec in _extract_records(payload, feat):
                avail = rec.get("available_date", "")
                val = rec.get("value")
                pit_ok = bool(avail)  # a record without a known availability date is NOT PIT-safe
                if pit_ok and as_of and str(avail) > str(as_of):
                    pit_ok = False  # never normalize a datum dated after the as-of (future leak)
                if pit_ok and val is not None:
                    records.append([sym, avail, _round(val, 6)])
                audit.append({"ticker": sym, "status": "OK" if pit_ok else "DROPPED_NO_PIT_DATE",
                              "available_date": avail, "value": _round(val, 6) if val is not None else "",
                              "pit_ok": pit_ok})
    header = (["ticker", "provider_symbol", "available_date", "news_sentiment", "news_count",
               "source_family", "source_provider"] if is_news
              else ["ticker", "available_date", feat])
    _write_csv(out_csv, header, records)
    n_tickers = len({r[0] for r in records})
    norm_manifest = [{"family": family["family"], "feature": feat,
                      "normalized_path": _rel(out_csv), "rows": len(records),
                      "tickers": n_tickers, "gitignored": True}]
    log.step("normalize", "DONE", "%s: %d PIT records / %d tickers"
             % (family["family"], len(records), n_tickers), count=len(records))
    return out_csv, norm_manifest, audit


def _to_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _strip_provider_suffix(provider_symbol: str) -> str:
    """Strip a provider exchange suffix to recover the clean ticker: A.US -> A, AAPL.US -> AAPL,
    BRK-B.US -> BRK-B (only the final dotted suffix is removed). The raw key is preserved separately
    as provider_symbol."""
    ps = str(provider_symbol).strip()
    if "." in ps:
        return ps.rsplit(".", 1)[0]
    return ps


def _pit_status(avail, value, as_of: str) -> Tuple[str, bool]:
    """Classify a single normalized record for point-in-time safety. A record is kept only if it has
    an availability date, that date is not after the as-of (future leak), and it carries a value."""
    if not avail:
        return "DROPPED_NO_PIT_DATE", False
    if as_of and str(avail) > str(as_of):
        return "DROPPED_FUTURE_DATE", False
    if value is None:
        return "DROPPED_NO_VALUE", False
    return "OK", True


def _provider_display_from_dir(provider_dir: Path) -> str:
    slug = provider_dir.name
    for f in ORTHOGONAL_FAMILIES:
        for p in f["providers"]:
            if _provider_slug(p["provider"]) == slug:
                return p["provider"]
    return slug


def _extract_news_sentiment_records(payload, file_stem: str) -> List[Dict]:
    """Flatten a news/social-sentiment payload into PIT-ready records. Handles the EODHD shape
    (top-level dict keyed by provider symbol -> list of daily {date,count,normalized} records), the
    Alpha-Vantage NEWS_SENTIMENT shape ({"feed":[{time_published, overall_sentiment_score}]}), the
    uniform test shape ({"records":[{available_date, value}]}), and a bare list. Maps
    date->available_date, normalized->news_sentiment, count->news_count; preserves the raw symbol key
    as provider_symbol and strips its exchange suffix for ticker."""
    out: List[Dict] = []

    def _emit(provider_symbol, rec):
        if not isinstance(rec, dict):
            return
        date = (rec.get("date") or rec.get("available_date") or rec.get("time_published")
                or rec.get("published") or rec.get("datetime"))
        avail = str(date)[:10] if date else ""
        sent = rec.get("normalized")
        if sent is None:
            sent = rec.get("overall_sentiment_score")
        if sent is None:
            sent = rec.get("sentiment")
        if sent is None:
            sent = rec.get("value")
        cnt = rec.get("count")
        if cnt is None:
            cnt = rec.get("articles_count")
        ps = str(provider_symbol).strip()
        out.append({"provider_symbol": ps, "ticker": _strip_provider_suffix(ps),
                    "available_date": avail, "news_sentiment": _to_float(sent),
                    "news_count": _to_float(cnt)})

    if isinstance(payload, dict):
        wrapped = False
        for key in ("feed", "data", "records"):
            if isinstance(payload.get(key), list):
                for rec in payload[key]:
                    _emit(file_stem, rec)
                wrapped = True
                break
        if not wrapped:
            listish = [(k, v) for k, v in payload.items() if isinstance(v, list)]
            if listish:
                for sym, recs in listish:
                    for rec in recs:
                        _emit(sym, rec)
            else:
                _emit(file_stem, payload)
    elif isinstance(payload, list):
        for rec in payload:
            _emit(file_stem, rec)
    return out


def _extract_records(payload, feat: str) -> List[Dict]:
    """Flatten a provider payload into [{available_date, value}] using common shapes. Tolerant: the
    normalized records carry a generic `value`; the per-provider exact field mapping is intentionally
    permissive (the acquisition adapter writes a uniform {available_date,value} shape in tests, and
    live payloads expose a date + a numeric field)."""
    out: List[Dict] = []

    def _num(d, keys):
        for k in keys:
            if k in d and d[k] is not None:
                try:
                    return float(d[k])
                except (TypeError, ValueError):
                    continue
        return None

    def _date(d, keys):
        for k in keys:
            if k in d and d[k]:
                return str(d[k])[:10]
        return ""

    date_keys = ("available_date", "date", "period", "asOfDate", "updated", "time",
                 "publishedDate", "gradingDate")
    val_keys = ("value", feat, "net", "ratio", "score", "sentiment", "estimateEps",
                "epsAvg", "priceTarget", "shortInterestRatio", "buy", "ticker_sentiment_score")
    if isinstance(payload, dict):
        # uniform test shape: {"records": [{"available_date":..., "value":...}, ...]}
        if isinstance(payload.get("records"), list):
            for rec in payload["records"]:
                if isinstance(rec, dict):
                    out.append({"available_date": _date(rec, date_keys), "value": _num(rec, val_keys)})
            return out
        # AV NEWS_SENTIMENT-style: {"feed": [{"time_published":..., "overall_sentiment_score":...}]}
        for list_key in ("feed", "data", "estimates", "recommendation"):
            if isinstance(payload.get(list_key), list):
                for rec in payload[list_key]:
                    if isinstance(rec, dict):
                        out.append({"available_date": _date(rec, ("time_published",) + date_keys),
                                    "value": _num(rec, ("overall_sentiment_score",) + val_keys)})
                return out
        v = _num(payload, val_keys)
        if v is not None:
            out.append({"available_date": _date(payload, date_keys), "value": v})
    elif isinstance(payload, list):
        for rec in payload:
            if isinstance(rec, dict):
                out.append({"available_date": _date(rec, date_keys), "value": _num(rec, val_keys)})
    return out


def attach_orthogonal_feature(ev, family: Dict, normalized_csv: Path, log):
    """As-of join the NEW point-in-time feature onto the event table by (ticker, entry_date): for each
    event, take the most recent normalized record with available_date <= entry_date. Enforces PIT (no
    lookahead). Returns (ev_with_feature, coverage_count, feature_cols_added)."""
    import numpy as np
    import pandas as pd
    feat = family["feature"]
    ev = ev.copy()
    ev[feat] = np.nan
    norm = _read_csv_file(normalized_csv)
    if not norm:
        log.step("feature", "EMPTY", "%s: no normalized records to attach" % feat)
        return ev, 0, []
    nf = pd.DataFrame(norm)
    nf["available_date"] = pd.to_datetime(nf["available_date"], errors="coerce")
    nf[feat] = pd.to_numeric(nf[feat], errors="coerce")
    nf = nf.dropna(subset=["available_date", feat])
    if nf.empty:
        return ev, 0, []
    by_ticker: Dict[str, "pd.DataFrame"] = {
        tk: g.sort_values("available_date") for tk, g in nf.groupby("ticker")}
    vals = []
    for tk, entry in zip(ev["ticker"].astype(str), ev["entry_date"]):
        g = by_ticker.get(tk)
        if g is None or pd.isna(entry):
            vals.append(np.nan)
            continue
        elig = g[g["available_date"] <= entry]
        vals.append(float(elig[feat].iloc[-1]) if not elig.empty else np.nan)
    ev[feat] = vals
    cols_added = [feat]
    # An interaction with the realized surprise (if present) - still ORTHOGONAL family input, but
    # lets the new family condition the old signal (cross-family interaction).
    if "surprise_pct" in ev.columns:
        zc_new = x8._within_month_z(ev.assign(month=ev["entry_date"].dt.to_period("M")), feat)
        zc_old = x8._within_month_z(ev.assign(month=ev["entry_date"].dt.to_period("M")), "surprise_pct")
        inter = "%s_x_surprise" % feat
        ev[inter] = (zc_new * zc_old).to_numpy()
        cols_added.append(inter)
    coverage = int(ev[feat].notna().sum())
    n_tickers_cov = int(ev.loc[ev[feat].notna(), "ticker"].nunique())
    log.step("feature", "DONE", "%s attached: %d events / %d tickers covered"
             % (feat, coverage, n_tickers_cov), count=coverage)
    return ev, coverage, cols_added


# --------------------------------------------------------------------------- #
# D. Augmented strong-alpha re-run (reuse the Phase 8-X gate verbatim).
# --------------------------------------------------------------------------- #
def augmented_scenarios(family: Dict, feature_cols: List[str]) -> List[Dict]:
    """Scenario specs that test ONLY the new orthogonal feature(s) - raw + within-sector + the
    cross-family interaction. Reuses the 8-X scenario record shape so x8.evaluate_scenario can score
    them through the identical scoring core."""
    specs = []
    feat = family["feature"]
    if feat in feature_cols:
        specs.append({"cycle": 1, "family": "orthogonal", "scenario": "%s_raw" % feat,
                      "signal": feat, "sector_neutral": False, "regime_filter": None,
                      "exploratory": False, "hypothesis": family["feature_desc"]})
        specs.append({"cycle": 1, "family": "orthogonal", "scenario": "%s_sector_neutral" % feat,
                      "signal": feat, "sector_neutral": True, "regime_filter": None,
                      "exploratory": False, "hypothesis": "within-sector " + family["feature_desc"]})
    inter = "%s_x_surprise" % feat
    if inter in feature_cols:
        specs.append({"cycle": 2, "family": "orthogonal", "scenario": inter, "signal": inter,
                      "sector_neutral": True, "regime_filter": None, "exploratory": False,
                      "hypothesis": "new family conditioning the realized surprise (cross-family)"})
    return specs


def augmented_models(family: Dict, feature_cols: List[str]) -> List[Dict]:
    """Walk-forward factor-ensemble model(s) that ADD the new feature to a transparent blend, so the
    new family is tested both standalone (scenarios) and in combination (models)."""
    feat = family["feature"]
    if feat not in feature_cols:
        return []
    base = ["surprise_pct", "sue", "quality_composite", "mom_pre_63"]
    return [{"cycle": 7, "family": "model", "scenario": "orthogonal_blend__ic_weighted",
             "signals": [feat] + base, "weighting": "ic_weighted", "sector_neutral": True,
             "regime_filter": None, "exploratory": False,
             "hypothesis": "walk-forward ic-weighted ensemble adding %s to a 4-factor base" % feat}]


def run_augmented_campaign(ev_aug, family: Dict, feature_cols: List[str], n_tickers: int,
                           n_events: int, min_tickers: int, min_events: int, log) -> Dict:
    """Re-run the Phase 8-X discovery logic on the AUGMENTED dataset: evaluate the new-family
    scenarios + models through x8.evaluate_scenario / x8.evaluate_model, apply x8's BH + STRONG gate +
    3-way classify VERBATIM. Weak/constrained candidates are never promoted as strong."""
    rng = x8._mk_rng()
    work = ev_aug.copy()
    work["month"] = work["entry_date"].dt.to_period("M")
    candidates: List[Dict] = []
    window_rows: List[Dict] = []
    for sc in augmented_scenarios(family, feature_cols):
        cand = x8.evaluate_scenario(work, sc, rng)
        cand["cycle"] = sc["cycle"]
        candidates.append(cand)
    for sp in augmented_models(family, feature_cols):
        cand, wrows = x8.evaluate_model(work, sp, rng)
        cand["cycle"] = sp["cycle"]
        for w in wrows:
            w["model"] = sp["scenario"]
        window_rows.extend(wrows)
        candidates.append(cand)
    x8._finalize_gates(candidates, n_tickers, n_events, min_tickers, min_events)
    strong = [c for c in candidates if c["status"] == "strong"]
    constrained = [c for c in candidates if c["status"] == "constrained"]
    rejected = [c for c in candidates if c["status"] == "rejected"]
    log.step("rerun", "DONE", "augmented: %d scenarios+models, strong=%d constrained=%d"
             % (len(candidates), len(strong), len(constrained)), count=len(candidates))
    return {"candidates": candidates, "window_rows": window_rows, "strong": strong,
            "constrained": constrained, "rejected": rejected}


# --------------------------------------------------------------------------- #
# E. Decision.
# --------------------------------------------------------------------------- #
def derive_decision(precond_ok: bool, selected_family: Optional[Dict], coverage: int,
                    min_coverage: int, camp: Optional[Dict], n_tickers: int, n_events: int,
                    min_tickers: int, min_events: int, raw_count: int = 0,
                    norm_rows: int = 0) -> Tuple[str, str]:
    """The terminal decision. Order: hard blocker (8-X precondition) -> no accessible family
    (purchase) -> strong found -> raw-present-but-zero-normalized (parser/normalizer blocker) ->
    insufficient coverage (next batch) -> exhausted."""
    if not precond_ok:
        return (DEC_BLOCKER,
                "Phase 8-X decision is not %s, so there is no NEEDS_NEW_DATA_FAMILY mandate to act on. "
                "Re-run Phase 8-X first (or restore its output)." % REQUIRED_PHASE8X_DECISION)
    if selected_family is None:
        top = ORTHOGONAL_FAMILIES[0]
        return (DEC_PURCHASE,
                "No accessible orthogonal data family: every mapped provider is MISSING_KEY / "
                "ENTITLEMENT_BLOCKED with the keys present in this environment. Purchase/enable, in "
                "priority order, the cheapest provider for the top-priority family '%s': %s. The "
                "single highest-leverage buy is a Finnhub key (free tier) - it unlocks the top three "
                "priority families (estimate revisions, recommendation changes, price-target "
                "revisions) at once; Alpha Vantage NEWS_SENTIMENT (free) unlocks news/social "
                "sentiment. Options IV/skew has no cheap source and needs a paid specialist feed "
                "(ORATS/CBOE/Polygon) only if the free families are exhausted first."
                % (top["family"], top["cheapest_unlock"]))
    if camp and camp["strong"]:
        names = ", ".join(sorted(c["name"] for c in camp["strong"]))
        return (DEC_STRONG,
                "The new orthogonal family '%s' produced a candidate clearing the FULL Phase-8-X "
                "strong gate (t>=%.1f, both cohorts +, both subperiods +, sector-diversified, "
                "BH-significant, net-of-25bps positive): %s. Carry to out-of-sample preview validation "
                "(still no orders)." % (selected_family["family"], STRONG_MIN_IC_T, names))
    if raw_count > 0 and norm_rows <= 0:
        return (DEC_PARSER_BLOCKER,
                "Raw provider payloads ARE present (%d cached file(s)) for the '%s' family but the "
                "normalizer produced ZERO point-in-time records. That is a parser/normalizer defect, "
                "NOT a data-exhaustion result, so it is explicitly NOT reported as "
                "READY_FOR_NEXT_ORTHOGONAL_BATCH. Fix the normalizer for this payload shape and "
                "re-normalize the cached raw with --normalize-existing-only (no API calls)."
                % (raw_count, selected_family["family"]))
    if coverage < min_coverage or n_tickers < min_tickers or n_events < min_events:
        return (DEC_NEXT_BATCH,
                "The new family '%s' is accessible and a bounded batch was acquired, but feature "
                "coverage (%d events) is below the %d-event floor needed to judge strong alpha. "
                "Acquire the next bounded batch (more tickers / deeper history) before concluding."
                % (selected_family["family"], coverage, min_coverage))
    why_constrained = ""
    if camp and camp["constrained"]:
        why_constrained = (" %d candidate(s) were statistically strong but CONSTRAINED "
                           "(old-cohort-only / single-sector) and are explicitly rejected as not "
                           "good enough." % len(camp["constrained"]))
    return (DEC_EXHAUSTED,
            "The new orthogonal family '%s' was acquired, normalized point-in-time, and re-run "
            "through the full Phase-8-X strong gate with sufficient coverage, but produced NO strong "
            "broad alpha.%s This family is exhausted; move to the next orthogonal family in priority "
            "order." % (selected_family["family"], why_constrained))


def _next_command(decision: str, selected_family: Optional[Dict]) -> Tuple[str, str]:
    if decision == DEC_STRONG:
        return ("Validate the strong candidate out-of-sample in the Paper Trader daily-review cockpit "
                "as a PREVIEW-ONLY ranking signal (manual review, no orders) before any reliance.",
                "Out-of-sample preview validation of the strong alpha")
    if decision == DEC_PURCHASE:
        return ("Set a Finnhub key (free tier) to unlock the top three priority families, then re-run: "
                "$env:FINNHUB_API_KEY = '<PASTE_FINNHUB_KEY_HERE>'; python research/"
                "run_phase8y_orthogonal_data_family_acquisition.py",
                "Enable the cheapest orthogonal provider, then re-run acquisition")
    if decision == DEC_NEXT_BATCH:
        fam = selected_family["family"] if selected_family else "the selected family"
        return ("python research/run_phase8y_orthogonal_data_family_acquisition.py --max-tickers 500 "
                "  (acquire the next bounded batch for %s; skip_existing keeps it incremental)" % fam,
                "Acquire the next bounded orthogonal batch")
    if decision == DEC_EXHAUSTED:
        return ("python research/run_phase8y_orthogonal_data_family_acquisition.py --family <next>  "
                "(advance to the next orthogonal family in priority order)",
                "Advance to the next orthogonal data family")
    if decision == DEC_PARSER_BLOCKER:
        fam = selected_family["family"] if selected_family else "news_social_sentiment"
        return ("python -B -m research.run_phase8y_orthogonal_data_family_acquisition --family %s "
                "--normalize-existing-only --max-tickers 0 --max-requests 0   (re-normalize the "
                "cached raw payloads after fixing the parser; makes no provider API calls)" % fam,
                "Fix the normalizer, then re-normalize cached raw payloads")
    if decision == DEC_BLOCKER:
        return ("python research/run_phase8x_autonomous_strong_alpha_discovery.py   (regenerate the "
                "NEEDS_NEW_DATA_FAMILY mandate first), then re-run this phase.",
                "Clear the hard blocker (8-X precondition), then re-run")
    return ("python research/run_phase8y_orthogonal_data_family_acquisition.py", "Re-run the phase")


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
class _Paths:
    def __init__(self, out_dir=None, phase8x_dir=None, data_dir=None):
        self.out = Path(out_dir) if out_dir else _OUT_DIR
        self.phase8x = Path(phase8x_dir) if phase8x_dir else _PHASE8X_DIR
        self.data = Path(data_dir) if data_dir else _DATA_ROOT
        for key, name in _ARTIFACTS.items():
            setattr(self, key, self.out / name)


def read_phase8x(phase8x_dir: Path) -> Dict:
    rep = _read_json(phase8x_dir / _PHASE8X_REPORT) or {}
    decision = rep.get("decision", "(none read)")
    return {"found": bool(rep), "decision": decision,
            "precond_ok": decision == REQUIRED_PHASE8X_DECISION,
            "recommended_new_data_family": rep.get("recommended_new_data_family"),
            "universe": rep.get("universe", {}) or {}}


def load_universe(phase8v_dir: Path, ev) -> Tuple[List[str], Dict]:
    """The acquisition universe = the expanded 545-name scoreable set (tickers in the event table),
    with the 8-V newly-scoreable cohort flagged. Metadata only - no payloads."""
    v8 = _read_json(phase8v_dir / _PHASE8V_REPORT) or {}
    newly = {str(t).upper() for t in (v8.get("newly_scoreable_tickers") or [])}
    tickers = sorted({str(t).upper() for t in ev["ticker"].unique()}) if ev is not None else []
    meta = {"n_tickers": len(tickers), "newly_scoreable": len(newly & set(tickers))}
    return tickers, {"newly": newly, **meta}


def run(out_dir: Optional[Path] = None, phase8x_dir: Optional[Path] = None,
        phase8v_dir: Optional[Path] = None, phase8w_dir: Optional[Path] = None,
        price_csv: Optional[Path] = None, data_dir: Optional[Path] = None,
        sector_csv: Optional[Path] = None, macro: Optional[Dict[str, Path]] = None,
        phase8n_dir: Optional[Path] = None, phase8r_dir: Optional[Path] = None,
        as_of: str = s8.DEFAULT_AS_OF, max_tickers: int = DEFAULT_MAX_TICKERS,
        max_requests: int = DEFAULT_MAX_REQUESTS, skip_existing: bool = True,
        min_coverage: int = DEFAULT_MIN_FEATURE_COVERAGE, min_tickers: int = STRONG_MIN_TICKERS,
        min_events: int = STRONG_MIN_EVENTS, family: Optional[str] = None, live: bool = True,
        offline: bool = False, normalize_existing_only: bool = False,
        transport: Optional[Transport] = None,
        transport_map: Optional[Dict[str, Transport]] = None, verbose: bool = True) -> Dict:
    """Run Phase 8-Y. By default probes only providers whose key is present; with --offline it emits
    the purchase/next-batch decision without any network. With --normalize-existing-only it skips all
    probing AND acquisition, re-normalizes the cached raw payloads from disk, attaches features, and
    re-runs the augmented strong-alpha test - making NO provider API call and requiring no key. Tests
    drive the acquire/re-run path fully offline via an injected ``transport``/``transport_map``."""
    P = _Paths(out_dir, phase8x_dir, data_dir)
    P.out.mkdir(parents=True, exist_ok=True)
    log = t8._Log(verbose)
    probe_live = bool(live and not offline and not normalize_existing_only)
    try:
        import pandas as pd  # noqa: F401

        # 1. Read Phase 8-X output + confirm the NEEDS_NEW_DATA_FAMILY mandate.
        x = read_phase8x(P.phase8x)
        log.step("read_8x", "DONE" if x["precond_ok"] else "BLOCK",
                 "8-X decision = %s (precondition %s)"
                 % (x["decision"], "met" if x["precond_ok"] else "NOT met"))

        # 2. Probe provider entitlements (priority order). One blocked/missing provider never stops.
        #    In --normalize-existing-only mode no transport is used at all (no provider call).
        audit_tp = None if normalize_existing_only else transport
        audit_tmap = None if normalize_existing_only else transport_map
        ent_rows = audit_entitlement(probe_live, audit_tp, audit_tmap, log)
        chosen_family, chosen_provider, sel_rows = select_family(ent_rows)
        if family is not None:  # explicit family override (still must be accessible to acquire)
            forced = _FAMILY_BY_NAME.get(family)
            if forced is not None:
                verified = [r for r in ent_rows
                            if r["family"] == family and r["entitlement"] == ENT_VERIFIED]
                chosen_family = forced if verified else (chosen_family if not verified else forced)
                if verified:
                    chosen_provider = verified[0]
        # --normalize-existing-only: force the family/provider from the CACHED raw on disk (no probe).
        cached_raw_dir: Optional[Path] = None
        if normalize_existing_only:
            chosen_family, cached_prov, cached_raw_dir = _find_cached_family_and_provider(
                P.data, family)
            chosen_provider = ({"provider": cached_prov["provider"], "entitlement": "CACHED_RAW"}
                               if cached_prov else None)

        # 3. Build the expanded event table (same gitignored 8-V panel + earnings cache as 8-X/8-W).
        ev = None
        n_tickers = n_events = 0
        if x["precond_ok"]:
            panel = Path(price_csv) if price_csv else _EXPANDED_PANEL
            P_score = s8._Paths(out_dir=P.out, data_dir=data_dir, phase8n_dir=phase8n_dir,
                                price_csv=panel, sector_csv=sector_csv, macro=macro,
                                phase8r_dir=phase8r_dir)
            ev, stats, _audit = w8.build_expanded_ev(P_score, as_of, log)
            if not getattr(ev, "empty", True) and stats.get("events_usable", 0) > 0:
                v8 = _read_json((Path(phase8v_dir) if phase8v_dir else _PHASE8V_DIR) / _PHASE8V_REPORT) or {}
                new_set = {str(t).upper() for t in (v8.get("newly_scoreable_tickers") or [])}
                ev = w8.tag_cohort(ev, new_set)
                _eod_norm = Path(P_score.eodhd_dir) / "normalized" / "eod_prices"
                ev = w8.attach_liquidity_proxy(ev, panel, extra_csvs=[s8._PRICE_CACHE_CSV],
                                               eod_norm_dir=_eod_norm)
                n_tickers = stats.get("tickers_usable", 0) or int(ev["ticker"].nunique())
                n_events = stats.get("events_usable", 0) or int(len(ev))
            else:
                ev = None
        universe, uni_meta = load_universe(Path(phase8v_dir) if phase8v_dir else _PHASE8V_DIR, ev)
        log.step("universe", "DONE", "%d scoreable tickers (newly-scoreable %d)"
                 % (uni_meta["n_tickers"], uni_meta["newly_scoreable"]), count=uni_meta["n_tickers"])

        # 4. Acquire + normalize + feature-build + re-run discovery (only if a family is accessible
        #    AND the precondition + event table are present).
        acq = {"progress": [], "raw_manifest": [], "requests_made": 0, "tickers_acquired": 0}
        norm_manifest: List[Dict] = []
        pit_audit: List[Dict] = []
        feature_cols: List[Dict] = []
        coverage = 0
        raw_count = 0
        norm_rows = 0
        norm_tickers = 0
        camp: Optional[Dict] = None
        ev_aug = ev
        accessible = chosen_family is not None and chosen_provider is not None
        if x["precond_ok"] and ev is not None and accessible:
            provider_name = chosen_provider["provider"]
            if normalize_existing_only:
                provider_dir = P.data / _provider_slug(provider_name)
                raw_dir = cached_raw_dir or (provider_dir / "raw" / chosen_family["family"])
                _ensure_gitignore(provider_dir, provider_name)
                acq = {"progress": [], "raw_manifest": _existing_raw_manifest(raw_dir, chosen_family),
                       "requests_made": 0, "tickers_acquired": 0,
                       "raw_dir": raw_dir, "provider_dir": provider_dir}
            else:
                acq = acquire_family(chosen_family, _provider_spec(chosen_family, provider_name),
                                     universe, max_tickers, max_requests, skip_existing,
                                     probe_live, transport, P.data, log)
            raw_dir = acq["raw_dir"]
            raw_count = len(list(raw_dir.glob("*.json"))) if raw_dir.is_dir() else 0
            norm_csv, norm_manifest, pit_audit = normalize_pit(
                chosen_family, raw_dir, acq["provider_dir"], as_of, log, provider_name=provider_name)
            norm_rows = norm_manifest[0]["rows"] if norm_manifest else 0
            norm_tickers = norm_manifest[0]["tickers"] if norm_manifest else 0
            ev_aug, coverage, cols_added = attach_orthogonal_feature(ev, chosen_family, norm_csv, log)
            feature_cols = cols_added
            if cols_added:
                camp = run_augmented_campaign(ev_aug, chosen_family, cols_added, n_tickers, n_events,
                                              min_tickers, min_events, log)

        # 5. Decision.
        decision, rationale = derive_decision(
            x["precond_ok"], chosen_family if accessible else None, coverage, min_coverage,
            camp, n_tickers, n_events, min_tickers, min_events, raw_count=raw_count,
            norm_rows=norm_rows)
        log.step("decision", "DONE", decision)

        # 6. Write artifacts.
        _write_all_artifacts(P, x, ent_rows, sel_rows, universe, uni_meta, acq, norm_manifest,
                             pit_audit, chosen_family, feature_cols, coverage, camp, decision,
                             rationale, n_tickers, n_events, as_of, max_tickers, max_requests,
                             probe_live, transport, log)

        report = _build_report(P, x, ent_rows, chosen_family, chosen_provider, accessible, universe,
                               uni_meta, acq, norm_manifest, feature_cols, coverage, camp, decision,
                               rationale, n_tickers, n_events, as_of, max_tickers, max_requests,
                               min_coverage, probe_live, transport, raw_count, norm_rows, norm_tickers,
                               normalize_existing_only)
        _write_json(P.report, report)
        if verbose:
            _print_summary(report)
        return report
    except Exception as exc:  # pragma: no cover - defensive; never leaks a key
        report = {"phase": PHASE, "decision": DEC_ERROR, "decision_is_terminal": True,
                  "allowed_decisions": list(ALLOWED_DECISIONS),
                  "error": "%s: %s" % (type(exc).__name__, exc),
                  "committed": False, "preview_only": True, "orders_enabled": False,
                  "automation_enabled": False, "paper_trader_touched": False, "gcp_touched": False,
                  "api_key_printed": False, "api_key_written": False}
        try:
            _write_json(P.report, report)
        except Exception:
            pass
        if verbose:
            print("ERROR: %s" % report["error"])
        return report


def _provider_spec(family: Dict, provider_name: str) -> Dict:
    for p in family["providers"]:
        if p["provider"] == provider_name:
            return p
    return family["providers"][0]


def _find_cached_family_and_provider(data_root: Path, prefer_family: Optional[str]
                                     ) -> Tuple[Optional[Dict], Optional[Dict], Optional[Path]]:
    """For --normalize-existing-only: locate a family/provider whose raw payloads are ALREADY cached
    on disk (no network). Honors an explicit --family preference first, then any priority-order family
    with cached raw. Falls back to the preferred family's last (e.g. EODHD) provider so the decision
    can still report 'raw missing' honestly when nothing is cached."""
    fams = list(ORTHOGONAL_FAMILIES)
    if prefer_family and prefer_family in _FAMILY_BY_NAME:
        pf = _FAMILY_BY_NAME[prefer_family]
        fams = [pf] + [f for f in ORTHOGONAL_FAMILIES if f["family"] != prefer_family]
    for fam in fams:
        for prov in fam["providers"]:
            raw_dir = data_root / _provider_slug(prov["provider"]) / "raw" / fam["family"]
            if raw_dir.is_dir() and any(raw_dir.glob("*.json")):
                return fam, prov, raw_dir
    if prefer_family and prefer_family in _FAMILY_BY_NAME:
        pf = _FAMILY_BY_NAME[prefer_family]
        if pf["providers"]:
            prov = pf["providers"][-1]
            raw_dir = data_root / _provider_slug(prov["provider"]) / "raw" / pf["family"]
            return pf, prov, raw_dir
    return None, None, None


def _existing_raw_manifest(raw_dir: Optional[Path], family: Dict) -> List[Dict]:
    rows: List[Dict] = []
    if raw_dir and raw_dir.is_dir():
        for p in sorted(raw_dir.glob("*.json")):
            rows.append({"ticker": p.stem, "family": family["family"], "raw_path": _rel(p),
                         "rows": "", "gitignored": True})
    return rows


# --------------------------------------------------------------------------- #
# Artifact writers.
# --------------------------------------------------------------------------- #
def _write_all_artifacts(P, x, ent_rows, sel_rows, universe, uni_meta, acq, norm_manifest,
                         pit_audit, chosen_family, feature_cols, coverage, camp, decision,
                         rationale, n_tickers, n_events, as_of, max_tickers, max_requests,
                         live, transport, log):
    _write_csv(P.entitlement_matrix,
               ["family", "priority", "provider", "env_var", "key_present", "probed", "entitlement",
                "rows", "endpoint_redacted", "note"],
               [[r["family"], r["priority"], r["provider"], r["env_var"], r["key_present"],
                 r.get("probed", False), r["entitlement"], r.get("rows", 0),
                 r.get("endpoint_redacted", ""), r.get("note", "")] for r in ent_rows])

    _write_csv(P.selected_family,
               ["family", "priority", "status", "best_entitlement", "selected_provider", "feature",
                "orthogonal_to", "cheapest_unlock"],
               [[r["family"], r["priority"], r["status"], r["best_entitlement"],
                 r["selected_provider"], r["feature"], r["orthogonal_to"], r["cheapest_unlock"]]
                for r in sel_rows])

    _write_csv(P.acq_universe, ["ticker", "newly_scoreable_from_8v"],
               [[t, t in uni_meta.get("newly", set())] for t in universe])

    _write_csv(P.acq_progress, ["ticker", "status", "rows", "requests_made"],
               [[r["ticker"], r["status"], r["rows"], r["requests_made"]] for r in acq["progress"]])

    _write_csv(P.raw_manifest, ["ticker", "family", "raw_path", "rows", "gitignored"],
               [[r["ticker"], r["family"], r["raw_path"], r["rows"], r["gitignored"]]
                for r in acq["raw_manifest"]])

    _write_csv(P.norm_manifest, ["family", "feature", "normalized_path", "rows", "tickers", "gitignored"],
               [[r["family"], r["feature"], r["normalized_path"], r["rows"], r["tickers"],
                 r["gitignored"]] for r in norm_manifest])

    _write_csv(P.pit_audit, ["ticker", "status", "available_date", "value", "pit_ok"],
               [[r["ticker"], r["status"], r["available_date"], r["value"], r["pit_ok"]]
                for r in pit_audit])

    feat_rows = []
    if chosen_family and feature_cols:
        for col in feature_cols:
            is_inter = col.endswith("_x_surprise")
            feat_rows.append([col, chosen_family["family"],
                              "cross-family interaction (new x realized surprise)" if is_inter
                              else chosen_family["feature_desc"],
                              chosen_family["orthogonal_to"],
                              "NO (orthogonal to EODHD earnings/fundamentals/price)", coverage])
    _write_csv(P.feature_catalog,
               ["feature", "family", "description", "orthogonal_to", "duplicates_existing_eodhd",
                "events_covered"], feat_rows)

    scen = [c for c in (camp["candidates"] if camp else []) if c["kind"] == "scenario"]
    models = [c for c in (camp["candidates"] if camp else []) if c["kind"] == "model"]
    _write_csv(P.aug_scenario_scoreboard, x8._CAND_HDR, [x8._cand_row_common(c) for c in scen])
    _write_csv(P.aug_model_scoreboard, x8._CAND_HDR, [x8._cand_row_common(c) for c in models])

    strong = camp["strong"] if camp else []
    rejected = (camp["rejected"] + camp["constrained"]) if camp else []
    _write_csv(P.strong_candidates, x8._CAND_HDR + ["all_strong_gate_checks_passed"],
               [x8._cand_row_common(c) + [True] for c in strong])
    _write_csv(P.rejected, ["name", "kind", "family", "ic_t", "bh_significant", "status",
                            "reject_reasons"],
               [[c["name"], c["kind"], c["family"], x8._g(c["metrics"], "ic_t", 2),
                 c.get("bh_significant", False), c["status"], "; ".join(c.get("strong_reasons", []))]
                for c in rejected])

    # data-family exhaustion (this phase's NEW family + a pointer to the exhausted EODHD families).
    exh_rows = []
    if chosen_family:
        best_t = None
        for c in (camp["candidates"] if camp else []):
            t = c["metrics"].get("ic_t")
            if t is not None and not (isinstance(t, float) and _isnan(t)):
                best_t = t if best_t is None else max(best_t, t)
        exh_rows.append([chosen_family["family"], "new_orthogonal", len(camp["candidates"]) if camp else 0,
                         _round(best_t, 2) if best_t is not None else "",
                         bool(strong), coverage,
                         bool(camp and not strong and coverage >= DEFAULT_MIN_FEATURE_COVERAGE)])
    exh_rows.append(["eodhd_earnings_fundamentals_price", "prior_8X", "", 2.24, False, n_events, True])
    _write_csv(P.data_exhaustion,
               ["data_family", "family_type", "hypotheses_tested", "best_ic_t",
                "any_strong_broad_alpha", "events_or_coverage", "family_exhausted_no_strong_alpha"],
               exh_rows)

    next_cmd, next_title = _next_command(decision, chosen_family)
    _write_json(P.next_plan, {
        "phase": "8-Z", "title": next_title, "depends_on_decision": decision,
        "next_command": next_cmd,
        "selected_data_family": chosen_family["family"] if chosen_family else None,
        "recommended_provider_purchase": (None if decision != DEC_PURCHASE
                                          else ORTHOGONAL_FAMILIES[0]["cheapest_unlock"]),
        "preview_only": True, "orders_enabled": False, "automation_enabled": False,
        "broker_execution_enabled": False, "paper_trader_touched": False, "committed": False})

    # secret/safety audit over everything written so far.
    written = [p for p in P.out.glob("*") if p.is_file()]
    leak_clean, scanned = _scan_for_leaks(written)
    _write_csv(P.secret_audit, ["check", "result", "detail"],
               [["api_key_printed", False, "keys read for presence/probe only; value never printed"],
                ["api_key_written_to_disk", False, "no key value written to any artifact"],
                ["raw_payloads_gitignored", True, "research/data/<provider>/ force-gitignored (raw/)"],
                ["normalized_payloads_gitignored", True,
                 "research/data/<provider>/ force-gitignored (normalized/)"],
                ["network_used", bool(live and transport is None and any(r.get("probed")
                                                                         for r in ent_rows)),
                 "probes only providers whose key is present; offline by --offline"],
                ["committed_artifacts_contain_key_query_param", not leak_clean,
                 "leak scan over %d committed files" % scanned],
                ["secret_leak_scan_clean", leak_clean, "no key value or key query param present"]])
    log.step("artifacts", "DONE", "wrote %d required artifacts" % len(_REQUIRED_ARTIFACTS))


def _isnan(x) -> bool:
    import math
    try:
        return math.isnan(x)
    except (TypeError, ValueError):
        return False


def _scan_for_leaks(paths: Sequence[Path]) -> Tuple[bool, int]:
    """Leak scan over committed artifacts: no key query param and no present key VALUE may appear.
    Reuses the 8-O provider env list plus this phase's full env-var set."""
    import os as _os
    markers = ("api" + "key=", "api_token=", "token=")
    secrets = [_os.environ.get(e, "") for e in _ALL_ENV_VARS]
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
        low = text.lower()
        if any(m in low for m in markers):
            clean = False
        if any(s and s in text for s in secrets):
            clean = False
    return clean, scanned


def _build_report(P, x, ent_rows, chosen_family, chosen_provider, accessible, universe, uni_meta,
                  acq, norm_manifest, feature_cols, coverage, camp, decision, rationale, n_tickers,
                  n_events, as_of, max_tickers, max_requests, min_coverage, live, transport,
                  raw_count=0, norm_rows=0, norm_tickers=0, normalize_existing_only=False):
    written = [p for p in P.out.glob("*") if p.is_file()]
    leak_clean, scanned = _scan_for_leaks(written)
    providers_audited = sorted({r["provider"] for r in ent_rows if r["provider"] != "(none mapped)"})
    access_status = {r["family"]: r["entitlement"] for r in ent_rows}
    best = x8._best_candidate(camp["candidates"]) if camp else None
    next_cmd, _ = _next_command(decision, chosen_family)
    return {
        "phase": PHASE,
        "objective": ("Acquire a NEW data family orthogonal to realized earnings / accounting "
                      "fundamentals / historical prices, normalize it point-in-time, build new "
                      "features, and re-run the Phase-8-X strong-alpha discovery on the augmented "
                      "dataset. Preview-only; no orders; constrained/weak signals never promoted; "
                      "raw + normalized provider payloads force-gitignored; keys never printed/written."),
        "builds_on_phase8x_decision": x["decision"],
        "phase8x_precondition_met": x["precond_ok"],
        "as_of": as_of,
        "providers_audited": providers_audited,
        "provider_entitlement_by_family": access_status,
        "priority_order": [f["family"] for f in ORTHOGONAL_FAMILIES],
        "selected_data_family": chosen_family["family"] if (accessible and chosen_family) else None,
        "selected_provider": chosen_provider["provider"] if (accessible and chosen_provider) else None,
        "access_status": (chosen_provider["entitlement"] if (accessible and chosen_provider)
                          else "NONE_ACCESSIBLE"),
        "universe": {"scoreable_tickers": uni_meta["n_tickers"],
                     "newly_scoreable_from_8v": uni_meta["newly_scoreable"],
                     "usable_events": n_events,
                     "meets_strong_universe_floor": bool(n_tickers >= STRONG_MIN_TICKERS
                                                         and n_events >= STRONG_MIN_EVENTS)},
        "acquisition": {"tickers_acquired": acq["tickers_acquired"],
                        "requests_made": acq["requests_made"],
                        "max_tickers": max_tickers, "max_requests": max_requests,
                        "skip_existing": True},
        "features_added": [c for c in feature_cols],
        "feature_coverage_events": coverage, "min_feature_coverage": min_coverage,
        "raw_payloads_present": raw_count,
        "normalized_rows": norm_rows, "normalized_tickers": norm_tickers,
        "normalize_existing_only": bool(normalize_existing_only),
        "parser_or_normalization_blocker": decision == DEC_PARSER_BLOCKER,
        "parser_bug_fixed": not (raw_count > 0 and norm_rows <= 0),
        "another_data_family_needed": decision == DEC_EXHAUSTED,
        "augmented_scenarios_tested": len([c for c in (camp["candidates"] if camp else [])
                                           if c["kind"] == "scenario"]),
        "augmented_models_tested": len([c for c in (camp["candidates"] if camp else [])
                                        if c["kind"] == "model"]),
        "strong_alpha_candidates": sorted(c["name"] for c in (camp["strong"] if camp else [])),
        "strong_alpha_found": bool(camp and camp["strong"]),
        "constrained_not_good_enough": sorted(c["name"] for c in (camp["constrained"] if camp else [])),
        "best_candidate": (best or {}).get("name") if best else None,
        "best_candidate_ic": x8._g((best or {}).get("metrics", {}), "mean_ic") if best else None,
        "best_candidate_ic_t": x8._g((best or {}).get("metrics", {}), "ic_t", 2) if best else None,
        "best_candidate_status": (best or {}).get("status") if best else None,
        "another_batch_needed": decision == DEC_NEXT_BATCH,
        "provider_purchase_needed": decision == DEC_PURCHASE,
        "recommended_provider_purchase": (ORTHOGONAL_FAMILIES[0]["cheapest_unlock"]
                                          if decision == DEC_PURCHASE else None),
        "decision": decision, "decision_is_terminal": True,
        "allowed_decisions": list(ALLOWED_DECISIONS), "decision_rationale": rationale,
        "recommended_next_command": next_cmd,
        "secret_safety_leak_scan_clean": leak_clean, "secret_safety_files_scanned": scanned,
        "outputs": {k: _rel(getattr(P, k)) for k in _ARTIFACTS},
        "required_artifacts": [_ARTIFACTS[k] for k in _REQUIRED_ARTIFACTS],
        # safety / provenance contract
        "preview_only": True, "orders_enabled": False, "automation_enabled": False,
        "broker_execution_enabled": False, "production_replacement": False,
        "network_used": bool(live and transport is None and any(r.get("probed") for r in ent_rows)),
        "api_key_printed": False, "api_key_written_to_disk": False,
        "raw_payloads_gitignored": True, "normalized_payloads_gitignored": True,
        "constrained_signal_productized": False, "paper_trader_touched": False, "gcp_touched": False,
        "deployed": False, "full_regression_invoked": False, "data_fabricated": False,
        "alpha_fabricated": False, "packages_installed": False, "committed": False,
    }


def _print_summary(report: Dict) -> None:
    print("phase:                 %s" % report.get("phase"))
    print("builds on 8-X:         %s (precondition met=%s)"
          % (report.get("builds_on_phase8x_decision"), report.get("phase8x_precondition_met")))
    print("providers audited:     %s" % ", ".join(report.get("providers_audited", [])))
    print("entitlement by family: %s" % report.get("provider_entitlement_by_family"))
    print("selected family:       %s (%s, %s)"
          % (report.get("selected_data_family"), report.get("selected_provider"),
             report.get("access_status")))
    print("tickers acquired:      %s (requests %s)"
          % (report.get("acquisition", {}).get("tickers_acquired"),
             report.get("acquisition", {}).get("requests_made")))
    print("raw payloads present:  %s -> normalized rows %s / tickers %s (parser fixed=%s)"
          % (report.get("raw_payloads_present"), report.get("normalized_rows"),
             report.get("normalized_tickers"), report.get("parser_bug_fixed")))
    print("features added:        %s (coverage %s events)"
          % (report.get("features_added"), report.get("feature_coverage_events")))
    print("strong candidates:     %s" % report.get("strong_alpha_candidates"))
    print("best candidate:        %s (ic_t %s, %s)"
          % (report.get("best_candidate"), report.get("best_candidate_ic_t"),
             report.get("best_candidate_status")))
    print("another batch needed:  %s" % report.get("another_batch_needed"))
    print("purchase needed:       %s -> %s"
          % (report.get("provider_purchase_needed"), report.get("recommended_provider_purchase")))
    print("decision:              %s" % report.get("decision"))
    print("next command:          %s" % report.get("recommended_next_command"))


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=("Phase 8-Y orthogonal data-family acquisition + strong-alpha re-run (probes "
                     "only providers whose key is present; --offline emits the decision with no "
                     "network)."))
    ap.add_argument("--as-of", default=s8.DEFAULT_AS_OF)
    ap.add_argument("--max-tickers", type=int, default=DEFAULT_MAX_TICKERS)
    ap.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    ap.add_argument("--family", default=None,
                    help="Force a specific orthogonal family (must still be accessible to acquire).")
    ap.add_argument("--offline", action="store_true",
                    help="No network: classify key presence + emit the purchase/next-batch decision.")
    ap.add_argument("--normalize-existing-only", action="store_true",
                    help="Skip all probing AND acquisition: re-normalize the cached raw payloads from "
                         "disk, attach features, and re-run the augmented strong-alpha test. Makes NO "
                         "provider API call and requires no key. Use with --family.")
    ap.add_argument("--no-skip-existing", action="store_true",
                    help="Re-acquire tickers even if a raw payload already exists.")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    run(as_of=args.as_of, max_tickers=args.max_tickers, max_requests=args.max_requests,
        family=args.family, offline=args.offline,
        normalize_existing_only=args.normalize_existing_only,
        skip_existing=not args.no_skip_existing, live=True, verbose=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
