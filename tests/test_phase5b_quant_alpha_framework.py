"""Phase 5-B — tests for the quant alpha framework specification.

Verifies that the canonical ``alpha_framework.json`` is valid and complete, that
the runner derives the required artifacts, and that the safety contract
(preview-only, no orders, no broker execution, no automation, no network, no
binary artifacts, no Paper Trader / GCP changes) holds.

Pure/offline: no database, no network. Runs under pytest and standalone.
"""
from __future__ import annotations

import csv
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pytest  # noqa: E402

_FRAMEWORK_PATH = os.path.join(
    _REPO_ROOT, "research", "model_v2_pricing_engine", "alpha_framework.json")
_OUT_DIR = os.path.join(_REPO_ROOT, "research", "output")

REQUIRED_SECTIONS = [
    "market_regime_layer", "cross_sectional_alpha_layer", "labels",
    "model_families", "scoring_design", "validation_methodology",
    "acceptance_gates", "implementation_contract", "future_gcp_endpoints",
]


def _load_framework() -> dict:
    with open(_FRAMEWORK_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _read_csv(path):
    with open(path, "r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- #
# Framework file: exists, valid JSON, required sections
# --------------------------------------------------------------------------- #
def test_framework_exists_and_is_valid_json():
    assert os.path.isfile(_FRAMEWORK_PATH), "alpha_framework.json missing"
    fw = _load_framework()
    assert isinstance(fw, dict) and fw


def test_required_top_level_sections_exist():
    fw = _load_framework()
    for section in REQUIRED_SECTIONS:
        assert section in fw, f"missing required section: {section}"


def test_spy_is_regime_proxy_not_trading_universe():
    fw = _load_framework()
    assert "regime" in fw["spy_role"].lower()
    assert "s&p 500" in fw["trading_universe"].lower()


# --------------------------------------------------------------------------- #
# Feature catalog content
# --------------------------------------------------------------------------- #
def test_feature_families_present():
    fw = _load_framework()
    families = {f["family"] for f in fw["cross_sectional_alpha_layer"]["feature_families"]}
    # momentum, relative strength, risk, liquidity, sentiment/news placeholders
    assert "trend_momentum" in families
    assert "relative_strength" in families
    assert "risk_volatility" in families
    assert "liquidity" in families
    assert "event_fundamental_sentiment_placeholders" in families
    # market regime is its own layer with features
    assert fw["market_regime_layer"]["features"]


def test_feature_catalog_csv_covers_required_families(tmp_path):
    # Build the catalog via the runner, then assert coverage.
    import importlib
    runner = importlib.import_module("research.run_phase5b_quant_alpha_framework")
    runner.main()
    rows = _read_csv(runner._FEATURE_CATALOG_OUT)
    layers = {r["layer"] for r in rows}
    families = {r["family"] for r in rows}
    assert "market_regime" in layers
    assert "cross_sectional" in layers
    assert "trend_momentum" in families
    assert "relative_strength" in families
    assert "risk_volatility" in families
    assert "liquidity" in families
    assert "event_fundamental_sentiment_placeholders" in families
    # sentiment/news placeholder features are present and marked future
    names = {r["feature_name"] for r in rows}
    assert "news_sentiment" in names
    assert "earnings_surprise" in names


# --------------------------------------------------------------------------- #
# Labels
# --------------------------------------------------------------------------- #
def test_label_catalog_includes_primary_excess_return():
    fw = _load_framework()
    names = {lab["name"] for lab in fw["labels"]["catalog"]}
    assert "forward_excess_return_20d_vs_spy" in names
    assert fw["labels"]["recommended_primary_label"] == "forward_excess_return_20d_vs_spy"


# --------------------------------------------------------------------------- #
# Validation gate matrix
# --------------------------------------------------------------------------- #
def test_validation_gate_matrix_covers_required_checks():
    fw = _load_framework()
    gates_text = json.dumps(fw["acceptance_gates"]).lower()
    methods_text = json.dumps(fw["validation_methodology"]).lower()
    blob = gates_text + " " + methods_text
    for token in ("ic", "decile spread", "turnover", "cost", "drawdown",
                  "leakage", "regime"):
        assert token in blob, f"validation/gates missing concept: {token}"


# --------------------------------------------------------------------------- #
# Action values
# --------------------------------------------------------------------------- #
def test_action_values_complete():
    fw = _load_framework()
    actions = set(fw["scoring_design"]["action_values"])
    assert {"BUY", "SELL", "HOLD", "AVOID", "INSUFFICIENT_DATA"} <= actions
    # action rules must explain each
    rules = fw["scoring_design"]["action_rules"]
    for a in ("BUY", "SELL", "HOLD", "AVOID", "INSUFFICIENT_DATA"):
        assert a in rules and rules[a]


# --------------------------------------------------------------------------- #
# Phase 5-C implementation contract
# --------------------------------------------------------------------------- #
def test_implementation_contract_defines_required_functions():
    fw = _load_framework()
    names = {f["name"] for f in fw["implementation_contract"]["functions"]}
    assert {"score_ticker_v2", "rank_universe_v2", "generate_trade_candidates_v2"} <= names
    # each has a signature and a response schema
    for f in fw["implementation_contract"]["functions"]:
        assert f.get("signature")
        assert isinstance(f.get("response_schema"), dict) and f["response_schema"]


# --------------------------------------------------------------------------- #
# Safety contract stated explicitly in the framework
# --------------------------------------------------------------------------- #
def test_framework_states_preview_only_and_no_execution():
    fw = _load_framework()
    assert fw["preview_only"] is True
    assert fw["orders_enabled"] is False
    assert fw["broker_execution_enabled"] is False
    assert fw["automation_enabled"] is False
    assert fw["production_replacement"] is False


def test_scoring_design_caps_confidence_when_calibration_limited():
    fw = _load_framework()
    sd = fw["scoring_design"]
    assert sd["calibration_status"] == "limited"
    assert "cap" in sd["confidence_capping_rule"].lower()


# --------------------------------------------------------------------------- #
# No network / paid-API dependency in the runner or framework
# --------------------------------------------------------------------------- #
def test_no_network_or_paid_api_dependency():
    import research.run_phase5b_quant_alpha_framework as runner
    import inspect
    import_lines = [
        ln.strip() for ln in inspect.getsource(runner).splitlines()
        if ln.strip().startswith(("import ", "from "))
    ]
    joined = " ".join(import_lines).lower()
    for forbidden in ("requests", "urllib", "http.client", "httpx",
                      "yfinance", "alpha_vantage", "alphavantage", "socket"):
        assert forbidden not in joined, f"runner imports network/paid-API token: {forbidden}"
    fw = _load_framework()
    assert fw["no_network_required"] is True
    assert fw["no_paid_apis_required"] is True


# --------------------------------------------------------------------------- #
# Runner writes all required artifacts
# --------------------------------------------------------------------------- #
def test_runner_writes_all_artifacts():
    import importlib
    runner = importlib.import_module("research.run_phase5b_quant_alpha_framework")
    rc = runner.main()
    assert rc == 0
    for out in (runner._REPORT_OUT, runner._FEATURE_CATALOG_OUT,
                runner._LABEL_CATALOG_OUT, runner._GATE_MATRIX_OUT, runner._PLAN_OUT):
        assert out.is_file(), f"runner did not write {out}"
    # report JSON valid + flags correct
    with open(runner._REPORT_OUT, "r", encoding="utf-8") as fh:
        report = json.load(fh)
    assert report["phase"] == "5-B"
    # Root-level safety flags must be exposed directly on the report (external
    # validation reads these without descending into ``framework``).
    assert report["preview_only"] is True
    assert report["orders_enabled"] is False
    assert report["automation_enabled"] is False
    assert report["broker_execution_enabled"] is False
    assert report["production_replacement"] is False
    assert report["network_used"] is False
    assert report["deployed"] is False
    assert report["binary_artifacts_created"] is False
    # plan JSON defines the three functions
    with open(runner._PLAN_OUT, "r", encoding="utf-8") as fh:
        plan = json.load(fh)
    fn_names = {f["name"] for f in plan["functions_to_build"]}
    assert {"score_ticker_v2", "rank_universe_v2", "generate_trade_candidates_v2"} <= fn_names


def test_gate_matrix_csv_has_safety_gates():
    import importlib
    runner = importlib.import_module("research.run_phase5b_quant_alpha_framework")
    runner.main()
    rows = _read_csv(runner._GATE_MATRIX_OUT)
    names = {r["gate_name"] for r in rows}
    assert "no_orders_gate" in names
    assert "no_broker_execution_gate" in names
    assert "no_automation_gate" in names
    assert "preview_only_gate" in names


# --------------------------------------------------------------------------- #
# No binary model artifacts were created anywhere in the repo by this phase
# --------------------------------------------------------------------------- #
def test_no_binary_model_artifacts_present():
    bad_ext = (".pkl", ".joblib", ".onnx", ".pt", ".h5", ".keras")
    offenders = []
    for root, _dirs, files in os.walk(_REPO_ROOT):
        if ".git" in root:
            continue
        for fn in files:
            if fn.lower().endswith(bad_ext):
                offenders.append(os.path.join(root, fn))
    assert not offenders, f"binary model artifacts found: {offenders}"


# --------------------------------------------------------------------------- #
# Framework does not reference Paper Trader / GCP deploy integration
# --------------------------------------------------------------------------- #
def test_framework_does_not_reference_paper_trader_or_deploy_calls():
    fw_text = json.dumps(_load_framework()).lower()
    # Future GCP endpoints are described (allowed), but no actual deploy/order calls.
    for forbidden in ("paper_trader", "gcloud ", "create_order(", "place_order(",
                      "execute_order("):
        assert forbidden not in fw_text, f"framework references forbidden token: {forbidden}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
