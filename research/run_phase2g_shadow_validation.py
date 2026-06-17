"""Phase 2G-A — real-data model-v2 shadow validation harness (offline / read-only).

Evaluates the Phase 2 model-v2 ranking pipeline on **price data supplied as a
CSV export** — either a small deterministic fixture (this phase) or, later, a
read-only export of the real GCP price history (Phase 2G-B). It is offline and
file-based by construction:

  * Input is a CSV (``ticker, date, adj_close`` + optional ``volume``); the
    default run synthesizes a clearly-labelled deterministic **fixture** CSV and
    validates against it.
  * Point-in-time features come from the existing Phase 2A builder
    (``model.features``); forward labels come from the same point-in-time source
    as the walk-forward dataset (``research.walk_forward_dataset``), so features
    and labels share one anchor and there is no look-ahead.
  * A transparent, documented **mean-z-score composite** over the predictive
    features is the model-v2-style ranking score (the same composite family the
    Phase 2B evaluator falls back to). No persisted training artifact, no DB, no
    network is required.
  * It computes shadow-style ranking metrics (rank IC, per-date top-decile excess
    return vs the universe, hit rate, bucket monotonicity, optional probability
    buckets) and writes a JSON summary plus the scored rows as CSV.

Safety contract (enforced by ``tests/test_phase2g_shadow_validation.py``):
  * **No DB connection**, no migration, no deployment, no env-var read at import
    time. Default behavior is offline and file-based; the only optional DB path
    is read-only, explicit, and **disabled by default** (see ``--db-url``, which
    this phase does not implement beyond refusing to write).
  * Does **not** import or modify ``api_server.py``; does **not** import Paper
    Trader; performs no trade execution, no order placement, and no scheduling.
  * Never enables ``PREDICTOR_USE_MODEL_V2`` (the summary records
    ``model_v2_enabled: false``).
  * **Never claims a production edge.** Fixture/sample output is marked
    ``fixture_only: true``, ``production_edge_claimed: false``, and
    ``safe_for_canary: false`` — it is harness validation, not a market result.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from model import features as FEAT  # noqa: E402
from research import metrics as M  # noqa: E402
from research.walk_forward_dataset import (  # noqa: E402
    DEFAULT_BENCHMARK,
    DEFAULT_HORIZON,
    TickerSeries,
    _as_date,
)

PHASE = "2G-A"
FEATURE_FLAG_ENV = "PREDICTOR_USE_MODEL_V2"

# Required CSV columns and the optional ones we use when present.
REQUIRED_COLUMNS = ("ticker", "date", "adj_close")
OPTIONAL_COLUMNS = ("volume",)

# Predictive features that feed the transparent ranking composite. These are the
# return-like, cross-sectionally comparable families; volume / level features are
# deliberately excluded from the score.
SCORE_FEATURES = [
    "return_5d", "return_10d", "return_21d", "return_63d", "momentum_12_1",
    "excess_return_vs_spy_21d", "excess_return_vs_spy_63d",
    "realized_vol_21d", "realized_vol_63d",
]
# Volatility enters the composite with a negative sign (lower vol preferred).
_NEGATIVE_FEATURES = {"realized_vol_21d", "realized_vol_63d"}

# Realized target columns produced by the label builder.
_EXCESS = "realized_excess_return_5d_vs_spy"
_RAW = "realized_return_5d"

# Small default min-history so modest CSV exports / fixtures still produce rows
# (the feature builder degrades each long-lookback feature to None individually).
DEFAULT_MIN_HISTORY = 30

# Plan §8 gate-1 rank-IC threshold reused as one canary precondition.
CANARY_RANK_IC_MIN = 0.03

DEFAULT_SAMPLE_CSV = os.path.join(
    "research", "output", "phase2g_shadow_validation_sample.csv")
DEFAULT_SAMPLE_JSON = os.path.join(
    "research", "output", "phase2g_shadow_validation_sample.json")

FIXTURE_NOTE = (
    "Deterministic FIXTURE built from synthetic price series with a planted "
    "per-ticker momentum drift, purely to exercise the harness end-to-end. This "
    "is NOT real market data and is NOT evidence of a production edge. Any skill "
    "shown is the planted signal being recovered, which only proves the pipeline "
    "runs.")


# --------------------------------------------------------------------------- #
# CSV input (read-only)
# --------------------------------------------------------------------------- #
def read_price_csv(path: str
                   ) -> Tuple[Dict[str, TickerSeries], Dict[str, List[float]]]:
    """Load a price CSV into per-ticker ascending, de-duplicated series.

    Required columns: ``ticker, date, adj_close``. Optional: ``volume``. Rows
    with a null / non-positive ``adj_close`` are dropped (matching the DB
    loader's contract). Returns ``(series_by_ticker, volume_by_ticker)``; a
    ticker appears in ``volume_by_ticker`` only when a real, fully-populated
    volume column is present for it.
    """
    df = pd.read_csv(path)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"input CSV missing required columns: {missing}")
    df = df[df["adj_close"].notna()].copy()
    df["adj_close"] = pd.to_numeric(df["adj_close"], errors="coerce")
    df = df[df["adj_close"] > 0]
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df = df[df["date"].notna()]
    has_volume = "volume" in df.columns

    series_by_ticker: Dict[str, TickerSeries] = {}
    volume_by_ticker: Dict[str, List[float]] = {}
    for ticker, sub in df.groupby("ticker"):
        sub = sub.drop_duplicates(subset=["date"], keep="last").sort_values("date")
        dates = [_as_date(d) for d in sub["date"]]
        adj = [float(x) for x in sub["adj_close"]]
        series_by_ticker[str(ticker)] = TickerSeries(
            ticker=str(ticker), dates=dates, adj=adj)
        if has_volume:
            vol = pd.to_numeric(sub["volume"], errors="coerce")
            if vol.notna().all() and (vol >= 0).all():
                volume_by_ticker[str(ticker)] = [float(x) for x in vol]
    return series_by_ticker, volume_by_ticker


# --------------------------------------------------------------------------- #
# Labeled dataset + score
# --------------------------------------------------------------------------- #
def build_labeled_frame(series_by_ticker: Dict[str, TickerSeries],
                        volume_by_ticker: Dict[str, List[float]],
                        *, benchmark: str = DEFAULT_BENCHMARK,
                        horizon: int = DEFAULT_HORIZON,
                        min_history: int = DEFAULT_MIN_HISTORY) -> pd.DataFrame:
    """Build the point-in-time features-joined-to-forward-labels frame (pure)."""
    spy = series_by_ticker.get(benchmark)
    spy = spy if (spy is not None and spy.dates) else None

    frames: List[pd.DataFrame] = []
    for ticker, series in series_by_ticker.items():
        if ticker == benchmark:
            continue
        if not series.dates:
            continue
        volume = volume_by_ticker.get(ticker)
        frame = FEAT.build_labeled_dataset_for_ticker(
            series, spy, volume=volume, horizon=horizon, min_history=min_history)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def add_score_and_probability(frame: pd.DataFrame) -> pd.DataFrame:
    """Add the model-v2-style composite ``score`` and a heuristic ``probability``.

    ``score`` is the within-date mean of the standardized predictive features
    (volatility sign-flipped) — a transparent composite, not a trained artifact.
    ``probability`` is the within-date rank percentile of ``score`` mapped to
    (0, 1); it is a documented heuristic, **not** a calibrated probability.
    """
    if frame.empty:
        return frame
    cols = [c for c in SCORE_FEATURES if c in frame.columns]
    out = FEAT.cross_sectional_zscore(frame, cols=cols, suffix="_z")
    z_cols = [c + "_z" for c in cols]
    signed = out[z_cols].copy()
    for c in cols:
        if c in _NEGATIVE_FEATURES:
            signed[c + "_z"] = -signed[c + "_z"]
    out["score"] = signed.mean(axis=1, skipna=True)

    def _pct_rank(s: pd.Series) -> pd.Series:
        n = s.notna().sum()
        if n <= 1:
            return s.where(s.isna(), 0.5)
        return (s.rank(method="average") - 0.5) / n

    out["probability"] = out.groupby("as_of_date")["score"].transform(_pct_rank)
    return out


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def _target_column(frame: pd.DataFrame) -> str:
    """Prefer realized excess vs SPY; fall back to raw realized return."""
    if _EXCESS in frame.columns and frame[_EXCESS].notna().any():
        return _EXCESS
    return _RAW


def per_date_top_decile(frame: pd.DataFrame, target: str,
                        *, decile: float = 0.10) -> Dict[str, Any]:
    """Per-date top-decile-by-score realized return vs the per-date universe.

    For each as_of date, take the top ``ceil(n*decile)`` rows by score, then
    average the per-date means across dates (equal weight per date). Returns the
    top-decile mean target, top-decile hit rate (target > 0), and the per-date
    universe mean target for a like-for-like comparison.
    """
    if frame.empty:
        return {"top_decile_mean": None, "top_decile_hit_rate": None,
                "universe_mean": None, "n_dates": 0}
    dec_ret: List[float] = []
    dec_hit: List[float] = []
    uni_ret: List[float] = []
    n_dates = 0
    for _d, sub in frame.groupby("as_of_date"):
        s = sub[["score", target]].dropna()
        if s.empty:
            continue
        n_dates += 1
        uni_ret.append(float(s[target].mean()))
        k = max(1, math.ceil(len(s) * decile))
        top = s.sort_values("score", ascending=False).head(k)
        dec_ret.append(float(top[target].mean()))
        dec_hit.append(float((top[target] > 0).mean()))
    if n_dates == 0:
        return {"top_decile_mean": None, "top_decile_hit_rate": None,
                "universe_mean": None, "n_dates": 0}
    return {
        "top_decile_mean": float(np.mean(dec_ret)),
        "top_decile_hit_rate": float(np.mean(dec_hit)),
        "universe_mean": float(np.mean(uni_ret)),
        "n_dates": n_dates,
    }


def bucket_monotonic(frame: pd.DataFrame, target: str, n_bins: int = 5
                     ) -> Tuple[Optional[bool], List[Dict[str, float]]]:
    """Whether mean realized target is non-decreasing across score quintiles."""
    if frame.empty:
        return None, []
    buckets = M.bucket_stats(frame["score"].to_numpy(),
                             frame[target].to_numpy(), n_bins=n_bins)
    if len(buckets) < 2:
        return None, buckets
    means = [b["mean_realized"] for b in buckets]
    mono = all(means[i + 1] >= means[i] for i in range(len(means) - 1))
    return bool(mono), buckets


def probability_bucket_summary(frame: pd.DataFrame) -> Optional[List[Dict[str, float]]]:
    """Per probability-decile: count, mean probability, realized outperform rate.

    Returns ``None`` when no probability column / outperform label is present.
    This is a diagnostic only; a positive trend is *not* a calibration proof.
    """
    if frame.empty or "probability" not in frame.columns:
        return None
    flag = "outperform_spy_flag" if "outperform_spy_flag" in frame.columns else None
    realized = (frame["outperform_spy_flag"] if flag
                else (frame[_target_column(frame)] > 0).astype(float))
    table = M.calibration_table(frame["probability"].to_numpy(),
                                realized.tolist(), n_bins=10)
    return table or None


def compute_metrics(frame: pd.DataFrame, *, horizon: int) -> Dict[str, Any]:
    """All shadow-style ranking metrics for the scored frame."""
    target = _target_column(frame) if not frame.empty else _EXCESS
    if frame.empty:
        return {
            "row_count": 0, "ticker_count": 0, "date_start": None,
            "date_end": None, "horizon_days": horizon, "target_metric": target,
            "rank_ic": None, "top_decile_mean_excess_return": None,
            "universe_mean_excess_return": None, "top_decile_hit_rate": None,
            "bucket_monotonic": None, "score_buckets": [],
            "probability_bucket_summary": None,
        }
    dates = sorted(frame["as_of_date"].unique())
    ic = M.spearman_rank_ic(frame["score"].to_numpy(), frame[target].to_numpy())
    decile = per_date_top_decile(frame, target)
    mono, buckets = bucket_monotonic(frame, target)
    return {
        "row_count": int(len(frame)),
        "ticker_count": int(frame["ticker"].nunique()),
        "date_start": str(dates[0]),
        "date_end": str(dates[-1]),
        "horizon_days": horizon,
        "target_metric": target,
        "rank_ic": _num(ic),
        "top_decile_mean_excess_return": _num(decile["top_decile_mean"]),
        "universe_mean_excess_return": _num(decile["universe_mean"]),
        "top_decile_hit_rate": _num(decile["top_decile_hit_rate"]),
        "top_decile_n_dates": decile["n_dates"],
        "bucket_monotonic": mono,
        "score_buckets": [_clean(b) for b in buckets],
        "probability_bucket_summary": (
            [_clean(b) for b in (probability_bucket_summary(frame) or [])]
            or None),
    }


# --------------------------------------------------------------------------- #
# Summary assembly
# --------------------------------------------------------------------------- #
def _num(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _clean(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: _num(v) if isinstance(v, float) else v for k, v in d.items()}


def _legacy_comparison_available(legacy_csv: Optional[str]) -> bool:
    return bool(legacy_csv) and os.path.exists(legacy_csv)


def _canary_ready(metrics: Dict[str, Any], *, fixture_only: bool,
                  legacy_available: bool) -> bool:
    """Conservative canary precondition. Always False for fixture/sample data.

    A real run is only *canary-candidate* (still requiring human sign-off and a
    later, separate canary phase) when it is real data, has a legacy comparison,
    clears the rank-IC floor, shows monotone score buckets, and the top decile
    beats the universe. This function never enables anything.
    """
    if fixture_only or not legacy_available:
        return False
    ic = metrics.get("rank_ic")
    td = metrics.get("top_decile_mean_excess_return")
    uni = metrics.get("universe_mean_excess_return")
    if ic is None or td is None or uni is None:
        return False
    return bool(ic >= CANARY_RANK_IC_MIN
                and metrics.get("bucket_monotonic") is True
                and td > uni)


def build_summary(frame: pd.DataFrame, *, horizon: int, fixture_only: bool,
                  input_csv: str, legacy_csv: Optional[str] = None,
                  benchmark: str = DEFAULT_BENCHMARK) -> Dict[str, Any]:
    """Assemble the full validation summary document (pure, JSON-safe)."""
    metrics = compute_metrics(frame, horizon=horizon)
    legacy_available = _legacy_comparison_available(legacy_csv)
    safe_for_canary = _canary_ready(
        metrics, fixture_only=fixture_only, legacy_available=legacy_available)

    summary: Dict[str, Any] = {
        "phase": PHASE,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "input_mode": "csv",
        "input_csv": input_csv,
        "benchmark": benchmark,
        "feature_flag": FEATURE_FLAG_ENV,
        # Safety flags (machine-readable; asserted by the tests).
        "database_touched": False,
        "migration_executed": False,
        "deployment_executed": False,
        "model_v2_enabled": False,
        "production_edge_claimed": False,
        "fixture_only": bool(fixture_only),
        "no_trading": True,
        "no_orders": True,
        "no_automation": True,
        # Comparison + gate.
        "legacy_comparison_available": legacy_available,
        "safe_for_canary": safe_for_canary,
    }
    summary.update(metrics)
    if fixture_only:
        summary["note"] = FIXTURE_NOTE
        summary["disclaimer"] = (
            "This is fixture/sample output. It is NOT production evidence and "
            "NOT a real market-edge claim. safe_for_canary is false.")
    return summary


# --------------------------------------------------------------------------- #
# Deterministic fixture (no DB, no network) — clearly labelled, never real data
# --------------------------------------------------------------------------- #
def build_fixture_frame(n_tickers: int = 6, n_days: int = 180, seed: int = 2026,
                        benchmark: str = DEFAULT_BENCHMARK) -> pd.DataFrame:
    """Build a deterministic long-format price CSV frame (ticker,date,adj_close,volume).

    Each ticker gets a persistent drift (a planted momentum signal) plus noise so
    the ranking metrics are non-trivial; the benchmark gets a mild drift. Tickers
    are prefixed ``FIX_`` so the fixture can never be mistaken for real data.
    """
    rng = np.random.default_rng(seed)
    start = dt.date(2024, 1, 2)
    dates: List[dt.date] = []
    d = start
    for _ in range(n_days):
        while d.weekday() > 4:
            d += dt.timedelta(days=1)
        dates.append(d)
        d += dt.timedelta(days=1)

    rows: List[Dict[str, Any]] = []

    def _emit(ticker: str, drift: float, vol: float, with_volume: bool) -> None:
        rets = rng.normal(drift, vol, n_days)
        adj = 100.0 * np.cumprod(1.0 + rets)
        volume = rng.integers(1_000_000, 5_000_000, n_days) if with_volume else None
        for k in range(n_days):
            row = {"ticker": ticker, "date": dates[k].isoformat(),
                   "adj_close": round(float(adj[k]), 4)}
            if with_volume:
                row["volume"] = int(volume[k])
            rows.append(row)

    _emit(benchmark, drift=0.0003, vol=0.008, with_volume=False)
    for k in range(n_tickers):
        # Spread drift across tickers so the cross-section has a real ordering.
        drift = 0.0009 - 0.0003 * k
        _emit(f"FIX_{chr(ord('A') + k)}", drift=drift, vol=0.013,
              with_volume=(k != 0))  # first ticker has no volume column populated
    return pd.DataFrame(rows)


def write_fixture_csv(path: str = DEFAULT_SAMPLE_CSV) -> str:
    """Write the deterministic fixture CSV (creating the output dir)."""
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    build_fixture_frame().to_csv(path, index=False)
    return path


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def write_json(path: str, obj: Dict[str, Any]) -> None:
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, allow_nan=False)


def run_validation(input_csv: str, output_json: str, *,
                   fixture_only: bool = True,
                   horizon: int = DEFAULT_HORIZON,
                   min_history: int = DEFAULT_MIN_HISTORY,
                   benchmark: str = DEFAULT_BENCHMARK,
                   legacy_csv: Optional[str] = None,
                   scored_csv: Optional[str] = None) -> Dict[str, Any]:
    """Run the full offline shadow validation and write the summary JSON."""
    series_by_ticker, volume_by_ticker = read_price_csv(input_csv)
    frame = build_labeled_frame(
        series_by_ticker, volume_by_ticker, benchmark=benchmark,
        horizon=horizon, min_history=min_history)
    frame = add_score_and_probability(frame)
    summary = build_summary(
        frame, horizon=horizon, fixture_only=fixture_only, input_csv=input_csv,
        legacy_csv=legacy_csv, benchmark=benchmark)
    if output_json:
        write_json(output_json, summary)
        summary["output_json"] = output_json
    if scored_csv and not frame.empty:
        if os.path.dirname(scored_csv):
            os.makedirs(os.path.dirname(scored_csv), exist_ok=True)
        frame.to_csv(scored_csv, index=False)
        summary["scored_csv"] = scored_csv
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Phase 2G-A model-v2 shadow validation (offline / read-only). "
                    "Reads a price CSV, builds point-in-time features + forward "
                    "labels, scores with a transparent composite, and writes a "
                    "ranking-metrics summary. No DB, no deploy, no flag enable, "
                    "no trading. With no --input-csv it generates a deterministic "
                    "fixture and writes the sample artifacts.")
    p.add_argument("--input-csv", type=str, default=None,
                   help="Price CSV (ticker,date,adj_close[,volume]). If omitted, "
                        "a deterministic FIXTURE is generated and validated.")
    p.add_argument("--output-json", type=str, default=None,
                   help="Where to write the summary JSON "
                        f"(default for fixture mode: {DEFAULT_SAMPLE_JSON}).")
    p.add_argument("--scored-csv", type=str, default=None,
                   help="Optional path to also write the scored per-row frame.")
    p.add_argument("--legacy-csv", type=str, default=None,
                   help="Optional legacy-prediction CSV enabling a comparison. "
                        "Its presence sets legacy_comparison_available.")
    p.add_argument("--benchmark", type=str, default=DEFAULT_BENCHMARK,
                   help="Benchmark ticker used for excess-return labels.")
    p.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON)
    p.add_argument("--min-history", type=int, default=DEFAULT_MIN_HISTORY)
    p.add_argument("--real-data", action="store_true",
                   help="Assert the input is a real, read-only export (sets "
                        "fixture_only=false). Default treats input as fixture.")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.input_csv:
        input_csv = args.input_csv
        output_json = args.output_json or os.path.join(
            "research", "output", "phase2g_shadow_validation.json")
        fixture_only = not args.real_data
    else:
        # No input: generate the deterministic fixture and produce the samples.
        input_csv = write_fixture_csv(DEFAULT_SAMPLE_CSV)
        output_json = args.output_json or DEFAULT_SAMPLE_JSON
        fixture_only = True  # generated fixture is never real data

    summary = run_validation(
        input_csv, output_json, fixture_only=fixture_only,
        horizon=args.horizon_days, min_history=args.min_history,
        benchmark=args.benchmark, legacy_csv=args.legacy_csv,
        scored_csv=args.scored_csv)

    print(f"[phase2g-a] input csv          : {input_csv}")
    print(f"[phase2g-a] rows / tickers     : {summary['row_count']} / "
          f"{summary['ticker_count']}")
    print(f"[phase2g-a] date range         : {summary['date_start']} -> "
          f"{summary['date_end']}")
    print(f"[phase2g-a] target metric      : {summary['target_metric']}")
    print(f"[phase2g-a] rank IC            : {summary['rank_ic']}")
    print(f"[phase2g-a] top-decile excess  : "
          f"{summary['top_decile_mean_excess_return']}")
    print(f"[phase2g-a] universe excess    : "
          f"{summary['universe_mean_excess_return']}")
    print(f"[phase2g-a] top-decile hit rate: {summary['top_decile_hit_rate']}")
    print(f"[phase2g-a] bucket monotonic   : {summary['bucket_monotonic']}")
    print(f"[phase2g-a] fixture_only       : {summary['fixture_only']}")
    print(f"[phase2g-a] production_edge     : {summary['production_edge_claimed']}")
    print(f"[phase2g-a] safe_for_canary    : {summary['safe_for_canary']}")
    print(f"[phase2g-a] model_v2_enabled   : {summary['model_v2_enabled']}")
    print(f"[phase2g-a] database_touched   : {summary['database_touched']}")
    print(f"[phase2g-a] summary JSON       : {output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
