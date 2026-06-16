"""Tests for the Phase 2E-B model-v2 serving adapter + gate-4 backtest.

Pure-logic / local-file only: no database connection, no network, no DB writes,
no import of api_server or Paper Trader. sklearn / xgboost / pytest are NOT
required.

Runs two ways:
  * under pytest:   pytest tests/test_phase2_serve.py
  * without pytest: python tests/test_phase2_serve.py
The __main__ block discovers every ``test_*`` function, runs it, prints
PASS/FAIL, and exits non-zero on any failure (the GCP venv has no pytest).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd  # noqa: E402

from model import serve_v2 as SV  # noqa: E402

_SAMPLE_CSV = os.path.join(_REPO_ROOT, "research", "output",
                           "phase2d_prediction_outputs_sample.csv")
_LABELS_CSV = os.path.join(_REPO_ROOT, "research", "output",
                           "phase2c_calibrated_predictions_sample.csv")

# Legacy keys that existing Paper Trader consumers expect on a /predict response.
_LEGACY_KEYS = ("ticker", "recommendation", "confidence", "agreement",
                "ensemble_day5", "predictions", "zscore")
# Additive model-v2 keys.
_V2_KEYS = ("model_version", "feature_set_version", "score",
            "predicted_excess_return_5d", "prob_outperform_spy", "lo_return_5d",
            "hi_return_5d", "interval_width", "risk_band", "rank_in_universe",
            "source", "model_v2")


def _preds() -> pd.DataFrame:
    return SV.load_prediction_outputs(_SAMPLE_CSV)


def _toy_two_dates() -> pd.DataFrame:
    cols = SV.STORE.PREDICTION_OUTPUT_COLUMNS
    base = {c: None for c in cols}
    r1 = {**base, "model_version": "mv", "ticker": "AAA",
          "as_of_date": "2023-01-02", "generated_at": "2026-06-16T00:00:00",
          "target_date": "2023-01-09", "feature_set_version": "fsv",
          "horizon_days": 5, "score": 0.01, "predicted_excess_return_5d": 0.01,
          "prob_outperform_spy": 0.60, "risk_band": "medium",
          "recommendation": "Buy", "rank_in_universe": 1, "source": "SYNTHETIC"}
    r2 = {**r1, "as_of_date": "2023-02-01", "target_date": "2023-02-08",
          "prob_outperform_spy": 0.72, "recommendation": "Strong Buy"}
    return pd.DataFrame([r1, r2])[cols]


# --------------------------------------------------------------------------- #
# 1. Feature flag defaults off
# --------------------------------------------------------------------------- #
def test_feature_flag_defaults_false():
    assert SV.model_v2_enabled(None) is False
    assert SV.model_v2_enabled("") is False
    assert SV.model_v2_enabled("0") is False
    assert SV.model_v2_enabled("false") is False
    assert SV.model_v2_enabled(False) is False
    # explicit truthy values turn it on
    assert SV.model_v2_enabled("1") is True
    assert SV.model_v2_enabled("true") is True
    assert SV.model_v2_enabled(True) is True
    assert SV.model_v2_enabled("on") is True


# --------------------------------------------------------------------------- #
# 2. Adapter reads local sample rows; latest selection is correct
# --------------------------------------------------------------------------- #
def test_adapter_reads_local_sample_rows():
    df = _preds()
    assert not df.empty
    assert "ticker" in df.columns and "as_of_date" in df.columns


def test_latest_ticker_prediction_is_selected():
    df = _toy_two_dates()
    row = SV.latest_row_for_ticker(df, "AAA")
    assert row is not None
    # latest as_of_date wins
    assert row["as_of_date"] == "2023-02-01"
    assert row["recommendation"] == "Strong Buy"
    assert SV.latest_row_for_ticker(df, "NOPE") is None


def test_multi_ticker_response_works():
    df = _preds()
    rows = SV.latest_rows_for_tickers(df)
    assert len(rows) >= 1
    # one latest row per distinct ticker
    assert len({r["ticker"] for r in rows}) == len(rows)
    resp = SV.rows_to_predict_all_response(rows)
    assert resp["model_v2"] is True
    assert resp["count"] == len(rows)
    assert isinstance(resp["results"], list) and resp["results"]
    assert resp["synthetic_sample"] is True


# --------------------------------------------------------------------------- #
# 3. Single-ticker response: legacy + v2 keys, confidence, recommendation
# --------------------------------------------------------------------------- #
def test_single_ticker_response_has_legacy_keys():
    df = _toy_two_dates()
    row = SV.latest_row_for_ticker(df, "AAA")
    resp = SV.row_to_predict_response(row)
    for k in _LEGACY_KEYS:
        assert k in resp, f"missing legacy key: {k}"


def test_single_ticker_response_has_v2_keys():
    df = _toy_two_dates()
    resp = SV.row_to_predict_response(SV.latest_row_for_ticker(df, "AAA"))
    for k in _V2_KEYS:
        assert k in resp, f"missing model-v2 key: {k}"
    assert resp["model_v2"] is True


def test_confidence_between_0_and_100():
    for p in (0.0, 0.5, 0.55, 0.72, 1.0):
        c = SV.confidence_from_prob(p)
        assert c is not None and 0.0 <= c <= 100.0
    # confidence = max(p, 1-p)*100
    assert SV.confidence_from_prob(0.72) == 72.0
    assert SV.confidence_from_prob(0.30) == 70.0
    assert SV.confidence_from_prob(None) is None
    # every sample row maps into range
    for row in SV.latest_rows_for_tickers(_preds()):
        resp = SV.row_to_predict_response(row)
        if resp["confidence"] is not None:
            assert 0.0 <= resp["confidence"] <= 100.0


def test_recommendation_preserved_from_row():
    df = _toy_two_dates()
    resp = SV.row_to_predict_response(SV.latest_row_for_ticker(df, "AAA"))
    assert resp["recommendation"] == "Strong Buy"


# --------------------------------------------------------------------------- #
# 4. Gate 4 backtest
# --------------------------------------------------------------------------- #
def test_gate4_runs_and_returns_required_fields():
    preds = _preds()
    labels = SV.load_realized_labels(_LABELS_CSV)
    g4 = SV.gate4_backtest(preds, labels)
    for fld in ("n_buy_rows", "n_buy_dates", "avg_buy_realized_excess_return",
                "avg_universe_realized_excess_return", "buy_hit_rate_vs_spy",
                "cost_bps", "net_of_cost_buy_excess_return", "passed",
                "evaluable", "synthetic_sample", "production_edge"):
        assert fld in g4, f"gate4 missing field: {fld}"
    assert g4["n_buy_rows"] >= 1
    assert g4["labels_joined"] is True
    assert g4["evaluable"] is True
    assert isinstance(g4["passed"], bool)


def test_gate4_marked_not_production_edge():
    g4 = SV.gate4_backtest(_preds(), SV.load_realized_labels(_LABELS_CSV))
    assert g4["production_edge"] is False
    assert g4["synthetic_sample"] is True
    assert "synthetic" in g4["note"].lower()


def test_gate4_handles_missing_labels():
    g4 = SV.gate4_backtest(_preds(), pd.DataFrame())
    assert g4["labels_joined"] is False
    assert g4["evaluable"] is False
    assert g4["passed"] is None


# --------------------------------------------------------------------------- #
# 5. CLI / sample outputs marked synthetic; no DB touched
# --------------------------------------------------------------------------- #
def test_cli_writes_samples_and_marks_synthetic():
    out_g4 = os.path.join(_REPO_ROOT, "research", "output",
                          "_phase2e_test_gate4.json")
    out_api = os.path.join(_REPO_ROOT, "research", "output",
                           "_phase2e_test_api.json")
    args = SV.build_arg_parser().parse_args([])
    args.gate4_output = out_g4
    args.api_sample_output = out_api
    try:
        out = SV.run(args)
        assert out["database_touched"] is False
        assert os.path.exists(out_g4) and os.path.exists(out_api)
        with open(out_api, "r", encoding="utf-8") as f:
            api = json.load(f)
        assert api["synthetic_sample"] is True
        assert api["production_edge"] is False
        assert api["single_ticker_response"] is not None
        assert api["multi_ticker_response"]["count"] >= 1
        with open(out_g4, "r", encoding="utf-8") as f:
            g4 = json.load(f)
        assert g4["production_edge"] is False
    finally:
        for p in (out_g4, out_api):
            if os.path.exists(p):
                os.remove(p)


# --------------------------------------------------------------------------- #
# 6. Guardrails: no DB / env / api_server / Paper Trader / sklearn / xgboost
# --------------------------------------------------------------------------- #
def test_no_database_connection_by_default():
    # If anything tried to connect via store's engine builder, fail loudly.
    orig = SV.STORE._build_engine
    SV.STORE._build_engine = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("serving adapter must not connect to a DB"))
    try:
        df = _preds()
        SV.row_to_predict_response(SV.latest_row_for_ticker(df, df["ticker"].iloc[0]))
        SV.gate4_backtest(df, SV.load_realized_labels(_LABELS_CSV))
        SV.build_api_response_sample(df)
    finally:
        SV.STORE._build_engine = orig


def test_no_env_read_at_import_time():
    src = open(SV.__file__, "r", encoding="utf-8").read()
    # The module documents the flag name but must not read the environment.
    assert "os.environ" not in src
    assert "getenv" not in src
    # no create_engine / sqlalchemy import anywhere in this module
    assert "create_engine" not in src
    assert "sqlalchemy" not in src.lower()


def test_no_api_server_or_paper_trader_import():
    assert "api_server" not in sys.modules
    offenders = [m for m in sys.modules
                 if m == "paper_trader" or m.startswith("paper_trader.")]
    assert not offenders, f"unexpected Paper Trader import: {offenders}"


def test_serve_source_has_no_forbidden_deps():
    src = open(SV.__file__, "r", encoding="utf-8").read().lower()
    for token in ("import sklearn", "from sklearn", "import xgboost",
                  "from xgboost", "import pickle", "from pickle",
                  "import api_server", "from api_server", "paper_trader"):
        assert token not in src, f"serve_v2.py contains forbidden token: {token!r}"


def test_api_server_references_v2_only_through_gated_path():
    # Phase 2E-C wires serve_v2 into api_server.py behind PREDICTOR_USE_MODEL_V2.
    # The adapter itself stays decoupled: it must never import api_server, and
    # api_server must reference the flag exactly once (read via os.getenv) and the
    # adapter only through the guarded import. (Pre-2E-C this asserted no refs.)
    path = os.path.join(_REPO_ROOT, "api_server.py")
    if not os.path.exists(path):
        return  # api_server lives only on the VM; skip where absent
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    assert src.count("from model import serve_v2") == 1
    assert src.count('os.getenv("PREDICTOR_USE_MODEL_V2")') == 1
    assert "model_v2_enabled(os.getenv(" in src


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
