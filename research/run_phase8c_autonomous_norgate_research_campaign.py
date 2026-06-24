"""
Phase 8-C — Autonomous Norgate Research Campaign.

WHAT THIS IS
============
An autonomous, bounded, multi-cycle research campaign operated by the Phase 8-A/8-B
agent system. It (1) expands the survivorship-aware dataset to the broadest feasible
Norgate "Current & Past" universe, (2) allocates a bounded experiment budget across the
agent system, (3) runs up to 3 research cycles that pre-register experiments, score them,
run validation/skeptic gates, and update the approved / weak / rejected / blocked sets,
and (4) continues until a signal passes the CONFIRMED_SIGNAL gate, the research budget is
exhausted, or the research director rejects the current signal families.

This is RESEARCH ONLY. It is NOT Paper Trader, NOT production, NOT deployment, NOT a broker,
NOT order execution, NOT automation, and it emits NO live trade recommendation. It does NOT
optimize weights, does NOT modify factor signs after seeing results, does NOT use regime
activation/throttling, and does NOT hide failed experiments. Large data lives only on D:
(research_panels / model_artifacts); the repo holds committed-safe summaries. No packages are
installed and no paid/network API is used (Norgate is the only, locally installed, provider).

The deterministic, leakage-safe primitives (signal blocks, capped equal-weight long-only
simulation, cost model, placebo, benchmarks, metrics, the full-sample viability gate) and the
comprehensive out-of-sample evaluator (holdout sub-periods, rolling windows, cost stress, risk
profile) are IMPORTED UNCHANGED from the Phase 8-A and 8-B engines, so the judgment machinery
is identical. Phase 8-C adds: broad-universe construction, the multi-cycle campaign loop, the
stricter CONFIRMED_SIGNAL gate, and the multiple-testing deflation across the whole campaign.
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
# Import the verified Phase 8-B director (which itself imports the 8-A engine).
# Loading is side-effect free: norgatedata is imported lazily inside
# NorgateAdapter.__init__, which neither 8-B nor the pure paths here construct.
# --------------------------------------------------------------------------- #
def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load module at {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod   # required so @dataclass can resolve cls.__module__
    spec.loader.exec_module(mod)
    return mod


P8B = _load_module("phase8b_engine",
                   _RESEARCH_DIR / "run_phase8b_autonomous_research_director.py")
P8A = P8B.P8A

# ---- reused pure machinery (unchanged) ---- #
evaluate_signal = P8B.evaluate_signal
build_8b_blocks = P8B.build_8b_blocks
_subperiod_mask = P8B._subperiod_mask
_rolling_stats = P8B._rolling_stats
Panel = P8B.Panel
load_panel = P8B.load_panel
QDIV = P8B.QDIV
SUBPERIODS = P8B.SUBPERIODS
RECENT_LABEL = P8B.RECENT_LABEL
COST_BPS_GRID = P8B.COST_BPS_GRID
PRIMARY_COST_BPS = P8B.PRIMARY_COST_BPS
COST_ROBUST_BPS = P8B.COST_ROBUST_BPS
MIN_SUBPERIOD_PERIODS = P8B.MIN_SUBPERIOD_PERIODS
MIN_PERIODS_VALID = P8B.MIN_PERIODS_VALID
MIN_NAMES_PORT = P8B.MIN_NAMES_PORT
MAX_POSITION = P8B.MAX_POSITION
PLACEBO_SHARPE_MARGIN = P8A.PLACEBO_SHARPE_MARGIN
PERIODS_PER_YEAR = P8A.PERIODS_PER_YEAR

build_signal_blocks = P8A.build_signal_blocks
simulate_long_only = P8A.simulate_long_only
sharpe = P8A.sharpe
ann_return = P8A.ann_return
max_drawdown = P8A.max_drawdown
_round = P8A._round
_write_json = P8A._write_json
_write_csv = P8A._write_csv
_utc_now_iso = P8A._utc_now_iso

# reuse the 12-agent / 6-contract readiness check verbatim
read_project_state = P8B.read_project_state
orchestrator_ready = P8B.orchestrator_ready
REQUIRED_CONTRACTS = P8B.REQUIRED_CONTRACTS

PHASE = "8-C"
OBJECTIVE = (
    "Run an autonomous, bounded, multi-cycle Norgate research campaign: expand to the broadest "
    "feasible survivorship-aware universe, allocate a <=120 experiment budget across the agent "
    "system over up to 3 cycles, pre-register every experiment, CHALLENGE existing leads, run "
    "holdout / rolling / cost-stress / placebo / multiple-testing / risk validation, and stop "
    "when a signal passes the CONFIRMED_SIGNAL gate, the budget is exhausted, or the director "
    "rejects the current families. Research only; no orders / automation / optimized weights."
)

# --------------------------------------------------------------------------- #
# Recommendation vocabulary (exactly the allowed set, in order).
# --------------------------------------------------------------------------- #
REC_CONFIRMED = "CONFIRMED_SIGNAL_FOUND"
REC_WEAK = "LEADS_WEAK_KEEP_RESEARCH_ONLY"
REC_REJECTED = "SIGNAL_RESEARCH_REJECTED_ON_BROAD_DATA"
REC_FULL_PANEL = "FULL_PANEL_BUILD_REQUIRED"
REC_DATA_BLOCKED = "DATA_PANEL_BLOCKED"
REC_ORCH_BLOCKED = "ORCHESTRATOR_BLOCKED"
REC_NEEDS_REVIEW = "NEEDS_RESEARCH_DIRECTOR_REVIEW"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (
    REC_CONFIRMED, REC_WEAK, REC_REJECTED, REC_FULL_PANEL,
    REC_DATA_BLOCKED, REC_ORCH_BLOCKED, REC_NEEDS_REVIEW, REC_ERROR,
)

# Per-experiment status vocabulary (exactly the allowed set).
ST_CONFIRMED = "CONFIRMED_SIGNAL"
ST_WEAK = "WEAK"
ST_REJECTED = "REJECTED"
ST_BLOCKED = "BLOCKED"
ST_DIAGNOSTIC = "DIAGNOSTIC_ONLY"
ALLOWED_STATUSES = (ST_CONFIRMED, ST_WEAK, ST_REJECTED, ST_BLOCKED, ST_DIAGNOSTIC)

# --------------------------------------------------------------------------- #
# Paths.
# --------------------------------------------------------------------------- #
DATA_ROOT = Path("D:/Stock_Prediction_app_data")
PANEL_ROOT = DATA_ROOT / "research_panels" / "phase8c_russell3000"
MODEL_ARTIFACTS_ROOT = DATA_ROOT / "model_artifacts"
P8A_OUTPUT_DIR = _RESEARCH_DIR / "output" / "phase8a_autonomous_norgate_research_engine"
P8B_OUTPUT_DIR = _RESEARCH_DIR / "output" / "phase8b_autonomous_research_director"
AGENT_CONTRACT_DIR = _RESEARCH_DIR / "agents"
DEFAULT_OUT_DIR = _RESEARCH_DIR / "output" / "phase8c_autonomous_norgate_research_campaign"

PANEL_FILES = dict(P8B.PANEL_FILES)

# Broadest-feasible universe preference order (data-foundation + universe-construction agents).
UNIVERSE_PREFERENCE = [
    "Russell 3000 Current & Past",
    "S&P Composite 1500 Current & Past",
    "Russell 1000 Current & Past",
    "Russell 2000 Current & Past",
    "S&P 1000 Current & Past",
    "S&P 500 Current & Past",   # fallback only
]
# index_constituent_timeseries index name per watchlist (membership masks).
UNIVERSE_INDEX_NAME = {
    "Russell 3000 Current & Past": "Russell 3000",
    "S&P Composite 1500 Current & Past": "S&P Composite 1500",
    "Russell 1000 Current & Past": "Russell 1000",
    "Russell 2000 Current & Past": "Russell 2000",
    "S&P 1000 Current & Past": "S&P 1000",
    "S&P 500 Current & Past": "S&P 500",
}
MIN_UNIVERSE_MEMBERS = 200   # a universe is "feasible" only if its superset is broad enough

# --------------------------------------------------------------------------- #
# CONFIRMED_SIGNAL gate thresholds (fixed a-priori — NOT tuned to results).
# --------------------------------------------------------------------------- #
GATE_SPY_SHARPE_MARGIN = 0.15     # net Sharpe@25 must beat SPY by >= this
GATE_EW_SHARPE_MARGIN = 0.10      # ... and beat equal-weight universe by >= this
GATE_HOLDOUT_BEAT_MIN = 2         # beats SPY in >= 2 of 3 holdout periods
GATE_ROLLING_BEAT_FRAC = 0.60     # rolling 10y beat-SPY fraction >= 60%
GATE_DD_WORSE_PP = 0.10           # max drawdown no worse than SPY by > 10pp
GATE_MAX_TURNOVER = 0.50          # average (one-sided) monthly turnover <= 50%
GATE_MIN_HOLDINGS = 30            # average holdings >= 30
GATE_PLACEBO_MARGIN = PLACEBO_SHARPE_MARGIN   # net Sharpe must beat a seeded placebo by >=0.25

# --------------------------------------------------------------------------- #
# Campaign budget (autonomy guardrails).
# --------------------------------------------------------------------------- #
MAX_TOTAL_EXPERIMENTS = 120
MAX_PER_FAMILY = 40
CHALLENGE_MIN_FRAC = 0.30
NON_MOMENTUM_MIN_FRAC = 0.25
MAX_CYCLES = 3
COST_CHALLENGE_BPS = (50, 100)

# --------------------------------------------------------------------------- #
# Agent roster (the 12-agent system, used for task allocation artifacts).
# --------------------------------------------------------------------------- #
DIR_A = "quant-research-director"
DATA_A = "data-foundation-agent"
UNI_A = "universe-construction-agent"
FEAT_A = "feature-library-agent"
MOM_A = "momentum-signal-agent"
REV_A = "reversal-signal-agent"
TRB_A = "trend-breadth-signal-agent"
VOL_A = "volatility-liquidity-agent"
VAL_A = "validation-skeptic-agent"
RSK_A = "risk-portfolio-agent"
ENS_A = "meta-model-ensemble-agent"
PUB_A = "signal-publishing-agent"
ALL_AGENTS = [DIR_A, DATA_A, UNI_A, FEAT_A, MOM_A, REV_A, TRB_A, VOL_A,
              VAL_A, RSK_A, ENS_A, PUB_A]

# --------------------------------------------------------------------------- #
# Allowed signal families and the feature -> family map.
# Forbidden: fundamentals, optimized ensembles, ML fitting, regime activation.
# --------------------------------------------------------------------------- #
FAM_LOW_VOL = "low_volatility"
FAM_VAM = "volatility_adjusted_momentum"
FAM_MOM = "momentum"
FAM_REV = "short_term_reversal"
FAM_TREND = "trend_breadth"
FAM_LIQ = "liquidity_turnover"
FAM_SECTOR = "sector_relative"
FAM_MARKET = "market_relative"
ALLOWED_FAMILIES = (FAM_LOW_VOL, FAM_VAM, FAM_MOM, FAM_REV, FAM_TREND,
                    FAM_LIQ, FAM_SECTOR, FAM_MARKET)
# families that count toward the >=25% non-momentum allocation
NON_MOMENTUM_FAMILIES = frozenset({FAM_LOW_VOL, FAM_REV, FAM_TREND, FAM_LIQ,
                                   FAM_SECTOR, FAM_MARKET})

FAMILY_OF = {
    "low_vol": FAM_LOW_VOL, "downside_vol": FAM_LOW_VOL,
    "low_vol_6": FAM_LOW_VOL, "downside_vol_6": FAM_LOW_VOL,
    "vol_adj_mom": FAM_VAM, "vol_adj_mom_6": FAM_VAM,
    "mom_12_1": FAM_MOM, "mom_6_1": FAM_MOM, "mom_3": FAM_MOM,
    "rev_losers": FAM_REV,
    "trend_score": FAM_TREND, "trend_ma12": FAM_TREND,
    "illiquidity": FAM_LIQ, "high_liquidity": FAM_LIQ,
    "sector_rel_mom": FAM_SECTOR,
    "rel_strength": FAM_MARKET,
}


def is_non_momentum_family(fam: str) -> bool:
    return fam in NON_MOMENTUM_FAMILIES


def agent_for(score_key: str, category: str) -> str:
    if category == "CHALLENGE":
        return VAL_A
    if category == "RISK_STRESS":
        return RSK_A
    fam = FAMILY_OF.get(score_key, FAM_TREND)
    if fam in (FAM_MOM, FAM_VAM):
        return MOM_A
    if fam == FAM_REV:
        return REV_A
    if fam in (FAM_TREND, FAM_SECTOR, FAM_MARKET):
        return TRB_A
    if fam in (FAM_LOW_VOL, FAM_LIQ):
        return VOL_A
    return TRB_A


# --------------------------------------------------------------------------- #
# Feature library (registered BEFORE scoring). Extends the 8-B blocks with a few
# deterministic, a-priori robustness variants. Every block at row t uses only data
# <= t; the realized forward return for t is strictly over (t, t+1]. These are
# standard anomaly definitions — NOT outcome-driven sign flips.
# --------------------------------------------------------------------------- #
def build_8c_blocks(close: pd.DataFrame, dollar_vol: pd.DataFrame,
                    spy_monthly: pd.Series, sector_map: Dict[str, str]) -> dict:
    blocks = build_8b_blocks(close, dollar_vol, spy_monthly, sector_map)
    ret_1m = close.pct_change()
    vol_12 = ret_1m.rolling(12, min_periods=6).std()
    vol_6 = ret_1m.rolling(6, min_periods=3).std()
    downside = ret_1m.clip(upper=0.0)
    dvol_6 = downside.rolling(6, min_periods=3).std()
    ma_12 = close.rolling(12, min_periods=6).mean()
    blocks.update({
        # low-volatility robustness (shorter estimation window)
        "low_vol_6": -vol_6,
        "downside_vol_6": -dvol_6,
        # trend / breadth: distance above the 12-month moving average
        "trend_ma12": close / ma_12 - 1.0,
        # volatility-adjusted 6-1 momentum (robustness on the VAM family)
        "vol_adj_mom_6": (blocks["mom_6_1"] / vol_12.replace(0.0, np.nan)),
    })
    return blocks


FEATURE_CATALOG = list(P8B.FEATURE_CATALOG) + [
    ("low_vol_6", FAM_LOW_VOL, "-rolling6_vol(ret_1m)",
     "low-volatility anomaly, 6m window (robustness, a-priori)", "close<=t"),
    ("downside_vol_6", FAM_LOW_VOL, "-rolling6_std(min(ret_1m,0))",
     "low downside-deviation, 6m window (robustness, a-priori)", "close<=t"),
    ("trend_ma12", FAM_TREND, "close/rolling12_mean(close)-1",
     "distance above 12m moving average (trend/breadth)", "close<=t"),
    ("vol_adj_mom_6", FAM_VAM, "mom_6_1 / rolling12_vol(ret_1m)",
     "volatility-adjusted 6-1 momentum (robustness)", "close<=t-1"),
]


# --------------------------------------------------------------------------- #
# CONFIRMED_SIGNAL gate.
# --------------------------------------------------------------------------- #
def mt_required_excess(n_search: int) -> float:
    """Multiple-testing deflation: the more experiments in the search universe, the higher the
    full-sample net-Sharpe-over-SPY margin a signal must clear. Monotonic in N, documented in
    multiple_testing_report.csv. (This is ON TOP of the placebo + out-of-sample + recency gates.)"""
    n = max(int(n_search), 10)
    return _round(GATE_SPY_SHARPE_MARGIN * max(1.0, math.log10(n)))


def confirmed_checks(ev: dict, n_search: int) -> Tuple[Dict[str, bool], dict]:
    """The literal CONFIRMED_SIGNAL gate, returned as named booleans + the metrics behind them."""
    ns = ev["net_sharpe_25bps"]
    spy = ev["spy_sharpe"]
    ew = ev["ew_universe_sharpe"]
    subs = ev["subperiods"]
    judged = {k: v for k, v in subs.items() if v.get("status") != ST_BLOCKED}
    n_beat = sum(1 for v in judged.values() if v.get("beats_spy"))
    recent = subs.get(RECENT_LABEL, {})
    recent_judged = recent.get("status") != ST_BLOCKED
    recent_beat = bool(recent_judged and recent.get("beats_spy"))
    roll = ev["rolling"]["frac_beat_spy"]
    net_dd = ev["net_max_drawdown_25bps"]
    spy_dd = ev["spy_max_drawdown"]
    turn = ev["one_sided_turnover"]
    avgn = ev["avg_names"]
    placebo_margin = ev["net_sharpe_minus_placebo"]
    cost50 = ev["cost_sharpe"].get(int(COST_ROBUST_BPS))
    mt_excess = mt_required_excess(n_search)
    excess_spy = (ns - spy) if (ns is not None and spy is not None) else None
    excess_ew = (ns - ew) if (ns is not None and ew is not None) else None

    checks = {
        "net_sharpe_beats_spy_by_0.15": bool(excess_spy is not None and excess_spy >= GATE_SPY_SHARPE_MARGIN),
        "net_sharpe_beats_ew_by_0.10": bool(excess_ew is not None and excess_ew >= GATE_EW_SHARPE_MARGIN),
        "beats_spy_2_of_3_holdouts": bool(len(judged) >= 2 and n_beat >= GATE_HOLDOUT_BEAT_MIN),
        "beats_spy_recent_2015_2026": recent_beat,
        "rolling_10y_beat_frac_ge_0.60": bool(roll is not None and roll >= GATE_ROLLING_BEAT_FRAC),
        "drawdown_within_10pp_of_spy": bool(net_dd is not None and spy_dd is not None
                                            and net_dd >= (spy_dd - GATE_DD_WORSE_PP)),
        "net_positive_at_50bps": bool(cost50 is not None and cost50 > 0),
        "avg_turnover_le_0.50": bool(turn is not None and turn <= GATE_MAX_TURNOVER),
        "avg_holdings_ge_30": bool(avgn is not None and avgn >= GATE_MIN_HOLDINGS),
        "leakage_check_pass": ev["leakage_check"] == "PASS_NO_LOOKAHEAD",
        "placebo_pass": bool(placebo_margin is not None and placebo_margin >= GATE_PLACEBO_MARGIN),
        "survives_multiple_testing": bool(excess_spy is not None and excess_spy >= mt_excess),
    }
    metrics = {
        "net_sharpe_25bps": ns, "spy_sharpe": spy, "ew_universe_sharpe": ew,
        "net_sharpe_minus_spy": _round(excess_spy), "net_sharpe_minus_ew": _round(excess_ew),
        "n_holdouts_beat_spy": n_beat, "n_holdouts_judged": len(judged),
        "recent_beats_spy": recent_beat,
        "rolling_frac_beat_spy": roll,
        "net_max_drawdown_25bps": net_dd, "spy_max_drawdown": spy_dd,
        "one_sided_turnover": turn, "avg_names": avgn,
        "placebo_net_sharpe_25bps": ev["placebo_net_sharpe_25bps"],
        "net_sharpe_minus_placebo": placebo_margin,
        "cost50_sharpe": cost50,
        "mt_required_excess_over_spy": mt_excess,
        # reported (not gated): active/delisted exposure + sector concentration
        "delisted_weight_avg": ev["risk"].get("delisted_weight_avg"),
        "top_sector": ev["risk"].get("top_sector"),
        "top_sector_weight": ev["risk"].get("top_sector_weight"),
    }
    return checks, metrics


def classify_8c(ev: dict, n_search: int) -> Tuple[str, str, dict, dict]:
    """CONFIRMED_SIGNAL only if EVERY gate passes. Positive-but-misses-a-major-gate -> WEAK.
    Fails benchmark / cost / recency -> REJECTED. Too little history -> BLOCKED.
    Borderline is NEVER rounded up."""
    checks, metrics = confirmed_checks(ev, n_search)
    subs = ev["subperiods"]
    judged = [v for v in subs.values() if v.get("status") != ST_BLOCKED]
    if ev["n_periods"] < MIN_PERIODS_VALID or len(judged) < 2:
        return ST_BLOCKED, "insufficient history to judge out-of-sample", checks, metrics

    ns = ev["net_sharpe_25bps"] or -9.0
    beats_spy_full = (ev["net_sharpe_minus_spy"] or -9.0) > 0
    cost50 = ev["cost_sharpe"].get(int(COST_ROBUST_BPS))
    cost_robust = cost50 is not None and cost50 > 0
    recent_beat = checks["beats_spy_recent_2015_2026"]

    if all(checks.values()):
        return (ST_CONFIRMED,
                "passes every CONFIRMED_SIGNAL gate (benchmark margins, 2/3 holdouts incl. recent, "
                "rolling>=60%, drawdown, turnover, holdings, leakage, placebo, multiple-testing)",
                checks, metrics)

    failed = [k for k, v in checks.items() if not v]
    # REJECTED if it fails a core benchmark / cost / recency gate
    if (ns <= 0) or (not beats_spy_full) or (not recent_beat) or (not cost_robust):
        why = []
        if ns <= 0 or not beats_spy_full:
            why.append("does not beat SPY full-sample net of cost")
        if not recent_beat:
            why.append("loses to SPY in the most recent 2015-2026 holdout")
        if not cost_robust:
            why.append(f"not net-positive at {int(COST_ROBUST_BPS)}bps")
        return ST_REJECTED, "; ".join(why) + f"; failed gates={failed}", checks, metrics

    # positive, beats SPY full-sample + recently, cost-robust, but misses a confirmation margin
    return (ST_WEAK,
            f"positive and beats SPY recently/full-sample but misses confirmation gate(s): {failed}",
            checks, metrics)


# --------------------------------------------------------------------------- #
# Experiment representation (registered BEFORE scoring).
# --------------------------------------------------------------------------- #
@dataclass
class Experiment:
    experiment_id: str
    cycle: int
    category: str            # CONFIRM | SCAN | ROBUSTNESS | NON_MOMENTUM | CHALLENGE | RISK_STRESS
    owning_agent: str
    family: str
    is_challenge: bool
    is_non_momentum: bool
    hypothesis: str
    score_key: str
    quantile: str
    gate_key: Optional[str]
    max_pos: float
    scope: str               # full | holdout:LABEL | cost@Nbps | breadth
    success_gate: str
    stop_condition: str
    challenges: str = ""      # the lead/family this experiment challenges (if any)
    # filled in after scoring:
    status: str = ""
    reason: str = ""
    finding: str = ""

    def ev_key(self) -> Tuple[str, str, Optional[str], float]:
        return (self.score_key, self.quantile, self.gate_key, self.max_pos)


def _mk(eid, cycle, category, family, score_key, quantile, hypothesis, success_gate,
        stop_condition, *, gate_key=None, max_pos=MAX_POSITION, scope="full",
        challenges="") -> Experiment:
    is_chal = category in ("CHALLENGE", "RISK_STRESS")
    return Experiment(
        experiment_id=eid, cycle=cycle, category=category,
        owning_agent=agent_for(score_key, category), family=family,
        is_challenge=is_chal, is_non_momentum=is_non_momentum_family(family),
        hypothesis=hypothesis, score_key=score_key, quantile=quantile,
        gate_key=gate_key, max_pos=max_pos, scope=scope,
        success_gate=success_gate, stop_condition=stop_condition, challenges=challenges)


# Priority leads carried in from Phase 8-B (NOT confirmed — to be challenged on broad data).
PRIORITY_LEADS = [("downside_vol", FAM_LOW_VOL), ("low_vol", FAM_LOW_VOL),
                  ("vol_adj_mom", FAM_VAM)]


def _challenge_block(cycle: int, base_id: str, score_key: str, quantile: str,
                     family: str, gate_key=None, max_pos=MAX_POSITION) -> List[Experiment]:
    """Validation-skeptic challenge set for one candidate: per-holdout beats-SPY + cost stress.
    These try to DISPROVE the lead; they are diagnostics, never promoted."""
    out: List[Experiment] = []
    for lbl in SUBPERIODS:
        out.append(_mk(f"{base_id}.H{lbl[:4]}", cycle, "CHALLENGE", family, score_key, quantile,
                       hypothesis=f"DISPROVE: does {score_key} {quantile} beat SPY in {lbl}?",
                       success_gate=f"net Sharpe in {lbl} > SPY", stop_condition="report regardless",
                       gate_key=gate_key, max_pos=max_pos, scope=f"holdout:{lbl}", challenges=base_id))
    for bps in COST_CHALLENGE_BPS:
        out.append(_mk(f"{base_id}.C{bps}", cycle, "CHALLENGE", family, score_key, quantile,
                       hypothesis=f"DISPROVE: is {score_key} {quantile} still net-positive at {bps}bps?",
                       success_gate=f"net Sharpe@{bps}bps > 0", stop_condition="report regardless",
                       gate_key=gate_key, max_pos=max_pos, scope=f"cost@{bps}bps", challenges=base_id))
    return out


# --------------------------------------------------------------------------- #
# Cycle planning. Allocation is conditioned on PRIOR-cycle outcomes (a research-director
# decision), but factor definitions and signs are NEVER changed after seeing results.
# --------------------------------------------------------------------------- #
def plan_cycle_1() -> List[Experiment]:
    exps: List[Experiment] = []
    # confirm the three carried-in 8-B leads (q5 + q10) on the broad universe
    confirm = [
        ("downside_vol", FAM_LOW_VOL), ("low_vol", FAM_LOW_VOL), ("vol_adj_mom", FAM_VAM),
    ]
    n = 1
    for sk, fam in confirm:
        for q in ("top_quintile", "top_decile"):
            exps.append(_mk(f"C1F{n:02d}", 1, "CONFIRM", fam, sk, q,
                            hypothesis=f"Confirm 8-B lead {sk} ({q}) on broad survivorship-aware universe",
                            success_gate="CONFIRMED_SIGNAL gate", stop_condition="REJECTED if loses to SPY recently",
                            challenges=""))
            n += 1
    # broad first scan across the remaining allowed families (top_quintile)
    scan = [
        ("mom_12_1", FAM_MOM), ("mom_6_1", FAM_MOM), ("rev_losers", FAM_REV),
        ("trend_score", FAM_TREND), ("trend_ma12", FAM_TREND),
        ("illiquidity", FAM_LIQ), ("high_liquidity", FAM_LIQ),
        ("sector_rel_mom", FAM_SECTOR), ("rel_strength", FAM_MARKET),
    ]
    for sk, fam in scan:
        exps.append(_mk(f"C1F{n:02d}", 1, ("NON_MOMENTUM" if is_non_momentum_family(fam) else "SCAN"),
                        fam, sk, "top_quintile",
                        hypothesis=f"Scan family {fam} via {sk} (top_quintile) on broad universe",
                        success_gate="CONFIRMED_SIGNAL gate", stop_condition="REJECTED if loses to SPY recently"))
        n += 1
    # challenge the three priority leads (q5)
    for sk, fam in PRIORITY_LEADS:
        exps.extend(_challenge_block(1, f"C1X_{sk}", sk, "top_quintile", fam))
    return exps


def plan_cycle_2(surviving_keys: List[str], surviving_families: set) -> List[Experiment]:
    """Robustness + remaining non-momentum on families that showed promise. Allocation only."""
    exps: List[Experiment] = []
    n = 1

    def add(sk, fam, q, category, hyp, gate_key=None):
        nonlocal n
        exps.append(_mk(f"C2F{n:02d}", 2, category, fam, sk, q, hypothesis=hyp,
                        success_gate="CONFIRMED_SIGNAL gate",
                        stop_condition="REJECTED if loses to SPY recently", gate_key=gate_key))
        n += 1

    if FAM_LOW_VOL in surviving_families:
        add("low_vol_6", FAM_LOW_VOL, "top_quintile", "ROBUSTNESS", "Low-vol robustness: 6m window q5")
        add("low_vol_6", FAM_LOW_VOL, "top_decile", "ROBUSTNESS", "Low-vol robustness: 6m window q10")
        add("downside_vol_6", FAM_LOW_VOL, "top_quintile", "ROBUSTNESS", "Downside-vol robustness: 6m window q5")
        add("downside_vol_6", FAM_LOW_VOL, "top_decile", "ROBUSTNESS", "Downside-vol robustness: 6m window q10")
        add("low_vol", FAM_LOW_VOL, "top_quintile", "ROBUSTNESS",
            "Low-vol q5 with liquidity-eligibility gate (tradability)", gate_key="liq_gate")
        add("downside_vol", FAM_LOW_VOL, "top_quintile", "ROBUSTNESS",
            "Downside-vol q5 with liquidity-eligibility gate (tradability)", gate_key="liq_gate")
    if FAM_VAM in surviving_families:
        add("vol_adj_mom_6", FAM_VAM, "top_quintile", "ROBUSTNESS", "Vol-adj 6-1 momentum q5 (robustness)")
        add("vol_adj_mom_6", FAM_VAM, "top_decile", "ROBUSTNESS", "Vol-adj 6-1 momentum q10 (robustness)")
        add("vol_adj_mom", FAM_VAM, "top_quintile", "ROBUSTNESS",
            "Vol-adj momentum q5 with liquidity-eligibility gate", gate_key="liq_gate")
    if FAM_MOM in surviving_families:
        add("mom_12_1", FAM_MOM, "top_decile", "ROBUSTNESS", "12-1 momentum q10 (robustness)")
        add("mom_3", FAM_MOM, "top_quintile", "ROBUSTNESS", "3-month momentum q5 (robustness)")
    if FAM_TREND in surviving_families:
        add("trend_score", FAM_TREND, "top_decile", "ROBUSTNESS", "Breakout proximity q10 (robustness)")
        add("trend_ma12", FAM_TREND, "top_decile", "ROBUSTNESS", "Above-12m-MA q10 (robustness)")
    if FAM_REV in surviving_families:
        add("rev_losers", FAM_REV, "top_decile", "ROBUSTNESS", "Short-term reversal q10 (robustness)")
    if FAM_LIQ in surviving_families:
        add("illiquidity", FAM_LIQ, "top_decile", "ROBUSTNESS", "Illiquidity premium q10 (robustness)")

    # challenge the strongest 2 carried/surviving candidate keys this cycle
    for sk in surviving_keys[:2]:
        fam = FAMILY_OF.get(sk, FAM_TREND)
        exps.extend(_challenge_block(2, f"C2X_{sk}", sk, "top_quintile", fam))
    return exps


def plan_cycle_3(top_leads: List[str]) -> List[Experiment]:
    """Final risk/breadth/cost stress on the best remaining leads (RISK_STRESS + CHALLENGE)."""
    exps: List[Experiment] = []
    n = 1
    for sk in top_leads[:2]:
        fam = FAMILY_OF.get(sk, FAM_TREND)
        exps.append(_mk(f"C3R{n:02d}", 3, "RISK_STRESS", fam, sk, "concentrated",
                        hypothesis=f"Breadth/capacity stress: concentrate {sk} into ~top 1/20 names",
                        success_gate="net Sharpe stable vs q5", stop_condition="report regardless",
                        scope="breadth", challenges=sk)); n += 1
        exps.append(_mk(f"C3R{n:02d}", 3, "RISK_STRESS", fam, sk, "broad",
                        hypothesis=f"Breadth stress: broaden {sk} to ~top 1/3 names",
                        success_gate="net Sharpe stable vs q5", stop_condition="report regardless",
                        scope="breadth", challenges=sk)); n += 1
        # final extreme-cost challenge
        exps.append(_mk(f"C3X{n:02d}", 3, "CHALLENGE", fam, sk, "top_quintile",
                        hypothesis=f"DISPROVE: is {sk} q5 still net-positive at 100bps?",
                        success_gate="net Sharpe@100bps > 0", stop_condition="report regardless",
                        scope="cost@100bps", challenges=sk)); n += 1
    return exps


# --------------------------------------------------------------------------- #
# Campaign driver.
# --------------------------------------------------------------------------- #
@dataclass
class CampaignState:
    registry: List[Experiment] = field(default_factory=list)
    evcache: Dict[tuple, dict] = field(default_factory=dict)
    family_count: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    cycle_log: List[dict] = field(default_factory=list)
    skipped: List[dict] = field(default_factory=list)

    @property
    def n_total(self) -> int:
        return len(self.registry)


def _can_add(state: CampaignState, exp: Experiment) -> Tuple[bool, str]:
    if state.n_total >= MAX_TOTAL_EXPERIMENTS:
        return False, f"campaign budget exhausted ({MAX_TOTAL_EXPERIMENTS})"
    if state.family_count[exp.family] >= MAX_PER_FAMILY:
        return False, f"family cap ({MAX_PER_FAMILY}) reached for {exp.family}"
    return True, ""


def _register(state: CampaignState, specs: List[Experiment]) -> List[Experiment]:
    """Pre-register experiments (respecting caps) BEFORE any scoring. Skips are logged, never silent."""
    added: List[Experiment] = []
    for spec in specs:
        ok, why = _can_add(state, spec)
        if not ok:
            state.skipped.append({"experiment_id": spec.experiment_id, "family": spec.family,
                                  "scope": spec.scope, "reason_skipped": why})
            continue
        state.registry.append(spec)
        state.family_count[spec.family] += 1
        added.append(spec)
    return added


def _get_ev(state: CampaignState, exp: Experiment, panel: Panel, blocks: dict,
            forward: pd.DataFrame, sector_map: dict, delisted: set, *, placebo: bool) -> dict:
    key = exp.ev_key()
    if key not in state.evcache:
        state.evcache[key] = evaluate_signal(
            blocks, forward, panel.membership, panel.spy_monthly, sector_map, delisted,
            score_key=exp.score_key, quantile=exp.quantile, gate_key=exp.gate_key,
            max_pos=exp.max_pos, with_placebo=placebo)
    return state.evcache[key]


def _base_ev_key(exp: Experiment) -> Tuple[str, str, Optional[str], float]:
    """The full-sample ev a challenge experiment refers to (same signal, scope='full')."""
    return (exp.score_key, exp.quantile, exp.gate_key, exp.max_pos)


def _score_experiments(state: CampaignState, added: List[Experiment], panel: Panel, blocks: dict,
                       forward: pd.DataFrame, sector_map: dict, delisted: set, n_search: int) -> None:
    # 1) full candidates first (build the ev cache, with placebo)
    for exp in added:
        if exp.scope == "full":
            ev = _get_ev(state, exp, panel, blocks, forward, sector_map, delisted, placebo=True)
            status, reason, _checks, _m = classify_8c(ev, n_search)
            exp.status, exp.reason = status, reason
            exp.finding = (f"net_sharpe@25={ev['net_sharpe_25bps']} vs SPY {ev['spy_sharpe']} "
                           f"(+{ev['net_sharpe_minus_spy']}); recent_beats_spy="
                           f"{_m['recent_beats_spy']}; rolling_beat={_m['rolling_frac_beat_spy']}")
        elif exp.scope == "breadth":
            ev = _get_ev(state, exp, panel, blocks, forward, sector_map, delisted, placebo=False)
            exp.status = ST_DIAGNOSTIC
            exp.reason = "breadth/capacity stress (reported, not promoted)"
            exp.finding = (f"net_sharpe@25={ev['net_sharpe_25bps']}, dd={ev['net_max_drawdown_25bps']}, "
                           f"avg_names={ev['avg_names']}")
    # 2) challenge experiments referencing a base full-sample ev
    for exp in added:
        if exp.scope.startswith("holdout:") or exp.scope.startswith("cost@"):
            base = state.evcache.get(_base_ev_key(exp))
            if base is None:   # base wasn't scored (capped out) -> evaluate it on demand
                base = _get_ev(state, exp, panel, blocks, forward, sector_map, delisted, placebo=True)
            exp.status = ST_DIAGNOSTIC
            if exp.scope.startswith("holdout:"):
                lbl = exp.scope.split(":", 1)[1]
                sub = base["subperiods"].get(lbl, {})
                if sub.get("status") == ST_BLOCKED:
                    exp.finding = f"{lbl}: insufficient periods to judge"
                    exp.reason = "challenge inconclusive (too few periods)"
                else:
                    beat = bool(sub.get("beats_spy"))
                    exp.finding = (f"{lbl}: net_sharpe={sub.get('net_sharpe')} vs SPY "
                                   f"{sub.get('spy_sharpe')} -> beats_spy={beat}")
                    exp.reason = ("lead survives this holdout" if beat
                                  else "lead FAILS to beat SPY in this holdout")
            else:
                bps = int(exp.scope.split("@")[1].replace("bps", ""))
                cs = base["cost_sharpe"].get(bps)
                pos = cs is not None and cs > 0
                exp.finding = f"net_sharpe@{bps}bps={cs} -> net_positive={pos}"
                exp.reason = ("lead survives this cost level" if pos
                              else "lead FAILS at this cost level")


def run_campaign(panel: Panel, n_search_seed: int = 31) -> Tuple[CampaignState, dict]:
    """Up to MAX_CYCLES autonomous cycles. n_search_seed = prior-phase search universe (8-A 18 + 8-B 13)."""
    sector_map = panel.metadata["gics_sector"].to_dict() if not panel.metadata.empty else {}
    delisted = (set(panel.metadata.index[panel.metadata["is_delisted"]])
                if "is_delisted" in panel.metadata.columns else set())
    forward = panel.close.pct_change().shift(-1)
    blocks = build_8c_blocks(panel.close, panel.dollar_vol, panel.spy_monthly, sector_map)

    state = CampaignState()

    def candidate_summary() -> dict:
        cands = [e for e in state.registry if e.scope == "full"]
        confirmed = [e for e in cands if e.status == ST_CONFIRMED]
        weak = [e for e in cands if e.status == ST_WEAK]
        rejected = [e for e in cands if e.status == ST_REJECTED]
        blocked = [e for e in cands if e.status == ST_BLOCKED]
        return {"n_candidates": len(cands), "n_confirmed": len(confirmed),
                "n_weak": len(weak), "n_rejected": len(rejected), "n_blocked": len(blocked),
                "confirmed": confirmed, "weak": weak}

    def surviving(cands_status=("CONFIRMED_SIGNAL", "WEAK")) -> Tuple[List[str], set]:
        keys, fams = [], set()
        scored = [e for e in state.registry if e.scope == "full" and e.status in cands_status]
        # rank by net excess over SPY (best first) using ev cache
        def excess(e):
            ev = state.evcache.get(e.ev_key(), {})
            return ev.get("net_sharpe_minus_spy") or -9.0
        for e in sorted(scored, key=excess, reverse=True):
            keys.append(e.score_key)
            fams.add(e.family)
        return keys, fams

    stop_reason = ""
    for cycle in range(1, MAX_CYCLES + 1):
        n_search_now = n_search_seed + sum(1 for e in state.registry if e.scope == "full")
        if cycle == 1:
            specs = plan_cycle_1()
        elif cycle == 2:
            keys, fams = surviving()
            if not fams:
                stop_reason = "all cycle-1 families rejected on broad data; no robustness warranted"
                state.cycle_log.append(_cycle_log_row(cycle, 0, 0, candidate_summary(), stop_reason))
                break
            specs = plan_cycle_2(keys, fams)
        else:
            keys, _ = surviving()
            if not keys:
                stop_reason = "no surviving leads to stress; campaign rejects current families"
                state.cycle_log.append(_cycle_log_row(cycle, 0, 0, candidate_summary(), stop_reason))
                break
            specs = plan_cycle_3(keys)

        # pre-register BEFORE scoring
        added = _register(state, specs)
        # the search-universe size used for multiple-testing grows as full candidates accrue
        n_search_final = n_search_seed + sum(1 for e in state.registry if e.scope == "full")
        _score_experiments(state, added, panel, blocks, forward, sector_map, delisted, n_search_final)

        summ = candidate_summary()
        decision = _cycle_decision(cycle, summ)
        state.cycle_log.append(_cycle_log_row(cycle, len(added), len(state.skipped), summ, decision))
        if summ["n_confirmed"] >= 1 and cycle >= 2:
            # a signal confirmed and has been (or will be in cycle 3) stress-tested
            if cycle == MAX_CYCLES:
                stop_reason = "confirmed signal stress-tested; campaign complete"
                break
            # let cycle 3 run as the stress cycle, then stop
            continue
        if cycle == MAX_CYCLES:
            stop_reason = "campaign budget / cycle ceiling reached"
            break

    meta = {"n_search_universe": n_search_seed + sum(1 for e in state.registry if e.scope == "full"),
            "n_search_seed": n_search_seed, "stop_reason": stop_reason,
            "sector_map_size": len(sector_map), "n_delisted": len(delisted)}
    return state, meta


def _cycle_decision(cycle: int, summ: dict) -> str:
    if summ["n_confirmed"] >= 1:
        return f"CONTINUE_TO_STRESS: {summ['n_confirmed']} confirmed candidate(s)"
    if summ["n_weak"] >= 1:
        return f"CONTINUE: {summ['n_weak']} weak lead(s), no confirmation yet"
    return "REJECT_TREND: no positive leads survive"


def _cycle_log_row(cycle, n_added, n_skipped, summ, decision) -> dict:
    return {
        "cycle": cycle, "experiments_added": n_added, "experiments_skipped_cumulative": n_skipped,
        "candidates_scored": summ["n_candidates"], "n_confirmed": summ["n_confirmed"],
        "n_weak": summ["n_weak"], "n_rejected": summ["n_rejected"], "n_blocked": summ["n_blocked"],
        "director_decision": decision,
    }


# --------------------------------------------------------------------------- #
# Budget / guardrail accounting.
# --------------------------------------------------------------------------- #
def budget_report(state: CampaignState) -> dict:
    n = state.n_total
    n_chal = sum(1 for e in state.registry if e.is_challenge)
    n_nonmom = sum(1 for e in state.registry if e.is_non_momentum)
    per_family = {fam: int(c) for fam, c in sorted(state.family_count.items())}
    return {
        "experiments_registered": n,
        "max_experiments": MAX_TOTAL_EXPERIMENTS,
        "n_challenge": n_chal,
        "challenge_fraction": _round(n_chal / n if n else 0.0, 3),
        "challenge_min_required": CHALLENGE_MIN_FRAC,
        "challenge_ok": bool(n and (n_chal / n) >= CHALLENGE_MIN_FRAC),
        "n_non_momentum": n_nonmom,
        "non_momentum_fraction": _round(n_nonmom / n if n else 0.0, 3),
        "non_momentum_min_required": NON_MOMENTUM_MIN_FRAC,
        "non_momentum_ok": bool(n and (n_nonmom / n) >= NON_MOMENTUM_MIN_FRAC),
        "per_family_counts": per_family,
        "max_per_family": MAX_PER_FAMILY,
        "per_family_ok": all(c <= MAX_PER_FAMILY for c in per_family.values()),
        "n_skipped_for_caps": len(state.skipped),
        "all_registered_before_scoring": True,
    }


# --------------------------------------------------------------------------- #
# Decision rule -> recommendation.
# --------------------------------------------------------------------------- #
def derive_recommendation(panel_ok: bool, orch_ok: bool, state: CampaignState) -> Tuple[str, str]:
    if not panel_ok:
        return REC_DATA_BLOCKED, "panel missing/corrupt — cannot test"
    if not orch_ok:
        return REC_ORCH_BLOCKED, "agent system not ready to orchestrate"
    cands = [e for e in state.registry if e.scope == "full"]
    n_conf = sum(1 for e in cands if e.status == ST_CONFIRMED)
    n_weak = sum(1 for e in cands if e.status == ST_WEAK)
    n_scored = sum(1 for e in cands if e.status in (ST_CONFIRMED, ST_WEAK, ST_REJECTED))
    if not cands or n_scored == 0:
        return REC_NEEDS_REVIEW, "no candidate was scored"
    if n_conf >= 1:
        return REC_CONFIRMED, f"{n_conf} signal(s) passed the full CONFIRMED_SIGNAL gate"
    if n_weak >= 1:
        return REC_WEAK, f"{n_weak} positive lead(s) miss major gates; keep research-only"
    return REC_REJECTED, "all tested families fail the benchmark/cost/recency gates on broad data"


# --------------------------------------------------------------------------- #
# Universe discovery + selection (data-foundation + universe-construction agents).
# Norgate is queried best-effort; falls back to the persisted panel build report so the
# engine stays runnable (and testable) without importing norgatedata.
# --------------------------------------------------------------------------- #
def discover_universes(panel_root: Path = PANEL_ROOT) -> Tuple[List[dict], str]:
    rows: List[dict] = []
    source = "norgate"
    try:
        import norgatedata as nd  # local provider only; never installed here
        wls = set(nd.watchlists())
        for name in UNIVERSE_PREFERENCE:
            present = name in wls
            members = None
            if present:
                try:
                    members = len(nd.watchlist_symbols(name))
                except Exception:  # pragma: no cover - defensive
                    members = None
            rows.append({"universe": name, "available": bool(present),
                         "superset_symbols": members if members is not None else "",
                         "feasible": bool(present and (members or 0) >= MIN_UNIVERSE_MEMBERS),
                         "index_name": UNIVERSE_INDEX_NAME.get(name, "")})
    except Exception:
        source = "panel_build_report"
        rep = _read_build_report(panel_root)
        sel = rep.get("universe_watchlist", "")
        for name in UNIVERSE_PREFERENCE:
            is_sel = (name == sel)
            rows.append({"universe": name,
                         "available": True if is_sel else "not_probed",
                         "superset_symbols": rep.get("superset_symbols", "") if is_sel else "",
                         "feasible": True if is_sel else "not_probed",
                         "index_name": UNIVERSE_INDEX_NAME.get(name, "")})
    return rows, source


def select_universe(universe_rows: List[dict]) -> dict:
    """Choose the broadest feasible universe in preference order."""
    order = {n: i for i, n in enumerate(UNIVERSE_PREFERENCE)}
    feasible = [r for r in universe_rows if r.get("feasible") is True]
    if not feasible:
        # fall back to whatever is marked available (e.g. the pre-built panel's universe)
        feasible = [r for r in universe_rows if r.get("available") is True]
    feasible.sort(key=lambda r: order.get(r["universe"], 999))
    chosen = feasible[0] if feasible else {"universe": UNIVERSE_PREFERENCE[0]}
    return {
        "selected_universe": chosen["universe"],
        "index_name": UNIVERSE_INDEX_NAME.get(chosen["universe"], ""),
        "superset_symbols": chosen.get("superset_symbols", ""),
        "selection_rule": "broadest feasible Current & Past universe in documented preference order",
        "preference_order": list(UNIVERSE_PREFERENCE),
    }


def _read_build_report(panel_root: Path) -> dict:
    p = panel_root / "_build_report.json"
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:  # pragma: no cover - defensive
            return {}
    return {}


# --------------------------------------------------------------------------- #
# Panel build-or-load (cached). For this run the panel is pre-built on D:.
# --------------------------------------------------------------------------- #
def build_or_load_panel(panel_root: Path = PANEL_ROOT, *, allow_build: bool = False,
                        universe: str = UNIVERSE_PREFERENCE[0]) -> Tuple[Panel, bool]:
    """Returns (panel, built_now). If the 5 panel CSVs are present, load them (cache hit).
    If absent and allow_build is True, build from Norgate and persist. If absent and build is
    not allowed/feasible, return a blocked Panel so the caller can emit FULL_PANEL_BUILD_REQUIRED."""
    have_all = all((panel_root / fn).exists() for fn in PANEL_FILES.values())
    if have_all:
        return load_panel(panel_root), False
    if not allow_build:
        empty = pd.DataFrame()
        return Panel(empty, empty, empty, empty, pd.Series(dtype=float), False,
                     [f"panel not built at {panel_root}; build required"]), False
    # build path (reuses 8-A build_panel + persist schema) — not exercised in the cached run
    import norgatedata as nd
    adapter = P8A.NorgateAdapter()
    syms = list(nd.watchlist_symbols(universe))
    res = P8A.build_panel(adapter, syms, index_name=UNIVERSE_INDEX_NAME.get(universe, "S&P 500"))
    panel_root.mkdir(parents=True, exist_ok=True)
    res.monthly_close.to_csv(panel_root / PANEL_FILES["monthly_close"])
    res.monthly_dollar_vol.to_csv(panel_root / PANEL_FILES["monthly_dollar_volume"])
    res.membership.to_csv(panel_root / PANEL_FILES["membership"])
    res.metadata.to_csv(panel_root / PANEL_FILES["metadata"])
    res.spy_monthly.to_csv(panel_root / PANEL_FILES["spy_monthly"])
    return load_panel(panel_root), True


# --------------------------------------------------------------------------- #
# Artifact assembly.
# --------------------------------------------------------------------------- #
@dataclass
class OutPaths:
    out_dir: Path

    def p(self, name: str) -> Path:
        return self.out_dir / name


def _ev_of(state: CampaignState, exp: Experiment) -> dict:
    return state.evcache.get(exp.ev_key(), {})


def _scoreboard_rows(state: CampaignState, n_search: int) -> List[dict]:
    rows: List[dict] = []
    for e in state.registry:
        ev = _ev_of(state, e)
        rows.append({
            "experiment_id": e.experiment_id, "cycle": e.cycle, "category": e.category,
            "family": e.family, "owning_agent": e.owning_agent, "is_challenge": e.is_challenge,
            "is_non_momentum": e.is_non_momentum, "score_key": e.score_key, "quantile": e.quantile,
            "gate_key": e.gate_key or "", "scope": e.scope, "status": e.status,
            "net_sharpe_25bps": ev.get("net_sharpe_25bps", ""),
            "spy_sharpe": ev.get("spy_sharpe", ""),
            "ew_universe_sharpe": ev.get("ew_universe_sharpe", ""),
            "net_sharpe_minus_spy": ev.get("net_sharpe_minus_spy", ""),
            "net_max_drawdown_25bps": ev.get("net_max_drawdown_25bps", ""),
            "one_sided_turnover": ev.get("one_sided_turnover", ""),
            "avg_names": ev.get("avg_names", ""),
            "rolling_frac_beat_spy": (ev.get("rolling") or {}).get("frac_beat_spy", ""),
            "finding": e.finding, "reason": e.reason, "challenges": e.challenges,
        })
    return rows


_SCOREBOARD_COLS = ["experiment_id", "cycle", "category", "family", "owning_agent", "is_challenge",
                    "is_non_momentum", "score_key", "quantile", "gate_key", "scope", "status",
                    "net_sharpe_25bps", "spy_sharpe", "ew_universe_sharpe", "net_sharpe_minus_spy",
                    "net_max_drawdown_25bps", "one_sided_turnover", "avg_names",
                    "rolling_frac_beat_spy", "finding", "reason", "challenges"]


def _candidate_rows(state: CampaignState, status: str, n_search: int) -> List[dict]:
    rows: List[dict] = []
    for e in state.registry:
        if e.scope != "full" or e.status != status:
            continue
        ev = _ev_of(state, e)
        _checks, m = confirmed_checks(ev, n_search)
        rows.append({
            "experiment_id": e.experiment_id, "cycle": e.cycle, "family": e.family,
            "score_key": e.score_key, "quantile": e.quantile, "gate_key": e.gate_key or "",
            "status": e.status,
            "net_sharpe_25bps": m["net_sharpe_25bps"], "spy_sharpe": m["spy_sharpe"],
            "ew_universe_sharpe": m["ew_universe_sharpe"],
            "net_sharpe_minus_spy": m["net_sharpe_minus_spy"],
            "net_sharpe_minus_ew": m["net_sharpe_minus_ew"],
            "n_holdouts_beat_spy": m["n_holdouts_beat_spy"], "recent_beats_spy": m["recent_beats_spy"],
            "rolling_frac_beat_spy": m["rolling_frac_beat_spy"],
            "net_max_drawdown_25bps": m["net_max_drawdown_25bps"], "spy_max_drawdown": m["spy_max_drawdown"],
            "one_sided_turnover": m["one_sided_turnover"], "avg_names": m["avg_names"],
            "net_sharpe_minus_placebo": m["net_sharpe_minus_placebo"],
            "delisted_weight_avg": m["delisted_weight_avg"], "top_sector": m["top_sector"],
            "top_sector_weight": m["top_sector_weight"], "reason": e.reason,
        })
    return rows


def _failed_rows(state: CampaignState) -> List[dict]:
    """REJECTED candidates + every challenge/stress that disproved a lead — failures are NOT hidden."""
    rows: List[dict] = []
    for e in state.registry:
        is_reject = e.scope == "full" and e.status == ST_REJECTED
        is_failed_challenge = (e.is_challenge and "FAIL" in (e.reason or "").upper())
        if is_reject or is_failed_challenge:
            rows.append({"experiment_id": e.experiment_id, "cycle": e.cycle, "category": e.category,
                         "family": e.family, "score_key": e.score_key, "quantile": e.quantile,
                         "scope": e.scope, "status": e.status or ST_DIAGNOSTIC,
                         "finding": e.finding, "reason": e.reason, "challenges": e.challenges})
    return rows


def _validation_skeptic_rows(state: CampaignState) -> List[dict]:
    rows: List[dict] = []
    for e in state.registry:
        if not e.is_challenge:
            continue
        rows.append({"experiment_id": e.experiment_id, "cycle": e.cycle, "challenges": e.challenges,
                     "scope": e.scope, "hypothesis": e.hypothesis, "finding": e.finding,
                     "verdict": e.reason})
    return rows


def _risk_portfolio_rows(state: CampaignState, n_search: int) -> List[dict]:
    rows: List[dict] = []
    seen = set()
    for e in state.registry:
        if e.scope not in ("full", "breadth"):
            continue
        key = e.ev_key()
        if key in seen:
            continue
        seen.add(key)
        ev = _ev_of(state, e)
        if not ev:
            continue
        risk = ev.get("risk", {})
        rows.append({
            "experiment_id": e.experiment_id, "family": e.family, "score_key": e.score_key,
            "quantile": e.quantile, "scope": e.scope, "status": e.status,
            "net_max_drawdown_25bps": ev.get("net_max_drawdown_25bps"),
            "spy_max_drawdown": ev.get("spy_max_drawdown"),
            "one_sided_turnover": ev.get("one_sided_turnover"),
            "avg_names": ev.get("avg_names"), "beta_spy": risk.get("beta_spy"),
            "delisted_weight_avg": risk.get("delisted_weight_avg"),
            "top_sector": risk.get("top_sector"), "top_sector_weight": risk.get("top_sector_weight"),
        })
    return rows


def _multiple_testing_report(state: CampaignState, n_search: int) -> dict:
    n_full = sum(1 for e in state.registry if e.scope == "full")
    n_conf = sum(1 for e in state.registry if e.scope == "full" and e.status == ST_CONFIRMED)
    return {
        "search_universe_phase8a": 18,
        "search_universe_phase8b": 13,
        "search_universe_phase8c_candidates": n_full,
        "search_universe_total": n_search,
        "mt_required_excess_over_spy_sharpe": mt_required_excess(n_search),
        "mt_rule": "required net-Sharpe-over-SPY margin = 0.15 * max(1, log10(N_search)); monotonic in N",
        "additional_deflation": ("placebo (>=0.25) + out-of-sample 2/3 holdouts incl. most recent + "
                                 "rolling-10y beat-SPY >= 60% required ON TOP of the margin; borderline "
                                 "never rounded up"),
        "n_confirmed_after_correction": n_conf,
        "interpretation": (f"With {n_search} experiments in the campaign search universe a single-bar "
                           "screen would surface lucky winners; the deflated margin plus placebo, "
                           "holdout, recency and rolling gates are the correction applied before any "
                           "signal is called CONFIRMED_SIGNAL."),
    }


def _ensemble_readiness_rows(state: CampaignState) -> List[dict]:
    confirmed = [e for e in state.registry if e.scope == "full" and e.status == ST_CONFIRMED]
    n = len(confirmed)
    ready = n >= 2
    note = ("ensemble assessed only if >=2 independent signals are confirmed; "
            "no optimized ensemble weights are built (forbidden)")
    rows = [{
        "n_confirmed_signals": n,
        "ensemble_ready": ready,
        "independent_signals_required": 2,
        "optimized_weights_built": False,
        "note": note if not ready else "two+ confirmed signals; assess independence before any equal-weight combo",
    }]
    return rows


def _paper_signal_contract_rows(state: CampaignState, n_search: int) -> List[dict]:
    confirmed = [e for e in state.registry if e.scope == "full" and e.status == ST_CONFIRMED]
    if not confirmed:
        return [{"status": "NO_CONFIRMED_SIGNAL",
                 "note": "no signal passed the CONFIRMED_SIGNAL gate; no paper-research contract drafted. "
                         "No orders, no execution, no automation.",
                 "safety": "PREVIEW ONLY | NO ORDERS | AUTOMATION OFF | MANUAL REVIEW"}]
    rows = []
    for e in confirmed:
        ev = _ev_of(state, e)
        rows.append({
            "status": "DRAFT_PAPER_RESEARCH_CONTRACT", "experiment_id": e.experiment_id,
            "family": e.family, "score_key": e.score_key, "quantile": e.quantile,
            "net_sharpe_25bps": ev.get("net_sharpe_25bps"),
            "net_sharpe_minus_spy": ev.get("net_sharpe_minus_spy"),
            "rebalance": "monthly", "selection": f"{e.quantile} cross-sectional, equal-weight, cap {e.max_pos}",
            "cost_assumption_bps": int(PRIMARY_COST_BPS),
            "safety": "PREVIEW ONLY | CREATES TRADE DECISIONS ONLY | NO ORDERS | AUTOMATION OFF | MANUAL REVIEW",
            "note": "paper-research only; no orders, no execution, no automation, no broker.",
        })
    return rows


def _research_agenda_rows(state: CampaignState) -> List[dict]:
    rows: List[dict] = []
    for e in state.registry:
        rows.append({"experiment_id": e.experiment_id, "cycle": e.cycle, "category": e.category,
                     "owning_agent": e.owning_agent, "family": e.family, "hypothesis": e.hypothesis,
                     "score_key": e.score_key, "quantile": e.quantile, "gate_key": e.gate_key or "",
                     "scope": e.scope, "success_gate": e.success_gate,
                     "stop_condition": e.stop_condition, "challenges": e.challenges})
    return rows


def _agent_allocation_rows(state: CampaignState, panel: Panel, selected: dict) -> List[dict]:
    counts: Dict[str, int] = defaultdict(int)
    chal: Dict[str, int] = defaultdict(int)
    for e in state.registry:
        counts[e.owning_agent] += 1
        if e.is_challenge:
            chal[e.owning_agent] += 1
    # static owners that don't "run experiments" but own pipeline stages
    static = {
        DIR_A: "owns cycle agenda, budget allocation, stop/go decisions",
        DATA_A: f"owns Norgate panel build + data quality ({selected.get('selected_universe','')})",
        UNI_A: "owns point-in-time membership masks + tradability filters",
        FEAT_A: f"owns {len(FEATURE_CATALOG)} feature definitions + leakage rules",
        ENS_A: "assesses ensemble readiness only (no optimized weights)",
        PUB_A: "drafts a paper-research contract only if >=1 signal confirmed",
    }
    rows: List[dict] = []
    for a in ALL_AGENTS:
        rows.append({"agent": a, "experiments_owned": int(counts.get(a, 0)),
                     "challenge_experiments": int(chal.get(a, 0)),
                     "role": static.get(a, "runs allocated signal experiments")})
    return rows


def _data_quality_rows(panel: Panel, build_report: dict) -> List[dict]:
    meta = panel.metadata
    n_total = int(len(meta))
    n_delisted = int(meta["is_delisted"].sum()) if "is_delisted" in meta.columns else 0
    rows = [
        {"metric": "panel_symbols_total", "value": n_total},
        {"metric": "panel_symbols_active", "value": n_total - n_delisted},
        {"metric": "panel_symbols_delisted", "value": n_delisted},
        {"metric": "survivorship_dropout_fraction",
         "value": _round(n_delisted / n_total, 4) if n_total else 0.0},
        {"metric": "n_months", "value": int(len(panel.close.index))},
        {"metric": "date_min", "value": str(panel.close.index.min())[:10] if len(panel.close.index) else ""},
        {"metric": "date_max", "value": str(panel.close.index.max())[:10] if len(panel.close.index) else ""},
        {"metric": "spy_obs", "value": int(panel.spy_monthly.notna().sum())},
        {"metric": "members_any_month",
         "value": int((panel.membership.sum(axis=1) > 0).sum()) if not panel.membership.empty else 0},
        {"metric": "max_simultaneous_members",
         "value": int(panel.membership.sum(axis=1).max()) if not panel.membership.empty else 0},
        {"metric": "missing_close_fraction",
         "value": _round(float(panel.close.isna().to_numpy().mean()), 4) if panel.close.size else 1.0},
        {"metric": "build_seconds", "value": build_report.get("build_seconds", "")},
    ]
    return rows


def _panel_build_report_rows(panel: Panel, build_report: dict, selected: dict, built_now: bool) -> List[dict]:
    return [
        {"field": "selected_universe", "value": selected.get("selected_universe", "")},
        {"field": "index_name", "value": selected.get("index_name", "")},
        {"field": "superset_symbols", "value": build_report.get("superset_symbols",
                                                                 selected.get("superset_symbols", ""))},
        {"field": "current_members", "value": build_report.get("current_members", "")},
        {"field": "panel_symbols_total", "value": int(len(panel.metadata))},
        {"field": "n_months", "value": int(len(panel.close.index))},
        {"field": "date_range",
         "value": (f"{str(panel.close.index.min())[:10]}..{str(panel.close.index.max())[:10]}"
                   if len(panel.close.index) else "")},
        {"field": "panel_root", "value": str(PANEL_ROOT).replace("\\", "/")},
        {"field": "built_this_run", "value": built_now},
        {"field": "note", "value": "large data on D: (not committed); repo holds summaries only"},
    ]


def _universe_manifest_rows(selected: dict, source: str) -> List[dict]:
    rows = [
        {"field": "selected_universe", "value": selected.get("selected_universe", "")},
        {"field": "index_name", "value": selected.get("index_name", "")},
        {"field": "superset_symbols", "value": selected.get("superset_symbols", "")},
        {"field": "selection_rule", "value": selected.get("selection_rule", "")},
        {"field": "discovery_source", "value": source},
        {"field": "preference_order", "value": " > ".join(UNIVERSE_PREFERENCE)},
    ]
    return rows


def _feature_catalog_rows() -> List[dict]:
    return [{"feature": f, "family": fam, "definition": d, "description": desc,
             "information_set": iset, "registered_before_scoring": True}
            for (f, fam, d, desc, iset) in FEATURE_CATALOG]


def _registry_rows(state: CampaignState) -> List[dict]:
    return [{"experiment_id": e.experiment_id, "cycle": e.cycle, "category": e.category,
             "owning_agent": e.owning_agent, "family": e.family, "is_challenge": e.is_challenge,
             "is_non_momentum": e.is_non_momentum, "score_key": e.score_key, "quantile": e.quantile,
             "gate_key": e.gate_key or "", "max_pos": e.max_pos, "scope": e.scope,
             "hypothesis": e.hypothesis, "success_gate": e.success_gate,
             "stop_condition": e.stop_condition, "registered_before_scoring": True,
             "status_after_scoring": e.status} for e in state.registry]


# --------------------------------------------------------------------------- #
# Report + emit.
# --------------------------------------------------------------------------- #
def _assemble_report(started, state, meta, panel, panel_ok, orch_ok, orch_problems,
                     selected, source, recommendation, rec_reason, build_report) -> dict:
    n_search = meta["n_search_universe"]
    budget = budget_report(state)
    cands = [e for e in state.registry if e.scope == "full"]
    confirmed = [e.experiment_id for e in cands if e.status == ST_CONFIRMED]
    weak = [e.experiment_id for e in cands if e.status == ST_WEAK]
    rejected = [e.experiment_id for e in cands if e.status == ST_REJECTED]
    return {
        "phase": PHASE,
        "generated_utc": started,
        "objective": OBJECTIVE,
        "recommendation": recommendation,
        "recommendation_reason": rec_reason,
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "allowed_statuses": list(ALLOWED_STATUSES),
        "orchestrator_ready": orch_ok,
        "orchestrator_problems": orch_problems,
        "panel_ok": panel_ok,
        "panel_issues": panel.issues,
        "selected_universe": selected,
        "universe_discovery_source": source,
        "panel_shape": {"symbols": int(len(panel.metadata)), "months": int(len(panel.close.index)),
                        "date_min": str(panel.close.index.min())[:10] if len(panel.close.index) else "",
                        "date_max": str(panel.close.index.max())[:10] if len(panel.close.index) else "",
                        "active": int(len(panel.metadata) - (int(panel.metadata["is_delisted"].sum())
                                  if "is_delisted" in panel.metadata.columns else 0)),
                        "delisted": int(panel.metadata["is_delisted"].sum())
                        if "is_delisted" in panel.metadata.columns else 0},
        "cycles_run": len([c for c in state.cycle_log]),
        "cycle_log": state.cycle_log,
        "stop_reason": meta["stop_reason"],
        "budget": budget,
        "confirmed_signals": confirmed,
        "weak_signals": weak,
        "rejected_signals": rejected,
        "n_search_universe": n_search,
        "multiple_testing": _multiple_testing_report(state, n_search),
        "confirmed_signal_gate": {
            "net_sharpe_beats_spy_by": GATE_SPY_SHARPE_MARGIN,
            "net_sharpe_beats_ew_by": GATE_EW_SHARPE_MARGIN,
            "holdouts_beat_spy_min": GATE_HOLDOUT_BEAT_MIN, "must_beat_recent_holdout": True,
            "rolling_10y_beat_frac": GATE_ROLLING_BEAT_FRAC, "dd_worse_than_spy_pp_max": GATE_DD_WORSE_PP,
            "avg_turnover_max": GATE_MAX_TURNOVER, "avg_holdings_min": GATE_MIN_HOLDINGS,
            "placebo_margin": GATE_PLACEBO_MARGIN, "multiple_testing_required": True,
        },
        "safety": {
            "research_only": True, "norgate_only": True, "packages_installed": False,
            "paper_trader_touched": False, "gcp_touched": False, "broker_or_orders": False,
            "automation": False, "optimized_weights": False, "factor_signs_modified_after_results": False,
            "regime_activation": False, "fundamentals_used": False, "failed_experiments_hidden": False,
            "committed": False, "pushed": False,
            "large_data_location": str(PANEL_ROOT).replace("\\", "/"),
        },
    }


ARTIFACTS = [
    "phase8c_autonomous_norgate_research_campaign.json", "available_norgate_universes.csv",
    "selected_universe_manifest.csv", "panel_build_report.csv", "data_quality_report.csv",
    "campaign_cycle_log.csv", "research_agenda.csv", "agent_task_allocation.csv",
    "feature_catalog.csv", "experiment_registry.csv", "all_experiments_scoreboard.csv",
    "failed_experiments.csv", "weak_signals.csv", "confirmed_signals.csv", "rejected_signals.csv",
    "validation_skeptic_report.csv", "risk_portfolio_report.csv", "multiple_testing_report.csv",
    "ensemble_readiness_report.csv", "paper_signal_contract.csv", "research_director_decision.json",
    "phase8d_next_plan.json",
]


def _emit_all(paths: OutPaths, report: dict, state: CampaignState, panel: Panel,
              selected: dict, source: str, universe_rows: List[dict], build_report: dict,
              built_now: bool) -> None:
    n_search = report["n_search_universe"]
    _write_json(paths.p("phase8c_autonomous_norgate_research_campaign.json"), report)
    _write_csv(paths.p("available_norgate_universes.csv"), universe_rows,
               ["universe", "available", "superset_symbols", "feasible", "index_name"])
    _write_csv(paths.p("selected_universe_manifest.csv"), _universe_manifest_rows(selected, source),
               ["field", "value"])
    _write_csv(paths.p("panel_build_report.csv"),
               _panel_build_report_rows(panel, build_report, selected, built_now), ["field", "value"])
    _write_csv(paths.p("data_quality_report.csv"), _data_quality_rows(panel, build_report),
               ["metric", "value"])
    _write_csv(paths.p("campaign_cycle_log.csv"), state.cycle_log,
               ["cycle", "experiments_added", "experiments_skipped_cumulative", "candidates_scored",
                "n_confirmed", "n_weak", "n_rejected", "n_blocked", "director_decision"])
    _write_csv(paths.p("research_agenda.csv"), _research_agenda_rows(state),
               ["experiment_id", "cycle", "category", "owning_agent", "family", "hypothesis",
                "score_key", "quantile", "gate_key", "scope", "success_gate", "stop_condition",
                "challenges"])
    _write_csv(paths.p("agent_task_allocation.csv"), _agent_allocation_rows(state, panel, selected),
               ["agent", "experiments_owned", "challenge_experiments", "role"])
    _write_csv(paths.p("feature_catalog.csv"), _feature_catalog_rows(),
               ["feature", "family", "definition", "description", "information_set",
                "registered_before_scoring"])
    _write_csv(paths.p("experiment_registry.csv"), _registry_rows(state),
               ["experiment_id", "cycle", "category", "owning_agent", "family", "is_challenge",
                "is_non_momentum", "score_key", "quantile", "gate_key", "max_pos", "scope",
                "hypothesis", "success_gate", "stop_condition", "registered_before_scoring",
                "status_after_scoring"])
    _write_csv(paths.p("all_experiments_scoreboard.csv"), _scoreboard_rows(state, n_search),
               _SCOREBOARD_COLS)
    _write_csv(paths.p("failed_experiments.csv"), _failed_rows(state),
               ["experiment_id", "cycle", "category", "family", "score_key", "quantile", "scope",
                "status", "finding", "reason", "challenges"])
    cand_cols = ["experiment_id", "cycle", "family", "score_key", "quantile", "gate_key", "status",
                 "net_sharpe_25bps", "spy_sharpe", "ew_universe_sharpe", "net_sharpe_minus_spy",
                 "net_sharpe_minus_ew", "n_holdouts_beat_spy", "recent_beats_spy",
                 "rolling_frac_beat_spy", "net_max_drawdown_25bps", "spy_max_drawdown",
                 "one_sided_turnover", "avg_names", "net_sharpe_minus_placebo", "delisted_weight_avg",
                 "top_sector", "top_sector_weight", "reason"]
    _write_csv(paths.p("weak_signals.csv"), _candidate_rows(state, ST_WEAK, n_search), cand_cols)
    _write_csv(paths.p("confirmed_signals.csv"), _candidate_rows(state, ST_CONFIRMED, n_search), cand_cols)
    _write_csv(paths.p("rejected_signals.csv"), _candidate_rows(state, ST_REJECTED, n_search), cand_cols)
    _write_csv(paths.p("validation_skeptic_report.csv"), _validation_skeptic_rows(state),
               ["experiment_id", "cycle", "challenges", "scope", "hypothesis", "finding", "verdict"])
    _write_csv(paths.p("risk_portfolio_report.csv"), _risk_portfolio_rows(state, n_search),
               ["experiment_id", "family", "score_key", "quantile", "scope", "status",
                "net_max_drawdown_25bps", "spy_max_drawdown", "one_sided_turnover", "avg_names",
                "beta_spy", "delisted_weight_avg", "top_sector", "top_sector_weight"])
    _write_json(paths.p("multiple_testing_report.csv"), _multiple_testing_report(state, n_search))
    # multiple_testing as CSV rows too (artifact name is .csv)
    mt = _multiple_testing_report(state, n_search)
    _write_csv(paths.p("multiple_testing_report.csv"), [{"field": k, "value": v} for k, v in mt.items()],
               ["field", "value"])
    _write_csv(paths.p("ensemble_readiness_report.csv"), _ensemble_readiness_rows(state),
               ["n_confirmed_signals", "ensemble_ready", "independent_signals_required",
                "optimized_weights_built", "note"])
    _write_csv(paths.p("paper_signal_contract.csv"), _paper_signal_contract_rows(state, n_search),
               None)
    _write_json(paths.p("research_director_decision.json"),
                _research_director_decision(report, state, n_search))
    _write_json(paths.p("phase8d_next_plan.json"), _phase8d_plan(report, state, n_search))


def _research_director_decision(report: dict, state: CampaignState, n_search: int) -> dict:
    cands = [e for e in state.registry if e.scope == "full"]
    leads = sorted([e for e in cands if e.status in (ST_CONFIRMED, ST_WEAK)],
                   key=lambda e: (_ev_of(state, e).get("net_sharpe_minus_spy") or -9), reverse=True)
    lead_rows = [{
        "experiment_id": e.experiment_id, "family": e.family, "score_key": e.score_key,
        "quantile": e.quantile, "status": e.status,
        "net_sharpe_25bps": _ev_of(state, e).get("net_sharpe_25bps"),
        "net_sharpe_minus_spy": _ev_of(state, e).get("net_sharpe_minus_spy"),
        "recent_beats_spy": (_ev_of(state, e).get("subperiods", {}).get(RECENT_LABEL, {}) or {}).get("beats_spy"),
        "net_max_drawdown_25bps": _ev_of(state, e).get("net_max_drawdown_25bps"),
    } for e in leads[:5]]
    return {
        "phase": PHASE, "generated_utc": report["generated_utc"],
        "recommendation": report["recommendation"],
        "recommendation_reason": report["recommendation_reason"],
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "selected_universe": report["selected_universe"]["selected_universe"],
        "panel_shape": report["panel_shape"],
        "cycles_run": report["cycles_run"], "stop_reason": report["stop_reason"],
        "budget": report["budget"],
        "confirmed_signals": report["confirmed_signals"],
        "weak_signals": report["weak_signals"],
        "rejected_signals": report["rejected_signals"],
        "top_leads": lead_rows,
        "multiple_testing": report["multiple_testing"],
        "agenda_authority": ("quant-research-director chose cycle agendas + budget allocation "
                             "autonomously within fixed guardrails; factor definitions/signs never "
                             "changed after seeing results."),
        "anti_p_hacking": {
            "all_experiments_pre_registered": True,
            "challenge_fraction_min": CHALLENGE_MIN_FRAC,
            "non_momentum_fraction_min": NON_MOMENTUM_MIN_FRAC,
            "placebo_required_margin": GATE_PLACEBO_MARGIN,
            "confirmed_requires_recent_holdout": True,
            "factor_signs_modified_after_results": False,
            "borderline_never_rounded_up": True,
            "failed_experiments_hidden": False,
        },
        "stop_conditions_honored": [
            "Norgate only", "large data only on D:", "no packages installed", "no paid APIs",
            "no Paper Trader", "no GCP", "no broker/order/automation", "no live trading signals",
            "no optimized weights", "no factor-sign flipping after results", "no regime activation",
            "no fundamentals", "failed experiments not hidden", "no commit", "no push",
        ],
    }


def _phase8d_plan(report: dict, state: CampaignState, n_search: int) -> dict:
    rec = report["recommendation"]
    cands = [e for e in state.registry if e.scope == "full"]
    leads = sorted([e for e in cands if e.status in (ST_CONFIRMED, ST_WEAK)],
                   key=lambda e: (_ev_of(state, e).get("net_sharpe_minus_spy") or -9), reverse=True)
    if rec == REC_CONFIRMED:
        steps = [
            "At least one signal passed the full CONFIRMED_SIGNAL gate on broad survivorship-aware data.",
            "8-D: independent replication on an alternate broad universe (e.g. S&P Composite 1500) "
            "through the IDENTICAL gate before any paper-research contract is trusted.",
            "Assess >=2 independent confirmed signals for an EQUAL-WEIGHT (not optimized) research combo.",
        ]
    elif rec == REC_WEAK:
        names = ", ".join(f"{e.score_key}({e.family})" for e in leads[:3]) or "none"
        steps = [
            f"Leads are positive but miss the CONFIRMED gate on broad data: {names}. Keep research-only.",
            "8-D: re-test the strongest leads on an alternate broad universe + longer rolling windows "
            "through the same gate; deterministic rules only.",
            "Do NOT promote, do NOT optimize weights, do NOT flip factor signs, do NOT add regimes.",
        ]
    elif rec == REC_REJECTED:
        steps = [
            "All tested deterministic families fail the benchmark/cost/recency gates on broad data.",
            "8-D: the research-director should decide whether deterministic cross-sectional anomalies "
            "are worth further pursuit, or whether the program pivots (still research-only).",
        ]
    else:
        steps = [f"recommendation={rec}; resolve the blocking condition before further experiments."]
    return {
        "from_phase": PHASE, "recommendation": rec, "next_phase": "8-D",
        "selected_universe": report["selected_universe"]["selected_universe"],
        "next_steps": steps,
        "hard_constraints": [
            "Norgate is the only provider", "large data only on D:", "repo outputs are summaries only",
            "no package install", "no paid APIs", "no Paper Trader", "no GCP",
            "no broker/order/automation", "no optimized weights without explicit director approval",
            "no factor-sign flipping", "no regime activation/throttling", "do not hide failed experiments",
            "do not commit", "do not push",
        ],
    }


def _blocked_report(started, panel, panel_ok, orch_ok, orch_problems, selected, source,
                    recommendation, reason) -> dict:
    return {
        "phase": PHASE, "generated_utc": started, "objective": OBJECTIVE,
        "recommendation": recommendation, "recommendation_reason": reason,
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "allowed_statuses": list(ALLOWED_STATUSES),
        "orchestrator_ready": orch_ok, "orchestrator_problems": orch_problems,
        "panel_ok": panel_ok, "panel_issues": panel.issues,
        "selected_universe": selected, "universe_discovery_source": source,
        "panel_shape": {"symbols": int(len(panel.metadata)), "months": int(len(panel.close.index))},
        "cycles_run": 0, "cycle_log": [], "stop_reason": reason,
        "budget": {"experiments_registered": 0},
        "confirmed_signals": [], "weak_signals": [], "rejected_signals": [],
        "n_search_universe": 31, "multiple_testing": {},
        "safety": {"research_only": True, "committed": False, "pushed": False},
    }


# --------------------------------------------------------------------------- #
# Top-level run.
# --------------------------------------------------------------------------- #
def run(out_dir: Path = DEFAULT_OUT_DIR, *, panel_root: Path = PANEL_ROOT,
        allow_build: bool = False) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = OutPaths(out_dir)
    started = _utc_now_iso()

    state_proj = read_project_state()
    orch_ok, orch_problems = orchestrator_ready(state_proj)

    universe_rows, source = discover_universes(panel_root)
    selected = select_universe(universe_rows)
    build_report = _read_build_report(panel_root)

    panel, built_now = build_or_load_panel(
        panel_root, allow_build=allow_build, universe=selected["selected_universe"])
    panel_ok = panel.ok

    # blocked paths emit a minimal-but-complete decision then return
    if not panel_ok:
        rec = REC_FULL_PANEL if any("build required" in i for i in panel.issues) else REC_DATA_BLOCKED
        report = _blocked_report(started, panel, panel_ok, orch_ok, orch_problems, selected, source,
                                 rec, f"panel not usable: {panel.issues}")
        empty_state = CampaignState()
        _emit_all(paths, report, empty_state, panel, selected, source, universe_rows,
                  build_report, built_now)
        _print_summary(report)
        return report
    if not orch_ok:
        report = _blocked_report(started, panel, panel_ok, orch_ok, orch_problems, selected, source,
                                 REC_ORCH_BLOCKED, f"orchestrator not ready: {orch_problems}")
        empty_state = CampaignState()
        _emit_all(paths, report, empty_state, panel, selected, source, universe_rows,
                  build_report, built_now)
        _print_summary(report)
        return report

    state, meta = run_campaign(panel)
    recommendation, rec_reason = derive_recommendation(panel_ok, orch_ok, state)
    report = _assemble_report(started, state, meta, panel, panel_ok, orch_ok, orch_problems,
                              selected, source, recommendation, rec_reason, build_report)
    _emit_all(paths, report, state, panel, selected, source, universe_rows, build_report, built_now)
    _print_summary(report)
    return report


def _print_summary(report: dict) -> None:
    b = report.get("budget", {})
    ps = report.get("panel_shape", {})
    print(f"[8-C] recommendation = {report['recommendation']}")
    print(f"      {report.get('recommendation_reason','')}")
    print(f"      universe = {report['selected_universe'].get('selected_universe','')}; "
          f"panel = {ps.get('symbols','?')} symbols x {ps.get('months','?')} months "
          f"[{ps.get('date_min','?')}..{ps.get('date_max','?')}] "
          f"active={ps.get('active','?')} delisted={ps.get('delisted','?')} (ok={report['panel_ok']})")
    print(f"      cycles = {report.get('cycles_run','?')}; stop = {report.get('stop_reason','')}")
    if b:
        print(f"      budget: {b.get('experiments_registered','?')}/{b.get('max_experiments','?')} "
              f"experiments; challenge={b.get('n_challenge','?')} ({b.get('challenge_fraction','?')}) "
              f"non-momentum={b.get('n_non_momentum','?')} ({b.get('non_momentum_fraction','?')})")
    print(f"      confirmed={report.get('confirmed_signals',[])} "
          f"weak={report.get('weak_signals',[])} "
          f"rejected={len(report.get('rejected_signals',[]))}")


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Phase 8-C autonomous Norgate research campaign (research only)")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--panel-root", type=Path, default=PANEL_ROOT)
    ap.add_argument("--allow-build", action="store_true",
                    help="build the panel from Norgate if the cached CSVs are absent (~minutes)")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ns = _parse_args(argv)
    report = run(ns.out_dir, panel_root=ns.panel_root, allow_build=ns.allow_build)
    return 0 if report["recommendation"] != REC_ERROR else 1


if __name__ == "__main__":
    raise SystemExit(main())
