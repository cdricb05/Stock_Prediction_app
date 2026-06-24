"""Phase 7-L — Momentum / Risk Strategy Viability Test.

**Track A (quant brain) research only.** Offline, point-in-time, leakage-safe. No
network, no provider call, no paid API, no API key read or required, no package
install, no model trained or deployed, **no factor-weight optimization, no factor-sign
flipping, no new alpha factors, no regime-based selection or weighting**, no Paper
Trader / GCP / deployment / broker / orders / automation, no commit, no push. Reads
only local price panels (the Phase 2K-G 128-name panel and the Phase 7-I broad 296-name
panel, both READ ONLY on D:) plus the repo-local FRED macro CSVs reused for regime
diagnostics. Writes nothing to D:; the repo receives only committed-safe summaries.

Why this phase exists
---------------------
Phase 7-F produced the best local 128-name multifactor composite (IC +0.017726, t
1.245). Phase 7-H showed dense TTM fundamentals did NOT improve it (TTM IC +0.013954,
t 0.922, TTM_SIGNAL_RELIABILITY_WEAK). Phase 7-J retested on the broad 296-name
universe and the composite did NOT survive (final composite IC +0.011255, t 0.793481,
incremental vs price-only baseline -0.005687, BROAD_UNIVERSE_SIGNAL_WEAK). Phase 7-K
then showed a genuinely survivorship-aware free-data foundation is NOT buildable
(FREE_DATA_NOT_SUFFICIENT). The multifactor composite is therefore not reliably
demonstrable on the free-data foundation. The next step is NOT another alpha-factor
phase. The remaining honest signal is simple price/momentum; this phase tests whether
that signal can support a realistic, risk-managed *research* strategy after turnover,
transaction costs, drawdowns, position limits, and regime/risk diagnostics.

The ONE question this phase answers
-----------------------------------
    CAN SIMPLE PRICE/MOMENTUM BECOME A VIABLE RISK-MANAGED RESEARCH STRATEGY,
    YES OR NO?

This is research only. It is not Paper Trader, not production, not broker/order
automation, not a deployment phase, and makes no live trade recommendation. The failed
multifactor composite is explicitly NOT a candidate strategy here.

What it does
------------
  1. Builds four momentum-only score variants from local prices (reusing the Phase 7-F
     factor construction, signs a priori, never flipped): 12-1 momentum, 6-1 momentum,
     risk-adjusted 12-1 momentum, and the equal-weight momentum bucket of the three.
  2. Evaluates every variant on BOTH the 128-name and the broad 296-name universe.
  3. Converts monthly cross-sectional scores into simple long-only portfolio
     simulations: top-decile and top-quintile, equal weight, monthly rebalance, no
     leverage, an explicit max-position cap. Sector exposure is REPORTED ONLY (no
     sector-neutralization — no point-in-time sector history exists).
  4. Applies realistic one-way transaction costs at 10 / 25 / 50 bps (charged on the
     full traded fraction each rebalance, so a name's entry and its later exit are both
     paid for).
  5. Reports annualized return proxy, annualized volatility, Sharpe proxy, max drawdown,
     turnover, average number of names, hit rate, performance by year and by regime.
  6. Compares every strategy against SPY, an equal-weight-universe benchmark, and the
     Phase 7-J broad price-only baseline IC (context).
  7. Uses Phase 7-E regimes strictly as diagnostics: no regime-based selection, no
     regime-based weighting.
  8. Emits a strict go/no-go recommendation (borderline is never rounded into a pass).

Leakage safety
--------------
Forward labels are next-month returns resolving strictly after the decision month
(reused unchanged from Phase 7-C ``build_price_factors``). Momentum skips the most
recent month. Regime labels (Phase 7-E) use only data observable at/before each scoring
month-end and never read the forward return they describe.
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

# Reuse, in order: the 7-B harness (Sharpe / drawdown / costs), the 7-C ranking engine
# (price-factor construction, loaders, normalization), the 7-E regime overlay (SPY
# loader + regime classification), the 7-F upgrade (momentum sub-factors + equal-weight
# combiner), and the 7-J retest (broad universe ticker reader + broad panel path). No
# work runs at import time in any of these.
from research import run_phase7b_validation_harness_foundation as H
from research import run_phase7c_multifactor_ranking_engine as C
from research import run_phase7e_regime_risk_overlay_foundation as E
from research import run_phase7f_signal_reliability_upgrade as F
from research import run_phase7j_broad_universe_signal_retest as J

PHASE = "7-L"
OBJECTIVE = (
    "Test whether the remaining honest signal (simple price/momentum) can support a "
    "realistic, risk-managed RESEARCH strategy after turnover, transaction costs, "
    "drawdowns, position limits, and regime/risk diagnostics, on both the 128-name and "
    "the broad 296-name universe. Long-only, equal weight, monthly rebalance, no "
    "leverage, max position cap. No factor-weight optimization, no new alpha factors, "
    "no regime selection/weighting, no failed-composite candidate, no trading/orders."
)

# --------------------------------------------------------------------------- #
# Recommendation vocabulary (exactly the allowed set).
# --------------------------------------------------------------------------- #
REC_VIABLE = "MOMENTUM_STRATEGY_VIABLE_FOR_PAPER_RESEARCH"
REC_WEAK = "MOMENTUM_SIGNAL_ONLY_WEAK"
REC_STOP = "MOMENTUM_RESEARCH_STOP"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (REC_VIABLE, REC_WEAK, REC_STOP, REC_ERROR)

# --------------------------------------------------------------------------- #
# Config (fixed, documented modelling choices — NO tuning loop, NO fit to outcome).
# --------------------------------------------------------------------------- #
QUANTILES = {"top_decile": 10, "top_quintile": 5}   # selection rules
COST_BPS_GRID = (0.0, 10.0, 25.0, 50.0)             # one-way bps charged on traded fraction
PRIMARY_COST_BPS = 25.0                             # the decision is judged net of this
MAX_POSITION = 0.10                                 # max single-name weight (no leverage)
MIN_NAMES_PORT = C.MIN_NAMES                        # 20 — minimum cross-section to form a quantile
PERIODS_PER_YEAR = 12

MOMENTUM_VARIANTS = ("mom_12_1", "mom_6_1", "mom_riskadj", "momentum_bucket")
_BUCKET_MEMBERS = ("mom_12_1", "mom_6_1", "mom_riskadj")

# Strict viability bars (a priori; borderline is never rounded up).
DD_FLOOR = -0.60                # max drawdown may not breach this absolute floor
DD_REL_SPY = 0.15               # ... and may not be more than 15pp deeper than SPY's
TURNOVER_CEIL = 0.50            # mean one-sided monthly turnover ceiling for "attractive"

# Phase 7-J broad price-only baseline (context anchor; read from its report at run time).
PHASE7J_REPORT = (_REPO_ROOT / "research" / "output" / "phase7j_broad_universe_signal_retest" /
                  "phase7j_broad_universe_signal_retest.json")

BENCHMARK = C.BENCHMARK         # "SPY"
PRICE_CSV_128 = C.PRICE_CSV
BROAD_PRICE_PANEL_CSV = J.BROAD_PRICE_PANEL_CSV

_OUT_DIR_DEFAULT = _REPO_ROOT / "research" / "output" / "phase7l_momentum_risk_strategy_viability"

_f = C._f
_round = C._round


# --------------------------------------------------------------------------- #
# Output artifact paths (committed-safe summaries only).
# --------------------------------------------------------------------------- #
@dataclass
class _OutPaths:
    base: Path

    def __post_init__(self):
        self.base.mkdir(parents=True, exist_ok=True)
        self.report = self.base / "phase7l_momentum_risk_strategy_viability.json"
        self.scoreboard = self.base / "momentum_strategy_scoreboard.csv"
        self.cost_sensitivity = self.base / "momentum_cost_sensitivity.csv"
        self.turnover = self.base / "momentum_turnover_report.csv"
        self.drawdown = self.base / "momentum_drawdown_report.csv"
        self.yearly = self.base / "momentum_yearly_performance.csv"
        self.regime = self.base / "momentum_regime_diagnostics.csv"
        self.risk_exposure = self.base / "momentum_risk_exposure_report.csv"
        self.gate_matrix = self.base / "momentum_strategy_gate_matrix.csv"
        self.next_plan = self.base / "phase7m_next_plan.json"


def _rel(p) -> str:
    try:
        return str(Path(p).resolve().relative_to(_REPO_ROOT)).replace(os.sep, "/")
    except Exception:
        return str(p).replace(os.sep, "/")


def _norm(p) -> str:
    return str(p).replace(os.sep, "/")


def _write_json(path, obj: Dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str)
        fh.write("\n")


def _write_csv(path, rows: List[Dict], columns: List[str]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in columns})


# --------------------------------------------------------------------------- #
# Universe + price loading.
# --------------------------------------------------------------------------- #
def load_universe_128() -> Tuple[List[str], pd.DataFrame, Dict[str, str]]:
    """The canonical 128-name universe used since Phase 7-C/7-F (price ∩ annual
    fundamentals ∩ sector). For momentum we use only its prices; the sector map is kept
    for the (report-only) sector exposure summary."""
    universe, prices, _annual, sector_map, _pit = F._build_universe_and_prices()
    return universe, prices, sector_map


def load_universe_296() -> Tuple[List[str], pd.DataFrame, Dict[str, str]]:
    """The broad 296-name universe from the Phase 7-I free price pilot. No point-in-time
    sector history exists for it; the static current-as-of map is reused for the
    report-only sector exposure summary (caveated, never for neutralization)."""
    universe, _summary = J.read_phase7i_usable_tickers()
    prices = C.load_prices(path=BROAD_PRICE_PANEL_CSV, universe=set(universe))
    sector_map, _pit = C.load_sector_map()
    return sorted(universe), prices, sector_map


# --------------------------------------------------------------------------- #
# Momentum score construction (reuse 7-F price factors; signs a priori, never flipped).
# --------------------------------------------------------------------------- #
def build_momentum_scores(prices: pd.DataFrame) -> Tuple[Dict, pd.DataFrame, Dict[str, pd.DataFrame]]:
    """Return (price_factor_bundle, realized_forward_returns, {variant: z-frame}).

    Cross-sectional z-scores are computed WITHOUT sector-neutralization (no PIT sector
    history). The momentum bucket is the equal-weight mean of the three momentum
    sub-factor z-scores — an equal-weight blend, never optimized."""
    pf = C.build_price_factors(prices)
    realized = pf["forward"]
    price_raw = F.build_upgraded_price_factors(pf)
    variant_z: Dict[str, pd.DataFrame] = {}
    for k in _BUCKET_MEMBERS:
        variant_z[k] = C.normalize_cross_sectional(price_raw[k], {}, sector_neutralize=False)
    variant_z["momentum_bucket"] = F.combine_equal_weight(variant_z, list(_BUCKET_MEMBERS))
    return pf, realized, variant_z


# --------------------------------------------------------------------------- #
# Long-only quantile portfolio simulation (equal weight, monthly rebalance, capped).
# --------------------------------------------------------------------------- #
def _capped_equal_weights(names: Sequence[str], max_pos: float) -> Dict[str, float]:
    """Equal weight 1/N, then iteratively cap at ``max_pos`` and redistribute the excess
    to uncapped names (no leverage; weights always sum to 1). With N >= 1/max_pos the
    cap never binds and this is exactly equal weight."""
    n = len(names)
    if n == 0:
        return {}
    w = {t: 1.0 / n for t in names}
    for _ in range(n):
        over = {t: v for t, v in w.items() if v > max_pos + 1e-12}
        if not over:
            break
        excess = sum(v - max_pos for v in over.values())
        for t in over:
            w[t] = max_pos
        free = [t for t in w if w[t] < max_pos - 1e-12]
        if not free:
            break
        add = excess / len(free)
        for t in free:
            w[t] += add
    return w


def simulate_long_only(scores: pd.DataFrame, realized: pd.DataFrame, *, q: int,
                       max_pos: float = MAX_POSITION) -> Dict:
    """Top-1/q long-only equal-weight portfolio, rebalanced monthly. Returns aligned
    per-period gross returns, traded fractions (Σ|Δw|, both legs), one-sided turnover
    (0.5·Σ|Δw|), selected-name counts, max weight and effective N, plus the realized
    weights per month (for the sector exposure report)."""
    j = scores.merge(realized, on=["month", "ticker"], how="inner")
    months = sorted(j["month"].unique())
    prev_w: Dict[str, float] = {}
    period_keys: List[str] = []
    gross: List[float] = []
    traded: List[float] = []
    one_sided: List[float] = []
    n_sel: List[int] = []
    max_w: List[float] = []
    eff_n: List[float] = []
    weights_by_month: Dict[str, Dict[str, float]] = {}
    for m in months:
        g = j[j["month"] == m]
        n = len(g)
        if n < MIN_NAMES_PORT:
            continue
        k = max(1, n // q)
        top = g.sort_values("z").iloc[-k:]
        names = list(top["ticker"])
        w = _capped_equal_weights(names, max_pos)
        fwd = {str(t): float(fv) for t, fv in zip(top["ticker"], top["fwd"])}
        ret = sum(w[t] * fwd[str(t)] for t in names)
        union = set(w) | set(prev_w)
        traded_fraction = sum(abs(w.get(t, 0.0) - prev_w.get(t, 0.0)) for t in union)
        period_keys.append(str(m))
        gross.append(float(ret))
        traded.append(float(traded_fraction))
        one_sided.append(float(0.5 * traded_fraction))
        n_sel.append(int(k))
        max_w.append(float(max(w.values())) if w else 0.0)
        eff_n.append(float(1.0 / sum(v * v for v in w.values())) if w else 0.0)
        weights_by_month[str(m)] = w
        prev_w = w
    return {"period_keys": period_keys, "gross": gross, "traded": traded,
            "one_sided_turnover": one_sided, "n_selected": n_sel,
            "max_weight": max_w, "effective_n": eff_n, "weights_by_month": weights_by_month}


def simulate_equal_weight_universe(realized: pd.DataFrame) -> Dict:
    """Equal-weight-ALL-names benchmark: each month hold every name with a realized
    forward return at equal weight. Low turnover (only membership churn)."""
    months = sorted(realized["month"].unique())
    prev_w: Dict[str, float] = {}
    period_keys: List[str] = []
    gross: List[float] = []
    traded: List[float] = []
    one_sided: List[float] = []
    n_sel: List[int] = []
    for m in months:
        g = realized[realized["month"] == m]
        names = [str(t) for t in g["ticker"]]
        n = len(names)
        if n < MIN_NAMES_PORT:
            continue
        w = {t: 1.0 / n for t in names}
        fwd = {str(t): float(fv) for t, fv in zip(g["ticker"], g["fwd"])}
        ret = sum(w[t] * fwd[t] for t in names)
        union = set(w) | set(prev_w)
        traded_fraction = sum(abs(w.get(t, 0.0) - prev_w.get(t, 0.0)) for t in union)
        period_keys.append(str(m))
        gross.append(float(ret))
        traded.append(float(traded_fraction))
        one_sided.append(float(0.5 * traded_fraction))
        n_sel.append(int(n))
        prev_w = w
    return {"period_keys": period_keys, "gross": gross, "traded": traded,
            "one_sided_turnover": one_sided, "n_selected": n_sel}


def load_spy_benchmark(price_csv: str, month_keys: Sequence[str]) -> Dict:
    """SPY buy-and-hold monthly returns aligned to ``month_keys`` (the m->m+1 forward
    return, matching the strategy's label convention). No cost, no turnover."""
    spy_daily = E.load_benchmark_daily(path=price_csv)
    if spy_daily.empty:
        return {"period_keys": [], "gross": []}
    me = E.benchmark_month_end_metrics(spy_daily)
    fwd = {str(m): _f(v) for m, v in zip(me["month"], me["fwd"])}
    keys = [k for k in month_keys if fwd.get(k) is not None]
    return {"period_keys": keys, "gross": [float(fwd[k]) for k in keys]}


# --------------------------------------------------------------------------- #
# Performance metrics.
# --------------------------------------------------------------------------- #
def perf_metrics(returns: Sequence[float]) -> Dict:
    """Annualized return proxy (geometric CAGR), annualized vol, Sharpe proxy, max
    drawdown, hit rate, and mean monthly return for a per-period return series."""
    arr = np.asarray([r for r in returns if r is not None and math.isfinite(r)], dtype=float)
    n = len(arr)
    if n == 0:
        return {"n_periods": 0, "ann_return": None, "ann_vol": None, "sharpe": None,
                "max_drawdown": None, "hit_rate": None, "mean_monthly": None,
                "total_return": None}
    equity = float(np.prod(1.0 + arr))
    cagr = (equity ** (PERIODS_PER_YEAR / n) - 1.0) if equity > 0 else -1.0
    vol = float(np.std(arr, ddof=1) * math.sqrt(PERIODS_PER_YEAR)) if n > 1 else None
    return {"n_periods": int(n), "ann_return": float(cagr), "ann_vol": vol,
            "sharpe": H.sharpe(arr, periods_per_year=PERIODS_PER_YEAR),
            "max_drawdown": H.max_drawdown(arr), "hit_rate": float(np.mean(arr > 0.0)),
            "mean_monthly": float(np.mean(arr)), "total_return": equity - 1.0}


def net_returns(gross: Sequence[float], traded: Sequence[float], cost_bps: float) -> List[float]:
    """Net per-period return = gross - traded_fraction * (cost_bps / 1e4). One-way bps
    charged on every unit traded (entry and exit both paid)."""
    c = cost_bps / 1e4
    return [float(g - t * c) for g, t in zip(gross, traded)]


def yearly_returns(period_keys: Sequence[str], returns: Sequence[float]) -> Dict[str, float]:
    """Compounded return by calendar year (period keys are 'YYYY-MM')."""
    groups: Dict[str, List[float]] = {}
    for k, r in zip(period_keys, returns):
        if r is None or not math.isfinite(r):
            continue
        groups.setdefault(str(k)[:4], []).append(r)
    return {y: float(np.prod([1.0 + x for x in v]) - 1.0) for y, v in sorted(groups.items())}


# --------------------------------------------------------------------------- #
# Regime diagnostics (reuse 7-E classification; descriptive ONLY).
# --------------------------------------------------------------------------- #
def regime_labels_for_panel(price_csv: str) -> Dict[str, Dict[str, str]]:
    """Per-axis {period_key: label} from the Phase 7-E classifier, using the SPY series
    in ``price_csv`` plus the repo-local macro CSVs. Empty if SPY/macro unavailable."""
    spy_daily = E.load_benchmark_daily(path=price_csv)
    if spy_daily.empty:
        return {}
    me = E.benchmark_month_end_metrics(spy_daily)
    cpi_level = E.load_macro_monthly(E.MACRO_CPI_CSV, "CPIAUCSL")
    cpi_yoy = cpi_level.pct_change(E.CPI_YOY_M).dropna() if not cpi_level.empty else cpi_level
    dgs10 = E.load_macro_monthly(E.MACRO_YIELDS_CSV, "DGS10")
    usd = E.load_macro_monthly(E.MACRO_DOLLAR_CSV, "DTWEXBGS")
    reg = E.classify_regimes(me, cpi_yoy=cpi_yoy, dgs10=dgs10, usd=usd)
    return {ax: reg["axis_labels"][ax] for ax in reg["available_axes"]}


def returns_by_regime(period_keys: Sequence[str], returns: Sequence[float],
                      axis_labels: Dict[str, str]) -> Dict[str, Dict]:
    """Partition a per-period return series by regime label; report n / mean monthly /
    annualized vol / Sharpe per label. Descriptive only — never used to select/weight."""
    by_label: Dict[str, List[float]] = {}
    for k, r in zip(period_keys, returns):
        lab = axis_labels.get(k)
        if lab is None or r is None or not math.isfinite(r):
            continue
        by_label.setdefault(lab, []).append(float(r))
    out: Dict[str, Dict] = {}
    for lab, rs in by_label.items():
        arr = np.asarray(rs, dtype=float)
        out[lab] = {"n_months": int(len(arr)),
                    "mean_monthly": float(np.mean(arr)) if len(arr) else None,
                    "ann_vol": float(np.std(arr, ddof=1) * math.sqrt(PERIODS_PER_YEAR)) if len(arr) > 1 else None,
                    "sharpe": H.sharpe(arr, periods_per_year=PERIODS_PER_YEAR)}
    return out


# --------------------------------------------------------------------------- #
# Sector exposure report (report-only; current-as-of map; NO neutralization).
# --------------------------------------------------------------------------- #
def sector_exposure(weights_by_month: Dict[str, Dict[str, float]],
                    sector_map: Dict[str, str]) -> Dict:
    """Average portfolio weight by sector across all rebalances + sector-map coverage.
    Uses the static current-as-of sector map (NOT point-in-time) — a caveated exposure
    report only, never used to neutralize or select."""
    sector_w: Dict[str, float] = {}
    covered = 0
    total = 0
    n_months = max(1, len(weights_by_month))
    for _m, w in weights_by_month.items():
        for t, wt in w.items():
            total += wt
            sec = sector_map.get(str(t))
            if sec:
                covered += wt
                sector_w[sec] = sector_w.get(sec, 0.0) + wt
    avg_sector = {s: v / n_months for s, v in sorted(sector_w.items(), key=lambda kv: -kv[1])}
    coverage = (covered / total) if total else 0.0
    top_sector, top_w = ("", 0.0)
    if avg_sector:
        top_sector = next(iter(avg_sector))
        top_w = avg_sector[top_sector]
    return {"sector_map_coverage_fraction": coverage, "avg_sector_weights": avg_sector,
            "top_sector": top_sector, "top_sector_avg_weight": top_w}


# --------------------------------------------------------------------------- #
# Strategy evaluation across universes / variants / selection rules.
# --------------------------------------------------------------------------- #
@dataclass
class _StrategyResult:
    universe: str
    variant: str
    selection: str
    sim: Dict
    gross_metrics: Dict
    net_metrics: Dict                 # at PRIMARY_COST_BPS
    cost_curve: Dict                  # {bps: {ann_return, sharpe}}
    mean_one_sided_turnover: Optional[float]
    avg_names: Optional[float]
    avg_max_weight: Optional[float]
    avg_effective_n: Optional[float]

    @property
    def config_id(self) -> str:
        return f"{self.universe}:{self.variant}:{self.selection}"


def evaluate_strategies(universe_name: str, realized: pd.DataFrame,
                        variant_z: Dict[str, pd.DataFrame]) -> List[_StrategyResult]:
    results: List[_StrategyResult] = []
    for variant in MOMENTUM_VARIANTS:
        scores = variant_z.get(variant)
        if scores is None or scores.empty:
            continue
        for sel_name, q in QUANTILES.items():
            sim = simulate_long_only(scores, realized, q=q)
            if not sim["gross"]:
                continue
            gross_m = perf_metrics(sim["gross"])
            net_primary = perf_metrics(net_returns(sim["gross"], sim["traded"], PRIMARY_COST_BPS))
            cost_curve = {}
            for bps in COST_BPS_GRID:
                nm = perf_metrics(net_returns(sim["gross"], sim["traded"], bps))
                cost_curve[bps] = {"ann_return": nm["ann_return"], "sharpe": nm["sharpe"]}
            results.append(_StrategyResult(
                universe=universe_name, variant=variant, selection=sel_name, sim=sim,
                gross_metrics=gross_m, net_metrics=net_primary, cost_curve=cost_curve,
                mean_one_sided_turnover=float(np.mean(sim["one_sided_turnover"])) if sim["one_sided_turnover"] else None,
                avg_names=float(np.mean(sim["n_selected"])) if sim["n_selected"] else None,
                avg_max_weight=float(np.mean(sim["max_weight"])) if sim.get("max_weight") else None,
                avg_effective_n=float(np.mean(sim["effective_n"])) if sim.get("effective_n") else None))
    return results


# --------------------------------------------------------------------------- #
# Strict decision rule (borderline never rounded up).
# --------------------------------------------------------------------------- #
def _dd_acceptable(mdd: Optional[float], spy_mdd: Optional[float]) -> bool:
    if mdd is None:
        return False
    if mdd < DD_FLOOR:
        return False
    if spy_mdd is not None and mdd < spy_mdd - DD_REL_SPY:
        return False
    return True


def _config_passes(r: _StrategyResult, spy_metrics: Dict, eqw_metrics: Dict) -> bool:
    """Whether a single config independently clears every viability bar (used to report
    robustness, so the verdict is not a best-of-N selection artifact)."""
    ns = r.net_metrics.get("sharpe")
    nr = r.net_metrics.get("ann_return")
    if ns is None or nr is None or ns <= 0 or nr <= 0:
        return False
    spy_s, eqw_s = spy_metrics.get("sharpe"), eqw_metrics.get("sharpe")
    if spy_s is None or eqw_s is None or ns <= spy_s or ns <= eqw_s:
        return False
    if not _dd_acceptable(r.net_metrics.get("max_drawdown"), spy_metrics.get("max_drawdown")):
        return False
    return r.mean_one_sided_turnover is not None and r.mean_one_sided_turnover <= TURNOVER_CEIL


def derive_recommendation(best: Optional[_StrategyResult], spy_metrics: Dict,
                          eqw_metrics: Dict,
                          all_results: Optional[List[_StrategyResult]] = None) -> Tuple[str, str, Dict]:
    """Judge the BEST strategy (highest net Sharpe at the primary cost) against the
    strict bars, and report how many configs independently clear them (robustness, not
    selection). Returns (recommendation, viable_yes_no, decision_detail)."""
    if best is None:
        return REC_STOP, "NO", {"reason": "no gradable momentum strategy produced"}
    net25_sharpe = best.net_metrics.get("sharpe")
    net25_ret = best.net_metrics.get("ann_return")
    gross_sharpe = best.gross_metrics.get("sharpe")
    mdd = best.net_metrics.get("max_drawdown")
    spy_sharpe = spy_metrics.get("sharpe")
    eqw_sharpe = eqw_metrics.get("sharpe")
    spy_mdd = spy_metrics.get("max_drawdown")

    positive_net = net25_sharpe is not None and net25_sharpe > 0 and net25_ret is not None and net25_ret > 0
    beats_spy = net25_sharpe is not None and spy_sharpe is not None and net25_sharpe > spy_sharpe
    beats_eqw = net25_sharpe is not None and eqw_sharpe is not None and net25_sharpe > eqw_sharpe
    beats_benchmarks = beats_spy and beats_eqw
    dd_ok = _dd_acceptable(mdd, spy_mdd)
    turnover_ok = (best.mean_one_sided_turnover is not None
                   and best.mean_one_sided_turnover <= TURNOVER_CEIL)
    attractive = dd_ok and turnover_ok
    raw_positive = gross_sharpe is not None and gross_sharpe > 0

    detail = {
        "decision_config": best.config_id,
        "primary_cost_bps": PRIMARY_COST_BPS,
        "net_sharpe_at_primary_cost": _round(net25_sharpe),
        "net_ann_return_at_primary_cost": _round(net25_ret),
        "gross_sharpe": _round(gross_sharpe),
        "net_max_drawdown": _round(mdd),
        "spy_sharpe": _round(spy_sharpe), "spy_max_drawdown": _round(spy_mdd),
        "equal_weight_universe_sharpe": _round(eqw_sharpe),
        "mean_one_sided_turnover": _round(best.mean_one_sided_turnover),
        "positive_net_after_cost": positive_net, "beats_spy": beats_spy,
        "beats_equal_weight_universe": beats_eqw, "drawdown_acceptable": dd_ok,
        "turnover_acceptable": turnover_ok, "raw_momentum_positive": raw_positive,
        "dd_floor": DD_FLOOR, "dd_rel_spy": DD_REL_SPY, "turnover_ceiling": TURNOVER_CEIL,
    }
    if all_results:
        passing = [r.config_id for r in all_results if _config_passes(r, spy_metrics, eqw_metrics)]
        detail["n_configs"] = len(all_results)
        detail["configs_independently_viable"] = len(passing)
        detail["canonical_12_1_bucket_also_viable"] = bool(
            any(r.variant in ("mom_12_1", "momentum_bucket") and _config_passes(r, spy_metrics, eqw_metrics)
                for r in all_results))
        # Incremental Sharpe of the best momentum over simply holding the equal-weight
        # current universe — the honest measure of what momentum adds beyond survivorship.
        if net25_sharpe is not None and eqw_sharpe is not None:
            detail["best_net_sharpe_minus_eqw_universe"] = _round(net25_sharpe - eqw_sharpe)

    if positive_net and dd_ok and turnover_ok and beats_benchmarks:
        return REC_VIABLE, "YES", detail
    if (not positive_net) or (not beats_benchmarks):
        return REC_STOP, "NO", detail
    if raw_positive:
        # Positive net and beats benchmarks, but unattractive on drawdown/turnover.
        return REC_WEAK, "NO", detail
    return REC_STOP, "NO", detail


def pick_best(results: List[_StrategyResult]) -> Optional[_StrategyResult]:
    """Best strategy = highest net Sharpe at the primary cost (None Sharpe sorts last)."""
    gradable = [r for r in results if r.net_metrics.get("sharpe") is not None]
    if not gradable:
        return None
    return max(gradable, key=lambda r: r.net_metrics["sharpe"])


# --------------------------------------------------------------------------- #
# Gate matrix.
# --------------------------------------------------------------------------- #
_SAFETY_GATES = {
    "local_data_only", "no_paid_api", "no_packages_installed", "no_new_data_collected",
    "no_factor_weight_optimization", "no_new_alpha_factors", "failed_composite_excluded",
    "no_regime_selection_or_weighting", "no_leverage", "no_trading_order_automation",
    "no_paper_trader_gcp_broker", "not_committed", "not_pushed",
}


def build_gate_matrix(best: Optional[_StrategyResult], detail: Dict, spy_metrics: Dict,
                      eqw_metrics: Dict, n_results: int) -> Tuple[List[Dict], Dict]:
    def g(name, status, det):
        return {"gate": name, "status": status, "detail": det}

    rows = [
        # --- capability ---
        g("strategies_evaluated", "PASS" if n_results > 0 else "FAIL",
          "%d (universe x variant x selection) strategy configs simulated" % n_results),
        g("both_universes_tested", "PASS" if n_results >= 8 else "WARN",
          "expected 4 variants x 2 selections x 2 universes = 16 configs; produced %d" % n_results),
        g("spy_benchmark_available", "PASS" if spy_metrics.get("sharpe") is not None else "FAIL",
          "SPY Sharpe = %s" % _round(spy_metrics.get("sharpe"))),
        g("equal_weight_universe_benchmark_available",
          "PASS" if eqw_metrics.get("sharpe") is not None else "FAIL",
          "equal-weight universe Sharpe = %s" % _round(eqw_metrics.get("sharpe"))),
        # --- results (honest; may FAIL — that is the finding, not an error) ---
        g("positive_net_sharpe_after_primary_cost",
          "PASS" if detail.get("positive_net_after_cost") else "FAIL",
          "best net Sharpe @ %sbps = %s" % (PRIMARY_COST_BPS, detail.get("net_sharpe_at_primary_cost"))),
        g("beats_spy_risk_adjusted", "PASS" if detail.get("beats_spy") else "FAIL",
          "best net Sharpe %s vs SPY %s" % (detail.get("net_sharpe_at_primary_cost"), detail.get("spy_sharpe"))),
        g("beats_equal_weight_universe_risk_adjusted",
          "PASS" if detail.get("beats_equal_weight_universe") else "FAIL",
          "best net Sharpe %s vs eqw-universe %s" % (detail.get("net_sharpe_at_primary_cost"),
                                                     detail.get("equal_weight_universe_sharpe"))),
        g("drawdown_acceptable", "PASS" if detail.get("drawdown_acceptable") else "FAIL",
          "best net max drawdown %s (floor %s; <= SPY %s - %s)" % (detail.get("net_max_drawdown"),
              DD_FLOOR, detail.get("spy_max_drawdown"), DD_REL_SPY)),
        g("turnover_acceptable", "PASS" if detail.get("turnover_acceptable") else "WARN",
          "mean one-sided turnover %s (ceiling %s)" % (detail.get("mean_one_sided_turnover"), TURNOVER_CEIL)),
        # --- validation caveats ---
        g("survivorship_caveat", "WARN",
          "both universes are current-as-of constituents (Phase 7-K: no free survivorship-aware data); "
          "long-only results are survivorship-biased upward"),
        g("sector_neutralization_caveat", "WARN",
          "no point-in-time sector history; sector exposure is reported only, never neutralized"),
        g("history_span_caveat", "WARN",
          "price history begins 2016; no 2008-style crash in sample — drawdowns understate tail risk"),
        # --- safety / scope ---
        g("local_data_only", "PASS", "only local price panels + repo-local macro CSVs read"),
        g("no_paid_api", "PASS", "no API called"),
        g("no_packages_installed", "PASS", "numpy + pandas only; nothing installed"),
        g("no_new_data_collected", "PASS", "no new data collected; existing local panels only"),
        g("no_factor_weight_optimization", "PASS", "equal weight within the momentum bucket; no weights fit"),
        g("no_new_alpha_factors", "PASS", "only 12-1 / 6-1 / risk-adjusted momentum (reused, signs a priori)"),
        g("failed_composite_excluded", "PASS", "the failed multifactor composite is NOT a candidate strategy"),
        g("no_factor_sign_flipping", "PASS", "momentum signs a priori; never flipped"),
        g("no_regime_selection_or_weighting", "PASS", "regimes used as diagnostics only"),
        g("no_leverage", "PASS", "long-only, weights sum to 1, max position cap %.2f" % MAX_POSITION),
        g("no_trading_order_automation", "PASS", "no order/broker/automation logic"),
        g("no_paper_trader_gcp_broker", "PASS", "no Paper Trader / GCP / broker / deployment"),
        g("not_committed", "PASS", "not committed"),
        g("not_pushed", "PASS", "not pushed"),
    ]
    counts = {"pass": sum(1 for r in rows if r["status"] == "PASS"),
              "fail": sum(1 for r in rows if r["status"] == "FAIL"),
              "warn": sum(1 for r in rows if r["status"] == "WARN"),
              "safety_fail": any(r["status"] == "FAIL" and r["gate"] in _SAFETY_GATES for r in rows)}
    return rows, counts


# --------------------------------------------------------------------------- #
# Artifact writers.
# --------------------------------------------------------------------------- #
def _bench_row(name: str, m: Dict, mean_turnover: Optional[float], avg_names: Optional[float]) -> Dict:
    return {"config": name, "universe": "benchmark", "variant": name, "selection": "buy_and_hold",
            "n_periods": m.get("n_periods"), "ann_return": _round(m.get("ann_return")),
            "ann_vol": _round(m.get("ann_vol")), "gross_sharpe": _round(m.get("sharpe")),
            "net_sharpe_25bps": _round(m.get("sharpe")), "max_drawdown": _round(m.get("max_drawdown")),
            "mean_one_sided_turnover": _round(mean_turnover), "avg_names": _round(avg_names),
            "hit_rate": _round(m.get("hit_rate"))}


def _write_scoreboard(paths, results: List[_StrategyResult], spy_metrics, eqw_metrics,
                      eqw_turnover, eqw_names) -> None:
    rows = []
    for r in results:
        rows.append({"config": r.config_id, "universe": r.universe, "variant": r.variant,
                     "selection": r.selection, "n_periods": r.gross_metrics.get("n_periods"),
                     "ann_return": _round(r.gross_metrics.get("ann_return")),
                     "ann_vol": _round(r.gross_metrics.get("ann_vol")),
                     "gross_sharpe": _round(r.gross_metrics.get("sharpe")),
                     "net_sharpe_25bps": _round(r.net_metrics.get("sharpe")),
                     "max_drawdown": _round(r.net_metrics.get("max_drawdown")),
                     "mean_one_sided_turnover": _round(r.mean_one_sided_turnover),
                     "avg_names": _round(r.avg_names),
                     "hit_rate": _round(r.gross_metrics.get("hit_rate"))})
    rows.sort(key=lambda x: (x["net_sharpe_25bps"] if isinstance(x["net_sharpe_25bps"], (int, float)) else -9),
              reverse=True)
    rows.append(_bench_row("SPY", spy_metrics, None, None))
    rows.append(_bench_row("equal_weight_universe", eqw_metrics, eqw_turnover, eqw_names))
    _write_csv(paths.scoreboard, rows,
               ["config", "universe", "variant", "selection", "n_periods", "ann_return",
                "ann_vol", "gross_sharpe", "net_sharpe_25bps", "max_drawdown",
                "mean_one_sided_turnover", "avg_names", "hit_rate"])


def _write_cost_sensitivity(paths, results: List[_StrategyResult]) -> None:
    rows = []
    for r in results:
        row = {"config": r.config_id}
        for bps in COST_BPS_GRID:
            cc = r.cost_curve.get(bps, {})
            row["net_ann_return_%dbps" % int(bps)] = _round(cc.get("ann_return"))
            row["net_sharpe_%dbps" % int(bps)] = _round(cc.get("sharpe"))
        rows.append(row)
    cols = ["config"]
    for bps in COST_BPS_GRID:
        cols += ["net_ann_return_%dbps" % int(bps), "net_sharpe_%dbps" % int(bps)]
    _write_csv(paths.cost_sensitivity, rows, cols)


def _write_turnover(paths, results: List[_StrategyResult]) -> None:
    rows = []
    for r in results:
        sim = r.sim
        rows.append({"config": r.config_id,
                     "mean_one_sided_turnover": _round(r.mean_one_sided_turnover),
                     "mean_traded_fraction": _round(float(np.mean(sim["traded"])) if sim["traded"] else None),
                     "max_one_sided_turnover": _round(float(np.max(sim["one_sided_turnover"])) if sim["one_sided_turnover"] else None),
                     "annual_turnover_proxy": _round((r.mean_one_sided_turnover or 0.0) * PERIODS_PER_YEAR),
                     "avg_names": _round(r.avg_names), "n_periods": r.gross_metrics.get("n_periods")})
    _write_csv(paths.turnover, rows,
               ["config", "mean_one_sided_turnover", "mean_traded_fraction",
                "max_one_sided_turnover", "annual_turnover_proxy", "avg_names", "n_periods"])


def _write_drawdown(paths, results: List[_StrategyResult], spy_metrics, eqw_metrics) -> None:
    rows = []
    for r in results:
        rows.append({"config": r.config_id,
                     "gross_max_drawdown": _round(r.gross_metrics.get("max_drawdown")),
                     "net_25bps_max_drawdown": _round(r.net_metrics.get("max_drawdown")),
                     "ann_return_net_25bps": _round(r.net_metrics.get("ann_return")),
                     "ann_vol": _round(r.gross_metrics.get("ann_vol"))})
    rows.append({"config": "SPY", "gross_max_drawdown": _round(spy_metrics.get("max_drawdown")),
                 "net_25bps_max_drawdown": _round(spy_metrics.get("max_drawdown")),
                 "ann_return_net_25bps": _round(spy_metrics.get("ann_return")),
                 "ann_vol": _round(spy_metrics.get("ann_vol"))})
    rows.append({"config": "equal_weight_universe",
                 "gross_max_drawdown": _round(eqw_metrics.get("max_drawdown")),
                 "net_25bps_max_drawdown": _round(eqw_metrics.get("max_drawdown")),
                 "ann_return_net_25bps": _round(eqw_metrics.get("ann_return")),
                 "ann_vol": _round(eqw_metrics.get("ann_vol"))})
    _write_csv(paths.drawdown, rows,
               ["config", "gross_max_drawdown", "net_25bps_max_drawdown",
                "ann_return_net_25bps", "ann_vol"])


def _write_yearly(paths, results: List[_StrategyResult], spy_bench, eqw_bench,
                  spy_metrics, eqw_metrics) -> None:
    rows = []
    for r in results:
        gross_yr = yearly_returns(r.sim["period_keys"], r.sim["gross"])
        net_yr = yearly_returns(r.sim["period_keys"], net_returns(r.sim["gross"], r.sim["traded"], PRIMARY_COST_BPS))
        for y in sorted(gross_yr):
            rows.append({"config": r.config_id, "year": y,
                         "gross_return": _round(gross_yr.get(y)),
                         "net_return_25bps": _round(net_yr.get(y))})
    for name, bench in (("SPY", spy_bench), ("equal_weight_universe", eqw_bench)):
        yr = yearly_returns(bench["period_keys"], bench["gross"])
        for y in sorted(yr):
            rows.append({"config": name, "year": y, "gross_return": _round(yr.get(y)),
                         "net_return_25bps": _round(yr.get(y))})
    _write_csv(paths.yearly, rows, ["config", "year", "gross_return", "net_return_25bps"])


def _write_regime(paths, regime_block: Dict) -> None:
    rows = []
    for config_id, axes in regime_block.items():
        for axis, labels in axes.items():
            for lab, d in sorted(labels.items()):
                rows.append({"config": config_id, "regime_axis": axis, "regime_label": lab,
                             "n_months": d["n_months"], "mean_monthly_return": _round(d["mean_monthly"]),
                             "ann_vol": _round(d["ann_vol"]), "sharpe": _round(d["sharpe"]),
                             "note": "diagnostic only; not used for selection or weighting"})
    _write_csv(paths.regime, rows,
               ["config", "regime_axis", "regime_label", "n_months", "mean_monthly_return",
                "ann_vol", "sharpe", "note"])


def _write_risk_exposure(paths, results: List[_StrategyResult], sector_blocks: Dict[str, Dict]) -> None:
    rows = []
    for r in results:
        sec = sector_blocks.get(r.config_id, {})
        rows.append({"config": r.config_id, "universe": r.universe, "variant": r.variant,
                     "selection": r.selection, "avg_names": _round(r.avg_names),
                     "avg_max_weight": _round(r.avg_max_weight),
                     "avg_effective_n": _round(r.avg_effective_n),
                     "sector_map_coverage_fraction": _round(sec.get("sector_map_coverage_fraction")),
                     "top_sector": sec.get("top_sector", ""),
                     "top_sector_avg_weight": _round(sec.get("top_sector_avg_weight")),
                     "note": "exposure report only; current-as-of sector map (NOT point-in-time); no neutralization"})
    _write_csv(paths.risk_exposure, rows,
               ["config", "universe", "variant", "selection", "avg_names", "avg_max_weight",
                "avg_effective_n", "sector_map_coverage_fraction", "top_sector",
                "top_sector_avg_weight", "note"])


def _write_gate_matrix(paths, gates: List[Dict]) -> None:
    _write_csv(paths.gate_matrix, gates, ["gate", "status", "detail"])


def _write_next_plan(paths, recommendation: str, viable: str, detail: Dict) -> None:
    if recommendation == REC_VIABLE:
        steps = [{"step": "phase7m_paper_research_strategy_spec", "scope": "RESEARCH_SPEC",
                  "detail": ("Momentum cleared the strict viability bars in research. Next: write a "
                             "paper-research strategy specification (rules, rebalance cadence, cost/"
                             "turnover budget, position limits, drawdown stop policy) and a "
                             "survivorship-bias impact study, BEFORE any Paper Trader preview. Still "
                             "no orders, no automation, no optimization. Acquiring survivorship-free "
                             "data would materially raise confidence.")}]
    elif recommendation == REC_WEAK:
        steps = [{"step": "phase7m_momentum_attractiveness_review", "scope": "DECISION",
                  "detail": ("Raw momentum is positive but unattractive after costs/drawdown/turnover. "
                             "Do NOT optimize weights or add factors to rescue it. Either accept a lower-"
                             "turnover, lower-ambition momentum sleeve as research-only, or stop. A "
                             "survivorship-free data foundation (paid) is the only path that could "
                             "change the verdict.")}]
    else:  # STOP
        steps = [{"step": "phase7m_stop_unless_paid_data", "scope": "DECISION",
                  "detail": ("Momentum is not a viable risk-managed strategy on the free-data "
                             "foundation after realistic costs and benchmarks. Recommend stopping "
                             "multifactor / momentum alpha research unless survivorship-free paid data "
                             "(CRSP / Norgate / Sharadar) is explicitly approved. No further local "
                             "factor polishing.")}]
    _write_json(paths.next_plan, {
        "from_phase": "7-L", "recommendation": recommendation,
        "momentum_viable_for_paper_research": viable,
        "decision_config": detail.get("decision_config"),
        "net_sharpe_at_primary_cost": detail.get("net_sharpe_at_primary_cost"),
        "next_steps": steps,
        "hard_constraints": [
            "research only — not Paper Trader, not production, not deployment",
            "free/local data only", "no paid APIs unless explicitly approved",
            "do not install packages", "no factor-weight optimization",
            "no new alpha factors", "no factor-sign flipping",
            "no regime-based selection or weighting", "no leverage",
            "no trading/order/automation", "no Paper Trader/GCP/broker/deployment",
            "do not commit", "do not push"],
    })


# --------------------------------------------------------------------------- #
# Report assembly.
# --------------------------------------------------------------------------- #
def _strategy_summary(r: _StrategyResult) -> Dict:
    return {"config": r.config_id, "universe": r.universe, "variant": r.variant,
            "selection": r.selection, "n_periods": r.gross_metrics.get("n_periods"),
            "gross": {"ann_return": _round(r.gross_metrics.get("ann_return")),
                      "ann_vol": _round(r.gross_metrics.get("ann_vol")),
                      "sharpe": _round(r.gross_metrics.get("sharpe")),
                      "max_drawdown": _round(r.gross_metrics.get("max_drawdown")),
                      "hit_rate": _round(r.gross_metrics.get("hit_rate"))},
            "net_25bps": {"ann_return": _round(r.net_metrics.get("ann_return")),
                          "sharpe": _round(r.net_metrics.get("sharpe")),
                          "max_drawdown": _round(r.net_metrics.get("max_drawdown"))},
            "cost_curve": {str(int(b)): {"ann_return": _round(v["ann_return"]),
                                         "sharpe": _round(v["sharpe"])}
                           for b, v in r.cost_curve.items()},
            "mean_one_sided_turnover": _round(r.mean_one_sided_turnover),
            "avg_names": _round(r.avg_names)}


def _bench_summary(m: Dict) -> Dict:
    return {"ann_return": _round(m.get("ann_return")), "ann_vol": _round(m.get("ann_vol")),
            "sharpe": _round(m.get("sharpe")), "max_drawdown": _round(m.get("max_drawdown")),
            "hit_rate": _round(m.get("hit_rate")), "n_periods": m.get("n_periods")}


def _assemble_report(paths, results, best, detail, recommendation, viable,
                     spy_metrics, eqw_metrics, phase7j_baseline_ic, gate_counts,
                     universe_sizes, span) -> Dict:
    return {
        "phase": PHASE, "objective": OBJECTIVE,
        "question": "CAN SIMPLE PRICE/MOMENTUM BECOME A VIABLE RISK-MANAGED RESEARCH STRATEGY?",
        "momentum_viable_for_paper_research": viable,
        "recommendation": recommendation,
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "universes": universe_sizes,
        "history_span": span,
        "strategy_variants_tested": list(MOMENTUM_VARIANTS),
        "selection_rules": list(QUANTILES),
        "cost_bps_grid": list(COST_BPS_GRID),
        "primary_cost_bps": PRIMARY_COST_BPS,
        "max_position_cap": MAX_POSITION,
        "n_strategy_configs": len(results),
        "decision": detail,
        "decision_rule": {
            "viable_if": "net Sharpe @ primary cost > 0 AND drawdown acceptable AND turnover "
                         "acceptable AND beats SPY and equal-weight-universe on Sharpe",
            "weak_if": "raw momentum positive and beats benchmarks net of cost, but drawdown/"
                       "turnover make it unattractive",
            "stop_if": "fails after costs (net Sharpe <= 0 or negative net return) OR underperforms "
                       "either benchmark on a risk-adjusted basis",
            "dd_floor": DD_FLOOR, "dd_rel_spy": DD_REL_SPY, "turnover_ceiling": TURNOVER_CEIL,
            "borderline_rounded_up": False,
        },
        "best_strategy": _strategy_summary(best) if best else None,
        "all_strategies": [_strategy_summary(r) for r in results],
        "benchmarks": {
            "spy": _bench_summary(spy_metrics),
            "equal_weight_universe": _bench_summary(eqw_metrics),
            "phase7j_broad_price_only_baseline_ic": phase7j_baseline_ic,
            "phase7j_note": "Phase 7-J broad price-only baseline is an IC (selection skill), not a "
                            "portfolio; the mom_12_1 variant here is its portfolio analogue.",
        },
        "gate_matrix_summary": gate_counts,
        "caveats": [
            "Both universes are current-as-of constituents; long-only momentum results are "
            "survivorship-biased upward (Phase 7-K: no free survivorship-aware data foundation).",
            "No point-in-time sector history: sector exposure is reported only, never neutralized.",
            "Price history starts 2016 — no 2008-style crash in sample; drawdowns understate tail risk.",
            "Transaction costs and the max-position cap are modelling assumptions, not a real book.",
            "Regimes are descriptive diagnostics only — never used to select or weight.",
        ],
        "artifacts": {
            "report": _rel(paths.report), "scoreboard": _rel(paths.scoreboard),
            "cost_sensitivity": _rel(paths.cost_sensitivity), "turnover": _rel(paths.turnover),
            "drawdown": _rel(paths.drawdown), "yearly_performance": _rel(paths.yearly),
            "regime_diagnostics": _rel(paths.regime), "risk_exposure": _rel(paths.risk_exposure),
            "gate_matrix": _rel(paths.gate_matrix), "next_plan": _rel(paths.next_plan),
        },
        "safety": {
            "research_only": True, "is_paper_trader": False, "is_production": False,
            "is_deployment": False, "live_data_used": False, "network_used": False,
            "paid_api_used": False, "packages_installed": False, "new_data_collected": False,
            "factor_weight_optimization": False, "new_alpha_factors": False,
            "failed_composite_used_as_candidate": False, "factor_sign_flipping": False,
            "regime_selection_or_weighting": False, "leverage_used": False,
            "wrote_to_data_drive": False, "trading_order_automation": False,
            "paper_trader_gcp_broker_touched": False, "committed": False, "pushed": False,
        },
    }


def _error_report(msg: str) -> Dict:
    return {"phase": PHASE, "recommendation": REC_ERROR,
            "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
            "momentum_viable_for_paper_research": "NO", "error": msg}


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
def _load_phase7j_baseline_ic() -> Optional[float]:
    try:
        with open(PHASE7J_REPORT, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d.get("results", {}).get("broad_price_only_baseline_ic")
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None


def _run(paths: _OutPaths) -> Dict:
    # ---- Universes + prices. ----
    uni128, prices128, sector_map = load_universe_128()
    uni296, prices296, sector_map296 = load_universe_296()

    universes = [
        ("u128", uni128, prices128, sector_map, PRICE_CSV_128),
        ("u296", uni296, prices296, sector_map296, BROAD_PRICE_PANEL_CSV),
    ]

    all_results: List[_StrategyResult] = []
    regime_block: Dict[str, Dict] = {}
    sector_blocks: Dict[str, Dict] = {}
    # Benchmarks are computed per universe; we report the broad-universe benchmarks as the
    # headline (widest, least survivorship-narrow), and use them for the decision.
    headline_spy: Dict = {}
    headline_eqw: Dict = {}
    span = {"start": None, "end": None}
    universe_sizes: Dict[str, int] = {}

    for uname, universe, prices, smap, price_csv in universes:
        universe_sizes[uname] = len(set(prices["ticker"].unique()))
        _pf, realized, variant_z = build_momentum_scores(prices)
        results = evaluate_strategies(uname, realized, variant_z)
        all_results.extend(results)

        # Benchmarks aligned to this universe's strategy month span.
        all_keys = sorted({k for r in results for k in r.sim["period_keys"]})
        if all_keys:
            span["start"] = all_keys[0] if span["start"] is None else min(span["start"], all_keys[0])
            span["end"] = all_keys[-1] if span["end"] is None else max(span["end"], all_keys[-1])
        eqw_sim = simulate_equal_weight_universe(realized)
        # Restrict the eqw benchmark to the strategy month span for a fair comparison.
        eqw_keys = [k for k in eqw_sim["period_keys"] if k in set(all_keys)] if all_keys else eqw_sim["period_keys"]
        eqw_gross = [g for k, g in zip(eqw_sim["period_keys"], eqw_sim["gross"]) if k in set(eqw_keys)]
        eqw_traded = [t for k, t in zip(eqw_sim["period_keys"], eqw_sim["traded"]) if k in set(eqw_keys)]
        eqw_one_sided = [t for k, t in zip(eqw_sim["period_keys"], eqw_sim["one_sided_turnover"]) if k in set(eqw_keys)]
        eqw_metrics = perf_metrics(eqw_gross)
        spy_bench = load_spy_benchmark(price_csv, all_keys)
        spy_metrics = perf_metrics(spy_bench["gross"])

        if uname == "u296":
            headline_spy, headline_eqw = spy_metrics, eqw_metrics
            headline_spy_bench, headline_eqw_bench = spy_bench, {
                "period_keys": eqw_keys, "gross": eqw_gross}
            headline_eqw_turnover = float(np.mean(eqw_one_sided)) if eqw_one_sided else None
            headline_eqw_names = float(np.mean(eqw_sim["n_selected"])) if eqw_sim["n_selected"] else None

        # Regime diagnostics for this universe's strategies.
        axis_labels = regime_labels_for_panel(price_csv)
        for r in results:
            reg_by_axis = {ax: returns_by_regime(r.sim["period_keys"], r.sim["gross"], labels)
                           for ax, labels in axis_labels.items()}
            regime_block[r.config_id] = reg_by_axis
            sector_blocks[r.config_id] = sector_exposure(r.sim["weights_by_month"], smap)

    # ---- Decision (judged on the broad-universe benchmarks; least survivorship-narrow). ----
    best = pick_best(all_results)
    recommendation, viable, detail = derive_recommendation(best, headline_spy, headline_eqw, all_results)

    # ---- Gates. ----
    gates, gate_counts = build_gate_matrix(best, detail, headline_spy, headline_eqw, len(all_results))

    # ---- Artifacts. ----
    _write_scoreboard(paths, all_results, headline_spy, headline_eqw,
                      headline_eqw_turnover, headline_eqw_names)
    _write_cost_sensitivity(paths, all_results)
    _write_turnover(paths, all_results)
    _write_drawdown(paths, all_results, headline_spy, headline_eqw)
    _write_yearly(paths, all_results, headline_spy_bench, headline_eqw_bench,
                  headline_spy, headline_eqw)
    _write_regime(paths, regime_block)
    _write_risk_exposure(paths, all_results, sector_blocks)
    _write_gate_matrix(paths, gates)
    _write_next_plan(paths, recommendation, viable, detail)

    phase7j_baseline_ic = _load_phase7j_baseline_ic()
    report = _assemble_report(paths, all_results, best, detail, recommendation, viable,
                              headline_spy, headline_eqw, phase7j_baseline_ic, gate_counts,
                              universe_sizes, span)
    _write_json(paths.report, report)
    return report


def run(out_dir: Optional[str] = None) -> Dict:
    paths = _OutPaths(base=Path(out_dir) if out_dir else _OUT_DIR_DEFAULT)
    try:
        return _run(paths)
    except Exception as exc:  # noqa: BLE001 — record, never raise out of the runner
        import traceback
        report = _error_report("%s: %s\n%s" % (type(exc).__name__, exc, traceback.format_exc()))
        _write_json(paths.report, report)
        return report


def _print_summary(report: Dict) -> None:
    print("[%s] recommendation = %s" % (PHASE, report.get("recommendation")))
    print("  momentum viable for paper research : %s" % report.get("momentum_viable_for_paper_research"))
    if report.get("error"):
        print("  ERROR: %s" % str(report["error"]).splitlines()[0])
        return
    best = report.get("best_strategy") or {}
    d = report.get("decision", {})
    if best:
        print("  best config                        : %s" % best.get("config"))
        print("  best gross sharpe                  : %s" % best.get("gross", {}).get("sharpe"))
        print("  best net sharpe @ %sbps           : %s" % (PRIMARY_COST_BPS, d.get("net_sharpe_at_primary_cost")))
        print("  best net max drawdown              : %s" % d.get("net_max_drawdown"))
        print("  mean one-sided turnover            : %s" % d.get("mean_one_sided_turnover"))
    b = report.get("benchmarks", {})
    print("  SPY sharpe / eqw-univ sharpe       : %s / %s" % (
        b.get("spy", {}).get("sharpe"), b.get("equal_weight_universe", {}).get("sharpe")))
    gc = report.get("gate_matrix_summary", {})
    if gc:
        print("  gates PASS=%s FAIL=%s WARN=%s safety_fail=%s" % (
            gc.get("pass"), gc.get("fail"), gc.get("warn"), gc.get("safety_fail")))


def _parse_args(argv):
    ap = argparse.ArgumentParser(description="Phase 7-L momentum/risk strategy viability test (offline).")
    ap.add_argument("--out-dir", default=None, help="output directory for committed-safe artifacts")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    report = run(out_dir=args.out_dir)
    _print_summary(report)
    return 0 if report.get("recommendation") in ALLOWED_RECOMMENDATIONS else 1


if __name__ == "__main__":
    raise SystemExit(main())
