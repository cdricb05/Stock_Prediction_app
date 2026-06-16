"""Tests for the Phase 2D artifact + prediction-persistence design.

Pure-logic only: no database, no network, no DB writes, and no import of
api_server or Paper Trader. sklearn / xgboost / pytest are NOT required.

Runs two ways:
  * under pytest:   pytest tests/test_phase2_persistence.py
  * without pytest: python tests/test_phase2_persistence.py
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

from model import train as T  # noqa: E402
from model import calibrate as C  # noqa: E402
from model import persist as P  # noqa: E402
from model import registry as REG  # noqa: E402


def _business_dates(start: dt.date, n: int):
    out, d = [], start
    for _ in range(n):
        while d.weekday() > 4:
            d += dt.timedelta(days=1)
        out.append(d)
        d += dt.timedelta(days=1)
    return out


def _make_preds(n_folds=4, per_fold=60, seed=0, signal=0.04):
    """A synthetic Phase 2B-shaped OOS prediction frame (T.OOS_COLUMNS)."""
    rng = np.random.default_rng(seed)
    rows = []
    base = dt.date(2023, 1, 2)
    block = _business_dates(base, n_folds * 10)
    for f in range(n_folds):
        for d in block[f * 10:(f + 1) * 10]:
            for j in range(per_fold // 10):
                score = float(rng.normal(0, 1))
                realized_excess = signal * score + float(rng.normal(0, 0.02))
                rows.append({
                    "ticker": f"N{j}", "as_of_date": d,
                    "target_date": d + dt.timedelta(days=7), "fold_id": f,
                    "model_name": "ridge", "score": score,
                    "predicted_excess_return_5d": score * signal,
                    "realized_excess_return_5d_vs_spy": realized_excess,
                    "realized_return_5d": realized_excess, "spy_return_5d": 0.0,
                    "outperform_spy_flag": bool(realized_excess > 0),
                    "positive_return_flag": bool(realized_excess > 0),
                })
    return pd.DataFrame(rows)[T.OOS_COLUMNS]


def _sample_outputs(seed=0):
    calibrated = C.build_calibrated_frame(_make_preds(seed=seed))
    meta = REG.build_metadata(
        model_name="ridge", feature_cols=["a_z", "b_z", "c_z"],
        training_source="SYNTHETIC (test)", training_start_date=dt.date(2023, 1, 2),
        training_end_date=dt.date(2023, 3, 1), horizon_days=5,
        calibration_method="isotonic", interval_method="split-conformal",
        decision_gate_summary={"gate3": {"evaluable": True, "passed": False}},
        metrics_summary={"brier": 0.25, "interval_coverage": 0.81},
        is_synthetic=True)
    outputs = P.build_prediction_outputs(calibrated, meta, "SYNTHETIC (test)")
    return calibrated, meta, outputs


# --------------------------------------------------------------------------- #
# 1. Metadata JSON round-trips correctly (including NaN sanitization)
# --------------------------------------------------------------------------- #
def test_metadata_json_round_trips():
    meta = REG.build_metadata(
        model_name="ridge", feature_cols=["b_z", "a_z"],
        training_source="SYNTHETIC (test)", training_start_date=dt.date(2023, 1, 2),
        training_end_date=dt.date(2023, 6, 1), horizon_days=5,
        calibration_method="isotonic", interval_method="split-conformal",
        decision_gate_summary={"gate3": {"passed": False}},
        metrics_summary={"brier": float("nan"), "coverage": 0.8})
    s = meta.to_json()
    # NaN was sanitized to null -> strict JSON parses without allow_nan.
    parsed = json.loads(s)
    assert parsed["metrics_summary"]["brier"] is None
    back = REG.ModelVersionMetadata.from_json(s)
    assert back == meta
    # all required fields are present
    for fld in REG.METADATA_FIELDS:
        assert fld in parsed, f"missing metadata field: {fld}"


def test_feature_set_version_is_stable_and_order_independent():
    v1 = REG.feature_set_version(["a_z", "b_z", "c_z"])
    v2 = REG.feature_set_version(["c_z", "a_z", "b_z"])  # order should not matter
    assert v1 == v2
    assert REG.feature_set_version(["a_z", "b_z"]) != v1  # different set differs


# --------------------------------------------------------------------------- #
# 2. Prediction output has the required columns (and maps to the schema)
# --------------------------------------------------------------------------- #
def test_prediction_output_has_required_columns():
    _, _, outputs = _sample_outputs(seed=1)
    assert list(outputs.columns) == P.PREDICTION_OUTPUT_COLUMNS
    assert len(outputs) > 0
    # one row per (ticker, as_of_date) prediction, no dupes introduced
    assert not outputs.duplicated(subset=["ticker", "as_of_date"]).any()


def test_build_prediction_outputs_handles_empty():
    meta = REG.build_metadata(
        model_name="ridge", feature_cols=[], training_source="SYNTHETIC (test)",
        training_start_date=None, training_end_date=None, horizon_days=5,
        calibration_method="isotonic", interval_method="split-conformal",
        decision_gate_summary={}, metrics_summary={})
    empty = pd.DataFrame(columns=C.CALIBRATED_COLUMNS)
    out = P.build_prediction_outputs(empty, meta, "SYNTHETIC (test)")
    assert list(out.columns) == P.PREDICTION_OUTPUT_COLUMNS
    assert out.empty


# --------------------------------------------------------------------------- #
# 3. Ranks are stable and correct (best score -> rank 1, deterministic ties)
# --------------------------------------------------------------------------- #
def test_ranks_are_stable_and_correct():
    df = pd.DataFrame({
        "ticker": ["AAA", "BBB", "CCC", "DDD"],
        "as_of_date": [dt.date(2023, 1, 2)] * 2 + [dt.date(2023, 1, 3)] * 2,
        "score": [0.1, 0.9, 0.5, 0.5],
    })
    r1 = P.rank_within_date(df)
    r2 = P.rank_within_date(df)
    assert list(r1) == list(r2)  # deterministic
    # date 1: BBB (0.9) rank1, AAA (0.1) rank2
    assert r1.iloc[1] == 1 and r1.iloc[0] == 2
    # date 2: tie 0.5/0.5 broken by ticker asc -> CCC rank1, DDD rank2
    assert r1.iloc[2] == 1 and r1.iloc[3] == 2
    # within each date ranks are exactly 1..n
    for _, idx in df.groupby("as_of_date").groups.items():
        assert sorted(r1.loc[idx].tolist()) == list(range(1, len(idx) + 1))


def test_non_finite_score_ranks_last():
    df = pd.DataFrame({
        "ticker": ["AAA", "BBB", "CCC"],
        "as_of_date": [dt.date(2023, 1, 2)] * 3,
        "score": [float("nan"), 0.2, 0.8],
    })
    r = P.rank_within_date(df)
    assert r.iloc[2] == 1 and r.iloc[1] == 2 and r.iloc[0] == 3  # NaN last


# --------------------------------------------------------------------------- #
# 4. Recommendation mapping is deterministic and uses all three inputs
# --------------------------------------------------------------------------- #
def test_recommendation_mapping_is_deterministic():
    assert P.map_recommendation(0.70, 0.02, "low") == "Strong Buy"
    assert P.map_recommendation(0.70, 0.02, "low") == \
        P.map_recommendation(0.70, 0.02, "low")
    assert P.map_recommendation(0.57, 0.01, "low") == "Buy"
    assert P.map_recommendation(0.50, 0.0, "low") == "Hold"
    assert P.map_recommendation(0.43, -0.01, "low") == "Sell"
    assert P.map_recommendation(0.30, -0.02, "low") == "Strong Sell"
    # every emitted label is in the published vocabulary
    for p in (0.1, 0.3, 0.5, 0.7, 0.9):
        assert P.map_recommendation(p, (p - 0.5), "medium") in P.RECOMMENDATIONS


def test_high_risk_downgrades_conviction_one_notch():
    # same prob/excess, only risk differs: high risk softens toward Hold
    assert P.map_recommendation(0.70, 0.02, "low") == "Strong Buy"
    assert P.map_recommendation(0.70, 0.02, "high") == "Buy"
    assert P.map_recommendation(0.30, -0.02, "low") == "Strong Sell"
    assert P.map_recommendation(0.30, -0.02, "high") == "Sell"
    # high risk never flips a bullish call to a bearish one (or vice versa)
    assert P.map_recommendation(0.56, 0.01, "high") in ("Buy", "Hold")


def test_recommendation_monotone_in_probability():
    # higher prob (positive edge) never yields a weaker conviction level
    levels = [P._conviction_level(p, 0.02, "low", P.DEFAULT_THRESHOLDS)
              for p in (0.30, 0.45, 0.55, 0.62, 0.80)]
    assert levels == sorted(levels)


# --------------------------------------------------------------------------- #
# 5. Synthetic/sample output is clearly marked non-production
# --------------------------------------------------------------------------- #
def test_synthetic_output_marked_non_production():
    _, meta, outputs = _sample_outputs(seed=2)
    report = P.build_report_markdown(meta, outputs, {"csv_path": "x.csv"},
                                     P.DEFAULT_THRESHOLDS)
    assert "not production edge" in report.lower()
    assert meta.is_synthetic is True
    assert "SYNTHETIC" in meta.training_source
    for section in P.REQUIRED_REPORT_SECTIONS:
        assert section in report, f"missing section: {section}"
    # the report explicitly states the API is unchanged and no DB writes occur
    assert "api_server.py" in report
    assert "no database writes" in report.lower()


# --------------------------------------------------------------------------- #
# 6. No database writes occur (static guarantee on the source)
# --------------------------------------------------------------------------- #
def test_no_database_writes():
    for mod in (P, REG):
        src = open(mod.__file__, "r", encoding="utf-8").read().lower()
        for token in ("to_sql", "insert into", "executemany", "cur.execute(",
                      "session.add", "commit()", "copy_from"):
            assert token not in src, f"{mod.__name__} contains DB-write token {token!r}"


def test_run_sample_flow_writes_only_local_files(tmp_paths=None):
    import argparse
    out_csv = os.path.join(_REPO_ROOT, "research", "output",
                           "_phase2d_test_outputs.csv")
    out_json = os.path.join(_REPO_ROOT, "research", "output",
                            "_phase2d_test_meta.json")
    out_md = os.path.join(_REPO_ROOT, "docs", "_phase2d_test_report.md")
    args = argparse.Namespace(
        db_url=None, tickers=None, max_tickers=8, start_date=None, end_date=None,
        horizon_days=T.DEFAULT_HORIZON, min_history=T.DEFAULT_MIN_HISTORY,
        n_folds=T.DEFAULT_N_FOLDS, embargo=T.DEFAULT_EMBARGO,
        min_train_dates=T.DEFAULT_MIN_TRAIN_DATES, alpha=T.DEFAULT_ALPHA,
        coverage=0.8, output=out_csv, metadata_output=out_json,
        report_output=out_md)
    try:
        res = P.run_sample_flow(args)
        assert os.path.exists(out_csv) and os.path.exists(out_json)
        assert os.path.exists(out_md)
        # metadata file is valid strict JSON and round-trips
        loaded = REG.ModelVersionMetadata.read_json(out_json)
        assert loaded == res["meta"]
        # the CSV has exactly the canonical schema
        df = pd.read_csv(out_csv)
        assert list(df.columns) == P.PREDICTION_OUTPUT_COLUMNS
    finally:
        for p in (out_csv, out_json, out_md):
            if os.path.exists(p):
                os.remove(p)


# --------------------------------------------------------------------------- #
# 7. Guardrails: no Paper Trader / sklearn / xgboost; api_server untouched
# --------------------------------------------------------------------------- #
def test_no_paper_trader_import():
    assert "api_server" not in sys.modules
    offenders = [m for m in sys.modules
                 if m == "paper_trader" or m.startswith("paper_trader.")]
    assert not offenders, f"unexpected Paper Trader import: {offenders}"


def test_no_sklearn_or_xgboost_dependency():
    import importlib
    for mod in ("model.persist", "model.registry"):
        m = importlib.import_module(mod)
        src = open(m.__file__, "r", encoding="utf-8").read().lower()
        assert "import sklearn" not in src and "from sklearn" not in src, mod
        assert "import xgboost" not in src and "from xgboost" not in src, mod
        assert "import pickle" not in src and "from pickle" not in src, mod


def test_model_sources_contain_no_paper_trader_or_api_server_refs():
    model_dir = os.path.join(_REPO_ROOT, "model")
    for fn in os.listdir(model_dir):
        if not fn.endswith(".py"):
            continue
        with open(os.path.join(model_dir, fn), "r", encoding="utf-8") as f:
            src = f.read().lower()
        assert "paper_trader" not in src, f"{fn} references paper_trader"
        assert "import api_server" not in src, f"{fn} imports api_server"


def test_api_server_predict_contract_unchanged():
    path = os.path.join(_REPO_ROOT, "api_server.py")
    if not os.path.exists(path):
        return  # api_server lives only on the VM; skip where absent
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    assert '@app.post("/predict_all_models/"' in src
    for key in ('"recommendation"', '"confidence"', '"agreement"',
                '"ensemble_day5"', '"predictions"', '"zscore"'):
        assert key in src, f"response key {key} missing from api_server.py"
    assert "model.persist" not in src and "model.registry" not in src
    assert "phase2d" not in src.lower()


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
