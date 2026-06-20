"""Phase 3-R tests for the Macro/Inflation Regime Feature Layer + Walk-Forward Re-Test.

These tests prove the phase is a disciplined, research-only, safety-controlled, NON-faking macro
layer: the runner compiles and imports; it confirms Phase 3-Q (phase == "3-Q", weak-fixable/pass
recommendation, next phase 3-R, no production artifacts); the result JSON exists with
phase == "3-R"; the Phase 3-Q confirmation block is present; recommended_next_phase.phase ==
"3-S"; the macro data inventory and feature registry CSVs exist; if no usable local macro data
exists the recommendation is MACRO_REGIME_WALKFORWARD_BLOCKED_NEEDS_LOCAL_MACRO_DATA (this is a
PASS, not a failure) and the macro data requirements are documented; if macro data exists the
point-in-time panel sample exists; the source contains no Alpha Vantage / provider / yfinance /
FRED-API / DB-write / deployment / order-trading / production-candidate / deployable-artifact
(pickle/joblib/serialized model) / D:-write tokens; macro_faked / sentiment_faked are False; and
every output file is Git-safe (< 50 MB).

Runs two ways:
  * under pytest:   pytest tests/test_phase3r_macro_regime_walkforward.py
  * without pytest: python tests/test_phase3r_macro_regime_walkforward.py
The __main__ block discovers every test_* function, runs it, prints PASS/FAIL/SKIP, and exits
non-zero on any failure.
"""
from __future__ import annotations

import ast
import csv
import importlib.util
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_RUNNER = os.path.join(_REPO_ROOT, "research", "run_phase3r_macro_regime_walkforward.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase3r_macro_regime_walkforward_v1.md")
_RESULT = os.path.join(_REPO_ROOT, "research", "output", "phase3r_macro_regime_walkforward.json")
_R_DIR = os.path.join(_REPO_ROOT, "research", "output", "phase3r_macro_regime_walkforward")


class _Skip(Exception):
    """Raised to mark a test skipped."""


# Required output artifacts (basenames).
_EXPECTED_OUTPUT_BASENAMES = (
    "macro_data_inventory.csv",
    "macro_feature_registry.csv",
    "macro_feature_panel_sample.csv",
    "macro_walkforward_scoreboard.csv",
    "macro_yearly_stability.csv",
    "macro_regime_performance.csv",
    "macro_bad_year_comparison.csv",
    "macro_improvement_decision_table.csv",
)

# Functions the source MUST define.
_REQUIRED_FUNCTIONS = (
    "confirm_phase3q", "build_macro_data_inventory", "macro_feature_registry",
    "point_in_time_assumptions", "macro_data_requirements", "run_macro_walkforward",
    "build_macro_feature_panel", "decide",
)

# Forbidden tokens (assembled from fragments so they never self-match this test's prose).
_FORBIDDEN_NETWORK_TOKENS = [
    "yf" + "inance", "import " + "requests", "from " + "requests",
    "alpha" + "vantage", "fin" + "nhub", "poly" + "gon.io", "iex" + "cloud",
    "urllib." + "request", "http" + "x", "fred" + "api", "pandas_" + "datareader",
]
_FORBIDDEN_INFRA_TOKENS = [
    "gcl" + "oud", "sub" + "process", "os." + "system", "para" + "miko", "system" + "ctl",
    "kube" + "ctl", "stock-api." + "service", "alem" + "bic", "PREDICTOR_USE_" + "MODEL_V2",
    "uvi" + "corn",
]
_FORBIDDEN_DB_TOKENS = [
    "insert " + "into", "delete " + "from", "drop " + "table", "alter " + "table",
    "create " + "table", "trun" + "cate", "to_" + "sql",
]
_FORBIDDEN_DEPLOY_TRADE_TOKENS = [
    "place" + "_order", "submit" + "_order", "create" + "_order", "ssh ", "scp ",
]
_FORBIDDEN_ARTIFACT_TOKENS = [
    "to_" + "pickle", "pickle." + "dump", "joblib." + "dump", "import " + "pickle",
    "import " + "joblib", ".pk" + "l", ".job" + "lib", ".onn" + "x", ".ker" + "as",
]
_FORBIDDEN_IMPORT_ROOTS = {
    "sklearn", "tensorflow", "torch", "keras", "lightgbm", "xgboost",
    "api_server", "yfinance", "requests", "urllib",
}

_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "phase3q_confirmation", "macro_data_inventory_summary",
    "macro_features_implemented", "macro_features_blocked", "point_in_time_assumptions",
    "model_comparison_summary", "bad_year_comparison", "recommendation",
    "recommended_next_phase", "interpretation",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed", "deployment_executed",
    "model_v2_enabled", "production_edge_claimed", "production_model_trained",
    "production_model_candidate_created", "deployable_model_artifact_written",
    "production_predictions_computed", "production_scores_computed", "portfolio_weights_computed",
    "order_instructions_created", "d_drive_written", "provider_api_called", "alpha_vantage_called",
    "paid_vendor_api_called", "macro_faked", "sentiment_faked",
)
_REQUIRED_TRUE = (
    "no_trading", "no_orders", "no_automation", "research_only", "labels_for_validation_only",
)
_ALLOWED_RECOMMENDATIONS = {
    "MACRO_REGIME_WALKFORWARD_IMPROVES_ROBUSTNESS",
    "MACRO_REGIME_WALKFORWARD_WEAK_NO_IMPROVEMENT",
    "MACRO_REGIME_WALKFORWARD_BLOCKED_NEEDS_LOCAL_MACRO_DATA",
    "MACRO_REGIME_WALKFORWARD_BLOCKED_INPUTS",
}
_MAX_FILE_BYTES = 50 * 1024 * 1024


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _read_csv_rows(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _import_runner():
    spec = importlib.util.spec_from_file_location("phase3r_runner_test", _RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_result():
    if not os.path.isfile(_RESULT):
        raise _Skip("result JSON not present; run the runner first")
    return json.loads(_read(_RESULT))


def _rows(basename):
    p = os.path.join(_R_DIR, basename)
    if not os.path.isfile(p):
        raise _Skip("%s not present; run the runner first" % basename)
    return _read_csv_rows(p)


def _no_macro_data(res):
    return bool(res["macro_data_inventory_summary"].get("no_local_macro_data"))


# --------------------------------------------------------------------------- #
# 1. Runner compiles and imports
# --------------------------------------------------------------------------- #
def test_runner_compiles():
    compile(_read(_RUNNER), _RUNNER, "exec")


def test_runner_imports():
    _import_runner()


def test_required_functions_defined():
    tree = ast.parse(_read(_RUNNER))
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for fn in _REQUIRED_FUNCTIONS:
        assert fn in defined, "runner must define function %r" % fn


# --------------------------------------------------------------------------- #
# 2. Outputs under research/output (C:), never D:
# --------------------------------------------------------------------------- #
def test_outputs_under_research_output_on_c_drive():
    mod = _import_runner()
    assert mod._R_DIR.replace("\\", "/").endswith(
        "research/output/phase3r_macro_regime_walkforward")
    assert mod.RESULT_JSON.replace("\\", "/").endswith(
        "research/output/phase3r_macro_regime_walkforward.json")
    assert os.path.splitdrive(os.path.abspath(mod._R_DIR))[0].upper() != "D:"


def test_price_panel_is_on_d_and_only_read():
    mod = _import_runner()
    assert os.path.splitdrive(os.path.abspath(mod.PRICE_CSV))[0].upper() == "D:"
    src = _read(_RUNNER)
    for write_token in ("to_csv(", ".to_parquet(", "open(" + "PRICE"):
        assert write_token not in src, "must not write the D: price panel (%r)" % write_token


# --------------------------------------------------------------------------- #
# 3. No forbidden tokens / imports in the source
# --------------------------------------------------------------------------- #
def test_no_forbidden_tokens():
    src = _read(_RUNNER).lower()
    for token in (_FORBIDDEN_NETWORK_TOKENS + _FORBIDDEN_INFRA_TOKENS + _FORBIDDEN_DB_TOKENS +
                  _FORBIDDEN_DEPLOY_TRADE_TOKENS + _FORBIDDEN_ARTIFACT_TOKENS):
        assert token.lower() not in src, "forbidden token present: %r" % token


def test_no_forbidden_imports():
    tree = ast.parse(_read(_RUNNER))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                roots.add(n.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    bad = roots & _FORBIDDEN_IMPORT_ROOTS
    assert not bad, "forbidden imports present: %s" % sorted(bad)


# --------------------------------------------------------------------------- #
# 4. Result JSON exists with phase == "3-R" and required fields
# --------------------------------------------------------------------------- #
def test_result_json_exists_and_phase():
    res = _load_result()
    assert res["phase"] == "3-R"
    for field in _REQUIRED_JSON_FIELDS:
        assert field in res, "missing result field %r" % field


def test_phase3q_confirmation_present():
    res = _load_result()
    conf = res["phase3q_confirmation"]
    assert conf.get("phase_is_3q") is True
    assert conf.get("next_phase_is_3r") is True
    assert conf.get("no_production_model_candidate") is True
    assert conf.get("no_deployable_artifact") is True
    assert conf.get("confirmed") is True


def test_recommendation_and_next_phase():
    res = _load_result()
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "3-S"


def test_blocked_recommendation_is_not_a_failure():
    # If no usable local macro data exists, the recommendation MUST be the blocked-needs-data
    # value and this is an expected (PASS) outcome, not a test failure.
    res = _load_result()
    if _no_macro_data(res):
        assert (res["recommendation"]["recommendation"]
                == "MACRO_REGIME_WALKFORWARD_BLOCKED_NEEDS_LOCAL_MACRO_DATA")
        assert res["recommended_next_phase"]["phase"] == "3-S"
        assert "macro_data_requirements" in res
        assert res["macro_data_requirements"]["required_files"], "requirements must be listed"


def test_safety_flags():
    res = _load_result()
    for flag in _REQUIRED_FALSE:
        assert res.get(flag) is False, "%s must be False" % flag
    for flag in _REQUIRED_TRUE:
        assert res.get(flag) is True, "%s must be True" % flag


def test_no_production_artifacts_in_result():
    res = _load_result()
    assert res["deployable_model_artifact_written"] is False
    assert res["production_model_candidate_created"] is False
    assert res["production_predictions_computed"] is False
    assert res["production_scores_computed"] is False
    assert res["portfolio_weights_computed"] is False
    assert res["macro_faked"] is False
    assert res["sentiment_faked"] is False
    interp = res["interpretation"]
    assert interp["full_prediction_panel_written_to_disk"] is False
    assert interp["macro_family_faked"] is False


# --------------------------------------------------------------------------- #
# 5. Output artifacts exist and are Git-safe
# --------------------------------------------------------------------------- #
def test_output_artifacts_exist():
    if not os.path.isdir(_R_DIR):
        raise _Skip("output dir not present; run the runner first")
    for base in _EXPECTED_OUTPUT_BASENAMES:
        p = os.path.join(_R_DIR, base)
        assert os.path.isfile(p), "missing output artifact %s" % base


def test_output_files_git_safe_under_50mb():
    if not os.path.isdir(_R_DIR):
        raise _Skip("output dir not present; run the runner first")
    for name in os.listdir(_R_DIR):
        p = os.path.join(_R_DIR, name)
        if os.path.isfile(p):
            assert os.path.getsize(p) <= _MAX_FILE_BYTES, "%s exceeds 50 MB" % name
    if os.path.isfile(_RESULT):
        assert os.path.getsize(_RESULT) <= _MAX_FILE_BYTES


def test_no_deployable_artifact_files_written():
    if not os.path.isdir(_R_DIR):
        raise _Skip("output dir not present; run the runner first")
    bad_ext = (".pk" + "l", ".job" + "lib", ".onn" + "x", ".h5", ".ker" + "as", ".pt", ".pth")
    for name in os.listdir(_R_DIR):
        assert not name.lower().endswith(bad_ext), "deployable artifact present: %s" % name


# --------------------------------------------------------------------------- #
# 6. Inventory / registry / requirements content
# --------------------------------------------------------------------------- #
def test_macro_data_inventory_exists():
    rows = _rows("macro_data_inventory.csv")
    assert rows, "macro data inventory must have at least one row"
    cols = set(rows[0].keys())
    for c in ("file_path", "detected_macro_family", "usable", "blocker"):
        assert c in cols, "inventory missing column %r" % c


def test_macro_feature_registry_exists():
    rows = _rows("macro_feature_registry.csv")
    assert rows, "macro feature registry must have rows"
    cols = set(rows[0].keys())
    for c in ("feature_name", "macro_family", "frequency", "availability_lag_rule",
              "point_in_time_safe", "implemented", "blocker"):
        assert c in cols, "registry missing column %r" % c
    feats = {r["feature_name"] for r in rows}
    for required in ("cpi_yoy", "fed_funds_level", "treasury_10y", "yield_curve_10y_2y",
                     "macro_risk_off_flag"):
        assert required in feats, "registry missing preferred feature %r" % required


def test_panel_sample_present_and_pit_documented():
    res = _load_result()
    p = os.path.join(_R_DIR, "macro_feature_panel_sample.csv")
    assert os.path.isfile(p), "macro_feature_panel_sample.csv must exist (headers at minimum)"
    pit = res["point_in_time_assumptions"]
    assert pit["never_use_future_macro_observations_for_past_scoring_dates"] is True
    assert "monthly_release_default_lag_calendar_days" in pit
    if not _no_macro_data(res):
        # When macro data exists the sample must carry actual rows.
        rows = _read_csv_rows(p)
        assert rows, "panel sample must have rows when macro data exists"


def test_bad_year_comparison_exists():
    rows = _rows("macro_bad_year_comparison.csv")
    assert rows, "bad-year comparison must have rows (baseline carry-forward at minimum)"
    cols = set(rows[0].keys())
    for c in ("model", "ic_2021", "worst_year_ic", "improves_bad_year"):
        assert c in cols, "bad-year comparison missing column %r" % c


def test_decision_table_exists():
    rows = _rows("macro_improvement_decision_table.csv")
    assert rows, "decision table must have rows"
    cols = set(rows[0].keys())
    for c in ("decision_item", "value", "passed", "note"):
        assert c in cols, "decision table missing column %r" % c
    items = {r["decision_item"] for r in rows}
    assert "usable_local_macro_data_found" in items
    assert "no_macro_data_faked" in items


# --------------------------------------------------------------------------- #
# 7. Documentation carries the required guardrail phrases
# --------------------------------------------------------------------------- #
_REQUIRED_DOC_PHRASES = [
    "does not deploy",
    "does not restart stock-api.service",
    "does not enable",
    "does not run migrations",
    "does not write to production DB",
    "does not trade",
    "production edge",
    "walk-forward",
    "point-in-time",
    "availability lag",
    "macro",
    "inflation",
    "BLOCKED_NEEDS_LOCAL_MACRO_DATA",
]


def test_doc_required_phrases():
    if not os.path.isfile(_DOC):
        raise _Skip("doc not present")
    doc = _read(_DOC)
    for phrase in _REQUIRED_DOC_PHRASES:
        assert phrase in doc, "doc missing required phrase %r" % phrase


def test_doc_explains_research_only_and_not_faked():
    if not os.path.isfile(_DOC):
        raise _Skip("doc not present")
    doc = _read(_DOC).lower()
    assert "research-only" in doc or "research only" in doc
    assert "not faked" in doc or "are not faked" in doc or "never faked" in doc


# --------------------------------------------------------------------------- #
# 8. Optional live end-to-end run (gated behind PHASE3R_LIVE=1)
# --------------------------------------------------------------------------- #
def test_live_end_to_end_optional():
    if os.environ.get("PHASE3R_LIVE") != "1":
        raise _Skip("set PHASE3R_LIVE=1 to run the full end-to-end macro re-test")
    import tempfile
    mod = _import_runner()
    tmp = tempfile.mkdtemp(prefix="phase3r_")
    res = mod.run(result_json_path=os.path.join(tmp, "result.json"), o_dir=tmp, verbose=False)
    assert res["phase"] == "3-R"
    assert res["d_drive_written"] is False
    assert res["portfolio_weights_computed"] is False
    assert res["production_predictions_computed"] is False
    assert res["production_scores_computed"] is False
    assert res["production_model_candidate_created"] is False
    assert res["deployable_model_artifact_written"] is False
    assert res["macro_faked"] is False and res["sentiment_faked"] is False
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "3-S"


# --------------------------------------------------------------------------- #
# Self-running harness (no pytest required)
# --------------------------------------------------------------------------- #
def _run_all():
    tests = sorted((n, f) for n, f in globals().items()
                   if n.startswith("test_") and callable(f))
    passed = failed = skipped = 0
    failures = []
    for name, fn in tests:
        try:
            fn()
            print("PASS %s" % name)
            passed += 1
        except _Skip as s:
            print("SKIP %s: %s" % (name, s))
            skipped += 1
        except Exception as e:  # noqa: BLE001
            import traceback
            print("FAIL %s: %s" % (name, e))
            failures.append((name, traceback.format_exc()))
            failed += 1
    print("\n%d passed, %d failed, %d skipped, %d total"
          % (passed, failed, skipped, len(tests)))
    if failures:
        print("\n--- failure details ---")
        for name, tb in failures:
            print("\n### %s\n%s" % (name, tb))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
