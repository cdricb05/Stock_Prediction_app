"""Phase 3-O - Multi-Signal Feature Factory + Research Baseline Model Gate.

This is the first *unified* multi-signal research feature factory for the current
~128-equity universe.  It deliberately does NOT wait for the Phase 3-M / 3-N Alpha
Vantage earnings collection to finish: it builds every signal family it can from data
that already exists locally -

  * the Phase 2K-G expanded free price panel (read READ ONLY on the D: drive),
  * the current-as-of sector map,
  * the Phase 3-L aligned SEC fundamental + forward-label panel,
  * the Phase 3-M partial earnings-surprise cache (25 tickers cached so far),

and adds technical/price, seasonality/calendar, market-regime, sector-relative, and
local AR/ARIMA-style time-series feature families on top.  It produces a unified research
feature panel (capped to a Git-safe sample), a feature registry, cross-sectional IC
diagnostics, and a baseline-model scoreboard.

It is research-only.  It fits NO production model, computes NO production predictions /
scores / portfolio weights, creates NO production model candidate, writes NO deployable
model artifact, touches no database, runs no migration, restarts no service, enables no
serving flag, writes nothing to the D: drive, calls no provider / paid vendor / Alpha
Vantage / third-party market-data-vendor API, purchases no data, places no orders, and
trades nothing.

Macro/inflation and sentiment families are intentionally declared in the registry but
left ``implemented=false`` with an ``external_*_data_required`` blocker, because no local
macro or sentiment data exists yet - they are NOT faked.

Forward labels (forward_excess_return_vs_spy_{21,63,126}d) are read from the Phase 3-L
panel and used for validation-only IC diagnostics; they are never turned into predictions.

Run:
    python -B research/run_phase3o_multisignal_feature_factory.py
Test:
    python -B tests/test_phase3o_multisignal_feature_factory.py
"""
from __future__ import annotations

import csv
import json
import math
import os
from datetime import datetime

import numpy as np
import pandas as pd

PHASE = "3-O"

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_OUT_DIR = os.path.join(_REPO_ROOT, "research", "output")
_O_DIR = os.path.join(_OUT_DIR, "phase3o_multisignal_feature_factory")
RESULT_JSON = os.path.join(_OUT_DIR, "phase3o_multisignal_feature_factory.json")

# Inputs (read-only).
_DATA_DIR = os.path.join("D:", os.sep, "Stock_Prediction_app_data", "phase2k_g", "output")
PRICE_CSV = os.path.join(_DATA_DIR, "phase2k_g_expanded_price_history_free.csv")
PRICE_QUALITY_JSON = os.path.join(_DATA_DIR, "phase2k_g_data_quality_report.json")
PRICE_SURVIVORSHIP_JSON = os.path.join(_DATA_DIR, "phase2k_g_survivorship_caveat.json")
SECTOR_MAP_CSV = os.path.join(_REPO_ROOT, "research", "input", "phase2k_p_sector_map_current.csv")
PHASE3L_JSON = os.path.join(_OUT_DIR, "phase3l_sec_universe_signal_gate.json")
PHASE3L_PANEL_CSV = os.path.join(
    _OUT_DIR, "phase3l_sec_universe_signal_gate", "aligned_feature_price_panel_universe.csv")
PHASE3M_JSON = os.path.join(_OUT_DIR, "phase3m_earnings_estimates_signal_gate.json")
PHASE3M_EARNINGS_CSV = os.path.join(
    _OUT_DIR, "phase3m_earnings_estimates_signal_gate", "earnings_features_universe.csv")
PHASE3M_PROGRESS_JSON = os.path.join(
    _OUT_DIR, "phase3m_earnings_estimates_signal_gate", "collection_progress.json")

BENCHMARK = "SPY"
HORIZONS = (21, 63, 126)
LABEL_TEMPLATE = "forward_excess_return_vs_spy_%dd"

# IC methodology (matches Phase 3-L / 3-M): cross-sectional daily Spearman rank IC,
# minimum cross-section size per date, summarized across dates.
IC_MIN_CROSS_SECTION = 15
DECILE_MIN_OBS = 20          # need >= this many names on a date to form deciles
FAINT_ABS_IC_FLOOR = 0.01    # below this on every family/horizon -> FAILS the signal check
ANNUAL_VOL_FACTOR = math.sqrt(252.0)
HIGH_VOL_ANNUAL_THRESHOLD = 0.20   # SPY 21d annualized vol above this == high-vol regime
SAMPLE_MAX_ROWS = 40000            # Git-safe cap for the panel sample CSV

# --------------------------------------------------------------------------- #
# Recommendations
# --------------------------------------------------------------------------- #
REC_SUCCESS = "MULTISIGNAL_FEATURE_FACTORY_SUCCESS_READY_FOR_RESEARCH_MODEL"
REC_PARTIAL = "MULTISIGNAL_FEATURE_FACTORY_PARTIAL_MACRO_SENTIMENT_MISSING"
REC_BLOCKED = "MULTISIGNAL_FEATURE_FACTORY_BLOCKED_INPUTS"
REC_FAILS = "MULTISIGNAL_FEATURE_FACTORY_FAILS_SIGNAL_CHECK"
ALLOWED_RECOMMENDATIONS = [REC_SUCCESS, REC_PARTIAL, REC_BLOCKED, REC_FAILS]

# --------------------------------------------------------------------------- #
# Feature family registry definition
# --------------------------------------------------------------------------- #
FAM_TECHNICAL = "technical_price"
FAM_SEASONALITY = "seasonality_calendar"
FAM_REGIME = "market_regime"
FAM_SECTOR = "sector_relative"
FAM_SEC = "sec_fundamental"
FAM_EARNINGS = "earnings_surprise"
FAM_MACRO = "macro_inflation"
FAM_SENTIMENT = "sentiment"
FAM_TIMESERIES = "time_series_arima_style"

ALL_FAMILIES = [
    FAM_TECHNICAL, FAM_SEASONALITY, FAM_REGIME, FAM_SECTOR, FAM_SEC,
    FAM_EARNINGS, FAM_MACRO, FAM_SENTIMENT, FAM_TIMESERIES,
]

# Per family: list of feature_name, and whether it varies cross-sectionally (so a daily
# cross-sectional IC is meaningful).  Calendar + market-regime features are identical
# across tickers on a given date, so their cross-sectional IC is undefined by construction.
TECHNICAL_FEATURES = [
    "return_5d", "return_21d", "return_63d", "return_126d", "momentum_21_126",
    "volatility_21d", "volatility_63d", "drawdown_63d", "price_vs_sma_21d",
    "price_vs_sma_63d", "realized_skew_63d", "realized_kurtosis_63d",
]
SEASONALITY_CALENDAR_FEATURES = [
    "month_of_year", "quarter_of_year", "day_of_week", "is_month_start", "is_month_end",
    "is_quarter_end", "is_year_end", "turn_of_month_window", "january_flag",
    "sell_in_may_window",
]
SEASONALITY_CROSS_SECTIONAL_FEATURES = [
    "historical_same_month_avg_return_by_ticker",
    "historical_same_month_win_rate_by_ticker",
    "historical_month_rank_by_ticker",
]
SEASONALITY_FEATURES = SEASONALITY_CALENDAR_FEATURES + SEASONALITY_CROSS_SECTIONAL_FEATURES
REGIME_FEATURES = [
    "spy_return_21d", "spy_return_63d", "spy_volatility_21d", "spy_volatility_63d",
    "spy_drawdown_63d", "spy_above_sma_200d", "market_risk_off_flag", "market_high_vol_flag",
    "cross_sectional_dispersion_21d", "cross_sectional_breadth_21d",
]
SECTOR_FEATURES = [
    "ticker_return_21d_minus_sector_return_21d", "ticker_return_63d_minus_sector_return_63d",
    "ticker_vol_21d_minus_sector_vol_21d", "sector_return_21d_minus_spy_return_21d",
    "sector_return_63d_minus_spy_return_63d", "sector_relative_strength_rank_by_date",
    "ticker_rank_within_sector_21d", "ticker_rank_within_sector_63d",
]
SEC_FEATURES = [
    "operating_margin", "net_margin", "cash_conversion", "accrual_proxy", "log_total_assets",
    "filing_lag_days", "debt_proxy_total_liabilities_to_assets", "revenue_yoy_growth",
    "eps_diluted_yoy_growth",
]
EARNINGS_FEATURES = [
    "eps_surprise", "eps_surprise_pct", "trailing_4q_avg_surprise_pct",
    "trailing_4q_positive_surprise_rate", "surprise_acceleration", "days_since_last_earnings",
]
MACRO_FEATURES = [
    "cpi_yoy", "cpi_mom", "inflation_regime", "10y_yield", "2y_yield", "yield_curve_10y_2y",
    "real_rate_proxy", "inflation_shock_flag", "fed_policy_regime",
]
SENTIMENT_FEATURES = [
    "news_sentiment_avg_7d", "news_sentiment_avg_30d", "sentiment_momentum",
    "negative_news_intensity", "event_count_7d", "analyst_tone_proxy",
]
TIMESERIES_FEATURES = [
    "ar1_beta_63d", "ar1_residual_zscore_63d", "rolling_mean_reversion_signal_21d",
    "trend_persistence_63d", "forecast_error_direction_21d",
]

# Cross-sectional features that get a meaningful daily IC.
IC_FEATURES_BY_FAMILY = {
    FAM_TECHNICAL: TECHNICAL_FEATURES,
    FAM_SEASONALITY: SEASONALITY_CROSS_SECTIONAL_FEATURES,
    FAM_SECTOR: SECTOR_FEATURES,
    FAM_SEC: SEC_FEATURES,
    FAM_EARNINGS: EARNINGS_FEATURES,
    FAM_TIMESERIES: TIMESERIES_FEATURES,
}
# Families that are time-series conditioning (constant across the cross-section on a date).
NON_CROSS_SECTIONAL_NOTE = (
    "time-series / calendar conditioning feature: identical across tickers on a given date, "
    "so a cross-sectional daily IC is undefined by construction (regime / calendar context)")


# --------------------------------------------------------------------------- #
# Small pure helpers (NaN-safe, mirroring Phase 3-L semantics)
# --------------------------------------------------------------------------- #
def _round(x, n=6):
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, n)


def _sign(x):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return 0
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def _mean(values):
    vals = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    return sum(vals) / len(vals) if vals else None


def _median(values):
    vals = sorted(v for v in values
                  if v is not None and not (isinstance(v, float) and math.isnan(v)))
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def _std(values):
    vals = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if len(vals) < 2:
        return None
    m = sum(vals) / len(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))


def _load_json(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------------------- #
# Cross-sectional daily rank IC (vectorized; same statistic as Phase 3-L / 3-M)
# --------------------------------------------------------------------------- #
def _summarize_ic(ic_by_date):
    """ic_by_date: dict date->ic (already filtered to valid dates). Returns summary dict."""
    ics = [v for v in ic_by_date.values()
           if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not ics:
        return {"mean": None, "median": None, "hit_rate": None, "ir": None, "date_count": 0}
    mean_ic = _mean(ics)
    std_ic = _std(ics)
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


def daily_rank_ic(df, feature, label, min_cs=IC_MIN_CROSS_SECTION):
    """Cross-sectional Spearman rank IC per scoring_date, vectorized.

    For each date with >= ``min_cs`` non-null (feature,label) pairs, IC == Pearson
    correlation of the within-date ranks (== Spearman). Dates with zero feature variance
    (e.g. a calendar/regime feature that is constant across the cross-section) yield NaN
    and are dropped. Returns (summary_dict, total_paired_observations).
    """
    sub = df[["scoring_date", feature, label]].dropna()
    if sub.empty:
        return _summarize_ic({}), 0
    cnt = sub.groupby("scoring_date")[feature].transform("size")
    sub = sub[cnt >= min_cs]
    if sub.empty:
        return _summarize_ic({}), 0
    rx = sub.groupby("scoring_date")[feature].rank()
    ry = sub.groupby("scoring_date")[label].rank()
    tmp = pd.DataFrame({"scoring_date": sub["scoring_date"].to_numpy(),
                        "rx": rx.to_numpy(), "ry": ry.to_numpy()})
    mx = tmp.groupby("scoring_date")["rx"].transform("mean")
    my = tmp.groupby("scoring_date")["ry"].transform("mean")
    dx = tmp["rx"] - mx
    dy = tmp["ry"] - my
    tmp = tmp.assign(num=dx * dy, sxx=dx * dx, syy=dy * dy)
    agg = tmp.groupby("scoring_date").agg(num=("num", "sum"), sxx=("sxx", "sum"),
                                          syy=("syy", "sum"))
    denom = np.sqrt(agg["sxx"] * agg["syy"])
    ic = agg["num"] / denom.replace(0.0, np.nan)
    ic = ic.dropna()
    return _summarize_ic(ic.to_dict()), int(len(sub))


# --------------------------------------------------------------------------- #
# 1. Inputs confirmation
# --------------------------------------------------------------------------- #
def confirm_inputs():
    progress = _load_json(PHASE3M_PROGRESS_JSON) or {}
    earnings_cache_count = progress.get("cached_ticker_count_after_run")
    if earnings_cache_count is None:
        earnings_cache_count = progress.get("cached_ticker_count_before_run", 0)
    return {
        "price_panel_present": os.path.isfile(PRICE_CSV),
        "price_panel_read_only": True,
        "price_panel_on_d_drive": os.path.splitdrive(os.path.abspath(PRICE_CSV))[0].upper() == "D:",
        "price_data_quality_present": os.path.isfile(PRICE_QUALITY_JSON),
        "price_survivorship_caveat_present": os.path.isfile(PRICE_SURVIVORSHIP_JSON),
        "sector_map_present": os.path.isfile(SECTOR_MAP_CSV),
        "phase3l_result_present": os.path.isfile(PHASE3L_JSON),
        "phase3l_aligned_panel_present": os.path.isfile(PHASE3L_PANEL_CSV),
        "phase3m_result_present": os.path.isfile(PHASE3M_JSON),
        "phase3m_earnings_features_present": os.path.isfile(PHASE3M_EARNINGS_CSV),
        "phase3m_collection_progress_present": os.path.isfile(PHASE3M_PROGRESS_JSON),
        "earnings_cache_ticker_count": int(earnings_cache_count or 0),
        "fetched_new_earnings_data": False,
        "alpha_vantage_called": False,
    }


# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #
def load_sector_map(path):
    df = pd.read_csv(path, usecols=lambda c: c in ("ticker", "sector", "industry"))
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["sector"] = df["sector"].astype(str).str.strip()
    return df[["ticker", "sector"]].drop_duplicates("ticker")


def load_price_panel(path):
    """Read the Phase 2K-G price panel READ ONLY. Returns a tidy ticker/date/close frame."""
    df = pd.read_csv(path, usecols=["ticker", "date", "adjusted_close", "volume"])
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["adjusted_close"] = pd.to_numeric(df["adjusted_close"], errors="coerce")
    df = df.dropna(subset=["date", "adjusted_close"])
    df = df[df["adjusted_close"] > 0]
    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)
    return df


def load_phase3l_panel(path):
    cols = ["ticker", "sector", "scoring_date", "adjusted_close"] + SEC_FEATURES + \
           [LABEL_TEMPLATE % h for h in HORIZONS]
    header = pd.read_csv(path, nrows=0).columns.tolist()
    usecols = [c for c in cols if c in header]
    df = pd.read_csv(path, usecols=usecols)
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["scoring_date"] = pd.to_datetime(df["scoring_date"], errors="coerce")
    df = df.dropna(subset=["scoring_date"])
    for c in SEC_FEATURES + [LABEL_TEMPLATE % h for h in HORIZONS]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def load_earnings_features(path):
    if not os.path.isfile(path):
        return pd.DataFrame()
    df = pd.read_csv(path)
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["availability_date"] = pd.to_datetime(df.get("availability_date"), errors="coerce")
    df["reported_date"] = pd.to_datetime(df.get("reported_date"), errors="coerce")
    if "point_in_time_usable" in df.columns:
        pit = df["point_in_time_usable"].astype(str).str.strip().str.lower()
        df = df[pit.isin(("true", "1", "1.0", "yes"))]
    df = df.dropna(subset=["availability_date"])
    keep = ["ticker", "availability_date", "reported_date"] + [
        c for c in EARNINGS_FEATURES if c in df.columns and c != "days_since_last_earnings"]
    return df[keep].sort_values("availability_date").reset_index(drop=True)


# --------------------------------------------------------------------------- #
# 3. Technical / price + 11. AR-style features (per ticker, point-in-time)
# --------------------------------------------------------------------------- #
def _ret(close, k):
    return close.pct_change(k, fill_method=None)


def compute_price_features(px):
    """Per-ticker rolling technical + AR-style features. Uses only data up to each date."""
    frames = []
    for _tkr, sub in px.groupby("ticker", sort=False):
        sub = sub.sort_values("date").copy()
        c = sub["adjusted_close"]
        logret = np.log(c).diff()
        sub["return_5d"] = _ret(c, 5)
        sub["return_21d"] = _ret(c, 21)
        sub["return_63d"] = _ret(c, 63)
        sub["return_126d"] = _ret(c, 126)
        # Intermediate momentum (126d minus most-recent 21d) - the classic 12-1 style.
        sub["momentum_21_126"] = sub["return_126d"] - sub["return_21d"]
        sub["volatility_21d"] = logret.rolling(21).std() * ANNUAL_VOL_FACTOR
        sub["volatility_63d"] = logret.rolling(63).std() * ANNUAL_VOL_FACTOR
        roll_max_63 = c.rolling(63).max()
        sub["drawdown_63d"] = c / roll_max_63 - 1.0
        sma21 = c.rolling(21).mean()
        sma63 = c.rolling(63).mean()
        sub["price_vs_sma_21d"] = c / sma21 - 1.0
        sub["price_vs_sma_63d"] = c / sma63 - 1.0
        sub["realized_skew_63d"] = logret.rolling(63).skew()
        sub["realized_kurtosis_63d"] = logret.rolling(63).kurt()
        # Regime/breadth helper (200d SMA) - not emitted directly per ticker.
        sma200 = c.rolling(200).mean()
        sub["_above_sma_200d"] = (c > sma200).astype(float)
        # ---- AR-style / mean-reversion diagnostics (local, no statsmodels) ----
        r = logret
        r_lag = r.shift(1)
        cov = r.rolling(63).cov(r_lag)
        var_lag = r_lag.rolling(63).var()
        beta = cov / var_lag.replace(0.0, np.nan)
        sub["ar1_beta_63d"] = beta
        mean_r = r.rolling(63).mean()
        mean_lag = r_lag.rolling(63).mean()
        resid = (r - mean_r) - beta * (r_lag - mean_lag)
        resid_std = resid.rolling(63).std()
        sub["ar1_residual_zscore_63d"] = resid / resid_std.replace(0.0, np.nan)
        # 21d Bollinger-style reversal: negative standardized deviation from the 21d mean.
        dev = c - sma21
        dev_std = c.rolling(21).std()
        sub["rolling_mean_reversion_signal_21d"] = -(dev / dev_std.replace(0.0, np.nan))
        # Trend persistence: mean sign of daily returns over 63d, in [-1, 1].
        sub["trend_persistence_63d"] = np.sign(r).rolling(63).mean()
        # Forecast-error direction: does the current 21d return agree with the prior
        # (non-overlapping) 21d return? +1 persistence, -1 reversal. Point-in-time.
        sub["forecast_error_direction_21d"] = (
            np.sign(sub["return_21d"]) * np.sign(sub["return_21d"].shift(21)))
        frames.append(sub)
    out = pd.concat(frames, ignore_index=True)
    return out


# --------------------------------------------------------------------------- #
# 4. Seasonality features
# --------------------------------------------------------------------------- #
def compute_seasonality_history(px):
    """Historical same-month statistics by ticker, using ONLY prior years (no lookahead)."""
    m = px.copy()
    m["ym"] = m["date"].dt.to_period("M")
    last = m.sort_values("date").groupby(["ticker", "ym"], as_index=False).tail(1)
    last = last.sort_values(["ticker", "ym"]).copy()
    last["m_ret"] = last.groupby("ticker")["adjusted_close"].pct_change(fill_method=None)
    last["year"] = last["ym"].dt.year.astype(int)
    last["month"] = last["ym"].dt.month.astype(int)
    rows = []
    for (tkr, month), grp in last.groupby(["ticker", "month"]):
        grp = grp.sort_values("year")
        rets = grp["m_ret"].tolist()
        years = grp["year"].tolist()
        for i, y in enumerate(years):
            prior = [v for v in rets[:i] if v is not None and not math.isnan(v)]
            avg = float(np.mean(prior)) if prior else np.nan
            win = float(np.mean([1.0 if v > 0 else 0.0 for v in prior])) if prior else np.nan
            rows.append((tkr, int(y), int(month), avg, win))
    hist = pd.DataFrame(rows, columns=[
        "ticker", "year", "month", "historical_same_month_avg_return_by_ticker",
        "historical_same_month_win_rate_by_ticker"])
    # Rank the 12 calendar months within (ticker, year) by their prior-years average.
    hist["historical_month_rank_by_ticker"] = (
        hist.groupby(["ticker", "year"])["historical_same_month_avg_return_by_ticker"]
        .rank(ascending=False))
    return hist


def add_calendar_features(panel):
    d = panel["scoring_date"].dt
    panel["month_of_year"] = d.month
    panel["quarter_of_year"] = d.quarter
    panel["day_of_week"] = d.dayofweek
    panel["is_month_start"] = d.is_month_start.astype(int)
    panel["is_month_end"] = d.is_month_end.astype(int)
    panel["is_quarter_end"] = (d.is_quarter_end).astype(int)
    panel["is_year_end"] = (d.is_year_end).astype(int)
    dom = d.day
    panel["turn_of_month_window"] = ((dom <= 3) | (dom >= 26)).astype(int)
    panel["january_flag"] = (d.month == 1).astype(int)
    panel["sell_in_may_window"] = d.month.isin([5, 6, 7, 8, 9, 10]).astype(int)
    return panel


# --------------------------------------------------------------------------- #
# 5. Market-regime features (SPY + cross-sectional dispersion/breadth)
# --------------------------------------------------------------------------- #
def compute_market_regime(price_feats):
    spy = price_feats[price_feats["ticker"] == BENCHMARK].sort_values("date").copy()
    regime = pd.DataFrame({"date": spy["date"].to_numpy()})
    regime["spy_return_21d"] = spy["return_21d"].to_numpy()
    regime["spy_return_63d"] = spy["return_63d"].to_numpy()
    regime["spy_volatility_21d"] = spy["volatility_21d"].to_numpy()
    regime["spy_volatility_63d"] = spy["volatility_63d"].to_numpy()
    regime["spy_drawdown_63d"] = spy["drawdown_63d"].to_numpy()
    regime["spy_above_sma_200d"] = spy["_above_sma_200d"].to_numpy()
    regime["market_risk_off_flag"] = (
        (regime["spy_above_sma_200d"] == 0) | (regime["spy_drawdown_63d"] < -0.10)).astype(float)
    regime["market_high_vol_flag"] = (
        regime["spy_volatility_21d"] > HIGH_VOL_ANNUAL_THRESHOLD).astype(float)
    # Cross-sectional dispersion / breadth across the equity universe (SPY excluded).
    eq = price_feats[price_feats["ticker"] != BENCHMARK]
    disp = eq.groupby("date")["return_21d"].std()
    breadth = eq.groupby("date").apply(
        lambda g: float(np.mean(g["return_21d"] > 0)) if g["return_21d"].notna().any() else np.nan,
        include_groups=False)
    disp_df = pd.DataFrame({"date": disp.index.to_numpy(),
                            "cross_sectional_dispersion_21d": disp.to_numpy()})
    breadth_df = pd.DataFrame({"date": breadth.index.to_numpy(),
                               "cross_sectional_breadth_21d": breadth.to_numpy()})
    regime = regime.merge(disp_df, on="date", how="left").merge(breadth_df, on="date", how="left")
    return regime


# --------------------------------------------------------------------------- #
# 6. Sector-relative features
# --------------------------------------------------------------------------- #
def compute_sector_relative(price_feats, sector_map, regime):
    eq = price_feats[price_feats["ticker"] != BENCHMARK][
        ["ticker", "date", "return_21d", "return_63d", "volatility_21d"]].copy()
    eq = eq.merge(sector_map, on="ticker", how="left")
    eq["sector"] = eq["sector"].fillna("UNKNOWN")
    # Sector aggregates per (date, sector).
    grp = eq.groupby(["date", "sector"])
    eq["sector_return_21d"] = grp["return_21d"].transform("mean")
    eq["sector_return_63d"] = grp["return_63d"].transform("mean")
    eq["sector_vol_21d"] = grp["volatility_21d"].transform("mean")
    eq["ticker_return_21d_minus_sector_return_21d"] = eq["return_21d"] - eq["sector_return_21d"]
    eq["ticker_return_63d_minus_sector_return_63d"] = eq["return_63d"] - eq["sector_return_63d"]
    eq["ticker_vol_21d_minus_sector_vol_21d"] = eq["volatility_21d"] - eq["sector_vol_21d"]
    # Sector vs SPY.
    spy = regime[["date", "spy_return_21d", "spy_return_63d"]]
    eq = eq.merge(spy, on="date", how="left")
    eq["sector_return_21d_minus_spy_return_21d"] = eq["sector_return_21d"] - eq["spy_return_21d"]
    eq["sector_return_63d_minus_spy_return_63d"] = eq["sector_return_63d"] - eq["spy_return_63d"]
    # Cross-sectional sector strength rank per date (rank of the sector's 21d return).
    sec_daily = eq.drop_duplicates(["date", "sector"])[["date", "sector", "sector_return_21d"]]
    sec_daily["sector_relative_strength_rank_by_date"] = (
        sec_daily.groupby("date")["sector_return_21d"].rank(ascending=False))
    eq = eq.merge(
        sec_daily[["date", "sector", "sector_relative_strength_rank_by_date"]],
        on=["date", "sector"], how="left")
    # Ticker rank within its sector per date.
    eq["ticker_rank_within_sector_21d"] = (
        eq.groupby(["date", "sector"])["return_21d"].rank(ascending=False))
    eq["ticker_rank_within_sector_63d"] = (
        eq.groupby(["date", "sector"])["return_63d"].rank(ascending=False))
    cols = ["ticker", "date"] + SECTOR_FEATURES
    return eq[cols]


# --------------------------------------------------------------------------- #
# 8. Earnings-surprise features (as-of merge onto the panel; partial coverage)
# --------------------------------------------------------------------------- #
def merge_earnings_asof(panel, earnings):
    cols = [c for c in EARNINGS_FEATURES if c != "days_since_last_earnings"]
    if earnings.empty:
        for c in EARNINGS_FEATURES:
            panel[c] = np.nan
        return panel, 0
    left = panel.sort_values("scoring_date")
    right = earnings.sort_values("availability_date")
    merged = pd.merge_asof(
        left, right, by="ticker", left_on="scoring_date", right_on="availability_date",
        direction="backward")
    for c in cols:
        if c not in merged.columns:
            merged[c] = np.nan
        merged[c] = pd.to_numeric(merged[c], errors="coerce")
    # Recompute days_since_last_earnings relative to the as-of scoring date.
    merged["days_since_last_earnings"] = (
        merged["scoring_date"] - merged["reported_date"]).dt.days
    covered = int(merged.loc[merged["eps_surprise_pct"].notna(), "ticker"].nunique()) \
        if "eps_surprise_pct" in merged.columns else 0
    merged = merged.drop(columns=[c for c in ("availability_date", "reported_date")
                                  if c in merged.columns])
    return merged, covered


# --------------------------------------------------------------------------- #
# 2. Feature registry
# --------------------------------------------------------------------------- #
def build_feature_registry(implemented_families):
    rows = []

    def add(name, family, source, pit_rule, lag_rule, future, implemented, blocker, notes):
        rows.append({
            "feature_name": name, "feature_family": family, "input_source": source,
            "point_in_time_rule": pit_rule, "availability_lag_rule": lag_rule,
            "uses_future_data": bool(future), "implemented": bool(implemented),
            "blocker": blocker, "notes": notes,
        })

    price_src = "phase2k_g_expanded_price_history_free.csv (D: read-only)"
    for f in TECHNICAL_FEATURES:
        add(f, FAM_TECHNICAL, price_src, "uses only adjusted closes up to and including the "
            "scoring date (trailing rolling window)", "none (prices are same-day)", False, True,
            "", "trailing price/return/vol/shape feature")
    for f in SEASONALITY_CALENDAR_FEATURES:
        add(f, FAM_SEASONALITY, "scoring_date calendar", "derived purely from the scoring date",
            "none", False, True, "",
            "deterministic calendar flag; identical across tickers on a date (not cross-sectional)")
    for f in SEASONALITY_CROSS_SECTIONAL_FEATURES:
        add(f, FAM_SEASONALITY, price_src, "monthly returns from strictly PRIOR years only",
            "prior-years only; current/future months excluded", False, True, "",
            "historical seasonal profile by ticker; cross-sectional")
    for f in REGIME_FEATURES:
        add(f, FAM_REGIME, price_src + " (SPY + universe)",
            "trailing SPY window / same-date cross-sectional aggregate", "none", False, True, "",
            "market-regime context; identical across tickers on a date (not cross-sectional)")
    for f in SECTOR_FEATURES:
        add(f, FAM_SECTOR, price_src + " + sector map",
            "same-date sector aggregate of trailing returns/vol", "none", False, True, "",
            "current-as-of sector map (NOT point-in-time membership); caveated research only")
    sec_src = "phase3l aligned_feature_price_panel_universe.csv"
    for f in SEC_FEATURES:
        add(f, FAM_SEC, sec_src, "Phase 3-L feature_asof_date (never the fiscal period end)",
            "SEC filing acceptance lag already applied upstream in Phase 3-L", False, True, "",
            "reused from Phase 3-L; not recomputed here")
    earn_src = "phase3m earnings_features_universe.csv (partial cache)"
    for f in EARNINGS_FEATURES:
        add(f, FAM_EARNINGS, earn_src,
            "as-of merge: latest earnings event with availability_date <= scoring_date",
            "provider availability_date (point-in-time-usable rows only)", False, True, "",
            "PARTIAL coverage: only the Phase 3-M cached tickers carry values; no new fetch")
    for f in MACRO_FEATURES:
        add(f, FAM_MACRO, "external macro provider (NOT available locally)", "n/a", "n/a", False,
            False, "external_macro_data_required",
            "declared for the roadmap; NOT implemented and NOT faked - needs a local macro file")
    for f in SENTIMENT_FEATURES:
        add(f, FAM_SENTIMENT, "external news/sentiment provider (NOT available locally)", "n/a",
            "n/a", False, False, "external_sentiment_data_required",
            "declared for the roadmap; NOT implemented and NOT faked - needs local sentiment data")
    for f in TIMESERIES_FEATURES:
        add(f, FAM_TIMESERIES, price_src,
            "trailing daily-return window up to the scoring date (local AR, no statsmodels)",
            "none", False, True, "",
            "ARIMA is treated as ONE signal family, not the whole model; statsmodels optional")
    return rows


# --------------------------------------------------------------------------- #
# IC diagnostics (13)
# --------------------------------------------------------------------------- #
def compute_feature_ic(panel, non_null_frac):
    rows = []
    for family, feats in IC_FEATURES_BY_FAMILY.items():
        for feat in feats:
            if feat not in panel.columns:
                continue
            for h in HORIZONS:
                label = LABEL_TEMPLATE % h
                summary, obs = daily_rank_ic(panel, feat, label)
                mean_ic = summary["mean"]
                rows.append({
                    "feature": feat, "feature_family": family, "horizon": "%dd" % h,
                    "_horizon_days": h, "_cross_sectional": True,
                    "non_null_feature_fraction": _round(non_null_frac.get(feat), 4),
                    "observation_count": obs, "ic_date_count": summary["date_count"],
                    "mean_rank_ic": mean_ic, "median_rank_ic": summary["median"],
                    "ic_hit_rate": summary["hit_rate"], "ic_ir": summary["ir"],
                    "absolute_mean_ic": _round(abs(mean_ic), 6) if mean_ic is not None else None,
                })
    # Non-cross-sectional families: emit explicit rows documenting why IC is undefined.
    for family, feats in ((FAM_REGIME, REGIME_FEATURES),
                          (FAM_SEASONALITY, SEASONALITY_CALENDAR_FEATURES)):
        for feat in feats:
            for h in HORIZONS:
                rows.append({
                    "feature": feat, "feature_family": family, "horizon": "%dd" % h,
                    "_horizon_days": h, "_cross_sectional": False,
                    "non_null_feature_fraction": _round(non_null_frac.get(feat), 4),
                    "observation_count": 0, "ic_date_count": 0, "mean_rank_ic": None,
                    "median_rank_ic": None, "ic_hit_rate": None, "ic_ir": None,
                    "absolute_mean_ic": None,
                })
    return rows


def compute_family_ic_summary(feature_ic_rows):
    out = []
    for family in ALL_FAMILIES:
        for h in HORIZONS:
            sub = [r for r in feature_ic_rows
                   if r["feature_family"] == family and r["_horizon_days"] == h]
            implemented = family in IMPLEMENTED_FAMILIES
            cross_sectional = any(r["_cross_sectional"] for r in sub)
            judgeable = [r for r in sub if r["absolute_mean_ic"] is not None]
            abs_ics = [r["absolute_mean_ic"] for r in judgeable]
            best = max(judgeable, key=lambda r: r["absolute_mean_ic"]) if judgeable else None
            if not implemented:
                note = "family not implemented (external data required); no IC computed"
            elif not cross_sectional:
                note = NON_CROSS_SECTIONAL_NOTE
            elif judgeable:
                note = "cross-sectional daily rank IC measured across implemented features"
            else:
                note = "implemented but no date cleared the minimum cross-section for this horizon"
            out.append({
                "feature_family": family, "horizon": "%dd" % h, "_horizon_days": h,
                "implemented": implemented, "cross_sectional": cross_sectional,
                "num_features": len([r for r in sub]),
                "num_features_with_ic": len(judgeable),
                "best_feature": best["feature"] if best else "",
                "best_feature_abs_mean_ic": best["absolute_mean_ic"] if best else None,
                "best_feature_mean_ic": best["mean_rank_ic"] if best else None,
                "mean_abs_mean_ic": _round(_mean(abs_ics), 6) if abs_ics else None,
                "max_ic_date_count": max((r["ic_date_count"] for r in sub), default=0),
                "note": note,
            })
    return out


# --------------------------------------------------------------------------- #
# Per-family feature summaries (descriptive coverage CSVs)
# --------------------------------------------------------------------------- #
def feature_summary_rows(panel, feats):
    rows = []
    n = len(panel)
    for f in feats:
        if f not in panel.columns:
            rows.append({"feature": f, "rows": n, "non_null_count": 0, "non_null_fraction": 0.0,
                         "mean": None, "std": None, "min": None, "median": None, "max": None})
            continue
        s = pd.to_numeric(panel[f], errors="coerce")
        nn = int(s.notna().sum())
        rows.append({
            "feature": f, "rows": n, "non_null_count": nn,
            "non_null_fraction": _round(nn / n, 4) if n else 0.0,
            "mean": _round(s.mean(), 6), "std": _round(s.std(), 6),
            "min": _round(s.min(), 6), "median": _round(s.median(), 6), "max": _round(s.max(), 6),
        })
    return rows


# --------------------------------------------------------------------------- #
# 14. Baseline model scoreboard (research-only; no weights, no production scores)
# --------------------------------------------------------------------------- #
def _pct_rank(panel, col):
    if col not in panel.columns:
        return pd.Series(np.nan, index=panel.index)
    return panel.groupby("scoring_date")[col].rank(pct=True)


def _composite_rank(panel, cols, signs=None):
    signs = signs or {}
    parts = []
    for c in cols:
        r = _pct_rank(panel, c)
        if signs.get(c, 1) < 0:
            r = 1.0 - r
        parts.append(r)
    if not parts:
        return pd.Series(np.nan, index=panel.index)
    mat = pd.concat(parts, axis=1)
    return mat.mean(axis=1, skipna=True)


def build_baseline_scores(panel):
    """Return {baseline_name: score Series}. Scores are cross-sectional ranking signals."""
    scores = {}
    scores["momentum_rank_composite"] = _composite_rank(
        panel, ["return_21d", "return_63d", "return_126d", "momentum_21_126"])
    scores["seasonality_rank_composite"] = _pct_rank(
        panel, "historical_same_month_avg_return_by_ticker")
    scores["sector_neutral_momentum"] = _composite_rank(
        panel, ["ticker_return_21d_minus_sector_return_21d",
                "ticker_return_63d_minus_sector_return_63d"])
    scores["ar_style_mean_reversion"] = _pct_rank(panel, "rolling_mean_reversion_signal_21d")
    scores["sec_fundamental_rank_composite"] = _composite_rank(
        panel, ["operating_margin", "net_margin", "revenue_yoy_growth", "eps_diluted_yoy_growth",
                "accrual_proxy", "debt_proxy_total_liabilities_to_assets"],
        signs={"accrual_proxy": -1, "debt_proxy_total_liabilities_to_assets": -1})
    # Regime-adjusted momentum: momentum normally, but switch to mean-reversion when the
    # market is in a high-volatility regime.
    mom = scores["momentum_rank_composite"]
    rev = scores["ar_style_mean_reversion"]
    high_vol = panel.get("market_high_vol_flag")
    if high_vol is not None:
        scores["market_regime_adjusted_momentum"] = mom.where(high_vol != 1, rev)
    else:
        scores["market_regime_adjusted_momentum"] = mom
    # Combined multi-signal composite: average of available family composites.
    combo = pd.concat([
        scores["momentum_rank_composite"], scores["seasonality_rank_composite"],
        scores["sector_neutral_momentum"], scores["sec_fundamental_rank_composite"],
        _pct_rank(panel, "trailing_4q_avg_surprise_pct"),
    ], axis=1)
    scores["combined_multisignal_rank_composite"] = combo.mean(axis=1, skipna=True)
    return scores


def _evaluate_signal(panel, score, label, min_cs=DECILE_MIN_OBS):
    """IC + top/bottom decile spread + selected-name mean excess for a ranking signal."""
    df = pd.DataFrame({"scoring_date": panel["scoring_date"].to_numpy(),
                       "ticker": panel["ticker"].to_numpy(),
                       "score": np.asarray(score, dtype=float),
                       "label": pd.to_numeric(panel[label], errors="coerce").to_numpy()})
    df = df.dropna(subset=["score", "label"])
    if df.empty:
        return _empty_scoreboard_metrics()
    ic_summary, _ = daily_rank_ic(df.assign(**{label: df["label"]}), "score", label,
                                  min_cs=IC_MIN_CROSS_SECTION) \
        if label in df.columns else (_summarize_ic({}), 0)
    # Recompute IC cleanly against a renamed label column.
    ic_df = df.rename(columns={"label": "_lab"})
    ic_summary, _ = daily_rank_ic(ic_df, "score", "_lab", min_cs=IC_MIN_CROSS_SECTION)
    top_spreads, top_means, hit = [], [], []
    years = set()
    for date, g in df.groupby("scoring_date"):
        n = len(g)
        if n < min_cs:
            continue
        k = max(1, n // 10)
        gs = g.sort_values("score")
        bottom = gs.iloc[:k]["label"]
        top = gs.iloc[-k:]["label"]
        top_mean = float(top.mean())
        top_spreads.append(top_mean - float(bottom.mean()))
        top_means.append(top_mean)
        hit.append(1.0 if top_mean > 0 else 0.0)
        years.add(pd.Timestamp(date).year)
    return {
        "sample_rows": int(len(df)),
        "sample_tickers": int(df["ticker"].nunique()),
        "mean_forward_excess_return": _round(_mean(top_means), 6),
        "information_coefficient": ic_summary["mean"],
        "top_decile_minus_bottom_decile_spread": _round(_mean(top_spreads), 6),
        "hit_rate": _round(_mean(hit), 4),
        "annual_coverage_years": len(years),
    }


def _empty_scoreboard_metrics():
    return {"sample_rows": 0, "sample_tickers": 0, "mean_forward_excess_return": None,
            "information_coefficient": None, "top_decile_minus_bottom_decile_spread": None,
            "hit_rate": None, "annual_coverage_years": 0}


def build_scoreboard(panel, scores):
    rows = []
    for h in HORIZONS:
        label = LABEL_TEMPLATE % h
        lab = pd.to_numeric(panel[label], errors="coerce")
        valid = lab.notna()
        years = pd.to_datetime(panel.loc[valid, "scoring_date"]).dt.year.nunique()
        # benchmark_spy: the passive reference. Excess is defined relative to SPY, so the
        # benchmark's own excess return is 0 by construction.
        rows.append({"model": "benchmark_spy", "horizon": "%dd" % h,
                     "sample_rows": int(valid.sum()),
                     "sample_tickers": int(panel.loc[valid, "ticker"].nunique()),
                     "mean_forward_excess_return": 0.0, "information_coefficient": None,
                     "top_decile_minus_bottom_decile_spread": None, "hit_rate": None,
                     "annual_coverage_years": int(years),
                     "notes": "passive SPY benchmark; forward excess return defined relative to it"})
        # equal_weight_universe: average forward excess of every eligible name vs SPY.
        ew = lab[valid]
        rows.append({"model": "equal_weight_universe", "horizon": "%dd" % h,
                     "sample_rows": int(valid.sum()),
                     "sample_tickers": int(panel.loc[valid, "ticker"].nunique()),
                     "mean_forward_excess_return": _round(ew.mean(), 6),
                     "information_coefficient": None,
                     "top_decile_minus_bottom_decile_spread": None,
                     "hit_rate": _round(float((ew > 0).mean()), 4) if len(ew) else None,
                     "annual_coverage_years": int(years),
                     "notes": "equal-weight all eligible names; universe tilt vs SPY"})
        for name, score in scores.items():
            m = _evaluate_signal(panel, score, label)
            note = "research-only cross-sectional ranking signal; no weights, no production scores"
            if name == "seasonality_rank_composite":
                note = "prior-years-only seasonal ranking; research diagnostic"
            elif name == "combined_multisignal_rank_composite":
                note = "equal-weight blend of available family rank composites; research only"
            rows.append({"model": name, "horizon": "%dd" % h, "notes": note, **m})
    return rows


def summarize_scoreboard(rows):
    ranking = [r for r in rows if r["model"] not in ("benchmark_spy", "equal_weight_universe")
               and r["information_coefficient"] is not None]
    best_ic = max(ranking, key=lambda r: abs(r["information_coefficient"]), default=None)
    by_spread = [r for r in rows if r["top_decile_minus_bottom_decile_spread"] is not None
                 and r["model"] not in ("benchmark_spy",)]
    best_spread = max(by_spread, key=lambda r: r["top_decile_minus_bottom_decile_spread"],
                      default=None)
    return {
        "model_count": len({r["model"] for r in rows}),
        "horizons": ["%dd" % h for h in HORIZONS],
        "best_by_abs_ic": None if not best_ic else {
            "model": best_ic["model"], "horizon": best_ic["horizon"],
            "information_coefficient": best_ic["information_coefficient"]},
        "best_by_decile_spread": None if not best_spread else {
            "model": best_spread["model"], "horizon": best_spread["horizon"],
            "top_decile_minus_bottom_decile_spread":
                best_spread["top_decile_minus_bottom_decile_spread"]},
    }


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #
IMPLEMENTED_FAMILIES = [
    FAM_TECHNICAL, FAM_SEASONALITY, FAM_REGIME, FAM_SECTOR, FAM_SEC, FAM_EARNINGS, FAM_TIMESERIES,
]
BLOCKED_FAMILIES = [FAM_MACRO, FAM_SENTIMENT]


def decide(inputs_ok, family_ic_rows, scoreboard_rows):
    if not inputs_ok:
        return REC_BLOCKED
    abs_family_ics = [r["mean_abs_mean_ic"] for r in family_ic_rows
                      if r["implemented"] and r["mean_abs_mean_ic"] is not None]
    abs_baseline_ics = [abs(r["information_coefficient"]) for r in scoreboard_rows
                        if r["information_coefficient"] is not None]
    max_family_ic = max(abs_family_ics) if abs_family_ics else 0.0
    max_baseline_ic = max(abs_baseline_ics) if abs_baseline_ics else 0.0
    has_signal = max(max_family_ic, max_baseline_ic) >= FAINT_ABS_IC_FLOOR
    if not has_signal:
        return REC_FAILS
    # Macro + sentiment are intentionally not implemented (no local data) -> PARTIAL.
    if BLOCKED_FAMILIES:
        return REC_PARTIAL
    return REC_SUCCESS


def build_recommendation(recommendation, max_family_ic, max_baseline_ic):
    reasons = {
        REC_SUCCESS: (
            "Every signal family - including macro/inflation and sentiment - is implemented and "
            "the multi-signal feature factory shows measurable cross-sectional IC. A research-only "
            "multi-signal walk-forward model is permitted next. No production candidate, no "
            "deployment, no production edge claimed."),
        REC_PARTIAL: (
            "The feature factory succeeded for every locally-available family (technical, "
            "seasonality, market-regime, sector-relative, SEC fundamental, partial earnings, and "
            "local AR/ARIMA-style time-series) and shows measurable signal (max implemented-family "
            "|mean IC| = %s; max baseline |IC| = %s), but the macro/inflation and sentiment "
            "families remain unimplemented because no local macro or sentiment data exists yet "
            "(they are declared in the registry, NOT faked). A research-only multi-signal "
            "walk-forward model may proceed on the implemented families; macro and sentiment "
            "sources should be added in parallel. No production candidate, no deployment, no "
            "production edge claimed." % (max_family_ic, max_baseline_ic)),
        REC_BLOCKED: (
            "A required local input was missing (price panel, sector map, or the Phase 3-L aligned "
            "panel), so the feature factory could not be built. Repair the inputs before "
            "continuing. No data was fetched and no provider API was called."),
        REC_FAILS: (
            "Inputs were present and the factory built, but no implemented feature family or "
            "baseline ranking signal cleared even the faint cross-sectional IC floor (%s). The "
            "feature families need rework before any model training. No edge claimed."
            % FAINT_ABS_IC_FLOOR),
    }
    return {
        "recommendation": recommendation,
        "allowed_values": ALLOWED_RECOMMENDATIONS,
        "research_model_allowed_next": recommendation in (REC_SUCCESS, REC_PARTIAL),
        "add_macro_and_sentiment_next": recommendation == REC_PARTIAL,
        "create_production_model_candidate_now": False,
        "train_production_model_now": False,
        "compute_portfolio_weights_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "reason": reasons[recommendation],
    }


def build_recommended_next_phase(recommendation):
    table = {
        REC_SUCCESS: ("Research-Only Multi-Signal Walk-Forward Model",
                      "Train a research-only multi-signal walk-forward model on the implemented, "
                      "IC-bearing feature families. No production candidate."),
        REC_PARTIAL: ("Research-Only Multi-Signal Walk-Forward Model",
                      "Proceed to a research-only multi-signal walk-forward model on the "
                      "locally-available families; add macro and sentiment data sources in "
                      "parallel. No production candidate."),
        REC_BLOCKED: ("Repair Phase 3-O Inputs Before Modeling",
                      "Restore the missing local price/sector/Phase-3-L inputs before rebuilding "
                      "the feature factory."),
        REC_FAILS: ("Rework Feature Families Before Model Training",
                    "Revise the feature families; no family or baseline showed usable "
                    "cross-sectional signal."),
    }
    title, purpose = table[recommendation]
    return {"phase": "3-P", "title": title, "purpose": purpose}


# --------------------------------------------------------------------------- #
# Interpretation + safety flags
# --------------------------------------------------------------------------- #
def build_safety_flags():
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
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "deployable_model_artifact_written": False,
        "d_drive_read": True,
        "d_drive_written": False,
        "provider_api_called": False,
        "paid_vendor_api_called": False,
        "alpha_vantage_called": False,
        "sentiment_faked": False,
        "macro_faked": False,
        "labels_for_validation_only": True,
        "production_predictions_computed": False,
        "portfolio_weights_computed": False,
    }


def build_interpretation(recommendation):
    return {
        "phase_is_research_only": True,
        "research_baseline_models_computed": True,
        "research_baseline_models_are_non_deployable": True,
        "production_model_trained": False,
        "production_predictions_computed": False,
        "production_scores_computed": False,
        "portfolio_weights_computed": False,
        "order_instructions_created": False,
        "deployable_model_artifact_written": False,
        "labels_for_validation_only": True,
        "macro_family_faked": False,
        "sentiment_family_faked": False,
        "arima_is_one_signal_family_not_the_model": True,
        "sector_map_is_current_as_of_not_point_in_time": True,
        "results_remain_survivorship_biased": True,
        "production_edge_claimed": False,
        "research_model_allowed_next": recommendation in (REC_SUCCESS, REC_PARTIAL),
        "narrative": (
            "Phase 3-O is the first unified, research-only multi-signal feature factory. It reads "
            "the Phase 2K-G price panel READ ONLY on the D: drive, the current-as-of sector map, "
            "the Phase 3-L aligned SEC fundamental + forward-label panel, and the Phase 3-M partial "
            "earnings-surprise cache, and engineers technical/price, seasonality/calendar, "
            "market-regime, sector-relative, SEC-fundamental, earnings-surprise, and local "
            "AR/ARIMA-style time-series feature families. Macro/inflation and sentiment are "
            "declared in the registry but left unimplemented (external_*_data_required) and are "
            "NOT faked. It computes cross-sectional daily rank-IC diagnostics and a research-only "
            "baseline-model scoreboard. It performs no deployment, restarts no prediction service, "
            "enables no model-v2 serving flag, runs no migration, writes to no production database, "
            "executes no trade, fits no production model, creates no production model "
            "candidate, writes no deployable model artifact, computes no production predictions / "
            "scores / portfolio weights, writes nothing to the D: drive, and calls no Alpha "
            "Vantage / provider / paid-vendor / third-party market-data-vendor API. The universe "
            "is current-as-of, so "
            "all results remain survivorship-biased and claim no production edge."),
    }


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #
def _write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            out = {}
            for c in columns:
                v = r.get(c, "")
                out[c] = "" if v is None or (isinstance(v, float) and math.isnan(v)) else v
            w.writerow(out)


def _dump_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def _strip_private(rows):
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
ALL_PANEL_FEATURES = (TECHNICAL_FEATURES + SEASONALITY_FEATURES + REGIME_FEATURES +
                      SECTOR_FEATURES + SEC_FEATURES + EARNINGS_FEATURES + TIMESERIES_FEATURES)

SAMPLE_COLUMNS = (["ticker", "scoring_date", "sector"] +
                  ["return_21d", "return_63d", "return_126d", "momentum_21_126",
                   "volatility_21d", "drawdown_63d", "price_vs_sma_21d",
                   "historical_same_month_avg_return_by_ticker", "historical_month_rank_by_ticker",
                   "spy_return_63d", "market_risk_off_flag", "cross_sectional_breadth_21d",
                   "ticker_return_21d_minus_sector_return_21d",
                   "ticker_rank_within_sector_21d", "operating_margin", "net_margin",
                   "revenue_yoy_growth", "eps_diluted_yoy_growth", "eps_surprise_pct",
                   "trailing_4q_avg_surprise_pct", "ar1_beta_63d",
                   "rolling_mean_reversion_signal_21d", "trend_persistence_63d"] +
                  [LABEL_TEMPLATE % h for h in HORIZONS])


def run(result_json_path=RESULT_JSON, o_dir=_O_DIR, price_csv=PRICE_CSV,
        phase3l_panel_csv=PHASE3L_PANEL_CSV, earnings_csv=PHASE3M_EARNINGS_CSV, verbose=True):
    inputs = confirm_inputs()
    inputs_ok = (os.path.isfile(price_csv) and os.path.isfile(SECTOR_MAP_CSV)
                 and os.path.isfile(phase3l_panel_csv))

    out_paths = {
        "registry": os.path.join(o_dir, "feature_registry.csv"),
        "sample": os.path.join(o_dir, "research_feature_panel_sample.csv"),
        "technical": os.path.join(o_dir, "technical_feature_summary.csv"),
        "seasonality": os.path.join(o_dir, "seasonality_feature_summary.csv"),
        "regime": os.path.join(o_dir, "market_regime_feature_summary.csv"),
        "sector": os.path.join(o_dir, "sector_relative_feature_summary.csv"),
        "fundamental": os.path.join(o_dir, "fundamental_feature_summary.csv"),
        "earnings": os.path.join(o_dir, "earnings_feature_summary.csv"),
        "timeseries": os.path.join(o_dir, "time_series_feature_summary.csv"),
        "feature_ic": os.path.join(o_dir, "feature_ic_summary.csv"),
        "family_ic": os.path.join(o_dir, "feature_family_ic_summary.csv"),
        "scoreboard": os.path.join(o_dir, "baseline_model_scoreboard.csv"),
        "readiness": os.path.join(o_dir, "readiness_decision_table.csv"),
    }

    if not inputs_ok:
        return _finish_blocked(result_json_path, o_dir, out_paths, inputs)

    if verbose:
        print("  loading price panel (read-only) ...")
    sector_map = load_sector_map(SECTOR_MAP_CSV)
    px = load_price_panel(price_csv)
    if verbose:
        print("  computing technical + AR-style features ...")
    price_feats = compute_price_features(px)
    regime = compute_market_regime(price_feats)
    sector_rel = compute_sector_relative(price_feats, sector_map, regime)
    seas_hist = compute_seasonality_history(px)

    if verbose:
        print("  loading Phase 3-L aligned panel (spine) ...")
    spine = load_phase3l_panel(phase3l_panel_csv)

    # ---- assemble the unified panel on the (ticker, scoring_date) spine ----
    tech_cols = ["ticker", "date"] + TECHNICAL_FEATURES + TIMESERIES_FEATURES
    panel = spine.merge(
        price_feats[tech_cols], left_on=["ticker", "scoring_date"], right_on=["ticker", "date"],
        how="left").drop(columns=["date"])
    panel = panel.merge(regime, left_on="scoring_date", right_on="date", how="left").drop(
        columns=["date"])
    panel = panel.merge(sector_rel, left_on=["ticker", "scoring_date"],
                        right_on=["ticker", "date"], how="left").drop(columns=["date"])
    panel = add_calendar_features(panel)
    panel["year"] = panel["scoring_date"].dt.year.astype(int)
    panel["month"] = panel["scoring_date"].dt.month.astype(int)
    panel = panel.merge(seas_hist, on=["ticker", "year", "month"], how="left")
    earnings = load_earnings_features(earnings_csv)
    panel, earnings_covered = merge_earnings_asof(panel, earnings)

    panel = panel.sort_values(["scoring_date", "ticker"]).reset_index(drop=True)
    total_rows = len(panel)
    tickers = sorted(panel["ticker"].unique())
    date_min = panel["scoring_date"].min()
    date_max = panel["scoring_date"].max()

    # ---- non-null coverage ----
    non_null_frac = {}
    for f in ALL_PANEL_FEATURES:
        if f in panel.columns:
            non_null_frac[f] = float(panel[f].notna().mean())
        else:
            non_null_frac[f] = 0.0

    # ---- registry ----
    if verbose:
        print("  building feature registry ...")
    registry = build_feature_registry(IMPLEMENTED_FAMILIES)
    registry_columns = ["feature_name", "feature_family", "input_source", "point_in_time_rule",
                        "availability_lag_rule", "uses_future_data", "implemented", "blocker",
                        "notes"]

    # ---- IC diagnostics ----
    if verbose:
        print("  computing cross-sectional IC diagnostics ...")
    feature_ic_rows = compute_feature_ic(panel, non_null_frac)
    family_ic_rows = compute_family_ic_summary(feature_ic_rows)

    # ---- baseline scoreboard ----
    if verbose:
        print("  building baseline model scoreboard ...")
    scores = build_baseline_scores(panel)
    scoreboard_rows = build_scoreboard(panel, scores)
    scoreboard_summary = summarize_scoreboard(scoreboard_rows)

    # ---- per-family feature summaries ----
    summaries = {
        "technical": feature_summary_rows(panel, TECHNICAL_FEATURES),
        "seasonality": feature_summary_rows(panel, SEASONALITY_FEATURES),
        "regime": feature_summary_rows(panel, REGIME_FEATURES),
        "sector": feature_summary_rows(panel, SECTOR_FEATURES),
        "fundamental": feature_summary_rows(panel, SEC_FEATURES),
        "earnings": feature_summary_rows(panel, EARNINGS_FEATURES),
        "timeseries": feature_summary_rows(panel, TIMESERIES_FEATURES),
    }
    summary_columns = ["feature", "rows", "non_null_count", "non_null_fraction", "mean", "std",
                       "min", "median", "max"]

    # ---- decision ----
    abs_family_ics = [r["mean_abs_mean_ic"] for r in family_ic_rows
                      if r["implemented"] and r["mean_abs_mean_ic"] is not None]
    abs_baseline_ics = [abs(r["information_coefficient"]) for r in scoreboard_rows
                        if r["information_coefficient"] is not None]
    max_family_ic = _round(max(abs_family_ics), 6) if abs_family_ics else 0.0
    max_baseline_ic = _round(max(abs_baseline_ics), 6) if abs_baseline_ics else 0.0
    recommendation = decide(inputs_ok, family_ic_rows, scoreboard_rows)

    # ---- best feature families (by mean abs IC over horizons) ----
    fam_best = {}
    for r in family_ic_rows:
        if not r["implemented"] or r["mean_abs_mean_ic"] is None:
            continue
        prev = fam_best.get(r["feature_family"])
        if prev is None or r["mean_abs_mean_ic"] > prev:
            fam_best[r["feature_family"]] = r["mean_abs_mean_ic"]
    best_feature_families = [
        {"feature_family": k, "best_mean_abs_ic": v}
        for k, v in sorted(fam_best.items(), key=lambda kv: kv[1], reverse=True)]

    feature_count_by_family = {}
    for fam, feats in (
            (FAM_TECHNICAL, TECHNICAL_FEATURES), (FAM_SEASONALITY, SEASONALITY_FEATURES),
            (FAM_REGIME, REGIME_FEATURES), (FAM_SECTOR, SECTOR_FEATURES), (FAM_SEC, SEC_FEATURES),
            (FAM_EARNINGS, EARNINGS_FEATURES), (FAM_MACRO, MACRO_FEATURES),
            (FAM_SENTIMENT, SENTIMENT_FEATURES), (FAM_TIMESERIES, TIMESERIES_FEATURES)):
        feature_count_by_family[fam] = len(feats)

    # ---- readiness / decision table ----
    decision_rows = build_decision_table(
        inputs, total_rows, len(tickers), max_family_ic, max_baseline_ic, earnings_covered,
        recommendation)

    # ---- write artifacts ----
    if verbose:
        print("  writing artifacts ...")
    _write_csv(out_paths["registry"], registry_columns, registry)
    _write_csv(out_paths["technical"], summary_columns, summaries["technical"])
    _write_csv(out_paths["seasonality"], summary_columns, summaries["seasonality"])
    _write_csv(out_paths["regime"], summary_columns, summaries["regime"])
    _write_csv(out_paths["sector"], summary_columns, summaries["sector"])
    _write_csv(out_paths["fundamental"], summary_columns, summaries["fundamental"])
    _write_csv(out_paths["earnings"], summary_columns, summaries["earnings"])
    _write_csv(out_paths["timeseries"], summary_columns, summaries["timeseries"])
    _write_csv(out_paths["feature_ic"], [
        "feature", "feature_family", "horizon", "non_null_feature_fraction", "observation_count",
        "ic_date_count", "mean_rank_ic", "median_rank_ic", "ic_hit_rate", "ic_ir",
        "absolute_mean_ic"], _strip_private(feature_ic_rows))
    _write_csv(out_paths["family_ic"], [
        "feature_family", "horizon", "implemented", "cross_sectional", "num_features",
        "num_features_with_ic", "best_feature", "best_feature_abs_mean_ic", "best_feature_mean_ic",
        "mean_abs_mean_ic", "max_ic_date_count", "note"], _strip_private(family_ic_rows))
    _write_csv(out_paths["scoreboard"], [
        "model", "horizon", "sample_rows", "sample_tickers", "mean_forward_excess_return",
        "information_coefficient", "top_decile_minus_bottom_decile_spread", "hit_rate",
        "annual_coverage_years", "notes"], scoreboard_rows)
    _write_csv(out_paths["readiness"], ["decision_item", "value", "passed", "note"], decision_rows)
    _write_sample_panel(out_paths["sample"], panel)

    # ---- result JSON ----
    result = {
        "phase": PHASE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "price_history_csv": price_csv.replace("\\", "/"),
            "price_data_quality_json": PRICE_QUALITY_JSON.replace("\\", "/"),
            "price_survivorship_caveat_json": PRICE_SURVIVORSHIP_JSON.replace("\\", "/"),
            "sector_map_csv": "research/input/phase2k_p_sector_map_current.csv",
            "phase3l_result_json": "research/output/phase3l_sec_universe_signal_gate.json",
            "phase3l_aligned_panel_csv":
                "research/output/phase3l_sec_universe_signal_gate/"
                "aligned_feature_price_panel_universe.csv",
            "phase3m_result_json": "research/output/phase3m_earnings_estimates_signal_gate.json",
            "phase3m_earnings_features_csv":
                "research/output/phase3m_earnings_estimates_signal_gate/"
                "earnings_features_universe.csv",
            "phase3m_collection_progress_json":
                "research/output/phase3m_earnings_estimates_signal_gate/collection_progress.json",
        },
        "outputs_written": {
            "result_json": "research/output/phase3o_multisignal_feature_factory.json",
            "feature_registry_csv": _rel(out_paths["registry"]),
            "research_feature_panel_sample_csv": _rel(out_paths["sample"]),
            "technical_feature_summary_csv": _rel(out_paths["technical"]),
            "seasonality_feature_summary_csv": _rel(out_paths["seasonality"]),
            "market_regime_feature_summary_csv": _rel(out_paths["regime"]),
            "sector_relative_feature_summary_csv": _rel(out_paths["sector"]),
            "fundamental_feature_summary_csv": _rel(out_paths["fundamental"]),
            "earnings_feature_summary_csv": _rel(out_paths["earnings"]),
            "time_series_feature_summary_csv": _rel(out_paths["timeseries"]),
            "feature_ic_summary_csv": _rel(out_paths["feature_ic"]),
            "feature_family_ic_summary_csv": _rel(out_paths["family_ic"]),
            "baseline_model_scoreboard_csv": _rel(out_paths["scoreboard"]),
            "readiness_decision_table_csv": _rel(out_paths["readiness"]),
        },
        "input_confirmation": inputs,
        "implemented_feature_families": IMPLEMENTED_FAMILIES,
        "blocked_feature_families": [
            {"feature_family": FAM_MACRO, "blocker": "external_macro_data_required"},
            {"feature_family": FAM_SENTIMENT, "blocker": "external_sentiment_data_required"},
        ],
        "feature_count_by_family": feature_count_by_family,
        "implemented_feature_count": sum(
            feature_count_by_family[f] for f in IMPLEMENTED_FAMILIES),
        "rows_in_feature_panel": total_rows,
        "tickers_in_feature_panel": len(tickers),
        "tickers": tickers,
        "date_range": {"start": _dstr(date_min), "end": _dstr(date_max)},
        "earnings_family_coverage": {
            "cached_tickers_with_earnings_features": earnings_covered,
            "universe_tickers": len(tickers),
            "coverage_is_partial": True,
            "note": "only the Phase 3-M cached tickers carry earnings-surprise values; no fetch",
        },
        "ic_methodology": {
            "ic_type": "cross-sectional daily Spearman rank information coefficient",
            "labels": [LABEL_TEMPLATE % h for h in HORIZONS],
            "min_cross_section_per_date": IC_MIN_CROSS_SECTION,
            "grouping": "rows grouped by scoring_date; IC computed within each date, then "
                        "summarized across dates (same methodology as Phase 3-L / 3-M)",
            "hit_rate_definition": "fraction of dates whose IC has the same sign as the mean IC",
            "ic_ir_definition": "mean IC divided by the sample standard deviation of per-date ICs",
            "non_cross_sectional_families": [FAM_REGIME, "%s (calendar flags)" % FAM_SEASONALITY],
            "survivorship_caveat": "current-as-of universe; overlapping daily windows; ICs are "
                                   "diagnostic only and claim no edge.",
        },
        "feature_ic_summary": _strip_private(feature_ic_rows),
        "feature_family_ic_summary": _strip_private(family_ic_rows),
        "best_feature_families": best_feature_families,
        "baseline_model_scoreboard": scoreboard_rows,
        "baseline_model_scoreboard_summary": scoreboard_summary,
        "feature_registry_summary": {
            "total_features": len(registry),
            "implemented_features": sum(1 for r in registry if r["implemented"]),
            "unimplemented_features": sum(1 for r in registry if not r["implemented"]),
            "families": ALL_FAMILIES,
        },
        "macro_inflation_status": {
            "implemented": False, "faked": False, "blocker": "external_macro_data_required",
            "proposed_features": MACRO_FEATURES,
            "note": "declared in the registry for the roadmap; no local macro data exists yet"},
        "sentiment_status": {
            "implemented": False, "faked": False, "blocker": "external_sentiment_data_required",
            "proposed_features": SENTIMENT_FEATURES,
            "note": "declared in the registry for the roadmap; no local sentiment data exists yet"},
        "arima_style_status": {
            "implemented": True, "statsmodels_required": False,
            "statsmodels_optional_path_available": _statsmodels_available(),
            "features": TIMESERIES_FEATURES,
            "note": "local AR-style diagnostics computed without statsmodels; ARIMA is treated as "
                    "ONE signal family, not the whole model"},
        "max_implemented_family_abs_ic": max_family_ic,
        "max_baseline_abs_ic": max_baseline_ic,
        "recommendation": build_recommendation(recommendation, max_family_ic, max_baseline_ic),
        "recommended_next_phase": build_recommended_next_phase(recommendation),
        "interpretation": build_interpretation(recommendation),
    }
    result.update(build_safety_flags())
    _dump_json(result_json_path, result)
    return result


def _statsmodels_available():
    try:
        import statsmodels  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _rel(path):
    return os.path.relpath(path, _REPO_ROOT).replace("\\", "/")


def _dstr(ts):
    if ts is None or pd.isna(ts):
        return ""
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _write_sample_panel(path, panel):
    """Write a Git-safe (<50MB) strided sample of the unified panel."""
    dates = sorted(panel["scoring_date"].unique())
    if not dates:
        _write_csv(path, SAMPLE_COLUMNS, [])
        return
    per_date = max(1, len(panel) // max(1, len(dates)))
    target_dates = max(1, SAMPLE_MAX_ROWS // per_date)
    stride = max(1, len(dates) // target_dates)
    keep = set(dates[::stride])
    sample = panel[panel["scoring_date"].isin(keep)].copy()
    if len(sample) > SAMPLE_MAX_ROWS:
        sample = sample.iloc[:SAMPLE_MAX_ROWS]
    cols = [c for c in SAMPLE_COLUMNS if c in sample.columns]
    sample = sample[cols].copy()
    sample["scoring_date"] = sample["scoring_date"].dt.strftime("%Y-%m-%d")
    rows = sample.to_dict("records")
    _write_csv(path, cols, rows)


def build_decision_table(inputs, total_rows, n_tickers, max_family_ic, max_baseline_ic,
                         earnings_covered, recommendation):
    rows = []

    def add(item, value, passed, note):
        rows.append({"decision_item": item, "value": value,
                     "passed": "" if passed is None else bool(passed), "note": note})

    add("price_panel_present", inputs["price_panel_present"], inputs["price_panel_present"],
        "Phase 2K-G price panel readable on D: (read-only)")
    add("sector_map_present", inputs["sector_map_present"], inputs["sector_map_present"],
        "current-as-of sector map present")
    add("phase3l_aligned_panel_present", inputs["phase3l_aligned_panel_present"],
        inputs["phase3l_aligned_panel_present"], "Phase 3-L aligned SEC + label panel present")
    add("phase3m_collection_progress_present", inputs["phase3m_collection_progress_present"],
        inputs["phase3m_collection_progress_present"], "Phase 3-M collection progress present")
    add("earnings_cache_ticker_count", inputs["earnings_cache_ticker_count"], None,
        "recorded only; no new earnings data fetched")
    add("feature_panel_rows", total_rows, total_rows > 0, "unified research feature panel rows")
    add("feature_panel_tickers", n_tickers, n_tickers > 0, "tickers in the unified panel")
    add("implemented_feature_families", len(IMPLEMENTED_FAMILIES), len(IMPLEMENTED_FAMILIES) >= 5,
        "technical/seasonality/regime/sector/SEC/earnings/time-series implemented locally")
    add("blocked_feature_families", len(BLOCKED_FAMILIES), None,
        "macro_inflation + sentiment: external_*_data_required (declared, not faked)")
    add("earnings_family_partial_coverage", earnings_covered, None,
        "tickers carrying earnings-surprise values (Phase 3-M cache only)")
    add("max_implemented_family_abs_ic", max_family_ic, max_family_ic >= FAINT_ABS_IC_FLOOR,
        "FAILS if no implemented family clears the faint IC floor (%s)" % FAINT_ABS_IC_FLOOR)
    add("max_baseline_abs_ic", max_baseline_ic, None, "best baseline ranking signal |IC|")
    add("no_production_model_trained", True, True, "research-only baselines; no production model")
    add("no_portfolio_weights", True, True, "no portfolio weights computed")
    add("no_production_predictions", True, True, "no production predictions/scores computed")
    add("d_drive_written", False, True, "wrote nothing to the D: drive")
    add("alpha_vantage_called", False, True, "no provider / Alpha Vantage call")
    add("recommendation", recommendation, recommendation in ALLOWED_RECOMMENDATIONS,
        "allowed values only")
    return rows


def _finish_blocked(result_json_path, o_dir, out_paths, inputs):
    """Required inputs missing: write a minimal blocked result + empty artifacts."""
    registry = build_feature_registry(IMPLEMENTED_FAMILIES)
    registry_columns = ["feature_name", "feature_family", "input_source", "point_in_time_rule",
                        "availability_lag_rule", "uses_future_data", "implemented", "blocker",
                        "notes"]
    _write_csv(out_paths["registry"], registry_columns, registry)
    summary_columns = ["feature", "rows", "non_null_count", "non_null_fraction", "mean", "std",
                       "min", "median", "max"]
    for key, feats in (("technical", TECHNICAL_FEATURES), ("seasonality", SEASONALITY_FEATURES),
                       ("regime", REGIME_FEATURES), ("sector", SECTOR_FEATURES),
                       ("fundamental", SEC_FEATURES), ("earnings", EARNINGS_FEATURES),
                       ("timeseries", TIMESERIES_FEATURES)):
        _write_csv(out_paths[key], summary_columns,
                   [{"feature": f, "rows": 0, "non_null_count": 0, "non_null_fraction": 0.0}
                    for f in feats])
    _write_csv(out_paths["feature_ic"], [
        "feature", "feature_family", "horizon", "non_null_feature_fraction", "observation_count",
        "ic_date_count", "mean_rank_ic", "median_rank_ic", "ic_hit_rate", "ic_ir",
        "absolute_mean_ic"], [])
    _write_csv(out_paths["family_ic"], [
        "feature_family", "horizon", "implemented", "cross_sectional", "num_features",
        "num_features_with_ic", "best_feature", "best_feature_abs_mean_ic", "best_feature_mean_ic",
        "mean_abs_mean_ic", "max_ic_date_count", "note"], [])
    _write_csv(out_paths["scoreboard"], [
        "model", "horizon", "sample_rows", "sample_tickers", "mean_forward_excess_return",
        "information_coefficient", "top_decile_minus_bottom_decile_spread", "hit_rate",
        "annual_coverage_years", "notes"], [])
    decision_rows = build_decision_table(inputs, 0, 0, 0.0, 0.0, 0, REC_BLOCKED)
    _write_csv(out_paths["readiness"], ["decision_item", "value", "passed", "note"], decision_rows)
    _write_csv(out_paths["sample"], SAMPLE_COLUMNS, [])
    result = {
        "phase": PHASE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_confirmation": inputs,
        "implemented_feature_families": IMPLEMENTED_FAMILIES,
        "blocked_feature_families": [
            {"feature_family": FAM_MACRO, "blocker": "external_macro_data_required"},
            {"feature_family": FAM_SENTIMENT, "blocker": "external_sentiment_data_required"}],
        "feature_count_by_family": {f: len(v) for f, v in (
            (FAM_TECHNICAL, TECHNICAL_FEATURES), (FAM_SEASONALITY, SEASONALITY_FEATURES),
            (FAM_REGIME, REGIME_FEATURES), (FAM_SECTOR, SECTOR_FEATURES), (FAM_SEC, SEC_FEATURES),
            (FAM_EARNINGS, EARNINGS_FEATURES), (FAM_MACRO, MACRO_FEATURES),
            (FAM_SENTIMENT, SENTIMENT_FEATURES), (FAM_TIMESERIES, TIMESERIES_FEATURES))},
        "rows_in_feature_panel": 0,
        "tickers_in_feature_panel": 0,
        "date_range": {"start": "", "end": ""},
        "best_feature_families": [],
        "baseline_model_scoreboard": [],
        "baseline_model_scoreboard_summary": {"model_count": 0},
        "macro_inflation_status": {"implemented": False, "faked": False,
                                   "blocker": "external_macro_data_required",
                                   "proposed_features": MACRO_FEATURES},
        "sentiment_status": {"implemented": False, "faked": False,
                             "blocker": "external_sentiment_data_required",
                             "proposed_features": SENTIMENT_FEATURES},
        "arima_style_status": {"implemented": True, "statsmodels_required": False,
                               "features": TIMESERIES_FEATURES},
        "recommendation": build_recommendation(REC_BLOCKED, 0.0, 0.0),
        "recommended_next_phase": build_recommended_next_phase(REC_BLOCKED),
        "interpretation": build_interpretation(REC_BLOCKED),
    }
    result.update(build_safety_flags())
    _dump_json(result_json_path, result)
    return result


def main():
    result = run()
    rec = result["recommendation"]
    nxt = result["recommended_next_phase"]
    print("Phase %s - Multi-Signal Feature Factory + Research Baseline Model Gate" % result["phase"])
    print("  implemented families : %s" % ", ".join(result["implemented_feature_families"]))
    print("  blocked families     : %s"
          % ", ".join(b["feature_family"] for b in result["blocked_feature_families"]))
    print("  feature panel        : %s rows x %s tickers"
          % (result["rows_in_feature_panel"], result["tickers_in_feature_panel"]))
    print("  date range           : %s -> %s"
          % (result["date_range"]["start"], result["date_range"]["end"]))
    print("  best feature families: %s"
          % [(b["feature_family"], b["best_mean_abs_ic"])
             for b in result.get("best_feature_families", [])[:4]])
    print("  scoreboard best IC   : %s" % result["baseline_model_scoreboard_summary"].get(
        "best_by_abs_ic"))
    print("  scoreboard best sprd : %s" % result["baseline_model_scoreboard_summary"].get(
        "best_by_decile_spread"))
    print("  macro status         : implemented=%s faked=%s"
          % (result["macro_inflation_status"]["implemented"],
             result["macro_inflation_status"]["faked"]))
    print("  sentiment status     : implemented=%s faked=%s"
          % (result["sentiment_status"]["implemented"], result["sentiment_status"]["faked"]))
    print("  recommendation       : %s" % rec["recommendation"])
    print("  recommended next     : %s - %s" % (nxt["phase"], nxt["title"]))
    return result


if __name__ == "__main__":
    main()
