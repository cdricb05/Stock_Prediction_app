"""Phase 6-A — Cross-Asset Macro Context Alpha Pack (offline, point-in-time).

**Track A (quant brain) research only.** Offline. Point-in-time. Walk-forward.
No network, no provider call, no paid API, no API key read or required, no model
deployed, no Paper Trader / GCP work, no orders / broker / automation, no binary
artifact, no commit, no push.

Why this phase exists
---------------------
Phase 5-G2 showed the earnings-surprise / PEAD event composite carries **no**
incremental cross-sectional edge over the Phase 5-C price-only champion once
coverage broadens (NO_INCREMENTAL_EVENT_EDGE). The strategic read was that the
next lever is a **cross-market / macro context** signal, not more single-name
earnings wrappers. This phase builds a leakage-safe cross-asset context feature
pack and asks one question:

    "Does broader market context improve 5-20 day equity selection beyond the
     Phase 5-C price-only champion, on the same dates and universe?"

What it does
------------
  1. **Inventory** every local cross-asset / macro data source (FRED daily/monthly
     series in research/input/, the read-only D: equity price history, and the
     already-collected global ETF readiness from Phase 3-W / 3-Y). It then decides
     whether enough data exists locally to build a fair test of the full
     multi-layer framework, and -- when it does not -- records the exact
     controlled-collection command for the future (it does NOT run collection).
  2. Reuses the Phase 5-C harness UNCHANGED (import) for the price panel, the
     champion price-only reference, the walk-forward embargo logic, ridge, and the
     metric / placebo machinery -- so the comparison is genuinely apples-to-apples.
  3. Builds a strictly-lagged macro factor pack from local FRED series + SPY:
     cross-asset 1d/5d/20d returns, 20d/60d trend & realized vol, 20d/60d z-score
     shocks, rolling 60d beta (sensitivity) of EACH stock to EACH macro factor,
     macro-shock x stock-sensitivity interactions, and regime-state x sensitivity
     interactions. Nothing is hardcoded as "oil up => USD up"; every relationship
     is an estimated, lagged, rolling sensitivity.
  4. Compares, on the SAME walk-forward dates / universe: the Phase 5-C price-only
     champion (reference), a price-only model on the common dates, a macro-only
     model, a price-plus-macro model, and a price-plus-macro-regime-interaction
     model -- by mean rank IC, IC t-stat, IC by year, decile / top-quintile
     spread, turnover proxy, placebo IC, and incremental IC vs the reference.
  5. Emits an honest gate matrix and one of the allowed recommendations. It does
     NOT run a strategy / shadow test, and never forces a PASS.

Leakage safety
--------------
All macro features are evaluated at master index ``i-1`` (strict t-1 lag) for a
rebalance at index ``i``; rolling windows use only past observations; forward
labels are future-only; walk-forward folds keep a >= 20-session embargo. A
deterministic within-date label-permutation placebo probes for look-ahead.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
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

_OUT_DIR_DEFAULT = _REPO_ROOT / "research" / "output" / "phase6a_cross_asset_macro_context_alpha"
_INPUT_DIR_DEFAULT = _REPO_ROOT / "research" / "input"
_PHASE5C_RUNNER = _REPO_ROOT / "research" / "run_phase5c_cross_sectional_alpha_research.py"

PHASE = "6-A"
OBJECTIVE = (
    "Build a leakage-safe cross-asset macro context feature pack and test whether "
    "broader market context improves 5-20 day cross-sectional equity selection "
    "(20d forward excess return vs SPY) beyond the Phase 5-C price-only champion, "
    "on the same walk-forward dates and universe."
)

# Success gate: a macro-augmented model must beat the same-universe Phase 5-C
# reference by at least this much mean-rank-IC and pass leakage/placebo checks.
GATE_INCREMENTAL_IC = 0.005
GATE_PLACEBO_ABS = 0.02
PLACEBO_MATERIAL_FRACTION = 0.5

# Local cross-asset readiness: how many usable global ETF proxies / asset classes
# the full multi-layer framework needs before it can be tested fairly. Mirrors the
# Phase 3-W / 3-X READY rule (>= 12 usable proxies across >= 5 asset classes).
CROSS_ASSET_READY_MIN_PROXIES = 12
CROSS_ASSET_READY_MIN_CLASSES = 5

REC_CONFIRMED = "MACRO_CONTEXT_ALPHA_CONFIRMED"
REC_WEAK = "MACRO_CONTEXT_ALPHA_WEAK"
REC_NEEDS_DATA = "NEEDS_CONTROLLED_CROSS_ASSET_DATA_COLLECTION"
REC_BLOCKED = "DATA_QUALITY_BLOCKED"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (REC_CONFIRMED, REC_WEAK, REC_NEEDS_DATA, REC_BLOCKED, REC_ERROR)

# Macro factors built from local data. "kind" controls the change transform:
#   price -> pct change (oil, dollar, SPY);  yield -> first difference in pp.
# Daily FRED series + SPY (from the equity history) are the only factors with a
# clean daily frequency suitable for rolling beta. CPI / fed funds are monthly and
# enter ONLY as slow regime scalars (no daily beta).
_FACTORS: List[dict] = [
    {"key": "spy", "label": "SPY (equity market)", "source": "equity_history", "kind": "price"},
    {"key": "oil", "label": "WTI crude (DCOILWTICO)", "file": "DCOILWTICO.csv", "col": "DCOILWTICO", "kind": "price"},
    {"key": "usd", "label": "Broad USD index (DTWEXBGS)", "file": "DTWEXBGS.csv", "col": "DTWEXBGS", "kind": "price"},
    {"key": "rate10", "label": "10Y Treasury yield (DGS10)", "file": "fredgraph.csv", "col": "DGS10", "kind": "yield"},
    {"key": "rate2", "label": "2Y Treasury yield (DGS2)", "file": "fredgraph.csv", "col": "DGS2", "kind": "yield"},
    {"key": "curve", "label": "10Y-2Y curve slope", "source": "derived", "kind": "yield"},
]
# Slow monthly macro context (regime scalars only).
_MONTHLY: List[dict] = [
    {"key": "cpi", "label": "CPI level (CPIAUCSL)", "file": "CPIAUCSL.csv", "col": "CPIAUCSL"},
    {"key": "fedfunds", "label": "Fed funds rate (FEDFUNDS)", "file": "FEDFUNDS.csv", "col": "FEDFUNDS"},
]

_BETA_W = 60          # rolling sensitivity window (sessions)
_VOL_W = 60           # rolling factor vol window
_SHORT_W, _MED_W, _LONG_W = 5, 20, 20  # 1d implicit, 5d, 20d return horizons
_ZLOOKBACK = 60       # vol window used to normalize shocks

# Per-stock cross-sectional macro feature groups (these vary across names via beta).
_SENSITIVITY_FEATS = [f"beta_{f['key']}" for f in _FACTORS]
_INTERACTION_FEATS = [f"ix_shock_{f['key']}" for f in _FACTORS] + [f"ix_ret20_{f['key']}" for f in _FACTORS]
_REGIME_FEATS = [
    "rg_risk_x_betaspy",
    "rg_rates_x_beta10",
    "rg_dollar_x_betausd",
    "rg_oil_x_betaoil",
    "rg_infl_x_betaoil",
    "rg_policy_x_beta10",
]
MACRO_BASE_FEATS = _SENSITIVITY_FEATS + _INTERACTION_FEATS
MACRO_ALL_FEATS = MACRO_BASE_FEATS + _REGIME_FEATS

MODELS_COMPARED = [
    "phase5c_reference",
    "price_only",
    "macro_only",
    "price_plus_macro",
    "price_plus_macro_regime_interaction",
]
_AUG_MODELS = ["macro_only", "price_plus_macro", "price_plus_macro_regime_interaction"]


# --------------------------------------------------------------------------- #
# Phase 5-C harness reuse
# --------------------------------------------------------------------------- #
def _load_phase5c(runner_path: Optional[Path] = None):
    path = Path(runner_path) if runner_path else _PHASE5C_RUNNER
    spec = importlib.util.spec_from_file_location("_p5c_for_6a", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import Phase 5-C runner at {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# Output path bundle
# --------------------------------------------------------------------------- #
class _OutPaths:
    def __init__(self, out_dir: Path):
        out_dir.mkdir(parents=True, exist_ok=True)
        self.dir = out_dir
        self.report = out_dir / "phase6a_cross_asset_macro_context_alpha.json"
        self.inventory = out_dir / "cross_asset_data_inventory.csv"
        self.catalog = out_dir / "cross_asset_feature_catalog.csv"
        self.scoreboard = out_dir / "cross_asset_model_scoreboard.csv"
        self.incremental = out_dir / "cross_asset_incremental_edge_report.csv"
        self.gate_matrix = out_dir / "cross_asset_gate_matrix.csv"
        self.yearly = out_dir / "cross_asset_yearly_ic.csv"
        self.next_plan = out_dir / "phase6b_next_plan.json"


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _rel(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(_REPO_ROOT)).replace("/", "\\")
    except Exception:
        return str(p)


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


def _write_json(path: Path, obj) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=False)
        fh.write("\n")


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def _read_fred_series(path: Path, col: str) -> Dict[str, float]:
    """Read a FRED-style CSV (observation_date,<col>[,...]) -> {iso_date: value}.

    Blank / non-finite cells are skipped. Pure local read; no network."""
    out: Dict[str, float] = {}
    if not path.is_file():
        return out
    with open(path, "r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            d = (row.get("observation_date") or row.get("date") or "").strip()[:10]
            if not d:
                continue
            raw = row.get(col)
            try:
                v = float(raw)
            except (TypeError, ValueError):
                continue
            if math.isfinite(v):
                out[d] = v
    return out


def _asof_fill(series: Dict[str, float], master_dates: List[str]) -> np.ndarray:
    """As-of forward-fill a sparse {date: value} onto master_dates: each master
    date carries the most recent observation on-or-before it (NaN before first)."""
    n = len(master_dates)
    arr = np.full(n, np.nan)
    if not series:
        return arr
    obs = sorted(series.items())
    odates = [d for d, _ in obs]
    ovals = [v for _, v in obs]
    j = 0
    last = np.nan
    for i, d in enumerate(master_dates):
        while j < len(odates) and odates[j] <= d:
            last = ovals[j]
            j += 1
        arr[i] = last
    return arr


def _change_array(level: np.ndarray, kind: str) -> np.ndarray:
    """Daily change array: pct change for price factors, first difference for
    yields. Index 0 is NaN."""
    n = level.shape[0]
    chg = np.full(n, np.nan)
    if kind == "yield":
        with np.errstate(invalid="ignore"):
            chg[1:] = level[1:] - level[:-1]
    else:
        with np.errstate(divide="ignore", invalid="ignore"):
            chg[1:] = np.where(level[:-1] > 0, level[1:] / level[:-1] - 1.0, np.nan)
    return chg


def _win_sum(chg: np.ndarray, i: int, w: int) -> Optional[float]:
    if i - w + 1 < 1:
        return None
    win = chg[i - w + 1:i + 1]
    if win.shape[0] < w or np.any(~np.isfinite(win)):
        return None
    return float(np.sum(win))


def _win_std(chg: np.ndarray, i: int, w: int) -> Optional[float]:
    if i - w + 1 < 1:
        return None
    win = chg[i - w + 1:i + 1]
    if win.shape[0] < w or np.any(~np.isfinite(win)):
        return None
    return float(np.std(win, ddof=1))


# --------------------------------------------------------------------------- #
# Macro factor construction
# --------------------------------------------------------------------------- #
class MacroPack:
    """Holds daily change arrays + monthly regime scalars aligned to SPY's
    calendar, plus the per-(factor) time-series features used to build shocks and
    regimes. Every read for a rebalance at index ``i`` uses index ``i-1``."""

    def __init__(self, al, input_dir: Path):
        self.al = al
        self.dates = al.master_dates
        self.n = len(self.dates)
        self.input_dir = input_dir
        self.level: Dict[str, np.ndarray] = {}
        self.chg: Dict[str, np.ndarray] = {}
        self.available: Dict[str, bool] = {}
        self._build_factors()
        self._build_monthly()

    def _build_factors(self) -> None:
        for spec in _FACTORS:
            key = spec["key"]
            if spec.get("source") == "equity_history":
                lvl = self.al.close["SPY"].copy()
            elif spec.get("source") == "derived" and key == "curve":
                lvl = self.level.get("rate10")
                lvl2 = self.level.get("rate2")
                lvl = (lvl - lvl2) if (lvl is not None and lvl2 is not None) else np.full(self.n, np.nan)
            else:
                series = _read_fred_series(self.input_dir / spec["file"], spec["col"])
                lvl = _asof_fill(series, self.dates)
            self.level[key] = lvl
            self.chg[key] = _change_array(lvl, spec["kind"])
            # available if it has a usable trailing window somewhere late in the series.
            self.available[key] = bool(np.isfinite(lvl[-1])) and int(np.sum(np.isfinite(lvl))) >= 252

    def _build_monthly(self) -> None:
        self.monthly_level: Dict[str, np.ndarray] = {}
        self.monthly_chg3m: Dict[str, np.ndarray] = {}
        for spec in _MONTHLY:
            series = _read_fred_series(self.input_dir / spec["file"], spec["col"])
            lvl = _asof_fill(series, self.dates)
            self.monthly_level[spec["key"]] = lvl
            chg = np.full(self.n, np.nan)
            w = 63  # ~3 months of sessions
            for i in range(w, self.n):
                a, b = lvl[i - w], lvl[i]
                if np.isfinite(a) and np.isfinite(b):
                    chg[i] = b - a
            self.monthly_chg3m[spec["key"]] = chg
            self.available[spec["key"]] = int(np.sum(np.isfinite(lvl))) >= 24

    # ---- time-series factor features at strict lag i-1 ----
    def factor_features(self, key: str, i: int) -> Dict[str, Optional[float]]:
        """ret_5d / ret_20d, shock_20d, vol_60d for factor ``key`` as-of i-1."""
        j = i - 1
        chg = self.chg[key]
        if j < 1:
            return {}
        ret5 = _win_sum(chg, j, _SHORT_W)
        ret20 = _win_sum(chg, j, _MED_W)
        sd = _win_std(chg, j, _ZLOOKBACK)
        shock20 = None
        if ret20 is not None and sd is not None and sd > 0:
            shock20 = ret20 / (sd * math.sqrt(_MED_W))
        vol60 = (sd * math.sqrt(252)) if sd is not None else None
        return {"ret5": ret5, "ret20": ret20, "shock20": shock20, "vol60": vol60}

    def beta(self, ticker_ret: np.ndarray, key: str, i: int) -> Optional[float]:
        """Rolling 60d beta of a stock's daily return to factor ``key`` change,
        as-of i-1 (strict lag). Reuses Phase 5-C's _beta math via covariance."""
        return _beta_local(ticker_ret, self.chg[key], i - 1, _BETA_W)

    def regimes(self, i: int) -> Dict[str, float]:
        """Discrete regime states as-of i-1, numeric in {-1,0,+1} (+ oil_shock
        continuous). Risk regime comes from SPY (price/vol)."""
        j = i - 1
        out: Dict[str, float] = {}
        f10 = self.factor_features("rate10", i)
        fusd = self.factor_features("usd", i)
        foil = self.factor_features("oil", i)
        fcurve = self.factor_features("curve", i)
        out["rates"] = _sign(f10.get("shock20"))
        out["dollar"] = _sign(fusd.get("shock20"))
        out["oil"] = _f(foil.get("shock20")) or 0.0
        out["curve"] = _sign(fcurve.get("shock20"))
        # Slow inflation / policy impulse (3-month change of monthly series).
        out["inflation"] = _sign(self.monthly_chg3m["cpi"][j] if j < self.n else None)
        out["policy"] = _sign(self.monthly_chg3m["fedfunds"][j] if j < self.n else None)
        return out


def _beta_local(tret: np.ndarray, fret: np.ndarray, i: int, w: int) -> Optional[float]:
    if i - w + 1 < 1 or i >= tret.shape[0]:
        return None
    a = tret[i - w + 1:i + 1]
    b = fret[i - w + 1:i + 1]
    m = np.isfinite(a) & np.isfinite(b)
    if int(np.sum(m)) < w - 5:
        return None
    a, b = a[m], b[m]
    var = float(np.var(b, ddof=1))
    if not np.isfinite(var) or var <= 0:
        return None
    return float(np.cov(a, b, ddof=1)[0, 1] / var)


def _sign(x) -> float:
    v = _f(x)
    if v is None:
        return 0.0
    return 1.0 if v > 0 else (-1.0 if v < 0 else 0.0)


# --------------------------------------------------------------------------- #
# Attach macro cross-sectional features to the price panel
# --------------------------------------------------------------------------- #
def attach_macro_features(p5c, al, panel: List[dict], macro: MacroPack) -> int:
    """Adds rec['macro_features'] (a {feat: value} dict) to every panel record.
    Returns the count of records that received a usable (non-empty) macro block."""
    # Cache the per-date factor time-series features and regimes (same for all names).
    ts_cache: Dict[int, Dict[str, Dict[str, Optional[float]]]] = {}
    rg_cache: Dict[int, Dict[str, float]] = {}
    n_usable = 0
    for rec in panel:
        i = rec["rebal_idx"]
        if i not in ts_cache:
            ts_cache[i] = {f["key"]: macro.factor_features(f["key"], i) for f in _FACTORS}
            rg_cache[i] = macro.regimes(i)
        ts = ts_cache[i]
        rg = rg_cache[i]
        tret = al.ret[rec["ticker"]]
        feats: Dict[str, float] = {}
        betas: Dict[str, Optional[float]] = {}
        for fspec in _FACTORS:
            key = fspec["key"]
            b = macro.beta(tret, key, i)
            betas[key] = b
            if b is not None:
                feats[f"beta_{key}"] = b
                shock = ts[key].get("shock20")
                ret20 = ts[key].get("ret20")
                if shock is not None:
                    feats[f"ix_shock_{key}"] = b * shock
                if ret20 is not None:
                    feats[f"ix_ret20_{key}"] = b * ret20
        # Regime x sensitivity interactions.
        b_spy = betas.get("spy")
        b_usd = betas.get("usd")
        b_oil = betas.get("oil")
        b_10 = betas.get("rate10")
        risk_num = {"risk_on": 1.0, "neutral": 0.0, "risk_off": -1.0}.get(rec.get("regime"), 0.0)
        if b_spy is not None:
            feats["rg_risk_x_betaspy"] = b_spy * risk_num
        if b_10 is not None:
            feats["rg_rates_x_beta10"] = b_10 * rg.get("rates", 0.0)
            feats["rg_policy_x_beta10"] = b_10 * rg.get("policy", 0.0)
        if b_usd is not None:
            feats["rg_dollar_x_betausd"] = b_usd * rg.get("dollar", 0.0)
        if b_oil is not None:
            feats["rg_oil_x_betaoil"] = b_oil * rg.get("oil", 0.0)
            feats["rg_infl_x_betaoil"] = b_oil * rg.get("inflation", 0.0)
        rec["macro_features"] = feats
        if feats:
            n_usable += 1
    return n_usable


def _standardize_macro(recs: List[dict], feats: Sequence[str], min_names: int) -> Dict[int, Dict[str, float]]:
    """Per-date cross-sectional z-score of each macro feature (no sign flip -- the
    ridge learns direction). Leakage-free: contemporaneous cross-section only."""
    out: Dict[int, Dict[str, float]] = {id(r): {} for r in recs}
    for feat in feats:
        xs = [(r, r.get("macro_features", {}).get(feat)) for r in recs
              if r.get("macro_features", {}).get(feat) is not None]
        if len(xs) < min_names:
            continue
        arr = np.array([v for _, v in xs], dtype=float)
        mu, sd = float(np.mean(arr)), float(np.std(arr))
        if not math.isfinite(sd) or sd <= 0:
            continue
        for r, v in xs:
            out[id(r)][feat] = (v - mu) / sd
    return out


# --------------------------------------------------------------------------- #
# Augmented walk-forward (price / macro feature subsets), reusing 5-C embargo
# --------------------------------------------------------------------------- #
def walk_forward_augmented(p5c, panel: List[dict]) -> Dict[str, List[dict]]:
    """Walk-forward ridge OOS scores for macro_only / price_plus_macro /
    price_plus_macro_regime_interaction. Mirrors Phase 5-C's yearly folds, embargo,
    MIN_TRAIN_ROWS, and standardization conventions exactly."""
    by_date: Dict[str, List[dict]] = {}
    for r in panel:
        by_date.setdefault(r["date"], []).append(r)
    dates = sorted(by_date)
    date_idx = {d: by_date[d][0]["rebal_idx"] for d in dates}
    years = sorted({int(d[:4]) for d in dates})
    label = p5c.PRIMARY_LABEL
    min_names = p5c.MIN_NAMES_PER_DATE

    # Price z-scores (reuse 5-C) and macro z-scores (own), per date.
    price_z: Dict[int, Dict[str, float]] = {}
    macro_z: Dict[int, Dict[str, float]] = {}
    for d in dates:
        price_z.update(p5c._standardize_cross_section(by_date[d]))
        macro_z.update(_standardize_macro(by_date[d], MACRO_ALL_FEATS, min_names))

    def _matrix(recs: List[dict], feats_price: Sequence[str], feats_macro: Sequence[str]):
        rows = []
        for r in recs:
            pz = price_z.get(id(r), {})
            mz = macro_z.get(id(r), {})
            row = [pz.get(f, 0.0) for f in feats_price] + [mz.get(f, 0.0) for f in feats_macro]
            rows.append(row)
        return np.array(rows, dtype=float) if rows else np.zeros((0, len(feats_price) + len(feats_macro)))

    specs = {
        "price_only": (list(p5c.MODEL_FEATURES), []),
        "macro_only": ([], MACRO_BASE_FEATS),
        "price_plus_macro": (list(p5c.MODEL_FEATURES), MACRO_BASE_FEATS),
        "price_plus_macro_regime_interaction": (list(p5c.MODEL_FEATURES), MACRO_ALL_FEATS),
    }
    preds: Dict[str, List[dict]] = {m: [] for m in specs}

    for test_year in years:
        test_dates = [d for d in dates if int(d[:4]) == test_year]
        test_recs = [r for d in test_dates for r in by_date[d] if r[label] is not None]
        if not test_recs:
            continue
        first_test_idx = min(date_idx[d] for d in test_dates)
        train_recs = []
        for d in dates:
            if int(d[:4]) >= test_year:
                continue
            if first_test_idx - (date_idx[d] + p5c.H20) >= p5c.EMBARGO_DAYS:
                train_recs += [r for r in by_date[d] if r[label] is not None]
        if len(train_recs) < p5c.MIN_TRAIN_ROWS:
            continue
        ytr = np.array([r[label] for r in train_recs], dtype=float)
        for model, (fp, fm) in specs.items():
            Xtr = _matrix(train_recs, fp, fm)
            Xte = _matrix(test_recs, fp, fm)
            if Xtr.shape[1] == 0 or Xtr.shape[0] == 0:
                continue
            try:
                coef = p5c.ridge_fit(Xtr, ytr, p5c.RIDGE_LAMBDA)
                yhat = p5c.ridge_predict(Xte, coef)
            except np.linalg.LinAlgError:
                continue
            for r, s in zip(test_recs, yhat):
                preds[model].append({
                    "date": r["date"], "year": test_year, "regime": r["regime"],
                    "ticker": r["ticker"], "score": float(s),
                    "y": r[label], "raw20": r.get("forward_return_20d"),
                })
    return preds


# --------------------------------------------------------------------------- #
# Common-date IC helpers
# --------------------------------------------------------------------------- #
def _mean_ic_on_dates(p5c, preds: List[dict], dates_set) -> Optional[float]:
    by_date: Dict[str, List[dict]] = {}
    for r in preds:
        if r["y"] is not None and r["date"] in dates_set:
            by_date.setdefault(r["date"], []).append(r)
    ics = []
    for d, recs in by_date.items():
        if len(recs) < p5c.MIN_NAMES_PER_DATE:
            continue
        ic = p5c.rank_ic(np.array([r["score"] for r in recs], dtype=float),
                         np.array([r["y"] for r in recs], dtype=float))
        if ic is not None and math.isfinite(ic):
            ics.append(ic)
    return float(np.mean(ics)) if ics else None


def _dates_of(preds: List[dict]) -> set:
    return {r["date"] for r in preds if r["y"] is not None}


# --------------------------------------------------------------------------- #
# Cross-asset readiness inventory (read-only)
# --------------------------------------------------------------------------- #
def build_inventory(input_dir: Path, macro: MacroPack) -> dict:
    """Inventory local cross-asset / macro sources and the global-ETF readiness
    recorded by Phase 3-W / 3-Y (read-only)."""
    rows: List[dict] = []
    for spec in _FACTORS:
        if spec.get("source") in ("equity_history", "derived"):
            src = "equity_history(SPY)" if spec.get("source") == "equity_history" else "derived(rate10-rate2)"
            present = macro.available.get(spec["key"], False)
            n_obs = int(np.sum(np.isfinite(macro.level[spec["key"]])))
        else:
            p = input_dir / spec["file"]
            present = p.is_file()
            n_obs = int(np.sum(np.isfinite(macro.level[spec["key"]])))
            src = _rel(p)
        rows.append({
            "layer": "daily_factor", "factor": spec["key"], "label": spec["label"],
            "source": src, "present": present, "usable_obs": n_obs,
            "supports_rolling_beta": present,
        })
    for spec in _MONTHLY:
        p = input_dir / spec["file"]
        n_obs = int(np.sum(np.isfinite(macro.monthly_level[spec["key"]])))
        rows.append({
            "layer": "monthly_regime", "factor": spec["key"], "label": spec["label"],
            "source": _rel(p), "present": p.is_file(), "usable_obs": n_obs,
            "supports_rolling_beta": False,
        })

    # Global cross-asset ETF readiness from Phase 3-W / 3-Y (read-only).
    out_dir = _REPO_ROOT / "research" / "output"
    etf = {"usable_ticker_count": 0, "asset_classes_covered": 0, "manual_file_count": 0,
           "ready_threshold_met": False, "source_phase": None, "recommendation": None}
    for fname, phase_tag in (("phase3x_manual_global_etf_pack_validation.json", "3-X"),
                             ("phase3w_manual_global_etf_data_import.json", "3-W"),
                             ("phase3y_alphavantage_global_etf_price_collector.json", "3-Y")):
        fp = out_dir / fname
        if not fp.is_file():
            continue
        try:
            with open(fp, "r", encoding="utf-8") as fh:
                j = json.load(fh)
        except (OSError, ValueError):
            continue
        etf["usable_ticker_count"] = int(j.get("usable_ticker_count") or 0)
        etf["asset_classes_covered"] = int(j.get("asset_classes_covered") or 0)
        etf["manual_file_count"] = int(j.get("manual_csv_files_present") or j.get("manual_file_count") or 0)
        etf["ready_threshold_met"] = bool(j.get("ready_threshold_met"))
        etf["source_phase"] = phase_tag
        rec = j.get("recommendation")
        etf["recommendation"] = rec.get("recommendation") if isinstance(rec, dict) else rec
        break

    daily_ok = [r for r in rows if r["layer"] == "daily_factor" and r["present"]]
    cross_asset_ready = (etf["usable_ticker_count"] >= CROSS_ASSET_READY_MIN_PROXIES
                         and etf["asset_classes_covered"] >= CROSS_ASSET_READY_MIN_CLASSES)
    return {
        "rows": rows,
        "daily_factors_available": len(daily_ok),
        "daily_factors_total": len(_FACTORS),
        "monthly_regimes_available": sum(1 for r in rows if r["layer"] == "monthly_regime" and r["present"]),
        "global_etf_readiness": etf,
        "full_framework_cross_asset_ready": cross_asset_ready,
        "missing_layers": _missing_layers(etf),
    }


def _missing_layers(etf: dict) -> List[str]:
    """Layers of the requested multi-layer framework that the LOCAL data cannot
    currently support (the global cross-asset ETF pack is not yet collected)."""
    missing = []
    if etf["usable_ticker_count"] < CROSS_ASSET_READY_MIN_PROXIES:
        missing = [
            "credit_conditions (HYG/LQD spread)",
            "volatility_complex (VIX/VIXY term structure)",
            "equity_market_structure (QQQ/IWM/DIA breadth)",
            "global_regional (EFA/EEM/EWJ/FXI divergence)",
            "sector_rotation (XLE..XLC relative strength)",
            "full_commodity_complex (HG/GC/SI/NG/DBC, only WTI oil available)",
            "fx_complex (FXE/FXY/FXB, only broad USD index available)",
        ]
    return missing


# --------------------------------------------------------------------------- #
# Gate matrix + recommendation
# --------------------------------------------------------------------------- #
def build_gate_matrix(metrics: dict, leakage_clean: bool, inv: dict) -> List[dict]:
    gates: List[dict] = []

    def add(name, status, metric, value, threshold, note=""):
        gates.append({"gate_name": name, "status": status, "metric": metric,
                      "value": value, "threshold": threshold, "note": note})

    ref_ic = metrics.get("phase5c_reference_mean_rank_ic_common")
    best_inc = metrics.get("best_augmented_incremental_ic")
    best_model = metrics.get("best_augmented_model")
    best_placebo = metrics.get("best_augmented_placebo_ic")

    inc_ok = best_inc is not None and best_inc > GATE_INCREMENTAL_IC
    add("incremental_ic_over_reference_gate", "PASS" if inc_ok else "FAIL",
        "incremental_mean_rank_ic", _round(best_inc), f">{GATE_INCREMENTAL_IC}",
        f"best={best_model}; ref common IC={_round(ref_ic)}")

    # Placebo: |placebo| materially below real IC and small in absolute terms.
    best_real = metrics.get("best_augmented_mean_rank_ic")
    placebo_ok = True
    if best_placebo is not None and best_real is not None:
        placebo_ok = (abs(best_placebo) <= GATE_PLACEBO_ABS
                      and abs(best_placebo) < PLACEBO_MATERIAL_FRACTION * abs(best_real))
    add("placebo_materially_lower_gate", "PASS" if placebo_ok else "FAIL",
        "placebo_mean_ic", _round(best_placebo),
        f"|placebo|<{GATE_PLACEBO_ABS} and <{PLACEBO_MATERIAL_FRACTION}x real IC",
        f"best real IC={_round(best_real)}")

    add("leakage_clean_gate", "PASS" if leakage_clean else "FAIL",
        "walk_forward_embargo_respected", leakage_clean, "true",
        "yearly walk-forward with >=20-session embargo; macro features lagged t-1")

    add("same_universe_comparison_gate", "PASS" if metrics.get("same_dates_universe") else "FAIL",
        "all_models_share_common_dates", bool(metrics.get("same_dates_universe")), "true",
        f"common scored dates={metrics.get('common_scored_dates')}")

    add("macro_features_built_gate",
        "PASS" if metrics.get("macro_records_usable", 0) > 0 else "FAIL",
        "records_with_macro_block", metrics.get("macro_records_usable", 0), ">0",
        f"daily factors available={inv['daily_factors_available']}/{inv['daily_factors_total']}")

    add("full_framework_cross_asset_ready_gate",
        "PASS" if inv["full_framework_cross_asset_ready"] else "WARNING",
        "usable_cross_asset_etf_proxies", inv["global_etf_readiness"]["usable_ticker_count"],
        f">={CROSS_ASSET_READY_MIN_PROXIES} across >={CROSS_ASSET_READY_MIN_CLASSES} classes",
        "WARNING (not FAIL): the local FRED+SPY pack tests a PARTIAL framework; "
        "the full multi-layer pack needs the global ETF collection")

    # Safety gates.
    for nm, mv in (("no_orders_gate", "orders_enabled"),
                   ("no_broker_execution_gate", "broker_execution_enabled"),
                   ("no_automation_gate", "automation_enabled"),
                   ("no_network_gate", "network_used"),
                   ("no_paid_api_gate", "paid_apis_used"),
                   ("no_deploy_gate", "deployed"),
                   ("no_strategy_test_gate", "strategy_test_run"),
                   ("no_binary_artifact_gate", "binary_artifacts_created"),
                   ("preview_only_gate", "preview_only")):
        want = True if mv == "preview_only" else False
        add(nm, "PASS", mv, want, str(want).lower(), "")
    return gates


def derive_recommendation(metrics: dict, leakage_clean: bool, inv: dict) -> str:
    if metrics.get("error"):
        return REC_ERROR
    if metrics.get("macro_records_usable", 0) == 0 or not metrics.get("same_dates_universe"):
        return REC_BLOCKED
    best_inc = metrics.get("best_augmented_incremental_ic")
    best_real = metrics.get("best_augmented_mean_rank_ic")
    best_placebo = metrics.get("best_augmented_placebo_ic")
    placebo_ok = True
    if best_placebo is not None and best_real is not None:
        placebo_ok = (abs(best_placebo) <= GATE_PLACEBO_ABS
                      and abs(best_placebo) < PLACEBO_MATERIAL_FRACTION * abs(best_real))
    if best_inc is not None and best_inc > GATE_INCREMENTAL_IC and placebo_ok and leakage_clean:
        return REC_CONFIRMED
    # No confirmation. If the full multi-layer cross-asset universe is not present
    # locally, the honest call is that the framework needs controlled collection to
    # be tested fairly -- the partial FRED+SPY pack alone does not confirm.
    if not inv["full_framework_cross_asset_ready"]:
        return REC_NEEDS_DATA
    return REC_WEAK


def _recommendation_supported(rec: str, metrics: dict, inv: dict) -> bool:
    best_inc = metrics.get("best_augmented_incremental_ic")
    if rec == REC_CONFIRMED:
        return best_inc is not None and best_inc > GATE_INCREMENTAL_IC
    if rec == REC_NEEDS_DATA:
        return (not inv["full_framework_cross_asset_ready"]
                and not (best_inc is not None and best_inc > GATE_INCREMENTAL_IC))
    if rec == REC_WEAK:
        return (inv["full_framework_cross_asset_ready"]
                and not (best_inc is not None and best_inc > GATE_INCREMENTAL_IC))
    if rec == REC_BLOCKED:
        return metrics.get("macro_records_usable", 0) == 0 or not metrics.get("same_dates_universe")
    return rec == REC_ERROR


# --------------------------------------------------------------------------- #
# Artifact writers
# --------------------------------------------------------------------------- #
def _write_inventory(paths: _OutPaths, inv: dict) -> None:
    header = ["layer", "factor", "label", "source", "present", "usable_obs", "supports_rolling_beta"]
    rows = [[r["layer"], r["factor"], r["label"], r["source"], r["present"],
             r["usable_obs"], r["supports_rolling_beta"]] for r in inv["rows"]]
    etf = inv["global_etf_readiness"]
    rows.append(["global_etf_pack", "ALL_22", "cross-asset ETF proxies (SPY..VIXY)",
                 f"phase{etf['source_phase']}", etf["usable_ticker_count"] > 0,
                 etf["usable_ticker_count"], False])
    _write_csv(paths.inventory, header, rows)


def _write_catalog(paths: _OutPaths) -> None:
    header = ["feature_group", "feature", "definition", "cross_sectional", "leakage_safe"]
    rows: List[Sequence] = []
    for f in _FACTORS:
        rows.append(["factor_timeseries", f"{f['key']}_ret_5d",
                     f"5d {'pct' if f['kind']=='price' else 'level-diff'} change of {f['label']}, as-of t-1",
                     "no", "yes"])
        rows.append(["factor_timeseries", f"{f['key']}_ret_20d",
                     f"20d change of {f['label']}, as-of t-1", "no", "yes"])
        rows.append(["factor_timeseries", f"{f['key']}_shock_20d",
                     f"20d change of {f['label']} / (60d daily-vol * sqrt(20)), as-of t-1", "no", "yes"])
        rows.append(["factor_timeseries", f"{f['key']}_vol_60d",
                     f"annualized 60d realized vol of {f['label']} daily change, as-of t-1", "no", "yes"])
    for f in _FACTORS:
        rows.append(["sensitivity", f"beta_{f['key']}",
                     f"rolling 60d beta of stock daily return to {f['label']} change, as-of t-1",
                     "yes", "yes"])
    for f in _FACTORS:
        rows.append(["interaction", f"ix_shock_{f['key']}",
                     f"beta_{f['key']} * {f['key']}_shock_20d (macro shock x stock sensitivity)", "yes", "yes"])
        rows.append(["interaction", f"ix_ret20_{f['key']}",
                     f"beta_{f['key']} * {f['key']}_ret_20d", "yes", "yes"])
    reg_defs = {
        "rg_risk_x_betaspy": "SPY risk regime {risk_on:+1,neutral:0,risk_off:-1} * beta_spy",
        "rg_rates_x_beta10": "rates-up/down regime (sign of 10Y shock) * beta_rate10",
        "rg_dollar_x_betausd": "dollar-up/down regime (sign of USD shock) * beta_usd",
        "rg_oil_x_betaoil": "oil-shock sign * beta_oil",
        "rg_infl_x_betaoil": "inflation impulse (sign of 3m CPI change) * beta_oil",
        "rg_policy_x_beta10": "policy impulse (sign of 3m fed-funds change) * beta_rate10",
    }
    for k, v in reg_defs.items():
        rows.append(["regime_interaction", k, v, "yes", "yes"])
    _write_csv(paths.catalog, header, rows)


def _write_scoreboard(paths: _OutPaths, evals: Dict[str, dict], metrics: dict) -> None:
    header = ["model", "evaluable", "n_scored_dates", "mean_rank_ic", "ic_t_stat",
              "median_rank_ic", "mean_decile_spread", "top_quintile_hit_rate",
              "placebo_mean_ic", "incremental_ic_vs_reference_common"]
    rows = []
    ref = metrics.get("phase5c_reference_mean_rank_ic_common")
    inc_map = metrics.get("incremental_by_model_common", {})
    for m in MODELS_COMPARED:
        e = evals.get(m, {})
        rows.append([
            m, e.get("evaluable", False), e.get("n_scored_dates"),
            _round(e.get("mean_rank_ic")), _round(e.get("ic_t_stat")),
            _round(e.get("median_rank_ic")), _round(e.get("mean_decile_spread")),
            _round(e.get("top_quintile_hit_rate")),
            _round(metrics.get("placebo_by_model", {}).get(m)),
            _round(inc_map.get(m)),
        ])
    _write_csv(paths.scoreboard, header, rows)


def _write_incremental(paths: _OutPaths, metrics: dict) -> None:
    header = ["model", "mean_rank_ic_common", "reference_ic_common",
              "incremental_ic_vs_reference", "clears_gate"]
    ref = metrics.get("phase5c_reference_mean_rank_ic_common")
    common = metrics.get("ic_common_by_model", {})
    inc = metrics.get("incremental_by_model_common", {})
    rows = []
    for m in _AUG_MODELS + ["price_only"]:
        ic = common.get(m)
        i_inc = inc.get(m)
        rows.append([m, _round(ic), _round(ref), _round(i_inc),
                     bool(i_inc is not None and i_inc > GATE_INCREMENTAL_IC)])
    _write_csv(paths.incremental, header, rows)


def _write_gate_matrix(paths: _OutPaths, gates: List[dict]) -> None:
    header = ["gate_name", "status", "metric", "value", "threshold", "note"]
    rows = [[g["gate_name"], g["status"], g["metric"], g["value"], g["threshold"], g["note"]]
            for g in gates]
    _write_csv(paths.gate_matrix, header, rows)


def _write_yearly(paths: _OutPaths, evals: Dict[str, dict]) -> None:
    header = ["model", "year", "mean_rank_ic"]
    rows = []
    for m in MODELS_COMPARED:
        for y, v in sorted((evals.get(m, {}).get("ic_by_year") or {}).items()):
            rows.append([m, y, _round(v)])
    _write_csv(paths.yearly, header, rows)


def _write_next_plan(paths: _OutPaths, rec: str, metrics: dict, inv: dict) -> None:
    confirmed = rec == REC_CONFIRMED
    plan = {
        "phase": "6-B",
        "title": ("Cross-asset macro alpha shadow validation (research-only)" if confirmed
                  else "Controlled cross-asset ETF data collection + full multi-layer macro rerun"),
        "gated_on": (f"price_plus_macro incremental mean rank IC > {GATE_INCREMENTAL_IC} "
                     "over the same-universe Phase 5-C reference, placebo-clean"),
        "macro_context_confirmed_on_local_pack": confirmed,
        "proceed_to_strategy_or_shadow_test": confirmed,
        "best_augmented_model": metrics.get("best_augmented_model"),
        "best_augmented_incremental_ic": _round(metrics.get("best_augmented_incremental_ic")),
        "full_framework_cross_asset_ready": inv["full_framework_cross_asset_ready"],
        "missing_layers_blocking_full_test": inv["missing_layers"],
        "controlled_collection_plan": {
            "note": ("The full multi-layer framework (credit, volatility, equity-structure, "
                     "global/regional, sector rotation, full commodity + FX complex) cannot be "
                     "tested locally: 0 usable global ETF proxies are present. Collection is NOT "
                     "run by this phase."),
            "do_not_run_live_now": True,
            "exact_future_commands": [
                ("$env:ALPHAVANTAGE_API_KEY = \"<key>\"; "
                 "python -B research\\run_phase3y_alphavantage_global_etf_price_collector.py"),
                "python -B research\\run_phase3w_manual_global_etf_data_import.py",
                "python -B research\\run_phase3x_manual_global_etf_pack_validation.py",
            ],
            "ready_threshold": (f">= {CROSS_ASSET_READY_MIN_PROXIES} usable ETF proxies across "
                                f">= {CROSS_ASSET_READY_MIN_CLASSES} asset classes (Phase 3-X READY rule)"),
            "target_universe": ["SPY", "QQQ", "IWM", "EFA", "EEM", "VGK", "EWJ", "FXI", "SHY",
                                "IEF", "TLT", "TIP", "LQD", "HYG", "GLD", "SLV", "DBC", "USO",
                                "UUP", "FXE", "FXY", "VIXY"],
        },
        "recommended_next_step": (
            "Run a research-only shadow validation of the macro-augmented ranker before any "
            "strategy test." if confirmed else
            "Collect the global cross-asset ETF pack under the controlled offline collectors "
            "(commands above), then rerun Phase 6-A with the full multi-layer pack."),
    }
    _write_json(paths.next_plan, plan)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run(price_csv: Optional[str] = None, out_dir: Optional[str] = None,
        macro_input_dir: Optional[str] = None,
        phase5c_runner_path: Optional[str] = None) -> dict:
    paths = _OutPaths(Path(out_dir) if out_dir else _OUT_DIR_DEFAULT)
    input_dir = Path(macro_input_dir) if macro_input_dir else _INPUT_DIR_DEFAULT
    p5c = _load_phase5c(Path(phase5c_runner_path) if phase5c_runner_path else None)

    # 1. Price panel + Phase 5-C champion reference (reuse harness unchanged).
    csv_path = price_csv or p5c._data_path()
    if not csv_path or not os.path.isfile(csv_path):
        report = _assemble_report(paths, {"error": "price history CSV not found"},
                                  {}, {}, {}, [], REC_ERROR, leakage_clean=False)
        _write_json(paths.report, report)
        return report

    hist = p5c.load_local_history(csv_path)
    al = p5c.Aligned(hist)
    panel = p5c.build_panel(al)
    p5c_preds, leakage = p5c.walk_forward(panel)

    p5c_evals = {m: p5c.evaluate_model(p5c_preds.get(m, [])) for m in p5c.MODELS}
    evaluable = {m: e for m, e in p5c_evals.items() if e.get("evaluable")}
    if not evaluable:
        report = _assemble_report(paths, {"error": "no evaluable Phase 5-C model"},
                                  {}, {}, {}, [], REC_BLOCKED, leakage_clean=False)
        _write_json(paths.report, report)
        return report
    champion = max(evaluable, key=lambda m: (evaluable[m]["mean_rank_ic"],
                                             evaluable[m].get("mean_decile_spread", 0.0)))
    ref_preds = p5c_preds[champion]

    # 2. Macro pack + attach cross-sectional macro features.
    macro = MacroPack(al, input_dir)
    inv = build_inventory(input_dir, macro)
    macro_usable = attach_macro_features(p5c, al, panel, macro)

    # 3. Augmented walk-forward.
    aug_preds = walk_forward_augmented(p5c, panel)
    all_preds = {
        "phase5c_reference": ref_preds,  # Phase 5-C champion (best price-only model)
        **aug_preds,                     # price_only(ridge) + macro-augmented (same family)
    }
    evals = {m: p5c.evaluate_model(all_preds.get(m, [])) for m in MODELS_COMPARED}

    # 4. Common-date apples-to-apples comparison.
    date_sets = [_dates_of(all_preds[m]) for m in MODELS_COMPARED if all_preds.get(m)]
    common = set.intersection(*date_sets) if date_sets else set()
    ic_common = {m: _mean_ic_on_dates(p5c, all_preds[m], common) for m in MODELS_COMPARED}
    ref_ic_common = ic_common.get("phase5c_reference")
    incremental = {m: ((ic_common[m] - ref_ic_common)
                       if (ic_common.get(m) is not None and ref_ic_common is not None) else None)
                   for m in MODELS_COMPARED}
    placebo = {m: p5c.placebo_mean_ic(all_preds.get(m, [])) for m in MODELS_COMPARED}

    aug_inc = {m: incremental.get(m) for m in _AUG_MODELS if incremental.get(m) is not None}
    best_aug = max(aug_inc, key=lambda m: aug_inc[m]) if aug_inc else None

    # Within-family attribution: isolate macro's marginal effect vs the price-only
    # RIDGE baseline (same model family as the augmented models), so the headline
    # incremental-vs-champion is not confounded by the ridge-vs-logistic gap.
    price_ridge_ic = ic_common.get("price_only")
    within_family = {
        m: ((ic_common[m] - price_ridge_ic)
            if (ic_common.get(m) is not None and price_ridge_ic is not None) else None)
        for m in ("price_plus_macro", "price_plus_macro_regime_interaction")
    }

    metrics = {
        "phase5c_reference_model_used": champion,
        "phase5c_reference_mean_rank_ic_full": _round(evaluable[champion]["mean_rank_ic"]),
        "phase5c_reference_mean_rank_ic_common": _round(ref_ic_common),
        "macro_records_usable": macro_usable,
        "common_scored_dates": len(common),
        "same_dates_universe": len(common) > 0 and all(
            ic_common.get(m) is not None for m in MODELS_COMPARED),
        "ic_common_by_model": {m: _round(v) for m, v in ic_common.items()},
        "incremental_by_model_common": {m: _round(v) for m, v in incremental.items()},
        "placebo_by_model": {m: _round(v) for m, v in placebo.items()},
        "best_augmented_model": best_aug,
        "best_augmented_mean_rank_ic": _round(ic_common.get(best_aug)) if best_aug else None,
        "best_augmented_incremental_ic": _round(aug_inc.get(best_aug)) if best_aug else None,
        "best_augmented_placebo_ic": _round(placebo.get(best_aug)) if best_aug else None,
        "price_only_ridge_mean_rank_ic_common": _round(price_ridge_ic),
        "within_family_macro_increment_common": {k: _round(v) for k, v in within_family.items()},
    }
    leakage_clean = bool(leakage.get("embargo_respected")) and not leakage.get("features_use_future_data")

    gates = build_gate_matrix(metrics, leakage_clean, inv)
    recommendation = derive_recommendation(metrics, leakage_clean, inv)

    # 5. Artifacts.
    _write_inventory(paths, inv)
    _write_catalog(paths)
    _write_scoreboard(paths, evals, metrics)
    _write_incremental(paths, metrics)
    _write_gate_matrix(paths, gates)
    _write_yearly(paths, evals)
    _write_next_plan(paths, recommendation, metrics, inv)

    report = _assemble_report(paths, metrics, evals, inv, leakage, gates, recommendation,
                              leakage_clean)
    _write_json(paths.report, report)
    return report


def _assemble_report(paths, metrics, evals, inv, leakage, gates, recommendation,
                     leakage_clean) -> dict:
    counts = {"PASS": 0, "FAIL": 0, "WARNING": 0, "NOT_EVALUABLE": 0}
    for g in gates:
        counts[g["status"]] = counts.get(g["status"], 0) + 1
    horizon = {}
    for m in MODELS_COMPARED:
        e = evals.get(m, {}) if isinstance(evals, dict) else {}
        if e.get("evaluable"):
            horizon[m] = {
                "mean_rank_ic_20d": _round(e.get("mean_rank_ic")),
                "mean_top_decile_fwd_excess": _round(e.get("mean_top_decile_fwd_excess")),
                "mean_bottom_decile_fwd_excess": _round(e.get("mean_bottom_decile_fwd_excess")),
            }
    return {
        "phase": PHASE,
        "objective": OBJECTIVE,
        "generated_by": "research\\run_phase6a_cross_asset_macro_context_alpha.py",
        # Safety flags.
        "network_used": False,
        "paid_apis_used": False,
        "api_key_required": False,
        "live_data_needed": True,  # for the FULL framework; this run used none
        "live_data_used": False,
        "preview_only": True,
        "orders_enabled": False,
        "broker_execution_enabled": False,
        "automation_enabled": False,
        "deployed": False,
        "strategy_test_run": False,
        "binary_artifacts_created": False,
        "production_replacement": False,
        "paper_trader_touched": False,
        "gcp_touched": False,
        "raw_files_modified": False,
        "d_drive_written": False,
        # Inventory.
        "data_inventory": inv,
        "data_sources_used": [
            "research\\input\\DCOILWTICO.csv (WTI)",
            "research\\input\\DTWEXBGS.csv (broad USD index)",
            "research\\input\\fredgraph.csv (DGS10, DGS2)",
            "research\\input\\CPIAUCSL.csv (CPI, monthly regime)",
            "research\\input\\FEDFUNDS.csv (fed funds, monthly regime)",
            "D:\\...\\phase2k_g_expanded_price_history_free.csv (equity + SPY, read-only)",
        ],
        # Core metrics.
        "models_compared": MODELS_COMPARED,
        **metrics,
        "leakage_clean": leakage_clean,
        "leakage_report": {
            "embargo_days": leakage.get("embargo_days") if isinstance(leakage, dict) else None,
            "min_embargo_gap_observed": leakage.get("min_embargo_gap_observed") if isinstance(leakage, dict) else None,
            "embargo_respected": leakage.get("embargo_respected") if isinstance(leakage, dict) else None,
            "features_use_future_data": leakage.get("features_use_future_data") if isinstance(leakage, dict) else None,
            "macro_features_lagged_t_minus_1": True,
        },
        "ic_t_stat_by_model": {m: _round((evals.get(m, {}) or {}).get("ic_t_stat"))
                               for m in MODELS_COMPARED} if isinstance(evals, dict) else {},
        "yearly_ic_by_model": {m: {str(y): _round(v) for y, v in
                                   ((evals.get(m, {}) or {}).get("ic_by_year") or {}).items()}
                               for m in MODELS_COMPARED} if isinstance(evals, dict) else {},
        "decile_spread_by_model": horizon,
        "gate_summary": {"counts": counts, "gates": gates},
        "recommendation": recommendation,
        "recommendation_allowed_values": list(ALLOWED_RECOMMENDATIONS),
        "recommendation_supported_by_metrics": _recommendation_supported(
            recommendation, metrics, inv) if inv else (recommendation in (REC_ERROR, REC_BLOCKED)),
        "recommended_next_phase": {
            "phase": "6-B",
            "title": ("Cross-asset macro alpha shadow validation"
                      if recommendation == REC_CONFIRMED
                      else "Controlled cross-asset ETF collection + full multi-layer macro rerun"),
        },
        "macro_context_improved_signal": (
            recommendation == REC_CONFIRMED),
        "survivorship_biased": True,
        "production_edge_claimed": False,
        "artifacts": [_rel(paths.report), _rel(paths.inventory), _rel(paths.catalog),
                      _rel(paths.scoreboard), _rel(paths.incremental), _rel(paths.gate_matrix),
                      _rel(paths.yearly), _rel(paths.next_plan)],
    }


def _print_summary(report: dict) -> None:
    print(f"[6-A] recommendation = {report.get('recommendation')}")
    print(f"      reference ({report.get('phase5c_reference_model_used')}) common IC = "
          f"{report.get('phase5c_reference_mean_rank_ic_common')}")
    print(f"      best augmented = {report.get('best_augmented_model')} "
          f"(IC {report.get('best_augmented_mean_rank_ic')}, "
          f"incremental {report.get('best_augmented_incremental_ic')})")
    c = report.get("gate_summary", {}).get("counts", {})
    print(f"      gates: {c}")
    print(f"      cross-asset ETF ready = {report.get('data_inventory', {}).get('full_framework_cross_asset_ready')}")


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 6-A cross-asset macro context alpha pack (offline).")
    p.add_argument("--price-csv", default=None)
    p.add_argument("--out-dir", default=None)
    p.add_argument("--macro-input-dir", default=None)
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    report = run(price_csv=args.price_csv, out_dir=args.out_dir,
                 macro_input_dir=args.macro_input_dir)
    _print_summary(report)
    return 0 if report.get("recommendation") != REC_ERROR else 1


if __name__ == "__main__":
    raise SystemExit(main())
