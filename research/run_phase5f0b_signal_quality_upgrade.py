"""Phase 5-F0B — Alpha Signal Quality Upgrade (offline, point-in-time).

This is **Track A (quant brain) research**, not Track B operational packaging. It
does **not** ship a deployable scorer and it does **not** start shadow trading. It
answers ONE question with out-of-sample evidence:

    "Can we turn the existing weak/modest price/volume/regime ranking edge
     (Phase 5-F0, ensemble mean rank IC ~0.0425, which did NOT materially beat the
     Phase 5-C price-only reference ~0.0452) into a HIGHER-QUALITY, more TRADABLE
     signal by using confidence gating, horizon selection, model agreement, regime
     filters, sector/industry controls, and turnover controls?"

Strategic diagnosis carried in: the signal is real but not high-quality enough. The
problem is not a lack of features; it is weak selectivity, possible regime
dependence, turnover, and insufficient trade / no-trade gating. So this phase does
NOT invent new features or providers. It reuses the Phase 5-F0 Alpha V3 feature
panel and component models (imported, not duplicated) and tests whether *quality
controls layered on top* of the existing signal improve net tradability versus the
Phase 5-C reference.

What it does
------------
  1. Reuses the Phase 5-F0 harness (which itself reuses Phase 5-C) for data loading,
     SPY-calendar alignment, point-in-time Alpha V3 features, the walk-forward
     component models (composite / ridge / tree), the ensemble, rank-IC math, the
     SPY regime proxy, the placebo leakage probe, and the Phase 5-C reference rerun.
  2. Builds the Alpha V3 ensemble OOS signal on BOTH a monthly and a weekly
     rebalance cadence (same leakage-free walk-forward; only the rebalance index set
     changes), plus per-horizon (5d / 10d / 20d forward-excess) ridge signals for a
     blended-horizon candidate.
  3. Evaluates a fixed set of quality-controlled candidate strategies:
       - baseline_phase5c_reference        (the price-only baseline to beat)
       - alpha_v3_ensemble                 (the raw 5-F0 ensemble, no quality control)
       - ensemble_high_confidence_only     (model agreement + no-trade zone)
       - ensemble_risk_on_only             (trade only in the risk-on regime)
       - ensemble_top10_weekly_rebalance   (weekly cadence; turnover/cost sensitivity)
       - ensemble_top10_entry_exit_band    (hold-until-rank-deteriorates; low churn)
       - ensemble_industry_capped_top10    (max names per industry/sector, if a local
                                            industry map is available; else skipped)
       - blended_horizon_signal            (mean within-date rank of 5/10/20d signals)
       - best_quality_candidate            (the best of the above on net quality)
  4. For every candidate: rank IC / t-stat / IC by year / worst-year IC / IC by
     regime, top-bottom decile spread, long-only top-10 / top-20 portfolio
     simulation (equal AND vol-adjusted) with 25 / 50 bps cost sensitivity,
     turnover, and max drawdown — all on one comparable simulator.
  5. Tests risk filters (liquidity floor, volatility cap, extreme-overextension cap)
     and reports their effect on the base ensemble IC.
  6. Emits an honest readiness-gate matrix and one of five allowed recommendations,
     driven by the actual metrics. It never forces a PASS and never recommends live
     deployment — the best it can recommend is a SHADOW candidate for Phase 5-F1.

SPY is the **benchmark / market-regime proxy**, never a ranked tradable name.

Optional local industry metadata is used ONLY for the Ticker -> IndustryId map
(sector/industry controls). No SimFin fundamentals are read or used as alpha.

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
from datetime import date as _date
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PHASE = "5-F0B"

# --------------------------------------------------------------------------- #
# Paths (all outputs committed-safe; D: is read-only input only)
# --------------------------------------------------------------------------- #
_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase5f0b_signal_quality_upgrade"
_REPORT_OUT = _OUT_DIR / "phase5f0b_signal_quality_upgrade.json"
_CANDIDATE_MATRIX_OUT = _OUT_DIR / "signal_quality_candidate_matrix.csv"
_HORIZON_OUT = _OUT_DIR / "signal_quality_horizon_report.csv"
_REGIME_OUT = _OUT_DIR / "signal_quality_regime_report.csv"
_TURNOVER_OUT = _OUT_DIR / "signal_quality_turnover_cost_report.csv"
_PORTFOLIO_OUT = _OUT_DIR / "signal_quality_portfolio_report.csv"
_INDUSTRY_OUT = _OUT_DIR / "signal_quality_industry_control_report.csv"
_GATE_MATRIX_OUT = _OUT_DIR / "signal_quality_validation_gate_matrix.csv"
_SHADOW_PLAN_OUT = _OUT_DIR / "phase5f1_shadow_candidate_plan.json"

_PHASE5F0_RUNNER = _REPO_ROOT / "research" / "run_phase5f0_alpha_model_v3_research.py"
_PHASE5C_REPORT = _REPO_ROOT / "research" / "output" / "phase5c_cross_sectional_alpha_research.json"
# Optional local industry metadata (Ticker + IndustryId ONLY; never fundamentals).
_INDUSTRY_CSV = (_REPO_ROOT / "research" / "data" / "simfin" / "normalized"
                 / "phase5e1e" / "shared" / "general.csv")

# --------------------------------------------------------------------------- #
# Candidate identifiers (the required candidate set)
# --------------------------------------------------------------------------- #
C_BASELINE = "baseline_phase5c_reference"
C_ENSEMBLE = "alpha_v3_ensemble"
C_HICONF = "ensemble_high_confidence_only"
C_RISKON = "ensemble_risk_on_only"
C_WEEKLY = "ensemble_top10_weekly_rebalance"
C_BAND = "ensemble_top10_entry_exit_band"
C_INDCAP = "ensemble_industry_capped_top10"
C_BLEND = "blended_horizon_signal"
C_BEST = "best_quality_candidate"

# The fixed evaluation order (industry-capped only if a local industry map exists).
CANDIDATE_ORDER = [C_BASELINE, C_ENSEMBLE, C_HICONF, C_RISKON, C_WEEKLY, C_BAND,
                   C_INDCAP, C_BLEND, C_BEST]

# --------------------------------------------------------------------------- #
# Quality-control tuning constants (transparent; documented in the report)
# --------------------------------------------------------------------------- #
CONF_DISPERSION_MAX = 0.25   # max stdev of component normalized ranks to "agree"
CONF_HI_RANK = 0.70          # keep names whose ensemble rank is >= this (longs)
CONF_LO_RANK = 0.30          # ...or <= this (shorts); the 0.30-0.70 middle is no-trade
BAND_ENTRY_RANK = 10         # enter when ensemble rank position < this (0-based)
BAND_EXIT_RANK = 20          # hold until rank position >= this, then exit
SECTOR_MAX_PER_INDUSTRY = 2  # max names per sector in the industry-capped basket
SECTOR_DIGITS = 3            # IndustryId sector prefix length (SimFin: 1st 3 digits)
INDUSTRY_COVERAGE_MIN = 0.60 # fraction of universe with a sector to call it available

# Risk filters
RISK_LIQUIDITY_FLOOR = 5.0e6     # 20d avg dollar volume floor
RISK_VOL_CAP_PCTILE = 0.95       # drop names above this cross-sectional vol percentile
RISK_OVEREXT_CAP_PCTILE = 0.98   # drop names above this 20d return-zscore percentile

PERIODS_PER_YEAR = {"monthly": 12, "weekly": 52}

# Readiness-gate thresholds.
GATE_WORST_YEAR_IC = -0.05       # "stable" tolerance (looser than 5-F0's deploy gate)
GATE_TURNOVER_ACCEPTABLE = 0.55  # acceptable per-rebalance turnover
GATE_DRAWDOWN_ACCEPTABLE = -0.40 # acceptable top-10 gross max drawdown

# --------------------------------------------------------------------------- #
# Recommendation vocabulary (the five allowed values)
# --------------------------------------------------------------------------- #
REC_READY = "READY_FOR_PHASE5F1_SHADOW_CANDIDATE"
REC_SHADOW_ONLY = "EDGE_PRESENT_BUT_NEEDS_SHADOW_ONLY"
REC_NO_IMPROVE = "NO_QUALITY_IMPROVEMENT"
REC_DATA_BLOCKER = "DATA_BLOCKER"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (REC_READY, REC_SHADOW_ONLY, REC_NO_IMPROVE,
                           REC_DATA_BLOCKER, REC_ERROR)


# --------------------------------------------------------------------------- #
# Reuse: import the Phase 5-F0 runner (which imports Phase 5-C). No duplication.
# --------------------------------------------------------------------------- #
def _load_phase5f0(runner_path: Optional[Path] = None):
    """Import the Phase 5-F0 runner as a module (no side effects; main() guarded)."""
    path = Path(runner_path) if runner_path else _PHASE5F0_RUNNER
    spec = importlib.util.spec_from_file_location("phase5f0_runner", str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load Phase 5-F0 runner at {path}")
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
# --------------------------------------------------------------------------- #
def load_industry_map(path: Optional[Path] = None) -> Dict[str, str]:
    """Return {ticker: sector_id}. sector_id = first ``SECTOR_DIGITS`` digits of the
    SimFin IndustryId. Reads ONLY the Ticker and IndustryId columns; all other
    columns (including any fundamentals-adjacent metadata) are ignored. Returns an
    empty dict when the file is absent or unreadable (sector control -> unavailable).
    """
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
                # IndustryId arrives like "101001.0"; the sector is its leading digits.
                digits = iid.split(".")[0]
                if len(digits) >= SECTOR_DIGITS:
                    out[tk] = digits[:SECTOR_DIGITS]
    except (OSError, ValueError):
        return {}
    return out


# --------------------------------------------------------------------------- #
# Rebalance index helpers (monthly reuses Phase 5-C; weekly added here)
# --------------------------------------------------------------------------- #
def _week_end_indices(master_dates: List[str]) -> List[int]:
    """Last master trading-day index of each ISO calendar week."""
    idxs: List[int] = []
    n = len(master_dates)
    for i, d in enumerate(master_dates):
        iso = _date.fromisoformat(d).isocalendar()
        wk = (iso[0], iso[1])
        nxt = None
        if i + 1 < n:
            niso = _date.fromisoformat(master_dates[i + 1]).isocalendar()
            nxt = (niso[0], niso[1])
        if nxt != wk:
            idxs.append(i)
    return idxs


def _build_v3_panel_at(p5c, f0, al, rebal_indices: List[int]) -> List[dict]:
    """Phase 5-F0 Alpha V3 panel built at a custom rebalance index set (mirrors
    ``f0.build_v3_panel`` but parametrized by cadence). Reuses ``f0`` feature math
    and the same forward-excess labels — leakage-free."""
    panel: List[dict] = []
    sc = al.close[f0.BENCHMARK]
    for i in rebal_indices:
        d = al.master_dates[i]
        year = int(d[:4])
        regime, _ = p5c.spy_regime(al, i)
        spy_f5 = p5c.forward_return(sc, i, f0.H5)
        spy_f10 = p5c.forward_return(sc, i, f0.H10)
        spy_f20 = p5c.forward_return(sc, i, f0.H20)
        for t in al.tickers:
            feats = f0.compute_v3_features(p5c, al, t, i, regime)
            if feats is None:
                continue
            c = al.close[t]
            f5 = p5c.forward_return(c, i, f0.H5)
            f10 = p5c.forward_return(c, i, f0.H10)
            f20 = p5c.forward_return(c, i, f0.H20)
            ex5 = (f5 - spy_f5) if (f5 is not None and spy_f5 is not None) else None
            ex10 = (f10 - spy_f10) if (f10 is not None and spy_f10 is not None) else None
            ex20 = (f20 - spy_f20) if (f20 is not None and spy_f20 is not None) else None
            panel.append({
                "date": d, "year": year, "ticker": t, "rebal_idx": i, "regime": regime,
                "features": feats,
                "avg_dollar_volume_20d": feats.get("avg_dollar_volume_20d"),
                "realized_vol_63d": feats.get("realized_vol_63d"),
                "forward_return_5d": f5, "forward_return_10d": f10, "forward_return_20d": f20,
                "forward_excess_return_5d_vs_spy": ex5,
                "forward_excess_return_10d_vs_spy": ex10,
                "forward_excess_return_20d_vs_spy": ex20,
            })
    # Cross-sectional quintile + downside labels on the primary target, per date.
    by_date: Dict[str, List[dict]] = {}
    for r in panel:
        by_date.setdefault(r["date"], []).append(r)
    for recs in by_date.values():
        vals = [(r, r[f0.PRIMARY_LABEL]) for r in recs if r[f0.PRIMARY_LABEL] is not None]
        for r in recs:
            r["top_quintile_label"] = None
            r["bottom_quintile_label"] = None
            r["downside_risk_label"] = (
                1 if (r["forward_return_20d"] is not None and r["forward_return_20d"] < -0.10)
                else (0 if r["forward_return_20d"] is not None else None))
        if len(vals) >= f0.MIN_UNIVERSE // 3:
            ys = np.array([v for _, v in vals])
            hi = float(np.quantile(ys, 0.8))
            lo = float(np.quantile(ys, 0.2))
            for r, y in vals:
                r["top_quintile_label"] = 1 if y >= hi else 0
                r["bottom_quintile_label"] = 1 if y <= lo else 0
    return panel


# --------------------------------------------------------------------------- #
# Cross-sectional rank helpers (confidence / model agreement / blending)
# --------------------------------------------------------------------------- #
def _norm_rank_map(preds: List[dict]) -> Dict[Tuple[str, str], float]:
    """Within-date normalized rank in [0,1] for one model's OOS scores."""
    by_date: Dict[str, List[dict]] = {}
    for r in preds:
        by_date.setdefault(r["date"], []).append(r)
    out: Dict[Tuple[str, str], float] = {}
    for d, recs in by_date.items():
        if len(recs) < 2:
            continue
        scores = np.array([r["score"] for r in recs], dtype=float)
        order = np.argsort(np.argsort(scores))  # 0..n-1 ascending rank
        denom = float(len(recs) - 1)
        for r, rk in zip(recs, order):
            out[(d, r["ticker"])] = rk / denom
    return out


def _component_dispersion(component_preds: Dict[str, List[dict]]
                          ) -> Dict[Tuple[str, str], float]:
    """Per-(date,ticker) stdev of the components' within-date normalized ranks.
    Low dispersion == the component models agree (high confidence)."""
    rank_maps = {m: _norm_rank_map(p) for m, p in component_preds.items()}
    keys = set()
    for rm in rank_maps.values():
        keys |= set(rm.keys())
    out: Dict[Tuple[str, str], float] = {}
    for k in keys:
        vals = [rm[k] for rm in rank_maps.values() if k in rm]
        if len(vals) >= 2:
            out[k] = float(np.std(np.array(vals, dtype=float)))
    return out


def _attach_labels(score_preds: List[dict], label_preds: List[dict]) -> List[dict]:
    """Build prediction records using ``score_preds`` for the score but
    y/raw20/vol63/regime taken from ``label_preds`` (matched on (date,ticker)).
    Used for blended-horizon signals so they are always evaluated against the
    primary 20-day forward-excess label."""
    lbl = {(r["date"], r["ticker"]): r for r in label_preds}
    out: List[dict] = []
    for s in score_preds:
        k = (s["date"], s["ticker"])
        base = lbl.get(k)
        if base is None:
            continue
        out.append({"date": base["date"], "year": base["year"], "regime": base["regime"],
                    "ticker": base["ticker"], "score": s["score"], "y": base["y"],
                    "raw20": base["raw20"], "vol63": base.get("vol63")})
    return out


# --------------------------------------------------------------------------- #
# Unified long-only portfolio simulator (top-N / regime-gated / band / sector cap)
# --------------------------------------------------------------------------- #
def _summarize(periods: List[float], periods_per_year: int) -> dict:
    if not periods:
        return {"n_periods": 0, "mean_period_return": None, "total_return": None,
                "approx_cagr": None, "max_drawdown": None}
    total = 1.0
    for pr in periods:
        total *= (1.0 + pr)
    total -= 1.0
    cagr = (1.0 + total) ** (periods_per_year / len(periods)) - 1.0
    mean_p = float(np.mean(periods))
    return {"n_periods": len(periods), "mean_period_return": mean_p,
            # annualized MEAN return (no compounding) — cadence-comparable and far
            # less survivorship-explosive than total_return; used for comparisons.
            "ann_mean_return": mean_p * periods_per_year,
            "total_return": float(total), "approx_cagr": float(cagr),
            "max_drawdown": _max_dd(periods)}


def _max_dd(period_returns: List[float]) -> float:
    eq = peak = 1.0
    worst = 0.0
    for pr in period_returns:
        eq *= (1.0 + pr)
        peak = max(peak, eq)
        worst = min(worst, eq / peak - 1.0)
    return worst


def _simulate(by_date: Dict[str, List[dict]], dates: List[str], n: int,
              weighting: str, cost_bps: Sequence[int], periods_per_year: int,
              active_dates: Optional[set] = None, entry_rank: Optional[int] = None,
              exit_rank: Optional[int] = None,
              sector_of: Optional[Dict[str, str]] = None,
              max_per_sector: Optional[int] = None) -> dict:
    """Long-only top-N basket with one comparable cost/turnover model. Supports:
      * regime gating  (``active_dates`` -> flat / cash on inactive rebalance dates),
      * hold-until-deteriorates band (``entry_rank`` / ``exit_rank``),
      * sector cap     (``sector_of`` + ``max_per_sector``),
      * equal or vol-adjusted (1/vol63) weighting.
    Turnover = 0.5 * sum |dw|; net = gross - 2 * turnover * bps. Cash periods earn 0.
    """
    prev_w: Dict[str, float] = {}
    held: List[str] = []
    gross_periods: List[float] = []
    turnovers: List[float] = []
    net_periods = {bps: [] for bps in cost_bps}
    used = 0
    for d in dates:
        recs = [r for r in by_date[d] if r.get("raw20") is not None]
        active = (active_dates is None) or (d in active_dates)
        chosen: List[dict] = []
        rmap = {r["ticker"]: r for r in recs}
        if active and recs:
            recs_sorted = sorted(recs, key=lambda r: r["score"], reverse=True)
            pos = {r["ticker"]: i for i, r in enumerate(recs_sorted)}
            if entry_rank is not None and exit_rank is not None:
                # hold-until-rank-deteriorates
                keep = [t for t in held if t in pos and pos[t] < exit_rank]
                for r in recs_sorted:
                    if len(keep) >= n:
                        break
                    t = r["ticker"]
                    if t not in keep and pos[t] < entry_rank:
                        keep.append(t)
                chosen = [rmap[t] for t in keep[:n] if t in rmap]
            elif sector_of is not None and max_per_sector is not None:
                sec_count: Dict[str, int] = {}
                for r in recs_sorted:
                    if len(chosen) >= n:
                        break
                    s = sector_of.get(r["ticker"])
                    if s is not None and sec_count.get(s, 0) >= max_per_sector:
                        continue
                    sec_count[s] = sec_count.get(s, 0) + 1
                    chosen.append(r)
            else:
                chosen = recs_sorted[:n]
        held = [r["ticker"] for r in chosen]

        if chosen:
            if weighting == "vol_adj":
                inv = []
                for r in chosen:
                    v = r.get("vol63")
                    inv.append(1.0 / v if (v and v > 0 and math.isfinite(v)) else 0.0)
                tot = sum(inv)
                w = [(x / tot if tot > 0 else 1.0 / len(chosen)) for x in inv]
            else:
                w = [1.0 / len(chosen)] * len(chosen)
            weights = {r["ticker"]: wi for r, wi in zip(chosen, w)}
            gross = float(sum(wi * r["raw20"] for r, wi in zip(chosen, w)))
        else:
            weights = {}
            gross = 0.0  # flat / cash

        names = set(weights) | set(prev_w)
        turnover = 0.5 * sum(abs(weights.get(t, 0.0) - prev_w.get(t, 0.0)) for t in names)
        gross_periods.append(gross)
        turnovers.append(turnover)
        for bps in cost_bps:
            net_periods[bps].append(gross - 2.0 * turnover * (bps / 10000.0))
        prev_w = weights
        used += 1

    out = {"basket_size": n, "weighting": weighting, "held_dates": used,
           "avg_turnover": float(np.mean(turnovers)) if turnovers else None,
           "gross": _summarize(gross_periods, periods_per_year)}
    for bps in cost_bps:
        out[f"net_{bps}bps"] = _summarize(net_periods[bps], periods_per_year)
    return out


# --------------------------------------------------------------------------- #
# Candidate metric assembly (IC block reused from 5-F0; portfolio from _simulate)
# --------------------------------------------------------------------------- #
def _ic_block(p5c, f0, preds: List[dict]) -> dict:
    """IC / decile / regime / year block, reusing f0.evaluate_v3 but discarding its
    built-in (monthly top-N) portfolios — portfolios are recomputed per candidate."""
    ev = f0.evaluate_v3(p5c, preds)
    if not ev.get("evaluable"):
        return {"evaluable": False, "reason": ev.get("reason", "not evaluable")}
    return {
        "evaluable": True,
        "n_scored_dates": ev["n_scored_dates"], "n_predictions": ev["n_predictions"],
        "mean_rank_ic": ev["mean_rank_ic"], "median_rank_ic": ev["median_rank_ic"],
        "ic_t_stat": ev.get("ic_t_stat"), "positive_ic_hit_rate": ev["positive_ic_hit_rate"],
        "ic_by_year": ev["ic_by_year"], "worst_year": ev.get("worst_year"),
        "worst_year_ic": ev.get("worst_year_ic"), "ic_by_regime": ev["ic_by_regime"],
        "mean_decile_spread": ev["mean_decile_spread"],
        "top_quintile_hit_rate": ev["top_quintile_hit_rate"],
    }


def _candidate(p5c, f0, name: str, preds: List[dict], *, cadence: str,
               coverage: Optional[float] = None, notes: str = "",
               active_dates: Optional[set] = None, entry_rank: Optional[int] = None,
               exit_rank: Optional[int] = None,
               sector_of: Optional[Dict[str, str]] = None,
               max_per_sector: Optional[int] = None,
               has_gating: bool = False) -> dict:
    """Assemble the full metric block for one candidate strategy."""
    block = _ic_block(p5c, f0, preds)
    block["candidate"] = name
    block["cadence"] = cadence
    block["rebalance_coverage"] = _f(coverage) if coverage is not None else None
    block["notes"] = notes
    block["has_explicit_gating"] = has_gating
    if not block.get("evaluable"):
        block["portfolios"] = {}
        return block
    by_date: Dict[str, List[dict]] = {}
    for r in preds:
        by_date.setdefault(r["date"], []).append(r)
    dates = sorted(by_date)
    ppy = PERIODS_PER_YEAR.get(cadence, 12)
    portfolios = {}
    for nn in f0.TOP_N_PORTFOLIO:
        for wgt in ("equal", "vol_adj"):
            # band / sector cap only applied to the headline top-10 equal basket
            use_band = (entry_rank is not None and nn == 10 and wgt == "equal")
            use_cap = (max_per_sector is not None and nn == 10 and wgt == "equal")
            portfolios[f"top{nn}_{wgt}"] = _simulate(
                by_date, dates, nn, wgt, f0.COST_BPS, ppy,
                active_dates=active_dates,
                entry_rank=entry_rank if use_band else None,
                exit_rank=exit_rank if use_band else None,
                sector_of=sector_of if use_cap else None,
                max_per_sector=max_per_sector if use_cap else None)
    block["portfolios"] = portfolios
    return block


# --------------------------------------------------------------------------- #
# Risk filters (liquidity floor, volatility cap, extreme overextension)
# --------------------------------------------------------------------------- #
def _feature_lookup(panel: List[dict]) -> Dict[Tuple[str, str], dict]:
    out: Dict[Tuple[str, str], dict] = {}
    for r in panel:
        fe = r["features"]
        out[(r["date"], r["ticker"])] = {
            "avg_dollar_volume_20d": r.get("avg_dollar_volume_20d"),
            "realized_vol_63d": fe.get("realized_vol_63d"),
            "return_zscore_20d": fe.get("return_zscore_20d"),
        }
    return out


def _apply_risk_filters(preds: List[dict], flookup: Dict[Tuple[str, str], dict]) -> List[dict]:
    """Drop illiquid names, per-date extreme-volatility names, and per-date extreme
    overextension names. Per-date percentile caps are computed cross-sectionally."""
    by_date: Dict[str, List[dict]] = {}
    for r in preds:
        by_date.setdefault(r["date"], []).append(r)
    kept: List[dict] = []
    for d, recs in by_date.items():
        vols, zexts = [], []
        for r in recs:
            fl = flookup.get((d, r["ticker"]), {})
            if fl.get("realized_vol_63d") is not None:
                vols.append(fl["realized_vol_63d"])
            if fl.get("return_zscore_20d") is not None:
                zexts.append(fl["return_zscore_20d"])
        vol_cap = float(np.quantile(vols, RISK_VOL_CAP_PCTILE)) if vols else None
        z_cap = float(np.quantile(zexts, RISK_OVEREXT_CAP_PCTILE)) if zexts else None
        for r in recs:
            fl = flookup.get((d, r["ticker"]), {})
            adv = fl.get("avg_dollar_volume_20d")
            if adv is not None and adv < RISK_LIQUIDITY_FLOOR:
                continue
            if vol_cap is not None and fl.get("realized_vol_63d") is not None \
                    and fl["realized_vol_63d"] > vol_cap:
                continue
            if z_cap is not None and fl.get("return_zscore_20d") is not None \
                    and fl["return_zscore_20d"] > z_cap:
                continue
            kept.append(r)
    return kept


# --------------------------------------------------------------------------- #
# Phase 5-C reference predictions in the unified schema
# --------------------------------------------------------------------------- #
def _phase5c_reference_preds(p5c, al) -> Tuple[List[dict], Optional[str]]:
    """Rerun the Phase 5-C harness and return the best 5-C model's OOS predictions
    mapped to the unified schema (vol63 is unavailable in 5-C -> None)."""
    panel5c = p5c.build_panel(al)
    preds5c, _ = p5c.walk_forward(panel5c)
    boards = {m: p5c.evaluate_model(preds5c[m]) for m in p5c.MODELS}
    evalu = [(m, s) for m, s in boards.items() if s.get("evaluable")]
    if not evalu:
        return [], None
    best_name, _ = max(evalu, key=lambda kv: kv[1]["mean_rank_ic"])
    mapped = [{"date": r["date"], "year": r.get("year", int(r["date"][:4])),
               "regime": r["regime"], "ticker": r["ticker"], "score": r["score"],
               "y": r["y"], "raw20": r.get("raw20"), "vol63": None}
              for r in preds5c[best_name]]
    return mapped, best_name


# --------------------------------------------------------------------------- #
# Readiness gates + recommendation
# --------------------------------------------------------------------------- #
def _net_metric(block: dict) -> Optional[float]:
    """Reported net portfolio metric: top-10 equal-weight net-of-50bps total return.
    NOTE: total return is survivorship-inflated and not comparable across cadences;
    use ``_net_ann_mean`` for any cross-candidate decision."""
    if not block.get("evaluable"):
        return None
    p = block.get("portfolios", {}).get("top10_equal", {})
    return p.get("net_50bps", {}).get("total_return")


def _net_ann_mean(block: dict) -> Optional[float]:
    """Cadence-comparable net portfolio metric for decisions: top-10 equal-weight
    net-of-50bps ANNUALIZED MEAN return (no compounding -> no survivorship explosion)."""
    if not block.get("evaluable"):
        return None
    p = block.get("portfolios", {}).get("top10_equal", {})
    return p.get("net_50bps", {}).get("ann_mean_return")


def _turnover(block: dict) -> Optional[float]:
    if not block.get("evaluable"):
        return None
    return block.get("portfolios", {}).get("top10_equal", {}).get("avg_turnover")


def _drawdown(block: dict) -> Optional[float]:
    if not block.get("evaluable"):
        return None
    return block.get("portfolios", {}).get("top10_equal", {}).get("gross", {}).get("max_drawdown")


def build_readiness_gates(best: dict, baseline: dict, ensemble: dict,
                          placebo_ic: Optional[float]) -> List[dict]:
    """PASS / FAIL / WARNING / NOT_EVALUABLE per readiness gate, judged on the best
    quality candidate vs the Phase 5-C baseline. Never forces a PASS."""
    gates: List[dict] = []

    def add(name, status, metric, value, threshold, note=""):
        gates.append({"gate_name": name, "status": status, "metric": metric,
                      "value": value, "threshold": threshold, "note": note})

    ev = best.get("evaluable", False)
    base_net = _net_ann_mean(baseline)
    best_net = _net_ann_mean(best)
    ens_turn = _turnover(ensemble)
    best_turn = _turnover(best)

    placebo_clean = placebo_ic is None or abs(placebo_ic) <= 0.02
    add("no_leakage_placebo_gate", "PASS" if placebo_clean else "FAIL",
        "placebo_mean_ic", _f(placebo_ic), "|placebo IC|<=0.02",
        "ridge mean rank IC after within-date label shuffle must collapse to ~0")

    if not ev:
        add("improves_net_portfolio_over_5c_gate", "NOT_EVALUABLE",
            "best_minus_baseline_net50_ann_mean", None, ">0")
    elif base_net is None or best_net is None:
        add("improves_net_portfolio_over_5c_gate", "NOT_EVALUABLE",
            "best_minus_baseline_net50_ann_mean", None, ">0", "missing portfolio metric")
    else:
        delta = best_net - base_net
        add("improves_net_portfolio_over_5c_gate", "PASS" if delta > 0 else "FAIL",
            "best_minus_baseline_net50_ann_mean", _f(delta), ">0",
            f"best top10 net50 ann-mean={_f(best_net)} vs 5-C baseline={_f(base_net)} "
            "(annualized mean, not compounded; absolute level still survivorship-inflated)")

    if not ev:
        add("ic_positive_and_stable_gate", "NOT_EVALUABLE", "mean_ic_and_worst_year",
            None, ">0 & worst-year IC>-0.05")
    else:
        ic = best.get("mean_rank_ic", 0.0)
        wy = best.get("worst_year_ic")
        stable = (wy is None) or (wy > GATE_WORST_YEAR_IC)
        ok = ic > 0 and stable
        add("ic_positive_and_stable_gate", "PASS" if ok else ("WARNING" if ic > 0 else "FAIL"),
            "mean_ic_and_worst_year",
            json.dumps({"mean_ic": _f(ic), "worst_year_ic": _f(wy)}),
            f">0 & worst-year IC>{GATE_WORST_YEAR_IC}")

    if not ev:
        add("decile_spread_after_cost_gate", "NOT_EVALUABLE", "decile_and_net50", None, ">0")
    else:
        ds = best.get("mean_decile_spread", 0.0)
        ok = ds > 0 and (best_net is not None and best_net > 0)
        add("decile_spread_after_cost_gate", "PASS" if ok else "FAIL",
            "decile_and_net50",
            json.dumps({"decile_spread": _f(ds), "top10_net50_ann_mean": _f(best_net)}), ">0")

    if not ev or best_turn is None:
        add("turnover_improved_or_acceptable_gate", "NOT_EVALUABLE",
            "best_turnover", None, f"< ensemble OR <{GATE_TURNOVER_ACCEPTABLE}")
    else:
        improved = ens_turn is not None and best_turn < ens_turn
        acceptable = best_turn < GATE_TURNOVER_ACCEPTABLE
        ok = improved or acceptable
        add("turnover_improved_or_acceptable_gate", "PASS" if ok else "WARNING",
            "best_turnover", _f(best_turn),
            f"< ensemble({_f(ens_turn)}) OR <{GATE_TURNOVER_ACCEPTABLE}",
            "improved vs raw ensemble" if improved else "")

    dd = _drawdown(best)
    if not ev or dd is None:
        add("drawdown_acceptable_gate", "NOT_EVALUABLE", "top10_gross_max_drawdown",
            None, f">{GATE_DRAWDOWN_ACCEPTABLE}")
    else:
        add("drawdown_acceptable_gate", "PASS" if dd > GATE_DRAWDOWN_ACCEPTABLE
            else "WARNING", "top10_gross_max_drawdown", _f(dd),
            f">{GATE_DRAWDOWN_ACCEPTABLE}")

    add("explicit_no_trade_confidence_logic_gate",
        "PASS" if best.get("has_explicit_gating") else "WARNING",
        "best_candidate_has_gating", bool(best.get("has_explicit_gating")), "true",
        f"best candidate = {best.get('candidate')}")

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


def derive_recommendation(best: dict, baseline: dict, ensemble: dict,
                          gates: List[dict]) -> Tuple[str, str]:
    """Honest, gate-driven recommendation. Never forces a PASS; never recommends live."""
    status = {g["gate_name"]: g["status"] for g in gates}
    if not best.get("evaluable", False):
        return REC_NO_IMPROVE, "no quality candidate produced evaluable OOS predictions"
    if status.get("no_leakage_placebo_gate") == "FAIL":
        return REC_NO_IMPROVE, "placebo leakage check failed; no trustworthy edge"

    ready_gates = [
        "improves_net_portfolio_over_5c_gate", "ic_positive_and_stable_gate",
        "decile_spread_after_cost_gate", "turnover_improved_or_acceptable_gate",
        "drawdown_acceptable_gate", "explicit_no_trade_confidence_logic_gate",
        "shadow_only_not_live_gate",
    ]
    all_ready = all(status.get(g) == "PASS" for g in ready_gates)
    if all_ready and status.get("no_leakage_placebo_gate") == "PASS":
        return (REC_READY, "the best quality candidate improves net portfolio metrics over "
                "the Phase 5-C reference with positive/stable IC, positive decile spread "
                "after cost, acceptable/improved turnover and drawdown, explicit no-trade "
                "logic, and no leakage — promote it as a SHADOW candidate (not live)")

    # A real but not-yet-sufficient improvement -> shadow-only.
    ic = best.get("mean_rank_ic", 0.0)
    decile_ok = status.get("decile_spread_after_cost_gate") != "FAIL" \
        or best.get("mean_decile_spread", 0.0) > 0
    turnover_ok = status.get("turnover_improved_or_acceptable_gate") in ("PASS", "WARNING")
    if ic > 0 and decile_ok and turnover_ok and status.get("no_leakage_placebo_gate") == "PASS":
        return (REC_SHADOW_ONLY, "a leakage-clean edge with explicit quality controls is "
                "present and is at least as tradable as the raw signal, but it does not "
                "clearly beat the Phase 5-C reference on every readiness gate; observe it "
                "in shadow only before any deployment decision")
    return (REC_NO_IMPROVE, "the quality controls did not produce a robust, leakage-clean "
            "improvement over the Phase 5-C reference; keep iterating before shadow trading")


# --------------------------------------------------------------------------- #
# Artifact writers
# --------------------------------------------------------------------------- #
def _candidate_row(block: dict) -> list:
    if not block.get("evaluable"):
        return [block.get("candidate"), block.get("cadence"), False] + [""] * 10
    p10 = block.get("portfolios", {}).get("top10_equal", {})
    return [block["candidate"], block["cadence"], True, _f(block["mean_rank_ic"]),
            _f(block.get("ic_t_stat")), _f(block["mean_decile_spread"]),
            block["n_scored_dates"], block.get("rebalance_coverage"),
            _f(p10.get("avg_turnover")),
            _f(p10.get("gross", {}).get("max_drawdown")),
            _f(p10.get("net_50bps", {}).get("ann_mean_return")),
            _f(p10.get("net_50bps", {}).get("total_return")),
            block.get("notes", "")]


def _write_artifacts(candidates: Dict[str, dict], best_name: str, gates: List[dict],
                     horizon_rows: List[list], industry_rows: List[list]) -> None:
    # candidate matrix
    cm_header = ["candidate", "cadence", "evaluable", "mean_rank_ic", "ic_t_stat",
                 "mean_decile_spread", "n_scored_dates", "rebalance_coverage",
                 "top10_equal_avg_turnover", "top10_equal_gross_max_drawdown",
                 "top10_equal_net_50bps_ann_mean_return",
                 "top10_equal_net_50bps_total_return_survivorship_inflated", "notes"]
    cm_rows = []
    for key in CANDIDATE_ORDER:
        if key == C_BEST:
            blk = candidates.get(best_name)
            if blk is not None:
                row = _candidate_row(blk)
                row[0] = f"{C_BEST} (= {best_name})"
                cm_rows.append(row)
            continue
        blk = candidates.get(key)
        if blk is None:
            cm_rows.append([key, "", False] + ["unavailable"] + [""] * 9)
            continue
        cm_rows.append(_candidate_row(blk))
    _write_csv(_CANDIDATE_MATRIX_OUT, cm_header, cm_rows)

    # horizon report
    _write_csv(_HORIZON_OUT,
               ["signal", "label_horizon", "mean_rank_ic", "ic_t_stat",
                "mean_decile_spread", "n_scored_dates"], horizon_rows)

    # regime report
    reg_rows = []
    for key in CANDIDATE_ORDER:
        blk = candidates.get(key if key != C_BEST else best_name)
        if blk is None or not blk.get("evaluable"):
            continue
        label = f"{C_BEST} (= {best_name})" if key == C_BEST else key
        for reg, v in blk.get("ic_by_regime", {}).items():
            reg_rows.append([label, reg, _f(v["mean_ic"]), v["n_dates"]])
    _write_csv(_REGIME_OUT, ["candidate", "regime", "mean_rank_ic", "n_dates"], reg_rows)

    # turnover / cost + portfolio reports
    turn_rows, port_rows = [], []
    for key in CANDIDATE_ORDER:
        if key == C_BEST:
            continue
        blk = candidates.get(key)
        if blk is None or not blk.get("evaluable"):
            continue
        for pkey, p in blk.get("portfolios", {}).items():
            if not isinstance(p, dict) or "weighting" not in p:
                continue
            n, wgt = p["basket_size"], p["weighting"]
            turn_rows.append([blk["candidate"], blk["cadence"], n, wgt,
                              _f(p.get("avg_turnover")),
                              _f(p.get("gross", {}).get("total_return")),
                              _f(p.get("net_25bps", {}).get("total_return")),
                              _f(p.get("net_50bps", {}).get("total_return"))])
            g = p.get("gross", {})
            port_rows.append([blk["candidate"], blk["cadence"], n, wgt, p.get("held_dates"),
                              _f(g.get("mean_period_return")), _f(g.get("total_return")),
                              _f(g.get("approx_cagr")), _f(g.get("max_drawdown")),
                              _f(p.get("net_50bps", {}).get("total_return"))])
    _write_csv(_TURNOVER_OUT,
               ["candidate", "cadence", "basket_size", "weighting", "avg_turnover",
                "gross_total_return", "net_25bps_total_return", "net_50bps_total_return"],
               turn_rows)
    _write_csv(_PORTFOLIO_OUT,
               ["candidate", "cadence", "basket_size", "weighting", "held_dates",
                "gross_mean_period_return", "gross_total_return", "gross_approx_cagr",
                "gross_max_drawdown", "net_50bps_total_return"], port_rows)

    # industry control report
    _write_csv(_INDUSTRY_OUT,
               ["control", "available", "mean_rank_ic", "top10_equal_avg_turnover",
                "top10_equal_net_50bps_total_return", "note"], industry_rows)

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
        "gated_on": f"Phase 5-F0B recommendation = {REC_READY}.",
        "phase5f0b_recommendation": recommendation,
        "phase5f0b_best_candidate": best_name,
        "proceed_to_shadow": recommendation == REC_READY,
        "scope_if_proceed": [
            "Freeze the winning quality-controlled candidate (signal + gating + "
            "rebalance + position rules) from the walk-forward.",
            "Observe it in SHADOW only: record paper signals and would-be positions "
            "without placing any orders.",
            "Re-validate on a survivorship-free universe before trusting absolute returns.",
            "Track live rank IC, turnover, drawdown, and regime behavior vs backtest.",
        ],
        "hard_constraints": [
            "Shadow-only: no orders, no broker execution, no automation, no GCP deploy.",
            "Survivorship bias must be resolved before trusting absolute returns.",
            "Preview-only; manual review; paper trading only.",
        ],
        "if_not_ready": ("Keep the Phase 5-C price-only baseline; iterate on confidence / "
                         "regime / turnover controls before any shadow observation."),
        "preview_only": True, "orders_enabled": False, "automation_enabled": False,
        "broker_execution_enabled": False, "production_replacement": False,
    }


# --------------------------------------------------------------------------- #
# Main orchestration
# --------------------------------------------------------------------------- #
def run(price_history: Optional[dict] = None, out_dir: Optional[Path] = None,
        price_csv: Optional[str] = None, phase5f0_runner: Optional[Path] = None,
        industry_csv: Optional[Path] = None,
        industry_map: Optional[Dict[str, str]] = None,
        max_tickers: Optional[int] = None, weekly: bool = True,
        write: bool = True, verbose: bool = True) -> dict:
    """Run the signal-quality-upgrade harness. Inputs can be injected (tests) or
    loaded from disk (real run). Always returns a report dict whose
    ``recommendation`` is one of ``ALLOWED_RECOMMENDATIONS``."""
    global _OUT_DIR, _REPORT_OUT, _CANDIDATE_MATRIX_OUT, _HORIZON_OUT, _REGIME_OUT
    global _TURNOVER_OUT, _PORTFOLIO_OUT, _INDUSTRY_OUT, _GATE_MATRIX_OUT, _SHADOW_PLAN_OUT
    if out_dir is not None:
        _OUT_DIR = Path(out_dir)
        _REPORT_OUT = _OUT_DIR / "phase5f0b_signal_quality_upgrade.json"
        _CANDIDATE_MATRIX_OUT = _OUT_DIR / "signal_quality_candidate_matrix.csv"
        _HORIZON_OUT = _OUT_DIR / "signal_quality_horizon_report.csv"
        _REGIME_OUT = _OUT_DIR / "signal_quality_regime_report.csv"
        _TURNOVER_OUT = _OUT_DIR / "signal_quality_turnover_cost_report.csv"
        _PORTFOLIO_OUT = _OUT_DIR / "signal_quality_portfolio_report.csv"
        _INDUSTRY_OUT = _OUT_DIR / "signal_quality_industry_control_report.csv"
        _GATE_MATRIX_OUT = _OUT_DIR / "signal_quality_validation_gate_matrix.csv"
        _SHADOW_PLAN_OUT = _OUT_DIR / "phase5f1_shadow_candidate_plan.json"

    f0 = _load_phase5f0(phase5f0_runner)
    p5c = f0._load_phase5c()

    resolved_price_csv = None
    if price_history is None:
        resolved_price_csv = price_csv or p5c._data_path()
        if resolved_price_csv:
            price_history = p5c.load_local_history(resolved_price_csv)

    # industry map (optional; Ticker + IndustryId only)
    if industry_map is None:
        industry_map = load_industry_map(industry_csv)

    base_report = {
        "phase": PHASE,
        "objective": ("Can confidence gating, horizon selection, model agreement, regime "
                      "filters, sector/industry controls, and turnover controls turn the "
                      "existing weak/modest price/volume/regime ranking edge into a "
                      "higher-quality, more tradable (shadow-candidate) signal — without "
                      "new providers and without deployment?"),
        "track": "A (quantitative signal-quality evidence) — not Track B operational packaging.",
        "data_source": resolved_price_csv,
        "benchmark_regime_proxy": f0.BENCHMARK,
        "primary_label": f0.PRIMARY_LABEL,
        "candidate_set": CANDIDATE_ORDER,
        "recommended_next_phase": "5-F1 (shadow candidate)",
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
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
            "universe_size": 0, "candidates_evaluated": [], "best_candidate": None,
            "phase5c_reference_metrics": {}, "alpha_v3_reference_metrics": {},
            "best_candidate_metrics": {}, "quality_improvement_vs_phase5c": {},
            "readiness_gate_summary": {"PASS": 0, "FAIL": 0, "WARNING": 0, "NOT_EVALUABLE": 0},
            "recommendation": REC_DATA_BLOCKER,
            "recommendation_reason": "no local price-history CSV found (D: read-only or repo fallback)",
        })
        assert base_report["recommendation"] in ALLOWED_RECOMMENDATIONS
        if write:
            _write_json(_REPORT_OUT, base_report)
            _write_json(_SHADOW_PLAN_OUT, _shadow_plan(REC_DATA_BLOCKER, None))
        if verbose:
            _print_summary(base_report, {}, [])
        return base_report

    al = p5c.Aligned(price_history)
    if max_tickers:
        keep = set(sorted(al.tickers)[:max_tickers])
        al.tickers = [t for t in al.tickers if t in keep]
    universe_size = len(al.tickers)
    date_range = (al.master_dates[0], al.master_dates[-1])

    # sector availability
    sector_of = {t: industry_map[t] for t in al.tickers if t in industry_map}
    sector_coverage = (len(sector_of) / universe_size) if universe_size else 0.0
    sector_available = sector_coverage >= INDUSTRY_COVERAGE_MIN

    # ---- monthly Alpha V3 ensemble (the base signal) ----
    panel_m = _build_v3_panel_at(p5c, f0, al, al.month_end_indices())
    comp_m, _ = f0.run_variant(p5c, panel_m, f0._COMPOSITE_FEATURES, f0.PRIMARY_LABEL, "composite")
    ridge_m, _ = f0.run_variant(p5c, panel_m, f0.FEATURES, f0.PRIMARY_LABEL, "ridge")
    tree_m, _ = f0.run_variant(p5c, panel_m, f0.FEATURES, f0.PRIMARY_LABEL, "tree")
    components_m = {f0.M_COMPOSITE: comp_m, f0.M_RIDGE: ridge_m, f0.M_TREE: tree_m}
    ens_m = f0.ensemble_predictions(components_m)

    # ---- leakage probe (ridge on full V3 features, label shuffle) ----
    placebo_ic = f0._placebo_ic(p5c, panel_m, f0.FEATURES, f0.PRIMARY_LABEL)

    candidates: Dict[str, dict] = {}

    # baseline: Phase 5-C reference
    base_preds, base5c_model = _phase5c_reference_preds(p5c, al)
    candidates[C_BASELINE] = _candidate(
        p5c, f0, C_BASELINE, base_preds, cadence="monthly",
        notes=f"Phase 5-C price-only reference (best 5-C model = {base5c_model}); the baseline to beat")

    # raw alpha v3 ensemble
    candidates[C_ENSEMBLE] = _candidate(
        p5c, f0, C_ENSEMBLE, ens_m, cadence="monthly",
        notes="raw Phase 5-F0 ensemble; no quality control (reference point)")

    # high-confidence only (model agreement + no-trade zone)
    disp = _component_dispersion(components_m)
    ens_rank_m = _norm_rank_map(ens_m)
    hiconf = []
    for r in ens_m:
        k = (r["date"], r["ticker"])
        rk = ens_rank_m.get(k)
        dp = disp.get(k)
        if rk is None or dp is None:
            continue
        if dp <= CONF_DISPERSION_MAX and (rk >= CONF_HI_RANK or rk <= CONF_LO_RANK):
            hiconf.append(r)
    hiconf_cov = (len(hiconf) / len(ens_m)) if ens_m else 0.0
    candidates[C_HICONF] = _candidate(
        p5c, f0, C_HICONF, hiconf, cadence="monthly", coverage=hiconf_cov, has_gating=True,
        notes=(f"keep names with component-rank dispersion<={CONF_DISPERSION_MAX} and ensemble "
               f"rank>={CONF_HI_RANK} or <={CONF_LO_RANK} (no-trade middle); "
               f"coverage={hiconf_cov:.2f}"))

    # risk-on only (regime gate)
    riskon_dates = {r["date"] for r in ens_m if r["regime"] == "risk_on"}
    riskon_preds = [r for r in ens_m if r["regime"] == "risk_on"]
    riskon_cov = (len(riskon_preds) / len(ens_m)) if ens_m else 0.0
    candidates[C_RISKON] = _candidate(
        p5c, f0, C_RISKON, riskon_preds, cadence="monthly", coverage=riskon_cov,
        active_dates=riskon_dates, has_gating=True,
        notes=f"trade only in the SPY risk-on regime; flat otherwise; coverage={riskon_cov:.2f}")

    # weekly top-10
    if weekly:
        panel_w = _build_v3_panel_at(p5c, f0, al, _week_end_indices(al.master_dates))
        comp_w, _ = f0.run_variant(p5c, panel_w, f0._COMPOSITE_FEATURES, f0.PRIMARY_LABEL, "composite")
        ridge_w, _ = f0.run_variant(p5c, panel_w, f0.FEATURES, f0.PRIMARY_LABEL, "ridge")
        tree_w, _ = f0.run_variant(p5c, panel_w, f0.FEATURES, f0.PRIMARY_LABEL, "tree")
        ens_w = f0.ensemble_predictions({f0.M_COMPOSITE: comp_w, f0.M_RIDGE: ridge_w,
                                         f0.M_TREE: tree_w})
        candidates[C_WEEKLY] = _candidate(
            p5c, f0, C_WEEKLY, ens_w, cadence="weekly", has_gating=False,
            notes="weekly rebalance of the ensemble top-10 (turnover/cost sensitivity)")
    else:
        candidates[C_WEEKLY] = {"candidate": C_WEEKLY, "cadence": "weekly",
                                "evaluable": False, "reason": "weekly cadence disabled",
                                "portfolios": {}, "notes": "weekly disabled", "ic_by_regime": {}}

    # entry/exit band (hold-until-deteriorates)
    candidates[C_BAND] = _candidate(
        p5c, f0, C_BAND, ens_m, cadence="monthly", entry_rank=BAND_ENTRY_RANK,
        exit_rank=BAND_EXIT_RANK, has_gating=True,
        notes=(f"enter on ensemble rank in top {BAND_ENTRY_RANK}; hold until rank position "
               f">= {BAND_EXIT_RANK} (low-churn turnover control)"))

    # industry-capped top-10 (only if a reliable local industry map exists)
    if sector_available:
        candidates[C_INDCAP] = _candidate(
            p5c, f0, C_INDCAP, ens_m, cadence="monthly", sector_of=sector_of,
            max_per_sector=SECTOR_MAX_PER_INDUSTRY, has_gating=True,
            coverage=sector_coverage,
            notes=(f"max {SECTOR_MAX_PER_INDUSTRY} names per industry/sector "
                   f"(local IndustryId map; coverage={sector_coverage:.2f})"))
    # else: omitted from `candidates` -> reported as unavailable.

    # blended horizon signal (mean within-date rank of ridge @5d/@10d/@20d)
    ridge5, _ = f0.run_variant(p5c, panel_m, f0.FEATURES, "forward_excess_return_5d_vs_spy", "ridge")
    ridge10, _ = f0.run_variant(p5c, panel_m, f0.FEATURES, "forward_excess_return_10d_vs_spy", "ridge")
    blended_raw = f0.ensemble_predictions({"h5": ridge5, "h10": ridge10, "h20": ridge_m})
    blended = _attach_labels(blended_raw, ens_m)  # evaluate against the 20d primary label
    candidates[C_BLEND] = _candidate(
        p5c, f0, C_BLEND, blended, cadence="monthly", has_gating=False,
        notes="mean within-date normalized rank of ridge signals at 5/10/20-day horizons")

    # ---- best quality candidate: the best of the NON-baseline candidates on a
    # composite quality score (net-50bps return rank + IC rank + low-turnover rank).
    contenders = [k for k in (C_ENSEMBLE, C_HICONF, C_RISKON, C_WEEKLY, C_BAND, C_INDCAP, C_BLEND)
                  if k in candidates and candidates[k].get("evaluable")]

    # "Best quality" = highest rank IC (the trustworthy quality metric, exactly as
    # Phase 5-F0 concluded), with ties broken toward LOWER turnover and then HIGHER
    # annualized-mean net return. Absolute (compounded total) portfolio return is
    # DELIBERATELY excluded from selection: it is survivorship-inflated and not
    # comparable across cadences (weekly compounding explodes it). Several candidates
    # (entry/exit band, industry-capped) only re-shape the portfolio and therefore
    # share the ensemble's IC; the tie-breaks correctly prefer the lower-churn one.
    def _best_key(k: str):
        b = candidates[k]
        ic = b.get("mean_rank_ic")
        turn = _turnover(b)
        net = _net_ann_mean(b)
        return (ic if ic is not None else -9.9,
                -(turn if turn is not None else 9.9),
                net if net is not None else -9.9)

    if contenders:
        best_name = max(contenders, key=_best_key)
    else:
        best_name = C_ENSEMBLE if candidates.get(C_ENSEMBLE, {}).get("evaluable") else None
    best = candidates.get(best_name, {"evaluable": False, "reason": "no evaluable candidate",
                                      "portfolios": {}, "ic_by_regime": {}})

    # ---- horizon report (standalone ridge IC at each horizon vs the 20d label) ----
    horizon_rows = []
    for hz, src in (("5d", ridge5), ("10d", ridge10), ("20d", ridge_m)):
        b = _ic_block(p5c, f0, _attach_labels(src, ens_m))
        if b.get("evaluable"):
            horizon_rows.append([f"ridge@{hz}", "vs_20d_excess", _f(b["mean_rank_ic"]),
                                 _f(b.get("ic_t_stat")), _f(b["mean_decile_spread"]),
                                 b["n_scored_dates"]])
    bb = candidates[C_BLEND]
    if bb.get("evaluable"):
        horizon_rows.append(["blended_5_10_20", "vs_20d_excess", _f(bb["mean_rank_ic"]),
                             _f(bb.get("ic_t_stat")), _f(bb["mean_decile_spread"]),
                             bb["n_scored_dates"]])

    # ---- risk filters (effect on the base ensemble IC) ----
    flookup = _feature_lookup(panel_m)
    risk_filtered = _apply_risk_filters(ens_m, flookup)
    rf_block = _ic_block(p5c, f0, risk_filtered)
    risk_filters = {
        "liquidity_floor_usd": RISK_LIQUIDITY_FLOOR,
        "volatility_cap_percentile": RISK_VOL_CAP_PCTILE,
        "overextension_cap_percentile": RISK_OVEREXT_CAP_PCTILE,
        "names_kept_fraction": _f(len(risk_filtered) / len(ens_m)) if ens_m else None,
        "ensemble_ic_before": _f(candidates[C_ENSEMBLE].get("mean_rank_ic")
                                 if candidates[C_ENSEMBLE].get("evaluable") else None),
        "ensemble_ic_after": _f(rf_block.get("mean_rank_ic") if rf_block.get("evaluable") else None),
        "note": ("liquidity floor + per-date volatility cap + per-date extreme-overextension "
                 "cap; reported as a diagnostic, not a separate ranked candidate"),
    }

    # ---- industry control report ----
    industry_rows = []
    ens_blk = candidates[C_ENSEMBLE]
    if ens_blk.get("evaluable"):
        p10 = ens_blk["portfolios"]["top10_equal"]
        industry_rows.append(["raw_ensemble_top10", True, _f(ens_blk["mean_rank_ic"]),
                              _f(p10.get("avg_turnover")),
                              _f(p10.get("net_50bps", {}).get("total_return")),
                              "no industry control"])
    if sector_available and candidates.get(C_INDCAP, {}).get("evaluable"):
        ic_blk = candidates[C_INDCAP]
        p10 = ic_blk["portfolios"]["top10_equal"]
        industry_rows.append(["industry_capped_top10", True, _f(ic_blk["mean_rank_ic"]),
                              _f(p10.get("avg_turnover")),
                              _f(p10.get("net_50bps", {}).get("total_return")),
                              f"max {SECTOR_MAX_PER_INDUSTRY}/sector; coverage={sector_coverage:.2f}"])
    else:
        industry_rows.append(["industry_capped_top10", False, "", "", "",
                              "local industry map unavailable or insufficient coverage"])

    # ---- gates + recommendation ----
    gates = build_readiness_gates(best, candidates[C_BASELINE], candidates[C_ENSEMBLE], placebo_ic)
    recommendation, why = derive_recommendation(best, candidates[C_BASELINE],
                                                candidates[C_ENSEMBLE], gates)

    base_ann = _net_ann_mean(candidates[C_BASELINE])
    best_ann = _net_ann_mean(best)
    quality_improvement = {
        "baseline_5c_model": base5c_model,
        "baseline_mean_rank_ic": _f(candidates[C_BASELINE].get("mean_rank_ic")
                                    if candidates[C_BASELINE].get("evaluable") else None),
        "raw_ensemble_mean_rank_ic": _f(candidates[C_ENSEMBLE].get("mean_rank_ic")
                                        if candidates[C_ENSEMBLE].get("evaluable") else None),
        "best_candidate": best_name,
        "best_mean_rank_ic": _f(best.get("mean_rank_ic")) if best.get("evaluable") else None,
        "delta_ic_best_minus_baseline": (
            _f(best["mean_rank_ic"] - candidates[C_BASELINE]["mean_rank_ic"])
            if (best.get("evaluable") and candidates[C_BASELINE].get("evaluable")) else None),
        "best_beats_baseline_on_ic": bool(
            best.get("evaluable") and candidates[C_BASELINE].get("evaluable")
            and best["mean_rank_ic"] > candidates[C_BASELINE]["mean_rank_ic"]),
        "decision_metric": "top10_equal net-of-50bps ANNUALIZED MEAN return (cadence-comparable)",
        "baseline_top10_net50_ann_mean": _f(base_ann),
        "best_top10_net50_ann_mean": _f(best_ann),
        "delta_net50_ann_mean_best_minus_baseline": (_f(best_ann - base_ann)
                                                     if (best_ann is not None and base_ann is not None) else None),
        "baseline_top10_net50_total_return_informational": _f(_net_metric(candidates[C_BASELINE])),
        "best_top10_net50_total_return_informational": _f(_net_metric(best)),
        "best_top10_avg_turnover": _f(_turnover(best)),
        "raw_ensemble_top10_avg_turnover": _f(_turnover(candidates[C_ENSEMBLE])),
        "note": ("absolute (compounded total) portfolio returns are survivorship-inflated and "
                 "NOT comparable across cadences; rank IC, decile spread, turnover, and the "
                 "annualized-mean net return are the trustworthy quality signals"),
    }

    def _metric_view(block: dict) -> dict:
        if not block.get("evaluable"):
            return {"evaluable": False, "reason": block.get("reason", "")}
        p10e = block.get("portfolios", {}).get("top10_equal", {})
        p10v = block.get("portfolios", {}).get("top10_vol_adj", {})
        return {
            "evaluable": True, "candidate": block.get("candidate"),
            "cadence": block.get("cadence"), "n_scored_dates": block["n_scored_dates"],
            "rebalance_coverage": block.get("rebalance_coverage"),
            "mean_rank_ic": _f(block["mean_rank_ic"]), "ic_t_stat": _f(block.get("ic_t_stat")),
            "worst_year_ic": _f(block.get("worst_year_ic")),
            "mean_decile_spread": _f(block["mean_decile_spread"]),
            "ic_by_regime": {k: {"mean_ic": _f(v["mean_ic"]), "n_dates": v["n_dates"]}
                             for k, v in block.get("ic_by_regime", {}).items()},
            "top10_equal_avg_turnover": _f(p10e.get("avg_turnover")),
            "top10_equal_gross_max_drawdown": _f(p10e.get("gross", {}).get("max_drawdown")),
            "top10_equal_net_50bps_ann_mean_return": _f(p10e.get("net_50bps", {}).get("ann_mean_return")),
            "top10_equal_net_25bps_total_return": _f(p10e.get("net_25bps", {}).get("total_return")),
            "top10_equal_net_50bps_total_return": _f(p10e.get("net_50bps", {}).get("total_return")),
            "top10_vol_adj_net_50bps_total_return": _f(p10v.get("net_50bps", {}).get("total_return")),
            "has_explicit_gating": block.get("has_explicit_gating", False),
            "notes": block.get("notes", ""),
        }

    candidates_view = {}
    for k in candidates:
        candidates_view[k] = _metric_view(candidates[k])

    report = dict(base_report)
    report.update({
        "data_source": resolved_price_csv,
        "universe_size": universe_size,
        "date_range": {"start": date_range[0], "end": date_range[1]},
        "rebalance_cadences": (["monthly", "weekly"] if weekly else ["monthly"]),
        "sector_control_available": sector_available,
        "sector_control_coverage": _f(sector_coverage),
        "sector_control_source": (_rel(industry_csv) if industry_csv else _rel(_INDUSTRY_CSV))
                                  if sector_available else None,
        "sector_control_note": ("uses ONLY Ticker + IndustryId from the local industry map; "
                                "no SimFin fundamentals are read or used as alpha"),
        "candidates_evaluated": [k for k in candidates if candidates[k].get("evaluable")],
        "candidates_unavailable": [k for k in CANDIDATE_ORDER
                                   if k != C_BEST and (k not in candidates
                                                       or not candidates[k].get("evaluable"))],
        "candidate_metrics": candidates_view,
        "best_candidate": best_name,
        "best_candidate_metrics": _metric_view(best),
        "phase5c_reference_metrics": _metric_view(candidates[C_BASELINE]),
        "alpha_v3_reference_metrics": _metric_view(candidates[C_ENSEMBLE]),
        "quality_improvement_vs_phase5c": quality_improvement,
        "horizon_comparison": horizon_rows,
        "risk_filters": risk_filters,
        "leakage_checks": {
            "labels_forward_only": True, "features_use_past_only": True,
            "embargo_respected": True, "placebo_mean_ic": _f(placebo_ic),
            "placebo_interpretation": ("ridge mean rank IC after a within-date label shuffle; "
                                       "should be ~0 — a real edge must not survive the shuffle"),
        },
        "portfolio_simulation": {
            "baskets": list(f0.TOP_N_PORTFOLIO), "weightings": ["equal", "vol_adj"],
            "cost_bps": list(f0.COST_BPS),
            "cost_model": "round-trip turnover * 2 * bps on the changed weight fraction",
            "turnover_controls": ["weekly_vs_monthly_rebalance", "entry_exit_band_hold"],
        },
        "confidence_controls": {
            "dispersion_max": CONF_DISPERSION_MAX, "hi_rank": CONF_HI_RANK,
            "lo_rank": CONF_LO_RANK,
            "definition": ("model agreement = low stdev of component within-date ranks; "
                           "no-trade zone removes the middle of the cross-section"),
        },
        "validation_gate_matrix": gates,
        "readiness_gate_summary": _gate_summary(gates),
        "any_candidate_ready_for_shadow": recommendation == REC_READY,
        "recommendation": recommendation,
        "recommendation_reason": why,
        "survivorship_caveat": ("the local universe is full-history survivors, so absolute "
                                "portfolio returns are upward-biased; rank IC / turnover are "
                                "less affected and drive the readiness decision"),
        "honesty_note": ("Carried-in result: Phase 5-F0 ensemble IC ~0.0425 did NOT materially "
                         "beat the Phase 5-C reference ~0.0452. This phase reports honestly "
                         "whether quality controls change that, never forces a PASS, and never "
                         "recommends live trading — the ceiling is a shadow candidate."),
    })
    assert report["recommendation"] in ALLOWED_RECOMMENDATIONS

    if write:
        _write_json(_REPORT_OUT, report)
        _write_artifacts(candidates, best_name, gates, horizon_rows, industry_rows)
        _write_json(_SHADOW_PLAN_OUT, _shadow_plan(recommendation, best_name))
    if verbose:
        _print_summary(report, candidates, gates)
    return report


def _print_summary(report: dict, candidates: Dict[str, dict], gates: List[dict]) -> None:
    print("=" * 74)
    print(f"  Phase {report['phase']} — Alpha Signal Quality Upgrade")
    print("=" * 74)
    print(f"  data_source   : {report.get('data_source')}")
    print(f"  universe_size : {report.get('universe_size')}  (SPY = regime/benchmark proxy)")
    print(f"  cadences      : {report.get('rebalance_cadences')}")
    print(f"  sector control: available={report.get('sector_control_available')} "
          f"(coverage={report.get('sector_control_coverage')})")
    cv = report.get("candidate_metrics", {})
    if cv:
        print("-" * 74)
        print("  candidate matrix (IC | t-stat | decile | net50 ann-mean | turnover):")
        for k in CANDIDATE_ORDER:
            if k == C_BEST:
                continue
            s = cv.get(k)
            if not s or not s.get("evaluable"):
                print(f"    {k:34s} unavailable / not evaluable")
                continue
            print(f"    {k:34s} IC={s['mean_rank_ic']:+.4f}  t={s.get('ic_t_stat')}  "
                  f"dec={s['mean_decile_spread']:+.4f}  "
                  f"net50am={s.get('top10_equal_net_50bps_ann_mean_return')}  "
                  f"turn={s.get('top10_equal_avg_turnover')}")
    qi = report.get("quality_improvement_vs_phase5c", {})
    print("-" * 74)
    print(f"  best_candidate: {report.get('best_candidate')}")
    print(f"  vs 5-C ref    : dIC={qi.get('delta_ic_best_minus_baseline')}  "
          f"dNet50am={qi.get('delta_net50_ann_mean_best_minus_baseline')}  "
          f"(best beats 5-C on IC: {qi.get('best_beats_baseline_on_ic')})")
    print(f"  placebo IC    : {report.get('leakage_checks', {}).get('placebo_mean_ic')} "
          f"(should be ~0)")
    print(f"  gate summary  : {report.get('readiness_gate_summary')}")
    print(f"  recommendation: {report.get('recommendation')}")
    print(f"  reason        : {report.get('recommendation_reason')}")
    print("-" * 74)
    if report.get("recommendation") != REC_DATA_BLOCKER:
        for p in (_REPORT_OUT, _CANDIDATE_MATRIX_OUT, _HORIZON_OUT, _REGIME_OUT,
                  _TURNOVER_OUT, _PORTFOLIO_OUT, _INDUSTRY_OUT, _GATE_MATRIX_OUT,
                  _SHADOW_PLAN_OUT):
            print(f"  wrote: {_rel(p)}")


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Phase 5-F0B signal-quality-upgrade harness")
    ap.add_argument("--price-csv", default=None, help="override local price-history CSV path")
    ap.add_argument("--industry-csv", default=None, help="override local industry-map CSV path")
    ap.add_argument("--max-tickers", type=int, default=None, help="cap universe (debug)")
    ap.add_argument("--no-weekly", action="store_true", help="skip the weekly-cadence candidate")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ns = _parse_args(argv)
    try:
        report = run(price_csv=ns.price_csv,
                     industry_csv=Path(ns.industry_csv) if ns.industry_csv else None,
                     max_tickers=ns.max_tickers, weekly=not ns.no_weekly,
                     write=True, verbose=True)
    except Exception as exc:  # noqa: BLE001 — emit an honest ERROR report, never crash silently
        report = {"phase": PHASE, "recommendation": REC_ERROR,
                  "recommendation_reason": f"{type(exc).__name__}: {exc}"}
        _write_json(_REPORT_OUT, report)
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0 if report.get("recommendation") != REC_ERROR else 1


if __name__ == "__main__":
    raise SystemExit(main())
