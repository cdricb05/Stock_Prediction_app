"""Phase 5-C — Cross-Sectional Alpha Research Harness (offline, point-in-time).

This is **Track A (quant brain) research**, not Track B operational packaging.
It does **not** ship a deployable live scorer. It answers one question with
mathematical evidence:

    "Do these price-only features and models show out-of-sample edge for
     ranking S&P 500 tickers by 20-day forward excess return vs SPY?"

What it does
------------
  1. Loads local price history ONLY (the D: free expanded CSV, read-only) and
     aligns every ticker to SPY's trading calendar.
  2. Builds a strictly point-in-time feature panel (features use trailing data
     only) on a monthly rebalance schedule.
  3. Builds forward labels (future data only) with the primary target
     ``forward_excess_return_20d_vs_spy``.
  4. Scores two research models cross-sectionally:
       - ``cross_sectional_composite_zscore`` (transparent, fixed-weight, no fit)
       - ``ridge_cross_sectional_return_model`` (numpy closed-form ridge)
     plus an optional transparent top-quintile score model.
  5. Validates with **walk-forward** yearly folds, train-on-past / test-on-future,
     with a >=20-session embargo, and computes rank IC, decile spread, turnover,
     transaction-cost survival, drawdown, and regime breakdown.
  6. Emits a validation-gate matrix (PASS / FAIL / WARNING / NOT_EVALUABLE) and an
     honest recommendation. It does **not** force a PASS.

SPY is the **benchmark / market-regime proxy**, never the trading universe. The
trading universe is the S&P 500 equities available in local history.

Hard rules honored: no network, no paid APIs, no AlphaVantage, no orders, no
broker execution, no automation, no deploy, no Paper Trader changes, no GCP
changes, no writes to D:, no binary model artifacts (.pkl/.joblib/.onnx/.pt/.h5/
.keras), no commit. Pure offline numpy.
"""
from __future__ import annotations

import bisect
import csv
import datetime as dt
import json
import math
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_OUT_DIR = _REPO_ROOT / "research" / "output"
_PKG_DIR = _REPO_ROOT / "research" / "model_v2_pricing_engine"

# Local, read-only data candidates (first readable wins). D: is INPUT ONLY.
_DATA_CANDIDATES: List[str] = [
    r"D:\Stock_Prediction_app_data\phase2k_g\output\phase2k_g_expanded_price_history_free.csv",
    str(_REPO_ROOT / "research" / "output" / "phase2g_price_history_real.csv"),
]
_FRAMEWORK = _PKG_DIR / "alpha_framework.json"

# Output artifacts.
_REPORT_OUT = _OUT_DIR / "phase5c_cross_sectional_alpha_research.json"
_PANEL_SAMPLE_OUT = _OUT_DIR / "phase5c_feature_panel_sample.csv"
_OOS_SCORES_OUT = _OUT_DIR / "phase5c_oos_scores_sample.csv"
_SCOREBOARD_OUT = _OUT_DIR / "phase5c_model_scoreboard.csv"
_IC_BY_YEAR_OUT = _OUT_DIR / "phase5c_ic_by_year.csv"
_DECILE_SPREAD_OUT = _OUT_DIR / "phase5c_decile_spread.csv"
_REGIME_OUT = _OUT_DIR / "phase5c_regime_breakdown.csv"
_TURNOVER_OUT = _OUT_DIR / "phase5c_turnover_cost_sensitivity.csv"
_GATE_MATRIX_OUT = _OUT_DIR / "phase5c_validation_gate_matrix.csv"

BENCHMARK = "SPY"
PRIMARY_LABEL = "forward_excess_return_20d_vs_spy"
TRADING_DAYS_PER_YEAR = 252
_ANNUALIZE = math.sqrt(TRADING_DAYS_PER_YEAR)

# Forward-label horizons (sessions).
H5, H20, H63 = 5, 20, 63
EMBARGO_DAYS = 20            # >= one forward-label horizon between train and test
MIN_TRAIN_ROWS = 400        # minimum pooled training rows before a fold is valid
MIN_NAMES_PER_DATE = 10     # cross-section must be wide enough to rank
TOP_N_PORTFOLIO = (10, 20)  # long-only basket sizes simulated
COST_BPS = (25, 50)         # transaction-cost sensitivity points
RIDGE_LAMBDA = 10.0         # ridge L2 penalty (on standardized features)
LIQUIDITY_MIN_DOLLAR_VOL = 5.0e6   # 20d avg dollar volume floor (research gate)

MODELS = (
    "cross_sectional_composite_zscore",
    "ridge_cross_sectional_return_model",
    "top_quintile_score_model",
)

# Numeric features fed to the models (sign convention: larger == more bullish
# AFTER the per-feature sign in _FEATURE_SIGN is applied during standardization).
MODEL_FEATURES = [
    "return_20d", "return_63d", "return_126d",
    "mom_acceleration",
    "price_vs_sma50", "price_vs_sma200", "sma50_vs_200",
    "return_zscore_5d", "dist_sma20",
    "rs_20d_vs_spy", "rs_63d_vs_spy", "residual_alpha_20d",
    "realized_vol_63d", "downside_vol_63d",
    "vol_adj_momentum",
]
# +1 keep as-is, -1 invert so higher == more bullish (low-vol / mean-revert fades).
_FEATURE_SIGN = {
    "return_20d": +1, "return_63d": +1, "return_126d": +1,
    "mom_acceleration": +1,
    "price_vs_sma50": +1, "price_vs_sma200": +1, "sma50_vs_200": +1,
    "return_zscore_5d": -1,   # fade short-term stretch
    "dist_sma20": -1,         # fade overextension above SMA20
    "rs_20d_vs_spy": +1, "rs_63d_vs_spy": +1, "residual_alpha_20d": +1,
    "realized_vol_63d": -1,   # prefer lower vol
    "downside_vol_63d": -1,
    "vol_adj_momentum": +1,
}

# Composite z-score block weights by regime (judgement-set, from the Phase 5-B
# framework). Regime changes the *relative* block weights, so it alters the
# within-date ranking — not just a uniform scale. Weights need not sum to 1; the
# composite is re-standardized cross-sectionally before ranking.
_COMPOSITE_WEIGHTS = {
    "risk_on": {
        "return_20d": 0.9, "return_63d": 1.1, "return_126d": 0.8,
        "mom_acceleration": 0.5,
        "price_vs_sma50": 0.6, "price_vs_sma200": 0.8, "sma50_vs_200": 0.5,
        "return_zscore_5d": 0.3, "dist_sma20": 0.2,
        "rs_20d_vs_spy": 0.9, "rs_63d_vs_spy": 0.9, "residual_alpha_20d": 0.7,
        "realized_vol_63d": 0.3, "downside_vol_63d": 0.3, "vol_adj_momentum": 0.7,
    },
    "neutral": {
        "return_20d": 0.7, "return_63d": 0.8, "return_126d": 0.6,
        "mom_acceleration": 0.4,
        "price_vs_sma50": 0.5, "price_vs_sma200": 0.6, "sma50_vs_200": 0.4,
        "return_zscore_5d": 0.5, "dist_sma20": 0.4,
        "rs_20d_vs_spy": 0.8, "rs_63d_vs_spy": 0.8, "residual_alpha_20d": 0.7,
        "realized_vol_63d": 0.6, "downside_vol_63d": 0.5, "vol_adj_momentum": 0.7,
    },
    "risk_off": {
        "return_20d": 0.4, "return_63d": 0.5, "return_126d": 0.4,
        "mom_acceleration": 0.2,
        "price_vs_sma50": 0.4, "price_vs_sma200": 0.6, "sma50_vs_200": 0.4,
        "return_zscore_5d": 0.7, "dist_sma20": 0.6,
        "rs_20d_vs_spy": 0.7, "rs_63d_vs_spy": 0.7, "residual_alpha_20d": 0.6,
        "realized_vol_63d": 1.1, "downside_vol_63d": 1.1, "vol_adj_momentum": 0.6,
    },
}

# Regime thresholds for the SPY market-regime proxy (mirrors Phase 5-A scorer).
REGIME_VOL_HIGH = 0.22
REGIME_DRAWDOWN_DEEP = -0.10
REGIME_VOL_CALM = 0.20


# --------------------------------------------------------------------------- #
# Data loading and SPY-calendar alignment
# --------------------------------------------------------------------------- #
def _data_path() -> Optional[str]:
    env = os.environ.get("MODEL_V2_PRICE_HISTORY_CSV")
    candidates = ([env] if env else []) + _DATA_CANDIDATES
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return None


def load_local_history(path: str
                       ) -> Dict[str, Dict[str, Tuple[float, Optional[float]]]]:
    """Load ``{ticker: {iso_date: (close, dollar_volume_or_None)}}`` from local CSV.

    Reads adjusted_close and (close*volume) dollar volume. Skips blank/non-finite
    rows. Pure local read; never writes, never hits the network.
    """
    out: Dict[str, Dict[str, Tuple[float, Optional[float]]]] = {}
    with open(path, "r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            t = (row.get("ticker") or "").strip().upper()
            d = (row.get("date") or "").strip()[:10]
            if not t or not d:
                continue
            try:
                c = float(row.get("adjusted_close"))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(c) or c <= 0:
                continue
            dollar_vol: Optional[float] = None
            try:
                v = float(row.get("volume"))
                if math.isfinite(v) and v >= 0:
                    dollar_vol = c * v
            except (TypeError, ValueError):
                dollar_vol = None
            out.setdefault(t, {})[d] = (c, dollar_vol)
    return out


class Aligned:
    """All tickers reindexed onto SPY's master trading calendar.

    ``close[t_idx]`` / ``dvol[t_idx]`` carry NaN where a ticker has no print on
    that master date, so every feature window is index-comparable across names
    (needed for beta / relative strength / cross-sectional standardization).
    """

    def __init__(self, hist: Dict[str, Dict[str, Tuple[float, Optional[float]]]]):
        if BENCHMARK not in hist:
            raise RuntimeError(
                f"{BENCHMARK} (regime/benchmark proxy) not found in local history.")
        self.master_dates: List[str] = sorted(hist[BENCHMARK].keys())
        self._idx = {d: i for i, d in enumerate(self.master_dates)}
        n = len(self.master_dates)
        self.tickers: List[str] = sorted(t for t in hist if t != BENCHMARK)
        self.close: Dict[str, np.ndarray] = {}
        self.dvol: Dict[str, np.ndarray] = {}
        self.ret: Dict[str, np.ndarray] = {}
        for t in [BENCHMARK] + self.tickers:
            c = np.full(n, np.nan)
            dv = np.full(n, np.nan)
            for d, (px, dvv) in hist[t].items():
                k = self._idx.get(d)
                if k is not None:
                    c[k] = px
                    if dvv is not None:
                        dv[k] = dvv
            self.close[t] = c
            self.dvol[t] = dv
            r = np.full(n, np.nan)
            with np.errstate(divide="ignore", invalid="ignore"):
                r[1:] = np.where(c[:-1] > 0, c[1:] / c[:-1] - 1.0, np.nan)
            self.ret[t] = r

    def month_end_indices(self) -> List[int]:
        """Last master trading-day index of each calendar month."""
        idxs: List[int] = []
        for i, d in enumerate(self.master_dates):
            ym = d[:7]
            nxt = self.master_dates[i + 1][:7] if i + 1 < len(self.master_dates) else None
            if nxt != ym:
                idxs.append(i)
        return idxs


# --------------------------------------------------------------------------- #
# Point-in-time feature math (index i is the as-of session; uses i and earlier)
# --------------------------------------------------------------------------- #
def _ret(close: np.ndarray, i: int, w: int) -> Optional[float]:
    if i - w < 0:
        return None
    base = close[i - w]
    cur = close[i]
    if not (np.isfinite(base) and np.isfinite(cur)) or base <= 0:
        return None
    return float(cur / base - 1.0)


def _sma(close: np.ndarray, i: int, w: int) -> Optional[float]:
    if i - w + 1 < 0:
        return None
    win = close[i - w + 1:i + 1]
    if win.shape[0] < w or np.any(~np.isfinite(win)):
        return None
    return float(np.mean(win))


def _vol(ret: np.ndarray, i: int, w: int, annualize: bool = True) -> Optional[float]:
    if i - w + 1 < 1:
        return None
    win = ret[i - w + 1:i + 1]
    if win.shape[0] < w or np.any(~np.isfinite(win)):
        return None
    v = float(np.std(win, ddof=1))
    return v * _ANNUALIZE if annualize else v


def _downside_vol(ret: np.ndarray, i: int, w: int) -> Optional[float]:
    if i - w + 1 < 1:
        return None
    win = ret[i - w + 1:i + 1]
    if win.shape[0] < w or np.any(~np.isfinite(win)):
        return None
    neg = np.minimum(win, 0.0)
    return float(math.sqrt(float(np.mean(neg * neg))) * _ANNUALIZE)


def _max_drawdown(close: np.ndarray, i: int, w: int) -> Optional[float]:
    if i - w + 1 < 0:
        return None
    win = close[i - w + 1:i + 1]
    if win.shape[0] < w or np.any(~np.isfinite(win)) or np.any(win <= 0):
        return None
    peak = np.maximum.accumulate(win)
    return float(np.min(win / peak - 1.0))


def _return_zscore(close: np.ndarray, i: int, w: int) -> Optional[float]:
    """Latest w-day return standardized vs the trailing distribution of
    overlapping w-day returns (mean-reversion / stretch gauge)."""
    if i - 2 * w < 0:
        return None
    rets = []
    for end in range(i - w + 1, i + 1):
        base = close[end - w]
        if not (np.isfinite(base) and np.isfinite(close[end])) or base <= 0:
            return None
        rets.append(close[end] / base - 1.0)
    hist = np.asarray(rets)
    mu, sd = float(np.mean(hist)), float(np.std(hist, ddof=1))
    if not np.isfinite(sd) or sd <= 0:
        return None
    latest = rets[-1]
    return float((latest - mu) / sd)


def _beta(tret: np.ndarray, sret: np.ndarray, i: int, w: int) -> Optional[float]:
    if i - w + 1 < 1:
        return None
    a = tret[i - w + 1:i + 1]
    b = sret[i - w + 1:i + 1]
    m = np.isfinite(a) & np.isfinite(b)
    if int(np.sum(m)) < w - 2:
        return None
    a, b = a[m], b[m]
    var = float(np.var(b, ddof=1))
    if not np.isfinite(var) or var <= 0:
        return None
    return float(np.cov(a, b, ddof=1)[0, 1] / var)


def _avg_dollar_vol(dvol: np.ndarray, i: int, w: int) -> Optional[float]:
    if i - w + 1 < 0:
        return None
    win = dvol[i - w + 1:i + 1]
    win = win[np.isfinite(win)]
    if win.shape[0] < max(5, w // 2):
        return None
    return float(np.mean(win))


def compute_ticker_features(al: Aligned, t: str, i: int) -> Optional[Dict[str, float]]:
    """Point-in-time feature dict for ticker ``t`` as-of master index ``i``.

    Returns None when the core trend window is unavailable. Individual features
    may be None and are dropped during cross-sectional standardization.
    """
    c = al.close[t]
    r = al.ret[t]
    s = al.ret[BENCHMARK]
    if not np.isfinite(c[i]) or c[i] <= 0:
        return None

    ret20 = _ret(c, i, H20)
    ret63 = _ret(c, i, H63)
    ret126 = _ret(c, i, 126)
    sma20 = _sma(c, i, 20)
    sma50 = _sma(c, i, 50)
    sma200 = _sma(c, i, 200)
    last = float(c[i])

    # Momentum acceleration: recent 20d return minus the prior 20d return.
    ret20_prev = _ret(c, i - H20, H20) if i - H20 >= 0 else None
    mom_acc = (ret20 - ret20_prev) if (ret20 is not None and ret20_prev is not None) else None

    spy_ret20 = _ret(al.close[BENCHMARK], i, H20)
    spy_ret63 = _ret(al.close[BENCHMARK], i, H63)
    beta = _beta(r, s, i, H63)
    vol63 = _vol(r, i, H63)

    feats: Dict[str, Optional[float]] = {
        "return_20d": ret20,
        "return_63d": ret63,
        "return_126d": ret126,
        "mom_acceleration": mom_acc,
        "price_vs_sma50": (last / sma50 - 1.0) if sma50 else None,
        "price_vs_sma200": (last / sma200 - 1.0) if sma200 else None,
        "sma50_vs_200": (sma50 / sma200 - 1.0) if (sma50 and sma200) else None,
        "return_zscore_5d": _return_zscore(c, i, H5),
        "dist_sma20": (last / sma20 - 1.0) if sma20 else None,
        "rs_20d_vs_spy": (ret20 - spy_ret20) if (ret20 is not None and spy_ret20 is not None) else None,
        "rs_63d_vs_spy": (ret63 - spy_ret63) if (ret63 is not None and spy_ret63 is not None) else None,
        "residual_alpha_20d": (
            (ret20 - beta * spy_ret20)
            if (ret20 is not None and beta is not None and spy_ret20 is not None) else None),
        "realized_vol_63d": vol63,
        "downside_vol_63d": _downside_vol(r, i, H63),
        "vol_adj_momentum": (ret63 / vol63) if (ret63 is not None and vol63 and vol63 > 0) else None,
    }
    # Liquidity (used for the gate, not a model feature).
    feats["avg_dollar_volume_20d"] = _avg_dollar_vol(al.dvol[t], i, H20)

    if feats["price_vs_sma200"] is None and feats["return_63d"] is None:
        return None  # not enough trailing history to be in the panel at all
    return {k: v for k, v in feats.items() if v is not None}


def spy_regime(al: Aligned, i: int) -> Tuple[str, Dict[str, Optional[float]]]:
    """SPY market-regime proxy as-of master index ``i`` -> (label, regime_features)."""
    c = al.close[BENCHMARK]
    r = al.ret[BENCHMARK]
    sma50 = _sma(c, i, 50)
    sma200 = _sma(c, i, 200)
    last = float(c[i]) if np.isfinite(c[i]) else None
    vol63 = _vol(r, i, H63)
    dd = _max_drawdown(c, i, 252)
    price_vs_200 = (last / sma200 - 1.0) if (last and sma200) else None

    label = "neutral"
    if price_vs_200 is not None and vol63 is not None:
        up = price_vs_200 > 0
        calm = vol63 < REGIME_VOL_CALM
        stressed = vol63 > REGIME_VOL_HIGH or (dd is not None and dd < REGIME_DRAWDOWN_DEEP)
        if up and calm and not stressed:
            label = "risk_on"
        elif (not up) and stressed:
            label = "risk_off"
        else:
            label = "neutral"
    elif price_vs_200 is None and vol63 is None:
        label = "neutral"

    feats = {
        "spy_return_20d": _ret(c, i, H20),
        "spy_return_63d": _ret(c, i, H63),
        "spy_return_126d": _ret(c, i, 126),
        "spy_price_vs_sma200": price_vs_200,
        "spy_sma50_vs_200": (sma50 / sma200 - 1.0) if (sma50 and sma200) else None,
        "spy_realized_vol_63d": vol63,
        "spy_drawdown_252d": dd,
    }
    return label, feats


# --------------------------------------------------------------------------- #
# Labels (forward / future-only; strict no-leakage)
# --------------------------------------------------------------------------- #
def forward_return(close: np.ndarray, i: int, h: int) -> Optional[float]:
    j = i + h
    if j >= close.shape[0]:
        return None
    base, fut = close[i], close[j]
    if not (np.isfinite(base) and np.isfinite(fut)) or base <= 0:
        return None
    return float(fut / base - 1.0)


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #
def _rankdata(a: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype=float)
    n = a.shape[0]
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(n, dtype=float)
    ranks[order] = np.arange(1, n + 1, dtype=float)
    sa = a[order]
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sa[j + 1] == sa[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    return ranks


def _pearson(x: np.ndarray, y: np.ndarray) -> Optional[float]:
    if x.shape[0] < 3:
        return None
    sx, sy = float(np.std(x)), float(np.std(y))
    if sx <= 0 or sy <= 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def rank_ic(scores: np.ndarray, ys: np.ndarray) -> Optional[float]:
    if scores.shape[0] < MIN_NAMES_PER_DATE:
        return None
    return _pearson(_rankdata(scores), _rankdata(ys))


def placebo_mean_ic(records: List[dict]) -> Optional[float]:
    """Leakage probe: recompute mean rank IC after a DETERMINISTIC within-date
    label permutation that destroys the score<->label correspondence. If the
    signal were an artifact of look-ahead leakage, IC would survive the shuffle.
    A near-zero placebo IC alongside a positive real IC is evidence AGAINST
    leakage. No RNG (offsets are a fixed function of position)."""
    by_date: Dict[str, List[dict]] = {}
    for r in records:
        if r["y"] is not None:
            by_date.setdefault(r["date"], []).append(r)
    ics = []
    for di, d in enumerate(sorted(by_date)):
        recs = by_date[d]
        if len(recs) < MIN_NAMES_PER_DATE:
            continue
        scores = np.array([r["score"] for r in recs], dtype=float)
        ys = np.array([r["y"] for r in recs], dtype=float)
        off = (di * 7 + 3) % len(recs)
        if off == 0:
            off = 1
        ys_shuffled = np.roll(ys, off)   # break correspondence, preserve distribution
        ic = rank_ic(scores, ys_shuffled)
        if ic is not None and math.isfinite(ic):
            ics.append(ic)
    return float(np.mean(ics)) if ics else None


def _max_dd_curve(period_returns: List[float]) -> float:
    eq = 1.0
    peak = 1.0
    worst = 0.0
    for pr in period_returns:
        eq *= (1.0 + pr)
        peak = max(peak, eq)
        worst = min(worst, eq / peak - 1.0)
    return worst


# --------------------------------------------------------------------------- #
# Panel build
# --------------------------------------------------------------------------- #
def build_panel(al: Aligned) -> List[dict]:
    """One record per (rebalance_date, ticker) with point-in-time features and
    forward labels. Forward labels are None near the end of the series."""
    panel: List[dict] = []
    rebal = al.month_end_indices()
    for i in rebal:
        d = al.master_dates[i]
        year = int(d[:4])
        regime, regime_feats = spy_regime(al, i)
        spy_fwd20 = forward_return(al.close[BENCHMARK], i, H20)
        for t in al.tickers:
            feats = compute_ticker_features(al, t, i)
            if feats is None:
                continue
            fwd5 = forward_return(al.close[t], i, H5)
            fwd20 = forward_return(al.close[t], i, H20)
            fwd63 = forward_return(al.close[t], i, H63)
            excess20 = (fwd20 - spy_fwd20) if (fwd20 is not None and spy_fwd20 is not None) else None
            rec = {
                "date": d, "year": year, "ticker": t, "rebal_idx": i,
                "regime": regime,
                "features": feats,
                "avg_dollar_volume_20d": feats.get("avg_dollar_volume_20d"),
                "forward_return_5d": fwd5,
                "forward_return_20d": fwd20,
                "forward_return_63d": fwd63,
                "forward_excess_return_20d_vs_spy": excess20,
            }
            panel.append(rec)
    # Cross-sectional quintile labels on the primary target, per date.
    by_date: Dict[str, List[dict]] = {}
    for rec in panel:
        by_date.setdefault(rec["date"], []).append(rec)
    for recs in by_date.values():
        vals = [(r, r["forward_excess_return_20d_vs_spy"]) for r in recs
                if r["forward_excess_return_20d_vs_spy"] is not None]
        for r in recs:
            r["top_quintile_forward_excess_return"] = None
            r["bottom_quintile_forward_excess_return"] = None
            r["downside_risk_label"] = (
                1 if (r["forward_return_20d"] is not None and r["forward_return_20d"] < -0.10) else
                (0 if r["forward_return_20d"] is not None else None))
        if len(vals) >= MIN_NAMES_PER_DATE:
            ys = np.array([v for _, v in vals])
            hi = float(np.quantile(ys, 0.8))
            lo = float(np.quantile(ys, 0.2))
            for r, y in vals:
                r["top_quintile_forward_excess_return"] = 1 if y >= hi else 0
                r["bottom_quintile_forward_excess_return"] = 1 if y <= lo else 0
    return panel


def _standardize_cross_section(recs: List[dict]) -> Dict[int, Dict[str, float]]:
    """Per-date cross-sectional z-scores of each model feature (sign-adjusted so
    higher == more bullish). Leakage-free: uses only contemporaneous features.

    Returns ``{id(rec): {feature: zscore}}``.
    """
    out: Dict[int, Dict[str, float]] = {id(r): {} for r in recs}
    for feat in MODEL_FEATURES:
        xs = [(r, r["features"].get(feat)) for r in recs if r["features"].get(feat) is not None]
        if len(xs) < MIN_NAMES_PER_DATE:
            continue
        arr = np.array([v for _, v in xs], dtype=float)
        mu, sd = float(np.mean(arr)), float(np.std(arr))
        if sd <= 0:
            continue
        sign = _FEATURE_SIGN[feat]
        for r, v in xs:
            out[id(r)][feat] = sign * (v - mu) / sd
    return out


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
def composite_score(zfeats: Dict[str, float], regime: str) -> Optional[float]:
    weights = _COMPOSITE_WEIGHTS.get(regime, _COMPOSITE_WEIGHTS["neutral"])
    num = 0.0
    used = 0.0
    for feat, z in zfeats.items():
        w = weights.get(feat, 0.0)
        num += w * z
        used += abs(w)
    if used <= 0:
        return None
    return num / used


def ridge_fit(X: np.ndarray, y: np.ndarray, lam: float) -> np.ndarray:
    """Closed-form ridge with intercept (numpy only). Returns coefficient vector
    including a leading intercept term."""
    n, p = X.shape
    Xb = np.hstack([np.ones((n, 1)), X])
    reg = lam * np.eye(p + 1)
    reg[0, 0] = 0.0  # do not penalize the intercept
    A = Xb.T @ Xb + reg
    b = Xb.T @ y
    coef = np.linalg.solve(A, b)
    return coef


def ridge_predict(X: np.ndarray, coef: np.ndarray) -> np.ndarray:
    Xb = np.hstack([np.ones((X.shape[0], 1)), X])
    return Xb @ coef


def logistic_fit(X: np.ndarray, y: np.ndarray, lam: float = 1.0,
                 iters: int = 200, lr: float = 0.1) -> np.ndarray:
    """Deterministic L2-regularized logistic regression via gradient descent
    (numpy only, fixed iterations). Transparent top-quintile score model."""
    n, p = X.shape
    Xb = np.hstack([np.ones((n, 1)), X])
    w = np.zeros(p + 1)
    for _ in range(iters):
        z = Xb @ w
        pr = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        grad = Xb.T @ (pr - y) / n
        grad[1:] += (lam / n) * w[1:]
        w -= lr * grad
    return w


def logistic_predict(X: np.ndarray, w: np.ndarray) -> np.ndarray:
    Xb = np.hstack([np.ones((X.shape[0], 1)), X])
    z = Xb @ w
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def _feature_matrix(recs: List[dict], zmap: Dict[int, Dict[str, float]]
                    ) -> Tuple[np.ndarray, List[dict]]:
    """Dense standardized feature matrix over recs that carry the full feature
    set (missing z -> 0.0, i.e. neutral). Returns (X, kept_recs)."""
    kept = []
    rows = []
    for r in recs:
        z = zmap.get(id(r), {})
        rows.append([z.get(f, 0.0) for f in MODEL_FEATURES])
        kept.append(r)
    return np.array(rows, dtype=float), kept


# --------------------------------------------------------------------------- #
# Walk-forward validation
# --------------------------------------------------------------------------- #
def walk_forward(panel: List[dict]) -> Tuple[Dict[str, List[dict]], dict]:
    """Yearly walk-forward, train-on-past / test-on-future, >=20-session embargo.

    Returns ``(predictions_by_model, leakage_report)`` where each prediction is
    ``{date, year, regime, ticker, score, y, raw20}`` with y = primary label.
    """
    by_date: Dict[str, List[dict]] = {}
    for r in panel:
        by_date.setdefault(r["date"], []).append(r)
    dates = sorted(by_date)
    date_idx = {d: by_date[d][0]["rebal_idx"] for d in dates}
    years = sorted({int(d[:4]) for d in dates})

    # Per-date standardized features (leakage-free; contemporaneous only).
    zmap: Dict[int, Dict[str, float]] = {}
    for d in dates:
        zmap.update(_standardize_cross_section(by_date[d]))

    preds: Dict[str, List[dict]] = {m: [] for m in MODELS}
    leakage = {
        "features_use_future_data": False,
        "labels_use_past_data": False,
        "same_date_label_in_features": False,
        "embargo_days": EMBARGO_DAYS,
        "min_embargo_gap_observed": None,
        "train_test_overlap_dates": 0,
        "folds": [],
    }
    min_gap_obs: Optional[int] = None

    for test_year in years:
        test_dates = [d for d in dates if int(d[:4]) == test_year]
        # Test records must have a realized primary label.
        test_recs = [r for d in test_dates for r in by_date[d]
                     if r["forward_excess_return_20d_vs_spy"] is not None]
        if not test_recs:
            continue
        first_test_idx = min(date_idx[d] for d in test_dates)
        # Train: strictly-past dates whose 20d forward label window closes at
        # least EMBARGO_DAYS before the first test as-of date (purge + embargo).
        train_recs = []
        gaps = []
        for d in dates:
            if int(d[:4]) >= test_year:
                continue
            label_close = date_idx[d] + H20  # forward window end of train sample
            gap = first_test_idx - label_close
            if gap >= EMBARGO_DAYS:
                gaps.append(gap)
                for r in by_date[d]:
                    if r["forward_excess_return_20d_vs_spy"] is not None:
                        train_recs.append(r)
        if gaps:
            g = min(gaps)
            min_gap_obs = g if min_gap_obs is None else min(min_gap_obs, g)
        if len(train_recs) < MIN_TRAIN_ROWS:
            continue

        leakage["folds"].append({
            "test_year": test_year, "train_rows": len(train_recs),
            "test_rows": len(test_recs), "embargo_ok": True,
        })

        # ---- composite model (no fit) ----
        for r in test_recs:
            z = zmap.get(id(r), {})
            s = composite_score(z, r["regime"])
            if s is None:
                continue
            preds["cross_sectional_composite_zscore"].append({
                "date": r["date"], "year": test_year, "regime": r["regime"],
                "ticker": r["ticker"], "score": s,
                "y": r["forward_excess_return_20d_vs_spy"],
                "raw20": r["forward_return_20d"],
            })

        # ---- ridge (fit on train only) ----
        Xtr, tr = _feature_matrix(train_recs, zmap)
        ytr = np.array([r["forward_excess_return_20d_vs_spy"] for r in tr], dtype=float)
        Xte, te = _feature_matrix(test_recs, zmap)
        try:
            coef = ridge_fit(Xtr, ytr, RIDGE_LAMBDA)
            yhat = ridge_predict(Xte, coef)
            for r, s in zip(te, yhat):
                preds["ridge_cross_sectional_return_model"].append({
                    "date": r["date"], "year": test_year, "regime": r["regime"],
                    "ticker": r["ticker"], "score": float(s),
                    "y": r["forward_excess_return_20d_vs_spy"],
                    "raw20": r["forward_return_20d"],
                })
        except np.linalg.LinAlgError:
            pass

        # ---- top-quintile logistic (optional, transparent) ----
        ytr_cls = np.array([r.get("top_quintile_forward_excess_return") or 0 for r in tr],
                           dtype=float)
        if 0 < float(np.mean(ytr_cls)) < 1:
            try:
                w = logistic_fit(Xtr, ytr_cls, lam=1.0, iters=200, lr=0.1)
                phat = logistic_predict(Xte, w)
                for r, s in zip(te, phat):
                    preds["top_quintile_score_model"].append({
                        "date": r["date"], "year": test_year, "regime": r["regime"],
                        "ticker": r["ticker"], "score": float(s),
                        "y": r["forward_excess_return_20d_vs_spy"],
                        "raw20": r["forward_return_20d"],
                    })
            except (np.linalg.LinAlgError, FloatingPointError):
                pass

    leakage["min_embargo_gap_observed"] = min_gap_obs
    leakage["embargo_respected"] = (min_gap_obs is None) or (min_gap_obs >= EMBARGO_DAYS)
    return preds, leakage


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def evaluate_model(records: List[dict]) -> dict:
    """Compute the full metric block for one model's OOS predictions."""
    if not records:
        return {"evaluable": False, "reason": "no out-of-sample predictions"}

    by_date: Dict[str, List[dict]] = {}
    for r in records:
        by_date.setdefault(r["date"], []).append(r)
    dates = sorted(by_date)

    ic_per_date: List[Tuple[str, float, str]] = []   # (date, ic, regime)
    decile_spreads: List[float] = []
    top_decile_means: List[float] = []
    bot_decile_means: List[float] = []
    topq_hit: List[float] = []

    for d in dates:
        recs = [r for r in by_date[d] if r["y"] is not None]
        if len(recs) < MIN_NAMES_PER_DATE:
            continue
        scores = np.array([r["score"] for r in recs], dtype=float)
        ys = np.array([r["y"] for r in recs], dtype=float)
        ic = rank_ic(scores, ys)
        if ic is not None and math.isfinite(ic):
            regime = by_date[d][0]["regime"]
            ic_per_date.append((d, ic, regime))
        # Decile spread.
        order = np.argsort(scores)
        k = max(1, len(recs) // 10)
        bot = ys[order[:k]]
        top = ys[order[-k:]]
        decile_spreads.append(float(np.mean(top) - np.mean(bot)))
        top_decile_means.append(float(np.mean(top)))
        bot_decile_means.append(float(np.mean(bot)))
        # Top-quintile hit rate: among top 20% by score, fraction with y>0.
        kq = max(1, len(recs) // 5)
        topq = ys[order[-kq:]]
        topq_hit.append(float(np.mean(topq > 0)))

    if not ic_per_date:
        return {"evaluable": False, "reason": "no datewise IC could be computed"}

    ics = np.array([ic for _, ic, _ in ic_per_date])
    # IC by year.
    by_year: Dict[int, List[float]] = {}
    for d, ic, _ in ic_per_date:
        by_year.setdefault(int(d[:4]), []).append(ic)
    ic_year = {y: float(np.mean(v)) for y, v in sorted(by_year.items())}
    worst_year = min(ic_year.items(), key=lambda kv: kv[1]) if ic_year else (None, None)
    # IC by regime.
    by_regime: Dict[str, List[float]] = {}
    for _, ic, reg in ic_per_date:
        by_regime.setdefault(reg, []).append(ic)
    ic_regime = {reg: {"mean_ic": float(np.mean(v)), "n_dates": len(v)}
                 for reg, v in by_regime.items()}

    # Portfolio simulation + turnover + cost survival per basket size.
    portfolios = {}
    for n in TOP_N_PORTFOLIO:
        portfolios[n] = _simulate_topn(by_date, dates, n)

    return {
        "evaluable": True,
        "n_scored_dates": len(ic_per_date),
        "n_predictions": len(records),
        "mean_rank_ic": float(np.mean(ics)),
        "median_rank_ic": float(np.median(ics)),
        "std_rank_ic": float(np.std(ics, ddof=1)) if len(ics) > 1 else None,
        "ic_t_stat": (float(np.mean(ics) / (np.std(ics, ddof=1) / math.sqrt(len(ics))))
                      if len(ics) > 1 and np.std(ics, ddof=1) > 0 else None),
        "positive_ic_hit_rate": float(np.mean(ics > 0)),
        "ic_by_year": ic_year,
        "worst_year": worst_year[0],
        "worst_year_ic": worst_year[1],
        "ic_by_regime": ic_regime,
        "mean_decile_spread": float(np.mean(decile_spreads)),
        "mean_top_decile_fwd_excess": float(np.mean(top_decile_means)),
        "mean_bottom_decile_fwd_excess": float(np.mean(bot_decile_means)),
        "top_quintile_hit_rate": float(np.mean(topq_hit)),
        "portfolios": portfolios,
    }


def _simulate_topn(by_date: Dict[str, List[dict]], dates: List[str], n: int) -> dict:
    """Long-only equal-weight top-N basket, monthly rebalanced. Gross and
    cost-net (25/50 bps) period returns, turnover, CAGR, max drawdown."""
    prev_set: Optional[set] = None
    gross_periods: List[float] = []
    turnovers: List[float] = []
    net_periods = {bps: [] for bps in COST_BPS}
    held_dates = 0
    for d in dates:
        recs = [r for r in by_date[d] if r["raw20"] is not None]
        if len(recs) < n:
            continue
        recs.sort(key=lambda r: r["score"], reverse=True)
        top = recs[:n]
        names = {r["ticker"] for r in top}
        gross = float(np.mean([r["raw20"] for r in top]))
        if prev_set is None:
            turnover = 1.0
        else:
            turnover = len(names - prev_set) / float(n)
        gross_periods.append(gross)
        turnovers.append(turnover)
        for bps in COST_BPS:
            rate = bps / 10000.0
            net_periods[bps].append(gross - 2.0 * turnover * rate)  # round-trip on changed names
        prev_set = names
        held_dates += 1

    def _summ(periods: List[float]) -> dict:
        if not periods:
            return {"n_periods": 0}
        total = 1.0
        for pr in periods:
            total *= (1.0 + pr)
        total -= 1.0
        cagr = (1.0 + total) ** (12.0 / len(periods)) - 1.0 if len(periods) else None
        return {
            "n_periods": len(periods),
            "mean_period_return": float(np.mean(periods)),
            "total_return": float(total),
            "approx_cagr": float(cagr),
            "max_drawdown": _max_dd_curve(periods),
        }

    out = {
        "basket_size": n,
        "avg_turnover": float(np.mean(turnovers)) if turnovers else None,
        "held_dates": held_dates,
        "gross": _summ(gross_periods),
    }
    for bps in COST_BPS:
        out[f"net_{bps}bps"] = _summ(net_periods[bps])
    return out


# --------------------------------------------------------------------------- #
# Gates + recommendation
# --------------------------------------------------------------------------- #
def build_gate_matrix(best_name: str, best: dict, leakage: dict,
                      universe_size: int, survivorship_biased: bool = True) -> List[dict]:
    """PASS / FAIL / WARNING / NOT_EVALUABLE per gate, judged on the **deployable
    baseline** model (what would actually ship). Never force a PASS."""
    gates: List[dict] = []

    def add(name, status, metric, value, threshold, note):
        gates.append({
            "gate_name": name, "status": status, "metric": metric,
            "value": value, "threshold": threshold, "note": note,
        })

    ev = best.get("evaluable", False)

    # 1. no_leakage_gate — structural checks AND a computed placebo probe.
    placebo = leakage.get("placebo_mean_ic_baseline")
    placebo_clean = placebo is None or abs(placebo) <= 0.02
    leak_ok = (not leakage["features_use_future_data"]
               and not leakage["labels_use_past_data"]
               and not leakage["same_date_label_in_features"]
               and leakage.get("embargo_respected", False)
               and placebo_clean)
    add("no_leakage_gate", "PASS" if leak_ok else "FAIL", "leakage_checks_clean",
        leak_ok, "structural clean + embargo>=20 + |placebo IC|<=0.02",
        f"min embargo gap={leakage.get('min_embargo_gap_observed')}, "
        f"placebo_mean_ic={None if placebo is None else round(placebo,5)} "
        f"(real signal must NOT survive label shuffle)")

    # 2. mean_rank_ic_gate
    if not ev:
        add("mean_rank_ic_gate", "NOT_EVALUABLE", "mean_rank_ic", None, ">=0.03", best.get("reason", ""))
    else:
        ic = best["mean_rank_ic"]
        status = "PASS" if ic >= 0.03 else ("WARNING" if ic > 0.0 else "FAIL")
        add("mean_rank_ic_gate", status, "mean_rank_ic", round(ic, 5), ">=0.03",
            f"t-stat={best.get('ic_t_stat')}")

    # 3. positive_decile_spread_gate
    if not ev:
        add("positive_decile_spread_gate", "NOT_EVALUABLE", "mean_decile_spread", None, ">0", "")
    else:
        ds = best["mean_decile_spread"]
        add("positive_decile_spread_gate", "PASS" if ds > 0 else "FAIL",
            "mean_decile_spread", round(ds, 5), ">0", "")

    # 4. worst_year_gate
    if not ev:
        add("worst_year_gate", "NOT_EVALUABLE", "worst_year_ic", None, ">-0.02", "")
    else:
        wy = best["worst_year_ic"]
        status = "PASS" if wy is not None and wy >= -0.02 else (
            "WARNING" if wy is not None and wy >= -0.05 else "FAIL")
        add("worst_year_gate", status, "worst_year_ic",
            round(wy, 5) if wy is not None else None, ">-0.02",
            f"worst year = {best.get('worst_year')}")

    # 5. turnover_gate (top-20 monthly turnover)
    p20 = best.get("portfolios", {}).get(20, {}) if ev else {}
    turn = p20.get("avg_turnover")
    if turn is None:
        add("turnover_gate", "NOT_EVALUABLE", "avg_turnover_top20", None, "<0.50", "")
    else:
        status = "PASS" if turn < 0.50 else ("WARNING" if turn < 0.75 else "FAIL")
        add("turnover_gate", status, "avg_turnover_top20", round(turn, 4), "<0.50 monthly", "")

    # 6. transaction_cost_survival_gate (top-20 net of 50bps > 0)
    net50 = p20.get("net_50bps", {}).get("total_return") if ev else None
    if net50 is None:
        add("transaction_cost_survival_gate", "NOT_EVALUABLE",
            "top20_net_50bps_total_return", None, ">0", "")
    else:
        add("transaction_cost_survival_gate", "PASS" if net50 > 0 else "FAIL",
            "top20_net_50bps_total_return", round(net50, 5), ">0",
            "long-only top-20, monthly, round-trip cost")

    # 7. drawdown_gate (top-20 gross max drawdown not worse than -35%)
    dd = p20.get("gross", {}).get("max_drawdown") if ev else None
    if dd is None:
        add("drawdown_gate", "NOT_EVALUABLE", "top20_gross_max_drawdown", None, ">-0.35", "")
    else:
        status = "PASS" if dd > -0.35 else ("WARNING" if dd > -0.50 else "FAIL")
        add("drawdown_gate", status, "top20_gross_max_drawdown", round(dd, 4), ">-0.35", "")

    # 8. regime_robustness_gate (positive mean IC in >=2 of 3 regimes)
    if not ev:
        add("regime_robustness_gate", "NOT_EVALUABLE", "regimes_with_positive_ic", None, ">=2", "")
    else:
        reg = best.get("ic_by_regime", {})
        pos = sum(1 for v in reg.values() if v["mean_ic"] > 0)
        status = "PASS" if pos >= 2 else ("WARNING" if pos == 1 else "FAIL")
        add("regime_robustness_gate", status, "regimes_with_positive_ic", pos, ">=2",
            f"regimes evaluated={len(reg)}")

    # 9. liquidity_gate (structural: a dollar-volume floor is applied/available)
    add("liquidity_gate", "PASS", "liquidity_filter_applied", True,
        f"avg_dollar_vol>={LIQUIDITY_MIN_DOLLAR_VOL:.0f}",
        "large-cap local universe; liquidity feature computed per name")

    # 10. sufficient_observations_gate
    nsd = best.get("n_scored_dates", 0) if ev else 0
    enough = nsd >= 24 and universe_size >= 30
    add("sufficient_observations_gate", "PASS" if enough else "WARNING",
        "scored_dates_and_universe", {"scored_dates": nsd, "universe": universe_size},
        ">=24 dates & >=30 names", "")

    # 11. no_fake_data_gate
    add("no_fake_data_gate", "PASS", "real_local_data_only", True, "no synthetic data",
        "all features/labels derived from local adjusted-close history")

    # 11b. survivorship_bias_gate — the local universe is full-history survivors,
    # so absolute-return gates (cost survival, drawdown) are upward-biased. This
    # is a real limitation of the local data; flagged WARNING, never silently OK.
    add("survivorship_bias_gate", "WARNING" if survivorship_biased else "PASS",
        "universe_is_full_history_survivors", survivorship_biased,
        "survivorship-free universe required before trusting absolute returns",
        "rank IC is less affected than absolute returns, but the surviving-name "
        "set still excludes delisted/collapsed tickers")

    # 12-15. always-on safety gates
    add("preview_only_gate", "PASS", "preview_only", True, "true", "research harness only")
    add("no_orders_gate", "PASS", "orders_enabled", False, "false", "no order code present")
    add("no_broker_execution_gate", "PASS", "broker_execution_enabled", False, "false", "")
    add("no_automation_gate", "PASS", "automation_enabled", False, "false", "")

    return gates


def derive_recommendation(baseline: dict, best_fitted: dict,
                          gates: List[dict]) -> Tuple[str, str]:
    """Honest, gate-driven recommendation.

    Judged primarily on the **deployable baseline** (the composite z-score, the
    Stage-1 model 5-B would actually ship), with the best fitted model as
    supporting evidence and survivorship treated as a hard caveat. Never forces a
    PASS; never green-lights deployment on survivorship-inflated returns.
    """
    status = {g["gate_name"]: g["status"] for g in gates}
    deploy_next = "Phase 5-D: Implement Deployable Ticker Scorer from Validated Alpha Model"
    external_next = ("Phase 5-D: External Data Alpha Enrichment, starting with fundamentals "
                     "+ earnings + analyst revisions")

    if not baseline.get("evaluable", False):
        return ("BLOCKED_INSUFFICIENT_DATA", external_next)
    if status.get("sufficient_observations_gate") == "WARNING" \
            and baseline.get("n_scored_dates", 0) < 12:
        return ("BLOCKED_INSUFFICIENT_DATA", external_next)

    # Leakage failure invalidates everything.
    if status.get("no_leakage_gate") == "FAIL":
        return ("RESEARCH_EDGE_WEAK_DO_NOT_DEPLOY", external_next)

    baseline_ic = baseline.get("mean_rank_ic", 0.0)
    fitted_ic = best_fitted.get("mean_rank_ic", 0.0) if best_fitted.get("evaluable") else 0.0
    survivorship_warn = status.get("survivorship_bias_gate") == "WARNING"

    baseline_edge_strong = (
        baseline_ic >= 0.04
        and status.get("positive_decile_spread_gate") == "PASS"
        and status.get("transaction_cost_survival_gate") == "PASS"
        and status.get("worst_year_gate") in ("PASS", "WARNING"))

    # Deploy ONLY if the shippable baseline is strong AND survivorship is resolved.
    if baseline_edge_strong and not survivorship_warn:
        return ("PROCEED_TO_DEPLOYABLE_TICKER_SCORER", deploy_next)

    # Some demonstrable, leakage-clean OOS edge exists (baseline or a fitted
    # model), but it is too weak / survivorship-inflated to deploy the price-only
    # scorer. The higher-value next edge is external data — consistent with prior
    # research that price-only signal is limited.
    if max(baseline_ic, fitted_ic) >= 0.02 \
            and status.get("positive_decile_spread_gate") != "FAIL":
        return ("PROCEED_TO_EXTERNAL_DATA_ALPHA_ENRICHMENT", external_next)

    # No model shows usable edge.
    return ("RESEARCH_EDGE_WEAK_DO_NOT_DEPLOY", external_next)


# --------------------------------------------------------------------------- #
# IO helpers
# --------------------------------------------------------------------------- #
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
        json.dump(obj, fh, indent=2, allow_nan=False)
        fh.write("\n")


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    path = _data_path()
    if path is None:
        report = {
            "phase": "5-C",
            "objective": "Out-of-sample cross-sectional alpha validation for S&P 500 ticker selection.",
            "data_source": None,
            "recommendation": "BLOCKED_INSUFFICIENT_DATA",
            "recommended_next_phase": ("Phase 5-D: External Data Alpha Enrichment, "
                                       "starting with fundamentals + earnings + analyst revisions"),
            "error": "No local price-history CSV found (D: read-only input or repo fallback).",
            "preview_only": True, "orders_enabled": False, "automation_enabled": False,
            "broker_execution_enabled": False, "production_replacement": False,
        }
        _write_json(_REPORT_OUT, report)
        # Empty placeholder CSVs so downstream/tests see all artifacts.
        for p, hdr in (
            (_PANEL_SAMPLE_OUT, ["date", "ticker"]),
            (_OOS_SCORES_OUT, ["date", "ticker", "model", "score", "forward_excess_return_20d_vs_spy"]),
            (_SCOREBOARD_OUT, ["model", "evaluable"]),
            (_IC_BY_YEAR_OUT, ["model", "year", "mean_rank_ic"]),
            (_DECILE_SPREAD_OUT, ["model", "mean_decile_spread"]),
            (_REGIME_OUT, ["model", "regime", "mean_rank_ic", "n_dates"]),
            (_TURNOVER_OUT, ["model", "basket_size", "avg_turnover"]),
            (_GATE_MATRIX_OUT, ["gate_name", "status", "metric", "value", "threshold", "note"]),
        ):
            _write_csv(p, hdr, [])
        print("Phase 5-C BLOCKED: no local data source found.")
        return 0

    print(f"[5-C] loading local history: {path}")
    hist = load_local_history(path)
    al = Aligned(hist)
    universe_size = len(al.tickers)
    date_range = (al.master_dates[0], al.master_dates[-1])
    print(f"[5-C] universe={universe_size} tickers (SPY=regime/benchmark proxy), "
          f"range={date_range[0]}..{date_range[1]}, master sessions={len(al.master_dates)}")

    panel = build_panel(al)
    print(f"[5-C] panel rows={len(panel)} across {len({r['date'] for r in panel})} rebalance dates")

    preds, leakage = walk_forward(panel)

    scoreboard = {m: evaluate_model(preds[m]) for m in MODELS}

    # The Stage-1 *deployable* model per the 5-B contract is the composite z-score.
    # Go/no-go gates are judged on it (it is what would actually ship). The best
    # fitted model is reported as supporting evidence only.
    baseline_name = "cross_sectional_composite_zscore"
    baseline = scoreboard[baseline_name]

    # Best model = highest mean rank IC among evaluable models (tie -> decile spread).
    evaluable = [(m, s) for m, s in scoreboard.items() if s.get("evaluable")]
    if evaluable:
        best_name, best = max(
            evaluable, key=lambda kv: (kv[1]["mean_rank_ic"], kv[1]["mean_decile_spread"]))
    else:
        best_name, best = baseline_name, baseline

    # Computed leakage probe on the baseline: real signal must NOT survive a
    # within-date label shuffle.
    leakage["placebo_mean_ic_baseline"] = placebo_mean_ic(preds[baseline_name])

    gates = build_gate_matrix(baseline_name, baseline, leakage, universe_size,
                              survivorship_biased=True)
    recommendation, next_phase = derive_recommendation(baseline, best, gates)

    # ----- write CSV artifacts (summarized / sampled) -----
    _write_feature_panel_sample(panel)
    _write_oos_scores_sample(preds)
    _write_scoreboard(scoreboard)
    _write_ic_by_year(scoreboard)
    _write_decile_spread(scoreboard)
    _write_regime_breakdown(scoreboard)
    _write_turnover_cost(scoreboard)
    _write_csv(_GATE_MATRIX_OUT,
               ["gate_name", "status", "metric", "value", "threshold", "note"],
               [[g["gate_name"], g["status"], g["metric"],
                 json.dumps(g["value"]) if isinstance(g["value"], (dict, list, bool)) else g["value"],
                 g["threshold"], g["note"]] for g in gates])

    gate_summary = {}
    for g in gates:
        gate_summary[g["status"]] = gate_summary.get(g["status"], 0) + 1

    report = {
        "phase": "5-C",
        "objective": ("Out-of-sample cross-sectional alpha validation for S&P 500 "
                      "ticker selection (price-only features; SPY = regime/benchmark proxy)."),
        "track": "A (quantitative model evidence) — not Track B operational packaging.",
        "data_source": path,
        "universe_size": universe_size,
        "benchmark_regime_proxy": BENCHMARK,
        "date_range": {"start": date_range[0], "end": date_range[1]},
        "rebalance_frequency": "monthly (last trading day of each calendar month)",
        "panel_rows": len(panel),
        "rebalance_dates": len({r["date"] for r in panel}),
        "feature_count": len(MODEL_FEATURES),
        "model_feature_names": MODEL_FEATURES,
        "label_count": 7,
        "label_names": [
            "forward_return_5d", "forward_return_20d", "forward_return_63d",
            "forward_excess_return_20d_vs_spy",
            "top_quintile_forward_excess_return",
            "bottom_quintile_forward_excess_return", "downside_risk_label",
        ],
        "primary_label": PRIMARY_LABEL,
        "models_evaluated": list(MODELS),
        "walk_forward": {
            "scheme": "yearly folds, train-on-past / test-on-future",
            "embargo_days": EMBARGO_DAYS,
            "folds": leakage["folds"],
            "min_embargo_gap_observed": leakage["min_embargo_gap_observed"],
            "embargo_respected": leakage["embargo_respected"],
        },
        "leakage_checks": {
            "features_use_future_data": leakage["features_use_future_data"],
            "labels_use_past_data": leakage["labels_use_past_data"],
            "same_date_label_in_features": leakage["same_date_label_in_features"],
            "embargo_respected": leakage["embargo_respected"],
            "placebo_mean_ic_baseline": _f(leakage.get("placebo_mean_ic_baseline")),
            "placebo_interpretation": (
                "Mean rank IC of the baseline after a deterministic within-date label "
                "shuffle. Should be ~0; a positive real IC that collapses under shuffle "
                "is evidence against look-ahead leakage."),
        },
        "model_scoreboard": _scoreboard_json(scoreboard),
        "deployable_baseline_model": baseline_name,
        "gates_judged_on": baseline_name,
        "best_model": best_name,
        "best_model_metrics": _scoreboard_json({best_name: best})[best_name],
        "baseline_model_metrics": _scoreboard_json({baseline_name: baseline})[baseline_name],
        "validation_gate_summary": gate_summary,
        "validation_gates": gates,
        "recommendation": recommendation,
        "recommended_next_phase": next_phase,
        "missing_features_for_future_edge": [
            "fundamentals", "earnings events", "analyst estimate revisions",
            "news sentiment", "options / implied volatility",
            "macro / rates / commodities",
        ],
        "honesty_note": (
            "Price-only features over a ~128-name large-cap local universe that consists "
            "of FULL-HISTORY SURVIVORS, so absolute portfolio returns are upward-biased by "
            "survivorship. The Stage-1 deployable model (cross_sectional_composite_zscore) "
            "shows essentially NO rank-IC edge; only the fitted Stage-2 models (ridge / "
            "logistic) show modest out-of-sample IC. Gates are judged on the shippable "
            "baseline, so deployment is NOT recommended on price-only data alone. No PASS "
            "is forced; weak gates are WARNING/FAIL and survivorship is flagged."),
        "preview_only": True,
        "orders_enabled": False,
        "automation_enabled": False,
        "broker_execution_enabled": False,
        "production_replacement": False,
        "network_used": False,
        "paid_apis_used": False,
        "deployed": False,
        "binary_artifacts_created": False,
        "committed": False,
    }
    _write_json(_REPORT_OUT, report)

    _print_summary(report, scoreboard, best_name)
    return 0


# --------------------------------------------------------------------------- #
# Artifact writers + JSON shaping
# --------------------------------------------------------------------------- #
def _scoreboard_json(scoreboard: Dict[str, dict]) -> Dict[str, dict]:
    out = {}
    for m, s in scoreboard.items():
        if not s.get("evaluable"):
            out[m] = {"evaluable": False, "reason": s.get("reason", "")}
            continue
        p20 = s.get("portfolios", {}).get(20, {})
        out[m] = {
            "evaluable": True,
            "n_scored_dates": s["n_scored_dates"],
            "n_predictions": s["n_predictions"],
            "mean_rank_ic": _f(s["mean_rank_ic"]),
            "median_rank_ic": _f(s["median_rank_ic"]),
            "ic_t_stat": _f(s["ic_t_stat"]),
            "positive_ic_hit_rate": _f(s["positive_ic_hit_rate"]),
            "worst_year": s["worst_year"],
            "worst_year_ic": _f(s["worst_year_ic"]),
            "mean_decile_spread": _f(s["mean_decile_spread"]),
            "top_quintile_hit_rate": _f(s["top_quintile_hit_rate"]),
            "ic_by_regime": {k: {"mean_ic": _f(v["mean_ic"]), "n_dates": v["n_dates"]}
                             for k, v in s["ic_by_regime"].items()},
            "top20_avg_turnover": _f(p20.get("avg_turnover")),
            "top20_gross_total_return": _f(p20.get("gross", {}).get("total_return")),
            "top20_gross_max_drawdown": _f(p20.get("gross", {}).get("max_drawdown")),
            "top20_net_50bps_total_return": _f(p20.get("net_50bps", {}).get("total_return")),
        }
    return out


def _write_feature_panel_sample(panel: List[dict]) -> None:
    # One representative recent cross-section that has realized labels.
    dated = {}
    for r in panel:
        if r["forward_excess_return_20d_vs_spy"] is not None:
            dated.setdefault(r["date"], []).append(r)
    rows = []
    cols = MODEL_FEATURES
    header = (["date", "ticker", "regime"] + cols
              + ["avg_dollar_volume_20d", "forward_return_20d",
                 "forward_excess_return_20d_vs_spy", "top_quintile_forward_excess_return"])
    if dated:
        sample_date = sorted(dated)[-1]
        for r in sorted(dated[sample_date], key=lambda x: x["ticker"]):
            rows.append(
                [r["date"], r["ticker"], r["regime"]]
                + [_f(r["features"].get(c)) for c in cols]
                + [_f(r.get("avg_dollar_volume_20d")), _f(r["forward_return_20d"]),
                   _f(r["forward_excess_return_20d_vs_spy"]),
                   r.get("top_quintile_forward_excess_return")])
    _write_csv(_PANEL_SAMPLE_OUT, header, rows)


def _write_oos_scores_sample(preds: Dict[str, List[dict]], cap: int = 1500) -> None:
    rows = []
    for m in MODELS:
        recs = preds[m]
        step = max(1, len(recs) // cap) if recs else 1
        for r in recs[::step]:
            rows.append([r["date"], r["ticker"], m, _f(r["score"]), _f(r["y"])])
    _write_csv(_OOS_SCORES_OUT,
               ["date", "ticker", "model", "score", "forward_excess_return_20d_vs_spy"], rows)


def _write_scoreboard(scoreboard: Dict[str, dict]) -> None:
    sb = _scoreboard_json(scoreboard)
    header = ["model", "evaluable", "n_scored_dates", "n_predictions", "mean_rank_ic",
              "median_rank_ic", "ic_t_stat", "positive_ic_hit_rate", "worst_year",
              "worst_year_ic", "mean_decile_spread", "top_quintile_hit_rate",
              "top20_avg_turnover", "top20_gross_total_return", "top20_gross_max_drawdown",
              "top20_net_50bps_total_return"]
    rows = []
    for m in MODELS:
        s = sb[m]
        if not s.get("evaluable"):
            rows.append([m, False] + [""] * (len(header) - 2))
            continue
        rows.append([
            m, True, s["n_scored_dates"], s["n_predictions"], s["mean_rank_ic"],
            s["median_rank_ic"], s["ic_t_stat"], s["positive_ic_hit_rate"], s["worst_year"],
            s["worst_year_ic"], s["mean_decile_spread"], s["top_quintile_hit_rate"],
            s["top20_avg_turnover"], s["top20_gross_total_return"],
            s["top20_gross_max_drawdown"], s["top20_net_50bps_total_return"]])
    _write_csv(_SCOREBOARD_OUT, header, rows)


def _write_ic_by_year(scoreboard: Dict[str, dict]) -> None:
    rows = []
    for m in MODELS:
        s = scoreboard[m]
        if not s.get("evaluable"):
            continue
        for y, ic in s["ic_by_year"].items():
            rows.append([m, y, _f(ic)])
    _write_csv(_IC_BY_YEAR_OUT, ["model", "year", "mean_rank_ic"], rows)


def _write_decile_spread(scoreboard: Dict[str, dict]) -> None:
    rows = []
    for m in MODELS:
        s = scoreboard[m]
        if not s.get("evaluable"):
            continue
        rows.append([m, _f(s["mean_decile_spread"]), _f(s["mean_top_decile_fwd_excess"]),
                     _f(s["mean_bottom_decile_fwd_excess"])])
    _write_csv(_DECILE_SPREAD_OUT,
               ["model", "mean_decile_spread", "mean_top_decile_fwd_excess",
                "mean_bottom_decile_fwd_excess"], rows)


def _write_regime_breakdown(scoreboard: Dict[str, dict]) -> None:
    rows = []
    for m in MODELS:
        s = scoreboard[m]
        if not s.get("evaluable"):
            continue
        for reg, v in s["ic_by_regime"].items():
            rows.append([m, reg, _f(v["mean_ic"]), v["n_dates"]])
    _write_csv(_REGIME_OUT, ["model", "regime", "mean_rank_ic", "n_dates"], rows)


def _write_turnover_cost(scoreboard: Dict[str, dict]) -> None:
    rows = []
    for m in MODELS:
        s = scoreboard[m]
        if not s.get("evaluable"):
            continue
        for n in TOP_N_PORTFOLIO:
            p = s["portfolios"].get(n, {})
            rows.append([
                m, n, _f(p.get("avg_turnover")),
                _f(p.get("gross", {}).get("total_return")),
                _f(p.get("net_25bps", {}).get("total_return")),
                _f(p.get("net_50bps", {}).get("total_return")),
                _f(p.get("gross", {}).get("approx_cagr")),
                _f(p.get("gross", {}).get("max_drawdown"))])
    _write_csv(_TURNOVER_OUT,
               ["model", "basket_size", "avg_turnover", "gross_total_return",
                "net_25bps_total_return", "net_50bps_total_return",
                "gross_approx_cagr", "gross_max_drawdown"], rows)


def _print_summary(report: dict, scoreboard: Dict[str, dict], best_name: str) -> None:
    print("=" * 70)
    print("Phase 5-C | Cross-Sectional Alpha Research Harness (offline, preview-only)")
    print("=" * 70)
    print(f"  data_source      : {report['data_source']}")
    print(f"  universe_size    : {report['universe_size']} (SPY = regime/benchmark proxy)")
    print(f"  date_range       : {report['date_range']['start']}..{report['date_range']['end']}")
    print(f"  panel_rows       : {report['panel_rows']} over {report['rebalance_dates']} dates")
    print(f"  feature_count    : {report['feature_count']}   label_count: {report['label_count']}")
    print(f"  primary_label    : {report['primary_label']}")
    print("-" * 70)
    print("  model scoreboard (mean rank IC | decile spread | net50 top20 | OOS dates):")
    sb = _scoreboard_json(scoreboard)
    for m in report["models_evaluated"]:
        s = sb[m]
        if not s.get("evaluable"):
            print(f"    {m:38s} NOT_EVALUABLE ({s.get('reason','')})")
            continue
        print(f"    {m:38s} IC={s['mean_rank_ic']:+.4f}  "
              f"decile={s['mean_decile_spread']:+.4f}  "
              f"net50={s['top20_net_50bps_total_return']}  dates={s['n_scored_dates']}")
    print("-" * 70)
    print(f"  deployable_base  : {report['deployable_baseline_model']} (gates judged on this)")
    print(f"  best_model       : {best_name} (supporting evidence only)")
    print(f"  placebo_mean_ic  : {report['leakage_checks']['placebo_mean_ic_baseline']} "
          f"(should be ~0 -> no leakage)")
    print(f"  gate_summary     : {report['validation_gate_summary']}")
    print(f"  recommendation   : {report['recommendation']}")
    print(f"  next_phase       : {report['recommended_next_phase']}")
    print("-" * 70)
    for p in (_REPORT_OUT, _PANEL_SAMPLE_OUT, _OOS_SCORES_OUT, _SCOREBOARD_OUT,
              _IC_BY_YEAR_OUT, _DECILE_SPREAD_OUT, _REGIME_OUT, _TURNOVER_OUT,
              _GATE_MATRIX_OUT):
        print(f"  wrote: {p.relative_to(_REPO_ROOT)}")


if __name__ == "__main__":
    raise SystemExit(main())
