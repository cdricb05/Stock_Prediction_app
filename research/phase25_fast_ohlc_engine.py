"""Phase 25 - cost-aware FAST OHLC-signal engine on the survivorship-free daily OHLC panel.

This is NOT another close-to-close reversal tuner (Phase 24 closed that question: INFORMATION_ONLY,
COST_KILLED - do not tune again).  It uses the genuinely different information in the daily bar's
SHAPE - overnight gap vs intraday move, high-low range, close location, range compression, breakout
strength - and evaluates each mechanism under an EXPLICIT, leakage-free executable-timing convention.

Executable-timing conventions (declared per experiment; enforced by construction):

  T1  "close-signal, next-open execution"  - signal fully known at the close of day t (uses bars <= t);
      entry at the open of t+1; forward return measured FROM that entry price:
        fwd_noc[t]  = close[t+1]/open[t+1] - 1          (enter next open, exit next close - intraday)
        fwd_oo_h[t] = open[t+1+h]/open[t+1] - 1         (enter next open, hold h days, exit at open)
  T2  "open-signal, open execution"        - the overnight gap is determined AT the open print of day t;
      entry at (effectively) that same open via a market-on-open convention; forward:
        fwd_oc[t]   = close[t]/open[t] - 1              (open -> close, same day)
      Because entry uses the same print that completes the signal, T2 candidates are additionally
      stress-tested with an explicit SLIPPAGE proxy (SLIP_BPS per side) and can only qualify if the
      slippage-stressed net25 stays positive.
  T3  "close-signal, close execution (overnight hold)" - signal known at the close of day t; entry at
      (effectively) that same close; exit next open:
        fwd_co[t]   = open[t+1]/close[t] - 1            (close -> next open, overnight)
      Same same-print caveat as T2 -> same mandatory slippage stress.

  Same-day-in-and-out conventions (fwd_oc / fwd_co / fwd_noc) fully establish AND liquidate the book
  every period, so their per-rebalance turnover is 1.0 by construction (full round trip) - the cost
  model charges 2 * cost * turnover exactly as in Phase 24 (never weakened).

Point-in-time discipline is inherited from the panel (survivorship-free Russell 1000 Current & Past,
PIT membership, TOTALRETURN O/H/L/C).  The metric battery (dev/val/holdout, NW-t, walk-forward,
subperiods, break-even, concentration) is REUSED VERBATIM from phase24_fast_engine.evaluate_metrics.

No orders, no broker, no live trading.  Owned data only.  Pure numpy/pandas.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research import run_phase22_autonomous_high_conviction_alpha_discovery as P
from research.phase24_fast_engine import evaluate_metrics  # proven battery, reused verbatim

DECILES = 10
MIN_NAMES = 30
COST_BPS = {"net25": 0.0025, "net50": 0.0050, "net75": 0.0075}   # round-trip proportional per turnover
SLIP_BPS = 0.0010            # 10 bps per side slippage proxy for same-print (T2/T3) executions

TIMING_T1 = "T1_close_signal_next_open_exec"
TIMING_T2 = "T2_open_signal_open_exec_slippage_stressed"
TIMING_T3 = "T3_close_signal_close_exec_overnight_slippage_stressed"

#: forward keys that fully liquidate each period (turnover == 1.0 by construction)
FULL_LIQUIDATION_FWDS = {"fwd_oc", "fwd_co", "fwd_noc"}


# --------------------------------------------------------------------------- #
# Daily PIT OHLC feature construction                                          #
# --------------------------------------------------------------------------- #
def build_ohlc_features(panel):
    """Construct PIT daily OHLC feature + forward matrices aligned to (T days, N symbols).

    Signals are tagged by WHEN they are fully known:
      * known at the OPEN of t:  on_gap, gap_z, resid_gap, sec_resid_gap
      * known at the CLOSE of t: intraday, cloc, range_hl, range_z, compress_score, breakout5,
                                 inside_prev(open-known actually close of t-1), absz, ret5_z,
                                 rvaccel, dvol_surge, mom_126_21
    Forwards are measured from the EXECUTION price of their timing convention (see module docstring).
    """
    open_ = pd.DataFrame(panel["open"], copy=False).astype("float32")
    high = pd.DataFrame(panel["high"], copy=False).astype("float32")
    low = pd.DataFrame(panel["low"], copy=False).astype("float32")
    close = pd.DataFrame(panel["close"], copy=False).astype("float32")
    dvol = pd.DataFrame(panel["dvol"], copy=False).astype("float32")
    member = panel["member"].astype(bool)
    T, N = close.shape

    ret1 = close.pct_change()
    feats = {"member": member, "sectors": np.asarray(panel["sectors"]), "dates": panel["dates"],
             "T": T, "N": N}

    # ---- trailing context (lagged so same-day contamination is impossible) ----
    adv20 = dvol.rolling(20, min_periods=10).mean().shift(1)
    rv20_lag = ret1.rolling(20, min_periods=10).std().shift(1)
    feats["adv20"] = adv20.to_numpy(np.float32)
    feats["absz"] = (ret1 / rv20_lag).to_numpy(np.float32)               # standardized today move (close t)
    feats["dvol_surge"] = (dvol / adv20 - 1.0).to_numpy(np.float32)      # volume surge today (close t)

    # ---- overnight / intraday decomposition ----
    on_gap = open_ / close.shift(1) - 1.0                                # known at OPEN of t
    intraday = close / open_ - 1.0                                       # known at CLOSE of t
    feats["on_gap"] = on_gap.to_numpy(np.float32)
    feats["intraday"] = intraday.to_numpy(np.float32)
    feats["gap_z"] = (on_gap / rv20_lag).to_numpy(np.float32)            # gap relative to trailing vol

    # market-residual gap: subtract the cross-sectional MEMBER median gap each day
    gap_np = feats["on_gap"]
    med = np.full(T, np.nan, dtype=np.float32)
    for t in range(T):
        row = gap_np[t][member[t] & np.isfinite(gap_np[t])]
        if row.size >= MIN_NAMES:
            med[t] = np.median(row)
    feats["mkt_gap"] = med
    feats["resid_gap"] = (gap_np - med[:, None]).astype(np.float32)

    # sector-residual gap (within-sector demeaned, per day)
    sec_codes, _ = _sector_codes(feats["sectors"])
    feats["_sec_codes"] = sec_codes
    sec_resid = gap_np.copy()
    for c in np.unique(sec_codes):
        colm = sec_codes == c
        if colm.sum() < 5:
            continue
        sub = gap_np[:, colm]
        with np.errstate(invalid="ignore"):
            smed = np.nanmedian(np.where(member[:, colm], sub, np.nan), axis=1)
        sec_resid[:, colm] = sub - smed[:, None]
    feats["sec_resid_gap"] = sec_resid.astype(np.float32)

    # ---- range / breakout family (all known at close t) ----
    range_hl = ((high - low) / close).astype("float32")
    feats["range_hl"] = range_hl.to_numpy(np.float32)
    range_ma_lag = range_hl.rolling(20, min_periods=10).mean().shift(1)
    feats["range_z"] = (range_hl / range_ma_lag - 1.0).to_numpy(np.float32)
    hl_span = (high - low)
    cloc = ((close - low) / hl_span).where(hl_span > 0)
    feats["cloc"] = cloc.to_numpy(np.float32)                            # close location in range [0,1]
    r5 = range_hl.rolling(5, min_periods=3).mean()
    r60 = range_hl.rolling(60, min_periods=30).mean()
    feats["compress_score"] = (r60 / r5 - 1.0).to_numpy(np.float32)      # higher == more compressed now
    prior_high5 = high.shift(1).rolling(5, min_periods=3).max()
    feats["breakout5"] = ((close - prior_high5) / (rv20_lag * close)).to_numpy(np.float32)
    inside = ((high < high.shift(1)) & (low > low.shift(1))).astype("float32")
    feats["inside_prev"] = inside.shift(1).to_numpy(np.float32)          # yesterday was an inside day

    # ---- shock / vol-dynamics family ----
    ret5 = close / close.shift(5) - 1.0
    feats["ret5_z"] = (ret5 / (rv20_lag * np.sqrt(5.0))).to_numpy(np.float32)
    rv5 = ret1.rolling(5, min_periods=3).std()
    rv60 = ret1.rolling(60, min_periods=30).std()
    feats["rvaccel"] = (rv5 / rv60).to_numpy(np.float32)
    feats["mom_126_21"] = (close.shift(21) / close.shift(147) - 1.0).to_numpy(np.float32)

    # ---- forward returns per executable-timing convention ----
    feats["fwd_oc"] = intraday.to_numpy(np.float32)                      # T2: open t -> close t
    feats["fwd_co"] = (open_.shift(-1) / close - 1.0).to_numpy(np.float32)   # T3: close t -> open t+1
    feats["fwd_noc"] = (close.shift(-1) / open_.shift(-1) - 1.0).to_numpy(np.float32)  # T1 intraday
    for h in (1, 2, 3, 5, 10, 21):
        feats[f"fwd_oo_{h}"] = (open_.shift(-1 - h) / open_.shift(-1) - 1.0).to_numpy(np.float32)
    return feats


def _sector_codes(sectors):
    uniq = {s: i for i, s in enumerate(sorted(set(sectors)))}
    return np.array([uniq[s] for s in sectors], dtype=np.int32), uniq


# --------------------------------------------------------------------------- #
# Cross-sectional long-short simulator (same stream contract as Phase 24)      #
# --------------------------------------------------------------------------- #
def simulate(feats, *, signal, sign, fwd_r, rebalance_every=1, top_frac=0.1, top_k=None,
             event=None, event_thresh=None, min_adv=None, sector_neutral=False, embargo=5):
    """Simulate a cross-sectional decile (or top-K) equal-weight long-short book on OHLC features.

    Forward keys in FULL_LIQUIDATION_FWDS establish AND liquidate the whole book every period, so
    turnover is 1.0 per rebalance by construction (complete round trip).  Multi-day open-to-open
    holds use name-churn turnover exactly like Phase 24.  Returns the Phase-24-shaped stream dict
    (plus slippage-stressed net streams) so phase24_fast_engine.evaluate_metrics applies verbatim.
    """
    S = feats[signal]
    F = feats[fwd_r]
    member = feats["member"]
    adv = feats["adv20"]
    dates = feats["dates"]
    T, N = feats["T"], feats["N"]
    r = int(rebalance_every)
    full_liq = fwd_r in FULL_LIQUIDATION_FWDS
    sec_codes = feats.get("_sec_codes")
    if sector_neutral and sec_codes is None:
        sec_codes, _ = _sector_codes(feats["sectors"])
        feats["_sec_codes"] = sec_codes
    ev = feats[event] if event else None

    rows = list(range(0, T, r))
    ics, gross, net25, net50, net75, net25_slip, turns, rdates, book_adv = [], [], [], [], [], [], [], [], []
    attr = {"rank_crossing": 0, "left_membership": 0, "missing_price": 0, "event_dropped": 0, "total_out": 0}
    prev_long, prev_short = set(), set()
    prev_elig = None
    n_trades = 0

    for t in rows:
        elig = member[t] & np.isfinite(S[t]) & np.isfinite(F[t])
        if min_adv is not None:
            elig &= np.isfinite(adv[t]) & (adv[t] >= min_adv)
        if ev is not None and event_thresh is not None:
            elig &= np.isfinite(ev[t]) & (np.abs(ev[t]) >= event_thresh)
        idx = np.where(elig)[0]
        if len(idx) < max(MIN_NAMES, 2 * DECILES):
            continue
        x = (sign * S[t, idx]).astype(np.float64)
        if sector_neutral:
            codes = sec_codes[idx]
            x = _demean_by_group(x, codes)
        f = F[t, idx].astype(np.float64)
        ics.append(P._spearman(x, f))

        order = np.argsort(x, kind="mergesort")
        k = top_k or max(1, int(len(idx) * top_frac))
        if k < 1 or 2 * k > len(idx):
            continue
        long_set = set(idx[order[-k:]].tolist())
        short_set = set(idx[order[:k]].tolist())

        long_arr = np.array(sorted(long_set)); short_arr = np.array(sorted(short_set))
        gl = float(np.nanmean(F[t, long_arr])); gs = float(np.nanmean(F[t, short_arr]))
        g = gl - gs
        gross.append(g)
        if full_liq:
            turn = 1.0                              # complete entry + exit every period
        elif prev_long:
            tl = len(long_set - prev_long) / max(1, len(prev_long))
            ts = len(short_set - prev_short) / max(1, len(prev_short))
            turn = 0.5 * (tl + ts)
            _attribute(prev_long - long_set, prev_elig, member, t, ev, event_thresh, attr)
            _attribute(prev_short - short_set, prev_elig, member, t, ev, event_thresh, attr)
        else:
            turn = 1.0
        turns.append(turn)
        net25.append(g - 2 * COST_BPS["net25"] * turn)
        net50.append(g - 2 * COST_BPS["net50"] * turn)
        net75.append(g - 2 * COST_BPS["net75"] * turn)
        net25_slip.append(g - 2 * (COST_BPS["net25"] + SLIP_BPS) * turn)
        adv_book = adv[t, long_arr]
        book_adv.append(float(np.nanmedian(adv_book)) if np.isfinite(adv_book).any() else float("nan"))
        rdates.append(pd.Timestamp(dates[t]))
        prev_long, prev_short, prev_elig = long_set, short_set, elig
        n_trades += 1

    return dict(
        rebalance_dates=rdates, ic=ics, gross=gross, net25=net25, net50=net50, net75=net75,
        net25_slip=net25_slip, turnover=turns, book_adv=book_adv, n_rebalances=n_trades,
        attribution=attr, holding_days=r, embargo=embargo, full_liquidation=full_liq)


def _demean_by_group(x, codes):
    out = x.copy()
    for c in np.unique(codes):
        m = codes == c
        if m.sum() >= 3:
            out[m] = x[m] - x[m].mean()
    return out


def _attribute(departed, prev_elig, member, t, ev, event_thresh, attr):
    for j in departed:
        attr["total_out"] += 1
        if not member[t, j]:
            attr["left_membership"] += 1
        elif ev is not None and event_thresh is not None and not (np.isfinite(ev[t, j]) and abs(ev[t, j]) >= event_thresh):
            attr["event_dropped"] += 1
        else:
            attr["rank_crossing"] += 1


# --------------------------------------------------------------------------- #
# Extra metrics on top of the reused Phase 24 battery                          #
# --------------------------------------------------------------------------- #
def evaluate_ohlc_metrics(sim, nw_lag=1):
    """Phase 24 battery verbatim + the slippage-stressed net25 mean (for T2/T3 qualification)."""
    m = evaluate_metrics(sim, nw_lag=nw_lag)
    slip = sim.get("net25_slip") or []
    if slip:
        m["net25_slip10"] = P._round(P._mean(slip), 6)
        n = len(slip)
        if n >= 24:
            cut2 = int(n * 0.8)
            m["holdout_net25_slip10"] = P._round(P._mean(slip[cut2:]), 6)
    m["full_liquidation"] = bool(sim.get("full_liquidation"))
    return m


def regime_slices(sim, feats):
    """Bull/bear (universe cum-return vs its 200d trail) and high/low-vol regime net25 slices."""
    dates = feats["dates"]
    member = feats["member"]
    # universe daily equal-weight return + trailing state, computed once and cached on feats
    if "_regime_tags" not in feats:
        close_ret = feats.get("_uni_ret")
        if close_ret is None:
            # reconstruct from fwd_oo_1 alignment-free: use member median of intraday+gap as daily proxy
            proxy = np.where(member, np.nan_to_num(feats["on_gap"], nan=0.0) +
                             np.nan_to_num(feats["intraday"], nan=0.0), np.nan)
            with np.errstate(invalid="ignore"):
                close_ret = np.nanmedian(proxy, axis=1)
            feats["_uni_ret"] = close_ret
        cum = np.cumsum(np.nan_to_num(close_ret, nan=0.0))
        ma200 = pd.Series(cum).rolling(200, min_periods=50).mean().to_numpy()
        bull = cum >= ma200
        with np.errstate(invalid="ignore"):
            uni_rv = pd.Series(close_ret).rolling(20, min_periods=10).std().to_numpy()
        med_rv = np.nanmedian(uni_rv)
        highvol = uni_rv >= med_rv
        feats["_regime_tags"] = {"bull": bull, "highvol": highvol}
    tags = feats["_regime_tags"]
    date_index = {pd.Timestamp(d): i for i, d in enumerate(feats["dates"])}
    out = {}
    for name, mask in (("bull", tags["bull"]), ("bear", ~tags["bull"]),
                       ("highvol", tags["highvol"]), ("lowvol", ~tags["highvol"])):
        keep = [i for i, d in enumerate(sim["rebalance_dates"]) if mask[date_index.get(d, 0)]]
        if len(keep) >= 12:
            out[name] = {"n": len(keep),
                         "net25": P._round(P._mean([sim["net25"][i] for i in keep]), 6),
                         "ic": P._round(P._mean([sim["ic"][i] for i in keep if sim["ic"][i] is not None
                                                 and not np.isnan(sim["ic"][i])]), 5)}
        else:
            out[name] = {"n": len(keep), "net25": None, "ic": None}
    return out


def cohort_slices(feats, spec, current_members):
    """IC computed separately on the currently-listed vs delisted/removed cohorts (survivorship check)."""
    S = feats[spec["signal"]]
    F = feats[spec["fwd"]]
    member = feats["member"]
    T = feats["T"]
    symbols = feats.get("_symbols")
    is_current = feats.get("_is_current")
    if is_current is None:
        return {}
    out = {}
    for name, colmask in (("current", is_current), ("delisted", ~is_current)):
        ics = []
        for t in range(0, T, max(1, spec.get("rebalance_every", 1) * 5)):   # sampled for speed
            elig = member[t] & np.isfinite(S[t]) & np.isfinite(F[t]) & colmask
            idx = np.where(elig)[0]
            if len(idx) < MIN_NAMES:
                continue
            ic = P._spearman((spec["sign"] * S[t, idx]).astype(np.float64), F[t, idx].astype(np.float64))
            if ic is not None and not np.isnan(ic):
                ics.append(ic)
        out[name] = {"n": len(ics), "mean_ic": P._round(P._mean(ics), 5) if ics else None,
                     "ic_t": P._round(P._t_stat(ics), 3) if len(ics) > 3 else None}
    return out


def signal_rank_correlation(feats, spec_a, spec_b, sample_every=21):
    """Mean cross-sectional Spearman between two candidate signals (sampled days)."""
    Sa, sa = feats[spec_a["signal"]], spec_a["sign"]
    Sb, sb = feats[spec_b["signal"]], spec_b["sign"]
    member = feats["member"]
    T = feats["T"]
    cors = []
    for t in range(0, T, sample_every):
        elig = member[t] & np.isfinite(Sa[t]) & np.isfinite(Sb[t])
        idx = np.where(elig)[0]
        if len(idx) < MIN_NAMES:
            continue
        c = P._spearman((sa * Sa[t, idx]).astype(np.float64), (sb * Sb[t, idx]).astype(np.float64))
        if c is not None and not np.isnan(c):
            cors.append(c)
    return P._round(P._mean(cors), 4) if cors else None
