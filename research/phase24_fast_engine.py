"""Phase 24 - cost-aware FAST-signal engine on the survivorship-free daily panel.

Given the aligned daily panel (survivorship-free Russell 1000 Current & Past, PIT membership), this module
builds point-in-time daily features and simulates cross-sectional long-short books with the turnover
controls the brief asks for (rebalance interval / holding period, no-trade band / hysteresis, event and
liquidity conditioning, decile vs top-K construction, sector-neutralization).  It returns the full
per-window IC / gross / net / turnover streams plus turnover attribution so the validation layer can
apply the strict battery and break-even-cost analysis.

Point-in-time discipline:
  * a signal at day t is built only from closes/volumes through t;
  * the r-day forward return fwd_r[t] = close[t+r]/close[t]-1 uses only future prices (t+1..t+r);
  * membership member[t] is the as-of index flag (survivorship-free);
  * so a book formed at t from sig[t] and evaluated on fwd_r[t] has no look-ahead.

No orders, no broker, no live trading.  Owned data only.  Pure numpy/pandas.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research import run_phase22_autonomous_high_conviction_alpha_discovery as P

DECILES = 10
MIN_NAMES = 30          # min cross-section per rebalance for a large-cap decile book
COST_BPS = {"net25": 0.0025, "net50": 0.0050, "net75": 0.0075}   # round-trip proportional per turnover


# --------------------------------------------------------------------------- #
# Daily PIT feature construction                                               #
# --------------------------------------------------------------------------- #
def build_daily_features(panel):
    """Construct PIT daily feature matrices from the loaded daily panel dict.

    Returns a dict of numpy float32/'?' arrays aligned to (T days, N symbols): trailing returns at
    several look-backs (signals), r-day forward returns (evaluation), trailing dollar-volume (liquidity),
    trailing vol, a standardized abnormal-move z, a volume-surge ratio, and the per-day member mask.
    """
    close = pd.DataFrame(panel["close"], copy=False)
    dvol = pd.DataFrame(panel["dvol"], copy=False)
    member = panel["member"].astype(bool)
    T, N = close.shape

    ret1 = close.pct_change()
    feats = {"member": member, "sectors": np.asarray(panel["sectors"])}
    # trailing return look-backs (signals; higher == 'up more')
    for lb in (1, 5, 10, 21, 63):
        feats[f"ret_{lb}"] = (close.shift(1) / close.shift(1 + lb) - 1.0).to_numpy(np.float32) if lb > 1 \
            else ret1.to_numpy(np.float32)
    # NOTE ret_1 == today's realized return (known at t); ret_5.. use close.shift(1) (strictly < t) so
    # the multi-day look-backs never touch the same-day close in a way that overlaps the forward window.
    # forward r-day total returns (evaluation targets)
    for r in (1, 2, 3, 5, 10, 21):
        feats[f"fwd_{r}"] = (close.shift(-r) / close - 1.0).to_numpy(np.float32)
    # liquidity: trailing 20d average dollar volume (known through t-1 to avoid same-day contamination)
    adv20 = dvol.rolling(20, min_periods=10).mean().shift(1)
    feats["adv20"] = adv20.to_numpy(np.float32)
    # trailing realized vol (through t-1) and standardized abnormal move today
    rv20_lag = ret1.rolling(20, min_periods=10).std().shift(1)
    feats["absz"] = (ret1 / rv20_lag).to_numpy(np.float32)
    feats["rv20"] = ret1.rolling(20, min_periods=10).std().to_numpy(np.float32)
    feats["rv63"] = ret1.rolling(63, min_periods=30).std().to_numpy(np.float32)
    # volume surge ratio today vs trailing average (through t-1)
    feats["dvol_surge"] = (dvol / adv20 - 1.0).to_numpy(np.float32)
    # short continuation (skip-1) momentum
    feats["mom_21_1"] = (close.shift(1) / close.shift(22) - 1.0).to_numpy(np.float32)
    feats["mom_126_21"] = (close.shift(21) / close.shift(147) - 1.0).to_numpy(np.float32)
    feats["dates"] = panel["dates"]
    feats["N"] = N
    feats["T"] = T
    return feats


def _sector_codes(sectors):
    uniq = {s: i for i, s in enumerate(sorted(set(sectors)))}
    return np.array([uniq[s] for s in sectors], dtype=np.int32), uniq


# --------------------------------------------------------------------------- #
# Cross-sectional long-short simulator with turnover controls                  #
# --------------------------------------------------------------------------- #
def simulate(feats, *, signal, sign, fwd_r, rebalance_every=1, buffer=0.0, top_frac=0.1, top_k=None,
             event=None, event_thresh=None, min_adv=None, sector_neutral=False, embargo=5):
    """Simulate a cross-sectional decile (or top-K) equal-weight long-short book.

    signal            : feature key used to rank (e.g. 'ret_1')
    sign              : +1 keeps the signal orientation, -1 inverts it (reversal)
    fwd_r             : forward-return key defining the holding window (e.g. 'fwd_1')
    rebalance_every   : reform the book every this-many trading days (holding period, B1)
    buffer            : no-trade band width in units of top_frac (hysteresis, B1)
    top_frac / top_k  : decile (0.1) or fixed-K construction (B4)
    event             : optional feature key to condition on ('absz' extreme move, 'dvol_surge')
    event_thresh      : |event| >= thresh keeps the name (B2)
    min_adv           : minimum trailing dollar volume to be eligible (liquidity screen, B4)
    sector_neutral    : demean the signal within GICS sector each day (B3/B4)

    Returns a rich dict: per-rebalance IC / gross / net / turnover streams, rebalance dates, turnover
    attribution counts, capacity (median book ADV), and the held-name cohort tags.
    """
    S = feats[signal]
    F = feats[fwd_r]
    member = feats["member"]
    adv = feats["adv20"]
    dates = feats["dates"]
    T, N = feats["T"], feats["N"]
    r = int(rebalance_every)
    sec_codes = feats.get("_sec_codes")
    if sector_neutral and sec_codes is None:
        sec_codes, _ = _sector_codes(feats["sectors"])
        feats["_sec_codes"] = sec_codes
    ev = feats[event] if event else None

    rows = list(range(0, T, r))
    ics, gross, net25, net50, net75, turns, rdates, book_adv = [], [], [], [], [], [], [], []
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

        order = np.argsort(x, kind="mergesort")          # ascending: long = high x (end)
        k = top_k or max(1, int(len(idx) * top_frac))
        if k < 1 or 2 * k > len(idx):
            continue
        top_new = idx[order[-k:]]
        bot_new = idx[order[:k]]

        if buffer > 0 and prev_long:
            band = max(1, int(len(idx) * top_frac * (1.0 + buffer)))
            top_band = set(idx[order[-band:]].tolist())
            bot_band = set(idx[order[:band]].tolist())
            long_set = _apply_band(prev_long, set(top_new.tolist()), top_band, k, idx[order[::-1]])
            short_set = _apply_band(prev_short, set(bot_new.tolist()), bot_band, k, idx[order])
        else:
            long_set = set(top_new.tolist())
            short_set = set(bot_new.tolist())

        long_arr = np.array(sorted(long_set)); short_arr = np.array(sorted(short_set))
        gl = float(np.nanmean(F[t, long_arr])); gs = float(np.nanmean(F[t, short_arr]))
        gross.append(gl - gs)
        # one-way turnover vs previous rebalance book (fraction of each leg replaced)
        if prev_long:
            tl = len(long_set - prev_long) / max(1, len(prev_long))
            ts = len(short_set - prev_short) / max(1, len(prev_short))
            turn = 0.5 * (tl + ts)
            _attribute(prev_long - long_set, prev_elig, member, t, ev, event_thresh, attr)
            _attribute(prev_short - short_set, prev_elig, member, t, ev, event_thresh, attr)
        else:
            turn = 1.0
        turns.append(turn)
        for lst, c in ((net25, "net25"), (net50, "net50"), (net75, "net75")):
            lst.append((gl - gs) - 2 * COST_BPS[c] * turn)
        adv_book = adv[t, long_arr]
        book_adv.append(float(np.nanmedian(adv_book)) if np.isfinite(adv_book).any() else float("nan"))
        rdates.append(pd.Timestamp(dates[t]))
        prev_long, prev_short, prev_elig = long_set, short_set, elig
        n_trades += 1

    return dict(
        rebalance_dates=rdates, ic=ics, gross=gross, net25=net25, net50=net50, net75=net75,
        turnover=turns, book_adv=book_adv, n_rebalances=n_trades, attribution=attr,
        holding_days=r, embargo=embargo)


def _demean_by_group(x, codes):
    out = x.copy()
    for c in np.unique(codes):
        m = codes == c
        if m.sum() >= 3:
            out[m] = x[m] - x[m].mean()
    return out


def _apply_band(prev_set, target_set, band_set, k, ranked_best_first):
    """Hysteresis: keep previously-held names still inside the widened band, then fill up to k with the
    best-ranked fresh names.  ranked_best_first is the eligible index ordered best (most long) first."""
    keep = (prev_set & band_set)
    book = set(keep)
    for j in ranked_best_first:
        if len(book) >= k:
            break
        book.add(int(j))
    return book


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
# Metrics + strict validation over the simulated streams                       #
# --------------------------------------------------------------------------- #
def _slice_metrics(gross, net25, net50, net75, ics, keep_idx):
    g = [gross[i] for i in keep_idx]
    return dict(
        n=len(keep_idx),
        mean_ic=P._round(P._mean([ics[i] for i in keep_idx]), 5),
        ic_t=P._round(P._t_stat([ics[i] for i in keep_idx]), 3),
        gross=P._round(P._mean(g), 6), gross_t=P._round(P._t_stat(g), 3),
        net25=P._round(P._mean([net25[i] for i in keep_idx]), 6),
        net50=P._round(P._mean([net50[i] for i in keep_idx]), 6),
        net75=P._round(P._mean([net75[i] for i in keep_idx]), 6))


def evaluate_metrics(sim, nw_lag=1):
    """Full metric battery over the per-rebalance streams: significance, cost curve, turnover, holdout,
    walk-forward, subperiod, break-even cost, drawdown, concentration."""
    ic = sim["ic"]; gross = sim["gross"]
    n25 = sim["net25"]; n50 = sim["net50"]; n75 = sim["net75"]; turn = sim["turnover"]
    dates = sim["rebalance_dates"]
    n = len(gross)
    if n < 24:
        return dict(n=n, insufficient=True)
    icv = np.asarray([v for v in ic if not (isinstance(v, float) and np.isnan(v))], float)
    avg_turn = P._mean(turn)
    mean_gross = P._mean(gross)
    breakeven_bps = (mean_gross / (2 * avg_turn)) * 1e4 if avg_turn > 1e-9 else float("inf")

    full = _slice_metrics(gross, n25, n50, n75, ic, list(range(n)))
    # anchored holdout = last 20% of rebalances (untouched); dev = first 60%; val = middle 20%
    cut1, cut2 = int(n * 0.6), int(n * 0.8)
    dev = _slice_metrics(gross, n25, n50, n75, ic, list(range(0, cut1)))
    val = _slice_metrics(gross, n25, n50, n75, ic, list(range(cut1, cut2)))
    hold = _slice_metrics(gross, n25, n50, n75, ic, list(range(cut2, n)))
    # subperiods by calendar year 2020
    pre = [i for i, d in enumerate(dates) if d.year < 2020]
    post = [i for i, d in enumerate(dates) if d.year >= 2020]
    pre_m = _slice_metrics(gross, n25, n50, n75, ic, pre) if len(pre) >= 12 else None
    post_m = _slice_metrics(gross, n25, n50, n75, ic, post) if len(post) >= 12 else None
    # rolling walk-forward: 5 contiguous folds, net25 sign stability
    folds = []
    step = n // 5
    for fi in range(5):
        a = fi * step
        b = n if fi == 4 else (fi + 1) * step
        folds.append(_slice_metrics(gross, n25, n50, n75, ic, list(range(a, b))))
    wf_pos = sum(1 for fo in folds if (fo["net25"] or 0) > 0)
    # concentration: max single-year share of net25 pnl
    yearly = {}
    for d, v in zip(dates, n25):
        yearly[d.year] = yearly.get(d.year, 0.0) + v
    tot = sum(yearly.values())
    max_year_frac = (max(abs(v) for v in yearly.values()) / abs(tot)) if abs(tot) > 1e-12 and yearly else float("nan")

    return dict(
        n=n, full=full, dev=dev, val=val, holdout=hold, pre2020=pre_m, post2020=post_m,
        ic_nw_t=P._round(P._newey_west_t(icv, nw_lag), 3), pos_ic_rate=P._round(P._positive_rate(ic), 3),
        avg_turnover=P._round(avg_turn, 4), breakeven_bps=P._round(breakeven_bps, 2),
        maxdd_net25=P._round(P._max_drawdown(n25), 5), max_year_frac=P._round(max_year_frac, 3),
        wf_folds=folds, wf_pos_folds=wf_pos, n_rebalances=sim["n_rebalances"],
        median_book_adv=P._round(P._mean(sim["book_adv"]), 1), attribution=sim["attribution"],
        yearly_net25={str(k): P._round(v, 5) for k, v in sorted(yearly.items())})
