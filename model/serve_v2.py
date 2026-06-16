"""Phase 2E-B — model-v2 serving adapter + Gate 4 BUY-basket backtest (offline).

This module prepares — but does **not** wire in — the path that will later let
the GCP API serve model-v2 prediction rows. It is pure / local-file by default:

  * read Phase 2D / Phase 2E-A ``prediction_outputs`` rows from a local CSV,
  * select the latest row(s) for one or many tickers,
  * convert a row into the existing ``/predict`` response shape (legacy keys
    preserved) and a ``/predict_all_models``-style batch shape, and
  * run an offline, net-of-cost BUY-basket backtest for plan §8 **gate 4** using
    realized labels joined from the Phase 2C calibrated sample.

Safety contract (enforced by tests):
  * **No DB connection** and **no env-var read at import time** (or at all, by
    default) — everything runs from local sample files.
  * Does **not** import or modify ``api_server.py``; does **not** import Paper
    Trader. stdlib + numpy/pandas only — no sklearn, no xgboost, no pickle.
  * The feature flag (:func:`model_v2_enabled`) defaults to **False**; the env
    var ``PREDICTOR_USE_MODEL_V2`` is documented for future wiring but is **not**
    read here. Actual ``api_server.py`` wiring behind the flag is Phase 2E-C.

Nothing in this module is production-proven; the sample outputs are SYNTHETIC.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from model import store as STORE

# Recommendations that constitute the BUY basket for the gate-4 backtest.
BUY_RECOMMENDATIONS = ("Buy", "Strong Buy")

# Future flag name (documented only; NOT read at import time / by default).
FEATURE_FLAG_ENV = "PREDICTOR_USE_MODEL_V2"

# Simple, transparent cost assumption for the net-of-cost gate-4 backtest.
DEFAULT_COST_BPS = 10.0

# Local sample inputs (Phase 2D outputs; Phase 2C calibrated labels).
DEFAULT_PRED_CSV = STORE.DEFAULT_PRED_CSV
DEFAULT_LABELS_CSV = os.path.join(
    "research", "output", "phase2c_calibrated_predictions_sample.csv")

# Local sample outputs written by the CLI.
DEFAULT_GATE4_PATH = os.path.join(
    "research", "output", "phase2e_gate4_backtest.json")
DEFAULT_API_SAMPLE_PATH = os.path.join(
    "research", "output", "phase2e_v2_api_response_sample.json")

# Loud, machine-readable marker so no downstream consumer mistakes the sample
# for a real, production-grade signal.
SYNTHETIC_NOTE = (
    "SYNTHETIC sample built from Phase 2B/2C planted-signal SYN_* rows. "
    "NOT real market data and not production edge.")


# --------------------------------------------------------------------------- #
# Feature flag (pure; default off; no env read here)
# --------------------------------------------------------------------------- #
def model_v2_enabled(config_or_env_value: Any = None) -> bool:
    """Return whether model-v2 serving is enabled, given an explicit value.

    Pure helper: the caller passes the configuration / env value in (e.g. the
    future ``api_server`` would read ``FEATURE_FLAG_ENV`` from the environment
    and pass the result here). This module never reads the environment itself.
    The default is **off**: ``None`` and any unrecognized value return ``False``.
    """
    if config_or_env_value is None:
        return False
    if isinstance(config_or_env_value, bool):
        return config_or_env_value
    if isinstance(config_or_env_value, (int, float)):
        return config_or_env_value != 0
    text = str(config_or_env_value).strip().lower()
    return text in ("1", "true", "t", "yes", "y", "on", "enabled")


# --------------------------------------------------------------------------- #
# Loading local prediction rows
# --------------------------------------------------------------------------- #
def load_prediction_outputs(path: str = DEFAULT_PRED_CSV) -> pd.DataFrame:
    """Load Phase 2D ``prediction_outputs`` rows from a local CSV (canonical cols)."""
    return STORE.load_phase2d_predictions(path)


def _coerce_dt(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


# --------------------------------------------------------------------------- #
# Row selection
# --------------------------------------------------------------------------- #
def latest_row_for_ticker(frame: pd.DataFrame, ticker: str) -> Optional[Dict[str, Any]]:
    """Return the most recent prediction row for one ticker, or ``None``."""
    if frame is None or frame.empty or "ticker" not in frame.columns:
        return None
    sub = frame[frame["ticker"] == ticker]
    if sub.empty:
        return None
    order = _coerce_dt(sub["as_of_date"])
    idx = order.idxmax()
    return STORE.prediction_frame_to_records(sub.loc[[idx]])[0]


def latest_rows_for_tickers(frame: pd.DataFrame,
                            tickers: Optional[Sequence[str]] = None
                            ) -> List[Dict[str, Any]]:
    """Return the latest prediction row per ticker (all tickers if ``tickers`` is None).

    The result is ordered by ``rank_in_universe`` when available, else by ticker.
    """
    if frame is None or frame.empty or "ticker" not in frame.columns:
        return []
    universe = list(tickers) if tickers is not None else sorted(
        frame["ticker"].dropna().unique().tolist())
    rows: List[Dict[str, Any]] = []
    for tk in universe:
        row = latest_row_for_ticker(frame, tk)
        if row is not None:
            rows.append(row)

    def _rank(r: Dict[str, Any]):
        rk = r.get("rank_in_universe")
        return (rk is None, rk if rk is not None else 0, r.get("ticker") or "")

    rows.sort(key=_rank)
    return rows


# --------------------------------------------------------------------------- #
# Confidence mapping
# --------------------------------------------------------------------------- #
def confidence_from_prob(prob: Any) -> Optional[float]:
    """Map ``prob_outperform_spy`` -> a transparent 0..100 confidence.

    ``confidence = max(prob, 1 - prob) * 100`` — i.e. how far the calibrated
    probability is from a coin flip, expressed on a 0..100 scale. This is a
    simple, documented heuristic, **not** a production-proven confidence.
    """
    if prob is None:
        return None
    try:
        p = float(prob)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(p):
        return None
    p = min(1.0, max(0.0, p))
    return round(max(p, 1.0 - p) * 100.0, 2)


# --------------------------------------------------------------------------- #
# Row -> response shapes
# --------------------------------------------------------------------------- #
def _f(row: Dict[str, Any], key: str) -> Optional[float]:
    v = row.get(key)
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def row_to_predict_response(row: Dict[str, Any]) -> Dict[str, Any]:
    """Convert one ``prediction_outputs`` row into a ``/predict``-style response.

    Legacy keys (``ticker``, ``recommendation``, ``confidence``, ``agreement``,
    ``ensemble_day5``, ``predictions``, ``zscore``) are preserved so existing
    Paper Trader consumers keep working. Model-v2 fields are added alongside and
    grouped under ``model_v2`` for clarity. No legacy key is removed.

    Note: model-v2 is **return-based**, not price-level, so ``ensemble_day5``
    (a legacy price target) and ``zscore`` (needs a historical price series) are
    intentionally ``None`` here — populating them would be fabricated data.
    """
    prob = _f(row, "prob_outperform_spy")
    confidence = confidence_from_prob(prob)
    # Single-model directional agreement proxy: distance of the calibrated
    # probability from 0.5, on a 0..1 scale. Documented, not a model vote count.
    agreement = round(max(prob, 1.0 - prob), 2) if prob is not None else None

    excess = _f(row, "predicted_excess_return_5d")
    # A v2-native "predictions" list: one horizon point described by its
    # predicted 5-day excess return vs SPY. Keeps the legacy key present without
    # inventing a price path.
    predictions = [{
        "model": "model_v2",
        "horizon_days": row.get("horizon_days"),
        "predicted_excess_return_5d": excess,
        "prob_outperform_spy": prob,
    }]

    return {
        # --- legacy keys (preserved) ---
        "ticker": row.get("ticker"),
        "recommendation": row.get("recommendation"),
        "confidence": confidence,
        "agreement": agreement,
        "ensemble_day5": None,
        "predictions": predictions,
        "zscore": None,
        # --- model-v2 additive keys ---
        "model_version": row.get("model_version"),
        "feature_set_version": row.get("feature_set_version"),
        "score": _f(row, "score"),
        "predicted_excess_return_5d": excess,
        "prob_outperform_spy": prob,
        "lo_return_5d": _f(row, "lo_return_5d"),
        "hi_return_5d": _f(row, "hi_return_5d"),
        "interval_width": _f(row, "interval_width"),
        "risk_band": row.get("risk_band"),
        "rank_in_universe": row.get("rank_in_universe"),
        "as_of_date": row.get("as_of_date"),
        "target_date": row.get("target_date"),
        "source": row.get("source"),
        "model_v2": True,
    }


def rows_to_predict_all_response(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Convert many rows into a ``/predict_all_models``-style batch response."""
    responses = [row_to_predict_response(r) for r in rows]
    model_version = rows[0].get("model_version") if rows else None
    source = rows[0].get("source") if rows else None
    as_of_dates = sorted({r.get("as_of_date") for r in rows if r.get("as_of_date")})
    return {
        "model_v2": True,
        "synthetic_sample": True,
        "note": SYNTHETIC_NOTE,
        "count": len(responses),
        "model_version": model_version,
        "as_of_dates": as_of_dates,
        "source": source,
        "results": responses,
    }


# --------------------------------------------------------------------------- #
# Gate 4 — net-of-cost BUY-basket backtest (offline)
# --------------------------------------------------------------------------- #
def load_realized_labels(path: str = DEFAULT_LABELS_CSV) -> pd.DataFrame:
    """Load Phase 2C calibrated rows carrying realized labels (or empty frame)."""
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path)


def _as_bool(v: Any) -> Optional[bool]:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            return None
        return v != 0
    s = str(v).strip().lower()
    if s in ("true", "1", "t", "yes", "y"):
        return True
    if s in ("false", "0", "f", "no", "n"):
        return False
    return None


def gate4_backtest(predictions: pd.DataFrame,
                   labels: Optional[pd.DataFrame] = None,
                   *, cost_bps: float = DEFAULT_COST_BPS) -> Dict[str, Any]:
    """Run an offline, net-of-cost BUY-basket backtest for plan §8 gate 4.

    The BUY basket is every prediction row whose ``recommendation`` is in
    :data:`BUY_RECOMMENDATIONS`. Realized labels are not present in the Phase 2D
    ``prediction_outputs``, so they are joined from the Phase 2C calibrated
    sample on ``(ticker, as_of_date)``. All figures are SYNTHETIC and are **not**
    a production edge.
    """
    result: Dict[str, Any] = {
        "phase": "2E-B",
        "gate": "gate4",
        "gate_name": "BUY basket beats SPY + universe after costs",
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "synthetic_sample": True,
        "note": SYNTHETIC_NOTE,
        "production_edge": False,
        "database_touched": False,
        "cost_bps": float(cost_bps),
        "buy_recommendations": list(BUY_RECOMMENDATIONS),
    }

    if predictions is None or predictions.empty:
        result.update({"evaluable": False, "passed": None,
                       "reason": "no prediction rows"})
        return result

    preds = predictions.copy()
    is_buy = preds["recommendation"].isin(BUY_RECOMMENDATIONS)
    buy = preds[is_buy]

    result["n_universe_rows"] = int(len(preds))
    result["n_buy_rows"] = int(len(buy))
    result["n_buy_dates"] = int(buy["as_of_date"].nunique()) if not buy.empty else 0
    result["model_version"] = (
        preds["model_version"].iloc[0] if "model_version" in preds.columns
        and len(preds) else None)

    # Join realized labels (Phase 2C) on the natural per-row key.
    labels = labels if labels is not None else pd.DataFrame()
    label_col = "realized_excess_return_5d_vs_spy"
    have_labels = (not labels.empty and label_col in labels.columns
                   and {"ticker", "as_of_date"}.issubset(labels.columns))
    result["labels_joined"] = bool(have_labels)

    if not have_labels:
        result.update({
            "evaluable": False, "passed": None,
            "reason": "no realized labels available to join",
            "avg_buy_realized_excess_return": None,
            "avg_universe_realized_excess_return": None,
            "buy_hit_rate_vs_spy": None,
            "net_of_cost_buy_excess_return": None,
        })
        return result

    lab = labels.copy()
    lab["_aod"] = lab["as_of_date"].astype(str)
    keep = ["ticker", "_aod", label_col]
    if "outperform_spy_flag" in lab.columns:
        keep.append("outperform_spy_flag")
    lab = lab[keep].drop_duplicates(subset=["ticker", "_aod"])

    preds["_aod"] = preds["as_of_date"].astype(str)
    merged = preds.merge(lab, on=["ticker", "_aod"], how="left")
    merged_buy = merged[merged["recommendation"].isin(BUY_RECOMMENDATIONS)]

    buy_excess = pd.to_numeric(merged_buy[label_col], errors="coerce").dropna()
    uni_excess = pd.to_numeric(merged[label_col], errors="coerce").dropna()

    result["n_buy_rows_with_labels"] = int(len(buy_excess))
    result["n_universe_rows_with_labels"] = int(len(uni_excess))

    avg_buy = float(buy_excess.mean()) if len(buy_excess) else None
    avg_uni = float(uni_excess.mean()) if len(uni_excess) else None
    result["avg_buy_realized_excess_return"] = avg_buy
    result["avg_universe_realized_excess_return"] = avg_uni

    # Hit rate vs SPY: prefer the explicit flag, else derive from excess > 0.
    if "outperform_spy_flag" in merged_buy.columns:
        flags = merged_buy["outperform_spy_flag"].map(_as_bool).dropna()
        hit_rate = float(flags.mean()) if len(flags) else None
    else:
        hit_rate = float((buy_excess > 0).mean()) if len(buy_excess) else None
    result["buy_hit_rate_vs_spy"] = hit_rate

    cost = cost_bps / 10000.0
    net_buy = (avg_buy - cost) if avg_buy is not None else None
    result["net_of_cost_buy_excess_return"] = net_buy
    result["buy_excess_minus_universe"] = (
        (avg_buy - avg_uni) if (avg_buy is not None and avg_uni is not None)
        else None)

    evaluable = avg_buy is not None and avg_uni is not None and hit_rate is not None
    result["evaluable"] = bool(evaluable)
    if evaluable:
        passed = bool(net_buy > 0.0 and avg_buy > avg_uni and hit_rate > 0.5)
        result["passed"] = passed
        result["pass_criteria"] = {
            "net_of_cost_buy_excess_return_gt_0": bool(net_buy > 0.0),
            "buy_excess_gt_universe_excess": bool(avg_buy > avg_uni),
            "buy_hit_rate_gt_0_5": bool(hit_rate > 0.5),
        }
        result["passed_on"] = "synthetic/sample only"
    else:
        result["passed"] = None
        result["reason"] = "insufficient labeled rows to evaluate"
    return result


# --------------------------------------------------------------------------- #
# Sample-response builder
# --------------------------------------------------------------------------- #
def build_api_response_sample(predictions: pd.DataFrame) -> Dict[str, Any]:
    """Build the single- + multi-ticker sample response document."""
    multi = latest_rows_for_tickers(predictions)
    single_ticker = multi[0]["ticker"] if multi else None
    single_row = (latest_row_for_ticker(predictions, single_ticker)
                  if single_ticker else None)
    return {
        "phase": "2E-B",
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "synthetic_sample": True,
        "production_edge": False,
        "database_touched": False,
        "note": SYNTHETIC_NOTE,
        "feature_flag": {
            "env_var": FEATURE_FLAG_ENV,
            "default_enabled": model_v2_enabled(None),
        },
        "single_ticker_response": (
            row_to_predict_response(single_row) if single_row else None),
        "multi_ticker_response": rows_to_predict_all_response(multi),
    }


# --------------------------------------------------------------------------- #
# CLI (local files only; no DB; no writes to a database)
# --------------------------------------------------------------------------- #
def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Phase 2E-B model-v2 serving adapter (offline). Reads local "
                    "Phase 2D sample prediction rows, writes a sample API "
                    "response and a gate-4 BUY-basket backtest. No DB connection, "
                    "no DB writes, no api_server.py changes.")
    p.add_argument("--predictions", type=str, default=DEFAULT_PRED_CSV,
                   help="Phase 2D prediction-output CSV (local).")
    p.add_argument("--labels", type=str, default=DEFAULT_LABELS_CSV,
                   help="Phase 2C calibrated CSV carrying realized labels (local).")
    p.add_argument("--gate4-output", type=str, default=DEFAULT_GATE4_PATH,
                   help="Where to write the gate-4 backtest JSON.")
    p.add_argument("--api-sample-output", type=str, default=DEFAULT_API_SAMPLE_PATH,
                   help="Where to write the sample API response JSON.")
    p.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS,
                   help="Round-trip cost assumption in basis points.")
    return p


def _write_json(path: str, obj: Dict[str, Any]) -> None:
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, allow_nan=False)


def run(args) -> Dict[str, Any]:
    predictions = load_prediction_outputs(args.predictions)
    labels = load_realized_labels(args.labels)

    api_sample = build_api_response_sample(predictions)
    gate4 = gate4_backtest(predictions, labels, cost_bps=args.cost_bps)

    if args.api_sample_output:
        _write_json(args.api_sample_output, api_sample)
    if args.gate4_output:
        _write_json(args.gate4_output, gate4)

    return {
        "api_sample": api_sample,
        "gate4": gate4,
        "api_sample_path": args.api_sample_output,
        "gate4_path": args.gate4_output,
        "database_touched": False,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    out = run(args)
    g4 = out["gate4"]
    single = out["api_sample"].get("single_ticker_response") or {}
    multi = out["api_sample"].get("multi_ticker_response") or {}
    print(f"[phase2e-b] feature flag default     : {model_v2_enabled(None)}")
    print(f"[phase2e-b] single-ticker            : {single.get('ticker')} "
          f"rec={single.get('recommendation')} conf={single.get('confidence')}")
    print(f"[phase2e-b] multi-ticker count       : {multi.get('count')}")
    print(f"[phase2e-b] gate4 n_buy_rows         : {g4.get('n_buy_rows')}")
    print(f"[phase2e-b] gate4 avg buy excess     : "
          f"{g4.get('avg_buy_realized_excess_return')}")
    print(f"[phase2e-b] gate4 net-of-cost buy    : "
          f"{g4.get('net_of_cost_buy_excess_return')}")
    print(f"[phase2e-b] gate4 hit rate vs SPY    : {g4.get('buy_hit_rate_vs_spy')}")
    print(f"[phase2e-b] gate4 passed (sample)    : {g4.get('passed')}")
    print(f"[phase2e-b] database_touched         : {out['database_touched']}")
    print(f"[phase2e-b] api sample JSON          : {out['api_sample_path']}")
    print(f"[phase2e-b] gate4 JSON               : {out['gate4_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
