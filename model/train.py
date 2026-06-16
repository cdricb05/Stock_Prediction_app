"""Phase 2B — walk-forward training + out-of-sample prediction.

Trains the Phase 2A cross-sectional features into an out-of-sample ranking
signal and emits one prediction row per ``(ticker, as_of_date)`` in the test
folds. This is **offline / research only**: it imports neither ``api_server``
nor Paper Trader, never writes to the production database, and does not touch the
live service in any way.

Design (mirrors the rest of the research stack):
- The model is a **numpy/pandas-only ridge regression** on the within-date
  z-scored features (``*_z``) produced by ``model.features``. No sklearn, no
  xgboost, no LSTM/Prophet/ARIMA — a transparent baseline first (plan §5). If
  ridge cannot be fit (too little data / singular), we fall back to a simple
  equal-weight z-score composite so a fold always produces a ranking.
- Validation is **walk-forward only** (no random split). ``make_walk_forward_folds``
  is a pure, testable purged/embargoed splitter: train dates are strictly before
  test dates with an ``embargo >= horizon`` gap so a train row's 5-day forward
  label can never overlap the test window.
- Everything is pure given a DataFrame, so the math runs without a database. The
  CLI builds a clearly-labeled SYNTHETIC sample by default and reads the GCP DB
  only when ``--db-url`` is supplied (read-only, via ``model.features``).

Primary target  : ``realized_excess_return_5d_vs_spy`` (the ranking score).
Secondary target: ``outperform_spy_flag`` (probability label; calibrated in 2C).
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from research.walk_forward_dataset import DEFAULT_HORIZON, TickerSeries, _as_date
from model.features import (
    DEFAULT_MIN_HISTORY,
    build_feature_dataset,
    build_labeled_dataset_for_ticker,
    cross_sectional_zscore,
    feature_names,
    unavailable_feature_families,
)

PRIMARY_TARGET = "realized_excess_return_5d_vs_spy"
SECONDARY_TARGET = "outperform_spy_flag"
DEFAULT_ALPHA = 10.0
DEFAULT_N_FOLDS = 4
DEFAULT_EMBARGO = DEFAULT_HORIZON  # >= horizon so train labels never reach test
DEFAULT_MIN_TRAIN_DATES = 60
# Below this many complete training rows we do not trust a ridge fit and use the
# transparent fallback instead.
MIN_RIDGE_ROWS = 40

# Column order of the out-of-sample prediction output (also the CSV schema).
OOS_COLUMNS = [
    "ticker", "as_of_date", "target_date", "fold_id", "model_name", "score",
    "predicted_excess_return_5d", "realized_excess_return_5d_vs_spy",
    "realized_return_5d", "spy_return_5d", "outperform_spy_flag",
    "positive_return_flag",
]


# --------------------------------------------------------------------------- #
# Feature selection
# --------------------------------------------------------------------------- #
def feature_columns(df: pd.DataFrame) -> List[str]:
    """The model's input columns: the within-date z-scored features (``*_z``).

    Cross-sectional standardization is the form the ranking model consumes
    (plan §4). Falls back to the raw feature names if no ``_z`` columns exist.
    """
    z = [c for c in df.columns if c.endswith("_z")]
    if z:
        return z
    return [c for c in feature_names() if c in df.columns]


# --------------------------------------------------------------------------- #
# Walk-forward splitter (pure, purged + embargoed). No DB access.
# --------------------------------------------------------------------------- #
@dataclass
class Fold:
    """One walk-forward fold: train dates strictly precede test dates."""
    fold_id: int
    train_dates: List[dt.date] = field(default_factory=list)
    test_dates: List[dt.date] = field(default_factory=list)


def make_walk_forward_folds(dates: Sequence,
                            n_folds: int = DEFAULT_N_FOLDS,
                            embargo: int = DEFAULT_EMBARGO,
                            min_train_dates: int = DEFAULT_MIN_TRAIN_DATES,
                            test_size: Optional[int] = None) -> List[Fold]:
    """Build expanding-window walk-forward folds with a purge/embargo gap.

    The unique ``as_of`` dates are sorted ascending; each fold trains on an
    expanding prefix and tests on the next contiguous block, separated by
    ``embargo`` sessions. With ``embargo >= horizon`` the last train date's
    forward label (which looks ``horizon`` sessions ahead) resolves strictly
    before the first test ``as_of`` date, so no train label overlaps the test
    window. Pure: depends only on the date list, never on a database.
    """
    uniq = sorted({_as_date(d) for d in dates})
    n = len(uniq)
    usable = n - min_train_dates - embargo
    if usable < 1:
        raise ValueError(
            f"not enough dates ({n}) for a fold: need > min_train_dates"
            f" ({min_train_dates}) + embargo ({embargo})")
    if test_size is None:
        n_folds = max(1, min(n_folds, usable))
        block = usable // n_folds
    else:
        block = max(1, test_size)
        n_folds = max(1, min(n_folds, usable // block))
    block = max(1, block)

    folds: List[Fold] = []
    for k in range(n_folds):
        test_start = min_train_dates + embargo + k * block
        test_end = n if k == n_folds - 1 else test_start + block
        train_end = test_start - embargo
        train_dates = uniq[:train_end]
        test_dates = uniq[test_start:test_end]
        if not test_dates or len(train_dates) < min_train_dates:
            continue
        folds.append(Fold(fold_id=len(folds), train_dates=train_dates,
                          test_dates=test_dates))
    return folds


# --------------------------------------------------------------------------- #
# Models (numpy-only). No sklearn / xgboost dependency.
# --------------------------------------------------------------------------- #
def _design_matrix(df: pd.DataFrame, feature_cols: Sequence[str]) -> np.ndarray:
    """Feature matrix with non-finite z-scores filled to 0 (= cross-sectional
    mean). Honest for already-standardized columns; never invents a raw value.
    """
    X = df.loc[:, list(feature_cols)].to_numpy(dtype=float)
    return np.where(np.isfinite(X), X, 0.0)


@dataclass
class RidgeModel:
    """Closed-form ridge on standardized features (numpy-only)."""
    name: str
    feature_cols: List[str]
    mu: np.ndarray
    sd: np.ndarray
    w: np.ndarray
    y_mean: float

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        X = _design_matrix(df, self.feature_cols)
        Xs = (X - self.mu) / self.sd
        return Xs @ self.w + self.y_mean


@dataclass
class CompositeModel:
    """Fallback: equal-weight mean of the available z-scored features.

    Used when ridge cannot be fit (insufficient/degenerate training data). Still
    produces a sensible cross-sectional ranking (high standardized features ->
    higher score) without fitting any coefficients.
    """
    name: str
    feature_cols: List[str]

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        X = df.loc[:, list(self.feature_cols)].to_numpy(dtype=float)
        X = np.where(np.isfinite(X), X, np.nan)
        with np.errstate(invalid="ignore"):
            m = np.nanmean(X, axis=1) if X.shape[1] else np.zeros(len(df))
        return np.where(np.isfinite(m), m, 0.0)


def _fit_ridge(X: np.ndarray, y: np.ndarray, alpha: float):
    """Closed-form ridge: standardize X, center y, penalize coefficients only."""
    mu = X.mean(axis=0)
    sd = X.std(axis=0, ddof=0)
    sd = np.where(sd > 0, sd, 1.0)
    Xs = (X - mu) / sd
    y_mean = float(y.mean())
    yc = y - y_mean
    d = Xs.shape[1]
    A = Xs.T @ Xs + alpha * np.eye(d)
    w = np.linalg.solve(A, Xs.T @ yc)
    return mu, sd, w, y_mean


def fit_predictor(train_df: pd.DataFrame, feature_cols: Sequence[str],
                  target_col: str = PRIMARY_TARGET, alpha: float = DEFAULT_ALPHA,
                  min_rows: int = MIN_RIDGE_ROWS):
    """Fit ridge if there is enough clean data, else the composite fallback."""
    feature_cols = list(feature_cols)
    sub = train_df.dropna(subset=[target_col])
    y = sub[target_col].to_numpy(dtype=float)
    X = _design_matrix(sub, feature_cols)
    enough = len(sub) >= max(min_rows, len(feature_cols) + 2) and X.shape[1] > 0
    if enough:
        try:
            mu, sd, w, y_mean = _fit_ridge(X, y, alpha)
            if np.all(np.isfinite(w)):
                return RidgeModel("ridge", feature_cols, mu, sd, w, y_mean)
        except np.linalg.LinAlgError:
            pass
    return CompositeModel("mean_zscore_composite", feature_cols)


# --------------------------------------------------------------------------- #
# Walk-forward training loop -> out-of-sample predictions
# --------------------------------------------------------------------------- #
def run_walk_forward(df: pd.DataFrame, target_col: str = PRIMARY_TARGET,
                     feature_cols: Optional[Sequence[str]] = None,
                     n_folds: int = DEFAULT_N_FOLDS, embargo: int = DEFAULT_EMBARGO,
                     min_train_dates: int = DEFAULT_MIN_TRAIN_DATES,
                     alpha: float = DEFAULT_ALPHA
                     ) -> Tuple[pd.DataFrame, List[Dict]]:
    """Train per fold on the past, predict the future test window (out-of-sample).

    Returns ``(predictions_df, fold_meta)``. ``predictions_df`` has exactly
    ``OOS_COLUMNS``; ``fold_meta`` carries per-fold row/date ranges. Pure given
    the DataFrame — no DB, no network.
    """
    if df.empty:
        return pd.DataFrame(columns=OOS_COLUMNS), []
    df = df.copy()
    df["as_of_date"] = [_as_date(d) for d in df["as_of_date"]]
    if feature_cols is None:
        feature_cols = feature_columns(df)
    feature_cols = list(feature_cols)

    dates = sorted(df["as_of_date"].unique())
    folds = make_walk_forward_folds(dates, n_folds=n_folds, embargo=embargo,
                                    min_train_dates=min_train_dates)

    out_frames: List[pd.DataFrame] = []
    fold_meta: List[Dict] = []
    for fold in folds:
        train_set = set(fold.train_dates)
        test_set = set(fold.test_dates)
        train_df = df[df["as_of_date"].isin(train_set)]
        test_df = df[df["as_of_date"].isin(test_set)]
        if test_df.empty:
            continue
        model = fit_predictor(train_df, feature_cols, target_col, alpha=alpha)
        scores = np.asarray(model.predict(test_df), dtype=float)
        out_frames.append(pd.DataFrame({
            "ticker": test_df["ticker"].to_numpy(),
            "as_of_date": test_df["as_of_date"].to_numpy(),
            "target_date": test_df["target_date"].to_numpy(),
            "fold_id": fold.fold_id,
            "model_name": model.name,
            "score": scores,
            "predicted_excess_return_5d": scores,
            "realized_excess_return_5d_vs_spy":
                test_df["realized_excess_return_5d_vs_spy"].to_numpy(),
            "realized_return_5d": test_df["realized_return_5d"].to_numpy(),
            "spy_return_5d": test_df["spy_return_5d"].to_numpy(),
            "outperform_spy_flag": test_df["outperform_spy_flag"].to_numpy(),
            "positive_return_flag": test_df["positive_return_flag"].to_numpy(),
        }))
        fold_meta.append({
            "fold_id": fold.fold_id,
            "model_name": model.name,
            "n_train_rows": int(len(train_df)),
            "n_test_rows": int(len(test_df)),
            "n_train_dates": len(fold.train_dates),
            "n_test_dates": len(fold.test_dates),
            "train_start": fold.train_dates[0],
            "train_end": fold.train_dates[-1],
            "test_start": fold.test_dates[0],
            "test_end": fold.test_dates[-1],
        })

    preds = (pd.concat(out_frames, ignore_index=True)[OOS_COLUMNS]
             if out_frames else pd.DataFrame(columns=OOS_COLUMNS))
    return preds, fold_meta


# --------------------------------------------------------------------------- #
# Synthetic, clearly-labeled sample dataset (no DB). AR(1) returns plant a real
# momentum signal so the harness can be demonstrated end-to-end. SYN_* tickers
# make it impossible to mistake for market data; the report never claims edge.
# --------------------------------------------------------------------------- #
def _ar1_series(ticker: str, n: int, seed: int, phi: float = 0.20,
                drift: Optional[float] = None,
                start: dt.date = dt.date(2023, 1, 2), with_volume: bool = True
                ) -> Tuple[TickerSeries, Optional[List[float]]]:
    rng = np.random.default_rng(seed)
    # A persistent per-ticker daily drift is the planted signal: trailing-return
    # momentum estimates it, and it also drives the forward return, so momentum
    # ranks the cross-section. (Plus mild AR(1) colour.) Purely synthetic.
    if drift is None:
        drift = float(rng.normal(0.0, 0.003))
    eps = rng.normal(0.0, 0.010, n)
    r = np.zeros(n, dtype=float)
    for t in range(1, n):
        r[t] = drift + phi * (r[t - 1] - drift) + eps[t]
    r[0] = drift + eps[0]
    adj = 100.0 * np.cumprod(1.0 + r)
    dates: List[dt.date] = []
    d = start
    for _ in range(n):
        while d.weekday() > 4:
            d += dt.timedelta(days=1)
        dates.append(d)
        d += dt.timedelta(days=1)
    series = TickerSeries(ticker=ticker, dates=dates, adj=[float(x) for x in adj])
    volume = ([float(x) for x in rng.integers(1_000_000, 5_000_000, n)]
              if with_volume else None)
    return series, volume


def build_synthetic_signal_dataset(n_tickers: int = 8, n_days: int = 300,
                                   min_history: int = 130,
                                   horizon: int = DEFAULT_HORIZON,
                                   phi: float = 0.20
                                   ) -> Tuple[pd.DataFrame, Dict[str, str]]:
    """Labeled, z-scored dataset from SYNTHETIC AR(1) prices (no DB, no real data).

    SPY has no autocorrelation (``phi=0``); each name has positive return
    autocorrelation so trailing-return momentum carries a planted, *synthetic*
    signal. Returns ``(dataframe, unavailable_families)``.
    """
    spy, _ = _ar1_series("SYN_SPY", n_days, seed=9999, phi=0.0, drift=0.0,
                         with_volume=False)
    frames: List[pd.DataFrame] = []
    for k in range(n_tickers):
        with_volume = k != 0  # first name has no volume (exercise disabled path)
        series, volume = _ar1_series(
            f"SYN_{chr(ord('A') + k)}", n_days, seed=k + 1, phi=phi,
            with_volume=with_volume)
        frames.append(build_labeled_dataset_for_ticker(
            series, spy, volume=volume, horizon=horizon, min_history=min_history))
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    df = cross_sectional_zscore(df)
    return df, unavailable_feature_families(volume_present=True)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
DEFAULT_PREDICTIONS_PATH = os.path.join(
    "research", "output", "phase2b_oos_predictions_sample.csv")
DEFAULT_REPORT_PATH = os.path.join(
    "docs", "phase2b_walk_forward_training_v1.md")


def _parse_date(s: Optional[str]) -> Optional[dt.date]:
    return dt.datetime.strptime(s, "%Y-%m-%d").date() if s else None


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Phase 2B walk-forward training + OOS evaluation "
                    "(offline/research). Writes a sample predictions CSV and a "
                    "markdown report. Reads the DB only when --db-url is given; "
                    "otherwise builds a clearly-labeled SYNTHETIC sample.")
    p.add_argument("--db-url", type=str, default=None,
                   help="Postgres URL. If omitted, a SYNTHETIC sample is used.")
    p.add_argument("--tickers", type=str, default=None,
                   help="Comma-separated tickers (DB mode). Default: universe.")
    p.add_argument("--max-tickers", type=int, default=30,
                   help="Cap tickers (DB mode) to keep the run small.")
    p.add_argument("--start-date", type=str, default=None, help="YYYY-MM-DD.")
    p.add_argument("--end-date", type=str, default=None, help="YYYY-MM-DD.")
    p.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON)
    p.add_argument("--min-history", type=int, default=DEFAULT_MIN_HISTORY)
    p.add_argument("--n-folds", type=int, default=DEFAULT_N_FOLDS)
    p.add_argument("--embargo", type=int, default=DEFAULT_EMBARGO,
                   help="Sessions purged between train and test (>= horizon).")
    p.add_argument("--min-train-dates", type=int, default=DEFAULT_MIN_TRAIN_DATES)
    p.add_argument("--alpha", type=float, default=DEFAULT_ALPHA,
                   help="Ridge L2 penalty.")
    p.add_argument("--predictions-output", type=str,
                   default=DEFAULT_PREDICTIONS_PATH)
    p.add_argument("--report-output", type=str, default=DEFAULT_REPORT_PATH)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    from model import eval as ev  # local import keeps module load light

    args = build_arg_parser().parse_args(argv)
    if args.embargo < args.horizon_days:
        print(f"[phase2b] WARNING: embargo ({args.embargo}) < horizon "
              f"({args.horizon_days}); raising embargo to horizon to prevent "
              f"label leakage.")
        args.embargo = args.horizon_days

    if args.db_url:
        tickers = ([t.strip() for t in args.tickers.split(",") if t.strip()]
                   if args.tickers else None)
        df, unavailable = build_feature_dataset(
            db_url=args.db_url, tickers=tickers, max_tickers=args.max_tickers,
            start_date=_parse_date(args.start_date),
            end_date=_parse_date(args.end_date),
            horizon=args.horizon_days, min_history=args.min_history,
            with_labels=True)
        df = cross_sectional_zscore(df)
        source = "DATABASE (real GCP DB, read-only)"
        is_synthetic = False
    else:
        df, unavailable = build_synthetic_signal_dataset(
            horizon=args.horizon_days)
        source = ("SYNTHETIC (planted momentum signal via persistent per-ticker "
                  "drift, SYN_* tickers; no --db-url given — NOT real market data)")
        is_synthetic = True

    preds, fold_meta = run_walk_forward(
        df, target_col=PRIMARY_TARGET, n_folds=args.n_folds,
        embargo=args.embargo, min_train_dates=args.min_train_dates,
        alpha=args.alpha)

    os.makedirs(os.path.dirname(args.predictions_output), exist_ok=True)
    preds.to_csv(args.predictions_output, index=False)

    meta = {
        "source": source,
        "is_synthetic": is_synthetic,
        "target": PRIMARY_TARGET,
        "secondary_target": SECONDARY_TARGET,
        "n_feature_cols": len(feature_columns(df)) if not df.empty else 0,
        "feature_cols": feature_columns(df) if not df.empty else [],
        "horizon": args.horizon_days,
        "embargo": args.embargo,
        "n_folds_requested": args.n_folds,
        "min_train_dates": args.min_train_dates,
        "alpha": args.alpha,
        "min_history": args.min_history,
        "generated_on": dt.date.today().isoformat(),
        "unavailable_families": unavailable,
        "predictions_path": args.predictions_output,
    }
    report_md = ev.build_report_markdown(preds, meta, fold_meta)
    os.makedirs(os.path.dirname(args.report_output), exist_ok=True)
    with open(args.report_output, "w", encoding="utf-8") as f:
        f.write(report_md)

    print(f"[phase2b] source           : {source}")
    print(f"[phase2b] dataset rows     : {len(df)}")
    print(f"[phase2b] feature columns  : {meta['n_feature_cols']}")
    print(f"[phase2b] folds            : {len(fold_meta)}")
    print(f"[phase2b] OOS prediction rows: {len(preds)}")
    if not preds.empty:
        ic = ev.rank_ic(preds)
        print(f"[phase2b] OOS rank IC      : {ic:.4f}")
        print(f"[phase2b] models used      : "
              f"{sorted(preds['model_name'].unique())}")
    print(f"[phase2b] predictions CSV  : {args.predictions_output}")
    print(f"[phase2b] report markdown  : {args.report_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
