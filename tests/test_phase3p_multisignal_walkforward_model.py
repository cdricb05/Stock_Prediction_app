"""Phase 3-P tests for the Research-Only Multi-Signal Walk-Forward Ranking Model.

These tests prove the phase is a disciplined, research-only, safety-controlled model layer:
the runner compiles and imports; it confirms Phase 3-O (phase == "3-O", research-ready
recommendation, next phase 3-P, macro/sentiment not faked); the result JSON exists with
phase == "3-P"; the Phase 3-O confirmation block is present; recommended_next_phase.phase ==
"3-Q"; the model scoreboard / walk-forward fold summary / feature-weight summary exist; the
model_feature_set.csv includes at least technical, sector, SEC and combined feature sets;
the source contains no Alpha Vantage / provider / yfinance / DB-write / deployment /
order-trading / production-candidate / deployable-artifact (pickle/joblib/serialized model) /
D:-write tokens; the data transforms are train-only (train_median_imputer / train_standardizer /
walkforward_splits_with_embargo present); and every output file is Git-safe (< 50 MB).

Runs two ways:
  * under pytest:   pytest tests/test_phase3p_multisignal_walkforward_model.py
  * without pytest: python tests/test_phase3p_multisignal_walkforward_model.py
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

_RUNNER = os.path.join(_REPO_ROOT, "research", "run_phase3p_multisignal_walkforward_model.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase3p_multisignal_walkforward_model_v1.md")
_RESULT = os.path.join(_REPO_ROOT, "research", "output",
                       "phase3p_multisignal_walkforward_model.json")
_P_DIR = os.path.join(_REPO_ROOT, "research", "output", "phase3p_multisignal_walkforward_model")


class _Skip(Exception):
    """Raised to mark a test skipped."""


# Required output artifacts (basenames).
_EXPECTED_OUTPUT_BASENAMES = (
    "model_feature_set.csv",
    "walkforward_fold_summary.csv",
    "model_scoreboard.csv",
    "yearly_model_stability.csv",
    "feature_weight_summary.csv",
    "readiness_decision_table.csv",
)

# Train-only transform / split function names the source MUST define.
_REQUIRED_FUNCTIONS = (
    "datewise_rank_or_zscore_target", "train_median_imputer", "apply_median_imputer",
    "train_standardizer", "apply_standardizer", "fit_ridge_closed_form", "predict_linear_score",
    "walkforward_splits_with_embargo", "evaluate_rank_model",
)

# Forbidden tokens (assembled from fragments so they never self-match this test's prose).
_FORBIDDEN_NETWORK_TOKENS = [
    "yf" + "inance", "import " + "requests", "from " + "requests",
    "alpha" + "vantage", "fin" + "nhub", "poly" + "gon.io", "iex" + "cloud",
    "urllib." + "request", "http" + "x",
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
    "phase", "generated_at", "phase3o_confirmation", "feature_panel_summary", "feature_sets",
    "walkforward_design", "model_scoreboard", "model_scoreboard_summary", "best_model",
    "model_family_comparison", "yearly_stability_summary", "recommendation",
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
    "no_trading", "no_orders", "no_automation", "research_models_trained_in_memory",
    "research_oos_scores_computed", "d_drive_read", "labels_for_validation_only",
)
_ALLOWED_RECOMMENDATIONS = {
    "MULTISIGNAL_WALKFORWARD_MODEL_RESEARCH_PASS",
    "MULTISIGNAL_WALKFORWARD_MODEL_WEAK_BUT_PROMISING",
    "MULTISIGNAL_WALKFORWARD_MODEL_FAILS_ROBUSTNESS",
    "MULTISIGNAL_WALKFORWARD_MODEL_BLOCKED_INPUTS",
}
_REQUIRED_SCOREBOARD_MODELS = (
    "momentum_rank_composite", "sector_neutral_momentum", "sec_fundamental_rank_composite",
    "ridge_technical_only", "ridge_sec_fundamental_only", "ridge_combined_no_earnings",
    "ridge_combined_with_partial_earnings", "ridge_combined_regime_interactions",
    "regime_aware_ensemble",
)
_REQUIRED_FEATURE_SETS = ("technical_only", "sector_relative_only", "sec_fundamental_only",
                          "combined_with_partial_earnings")
_MAX_FILE_BYTES = 50 * 1024 * 1024


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _read_csv_rows(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _import_runner():
    spec = importlib.util.spec_from_file_location("phase3p_runner_test", _RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_result():
    if not os.path.isfile(_RESULT):
        raise _Skip("result JSON not present; run the runner first")
    return json.loads(_read(_RESULT))


# --------------------------------------------------------------------------- #
# 1. Runner compiles and imports
# --------------------------------------------------------------------------- #
def test_runner_compiles():
    compile(_read(_RUNNER), _RUNNER, "exec")


def test_runner_imports():
    _import_runner()


def test_required_transform_functions_defined():
    tree = ast.parse(_read(_RUNNER))
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for fn in _REQUIRED_FUNCTIONS:
        assert fn in defined, "runner must define train-only/split function %r" % fn


# --------------------------------------------------------------------------- #
# 2. Outputs under research/output (C:), never D:
# --------------------------------------------------------------------------- #
def test_outputs_under_research_output_on_c_drive():
    mod = _import_runner()
    assert mod._P_DIR.replace("\\", "/").endswith(
        "research/output/phase3p_multisignal_walkforward_model")
    assert mod.RESULT_JSON.replace("\\", "/").endswith(
        "research/output/phase3p_multisignal_walkforward_model.json")
    assert os.path.splitdrive(os.path.abspath(mod._P_DIR))[0].upper() != "D:"


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
# 4. Result JSON exists with phase == "3-P" and required fields
# --------------------------------------------------------------------------- #
def test_result_json_exists_and_phase():
    res = _load_result()
    assert res["phase"] == "3-P"
    for field in _REQUIRED_JSON_FIELDS:
        assert field in res, "missing result field %r" % field


def test_phase3o_confirmation_present():
    res = _load_result()
    conf = res["phase3o_confirmation"]
    assert conf.get("phase_is_3o") is True
    assert conf.get("next_phase_is_3p") is True
    assert conf.get("macro_not_faked") is True
    assert conf.get("sentiment_not_faked") is True
    assert conf.get("confirmed") is True


def test_recommendation_and_next_phase():
    res = _load_result()
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "3-Q"


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
    assert res["portfolio_weights_computed"] is False
    interp = res["interpretation"]
    assert interp["ridge_is_numpy_closed_form_no_sklearn"] is True
    assert interp["all_transforms_fit_on_training_rows_only"] is True


# --------------------------------------------------------------------------- #
# 5. Output artifacts exist and are Git-safe
# --------------------------------------------------------------------------- #
def test_output_artifacts_exist():
    if not os.path.isdir(_P_DIR):
        raise _Skip("output dir not present; run the runner first")
    for base in _EXPECTED_OUTPUT_BASENAMES:
        p = os.path.join(_P_DIR, base)
        assert os.path.isfile(p), "missing output artifact %s" % base


def test_output_files_git_safe_under_50mb():
    if not os.path.isdir(_P_DIR):
        raise _Skip("output dir not present; run the runner first")
    for name in os.listdir(_P_DIR):
        p = os.path.join(_P_DIR, name)
        if os.path.isfile(p):
            assert os.path.getsize(p) <= _MAX_FILE_BYTES, "%s exceeds 50 MB" % name
    if os.path.isfile(_RESULT):
        assert os.path.getsize(_RESULT) <= _MAX_FILE_BYTES


def test_no_deployable_artifact_files_written():
    if not os.path.isdir(_P_DIR):
        raise _Skip("output dir not present; run the runner first")
    bad_ext = (".pk" + "l", ".job" + "lib", ".onn" + "x", ".h5", ".ker" + "as", ".pt", ".pth")
    for name in os.listdir(_P_DIR):
        assert not name.lower().endswith(bad_ext), "deployable artifact present: %s" % name


# --------------------------------------------------------------------------- #
# 6. Scoreboard / folds / weights present with required content
# --------------------------------------------------------------------------- #
def _rows(basename):
    p = os.path.join(_P_DIR, basename)
    if not os.path.isfile(p):
        raise _Skip("%s not present; run the runner first" % basename)
    return _read_csv_rows(p)


def test_model_scoreboard_has_required_models():
    rows = _rows("model_scoreboard.csv")
    models = {r["model"] for r in rows}
    for m in _REQUIRED_SCOREBOARD_MODELS:
        assert m in models, "scoreboard missing model %r" % m
    cols = set(rows[0].keys())
    for c in ("horizon", "fold_count", "train_rows_total", "test_rows_total", "test_tickers",
              "mean_daily_rank_ic", "median_daily_rank_ic", "rank_ic_hit_rate",
              "top_decile_minus_bottom_decile_spread", "top_quintile_minus_bottom_quintile_spread",
              "top_decile_hit_rate", "annual_coverage_years", "worst_year_ic", "best_year_ic",
              "stability_score"):
        assert c in cols, "scoreboard missing metric column %r" % c


def test_walkforward_fold_summary_exists():
    rows = _rows("walkforward_fold_summary.csv")
    assert rows, "walk-forward fold summary must have rows"
    cols = set(rows[0].keys())
    for c in ("model", "horizon", "test_year", "train_rows", "test_rows", "embargo_trading_days"):
        assert c in cols, "fold summary missing column %r" % c
    # Embargo must equal the horizon (per-horizon embargo).
    for r in rows:
        h = int(r["horizon"].replace("d", ""))
        assert int(r["embargo_trading_days"]) == h, "embargo must equal the horizon"


def test_feature_weight_summary_exists():
    rows = _rows("feature_weight_summary.csv")
    assert rows, "feature weight summary must have rows"
    cols = set(rows[0].keys())
    for c in ("model", "horizon", "feature", "mean_standardized_weight", "mean_abs_weight"):
        assert c in cols, "weight summary missing column %r" % c
    models = {r["model"] for r in rows}
    assert any(m.startswith("ridge_") for m in models), "weights must cover a ridge model"


def test_model_feature_set_includes_required_sets():
    rows = _rows("model_feature_set.csv")
    sets = {r["feature_set"] for r in rows}
    for s in _REQUIRED_FEATURE_SETS:
        assert s in sets, "model_feature_set.csv missing feature set %r" % s
    # Technical, sector and SEC families must be represented.
    fams = {r["feature_family"] for r in rows}
    for fam in ("technical_price", "sector_relative", "sec_fundamental"):
        assert fam in fams, "feature set listing missing family %r" % fam


def test_yearly_stability_present():
    rows = _rows("yearly_model_stability.csv")
    # May be empty only if blocked; if present, must carry the expected columns.
    if rows:
        cols = set(rows[0].keys())
        for c in ("model", "horizon", "year", "mean_daily_rank_ic"):
            assert c in cols, "stability summary missing column %r" % c


# --------------------------------------------------------------------------- #
# 7. Walk-forward design carries the embargo + min-train-years contract
# --------------------------------------------------------------------------- #
def test_walkforward_design_contract():
    res = _load_result()
    d = res["walkforward_design"]
    assert d["min_train_years"] >= 3
    emb = d["embargo_trading_days_by_horizon"]
    assert int(emb["21"]) == 21 and int(emb["63"]) == 63 and int(emb["126"]) == 126
    assert "training" in d["lambda_selection"].lower()


# --------------------------------------------------------------------------- #
# 8. Documentation carries the required guardrail phrases
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
    "embargo",
]


def test_doc_required_phrases():
    if not os.path.isfile(_DOC):
        raise _Skip("doc not present")
    doc = _read(_DOC)
    for phrase in _REQUIRED_DOC_PHRASES:
        assert phrase in doc, "doc missing required phrase %r" % phrase


def test_doc_explains_no_sklearn_and_macro_sentiment():
    if not os.path.isfile(_DOC):
        raise _Skip("doc not present")
    doc = _read(_DOC).lower()
    assert "numpy" in doc and "ridge" in doc
    assert "not faked" in doc or "are not faked" in doc


# --------------------------------------------------------------------------- #
# 9. Optional live end-to-end run (gated behind PHASE3P_LIVE=1)
# --------------------------------------------------------------------------- #
def test_live_end_to_end_optional():
    if os.environ.get("PHASE3P_LIVE") != "1":
        raise _Skip("set PHASE3P_LIVE=1 to run the full end-to-end walk-forward model")
    import tempfile
    mod = _import_runner()
    if not os.path.isfile(mod.PHASE3L_PANEL_CSV):
        raise _Skip("Phase 3-L aligned panel not present in this environment")
    tmp = tempfile.mkdtemp(prefix="phase3p_")
    res = mod.run(result_json_path=os.path.join(tmp, "result.json"), o_dir=tmp, verbose=False)
    assert res["phase"] == "3-P"
    assert res["d_drive_written"] is False
    assert res["portfolio_weights_computed"] is False
    assert res["production_model_candidate_created"] is False
    assert res["deployable_model_artifact_written"] is False
    assert res["macro_faked"] is False and res["sentiment_faked"] is False
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "3-Q"


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
