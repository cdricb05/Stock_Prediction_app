"""Phase 7-H — TTM Fundamental Signal Retest.

**Track A (quant brain) research only.** Offline, point-in-time, leakage-safe.
No network, no provider call, no paid API, no API key read or required, no model
trained or deployed, **no factor-weight optimization, no factor-sign flipping to
chase IC, no regime-throttle activation**, no Paper Trader / GCP work, no orders /
broker / automation, no live data call, no binary artifact, no commit, no push.
Reads only local files: the Phase 2K-G price panel (READ ONLY on D:), the committed
SEC fundamentals / sector artifacts, and (for regime diagnostics) local FRED CSVs.
Writes nothing to D:.

Why this phase exists
---------------------
Phase 7-F lifted the equal-weight composite mean rank IC to +0.0177 (t ~ 1.25), but
honestly attributed most of the gain to reclassifying low-volatility out of the alpha
composite rather than to better fundamentals, and its TTM attempt graded 0 months
because local clean 3-month quarterly frames are sparse. Phase 7-G then proved that a
materially DENSER point-in-time quarterly / TTM panel can be built locally by
de-cumulating YTD 10-Q flows (continuity-ready tickers: revenue 68, net_income 106,
operating_cash_flow 116 — all >= MIN_NAMES), and authorized a fundamental-density-only
retest of the 7-F signal.

This phase is that retest. It answers ONE question:

    DID DENSE TTM FUNDAMENTALS IMPROVE SIGNAL QUALITY, YES OR NO?

It is a RESULTS phase, not a plan-only phase. It builds the dense TTM panel from the
Phase 7-G de-cumulation logic (reused EXACTLY), constructs TTM value / quality / growth
factors, preserves the Phase 7-F price-momentum bucket, keeps low-volatility as a risk
descriptor only, combines equal-weight, and grades the final composite through the
UNMODIFIED Phase 7-B harness against the Phase 7-F upgraded composite baseline. The
universe is the SAME ~128-name current-constituent panel (7-G found no broader local
universe and no point-in-time sector history); this is therefore a fundamental-density
retest, NOT a universe/survivorship robustness retest.

What it does NOT do
-------------------
  * broaden the universe / collect data (7-G proved neither is locally possible);
  * optimize factor weights or flip factor signs to chase IC;
  * activate any regime throttle (Phase 7-E regimes stay strictly diagnostic);
  * touch Paper Trader / GCP / brokers / orders / automation.

Leakage safety
--------------
Forward labels are next-month returns resolving strictly after the decision date.
Every TTM observation's availability date is the MAX availability of its four
constituent 3-month legs (de-cumulation legs included), and a TTM level is activated at
month m only if that availability is strictly before month m's market month-end. A
within-period label-permutation placebo must collapse the composite IC toward zero.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Reuse, in order: the 7-C ranking engine (loaders / normalization / grading /
# composite / placebo / holdout), the 7-F reliability upgrade (universe construction,
# price-momentum factors, implied shares, the 7-F baseline composite construction, and
# the regime-diagnostic helper), and the 7-G data-foundation upgrade (the de-cumulation
# that turns YTD 10-Q flows into point-in-time 3-month quarters). No work at import.
from research import run_phase7b_validation_harness_foundation as H
from research import run_phase7c_multifactor_ranking_engine as C
from research import run_phase7f_signal_reliability_upgrade as F
from research import run_phase7g_signal_data_foundation_upgrade as G

PHASE = "7-H"
OBJECTIVE = (
    "Build the dense point-in-time TTM fundamental panel proven feasible in Phase 7-G "
    "(by de-cumulating YTD 10-Q flows), construct TTM value / quality / growth factors, "
    "preserve the Phase 7-F price-momentum bucket, keep low-volatility as a risk "
    "descriptor only, combine EQUAL WEIGHT, and grade the final composite through the "
    "unmodified Phase 7-B harness against the Phase 7-F upgraded composite baseline "
    "(IC +0.0177, t ~ 1.25). One question: did dense TTM fundamentals improve signal "
    "quality? No weight optimization, no sign flipping, no regime selection, no live data."
)

# --------------------------------------------------------------------------- #
# Recommendation vocabulary (exactly the allowed set).
# --------------------------------------------------------------------------- #
REC_IMPROVED = "TTM_SIGNAL_RELIABILITY_IMPROVED"
REC_WEAK = "TTM_SIGNAL_RELIABILITY_WEAK"
REC_NEEDS_UNIVERSE = "NEEDS_BROADER_UNIVERSE_COLLECTION"
REC_NEEDS_REVIEW = "NEEDS_REVIEW"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (REC_IMPROVED, REC_WEAK, REC_NEEDS_UNIVERSE, REC_NEEDS_REVIEW, REC_ERROR)

# --------------------------------------------------------------------------- #
# Phase 7-F published baseline (the bar to beat). Recomputed live as a cross-check.
# --------------------------------------------------------------------------- #
PHASE7F_BASELINE_IC = 0.0177
PHASE7F_BASELINE_T = 1.25
IMPROVEMENT_GATE = 0.005                       # final composite must beat 7-F by this mean rank IC
ABSOLUTE_IC_GATE = PHASE7F_BASELINE_IC + IMPROVEMENT_GATE   # 0.0227 (task-specified)
BASELINE_RECOMPUTE_TOL = 0.004                 # sanity band around the published baseline

# --------------------------------------------------------------------------- #
# Config (fixed, documented; NO tuning loop, NO fit to IC). Reused where possible.
# --------------------------------------------------------------------------- #
MIN_NAMES = C.MIN_NAMES                         # 20 — minimum cross-section for a usable rank IC
MIN_APPROVE_PERIODS = F.MIN_APPROVE_PERIODS     # 24 — breadth-and-history approval (NOT IC-based)
QUARTER_GAP_LO_D = G.QUARTER_GAP_LO_D           # 80  — contiguous-quarter spacing window
QUARTER_GAP_HI_D = G.QUARTER_GAP_HI_D           # 100
MIN_TTM_WINDOWS_PER_TICKER = G.MIN_TTM_WINDOWS_PER_TICKER  # 8
YOY_TARGET_D = 365
YOY_TOL_D = 50                                  # tolerance matching a year-ago TTM window
COST_BPS = 10.0

# TTM flow fields built by de-cumulation (core are required; the rest are best-effort).
CORE_TTM_FIELDS = G.CORE_TTM_FIELDS             # revenue, net_income, operating_cash_flow
OPTIONAL_TTM_FIELDS = ("operating_income", "free_cash_flow", "capital_expenditures", "eps_diluted")
ALL_TTM_FIELDS = tuple(CORE_TTM_FIELDS) + OPTIONAL_TTM_FIELDS

# TTM alpha buckets and their preserved price-momentum partner.
MOMENTUM_SUBFACTORS = ("mom_12_1", "mom_6_1", "mom_riskadj")
TTM_BUCKETS = ("momentum", "value_ttm", "quality_ttm", "growth_ttm")

NAN = float("nan")

_f = C._f
_round = C._round


# --------------------------------------------------------------------------- #
# TTM sub-factor catalogue (definitions only; sign-oriented a priori, never flipped).
# --------------------------------------------------------------------------- #
@dataclass
class TtmFactor:
    key: str
    bucket: str
    definition: str
    needs: Tuple[str, ...]          # TTM fields (and "price"/"shares") required
    note: str = ""


TTM_FACTORS: List[TtmFactor] = [
    # ---- value_ttm (TTM flow yields vs market cap; share-free via implied shares) ----
    TtmFactor("ttm_sales_yield", "value_ttm",
              "TTM revenue / market cap (implied diluted shares * price); higher = cheaper",
              ("revenue", "price", "shares")),
    TtmFactor("ttm_earnings_yield", "value_ttm",
              "TTM net income / market cap; higher = cheaper",
              ("net_income", "price", "shares")),
    TtmFactor("ttm_operating_cashflow_yield", "value_ttm",
              "TTM operating cash flow / market cap; higher = cheaper",
              ("operating_cash_flow", "price", "shares")),
    TtmFactor("ttm_fcf_yield", "value_ttm",
              "TTM free cash flow / market cap; higher = cheaper (FCF may be negative)",
              ("free_cash_flow", "price", "shares")),
    # ---- quality_ttm (TTM margins; split-invariant ratios) ----
    TtmFactor("ttm_net_margin", "quality_ttm",
              "TTM net income / TTM revenue; higher = better",
              ("net_income", "revenue")),
    TtmFactor("ttm_operating_margin", "quality_ttm",
              "TTM operating income / TTM revenue; higher = better",
              ("operating_income", "revenue")),
    TtmFactor("ttm_ocf_margin", "quality_ttm",
              "TTM operating cash flow / TTM revenue; higher = better",
              ("operating_cash_flow", "revenue")),
    # ---- growth_ttm (TTM-over-TTM-one-year-ago; split-invariant; positive base guarded) ----
    TtmFactor("ttm_revenue_growth", "growth_ttm",
              "TTM revenue / TTM revenue one year ago - 1; higher = better",
              ("revenue",)),
    TtmFactor("ttm_net_income_growth", "growth_ttm",
              "TTM net income / TTM net income one year ago - 1 (prior > 0 only)",
              ("net_income",)),
    TtmFactor("ttm_ocf_growth", "growth_ttm",
              "TTM operating cash flow / TTM operating cash flow one year ago - 1 (prior > 0 only)",
              ("operating_cash_flow",)),
]

_VALUE_KEYS = [f.key for f in TTM_FACTORS if f.bucket == "value_ttm"]
_QUALITY_KEYS = [f.key for f in TTM_FACTORS if f.bucket == "quality_ttm"]
_GROWTH_KEYS = [f.key for f in TTM_FACTORS if f.bucket == "growth_ttm"]


# --------------------------------------------------------------------------- #
# Output paths.
# --------------------------------------------------------------------------- #
@dataclass
class _OutPaths:
    base: Path

    def __post_init__(self):
        self.base.mkdir(parents=True, exist_ok=True)
        self.report = self.base / "phase7h_ttm_fundamental_signal_retest.json"
        self.panel_coverage = self.base / "ttm_panel_coverage.csv"
        self.factor_catalog = self.base / "ttm_factor_catalog.csv"
        self.factor_scoreboard = self.base / "ttm_factor_scoreboard.csv"
        self.bucket_scoreboard = self.base / "ttm_bucket_scoreboard.csv"
        self.composite_scoreboard = self.base / "ttm_composite_scoreboard.csv"
        self.attribution = self.base / "ttm_attribution_table.csv"
        self.regime_scoreboard = self.base / "ttm_regime_diagnostic_scoreboard.csv"
        self.gate_matrix = self.base / "ttm_signal_gate_matrix.csv"
        self.next_plan = self.base / "phase7i_next_plan.json"


_OUT_DIR_DEFAULT = _REPO_ROOT / "research" / "output" / "phase7h_ttm_fundamental_signal_retest"


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
# Dense TTM panel — reuse the Phase 7-G de-cumulation EXACTLY (G.build_3mo_quarters),
# then roll trailing-4 contiguous quarters into a TTM value series per ticker.
# --------------------------------------------------------------------------- #
def build_ttm_value_series(field: str, universe: set
                           ) -> Dict[str, List[Tuple[pd.Timestamp, pd.Timestamp, float]]]:
    """{ticker: [(window_end, avail, ttm_value)] sorted by window_end}.

    A TTM window is four contiguous 3-month quarters (each ~80-100 days apart). Its
    value is the sum of the four legs; its availability is the MAX availability of the
    four legs (so a window is never usable before its last-arriving leg). The 3-month
    quarters come straight from Phase 7-G's de-cumulation (clean frames used directly;
    Q1 == YTD; Q2/Q3 = YTD differences; Q4 = annual 10-K - Q3 YTD), point-in-time safe."""
    quarters = G.build_3mo_quarters(field, universe)
    by_t: Dict[str, List] = {}
    for q in quarters:
        by_t.setdefault(q.ticker, []).append(q)
    out: Dict[str, List[Tuple[pd.Timestamp, pd.Timestamp, float]]] = {}
    for tk, qs in by_t.items():
        seen: Dict[pd.Timestamp, object] = {}
        for q in qs:                                   # one quarter per quarter_end
            seen.setdefault(q.quarter_end, q)
        ordered = [seen[k] for k in sorted(seen)]
        wins: List[Tuple[pd.Timestamp, pd.Timestamp, float]] = []
        for i in range(3, len(ordered)):
            legs = ordered[i - 3:i + 1]
            ok = all(QUARTER_GAP_LO_D <= (b.quarter_end - a.quarter_end).days <= QUARTER_GAP_HI_D
                     for a, b in zip(legs, legs[1:]))
            if ok:
                wins.append((legs[-1].quarter_end, max(l.avail for l in legs),
                             float(sum(l.value for l in legs))))
        if wins:
            out[tk] = sorted(wins, key=lambda x: x[0])
    return out


def _ttm_asof(wins: List[Tuple[pd.Timestamp, pd.Timestamp, float]],
              cutoff: pd.Timestamp) -> Optional[Tuple[pd.Timestamp, float]]:
    """Latest (window_end, ttm_value) whose availability is strictly before ``cutoff``."""
    avail = sorted((we, val) for we, av, val in wins if av < cutoff)
    return avail[-1] if avail else None


def _ttm_asof_yoy(wins: List[Tuple[pd.Timestamp, pd.Timestamp, float]],
                  cutoff: pd.Timestamp) -> Optional[Tuple[float, float]]:
    """(current TTM, year-ago TTM) both available strictly before ``cutoff``. The
    year-ago window is the one whose end is closest to (current_end - 1yr) within
    tolerance — so growth is TTM-over-TTM, never TTM-over-a-stale-annual."""
    avail = sorted((we, val) for we, av, val in wins if av < cutoff)
    if len(avail) < 2:
        return None
    cur_end, cur_val = avail[-1]
    target = cur_end - pd.Timedelta(days=YOY_TARGET_D)
    cands = [(we, val) for we, val in avail[:-1]
             if abs((we - target).days) <= YOY_TOL_D]
    if not cands:
        return None
    _, prev_val = min(cands, key=lambda x: abs((x[0] - target).days))
    return cur_val, prev_val


def build_ttm_panels(universe: set) -> Dict[str, Dict]:
    """TTM value series for every required field + per-field readiness stats."""
    panels: Dict[str, Dict] = {}
    for fld in ALL_TTM_FIELDS:
        series = build_ttm_value_series(fld, universe)
        n_windows = sum(len(v) for v in series.values())
        continuity_ready = sum(1 for v in series.values() if len(v) >= MIN_TTM_WINDOWS_PER_TICKER)
        panels[fld] = {
            "series": series,
            "tickers_with_ttm": len(series),
            "total_ttm_windows": int(n_windows),
            "continuity_ready_tickers": continuity_ready,
        }
    return panels


# --------------------------------------------------------------------------- #
# TTM factor construction (point-in-time, leakage-safe). Mirrors the 7-F fundamental
# loop but on TTM levels rather than annual levels. Share basis = 7-F implied diluted
# shares (annual NI/EPS), so value yields stay share-free on the SAME basis as 7-F.
# --------------------------------------------------------------------------- #
def build_ttm_factors(panels: Dict[str, Dict], annual_u: pd.DataFrame,
                      monthly_close: pd.DataFrame, month_end_date: pd.Series,
                      sector_map: Dict[str, str]) -> Tuple[Dict[str, pd.DataFrame], Dict[str, pd.DataFrame]]:
    """Return (raw long frames, z-scored long frames) for every TTM factor."""
    ni_a = C._field_obs(annual_u, "net_income")
    eps_a = C._field_obs(annual_u, "eps_diluted")
    shares = F._shares_obs(ni_a, eps_a)

    ser = {fld: panels[fld]["series"] for fld in ALL_TTM_FIELDS}
    rows: Dict[str, list] = {f.key: [] for f in TTM_FACTORS}

    months = list(monthly_close.index)
    tickers = list(monthly_close.columns)
    for m in months:
        cutoff = month_end_date.get(m)
        if cutoff is None:
            continue
        for t in tickers:
            px = _f(monthly_close.at[m, t]) if t in monthly_close.columns else None
            sh = C._asof(shares.get(t, []), cutoff)
            mktcap = (sh[1] * px) if (sh is not None and px is not None and px > 0) else None

            def lvl(fld):
                a = _ttm_asof(ser[fld].get(t, []), cutoff)
                return a[1] if a is not None else None

            rev = lvl("revenue")
            ni = lvl("net_income")
            ocf = lvl("operating_cash_flow")
            opinc = lvl("operating_income")
            fcf = lvl("free_cash_flow")

            # ---- value_ttm (flow yields vs market cap) ----
            if mktcap is not None and mktcap > 0:
                if rev is not None:
                    rows["ttm_sales_yield"].append((m, t, rev / mktcap))
                if ni is not None:
                    rows["ttm_earnings_yield"].append((m, t, ni / mktcap))
                if ocf is not None:
                    rows["ttm_operating_cashflow_yield"].append((m, t, ocf / mktcap))
                if fcf is not None:
                    rows["ttm_fcf_yield"].append((m, t, fcf / mktcap))

            # ---- quality_ttm (margins) ----
            if rev is not None and rev > 0:
                if ni is not None:
                    rows["ttm_net_margin"].append((m, t, ni / rev))
                if opinc is not None:
                    rows["ttm_operating_margin"].append((m, t, opinc / rev))
                if ocf is not None:
                    rows["ttm_ocf_margin"].append((m, t, ocf / rev))

            # ---- growth_ttm (TTM over year-ago TTM; positive base guarded) ----
            g = _ttm_asof_yoy(ser["revenue"].get(t, []), cutoff)
            if g is not None and g[1] > 0:
                rows["ttm_revenue_growth"].append((m, t, g[0] / g[1] - 1.0))
            g = _ttm_asof_yoy(ser["net_income"].get(t, []), cutoff)
            if g is not None and g[1] > 0:
                rows["ttm_net_income_growth"].append((m, t, g[0] / g[1] - 1.0))
            g = _ttm_asof_yoy(ser["operating_cash_flow"].get(t, []), cutoff)
            if g is not None and g[1] > 0:
                rows["ttm_ocf_growth"].append((m, t, g[0] / g[1] - 1.0))

    raw = {k: pd.DataFrame(v, columns=["month", "ticker", "raw"]) for k, v in rows.items()}
    z = {k: C.normalize_cross_sectional(v, sector_map, sector_neutralize=True) for k, v in raw.items()}
    return raw, z


# --------------------------------------------------------------------------- #
# Grading helpers.
# --------------------------------------------------------------------------- #
def _grade_or_none(z: pd.DataFrame, realized: pd.DataFrame) -> Optional[Dict]:
    return C.grade_series(z, realized) if (z is not None and not z.empty) else None


def _ic(graded: Optional[Dict]) -> Optional[float]:
    return (graded or {}).get("mean_rank_ic")


def _t(graded: Optional[Dict]) -> Optional[float]:
    return (graded or {}).get("ic_t_stat")


# --------------------------------------------------------------------------- #
# Phase 7-F upgraded composite baseline — recomputed live by REUSING 7-F's own
# construction functions on the same universe / harness, so the comparison is exact.
# --------------------------------------------------------------------------- #
def compute_phase7f_baseline(universe: List[str], annual_u: pd.DataFrame,
                             pf: Dict[str, object], realized: pd.DataFrame,
                             sector_map: Dict[str, str]) -> Tuple[Optional[Dict], Dict]:
    uset = set(universe)
    monthly_close = pf["monthly_close"]
    month_end_date = pf["month_end_date"]

    balance = F.load_balance_pit(universe=uset)
    ttm_ni_qtr = F.load_clean_quarterly_obs(field="net_income", universe=uset)
    price_raw = F.build_upgraded_price_factors(pf)
    fund_raw = F.build_upgraded_fundamental_factors(annual_u, balance, monthly_close,
                                                    month_end_date, ttm_ni_qtr=ttm_ni_qtr)
    raw_all = {**price_raw, **fund_raw}
    sub_z = {k: C.normalize_cross_sectional(v, sector_map, sector_neutralize=True)
             for k, v in raw_all.items()}

    catalog = list(F.SUBFACTORS) + [F.TTM_SUBFACTOR]
    approved = {}
    for sf in catalog:
        z = sub_z.get(sf.key, pd.DataFrame(columns=["month", "ticker", "z"]))
        graded = _grade_or_none(z, realized)
        n_per = (graded or {}).get("n_periods") or 0
        approved[sf.key] = bool(not z.empty and n_per >= MIN_APPROVE_PERIODS)

    bucket_z = {}
    for b in F.ALPHA_BUCKETS:
        members = [sf.key for sf in F.SUBFACTORS
                   if sf.bucket == b and sf.role == "alpha" and approved[sf.key]]
        if b == "value" and approved.get(F.TTM_SUBFACTOR.key):
            members.append(F.TTM_SUBFACTOR.key)
        bucket_z[b] = F.combine_equal_weight(sub_z, members)
    approved_buckets = [b for b in F.ALPHA_BUCKETS if not bucket_z[b].empty]
    composite = F.combine_equal_weight(bucket_z, approved_buckets)
    graded = _grade_or_none(composite, realized)
    meta = {"approved_buckets": approved_buckets,
            "recomputed_ic": _round(_ic(graded)), "recomputed_t": _round(_t(graded)),
            "published_ic": PHASE7F_BASELINE_IC, "published_t": PHASE7F_BASELINE_T}
    return graded, meta


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
def _run(paths: _OutPaths) -> Dict:
    # Same universe / prices / labels as Phase 7-F (7-G confirmed no broader local set).
    universe, prices, annual, sector_map, sector_pit = F._build_universe_and_prices()
    annual_u = annual[annual["ticker"].isin(universe)].copy()
    uset = set(universe)

    pf = C.build_price_factors(prices)
    realized = pf["forward"]
    monthly_close = pf["monthly_close"]
    month_end_date = pf["month_end_date"]

    # ---- Phase 7-F upgraded composite baseline (the bar to beat). ----
    base_graded, base_meta = compute_phase7f_baseline(universe, annual_u, pf, realized, sector_map)
    baseline_ic = _ic(base_graded)
    baseline_t = _t(base_graded)

    # ---- Dense TTM panel (reuse 7-G de-cumulation) + TTM factors. ----
    panels = build_ttm_panels(uset)
    ttm_raw, ttm_z = build_ttm_factors(panels, annual_u, monthly_close, month_end_date, sector_map)

    # ---- Preserved Phase 7-F price-momentum factors + low-vol risk descriptor. ----
    price_raw = F.build_upgraded_price_factors(pf)
    price_z = {k: C.normalize_cross_sectional(price_raw[k], sector_map, sector_neutralize=True)
               for k in ("mom_12_1", "mom_6_1", "mom_riskadj", "low_volatility")}

    all_z = {**ttm_z, **{k: price_z[k] for k in MOMENTUM_SUBFACTORS}}
    all_z["low_volatility"] = price_z["low_volatility"]   # graded standalone, NOT in composite

    # ---- Grade every sub-factor; approval is breadth-and-history only (never IC). ----
    factor_results: Dict[str, Dict] = {}
    catalog_keys = [f.key for f in TTM_FACTORS] + list(MOMENTUM_SUBFACTORS) + ["low_volatility"]
    for key in catalog_keys:
        z = all_z.get(key, pd.DataFrame(columns=["month", "ticker", "z"]))
        graded = _grade_or_none(z, realized)
        cov = C.coverage_fraction(z, realized)
        n_per = (graded or {}).get("n_periods") or 0
        approved = bool(not z.empty and n_per >= MIN_APPROVE_PERIODS)
        factor_results[key] = {"graded": graded, "coverage": cov, "approved": approved}

    # ---- Equal-weight buckets (approved alpha sub-factors only; low-vol excluded). ----
    bucket_members = {
        "momentum": [k for k in MOMENTUM_SUBFACTORS if factor_results[k]["approved"]],
        "value_ttm": [k for k in _VALUE_KEYS if factor_results[k]["approved"]],
        "quality_ttm": [k for k in _QUALITY_KEYS if factor_results[k]["approved"]],
        "growth_ttm": [k for k in _GROWTH_KEYS if factor_results[k]["approved"]],
    }
    bucket_z = {b: F.combine_equal_weight(all_z, bucket_members[b]) for b in TTM_BUCKETS}
    bucket_graded = {b: _grade_or_none(bucket_z[b], realized) for b in TTM_BUCKETS}
    approved_buckets = [b for b in TTM_BUCKETS if not bucket_z[b].empty]

    # ---- Final composite = equal weight across approved buckets only. ----
    composite = F.combine_equal_weight(bucket_z, approved_buckets)
    comp_graded = _grade_or_none(composite, realized)
    ttm_ic = _ic(comp_graded)
    ttm_t = _t(comp_graded)
    incremental_ic = (ttm_ic - baseline_ic) if (ttm_ic is not None and baseline_ic is not None) else None
    t_change = (ttm_t - baseline_t) if (ttm_t is not None and baseline_t is not None) else None

    # ---- Leakage / placebo on the final composite. ----
    strictly_forward = C._forward_label_check(composite, realized)
    placebo = C.placebo_test(comp_graded["_sbp"], comp_graded["_rbp"]) if comp_graded else {"placebo_mean_ic": None}
    placebo_collapsed = C._placebo_collapsed(placebo)

    # ---- Portfolio diagnostics + holdout on the final composite. ----
    port = C.spread_series_from_frame(composite, realized, q=5) if not composite.empty \
        else {"spreads": [], "turnovers": []}
    cost = H.apply_transaction_costs(port["spreads"][1:], port["turnovers"], cost_bps=COST_BPS) \
        if len(port["turnovers"]) else {"gross_mean": None, "net_mean": None, "mean_turnover": None}
    comp_sharpe = H.sharpe(port["spreads"], periods_per_year=12) if port["spreads"] else None
    comp_mdd = H.max_drawdown(port["spreads"]) if port["spreads"] else None
    holdout = C._holdout_split(base_graded, comp_graded)

    # ---- Regime diagnostics (reuse 7-F helper; descriptive ONLY, never selection). ----
    regime = F._regime_diagnostics(comp_graded, base_graded, bucket_graded, realized)

    # ---- TTM coverage sufficiency (reuse 7-G's continuity-ready notion). ----
    core_ready = {fld: panels[fld]["continuity_ready_tickers"] for fld in CORE_TTM_FIELDS}
    ttm_coverage_sufficient = (all(core_ready[f] >= MIN_NAMES for f in CORE_TTM_FIELDS)
                               and len(approved_buckets) >= 2 and ttm_ic is not None)

    # ---- Attribution (item 11). ----
    attribution = _build_attribution(base_graded, bucket_graded, comp_graded, baseline_ic)

    # ---- Gates / recommendation. ----
    baseline_consistent = (baseline_ic is not None
                           and abs(baseline_ic - PHASE7F_BASELINE_IC) <= BASELINE_RECOMPUTE_TOL)
    gates, gate_counts = _build_gate_matrix(
        strictly_forward, placebo_collapsed, comp_graded, approved_buckets, ttm_ic, ttm_t,
        incremental_ic, t_change, ttm_coverage_sufficient, core_ready, baseline_consistent, sector_pit)
    recommendation = _derive_recommendation(
        gates, approved_buckets, ttm_ic, ttm_t, incremental_ic, t_change,
        ttm_coverage_sufficient, strictly_forward, placebo_collapsed)
    improved_yes_no = "YES" if recommendation == REC_IMPROVED else "NO"

    # ---- Artifacts. ----
    _write_panel_coverage(paths, panels, realized, ttm_z)
    _write_factor_catalog(paths, factor_results)
    _write_factor_scoreboard(paths, factor_results)
    _write_bucket_scoreboard(paths, bucket_graded, bucket_members, baseline_ic)
    _write_composite_scoreboard(paths, base_meta, base_graded, comp_graded, incremental_ic,
                                t_change, comp_sharpe, comp_mdd, cost, holdout)
    _write_attribution(paths, attribution)
    _write_regime_scoreboard(paths, regime)
    _write_gate_matrix_csv(paths, gates)
    _write_next_plan(paths, recommendation, improved_yes_no, approved_buckets, core_ready)

    return _assemble_report(
        paths, universe, prices, sector_pit, base_meta, baseline_ic, baseline_t,
        factor_results, bucket_members, bucket_graded, approved_buckets, comp_graded,
        ttm_ic, ttm_t, incremental_ic, t_change, attribution, panels, core_ready,
        ttm_coverage_sufficient, baseline_consistent, strictly_forward, placebo,
        placebo_collapsed, comp_sharpe, comp_mdd, cost, holdout, regime, gates,
        gate_counts, recommendation, improved_yes_no)


# --------------------------------------------------------------------------- #
# Attribution table (item 11).
# --------------------------------------------------------------------------- #
def _build_attribution(base_graded, bucket_graded, comp_graded, baseline_ic) -> List[Dict]:
    def row(name, graded):
        ic = _ic(graded)
        return {"series": name, "mean_rank_ic": _round(ic), "ic_t_stat": _round(_t(graded)),
                "n_periods": (graded or {}).get("n_periods") or 0,
                "incremental_vs_7f": _round((ic - baseline_ic)
                                            if (ic is not None and baseline_ic is not None) else None)}
    return [
        row("phase7f_baseline_composite", base_graded),
        row("phase7h_momentum_only", bucket_graded.get("momentum")),
        row("phase7h_value_ttm_bucket", bucket_graded.get("value_ttm")),
        row("phase7h_quality_ttm_bucket", bucket_graded.get("quality_ttm")),
        row("phase7h_growth_ttm_bucket", bucket_graded.get("growth_ttm")),
        row("phase7h_final_composite", comp_graded),
    ]


# --------------------------------------------------------------------------- #
# Gates / recommendation.
# --------------------------------------------------------------------------- #
def _build_gate_matrix(strictly_forward, placebo_collapsed, comp_graded, approved_buckets,
                       ttm_ic, ttm_t, incremental_ic, t_change, ttm_coverage_sufficient,
                       core_ready, baseline_consistent, sector_pit) -> Tuple[List[Dict], Dict]:
    gates: List[Dict] = []

    def add(name, status, metric, value, threshold, note=""):
        gates.append({"gate_name": name, "status": status, "metric": metric,
                      "value": value, "threshold": threshold, "note": note})

    add("no_lookahead_gate", "PASS" if strictly_forward.get("strictly_forward") else "FAIL",
        "forward_label_violations", strictly_forward.get("n_violations"), "0",
        "every label resolves strictly after its scoring month; TTM availability = max of all legs")
    add("placebo_collapses_gate", "PASS" if placebo_collapsed else "FAIL",
        "within_period_label_shuffle_ic_collapses", placebo_collapsed, "true",
        "shuffled-label composite IC must collapse toward zero")
    add("composite_built_gate", "PASS" if ttm_ic is not None else "FAIL",
        "ttm_composite_mean_rank_ic_computed", ttm_ic is not None, "true",
        f"{len(approved_buckets)} approved buckets equal-weighted: {approved_buckets}")
    add("min_buckets_gate", "PASS" if len(approved_buckets) >= 2 else "FAIL",
        "approved_bucket_count", len(approved_buckets), ">= 2",
        "need at least two approved alpha buckets for a multi-factor composite")
    add("ttm_coverage_sufficient_gate", "PASS" if ttm_coverage_sufficient else "FAIL",
        "core_ttm_continuity_ready_vs_min_names",
        ", ".join(f"{k}={v}" for k, v in core_ready.items()), f">= {MIN_NAMES} each",
        "dense TTM panel must clear continuity-readiness on each core flow field")
    add("improvement_gate",
        "PASS" if (incremental_ic is not None and incremental_ic >= IMPROVEMENT_GATE) else "FAIL",
        "ttm_composite_minus_7f_baseline_ic", _round(incremental_ic), f">= {IMPROVEMENT_GATE}",
        "final composite must beat the Phase 7-F upgraded composite by the success gate")
    add("absolute_ic_gate",
        "PASS" if (ttm_ic is not None and ttm_ic >= ABSOLUTE_IC_GATE) else "FAIL",
        "ttm_composite_mean_rank_ic", _round(ttm_ic), f">= {ABSOLUTE_IC_GATE}",
        "task-specified absolute IC bar (0.0177 baseline + 0.005 gate)")
    add("t_stat_improves_gate",
        "PASS" if (t_change is not None and t_change > 0) else "FAIL",
        "ttm_minus_7f_ic_t_stat", _round(t_change), "> 0",
        "final composite IC t-stat must improve versus Phase 7-F")
    add("nonnegative_ic_gate",
        "PASS" if (ttm_ic is not None and ttm_ic >= 0) else "FAIL",
        "ttm_composite_mean_rank_ic", _round(ttm_ic), ">= 0", "composite must be non-negative")
    add("ic_significance_gate",
        "PASS" if (comp_graded and comp_graded.get("ic_t_stat") is not None
                   and abs(comp_graded["ic_t_stat"]) >= 2.0) else "WARNING",
        "ttm_composite_ic_t_stat", _round(ttm_t), "|t| >= 2", "informational: IC significance")
    add("baseline_recompute_consistent_gate", "PASS" if baseline_consistent else "WARNING",
        "recomputed_7f_baseline_near_published", baseline_consistent,
        f"|recomputed - {PHASE7F_BASELINE_IC}| <= {BASELINE_RECOMPUTE_TOL}",
        "live-recomputed 7-F baseline should match the published +0.0177")
    add("low_vol_excluded_from_alpha_gate", "PASS", "low_volatility_role", "risk_descriptor",
        "risk_descriptor", "low-volatility graded standalone but excluded from the alpha composite")
    add("no_sign_flipping_gate", "PASS", "factor_signs_flipped_to_chase_ic", False, "false",
        "all factor signs fixed a priori; none flipped to mechanically improve IC")
    add("no_weight_optimization_gate", "PASS", "factor_weights_optimized", False, "false",
        "every blend is equal weight; no weights tuned")
    add("regimes_diagnostic_only_gate", "PASS", "regimes_used_to_select_or_weight", False, "false",
        "Phase 7-E regimes reported for diagnostics only; never used to select/weight factors")
    add("sector_neutralization_caveat_gate", "WARNING" if not sector_pit else "PASS",
        "sector_map_point_in_time", sector_pit, "true (ideal)",
        "static current-as-of sector map (7-G: no PIT sector history locally)")

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
        ("no_universe_broadening_gate", "universe_broadened", False),
        ("no_data_collection_gate", "data_collected", False),
        ("no_regime_throttle_gate", "regime_throttle_activated", False),
        ("no_future_fundamentals_gate", "future_fundamentals_used", False),
        ("no_paper_trader_gate", "paper_trader_touched", False),
        ("no_gcp_gate", "gcp_touched", False),
        ("no_d_drive_write_gate", "d_drive_written", False),
        ("preview_only_gate", "preview_only", True),
    ):
        add(nm, "PASS", mv, want, str(want).lower(), "")

    counts = {"PASS": 0, "FAIL": 0, "WARNING": 0}
    for g in gates:
        counts[g["status"]] = counts.get(g["status"], 0) + 1
    return gates, counts


def _is_safety_gate(name: str) -> bool:
    return name.startswith(("no_", "preview_")) and name not in (
        "no_lookahead_gate", "no_sign_flipping_gate", "no_weight_optimization_gate")


def _derive_recommendation(gates, approved_buckets, ttm_ic, ttm_t, incremental_ic, t_change,
                           ttm_coverage_sufficient, strictly_forward, placebo_collapsed) -> str:
    safety_fail = any(g["status"] == "FAIL" and _is_safety_gate(g["gate_name"]) for g in gates)
    if safety_fail or not strictly_forward.get("strictly_forward") or not placebo_collapsed:
        return REC_NEEDS_REVIEW
    if not ttm_coverage_sufficient or len(approved_buckets) < 2 or ttm_ic is None:
        # Coverage/build inadequacy on this constrained universe -> broader collection.
        return REC_NEEDS_UNIVERSE
    improved = (ttm_ic >= ABSOLUTE_IC_GATE
                and incremental_ic is not None and incremental_ic >= IMPROVEMENT_GATE
                and t_change is not None and t_change > 0)
    if improved:
        return REC_IMPROVED
    if ttm_ic > 0:
        # Built cleanly, positive, but did not clear the bar -> weak (no rounding up).
        return REC_WEAK
    # Non-positive on the constrained current-constituent universe: the binding limit is
    # universe breadth / survivorship (7-G's finding), not further local fundamental work.
    return REC_NEEDS_UNIVERSE


# --------------------------------------------------------------------------- #
# Artifact writers.
# --------------------------------------------------------------------------- #
def _write_panel_coverage(paths, panels, realized, ttm_z) -> None:
    rows = []
    for fld in ALL_TTM_FIELDS:
        p = panels[fld]
        # label-cell coverage of the matching value/quality factor if one maps to this field
        rows.append([fld, p["tickers_with_ttm"], p["total_ttm_windows"],
                     p["continuity_ready_tickers"], MIN_TTM_WINDOWS_PER_TICKER,
                     str(p["continuity_ready_tickers"] >= MIN_NAMES).lower(),
                     "core" if fld in CORE_TTM_FIELDS else "optional"])
    _write_csv(paths.panel_coverage,
               ["ttm_field", "tickers_with_ttm", "total_ttm_windows", "continuity_ready_tickers",
                "min_ttm_windows_per_ticker", "meets_min_names", "field_class"], rows)


def _write_factor_catalog(paths, factor_results) -> None:
    rows = []
    for f in TTM_FACTORS:
        r = factor_results[f.key]
        rows.append([f.bucket, f.key, "fundamentals_ttm", "alpha", f.definition,
                     str(r["graded"] is not None).lower(), str(r["approved"]).lower(),
                     _round(r["coverage"])])
    for k in MOMENTUM_SUBFACTORS:
        r = factor_results[k]
        rows.append(["momentum", k, "price", "alpha", "preserved Phase 7-F price-momentum spec",
                     str(r["graded"] is not None).lower(), str(r["approved"]).lower(),
                     _round(r["coverage"])])
    r = factor_results["low_volatility"]
    rows.append(["low_volatility", "low_volatility", "price", "risk_descriptor",
                 "negative trailing realized vol; reported only, excluded from alpha composite",
                 str(r["graded"] is not None).lower(), "false", _round(r["coverage"])])
    _write_csv(paths.factor_catalog,
               ["bucket", "factor_key", "source", "role", "definition", "available",
                "approved", "coverage_fraction"], rows)


def _write_factor_scoreboard(paths, factor_results) -> None:
    rows = []
    for key in [f.key for f in TTM_FACTORS] + list(MOMENTUM_SUBFACTORS) + ["low_volatility"]:
        r = factor_results[key]
        g = r["graded"] or {}
        rows.append([key, _round(g.get("mean_rank_ic")), _round(g.get("ic_t_stat")),
                     g.get("n_periods") or 0, _round(g.get("quintile_spread")),
                     _round(g.get("decile_spread")), _round(r["coverage"]),
                     str(r["approved"]).lower()])
    _write_csv(paths.factor_scoreboard,
               ["factor_key", "mean_rank_ic", "ic_t_stat", "n_periods", "quintile_spread",
                "decile_spread", "coverage_fraction", "approved"], rows)


def _write_bucket_scoreboard(paths, bucket_graded, bucket_members, baseline_ic) -> None:
    rows = []
    for b in TTM_BUCKETS:
        g = bucket_graded.get(b) or {}
        ic = g.get("mean_rank_ic")
        rows.append([b, ";".join(bucket_members[b]), _round(ic), _round(g.get("ic_t_stat")),
                     g.get("n_periods") or 0, _round(g.get("quintile_spread")),
                     _round((ic - baseline_ic) if (ic is not None and baseline_ic is not None) else None)])
    _write_csv(paths.bucket_scoreboard,
               ["bucket", "members", "mean_rank_ic", "ic_t_stat", "n_periods",
                "quintile_spread", "incremental_vs_7f"], rows)


def _write_composite_scoreboard(paths, base_meta, base_graded, comp_graded, incremental_ic,
                                t_change, comp_sharpe, comp_mdd, cost, holdout) -> None:
    def row(name, g):
        return [name, _round((g or {}).get("mean_rank_ic")), _round((g or {}).get("ic_t_stat")),
                (g or {}).get("n_periods"), _round((g or {}).get("quintile_spread")),
                _round((g or {}).get("decile_spread"))]
    rows = [
        row("phase7f_upgraded_composite_baseline (recomputed)", base_graded),
        ["phase7f_baseline_published", PHASE7F_BASELINE_IC, PHASE7F_BASELINE_T, "", "", ""],
        row("phase7h_ttm_final_composite", comp_graded),
        ["incremental_ic (7h - 7f)", _round(incremental_ic), "", "", "", ""],
        ["ic_t_stat_change (7h - 7f)", _round(t_change), "", "", "", ""],
        ["  ttm_composite_sharpe", _round(comp_sharpe), "", "", "", ""],
        ["  ttm_composite_max_drawdown", _round(comp_mdd), "", "", "", ""],
        ["  ttm_net_mean_spread", _round(cost.get("net_mean")), "", "", "", ""],
        ["  holdout_incremental_ic", _round(holdout.get("incremental_holdout_ic")), "", "", "", ""],
    ]
    _write_csv(paths.composite_scoreboard,
               ["series", "mean_rank_ic", "ic_t_stat", "n_periods", "quintile_spread", "decile_spread"], rows)


def _write_attribution(paths, attribution) -> None:
    _write_csv(paths.attribution,
               ["series", "mean_rank_ic", "ic_t_stat", "n_periods", "incremental_vs_7f"],
               [[a["series"], a["mean_rank_ic"], a["ic_t_stat"], a["n_periods"],
                 a["incremental_vs_7f"]] for a in attribution])


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


def _write_next_plan(paths, recommendation, improved_yes_no, approved_buckets, core_ready) -> None:
    if recommendation == REC_IMPROVED:
        next_phase = {"phase": "7-I", "title": "Confirm TTM signal robustness & begin measurement-only book construction",
                      "rationale": "Dense TTM fundamentals improved the signal; next, stress its robustness "
                                   "(yearly stability, holdout, regimes) before any sizing — still no orders."}
    else:
        next_phase = {"phase": "7-I", "title": "Controlled broader-universe + survivorship-aware collection (gated)",
                      "rationale": "Local fundamental density is now exhausted; the binding limit is the "
                                   "~128-name current-constituent universe and survivorship bias (Phase 7-G). "
                                   "The next real gain requires a controlled, explicitly-approved collection of "
                                   "survivorship-aware historical membership + delisted-name prices + point-in-time "
                                   "sectors — NOT more local polishing."}
    plan = {
        "from_phase": PHASE,
        "recommendation": recommendation,
        "did_dense_ttm_improve_signal_quality": improved_yes_no,
        "approved_buckets": approved_buckets,
        "core_ttm_continuity_ready": core_ready,
        "recommended_next_phase": next_phase,
        "hard_constraints": [
            "no live or paid data calls", "no universe broadening or data collection in-phase",
            "no factor-weight optimization", "no factor-sign flipping", "regimes diagnostic only",
            "no regime-throttle activation", "no trading / order / broker / automation",
            "no Paper Trader / GCP / deploy", "do not commit", "do not push",
        ],
    }
    _write_json(paths.next_plan, plan)


# --------------------------------------------------------------------------- #
# Report assembly.
# --------------------------------------------------------------------------- #
def _safety_flags() -> Dict:
    return {
        "preview_only": True, "network_used": False, "live_data_used": False,
        "paid_apis_used": False, "api_key_required": False,
        "orders_enabled": False, "broker_execution_enabled": False, "automation_enabled": False,
        "deployed": False, "production_model_built": False,
        "factor_weights_optimized": False, "factor_signs_flipped": False,
        "universe_broadened": False, "data_collected": False,
        "regime_throttle_activated": False, "regimes_used_to_select_or_weight": False,
        "future_fundamentals_used": False, "binary_artifacts_created": False,
        "paper_trader_touched": False, "gcp_touched": False,
        "raw_files_modified": False, "d_drive_written": False,
    }


def _assemble_report(paths, universe, prices, sector_pit, base_meta, baseline_ic, baseline_t,
                     factor_results, bucket_members, bucket_graded, approved_buckets, comp_graded,
                     ttm_ic, ttm_t, incremental_ic, t_change, attribution, panels, core_ready,
                     ttm_coverage_sufficient, baseline_consistent, strictly_forward, placebo,
                     placebo_collapsed, comp_sharpe, comp_mdd, cost, holdout, regime, gates,
                     gate_counts, recommendation, improved_yes_no) -> Dict:
    pdates = prices["date"]

    def fac(key):
        r = factor_results[key]
        g = r["graded"] or {}
        return {"available": r["graded"] is not None, "approved": r["approved"],
                "coverage": _round(r["coverage"]), "mean_rank_ic": _round(g.get("mean_rank_ic")),
                "ic_t_stat": _round(g.get("ic_t_stat")), "n_periods": g.get("n_periods") or 0}

    return {
        "phase": PHASE,
        "objective": OBJECTIVE,
        "generated_by": "research\\run_phase7h_ttm_fundamental_signal_retest.py",
        "did_dense_ttm_improve_signal_quality": improved_yes_no,
        "recommendation": recommendation,
        "recommendation_allowed_values": list(ALLOWED_RECOMMENDATIONS),
        "charter": {
            "path": "docs/project_charter_sp500_multifactor_ranking_v1.md",
            "mapping": "Charter — strengthen System 1 signal reliability before sizing / regime activation.",
            "principle": "Equal weight. No optimization, no sign flipping, regimes diagnostic only.",
        },
        "measured_by": "research/run_phase7b_validation_harness_foundation.py (unmodified Phase 7-B harness)",
        "reuses": "7-C (loaders/normalize/grade/composite/placebo/holdout), 7-F (universe, "
                  "price-momentum, implied shares, baseline composite, regime diagnostics), "
                  "7-G (YTD de-cumulation -> point-in-time 3-month quarters).",
        **_safety_flags(),
        "is_predictive": False,
        "universe": {"n_tickers": len(universe),
                     "date_range": f"{pdates.min().date()}..{pdates.max().date()}",
                     "cadence": "monthly (month-end scoring, next-month forward label)",
                     "same_universe_as_phase7f": True,
                     "scope": "fundamental_density_only (Phase 7-G: no broader local universe, no PIT sectors)",
                     "tickers_sample": universe[:10]},
        "sector_neutralization": {"applied": True, "map_point_in_time": sector_pit,
                                  "caveat": "static current-as-of sector map (membership stable, not strictly PIT)"},
        # The headline comparison.
        "phase7f_baseline": {"published_ic": PHASE7F_BASELINE_IC, "published_t": PHASE7F_BASELINE_T,
                             "recomputed_ic": base_meta["recomputed_ic"], "recomputed_t": base_meta["recomputed_t"],
                             "recompute_consistent_with_published": baseline_consistent,
                             "recomputed_approved_buckets": base_meta["approved_buckets"]},
        "phase7h_final_composite": {"mean_rank_ic": _round(ttm_ic), "ic_t_stat": _round(ttm_t),
                                    "n_periods": (comp_graded or {}).get("n_periods"),
                                    "quintile_spread": _round((comp_graded or {}).get("quintile_spread")),
                                    "decile_spread": _round((comp_graded or {}).get("decile_spread")),
                                    "yearly_ic": {y: _round(v) for y, v in
                                                  ((comp_graded or {}).get("yearly_ic") or {}).items()}},
        "incremental_ic_vs_7f": _round(incremental_ic),
        "ic_t_stat_change_vs_7f": _round(t_change),
        "improvement_gate": IMPROVEMENT_GATE,
        "absolute_ic_gate": ABSOLUTE_IC_GATE,
        "signal_quality_improved": recommendation == REC_IMPROVED,
        # TTM panel coverage.
        "ttm_panel_coverage": {fld: {"tickers_with_ttm": panels[fld]["tickers_with_ttm"],
                                     "total_ttm_windows": panels[fld]["total_ttm_windows"],
                                     "continuity_ready_tickers": panels[fld]["continuity_ready_tickers"]}
                               for fld in ALL_TTM_FIELDS},
        "core_ttm_continuity_ready": core_ready,
        "ttm_coverage_sufficient": ttm_coverage_sufficient,
        "min_names": MIN_NAMES,
        # Factors / buckets.
        "ttm_factors": {f.key: fac(f.key) for f in TTM_FACTORS},
        "momentum_factors": {k: fac(k) for k in MOMENTUM_SUBFACTORS},
        "low_volatility_treatment": {
            "role": "risk_descriptor",
            "standalone_mean_rank_ic": _round((factor_results["low_volatility"]["graded"] or {}).get("mean_rank_ic")),
            "note": "graded standalone for evidence; excluded from the alpha composite a priori."},
        "bucket_members": bucket_members,
        "approved_buckets": approved_buckets,
        "bucket_ic": {b: _round(_ic(bucket_graded.get(b))) for b in TTM_BUCKETS},
        "attribution_table": attribution,
        # Validation.
        "leakage_checks": {"strictly_forward_labels": strictly_forward,
                           "placebo_n_shuffles": placebo.get("n_shuffles"),
                           "placebo_mean_rank_ic": _round(placebo.get("placebo_mean_ic")),
                           "placebo_collapsed": placebo_collapsed},
        "cost_model": {"cost_bps": COST_BPS, "net_mean_spread": _round(cost.get("net_mean")),
                       "mean_turnover": _round(cost.get("mean_turnover"))},
        "ttm_composite_sharpe": _round(comp_sharpe),
        "ttm_composite_max_drawdown": _round(comp_mdd),
        "holdout": holdout,
        "regime_diagnostics": {"available": regime.get("available", False),
                               "axes": regime.get("axes", []),
                               "asof_violations": regime.get("asof_violations"),
                               "full_sample_ic": {k: _round(v) for k, v in regime.get("full_sample_ic", {}).items()},
                               "note": "IC decomposed by Phase 7-E regimes for diagnostics ONLY; "
                                       "regimes are never used to select, weight, or throttle factors."},
        "gate_summary": {"counts": gate_counts, "gates": gates},
        "what_is_still_caveated": [
            "Same constrained ~128-name current-constituent universe; survivorship / "
            "current-constituent bias is unchanged (Phase 7-G: not locally fixable).",
            "Sector neutralization still uses a static current-as-of map (no PIT sector history).",
            "Value yields use Phase 7-F implied diluted shares (annual NI/EPS) — a residual "
            "adjusted-price vs as-reported-share split-basis caveat remains.",
            "TTM growth requires a year-ago TTM window within tolerance, so growth coverage is "
            "thinner than levels; reported per field in ttm_panel_coverage.csv.",
            "This is a fundamental-density retest, NOT a true robustness retest of the dominant "
            "universe/survivorship weakness.",
        ],
        "commit_appropriate": False,
        "commit_note": "Per task instruction nothing is committed or pushed.",
        "artifacts": [_rel(paths.report), _rel(paths.panel_coverage), _rel(paths.factor_catalog),
                      _rel(paths.factor_scoreboard), _rel(paths.bucket_scoreboard),
                      _rel(paths.composite_scoreboard), _rel(paths.attribution),
                      _rel(paths.regime_scoreboard), _rel(paths.gate_matrix), _rel(paths.next_plan)],
    }


def _error_report(msg: str, tb: str) -> Dict:
    return {"phase": PHASE, "recommendation": REC_ERROR, "error": msg, "traceback": tb,
            "did_dense_ttm_improve_signal_quality": "NO", **_safety_flags(),
            "is_predictive": False, "recommendation_allowed_values": list(ALLOWED_RECOMMENDATIONS)}


def run(out_dir: Optional[str] = None) -> Dict:
    paths = _OutPaths(Path(out_dir) if out_dir else _OUT_DIR_DEFAULT)
    try:
        report = _run(paths)
    except Exception as exc:  # pragma: no cover - defensive
        import traceback
        report = _error_report(str(exc), traceback.format_exc())
        _write_json(paths.report, report)
        return report
    _write_json(paths.report, report)
    return report


# --------------------------------------------------------------------------- #
# CLI.
# --------------------------------------------------------------------------- #
def _print_summary(report: Dict) -> None:
    print(f"[{PHASE}] recommendation = {report.get('recommendation')}")
    if report.get("error"):
        print(f"      ERROR: {report['error']}")
        return
    print(f"      DID DENSE TTM FUNDAMENTALS IMPROVE SIGNAL QUALITY? "
          f"{report.get('did_dense_ttm_improve_signal_quality')}")
    bl = report.get("phase7f_baseline", {})
    print(f"      7-F baseline IC = {bl.get('published_ic')} (recomputed {bl.get('recomputed_ic')}, "
          f"t {bl.get('recomputed_t')})")
    fc = report.get("phase7h_final_composite", {})
    print(f"      7-H final composite IC = {fc.get('mean_rank_ic')}  t = {fc.get('ic_t_stat')}")
    print(f"      incremental IC vs 7-F = {report.get('incremental_ic_vs_7f')} "
          f"(gate >= {report.get('improvement_gate')}; absolute >= {report.get('absolute_ic_gate')})  "
          f"t-change = {report.get('ic_t_stat_change_vs_7f')}")
    print(f"      bucket IC = {report.get('bucket_ic')}")
    print(f"      core TTM continuity-ready = {report.get('core_ttm_continuity_ready')}")
    print(f"      gates: {report['gate_summary']['counts']}  "
          f"placebo_collapsed={report['leakage_checks']['placebo_collapsed']}")


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 7-H TTM fundamental signal retest (offline, harness-graded).")
    p.add_argument("--out-dir", default=None)
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    report = run(out_dir=args.out_dir)
    _print_summary(report)
    rec = report.get("recommendation")
    return 0 if rec in ALLOWED_RECOMMENDATIONS and rec != REC_ERROR else 1


if __name__ == "__main__":
    raise SystemExit(main())
