"""Phase 24 - survivorship-free DAILY point-in-time panel (Norgate Russell 1000 Current & Past).

This module has TWO halves that are deliberately decoupled:

  * ``build_daily_panel_from_norgate`` - the ONE-TIME acquisition.  It talks to the locally-installed
    (owned) Norgate Data Director through the ``norgatedata`` package (v1.0.74, already installed - NOT
    upgraded, NOT re-installed).  It pulls total-return-adjusted daily OHLCV + Turnover (dollar volume)
    for every symbol that was EVER a Russell 1000 constituent (Current & Past = 3,597 symbols, of which
    ~2,600 are delisted/removed), together with the point-in-time index-membership timeseries, and writes
    a compact aligned NPZ plus a provenance manifest under D:.  It is resumable (per-shard checkpoints).

  * ``load_daily_panel`` - the norgate-FREE reader used by the analysis module and the tests.  It only
    reads the NPZ, so nothing downstream needs the Norgate service (or network) to run.

Survivorship treatment: the universe is the Current & Past watchlist, so delisted names are retained;
the per-date membership mask marks when each symbol was actually in the index.  Point-in-time: the close
at day t is known at t; a signal built from returns through t predicts forward returns from t+1; the
membership flag at t is the as-of index membership (Norgate's constituent timeseries is survivorship-free
and PIT).  Prices are TOTAL-RETURN adjusted (splits + dividends), consistent with the phase8c monthly and
phase8d weekly panels.

RESEARCH ONLY.  No orders, no broker, no automation, no live promotion, no DB writes.  Read-only owned
data.  No credentials are read or persisted.  No package is installed or upgraded.
"""
from __future__ import annotations

import glob
import json
import os
import pickle
import time

import numpy as np
import pandas as pd

DATA_ROOT = r"D:\Stock_Prediction_app_data"
CACHE_DIR = os.path.join(DATA_ROOT, "phase24_cache")
DAILY_DIR = os.path.join(CACHE_DIR, "daily_panel")
SHARD_DIR = os.path.join(DAILY_DIR, "shards")
NPZ_PATH = os.path.join(DAILY_DIR, "russell1000_cp_daily.npz")
MANIFEST_PATH = os.path.join(DAILY_DIR, "manifest.json")
PROGRESS_PATH = os.path.join(DAILY_DIR, "progress.json")

WATCHLIST_CP = "Russell 1000 Current & Past"
WATCHLIST_CURRENT = "Russell 1000"
INDEX_NAME = "Russell 1000"
START_DATE = "2000-01-01"
SHARD = 300

# Sector source (owned, small) - reused from the phase8d survivorship-aware weekly grid metadata.
SECTOR_META = os.path.join(DATA_ROOT, "research_panels", "phase8d_daily_conditional", "symbol_metadata.csv")


# --------------------------------------------------------------------------- #
# ACQUISITION (norgate-dependent, one-time, resumable)                         #
# --------------------------------------------------------------------------- #
def _load_progress():
    if os.path.exists(PROGRESS_PATH):
        try:
            with open(PROGRESS_PATH) as fh:
                return json.load(fh)
        except Exception:
            pass
    return {"shards_done": [], "symbols_ok": 0, "symbols_missing": []}


def _save_progress(prog):
    os.makedirs(DAILY_DIR, exist_ok=True)
    tmp = PROGRESS_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(prog, fh, indent=2)
    os.replace(tmp, PROGRESS_PATH)


def _pull_symbol(ng, sym, start_date, end_date=None):
    """Return (close, dvol, member) pandas Series for one symbol, or None if unavailable.

    ``end_date`` is the POINT-IN-TIME cutoff.  When supplied it is passed to BOTH the price
    and the index-membership series, so the symbol contributes no observation later than the
    cutoff.  A trailing truncation is applied as well, so a provider that ignores the bound
    still cannot leak a future row into the panel.
    """
    kw = {"start_date": start_date}
    if end_date:
        kw["end_date"] = end_date
    try:
        px = ng.price_timeseries(
            sym, stock_price_adjustment_setting=ng.StockPriceAdjustmentType.TOTALRETURN,
            padding_setting=ng.PaddingType.NONE, format="pandas-dataframe", **kw)
    except Exception:
        return None
    if px is None or len(px) == 0 or "Close" not in px.columns:
        return None
    if end_date:
        px = px.loc[px.index <= pd.Timestamp(end_date)]
        if len(px) == 0:
            return None
    close = px["Close"].astype("float64")
    dvol = px["Turnover"].astype("float64") if "Turnover" in px.columns else pd.Series(index=px.index, dtype="float64")
    try:
        mem = ng.index_constituent_timeseries(
            sym, INDEX_NAME, format="pandas-dataframe", **kw)
        mcol = "Index Constituent" if "Index Constituent" in mem.columns else mem.columns[-1]
        member = mem[mcol].astype("float64").reindex(close.index).fillna(0.0)
    except Exception:
        member = pd.Series(0.0, index=close.index)
    return close, dvol, member


def build_daily_panel_from_norgate(limit=None, start_date=START_DATE, force=False, log=print):
    """Pull the survivorship-free daily panel from the owned Norgate service.  Resumable via shards.

    Returns the manifest dict.  Writes NPZ + manifest under D:.  Requires the Norgate Data Director
    service (owned) to be running; import is done lazily so importing this module never needs norgate.
    """
    import norgatedata as ng  # owned adapter; lazy so tests/analysis never require it

    os.makedirs(SHARD_DIR, exist_ok=True)
    if os.path.exists(NPZ_PATH) and not force and limit is None:
        log(f"[daily] NPZ already exists at {NPZ_PATH}; use force=True to rebuild")
        return json.load(open(MANIFEST_PATH)) if os.path.exists(MANIFEST_PATH) else {}

    universe = list(ng.watchlist_symbols(WATCHLIST_CP))
    try:
        current = set(ng.watchlist_symbols(WATCHLIST_CURRENT))
    except Exception:
        current = set()
    if limit:
        universe = universe[:limit]
    n_total = len(universe)
    delisted = [s for s in universe if s not in current]
    log(f"[daily] universe={n_total} current={len(current)} delisted/removed={len(delisted)}")

    prog = _load_progress() if not force else {"shards_done": [], "symbols_ok": 0, "symbols_missing": []}
    done_shards = set(prog["shards_done"]) if not limit else set()
    missing = list(prog.get("symbols_missing", [])) if not limit else []
    t0 = time.time()
    n_ok = 0
    for si in range(0, n_total, SHARD):
        shard_id = si // SHARD
        shard_path = os.path.join(SHARD_DIR, f"shard_{shard_id:03d}.pkl")
        if shard_id in done_shards and os.path.exists(shard_path) and not force:
            continue
        batch = universe[si:si + SHARD]
        data = {}
        for sym in batch:
            res = _pull_symbol(ng, sym, start_date)
            if res is None:
                missing.append(sym)
                continue
            data[sym] = res
            n_ok += 1
        with open(shard_path + ".tmp", "wb") as fh:
            pickle.dump(data, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(shard_path + ".tmp", shard_path)
        if not limit:
            prog["shards_done"] = sorted(done_shards | {shard_id})
            prog["symbols_ok"] = prog.get("symbols_ok", 0) + len(data)
            prog["symbols_missing"] = missing
            _save_progress(prog)
            done_shards.add(shard_id)
        log(f"[daily] shard {shard_id} pulled {len(data)}/{len(batch)} (elapsed {time.time()-t0:.1f}s)")

    manifest = _assemble_npz(universe, current, delisted, missing, start_date, n_total, log)
    manifest["build_seconds"] = round(time.time() - t0, 1)
    with open(MANIFEST_PATH, "w") as fh:
        json.dump(manifest, fh, indent=2, default=str)
    return manifest


def _assemble_npz(universe, current, delisted, missing, start_date, n_total, log,
                  shard_dir=None, npz_path=None, as_of=None):
    """Assemble aligned float32 matrices from the per-shard pickles and persist a single NPZ."""
    shard_dir = shard_dir or SHARD_DIR
    npz_path = npz_path or NPZ_PATH
    close_map, dvol_map, mem_map = {}, {}, {}
    for shard_path in sorted(glob.glob(os.path.join(shard_dir, "shard_*.pkl"))):
        with open(shard_path, "rb") as fh:
            data = pickle.load(fh)
        for sym, (close, dvol, member) in data.items():
            close_map[sym] = close
            dvol_map[sym] = dvol
            mem_map[sym] = member
    symbols = sorted(close_map)
    close_df = pd.DataFrame(close_map).sort_index()
    # POINT-IN-TIME BOUND (defence in depth).  The per-symbol pull is already bounded by
    # end_date; truncating the assembled calendar here means no future row can survive
    # even if a provider ignored the bound or a stale shard was picked up.
    if as_of:
        close_df = close_df.loc[close_df.index <= pd.Timestamp(as_of)]
    cal = close_df.index
    close_df = close_df.reindex(columns=symbols)
    dvol_df = pd.DataFrame(dvol_map).reindex(index=cal, columns=symbols)
    mem_df = pd.DataFrame(mem_map).reindex(index=cal, columns=symbols).fillna(0.0)

    dates = np.array([np.datetime64(pd.Timestamp(d), "D") for d in cal])
    close = close_df.to_numpy(dtype=np.float32)
    dvol = dvol_df.to_numpy(dtype=np.float32)
    member = (mem_df.to_numpy() > 0.5).astype(np.int8)

    sectors = _sector_vector(symbols)
    os.makedirs(os.path.dirname(npz_path) or DAILY_DIR, exist_ok=True)
    np.savez_compressed(npz_path, dates=dates, symbols=np.array(symbols),
                        close=close, dvol=dvol, member=member, sectors=np.array(sectors))
    log(f"[daily] wrote NPZ {npz_path}  shape={close.shape}  size={os.path.getsize(npz_path)/1e6:.1f}MB")

    member_days = int(member.sum())
    cov = float(np.isfinite(close).mean())
    return dict(
        source="Norgate Data (owned, local NDU v1.0.74): US Equities + US Equities Delisted",
        entitlement="reachable+entitled (databases include 'US Equities Delisted'); read-only; not upgraded",
        watchlist=WATCHLIST_CP, index=INDEX_NAME, adjustment="TOTALRETURN (splits+dividends)",
        start_date=start_date, first_date=str(dates[0]), last_date=str(dates[-1]),
        n_trading_days=int(len(dates)), securities_pulled=len(symbols), universe_current_past=n_total,
        current_members=len(current), delisted_or_removed=len(delisted), symbols_missing=len(missing),
        member_symbol_days=member_days, price_coverage_fraction=round(cov, 4),
        symbol_mappings="Norgate canonical tickers (delisted retained under Current & Past watchlist)",
        missingness="NaN where a symbol had no listed price on a trading day (pre-listing/post-delisting)",
        survivorship_caveats="Universe = Russell 1000 Current & Past (delisted retained). Membership mask "
        "is PIT/survivorship-free. A fast alpha here is capacity-relevant (large-cap liquid).",
        pit_confidence="HIGH: close known at t, forward from t+1, PIT membership as-of t, TR adjustment.",
        as_of_cutoff=(str(as_of)[:10] if as_of else None),
        bounded_refresh=bool(as_of),
        npz_path=npz_path)


def _sector_vector(symbols):
    """GICS sector per symbol, joined from the owned phase8d weekly-grid metadata (best-effort)."""
    smap = {}
    if os.path.exists(SECTOR_META):
        try:
            meta = pd.read_csv(SECTOR_META)
            scol = next((c for c in meta.columns if c.lower() == "sector"), None)
            ycol = next((c for c in meta.columns if c.lower() in ("symbol", "ticker")), None)
            if scol and ycol:
                smap = dict(zip(meta[ycol].astype(str), meta[scol].astype(str)))
        except Exception:
            pass
    return [smap.get(s, "Unknown") for s in symbols]


# --------------------------------------------------------------------------- #
# BOUNDED POINT-IN-TIME REFRESH (the CONTROLLED maintenance path)              #
#                                                                              #
# ``build_daily_panel_from_norgate`` is a ONE-TIME acquisition: it returns early when the
# NPZ already exists, and it pulls to the provider's LATEST observation.  Neither property
# is usable for the operational research cycle, which must be able to rebuild the panel for
# a NAMED historical session without ever seeing data that session did not have.  Hence ONE
# extra entry point on the SAME owner (there is no second panel writer):
#
#   * the caller supplies an internal as-of cutoff (the eligible research session).  Every
#     symbol is pulled with end_date=as_of and truncated again after assembly;
#   * delisted / removed names are retained exactly as in the one-time build (the universe
#     is the Current & Past watchlist and the membership mask is per-day PIT), so the
#     refresh is survivorship-free by the same construction;
#   * quality is checked against the panel being REPLACED and the refresh FAILS CLOSED
#     (raising, writing nothing) on a short calendar, a future-dated row or lost symbols;
#   * the canonical NPZ + manifest are replaced atomically, so a failed refresh always
#     leaves the previous panel intact;
#   * it is idempotent: refreshing twice to the same cutoff reproduces the same last_date
#     and the same symbol set.
# --------------------------------------------------------------------------- #
REFRESH_SHARD_SUBDIR = "_refresh_shards"


class PanelRefreshError(RuntimeError):
    """A bounded refresh that failed its own quality contract.  Nothing was written."""

    def __init__(self, message, code="PANEL_REFRESH_FAILED", detail=None):
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


def _existing_manifest():
    if os.path.exists(MANIFEST_PATH):
        try:
            with open(MANIFEST_PATH) as fh:
                return json.load(fh)
        except Exception:
            return {}
    return {}


def refresh_daily_panel_as_of(as_of, start_date=START_DATE, log=print, limit=None):
    """Rebuild the canonical survivorship-free daily panel bounded to ``as_of``.

    ``as_of`` (YYYY-MM-DD) is the point-in-time cutoff — the eligible research session.
    Returns the new manifest.  Raises ``PanelRefreshError`` (writing nothing) when the
    refreshed panel fails its quality contract.
    """
    import norgatedata as ng  # owned adapter; lazy so importing this module never needs norgate

    if not as_of:
        raise PanelRefreshError("A bounded refresh requires an as-of cutoff.",
                                code="PANEL_REFRESH_NO_CUTOFF")
    cutoff = pd.Timestamp(str(as_of)[:10])
    prior = _existing_manifest()
    refresh_shards = os.path.join(DAILY_DIR, REFRESH_SHARD_SUBDIR)
    if os.path.isdir(refresh_shards):
        for stale in glob.glob(os.path.join(refresh_shards, "shard_*.pkl")):
            os.remove(stale)
    os.makedirs(refresh_shards, exist_ok=True)

    universe = list(ng.watchlist_symbols(WATCHLIST_CP))
    try:
        current = set(ng.watchlist_symbols(WATCHLIST_CURRENT))
    except Exception:
        current = set()
    if limit:
        universe = universe[:limit]
    n_total = len(universe)
    delisted = [s for s in universe if s not in current]
    log(f"[refresh] as_of={cutoff.date()} universe={n_total} delisted/removed={len(delisted)}")

    t0 = time.time()
    missing = []
    end_str = str(cutoff.date())
    for si in range(0, n_total, SHARD):
        shard_id = si // SHARD
        batch = universe[si:si + SHARD]
        data = {}
        for sym in batch:
            res = _pull_symbol(ng, sym, start_date, end_date=end_str)
            if res is None:
                missing.append(sym)
                continue
            data[sym] = res
        shard_path = os.path.join(refresh_shards, f"shard_{shard_id:03d}.pkl")
        with open(shard_path + ".tmp", "wb") as fh:
            pickle.dump(data, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(shard_path + ".tmp", shard_path)
        log(f"[refresh] shard {shard_id} pulled {len(data)}/{len(batch)} "
            f"(elapsed {time.time()-t0:.1f}s)")

    tmp_npz = NPZ_PATH + ".refresh.tmp.npz"
    manifest = _assemble_npz(universe, current, delisted, missing, start_date, n_total, log,
                             shard_dir=refresh_shards, npz_path=tmp_npz, as_of=end_str)
    manifest["build_seconds"] = round(time.time() - t0, 1)
    manifest["refresh_kind"] = "BOUNDED_AS_OF"
    manifest["previous_last_date"] = prior.get("last_date")
    manifest["previous_securities_pulled"] = prior.get("securities_pulled")

    # --- quality contract: fail closed, leaving the previous panel untouched ----------- #
    def _fail(code, message, **detail):
        try:
            os.remove(tmp_npz)
        except OSError:
            pass
        raise PanelRefreshError(message, code=code,
                                detail=dict(as_of=end_str, **detail))

    last = str(manifest.get("last_date") or "")[:10]
    if not last:
        _fail("SOURCE_PANEL_INCOMPLETE", "Refreshed panel has no trading days.")
    if last > end_str:
        _fail("SOURCE_PANEL_FUTURE_DATED",
              f"Refreshed panel last date {last} is later than the cutoff {end_str}.",
              last_date=last)
    if last != end_str:
        _fail("SOURCE_PANEL_INCOMPLETE",
              f"Refreshed panel reaches only {last}; the cutoff session {end_str} is not "
              f"covered by the owned provider.", last_date=last)
    prev_syms = prior.get("securities_pulled")
    if isinstance(prev_syms, int) and manifest["securities_pulled"] < prev_syms:
        _fail("HISTORICAL_UNIVERSE_COVERAGE_FAILED",
              f"Refreshed panel carries {manifest['securities_pulled']} securities, fewer "
              f"than the {prev_syms} already held; refusing to drop historical names.",
              securities_pulled=manifest["securities_pulled"], previous=prev_syms)
    prev_days = prior.get("n_trading_days")
    if isinstance(prev_days, int) and manifest["n_trading_days"] < prev_days:
        _fail("SOURCE_PANEL_INCOMPLETE",
              f"Refreshed panel carries {manifest['n_trading_days']} trading days, fewer "
              f"than the {prev_days} already held.",
              n_trading_days=manifest["n_trading_days"], previous=prev_days)

    # --- atomic promotion -------------------------------------------------------------- #
    os.replace(tmp_npz, NPZ_PATH)
    manifest["npz_path"] = NPZ_PATH
    tmp_manifest = MANIFEST_PATH + ".tmp"
    with open(tmp_manifest, "w") as fh:
        json.dump(manifest, fh, indent=2, default=str)
    os.replace(tmp_manifest, MANIFEST_PATH)
    log(f"[refresh] promoted panel bounded to {end_str} "
        f"({manifest['n_trading_days']} days, {manifest['securities_pulled']} securities)")
    return manifest


# --------------------------------------------------------------------------- #
# READER (norgate-free; used by analysis + tests)                              #
# --------------------------------------------------------------------------- #
def load_daily_panel(npz_path=None):
    """Load the aligned daily panel from NPZ.  Returns a dict of numpy arrays / index objects.

    Keys: dates (DatetimeIndex), symbols (list[str]), close/dvol (float32 [T,N]), member (int8 [T,N]),
    sectors (list[str]).  No Norgate dependency.
    """
    npz_path = npz_path or NPZ_PATH
    z = np.load(npz_path, allow_pickle=True)
    dates = pd.to_datetime([str(d) for d in z["dates"]])
    return dict(dates=dates, symbols=[str(s) for s in z["symbols"]],
                close=z["close"], dvol=z["dvol"], member=z["member"],
                sectors=[str(s) for s in z["sectors"]])


def panel_exists(npz_path=None):
    return os.path.exists(npz_path or NPZ_PATH)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Build the Phase 24 survivorship-free daily panel")
    ap.add_argument("--limit", type=int, default=None, help="only pull the first N symbols (timing test)")
    ap.add_argument("--start", default=START_DATE)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    m = build_daily_panel_from_norgate(limit=args.limit, start_date=args.start, force=args.force)
    print(json.dumps(m, indent=2, default=str))
