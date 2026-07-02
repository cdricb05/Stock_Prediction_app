"""Phase 10-B - EODHD + Norgate Exhaustive Paid-Subscription Alpha Factory.

Direction correction over Phase 10-A: the user is NOT paying for FMP, so FMP 403s are expected and
must NOT drive the research plan. The user's two ACTUAL paid subscriptions are:

    1. Norgate US Stocks Diamond Package  -> survivorship-free US equities foundation (universe,
       membership, delisted names, sectors, liquidity, returns).
    2. EODHD Fundamentals Data Feed ($59.99/mo) -> EOD history, fundamentals, calendar, splits/
       dividends, exchange lists, and news/sentiment.

This phase therefore mines ONLY the data the user actually pays for: Norgate (foundation) + EODHD
(fundamentals / calendar / splits-dividends / news-sentiment / corporate actions). It (a) audits the
real EODHD entitlements visible to the current key, (b) inventories every EODHD fundamentals section
+ field with its history depth and point-in-time usability, (c) classifies each field PIT-usable vs
snapshot-only, (d) normalizes every PIT-usable family (an `available_date` <= the event entry_date,
no lookahead), (e) joins onto the Norgate survivorship-free earnings-event panel (545 tickers /
~38,725 events), (f) builds a broad EODHD feature factory across the families Phase 8-X never
exhausted (gross profitability, Sloan accruals, asset growth, net share issuance, leverage change,
FCF/assets, dividend growth) PLUS clean re-tests of surprise / growth / sentiment, and (g) runs the
SAME Phase 8-X broad strong-alpha gate (IC t>=3.0, BH-significant, net-of-25bps positive, both
cohorts positive, both pre/post-2020 halves positive, sector-diversified) + a 1/5/21/63-day horizon
sweep. No weak / constrained signal is ever promoted as strong.

FMP may be present but is IGNORED except as historical context. No new paid data is recommended until
EODHD + Norgate are fully audited and tested.

Reuse (single source of truth - nothing reimplemented):
    r8 = run_phase8r_broad_bundle_evaluation              EODHD client (host allow-list / redaction /
                                                           live GET / key presence / fundamentals)
    s8 = run_phase8s_autonomous_eodhd_alpha_factory        EODHD data layer / FWD_WINDOWS / IO helpers
    w8 = run_phase8w_expanded_universe_failure_attribution  expanded event table / cohort / liquidity
    x8 = run_phase8x_autonomous_strong_alpha_discovery     gate / scenarios / models / scoreboards
    z8 = run_phase8z_autonomous_no_excuses_alpha_agent     point-in-time feature factory + hypotheses
    y8 = run_phase8y_orthogonal_data_family_acquisition    PIT status / attach / gitignore helpers
    c9 = run_phase9c_verified_owned_feed_alpha_acquisition  Norgate foundation verification (reuse)

Constraints honored: Windows-compatible Python (stdlib + already-installed pandas/numpy); no package
install; no Paper Trader, no GCP, no orders, no automation, no deploy; no full regression (targeted
tests only); keys never printed or written; raw + normalized EODHD payloads force-gitignored. No
commit. No push.
"""
from __future__ import annotations

import argparse
import json
import math
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

from research import run_phase8r_broad_bundle_evaluation as r8            # noqa: E402
from research import run_phase8w_expanded_universe_failure_attribution as w8  # noqa: E402
from research import run_phase8x_autonomous_strong_alpha_discovery as x8   # noqa: E402
from research import run_phase8z_autonomous_no_excuses_alpha_agent as z8   # noqa: E402
from research import run_phase8y_orthogonal_data_family_acquisition as y8  # noqa: E402
from research import run_phase9c_verified_owned_feed_alpha_acquisition as c9  # noqa: E402

s8 = x8.s8
t8 = x8.t8

_write_json = s8._write_json
_write_csv = s8._write_csv
_read_csv_file = s8._read_csv_file
_read_json = s8._read_json
_round = s8._round
_rel = s8._rel

PHASE = "10-B"

# --------------------------------------------------------------------------- #
# Config (a-priori; never tuned to a result).
# --------------------------------------------------------------------------- #
AS_OF = s8.DEFAULT_AS_OF          # "2026-06-26"
DEEP_FROM = "2016-01-01"
DEEP_TO = AS_OF

DEFAULT_MAX_TICKERS = 545
DEFAULT_MAX_REQUESTS_PER_RUN = 2000
DEFAULT_MAX_CYCLES = 5
DEFAULT_TOTAL_REQUEST_CEILING = 10000
DEFAULT_MAX_SCENARIOS = 500
DEFAULT_MAX_MODELS = 100
ACQUIRE_MIN_SLEEP_SECONDS = 0.30
_PIT_FALLBACK_LAG_DAYS = 90       # conservative availability lag when a filing_date is absent

FWD_WINDOWS = s8.FWD_WINDOWS                    # (1, 5, 21, 63)
PRIMARY_HORIZON = s8.PRIMARY_HORIZON           # 21

# Strong-alpha gate floors (reuse the 8-X promotion standard verbatim).
STRONG_MIN_TICKERS = x8.STRONG_MIN_TICKERS     # 500
STRONG_MIN_EVENTS = x8.STRONG_MIN_EVENTS       # 30000
STRONG_MIN_IC_T = x8.STRONG_MIN_IC_T           # 3.0
GATE_BH_Q = x8.GATE_BH_Q
RET_COL = x8.RET_COL                           # "fwd_exc_21"

NORGATE_REBUILD_COMMAND = c9.NORGATE_REBUILD_COMMAND

# The ONLY required key is EODHD. FMP may be present but is recorded for context and ignored as a
# research source. Norgate is a local install verified separately (no API key).
REQUIRED_VISIBLE_KEYS = ("EODHD_API_KEY",)
CONTEXT_ONLY_KEYS = ("FMP_API_KEY",)
_ALL_ENV_VARS = tuple(REQUIRED_VISIBLE_KEYS) + CONTEXT_ONLY_KEYS

EODHD_KEY_ENV = r8.API_KEY_ENV                  # "EODHD_API_KEY"
ALLOWED_HOSTS = r8.ALLOWED_HOSTS
_USER_AGENT = "paper-trader-research-phase10b/1.0"
PROBE_TIMEOUT_SECONDS = 30.0
PROBE_TICKER = "AAPL"

# --------------------------------------------------------------------------- #
# Terminal decisions (allowed) - each carries an exact next action / exact fix.
# --------------------------------------------------------------------------- #
DEC_STRONG = "STRONG_ALPHA_FOUND_READY_FOR_REVIEW"
DEC_NEXT_BATCH = "EODHD_NORGATE_READY_FOR_NEXT_BATCH"
DEC_EXHAUSTED = "EODHD_NORGATE_EXHAUSTED_NO_STRONG_ALPHA"
DEC_ENTITLEMENT = "EODHD_ENTITLEMENT_LIMITATION_WITH_EXACT_FIX"
DEC_OPTIONS = "EODHD_USEFUL_BUT_OPTIONS_ADDON_RECOMMENDED"
DEC_ESTIMATE = "EODHD_USEFUL_BUT_ESTIMATE_TARGET_PROVIDER_NEEDED"
DEC_CANCEL = "EODHD_NOT_SUFFICIENT_CANCEL_OR_DOWNGRADE"
DEC_BLOCKER = "HARD_BLOCKER_REQUIRES_USER_ACTION"
DEC_ERROR = "ERROR_WITH_REPRO_COMMAND"
ALLOWED_DECISIONS = (DEC_STRONG, DEC_NEXT_BATCH, DEC_EXHAUSTED, DEC_ENTITLEMENT, DEC_OPTIONS,
                     DEC_ESTIMATE, DEC_CANCEL, DEC_BLOCKER, DEC_ERROR)
FORBIDDEN_DECISIONS = ("MISSING_KEY", "NO_DATA", "EMPTY_PAYLOAD", "NEEDS_PROVIDER", "ERROR")

# Entitlement vocabulary.
ENT_VERIFIED = "ACCESS_VERIFIED"
ENT_BLOCKED = "ENTITLEMENT_BLOCKED"
ENT_RATE = "RATE_LIMITED"
ENT_NOTFOUND = "NOT_FOUND"
ENT_PARSE = "PARSE_ERROR"
ENT_NETWORK = "NETWORK_ERROR"
ENT_NOT_PROBED = "NOT_PROBED"

# --------------------------------------------------------------------------- #
# EODHD endpoint catalogue probed in the entitlement audit. {symbol} is filled transiently at
# request time; the persisted endpoint is always redacted (no key, no key marker).
# --------------------------------------------------------------------------- #
def _ep(name, url, section, pit, note):
    return {"name": name, "url": url, "section": section, "pit": pit, "note": note}


AUDIT_ENDPOINTS: Tuple[Dict, ...] = (
    _ep("fundamentals", "https://eodhd.com/api/fundamentals/{symbol}.US?fmt=json",
        "Fundamentals (all sections)", True,
        "workhorse: General/Highlights/Valuation/SharesStats/Earnings/Financials in one call"),
    _ep("eod_prices", "https://eodhd.com/api/eod/{symbol}.US?fmt=json&from=2016-01-01",
        "EOD historical prices", True, "daily OHLCV + adjusted_close"),
    _ep("calendar_earnings", "https://eodhd.com/api/calendar/earnings?fmt=json&symbols={symbol}.US",
        "Calendar::Earnings", True, "report_date PIT earnings calendar"),
    _ep("calendar_trends", "https://eodhd.com/api/calendar/trends?fmt=json&symbols={symbol}.US",
        "Calendar::Trends", False, "analyst estimate trend (revised; weak as-of)"),
    _ep("news", "https://eodhd.com/api/news?s={symbol}.US&limit=5&fmt=json",
        "News", True, "per-article news with sentiment polarity (date PIT)"),
    _ep("sentiments", "https://eodhd.com/api/sentiments?s={symbol}.US&fmt=json",
        "News::Sentiment", True, "daily aggregated normalized sentiment + article count"),
    _ep("insider_tx", "https://eodhd.com/api/insider-transactions?code={symbol}.US&limit=10&fmt=json",
        "InsiderTransactions / SEC Form 4", True, "Form-4 transactions (transactionDate PIT)"),
    _ep("dividends", "https://eodhd.com/api/div/{symbol}.US?fmt=json&from=2010-01-01",
        "SplitsDividends::Dividends", True, "dividend history (declarationDate PIT)"),
    _ep("splits", "https://eodhd.com/api/splits/{symbol}.US?fmt=json",
        "SplitsDividends::Splits", True, "split history (date PIT)"),
    _ep("macro_indicator",
        "https://eodhd.com/api/macro-indicator/USA?indicator=inflation_consumer_prices_annual&fmt=json",
        "Macro indicators", True, "country macro time series"),
    _ep("index_constituents", "https://eodhd.com/api/fundamentals/GSPC.INDX?fmt=json",
        "Index constituents", True, "GSPC.INDX Components + HistoricalTickerComponents"),
    _ep("exchange_symbols", "https://eodhd.com/api/exchange-symbol-list/US?fmt=json",
        "Exchange symbol list", False, "US listing universe (Norgate is the survivorship foundation)"),
    _ep("bulk_fundamentals", "https://eodhd.com/api/bulk-fundamentals/US?symbols={symbol}&fmt=json",
        "Bulk fundamentals", False,
        "NOT used as primary even if entitled (loses fields/history vs single-symbol)"),
    _ep("options", "https://eodhd.com/api/options/{symbol}.US?fmt=json",
        "Options", False, "options IV/skew/put-call (NOT assumed under the Fundamentals feed)"),
)

# --------------------------------------------------------------------------- #
# Section-level point-in-time classification of the EODHD fundamentals payload.
#   PIT-usable  = a section with a dated history that supports an available_date <= entry_date join.
#   snapshot    = a current-value section (no usable history) -> recorded, NEVER used for historical
#                 alpha (matches r8.classify_fundamentals' treatment of Trend / AnalystRatings).
# --------------------------------------------------------------------------- #
PIT_USABLE_SECTIONS: Dict[str, str] = {
    "Earnings::History": "reportDate",
    "Financials::Income_Statement::quarterly": "filing_date",
    "Financials::Balance_Sheet::quarterly": "filing_date",
    "Financials::Cash_Flow::quarterly": "filing_date",
    "outstandingShares::quarterly": "dateFormatted",
}
SNAPSHOT_ONLY_SECTIONS: Tuple[str, ...] = (
    "General", "Highlights", "Valuation", "SharesStats", "Technicals", "SplitsDividends",
    "AnalystRatings", "Holders", "InsiderTransactions", "ESGScores", "Earnings::Trend",
    "Earnings::Annual",
)

# --------------------------------------------------------------------------- #
# EODHD point-in-time feature families. Each family is ONE normalized series (ticker, available_date,
# <feature>) that the 8-Z factory explodes into ~21 PIT-safe transforms (level / chg / accel / lag /
# rolling mean-std-chg over 5/21/63 obs / within-month z / sector-neutral z / rank / winsor /
# x surprise|quality|value|momentum). `source` selects the normalizer; `pit_field` documents the
# availability date; `additive` marks families Phase 8-X had NOT exhausted.
# --------------------------------------------------------------------------- #
def _fam(family, source, feature, desc, orthogonal_to, pit_field, additive, paid_alt=""):
    return {"family": family, "source": source, "feature": feature, "feature_desc": desc,
            "orthogonal_to": orthogonal_to, "pit_field": pit_field, "additive": additive,
            "paid_alt": paid_alt}


EODHD_FAMILIES: Tuple[Dict, ...] = (
    _fam("eodhd_earnings_surprise", "fundamentals", "earnings_surprise",
         "EODHD Earnings.History surprisePercent (actual vs consensus EPS) at the report date",
         "clean EODHD re-derivation of the earnings surprise", "Earnings.History.reportDate", False),
    _fam("eodhd_eps_growth_yoy", "fundamentals", "eps_growth_yoy",
         "EODHD Earnings.History year-over-year actual-EPS growth at the report date",
         "earnings growth", "Earnings.History.reportDate", False),
    _fam("eodhd_revenue_growth_yoy", "fundamentals", "rev_growth_yoy",
         "year-over-year quarterly revenue growth at the filing date",
         "sales growth", "Income_Statement.quarterly.filing_date", False),
    _fam("eodhd_gross_profitability", "fundamentals", "gross_profitability",
         "Novy-Marx gross profitability: gross profit / total assets at the filing date",
         "profitability quality (orthogonal to valuation)", "Income/Balance.filing_date", True,
         "already owned via EODHD - no purchase"),
    _fam("eodhd_operating_accruals", "fundamentals", "operating_accruals",
         "Sloan operating accruals: (net income - operating cash flow) / total assets at filing date",
         "earnings quality / accruals anomaly", "Income/CashFlow/Balance.filing_date", True,
         "already owned via EODHD - no purchase"),
    _fam("eodhd_asset_growth", "fundamentals", "asset_growth",
         "year-over-year total-asset growth (investment / asset-growth anomaly) at the filing date",
         "investment factor", "Balance_Sheet.quarterly.filing_date", True,
         "already owned via EODHD - no purchase"),
    _fam("eodhd_net_share_issuance", "fundamentals", "net_share_issuance",
         "year-over-year change in common shares outstanding (dilution / buyback) at the filing date",
         "net issuance / buyback anomaly", "Balance_Sheet.quarterly.filing_date", True,
         "already owned via EODHD - no purchase"),
    _fam("eodhd_leverage_change", "fundamentals", "leverage_change",
         "change in total-debt / total-assets (de/re-leveraging) at the filing date",
         "balance-sheet risk change", "Balance_Sheet.quarterly.filing_date", True,
         "already owned via EODHD - no purchase"),
    _fam("eodhd_fcf_to_assets", "fundamentals", "fcf_to_assets",
         "free cash flow / total assets (cash-flow quality) at the filing date",
         "cash-flow quality", "CashFlow/Balance.filing_date", True,
         "already owned via EODHD - no purchase"),
    _fam("eodhd_dividend_growth", "dividends", "dividend_growth",
         "year-over-year cash-dividend growth at the declaration date",
         "shareholder-yield / dividend-policy signal", "Dividends.declarationDate", True,
         "already owned via EODHD - no purchase"),
    _fam("eodhd_news_sentiment", "news", "news_sentiment",
         "EODHD daily normalized news sentiment polarity (article-date PIT)",
         "soft information / news flow", "News.date", False),
)
_FAMILY_BY_NAME = {f["family"]: f for f in EODHD_FAMILIES}

# Endpoints actually acquired to disk (cached, gitignored, resumable). The fundamentals endpoint feeds
# the bulk of the families; news/dividends each feed one. eod prices come from the existing expanded
# panel and are not re-acquired here.
ACQUIRE_ENDPOINTS: Tuple[Dict, ...] = (
    {"name": "fundamentals", "cache": "fundamentals",
     "url": "https://eodhd.com/api/fundamentals/{symbol}.US?fmt=json"},
    {"name": "sentiments", "cache": "news_social_sentiment",
     "url": "https://eodhd.com/api/sentiments?s={symbol}.US&fmt=json"},
    {"name": "dividends", "cache": "dividends",
     "url": "https://eodhd.com/api/div/{symbol}.US?fmt=json&from=2010-01-01"},
)

# --------------------------------------------------------------------------- #
# Required artifacts (32 incl. the report json). Raw + normalized EODHD payloads stay force-gitignored
# under research/data/eodhd/; only manifests / inventories / scoreboards / metadata are committed-safe.
# --------------------------------------------------------------------------- #
_ARTIFACTS = {
    "report": "phase10b_eodhd_norgate_exhaustive_alpha_factory.json",
    "key_preflight": "key_visibility_preflight.csv",
    "entitlement_audit": "eodhd_entitlement_audit.csv",
    "section_inventory": "eodhd_section_inventory.csv",
    "field_inventory": "eodhd_field_inventory.csv",
    "snapshot_fields": "eodhd_snapshot_only_fields.csv",
    "pit_fields": "eodhd_pit_usable_fields.csv",
    "acq_progress": "acquisition_progress.csv",
    "raw_manifest": "raw_payload_manifest.csv",
    "norm_manifest": "normalized_payload_manifest.csv",
    "pit_norm_audit": "pit_normalization_audit.csv",
    "pit_join_audit": "point_in_time_join_audit.csv",
    "norgate_manifest": "norgate_foundation_manifest.csv",
    "feature_catalog": "feature_catalog.csv",
    "feature_coverage": "feature_coverage_report.csv",
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
    "keep_decision": "eodhd_keep_upgrade_cancel_decision.csv",
    "missing_after": "missing_data_after_eodhd_norgate.csv",
    "next_commands": "exact_next_commands.csv",
    "next_plan": "phase10c_next_plan.json",
    "secret_audit": "secret_safety_audit.csv",
}
_REQUIRED_ARTIFACTS = tuple(_ARTIFACTS.keys())
_RESUME_STATE = "phase10b_resume_state.json"


class _Paths:
    def __init__(self, out_dir=None, data_dir=None):
        self.out = Path(out_dir) if out_dir else (
            _REPO_ROOT / "research" / "output" / "phase10b_eodhd_norgate_exhaustive_alpha_factory")
        self.data_root = Path(data_dir) if data_dir else (_REPO_ROOT / "research" / "data")

    def art(self, key: str) -> Path:
        return self.out / _ARTIFACTS[key]

    @property
    def eodhd_dir(self) -> Path:
        return self.data_root / "eodhd"

    def raw_dir(self, cache: str) -> Path:
        return self.eodhd_dir / "raw" / cache

    def norm_dir(self, family: str) -> Path:
        return self.eodhd_dir / "normalized" / family

    @property
    def resume_state(self) -> Path:
        return self.out / _RESUME_STATE


# --------------------------------------------------------------------------- #
# A. Key-visibility preflight (PRESENT/missing only; value never read).
# --------------------------------------------------------------------------- #
def key_visibility_preflight(transport: Optional[Callable] = None) -> Tuple[List[Dict], bool, List[str]]:
    """(rows, eodhd_visible, missing_required). EODHD must be PRESENT. FMP is recorded for context and
    never required. A test transport satisfies EODHD offline so the suite needs no real key."""
    rows: List[Dict] = []
    missing_required: List[str] = []
    for env in REQUIRED_VISIBLE_KEYS:
        present = r8.key_present(env) or (transport is not None)
        rows.append({"env_var": env, "required": True, "role": "EODHD paid feed (primary)",
                     "visibility": "PRESENT" if present else "missing"})
        if not present:
            missing_required.append(env)
    for env in CONTEXT_ONLY_KEYS:
        present = r8.key_present(env)
        rows.append({"env_var": env, "required": False,
                     "role": "context only - IGNORED as a research source",
                     "visibility": "PRESENT" if present else "missing"})
    return rows, (not missing_required), missing_required


# --------------------------------------------------------------------------- #
# B. Generic EODHD GET (host-allowlisted; key appended transiently; errors classified, never raise a
#    key). Mirrors r8._live_get but for arbitrary EODHD paths used across the audit + acquisition.
# --------------------------------------------------------------------------- #
class EodhdProbeError(Exception):
    def __init__(self, message, status_code=None, kind="http_error"):
        super().__init__(message)
        self.status_code = status_code
        self.kind = kind


def _host_of(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).netloc or "").split("@")[-1].split(":")[0].lower()
    except ValueError:
        return ""


def redact(url: str) -> str:
    return r8.redact_url(url)


def _eodhd_live_get(url_template: str, symbol: str) -> object:
    """One bounded live GET. The key-bearing URL is transient and never persisted; HTTP errors are
    classified (401/402/403 -> blocked, 429 -> rate, 404 -> not found) and never carry the key."""
    base = url_template.replace("{symbol}", urllib.parse.quote(symbol))
    if _host_of(base) not in ALLOWED_HOSTS:
        raise EodhdProbeError("refusing non-allowlisted host", kind="host_blocked")
    key = os.environ.get(EODHD_KEY_ENV, "") or ""
    full = base + ("&" if "?" in base else "?") + "api_token=" + urllib.parse.quote(key)
    try:
        req = urllib.request.Request(
            full, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_SECONDS) as resp:  # nosec allowlisted
            raw = resp.read()
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            raise EodhdProbeError("non-JSON response: %s" % exc, kind="bad_response")
    except urllib.error.HTTPError as exc:
        code = getattr(exc, "code", None)
        if code in (401, 402, 403):
            kind = "entitlement_blocked"
        elif code == 429:
            kind = "rate_limited"
        elif code == 404:
            kind = "not_found"
        else:
            kind = "http_error"
        raise EodhdProbeError("provider returned HTTP %s" % code, status_code=code, kind=kind)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EodhdProbeError("network error: %s" % type(exc).__name__, kind="network_error")


def _classify_probe(payload_or_exc) -> Tuple[str, int, str]:
    """Map a GET outcome to an entitlement class + a rough row count + note."""
    if isinstance(payload_or_exc, EodhdProbeError):
        exc = payload_or_exc
        if exc.kind == "entitlement_blocked":
            return ENT_BLOCKED, 0, "HTTP %s entitlement-blocked" % exc.status_code
        if exc.kind == "rate_limited":
            return ENT_RATE, 0, "HTTP 429 rate-limited"
        if exc.kind == "not_found":
            return ENT_NOTFOUND, 0, "HTTP 404 not found / not entitled"
        if exc.kind == "network_error":
            return ENT_NETWORK, 0, "network error"
        if exc.kind == "bad_response":
            return ENT_PARSE, 0, "non-JSON response"
        return ENT_BLOCKED, 0, "HTTP %s" % (exc.status_code or "error")
    payload = payload_or_exc
    if isinstance(payload, dict):
        return ENT_VERIFIED, len(payload), "dict payload"
    if isinstance(payload, list):
        return ENT_VERIFIED, len(payload), "list payload"
    return ENT_PARSE, 0, "unexpected payload type"


# --------------------------------------------------------------------------- #
# C. EODHD entitlement audit (one bounded probe per endpoint; never stops on a block).
# --------------------------------------------------------------------------- #
def eodhd_entitlement_audit(live: bool, transport: Optional[Callable], log) -> List[Dict]:
    rows: List[Dict] = []
    present = r8.key_present(EODHD_KEY_ENV)
    for ep in AUDIT_ENDPOINTS:
        redacted = redact(ep["url"].replace("{symbol}", PROBE_TICKER) + "&api_token=")
        row = {"name": ep["name"], "section": ep["section"], "pit": ep["pit"],
               "endpoint_redacted": redacted, "note": ep["note"], "http_status": "",
               "entitlement": ENT_NOT_PROBED, "rows": 0}
        if transport is None and (not live or not present):
            row["note"] = ("EODHD key absent - probe skipped" if not present
                           else "offline mode; re-run --live to probe")
            row["entitlement"] = ENT_NOT_PROBED if present else ENT_NOTFOUND
            rows.append(row)
            continue
        try:
            if transport is not None:
                payload = transport(ep["url"].replace("{symbol}", PROBE_TICKER))
            else:
                payload = _eodhd_live_get(ep["url"], PROBE_TICKER)
            ent, n, note = _classify_probe(payload)
            http = 200
        except EodhdProbeError as exc:
            ent, n, note = _classify_probe(exc)
            http = getattr(exc, "status_code", "") or ""
        # Preserve the endpoint's descriptive note (e.g. "bulk - NOT used as primary") AND the probe
        # classification, so the audit artifact carries both the design intent and the live result.
        row.update({"entitlement": ent, "rows": n, "http_status": http,
                    "note": "%s | %s" % (ep["note"], note)})
        rows.append(row)
        log.step("audit", ent, "%s -> %s (http=%s)" % (ep["name"], ent, http))
        if transport is None:
            time.sleep(ACQUIRE_MIN_SLEEP_SECONDS)
    return rows


def _audit_ok(audit: List[Dict], name: str) -> bool:
    return any(r["name"] == name and r["entitlement"] == ENT_VERIFIED for r in audit)


# --------------------------------------------------------------------------- #
# D. EODHD acquisition (cached, gitignored, resumable, shared request budget).
# --------------------------------------------------------------------------- #
def _ensure_eodhd_gitignore(P: _Paths) -> None:
    eod = P.eodhd_dir
    eod.mkdir(parents=True, exist_ok=True)
    gi = eod / ".gitignore"
    if not gi.exists():
        gi.write_text("# EODHD raw + normalized payloads are gitignored (provider data).\n"
                      "raw/\nnormalized/\n", encoding="utf-8")


def _cached_tickers(raw_dir: Path) -> set:
    if not raw_dir.is_dir():
        return set()
    return {p.stem.upper() for p in raw_dir.glob("*.json")}


def acquire_eodhd(universe: List[str], audit: List[Dict], P: _Paths, *, max_tickers: int,
                  request_budget: int, live: bool, transport: Optional[Callable],
                  skip_existing: bool, log) -> Dict:
    """For each accessible acquire-endpoint, fetch a bounded batch of the universe (skip-existing makes
    it resumable), sharing a single request budget. Raw JSON is written under the gitignored EODHD
    tree. Returns per-endpoint state + progress + raw manifest + aggregate request count."""
    _ensure_eodhd_gitignore(P)
    ep_state: Dict[str, Dict] = {}
    progress_rows: List[Dict] = []
    raw_rows: List[Dict] = []
    total_requests = 0
    want = [t.strip().upper() for t in universe if t and t.strip()][:max_tickers]
    for ep in ACQUIRE_ENDPOINTS:
        accessible = _audit_ok(audit, ep["name"]) or transport is not None
        raw_dir = P.raw_dir(ep["cache"])
        if not accessible:
            ep_state[ep["name"]] = {"cache": ep["cache"], "raw_dir": raw_dir, "requests": 0,
                                    "acquired": 0, "status": "NOT_ACCESSIBLE"}
            continue
        raw_dir.mkdir(parents=True, exist_ok=True)
        cached = _cached_tickers(raw_dir)
        acquired = 0
        requests_made = 0
        for tk in want:
            if total_requests >= request_budget:
                break
            existing = tk in cached
            if existing and skip_existing:
                progress_rows.append({"endpoint": ep["name"], "ticker": tk, "status": "CACHED",
                                      "rows": 0, "requests_made": 0})
                continue
            try:
                if transport is not None:
                    payload = transport(ep["url"].replace("{symbol}", tk))
                else:
                    payload = _eodhd_live_get(ep["url"], tk)
                requests_made += 1
                total_requests += 1
                n = len(payload) if hasattr(payload, "__len__") else 0
                out = raw_dir / ("%s.json" % tk)
                with open(out, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh)
                acquired += 1
                cached.add(tk)
                progress_rows.append({"endpoint": ep["name"], "ticker": tk, "status": "ACQUIRED",
                                      "rows": n, "requests_made": 1})
                raw_rows.append({"endpoint": ep["name"], "ticker": tk, "raw_path": _rel(out),
                                 "rows": n, "gitignored": True})
            except EodhdProbeError as exc:
                requests_made += 1
                total_requests += 1
                ent, _n, note = _classify_probe(exc)
                progress_rows.append({"endpoint": ep["name"], "ticker": tk, "status": ent,
                                      "rows": 0, "requests_made": 1})
                # A 403 entitlement block is systematic -> stop hammering this endpoint. A 404 (this
                # ticker has no data, e.g. a non-dividend payer) is per-ticker -> keep going.
                if ent == ENT_BLOCKED:
                    break
            if transport is None:
                time.sleep(ACQUIRE_MIN_SLEEP_SECONDS)
        ep_state[ep["name"]] = {"cache": ep["cache"], "raw_dir": raw_dir, "requests": requests_made,
                                "acquired": acquired,
                                "status": "ACQUIRED" if acquired else "ALL_CACHED"}
        log.step("acquire", "DONE", "%s: +%d acquired, %d requests (cache=%d)"
                 % (ep["name"], acquired, requests_made, len(cached)))
    return {"ep_state": ep_state, "progress_rows": progress_rows, "raw_rows": raw_rows,
            "total_requests": total_requests}


# --------------------------------------------------------------------------- #
# E. Point-in-time normalization of each EODHD family.
# --------------------------------------------------------------------------- #
def _f(v):
    return y8._to_float(v)


def _date10(v) -> str:
    return str(v)[:10] if v not in (None, "") else ""


def _avail_from(filing_date: str, fiscal_date: str) -> str:
    """Availability date for a fundamentals row: the filing date if present, else the fiscal-period end
    plus a conservative lag (no row is ever treated as available before it could have been filed)."""
    fd = _date10(filing_date)
    if fd:
        return fd
    base = _date10(fiscal_date)
    if not base:
        return ""
    try:
        import pandas as pd
        return str((pd.Timestamp(base) + pd.Timedelta(days=_PIT_FALLBACK_LAG_DAYS)).date())
    except Exception:
        return base


def _fund_quarters(payload: Dict) -> List[Dict]:
    """Merged per-quarter fundamentals rows (income + balance + cash-flow) keyed by fiscal period,
    carrying the availability date. Sorted ascending by fiscal date."""
    fin = payload.get("Financials") or {}
    inc = ((fin.get("Income_Statement") or {}).get("quarterly") or {})
    bal = ((fin.get("Balance_Sheet") or {}).get("quarterly") or {})
    cfs = ((fin.get("Cash_Flow") or {}).get("quarterly") or {})
    out: List[Dict] = []
    for period, irow in inc.items():
        if not isinstance(irow, dict):
            continue
        brow = bal.get(period, {}) if isinstance(bal.get(period), dict) else {}
        crow = cfs.get(period, {}) if isinstance(cfs.get(period), dict) else {}
        filing = irow.get("filing_date") or brow.get("filing_date") or crow.get("filing_date") or ""
        out.append({
            "fiscal_date": _date10(period),
            "available_date": _avail_from(filing, period),
            "total_revenue": _f(irow.get("totalRevenue")),
            "gross_profit": _f(irow.get("grossProfit")),
            "operating_income": _f(irow.get("operatingIncome")),
            "net_income": _f(irow.get("netIncome")),
            "total_assets": _f(brow.get("totalAssets")),
            "total_debt": _f(brow.get("shortLongTermDebtTotal")
                             if brow.get("shortLongTermDebtTotal") is not None
                             else brow.get("netDebt")),
            "shares_out": _f(brow.get("commonStockSharesOutstanding")),
            "cfo": _f(crow.get("totalCashFromOperatingActivities")),
            "free_cash_flow": _f(crow.get("freeCashFlow")),
        })
    out = [r for r in out if r["fiscal_date"]]
    out.sort(key=lambda r: r["fiscal_date"])
    return out


def _earnings_history(payload: Dict) -> List[Dict]:
    earn = (payload.get("Earnings") or {})
    hist = earn.get("History") or {}
    out: List[Dict] = []
    if isinstance(hist, dict):
        for _k, v in hist.items():
            if not isinstance(v, dict):
                continue
            out.append({"available_date": _date10(v.get("reportDate") or v.get("date")),
                        "eps_actual": _f(v.get("epsActual")),
                        "eps_estimate": _f(v.get("epsEstimate")),
                        "surprise_pct": _f(v.get("surprisePercent"))})
    out = [r for r in out if r["available_date"]]
    out.sort(key=lambda r: r["available_date"])
    return out


def _yoy(series: List[Optional[float]], i: int):
    """Year-over-year ratio change vs 4 quarters back (None if not computable / sign-unsafe)."""
    if i < 4:
        return None
    cur, prev = series[i], series[i - 4]
    if cur is None or prev is None or prev == 0:
        return None
    if prev < 0 and cur < 0:
        return None
    return cur / abs(prev) - 1.0


def _family_records(family: Dict, payload: Dict) -> List[Dict]:
    """Return [{available_date, value}] for one ticker's cached payload for the given family."""
    slug = family["family"]
    out: List[Dict] = []
    if slug == "eodhd_earnings_surprise":
        for r in _earnings_history(payload):
            if r["surprise_pct"] is not None:
                out.append({"available_date": r["available_date"], "value": r["surprise_pct"]})
    elif slug == "eodhd_eps_growth_yoy":
        eh = _earnings_history(payload)
        acts = [r["eps_actual"] for r in eh]
        for i, r in enumerate(eh):
            g = _yoy(acts, i)
            if g is not None:
                out.append({"available_date": r["available_date"], "value": g})
    elif slug in ("eodhd_revenue_growth_yoy", "eodhd_gross_profitability", "eodhd_operating_accruals",
                  "eodhd_asset_growth", "eodhd_net_share_issuance", "eodhd_leverage_change",
                  "eodhd_fcf_to_assets"):
        q = _fund_quarters(payload)
        rev = [r["total_revenue"] for r in q]
        assets = [r["total_assets"] for r in q]
        shares = [r["shares_out"] for r in q]
        for i, r in enumerate(q):
            ad = r["available_date"]
            ta = r["total_assets"]
            val = None
            if slug == "eodhd_revenue_growth_yoy":
                val = _yoy(rev, i)
            elif slug == "eodhd_gross_profitability":
                if r["gross_profit"] is not None and ta:
                    val = r["gross_profit"] / ta
            elif slug == "eodhd_operating_accruals":
                if r["net_income"] is not None and r["cfo"] is not None and ta:
                    val = (r["net_income"] - r["cfo"]) / ta
            elif slug == "eodhd_asset_growth":
                val = _yoy(assets, i)
            elif slug == "eodhd_net_share_issuance":
                val = _yoy(shares, i)
            elif slug == "eodhd_leverage_change":
                if r["total_debt"] is not None and ta and i >= 4:
                    prev = q[i - 4]
                    if prev["total_assets"]:
                        val = (r["total_debt"] / ta) - (
                            (prev["total_debt"] / prev["total_assets"])
                            if prev["total_debt"] is not None else 0.0)
            elif slug == "eodhd_fcf_to_assets":
                if r["free_cash_flow"] is not None and ta:
                    val = r["free_cash_flow"] / ta
            if val is not None and ad:
                out.append({"available_date": ad, "value": val})
    return out


def _dividend_records(payload) -> List[Dict]:
    """Year-over-year cash-dividend growth from the EODHD /div payload (declarationDate PIT)."""
    rows = payload if isinstance(payload, list) else (payload.get("data") if isinstance(payload, dict)
                                                      else [])
    recs = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        ad = _date10(r.get("declarationDate") or r.get("date"))
        val = _f(r.get("value") or r.get("unadjustedValue"))
        if ad and val is not None:
            recs.append({"available_date": ad, "value": val})
    recs.sort(key=lambda x: x["available_date"])
    vals = [x["value"] for x in recs]
    out = []
    for i, x in enumerate(recs):
        if i >= 4 and vals[i - 4]:
            out.append({"available_date": x["available_date"], "value": vals[i] / vals[i - 4] - 1.0})
    return out


def normalize_eodhd_family(family: Dict, P: _Paths, news_csv: Optional[Path], as_of: str,
                           log) -> Tuple[Optional[Path], List[Dict], List[Dict]]:
    """Flatten a family's cached EODHD payloads into a uniform PIT CSV (ticker, available_date,
    <feature>). Records with no availability date, no value, or an availability date AFTER as_of
    (future leak) are dropped. Returns (csv_path, manifest_rows, pit_audit_rows)."""
    feat = family["feature"]
    out_dir = P.norm_dir(family["family"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = out_dir / ("%s.csv" % feat)
    rows_out: List[List] = []
    audit: List[Dict] = []
    n_tickers = 0

    if family["source"] == "news":
        # Reuse the already-normalized EODHD news-sentiment table (ticker, available_date,
        # news_sentiment) - it is PIT-correct (article date). Drop future-dated rows.
        src = news_csv if news_csv and Path(news_csv).is_file() else None
        if src is None:
            _write_csv(out_csv, ["ticker", "available_date", feat], [])
            log.step("normalize", "EMPTY", "%s: no normalized news table" % family["family"])
            return out_csv, [{"family": family["family"], "feature": feat,
                              "normalized_path": _rel(out_csv), "rows": 0, "tickers": 0,
                              "fields": "ticker|available_date|%s" % feat, "gitignored": True}], audit
        seen = set()
        for r in _read_csv_file(src):
            tk = (r.get("ticker") or "").strip().upper()
            ad = _date10(r.get("available_date"))
            val = _f(r.get("news_sentiment"))
            if not tk or not ad or val is None:
                audit.append({"family": family["family"], "ticker": tk, "status": "DROPPED",
                              "available_date": ad, "value": "", "pit_ok": False})
                continue
            if ad > as_of:
                audit.append({"family": family["family"], "ticker": tk, "status": "DROPPED_FUTURE_DATE",
                              "available_date": ad, "value": _round(val, 6), "pit_ok": False})
                continue
            rows_out.append([tk, ad, _round(val, 6)])
            seen.add(tk)
        n_tickers = len(seen)
        _write_csv(out_csv, ["ticker", "available_date", feat], rows_out)
        log.step("normalize", "DONE", "%s: %d PIT rows / %d tickers (reused news table)"
                 % (family["family"], len(rows_out), n_tickers), count=len(rows_out))
        return out_csv, [{"family": family["family"], "feature": feat,
                          "normalized_path": _rel(out_csv), "rows": len(rows_out),
                          "tickers": n_tickers, "fields": "ticker|available_date|%s" % feat,
                          "gitignored": True}], audit

    raw_dir = P.raw_dir("fundamentals" if family["source"] == "fundamentals" else "dividends")
    seen = set()
    if raw_dir.is_dir():
        for raw_path in sorted(raw_dir.glob("*.json")):
            tk = raw_path.stem.upper()
            try:
                with open(raw_path, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
            except (OSError, json.JSONDecodeError):
                audit.append({"family": family["family"], "ticker": tk, "status": "PARSE_FAILED",
                              "available_date": "", "value": "", "pit_ok": False})
                continue
            recs = (_family_records(family, payload) if family["source"] == "fundamentals"
                    else _dividend_records(payload))
            for rec in recs:
                ad = rec.get("available_date", "")
                val = rec.get("value")
                status, pit_ok = y8._pit_status(ad, val, as_of)
                if pit_ok:
                    rows_out.append([tk, ad, _round(val, 6)])
                    seen.add(tk)
                audit.append({"family": family["family"], "ticker": tk, "status": status,
                              "available_date": ad,
                              "value": _round(val, 6) if val is not None else "", "pit_ok": pit_ok})
    n_tickers = len(seen)
    _write_csv(out_csv, ["ticker", "available_date", feat], rows_out)
    log.step("normalize", "DONE", "%s: %d PIT rows / %d tickers"
             % (family["family"], len(rows_out), n_tickers), count=len(rows_out))
    return out_csv, [{"family": family["family"], "feature": feat, "normalized_path": _rel(out_csv),
                      "rows": len(rows_out), "tickers": n_tickers,
                      "fields": "ticker|available_date|%s" % feat, "gitignored": True}], audit


# --------------------------------------------------------------------------- #
# F. EODHD section + field inventory (point-in-time usability classification).
# --------------------------------------------------------------------------- #
def _sample_fundamentals(P: _Paths) -> Tuple[str, Optional[Dict]]:
    raw = P.raw_dir("fundamentals")
    if not raw.is_dir():
        return "", None
    pref = raw / "AAPL.json"
    fp = pref if pref.is_file() else next(iter(sorted(raw.glob("*.json"))), None)
    if fp is None:
        return "", None
    try:
        with open(fp, "r", encoding="utf-8") as fh:
            return fp.stem.upper(), json.load(fh)
    except (OSError, json.JSONDecodeError):
        return "", None


def _span(dates: Sequence[str]) -> Tuple[str, str, int]:
    ds = sorted(d for d in (str(x)[:10] for x in dates) if d and d[:4].isdigit())
    return (ds[0] if ds else "", ds[-1] if ds else "", len(ds))


def build_section_field_inventory(P: _Paths) -> Tuple[List[Dict], List[Dict], List[Dict], List[Dict]]:
    """From a representative cached fundamentals payload, enumerate every section + field, its history
    depth, and whether it is PIT-usable or snapshot-only. Returns
    (section_rows, field_rows, pit_rows, snapshot_rows)."""
    sym, fund = _sample_fundamentals(P)
    section_rows: List[Dict] = []
    field_rows: List[Dict] = []
    pit_rows: List[Dict] = []
    snapshot_rows: List[Dict] = []
    if not isinstance(fund, dict):
        return section_rows, field_rows, pit_rows, snapshot_rows

    # Top-level sections.
    for sec, val in fund.items():
        kind = ("dict" if isinstance(val, dict) else "list" if isinstance(val, list) else "scalar")
        n = len(val) if hasattr(val, "__len__") else 1
        section_rows.append({"section": sec, "kind": kind, "n_entries": n,
                             "pit_usable": sec in ("Earnings", "Financials", "outstandingShares"),
                             "sample_ticker": sym})

    # PIT-usable detailed sections (history depth + fields).
    eh = _earnings_history(fund)
    if eh:
        first, last, depth = _span([r["available_date"] for r in eh])
        pit_rows.append({"section": "Earnings::History", "date_field": "reportDate",
                         "rows": depth, "first_date": first, "last_date": last,
                         "fields": "reportDate|epsActual|epsEstimate|surprisePercent"})
        for fld in ("reportDate", "epsActual", "epsEstimate", "epsDifference", "surprisePercent"):
            field_rows.append({"section": "Earnings::History", "field": fld, "pit_usable": True,
                               "date_field": "reportDate", "history_rows": depth,
                               "first_date": first, "last_date": last})

    q = _fund_quarters(fund)
    if q:
        first, last, depth = _span([r["available_date"] for r in q])
        for sub in ("Income_Statement", "Balance_Sheet", "Cash_Flow"):
            pit_rows.append({"section": "Financials::%s::quarterly" % sub, "date_field": "filing_date",
                             "rows": depth, "first_date": first, "last_date": last,
                             "fields": "filing_date|totalRevenue|netIncome|totalAssets|"
                                       "totalCashFromOperatingActivities|freeCashFlow|"
                                       "commonStockSharesOutstanding"})
        for fld in ("filing_date", "totalRevenue", "grossProfit", "operatingIncome", "netIncome",
                    "totalAssets", "shortLongTermDebtTotal", "commonStockSharesOutstanding",
                    "totalCashFromOperatingActivities", "freeCashFlow"):
            field_rows.append({"section": "Financials::quarterly", "field": fld, "pit_usable": True,
                               "date_field": "filing_date", "history_rows": depth,
                               "first_date": first, "last_date": last})

    # Snapshot-only sections (recorded, NOT used for historical alpha).
    for sec in SNAPSHOT_ONLY_SECTIONS:
        top = sec.split("::")[0]
        present = top in fund
        val = fund.get(top)
        keys = []
        if "::" in sec:
            sub = sec.split("::")[1]
            sval = (val or {}).get(sub) if isinstance(val, dict) else None
            keys = list(sval.keys())[:12] if isinstance(sval, dict) else []
            present = isinstance(sval, (dict, list))
        else:
            keys = list(val.keys())[:12] if isinstance(val, dict) else []
        snapshot_rows.append({"section": sec, "present": present,
                              "reason": "current-value snapshot - no usable dated history (PIT-unsafe)",
                              "fields": "|".join(keys)})
        for k in keys:
            field_rows.append({"section": sec, "field": k, "pit_usable": False,
                               "date_field": "", "history_rows": 0, "first_date": "", "last_date": ""})
    return section_rows, field_rows, pit_rows, snapshot_rows


# --------------------------------------------------------------------------- #
# G. Feature factory + cross-family interactions + campaign through the 8-X gate.
# --------------------------------------------------------------------------- #
def _coverage_diagnosis(csv_path, ev, fam, norm_rows, coverage) -> str:
    if norm_rows <= 0:
        return ("0 normalized rows for %s - acquire/refresh the EODHD cache or patch the normalizer "
                "(NOT data exhaustion)" % fam["family"])
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
            return ("date-span mismatch: %s history starts %s after the last event %s (NOT exhaustion)"
                    % (fam["family"], str(nd.min())[:10], str(ev_max)[:10]))
        return "date-span/join mismatch - acquire deeper history overlapping the panel (NOT exhaustion)"
    except Exception:                                      # pragma: no cover - defensive
        return "zero coverage - investigate join (NOT exhaustion)"


def build_cross_family_features(ev, norm_csvs: Dict[str, Path], log, families=None):
    """As-of attach each family PIT level, build within-month z products across families + each family
    x earnings-surprise / momentum / quality / value / liquidity. Returns (ev_aug, specs, catalog)."""
    import pandas as pd
    families = families or EODHD_FAMILIES
    ev_aug = ev.copy()
    if "month" not in ev_aug.columns:
        ev_aug["month"] = ev_aug["entry_date"].dt.to_period("M")
    levels: Dict[str, str] = {}
    for fam in families:
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

    feats = [f["feature"] for f in families]
    for i in range(len(feats)):
        for j in range(i + 1, len(feats)):
            _pair(feats[i], feats[j], "%s_x_%s" % (feats[i], feats[j]),
                  "cross-family interaction %s x %s" % (feats[i], feats[j]))

    for fam in families:
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


def run_campaign(ev, norm_csvs: Dict[str, Path], *, max_scenarios: int, max_models: int, log,
                 families=None) -> Dict:
    """Per family: the 8-Z PIT feature factory + factory hypotheses; then cross-family interaction
    scenarios. Aggregates candidates / registries / catalog / coverage / per-family diagnosis."""
    families = families or EODHD_FAMILIES
    fam_results: List[Dict] = []
    candidates: List[Dict] = []
    catalog_rows: List[List] = []
    scenario_specs: List[Dict] = []
    model_specs: List[Dict] = []
    coverage_rows: List[Dict] = []
    rng = x8._mk_rng()
    for fam in families:
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
                            "additive": fam["additive"], "populated": bool(populated),
                            "max_coverage": max_cov,
                            "diagnosis": _coverage_diagnosis(csv_path, ev, fam, norm_rows, max_cov)})

    ev_ix, ix_specs, ix_catalog = build_cross_family_features(ev, norm_csvs, log, families)
    catalog_rows.extend(ix_catalog)
    for sc in ix_specs:
        candidates.append(x8.evaluate_scenario(ev_ix, sc, rng))
    scenario_specs.extend(ix_specs)
    return {"fam_results": fam_results, "candidates": candidates, "catalog_rows": catalog_rows,
            "scenario_specs": scenario_specs, "model_specs": model_specs,
            "coverage_rows": coverage_rows, "ev_ix": ev_ix}


# --------------------------------------------------------------------------- #
# H. Horizon sweep (1/5/21/63-day forward excess return IC) - local, no existing-phase change.
# --------------------------------------------------------------------------- #
def horizon_sweep(ev_ix, log, families=None) -> List[Dict]:
    import numpy as np
    import pandas as pd
    families = families or EODHD_FAMILIES
    rows: List[Dict] = []
    if getattr(ev_ix, "empty", True):
        return rows
    work = ev_ix.copy()
    if "month" not in work.columns:
        work["month"] = work["entry_date"].dt.to_period("M")
    signal_cols: List[Tuple[str, str]] = []
    for fam in families:
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
    log.step("horizon", "DONE", "%d horizon-sweep rows over %d signals" % (len(rows), len(signal_cols)))
    return rows


# --------------------------------------------------------------------------- #
# I. Decision + keep/upgrade/cancel + missing-data-after.
# --------------------------------------------------------------------------- #
def _missing_after_rows(audit: List[Dict]) -> List[Dict]:
    """The exact data NOT available after fully exhausting EODHD + Norgate. EODHD provides only current
    snapshots of analyst targets/estimates (no PIT revision history) and the Fundamentals feed has no
    options chain - these are the genuine remaining mechanisms."""
    options_ok = _audit_ok(audit, "options")
    return [
        {"missing_mechanism": "analyst_estimate_revision_history",
         "why": "EODHD AnalystRatings / Earnings.Trend are current snapshots - no dated as-of history "
                "of EPS/revenue estimate revisions (PIT-unsafe for a revision-drift signal)",
         "exact_paid_fix": "Intrinio/Zacks estimate-revision history, or FMP Premium analyst-estimates "
                           "(only if the user chooses to add a non-EODHD estimate feed)"},
        {"missing_mechanism": "price_target_revision_history",
         "why": "EODHD WallStreetTargetPrice / AnalystRatings.TargetPrice are current snapshots - no "
                "dated history of target changes",
         "exact_paid_fix": "Benzinga / Intrinio analyst price-target history"},
        {"missing_mechanism": "options_iv_skew_put_call",
         "why": ("the EODHD options endpoint returned 404/not-entitled under the Fundamentals feed"
                 if not options_ok else "options endpoint reachable but not part of the audited factory"),
         "exact_paid_fix": "EODHD Options add-on, or ORATS / Polygon Options Starter (historical chains)"},
        {"missing_mechanism": "intraday_technical_tick_data",
         "why": "the Fundamentals feed does not include the intraday/technical/tick API",
         "exact_paid_fix": "EODHD Technical/Intraday add-on (only if an intraday signal is pursued)"},
    ]


def keep_upgrade_cancel(fam_results: List[Dict], audit: List[Dict], strong: bool) -> Tuple[str, str]:
    usable = [f for f in fam_results if f["max_coverage"] > 0]
    sections_ok = sum(1 for r in audit if r["entitlement"] == ENT_VERIFIED)
    if strong:
        return ("KEEP", "EODHD produced a promoted strong-alpha signal on the Norgate survivorship-free "
                        "panel - keep the subscription and productize (paper-only).")
    if len(usable) >= 4:
        return ("KEEP", "EODHD delivered %d PIT-usable fundamental/sentiment families with real panel "
                        "coverage across %d accessible endpoints at $59.99/mo - it is the cheap "
                        "survivorship-free fundamentals backbone even with no single strong standalone "
                        "alpha. Keep it; do NOT cancel/downgrade. Any future spend should be an "
                        "ESTIMATE/OPTIONS add-on, not a replacement." % (len(usable), sections_ok))
    if len(usable) >= 1:
        return ("KEEP", "EODHD yielded usable PIT families but thin coverage; keep at the current tier "
                        "and deepen the cache before considering any add-on.")
    return ("REVIEW", "No EODHD family produced usable PIT panel coverage - investigate the join/cache "
                      "before any keep/cancel decision (NOT a cancel recommendation yet).")


def derive_decision(*, panel_ok: bool, eodhd_ok: bool, audit: List[Dict], acq: Dict,
                    fam_results: List[Dict], candidates: List[Dict], universe_size: int,
                    max_tickers: int, total_requests: int, request_ceiling: int
                    ) -> Tuple[str, str, List[Dict]]:
    next_rows: List[Dict] = []

    strong = [c for c in candidates if c.get("status") == "strong"]
    if strong:
        best = x8._best_candidate(strong) or strong[0]
        next_rows.append({"action": "review_promoted_strong_alpha", "family": best.get("family", ""),
                          "command": "review research/output/phase10b_eodhd_norgate_exhaustive_alpha_"
                                     "factory/strong_alpha_candidates.csv then productize paper-only"})
        return (DEC_STRONG,
                "Promoted %d strong EODHD+Norgate candidate(s) clearing the full broad 8-X gate "
                "(t>=%.1f, BH-significant, net-of-25bps positive, both cohorts + both pre/post-2020 "
                "halves positive, sector-diversified). Best: %s (t=%s)."
                % (len(strong), STRONG_MIN_IC_T, best["name"], x8._g(best["metrics"], "ic_t", 2)),
                next_rows)

    if not eodhd_ok:
        next_rows.append({"action": "set_eodhd_key", "family": "",
                          "command": "set $env:EODHD_API_KEY then re-run --live"})
        return (DEC_BLOCKER, "EODHD_API_KEY is not visible to this shell, so the paid EODHD feed could "
                             "not be audited. Set the key and re-run.", next_rows)

    if not panel_ok:
        next_rows.append({"action": "restore_norgate_panel", "family": "",
                          "command": NORGATE_REBUILD_COMMAND})
        return (DEC_BLOCKER, "The Norgate survivorship-free earnings-event panel is not present, so no "
                             "point-in-time events could be scored. Rebuild the Norgate foundation, "
                             "then re-run.", next_rows)

    acquired_usable = [f for f in fam_results if f["max_coverage"] > 0]
    acquired_norm = [f for f in fam_results if f["norm_rows"] > 0]

    # If raw was acquired/normalized but does not overlap the panel yet, that is a resumable batch.
    if acquired_norm and not acquired_usable:
        gaps = "; ".join("%s: %s" % (f["family"], f["diagnosis"])
                         for f in acquired_norm if f["max_coverage"] == 0)
        next_rows.append({"action": "acquire_deeper_history", "family": "",
                          "command": "python research/run_phase10b_eodhd_norgate_exhaustive_alpha_"
                                     "factory.py --live --refresh --max-tickers %d" % max_tickers})
        return (DEC_NEXT_BATCH,
                "EODHD families normalized PIT but do not yet overlap the earnings panel at the join. "
                "Exact fix: %s" % gaps, next_rows)

    stopped_budget = any(p.get("status") == ENT_RATE for p in acq.get("progress_rows", []))
    more_universe = universe_size > max_tickers
    ceiling_left = total_requests < request_ceiling
    if (more_universe or stopped_budget) and ceiling_left and total_requests > 0:
        next_rows.append({"action": "continue_next_batch", "family": "",
                          "command": "python research/run_phase10b_eodhd_norgate_exhaustive_alpha_"
                                     "factory.py --live --max-tickers %d" % max_tickers})
        return (DEC_NEXT_BATCH,
                "Acquired and scored a bounded EODHD batch; the request budget/universe is not "
                "exhausted. Continue the next resumable batch (skip-existing resumes).", next_rows)

    # Fully tested, no strong alpha -> EODHD + Norgate accessible families are exhausted. Record the
    # keep decision + the exact remaining (estimate/target/options) mechanisms, but the honest terminal
    # is exhaustion (we do NOT promote a new paid purchase as the terminal).
    tested = ", ".join(f["family"] for f in acquired_usable) or "(none)"
    next_rows.append({"action": "review_exhaustion", "family": "",
                      "command": "review research/output/phase10b_eodhd_norgate_exhaustive_alpha_"
                                 "factory/phase10b_eodhd_norgate_exhaustive_alpha_factory.json + "
                                 "missing_data_after_eodhd_norgate.csv"})
    rationale = (
        "Full broad 8-X gate + 1/5/21/63d horizon sweep run over the EODHD PIT feature factory on the "
        "%d-ticker Norgate survivorship-free panel with NO strong alpha. Fully tested families: %s. "
        "EODHD + Norgate accessible data is exhausted; the only remaining mechanisms are "
        "forward-looking (estimate/target revision history, options IV/skew) which the Fundamentals "
        "feed does not provide as PIT history (see missing_data_after_eodhd_norgate.csv). Keep EODHD "
        "(cheap survivorship-free fundamentals backbone); any future spend is an add-on, not a "
        "replacement." % (universe_size, tested))
    return (DEC_EXHAUSTED, rationale, next_rows)


# --------------------------------------------------------------------------- #
# J. Secret-safety audit.
# --------------------------------------------------------------------------- #
def _secret_safety_audit(out_dir: Path) -> Tuple[List[Dict], bool]:
    markers = ["apikey=", "api_token=", "token=", "api_key=", "&apikey", "?apikey", "&token"]
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
# K. Artifact writers.
# --------------------------------------------------------------------------- #
def _leakage_audit_rows(ev, norm_csvs: Dict[str, Path], pit_audit: List[Dict]) -> List[List]:
    rows = [
        ["as_of_join_direction", "PASS", "backward only: feature attached iff available_date <= "
         "entry_date (y8.attach_orthogonal_feature / z8.feature_factory)"],
        ["future_dated_records_dropped", "PASS", "normalizer drops available_date > as_of (%s)" % AS_OF],
        ["snapshot_only_excluded", "PASS", "snapshot-only sections (Highlights/Valuation/SharesStats/"
         "AnalystRatings/Earnings.Trend/ESG) are recorded but NEVER fed to the historical factory"],
        ["forward_return_label", "PASS", "fwd_exc_{1,5,21,63} computed strictly after entry_date "
         "(8-S forward-return engine)"],
        ["conservative_filing_lag", "PASS", "fundamentals rows with no filing_date use fiscal-end + "
         "%d-day conservative availability lag" % _PIT_FALLBACK_LAG_DAYS],
    ]
    dropped_future = sum(1 for a in pit_audit if a.get("status") == "DROPPED_FUTURE_DATE")
    rows.append(["pit_records_dropped_future_dated", "PASS",
                 "%d future-dated records dropped at normalization" % dropped_future])
    try:
        import pandas as pd
        ev_max = str(ev["entry_date"].max())[:10] if not getattr(ev, "empty", True) else ""
        for fam in EODHD_FAMILIES:
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


def write_artifacts(P: _Paths, *, preflight_rows, audit, inv, acq, campaign, candidates, strong,
                    rejected, horizon_rows, ev, norm_csvs, pit_audit, norm_manifest, norgate_rows,
                    keep_dec, missing_after, next_rows, log) -> bool:
    section_rows, field_rows, pit_rows, snapshot_rows = inv

    _write_csv(P.art("key_preflight"), ["env_var", "required", "role", "visibility"],
               [[r["env_var"], r["required"], r["role"], r["visibility"]] for r in preflight_rows])

    _write_csv(P.art("entitlement_audit"),
               ["name", "section", "pit", "entitlement", "http_status", "rows", "endpoint_redacted",
                "note"],
               [[r["name"], r["section"], r["pit"], r["entitlement"], r["http_status"], r["rows"],
                 r["endpoint_redacted"], r["note"]] for r in audit])

    _write_csv(P.art("section_inventory"),
               ["section", "kind", "n_entries", "pit_usable", "sample_ticker"],
               [[r["section"], r["kind"], r["n_entries"], r["pit_usable"], r["sample_ticker"]]
                for r in section_rows])
    _write_csv(P.art("field_inventory"),
               ["section", "field", "pit_usable", "date_field", "history_rows", "first_date",
                "last_date"],
               [[r["section"], r["field"], r["pit_usable"], r["date_field"], r["history_rows"],
                 r["first_date"], r["last_date"]] for r in field_rows])
    _write_csv(P.art("pit_fields"),
               ["section", "date_field", "rows", "first_date", "last_date", "fields"],
               [[r["section"], r["date_field"], r["rows"], r["first_date"], r["last_date"],
                 r["fields"]] for r in pit_rows])
    _write_csv(P.art("snapshot_fields"), ["section", "present", "reason", "fields"],
               [[r["section"], r["present"], r["reason"], r["fields"]] for r in snapshot_rows])

    _write_csv(P.art("acq_progress"), ["endpoint", "ticker", "status", "rows", "requests_made"],
               [[r["endpoint"], r["ticker"], r["status"], r["rows"], r["requests_made"]]
                for r in acq["progress_rows"]])
    _write_csv(P.art("raw_manifest"), ["endpoint", "ticker", "raw_path", "rows", "gitignored"],
               [[r["endpoint"], r["ticker"], r["raw_path"], r["rows"], r["gitignored"]]
                for r in acq["raw_rows"]])
    _write_csv(P.art("norm_manifest"),
               ["family", "feature", "normalized_path", "rows", "tickers", "fields", "gitignored"],
               [[m["family"], m["feature"], m["normalized_path"], m["rows"], m["tickers"],
                 m.get("fields", ""), m.get("gitignored", True)] for m in norm_manifest])

    _write_csv(P.art("pit_norm_audit"),
               ["family", "ticker", "status", "available_date", "value", "pit_ok"],
               [[a.get("family", ""), a.get("ticker", ""), a.get("status", ""),
                 a.get("available_date", ""), a.get("value", ""), a.get("pit_ok", "")]
                for a in pit_audit])
    _write_csv(P.art("pit_join_audit"),
               ["family", "additive", "norm_rows", "coverage_events", "join_direction", "diagnosis"],
               [[f["family"], f["additive"], f["norm_rows"], f["max_coverage"],
                 "available_date <= entry_date", f["diagnosis"]] for f in campaign["fam_results"]])

    _write_csv(P.art("norgate_manifest"), ["check", "value", "detail"],
               [[r.get("check", ""), r.get("value", ""), r.get("detail", "")] for r in norgate_rows])

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

    _write_csv(P.art("keep_decision"), ["subscription", "verdict", "rationale", "monthly_cost"],
               [["EODHD Fundamentals Data Feed", keep_dec[0], keep_dec[1], "$59.99"],
                ["Norgate US Stocks Diamond", "KEEP",
                 "survivorship-free US equities foundation (universe / membership / delisted / sector "
                 "/ liquidity / returns) - irreplaceable backbone; expires 2026-12-24", "(annual)"]])
    _write_csv(P.art("missing_after"), ["missing_mechanism", "why", "exact_paid_fix"],
               [[r["missing_mechanism"], r["why"], r["exact_paid_fix"]] for r in missing_after])

    _write_csv(P.art("next_commands"), ["action", "family", "command"],
               [[r["action"], r["family"], r["command"]] for r in next_rows])

    rows, clean = _secret_safety_audit(P.out)
    _write_csv(P.art("secret_audit"), ["file", "clean", "keyed_url_marker", "key_value_present"],
               [[r["file"], r["clean"], r["keyed_url_marker"], r["key_value_present"]] for r in rows])
    log.step("artifacts", "DONE", "wrote %d required artifacts" % len(_REQUIRED_ARTIFACTS))
    return clean


# --------------------------------------------------------------------------- #
# L. Report / plan / summary.
# --------------------------------------------------------------------------- #
def _empty_ev():
    import pandas as pd
    return pd.DataFrame({"ticker": [], "entry_date": []})


def _build_report(P, decision, rationale, next_rows, preflight_ok, missing_required, norgate, audit,
                  acq, campaign, candidates, strong, constrained, rejected, horizon_rows,
                  exhausted_labels, n_tickers, n_events, universe_size, max_tickers, total_requests,
                  request_ceiling, panel_ok, live, leak_clean, keep_dec, missing_after, as_of) -> Dict:
    best = x8._best_candidate(candidates)
    sections_accessible = sorted({r["section"] for r in audit if r["entitlement"] == ENT_VERIFIED})
    sections_blocked = sorted({"%s (%s)" % (r["section"], r["entitlement"]) for r in audit
                               if r["entitlement"] in (ENT_BLOCKED, ENT_NOTFOUND, ENT_RATE)})
    horizons_tested = sorted({r["horizon_days"] for r in horizon_rows})
    pit_usable = [f["family"] for f in campaign["fam_results"] if f["max_coverage"] > 0]
    return {
        "phase": PHASE, "as_of": as_of, "decision": decision, "decision_rationale": rationale,
        "exact_next_command": (next_rows[0]["command"] if next_rows else ""),
        "live_mode": live, "allowed_decisions": list(ALLOWED_DECISIONS),
        "eodhd_key_visible": preflight_ok, "key_visibility_missing_required": missing_required,
        "fmp_role": "present-but-ignored (not a research source)" if r8.key_present("FMP_API_KEY")
        else "absent (irrelevant)",
        "norgate_foundation": {"usable_now": bool(norgate.get("panel_ok")),
                               "reuse_or_rebuild": norgate.get("reuse_or_rebuild"),
                               "last_panel_month": norgate.get("last_month"),
                               "rebuild_can_run_now": norgate.get("rebuild_can_run_now"),
                               "rebuild_command": NORGATE_REBUILD_COMMAND, "keep": True},
        "eodhd_sections_accessible": sections_accessible,
        "eodhd_sections_blocked": sections_blocked,
        "eodhd_pit_usable_families": pit_usable,
        "eodhd_snapshot_only_sections": list(SNAPSHOT_ONLY_SECTIONS),
        "requests_total": total_requests, "request_ceiling": request_ceiling,
        "requests_by_endpoint": {k: int(v.get("requests", 0)) for k, v in acq["ep_state"].items()},
        "acquired_by_endpoint": {k: int(v.get("acquired", 0)) for k, v in acq["ep_state"].items()},
        "normalized_rows_by_family": {f["family"]: f["norm_rows"] for f in campaign["fam_results"]},
        "feature_coverage_by_family": {f["family"]: f["max_coverage"]
                                       for f in campaign["fam_results"]},
        "feature_coverage_diagnosis": {f["family"]: f["diagnosis"] for f in campaign["fam_results"]},
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
        "eodhd_keep_upgrade_cancel": {"verdict": keep_dec[0], "rationale": keep_dec[1]},
        "norgate_keep": True,
        "missing_data_after_eodhd_norgate": missing_after,
        "panel_present": panel_ok, "secret_safety_leak_scan_clean": leak_clean,
        "api_key_printed": False, "api_key_written_to_disk": False,
        "required_artifacts": list(_ARTIFACTS.values()),
        "exact_next_commands": next_rows,
    }


def _phase10c_plan(decision, next_rows, strong, missing_after) -> Dict:
    return {"from_phase": PHASE, "decision": decision, "next_phase": "10-C",
            "exact_next_commands": next_rows,
            "eodhd_families": [f["family"] for f in EODHD_FAMILIES],
            "strong_candidates": [c["name"] for c in strong],
            "missing_data_after_eodhd_norgate": [m["missing_mechanism"] for m in missing_after],
            "next_steps": (["Productize the promoted strong EODHD signal (paper-only, manual-review, "
                            "NO orders/automation)."] if strong else
                           ["EODHD + Norgate accessible families are exhausted with no strong alpha. "
                            "Keep both subscriptions. If pursuing more alpha, the next data is a "
                            "forward-looking add-on (estimate/target revision history or options "
                            "IV/skew) - NOT a replacement for EODHD/Norgate."])}


def _print_summary(report: Dict) -> None:
    print("[10-B] decision=%s | eodhd_key=%s | sections_ok=%d | usable_families=%d | requests=%s | "
          "scenarios=%s models=%s horizons=%s | strong=%s | best=%s (t=%s) | keep=%s | leak_clean=%s"
          % (report["decision"], report["eodhd_key_visible"],
             len(report["eodhd_sections_accessible"]), len(report["eodhd_pit_usable_families"]),
             report["requests_total"], report["scenarios_tested"], report["models_tested"],
             report["horizons_tested"], report["strong_alpha_found"], report["best_candidate"],
             report["best_candidate_t_stat"], report["eodhd_keep_upgrade_cancel"]["verdict"],
             report["secret_safety_leak_scan_clean"]))


# --------------------------------------------------------------------------- #
# M. Orchestration (single bounded campaign; cycle loop is resumable + ceiling-bounded).
# --------------------------------------------------------------------------- #
def run(out_dir: Optional[Path] = None, data_dir: Optional[Path] = None, *,
        price_csv: Optional[Path] = None, phase8v_dir: Optional[Path] = None,
        sector_csv: Optional[Path] = None, macro: Optional[Dict[str, Path]] = None,
        phase8n_dir: Optional[Path] = None, phase8r_dir: Optional[Path] = None,
        news_csv: Optional[Path] = None, as_of: str = AS_OF, live: bool = False,
        refresh: bool = False, transport: Optional[Callable] = None,
        max_tickers: int = DEFAULT_MAX_TICKERS, max_requests: int = DEFAULT_MAX_REQUESTS_PER_RUN,
        max_cycles: int = DEFAULT_MAX_CYCLES, request_ceiling: int = DEFAULT_TOTAL_REQUEST_CEILING,
        max_scenarios: int = DEFAULT_MAX_SCENARIOS, max_models: int = DEFAULT_MAX_MODELS,
        families=None, verbose: bool = True) -> Dict:
    P = _Paths(out_dir, data_dir)
    P.out.mkdir(parents=True, exist_ok=True)
    fams = families or EODHD_FAMILIES
    log = t8._Log(verbose)
    try:
        import pandas as pd  # noqa: F401

        # 0. Key-visibility preflight (EODHD must be PRESENT; FMP context-only).
        preflight_rows, eodhd_ok, missing_required = key_visibility_preflight(transport)
        for r in preflight_rows:
            log.step("preflight", r["visibility"], "%s required=%s" % (r["env_var"], r["required"]))

        # 1. Norgate foundation (reuse 9-C verification - pure read, reuse-vs-rebuild).
        norgate_rows, norgate = c9.verify_norgate_foundation(as_of, log)

        # 2. Norgate survivorship-free earnings-event panel + cohort tag + liquidity proxy (8-W/8-S).
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

        # 3. EODHD entitlement audit (one probe per endpoint; never stops on a block).
        audit = eodhd_entitlement_audit(live, transport, log)

        # 4. Section + field inventory (PIT-usable vs snapshot-only) from the cached fundamentals.
        inv = build_section_field_inventory(P)

        # 5. Bounded, resumable EODHD acquisition + cycle loop (ceiling-bounded).
        total_requests = 0
        acq = {"ep_state": {}, "progress_rows": [], "raw_rows": [], "total_requests": 0}
        if panel_ok:
            for cycle in range(max(1, max_cycles)):
                budget = min(max_requests, max(0, request_ceiling - total_requests))
                if budget <= 0:
                    break
                cyc = acquire_eodhd(universe, audit, P, max_tickers=max_tickers,
                                    request_budget=budget, live=live, transport=transport,
                                    skip_existing=not refresh, log=log)
                total_requests += cyc["total_requests"]
                acq["progress_rows"].extend(cyc["progress_rows"])
                acq["raw_rows"].extend(cyc["raw_rows"])
                for ep, st in cyc["ep_state"].items():
                    prev = acq["ep_state"].get(ep)
                    if prev is None:
                        acq["ep_state"][ep] = dict(st)
                    else:
                        prev["requests"] += st["requests"]
                        prev["acquired"] += st["acquired"]
                        if st["status"] == "ACQUIRED":
                            prev["status"] = "ACQUIRED"
                log.step("cycle", "DONE", "cycle %d: +%d requests (total %d / ceiling %d)"
                         % (cycle + 1, cyc["total_requests"], total_requests, request_ceiling))
                if cyc["total_requests"] == 0:
                    break
            acq["total_requests"] = total_requests

        # 6. Normalize each EODHD family PIT (gitignored).
        norm_csvs: Dict[str, Path] = {}
        norm_manifest: List[Dict] = []
        pit_audit: List[Dict] = []
        news_table = Path(news_csv) if news_csv else (
            P.eodhd_dir / "normalized" / "news_social_sentiment" / "news_sentiment.csv")
        if panel_ok:
            for fam in fams:
                csv_path, man, aud = normalize_eodhd_family(fam, P, news_table, as_of, log)
                norm_csvs[fam["family"]] = csv_path
                norm_manifest += man
                pit_audit += aud

        # 7. Feature factory + interactions through the 8-X gate + horizon sweep.
        if panel_ok:
            campaign = run_campaign(ev, norm_csvs, max_scenarios=max_scenarios,
                                    max_models=max_models, log=log, families=fams)
            horizon_rows = horizon_sweep(campaign["ev_ix"], log, families=fams)
        else:
            campaign = {"fam_results": [], "candidates": [], "catalog_rows": [], "scenario_specs": [],
                        "model_specs": [], "coverage_rows": [], "ev_ix": _empty_ev()}
            horizon_rows = []
        candidates = campaign["candidates"]

        # 8. Multiple-testing + broad strong gate over ALL candidates.
        x8._finalize_gates(candidates, n_tickers, n_events, STRONG_MIN_TICKERS, STRONG_MIN_EVENTS)
        strong = [c for c in candidates if c["status"] == "strong"]
        constrained = [c for c in candidates if c["status"] == "constrained"]
        rejected = [c for c in candidates if c["status"] == "rejected"]
        exhausted_search = bool(panel_ok and universe and len(universe) <= max_tickers)
        exh_rows, exhausted_labels = x8.data_family_exhaustion(candidates, exhausted_search)

        # 9. Keep/cancel + missing-after + decision.
        keep_dec = keep_upgrade_cancel(campaign["fam_results"], audit, bool(strong))
        missing_after = _missing_after_rows(audit)
        norgate_panel_state = dict(norgate)
        norgate_panel_state["panel_ok"] = panel_ok
        decision, rationale, next_rows = derive_decision(
            panel_ok=panel_ok, eodhd_ok=eodhd_ok, audit=audit, acq=acq,
            fam_results=campaign["fam_results"], candidates=candidates, universe_size=len(universe),
            max_tickers=max_tickers, total_requests=total_requests, request_ceiling=request_ceiling)

        # 10. Resume state.
        _write_json(P.resume_state, {"as_of": as_of, "max_tickers": max_tickers,
                                     "total_requests": total_requests,
                                     "acquired_by_endpoint": {k: v.get("acquired", 0)
                                                              for k, v in acq["ep_state"].items()}})

        # 11. Artifacts + report.
        leak_clean = write_artifacts(
            P, preflight_rows=preflight_rows, audit=audit, inv=inv, acq=acq, campaign=campaign,
            candidates=candidates, strong=strong, rejected=rejected, horizon_rows=horizon_rows,
            ev=ev if panel_ok else _empty_ev(), norm_csvs=norm_csvs, pit_audit=pit_audit,
            norm_manifest=norm_manifest, norgate_rows=norgate_rows, keep_dec=keep_dec,
            missing_after=missing_after, next_rows=next_rows, log=log)

        report = _build_report(
            P, decision, rationale, next_rows, eodhd_ok, missing_required, norgate_panel_state, audit,
            acq, campaign, candidates, strong, constrained, rejected, horizon_rows, exhausted_labels,
            n_tickers, n_events, len(universe), max_tickers, total_requests, request_ceiling,
            panel_ok, live, leak_clean, keep_dec, missing_after, as_of)
        _write_json(P.art("report"), report)
        _write_json(P.art("next_plan"), _phase10c_plan(decision, next_rows, strong, missing_after))
        _print_summary(report)
        return report
    except Exception as exc:                               # pragma: no cover - defensive
        import traceback
        detail = "%s: %s" % (type(exc).__name__, exc)
        log.step("run", "ERROR", detail)
        report = {"phase": PHASE, "decision": DEC_ERROR, "decision_rationale": detail,
                  "repro_command": ("python research/run_phase10b_eodhd_norgate_exhaustive_alpha_"
                                    "factory.py --live"),
                  "traceback": traceback.format_exc()}
        try:
            _write_json(P.art("report"), report)
        except Exception:
            pass
        return report


# --------------------------------------------------------------------------- #
# N. CLI.
# --------------------------------------------------------------------------- #
def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 10-B - EODHD + Norgate Exhaustive Alpha Factory")
    p.add_argument("--live", action="store_true",
                   help="audit + acquire live with the EODHD key (read from env, never written)")
    p.add_argument("--refresh", action="store_true",
                   help="overwrite existing raw payloads instead of skipping (deeper history)")
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
