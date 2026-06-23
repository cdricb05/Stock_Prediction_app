"""Phase 7-D — Risk Decomposition Foundation (book-level risk measurement layer).

**Track A (quant brain) research only.** Offline, measurement-only, leakage-irrelevant
(this layer predicts nothing). No network, no provider call, no paid API, no API key
read or required, no model trained or deployed, no factor-weight optimization, no
order / broker / automation / hedging logic, no trade recommendation, no Paper Trader /
GCP work, no live data call, no binary artifact, no commit, no push. Reads only local
files: the Phase 2K-G price panel (READ ONLY, on D:) and the committed SEC fundamentals
/ sector artifacts (repo-local). Writes nothing to D:.

Why this phase exists
---------------------
The charter (docs/project_charter_sp500_multifactor_ranking_v1.md) is a two-system
design: System 1 = multi-factor ranking engine (Phase 7-C), System 2 = regime / risk
overlay. Charter Phase 4.5 calls for a *risk decomposition* layer that explains the
exposures of a book BEFORE any sizing, hedging, or regime overlay is built. That layer
is valuable even though Phase 7-C recommended MULTIFACTOR_RANKING_ENGINE_WEAK (no
confirmed alpha): understanding *what risks a book carries* is independent of whether
the ranking signal is profitable.

This is NOT a trading system, NOT a production model, NOT an order system, NOT
automation. It is a measurement layer: given a deterministic *sample* portfolio (never
live holdings), it explains the book's exposures across five lenses —

    1. market beta exposure       (net beta to SPY, systematic vol share)
    2. sector concentration       (sector weights, sector HHI, top sector)
    3. factor tilt exposure       (book-weighted Phase 7-C factor z-scores)
    4. idiosyncratic / concentration risk (name HHI, effective N, diversifiable vol)
    5. liquidity risk             (ADV-based days-to-liquidate)

It explains exposure; it does not predict returns and does not recommend trades.

What it does
------------
  1. Loads local price history (incl. SPY) and the local sector map.
  2. Builds a DETERMINISTIC sample portfolio from the available universe (not live).
  3. Computes per-position: weight, sector, trailing realized volatility, rolling beta
     to SPY, residual (idiosyncratic) volatility, average dollar volume.
  4. Aggregates to book level: net beta, sector exposure, Herfindahl concentration,
     effective number of names, top holdings, systematic-vs-idiosyncratic variance
     split, and ADV-based liquidity / days-to-liquidate.
  5. Reuses the Phase 7-C factor construction to report the book's factor tilt (the
     weighted latest cross-sectional z of each approved factor bucket), if available.
  6. Emits a risk gate matrix and a CFO-readable consolidated risk view.

Safety
------
No order, broker, automation, or hedging code path exists. No live provider call. No
Paper Trader / GCP / deploy. The sample portfolio is generated deterministically from
local data; it is explicitly NOT a set of live or recommended holdings. D: is read-only.
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

# Reuse the Phase 7-C engine's data loaders + factor construction (no work runs at
# import time) so the factor-tilt lens uses the exact same point-in-time factors.
from research import run_phase7c_multifactor_ranking_engine as C

PHASE = "7-D"
OBJECTIVE = (
    "Build the measurement-only, book-level risk decomposition layer (charter Phase "
    "4.5): given a deterministic sample portfolio built from local data, explain its "
    "market beta, sector concentration, factor tilt, idiosyncratic/concentration, and "
    "liquidity exposures. Explains exposure; does not predict returns, does not "
    "recommend trades, sizes nothing, hedges nothing, places no orders."
)

# Recommendation taxonomy (task-specified).
REC_READY = "READY_FOR_PHASE7E_REGIME_OVERLAY"
REC_NEEDS_REVIEW = "NEEDS_RISK_REVIEW"
REC_BLOCKED = "DATA_QUALITY_BLOCKED"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (REC_READY, REC_NEEDS_REVIEW, REC_BLOCKED, REC_ERROR)

# --------------------------------------------------------------------------- #
# Paths (price panel READ ONLY on D:; everything else repo-local under C:).
# --------------------------------------------------------------------------- #
PRICE_CSV = C.PRICE_CSV
SECTOR_MAP_CSV = C.SECTOR_MAP_CSV
BENCHMARK = C.BENCHMARK  # "SPY" — KEPT here (Phase 7-C drops it; we need it for beta)
_OUT_DIR_DEFAULT = _REPO_ROOT / "research" / "output" / "phase7d_risk_decomposition_foundation"

# --------------------------------------------------------------------------- #
# Config (fixed, documented modelling choices — no tuning loop).
# --------------------------------------------------------------------------- #
N_SAMPLE = 15                 # number of names in the deterministic sample portfolio
TRADING_DAYS = 252            # annualization factor
BETA_WINDOW_D = 252           # trailing window for realized vol / beta
WINDOW_MIN_D = 120            # minimum daily observations to compute vol / beta
LIQ_WINDOW_D = 63             # trailing window for average dollar volume (ADV)
ASSUMED_BOOK_USD = 10_000_000.0  # assumed gross notional, for days-to-liquidate only
LIQ_PARTICIPATION = 0.10      # assume liquidating <=10% of a name's ADV per day

# Informational risk-flag thresholds (describe the sample book; never an order trigger).
MAX_NAME_WEIGHT_FLAG = 0.20   # single-name weight above this is flagged as concentrated
TOP_SECTOR_WEIGHT_FLAG = 0.40 # single-sector weight above this is flagged
EFFECTIVE_N_FLAG = 8.0        # effective number of names below this is flagged
NET_BETA_HI_FLAG = 1.30       # |net beta| above this is flagged as high market exposure
DTL_FLAG_DAYS = 5.0           # worst-name days-to-liquidate above this is flagged

NAN = float("nan")
_ANNUALIZE_VOL = math.sqrt(TRADING_DAYS)


# --------------------------------------------------------------------------- #
# Numeric helpers.
# --------------------------------------------------------------------------- #
def _f(x) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _round(x, n=6):
    v = _f(x)
    return round(v, n) if v is not None else None


# --------------------------------------------------------------------------- #
# Data layer.
# --------------------------------------------------------------------------- #
def load_prices_with_benchmark(path=PRICE_CSV, universe: Optional[set] = None) -> pd.DataFrame:
    """Load the adjusted daily price panel (READ ONLY), KEEPING the SPY benchmark so
    beta can be computed. Returns long [ticker, date, adj_close, volume]."""
    df = pd.read_csv(path, usecols=["ticker", "date", "adjusted_close", "volume"])
    df = df.rename(columns={"adjusted_close": "adj_close"})
    df["date"] = pd.to_datetime(df["date"])
    keep = set(universe) | {BENCHMARK} if universe is not None else None
    if keep is not None:
        df = df[df["ticker"].isin(keep)]
    df = df.dropna(subset=["adj_close"]).sort_values(["ticker", "date"]).reset_index(drop=True)
    return df


def daily_returns_wide(prices: pd.DataFrame) -> pd.DataFrame:
    """Wide [date x ticker] daily simple returns from adjusted close."""
    close = prices.pivot(index="date", columns="ticker", values="adj_close").sort_index()
    return close.pct_change(fill_method=None)


def dollar_volume_wide(prices: pd.DataFrame) -> pd.DataFrame:
    """Wide [date x ticker] daily dollar volume = adj_close * volume."""
    dv = prices.copy()
    dv["dollar_volume"] = dv["adj_close"] * dv["volume"]
    return dv.pivot(index="date", columns="ticker", values="dollar_volume").sort_index()


# --------------------------------------------------------------------------- #
# Deterministic sample portfolio (explicitly NOT live or recommended holdings).
# --------------------------------------------------------------------------- #
def build_sample_portfolio(universe: Sequence[str], sector_map: Dict[str, str],
                           n: int = N_SAMPLE) -> pd.DataFrame:
    """Deterministically select ``n`` names spread across the sorted universe and assign
    linearly-decaying (deliberately concentrated) weights that sum to 1. Fully
    reproducible from local data — no randomness, no live holdings, no recommendation."""
    names = sorted(universe)
    if not names:
        return pd.DataFrame(columns=["ticker", "sector", "weight"])
    n = min(n, len(names))
    if n == 1:
        picks = [names[0]]
    else:
        # Evenly spaced indices across the sorted universe -> sector variety, deterministic.
        picks = [names[int(round(i * (len(names) - 1) / (n - 1)))] for i in range(n)]
        # De-duplicate while preserving order (spacing can collide on small universes).
        seen, uniq = set(), []
        for t in picks:
            if t not in seen:
                seen.add(t)
                uniq.append(t)
        picks = uniq
    k = len(picks)
    raw = [float(k - j) for j in range(k)]  # linear decay: first pick heaviest
    tot = sum(raw)
    weights = [w / tot for w in raw]
    rows = [(t, sector_map.get(t, "UNKNOWN"), w) for t, w in zip(picks, weights)]
    return pd.DataFrame(rows, columns=["ticker", "sector", "weight"])


# --------------------------------------------------------------------------- #
# Per-position risk statistics.
# --------------------------------------------------------------------------- #
def _trailing(returns: pd.DataFrame, window: int) -> pd.DataFrame:
    """Last ``window`` rows of a returns frame (chronological tail)."""
    return returns.tail(window)


def position_statistics(rets_win: pd.DataFrame, spy_ret: pd.Series, dollar_vol: pd.DataFrame,
                        portfolio: pd.DataFrame) -> pd.DataFrame:
    """Per-position trailing realized vol, beta to SPY, residual (idiosyncratic) vol,
    and average dollar volume. All trailing/point-in-time over the configured windows."""
    spy_var = float(np.nanvar(spy_ret.values, ddof=0)) if spy_ret.notna().sum() >= WINDOW_MIN_D else NAN
    rows = []
    adv_win = _trailing(dollar_vol, LIQ_WINDOW_D)
    for r in portfolio.itertuples():
        t = r.ticker
        s = rets_win[t] if t in rets_win.columns else pd.Series(dtype=float)
        pair = pd.concat([s, spy_ret], axis=1, keys=["x", "spy"]).dropna()
        n_obs = len(pair)
        if n_obs >= WINDOW_MIN_D:
            var_x = float(np.var(pair["x"].values, ddof=0))
            vol_annual = math.sqrt(var_x) * _ANNUALIZE_VOL
            var_spy_pair = float(np.var(pair["spy"].values, ddof=0))
            if var_spy_pair > 0:
                cov = float(np.cov(pair["x"].values, pair["spy"].values, ddof=0)[0, 1])
                beta = cov / var_spy_pair
                resid_var = max(0.0, var_x - beta * beta * var_spy_pair)
                resid_vol_annual = math.sqrt(resid_var) * _ANNUALIZE_VOL
            else:
                beta = resid_vol_annual = None
        else:
            var_x = vol_annual = beta = resid_vol_annual = None
        adv = float(np.nanmean(adv_win[t].values)) if t in adv_win.columns and adv_win[t].notna().any() else None
        rows.append({
            "ticker": t, "sector": r.sector, "weight": float(r.weight),
            "n_obs": n_obs,
            "realized_vol_annual": vol_annual,
            "beta_to_spy": beta,
            "residual_vol_annual": resid_vol_annual,
            "daily_variance": var_x,
            "avg_dollar_volume": adv,
        })
    out = pd.DataFrame(rows)
    out.attrs["spy_var_daily"] = spy_var
    return out


# --------------------------------------------------------------------------- #
# Book-level aggregation.
# --------------------------------------------------------------------------- #
def market_beta_exposure(pos: pd.DataFrame, rets_win: pd.DataFrame, spy_ret: pd.Series) -> Dict:
    """Net beta and a systematic-vs-idiosyncratic variance split of the book."""
    have = pos[pos["beta_to_spy"].notna()]
    w_have = have["weight"].sum()
    net_beta = float((have["weight"] * have["beta_to_spy"]).sum() / w_have) if w_have > 0 else None

    # Portfolio variance from the covariance matrix of held names over the window.
    cols = [t for t in pos["ticker"] if t in rets_win.columns]
    sub = rets_win[cols].dropna()
    port_var_daily = None
    if len(sub) >= WINDOW_MIN_D and len(cols) >= 2:
        wmap = dict(zip(pos["ticker"], pos["weight"]))
        w = np.array([wmap[c] for c in cols], dtype=float)
        w = w / w.sum() if w.sum() > 0 else w
        cov = np.cov(sub.values, rowvar=False, ddof=0)
        port_var_daily = float(w @ cov @ w)
    elif len(cols) == 1 and pos.iloc[0]["daily_variance"] is not None:
        port_var_daily = float(pos.iloc[0]["daily_variance"])

    spy_var = float(spy_ret.tail(BETA_WINDOW_D).dropna().var(ddof=0)) if spy_ret.notna().sum() >= WINDOW_MIN_D else None
    systematic_var_daily = (net_beta ** 2 * spy_var) if (net_beta is not None and spy_var is not None) else None
    resid_var_daily = (max(0.0, port_var_daily - systematic_var_daily)
                       if (port_var_daily is not None and systematic_var_daily is not None) else None)

    def ann_vol(v):
        return math.sqrt(v) * _ANNUALIZE_VOL if (v is not None and v >= 0) else None

    systematic_share = (systematic_var_daily / port_var_daily
                        if (port_var_daily not in (None, 0) and systematic_var_daily is not None) else None)
    return {
        "net_beta": _round(net_beta),
        "beta_coverage_weight": _round(w_have),
        "portfolio_vol_annual": _round(ann_vol(port_var_daily)),
        "spy_vol_annual": _round(ann_vol(spy_var)),
        "systematic_vol_annual": _round(ann_vol(systematic_var_daily)),
        "idiosyncratic_vol_annual": _round(ann_vol(resid_var_daily)),
        "systematic_variance_share": _round(systematic_share),
        "idiosyncratic_variance_share": _round(1.0 - systematic_share) if systematic_share is not None else None,
    }


def diversifiable_idiosyncratic_vol(pos: pd.DataFrame) -> Optional[float]:
    """Concentration-aware idiosyncratic vol assuming zero residual correlation:
    sqrt(sum w_i^2 * resid_var_i). Higher when a few names dominate."""
    have = pos[pos["residual_vol_annual"].notna()]
    if have.empty:
        return None
    resid_var_annual = (have["residual_vol_annual"] ** 2)
    return float(math.sqrt((have["weight"] ** 2 * resid_var_annual).sum()))


def sector_exposure(pos: pd.DataFrame) -> pd.DataFrame:
    """Sector weights, name counts, and HHI contribution, sorted by weight."""
    g = pos.groupby("sector").agg(weight=("weight", "sum"), n_names=("ticker", "count")).reset_index()
    g["hhi_contrib"] = g["weight"] ** 2
    return g.sort_values("weight", ascending=False).reset_index(drop=True)


def concentration_metrics(pos: pd.DataFrame, sectors: pd.DataFrame) -> Dict:
    """Herfindahl index, effective number of names, top-holding weights."""
    w = pos["weight"].astype(float).sort_values(ascending=False)
    hhi = float((w ** 2).sum())
    eff_n = float(1.0 / hhi) if hhi > 0 else None
    sector_hhi = float((sectors["weight"].astype(float) ** 2).sum())
    return {
        "n_positions": int(len(pos)),
        "herfindahl_index": _round(hhi),
        "effective_n_names": _round(eff_n),
        "max_name_weight": _round(float(w.iloc[0])) if len(w) else None,
        "top1_weight": _round(float(w.iloc[0])) if len(w) else None,
        "top3_weight": _round(float(w.iloc[:3].sum())),
        "top5_weight": _round(float(w.iloc[:5].sum())),
        "sector_herfindahl_index": _round(sector_hhi),
        "effective_n_sectors": _round(1.0 / sector_hhi) if sector_hhi > 0 else None,
        "n_sectors": int(len(sectors)),
        "top_sector": str(sectors.iloc[0]["sector"]) if len(sectors) else None,
        "top_sector_weight": _round(float(sectors.iloc[0]["weight"])) if len(sectors) else None,
    }


def liquidity_report(pos: pd.DataFrame, *, book_usd: float = ASSUMED_BOOK_USD,
                     participation: float = LIQ_PARTICIPATION) -> pd.DataFrame:
    """ADV-based liquidity: position notional (at an assumed gross book) divided by the
    daily liquidatable volume (participation * ADV) -> days-to-liquidate per name."""
    rows = []
    for r in pos.itertuples():
        notional = float(r.weight) * book_usd
        adv = r.avg_dollar_volume
        if adv and adv > 0:
            dtl = notional / (participation * adv)
            bucket = ("deep" if dtl < 0.5 else "liquid" if dtl < 2.0
                      else "moderate" if dtl < DTL_FLAG_DAYS else "thin")
        else:
            dtl, bucket = None, "unknown"
        rows.append({"ticker": r.ticker, "weight": float(r.weight), "notional_usd": notional,
                     "avg_dollar_volume": adv, "participation": participation,
                     "days_to_liquidate": dtl, "liquidity_bucket": bucket})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Factor tilt lens — reuse the Phase 7-C point-in-time factor construction.
# --------------------------------------------------------------------------- #
def _book_tilt_from_factor_z(factor_z: Dict[str, pd.DataFrame], portfolio: pd.DataFrame) -> Dict:
    """Pure core: book-weighted latest-month cross-sectional z of each factor bucket.
    For each factor, take its most recent month's z-scores, weight them by the book's
    weights (normalized over the names the factor actually covers), and average across
    factors for a composite tilt. Coverage is reported per factor (never faked)."""
    book = list(portfolio["ticker"])
    wmap = dict(zip(portfolio["ticker"], portfolio["weight"]))
    out_factors: Dict[str, Dict] = {}
    tilts: List[float] = []
    for key, zf in factor_z.items():
        if zf is None or zf.empty:
            out_factors[key] = {"available": False, "book_tilt_z": None, "coverage": 0.0}
            continue
        latest_month = zf["month"].max()
        sub = zf[zf["month"] == latest_month]
        zmap = {str(rr.ticker): float(rr.z) for rr in sub.itertuples()}
        covered = [t for t in book if t in zmap]
        w_cov = sum(wmap[t] for t in covered)
        if w_cov > 0:
            tilt = sum(wmap[t] * zmap[t] for t in covered) / w_cov  # weight-normalized over covered
            tilts.append(tilt)
        else:
            tilt = None
        out_factors[key] = {"available": True, "book_tilt_z": _round(tilt),
                            "coverage": _round(len(covered) / len(book) if book else 0.0),
                            "as_of_month": str(latest_month)}
    composite = float(np.mean(tilts)) if tilts else None
    return {"available": any(v["available"] for v in out_factors.values()),
            "factors": out_factors, "composite_tilt_z": _round(composite)}


def factor_tilt_exposure(portfolio: pd.DataFrame) -> Dict:
    """Book-weighted latest-month cross-sectional z of each Phase 7-C factor bucket.
    Best-effort: if the 7-C factor inputs are unavailable, the lens is marked
    unavailable (never faked)."""
    try:
        sector_map, _ = C.load_sector_map()
        fund = C.load_annual_fundamentals()
        fund_tickers = set(fund["ticker"].unique())
        factor_universe = set(sector_map) & fund_tickers
        prices = C.load_prices(universe=factor_universe | set(sector_map))
        price_tickers = set(prices["ticker"].unique())
        factor_universe = sorted(factor_universe & price_tickers)
        prices = prices[prices["ticker"].isin(factor_universe)].copy()
        pf = C.build_price_factors(prices)
        ff = C.build_fundamental_factors(fund[fund["ticker"].isin(factor_universe)],
                                         pf["monthly_close"], pf["month_end_date"])
        raw = {"momentum": pf["momentum"], "low_volatility": pf["low_volatility"],
               "value": ff["value"], "quality": ff["quality"], "growth": ff["growth"]}
        factor_z = {k: C.normalize_cross_sectional(v, sector_map, sector_neutralize=True)
                    for k, v in raw.items()}
    except Exception as exc:  # pragma: no cover - defensive (data absent)
        return {"available": False, "reason": f"factor inputs unavailable: {exc}", "factors": {}}

    return _book_tilt_from_factor_z(factor_z, portfolio)


# --------------------------------------------------------------------------- #
# Output paths.
# --------------------------------------------------------------------------- #
@dataclass
class _OutPaths:
    dir: Path

    def __post_init__(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        self.report = self.dir / "phase7d_risk_decomposition_foundation.json"
        self.sample_portfolio = self.dir / "sample_portfolio.csv"
        self.position_risk = self.dir / "position_risk_decomposition.csv"
        self.sector_exposure = self.dir / "sector_exposure.csv"
        self.factor_tilt = self.dir / "factor_tilt_exposure.csv"
        self.liquidity = self.dir / "liquidity_risk_report.csv"
        self.concentration = self.dir / "concentration_report.csv"
        self.gate_matrix = self.dir / "risk_gate_matrix.csv"
        self.next_plan = self.dir / "phase7e_next_plan.json"


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
        report = _run_engine(paths)
    except Exception as exc:  # pragma: no cover - defensive
        import traceback
        report = _error_report(str(exc), traceback.format_exc())
        _write_json(paths.report, report)
    return report


def _run_engine(paths: _OutPaths) -> Dict:
    sector_map, sector_pit = load_prices_sector_map()
    prices_all = load_prices_with_benchmark(universe=set(sector_map))
    all_tickers = set(prices_all["ticker"].unique())
    if BENCHMARK not in all_tickers:
        report = _blocked_report("benchmark SPY absent from the local price panel; "
                                 "beta / systematic decomposition cannot be computed")
        _write_json(paths.report, report)
        return report
    universe = sorted((all_tickers - {BENCHMARK}) & set(sector_map))
    if len(universe) < 5:
        report = _blocked_report(f"investable universe too small ({len(universe)} names) "
                                 "after intersecting price panel with the sector map")
        _write_json(paths.report, report)
        return report

    portfolio = build_sample_portfolio(universe, sector_map, n=N_SAMPLE)

    rets = daily_returns_wide(prices_all)
    rets_win = _trailing(rets, BETA_WINDOW_D)
    spy_ret = rets_win[BENCHMARK] if BENCHMARK in rets_win.columns else pd.Series(dtype=float)
    dollar_vol = dollar_volume_wide(prices_all)

    pos = position_statistics(rets_win, spy_ret, dollar_vol, portfolio)
    beta_view = market_beta_exposure(pos, rets_win, spy_ret)
    sectors = sector_exposure(pos)
    conc = concentration_metrics(pos, sectors)
    liq = liquidity_report(pos)
    tilt = factor_tilt_exposure(portfolio)
    diversifiable_idio = diversifiable_idiosyncratic_vol(pos)

    price_dates = prices_all["date"]
    data_window = f"{rets_win.index.min().date()}..{rets_win.index.max().date()}" if len(rets_win) else "n/a"

    gates = _build_gate_matrix(pos, beta_view, conc, liq, tilt, sector_pit)
    recommendation = _derive_recommendation(gates)

    # --- artifacts ---
    _write_sample_portfolio(paths, portfolio)
    _write_position_risk(paths, pos)
    _write_sector_exposure(paths, sectors)
    _write_factor_tilt(paths, tilt)
    _write_liquidity(paths, liq)
    _write_concentration(paths, conc, beta_view, diversifiable_idio)
    _write_gate_matrix_csv(paths, gates)
    _write_next_plan(paths, recommendation, conc, beta_view)

    report = _assemble_report(paths, universe, portfolio, pos, beta_view, sectors, conc,
                              liq, tilt, diversifiable_idio, gates, recommendation,
                              sector_pit, data_window, price_dates)
    _write_json(paths.report, report)
    return report


def load_prices_sector_map() -> Tuple[Dict[str, str], bool]:
    """Thin reuse of the Phase 7-C sector-map loader (kept as a seam for tests)."""
    return C.load_sector_map(SECTOR_MAP_CSV)


# --------------------------------------------------------------------------- #
# Gates / recommendation.
# --------------------------------------------------------------------------- #
def _build_gate_matrix(pos: pd.DataFrame, beta_view: Dict, conc: Dict, liq: pd.DataFrame,
                       tilt: Dict, sector_pit: bool) -> List[Dict]:
    gates: List[Dict] = []

    def add(name, status, metric, value, threshold, note=""):
        gates.append({"gate_name": name, "status": status, "metric": metric,
                      "value": value, "threshold": threshold, "note": note})

    # --- capability gates (was the risk view actually computed from local data?) ---
    wsum = float(pos["weight"].sum()) if len(pos) else 0.0
    add("weights_normalized_gate", "PASS" if abs(wsum - 1.0) < 1e-6 else "FAIL",
        "portfolio_weight_sum", _round(wsum), "1.0", "sample portfolio weights sum to one")
    add("position_vol_gate",
        "PASS" if pos["realized_vol_annual"].notna().any() else "FAIL",
        "positions_with_realized_vol", int(pos["realized_vol_annual"].notna().sum()), ">=1",
        "trailing realized volatility computed per position")
    add("beta_gate", "PASS" if beta_view.get("net_beta") is not None else "FAIL",
        "net_beta_to_spy", beta_view.get("net_beta"), "computed",
        "net beta to SPY computed from trailing returns")
    add("idiosyncratic_gate",
        "PASS" if beta_view.get("idiosyncratic_vol_annual") is not None else "FAIL",
        "idiosyncratic_vol_annual", beta_view.get("idiosyncratic_vol_annual"), "computed",
        "systematic-vs-idiosyncratic variance split computed")
    add("concentration_gate", "PASS" if conc.get("herfindahl_index") is not None else "FAIL",
        "herfindahl_index", conc.get("herfindahl_index"), "computed",
        "Herfindahl index + effective N computed")
    add("liquidity_gate",
        "PASS" if liq["days_to_liquidate"].notna().any() else "FAIL",
        "positions_with_days_to_liquidate", int(liq["days_to_liquidate"].notna().sum()), ">=1",
        "ADV-based days-to-liquidate computed")
    add("factor_tilt_gate", "PASS" if tilt.get("available") else "WARNING",
        "factor_tilt_available", bool(tilt.get("available")), "true (ideal)",
        "Phase 7-C factor tilt lens populated (informational if unavailable)")

    # --- informational risk-flag gates (describe the sample book; never an order trigger) ---
    mnw = conc.get("max_name_weight")
    add("name_concentration_flag",
        "WARNING" if (mnw is not None and mnw > MAX_NAME_WEIGHT_FLAG) else "PASS",
        "max_name_weight", mnw, f"<= {MAX_NAME_WEIGHT_FLAG}",
        "informational: single-name concentration of the sample book")
    tsw = conc.get("top_sector_weight")
    add("sector_concentration_flag",
        "WARNING" if (tsw is not None and tsw > TOP_SECTOR_WEIGHT_FLAG) else "PASS",
        "top_sector_weight", tsw, f"<= {TOP_SECTOR_WEIGHT_FLAG}",
        "informational: single-sector concentration of the sample book")
    en = conc.get("effective_n_names")
    add("breadth_flag",
        "WARNING" if (en is not None and en < EFFECTIVE_N_FLAG) else "PASS",
        "effective_n_names", en, f">= {EFFECTIVE_N_FLAG}",
        "informational: effective number of names (1/HHI)")
    nb = beta_view.get("net_beta")
    add("market_beta_flag",
        "WARNING" if (nb is not None and abs(nb) > NET_BETA_HI_FLAG) else "PASS",
        "abs_net_beta", _round(abs(nb)) if nb is not None else None, f"<= {NET_BETA_HI_FLAG}",
        "informational: book net market exposure")
    worst_dtl = liq["days_to_liquidate"].max(skipna=True)
    worst_dtl = float(worst_dtl) if pd.notna(worst_dtl) else None
    add("liquidity_flag",
        "WARNING" if (worst_dtl is not None and worst_dtl > DTL_FLAG_DAYS) else "PASS",
        "worst_days_to_liquidate", _round(worst_dtl), f"<= {DTL_FLAG_DAYS}",
        "informational: slowest-to-liquidate name at the assumed book size")
    add("sector_map_pit_flag", "WARNING" if not sector_pit else "PASS",
        "sector_map_point_in_time", sector_pit, "true (ideal)",
        "static current-as-of sector map used for sector exposure (membership is stable; flagged)")

    # --- safety gates (all hard-false except preview_only) ---
    for nm, mv in (("no_orders_gate", "orders_enabled"),
                   ("no_broker_execution_gate", "broker_execution_enabled"),
                   ("no_automation_gate", "automation_enabled"),
                   ("no_hedging_gate", "hedging_enabled"),
                   ("no_order_sizing_gate", "order_sizing_enabled"),
                   ("no_trade_recommendation_gate", "trade_recommendation_made"),
                   ("no_network_gate", "network_used"),
                   ("no_live_data_gate", "live_data_used"),
                   ("no_paid_api_gate", "paid_apis_used"),
                   ("no_deploy_gate", "deployed"),
                   ("no_production_model_gate", "production_model_built"),
                   ("no_paper_trader_gate", "paper_trader_touched"),
                   ("no_gcp_gate", "gcp_touched"),
                   ("no_d_drive_write_gate", "d_drive_written"),
                   ("preview_only_gate", "preview_only")):
        want = True if mv == "preview_only" else False
        add(nm, "PASS", mv, want, str(want).lower(), "")
    return gates


def _derive_recommendation(gates: List[Dict]) -> str:
    safety_fail = any(g["status"] == "FAIL" and g["gate_name"].startswith(("no_", "preview_"))
                      for g in gates)
    capability_gates = ("weights_normalized_gate", "position_vol_gate", "beta_gate",
                        "idiosyncratic_gate", "concentration_gate", "liquidity_gate")
    capability_fail = any(g["status"] == "FAIL" and g["gate_name"] in capability_gates
                          for g in gates)
    if safety_fail or capability_fail:
        return REC_NEEDS_REVIEW
    return REC_READY


# --------------------------------------------------------------------------- #
# Artifact writers.
# --------------------------------------------------------------------------- #
def _write_sample_portfolio(paths, portfolio: pd.DataFrame) -> None:
    rows = [[r.ticker, r.sector, _round(r.weight),
             _round(float(r.weight) * ASSUMED_BOOK_USD, 2)] for r in portfolio.itertuples()]
    _write_csv(paths.sample_portfolio, ["ticker", "sector", "weight", "notional_usd_at_assumed_book"], rows)


def _write_position_risk(paths, pos: pd.DataFrame) -> None:
    rows = []
    for r in pos.itertuples():
        rows.append([r.ticker, r.sector, _round(r.weight), r.n_obs,
                     _round(r.realized_vol_annual), _round(r.beta_to_spy),
                     _round(r.residual_vol_annual), _round(r.avg_dollar_volume, 2)])
    _write_csv(paths.position_risk,
               ["ticker", "sector", "weight", "n_obs", "realized_vol_annual", "beta_to_spy",
                "residual_vol_annual", "avg_dollar_volume"], rows)


def _write_sector_exposure(paths, sectors: pd.DataFrame) -> None:
    rows = [[r.sector, _round(r.weight), int(r.n_names), _round(r.hhi_contrib)]
            for r in sectors.itertuples()]
    _write_csv(paths.sector_exposure, ["sector", "weight", "n_names", "hhi_contribution"], rows)


def _write_factor_tilt(paths, tilt: Dict) -> None:
    rows = []
    for key, v in tilt.get("factors", {}).items():
        rows.append([key, str(v.get("available")).lower(), _round(v.get("book_tilt_z")),
                     _round(v.get("coverage")), v.get("as_of_month", "")])
    rows.append(["composite", str(tilt.get("available")).lower(), _round(tilt.get("composite_tilt_z")), "", ""])
    _write_csv(paths.factor_tilt, ["factor", "available", "book_tilt_z", "coverage", "as_of_month"], rows)


def _write_liquidity(paths, liq: pd.DataFrame) -> None:
    rows = []
    for r in liq.itertuples():
        rows.append([r.ticker, _round(r.weight), _round(r.notional_usd, 2),
                     _round(r.avg_dollar_volume, 2), _round(r.participation),
                     _round(r.days_to_liquidate), r.liquidity_bucket])
    _write_csv(paths.liquidity,
               ["ticker", "weight", "notional_usd", "avg_dollar_volume", "participation_rate",
                "days_to_liquidate", "liquidity_bucket"], rows)


def _write_concentration(paths, conc: Dict, beta_view: Dict, diversifiable_idio: Optional[float]) -> None:
    rows = [
        ["n_positions", conc.get("n_positions")],
        ["herfindahl_index", conc.get("herfindahl_index")],
        ["effective_n_names", conc.get("effective_n_names")],
        ["max_name_weight", conc.get("max_name_weight")],
        ["top3_weight", conc.get("top3_weight")],
        ["top5_weight", conc.get("top5_weight")],
        ["n_sectors", conc.get("n_sectors")],
        ["sector_herfindahl_index", conc.get("sector_herfindahl_index")],
        ["effective_n_sectors", conc.get("effective_n_sectors")],
        ["top_sector", conc.get("top_sector")],
        ["top_sector_weight", conc.get("top_sector_weight")],
        ["net_beta", beta_view.get("net_beta")],
        ["portfolio_vol_annual", beta_view.get("portfolio_vol_annual")],
        ["systematic_variance_share", beta_view.get("systematic_variance_share")],
        ["idiosyncratic_variance_share", beta_view.get("idiosyncratic_variance_share")],
        ["diversifiable_idiosyncratic_vol_annual", _round(diversifiable_idio)],
    ]
    _write_csv(paths.concentration, ["metric", "value"], rows)


def _write_gate_matrix_csv(paths, gates) -> None:
    _write_csv(paths.gate_matrix, ["gate_name", "status", "metric", "value", "threshold", "note"],
               [[g["gate_name"], g["status"], g["metric"], g["value"], g["threshold"], g["note"]] for g in gates])


def _write_next_plan(paths, recommendation, conc, beta_view) -> None:
    plan = {
        "phase": "7-E",
        "title": "Regime / Risk Overlay Foundation (System 2)",
        "charter_mapping": "Charter Phase 5 — regime classification + risk overlay on top of System 1 + the 7-D risk lens",
        "gated_on": "Phase 7-D risk decomposition accepted; this run recommendation = " + recommendation,
        "current_book_snapshot": {"net_beta": beta_view.get("net_beta"),
                                  "effective_n_names": conc.get("effective_n_names"),
                                  "top_sector": conc.get("top_sector")},
        "build_next": [
            "Classify market regimes from local price/vol history (e.g., trend / vol / dispersion states).",
            "Define a measurement-only overlay that REPORTS how the 7-D risk lens shifts by regime.",
            "Keep it descriptive: no position sizing, no hedging, no orders, no automation.",
        ],
        "explicitly_not_in_7e": [
            "position sizing or weight optimization (forbidden until the owner endorses a sizing philosophy)",
            "hedging, order generation, broker connectivity, automation",
            "live provider calls / Paper Trader / GCP / deploy",
        ],
        "reused_from_7c_7d": [
            "Phase 7-C point-in-time factor construction (factor tilt lens)",
            "Phase 7-D per-position beta / vol / idiosyncratic / liquidity decomposition",
        ],
    }
    _write_json(paths.next_plan, plan)


# --------------------------------------------------------------------------- #
# Report assembly.
# --------------------------------------------------------------------------- #
def _assemble_report(paths, universe, portfolio, pos, beta_view, sectors, conc, liq, tilt,
                     diversifiable_idio, gates, recommendation, sector_pit, data_window,
                     price_dates) -> Dict:
    counts = {"PASS": 0, "FAIL": 0, "WARNING": 0}
    for g in gates:
        counts[g["status"]] = counts.get(g["status"], 0) + 1

    worst_dtl = liq["days_to_liquidate"].max(skipna=True)
    worst_dtl = float(worst_dtl) if pd.notna(worst_dtl) else None
    thin_names = [r.ticker for r in liq.itertuples() if r.liquidity_bucket == "thin"]

    return {
        "phase": PHASE,
        "objective": OBJECTIVE,
        "generated_by": "research\\run_phase7d_risk_decomposition_foundation.py",
        "charter": {
            "path": "docs/project_charter_sp500_multifactor_ranking_v1.md",
            "mapping": "Charter Phase 4.5 — book-level risk decomposition (measurement only).",
            "principle": "Explain exposure, not predict returns. No sizing, no hedging, no orders.",
        },
        "reuses": "research/run_phase7c_multifactor_ranking_engine.py (factor construction, sector map, loaders)",
        # Safety flags.
        "preview_only": True, "network_used": False, "live_data_used": False,
        "paid_apis_used": False, "api_key_required": False,
        "orders_enabled": False, "broker_execution_enabled": False, "automation_enabled": False,
        "hedging_enabled": False, "order_sizing_enabled": False, "trade_recommendation_made": False,
        "deployed": False, "production_model_built": False,
        "paper_trader_touched": False, "gcp_touched": False,
        "raw_files_modified": False, "d_drive_written": False, "binary_artifacts_created": False,
        # Data / universe.
        "data": {"price_panel": _rel(Path(PRICE_CSV)),
                 "price_date_range": f"{price_dates.min().date()}..{price_dates.max().date()}",
                 "risk_window_days": BETA_WINDOW_D, "risk_window_dates": data_window,
                 "investable_universe_n": len(universe)},
        "sample_portfolio": {
            "is_live_holdings": False,
            "construction": f"deterministic: {N_SAMPLE} names evenly spaced across the sorted "
                            "universe, linearly-decaying weights summing to 1 (NOT a recommendation)",
            "n_positions": int(len(portfolio)),
            "assumed_gross_book_usd": ASSUMED_BOOK_USD,
            "holdings": [{"ticker": r.ticker, "sector": r.sector, "weight": _round(r.weight)}
                         for r in portfolio.itertuples()],
        },
        # The five risk lenses.
        "risk_view": {
            "market_beta_exposure": beta_view,
            "sector_concentration": {
                "sectors": [{"sector": r.sector, "weight": _round(r.weight), "n_names": int(r.n_names)}
                            for r in sectors.itertuples()],
                "top_sector": conc.get("top_sector"), "top_sector_weight": conc.get("top_sector_weight"),
                "sector_herfindahl_index": conc.get("sector_herfindahl_index"),
                "effective_n_sectors": conc.get("effective_n_sectors"),
            },
            "factor_tilt_exposure": tilt,
            "idiosyncratic_concentration_risk": {
                "herfindahl_index": conc.get("herfindahl_index"),
                "effective_n_names": conc.get("effective_n_names"),
                "max_name_weight": conc.get("max_name_weight"),
                "top3_weight": conc.get("top3_weight"), "top5_weight": conc.get("top5_weight"),
                "idiosyncratic_variance_share": beta_view.get("idiosyncratic_variance_share"),
                "diversifiable_idiosyncratic_vol_annual": _round(diversifiable_idio),
            },
            "liquidity_risk": {
                "assumed_gross_book_usd": ASSUMED_BOOK_USD,
                "participation_rate": LIQ_PARTICIPATION,
                "worst_days_to_liquidate": _round(worst_dtl),
                "thin_names": thin_names,
                "n_positions_priced": int(liq["days_to_liquidate"].notna().sum()),
            },
        },
        "sector_neutralization_note": {
            "sector_map_point_in_time": sector_pit,
            "caveat": "static current-as-of sector map; membership is stable but not strictly PIT",
        },
        "gate_summary": {"counts": counts, "gates": gates},
        "recommendation": recommendation,
        "recommendation_allowed_values": list(ALLOWED_RECOMMENDATIONS),
        "what_is_still_caveated": [
            "The portfolio is a DETERMINISTIC SAMPLE, not live or recommended holdings.",
            "Beta / vol / idiosyncratic split use a single trailing window (no regime conditioning yet — that is Phase 7-E).",
            "Idiosyncratic split assumes residual = portfolio variance minus net-beta systematic variance; "
            "the diversifiable proxy additionally assumes zero residual correlation across names.",
            "Liquidity uses adjusted-close * volume as a dollar-volume proxy at an ASSUMED gross book size.",
            "Sector exposure uses a static current-as-of sector map (not strictly point-in-time).",
            "Factor tilt reuses annual-only Phase 7-C factors (value/quality/growth stale up to ~1 year).",
        ],
        "recommended_next_phase": {"phase": "7-E", "title": "Regime / Risk Overlay Foundation (System 2)"},
        "commit_appropriate": False,
        "commit_note": "Per task instruction nothing is committed or pushed.",
        "artifacts": [_rel(paths.report), _rel(paths.sample_portfolio), _rel(paths.position_risk),
                      _rel(paths.sector_exposure), _rel(paths.factor_tilt), _rel(paths.liquidity),
                      _rel(paths.concentration), _rel(paths.gate_matrix), _rel(paths.next_plan)],
    }


def _error_report(msg: str, tb: str) -> Dict:
    return {"phase": PHASE, "recommendation": REC_ERROR, "error": msg, "traceback": tb,
            "preview_only": True, "network_used": False, "live_data_used": False,
            "orders_enabled": False, "trade_recommendation_made": False,
            "recommendation_allowed_values": list(ALLOWED_RECOMMENDATIONS)}


def _blocked_report(reason: str) -> Dict:
    return {"phase": PHASE, "recommendation": REC_BLOCKED, "blocked_reason": reason,
            "preview_only": True, "network_used": False, "live_data_used": False,
            "orders_enabled": False, "trade_recommendation_made": False,
            "recommendation_allowed_values": list(ALLOWED_RECOMMENDATIONS)}


def _print_summary(report: Dict) -> None:
    print(f"[7-D] recommendation = {report.get('recommendation')}")
    if report.get("error"):
        print(f"      ERROR: {report['error']}")
        return
    if report.get("blocked_reason"):
        print(f"      BLOCKED: {report['blocked_reason']}")
        return
    mbe = report["risk_view"]["market_beta_exposure"]
    icr = report["risk_view"]["idiosyncratic_concentration_risk"]
    liq = report["risk_view"]["liquidity_risk"]
    print(f"      net_beta = {mbe.get('net_beta')}  port_vol_annual = {mbe.get('portfolio_vol_annual')}  "
          f"systematic_share = {mbe.get('systematic_variance_share')}")
    print(f"      HHI = {icr.get('herfindahl_index')}  effective_n = {icr.get('effective_n_names')}  "
          f"max_name_w = {icr.get('max_name_weight')}")
    print(f"      top_sector = {report['risk_view']['sector_concentration'].get('top_sector')} "
          f"({report['risk_view']['sector_concentration'].get('top_sector_weight')})  "
          f"worst_DTL = {liq.get('worst_days_to_liquidate')}")
    print(f"      gates: {report['gate_summary']['counts']}")


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 7-D risk decomposition foundation (offline, measurement-only).")
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
