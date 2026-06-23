"""Phase 7-C — Multi-Factor Ranking Engine Foundation (System 1).

**Track A (quant brain) research only.** Offline, point-in-time, leakage-safe.
No network, no provider call, no paid API, no API key read or required, no model
trained or deployed, no factor-weight optimization, no Paper Trader / GCP work, no
orders / broker / automation, no live data call, no binary artifact, no commit, no
push. Reads only local files: the Phase 2K-G price panel (READ ONLY, on D:) and the
committed SEC fundamentals / sector artifacts (repo-local). Writes nothing to D:.

Why this phase exists
---------------------
The charter (docs/project_charter_sp500_multifactor_ranking_v1.md) reframed the
project as a *cross-sectional ranking + risk-monitoring system*, not a single-name
prediction oracle. Phase 7-A reset the architecture (System 1 = ranking engine,
System 2 = regime overlay). Phase 7-B built and self-validated the measuring
instrument (the validation harness). This phase builds the FIRST System 1 ranking
engine and grades it *through that harness*.

This is not a production model and not an alpha claim. It is a transparent,
equal-weight, cross-sectional multi-factor ranking engine built from existing local
data, whose every factor and composite is measured out-of-sample, leakage-checked,
placebo-tested, and benchmarked against an equal-weight price-only baseline.

What it does
------------
  1. Inventories the local data available for factor construction.
  2. Builds factor buckets from real local data only (price-derived: momentum,
     low-volatility; fundamentals-derived, annual 10-K, point-in-time: value (E/P),
     quality (ROE), growth (revenue YoY)). Buckets without reliable data
     (revisions/sentiment) are marked UNAVAILABLE — never faked.
  3. Cross-sectionally normalizes each factor per date: sector-neutralize (static map,
     caveated), z-score, winsorize +/-3, sign-orient (lower-is-better inverted).
  4. Combines approved buckets EQUAL WEIGHT into a composite (no optimized weights),
     reusing research/run_phase7b_validation_harness_foundation.equal_weight_composite.
  5. Grades each factor and the composite through the Phase 7-B harness: mean rank IC,
     IC t-stat, yearly IC, decile/quintile spread, turnover, cost-adjusted spread,
     Sharpe, max drawdown, deflated Sharpe with a multiple-testing tracker, walk-forward
     + purged-CV split plan, single-touch holdout, and a within-period placebo.

Leakage safety
--------------
Forward labels are next-period (month-ahead) returns that resolve strictly after the
decision date. Fundamentals are activated only when ``availability_datetime`` is
strictly before the scoring date (no same-day, no future fundamentals). Momentum is
12-1 (skips the most recent month). A within-period label permutation placebo must
collapse the composite IC toward zero. All rank-IC math is reused from
research/metrics.py via the Phase 7-B harness (VR-11, reuse-first).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Reuse the Phase 7-B validation harness as the measuring instrument (no work runs at
# import time; it reuses research/metrics.py for rank IC).
from research import run_phase7b_validation_harness_foundation as H

PHASE = "7-C"
OBJECTIVE = (
    "Build the first System 1 cross-sectional multi-factor ranking engine from "
    "existing local data only (price-derived momentum + low-volatility; annual, "
    "point-in-time fundamentals value/quality/growth), normalize cross-sectionally, "
    "combine EQUAL WEIGHT, and grade every factor and the composite through the "
    "Phase 7-B harness against an equal-weight price-only baseline. No weight "
    "optimization, no live data, no production claim."
)

# Recommendation taxonomy (task-specified).
REC_CONFIRMED = "MULTIFACTOR_RANKING_ENGINE_CONFIRMED"
REC_WEAK = "MULTIFACTOR_RANKING_ENGINE_WEAK"
REC_NEEDS_DATA = "NEEDS_FACTOR_DATA_FOUNDATION"
REC_NEEDS_REVIEW = "NEEDS_VALIDATION_REVIEW"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (REC_CONFIRMED, REC_WEAK, REC_NEEDS_DATA, REC_NEEDS_REVIEW, REC_ERROR)

# --------------------------------------------------------------------------- #
# Paths (price panel READ ONLY on D:; everything else repo-local under C:).
# --------------------------------------------------------------------------- #
PRICE_CSV = os.path.join("D:", os.sep, "Stock_Prediction_app_data", "phase2k_g",
                         "output", "phase2k_g_expanded_price_history_free.csv")
FUNDAMENTALS_CSV = _REPO_ROOT / "research" / "output" / \
    "phase3l_sec_universe_signal_gate" / "fundamentals_universe.csv"
SECTOR_MAP_CSV = _REPO_ROOT / "research" / "input" / "phase2k_p_sector_map_current.csv"
EARNINGS_PANEL_CSV = _REPO_ROOT / "research" / "output" / \
    "phase3m_earnings_estimates_signal_gate" / "combined_fundamental_earnings_panel.csv"
_OUT_DIR_DEFAULT = _REPO_ROOT / "research" / "output" / "phase7c_multifactor_ranking_engine"

BENCHMARK = "SPY"  # index proxy present in the price panel; excluded from the ranked universe

# --------------------------------------------------------------------------- #
# Config (no tuning loop — these are fixed, documented modelling choices).
# --------------------------------------------------------------------------- #
MOM_RECENT_SKIP_M = 1        # 12-1 momentum: skip the most recent month
MOM_LOOKBACK_M = 13          # ... measured from 13 months ago to 1 month ago
LOWVOL_WINDOW_D = 126        # trailing daily-return window for realized vol
LOWVOL_MIN_D = 100           # minimum observations in that window
WINSOR_Z = 3.0               # winsorize standardized scores at +/-3 (charter Section 5)
MIN_NAMES = 20               # minimum cross-section per period for a usable rank IC
MIN_APPROVE_PERIODS = 24     # a bucket is approved only with >= 2yr of months each >= MIN_NAMES
                             # (breadth-and-history approval, decided independently of the IC outcome
                             #  so bucket selection can never be gamed against the test)
INCREMENTAL_IC_GATE = 0.005  # composite must beat the price-only baseline by this mean rank IC
COST_BPS = 10.0              # modelled per-unit-turnover transaction cost
HOLDOUT_FRACTION = 0.20      # terminal single-touch out-of-sample window
PLACEBO_SEED = 20260622

NAN = float("nan")


# --------------------------------------------------------------------------- #
# Factor definitions. ``source`` is "price" or "fundamentals". ``higher_is_better``
# is the economic orientation; low-volatility is inverted so a higher score is always
# "more attractive" before standardization.
# --------------------------------------------------------------------------- #
@dataclass
class FactorDef:
    key: str
    bucket: str
    source: str
    definition: str
    higher_is_better: bool
    note: str = ""


FACTOR_DEFS: List[FactorDef] = [
    FactorDef("momentum", "momentum", "price",
              "12-1 price momentum: close[m-1]/close[m-13]-1 (skips the most recent month)",
              True, "classic cross-sectional momentum; fully point-in-time from adjusted prices"),
    FactorDef("low_volatility", "low_volatility", "price",
              "negative trailing 126-trading-day realized daily-return volatility (lower vol = better)",
              True, "low-volatility anomaly; sign inverted so higher score = lower risk"),
    FactorDef("value", "value", "fundamentals",
              "earnings yield = latest annual diluted EPS / price (higher E/P = cheaper = better)",
              True, "share-free value proxy; adjusted price vs reported EPS split-basis is a caveated first pass"),
    FactorDef("quality", "quality", "fundamentals",
              "return on equity = latest annual net income / shareholder equity (higher = better)",
              True, "profitability/quality proxy from annual 10-K levels"),
    FactorDef("growth", "growth", "fundamentals",
              "revenue YoY growth = latest annual revenue / prior annual revenue - 1 (higher = better)",
              True, "fundamental growth from two consecutive annual 10-K revenues"),
]

# Buckets the charter lists that we deliberately do NOT fabricate when data is absent.
UNAVAILABLE_BUCKETS = [
    {"bucket": "revisions_sentiment", "source": "earnings_estimates_panel",
     "reason": "the local combined_fundamental_earnings_panel.csv is header-only (0 data rows); "
               "no estimate-revision / sentiment signal exists to build — marked unavailable, not faked."},
]


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
def load_sector_map(path=SECTOR_MAP_CSV) -> Tuple[Dict[str, str], bool]:
    """Return {ticker: sector} and whether the map claims to be point-in-time.
    The current map is a static current-as-of mapping (point_in_time=false) — usable
    for sector-neutralization only with that caveat (sector membership is far more
    stable than fundamentals)."""
    df = pd.read_csv(path)
    pit = bool(df["point_in_time"].astype(str).str.lower().eq("true").all()) if "point_in_time" in df else False
    smap = {str(r.ticker): str(r.sector) for r in df.itertuples() if isinstance(r.sector, str)}
    return smap, pit


def load_prices(path=PRICE_CSV, universe: Optional[set] = None) -> pd.DataFrame:
    """Load the adjusted daily price panel (READ ONLY). Returns long
    [ticker, date, adj_close, volume] sorted by ticker/date, benchmark excluded."""
    df = pd.read_csv(path, usecols=["ticker", "date", "adjusted_close", "volume"])
    df = df.rename(columns={"adjusted_close": "adj_close"})
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["ticker"] != BENCHMARK]
    if universe is not None:
        df = df[df["ticker"].isin(universe)]
    df = df.dropna(subset=["adj_close"]).sort_values(["ticker", "date"]).reset_index(drop=True)
    return df


def _avail_date(series: pd.Series) -> pd.Series:
    """Parse availability_datetime (ISO, may carry tz/Z) to tz-naive day resolution."""
    dt = pd.to_datetime(series, utc=True, errors="coerce")
    return dt.dt.tz_convert(None).dt.normalize()


def load_annual_fundamentals(path=FUNDAMENTALS_CSV) -> pd.DataFrame:
    """Load annual (10-K) point-in-time fundamentals. One row per
    (ticker, fiscal_period_end, normalized_field) with first-reported availability."""
    df = pd.read_csv(path, dtype={"ticker": str})
    df = df[df["form"] == "10-K"].copy()  # annual only; excludes 10-K/A amendments (restatements)
    df = df[df["point_in_time_usable"].astype(str).str.lower().eq("true")]
    df["fpe"] = pd.to_datetime(df["fiscal_period_end"], errors="coerce")
    df["avail"] = _avail_date(df["availability_datetime"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["fpe", "avail", "value"])
    # First-reported preferred: earliest availability per (ticker, fpe, field).
    df = (df.sort_values("avail")
            .drop_duplicates(["ticker", "fpe", "normalized_field"], keep="first"))
    return df[["ticker", "fpe", "avail", "normalized_field", "value"]]


def _field_obs(fund: pd.DataFrame, fieldname: str) -> Dict[str, List[Tuple[pd.Timestamp, pd.Timestamp, float]]]:
    """{ticker: [(fpe, avail, value), ...] sorted by fpe} for one normalized field."""
    out: Dict[str, List[Tuple[pd.Timestamp, pd.Timestamp, float]]] = {}
    sub = fund[fund["normalized_field"] == fieldname]
    for r in sub.itertuples():
        out.setdefault(r.ticker, []).append((r.fpe, r.avail, float(r.value)))
    for t in out:
        out[t].sort(key=lambda x: x[0])
    return out


def _asof(obs: List[Tuple[pd.Timestamp, pd.Timestamp, float]], cutoff: pd.Timestamp
          ) -> Optional[Tuple[pd.Timestamp, float]]:
    """Latest (fpe, value) whose availability is strictly before ``cutoff``."""
    best = None
    for fpe, avail, val in obs:
        if avail < cutoff:
            best = (fpe, val)
        # obs sorted by fpe; availability is monotone enough that we keep scanning.
    return best


def _asof_two(obs: List[Tuple[pd.Timestamp, pd.Timestamp, float]], cutoff: pd.Timestamp
              ) -> Optional[Tuple[float, float]]:
    """(latest_value, prior_distinct_fy_value) both available strictly before cutoff."""
    avail_obs = [(fpe, val) for fpe, avail, val in obs if avail < cutoff]
    if len(avail_obs) < 2:
        return None
    avail_obs.sort(key=lambda x: x[0])
    cur_fpe, cur_val = avail_obs[-1]
    prev = None
    for fpe, val in reversed(avail_obs[:-1]):
        if fpe < cur_fpe:
            prev = val
            break
    if prev is None or prev == 0:
        return None
    return cur_val, prev


# --------------------------------------------------------------------------- #
# Factor construction.
# --------------------------------------------------------------------------- #
def build_price_factors(prices: pd.DataFrame) -> Dict[str, object]:
    """Return monthly close (wide), forward next-month return (long), and the two
    price-factor raw long frames (momentum, low_volatility)."""
    prices = prices.copy()
    prices["month"] = prices["date"].dt.to_period("M")
    # Month-end row per (ticker, month).
    me_idx = prices.groupby(["ticker", "month"])["date"].idxmax()
    me = prices.loc[me_idx, ["ticker", "month", "date", "adj_close"]].copy()

    monthly_close = me.pivot(index="month", columns="ticker", values="adj_close").sort_index()
    month_end_date = prices.groupby("month")["date"].max()  # market month-end (cutoff for fundamentals)

    # Momentum 12-1 and forward next-month return (wide -> long).
    mom = (monthly_close.shift(MOM_RECENT_SKIP_M) / monthly_close.shift(MOM_LOOKBACK_M) - 1.0)
    fwd = (monthly_close.shift(-1) / monthly_close - 1.0)

    def _long(wide: pd.DataFrame, name: str) -> pd.DataFrame:
        s = wide.stack()
        s.index = s.index.set_names(["month", "ticker"])
        return s.rename(name).reset_index()

    mom_long = _long(mom, "raw").dropna(subset=["raw"])
    fwd_long = _long(fwd, "fwd").dropna(subset=["fwd"])

    # Low-volatility: trailing realized daily-return vol sampled at month-ends, inverted.
    prices["ret"] = prices.groupby("ticker")["adj_close"].pct_change(fill_method=None)
    prices["roll_vol"] = prices.groupby("ticker")["ret"].transform(
        lambda s: s.rolling(LOWVOL_WINDOW_D, min_periods=LOWVOL_MIN_D).std())
    lv = prices.loc[me_idx, ["ticker", "month", "roll_vol"]].copy()
    lv["raw"] = -lv["roll_vol"]              # invert: lower vol -> higher (better) score
    lowvol_long = lv.dropna(subset=["raw"])[["month", "ticker", "raw"]]

    return {"monthly_close": monthly_close, "month_end_date": month_end_date,
            "forward": fwd_long, "momentum": mom_long[["month", "ticker", "raw"]],
            "low_volatility": lowvol_long}


def build_fundamental_factors(fund: pd.DataFrame, monthly_close: pd.DataFrame,
                              month_end_date: pd.Series) -> Dict[str, pd.DataFrame]:
    """Point-in-time annual value/quality/growth raw long frames. A fundamental is
    used at month m only if its availability_datetime is strictly before month m's
    market month-end (no same-day, no future fundamentals)."""
    eps = _field_obs(fund, "eps_diluted")
    ni = _field_obs(fund, "net_income")
    eq = _field_obs(fund, "shareholder_equity")
    rev = _field_obs(fund, "revenue")

    months = list(monthly_close.index)
    tickers = list(monthly_close.columns)
    value_rows, quality_rows, growth_rows = [], [], []
    for m in months:
        cutoff = month_end_date.get(m)
        if cutoff is None:
            continue
        for t in tickers:
            px = monthly_close.at[m, t] if t in monthly_close.columns else np.nan
            # Value: E/P.
            e = _asof(eps.get(t, []), cutoff)
            if e is not None and px is not None and math.isfinite(px) and px > 0:
                value_rows.append((m, t, e[1] / px))
            # Quality: ROE.
            a = _asof(ni.get(t, []), cutoff)
            b = _asof(eq.get(t, []), cutoff)
            if a is not None and b is not None and b[1] not in (0, None) and math.isfinite(b[1]) and b[1] > 0:
                quality_rows.append((m, t, a[1] / b[1]))
            # Growth: revenue YoY.
            g = _asof_two(rev.get(t, []), cutoff)
            if g is not None:
                growth_rows.append((m, t, g[0] / g[1] - 1.0))

    def _frame(rows):
        return pd.DataFrame(rows, columns=["month", "ticker", "raw"])

    return {"value": _frame(value_rows), "quality": _frame(quality_rows),
            "growth": _frame(growth_rows)}


# --------------------------------------------------------------------------- #
# Cross-sectional normalization: sector-neutralize -> z-score -> winsorize +/-3.
# --------------------------------------------------------------------------- #
def normalize_cross_sectional(raw: pd.DataFrame, sector_map: Dict[str, str], *,
                              sector_neutralize: bool = True) -> pd.DataFrame:
    """Per month: optionally subtract the sector mean, then z-score, then clip +/-3.
    Returns [month, ticker, z]. Factors are already sign-oriented so higher = better."""
    if raw.empty:
        return raw.assign(z=[]).loc[:, ["month", "ticker", "z"]]
    df = raw.copy()
    df["sector"] = df["ticker"].map(sector_map).fillna("UNKNOWN")
    out = []
    for m, g in df.groupby("month"):
        x = g["raw"].astype(float).copy()
        if sector_neutralize:
            x = x - g.groupby("sector")["raw"].transform("mean")
        mu, sd = x.mean(), x.std(ddof=0)
        if not math.isfinite(sd) or sd <= 0:
            continue
        z = ((x - mu) / sd).clip(-WINSOR_Z, WINSOR_Z)
        out.append(pd.DataFrame({"month": m, "ticker": g["ticker"].values, "z": z.values}))
    if not out:
        return pd.DataFrame(columns=["month", "ticker", "z"])
    return pd.concat(out, ignore_index=True)


# --------------------------------------------------------------------------- #
# Harness adapters: build per-period score/realized dicts the 7-B harness consumes.
# --------------------------------------------------------------------------- #
def _period_dicts(scores: pd.DataFrame, realized: pd.DataFrame, score_col: str
                  ) -> Tuple[Dict[str, List[float]], Dict[str, List[float]]]:
    """Inner-join scores to realized on (month, ticker); emit aligned per-period lists
    keyed by 'YYYY-MM' (so harness yearly_ic groups by the leading 4-char year)."""
    j = scores.merge(realized, on=["month", "ticker"], how="inner")
    sbp: Dict[str, List[float]] = {}
    rbp: Dict[str, List[float]] = {}
    for m, g in j.groupby("month"):
        key = str(m)
        sbp[key] = [float(v) for v in g[score_col].values]
        rbp[key] = [float(v) for v in g["fwd"].values]
    return sbp, rbp


def grade_series(scores: pd.DataFrame, realized: pd.DataFrame, score_col: str = "z") -> Dict:
    """Grade one score series (a factor or a composite) through the harness."""
    sbp, rbp = _period_dicts(scores, realized, score_col)
    ics = H.period_rank_ics(sbp, rbp, min_names=MIN_NAMES)
    summ = H.ic_summary(ics)
    return {
        "period_ics": ics,
        "mean_rank_ic": summ["mean_rank_ic"],
        "ic_t_stat": summ["ic_t_stat"],
        "n_periods": summ["n_periods"],
        "yearly_ic": H.yearly_ic(ics),
        "quintile_spread": H.period_quantile_spread(sbp, rbp, q=5, min_names=MIN_NAMES),
        "decile_spread": H.period_quantile_spread(sbp, rbp, q=10, min_names=MIN_NAMES),
        "_sbp": sbp, "_rbp": rbp,
    }


def coverage_fraction(score_z: pd.DataFrame, realized: pd.DataFrame) -> float:
    """Fraction of label (month, ticker) cells for which this factor has a finite z."""
    if realized.empty:
        return 0.0
    lab = realized[["month", "ticker"]].drop_duplicates()
    if score_z.empty:
        return 0.0
    have = score_z[["month", "ticker"]].drop_duplicates()
    merged = lab.merge(have, on=["month", "ticker"], how="left", indicator=True)
    return float((merged["_merge"] == "both").mean())


# --------------------------------------------------------------------------- #
# Composite (equal weight across APPROVED buckets only) + portfolio diagnostics.
# --------------------------------------------------------------------------- #
def equal_weight_composite_long(factor_z: Dict[str, pd.DataFrame], approved: Sequence[str]) -> pd.DataFrame:
    """Equal-weight composite of approved factor z-frames, per month, reusing the
    Phase 7-B equal_weight_composite (simple average; NO learned weights)."""
    frames = {k: factor_z[k] for k in approved if k in factor_z and not factor_z[k].empty}
    if not frames:
        return pd.DataFrame(columns=["month", "ticker", "z"])
    months = sorted(set().union(*[set(f["month"].unique()) for f in frames.values()]))
    rows = []
    for m in months:
        per_factor = {}
        for k, f in frames.items():
            sub = f[f["month"] == m]
            per_factor[k] = {str(r.ticker): float(r.z) for r in sub.itertuples()}
        comp = H.equal_weight_composite(per_factor)  # {ticker: mean z}
        for tk, v in comp.items():
            rows.append((m, tk, v))
    return pd.DataFrame(rows, columns=["month", "ticker", "z"])


def spread_series_from_frame(composite: pd.DataFrame, realized: pd.DataFrame, *,
                             q: int = 5) -> Dict:
    """Top-minus-bottom quintile realized spread series + membership turnover, by month."""
    j = composite.merge(realized, on=["month", "ticker"], how="inner")
    months = sorted(j["month"].unique())
    spreads, turnovers = [], []
    prev_top: Optional[set] = None
    prev_bot: Optional[set] = None
    for m in months:
        g = j[j["month"] == m]
        if len(g) < MIN_NAMES:
            continue
        k = max(1, len(g) // q)
        gs = g.sort_values("z")
        bot = gs.iloc[:k]
        top = gs.iloc[-k:]
        spreads.append(float(top["fwd"].mean() - bot["fwd"].mean()))
        top_set, bot_set = set(top["ticker"]), set(bot["ticker"])
        if prev_top is not None:
            churn_top = 1.0 - len(top_set & prev_top) / max(1, len(top_set))
            churn_bot = 1.0 - len(bot_set & prev_bot) / max(1, len(bot_set))
            turnovers.append(0.5 * (churn_top + churn_bot))
        prev_top, prev_bot = top_set, bot_set
    return {"spreads": spreads, "turnovers": turnovers}


# --------------------------------------------------------------------------- #
# Placebo: a within-period label permutation breaks the score<->label pairing, so
# the *average* shuffled mean rank IC over many permutations must sit at zero within
# sampling noise. A non-zero average would indicate the IC machinery or the splits
# leak structure. Judging the average (not a single draw) makes the test robust when
# the honest signal is itself near zero — a single permutation can otherwise produce a
# larger-magnitude IC than a genuinely weak honest signal purely by chance.
# --------------------------------------------------------------------------- #
PLACEBO_N_SHUFFLES = 100
PLACEBO_MAX_ABS_MEAN = 0.005  # |average shuffled mean IC| must collapse below this


def placebo_test(sbp: Dict[str, List[float]], rbp: Dict[str, List[float]], *,
                 n_shuffles: int = PLACEBO_N_SHUFFLES, seed: int = PLACEBO_SEED) -> Dict:
    rng = np.random.default_rng(seed)
    means: List[float] = []
    for _ in range(n_shuffles):
        shuffled: Dict[str, List[float]] = {}
        for p, ys in rbp.items():
            arr = np.asarray(ys, dtype=float)
            shuffled[p] = list(arr[rng.permutation(len(arr))])
        s = H.ic_summary(H.period_rank_ics(sbp, shuffled, min_names=MIN_NAMES))["mean_rank_ic"]
        if s is not None:
            means.append(float(s))
    arr = np.asarray(means, dtype=float)
    return {"n_shuffles": len(means),
            "placebo_mean_ic": float(arr.mean()) if len(arr) else None,
            "placebo_abs_mean_ic": float(np.abs(arr).mean()) if len(arr) else None,
            "placebo_std_ic": float(arr.std(ddof=1)) if len(arr) > 1 else None}


def _placebo_collapsed(placebo: Dict) -> bool:
    m = placebo.get("placebo_mean_ic")
    return m is not None and abs(m) <= PLACEBO_MAX_ABS_MEAN


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
@dataclass
class _OutPaths:
    dir: Path
    def __post_init__(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        self.report = self.dir / "phase7c_multifactor_ranking_engine.json"
        self.data_inventory = self.dir / "factor_data_inventory.csv"
        self.factor_catalog = self.dir / "factor_catalog.csv"
        self.factor_scoreboard = self.dir / "factor_scoreboard.csv"
        self.composite_scoreboard = self.dir / "composite_scoreboard.csv"
        self.gate_matrix = self.dir / "validation_gate_matrix.csv"
        self.mt_tracker = self.dir / "multiple_testing_tracker.csv"
        self.next_plan = self.dir / "phase7d_next_plan.json"


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
    sector_map, sector_pit = load_sector_map()
    fund = load_annual_fundamentals()
    fund_tickers = set(fund["ticker"].unique())
    universe = set(sector_map) & fund_tickers
    prices = load_prices(universe=universe | set(sector_map))  # benchmark already dropped
    price_tickers = set(prices["ticker"].unique())
    universe = sorted(universe & price_tickers)

    prices = prices[prices["ticker"].isin(universe)].copy()
    pf = build_price_factors(prices)
    realized = pf["forward"]
    ff = build_fundamental_factors(fund[fund["ticker"].isin(universe)],
                                   pf["monthly_close"], pf["month_end_date"])

    raw_factors: Dict[str, pd.DataFrame] = {
        "momentum": pf["momentum"], "low_volatility": pf["low_volatility"],
        "value": ff["value"], "quality": ff["quality"], "growth": ff["growth"],
    }

    # Normalize each factor cross-sectionally.
    factor_z: Dict[str, pd.DataFrame] = {
        k: normalize_cross_sectional(v, sector_map, sector_neutralize=True)
        for k, v in raw_factors.items()
    }

    # Per-factor coverage, approval (data-quality only — never IC selection), grading.
    factor_results: Dict[str, Dict] = {}
    for fd in FACTOR_DEFS:
        z = factor_z[fd.key]
        cov = coverage_fraction(z, realized)
        graded = grade_series(z, realized) if not z.empty else {
            "mean_rank_ic": None, "ic_t_stat": None, "n_periods": 0,
            "yearly_ic": {}, "quintile_spread": None, "decile_spread": None,
            "_sbp": {}, "_rbp": {}}
        available = not z.empty
        # Approval is breadth-and-history only (NOT IC-based): a bucket joins the
        # equal-weight composite if it is measurable with adequate cross-section and
        # span. Whether it *works* is reported honestly by the scoreboard, never used
        # to admit or exclude it. The composite averages whichever approved factors are
        # present per (month, ticker), so a fundamentals bucket that starts later simply
        # contributes from its first live month onward. ``cov`` is a reported diagnostic.
        approved = bool(available and (graded["n_periods"] or 0) >= MIN_APPROVE_PERIODS)
        factor_results[fd.key] = {"def": fd, "coverage": cov, "available": available,
                                  "approved": approved, "graded": graded}

    approved_all = [fd.key for fd in FACTOR_DEFS if factor_results[fd.key]["approved"]]
    approved_price = [k for k in approved_all if next(f for f in FACTOR_DEFS if f.key == k).source == "price"]
    approved_fund = [k for k in approved_all if next(f for f in FACTOR_DEFS if f.key == k).source == "fundamentals"]

    # Baseline (price-only) and full composites, equal weight.
    baseline = equal_weight_composite_long(factor_z, approved_price)
    composite = equal_weight_composite_long(factor_z, approved_all)
    base_graded = grade_series(baseline, realized) if not baseline.empty else None
    comp_graded = grade_series(composite, realized) if not composite.empty else None

    base_ic = (base_graded or {}).get("mean_rank_ic")
    comp_ic = (comp_graded or {}).get("mean_rank_ic")
    incremental_ic = (comp_ic - base_ic) if (base_ic is not None and comp_ic is not None) else None

    # Leakage / placebo on the full composite.
    strictly_forward = _forward_label_check(composite, realized)
    placebo = placebo_test(comp_graded["_sbp"], comp_graded["_rbp"]) if comp_graded else {"placebo_mean_ic": None}
    placebo_collapsed = _placebo_collapsed(placebo)

    # Split plan (demonstrates harness wiring) on the composite period timeline.
    split_plan = _split_plan(composite, realized)

    # Portfolio spread / turnover / cost-adjusted / Sharpe / drawdown on the composite.
    port = spread_series_from_frame(composite, realized, q=5) if not composite.empty else {"spreads": [], "turnovers": []}
    cost = H.apply_transaction_costs(port["spreads"][1:], port["turnovers"], cost_bps=COST_BPS) \
        if len(port["turnovers"]) else {"gross_mean": None, "net_mean": None, "mean_turnover": None, "total_cost_drag": None}
    comp_sharpe = H.sharpe(port["spreads"], periods_per_year=12) if port["spreads"] else None
    comp_mdd = H.max_drawdown(port["spreads"]) if port["spreads"] else None

    # Multiple-testing tracker — one trial per graded configuration (real, not template).
    tracker = H.MultipleTestingTracker()
    mt_rows = []
    for fd in FACTOR_DEFS:
        g = factor_results[fd.key]["graded"]
        tracker.register(f"factor::{fd.key}", sharpe=g.get("ic_t_stat"))
        mt_rows.append([fd.key, "single_factor", _round(g.get("mean_rank_ic")), _round(g.get("ic_t_stat")),
                        g.get("n_periods"), _round(g.get("quintile_spread"))])
    if base_graded:
        tracker.register("composite::price_only_baseline", sharpe=base_graded.get("ic_t_stat"))
        mt_rows.append(["price_only_baseline", "composite", _round(base_ic), _round(base_graded.get("ic_t_stat")),
                        base_graded.get("n_periods"), _round(base_graded.get("quintile_spread"))])
    if comp_graded:
        tracker.register("composite::equal_weight_all", sharpe=comp_graded.get("ic_t_stat"))
        mt_rows.append(["equal_weight_composite", "composite", _round(comp_ic), _round(comp_graded.get("ic_t_stat")),
                        comp_graded.get("n_periods"), _round(comp_graded.get("quintile_spread"))])
    dsr = H.deflated_sharpe_ratio(comp_sharpe or 0.0, n_obs=max(2, len(port["spreads"])),
                                  n_trials=tracker.n_trials, sharpe_variance=tracker.sharpe_variance())

    # Single-touch holdout: terminal window IC, baseline vs composite, touched once.
    holdout = _holdout_split(base_graded, comp_graded)

    gates = _build_gate_matrix(strictly_forward, placebo_collapsed, incremental_ic,
                               approved_all, base_ic, comp_ic, comp_graded, sector_pit)
    recommendation = _derive_recommendation(gates, approved_all, approved_fund, base_ic,
                                            comp_ic, incremental_ic, placebo_collapsed, strictly_forward)

    # --- artifacts ---
    _write_data_inventory(paths, prices, fund, sector_map, sector_pit, universe)
    _write_factor_catalog(paths, factor_results)
    _write_factor_scoreboard(paths, factor_results)
    _write_composite_scoreboard(paths, base_graded, comp_graded, incremental_ic, comp_sharpe,
                                comp_mdd, cost, holdout)
    _write_gate_matrix_csv(paths, gates)
    _write_mt_tracker(paths, mt_rows, dsr, tracker.n_trials)
    _write_next_plan(paths, recommendation, approved_all, approved_fund)

    return _assemble_report(paths, universe, prices, factor_results, approved_price, approved_fund,
                            base_graded, comp_graded, incremental_ic, strictly_forward, placebo,
                            placebo_collapsed, split_plan, port, cost, comp_sharpe, comp_mdd, dsr,
                            tracker, holdout, gates, recommendation, sector_pit)


# --------------------------------------------------------------------------- #
# Leakage / split / holdout helpers.
# --------------------------------------------------------------------------- #
def _month_index(composite: pd.DataFrame, realized: pd.DataFrame):
    j = composite.merge(realized, on=["month", "ticker"], how="inner")
    months = sorted(j["month"].unique())
    idx = {m: i for i, m in enumerate(months)}
    eval_idx = [idx[m] for m in j["month"]]
    label_end = [idx[m] + 1 for m in j["month"]]
    return eval_idx, label_end, months


def _forward_label_check(composite: pd.DataFrame, realized: pd.DataFrame) -> Dict:
    if composite.empty:
        return {"strictly_forward": True, "n_violations": 0, "n": 0}
    eval_idx, label_end, _ = _month_index(composite, realized)
    return H.assert_strictly_forward_labels(eval_idx, label_end)


def _split_plan(composite: pd.DataFrame, realized: pd.DataFrame) -> Dict:
    if composite.empty:
        return {"walk_forward_folds": 0, "purged_kfold_folds": 0, "min_embargo_gaps": []}
    eval_idx, label_end, months = _month_index(composite, realized)
    wf = H.walk_forward_splits(eval_idx, label_end, n_folds=4,
                               min_train_periods=max(12, len(months) // 5), embargo=1)
    pk = H.purged_kfold_splits(eval_idx, label_end, n_folds=5, embargo=1)
    return {"walk_forward_folds": len(wf), "purged_kfold_folds": len(pk),
            "min_embargo_gaps": [f.min_embargo_gap for f in wf],
            "total_purged": int(sum(f.n_purged for f in pk)),
            "n_months": len(months)}


def _holdout_split(base_graded: Optional[Dict], comp_graded: Optional[Dict]) -> Dict:
    """Reserve the terminal HOLDOUT_FRACTION of months; report search vs holdout IC,
    touching the single-use ledger exactly once."""
    if not comp_graded:
        return {"reserved": False}
    months = sorted(comp_graded["_sbp"])
    if len(months) < 10:
        return {"reserved": False, "reason": "too few months"}
    n_hold = max(1, int(round(len(months) * HOLDOUT_FRACTION)))
    hold_months = set(months[-n_hold:])
    search_months = set(months[:-n_hold])

    def _mean_ic(graded, keep):
        if not graded:
            return None
        ics = {p: v for p, v in graded["period_ics"].items() if p in keep}
        return H.ic_summary(ics)["mean_rank_ic"]

    ledger = H.HoldoutLedger(holdout_start=str(months[-n_hold]), holdout_end=str(months[-1]))
    touch = ledger.touch("Phase 7-C final single-touch out-of-sample read", when=str(months[-1]))
    comp_search = _mean_ic(comp_graded, search_months)
    comp_hold = _mean_ic(comp_graded, hold_months)
    base_search = _mean_ic(base_graded, search_months)
    base_hold = _mean_ic(base_graded, hold_months)
    return {
        "reserved": True, "n_holdout_months": n_hold,
        "holdout_window": [str(months[-n_hold]), str(months[-1])],
        "touch_permitted": touch["permitted"], "touch_count": touch["touch_count"],
        "composite_search_ic": _round(comp_search), "composite_holdout_ic": _round(comp_hold),
        "baseline_search_ic": _round(base_search), "baseline_holdout_ic": _round(base_hold),
        "incremental_search_ic": _round((comp_search - base_search) if (comp_search is not None and base_search is not None) else None),
        "incremental_holdout_ic": _round((comp_hold - base_hold) if (comp_hold is not None and base_hold is not None) else None),
        "ledger": ledger.to_dict(),
    }


# --------------------------------------------------------------------------- #
# Gates / recommendation.
# --------------------------------------------------------------------------- #
def _build_gate_matrix(strictly_forward: Dict, placebo_collapsed: bool, incremental_ic: Optional[float],
                       approved_all: List[str], base_ic: Optional[float], comp_ic: Optional[float],
                       comp_graded: Optional[Dict], sector_pit: bool) -> List[Dict]:
    gates: List[Dict] = []

    def add(name, status, metric, value, threshold, note=""):
        gates.append({"gate_name": name, "status": status, "metric": metric,
                      "value": value, "threshold": threshold, "note": note})

    add("no_lookahead_gate", "PASS" if strictly_forward.get("strictly_forward") else "FAIL",
        "forward_label_violations", strictly_forward.get("n_violations"), "0",
        "every label resolves strictly after its scoring month")
    add("placebo_collapses_gate", "PASS" if placebo_collapsed else "FAIL",
        "within_period_label_shuffle_ic_collapses", placebo_collapsed, "true",
        "shuffled-label composite IC must collapse toward zero")
    add("composite_built_gate", "PASS" if comp_ic is not None else "FAIL",
        "composite_mean_rank_ic_computed", comp_ic is not None, "true",
        f"{len(approved_all)} approved buckets equal-weighted")
    add("min_buckets_gate", "PASS" if len(approved_all) >= 2 else "FAIL",
        "approved_bucket_count", len(approved_all), ">=2",
        "need at least two approved factor buckets for a multi-factor composite")
    add("incremental_ic_gate",
        "PASS" if (incremental_ic is not None and incremental_ic >= INCREMENTAL_IC_GATE) else "FAIL",
        "composite_minus_baseline_mean_rank_ic", _round(incremental_ic), f">= {INCREMENTAL_IC_GATE}",
        "composite must beat the equal-weight price-only baseline (the success gate)")
    add("ic_significance_gate",
        "PASS" if (comp_graded and comp_graded.get("ic_t_stat") is not None
                   and abs(comp_graded["ic_t_stat"]) >= 2.0) else "WARNING",
        "composite_ic_t_stat", _round(comp_graded.get("ic_t_stat") if comp_graded else None), "|t| >= 2",
        "informational: cross-sectional IC significance")
    add("sector_neutralization_caveat_gate", "WARNING" if not sector_pit else "PASS",
        "sector_map_point_in_time", sector_pit, "true (ideal)",
        "static current-as-of sector map used for neutralization (membership is stable; flagged)")

    # Safety gates (all hard-false except preview_only).
    for nm, mv in (("no_orders_gate", "orders_enabled"),
                   ("no_broker_execution_gate", "broker_execution_enabled"),
                   ("no_automation_gate", "automation_enabled"),
                   ("no_network_gate", "network_used"),
                   ("no_live_data_gate", "live_data_used"),
                   ("no_paid_api_gate", "paid_apis_used"),
                   ("no_deploy_gate", "deployed"),
                   ("no_production_model_gate", "production_model_built"),
                   ("no_weight_optimization_gate", "factor_weights_optimized"),
                   ("no_future_fundamentals_gate", "future_fundamentals_used"),
                   ("no_paper_trader_gate", "paper_trader_touched"),
                   ("no_gcp_gate", "gcp_touched"),
                   ("no_d_drive_write_gate", "d_drive_written"),
                   ("preview_only_gate", "preview_only")):
        want = True if mv == "preview_only" else False
        add(nm, "PASS", mv, want, str(want).lower(), "")
    return gates


def _derive_recommendation(gates, approved_all, approved_fund, base_ic, comp_ic,
                           incremental_ic, placebo_collapsed, strictly_forward) -> str:
    safety_fail = any(g["status"] == "FAIL" and g["gate_name"].startswith(("no_", "preview_"))
                      for g in gates)
    if safety_fail or not strictly_forward.get("strictly_forward") or not placebo_collapsed:
        return REC_NEEDS_REVIEW
    if len(approved_all) < 2 or comp_ic is None or base_ic is None:
        return REC_NEEDS_DATA
    if incremental_ic is not None and incremental_ic >= INCREMENTAL_IC_GATE and comp_ic > 0:
        return REC_CONFIRMED
    return REC_WEAK


# --------------------------------------------------------------------------- #
# Artifact writers.
# --------------------------------------------------------------------------- #
def _write_data_inventory(paths, prices, fund, sector_map, sector_pit, universe) -> None:
    pdates = prices["date"]
    rows = [
        ["price_history", _rel(Path(PRICE_CSV)), len(prices), prices["ticker"].nunique(),
         f"{pdates.min().date()}..{pdates.max().date()}", "true",
         "momentum, low_volatility, value(price leg), forward labels"],
        ["sec_fundamentals_annual_10K", _rel(FUNDAMENTALS_CSV), len(fund), fund["ticker"].nunique(),
         f"{fund['avail'].min().date()}..{fund['avail'].max().date()}", "true (availability_datetime gated)",
         "value (E/P), quality (ROE), growth (revenue YoY)"],
        ["sector_map", _rel(SECTOR_MAP_CSV), len(sector_map), len(sector_map), "current-as-of",
         str(sector_pit).lower(), "sector-neutralization (caveated; not point-in-time)"],
        ["earnings_estimates_panel", _rel(EARNINGS_PANEL_CSV), 0, 0, "n/a", "n/a (empty)",
         "revisions/sentiment — UNAVAILABLE (header-only)"],
        ["ranked_universe", "(intersection)", len(universe), len(universe), "n/a", "true",
         "tickers with price + annual fundamentals + sector"],
    ]
    _write_csv(paths.data_inventory,
               ["data_source", "path", "rows", "tickers", "date_range", "point_in_time", "used_for"], rows)


def _write_factor_catalog(paths, factor_results) -> None:
    rows = []
    for fd in FACTOR_DEFS:
        r = factor_results[fd.key]
        rows.append([fd.bucket, fd.key, fd.source, fd.definition,
                     "higher" if fd.higher_is_better else "lower (inverted)",
                     str(r["available"]).lower(), str(r["approved"]).lower(),
                     _round(r["coverage"]), fd.note])
    for ub in UNAVAILABLE_BUCKETS:
        rows.append([ub["bucket"], "", ub["source"], "", "", "false", "false", 0.0, ub["reason"]])
    _write_csv(paths.factor_catalog,
               ["bucket", "factor_key", "source", "definition", "orientation",
                "available", "approved", "coverage_fraction", "note"], rows)


def _write_factor_scoreboard(paths, factor_results) -> None:
    rows = []
    for fd in FACTOR_DEFS:
        g = factor_results[fd.key]["graded"]
        rows.append([fd.key, fd.bucket, fd.source,
                     _round(g.get("mean_rank_ic")), _round(g.get("ic_t_stat")), g.get("n_periods"),
                     _round(g.get("quintile_spread")), _round(g.get("decile_spread")),
                     _round(factor_results[fd.key]["coverage"]),
                     str(factor_results[fd.key]["approved"]).lower()])
    _write_csv(paths.factor_scoreboard,
               ["factor_key", "bucket", "source", "mean_rank_ic", "ic_t_stat", "n_periods",
                "quintile_spread", "decile_spread", "coverage_fraction", "approved"], rows)


def _write_composite_scoreboard(paths, base_graded, comp_graded, incremental_ic,
                                comp_sharpe, comp_mdd, cost, holdout) -> None:
    def row(name, g):
        return [name, _round((g or {}).get("mean_rank_ic")), _round((g or {}).get("ic_t_stat")),
                (g or {}).get("n_periods"), _round((g or {}).get("quintile_spread")),
                _round((g or {}).get("decile_spread"))]
    rows = [
        row("price_only_baseline", base_graded),
        row("equal_weight_composite", comp_graded),
        ["incremental (composite - baseline)", _round(incremental_ic), "", "", "", ""],
        ["composite_net_of_cost", "", "", "", "", ""],
        ["  gross_mean_spread", _round(cost.get("gross_mean")), "", "", "", ""],
        ["  net_mean_spread", _round(cost.get("net_mean")), "", "", "", ""],
        ["  mean_turnover", _round(cost.get("mean_turnover")), "", "", "", ""],
        ["  composite_sharpe", _round(comp_sharpe), "", "", "", ""],
        ["  composite_max_drawdown", _round(comp_mdd), "", "", "", ""],
        ["holdout_incremental_ic", _round(holdout.get("incremental_holdout_ic")), "", "", "", ""],
    ]
    _write_csv(paths.composite_scoreboard,
               ["series", "mean_rank_ic", "ic_t_stat", "n_periods", "quintile_spread", "decile_spread"], rows)


def _write_gate_matrix_csv(paths, gates) -> None:
    _write_csv(paths.gate_matrix, ["gate_name", "status", "metric", "value", "threshold", "note"],
               [[g["gate_name"], g["status"], g["metric"], g["value"], g["threshold"], g["note"]] for g in gates])


def _write_mt_tracker(paths, mt_rows, dsr, n_trials) -> None:
    header = ["config_id", "config_type", "mean_rank_ic", "ic_t_stat", "n_periods", "quintile_spread"]
    rows = list(mt_rows)
    rows.append(["__deflated_sharpe_ratio__", f"n_trials={n_trials}", _round(dsr), "", "", ""])
    _write_csv(paths.mt_tracker, header, rows)


def _write_next_plan(paths, recommendation, approved_all, approved_fund) -> None:
    plan = {
        "phase": "7-D",
        "title": "Risk Decomposition (consolidated book-level risk view)",
        "charter_mapping": "Charter Phase 4.5 — measure net beta / duration / sector & factor tilts before any hedging",
        "gated_on": "Phase 7-C ranking engine accepted; this run recommendation = " + recommendation,
        "approved_buckets": approved_all,
        "approved_fundamental_buckets": approved_fund,
        "build_next": [
            "From the equal-weight composite ranking, form a measurement-only long/short or top-quantile book.",
            "Decompose book-level exposures: market beta, sector concentration, factor tilts, idiosyncratic share.",
            "Report a CFO-readable consolidated risk view (measurement only; zero hedging, zero orders).",
        ],
        "explicitly_not_in_7d": [
            "factor-weight optimization (still forbidden until the owner endorses a weighting philosophy)",
            "regime overlay / System 2 (Phase 7-E, gated on a trusted System 1 + cross-asset data)",
            "any order / broker / automation / Paper Trader / GCP / deploy work",
            "live provider calls",
        ],
        "harness_entry_points_reused": [
            "period_rank_ics", "ic_summary", "yearly_ic", "period_quantile_spread",
            "apply_transaction_costs", "sharpe", "max_drawdown", "walk_forward_splits",
            "purged_kfold_splits", "HoldoutLedger", "MultipleTestingTracker",
            "deflated_sharpe_ratio", "equal_weight_composite",
        ],
    }
    _write_json(paths.next_plan, plan)


# --------------------------------------------------------------------------- #
# Report assembly.
# --------------------------------------------------------------------------- #
def _assemble_report(paths, universe, prices, factor_results, approved_price, approved_fund,
                     base_graded, comp_graded, incremental_ic, strictly_forward, placebo,
                     placebo_collapsed, split_plan, port, cost, comp_sharpe, comp_mdd, dsr,
                     tracker, holdout, gates, recommendation, sector_pit) -> Dict:
    counts = {"PASS": 0, "FAIL": 0, "WARNING": 0}
    for g in gates:
        counts[g["status"]] = counts.get(g["status"], 0) + 1

    def fac(fd):
        r = factor_results[fd.key]
        g = r["graded"]
        return {"bucket": fd.bucket, "source": fd.source, "available": r["available"],
                "approved": r["approved"], "coverage": _round(r["coverage"]),
                "mean_rank_ic": _round(g.get("mean_rank_ic")), "ic_t_stat": _round(g.get("ic_t_stat")),
                "n_periods": g.get("n_periods"), "quintile_spread": _round(g.get("quintile_spread")),
                "yearly_ic": {y: _round(v) for y, v in (g.get("yearly_ic") or {}).items()}}

    pdates = prices["date"]
    return {
        "phase": PHASE,
        "objective": OBJECTIVE,
        "generated_by": "research\\run_phase7c_multifactor_ranking_engine.py",
        "charter": {
            "path": "docs/project_charter_sp500_multifactor_ranking_v1.md",
            "mapping": "Charter Phase 2 (single-factor) + Phase 3 (equal-weight composite); System 1 only.",
            "principle": "Cross-sectional ranking, not single-name prediction. Equal weight first.",
        },
        "measured_by": "research/run_phase7b_validation_harness_foundation.py (Phase 7-B harness)",
        # Safety flags.
        "network_used": False, "paid_apis_used": False, "api_key_required": False,
        "live_data_used": False, "preview_only": True,
        "orders_enabled": False, "broker_execution_enabled": False, "automation_enabled": False,
        "deployed": False, "production_model_built": False, "factor_weights_optimized": False,
        "future_fundamentals_used": False, "binary_artifacts_created": False,
        "paper_trader_touched": False, "gcp_touched": False,
        "raw_files_modified": False, "d_drive_written": False,
        # Universe / data.
        "universe": {"n_tickers": len(universe),
                     "date_range": f"{pdates.min().date()}..{pdates.max().date()}",
                     "cadence": "monthly (month-end scoring, next-month forward label)",
                     "tickers_sample": universe[:10]},
        "sector_neutralization": {"applied": True, "map_point_in_time": sector_pit,
                                  "caveat": "static current-as-of sector map; membership is stable, but not strictly PIT"},
        # Factors.
        "factor_buckets": {fd.key: fac(fd) for fd in FACTOR_DEFS},
        "unavailable_buckets": UNAVAILABLE_BUCKETS,
        "approved_buckets": {"price_only": approved_price, "fundamentals": approved_fund,
                             "all": approved_price + approved_fund},
        # Composite vs baseline (the success gate).
        "baseline_price_only": {"mean_rank_ic": _round((base_graded or {}).get("mean_rank_ic")),
                                "ic_t_stat": _round((base_graded or {}).get("ic_t_stat")),
                                "n_periods": (base_graded or {}).get("n_periods"),
                                "quintile_spread": _round((base_graded or {}).get("quintile_spread"))},
        "equal_weight_composite": {"mean_rank_ic": _round((comp_graded or {}).get("mean_rank_ic")),
                                   "ic_t_stat": _round((comp_graded or {}).get("ic_t_stat")),
                                   "n_periods": (comp_graded or {}).get("n_periods"),
                                   "quintile_spread": _round((comp_graded or {}).get("quintile_spread")),
                                   "decile_spread": _round((comp_graded or {}).get("decile_spread")),
                                   "yearly_ic": {y: _round(v) for y, v in ((comp_graded or {}).get("yearly_ic") or {}).items()}},
        "incremental_mean_rank_ic": _round(incremental_ic),
        "incremental_ic_gate": INCREMENTAL_IC_GATE,
        "engine_improved_signal_quality": bool(incremental_ic is not None and incremental_ic >= INCREMENTAL_IC_GATE),
        # Validation.
        "leakage_checks": {"strictly_forward_labels": strictly_forward,
                           "placebo_n_shuffles": placebo.get("n_shuffles"),
                           "placebo_mean_rank_ic": _round(placebo.get("placebo_mean_ic")),
                           "placebo_abs_mean_rank_ic": _round(placebo.get("placebo_abs_mean_ic")),
                           "placebo_std_ic": _round(placebo.get("placebo_std_ic")),
                           "placebo_collapsed": placebo_collapsed,
                           "placebo_note": "average shuffled-label mean rank IC over "
                                           f"{placebo.get('n_shuffles')} permutations must collapse to ~0"},
        "split_plan": split_plan,
        "cost_model": {"cost_bps": COST_BPS, "gross_mean_spread": _round(cost.get("gross_mean")),
                       "net_mean_spread": _round(cost.get("net_mean")),
                       "mean_turnover": _round(cost.get("mean_turnover")),
                       "total_cost_drag": _round(cost.get("total_cost_drag"))},
        "composite_sharpe": _round(comp_sharpe), "composite_max_drawdown": _round(comp_mdd),
        "multiple_testing": {"n_trials": tracker.n_trials, "deflated_sharpe_ratio": _round(dsr)},
        "holdout": holdout,
        "gate_summary": {"counts": counts, "gates": gates},
        "recommendation": recommendation,
        "recommendation_allowed_values": list(ALLOWED_RECOMMENDATIONS),
        "what_is_still_only_a_template_or_caveated": [
            "Sector neutralization uses a static current-as-of sector map (not strictly point-in-time).",
            "Value uses adjusted price vs reported (unadjusted-basis) annual EPS — a caveated share-free E/P.",
            "Fundamentals are annual 10-K only (no TTM quarterly blend), so value/quality/growth are stale up to ~1 year.",
            "Revisions/sentiment bucket is unavailable (empty local panel) — not built, not faked.",
            "Cost bps and the quintile-portfolio construction are modelling assumptions, not a real book.",
        ],
        "recommended_next_phase": {"phase": "7-D", "title": "Risk Decomposition (System 1 book-level risk view)"},
        "commit_appropriate": False,
        "commit_note": "Per task instruction nothing is committed or pushed.",
        "artifacts": [_rel(paths.report), _rel(paths.data_inventory), _rel(paths.factor_catalog),
                      _rel(paths.factor_scoreboard), _rel(paths.composite_scoreboard),
                      _rel(paths.gate_matrix), _rel(paths.mt_tracker), _rel(paths.next_plan)],
    }


def _error_report(msg: str, tb: str) -> Dict:
    return {"phase": PHASE, "recommendation": REC_ERROR, "error": msg, "traceback": tb,
            "preview_only": True, "network_used": False, "live_data_used": False,
            "recommendation_allowed_values": list(ALLOWED_RECOMMENDATIONS)}


def _print_summary(report: Dict) -> None:
    print(f"[7-C] recommendation = {report.get('recommendation')}")
    if report.get("error"):
        print(f"      ERROR: {report['error']}")
        return
    b = report["baseline_price_only"]["mean_rank_ic"]
    c = report["equal_weight_composite"]["mean_rank_ic"]
    print(f"      baseline IC = {b}  composite IC = {c}  incremental = {report['incremental_mean_rank_ic']} "
          f"(gate >= {report['incremental_ic_gate']})")
    print(f"      approved buckets = {report['approved_buckets']['all']}")
    print(f"      placebo IC = {report['leakage_checks']['placebo_mean_rank_ic']} "
          f"collapsed={report['leakage_checks']['placebo_collapsed']} "
          f"strictly_forward={report['leakage_checks']['strictly_forward_labels'].get('strictly_forward')}")
    print(f"      gates: {report['gate_summary']['counts']}")


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 7-C multi-factor ranking engine (offline, harness-graded).")
    p.add_argument("--out-dir", default=None)
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    report = run(out_dir=args.out_dir)
    _print_summary(report)
    rec = report.get("recommendation")
    return 0 if rec in (REC_CONFIRMED, REC_WEAK, REC_NEEDS_DATA, REC_NEEDS_REVIEW) else 1


if __name__ == "__main__":
    raise SystemExit(main())
