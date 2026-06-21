"""Phase 5-B runner — validate the quant alpha framework and emit its artifacts.

This phase is a *specification*, not a model build. The canonical source of
truth is ``research/model_v2_pricing_engine/alpha_framework.json``. This runner:

  1. Loads (or, if absent, would fail loudly — it does not fabricate) the
     framework.
  2. Validates that every required section is present and internally consistent.
  3. Derives and writes:
       research/output/phase5b_quant_alpha_framework.json   (validated report)
       research/output/phase5b_feature_catalog.csv
       research/output/phase5b_label_catalog.csv
       research/output/phase5b_validation_gate_matrix.csv
       research/output/phase5b_phase5c_implementation_plan.json
  4. Prints a concise summary.

No network, no paid APIs, no AlphaVantage, no orders, no automation, no broker
execution, no deploy. Does not write to D:. Does not modify Paper Trader or GCP.
Does not create binary model artifacts. Does not commit.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_PKG_DIR = _REPO_ROOT / "research" / "model_v2_pricing_engine"
_OUT_DIR = _REPO_ROOT / "research" / "output"
_FRAMEWORK = _PKG_DIR / "alpha_framework.json"

_REPORT_OUT = _OUT_DIR / "phase5b_quant_alpha_framework.json"
_FEATURE_CATALOG_OUT = _OUT_DIR / "phase5b_feature_catalog.csv"
_LABEL_CATALOG_OUT = _OUT_DIR / "phase5b_label_catalog.csv"
_GATE_MATRIX_OUT = _OUT_DIR / "phase5b_validation_gate_matrix.csv"
_PLAN_OUT = _OUT_DIR / "phase5b_phase5c_implementation_plan.json"

# Sections every valid framework must define.
REQUIRED_SECTIONS = [
    "market_regime_layer",
    "cross_sectional_alpha_layer",
    "labels",
    "model_families",
    "scoring_design",
    "validation_methodology",
    "acceptance_gates",
    "implementation_contract",
    "future_gcp_endpoints",
]

REQUIRED_ACTIONS = {"BUY", "SELL", "HOLD", "AVOID", "INSUFFICIENT_DATA"}
REQUIRED_FUNCTIONS = {"score_ticker_v2", "rank_universe_v2", "generate_trade_candidates_v2"}
SAFETY_FLAGS = {
    "preview_only": True,
    "orders_enabled": False,
    "broker_execution_enabled": False,
    "automation_enabled": False,
}


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, allow_nan=False)
        fh.write("\n")


def _write_csv(path: Path, header, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def load_framework() -> dict:
    if not _FRAMEWORK.is_file():
        raise FileNotFoundError(
            f"alpha_framework.json not found at {_FRAMEWORK}. Phase 5-B requires "
            "the canonical framework spec; it is not fabricated by the runner.")
    with open(_FRAMEWORK, "r", encoding="utf-8") as fh:
        return json.load(fh)


def validate_framework(fw: dict) -> dict:
    """Return a validation report; raise AssertionError on any hard failure."""
    errors = []
    warnings = []

    for section in REQUIRED_SECTIONS:
        if section not in fw:
            errors.append(f"missing required section: {section}")

    # Safety flags must be present and correct.
    for flag, expected in SAFETY_FLAGS.items():
        if fw.get(flag) != expected:
            errors.append(f"safety flag {flag} must be {expected}, got {fw.get(flag)!r}")

    # Action values must include the full action set.
    actions = set(fw.get("scoring_design", {}).get("action_values", []))
    missing_actions = REQUIRED_ACTIONS - actions
    if missing_actions:
        errors.append(f"scoring_design.action_values missing: {sorted(missing_actions)}")

    # Implementation contract must define the three Phase 5-C functions.
    fn_names = {f.get("name") for f in fw.get("implementation_contract", {}).get("functions", [])}
    missing_fns = REQUIRED_FUNCTIONS - fn_names
    if missing_fns:
        errors.append(f"implementation_contract missing functions: {sorted(missing_fns)}")

    # Primary label must be the 20d forward excess return vs SPY.
    primary = fw.get("labels", {}).get("recommended_primary_label")
    if primary != "forward_excess_return_20d_vs_spy":
        errors.append(
            "labels.recommended_primary_label must be "
            f"'forward_excess_return_20d_vs_spy', got {primary!r}")

    # SPY must be the regime proxy, not the trading universe.
    if "regime" not in str(fw.get("spy_role", "")).lower():
        warnings.append("spy_role does not clearly mark SPY as a regime proxy")

    if errors:
        raise AssertionError("framework validation failed:\n  - " + "\n  - ".join(errors))

    return {"errors": errors, "warnings": warnings, "ok": True}


def _feature_catalog_rows(fw: dict):
    rows = []
    # Market regime layer features.
    for feat in fw["market_regime_layer"].get("features", []):
        rows.append([
            "market_regime",
            feat.get("regime_dimension", ""),
            feat["name"],
            feat.get("definition", ""),
            feat.get("availability", ""),
            feat.get("data_source", ""),
        ])
    # Cross-sectional feature families.
    for fam in fw["cross_sectional_alpha_layer"].get("feature_families", []):
        for feat in fam.get("features", []):
            rows.append([
                "cross_sectional",
                fam["family"],
                feat["name"],
                feat.get("definition", ""),
                feat.get("availability", ""),
                feat.get("data_source", ""),
            ])
    return rows


def _label_catalog_rows(fw: dict):
    rows = []
    for lab in fw["labels"].get("catalog", []):
        rows.append([
            lab["name"], lab.get("horizon", ""), lab.get("type", ""),
            lab.get("role", ""), lab.get("definition", ""), lab.get("feasibility", ""),
        ])
    return rows


def _gate_matrix_rows(fw: dict):
    rows = []
    for g in fw["acceptance_gates"].get("gates", []):
        rows.append([
            g["gate_id"], g.get("category", ""), g["gate_name"], g.get("metric", ""),
            g.get("threshold", ""), str(g.get("blocking", "")), g.get("applies_before", ""),
        ])
    return rows


def _implementation_plan(fw: dict) -> dict:
    contract = fw["implementation_contract"]
    return {
        "phase": "5-C",
        "framework_version": fw.get("framework_version"),
        "package": "research/model_v2_pricing_engine",
        "first_deployable_model": fw["model_families"]["stage_1_baseline_deployable"]["name"],
        "recommended_primary_label": fw["labels"]["recommended_primary_label"],
        "recommended_secondary_labels": fw["labels"]["recommended_secondary_labels"],
        "functions_to_build": contract["functions"],
        "build_order": [
            "1. Universe resolution (S&P 500 constituents from local artifacts; exclude SPY).",
            "2. Cross-sectional feature computation per ticker (point-in-time, reuse features.py).",
            "3. SPY regime overlay via score_market_v2 (market_regime + market_regime_score).",
            "4. Composite z-score + regime adjustment -> signal_score; rank -> rank_score.",
            "5. Action derivation (BUY/SELL/HOLD/AVOID/INSUFFICIENT_DATA) + reason_codes + risk_flags.",
            "6. rank_universe_v2 and generate_trade_candidates_v2 wrappers.",
            "7. Walk-forward validation harness + acceptance-gate evaluation (no deploy).",
        ],
        "gating": {
            "must_pass_before_preview_ranking": [
                g["gate_id"] for g in fw["acceptance_gates"]["gates"]
                if g.get("applies_before") in ("phase_5c_preview_ranking", "always")
            ],
            "must_pass_before_gcp_deployment": [
                g["gate_id"] for g in fw["acceptance_gates"]["gates"]
                if g.get("applies_before") == "gcp_deployment"
            ],
            "must_pass_before_any_execution": [
                g["gate_id"] for g in fw["acceptance_gates"]["gates"]
                if g.get("applies_before") == "any_execution"
            ],
        },
        "future_gcp_endpoints": fw["future_gcp_endpoints"]["endpoints"],
        "safety": {k: fw.get(k) for k in SAFETY_FLAGS},
        "preview_only": True,
        "orders_enabled": False,
        "broker_execution_enabled": False,
        "automation_enabled": False,
        "deploy_now": False,
        "commit_now": False,
    }


def main() -> int:
    fw = load_framework()
    validation = validate_framework(fw)

    feature_rows = _feature_catalog_rows(fw)
    label_rows = _label_catalog_rows(fw)
    gate_rows = _gate_matrix_rows(fw)
    plan = _implementation_plan(fw)

    feature_families = [f["family"] for f in fw["cross_sectional_alpha_layer"]["feature_families"]]
    n_gates = len(gate_rows)
    n_labels = len(label_rows)

    # Write derived artifacts.
    _write_csv(_FEATURE_CATALOG_OUT,
               ["layer", "family", "feature_name", "definition", "availability", "data_source"],
               feature_rows)
    _write_csv(_LABEL_CATALOG_OUT,
               ["label_name", "horizon", "type", "role", "definition", "feasibility"],
               label_rows)
    _write_csv(_GATE_MATRIX_OUT,
               ["gate_id", "category", "gate_name", "metric", "threshold", "blocking", "applies_before"],
               gate_rows)
    _write_json(_PLAN_OUT, plan)

    report = {
        "phase": "5-B",
        "framework_name": fw.get("framework_name"),
        "framework_version": fw.get("framework_version"),
        # Root-level safety flags: mirrored from the canonical framework so
        # external validation can read them without descending into ``framework``.
        "preview_only": fw.get("preview_only"),
        "orders_enabled": fw.get("orders_enabled"),
        "automation_enabled": fw.get("automation_enabled"),
        "broker_execution_enabled": fw.get("broker_execution_enabled"),
        "production_replacement": fw.get("production_replacement"),
        "spy_role": fw.get("spy_role"),
        "trading_universe": fw.get("trading_universe"),
        "validation": validation,
        "counts": {
            "regime_feature_count": len(fw["market_regime_layer"]["features"]),
            "feature_family_count": len(feature_families),
            "cross_sectional_feature_count": sum(
                len(f["features"]) for f in fw["cross_sectional_alpha_layer"]["feature_families"]),
            "total_feature_rows": len(feature_rows),
            "label_count": n_labels,
            "validation_method_count": len(fw["validation_methodology"]["methods"]),
            "acceptance_gate_count": n_gates,
            "function_count": len(fw["implementation_contract"]["functions"]),
            "future_endpoint_count": len(fw["future_gcp_endpoints"]["endpoints"]),
        },
        "feature_families": feature_families,
        "recommended_primary_label": fw["labels"]["recommended_primary_label"],
        "recommended_first_deployable_model": fw["model_families"]["stage_1_baseline_deployable"]["name"],
        "framework_source": str(_FRAMEWORK.relative_to(_REPO_ROOT)),
        "framework": fw,
        "network_used": False,
        "paid_apis_used": False,
        "deployed": False,
        "data_faked": False,
        "binary_artifacts_created": False,
        "next_phase": "5-C: implement score_ticker_v2 (extend), rank_universe_v2, generate_trade_candidates_v2 + walk-forward validation. Preview-only, no deploy.",
    }
    _write_json(_REPORT_OUT, report)

    # Concise summary.
    print("=" * 64)
    print("Phase 5-B | quant alpha framework for S&P 500 ticker selection")
    print("=" * 64)
    print(f"  framework_version          : {fw.get('framework_version')}")
    print(f"  spy_role                   : {fw.get('spy_role')}")
    print(f"  trading_universe           : {fw.get('trading_universe')}")
    print(f"  feature families           : {len(feature_families)} "
          f"({', '.join(feature_families)})")
    print(f"  total feature rows         : {len(feature_rows)} "
          f"(regime + cross-sectional)")
    print(f"  labels                     : {n_labels}")
    print(f"  validation methods         : {len(fw['validation_methodology']['methods'])}")
    print(f"  validation/acceptance gates: {n_gates}")
    print(f"  recommended primary label  : {fw['labels']['recommended_primary_label']}")
    print(f"  first deployable model     : {fw['model_families']['stage_1_baseline_deployable']['name']}")
    print(f"  validation_ok              : {validation['ok']} "
          f"(warnings: {len(validation['warnings'])})")
    print("-" * 64)
    print(f"  wrote: {_REPORT_OUT.relative_to(_REPO_ROOT)}")
    print(f"  wrote: {_FEATURE_CATALOG_OUT.relative_to(_REPO_ROOT)}")
    print(f"  wrote: {_LABEL_CATALOG_OUT.relative_to(_REPO_ROOT)}")
    print(f"  wrote: {_GATE_MATRIX_OUT.relative_to(_REPO_ROOT)}")
    print(f"  wrote: {_PLAN_OUT.relative_to(_REPO_ROOT)}")
    print(f"  next_phase                 : 5-C (implement ranking + candidates, preview-only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
