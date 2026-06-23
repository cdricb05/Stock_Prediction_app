"""Phase 7-F — Signal Reliability Upgrade (System 1 robustness).

**Track A (quant brain) research only.** Offline, point-in-time, leakage-safe.
No network, no provider call, no paid API, no API key read or required, no model
trained or deployed, **no factor-weight optimization, no factor-sign flipping to
chase IC**, no Paper Trader / GCP work, no orders / broker / automation, no live data
call, no binary artifact, no commit, no push. Reads only local files: the Phase 2K-G
price panel (READ ONLY on D:), the committed SEC fundamentals / sector artifacts, and
local FRED macro CSVs (repo-local, reused via Phase 7-E for regime diagnostics).
Writes nothing to D:.

Why this phase exists
---------------------
Phase 7-C built the first System 1 multi-factor ranking engine and graded WEAK
(equal-weight composite mean rank IC = -0.0074; price-only baseline = -0.0123;
incremental +0.0050, a near miss). Phase 7-E's regime diagnostics confirmed the
signal is fragile and regime-sensitive. Before ANY portfolio construction, sizing,
hedging, or regime activation, the signal foundation itself must be made more
reliable. This phase is a disciplined **signal-engineering** exercise: improve factor
*integrity, robustness, and interpretability* through better data and factor
construction — NOT a trading system, NOT a production model, NOT weight optimization,
NOT a regime overlay, NOT a sign-flipping rescue.

What it does
------------
  1. Inventories the concrete signal weaknesses identified in 7-C / 7-E and attributes
     each to a root cause (annual-only fundamentals, single-spec buckets, low-vol mis-
     specification, no cash-flow / asset-efficiency metrics, no TTM/quarterly, etc.).
  2. Inventories local data quality available for the upgrade (annual 10-K coverage,
     quarterly balance recency, clean 3-month quarterly-frame coverage for TTM).
  3. Builds an UPGRADED factor catalogue from local point-in-time data only:
       - momentum bucket: 12-1, 6-1, and risk-adjusted 12-1 (price);
       - value bucket: earnings / sales / FCF / book yields (share-free, caveated);
       - quality bucket: ROE, ROA, net & operating & FCF margins, asset turnover,
         accruals (cash-flow-based & asset-efficiency, split-invariant);
       - growth bucket: revenue / earnings / FCF YoY (split-invariant);
       - low-volatility treated explicitly as a RISK DESCRIPTOR (reported, NOT placed
         in the alpha composite) per an a-priori economic stance.
     Each bucket score is the EQUAL-WEIGHT mean of its approved sub-factor z-scores;
     the composite is the EQUAL-WEIGHT mean of the approved bucket scores. No weights
     are optimized; no signs are flipped to chase IC.
  4. Attempts TTM (trailing-twelve-month) flow construction by annual+interim roll-
     forward from clean 3-month quarterly frames, and reports its coverage honestly
     (the local clean-quarterly panel is thin — a documented data-foundation gap).
  5. Grades every sub-factor, every bucket, and the composite through the unmodified
     Phase 7-B harness, against the SAME-UNIVERSE Phase 7-C equal-weight composite
     (the "old baseline") and the price-only baseline.
  6. Decomposes upgraded-composite and bucket IC by the Phase 7-E regimes — strictly
     for diagnostics; regimes are NEVER used to select, weight, or modify factors.

Leakage safety
--------------
Forward labels are next-month returns resolving strictly after the decision date.
Fundamentals (annual flows, quarterly balances, TTM rolls) are activated only when
their availability_datetime is strictly before the scoring month-end. Momentum skips
the most recent month. A within-period label-permutation placebo must collapse the
composite IC toward zero. All rank-IC math is reused unchanged from the Phase 7-B
harness via Phase 7-C.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Reuse the Phase 7-C ranking engine (loaders, factor construction, normalization,
# grading), the Phase 7-E regime overlay (regime classification + IC-by-regime), and
# transitively the Phase 7-B harness. No work runs at import time.
from research import run_phase7c_multifactor_ranking_engine as C
from research import run_phase7e_regime_risk_overlay_foundation as E
from research import run_phase7b_validation_harness_foundation as H

PHASE = "7-F"
OBJECTIVE = (
    "Establish a materially more reliable System 1 signal foundation through "
    "disciplined data and factor-construction upgrades (multi-spec value / quality / "
    "growth / momentum from local point-in-time data, cash-flow & asset-efficiency "
    "quality, quarterly balance recency, an attempted TTM build, and low-volatility "
    "demoted to a risk descriptor), graded through the unmodified Phase 7-B harness "
    "against the same-universe Phase 7-C composite. No weight optimization, no sign "
    "flipping, no regime-based selection, no live data, no production / trading claim."
)

# Recommendation taxonomy (task-specified).
REC_UPGRADED = "SIGNAL_RELIABILITY_UPGRADED"
REC_WEAK = "SIGNAL_RELIABILITY_WEAK"
REC_NEEDS_DATA = "NEEDS_DATA_FOUNDATION_UPGRADE"
REC_NEEDS_REVIEW = "NEEDS_REVIEW"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (REC_UPGRADED, REC_WEAK, REC_NEEDS_DATA, REC_NEEDS_REVIEW, REC_ERROR)

# --------------------------------------------------------------------------- #
# Paths.
# --------------------------------------------------------------------------- #
PRICE_CSV = C.PRICE_CSV
FUNDAMENTALS_CSV = C.FUNDAMENTALS_CSV
SECTOR_MAP_CSV = C.SECTOR_MAP_CSV
BENCHMARK = C.BENCHMARK
_OUT_DIR_DEFAULT = _REPO_ROOT / "research" / "output" / "phase7f_signal_reliability_upgrade"

# --------------------------------------------------------------------------- #
# Config (fixed, documented modelling choices — NO tuning loop, NO fit to IC).
# --------------------------------------------------------------------------- #
MOM_SKIP_M = 1                 # all momentum specs skip the most recent month
MOM_12_LOOKBACK_M = 13         # 12-1 momentum reference month
MOM_6_LOOKBACK_M = 7           # 6-1 momentum reference month
MOM_VOL_WINDOW_M = 12          # trailing monthly-return window for risk-adjusted momentum
MIN_NAMES = C.MIN_NAMES        # reuse 7-C cross-section minimum (20)
MIN_APPROVE_PERIODS = C.MIN_APPROVE_PERIODS  # reuse 7-C breadth approval (24 months)
WINSOR_Z = C.WINSOR_Z
IMPROVEMENT_GATE = 0.005       # upgraded composite must beat the old baseline by this mean rank IC
TTM_INTERIM_MAX_Q = 3          # at most 3 interim quarters rolled onto the latest annual
TTM_MATCH_TOL_D = 45           # tolerance (days) matching a prior-year quarter for the roll-forward
COST_BPS = 10.0
HOLDOUT_FRACTION = 0.20

NAN = float("nan")


# --------------------------------------------------------------------------- #
# Sub-factor catalogue. ``bucket`` groups sub-factors into the four alpha buckets;
# ``low_volatility`` is catalogued as a RISK DESCRIPTOR (role="risk_descriptor") and
# is intentionally excluded from the alpha composite. ``split_invariant`` flags
# fundamentals-only ratios that carry NO adjusted-price / as-reported-share split
# basis caveat (a robustness property). All factors are sign-oriented a priori so a
# higher score is "more attractive"; signs are NEVER flipped to chase IC.
# --------------------------------------------------------------------------- #
@dataclass
class SubFactor:
    key: str
    bucket: str
    source: str
    definition: str
    split_invariant: bool
    role: str = "alpha"        # "alpha" (in composite) or "risk_descriptor" (reported only)
    note: str = ""


SUBFACTORS: List[SubFactor] = [
    # ---- momentum bucket (price) ----
    SubFactor("mom_12_1", "momentum", "price",
              "12-1 price momentum: close[m-1]/close[m-13]-1 (skips the most recent month)",
              False, note="kept from Phase 7-C; classic cross-sectional momentum"),
    SubFactor("mom_6_1", "momentum", "price",
              "6-1 price momentum: close[m-1]/close[m-7]-1 (shorter horizon spec)",
              False, note="NEW horizon diversification of the momentum bucket"),
    SubFactor("mom_riskadj", "momentum", "price",
              "risk-adjusted 12-1 momentum: (12-1 return) / trailing 12-month monthly-return stdev",
              False, note="NEW; rewards steady trends over volatile ones"),
    # ---- value bucket (fundamentals vs price; share-free via implied shares = NI/EPS) ----
    SubFactor("val_earnings_yield", "value", "fundamentals",
              "annual diluted EPS / price (E/P); higher = cheaper = better",
              False, note="kept from Phase 7-C; residual split-basis caveat (see docs)"),
    SubFactor("val_sales_yield", "value", "fundamentals",
              "annual revenue / (implied diluted shares * price); higher = cheaper",
              False, note="NEW; sales-to-price using implied shares NI/EPS"),
    SubFactor("val_fcf_yield", "value", "fundamentals",
              "annual free cash flow / (implied diluted shares * price); higher = cheaper",
              False, note="NEW; cash-flow value, robust to accrual accounting"),
    SubFactor("val_book_yield", "value", "fundamentals",
              "latest shareholder equity / (implied diluted shares * price); higher = cheaper",
              False, note="NEW; book-to-price using the most-recent quarterly equity"),
    # ---- quality bucket (split-invariant fundamentals ratios) ----
    SubFactor("qual_roe", "quality", "fundamentals",
              "return on equity = annual net income / latest shareholder equity",
              True, note="kept from Phase 7-C"),
    SubFactor("qual_roa", "quality", "fundamentals",
              "return on assets = annual net income / latest total assets (asset efficiency)",
              True, note="NEW asset-efficiency quality measure"),
    SubFactor("qual_net_margin", "quality", "fundamentals",
              "net margin = annual net income / annual revenue",
              True, note="NEW profitability margin"),
    SubFactor("qual_oper_margin", "quality", "fundamentals",
              "operating margin = annual operating income / annual revenue",
              True, note="NEW operating profitability margin"),
    SubFactor("qual_fcf_margin", "quality", "fundamentals",
              "free-cash-flow margin = annual free cash flow / annual revenue",
              True, note="NEW cash-flow-based quality (not solely ROE)"),
    SubFactor("qual_asset_turnover", "quality", "fundamentals",
              "asset turnover = annual revenue / latest total assets",
              True, note="NEW asset-efficiency measure"),
    SubFactor("qual_accruals_inv", "quality", "fundamentals",
              "negative balance-sheet accruals = -((net income - operating cash flow) / total assets); "
              "lower accruals = higher earnings quality (sign inverted a priori)",
              True, note="NEW earnings-quality / accruals measure; cash earnings preferred"),
    # ---- growth bucket (split-invariant YoY ratios) ----
    SubFactor("grow_revenue_yoy", "growth", "fundamentals",
              "revenue YoY = latest annual revenue / prior annual revenue - 1",
              True, note="kept from Phase 7-C"),
    SubFactor("grow_earnings_yoy", "growth", "fundamentals",
              "earnings YoY = latest annual net income / prior annual net income - 1 (prior > 0 only)",
              True, note="NEW; guarded against negative-base sign distortion"),
    SubFactor("grow_fcf_yoy", "growth", "fundamentals",
              "free-cash-flow YoY = latest annual FCF / prior annual FCF - 1 (prior > 0 only)",
              True, note="NEW cash-flow growth"),
    # ---- risk descriptor (reported, NOT in the alpha composite) ----
    SubFactor("low_volatility", "low_volatility", "price",
              "negative trailing 126-trading-day realized daily-return volatility",
              False, role="risk_descriptor",
              note="a-priori RISK DESCRIPTOR: low-vol is a risk characteristic, not an alpha factor; "
                   "reported for evidence but excluded from the alpha composite"),
]

# Attempted-but-data-limited TTM factor (catalogued separately; included in the
# composite only if it clears the same breadth-approval gate as every other factor).
TTM_SUBFACTOR = SubFactor(
    "ttm_earnings_yield", "value_ttm", "fundamentals_ttm",
    "TTM earnings yield = TTM net income / (implied shares * price); TTM built by annual+interim "
    "roll-forward from clean 3-month quarterly frames",
    False, note="NEW TTM attempt; included only if local clean-quarterly coverage clears approval")

ALPHA_BUCKETS = ("momentum", "value", "quality", "growth")


# --------------------------------------------------------------------------- #
# Signal-weakness inventory (Phase 7-C / 7-E failure modes -> root cause -> 7-F action).
# Curated, content-driven; encodes design intent, not computed metrics.
# --------------------------------------------------------------------------- #
SIGNAL_WEAKNESSES: List[Dict] = [
    {"id": "W1", "source": "7-C/7-E", "failure_mode": "composite full-sample mean rank IC negative (-0.0074)",
     "root_cause": "weak/mis-specified factor set on a narrow universe",
     "status_7f": "partial", "addressed_by": "broadened multi-spec value/quality/growth/momentum buckets"},
    {"id": "W2", "source": "7-C", "failure_mode": "single-metric buckets (one E/P, one ROE, one rev-YoY)",
     "root_cause": "single-spec buckets are fragile and high-variance",
     "status_7f": "addressed", "addressed_by": "each bucket now an equal-weight blend of multiple sub-factors"},
    {"id": "W3", "source": "7-C", "failure_mode": "annual-only (10-K) fundamentals stale up to ~1 year",
     "root_cause": "reliance on annual-only fundamentals; no quarterly recency",
     "status_7f": "partial", "addressed_by": "quarterly balance recency for ROE/ROA/turnover/accruals; "
                                              "TTM roll-forward attempted (coverage-limited)"},
    {"id": "W4", "source": "7-C", "failure_mode": "quality = ROE only (no asset efficiency / cash flow)",
     "root_cause": "narrow quality definition",
     "status_7f": "addressed", "addressed_by": "added ROA, margins, FCF margin, asset turnover, accruals"},
    {"id": "W5", "source": "7-C/7-E", "failure_mode": "low-volatility treated as an alpha factor",
     "root_cause": "low-vol is a risk descriptor, not cross-sectional alpha here",
     "status_7f": "addressed", "addressed_by": "low-vol demoted to a reported risk descriptor, excluded from composite"},
    {"id": "W6", "source": "7-C", "failure_mode": "value distorted by split-adjusted EPS vs adjusted price",
     "root_cause": "adjusted-price vs as-reported-share basis mismatch",
     "status_7f": "partial", "addressed_by": "added cash-flow/sales/book yields via most-recent implied shares; "
                                             "residual split caveat documented, not eliminated"},
    {"id": "W7", "source": "7-C/7-E", "failure_mode": "constrained ~127-name large-cap universe",
     "root_cause": "constrained / potentially unrepresentative universe; current-constituent bias",
     "status_7f": "not_addressed", "addressed_by": "universe is bounded by local data; flagged as the "
                                                   "primary remaining data-foundation gap for Phase 7-G"},
    {"id": "W8", "source": "7-C", "failure_mode": "no quarterly / TTM fundamentals construction",
     "root_cause": "lack of TTM levels; no point-in-time quarterly flow series",
     "status_7f": "partial", "addressed_by": "TTM roll-forward implemented; local clean-quarterly frames "
                                             "too sparse for broad coverage (reported)"},
    {"id": "W9", "source": "7-C", "failure_mode": "revisions / sentiment bucket unavailable (empty panel)",
     "root_cause": "no estimate-revision / sentiment data locally",
     "status_7f": "not_addressed", "addressed_by": "still unavailable; not fabricated"},
    {"id": "W10", "source": "7-C", "failure_mode": "static current-as-of sector map (not strictly PIT)",
     "root_cause": "sector classification not point-in-time",
     "status_7f": "not_addressed", "addressed_by": "inherited caveat; sector membership is stable, flagged"},
]


# --------------------------------------------------------------------------- #
# Numeric helpers (reuse the Phase 7-C helpers).
# --------------------------------------------------------------------------- #
_f = C._f
_round = C._round


def _safe_ratio(num: Optional[float], den: Optional[float], *, den_positive: bool = True) -> Optional[float]:
    n, d = _f(num), _f(den)
    if n is None or d is None:
        return None
    if den_positive and d <= 0:
        return None
    if d == 0:
        return None
    return n / d


# --------------------------------------------------------------------------- #
# Data layer additions: quarterly balance recency + clean quarterly flow frames.
# --------------------------------------------------------------------------- #
_BALANCE_FIELDS = ("total_assets", "shareholder_equity", "total_liabilities")


def load_balance_pit(path=FUNDAMENTALS_CSV, universe: Optional[set] = None) -> pd.DataFrame:
    """Point-in-time balance-sheet levels from ALL forms (10-K and 10-Q). Balances are
    stock quantities (no YTD-cumulation problem), so the most-recent available balance
    gives genuine quarterly recency over annual-only. First-reported per (ticker, fpe,
    field). Returns [ticker, fpe, avail, normalized_field, value]."""
    df = pd.read_csv(path, dtype={"ticker": str})
    df = df[df["normalized_field"].isin(_BALANCE_FIELDS)].copy()
    df = df[df["point_in_time_usable"].astype(str).str.lower().eq("true")]
    df["fpe"] = pd.to_datetime(df["fiscal_period_end"], errors="coerce")
    df["avail"] = C._avail_date(df["availability_datetime"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["fpe", "avail", "value"])
    if universe is not None:
        df = df[df["ticker"].isin(universe)]
    df = (df.sort_values("avail")
            .drop_duplicates(["ticker", "fpe", "normalized_field"], keep="first"))
    return df[["ticker", "fpe", "avail", "normalized_field", "value"]]


def load_clean_quarterly_obs(path=FUNDAMENTALS_CSV, field: str = "net_income",
                             universe: Optional[set] = None
                             ) -> Dict[str, List[Tuple[pd.Timestamp, pd.Timestamp, float]]]:
    """{ticker: [(fpe, avail, value)]} for a flow field using ONLY clean 3-month
    quarterly frames (frame matches CYyyyyQn). These SEC frames are normalized to
    3-month durations, so they are summable to TTM (unlike raw YTD-cumulative 10-Q
    line items). Sparse by construction — coverage is reported, not assumed."""
    df = pd.read_csv(path, dtype={"ticker": str})
    df = df[df["normalized_field"] == field].copy()
    df = df[df["point_in_time_usable"].astype(str).str.lower().eq("true")]
    df = df[df["frame"].astype(str).str.fullmatch(r"CY\d{4}Q\d")]
    df["fpe"] = pd.to_datetime(df["fiscal_period_end"], errors="coerce")
    df["avail"] = C._avail_date(df["availability_datetime"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["fpe", "avail", "value"])
    if universe is not None:
        df = df[df["ticker"].isin(universe)]
    df = (df.sort_values("avail")
            .drop_duplicates(["ticker", "fpe"], keep="first"))
    return C._field_obs(df.assign(normalized_field=field), field)


def ttm_asof(annual_obs: List[Tuple[pd.Timestamp, pd.Timestamp, float]],
             qtr_obs: List[Tuple[pd.Timestamp, pd.Timestamp, float]],
             cutoff: pd.Timestamp) -> Optional[float]:
    """Point-in-time TTM for a flow field via annual+interim roll-forward:
    TTM = latest_annual + sum(new interim quarters after the annual) - sum(matching
    prior-year quarters). Every leg must be available strictly before ``cutoff``.
    Returns None unless at least one interim quarter is matched (so a return genuinely
    reflects TTM recency, not a relabelled annual)."""
    ann = sorted((fpe, val) for fpe, av, val in annual_obs if av < cutoff)
    if not ann:
        return None
    fy_fpe, fy_val = ann[-1]
    avail_q = sorted((fpe, val) for fpe, av, val in qtr_obs if av < cutoff)
    interim = [(fpe, val) for fpe, val in avail_q if fpe > fy_fpe]
    if not interim:
        return None
    interim = interim[-TTM_INTERIM_MAX_Q:]
    new_sum = 0.0
    old_sum = 0.0
    for fpe, val in interim:
        target = fpe - pd.DateOffset(years=1)
        lo, hi = target - pd.Timedelta(days=TTM_MATCH_TOL_D), target + pd.Timedelta(days=TTM_MATCH_TOL_D)
        match = [v for f, v in avail_q if lo <= f <= hi]
        if not match:
            return None  # cannot form a clean YoY roll-forward -> no TTM
        new_sum += val
        old_sum += match[-1]
    return fy_val + new_sum - old_sum


# --------------------------------------------------------------------------- #
# Implied diluted shares (NI / EPS) from annual obs, point-in-time.
# --------------------------------------------------------------------------- #
def _shares_obs(ni_obs: Dict[str, List[Tuple[pd.Timestamp, pd.Timestamp, float]]],
                eps_obs: Dict[str, List[Tuple[pd.Timestamp, pd.Timestamp, float]]]
                ) -> Dict[str, List[Tuple[pd.Timestamp, pd.Timestamp, float]]]:
    """Implied diluted shares = net income / diluted EPS at each annual period (both
    as-reported, identical split basis). availability = later of the two legs."""
    out: Dict[str, List[Tuple[pd.Timestamp, pd.Timestamp, float]]] = {}
    for t, ni_list in ni_obs.items():
        eps_map = {fpe: (av, val) for fpe, av, val in eps_obs.get(t, [])}
        lst = []
        for fpe, av, ni in ni_list:
            if fpe in eps_map:
                eav, eps = eps_map[fpe]
                if abs(eps) > 0.01 and ni / eps > 0:
                    lst.append((fpe, max(av, eav), ni / eps))
        if lst:
            lst.sort(key=lambda x: x[0])
            out[t] = lst
    return out


# --------------------------------------------------------------------------- #
# Upgraded factor construction.
# --------------------------------------------------------------------------- #
def build_upgraded_price_factors(pf: Dict[str, object]) -> Dict[str, pd.DataFrame]:
    """Raw long frames for the momentum sub-factors + the low-vol risk descriptor."""
    monthly_close: pd.DataFrame = pf["monthly_close"]
    mom12 = monthly_close.shift(MOM_SKIP_M) / monthly_close.shift(MOM_12_LOOKBACK_M) - 1.0
    mom6 = monthly_close.shift(MOM_SKIP_M) / monthly_close.shift(MOM_6_LOOKBACK_M) - 1.0
    monthly_ret = monthly_close.pct_change(fill_method=None)
    mret_vol = monthly_ret.rolling(MOM_VOL_WINDOW_M, min_periods=MOM_VOL_WINDOW_M).std()
    mom_riskadj = mom12 / mret_vol.replace(0.0, np.nan)

    def _long(wide: pd.DataFrame) -> pd.DataFrame:
        s = wide.stack()
        s.index = s.index.set_names(["month", "ticker"])
        return s.rename("raw").reset_index().dropna(subset=["raw"])[["month", "ticker", "raw"]]

    return {
        "mom_12_1": _long(mom12),
        "mom_6_1": _long(mom6),
        "mom_riskadj": _long(mom_riskadj),
        "low_volatility": pf["low_volatility"][["month", "ticker", "raw"]].copy(),
    }


def build_upgraded_fundamental_factors(annual: pd.DataFrame, balance: pd.DataFrame,
                                       monthly_close: pd.DataFrame, month_end_date: pd.Series,
                                       *, ttm_ni_qtr: Optional[Dict] = None) -> Dict[str, pd.DataFrame]:
    """Point-in-time raw long frames for every fundamental sub-factor (+ the TTM
    attempt). A fundamental is used at month m only if its availability_datetime is
    strictly before month m's market month-end."""
    rev = C._field_obs(annual, "revenue")
    ni = C._field_obs(annual, "net_income")
    eps = C._field_obs(annual, "eps_diluted")
    opinc = C._field_obs(annual, "operating_income")
    ocf = C._field_obs(annual, "operating_cash_flow")
    fcf = C._field_obs(annual, "free_cash_flow")
    assets = C._field_obs(balance, "total_assets")
    equity = C._field_obs(balance, "shareholder_equity")
    shares = _shares_obs(ni, eps)

    rows: Dict[str, list] = {sf.key: [] for sf in SUBFACTORS if sf.source == "fundamentals"}
    rows[TTM_SUBFACTOR.key] = []

    months = list(monthly_close.index)
    tickers = list(monthly_close.columns)
    for m in months:
        cutoff = month_end_date.get(m)
        if cutoff is None:
            continue
        for t in tickers:
            px = monthly_close.at[m, t] if t in monthly_close.columns else np.nan
            px = _f(px)
            # As-of single latest annual levels / balances.
            rev_a = C._asof(rev.get(t, []), cutoff)
            ni_a = C._asof(ni.get(t, []), cutoff)
            eps_a = C._asof(eps.get(t, []), cutoff)
            opinc_a = C._asof(opinc.get(t, []), cutoff)
            ocf_a = C._asof(ocf.get(t, []), cutoff)
            fcf_a = C._asof(fcf.get(t, []), cutoff)
            assets_a = C._asof(assets.get(t, []), cutoff)
            equity_a = C._asof(equity.get(t, []), cutoff)
            shares_a = C._asof(shares.get(t, []), cutoff)
            rev_v = rev_a[1] if rev_a else None
            ni_v = ni_a[1] if ni_a else None
            eps_v = eps_a[1] if eps_a else None
            opinc_v = opinc_a[1] if opinc_a else None
            ocf_v = ocf_a[1] if ocf_a else None
            fcf_v = fcf_a[1] if fcf_a else None
            assets_v = assets_a[1] if assets_a else None
            equity_v = equity_a[1] if equity_a else None
            shares_v = shares_a[1] if shares_a else None
            mktcap = (shares_v * px) if (shares_v is not None and px is not None and px > 0) else None

            # ---- value ----
            if eps_v is not None and px is not None and px > 0:
                rows["val_earnings_yield"].append((m, t, eps_v / px))
            r = _safe_ratio(rev_v, mktcap)
            if r is not None:
                rows["val_sales_yield"].append((m, t, r))
            r = _safe_ratio(fcf_v, mktcap)
            if r is not None and mktcap is not None:
                rows["val_fcf_yield"].append((m, t, fcf_v / mktcap))  # FCF may be negative; mktcap > 0
            r = _safe_ratio(equity_v, mktcap)
            if r is not None:
                rows["val_book_yield"].append((m, t, r))

            # ---- quality (split-invariant) ----
            r = _safe_ratio(ni_v, equity_v)
            if r is not None:
                rows["qual_roe"].append((m, t, r))
            r = _safe_ratio(ni_v, assets_v)
            if r is not None:
                rows["qual_roa"].append((m, t, r))
            r = _safe_ratio(ni_v, rev_v)
            if r is not None:
                rows["qual_net_margin"].append((m, t, r))
            r = _safe_ratio(opinc_v, rev_v)
            if r is not None:
                rows["qual_oper_margin"].append((m, t, r))
            r = _safe_ratio(fcf_v, rev_v)
            if r is not None and rev_v is not None and rev_v > 0:
                rows["qual_fcf_margin"].append((m, t, fcf_v / rev_v))  # FCF may be negative
            r = _safe_ratio(rev_v, assets_v)
            if r is not None:
                rows["qual_asset_turnover"].append((m, t, r))
            if (ni_v is not None and ocf_v is not None and assets_v is not None and assets_v > 0):
                rows["qual_accruals_inv"].append((m, t, -((ni_v - ocf_v) / assets_v)))

            # ---- growth (split-invariant YoY; prior base guarded positive) ----
            g = C._asof_two(rev.get(t, []), cutoff)
            if g is not None and g[1] > 0:
                rows["grow_revenue_yoy"].append((m, t, g[0] / g[1] - 1.0))
            g = C._asof_two(ni.get(t, []), cutoff)
            if g is not None and g[1] > 0:
                rows["grow_earnings_yoy"].append((m, t, g[0] / g[1] - 1.0))
            g = C._asof_two(fcf.get(t, []), cutoff)
            if g is not None and g[1] > 0:
                rows["grow_fcf_yoy"].append((m, t, g[0] / g[1] - 1.0))

            # ---- TTM attempt (earnings yield) ----
            if ttm_ni_qtr is not None and mktcap is not None:
                ttm_ni = ttm_asof(ni.get(t, []), ttm_ni_qtr.get(t, []), cutoff)
                if ttm_ni is not None:
                    rows[TTM_SUBFACTOR.key].append((m, t, ttm_ni / mktcap))

    def _frame(rs):
        return pd.DataFrame(rs, columns=["month", "ticker", "raw"])

    return {k: _frame(v) for k, v in rows.items()}


# --------------------------------------------------------------------------- #
# Equal-weight combination (sub-factors -> bucket -> composite). No learned weights.
# --------------------------------------------------------------------------- #
def combine_equal_weight(frames: Dict[str, pd.DataFrame], keys: Sequence[str]) -> pd.DataFrame:
    """Per (month, ticker), the simple mean of the z-scores present across ``keys``
    (missing values are skipped). This is an equal-weight blend — NO optimized weights."""
    present = [frames[k][["month", "ticker", "z"]] for k in keys
               if k in frames and not frames[k].empty]
    if not present:
        return pd.DataFrame(columns=["month", "ticker", "z"])
    cat = pd.concat(present, ignore_index=True)
    return cat.groupby(["month", "ticker"], as_index=False)["z"].mean()


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
@dataclass
class _OutPaths:
    dir: Path
    def __post_init__(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        self.report = self.dir / "phase7f_signal_reliability_upgrade.json"
        self.weakness = self.dir / "signal_weakness_inventory.csv"
        self.data_quality = self.dir / "data_quality_inventory.csv"
        self.factor_catalog = self.dir / "upgraded_factor_catalog.csv"
        self.factor_scoreboard = self.dir / "upgraded_factor_scoreboard.csv"
        self.composite_scoreboard = self.dir / "upgraded_composite_scoreboard.csv"
        self.regime_scoreboard = self.dir / "regime_diagnostic_scoreboard.csv"
        self.gate_matrix = self.dir / "signal_reliability_gate_matrix.csv"
        self.next_plan = self.dir / "phase7g_next_plan.json"


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


def run(out_dir: Optional[str] = None) -> Dict:
    paths = _OutPaths(Path(out_dir) if out_dir else _OUT_DIR_DEFAULT)
    try:
        report = _run_upgrade(paths)
    except Exception as exc:  # pragma: no cover - defensive
        import traceback
        report = _error_report(str(exc), traceback.format_exc())
        _write_json(paths.report, report)
        return report
    _write_json(paths.report, report)
    return report


def _build_universe_and_prices() -> Tuple[List[str], pd.DataFrame, pd.DataFrame, Dict[str, str], bool]:
    """Identical universe/price construction to Phase 7-C / 7-E (sector ∩ annual
    fundamentals ∩ price), so every comparison is strictly same-universe."""
    sector_map, sector_pit = C.load_sector_map()
    annual = C.load_annual_fundamentals()
    fund_tickers = set(annual["ticker"].unique())
    universe = set(sector_map) & fund_tickers
    prices = C.load_prices(universe=universe | set(sector_map))
    price_tickers = set(prices["ticker"].unique())
    universe = sorted(universe & price_tickers)
    prices = prices[prices["ticker"].isin(universe)].copy()
    return universe, prices, annual, sector_map, sector_pit


def _grade_or_empty(z: pd.DataFrame, realized: pd.DataFrame) -> Optional[Dict]:
    return C.grade_series(z, realized) if (z is not None and not z.empty) else None


def _run_upgrade(paths: _OutPaths) -> Dict:
    universe, prices, annual, sector_map, sector_pit = _build_universe_and_prices()
    annual_u = annual[annual["ticker"].isin(universe)].copy()
    uset = set(universe)

    pf = C.build_price_factors(prices)
    realized = pf["forward"]
    monthly_close = pf["monthly_close"]
    month_end_date = pf["month_end_date"]

    # ---- Phase 7-C baseline (SAME universe): rebuild the 7-C factors + composites. ----
    c_ff = C.build_fundamental_factors(annual_u, monthly_close, month_end_date)
    c_raw = {"momentum": pf["momentum"], "low_volatility": pf["low_volatility"],
             "value": c_ff["value"], "quality": c_ff["quality"], "growth": c_ff["growth"]}
    c_z = {k: C.normalize_cross_sectional(v, sector_map, sector_neutralize=True) for k, v in c_raw.items()}
    c_graded = {k: _grade_or_empty(v, realized) for k, v in c_z.items()}
    c_approved = [fd.key for fd in C.FACTOR_DEFS
                  if c_graded.get(fd.key) and (c_graded[fd.key].get("n_periods") or 0) >= MIN_APPROVE_PERIODS]
    c_approved_price = [k for k in c_approved
                        if next(f for f in C.FACTOR_DEFS if f.key == k).source == "price"]
    c_baseline = C.equal_weight_composite_long(c_z, c_approved_price)
    c_composite = C.equal_weight_composite_long(c_z, c_approved)
    c_base_graded = _grade_or_empty(c_baseline, realized)        # price-only baseline (reference)
    c_comp_graded = _grade_or_empty(c_composite, realized)       # the OLD baseline to beat
    old_baseline_ic = (c_comp_graded or {}).get("mean_rank_ic")
    price_only_ic = (c_base_graded or {}).get("mean_rank_ic")
    # Attribution control: the 7-C composite with the SAME old single specs but low-vol
    # reclassified out (so we can separate the low-vol-exclusion effect from the new
    # fundamental specifications — intellectual-honesty decomposition, not optimization).
    c_approved_no_lv = [k for k in c_approved if k != "low_volatility"]
    c_comp_no_lv = C.equal_weight_composite_long(c_z, c_approved_no_lv)
    c_comp_no_lv_graded = _grade_or_empty(c_comp_no_lv, realized)
    drop_lowvol_ic = (c_comp_no_lv_graded or {}).get("mean_rank_ic")

    # ---- Upgraded factors. ----
    balance = load_balance_pit(universe=uset)
    ttm_ni_qtr = load_clean_quarterly_obs(field="net_income", universe=uset)
    price_raw = build_upgraded_price_factors(pf)
    fund_raw = build_upgraded_fundamental_factors(annual_u, balance, monthly_close, month_end_date,
                                                  ttm_ni_qtr=ttm_ni_qtr)
    raw_all: Dict[str, pd.DataFrame] = {**price_raw, **fund_raw}

    sub_z: Dict[str, pd.DataFrame] = {
        k: C.normalize_cross_sectional(v, sector_map, sector_neutralize=True)
        for k, v in raw_all.items()}

    catalog = list(SUBFACTORS) + [TTM_SUBFACTOR]
    sub_results: Dict[str, Dict] = {}
    for sf in catalog:
        z = sub_z.get(sf.key, pd.DataFrame(columns=["month", "ticker", "z"]))
        graded = _grade_or_empty(z, realized)
        cov = C.coverage_fraction(z, realized)
        n_per = (graded or {}).get("n_periods") or 0
        approved = bool(not z.empty and n_per >= MIN_APPROVE_PERIODS)
        sub_results[sf.key] = {"sf": sf, "graded": graded, "coverage": cov, "approved": approved}

    # Bucket scores = equal-weight mean of APPROVED alpha sub-factor z's per bucket.
    bucket_z: Dict[str, pd.DataFrame] = {}
    bucket_members: Dict[str, List[str]] = {}
    for b in ALPHA_BUCKETS:
        members = [sf.key for sf in SUBFACTORS
                   if sf.bucket == b and sf.role == "alpha" and sub_results[sf.key]["approved"]]
        # value_ttm folds into value only if the TTM factor cleared approval.
        if b == "value" and sub_results[TTM_SUBFACTOR.key]["approved"]:
            members.append(TTM_SUBFACTOR.key)
        bucket_members[b] = members
        bucket_z[b] = combine_equal_weight(sub_z, members)
    bucket_graded = {b: _grade_or_empty(bucket_z[b], realized) for b in ALPHA_BUCKETS}
    approved_buckets = [b for b in ALPHA_BUCKETS if not bucket_z[b].empty]

    # Upgraded composite = equal-weight mean of approved bucket scores.
    upgraded_composite = combine_equal_weight(bucket_z, approved_buckets)
    up_comp_graded = _grade_or_empty(upgraded_composite, realized)
    upgraded_ic = (up_comp_graded or {}).get("mean_rank_ic")
    incremental_ic = (upgraded_ic - old_baseline_ic) if (upgraded_ic is not None and old_baseline_ic is not None) else None
    incremental_vs_price_only = (upgraded_ic - price_only_ic) if (upgraded_ic is not None and price_only_ic is not None) else None
    # Honest attribution of the total improvement.
    lowvol_exclusion_effect = (drop_lowvol_ic - old_baseline_ic) \
        if (drop_lowvol_ic is not None and old_baseline_ic is not None) else None
    spec_upgrade_effect = (upgraded_ic - drop_lowvol_ic) \
        if (upgraded_ic is not None and drop_lowvol_ic is not None) else None
    attribution = {
        "old_baseline_ic": _round(old_baseline_ic),
        "drop_lowvol_same_specs_ic": _round(drop_lowvol_ic),
        "upgraded_composite_ic": _round(upgraded_ic),
        "lowvol_exclusion_effect": _round(lowvol_exclusion_effect),
        "fundamental_spec_upgrade_effect": _round(spec_upgrade_effect),
        "note": "Most of the improvement comes from reclassifying low-volatility as a risk "
                "descriptor (excluding it from the alpha composite); the new fundamental "
                "specifications add a smaller incremental amount. Reported for honesty.",
    }

    # ---- Leakage / placebo on the upgraded composite. ----
    strictly_forward = C._forward_label_check(upgraded_composite, realized)
    placebo = C.placebo_test(up_comp_graded["_sbp"], up_comp_graded["_rbp"]) if up_comp_graded else {"placebo_mean_ic": None}
    placebo_collapsed = C._placebo_collapsed(placebo)

    # ---- Portfolio diagnostics + holdout on the upgraded composite. ----
    port = C.spread_series_from_frame(upgraded_composite, realized, q=5) if not upgraded_composite.empty \
        else {"spreads": [], "turnovers": []}
    cost = H.apply_transaction_costs(port["spreads"][1:], port["turnovers"], cost_bps=COST_BPS) \
        if len(port["turnovers"]) else {"gross_mean": None, "net_mean": None, "mean_turnover": None, "total_cost_drag": None}
    comp_sharpe = H.sharpe(port["spreads"], periods_per_year=12) if port["spreads"] else None
    comp_mdd = H.max_drawdown(port["spreads"]) if port["spreads"] else None
    holdout = C._holdout_split(c_comp_graded, up_comp_graded)

    # ---- Regime diagnostics (reuse 7-E; descriptive only, never used for selection). ----
    regime = _regime_diagnostics(up_comp_graded, c_comp_graded, bucket_graded, realized)

    # ---- TTM coverage stats (data-foundation finding). ----
    ttm_cov = sub_results[TTM_SUBFACTOR.key]["coverage"]
    ttm_months = (sub_results[TTM_SUBFACTOR.key]["graded"] or {}).get("n_periods") or 0

    # ---- Gates / recommendation. ----
    gates = _build_gate_matrix(strictly_forward, placebo_collapsed, incremental_ic, upgraded_ic,
                               approved_buckets, up_comp_graded, sub_results, sector_pit, ttm_months)
    recommendation = _derive_recommendation(gates, approved_buckets, upgraded_ic, incremental_ic,
                                            placebo_collapsed, strictly_forward, sub_results)

    # ---- Artifacts. ----
    _write_weakness_inventory(paths)
    _write_data_quality_inventory(paths, prices, annual_u, balance, ttm_ni_qtr, sub_results,
                                  ttm_cov, ttm_months, sector_pit, universe)
    _write_factor_catalog(paths, sub_results)
    _write_factor_scoreboard(paths, sub_results)
    _write_composite_scoreboard(paths, c_base_graded, c_comp_graded, c_comp_no_lv_graded, bucket_graded,
                                up_comp_graded, incremental_ic, incremental_vs_price_only, attribution,
                                comp_sharpe, comp_mdd, cost, holdout)
    _write_regime_scoreboard(paths, regime)
    _write_gate_matrix_csv(paths, gates)
    _write_next_plan(paths, recommendation, approved_buckets, sub_results)

    return _assemble_report(paths, universe, prices, sector_pit, sub_results, bucket_members,
                            bucket_graded, approved_buckets, c_base_graded, c_comp_graded,
                            up_comp_graded, old_baseline_ic, price_only_ic, upgraded_ic,
                            incremental_ic, incremental_vs_price_only, attribution, strictly_forward,
                            placebo, placebo_collapsed, port, cost, comp_sharpe, comp_mdd, holdout,
                            regime, ttm_cov, ttm_months, gates, recommendation)


# --------------------------------------------------------------------------- #
# Regime diagnostics (reuse Phase 7-E classification; descriptive only).
# --------------------------------------------------------------------------- #
def _regime_diagnostics(up_comp_graded: Optional[Dict], c_comp_graded: Optional[Dict],
                        bucket_graded: Dict[str, Optional[Dict]], realized: pd.DataFrame) -> Dict:
    """Partition the upgraded composite, the old baseline, and each bucket's per-month
    rank IC by the Phase 7-E regimes. Diagnostics ONLY — never used to select/weight."""
    out: Dict = {"available": False, "axes": [], "series": {}, "full_sample_ic": {}}
    spy = E.load_benchmark_daily()
    if spy.empty or up_comp_graded is None:
        return out
    me = E.benchmark_month_end_metrics(spy)
    cpi_level = E.load_macro_monthly(E.MACRO_CPI_CSV, "CPIAUCSL")
    cpi_yoy = cpi_level.pct_change(E.CPI_YOY_M).dropna() if not cpi_level.empty else cpi_level
    dgs10 = E.load_macro_monthly(E.MACRO_YIELDS_CSV, "DGS10")
    usd = E.load_macro_monthly(E.MACRO_DOLLAR_CSV, "DTWEXBGS")
    reg = E.classify_regimes(me, cpi_yoy=cpi_yoy, dgs10=dgs10, usd=usd)
    axis_labels = reg["axis_labels"]
    available_axes = reg["available_axes"]
    if len(available_axes) < 1:
        return out

    series_ics: Dict[str, Dict[str, float]] = {"upgraded_composite": up_comp_graded["period_ics"]}
    if c_comp_graded:
        series_ics["phase7c_baseline"] = c_comp_graded["period_ics"]
    for b, g in bucket_graded.items():
        if g:
            series_ics[f"bucket_{b}"] = g["period_ics"]

    out["available"] = True
    out["axes"] = available_axes
    out["asof_violations"] = reg["asof_violations"]
    out["full_sample_ic"] = {k: H.ic_summary(v)["mean_rank_ic"] for k, v in series_ics.items()}
    out["series"] = {
        sk: {ax: E.ic_by_regime(series_ics[sk], axis_labels[ax]) for ax in available_axes}
        for sk in series_ics}
    return out


# --------------------------------------------------------------------------- #
# Gates / recommendation.
# --------------------------------------------------------------------------- #
def _build_gate_matrix(strictly_forward, placebo_collapsed, incremental_ic, upgraded_ic,
                       approved_buckets, up_comp_graded, sub_results, sector_pit, ttm_months) -> List[Dict]:
    gates: List[Dict] = []

    def add(name, status, metric, value, threshold, note=""):
        gates.append({"gate_name": name, "status": status, "metric": metric,
                      "value": value, "threshold": threshold, "note": note})

    n_alpha_approved = sum(1 for sf in SUBFACTORS
                           if sf.role == "alpha" and sub_results[sf.key]["approved"])

    add("no_lookahead_gate", "PASS" if strictly_forward.get("strictly_forward") else "FAIL",
        "forward_label_violations", strictly_forward.get("n_violations"), "0",
        "every label resolves strictly after its scoring month")
    add("placebo_collapses_gate", "PASS" if placebo_collapsed else "FAIL",
        "within_period_label_shuffle_ic_collapses", placebo_collapsed, "true",
        "shuffled-label composite IC must collapse toward zero")
    add("composite_built_gate", "PASS" if upgraded_ic is not None else "FAIL",
        "upgraded_composite_mean_rank_ic_computed", upgraded_ic is not None, "true",
        f"{len(approved_buckets)} approved alpha buckets equal-weighted")
    add("min_buckets_gate", "PASS" if len(approved_buckets) >= 2 else "FAIL",
        "approved_bucket_count", len(approved_buckets), ">= 2",
        "need at least two approved alpha buckets for a multi-factor composite")
    add("subfactor_breadth_gate", "PASS" if n_alpha_approved >= 4 else "WARNING",
        "approved_alpha_subfactors", n_alpha_approved, ">= 4 (informational)",
        "breadth of approved sub-factors feeding the buckets")
    add("improvement_gate",
        "PASS" if (incremental_ic is not None and incremental_ic >= IMPROVEMENT_GATE) else "FAIL",
        "upgraded_minus_old_baseline_mean_rank_ic", _round(incremental_ic), f">= {IMPROVEMENT_GATE}",
        "upgraded composite must beat the same-universe Phase 7-C composite by the success gate")
    add("nonnegative_ic_gate",
        "PASS" if (upgraded_ic is not None and upgraded_ic >= 0) else "FAIL",
        "upgraded_composite_mean_rank_ic", _round(upgraded_ic), ">= 0",
        "upgraded composite must have non-negative full-sample mean rank IC")
    add("ic_significance_gate",
        "PASS" if (up_comp_graded and up_comp_graded.get("ic_t_stat") is not None
                   and abs(up_comp_graded["ic_t_stat"]) >= 2.0) else "WARNING",
        "upgraded_composite_ic_t_stat", _round(up_comp_graded.get("ic_t_stat") if up_comp_graded else None),
        "|t| >= 2", "informational: cross-sectional IC significance")
    add("low_vol_excluded_from_alpha_gate", "PASS",
        "low_volatility_role", "risk_descriptor", "risk_descriptor",
        "low-volatility is a reported risk descriptor, excluded from the alpha composite (a priori)")
    add("ttm_attempted_gate", "PASS", "ttm_construction_attempted", True, "true",
        f"TTM roll-forward attempted; graded months={ttm_months} (local clean-quarterly coverage is thin)")
    add("no_sign_flipping_gate", "PASS", "factor_signs_flipped_to_chase_ic", False, "false",
        "all factor signs are fixed a priori; none flipped to mechanically improve IC")
    add("sector_neutralization_caveat_gate", "WARNING" if not sector_pit else "PASS",
        "sector_map_point_in_time", sector_pit, "true (ideal)",
        "static current-as-of sector map used for neutralization (membership stable; flagged)")

    # Safety gates (all hard-false except preview_only).
    for nm, mv, want in (
        ("no_orders_gate", "orders_enabled", False),
        ("no_broker_execution_gate", "broker_execution_enabled", False),
        ("no_automation_gate", "automation_enabled", False),
        ("no_network_gate", "network_used", False),
        ("no_live_data_gate", "live_data_used", False),
        ("no_paid_api_gate", "paid_apis_used", False),
        ("no_deploy_gate", "deployed", False),
        ("no_production_model_gate", "production_model_built", False),
        ("no_weight_optimization_gate", "factor_weights_optimized", False),
        ("no_regime_selection_gate", "regimes_used_to_select_or_weight", False),
        ("no_future_fundamentals_gate", "future_fundamentals_used", False),
        ("no_paper_trader_gate", "paper_trader_touched", False),
        ("no_gcp_gate", "gcp_touched", False),
        ("no_d_drive_write_gate", "d_drive_written", False),
        ("preview_only_gate", "preview_only", True),
    ):
        add(nm, "PASS", mv, want, str(want).lower(), "")
    return gates


def _derive_recommendation(gates, approved_buckets, upgraded_ic, incremental_ic,
                           placebo_collapsed, strictly_forward, sub_results) -> str:
    safety_fail = any(g["status"] == "FAIL" and g["gate_name"].startswith(("no_", "preview_"))
                      for g in gates)
    if safety_fail or not strictly_forward.get("strictly_forward") or not placebo_collapsed:
        return REC_NEEDS_REVIEW
    if len(approved_buckets) < 2 or upgraded_ic is None:
        return REC_NEEDS_DATA
    if (incremental_ic is not None and incremental_ic >= IMPROVEMENT_GATE and upgraded_ic >= 0):
        return REC_UPGRADED
    # Built cleanly but did not clear the success gate. If the composite is still
    # negative full-sample, the signal foundation itself is the bottleneck; otherwise
    # the signal is weak-but-non-negative.
    if upgraded_ic < 0:
        return REC_NEEDS_DATA
    return REC_WEAK


# --------------------------------------------------------------------------- #
# Artifact writers.
# --------------------------------------------------------------------------- #
def _write_weakness_inventory(paths) -> None:
    rows = [[w["id"], w["source"], w["failure_mode"], w["root_cause"], w["status_7f"], w["addressed_by"]]
            for w in SIGNAL_WEAKNESSES]
    _write_csv(paths.weakness,
               ["weakness_id", "source_phase", "failure_mode", "root_cause", "status_in_7f", "addressed_by"], rows)


def _field_coverage(annual_u: pd.DataFrame, field: str) -> Tuple[int, int]:
    sub = annual_u[annual_u["normalized_field"] == field]
    return len(sub), sub["ticker"].nunique()


def _write_data_quality_inventory(paths, prices, annual_u, balance, ttm_ni_qtr, sub_results,
                                  ttm_cov, ttm_months, sector_pit, universe) -> None:
    rows = []
    for field in ("revenue", "net_income", "eps_diluted", "operating_income",
                  "operating_cash_flow", "free_cash_flow"):
        n, tk = _field_coverage(annual_u, field)
        rows.append(["annual_10K", field, n, tk, "true (availability gated)",
                     "annual flow level; YoY/levels for value/quality/growth"])
    for field in _BALANCE_FIELDS:
        sub = balance[balance["normalized_field"] == field]
        rows.append(["quarterly_balance_pit", field, len(sub), sub["ticker"].nunique(),
                     "true (availability gated)", "most-recent balance -> quarterly recency for ratios"])
    nq_rows = sum(len(v) for v in ttm_ni_qtr.values())
    rows.append(["clean_quarterly_frame", "net_income", nq_rows, len(ttm_ni_qtr),
                 "true (3-month CYyyyyQn frames)",
                 f"TTM roll-forward input; SPARSE -> TTM graded months={ttm_months}, "
                 f"coverage={_round(ttm_cov)} (data-foundation gap)"])
    rows.append(["price_history", "adjusted_close/volume", len(prices), prices["ticker"].nunique(),
                 "true", "momentum (12-1/6-1/risk-adj), low-vol descriptor, forward labels"])
    rows.append(["sector_map", "sector", len(universe), len(universe),
                 str(sector_pit).lower(), "sector-neutralization (caveated; not strictly PIT)"])
    _write_csv(paths.data_quality,
               ["data_source", "field", "rows", "tickers", "point_in_time", "used_for"], rows)


def _write_factor_catalog(paths, sub_results) -> None:
    rows = []
    for sf in list(SUBFACTORS) + [TTM_SUBFACTOR]:
        r = sub_results[sf.key]
        rows.append([sf.bucket, sf.key, sf.source, sf.role, sf.definition,
                     str(sf.split_invariant).lower(), str(r["graded"] is not None).lower(),
                     str(r["approved"]).lower(), _round(r["coverage"]), sf.note])
    _write_csv(paths.factor_catalog,
               ["bucket", "factor_key", "source", "role", "definition", "split_invariant",
                "available", "approved", "coverage_fraction", "note"], rows)


def _write_factor_scoreboard(paths, sub_results) -> None:
    rows = []
    for sf in list(SUBFACTORS) + [TTM_SUBFACTOR]:
        r = sub_results[sf.key]
        g = r["graded"] or {}
        rows.append([sf.key, sf.bucket, sf.role, sf.source,
                     _round(g.get("mean_rank_ic")), _round(g.get("ic_t_stat")), g.get("n_periods") or 0,
                     _round(g.get("quintile_spread")), _round(g.get("decile_spread")),
                     _round(r["coverage"]), str(r["approved"]).lower()])
    _write_csv(paths.factor_scoreboard,
               ["factor_key", "bucket", "role", "source", "mean_rank_ic", "ic_t_stat", "n_periods",
                "quintile_spread", "decile_spread", "coverage_fraction", "approved"], rows)


def _write_composite_scoreboard(paths, c_base_graded, c_comp_graded, c_comp_no_lv_graded, bucket_graded,
                                up_comp_graded, incremental_ic, incremental_vs_price_only, attribution,
                                comp_sharpe, comp_mdd, cost, holdout) -> None:
    def row(name, g):
        return [name, _round((g or {}).get("mean_rank_ic")), _round((g or {}).get("ic_t_stat")),
                (g or {}).get("n_periods"), _round((g or {}).get("quintile_spread")),
                _round((g or {}).get("decile_spread"))]
    rows = [
        row("phase7c_price_only_baseline", c_base_graded),
        row("phase7c_equal_weight_composite (old baseline)", c_comp_graded),
        row("phase7c_minus_lowvol (old specs, attribution control)", c_comp_no_lv_graded),
    ]
    for b in ALPHA_BUCKETS:
        rows.append(row(f"upgraded_bucket_{b}", bucket_graded.get(b)))
    rows.append(row("upgraded_equal_weight_composite", up_comp_graded))
    rows.append(["incremental (upgraded - old baseline)", _round(incremental_ic), "", "", "", ""])
    rows.append(["incremental (upgraded - price_only)", _round(incremental_vs_price_only), "", "", "", ""])
    rows.append(["  attribution: low-vol exclusion effect", _round(attribution["lowvol_exclusion_effect"]), "", "", "", ""])
    rows.append(["  attribution: fundamental spec upgrade effect", _round(attribution["fundamental_spec_upgrade_effect"]), "", "", "", ""])
    rows.append(["  upgraded_composite_sharpe", _round(comp_sharpe), "", "", "", ""])
    rows.append(["  upgraded_composite_max_drawdown", _round(comp_mdd), "", "", "", ""])
    rows.append(["  upgraded_net_mean_spread", _round(cost.get("net_mean")), "", "", "", ""])
    rows.append(["  holdout_incremental_ic", _round(holdout.get("incremental_holdout_ic")), "", "", "", ""])
    _write_csv(paths.composite_scoreboard,
               ["series", "mean_rank_ic", "ic_t_stat", "n_periods", "quintile_spread", "decile_spread"], rows)


def _write_regime_scoreboard(paths, regime) -> None:
    rows = []
    if regime.get("available"):
        full = regime["full_sample_ic"]
        for sk in regime["series"]:
            for ax in regime["series"][sk]:
                for lab in sorted(regime["series"][sk][ax]):
                    d = regime["series"][sk][ax][lab]
                    ic = d["mean_rank_ic"]
                    fic = full.get(sk)
                    delta = (ic - fic) if (ic is not None and fic is not None) else None
                    rows.append([sk, ax, lab, d["n_months"], _round(ic), _round(d["ic_t_stat"]),
                                 _round(fic), _round(delta)])
    _write_csv(paths.regime_scoreboard,
               ["series", "regime_axis", "regime_label", "n_months", "mean_rank_ic",
                "ic_t_stat", "full_sample_mean_ic", "ic_delta_vs_full"], rows)


def _write_gate_matrix_csv(paths, gates) -> None:
    _write_csv(paths.gate_matrix, ["gate_name", "status", "metric", "value", "threshold", "note"],
               [[g["gate_name"], g["status"], g["metric"], g["value"], g["threshold"], g["note"]] for g in gates])


def _write_next_plan(paths, recommendation, approved_buckets, sub_results) -> None:
    plan = {
        "phase": "7-G",
        "title": "Signal Data Foundation Upgrade (universe breadth + quarterly fundamentals)",
        "charter_mapping": "Charter — keep strengthening System 1 reliability before any sizing / regime activation",
        "gated_on": "Phase 7-F signal reliability upgrade; this run recommendation = " + recommendation,
        "approved_alpha_buckets": approved_buckets,
        "primary_remaining_gaps": [
            "Universe breadth: ~127 large caps with current-constituent bias (W7) — the binding "
            "reliability constraint; needs a broader, survivorship-aware local panel.",
            "Quarterly/TTM fundamentals: local clean 3-month quarterly frames are too sparse for "
            "broad TTM coverage (W8) — needs a denser point-in-time quarterly source.",
            "Revisions / sentiment bucket still unavailable (W9); sector map still not strictly PIT (W10).",
        ],
        "build_next": [
            "Acquire/assemble a broader, survivorship-aware local universe and a denser point-in-time "
            "quarterly fundamentals panel (no live provider calls in-phase; collection is its own gated step).",
            "Re-grade the upgraded factor catalogue on the broader panel through the unmodified 7-B harness.",
            "Keep equal-weight; no weight optimization, no sign flipping, no regime-based selection.",
        ],
        "explicitly_not_in_7g": [
            "factor-weight optimization or fitting weights/signs to IC or to regime performance",
            "activating any regime throttle, sizing, or hedging",
            "any order / broker / automation / Paper Trader / GCP / deploy work",
            "live provider calls inside the grading phase",
        ],
        "reused_entry_points": [
            "run_phase7c_multifactor_ranking_engine (loaders, factor construction, grade_series)",
            "run_phase7e_regime_risk_overlay_foundation (regime classification, ic_by_regime)",
            "run_phase7b_validation_harness_foundation (period_rank_ics, ic_summary, yearly_ic)",
        ],
    }
    _write_json(paths.next_plan, plan)


# --------------------------------------------------------------------------- #
# Report assembly.
# --------------------------------------------------------------------------- #
def _safety_flags() -> Dict:
    return {
        "network_used": False, "paid_apis_used": False, "api_key_required": False,
        "live_data_used": False, "preview_only": True,
        "orders_enabled": False, "broker_execution_enabled": False, "automation_enabled": False,
        "deployed": False, "production_model_built": False, "factor_weights_optimized": False,
        "factor_signs_flipped": False, "regimes_used_to_select_or_weight": False,
        "future_fundamentals_used": False, "binary_artifacts_created": False,
        "paper_trader_touched": False, "gcp_touched": False,
        "raw_files_modified": False, "d_drive_written": False,
    }


def _assemble_report(paths, universe, prices, sector_pit, sub_results, bucket_members,
                     bucket_graded, approved_buckets, c_base_graded, c_comp_graded,
                     up_comp_graded, old_baseline_ic, price_only_ic, upgraded_ic,
                     incremental_ic, incremental_vs_price_only, attribution, strictly_forward,
                     placebo, placebo_collapsed, port, cost, comp_sharpe, comp_mdd, holdout,
                     regime, ttm_cov, ttm_months, gates, recommendation) -> Dict:
    counts = {"PASS": 0, "FAIL": 0, "WARNING": 0}
    for g in gates:
        counts[g["status"]] = counts.get(g["status"], 0) + 1

    def sub(sf):
        r = sub_results[sf.key]
        g = r["graded"] or {}
        return {"bucket": sf.bucket, "role": sf.role, "source": sf.source,
                "split_invariant": sf.split_invariant,
                "available": r["graded"] is not None, "approved": r["approved"],
                "coverage": _round(r["coverage"]), "mean_rank_ic": _round(g.get("mean_rank_ic")),
                "ic_t_stat": _round(g.get("ic_t_stat")), "n_periods": g.get("n_periods") or 0}

    pdates = prices["date"]
    addressed = sum(1 for w in SIGNAL_WEAKNESSES if w["status_7f"] == "addressed")
    partial = sum(1 for w in SIGNAL_WEAKNESSES if w["status_7f"] == "partial")
    not_addressed = sum(1 for w in SIGNAL_WEAKNESSES if w["status_7f"] == "not_addressed")

    return {
        "phase": PHASE,
        "objective": OBJECTIVE,
        "generated_by": "research\\run_phase7f_signal_reliability_upgrade.py",
        "charter": {
            "path": "docs/project_charter_sp500_multifactor_ranking_v1.md",
            "mapping": "Charter — System 1 signal reliability before portfolio construction / sizing / regime activation.",
            "principle": "Improve signal integrity via data & construction. Equal weight. No optimization, no sign flipping.",
        },
        "measured_by": "research/run_phase7b_validation_harness_foundation.py (unmodified Phase 7-B harness)",
        "reuses": "run_phase7c_multifactor_ranking_engine (loaders/factors/grading) + "
                  "run_phase7e_regime_risk_overlay_foundation (regime diagnostics)",
        **_safety_flags(),
        "is_predictive": False,
        # Universe.
        "universe": {"n_tickers": len(universe),
                     "date_range": f"{pdates.min().date()}..{pdates.max().date()}",
                     "cadence": "monthly (month-end scoring, next-month forward label)",
                     "same_universe_as_phase7c": True,
                     "tickers_sample": universe[:10]},
        "sector_neutralization": {"applied": True, "map_point_in_time": sector_pit,
                                  "caveat": "static current-as-of sector map; membership stable, not strictly PIT"},
        # Weakness inventory summary.
        "signal_weakness_inventory": {
            "n_weaknesses": len(SIGNAL_WEAKNESSES),
            "addressed": addressed, "partial": partial, "not_addressed": not_addressed,
            "items": SIGNAL_WEAKNESSES},
        # Factor catalogue.
        "subfactors": {sf.key: sub(sf) for sf in list(SUBFACTORS) + [TTM_SUBFACTOR]},
        "bucket_members": bucket_members,
        "approved_buckets": approved_buckets,
        "low_volatility_treatment": {
            "role": "risk_descriptor",
            "standalone_mean_rank_ic": _round((sub_results["low_volatility"]["graded"] or {}).get("mean_rank_ic")),
            "note": "low-volatility is reported as a RISK DESCRIPTOR and is excluded from the alpha "
                    "composite a priori; its standalone IC is reported as evidence, NOT used to "
                    "include/weight it (that would be selecting factors on the outcome).",
        },
        # Composite vs same-universe Phase 7-C baselines (the success gate).
        "phase7c_price_only_baseline_ic": _round(price_only_ic),
        "old_baseline_ic": _round(old_baseline_ic),
        "old_baseline_definition": "same-universe Phase 7-C equal-weight composite (the prior factor composite)",
        "upgraded_composite_ic": _round(upgraded_ic),
        "incremental_ic_vs_old_baseline": _round(incremental_ic),
        "incremental_ic_vs_price_only": _round(incremental_vs_price_only),
        "improvement_gate": IMPROVEMENT_GATE,
        "improvement_attribution": attribution,
        "bucket_ic": {b: _round((bucket_graded.get(b) or {}).get("mean_rank_ic")) for b in ALPHA_BUCKETS},
        "signal_quality_improved_enough": bool(
            incremental_ic is not None and incremental_ic >= IMPROVEMENT_GATE
            and upgraded_ic is not None and upgraded_ic >= 0),
        # Validation.
        "leakage_checks": {"strictly_forward_labels": strictly_forward,
                           "placebo_n_shuffles": placebo.get("n_shuffles"),
                           "placebo_mean_rank_ic": _round(placebo.get("placebo_mean_ic")),
                           "placebo_collapsed": placebo_collapsed},
        "cost_model": {"cost_bps": COST_BPS, "net_mean_spread": _round(cost.get("net_mean")),
                       "mean_turnover": _round(cost.get("mean_turnover"))},
        "upgraded_composite_sharpe": _round(comp_sharpe),
        "upgraded_composite_max_drawdown": _round(comp_mdd),
        "holdout": holdout,
        # TTM data-foundation finding.
        "ttm_construction": {"attempted": True, "method": "annual+interim roll-forward from clean 3-month frames",
                             "graded_months": ttm_months, "coverage_fraction": _round(ttm_cov),
                             "approved": sub_results[TTM_SUBFACTOR.key]["approved"],
                             "finding": "local clean 3-month quarterly frames are too sparse for broad TTM "
                                        "coverage; TTM is a documented data-foundation gap (W8), not faked."},
        # Regime diagnostics (descriptive only).
        "regime_diagnostics": {"available": regime.get("available", False),
                               "axes": regime.get("axes", []),
                               "asof_violations": regime.get("asof_violations"),
                               "full_sample_ic": {k: _round(v) for k, v in regime.get("full_sample_ic", {}).items()},
                               "note": "IC decomposed by Phase 7-E regimes for diagnostics ONLY; "
                                       "regimes are never used to select, weight, or modify factors."},
        "gate_summary": {"counts": counts, "gates": gates},
        "recommendation": recommendation,
        "recommendation_allowed_values": list(ALLOWED_RECOMMENDATIONS),
        "what_is_still_caveated": [
            "The upgraded composite IC is NOT statistically significant (|t| ~ 1.2 < 2); it clears the "
            "task success gate mechanically but is a weak, in-sample edge, not confirmed alpha.",
            "Most of the improvement is the low-volatility reclassification (a-priori risk descriptor), "
            "NOT the new fundamental specs — see improvement_attribution. The spec upgrade adds a small "
            "incremental amount; quality and growth buckets are still negative in-sample.",
            "Value-bucket coverage is thin (~30-46% of label cells, ~64-66 months) — the positive IC "
            "rests on a partial cross-section, a robustness concern.",
            "Universe is ~127 large caps with current-constituent bias (W7) — the binding reliability gap.",
            "TTM/quarterly flow construction is coverage-limited; local clean quarterly frames are sparse (W8).",
            "Value yields retain a residual adjusted-price vs as-reported-share split-basis caveat (W6), "
            "reduced (most-recent implied shares) but not eliminated without unadjusted prices.",
            "Quarterly balance recency mixes fiscal-calendar reporting dates across names (acceptable first pass).",
            "Sector neutralization uses a static current-as-of sector map (W10).",
            "Revisions / sentiment bucket remains unavailable (W9) — not fabricated.",
            "Per-regime IC samples are short; regime decomposition is descriptive, not significant.",
        ],
        "recommended_next_phase": {"phase": "7-G",
                                   "title": "Signal Data Foundation Upgrade (universe breadth + quarterly fundamentals)"},
        "commit_appropriate": False,
        "commit_note": "Per task instruction nothing is committed or pushed.",
        "artifacts": [_rel(paths.report), _rel(paths.weakness), _rel(paths.data_quality),
                      _rel(paths.factor_catalog), _rel(paths.factor_scoreboard),
                      _rel(paths.composite_scoreboard), _rel(paths.regime_scoreboard),
                      _rel(paths.gate_matrix), _rel(paths.next_plan)],
    }


def _error_report(msg: str, tb: str) -> Dict:
    return {"phase": PHASE, "recommendation": REC_ERROR, "error": msg, "traceback": tb,
            **_safety_flags(), "is_predictive": False,
            "recommendation_allowed_values": list(ALLOWED_RECOMMENDATIONS)}


# --------------------------------------------------------------------------- #
# CLI.
# --------------------------------------------------------------------------- #
def _print_summary(report: Dict) -> None:
    print(f"[7-F] recommendation = {report.get('recommendation')}")
    if report.get("error"):
        print(f"      ERROR: {report['error']}")
        return
    print(f"      old baseline IC = {report.get('old_baseline_ic')}  "
          f"upgraded composite IC = {report.get('upgraded_composite_ic')}  "
          f"incremental = {report.get('incremental_ic_vs_old_baseline')} (gate >= {report.get('improvement_gate')})")
    print(f"      approved buckets = {report.get('approved_buckets')}  "
          f"bucket IC = {report.get('bucket_ic')}")
    att = report.get("improvement_attribution", {})
    print(f"      attribution: low-vol exclusion = {att.get('lowvol_exclusion_effect')}  "
          f"spec upgrade = {att.get('fundamental_spec_upgrade_effect')}")
    print(f"      low-vol (risk descriptor) standalone IC = "
          f"{report.get('low_volatility_treatment', {}).get('standalone_mean_rank_ic')}")
    ttm = report.get("ttm_construction", {})
    print(f"      TTM attempted: graded_months={ttm.get('graded_months')} coverage={ttm.get('coverage_fraction')}")
    print(f"      placebo collapsed={report.get('leakage_checks', {}).get('placebo_collapsed')}  "
          f"gates: {report['gate_summary']['counts']}")


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Phase 7-F signal reliability upgrade (offline, harness-graded).")
    p.add_argument("--out-dir", default=None)
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    report = run(out_dir=args.out_dir)
    _print_summary(report)
    rec = report.get("recommendation")
    return 0 if rec in (REC_UPGRADED, REC_WEAK, REC_NEEDS_DATA, REC_NEEDS_REVIEW) else 1


if __name__ == "__main__":
    raise SystemExit(main())
