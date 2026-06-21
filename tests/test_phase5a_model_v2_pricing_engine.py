"""Phase 5-A — tests for the model_v2 preview pricing engine.

Verifies the deployable scoring package: imports cleanly, honours the prediction
contract, keeps safety flags correct, degrades (never crashes) on missing data,
requires no network/API, and that the runner writes both output JSON files.

Pure/offline: no database, no network. Runs under pytest and standalone.
"""
from __future__ import annotations

import json
import os
import sys
import datetime as dt

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pytest  # noqa: E402

from research.model_v2_pricing_engine import (  # noqa: E402
    ALLOWED_PREDICTIONS,
    ALLOWED_REGIMES,
    MODEL_NAME,
    MODEL_VERSION,
    score_market_v2,
    score_ticker_v2,
)
from research.model_v2_pricing_engine import features as F  # noqa: E402
from research.model_v2_pricing_engine import scorer as S  # noqa: E402

REQUIRED_CONTRACT_KEYS = [
    "model_version", "model_name", "target_ticker", "as_of_date", "horizon",
    "prediction", "expected_return", "confidence", "risk_regime", "signal_score",
    "calibration_status", "calibration_limitations", "features_used",
    "missing_features", "data_as_of", "input_data_source",
    "preview_only", "orders_enabled", "automation_enabled",
    "broker_execution_enabled", "production_replacement",
]


# --------------------------------------------------------------------------- #
# Import + basic call
# --------------------------------------------------------------------------- #
def test_package_imports():
    assert MODEL_NAME == "model_v2_preview_market_direction"
    assert isinstance(MODEL_VERSION, str) and MODEL_VERSION


def test_score_market_returns_dict():
    out = score_market_v2("SPY")
    assert isinstance(out, dict)


def test_score_ticker_returns_dict():
    out = score_ticker_v2("SPY")
    assert isinstance(out, dict)


# --------------------------------------------------------------------------- #
# Contract
# --------------------------------------------------------------------------- #
def test_required_contract_keys_present():
    out = score_market_v2("SPY")
    for key in REQUIRED_CONTRACT_KEYS:
        assert key in out, f"missing contract key: {key}"


def test_prediction_in_allowed_values():
    out = score_market_v2("SPY")
    assert out["prediction"] in ALLOWED_PREDICTIONS


def test_risk_regime_in_allowed_values():
    out = score_market_v2("SPY")
    assert out["risk_regime"] in ALLOWED_REGIMES


def test_confidence_between_0_and_1():
    out = score_market_v2("SPY")
    conf = out["confidence"]
    assert isinstance(conf, (int, float))
    assert 0.0 <= conf <= 1.0


def test_confidence_capped_when_calibration_limited():
    out = score_market_v2("SPY")
    if out["calibration_status"] == "limited":
        assert out["confidence"] <= S.CONFIDENCE_CAP


def test_expected_return_numeric_or_null():
    out = score_market_v2("SPY")
    er = out["expected_return"]
    assert er is None or isinstance(er, (int, float))


def test_horizon_and_identity_fields():
    out = score_market_v2("SPY")
    assert out["model_name"] == MODEL_NAME
    assert out["model_version"] == MODEL_VERSION
    assert out["target_ticker"] == "SPY"
    assert out["horizon"] == "21d"


def test_calibration_status_limited_with_reasons():
    out = score_market_v2("SPY")
    assert out["calibration_status"] == "limited"
    assert isinstance(out["calibration_limitations"], list)
    assert len(out["calibration_limitations"]) >= 1


def test_features_used_and_missing_are_lists():
    out = score_market_v2("SPY")
    assert isinstance(out["features_used"], list)
    assert isinstance(out["missing_features"], list)
    # Every named feature is accounted for as either used or missing.
    assert set(out["features_used"]) | set(out["missing_features"]) == set(F.ALL_FEATURES)
    assert not (set(out["features_used"]) & set(out["missing_features"]))


# --------------------------------------------------------------------------- #
# Safety flags
# --------------------------------------------------------------------------- #
def test_safety_flags_correct():
    out = score_market_v2("SPY")
    assert out["preview_only"] is True
    assert out["orders_enabled"] is False
    assert out["automation_enabled"] is False
    assert out["broker_execution_enabled"] is False
    assert out["production_replacement"] is False


def test_safety_flags_correct_on_insufficient_data():
    # Unknown ticker -> INSUFFICIENT_DATA, but safety flags must still hold.
    out = score_ticker_v2("__NOT_A_REAL_TICKER__")
    assert out["prediction"] == "INSUFFICIENT_DATA"
    assert out["preview_only"] is True
    assert out["orders_enabled"] is False
    assert out["automation_enabled"] is False
    assert out["broker_execution_enabled"] is False
    assert out["production_replacement"] is False
    assert out["calibration_status"] == "limited"
    assert 0.0 <= out["confidence"] <= 1.0


# --------------------------------------------------------------------------- #
# Graceful degradation: empty / invalid input never raises
# --------------------------------------------------------------------------- #
def test_empty_ticker_degrades_cleanly():
    out = score_ticker_v2("")
    assert out["prediction"] == "INSUFFICIENT_DATA"
    assert out["confidence"] <= S.CONFIDENCE_CAP


def test_short_series_degrades_without_crash():
    # Direct feature path on a too-short series: all-or-most features None,
    # composite signal None -> INSUFFICIENT_DATA classification.
    feats, used, missing = F.compute_feature_vector([100.0, 101.0, 102.0])
    assert isinstance(feats, dict)
    signal, _c, _cov = S._composite_signal(feats)
    assert S._classify(signal) in ALLOWED_PREDICTIONS


# --------------------------------------------------------------------------- #
# No network / API dependency
# --------------------------------------------------------------------------- #
def test_no_network_or_paid_api_imports():
    import research.model_v2_pricing_engine.scorer as sc
    import research.model_v2_pricing_engine.features as ft
    import inspect
    # Scan only the executable import lines so prose disclaimers (e.g. the
    # module docstring's "No AlphaVantage") do not false-trip the guard.
    for mod in (sc, ft):
        import_lines = [
            ln.strip() for ln in inspect.getsource(mod).splitlines()
            if ln.strip().startswith(("import ", "from "))
        ]
        joined = " ".join(import_lines).lower()
        for forbidden in ("requests", "urllib", "http.client", "httpx",
                          "yfinance", "alpha_vantage", "alphavantage", "socket"):
            assert forbidden not in joined, (
                f"{mod.__name__} imports network/paid-API token: {forbidden}")


# --------------------------------------------------------------------------- #
# Output JSON validity (uses already-written sample if present, else computes)
# --------------------------------------------------------------------------- #
def test_payload_is_json_serializable():
    out = score_market_v2("SPY")
    text = json.dumps(out, allow_nan=False)  # must not raise (no NaN/inf)
    again = json.loads(text)
    assert again["model_name"] == MODEL_NAME


# --------------------------------------------------------------------------- #
# Runner writes both output files and the sample is valid JSON
# --------------------------------------------------------------------------- #
def test_runner_writes_both_outputs(tmp_path, monkeypatch):
    import importlib
    runner = importlib.import_module(
        "research.run_phase5a_build_model_v2_pricing_engine")
    rc = runner.main()
    assert rc == 0
    assert runner._ENGINE_OUT.is_file(), "engine report JSON not written"
    assert runner._SAMPLE_OUT.is_file(), "sample SPY prediction JSON not written"
    with open(runner._SAMPLE_OUT, "r", encoding="utf-8") as fh:
        sample = json.load(fh)
    assert sample["target_ticker"] == "SPY"
    assert sample["prediction"] in ALLOWED_PREDICTIONS
    assert 0.0 <= sample["confidence"] <= 1.0
    with open(runner._ENGINE_OUT, "r", encoding="utf-8") as fh:
        engine = json.load(fh)
    assert engine["phase"] == "5-A"
    assert engine["network_used"] is False


# --------------------------------------------------------------------------- #
# No Paper Trader / GCP / deploy files are touched by this package
# --------------------------------------------------------------------------- #
def test_package_does_not_reference_paper_trader_or_gcp():
    import research.model_v2_pricing_engine.scorer as sc
    import research.model_v2_pricing_engine.features as ft
    import inspect
    # Note: the strings "broker"/"order" appear only inside the *disabled*
    # safety-flag names (broker_execution_enabled, orders_enabled), so they are
    # intentionally not scanned here — we check for actual integration calls.
    for mod in (sc, ft):
        src = inspect.getsource(mod).lower()
        for forbidden in ("paper_trader", "gcloud", "compute_v1",
                          "create_order(", "place_order(", "execute_order("):
            assert forbidden not in src, f"{mod.__name__} references forbidden token: {forbidden}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
