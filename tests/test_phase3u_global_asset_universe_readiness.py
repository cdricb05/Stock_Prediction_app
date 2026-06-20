"""Phase 3-U tests for the Global Cross-Asset ETF Proxy Universe Readiness Gate.

These tests prove the phase is a disciplined, research-only, safety-controlled, NON-faking
readiness / data-planning gate: the runner compiles, imports, and defines the required functions;
the result JSON exists with phase == "3-U"; recommended_next_phase.phase == "3-V"; every required
output CSV exists; the target universe has >= 20 proxies covering the required asset classes; the
source contains no Alpha Vantage / provider / yfinance(import) / FRED-API / DB-write / deployment /
order-trading / production-candidate / deployable-artifact tokens; the runner WRITES nothing to the
data drive and never writes a Phase 3-M path; no price data is faked; and every output file is
Git-safe (< 50 MB). A fast end-to-end run (no data-drive read) confirms Phase 3-M outputs are not
modified.

Runs two ways:
  * under pytest:   pytest tests/test_phase3u_global_asset_universe_readiness.py
  * without pytest: python tests/test_phase3u_global_asset_universe_readiness.py
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
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_RUNNER = os.path.join(_REPO_ROOT, "research", "run_phase3u_global_asset_universe_readiness.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase3u_global_asset_universe_readiness_v1.md")
_RESULT = os.path.join(_REPO_ROOT, "research", "output",
                       "phase3u_global_asset_universe_readiness.json")
_U_DIR = os.path.join(_REPO_ROOT, "research", "output",
                      "phase3u_global_asset_universe_readiness")
_M_DIR = os.path.join(_REPO_ROOT, "research", "output", "phase3m_earnings_estimates_signal_gate")


class _Skip(Exception):
    """Raised to mark a test skipped."""


# Required output artifacts (basenames).
_EXPECTED_OUTPUT_BASENAMES = (
    "target_global_asset_universe.csv",
    "local_price_coverage.csv",
    "global_asset_data_requirements.csv",
    "cross_asset_feature_blueprint.csv",
    "global_model_roadmap.csv",
    "readiness_decision_table.csv",
)

# Functions the source MUST define.
_REQUIRED_FUNCTIONS = (
    "confirm_phase3t_state", "build_target_universe", "inspect_local_price_coverage",
    "build_data_requirements", "build_feature_blueprint", "build_global_model_roadmap",
    "decide", "run",
)

# Forbidden tokens (assembled from fragments so they never self-match this test's prose). These are
# specific usage patterns, not the bare word - e.g. "yfinance_called" / "FRED API" remain allowed.
_FORBIDDEN_NETWORK_TOKENS = [
    "import " + "yf" + "inance", "yf" + "inance.", "import " + "requests", "from " + "requests",
    "alpha" + "vantage", "fin" + "nhub", "poly" + "gon.io", "iex" + "cloud",
    "urllib." + "request", "http" + "x.", "fred" + "api", "pandas_" + "datareader",
    ".download(",
]
_FORBIDDEN_INFRA_TOKENS = [
    "gcl" + "oud", "sub" + "process", "os." + "system", "para" + "miko", "system" + "ctl",
    "kube" + "ctl", "alem" + "bic", "PREDICTOR_USE_" + "MODEL_V2", "uvi" + "corn",
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
    "phase", "generated_at", "phase3t_status_summary", "target_universe_summary",
    "local_price_coverage_summary", "missing_data_summary", "feature_blueprint_summary",
    "global_model_roadmap", "readiness_decision_table", "recommendation",
    "recommended_next_phase", "interpretation",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed", "deployment_executed",
    "model_v2_enabled", "production_edge_claimed", "production_model_trained",
    "production_model_candidate_created", "deployable_model_artifact_written",
    "production_predictions_computed", "production_scores_computed", "portfolio_weights_computed",
    "order_instructions_created", "d_drive_written", "provider_api_called", "alpha_vantage_called",
    "fred_called", "paid_vendor_api_called", "yfinance_called", "data_faked",
)
_REQUIRED_TRUE = (
    "no_trading", "no_orders", "no_automation", "research_only", "labels_for_validation_only",
)
_ALLOWED_RECOMMENDATIONS = {
    "GLOBAL_ASSET_UNIVERSE_READY_FOR_FEATURE_FACTORY",
    "GLOBAL_ASSET_UNIVERSE_PARTIAL_LOCAL_COVERAGE",
    "GLOBAL_ASSET_UNIVERSE_BLOCKED_NEEDS_LOCAL_PRICE_DATA",
    "GLOBAL_ASSET_UNIVERSE_BLOCKED_INPUTS",
}
_REQUIRED_ASSET_CLASSES = ("us_equity", "rates", "credit", "commodity", "currency", "volatility")
_MAX_FILE_BYTES = 50 * 1024 * 1024


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _read_csv_rows(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _import_runner():
    spec = importlib.util.spec_from_file_location("phase3u_runner_test", _RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_result():
    if not os.path.isfile(_RESULT):
        raise _Skip("result JSON not present; run the runner first")
    return json.loads(_read(_RESULT))


def _rows(basename):
    p = os.path.join(_U_DIR, basename)
    if not os.path.isfile(p):
        raise _Skip("%s not present; run the runner first" % basename)
    return _read_csv_rows(p)


# --------------------------------------------------------------------------- #
# 1. Runner compiles, imports, defines required functions
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
# 2. Outputs under research/output (C:), and never written to the data drive
# --------------------------------------------------------------------------- #
def test_outputs_under_research_output():
    mod = _import_runner()
    assert mod._U_DIR.replace("\\", "/").endswith(
        "research/output/phase3u_global_asset_universe_readiness")
    assert mod.RESULT_JSON.replace("\\", "/").endswith(
        "research/output/phase3u_global_asset_universe_readiness.json")


def test_no_data_drive_or_phase3m_writes_in_source():
    """Static guarantee: the data drive is only ever READ, and no write helper ever targets a
    Phase 3-M path. Every write-mode open lives in the generic _write_csv / _dump_json helpers, and
    every write-helper call site references only phase3u output paths."""
    src = _read(_RUNNER)
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # All write-mode opens must be the generic helper form open(path, "w", ...).
        if "open(" in line and '"w"' in line:
            assert "open(path," in line, "write-mode open must be the generic helper: %r" % line
        # Write-helper call sites must not target a Phase 3-M path or the data drive.
        if "_write_csv(" in line or "_dump_json(" in line:
            low = line.lower()
            assert "phase3m" not in low, "write helper must not target a Phase 3-M path: %r" % line
            assert "_m_dir" not in low, "write helper must not target the Phase 3-M dir: %r" % line
            assert "d:" not in low and "data_drive" not in low, \
                "write helper must not target the data drive: %r" % line


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
# 4. Result JSON exists with phase == "3-U" and required fields
# --------------------------------------------------------------------------- #
def test_result_json_exists_and_phase():
    res = _load_result()
    assert res["phase"] == "3-U"
    for field in _REQUIRED_JSON_FIELDS:
        assert field in res, "missing result field %r" % field


def test_recommendation_and_next_phase():
    res = _load_result()
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "3-V"


def test_safety_flags():
    res = _load_result()
    for flag in _REQUIRED_FALSE:
        assert res.get(flag) is False, "%s must be False" % flag
    for flag in _REQUIRED_TRUE:
        assert res.get(flag) is True, "%s must be True" % flag


def test_nothing_faked_in_result():
    res = _load_result()
    assert res["data_faked"] is False
    assert res["recommendation"]["price_data_faked"] is False
    interp = res["interpretation"]
    assert interp["price_data_faked"] is False
    assert interp["provider_api_called"] is False
    assert interp["production_predictions_computed"] is False
    assert interp["portfolio_weights_computed"] is False


# --------------------------------------------------------------------------- #
# 5. Output artifacts exist and are Git-safe
# --------------------------------------------------------------------------- #
def test_output_artifacts_exist():
    if not os.path.isdir(_U_DIR):
        raise _Skip("output dir not present; run the runner first")
    for base in _EXPECTED_OUTPUT_BASENAMES:
        p = os.path.join(_U_DIR, base)
        assert os.path.isfile(p), "missing output artifact %s" % base


def test_output_files_git_safe_under_50mb():
    if not os.path.isdir(_U_DIR):
        raise _Skip("output dir not present; run the runner first")
    for name in os.listdir(_U_DIR):
        p = os.path.join(_U_DIR, name)
        if os.path.isfile(p):
            assert os.path.getsize(p) <= _MAX_FILE_BYTES, "%s exceeds 50 MB" % name
    if os.path.isfile(_RESULT):
        assert os.path.getsize(_RESULT) <= _MAX_FILE_BYTES


def test_no_deployable_artifact_files_written():
    if not os.path.isdir(_U_DIR):
        raise _Skip("output dir not present; run the runner first")
    bad_ext = (".pk" + "l", ".job" + "lib", ".onn" + "x", ".h5", ".ker" + "as", ".pt", ".pth")
    for name in os.listdir(_U_DIR):
        assert not name.lower().endswith(bad_ext), "deployable artifact present: %s" % name


# --------------------------------------------------------------------------- #
# 6. Target universe content
# --------------------------------------------------------------------------- #
def test_target_universe_has_at_least_20_assets():
    rows = _rows("target_global_asset_universe.csv")
    assert len(rows) >= 20, "target universe must have >= 20 proxies, got %d" % len(rows)
    cols = set(rows[0].keys())
    for c in ("ticker", "asset_class", "region", "exposure", "role_in_model", "priority", "notes"):
        assert c in cols, "universe missing column %r" % c


def test_required_asset_classes_present():
    rows = _rows("target_global_asset_universe.csv")
    classes = {r["asset_class"] for r in rows}
    for required in _REQUIRED_ASSET_CLASSES:
        assert required in classes, "universe missing required asset class %r" % required
    tickers = {r["ticker"] for r in rows}
    for required in ("SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "LQD", "HYG", "GLD", "UUP", "VIXY"):
        assert required in tickers, "universe missing required proxy %r" % required


# --------------------------------------------------------------------------- #
# 7. Coverage / requirements / blueprint / roadmap / decision content
# --------------------------------------------------------------------------- #
def test_local_price_coverage_exists():
    rows = _rows("local_price_coverage.csv")
    assert rows, "coverage table must have rows"
    cols = set(rows[0].keys())
    for c in ("ticker", "asset_class", "locally_available", "source_file", "date_min", "date_max",
              "row_count", "coverage_status", "blocker"):
        assert c in cols, "coverage missing column %r" % c


def test_data_requirements_exists():
    rows = _rows("global_asset_data_requirements.csv")
    cols = set(rows[0].keys()) if rows else set()
    if rows:
        for c in ("ticker", "asset_class", "required_fields", "min_history_start",
                  "required_frequency", "point_in_time_rule", "priority", "why_needed",
                  "acceptable_local_file_format"):
            assert c in cols, "requirements missing column %r" % c


def test_feature_blueprint_exists():
    rows = _rows("cross_asset_feature_blueprint.csv")
    assert rows, "feature blueprint must have rows"
    cols = set(rows[0].keys())
    for c in ("feature_family", "feature_name", "required_tickers", "description",
              "expected_impact", "point_in_time_safe", "dependency", "implementation_status"):
        assert c in cols, "blueprint missing column %r" % c
    families = {r["feature_family"] for r in rows}
    for required in ("equity_risk_appetite", "global_equity_breadth", "rates_duration_regime",
                     "credit_risk", "commodity_inflation", "dollar_liquidity", "volatility_risk",
                     "cross_asset_regime"):
        assert required in families, "blueprint missing feature family %r" % required


def test_roadmap_exists_with_ordered_phases():
    rows = _rows("global_model_roadmap.csv")
    phases = [r["phase"] for r in rows]
    for required in ("3-V", "3-W", "3-X", "3-Y", "3-Z", "4-A"):
        assert required in phases, "roadmap missing phase %r" % required


def test_decision_table_exists():
    rows = _rows("readiness_decision_table.csv")
    assert rows, "decision table must have rows"
    cols = set(rows[0].keys())
    for c in ("decision_item", "value", "passed", "note"):
        assert c in cols, "decision table missing column %r" % c
    items = {r["decision_item"] for r in rows}
    assert "no_price_data_faked" in items
    assert "d_drive_written" in items
    assert "phase3m_outputs_modified" in items


def test_recommendation_matches_coverage_rule():
    res = _load_result()
    cov = res["local_price_coverage_summary"]
    avail = int(cov["locally_available_count"])
    classes = int(cov["asset_classes_covered_locally_count"])
    rec = res["recommendation"]["recommendation"]
    if avail < 5:
        assert rec == "GLOBAL_ASSET_UNIVERSE_BLOCKED_NEEDS_LOCAL_PRICE_DATA"
    elif avail >= 12 and classes >= 5:
        assert rec == "GLOBAL_ASSET_UNIVERSE_READY_FOR_FEATURE_FACTORY"
    else:
        assert rec == "GLOBAL_ASSET_UNIVERSE_PARTIAL_LOCAL_COVERAGE"


# --------------------------------------------------------------------------- #
# 8. Documentation carries the required guardrail phrases
# --------------------------------------------------------------------------- #
_REQUIRED_DOC_PHRASES = [
    "does not deploy",
    "does not run migrations",
    "does not write to production DB",
    "does not trade",
    "production edge",
    "readiness",
    "cross-asset",
    "point-in-time",
    "GLOBAL_ASSET_UNIVERSE_BLOCKED_NEEDS_LOCAL_PRICE_DATA",
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
    assert "not faked" in doc or "never faked" in doc or "are not faked" in doc


# --------------------------------------------------------------------------- #
# 9. Fast end-to-end run (no data-drive read) proves Phase 3-M is not modified
# --------------------------------------------------------------------------- #
def _snapshot(dir_path):
    snap = {}
    if not os.path.isdir(dir_path):
        return snap
    for dp, _dn, files in os.walk(dir_path):
        for n in files:
            p = os.path.join(dp, n)
            try:
                st = os.stat(p)
                snap[p] = (st.st_size, st.st_mtime_ns)
            except OSError:
                pass
    return snap


def test_run_does_not_modify_phase3m_outputs():
    mod = _import_runner()
    before = _snapshot(_M_DIR)
    tmp = tempfile.mkdtemp(prefix="phase3u_")
    # No data-drive read (panel path forced absent) and no repo scan -> fast, 0 proxies available.
    res = mod.run(result_json_path=os.path.join(tmp, "result.json"), o_dir=tmp,
                  data_drive_panel=os.path.join(tmp, "no_such_panel.csv"), scan_roots=[],
                  verbose=False)
    after = _snapshot(_M_DIR)
    assert before == after, "Phase 3-M outputs must not be modified by Phase 3-U"
    assert res["phase"] == "3-U"
    assert res["recommended_next_phase"]["phase"] == "3-V"
    assert res["d_drive_read"] is False
    assert res["d_drive_written"] is False
    assert res["data_faked"] is False
    assert res["portfolio_weights_computed"] is False
    assert res["production_predictions_computed"] is False
    assert res["production_model_candidate_created"] is False
    assert res["deployable_model_artifact_written"] is False
    # With nothing available, the gate must recommend acquiring local price data.
    assert res["recommendation"]["recommendation"] == \
        "GLOBAL_ASSET_UNIVERSE_BLOCKED_NEEDS_LOCAL_PRICE_DATA"


# --------------------------------------------------------------------------- #
# 10. Optional live end-to-end run against the real inputs (gated by PHASE3U_LIVE=1)
# --------------------------------------------------------------------------- #
def test_live_end_to_end_optional():
    if os.environ.get("PHASE3U_LIVE") != "1":
        raise _Skip("set PHASE3U_LIVE=1 to run the full end-to-end readiness gate")
    mod = _import_runner()
    before = _snapshot(_M_DIR)
    tmp = tempfile.mkdtemp(prefix="phase3u_live_")
    res = mod.run(result_json_path=os.path.join(tmp, "result.json"), o_dir=tmp, verbose=False)
    after = _snapshot(_M_DIR)
    assert before == after, "Phase 3-M outputs must not be modified by Phase 3-U"
    assert res["phase"] == "3-U"
    assert res["recommended_next_phase"]["phase"] == "3-V"
    assert res["d_drive_written"] is False
    assert res["provider_api_called"] is False
    assert res["alpha_vantage_called"] is False
    assert res["yfinance_called"] is False
    assert res["fred_called"] is False
    assert res["portfolio_weights_computed"] is False
    assert res["production_predictions_computed"] is False
    assert res["data_faked"] is False
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS


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
