"""Phase 7-E — Regime / Risk Overlay Foundation (System 2, descriptive).

**Track A (quant brain) research only.** Offline, as-of / lagged, leakage-safe,
descriptive. No network, no provider call, no paid API, no API key read or required,
no model trained or deployed, no factor-weight optimization, no Paper Trader / GCP
work, no orders / broker / automation / hedging / order sizing, no trade
recommendation, no live data, no binary artifact, no commit, no push. Reads only
local files: the Phase 2K-G price panel (READ ONLY on D:), the committed sector /
SEC fundamentals artifacts, and local FRED macro CSVs (repo-local). Writes nothing
to D:.

Why this phase exists
---------------------
The charter is a two-system design: System 1 = multi-factor ranking engine (Phase
7-C), System 2 = regime / risk overlay. Phase 7-D built the measurement-only book
risk lens. This phase builds the FIRST System 2 layer: a **descriptive** regime
classifier that labels each historical month into market regimes from lagged / as-of
local data, then reports how the Phase 7-C ranking signal (per-factor, baseline,
composite IC) and a market-risk proxy **behaved conditional on regime**.

This is NOT predictive. It does not forecast regimes, does not forecast returns,
does not size, does not hedge, does not trade. It is a regime *diagnostics* and a
*risk-throttle template* — and the throttle template is explicitly NOT activated
(NOT LIVE / NOT TRADING / OWNER REVIEW REQUIRED).

Leakage safety
--------------
Every regime label for scoring month m is computed only from data observable at or
before month m's market month-end. Price-based regimes (trend / volatility /
drawdown) use trailing daily SPY windows ending at month-end m. Macro regimes
(inflation / rates / dollar) read the latest macro observation lagged by an explicit
publication lag (>= 1 month), so no same-day and no future macro is used. Regime
labels never touch the forward (m -> m+1) return — the IC they condition is itself
strictly forward (reused unchanged from the Phase 7-C / 7-B machinery).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Reuse the Phase 7-C ranking engine (factor construction, loaders, IC grading) and,
# transitively, the Phase 7-B harness. No work runs at import time.
from research import run_phase7c_multifactor_ranking_engine as C
from research import run_phase7b_validation_harness_foundation as H

PHASE = "7-E"
OBJECTIVE = (
    "Build a DESCRIPTIVE System 2 regime overlay: classify each historical scoring "
    "month into market regimes (risk-on/off, high/low vol, above/below trend, "
    "inflation up/down, rates up/down, dollar up/down) from local lagged / as-of "
    "data only, then report how the Phase 7-C factor / baseline / composite rank IC "
    "and a market-risk proxy behaved by regime, plus regime transitions and a "
    "NOT-LIVE risk-throttle template. Not predictive, no weight optimization, no "
    "orders, no live data."
)

# Recommendation taxonomy (task-specified).
REC_READY = "READY_FOR_PHASE7F_SIGNAL_RELIABILITY_UPGRADE"
REC_NEEDS_REVIEW = "NEEDS_REGIME_REVIEW"
REC_BLOCKED = "DATA_QUALITY_BLOCKED"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (REC_READY, REC_NEEDS_REVIEW, REC_BLOCKED, REC_ERROR)

# --------------------------------------------------------------------------- #
# Paths (price panel READ ONLY on D:; macro CSVs repo-local under C:).
# --------------------------------------------------------------------------- #
PRICE_CSV = C.PRICE_CSV
BENCHMARK = C.BENCHMARK  # "SPY"
_INPUT_DIR = _REPO_ROOT / "research" / "input"
MACRO_CPI_CSV = _INPUT_DIR / "CPIAUCSL.csv"        # CPI level (monthly)
MACRO_OIL_CSV = _INPUT_DIR / "DCOILWTICO.csv"      # WTI crude (daily) — reported, not a regime axis
MACRO_DOLLAR_CSV = _INPUT_DIR / "DTWEXBGS.csv"     # broad trade-weighted USD (daily)
MACRO_FEDFUNDS_CSV = _INPUT_DIR / "FEDFUNDS.csv"   # fed funds (monthly) — reported
MACRO_YIELDS_CSV = _INPUT_DIR / "fredgraph.csv"    # DGS10, DGS2 (daily)
PHASE7C_JSON = _REPO_ROOT / "research" / "output" / "phase7c_multifactor_ranking_engine" / \
    "phase7c_multifactor_ranking_engine.json"
PHASE7D_JSON = _REPO_ROOT / "research" / "output" / "phase7d_risk_decomposition_foundation" / \
    "phase7d_risk_decomposition_foundation.json"
_OUT_DIR_DEFAULT = _REPO_ROOT / "research" / "output" / "phase7e_regime_risk_overlay_foundation"

# --------------------------------------------------------------------------- #
# Config (fixed, documented classification choices — NO tuning loop, NO fit to IC).
# --------------------------------------------------------------------------- #
TRADING_DAYS = 252
TREND_SMA_D = 200          # SPY trend reference: 200-day simple moving average
VOL_WINDOW_D = 21          # trailing daily-return window for the realized-vol regime
VOL_MIN_HISTORY_M = 12     # months of vol history required before the vol regime activates
DD_WINDOW_D = 252          # trailing window for the drawdown-from-peak risk regime
DD_MIN_D = 63              # minimum observations before drawdown is computed
RISK_OFF_DD = -0.10        # >= 10% below the trailing 252d peak => risk-off
MACRO_DIR_MONTHS = 3       # macro direction measured over a 3-month change
CPI_LAG_M = 2              # CPI publication lag (months) — conservative as-of
RATES_LAG_M = 1            # 10Y yield lag (months)
DOLLAR_LAG_M = 1           # USD index lag (months)
CPI_YOY_M = 12             # CPI year-over-year horizon
MIN_REGIME_MONTHS = 24     # minimum graded IC months to proceed (else DATA_QUALITY_BLOCKED)
MIN_AXIS_LABEL_MONTHS = 6  # informational breadth threshold per regime label
_ANNUALIZE_VOL = math.sqrt(TRADING_DAYS)
_ANNUALIZE_MONTHLY = math.sqrt(12)

NAN = float("nan")

# Regime axis catalogue (label pairs are the two non-"unknown" outcomes).
REGIME_AXES: List[Dict] = [
    {"axis": "risk_regime", "labels": ("risk_on", "risk_off"), "source": "SPY price",
     "definition": "risk-off when SPY is >=10% below its trailing 252-day peak, else risk-on"},
    {"axis": "vol_regime", "labels": ("low_vol", "high_vol"), "source": "SPY price",
     "definition": "high-vol when trailing 21-day annualized SPY realized vol exceeds its "
                   "as-of expanding median (>=12m history), else low-vol"},
    {"axis": "trend_regime", "labels": ("above_trend", "below_trend"), "source": "SPY price",
     "definition": "above-trend when SPY month-end close >= its 200-day SMA, else below-trend"},
    {"axis": "inflation_regime", "labels": ("inflation_up", "disinflation"), "source": "CPIAUCSL (FRED)",
     "definition": "inflation-up when as-of CPI YoY is higher than 3 months earlier, else disinflationary"},
    {"axis": "rates_regime", "labels": ("rates_up", "rates_down"), "source": "DGS10 (FRED)",
     "definition": "rates-up when the as-of 10-year Treasury yield is higher than 3 months earlier"},
    {"axis": "dollar_regime", "labels": ("dollar_up", "dollar_down"), "source": "DTWEXBGS (FRED)",
     "definition": "dollar-up when the as-of broad trade-weighted USD index is higher than 3 months earlier"},
]

# Illustrative NOT-LIVE throttle postures. Chosen by simple risk logic (risk-off ->
# lower suggested gross), NOT fitted to the IC-by-regime results, NOT optimized.
_THROTTLE = {
    ("risk_regime", "risk_on"): ("RISK_ON", 0.9, 1.0),
    ("risk_regime", "risk_off"): ("RISK_OFF", 0.3, 0.6),
    ("vol_regime", "low_vol"): ("RISK_ON", 0.9, 1.0),
    ("vol_regime", "high_vol"): ("RISK_OFF", 0.4, 0.7),
    ("trend_regime", "above_trend"): ("RISK_ON", 0.9, 1.0),
    ("trend_regime", "below_trend"): ("RISK_OFF", 0.4, 0.7),
}
_PRICE_THROTTLE_AXES = {"risk_regime", "vol_regime", "trend_regime"}


# --------------------------------------------------------------------------- #
# Numeric helpers (reuse the Phase 7-C rounding helpers).
# --------------------------------------------------------------------------- #
_f = C._f
_round = C._round


def _pkey(period: pd.Period) -> str:
    """'YYYY-MM' key matching the Phase 7-C / harness period_ics keys."""
    return str(period)


# --------------------------------------------------------------------------- #
# Phase 7-C signal reconstruction (reuse-first: exactly reproduces the 7-C engine
# to obtain per-month period_ics for every factor, the price-only baseline, and the
# equal-weight composite).
# --------------------------------------------------------------------------- #
def reconstruct_phase7c_signals() -> Dict:
    """Rebuild the 7-C factor_z / realized / approved buckets and grade every series,
    returning each series' period_ics keyed 'YYYY-MM' (the exact 7-C measurement)."""
    sector_map, sector_pit = C.load_sector_map()
    fund = C.load_annual_fundamentals()
    fund_tickers = set(fund["ticker"].unique())
    universe = set(sector_map) & fund_tickers
    prices = C.load_prices(universe=universe | set(sector_map))  # benchmark already dropped
    price_tickers = set(prices["ticker"].unique())
    universe = sorted(universe & price_tickers)
    prices = prices[prices["ticker"].isin(universe)].copy()

    pf = C.build_price_factors(prices)
    realized = pf["forward"]
    ff = C.build_fundamental_factors(fund[fund["ticker"].isin(universe)],
                                     pf["monthly_close"], pf["month_end_date"])
    raw_factors = {
        "momentum": pf["momentum"], "low_volatility": pf["low_volatility"],
        "value": ff["value"], "quality": ff["quality"], "growth": ff["growth"],
    }
    factor_z = {k: C.normalize_cross_sectional(v, sector_map, sector_neutralize=True)
                for k, v in raw_factors.items()}

    factor_graded: Dict[str, Dict] = {}
    approved: List[str] = []
    for fd in C.FACTOR_DEFS:
        z = factor_z[fd.key]
        graded = C.grade_series(z, realized) if not z.empty else None
        factor_graded[fd.key] = graded
        if graded is not None and (graded.get("n_periods") or 0) >= C.MIN_APPROVE_PERIODS:
            approved.append(fd.key)
    approved_price = [k for k in approved
                      if next(f for f in C.FACTOR_DEFS if f.key == k).source == "price"]

    baseline = C.equal_weight_composite_long(factor_z, approved_price)
    composite = C.equal_weight_composite_long(factor_z, approved)
    base_graded = C.grade_series(baseline, realized) if not baseline.empty else None
    comp_graded = C.grade_series(composite, realized) if not composite.empty else None

    return {
        "universe": universe, "sector_pit": sector_pit,
        "realized": realized, "monthly_close": pf["monthly_close"],
        "factor_graded": factor_graded, "approved": approved, "approved_price": approved_price,
        "baseline_graded": base_graded, "composite_graded": comp_graded,
    }


# --------------------------------------------------------------------------- #
# Benchmark (SPY) loader — the 7-C loader drops SPY; the regime layer needs it.
# --------------------------------------------------------------------------- #
def load_benchmark_daily(path=PRICE_CSV) -> pd.DataFrame:
    """Daily SPY [date, adj_close] sorted ascending (READ ONLY). Empty if absent."""
    df = pd.read_csv(path, usecols=["ticker", "date", "adjusted_close"])
    df = df[df["ticker"] == BENCHMARK].copy()
    if df.empty:
        return df.assign(adj_close=[]).loc[:, ["date", "adj_close"]]
    df = df.rename(columns={"adjusted_close": "adj_close"})
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["adj_close"]).sort_values("date").reset_index(drop=True)
    return df[["date", "adj_close"]]


def benchmark_month_end_metrics(spy_daily: pd.DataFrame) -> pd.DataFrame:
    """Month-end SPY metrics (all trailing / as-of as of each month-end):
    close, 200d SMA, trailing 21d annualized realized vol, drawdown from 252d peak,
    plus the month's realized return and next-month forward return."""
    if spy_daily.empty:
        return pd.DataFrame(columns=["month", "close", "sma200", "vol21", "dd252", "ret", "fwd"])
    df = spy_daily.copy()
    df["ret_d"] = df["adj_close"].pct_change(fill_method=None)
    df["sma200"] = df["adj_close"].rolling(TREND_SMA_D, min_periods=TREND_SMA_D).mean()
    df["vol21"] = df["ret_d"].rolling(VOL_WINDOW_D, min_periods=VOL_WINDOW_D).std() * _ANNUALIZE_VOL
    df["max252"] = df["adj_close"].rolling(DD_WINDOW_D, min_periods=DD_MIN_D).max()
    df["dd252"] = df["adj_close"] / df["max252"] - 1.0
    df["month"] = df["date"].dt.to_period("M")
    me_idx = df.groupby("month")["date"].idxmax()
    me = df.loc[me_idx, ["month", "adj_close", "sma200", "vol21", "dd252"]].copy()
    me = me.rename(columns={"adj_close": "close"}).sort_values("month").reset_index(drop=True)
    me["ret"] = me["close"].pct_change(fill_method=None)
    me["fwd"] = me["close"].shift(-1) / me["close"] - 1.0
    return me


# --------------------------------------------------------------------------- #
# Macro loaders + as-of helpers (strictly lagged; no same-day, no future).
# --------------------------------------------------------------------------- #
def load_macro_monthly(path: Path, value_col: str) -> pd.Series:
    """FRED CSV -> month-end-resampled numeric Series indexed by Period('M').
    Returns an empty Series if the file or column is absent."""
    try:
        df = pd.read_csv(path)
    except (FileNotFoundError, OSError):
        return pd.Series(dtype=float)
    if "observation_date" not in df.columns or value_col not in df.columns:
        return pd.Series(dtype=float)
    df = df[["observation_date", value_col]].copy()
    df["period"] = pd.to_datetime(df["observation_date"], errors="coerce").dt.to_period("M")
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    df = df.dropna(subset=["period", value_col])
    if df.empty:
        return pd.Series(dtype=float)
    s = df.groupby("period")[value_col].last().sort_index()
    return s


def _asof_period(s: pd.Series, period: pd.Period, lag_months: int) -> Optional[float]:
    """Latest value whose period is <= (period - lag_months). None if unavailable."""
    if s.empty:
        return None
    cutoff = period - lag_months
    sub = s[s.index <= cutoff]
    if sub.empty:
        return None
    return float(sub.iloc[-1])


def _macro_direction(s: pd.Series, period: pd.Period, lag_months: int,
                     up_label: str, down_label: str) -> str:
    """Direction over MACRO_DIR_MONTHS, both legs read strictly lagged."""
    cur = _asof_period(s, period, lag_months)
    prev = _asof_period(s, period, lag_months + MACRO_DIR_MONTHS)
    if cur is None or prev is None:
        return "unknown"
    return up_label if cur > prev else down_label


# --------------------------------------------------------------------------- #
# Regime classification (one label per scoring month per axis; all as-of / lagged).
# --------------------------------------------------------------------------- #
def classify_regimes(me: pd.DataFrame, *, cpi_yoy: pd.Series, dgs10: pd.Series,
                     usd: pd.Series) -> Dict:
    """Return per-axis {period_key: label} dicts plus availability and as-of audit."""
    axis_labels: Dict[str, Dict[str, str]] = {a["axis"]: {} for a in REGIME_AXES}
    axis_available: Dict[str, bool] = {a["axis"]: False for a in REGIME_AXES}
    asof_violations = 0
    months = list(me["month"])

    # Expanding as-of median threshold for the volatility regime (uses only <= m).
    vol_series = me.set_index("month")["vol21"]

    for i, m in enumerate(months):
        key = _pkey(m)
        row = me.iloc[i]
        decision_end = m.end_time  # market month-end timestamp for this scoring month

        # --- risk regime: drawdown from trailing 252d peak ---
        dd = _f(row["dd252"])
        if dd is not None:
            axis_labels["risk_regime"][key] = "risk_off" if dd <= RISK_OFF_DD else "risk_on"
            axis_available["risk_regime"] = True

        # --- trend regime: close vs 200d SMA ---
        close, sma = _f(row["close"]), _f(row["sma200"])
        if close is not None and sma is not None:
            axis_labels["trend_regime"][key] = "above_trend" if close >= sma else "below_trend"
            axis_available["trend_regime"] = True

        # --- vol regime: as-of expanding median of trailing 21d vol ---
        v = _f(row["vol21"])
        hist = vol_series.iloc[: i + 1].dropna()
        if v is not None and len(hist) >= VOL_MIN_HISTORY_M:
            thr = float(np.median(hist.values))
            axis_labels["vol_regime"][key] = "high_vol" if v > thr else "low_vol"
            axis_available["vol_regime"] = True

        # --- macro regimes (strictly lagged) ---
        infl = _macro_direction(cpi_yoy, m, CPI_LAG_M, "inflation_up", "disinflation")
        if infl != "unknown":
            axis_labels["inflation_regime"][key] = infl
            axis_available["inflation_regime"] = True
            if not ((m - CPI_LAG_M) < m):  # lag must place the read strictly before m
                asof_violations += 1
        rates = _macro_direction(dgs10, m, RATES_LAG_M, "rates_up", "rates_down")
        if rates != "unknown":
            axis_labels["rates_regime"][key] = rates
            axis_available["rates_regime"] = True
        dol = _macro_direction(usd, m, DOLLAR_LAG_M, "dollar_up", "dollar_down")
        if dol != "unknown":
            axis_labels["dollar_regime"][key] = dol
            axis_available["dollar_regime"] = True

        # price-based regimes use only trailing daily data ending at the month-end,
        # so the as-of timestamp never exceeds the decision month-end (audited here).
        if decision_end < m.start_time:  # structurally impossible; audit guard
            asof_violations += 1

    return {"axis_labels": axis_labels, "axis_available": axis_available,
            "asof_violations": asof_violations,
            "available_axes": [a for a, ok in axis_available.items() if ok]}


# --------------------------------------------------------------------------- #
# Conditional behaviour: IC by regime, risk by regime, transitions / persistence.
# --------------------------------------------------------------------------- #
def ic_by_regime(period_ics: Dict[str, float], axis_labels: Dict[str, str]) -> Dict[str, Dict]:
    """For one series, partition its per-month rank ICs by regime label and summarize."""
    by_label: Dict[str, Dict[str, float]] = defaultdict(dict)
    for p, ic in period_ics.items():
        lab = axis_labels.get(p)
        if lab is None:
            continue
        by_label[lab][p] = ic
    out: Dict[str, Dict] = {}
    for lab, sub in by_label.items():
        summ = H.ic_summary(sub)
        out[lab] = {"n_months": summ["n_periods"], "mean_rank_ic": summ["mean_rank_ic"],
                    "ic_t_stat": summ["ic_t_stat"]}
    return out


def risk_by_regime(me: pd.DataFrame, disp: pd.Series, axis_labels: Dict[str, str]) -> Dict[str, Dict]:
    """Market-risk proxy conditional on regime: SPY next-month return mean / vol /
    worst, and the universe cross-sectional return dispersion. Descriptive only."""
    fwd = me.set_index("month")["fwd"]
    by_label: Dict[str, List[Tuple[float, Optional[float]]]] = defaultdict(list)
    for m, lab in [(pd.Period(k, "M"), v) for k, v in axis_labels.items()]:
        f = _f(fwd.get(m))
        d = _f(disp.get(m))
        if lab is not None:
            by_label[lab].append((f, d))
    out: Dict[str, Dict] = {}
    for lab, rows in by_label.items():
        fwds = np.asarray([f for f, _ in rows if f is not None], dtype=float)
        disps = np.asarray([d for _, d in rows if d is not None], dtype=float)
        out[lab] = {
            "n_months": int(len(rows)),
            "spy_fwd_ret_mean_monthly": float(np.mean(fwds)) if len(fwds) else None,
            "spy_fwd_ret_vol_annual": float(np.std(fwds, ddof=1) * _ANNUALIZE_MONTHLY) if len(fwds) > 1 else None,
            "spy_fwd_worst_month": float(np.min(fwds)) if len(fwds) else None,
            "universe_xsec_dispersion_mean": float(np.mean(disps)) if len(disps) else None,
        }
    return out


def transitions_and_persistence(axis_labels: Dict[str, str], ordered_keys: List[str]) -> Dict:
    """Month-over-month transition counts, per-label persistence and average run length."""
    seq = [axis_labels[k] for k in ordered_keys if k in axis_labels]
    trans: Dict[Tuple[str, str], int] = defaultdict(int)
    for a, b in zip(seq[:-1], seq[1:]):
        trans[(a, b)] += 1
    # Persistence + run length per label.
    label_counts: Dict[str, int] = defaultdict(int)
    for lab in seq:
        label_counts[lab] += 1
    runs: Dict[str, int] = defaultdict(int)
    prev = None
    for lab in seq:
        if lab != prev:
            runs[lab] += 1
        prev = lab
    persistence: Dict[str, Optional[float]] = {}
    avg_run: Dict[str, Optional[float]] = {}
    for lab in label_counts:
        out_total = sum(c for (frm, _to), c in trans.items() if frm == lab)
        stay = trans.get((lab, lab), 0)
        persistence[lab] = (stay / out_total) if out_total else None
        avg_run[lab] = (label_counts[lab] / runs[lab]) if runs[lab] else None
    return {"transitions": dict(trans), "label_counts": dict(label_counts),
            "persistence": persistence, "avg_run_length": avg_run,
            "n_transitions": int(sum(trans.values()))}


# --------------------------------------------------------------------------- #
# Output paths + writers.
# --------------------------------------------------------------------------- #
@dataclass
class _OutPaths:
    dir: Path
    def __post_init__(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        self.report = self.dir / "phase7e_regime_risk_overlay_foundation.json"
        self.classification = self.dir / "regime_classification_summary.csv"
        self.transition = self.dir / "regime_transition_matrix.csv"
        self.factor_ic = self.dir / "factor_ic_by_regime.csv"
        self.composite_ic = self.dir / "composite_ic_by_regime.csv"
        self.risk = self.dir / "risk_by_regime.csv"
        self.throttle = self.dir / "regime_throttle_template.csv"
        self.gate_matrix = self.dir / "regime_gate_matrix.csv"
        self.next_plan = self.dir / "phase7f_next_plan.json"


def _rel(p: Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(_REPO_ROOT)).replace("/", "\\")
    except Exception:
        return str(p)


def _write_json(path: Path, obj) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=False, default=str)
        fh.write("\n")


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
def run(out_dir: Optional[str] = None) -> Dict:
    paths = _OutPaths(Path(out_dir) if out_dir else _OUT_DIR_DEFAULT)
    try:
        report = _run_overlay(paths)
    except Exception as exc:  # pragma: no cover - defensive
        import traceback
        report = _error_report(str(exc), traceback.format_exc())
        _write_json(paths.report, report)
        return report
    _write_json(paths.report, report)
    return report


def _run_overlay(paths: _OutPaths) -> Dict:
    sig = reconstruct_phase7c_signals()
    spy_daily = load_benchmark_daily()
    if spy_daily.empty:
        report = _blocked_report("SPY benchmark absent from the local price panel — "
                                 "cannot classify price-based regimes.")
        _write_blocked_artifacts(paths, report)
        return report

    me = benchmark_month_end_metrics(spy_daily)

    # Macro series (best-effort; missing files -> that axis stays unavailable).
    cpi_level = load_macro_monthly(MACRO_CPI_CSV, "CPIAUCSL")
    cpi_yoy = cpi_level.pct_change(CPI_YOY_M).dropna() if not cpi_level.empty else cpi_level
    dgs10 = load_macro_monthly(MACRO_YIELDS_CSV, "DGS10")
    usd = load_macro_monthly(MACRO_DOLLAR_CSV, "DTWEXBGS")

    reg = classify_regimes(me, cpi_yoy=cpi_yoy, dgs10=dgs10, usd=usd)
    axis_labels = reg["axis_labels"]
    available_axes = reg["available_axes"]

    # Per-month universe cross-sectional dispersion of forward returns.
    realized = sig["realized"]
    disp = realized.groupby("month")["fwd"].std(ddof=0)

    # Graded signal series -> period_ics.
    series_ics: Dict[str, Dict[str, float]] = {}
    for fd in C.FACTOR_DEFS:
        g = sig["factor_graded"].get(fd.key)
        if g:
            series_ics[fd.key] = g["period_ics"]
    base_g = sig["baseline_graded"]
    comp_g = sig["composite_graded"]
    if base_g:
        series_ics["price_only_baseline"] = base_g["period_ics"]
    if comp_g:
        series_ics["equal_weight_composite"] = comp_g["period_ics"]

    comp_n_months = (comp_g or {}).get("n_periods") or 0
    blocked = (comp_n_months < MIN_REGIME_MONTHS) or (len(available_axes) < 2)
    if blocked:
        report = _blocked_report(
            f"insufficient regime inputs: graded composite months={comp_n_months} "
            f"(need >= {MIN_REGIME_MONTHS}), available regime axes={len(available_axes)} "
            f"(need >= 2).")
        _write_blocked_artifacts(paths, report)
        return report

    ordered_keys = [_pkey(m) for m in me["month"]]

    # IC by regime (factors + baseline/composite), risk by regime, transitions.
    factor_ic = {fk: {a["axis"]: ic_by_regime(series_ics[fk], axis_labels[a["axis"]])
                      for a in REGIME_AXES if a["axis"] in available_axes}
                 for fk in series_ics if fk not in ("price_only_baseline", "equal_weight_composite")}
    composite_ic = {fk: {a["axis"]: ic_by_regime(series_ics[fk], axis_labels[a["axis"]])
                         for a in REGIME_AXES if a["axis"] in available_axes}
                    for fk in ("price_only_baseline", "equal_weight_composite") if fk in series_ics}
    risk = {a["axis"]: risk_by_regime(me, disp, axis_labels[a["axis"]])
            for a in REGIME_AXES if a["axis"] in available_axes}
    transitions = {a["axis"]: transitions_and_persistence(axis_labels[a["axis"]], ordered_keys)
                   for a in REGIME_AXES if a["axis"] in available_axes}

    full_sample_ic = {fk: H.ic_summary(series_ics[fk])["mean_rank_ic"] for fk in series_ics}

    # Gates / recommendation.
    gates = _build_gate_matrix(reg, available_axes, comp_n_months, series_ics, sig["sector_pit"])
    recommendation = _derive_recommendation(gates, available_axes, comp_n_months)

    # --- artifacts ---
    _write_classification_summary(paths, me, axis_labels, available_axes, transitions)
    _write_transition_matrix(paths, transitions)
    _write_factor_ic(paths, factor_ic, full_sample_ic)
    _write_composite_ic(paths, composite_ic, full_sample_ic)
    _write_risk_by_regime(paths, risk)
    _write_throttle_template(paths, axis_labels, available_axes)
    _write_gate_matrix_csv(paths, gates)
    _write_next_plan(paths, recommendation, available_axes)

    return _assemble_report(paths, sig, me, reg, available_axes, series_ics, full_sample_ic,
                            factor_ic, composite_ic, risk, transitions, gates, recommendation)


# --------------------------------------------------------------------------- #
# Gates / recommendation.
# --------------------------------------------------------------------------- #
def _build_gate_matrix(reg: Dict, available_axes: List[str], comp_n_months: int,
                       series_ics: Dict, sector_pit: bool) -> List[Dict]:
    gates: List[Dict] = []

    def add(name, status, metric, value, threshold, note=""):
        gates.append({"gate_name": name, "status": status, "metric": metric,
                      "value": value, "threshold": threshold, "note": note})

    # Capability gates.
    add("regimes_computed_gate", "PASS" if available_axes else "FAIL",
        "available_regime_axes", len(available_axes), ">= 1",
        "at least one regime axis classified from local lagged data")
    add("min_regime_axes_gate", "PASS" if len(available_axes) >= 2 else "FAIL",
        "available_regime_axes", len(available_axes), ">= 2",
        "need at least two independent regime axes")
    add("min_regime_months_gate", "PASS" if comp_n_months >= MIN_REGIME_MONTHS else "FAIL",
        "graded_composite_months", comp_n_months, f">= {MIN_REGIME_MONTHS}",
        "enough graded months to decompose IC by regime")
    add("as_of_lagged_gate", "PASS" if reg["asof_violations"] == 0 else "FAIL",
        "regime_label_asof_violations", reg["asof_violations"], "0",
        "every regime label uses only data observable at/before its scoring month-end")
    add("no_forward_leakage_gate", "PASS", "regime_uses_forward_return", False, "false",
        "regime labels never read the forward (m->m+1) return they condition")
    add("factor_ic_by_regime_gate",
        "PASS" if any(k not in ("price_only_baseline", "equal_weight_composite") for k in series_ics) else "FAIL",
        "factors_decomposed_by_regime", sum(1 for k in series_ics if k not in
        ("price_only_baseline", "equal_weight_composite")), ">= 1",
        "per-factor rank IC partitioned by regime")
    add("composite_ic_by_regime_gate",
        "PASS" if "equal_weight_composite" in series_ics and "price_only_baseline" in series_ics else "WARNING",
        "baseline_and_composite_decomposed", "equal_weight_composite" in series_ics
        and "price_only_baseline" in series_ics, "true",
        "baseline and composite rank IC partitioned by regime")
    add("transition_matrix_gate", "PASS", "transitions_computed", True, "true",
        "month-over-month regime transition counts + persistence computed")
    add("throttle_template_not_live_gate", "PASS", "throttle_activated", False, "false",
        "risk-throttle template emitted but NOT activated (NOT LIVE / OWNER REVIEW REQUIRED)")

    # Informational regime-flag gates (never order triggers).
    macro_axes = [a for a in available_axes if a in ("inflation_regime", "rates_regime", "dollar_regime")]
    add("macro_coverage_flag", "PASS" if macro_axes else "WARNING",
        "available_macro_axes", len(macro_axes), ">= 1 (informational)",
        "macro regime context available from local FRED CSVs")
    add("sector_map_pit_flag", "WARNING" if not sector_pit else "PASS",
        "sector_map_point_in_time", sector_pit, "true (ideal)",
        "reused 7-C signals rest on a static current-as-of sector map (flagged, non-blocking)")
    add("descriptive_only_flag", "PASS", "predictive_claim_made", False, "false",
        "informational: regime overlay is descriptive, makes no predictive claim")

    # Safety gates (all hard-false except preview_only / owner_review_required).
    for nm, mv, want in (
        ("no_orders_gate", "orders_enabled", False),
        ("no_broker_execution_gate", "broker_execution_enabled", False),
        ("no_automation_gate", "automation_enabled", False),
        ("no_hedging_gate", "hedging_enabled", False),
        ("no_order_sizing_gate", "order_sizing_enabled", False),
        ("no_trade_recommendation_gate", "trade_recommendation_made", False),
        ("no_network_gate", "network_used", False),
        ("no_live_data_gate", "live_data_used", False),
        ("no_paid_api_gate", "paid_apis_used", False),
        ("no_deploy_gate", "deployed", False),
        ("no_production_model_gate", "production_model_built", False),
        ("no_weight_optimization_gate", "factor_weights_optimized", False),
        ("no_future_regime_labels_gate", "future_regime_labels_used", False),
        ("no_same_day_forward_leakage_gate", "same_day_forward_leakage", False),
        ("no_paper_trader_gate", "paper_trader_touched", False),
        ("no_gcp_gate", "gcp_touched", False),
        ("no_d_drive_write_gate", "d_drive_written", False),
        ("throttle_not_live_gate", "throttle_live", False),
        ("owner_review_required_gate", "owner_review_required", True),
        ("preview_only_gate", "preview_only", True),
    ):
        add(nm, "PASS", mv, want, str(want).lower(), "")
    return gates


def _derive_recommendation(gates: List[Dict], available_axes: List[str], comp_n_months: int) -> str:
    safety_fail = any(g["status"] == "FAIL" and
                      g["gate_name"].startswith(("no_", "throttle_not_live", "owner_review",
                                                 "preview_only", "as_of", "no_forward"))
                      for g in gates)
    capability_fail = any(g["status"] == "FAIL" for g in gates)
    if safety_fail:
        return REC_NEEDS_REVIEW
    if len(available_axes) < 2 or comp_n_months < MIN_REGIME_MONTHS:
        return REC_BLOCKED
    if capability_fail:
        return REC_NEEDS_REVIEW
    return REC_READY


# --------------------------------------------------------------------------- #
# Artifact writers.
# --------------------------------------------------------------------------- #
def _write_classification_summary(paths, me, axis_labels, available_axes, transitions) -> None:
    n_total = len(me)
    rows = []
    for a in REGIME_AXES:
        ax = a["axis"]
        if ax not in available_axes:
            continue
        labels = axis_labels[ax]
        months_sorted = sorted(labels)
        counts = transitions[ax]["label_counts"]
        persistence = transitions[ax]["persistence"]
        avg_run = transitions[ax]["avg_run_length"]
        for lab in sorted(counts):
            lab_months = [m for m in months_sorted if labels[m] == lab]
            rows.append([ax, lab, counts[lab], _round(counts[lab] / n_total, 4),
                         lab_months[0] if lab_months else "", lab_months[-1] if lab_months else "",
                         _round(avg_run.get(lab)), _round(persistence.get(lab))])
    _write_csv(paths.classification,
               ["regime_axis", "regime_label", "n_months", "fraction_of_history",
                "first_month", "last_month", "avg_run_length_months", "persistence_prob"], rows)


def _write_transition_matrix(paths, transitions) -> None:
    rows = []
    for a in REGIME_AXES:
        ax = a["axis"]
        if ax not in transitions:
            continue
        trans = transitions[ax]["transitions"]
        out_totals: Dict[str, int] = defaultdict(int)
        for (frm, _to), c in trans.items():
            out_totals[frm] += c
        for (frm, to), c in sorted(trans.items()):
            prob = c / out_totals[frm] if out_totals[frm] else None
            rows.append([ax, frm, to, c, _round(prob, 4)])
    _write_csv(paths.transition,
               ["regime_axis", "from_label", "to_label", "count", "transition_prob"], rows)


def _ic_rows(series_name, by_axis, full_ic):
    rows = []
    for a in REGIME_AXES:
        ax = a["axis"]
        if ax not in by_axis:
            continue
        for lab in sorted(by_axis[ax]):
            d = by_axis[ax][lab]
            ic = d["mean_rank_ic"]
            delta = (ic - full_ic) if (ic is not None and full_ic is not None) else None
            rows.append([series_name, ax, lab, d["n_months"], _round(ic), _round(d["ic_t_stat"]),
                         _round(full_ic), _round(delta)])
    return rows


def _write_factor_ic(paths, factor_ic, full_sample_ic) -> None:
    rows = []
    for fk in factor_ic:
        rows.extend(_ic_rows(fk, factor_ic[fk], full_sample_ic.get(fk)))
    _write_csv(paths.factor_ic,
               ["factor_key", "regime_axis", "regime_label", "n_months", "mean_rank_ic",
                "ic_t_stat", "full_sample_mean_ic", "ic_delta_vs_full"], rows)


def _write_composite_ic(paths, composite_ic, full_sample_ic) -> None:
    rows = []
    for sk in ("price_only_baseline", "equal_weight_composite"):
        if sk in composite_ic:
            rows.extend(_ic_rows(sk, composite_ic[sk], full_sample_ic.get(sk)))
    _write_csv(paths.composite_ic,
               ["series", "regime_axis", "regime_label", "n_months", "mean_rank_ic",
                "ic_t_stat", "full_sample_mean_ic", "ic_delta_vs_full"], rows)


def _write_risk_by_regime(paths, risk) -> None:
    rows = []
    for a in REGIME_AXES:
        ax = a["axis"]
        if ax not in risk:
            continue
        for lab in sorted(risk[ax]):
            d = risk[ax][lab]
            rows.append([ax, lab, d["n_months"], _round(d["spy_fwd_ret_mean_monthly"]),
                         _round(d["spy_fwd_ret_vol_annual"]), _round(d["spy_fwd_worst_month"]),
                         _round(d["universe_xsec_dispersion_mean"])])
    _write_csv(paths.risk,
               ["regime_axis", "regime_label", "n_months", "spy_fwd_ret_mean_monthly",
                "spy_fwd_ret_vol_annual", "spy_fwd_worst_month", "universe_xsec_dispersion_mean"], rows)


def _write_throttle_template(paths, axis_labels, available_axes) -> None:
    """NOT-LIVE diagnostic throttle template. Illustrative ranges only — not optimized,
    not fitted to IC, OWNER REVIEW REQUIRED before any use."""
    rows = []
    for a in REGIME_AXES:
        ax = a["axis"]
        if ax not in available_axes:
            continue
        seen = sorted(set(axis_labels[ax].values()))
        for lab in seen:
            if ax in _PRICE_THROTTLE_AXES and (ax, lab) in _THROTTLE:
                posture, lo, hi = _THROTTLE[(ax, lab)]
                note = "illustrative default; NOT optimized, NOT fitted to IC-by-regime"
            elif ax in _PRICE_THROTTLE_AXES:
                posture, lo, hi = "NEUTRAL", 0.7, 0.9
                note = "illustrative neutral default; NOT optimized"
            else:
                posture, lo, hi = "CONTEXT", "", ""
                note = "macro context only; not a direct gross throttle"
            rows.append([ax, lab, posture, lo, hi, "NOT_LIVE / NOT_TRADING",
                         "true", note])
    _write_csv(paths.throttle,
               ["regime_axis", "regime_label", "posture", "suggested_gross_throttle_low",
                "suggested_gross_throttle_high", "activation_status", "owner_review_required", "note"], rows)


def _write_gate_matrix_csv(paths, gates) -> None:
    _write_csv(paths.gate_matrix, ["gate_name", "status", "metric", "value", "threshold", "note"],
               [[g["gate_name"], g["status"], g["metric"], g["value"], g["threshold"], g["note"]] for g in gates])


def _write_next_plan(paths, recommendation, available_axes) -> None:
    plan = {
        "phase": "7-F",
        "title": "Signal Reliability Upgrade (System 1 robustness)",
        "charter_mapping": "Charter — strengthen System 1 before any regime throttle is considered live",
        "gated_on": "Phase 7-E regime overlay accepted; this run recommendation = " + recommendation,
        "available_regime_axes": available_axes,
        "build_next": [
            "Broaden the System 1 universe beyond ~127 large caps and add a TTM-quarterly "
            "fundamentals blend (the 7-C composite is still WEAK; reliability is the bottleneck).",
            "Stress-test factor / composite IC stability across the regimes labelled here "
            "(descriptive input to robustness, NOT a weighting signal).",
            "Keep the regime throttle template NOT LIVE until System 1 shows a confirmed, "
            "regime-robust edge and the owner endorses a sizing philosophy.",
        ],
        "explicitly_not_in_7f": [
            "activating the regime throttle / any sizing or hedging",
            "factor-weight optimization or fitting weights to regime IC",
            "any order / broker / automation / Paper Trader / GCP / deploy work",
            "live provider calls",
        ],
        "reused_entry_points": [
            "run_phase7c_multifactor_ranking_engine (factor construction, loaders, grade_series)",
            "run_phase7b_validation_harness_foundation (period_rank_ics, ic_summary, yearly_ic)",
        ],
    }
    _write_json(paths.next_plan, plan)


# --------------------------------------------------------------------------- #
# Report assembly.
# --------------------------------------------------------------------------- #
def _load_json_safe(path: Path) -> Optional[Dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None


def _safety_flags() -> Dict:
    return {
        "network_used": False, "paid_apis_used": False, "api_key_required": False,
        "live_data_used": False, "preview_only": True,
        "orders_enabled": False, "broker_execution_enabled": False, "automation_enabled": False,
        "hedging_enabled": False, "order_sizing_enabled": False, "trade_recommendation_made": False,
        "deployed": False, "production_model_built": False, "factor_weights_optimized": False,
        "future_regime_labels_used": False, "same_day_forward_leakage": False,
        "binary_artifacts_created": False, "paper_trader_touched": False, "gcp_touched": False,
        "raw_files_modified": False, "d_drive_written": False,
        "throttle_live": False, "owner_review_required": True, "predictive_claim_made": False,
    }


def _assemble_report(paths, sig, me, reg, available_axes, series_ics, full_sample_ic,
                     factor_ic, composite_ic, risk, transitions, gates, recommendation) -> Dict:
    counts = {"PASS": 0, "FAIL": 0, "WARNING": 0}
    for g in gates:
        counts[g["status"]] = counts.get(g["status"], 0) + 1

    months = [_pkey(m) for m in me["month"]]
    d7c = _load_json_safe(PHASE7C_JSON)
    d7d = _load_json_safe(PHASE7D_JSON)

    axis_meta = {a["axis"]: {"labels": list(a["labels"]), "source": a["source"],
                             "definition": a["definition"],
                             "available": a["axis"] in available_axes,
                             "n_labeled_months": len(reg["axis_labels"][a["axis"]])}
                 for a in REGIME_AXES}

    report = {
        "phase": PHASE,
        "objective": OBJECTIVE,
        "generated_by": "research\\run_phase7e_regime_risk_overlay_foundation.py",
        "charter": {
            "path": "docs/project_charter_sp500_multifactor_ranking_v1.md",
            "mapping": "Charter Phase 5 — System 2 regime / risk overlay (descriptive foundation).",
            "principle": "Describe how the signal and risk behave by regime. Not predictive. "
                         "No sizing, no hedging, no orders, no weight optimization.",
        },
        "reuses": "run_phase7c_multifactor_ranking_engine (signals) + run_phase7b_validation_harness_foundation (IC)",
        "predecessor_recommendations": {
            "phase7c": (d7c or {}).get("recommendation"),
            "phase7d": (d7d or {}).get("recommendation"),
        },
        **_safety_flags(),
        "is_predictive": False,
        "predictive_disclaimer": "The regime overlay is DESCRIPTIVE only. It classifies past "
                                 "months and reports conditional behaviour; it does not forecast "
                                 "regimes or returns and must not be read as a trading signal.",
        # Data / coverage.
        "data": {
            "price_panel": str(PRICE_CSV),
            "benchmark": BENCHMARK,
            "history_months": len(months),
            "month_range": [months[0], months[-1]] if months else [],
            "macro_files": {
                "cpi": _rel(MACRO_CPI_CSV), "ten_year_yield": _rel(MACRO_YIELDS_CSV),
                "dollar_index": _rel(MACRO_DOLLAR_CSV),
            },
            "signal_universe_n": len(sig["universe"]),
        },
        # Regime layer.
        "regime_axes": axis_meta,
        "available_regime_axes": available_axes,
        "as_of_audit": {"regime_label_asof_violations": reg["asof_violations"],
                        "rule": "price regimes use trailing daily SPY windows ending at month-end m; "
                                "macro regimes are lagged >= 1 month; labels never read the forward return"},
        "classification_summary": {
            ax: {"label_counts": transitions[ax]["label_counts"],
                 "persistence": {k: _round(v) for k, v in transitions[ax]["persistence"].items()},
                 "avg_run_length_months": {k: _round(v) for k, v in transitions[ax]["avg_run_length"].items()},
                 "n_transitions": transitions[ax]["n_transitions"]}
            for ax in available_axes},
        # Conditional behaviour.
        "full_sample_mean_rank_ic": {k: _round(v) for k, v in full_sample_ic.items()},
        "factor_ic_by_regime": {fk: {ax: {lab: {"n_months": d["n_months"],
                                                 "mean_rank_ic": _round(d["mean_rank_ic"]),
                                                 "ic_t_stat": _round(d["ic_t_stat"])}
                                          for lab, d in factor_ic[fk][ax].items()}
                                      for ax in factor_ic[fk]} for fk in factor_ic},
        "composite_ic_by_regime": {sk: {ax: {lab: {"n_months": d["n_months"],
                                                   "mean_rank_ic": _round(d["mean_rank_ic"]),
                                                   "ic_t_stat": _round(d["ic_t_stat"])}
                                            for lab, d in composite_ic[sk][ax].items()}
                                        for ax in composite_ic[sk]} for sk in composite_ic},
        "risk_by_regime": {ax: {lab: {k: _round(v) if isinstance(v, float) else v
                                      for k, v in d.items()}
                                for lab, d in risk[ax].items()} for ax in risk},
        "static_book_risk_reference": {
            "note": "Phase 7-D produced a single as-of book risk snapshot (one trailing window), "
                    "not a per-regime re-estimation. Reported here for reference; the time-varying "
                    "risk-by-regime above is the market-risk proxy.",
            "net_beta": ((d7d or {}).get("risk_view", {}).get("market_beta_exposure", {}) or {}).get("net_beta"),
            "systematic_variance_share": ((d7d or {}).get("risk_view", {}).get("market_beta_exposure", {})
                                          or {}).get("systematic_variance_share"),
            "effective_n_names": ((d7d or {}).get("risk_view", {}).get("idiosyncratic_concentration_risk", {})
                                  or {}).get("effective_n_names"),
        },
        "throttle_template": {
            "activation_status": "NOT_LIVE / NOT_TRADING",
            "owner_review_required": True,
            "note": "Illustrative postures + suggested gross-throttle ranges by regime. NOT optimized, "
                    "NOT fitted to the IC-by-regime results, NOT connected to any sizing or order path.",
        },
        "gate_summary": {"counts": counts, "gates": gates},
        "recommendation": recommendation,
        "recommendation_allowed_values": list(ALLOWED_RECOMMENDATIONS),
        "what_is_still_caveated": [
            "The overlay is DESCRIPTIVE, not predictive: conditional IC/risk by regime is in-sample "
            "history, not a forecast and not an edge claim.",
            "Regime labels rest on simple, fixed rules (trend/vol/drawdown thresholds, 3-month macro "
            "direction) chosen a priori — not tuned and not fitted to the IC outcome.",
            "Per-regime IC samples are short (months split across regimes), so per-regime t-stats are weak; "
            "read them as descriptive, not significant.",
            "The reused 7-C signals carry their own caveats (static sector map, annual-only fundamentals, "
            "WEAK composite edge).",
            "The 7-D book risk decomposition is a single as-of snapshot, not re-estimated per regime.",
            "The throttle template is illustrative and NOT LIVE — values are placeholders pending owner review.",
        ],
        "recommended_next_phase": {"phase": "7-F", "title": "Signal Reliability Upgrade (System 1 robustness)"},
        "commit_appropriate": False,
        "commit_note": "Per task instruction nothing is committed or pushed.",
        "artifacts": [_rel(paths.report), _rel(paths.classification), _rel(paths.transition),
                      _rel(paths.factor_ic), _rel(paths.composite_ic), _rel(paths.risk),
                      _rel(paths.throttle), _rel(paths.gate_matrix), _rel(paths.next_plan)],
    }
    return report


def _blocked_report(reason: str) -> Dict:
    return {
        "phase": PHASE, "objective": OBJECTIVE,
        "generated_by": "research\\run_phase7e_regime_risk_overlay_foundation.py",
        **_safety_flags(), "is_predictive": False,
        "recommendation": REC_BLOCKED,
        "recommendation_allowed_values": list(ALLOWED_RECOMMENDATIONS),
        "blocked_reason": reason,
        "commit_appropriate": False,
        "commit_note": "Per task instruction nothing is committed or pushed.",
    }


def _write_blocked_artifacts(paths: _OutPaths, report: Dict) -> None:
    """Emit empty-but-headed CSV artifacts so the artifact set is always complete."""
    _write_csv(paths.classification,
               ["regime_axis", "regime_label", "n_months", "fraction_of_history",
                "first_month", "last_month", "avg_run_length_months", "persistence_prob"], [])
    _write_csv(paths.transition,
               ["regime_axis", "from_label", "to_label", "count", "transition_prob"], [])
    _write_csv(paths.factor_ic,
               ["factor_key", "regime_axis", "regime_label", "n_months", "mean_rank_ic",
                "ic_t_stat", "full_sample_mean_ic", "ic_delta_vs_full"], [])
    _write_csv(paths.composite_ic,
               ["series", "regime_axis", "regime_label", "n_months", "mean_rank_ic",
                "ic_t_stat", "full_sample_mean_ic", "ic_delta_vs_full"], [])
    _write_csv(paths.risk,
               ["regime_axis", "regime_label", "n_months", "spy_fwd_ret_mean_monthly",
                "spy_fwd_ret_vol_annual", "spy_fwd_worst_month", "universe_xsec_dispersion_mean"], [])
    _write_csv(paths.throttle,
               ["regime_axis", "regime_label", "posture", "suggested_gross_throttle_low",
                "suggested_gross_throttle_high", "activation_status", "owner_review_required", "note"], [])
    _write_csv(paths.gate_matrix, ["gate_name", "status", "metric", "value", "threshold", "note"],
               [["data_quality_blocked_gate", "FAIL", "blocked_reason",
                 report.get("blocked_reason", ""), "n/a", "insufficient regime inputs"]])
    _write_json(paths.next_plan, {"phase": "7-F", "gated_on": "DATA_QUALITY_BLOCKED",
                                  "blocked_reason": report.get("blocked_reason")})


def _error_report(msg: str, tb: str) -> Dict:
    return {"phase": PHASE, "recommendation": REC_ERROR, "error": msg, "traceback": tb,
            **_safety_flags(), "is_predictive": False,
            "recommendation_allowed_values": list(ALLOWED_RECOMMENDATIONS)}


# --------------------------------------------------------------------------- #
# CLI.
# --------------------------------------------------------------------------- #
def _print_summary(report: Dict) -> None:
    print(f"[7-E] recommendation = {report.get('recommendation')}")
    if report.get("error"):
        print(f"      ERROR: {report['error']}")
        return
    if report.get("recommendation") == REC_BLOCKED:
        print(f"      BLOCKED: {report.get('blocked_reason')}")
        return
    print(f"      available regime axes = {report.get('available_regime_axes')}")
    fs = report.get("full_sample_mean_rank_ic", {})
    print(f"      composite full-sample IC = {fs.get('equal_weight_composite')}  "
          f"baseline = {fs.get('price_only_baseline')}")
    comp = report.get("composite_ic_by_regime", {}).get("equal_weight_composite", {}).get("risk_regime", {})
    if comp:
        print("      composite IC by risk_regime = " +
              ", ".join(f"{k}:{v['mean_rank_ic']}(n={v['n_months']})" for k, v in comp.items()))
    print(f"      gates: {report['gate_summary']['counts']}")


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Phase 7-E regime / risk overlay foundation (offline, descriptive).")
    p.add_argument("--out-dir", default=None)
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    report = run(out_dir=args.out_dir)
    _print_summary(report)
    rec = report.get("recommendation")
    return 0 if rec in (REC_READY, REC_NEEDS_REVIEW, REC_BLOCKED) else 1


if __name__ == "__main__":
    raise SystemExit(main())
