"""Phase 5-F0C — Signal-to-Strategy Conversion (offline, point-in-time).

This is **Track A (quant brain) research**, not Track B operational packaging. It
ships **no** deployable scorer and starts **no** shadow trading. It answers ONE
question with out-of-sample evidence, building directly on Phase 5-F0B:

    Phase 5-F0B found that confidence gating lifts the cross-sectional rank IC from
    ~0.0425 (raw Alpha V3 ensemble) to ~0.0528 (ensemble_high_confidence_only),
    which beats the Phase 5-C price-only reference (~0.0452) on the *trustworthy*
    ranking metric. But the best 5-F0B candidate did NOT improve net portfolio
    quality: turnover stayed high and net-of-cost annualized-mean return was below
    Phase 5-C. The entry/exit band independently reduced turnover, but was never
    *combined* with high-confidence entry.

    Phase 5-F0C combines the parts that worked — high-confidence model-agreement
    entry, entry/exit-band turnover control, top-N concentration, optional industry
    cap, and regime exposure scaling — to ask:

        "Can the improved high-confidence IC be CONVERTED into a more TRADABLE
         strategy with acceptable turnover and better net-of-cost portfolio metrics
         than the Phase 5-C reference?"

What it does
------------
  1. Reuses the Phase 5-F0B harness (which itself reuses Phase 5-F0 and Phase 5-C):
     data loading, SPY-calendar alignment, point-in-time Alpha V3 features, the
     walk-forward component models (composite / ridge / tree), the ensemble, the
     rank-IC math, the SPY regime proxy, the placebo leakage probe, the Phase 5-C
     reference rerun, and the optional Ticker -> IndustryId industry map.
  2. Builds the high-confidence SIGNAL exactly as Phase 5-F0B did (component-rank
     agreement + no-trade zone) and reuses its rank IC as the signal-quality metric
     the strategies must preserve.
  3. Evaluates COMPOSED long-only strategy candidates on one comparable simulator
     that supports, in any combination: high-confidence entry eligibility, a
     hold-until-rank-deteriorates entry/exit band, top-N concentration, an industry
     cap, and REGIME EXPOSURE SCALING (reduce gross exposure in neutral / risk-off
     regimes instead of going fully flat).
  4. For each strategy: signal rank IC / t-stat / worst-year IC / IC by regime,
     top-N average forward excess return, top-N hit rate, net-of-25bps and
     net-of-50bps ANNUALIZED-MEAN return, turnover, max drawdown, exposure
     utilization, number of no-trade periods, plus a comparison vs both the Phase
     5-C reference and the Phase 5-F0B high-confidence reference.
  5. Emits an honest readiness-gate matrix and one of five allowed recommendations,
     driven by the actual metrics. It never forces a PASS and never recommends live
     deployment — the ceiling is a SHADOW candidate for Phase 5-F1.

Cross-strategy decisions use the **top-N equal-weight net-of-50bps ANNUALIZED-MEAN
return** (no compounding -> cadence-comparable and far less survivorship-explosive
than compounded total return) together with turnover and drawdown. All candidates
share the monthly cadence so they are directly comparable; the compounded total
return is reported but flagged ``survivorship_inflated`` and never drives a gate.

SPY is the **benchmark / market-regime proxy**, never a ranked tradable name. The
optional local industry metadata is used ONLY for the Ticker -> IndustryId map. No
SimFin fundamentals are read or used as alpha.

Hard rules honored: no live network / API calls, no provider work, no FMP, no SimFin
fundamentals expansion, no package installs, D: is read-only price input only, no
writes to D:, no Paper Trader changes, no GCP changes, no deploy, no orders / broker
execution / automation, no binary model artifacts, no commit, no push. Pure offline
numpy + csv.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PHASE = "5-F0C"

# --------------------------------------------------------------------------- #
# Paths (all outputs committed-safe; D: is read-only input only)
# --------------------------------------------------------------------------- #
_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase5f0c_signal_to_strategy_conversion"
_REPORT_OUT = _OUT_DIR / "phase5f0c_signal_to_strategy_conversion.json"
_CANDIDATE_MATRIX_OUT = _OUT_DIR / "strategy_candidate_matrix.csv"
_ENTRY_EXIT_OUT = _OUT_DIR / "strategy_entry_exit_report.csv"
_TURNOVER_OUT = _OUT_DIR / "strategy_turnover_cost_report.csv"
_PORTFOLIO_OUT = _OUT_DIR / "strategy_portfolio_report.csv"
_REGIME_EXPOSURE_OUT = _OUT_DIR / "strategy_regime_exposure_report.csv"
_INDUSTRY_OUT = _OUT_DIR / "strategy_industry_control_report.csv"
_GATE_MATRIX_OUT = _OUT_DIR / "strategy_validation_gate_matrix.csv"
_SHADOW_PLAN_OUT = _OUT_DIR / "phase5f1_shadow_candidate_plan.json"

_PHASE5F0B_RUNNER = _REPO_ROOT / "research" / "run_phase5f0b_signal_quality_upgrade.py"
# Optional local industry metadata (Ticker + IndustryId ONLY; never fundamentals).
_INDUSTRY_CSV = (_REPO_ROOT / "research" / "data" / "simfin" / "normalized"
                 / "phase5e1e" / "shared" / "general.csv")

# --------------------------------------------------------------------------- #
# Strategy candidate identifiers (the required candidate set)
# --------------------------------------------------------------------------- #
S_HC_TOP5 = "high_confidence_top5_monthly"
S_HC_TOP10 = "high_confidence_top10_monthly"
S_HC_TOP10_BAND = "high_confidence_top10_entry_exit_band"
S_HC_TOP5_BAND = "high_confidence_top5_entry_exit_band"
S_HC_TOP10_INDCAP = "high_confidence_top10_industry_capped"
S_HC_TOP10_REGIME = "high_confidence_top10_regime_scaled"
S_HC_TOP10_BAND_INDCAP = "high_confidence_top10_entry_exit_industry_capped"
S_HC_TOP10_BAND_REGIME = "high_confidence_top10_entry_exit_regime_scaled"
S_BEST = "best_strategy_candidate"

# Fixed evaluation order (industry-capped variants only if a local map is available).
STRATEGY_ORDER = [S_HC_TOP5, S_HC_TOP10, S_HC_TOP10_BAND, S_HC_TOP5_BAND,
                  S_HC_TOP10_INDCAP, S_HC_TOP10_REGIME, S_HC_TOP10_BAND_INDCAP,
                  S_HC_TOP10_BAND_REGIME, S_BEST]

# Strategy configuration table: (id, basket_size, use_band, use_industry_cap, use_regime_scale).
_STRATEGY_CONFIG = {
    S_HC_TOP5:               dict(n=5,  band=False, cap=False, regime=False),
    S_HC_TOP10:              dict(n=10, band=False, cap=False, regime=False),
    S_HC_TOP10_BAND:         dict(n=10, band=True,  cap=False, regime=False),
    S_HC_TOP5_BAND:          dict(n=5,  band=True,  cap=False, regime=False),
    S_HC_TOP10_INDCAP:       dict(n=10, band=False, cap=True,  regime=False),
    S_HC_TOP10_REGIME:       dict(n=10, band=False, cap=False, regime=True),
    S_HC_TOP10_BAND_INDCAP:  dict(n=10, band=True,  cap=True,  regime=False),
    S_HC_TOP10_BAND_REGIME:  dict(n=10, band=True,  cap=False, regime=True),
}
_NEEDS_INDUSTRY = {S_HC_TOP10_INDCAP, S_HC_TOP10_BAND_INDCAP}

# --------------------------------------------------------------------------- #
# Quality-control tuning constants (mirror Phase 5-F0B; transparent + documented)
# --------------------------------------------------------------------------- #
CONF_DISPERSION_MAX = 0.25   # max stdev of component normalized ranks to "agree"
CONF_HI_RANK = 0.70          # high-confidence LONG bucket: ensemble rank >= this
CONF_LO_RANK = 0.30          # the F0B no-trade middle (0.30-0.70); long enters >= hi only
BAND_ENTRY_RANK = 10         # enter only when ensemble rank position < this (0-based)
BAND_EXIT_RANK = 20          # hold until rank position >= this, then exit (low churn)
SECTOR_MAX_PER_INDUSTRY = 2  # max names per sector in the industry-capped baskets
SECTOR_DIGITS = 3            # IndustryId sector prefix length (SimFin: 1st 3 digits)
INDUSTRY_COVERAGE_MIN = 0.60 # fraction of universe with a sector to call it available

# Regime exposure scaling: reduce gross exposure in weaker regimes (NOT fully flat).
# Risk-off captures both deep drawdown and high realized vol in the SPY regime proxy.
REGIME_EXPOSURE = {"risk_on": 1.0, "neutral": 0.6, "risk_off": 0.3}

PERIODS_PER_YEAR = {"monthly": 12, "weekly": 52}

# Readiness-gate thresholds.
GATE_WORST_YEAR_IC = -0.05       # "stable" tolerance (same as 5-F0B)
GATE_TURNOVER_ACCEPTABLE = 0.55  # acceptable per-rebalance turnover
GATE_DRAWDOWN_ACCEPTABLE = -0.40 # acceptable top-N gross max drawdown

# --------------------------------------------------------------------------- #
# Recommendation vocabulary (the five allowed values)
# --------------------------------------------------------------------------- #
REC_READY = "READY_FOR_PHASE5F1_SHADOW_CANDIDATE"
REC_NOT_TRADABLE = "EDGE_PRESENT_BUT_STILL_NOT_TRADABLE"
REC_NO_IMPROVE = "NO_STRATEGY_IMPROVEMENT"
REC_DATA_BLOCKER = "DATA_BLOCKER"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (REC_READY, REC_NOT_TRADABLE, REC_NO_IMPROVE,
                           REC_DATA_BLOCKER, REC_ERROR)


# --------------------------------------------------------------------------- #
# Reuse: import the Phase 5-F0B runner (which imports 5-F0, which imports 5-C).
# --------------------------------------------------------------------------- #
def _load_phase5f0b(runner_path: Optional[Path] = None):
    """Import the Phase 5-F0B runner as a module (no side effects; main() guarded)."""
    path = Path(runner_path) if runner_path else _PHASE5F0B_RUNNER
    spec = importlib.util.spec_from_file_location("phase5f0b_runner", str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load Phase 5-F0B runner at {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# Small IO / numeric helpers
# --------------------------------------------------------------------------- #
def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(_REPO_ROOT))
    except ValueError:
        return str(p)


def _f(x) -> Optional[float]:
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    return round(xf, 6) if math.isfinite(xf) else None


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str)
        fh.write("\n")


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


# --------------------------------------------------------------------------- #
# Optional local industry map (Ticker -> sector). Reads ONLY Ticker + IndustryId.
# This is metadata, NOT fundamentals: no statement line item is read or used.
# (Re-implemented locally so the source is self-contained for the safety scans;
# identical semantics to Phase 5-F0B.)
# --------------------------------------------------------------------------- #
def load_industry_map(path: Optional[Path] = None) -> Dict[str, str]:
    """Return {ticker: sector_id}. sector_id = first ``SECTOR_DIGITS`` digits of the
    SimFin IndustryId. Reads ONLY the Ticker and IndustryId columns; all other
    columns (including any fundamentals-adjacent metadata) are ignored. Returns an
    empty dict when the file is absent or unreadable (sector control -> unavailable)."""
    p = Path(path) if path else _INDUSTRY_CSV
    if not p.is_file():
        return {}
    out: Dict[str, str] = {}
    try:
        with open(p, "r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None or "Ticker" not in reader.fieldnames \
                    or "IndustryId" not in reader.fieldnames:
                return {}
            for row in reader:
                tk = (row.get("Ticker") or "").strip().upper()
                iid = (row.get("IndustryId") or "").strip()
                if not tk or not iid:
                    continue
                digits = iid.split(".")[0]
                if len(digits) >= SECTOR_DIGITS:
                    out[tk] = digits[:SECTOR_DIGITS]
    except (OSError, ValueError):
        return {}
    return out


# --------------------------------------------------------------------------- #
# Composed long-only strategy simulator
# --------------------------------------------------------------------------- #
# Supports, in ANY combination:
#   * high-confidence ENTRY eligibility (only names in ``hc_long_keys`` may enter),
#   * hold-until-rank-deteriorates band (``entry_rank`` / ``exit_rank``),
#   * top-N concentration,
#   * industry cap (``sector_of`` + ``max_per_sector``),
#   * regime exposure scaling (``regime_scale`` -> scale gross weights by the date's
#     regime factor; the un-invested fraction sits in cash and earns 0).
# Turnover = 0.5 * sum |dw| on the (post-scale) weights; net = gross - 2*turnover*bps.
# --------------------------------------------------------------------------- #
def _simulate_strategy(f0b, by_date: Dict[str, List[dict]], dates: List[str], n: int,
                       weighting: str, cost_bps: Sequence[int], periods_per_year: int,
                       date_regime: Dict[str, str], *,
                       hc_long_keys: Optional[set] = None,
                       entry_rank: Optional[int] = None, exit_rank: Optional[int] = None,
                       sector_of: Optional[Dict[str, str]] = None,
                       max_per_sector: Optional[int] = None,
                       regime_scale: Optional[Dict[str, float]] = None) -> dict:
    band = entry_rank is not None and exit_rank is not None
    cap = sector_of is not None and max_per_sector is not None

    prev_w: Dict[str, float] = {}
    held: List[str] = []
    gross_periods: List[float] = []
    turnovers: List[float] = []
    exposures: List[float] = []
    net_periods = {bps: [] for bps in cost_bps}
    exposures_by_regime: Dict[str, List[float]] = {}
    fwd_excess_per_date: List[float] = []   # mean raw20 of the chosen names (equal, unscaled)
    hit_hits = 0
    hit_total = 0
    no_trade = 0
    used = 0

    def _eligible(d: str, t: str) -> bool:
        return hc_long_keys is None or (d, t) in hc_long_keys

    for d in dates:
        recs = [r for r in by_date[d] if r.get("raw20") is not None]
        rmap = {r["ticker"]: r for r in recs}
        recs_sorted = sorted(recs, key=lambda r: r["score"], reverse=True)
        pos = {r["ticker"]: i for i, r in enumerate(recs_sorted)}
        sec_count: Dict[str, int] = {}
        chosen: List[dict] = []

        if band:
            # keep current holdings whose rank has not deteriorated past the exit band
            keep = [t for t in held if t in pos and pos[t] < exit_rank]
            if cap:
                for t in keep:
                    s = sector_of.get(t)
                    sec_count[s] = sec_count.get(s, 0) + 1
            for r in recs_sorted:
                if len(keep) >= n:
                    break
                t = r["ticker"]
                if t in keep:
                    continue
                if not _eligible(d, t):
                    continue
                if pos[t] >= entry_rank:
                    continue
                if cap:
                    s = sector_of.get(t)
                    if s is not None and sec_count.get(s, 0) >= max_per_sector:
                        continue
                    sec_count[s] = sec_count.get(s, 0) + 1
                keep.append(t)
            chosen = [rmap[t] for t in keep[:n] if t in rmap]
        else:
            for r in recs_sorted:
                if len(chosen) >= n:
                    break
                t = r["ticker"]
                if not _eligible(d, t):
                    continue
                if cap:
                    s = sector_of.get(t)
                    if s is not None and sec_count.get(s, 0) >= max_per_sector:
                        continue
                    sec_count[s] = sec_count.get(s, 0) + 1
                chosen.append(r)

        held = [r["ticker"] for r in chosen]
        scale = (regime_scale.get(date_regime.get(d, "neutral"), 1.0)
                 if regime_scale else 1.0)

        if chosen:
            if weighting == "vol_adj":
                inv = []
                for r in chosen:
                    v = r.get("vol63")
                    inv.append(1.0 / v if (v and v > 0 and math.isfinite(v)) else 0.0)
                tot = sum(inv)
                base_w = [(x / tot if tot > 0 else 1.0 / len(chosen)) for x in inv]
            else:
                base_w = [1.0 / len(chosen)] * len(chosen)
            weights = {r["ticker"]: bw * scale for r, bw in zip(chosen, base_w)}
            gross = float(sum(bw * scale * r["raw20"] for r, bw in zip(chosen, base_w)))
            exposure = float(sum(weights.values()))
            # selection-quality diagnostics (equal-name avg, unscaled, pre-cost)
            names_raw = [r["raw20"] for r in chosen]
            fwd_excess_per_date.append(float(np.mean(names_raw)))
            hit_hits += sum(1 for x in names_raw if x > 0)
            hit_total += len(names_raw)
        else:
            weights = {}
            gross = 0.0
            exposure = 0.0
            no_trade += 1

        names = set(weights) | set(prev_w)
        turnover = 0.5 * sum(abs(weights.get(t, 0.0) - prev_w.get(t, 0.0)) for t in names)
        gross_periods.append(gross)
        turnovers.append(turnover)
        exposures.append(exposure)
        exposures_by_regime.setdefault(date_regime.get(d, "neutral"), []).append(exposure)
        for bps in cost_bps:
            net_periods[bps].append(gross - 2.0 * turnover * (bps / 10000.0))
        prev_w = weights
        used += 1

    out = {
        "basket_size": n, "weighting": weighting, "held_dates": used,
        "avg_turnover": float(np.mean(turnovers)) if turnovers else None,
        "exposure_utilization": float(np.mean(exposures)) if exposures else None,
        "no_trade_periods": no_trade,
        "topn_avg_fwd_excess_return": (float(np.mean(fwd_excess_per_date))
                                       if fwd_excess_per_date else None),
        "topn_hit_rate": (hit_hits / hit_total) if hit_total else None,
        "regime_exposure": {k: float(np.mean(v)) for k, v in exposures_by_regime.items()},
        "gross": f0b._summarize(gross_periods, periods_per_year),
    }
    for bps in cost_bps:
        out[f"net_{bps}bps"] = f0b._summarize(net_periods[bps], periods_per_year)
    return out


# --------------------------------------------------------------------------- #
# Strategy candidate assembly
# --------------------------------------------------------------------------- #
def _strategy_candidate(f0b, f0, p5c, sid: str, cfg: dict, by_date: Dict[str, List[dict]],
                        dates: List[str], date_regime: Dict[str, str], sig_block: dict, *,
                        hc_long_keys: set, sector_of: Optional[Dict[str, str]],
                        coverage: Optional[float] = None) -> dict:
    """Assemble the full metric block for one composed strategy. The signal IC block
    (``sig_block``) is shared across all strategies — basket size / band / cap /
    regime scaling are portfolio-construction choices and do not change the ranking
    IC of the high-confidence signal that every strategy trades."""
    n = cfg["n"]
    use_band = cfg["band"]
    use_cap = cfg["cap"]
    use_regime = cfg["regime"]
    ppy = PERIODS_PER_YEAR["monthly"]

    def _sim(weighting: str) -> dict:
        return _simulate_strategy(
            f0b, by_date, dates, n, weighting, f0.COST_BPS, ppy, date_regime,
            hc_long_keys=hc_long_keys,
            entry_rank=BAND_ENTRY_RANK if use_band else None,
            exit_rank=BAND_EXIT_RANK if use_band else None,
            sector_of=sector_of if use_cap else None,
            max_per_sector=SECTOR_MAX_PER_INDUSTRY if use_cap else None,
            regime_scale=REGIME_EXPOSURE if use_regime else None)

    primary = _sim("equal")
    vol_adj = _sim("vol_adj")

    notes = []
    notes.append(f"high-confidence entry (dispersion<={CONF_DISPERSION_MAX}, rank>={CONF_HI_RANK})")
    notes.append(f"top-{n}")
    if use_band:
        notes.append(f"entry/exit band (enter rank<{BAND_ENTRY_RANK}, hold until rank>={BAND_EXIT_RANK})")
    if use_cap:
        notes.append(f"industry cap (max {SECTOR_MAX_PER_INDUSTRY}/sector)")
    if use_regime:
        notes.append("regime exposure scaling " + json.dumps(REGIME_EXPOSURE))

    block = {
        "strategy": sid, "cadence": "monthly", "evaluable": sig_block.get("evaluable", False),
        "basket_size": n, "config": dict(cfg), "rebalance_coverage": _f(coverage),
        "has_explicit_no_trade_logic": True,  # all strategies gate entry on the no-trade zone
        "notes": "; ".join(notes),
    }
    if not sig_block.get("evaluable"):
        block["reason"] = sig_block.get("reason", "high-confidence signal not evaluable")
        block["portfolios"] = {}
        return block

    # signal-quality metrics (shared high-confidence signal IC block)
    block.update({
        "n_scored_dates": sig_block["n_scored_dates"],
        "signal_mean_rank_ic": sig_block["mean_rank_ic"],
        "ic_t_stat": sig_block.get("ic_t_stat"),
        "worst_year_ic": sig_block.get("worst_year_ic"),
        "mean_decile_spread": sig_block["mean_decile_spread"],
        "ic_by_regime": sig_block.get("ic_by_regime", {}),
        "portfolios": {"primary_equal": primary, "vol_adj": vol_adj},
    })
    return block


# --------------------------------------------------------------------------- #
# Metric extraction helpers
# --------------------------------------------------------------------------- #
def _net_ann_mean(block: dict) -> Optional[float]:
    """Decision metric: primary-basket equal-weight net-of-50bps ANNUALIZED MEAN
    return (no compounding -> cadence-comparable, not survivorship-explosive)."""
    if not block.get("evaluable"):
        return None
    p = block.get("portfolios", {}).get("primary_equal", {})
    return p.get("net_50bps", {}).get("ann_mean_return")


def _net_ann_mean_25(block: dict) -> Optional[float]:
    if not block.get("evaluable"):
        return None
    p = block.get("portfolios", {}).get("primary_equal", {})
    return p.get("net_25bps", {}).get("ann_mean_return")


def _turnover(block: dict) -> Optional[float]:
    if not block.get("evaluable"):
        return None
    return block.get("portfolios", {}).get("primary_equal", {}).get("avg_turnover")


def _drawdown(block: dict) -> Optional[float]:
    if not block.get("evaluable"):
        return None
    return block.get("portfolios", {}).get("primary_equal", {}).get("gross", {}).get("max_drawdown")


def _ref_net_ann_mean(ref_block: dict) -> Optional[float]:
    """net-50bps annualized-mean of an f0b._candidate reference block's top10_equal."""
    if not ref_block.get("evaluable"):
        return None
    p = ref_block.get("portfolios", {}).get("top10_equal", {})
    return p.get("net_50bps", {}).get("ann_mean_return")


def _ref_turnover(ref_block: dict) -> Optional[float]:
    if not ref_block.get("evaluable"):
        return None
    return ref_block.get("portfolios", {}).get("top10_equal", {}).get("avg_turnover")


def _ref_view(ref_block: dict) -> dict:
    """Standard metric view for a Phase 5-C / 5-F0B reference (f0b._candidate block)."""
    if not ref_block.get("evaluable"):
        return {"evaluable": False, "reason": ref_block.get("reason", "")}
    p10 = ref_block.get("portfolios", {}).get("top10_equal", {})
    return {
        "evaluable": True, "candidate": ref_block.get("candidate"),
        "cadence": ref_block.get("cadence"), "n_scored_dates": ref_block["n_scored_dates"],
        "rebalance_coverage": ref_block.get("rebalance_coverage"),
        "mean_rank_ic": _f(ref_block["mean_rank_ic"]), "ic_t_stat": _f(ref_block.get("ic_t_stat")),
        "worst_year_ic": _f(ref_block.get("worst_year_ic")),
        "mean_decile_spread": _f(ref_block["mean_decile_spread"]),
        "top10_equal_avg_turnover": _f(p10.get("avg_turnover")),
        "top10_equal_gross_max_drawdown": _f(p10.get("gross", {}).get("max_drawdown")),
        "top10_equal_net_25bps_ann_mean_return": _f(p10.get("net_25bps", {}).get("ann_mean_return")),
        "top10_equal_net_50bps_ann_mean_return": _f(p10.get("net_50bps", {}).get("ann_mean_return")),
        "top10_equal_net_50bps_total_return_survivorship_inflated":
            _f(p10.get("net_50bps", {}).get("total_return")),
    }


def _strat_view(block: dict) -> dict:
    if not block.get("evaluable"):
        return {"evaluable": False, "strategy": block.get("strategy"),
                "reason": block.get("reason", ""), "config": block.get("config")}
    pe = block.get("portfolios", {}).get("primary_equal", {})
    pv = block.get("portfolios", {}).get("vol_adj", {})
    return {
        "evaluable": True, "strategy": block["strategy"], "cadence": block["cadence"],
        "basket_size": block["basket_size"], "config": block["config"],
        "n_scored_dates": block["n_scored_dates"],
        "rebalance_coverage": block.get("rebalance_coverage"),
        "signal_mean_rank_ic": _f(block["signal_mean_rank_ic"]),
        "ic_t_stat": _f(block.get("ic_t_stat")), "worst_year_ic": _f(block.get("worst_year_ic")),
        "mean_decile_spread": _f(block["mean_decile_spread"]),
        "ic_by_regime": {k: {"mean_ic": _f(v["mean_ic"]), "n_dates": v["n_dates"]}
                         for k, v in block.get("ic_by_regime", {}).items()},
        "topn_avg_fwd_excess_return": _f(pe.get("topn_avg_fwd_excess_return")),
        "topn_hit_rate": _f(pe.get("topn_hit_rate")),
        "net_25bps_ann_mean_return": _f(pe.get("net_25bps", {}).get("ann_mean_return")),
        "net_50bps_ann_mean_return": _f(pe.get("net_50bps", {}).get("ann_mean_return")),
        "net_50bps_total_return_survivorship_inflated": _f(pe.get("net_50bps", {}).get("total_return")),
        "gross_ann_mean_return": _f(pe.get("gross", {}).get("ann_mean_return")),
        "avg_turnover": _f(pe.get("avg_turnover")),
        "max_drawdown": _f(pe.get("gross", {}).get("max_drawdown")),
        "exposure_utilization": _f(pe.get("exposure_utilization")),
        "no_trade_periods": pe.get("no_trade_periods"),
        "regime_exposure": {k: _f(v) for k, v in pe.get("regime_exposure", {}).items()},
        "vol_adj_net_50bps_ann_mean_return": _f(pv.get("net_50bps", {}).get("ann_mean_return")),
        "has_explicit_no_trade_logic": block.get("has_explicit_no_trade_logic", True),
        "notes": block.get("notes", ""),
    }


# --------------------------------------------------------------------------- #
# Readiness gates + recommendation
# --------------------------------------------------------------------------- #
def build_readiness_gates(best: dict, baseline5c: dict, f0b_ref: dict, ensemble: dict,
                          placebo_ic: Optional[float]) -> List[dict]:
    """PASS / FAIL / WARNING / NOT_EVALUABLE per readiness gate, judged on the best
    composed strategy vs the Phase 5-C baseline (and the F0B high-confidence ref).
    Never forces a PASS; the ceiling is a shadow candidate."""
    gates: List[dict] = []

    def add(name, status, metric, value, threshold, note=""):
        gates.append({"gate_name": name, "status": status, "metric": metric,
                      "value": value, "threshold": threshold, "note": note})

    ev = best.get("evaluable", False)
    base_ic = baseline5c.get("mean_rank_ic") if baseline5c.get("evaluable") else None
    best_ic = best.get("signal_mean_rank_ic") if ev else None
    base_net = _ref_net_ann_mean(baseline5c)
    best_net = _net_ann_mean(best)
    ens_turn = _ref_turnover(ensemble)
    best_turn = _turnover(best)

    placebo_clean = placebo_ic is None or abs(placebo_ic) <= 0.02
    add("no_leakage_placebo_gate", "PASS" if placebo_clean else "FAIL",
        "placebo_mean_ic", _f(placebo_ic), "|placebo IC|<=0.02",
        "ridge mean rank IC after within-date label shuffle must collapse to ~0")

    # 1. keeps the high-confidence IC improvement (signal IC still beats the 5-C ref)
    if not ev or best_ic is None or base_ic is None:
        add("keeps_high_confidence_ic_improvement_gate", "NOT_EVALUABLE",
            "best_signal_ic_minus_baseline_ic", None, ">0")
    else:
        d = best_ic - base_ic
        add("keeps_high_confidence_ic_improvement_gate", "PASS" if d > 0 else "FAIL",
            "best_signal_ic_minus_baseline_ic", _f(d), ">0",
            f"best high-confidence signal IC={_f(best_ic)} vs Phase 5-C reference IC={_f(base_ic)}")

    # 2. net-of-cost annualized mean return beats Phase 5-C reference
    if not ev or best_net is None or base_net is None:
        add("net_return_beats_phase5c_gate", "NOT_EVALUABLE",
            "best_minus_baseline_net50_ann_mean", None, ">0", "missing portfolio metric")
    else:
        d = best_net - base_net
        add("net_return_beats_phase5c_gate", "PASS" if d > 0 else "FAIL",
            "best_minus_baseline_net50_ann_mean", _f(d), ">0",
            f"best net50 ann-mean={_f(best_net)} vs 5-C baseline={_f(base_net)} "
            "(annualized mean, not compounded; absolute level still survivorship-inflated)")

    # 3. turnover acceptable or materially improved (vs raw ensemble or below threshold)
    if not ev or best_turn is None:
        add("turnover_acceptable_or_improved_gate", "NOT_EVALUABLE", "best_turnover",
            None, f"< ensemble OR <{GATE_TURNOVER_ACCEPTABLE}")
    else:
        improved = ens_turn is not None and best_turn < ens_turn
        acceptable = best_turn < GATE_TURNOVER_ACCEPTABLE
        ok = improved or acceptable
        add("turnover_acceptable_or_improved_gate", "PASS" if ok else "WARNING",
            "best_turnover", _f(best_turn),
            f"< ensemble({_f(ens_turn)}) OR <{GATE_TURNOVER_ACCEPTABLE}",
            "improved vs raw ensemble" if improved else "")

    # 4. max drawdown acceptable
    dd = _drawdown(best)
    if not ev or dd is None:
        add("drawdown_acceptable_gate", "NOT_EVALUABLE", "top_n_gross_max_drawdown",
            None, f">{GATE_DRAWDOWN_ACCEPTABLE}")
    else:
        add("drawdown_acceptable_gate", "PASS" if dd > GATE_DRAWDOWN_ACCEPTABLE else "WARNING",
            "top_n_gross_max_drawdown", _f(dd), f">{GATE_DRAWDOWN_ACCEPTABLE}")

    # 5. worst-year IC acceptable
    if not ev:
        add("worst_year_ic_acceptable_gate", "NOT_EVALUABLE", "worst_year_ic", None,
            f">{GATE_WORST_YEAR_IC}")
    else:
        wy = best.get("worst_year_ic")
        ok = (wy is None) or (wy > GATE_WORST_YEAR_IC)
        add("worst_year_ic_acceptable_gate", "PASS" if ok else "WARNING",
            "worst_year_ic", _f(wy), f">{GATE_WORST_YEAR_IC}")

    # 6. explicit no-trade logic
    add("explicit_no_trade_logic_gate", "PASS" if best.get("has_explicit_no_trade_logic") else "WARNING",
        "best_has_no_trade_logic", bool(best.get("has_explicit_no_trade_logic")), "true",
        f"best strategy = {best.get('strategy')} (high-confidence entry + no-trade zone)")

    add("shadow_only_not_live_gate", "PASS", "deployment_mode", "shadow_only",
        "shadow_only", "this phase recommends at most a shadow candidate; never live")
    add("survivorship_bias_gate", "WARNING", "universe_is_full_history_survivors", True,
        "survivorship-free universe required before trusting absolute returns",
        "rank IC / turnover are less affected than absolute portfolio returns")
    add("no_fundamentals_as_alpha_gate", "PASS", "fundamentals_used_as_alpha", False,
        "false", "optional industry map uses Ticker/IndustryId only; no statement line items")
    add("preview_only_gate", "PASS", "preview_only", True, "true", "research harness only")
    add("no_orders_gate", "PASS", "orders_enabled", False, "false", "")
    add("no_broker_execution_gate", "PASS", "broker_execution_enabled", False, "false", "")
    add("no_automation_gate", "PASS", "automation_enabled", False, "false", "")
    add("no_binary_model_artifact_gate", "PASS", "binary_model_written", False, "false",
        "models fit in-memory for evaluation only; nothing persisted")
    return gates


def derive_recommendation(best: dict, gates: List[dict]) -> Tuple[str, str]:
    """Honest, gate-driven recommendation. Never forces a PASS; never recommends live."""
    status = {g["gate_name"]: g["status"] for g in gates}
    if not best.get("evaluable", False):
        return REC_NO_IMPROVE, "no composed strategy produced an evaluable high-confidence signal"
    if status.get("no_leakage_placebo_gate") == "FAIL":
        return REC_NO_IMPROVE, "placebo leakage check failed; no trustworthy edge"

    ready_gates = [
        "keeps_high_confidence_ic_improvement_gate", "net_return_beats_phase5c_gate",
        "turnover_acceptable_or_improved_gate", "drawdown_acceptable_gate",
        "worst_year_ic_acceptable_gate", "explicit_no_trade_logic_gate",
        "shadow_only_not_live_gate",
    ]
    if all(status.get(g) == "PASS" for g in ready_gates):
        return (REC_READY, "the best composed strategy keeps the high-confidence IC improvement "
                "over the Phase 5-C reference, beats it on net-of-cost annualized-mean return "
                "with acceptable/improved turnover and drawdown, an acceptable worst-year IC, "
                "and explicit no-trade logic, with no leakage — promote it as a SHADOW candidate "
                "(not live)")

    # A real, leakage-clean edge with explicit controls that does not (yet) clear the
    # full tradability bar over Phase 5-C.
    keeps_ic = status.get("keeps_high_confidence_ic_improvement_gate") == "PASS"
    turnover_ok = status.get("turnover_acceptable_or_improved_gate") in ("PASS", "WARNING")
    if keeps_ic and turnover_ok and status.get("no_leakage_placebo_gate") == "PASS" \
            and best.get("has_explicit_no_trade_logic"):
        return (REC_NOT_TRADABLE, "the high-confidence IC improvement is preserved and the "
                "composed turnover/exposure controls behave as designed, but the strategy does "
                "not clearly beat the Phase 5-C reference on net-of-cost portfolio metrics; keep "
                "it shadow-only and keep iterating before any deployment decision")
    return (REC_NO_IMPROVE, "the composed strategies did not convert the high-confidence ranking "
            "edge into a robust, leakage-clean tradability improvement over the Phase 5-C "
            "reference; keep iterating before shadow trading")


# --------------------------------------------------------------------------- #
# Artifact writers
# --------------------------------------------------------------------------- #
def _candidate_row(view: dict) -> list:
    if not view.get("evaluable"):
        return [view.get("strategy"), "monthly", False] + [""] * 11
    return [view["strategy"], view["cadence"], True, view.get("basket_size"),
            view.get("signal_mean_rank_ic"), view.get("ic_t_stat"),
            view.get("topn_avg_fwd_excess_return"), view.get("topn_hit_rate"),
            view.get("net_25bps_ann_mean_return"), view.get("net_50bps_ann_mean_return"),
            view.get("avg_turnover"), view.get("max_drawdown"),
            view.get("exposure_utilization"), view.get("no_trade_periods"),
            view.get("net_50bps_total_return_survivorship_inflated")]


def _write_artifacts(views: Dict[str, dict], best_name: Optional[str], gates: List[dict],
                     industry_rows: List[list]) -> None:
    # candidate matrix
    cm_header = ["strategy", "cadence", "evaluable", "basket_size", "signal_mean_rank_ic",
                 "ic_t_stat", "topn_avg_fwd_excess_return", "topn_hit_rate",
                 "net_25bps_ann_mean_return", "net_50bps_ann_mean_return", "avg_turnover",
                 "max_drawdown", "exposure_utilization", "no_trade_periods",
                 "net_50bps_total_return_survivorship_inflated"]
    cm_rows = []
    for key in STRATEGY_ORDER:
        if key == S_BEST:
            if best_name and best_name in views and views[best_name].get("evaluable"):
                row = _candidate_row(views[best_name])
                row[0] = f"{S_BEST} (= {best_name})"
                cm_rows.append(row)
            continue
        v = views.get(key)
        if v is None:
            cm_rows.append([key, "monthly", False, "unavailable"] + [""] * 11)
            continue
        cm_rows.append(_candidate_row(v))
    _write_csv(_CANDIDATE_MATRIX_OUT, cm_header, cm_rows)

    # entry/exit report (band behavior + holding economics)
    ee_rows = []
    for key in STRATEGY_ORDER:
        if key == S_BEST:
            continue
        v = views.get(key)
        if v is None or not v.get("evaluable"):
            continue
        cfg = v.get("config", {})
        ee_rows.append([v["strategy"], cfg.get("n"), bool(cfg.get("band")),
                        BAND_ENTRY_RANK if cfg.get("band") else "",
                        BAND_EXIT_RANK if cfg.get("band") else "",
                        v.get("avg_turnover"), v.get("no_trade_periods"),
                        v.get("exposure_utilization")])
    _write_csv(_ENTRY_EXIT_OUT,
               ["strategy", "basket_size", "entry_exit_band", "entry_rank", "exit_rank",
                "avg_turnover", "no_trade_periods", "exposure_utilization"], ee_rows)

    # turnover / cost report (per candidate, equal + vol_adj)
    turn_rows, port_rows, reg_rows = [], [], []
    for key in STRATEGY_ORDER:
        if key == S_BEST:
            continue
        v = views.get(key)
        if v is None or not v.get("evaluable"):
            continue
        turn_rows.append([v["strategy"], "equal", v.get("avg_turnover"),
                          v.get("net_25bps_ann_mean_return"), v.get("net_50bps_ann_mean_return"),
                          v.get("net_50bps_total_return_survivorship_inflated")])
        port_rows.append([v["strategy"], v.get("basket_size"),
                          v.get("topn_avg_fwd_excess_return"), v.get("topn_hit_rate"),
                          v.get("gross_ann_mean_return"), v.get("net_50bps_ann_mean_return"),
                          v.get("max_drawdown"), v.get("exposure_utilization")])
        for reg, exp in (v.get("regime_exposure") or {}).items():
            icr = v.get("ic_by_regime", {}).get(reg, {})
            reg_rows.append([v["strategy"], reg, exp, icr.get("mean_ic"), icr.get("n_dates")])
    _write_csv(_TURNOVER_OUT,
               ["strategy", "weighting", "avg_turnover", "net_25bps_ann_mean_return",
                "net_50bps_ann_mean_return", "net_50bps_total_return_survivorship_inflated"],
               turn_rows)
    _write_csv(_PORTFOLIO_OUT,
               ["strategy", "basket_size", "topn_avg_fwd_excess_return", "topn_hit_rate",
                "gross_ann_mean_return", "net_50bps_ann_mean_return", "max_drawdown",
                "exposure_utilization"], port_rows)
    _write_csv(_REGIME_EXPOSURE_OUT,
               ["strategy", "regime", "avg_exposure", "signal_mean_ic", "n_dates"], reg_rows)

    # industry control report
    _write_csv(_INDUSTRY_OUT,
               ["control", "available", "basket_size", "avg_turnover",
                "net_50bps_ann_mean_return", "note"], industry_rows)

    # gate matrix
    _write_csv(_GATE_MATRIX_OUT,
               ["gate_name", "status", "metric", "value", "threshold", "note"],
               [[g["gate_name"], g["status"], g["metric"], g["value"], g["threshold"],
                 g["note"]] for g in gates])


def _gate_summary(gates: List[dict]) -> dict:
    summ = {"PASS": 0, "FAIL": 0, "WARNING": 0, "NOT_EVALUABLE": 0}
    for g in gates:
        summ[g["status"]] = summ.get(g["status"], 0) + 1
    return summ


def _shadow_plan(recommendation: str, best_name: Optional[str]) -> dict:
    return {
        "phase": "5-F1 (shadow candidate)",
        "title": "Shadow-only candidate observation plan (planned, not yet built)",
        "gated_on": f"Phase 5-F0C recommendation = {REC_READY}.",
        "phase5f0c_recommendation": recommendation,
        "phase5f0c_best_strategy": best_name,
        "proceed_to_shadow": recommendation == REC_READY,
        "scope_if_proceed": [
            "Freeze the winning composed strategy (high-confidence entry + entry/exit "
            "band + top-N + optional industry cap + regime exposure scaling) from the "
            "walk-forward.",
            "Observe it in SHADOW only: record paper signals and would-be positions "
            "without placing any orders.",
            "Re-validate on a survivorship-free universe before trusting absolute returns.",
            "Track live signal rank IC, turnover, drawdown, exposure utilization, and "
            "regime behavior vs backtest.",
        ],
        "hard_constraints": [
            "Shadow-only: no orders, no broker execution, no automation, no GCP deploy.",
            "Survivorship bias must be resolved before trusting absolute returns.",
            "Preview-only; manual review; paper trading only.",
        ],
        "if_not_ready": ("Keep the Phase 5-C price-only baseline; keep iterating on the "
                         "combination of confidence / band / regime / industry controls "
                         "before any shadow observation."),
        "preview_only": True, "orders_enabled": False, "automation_enabled": False,
        "broker_execution_enabled": False, "production_replacement": False,
    }


# --------------------------------------------------------------------------- #
# Main orchestration
# --------------------------------------------------------------------------- #
def run(price_history: Optional[dict] = None, out_dir: Optional[Path] = None,
        price_csv: Optional[str] = None, phase5f0b_runner: Optional[Path] = None,
        industry_csv: Optional[Path] = None,
        industry_map: Optional[Dict[str, str]] = None,
        max_tickers: Optional[int] = None, write: bool = True, verbose: bool = True) -> dict:
    """Run the signal-to-strategy-conversion harness. Inputs can be injected (tests)
    or loaded from disk (real run). Always returns a report dict whose
    ``recommendation`` is one of ``ALLOWED_RECOMMENDATIONS``."""
    global _OUT_DIR, _REPORT_OUT, _CANDIDATE_MATRIX_OUT, _ENTRY_EXIT_OUT, _TURNOVER_OUT
    global _PORTFOLIO_OUT, _REGIME_EXPOSURE_OUT, _INDUSTRY_OUT, _GATE_MATRIX_OUT, _SHADOW_PLAN_OUT
    if out_dir is not None:
        _OUT_DIR = Path(out_dir)
        _REPORT_OUT = _OUT_DIR / "phase5f0c_signal_to_strategy_conversion.json"
        _CANDIDATE_MATRIX_OUT = _OUT_DIR / "strategy_candidate_matrix.csv"
        _ENTRY_EXIT_OUT = _OUT_DIR / "strategy_entry_exit_report.csv"
        _TURNOVER_OUT = _OUT_DIR / "strategy_turnover_cost_report.csv"
        _PORTFOLIO_OUT = _OUT_DIR / "strategy_portfolio_report.csv"
        _REGIME_EXPOSURE_OUT = _OUT_DIR / "strategy_regime_exposure_report.csv"
        _INDUSTRY_OUT = _OUT_DIR / "strategy_industry_control_report.csv"
        _GATE_MATRIX_OUT = _OUT_DIR / "strategy_validation_gate_matrix.csv"
        _SHADOW_PLAN_OUT = _OUT_DIR / "phase5f1_shadow_candidate_plan.json"

    f0b = _load_phase5f0b(phase5f0b_runner)
    f0 = f0b._load_phase5f0()
    p5c = f0._load_phase5c()

    resolved_price_csv = None
    if price_history is None:
        resolved_price_csv = price_csv or p5c._data_path()
        if resolved_price_csv:
            price_history = p5c.load_local_history(resolved_price_csv)

    if industry_map is None:
        industry_map = load_industry_map(industry_csv)

    base_report = {
        "phase": PHASE,
        "objective": ("Can the Phase 5-F0B high-confidence ranking improvement be CONVERTED "
                      "into a more tradable strategy — combining high-confidence model-agreement "
                      "entry, entry/exit-band turnover control, top-N concentration, optional "
                      "industry cap, and regime exposure scaling — with acceptable turnover and "
                      "better net-of-cost portfolio metrics than the Phase 5-C reference, without "
                      "new providers and without deployment?"),
        "track": "A (quantitative signal-to-strategy evidence) — not Track B operational packaging.",
        "data_source": resolved_price_csv,
        "benchmark_regime_proxy": f0.BENCHMARK,
        "primary_label": f0.PRIMARY_LABEL,
        "strategy_set": STRATEGY_ORDER,
        "recommended_next_phase": "5-F1 (shadow candidate)",
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "regime_exposure_scaling": dict(REGIME_EXPOSURE),
        "preview_only": True, "orders_enabled": False, "automation_enabled": False,
        "broker_execution_enabled": False, "production_replacement": False,
        "network_used": False, "paid_apis_used": False, "deployed": False,
        "binary_artifacts_created": False, "committed": False,
        "writes_to_d_drive": False, "modifies_paper_trader": False, "modifies_gcp": False,
        "uses_simfin_fundamentals_as_alpha": False, "provider_work": False,
        "packages_installed": False, "live_trading": False, "shadow_only": True,
    }

    if not price_history:
        base_report.update({
            "universe_size": 0, "strategies_evaluated": [], "best_strategy": None,
            "phase5c_reference_metrics": {}, "phase5f0b_reference_metrics": {},
            "best_strategy_metrics": {}, "strategy_improvement_vs_phase5c": {},
            "strategy_improvement_vs_f0b": {},
            "readiness_gate_summary": {"PASS": 0, "FAIL": 0, "WARNING": 0, "NOT_EVALUABLE": 0},
            "recommendation": REC_DATA_BLOCKER,
            "recommendation_reason": "no local price-history CSV found (D: read-only or repo fallback)",
        })
        assert base_report["recommendation"] in ALLOWED_RECOMMENDATIONS
        if write:
            _write_json(_REPORT_OUT, base_report)
            _write_json(_SHADOW_PLAN_OUT, _shadow_plan(REC_DATA_BLOCKER, None))
        if verbose:
            _print_summary(base_report, {})
        return base_report

    al = p5c.Aligned(price_history)
    if max_tickers:
        keep = set(sorted(al.tickers)[:max_tickers])
        al.tickers = [t for t in al.tickers if t in keep]
    universe_size = len(al.tickers)
    date_range = (al.master_dates[0], al.master_dates[-1])

    # sector availability (Ticker + IndustryId only)
    sector_of = {t: industry_map[t] for t in al.tickers if t in industry_map}
    sector_coverage = (len(sector_of) / universe_size) if universe_size else 0.0
    sector_available = sector_coverage >= INDUSTRY_COVERAGE_MIN

    # ---- monthly Alpha V3 ensemble (the base signal), reusing the F0B/F0 harness ----
    panel_m = f0b._build_v3_panel_at(p5c, f0, al, al.month_end_indices())
    comp_m, _ = f0.run_variant(p5c, panel_m, f0._COMPOSITE_FEATURES, f0.PRIMARY_LABEL, "composite")
    ridge_m, _ = f0.run_variant(p5c, panel_m, f0.FEATURES, f0.PRIMARY_LABEL, "ridge")
    tree_m, _ = f0.run_variant(p5c, panel_m, f0.FEATURES, f0.PRIMARY_LABEL, "tree")
    components_m = {f0.M_COMPOSITE: comp_m, f0.M_RIDGE: ridge_m, f0.M_TREE: tree_m}
    ens_m = f0.ensemble_predictions(components_m)

    # leakage probe (ridge on full V3 features, within-date label shuffle)
    placebo_ic = f0._placebo_ic(p5c, panel_m, f0.FEATURES, f0.PRIMARY_LABEL)

    # ---- high-confidence signal (exactly as Phase 5-F0B) + long-entry eligibility ----
    disp = f0b._component_dispersion(components_m)
    ens_rank_m = f0b._norm_rank_map(ens_m)
    hc_signal_preds = []      # F0B high_confidence_only set (top AND bottom buckets)
    hc_long_keys: set = set()  # long-only ENTRY eligibility (top bucket only)
    for r in ens_m:
        k = (r["date"], r["ticker"])
        rk = ens_rank_m.get(k)
        dp = disp.get(k)
        if rk is None or dp is None:
            continue
        if dp <= CONF_DISPERSION_MAX and (rk >= CONF_HI_RANK or rk <= CONF_LO_RANK):
            hc_signal_preds.append(r)
        if dp <= CONF_DISPERSION_MAX and rk >= CONF_HI_RANK:
            hc_long_keys.add(k)
    hc_signal_cov = (len(hc_signal_preds) / len(ens_m)) if ens_m else 0.0

    # signal IC block shared across all composed strategies (the metric to preserve)
    sig_block = f0b._ic_block(p5c, f0, hc_signal_preds)

    # ---- reference blocks via the reused F0B machinery ----
    base_preds, base5c_model = f0b._phase5c_reference_preds(p5c, al)
    baseline5c = f0b._candidate(p5c, f0, "baseline_phase5c_reference", base_preds,
                                cadence="monthly",
                                notes=f"Phase 5-C price-only reference (best model = {base5c_model})")
    ensemble_ref = f0b._candidate(p5c, f0, "alpha_v3_ensemble", ens_m, cadence="monthly",
                                  notes="raw Phase 5-F0 ensemble; no quality control")
    f0b_ref = f0b._candidate(p5c, f0, "ensemble_high_confidence_only", hc_signal_preds,
                             cadence="monthly", coverage=hc_signal_cov, has_gating=True,
                             notes="Phase 5-F0B best candidate (high-confidence signal)")

    # ---- composed strategy candidates ----
    by_date: Dict[str, List[dict]] = {}
    for r in ens_m:
        by_date.setdefault(r["date"], []).append(r)
    dates = sorted(by_date)
    date_regime = {r["date"]: r["regime"] for r in ens_m}

    strategies: Dict[str, dict] = {}
    for sid in (S_HC_TOP5, S_HC_TOP10, S_HC_TOP10_BAND, S_HC_TOP5_BAND, S_HC_TOP10_INDCAP,
                S_HC_TOP10_REGIME, S_HC_TOP10_BAND_INDCAP, S_HC_TOP10_BAND_REGIME):
        cfg = _STRATEGY_CONFIG[sid]
        if sid in _NEEDS_INDUSTRY and not sector_available:
            continue  # reported as unavailable (no local industry map)
        cov = sector_coverage if sid in _NEEDS_INDUSTRY else None
        strategies[sid] = _strategy_candidate(
            f0b, f0, p5c, sid, cfg, by_date, dates, date_regime, sig_block,
            hc_long_keys=hc_long_keys,
            sector_of=sector_of if sid in _NEEDS_INDUSTRY else None, coverage=cov)

    # ---- best strategy: highest net-50bps ANNUALIZED-MEAN return (the tradability
    # metric this phase is about), ties -> lower turnover, then better (less negative)
    # drawdown. Signal IC is identical across strategies, so it cannot select among
    # them; the conversion question is decided on net portfolio economics. All
    # strategies share the monthly cadence, so the annualized-mean net return is
    # directly comparable (and survivorship affects them equally). ----
    contenders = [k for k, b in strategies.items() if b.get("evaluable")]

    def _best_key(k: str):
        b = strategies[k]
        net = _net_ann_mean(b)
        turn = _turnover(b)
        dd = _drawdown(b)
        return (net if net is not None else -9.9,
                -(turn if turn is not None else 9.9),
                dd if dd is not None else -9.9)

    best_name = max(contenders, key=_best_key) if contenders else None
    best = strategies.get(best_name, {"evaluable": False, "strategy": None,
                                      "reason": "no evaluable strategy", "portfolios": {},
                                      "ic_by_regime": {}})

    # ---- gates + recommendation ----
    gates = build_readiness_gates(best, baseline5c, f0b_ref, ensemble_ref, placebo_ic)
    recommendation, why = derive_recommendation(best, gates)

    # ---- comparisons ----
    base_ic = baseline5c.get("mean_rank_ic") if baseline5c.get("evaluable") else None
    base_net = _ref_net_ann_mean(baseline5c)
    best_ic = best.get("signal_mean_rank_ic") if best.get("evaluable") else None
    best_net = _net_ann_mean(best)
    f0b_net = _ref_net_ann_mean(f0b_ref)
    f0b_turn = _ref_turnover(f0b_ref)
    f0b_ic = f0b_ref.get("mean_rank_ic") if f0b_ref.get("evaluable") else None
    best_turn = _turnover(best)

    strategy_improvement_vs_phase5c = {
        "baseline_5c_model": base5c_model,
        "baseline_mean_rank_ic": _f(base_ic),
        "best_strategy": best_name,
        "best_signal_mean_rank_ic": _f(best_ic),
        "delta_ic_best_minus_baseline": (_f(best_ic - base_ic)
                                         if (best_ic is not None and base_ic is not None) else None),
        "best_keeps_ic_improvement_over_5c": bool(best_ic is not None and base_ic is not None
                                                  and best_ic > base_ic),
        "decision_metric": "top-N equal-weight net-of-50bps ANNUALIZED MEAN return (cadence-comparable)",
        "baseline_top10_net50_ann_mean": _f(base_net),
        "best_net50_ann_mean": _f(best_net),
        "delta_net50_ann_mean_best_minus_baseline": (_f(best_net - base_net)
                                                     if (best_net is not None and base_net is not None) else None),
        "best_beats_5c_on_net_return": bool(best_net is not None and base_net is not None
                                            and best_net > base_net),
        "best_turnover": _f(best_turn),
        "note": ("absolute (compounded total) returns are survivorship-inflated and NOT "
                 "comparable across cadences; signal rank IC, turnover, drawdown, and the "
                 "annualized-mean net return are the trustworthy metrics"),
    }
    strategy_improvement_vs_f0b = {
        "f0b_best_candidate": "ensemble_high_confidence_only",
        "f0b_mean_rank_ic": _f(f0b_ic),
        "f0b_top10_net50_ann_mean": _f(f0b_net),
        "f0b_top10_avg_turnover": _f(f0b_turn),
        "best_signal_mean_rank_ic": _f(best_ic),
        "best_net50_ann_mean": _f(best_net),
        "best_turnover": _f(best_turn),
        "delta_net50_ann_mean_best_minus_f0b": (_f(best_net - f0b_net)
                                                if (best_net is not None and f0b_net is not None) else None),
        "delta_turnover_best_minus_f0b": (_f(best_turn - f0b_turn)
                                          if (best_turn is not None and f0b_turn is not None) else None),
        "best_improves_turnover_vs_f0b": bool(best_turn is not None and f0b_turn is not None
                                              and best_turn < f0b_turn),
        "best_improves_net_return_vs_f0b": bool(best_net is not None and f0b_net is not None
                                                and best_net > f0b_net),
        "note": ("Phase 5-F0B never combined high-confidence entry with the entry/exit band; "
                 "this phase tests whether that combination converts the IC edge into better "
                 "net tradability"),
    }

    strategy_views = {k: _strat_view(strategies[k]) for k in strategies}

    # ---- industry control report ----
    industry_rows = []
    if S_HC_TOP10 in strategy_views and strategy_views[S_HC_TOP10].get("evaluable"):
        v = strategy_views[S_HC_TOP10]
        industry_rows.append(["high_confidence_top10 (uncapped)", True, v["basket_size"],
                              v.get("avg_turnover"), v.get("net_50bps_ann_mean_return"),
                              "no industry control"])
    if sector_available and S_HC_TOP10_INDCAP in strategy_views \
            and strategy_views[S_HC_TOP10_INDCAP].get("evaluable"):
        v = strategy_views[S_HC_TOP10_INDCAP]
        industry_rows.append(["high_confidence_top10_industry_capped", True, v["basket_size"],
                              v.get("avg_turnover"), v.get("net_50bps_ann_mean_return"),
                              f"max {SECTOR_MAX_PER_INDUSTRY}/sector; coverage={sector_coverage:.2f}"])
    else:
        industry_rows.append(["high_confidence_top10_industry_capped", False, "", "", "",
                              "local industry map unavailable or insufficient coverage"])

    report = dict(base_report)
    report.update({
        "data_source": resolved_price_csv,
        "universe_size": universe_size,
        "date_range": {"start": date_range[0], "end": date_range[1]},
        "rebalance_cadence": "monthly (all strategies; cadence-comparable)",
        "sector_control_available": sector_available,
        "sector_control_coverage": _f(sector_coverage),
        "sector_control_source": (_rel(industry_csv) if industry_csv else _rel(_INDUSTRY_CSV))
                                  if sector_available else None,
        "sector_control_note": ("uses ONLY Ticker + IndustryId from the local industry map; "
                                "no SimFin fundamentals are read or used as alpha"),
        "high_confidence_signal": {
            "definition": ("Phase 5-F0B high-confidence signal: component-rank dispersion <= "
                           f"{CONF_DISPERSION_MAX} and ensemble rank >= {CONF_HI_RANK} or <= "
                           f"{CONF_LO_RANK} (no-trade middle). Long entry uses the top bucket "
                           f"(rank >= {CONF_HI_RANK}) only."),
            "signal_coverage": _f(hc_signal_cov),
            "long_entry_coverage": _f(len(hc_long_keys) / len(ens_m)) if ens_m else None,
            "signal_mean_rank_ic": _f(sig_block.get("mean_rank_ic") if sig_block.get("evaluable") else None),
            "signal_ic_t_stat": _f(sig_block.get("ic_t_stat") if sig_block.get("evaluable") else None),
            "signal_worst_year_ic": _f(sig_block.get("worst_year_ic") if sig_block.get("evaluable") else None),
        },
        "confidence_controls": {
            "dispersion_max": CONF_DISPERSION_MAX, "hi_rank": CONF_HI_RANK, "lo_rank": CONF_LO_RANK,
            "definition": ("model agreement = low stdev of component within-date ranks; the "
                           "no-trade zone removes the middle of the cross-section; long entry "
                           "requires the high-confidence top bucket"),
        },
        "strategies_evaluated": [k for k in strategies if strategies[k].get("evaluable")],
        "strategies_unavailable": [k for k in STRATEGY_ORDER
                                   if k != S_BEST and k not in strategies],
        "strategy_metrics": strategy_views,
        "best_strategy": best_name,
        "best_strategy_metrics": _strat_view(best) if best.get("evaluable") else {"evaluable": False},
        "phase5c_reference_metrics": _ref_view(baseline5c),
        "alpha_v3_reference_metrics": _ref_view(ensemble_ref),
        "phase5f0b_reference_metrics": _ref_view(f0b_ref),
        "strategy_improvement_vs_phase5c": strategy_improvement_vs_phase5c,
        "strategy_improvement_vs_f0b": strategy_improvement_vs_f0b,
        "portfolio_simulation": {
            "basket_sizes": [5, 10], "weightings": ["equal", "vol_adj"],
            "cost_bps": list(f0.COST_BPS),
            "cost_model": "round-trip turnover * 2 * bps on the changed (post-scale) weight fraction",
            "composed_controls": ["high_confidence_entry", "entry_exit_band",
                                  "top_n_concentration", "industry_cap", "regime_exposure_scaling"],
            "regime_exposure_scaling": dict(REGIME_EXPOSURE),
        },
        "leakage_checks": {
            "labels_forward_only": True, "features_use_past_only": True,
            "embargo_respected": True, "placebo_mean_ic": _f(placebo_ic),
            "placebo_interpretation": ("ridge mean rank IC after a within-date label shuffle; "
                                       "should be ~0 — a real edge must not survive the shuffle"),
        },
        "validation_gate_matrix": gates,
        "readiness_gate_summary": _gate_summary(gates),
        "any_strategy_ready_for_shadow": recommendation == REC_READY,
        "recommendation": recommendation,
        "recommendation_reason": why,
        "survivorship_caveat": ("the local universe is full-history survivors, so absolute "
                                "portfolio returns are upward-biased; rank IC / turnover / "
                                "exposure drive the readiness decision, and all strategies share "
                                "the cadence so relative net-return comparisons are fair"),
        "honesty_note": ("Carried-in result: Phase 5-F0B confidence gating lifted rank IC to "
                         "~0.0528 (beating the 5-C reference ~0.0452) but did NOT improve net "
                         "portfolio quality. This phase reports honestly whether COMBINING the "
                         "controls converts the edge into tradability, never forces a PASS, and "
                         "never recommends live trading — the ceiling is a shadow candidate."),
    })
    assert report["recommendation"] in ALLOWED_RECOMMENDATIONS

    if write:
        _write_json(_REPORT_OUT, report)
        _write_artifacts(strategy_views, best_name, gates, industry_rows)
        _write_json(_SHADOW_PLAN_OUT, _shadow_plan(recommendation, best_name))
    if verbose:
        _print_summary(report, strategy_views)
    return report


def _print_summary(report: dict, views: Dict[str, dict]) -> None:
    print("=" * 78)
    print(f"  Phase {report['phase']} — Signal-to-Strategy Conversion")
    print("=" * 78)
    print(f"  data_source   : {report.get('data_source')}")
    print(f"  universe_size : {report.get('universe_size')}  (SPY = regime/benchmark proxy)")
    print(f"  sector control: available={report.get('sector_control_available')} "
          f"(coverage={report.get('sector_control_coverage')})")
    hcs = report.get("high_confidence_signal", {})
    print(f"  hc signal IC  : {hcs.get('signal_mean_rank_ic')} "
          f"(long-entry coverage={hcs.get('long_entry_coverage')})")
    if views:
        print("-" * 78)
        print("  strategy matrix (sigIC | net50am | net25am | turnover | dd | expo | hit):")
        for k in STRATEGY_ORDER:
            if k == S_BEST:
                continue
            s = views.get(k)
            if not s or not s.get("evaluable"):
                print(f"    {k:48s} unavailable / not evaluable")
                continue
            print(f"    {k:48s} IC={s['signal_mean_rank_ic']:+.4f} "
                  f"net50am={s.get('net_50bps_ann_mean_return')} "
                  f"net25am={s.get('net_25bps_ann_mean_return')} "
                  f"turn={s.get('avg_turnover')} dd={s.get('max_drawdown')} "
                  f"expo={s.get('exposure_utilization')} hit={s.get('topn_hit_rate')}")
    qi = report.get("strategy_improvement_vs_phase5c", {})
    qf = report.get("strategy_improvement_vs_f0b", {})
    print("-" * 78)
    print(f"  best_strategy : {report.get('best_strategy')}")
    print(f"  vs 5-C ref    : dIC={qi.get('delta_ic_best_minus_baseline')} "
          f"dNet50am={qi.get('delta_net50_ann_mean_best_minus_baseline')} "
          f"(beats 5-C net: {qi.get('best_beats_5c_on_net_return')})")
    print(f"  vs F0B ref    : dNet50am={qf.get('delta_net50_ann_mean_best_minus_f0b')} "
          f"dTurn={qf.get('delta_turnover_best_minus_f0b')} "
          f"(improves turnover: {qf.get('best_improves_turnover_vs_f0b')})")
    print(f"  placebo IC    : {report.get('leakage_checks', {}).get('placebo_mean_ic')} (should be ~0)")
    print(f"  gate summary  : {report.get('readiness_gate_summary')}")
    print(f"  recommendation: {report.get('recommendation')}")
    print(f"  reason        : {report.get('recommendation_reason')}")
    print("-" * 78)
    if report.get("recommendation") != REC_DATA_BLOCKER:
        for p in (_REPORT_OUT, _CANDIDATE_MATRIX_OUT, _ENTRY_EXIT_OUT, _TURNOVER_OUT,
                  _PORTFOLIO_OUT, _REGIME_EXPOSURE_OUT, _INDUSTRY_OUT, _GATE_MATRIX_OUT,
                  _SHADOW_PLAN_OUT):
            print(f"  wrote: {_rel(p)}")


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Phase 5-F0C signal-to-strategy-conversion harness")
    ap.add_argument("--price-csv", default=None, help="override local price-history CSV path")
    ap.add_argument("--industry-csv", default=None, help="override local industry-map CSV path")
    ap.add_argument("--max-tickers", type=int, default=None, help="cap universe (debug)")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ns = _parse_args(argv)
    try:
        report = run(price_csv=ns.price_csv,
                     industry_csv=Path(ns.industry_csv) if ns.industry_csv else None,
                     max_tickers=ns.max_tickers, write=True, verbose=True)
    except Exception as exc:  # noqa: BLE001 — emit an honest ERROR report, never crash silently
        report = {"phase": PHASE, "recommendation": REC_ERROR,
                  "recommendation_reason": f"{type(exc).__name__}: {exc}"}
        _write_json(_REPORT_OUT, report)
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0 if report.get("recommendation") != REC_ERROR else 1


if __name__ == "__main__":
    raise SystemExit(main())
