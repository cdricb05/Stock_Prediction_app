"""Phase 2E-A — storage layer + idempotent SQL design (offline only).

Prepares the database storage layer for model-v2 predictions **without**
connecting it to the live API and **without writing to the production
database**. This module:

  * validates Phase 2D model-version metadata and prediction-output rows,
  * converts the Phase 2D CSV/JSON artifacts into DB-ready records that line up
    1:1 with ``migrations/phase2e_prediction_outputs.sql``, and
  * supports a **dry-run** mode (the default) that writes only a local JSON
    summary and never touches a database.

Safety contract (enforced by tests):
  * **No DB connection at import time** and **no env var reads at import time.**
  * A database is touched **only** when ``db_url`` is explicitly supplied **and**
    ``commit=True`` is explicitly passed. The default is ``dry_run`` /
    ``commit=False`` — a pure local-file operation.
  * Imports neither ``api_server`` nor Paper Trader; stdlib + numpy/pandas only —
    no sklearn, no xgboost, no pickle.

The actual write-back and the API read path are Phase 2E-B, behind the
``PREDICTOR_USE_MODEL_V2`` flag. The migration is **not run** in this phase.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from model import registry as REG

# Columns that back ``model_registry`` (see the SQL migration). Order is the
# canonical insert order; ``schema_version`` is optional metadata.
MODEL_REGISTRY_COLUMNS = [
    "model_version", "feature_set_version", "created_at", "training_source",
    "training_start_date", "training_end_date", "horizon_days", "model_name",
    "calibration_method", "interval_method", "decision_gate_summary",
    "metrics_summary", "is_synthetic", "schema_version",
]

# Columns that back ``prediction_outputs`` (the Phase 2D canonical schema).
PREDICTION_OUTPUT_COLUMNS = [
    "model_version", "ticker", "as_of_date", "generated_at", "target_date",
    "feature_set_version", "horizon_days", "score",
    "predicted_excess_return_5d", "prob_outperform_spy", "lo_return_5d",
    "hi_return_5d", "interval_width", "risk_band", "recommendation",
    "rank_in_universe", "source",
]

# Natural key that protects against duplicate prediction rows.
PREDICTION_OUTPUT_NATURAL_KEY = ("model_version", "ticker", "as_of_date")

# Fields that must be present and non-empty on every metadata row.
_REQUIRED_METADATA_FIELDS = [
    "model_version", "feature_set_version", "created_at", "training_source",
    "horizon_days", "model_name",
]
# Fields that must be present and non-empty on every prediction row.
_REQUIRED_PREDICTION_FIELDS = [
    "model_version", "ticker", "as_of_date", "feature_set_version",
    "horizon_days",
]


# --------------------------------------------------------------------------- #
# Validation result
# --------------------------------------------------------------------------- #
@dataclass
class ValidationResult:
    """Outcome of validating a batch of rows."""
    ok: bool
    n_rows: int
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": bool(self.ok), "n_rows": int(self.n_rows),
                "errors": list(self.errors)}


def _is_missing(v: Any) -> bool:
    """True for None / NaN / empty-string-like values."""
    if v is None:
        return True
    if isinstance(v, float) and not np.isfinite(v):
        return True
    if isinstance(v, str) and not v.strip():
        return True
    try:
        # pandas NaT / NaN
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# Pure validation helpers
# --------------------------------------------------------------------------- #
def validate_metadata_row(row: Dict[str, Any]) -> List[str]:
    """Return a list of human-readable problems with one metadata row (empty=ok)."""
    errors: List[str] = []
    for fld in _REQUIRED_METADATA_FIELDS:
        if fld not in row or _is_missing(row.get(fld)):
            errors.append(f"missing required metadata field: {fld}")
    # horizon_days must be a positive integer
    h = row.get("horizon_days")
    if not _is_missing(h):
        try:
            if int(h) <= 0:
                errors.append(f"horizon_days must be positive, got {h!r}")
        except (TypeError, ValueError):
            errors.append(f"horizon_days must be an integer, got {h!r}")
    # the two summaries, when present, must be JSON-serializable objects
    for fld in ("decision_gate_summary", "metrics_summary"):
        if fld in row and row[fld] is not None and not isinstance(row[fld], dict):
            errors.append(f"{fld} must be an object/dict, got {type(row[fld]).__name__}")
    return errors


def validate_metadata(meta: REG.ModelVersionMetadata) -> ValidationResult:
    """Validate a :class:`ModelVersionMetadata` instance (one registry row)."""
    errors = validate_metadata_row(meta.to_dict())
    return ValidationResult(ok=not errors, n_rows=1, errors=errors)


def validate_prediction_rows(frame: pd.DataFrame) -> ValidationResult:
    """Validate a prediction-output frame against the canonical schema.

    Checks required columns exist, required fields are non-empty, the probability
    is in ``[0, 1]`` where present, and the natural key is unique (no duplicates).
    """
    errors: List[str] = []
    if frame is None or frame.empty:
        return ValidationResult(ok=True, n_rows=0, errors=[])

    missing_cols = [c for c in PREDICTION_OUTPUT_COLUMNS if c not in frame.columns]
    if missing_cols:
        errors.append(f"missing columns: {missing_cols}")
        return ValidationResult(ok=False, n_rows=len(frame), errors=errors)

    for fld in _REQUIRED_PREDICTION_FIELDS:
        n_bad = int(frame[fld].map(_is_missing).sum())
        if n_bad:
            errors.append(f"{n_bad} row(s) missing required field {fld!r}")

    # probability sanity (only where present)
    prob = pd.to_numeric(frame["prob_outperform_spy"], errors="coerce")
    bad_prob = int(((prob < 0) | (prob > 1)).sum())
    if bad_prob:
        errors.append(f"{bad_prob} row(s) with prob_outperform_spy outside [0,1]")

    # natural-key uniqueness (duplicate protection mirrors the SQL PK)
    key = list(PREDICTION_OUTPUT_NATURAL_KEY)
    n_dup = int(frame.duplicated(subset=key).sum())
    if n_dup:
        errors.append(f"{n_dup} duplicate row(s) on natural key {tuple(key)}")

    return ValidationResult(ok=not errors, n_rows=len(frame), errors=errors)


# --------------------------------------------------------------------------- #
# Convert Phase 2D artifacts -> DB-ready records
# --------------------------------------------------------------------------- #
def metadata_to_record(meta: REG.ModelVersionMetadata) -> Dict[str, Any]:
    """Project metadata onto ``MODEL_REGISTRY_COLUMNS`` (JSON-safe values)."""
    d = meta.to_dict()
    return {col: d.get(col) for col in MODEL_REGISTRY_COLUMNS}


def prediction_frame_to_records(frame: pd.DataFrame) -> List[Dict[str, Any]]:
    """Project a prediction frame onto ``PREDICTION_OUTPUT_COLUMNS`` records.

    Values are sanitized through :func:`registry.jsonsafe` so dates become ISO
    strings and NaN/Inf/numpy scalars become plain JSON-safe values — i.e. the
    records are ready for either a JSON dry-run summary or a parameterized insert.
    """
    if frame is None or frame.empty:
        return []
    present = [c for c in PREDICTION_OUTPUT_COLUMNS if c in frame.columns]
    sub = frame[present]
    records: List[Dict[str, Any]] = []
    for rec in sub.to_dict(orient="records"):
        records.append({col: REG.jsonsafe(rec.get(col))
                        for col in PREDICTION_OUTPUT_COLUMNS})
    return records


# --------------------------------------------------------------------------- #
# Loading the Phase 2D sample artifacts
# --------------------------------------------------------------------------- #
DEFAULT_PRED_CSV = os.path.join(
    "research", "output", "phase2d_prediction_outputs_sample.csv")
DEFAULT_META_JSON = os.path.join(
    "research", "output", "phase2d_model_metadata_sample.json")
DEFAULT_SUMMARY_PATH = os.path.join(
    "research", "output", "phase2e_store_dry_run_summary.json")


def load_phase2d_metadata(path: str = DEFAULT_META_JSON
                          ) -> REG.ModelVersionMetadata:
    return REG.ModelVersionMetadata.read_json(path)


def load_phase2d_predictions(path: str = DEFAULT_PRED_CSV) -> pd.DataFrame:
    df = pd.read_csv(path)
    # keep only canonical columns, in canonical order, if all present
    cols = [c for c in PREDICTION_OUTPUT_COLUMNS if c in df.columns]
    return df[cols]


# --------------------------------------------------------------------------- #
# Persist (dry-run by default; DB write only on explicit commit=True)
# --------------------------------------------------------------------------- #
def _build_engine(db_url: str):
    """Lazily build a SQLAlchemy engine. Never called at import / dry-run."""
    from sqlalchemy import create_engine  # local import: optional dependency
    return create_engine(db_url, pool_pre_ping=True)


def persist(meta: REG.ModelVersionMetadata, predictions: pd.DataFrame, *,
            db_url: Optional[str] = None, commit: bool = False,
            summary_path: Optional[str] = DEFAULT_SUMMARY_PATH,
            ) -> Dict[str, Any]:
    """Validate + (dry-run) persist Phase 2D artifacts.

    Default behavior is a **dry run**: validate everything, compute how many rows
    *would* be written, and emit a local JSON summary. **No database is contacted
    unless ``db_url`` is given AND ``commit=True``.**
    """
    meta_result = validate_metadata(meta)
    pred_result = validate_prediction_rows(predictions)

    meta_record = metadata_to_record(meta)
    pred_records = prediction_frame_to_records(predictions)

    would_write_model_rows = 1 if meta_result.ok else 0
    would_write_prediction_rows = pred_result.n_rows if pred_result.ok else 0

    # Decide whether an actual write is permitted. Both conditions are required;
    # the default path takes neither and never touches a DB.
    commit_enabled = bool(commit and db_url)
    database_touched = False
    write_error: Optional[str] = None

    if commit and not db_url:
        write_error = "commit=True requires an explicit db_url; refusing to write."

    if commit_enabled:
        if not (meta_result.ok and pred_result.ok):
            write_error = "validation failed; refusing to write to the database."
        else:
            database_touched = True  # an explicit, opt-in write was performed
            _write_to_db(db_url, meta_record, pred_records)

    summary = {
        "phase": "2E-A",
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "model_version": meta.model_version,
        "feature_set_version": meta.feature_set_version,
        "detected_source": meta.training_source,
        "is_synthetic": bool(meta.is_synthetic),
        "metadata_rows_validated": meta_result.n_rows,
        "prediction_rows_validated": pred_result.n_rows,
        "metadata_validation": meta_result.to_dict(),
        "prediction_validation": pred_result.to_dict(),
        "would_write_model_registry_rows": int(would_write_model_rows),
        "would_write_prediction_output_rows": int(would_write_prediction_rows),
        "natural_key": list(PREDICTION_OUTPUT_NATURAL_KEY),
        "commit_enabled": bool(commit_enabled),
        "database_touched": bool(database_touched),
        "write_error": write_error,
        "migration_file": os.path.join(
            "migrations", "phase2e_prediction_outputs.sql"),
        "migration_executed": False,
    }

    if summary_path:
        os.makedirs(os.path.dirname(summary_path), exist_ok=True)
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, allow_nan=False)
        summary["summary_path"] = summary_path

    return summary


def _write_to_db(db_url: str, meta_record: Dict[str, Any],
                 pred_records: List[Dict[str, Any]]) -> None:
    """Explicit, opt-in upsert. Only reached when commit=True AND db_url given.

    Uses parameterized SQLAlchemy statements with ON CONFLICT DO NOTHING so the
    natural key protects against duplicates. This is **not** used in Phase 2E-A
    (no tests/docs call it with commit=True) — it exists so Phase 2E-B can flip
    the flag without restructuring this module.
    """
    from sqlalchemy import text  # local import: optional dependency
    engine = _build_engine(db_url)
    reg_cols = list(MODEL_REGISTRY_COLUMNS)
    reg_sql = text(
        f"INSERT INTO model_registry ({', '.join(reg_cols)}) "
        f"VALUES ({', '.join(':' + c for c in reg_cols)}) "
        f"ON CONFLICT (model_version) DO NOTHING")
    # JSONB params must be serialized strings.
    reg_params = dict(meta_record)
    for jcol in ("decision_gate_summary", "metrics_summary"):
        if reg_params.get(jcol) is not None:
            reg_params[jcol] = json.dumps(reg_params[jcol], allow_nan=False)

    pred_cols = list(PREDICTION_OUTPUT_COLUMNS)
    pred_sql = text(
        f"INSERT INTO prediction_outputs ({', '.join(pred_cols)}) "
        f"VALUES ({', '.join(':' + c for c in pred_cols)}) "
        f"ON CONFLICT (model_version, ticker, as_of_date) DO NOTHING")

    with engine.begin() as cx:
        cx.execute(reg_sql, reg_params)
        if pred_records:
            cx.execute(pred_sql, pred_records)
    engine.dispose()


# --------------------------------------------------------------------------- #
# CLI (dry-run by default; never connects unless --db-url AND --commit)
# --------------------------------------------------------------------------- #
def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Phase 2E-A storage layer (offline). Validates the Phase 2D "
                    "sample artifacts and writes a local dry-run summary. NO "
                    "database connection by default; even with --db-url it will "
                    "NOT write unless --commit is also passed (do not run yet).")
    p.add_argument("--predictions", type=str, default=DEFAULT_PRED_CSV,
                   help="Phase 2D prediction-output CSV to validate.")
    p.add_argument("--metadata", type=str, default=DEFAULT_META_JSON,
                   help="Phase 2D model-metadata JSON to validate.")
    p.add_argument("--summary-output", type=str, default=DEFAULT_SUMMARY_PATH,
                   help="Where to write the dry-run summary JSON.")
    p.add_argument("--db-url", type=str, default=None,
                   help="Postgres URL. Omit for a pure dry run (the default).")
    p.add_argument("--commit", action="store_true",
                   help="DO NOT USE YET. Future Phase 2E-B switch to actually "
                        "write rows; requires --db-url. Off by default.")
    return p


def run(args) -> Dict[str, Any]:
    meta = load_phase2d_metadata(args.metadata)
    predictions = load_phase2d_predictions(args.predictions)
    return persist(meta, predictions, db_url=args.db_url, commit=args.commit,
                   summary_path=args.summary_output)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    summary = run(args)
    print(f"[phase2e-a] model_version          : {summary['model_version']}")
    print(f"[phase2e-a] detected_source        : {summary['detected_source']}")
    print(f"[phase2e-a] is_synthetic           : {summary['is_synthetic']}")
    print(f"[phase2e-a] metadata rows validated: "
          f"{summary['metadata_rows_validated']}")
    print(f"[phase2e-a] prediction rows valid. : "
          f"{summary['prediction_rows_validated']}")
    print(f"[phase2e-a] would write registry   : "
          f"{summary['would_write_model_registry_rows']}")
    print(f"[phase2e-a] would write predictions: "
          f"{summary['would_write_prediction_output_rows']}")
    print(f"[phase2e-a] commit_enabled         : {summary['commit_enabled']}")
    print(f"[phase2e-a] database_touched       : {summary['database_touched']}")
    if summary.get("write_error"):
        print(f"[phase2e-a] note                   : {summary['write_error']}")
    print(f"[phase2e-a] summary JSON           : "
          f"{summary.get('summary_path', args.summary_output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
