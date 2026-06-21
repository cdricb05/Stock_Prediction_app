"""Phase 5-A runner — build / validate the model_v2 pricing engine and emit a
deployable SPY prediction.

Steps:
  1. Locate local SPY price data (offline; D: read-only or repo fallback).
  2. Validate the model_v2 package (manifest + feature schema consistency).
  3. Generate manifest / feature schema files if missing.
  4. Run score_market_v2("SPY").
  5. Write:
       research/output/phase5a_model_v2_pricing_engine.json
       research/output/phase5a_model_v2_sample_spy_prediction.json
  6. Print a concise summary.

No network, no paid APIs, no orders, no automation, no deploy. Does not write to
D:. Does not commit.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research.model_v2_pricing_engine import (  # noqa: E402
    MODEL_NAME,
    MODEL_VERSION,
    score_market_v2,
)
from research.model_v2_pricing_engine import features as F  # noqa: E402
from research.model_v2_pricing_engine import scorer as S  # noqa: E402

_PKG_DIR = _REPO_ROOT / "research" / "model_v2_pricing_engine"
_OUT_DIR = _REPO_ROOT / "research" / "output"
_ENGINE_OUT = _OUT_DIR / "phase5a_model_v2_pricing_engine.json"
_SAMPLE_OUT = _OUT_DIR / "phase5a_model_v2_sample_spy_prediction.json"
_MANIFEST = _PKG_DIR / "manifest.json"
_FEATURE_SCHEMA = _PKG_DIR / "feature_schema.json"


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, allow_nan=False)
        fh.write("\n")


def _ensure_feature_schema() -> dict:
    """Load feature_schema.json, regenerating it from the code if absent."""
    if not _FEATURE_SCHEMA.is_file():
        schema = {
            "feature_set_version": F.FEATURE_SET_VERSION,
            "anchor": "last available session (point-in-time; trailing window only)",
            "missing_value_policy": (
                "Any feature whose lookback window is unavailable degrades to "
                "null and is listed in missing_features; it never blocks scoring."),
            "features": F.feature_schema(),
        }
        _write_json(_FEATURE_SCHEMA, schema)
    with open(_FEATURE_SCHEMA, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _locate_spy() -> dict:
    dates, closes, volumes, source = S.load_price_series("SPY")
    return {
        "found": bool(closes),
        "source": source,
        "rows": len(closes),
        "first_date": dates[0].isoformat() if dates else None,
        "last_date": dates[-1].isoformat() if dates else None,
        "has_volume": volumes is not None,
    }


def main() -> int:
    # 1. Locate local SPY data.
    spy_data = _locate_spy()

    # 2 + 3. Validate / generate manifest and feature schema.
    schema = _ensure_feature_schema()
    manifest_present = _MANIFEST.is_file()
    manifest = {}
    if manifest_present:
        with open(_MANIFEST, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)

    schema_features = set(schema.get("features", {}).keys())
    code_features = set(F.ALL_FEATURES)
    schema_consistent = schema_features == code_features

    # 4. Score the market (SPY).
    prediction = score_market_v2("SPY")

    # 5. Write outputs.
    engine_report = {
        "phase": "5-A",
        "model_name": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "data_location": spy_data,
        "package_validation": {
            "manifest_present": manifest_present,
            "feature_schema_present": _FEATURE_SCHEMA.is_file(),
            "feature_schema_consistent_with_code": schema_consistent,
            "schema_only_features": sorted(schema_features - code_features),
            "code_only_features": sorted(code_features - schema_features),
            "manifest_model_version_matches": manifest.get("model_version") == MODEL_VERSION,
        },
        "spy_prediction": prediction,
        "calibration_status": prediction.get("calibration_status"),
        "calibration_limitations": prediction.get("calibration_limitations"),
        "safety": {
            "preview_only": prediction.get("preview_only"),
            "orders_enabled": prediction.get("orders_enabled"),
            "automation_enabled": prediction.get("automation_enabled"),
            "broker_execution_enabled": prediction.get("broker_execution_enabled"),
            "production_replacement": prediction.get("production_replacement"),
        },
        "network_used": False,
        "paid_apis_used": False,
        "deployed": False,
        "data_faked": False,
        "next_phase": "5-B: serve model_v2 from GCP via /predict_v2/{ticker} and /predict_market_v2 (flag-off, preview-only).",
    }
    _write_json(_ENGINE_OUT, engine_report)
    _write_json(_SAMPLE_OUT, prediction)

    # 6. Concise summary.
    print("=" * 60)
    print("Phase 5-A | model_v2 preview pricing engine")
    print("=" * 60)
    print(f"  target_ticker      : {prediction['target_ticker']}")
    print(f"  as_of_date         : {prediction['as_of_date']}")
    print(f"  prediction         : {prediction['prediction']}")
    print(f"  expected_return    : {prediction['expected_return']}")
    print(f"  confidence         : {prediction['confidence']}")
    print(f"  risk_regime        : {prediction['risk_regime']}")
    print(f"  signal_score       : {prediction['signal_score']}")
    print(f"  calibration_status : {prediction['calibration_status']}")
    print(f"  preview_only       : {prediction['preview_only']}")
    print(f"  orders_enabled     : {prediction['orders_enabled']}")
    print(f"  data_source        : {spy_data['source']}")
    print(f"  data_rows          : {spy_data['rows']} "
          f"({spy_data['first_date']} -> {spy_data['last_date']})")
    print(f"  schema_consistent  : {schema_consistent}")
    print(f"  next_phase         : 5-B (serve from GCP, flag-off, preview-only)")
    print("-" * 60)
    print(f"  wrote: {_ENGINE_OUT.relative_to(_REPO_ROOT)}")
    print(f"  wrote: {_SAMPLE_OUT.relative_to(_REPO_ROOT)}")
    if not spy_data["found"]:
        print("  NOTE: no local SPY data found -> prediction degraded to "
              "INSUFFICIENT_DATA (calibration_status=limited). Packaging OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
