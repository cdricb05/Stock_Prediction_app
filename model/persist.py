"""Phase 2D — offline prediction-output materialization (design only).

Turns the Phase 2C calibrated frame (probabilities + intervals + risk bands) into
a canonical **offline prediction-output table** with a transparent 5-level
recommendation and a cross-sectional rank, plus the model-version metadata
artifact (see :mod:`model.registry`). This is **offline / research only**: it
imports neither ``api_server`` nor Paper Trader, **never writes to any database**,
does not deploy, and is not on the live request path. numpy/pandas + stdlib only —
no sklearn, no xgboost, no pickle.

The output schema is designed to map cleanly onto a future ``prediction_outputs``
table, but **no table is created or modified in this phase** (DB write-back +
API read path are Phase 2E, behind the ``PREDICTOR_USE_MODEL_V2`` flag).
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from model import calibrate as C
from model import risk as R
from model import train as T
from model import registry as REG

# Canonical offline prediction-output schema (also the sample CSV column order).
# Maps 1:1 onto a future ``prediction_outputs`` table (no table created here).
PREDICTION_OUTPUT_COLUMNS = [
    "ticker", "as_of_date", "generated_at", "target_date",
    "model_version", "feature_set_version", "horizon_days",
    "score", "predicted_excess_return_5d", "prob_outperform_spy",
    "lo_return_5d", "hi_return_5d", "interval_width", "risk_band",
    "recommendation", "rank_in_universe", "source",
]

# The 5-level recommendation vocabulary (matches api_server's strings exactly so
# a future serving path needs no translation — but nothing is served here).
RECOMMENDATIONS = ("Strong Buy", "Buy", "Hold", "Sell", "Strong Sell")
# conviction ladder: +2 .. -2  <->  Strong Buy .. Strong Sell
_LEVEL_TO_REC = {2: "Strong Buy", 1: "Buy", 0: "Hold", -1: "Sell", -2: "Strong Sell"}

REQUIRED_REPORT_SECTIONS = [
    "## Objective",
    "## Registry metadata schema",
    "## Prediction output schema",
    "## Recommendation mapping",
    "## Ranking logic",
    "## Mapping to the future database table",
    "## Why this is still offline / research only",
    "## API unchanged",
    "## No database writes",
]


# --------------------------------------------------------------------------- #
# Recommendation mapping (transparent, deterministic, easy to retune)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RecommendationThresholds:
    """Calibrated-probability cut points for the 5-level recommendation.

    All thresholds are on ``prob_outperform_spy`` (a calibrated probability in
    ``[0, 1]``). They are intentionally simple and centred on 0.5 so they are easy
    to read and to retune. ``min_abs_excess`` requires a minimum *expected* edge
    before any non-Hold call; ``high_risk_downgrade`` softens conviction by one
    notch when the row's ``risk_band`` is ``high``.
    """
    strong_buy: float = 0.62
    buy: float = 0.55
    sell: float = 0.45
    strong_sell: float = 0.38
    min_abs_excess: float = 0.0
    high_risk_downgrade: bool = True


DEFAULT_THRESHOLDS = RecommendationThresholds()


def _conviction_level(prob: float, predicted_excess: float, risk_band: str,
                      t: RecommendationThresholds) -> int:
    """Map (prob, expected excess, risk_band) to a conviction level in [-2, 2]."""
    if not (isinstance(prob, (int, float)) and np.isfinite(prob)):
        return 0
    exc = predicted_excess if (isinstance(predicted_excess, (int, float))
                               and np.isfinite(predicted_excess)) else 0.0
    # Bullish side requires prob above the cut AND a non-negative expected edge.
    if prob >= t.strong_buy and exc >= t.min_abs_excess:
        level = 2
    elif prob >= t.buy and exc >= t.min_abs_excess:
        level = 1
    elif prob <= t.strong_sell and exc <= -t.min_abs_excess:
        level = -2
    elif prob <= t.sell and exc <= -t.min_abs_excess:
        level = -1
    else:
        level = 0
    # A high-risk (wide interval per unit of signal) call loses one notch of
    # conviction toward Hold — never flips direction.
    if t.high_risk_downgrade and risk_band == "high" and level != 0:
        level = int(np.sign(level) * (abs(level) - 1))
    return level


def map_recommendation(prob: float, predicted_excess: float, risk_band: str = "",
                       thresholds: RecommendationThresholds = DEFAULT_THRESHOLDS
                       ) -> str:
    """Deterministic 5-level recommendation from prob + expected excess + risk."""
    return _LEVEL_TO_REC[_conviction_level(prob, predicted_excess, risk_band,
                                           thresholds)]


def add_recommendations(frame: pd.DataFrame,
                        thresholds: RecommendationThresholds = DEFAULT_THRESHOLDS
                        ) -> pd.Series:
    """Vectorized (row-wise) recommendation column for a calibrated frame."""
    if frame.empty:
        return pd.Series([], dtype=object)
    recs = [
        map_recommendation(p, e, b, thresholds)
        for p, e, b in zip(frame[C.PROB_COL].tolist(),
                           frame["predicted_excess_return_5d"].tolist(),
                           frame["risk_band"].tolist())
    ]
    return pd.Series(recs, index=frame.index, dtype=object)


# --------------------------------------------------------------------------- #
# Cross-sectional ranking (best score -> rank 1, stable & deterministic)
# --------------------------------------------------------------------------- #
def rank_within_date(frame: pd.DataFrame) -> pd.Series:
    """Rank rows 1..n within each ``as_of_date`` by score (descending).

    Best (highest) score gets rank 1. Ties are broken by ``ticker`` ascending so
    the ordering is **stable and deterministic** (no random tie-breaks). Rows with
    a non-finite score are ranked last (after all finite scores), still by ticker.
    """
    if frame.empty:
        return pd.Series([], dtype="int64")
    ranks = pd.Series(0, index=frame.index, dtype="int64")
    score = frame["score"].to_numpy(dtype=float)
    finite = np.isfinite(score)
    for _, idx in frame.groupby("as_of_date", sort=True).groups.items():
        sub = frame.loc[idx]
        s = sub["score"].to_numpy(dtype=float)
        fin = np.isfinite(s)
        # sort key: finite-first, then score desc, then ticker asc (stable)
        order = sorted(
            range(len(sub)),
            key=lambda i: (0 if fin[i] else 1,
                           -s[i] if fin[i] else 0.0,
                           str(sub["ticker"].iloc[i])),
        )
        for rank, pos in enumerate(order, start=1):
            ranks.loc[sub.index[pos]] = rank
    return ranks


# --------------------------------------------------------------------------- #
# Materialize the canonical prediction-output frame
# --------------------------------------------------------------------------- #
def build_prediction_outputs(calibrated: pd.DataFrame,
                             meta: REG.ModelVersionMetadata, source: str,
                             generated_at: Optional[dt.datetime] = None,
                             thresholds: RecommendationThresholds = DEFAULT_THRESHOLDS
                             ) -> pd.DataFrame:
    """Project the Phase 2C calibrated frame onto ``PREDICTION_OUTPUT_COLUMNS``."""
    if calibrated.empty:
        return pd.DataFrame(columns=PREDICTION_OUTPUT_COLUMNS)
    generated_at = generated_at or dt.datetime.now()
    out = pd.DataFrame(index=calibrated.index)
    out["ticker"] = calibrated["ticker"].to_numpy()
    out["as_of_date"] = calibrated["as_of_date"].to_numpy()
    out["generated_at"] = generated_at.isoformat(timespec="seconds")
    out["target_date"] = calibrated["target_date"].to_numpy()
    out["model_version"] = meta.model_version
    out["feature_set_version"] = meta.feature_set_version
    out["horizon_days"] = meta.horizon_days
    for col in ("score", "predicted_excess_return_5d", "prob_outperform_spy",
                "lo_return_5d", "hi_return_5d", "interval_width", "risk_band"):
        out[col] = calibrated[col].to_numpy()
    out["recommendation"] = add_recommendations(calibrated, thresholds).to_numpy()
    out["rank_in_universe"] = rank_within_date(calibrated).to_numpy()
    out["source"] = source
    return out[PREDICTION_OUTPUT_COLUMNS]


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def _rec_distribution(frame: pd.DataFrame) -> Dict[str, int]:
    if frame.empty:
        return {}
    counts = frame["recommendation"].value_counts().to_dict()
    return {r: int(counts.get(r, 0)) for r in RECOMMENDATIONS if counts.get(r, 0)}


def build_report_markdown(meta: REG.ModelVersionMetadata, outputs: pd.DataFrame,
                          extra: Dict, thresholds: RecommendationThresholds
                          ) -> str:
    is_syn = meta.is_synthetic
    lines: List[str] = []
    a = lines.append

    a("# Phase 2D — Model Artifact + Prediction Persistence Design (v1)")
    a("")
    a("_Offline / research only. This phase defines a reproducible way to **save** "
      "the Phase 2B/2C output: model-version metadata, the canonical prediction "
      "row schema, the recommendation mapping, the ranking rule, and the promotion "
      "gate results. It changes **no** live behavior — `api_server.py` is "
      "untouched, **nothing is written to any database**, there is no deploy, and "
      "it is not on the request path._")
    a("")
    if is_syn:
        a("> **DATA SOURCE: SYNTHETIC SAMPLE.** Built on the same synthetic "
          "`SYN_*` series as Phase 2B/2C (a *planted* signal, not market data). "
          "**These rows are NOT a market result and do NOT constitute evidence of "
          "a production edge** — they only demonstrate the artifact + persistence "
          "design end-to-end. This synthetic/sample output is not production edge.")
    else:
        a(f"> **DATA SOURCE: REAL GCP DB (read-only).** `{meta.training_source}`.")
    a("")

    a("## Objective")
    a("")
    a("Take the Phase 2B/2C output and define a reproducible, auditable way to save:")
    a("")
    a("1. model-version metadata,")
    a("2. the feature-set version,")
    a("3. calibration / risk metadata,")
    a("4. daily prediction rows, and")
    a("5. promotion gate results.")
    a("")
    a("This is **not** API integration — connecting these rows to the live service "
      "is Phase 2E, behind the `PREDICTOR_USE_MODEL_V2` flag.")
    a("")

    a("## Registry metadata schema")
    a("")
    a("`model.registry.ModelVersionMetadata` is a JSON-serializable record (stdlib "
      "`json`, **no pickle**). Required fields:")
    a("")
    a("| field | meaning |")
    a("|---|---|")
    a("| `model_version` | deterministic id `<prefix>-<model>-<date>-<hash6>` |")
    a("| `feature_set_version` | id over the sorted Phase 2A feature names `<prefix>-<n>f-<hash8>` |")
    a("| `created_at` | ISO timestamp the artifact was built |")
    a("| `training_source` | SYNTHETIC sample or the read-only DB descriptor |")
    a("| `training_start_date` / `training_end_date` | training window (ISO dates) |")
    a("| `horizon_days` | forecast horizon (sessions) |")
    a("| `model_name` | underlying model (e.g. `ridge`) |")
    a("| `calibration_method` | how `prob_outperform_spy` was produced |")
    a("| `interval_method` | how `lo/hi_return_5d` was produced |")
    a("| `decision_gate_summary` | plan §8 gate results (3 & 5 here) |")
    a("| `metrics_summary` | Brier / coverage / drawdown summary |")
    a("")
    a("It round-trips: `ModelVersionMetadata.from_json(m.to_json()) == m`. NaN/Inf "
      "and numpy scalars are sanitized to valid JSON before writing.")
    a("")
    a("Sample artifact for this run:")
    a("")
    a("```json")
    a(meta.to_json())
    a("```")
    a("")

    a("## Prediction output schema")
    a("")
    a("`model.persist.PREDICTION_OUTPUT_COLUMNS` — one row per "
      "`(ticker, as_of_date)` prediction:")
    a("")
    a("| column | meaning |")
    a("|---|---|")
    rowdoc = {
        "ticker": "instrument symbol",
        "as_of_date": "point-in-time anchor of the features",
        "generated_at": "when this offline row was materialized",
        "target_date": "date the horizon return resolves",
        "model_version": "FK to the registry metadata",
        "feature_set_version": "feature-set id used",
        "horizon_days": "forecast horizon (sessions)",
        "score": "raw ranking score",
        "predicted_excess_return_5d": "point prediction of excess vs SPY",
        "prob_outperform_spy": "calibrated probability in [0,1]",
        "lo_return_5d": "conformal lower bound",
        "hi_return_5d": "conformal upper bound",
        "interval_width": "hi - lo",
        "risk_band": "low / medium / high",
        "recommendation": "5-level call (see below)",
        "rank_in_universe": "cross-sectional rank within as_of_date (1 = best)",
        "source": "SYNTHETIC sample or read-only DB descriptor",
    }
    for col in PREDICTION_OUTPUT_COLUMNS:
        a(f"| `{col}` | {rowdoc[col]} |")
    a("")
    a(f"Sample rows written: **{len(outputs)}** "
      f"(see `{extra.get('csv_path', '')}`).")
    a("")

    a("## Recommendation mapping")
    a("")
    a("A transparent, deterministic mapping from the **calibrated probability**, "
      "the **expected excess return**, and the **risk band** to a 5-level call. "
      "Conviction is a ladder (+2 Strong Buy … -2 Strong Sell); a `high` risk_band "
      "softens conviction by one notch toward Hold (never flips direction).")
    a("")
    a("| condition | recommendation |")
    a("|---|---|")
    a(f"| `prob >= {thresholds.strong_buy}` and expected excess >= "
      f"{thresholds.min_abs_excess} | Strong Buy |")
    a(f"| `prob >= {thresholds.buy}` and expected excess >= "
      f"{thresholds.min_abs_excess} | Buy |")
    a(f"| `{thresholds.sell} < prob < {thresholds.buy}` (or edge wrong sign) | Hold |")
    a(f"| `prob <= {thresholds.sell}` and expected excess <= "
      f"-{thresholds.min_abs_excess} | Sell |")
    a(f"| `prob <= {thresholds.strong_sell}` and expected excess <= "
      f"-{thresholds.min_abs_excess} | Strong Sell |")
    a("")
    a("Thresholds live in `RecommendationThresholds` (one dataclass, easy to "
      "retune). The strings match `api_server`'s vocabulary exactly, so a future "
      "serving path needs no translation — **but nothing is served here.**")
    a("")
    dist = _rec_distribution(outputs)
    if dist:
        a("Distribution on this sample:")
        a("")
        a("| recommendation | n |")
        a("|---|---|")
        for r in RECOMMENDATIONS:
            if dist.get(r):
                a(f"| {r} | {dist[r]} |")
        a("")

    a("## Ranking logic")
    a("")
    a("`rank_within_date` ranks rows **within each `as_of_date`** by `score` "
      "descending — the best score gets `rank_in_universe = 1`. Ties break by "
      "`ticker` ascending and non-finite scores rank last, so the ordering is "
      "**stable and deterministic** (no random tie-breaks).")
    a("")

    a("## Mapping to the future database table")
    a("")
    a("`PREDICTION_OUTPUT_COLUMNS` is designed to map 1:1 onto a future "
      "`prediction_outputs` table and `ModelVersionMetadata` onto a "
      "`model_registry` row (`model_version` is the foreign key). The natural "
      "primary key is `(model_version, ticker, as_of_date)`. **No table is created "
      "or altered in this phase** — this is a schema *design*, materialized only to "
      "local CSV/JSON. The actual write-back and the API read path are Phase 2E, "
      "gated by `PREDICTOR_USE_MODEL_V2`.")
    a("")

    a("## Why this is still offline / research only")
    a("")
    a("The numbers above are produced from a clearly-labeled "
      f"{'SYNTHETIC sample' if is_syn else 'read-only DB slice'} and are not a "
      "promotion signal. Promotion requires all five plan §8 gates passing on the "
      "real GCP DB across two non-overlapping sub-periods. Persistence here means "
      "**writing local files only**; connecting to the live service is deferred to "
      "Phase 2E.")
    a("")
    g = meta.decision_gate_summary or {}
    if g:
        a("Promotion gate summary carried in the metadata:")
        a("")
        a("| gate | evaluable | passed |")
        a("|---|---|---|")
        for key in ("gate3", "gate5"):
            gv = g.get(key, {})
            a(f"| {key} | {gv.get('evaluable')} | {gv.get('passed')} |")
        a("")

    a("## API unchanged")
    a("")
    a("`api_server.py` is **not modified** by this work. `/predict` and "
      "`/predict_all_models/` behave exactly as before; this Phase 2D layer is "
      "pure offline research and is not on the live request path.")
    a("")

    a("## No database writes")
    a("")
    a("This phase performs **no database writes of any kind**. The DB is read "
      "**only** when `--db-url` is supplied (the same read-only path as Phase "
      "2B/2C); the default run is fully synthetic and offline. Output is written "
      "exclusively to local CSV/JSON/markdown files.")
    a("")

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
DEFAULT_CSV_PATH = os.path.join(
    "research", "output", "phase2d_prediction_outputs_sample.csv")
DEFAULT_METADATA_PATH = os.path.join(
    "research", "output", "phase2d_model_metadata_sample.json")
DEFAULT_REPORT_PATH = os.path.join(
    "docs", "phase2d_artifact_persistence_v1.md")


def _parse_date(s: Optional[str]) -> Optional[dt.date]:
    return dt.datetime.strptime(s, "%Y-%m-%d").date() if s else None


def _date_range(df: pd.DataFrame):
    if df.empty or "as_of_date" not in df.columns:
        return None, None
    ds = sorted(pd.to_datetime(df["as_of_date"]).dt.date.unique())
    return (ds[0], ds[-1]) if ds else (None, None)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Phase 2D artifact + prediction persistence design "
                    "(offline/research). Builds the Phase 2B/2C sample, "
                    "materializes the canonical prediction-output CSV + model "
                    "metadata JSON + a markdown report. NO database writes. Reads "
                    "the DB only with --db-url (read-only); otherwise SYNTHETIC.")
    p.add_argument("--db-url", type=str, default=None,
                   help="Postgres URL (read-only). Omit for a SYNTHETIC sample.")
    p.add_argument("--tickers", type=str, default=None)
    p.add_argument("--max-tickers", type=int, default=30)
    p.add_argument("--start-date", type=str, default=None)
    p.add_argument("--end-date", type=str, default=None)
    p.add_argument("--horizon-days", type=int, default=T.DEFAULT_HORIZON)
    p.add_argument("--min-history", type=int, default=T.DEFAULT_MIN_HISTORY)
    p.add_argument("--n-folds", type=int, default=T.DEFAULT_N_FOLDS)
    p.add_argument("--embargo", type=int, default=T.DEFAULT_EMBARGO)
    p.add_argument("--min-train-dates", type=int, default=T.DEFAULT_MIN_TRAIN_DATES)
    p.add_argument("--alpha", type=float, default=T.DEFAULT_ALPHA)
    p.add_argument("--coverage", type=float, default=R.DEFAULT_COVERAGE)
    p.add_argument("--output", type=str, default=DEFAULT_CSV_PATH)
    p.add_argument("--metadata-output", type=str, default=DEFAULT_METADATA_PATH)
    p.add_argument("--report-output", type=str, default=DEFAULT_REPORT_PATH)
    return p


def run_sample_flow(args) -> Dict:
    """Build calibrated preds -> metadata + prediction-output rows + report.

    Pure of any DB *writes*. Returns the artifacts and paths (also writes files).
    """
    embargo = max(args.embargo, args.horizon_days)

    if args.db_url:
        tickers = ([t.strip() for t in args.tickers.split(",") if t.strip()]
                   if args.tickers else None)
        df, _ = T.build_feature_dataset(
            db_url=args.db_url, tickers=tickers, max_tickers=args.max_tickers,
            start_date=_parse_date(args.start_date),
            end_date=_parse_date(args.end_date),
            horizon=args.horizon_days, min_history=args.min_history,
            with_labels=True)
        df = T.cross_sectional_zscore(df)
        source = "DATABASE (real GCP DB, read-only)"
        is_synthetic = False
    else:
        df, _ = T.build_synthetic_signal_dataset(horizon=args.horizon_days)
        source = ("SYNTHETIC (Phase 2B/2C planted-signal SYN_* sample; no --db-url "
                  "given — NOT real market data)")
        is_synthetic = True

    preds, _ = T.run_walk_forward(
        df, target_col=T.PRIMARY_TARGET, n_folds=args.n_folds, embargo=embargo,
        min_train_dates=args.min_train_dates, alpha=args.alpha)
    calibrated = C.build_calibrated_frame(preds, coverage=args.coverage)

    # Gate + metrics summaries (plan §8 gates 3 & 5; gate 4 deferred to Phase 2E).
    g3 = C.gate3(calibrated) if not calibrated.empty else {"evaluable": False,
                                                           "passed": None}
    g5 = R.gate5(calibrated, coverage=args.coverage) if not calibrated.empty \
        else {"evaluable": False, "passed": None}
    cal_eval = C.evaluate_calibration(calibrated) if not calibrated.empty else {}
    iv_eval = R.evaluate_intervals(calibrated) if not calibrated.empty else {}
    gate_summary = {
        "gate3": {"name": g3.get("name"), "evaluable": g3.get("evaluable"),
                  "passed": g3.get("passed")},
        "gate5": {"name": g5.get("name"), "evaluable": g5.get("evaluable"),
                  "passed": g5.get("passed")},
        "gate4": {"name": "BUY basket beats SPY + universe after costs",
                  "evaluable": False, "passed": None,
                  "note": "deferred to Phase 2E (decision/cost backtest)"},
    }
    metrics_summary = {
        "n_rows": int(len(calibrated)),
        "brier": cal_eval.get("brier"),
        "brier_base_rate": cal_eval.get("brier_base_rate"),
        "base_rate": cal_eval.get("base_rate"),
        "interval_coverage": iv_eval.get("coverage"),
        "target_coverage": args.coverage,
        "avg_interval_width": iv_eval.get("avg_width"),
        "drawdown": iv_eval.get("drawdown", {}),
    }

    model_name = (sorted(preds["model_name"].unique())[0]
                  if not preds.empty else "ridge")
    train_start, train_end = _date_range(df)
    feature_cols = T.feature_columns(df) if not df.empty else T.feature_columns(
        pd.DataFrame())
    meta = REG.build_metadata(
        model_name=model_name, feature_cols=feature_cols, training_source=source,
        training_start_date=train_start, training_end_date=train_end,
        horizon_days=args.horizon_days,
        calibration_method=("isotonic (PAVA) -> Platt fallback -> base-rate; "
                            "walk-forward, prior folds only"),
        interval_method=(f"split-conformal signed-residual quantiles "
                         f"(coverage {args.coverage}); prior folds only"),
        decision_gate_summary=gate_summary, metrics_summary=metrics_summary,
        is_synthetic=is_synthetic)

    outputs = build_prediction_outputs(calibrated, meta, source)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    outputs.to_csv(args.output, index=False)
    os.makedirs(os.path.dirname(args.metadata_output), exist_ok=True)
    meta.write_json(args.metadata_output)

    report = build_report_markdown(meta, outputs,
                                   {"csv_path": args.output}, DEFAULT_THRESHOLDS)
    os.makedirs(os.path.dirname(args.report_output), exist_ok=True)
    with open(args.report_output, "w", encoding="utf-8") as f:
        f.write(report)

    return {"meta": meta, "outputs": outputs, "gate_summary": gate_summary,
            "csv_path": args.output, "metadata_path": args.metadata_output,
            "report_path": args.report_output, "source": source,
            "is_synthetic": is_synthetic}


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    res = run_sample_flow(args)
    meta = res["meta"]
    outputs = res["outputs"]
    print(f"[phase2d] source            : {res['source']}")
    print(f"[phase2d] model_version     : {meta.model_version}")
    print(f"[phase2d] feature_set_ver   : {meta.feature_set_version}")
    print(f"[phase2d] prediction rows   : {len(outputs)}")
    print(f"[phase2d] recommendations   : {_rec_distribution(outputs)}")
    print(f"[phase2d] gate 3 (calib)    : "
          f"{res['gate_summary']['gate3']['passed']}")
    print(f"[phase2d] gate 5 (interval) : "
          f"{res['gate_summary']['gate5']['passed']}")
    print(f"[phase2d] predictions CSV   : {res['csv_path']}")
    print(f"[phase2d] metadata JSON     : {res['metadata_path']}")
    print(f"[phase2d] report markdown   : {res['report_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
