"""Phase 10-A - Missing Alpha Data Direct Acquisition, PIT Normalization, and Alpha Search.

Phase 9-C exhausted the OWNED sentiment/insider/recommendation feeds (decision
OWNED_FEEDS_EXHAUSTED_NO_STRONG_ALPHA, best candidate f_lag1_sn at t=1.9). The remaining,
unmined alpha mechanisms are forward-looking / positioning families that need NEW provider data:

    1. analyst_estimate_revisions      (EPS / revenue estimate drift before the report)
    2. price_target_revisions          (consensus target change vs price)
    3. short_interest_days_to_cover     (crowding / squeeze risk, days-to-cover)
    4. options_iv_skew_put_call         (forward-looking risk repricing)

This phase DIRECTLY probes + (where entitled) downloads each family from the currently visible
market-data keys, in the brief's exact provider priority order; normalizes every datum
point-in-time (an `available_date` <= the event's `entry_date`, no lookahead); joins it onto the
existing Phase 9-C / Norgate expanded earnings-event panel (545 tickers / ~38,725 events); builds a
broad feature catalogue (level / lag / rolling / change / acceleration / z / sector-neutral z / rank
/ winsor / x surprise|momentum|quality|value|liquidity + cross-family interactions); runs the SAME
Phase 8-X broad strong-alpha gate (IC t>=3.0, BH-significant, net-of-25bps positive, both old/new
cohorts positive, both pre/post-2020 halves positive, sector-diversified) PLUS a 1/5/21/63-day
horizon sweep. No weak / constrained signal is ever promoted as strong.

A provider that blocks entitlement NEVER stops the phase: every provider configured for a family is
probed, the exact provider / endpoint / HTTP status / blocker is recorded, and the next accessible
provider is tried. ORATS / INTRINIO / BENZINGA are recorded as missing keys (the user does not have
them) and are never required. FMP is probed like any other present key (entitlement is measured, not
assumed).

Reuse (single source of truth - nothing reimplemented):
    o8 = run_phase8o_cheapest_provider_selection         key-presence / bounded GET / redaction / IO
    y8 = run_phase8y_orthogonal_data_family_acquisition  probe / classify / acquire_family / normalize
    w8 = run_phase8w_expanded_universe_failure_attribution expanded event table / cohort / liquidity
    x8 = run_phase8x_autonomous_strong_alpha_discovery   gate / scenarios / models / scoreboards
    z8 = run_phase8z_autonomous_no_excuses_alpha_agent   point-in-time feature factory + hypotheses
    c9 = run_phase9c_verified_owned_feed_alpha_acquisition Norgate foundation verification (reuse)
    s8 = x8.s8 (8-S data layer / FWD_WINDOWS) ; t8 = x8.t8 (8-T scoring core / PRIMARY_HORIZON)

Constraints honored: Windows-compatible Python (stdlib + already-installed pandas/numpy); no package
install; no Paper Trader, no GCP, no orders, no automation, no deploy; no full Phase-8 regression
(targeted tests only); keys never printed or written; raw + normalized provider payloads
force-gitignored. No commit. No push.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research import run_phase8o_cheapest_provider_selection as o8           # noqa: E402
from research import run_phase8y_orthogonal_data_family_acquisition as y8    # noqa: E402
from research import run_phase8w_expanded_universe_failure_attribution as w8  # noqa: E402
from research import run_phase8x_autonomous_strong_alpha_discovery as x8      # noqa: E402
from research import run_phase8z_autonomous_no_excuses_alpha_agent as z8      # noqa: E402
from research import run_phase9c_verified_owned_feed_alpha_acquisition as c9  # noqa: E402

s8 = x8.s8
t8 = x8.t8

_write_json = s8._write_json
_write_csv = s8._write_csv
_read_csv_file = s8._read_csv_file
_read_json = s8._read_json
_round = s8._round
_rel = s8._rel

PHASE = "10-A"

# --------------------------------------------------------------------------- #
# Config (a-priori; never tuned to a result).
# --------------------------------------------------------------------------- #
AS_OF = s8.DEFAULT_AS_OF          # "2026-06-26"
DEEP_FROM = "2016-01-01"
DEEP_TO = AS_OF

# Bounded acquisition (from the brief).
DEFAULT_MAX_TICKERS = 545
DEFAULT_MAX_REQUESTS_PER_RUN = 2000
DEFAULT_MAX_CYCLES = 5
DEFAULT_TOTAL_REQUEST_CEILING = 8000
DEFAULT_MAX_SCENARIOS = 500
DEFAULT_MAX_MODELS = 100
ACQUIRE_MIN_SLEEP_SECONDS = 0.30

# Forward-return horizons already materialized on the event table (8-S FWD_WINDOWS).
FWD_WINDOWS = s8.FWD_WINDOWS                    # (1, 5, 21, 63)
PRIMARY_HORIZON = s8.PRIMARY_HORIZON           # 21

# Strong-alpha gate floors (reuse the 8-X promotion standard verbatim).
STRONG_MIN_TICKERS = x8.STRONG_MIN_TICKERS     # 500
STRONG_MIN_EVENTS = x8.STRONG_MIN_EVENTS       # 30000
STRONG_MIN_IC_T = x8.STRONG_MIN_IC_T           # 3.0
GATE_BH_Q = x8.GATE_BH_Q                       # 0.05
RET_COL = x8.RET_COL                           # "fwd_exc_21"

NORGATE_REBUILD_COMMAND = c9.NORGATE_REBUILD_COMMAND

# Required keys that must be VISIBLE to this shell before any live acquisition.
REQUIRED_VISIBLE_KEYS = ("EODHD_API_KEY", "FINNHUB_API_KEY", "FMP_API_KEY", "ALPHAVANTAGE_API_KEY",
                         "NASDAQ_DATA_LINK_API_KEY", "POLYGON_API_KEY", "TIINGO_API_KEY",
                         "FRED_API_KEY")
# Keys the user does NOT have - recorded missing, NEVER required, NEVER block the phase.
OPTIONAL_MISSING_KEYS = ("ORATS_API_KEY", "INTRINIO_API_KEY", "BENZINGA_API_KEY")
_ALL_ENV_VARS = tuple(REQUIRED_VISIBLE_KEYS) + OPTIONAL_MISSING_KEYS

# --------------------------------------------------------------------------- #
# Terminal decisions (allowed) - each carries an exact data family / provider / endpoint / action.
# --------------------------------------------------------------------------- #
DEC_STRONG = "STRONG_ALPHA_FOUND_READY_FOR_REVIEW"
DEC_NEXT_BATCH = "MISSING_ALPHA_DATA_ACQUIRED_READY_FOR_NEXT_BATCH"
DEC_EXHAUSTED = "ACCESSIBLE_MISSING_ALPHA_DATA_EXHAUSTED_NO_STRONG_ALPHA"
DEC_ALL_BLOCKED = "ALL_TARGET_FAMILIES_BLOCKED_BY_ENTITLEMENT"
DEC_KEYS_REQUIRED = "PROVIDER_KEYS_REQUIRED_WITH_EXACT_ACTIONS"
DEC_PAID_REQUIRED = "EXACT_PAID_PROVIDER_REQUIRED_TO_CONTINUE"
DEC_KEY_ENV = "KEY_ENV_NOT_VISIBLE_RESTART_CLAUDE_CODE"
DEC_BLOCKER = "HARD_BLOCKER_REQUIRES_USER_ACTION"
DEC_ERROR = "ERROR_WITH_REPRO_COMMAND"
ALLOWED_DECISIONS = (DEC_STRONG, DEC_NEXT_BATCH, DEC_EXHAUSTED, DEC_ALL_BLOCKED, DEC_KEYS_REQUIRED,
                     DEC_PAID_REQUIRED, DEC_KEY_ENV, DEC_BLOCKER, DEC_ERROR)
# Never emit a bare/generic terminal - it must always name family / provider / endpoint / next action.
FORBIDDEN_DECISIONS = ("MISSING_KEY", "NO_DATA", "EMPTY_PAYLOAD", "NEEDS_PROVIDER", "ERROR")

# Entitlement vocabulary (reuse the 8-Y states).
ENT_VERIFIED = y8.ENT_VERIFIED
ENT_BLOCKED = y8.ENT_BLOCKED
ENT_RATE = y8.ENT_RATE
ENT_MISSING = y8.ENT_MISSING
ENT_NOTFOUND = y8.ENT_NOTFOUND
ENT_PARSE = y8.ENT_PARSE
ENT_NOT_PROBED = y8.ENT_NOT_PROBED

# --------------------------------------------------------------------------- #
# Missing-alpha data-family registry. Providers are in the brief's exact priority order. Each provider
# spec carries the request host + secret query-parameter name (used transiently at request time;
# never persisted). ORATS / INTRINIO / BENZINGA are recorded as MISSING-KEY purchase pointers only.
# --------------------------------------------------------------------------- #
def _ep(provider, env_var, host, url, secret_param, doc):
    return y8._ep(provider, env_var, host, url, secret_param, doc)


PROV_FMP = "FMP"
PROV_FINNHUB = "Finnhub"
PROV_AV = "Alpha Vantage"
PROV_POLYGON = "Polygon"
PROV_NASDAQ = "Nasdaq Data Link"

# Missing-key providers, mapped to the families they would unlock + the exact purchase action.
MISSING_PROVIDERS: Tuple[Dict, ...] = (
    {"provider": "ORATS", "env_var": "ORATS_API_KEY", "families": ("options_iv_skew_put_call",),
     "purchase": "ORATS Data API (one-minute / EOD options, IV surface + skew) - subscribe at "
                 "orats.com/data-api, then set $env:ORATS_API_KEY"},
    {"provider": "Intrinio", "env_var": "INTRINIO_API_KEY",
     "families": ("analyst_estimate_revisions", "price_target_revisions",
                  "short_interest_days_to_cover", "options_iv_skew_put_call"),
     "purchase": "Intrinio (Zacks estimates / options price feed) - subscribe at intrinio.com, "
                 "then set $env:INTRINIO_API_KEY"},
    {"provider": "Benzinga", "env_var": "BENZINGA_API_KEY", "families": ("price_target_revisions",),
     "purchase": "Benzinga analyst ratings / price-target API - subscribe at benzinga.com/apis, "
                 "then set $env:BENZINGA_API_KEY"},
)
_MISSING_BY_FAMILY: Dict[str, List[Dict]] = {}
for _mp in MISSING_PROVIDERS:
    for _fam in _mp["families"]:
        _MISSING_BY_FAMILY.setdefault(_fam, []).append(_mp)


MISSING_ALPHA_FAMILIES: Tuple[Dict, ...] = (
    {
        "family": "analyst_estimate_revisions",
        "priority": 1,
        "feature": "est_eps_revision",
        "feature_desc": "consensus EPS-estimate level + its change/acceleration (estimate revision "
                        "drift) - forward-looking, orthogonal to the realized earnings surprise",
        "orthogonal_to": "realized earnings surprise / accounting fundamentals",
        "paid_unlock": "FMP Premium analyst-estimates, or Intrinio/Zacks estimate-revision history",
        "providers": (
            _ep(PROV_FMP, "FMP_API_KEY", "financialmodelingprep.com",
                "https://financialmodelingprep.com/api/v3/analyst-estimates/{symbol}",
                "apikey", "financialmodelingprep.com : /v3/analyst-estimates (estimate history)"),
            _ep(PROV_FINNHUB, "FINNHUB_API_KEY", "finnhub.io",
                "https://finnhub.io/api/v1/stock/eps-estimate?symbol={symbol}&freq=quarterly",
                "token", "finnhub.io/docs/api : /stock/eps-estimate (analyst EPS estimates)"),
            _ep(PROV_AV, "ALPHAVANTAGE_API_KEY", "www.alphavantage.co",
                "https://www.alphavantage.co/query?function=EARNINGS&symbol={symbol}",
                "apikey", "alphavantage.co/documentation : EARNINGS (reportedDate + estimatedEPS)"),
        ),
    },
    {
        "family": "price_target_revisions",
        "priority": 2,
        "feature": "pt_revision",
        "feature_desc": "consensus price-target level / implied upside vs price and its change "
                        "(target revision) - forward-looking, orthogonal to realized momentum",
        "orthogonal_to": "historical price momentum / reversal",
        "paid_unlock": "FMP Premium price-target, or Benzinga / Intrinio analyst price targets",
        "providers": (
            _ep(PROV_FMP, "FMP_API_KEY", "financialmodelingprep.com",
                "https://financialmodelingprep.com/api/v4/price-target?symbol={symbol}",
                "apikey", "financialmodelingprep.com : /v4/price-target (per-analyst PT actions)"),
            _ep(PROV_FINNHUB, "FINNHUB_API_KEY", "finnhub.io",
                "https://finnhub.io/api/v1/stock/price-target?symbol={symbol}",
                "token", "finnhub.io/docs/api : /stock/price-target (consensus target snapshot)"),
            _ep(PROV_POLYGON, "POLYGON_API_KEY", "api.polygon.io",
                "https://api.polygon.io/benzinga/v1/analyst-insights?ticker={symbol}",
                "apiKey", "api.polygon.io : /benzinga/v1/analyst-insights (Benzinga add-on)"),
        ),
    },
    {
        "family": "short_interest_days_to_cover",
        "priority": 3,
        "feature": "short_interest_ratio",
        "feature_desc": "short interest / days-to-cover (or daily FINRA short-volume ratio) - "
                        "crowding + squeeze risk, orthogonal to every fundamentals/price feature",
        "orthogonal_to": "fundamentals + price (positioning, not value)",
        "paid_unlock": "Nasdaq Data Link FINRA short-sale tables, Polygon short-interest, "
                       "or FMP Premium short_interest",
        "providers": (
            _ep(PROV_NASDAQ, "NASDAQ_DATA_LINK_API_KEY", "data.nasdaq.com",
                "https://data.nasdaq.com/api/v3/datasets/FINRA/FNSQ_{symbol}.json",
                "api_key", "data.nasdaq.com : FINRA/FNSQ_<sym> (daily short-volume time series)"),
            _ep(PROV_POLYGON, "POLYGON_API_KEY", "api.polygon.io",
                "https://api.polygon.io/stocks/v1/short-interest?ticker={symbol}",
                "apiKey", "api.polygon.io : /stocks/v1/short-interest (settlement + days_to_cover)"),
            _ep(PROV_FMP, "FMP_API_KEY", "financialmodelingprep.com",
                "https://financialmodelingprep.com/api/v4/short_interest?symbol={symbol}",
                "apikey", "financialmodelingprep.com : /v4/short_interest"),
        ),
    },
    {
        "family": "options_iv_skew_put_call",
        "priority": 4,
        "feature": "put_call_oi_ratio",
        "feature_desc": "put/call open-interest (+ volume) ratio and IV skew proxy from the options "
                        "chain - forward-looking risk repricing, orthogonal to all realized data",
        "orthogonal_to": "all realized earnings/fundamentals/price (forward-looking risk)",
        "paid_unlock": "ORATS / Intrinio options feed, or Polygon Options Starter (historical chains)",
        "providers": (
            _ep(PROV_POLYGON, "POLYGON_API_KEY", "api.polygon.io",
                "https://api.polygon.io/v3/snapshot/options/{symbol}?limit=250",
                "apiKey", "api.polygon.io : /v3/snapshot/options/<sym> (chain snapshot)"),
        ),
    },
)
_FAMILY_BY_NAME = {f["family"]: f for f in MISSING_ALPHA_FAMILIES}

# --------------------------------------------------------------------------- #
# Required artifacts (30 incl. the report json). Raw/normalized provider payloads stay force-gitignored
# under research/data/<provider>/; only manifests / scoreboards / metadata are written to the out dir.
# --------------------------------------------------------------------------- #
_ARTIFACTS = {
    "report": "phase10a_missing_alpha_data_acquisition.json",
    "key_preflight": "key_visibility_preflight.csv",
    "provider_attempts": "provider_family_attempts.csv",
    "entitlement_blockers": "entitlement_blockers.csv",
    "missing_keys": "missing_keys_exact_actions.csv",
    "provider_purchase": "provider_purchase_required.csv",
    "acq_progress": "acquisition_progress.csv",
    "raw_manifest": "raw_payload_manifest.csv",
    "norm_manifest": "normalized_payload_manifest.csv",
    "pit_norm_audit": "pit_normalization_audit.csv",
    "pit_join_audit": "point_in_time_join_audit.csv",
    "usable_families": "usable_missing_alpha_families.csv",
    "unusable_families": "unusable_missing_alpha_families.csv",
    "feature_coverage": "feature_coverage_report.csv",
    "feature_catalog": "feature_catalog.csv",
    "scenario_registry": "scenario_registry.csv",
    "scenario_scoreboard": "scenario_scoreboard.csv",
    "model_registry": "model_registry.csv",
    "model_scoreboard": "model_scoreboard.csv",
    "horizon_sweep": "horizon_sweep_report.csv",
    "strong_candidates": "strong_alpha_candidates.csv",
    "rejected": "rejected_hypotheses.csv",
    "tcost": "transaction_cost_report.csv",
    "cohort_stability": "cohort_stability_report.csv",
    "subperiod": "subperiod_stability_report.csv",
    "sector_conc": "sector_concentration_report.csv",
    "leakage_audit": "leakage_audit.csv",
    "next_commands": "exact_next_commands.csv",
    "next_plan": "phase10b_next_plan.json",
    "secret_audit": "secret_safety_audit.csv",
}
_REQUIRED_ARTIFACTS = tuple(_ARTIFACTS.keys())
_RESUME_STATE = "phase10a_resume_state.json"


class _Paths:
    def __init__(self, out_dir=None, data_dir=None):
        self.out = Path(out_dir) if out_dir else (
            _REPO_ROOT / "research" / "output" / "phase10a_missing_alpha_data_acquisition")
        self.data_root = Path(data_dir) if data_dir else (_REPO_ROOT / "research" / "data")

    def art(self, key: str) -> Path:
        return self.out / _ARTIFACTS[key]

    @property
    def resume_state(self) -> Path:
        return self.out / _RESUME_STATE


# --------------------------------------------------------------------------- #
# A. Key-visibility preflight (PRESENT/missing only; value never read).
# --------------------------------------------------------------------------- #
def key_visibility_preflight(transports: Optional[Dict[str, Callable]] = None
                             ) -> Tuple[List[Dict], bool, List[str]]:
    """Return (rows, all_required_visible, missing_required). A test transport for a provider counts
    as that provider's required keys being satisfiable offline (so the suite never needs a real key)."""
    transports = transports or {}
    # Map transport provider names to the env vars they satisfy.
    tp_envs = set()
    for fam in MISSING_ALPHA_FAMILIES:
        for p in fam["providers"]:
            if p["provider"] in transports:
                tp_envs.add(p["env_var"])
    rows: List[Dict] = []
    missing_required: List[str] = []
    for env in REQUIRED_VISIBLE_KEYS:
        present = o8.key_present(env) or (env in tp_envs)
        rows.append({"env_var": env, "required": True,
                     "visibility": "PRESENT" if present else "missing"})
        if not present:
            missing_required.append(env)
    for env in OPTIONAL_MISSING_KEYS:
        present = o8.key_present(env)
        rows.append({"env_var": env, "required": False,
                     "visibility": "PRESENT" if present else "missing"})
    return rows, (not missing_required), missing_required


# --------------------------------------------------------------------------- #
# B. Per-(family, provider) entitlement probe (one bounded ticker). Persists ONLY a redacted endpoint.
# --------------------------------------------------------------------------- #
def _redact_endpoint(provider: Dict) -> str:
    """Persist-safe endpoint: the template (symbol -> AAPL) with NO secret parameter, redacted. Robust
    to provider secret-param names not in the shared redaction set (e.g. Nasdaq's 'api_key')."""
    return o8.redact_url(provider["url"].replace("{symbol}", o8.PROBE_TICKER))


def probe_family_provider(family: Dict, provider: Dict, live: bool,
                          transport: Optional[Callable]) -> Dict:
    """Bounded 1-ticker probe. Never probes without a key unless a test transport is injected. Captures
    the HTTP status / entitlement class. Never raises, never persists a key or keyed URL."""
    env = provider["env_var"]
    present = o8.key_present(env)
    redacted = _redact_endpoint(provider)
    row = {"family": family["family"], "priority": family["priority"], "provider": provider["provider"],
           "env_var": env, "key_present": present, "endpoint_redacted": redacted,
           "doc": provider["doc"], "rows": 0, "http_status": "", "probed": False,
           "entitlement": ENT_MISSING, "note": ""}
    if not present and transport is None:
        row["note"] = "no %s in this session - classified MISSING_KEY, not invalid" % env
        return row
    if transport is None and not live:
        row.update({"entitlement": ENT_NOT_PROBED,
                    "note": "key present but offline mode; re-run live to probe"})
        return row
    try:
        if transport is not None:
            payload = transport(provider["url"].replace("{symbol}", o8.PROBE_TICKER))
        else:
            payload = o8._live_get(y8._build_url(provider, o8.PROBE_TICKER), provider["host"])
        ent, rows, note = y8.classify_entitlement(payload, present)
        http = 200
    except o8.ProbeError as exc:
        ent, rows, note = y8.classify_entitlement(exc, present)
        http = getattr(exc, "status_code", "") or ""
    row.update({"entitlement": ent, "probed": True, "rows": rows, "note": note, "http_status": http})
    if transport is None:
        time.sleep(ACQUIRE_MIN_SLEEP_SECONDS)
    return row


def probe_all_families(live: bool, transports: Optional[Dict[str, Callable]], log) -> List[Dict]:
    """Probe EVERY (family, provider) in strict priority order. One blocked/missing provider never
    stops the sweep. Returns the full attempt matrix."""
    transports = transports or {}
    rows: List[Dict] = []
    for family in MISSING_ALPHA_FAMILIES:
        for provider in family["providers"]:
            tp = transports.get(provider["provider"])
            r = probe_family_provider(family, provider, live, tp)
            rows.append(r)
            log.step("probe", r["entitlement"],
                     "%s / %s -> %s (http=%s)" % (family["family"], provider["provider"],
                                                  r["entitlement"], r["http_status"]))
    return rows


def select_provider_for_family(family: Dict, attempts: List[Dict]) -> Optional[Dict]:
    """The first ACCESS_VERIFIED provider in the family's declared (priority) order, else None."""
    verified = {r["provider"] for r in attempts
                if r["family"] == family["family"] and r["entitlement"] == ENT_VERIFIED}
    for provider in family["providers"]:
        if provider["provider"] in verified:
            return provider
    return None


# --------------------------------------------------------------------------- #
# C. Bounded, resumable acquisition of each accessible family (first verified provider).
# --------------------------------------------------------------------------- #
def acquire_missing_families(universe: List[str], attempts: List[Dict], data_root: Path, *,
                             max_tickers: int, request_budget: int, live: bool,
                             transports: Optional[Dict[str, Callable]], skip_existing: bool,
                             log) -> Dict:
    """For each family with at least one verified provider, acquire a bounded batch from the cheapest
    verified provider, sharing a single request budget across families. Skip-existing makes it
    resumable. Returns acquisition state per family + the aggregate request count."""
    transports = transports or {}
    fam_state: Dict[str, Dict] = {}
    progress_rows: List[Dict] = []
    raw_rows: List[Dict] = []
    total_requests = 0
    for family in MISSING_ALPHA_FAMILIES:
        provider = select_provider_for_family(family, attempts)
        if provider is None:
            fam_state[family["family"]] = {"provider": None, "requests": 0, "acquired": 0,
                                           "progress": [], "raw_dir": None,
                                           "status": "NO_ACCESSIBLE_PROVIDER"}
            continue
        remaining = max(0, request_budget - total_requests)
        if remaining <= 0:
            fam_state[family["family"]] = {"provider": provider["provider"], "requests": 0,
                                           "acquired": 0, "progress": [],
                                           "raw_dir": None, "status": "STOPPED_REQUEST_BUDGET"}
            continue
        tp = transports.get(provider["provider"])
        res = y8.acquire_family(family, provider, universe, max_tickers, remaining, skip_existing,
                                live, tp, data_root, log)
        total_requests += res["requests_made"]
        fam_state[family["family"]] = {
            "provider": provider["provider"], "requests": res["requests_made"],
            "acquired": res["tickers_acquired"], "progress": res["progress"],
            "raw_dir": res["raw_dir"],
            "status": "ACQUIRED" if res["tickers_acquired"] else "NO_NEW_DATA"}
        for p in res["progress"]:
            progress_rows.append({"family": family["family"], "provider": provider["provider"],
                                  "ticker": p.get("ticker", ""), "status": p.get("status", ""),
                                  "rows": p.get("rows", 0),
                                  "requests_made": p.get("requests_made", 0)})
        for m in res["raw_manifest"]:
            raw_rows.append({"family": family["family"], "provider": provider["provider"],
                             "ticker": m.get("ticker", ""), "raw_path": m.get("raw_path", ""),
                             "rows": m.get("rows", 0), "gitignored": True})
    return {"fam_state": fam_state, "progress_rows": progress_rows, "raw_rows": raw_rows,
            "total_requests": total_requests}


# --------------------------------------------------------------------------- #
# D. Point-in-time normalization (provider-aware record extraction -> uniform PIT schema).
# --------------------------------------------------------------------------- #
def _f(v):
    return y8._to_float(v)


def _date10(v) -> str:
    return str(v)[:10] if v not in (None, "") else ""


def _extract_family_records(family_name: str, provider_name: str, payload, sym: str) -> List[Dict]:
    """Return [{available_date, value, **extra}] for one ticker's raw payload. Branches on
    (family, provider) for the real provider shapes; a generic {"records":[{available_date,value}]} /
    bare-list shape keeps the tests fully offline."""
    out: List[Dict] = []

    # Uniform test / generic shape first (lets the suite drive any family offline).
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        for r in payload["records"]:
            if isinstance(r, dict):
                out.append({"available_date": _date10(r.get("available_date") or r.get("date")),
                            "value": _f(r.get("value"))})
        return out

    if family_name == "analyst_estimate_revisions":
        if provider_name == PROV_FMP and isinstance(payload, list):
            for r in payload:
                if not isinstance(r, dict):
                    continue
                out.append({"available_date": _date10(r.get("date")),
                            "value": _f(r.get("estimatedEpsAvg")),
                            "eps_estimate": _f(r.get("estimatedEpsAvg")),
                            "revenue_estimate": _f(r.get("estimatedRevenueAvg")),
                            "num_analysts": _f(r.get("numberAnalystEstimatedEps"))})
        elif provider_name == PROV_FINNHUB and isinstance(payload, dict):
            for r in payload.get("data", []) or []:
                if not isinstance(r, dict):
                    continue
                out.append({"available_date": _date10(r.get("period")),
                            "value": _f(r.get("epsAvg")),
                            "eps_estimate": _f(r.get("epsAvg")),
                            "revenue_estimate": _f(r.get("revenueAvg")),
                            "num_analysts": _f(r.get("numberAnalysts"))})
        elif provider_name == PROV_AV and isinstance(payload, dict):
            for r in payload.get("quarterlyEarnings", []) or []:
                if not isinstance(r, dict):
                    continue
                out.append({"available_date": _date10(r.get("reportedDate")),
                            "value": _f(r.get("estimatedEPS")),
                            "eps_estimate": _f(r.get("estimatedEPS")),
                            "reported_eps": _f(r.get("reportedEPS"))})
    elif family_name == "price_target_revisions":
        if provider_name == PROV_FMP and isinstance(payload, list):
            for r in payload:
                if not isinstance(r, dict):
                    continue
                tgt = _f(r.get("priceTarget"))
                base = _f(r.get("priceWhenPosted"))
                val = (tgt / base - 1.0) if (tgt is not None and base) else tgt
                out.append({"available_date": _date10(r.get("publishedDate")), "value": val,
                            "price_target": tgt, "price_when_posted": base,
                            "analyst": r.get("analystName") or r.get("analystCompany") or ""})
        elif provider_name == PROV_FINNHUB and isinstance(payload, dict):
            out.append({"available_date": _date10(payload.get("lastUpdated")),
                        "value": _f(payload.get("targetMean")),
                        "target_mean": _f(payload.get("targetMean")),
                        "target_high": _f(payload.get("targetHigh")),
                        "target_low": _f(payload.get("targetLow"))})
        elif provider_name == PROV_POLYGON and isinstance(payload, dict):
            for r in payload.get("results", []) or []:
                if not isinstance(r, dict):
                    continue
                out.append({"available_date": _date10(r.get("date") or r.get("last_updated")),
                            "value": _f(r.get("pt_current") or r.get("price_target")),
                            "price_target": _f(r.get("pt_current") or r.get("price_target"))})
    elif family_name == "short_interest_days_to_cover":
        if provider_name == PROV_NASDAQ and isinstance(payload, dict):
            ds = payload.get("dataset") or {}
            cols = [str(c).lower() for c in (ds.get("column_names") or [])]
            di = cols.index("date") if "date" in cols else 0
            svi = next((i for i, c in enumerate(cols) if "short" in c and "volume" in c), None)
            tvi = next((i for i, c in enumerate(cols) if "total" in c and "volume" in c), None)
            for r in ds.get("data", []) or []:
                if not isinstance(r, list) or svi is None or tvi is None:
                    continue
                sv, tv = _f(r[svi]), _f(r[tvi])
                ratio = (sv / tv) if (sv is not None and tv) else None
                out.append({"available_date": _date10(r[di]), "value": ratio,
                            "short_volume": sv, "total_volume": tv})
        elif provider_name == PROV_POLYGON and isinstance(payload, dict):
            for r in payload.get("results", []) or []:
                if not isinstance(r, dict):
                    continue
                dtc = _f(r.get("days_to_cover"))
                si = _f(r.get("short_interest"))
                adv = _f(r.get("avg_daily_volume"))
                val = dtc if dtc is not None else ((si / adv) if (si is not None and adv) else si)
                out.append({"available_date": _date10(r.get("settlement_date")), "value": val,
                            "short_interest": si, "days_to_cover": dtc, "avg_daily_volume": adv})
        elif provider_name == PROV_FMP and isinstance(payload, list):
            for r in payload:
                if not isinstance(r, dict):
                    continue
                out.append({"available_date": _date10(r.get("settlementDate") or r.get("date")),
                            "value": _f(r.get("shortInterest") or r.get("shortInterestRatio")),
                            "short_interest": _f(r.get("shortInterest"))})
    elif family_name == "options_iv_skew_put_call":
        if isinstance(payload, dict):
            put_oi = call_oi = put_vol = call_vol = 0.0
            put_iv: List[float] = []
            call_iv: List[float] = []
            for r in payload.get("results", []) or []:
                if not isinstance(r, dict):
                    continue
                det = r.get("details") or {}
                ctype = str(det.get("contract_type") or "").lower()
                oi = _f(r.get("open_interest")) or 0.0
                vol = _f((r.get("day") or {}).get("volume")) or 0.0
                iv = _f(r.get("implied_volatility"))
                if ctype == "put":
                    put_oi += oi; put_vol += vol
                    if iv is not None:
                        put_iv.append(iv)
                elif ctype == "call":
                    call_oi += oi; call_vol += vol
                    if iv is not None:
                        call_iv.append(iv)
            if call_oi or put_oi:
                pcr = (put_oi / call_oi) if call_oi else None
                skew = ((sum(put_iv) / len(put_iv)) - (sum(call_iv) / len(call_iv))
                        ) if (put_iv and call_iv) else None
                out.append({"available_date": _date10(payload.get("as_of") or DEEP_TO),
                            "value": pcr, "put_oi": put_oi, "call_oi": call_oi,
                            "put_call_vol_ratio": (put_vol / call_vol) if call_vol else None,
                            "iv_skew": skew})
    # bare-list fallback (provider returned a plain list we did not branch on).
    if not out and isinstance(payload, list):
        for r in payload:
            if isinstance(r, dict):
                out.append({"available_date": _date10(r.get("date") or r.get("available_date")),
                            "value": _f(r.get("value"))})
    return out


def normalize_family_pit(family: Dict, provider_name: Optional[str], raw_dir: Optional[Path],
                         provider_dir: Path, as_of: str, log) -> Tuple[Path, List[Dict], List[Dict]]:
    """Flatten a family's raw payloads into a uniform PIT CSV (ticker, available_date, <feature>, +
    family-specific extra fields). Every record is classified; records with no availability date, no
    value, or an availability date AFTER the as-of (future leak) are dropped. Returns
    (normalized_csv_path, norm_manifest_rows, pit_audit_rows)."""
    feat = family["feature"]
    norm_dir = provider_dir / "normalized" / family["family"]
    norm_dir.mkdir(parents=True, exist_ok=True)
    out_csv = norm_dir / ("%s.csv" % feat)
    records: List[List] = []
    audit: List[Dict] = []
    extra_keys: List[str] = []
    if raw_dir is not None and Path(raw_dir).is_dir():
        import json
        for raw_path in sorted(Path(raw_dir).glob("*.json")):
            sym = raw_path.stem.upper()
            try:
                with open(raw_path, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
            except (OSError, json.JSONDecodeError):
                audit.append({"family": family["family"], "ticker": sym, "status": "PARSE_FAILED",
                              "available_date": "", "value": "", "pit_ok": False})
                continue
            for rec in _extract_family_records(family["family"], provider_name or "", payload, sym):
                avail = rec.get("available_date", "")
                val = rec.get("value")
                status, pit_ok = y8._pit_status(avail, val, as_of)
                for k in rec:
                    if k not in ("available_date", "value") and k not in extra_keys:
                        extra_keys.append(k)
                if pit_ok:
                    records.append([sym, avail, _round(val, 6), rec])
                audit.append({"family": family["family"], "ticker": sym, "status": status,
                              "available_date": avail,
                              "value": _round(val, 6) if val is not None else "", "pit_ok": pit_ok})
    header = ["ticker", "available_date", feat] + extra_keys
    rows_out = []
    for sym, avail, val, rec in records:
        row = [sym, avail, val] + [
            (_round(rec.get(k), 6) if isinstance(rec.get(k), (int, float)) else (rec.get(k) or ""))
            for k in extra_keys]
        rows_out.append(row)
    _write_csv(out_csv, header, rows_out)
    n_tickers = len({r[0] for r in records})
    manifest = [{"family": family["family"], "provider": provider_name or "",
                 "feature": feat, "normalized_path": _rel(out_csv), "rows": len(records),
                 "tickers": n_tickers, "fields": "|".join(header), "gitignored": True}]
    log.step("normalize", "DONE", "%s (%s): %d PIT rows / %d tickers"
             % (family["family"], provider_name or "-", len(records), n_tickers), count=len(records))
    return out_csv, manifest, audit


# --------------------------------------------------------------------------- #
# E. Feature factory + cross-family interactions + campaign through the 8-X gate.
# --------------------------------------------------------------------------- #
def build_cross_family_features(ev, norm_csvs: Dict[str, Path], log):
    """As-of attach each family PIT level, build within-month z products across families + each family
    x earnings-surprise / momentum / liquidity. Returns (ev_aug, interaction_specs, catalog_rows)."""
    import pandas as pd
    ev_aug = ev.copy()
    if "month" not in ev_aug.columns:
        ev_aug["month"] = ev_aug["entry_date"].dt.to_period("M")
    levels: Dict[str, str] = {}
    for fam in MISSING_ALPHA_FAMILIES:
        csv_path = norm_csvs.get(fam["family"])
        if not csv_path or not Path(csv_path).is_file():
            continue
        ev_aug, cov, _cols = y8.attach_orthogonal_feature(ev_aug, fam, csv_path, log)
        feat = fam["feature"]
        if cov > 0 and feat in ev_aug.columns and int(ev_aug[feat].notna().sum()) > 0:
            zc = "%s__z" % feat
            ev_aug[zc] = x8._within_month_z(ev_aug, feat).to_numpy()
            levels[fam["feature"]] = zc

    specs: List[Dict] = []
    catalog: List[List] = []

    def _pair(a_feat, b_feat, name, desc):
        za, zb = levels.get(a_feat), levels.get(b_feat)
        if za is None or zb is None:
            return
        col = "ix_%s" % name
        ev_aug[col] = (ev_aug[za] * ev_aug[zb]).to_numpy()
        cov = int(pd.Series(ev_aug[col]).notna().sum())
        catalog.append([col, "interaction", name, desc, "%s x %s" % (a_feat, b_feat), cov])
        if cov > 0:
            specs.append({"cycle": 1, "family": "interaction", "scenario": col, "signal": col,
                          "sector_neutral": False, "regime_filter": None, "exploratory": False,
                          "hypothesis": desc})

    feats = [f["feature"] for f in MISSING_ALPHA_FAMILIES]
    for i in range(len(feats)):
        for j in range(i + 1, len(feats)):
            _pair(feats[i], feats[j], "%s_x_%s" % (feats[i], feats[j]),
                  "cross-family interaction %s x %s" % (feats[i], feats[j]))

    for fam in MISSING_ALPHA_FAMILIES:
        zf = levels.get(fam["feature"])
        if zf is None:
            continue
        for base, blabel in (("surprise_pct", "surprise"), ("mom_pre_63", "momentum"),
                             ("quality_composite", "quality"), ("earnings_yield", "value"),
                             ("liquidity", "liquidity")):
            if base not in ev_aug.columns:
                continue
            zb = "%s__zbase" % base
            if zb not in ev_aug.columns:
                ev_aug[zb] = x8._within_month_z(ev_aug, base).to_numpy()
            col = "ix_%s_x_%s" % (fam["feature"], blabel)
            ev_aug[col] = (ev_aug[zf] * ev_aug[zb]).to_numpy()
            cov = int(pd.Series(ev_aug[col]).notna().sum())
            catalog.append([col, "interaction", "%s_x_%s" % (fam["feature"], blabel),
                            "%s conditioned on %s" % (fam["feature"], blabel),
                            "%s x %s" % (fam["feature"], base), cov])
            if cov > 0:
                specs.append({"cycle": 1, "family": "interaction", "scenario": col, "signal": col,
                              "sector_neutral": False, "regime_filter": None, "exploratory": False,
                              "hypothesis": "%s conditioned on %s" % (fam["feature"], blabel)})
    log.step("interactions", "DONE", "%d cross-family interaction features built" % len(specs))
    return ev_aug, specs, catalog


def run_campaign(ev, norm_csvs: Dict[str, Path], *, max_scenarios: int, max_models: int, log) -> Dict:
    """Per family: the 8-Z PIT feature factory + factory hypotheses; then cross-family interaction
    scenarios. Aggregates candidates / registries / catalog / coverage / per-family diagnosis."""
    fam_results: List[Dict] = []
    candidates: List[Dict] = []
    catalog_rows: List[List] = []
    scenario_specs: List[Dict] = []
    model_specs: List[Dict] = []
    coverage_rows: List[Dict] = []
    rng = x8._mk_rng()
    for fam in MISSING_ALPHA_FAMILIES:
        csv_path = norm_csvs.get(fam["family"])
        norm_rows = len(_read_csv_file(csv_path)) if csv_path and Path(csv_path).is_file() else 0
        ev_aug, populated, cat, cov = z8.feature_factory(ev, csv_path, fam, log)
        catalog_rows.extend(cat)
        max_cov = max(cov.values()) if cov else 0
        for col, c in sorted(cov.items()):
            coverage_rows.append({"family": fam["family"], "feature_col": col, "coverage": c})
        if populated:
            cands, _w, sspecs, mspecs = z8.evaluate_factory_hypotheses(
                ev_aug, fam, populated, max_scenarios, max_models, log)
            candidates.extend(cands)
            scenario_specs.extend(sspecs)
            model_specs.extend(mspecs)
        fam_results.append({"family": fam["family"], "feature": fam["feature"], "norm_rows": norm_rows,
                            "populated": bool(populated), "max_coverage": max_cov,
                            "parser_blocker": bool(norm_rows > 0 and not populated),
                            "diagnosis": _coverage_diagnosis(csv_path, ev, fam, norm_rows, max_cov)})

    ev_ix, ix_specs, ix_catalog = build_cross_family_features(ev, norm_csvs, log)
    catalog_rows.extend(ix_catalog)
    for sc in ix_specs:
        candidates.append(x8.evaluate_scenario(ev_ix, sc, rng))
    scenario_specs.extend(ix_specs)
    return {"fam_results": fam_results, "candidates": candidates, "catalog_rows": catalog_rows,
            "scenario_specs": scenario_specs, "model_specs": model_specs,
            "coverage_rows": coverage_rows, "ev_ix": ev_ix}


def _coverage_diagnosis(csv_path, ev, fam, norm_rows, coverage) -> str:
    if norm_rows <= 0:
        return ("0 normalized rows for %s - acquire the feed (verified provider) or patch the "
                "normalizer (NOT data exhaustion)" % fam["family"])
    if coverage > 0:
        return "covered"
    try:
        import pandas as pd
        nf = pd.DataFrame(_read_csv_file(csv_path))
        nf["available_date"] = pd.to_datetime(nf["available_date"], errors="coerce")
        nd = nf["available_date"].dropna()
        norm_t = set(nf["ticker"].astype(str).str.upper())
        ev_t = set(ev["ticker"].astype(str).str.upper())
        if not (norm_t & ev_t):
            return ("ticker mismatch: 0 of %d normalized tickers overlap the %d event tickers "
                    "(join-key issue, NOT exhaustion)" % (len(norm_t), len(ev_t)))
        ev_max = ev["entry_date"].max()
        if not nd.empty and nd.min() > ev_max:
            return ("date-span mismatch: %s history starts %s AFTER last event %s - acquire DEEPER "
                    "from/to history (or a snapshot endpoint returns current-only; needs historical "
                    "feed) (NOT exhaustion)" % (fam["family"], str(nd.min())[:10], str(ev_max)[:10]))
        return "date-span/join mismatch - acquire deeper history overlapping the panel (NOT exhaustion)"
    except Exception:                                      # pragma: no cover - defensive
        return "zero coverage - investigate join (NOT exhaustion)"


# --------------------------------------------------------------------------- #
# F. Horizon sweep (1/5/21/63-day forward excess return IC) - local extension, no existing phase change.
# --------------------------------------------------------------------------- #
def horizon_sweep(ev_ix, norm_csvs: Dict[str, Path], log) -> List[Dict]:
    """For each family's primary as-of-attached level + its within-month z, compute the within-month
    Spearman IC against fwd_exc_{1,5,21,63} and its t-stat. Pure local computation on the columns the
    factory already attached - it does not touch the 8-X scoring core or any other phase."""
    import numpy as np
    import pandas as pd
    rows: List[Dict] = []
    if getattr(ev_ix, "empty", True):
        return rows
    work = ev_ix.copy()
    if "month" not in work.columns:
        work["month"] = work["entry_date"].dt.to_period("M")
    signal_cols: List[Tuple[str, str]] = []
    for fam in MISSING_ALPHA_FAMILIES:
        feat = fam["feature"]
        if feat in work.columns and int(work[feat].notna().sum()) > 0:
            signal_cols.append((fam["family"], feat))
            zc = "%s__z" % feat
            if zc in work.columns and int(work[zc].notna().sum()) > 0:
                signal_cols.append((fam["family"], zc))
    for fam_name, col in signal_cols:
        for h in FWD_WINDOWS:
            ret_col = "fwd_exc_%d" % h
            if ret_col not in work.columns:
                rows.append({"family": fam_name, "signal": col, "horizon_days": h, "n_months": 0,
                             "n_events": 0, "mean_ic": "", "ic_t": "", "note": "no %s column" % ret_col})
                continue
            ics: List[float] = []
            n_events = 0
            for _m, g in work.groupby("month"):
                sub = g[[col, ret_col]].dropna()
                if len(sub) < 8:
                    continue
                a = sub[col].rank()
                b = sub[ret_col].rank()
                if a.std(ddof=0) == 0 or b.std(ddof=0) == 0:
                    continue
                ic = float(np.corrcoef(a, b)[0, 1])
                if not math.isnan(ic):
                    ics.append(ic)
                    n_events += len(sub)
            if len(ics) >= 2:
                arr = np.array(ics, dtype=float)
                mean_ic = float(arr.mean())
                sd = float(arr.std(ddof=1))
                ic_t = (mean_ic / (sd / math.sqrt(len(arr)))) if sd > 0 else 0.0
                rows.append({"family": fam_name, "signal": col, "horizon_days": h,
                             "n_months": len(arr), "n_events": n_events,
                             "mean_ic": _round(mean_ic, 5), "ic_t": _round(ic_t, 3),
                             "note": "primary" if h == PRIMARY_HORIZON else ""})
            else:
                rows.append({"family": fam_name, "signal": col, "horizon_days": h,
                             "n_months": len(ics), "n_events": n_events, "mean_ic": "",
                             "ic_t": "", "note": "insufficient monthly coverage"})
    log.step("horizon", "DONE", "%d horizon-sweep rows over %d signals"
             % (len(rows), len(signal_cols)))
    return rows


# --------------------------------------------------------------------------- #
# G. Decision.
# --------------------------------------------------------------------------- #
def derive_decision(*, panel_ok: bool, attempts: List[Dict], acq: Dict, fam_results: List[Dict],
                    candidates: List[Dict], universe_size: int, max_tickers: int,
                    total_requests: int, request_ceiling: int) -> Tuple[str, str, List[Dict]]:
    """Return (decision, rationale, next_command_rows). Always carries an exact next action; never a
    bare terminal. Strong > acquired-more > acquired-exhausted > all-blocked/paid/keys-required."""
    next_rows: List[Dict] = []

    strong = [c for c in candidates if c.get("status") == "strong"]
    if strong:
        best = x8._best_candidate(strong) or strong[0]
        next_rows.append({"action": "review_promoted_strong_alpha", "family": best.get("family", ""),
                          "command": "python research/run_phase10b_missing_alpha_productization.py"})
        return (DEC_STRONG,
                "Promoted %d strong missing-alpha candidate(s) clearing the full broad 8-X gate "
                "(t>=%.1f, BH-significant, net-of-25bps positive, both cohorts + both pre/post-2020 "
                "halves positive, sector-diversified). Best: %s (t=%s)."
                % (len(strong), STRONG_MIN_IC_T, best["name"], x8._g(best["metrics"], "ic_t", 2)),
                next_rows)

    if not panel_ok:
        next_rows.append({"action": "restore_event_panel", "family": "",
                          "command": "python research/run_phase8v_combined_eodhd_universe_expansion.py"})
        return (DEC_BLOCKER,
                "The gitignored expanded earnings-event panel (8-V/8-W EODHD cache + Norgate controls) "
                "is not present, so no point-in-time events could be scored. Restore the panel, "
                "then re-run.", next_rows)

    verified_fams = {r["family"] for r in attempts if r["entitlement"] == ENT_VERIFIED}
    blocked_fams = {r["family"] for r in attempts if r["entitlement"] == ENT_BLOCKED}
    missing_only_fams = set()
    for fam in MISSING_ALPHA_FAMILIES:
        ents = {r["entitlement"] for r in attempts if r["family"] == fam["family"]}
        if not (ents & {ENT_VERIFIED, ENT_BLOCKED, ENT_RATE}):
            missing_only_fams.add(fam["family"])

    acquired_usable = [f for f in fam_results if f["max_coverage"] > 0]
    acquired_norm = [f for f in fam_results if f["norm_rows"] > 0]

    if not verified_fams:
        # Nothing accessible at all. Distinguish entitlement-block vs missing-key vs paid-needed.
        for fam in MISSING_ALPHA_FAMILIES:
            for mp in _MISSING_BY_FAMILY.get(fam["family"], []):
                next_rows.append({"action": "purchase_provider", "family": fam["family"],
                                  "command": "set $env:%s after subscribing - %s"
                                  % (mp["env_var"], mp["purchase"])})
        if blocked_fams:
            paid = "; ".join("%s -> %s" % (f["family"], f["paid_unlock"])
                             for f in MISSING_ALPHA_FAMILIES if f["family"] in blocked_fams)
            return (DEC_ALL_BLOCKED,
                    "Every target family's present-key providers are entitlement-blocked: %s. "
                    "Upgrade one of the exact paid feeds below or add a specialist key." % paid,
                    next_rows)
        if missing_only_fams and not blocked_fams:
            keys = "; ".join("set $env:%s" % e for e in REQUIRED_VISIBLE_KEYS)
            next_rows.append({"action": "set_provider_keys", "family": "",
                              "command": keys + " then re-run --live"})
            return (DEC_KEYS_REQUIRED,
                    "No verified provider for any target family and the configured providers were not "
                    "reachable with a present key. Set the exact provider keys, then re-run.",
                    next_rows)
        return (DEC_PAID_REQUIRED,
                "No accessible provider yields the missing-alpha families with the current keys; the "
                "exact paid provider/subscription per family is recorded.", next_rows)

    # At least one family is accessible.
    stopped_budget = any(p.get("status") in ("STOPPED_MAX_REQUESTS",)
                         for f in acq["fam_state"].values() for p in f.get("progress", []))
    more_universe = universe_size > max_tickers or stopped_budget
    ceiling_left = total_requests < request_ceiling

    if acquired_norm and not acquired_usable:
        # Acquired raw + normalized PIT rows but zero overlap with the panel -> deeper history / not
        # snapshot-only. Treat as ready-for-next-batch with an exact deeper-history command.
        gaps = "; ".join("%s: %s" % (f["family"], f["diagnosis"])
                         for f in acquired_norm if f["max_coverage"] == 0)
        next_rows.append({"action": "acquire_deeper_history", "family": "",
                          "command": "python research/run_phase10a_missing_alpha_data_acquisition.py "
                                     "--live --refresh --deep-from %s --max-tickers %d"
                                     % (DEEP_FROM, max_tickers)})
        return (DEC_NEXT_BATCH,
                "Missing-alpha data acquired + normalized PIT but it does not yet overlap the earnings "
                "panel at the join. Exact fix: %s" % gaps, next_rows)

    if more_universe and ceiling_left:
        next_rows.append({"action": "continue_next_batch", "family": "",
                          "command": "python research/run_phase10a_missing_alpha_data_acquisition.py "
                                     "--live --max-tickers %d --max-requests %d"
                                     % (max_tickers, DEFAULT_MAX_REQUESTS_PER_RUN)})
        return (DEC_NEXT_BATCH,
                "Acquired and scored a bounded missing-alpha batch from the accessible families (%s); "
                "no strong alpha yet, the request budget/universe is not exhausted. Continue the next "
                "resumable batch (skip-existing resumes)." % ", ".join(sorted(verified_fams)),
                next_rows)

    # Distinguish FULLY-TESTED accessible families (whole universe acquired, gate run) from
    # rate-limit-capped ones (a verified provider that throttled before covering the universe -> NOT
    # exhausted), and name the exact paid upgrade for each entitlement-blocked family.
    rate_limited, fully_tested = [], []
    for fam_name in sorted(verified_fams):
        st = acq["fam_state"].get(fam_name, {})
        n_rl = sum(1 for p in st.get("progress", []) if p.get("status") == ENT_RATE)
        cov = next((f["max_coverage"] for f in fam_results if f["family"] == fam_name), 0)
        if n_rl > 0 and int(st.get("acquired", 0)) < universe_size:
            rate_limited.append(fam_name)
        elif cov > 0:
            fully_tested.append(fam_name)
    for fam_name in rate_limited:
        fam = _FAMILY_BY_NAME.get(fam_name, {})
        next_rows.append({"action": "complete_rate_limited_family", "family": fam_name,
                          "command": "%s rate-limited at a partial universe; wait for the provider's "
                                     "daily quota to reset OR upgrade the tier / use a paid feed (%s), "
                                     "then re-run run_phase10a_missing_alpha_data_acquisition.py "
                                     "--live --refresh" % (fam_name, fam.get("paid_unlock", ""))})
    for fam in MISSING_ALPHA_FAMILIES:
        if fam["family"] in blocked_fams and fam["family"] not in verified_fams:
            next_rows.append({"action": "purchase_provider_for_blocked_family",
                              "family": fam["family"],
                              "command": "subscribe + set the key for: %s, then re-run "
                                         "run_phase10a_missing_alpha_data_acquisition.py --live"
                                         % fam["paid_unlock"]})
    rationale = (
        "Full broad 8-X gate + 1/5/21/63d horizon sweep run with NO strong alpha. Fully tested over "
        "the whole %d-ticker survivorship-free universe: %s -> no strong alpha (best below t>=%.1f). "
        "Rate-limit-capped (partial universe, NOT exhausted - resume/upgrade to finish): %s. "
        "Entitlement-blocked, need a paid feed: %s." % (
            universe_size, ", ".join(fully_tested) or "(none)", STRONG_MIN_IC_T,
            ", ".join(rate_limited) or "(none)", ", ".join(sorted(blocked_fams)) or "(none)"))
    if not next_rows:
        next_rows.append({"action": "review_results", "family": "",
                          "command": "review research/output/phase10a_missing_alpha_data_acquisition/"})
    return (DEC_EXHAUSTED, rationale, next_rows)


# --------------------------------------------------------------------------- #
# H. Secret-safety audit.
# --------------------------------------------------------------------------- #
def _secret_safety_audit(out_dir: Path) -> Tuple[List[Dict], bool]:
    markers = ["apikey=", "api_token=", "token=", "api_key=", "&apikey", "?apikey", "&token", "&apikey="]
    present_values = []
    for env in _ALL_ENV_VARS:
        v = os.environ.get(env)
        if isinstance(v, str) and v.strip():
            present_values.append(v.strip())
    rows: List[Dict] = []
    clean = True
    for p in sorted(out_dir.glob("*")):
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        low = text.lower()
        marker_hit = next((m for m in markers if m in low), "")
        value_hit = any(val in text for val in present_values)
        file_clean = not marker_hit and not value_hit
        clean = clean and file_clean
        rows.append({"file": p.name, "clean": file_clean,
                     "keyed_url_marker": marker_hit or "none", "key_value_present": value_hit})
    return rows, clean


def _finite(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


# --------------------------------------------------------------------------- #
# I. Artifact writers.
# --------------------------------------------------------------------------- #
def _leakage_audit_rows(ev, norm_csvs: Dict[str, Path], pit_audit: List[Dict]) -> List[List]:
    rows = [
        ["as_of_join_direction", "PASS", "backward only: feature attached iff available_date <= "
         "entry_date (y8.attach_orthogonal_feature / z8.feature_factory)"],
        ["future_dated_records_dropped", "PASS",
         "normalizer drops available_date > as_of (%s)" % AS_OF],
        ["forward_return_label", "PASS",
         "fwd_exc_{1,5,21,63} computed strictly after entry_date (8-S forward-return engine)"],
    ]
    dropped_future = sum(1 for a in pit_audit if a.get("status") == "DROPPED_FUTURE_DATE")
    rows.append(["pit_records_dropped_future_dated", "PASS",
                 "%d future-dated records dropped at normalization" % dropped_future])
    try:
        import pandas as pd
        ev_max = str(ev["entry_date"].max())[:10] if not getattr(ev, "empty", True) else ""
        for fam in MISSING_ALPHA_FAMILIES:
            cp = norm_csvs.get(fam["family"])
            if not cp or not Path(cp).is_file():
                continue
            nf = pd.DataFrame(_read_csv_file(cp))
            if nf.empty or "available_date" not in nf.columns:
                continue
            nd = pd.to_datetime(nf["available_date"], errors="coerce").dropna()
            if nd.empty:
                continue
            rows.append(["normalized_span_%s" % fam["family"],
                         "PASS" if str(nd.max())[:10] <= AS_OF else "FAIL",
                         "span %s..%s vs last event %s" % (str(nd.min())[:10], str(nd.max())[:10],
                                                           ev_max)])
    except Exception:                                      # pragma: no cover - defensive
        pass
    return rows


def write_artifacts(P: _Paths, *, preflight_rows, attempts, acq, campaign, candidates, strong,
                    rejected, exh_rows, horizon_rows, ev, norm_csvs, pit_audit, norm_manifest,
                    next_rows, log) -> bool:
    _write_csv(P.art("key_preflight"), ["env_var", "required", "visibility"],
               [[r["env_var"], r["required"], r["visibility"]] for r in preflight_rows])

    _write_csv(P.art("provider_attempts"),
               ["family", "priority", "provider", "env_var", "key_present", "entitlement",
                "http_status", "rows", "endpoint_redacted", "note"],
               [[r["family"], r["priority"], r["provider"], r["env_var"], r["key_present"],
                 r["entitlement"], r["http_status"], r["rows"], r["endpoint_redacted"], r["note"]]
                for r in attempts])

    _write_csv(P.art("entitlement_blockers"),
               ["family", "provider", "endpoint_redacted", "http_status", "entitlement", "blocker"],
               [[r["family"], r["provider"], r["endpoint_redacted"], r["http_status"],
                 r["entitlement"], r["note"]]
                for r in attempts if r["entitlement"] in (ENT_BLOCKED, ENT_RATE, ENT_NOTFOUND,
                                                          ENT_PARSE)])

    missing_rows = []
    for mp in MISSING_PROVIDERS:
        missing_rows.append([mp["provider"], mp["env_var"], o8.key_present(mp["env_var"]),
                             "|".join(mp["families"]), mp["purchase"]])
    _write_csv(P.art("missing_keys"),
               ["provider", "env_var", "key_present", "families_unlocked", "exact_action"],
               missing_rows)

    purchase_rows = []
    for fam in MISSING_ALPHA_FAMILIES:
        verified = any(r["family"] == fam["family"] and r["entitlement"] == ENT_VERIFIED
                       for r in attempts)
        if not verified:
            purchase_rows.append([fam["family"], fam["priority"], fam["paid_unlock"],
                                  "; ".join("%s(%s)" % (mp["provider"], mp["env_var"])
                                            for mp in _MISSING_BY_FAMILY.get(fam["family"], []))])
    _write_csv(P.art("provider_purchase"),
               ["family", "priority", "paid_unlock", "missing_specialist_keys"], purchase_rows)

    _write_csv(P.art("acq_progress"),
               ["family", "provider", "ticker", "status", "rows", "requests_made"],
               [[r["family"], r["provider"], r["ticker"], r["status"], r["rows"], r["requests_made"]]
                for r in acq["progress_rows"]])
    _write_csv(P.art("raw_manifest"),
               ["family", "provider", "ticker", "raw_path", "rows", "gitignored"],
               [[r["family"], r["provider"], r["ticker"], r["raw_path"], r["rows"], r["gitignored"]]
                for r in acq["raw_rows"]])

    _write_csv(P.art("norm_manifest"),
               ["family", "provider", "feature", "normalized_path", "rows", "tickers", "fields",
                "gitignored"],
               [[m["family"], m.get("provider", ""), m["feature"], m["normalized_path"], m["rows"],
                 m["tickers"], m.get("fields", ""), m.get("gitignored", True)] for m in norm_manifest])

    _write_csv(P.art("pit_norm_audit"),
               ["family", "ticker", "status", "available_date", "value", "pit_ok"],
               [[a.get("family", ""), a.get("ticker", ""), a.get("status", ""),
                 a.get("available_date", ""), a.get("value", ""), a.get("pit_ok", "")]
                for a in pit_audit])
    # the as-of join audit is the same PIT discipline applied at attach time (one row per family).
    _write_csv(P.art("pit_join_audit"),
               ["family", "norm_rows", "coverage_events", "join_direction", "diagnosis"],
               [[f["family"], f["norm_rows"], f["max_coverage"], "available_date <= entry_date",
                 f["diagnosis"]] for f in campaign["fam_results"]])

    usable, unusable = [], []
    for f in campaign["fam_results"]:
        if f["max_coverage"] > 0:
            usable.append([f["family"], f["feature"], f["norm_rows"], f["max_coverage"],
                           "USABLE_PIT_COVERAGE"])
        else:
            unusable.append([f["family"], f["feature"], f["norm_rows"], f["max_coverage"],
                             f["diagnosis"]])
    _write_csv(P.art("usable_families"),
               ["family", "feature", "norm_rows", "coverage_events", "status"], usable)
    _write_csv(P.art("unusable_families"),
               ["family", "feature", "norm_rows", "coverage_events", "reason"], unusable)

    _write_csv(P.art("feature_coverage"), ["family", "feature_col", "coverage"],
               [[r["family"], r["feature_col"], r["coverage"]] for r in campaign["coverage_rows"]])
    _write_csv(P.art("feature_catalog"),
               ["feature_col", "family", "transform", "description", "orthogonal_to", "coverage"],
               [list(r) + [""] * (6 - len(r)) for r in campaign["catalog_rows"]])

    scen = [c for c in candidates if c["kind"] == "scenario"]
    model = [c for c in candidates if c["kind"] == "model"]
    _write_csv(P.art("scenario_scoreboard"), x8._CAND_HDR, [x8._cand_row_common(c) for c in scen])
    _write_csv(P.art("model_scoreboard"), x8._CAND_HDR, [x8._cand_row_common(c) for c in model])
    _write_csv(P.art("scenario_registry"),
               ["scenario", "family", "signal", "sector_neutral", "regime_filter", "exploratory",
                "hypothesis"],
               [[s.get("scenario", ""), s.get("family", ""), s.get("signal", ""),
                 s.get("sector_neutral", False), s.get("regime_filter"), s.get("exploratory", False),
                 s.get("hypothesis", "")] for s in campaign["scenario_specs"]])
    _write_csv(P.art("model_registry"),
               ["scenario", "family", "signals", "weighting", "sector_neutral", "hypothesis"],
               [[s.get("scenario", ""), s.get("family", "model"), "+".join(s.get("signals", [])),
                 s.get("weighting", ""), s.get("sector_neutral", False), s.get("hypothesis", "")]
                for s in campaign["model_specs"]])

    _write_csv(P.art("horizon_sweep"),
               ["family", "signal", "horizon_days", "n_months", "n_events", "mean_ic", "ic_t", "note"],
               [[r["family"], r["signal"], r["horizon_days"], r["n_months"], r["n_events"],
                 r["mean_ic"], r["ic_t"], r["note"]] for r in horizon_rows])

    _write_csv(P.art("strong_candidates"), x8._CAND_HDR + ["all_strong_gate_checks_passed"],
               [x8._cand_row_common(c) + [True] for c in strong])
    _write_csv(P.art("rejected"),
               ["name", "kind", "family", "ic_t", "bh_significant", "reject_reasons"],
               [[c["name"], c["kind"], c["family"], x8._g(c["metrics"], "ic_t", 2),
                 c.get("bh_significant", False), "; ".join(c.get("strong_reasons", []))]
                for c in rejected])

    _write_csv(P.art("tcost"),
               ["name", "kind", "gross_spread", "avg_turnover", "net_10bps", "net_25bps", "net_50bps"],
               [[c["name"], c["kind"], x8._g(c["metrics"], "mean_spread"),
                 x8._g(c["metrics"], "avg_turnover"), x8._g(c["metrics"], "net_spread_10bps"),
                 x8._g(c["metrics"], "net_spread_25bps"), x8._g(c["metrics"], "net_spread_50bps")]
                for c in candidates])
    _write_csv(P.art("cohort_stability"),
               ["name", "kind", "family", "ic_old", "t_old", "ic_new", "t_new",
                "both_cohorts_positive", "old_cohort_only"],
               [[c["name"], c["kind"], c["family"], c.get("ic_old"), c.get("t_old"), c.get("ic_new"),
                 c.get("t_new"),
                 bool(c.get("ic_old") is not None and c.get("ic_new") is not None
                      and (c.get("ic_old") or 0) > 0 and (c.get("ic_new") or 0) > 0),
                 bool(c.get("ic_old") is not None and (c.get("ic_old") or 0) > 0
                      and (c.get("ic_new") is None or (c.get("ic_new") or 0) <= 0))]
                for c in candidates])
    _write_csv(P.art("subperiod"),
               ["name", "kind", "first_half_ic", "second_half_ic", "subperiod_stable", "n_months"],
               [[c["name"], c["kind"], x8._g(c["metrics"], "h1_ic"), x8._g(c["metrics"], "h2_ic"),
                 c["metrics"].get("subperiod_stable", False), c["metrics"].get("n_months", 0)]
                for c in candidates])
    _write_csv(P.art("sector_conc"),
               ["name", "kind", "top_sector", "top_sector_share", "hhi", "single_sector_dominated"],
               [[c["name"], c["kind"], c["metrics"].get("top_sector", ""),
                 x8._g(c["metrics"], "top_sector_share"), x8._g(c["metrics"], "hhi"),
                 bool(_finite(c["metrics"].get("top_sector_share"))
                      and c["metrics"]["top_sector_share"] > x8.STRONG_MAX_SECTOR_SHARE)]
                for c in candidates])

    _write_csv(P.art("leakage_audit"), ["check", "status", "detail"],
               _leakage_audit_rows(ev, norm_csvs, pit_audit))

    _write_csv(P.art("next_commands"), ["action", "family", "command"],
               [[r["action"], r["family"], r["command"]] for r in next_rows])

    rows, clean = _secret_safety_audit(P.out)
    _write_csv(P.art("secret_audit"), ["file", "clean", "keyed_url_marker", "key_value_present"],
               [[r["file"], r["clean"], r["keyed_url_marker"], r["key_value_present"]] for r in rows])
    log.step("artifacts", "DONE", "wrote %d required artifacts" % len(_REQUIRED_ARTIFACTS))
    return clean


# --------------------------------------------------------------------------- #
# J. Report / plan / summary.
# --------------------------------------------------------------------------- #
def _empty_ev():
    import pandas as pd
    return pd.DataFrame({"ticker": [], "entry_date": []})


def _build_report(P, decision, rationale, next_rows, preflight_ok, missing_required, norgate,
                  attempts, acq, campaign, candidates, strong, constrained, rejected, horizon_rows,
                  exhausted_labels, n_tickers, n_events, universe_size, max_tickers,
                  total_requests, request_ceiling, panel_ok, live, leak_clean, as_of) -> Dict:
    best = x8._best_candidate(candidates)
    by_provider_requests: Dict[str, int] = {}
    by_provider_tickers: Dict[str, int] = {}
    for fam, st in acq["fam_state"].items():
        prov = st.get("provider") or "(none)"
        by_provider_requests[prov] = by_provider_requests.get(prov, 0) + int(st.get("requests", 0))
        by_provider_tickers[prov] = by_provider_tickers.get(prov, 0) + int(st.get("acquired", 0))

    families_acquired = sorted({f["family"] for f in campaign["fam_results"]
                                if f["max_coverage"] > 0})
    families_blocked = sorted({r["family"] for r in attempts if r["entitlement"] == ENT_BLOCKED}
                              - set(families_acquired))
    blocked_detail = [{"family": r["family"], "provider": r["provider"],
                       "endpoint": r["endpoint_redacted"], "http_status": r["http_status"],
                       "entitlement": r["entitlement"]}
                      for r in attempts if r["entitlement"] == ENT_BLOCKED]

    horizons_tested = sorted({r["horizon_days"] for r in horizon_rows})
    return {
        "phase": PHASE, "as_of": as_of, "decision": decision, "decision_rationale": rationale,
        "exact_next_command": (next_rows[0]["command"] if next_rows else ""),
        "live_mode": live, "allowed_decisions": list(ALLOWED_DECISIONS),
        "key_visibility_preflight_ok": preflight_ok,
        "key_visibility_missing_required": missing_required,
        "required_visible_keys": list(REQUIRED_VISIBLE_KEYS),
        "optional_missing_keys": list(OPTIONAL_MISSING_KEYS),
        "norgate_foundation": {"usable_now": bool(norgate.get("panel_ok")),
                               "reuse_or_rebuild": norgate.get("reuse_or_rebuild"),
                               "last_panel_month": norgate.get("last_month"),
                               "rebuild_can_run_now": norgate.get("rebuild_can_run_now"),
                               "rebuild_command": NORGATE_REBUILD_COMMAND},
        "data_families_acquired": families_acquired,
        "data_families_blocked": families_blocked,
        "entitlement_blocked_detail": blocked_detail,
        "provider_purchase_required": [
            {"family": f["family"], "paid_unlock": f["paid_unlock"]}
            for f in MISSING_ALPHA_FAMILIES
            if not any(r["family"] == f["family"] and r["entitlement"] == ENT_VERIFIED
                       for r in attempts)],
        "requests_by_provider": by_provider_requests,
        "tickers_by_provider": by_provider_tickers,
        "total_requests": total_requests, "request_ceiling": request_ceiling,
        "normalized_rows_by_family": {f["family"]: f["norm_rows"] for f in campaign["fam_results"]},
        "feature_coverage_by_family": {f["family"]: f["max_coverage"]
                                       for f in campaign["fam_results"]},
        "feature_coverage_diagnosis": {f["family"]: f["diagnosis"]
                                       for f in campaign["fam_results"]},
        "horizons_tested": horizons_tested,
        "scoreable_tickers": n_tickers, "scoreable_events": n_events, "universe_size": universe_size,
        "max_tickers": max_tickers,
        "scenarios_tested": sum(1 for c in candidates if c["kind"] == "scenario"),
        "models_tested": sum(1 for c in candidates if c["kind"] == "model"),
        "strong_alpha_found": bool(strong), "n_strong": len(strong),
        "n_constrained": len(constrained), "n_rejected": len(rejected),
        "best_candidate": (best["name"] if best else None),
        "best_candidate_t_stat": (x8._g(best["metrics"], "ic_t", 2) if best else None),
        "best_candidate_family": (best["family"] if best else None),
        "best_candidate_accept_reject": ("ACCEPTED_STRONG" if strong else
                                         ("REJECTED_BELOW_GATE" if best else "NO_CANDIDATE")),
        "exhausted_data_families": exhausted_labels,
        "panel_present": panel_ok, "secret_safety_leak_scan_clean": leak_clean,
        "api_key_printed": False, "api_key_written_to_disk": False,
        "required_artifacts": list(_ARTIFACTS.values()),
        "exact_next_commands": next_rows,
    }


def _phase10b_plan(decision, next_rows, strong, campaign) -> Dict:
    return {"from_phase": PHASE, "decision": decision, "next_phase": "10-B",
            "exact_next_commands": next_rows,
            "missing_alpha_families": [f["family"] for f in MISSING_ALPHA_FAMILIES],
            "strong_candidates": [c["name"] for c in strong],
            "next_steps": (["Productize the promoted strong missing-alpha signal (paper-only, "
                            "manual-review, NO orders/automation)."] if strong else
                           ["Acquire the next bounded missing-alpha batch / upgrade the exact paid "
                            "provider recorded in provider_purchase_required.csv, then re-run the "
                            "broad 8-X gate + horizon sweep."])}


def _print_summary(report: Dict) -> None:
    print("[10-A] decision=%s | preflight_ok=%s | acquired=%s | blocked=%s | requests=%s | "
          "scenarios=%s models=%s horizons=%s | strong=%s | best=%s (t=%s) | leak_clean=%s"
          % (report["decision"], report["key_visibility_preflight_ok"],
             ",".join(report["data_families_acquired"]) or "-",
             ",".join(report["data_families_blocked"]) or "-", report["total_requests"],
             report["scenarios_tested"], report["models_tested"], report["horizons_tested"],
             report["strong_alpha_found"], report["best_candidate"],
             report["best_candidate_t_stat"], report["secret_safety_leak_scan_clean"]))


# --------------------------------------------------------------------------- #
# K. Orchestration (single bounded campaign; cycle loop is resumable + ceiling-bounded).
# --------------------------------------------------------------------------- #
def run(out_dir: Optional[Path] = None, data_dir: Optional[Path] = None, *,
        price_csv: Optional[Path] = None, phase8v_dir: Optional[Path] = None,
        sector_csv: Optional[Path] = None, macro: Optional[Dict[str, Path]] = None,
        phase8n_dir: Optional[Path] = None, phase8r_dir: Optional[Path] = None,
        as_of: str = AS_OF, live: bool = False, refresh: bool = False,
        transports: Optional[Dict[str, Callable]] = None,
        max_tickers: int = DEFAULT_MAX_TICKERS,
        max_requests: int = DEFAULT_MAX_REQUESTS_PER_RUN, max_cycles: int = DEFAULT_MAX_CYCLES,
        request_ceiling: int = DEFAULT_TOTAL_REQUEST_CEILING,
        max_scenarios: int = DEFAULT_MAX_SCENARIOS, max_models: int = DEFAULT_MAX_MODELS,
        verbose: bool = True) -> Dict:
    P = _Paths(out_dir, data_dir)
    P.out.mkdir(parents=True, exist_ok=True)
    log = t8._Log(verbose)
    try:
        import pandas as pd  # noqa: F401

        # 0. Key-visibility preflight (always first).
        preflight_rows, preflight_ok, missing_required = key_visibility_preflight(transports)
        for r in preflight_rows:
            log.step("preflight", r["visibility"], "%s required=%s" % (r["env_var"], r["required"]))

        # 1. Norgate foundation (reuse 9-C verification - pure read, reuse-vs-rebuild).
        norgate_rows, norgate = c9.verify_norgate_foundation(as_of, log)

        # 2. Expanded earnings-event panel + cohort tag + Norgate liquidity proxy (reuse 8-W/8-S).
        panel = Path(price_csv) if price_csv else x8._EXPANDED_PANEL
        P_score = s8._Paths(out_dir=P.out, data_dir=data_dir, phase8n_dir=phase8n_dir,
                            price_csv=panel, sector_csv=sector_csv, macro=macro,
                            phase8r_dir=phase8r_dir)
        ev, stats, _audit = w8.build_expanded_ev(P_score, as_of, log)
        panel_ok = not (getattr(ev, "empty", True) or stats.get("events_usable", 0) == 0)
        if panel_ok:
            v8 = _read_json((phase8v_dir or x8._PHASE8V_DIR) / x8._PHASE8V_REPORT)
            new_set = set(str(t).upper() for t in (v8.get("newly_scoreable_tickers") or []))
            ev = w8.tag_cohort(ev, new_set)
            eod_norm = Path(P_score.eodhd_dir) / "normalized" / "eod_prices"
            ev = w8.attach_liquidity_proxy(ev, panel, extra_csvs=[s8._PRICE_CACHE_CSV],
                                           eod_norm_dir=eod_norm)
            universe = sorted(ev["ticker"].astype(str).str.upper().unique())
            n_tickers = stats.get("tickers_usable", 0) or len(universe)
            n_events = stats.get("events_usable", 0) or int(len(ev))
        else:
            universe, n_tickers, n_events = [], 0, 0

        # 3. Probe every (family, provider) - one blocked provider never stops the sweep.
        attempts = probe_all_families(live, transports, log) if panel_ok else []

        # 4. Bounded, resumable acquisition across accessible families + cycle loop (ceiling-bounded).
        total_requests = 0
        acq = {"fam_state": {}, "progress_rows": [], "raw_rows": [], "total_requests": 0}
        if panel_ok:
            for cycle in range(max(1, max_cycles)):
                budget = min(max_requests, max(0, request_ceiling - total_requests))
                if budget <= 0:
                    break
                cyc = acquire_missing_families(
                    universe, attempts, P.data_root, max_tickers=max_tickers, request_budget=budget,
                    live=live, transports=transports, skip_existing=not refresh, log=log)
                total_requests += cyc["total_requests"]
                # merge cycle results
                acq["progress_rows"].extend(cyc["progress_rows"])
                acq["raw_rows"].extend(cyc["raw_rows"])
                for fam, st in cyc["fam_state"].items():
                    prev = acq["fam_state"].get(fam)
                    if prev is None:
                        acq["fam_state"][fam] = dict(st)
                    else:
                        prev["requests"] += st["requests"]
                        prev["acquired"] += st["acquired"]
                        prev["progress"] = st["progress"]
                        if st["status"] == "ACQUIRED":
                            prev["status"] = "ACQUIRED"
                log.step("cycle", "DONE", "cycle %d: +%d requests (total %d / ceiling %d)"
                         % (cycle + 1, cyc["total_requests"], total_requests, request_ceiling))
                if cyc["total_requests"] == 0:           # nothing new acquired -> resumable batch done
                    break
            acq["total_requests"] = total_requests

        # 5. Normalize each accessible family PIT (provider-aware), gitignored.
        norm_csvs: Dict[str, Path] = {}
        norm_manifest: List[Dict] = []
        pit_audit: List[Dict] = []
        if panel_ok:
            for fam in MISSING_ALPHA_FAMILIES:
                st = acq["fam_state"].get(fam["family"], {})
                prov = st.get("provider")
                raw_dir = st.get("raw_dir")
                if not prov or raw_dir is None:
                    # no accessible provider for this family -> nothing acquired, nothing to normalize
                    # (no stray writes outside a gitignored provider tree).
                    norm_csvs[fam["family"]] = None
                    continue
                provider_dir = P.data_root / y8._provider_slug(prov)
                y8._ensure_gitignore(provider_dir, prov)
                csv_path, man, aud = normalize_family_pit(fam, prov, raw_dir, provider_dir, as_of, log)
                norm_csvs[fam["family"]] = csv_path
                norm_manifest += man
                pit_audit += aud

        # 6. Feature factory + interactions through the 8-X gate + horizon sweep.
        if panel_ok:
            campaign = run_campaign(ev, norm_csvs, max_scenarios=max_scenarios,
                                    max_models=max_models, log=log)
            horizon_rows = horizon_sweep(campaign["ev_ix"], norm_csvs, log)
        else:
            campaign = {"fam_results": [], "candidates": [], "catalog_rows": [], "scenario_specs": [],
                        "model_specs": [], "coverage_rows": [], "ev_ix": _empty_ev()}
            horizon_rows = []
        candidates = campaign["candidates"]

        # 7. Multiple-testing + broad strong gate over ALL candidates.
        x8._finalize_gates(candidates, n_tickers, n_events, STRONG_MIN_TICKERS, STRONG_MIN_EVENTS)
        strong = [c for c in candidates if c["status"] == "strong"]
        constrained = [c for c in candidates if c["status"] == "constrained"]
        rejected = [c for c in candidates if c["status"] == "rejected"]
        exhausted_search = bool(panel_ok and universe and len(universe) <= max_tickers)
        exh_rows, exhausted_labels = x8.data_family_exhaustion(candidates, exhausted_search)

        # 8. Decision.
        decision, rationale, next_rows = derive_decision(
            panel_ok=panel_ok, attempts=attempts, acq=acq, fam_results=campaign["fam_results"],
            candidates=candidates, universe_size=len(universe), max_tickers=max_tickers,
            total_requests=total_requests, request_ceiling=request_ceiling)

        # 9. Resume state (acquisition is resumable via skip-existing on disk).
        _write_json(P.resume_state, {"as_of": as_of, "max_tickers": max_tickers,
                                     "total_requests": total_requests,
                                     "acquired_by_family": {k: v.get("acquired", 0)
                                                            for k, v in acq["fam_state"].items()}})

        # 10. Artifacts + report.
        leak_clean = write_artifacts(
            P, preflight_rows=preflight_rows, attempts=attempts, acq=acq, campaign=campaign,
            candidates=candidates, strong=strong, rejected=rejected, exh_rows=exh_rows,
            horizon_rows=horizon_rows, ev=ev if panel_ok else _empty_ev(), norm_csvs=norm_csvs,
            pit_audit=pit_audit, norm_manifest=norm_manifest, next_rows=next_rows, log=log)
        # Norgate manifest is part of the foundation evidence (write alongside).
        _write_csv(P.out / "norgate_foundation_manifest.csv", ["check", "value", "detail"],
                   [[r["check"], r["value"], r["detail"]] for r in norgate_rows])

        report = _build_report(
            P, decision, rationale, next_rows, preflight_ok, missing_required, norgate, attempts,
            acq, campaign, candidates, strong, constrained, rejected, horizon_rows, exhausted_labels,
            n_tickers, n_events, len(universe), max_tickers, total_requests, request_ceiling,
            panel_ok, live, leak_clean, as_of)
        _write_json(P.art("report"), report)
        _write_json(P.art("next_plan"), _phase10b_plan(decision, next_rows, strong, campaign))
        _print_summary(report)
        return report
    except Exception as exc:                               # pragma: no cover - defensive
        import traceback
        detail = "%s: %s" % (type(exc).__name__, exc)
        log.step("run", "ERROR", detail)
        report = {"phase": PHASE, "decision": DEC_ERROR, "decision_rationale": detail,
                  "repro_command": ("python research/run_phase10a_missing_alpha_data_acquisition.py "
                                    "--live"),
                  "traceback": traceback.format_exc()}
        try:
            _write_json(P.art("report"), report)
        except Exception:
            pass
        return report


# --------------------------------------------------------------------------- #
# L. CLI.
# --------------------------------------------------------------------------- #
def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 10-A - Missing Alpha Data Acquisition")
    p.add_argument("--live", action="store_true",
                   help="probe + acquire live with the visible keys (read from env, never written)")
    p.add_argument("--refresh", action="store_true",
                   help="overwrite existing raw payloads (deeper from/to history) instead of skipping")
    p.add_argument("--out-dir", default=None)
    p.add_argument("--data-dir", default=None)
    p.add_argument("--as-of", default=AS_OF)
    p.add_argument("--deep-from", default=DEEP_FROM)
    p.add_argument("--max-tickers", type=int, default=DEFAULT_MAX_TICKERS)
    p.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS_PER_RUN)
    p.add_argument("--max-cycles", type=int, default=DEFAULT_MAX_CYCLES)
    p.add_argument("--request-ceiling", type=int, default=DEFAULT_TOTAL_REQUEST_CEILING)
    p.add_argument("--max-scenarios", type=int, default=DEFAULT_MAX_SCENARIOS)
    p.add_argument("--max-models", type=int, default=DEFAULT_MAX_MODELS)
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    a = _parse_args(argv)
    global DEEP_FROM
    if a.deep_from:
        DEEP_FROM = a.deep_from
    report = run(out_dir=a.out_dir, data_dir=a.data_dir, as_of=a.as_of, live=a.live,
                 refresh=a.refresh, max_tickers=a.max_tickers, max_requests=a.max_requests,
                 max_cycles=a.max_cycles, request_ceiling=a.request_ceiling,
                 max_scenarios=a.max_scenarios, max_models=a.max_models, verbose=not a.quiet)
    return 0 if report.get("decision") in ALLOWED_DECISIONS else 1


if __name__ == "__main__":
    raise SystemExit(main())
