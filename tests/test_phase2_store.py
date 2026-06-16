"""Tests for the Phase 2E-A storage layer + idempotent SQL design.

Pure-logic / local-file only: no database connection, no network, no DB writes,
and no import of api_server or Paper Trader. sklearn / xgboost / pytest are NOT
required.

Runs two ways:
  * under pytest:   pytest tests/test_phase2_store.py
  * without pytest: python tests/test_phase2_store.py
The __main__ block discovers every ``test_*`` function, runs it, prints
PASS/FAIL, and exits non-zero on any failure (the GCP venv has no pytest).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

# Make the repo root importable when run directly (sys.path[0] == tests/).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from model import registry as REG  # noqa: E402
from model import store as S  # noqa: E402

_SQL_PATH = os.path.join(_REPO_ROOT, "migrations",
                         "phase2e_prediction_outputs.sql")
_SAMPLE_CSV = os.path.join(_REPO_ROOT, "research", "output",
                           "phase2d_prediction_outputs_sample.csv")
_SAMPLE_META = os.path.join(_REPO_ROOT, "research", "output",
                            "phase2d_model_metadata_sample.json")


def _read_sql() -> str:
    """Read the migration with ``--`` comment lines stripped.

    Comments intentionally *describe* what the file avoids (e.g. "NO TRUNCATE"),
    so destructive-token / CREATE-TABLE checks must run against executable SQL
    only, not the documentation.
    """
    out = []
    with open(_SQL_PATH, "r", encoding="utf-8") as f:
        for line in f:
            code = line.split("--", 1)[0]
            out.append(code)
    return "\n".join(out)


def _sample_meta() -> REG.ModelVersionMetadata:
    return REG.build_metadata(
        model_name="ridge", feature_cols=["a_z", "b_z", "c_z"],
        training_source="SYNTHETIC (test)", training_start_date=dt.date(2023, 1, 2),
        training_end_date=dt.date(2023, 3, 1), horizon_days=5,
        calibration_method="isotonic", interval_method="split-conformal",
        decision_gate_summary={"gate3": {"evaluable": True, "passed": False}},
        metrics_summary={"brier": 0.25, "interval_coverage": 0.81},
        is_synthetic=True)


def _sample_predictions(n=6) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    rows = []
    for i in range(n):
        rows.append({
            "model_version": "phase2-ridge-x", "ticker": f"T{i}",
            "as_of_date": dt.date(2023, 1, 2), "generated_at": "2026-06-16T00:00:00",
            "target_date": dt.date(2023, 1, 9), "feature_set_version": "fsv-1",
            "horizon_days": 5, "score": float(rng.normal()),
            "predicted_excess_return_5d": 0.01, "prob_outperform_spy": 0.55,
            "lo_return_5d": -0.02, "hi_return_5d": 0.05, "interval_width": 0.07,
            "risk_band": "medium", "recommendation": "Buy",
            "rank_in_universe": i + 1, "source": "SYNTHETIC (test)",
        })
    return pd.DataFrame(rows)[S.PREDICTION_OUTPUT_COLUMNS]


# --------------------------------------------------------------------------- #
# 1. SQL migration is idempotent and non-destructive
# --------------------------------------------------------------------------- #
def test_sql_uses_create_table_if_not_exists():
    sql = _read_sql().lower()
    assert "create table if not exists" in sql
    # every CREATE TABLE must be guarded with IF NOT EXISTS
    n_create = sql.count("create table")
    n_guarded = sql.count("create table if not exists")
    assert n_create == n_guarded, "an unguarded CREATE TABLE exists"


def test_sql_has_no_destructive_statements():
    sql = _read_sql().lower()
    for token in ("drop table", "truncate", "delete from"):
        assert token not in sql, f"SQL contains destructive token: {token!r}"


def test_sql_defines_both_tables():
    sql = _read_sql().lower()
    assert "create table if not exists model_registry" in sql
    assert "create table if not exists prediction_outputs" in sql


def test_sql_has_natural_duplicate_protection_key():
    sql = _read_sql().lower()
    # primary key on the natural key (model_version, ticker, as_of_date)
    assert "primary key (model_version, ticker, as_of_date)" in sql


def test_sql_model_registry_has_phase2d_fields():
    sql = _read_sql().lower()
    for fld in ("model_version", "feature_set_version", "created_at",
                "training_source", "training_start_date", "training_end_date",
                "horizon_days", "model_name", "calibration_method",
                "interval_method", "decision_gate_summary", "metrics_summary",
                "is_synthetic"):
        assert fld in sql, f"model_registry missing field: {fld}"
    assert "jsonb" in sql  # the two summaries are JSONB


# --------------------------------------------------------------------------- #
# 2. Validation passes on the real Phase 2D sample artifacts
# --------------------------------------------------------------------------- #
def test_metadata_validation_passes_on_phase2d_sample():
    meta = S.load_phase2d_metadata(_SAMPLE_META)
    result = S.validate_metadata(meta)
    assert result.ok, result.errors
    assert result.n_rows == 1


def test_prediction_validation_passes_on_phase2d_sample():
    df = S.load_phase2d_predictions(_SAMPLE_CSV)
    result = S.validate_prediction_rows(df)
    assert result.ok, result.errors
    assert result.n_rows == len(df) > 0


def test_validation_catches_missing_fields_and_dupes():
    # missing required metadata field
    bad_meta_errs = S.validate_metadata_row({"model_version": "x"})
    assert any("horizon_days" in e or "feature_set_version" in e
               for e in bad_meta_errs)
    # duplicate natural key is rejected
    df = _sample_predictions(3)
    dup = pd.concat([df, df.iloc[[0]]], ignore_index=True)
    res = S.validate_prediction_rows(dup)
    assert not res.ok
    assert any("duplicate" in e for e in res.errors)
    # out-of-range probability is rejected
    df2 = _sample_predictions(2)
    df2.loc[0, "prob_outperform_spy"] = 1.5
    res2 = S.validate_prediction_rows(df2)
    assert not res2.ok and any("prob_outperform_spy" in e for e in res2.errors)


def test_records_conversion_is_json_safe():
    meta = _sample_meta()
    rec = S.metadata_to_record(meta)
    assert set(rec) == set(S.MODEL_REGISTRY_COLUMNS)
    # whole record is strictly JSON-serializable
    json.dumps(rec, allow_nan=False)
    recs = S.prediction_frame_to_records(_sample_predictions(4))
    assert len(recs) == 4
    assert all(set(r) == set(S.PREDICTION_OUTPUT_COLUMNS) for r in recs)
    json.dumps(recs, allow_nan=False)
    # dates were converted to ISO strings by jsonsafe
    assert isinstance(recs[0]["as_of_date"], str)


# --------------------------------------------------------------------------- #
# 3. Dry-run persists locally only; never touches a database
# --------------------------------------------------------------------------- #
def test_dry_run_does_not_touch_database():
    meta = _sample_meta()
    preds = _sample_predictions(5)
    tmp = os.path.join(_REPO_ROOT, "research", "output",
                       "_phase2e_test_summary.json")
    # Sabotage the engine builder: if the dry run tries to connect, fail loudly.
    orig = S._build_engine
    S._build_engine = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("dry run must not build a DB engine"))
    try:
        summary = S.persist(meta, preds, summary_path=tmp)  # defaults: no db, no commit
        assert summary["database_touched"] is False
        assert summary["commit_enabled"] is False
        assert summary["would_write_model_registry_rows"] == 1
        assert summary["would_write_prediction_output_rows"] == 5
        assert summary["migration_executed"] is False
        # the summary file exists and round-trips as strict JSON
        with open(tmp, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded["database_touched"] is False
        assert loaded["commit_enabled"] is False
    finally:
        S._build_engine = orig
        if os.path.exists(tmp):
            os.remove(tmp)


def test_commit_without_db_url_refuses_and_does_not_write():
    meta = _sample_meta()
    preds = _sample_predictions(3)
    orig = S._build_engine
    S._build_engine = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("must not build an engine without a db_url"))
    try:
        summary = S.persist(meta, preds, db_url=None, commit=True, summary_path=None)
        assert summary["database_touched"] is False
        assert summary["commit_enabled"] is False
        assert summary["write_error"]  # explains it refused
    finally:
        S._build_engine = orig


def test_cli_default_is_dry_run_summary():
    args = S.build_arg_parser().parse_args([])
    assert args.db_url is None
    assert args.commit is False
    out = os.path.join(_REPO_ROOT, "research", "output",
                       "_phase2e_cli_test_summary.json")
    args.summary_output = out
    orig = S._build_engine
    S._build_engine = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("CLI default must not connect to a DB"))
    try:
        summary = S.run(args)
        assert summary["database_touched"] is False
        assert summary["commit_enabled"] is False
        assert os.path.exists(out)
    finally:
        S._build_engine = orig
        if os.path.exists(out):
            os.remove(out)


# --------------------------------------------------------------------------- #
# 4. Guardrails: no api_server / Paper Trader / sklearn / xgboost / pickle
# --------------------------------------------------------------------------- #
def test_no_api_server_or_paper_trader_import():
    assert "api_server" not in sys.modules
    offenders = [m for m in sys.modules
                 if m == "paper_trader" or m.startswith("paper_trader.")]
    assert not offenders, f"unexpected Paper Trader import: {offenders}"


def test_store_source_has_no_forbidden_deps():
    src = open(S.__file__, "r", encoding="utf-8").read().lower()
    for token in ("import sklearn", "from sklearn", "import xgboost",
                  "from xgboost", "import pickle", "from pickle",
                  "import api_server", "paper_trader"):
        assert token not in src, f"store.py contains forbidden token: {token!r}"


def test_no_env_read_or_connect_at_import_time():
    # store.py must not read env vars or connect at import time.
    src = open(S.__file__, "r", encoding="utf-8").read()
    # os.environ / getenv usage is not allowed anywhere in this module
    assert "os.environ" not in src and "getenv" not in src
    # sqlalchemy / create_engine must only appear inside functions (lazy import),
    # never at module top level.
    for line in src.splitlines():
        if line and not line[0].isspace():  # top-level (unindented) statement
            assert "create_engine" not in line
            assert "import sqlalchemy" not in line


def test_api_server_unchanged_no_store_refs():
    path = os.path.join(_REPO_ROOT, "api_server.py")
    if not os.path.exists(path):
        return  # api_server lives only on the VM; skip where absent
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    assert "model.store" not in src
    assert "phase2e" not in src.lower()


# --------------------------------------------------------------------------- #
# Self-running harness (no pytest required)
# --------------------------------------------------------------------------- #
def _run_all():
    tests = sorted((n, f) for n, f in globals().items()
                   if n.startswith("test_") and callable(f))
    passed = failed = 0
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"FAIL {name}: {e}")
            failures.append((name, traceback.format_exc()))
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {len(tests)} total")
    if failures:
        print("\n--- failure details ---")
        for name, tb in failures:
            print(f"\n### {name}\n{tb}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
