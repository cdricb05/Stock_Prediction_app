"""Phase 25 Track-A input emitter - momentum + risk inputs for the multi-horizon Paper Trader.

Paper Trader's operational engine is deliberately pure-stdlib (no numpy/pandas, exactly like the
existing ``api/alpha_factory``): it reads compact owned CSVs.  The *momentum* leg (``mom_6_1``) and
the *risk* diagnostics require the owned survivorship-free daily price panel, so this research-side
emitter does the numpy/pandas heavy lifting ONCE and writes small CSVs that Paper Trader consumes.

It reads ONLY the already-built, owned, Norgate-FREE Phase 24 daily NPZ (total-return Russell 1000
Current & Past, 2000-2026).  It does NOT touch the Norgate service, the network, PostgreSQL, or any
paid provider.  Outputs go under ``D:\\Stock_Prediction_app_data\\phase25_multi_horizon_alpha\\_inputs``.

Emitted artifacts:
  * current_momentum_scores.csv  - latest completed-month cross-section: mom_6_1 (skip-1-month
      6-month total-return momentum), eligibility, trailing history, liquidity, realized vol, sector.
  * momentum_monthly_panel.csv   - the full monthly panel (month, ticker, mom_6_1, fwd_1m_return,
      is_member, adv_dollar, realized_vol_63d, sector) driving the historical book reconstruction.
  * current_risk_stats.csv       - latest per-name realized vol / beta / ADV / drawdown diagnostics.
  * inputs_manifest.json         - provenance + fingerprints + counts.

mom_6_1 definition (matches the committed validated momentum model): using MONTH-END total-return
closes, ``mom_6_1 = close[m-1] / close[m-7] - 1`` - i.e. six months of momentum skipping the most
recent (possibly incomplete) month.  Point-in-time: for month-end m, mom_6_1 uses only closes through
m-1; the forward return is m -> m+1.  RESEARCH ONLY, read-only owned data, no writes outside D:.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Users\binis\Stock_Prediction_app_push")
from research.phase24_daily_panel import load_daily_panel, NPZ_PATH  # noqa: E402

DATA_ROOT = r"D:\Stock_Prediction_app_data"
OUT_DIR = os.path.join(DATA_ROOT, "phase25_multi_horizon_alpha", "_inputs")

TRADING_DAYS_YEAR = 252
VOL_WINDOW = 63          # realized-vol lookback (trading days)
ADV_WINDOW = 20          # average-dollar-volume lookback
HISTORY_WINDOW = 126     # ~6 months of trading days required for momentum eligibility
MIN_HISTORY_OBS = 120    # of the last HISTORY_WINDOW days, need >= this many real closes
BETA_WINDOW = 252
MOM_WINSOR = 3.0         # flag |mom_6_1| beyond this as an extreme (spinoff/relisting artifact)


def _fingerprint(path):
    try:
        h = hashlib.sha1()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except OSError:
        return None


def build_inputs(npz_path=None, out_dir=OUT_DIR, log=print):
    npz_path = npz_path or NPZ_PATH
    p = load_daily_panel(npz_path)
    dates, syms = p["dates"], p["symbols"]
    close = p["close"].astype("float64")
    dvol = p["dvol"].astype("float64")
    member = p["member"]
    sectors = dict(zip(syms, p["sectors"]))

    close_df = pd.DataFrame(close, index=dates, columns=syms)
    dvol_df = pd.DataFrame(dvol, index=dates, columns=syms)
    member_df = pd.DataFrame(member, index=dates, columns=syms)
    rets = close_df.pct_change()

    # --- daily rolling stats (sampled at month ends) ---------------------------------------
    adv_df = dvol_df.rolling(ADV_WINDOW, min_periods=max(5, ADV_WINDOW // 2)).mean()
    vol_df = rets.rolling(VOL_WINDOW, min_periods=max(20, VOL_WINDOW // 2)).std() * np.sqrt(TRADING_DAYS_YEAR)
    obs_df = close_df.notna().rolling(HISTORY_WINDOW, min_periods=1).sum()

    me_close = close_df.resample("ME").last()
    me_member = member_df.resample("ME").last()
    me_adv = adv_df.reindex(me_close.index, method="ffill")
    me_vol = vol_df.reindex(me_close.index, method="ffill")
    me_obs = obs_df.reindex(me_close.index, method="ffill")
    me_date = pd.Series(rets.index, index=rets.index).resample("ME").last()  # actual last trading day per month

    mom = me_close.shift(1) / me_close.shift(7) - 1.0          # mom_6_1 (skip most recent month)
    fwd1 = me_close.shift(-1) / me_close - 1.0                 # forward 1-month total return

    months = list(me_close.index)
    last_trading_date = str(rets.index[-1].date())

    # --- full monthly panel (member rows only; the PIT Russell 1000 universe) --------------
    os.makedirs(out_dir, exist_ok=True)
    panel_path = os.path.join(out_dir, "momentum_monthly_panel.csv")
    n_panel = 0
    with open(panel_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["month", "market_date", "ticker", "mom_6_1", "fwd_1m_return", "is_member",
                    "adv_dollar", "realized_vol_63d", "eligible_history", "sector"])
        for mi, m in enumerate(months):
            mlabel = f"{m.year:04d}-{m.month:02d}"
            mdate = me_date.get(m)
            mdate = str(pd.Timestamp(mdate).date()) if pd.notna(mdate) else mlabel
            mom_row = mom.loc[m]; fwd_row = fwd1.loc[m]; mem_row = me_member.loc[m]
            adv_row = me_adv.loc[m]; vol_row = me_vol.loc[m]; obs_row = me_obs.loc[m]
            for tk in syms:
                is_mem = 1 if mem_row.get(tk, 0) > 0.5 else 0
                if not is_mem:
                    continue
                mv = mom_row.get(tk)
                if mv is None or not np.isfinite(mv):
                    continue
                obs = obs_row.get(tk, 0)
                elig = 1 if (np.isfinite(obs) and obs >= MIN_HISTORY_OBS) else 0
                fv = fwd_row.get(tk)
                av = adv_row.get(tk); vv = vol_row.get(tk)
                w.writerow([mlabel, mdate, tk, round(float(mv), 8),
                            (round(float(fv), 8) if fv is not None and np.isfinite(fv) else ""),
                            is_mem,
                            (round(float(av), 2) if av is not None and np.isfinite(av) else ""),
                            (round(float(vv), 6) if vv is not None and np.isfinite(vv) else ""),
                            elig, sectors.get(tk, "Unknown")])
                n_panel += 1
    log(f"[inputs] momentum_monthly_panel.csv rows={n_panel} months={len(months)}")

    # --- current cross-section (latest completed month) ------------------------------------
    m = months[-1]
    mlabel = f"{m.year:04d}-{m.month:02d}"
    cur_path = os.path.join(out_dir, "current_momentum_scores.csv")
    n_cur = 0; n_elig = 0
    mom_row = mom.loc[m]; mem_row = me_member.loc[m]; adv_row = me_adv.loc[m]
    vol_row = me_vol.loc[m]; obs_row = me_obs.loc[m]
    with open(cur_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "mom_6_1", "is_member", "adv_dollar", "realized_vol_63d",
                    "trailing_obs_126", "eligible_history", "extreme_flag", "sector",
                    "market_as_of_date", "month_label"])
        for tk in syms:
            if mem_row.get(tk, 0) <= 0.5:
                continue
            mv = mom_row.get(tk)
            if mv is None or not np.isfinite(mv):
                continue
            obs = obs_row.get(tk, 0)
            elig = 1 if (np.isfinite(obs) and obs >= MIN_HISTORY_OBS) else 0
            extreme = 1 if abs(float(mv)) > MOM_WINSOR else 0
            av = adv_row.get(tk); vv = vol_row.get(tk)
            w.writerow([tk, round(float(mv), 8), 1,
                        (round(float(av), 2) if av is not None and np.isfinite(av) else ""),
                        (round(float(vv), 6) if vv is not None and np.isfinite(vv) else ""),
                        int(obs) if np.isfinite(obs) else 0, elig, extreme,
                        sectors.get(tk, "Unknown"), last_trading_date, mlabel])
            n_cur += 1
            n_elig += elig
    log(f"[inputs] current_momentum_scores.csv names={n_cur} eligible={n_elig} month={mlabel}")

    # --- current risk stats (all names with a recent price; beta vs equal-weight universe) --
    uni_ret = rets.mean(axis=1)                      # equal-weight universe daily return proxy
    tail = rets.tail(BETA_WINDOW)
    uni_tail = uni_ret.tail(BETA_WINDOW)
    uni_var = float(uni_tail.var())
    cov = tail.apply(lambda col: col.cov(uni_tail))
    beta = (cov / uni_var) if uni_var > 0 else cov * np.nan
    dd_window = close_df.tail(TRADING_DAYS_YEAR)
    roll_max = dd_window.cummax()
    drawdown = (dd_window / roll_max - 1.0).min()
    last_vol = vol_df.iloc[-1]
    last_adv = adv_df.iloc[-1]
    last_present = close_df.iloc[-1].notna()
    risk_path = os.path.join(out_dir, "current_risk_stats.csv")
    n_risk = 0
    with open(risk_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "realized_vol_63d", "beta_universe", "adv_dollar_20d",
                    "max_drawdown_252d", "is_current_member", "last_price_date", "sector"])
        for tk in syms:
            if not bool(last_present.get(tk, False)):
                continue
            vv = last_vol.get(tk); bv = beta.get(tk); av = last_adv.get(tk); dv = drawdown.get(tk)
            w.writerow([tk,
                        (round(float(vv), 6) if vv is not None and np.isfinite(vv) else ""),
                        (round(float(bv), 4) if bv is not None and np.isfinite(bv) else ""),
                        (round(float(av), 2) if av is not None and np.isfinite(av) else ""),
                        (round(float(dv), 6) if dv is not None and np.isfinite(dv) else ""),
                        1 if me_member.loc[m].get(tk, 0) > 0.5 else 0,
                        last_trading_date, sectors.get(tk, "Unknown")])
            n_risk += 1
    log(f"[inputs] current_risk_stats.csv names={n_risk}")

    manifest = {
        "phase": "25",
        "role": "Track A multi-horizon Paper Trader momentum + risk inputs",
        "source_npz": npz_path,
        "source_npz_fingerprint": _fingerprint(npz_path),
        "source": "owned Phase 24 survivorship-free daily NPZ (Norgate Russell 1000 Current & Past, TOTALRETURN)",
        "norgate_service_used": False,
        "market_as_of_date": last_trading_date,
        "current_month_label": mlabel,
        "fundamental_note": "composite_sn is read separately by Paper Trader from the frozen Phase 10-L panel.",
        "mom_definition": "mom_6_1 = close[m-1]/close[m-7]-1 on month-end TOTALRETURN closes (skip most recent month).",
        "eligibility": f"eligible_history requires >= {MIN_HISTORY_OBS} of the last {HISTORY_WINDOW} daily closes present.",
        "beta_proxy": "beta vs equal-weight universe daily return (owned proxy; risk diagnostic only, not a return alpha).",
        "counts": {"months": len(months), "monthly_panel_rows": n_panel,
                   "current_names": n_cur, "current_eligible": n_elig, "risk_names": n_risk},
        "outputs": ["current_momentum_scores.csv", "momentum_monthly_panel.csv",
                    "current_risk_stats.csv", "inputs_manifest.json"],
        "out_dir": out_dir,
        "safety": "READ-ONLY owned data. No orders/broker/automation/DB/prediction-service. No package install.",
    }
    with open(os.path.join(out_dir, "inputs_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    for k, v in [(k, v) for k, v in manifest["counts"].items()]:
        pass
    log(f"[inputs] wrote manifest -> {out_dir}")
    return manifest


if __name__ == "__main__":
    m = build_inputs()
    print(json.dumps(m, indent=2))
