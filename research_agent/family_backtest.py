"""Deterministic historical backtest harness for fundamental_momentum_50_50_v1.

This is the single Phase 29A adapter that connects the research agent to the
existing deterministic research machinery WITHOUT touching Paper Trader:

- The per-cross-section semantics (rank-percentile blend over the common
  eligible universe, greedy Top-N with the 25% known-sector cap, 4-month
  fundamental carry-forward, ``net = gross - round_trip_cost * turnover``)
  are ported VERBATIM from the published historical reconstruction that
  produced the owned evidence file ``historical_book_returns.csv``
  (paper_trader ``api/multi_horizon_history.py`` — read-only reference).
  Faithfulness is PROVEN, not assumed: baseline validation must reproduce
  that owned reference series exactly before any variant may run.
- Statistical primitives (Spearman rank IC, plain and Newey-West t-stats)
  are REUSED from ``research.run_phase22_autonomous_high_conviction_alpha_
  discovery`` — never re-derived here.

Approved variation dimensions only: blend weights, Top-25/50, sector
treatment (raw / 25% cap / within-sector demeaning), exit-buffer hysteresis
(0.0 or the live engine's 0.20), per-side cost bps, and the reconstruction vs
live-eligibility universe. Nothing here writes to any operational store.
"""

from __future__ import annotations

import calendar
import csv
import datetime as _dt
import hashlib
import math
import os
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from research import run_phase22_autonomous_high_conviction_alpha_discovery as P

from .artifact_store import content_hash

# --------------------------------------------------------------------------- #
# Owned input locations (env-free; tests pass in-memory frames instead)
# --------------------------------------------------------------------------- #
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = r"D:\Stock_Prediction_app_data"

DEFAULT_MOMENTUM_PANEL = os.path.join(
    DATA_ROOT, "phase25_multi_horizon_alpha", "_inputs", "momentum_monthly_panel.csv"
)
DEFAULT_FUND_PANEL = os.path.join(
    REPO_ROOT,
    "research",
    "output",
    "phase10l_historical_sector_neutral_scored_panel_reconstruction",
    "historical_sector_neutral_scored_panel.csv",
)
DEFAULT_SECTOR_MAP = os.path.join(
    REPO_ROOT,
    "research",
    "output",
    "phase10f_owned_sector_mapping_repair",
    "repaired_sector_mapping.csv",
)
DEFAULT_SPY_MONTHLY = os.path.join(
    DATA_ROOT, "research_panels", "phase8c_russell3000", "spy_monthly_total_return.csv"
)
DEFAULT_REFERENCE_BOOK_RETURNS = os.path.join(
    DATA_ROOT, "phase25_multi_horizon_alpha", "historical_book_returns.csv"
)
DEFAULT_PHASE8C_CLOSE = os.path.join(
    DATA_ROOT, "research_panels", "phase8c_russell3000", "monthly_close_total_return.csv"
)

# Ported construction constants (multi_horizon_engine / multi_horizon_history).
SECTOR_CAP_FRACTION = 0.25
MAX_INDIVIDUAL_WEIGHT = 0.10
MAX_FUND_STALE_MONTHS = 4
MIN_ADV_DOLLAR_DEFAULT = 1.0e7
BASELINE_BOOK_ID = "fundamental_momentum_50_50_top25"

COST_LADDER_BPS_PER_SIDE = (12.5, 25.0, 50.0)


# --------------------------------------------------------------------------- #
# small ported helpers
# --------------------------------------------------------------------------- #
def _to_float(x) -> Optional[float]:
    try:
        v = float(x)
        return None if (math.isnan(v) or math.isinf(v)) else v
    except (TypeError, ValueError):
        return None


def _rank_desc_pct(values: Dict[str, float]) -> Dict[str, float]:
    """Percentile (best=1) with deterministic ticker tie-break (ported)."""
    items = sorted(values.items(), key=lambda kv: (-kv[1], kv[0]))
    n = len(items)
    return {tk: (1.0 if n == 1 else (n - (i + 1)) / (n - 1)) for i, (tk, _v) in enumerate(items)}


def _month_diff(a: str, b: str) -> int:
    ya, ma = int(a[:4]), int(a[5:7])
    yb, mb = int(b[:4]), int(b[5:7])
    return (ya - yb) * 12 + (ma - mb)


def _next_month(m: str) -> str:
    y, mm = int(m[:4]), int(m[5:7])
    if mm == 12:
        return "%04d-01" % (y + 1)
    return "%04d-%02d" % (y, mm + 1)


def _month_end(m: str) -> str:
    y, mm = int(m[:4]), int(m[5:7])
    return "%04d-%02d-%02d" % (y, mm, calendar.monthrange(y, mm)[1])


def _file_sha256(path: str) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _max_drawdown(equity: List[float]) -> float:
    peak, worst = float("-inf"), 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            worst = min(worst, (v - peak) / peak)
    return abs(worst)


# --------------------------------------------------------------------------- #
# loaders (ported semantics; in-memory seams for tests)
# --------------------------------------------------------------------------- #
def load_sector_map(path: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                tk = (r.get("ticker") or "").strip().upper()
                sec = (r.get("repaired_sector") or r.get("sector") or "").strip()
                if tk and sec and sec != "Unknown":
                    out[tk] = sec
    except OSError:
        return {}
    return out


def load_fund_monthly(
    path: str, sector_map: Optional[Dict[str, str]] = None
) -> Dict[str, Dict[str, dict]]:
    """{month: {ticker: {composite_sn, sector}}}; latest rebalance row per month."""
    best: Dict[str, Dict[str, Tuple[str, dict]]] = {}
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                full = (r.get("rebalance_date") or "").strip()
                if len(full) < 7:
                    continue
                comp = _to_float(r.get("composite_sn"))
                if comp is None:
                    continue
                tk = (r.get("ticker") or "").strip().upper()
                if not tk:
                    continue
                sec = (r.get("sector") or "").strip() or "Unknown"
                if sec == "Unknown" and sector_map:
                    sec = sector_map.get(tk, "Unknown")
                payload = {"ticker": tk, "composite_sn": comp, "sector": sec}
                mth = best.setdefault(full[:7], {})
                cur = mth.get(tk)
                if cur is None or full > cur[0]:
                    mth[tk] = (full, payload)
    except OSError:
        return {}
    return {m: {tk: p for tk, (_f, p) in d.items()} for m, d in best.items()}


def load_mom_monthly(path: str) -> Dict[str, Dict[str, dict]]:
    """{month: {ticker: {mom_6_1, fwd_1m, sector, eligible, is_member, adv_dollar}}}."""
    out: Dict[str, Dict[str, dict]] = {}
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                m = (r.get("month") or "").strip()
                tk = (r.get("ticker") or "").strip().upper()
                if not m or not tk:
                    continue
                mom = _to_float(r.get("mom_6_1"))
                if mom is None:
                    continue
                out.setdefault(m, {})[tk] = {
                    "ticker": tk,
                    "mom_6_1": mom,
                    "fwd_1m": _to_float(r.get("fwd_1m_return")),
                    "sector": (r.get("sector") or "").strip() or "Unknown",
                    "eligible": (r.get("eligible_history") == "1"),
                    "is_member": (r.get("is_member") == "1"),
                    "adv_dollar": _to_float(r.get("adv_dollar")),
                }
    except OSError:
        return {}
    return out


def load_spy_monthly(path: str) -> Dict[str, float]:
    """{YYYY-MM: month-end total-return close}."""
    out: Dict[str, float] = {}
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                d = (r.get("Date") or "").strip()
                c = _to_float(r.get("Close"))
                if len(d) >= 7 and c is not None:
                    out[d[:7]] = c
    except OSError:
        return {}
    return out


def build_fund_carryforward(
    fund_monthly: Dict[str, Dict[str, dict]],
    months: List[str],
    max_stale_months: int = MAX_FUND_STALE_MONTHS,
) -> Dict[str, Dict[str, dict]]:
    """Ported: each month uses each ticker's latest composite_sn on/before it."""
    by_ticker: Dict[str, List[Tuple[str, float, str]]] = {}
    for m, d in fund_monthly.items():
        for tk, p in d.items():
            if p.get("composite_sn") is not None:
                by_ticker.setdefault(tk, []).append(
                    (m, p["composite_sn"], p.get("sector", "Unknown"))
                )
    for tk in by_ticker:
        by_ticker[tk].sort()
    out: Dict[str, Dict[str, dict]] = {}
    for m in sorted(months):
        cur = {}
        for tk, tl in by_ticker.items():
            best = None
            for (mm, cs, sec) in tl:
                if mm <= m:
                    best = (mm, cs, sec)
                else:
                    break
            if best is not None and _month_diff(m, best[0]) <= max_stale_months:
                cur[tk] = {"composite_sn": best[1], "sector": best[2], "fund_month": best[0]}
        out[m] = cur
    return out


def load_family_inputs(
    *,
    data_cutoff: str,
    momentum_panel_path: Optional[str] = None,
    fundamental_panel_path: Optional[str] = None,
    sector_map_path: Optional[str] = None,
    spy_monthly_path: Optional[str] = None,
    mom_monthly: Optional[Dict[str, Dict[str, dict]]] = None,
    fund_monthly: Optional[Dict[str, Dict[str, dict]]] = None,
    sector_map: Optional[Dict[str, str]] = None,
    spy_close: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Load (or accept injected) owned inputs, truncated honestly at the cutoff.

    A formation month m is usable only when its ENTIRE forward window (month
    m+1) has completed on or before the cutoff — the harness can never read a
    price beyond ``data_cutoff``.
    """
    mom_path = momentum_panel_path or DEFAULT_MOMENTUM_PANEL
    fund_path = fundamental_panel_path or DEFAULT_FUND_PANEL
    smap_path = sector_map_path or DEFAULT_SECTOR_MAP
    spy_path = spy_monthly_path or DEFAULT_SPY_MONTHLY

    smap = sector_map if sector_map is not None else load_sector_map(smap_path)
    mom = mom_monthly if mom_monthly is not None else load_mom_monthly(mom_path)
    fund = (
        fund_monthly
        if fund_monthly is not None
        else load_fund_monthly(fund_path, sector_map=smap)
    )
    spy = spy_close if spy_close is not None else load_spy_monthly(spy_path)

    cutoff_month = data_cutoff[:7]
    # forward window (month m+1) must be fully realized by the cutoff date
    formation_months = sorted(
        m for m in mom if _month_end(_next_month(m)) <= data_cutoff
    )
    # hygiene: never carry a fundamental cross-section from beyond the cutoff
    fund = {m: d for m, d in fund.items() if m <= cutoff_month}
    fund_cf = build_fund_carryforward(fund, formation_months)
    spy_fwd = {
        m: (spy[_next_month(m)] / spy[m] - 1.0)
        if (m in spy and _next_month(m) in spy and spy.get(m))
        else None
        for m in formation_months
    }

    provenance = {
        "schema_version": "29A.1",
        "data_cutoff": data_cutoff,
        "paths": {
            "momentum_panel": None if mom_monthly is not None else mom_path,
            "fundamental_panel": None if fund_monthly is not None else fund_path,
            "sector_map": None if sector_map is not None else smap_path,
            "spy_monthly": None if spy_close is not None else spy_path,
        },
        "sha256": {
            "momentum_panel": None if mom_monthly is not None else _file_sha256(mom_path),
            "fundamental_panel": None if fund_monthly is not None else _file_sha256(fund_path),
            "sector_map": None if sector_map is not None else _file_sha256(smap_path),
            "spy_monthly": None if spy_close is not None else _file_sha256(spy_path),
        },
        "momentum_months": [min(mom), max(mom)] if mom else None,
        "fundamental_months": [min(fund), max(fund)] if fund else None,
        "n_formation_months": len(formation_months),
        "max_formation_month": formation_months[-1] if formation_months else None,
        "max_realized_month_end": _month_end(_next_month(formation_months[-1]))
        if formation_months
        else None,
        "injected_frames": mom_monthly is not None or fund_monthly is not None,
    }
    return {
        "mom_monthly": mom,
        "fund_monthly": fund,
        "fund_cf": fund_cf,
        "sector_map": smap,
        "spy_close": spy,
        "spy_fwd": spy_fwd,
        "months": formation_months,
        "data_cutoff": data_cutoff,
        "provenance": provenance,
    }


# --------------------------------------------------------------------------- #
# selection (ported greedy cap + live-engine exit-buffer hysteresis)
# --------------------------------------------------------------------------- #
def _select_topn_capped(rows: List[dict], size: int) -> List[dict]:
    max_per_sector = max(1, int(SECTOR_CAP_FRACTION * size))
    sc: Dict[str, int] = {}
    picked: List[dict] = []
    for r in rows:  # pre-sorted score desc, ticker asc
        if len(picked) >= size:
            break
        sec = r.get("sector", "Unknown")
        if sec != "Unknown" and sc.get(sec, 0) >= max_per_sector:
            continue
        picked.append(r)
        sc[sec] = sc.get(sec, 0) + 1
    return picked


def _select_book(
    ranked: List[dict],
    size: int,
    *,
    sector_treatment: str,
    exit_buffer_fraction: float,
    prev_set: Optional[set],
) -> List[dict]:
    """Deterministic Top-N with optional 25% cap and optional exit-buffer hold."""
    use_cap = sector_treatment == "sector_cap"
    if exit_buffer_fraction <= 0.0 or not prev_set:
        return _select_topn_capped(ranked, size) if use_cap else ranked[:size]

    exit_bound = math.ceil(size * (1.0 + exit_buffer_fraction))
    retained = [r for r in ranked[:exit_bound] if r["ticker"] in prev_set][:size]
    retained_set = {r["ticker"] for r in retained}
    max_per_sector = max(1, int(SECTOR_CAP_FRACTION * size))
    sc: Dict[str, int] = {}
    for r in retained:  # existing holds keep their seat regardless of the cap
        sc[r.get("sector", "Unknown")] = sc.get(r.get("sector", "Unknown"), 0) + 1
    picked = list(retained)
    for r in ranked:
        if len(picked) >= size:
            break
        if r["ticker"] in retained_set:
            continue
        sec = r.get("sector", "Unknown")
        if use_cap and sec != "Unknown" and sc.get(sec, 0) >= max_per_sector:
            continue
        picked.append(r)
        sc[sec] = sc.get(sec, 0) + 1
    picked.sort(key=lambda r: (-r["score"], r["ticker"]))
    return picked


def _demean_by_sector(scored: List[dict]) -> None:
    """Within-sector demeaning of the blended score (run_phase22
    sector_neutralize semantics applied to one cross-section)."""
    groups: Dict[str, List[dict]] = {}
    for r in scored:
        groups.setdefault(r.get("sector", "Unknown"), []).append(r)
    for rows in groups.values():
        mu = sum(r["score"] for r in rows) / len(rows)
        for r in rows:
            r["score"] = r["score"] - mu


# --------------------------------------------------------------------------- #
# one experiment simulation
# --------------------------------------------------------------------------- #
def run_family_experiment(inputs: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic monthly simulation of one bounded family variant."""
    wf = float(params["fundamental_weight"])
    wm = float(params["momentum_weight"])
    size = int(params["top_n"])
    sector_treatment = params.get("sector_treatment", "sector_cap")
    buffer_f = float(params.get("exit_buffer_fraction", 0.0))
    universe = params.get("universe", "mhz_reconstruction")
    min_adv = float(params.get("min_adv_dollar", MIN_ADV_DOLLAR_DEFAULT))

    fund_cf = inputs["fund_cf"]
    mom_monthly = inputs["mom_monthly"]

    periods: List[dict] = []
    skipped: List[dict] = []
    prev_set: Optional[set] = None
    for m in inputs["months"]:
        fb = fund_cf.get(m, {})
        mb = mom_monthly.get(m, {})
        common = [
            tk
            for tk in fb
            if tk in mb
            and mb[tk].get("eligible")
            and mb[tk].get("mom_6_1") is not None
            and mb[tk].get("fwd_1m") is not None
        ]
        if universe == "mhz_live_eligibility":
            common = [
                tk
                for tk in common
                if mb[tk].get("is_member")
                and (mb[tk].get("adv_dollar") is None or mb[tk]["adv_dollar"] >= min_adv)
            ]
        if len(common) < size:
            if fb:  # only fund-era months count as coverage gaps
                skipped.append({"month": m, "n_common": len(common)})
            prev_set = None
            continue
        fvals = {tk: fb[tk]["composite_sn"] for tk in common}
        mvals = {tk: mb[tk]["mom_6_1"] for tk in common}
        fp = _rank_desc_pct(fvals)
        mp = _rank_desc_pct(mvals)
        scored = [
            {
                "ticker": tk,
                "score": wf * fp[tk] + wm * mp[tk],
                "fwd": mb[tk]["fwd_1m"],
                "sector": fb[tk]["sector"] if fb[tk]["sector"] != "Unknown" else mb[tk]["sector"],
            }
            for tk in common
        ]
        if sector_treatment == "sector_neutral":
            _demean_by_sector(scored)
        scored.sort(key=lambda r: (-r["score"], r["ticker"]))

        picked = _select_book(
            scored,
            size,
            sector_treatment=sector_treatment,
            exit_buffer_fraction=buffer_f,
            prev_set=prev_set,
        )
        if len(picked) < size:
            skipped.append({"month": m, "n_common": len(common), "underfilled": len(picked)})
            prev_set = None
            continue

        gross = sum(r["fwd"] for r in picked) / len(picked)
        cur_set = {r["ticker"] for r in picked}
        established = prev_set is None
        turnover = 1.0 if established else len(cur_set - prev_set) / len(cur_set)

        w = 1.0 / len(picked)
        sec_w: Dict[str, float] = {}
        for r in picked:
            sec_w[r["sector"]] = sec_w.get(r["sector"], 0.0) + w

        scores_arr = np.array([r["score"] for r in scored], dtype=float)
        fwd_arr = np.array([r["fwd"] for r in scored], dtype=float)
        ic = P._spearman(scores_arr, fwd_arr) if len(scored) >= 10 else None

        bottom = scored[-size:]
        spread = gross - (sum(r["fwd"] for r in bottom) / len(bottom))

        periods.append(
            {
                "month": m,
                "gross": gross,
                "turnover": turnover,
                "established": established,
                "n": len(picked),
                "n_common": len(common),
                "constituents": sorted(cur_set),
                "sector_weights": {k: round(v, 6) for k, v in sec_w.items()},
                "max_sector_weight": round(max(sec_w.values()), 6) if sec_w else None,
                "rank_ic": ic,
                "top_minus_bottom_spread": spread,
            }
        )
        prev_set = cur_set

    return {
        "params": dict(params),
        "periods": periods,
        "skipped_months": skipped,
        "n_periods": len(periods),
        "first_month": periods[0]["month"] if periods else None,
        "last_month": periods[-1]["month"] if periods else None,
    }


# --------------------------------------------------------------------------- #
# metric battery (canonical names consumed by evaluator/tools)
# --------------------------------------------------------------------------- #
def _ann(mean_monthly: Optional[float]) -> Optional[float]:
    return None if mean_monthly is None else mean_monthly * 12.0


def _mean(xs: List[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def _std(xs: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    mu = _mean(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (n - 1))


def _regime_masks_pit(months: List[str], spy_close: Dict[str, float]) -> Dict[str, List[bool]]:
    """PIT SPY regimes: bull = trailing-12m return > 0; high_vol = trailing-12m
    monthly-return std at/above the EXPANDING median of that same statistic."""
    spy_months = sorted(spy_close)
    rets: Dict[str, float] = {}
    for prev, cur in zip(spy_months, spy_months[1:]):
        if _month_diff(cur, prev) == 1 and spy_close[prev]:
            rets[cur] = spy_close[cur] / spy_close[prev] - 1.0
    bull, bear, high, low = [], [], [], []
    hist_stds: List[float] = []
    for m in months:
        t12 = None
        prior = [k for k in rets if k <= m]
        prior.sort()
        window = [rets[k] for k in prior[-12:]]
        if m in spy_close:
            back = "%04d-%02d" % (int(m[:4]) - 1, int(m[5:7]))
            if back in spy_close and spy_close[back]:
                t12 = spy_close[m] / spy_close[back] - 1.0
        is_bull = t12 is not None and t12 > 0
        bull.append(bool(is_bull))
        bear.append(bool(t12 is not None and t12 <= 0))
        sd = _std(window) if len(window) >= 12 else None
        if sd is None:
            high.append(False)
            low.append(False)
        else:
            hist_stds.append(sd)
            med = sorted(hist_stds)[len(hist_stds) // 2]
            high.append(sd >= med)
            low.append(sd < med)
    return {"bull": bull, "bear": bear, "high_vol": high, "low_vol": low}


def compute_experiment_metrics(
    sim: Dict[str, Any],
    inputs: Dict[str, Any],
    *,
    primary_cost_bps_per_side: float,
    cost_ladder_bps_per_side: Tuple[float, ...] = COST_LADDER_BPS_PER_SIDE,
) -> Dict[str, Any]:
    periods = sim["periods"]
    months = [p["month"] for p in periods]
    n = len(periods)
    warnings: List[str] = []
    if n == 0:
        return {"months": 0, "warnings": ["no valid periods"], "coverage_fraction": 0.0}

    gross = [p["gross"] for p in periods]
    turn = [p["turnover"] for p in periods]
    spy_fwd = inputs.get("spy_fwd", {})
    spy = [spy_fwd.get(m) for m in months]
    if any(s is None for s in spy):
        warnings.append("SPY forward return missing for %d month(s)" % sum(1 for s in spy if s is None))

    def _net(c_bps: float) -> List[float]:
        rt = 2.0 * (c_bps / 1e4)  # per-side bps -> round-trip fraction
        return [g - rt * t for g, t in zip(gross, turn)]

    def _excess(nets: List[float]) -> List[Optional[float]]:
        return [None if s is None else nv - s for nv, s in zip(nets, spy)]

    by_cost: Dict[str, Optional[float]] = {}
    for c in cost_ladder_bps_per_side:
        exc = [e for e in _excess(_net(c)) if e is not None]
        by_cost[str(c)] = _ann(_mean(exc))

    net = _net(primary_cost_bps_per_side)
    excess_full = _excess(net)
    excess = [e for e in excess_full if e is not None]

    ic_series = [p["rank_ic"] for p in periods if p.get("rank_ic") is not None]
    ic_arr = np.array(ic_series, dtype=float)
    ic_mean = float(np.mean(ic_arr)) if len(ic_arr) else None
    ic_std = float(np.std(ic_arr, ddof=1)) if len(ic_arr) > 1 else None
    ic_t = float(P._t_stat(ic_arr)) if len(ic_arr) > 2 else None
    ic_nw_t = float(P._newey_west_t(ic_arr, 0)) if len(ic_arr) > 2 else None

    equity = []
    nav = 1.0
    for r in net:
        nav *= 1.0 + r
        equity.append(nav)

    steady_turn = [p["turnover"] for p in periods if not p["established"]]
    hit = [1.0 for nv, s in zip(net, spy) if s is not None and nv > s]

    # contiguous thirds of the realized sample
    third = max(1, n // 3)
    seg_bounds = [(0, third), (third, 2 * third), (2 * third, n)]
    seg_means: List[Optional[float]] = []
    for a, b in seg_bounds:
        seg = [e for e in excess_full[a:b] if e is not None]
        seg_means.append(_mean(seg))
    evaluable = [s for s in seg_means if s is not None]
    n_pos = sum(1 for s in evaluable if s > 0)
    best_idx = max(range(len(seg_means)), key=lambda i: (seg_means[i] if seg_means[i] is not None else float("-inf")))
    ex_best = [
        e
        for i, (a, b) in enumerate(seg_bounds)
        if i != best_idx
        for e in excess_full[a:b]
        if e is not None
    ]

    pre = [e for m, e in zip(months, excess_full) if e is not None and m < "2020-01"]
    post = [e for m, e in zip(months, excess_full) if e is not None and m >= "2020-01"]

    masks = _regime_masks_pit(months, inputs.get("spy_close", {}))
    regime_means: Dict[str, Optional[float]] = {}
    reg_pos, reg_n = 0, 0
    for name, mask in masks.items():
        vals = [e for e, keep in zip(excess_full, mask) if keep and e is not None]
        if len(vals) >= 6:
            mu = _mean(vals)
            regime_means[name] = _ann(mu)
            reg_n += 1
            if mu is not None and mu > 0:
                reg_pos += 1
        else:
            regime_means[name] = None

    fund_era = [m for m in inputs["months"] if inputs["fund_cf"].get(m)]
    coverage = n / len(fund_era) if fund_era else 0.0

    max_sec = [p["max_sector_weight"] for p in periods if p.get("max_sector_weight") is not None]
    spreads = [p["top_minus_bottom_spread"] for p in periods]

    return {
        "months": n,
        "first_month": months[0],
        "last_month": months[-1],
        "primary_cost_bps_per_side": primary_cost_bps_per_side,
        "gross_return_ann": _ann(_mean(gross)),
        "net_return_ann": _ann(_mean(net)),
        "net_spy_excess_ann": _ann(_mean(excess)),
        "net_excess_ann_by_cost_bps": by_cost,
        "cost_slope_12p5_to_50": (
            by_cost.get("12.5") - by_cost.get("50.0")
            if by_cost.get("12.5") is not None and by_cost.get("50.0") is not None
            else None
        ),
        "rank_ic_mean": ic_mean,
        "rank_ic_t": ic_t,
        "rank_ic_nw_t": ic_nw_t,
        "rank_ic_ir": (ic_mean / ic_std) if ic_mean is not None and ic_std else None,
        "rank_ic_months": len(ic_series),
        "top_minus_bottom_spread_monthly": _mean(spreads),
        "volatility_ann": (_std(net) * math.sqrt(12.0)) if _std(net) is not None else None,
        "max_drawdown": _max_drawdown(equity),
        "hit_rate": (len(hit) / len(excess)) if excess else None,
        "turnover_monthly_oneside": _mean(steady_turn) if steady_turn else _mean(turn),
        "turnover_including_establishment": _mean(turn),
        "membership_stability": (1.0 - _mean(steady_turn)) if steady_turn else None,
        "n_subperiods": len(evaluable),
        "n_positive_subperiods": n_pos,
        "subperiod_positive_fraction": (n_pos / len(evaluable)) if evaluable else None,
        "subperiod_excess_ann": [_ann(s) for s in seg_means],
        "net_excess_ann_ex_best_subperiod": _ann(_mean(ex_best)) if ex_best else None,
        "pre2020_excess_ann": _ann(_mean(pre)) if pre else None,
        "post2020_excess_ann": _ann(_mean(post)) if post else None,
        "regime_excess_ann": regime_means,
        "regime_positive_fraction": (reg_pos / reg_n) if reg_n else None,
        "max_sector_weight": max(max_sec) if max_sec else None,
        "avg_max_sector_weight": _mean(max_sec),
        "coverage_fraction": coverage,
        "avg_common_universe": _mean([p["n_common"] for p in periods]),
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- #
# baseline validation: determinism + exact reproduction of the owned reference
# --------------------------------------------------------------------------- #
# exit_buffer_fraction 0.0 is deliberate: the reference evidence file was built
# by the RAW monthly reconstruction (paper_trader multi_horizon_history has no
# buffer), and only 0.0 replays it exactly (120/120 months; 0.20 mismatches
# 111/120). The live engine's EXIT_BUFFER_FRACTION 0.20 is an operational
# holdings/action-layer policy and is tested separately as a sensitivity.
BASELINE_PARAMS = {
    "fundamental_weight": 0.5,
    "momentum_weight": 0.5,
    "top_n": 25,
    "sector_treatment": "sector_cap",
    "exit_buffer_fraction": 0.0,
    "universe": "mhz_reconstruction",
    "min_adv_dollar": MIN_ADV_DOLLAR_DEFAULT,
}
# 12.5 bps/side is the reference file's own convention (net = gross - 0.0025 *
# one-way turnover) and equals the paper desk's COST_BPS_PER_SIDE execution
# model. Campaign evaluation uses baseline.cost_bps_per_side (25.0, the Phase
# 10-C standard) — each cost level is applied once, from gross, never stacked.
BASELINE_REFERENCE_COST_BPS_PER_SIDE = 12.5


def load_reference_book_returns(
    path: Optional[str] = None, book_id: str = BASELINE_BOOK_ID
) -> List[dict]:
    rows: List[dict] = []
    try:
        with open(path or DEFAULT_REFERENCE_BOOK_RETURNS, "r", encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                if (r.get("book_id") or "").strip() != book_id:
                    continue
                rows.append(
                    {
                        "month": (r.get("month") or "").strip(),
                        "gross": _to_float(r.get("gross")),
                        "net": _to_float(r.get("net")),
                        "turnover": _to_float(r.get("turnover")),
                        "n": int(r["n"]) if (r.get("n") or "").strip().isdigit() else None,
                    }
                )
    except OSError:
        return []
    return rows


def run_baseline_validation(
    inputs: Dict[str, Any],
    *,
    reference_path: Optional[str] = None,
    reference_rows: Optional[List[dict]] = None,
    tolerance: float = 1.0e-6,
) -> Dict[str, Any]:
    """Prove the harness: bit-identical rerun + exact owned-reference replay."""
    sim1 = run_family_experiment(inputs, BASELINE_PARAMS)
    sim2 = run_family_experiment(inputs, BASELINE_PARAMS)
    h1, h2 = content_hash(sim1), content_hash(sim2)
    deterministic = h1 == h2

    invariant_failures: List[str] = []
    max_per_sector = max(1, int(SECTOR_CAP_FRACTION * BASELINE_PARAMS["top_n"]))
    for p in sim1["periods"]:
        if p["n"] > BASELINE_PARAMS["top_n"]:
            invariant_failures.append("%s: book larger than target" % p["month"])
        if not (0.0 <= p["turnover"] <= 1.0):
            invariant_failures.append("%s: turnover out of range" % p["month"])
        w = 1.0 / p["n"]
        for sec, sw in p["sector_weights"].items():
            if sec != "Unknown" and sw > max_per_sector * w + 1e-9:
                invariant_failures.append("%s: sector cap violated (%s)" % (p["month"], sec))

    ref = (
        reference_rows
        if reference_rows is not None
        else load_reference_book_returns(reference_path)
    )
    ref_by_month = {r["month"]: r for r in ref}
    ours_by_month = {p["month"]: p for p in sim1["periods"]}
    rt = 2.0 * (BASELINE_REFERENCE_COST_BPS_PER_SIDE / 1e4)
    mismatches: List[dict] = []
    compared = 0
    for m in sorted(set(ref_by_month) & set(ours_by_month)):
        r, p = ref_by_month[m], ours_by_month[m]
        our_net = p["gross"] - rt * p["turnover"]
        diffs = {
            "gross": abs(round(p["gross"], 6) - (r["gross"] if r["gross"] is not None else float("nan"))),
            "net": abs(round(our_net, 6) - (r["net"] if r["net"] is not None else float("nan"))),
            "turnover": abs(round(p["turnover"], 4) - (r["turnover"] if r["turnover"] is not None else float("nan"))),
        }
        compared += 1
        if any((not (d <= tolerance)) for d in diffs.values()) or r["n"] != p["n"]:
            mismatches.append({"month": m, **{k: round(v, 8) for k, v in diffs.items()}, "ref_n": r["n"], "our_n": p["n"]})
    only_ref = sorted(set(ref_by_month) - set(ours_by_month))
    only_ours = sorted(set(ours_by_month) - set(ref_by_month))
    reference_available = bool(ref)
    reference_reproduced = (
        reference_available and compared > 0 and not mismatches and not only_ref and not only_ours
    )

    reproduced = deterministic and not invariant_failures and (
        reference_reproduced or not reference_available
    )
    return {
        "baseline_params": dict(BASELINE_PARAMS),
        "deterministic": deterministic,
        "run_hash": h1,
        "invariant_failures": invariant_failures,
        "reference_available": reference_available,
        "reference_months_compared": compared,
        "reference_mismatches": mismatches[:10],
        "reference_mismatch_count": len(mismatches),
        "months_only_in_reference": only_ref[:10],
        "months_only_in_ours": only_ours[:10],
        "reference_reproduced": reference_reproduced,
        "baseline_reproduced": reproduced,
        "n_periods": sim1["n_periods"],
        "first_month": sim1["first_month"],
        "last_month": sim1["last_month"],
        "sim": sim1,
    }


# --------------------------------------------------------------------------- #
# point-in-time integrity (structural + owned-panel cross-checks)
# --------------------------------------------------------------------------- #
def validate_point_in_time_integrity(
    inputs: Dict[str, Any],
    *,
    seed: int,
    sample_size: int = 40,
    tolerance: float = 1.0e-3,
    phase8c_close_path: Optional[str] = None,
    close_frame: Optional[Any] = None,
) -> Dict[str, Any]:
    """Structural no-lookahead checks + sampled forward-return alignment.

    The sampled check proves fwd_1m_return really is the NEXT month's return
    by recomputing it from the independent owned phase8c monthly TR panel.
    """
    checks: List[Dict[str, Any]] = []

    # 1) fundamental carry-forward only ever looks backward (and <= staleness)
    stale_viol = 0
    lookahead_viol = 0
    for m, d in inputs["fund_cf"].items():
        for tk, p in d.items():
            fm = p.get("fund_month")
            if fm is None:
                continue
            if fm > m:
                lookahead_viol += 1
            elif _month_diff(m, fm) > MAX_FUND_STALE_MONTHS:
                stale_viol += 1
    checks.append(
        {
            "check": "fundamental_carryforward_backward_only",
            "passed": lookahead_viol == 0,
            "violations": lookahead_viol,
        }
    )
    checks.append(
        {
            "check": "fundamental_staleness_bound",
            "passed": stale_viol == 0,
            "violations": stale_viol,
        }
    )

    # 2) no formation month whose forward window passes the cutoff
    cutoff = inputs["data_cutoff"]
    bad_cutoff = [
        m for m in inputs["months"] if _month_end(_next_month(m)) > cutoff
    ]
    checks.append(
        {
            "check": "forward_window_within_cutoff",
            "passed": not bad_cutoff,
            "violations": len(bad_cutoff),
        }
    )

    # 3) sampled forward-return alignment vs the independent phase8c panel
    sample_result: Dict[str, Any] = {"evaluated": False}
    candidates = [
        (m, tk, row["fwd_1m"])
        for m, d in inputs["mom_monthly"].items()
        for tk, row in d.items()
        if row.get("fwd_1m") is not None and m in inputs["months"]
    ]
    frame = close_frame
    if frame is None and (phase8c_close_path or os.path.exists(DEFAULT_PHASE8C_CLOSE)):
        path = phase8c_close_path or DEFAULT_PHASE8C_CLOSE
        try:
            import pandas as pd

            header = pd.read_csv(path, nrows=0)
            available = set(header.columns)
            rng = random.Random(int(seed))
            rng.shuffle(candidates)
            chosen = [c for c in candidates if c[1] in available][: int(sample_size)]
            usecols = ["Date"] + sorted({tk for _m, tk, _f in chosen})
            if chosen:
                frame = pd.read_csv(path, usecols=usecols)
        except Exception as exc:  # structured, never a crash
            sample_result = {"evaluated": False, "error": "%s: %s" % (type(exc).__name__, exc)}
            frame = None
            chosen = []
    else:
        rng = random.Random(int(seed))
        rng.shuffle(candidates)
        chosen = candidates[: int(sample_size)] if frame is not None else []

    if frame is not None and chosen:
        frame = frame.copy()
        frame["month"] = frame["Date"].astype(str).str.slice(0, 7)
        frame = frame.set_index("month")
        agree, disagree, unavailable = 0, 0, 0
        worst = 0.0
        for m, tk, fwd in chosen:
            nm = _next_month(m)
            if tk not in frame.columns or m not in frame.index or nm not in frame.index:
                unavailable += 1
                continue
            c0, c1 = frame.at[m, tk], frame.at[nm, tk]
            try:
                c0, c1 = float(c0), float(c1)
            except (TypeError, ValueError):
                unavailable += 1
                continue
            if not (c0 and math.isfinite(c0) and math.isfinite(c1)):
                unavailable += 1
                continue
            ref = c1 / c0 - 1.0
            d = abs(ref - fwd)
            worst = max(worst, d)
            if d <= tolerance:
                agree += 1
            else:
                disagree += 1
        evaluated = agree + disagree
        frac = (agree / evaluated) if evaluated else None
        sample_result = {
            "evaluated": True,
            "sampled": len(chosen),
            "agree": agree,
            "disagree": disagree,
            "unavailable": unavailable,
            "agree_fraction": frac,
            "worst_abs_diff": worst,
            "tolerance": tolerance,
            "seed": int(seed),
        }
        checks.append(
            {
                "check": "forward_return_is_next_month_vs_phase8c",
                "passed": frac is not None and frac >= 0.8,
                "detail": sample_result,
            }
        )
    else:
        checks.append(
            {
                "check": "forward_return_is_next_month_vs_phase8c",
                "passed": None,
                "detail": sample_result,
            }
        )

    hard = [c for c in checks if c["passed"] is False]
    return {
        "pit_integrity_ok": not hard,
        "checks": checks,
        "failed_checks": [c["check"] for c in hard],
        "sample_cross_check": sample_result,
        "data_cutoff": cutoff,
    }


__all__ = [
    "BASELINE_PARAMS",
    "BASELINE_REFERENCE_COST_BPS_PER_SIDE",
    "COST_LADDER_BPS_PER_SIDE",
    "DEFAULT_FUND_PANEL",
    "DEFAULT_MOMENTUM_PANEL",
    "DEFAULT_PHASE8C_CLOSE",
    "DEFAULT_REFERENCE_BOOK_RETURNS",
    "DEFAULT_SECTOR_MAP",
    "DEFAULT_SPY_MONTHLY",
    "MAX_FUND_STALE_MONTHS",
    "SECTOR_CAP_FRACTION",
    "build_fund_carryforward",
    "compute_experiment_metrics",
    "load_family_inputs",
    "load_fund_monthly",
    "load_mom_monthly",
    "load_reference_book_returns",
    "load_sector_map",
    "load_spy_monthly",
    "run_baseline_validation",
    "run_family_experiment",
    "validate_point_in_time_integrity",
]
