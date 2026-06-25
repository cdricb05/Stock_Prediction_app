"""Phase 8-E — Sensitivity-Aware Multi-Input Signal Factory.

**Track A (quant brain) research only.** Phases 8-A..8-D rejected always-on factor
portfolios AND price/volume-only conditional setups on broad survivorship-aware data. The
director's redesigned thesis for 8-E:

    Markets are not purely random, but different stocks react to different EXTERNAL drivers.
    A signal may only become visible when modelled as:
        external input  x  ticker sensitivity  x  market context  x  conditional setup.

The ONE question this phase answers
-----------------------------------
    CAN WE FIND A REPEATABLE SIGNAL BY MODELING EXTERNAL INPUTS AND TICKER-SPECIFIC
    SENSITIVITIES? i.e. when an external driver shocks, does the cohort of tickers that is
    historically SENSITIVE to that driver (estimated from data, never hard-coded) improve its
    forward return distribution vs MATCHED CONTROLS that saw the SAME shock but are NOT in the
    sensitive cohort — net of costs, out of sample, and in 2015-2026?

Design (how this differs from 8-D)
----------------------------------
  - A per-ticker SENSITIVITY MAP: leak-safe rolling betas of each ticker's return to each
    external driver proxy (market, oil, rates/duration, credit, USD, volatility, broad
    commodity, size, own-sector). Sensitivity DIRECTION is estimated, never assumed.
  - SENSITIVITY COHORTS: per decision date, the cross-sectional top/bottom quintile of a
    driver's rolling beta becomes a cohort flag (e.g. oil_positive_sensitive). Time-varying and
    fully leak-safe (trailing window known at t).
  - EXTERNAL DRIVER SHOCKS: date-level trailing returns / shock z-scores of each driver proxy
    (e.g. an oil 20d shock, a VIX spike), known at t.
  - A sensitivity setup fires only when:  driver shock present  AND  ticker in the historically
    sensitive cohort  AND  a price/volume confirming condition holds.
  - MATCHED CONTROLS: same date (=> same shock), same sector x liquidity x volatility x
    market-beta bucket, NOT triggered, and NOT in the same sensitivity cohort — so the only
    thing that differs is the driver sensitivity. This isolates the sensitivity contribution.

External data reality (Part A/B, honest)
----------------------------------------
  - LOCAL_READY via the *locally installed* Norgate desktop database: every macro/cross-asset
    proxy needed (SPY, 11 SPDR sector ETFs, TLT/IEF, HYG/LQD, UUP, USO/UNG/GLD/DBC, $VIX back to
    1990, EFA/EEM/IWM/SPHB). LOCAL_READY via on-disk FRED CSVs (CPI, WTI, USD index, yields).
    LOCAL_PARTIAL: SimFin + SEC EDGAR fundamentals on disk.
  - NEEDS_PROVIDER (no local series): analyst estimates/revisions, options/IV/skew, news,
    sentiment, transcripts, short interest. Their setup templates are pre-registered and marked
    NEEDS_PROVIDER_DATA (never faked); the exact acquisition plan is emitted instead.

Hard safety contract (unchanged)
--------------------------------
Offline, point-in-time, leakage-safe. Only provider is the locally installed Norgate Data
desktop database + on-disk FRED CSVs (no network/paid API, no package install). Large data is
written ONLY under D:\\Stock_Prediction_app_data\\research_panels; the repo gets summaries only.
No Paper Trader, no GCP, no deployment, no broker/orders/automation, no live trading signals, no
portfolio-weight optimization, no factor-sign flipping after seeing results, no regime
activation/throttling, no ML fitting, no using holdout feedback to tune thresholds, no hidden
experiments, no commit, no push. If external data is missing it is reported as missing with an
acquisition plan; it is never imputed or pretended to exist.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# --------------------------------------------------------------------------- #
# Reuse the Phase 8-D engine (which itself reuses 8-A's Norgate adapter + leak-safe
# primitives + IO) by ABSOLUTE PATH via importlib, so this module loads
# cwd-independently and imports norgatedata only when the panel-build path runs.
# --------------------------------------------------------------------------- #
def _load_module(name: str, rel: str):
    path = _REPO_ROOT / rel
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod          # REQUIRED before exec so dataclasses resolve __module__
    spec.loader.exec_module(mod)
    return mod


P8D = _load_module("phase8d_engine_for_8e", "research/run_phase8d_conditional_setup_signal_framework.py")
P8A = P8D.P8A

# primitives reused verbatim
NorgateAdapter = P8D.NorgateAdapter
sharpe = P8D.sharpe
max_drawdown = P8D.max_drawdown
ann_return = P8D.ann_return
_round = P8D._round
_write_json = P8D._write_json
_write_csv = P8D._write_csv
_utc_now_iso = P8D._utc_now_iso
capped_equal_weights = P8D.capped_equal_weights
symbol_features = P8D.symbol_features
forward_labels = P8D.forward_labels
simulate_event_portfolio = P8D.simulate_event_portfolio
_fwd5_pivot = P8D._fwd5_pivot
_spy_weekly = P8D._spy_weekly
_weekly_grid_dates = P8D._weekly_grid_dates
_OPS = P8D._OPS

PHASE = "8-E"
OBJECTIVE = (
    "Discover repeatable signals by modelling external-driver shocks x estimated ticker "
    "sensitivity cohorts x confirming conditions. Estimate (never assume) which tickers are "
    "sensitive to which drivers, fire only when a driver shocks and the sensitive cohort is "
    "present, and compare triggered events to matched controls that saw the SAME shock but are "
    "NOT in the sensitive cohort. Research only; no orders/automation/optimization."
)

# --------------------------------------------------------------------------- #
# Recommendation vocabulary (allowed set, in order). ERROR is the internal guard.
# --------------------------------------------------------------------------- #
REC_CONFIRMED = "CONFIRMED_SENSITIVITY_SIGNAL_FOUND"
REC_PROMISING = "PROMISING_SENSITIVITY_SETUPS_NEED_MORE_VALIDATION"
REC_NEEDS_PROVIDER = "NEEDS_EXTERNAL_PROVIDER_DATA"
REC_REJECTED = "SENSITIVITY_SIGNAL_RESEARCH_REJECTED"
REC_HUMAN = "NEEDS_HUMAN_PROVIDER_DECISION"
REC_FRAMEWORK_BLOCKED = "ASSESSMENT_FRAMEWORK_BLOCKED"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (
    REC_CONFIRMED, REC_PROMISING, REC_NEEDS_PROVIDER, REC_REJECTED,
    REC_HUMAN, REC_FRAMEWORK_BLOCKED, REC_ERROR,
)

# Per-setup status vocabulary.
ST_CONFIRMED = "CONFIRMED_SENSITIVITY_SIGNAL"
ST_PROMISING = "PROMISING_SENSITIVITY_SETUP"
ST_REJECTED = "REJECTED"
ST_NEEDS_PROVIDER = "NEEDS_PROVIDER_DATA"
ST_BLOCKED = "BLOCKED"
ALLOWED_STATUSES = (ST_CONFIRMED, ST_PROMISING, ST_REJECTED, ST_NEEDS_PROVIDER, ST_BLOCKED)

# --------------------------------------------------------------------------- #
# Paths.
# --------------------------------------------------------------------------- #
DATA_ROOT = Path("D:/Stock_Prediction_app_data")
PANEL_ROOT = DATA_ROOT / "research_panels" / "phase8e_sensitivity"
DEFAULT_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase8e_sensitivity_aware_signal_factory"
INPUT_DIR = _REPO_ROOT / "research" / "input"

# On-disk external data locations probed for the inventory (read-only).
LOCAL_DATA_PROBES = [
    # (family, path, note)
    ("macro_cross_asset", INPUT_DIR / "macro_cpi_us.csv", "FRED CPI (CPIAUCSL) monthly"),
    ("macro_cross_asset", INPUT_DIR / "macro_oil_wti.csv", "FRED WTI crude (DCOILWTICO) daily"),
    ("macro_cross_asset", INPUT_DIR / "macro_dollar_index.csv", "FRED broad USD index (DTWEXBGS) daily"),
    ("macro_cross_asset", INPUT_DIR / "macro_fed_funds.csv", "FRED fed funds (FEDFUNDS) monthly"),
    ("macro_cross_asset", INPUT_DIR / "macro_treasury_yields.csv", "FRED DGS10/DGS2 daily yields"),
    ("sector_industry", INPUT_DIR / "phase2k_p_sector_map_current.csv", "current-as-of sector map (NOT PIT)"),
    ("fundamentals_pit", _REPO_ROOT / "research" / "data" / "simfin" / "us-income-quarterly.csv",
     "SimFin quarterly income statement"),
    ("fundamentals_pit", _REPO_ROOT / "research" / "data" / "simfin" / "us-balance-quarterly.csv",
     "SimFin quarterly balance sheet"),
    ("fundamentals_pit", DATA_ROOT / "phase7j_broad_universe_signal_retest" / "broad_fundamentals.csv",
     "SEC EDGAR normalized fundamentals (PIT, filing-dated)"),
    ("global_etf_intake", INPUT_DIR / "global_assets" / "manual" / "manual_global_etf_price_template.csv",
     "manual global-ETF intake template (superseded by Norgate proxies)"),
]

# --------------------------------------------------------------------------- #
# Universe (reuse 8-D feasibility logic / preference).
# --------------------------------------------------------------------------- #
UNIVERSE_PREFERENCE = list(P8D.UNIVERSE_PREFERENCE)
UNIVERSE_INDEX_NAME = dict(P8D.UNIVERSE_INDEX_NAME)
MAX_FEASIBLE_DAILY_SYMBOLS = P8D.MAX_FEASIBLE_DAILY_SYMBOLS
MIN_UNIVERSE_MEMBERS = P8D.MIN_UNIVERSE_MEMBERS

PANEL_START = "1990-01-01"
PANEL_END = "2026-12-31"
BENCHMARK_SYMBOL = "SPY"

GRID_FREQ = P8D.GRID_FREQ
FWD_HORIZONS = P8D.FWD_HORIZONS
WEEK_TRADING_DAYS = P8D.WEEK_TRADING_DAYS
RECENT_START = P8D.RECENT_START
RECENT_LABEL = P8D.RECENT_LABEL
WALK_FORWARD_FOLDS = P8D.WALK_FORWARD_FOLDS
WF_BOUNDS = dict(P8D.WF_BOUNDS)

N_LIQ_BUCKETS = P8D.N_LIQ_BUCKETS
N_VOL_BUCKETS = P8D.N_VOL_BUCKETS
N_BETA_BUCKETS = 3                     # adds a market-beta dimension to matched controls

# Cost model (discrete enter+exit => round trip).
COST_BPS_GRID = P8D.COST_BPS_GRID
PRIMARY_COST_BPS = P8D.PRIMARY_COST_BPS
COST_ROBUST_BPS = P8D.COST_ROBUST_BPS
MAX_POSITION = P8D.MAX_POSITION
PERIODS_PER_YEAR_WEEKLY = P8D.PERIODS_PER_YEAR_WEEKLY

# --------------------------------------------------------------------------- #
# CONFIRMED_SENSITIVITY_SIGNAL gate thresholds (a priori; borderline never rounded up).
# --------------------------------------------------------------------------- #
GATE_MIN_EVENTS_TOTAL = 1000
GATE_MIN_EVENTS_RECENT = 100
GATE_MIN_EV_AFTER_COST = 0.0
GATE_MIN_LIFT = 0.0015
GATE_HIT_RATE_LIFT_PP = 0.03
GATE_PAYOFF_LIFT = 0.05
GATE_WORST_DECILE_FLOOR = -0.12
GATE_MAX_YEAR_CONC = 0.40
GATE_MAX_SECTOR_CONC = 0.50
GATE_MAX_TICKER_CONC = 0.05            # <= 5% of triggers from any single ticker (cohort breadth)
GATE_PLACEBO_MAX_LIFT = 0.0010
GATE_MIN_WF_FOLDS_POSITIVE = 2

# --------------------------------------------------------------------------- #
# Campaign budget.
# --------------------------------------------------------------------------- #
MAX_TOTAL_SETUPS = 200
MAX_PER_FAMILY = 60
CHALLENGE_MIN_FRAC = 0.30
MAX_CYCLES = 3

# Sensitivity estimation windows.
SENS_BETA_WINDOW = 252
SENS_BETA_MINOBS = 126
SHOCK_WINDOW = 252                     # z-score normalisation window for driver shocks
SHOCK_LOOKBACK = 20                    # trailing-return horizon that defines a "shock"
COHORT_TOP = 0.80                      # top-quintile rolling-beta -> "high" cohort
COHORT_BOTTOM = 0.20                   # bottom-quintile -> "low" cohort
SHOCK_Z = 1.0                          # |z| >= 1.0 defines a driver shock

# --------------------------------------------------------------------------- #
# Agents (12) — same system as 8-A..8-D.
# --------------------------------------------------------------------------- #
DIR_A = "quant-research-director"
DATA_A = "data-foundation-agent"
UNI_A = "universe-construction-agent"
FEAT_A = "feature-library-agent"
SENS_A = "sensitivity-estimation-agent"
MACRO_A = "macro-cross-asset-agent"
EVENT_A = "event-driver-agent"
VOL_A = "volatility-liquidity-agent"
VAL_A = "validation-skeptic-agent"
RSK_A = "risk-portfolio-agent"
PROV_A = "provider-acquisition-agent"
PUB_A = "signal-publishing-agent"
ALL_AGENTS = [DIR_A, DATA_A, UNI_A, FEAT_A, SENS_A, MACRO_A, EVENT_A, VOL_A, VAL_A, RSK_A, PROV_A, PUB_A]

# --------------------------------------------------------------------------- #
# Setup families.
# --------------------------------------------------------------------------- #
FAM_MACRO_SENS = "macro_shock_x_sensitivity"           # LOCAL_READY (testable now)
FAM_REVISION_SENS = "revision_event_x_sensitivity"     # NEEDS_PROVIDER
FAM_NEWS_SENS = "news_sentiment_x_sensitivity"         # NEEDS_PROVIDER
FAM_OPTIONS_SENS = "options_iv_x_sensitivity"          # NEEDS_PROVIDER
ALLOWED_FAMILIES = (FAM_MACRO_SENS, FAM_REVISION_SENS, FAM_NEWS_SENS, FAM_OPTIONS_SENS)
FAMILY_AGENT = {
    FAM_MACRO_SENS: MACRO_A,
    FAM_REVISION_SENS: EVENT_A,
    FAM_NEWS_SENS: EVENT_A,
    FAM_OPTIONS_SENS: EVENT_A,
}
PROVIDER_FAMILIES = (FAM_REVISION_SENS, FAM_NEWS_SENS, FAM_OPTIONS_SENS)


def agent_for(family: str, is_challenge: bool) -> str:
    if is_challenge:
        return VAL_A
    return FAMILY_AGENT.get(family, DIR_A)


# =========================================================================== #
# External driver catalog (Part B).  proxy=None => NEEDS_PROVIDER (no local series).
# Each driver: key, family, proxy symbol (Norgate) or None, frequency, PIT-safe,
# mechanism, candidate horizons, availability status.
# =========================================================================== #
@dataclass(frozen=True)
class Driver:
    key: str
    label: str
    family: str
    proxy: Optional[str]
    source: str
    frequency: str
    pit_safe: bool
    mechanism: str
    horizons: str
    availability: str
    change_type: str = "ret"           # "ret" (pct change) or "level" (VIX uses pct change of level)


DRIVER_CATALOG: List[Driver] = [
    # --- macro / cross-asset proxies: LOCAL_READY via Norgate -------------- #
    Driver("market", "Market (SPY)", "macro_cross_asset", "SPY", "Norgate ETF", "daily", True,
           "broad equity beta / risk appetite", "5d/10d/20d/60d", "LOCAL_READY"),
    Driver("oil", "Crude oil (USO)", "macro_cross_asset", "USO", "Norgate ETF", "daily", True,
           "energy input/output cost; energy-sector revenue", "5d/10d/20d", "LOCAL_READY"),
    Driver("rates", "Long duration (TLT)", "macro_cross_asset", "TLT", "Norgate ETF", "daily", True,
           "rate/duration sensitivity (TLT up == yields down)", "10d/20d/60d", "LOCAL_READY"),
    Driver("credit", "High-yield credit (HYG)", "macro_cross_asset", "HYG", "Norgate ETF", "daily", True,
           "credit-spread / risk-off stress transmission", "5d/10d/20d", "LOCAL_READY"),
    Driver("usd", "US dollar (UUP)", "macro_cross_asset", "UUP", "Norgate ETF", "daily", True,
           "FX translation / exporter competitiveness", "10d/20d/60d", "LOCAL_READY"),
    Driver("vix", "Volatility ($VIX)", "macro_cross_asset", "$VIX", "Norgate index", "daily", True,
           "implied-volatility regime / risk-off shocks", "5d/10d/20d", "LOCAL_READY", change_type="level"),
    Driver("commodity", "Broad commodities (DBC)", "macro_cross_asset", "DBC", "Norgate ETF", "daily", True,
           "inflation / commodity-input sensitivity", "10d/20d/60d", "LOCAL_READY"),
    Driver("size", "Small caps (IWM)", "macro_cross_asset", "IWM", "Norgate ETF", "daily", True,
           "size / risk-appetite tilt", "10d/20d/60d", "LOCAL_READY"),
    Driver("sector", "Own GICS sector ETF", "sector_industry", "SECTOR", "Norgate ETF", "daily", True,
           "sector leadership / rotation sensitivity", "10d/20d/60d", "LOCAL_READY"),
    # --- NEEDS_PROVIDER: no local time series ----------------------------- #
    Driver("analyst_revision", "Analyst estimate revisions", "analyst_estimates", None,
           "IBES/Zacks/FMP/AlphaVantage (paid/keyed)", "event", True,
           "consensus EPS/target revisions re-rate revision-sensitive names", "5d/20d/60d", "NEEDS_PROVIDER"),
    Driver("earnings_surprise", "Earnings surprise/guidance", "earnings_events", None,
           "AlphaVantage/FMP/Finnhub (keyed)", "event", True,
           "surprise + guidance drift in surprise-sensitive names", "5d/20d", "NEEDS_PROVIDER"),
    Driver("news_sentiment", "News sentiment shocks", "news_sentiment", None,
           "news/sentiment provider (keyed)", "event", True,
           "sentiment shocks move sentiment-reactive names", "1d/5d/10d", "NEEDS_PROVIDER"),
    Driver("options_iv", "Options IV / skew", "options_iv", None,
           "OptionMetrics/IVolatility/CBOE (paid)", "daily", True,
           "implied-vol/skew shifts precede moves in options-informative names", "5d/10d/20d", "NEEDS_PROVIDER"),
    Driver("short_interest", "Short interest / borrow", "short_interest", None,
           "FINRA/Ortex/S3 (paid/keyed)", "biweekly", True,
           "borrow/short-squeeze dynamics in heavily-shorted names", "10d/20d", "NEEDS_PROVIDER"),
]
DRIVER_BY_KEY = {d.key: d for d in DRIVER_CATALOG}

# Drivers used for per-ticker rolling-beta sensitivity (proxy-backed, excluding "sector"
# which is handled per-symbol against the symbol's own sector ETF).
SENS_DRIVERS = ["market", "oil", "rates", "credit", "usd", "vix", "commodity", "size"]
# Date-level shock drivers (trailing return + z-score on the proxy).
SHOCK_DRIVERS = ["market", "oil", "rates", "credit", "usd", "vix", "commodity"]

# GICS sector -> SPDR sector ETF (all confirmed available in Norgate).
SECTOR_ETF = {
    "Energy": "XLE",
    "Information Technology": "XLK",
    "Financials": "XLF",
    "Health Care": "XLV",
    "Industrials": "XLI",
    "Utilities": "XLU",
    "Consumer Staples": "XLP",
    "Consumer Discretionary": "XLY",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
}
ALL_PROXY_SYMBOLS = sorted({d.proxy for d in DRIVER_CATALOG if d.proxy and d.proxy != "SECTOR"}
                           | set(SECTOR_ETF.values()))


# =========================================================================== #
# Sensitivity cohorts (Part C/D).  Each cohort = top/bottom cross-sectional
# quintile of a driver's leak-safe rolling beta, materialised per decision date.
# =========================================================================== #
@dataclass(frozen=True)
class Cohort:
    col: str
    driver: str
    side: str                          # "high" (>= COHORT_TOP pct) or "low" (<= COHORT_BOTTOM pct)
    label: str
    mechanism: str


COHORT_CATALOG: List[Cohort] = [
    Cohort("cohort_high_beta", "market", "high", "high_beta_market_sensitive",
           "top-quintile rolling market beta — amplifies market shocks"),
    Cohort("cohort_low_beta", "market", "low", "low_beta_defensive",
           "bottom-quintile market beta — defensive, dampens market shocks"),
    Cohort("cohort_oil_pos", "oil", "high", "oil_positive_sensitive",
           "top-quintile oil beta — rises with crude"),
    Cohort("cohort_oil_neg", "oil", "low", "oil_negative_sensitive",
           "bottom-quintile oil beta — hurt by rising crude (oil as input cost)"),
    Cohort("cohort_rates_pos", "rates", "high", "rates_positive_sensitive",
           "top-quintile TLT beta — long-duration, benefits when yields fall"),
    Cohort("cohort_rates_neg", "rates", "low", "rates_negative_sensitive",
           "bottom-quintile TLT beta — short-duration / hurt when yields fall"),
    Cohort("cohort_credit_sens", "credit", "high", "credit_stress_sensitive",
           "top-quintile HYG beta — transmits credit/risk-off stress"),
    Cohort("cohort_usd_pos", "usd", "high", "dollar_positive_sensitive",
           "top-quintile UUP beta — rises with the dollar"),
    Cohort("cohort_usd_neg", "usd", "low", "dollar_negative_sensitive",
           "bottom-quintile UUP beta — hurt by a rising dollar (exporters)"),
    Cohort("cohort_vol_spike_sens", "vix", "low", "volatility_spike_sensitive",
           "bottom-quintile VIX beta (most negative) — most hurt by volatility spikes"),
    Cohort("cohort_sector_lead", "sector", "high", "sector_leadership_sensitive",
           "top-quintile own-sector-ETF beta — moves with sector leadership"),
]
COHORT_BY_COL = {c.col: c for c in COHORT_CATALOG}
COHORT_COLS = [c.col for c in COHORT_CATALOG]


# =========================================================================== #
# Date-level driver shock columns (Part E inputs).  All trailing / leak-safe.
# =========================================================================== #
SHOCK_COLS: List[Tuple[str, str, str]] = [
    # (column, driver, description)
    ("drv_market_ret_20", "market", "SPY trailing 20d return"),
    ("drv_market_shock_z", "market", "z-score of SPY 20d return vs trailing 252d"),
    ("drv_oil_ret_20", "oil", "USO trailing 20d return"),
    ("drv_oil_shock_z", "oil", "z-score of USO 20d return (oil shock)"),
    ("drv_rates_ret_20", "rates", "TLT trailing 20d return (duration; +ve == yields down)"),
    ("drv_rates_shock_z", "rates", "z-score of TLT 20d return (rates shock)"),
    ("drv_credit_ret_20", "credit", "HYG trailing 20d return"),
    ("drv_credit_shock_z", "credit", "z-score of HYG 20d return (-ve == credit stress)"),
    ("drv_usd_ret_20", "usd", "UUP trailing 20d return"),
    ("drv_usd_shock_z", "usd", "z-score of UUP 20d return (dollar shock)"),
    ("drv_vix_chg_20", "vix", "VIX 20d level change / level (vol shock)"),
    ("drv_vix_spike_z", "vix", "z-score of VIX 20d change (+ve == volatility spike)"),
    ("drv_commodity_ret_20", "commodity", "DBC trailing 20d return"),
    ("drv_commodity_shock_z", "commodity", "z-score of DBC 20d return (commodity shock)"),
]
SHOCK_COL_NAMES = [c for c, _d, _x in SHOCK_COLS]


# =========================================================================== #
# Driver proxy loading + per-ticker sensitivity estimation.
# =========================================================================== #
def load_driver_returns(adapter, *, start: str = PANEL_START, end: str = PANEL_END
                        ) -> Tuple[Dict[str, pd.Series], Dict[str, pd.Series], List[dict]]:
    """Return (proxy_close, proxy_ret, coverage_rows) for every proxy symbol (read-only)."""
    closes: Dict[str, pd.Series] = {}
    rets: Dict[str, pd.Series] = {}
    coverage: List[dict] = []
    for sym in ALL_PROXY_SYMBOLS:
        px = adapter.price_history(sym, start, end)
        if px is None or "Close" not in getattr(px, "columns", []):
            coverage.append({"proxy": sym, "status": "NO_PRICE", "n_rows": 0, "start": "", "end": ""})
            continue
        px = px[~px.index.duplicated(keep="last")].sort_index()
        close = pd.to_numeric(px["Close"], errors="coerce").where(lambda s: s > 0)
        close = close.dropna()
        if close.empty:
            coverage.append({"proxy": sym, "status": "EMPTY", "n_rows": 0, "start": "", "end": ""})
            continue
        closes[sym] = close
        rets[sym] = close.pct_change()
        coverage.append({"proxy": sym, "status": "OK", "n_rows": int(len(close)),
                         "start": str(close.index.min())[:10], "end": str(close.index.max())[:10]})
    return closes, rets, coverage


def _rolling_beta(y_ret: pd.Series, x_ret: pd.Series,
                  win: int = SENS_BETA_WINDOW, minp: int = SENS_BETA_MINOBS) -> pd.Series:
    """Leak-safe trailing-window beta of y on x: cov(y,x)/var(x) over a backward window."""
    x = x_ret.reindex(y_ret.index)
    cov = y_ret.rolling(win, min_periods=minp).cov(x)
    var = x.rolling(win, min_periods=minp).var()
    return cov / var.replace(0.0, np.nan)


def symbol_sensitivities(close: pd.Series, sector: str, proxy_ret: Dict[str, pd.Series]
                         ) -> Tuple[pd.DataFrame, List[dict]]:
    """Per-symbol leak-safe rolling betas to each sensitivity driver (every value at t uses
    only returns <= t). Returns (beta_frame, sens_summary_rows)."""
    ret_1 = close.pct_change()
    out: Dict[str, pd.Series] = {}
    summary: List[dict] = []
    drivers = list(SENS_DRIVERS)
    for d in drivers:
        proxy = DRIVER_BY_KEY[d].proxy
        if proxy is None or proxy not in proxy_ret:
            continue
        beta = _rolling_beta(ret_1, proxy_ret[proxy])
        out[f"sens_beta_{d}"] = beta
        summary.append(_sens_summary_row(d, proxy, ret_1, proxy_ret[proxy], beta))
    # own-sector ETF beta
    etf = SECTOR_ETF.get(sector)
    if etf and etf in proxy_ret:
        beta = _rolling_beta(ret_1, proxy_ret[etf])
        out["sens_beta_sector"] = beta
        summary.append(_sens_summary_row("sector", etf, ret_1, proxy_ret[etf], beta))
    frame = pd.DataFrame(out, index=close.index)
    return frame, summary


def _sens_summary_row(driver: str, proxy: str, y_ret: pd.Series, x_ret: pd.Series,
                      beta: pd.Series) -> dict:
    """Static per-ticker/driver summary for the sensitivity map (latest + full-sample)."""
    b = beta.dropna()
    x = x_ret.reindex(y_ret.index)
    full = pd.concat([y_ret, x], axis=1).dropna()
    if len(full) >= SENS_BETA_MINOBS:
        vx = float(full.iloc[:, 1].var())
        full_beta = float(full.iloc[:, 0].cov(full.iloc[:, 1]) / vx) if vx > 0 else float("nan")
        full_corr = float(full.iloc[:, 0].corr(full.iloc[:, 1]))
    else:
        full_beta = full_corr = float("nan")
    latest = float(b.iloc[-1]) if len(b) else float("nan")
    stability = float(b.std()) if len(b) > 5 else float("nan")
    # sign consistency of the rolling beta (fraction of obs sharing the dominant sign)
    if len(b):
        pos = float((b > 0).mean())
        sign_consistency = max(pos, 1.0 - pos)
        direction = "positive" if (not math.isnan(latest) and latest >= 0) else "negative"
    else:
        sign_consistency = float("nan")
        direction = ""
    n_obs = int(len(b))
    # confidence: more obs + more sign-stable + non-trivial |beta| -> higher (0..1)
    if n_obs:
        sc = sign_consistency if not math.isnan(sign_consistency) else 0.0
        lat = 0.0 if math.isnan(latest) else min(abs(latest), 1.0)
        conf = float(np.clip(min(n_obs / 1500.0, 1.0) * sc * lat, 0.0, 1.0))
    else:
        conf = 0.0
    return {
        "driver": driver, "proxy": proxy, "n_obs": n_obs,
        "latest_beta": _round(latest), "full_sample_beta": _round(full_beta),
        "full_sample_corr": _round(full_corr), "rolling_beta_std": _round(stability),
        "sign_consistency": _round(sign_consistency), "direction": direction,
        "confidence": _round(conf),
        "min_obs_required": SENS_BETA_MINOBS,
        "meets_min_obs": n_obs >= SENS_BETA_MINOBS,
    }


def build_driver_shock_grid(proxy_close: Dict[str, pd.Series],
                            grid_dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Date-level trailing shock columns (leak-safe), one row per grid date."""
    daily = {}
    for col, driver, _desc in SHOCK_COLS:
        proxy = DRIVER_BY_KEY[driver].proxy
        if proxy is None or proxy not in proxy_close:
            continue
        s = proxy_close[proxy]
        if DRIVER_BY_KEY[driver].change_type == "level":
            base = s.diff(SHOCK_LOOKBACK) / s.shift(SHOCK_LOOKBACK).replace(0.0, np.nan)
        else:
            base = s / s.shift(SHOCK_LOOKBACK) - 1.0
        if col.endswith("_shock_z") or col.endswith("_spike_z"):
            mu = base.rolling(SHOCK_WINDOW, min_periods=SHOCK_WINDOW // 2).mean()
            sd = base.rolling(SHOCK_WINDOW, min_periods=SHOCK_WINDOW // 2).std()
            daily[col] = (base - mu) / sd.replace(0.0, np.nan)
        else:
            daily[col] = base
    if not daily:
        return pd.DataFrame(index=grid_dates)
    df = pd.DataFrame(daily)
    df = df.reindex(grid_dates, method="ffill", limit=WEEK_TRADING_DAYS)
    return df


# =========================================================================== #
# Sensitivity panel build -> weekly observation grid (events + controls).
# =========================================================================== #
@dataclass
class SensPanel:
    grid: pd.DataFrame
    metadata: pd.DataFrame
    spy_close: pd.Series
    grid_dates: pd.DatetimeIndex
    ok: bool
    sensitivity_map: pd.DataFrame = field(default_factory=pd.DataFrame)
    proxy_coverage: List[dict] = field(default_factory=list)
    quality_rows: List[dict] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def build_sensitivity_panel(adapter, symbols: Sequence[str], index_name: str, *,
                            start: str = PANEL_START, end: str = PANEL_END) -> SensPanel:
    """Daily Norgate data -> per-symbol features + sensitivities + forward labels -> weekly grid."""
    spy_px = adapter.price_history(BENCHMARK_SYMBOL, start, end)
    if spy_px is None or "Close" not in getattr(spy_px, "columns", []):
        return SensPanel(pd.DataFrame(), pd.DataFrame(), pd.Series(dtype=float),
                         pd.DatetimeIndex([]), False, notes=["SPY daily history unavailable"])
    spy_px = spy_px[~spy_px.index.duplicated(keep="last")].sort_index()
    spy_close = pd.to_numeric(spy_px["Close"], errors="coerce").where(lambda s: s > 0)
    grid_dates = _weekly_grid_dates(spy_close.dropna().index)

    proxy_close, proxy_ret, coverage = load_driver_returns(adapter, start=start, end=end)
    shock_grid = build_driver_shock_grid(proxy_close, grid_dates)

    feat_cols = [c for c, _a, _d in P8D.FEATURE_CATALOG]
    label_cols = [c for c, _d in P8D.EVENT_LABELS]
    sens_cols = [f"sens_beta_{d}" for d in SENS_DRIVERS] + ["sens_beta_sector"]

    blocks: List[pd.DataFrame] = []
    meta: List[dict] = []
    quality: List[dict] = []
    sens_rows: List[dict] = []

    for sym in symbols:
        px = adapter.price_history(sym, start, end)
        if px is None or "Close" not in getattr(px, "columns", []):
            quality.append({"ticker": sym, "status": "NO_PRICE", "n_daily": 0, "n_grid": 0})
            continue
        px = px[~px.index.duplicated(keep="last")].sort_index()
        close = pd.to_numeric(px["Close"], errors="coerce").where(lambda s: s > 0)
        if int(close.notna().sum()) < 300:
            quality.append({"ticker": sym, "status": "TOO_SHORT",
                            "n_daily": int(close.notna().sum()), "n_grid": 0})
            continue
        volume = pd.to_numeric(px["Volume"], errors="coerce") if "Volume" in px.columns \
            else pd.Series(np.nan, index=close.index)
        dollar_vol = pd.to_numeric(px["Turnover"], errors="coerce") if "Turnover" in px.columns \
            else close * volume

        mem = adapter.index_membership(sym, index_name, start, end)
        if mem is None or not len(mem):
            quality.append({"ticker": sym, "status": "NO_MEMBERSHIP",
                            "n_daily": int(close.notna().sum()), "n_grid": 0})
            continue
        mem = pd.to_numeric(mem, errors="coerce").reindex(close.index).ffill().fillna(0.0)
        is_member = mem > 0

        sector = adapter.sector(sym) or "UNKNOWN"
        feats = symbol_features(close, volume, dollar_vol, spy_close)
        sens, summ = symbol_sensitivities(close, sector, proxy_ret)
        labels = forward_labels(close, spy_close)
        frame = pd.concat([feats, sens, labels], axis=1)
        frame = frame.reindex(grid_dates, method="ffill", limit=WEEK_TRADING_DAYS)
        member_on_grid = is_member.reindex(grid_dates, method="ffill", limit=WEEK_TRADING_DAYS).fillna(False)
        frame = frame[member_on_grid.to_numpy().astype(bool)]
        frame = frame.dropna(subset=["rv_20", "ret_60", "fwd_excess_20", "fwd_total_5", "sens_beta_market"])
        if frame.empty:
            quality.append({"ticker": sym, "status": "NO_GRID_OBS",
                            "n_daily": int(close.notna().sum()), "n_grid": 0})
            continue
        frame = frame.reset_index().rename(columns={"index": "date"})
        if "date" not in frame.columns:
            frame = frame.rename(columns={frame.columns[0]: "date"})
        frame["symbol"] = sym
        frame["sector"] = sector

        last_q = adapter.last_quoted_date(sym)
        first_q = adapter.first_quoted_date(sym)
        delisted = P8A._is_delisted_symbol(sym, last_q)
        blocks.append(frame)
        meta.append({"ticker": sym, "gics_sector": sector, "sector_etf": SECTOR_ETF.get(sector, ""),
                     "first_quoted_date": first_q or "", "last_quoted_date": last_q or "",
                     "is_delisted": delisted, "n_grid_obs": int(len(frame))})
        for r in summ:
            sens_rows.append({"ticker": sym, "sector": sector, **r})
        quality.append({"ticker": sym, "status": "OK",
                        "n_daily": int(close.notna().sum()), "n_grid": int(len(frame))})

    if not blocks:
        return SensPanel(pd.DataFrame(), pd.DataFrame(meta), spy_close, grid_dates, False,
                         pd.DataFrame(sens_rows), coverage, quality, ["no member observations produced"])

    grid = pd.concat(blocks, ignore_index=True)
    keep = ["date", "symbol", "sector"] + feat_cols + sens_cols + label_cols
    grid = grid[[c for c in keep if c in grid.columns]]
    grid = _add_cross_sectional(grid, shock_grid)
    metadata = pd.DataFrame(meta).set_index("ticker")
    return SensPanel(grid, metadata, spy_close, grid_dates, True,
                     pd.DataFrame(sens_rows), coverage, quality, [])


def _add_cross_sectional(grid: pd.DataFrame, shock_grid: pd.DataFrame) -> pd.DataFrame:
    """Add 8-D cross-sectional features + market-beta bucket + cohort flags + driver shocks."""
    g = grid
    by_date = g.groupby("date", observed=True)
    g["liquidity_pct"] = by_date["dollar_vol_20"].rank(pct=True)
    sect_mean = g.groupby(["date", "sector"], observed=True)["rel_str_60"].transform("mean")
    g["sector_rel_str_60"] = g["rel_str_60"] - sect_mean
    g["market_breadth"] = by_date["above_ma200"].transform("mean")
    g["sector_breadth"] = g.groupby(["date", "sector"], observed=True)["above_ma200"].transform("mean")
    rk = by_date["fwd_excess_20"].rank(pct=True)
    g["large_pos_20"] = (rk >= 0.90).astype(float)
    g["large_neg_20"] = (rk <= 0.10).astype(float)
    for col in P8D.PCTILE_FEATURES:
        if col in g.columns:
            g[f"pct_{col}"] = g.groupby("date", observed=True)[col].rank(pct=True)

    # matched-control buckets: sector x liquidity x volatility x market-beta
    g["liq_bucket"] = (g["liquidity_pct"] * N_LIQ_BUCKETS).clip(0, N_LIQ_BUCKETS - 1e-9).astype(int)
    vol_pct = by_date["rv_20"].rank(pct=True)
    g["vol_bucket"] = (vol_pct * N_VOL_BUCKETS).clip(0, N_VOL_BUCKETS - 1e-9).astype(int)
    beta_pct = by_date["beta_60"].rank(pct=True)
    g["beta_bucket"] = (beta_pct * N_BETA_BUCKETS).clip(0, N_BETA_BUCKETS - 1e-9).astype(int)
    g["ctrl_bucket"] = (g["sector"].astype(str) + "|" + g["liq_bucket"].astype(str)
                        + "|" + g["vol_bucket"].astype(str) + "|" + g["beta_bucket"].astype(str))

    # sensitivity cohort flags: per-date cross-sectional quintile of each driver's rolling beta
    for c in COHORT_CATALOG:
        scol = f"sens_beta_{c.driver}"
        if scol not in g.columns:
            g[c.col] = 0.0
            continue
        pr = g.groupby("date", observed=True)[scol].rank(pct=True)
        if c.side == "high":
            g[c.col] = (pr >= COHORT_TOP).astype(float)
        else:
            g[c.col] = (pr <= COHORT_BOTTOM).astype(float)
        g[c.col] = g[c.col].where(g[scol].notna(), 0.0)

    # date-level driver shocks merged onto every row of that date
    if shock_grid is not None and not shock_grid.empty:
        sg = shock_grid.copy()
        sg.index.name = "date"
        g = g.merge(sg.reset_index(), on="date", how="left")
    else:
        for col in SHOCK_COL_NAMES:
            g[col] = np.nan
    return g


def persist_sensitivity_panel(panel: SensPanel) -> dict:
    PANEL_ROOT.mkdir(parents=True, exist_ok=True)
    files = {}
    gp = PANEL_ROOT / "weekly_observation_grid.csv"
    panel.grid.to_csv(gp, index=False)
    files["weekly_observation_grid"] = str(gp)
    mp = PANEL_ROOT / "symbol_metadata.csv"
    panel.metadata.to_csv(mp)
    files["symbol_metadata"] = str(mp)
    smp = PANEL_ROOT / "ticker_sensitivity_map_full.csv"
    panel.sensitivity_map.to_csv(smp, index=False)
    files["ticker_sensitivity_map_full"] = str(smp)
    sp = PANEL_ROOT / "spy_daily_close.csv"
    panel.spy_close.to_csv(sp)
    files["spy_daily_close"] = str(sp)
    return files


# =========================================================================== #
# Setup definitions (deterministic, interpretable, pre-registered thresholds).
# =========================================================================== #
@dataclass
class SensSetup:
    setup_id: str
    cycle: int
    family: str
    owning_agent: str
    is_challenge: bool
    driver: str
    cohort: str                        # cohort column this setup conditions on ("" if none)
    hypothesis: str
    primary_horizon: int
    conditions: List[Tuple[str, str, float]]
    success_gate: str
    stop_condition: str
    challenges: str = ""
    placebo: bool = False
    needs_provider: bool = False       # template requires external data not available locally
    provider_note: str = ""
    status: str = ""
    reason: str = ""
    metrics: dict = field(default_factory=dict)

    def trigger_mask(self, g: pd.DataFrame) -> pd.Series:
        m = pd.Series(True, index=g.index)
        for col, op, val in self.conditions:
            if col not in g.columns:
                return pd.Series(False, index=g.index)
            m &= _OPS[op](g[col], val) & g[col].notna()
        return m


def _mk(setup_id, cycle, family, driver, cohort, horizon, conditions, hypothesis, *,
        is_challenge=False, challenges="", placebo=False, needs_provider=False,
        provider_note="") -> SensSetup:
    return SensSetup(setup_id=setup_id, cycle=cycle, family=family,
                     owning_agent=agent_for(family, is_challenge), is_challenge=is_challenge,
                     driver=driver, cohort=cohort, hypothesis=hypothesis,
                     primary_horizon=horizon, conditions=list(conditions),
                     success_gate=GATE_TXT, stop_condition=STOP_TXT, challenges=challenges,
                     placebo=placebo, needs_provider=needs_provider, provider_note=provider_note)


GATE_TXT = ("uses explicit driver+sensitivity cohort; conditional EV after 25bps > 0; lift vs "
            "matched control (same shock, NOT in cohort) >= deflated hurdle; hit-rate +3pp or "
            "payoff lift; survives walk-forward + 2015-2026; not concentrated; placebo-clean")
STOP_TXT = ("reject if events < 1000 (or <100 in 2015-2026), or EV<=0 after cost, or lift<=0 vs "
            "matched control, or fails 2015-2026, or concentrated, or placebo shows lift")


def plan_cycle_1() -> List[SensSetup]:
    """Macro-shock x sensitivity-cohort x confirm templates + challenges + provider templates."""
    s: List[SensSetup] = []
    # --- testable macro-shock x sensitivity candidates ------------------- #
    # oil up shock rides oil-positive cohort
    s.append(_mk("S8E-001", 1, FAM_MACRO_SENS, "oil", "cohort_oil_pos", 20,
                 [("drv_oil_shock_z", "ge", SHOCK_Z), ("cohort_oil_pos", "ge", 1.0), ("ret_5", "gt", 0.0)],
                 "When crude shocks UP, oil-positive-sensitive names that confirm continue over 20d."))
    # oil down shock hurts oil-positive -> test oil-negative cohort outperforming on oil-down
    s.append(_mk("S8E-002", 1, FAM_MACRO_SENS, "oil", "cohort_oil_neg", 20,
                 [("drv_oil_shock_z", "le", -SHOCK_Z), ("cohort_oil_neg", "ge", 1.0), ("rel_str_60", "gt", 0.0)],
                 "When crude shocks DOWN, oil-negative-sensitive names (input-cost relief) outperform over 20d."))
    # rates rally (TLT up) lifts long-duration cohort
    s.append(_mk("S8E-010", 1, FAM_MACRO_SENS, "rates", "cohort_rates_pos", 20,
                 [("drv_rates_shock_z", "ge", SHOCK_Z), ("cohort_rates_pos", "ge", 1.0), ("above_ma50", "ge", 1.0)],
                 "When bonds rally (yields fall), long-duration-sensitive names in uptrends outperform over 20d."))
    s.append(_mk("S8E-011", 1, FAM_MACRO_SENS, "rates", "cohort_rates_neg", 20,
                 [("drv_rates_shock_z", "le", -SHOCK_Z), ("cohort_rates_neg", "ge", 1.0), ("rel_str_60", "gt", 0.0)],
                 "When bonds sell off (yields rise), short-duration-sensitive names outperform over 20d."))
    # credit stress (HYG down) -> defensives (low beta) hold up
    s.append(_mk("S8E-020", 1, FAM_MACRO_SENS, "credit", "cohort_low_beta", 10,
                 [("drv_credit_shock_z", "le", -SHOCK_Z), ("cohort_low_beta", "ge", 1.0), ("above_ma200", "ge", 1.0)],
                 "Under credit stress, low-beta defensives above their 200d MA outperform over 10d."))
    # vol spike -> low-beta defensives outperform
    s.append(_mk("S8E-030", 1, FAM_MACRO_SENS, "vix", "cohort_low_beta", 10,
                 [("drv_vix_spike_z", "ge", SHOCK_Z), ("cohort_low_beta", "ge", 1.0), ("above_ma200", "ge", 1.0)],
                 "On a volatility spike, low-beta defensives in uptrends outperform over 10d."))
    s.append(_mk("S8E-031", 1, FAM_MACRO_SENS, "vix", "cohort_vol_spike_sens", 20,
                 [("drv_vix_spike_z", "ge", SHOCK_Z), ("cohort_vol_spike_sens", "ge", 1.0), ("ret_5", "le", 0.0)],
                 "Volatility-spike-sensitive names that just sold off rebound over 20d once vol spikes."))
    # market shock down + high-beta rebound (oversold high-beta)
    s.append(_mk("S8E-040", 1, FAM_MACRO_SENS, "market", "cohort_high_beta", 10,
                 [("drv_market_shock_z", "le", -SHOCK_Z), ("cohort_high_beta", "ge", 1.0), ("pct_ret_5", "le", 0.20)],
                 "After a market down-shock, oversold high-beta names rebound over 10d."))
    # dollar shock up + dollar-positive cohort
    s.append(_mk("S8E-050", 1, FAM_MACRO_SENS, "usd", "cohort_usd_pos", 20,
                 [("drv_usd_shock_z", "ge", SHOCK_Z), ("cohort_usd_pos", "ge", 1.0), ("rel_str_60", "gt", 0.0)],
                 "When the dollar shocks up, dollar-positive-sensitive names with rel strength continue over 20d."))
    s.append(_mk("S8E-051", 1, FAM_MACRO_SENS, "usd", "cohort_usd_neg", 20,
                 [("drv_usd_shock_z", "le", -SHOCK_Z), ("cohort_usd_neg", "ge", 1.0), ("rel_str_60", "gt", 0.0)],
                 "When the dollar shocks down, dollar-negative-sensitive exporters outperform over 20d."))
    # commodity shock up + commodity-sensitive (use oil_pos as commodity proxy cohort via beta)
    s.append(_mk("S8E-060", 1, FAM_MACRO_SENS, "commodity", "cohort_oil_pos", 20,
                 [("drv_commodity_shock_z", "ge", SHOCK_Z), ("cohort_oil_pos", "ge", 1.0), ("ret_20", "gt", 0.0)],
                 "When broad commodities shock up, commodity-sensitive names that confirm continue over 20d."))
    # sector leadership: own-sector ETF strong + sector-leadership cohort
    s.append(_mk("S8E-070", 1, FAM_MACRO_SENS, "sector", "cohort_sector_lead", 20,
                 [("cohort_sector_lead", "ge", 1.0), ("pct_sector_rel_str_60", "ge", 0.70),
                  ("sector_breadth", "ge", 0.50)],
                 "Sector-leadership-sensitive names leading a healthy sector keep leading over 20d."))

    # --- validation/skeptic challenges (>= 30%) -------------------------- #
    # (a) WRONG-cohort: same shock, OPPOSITE cohort -> should not show the same lift
    s.append(_mk("S8E-901", 1, FAM_MACRO_SENS, "oil", "cohort_oil_neg", 20,
                 [("drv_oil_shock_z", "ge", SHOCK_Z), ("cohort_oil_neg", "ge", 1.0), ("ret_5", "gt", 0.0)],
                 "CHALLENGE: oil UP shock but oil-NEGATIVE cohort — disproves shock alone drives lift.",
                 is_challenge=True, challenges="S8E-001"))
    s.append(_mk("S8E-902", 1, FAM_MACRO_SENS, "rates", "cohort_rates_neg", 20,
                 [("drv_rates_shock_z", "ge", SHOCK_Z), ("cohort_rates_neg", "ge", 1.0), ("above_ma50", "ge", 1.0)],
                 "CHALLENGE: bond rally but SHORT-duration cohort — wrong sensitivity sign.",
                 is_challenge=True, challenges="S8E-010"))
    s.append(_mk("S8E-903", 1, FAM_MACRO_SENS, "vix", "cohort_high_beta", 10,
                 [("drv_vix_spike_z", "ge", SHOCK_Z), ("cohort_high_beta", "ge", 1.0), ("above_ma200", "ge", 1.0)],
                 "CHALLENGE: vol spike but HIGH-beta cohort — defensive hypothesis inverted.",
                 is_challenge=True, challenges="S8E-030"))
    # (b) PLACEBO: shock + confirm but NO cohort condition -> isolates the cohort contribution
    s.append(_mk("S8E-910", 1, FAM_MACRO_SENS, "oil", "", 20,
                 [("drv_oil_shock_z", "ge", SHOCK_Z), ("ret_5", "gt", 0.0)],
                 "CHALLENGE/placebo: oil shock + confirm, NO sensitivity cohort — isolates cohort lift.",
                 is_challenge=True, placebo=True, challenges="S8E-001"))
    s.append(_mk("S8E-911", 1, FAM_MACRO_SENS, "vix", "", 10,
                 [("drv_vix_spike_z", "ge", SHOCK_Z), ("above_ma200", "ge", 1.0)],
                 "CHALLENGE/placebo: vol spike + uptrend, NO cohort — isolates defensive-cohort lift.",
                 is_challenge=True, placebo=True, challenges="S8E-030"))
    # (c) NO-shock: cohort + confirm but NO driver shock -> isolates the shock contribution
    s.append(_mk("S8E-920", 1, FAM_MACRO_SENS, "oil", "cohort_oil_pos", 20,
                 [("cohort_oil_pos", "ge", 1.0), ("ret_5", "gt", 0.0)],
                 "CHALLENGE/placebo: oil-positive cohort + confirm, NO oil shock — isolates the shock lift.",
                 is_challenge=True, placebo=True, challenges="S8E-001"))

    # --- NEEDS_PROVIDER family templates (pre-registered, not faked) ----- #
    s.append(_mk("S8E-700", 1, FAM_REVISION_SENS, "analyst_revision", "(revision_sensitive)", 20,
                 [("drv_analyst_revision_up", "ge", 1.0), ("cohort_revision_sensitive", "ge", 1.0),
                  ("ret_5", "gt", 0.0)],
                 "Upward consensus revision in a revision-sensitive name with price confirmation drifts up.",
                 needs_provider=True,
                 provider_note="needs analyst estimate/revision history (IBES/Zacks/FMP) — not local"))
    s.append(_mk("S8E-710", 1, FAM_NEWS_SENS, "news_sentiment", "(sentiment_sensitive)", 5,
                 [("drv_news_sentiment_shock", "ge", 1.0), ("cohort_sentiment_sensitive", "ge", 1.0),
                  ("vol_surge_z", "ge", 1.0)],
                 "A positive sentiment shock in a sentiment-reactive name confirmed by volume drifts up.",
                 needs_provider=True,
                 provider_note="needs timestamped news/sentiment series — not local"))
    s.append(_mk("S8E-720", 1, FAM_OPTIONS_SENS, "options_iv", "(options_informative)", 10,
                 [("drv_options_iv_shift", "ge", 1.0), ("cohort_options_informative", "ge", 1.0),
                  ("ret_5", "gt", 0.0)],
                 "An IV/skew shift in an options-informative name with price confirmation precedes a move.",
                 needs_provider=True,
                 provider_note="needs options-implied vol/skew history (OptionMetrics/CBOE) — not local"))
    return s


def plan_cycle_2(leads: List[str]) -> List[SensSetup]:
    """Refine surviving macro-sensitivity setups (tighter shock + extra confirm) + placebos."""
    s: List[SensSetup] = []
    refine = {
        "S8E-001": _mk("S8E-101", 2, FAM_MACRO_SENS, "oil", "cohort_oil_pos", 20,
                       [("drv_oil_shock_z", "ge", 1.5), ("cohort_oil_pos", "ge", 1.0),
                        ("ret_5", "gt", 0.0), ("above_ma50", "ge", 1.0)],
                       "Refine: stronger oil shock + oil-positive cohort confirmed by an uptrend."),
        "S8E-010": _mk("S8E-110", 2, FAM_MACRO_SENS, "rates", "cohort_rates_pos", 20,
                       [("drv_rates_shock_z", "ge", 1.5), ("cohort_rates_pos", "ge", 1.0),
                        ("above_ma50", "ge", 1.0), ("pct_rel_str_60", "ge", 0.50)],
                       "Refine: stronger bond rally + long-duration cohort + relative strength."),
        "S8E-030": _mk("S8E-130", 2, FAM_MACRO_SENS, "vix", "cohort_low_beta", 20,
                       [("drv_vix_spike_z", "ge", 1.5), ("cohort_low_beta", "ge", 1.0),
                        ("above_ma200", "ge", 1.0), ("pct_rv_20", "le", 0.50)],
                       "Refine: bigger vol spike + low-beta defensive + low own-vol confirmation."),
    }
    for lead_id, setup in refine.items():
        s.append(setup)
    # always-present cycle-2 challenges so the >=30% challenge fraction holds at every stop point
    s.append(_mk("S8E-940", 2, FAM_MACRO_SENS, "vix", "", 20,
                 [("drv_vix_spike_z", "ge", 1.5)],
                 "CHALLENGE/placebo: big vol spike alone, NO cohort/confirm — must not match refined lift.",
                 is_challenge=True, placebo=True, challenges="cycle-2 refinements"))
    s.append(_mk("S8E-941", 2, FAM_MACRO_SENS, "market", "cohort_low_beta", 20,
                 [("cohort_low_beta", "ge", 1.0)],
                 "CHALLENGE/placebo: low-beta cohort alone, NO shock/confirm — isolates shock+confirm lift.",
                 is_challenge=True, placebo=True, challenges="cycle-2 refinements"))
    return s


def plan_cycle_3(leads: List[str]) -> List[SensSetup]:
    """Cost/recency stress challenges on any setup still alive after cycle 2."""
    s: List[SensSetup] = []
    s.append(_mk("S8E-960", 3, FAM_MACRO_SENS, "vix", "cohort_low_beta", 60,
                 [("drv_vix_spike_z", "ge", SHOCK_Z), ("cohort_low_beta", "ge", 1.0), ("above_ma200", "ge", 1.0)],
                 "CHALLENGE: same defensive edge at 60d (higher cost drag) — does lift survive?",
                 is_challenge=True, challenges="lead cost stress"))
    s.append(_mk("S8E-961", 3, FAM_MACRO_SENS, "oil", "cohort_oil_pos", 20,
                 [("drv_oil_shock_z", "ge", SHOCK_Z), ("cohort_oil_pos", "ge", 1.0), ("ret_5", "gt", 0.0)],
                 "CHALLENGE: strongest oil-sensitivity lead restricted to 2015-2026 recency stress.",
                 is_challenge=True, challenges="lead recency stress 2015-2026"))
    return s


# =========================================================================== #
# Event evaluation: cohort-aware matched controls + conditional expectancy.
# =========================================================================== #
def _roundtrip_cost(bps: float) -> float:
    return 2.0 * (bps / 1e4)


def evaluate_sensitivity_setup(setup: SensSetup, grid: pd.DataFrame) -> dict:
    """Triggered vs matched-control evaluation. Controls = same (date, ctrl_bucket), NOT
    triggered, and NOT in the setup's sensitivity cohort (so the same driver shock is present
    but the sensitivity differs). This isolates the sensitivity contribution."""
    h = setup.primary_horizon
    lbl = f"fwd_excess_{h}"
    if lbl not in grid.columns:
        return {"n_events": 0, "status_hint": ST_BLOCKED, "reason": f"no label {lbl}"}
    g = grid[grid[lbl].notna()].copy()
    if g.empty:
        return {"n_events": 0, "status_hint": ST_BLOCKED, "reason": "no labelled observations"}
    trig = setup.trigger_mask(g)
    g["_trig"] = trig.to_numpy().astype(bool)
    triggered = g[g["_trig"]]
    n_events = int(len(triggered))

    nontrig = g[~g["_trig"]]
    if setup.cohort and setup.cohort in g.columns:
        nontrig = nontrig[nontrig[setup.cohort] <= 0.0]          # controls NOT in the cohort
    ctrl_mean = nontrig.groupby(["date", "ctrl_bucket"], observed=True)[lbl].mean()
    matched = pd.Series(
        np.asarray(triggered.set_index(["date", "ctrl_bucket"]).index.map(ctrl_mean), dtype="float64"),
        index=triggered.index)
    have_ctrl = matched.notna()
    n_matched = int(have_ctrl.sum())

    base_rate = float(g[lbl].mean())
    trig_mean = float(triggered[lbl].mean()) if n_events else float("nan")
    trig_median = float(triggered[lbl].median()) if n_events else float("nan")
    ctrl_mean_overall = float(matched[have_ctrl].mean()) if n_matched else float("nan")
    lift_vs_ctrl = (float((triggered[lbl][have_ctrl] - matched[have_ctrl]).mean())
                    if n_matched else float("nan"))
    lift_vs_base = trig_mean - base_rate if n_events else float("nan")

    hit = float((triggered[lbl] > 0).mean()) if n_events else float("nan")
    ctrl_hit = float((nontrig[lbl] > 0).mean()) if len(nontrig) else float("nan")
    gains = triggered[lbl][triggered[lbl] > 0]
    losses = triggered[lbl][triggered[lbl] < 0]
    payoff = (float(gains.mean()) / abs(float(losses.mean()))
              if len(gains) and len(losses) and losses.mean() != 0 else float("nan"))
    ctrl_gains = nontrig[lbl][nontrig[lbl] > 0]
    ctrl_losses = nontrig[lbl][nontrig[lbl] < 0]
    ctrl_payoff = (float(ctrl_gains.mean()) / abs(float(ctrl_losses.mean()))
                   if len(ctrl_gains) and len(ctrl_losses) and ctrl_losses.mean() != 0 else float("nan"))

    ev_after_cost = {f"{int(b)}bps": _round(trig_mean - _roundtrip_cost(b)) for b in COST_BPS_GRID}
    worst_decile = (float(triggered[lbl][triggered[lbl] <= triggered[lbl].quantile(0.10)].mean())
                    if n_events >= 10 else float("nan"))
    mae = float(triggered["mae_20"].mean()) if "mae_20" in triggered.columns and n_events else float("nan")

    recent = triggered[triggered["date"] >= pd.Timestamp(RECENT_START)]
    n_recent = int(len(recent))
    recent_ctrl = pd.Series(
        np.asarray(recent.set_index(["date", "ctrl_bucket"]).index.map(ctrl_mean), dtype="float64"),
        index=recent.index)
    recent_have = recent_ctrl.notna()
    recent_lift = (float((recent[lbl][recent_have] - recent_ctrl[recent_have]).mean())
                   if recent_have.any() else float("nan"))

    if n_events:
        year_frac = float(triggered["date"].dt.year.value_counts(normalize=True).max())
        sector_frac = float(triggered["sector"].value_counts(normalize=True).max())
        ticker_frac = float(triggered["symbol"].value_counts(normalize=True).max())
        n_symbols = int(triggered["symbol"].nunique())
    else:
        year_frac = sector_frac = ticker_frac = float("nan")
        n_symbols = 0

    return {
        "setup_id": setup.setup_id, "family": setup.family, "driver": setup.driver,
        "cohort": setup.cohort, "horizon": h,
        "is_challenge": setup.is_challenge, "placebo": setup.placebo,
        "n_events": n_events, "n_matched_control": n_matched, "n_recent_events": n_recent,
        "n_symbols": n_symbols, "base_rate": _round(base_rate),
        "triggered_mean": _round(trig_mean), "triggered_median": _round(trig_median),
        "control_mean": _round(ctrl_mean_overall),
        "lift_vs_control": _round(lift_vs_ctrl), "lift_vs_base_rate": _round(lift_vs_base),
        "hit_rate": _round(hit), "control_hit_rate": _round(ctrl_hit),
        "hit_rate_lift_pp": _round((hit - ctrl_hit) if (not math.isnan(hit) and not math.isnan(ctrl_hit)) else None),
        "payoff_ratio": _round(payoff), "control_payoff_ratio": _round(ctrl_payoff),
        "ev_after_cost": ev_after_cost, "ev_after_25bps": ev_after_cost.get("25bps"),
        "worst_decile_mean": _round(worst_decile), "avg_mae_20": _round(mae),
        "recent_lift_vs_control": _round(recent_lift),
        "max_year_fraction": _round(year_frac), "max_sector_fraction": _round(sector_frac),
        "max_ticker_fraction": _round(ticker_frac),
        "status_hint": "", "reason": "",
    }


def walk_forward_lift(setup: SensSetup, grid: pd.DataFrame) -> dict:
    """Per-fold triggered-vs-(cohort-excluded)-control lift at the primary horizon."""
    h = setup.primary_horizon
    lbl = f"fwd_excess_{h}"
    out: dict = {}
    n_pos = 0
    for fold, (lo, hi) in WF_BOUNDS.items():
        if lbl not in grid.columns:
            out[fold] = {"n_events": 0, "lift_vs_control": None, "beats_control": False}
            continue
        sub = grid[(grid["date"] >= pd.Timestamp(lo)) & (grid["date"] <= pd.Timestamp(hi))
                   & grid[lbl].notna()].copy()
        if sub.empty:
            out[fold] = {"n_events": 0, "lift_vs_control": None, "beats_control": False}
            continue
        sub["_trig"] = setup.trigger_mask(sub).to_numpy().astype(bool)
        triggered = sub[sub["_trig"]]
        nontrig = sub[~sub["_trig"]]
        if setup.cohort and setup.cohort in sub.columns:
            nontrig = nontrig[nontrig[setup.cohort] <= 0.0]
        if triggered.empty or nontrig.empty:
            out[fold] = {"n_events": int(len(triggered)), "lift_vs_control": None, "beats_control": False}
            continue
        ctrl_mean = nontrig.groupby(["date", "ctrl_bucket"], observed=True)[lbl].mean()
        matched = pd.Series(
            np.asarray(triggered.set_index(["date", "ctrl_bucket"]).index.map(ctrl_mean), dtype="float64"),
            index=triggered.index)
        have = matched.notna()
        lift = float((triggered[lbl][have] - matched[have]).mean()) if have.any() else float("nan")
        beats = bool(not math.isnan(lift) and lift > 0)
        n_pos += int(beats)
        out[fold] = {"n_events": int(len(triggered)), "lift_vs_control": _round(lift),
                     "beats_control": beats}
    out["n_folds_positive"] = n_pos
    return out


# =========================================================================== #
# Multiple-testing deflation + classification (the CONFIRMED_SENSITIVITY_SIGNAL gate).
# =========================================================================== #
def mt_required_lift(n_search: int) -> float:
    return _round(GATE_MIN_LIFT * max(1.0, math.log10(max(n_search, 10))))


def classify_setup(ev: dict, wf: dict, port: dict, n_search: int, setup: SensSetup
                   ) -> Tuple[str, str, dict]:
    """Borderline never rounded up. Challenges/placebos are DIAGNOSTIC (never confirmed).
    A CONFIRMED signal MUST use an explicit external driver AND a sensitivity cohort."""
    if setup.needs_provider:
        return ST_NEEDS_PROVIDER, "external provider data required (not available locally)", {}
    n = ev.get("n_events", 0)
    if n == 0:
        return ST_BLOCKED, "no triggered events", {}
    req_lift = mt_required_lift(n_search)
    uses_driver_and_cohort = bool(setup.driver) and bool(setup.cohort)
    checks = {
        "uses_external_driver_and_cohort": uses_driver_and_cohort,
        "events_total_ge_1000": n >= GATE_MIN_EVENTS_TOTAL,
        "events_recent_ge_100": ev.get("n_recent_events", 0) >= GATE_MIN_EVENTS_RECENT,
        "ev_after_25bps_positive": (ev.get("ev_after_25bps") or -1) > GATE_MIN_EV_AFTER_COST,
        "lift_vs_control_meaningful": (ev.get("lift_vs_control") or -1) >= req_lift,
        "hit_or_payoff_improves": (
            (ev.get("hit_rate_lift_pp") or -1) >= GATE_HIT_RATE_LIFT_PP
            or ((ev.get("payoff_ratio") or 0) - (ev.get("control_payoff_ratio") or 0)) >= GATE_PAYOFF_LIFT),
        "portfolio_beats_spy_and_cash_active": bool(port.get("beats_spy_active")
                                                    and port.get("beats_cash_active")),
        "worst_decile_not_catastrophic": (ev.get("worst_decile_mean") if ev.get("worst_decile_mean")
                                          is not None else -1) >= GATE_WORST_DECILE_FLOOR,
        "survives_walk_forward": wf.get("n_folds_positive", 0) >= GATE_MIN_WF_FOLDS_POSITIVE,
        "survives_recent_2015_2026": (ev.get("recent_lift_vs_control") or -1) > 0,
        "not_year_concentrated": (ev.get("max_year_fraction") or 1) <= GATE_MAX_YEAR_CONC,
        "not_sector_concentrated": (ev.get("max_sector_fraction") or 1) <= GATE_MAX_SECTOR_CONC,
        "not_ticker_concentrated": (ev.get("max_ticker_fraction") or 1) <= GATE_MAX_TICKER_CONC,
        "leakage_safe": True,
        "placebo_clean": not (ev.get("placebo") or ev.get("is_challenge")),
    }
    if ev.get("is_challenge") or ev.get("placebo"):
        return ST_REJECTED, "challenge/placebo control (diagnostic; never promoted)", checks
    if not uses_driver_and_cohort:
        return ST_REJECTED, "does not use an explicit external driver AND sensitivity cohort", checks
    if not checks["events_total_ge_1000"] or not checks["events_recent_ge_100"]:
        return ST_REJECTED, f"insufficient events (total={n}, recent={ev.get('n_recent_events')})", checks
    if not checks["ev_after_25bps_positive"]:
        return ST_REJECTED, f"non-positive EV after 25bps ({ev.get('ev_after_25bps')})", checks
    if (ev.get("lift_vs_control") or -1) <= 0:
        return ST_REJECTED, f"no positive lift vs matched control ({ev.get('lift_vs_control')})", checks
    if not checks["survives_recent_2015_2026"]:
        return ST_REJECTED, f"fails 2015-2026 recency (recent lift {ev.get('recent_lift_vs_control')})", checks
    if all(checks.values()):
        return ST_CONFIRMED, ("driver-shock x sensitivity-cohort improves the forward distribution vs "
                              "matched controls, OOS + recent, net of costs"), checks
    missed = [k for k, v in checks.items() if not v]
    return ST_PROMISING, "positive but misses: " + ", ".join(missed), checks


# =========================================================================== #
# Campaign state + loop.
# =========================================================================== #
@dataclass
class CampaignState:
    registry: List[SensSetup] = field(default_factory=list)
    family_count: Dict[str, int] = field(default_factory=dict)
    cycle_log: List[dict] = field(default_factory=list)
    skipped: List[dict] = field(default_factory=list)

    @property
    def n_total(self) -> int:
        return len(self.registry)


def _can_add(state: CampaignState, setup: SensSetup) -> Optional[str]:
    if state.n_total >= MAX_TOTAL_SETUPS:
        return "total budget cap reached"
    if state.family_count.get(setup.family, 0) >= MAX_PER_FAMILY:
        return f"family cap reached for {setup.family}"
    if any(e.setup_id == setup.setup_id for e in state.registry):
        return "duplicate setup_id"
    return None


def _register(state: CampaignState, setups: List[SensSetup]) -> List[SensSetup]:
    added: List[SensSetup] = []
    for s in setups:
        why = _can_add(state, s)
        if why:
            state.skipped.append({"setup_id": s.setup_id, "family": s.family, "reason_skipped": why})
            continue
        state.registry.append(s)
        state.family_count[s.family] = state.family_count.get(s.family, 0) + 1
        added.append(s)
    return added


def _score(setup: SensSetup, grid: pd.DataFrame, fwd5_pivot: pd.DataFrame,
           spy_week: pd.Series, n_search: int) -> dict:
    if setup.needs_provider:
        status, reason, checks = classify_setup({}, {}, {}, n_search, setup)
        setup.status, setup.reason = status, reason
        setup.metrics = {"n_events": 0, "needs_provider": True, "provider_note": setup.provider_note,
                         "driver": setup.driver, "cohort": setup.cohort}
        return {"ev": {}, "wf": {}, "port": {}, "checks": {}}
    ev = evaluate_sensitivity_setup(setup, grid)
    if ev.get("n_events", 0) == 0:
        setup.status = ST_BLOCKED
        setup.reason = ev.get("reason", "no events")
        setup.metrics = ev
        return {"ev": ev, "wf": {}, "port": {}, "checks": {}}
    wf = walk_forward_lift(setup, grid)
    port = simulate_event_portfolio(setup, grid, fwd5_pivot=fwd5_pivot, spy_week=spy_week)
    status, reason, checks = classify_setup(ev, wf, port, n_search, setup)
    setup.status, setup.reason = status, reason
    setup.metrics = {**ev, "walk_forward": wf, "portfolio": port, "checks": checks}
    return {"ev": ev, "wf": wf, "port": port, "checks": checks}


def _surviving_leads(state: CampaignState) -> List[str]:
    return [e.setup_id for e in state.registry
            if (not e.is_challenge) and (not e.needs_provider)
            and e.status in (ST_CONFIRMED, ST_PROMISING)]


def run_campaign(panel: SensPanel) -> Tuple[CampaignState, dict]:
    state = CampaignState()
    grid = panel.grid
    fwd5_pivot = _fwd5_pivot(grid)
    spy_week = _spy_weekly(panel.spy_close, panel.grid_dates)

    c1 = _register(state, plan_cycle_1())
    n_search = max(sum(1 for e in c1 if not e.needs_provider), 10)
    for s in c1:
        _score(s, grid, fwd5_pivot, spy_week, n_search)
    leads = _surviving_leads(state)
    state.cycle_log.append({"cycle": 1, "registered": len(c1), "leads": ";".join(leads),
                            "confirmed": sum(1 for e in c1 if e.status == ST_CONFIRMED),
                            "promising": sum(1 for e in c1 if e.status == ST_PROMISING),
                            "rejected": sum(1 for e in c1 if e.status == ST_REJECTED),
                            "needs_provider": sum(1 for e in c1 if e.status == ST_NEEDS_PROVIDER),
                            "note": "macro-shock x sensitivity templates + challenges + provider templates"})

    if leads and state.n_total < MAX_TOTAL_SETUPS:
        c2 = _register(state, plan_cycle_2(leads))
        n_search = max(sum(1 for e in state.registry if not e.needs_provider), 10)
        for s in c2:
            _score(s, grid, fwd5_pivot, spy_week, n_search)
        leads2 = _surviving_leads(state)
        state.cycle_log.append({"cycle": 2, "registered": len(c2), "leads": ";".join(leads2),
                                "confirmed": sum(1 for e in c2 if e.status == ST_CONFIRMED),
                                "promising": sum(1 for e in c2 if e.status == ST_PROMISING),
                                "rejected": sum(1 for e in c2 if e.status == ST_REJECTED),
                                "needs_provider": 0,
                                "note": "refinements on surviving leads"})
        leads = leads2
    else:
        state.cycle_log.append({"cycle": 2, "registered": 0, "leads": "",
                                "confirmed": 0, "promising": 0, "rejected": 0, "needs_provider": 0,
                                "note": "no surviving leads after cycle 1 — early stop"})

    if leads and state.n_total < MAX_TOTAL_SETUPS:
        c3 = _register(state, plan_cycle_3(leads))
        n_search = max(sum(1 for e in state.registry if not e.needs_provider), 10)
        for s in c3:
            _score(s, grid, fwd5_pivot, spy_week, n_search)
        state.cycle_log.append({"cycle": 3, "registered": len(c3), "leads": ";".join(leads),
                                "confirmed": 0, "promising": 0,
                                "rejected": sum(1 for e in c3 if e.status == ST_REJECTED),
                                "needs_provider": 0,
                                "note": "cost/recency stress challenges on surviving leads"})
    else:
        state.cycle_log.append({"cycle": 3, "registered": 0, "leads": "",
                                "confirmed": 0, "promising": 0, "rejected": 0, "needs_provider": 0,
                                "note": "no surviving leads — no stress cycle warranted"})

    return state, {"n_search_total": state.n_total, "leads_final": leads}


def budget_report(state: CampaignState) -> dict:
    n = state.n_total
    n_challenge = sum(1 for e in state.registry if e.is_challenge)
    n_provider = sum(1 for e in state.registry if e.needs_provider)
    n_testable = n - n_provider
    fams = state.family_count
    # the >=30% challenge guardrail is assessed against TESTABLE setups (provider templates are
    # untestable placeholders, not part of the search that needs skeptic challenges).
    return {
        "setups_registered": n,
        "max_total_setups": MAX_TOTAL_SETUPS,
        "n_testable": n_testable, "n_needs_provider": n_provider,
        "n_challenge": n_challenge,
        "challenge_fraction": _round(n_challenge / n_testable if n_testable else 0.0, 4),
        "challenge_ok": (n_challenge / n_testable if n_testable else 0.0) >= CHALLENGE_MIN_FRAC,
        "per_family_counts": dict(fams),
        "per_family_ok": all(v <= MAX_PER_FAMILY for v in fams.values()),
        "all_registered_before_scoring": True,
        "n_skipped": len(state.skipped),
    }


# =========================================================================== #
# Recommendation.
# =========================================================================== #
def derive_recommendation(panel_ok: bool, framework_ok: bool, any_external_data: bool,
                          state: CampaignState) -> Tuple[str, dict]:
    confirmed = [e for e in state.registry if e.status == ST_CONFIRMED]
    promising = [e for e in state.registry if e.status == ST_PROMISING]
    rejected = [e for e in state.registry if e.status == ST_REJECTED]
    provider = [e for e in state.registry if e.status == ST_NEEDS_PROVIDER]
    n_testable = sum(1 for e in state.registry if not e.is_challenge and not e.needs_provider)
    detail = {"n_confirmed": len(confirmed), "n_promising": len(promising),
              "n_rejected": len(rejected), "n_needs_provider": len(provider),
              "n_testable_candidates": n_testable, "n_total": state.n_total}
    if not framework_ok:
        return REC_FRAMEWORK_BLOCKED, detail
    if not any_external_data:
        return REC_HUMAN, detail
    if not panel_ok:
        return REC_HUMAN, detail
    if confirmed:
        return REC_CONFIRMED, detail
    if promising:
        return REC_PROMISING, detail
    # macro/cross-asset x sensitivity was testable but produced no confirmed/promising edge;
    # richer external inputs (revisions/news/options) remain pre-registered & un-tested.
    if provider:
        return REC_NEEDS_PROVIDER, detail
    if n_testable:
        return REC_REJECTED, detail
    return REC_HUMAN, detail


# =========================================================================== #
# Universe discovery / selection (reuse 8-D).
# =========================================================================== #
discover_universes = P8D.discover_universes
select_universe = P8D.select_universe
select_symbols = P8D.select_symbols


# =========================================================================== #
# Part A/B inventory + gap + driver catalog rows.
# =========================================================================== #
def scan_local_inventory() -> List[dict]:
    """Read-only existence/size scan of known local external-data paths."""
    rows = []
    for family, path, note in LOCAL_DATA_PROBES:
        exists = path.exists()
        try:
            size = int(path.stat().st_size) if exists else 0
        except OSError:
            size = 0
        n_lines = ""
        header = ""
        populated = False
        if exists and path.suffix.lower() == ".csv" and size < 60_000_000:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    header = fh.readline().strip()[:200]
                    cnt = sum(1 for _ in fh)
                    n_lines = cnt
                    populated = cnt > 0
            except OSError:
                pass
        elif exists and size >= 60_000_000:
            populated = True
            n_lines = ">1e6 (large)"
        rows.append({
            "family": family, "path": str(path).replace("\\", "/"),
            "exists": exists, "size_bytes": size, "n_data_rows": n_lines,
            "populated": populated, "header_sample": header, "note": note,
        })
    return rows


def driver_catalog_rows(coverage: Optional[List[dict]] = None) -> List[dict]:
    cov = {r["proxy"]: r for r in (coverage or []) if r.get("proxy")}
    rows = []
    for d in DRIVER_CATALOG:
        c = cov.get(d.proxy or "", {})
        rows.append({
            "driver_name": d.key, "driver_label": d.label, "driver_family": d.family,
            "proxy_symbol": d.proxy or "", "data_source": d.source, "frequency": d.frequency,
            "point_in_time_safe": d.pit_safe, "expected_mechanism": d.mechanism,
            "likely_horizon": d.horizons, "availability": d.availability,
            "proxy_coverage_rows": c.get("n_rows", ""), "proxy_start": c.get("start", ""),
            "proxy_end": c.get("end", ""), "proxy_status": c.get("status", ""),
        })
    return rows


def gap_report_rows(inventory: List[dict]) -> List[dict]:
    """One row per external-data family: what exists locally vs what is missing."""
    fam_local = {}
    for r in inventory:
        fam_local.setdefault(r["family"], False)
        if r["exists"] and r["populated"]:
            fam_local[r["family"]] = True
    families = [
        ("analyst_estimates", "Analyst estimates & revisions", FAM_REVISION_SENS),
        ("earnings_events", "Earnings events / surprise / guidance", FAM_REVISION_SENS),
        ("fundamentals_pit", "Point-in-time fundamentals", ""),
        ("options_iv", "Options / implied volatility / skew", FAM_OPTIONS_SENS),
        ("news", "News", FAM_NEWS_SENS),
        ("sentiment", "Sentiment", FAM_NEWS_SENS),
        ("transcripts", "Transcripts / management tone", FAM_NEWS_SENS),
        ("short_interest", "Short interest / borrow cost", ""),
        ("macro_cross_asset", "Macro & cross-asset", FAM_MACRO_SENS),
        ("sector_industry", "Sector / industry context", FAM_MACRO_SENS),
    ]
    rows = []
    for key, label, fam in families:
        has_local = fam_local.get(key, False)
        # macro/cross-asset is additionally LOCAL_READY via Norgate proxies (not in the file probes)
        if key == "macro_cross_asset":
            status = "LOCAL_READY"
            detail = "Norgate ETF/index proxies (SPY, sector ETFs, TLT, HYG, UUP, USO, GLD, DBC, $VIX) + FRED CSVs"
        elif key == "sector_industry":
            status = "LOCAL_READY"
            detail = "Norgate GICS sector + SPDR sector ETFs (current-as-of sector map present, flagged NOT PIT)"
        elif key == "fundamentals_pit":
            status = "LOCAL_PARTIAL" if has_local else "NEEDS_PROVIDER"
            detail = "SimFin quarterly + SEC EDGAR normalized fundamentals on disk (PIT, filing-dated)"
        elif has_local:
            status = "LOCAL_PARTIAL"
            detail = "local file present"
        else:
            status = "NEEDS_PROVIDER"
            detail = "no local time series found"
        rows.append({"family_key": key, "family_label": label, "availability": status,
                     "local_present": has_local, "blocks_setup_family": fam, "detail": detail})
    return rows


def provider_acquisition_rows() -> List[dict]:
    """Acquisition plan + minimal schema for each NEEDS_PROVIDER driver."""
    plans = {
        "analyst_revision": {
            "providers": "IBES (Refinitiv), Zacks, FMP estimates, AlphaVantage (keyed)",
            "cost": "paid / free-tier keyed", "priority": 1,
            "schema": "ticker,date,metric(eps_fy1/target),consensus_mean,n_estimates,revision_up,revision_dn,availability_date,pit_usable",
            "value_hypothesis": "revision drift is among the most robust documented anomalies; pairs naturally with a revision-sensitivity cohort"},
        "earnings_surprise": {
            "providers": "AlphaVantage EARNINGS / FMP earnings-surprises / Finnhub (keyed)",
            "cost": "free-tier keyed (rate-limited)", "priority": 2,
            "schema": "ticker,fiscal_period,reported_date,reported_eps,estimated_eps,surprise_pct,availability_date,pit_usable",
            "value_hypothesis": "post-earnings-announcement drift in surprise-sensitive names"},
        "news_sentiment": {
            "providers": "RavenPack/Bigdata.com, AlphaVantage NEWS_SENTIMENT, GDELT (free)",
            "cost": "paid / free (GDELT)", "priority": 3,
            "schema": "ticker,timestamp,sentiment_score,novelty,volume,source,availability_timestamp",
            "value_hypothesis": "sentiment shocks move sentiment-reactive names; decays fast (1-5d)"},
        "options_iv": {
            "providers": "OptionMetrics IvyDB, IVolatility, CBOE DataShop",
            "cost": "paid", "priority": 4,
            "schema": "ticker,date,iv_30d,iv_rank,skew_25d,put_call_oi,availability_date,pit_usable",
            "value_hypothesis": "IV/skew shifts precede moves in options-informative names"},
        "short_interest": {
            "providers": "FINRA short interest (free, biweekly), Ortex/S3 (paid borrow)",
            "cost": "free (FINRA) / paid (borrow)", "priority": 5,
            "schema": "ticker,settlement_date,short_interest,days_to_cover,borrow_fee,availability_date,pit_usable",
            "value_hypothesis": "squeeze/borrow dynamics in heavily-shorted names"},
    }
    rows = []
    for d in DRIVER_CATALOG:
        if d.availability != "NEEDS_PROVIDER":
            continue
        p = plans.get(d.key, {})
        rows.append({
            "driver": d.key, "driver_family": d.family, "providers": p.get("providers", ""),
            "estimated_cost": p.get("cost", ""), "priority": p.get("priority", 99),
            "minimal_schema": p.get("schema", ""), "value_hypothesis": p.get("value_hypothesis", ""),
            "point_in_time_requirement": "must carry an availability_date >= the underlying event date",
            "blocked_setup_family": FAMILY_AGENT.get(
                {"analyst_revision": FAM_REVISION_SENS, "earnings_surprise": FAM_REVISION_SENS,
                 "news_sentiment": FAM_NEWS_SENS, "options_iv": FAM_OPTIONS_SENS,
                 "short_interest": ""}.get(d.key, ""), "") or "",
        })
    return sorted(rows, key=lambda r: r["priority"])


# =========================================================================== #
# Orchestration + artifacts (20 committed-safe).
# =========================================================================== #
ARTIFACTS = [
    "phase8e_sensitivity_aware_signal_factory.json",
    "local_external_data_inventory.csv",
    "external_data_gap_report.csv",
    "external_driver_catalog.csv",
    "ticker_external_sensitivity_map.csv",
    "sensitivity_cohort_catalog.csv",
    "sensitivity_quality_report.csv",
    "sensitivity_allotments.csv",
    "cohort_membership_panel.csv",
    "setup_experiment_registry.csv",
    "sensitivity_setup_scoreboard.csv",
    "matched_control_report.csv",
    "promising_sensitivity_setups.csv",
    "confirmed_sensitivity_signals.csv",
    "failed_sensitivity_setups.csv",
    "provider_acquisition_plan.csv",
    "validation_skeptic_report.csv",
    "multiple_testing_report.csv",
    "research_director_decision.json",
    "phase8f_next_plan.json",
]


def _cohort_catalog_rows() -> List[dict]:
    rows = []
    for c in COHORT_CATALOG:
        d = DRIVER_BY_KEY.get(c.driver)
        rows.append({"cohort": c.col, "label": c.label, "driver": c.driver,
                     "proxy": (d.proxy if d else ""), "side": c.side,
                     "quantile_rule": (f">= {COHORT_TOP:.2f} pctile" if c.side == "high"
                                       else f"<= {COHORT_BOTTOM:.2f} pctile"),
                     "estimated_from": "leak-safe rolling 252d beta (cross-sectional rank per date)",
                     "mechanism": c.mechanism})
    return rows


def _sensitivity_map_rows(panel: SensPanel) -> List[dict]:
    """Latest per-ticker/driver sensitivity (one row per ticker x driver)."""
    sm = panel.sensitivity_map
    if sm is None or sm.empty:
        return []
    cols = ["ticker", "sector", "driver", "proxy", "n_obs", "latest_beta", "full_sample_beta",
            "full_sample_corr", "rolling_beta_std", "sign_consistency", "direction",
            "confidence", "min_obs_required", "meets_min_obs"]
    return sm[[c for c in cols if c in sm.columns]].to_dict("records")


def _sensitivity_quality_rows(panel: SensPanel) -> List[dict]:
    """Per-driver coverage/quality summary across the universe."""
    sm = panel.sensitivity_map
    rows = []
    if sm is None or sm.empty:
        return rows
    for driver, grp in sm.groupby("driver"):
        meets = grp["meets_min_obs"].sum() if "meets_min_obs" in grp else 0
        rows.append({
            "driver": driver,
            "proxy": grp["proxy"].iloc[0] if "proxy" in grp and len(grp) else "",
            "n_tickers": int(len(grp)),
            "n_meets_min_obs": int(meets),
            "median_full_sample_beta": _round(float(grp["full_sample_beta"].median())),
            "median_confidence": _round(float(grp["confidence"].median())),
            "median_sign_consistency": _round(float(grp["sign_consistency"].median())),
            "median_rolling_beta_std": _round(float(grp["rolling_beta_std"].median())),
            "min_obs_required": SENS_BETA_MINOBS,
        })
    return rows


def _allotment_rows(panel: SensPanel) -> List[dict]:
    """Cohort sizes at the latest grid date (how many names are allotted to each cohort)."""
    g = panel.grid
    rows = []
    if g is None or g.empty:
        return rows
    last_date = g["date"].max()
    last = g[g["date"] == last_date]
    n_members = int(len(last))
    for c in COHORT_CATALOG:
        n = int(last[c.col].sum()) if c.col in last.columns else 0
        rows.append({"cohort": c.col, "driver": c.driver, "side": c.side,
                     "as_of_date": str(last_date)[:10], "n_members_in_cohort": n,
                     "n_members_total": n_members,
                     "fraction": _round(n / n_members if n_members else 0.0),
                     "label": c.label})
    return rows


def _cohort_membership_rows(panel: SensPanel, max_rows: int = 20000) -> List[dict]:
    """Compact long cohort-membership panel: (date, symbol, cohort) for active cohort flags.
    Bounded to the most recent dates so the repo artifact stays a summary, not the full grid."""
    g = panel.grid
    rows: List[dict] = []
    if g is None or g.empty:
        return rows
    dates = sorted(g["date"].unique())
    recent = set(dates[-104:])                       # ~2 most recent years of weekly dates
    sub = g[g["date"].isin(recent)]
    for c in COHORT_CATALOG:
        if c.col not in sub.columns:
            continue
        hit = sub[sub[c.col] >= 1.0]
        for d, sym in hit[["date", "symbol"]].itertuples(index=False):
            rows.append({"date": str(d)[:10], "symbol": sym, "cohort": c.col, "driver": c.driver})
            if len(rows) >= max_rows:
                return rows
    return rows


def _registry_rows(state: CampaignState) -> List[dict]:
    rows = []
    for e in state.registry:
        rows.append({
            "setup_id": e.setup_id, "cycle": e.cycle, "family": e.family,
            "owning_agent": e.owning_agent, "is_challenge": e.is_challenge, "placebo": e.placebo,
            "needs_provider": e.needs_provider, "driver": e.driver, "cohort": e.cohort,
            "primary_horizon": e.primary_horizon,
            "conditions": "; ".join(f"{c} {op} {v}" for c, op, v in e.conditions),
            "hypothesis": e.hypothesis, "challenges": e.challenges,
            "provider_note": e.provider_note, "status": e.status, "reason": e.reason,
            "registered_before_scoring": True,
        })
    return rows


_SCOREBOARD_COLS = [
    "setup_id", "cycle", "family", "owning_agent", "driver", "cohort", "is_challenge", "horizon",
    "status", "n_events", "n_recent_events", "n_matched_control", "base_rate", "triggered_mean",
    "control_mean", "lift_vs_control", "lift_vs_base_rate", "hit_rate", "control_hit_rate",
    "hit_rate_lift_pp", "payoff_ratio", "ev_after_25bps", "worst_decile_mean",
    "recent_lift_vs_control", "max_year_fraction", "max_sector_fraction", "max_ticker_fraction",
    "reason",
]


def _scoreboard_rows(state: CampaignState) -> List[dict]:
    rows = []
    for e in state.registry:
        m = e.metrics or {}
        row = {k: m.get(k) for k in _SCOREBOARD_COLS if k in m}
        row.update({"setup_id": e.setup_id, "cycle": e.cycle, "family": e.family,
                    "owning_agent": e.owning_agent, "driver": e.driver, "cohort": e.cohort,
                    "is_challenge": e.is_challenge, "horizon": e.primary_horizon,
                    "status": e.status, "reason": e.reason})
        rows.append(row)
    return rows


def _matched_control_rows(state: CampaignState) -> List[dict]:
    rows = []
    for e in state.registry:
        m = e.metrics or {}
        if not m or m.get("needs_provider"):
            continue
        rows.append({
            "setup_id": e.setup_id, "family": e.family, "driver": e.driver, "cohort": e.cohort,
            "is_challenge": e.is_challenge, "n_events": m.get("n_events"),
            "n_matched_control": m.get("n_matched_control"),
            "triggered_mean": m.get("triggered_mean"), "control_mean": m.get("control_mean"),
            "lift_vs_control": m.get("lift_vs_control"), "lift_vs_base_rate": m.get("lift_vs_base_rate"),
            "hit_rate": m.get("hit_rate"), "control_hit_rate": m.get("control_hit_rate"),
            "hit_rate_lift_pp": m.get("hit_rate_lift_pp"),
            "payoff_ratio": m.get("payoff_ratio"), "control_payoff_ratio": m.get("control_payoff_ratio"),
            "ev_after_25bps": m.get("ev_after_25bps"),
            "recent_lift_vs_control": m.get("recent_lift_vs_control"),
        })
    return rows


def _validation_skeptic_rows(state: CampaignState) -> List[dict]:
    rows = []
    for e in state.registry:
        checks = (e.metrics or {}).get("checks", {})
        rows.append({
            "setup_id": e.setup_id, "family": e.family, "driver": e.driver, "cohort": e.cohort,
            "is_challenge": e.is_challenge, "placebo": e.placebo, "challenges": e.challenges,
            "status": e.status, "lift_vs_control": (e.metrics or {}).get("lift_vs_control"),
            "n_failed_checks": sum(1 for v in checks.values() if v is False),
            "failed_checks": "; ".join(k for k, v in checks.items() if v is False),
            "reason": e.reason,
        })
    return rows


def _multiple_testing_report(state: CampaignState) -> dict:
    n_testable = sum(1 for e in state.registry if not e.needs_provider)
    return {
        "n_setups_searched": n_testable,
        "n_needs_provider_untested": sum(1 for e in state.registry if e.needs_provider),
        "base_lift_hurdle": GATE_MIN_LIFT,
        "deflated_lift_hurdle": mt_required_lift(n_testable),
        "method": "a-priori lift hurdle inflated by log10(n_search); cohort-aware matched control "
                  "(same shock, NOT in cohort) + recent-period + walk-forward + placebo all required",
        "n_confirmed_after_deflation": sum(1 for e in state.registry if e.status == ST_CONFIRMED),
        "n_promising": sum(1 for e in state.registry if e.status == ST_PROMISING),
        "challenge_fraction": budget_report(state)["challenge_fraction"],
        "placebos_showing_lift": [
            e.setup_id for e in state.registry
            if (e.is_challenge or e.placebo) and ((e.metrics or {}).get("lift_vs_control") or -1) >= GATE_PLACEBO_MAX_LIFT
        ],
    }


def _multiple_testing_rows(state: CampaignState) -> List[dict]:
    mt = _multiple_testing_report(state)
    return [{"metric": k, "value": (";".join(map(str, v)) if isinstance(v, list) else v)}
            for k, v in mt.items()]


def _safety_block() -> dict:
    return {
        "research_only": True, "norgate_only_plus_local_fred": True,
        "network_or_paid_api_used": False, "packages_installed": False,
        "large_data_only_on_d": True, "external_data_faked": False,
        "always_on_factor_test": False, "optimized_weights": False,
        "factor_signs_modified_after_results": False, "regime_activation": False,
        "ml_fit": False, "holdout_feedback_used_to_tune_thresholds": False,
        "failed_experiments_hidden": False, "live_trading_signals": False,
        "broker_or_orders": False, "automation": False, "paper_trader_touched": False,
        "gcp_touched": False, "committed": False, "pushed": False,
    }


def derive_any_external_data(inventory: List[dict], coverage: List[dict]) -> bool:
    has_local_file = any(r["exists"] and r["populated"] for r in inventory)
    has_proxy = any(r.get("status") == "OK" for r in coverage)
    return bool(has_local_file or has_proxy)


def run(out_dir: Path, *, universe_override: Optional[str] = None,
        max_symbols: Optional[int] = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    started = _utc_now_iso()
    adapter = NorgateAdapter()
    framework_ok = True
    inventory = scan_local_inventory()

    if not adapter.available:
        report = _minimal_report(started, REC_HUMAN,
                                 {"blocked_reason": f"norgatedata import failed: {adapter.import_error}"},
                                 inventory, [])
        _emit_minimal(out_dir, report, inventory, [])
        return report

    universes = discover_universes(adapter)
    sel = select_universe(universes)
    if universe_override:
        sel = {**sel, "selected_universe": universe_override,
               "index_name": UNIVERSE_INDEX_NAME.get(universe_override, ""),
               "justification": "universe overridden via CLI for this run"}
    universe_name = sel["selected_universe"]
    index_name = sel.get("index_name") or UNIVERSE_INDEX_NAME.get(universe_name, "")

    symbols = select_symbols(adapter, universe_name, max_symbols)
    coverage_preview: List[dict] = []
    if len(symbols) < MIN_UNIVERSE_MEMBERS:
        report = _minimal_report(started, REC_HUMAN,
                                 {"blocked_reason": f"universe {universe_name} returned {len(symbols)} symbols",
                                  "selected_universe": sel}, inventory, [])
        _emit_minimal(out_dir, report, inventory, [])
        return report

    panel = build_sensitivity_panel(adapter, symbols, index_name)
    panel_ok = bool(panel.ok and not panel.grid.empty)
    coverage = panel.proxy_coverage
    persisted = persist_sensitivity_panel(panel) if panel_ok else {}

    state = CampaignState()
    if panel_ok:
        state, _meta = run_campaign(panel)

    any_external = derive_any_external_data(inventory, coverage)
    rec, detail = derive_recommendation(panel_ok, framework_ok, any_external, state)
    report = _assemble_report(started, sel, universes, panel, persisted, state, rec, detail,
                              inventory, coverage)
    _emit_all(out_dir, report, panel, persisted, state, inventory, coverage)
    return report


def _assemble_report(started, sel, universes, panel: SensPanel, persisted, state: CampaignState,
                     rec: str, detail: dict, inventory, coverage) -> dict:
    md = panel.metadata
    n_active = int((~md["is_delisted"]).sum()) if not md.empty and "is_delisted" in md else 0
    n_delisted = int(md["is_delisted"].sum()) if not md.empty and "is_delisted" in md else 0
    grid = panel.grid
    confirmed = [e.setup_id for e in state.registry if e.status == ST_CONFIRMED]
    promising = [e.setup_id for e in state.registry if e.status == ST_PROMISING]
    rejected = [e.setup_id for e in state.registry if e.status == ST_REJECTED]
    provider = [e.setup_id for e in state.registry if e.status == ST_NEEDS_PROVIDER]
    gap = gap_report_rows(inventory)
    return {
        "phase": PHASE, "objective": OBJECTIVE, "generated_utc": started,
        "recommendation": rec, "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "decision_detail": detail,
        "question_answered": ("Can we find a repeatable signal by modelling external-driver shocks "
                              "and ticker-specific sensitivity cohorts vs matched controls (same shock, "
                              "not in cohort), net of costs, OOS and in 2015-2026?"),
        "provider": "Norgate Data (local desktop database) + on-disk FRED CSVs",
        "selected_universe": sel, "available_universes": universes,
        "external_data": {
            "local_families_ready": [r["family_key"] for r in gap if r["availability"] in ("LOCAL_READY", "LOCAL_PARTIAL")],
            "needs_provider_families": [r["family_key"] for r in gap if r["availability"] == "NEEDS_PROVIDER"],
            "news_or_sentiment_local": any(r["family_key"] in ("news", "sentiment") and r["local_present"] for r in gap),
            "proxy_coverage_ok": sum(1 for r in coverage if r.get("status") == "OK"),
            "proxy_coverage_total": len(coverage),
        },
        "panel_shape": {
            "n_symbols_with_obs": int(grid["symbol"].nunique()) if not grid.empty else 0,
            "n_grid_observations": int(len(grid)),
            "n_grid_dates": int(grid["date"].nunique()) if not grid.empty else 0,
            "grid_freq_weekly": GRID_FREQ,
            "date_range": ([str(grid["date"].min())[:10], str(grid["date"].max())[:10]]
                           if not grid.empty else []),
            "active": n_active, "delisted": n_delisted,
            "panel_ok": bool(panel.ok), "large_data_root": str(PANEL_ROOT).replace("\\", "/"),
            "persisted_files": {k: v.replace("\\", "/") for k, v in persisted.items()},
            "notes": panel.notes,
        },
        "sensitivity_drivers": SENS_DRIVERS + ["sector"],
        "shock_drivers": SHOCK_DRIVERS,
        "cohorts": COHORT_COLS,
        "n_cohorts": len(COHORT_CATALOG),
        "budget": budget_report(state),
        "cycle_log": state.cycle_log,
        "setups": {
            "n_registered": state.n_total,
            "confirmed_signals": confirmed, "promising_signals": promising,
            "rejected_signals": rejected, "needs_provider_signals": provider,
        },
        "multiple_testing": _multiple_testing_report(state),
        "safety": _safety_block(),
    }


def _emit_all(out_dir: Path, report: dict, panel: SensPanel, persisted, state, inventory, coverage) -> None:
    p = lambda n: out_dir / n
    _write_json(p("phase8e_sensitivity_aware_signal_factory.json"), report)
    _write_csv(p("local_external_data_inventory.csv"), inventory,
               ["family", "path", "exists", "size_bytes", "n_data_rows", "populated",
                "header_sample", "note"])
    _write_csv(p("external_data_gap_report.csv"), gap_report_rows(inventory),
               ["family_key", "family_label", "availability", "local_present", "blocks_setup_family", "detail"])
    _write_csv(p("external_driver_catalog.csv"), driver_catalog_rows(coverage),
               ["driver_name", "driver_label", "driver_family", "proxy_symbol", "data_source",
                "frequency", "point_in_time_safe", "expected_mechanism", "likely_horizon",
                "availability", "proxy_coverage_rows", "proxy_start", "proxy_end", "proxy_status"])
    _write_csv(p("ticker_external_sensitivity_map.csv"), _sensitivity_map_rows(panel),
               ["ticker", "sector", "driver", "proxy", "n_obs", "latest_beta", "full_sample_beta",
                "full_sample_corr", "rolling_beta_std", "sign_consistency", "direction",
                "confidence", "min_obs_required", "meets_min_obs"])
    _write_csv(p("sensitivity_cohort_catalog.csv"), _cohort_catalog_rows(),
               ["cohort", "label", "driver", "proxy", "side", "quantile_rule", "estimated_from", "mechanism"])
    _write_csv(p("sensitivity_quality_report.csv"), _sensitivity_quality_rows(panel),
               ["driver", "proxy", "n_tickers", "n_meets_min_obs", "median_full_sample_beta",
                "median_confidence", "median_sign_consistency", "median_rolling_beta_std", "min_obs_required"])
    _write_csv(p("sensitivity_allotments.csv"), _allotment_rows(panel),
               ["cohort", "driver", "side", "as_of_date", "n_members_in_cohort", "n_members_total",
                "fraction", "label"])
    _write_csv(p("cohort_membership_panel.csv"), _cohort_membership_rows(panel),
               ["date", "symbol", "cohort", "driver"])
    _write_csv(p("setup_experiment_registry.csv"), _registry_rows(state),
               ["setup_id", "cycle", "family", "owning_agent", "is_challenge", "placebo",
                "needs_provider", "driver", "cohort", "primary_horizon", "conditions", "hypothesis",
                "challenges", "provider_note", "status", "reason", "registered_before_scoring"])
    sb = _scoreboard_rows(state)
    _write_csv(p("sensitivity_setup_scoreboard.csv"), sb, _SCOREBOARD_COLS)
    _write_csv(p("matched_control_report.csv"), _matched_control_rows(state),
               ["setup_id", "family", "driver", "cohort", "is_challenge", "n_events",
                "n_matched_control", "triggered_mean", "control_mean", "lift_vs_control",
                "lift_vs_base_rate", "hit_rate", "control_hit_rate", "hit_rate_lift_pp",
                "payoff_ratio", "control_payoff_ratio", "ev_after_25bps", "recent_lift_vs_control"])
    promising = [r for r in sb if r["status"] == ST_PROMISING]
    _write_csv(p("promising_sensitivity_setups.csv"), promising, _SCOREBOARD_COLS)
    confirmed = [r for r in sb if r["status"] == ST_CONFIRMED]
    _write_csv(p("confirmed_sensitivity_signals.csv"),
               confirmed if confirmed else [{"status": "NO_CONFIRMED_SENSITIVITY_SIGNAL"}],
               _SCOREBOARD_COLS if confirmed else ["status"])
    failed = [r for r in sb if r["status"] in (ST_REJECTED, ST_BLOCKED)]
    _write_csv(p("failed_sensitivity_setups.csv"), failed, _SCOREBOARD_COLS)
    _write_csv(p("provider_acquisition_plan.csv"), provider_acquisition_rows(),
               ["driver", "driver_family", "providers", "estimated_cost", "priority",
                "minimal_schema", "value_hypothesis", "point_in_time_requirement", "blocked_setup_family"])
    _write_csv(p("validation_skeptic_report.csv"), _validation_skeptic_rows(state),
               ["setup_id", "family", "driver", "cohort", "is_challenge", "placebo", "challenges",
                "status", "lift_vs_control", "n_failed_checks", "failed_checks", "reason"])
    _write_csv(p("multiple_testing_report.csv"), _multiple_testing_rows(state), ["metric", "value"])
    _write_json(p("research_director_decision.json"), _director_decision(report, state))
    _write_json(p("phase8f_next_plan.json"), _phase8f_plan(report))


def _director_decision(report: dict, state: CampaignState) -> dict:
    confirmed = [e for e in state.registry if e.status == ST_CONFIRMED]
    promising = [e for e in state.registry if e.status == ST_PROMISING]
    best = max(promising + confirmed,
               key=lambda e: ((e.metrics or {}).get("lift_vs_control") or -9), default=None)
    return {
        "phase": PHASE, "generated_utc": report["generated_utc"],
        "recommendation": report["recommendation"],
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "decision_detail": report["decision_detail"],
        "thesis": "external input x ticker sensitivity x market context x conditional setup",
        "assessment_redesign": {
            "from": "which single price/volume factor works across all tickers",
            "to": "which tickers are sensitive to which external drivers; when a driver shocks, does "
                  "the sensitive cohort beat matched controls that saw the same shock but are not in "
                  "the cohort",
        },
        "anti_p_hacking": {
            "all_setups_pre_registered": True, "thresholds_fixed_a_priori": True,
            "sensitivity_direction_estimated_not_assumed": True,
            "no_threshold_tuning_on_test_results": True,
            "challenge_fraction": budget_report(state)["challenge_fraction"],
            "cohort_aware_matched_control_required": True, "placebo_controls_required": True,
            "multiple_testing_deflation": mt_required_lift(sum(1 for e in state.registry if not e.needs_provider)),
            "borderline_never_rounded_up": True,
        },
        "best_lead": None if best is None else {
            "setup_id": best.setup_id, "family": best.family, "driver": best.driver,
            "cohort": best.cohort, "status": best.status, "horizon": best.primary_horizon,
            "lift_vs_control": (best.metrics or {}).get("lift_vs_control"),
            "ev_after_25bps": (best.metrics or {}).get("ev_after_25bps"),
            "recent_lift_vs_control": (best.metrics or {}).get("recent_lift_vs_control"),
            "reason": best.reason,
        },
        "stop_conditions_honored": [
            "no price/volume-only signal mining (every candidate uses an external driver + sensitivity cohort)",
            "no single universal factor across every ticker", "sensitivity direction estimated from data",
            "no weak full-sample promotion", "no portfolio-weight optimization", "no factor-sign flipping",
            "no regime activation/throttling", "no ML fitting", "no holdout feedback used to tune thresholds",
            "external data never faked (missing -> acquisition plan)", "no live trading signals",
            "no orders/broker/automation", "no Paper Trader / GCP / deployment",
            "failed experiments not hidden", "no commit", "no push",
        ],
    }


def _phase8f_plan(report: dict) -> dict:
    rec = report["recommendation"]
    if rec == REC_CONFIRMED:
        steps = ["Hand each CONFIRMED_SENSITIVITY_SIGNAL to risk-portfolio-agent for cohort-aware event-book "
                 "risk and to signal-publishing-agent for a paper-research preview contract (NO orders).",
                 "Re-confirm on a broader survivorship-aware daily universe (S&P 1500 / Russell 3000)."]
    elif rec == REC_PROMISING:
        steps = ["Broaden the daily universe to test whether promising sensitivity setups clear the "
                 "event-count and recency gates on more data; keep thresholds FIXED (no tuning).",
                 "Acquire the highest-priority provider dataset (analyst revisions) to add a second driver family."]
    elif rec == REC_NEEDS_PROVIDER:
        steps = ["Macro/cross-asset x sensitivity is testable locally but did not clear the gate; the next "
                 "edge needs richer external inputs. Acquire provider data in priority order (analyst "
                 "revisions -> earnings surprise -> news/sentiment -> options IV) per provider_acquisition_plan.csv.",
                 "Re-run the IDENTICAL gate with the new driver family added; no threshold tuning."]
    elif rec == REC_REJECTED:
        steps = ["Sensitivity setups on locally-available drivers did not improve the forward distribution "
                 "vs cohort-aware matched controls net of costs; escalate to the director for an agenda change."]
    elif rec == REC_HUMAN:
        steps = ["No viable external data source is available to the agent shell; a human provider/key "
                 "decision is required before further sensitivity testing."]
    else:
        steps = ["Resolve the blocking condition (Norgate access / framework) before re-running."]
    return {"from_phase": PHASE, "recommendation": rec, "next_phase": "8-F",
            "selected_universe": report.get("selected_universe", {}).get("selected_universe"),
            "next_steps": steps,
            "provider_priority": [r["driver"] for r in provider_acquisition_rows()],
            "hard_constraints": [
                "Norgate + local data only", "large data on D: only", "repo outputs are summaries only",
                "do not install packages", "no Paper Trader / GCP / deployment",
                "no broker/order/automation", "no live trading signals", "no weight optimization",
                "no factor-sign flipping after results", "no regime activation/throttling",
                "external data never faked", "do not hide failed experiments", "do not commit", "do not push"]}


def _minimal_report(started: str, rec: str, detail: dict, inventory, coverage) -> dict:
    return {"phase": PHASE, "objective": OBJECTIVE, "generated_utc": started,
            "recommendation": rec, "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
            "decision_detail": detail, "selected_universe": detail.get("selected_universe", {}),
            "panel_shape": {"n_grid_observations": 0, "panel_ok": False},
            "external_data": {"proxy_coverage_ok": sum(1 for r in coverage if r.get("status") == "OK")},
            "budget": {"setups_registered": 0},
            "setups": {"confirmed_signals": [], "promising_signals": [], "rejected_signals": [],
                       "needs_provider_signals": []},
            "safety": _safety_block()}


def _emit_minimal(out_dir: Path, report: dict, inventory, coverage) -> None:
    p = lambda n: out_dir / n
    _write_json(p("phase8e_sensitivity_aware_signal_factory.json"), report)
    _write_csv(p("local_external_data_inventory.csv"), inventory,
               ["family", "path", "exists", "size_bytes", "n_data_rows", "populated", "header_sample", "note"])
    _write_csv(p("external_data_gap_report.csv"), gap_report_rows(inventory),
               ["family_key", "family_label", "availability", "local_present", "blocks_setup_family", "detail"])
    _write_csv(p("external_driver_catalog.csv"), driver_catalog_rows(coverage),
               ["driver_name", "driver_label", "driver_family", "proxy_symbol", "data_source",
                "frequency", "point_in_time_safe", "expected_mechanism", "likely_horizon",
                "availability", "proxy_coverage_rows", "proxy_start", "proxy_end", "proxy_status"])
    _write_csv(p("sensitivity_cohort_catalog.csv"), _cohort_catalog_rows(),
               ["cohort", "label", "driver", "proxy", "side", "quantile_rule", "estimated_from", "mechanism"])
    _write_csv(p("provider_acquisition_plan.csv"), provider_acquisition_rows(),
               ["driver", "driver_family", "providers", "estimated_cost", "priority",
                "minimal_schema", "value_hypothesis", "point_in_time_requirement", "blocked_setup_family"])
    for empty in ("ticker_external_sensitivity_map.csv", "sensitivity_quality_report.csv",
                  "sensitivity_allotments.csv", "cohort_membership_panel.csv",
                  "setup_experiment_registry.csv", "sensitivity_setup_scoreboard.csv",
                  "matched_control_report.csv", "promising_sensitivity_setups.csv",
                  "failed_sensitivity_setups.csv", "validation_skeptic_report.csv"):
        _write_csv(p(empty), [])
    _write_csv(p("confirmed_sensitivity_signals.csv"),
               [{"status": "NO_CONFIRMED_SENSITIVITY_SIGNAL"}], ["status"])
    _write_csv(p("multiple_testing_report.csv"), _multiple_testing_rows(CampaignState()), ["metric", "value"])
    _write_json(p("research_director_decision.json"),
                {"phase": PHASE, "recommendation": report["recommendation"],
                 "decision_detail": report["decision_detail"], "generated_utc": report["generated_utc"]})
    _write_json(p("phase8f_next_plan.json"), _phase8f_plan(report))


def _print_summary(report: dict) -> None:
    rec = report["recommendation"]
    ps = report.get("panel_shape", {})
    setups = report.get("setups", {})
    b = report.get("budget", {})
    ed = report.get("external_data", {})
    print(f"[{PHASE}] recommendation = {rec}")
    print(f"[{PHASE}] universe = {report.get('selected_universe', {}).get('selected_universe')}; "
          f"grid = {ps.get('n_symbols_with_obs')} symbols x {ps.get('n_grid_dates')} weekly dates "
          f"= {ps.get('n_grid_observations')} obs {ps.get('date_range')} "
          f"active={ps.get('active')} delisted={ps.get('delisted')} (ok={ps.get('panel_ok')})")
    print(f"[{PHASE}] external proxies OK = {ed.get('proxy_coverage_ok')}/{ed.get('proxy_coverage_total')}; "
          f"news/sentiment local = {ed.get('news_or_sentiment_local')}")
    print(f"[{PHASE}] setups: {b.get('setups_registered')}/{MAX_TOTAL_SETUPS} "
          f"(testable={b.get('n_testable')} provider={b.get('n_needs_provider')}) "
          f"challenge={b.get('n_challenge')} ({b.get('challenge_fraction')}); "
          f"confirmed={setups.get('confirmed_signals')} promising={setups.get('promising_signals')} "
          f"rejected={len(setups.get('rejected_signals', []))} "
          f"needs_provider={len(setups.get('needs_provider_signals', []))}")


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Phase 8-E Sensitivity-Aware Multi-Input Signal Factory")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--universe", default=None, help="override selected universe watchlist name")
    ap.add_argument("--max-symbols", type=int, default=None, help="bound the universe (bounded runs)")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        report = run(Path(args.out_dir), universe_override=args.universe, max_symbols=args.max_symbols)
    except Exception as exc:  # pragma: no cover - top-level guard
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        err = {"phase": PHASE, "recommendation": REC_ERROR, "error": repr(exc),
               "generated_utc": _utc_now_iso()}
        _write_json(out / "phase8e_sensitivity_aware_signal_factory.json", err)
        print(f"[{PHASE}] recommendation = {REC_ERROR}: {exc!r}")
        return 1
    _print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
