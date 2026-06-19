"""Phase 3-M - Earnings Estimates / Surprise Data Gate + Immediate Signal Test.

Phase 3-L (SEC_UNIVERSE_SIGNAL_GATE_WEAK_BUT_EXPAND_OR_ADD_REVISIONS) built a clean, leakage-free,
multi-regime 128-equity SEC fundamental panel (303,201 rows, 11 distinct dense IC years) but found
that SEC-only as-reported fundamentals carry only weak cross-sectional signal: 4 moderate-or-better
features and 1 strong feature, short of the >= 5 / >= 2 research-model gate. It explicitly routed
here to add richer structured data - analyst earnings estimates / EPS surprise - before any model
training. This phase is that step: detect a supported earnings-estimates provider from the
environment, ingest historical estimate / actual / surprise data for the Phase 3-L universe (cache
first, conservative rate limits), normalize point-in-time earnings events, build trailing-only
earnings-surprise features, align them to the existing Phase 3-L panel (reusing its validation-only
labels), run the same IC / feature-family / temporal-breadth signal gate on earnings-only, SEC-only,
and combined feature sets, and decide whether Phase 3-N may train a research-only walk-forward
model.

Provider policy. A provider is selected ONLY from an API key already present in the environment, in
priority order Alpha Vantage -> FMP -> Finnhub -> Intrinio. Keys are read from environment variables
only; they are NEVER printed, NEVER written to any artifact, and NEVER hardcoded. Stored endpoint
strings are redacted. Nothing is purchased, no account is created, and the user is never prompted
for a key inside code. If no supported key exists, a clearly-marked
EARNINGS_ESTIMATES_GATE_BLOCKED_NEEDS_API_KEY result is written (no faked data, no modeling). If a
provider returns a rate-limit / entitlement / premium-required error, a
EARNINGS_ESTIMATES_GATE_BLOCKED_PROVIDER_LIMIT result is written rather than crashing. Network is
used only for the selected provider host; there is no SEC network in this phase and no
third-party market-data vendor package is used.

Scope and safety. This phase reuses the Phase 3-L validation-only labels (it computes no new
targets); it touches no D: drive (it reads only Phase 3-L outputs on the C: drive). It fits NO model
(no regression / logistic / ridge / lasso / tree / ML estimator), computes NO predictions, scores,
trading rankings, or portfolio weights, creates NO production model candidate, and writes NO
deployable model artifact. It touches no database, runs no migration, restarts no prediction
service, enables no serving flag, places no orders, and trades nothing. Membership is current-as-of,
so every result remains survivorship-caveated and claims no production edge.
"""
from __future__ import annotations

import csv
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

PHASE = "3-M"

# --------------------------------------------------------------------------- #
# Paths (repo-local on the C: drive; NO D: drive access in this phase)
# --------------------------------------------------------------------------- #
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUT_DIR = os.path.join(_REPO_ROOT, "research", "output")
_IN_DIR = os.path.join(_REPO_ROOT, "research", "input")
_M_DIR = os.path.join(_OUT_DIR, "phase3m_earnings_estimates_signal_gate")
_RAW_DIR = os.path.join(_M_DIR, "raw")

# Inputs (Phase 3-L outputs + the sector map).
PHASE3L_JSON = os.path.join(_OUT_DIR, "phase3l_sec_universe_signal_gate.json")
_L_DIR = os.path.join(_OUT_DIR, "phase3l_sec_universe_signal_gate")
PHASE3L_PANEL_CSV = os.path.join(_L_DIR, "aligned_feature_price_panel_universe.csv")
PHASE3L_IDENTITY_CSV = os.path.join(_L_DIR, "company_identity_universe.csv")
PHASE3L_LABEL_SUMMARY_CSV = os.path.join(_L_DIR, "label_summary_by_horizon.csv")
PHASE3L_FEATURE_IC_CSV = os.path.join(_L_DIR, "feature_ic_summary.csv")
PHASE3L_HORIZON_READINESS_CSV = os.path.join(_L_DIR, "horizon_readiness_summary.csv")
SECTOR_MAP_CSV = os.path.join(_IN_DIR, "phase2k_p_sector_map_current.csv")

# Outputs.
RESULT_JSON = os.path.join(_OUT_DIR, "phase3m_earnings_estimates_signal_gate.json")
PROVIDER_ACCESS_JSON = os.path.join(_M_DIR, "provider_access_report.json")
EARNINGS_EVENTS_CSV = os.path.join(_M_DIR, "earnings_events_universe.csv")
EARNINGS_FEATURES_CSV = os.path.join(_M_DIR, "earnings_features_universe.csv")
COMBINED_PANEL_CSV = os.path.join(_M_DIR, "combined_fundamental_earnings_panel.csv")
EARNINGS_FEATURE_COVERAGE_CSV = os.path.join(_M_DIR, "earnings_feature_coverage_by_ticker.csv")
EARNINGS_LABEL_SUMMARY_CSV = os.path.join(_M_DIR, "earnings_label_summary_by_horizon.csv")
EARNINGS_FEATURE_IC_CSV = os.path.join(_M_DIR, "earnings_feature_ic_summary.csv")
EARNINGS_FEATURE_FAMILY_IC_CSV = os.path.join(_M_DIR, "earnings_feature_family_ic_summary.csv")
EARNINGS_HORIZON_READINESS_CSV = os.path.join(_M_DIR, "earnings_horizon_readiness_summary.csv")
EARNINGS_YEARLY_IC_CSV = os.path.join(_M_DIR, "earnings_yearly_ic_summary.csv")
EARNINGS_SECTOR_SANITY_CSV = os.path.join(_M_DIR, "earnings_sector_sanity_summary.csv")
DECISION_TABLE_CSV = os.path.join(_M_DIR, "decision_table.csv")

# --------------------------------------------------------------------------- #
# Upstream confirmation contract (Phase 3-L)
# --------------------------------------------------------------------------- #
PHASE3L_EXPECTED_RECOMMENDATION = "SEC_UNIVERSE_SIGNAL_GATE_WEAK_BUT_EXPAND_OR_ADD_REVISIONS"
PHASE3L_EXPECTED_PROCESSED = 128
PHASE3L_EXPECTED_ALIGNED = 128

# --------------------------------------------------------------------------- #
# Provider policy (keys read from env only; never printed/written/hardcoded)
# --------------------------------------------------------------------------- #
PROVIDER_ALPHAVANTAGE = "alpha_vantage"
PROVIDER_FMP = "fmp"
PROVIDER_FINNHUB = "finnhub"
PROVIDER_INTRINIO = "intrinio"

# (provider, environment-variable-name, network host) in priority order.
PROVIDER_PRIORITY = [
    (PROVIDER_ALPHAVANTAGE, "ALPHAVANTAGE_API_KEY", "www.alphavantage.co"),
    (PROVIDER_FMP, "FMP_API_KEY", "financialmodelingprep.com"),
    (PROVIDER_FINNHUB, "FINNHUB_API_KEY", "finnhub.io"),
    (PROVIDER_INTRINIO, "INTRINIO_API_KEY", "api-v2.intrinio.com"),
]
SUPPORTED_ENV_VARS = [env for _, env, _ in PROVIDER_PRIORITY]
ALLOWED_PROVIDER_HOSTS = {host for _, _, host in PROVIDER_PRIORITY}

# Redacted endpoint templates (NEVER store a real key; {apikey} stays a placeholder in every
# persisted artifact).  {symbol} is filled per ticker only inside the request, never persisted.
_REDACTED = "REDACTED"
ENDPOINT_TEMPLATES = {
    PROVIDER_ALPHAVANTAGE:
        "https://www.alphavantage.co/query?function=EARNINGS&symbol={symbol}&apikey=" + _REDACTED,
    PROVIDER_FMP:
        "https://financialmodelingprep.com/api/v3/earnings-surprises/{symbol}?apikey=" + _REDACTED,
    PROVIDER_FINNHUB:
        "https://finnhub.io/api/v1/stock/earnings?symbol={symbol}&token=" + _REDACTED,
    PROVIDER_INTRINIO:
        "https://api-v2.intrinio.com/securities/{symbol}/zacks/eps_surprises?api_key=" + _REDACTED,
}
# Conservative per-provider minimum request interval (seconds) and a hard total request cap.
PROVIDER_MIN_INTERVAL_S = {
    PROVIDER_ALPHAVANTAGE: 15.0,   # free tier ~5 requests/min
    PROVIDER_FMP: 0.40,
    PROVIDER_FINNHUB: 1.10,        # free tier ~60 requests/min
    PROVIDER_INTRINIO: 1.10,
}
MAX_TOTAL_PROVIDER_REQUESTS = 200
PROVIDER_USER_AGENT = "PaperTraderResearch/Phase3M cedric.binisti.research@example.com"

# --------------------------------------------------------------------------- #
# Earnings feature engineering
# --------------------------------------------------------------------------- #
EARNINGS_EVENT_COLUMNS = [
    "ticker", "fiscal_date_ending", "reported_date", "reported_eps", "estimated_eps", "surprise",
    "surprise_percentage", "source", "availability_date", "point_in_time_usable", "provider",
    "validation_note",
]

FAM_SURPRISE = "earnings_surprise"
FAM_SURPRISE_TREND = "earnings_surprise_trend"
FAM_RECENCY = "earnings_recency"
FAM_REVISION = "estimate_revision"
ALL_EARNINGS_FAMILIES = [FAM_SURPRISE, FAM_SURPRISE_TREND, FAM_RECENCY, FAM_REVISION]

# (feature_name, family).  estimate_revision_proxy is only populated when the provider supplies a
# prior estimate for the same event; otherwise it is left blank and documented as unavailable.
EARNINGS_FEATURE_DEFS = [
    ("eps_surprise", FAM_SURPRISE),
    ("eps_surprise_pct", FAM_SURPRISE),
    ("positive_surprise_flag", FAM_SURPRISE),
    ("negative_surprise_flag", FAM_SURPRISE),
    ("surprise_magnitude_abs", FAM_SURPRISE),
    ("trailing_4q_avg_surprise_pct", FAM_SURPRISE_TREND),
    ("trailing_4q_positive_surprise_rate", FAM_SURPRISE_TREND),
    ("trailing_8q_avg_surprise_pct", FAM_SURPRISE_TREND),
    ("surprise_acceleration", FAM_SURPRISE_TREND),
    ("days_since_last_earnings", FAM_RECENCY),
    ("earnings_event_recency_bucket", FAM_RECENCY),
    ("estimate_revision_proxy", FAM_REVISION),
]
EARNINGS_FEATURES = [n for n, _ in EARNINGS_FEATURE_DEFS]
EARNINGS_FAMILY_OF = {n: fam for n, fam in EARNINGS_FEATURE_DEFS}

EARNINGS_FEATURE_BASE_COLUMNS = [
    "ticker", "fiscal_date_ending", "reported_date", "availability_date", "point_in_time_usable",
    "provider",
]
EARNINGS_FEATURE_COLUMNS = EARNINGS_FEATURE_BASE_COLUMNS + EARNINGS_FEATURES

# Recency buckets (calendar days since last reported earnings -> ordinal bucket).
_RECENCY_BUCKETS = [(21, 0), (42, 1), (63, 2), (126, 3)]  # else bucket 4
EARNINGS_STALENESS_CAP_DAYS = 180

# --------------------------------------------------------------------------- #
# Labels reused from Phase 3-L (validation only; NO new targets here)
# --------------------------------------------------------------------------- #
HORIZONS = [21, 63, 126]
EXCESS_LABEL_TMPL = "forward_excess_return_vs_spy_%dd"
RANK_LABEL_TMPL = "forward_return_rank_by_date_%dd"
LABEL_COLUMNS = ([EXCESS_LABEL_TMPL % h for h in HORIZONS]
                 + [RANK_LABEL_TMPL % h for h in HORIZONS])
COV_GATE = {21: 0.80, 63: 0.70, 126: 0.60}

# SEC baseline features carried from the Phase 3-L panel (for the combined IC + comparison).
SEC_BASELINE_FEATURES = [
    "operating_margin", "net_margin", "fcf_margin", "operating_cash_flow_margin",
    "debt_proxy_total_liabilities_to_assets", "equity_to_assets", "asset_turnover_proxy",
    "liability_to_equity", "revenue_yoy_growth", "net_income_yoy_growth",
    "operating_income_yoy_growth", "eps_diluted_yoy_growth", "total_assets_yoy_growth",
    "operating_cash_flow_yoy_growth", "free_cash_flow_yoy_growth", "cash_conversion",
    "fcf_to_net_income", "capex_intensity", "accrual_proxy", "log_total_assets", "log_revenue_abs",
    "log_total_liabilities_abs", "filing_lag_days",
]
FAM_SEC = "sec_fundamental"

# The compact combined panel written to disk keeps only the join keys, earnings event metadata,
# earnings features, and reused labels (NOT the 23 SEC features), so the Git artifact stays well
# under 50 MB; SEC features are joined in-memory from the Phase 3-L panel for the combined IC.
COMBINED_PANEL_COLUMNS = (
    ["ticker", "sector", "scoring_date", "active_earnings_reported_date",
     "active_earnings_fiscal_date_ending", "earnings_age_days", "earnings_provider"]
    + EARNINGS_FEATURES + LABEL_COLUMNS)

# --------------------------------------------------------------------------- #
# IC gate (Phase 3-K / 3-L methodology, reused)
# --------------------------------------------------------------------------- #
IC_MIN_CROSS_SECTION = 15
DENSE_MIN_CROSS_SECTION = 25
QUINTILE_MIN_OBS = 10
TOPK = 10

MODERATE_ABS_IC = 0.03
MODERATE_MIN_DATES = 100
MODERATE_MIN_HIT = 0.52
MODERATE_MIN_NONNULL = 0.50
STRONG_ABS_IC = 0.05
STRONG_MIN_DATES = 100
STRONG_MIN_HIT = 0.55
STRONG_MIN_NONNULL = 0.60
STRONG_MIN_SPREAD_HIT = 0.55

READY_MIN_MODERATE = 5
READY_MIN_STRONG = 2
READY_MIN_STABLE_FAMILIES = 2
READY_MIN_DENSE_IC_DATES = 200

MIN_QUALIFYING_DATES_PER_YEAR = 20
FULL_MIN_YEARS = 6
PARTIAL_MIN_YEARS = 4
FAINT_SIGNAL_ABS_IC = 0.01

# Coverage gates for PASS.
PASS_MIN_PROCESSED_TICKERS = 90
PASS_MIN_EARNINGS_FEATURE_TICKERS = 75
PASS_MIN_COMBINED_ROWS = 75000

# --------------------------------------------------------------------------- #
# Recommendation vocabulary -> all route to Phase 3-N
# --------------------------------------------------------------------------- #
REC_PASSES = "EARNINGS_ESTIMATES_SIGNAL_GATE_PASSES_RESEARCH_MODEL_ALLOWED"
REC_WEAK = "EARNINGS_ESTIMATES_SIGNAL_GATE_WEAK_BUT_USEFUL"
REC_BLOCKED_KEY = "EARNINGS_ESTIMATES_GATE_BLOCKED_NEEDS_API_KEY"
REC_BLOCKED_LIMIT = "EARNINGS_ESTIMATES_GATE_BLOCKED_PROVIDER_LIMIT"
REC_FAILS = "EARNINGS_ESTIMATES_SIGNAL_GATE_FAILS"
REC_INCONCLUSIVE = "EARNINGS_ESTIMATES_SIGNAL_GATE_INCONCLUSIVE_COVERAGE"
ALLOWED_RECOMMENDATIONS = [
    REC_PASSES, REC_WEAK, REC_BLOCKED_KEY, REC_BLOCKED_LIMIT, REC_FAILS, REC_INCONCLUSIVE,
]

NEXT_PHASE_TABLE = {
    REC_PASSES: ("Research-Only Earnings + Fundamentals Model Walk-Forward",
                 "Train a research-only walk-forward model using only the cleared earnings and SEC "
                 "features; no production candidate."),
    REC_WEAK: ("Upgrade Earnings Estimates Provider or Add Revisions",
               "Improve data depth / estimate-revision history before model training."),
    REC_BLOCKED_KEY: ("Configure Earnings Estimates Provider",
                      "Set ALPHAVANTAGE_API_KEY or another supported provider key before "
                      "continuing."),
    REC_BLOCKED_LIMIT: ("Provider Entitlement Decision",
                        "Decide whether to use a paid provider, trial, or alternate source."),
    REC_FAILS: ("Stop Earnings-Surprise Route and Evaluate Other Rich Data",
                "Evaluate options, sentiment, insider transactions, or news instead of estimates."),
    REC_INCONCLUSIVE: ("Repair Earnings Estimates Coverage",
                       "Improve ticker coverage, history, or feature alignment."),
}

# CSV column schemas (IC gate; mirror Phase 3-L).
FEATURE_IC_COLUMNS = [
    "feature", "feature_family", "feature_group", "horizon", "observation_count", "date_count",
    "non_null_feature_fraction", "mean_rank_ic_excess_return", "median_rank_ic_excess_return",
    "ic_hit_rate_excess_return", "ic_ir_excess_return", "mean_rank_ic_forward_rank",
    "median_rank_ic_forward_rank", "ic_hit_rate_forward_rank", "ic_ir_forward_rank",
    "absolute_mean_ic", "top_minus_bottom_spread_quintile", "positive_spread_fraction_quintile",
    "top_minus_bottom_spread_top10", "positive_spread_fraction_top10", "signal_direction",
    "diagnostic_strength",
]
FEATURE_FAMILY_IC_COLUMNS = [
    "feature_family", "feature_group", "horizon", "num_features", "best_feature",
    "best_feature_abs_mean_ic", "median_abs_mean_ic", "mean_abs_mean_ic",
    "moderate_or_better_count", "strong_count", "best_top_minus_bottom_spread", "stable_signal",
    "stability_note",
]
HORIZON_READINESS_COLUMNS = [
    "horizon", "feature_group", "label_coverage", "avg_daily_cross_section_size",
    "valid_ic_date_count", "dense_ic_date_count", "distinct_ic_years", "best_feature",
    "best_feature_abs_mean_ic", "moderate_or_better_count", "strong_count", "horizon_readiness",
]
YEARLY_IC_COLUMNS = [
    "feature", "feature_group", "horizon", "year", "ic_date_count", "mean_rank_ic_excess_return",
    "positive_ic_year", "small_sample_caveat",
]
SECTOR_SANITY_COLUMNS = [
    "sector", "ticker_count", "row_count", "row_fraction", "tickers",
    "earnings_feature_non_null_fraction", "note",
]
EARNINGS_FEATURE_COVERAGE_COLUMNS = [
    "ticker", "sector", "feature_name", "non_null_count", "total_event_count", "coverage_fraction",
    "earliest_reported_date", "latest_reported_date",
]
LABEL_SUMMARY_COLUMNS = [
    "horizon", "combined_rows", "non_null_excess_return", "non_null_rank", "label_coverage",
    "mean_forward_excess_return_vs_spy", "earliest_scoring_date", "latest_scoring_date",
]
DECISION_TABLE_COLUMNS = ["decision_item", "value", "passed", "note"]

_LEAKAGE_CHECK_DEFS = [
    ("active_earnings_availability_present", "hard"),
    ("active_earnings_availability_before_scoring_date", "hard"),
    ("availability_date_is_reported_date_not_fiscal_end", "hard"),
    ("earnings_age_within_staleness_cap", "hard"),
    ("label_reused_from_phase3l_not_recomputed", "hard"),
]

_SOURCE_LIMITATIONS = [
    "Earnings features come from a single configured provider's historical EPS estimate / actual / "
    "surprise records; free tiers may cut off deep history or omit some tickers, which is "
    "reported as coverage rather than patched.",
    "Most free earnings endpoints expose reported actuals and the consensus AT report time but not "
    "a time series of prior estimate revisions; estimate_revision_proxy is therefore populated only "
    "when the provider supplies a prior estimate and is left blank (documented) otherwise.",
    "The equity universe and reused price labels are current-as-of the Phase 3-L panel; membership "
    "is NOT point-in-time, so this gate and any downstream result remain survivorship-biased.",
    "Forward-return labels are reused unchanged from Phase 3-L for VALIDATION ONLY; no new target is "
    "computed and no label is forward-filled in this phase.",
    "Daily scoring with overlapping 21/63/126-day forward windows means per-date ICs are serially "
    "correlated; the temporal-breadth guard (distinct dense IC years) governs the gate, not single "
    "-year magnitude.",
]


# --------------------------------------------------------------------------- #
# Small helpers (stdlib only)
# --------------------------------------------------------------------------- #
def _load_json(path):
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _to_float(x):
    if x is None:
        return None
    s = str(x).strip()
    if s == "" or s.lower() in ("none", "nan", "null"):
        return None
    s = s.replace("%", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _round(x, n=6):
    if x is None:
        return None
    try:
        return round(float(x), n)
    except (TypeError, ValueError):
        return None


def _date_part(dt_str):
    if not dt_str:
        return ""
    return str(dt_str).split("T")[0].strip()


def _parse_date(d):
    d = _date_part(d)
    if not d:
        return None
    try:
        return datetime.strptime(d, "%Y-%m-%d")
    except ValueError:
        return None


def _days_between(d_late, d_early):
    a, b = _parse_date(d_late), _parse_date(d_early)
    if a is None or b is None:
        return None
    return (a - b).days


def _mean(values):
    return sum(values) / len(values) if values else None


def _median(values):
    if not values:
        return None
    vs = sorted(values)
    n = len(vs)
    mid = n // 2
    if n % 2:
        return vs[mid]
    return (vs[mid - 1] + vs[mid]) / 2.0


def _sample_std(values):
    n = len(values)
    if n < 2:
        return None
    m = sum(values) / n
    var = sum((v - m) ** 2 for v in values) / (n - 1)
    return math.sqrt(var)


def _sign(x):
    if x is None:
        return 0
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def _ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def _spearman(xs, ys):
    if len(xs) < 3:
        return None
    return _pearson(_ranks(xs), _ranks(ys))


# --------------------------------------------------------------------------- #
# Upstream confirmation (Phase 3-L)
# --------------------------------------------------------------------------- #
def confirm_phase3l(phase3l):
    """Confirm the committed Phase 3-L result that gates this phase (spec step 1)."""
    if not phase3l:
        return {"all_confirmed": False, "phase3l_present": False}
    rec = phase3l.get("recommendation", {}) or {}
    nxt = phase3l.get("recommended_next_phase", {}) or {}
    us = phase3l.get("universe_summary", {}) or {}
    al = phase3l.get("alignment_summary", {}) or {}
    lk = phase3l.get("leakage_check_summary", {}) or {}
    checks = {
        "phase3l_present": True,
        "phase_is_3l": phase3l.get("phase") == "3-L",
        "recommendation_is_weak_add_revisions":
            rec.get("recommendation") == PHASE3L_EXPECTED_RECOMMENDATION,
        "next_phase_is_3m": nxt.get("phase") == "3-M",
        "processed_ticker_count_128":
            us.get("processed_ticker_count") == PHASE3L_EXPECTED_PROCESSED,
        "aligned_ticker_count_128":
            al.get("aligned_ticker_count") == PHASE3L_EXPECTED_ALIGNED,
        "leakage_failure_count_zero": lk.get("leakage_failure_count") == 0,
        "research_model_allowed_next_false": phase3l.get("research_model_allowed_next") is False,
        "model_trained_false": phase3l.get("model_trained") is False,
        "predictions_computed_false": phase3l.get("predictions_computed") is False,
        "portfolio_weights_computed_false": phase3l.get("portfolio_weights_computed") is False,
        "production_model_candidate_created_false":
            phase3l.get("production_model_candidate_created") is False,
    }
    checks["all_confirmed"] = all(checks.values())
    return checks


def load_sector_map(path):
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            t = (row.get("ticker") or "").strip().upper()
            if not t or t == "SPY":
                continue
            out[t] = {
                "sector": (row.get("sector") or "").strip(),
                "industry": (row.get("industry") or "").strip(),
            }
    return out


def load_phase3l_universe(identity_path, sector_map):
    """Universe tickers from the Phase 3-L identity CSV, falling back to the sector map."""
    tickers = []
    if os.path.isfile(identity_path):
        with open(identity_path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                t = (row.get("ticker") or "").strip().upper()
                if t and t != "SPY":
                    tickers.append(t)
    if not tickers:
        tickers = sorted(sector_map)
    return sorted(set(tickers))


# --------------------------------------------------------------------------- #
# Provider detection (keys read from env only; values never stored/printed)
# --------------------------------------------------------------------------- #
def detect_providers(env=None):
    """Return (selected_provider, selected_env_var, detected_keys, host).

    detected_keys maps every supported env-var NAME to a boolean (present/absent).  No key VALUE is
    ever read into the report.  Selection follows the strict priority order.
    """
    env = os.environ if env is None else env
    detected = {}
    for _provider, env_var, _host in PROVIDER_PRIORITY:
        detected[env_var] = bool((env.get(env_var) or "").strip())
    for provider, env_var, host in PROVIDER_PRIORITY:
        if detected[env_var]:
            return provider, env_var, detected, host
    return None, None, detected, None


# --------------------------------------------------------------------------- #
# Provider client (cache-first; throttled; key never logged or persisted)
# --------------------------------------------------------------------------- #
class ProviderClient:
    """Minimal stdlib provider client.  The API key is held only as a local attribute, is never
    written to disk, never printed, and never placed in any persisted endpoint string."""

    def __init__(self, provider, api_key, host, raw_dir):
        self.provider = provider
        self._api_key = api_key            # private; never serialized
        self.host = host
        self.raw_dir = raw_dir
        self.request_count = 0
        self.cache_hits = 0
        self.network_requests = 0
        self.errors = []
        self.rate_limit_or_premium_errors = 0
        self._last_request_time = 0.0
        self._min_interval = PROVIDER_MIN_INTERVAL_S.get(provider, 1.0)

    def _throttle(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    def _request_url(self, symbol):
        """Build the live request URL (with the key) - used only transiently, never persisted."""
        enc = urllib.parse.quote(symbol)
        key = urllib.parse.quote(self._api_key)
        if self.provider == PROVIDER_ALPHAVANTAGE:
            return ("https://www.alphavantage.co/query?function=EARNINGS&symbol=%s&apikey=%s"
                    % (enc, key))
        if self.provider == PROVIDER_FMP:
            return ("https://financialmodelingprep.com/api/v3/earnings-surprises/%s?apikey=%s"
                    % (enc, key))
        if self.provider == PROVIDER_FINNHUB:
            return "https://finnhub.io/api/v1/stock/earnings?symbol=%s&token=%s" % (enc, key)
        if self.provider == PROVIDER_INTRINIO:
            return ("https://api-v2.intrinio.com/securities/%s/zacks/eps_surprises?api_key=%s"
                    % (enc, key))
        raise ValueError("unsupported provider: %s" % self.provider)

    def _host_allowed(self, url):
        host = urllib.parse.urlparse(url).netloc
        return host in ALLOWED_PROVIDER_HOSTS

    def _fetch_json(self, symbol):
        url = self._request_url(symbol)
        if not self._host_allowed(url):
            raise ValueError("refusing non-provider URL host")
        if self.request_count >= MAX_TOTAL_PROVIDER_REQUESTS:
            raise RuntimeError("provider request cap reached (%d)" % MAX_TOTAL_PROVIDER_REQUESTS)
        self._throttle()
        req = urllib.request.Request(url, headers={
            "User-Agent": PROVIDER_USER_AGENT, "Accept": "application/json"})
        self.request_count += 1
        self._last_request_time = time.time()
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read()
        self.network_requests += 1
        return json.loads(raw.decode("utf-8"))

    @staticmethod
    def _looks_rate_limited(payload):
        """Detect common rate-limit / entitlement / premium responses across providers."""
        if isinstance(payload, dict):
            for k in ("Note", "Information", "error", "error_message", "message"):
                v = payload.get(k)
                if isinstance(v, str) and v.strip():
                    low = v.lower()
                    if any(s in low for s in (
                            "rate limit", "frequency", "premium", "thank you for using",
                            "higher api call", "not entitled", "subscription", "upgrade",
                            "api key", "forbidden", "limit")):
                        return True
        return False

    def get_earnings(self, symbol):
        """Return (payload, source) reading cache first; raises on rate-limit/premium errors.

        The cached file holds only the provider RESPONSE BODY (which contains no key), keyed by
        symbol - the request URL (which carries the key) is never written.
        """
        cache_path = os.path.join(self.raw_dir, "%s_%s.json" % (self.provider, symbol.upper()))
        if os.path.isfile(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                self.cache_hits += 1
                return json.load(f), "cache"
        payload = self._fetch_json(symbol)
        if self._looks_rate_limited(payload):
            self.rate_limit_or_premium_errors += 1
            raise _ProviderLimited("provider returned a rate-limit / entitlement response")
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))
        return payload, "network"


class _ProviderLimited(Exception):
    """Raised when the provider signals a rate-limit / entitlement / premium-required error."""


# --------------------------------------------------------------------------- #
# Provider-specific normalization to canonical earnings events
# --------------------------------------------------------------------------- #
def _norm_event(ticker, provider, fiscal_date_ending, reported_date, reported_eps, estimated_eps,
                surprise, surprise_pct, source):
    """Canonical earnings event row with point-in-time availability rules (spec step 5)."""
    rep = _date_part(reported_date)
    fde = _date_part(fiscal_date_ending)
    reported_eps = _to_float(reported_eps)
    estimated_eps = _to_float(estimated_eps)
    surprise = _to_float(surprise)
    surprise_pct = _to_float(surprise_pct)
    if surprise is None and reported_eps is not None and estimated_eps is not None:
        surprise = reported_eps - estimated_eps
    if (surprise_pct is None and surprise is not None and estimated_eps not in (None, 0)):
        surprise_pct = 100.0 * surprise / abs(estimated_eps)
    # availability_date MUST be the report date (never back-dated to the fiscal quarter end).
    availability = rep
    pit_usable = bool(rep)
    if not rep:
        note = "no reported_date - not point-in-time usable (quarantined)"
    elif fde and rep <= fde:
        note = "reported_date on/before fiscal_date_ending - suspect; flagged not usable"
        pit_usable = False
    else:
        note = "availability_date = reported_date (filing/report timestamp, not fiscal end)"
    return {
        "ticker": ticker, "fiscal_date_ending": fde, "reported_date": rep,
        "reported_eps": reported_eps, "estimated_eps": estimated_eps,
        "surprise": _round(surprise, 6) if surprise is not None else None,
        "surprise_percentage": _round(surprise_pct, 6) if surprise_pct is not None else None,
        "source": source, "availability_date": availability,
        "point_in_time_usable": pit_usable, "provider": provider, "validation_note": note,
    }


def normalize_events(ticker, provider, payload):
    """Dispatch to the provider-specific parser; return a list of canonical event rows."""
    if provider == PROVIDER_ALPHAVANTAGE:
        return _normalize_alphavantage(ticker, payload)
    if provider == PROVIDER_FMP:
        return _normalize_fmp(ticker, payload)
    if provider == PROVIDER_FINNHUB:
        return _normalize_finnhub(ticker, payload)
    if provider == PROVIDER_INTRINIO:
        return _normalize_intrinio(ticker, payload)
    return []


def _normalize_alphavantage(ticker, payload):
    src = "AlphaVantage EARNINGS quarterlyEarnings"
    out = []
    quarters = (payload or {}).get("quarterlyEarnings", []) or []
    for q in quarters:
        out.append(_norm_event(
            ticker, PROVIDER_ALPHAVANTAGE, q.get("fiscalDateEnding"), q.get("reportedDate"),
            q.get("reportedEPS"), q.get("estimatedEPS"), q.get("surprise"),
            q.get("surprisePercentage"), src))
    return out


def _normalize_fmp(ticker, payload):
    src = "FMP earnings-surprises"
    out = []
    for q in (payload or []):
        if not isinstance(q, dict):
            continue
        out.append(_norm_event(
            ticker, PROVIDER_FMP, q.get("date"), q.get("date"), q.get("actualEarningResult"),
            q.get("estimatedEarning"), None, None, src))
    return out


def _normalize_finnhub(ticker, payload):
    src = "Finnhub stock/earnings"
    out = []
    rows = (payload or []) if isinstance(payload, list) else (payload or {}).get("earnings", [])
    for q in (rows or []):
        if not isinstance(q, dict):
            continue
        out.append(_norm_event(
            ticker, PROVIDER_FINNHUB, q.get("period"), q.get("period"), q.get("actual"),
            q.get("estimate"), q.get("surprise"), q.get("surprisePercent"), src))
    return out


def _normalize_intrinio(ticker, payload):
    src = "Intrinio zacks/eps_surprises"
    out = []
    rows = (payload or {}).get("eps_surprises", []) if isinstance(payload, dict) else []
    for q in (rows or []):
        if not isinstance(q, dict):
            continue
        out.append(_norm_event(
            ticker, PROVIDER_INTRINIO, q.get("fiscal_year_end"),
            q.get("report_date") or q.get("reported_date"), q.get("eps_actual"),
            q.get("eps_mean_estimate"), q.get("eps_amount_diff"), q.get("eps_percent_diff"), src))
    return out


# --------------------------------------------------------------------------- #
# Earnings feature construction (trailing-only, point-in-time)
# --------------------------------------------------------------------------- #
def _recency_bucket(days):
    if days is None:
        return None
    for thresh, bucket in _RECENCY_BUCKETS:
        if days <= thresh:
            return bucket
    return 4


def build_earnings_features(events_by_ticker):
    """Per ticker, build trailing-only features for each usable event (ordered by reported_date).

    Every feature uses only the current event and strictly-prior events; nothing forward-looking is
    used.  The event becomes active starting the first trading day after reported_date (the join in
    combine_with_panel enforces availability_date < scoring_date).
    """
    feature_rows = []
    for ticker in sorted(events_by_ticker):
        events = [e for e in events_by_ticker[ticker]
                  if e.get("point_in_time_usable") and e.get("reported_date")]
        events.sort(key=lambda e: (e.get("reported_date") or "", e.get("fiscal_date_ending") or ""))
        prior_surprise_pcts = []
        prev_reported_date = None
        prev_surprise_pct = None
        for e in events:
            sp = e.get("surprise_percentage")
            s = e.get("surprise")
            row = {
                "ticker": ticker, "fiscal_date_ending": e.get("fiscal_date_ending"),
                "reported_date": e.get("reported_date"),
                "availability_date": e.get("availability_date"),
                "point_in_time_usable": e.get("point_in_time_usable"),
                "provider": e.get("provider"),
                "eps_surprise": s,
                "eps_surprise_pct": sp,
                "positive_surprise_flag": (1 if (s is not None and s > 0) else
                                           (0 if s is not None else None)),
                "negative_surprise_flag": (1 if (s is not None and s < 0) else
                                           (0 if s is not None else None)),
                "surprise_magnitude_abs": (abs(s) if s is not None else None),
            }
            trail4 = prior_surprise_pcts[-4:]
            trail8 = prior_surprise_pcts[-8:]
            row["trailing_4q_avg_surprise_pct"] = _round(_mean(trail4), 6) if trail4 else None
            row["trailing_4q_positive_surprise_rate"] = (
                _round(sum(1 for v in trail4 if v > 0) / len(trail4), 4) if trail4 else None)
            row["trailing_8q_avg_surprise_pct"] = _round(_mean(trail8), 6) if trail8 else None
            row["surprise_acceleration"] = (
                _round(sp - prev_surprise_pct, 6)
                if (sp is not None and prev_surprise_pct is not None) else None)
            days_since = _days_between(e.get("reported_date"), prev_reported_date)
            row["days_since_last_earnings"] = days_since
            row["earnings_event_recency_bucket"] = _recency_bucket(0)  # active on report; 0d old
            # estimate_revision_proxy: only available if the provider supplies a *prior* estimate
            # for this event; the free endpoints used here do not, so it is left blank (documented).
            row["estimate_revision_proxy"] = None
            feature_rows.append(row)
            if sp is not None:
                prior_surprise_pcts.append(sp)
                prev_surprise_pct = sp
            prev_reported_date = e.get("reported_date")
    return feature_rows


def build_earnings_feature_coverage(feature_rows, sector_of):
    by = {}
    for r in feature_rows:
        t = r["ticker"]
        d = by.setdefault(t, {"rows": [], "dates": []})
        d["rows"].append(r)
        if r.get("reported_date"):
            d["dates"].append(r["reported_date"])
    out = []
    for t in sorted(by):
        rows = by[t]["rows"]
        dates = sorted(by[t]["dates"])
        total = len(rows)
        for fname in EARNINGS_FEATURES:
            nn = sum(1 for r in rows if r.get(fname) is not None)
            out.append({
                "ticker": t, "sector": sector_of.get(t, ""), "feature_name": fname,
                "non_null_count": nn, "total_event_count": total,
                "coverage_fraction": _round(nn / total, 4) if total else 0.0,
                "earliest_reported_date": dates[0] if dates else "",
                "latest_reported_date": dates[-1] if dates else "",
            })
    return out


# --------------------------------------------------------------------------- #
# Combine with the Phase 3-L aligned panel (reuse labels; no new targets)
# --------------------------------------------------------------------------- #
def _active_feature_snapshots(feature_rows):
    """ticker -> list of (availability_date, feature_dict) ascending by availability_date."""
    by_ticker = {}
    for r in feature_rows:
        av = _date_part(r.get("availability_date"))
        if not av:
            continue
        by_ticker.setdefault(r["ticker"], []).append((av, r))
    for t in by_ticker:
        by_ticker[t].sort(key=lambda x: x[0])
    return by_ticker


def combine_with_panel(panel_path, active_by_ticker, want_sec=True):
    """Stream the Phase 3-L panel, attach the active earnings event per (ticker, scoring_date).

    Returns (combined_rows, sec_present).  Each combined row carries the reused labels, the SEC
    baseline features (in-memory only, for the combined IC), and the active earnings features whose
    availability_date < scoring_date and whose age <= the earnings staleness cap.
    """
    combined = []
    if not os.path.isfile(panel_path):
        return combined, False
    # per-ticker cursor over the ascending active earnings snapshots
    cursor = {t: -1 for t in active_by_ticker}
    last_ticker = None
    sec_seen = False
    with open(panel_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        sec_cols = [c for c in SEC_BASELINE_FEATURES if c in header]
        sec_seen = bool(sec_cols)
        for prow in reader:
            t = (prow.get("ticker") or "").strip().upper()
            scoring_date = (prow.get("scoring_date") or "").strip()
            if not t or not scoring_date:
                continue
            if t != last_ticker:
                last_ticker = t
            snaps = active_by_ticker.get(t)
            active = None
            if snaps:
                idx = cursor[t]
                while idx + 1 < len(snaps) and snaps[idx + 1][0] < scoring_date:
                    idx += 1
                cursor[t] = idx
                if idx >= 0:
                    av, feat = snaps[idx]
                    age = _days_between(scoring_date, av)
                    if age is not None and 0 <= age <= EARNINGS_STALENESS_CAP_DAYS:
                        active = (av, feat, age)
            row = {
                "ticker": t, "sector": (prow.get("sector") or "").strip(),
                "scoring_date": scoring_date,
            }
            for lab in LABEL_COLUMNS:
                row[lab] = _to_float(prow.get(lab))
            if want_sec:
                for c in sec_cols:
                    row["_sec_%s" % c] = _to_float(prow.get(c))
            if active is not None:
                av, feat, age = active
                row["active_earnings_reported_date"] = feat.get("reported_date")
                row["active_earnings_fiscal_date_ending"] = feat.get("fiscal_date_ending")
                row["earnings_age_days"] = age
                row["earnings_provider"] = feat.get("provider")
                # recompute the recency bucket against the scoring date
                days_since_active = age
                row["earnings_event_recency_bucket"] = _recency_bucket(days_since_active)
                for fname in EARNINGS_FEATURES:
                    if fname == "earnings_event_recency_bucket":
                        continue
                    row[fname] = feat.get(fname)
            else:
                row["active_earnings_reported_date"] = ""
                row["active_earnings_fiscal_date_ending"] = ""
                row["earnings_age_days"] = None
                row["earnings_provider"] = ""
                for fname in EARNINGS_FEATURES:
                    row[fname] = None
            combined.append(row)
    return combined, sec_seen


def evaluate_leakage_summary(combined_rows):
    counts = {name: 0 for name, _ in _LEAKAGE_CHECK_DEFS}
    n_active = 0
    for r in combined_rows:
        rep = _date_part(r.get("active_earnings_reported_date"))
        fde = _date_part(r.get("active_earnings_fiscal_date_ending"))
        scoring = (r.get("scoring_date") or "").strip()
        age = r.get("earnings_age_days")
        if not rep:
            continue  # no active earnings event on this row -> not subject to earnings leakage
        n_active += 1
        results = {
            "active_earnings_availability_present": bool(rep),
            "active_earnings_availability_before_scoring_date": bool(rep) and rep < scoring,
            "availability_date_is_reported_date_not_fiscal_end": bool(rep) and rep != fde,
            "earnings_age_within_staleness_cap":
                age is not None and 0 <= age <= EARNINGS_STALENESS_CAP_DAYS,
            "label_reused_from_phase3l_not_recomputed": True,
        }
        for name, _sev in _LEAKAGE_CHECK_DEFS:
            if not results[name]:
                counts[name] += 1
    notes = {
        "active_earnings_availability_present": "active rows carry a reported_date availability",
        "active_earnings_availability_before_scoring_date":
            "earnings event became public strictly before the scoring day",
        "availability_date_is_reported_date_not_fiscal_end":
            "availability is the report date, never the fiscal_date_ending",
        "earnings_age_within_staleness_cap":
            "active earnings event is within the %d-day staleness cap" % EARNINGS_STALENESS_CAP_DAYS,
        "label_reused_from_phase3l_not_recomputed":
            "forward labels are reused unchanged from Phase 3-L (no new target computed)",
    }
    out = []
    total_failures = 0
    for name, sev in _LEAKAGE_CHECK_DEFS:
        total_failures += counts[name]
        out.append({"check_name": name, "severity": sev, "rows_checked": n_active,
                    "failures": counts[name], "passed": counts[name] == 0, "note": notes[name]})
    return out, total_failures


# --------------------------------------------------------------------------- #
# IC gate machinery (reused from Phase 3-L)
# --------------------------------------------------------------------------- #
def group_by_date(rows):
    groups = {}
    for r in rows:
        groups.setdefault((r.get("scoring_date") or "").strip(), []).append(r)
    return [(d, groups[d]) for d in sorted(groups) if d]


def daily_rank_ic(groups, feature_key, label_col):
    ic_series, obs = [], 0
    for date, rs in groups:
        xs, ys = [], []
        for r in rs:
            fv = _to_float(r.get(feature_key))
            lv = _to_float(r.get(label_col))
            if fv is not None and lv is not None:
                xs.append(fv)
                ys.append(lv)
        if len(xs) >= IC_MIN_CROSS_SECTION:
            ic = _spearman(xs, ys)
            if ic is not None:
                ic_series.append((date, ic))
                obs += len(xs)
    return ic_series, obs


def summarize_ic(ic_series):
    ics = [ic for _, ic in ic_series]
    if not ics:
        return {"mean": None, "median": None, "hit_rate": None, "ir": None, "date_count": 0}
    mean_ic = _mean(ics)
    std_ic = _sample_std(ics)
    s = _sign(mean_ic)
    if s > 0:
        hit = sum(1 for v in ics if v > 0) / len(ics)
    elif s < 0:
        hit = sum(1 for v in ics if v < 0) / len(ics)
    else:
        hit = 0.0
    ir = (mean_ic / std_ic) if (std_ic and std_ic > 0) else None
    return {"mean": _round(mean_ic, 6), "median": _round(_median(ics), 6),
            "hit_rate": _round(hit, 4), "ir": _round(ir, 4) if ir is not None else None,
            "date_count": len(ics)}


def bucket_spread_series(groups, feature_key, label_col, quintile=True):
    series = []
    for date, rs in groups:
        pairs = []
        for r in rs:
            fv = _to_float(r.get(feature_key))
            lv = _to_float(r.get(label_col))
            if fv is not None and lv is not None:
                pairs.append((fv, lv))
        n = len(pairs)
        if quintile:
            if n < QUINTILE_MIN_OBS:
                continue
            k = max(1, n // 5)
        else:
            if n < 2 * TOPK:
                continue
            k = TOPK
        pairs.sort(key=lambda p: p[0])
        bottom = pairs[:k]
        top = pairs[-k:]
        spread = (sum(p[1] for p in top) / k) - (sum(p[1] for p in bottom) / k)
        series.append((date, spread))
    return series


def summarize_spread(series):
    spreads = [s for _, s in series]
    if not spreads:
        return {"mean": None, "positive_fraction": None, "hit_rate": None, "date_count": 0}
    mean_s = _mean(spreads)
    pos_frac = sum(1 for s in spreads if s > 0) / len(spreads)
    sgn = _sign(mean_s)
    hit = sum(1 for s in spreads if _sign(s) == sgn) / len(spreads) if sgn != 0 else 0.0
    return {"mean": _round(mean_s, 6), "positive_fraction": _round(pos_frac, 4),
            "hit_rate": _round(hit, 4), "date_count": len(spreads)}


def _classify_strength(excess, spread_q, non_null_frac):
    dc = excess["date_count"]
    abs_ic = abs(excess["mean"]) if excess["mean"] is not None else 0.0
    hit = excess["hit_rate"] or 0.0
    if dc < MODERATE_MIN_DATES or (non_null_frac or 0.0) < MODERATE_MIN_NONNULL:
        return "unusable"
    spread_sign_ok = (
        spread_q["mean"] is not None and excess["mean"] is not None
        and _sign(spread_q["mean"]) == _sign(excess["mean"]) and _sign(excess["mean"]) != 0)
    spread_hit_ok = (spread_q["hit_rate"] or 0.0) >= STRONG_MIN_SPREAD_HIT
    is_strong = (
        abs_ic >= STRONG_ABS_IC and dc >= STRONG_MIN_DATES and hit >= STRONG_MIN_HIT
        and (non_null_frac or 0.0) >= STRONG_MIN_NONNULL and spread_sign_ok and spread_hit_ok)
    if is_strong:
        return "strong"
    is_moderate = (
        abs_ic >= MODERATE_ABS_IC and dc >= MODERATE_MIN_DATES and hit >= MODERATE_MIN_HIT
        and (non_null_frac or 0.0) >= MODERATE_MIN_NONNULL)
    return "moderate" if is_moderate else "weak"


def _signal_direction(excess):
    if excess["date_count"] < 30 or excess["mean"] is None:
        return "unstable"
    if (excess["hit_rate"] or 0.0) < 0.5 or abs(excess["mean"]) < 0.005:
        return "unstable"
    return "positive" if excess["mean"] > 0 else "negative"


def compute_feature_ic(groups, feature_specs, non_null_frac):
    """feature_specs: list of (feature_key, display_name, family, group)."""
    rows = []
    excess_ic_series = {}
    for feature_key, name, fam, group in feature_specs:
        nnf = non_null_frac.get(feature_key)
        for h in HORIZONS:
            ex_col = EXCESS_LABEL_TMPL % h
            rk_col = RANK_LABEL_TMPL % h
            ex_series, obs = daily_rank_ic(groups, feature_key, ex_col)
            rk_series, _ = daily_rank_ic(groups, feature_key, rk_col)
            excess_ic_series[(name, group, h)] = ex_series
            ex = summarize_ic(ex_series)
            rk = summarize_ic(rk_series)
            spread_q = summarize_spread(bucket_spread_series(groups, feature_key, ex_col, True))
            spread_k = summarize_spread(bucket_spread_series(groups, feature_key, ex_col, False))
            abs_mean_ic = _round(abs(ex["mean"]), 6) if ex["mean"] is not None else None
            rows.append({
                "feature": name, "feature_family": fam, "feature_group": group,
                "horizon": "%dd" % h, "_horizon_days": h, "observation_count": obs,
                "date_count": ex["date_count"],
                "non_null_feature_fraction": _round(nnf, 4) if nnf is not None else None,
                "mean_rank_ic_excess_return": ex["mean"],
                "median_rank_ic_excess_return": ex["median"],
                "ic_hit_rate_excess_return": ex["hit_rate"], "ic_ir_excess_return": ex["ir"],
                "mean_rank_ic_forward_rank": rk["mean"],
                "median_rank_ic_forward_rank": rk["median"],
                "ic_hit_rate_forward_rank": rk["hit_rate"], "ic_ir_forward_rank": rk["ir"],
                "absolute_mean_ic": abs_mean_ic,
                "top_minus_bottom_spread_quintile": spread_q["mean"],
                "positive_spread_fraction_quintile": spread_q["positive_fraction"],
                "top_minus_bottom_spread_top10": spread_k["mean"],
                "positive_spread_fraction_top10": spread_k["positive_fraction"],
                "signal_direction": _signal_direction(ex),
                "diagnostic_strength": _classify_strength(ex, spread_q, nnf),
            })
    return rows, excess_ic_series


def compute_family_summary(feature_rows):
    out = []
    groups = sorted({r["feature_group"] for r in feature_rows})
    families = sorted({r["feature_family"] for r in feature_rows})
    for group in groups:
        for fam in families:
            for h in HORIZONS:
                sub = [r for r in feature_rows if r["feature_group"] == group
                       and r["feature_family"] == fam and r["_horizon_days"] == h]
                if not sub:
                    continue
                judgeable = [r for r in sub if r["absolute_mean_ic"] is not None
                             and r["diagnostic_strength"] != "unusable"]
                abs_ics = [r["absolute_mean_ic"] for r in judgeable]
                mod_count = sum(1 for r in sub
                                if r["diagnostic_strength"] in ("moderate", "strong"))
                strong_count = sum(1 for r in sub if r["diagnostic_strength"] == "strong")
                best = max(judgeable, key=lambda r: r["absolute_mean_ic"]) if judgeable else None
                spreads = [abs(r["top_minus_bottom_spread_quintile"]) for r in sub
                           if r["top_minus_bottom_spread_quintile"] is not None]
                stable = mod_count >= 1 and any(
                    r["signal_direction"] in ("positive", "negative")
                    and r["diagnostic_strength"] in ("moderate", "strong") for r in sub)
                if stable:
                    note = "at least one moderate-or-better feature with a consistent IC direction"
                elif judgeable:
                    note = "features measurable but none clears the moderate gate on this horizon"
                else:
                    note = "no judgeable feature (insufficient dates or low non-null coverage)"
                out.append({
                    "feature_family": fam, "feature_group": group, "horizon": "%dd" % h,
                    "_horizon_days": h, "num_features": len(sub),
                    "best_feature": best["feature"] if best else "",
                    "best_feature_abs_mean_ic": best["absolute_mean_ic"] if best else None,
                    "median_abs_mean_ic": _round(_median(abs_ics), 6) if abs_ics else None,
                    "mean_abs_mean_ic": _round(_mean(abs_ics), 6) if abs_ics else None,
                    "moderate_or_better_count": mod_count, "strong_count": strong_count,
                    "best_top_minus_bottom_spread": _round(max(spreads), 6) if spreads else None,
                    "stable_signal": stable, "stability_note": note,
                })
    return out


def compute_horizon_readiness(feature_rows, groups, label_coverage, group_name):
    out = []
    for h in HORIZONS:
        ex_col = EXCESS_LABEL_TMPL % h
        sub = [r for r in feature_rows if r["_horizon_days"] == h
               and r["feature_group"] == group_name]
        judgeable = [r for r in sub if r["absolute_mean_ic"] is not None
                     and r["diagnostic_strength"] != "unusable"]
        mod_count = sum(1 for r in sub if r["diagnostic_strength"] in ("moderate", "strong"))
        strong_count = sum(1 for r in sub if r["diagnostic_strength"] == "strong")
        best = max(judgeable, key=lambda r: r["absolute_mean_ic"]) if judgeable else None
        cs_sizes, valid_ic_dates, dense_ic_dates = [], 0, 0
        dense_by_year = {}
        for date, rs in groups:
            labeled = sum(1 for r in rs if _to_float(r.get(ex_col)) is not None)
            if labeled >= 1:
                cs_sizes.append(labeled)
            if labeled >= IC_MIN_CROSS_SECTION:
                valid_ic_dates += 1
            if labeled >= DENSE_MIN_CROSS_SECTION:
                dense_ic_dates += 1
                dense_by_year[date[:4]] = dense_by_year.get(date[:4], 0) + 1
        distinct_ic_years = sum(1 for n in dense_by_year.values()
                                if n >= MIN_QUALIFYING_DATES_PER_YEAR)
        breadth_ok = distinct_ic_years >= FULL_MIN_YEARS
        if mod_count >= 1 and dense_ic_dates >= READY_MIN_DENSE_IC_DATES and breadth_ok:
            readiness = "ready_for_research_model"
        elif valid_ic_dates >= MODERATE_MIN_DATES and judgeable:
            readiness = "diagnostic_only"
        else:
            readiness = "not_ready"
        out.append({
            "horizon": "%dd" % h, "_horizon_days": h, "feature_group": group_name,
            "label_coverage": label_coverage.get(h),
            "avg_daily_cross_section_size": _round(_mean(cs_sizes), 2) if cs_sizes else None,
            "valid_ic_date_count": valid_ic_dates, "dense_ic_date_count": dense_ic_dates,
            "distinct_ic_years": distinct_ic_years,
            "best_feature": best["feature"] if best else "",
            "best_feature_abs_mean_ic": best["absolute_mean_ic"] if best else None,
            "moderate_or_better_count": mod_count, "strong_count": strong_count,
            "horizon_readiness": readiness,
        })
    return out


def pick_best_features(feature_rows, group_name, top_n=5):
    best_by_feat = {}
    for r in feature_rows:
        if (r["feature_group"] != group_name or r["absolute_mean_ic"] is None
                or r["diagnostic_strength"] == "unusable"):
            continue
        cur = best_by_feat.get(r["feature"])
        if cur is None or r["absolute_mean_ic"] > cur:
            best_by_feat[r["feature"]] = r["absolute_mean_ic"]
    ranked = sorted(best_by_feat.items(), key=lambda kv: kv[1], reverse=True)
    return [f for f, _ in ranked[:top_n]]


def compute_yearly_diagnostics(best_features, group_name, excess_ic_series, feature_rows):
    rows = []
    summary = []
    overall_mean = {(r["feature"], r["feature_group"], r["_horizon_days"]):
                    r["mean_rank_ic_excess_return"] for r in feature_rows}
    for feat in best_features:
        for h in HORIZONS:
            series = excess_ic_series.get((feat, group_name, h), [])
            by_year = {}
            for date, ic in series:
                by_year.setdefault(date[:4], []).append(ic)
            base_sign = _sign(overall_mean.get((feat, group_name, h)))
            pos_years, bad_years = 0, []
            for yr in sorted(by_year):
                ics = by_year[yr]
                ymean = _mean(ics)
                positive = base_sign != 0 and _sign(ymean) == base_sign
                if positive:
                    pos_years += 1
                elif base_sign != 0 and len(ics) >= 12:
                    bad_years.append(yr)
                rows.append({
                    "feature": feat, "feature_group": group_name, "horizon": "%dd" % h, "year": yr,
                    "ic_date_count": len(ics), "mean_rank_ic_excess_return": _round(ymean, 6),
                    "positive_ic_year": positive, "small_sample_caveat": len(ics) < 12,
                })
            n_years = len(by_year)
            summary.append({
                "feature": feat, "feature_group": group_name, "horizon": "%dd" % h,
                "year_count": n_years,
                "positive_ic_year_fraction": _round(pos_years / n_years, 4) if n_years else None,
                "bad_years": bad_years,
            })
    return rows, summary


def compute_sector_sanity(combined_rows):
    total = len(combined_rows)
    by_sector = {}
    for r in combined_rows:
        sec = (r.get("sector") or "").strip() or "UNKNOWN"
        d = by_sector.setdefault(sec, {"rows": 0, "tickers": set(), "ef_nn": 0})
        d["rows"] += 1
        d["tickers"].add((r.get("ticker") or "").strip().upper())
        if _to_float(r.get("eps_surprise_pct")) is not None:
            d["ef_nn"] += 1
    out = []
    for sec in sorted(by_sector):
        d = by_sector[sec]
        out.append({
            "sector": sec, "ticker_count": len(d["tickers"]), "row_count": d["rows"],
            "row_fraction": _round(d["rows"] / total, 4) if total else None,
            "tickers": "|".join(sorted(d["tickers"])),
            "earnings_feature_non_null_fraction": _round(d["ef_nn"] / d["rows"], 4) if d["rows"]
            else 0.0,
            "note": "thin sector (<=2 tickers)" if len(d["tickers"]) <= 2 else "",
        })
    return out


# --------------------------------------------------------------------------- #
# Phase 3-L SEC baseline (read the committed feature_ic_summary.csv)
# --------------------------------------------------------------------------- #
def load_phase3l_sec_baseline(feature_ic_path):
    """Read Phase 3-L feature_ic_summary.csv -> moderate/strong counts for the SEC baseline."""
    if not os.path.isfile(feature_ic_path):
        return {"available": False, "moderate_or_better_count": None, "strong_count": None,
                "moderate_or_better_features": [], "strong_features": [], "max_absolute_mean_ic":
                None}
    mod, strong = set(), set()
    max_abs = None
    with open(feature_ic_path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            ds = (row.get("diagnostic_strength") or "").strip()
            feat = (row.get("feature") or "").strip()
            am = _to_float(row.get("absolute_mean_ic"))
            if am is not None and (max_abs is None or am > max_abs):
                max_abs = am
            if ds in ("moderate", "strong"):
                mod.add(feat)
            if ds == "strong":
                strong.add(feat)
    return {"available": True, "moderate_or_better_count": len(mod), "strong_count": len(strong),
            "moderate_or_better_features": sorted(mod), "strong_features": sorted(strong),
            "max_absolute_mean_ic": _round(max_abs, 6) if max_abs is not None else None}


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #
def _group_metrics(feature_rows, horizon_rows, group_name):
    mod = {r["feature"] for r in feature_rows if r["feature_group"] == group_name
           and r["diagnostic_strength"] in ("moderate", "strong")}
    strong = {r["feature"] for r in feature_rows if r["feature_group"] == group_name
              and r["diagnostic_strength"] == "strong"}
    abs_ics = [r["absolute_mean_ic"] for r in feature_rows if r["feature_group"] == group_name
               and r["absolute_mean_ic"] is not None and r["diagnostic_strength"] != "unusable"]
    hsub = [h for h in horizon_rows if h["feature_group"] == group_name]
    any_ready = any(h["horizon_readiness"] == "ready_for_research_model" for h in hsub)
    max_years = max((h.get("distinct_ic_years", 0) for h in hsub), default=0)
    return {
        "moderate_feature_count": len(mod), "moderate_features": sorted(mod),
        "strong_feature_count": len(strong), "strong_features": sorted(strong),
        "any_horizon_ready": any_ready, "max_distinct_ic_years": max_years,
        "max_abs_ic": _round(max(abs_ics), 6) if abs_ics else None,
    }


def decide(confirmed, provider_selected, provider_limited, coverage_ok, leakage_failures,
           label_cov_ok, family_rows, earnings_metrics, combined_metrics, sec_baseline):
    if not confirmed.get("all_confirmed"):
        return REC_INCONCLUSIVE
    if provider_selected is None:
        return REC_BLOCKED_KEY
    if provider_limited:
        return REC_BLOCKED_LIMIT
    if leakage_failures > 0:
        return REC_INCONCLUSIVE
    if not coverage_ok or not label_cov_ok:
        return REC_INCONCLUSIVE
    stable_families = sum(1 for f in family_rows if f["stable_signal"]
                          and f["feature_group"] in ("earnings_only", "combined"))
    # Use the stronger of earnings-only / combined for the signal-density gate.
    best = max((earnings_metrics, combined_metrics),
               key=lambda m: (m["moderate_feature_count"], m["strong_feature_count"]))
    improves = True
    if sec_baseline.get("available") and sec_baseline.get("moderate_or_better_count") is not None:
        improves = (best["moderate_feature_count"] > sec_baseline["moderate_or_better_count"]
                    or best["strong_feature_count"] > sec_baseline["strong_count"])
    passes = (best["moderate_feature_count"] >= READY_MIN_MODERATE
              and best["strong_feature_count"] >= READY_MIN_STRONG
              and stable_families >= READY_MIN_STABLE_FAMILIES
              and best["max_distinct_ic_years"] >= FULL_MIN_YEARS
              and best["any_horizon_ready"] and improves)
    if passes:
        return REC_PASSES
    earnings_adds = (earnings_metrics["moderate_feature_count"] >= 1
                     or (earnings_metrics["max_abs_ic"] or 0.0) >= FAINT_SIGNAL_ABS_IC)
    if earnings_adds and improves:
        return REC_WEAK
    return REC_FAILS


def build_recommendation(recommendation, provider_selected, earnings_metrics, combined_metrics,
                         sec_baseline):
    em, cm = earnings_metrics, combined_metrics
    reasons = {
        REC_PASSES: (
            "With a configured earnings-estimates provider, the earnings-only and/or combined "
            "feature set clears the full research-model gate (>= %d moderate-or-better, >= %d "
            "strong, >= %d stable families, a ready horizon, and >= %d distinct dense IC years) and "
            "improves signal density over the SEC-only Phase 3-L baseline. A research-only "
            "walk-forward model on the cleared earnings + SEC features is permitted next - still no "
            "production candidate, no deployment, and no production edge claim."
            % (READY_MIN_MODERATE, READY_MIN_STRONG, READY_MIN_STABLE_FAMILIES, FULL_MIN_YEARS)),
        REC_WEAK: (
            "Earnings-surprise features add measurable signal over the SEC-only baseline "
            "(earnings-only: %d moderate-or-better / %d strong; combined: %d / %d) but the set does "
            "not clear the full research-model gate. Upgrade to a deeper estimates endpoint or add "
            "true estimate-revision history before any training. No model is trained here and no "
            "production edge is claimed."
            % (em["moderate_feature_count"], em["strong_feature_count"],
               cm["moderate_feature_count"], cm["strong_feature_count"])),
        REC_BLOCKED_KEY: (
            "No supported earnings-estimates provider key is present in the environment "
            "(checked %s). No data was fetched, faked, or modeled. Set one of those environment "
            "variables - Alpha Vantage (ALPHAVANTAGE_API_KEY) is the preferred first provider - and "
            "re-run. No data was purchased and no account was created."
            % ", ".join(SUPPORTED_ENV_VARS)),
        REC_BLOCKED_LIMIT: (
            "A provider key was present but the provider returned a rate-limit / entitlement / "
            "premium-required response before usable earnings history could be assembled. Decide on "
            "a provider entitlement (paid tier, trial, or alternate source) before continuing. No "
            "data was purchased."),
        REC_FAILS: (
            "With sufficient coverage and a leakage-free combined panel, earnings-surprise features "
            "add no useful cross-sectional IC or bucket-spread signal over the SEC-only baseline. "
            "Stop the earnings-surprise route and evaluate other rich data (options, sentiment, "
            "insider transactions, news). No edge claimed."),
        REC_INCONCLUSIVE: (
            "Earnings coverage, label coverage, leakage status, or the Phase 3-L confirmation was "
            "insufficient to decide. Repair earnings-estimate ticker coverage / history / feature "
            "alignment before deciding. Diagnostic only; no edge claimed."),
    }
    research_allowed = recommendation == REC_PASSES
    return {
        "recommendation": recommendation,
        "allowed_values": ALLOWED_RECOMMENDATIONS,
        "selected_provider": provider_selected,
        "create_production_model_candidate_now": False,
        "train_production_model_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "research_model_allowed_next": research_allowed,
        "add_richer_data_next": recommendation in (REC_WEAK, REC_FAILS, REC_BLOCKED_LIMIT),
        "configure_provider_next": recommendation in (REC_BLOCKED_KEY, REC_BLOCKED_LIMIT),
        "reason": reasons[recommendation],
    }


def build_recommended_next_phase(recommendation):
    title, purpose = NEXT_PHASE_TABLE[recommendation]
    return {"phase": "3-N", "title": title, "purpose": purpose}


def build_decision_table(confirmed, provider_selected, provider_limited, processed_ticker_count,
                         earnings_feature_ticker_count, combined_rows, leakage_failures,
                         label_coverage, label_cov_ok, earnings_metrics, combined_metrics,
                         stable_families, sec_baseline, recommendation):
    rows = []

    def add(item, value, passed, note):
        rows.append({"decision_item": item, "value": value,
                     "passed": "" if passed is None else bool(passed), "note": note})

    add("phase3l_confirmed", confirmed.get("all_confirmed"), confirmed.get("all_confirmed"),
        "Phase 3-L confirmed as %s, routed to 3-M" % PHASE3L_EXPECTED_RECOMMENDATION)
    add("provider_selected", provider_selected, provider_selected is not None,
        "an earnings-estimates provider key must be present (priority: %s)"
        % ", ".join(SUPPORTED_ENV_VARS))
    add("provider_not_rate_limited", not provider_limited, not provider_limited,
        "provider must not return a rate-limit / entitlement / premium error")
    add("processed_ticker_count", processed_ticker_count,
        processed_ticker_count >= PASS_MIN_PROCESSED_TICKERS,
        "PASS wants >= %d processed tickers" % PASS_MIN_PROCESSED_TICKERS)
    add("earnings_feature_ticker_count", earnings_feature_ticker_count,
        earnings_feature_ticker_count >= PASS_MIN_EARNINGS_FEATURE_TICKERS,
        "PASS wants >= %d tickers with earnings features" % PASS_MIN_EARNINGS_FEATURE_TICKERS)
    add("combined_panel_rows", combined_rows, combined_rows >= PASS_MIN_COMBINED_ROWS,
        "PASS wants >= %d combined rows" % PASS_MIN_COMBINED_ROWS)
    add("leakage_failure_count", leakage_failures, leakage_failures == 0, "must be 0")
    add("label_coverage_21d", label_coverage.get(21), label_coverage.get(21, 0.0) >= COV_GATE[21],
        "PASS wants >= %.2f (reused Phase 3-L labels)" % COV_GATE[21])
    add("label_coverage_63d", label_coverage.get(63), label_coverage.get(63, 0.0) >= COV_GATE[63],
        "PASS wants >= %.2f" % COV_GATE[63])
    add("label_coverage_126d", label_coverage.get(126),
        label_coverage.get(126, 0.0) >= COV_GATE[126], "PASS wants >= %.2f" % COV_GATE[126])
    add("earnings_only_moderate_or_better", earnings_metrics["moderate_feature_count"],
        earnings_metrics["moderate_feature_count"] >= READY_MIN_MODERATE,
        "earnings-only moderate-or-better features")
    add("earnings_only_strong", earnings_metrics["strong_feature_count"],
        earnings_metrics["strong_feature_count"] >= READY_MIN_STRONG, "earnings-only strong")
    add("combined_moderate_or_better", combined_metrics["moderate_feature_count"],
        combined_metrics["moderate_feature_count"] >= READY_MIN_MODERATE,
        "combined moderate-or-better features")
    add("combined_strong", combined_metrics["strong_feature_count"],
        combined_metrics["strong_feature_count"] >= READY_MIN_STRONG, "combined strong")
    add("stable_family_count", stable_families, stable_families >= READY_MIN_STABLE_FAMILIES,
        "PASS wants >= %d stable families" % READY_MIN_STABLE_FAMILIES)
    add("max_distinct_dense_ic_years",
        max(earnings_metrics["max_distinct_ic_years"], combined_metrics["max_distinct_ic_years"]),
        max(earnings_metrics["max_distinct_ic_years"],
            combined_metrics["max_distinct_ic_years"]) >= FULL_MIN_YEARS,
        "PASS wants >= %d distinct dense IC years" % FULL_MIN_YEARS)
    sec_mod = sec_baseline.get("moderate_or_better_count")
    add("sec_baseline_moderate_or_better", sec_mod, None,
        "Phase 3-L SEC-only baseline moderate-or-better feature count (for comparison)")
    add("no_model_training", True, True, "this phase trains no model")
    add("no_predictions_computed", True, True, "this phase computes no predictions or scores")
    add("no_portfolio_weights", True, True, "this phase computes no portfolio weights")
    add("labels_reused_not_recomputed", True, True, "Phase 3-L labels reused; no new target")
    add("recommendation", recommendation, recommendation in ALLOWED_RECOMMENDATIONS,
        "allowed values only")
    return rows


# --------------------------------------------------------------------------- #
# Interpretation + safety flags
# --------------------------------------------------------------------------- #
def build_interpretation(recommendation, provider_selected, network_used, vendor_called,
                         features_computed, price_join, labels_reused):
    return {
        "earnings_estimates_signal_gate_only": True,
        "follows_phase3l_weak_sec_only": True,
        "selected_provider": provider_selected,
        "model_features_computed": bool(features_computed),
        "price_join_performed": bool(price_join),
        "labels_computed": False,
        "labels_reused_from_phase3l": bool(labels_reused),
        "labels_for_validation_only": True,
        "ic_computed": bool(features_computed),
        "predictions_computed": False,
        "scores_computed": False,
        "portfolio_weights_computed": False,
        "model_trained": False,
        "research_model_trained": False,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "deployable_model_artifact_written": False,
        "research_model_allowed_next": recommendation == REC_PASSES,
        "network_used": bool(network_used),
        "vendor_api_called": bool(vendor_called),
        "paid_vendor_api_called": False,
        "user_configured_provider_key_used": provider_selected is not None,
        "data_purchased": False,
        "read_from_d_drive": False,
        "wrote_to_d_drive": False,
        "results_context_remains_survivorship_biased": True,
        "production_edge_claimed": False,
        "narrative": (
            "Phase 3-M is the research-only earnings-estimates / surprise data gate that follows "
            "the weak SEC-only Phase 3-L result. It confirmed Phase 3-L "
            "(WEAK_BUT_EXPAND_OR_ADD_REVISIONS, 128/128 tickers, leakage-free) and routed here, "
            "detected supported provider keys from the environment (values never read into any "
            "artifact), and - when a key was present - ingested historical EPS estimate / actual / "
            "surprise data cache-first under conservative rate limits, normalized point-in-time "
            "earnings events (availability = report date, never the fiscal quarter end), built "
            "trailing-only surprise features, joined them to the Phase 3-L panel reusing its "
            "validation-only labels, and measured cross-sectional rank ICs for earnings-only, "
            "SEC-only, and combined feature sets with the same temporal-breadth guard. It fitted NO "
            "model, computed NO predictions / scores / trading rankings / portfolio weights, created "
            "NO production model candidate, wrote NO deployable model artifact, touched no database, "
            "ran no migration, restarted no prediction service, enabled no serving flag, read "
            "nothing from the D: drive, called no paid vendor API, used no third-party "
            "market-data vendor package, purchased no data, placed no orders, and traded nothing. "
            "The universe is current-as-of, so every "
            "result remains survivorship-biased and claims no production edge."),
    }


def build_safety_flags(recommendation, network_used, vendor_called, provider_selected,
                       features_computed, price_join, labels_reused):
    return {
        "database_touched": False,
        "database_write_executed": False,
        "migration_executed": False,
        "deployment_executed": False,
        "model_v2_enabled": False,
        "production_edge_claimed": False,
        "no_trading": True,
        "no_orders": True,
        "no_automation": True,
        "research_model_trained": False,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "deployable_model_artifact_written": False,
        "d_drive_read": False,
        "d_drive_written": False,
        "network_used": bool(network_used),
        "vendor_api_called": bool(vendor_called),
        "paid_vendor_api_called": False,
        "user_configured_provider_key_used": provider_selected is not None,
        "data_purchase_made": False,
        "model_features_computed": bool(features_computed),
        "price_join_performed": bool(price_join),
        "labels_computed": False,
        "labels_reused_from_phase3l": bool(labels_reused),
        "labels_for_validation_only": True,
        "model_trained": False,
        "predictions_computed": False,
        "portfolio_weights_computed": False,
        "research_model_allowed_next": recommendation == REC_PASSES,
    }


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #
def _write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows or []:
            out = {}
            for c in columns:
                val = r.get(c, "")
                out[c] = "" if val is None else val
            w.writerow(out)


def _dump_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def _strip_private(rows):
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]


def _write_all_empty_artifacts():
    """Header-only artifacts for a BLOCKED run so every listed output exists."""
    _write_csv(EARNINGS_EVENTS_CSV, EARNINGS_EVENT_COLUMNS, [])
    _write_csv(EARNINGS_FEATURES_CSV, EARNINGS_FEATURE_COLUMNS, [])
    _write_csv(COMBINED_PANEL_CSV, COMBINED_PANEL_COLUMNS, [])
    _write_csv(EARNINGS_FEATURE_COVERAGE_CSV, EARNINGS_FEATURE_COVERAGE_COLUMNS, [])
    _write_csv(EARNINGS_LABEL_SUMMARY_CSV, LABEL_SUMMARY_COLUMNS, [])
    _write_csv(EARNINGS_FEATURE_IC_CSV, FEATURE_IC_COLUMNS, [])
    _write_csv(EARNINGS_FEATURE_FAMILY_IC_CSV, FEATURE_FAMILY_IC_COLUMNS, [])
    _write_csv(EARNINGS_HORIZON_READINESS_CSV, HORIZON_READINESS_COLUMNS, [])
    _write_csv(EARNINGS_YEARLY_IC_CSV, YEARLY_IC_COLUMNS, [])
    _write_csv(EARNINGS_SECTOR_SANITY_CSV, SECTOR_SANITY_COLUMNS, [])


def build_provider_access_summary(provider_selected, detected_keys, client, provider_limited,
                                  recommendation):
    """provider_access_report.json content - booleans only, NEVER any key value."""
    return {
        "detected_keys": dict(detected_keys),            # env-var-name -> bool (present/absent)
        "supported_env_vars": list(SUPPORTED_ENV_VARS),
        "provider_priority_order": [p for p, _e, _h in PROVIDER_PRIORITY],
        "selected_provider": provider_selected,
        "endpoint_used": ENDPOINT_TEMPLATES.get(provider_selected) if provider_selected else None,
        "entitlement_status": ("rate_limited_or_premium" if provider_limited
                               else ("ok" if provider_selected else "no_key")),
        "network_used": bool(client.network_requests) if client else False,
        "request_count": client.request_count if client else 0,
        "cache_hit_count": client.cache_hits if client else 0,
        "rate_limit_or_premium_errors": client.rate_limit_or_premium_errors if client else 0,
        "key_values_never_stored": True,
        "recommendation": recommendation,
    }


_INPUTS_READ = {
    "phase3l_result_json": "research/output/phase3l_sec_universe_signal_gate.json",
    "phase3l_aligned_panel_csv":
        "research/output/phase3l_sec_universe_signal_gate/aligned_feature_price_panel_universe.csv",
    "phase3l_company_identity_csv":
        "research/output/phase3l_sec_universe_signal_gate/company_identity_universe.csv",
    "phase3l_label_summary_csv":
        "research/output/phase3l_sec_universe_signal_gate/label_summary_by_horizon.csv",
    "phase3l_feature_ic_summary_csv":
        "research/output/phase3l_sec_universe_signal_gate/feature_ic_summary.csv",
    "phase3l_horizon_readiness_csv":
        "research/output/phase3l_sec_universe_signal_gate/horizon_readiness_summary.csv",
    "sector_map_csv": "research/input/phase2k_p_sector_map_current.csv",
}
_OUTPUTS_WRITTEN = {
    "result_json": "research/output/phase3m_earnings_estimates_signal_gate.json",
    "provider_access_report_json":
        "research/output/phase3m_earnings_estimates_signal_gate/provider_access_report.json",
    "earnings_events_universe_csv":
        "research/output/phase3m_earnings_estimates_signal_gate/earnings_events_universe.csv",
    "earnings_features_universe_csv":
        "research/output/phase3m_earnings_estimates_signal_gate/earnings_features_universe.csv",
    "combined_fundamental_earnings_panel_csv":
        "research/output/phase3m_earnings_estimates_signal_gate/"
        "combined_fundamental_earnings_panel.csv",
    "earnings_feature_coverage_by_ticker_csv":
        "research/output/phase3m_earnings_estimates_signal_gate/"
        "earnings_feature_coverage_by_ticker.csv",
    "earnings_label_summary_by_horizon_csv":
        "research/output/phase3m_earnings_estimates_signal_gate/"
        "earnings_label_summary_by_horizon.csv",
    "earnings_feature_ic_summary_csv":
        "research/output/phase3m_earnings_estimates_signal_gate/earnings_feature_ic_summary.csv",
    "earnings_feature_family_ic_summary_csv":
        "research/output/phase3m_earnings_estimates_signal_gate/"
        "earnings_feature_family_ic_summary.csv",
    "earnings_horizon_readiness_summary_csv":
        "research/output/phase3m_earnings_estimates_signal_gate/"
        "earnings_horizon_readiness_summary.csv",
    "earnings_yearly_ic_summary_csv":
        "research/output/phase3m_earnings_estimates_signal_gate/earnings_yearly_ic_summary.csv",
    "earnings_sector_sanity_summary_csv":
        "research/output/phase3m_earnings_estimates_signal_gate/"
        "earnings_sector_sanity_summary.csv",
    "decision_table_csv":
        "research/output/phase3m_earnings_estimates_signal_gate/decision_table.csv",
    "raw_cache_dir": "research/output/phase3m_earnings_estimates_signal_gate/raw/",
}


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run(result_json_path=RESULT_JSON, m_dir=_M_DIR, raw_dir=_RAW_DIR,
        panel_path=PHASE3L_PANEL_CSV, env=None, max_tickers=None, verbose=True):
    """Run the full Phase 3-M gate. Degrades to a BLOCKED result when no provider key is present."""
    os.makedirs(m_dir, exist_ok=True)
    phase3l = _load_json(PHASE3L_JSON)
    confirmed = confirm_phase3l(phase3l)
    sector_map = load_sector_map(SECTOR_MAP_CSV)
    sector_of = {t: info.get("sector", "") for t, info in sector_map.items()}
    universe = load_phase3l_universe(PHASE3L_IDENTITY_CSV, sector_map)
    if max_tickers is not None:
        universe = universe[:max_tickers]
    sec_baseline = load_phase3l_sec_baseline(PHASE3L_FEATURE_IC_CSV)

    provider_selected, selected_env, detected_keys, host = detect_providers(env)

    # ---- BLOCKED: no provider key ----
    if provider_selected is None:
        return _finish_blocked(
            result_json_path, REC_BLOCKED_KEY, confirmed, None, detected_keys, None, False,
            verbose, sec_baseline)

    # ---- Provider present: ingest earnings (cache-first) ----
    client = ProviderClient(provider_selected, (env or os.environ).get(selected_env, ""), host,
                            raw_dir)
    events_by_ticker = {}
    processed, failed = [], []
    provider_limited = False
    for n, t in enumerate(universe):
        if verbose and n and n % 10 == 0:
            print("  ... processed %d/%d tickers (reqs=%d, cache=%d)"
                  % (n, len(universe), client.request_count, client.cache_hits))
        try:
            payload, _src = client.get_earnings(t)
        except _ProviderLimited as e:
            client.errors.append("%s: %s" % (t, e))
            provider_limited = True
            break
        except (urllib.error.URLError, OSError, ValueError, RuntimeError,
                json.JSONDecodeError) as e:
            client.errors.append("%s fetch failed: %s" % (t, e))
            failed.append({"ticker": t, "reason": "provider fetch/cache failed"})
            continue
        events = normalize_events(t, provider_selected, payload)
        usable = [e for e in events if e.get("point_in_time_usable")]
        if usable:
            events_by_ticker[t] = events
            processed.append(t)
        else:
            failed.append({"ticker": t, "reason": "no usable earnings events"})

    network_used = client.network_requests > 0
    vendor_called = client.network_requests > 0

    if provider_limited:
        return _finish_blocked(
            result_json_path, REC_BLOCKED_LIMIT, confirmed, provider_selected, detected_keys,
            client, True, verbose, sec_baseline)

    # ---- normalize events + build trailing features ----
    all_events = []
    for t in sorted(events_by_ticker):
        all_events.extend(events_by_ticker[t])
    feature_rows = build_earnings_features(events_by_ticker)
    feature_coverage = build_earnings_feature_coverage(feature_rows, sector_of)
    earnings_feature_ticker_count = len({r["ticker"] for r in feature_rows})

    # ---- combine with the Phase 3-L panel (reuse labels) ----
    active_by_ticker = _active_feature_snapshots(feature_rows)
    combined_rows, sec_present = combine_with_panel(panel_path, active_by_ticker, want_sec=True)
    leakage_check_rows, leakage_failures = evaluate_leakage_summary(combined_rows)

    # ---- label coverage (reused labels) ----
    total_rows = len(combined_rows)
    label_coverage = {}
    for h in HORIZONS:
        col = EXCESS_LABEL_TMPL % h
        nn = sum(1 for r in combined_rows if _to_float(r.get(col)) is not None)
        label_coverage[h] = _round(nn / total_rows, 4) if total_rows else 0.0
    label_cov_ok = all(label_coverage.get(h, 0.0) >= COV_GATE[h] for h in HORIZONS)
    label_summary_rows = []
    for h in HORIZONS:
        col = EXCESS_LABEL_TMPL % h
        rkcol = RANK_LABEL_TMPL % h
        ex = [_to_float(r.get(col)) for r in combined_rows if _to_float(r.get(col)) is not None]
        rk = sum(1 for r in combined_rows if _to_float(r.get(rkcol)) is not None)
        sd = [r["scoring_date"] for r in combined_rows if _to_float(r.get(col)) is not None]
        label_summary_rows.append({
            "horizon": "%dd" % h, "combined_rows": total_rows, "non_null_excess_return": len(ex),
            "non_null_rank": rk, "label_coverage": label_coverage[h],
            "mean_forward_excess_return_vs_spy": _round(_mean(ex), 6) if ex else None,
            "earliest_scoring_date": min(sd) if sd else "",
            "latest_scoring_date": max(sd) if sd else "",
        })

    # ---- IC gate: earnings-only, sec-only, combined ----
    groups = group_by_date(combined_rows)
    nn_frac = {}
    specs = []
    for fname in EARNINGS_FEATURES:
        specs.append((fname, fname, EARNINGS_FAMILY_OF[fname], "earnings_only"))
    sec_keys = []
    if sec_present:
        for c in SEC_BASELINE_FEATURES:
            key = "_sec_%s" % c
            if any(key in r for r in combined_rows[:1]):
                sec_keys.append((key, c))
        for key, c in sec_keys:
            specs.append((key, c, FAM_SEC, "sec_only"))
        # combined group = earnings features + sec features measured together (same panel)
        for fname in EARNINGS_FEATURES:
            specs.append((fname, fname, EARNINGS_FAMILY_OF[fname], "combined"))
        for key, c in sec_keys:
            specs.append((key, c, FAM_SEC, "combined"))
    for feature_key, _name, _fam, _grp in specs:
        if feature_key not in nn_frac:
            nn = sum(1 for r in combined_rows if _to_float(r.get(feature_key)) is not None)
            nn_frac[feature_key] = (nn / total_rows) if total_rows else 0.0

    feature_ic_rows, excess_ic_series = compute_feature_ic(groups, specs, nn_frac)
    family_rows = compute_family_summary(feature_ic_rows)
    horizon_rows = []
    for grp in ("earnings_only", "sec_only", "combined"):
        if any(r["feature_group"] == grp for r in feature_ic_rows):
            horizon_rows.extend(compute_horizon_readiness(feature_ic_rows, groups, label_coverage,
                                                          grp))
    best_earnings = pick_best_features(feature_ic_rows, "earnings_only", 5)
    yearly_rows, yearly_summary = compute_yearly_diagnostics(
        best_earnings, "earnings_only", excess_ic_series, feature_ic_rows)
    sector_rows = compute_sector_sanity(combined_rows)

    earnings_metrics = _group_metrics(feature_ic_rows, horizon_rows, "earnings_only")
    combined_metrics = _group_metrics(feature_ic_rows, horizon_rows, "combined")
    stable_families = sum(1 for f in family_rows if f["stable_signal"]
                          and f["feature_group"] in ("earnings_only", "combined"))

    coverage_ok = (len(processed) >= PASS_MIN_PROCESSED_TICKERS
                   and earnings_feature_ticker_count >= PASS_MIN_EARNINGS_FEATURE_TICKERS
                   and total_rows >= PASS_MIN_COMBINED_ROWS)
    recommendation = decide(confirmed, provider_selected, False, coverage_ok, leakage_failures,
                            label_cov_ok, family_rows, earnings_metrics, combined_metrics,
                            sec_baseline)

    decision_table = build_decision_table(
        confirmed, provider_selected, False, len(processed), earnings_feature_ticker_count,
        total_rows, leakage_failures, label_coverage, label_cov_ok, earnings_metrics,
        combined_metrics, stable_families, sec_baseline, recommendation)

    # ---- compact combined panel (earnings features + labels + keys; SEC features stay in 3-L) ----
    _write_csv(EARNINGS_EVENTS_CSV, EARNINGS_EVENT_COLUMNS, all_events)
    _write_csv(EARNINGS_FEATURES_CSV, EARNINGS_FEATURE_COLUMNS, feature_rows)
    _write_csv(COMBINED_PANEL_CSV, COMBINED_PANEL_COLUMNS, combined_rows)
    _write_csv(EARNINGS_FEATURE_COVERAGE_CSV, EARNINGS_FEATURE_COVERAGE_COLUMNS, feature_coverage)
    _write_csv(EARNINGS_LABEL_SUMMARY_CSV, LABEL_SUMMARY_COLUMNS, label_summary_rows)
    _write_csv(EARNINGS_FEATURE_IC_CSV, FEATURE_IC_COLUMNS, feature_ic_rows)
    _write_csv(EARNINGS_FEATURE_FAMILY_IC_CSV, FEATURE_FAMILY_IC_COLUMNS, family_rows)
    _write_csv(EARNINGS_HORIZON_READINESS_CSV, HORIZON_READINESS_COLUMNS, horizon_rows)
    _write_csv(EARNINGS_YEARLY_IC_CSV, YEARLY_IC_COLUMNS, yearly_rows)
    _write_csv(EARNINGS_SECTOR_SANITY_CSV, SECTOR_SANITY_COLUMNS, sector_rows)
    _write_csv(DECISION_TABLE_CSV, DECISION_TABLE_COLUMNS, decision_table)

    provider_summary = build_provider_access_summary(
        provider_selected, detected_keys, client, False, recommendation)
    _dump_json(PROVIDER_ACCESS_JSON, provider_summary)

    result = _assemble_result(
        result_json_path, recommendation, confirmed, phase3l, provider_summary,
        provider_selected, network_used, vendor_called, sec_baseline,
        events_summary={
            "provider": provider_selected, "event_rows": len(all_events),
            "processed_ticker_count": len(processed), "failed_ticker_count": len(failed),
            "failed_tickers": failed,
            "point_in_time_usable_event_rows": sum(1 for e in all_events
                                                   if e.get("point_in_time_usable")),
            "availability_is_reported_date": True,
        },
        feature_summary={
            "earnings_feature_count": len(EARNINGS_FEATURES),
            "earnings_features": list(EARNINGS_FEATURES),
            "earnings_feature_family_of": dict(EARNINGS_FAMILY_OF),
            "earnings_feature_ticker_count": earnings_feature_ticker_count,
            "estimate_revision_proxy_available": False,
            "estimate_revision_proxy_note":
                "the configured free endpoint exposes no prior-estimate time series; "
                "estimate_revision_proxy is left blank and documented",
            "feature_rows": len(feature_rows),
        },
        combined_summary={
            "combined_panel_rows": total_rows,
            "combined_panel_is_compact": True,
            "compact_columns": list(COMBINED_PANEL_COLUMNS),
            "compact_note": "earnings features + reused labels + join keys are written to disk; the "
                            "23 SEC features are NOT duplicated (they remain in the Phase 3-L "
                            "panel) so the combined CSV stays well under the 50 MB Git limit; SEC "
                            "features are joined in-memory for the combined IC",
            "sec_features_present_in_memory": sec_present,
            "earnings_staleness_cap_days": EARNINGS_STALENESS_CAP_DAYS,
            "rows_with_active_earnings_event":
                sum(1 for r in combined_rows if r.get("active_earnings_reported_date")),
        },
        leakage_summary={
            "leakage_failure_count": leakage_failures,
            "checks_evaluated": [n for n, _ in _LEAKAGE_CHECK_DEFS],
            "all_checks_passed": leakage_failures == 0, "by_check": leakage_check_rows,
        },
        label_coverage=label_coverage, label_summary_rows=label_summary_rows,
        feature_ic_rows=feature_ic_rows, family_rows=family_rows, horizon_rows=horizon_rows,
        yearly_summary=yearly_summary, yearly_rows=yearly_rows, sector_rows=sector_rows,
        earnings_metrics=earnings_metrics, combined_metrics=combined_metrics,
        stable_families=stable_families, decision_table=decision_table,
        features_computed=True, price_join=True, labels_reused=True, best_earnings=best_earnings)

    _dump_json(result_json_path, result)
    return result


def _finish_blocked(result_json_path, recommendation, confirmed, provider_selected, detected_keys,
                    client, provider_limited, verbose, sec_baseline):
    """Write provider report, header-only artifacts, and a BLOCKED result JSON; no modeling."""
    _write_all_empty_artifacts()
    provider_summary = build_provider_access_summary(
        provider_selected, detected_keys, client, provider_limited, recommendation)
    _dump_json(PROVIDER_ACCESS_JSON, provider_summary)
    empty_metrics = {"moderate_feature_count": 0, "moderate_features": [], "strong_feature_count":
                     0, "strong_features": [], "any_horizon_ready": False,
                     "max_distinct_ic_years": 0, "max_abs_ic": None}
    decision_table = build_decision_table(
        confirmed, provider_selected, provider_limited, 0, 0, 0, 0, {}, False, empty_metrics,
        empty_metrics, 0, sec_baseline, recommendation)
    _write_csv(DECISION_TABLE_CSV, DECISION_TABLE_COLUMNS, decision_table)
    network_used = bool(client and client.network_requests)
    vendor_called = bool(client and client.network_requests)
    result = _assemble_result(
        result_json_path, recommendation, confirmed, _load_json(PHASE3L_JSON), provider_summary,
        provider_selected, network_used, vendor_called, sec_baseline,
        events_summary={"provider": provider_selected, "event_rows": 0,
                        "processed_ticker_count": 0, "failed_ticker_count": 0, "failed_tickers": [],
                        "point_in_time_usable_event_rows": 0,
                        "availability_is_reported_date": True,
                        "blocked_reason": ("no supported provider API key in environment"
                                           if recommendation == REC_BLOCKED_KEY
                                           else "provider rate-limit / entitlement error")},
        feature_summary={"earnings_feature_count": len(EARNINGS_FEATURES),
                         "earnings_features": list(EARNINGS_FEATURES),
                         "earnings_feature_family_of": dict(EARNINGS_FAMILY_OF),
                         "earnings_feature_ticker_count": 0,
                         "estimate_revision_proxy_available": False,
                         "estimate_revision_proxy_note": "not evaluated (blocked)",
                         "feature_rows": 0},
        combined_summary={"combined_panel_rows": 0, "combined_panel_is_compact": True,
                          "compact_columns": list(COMBINED_PANEL_COLUMNS),
                          "compact_note": "not built (blocked)",
                          "sec_features_present_in_memory": False,
                          "earnings_staleness_cap_days": EARNINGS_STALENESS_CAP_DAYS,
                          "rows_with_active_earnings_event": 0},
        leakage_summary={"leakage_failure_count": 0,
                         "checks_evaluated": [n for n, _ in _LEAKAGE_CHECK_DEFS],
                         "all_checks_passed": True, "by_check": []},
        label_coverage={h: 0.0 for h in HORIZONS}, label_summary_rows=[],
        feature_ic_rows=[], family_rows=[], horizon_rows=[], yearly_summary=[], yearly_rows=[],
        sector_rows=[], earnings_metrics=empty_metrics, combined_metrics=empty_metrics,
        stable_families=0, decision_table=decision_table,
        features_computed=False, price_join=False, labels_reused=False, best_earnings=[])
    _dump_json(result_json_path, result)
    if verbose:
        print("Phase 3-M BLOCKED: %s" % recommendation)
    return result


def _assemble_result(result_json_path, recommendation, confirmed, phase3l, provider_summary,
                     provider_selected, network_used, vendor_called, sec_baseline,
                     events_summary, feature_summary, combined_summary, leakage_summary,
                     label_coverage, label_summary_rows, feature_ic_rows, family_rows, horizon_rows,
                     yearly_summary, yearly_rows, sector_rows, earnings_metrics, combined_metrics,
                     stable_families, decision_table, features_computed, price_join, labels_reused,
                     best_earnings):
    rec3l = (phase3l or {}).get("recommendation", {}).get("recommendation")
    result = {
        "phase": PHASE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs_read": dict(_INPUTS_READ),
        "outputs_written": dict(_OUTPUTS_WRITTEN),
        "phase3l_summary": {
            "phase3l_confirmed": confirmed,
            "phase3l_recommendation": rec3l,
            "phase3l_processed_ticker_count":
                (phase3l or {}).get("universe_summary", {}).get("processed_ticker_count"),
            "phase3l_aligned_ticker_count":
                (phase3l or {}).get("alignment_summary", {}).get("aligned_ticker_count"),
            "sec_only_baseline": sec_baseline,
        },
        "provider_access_summary": provider_summary,
        "earnings_events_summary": events_summary,
        "earnings_feature_summary": feature_summary,
        "combined_panel_summary": combined_summary,
        "leakage_check_summary": leakage_summary,
        "ic_methodology": {
            "ic_type": "cross-sectional daily Spearman rank information coefficient",
            "labels_reused_from_phase3l": ["forward_excess_return_vs_spy_{h}d",
                                           "forward_return_rank_by_date_{h}d"],
            "min_cross_section_per_date_partial": IC_MIN_CROSS_SECTION,
            "min_cross_section_per_date_full": DENSE_MIN_CROSS_SECTION,
            "feature_groups": ["earnings_only", "sec_only", "combined"],
            "moderate_or_better_gate": {"abs_mean_rank_ic_at_least": MODERATE_ABS_IC,
                                        "date_count_at_least": MODERATE_MIN_DATES,
                                        "ic_hit_rate_at_least": MODERATE_MIN_HIT,
                                        "non_null_feature_fraction_at_least": MODERATE_MIN_NONNULL},
            "strong_gate": {"abs_mean_rank_ic_at_least": STRONG_ABS_IC,
                            "date_count_at_least": STRONG_MIN_DATES,
                            "ic_hit_rate_at_least": STRONG_MIN_HIT,
                            "non_null_feature_fraction_at_least": STRONG_MIN_NONNULL,
                            "bucket_spread_sign_matches_ic": True,
                            "bucket_spread_hit_rate_at_least": STRONG_MIN_SPREAD_HIT},
            "temporal_breadth_gate": {"dense_cross_section_min_names": DENSE_MIN_CROSS_SECTION,
                                      "min_qualifying_dates_per_year": MIN_QUALIFYING_DATES_PER_YEAR,
                                      "full_min_distinct_years": FULL_MIN_YEARS,
                                      "partial_min_distinct_years": PARTIAL_MIN_YEARS},
            "no_model_fit": True,
            "survivorship_caveat": "current-as-of universe; daily overlapping windows; ICs are "
                                   "diagnostic only and claim no edge.",
        },
        "signal_gate_summary": {
            "label_coverage_by_horizon": {("%dd" % h): label_coverage.get(h) for h in HORIZONS},
            "labels_for_validation_only": True,
            "labels_reused_from_phase3l": bool(labels_reused),
            "by_horizon_label_summary": label_summary_rows,
            "earnings_only_metrics": earnings_metrics,
            "combined_metrics": combined_metrics,
            "stable_family_count": stable_families,
            "best_earnings_features": best_earnings,
            "feature_ic_summary": _strip_private(feature_ic_rows),
            "feature_family_summary": _strip_private(family_rows),
            "horizon_readiness_summary": _strip_private(horizon_rows),
            "yearly_stability_summary": {"by_feature_horizon": yearly_summary, "by_year": yearly_rows},
            "sector_sanity_summary": sector_rows,
            "decision_table": decision_table,
        },
        "comparison_to_phase3l_sec_only": {
            "sec_only_baseline": sec_baseline,
            "earnings_only_moderate_or_better_count": earnings_metrics["moderate_feature_count"],
            "earnings_only_strong_count": earnings_metrics["strong_feature_count"],
            "combined_moderate_or_better_count": combined_metrics["moderate_feature_count"],
            "combined_strong_count": combined_metrics["strong_feature_count"],
            "earnings_features_improve_signal_density": (
                sec_baseline.get("available") and (
                    max(earnings_metrics["moderate_feature_count"],
                        combined_metrics["moderate_feature_count"])
                    > (sec_baseline.get("moderate_or_better_count") or 0)
                    or max(earnings_metrics["strong_feature_count"],
                           combined_metrics["strong_feature_count"])
                    > (sec_baseline.get("strong_count") or 0))),
            "note": "comparison is diagnostic; no model is trained and no edge is claimed",
        },
        "source_limitations": list(_SOURCE_LIMITATIONS),
        "recommendation": build_recommendation(recommendation, provider_selected, earnings_metrics,
                                               combined_metrics, sec_baseline),
        "interpretation": build_interpretation(recommendation, provider_selected, network_used,
                                              vendor_called, features_computed, price_join,
                                              labels_reused),
        "recommended_next_phase": build_recommended_next_phase(recommendation),
    }
    result.update(build_safety_flags(recommendation, network_used, vendor_called, provider_selected,
                                     features_computed, price_join, labels_reused))
    return result


def main():
    result = run()
    rec = result["recommendation"]
    nxt = result["recommended_next_phase"]
    pas = result["provider_access_summary"]
    es = result["earnings_events_summary"]
    cs = result["combined_panel_summary"]
    print("Phase %s - Earnings Estimates / Surprise Data Gate + Immediate Signal Test"
          % result["phase"])
    print("  phase3l confirmed        : %s"
          % result["phase3l_summary"]["phase3l_confirmed"].get("all_confirmed"))
    print("  detected provider keys   : %s" % pas["detected_keys"])
    print("  selected provider        : %s" % pas["selected_provider"])
    print("  network used / vendor    : %s / %s" % (result["network_used"],
                                                     result["vendor_api_called"]))
    print("  processed tickers        : %s" % es["processed_ticker_count"])
    print("  earnings feature tickers : %s"
          % result["earnings_feature_summary"]["earnings_feature_ticker_count"])
    print("  combined panel rows      : %s" % cs["combined_panel_rows"])
    print("  leakage failures         : %s" % result["leakage_check_summary"]["leakage_failure_count"])
    print("  earnings-only mod/strong : %s / %s"
          % (result["signal_gate_summary"]["earnings_only_metrics"]["moderate_feature_count"],
             result["signal_gate_summary"]["earnings_only_metrics"]["strong_feature_count"]))
    print("  combined mod/strong      : %s / %s"
          % (result["signal_gate_summary"]["combined_metrics"]["moderate_feature_count"],
             result["signal_gate_summary"]["combined_metrics"]["strong_feature_count"]))
    print("  model trained            : %s" % result["model_trained"])
    print("  recommendation           : %s" % rec["recommendation"])
    print("  research model next      : %s" % rec["research_model_allowed_next"])
    print("  recommended next phase   : %s - %s" % (nxt["phase"], nxt["title"]))
    return result


if __name__ == "__main__":
    main()
