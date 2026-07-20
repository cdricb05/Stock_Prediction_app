"""Phase 25 - survivorship-free DAILY OHLC point-in-time panel (Norgate Russell 1000 Current & Past).

This is the Track-B data foundation.  Phase 24 stored only total-return Close + dollar-volume; the
Phase 25 fast-OHLC research needs the intraday shape of each bar (Open / High / Low / Close), so this
module pulls and aligns the *full* total-return-adjusted daily bar plus dollar volume and the
point-in-time index-membership mask.

Two deliberately decoupled halves (identical design to ``phase24_daily_panel``):

  * ``build_ohlc_panel_from_norgate`` - the ONE-TIME acquisition against the locally-installed OWNED
    Norgate Data Director (``norgatedata`` v1.0.74, already installed - NOT upgraded / NOT re-installed).
    Pulls TOTALRETURN Open/High/Low/Close + Turnover(=dollar volume) for every symbol that was EVER a
    Russell 1000 constituent (Current & Past), plus the survivorship-free membership timeseries, and
    writes a compact aligned float32 NPZ + provenance manifest under D:.  Resumable per-shard.

  * ``load_ohlc_panel`` - the norgate-FREE reader used by the fast engine + tests.  Reads only the NPZ,
    so nothing downstream needs the Norgate service or the network.

Survivorship treatment: the universe is the Current & Past watchlist (delisted names retained); the
per-date membership mask marks when each symbol was actually in the index.  Point-in-time: the OHLC of
day t is known only *after* t's close; an overnight/intraday signal that is fully known at t predicts a
forward return from t+1 (or from t's own execution price, see the fast engine's executable-timing rules);
membership at t is the as-of index membership.  Prices are TOTAL-RETURN adjusted (splits + dividends),
consistent with the phase8c monthly, phase8d weekly and phase24 daily panels.

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
OUT_DIR = os.path.join(DATA_ROOT, "phase25_fast_ohlc")
PANEL_DIR = os.path.join(OUT_DIR, "ohlc_panel")
SHARD_DIR = os.path.join(PANEL_DIR, "shards")
NPZ_PATH = os.path.join(PANEL_DIR, "russell1000_cp_ohlc.npz")
MANIFEST_PATH = os.path.join(PANEL_DIR, "manifest.json")
PROGRESS_PATH = os.path.join(PANEL_DIR, "progress.json")

WATCHLIST_CP = "Russell 1000 Current & Past"
WATCHLIST_CURRENT = "Russell 1000"
INDEX_NAME = "Russell 1000"
START_DATE = "2000-01-01"
SHARD = 300

# Sector source (owned, small) - reused from the phase8d survivorship-aware weekly grid metadata.
SECTOR_META = os.path.join(DATA_ROOT, "research_panels", "phase8d_daily_conditional", "symbol_metadata.csv")

_OHLC_COLS = ("Open", "High", "Low", "Close")


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
    os.makedirs(PANEL_DIR, exist_ok=True)
    tmp = PROGRESS_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(prog, fh, indent=2)
    os.replace(tmp, PROGRESS_PATH)


def _pull_symbol(ng, sym, start_date):
    """Return (open, high, low, close, dvol, member) pandas Series for one symbol, or None.

    All prices are TOTAL-RETURN adjusted so intraday shape and forward returns are on one basis.
    """
    try:
        px = ng.price_timeseries(
            sym, stock_price_adjustment_setting=ng.StockPriceAdjustmentType.TOTALRETURN,
            padding_setting=ng.PaddingType.NONE, start_date=start_date, format="pandas-dataframe")
    except Exception:
        return None
    if px is None or len(px) == 0 or "Close" not in px.columns:
        return None
    o = px["Open"].astype("float64") if "Open" in px.columns else px["Close"].astype("float64")
    h = px["High"].astype("float64") if "High" in px.columns else px["Close"].astype("float64")
    lo = px["Low"].astype("float64") if "Low" in px.columns else px["Close"].astype("float64")
    c = px["Close"].astype("float64")
    dvol = px["Turnover"].astype("float64") if "Turnover" in px.columns else pd.Series(index=px.index, dtype="float64")
    try:
        mem = ng.index_constituent_timeseries(
            sym, INDEX_NAME, start_date=start_date, format="pandas-dataframe")
        mcol = "Index Constituent" if "Index Constituent" in mem.columns else mem.columns[-1]
        member = mem[mcol].astype("float64").reindex(c.index).fillna(0.0)
    except Exception:
        member = pd.Series(0.0, index=c.index)
    return o, h, lo, c, dvol, member


def build_ohlc_panel_from_norgate(limit=None, start_date=START_DATE, force=False, log=print):
    """Pull the survivorship-free daily OHLC panel from the owned Norgate service.  Resumable via shards.

    Returns the manifest dict.  Writes NPZ + manifest under D:.  Requires the (owned) Norgate Data
    Director service to be running; the import is lazy so importing this module never needs norgate.
    """
    import norgatedata as ng  # owned adapter; lazy so tests/analysis never require it

    os.makedirs(SHARD_DIR, exist_ok=True)
    if os.path.exists(NPZ_PATH) and not force and limit is None:
        log(f"[ohlc] NPZ already exists at {NPZ_PATH}; use force=True to rebuild")
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
    log(f"[ohlc] universe={n_total} current={len(current)} delisted/removed={len(delisted)}")

    prog = _load_progress() if not force else {"shards_done": [], "symbols_ok": 0, "symbols_missing": []}
    done_shards = set(prog["shards_done"]) if not limit else set()
    missing = list(prog.get("symbols_missing", [])) if not limit else []
    t0 = time.time()
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
        with open(shard_path + ".tmp", "wb") as fh:
            pickle.dump(data, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(shard_path + ".tmp", shard_path)
        if not limit:
            prog["shards_done"] = sorted(done_shards | {shard_id})
            prog["symbols_ok"] = prog.get("symbols_ok", 0) + len(data)
            prog["symbols_missing"] = missing
            _save_progress(prog)
            done_shards.add(shard_id)
        log(f"[ohlc] shard {shard_id} pulled {len(data)}/{len(batch)} (elapsed {time.time()-t0:.1f}s)")

    manifest = _assemble_npz(universe, current, delisted, missing, start_date, n_total, log)
    manifest["build_seconds"] = round(time.time() - t0, 1)
    with open(MANIFEST_PATH, "w") as fh:
        json.dump(manifest, fh, indent=2, default=str)
    return manifest


def _assemble_npz(universe, current, delisted, missing, start_date, n_total, log):
    """Assemble aligned float32 matrices from the per-shard pickles and persist a single NPZ."""
    o_map, h_map, l_map, c_map, dv_map, mem_map = {}, {}, {}, {}, {}, {}
    for shard_path in sorted(glob.glob(os.path.join(SHARD_DIR, "shard_*.pkl"))):
        with open(shard_path, "rb") as fh:
            data = pickle.load(fh)
        for sym, tup in data.items():
            o, h, lo, c, dvol, member = tup
            o_map[sym], h_map[sym], l_map[sym] = o, h, lo
            c_map[sym], dv_map[sym], mem_map[sym] = c, dvol, member
    symbols = sorted(c_map)
    close_df = pd.DataFrame(c_map).sort_index()
    cal = close_df.index
    close_df = close_df.reindex(columns=symbols)

    def _mat(m):
        return pd.DataFrame(m).reindex(index=cal, columns=symbols).to_numpy(dtype=np.float32)

    dates = np.array([np.datetime64(pd.Timestamp(d), "D") for d in cal])
    open_ = _mat(o_map)
    high = _mat(h_map)
    low = _mat(l_map)
    close = close_df.to_numpy(dtype=np.float32)
    dvol = _mat(dv_map)
    mem_df = pd.DataFrame(mem_map).reindex(index=cal, columns=symbols).fillna(0.0)
    member = (mem_df.to_numpy() > 0.5).astype(np.int8)

    sectors = _sector_vector(symbols)
    os.makedirs(PANEL_DIR, exist_ok=True)
    np.savez_compressed(NPZ_PATH, dates=dates, symbols=np.array(symbols),
                        open=open_, high=high, low=low, close=close, dvol=dvol,
                        member=member, sectors=np.array(sectors))
    log(f"[ohlc] wrote NPZ {NPZ_PATH}  shape={close.shape}  size={os.path.getsize(NPZ_PATH)/1e6:.1f}MB")

    member_days = int(member.sum())
    cov = float(np.isfinite(close).mean())
    # basic OHLC sanity: high>=max(open,close), low<=min(open,close) where all present
    fin = np.isfinite(open_) & np.isfinite(high) & np.isfinite(low) & np.isfinite(close)
    hi_ok = np.where(fin, high + 1e-4 >= np.maximum(open_, close), True)
    lo_ok = np.where(fin, low - 1e-4 <= np.minimum(open_, close), True)
    ohlc_consistency = float((hi_ok & lo_ok).mean())
    return dict(
        source="Norgate Data (owned, local NDU v1.0.74): US Equities + US Equities Delisted",
        entitlement="reachable+entitled (databases include 'US Equities Delisted'); read-only; not upgraded",
        watchlist=WATCHLIST_CP, index=INDEX_NAME, adjustment="TOTALRETURN (splits+dividends) on O/H/L/C",
        fields="open,high,low,close,dvol(dollar volume),member(PIT)",
        start_date=start_date, first_date=str(dates[0]), last_date=str(dates[-1]),
        n_trading_days=int(len(dates)), securities_pulled=len(symbols), universe_current_past=n_total,
        current_members=len(current), delisted_or_removed=len(delisted), symbols_missing=len(missing),
        member_symbol_days=member_days, price_coverage_fraction=round(cov, 4),
        ohlc_consistency_fraction=round(ohlc_consistency, 6),
        symbol_mappings="Norgate canonical tickers (delisted retained under Current & Past watchlist)",
        missingness="NaN where a symbol had no listed bar on a trading day (pre-listing/post-delisting)",
        survivorship_caveats="Universe = Russell 1000 Current & Past (delisted retained). Membership mask "
        "is PIT/survivorship-free. Capacity-relevant (large-cap liquid).",
        pit_confidence="HIGH: OHLC of t known after t's close; forward from t+1 (or t's own exec price per "
        "executable-timing rules); PIT membership as-of t; TR adjustment.",
        npz_path=NPZ_PATH)


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
# READER (norgate-free; used by the fast engine + tests)                       #
# --------------------------------------------------------------------------- #
def load_ohlc_panel(npz_path=None):
    """Load the aligned daily OHLC panel from NPZ.  No Norgate dependency.

    Keys: dates (DatetimeIndex), symbols (list[str]), open/high/low/close/dvol (float32 [T,N]),
    member (int8 [T,N]), sectors (list[str]).
    """
    npz_path = npz_path or NPZ_PATH
    z = np.load(npz_path, allow_pickle=True)
    dates = pd.to_datetime([str(d) for d in z["dates"]])
    return dict(dates=dates, symbols=[str(s) for s in z["symbols"]],
                open=z["open"], high=z["high"], low=z["low"], close=z["close"],
                dvol=z["dvol"], member=z["member"], sectors=[str(s) for s in z["sectors"]])


def panel_exists(npz_path=None):
    return os.path.exists(npz_path or NPZ_PATH)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Build the Phase 25 survivorship-free daily OHLC panel")
    ap.add_argument("--limit", type=int, default=None, help="only pull the first N symbols (timing test)")
    ap.add_argument("--start", default=START_DATE)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    m = build_ohlc_panel_from_norgate(limit=args.limit, start_date=args.start, force=args.force)
    print(json.dumps(m, indent=2, default=str))
