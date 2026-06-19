"""Phase 3-O tests for the Multi-Signal Feature Factory + Research Baseline Model Gate.

These tests prove the phase is a disciplined, research-only, safety-controlled build: the
analyzer compiles and imports; it references the expected committed inputs and reads the D:
price panel READ ONLY (writing nothing to D:); the result JSON exists with phase == "3-O";
the feature registry exists and includes every required family; macro_inflation and sentiment
appear in the registry but are NOT faked (implemented == false with an external_*_data_required
blocker); seasonality, market-regime, sector-relative, and ARIMA-style/time-series features
exist; a baseline-model scoreboard exists; the IC summary uses the cross-sectional daily rank-IC
methodology; the source contains no Alpha Vantage / provider / yfinance / DB-write / deployment /
order-trading / production-candidate / deployable-artifact / D:-write tokens; every output file
is Git-safe (< 50 MB); the safety flags are correct; and recommended_next_phase.phase == "3-P".

Runs two ways:
  * under pytest:   pytest tests/test_phase3o_multisignal_feature_factory.py
  * without pytest: python tests/test_phase3o_multisignal_feature_factory.py
The __main__ block discovers every test_* function, runs it, prints PASS/FAIL/SKIP, and exits
non-zero on any failure.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_ANALYZER = os.path.join(_REPO_ROOT, "research", "run_phase3o_multisignal_feature_factory.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase3o_multisignal_feature_factory_v1.md")
_RESULT = os.path.join(_REPO_ROOT, "research", "output", "phase3o_multisignal_feature_factory.json")
_O_DIR = os.path.join(_REPO_ROOT, "research", "output", "phase3o_multisignal_feature_factory")


class _Skip(Exception):
    """Raised to mark a test skipped."""


# Committed inputs this phase must reference (path fragments).
_EXPECTED_INPUT_FRAGMENTS = (
    "phase2k_g_expanded_price_history_free.csv",
    "phase2k_p_sector_map_current.csv",
    "phase3l_sec_universe_signal_gate",
    "aligned_feature_price_panel_universe.csv",
    "phase3m_earnings_estimates_signal_gate",
    "collection_progress.json",
)

# Required output artifacts (basenames).
_EXPECTED_OUTPUT_BASENAMES = (
    "feature_registry.csv",
    "research_feature_panel_sample.csv",
    "technical_feature_summary.csv",
    "seasonality_feature_summary.csv",
    "market_regime_feature_summary.csv",
    "sector_relative_feature_summary.csv",
    "fundamental_feature_summary.csv",
    "earnings_feature_summary.csv",
    "time_series_feature_summary.csv",
    "feature_ic_summary.csv",
    "feature_family_ic_summary.csv",
    "baseline_model_scoreboard.csv",
    "readiness_decision_table.csv",
)

_REQUIRED_FAMILIES = (
    "technical_price", "seasonality_calendar", "market_regime", "sector_relative",
    "sec_fundamental", "earnings_surprise", "macro_inflation", "sentiment",
    "time_series_arima_style",
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
    "import " + "joblib", ".pk" + "l", ".job" + "lib",
]
_FORBIDDEN_IMPORT_ROOTS = {
    "sklearn", "tensorflow", "torch", "keras", "lightgbm", "xgboost",
    "api_server", "yfinance", "requests", "urllib",
}

_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read", "outputs_written", "input_confirmation",
    "implemented_feature_families", "blocked_feature_families", "feature_count_by_family",
    "rows_in_feature_panel", "tickers_in_feature_panel", "date_range", "best_feature_families",
    "ic_methodology", "feature_ic_summary", "feature_family_ic_summary",
    "baseline_model_scoreboard", "baseline_model_scoreboard_summary", "macro_inflation_status",
    "sentiment_status", "arima_style_status", "recommendation", "recommended_next_phase",
    "interpretation",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed", "deployment_executed",
    "model_v2_enabled", "production_edge_claimed", "production_model_trained",
    "production_model_candidate_created", "deployable_model_artifact_written", "d_drive_written",
    "provider_api_called", "paid_vendor_api_called", "alpha_vantage_called", "sentiment_faked",
    "macro_faked", "production_predictions_computed", "portfolio_weights_computed",
)
_REQUIRED_TRUE = (
    "no_trading", "no_orders", "no_automation", "d_drive_read", "labels_for_validation_only",
)
_ALLOWED_RECOMMENDATIONS = {
    "MULTISIGNAL_FEATURE_FACTORY_SUCCESS_READY_FOR_RESEARCH_MODEL",
    "MULTISIGNAL_FEATURE_FACTORY_PARTIAL_MACRO_SENTIMENT_MISSING",
    "MULTISIGNAL_FEATURE_FACTORY_BLOCKED_INPUTS",
    "MULTISIGNAL_FEATURE_FACTORY_FAILS_SIGNAL_CHECK",
}
_REQUIRED_DOC_PHRASES = [
    "does not deploy",
    "does not restart stock-api.service",
    "does not enable",
    "does not run migrations",
    "does not write to production DB",
    "does not trade",
    "production edge",
]
_MAX_FILE_BYTES = 50 * 1024 * 1024


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _read_csv_rows(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _import_analyzer():
    spec = importlib.util.spec_from_file_location("phase3o_analyzer_test", _ANALYZER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_result():
    if not os.path.isfile(_RESULT):
        raise _Skip("result JSON not present; run the analyzer first")
    return json.loads(_read(_RESULT))


# --------------------------------------------------------------------------- #
# 1. Analyzer compiles and imports
# --------------------------------------------------------------------------- #
def test_analyzer_compiles():
    compile(_read(_ANALYZER), _ANALYZER, "exec")


def test_analyzer_imports():
    _import_analyzer()


# --------------------------------------------------------------------------- #
# 2. Expected inputs referenced; outputs under research/output (C:)
# --------------------------------------------------------------------------- #
def test_expected_inputs_referenced():
    src = _read(_ANALYZER)
    for frag in _EXPECTED_INPUT_FRAGMENTS:
        assert frag in src, "analyzer must reference input fragment %r" % frag


def test_outputs_under_research_output_on_c_drive():
    mod = _import_analyzer()
    assert mod._O_DIR.replace("\\", "/").endswith(
        "research/output/phase3o_multisignal_feature_factory")
    assert mod.RESULT_JSON.replace("\\", "/").endswith(
        "research/output/phase3o_multisignal_feature_factory.json")
    # Outputs must NOT be on the D: drive.
    assert os.path.splitdrive(os.path.abspath(mod._O_DIR))[0].upper() != "D:"


# --------------------------------------------------------------------------- #
# 3. Reads D: price panel read-only; never writes D:
# --------------------------------------------------------------------------- #
def test_price_panel_is_on_d_and_only_read():
    mod = _import_analyzer()
    assert os.path.splitdrive(os.path.abspath(mod.PRICE_CSV))[0].upper() == "D:"
    src = _read(_ANALYZER)
    # The only access to the D: price path is via pandas.read_csv / os.path.isfile / splitdrive.
    for write_token in ("to_csv(", "open(" + "PRICE", ".to_parquet("):
        assert write_token not in src, "must not write the D: price panel (%r)" % write_token


# --------------------------------------------------------------------------- #
# 4. No forbidden tokens / imports in the source
# --------------------------------------------------------------------------- #
def test_no_forbidden_tokens():
    src = _read(_ANALYZER).lower()
    for token in (_FORBIDDEN_NETWORK_TOKENS + _FORBIDDEN_INFRA_TOKENS + _FORBIDDEN_DB_TOKENS +
                  _FORBIDDEN_DEPLOY_TRADE_TOKENS + _FORBIDDEN_ARTIFACT_TOKENS):
        assert token.lower() not in src, "forbidden token present: %r" % token


def test_no_forbidden_imports():
    import ast
    tree = ast.parse(_read(_ANALYZER))
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
# 5. Result JSON exists with phase == "3-O" and required fields
# --------------------------------------------------------------------------- #
def test_result_json_exists_and_phase():
    res = _load_result()
    assert res["phase"] == "3-O"
    for field in _REQUIRED_JSON_FIELDS:
        assert field in res, "missing result field %r" % field


def test_recommendation_and_next_phase():
    res = _load_result()
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "3-P"


def test_safety_flags():
    res = _load_result()
    for flag in _REQUIRED_FALSE:
        assert res.get(flag) is False, "%s must be False" % flag
    for flag in _REQUIRED_TRUE:
        assert res.get(flag) is True, "%s must be True" % flag


# --------------------------------------------------------------------------- #
# 6. Output artifacts exist and are Git-safe
# --------------------------------------------------------------------------- #
def test_output_artifacts_exist():
    if not os.path.isdir(_O_DIR):
        raise _Skip("output dir not present; run the analyzer first")
    for base in _EXPECTED_OUTPUT_BASENAMES:
        p = os.path.join(_O_DIR, base)
        assert os.path.isfile(p), "missing output artifact %s" % base


def test_output_files_git_safe_under_50mb():
    if not os.path.isdir(_O_DIR):
        raise _Skip("output dir not present; run the analyzer first")
    for name in os.listdir(_O_DIR):
        p = os.path.join(_O_DIR, name)
        if os.path.isfile(p):
            assert os.path.getsize(p) <= _MAX_FILE_BYTES, "%s exceeds 50 MB" % name
    if os.path.isfile(_RESULT):
        assert os.path.getsize(_RESULT) <= _MAX_FILE_BYTES


# --------------------------------------------------------------------------- #
# 7. Feature registry includes all required families; macro/sentiment not faked
# --------------------------------------------------------------------------- #
def test_feature_registry_has_all_families():
    p = os.path.join(_O_DIR, "feature_registry.csv")
    if not os.path.isfile(p):
        raise _Skip("registry not present; run the analyzer first")
    rows = _read_csv_rows(p)
    families = {r["feature_family"] for r in rows}
    for fam in _REQUIRED_FAMILIES:
        assert fam in families, "registry missing family %r" % fam


def test_macro_in_registry_and_not_faked():
    p = os.path.join(_O_DIR, "feature_registry.csv")
    if not os.path.isfile(p):
        raise _Skip("registry not present; run the analyzer first")
    rows = _read_csv_rows(p)
    macro = [r for r in rows if r["feature_family"] == "macro_inflation"]
    assert macro, "macro_inflation must be in the registry"
    for r in macro:
        assert r["implemented"].lower() == "false", "macro must not be implemented (not faked)"
        assert r["blocker"] == "external_macro_data_required"
    res = _load_result()
    assert res["macro_inflation_status"]["implemented"] is False
    assert res["macro_inflation_status"]["faked"] is False
    assert res["macro_faked"] is False


def test_sentiment_in_registry_and_not_faked():
    p = os.path.join(_O_DIR, "feature_registry.csv")
    if not os.path.isfile(p):
        raise _Skip("registry not present; run the analyzer first")
    rows = _read_csv_rows(p)
    sent = [r for r in rows if r["feature_family"] == "sentiment"]
    assert sent, "sentiment must be in the registry"
    for r in sent:
        assert r["implemented"].lower() == "false", "sentiment must not be implemented (not faked)"
        assert r["blocker"] == "external_sentiment_data_required"
    res = _load_result()
    assert res["sentiment_status"]["implemented"] is False
    assert res["sentiment_status"]["faked"] is False
    assert res["sentiment_faked"] is False


def test_no_future_data_flag_in_registry():
    p = os.path.join(_O_DIR, "feature_registry.csv")
    if not os.path.isfile(p):
        raise _Skip("registry not present; run the analyzer first")
    rows = _read_csv_rows(p)
    for r in rows:
        assert r["uses_future_data"].lower() == "false", \
            "%s must not use future data" % r["feature_name"]


# --------------------------------------------------------------------------- #
# 8. Implemented families produce features (seasonality / regime / sector / ARIMA-style)
# --------------------------------------------------------------------------- #
def _summary_has_rows(basename):
    p = os.path.join(_O_DIR, basename)
    if not os.path.isfile(p):
        raise _Skip("%s not present; run the analyzer first" % basename)
    return _read_csv_rows(p)


def test_seasonality_features_exist():
    rows = _summary_has_rows("seasonality_feature_summary.csv")
    feats = {r["feature"] for r in rows}
    for f in ("month_of_year", "historical_same_month_avg_return_by_ticker",
              "historical_month_rank_by_ticker"):
        assert f in feats, "seasonality feature %r missing" % f


def test_market_regime_features_exist():
    rows = _summary_has_rows("market_regime_feature_summary.csv")
    feats = {r["feature"] for r in rows}
    for f in ("spy_return_63d", "market_risk_off_flag", "cross_sectional_breadth_21d"):
        assert f in feats, "market regime feature %r missing" % f


def test_sector_relative_features_exist():
    rows = _summary_has_rows("sector_relative_feature_summary.csv")
    feats = {r["feature"] for r in rows}
    for f in ("ticker_return_21d_minus_sector_return_21d", "ticker_rank_within_sector_21d"):
        assert f in feats, "sector-relative feature %r missing" % f


def test_arima_style_timeseries_features_exist():
    rows = _summary_has_rows("time_series_feature_summary.csv")
    feats = {r["feature"] for r in rows}
    for f in ("ar1_beta_63d", "rolling_mean_reversion_signal_21d", "trend_persistence_63d"):
        assert f in feats, "time-series/ARIMA-style feature %r missing" % f
    res = _load_result()
    assert res["arima_style_status"]["implemented"] is True
    assert res["arima_style_status"]["statsmodels_required"] is False


# --------------------------------------------------------------------------- #
# 9. IC summary uses cross-sectional rank-IC; baseline scoreboard exists
# --------------------------------------------------------------------------- #
def test_feature_ic_summary_methodology():
    res = _load_result()
    meth = res["ic_methodology"]["ic_type"].lower()
    assert "cross-sectional" in meth and "rank" in meth
    rows = _summary_has_rows("feature_ic_summary.csv")
    assert rows, "feature IC summary must have rows"
    cols = set(rows[0].keys())
    for c in ("feature", "feature_family", "horizon", "mean_rank_ic", "ic_hit_rate"):
        assert c in cols, "feature IC summary missing column %r" % c


def test_baseline_scoreboard_exists_with_required_models():
    rows = _summary_has_rows("baseline_model_scoreboard.csv")
    models = {r["model"] for r in rows}
    for m in ("benchmark_spy", "equal_weight_universe", "momentum_rank_composite",
              "seasonality_rank_composite", "market_regime_adjusted_momentum",
              "sector_neutral_momentum", "ar_style_mean_reversion",
              "sec_fundamental_rank_composite", "combined_multisignal_rank_composite"):
        assert m in models, "scoreboard missing model %r" % m
    cols = set(rows[0].keys())
    for c in ("horizon", "sample_rows", "sample_tickers", "mean_forward_excess_return",
              "information_coefficient", "top_decile_minus_bottom_decile_spread", "hit_rate",
              "annual_coverage_years"):
        assert c in cols, "scoreboard missing metric column %r" % c


# --------------------------------------------------------------------------- #
# 10. Documentation carries the required guardrail phrases
# --------------------------------------------------------------------------- #
def test_doc_required_phrases():
    if not os.path.isfile(_DOC):
        raise _Skip("doc not present")
    doc = _read(_DOC)
    for phrase in _REQUIRED_DOC_PHRASES:
        assert phrase in doc, "doc missing required guardrail phrase %r" % phrase


def test_doc_explains_macro_and_sentiment_not_faked():
    if not os.path.isfile(_DOC):
        raise _Skip("doc not present")
    doc = _read(_DOC).lower()
    assert "external_macro_data_required" in doc
    assert "external_sentiment_data_required" in doc
    assert "not faked" in doc or "are not faked" in doc


# --------------------------------------------------------------------------- #
# 11. Optional live end-to-end run (gated behind PHASE3O_LIVE=1)
# --------------------------------------------------------------------------- #
def test_live_end_to_end_optional():
    if os.environ.get("PHASE3O_LIVE") != "1":
        raise _Skip("set PHASE3O_LIVE=1 to run the full end-to-end factory")
    import tempfile
    mod = _import_analyzer()
    if not os.path.isfile(mod.PHASE3L_PANEL_CSV):
        raise _Skip("Phase 3-L aligned panel not present in this environment")
    tmp = tempfile.mkdtemp(prefix="phase3o_")
    res = mod.run(result_json_path=os.path.join(tmp, "result.json"), o_dir=tmp, verbose=False)
    assert res["phase"] == "3-O"
    assert res["d_drive_written"] is False
    assert res["portfolio_weights_computed"] is False
    assert res["production_model_candidate_created"] is False
    assert res["deployable_model_artifact_written"] is False
    assert res["macro_faked"] is False and res["sentiment_faked"] is False
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "3-P"


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
