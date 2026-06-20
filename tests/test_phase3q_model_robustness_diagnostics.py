"""Phase 3-Q tests for the Model Robustness, Turnover, Cost, and Bad-Year Failure Diagnosis.

These tests prove the phase is a disciplined, research-only, safety-controlled DIAGNOSIS layer:
the runner compiles and imports; it confirms Phase 3-P (phase == "3-P", weak/pass recommendation,
next phase 3-Q, no production artifacts); the result JSON exists with phase == "3-Q"; the
Phase 3-P confirmation block is present; recommended_next_phase.phase == "3-R"; the nine
diagnostic CSVs exist (bad-year / regime / sector / horizon / turnover-cost / weight-stability /
blend / improvement-priority / readiness); the bad-year attribution includes 2021; the
turnover/cost grid includes 0/5/10/25/50 bps; the source contains no Alpha Vantage / provider /
yfinance / DB-write / deployment / order-trading / production-candidate / deployable-artifact
(pickle/joblib/serialized model) / D:-write tokens; and every output file is Git-safe (< 50 MB).

Runs two ways:
  * under pytest:   pytest tests/test_phase3q_model_robustness_diagnostics.py
  * without pytest: python tests/test_phase3q_model_robustness_diagnostics.py
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

_RUNNER = os.path.join(_REPO_ROOT, "research", "run_phase3q_model_robustness_diagnostics.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase3q_model_robustness_diagnostics_v1.md")
_RESULT = os.path.join(_REPO_ROOT, "research", "output",
                       "phase3q_model_robustness_diagnostics.json")
_Q_DIR = os.path.join(_REPO_ROOT, "research", "output", "phase3q_model_robustness_diagnostics")


class _Skip(Exception):
    """Raised to mark a test skipped."""


# Required output artifacts (basenames).
_EXPECTED_OUTPUT_BASENAMES = (
    "year_failure_attribution.csv",
    "regime_performance_summary.csv",
    "sector_performance_summary.csv",
    "horizon_comparison_summary.csv",
    "turnover_cost_sensitivity.csv",
    "feature_weight_stability_summary.csv",
    "ensemble_blend_sensitivity.csv",
    "improvement_priority_table.csv",
    "readiness_decision_table.csv",
)

# Diagnostic / regeneration functions the source MUST define.
_REQUIRED_FUNCTIONS = (
    "confirm_phase3p", "oos_ridge_predictions", "oos_regime_ensemble_predictions",
    "bad_year_attribution", "regime_performance", "sector_attribution", "horizon_comparison",
    "turnover_cost_sensitivity", "feature_weight_stability", "ensemble_blend_sensitivity",
    "improvement_priority_table", "decide",
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
    "phase", "generated_at", "phase3p_confirmation", "best_model_reviewed", "bad_year_diagnosis",
    "regime_diagnosis", "sector_diagnosis", "turnover_cost_summary", "blend_summary",
    "improvement_priorities", "recommendation", "recommended_next_phase", "interpretation",
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
    "no_trading", "no_orders", "no_automation", "research_diagnostics_only", "d_drive_read",
    "labels_for_validation_only",
)
_ALLOWED_RECOMMENDATIONS = {
    "MODEL_ROBUSTNESS_PASS_READY_FOR_RISK_SIMULATION",
    "MODEL_ROBUSTNESS_WEAK_FIXABLE_WITH_REGIME_AND_DATA",
    "MODEL_ROBUSTNESS_FAILS_COST_OR_BAD_YEAR",
    "MODEL_ROBUSTNESS_BLOCKED_INPUTS",
}
_REQUIRED_COST_BPS = {0, 5, 10, 25, 50}
_REQUIRED_HORIZONS = {"21d", "63d", "126d"}
_MAX_FILE_BYTES = 50 * 1024 * 1024


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _read_csv_rows(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _import_runner():
    spec = importlib.util.spec_from_file_location("phase3q_runner_test", _RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_result():
    if not os.path.isfile(_RESULT):
        raise _Skip("result JSON not present; run the runner first")
    return json.loads(_read(_RESULT))


def _rows(basename):
    p = os.path.join(_Q_DIR, basename)
    if not os.path.isfile(p):
        raise _Skip("%s not present; run the runner first" % basename)
    return _read_csv_rows(p)


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
        assert fn in defined, "runner must define diagnostic function %r" % fn


# --------------------------------------------------------------------------- #
# 2. Outputs under research/output (C:), never D:
# --------------------------------------------------------------------------- #
def test_outputs_under_research_output_on_c_drive():
    mod = _import_runner()
    assert mod._Q_DIR.replace("\\", "/").endswith(
        "research/output/phase3q_model_robustness_diagnostics")
    assert mod.RESULT_JSON.replace("\\", "/").endswith(
        "research/output/phase3q_model_robustness_diagnostics.json")
    assert os.path.splitdrive(os.path.abspath(mod._Q_DIR))[0].upper() != "D:"


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
# 4. Result JSON exists with phase == "3-Q" and required fields
# --------------------------------------------------------------------------- #
def test_result_json_exists_and_phase():
    res = _load_result()
    assert res["phase"] == "3-Q"
    for field in _REQUIRED_JSON_FIELDS:
        assert field in res, "missing result field %r" % field


def test_phase3p_confirmation_present():
    res = _load_result()
    conf = res["phase3p_confirmation"]
    assert conf.get("phase_is_3p") is True
    assert conf.get("next_phase_is_3q") is True
    assert conf.get("no_production_model_candidate") is True
    assert conf.get("no_deployable_artifact") is True
    assert conf.get("confirmed") is True


def test_recommendation_and_next_phase():
    res = _load_result()
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "3-R"


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
    interp = res["interpretation"]
    assert interp["rebuilt_oos_scores_in_memory"] is True
    assert interp["full_prediction_panel_written_to_disk"] is False


# --------------------------------------------------------------------------- #
# 5. Output artifacts exist and are Git-safe
# --------------------------------------------------------------------------- #
def test_output_artifacts_exist():
    if not os.path.isdir(_Q_DIR):
        raise _Skip("output dir not present; run the runner first")
    for base in _EXPECTED_OUTPUT_BASENAMES:
        p = os.path.join(_Q_DIR, base)
        assert os.path.isfile(p), "missing output artifact %s" % base


def test_output_files_git_safe_under_50mb():
    if not os.path.isdir(_Q_DIR):
        raise _Skip("output dir not present; run the runner first")
    for name in os.listdir(_Q_DIR):
        p = os.path.join(_Q_DIR, name)
        if os.path.isfile(p):
            assert os.path.getsize(p) <= _MAX_FILE_BYTES, "%s exceeds 50 MB" % name
    if os.path.isfile(_RESULT):
        assert os.path.getsize(_RESULT) <= _MAX_FILE_BYTES


def test_no_deployable_artifact_files_written():
    if not os.path.isdir(_Q_DIR):
        raise _Skip("output dir not present; run the runner first")
    bad_ext = (".pk" + "l", ".job" + "lib", ".onn" + "x", ".h5", ".ker" + "as", ".pt", ".pth")
    for name in os.listdir(_Q_DIR):
        assert not name.lower().endswith(bad_ext), "deployable artifact present: %s" % name


# --------------------------------------------------------------------------- #
# 6. Diagnostic CSV content
# --------------------------------------------------------------------------- #
def test_bad_year_attribution_includes_2021():
    rows = _rows("year_failure_attribution.csv")
    assert rows, "year failure attribution must have rows"
    years = {str(r["year"]) for r in rows}
    assert "2021" in years, "bad-year attribution must include 2021"
    cols = set(rows[0].keys())
    for c in ("year", "mean_daily_ic", "decile_spread", "mean_ic_risk_off", "mean_ic_low_vol"):
        assert c in cols, "year failure attribution missing column %r" % c


def test_regime_performance_summary_exists():
    rows = _rows("regime_performance_summary.csv")
    assert rows, "regime performance summary must have rows"
    regimes = {r["regime"] for r in rows}
    for reg in ("risk_on", "risk_off", "high_vol", "low_vol"):
        assert reg in regimes, "regime summary missing regime %r" % reg


def test_sector_performance_summary_exists():
    rows = _rows("sector_performance_summary.csv")
    assert rows, "sector performance summary must have rows"
    cols = set(rows[0].keys())
    for c in ("sector", "top_decile_share", "top_minus_bottom_label_spread",
              "top_minus_bottom_label_spread_2021"):
        assert c in cols, "sector summary missing column %r" % c


def test_horizon_comparison_has_all_horizons():
    rows = _rows("horizon_comparison_summary.csv")
    horizons = {r["horizon"] for r in rows}
    assert _REQUIRED_HORIZONS <= horizons, "horizon comparison missing a horizon"
    cols = set(rows[0].keys())
    for c in ("mean_daily_ic", "worst_year_ic", "stability_score",
              "est_monthly_turnover_top_decile"):
        assert c in cols, "horizon comparison missing column %r" % c


def test_turnover_cost_sensitivity_has_all_bps():
    rows = _rows("turnover_cost_sensitivity.csv")
    assert rows, "turnover/cost sensitivity must have rows"
    bps = {int(r["cost_bps"]) for r in rows}
    assert _REQUIRED_COST_BPS <= bps, "turnover/cost grid missing a cost level"
    cols = set(rows[0].keys())
    for c in ("bucket", "monthly_turnover_oneway", "gross_monthly_spread", "net_monthly_spread",
              "break_even_cost_bps", "still_positive_after_cost"):
        assert c in cols, "turnover/cost missing column %r" % c
    buckets = {r["bucket"] for r in rows}
    for b in ("top_decile", "top_quintile", "top_30pct"):
        assert b in buckets, "turnover/cost missing bucket %r" % b


def test_feature_weight_stability_exists():
    rows = _rows("feature_weight_stability_summary.csv")
    assert rows, "feature weight stability must have rows"
    cols = set(rows[0].keys())
    for c in ("model", "horizon", "feature_family", "abs_weight_share", "mean_sign_stability"):
        assert c in cols, "weight stability missing column %r" % c
    models = {r["model"] for r in rows}
    assert any(m.startswith("ridge_") for m in models), "weight stability must cover a ridge model"


def test_ensemble_blend_sensitivity_exists():
    rows = _rows("ensemble_blend_sensitivity.csv")
    assert len(rows) >= 5, "must test at least five blends"
    cols = set(rows[0].keys())
    for c in ("blend", "mean_daily_ic", "worst_year_ic", "stability_score", "improves_worst_year"):
        assert c in cols, "blend sensitivity missing column %r" % c
    blends = {r["blend"] for r in rows}
    assert any("100pct" in b for b in blends), "must include the 100% base blend"


def test_improvement_priority_table_exists():
    rows = _rows("improvement_priority_table.csv")
    assert len(rows) >= 5, "improvement priority table must rank multiple workstreams"
    cols = set(rows[0].keys())
    for c in ("rank", "workstream", "expected_impact", "urgency", "blocker", "cost_effort",
              "recommended_next_phase"):
        assert c in cols, "improvement priority missing column %r" % c
    workstreams = " ".join(r["workstream"] for r in rows)
    assert "macro" in workstreams and "earnings" in workstreams and "sentiment" in workstreams


def test_readiness_decision_table_exists():
    rows = _rows("readiness_decision_table.csv")
    assert rows, "readiness decision table must have rows"
    cols = set(rows[0].keys())
    for c in ("decision_item", "value", "passed", "note"):
        assert c in cols, "readiness table missing column %r" % c


# --------------------------------------------------------------------------- #
# 7. Bad-year diagnosis is present and regime-aware
# --------------------------------------------------------------------------- #
def test_bad_year_diagnosis_block():
    res = _load_result()
    diag = res["bad_year_diagnosis"].get("diagnosis", {})
    assert diag.get("bad_year") == 2021
    assert "regime_driven" in diag
    assert "bad_year_regime_composition" in diag
    # The focus model under review is ridge_technical_only @ 126d.
    bmr = res["best_model_reviewed"]
    assert bmr["model"] == "ridge_technical_only" and bmr["horizon"] == "126d"


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
    "turnover",
    "transaction cost",
    "regime",
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
    assert "not faked" in doc or "are not faked" in doc


# --------------------------------------------------------------------------- #
# 9. Optional live end-to-end run (gated behind PHASE3Q_LIVE=1)
# --------------------------------------------------------------------------- #
def test_live_end_to_end_optional():
    if os.environ.get("PHASE3Q_LIVE") != "1":
        raise _Skip("set PHASE3Q_LIVE=1 to run the full end-to-end robustness diagnosis")
    import tempfile
    mod = _import_runner()
    if not os.path.isfile(mod.PHASE3L_PANEL_CSV):
        raise _Skip("Phase 3-L aligned panel not present in this environment")
    tmp = tempfile.mkdtemp(prefix="phase3q_")
    res = mod.run(result_json_path=os.path.join(tmp, "result.json"), o_dir=tmp, verbose=False)
    assert res["phase"] == "3-Q"
    assert res["d_drive_written"] is False
    assert res["portfolio_weights_computed"] is False
    assert res["production_predictions_computed"] is False
    assert res["production_scores_computed"] is False
    assert res["production_model_candidate_created"] is False
    assert res["deployable_model_artifact_written"] is False
    assert res["macro_faked"] is False and res["sentiment_faked"] is False
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "3-R"


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
