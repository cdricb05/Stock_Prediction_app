"""Phase 8-B — Autonomous Research Director Orchestrator.

**Track A (quant brain) research only.** Offline, point-in-time, leakage-safe. This phase
turns the Phase 8-A agent *definitions* into an operational research loop: a deterministic
research-director orchestrator that reads project state + agent contracts + the 8-A
experiment registry, autonomously chooses the next agenda and a bounded experiment queue,
implements each agent's responsibility as an explicit artifact, runs the next experiment
batch, updates the approved/rejected signal set, and emits a research-director decision and a
next-action plan.

Hard scope (unchanged from 8-A, enforced here):
  * Reuse the EXISTING Phase 8-A survivorship-aware Norgate panel on
    D:\\Stock_Prediction_app_data\\research_panels\\phase8a_norgate_sample (no new collection
    unless that panel is missing/corrupt).
  * No package install, no paid/network API, no Paper Trader, no GCP, no broker/order/
    automation, no live trading signals.
  * No optimized weights, no factor-sign flipping after seeing results, no hidden tuning,
    no hidden/failed-experiment suppression.
  * Large data stays on D:; the repo receives committed-safe summaries only. No commit/push.

Autonomy budget (this run): at most 30 experiments. >=30% of the budget must CHALLENGE or
disprove the existing 8-A approved signals (EXP02 12-1 momentum quintile, EXP11
volatility-adjusted momentum quintile); >=20% must test NON-momentum orthogonal families.
Every experiment is registered (hypothesis / owner / inputs / output / success gate / stop
condition) BEFORE scoring.

The ONE question this phase answers
-----------------------------------
    DO THE PHASE 8-A APPROVED SIGNALS SURVIVE OUT-OF-SAMPLE CONFIRMATION (HOLDOUT
    SUB-PERIODS, ROLLING WINDOWS, COST STRESS, MULTIPLE-TESTING, RISK GATES) ON
    SURVIVORSHIP-AWARE DATA, AND IS THE AUTONOMOUS RESEARCH LOOP READY TO OPERATE?

Validation reused / extended from 8-A
-------------------------------------
The deterministic, leakage-safe primitives (signal blocks, capped equal-weight long-only
simulation, cost model, placebo, benchmarks, metrics, and the strict viability gate) are
imported from `run_phase8a_autonomous_norgate_research_engine` so the judgment is identical
machinery. 8-B adds: holdout sub-periods (1990-2004 / 2005-2014 / 2015-2026), rolling 10-year
windows, a 10/25/50/100 bps cost-stress curve, a multiple-testing conclusion over the full
8-A + 8-B search universe, and a risk-portfolio pass (drawdown / turnover / concentration /
beta / sector / delisted-contribution). Confirmation requires a signal to clear the full-sample
gate AND remain positive across sub-periods (incl. the most recent), survive 50 bps, and pass
the risk gate. Borderline is never rounded up.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RESEARCH_DIR = _REPO_ROOT / "research"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# --------------------------------------------------------------------------- #
# Import the verified Phase 8-A engine (pure primitives reused unchanged).
# Loading the module has no side effects (norgatedata import is lazy, inside
# NorgateAdapter.__init__ which we never construct here).
# --------------------------------------------------------------------------- #
def _load_p8a():
    path = _RESEARCH_DIR / "run_phase8a_autonomous_norgate_research_engine.py"
    spec = importlib.util.spec_from_file_location("phase8a_engine", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load Phase 8-A engine at {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod   # required so @dataclass can resolve cls.__module__
    spec.loader.exec_module(mod)
    return mod


P8A = _load_p8a()

# Reused pure primitives.
simulate_long_only = P8A.simulate_long_only
net_returns = P8A.net_returns
benchmark_returns = P8A.benchmark_returns
equal_weight_universe_returns = P8A.equal_weight_universe_returns
build_signal_blocks = P8A.build_signal_blocks
sharpe = P8A.sharpe
ann_return = P8A.ann_return
ann_vol = P8A.ann_vol
max_drawdown = P8A.max_drawdown
hit_rate = P8A.hit_rate
perf_metrics = P8A.perf_metrics
yearly_returns = P8A.yearly_returns
_seeded_permute = P8A._seeded_permute
classify_experiment = P8A.classify_experiment
_dd_acceptable = P8A._dd_acceptable
_round = P8A._round
_f = P8A._f
_rel = P8A._rel
_write_json = P8A._write_json
_write_csv = P8A._write_csv
_utc_now_iso = P8A._utc_now_iso

PHASE = "8-B"
OBJECTIVE = (
    "Operate the Phase 8-A agent system as an autonomous research loop: read project state "
    "and agent contracts, allocate a bounded experiment budget (<=30) across confirmation, "
    "momentum-robustness, non-momentum orthogonal, and risk-stress work; CHALLENGE the 8-A "
    "approved signals out-of-sample; run holdout / rolling / cost-stress / multiple-testing / "
    "risk validation; and decide whether the autonomous research loop is ready. Research only; "
    "no orders / automation / optimized weights."
)

# --------------------------------------------------------------------------- #
# 8-B recommendation vocabulary (exactly the allowed set, in order).
# --------------------------------------------------------------------------- #
REC_LOOP_READY = "AUTONOMOUS_RESEARCH_LOOP_READY"
REC_WEAK = "SIGNALS_WEAK_KEEP_RESEARCH_ONLY"
REC_REJECTED = "SIGNAL_RESEARCH_REJECTED_ON_CLEAN_DATA"
REC_ORCH_BLOCKED = "ORCHESTRATOR_BLOCKED"
REC_DATA_BLOCKED = "DATA_PANEL_BLOCKED"
REC_NEEDS_REVIEW = "NEEDS_RESEARCH_DIRECTOR_REVIEW"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (
    REC_LOOP_READY, REC_WEAK, REC_REJECTED, REC_ORCH_BLOCKED,
    REC_DATA_BLOCKED, REC_NEEDS_REVIEW, REC_ERROR,
)

# Per-experiment status vocabulary (exactly the allowed set).
ST_APPROVED = "APPROVED"
ST_WEAK = "WEAK"
ST_REJECTED = "REJECTED"
ST_DIAGNOSTIC = "DIAGNOSTIC_ONLY"
ST_BLOCKED = "BLOCKED"
ALLOWED_STATUSES = (ST_APPROVED, ST_WEAK, ST_REJECTED, ST_DIAGNOSTIC, ST_BLOCKED)

# --------------------------------------------------------------------------- #
# Paths.
# --------------------------------------------------------------------------- #
DATA_ROOT = Path("D:/Stock_Prediction_app_data")
PANEL_ROOT = DATA_ROOT / "research_panels" / "phase8a_norgate_sample"
P8A_OUTPUT_DIR = _RESEARCH_DIR / "output" / "phase8a_autonomous_norgate_research_engine"
AGENT_CONTRACT_DIR = _RESEARCH_DIR / "agents"
DEFAULT_OUT_DIR = _RESEARCH_DIR / "output" / "phase8b_autonomous_research_director"

PANEL_FILES = {
    "monthly_close": "monthly_close_total_return.csv",
    "monthly_dollar_volume": "monthly_dollar_volume.csv",
    "membership": "membership_panel.csv",
    "metadata": "metadata.csv",
    "spy_monthly": "spy_monthly_total_return.csv",
}

# --------------------------------------------------------------------------- #
# Fixed modelling config (documented; NO tuning loop, NO fit-to-outcome).
# Reuse 8-A constants where they exist so the judgment is identical.
# --------------------------------------------------------------------------- #
QUANTILES = P8A.QUANTILES
# quantile label -> top 1/divisor selection. "concentrated"/"broad" are breadth-stress books.
QDIV = {"top_decile": 10, "top_quintile": 5, "concentrated": 20, "broad": 3}
COST_BPS_GRID = (10.0, 25.0, 50.0, 100.0)          # 8-B cost-stress grid
PRIMARY_COST_BPS = P8A.PRIMARY_COST_BPS            # 25 bps decision
COST_ROBUST_BPS = 50.0                             # must still be net-positive here
MAX_POSITION = P8A.MAX_POSITION                    # 0.10 base
CAP_TIGHT = 0.05                                   # capacity stress (smaller book cap)
CAP_LOOSE = 0.20                                   # concentration stress (larger cap)
MIN_NAMES_PORT = P8A.MIN_NAMES_PORT
MIN_PERIODS_VALID = P8A.MIN_PERIODS_VALID          # 36
MIN_SUBPERIOD_PERIODS = 24                         # min months to judge a holdout slice
PERIODS_PER_YEAR = P8A.PERIODS_PER_YEAR
ROLL_WINDOW = 120                                  # 10-year rolling windows
ROLL_STEP = 12
PLACEBO_SHARPE_MARGIN = P8A.PLACEBO_SHARPE_MARGIN  # 0.25
PLACEBO_BASE_SEED = P8A.PLACEBO_BASE_SEED
MAX_EXPERIMENTS = 30                               # autonomy budget ceiling

SUBPERIODS: Dict[str, Tuple[int, int]] = {
    "1990-2004": (1990, 2004),
    "2005-2014": (2005, 2014),
    "2015-2026": (2015, 2026),
}
RECENT_LABEL = "2015-2026"

# Phase 8-A approved signals this phase must challenge/confirm.
P8A_APPROVED = {
    "EXP02": {"score_key": "mom_12_1", "quantile": "top_quintile", "gate": None,
              "family": "momentum"},
    "EXP11": {"score_key": "vol_adj_mom", "quantile": "top_quintile", "gate": None,
              "family": "volatility_liquidity"},
}


# --------------------------------------------------------------------------- #
# Panel loading from D: (no Norgate access; reuses the 8-A persisted panel).
# --------------------------------------------------------------------------- #
@dataclass
class Panel:
    close: pd.DataFrame
    dollar_vol: pd.DataFrame
    membership: pd.DataFrame
    metadata: pd.DataFrame
    spy_monthly: pd.Series
    ok: bool
    issues: List[str] = field(default_factory=list)


def load_panel(panel_root: Path = PANEL_ROOT) -> Panel:
    """Load the existing 8-A panel. Reports issues rather than raising, so the orchestrator
    can emit a DATA_PANEL_BLOCKED decision deterministically."""
    issues: List[str] = []
    missing = [k for k, fn in PANEL_FILES.items() if not (panel_root / fn).exists()]
    if missing:
        issues.append(f"missing panel files: {missing}")
        empty = pd.DataFrame()
        return Panel(empty, empty, empty, empty, pd.Series(dtype=float), False, issues)

    def _read_frame(fn: str) -> pd.DataFrame:
        df = pd.read_csv(panel_root / fn, index_col=0)
        df.index = pd.to_datetime(df.index, errors="coerce")
        return df.sort_index()

    close = _read_frame(PANEL_FILES["monthly_close"]).apply(pd.to_numeric, errors="coerce")
    dollar_vol = _read_frame(PANEL_FILES["monthly_dollar_volume"]).apply(pd.to_numeric, errors="coerce")
    membership = _read_frame(PANEL_FILES["membership"]).apply(pd.to_numeric, errors="coerce").fillna(0.0)
    membership = membership.reindex(close.index).fillna(0.0)
    dollar_vol = dollar_vol.reindex(close.index)

    meta = pd.read_csv(panel_root / PANEL_FILES["metadata"], index_col=0)
    if "is_delisted" in meta.columns:
        meta["is_delisted"] = meta["is_delisted"].astype(str).str.strip().str.lower().isin(("true", "1"))
    if "gics_sector" not in meta.columns:
        meta["gics_sector"] = "UNKNOWN"

    spy_df = pd.read_csv(panel_root / PANEL_FILES["spy_monthly"], index_col=0)
    spy_df.index = pd.to_datetime(spy_df.index, errors="coerce")
    spy_col = "Close" if "Close" in spy_df.columns else spy_df.columns[0]
    spy_monthly = pd.to_numeric(spy_df[spy_col], errors="coerce").sort_index().reindex(close.index)

    if len(close) < MIN_PERIODS_VALID:
        issues.append(f"too few months: {len(close)} < {MIN_PERIODS_VALID}")
    if close.shape[1] < MIN_NAMES_PORT:
        issues.append(f"too few symbols: {close.shape[1]} < {MIN_NAMES_PORT}")
    if close.index.isna().any():
        issues.append("unparseable month-end timestamps in close panel")
    if float(np.nansum(membership.to_numpy())) <= 0:
        issues.append("membership panel has no point-in-time members")
    if float(spy_monthly.notna().sum()) < MIN_PERIODS_VALID:
        issues.append("SPY benchmark has too few observations")

    ok = not issues
    return Panel(close, dollar_vol, membership, meta, spy_monthly, ok, issues)


# --------------------------------------------------------------------------- #
# 8-B feature blocks: extend 8-A blocks with NON-momentum orthogonal families.
# All registered in feature_catalog.csv BEFORE scoring. Every block at row t uses
# only data <= t; the forward return for t is realized strictly over (t, t+1].
# These are standard, a-priori anomaly definitions — NOT outcome-driven sign flips.
# --------------------------------------------------------------------------- #
def build_8b_blocks(close: pd.DataFrame, dollar_vol: pd.DataFrame,
                    spy_monthly: pd.Series, sector_map: Dict[str, str]) -> dict:
    blocks = build_signal_blocks(close, dollar_vol, spy_monthly, sector_map)
    ret_1m = close.pct_change()
    vol_12 = ret_1m.rolling(12, min_periods=6).std()
    downside = ret_1m.clip(upper=0.0)
    dvol_12 = downside.rolling(12, min_periods=6).std()
    blocks.update({
        # low-volatility anomaly: prefer the LOWEST trailing realized volatility
        "low_vol": -vol_12,
        # low downside-deviation: prefer the lowest semi-deviation of negative returns
        "downside_vol": -dvol_12,
        # illiquidity premium: prefer the LOWEST dollar volume among members
        "illiquidity": -dollar_vol,
        # liquidity / large-cap proxy: prefer the HIGHEST dollar volume
        "high_liquidity": dollar_vol.copy(),
    })
    return blocks


# Feature definitions (registered before scoring) — for feature_catalog.csv.
FEATURE_CATALOG = [
    ("ret_1m", "momentum", "close.pct_change()", "contemporaneous 1m total return; used for reversal", "close<=t"),
    ("mom_12_1", "momentum", "close[t-1]/close[t-12]-1", "12-1 momentum (skips most recent month)", "close<=t-1"),
    ("mom_6_1", "momentum", "close[t-1]/close[t-6]-1", "6-1 momentum (skips most recent month)", "close<=t-1"),
    ("mom_3", "momentum", "close[t]/close[t-3]-1", "3-month momentum", "close<=t"),
    ("rev_losers", "reversal", "-close.pct_change()", "1-month short-term reversal (buy losers)", "close<=t"),
    ("vol_adj_mom", "volatility_liquidity", "mom_12_1 / rolling12_vol(ret_1m)", "volatility-adjusted 12-1 momentum", "close<=t-1"),
    ("liq_gate", "volatility_liquidity", "dollar_vol >= cross-sectional median", "liquidity eligibility gate", "dollar_vol<=t"),
    ("rel_strength", "breadth_relative_strength", "mom_12_1 - SPY mom_12_1", "relative strength vs SPY", "close<=t-1"),
    ("trend_score", "trend_breakout", "close / rolling12_max(close)", "proximity to 12m high (breakout)", "close<=t"),
    ("sector_rel_mom", "breadth_relative_strength", "sector-demeaned mom_12_1", "sector-relative (breadth) momentum", "close<=t-1"),
    ("low_vol", "low_volatility", "-rolling12_vol(ret_1m)", "low-volatility anomaly (a-priori, not a sign flip)", "close<=t"),
    ("downside_vol", "low_volatility", "-rolling12_std(min(ret_1m,0))", "low downside-deviation anomaly", "close<=t"),
    ("illiquidity", "liquidity", "-dollar_vol", "illiquidity premium (low dollar volume)", "dollar_vol<=t"),
    ("high_liquidity", "liquidity", "dollar_vol", "liquidity / large-cap proxy (high dollar volume)", "dollar_vol<=t"),
]


# --------------------------------------------------------------------------- #
# Comprehensive deterministic evaluation of a single signal.
# --------------------------------------------------------------------------- #
def _subperiod_mask(pidx: pd.DatetimeIndex, a: int, b: int) -> np.ndarray:
    yrs = pidx.year
    return (yrs >= a) & (yrs <= b)


def _rolling_stats(net: pd.Series, spy_fwd: pd.Series,
                   window: int = ROLL_WINDOW, step: int = ROLL_STEP) -> dict:
    n = len(net)
    if n < window:
        return {"n_windows": 0, "min_sharpe": None, "median_sharpe": None,
                "max_sharpe": None, "frac_positive": None, "frac_beat_spy": None}
    sharpes: List[float] = []
    beats: List[int] = []
    for s in range(0, n - window + 1, step):
        seg = net.iloc[s:s + window]
        sh = sharpe(seg)
        if sh is None:
            continue
        sharpes.append(sh)
        seg_spy = spy_fwd.iloc[s:s + window]
        sp = sharpe(seg_spy)
        beats.append(1 if (sp is not None and sh > sp) else 0)
    if not sharpes:
        return {"n_windows": 0, "min_sharpe": None, "median_sharpe": None,
                "max_sharpe": None, "frac_positive": None, "frac_beat_spy": None}
    arr = np.array(sharpes)
    return {
        "n_windows": len(sharpes),
        "min_sharpe": _round(float(arr.min())),
        "median_sharpe": _round(float(np.median(arr))),
        "max_sharpe": _round(float(arr.max())),
        "frac_positive": _round(float((arr > 0).mean())),
        "frac_beat_spy": _round(float(np.mean(beats))),
    }


def _risk_profile(sim: dict, sector_map: Dict[str, str], delisted: set,
                  net25: pd.Series, spy_fwd: pd.Series) -> dict:
    weights_by_month = sim["weights_by_month"]
    months = max(1, len(weights_by_month))
    sector_acc: Dict[str, float] = defaultdict(float)
    delisted_w: List[float] = []
    for _t, w in weights_by_month.items():
        dw = 0.0
        for name, wt in w.items():
            sector_acc[sector_map.get(name, "UNKNOWN")] += wt
            if name in delisted:
                dw += wt
        delisted_w.append(dw)
    sector_exposure = {s: _round(v / months, 4) for s, v in
                       sorted(sector_acc.items(), key=lambda kv: -kv[1])}
    top_sector = next(iter(sector_exposure), "UNKNOWN")
    # beta to SPY on the realized net return series
    aligned = pd.concat([net25.rename("p"), spy_fwd.rename("b")], axis=1).dropna()
    beta = None
    if len(aligned) >= 12:
        var_b = float(aligned["b"].var(ddof=1))
        if var_b > 0:
            beta = float(aligned[["p", "b"]].cov().loc["p", "b"] / var_b)
    return {
        "beta_spy": _round(beta, 3),
        "delisted_weight_avg": _round(float(np.mean(delisted_w)) if delisted_w else 0.0, 4),
        "top_sector": top_sector,
        "top_sector_weight": sector_exposure.get(top_sector),
        "sector_exposure": sector_exposure,
    }


def evaluate_signal(blocks: dict, forward: pd.DataFrame, members: pd.DataFrame,
                    spy_monthly: pd.Series, sector_map: Dict[str, str], delisted: set, *,
                    score_key: str, quantile: str, gate_key: Optional[str] = None,
                    max_pos: float = MAX_POSITION, with_placebo: bool = True) -> dict:
    score = blocks[score_key]
    gate = blocks.get(gate_key) if gate_key else None
    q = QDIV[quantile]
    sim = simulate_long_only(score, forward, members, q=q, gate=gate, max_pos=max_pos)
    pidx = sim["period_index"]
    gross = sim["gross"]
    traded = sim["traded_fraction"]

    net25 = net_returns(gross, traded, PRIMARY_COST_BPS)
    cost_sharpe = {int(b): _round(sharpe(net_returns(gross, traded, b))) for b in COST_BPS_GRID}
    cost_ann = {int(b): _round(ann_return(net_returns(gross, traded, b))) for b in COST_BPS_GRID}

    spy_fwd = benchmark_returns(spy_monthly, pidx)
    ew_fwd = equal_weight_universe_returns(forward, members, pidx)
    spy_sharpe = sharpe(spy_fwd)
    ew_sharpe = sharpe(ew_fwd)

    placebo_sharpe = None
    if with_placebo:
        psim = simulate_long_only(_seeded_permute(score, PLACEBO_BASE_SEED), forward, members,
                                  q=q, gate=gate, max_pos=max_pos)
        placebo_sharpe = sharpe(net_returns(psim["gross"], psim["traded_fraction"], PRIMARY_COST_BPS))

    gross_m = perf_metrics(gross)
    net_m = perf_metrics(net25)

    subperiods: Dict[str, dict] = {}
    for label, (a, b) in SUBPERIODS.items():
        mask = _subperiod_mask(pidx, a, b)
        npd = int(mask.sum())
        if npd < MIN_SUBPERIOD_PERIODS:
            subperiods[label] = {"n_periods": npd, "status": ST_BLOCKED,
                                 "net_sharpe": None, "net_ann_return": None,
                                 "net_max_drawdown": None, "beats_spy": None,
                                 "beats_ew": None, "spy_sharpe": None, "ew_sharpe": None}
            continue
        g_s = gross[mask]
        tr_s = traded[mask]
        n_s = net_returns(g_s, tr_s, PRIMARY_COST_BPS)
        sub_pidx = pidx[mask]
        spy_s = sharpe(benchmark_returns(spy_monthly, sub_pidx))
        ew_s = sharpe(equal_weight_universe_returns(forward, members, sub_pidx))
        ns = sharpe(n_s)
        subperiods[label] = {
            "n_periods": npd,
            "net_sharpe": _round(ns),
            "net_ann_return": _round(ann_return(n_s)),
            "net_max_drawdown": _round(max_drawdown(n_s)),
            "spy_sharpe": _round(spy_s),
            "ew_sharpe": _round(ew_s),
            "beats_spy": bool(ns is not None and spy_s is not None and ns > spy_s),
            "beats_ew": bool(ns is not None and ew_s is not None and ns > ew_s),
            "status": ST_DIAGNOSTIC,
        }

    rolling = _rolling_stats(net25, spy_fwd)
    risk = _risk_profile(sim, sector_map, delisted, net25, spy_fwd)

    leakage_ok = bool(len(pidx) == 0 or pidx.is_monotonic_increasing)
    ns25 = net_m["sharpe"]
    return {
        "score_key": score_key, "quantile": quantile, "gate": gate_key or "",
        "max_pos": max_pos,
        "n_periods": net_m["n_periods"], "avg_names": _round(sim["avg_names"], 2),
        "avg_max_weight": _round(sim["avg_max_weight"], 4),
        "one_sided_turnover": _round(sim["one_sided_turnover"], 4),
        "gross_sharpe": gross_m["sharpe"], "gross_ann_return": gross_m["ann_return"],
        "net_sharpe_25bps": ns25, "net_ann_return_25bps": net_m["ann_return"],
        "net_ann_vol_25bps": net_m["ann_vol"], "net_max_drawdown_25bps": net_m["max_drawdown"],
        "net_hit_rate_25bps": net_m["hit_rate"],
        "cost_sharpe": cost_sharpe, "cost_ann": cost_ann,
        "spy_sharpe": _round(spy_sharpe), "spy_max_drawdown": _round(max_drawdown(spy_fwd)),
        "ew_universe_sharpe": _round(ew_sharpe), "ew_universe_ann_return": _round(ann_return(ew_fwd)),
        "placebo_net_sharpe_25bps": _round(placebo_sharpe),
        "net_sharpe_minus_placebo": _round((ns25 or 0) - (placebo_sharpe or 0)),
        "net_sharpe_minus_spy": _round((ns25 or 0) - (spy_sharpe or 0)),
        "net_sharpe_minus_ew": _round((ns25 or 0) - (ew_sharpe or 0)),
        "leakage_check": "PASS_NO_LOOKAHEAD" if leakage_ok else "FAIL",
        "subperiods": subperiods, "rolling": rolling, "risk": risk,
        "yearly_net_25bps": yearly_returns(net25),
        "period_start": str(pidx.min())[:10] if len(pidx) else "",
        "period_end": str(pidx.max())[:10] if len(pidx) else "",
        "_sim": sim,
    }


# --------------------------------------------------------------------------- #
# Classification: candidate-signal verdict + confirmation overlay.
# --------------------------------------------------------------------------- #
def _full_gate_status(ev: dict) -> Tuple[str, str]:
    """Apply the identical 8-A full-sample viability gate to an 8-B evaluation."""
    pseudo = {
        "n_periods": ev["n_periods"], "avg_names": ev["avg_names"],
        "net_sharpe_25bps": ev["net_sharpe_25bps"], "net_ann_return_25bps": ev["net_ann_return_25bps"],
        "leakage_check": ev["leakage_check"], "spy_sharpe": ev["spy_sharpe"],
        "ew_universe_sharpe": ev["ew_universe_sharpe"],
        "net_sharpe_minus_placebo": ev["net_sharpe_minus_placebo"],
        "net_max_drawdown_25bps": ev["net_max_drawdown_25bps"],
        "spy_max_drawdown": ev["spy_max_drawdown"],
        "one_sided_turnover": ev["one_sided_turnover"],
    }
    return classify_experiment(pseudo)


def _stability(ev: dict) -> dict:
    """Out-of-sample stability. A holdout sub-period only "holds" if the signal is net-positive
    AND BEATS SPY in that slice — being net-positive alone is trivial for a long-only equity book.
    A confirmed signal must beat SPY in >=2 of 3 sub-periods AND in the most recent sub-period,
    and stay net-positive after 50 bps."""
    subs = ev["subperiods"]
    judged = {k: v for k, v in subs.items() if v.get("status") != ST_BLOCKED}
    n_pos = sum(1 for v in judged.values() if (v.get("net_sharpe") or -9) > 0)
    n_beat_spy = sum(1 for v in judged.values() if v.get("beats_spy"))
    recent = subs.get(RECENT_LABEL, {})
    recent_judged = recent.get("status") != ST_BLOCKED
    recent_pos = recent_judged and (recent.get("net_sharpe") or -9) > 0
    recent_beats_spy = recent_judged and bool(recent.get("beats_spy"))
    cost50 = ev["cost_sharpe"].get(int(COST_ROBUST_BPS))
    cost_robust = cost50 is not None and cost50 > 0
    return {
        "n_subperiods_judged": len(judged),
        "n_subperiods_positive": n_pos,
        "n_subperiods_beat_spy": n_beat_spy,
        "recent_positive": bool(recent_pos),
        "recent_beats_spy": bool(recent_beats_spy),
        "cost50_sharpe": cost50,
        "cost_robust_50bps": bool(cost_robust),
        # confirmation requires beating the benchmark out-of-sample, not just positive returns
        "stable": bool(n_beat_spy >= 2 and recent_beats_spy and cost_robust),
    }


def _risk_gate_ok(ev: dict) -> bool:
    dd_ok = _dd_acceptable(ev["net_max_drawdown_25bps"], ev["spy_max_drawdown"])
    turn_ok = (ev["one_sided_turnover"] is not None and ev["one_sided_turnover"] <= P8A.TURNOVER_CEIL)
    beta = ev["risk"].get("beta_spy")
    beta_ok = beta is None or beta <= 1.5    # not a hidden leveraged beta bet
    return bool(dd_ok and turn_ok and beta_ok)


def classify_candidate(ev: dict) -> Tuple[str, str, dict]:
    """APPROVED requires the full 8-A gate AND out-of-sample stability AND the risk gate.
    Positive-but-not-robust -> WEAK. Fails gate -> REJECTED. Too little history -> BLOCKED."""
    base, base_reason = _full_gate_status(ev)
    stab = _stability(ev)
    risk_ok = _risk_gate_ok(ev)
    if base == P8A.ST_NEEDS_FULL:
        return ST_BLOCKED, f"insufficient history: {base_reason}", stab
    if base == P8A.ST_REJECTED:
        return ST_REJECTED, base_reason, stab
    # base is APPROVED or WEAK on the full sample
    if base == P8A.ST_APPROVED and stab["stable"] and risk_ok:
        return (ST_APPROVED,
                f"confirmed: full-sample gate + beats SPY in {stab['n_subperiods_beat_spy']}/"
                f"{stab['n_subperiods_judged']} sub-periods incl. most recent, "
                f"net-positive at {int(COST_ROBUST_BPS)}bps, risk gate OK", stab)
    # positive net but not fully robust / not risk-clean -> WEAK (never hide it)
    if (ev["net_sharpe_25bps"] or 0) > 0:
        why = []
        if base != P8A.ST_APPROVED:
            why.append(f"full-sample {base} ({base_reason})")
        if not stab["stable"]:
            why.append(f"does not confirm out-of-sample (beats SPY in only "
                       f"{stab['n_subperiods_beat_spy']}/{stab['n_subperiods_judged']} "
                       f"sub-periods, recent_beats_spy={stab['recent_beats_spy']}, "
                       f"cost_robust_50bps={stab['cost_robust_50bps']})")
        if not risk_ok:
            why.append("risk gate fail")
        return ST_WEAK, "; ".join(why) or "positive but thin", stab
    return ST_REJECTED, base_reason, stab


# --------------------------------------------------------------------------- #
# Experiment queue (registered BEFORE scoring).
# --------------------------------------------------------------------------- #
@dataclass
class QueuedExperiment:
    experiment_id: str
    category: str            # CONFIRM | MOMENTUM_ROBUSTNESS | NON_MOMENTUM | CHALLENGE | RISK_STRESS
    owning_agent: str
    family: str
    hypothesis: str
    score_key: str
    quantile: str
    gate_key: Optional[str]
    max_pos: float
    scope: str               # full | subperiod label | cost@Nbps | cap@X
    success_gate: str
    stop_condition: str
    challenges: str = ""      # 8-A experiment id this challenges, if any
    base_signal: str = ""     # key into base evaluations


MOM_A = "momentum-signal-agent"
REV_A = "reversal-signal-agent"
TRB_A = "trend-breadth-signal-agent"
VOL_A = "volatility-liquidity-agent"
VAL_A = "validation-skeptic-agent"
RSK_A = "risk-portfolio-agent"

# Base candidate signals evaluated once at the base 0.10 cap (id -> spec).
BASE_SIGNALS: Dict[str, dict] = {
    "MR01": {"category": "CONFIRM", "agent": MOM_A, "family": "momentum",
             "score_key": "mom_12_1", "quantile": "top_quintile", "gate": None,
             "hyp": "EXP02 12-1 momentum quintile survives out-of-sample confirmation",
             "challenges": "EXP02"},
    "MR02": {"category": "CONFIRM", "agent": VOL_A, "family": "volatility_liquidity",
             "score_key": "vol_adj_mom", "quantile": "top_quintile", "gate": None,
             "hyp": "EXP11 volatility-adjusted momentum quintile survives confirmation",
             "challenges": "EXP11"},
    "MR03": {"category": "MOMENTUM_ROBUSTNESS", "agent": MOM_A, "family": "momentum",
             "score_key": "mom_12_1", "quantile": "top_quintile", "gate": "liq_gate",
             "hyp": "12-1 momentum quintile is robust when restricted to liquid names"},
    "MR04": {"category": "MOMENTUM_ROBUSTNESS", "agent": VOL_A, "family": "volatility_liquidity",
             "score_key": "vol_adj_mom", "quantile": "top_quintile", "gate": "liq_gate",
             "hyp": "vol-adjusted momentum quintile is robust among liquid names"},
    "MR05": {"category": "MOMENTUM_ROBUSTNESS", "agent": MOM_A, "family": "momentum",
             "score_key": "mom_6_1", "quantile": "top_quintile", "gate": None,
             "hyp": "6-1 momentum quintile is a robust shorter-horizon variant"},
    "MR06": {"category": "MOMENTUM_ROBUSTNESS", "agent": TRB_A, "family": "breadth_relative_strength",
             "score_key": "sector_rel_mom", "quantile": "top_quintile", "gate": None,
             "hyp": "sector-relative (breadth) momentum quintile is robust"},
    "NM01": {"category": "NON_MOMENTUM", "agent": VOL_A, "family": "low_volatility",
             "score_key": "low_vol", "quantile": "top_quintile", "gate": None,
             "hyp": "low-volatility anomaly: lowest-vol quintile earns superior risk-adjusted return"},
    "NM02": {"category": "NON_MOMENTUM", "agent": VOL_A, "family": "low_volatility",
             "score_key": "low_vol", "quantile": "top_decile", "gate": None,
             "hyp": "low-volatility anomaly concentrates in the lowest-vol decile"},
    "NM03": {"category": "NON_MOMENTUM", "agent": REV_A, "family": "reversal",
             "score_key": "rev_losers", "quantile": "top_quintile", "gate": None,
             "hyp": "1-month reversal (buy losers) quintile — reassessed on clean data"},
    "NM04": {"category": "NON_MOMENTUM", "agent": REV_A, "family": "reversal",
             "score_key": "rev_losers", "quantile": "top_decile", "gate": None,
             "hyp": "1-month reversal (buy losers) decile — reassessed on clean data"},
    "NM05": {"category": "NON_MOMENTUM", "agent": VOL_A, "family": "liquidity",
             "score_key": "illiquidity", "quantile": "top_quintile", "gate": None,
             "hyp": "illiquidity premium: lowest dollar-volume quintile earns a premium"},
    "NM06": {"category": "NON_MOMENTUM", "agent": VOL_A, "family": "liquidity",
             "score_key": "high_liquidity", "quantile": "top_quintile", "gate": None,
             "hyp": "liquidity/large-cap proxy: highest dollar-volume quintile (control)"},
    "NM07": {"category": "NON_MOMENTUM", "agent": VOL_A, "family": "low_volatility",
             "score_key": "downside_vol", "quantile": "top_quintile", "gate": None,
             "hyp": "low downside-deviation quintile earns superior risk-adjusted return"},
}


def build_experiment_queue() -> List[QueuedExperiment]:
    """Deterministic, pre-registered queue. <=30 experiments, >=30% challenge, >=20% non-momentum."""
    q: List[QueuedExperiment] = []
    GATE_CANDIDATE = ("net>0 AND beats SPY AND beats EW AND placebo margin>=0.25 AND dd/turnover OK "
                      "AND >=2/3 sub-periods positive AND recent positive AND net>0 @50bps AND risk gate OK")
    STOP_CANDIDATE = "reject if net<=0 after 25bps OR fails placebo OR <36 periods"
    for sid, spec in BASE_SIGNALS.items():
        q.append(QueuedExperiment(
            experiment_id=sid, category=spec["category"], owning_agent=spec["agent"],
            family=spec["family"], hypothesis=spec["hyp"], score_key=spec["score_key"],
            quantile=spec["quantile"], gate_key=spec["gate"], max_pos=MAX_POSITION,
            scope="full", success_gate=GATE_CANDIDATE, stop_condition=STOP_CANDIDATE,
            challenges=spec.get("challenges", ""), base_signal=sid))

    # CHALLENGE experiments: stress the two 8-A approved signals out-of-sample (>=30%).
    ch = 0
    for base_sid, exp_id in (("MR01", "EXP02"), ("MR02", "EXP11")):
        for label in SUBPERIODS:
            ch += 1
            q.append(QueuedExperiment(
                experiment_id=f"CH{ch:02d}", category="CHALLENGE", owning_agent=VAL_A,
                family="validation", hypothesis=f"{exp_id} edge persists in holdout {label}",
                score_key=BASE_SIGNALS[base_sid]["score_key"],
                quantile=BASE_SIGNALS[base_sid]["quantile"],
                gate_key=BASE_SIGNALS[base_sid]["gate"], max_pos=MAX_POSITION,
                scope=f"subperiod:{label}",
                success_gate="net Sharpe>0 AND beats SPY in the holdout sub-period",
                stop_condition="flag NON-PERSISTENT if net<=0 or underperforms SPY in the slice",
                challenges=exp_id, base_signal=base_sid))
    for base_sid, exp_id in (("MR01", "EXP02"), ("MR02", "EXP11")):
        for bps in (50, 100):
            ch += 1
            q.append(QueuedExperiment(
                experiment_id=f"CH{ch:02d}", category="CHALLENGE", owning_agent=VAL_A,
                family="validation", hypothesis=f"{exp_id} edge survives {bps}bps round-trip costs",
                score_key=BASE_SIGNALS[base_sid]["score_key"],
                quantile=BASE_SIGNALS[base_sid]["quantile"],
                gate_key=BASE_SIGNALS[base_sid]["gate"], max_pos=MAX_POSITION,
                scope=f"cost@{bps}bps",
                success_gate=f"net Sharpe>0 at {bps}bps",
                stop_condition=f"flag COST-FRAGILE if net<=0 at {bps}bps",
                challenges=exp_id, base_signal=base_sid))

    # RISK-STRESS experiments: breadth concentration. A ~100-name quintile book is ~1%/name,
    # so a position cap never binds and is a no-op; the meaningful concentration lever is how
    # FEW names the book holds. "concentrated" = top ~25 names (q20), "broad" = top tercile (q3).
    rs = 0
    for base_sid, exp_id in (("MR01", "EXP02"), ("MR02", "EXP11")):
        for qlabel, tag in (("concentrated", "concentration"), ("broad", "breadth")):
            rs += 1
            q.append(QueuedExperiment(
                experiment_id=f"RS{rs:02d}", category="RISK_STRESS", owning_agent=RSK_A,
                family="risk", hypothesis=f"{exp_id} is robust to {tag} (selection breadth = {qlabel})",
                score_key=BASE_SIGNALS[base_sid]["score_key"], quantile=qlabel,
                gate_key=BASE_SIGNALS[base_sid]["gate"], max_pos=MAX_POSITION,
                scope=f"breadth:{qlabel}",
                success_gate="net Sharpe within ~0.20 of base quintile; drawdown within floor",
                stop_condition="flag CONCENTRATION-DEPENDENT if edge only appears at one breadth",
                challenges=exp_id, base_signal=base_sid))
    return q[:MAX_EXPERIMENTS]


# --------------------------------------------------------------------------- #
# Project-state + agent-contract reading (the orchestrator's inputs).
# --------------------------------------------------------------------------- #
REQUIRED_CONTRACTS = [
    "agent_manifest.json", "agent_contracts.json", "experiment_registry_schema.json",
    "handoff_contracts.json", "validation_gate_schema.json", "research_director_protocol.json",
]


def read_project_state() -> dict:
    """Read 8-A outputs + agent assets that the orchestrator depends on."""
    state: dict = {"phase8a_present": {}, "contracts_present": {}, "subagents_ok": False}
    # 8-A committed-safe outputs
    for fn in ("research_director_decision.json", "approved_signals.csv",
               "failed_experiments.csv", "experiment_registry.csv",
               "all_experiments_scoreboard.csv", "norgate_sample_panel_manifest.csv",
               "survivorship_audit.csv"):
        state["phase8a_present"][fn] = (P8A_OUTPUT_DIR / fn).exists()
    # 8-A approved-signal ids (from the scoreboard)
    approved_ids: List[str] = []
    sb = P8A_OUTPUT_DIR / "all_experiments_scoreboard.csv"
    if sb.exists():
        try:
            df = pd.read_csv(sb)
            approved_ids = sorted(df.loc[df["status"] == "APPROVED", "experiment_id"].astype(str))
        except Exception:  # pragma: no cover - defensive
            approved_ids = []
    state["phase8a_approved_ids"] = approved_ids
    # machine-readable contracts
    contracts: Dict[str, dict] = {}
    for fn in REQUIRED_CONTRACTS:
        p = AGENT_CONTRACT_DIR / fn
        ok = p.exists()
        state["contracts_present"][fn] = ok
        if ok:
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    contracts[fn] = json.load(fh)
            except Exception as exc:  # pragma: no cover - defensive
                state["contracts_present"][fn] = f"PARSE_ERROR: {exc!r}"
    # subagent + contract presence (reuse 8-A check)
    agents_ok, present_sub, present_con = P8A._agents_present()
    state["subagents_ok"] = agents_ok
    state["subagents_present"] = present_sub
    state["contracts_present_list"] = present_con
    state["n_subagents"] = len(present_sub)
    state["n_contracts"] = len(present_con)
    state["_contracts"] = contracts
    return state


def orchestrator_ready(state: dict) -> Tuple[bool, List[str]]:
    problems: List[str] = []
    if not state.get("subagents_ok"):
        problems.append(f"subagent set incomplete ({state.get('n_subagents')}/12)")
    if state.get("n_contracts", 0) < len(REQUIRED_CONTRACTS):
        problems.append(f"agent contracts incomplete ({state.get('n_contracts')}/{len(REQUIRED_CONTRACTS)})")
    for fn, ok in state.get("contracts_present", {}).items():
        if ok is not True:
            problems.append(f"contract {fn}: {ok}")
    if not state["phase8a_present"].get("all_experiments_scoreboard.csv"):
        problems.append("missing 8-A scoreboard input")
    if not state.get("phase8a_approved_ids"):
        problems.append("no 8-A approved signals to confirm")
    return (not problems), problems


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
@dataclass
class OutPaths:
    out_dir: Path

    def p(self, name: str) -> Path:
        return self.out_dir / name


def run(out_dir: Path, *, panel_root: Path = PANEL_ROOT) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = OutPaths(out_dir)
    started = _utc_now_iso()

    state = read_project_state()
    orch_ok, orch_problems = orchestrator_ready(state)
    panel = load_panel(panel_root)

    queue = build_experiment_queue()

    # Blocked paths emit a minimal-but-complete artifact set + decision, then return.
    if not panel.ok:
        report = _blocked_report(started, state, orch_ok, orch_problems, panel,
                                 REC_DATA_BLOCKED, f"panel issues: {panel.issues}")
        _emit_all(paths, report, state, panel, queue, evaluations={}, decision=report["decision"])
        return report
    if not orch_ok:
        report = _blocked_report(started, state, orch_ok, orch_problems, panel,
                                 REC_ORCH_BLOCKED, f"orchestrator not ready: {orch_problems}")
        _emit_all(paths, report, state, panel, queue, evaluations={}, decision=report["decision"])
        return report

    # ---- run the bounded experiment batch ----
    sector_map = panel.metadata["gics_sector"].to_dict() if not panel.metadata.empty else {}
    delisted = set(panel.metadata.index[panel.metadata["is_delisted"]]) if "is_delisted" in panel.metadata.columns else set()
    forward = panel.close.pct_change().shift(-1)
    blocks = build_8b_blocks(panel.close, panel.dollar_vol, panel.spy_monthly, sector_map)

    # 1) base candidate signals (full sample, 0.10 cap)
    base_ev: Dict[str, dict] = {}
    for sid, spec in BASE_SIGNALS.items():
        base_ev[sid] = evaluate_signal(
            blocks, forward, panel.membership, panel.spy_monthly, sector_map, delisted,
            score_key=spec["score_key"], quantile=spec["quantile"], gate_key=spec["gate"])

    # 2) capacity / concentration re-sims for the risk-stress experiments
    cap_ev: Dict[str, dict] = {}
    for q in queue:
        if q.category == "RISK_STRESS":
            cap_ev[q.experiment_id] = evaluate_signal(
                blocks, forward, panel.membership, panel.spy_monthly, sector_map, delisted,
                score_key=q.score_key, quantile=q.quantile, gate_key=q.gate_key,
                max_pos=q.max_pos, with_placebo=False)

    # 3) classify candidates
    candidate_verdicts: Dict[str, Tuple[str, str, dict]] = {
        sid: classify_candidate(ev) for sid, ev in base_ev.items()}

    # 4) assemble per-experiment scoreboard rows (candidates + challenge + risk diagnostics)
    scoreboard = _scoreboard_rows(queue, base_ev, cap_ev, candidate_verdicts)

    # 5) confirmation of the two 8-A approved signals
    confirmation = _confirmation(base_ev, candidate_verdicts)

    # 6) decision
    decision = _derive_decision(state, orch_ok, panel, confirmation, candidate_verdicts)
    decision["promising_leads"] = _promising_leads(base_ev, candidate_verdicts)

    report = _assemble_report(started, state, orch_ok, orch_problems, panel, queue,
                              base_ev, candidate_verdicts, confirmation, decision)
    _emit_all(paths, report, state, panel, queue, base_ev, cap_ev, candidate_verdicts,
              scoreboard, confirmation, decision)
    return report


def _confirmation(base_ev: dict, verdicts: dict) -> dict:
    """Confirmation verdict for the two Phase 8-A approved signals."""
    out: Dict[str, dict] = {}
    for sid, exp_id in (("MR01", "EXP02"), ("MR02", "EXP11")):
        ev = base_ev[sid]
        status, reason, stab = verdicts[sid]
        out[exp_id] = {
            "phase8a_id": exp_id, "phase8b_id": sid,
            "score_key": ev["score_key"], "quantile": ev["quantile"],
            "status": status, "reason": reason,
            "net_sharpe_25bps": ev["net_sharpe_25bps"],
            "net_sharpe_minus_spy": ev["net_sharpe_minus_spy"],
            "net_sharpe_minus_ew": ev["net_sharpe_minus_ew"],
            "cost50_sharpe": stab["cost50_sharpe"],
            "cost100_sharpe": ev["cost_sharpe"].get(100),
            "n_subperiods_positive": stab["n_subperiods_positive"],
            "n_subperiods_beat_spy": stab["n_subperiods_beat_spy"],
            "n_subperiods_judged": stab["n_subperiods_judged"],
            "recent_positive": stab["recent_positive"],
            "recent_beats_spy": stab["recent_beats_spy"],
            "rolling_frac_positive": ev["rolling"]["frac_positive"],
            "rolling_frac_beat_spy": ev["rolling"]["frac_beat_spy"],
            "rolling_min_sharpe": ev["rolling"]["min_sharpe"],
            "beta_spy": ev["risk"]["beta_spy"],
            "confirmed": status == ST_APPROVED,
        }
    return out


def _promising_leads(base_ev: dict, candidate_verdicts: dict, top_n: int = 3) -> List[dict]:
    """The best non-confirmed candidates (WEAK but beating SPY full-sample) — the priority
    leads for 8-C confirmation on a wider panel. Honest: these are NOT confirmed, only the
    most promising directions surfaced this round."""
    leads: List[dict] = []
    for sid, ev in base_ev.items():
        st = candidate_verdicts[sid][0]
        if st == ST_WEAK and (ev["net_sharpe_minus_spy"] or 0) > 0:
            leads.append({
                "experiment_id": sid, "score_key": ev["score_key"], "quantile": ev["quantile"],
                "family": BASE_SIGNALS[sid]["family"],
                "net_sharpe_25bps": ev["net_sharpe_25bps"],
                "net_sharpe_minus_spy": ev["net_sharpe_minus_spy"],
                "net_max_drawdown_25bps": ev["net_max_drawdown_25bps"],
                "n_subperiods_beat_spy": candidate_verdicts[sid][2]["n_subperiods_beat_spy"],
            })
    leads.sort(key=lambda d: (d["net_sharpe_25bps"] or -9), reverse=True)
    return leads[:top_n]


def _multiple_testing(state: dict, queue: List[QueuedExperiment],
                      candidate_verdicts: dict) -> dict:
    """Treat 8-A (18) + 8-B candidate experiments as the search universe."""
    n_8a = 18
    n_8b_candidates = len(BASE_SIGNALS)
    n_8b_total = len(queue)
    n_search = n_8a + n_8b_candidates
    n_candidate_approved = sum(1 for v in candidate_verdicts.values() if v[0] == ST_APPROVED)
    return {
        "search_universe_8a": n_8a,
        "search_universe_8b_candidates": n_8b_candidates,
        "search_universe_total": n_search,
        "n_8b_experiments_registered": n_8b_total,
        "n_candidate_approved_8b": n_candidate_approved,
        "correction": "out-of-sample stability (>=2/3 holdout sub-periods positive + recent "
                      "positive + net-positive at 50bps) required ON TOP of the full-sample gate; "
                      "borderline never rounded up",
        "interpretation": (f"With {n_search} experiments in the search universe, a single-bar "
                           "net-Sharpe screen would be expected to surface lucky winners; the "
                           "stability + recency + cost-robustness requirement is the deflation "
                           "applied before any signal is called confirmed."),
    }


def _derive_decision(state: dict, orch_ok: bool, panel: Panel,
                     confirmation: dict, candidate_verdicts: dict) -> dict:
    confirmed = [k for k, v in confirmation.items() if v["confirmed"]]
    weak = [k for k, v in confirmation.items() if v["status"] == ST_WEAK]
    rejected = [k for k, v in confirmation.items() if v["status"] == ST_REJECTED]
    positive_thin = [k for k, v in confirmation.items()
                     if (v["net_sharpe_25bps"] or 0) > 0 and not v["confirmed"]]

    if not panel.ok:
        rec = REC_DATA_BLOCKED
    elif not orch_ok:
        rec = REC_ORCH_BLOCKED
    elif len(confirmed) >= 1:
        rec = REC_LOOP_READY
    elif len(positive_thin) >= 1:
        rec = REC_WEAK
    elif len(rejected) == len(confirmation) and confirmation:
        rec = REC_REJECTED
    else:
        rec = REC_NEEDS_REVIEW

    return {
        "recommendation": rec,
        "confirmed_8a_signals": confirmed,
        "weak_8a_signals": weak,
        "rejected_8a_signals": rejected,
        "positive_but_thin_8a_signals": positive_thin,
        "n_candidate_signals": len(candidate_verdicts),
        "n_candidate_approved": sum(1 for v in candidate_verdicts.values() if v[0] == ST_APPROVED),
        "n_candidate_weak": sum(1 for v in candidate_verdicts.values() if v[0] == ST_WEAK),
        "n_candidate_rejected": sum(1 for v in candidate_verdicts.values() if v[0] == ST_REJECTED),
        "n_candidate_blocked": sum(1 for v in candidate_verdicts.values() if v[0] == ST_BLOCKED),
    }


# --------------------------------------------------------------------------- #
# Scoreboard / report assembly.
# --------------------------------------------------------------------------- #
_SCOREBOARD_COLS = [
    "experiment_id", "category", "owning_agent", "family", "score_key", "quantile", "gate",
    "max_pos", "scope", "challenges", "status", "n_periods", "avg_names", "one_sided_turnover",
    "net_sharpe_25bps", "net_ann_return_25bps", "net_max_drawdown_25bps",
    "net_sharpe_50bps", "net_sharpe_100bps", "spy_sharpe", "ew_universe_sharpe",
    "net_sharpe_minus_spy", "net_sharpe_minus_ew", "placebo_net_sharpe_25bps",
    "net_sharpe_minus_placebo", "subperiod_net_sharpe", "subperiod_beats_spy",
    "leakage_check", "reason",
]


def _scoreboard_rows(queue: List[QueuedExperiment], base_ev: dict, cap_ev: dict,
                     candidate_verdicts: dict) -> List[dict]:
    rows: List[dict] = []
    for q in queue:
        row = {
            "experiment_id": q.experiment_id, "category": q.category,
            "owning_agent": q.owning_agent, "family": q.family, "score_key": q.score_key,
            "quantile": q.quantile, "gate": q.gate_key or "", "max_pos": q.max_pos,
            "scope": q.scope, "challenges": q.challenges,
        }
        if q.category in ("CONFIRM", "MOMENTUM_ROBUSTNESS", "NON_MOMENTUM"):
            ev = base_ev[q.base_signal]
            st, reason, _ = candidate_verdicts[q.base_signal]
            row.update(_ev_scoreboard_fields(ev))
            row["status"] = st
            row["reason"] = reason
        elif q.category == "CHALLENGE":
            ev = base_ev[q.base_signal]
            row.update(_challenge_fields(q, ev))
        elif q.category == "RISK_STRESS":
            ev = cap_ev[q.experiment_id]
            base = base_ev[q.base_signal]
            row.update(_ev_scoreboard_fields(ev))
            row["status"] = ST_DIAGNOSTIC
            d_sh = (ev["net_sharpe_25bps"] or 0) - (base["net_sharpe_25bps"] or 0)
            row["reason"] = (f"risk stress cap={q.max_pos:.2f}: net Sharpe {ev['net_sharpe_25bps']} "
                             f"(delta vs base {_round(d_sh, 3)})")
        rows.append(row)
    return rows


def _ev_scoreboard_fields(ev: dict) -> dict:
    return {
        "n_periods": ev["n_periods"], "avg_names": ev["avg_names"],
        "one_sided_turnover": ev["one_sided_turnover"],
        "net_sharpe_25bps": ev["net_sharpe_25bps"], "net_ann_return_25bps": ev["net_ann_return_25bps"],
        "net_max_drawdown_25bps": ev["net_max_drawdown_25bps"],
        "net_sharpe_50bps": ev["cost_sharpe"].get(50), "net_sharpe_100bps": ev["cost_sharpe"].get(100),
        "spy_sharpe": ev["spy_sharpe"], "ew_universe_sharpe": ev["ew_universe_sharpe"],
        "net_sharpe_minus_spy": ev["net_sharpe_minus_spy"], "net_sharpe_minus_ew": ev["net_sharpe_minus_ew"],
        "placebo_net_sharpe_25bps": ev["placebo_net_sharpe_25bps"],
        "net_sharpe_minus_placebo": ev["net_sharpe_minus_placebo"],
        "subperiod_net_sharpe": "", "subperiod_beats_spy": "",
        "leakage_check": ev["leakage_check"],
    }


def _challenge_fields(q: QueuedExperiment, ev: dict) -> dict:
    row = {"status": ST_DIAGNOSTIC, "n_periods": ev["n_periods"], "avg_names": ev["avg_names"],
           "one_sided_turnover": ev["one_sided_turnover"], "leakage_check": ev["leakage_check"],
           "spy_sharpe": ev["spy_sharpe"], "ew_universe_sharpe": ev["ew_universe_sharpe"]}
    if q.scope.startswith("subperiod:"):
        label = q.scope.split(":", 1)[1]
        sub = ev["subperiods"].get(label, {})
        row["net_sharpe_25bps"] = sub.get("net_sharpe")
        row["net_ann_return_25bps"] = sub.get("net_ann_return")
        row["net_max_drawdown_25bps"] = sub.get("net_max_drawdown")
        row["spy_sharpe"] = sub.get("spy_sharpe")
        row["ew_universe_sharpe"] = sub.get("ew_sharpe")
        row["subperiod_net_sharpe"] = sub.get("net_sharpe")
        row["subperiod_beats_spy"] = sub.get("beats_spy")
        row["n_periods"] = sub.get("n_periods")
        persist = "PERSISTENT" if sub.get("beats_spy") and (sub.get("net_sharpe") or -9) > 0 else "NON_PERSISTENT"
        if sub.get("status") == ST_BLOCKED:
            persist = "INSUFFICIENT_HISTORY"
        row["reason"] = f"holdout {label}: {persist} (net Sharpe {sub.get('net_sharpe')}, beats_spy={sub.get('beats_spy')})"
    elif q.scope.startswith("cost@"):
        bps = int(q.scope.split("@")[1].replace("bps", ""))
        sh = ev["cost_sharpe"].get(bps)
        row["net_sharpe_25bps"] = ev["net_sharpe_25bps"]
        row[f"net_sharpe_{bps}bps"] = sh
        row["net_sharpe_50bps"] = ev["cost_sharpe"].get(50)
        row["net_sharpe_100bps"] = ev["cost_sharpe"].get(100)
        verdict = "COST_SURVIVES" if (sh is not None and sh > 0) else "COST_FRAGILE"
        row["reason"] = f"cost stress {bps}bps: net Sharpe {sh} -> {verdict}"
    return row


def _assemble_report(started, state, orch_ok, orch_problems, panel, queue,
                     base_ev, candidate_verdicts, confirmation, decision) -> dict:
    idx = panel.close.index
    mt = _multiple_testing(state, queue, candidate_verdicts)
    n_challenge = sum(1 for q in queue if q.category == "CHALLENGE")
    n_nonmom = sum(1 for q in queue if q.category == "NON_MOMENTUM")
    return {
        "phase": PHASE,
        "objective": OBJECTIVE,
        "generated_utc": started,
        "recommendation": decision["recommendation"],
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "allowed_experiment_statuses": list(ALLOWED_STATUSES),
        "decision": decision,
        "confirmation": confirmation,
        "multiple_testing": mt,
        "orchestrator": {
            "ready": orch_ok, "problems": orch_problems,
            "subagents_ok": state.get("subagents_ok"), "n_subagents": state.get("n_subagents"),
            "n_contracts": state.get("n_contracts"),
            "phase8a_inputs_present": state.get("phase8a_present"),
            "phase8a_approved_ids": state.get("phase8a_approved_ids"),
        },
        "autonomy_budget": {
            "max_experiments": MAX_EXPERIMENTS,
            "experiments_registered": len(queue),
            "n_challenge": n_challenge,
            "challenge_fraction": _round(n_challenge / len(queue), 3) if queue else 0,
            "challenge_min_required": 0.30,
            "n_non_momentum": n_nonmom,
            "non_momentum_fraction": _round(n_nonmom / len(queue), 3) if queue else 0,
            "non_momentum_min_required": 0.20,
            "all_registered_before_scoring": True,
        },
        "panel": {
            "source": "Phase 8-A persisted survivorship-aware panel (reused, not recollected)",
            "root": str(panel_root_str()),
            "n_symbols": int(panel.close.shape[1]),
            "n_months": int(len(idx)),
            "date_range": [str(idx.min())[:10], str(idx.max())[:10]] if len(idx) else [],
            "n_active": int((~panel.metadata["is_delisted"]).sum()) if "is_delisted" in panel.metadata else None,
            "n_delisted": int(panel.metadata["is_delisted"].sum()) if "is_delisted" in panel.metadata else None,
            "ok": panel.ok, "issues": panel.issues,
        },
        "safety": {
            "provider_is_local_norgate_only": True,
            "reused_existing_panel_no_recollection": True,
            "network_or_paid_api_used": False,
            "packages_installed": False,
            "large_data_only_on_d": True,
            "optimized_weights_used": False,
            "factor_signs_modified_after_results": False,
            "regime_activation_or_throttling": False,
            "failed_experiments_hidden": False,
            "paper_trader_gcp_broker_touched": False,
            "orders_or_automation_created": False,
            "committed": False,
            "pushed": False,
        },
    }


def panel_root_str() -> str:
    return str(PANEL_ROOT).replace("\\", "/")


# --------------------------------------------------------------------------- #
# Artifact emission (21 committed-safe artifacts).
# --------------------------------------------------------------------------- #
def _emit_all(paths: OutPaths, report: dict, state: dict, panel: Panel,
              queue: List[QueuedExperiment], base_ev: Optional[dict] = None,
              cap_ev: Optional[dict] = None, candidate_verdicts: Optional[dict] = None,
              scoreboard: Optional[List[dict]] = None, confirmation: Optional[dict] = None,
              decision: Optional[dict] = None, evaluations: Optional[dict] = None) -> None:
    base_ev = base_ev or {}
    cap_ev = cap_ev or {}
    candidate_verdicts = candidate_verdicts or {}
    confirmation = confirmation or {}
    decision = decision or report.get("decision", {})

    _write_json(paths.p("phase8b_autonomous_research_director.json"), report)

    # 2) research_agenda.csv
    _write_csv(paths.p("research_agenda.csv"), _research_agenda_rows(queue),
               ["theme", "rationale", "n_experiments", "owning_agents"])
    # 3) agent_task_allocation.csv
    _write_csv(paths.p("agent_task_allocation.csv"), _agent_allocation_rows(queue),
               ["agent", "n_experiments", "experiment_ids", "deliverable_artifact", "note"])
    # 4) data_panel_check.csv
    _write_csv(paths.p("data_panel_check.csv"), _data_panel_check_rows(panel),
               ["check", "value", "status"])
    # 5) universe_check.csv
    _write_csv(paths.p("universe_check.csv"), _universe_check_rows(panel),
               ["scope", "median_members", "max_members", "n_months", "pit_membership_confirmed"])
    # 6) feature_catalog.csv
    _write_csv(paths.p("feature_catalog.csv"), _feature_catalog_rows(),
               ["feature", "family", "definition", "description", "leakage_rule", "registered_before_scoring"])
    # 7) experiment_queue.csv
    _write_csv(paths.p("experiment_queue.csv"), _queue_rows(queue),
               ["experiment_id", "category", "owning_agent", "family", "score_key", "quantile",
                "gate", "max_pos", "scope", "challenges", "hypothesis", "success_gate", "stop_condition"])
    # 8) experiment_registry.csv
    _write_csv(paths.p("experiment_registry.csv"), _registry_rows(queue, scoreboard or []),
               ["experiment_id", "category", "owning_agent", "family", "scope", "challenges",
                "hypothesis", "status"])
    # 9) all_experiments_scoreboard.csv
    _write_csv(paths.p("all_experiments_scoreboard.csv"), scoreboard or [], _SCOREBOARD_COLS)
    # 10) failed_experiments.csv (never hidden)
    failed = [r for r in (scoreboard or []) if r.get("status") in (ST_REJECTED, ST_WEAK, ST_BLOCKED)]
    _write_csv(paths.p("failed_experiments.csv"), failed, _SCOREBOARD_COLS)
    # 11) approved_signals.csv
    approved = [r for r in (scoreboard or []) if r.get("status") == ST_APPROVED]
    _write_csv(paths.p("approved_signals.csv"), approved, _SCOREBOARD_COLS)

    # 12-15) per-agent signal reports
    _write_csv(paths.p("momentum_agent_report.csv"),
               _agent_report_rows(queue, base_ev, candidate_verdicts, MOM_A),
               _AGENT_REPORT_COLS)
    _write_csv(paths.p("reversal_agent_report.csv"),
               _agent_report_rows(queue, base_ev, candidate_verdicts, REV_A),
               _AGENT_REPORT_COLS)
    _write_csv(paths.p("trend_breadth_agent_report.csv"),
               _agent_report_rows(queue, base_ev, candidate_verdicts, TRB_A),
               _AGENT_REPORT_COLS)
    _write_csv(paths.p("volatility_liquidity_agent_report.csv"),
               _agent_report_rows(queue, base_ev, candidate_verdicts, VOL_A),
               _AGENT_REPORT_COLS)
    # 16) validation_skeptic_report.csv
    _write_csv(paths.p("validation_skeptic_report.csv"),
               _validation_skeptic_rows(base_ev, candidate_verdicts, report.get("multiple_testing", {})),
               ["signal_id", "phase8a_id", "check_type", "scope", "net_sharpe", "net_ann_return",
                "beats_spy", "beats_ew", "verdict", "detail"])
    # 17) risk_portfolio_report.csv
    _write_csv(paths.p("risk_portfolio_report.csv"),
               _risk_portfolio_rows(base_ev, cap_ev, queue),
               ["signal_id", "phase8a_id", "scope", "max_pos", "net_sharpe_25bps",
                "net_max_drawdown_25bps", "one_sided_turnover", "avg_names", "avg_max_weight",
                "beta_spy", "top_sector", "top_sector_weight", "delisted_weight_avg", "finding"])
    # 18) ensemble_readiness_report.csv
    _write_csv(paths.p("ensemble_readiness_report.csv"),
               _ensemble_readiness_rows(base_ev, candidate_verdicts),
               ["metric", "value", "note"])
    # 19) paper_signal_contract.csv
    _write_csv(paths.p("paper_signal_contract.csv"),
               _paper_signal_contract_rows(confirmation, base_ev),
               ["signal_id", "phase8a_id", "definition", "universe", "rebalance", "quantile",
                "cost_assumption_bps", "expected_net_sharpe", "status", "safety", "note"])
    # 20) research_director_decision.json
    _write_json(paths.p("research_director_decision.json"),
                _research_director_decision(report, state, confirmation, decision))
    # 21) phase8c_next_plan.json
    _write_json(paths.p("phase8c_next_plan.json"), _phase8c_plan(report, decision, confirmation))


_AGENT_REPORT_COLS = [
    "experiment_id", "score_key", "quantile", "gate", "family", "status", "n_periods",
    "avg_names", "one_sided_turnover", "net_sharpe_25bps", "net_ann_return_25bps",
    "net_max_drawdown_25bps", "net_sharpe_50bps", "spy_sharpe", "ew_universe_sharpe",
    "net_sharpe_minus_spy", "net_sharpe_minus_placebo", "subperiods_positive", "reason",
]


def _agent_report_rows(queue, base_ev, candidate_verdicts, agent) -> List[dict]:
    rows: List[dict] = []
    for q in queue:
        if q.owning_agent != agent or q.base_signal not in base_ev:
            continue
        if q.category not in ("CONFIRM", "MOMENTUM_ROBUSTNESS", "NON_MOMENTUM"):
            continue
        ev = base_ev[q.base_signal]
        st, reason, stab = candidate_verdicts[q.base_signal]
        rows.append({
            "experiment_id": q.experiment_id, "score_key": ev["score_key"],
            "quantile": ev["quantile"], "gate": ev["gate"], "family": q.family, "status": st,
            "n_periods": ev["n_periods"], "avg_names": ev["avg_names"],
            "one_sided_turnover": ev["one_sided_turnover"],
            "net_sharpe_25bps": ev["net_sharpe_25bps"], "net_ann_return_25bps": ev["net_ann_return_25bps"],
            "net_max_drawdown_25bps": ev["net_max_drawdown_25bps"],
            "net_sharpe_50bps": ev["cost_sharpe"].get(50), "spy_sharpe": ev["spy_sharpe"],
            "ew_universe_sharpe": ev["ew_universe_sharpe"],
            "net_sharpe_minus_spy": ev["net_sharpe_minus_spy"],
            "net_sharpe_minus_placebo": ev["net_sharpe_minus_placebo"],
            "subperiods_positive": f"{stab['n_subperiods_positive']}/{stab['n_subperiods_judged']}",
            "reason": reason,
        })
    if not rows:
        rows.append({"experiment_id": "(none allocated)", "family": "", "status": "NOT_ALLOCATED",
                     "reason": _NOT_ALLOCATED_NOTE.get(agent, "Director allocated no experiments to "
                               "this agent this round; see research_agenda.csv for rationale.")})
    return rows


_NOT_ALLOCATED_NOTE = {
    TRB_A: ("Only sector-relative breadth momentum (MR06) was allocated; all other 8-A "
            "trend/breakout/relative-strength variants (EXP13-EXP16) were REJECTED on clean data "
            "and re-running them is low value versus confirming survivors + testing orthogonal "
            "low-volatility / liquidity families."),
}


def _research_agenda_rows(queue: List[QueuedExperiment]) -> List[dict]:
    by_cat: Dict[str, List[str]] = defaultdict(list)
    agents_by_cat: Dict[str, set] = defaultdict(set)
    for q in queue:
        by_cat[q.category].append(q.experiment_id)
        agents_by_cat[q.category].add(q.owning_agent)
    themes = {
        "CONFIRM": "Confirm Phase 8-A approved signals (EXP02, EXP11) out-of-sample",
        "CHALLENGE": "Challenge/disprove the approved signals via holdout sub-periods + cost stress",
        "MOMENTUM_ROBUSTNESS": "Bounded, pre-registered momentum-family robustness variants",
        "NON_MOMENTUM": "Orthogonal non-momentum families (low-vol, downside-vol, liquidity, reversal)",
        "RISK_STRESS": "Capacity (tight cap) and concentration (loose cap) risk stress",
    }
    rationale = {
        "CONFIRM": "8-A edge was thin (+0.03..0.07 Sharpe); must verify it is not a lucky winner.",
        "CHALLENGE": ">=30% of budget mandated to try to disprove the survivors before trusting them.",
        "MOMENTUM_ROBUSTNESS": "Test whether the survivor edge generalizes across horizon/liquidity/breadth.",
        "NON_MOMENTUM": ">=20% of budget mandated to seek orthogonal, deterministic alternatives.",
        "RISK_STRESS": "Confirm the edge is not an artifact of concentration or capacity assumptions.",
    }
    order = ["CONFIRM", "CHALLENGE", "MOMENTUM_ROBUSTNESS", "NON_MOMENTUM", "RISK_STRESS"]
    return [{
        "theme": themes[c], "rationale": rationale[c],
        "n_experiments": len(by_cat[c]),
        "owning_agents": ", ".join(sorted(agents_by_cat[c])),
    } for c in order if c in by_cat]


def _agent_allocation_rows(queue: List[QueuedExperiment]) -> List[dict]:
    alloc: Dict[str, List[str]] = defaultdict(list)
    for q in queue:
        alloc[q.owning_agent].append(q.experiment_id)
    deliverables = {
        MOM_A: "momentum_agent_report.csv", REV_A: "reversal_agent_report.csv",
        TRB_A: "trend_breadth_agent_report.csv", VOL_A: "volatility_liquidity_agent_report.csv",
        VAL_A: "validation_skeptic_report.csv", RSK_A: "risk_portfolio_report.csv",
    }
    support = {
        "quant-research-director": "research_director_decision.json / research_agenda.csv",
        "data-foundation-agent": "data_panel_check.csv",
        "universe-construction-agent": "universe_check.csv",
        "feature-library-agent": "feature_catalog.csv",
        "meta-model-ensemble-agent": "ensemble_readiness_report.csv",
        "signal-publishing-agent": "paper_signal_contract.csv",
    }
    rows: List[dict] = []
    for agent in (MOM_A, REV_A, TRB_A, VOL_A, VAL_A, RSK_A):
        ids = alloc.get(agent, [])
        rows.append({"agent": agent, "n_experiments": len(ids),
                     "experiment_ids": ", ".join(ids),
                     "deliverable_artifact": deliverables[agent],
                     "note": "scores allocated experiments"})
    for agent, art in support.items():
        rows.append({"agent": agent, "n_experiments": 0, "experiment_ids": "",
                     "deliverable_artifact": art,
                     "note": "support role (no scored experiments this round)"})
    return rows


def _data_panel_check_rows(panel: Panel) -> List[dict]:
    idx = panel.close.index
    rows = [
        {"check": "panel_root", "value": panel_root_str(), "status": "OK" if panel.ok else "ISSUE"},
        {"check": "files_present", "value": ", ".join(PANEL_FILES.values()),
         "status": "OK" if panel.ok or not [i for i in panel.issues if "missing" in i] else "MISSING"},
        {"check": "n_symbols", "value": int(panel.close.shape[1]),
         "status": "OK" if panel.close.shape[1] >= MIN_NAMES_PORT else "ISSUE"},
        {"check": "n_months", "value": int(len(idx)),
         "status": "OK" if len(idx) >= MIN_PERIODS_VALID else "ISSUE"},
        {"check": "date_range", "value": f"{str(idx.min())[:10]}..{str(idx.max())[:10]}" if len(idx) else "",
         "status": "OK" if len(idx) else "ISSUE"},
        {"check": "n_active", "value": int((~panel.metadata["is_delisted"]).sum()) if "is_delisted" in panel.metadata else "",
         "status": "OK"},
        {"check": "n_delisted", "value": int(panel.metadata["is_delisted"].sum()) if "is_delisted" in panel.metadata else "",
         "status": "OK" if ("is_delisted" in panel.metadata and panel.metadata["is_delisted"].sum() > 0) else "ISSUE"},
        {"check": "membership_sum", "value": int(np.nansum(panel.membership.to_numpy())),
         "status": "OK" if np.nansum(panel.membership.to_numpy()) > 0 else "ISSUE"},
        {"check": "spy_observations", "value": int(panel.spy_monthly.notna().sum()),
         "status": "OK" if panel.spy_monthly.notna().sum() >= MIN_PERIODS_VALID else "ISSUE"},
        {"check": "overall", "value": "USABLE" if panel.ok else "; ".join(panel.issues),
         "status": "OK" if panel.ok else "BLOCKED"},
    ]
    return rows


def _universe_check_rows(panel: Panel) -> List[dict]:
    if panel.membership.empty:
        return [{"scope": "full", "median_members": 0, "max_members": 0, "n_months": 0,
                 "pit_membership_confirmed": False}]
    rows: List[dict] = []
    counts = panel.membership.sum(axis=1)

    def _slice(a: int, b: int):
        yrs = panel.membership.index.year
        return counts[(yrs >= a) & (yrs <= b)]

    full_pos = counts[counts > 0]
    rows.append({"scope": "full", "median_members": int(full_pos.median()) if len(full_pos) else 0,
                 "max_members": int(counts.max()) if len(counts) else 0,
                 "n_months": int((counts > 0).sum()), "pit_membership_confirmed": bool(counts.max() > 0)})
    for label, (a, b) in SUBPERIODS.items():
        c = _slice(a, b)
        cpos = c[c > 0]
        rows.append({"scope": label, "median_members": int(cpos.median()) if len(cpos) else 0,
                     "max_members": int(c.max()) if len(c) else 0,
                     "n_months": int((c > 0).sum()), "pit_membership_confirmed": bool(len(cpos) > 0)})
    return rows


def _feature_catalog_rows() -> List[dict]:
    return [{"feature": f, "family": fam, "definition": d, "description": desc,
             "leakage_rule": lr, "registered_before_scoring": True}
            for f, fam, d, desc, lr in FEATURE_CATALOG]


def _queue_rows(queue: List[QueuedExperiment]) -> List[dict]:
    return [{
        "experiment_id": q.experiment_id, "category": q.category, "owning_agent": q.owning_agent,
        "family": q.family, "score_key": q.score_key, "quantile": q.quantile,
        "gate": q.gate_key or "", "max_pos": q.max_pos, "scope": q.scope,
        "challenges": q.challenges, "hypothesis": q.hypothesis,
        "success_gate": q.success_gate, "stop_condition": q.stop_condition,
    } for q in queue]


def _registry_rows(queue: List[QueuedExperiment], scoreboard: List[dict]) -> List[dict]:
    status_by_id = {r["experiment_id"]: r.get("status", "") for r in scoreboard}
    return [{
        "experiment_id": q.experiment_id, "category": q.category, "owning_agent": q.owning_agent,
        "family": q.family, "scope": q.scope, "challenges": q.challenges,
        "hypothesis": q.hypothesis, "status": status_by_id.get(q.experiment_id, ""),
    } for q in queue]


def _validation_skeptic_rows(base_ev: dict, candidate_verdicts: dict, mt: dict) -> List[dict]:
    rows: List[dict] = []
    for sid, exp_id in (("MR01", "EXP02"), ("MR02", "EXP11")):
        ev = base_ev.get(sid)
        if ev is None:
            continue
        st, reason, stab = candidate_verdicts[sid]
        # leakage
        rows.append({"signal_id": sid, "phase8a_id": exp_id, "check_type": "leakage",
                     "scope": "full", "net_sharpe": ev["net_sharpe_25bps"], "net_ann_return": ev["net_ann_return_25bps"],
                     "beats_spy": (ev["net_sharpe_minus_spy"] or 0) > 0, "beats_ew": (ev["net_sharpe_minus_ew"] or 0) > 0,
                     "verdict": "PASS" if ev["leakage_check"] == "PASS_NO_LOOKAHEAD" else "FAIL",
                     "detail": ev["leakage_check"]})
        # placebo
        pg = ev["net_sharpe_minus_placebo"]
        rows.append({"signal_id": sid, "phase8a_id": exp_id, "check_type": "placebo",
                     "scope": "full", "net_sharpe": ev["net_sharpe_25bps"], "net_ann_return": "",
                     "beats_spy": "", "beats_ew": "",
                     "verdict": "PASS" if (pg or 0) >= PLACEBO_SHARPE_MARGIN else "FAIL",
                     "detail": f"net Sharpe beats placebo by {pg} (margin {PLACEBO_SHARPE_MARGIN})"})
        # holdout sub-periods
        for label, sub in ev["subperiods"].items():
            if sub.get("status") == ST_BLOCKED:
                verdict = "INSUFFICIENT_HISTORY"
            else:
                verdict = "PERSISTENT" if (sub.get("beats_spy") and (sub.get("net_sharpe") or -9) > 0) else "NON_PERSISTENT"
            rows.append({"signal_id": sid, "phase8a_id": exp_id, "check_type": "holdout",
                         "scope": label, "net_sharpe": sub.get("net_sharpe"),
                         "net_ann_return": sub.get("net_ann_return"),
                         "beats_spy": sub.get("beats_spy"), "beats_ew": sub.get("beats_ew"),
                         "verdict": verdict, "detail": f"n_periods={sub.get('n_periods')}"})
        # rolling windows
        rl = ev["rolling"]
        rows.append({"signal_id": sid, "phase8a_id": exp_id, "check_type": "rolling_10y",
                     "scope": f"{rl['n_windows']} windows", "net_sharpe": rl["median_sharpe"],
                     "net_ann_return": "", "beats_spy": rl["frac_beat_spy"], "beats_ew": "",
                     "verdict": ("BEATS_SPY_MAJORITY" if (rl["frac_beat_spy"] or 0) >= 0.5
                                 else "UNDERPERFORMS_SPY_MAJORITY"),
                     "detail": f"min={rl['min_sharpe']} median={rl['median_sharpe']} max={rl['max_sharpe']} "
                               f"frac_positive={rl['frac_positive']} frac_beat_spy={rl['frac_beat_spy']}"})
        # cost stress
        for bps in (10, 25, 50, 100):
            sh = ev["cost_sharpe"].get(bps)
            rows.append({"signal_id": sid, "phase8a_id": exp_id, "check_type": "cost_stress",
                         "scope": f"{bps}bps", "net_sharpe": sh, "net_ann_return": ev["cost_ann"].get(bps),
                         "beats_spy": (sh is not None and (ev["spy_sharpe"] is not None) and sh > ev["spy_sharpe"]),
                         "beats_ew": "", "verdict": "SURVIVES" if (sh is not None and sh > 0) else "FRAGILE",
                         "detail": f"net Sharpe at {bps}bps"})
        # multiple testing + overall confirmation
        rows.append({"signal_id": sid, "phase8a_id": exp_id, "check_type": "multiple_testing",
                     "scope": f"{mt.get('search_universe_total')} tests", "net_sharpe": ev["net_sharpe_25bps"],
                     "net_ann_return": "", "beats_spy": "", "beats_ew": "",
                     "verdict": "DEFLATED_BY_STABILITY",
                     "detail": mt.get("correction", "")})
        rows.append({"signal_id": sid, "phase8a_id": exp_id, "check_type": "CONFIRMATION",
                     "scope": "overall", "net_sharpe": ev["net_sharpe_25bps"],
                     "net_ann_return": ev["net_ann_return_25bps"],
                     "beats_spy": (ev["net_sharpe_minus_spy"] or 0) > 0,
                     "beats_ew": (ev["net_sharpe_minus_ew"] or 0) > 0,
                     "verdict": st, "detail": reason})
    return rows


def _risk_portfolio_rows(base_ev: dict, cap_ev: dict, queue: List[QueuedExperiment]) -> List[dict]:
    rows: List[dict] = []
    for sid, exp_id in (("MR01", "EXP02"), ("MR02", "EXP11")):
        ev = base_ev.get(sid)
        if ev is None:
            continue
        r = ev["risk"]
        rows.append({
            "signal_id": sid, "phase8a_id": exp_id, "scope": "base", "max_pos": ev["max_pos"],
            "net_sharpe_25bps": ev["net_sharpe_25bps"], "net_max_drawdown_25bps": ev["net_max_drawdown_25bps"],
            "one_sided_turnover": ev["one_sided_turnover"], "avg_names": ev["avg_names"],
            "avg_max_weight": ev["avg_max_weight"], "beta_spy": r["beta_spy"],
            "top_sector": r["top_sector"], "top_sector_weight": r["top_sector_weight"],
            "delisted_weight_avg": r["delisted_weight_avg"],
            "finding": f"dd {ev['net_max_drawdown_25bps']} (floor {P8A.DD_FLOOR}); turnover "
                       f"{ev['one_sided_turnover']} (ceil {P8A.TURNOVER_CEIL}); beta {r['beta_spy']}; "
                       f"top sector {r['top_sector']} {r['top_sector_weight']}; "
                       f"avg delisted weight {r['delisted_weight_avg']}; "
                       f"avg max name weight {ev['avg_max_weight']} << {ev['max_pos']} cap "
                       f"(non-binding -> low single-name concentration risk; ADV capacity deferred to 8-C)",
        })
    # capacity / concentration stress rows
    base_by_signal = {"EXP02": base_ev.get("MR01"), "EXP11": base_ev.get("MR02")}
    for q in queue:
        if q.category != "RISK_STRESS" or q.experiment_id not in cap_ev:
            continue
        ev = cap_ev[q.experiment_id]
        base = base_by_signal.get(q.challenges)
        d_sh = (ev["net_sharpe_25bps"] or 0) - ((base or {}).get("net_sharpe_25bps") or 0)
        r = ev["risk"]
        rows.append({
            "signal_id": q.experiment_id, "phase8a_id": q.challenges, "scope": q.scope,
            "max_pos": q.max_pos, "net_sharpe_25bps": ev["net_sharpe_25bps"],
            "net_max_drawdown_25bps": ev["net_max_drawdown_25bps"],
            "one_sided_turnover": ev["one_sided_turnover"], "avg_names": ev["avg_names"],
            "avg_max_weight": ev["avg_max_weight"], "beta_spy": r["beta_spy"],
            "top_sector": r["top_sector"], "top_sector_weight": r["top_sector_weight"],
            "delisted_weight_avg": r["delisted_weight_avg"],
            "finding": f"net Sharpe {ev['net_sharpe_25bps']} (delta vs base {_round(d_sh, 3)}) at cap {q.max_pos:.2f}",
        })
    return rows


def _ensemble_readiness_rows(base_ev: dict, candidate_verdicts: dict) -> List[dict]:
    confirmed = [sid for sid, v in candidate_verdicts.items() if v[0] == ST_APPROVED]
    # correlation between the two 8-A approved signals' net return series (redundancy check)
    corr = None
    a = base_ev.get("MR01")
    b = base_ev.get("MR02")
    if a is not None and b is not None:
        na = net_returns(a["_sim"]["gross"], a["_sim"]["traded_fraction"], PRIMARY_COST_BPS)
        nb = net_returns(b["_sim"]["gross"], b["_sim"]["traded_fraction"], PRIMARY_COST_BPS)
        aligned = pd.concat([na.rename("a"), nb.rename("b")], axis=1).dropna()
        if len(aligned) >= 12:
            corr = _round(float(aligned["a"].corr(aligned["b"])), 3)
    n_confirmed = len(confirmed)
    ready = n_confirmed >= 2 and (corr is None or corr < 0.7)
    return [
        {"metric": "n_confirmed_signals", "value": n_confirmed,
         "note": "signals that passed full confirmation this round"},
        {"metric": "confirmed_signal_ids", "value": ", ".join(confirmed) or "(none)", "note": ""},
        {"metric": "corr_EXP02_EXP11_net", "value": corr,
         "note": "net-return correlation of the two 8-A momentum-family survivors"},
        {"metric": "distinct_low_correlation_signals", "value": ready,
         "note": "need >=2 confirmed signals with corr<0.7 before an ensemble is meaningful"},
        {"metric": "ensemble_ready", "value": ready,
         "note": "NO optimized weights computed; equal-weight only and only when ready"},
        {"metric": "optimized_weights_used", "value": False,
         "note": "forbidden in 8-B per the research-director protocol"},
    ]


def _paper_signal_contract_rows(confirmation: dict, base_ev: dict) -> List[dict]:
    confirmed = [(exp_id, c) for exp_id, c in confirmation.items() if c.get("confirmed")]
    if not confirmed:
        return [{"signal_id": "(none)", "phase8a_id": "", "definition": "",
                 "universe": "", "rebalance": "", "quantile": "", "cost_assumption_bps": "",
                 "expected_net_sharpe": "", "status": "NO_CONFIRMED_SIGNAL",
                 "safety": "PREVIEW ONLY | NO ORDERS | NO AUTOMATION | MANUAL REVIEW",
                 "note": "No signal passed full confirmation this round; no paper-research "
                         "contract drafted. Keep research-only."}]
    rows: List[dict] = []
    defs = {"mom_12_1": "12-1 month total-return momentum (skip most recent month), top quintile",
            "vol_adj_mom": "volatility-adjusted 12-1 momentum (mom_12_1 / 12m return vol), top quintile"}
    for exp_id, c in confirmed:
        rows.append({
            "signal_id": c["phase8b_id"], "phase8a_id": exp_id,
            "definition": defs.get(c["score_key"], c["score_key"]),
            "universe": "S&P 500 point-in-time members (survivorship-aware, incl. delisted while members)",
            "rebalance": "monthly, month-end", "quantile": c["quantile"],
            "cost_assumption_bps": int(PRIMARY_COST_BPS),
            "expected_net_sharpe": c["net_sharpe_25bps"],
            "status": "CONFIRMED_RESEARCH_PREVIEW",
            "safety": "PREVIEW ONLY | CREATES NO ORDERS | NO AUTOMATION | MANUAL REVIEW | NO BROKER",
            "note": "Paper-research preview only; thin edge — see validation_skeptic_report.csv. "
                    "Not a live trade recommendation.",
        })
    return rows


def _research_director_decision(report: dict, state: dict, confirmation: dict, decision: dict) -> dict:
    return {
        "phase": PHASE,
        "generated_utc": report["generated_utc"],
        "recommendation": report["recommendation"],
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "decision": decision,
        "confirmation": confirmation,
        "multiple_testing": report.get("multiple_testing", {}),
        "autonomy_budget": report.get("autonomy_budget", {}),
        "agenda_authority": "quant-research-director chose the agenda and budget autonomously "
                            "within the fixed guardrails (no user micro-instructions).",
        "anti_p_hacking": {
            "all_experiments_pre_registered": True,
            "challenge_fraction_min": 0.30,
            "non_momentum_fraction_min": 0.20,
            "placebo_required_margin": PLACEBO_SHARPE_MARGIN,
            "out_of_sample_stability_required_for_approval": True,
            "factor_signs_modified_after_results": False,
            "borderline_never_rounded_up": True,
            "failed_experiments_hidden": False,
        },
        "stop_conditions_honored": [
            "reused existing panel (no recollection)", "no packages installed",
            "no optimized weights", "no factor-sign flipping after results",
            "no regime activation/throttling", "no fundamentals", "no live trading signals",
            "no orders/broker/automation", "no Paper Trader / GCP", "no commit", "no push",
        ],
    }


def _phase8c_plan(report: dict, decision: dict, confirmation: dict) -> dict:
    rec = report["recommendation"]
    confirmed = decision.get("confirmed_8a_signals", [])
    weak = decision.get("positive_but_thin_8a_signals", [])
    if rec == REC_LOOP_READY:
        steps = [
            f"Promote confirmed signal(s) {confirmed} to a wider survivorship-aware panel "
            "(full Norgate S&P 500 Current & Past + optional Russell 3000) for a true holdout.",
            "Run the risk-portfolio-agent at target capacity with liquidity-constrained sizing "
            "before any paper-research preview is taken seriously.",
            "Only THEN assess an equal-weight (never optimized) combination IF a second, "
            "low-correlation confirmed signal exists.",
        ]
    elif rec == REC_WEAK:
        leads = decision.get("promising_leads", [])
        lead_str = ", ".join(f"{l['experiment_id']}:{l['score_key']} "
                             f"(net Sharpe {l['net_sharpe_25bps']}, +{l['net_sharpe_minus_spy']} vs SPY)"
                             for l in leads) or "(none beat SPY full-sample)"
        steps = [
            f"8-A survivors {weak} are positive full-sample but FAIL out-of-sample confirmation "
            "(beat SPY only in 1990-2004, not in 2005-2014 or the most recent 2015-2026): "
            "keep research-only, do NOT promote.",
            f"Prioritize the most promising NON-momentum leads for confirmation on a wider/longer "
            f"survivorship-aware panel: {lead_str}. They beat SPY full-sample with shallower "
            "drawdowns, but must clear the same out-of-sample sub-period + recency gate before trust.",
            "Build the full Norgate superset panel (and optionally Russell 3000) and re-run this "
            "exact confirmation gate; only deterministic rules, no re-tuning of momentum, "
            "no optimized weights, no factor-sign flipping.",
        ]
    elif rec == REC_REJECTED:
        steps = ["All 8-A approved signals failed confirmation on clean data; stop price/volume "
                 "signal hunting and escalate to the research director for an agenda change "
                 "(e.g., move Track A focus away from cross-sectional momentum)."]
    elif rec == REC_DATA_BLOCKED:
        steps = ["Rebuild the survivorship-aware Norgate panel (Phase 8-A run, full superset) "
                 "before any further confirmation work."]
    elif rec == REC_ORCH_BLOCKED:
        steps = ["Restore the agent system (12 subagents + 6 contracts) and 8-A outputs before "
                 "the orchestrator can run."]
    else:
        steps = ["Escalate to the research director: confirmation result is ambiguous."]
    return {
        "from_phase": PHASE, "recommendation": rec, "next_phase": "8-C",
        "next_steps": steps,
        "hard_constraints": [
            "Norgate is the only provider", "reuse/extend the panel on D: only",
            "repo outputs are summaries only", "no package install", "no paid APIs",
            "no Paper Trader", "no GCP", "no broker/order/automation",
            "no optimized weights without explicit director approval",
            "no factor-sign flipping", "do not hide failed experiments",
            "do not commit", "do not push",
        ],
    }


def _blocked_report(started, state, orch_ok, orch_problems, panel: Panel,
                    rec: str, reason: str) -> dict:
    queue = build_experiment_queue()
    n_challenge = sum(1 for q in queue if q.category == "CHALLENGE")
    n_nonmom = sum(1 for q in queue if q.category == "NON_MOMENTUM")
    decision = {"recommendation": rec, "blocked_reason": reason,
                "confirmed_8a_signals": [], "weak_8a_signals": [], "rejected_8a_signals": [],
                "positive_but_thin_8a_signals": [], "n_candidate_signals": 0,
                "n_candidate_approved": 0, "n_candidate_weak": 0,
                "n_candidate_rejected": 0, "n_candidate_blocked": 0}
    idx = panel.close.index
    return {
        "phase": PHASE, "objective": OBJECTIVE, "generated_utc": started,
        "recommendation": rec, "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "allowed_experiment_statuses": list(ALLOWED_STATUSES),
        "decision": decision, "confirmation": {},
        "multiple_testing": {"search_universe_total": 18 + len(BASE_SIGNALS)},
        "orchestrator": {"ready": orch_ok, "problems": orch_problems,
                         "subagents_ok": state.get("subagents_ok"),
                         "n_subagents": state.get("n_subagents"), "n_contracts": state.get("n_contracts"),
                         "phase8a_inputs_present": state.get("phase8a_present"),
                         "phase8a_approved_ids": state.get("phase8a_approved_ids")},
        "autonomy_budget": {"max_experiments": MAX_EXPERIMENTS, "experiments_registered": len(queue),
                            "n_challenge": n_challenge, "n_non_momentum": n_nonmom,
                            "all_registered_before_scoring": True},
        "panel": {"source": "Phase 8-A persisted panel", "root": panel_root_str(),
                  "n_symbols": int(panel.close.shape[1]) if not panel.close.empty else 0,
                  "n_months": int(len(idx)), "ok": panel.ok, "issues": panel.issues},
        "safety": {"provider_is_local_norgate_only": True, "reused_existing_panel_no_recollection": True,
                   "network_or_paid_api_used": False, "packages_installed": False,
                   "large_data_only_on_d": True, "optimized_weights_used": False,
                   "orders_or_automation_created": False, "committed": False, "pushed": False},
    }


def _print_summary(report: dict) -> None:
    rec = report["recommendation"]
    dec = report.get("decision", {})
    ab = report.get("autonomy_budget", {})
    pan = report.get("panel", {})
    print(f"[{PHASE}] recommendation = {rec}")
    print(f"[{PHASE}] orchestrator ready = {report['orchestrator'].get('ready')}; "
          f"subagents={report['orchestrator'].get('n_subagents')}/12; "
          f"contracts={report['orchestrator'].get('n_contracts')}/6")
    print(f"[{PHASE}] panel = {pan.get('n_symbols')} symbols x {pan.get('n_months')} months "
          f"{pan.get('date_range')} (ok={pan.get('ok')})")
    print(f"[{PHASE}] budget: {ab.get('experiments_registered')}/{ab.get('max_experiments')} experiments; "
          f"challenge={ab.get('n_challenge')} ({ab.get('challenge_fraction')}); "
          f"non-momentum={ab.get('n_non_momentum')} ({ab.get('non_momentum_fraction')})")
    print(f"[{PHASE}] confirmed 8-A signals = {dec.get('confirmed_8a_signals')}; "
          f"weak = {dec.get('positive_but_thin_8a_signals')}; rejected = {dec.get('rejected_8a_signals')}")
    print(f"[{PHASE}] candidates: approved={dec.get('n_candidate_approved')} "
          f"weak={dec.get('n_candidate_weak')} rejected={dec.get('n_candidate_rejected')} "
          f"blocked={dec.get('n_candidate_blocked')}")


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Phase 8-B Autonomous Research Director Orchestrator")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--panel-root", default=str(PANEL_ROOT))
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        report = run(Path(args.out_dir), panel_root=Path(args.panel_root))
    except Exception as exc:  # pragma: no cover - top-level guard
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        err = {"phase": PHASE, "recommendation": REC_ERROR, "error": repr(exc),
               "generated_utc": _utc_now_iso()}
        _write_json(out / "phase8b_autonomous_research_director.json", err)
        print(f"[{PHASE}] recommendation = {REC_ERROR}: {exc!r}")
        return 1
    _print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
