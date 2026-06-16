"""Replay the *current* production model over historical as-of dates.

This module reuses the exact model + ensemble functions from ``api_server`` so
the replay cannot drift from production. It imports ``api_server`` lazily (inside
functions) so that the pure-logic tests and the dataset builder do not require a
database connection or the full model stack.

Fidelity vs the live ``POST /predict_all_models/`` path
-------------------------------------------------------
Identical: the model suite (api_server.run_drift / run_linear / run_xgboost /
run_naive / run_sma when FAST_ONLY), the robust-average ensemble, agreement /
confidence, recommendation thresholds, residual-alpha-vs-SPY computation, and the
z-score — all imported, not re-implemented.

Intentional, documented differences (the right choices for a leak-free backtest):
1. Inputs come from a point-in-time DB slice (date <= as_of) instead of
   ``get_fresh_series`` — so NO yfinance refresh and NO future data.
2. ``current_price`` is the as_of session's adj_close (db_last_close), the
   point-in-time-correct proxy. The live path only substitutes an intraday tick
   during US market hours, which cannot be reconstructed historically; off-hours
   the live path also uses db_last_close.
3. Models are called directly rather than via the multiprocessing timeout
   wrapper (``run_with_timeout``) — same math, no per-model subprocess.

Nothing here writes to the database, calls a network service, or touches the
running stock-api process.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from research.walk_forward_dataset import TickerSeries, _asof_index


def _pit_frame(series: TickerSeries, as_of: dt.date, lookback: int) -> Optional[pd.DataFrame]:
    """Point-in-time (ds, y) frame: the last ``lookback`` sessions with date <= as_of.

    Mirrors api_server.get_data_from_db (LIMIT lookback ORDER BY date DESC) then
    ascending sort — but anchored at as_of, on the de-duplicated series.
    """
    idx = _asof_index(series.dates, as_of)
    if idx is None:
        return None
    lo = max(0, idx - lookback + 1)
    ds = series.dates[lo:idx + 1]
    y = series.adj[lo:idx + 1]
    if len(ds) == 0:
        return None
    return pd.DataFrame({"ds": pd.to_datetime(pd.Series(ds)), "y": np.asarray(y, dtype=float)})


def _fast_suite_funcs(api):
    """The model set the live service runs, in production order."""
    if getattr(api, "FAST_ONLY", True):
        return [api.run_drift, api.run_linear, api.run_xgboost, api.run_naive, api.run_sma]
    return [api.run_drift, api.run_linear, api.run_ets, api.run_arima,
            api.run_xgboost, api.run_naive, api.run_sma]


def _run_suite(api, df: pd.DataFrame, spy_price: float):
    """Direct (no-subprocess) equivalent of api_server.run_model_suite."""
    results: Dict[str, List[float]] = {}
    ratios: Dict[str, List[float]] = {}
    metrics: Dict[str, List[float]] = {}
    ran: List[str] = []
    errors: Dict[str, str] = {}
    for f in _fast_suite_funcs(api):
        try:
            r, rr, m = f(df.copy(), spy_price if spy_price else 1.0)
        except Exception as e:  # mirror live behaviour: a failed model is skipped
            errors[f.__name__] = str(e)
            continue
        if r:
            results.update(r)
            ratios.update(rr)
            metrics.update(m)
            ran.extend(list(r.keys()))
        else:
            errors[f.__name__] = "failed"
    return results, ratios, metrics, ran, errors


def replay_one(api, ticker: str, as_of: dt.date,
               series: TickerSeries, spy_series: Optional[TickerSeries],
               lookback: int) -> Optional[Dict]:
    """Replay the production prediction for one (ticker, as_of). None if no data."""
    df = _pit_frame(series, as_of, lookback)
    if df is None or df.empty:
        return None
    current_price = float(df["y"].iloc[-1])
    if current_price <= 0:
        return None

    spy_df = _pit_frame(spy_series, as_of, lookback) if spy_series else None
    spy_price_for_ratios = float(spy_df["y"].iloc[-1]) if spy_df is not None and not spy_df.empty else None
    spy_current_price = spy_price_for_ratios

    results, ratios, metrics, ran_models, model_errors = _run_suite(
        api, df, spy_price_for_ratios or 1.0)
    if not results:
        return {
            "ticker": ticker, "as_of_date": as_of, "current_price": current_price,
            "forecast_price_5d": None, "predicted_return_5d": None,
            "recommendation": None, "confidence": None, "agreement": None,
            "residual_alpha_vs_spy_pct": None, "zscore": None,
            "n_eligible_models": 0, "hold_reason": "no_model_output",
            "ran_models": "", "model_errors": ";".join(f"{k}:{v}" for k, v in model_errors.items()),
        }

    robust_avg = api.robust_avg_series(results, metrics)
    avg_forecast = api.avg_forecast_series(results)
    ensemble_day5 = float(robust_avg[-1] if robust_avg else (avg_forecast[-1] if avg_forecast else current_price))

    eligible = [m for m, v in metrics.items()
                if isinstance(v, list) and len(v) == 2 and v[0] is not None and v[1] is not None
                and v[0] <= api.WARN_ERR and v[1] <= api.WARN_ERR]
    agreement, confidence, elig_details = api.compute_agreement_confidence(results, current_price, eligible)
    n_eligible_models = int(elig_details.get("n_eligible", 0)) if isinstance(elig_details, dict) else 0

    # Residual alpha vs SPY (D+5), replicating predict_all's SPY fast-model block.
    alpha_day5_pct = None
    if spy_df is not None and not spy_df.empty and spy_current_price:
        spy_results: Dict[str, List[float]] = {}
        for f in (api.run_drift, api.run_linear, api.run_naive):
            try:
                r, _, _ = f(spy_df.copy(), spy_current_price)
                spy_results.update(r)
            except Exception:
                pass
        spy_avg = api.avg_forecast_series(spy_results)
        if robust_avg and spy_avg and spy_current_price:
            r_t = robust_avg[-1] / current_price - 1.0
            r_s = spy_avg[-1] / spy_current_price - 1.0
            alpha_day5_pct = round(100.0 * (r_t - r_s), 2)
    if alpha_day5_pct is None and robust_avg:
        alpha_day5_pct = round(100.0 * (robust_avg[-1] / current_price - 1.0), 2)

    z = api.zscore_abs(ensemble_day5, current_price, df["y"])
    recommendation = api.make_recommendation(ensemble_day5, current_price, agreement, confidence)
    predicted_return_5d = ensemble_day5 / current_price - 1.0 if current_price else None
    hold_reason = _hold_reason(api, recommendation, predicted_return_5d, agreement, confidence)

    return {
        "ticker": ticker,
        "as_of_date": as_of,
        "current_price": round(current_price, 4),
        "forecast_price_5d": round(ensemble_day5, 4),
        "predicted_return_5d": predicted_return_5d,
        "recommendation": recommendation,
        "confidence": float(confidence) if confidence is not None else None,
        "agreement": float(agreement) if agreement is not None else None,
        "residual_alpha_vs_spy_pct": alpha_day5_pct,
        "zscore": float(z) if z is not None else None,
        "n_eligible_models": n_eligible_models,
        "hold_reason": hold_reason,
        "ran_models": ",".join(ran_models),
        "model_errors": ";".join(f"{k}:{v}" for k, v in model_errors.items()),
    }


def _hold_reason(api, recommendation: Optional[str], rel: Optional[float],
                 agreement: Optional[float], confidence: Optional[float]) -> str:
    """Why did the actionability gate yield this label?

    Mirrors api_server.make_recommendation's gate, in precedence order, so the
    report can show *why* HOLD-heavy output occurs. Returns "actionable" for
    BUY/SELL labels.
    """
    if recommendation in {"Strong Buy", "Buy", "Strong Sell", "Sell"}:
        return "actionable"
    # Below here the live label is Hold — diagnose which gate failed.
    if agreement is None or confidence is None:
        return "insufficient_eligible_models"
    if agreement < api.REC_MIN_AGREEMENT:
        return "low_agreement"
    if confidence < api.REC_MIN_CONF:
        return "low_confidence"
    if rel is not None and abs(rel) < api.REC_BUY_WEAK:
        return "below_direction_band"
    return "hold_other"


def replay_dataset(dataset: pd.DataFrame, series_cache: Dict[str, TickerSeries],
                   benchmark: str = "SPY", lookback: Optional[int] = None,
                   progress_every: int = 250, log=print) -> pd.DataFrame:
    """Replay every (ticker, as_of_date) row in ``dataset``.

    ``series_cache`` must already contain de-duplicated series for every ticker
    referenced (the dataset builder returns exactly this cache). Imports
    api_server lazily; reads ``api_server.LOOKBACK_DAYS`` for the default
    lookback so the replay matches the live service configuration.
    """
    import api_server as api  # lazy: needs DB + model stack (VM only)

    if lookback is None:
        lookback = int(getattr(api, "LOOKBACK_DAYS", 365))
    spy_series = series_cache.get(benchmark)
    spy_series = spy_series if (spy_series and spy_series.dates) else None

    out_rows: List[Dict] = []
    total = len(dataset)
    for n, (_, row) in enumerate(dataset.iterrows(), start=1):
        ticker = row["ticker"]
        as_of = row["as_of_date"]
        series = series_cache.get(ticker)
        if series is None or not series.dates:
            continue
        rec = replay_one(api, ticker, as_of, series, spy_series, lookback)
        if rec is not None:
            out_rows.append(rec)
        if progress_every and n % progress_every == 0:
            log(f"  replayed {n}/{total} predictions ...")
    preds = pd.DataFrame(out_rows)
    if preds.empty:
        return preds
    merged = preds.merge(
        dataset[["ticker", "as_of_date", "target_date", "realized_return_5d",
                 "spy_return_5d", "realized_excess_return_5d_vs_spy",
                 "positive_return_flag", "outperform_spy_flag"]],
        on=["ticker", "as_of_date"], how="left")
    return merged
