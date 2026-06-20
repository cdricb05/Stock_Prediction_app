"""Phase 3-S tests for the Earnings Coverage + Sentiment/Analyst Readiness & Prioritization Gate.

These tests prove the phase is a disciplined, research-only, safety-controlled, NON-faking
readiness gate: the runner compiles and imports; it confirms Phase 3-R (phase == "3-R",
WEAK/IMPROVES recommendation, next phase 3-S, no production artifacts, nothing faked); the result
JSON exists with phase == "3-S"; the Phase 3-R confirmation block is present; recommended_next_
phase.phase == "3-T"; every required output CSV exists (coverage, collection plan, sentiment
inventory, analyst-revision inventory, priority table, roadmap, decision table); if cached
earnings tickers < 75 the recommendation is CONTINUE_EARNINGS_COLLECTION; the source contains no
Alpha Vantage / provider / yfinance / FRED-API / DB-write / deployment / order-trading /
production-candidate / deployable-artifact (pickle/joblib/serialized model) / D:-write tokens; no
sentiment, analyst-revision, or earnings data is faked; and every output file is Git-safe (<50 MB).

Runs two ways:
  * under pytest:   pytest tests/test_phase3s_event_signal_readiness_gate.py
  * without pytest: python tests/test_phase3s_event_signal_readiness_gate.py
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

_RUNNER = os.path.join(_REPO_ROOT, "research", "run_phase3s_event_signal_readiness_gate.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase3s_event_signal_readiness_gate_v1.md")
_RESULT = os.path.join(_REPO_ROOT, "research", "output",
                       "phase3s_event_signal_readiness_gate.json")
_S_DIR = os.path.join(_REPO_ROOT, "research", "output", "phase3s_event_signal_readiness_gate")


class _Skip(Exception):
    """Raised to mark a test skipped."""


# Required output artifacts (basenames).
_EXPECTED_OUTPUT_BASENAMES = (
    "earnings_coverage_status.csv",
    "earnings_next_collection_plan.csv",
    "sentiment_data_inventory.csv",
    "analyst_revision_data_inventory.csv",
    "event_signal_priority_table.csv",
    "model_improvement_roadmap.csv",
    "readiness_decision_table.csv",
)

# Functions the source MUST define.
_REQUIRED_FUNCTIONS = (
    "confirm_phase3r", "earnings_coverage_status", "earnings_next_collection_plan",
    "scan_event_signal_inventory", "event_signal_data_requirements", "build_priority_table",
    "build_roadmap", "decide", "run",
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
    "phase", "generated_at", "phase3r_confirmation", "earnings_coverage_summary",
    "earnings_next_collection_plan", "sentiment_inventory_summary",
    "analyst_revision_inventory_summary", "blocked_event_signal_families",
    "priority_decision", "model_improvement_roadmap", "readiness_decision_table",
    "recommendation", "recommended_next_phase", "interpretation",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed", "deployment_executed",
    "model_v2_enabled", "production_edge_claimed", "production_model_trained",
    "production_model_candidate_created", "deployable_model_artifact_written",
    "production_predictions_computed", "production_scores_computed", "portfolio_weights_computed",
    "order_instructions_created", "d_drive_read", "d_drive_written", "provider_api_called",
    "alpha_vantage_called", "paid_vendor_api_called", "fred_called", "macro_faked",
    "sentiment_faked", "analyst_revision_faked", "earnings_faked",
)
_REQUIRED_TRUE = (
    "no_trading", "no_orders", "no_automation", "research_only", "labels_for_validation_only",
)
_ALLOWED_RECOMMENDATIONS = {
    "EVENT_SIGNAL_READINESS_CONTINUE_EARNINGS_COLLECTION",
    "EVENT_SIGNAL_READINESS_ACQUIRE_SENTIMENT_OR_ANALYST_DATA",
    "EVENT_SIGNAL_READINESS_READY_FOR_GLOBAL_ASSET_EXPANSION",
    "EVENT_SIGNAL_READINESS_BLOCKED_INPUTS",
}
_BLOCKED_FAMILIES = ("news_sentiment", "analyst_revision", "analyst_rating",
                     "target_price_revision")
_MAX_FILE_BYTES = 50 * 1024 * 1024


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _read_csv_rows(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _import_runner():
    spec = importlib.util.spec_from_file_location("phase3s_runner_test", _RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_result():
    if not os.path.isfile(_RESULT):
        raise _Skip("result JSON not present; run the runner first")
    return json.loads(_read(_RESULT))


def _rows(basename):
    p = os.path.join(_S_DIR, basename)
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
# 2. Outputs under research/output (C:), never D:
# --------------------------------------------------------------------------- #
def test_outputs_under_research_output_on_c_drive():
    mod = _import_runner()
    assert mod._S_DIR.replace("\\", "/").endswith(
        "research/output/phase3s_event_signal_readiness_gate")
    assert mod.RESULT_JSON.replace("\\", "/").endswith(
        "research/output/phase3s_event_signal_readiness_gate.json")
    assert os.path.splitdrive(os.path.abspath(mod._S_DIR))[0].upper() != "D:"


def test_no_d_drive_paths_in_source():
    src = _read(_RUNNER)
    for token in ("D:\\", "D:/", 'splitdrive'):
        # The runner must not reference the D: drive at all (no read, no write).
        if token in ("D:\\", "D:/"):
            assert token not in src, "runner must not reference the D: drive (%r)" % token


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
# 4. Result JSON exists with phase == "3-S" and required fields
# --------------------------------------------------------------------------- #
def test_result_json_exists_and_phase():
    res = _load_result()
    assert res["phase"] == "3-S"
    for field in _REQUIRED_JSON_FIELDS:
        assert field in res, "missing result field %r" % field


def test_phase3r_confirmation_present():
    res = _load_result()
    conf = res["phase3r_confirmation"]
    assert conf.get("phase_is_3r") is True
    assert conf.get("next_phase_is_3s") is True
    assert conf.get("macro_not_faked") is True
    assert conf.get("sentiment_not_faked") is True
    assert conf.get("no_production_model_candidate") is True
    assert conf.get("confirmed") is True


def test_recommendation_and_next_phase():
    res = _load_result()
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "3-T"


def test_safety_flags():
    res = _load_result()
    for flag in _REQUIRED_FALSE:
        assert res.get(flag) is False, "%s must be False" % flag
    for flag in _REQUIRED_TRUE:
        assert res.get(flag) is True, "%s must be True" % flag


def test_nothing_faked_in_result():
    res = _load_result()
    assert res["sentiment_faked"] is False
    assert res["analyst_revision_faked"] is False
    assert res["earnings_faked"] is False
    rec = res["recommendation"]
    assert rec["sentiment_faked"] is False
    assert rec["analyst_revision_faked"] is False
    assert rec["earnings_faked"] is False
    interp = res["interpretation"]
    assert interp["sentiment_family_faked"] is False
    assert interp["analyst_revision_family_faked"] is False
    assert interp["earnings_family_faked"] is False
    assert interp["provider_api_called"] is False
    assert interp["provider_api_key_value_read"] is False


# --------------------------------------------------------------------------- #
# 5. Output artifacts exist and are Git-safe
# --------------------------------------------------------------------------- #
def test_output_artifacts_exist():
    if not os.path.isdir(_S_DIR):
        raise _Skip("output dir not present; run the runner first")
    for base in _EXPECTED_OUTPUT_BASENAMES:
        p = os.path.join(_S_DIR, base)
        assert os.path.isfile(p), "missing output artifact %s" % base


def test_output_files_git_safe_under_50mb():
    if not os.path.isdir(_S_DIR):
        raise _Skip("output dir not present; run the runner first")
    for name in os.listdir(_S_DIR):
        p = os.path.join(_S_DIR, name)
        if os.path.isfile(p):
            assert os.path.getsize(p) <= _MAX_FILE_BYTES, "%s exceeds 50 MB" % name
    if os.path.isfile(_RESULT):
        assert os.path.getsize(_RESULT) <= _MAX_FILE_BYTES


def test_no_deployable_artifact_files_written():
    if not os.path.isdir(_S_DIR):
        raise _Skip("output dir not present; run the runner first")
    bad_ext = (".pk" + "l", ".job" + "lib", ".onn" + "x", ".h5", ".ker" + "as", ".pt", ".pth")
    for name in os.listdir(_S_DIR):
        assert not name.lower().endswith(bad_ext), "deployable artifact present: %s" % name


# --------------------------------------------------------------------------- #
# 6. Earnings coverage status + collection plan content
# --------------------------------------------------------------------------- #
def test_earnings_coverage_status_exists():
    rows = _rows("earnings_coverage_status.csv")
    assert rows, "earnings coverage status must have a row"
    cols = set(rows[0].keys())
    for c in ("universe_ticker_count", "cached_ticker_count", "missing_ticker_count",
              "signal_gate_min_tickers", "signal_gate_allowed_now", "next_safe_collection_action"):
        assert c in cols, "coverage status missing column %r" % c


def test_collection_plan_exists_and_no_provider_call_from_gate():
    rows = _rows("earnings_next_collection_plan.csv")
    assert rows, "collection plan must have at least one action"
    cols = set(rows[0].keys())
    for c in ("action_order", "action", "requires_api_key", "uses_provider_call",
              "safe_to_run_today", "expected_new_tickers", "commit_after_success"):
        assert c in cols, "collection plan missing column %r" % c
    # The first inspection action must not require a provider call.
    first = rows[0]
    assert str(first.get("uses_provider_call")).lower() == "false", \
        "the inspection action must not use a provider call"


def test_recommendation_matches_coverage_rule():
    res = _load_result()
    cov = res["earnings_coverage_summary"]
    cached = int(cov["cached_ticker_count"])
    min_tickers = int(cov["signal_gate_min_tickers"])
    rec = res["recommendation"]["recommendation"]
    if cached < min_tickers:
        assert rec == "EVENT_SIGNAL_READINESS_CONTINUE_EARNINGS_COLLECTION", \
            "cached %d < %d must recommend CONTINUE_EARNINGS_COLLECTION" % (cached, min_tickers)


# --------------------------------------------------------------------------- #
# 7. Sentiment / analyst inventories + blocked families (no faking)
# --------------------------------------------------------------------------- #
def test_sentiment_inventory_exists():
    p = os.path.join(_S_DIR, "sentiment_data_inventory.csv")
    if not os.path.isfile(p):
        raise _Skip("sentiment inventory not present; run the runner first")
    with open(p, "r", encoding="utf-8", newline="") as f:
        header = f.readline()
    for c in ("file_path", "detected_family", "usable", "blocker"):
        assert c in header, "sentiment inventory missing column %r" % c


def test_analyst_inventory_exists():
    p = os.path.join(_S_DIR, "analyst_revision_data_inventory.csv")
    if not os.path.isfile(p):
        raise _Skip("analyst inventory not present; run the runner first")
    with open(p, "r", encoding="utf-8", newline="") as f:
        header = f.readline()
    for c in ("file_path", "detected_family", "usable", "blocker"):
        assert c in header, "analyst inventory missing column %r" % c


def test_blocked_families_when_no_usable_data():
    res = _load_result()
    s_usable = res["sentiment_inventory_summary"]["any_usable"]
    a_usable = res["analyst_revision_inventory_summary"]["any_usable"]
    blocked = set(res["blocked_event_signal_families"])
    if not s_usable:
        assert "news_sentiment" in blocked
    if not a_usable:
        assert "analyst_revision" in blocked
    # When neither exists, all four families are blocked and requirements are documented.
    if not s_usable and not a_usable:
        for fam in _BLOCKED_FAMILIES:
            assert fam in blocked, "family %r must be blocked when no usable data exists" % fam
        reqs = res["event_signal_data_requirements"]
        assert reqs["required_columns"], "exact data requirements must be documented"
        assert reqs["recommended_local_sources_described_only"], "sources must be described"


def test_no_usable_sentiment_or_analyst_rows_are_faked():
    # Any row marked usable must carry a real ticker + value column + dates (not fabricated).
    for base in ("sentiment_data_inventory.csv", "analyst_revision_data_inventory.csv"):
        p = os.path.join(_S_DIR, base)
        if not os.path.isfile(p):
            continue
        for r in _read_csv_rows(p):
            if str(r.get("usable")).lower() == "true":
                assert str(r.get("ticker_column_detected")).lower() == "true"
                assert str(r.get("sentiment_or_revision_column_detected")).lower() == "true"
                assert r.get("date_min"), "usable row must have a real date range"


# --------------------------------------------------------------------------- #
# 8. Priority table + roadmap content
# --------------------------------------------------------------------------- #
def test_priority_table_exists():
    rows = _rows("event_signal_priority_table.csv")
    assert rows, "priority table must have rows"
    cols = set(rows[0].keys())
    for c in ("priority_rank", "workstream", "expected_impact", "urgency", "dependency",
              "estimated_effort", "cost_risk", "why_now", "recommended_next_phase"):
        assert c in cols, "priority table missing column %r" % c
    workstreams = {r["workstream"] for r in rows}
    for required in ("continue_earnings_coverage_to_75_tickers", "acquire_analyst_revision_data",
                     "acquire_news_sentiment_data", "add_global_cross_asset_etf_proxy_universe"):
        assert required in workstreams, "priority table missing workstream %r" % required


def test_roadmap_exists_with_ordered_phases():
    rows = _rows("model_improvement_roadmap.csv")
    assert rows, "roadmap must have rows"
    phases = [r["phase"] for r in rows]
    for required in ("3-T", "3-U", "3-V", "3-W", "3-X", "3-Y"):
        assert required in phases, "roadmap missing phase %r" % required


def test_decision_table_exists():
    rows = _rows("readiness_decision_table.csv")
    assert rows, "decision table must have rows"
    cols = set(rows[0].keys())
    for c in ("decision_item", "value", "passed", "note"):
        assert c in cols, "decision table missing column %r" % c
    items = {r["decision_item"] for r in rows}
    assert "no_sentiment_data_faked" in items
    assert "no_earnings_data_faked" in items
    assert "phase3r_confirmed" in items


# --------------------------------------------------------------------------- #
# 9. Documentation carries the required guardrail phrases
# --------------------------------------------------------------------------- #
_REQUIRED_DOC_PHRASES = [
    "does not deploy",
    "does not run migrations",
    "does not write to production DB",
    "does not trade",
    "production edge",
    "readiness",
    "sentiment",
    "analyst",
    "earnings coverage",
    "point-in-time",
    "EVENT_SIGNAL_READINESS_CONTINUE_EARNINGS_COLLECTION",
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
# 10. Optional live end-to-end run (gated behind PHASE3S_LIVE=1)
# --------------------------------------------------------------------------- #
def test_live_end_to_end_optional():
    if os.environ.get("PHASE3S_LIVE") != "1":
        raise _Skip("set PHASE3S_LIVE=1 to run the full end-to-end readiness gate")
    import tempfile
    mod = _import_runner()
    tmp = tempfile.mkdtemp(prefix="phase3s_")
    res = mod.run(result_json_path=os.path.join(tmp, "result.json"), o_dir=tmp, verbose=False)
    assert res["phase"] == "3-S"
    assert res["d_drive_read"] is False
    assert res["d_drive_written"] is False
    assert res["provider_api_called"] is False
    assert res["alpha_vantage_called"] is False
    assert res["portfolio_weights_computed"] is False
    assert res["production_predictions_computed"] is False
    assert res["production_scores_computed"] is False
    assert res["production_model_candidate_created"] is False
    assert res["deployable_model_artifact_written"] is False
    assert res["sentiment_faked"] is False and res["analyst_revision_faked"] is False
    assert res["earnings_faked"] is False
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "3-T"


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
