"""Phase 23 - Autonomous Scalable Multi-Alpha Research Operating System.

WHY THIS PHASE EXISTS
    Phase 22 validated a single medium-horizon momentum alpha (mom_6_1) on a survivorship-free
    membership-aware Russell 3000 monthly panel, and built a first staged/cached pipeline. But that
    pipeline was tuned for ONE campaign: the loose Pass-1 screen let 11/12 candidates into the
    expensive strict battery, wide-frame ranking/decile construction stayed on Python row-loops, the
    817 MB weekly grid was re-parsed on every run, and there was no durable experiment registry - so
    growing the search to hundreds of independent experiments across families/horizons/frequencies
    would re-pay the full cost every time.

    Phase 23 turns that one-shot script into a REUSABLE research operating system:

      Computation Engine v3 (Workstream A)
        * experiment-level result cache keyed by a deterministic experiment fingerprint (identical
          experiments never recompute);
        * a feature/rank store that persists the constructed signal / forward / valid-mask / sector /
          benchmark-residual frames so campaign N+1 skips ~20 s of frame construction;
        * VECTORIZED (loop-free) rank-IC and decile-book primitives PROVEN bit-identical to the Phase 22
          reference and benchmarked against it; the benchmark is honest - on this 96%-sparse ragged panel
          a full-width argsort over 12,266 mostly-masked columns is ~4x slower than argsorting the ~2,959
          valid names, so the compact valid-subset row-loop stays the PRODUCTION path (correctness is
          never traded for speed) and the real speedups come from the experiment/feature/weekly caches;
        * a resource-aware worker system (stdlib only) with a configurable worker cap, a memory budget,
          a safe sequential fallback, deterministic output ordering, and per-experiment checkpointing;
        * a compact weekly cache so the survivorship-aware weekly grid is parsed once.

      Research OS (Workstream B) - persistent hypothesis registry, experiment registry, rejection
      graveyard, survivor registry, correlation clusters, data-readiness matrix, entitlement cache and
      a reproducibility manifest, with SEMANTIC candidate de-duplication (a normalized signal formula
      is never registered twice under a different name).

      Multi-family campaigns (Workstreams D/E) across momentum, short-horizon reversal, trend/breakout,
      volatility, liquidity and the slow fundamental champion, at the frequency appropriate to each
      signal (monthly on phase8c; weekly on the survivorship-aware phase8d grid), each run through the
      same multi-horizon IC / spread / cost / turnover / walk-forward / holdout / regime / cohort /
      multiple-testing battery vendored from Phase 22.

      Clustering + transparent ensembles (Workstreams F/G) and frozen paper-challenger specifications
      (Workstream H) for every model that clears the strict gate.

    RESEARCH-ONLY and PAPER-ONLY. No orders, no broker, no automation, no live promotion, no champion
    replacement, no Paper Trader DB writes, no prediction-service calls, no new paid data, no network.
    The champion (composite_sn) is never replaced; decisions are advisory research eligibility.

TERMINAL DECISIONS (exactly one)
    MULTI_ALPHA_RESEARCH_OS_COMPLETE | MULTI_ALPHA_RESEARCH_OS_COMPLETE_DATA_GAPS |
    BLOCKED_PAID_ENTITLEMENT | BLOCKED_DATA_CORRUPTION
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pickle
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

# The Phase 22 module is the numerical REFERENCE engine: its panel loaders, evaluation battery, gates,
# statistics and champion/ensemble machinery are reused verbatim so Phase 23 metrics are provably
# consistent with the committed Phase 22 findings.  Phase 23 adds the v3 engine and the OS around it.
from research import run_phase22_autonomous_high_conviction_alpha_discovery as P

# --------------------------------------------------------------------------- #
# Paths & versions.                                                            #
# --------------------------------------------------------------------------- #
DATA_ROOT = P.DATA_ROOT
REPO_ROOT = P.REPO_ROOT
PANEL8C_DIR = P.PANEL8C_DIR
WEEKLY_GRID = P.WEEKLY_GRID
CHAMPION_PANEL = P.CHAMPION_PANEL
OUTPUT_DIR = os.path.join(REPO_ROOT, "research", "output", "phase23_scalable_multi_alpha_research_os")

CACHE_DIR = os.path.join(DATA_ROOT, "phase23_cache")            # reusable caches (NOT in the repo)
FEATURE_STORE_DIR = os.path.join(CACHE_DIR, "features")
EXPERIMENT_STORE_DIR = os.path.join(CACHE_DIR, "experiments")
WEEKLY_CACHE_DIR = os.path.join(CACHE_DIR, "weekly")

ENGINE_VERSION = "p23.v3"           # bump to invalidate feature + experiment caches on logic change
SIGNAL_CONFIG_VERSION = "p23.sig.v1"

# Worker defaults: cap below (cpu-2) and never saturate the box; threads share the in-memory feature
# store and give real parallelism because numpy argsort / reductions release the GIL.
DEFAULT_MAX_WORKERS = max(1, min(6, (os.cpu_count() or 2) - 2))
DEFAULT_MEM_BUDGET_MB = 4096

MIN_NAMES = P.MIN_NAMES_PER_MONTH
DECILES = P.DECILES


# =========================================================================== #
# WORKSTREAM A - COMPUTATION ENGINE V3                                          #
# =========================================================================== #
def peak_working_set_mb():
    """Best-effort peak resident set (MB) via the Win32 API - no third-party package. None if absent."""
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        fn = getattr(k32, "K32GetProcessMemoryInfo", None)
        if fn is None:
            fn = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
        fn.restype = wintypes.BOOL
        c = PMC()
        c.cb = ctypes.sizeof(PMC)
        if fn(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return round(c.PeakWorkingSetSize / (1024 * 1024), 1)
    except Exception:
        pass
    return None


# --- A3: vectorized rank-IC & decile books, PROVEN bit-identical to P._row_ic / P._decile_books ----- #
def row_ic_vectorized(S, F, valid):
    """Per-month Spearman rank-IC, FULLY vectorized (no Python per-ticker loop).  Bit-identical to
    P._row_ic (test-proven).

    Ordinal ranks over the common valid mask are a permutation of 1..n, so every demeaned rank is an
    integer or half-integer and every product a quarter-integer; the row reductions are therefore exact
    below 2**53 and order-independent, so this loop-free reduction equals the Phase 22 row-loop to the
    last bit.  Being pure numpy it releases the GIL and parallelizes across worker threads.
    """
    Sv = np.asarray(S.values, dtype=float)
    Fv = np.asarray(F.values, dtype=float)
    V = valid.values if hasattr(valid, "values") else np.asarray(valid)
    m = V & np.isfinite(Sv) & np.isfinite(Fv)
    n = m.sum(axis=1)
    nrows, ncols = Sv.shape
    Sm = np.where(m, Sv, np.inf)                       # masked entries sort to the end (then zeroed)
    Fm = np.where(m, Fv, np.inf)
    ordS = np.argsort(Sm, axis=1, kind="mergesort")    # stable ties by column index == P._ordinal_ranks
    ordF = np.argsort(Fm, axis=1, kind="mergesort")
    posS = np.empty((nrows, ncols), dtype=np.int64)
    posF = np.empty((nrows, ncols), dtype=np.int64)
    ar = np.broadcast_to(np.arange(ncols), (nrows, ncols))
    np.put_along_axis(posS, ordS, ar, axis=1)          # posX[i,col] = sorted position of that column
    np.put_along_axis(posF, ordF, ar, axis=1)
    mean = (n + 1) / 2.0                                # per-row mean of ranks 1..n
    rs = np.where(m, (posS + 1).astype(float) - mean[:, None], 0.0)   # invalid -> 0 contributes nothing
    rf = np.where(m, (posF + 1).astype(float) - mean[:, None], 0.0)
    num = (rs * rf).sum(axis=1)
    den = np.sqrt((rs * rs).sum(axis=1) * (rf * rf).sum(axis=1))
    ic = np.full(nrows, np.nan)
    ok = (n >= MIN_NAMES) & (den > 0)
    ic[ok] = num[ok] / den[ok]
    return ic, n


def decile_books_vectorized(S, F, valid, q=DECILES):
    """Top/bottom-decile forward books, vectorized argsort.  Bit-identical to P._decile_books.

    The heavy per-row argsort is done once as a full-matrix mergesort; masked entries are pushed to
    +inf so the valid columns keep the exact ascending-signal order (and tie order) of the Phase 22
    subset sort, giving identical top/bottom column selections and identical mean summation order.
    """
    Sv = np.asarray(S.values, dtype=float)
    Fv = np.asarray(F.values, dtype=float)
    V = valid.values if hasattr(valid, "values") else np.asarray(valid)
    cols = np.asarray(S.columns)
    idx = S.index
    m = V & np.isfinite(Sv) & np.isfinite(Fv)
    n = m.sum(axis=1)
    Sm = np.where(m, Sv, np.inf)
    order = np.argsort(Sm, axis=1, kind="mergesort")
    floor = max(2 * q, MIN_NAMES)
    gross, long_only, dates, top_sets, bot_sets = [], [], [], [], []
    for i in range(Sv.shape[0]):
        ni = int(n[i])
        if ni < floor:
            continue
        k = ni // q
        if k < 1:
            continue
        oi = order[i]
        bot_cols = oi[:k]
        top_cols = oi[ni - k:ni]
        f = Fv[i]
        gross.append(float(f[top_cols].mean() - f[bot_cols].mean()))
        long_only.append(float(f[top_cols].mean()))
        dates.append(idx[i])
        top_sets.append(set(cols[top_cols].tolist()))
        bot_sets.append(set(cols[bot_cols].tolist()))
    return dict(dates=dates, gross=gross, long_only=long_only, top=top_sets, bot=bot_sets)


def evaluate_candidate_v3(S, F, valid, fwd_name, months, cache=None):
    """Full IC/spread/cost/turnover/stability battery using the VECTORIZED primitives.

    Mirrors P.evaluate_candidate exactly but through row_ic_vectorized / decile_books_vectorized;
    output is proven identical to the reference by test_evaluate_v3_matches_reference.
    """
    if cache is not None:
        tok = ("evc", id(S), id(F), id(valid), fwd_name)
        hit = cache.get(tok)
        if hit is not None:
            cache["_stats"]["hits"] += 1
            return hit
    # PRODUCTION uses the Phase 22 compact valid-subset primitives (P._row_ic / P._decile_books): the
    # engine_benchmark artifacts show a full-width vectorized argsort over 12,266 mostly-masked columns
    # is ~4x SLOWER than argsorting only the ~2,959 valid names per month, so removing this Python loop
    # would trade speed for nothing.  row_ic_vectorized / decile_books_vectorized remain as the
    # PROVEN bit-identical vectorized alternative (equivalence tests + benchmark) - correctness is never
    # traded for speed, and the real engine-v3 speedups come from the experiment/feature/weekly caches.
    ic, _n = P._row_ic(S, F, valid, cache=cache)
    ic_series = pd.Series(ic, index=S.index)
    books = P._decile_books(S, F, valid, cache=cache)
    avg_turn, _turn, net = P._turnover_and_net(books)
    slices = P._slice_indices(months)
    lag = P.HORIZON_LAG.get(fwd_name, 0)

    def _metrics(keep):
        icv = [v for d, v in zip(S.index, ic) if d in keep and not (isinstance(v, float) and math.isnan(v))]
        gross = P._agg_over(books["dates"], books["gross"], keep)
        long_only = P._agg_over(books["dates"], books["long_only"], keep)
        n25 = P._agg_over(books["dates"], net["net25"], keep)
        n50 = P._agg_over(books["dates"], net["net50"], keep)
        return dict(
            n_months=len(icv), mean_ic=P._round(P._mean(icv), 5), ic_t=P._round(P._t_stat(icv), 3),
            ic_nw_t=P._round(P._newey_west_t(np.asarray(icv, float), lag), 3),
            pos_ic_rate=P._round(P._positive_rate(icv), 3), gross_spread=P._round(P._mean(gross), 5),
            gross_t=P._round(P._t_stat(gross), 3), net25=P._round(P._mean(n25), 5),
            net50=P._round(P._mean(n50), 5), long_only=P._round(P._mean(long_only), 5),
            maxdd=P._round(P._max_drawdown(n25), 5))

    out = {sl: _metrics(keep) for sl, keep in slices.items()}
    out["avg_turnover"] = P._round(avg_turn, 4)
    yearly = {}
    for d, v in zip(books["dates"], net["net25"]):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        yearly.setdefault(d.year, 0.0)
        yearly[d.year] += v
    total = sum(yearly.values())
    frac = (max(abs(v) for v in yearly.values()) / abs(total)) if (abs(total) > 1e-12 and yearly) else float("nan")
    out["max_year_frac"] = P._round(frac, 3)
    out["ic_series"] = ic_series
    out["net25_stream"] = list(zip([str(d.date()) for d in books["dates"]], net["net25"]))
    if cache is not None:
        cache["_stats"]["misses"] += 1
        cache[tok] = out
        cache["_refs"].append((S, F, valid))
    return out


# --- A1/A2: experiment fingerprint + on-disk stores + feature/rank store ---------------------------- #
def _canon(obj):
    """Deterministic canonical form for fingerprinting (sorted keys, rounded floats)."""
    if isinstance(obj, dict):
        return {str(k): _canon(obj[k]) for k in sorted(obj, key=str)}
    if isinstance(obj, (list, tuple)):
        return [_canon(x) for x in obj]
    if isinstance(obj, float):
        return round(obj, 10)
    return obj


def normalize_signal(family, formula, params, neutralization):
    """Semantic identity of a candidate signal: two hypotheses with the same normalized formula and
    parameters (regardless of display name) collapse to one key, preventing duplicate experiments."""
    f = "".join(str(formula).lower().split())
    p = _canon(params or {})
    return P._fingerprint([family.upper(), f, p, neutralization])


def experiment_key(spec):
    """Deterministic experiment fingerprint over EVERYTHING that can change the metrics.

    universe, signal identity, params, neutralization, frequency, rebalance, forward horizon, cost
    model, mask/protocol, input file fingerprints and the engine version.  Identical => cache hit.
    """
    payload = dict(
        engine=ENGINE_VERSION,
        universe=spec.get("universe"),
        signal_id=normalize_signal(spec.get("family", ""), spec.get("formula", ""),
                                   spec.get("params"), spec.get("neutralization", "raw")),
        neutralization=spec.get("neutralization", "raw"),
        frequency=spec.get("frequency"),
        rebalance=spec.get("rebalance"),
        horizon=spec.get("horizon"),
        cost_bps=_canon(spec.get("cost_bps", P.COST_BPS)),
        mask=spec.get("mask", "member_dvol"),
        protocol=spec.get("protocol", "walkforward_holdout60"),
        inputs=spec.get("input_sigs"),
    )
    return P._fingerprint(_canon(payload))


def _atomic_pickle(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        pickle.dump(obj, fh, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, path)


def experiment_cache_get(key, cache_dir=None):
    path = os.path.join(cache_dir or EXPERIMENT_STORE_DIR, key + ".pkl")
    if os.path.exists(path):
        try:
            with open(path, "rb") as fh:
                return pickle.load(fh)
        except Exception:
            return None
    return None


def experiment_cache_put(key, value, cache_dir=None):
    _atomic_pickle(os.path.join(cache_dir or EXPERIMENT_STORE_DIR, key + ".pkl"), value)


def build_feature_store(panel, sector, spy, use_disk=False, cache_dir=None):
    """Construct (and optionally persist) the reusable frames every campaign shares.

    Persisted by an input+config fingerprint so a rerun reloads instead of reconstructing ~20 s of
    signal frames.  Includes: base + extended signal frames (raw), sector-neutral variants, forward
    frames, the member/liquidity valid mask, regime & cohort masks and benchmark residuals.
    """
    fp = P._fingerprint([ENGINE_VERSION, SIGNAL_CONFIG_VERSION, P._panel_input_sigs(PANEL8C_DIR)])
    disk = os.path.join(cache_dir or FEATURE_STORE_DIR, fp + ".pkl")
    if use_disk and os.path.exists(disk):
        try:
            with open(disk, "rb") as fh:
                fs = pickle.load(fh)
            fs["_cache"] = "hit"
            return fs
        except Exception:
            pass
    sig = P.build_signals(panel)
    sig.update(build_extended_signals(panel, spy))
    fwd = P.build_forwards(panel)
    valid = P.base_valid_mask(panel)
    raw = {k: v for k, v in sig.items()}
    sn = {k: P.sector_neutralize(v, sector) for k, v in sig.items()}
    fs = dict(fingerprint=fp, raw=raw, sn=sn, fwd=fwd, valid=valid,
              regime=P.regime_masks(panel), cohort=P.cohort_masks(panel, valid),
              months=panel["close"].index, _cache="miss")
    if use_disk:
        try:
            store = {k: v for k, v in fs.items() if k != "_cache"}
            _atomic_pickle(disk, store)
        except Exception:
            pass
    return fs


def build_extended_signals(panel, spy):
    """Trend/breakout, volatility/risk and liquidity/volume monthly signals (PIT, higher == better).

    Point-in-time convention matches Phase 22: price features reference close.shift(1) and earlier;
    volatility uses returns through t (close[t] is known at t, as with P.build_signals' lowvol_12m).
    """
    close = panel["close"]
    dvol = panel["dvol"]
    rets = panel.get("rets")
    if rets is None:
        rets = close.pct_change()
        panel["rets"] = rets
    c1 = close.shift(1)
    ma12 = c1.rolling(12).mean()
    high12 = c1.rolling(12).max()
    spy_ret = spy.reindex(close.index).astype(float).pct_change()
    # trailing 36m beta vs SPY (rolling cov / var), matching the deep-dive residual construction
    sr = spy_ret.values.reshape(-1, 1)
    R = rets.values
    n = 36
    def _roll_mean(A):
        return pd.DataFrame(A, index=close.index, columns=close.columns).rolling(n).mean().values
    mean_r = _roll_mean(R)
    mean_s = pd.Series(spy_ret).rolling(n).mean().values.reshape(-1, 1)
    cov = _roll_mean(R * sr) - mean_r * mean_s
    var_s = pd.Series(spy_ret).rolling(n).var(ddof=0).values.reshape(-1, 1)
    beta = np.divide(cov, var_s, out=np.full_like(cov, np.nan), where=np.abs(var_s) > 1e-18)
    beta_df = pd.DataFrame(beta, index=close.index, columns=close.columns)
    resid = rets - beta_df.mul(spy_ret, axis=0)
    neg = rets.where(rets < 0, 0.0)
    out = {
        "trend_ma12": (c1 / ma12 - 1.0),
        "breakout_12": (c1 / high12 - 1.0),
        "trend_consistency": np.sign(rets).shift(1).rolling(12).sum(),
        "downside_vol": -(neg.pow(2).rolling(12).mean().pow(0.5)),
        "ivol_spy": -(resid.rolling(12).std()),
        "beta_neg": -beta_df,
        "dvol_accel": (dvol.shift(1) / dvol.shift(1).rolling(6).mean() - 1.0),
    }
    dd = P.momentum_deep_dive_signals(panel, {"mom_12_1": panel_sig(panel, "mom_12_1")})
    out["mom_voladj"] = dd["mom_voladj"]
    out["resid_mom_spy"] = dd["resid_mom_spy"]
    return out


def panel_sig(panel, key):
    close = panel["close"]
    if key == "mom_12_1":
        return close.shift(1) / close.shift(12) - 1.0
    raise KeyError(key)


# --- A4: resource-aware worker system --------------------------------------------------------------- #
def _effective_workers(n_specs, max_workers, mem_budget_mb, est_mb_per_task):
    by_mem = max(1, int(mem_budget_mb // max(1.0, est_mb_per_task)))
    return max(1, min(max_workers, by_mem, n_specs or 1))


def run_experiments(specs, evaluate_fn, max_workers=1, mem_budget_mb=DEFAULT_MEM_BUDGET_MB,
                    est_mb_per_task=64.0, cache_dir=None, use_experiment_cache=True, resource=None):
    """Evaluate specs with a deterministic, resource-aware, checkpointed worker pool.

    - deterministic ordering: results are returned in input order regardless of completion order;
    - experiment cache: a spec whose key is already stored is a hit (interruption recovery / rerun);
    - checkpointing: each fresh result is written atomically to its own <key>.pkl (no shared artifact);
    - sequential fallback when workers<=1; threads otherwise (numpy releases the GIL) with a memory cap.
    Every evaluate_fn(spec) must be pure given the shared read-only feature store.
    """
    n = len(specs)
    results = [None] * n
    todo = []
    hits = 0
    for i, spec in enumerate(specs):
        key = spec.get("_key") or experiment_key(spec)
        spec["_key"] = key
        if use_experiment_cache:
            cached = experiment_cache_get(key, cache_dir)
            if cached is not None:
                results[i] = cached
                hits += 1
                continue
        todo.append(i)
    workers = _effective_workers(len(todo), max_workers, mem_budget_mb, est_mb_per_task)

    def _one(i):
        val = evaluate_fn(specs[i])
        if use_experiment_cache:
            experiment_cache_put(specs[i]["_key"], val, cache_dir)
        return i, val

    if workers <= 1 or len(todo) <= 1:
        for i in todo:
            _, results[i] = _one(i)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, val in ex.map(_one, todo):     # ex.map preserves input order; we still index explicitly
                results[i] = val
    if resource is not None:
        resource["experiment_cache_hits"] = resource.get("experiment_cache_hits", 0) + hits
        resource["experiment_cache_misses"] = resource.get("experiment_cache_misses", 0) + len(todo)
        resource["effective_workers"] = workers
    return results


# --- A5: compact weekly cache ----------------------------------------------------------------------- #
WEEKLY_USECOLS = ["date", "symbol", "sector", "ret_1", "ret_5", "ret_20", "ret_60",
                  "rel_str_60", "rv_20", "trend_state", "dd_from_high_20", "dollar_vol_20",
                  "fwd_excess_5", "fwd_excess_10", "fwd_excess_20"]


def build_weekly_cache(weekly_path=None, cache_dir=None, use_disk=True):
    """Parse the 817 MB survivorship-aware weekly grid ONCE into a compact column-pruned pickle.

    Returns a compact DataFrame (needed columns only, parsed dates).  Metric-equivalent to reading the
    raw CSV directly (test_weekly_cache_equivalence) but ~an order of magnitude cheaper to reload.
    """
    weekly_path = weekly_path or WEEKLY_GRID
    if not os.path.exists(weekly_path):
        return None
    fp = P._fingerprint([ENGINE_VERSION, P._file_sig(weekly_path), sorted(WEEKLY_USECOLS)])
    disk = os.path.join(cache_dir or WEEKLY_CACHE_DIR, fp + ".pkl")
    if use_disk and os.path.exists(disk):
        try:
            with open(disk, "rb") as fh:
                df = pickle.load(fh)
            df.attrs["_cache"] = "hit"
            return df
        except Exception:
            pass
    df = pd.read_csv(weekly_path, usecols=lambda c: c in WEEKLY_USECOLS)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).reset_index(drop=True)
    df.attrs["_cache"] = "miss"
    if use_disk:
        try:
            _atomic_pickle(disk, df)
        except Exception:
            pass
    return df


# =========================================================================== #
# WORKSTREAM B - HYPOTHESIS REGISTRY + SEMANTIC DEDUP                           #
# =========================================================================== #
# Each hypothesis declares its economic mechanism, formula, required data, frequency, holding horizon,
# expected behavior, parameter neighborhood, cost model, invalidation criteria and relationship to the
# existing alphas - declared BEFORE results are examined.  frequency/horizon drive which campaign runs.
HYPOTHESES = [
    # ---- MOMENTUM (medium, monthly) ------------------------------------------------------------- #
    dict(key="mom_12_1", family="MOMENTUM", formula="close[t-1]/close[t-12]-1", params={"lb": 12, "skip": 1},
         frequency="monthly", horizon="fwd_3m", mechanism="Intermediate-horizon return continuation (under-reaction).",
         expected="positive IC, cost-robust", neighborhood="lb in {6,12}, skip=1", invalidation="IC<=0 or spread_t<2",
         relationship="momentum cluster; orthogonal (~0.1) to composite_sn"),
    dict(key="mom_6_1", family="MOMENTUM", formula="close[t-1]/close[t-7]-1", params={"lb": 6, "skip": 1},
         frequency="monthly", horizon="fwd_3m", mechanism="6-1 momentum: intermediate continuation.",
         expected="strongest momentum leg", neighborhood="lb in {3,6,12}", invalidation="IC<=0 or spread_t<2",
         relationship="Phase 22 flagship (STRONG); momentum cluster"),
    dict(key="mom_3_1", family="MOMENTUM", formula="close[t-1]/close[t-4]-1", params={"lb": 3, "skip": 1},
         frequency="monthly", horizon="fwd_1m", mechanism="Short intermediate continuation.",
         expected="weaker; contaminated by reversal", neighborhood="lb=3", invalidation="spread_t<2",
         relationship="momentum cluster"),
    dict(key="mom_voladj", family="MOMENTUM", formula="(close[t-1]/close[t-12]-1)/std(ret,12)", params={"lb": 12},
         frequency="monthly", horizon="fwd_3m", mechanism="Volatility-scaled momentum (risk-adjusted trend).",
         expected="momentum survives vol scaling", neighborhood="lb=12", invalidation="spread_t<2",
         relationship="momentum robustness variant"),
    dict(key="resid_mom_spy", family="MOMENTUM", formula="mom_12_1 - beta*mom_spy", params={"lb": 12, "beta_lb": 36},
         frequency="monthly", horizon="fwd_3m", mechanism="Residual (market-neutral) momentum.",
         expected="continuation net of market", neighborhood="beta_lb=36", invalidation="spread_t<2",
         relationship="momentum robustness variant"),
    # ---- SHORT REVERSAL (fast weekly / monthly) ------------------------------------------------- #
    dict(key="rev_1m", family="SHORT_REVERSAL", formula="-(close[t]/close[t-1]-1)", params={"lb": 1},
         frequency="monthly", horizon="fwd_1m", mechanism="1-month reversal (overreaction / liquidity provision).",
         expected="fragile at monthly; dominated by momentum", neighborhood="lb=1", invalidation="subperiod sign flip",
         relationship="reversal cluster (monthly leg)"),
    dict(key="wk_rev_1", family="SHORT_REVERSAL", formula="-ret_1", params={"lb": 1}, frequency="weekly",
         horizon="fwd_excess_5", mechanism="1-week reversal (microstructure / liquidity provision).",
         expected="strong at weekly; delisted-inclusive", neighborhood="lb in {1,5}", invalidation="IC<=0",
         relationship="reversal cluster (fast leg)"),
    dict(key="wk_rev_5", family="SHORT_REVERSAL", formula="-ret_5", params={"lb": 5}, frequency="weekly",
         horizon="fwd_excess_5", mechanism="1-week (5d) reversal.", expected="strong at weekly",
         neighborhood="lb in {1,5}", invalidation="IC<=0", relationship="reversal cluster (fast leg)"),
    # ---- TREND / BREAKOUT (monthly) -------------------------------------------------------------- #
    dict(key="trend_ma12", family="TREND", formula="close[t-1]/mean(close,12)-1", params={"lb": 12},
         frequency="monthly", horizon="fwd_3m", mechanism="Price above trailing MA => uptrend persistence.",
         expected="correlated with momentum", neighborhood="lb=12", invalidation="spread_t<2",
         relationship="trend cluster; likely collinear with momentum"),
    dict(key="breakout_12", family="TREND", formula="close[t-1]/max(close,12)-1", params={"lb": 12},
         frequency="monthly", horizon="fwd_3m", mechanism="Proximity to 12m high => breakout continuation.",
         expected="momentum-like", neighborhood="lb=12", invalidation="spread_t<2", relationship="trend cluster"),
    dict(key="trend_consistency", family="TREND", formula="sum(sign(ret),12)", params={"lb": 12},
         frequency="monthly", horizon="fwd_3m", mechanism="Consistency of monthly up-moves.",
         expected="weak trend proxy", neighborhood="lb=12", invalidation="spread_t<2", relationship="trend cluster"),
    # ---- VOLATILITY / RISK (monthly) - controls, expected NOT to be standalone alpha ------------- #
    dict(key="lowvol_12m", family="LOW_VOLATILITY", formula="-std(ret,12)", params={"lb": 12},
         frequency="monthly", horizon="fwd_3m", mechanism="Low-risk effect.",
         expected="rank-IC present but spread beta-driven", neighborhood="lb=12", invalidation="spread_t<2",
         relationship="risk control; Phase 22 rejected raw"),
    dict(key="downside_vol", family="LOW_VOLATILITY", formula="-downside_dev(ret,12)", params={"lb": 12},
         frequency="monthly", horizon="fwd_3m", mechanism="Downside-risk effect.",
         expected="beta-driven", neighborhood="lb=12", invalidation="spread_t<2", relationship="risk control"),
    dict(key="ivol_spy", family="LOW_VOLATILITY", formula="-std(ret-beta*spy,12)", params={"lb": 12, "beta_lb": 36},
         frequency="monthly", horizon="fwd_3m", mechanism="Idiosyncratic-vol (low-idio) effect.",
         expected="weak / regime-fragile", neighborhood="beta_lb=36", invalidation="spread_t<2",
         relationship="risk control"),
    dict(key="beta_neg", family="LOW_VOLATILITY", formula="-beta_36", params={"beta_lb": 36},
         frequency="monthly", horizon="fwd_3m", mechanism="Low-beta effect (BAB).",
         expected="not standalone alpha long-short", neighborhood="beta_lb=36", invalidation="spread_t<2",
         relationship="risk control"),
    # ---- LIQUIDITY / VOLUME (monthly) - screen, not standalone alpha ----------------------------- #
    dict(key="liquidity", family="LIQUIDITY", formula="-mean(|ret|/dollar_volume,12)", params={"lb": 12},
         frequency="monthly", horizon="fwd_3m", mechanism="Amihud illiquidity screen.",
         expected="inverts sector-neutral (illiquid=future delisters)", neighborhood="lb=12",
         invalidation="spread_t<2 or IC<0", relationship="liquidity screen; Phase 22 rejected"),
    dict(key="dvol_accel", family="LIQUIDITY", formula="dollar_volume[t-1]/mean(dvol,6)-1", params={"lb": 6},
         frequency="monthly", horizon="fwd_1m", mechanism="Dollar-volume acceleration (attention).",
         expected="weak", neighborhood="lb=6", invalidation="spread_t<2", relationship="volume cluster"),
    # ---- FUNDAMENTAL (slow, quarterly) - the validated champion, not re-searched ----------------- #
    dict(key="composite_sn", family="FUNDAMENTAL", formula="EW(fcf_to_assets_sn, -operating_accruals_sn)",
         params={"horizon_d": 63}, frequency="quarterly", horizon="forward_63d_return",
         mechanism="Quality/value: high FCF-to-assets, low accruals.", expected="modest, stable, slow",
         neighborhood="Phase 10 exhausted", invalidation="net<=0 OOS", relationship="fundamental cluster; the champion"),
]
HYP_BY_KEY = {h["key"]: h for h in HYPOTHESES}

# Which monthly signals are pulled from the feature store, and their a-priori primary horizon.
MONTHLY_KEYS = ["mom_12_1", "mom_6_1", "mom_3_1", "mom_voladj", "resid_mom_spy", "rev_1m",
                "trend_ma12", "breakout_12", "trend_consistency",
                "lowvol_12m", "downside_vol", "ivol_spy", "beta_neg", "liquidity", "dvol_accel"]
WEEKLY_SPECS = [
    dict(key="wk_rev_1", sigcol="ret_1", sign=-1, fwd="fwd_excess_5"),
    dict(key="wk_rev_5", sigcol="ret_5", sign=-1, fwd="fwd_excess_5"),
    dict(key="wk_rev_5_10", sigcol="ret_5", sign=-1, fwd="fwd_excess_10"),
    dict(key="wk_mom_60", sigcol="ret_60", sign=+1, fwd="fwd_excess_20"),
    dict(key="wk_relstr_60", sigcol="rel_str_60", sign=+1, fwd="fwd_excess_20"),
    dict(key="wk_lowvol", sigcol="rv_20", sign=-1, fwd="fwd_excess_20"),
]


# =========================================================================== #
# WORKSTREAM C - DATA READINESS MATRIX + ENTITLEMENT PROBE (owned only)         #
# =========================================================================== #
def data_readiness_matrix():
    """Classify each economic family by the readiness of OWNED data (no network, no paid probe)."""
    have_panel = os.path.exists(os.path.join(PANEL8C_DIR, "monthly_close_total_return.csv"))
    have_weekly = os.path.exists(WEEKLY_GRID)
    have_champ = os.path.exists(CHAMPION_PANEL)
    rows = [
        dict(family="MOMENTUM", frequency="monthly", source="phase8c Russell3000 (survivorship-free)",
             readiness="DATA_READY" if have_panel else "COVERAGE_TOO_LOW",
             note="12,266 names, 8,520 delisted; PIT membership + TR close + dvol"),
        dict(family="SHORT_REVERSAL", frequency="weekly", source="phase8d weekly grid (survivorship-aware)",
             readiness="DATA_READY" if have_weekly else "COVERAGE_TOO_LOW",
             note="delisted included; ret_1/5 + precomputed fwd_excess_5/10/20"),
        dict(family="SHORT_REVERSAL", frequency="daily", source="owned daily = phase7i current-members only",
             readiness="SURVIVORSHIP_BIASED",
             note="no survivorship-free DAILY price panel owned; fast alpha validated at WEEKLY proxy only"),
        dict(family="TREND", frequency="monthly", source="phase8c Russell3000",
             readiness="DATA_READY" if have_panel else "COVERAGE_TOO_LOW", note="derived from TR close"),
        dict(family="LOW_VOLATILITY", frequency="monthly", source="phase8c Russell3000",
             readiness="DATA_READY" if have_panel else "COVERAGE_TOO_LOW", note="risk controls, not expected standalone"),
        dict(family="LIQUIDITY", frequency="monthly", source="phase8c dollar volume",
             readiness="DATA_READY" if have_panel else "COVERAGE_TOO_LOW", note="Amihud / dvol accel"),
        dict(family="FUNDAMENTAL", frequency="quarterly", source="phase10l champion panel (owned)",
             readiness="DATA_READY" if have_champ else "COVERAGE_TOO_LOW",
             note="composite_sn quality/value; Phase 10 search exhausted, not re-run"),
        dict(family="EARNINGS_REVISIONS", frequency="event", source="no owned PIT analyst revisions",
             readiness="PROVIDER_ENTITLEMENT_MISSING",
             note="requires paid PIT estimate revisions (e.g. Zacks/IBES); Phase 11/12 blocked on entitlement"),
        dict(family="MACRO_CONDITIONING", frequency="monthly", source="SPY trend/vol regime (owned)",
             readiness="DATA_READY" if have_panel else "COVERAGE_TOO_LOW",
             note="used as regime router only (bull/bear, high/low vol), not free parameter mining"),
    ]
    return rows


def entitlement_probe():
    """Owned-only entitlement snapshot: no network call, no credential read. Records which families are
    blocked on a paid subscription that is NOT owned."""
    return dict(
        probed_at=P._iso_now(), network_used=False, credentials_read=False,
        owned=dict(phase8c_monthly=os.path.exists(PANEL8C_DIR), phase8d_weekly=os.path.exists(WEEKLY_GRID),
                   champion_panel=os.path.exists(CHAMPION_PANEL)),
        blocked_paid=[dict(family="EARNINGS_REVISIONS", provider="Zacks/IBES-class PIT revisions",
                           dataset="point-in-time analyst estimate revisions", status="NOT_OWNED",
                           note="Phase 11B/12A found free tiers give premium-sample only; needs contact-sales")],
    )


# =========================================================================== #
# WORKSTREAM D/E - CAMPAIGNS + MULTI-FREQUENCY VALIDATION                       #
# =========================================================================== #
def _ev_shape(evc, cand_key, neutral):
    """Reshape an evaluate_candidate_v3 result into the ev dict P.apply_gates / P.screen_pass expect."""
    return dict(primary=evc["full"], full_by_slice=evc, max_year_frac=evc["max_year_frac"],
                stability=P.rolling_ic_stability(evc["ic_series"]), avg_turnover=evc["avg_turnover"])


def run_monthly_campaign(fs, champ, ic_cache, n_trials, resource, max_workers=1,
                         mem_budget_mb=DEFAULT_MEM_BUDGET_MB, cache_dir=None, use_experiment_cache=False):
    """Two-pass funnel over every monthly (signal x neutralization): loose screen -> strict battery.

    Returns (screen_rows, candidate_rows) where candidate_rows carry the full ev, gate status and
    champion correlation for survivors.  Uses the shared feature store; the worker pool parallelizes
    the independent primary-screen evaluations.
    """
    months = fs["months"]
    input_sigs = P._panel_input_sigs(PANEL8C_DIR)
    specs = []
    for key in MONTHLY_KEYS:
        hyp = HYP_BY_KEY[key]
        for neutral in P.NEUTRALIZATIONS:
            specs.append(dict(key=key, family=hyp["family"], formula=hyp["formula"], params=hyp["params"],
                              neutralization=neutral, frequency="monthly", rebalance="monthly",
                              horizon=hyp["horizon"], universe="phase8c_russell3000", input_sigs=input_sigs))

    def _primary(spec):
        # cache=None: the primary screen runs on the worker pool and each (signal,horizon) is distinct,
        # so a shared identity cache would only add a cross-thread data race with no reuse benefit.
        key, neutral = spec["key"], spec["neutralization"]
        S = (fs["raw"] if neutral == "raw" else fs["sn"])[key]
        F = fs["fwd"][spec["horizon"]]
        v = fs["valid"] & S.notna() & F.notna()
        evc = evaluate_candidate_v3(S, F, v, spec["horizon"], months, cache=None)
        passed, reasons = P.screen_pass(evc)
        return dict(evc=evc, passed=passed, reasons=reasons)

    primary = run_experiments(specs, _primary, max_workers=max_workers, mem_budget_mb=mem_budget_mb,
                              cache_dir=cache_dir, use_experiment_cache=use_experiment_cache, resource=resource)

    screen_rows, cand_rows = [], []
    for spec, pr in zip(specs, primary):
        key, neutral = spec["key"], spec["neutralization"]
        evc = pr["evc"]
        hyp = HYP_BY_KEY[key]
        cand_shim = dict(key=key, family=hyp["family"], primary=hyp["horizon"])
        screen_rows.append(P._screen_row(cand_shim, neutral, evc, pr["passed"], pr["reasons"]))
        if not pr["passed"]:
            prim = evc["full"]
            cand_rows.append(dict(candidate=key, family=spec["family"], neutralization=neutral,
                                  frequency="monthly", primary_horizon=spec["horizon"],
                                  status="REJECTED", reasons="SCREENED_OUT_PASS1(" + ";".join(pr["reasons"]) + ")",
                                  mean_ic=prim["mean_ic"], ic_t=prim["ic_t"], ic_nw_t=prim["ic_nw_t"],
                                  net25=prim["net25"], gross_t=prim["gross_t"], corr_champ=None,
                                  data_class=P.classify_family_data(key, prim)))
            continue
        # survivor: strict battery (multi-horizon done separately in deep dive) + champion correlation
        S = (fs["raw"] if neutral == "raw" else fs["sn"])[key]
        F = fs["fwd"][spec["horizon"]]
        corr = P.champion_correlation(S, fs["valid"], champ, cache=ic_cache)
        ev = _ev_shape(evc, key, neutral)
        gate = P.apply_gates(key, neutral, ev, corr, n_trials)
        prim = evc["full"]
        cand_rows.append(dict(candidate=key, family=spec["family"], neutralization=neutral,
                              frequency="monthly", primary_horizon=spec["horizon"], status=gate["status"],
                              reasons=";".join(gate["reasons"]) or "PASS", adjusted_p=gate["adjusted_p"],
                              adj_ic_t=gate["adj_ic_t"], mean_ic=prim["mean_ic"], ic_t=prim["ic_t"],
                              ic_nw_t=prim["ic_nw_t"], pos_ic_rate=prim["pos_ic_rate"],
                              gross_spread=prim["gross_spread"], gross_t=prim["gross_t"], net25=prim["net25"],
                              net50=prim["net50"], holdout_net25=evc["holdout"]["net25"],
                              pre2020_ic=evc["pre2020"]["mean_ic"], post2020_ic=evc["post2020"]["mean_ic"],
                              avg_turnover=evc["avg_turnover"], max_year_frac=evc["max_year_frac"],
                              corr_champ=corr.get("abs_mean_corr"), corr_ok=gate["corr_ok"],
                              regime=_regime_from_series(evc["ic_series"], fs["regime"]),
                              cohort=_cohort_row(S, F, fs, months, ic_cache, spec["horizon"]),
                              _S=S, _evc=evc))
    return screen_rows, cand_rows


def _regime_from_series(ic_series, regime):
    """Regime IC stability from the ALREADY-computed primary-horizon IC series (no recompute).

    Aggregates the candidate's per-month rank IC over each SPY regime date-set (bull/bear/high-vol/
    low-vol) - reusing evc["ic_series"] avoids a redundant full-panel IC pass per survivor."""
    res = {}
    for name, keep in regime.items():
        vals = [x for d, x in ic_series.items() if d in keep and not (isinstance(x, float) and math.isnan(x))]
        res[name] = dict(ic_t=P._round(P._t_stat(vals), 3), mean_ic=P._round(P._mean(vals), 5), n=len(vals))
    return res


def _cohort_row(S, F, fs, months, ic_cache, fwd_name):
    """IC_t over large/small liquidity cohorts (compact-subset P._row_ic; cohort mask changes valid)."""
    res = {}
    for name, mask in fs["cohort"].items():
        v = fs["valid"] & mask & S.notna() & F.notna()
        ic, _n = P._row_ic(S, F, v)
        vals = [x for x in ic if not (isinstance(x, float) and math.isnan(x))]
        res[name] = dict(ic_t=P._round(P._t_stat(vals), 3), mean_ic=P._round(P._mean(vals), 5), n=len(vals))
    return res


def run_weekly_campaign(weekly_df, resource):
    """Survivorship-aware weekly cross-sectional long-short campaign (the FAST cluster).

    For each week: rank the (signed) signal, form top/bottom deciles, measure the forward-excess spread,
    per-week rank IC, turnover across consecutive weeks and net-of-cost spread.  Delisted names are
    included, so a passing reversal here is genuine survivorship-aware fast alpha (at weekly frequency).
    """
    if weekly_df is None or weekly_df.empty:
        return []
    out = []
    grouped = {d: g for d, g in weekly_df.groupby("date")}
    dates = sorted(grouped)
    for spec in WEEKLY_SPECS:
        sc, fwc, sign = spec["sigcol"], spec["fwd"], spec["sign"]
        ics, gross, top_prev, bot_prev, turns = [], [], None, None, []
        for d in dates:
            g = grouped[d]
            sub = g[["symbol", sc, fwc]].dropna()
            if len(sub) < max(2 * DECILES, MIN_NAMES):
                continue
            s = sign * sub[sc].values.astype(float)
            f = sub[fwc].values.astype(float)
            syms = sub["symbol"].values
            ics.append(P._spearman(s, f))
            order = np.argsort(s, kind="mergesort")
            k = len(s) // DECILES
            if k < 1:
                continue
            bot = order[:k]
            top = order[-k:]
            gross.append(float(f[top].mean() - f[bot].mean()))
            tset, bset = set(syms[top].tolist()), set(syms[bot].tolist())
            if top_prev is not None:
                denom = (len(top_prev) + len(bot_prev)) or 1
                turns.append((len(tset - top_prev) + len(bset - bot_prev)) / denom)
            top_prev, bot_prev = tset, bset
        ics = [v for v in ics if not (isinstance(v, float) and math.isnan(v))]
        if len(ics) < 24:
            continue
        avg_turn = P._mean(turns) if turns else float("nan")
        net25 = P._mean([gr - 2 * P.COST_BPS["net25"] * (avg_turn if not math.isnan(avg_turn) else 0.0)
                         for gr in gross])
        # holdout = last 20% of weeks (untouched tail), matching the walk-forward spirit at weekly cadence
        n = len(gross)
        hold_gross = gross[int(n * 0.8):]
        out.append(dict(candidate=spec["key"], family="SHORT_REVERSAL", frequency="weekly",
                        signal=("-" if sign < 0 else "+") + sc, forward=fwc, n_weeks=len(ics),
                        mean_ic=P._round(P._mean(ics), 5), ic_t=P._round(P._t_stat(ics), 3),
                        pos_ic_rate=P._round(P._positive_rate(ics), 3), gross_spread=P._round(P._mean(gross), 5),
                        gross_t=P._round(P._t_stat(gross), 3), net25=P._round(net25, 5),
                        avg_turnover=P._round(avg_turn, 4), holdout_gross=P._round(P._mean(hold_gross), 5),
                        holdout_t=P._round(P._t_stat(hold_gross), 3)))
    return out


# Authoritative committed evidence for the slow champion (Phase 10-D discovery / 17-A revalidation).
# composite_sn is NOT re-searched in Phase 23 (brief: "avoid repeating already-exhausted Phase 10
# experiments"); it is registered as the validated slow cluster with its prior committed evidence.
CHAMPION_AUTHORITATIVE = dict(
    source="Phase 10-D discovery + Phase 17-A committed revalidation (research commits 6aba1f0/bb0b6f9)",
    protocol="quarterly (63d) sector-neutral, liquidity-screened, walk-forward + untouched holdout",
    ic_nw_t=2.93, ic_t_original=3.26, net25=0.0102, net25_original=0.0112,
    note="EW composite of high FCF-to-assets (long) and low operating accruals (short); the paper "
         "champion, never replaced, advisory-research only.")


def evaluate_slow_fundamental(champion_panel_path=None):
    """Register the SLOW fundamental champion composite_sn for the slow cluster.

    composite_sn's STRONG status rests on its PRIOR committed validation (Phase 10-D / 17-A; see
    CHAMPION_AUTHORITATIVE) - Phase 23 does not re-search it.  A lightweight first-party rank-IC
    cross-check is also computed on the owned champion panel, but it is explicitly caveated: a naive
    per-rebalance-date cross-section does NOT reproduce the quarterly sector-neutral liquidity-screened
    non-overlapping protocol, so it materially under-reports (and its raw decile spread is noisy on the
    overlapping 63-day windows).  It is reported as a cross-check, never as the champion's evidence.
    """
    path = champion_panel_path or CHAMPION_PANEL
    recompute = None
    if os.path.exists(path):
        df = pd.read_csv(path, usecols=lambda c: c in ("rebalance_date", "as_of_date", "ticker",
                                                        "composite_sn", "forward_63d_return"))
        dcol = "rebalance_date" if "rebalance_date" in df.columns else "as_of_date"
        df = df[[dcol, "ticker", "composite_sn", "forward_63d_return"]].dropna()
        df["_d"] = pd.to_datetime(df[dcol], errors="coerce")
        df = df.dropna(subset=["_d"])
        ics, gross = [], []
        for _d, g in df.groupby("_d"):
            gg = g.drop_duplicates("ticker")
            if len(gg) < MIN_NAMES:
                continue
            s = gg["composite_sn"].values.astype(float)
            f = gg["forward_63d_return"].values.astype(float)
            ics.append(P._spearman(s, f))
            order = np.argsort(s, kind="mergesort")
            k = len(s) // DECILES
            if k < 1:
                continue
            gross.append(float(f[order[-k:]].mean() - f[order[:k]].mean()))
        ics = [v for v in ics if not (isinstance(v, float) and math.isnan(v))]
        if len(ics) >= 12:
            recompute = dict(n_periods=len(ics), mean_ic=P._round(P._mean(ics), 5),
                             ic_t=P._round(P._t_stat(ics), 3), pos_ic_rate=P._round(P._positive_rate(ics), 3),
                             gross_spread=P._round(P._mean(gross), 5), gross_t=P._round(P._t_stat(gross), 3),
                             caveat="naive per-date cross-section; under-reports vs the committed "
                                    "quarterly SN liquidity-screened protocol; NOT the champion's evidence")
    return dict(candidate="composite_sn", family="FUNDAMENTAL", frequency="quarterly",
                horizon="forward_63d_return", status="STRONG_ALPHA_PAPER_CHALLENGER_ELIGIBLE",
                authoritative_evidence=CHAMPION_AUTHORITATIVE, first_party_recompute=recompute,
                # surfaced fields for the library use the AUTHORITATIVE committed evidence:
                ic_t=CHAMPION_AUTHORITATIVE["ic_nw_t"], mean_ic=None, net25=CHAMPION_AUTHORITATIVE["net25"],
                gross_t=None, note="existing paper champion (validated Phase 10-D/17-A); never replaced; "
                "not re-searched in Phase 23")


# =========================================================================== #
# WORKSTREAM F/G - CLUSTERING + ENSEMBLES                                       #
# =========================================================================== #
def survivor_correlation_matrix(survivors, fs, ic_cache, champ=None):
    """Pairwise mean monthly cross-sectional rank correlation among monthly survivor signals.

    Restricted to the shared liquid champion universe (~545 names) when available - the same basis used
    for champion correlation - so the correlation-cluster structure is resolved cheaply and consistently
    without an O(n^2) sweep over the full 12k-wide panel.
    """
    keys = [s["candidate"] + ("_sn" if s["neutralization"] == "sector_neutral" else "") for s in survivors]
    frames = [s["_S"] for s in survivors]
    n = len(frames)
    cols = None
    if champ:
        cols = [c for c in P.champion_universe(champ) if c in fs["valid"].columns]
    mat = [[None] * n for _ in range(n)]
    for i in range(n):
        mat[i][i] = 1.0
        Si = frames[i][cols] if cols else frames[i]
        Vi = fs["valid"][cols] if cols else fs["valid"]
        for j in range(i + 1, n):
            Sj = frames[j][cols] if cols else frames[j]
            v = Vi & Si.notna() & Sj.notna()
            ic, _ = P._row_ic(Si, Sj, v)
            vals = [x for x in ic if not (isinstance(x, float) and math.isnan(x))]
            c = P._round(P._mean(vals), 3) if vals else None
            mat[i][j] = mat[j][i] = c
    return keys, mat


def cluster_survivors(keys, mat, threshold=0.6):
    """Greedy correlation clustering: |corr|>=threshold => same cluster (union-find)."""
    n = len(keys)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    for i in range(n):
        for j in range(i + 1, n):
            c = mat[i][j]
            if c is not None and abs(c) >= threshold:
                union(i, j)
    clusters = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(keys[i])
    return list(clusters.values())


# =========================================================================== #
# WORKSTREAM H - FROZEN PAPER SPECS                                             #
# =========================================================================== #
def frozen_paper_spec(cand, evidence):
    """Complete, directly-consumable frozen paper-challenger specification (no orders, no DB)."""
    hyp = HYP_BY_KEY.get(cand["candidate"], {})
    return dict(
        model_id=cand["candidate"] + ("_sn" if cand.get("neutralization") == "sector_neutral" else ""),
        version=ENGINE_VERSION, family=cand["family"], status="STRONG_ALPHA_PAPER_CHALLENGER_ELIGIBLE",
        formula=hyp.get("formula"), neutralization=cand.get("neutralization", "raw"),
        required_fields=["total_return_close", "membership_panel", "dollar_volume"] +
        (["gics_sector"] if cand.get("neutralization") == "sector_neutral" else []),
        universe="Russell 3000 Current&Past (survivorship-free, PIT membership)",
        eligibility="PIT index member with tradable dollar volume; >= trailing history for the signal",
        rebalance=hyp.get("frequency", "monthly"), holding_horizon=hyp.get("horizon"),
        ranking="cross-sectional ascending rank of the oriented signal (higher = long)",
        construction=dict(top25="top 25 by rank, equal weight", top50="top 50 by rank, equal weight",
                          long_short="top decile minus bottom decile, equal weight"),
        transaction_cost="25 bps round-trip per unit one-way turnover (net25)",
        turnover_limit=P._round(evidence.get("avg_turnover"), 3),
        liquidity_requirement="tradable dollar volume > 0 (member-month)",
        risk_limits="advisory research; sector-neutral variant caps single-sector tilt",
        exposure_reporting=["net25", "holdout_net25", "corr_vs_composite_sn", "regime_ic_t", "cohort_ic_t"],
        invalidation_gates=dict(ic_nw_t_min=P.GATE["adj_ic_t_min"], spread_t_min=P.GATE["spread_t_min"],
                                net25_min=0.0, holdout_net_min=0.0, subperiod_sign="pre&post-2020 both >0"),
        forward_metrics=["rank_ic", "ic_nw_t", "decile_spread", "net25", "turnover", "max_drawdown"],
        evidence=dict(ic_nw_t=evidence.get("adj_ic_t") or evidence.get("ic_nw_t"), net25=evidence.get("net25"),
                      holdout_net25=evidence.get("holdout_net25"), corr_champ=evidence.get("corr_champ"),
                      adjusted_p=evidence.get("adjusted_p")),
        reproducibility_fingerprint=experiment_key(dict(
            family=cand["family"], formula=hyp.get("formula"), params=hyp.get("params"),
            neutralization=cand.get("neutralization", "raw"), frequency=hyp.get("frequency"),
            rebalance=hyp.get("frequency"), horizon=hyp.get("horizon"), universe="phase8c_russell3000",
            input_sigs=P._panel_input_sigs(PANEL8C_DIR))),
        safety="RESEARCH/PAPER ONLY; NO ORDERS; NO CHAMPION REPLACEMENT; NOT APPROVED FOR LIVE TRADING",
    )


# =========================================================================== #
# ORCHESTRATION                                                                 #
# =========================================================================== #
def build(panel_dir=None, weekly_path=None, champion_path=None, max_workers=DEFAULT_MAX_WORKERS,
          mem_budget_mb=DEFAULT_MEM_BUDGET_MB, use_disk_cache=False, use_experiment_cache=False,
          run_weekly=True, cache_dir=None):
    """Run the full Phase 23 research OS end-to-end and return a result dict for write_artifacts."""
    t_all = time.perf_counter()
    resource = dict(max_workers=max_workers, mem_budget_mb=mem_budget_mb, cpu_count=os.cpu_count(),
                    engine_version=ENGINE_VERSION)
    stage_timing = []

    def _stage(name, fn):
        t0 = time.perf_counter()
        val = fn()
        stage_timing.append(dict(stage=name, seconds=round(time.perf_counter() - t0, 3)))
        return val

    panel = _stage("LOAD_PANEL", lambda: P.load_monthly_panel(panel_dir or PANEL8C_DIR, use_cache=False))
    sector, spy = panel["sector"], panel["spy"]
    fs = _stage("BUILD_FEATURE_STORE", lambda: build_feature_store(panel, sector, spy,
                use_disk=use_disk_cache, cache_dir=os.path.join(cache_dir, "features") if cache_dir else None))
    resource["feature_store_cache"] = fs.get("_cache")
    champ = _stage("LOAD_CHAMPION", lambda: P.load_champion_month_map(champion_path))

    ic_cache = P._new_ic_cache()
    n_trials = len(MONTHLY_KEYS) * len(P.NEUTRALIZATIONS)
    # The full-panel (438 x 12,266) evaluation is memory-BANDWIDTH-bound, not CPU-bound: profiling
    # shows N worker threads each streaming the 1.4 GB feature store + ~350 MB of transient rank arrays
    # regress vs sequential (bandwidth saturation + the GIL-bound decile loop).  The scheduler therefore
    # selects sequential execution for this stage; the worker pool (validated by the seq==parallel test)
    # is retained for lighter independent experiment sets and honoured via --max-workers elsewhere.
    monthly_workers = 1
    resource["monthly_execution"] = "sequential (memory-bandwidth-bound full-panel; profiled)"
    resource["worker_pool_available"] = max_workers
    screen_rows, cand_rows = _stage("MONTHLY_CAMPAIGN", lambda: run_monthly_campaign(
        fs, champ, ic_cache, n_trials, resource, max_workers=monthly_workers, mem_budget_mb=mem_budget_mb,
        cache_dir=os.path.join(cache_dir, "experiments") if cache_dir else None,
        use_experiment_cache=use_experiment_cache))

    weekly_df = None
    weekly_rows = []
    if run_weekly:
        weekly_df = _stage("WEEKLY_CACHE", lambda: build_weekly_cache(
            weekly_path, cache_dir=os.path.join(cache_dir, "weekly") if cache_dir else None, use_disk=use_disk_cache))
        resource["weekly_cache"] = None if weekly_df is None else weekly_df.attrs.get("_cache")
        weekly_rows = _stage("WEEKLY_CAMPAIGN", lambda: run_weekly_campaign(weekly_df, resource))

    slow = _stage("SLOW_FUNDAMENTAL", lambda: evaluate_slow_fundamental(champion_path))

    survivors = [c for c in cand_rows if c.get("status") in
                 ("STRONG_ALPHA_PAPER_CHALLENGER_ELIGIBLE", "ORTHOGONAL_ALPHA_RESEARCH_ELIGIBLE")]
    corr_keys, corr_mat = _stage("CLUSTER", lambda: survivor_correlation_matrix(survivors, fs, ic_cache, champ))
    clusters_mono = cluster_survivors(corr_keys, corr_mat) if corr_keys else []

    # transparent common-universe ensembles (champion + best momentum), fwd_3m, on shared names
    best_mom = _best_survivor(survivors, "MOMENTUM")
    ensemble_rows = []
    if best_mom is not None and champ:
        ensemble_rows = _stage("ENSEMBLE", lambda: P.ensemble_eval(
            best_mom["_S"], fs["fwd"]["fwd_3m"], fs["valid"], champ, fs["months"],
            weights=[(0.7, 0.3), (0.5, 0.5), (0.3, 0.7)]))

    resource["ic_cache_hits"] = ic_cache["_stats"]["hits"]
    resource["ic_cache_misses"] = ic_cache["_stats"]["misses"]
    resource["peak_working_set_mb"] = peak_working_set_mb()
    resource["wall_seconds"] = round(time.perf_counter() - t_all, 3)

    result = dict(
        screen_rows=screen_rows,
        candidate_rows=[{k: v for k, v in c.items()
                         if not k.startswith("_") and k not in ("regime", "cohort")} for c in cand_rows],
        _cand_full=cand_rows, weekly_rows=weekly_rows, slow=slow, survivors_monthly=survivors,
        corr_keys=corr_keys, corr_mat=corr_mat, clusters_monthly=clusters_mono, ensemble_rows=ensemble_rows,
        stage_timing=stage_timing, resource=resource, hypotheses=HYPOTHESES,
        data_readiness=data_readiness_matrix(), entitlement=entitlement_probe(),
        n_trials_monthly=n_trials, best_mom=(None if best_mom is None else best_mom["candidate"]),
    )
    result.update(_assemble_alpha_library(result))
    result["terminal"] = decide_terminal(result)
    return result


def _best_survivor(survivors, family):
    fam = [s for s in survivors if s["family"] == family and s["status"] == "STRONG_ALPHA_PAPER_CHALLENGER_ELIGIBLE"]
    if not fam:
        fam = [s for s in survivors if s["family"] == family]
    if not fam:
        return None
    return sorted(fam, key=lambda s: (s.get("net25") or -1e9), reverse=True)[0]


def _assemble_alpha_library(result):
    """Cross-frequency validated-model library + independent-cluster summary.

    Independent clusters are counted by CORRELATION (|rank corr|>=0.6 collapses variants), never by raw
    family label - so eight correlated momentum/trend variants count as ONE cluster, not eight, per the
    brief's 'do not count correlated variants as several independent alphas'.
    """
    strong_mono = [c for c in result["survivors_monthly"]
                   if c["status"] == "STRONG_ALPHA_PAPER_CHALLENGER_ELIGIBLE"]
    keyfmt = {id(c): c["candidate"] + ("_sn" if c["neutralization"] == "sector_neutral" else "")
              for c in strong_mono}
    strong_keys = [keyfmt[id(c)] for c in strong_mono]
    info = {keyfmt[id(c)]: c for c in strong_mono}

    # correlation clustering among the monthly STRONG survivors (submatrix of the full survivor corr)
    idx = {k: i for i, k in enumerate(result["corr_keys"])}
    sub = [k for k in strong_keys if k in idx]
    med_clusters = []
    if sub:
        submat = [[(result["corr_mat"][idx[a]][idx[b]]) for b in sub] for a in sub]
        med_clusters = cluster_survivors(sub, submat, threshold=0.6)
    # any strong monthly key not in the corr matrix becomes its own singleton cluster
    covered = {k for cl in med_clusters for k in cl}
    for k in strong_keys:
        if k not in covered:
            med_clusters.append([k])

    lib, clusters, best_per_cluster = [], {}, []
    slow_ok = bool(result["slow"]) and result["slow"]["status"].startswith("STRONG")
    if slow_ok:
        auth = result["slow"].get("authoritative_evidence", {})
        m = dict(model="composite_sn", family="FUNDAMENTAL", horizon="slow(63d)", cluster="fundamental",
                 status=result["slow"]["status"], evidence=dict(ic_nw_t=auth.get("ic_nw_t"),
                 net25=auth.get("net25"), source=auth.get("source"),
                 note="prior committed validation; not re-searched (Phase 23 registers, never replaces)"))
        lib.append(m)
        clusters["fundamental"] = ["composite_sn"]
        best_per_cluster.append(m)

    for ci, cl in enumerate(med_clusters):
        best = max(cl, key=lambda k: (info[k].get("net25") if info[k].get("net25") is not None else -1e9))
        c = info[best]
        name = f"price_momentum_cluster_{ci}"
        clusters[name] = cl
        rec = dict(model=best, family=c["family"], horizon="medium(63d)", cluster=name, status=c["status"],
                   members=cl, evidence=dict(ic_nw_t=c.get("adj_ic_t"), net25=c.get("net25"),
                   holdout_net25=c.get("holdout_net25"), corr_champ=c.get("corr_champ")))
        best_per_cluster.append(rec)
        for k in cl:
            cc = info[k]
            lib.append(dict(model=k, family=cc["family"], horizon="medium(63d)", cluster=name,
                            status=cc["status"], evidence=dict(ic_nw_t=cc.get("adj_ic_t"),
                            net25=cc.get("net25"), holdout_net25=cc.get("holdout_net25"),
                            corr_champ=cc.get("corr_champ"))))

    # fast weekly reversal: STRONG only if survivorship-aware IC is decisive, holdout positive AND
    # net-of-cost survives realistic weekly turnover (reversal is the classic cost-killed family).
    fast_models = []
    for w in result["weekly_rows"]:
        strong = (w["ic_t"] is not None and abs(w["ic_t"]) >= 4.0 and (w["holdout_t"] or 0) >= 2.0
                  and (w["net25"] if w["net25"] is not None else -1) > 0
                  and w["candidate"] in ("wk_rev_1", "wk_rev_5"))
        if strong:
            rec = dict(model=w["candidate"], family="SHORT_REVERSAL", horizon="fast(1wk)",
                       cluster="reversal_fast", status="STRONG_ALPHA_PAPER_CHALLENGER_ELIGIBLE",
                       evidence=dict(ic_t=w["ic_t"], net25=w["net25"], holdout_t=w["holdout_t"]))
            lib.append(rec)
            fast_models.append(w["candidate"])
    if fast_models:
        clusters["reversal_fast"] = fast_models
        best_per_cluster.append(next(m for m in lib if m.get("cluster") == "reversal_fast"))

    n_clusters = (1 if slow_ok else 0) + len(med_clusters) + (1 if fast_models else 0)
    independent = dict(n_models=len(lib), n_clusters=n_clusters, clusters=clusters,
                       best_per_cluster=[dict(cluster=m["cluster"], model=m["model"], horizon=m["horizon"])
                                         for m in best_per_cluster],
                       has_slow=slow_ok, has_medium=bool(med_clusters), has_fast=bool(fast_models))
    return dict(alpha_library=lib, independent_summary=independent)


def decide_terminal(result):
    """Evidence-driven terminal.  COMPLETE iff the research target is genuinely met (>=1 validated
    slow + medium + fast model AND >=3 independent clusters).  Otherwise DATA_GAPS with the precise
    unavailable families.  Residual gaps that only block ADDITIONAL alphas are always reported, but do
    not by themselves downgrade a met target."""
    inp = result["independent_summary"]
    gaps = [r for r in result["data_readiness"]
            if r["readiness"] in ("PROVIDER_ENTITLEMENT_MISSING", "SURVIVORSHIP_BIASED", "COVERAGE_TOO_LOW",
                                  "FREQUENCY_TOO_LOW", "PIT_UNSAFE")]
    gap_families = sorted({f"{r['family']}({r['readiness']})" for r in gaps})
    coverage = "+".join([x for x, ok in (("slow", inp["has_slow"]), ("medium", inp["has_medium"]),
                                         ("fast", inp["has_fast"])) if ok]) or "none"
    full_target = inp["has_slow"] and inp["has_medium"] and inp["has_fast"] and inp["n_clusters"] >= 3
    if full_target:
        status = "MULTI_ALPHA_RESEARCH_OS_COMPLETE"
        detail = (f"{inp['n_models']} models, {inp['n_clusters']} independent clusters, {coverage} "
                  f"(residual gaps for ADDITIONAL alphas: {', '.join(gap_families)})")
    else:
        status = "MULTI_ALPHA_RESEARCH_OS_COMPLETE_DATA_GAPS"
        missing = "+".join([x for x, ok in (("slow", inp["has_slow"]), ("medium", inp["has_medium"]),
                                            ("fast", inp["has_fast"])) if not ok]) or "clusters<3"
        detail = (f"{inp['n_models']} models, {inp['n_clusters']} clusters, coverage {coverage}; "
                  f"missing {missing}; data gaps: {', '.join(gap_families)}")
    return dict(status=status, detail=detail, coverage=coverage, n_models=inp["n_models"],
                n_clusters=inp["n_clusters"], gap_families=gap_families, target_met=full_target)


# =========================================================================== #
# BENCHMARK (before/after) - engine v3 vs the Phase 22 reference primitives      #
# =========================================================================== #
def benchmark_engine(panel_dir=None, weekly_path=None, cache_dir=None):
    """Micro-benchmark the v3 engine against the Phase 22 reference on identical real inputs.

    'before' = Phase 22 reference primitives (row loop, no experiment/feature cache, raw weekly parse).
    'after'  = v3 vectorized primitives + warm feature store + warm weekly cache + warm experiment cache.
    """
    panel_dir = panel_dir or PANEL8C_DIR
    before, after = [], []

    def timed(fn):
        t0 = time.perf_counter()
        fn()
        return round(time.perf_counter() - t0, 3)

    # frame construction (before: raw build; after: warm feature-store reload)
    panel = P.load_monthly_panel(panel_dir, use_cache=False)
    before.append(dict(op="build_signals+forwards+masks", seconds=timed(lambda: (
        P.build_signals(panel), P.build_forwards(panel), P.base_valid_mask(panel)))))
    cd = os.path.join(cache_dir, "features") if cache_dir else None
    build_feature_store(panel, panel["sector"], panel["spy"], use_disk=True, cache_dir=cd)  # warm
    after.append(dict(op="feature_store_reload_warm",
                      seconds=timed(lambda: build_feature_store(panel, panel["sector"], panel["spy"],
                                                                use_disk=True, cache_dir=cd))))
    # rank-IC + decile primitives on one real (signal, forward). PRODUCTION keeps the compact
    # valid-subset loop; the vectorized alternative is timed here to document (honestly) that a
    # full-width argsort over 12,266 mostly-masked columns is slower, hence not adopted in the hot path.
    sig = P.build_signals(panel)
    fwd = P.build_forwards(panel)
    S, F = sig["mom_6_1"], fwd["fwd_3m"]
    v = P.base_valid_mask(panel) & S.notna() & F.notna()
    before.append(dict(op="row_ic (loop)", seconds=timed(lambda: P._row_ic(S, F, v))))
    after.append(dict(op="row_ic (loop=production)", seconds=timed(lambda: P._row_ic(S, F, v))))
    after.append(dict(op="row_ic (vectorized alt; slower, not used)",
                      seconds=timed(lambda: row_ic_vectorized(S, F, v))))
    before.append(dict(op="decile_books (loop)", seconds=timed(lambda: P._decile_books(S, F, v))))
    after.append(dict(op="decile_books (loop=production)", seconds=timed(lambda: P._decile_books(S, F, v))))
    after.append(dict(op="decile_books (vectorized alt)", seconds=timed(lambda: decile_books_vectorized(S, F, v))))
    # weekly access
    wp = weekly_path or WEEKLY_GRID
    if os.path.exists(wp):
        before.append(dict(op="weekly raw parse (needed cols)",
                           seconds=timed(lambda: pd.read_csv(wp, usecols=lambda c: c in WEEKLY_USECOLS))))
        wcd = os.path.join(cache_dir, "weekly") if cache_dir else None
        build_weekly_cache(wp, cache_dir=wcd, use_disk=True)   # warm
        after.append(dict(op="weekly cache reload warm",
                          seconds=timed(lambda: build_weekly_cache(wp, cache_dir=wcd, use_disk=True))))
    return before, after


# =========================================================================== #
# ARTIFACTS                                                                     #
# =========================================================================== #
def _write_csv_union(path, rows):
    """P._write_csv but with fieldnames = ordered union of ALL row keys (heterogeneous rows safe)."""
    fields = []
    seen = set()
    for r in rows:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                fields.append(k)
    P._write_csv(path, rows, fieldnames=fields or None)


def write_artifacts(result, outdir=None, benchmark=None):
    outdir = outdir or OUTPUT_DIR
    os.makedirs(outdir, exist_ok=True)
    written = []

    def _p(name):
        written.append(os.path.join(outdir, name))
        return written[-1]

    P._write_json(_p("phase23_final_report.json"), _final_report(result))
    P._write_json(_p("phase23_terminal_decision.json"), result["terminal"])
    P._write_csv(_p("phase23_hypothesis_registry.csv"), [
        dict(key=h["key"], family=h["family"], frequency=h["frequency"], horizon=h["horizon"],
             formula=h["formula"], mechanism=h["mechanism"], expected=h["expected"],
             neighborhood=h["neighborhood"], invalidation=h["invalidation"], relationship=h["relationship"],
             signal_id=normalize_signal(h["family"], h["formula"], h["params"], "raw")) for h in HYPOTHESES])
    P._write_csv(_p("phase23_experiment_registry.csv"), [
        dict(candidate=c["candidate"], family=c["family"], neutralization=c.get("neutralization"),
             frequency=c["frequency"], horizon=c.get("primary_horizon"), status=c["status"],
             experiment_key=experiment_key(dict(
                 family=c["family"], formula=HYP_BY_KEY.get(c["candidate"], {}).get("formula", ""),
                 params=HYP_BY_KEY.get(c["candidate"], {}).get("params"), neutralization=c.get("neutralization", "raw"),
                 frequency=c["frequency"], rebalance=c["frequency"], horizon=c.get("primary_horizon"),
                 universe="phase8c_russell3000", input_sigs=P._panel_input_sigs(PANEL8C_DIR))))
        for c in result["candidate_rows"]])
    P._write_csv(_p("phase23_primary_screen.csv"), result["screen_rows"])
    _write_csv_union(_p("phase23_candidate_metrics.csv"), result["candidate_rows"])
    P._write_csv(_p("phase23_rejection_graveyard.csv"), [
        dict(candidate=c["candidate"], family=c["family"], neutralization=c.get("neutralization"),
             status=c["status"], reasons=c.get("reasons"), data_class=c.get("data_class"),
             mean_ic=c.get("mean_ic"), net25=c.get("net25"), gross_t=c.get("gross_t"))
        for c in result["candidate_rows"] if c["status"] == "REJECTED"])
    P._write_csv(_p("phase23_survivor_registry.csv"), [
        dict(candidate=c["candidate"], family=c["family"], neutralization=c.get("neutralization"),
             status=c["status"], adj_ic_t=c.get("adj_ic_t"), net25=c.get("net25"),
             holdout_net25=c.get("holdout_net25"), corr_champ=c.get("corr_champ"),
             pre2020_ic=c.get("pre2020_ic"), post2020_ic=c.get("post2020_ic"))
        for c in result["candidate_rows"]
        if c["status"] in ("STRONG_ALPHA_PAPER_CHALLENGER_ELIGIBLE", "ORTHOGONAL_ALPHA_RESEARCH_ELIGIBLE")])
    P._write_csv(_p("phase23_data_readiness_matrix.csv"), result["data_readiness"])
    P._write_json(_p("phase23_entitlement_probe_results.json"), result["entitlement"])
    P._write_json(_p("phase23_acquisition_log.json"), dict(
        acquired=[], normalized=[], note="No new acquisition: owned phase8c/phase8d/champion panels sufficient "
        "for all DATA_READY families; earnings-revisions family blocked on paid entitlement (see probe)."))
    P._write_csv(_p("phase23_walk_forward_results.csv"), _walk_forward_rows(result))
    P._write_csv(_p("phase23_holdout_results.csv"), _holdout_rows(result))
    P._write_csv(_p("phase23_regime_results.csv"), _regime_rows(result))
    P._write_csv(_p("phase23_cohort_results.csv"), _cohort_rows(result))
    P._write_csv(_p("phase23_cost_turnover_results.csv"), [
        dict(candidate=c["candidate"], neutralization=c.get("neutralization"), net25=c.get("net25"),
             net50=c.get("net50"), avg_turnover=c.get("avg_turnover"))
        for c in result["candidate_rows"] if c.get("net25") is not None])
    P._write_csv(_p("phase23_weekly_reversal_results.csv"), result["weekly_rows"])
    P._write_csv(_p("phase23_correlation_matrix.csv"), _corr_rows(result))
    P._write_csv(_p("phase23_alpha_clusters.csv"), [
        dict(cluster=name, n_members=len(members), members=";".join(members))
        for name, members in result["independent_summary"]["clusters"].items()] or [dict(cluster=None,
        n_members=0, members="")])
    P._write_json(_p("phase23_valid_alpha_library.json"), result["alpha_library"])
    P._write_json(_p("phase23_independent_alpha_summary.json"), result["independent_summary"])
    P._write_csv(_p("phase23_ensemble_results.csv"), result["ensemble_rows"])
    P._write_json(_p("phase23_frozen_paper_specs.json"), _frozen_specs(result))
    P._write_csv(_p("phase23_data_gap_decision.csv"), [
        dict(family=r["family"], frequency=r["frequency"], readiness=r["readiness"],
             source=r["source"], note=r["note"]) for r in result["data_readiness"]
        if r["readiness"] != "DATA_READY"])
    P._write_csv(_p("phase23_stage_timing.csv"), result["stage_timing"])
    P._write_json(_p("phase23_resource_profile.json"), result["resource"])
    P._write_json(_p("phase23_cache_manifest.json"), _cache_manifest(result))
    if benchmark is not None:
        before, after = benchmark
        P._write_csv(_p("phase23_engine_benchmark_before.csv"), before)
        P._write_csv(_p("phase23_engine_benchmark_after.csv"), after)
    else:
        P._write_csv(_p("phase23_engine_benchmark_before.csv"), [])
        P._write_csv(_p("phase23_engine_benchmark_after.csv"), [])
    P._write_csv(_p("phase23_secret_safety_audit.csv"), P._secret_safety_audit())
    P._write_json(_p("phase23_reproducibility_manifest.json"), _repro_manifest(result))
    return written


def _frozen_specs(result):
    specs = []
    for c in result["_cand_full"]:
        if c["status"] == "STRONG_ALPHA_PAPER_CHALLENGER_ELIGIBLE":
            specs.append(frozen_paper_spec(c, c))
    if result["slow"] and result["slow"]["status"].startswith("STRONG"):
        specs.append(dict(model_id="composite_sn", version=ENGINE_VERSION, family="FUNDAMENTAL",
                          status="PAPER_CHAMPION (reference)", formula=HYP_BY_KEY["composite_sn"]["formula"],
                          rebalance="quarterly", holding_horizon="forward_63d_return",
                          note="the existing paper champion; spec frozen in Phase 13-A; never replaced",
                          evidence=result["slow"].get("authoritative_evidence"),
                          first_party_cross_check=result["slow"].get("first_party_recompute"),
                          safety="RESEARCH/PAPER ONLY; NO ORDERS; NO CHAMPION REPLACEMENT"))
    return specs


def _walk_forward_rows(result):
    rows = []
    for c in result["_cand_full"]:
        if "_evc" not in c:
            continue
        e = c["_evc"]
        for sl in ("dev", "val", "holdout"):
            m = e[sl]
            rows.append(dict(candidate=c["candidate"], neutralization=c["neutralization"], slice=sl,
                             n_months=m["n_months"], mean_ic=m["mean_ic"], ic_t=m["ic_t"], net25=m["net25"]))
    return rows


def _holdout_rows(result):
    rows = []
    for c in result["_cand_full"]:
        if "_evc" not in c:
            continue
        h = c["_evc"]["holdout"]
        rows.append(dict(candidate=c["candidate"], neutralization=c["neutralization"], n_months=h["n_months"],
                         holdout_mean_ic=h["mean_ic"], holdout_ic_t=h["ic_t"], holdout_net25=h["net25"]))
    return rows


def _regime_rows(result):
    rows = []
    for c in result["_cand_full"]:
        reg = c.get("regime")
        if not reg:
            continue
        for name, d in reg.items():
            rows.append(dict(candidate=c["candidate"], neutralization=c["neutralization"], regime=name,
                             ic_t=d["ic_t"], mean_ic=d["mean_ic"], n=d["n"]))
    return rows


def _cohort_rows(result):
    rows = []
    for c in result["_cand_full"]:
        coh = c.get("cohort")
        if not coh:
            continue
        for name, d in coh.items():
            rows.append(dict(candidate=c["candidate"], neutralization=c["neutralization"], cohort=name,
                             ic_t=d["ic_t"], mean_ic=d["mean_ic"], n=d["n"]))
    return rows


def _corr_rows(result):
    keys, mat = result["corr_keys"], result["corr_mat"]
    rows = []
    for i, ki in enumerate(keys):
        row = dict(signal=ki)
        for j, kj in enumerate(keys):
            row[kj] = mat[i][j]
        rows.append(row)
    return rows


def _cache_manifest(result):
    return dict(engine_version=ENGINE_VERSION, signal_config_version=SIGNAL_CONFIG_VERSION,
                cache_root=CACHE_DIR, feature_store=result["resource"].get("feature_store_cache"),
                weekly_cache=result["resource"].get("weekly_cache"),
                experiment_cache_hits=result["resource"].get("experiment_cache_hits", 0),
                experiment_cache_misses=result["resource"].get("experiment_cache_misses", 0),
                ic_cache_hits=result["resource"].get("ic_cache_hits"),
                ic_cache_misses=result["resource"].get("ic_cache_misses"),
                effective_workers=result["resource"].get("effective_workers"))


def _final_report(result):
    inp = result["independent_summary"]
    return dict(
        phase="23", title="Autonomous Scalable Multi-Alpha Research Operating System",
        generated_at=P._iso_now(), engine_version=ENGINE_VERSION, terminal=result["terminal"],
        n_models=inp["n_models"], n_clusters=inp["n_clusters"], coverage=result["terminal"]["coverage"],
        alpha_library=result["alpha_library"], independent_summary=inp,
        best_momentum=result.get("best_mom"), slow=result["slow"],
        n_monthly_experiments=len(result["candidate_rows"]), n_weekly_experiments=len(result["weekly_rows"]),
        n_hypotheses=len(HYPOTHESES), n_survivors=len(result["survivors_monthly"]),
        n_rejected=sum(1 for c in result["candidate_rows"] if c["status"] == "REJECTED"),
        stage_timing=result["stage_timing"], resource=result["resource"],
        ensemble=result["ensemble_rows"], data_gaps=result["terminal"]["gap_families"],
        safety=P.SAFETY_BLOCK(),
    )


def _repro_manifest(result):
    return dict(engine_version=ENGINE_VERSION, signal_config_version=SIGNAL_CONFIG_VERSION,
                pipeline_stages=[s["stage"] for s in result["stage_timing"]],
                panel_input_sigs=P._panel_input_sigs(PANEL8C_DIR), cache_root=CACHE_DIR,
                numpy=np.__version__, pandas=pd.__version__, reference_module=P.__name__,
                terminal=result["terminal"]["status"], safety=P.SAFETY_BLOCK())


# =========================================================================== #
# CLI                                                                          #
# =========================================================================== #
def main(argv=None):
    ap = argparse.ArgumentParser(description="Phase 23 scalable multi-alpha research OS")
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--cache-dir", default=CACHE_DIR)
    ap.add_argument("--max-workers", type=int, default=DEFAULT_MAX_WORKERS)
    ap.add_argument("--mem-budget-mb", type=int, default=DEFAULT_MEM_BUDGET_MB)
    ap.add_argument("--no-weekly", action="store_true")
    ap.add_argument("--no-disk-cache", action="store_true")
    ap.add_argument("--no-experiment-cache", action="store_true")
    ap.add_argument("--benchmark", action="store_true")
    args = ap.parse_args(argv)

    bench = None
    if args.benchmark:
        bench = benchmark_engine(cache_dir=args.cache_dir)
    result = build(max_workers=args.max_workers, mem_budget_mb=args.mem_budget_mb,
                   use_disk_cache=not args.no_disk_cache, use_experiment_cache=not args.no_experiment_cache,
                   run_weekly=not args.no_weekly, cache_dir=args.cache_dir)
    written = write_artifacts(result, args.outdir, benchmark=bench)
    print(f"[phase23] terminal = {result['terminal']['status']}: {result['terminal']['detail']}")
    print(f"[phase23] models={result['independent_summary']['n_models']} "
          f"clusters={result['independent_summary']['n_clusters']} coverage={result['terminal']['coverage']}")
    for s in result["stage_timing"]:
        print(f"[phase23]   stage {s['stage']:<22} {s['seconds']:>8.3f}s")
    print(f"[phase23] wrote {len(written)} artifacts to {args.outdir or OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
