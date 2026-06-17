"""Phase 2K-D tests for the Data Expansion / Universe Broadening Plan analyzer.

These tests prove the analyzer produces a disciplined data-expansion plan in response
to the Phase 2L-B walk-forward result without continuing model research or creating a
model candidate: it compiles and imports without side effects; it references only the
upstream Phase 2L-B / 2L-A / 2K-C / 2K-A JSON summaries and the Phase 2G run summary;
it writes only research/output/phase2k_d_data_expansion_plan.json; it never imports
api_server or Paper Trader; its source contains no deploy / gcloud / SSH / service /
DB-write / migration logic and no model-training / fitting tokens; and the planning
JSON it produces reads the Phase 2L-B result, carries every required field and safety
flag, decides continue_model_research_now == false and create_model_candidate_now ==
false, defines the gap analysis / expanded-dataset requirements / target validation
capacity / data-quality gates / implementation options, and routes to Phase 2K-E. The
companion doc must contain the guardrail phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase2k_d_data_expansion_plan.py
  * without pytest: python tests/test_phase2k_d_data_expansion_plan.py
The __main__ block discovers every test_* function, runs it, prints PASS/FAIL,
and exits non-zero on any failure (the GCP venv has no pytest).
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_ANALYZER = os.path.join(
    _REPO_ROOT, "research", "analyze_phase2k_d_data_expansion_plan.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase2k_d_data_expansion_plan_v1.md")
_INPUT_2LB_JSON = os.path.join(
    _REPO_ROOT, "research", "output", "phase2l_b_walk_forward_validation.json")

# Allowed upstream walk-forward verdicts; the pass case routes elsewhere (2L-C).
_ALLOWED_UPSTREAM = {"WALK_FORWARD_PASS", "WALK_FORWARD_FAIL", "NEED_MORE_DATA"}

# Forbidden infrastructure tokens: the analyzer must not deploy, shell out, touch a
# DB, or run migrations. Assembled from fragments so this test file does not itself
# trivially contain them as contiguous literals.
_FORBIDDEN_TOKENS = [
    "gcl" + "oud",
    "sub" + "process",
    "os." + "system",
    "para" + "miko",
    "system" + "ctl",
    "stock-api." + "service",
    "alem" + "bic",
    "create" + " table",
    "drop" + " table",
    "alter" + " table",
    "insert" + " into",
    "delete" + " from",
    "trun" + "cate",
    "place" + "_order",
    "submit" + "_order",
    "deploy" + "(",
    "PREDICTOR_USE_" + "MODEL_V2",
]

# Forbidden model-training / fitting tokens: this phase plans data, it does not model.
_FORBIDDEN_MODEL_TOKENS = [
    ".fit(",
    "fit_transform",
    "sklearn",
    "tensorflow",
    "lightgbm",
    "xgboost",
    "torch",
    "keras",
    "LinearRegression",
    "RandomForest",
    "MLPRegressor",
    "GradientBoosting",
]

# Guardrail phrases the doc must contain verbatim.
_REQUIRED_DOC_PHRASES = [
    "does not deploy",
    "does not restart stock-api.service",
    "does not enable",
    "does not run migrations",
    "does not write to production DB",
    "does not trade",
    "production edge",
]

# Required top-level fields of the plan JSON.
_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read", "upstream_phase2l_b_summary", "decision",
    "data_gap_analysis", "expanded_dataset_requirements", "target_validation_capacity",
    "data_quality_gates", "implementation_options", "research_retest_plan",
    "recommended_next_phase",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_edge_claimed",
)
_REQUIRED_TRUE = ("no_trading", "no_orders", "no_automation")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _import_analyzer():
    spec = importlib.util.spec_from_file_location("phase2k_d_analyzer", _ANALYZER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Build the plan at most once per test process and cache both the returned dict and
# the on-disk JSON.
_PLAN_CACHE = {}


def _plan():
    if "plan" not in _PLAN_CACHE:
        mod = _import_analyzer()
        out = os.path.join(tempfile.mkdtemp(), "plan.json")
        _PLAN_CACHE["plan"] = mod.run(output_path=out)
        _PLAN_CACHE["on_disk"] = json.loads(_read(out))
    return _PLAN_CACHE["plan"]


# --------------------------------------------------------------------------- #
# 1. Analyzer compiles
# --------------------------------------------------------------------------- #
def test_analyzer_compiles():
    compile(_read(_ANALYZER), _ANALYZER, "exec")


# --------------------------------------------------------------------------- #
# 2. Expected input files are referenced; no network
# --------------------------------------------------------------------------- #
def test_references_expected_inputs():
    mod = _import_analyzer()
    expected = {
        "INPUT_2LB_JSON": "research/output/phase2l_b_walk_forward_validation.json",
        "INPUT_2LA_JSON": "research/output/phase2l_a_walk_forward_design.json",
        "INPUT_2KC_JSON": "research/output/phase2k_c_residual_robustness.json",
        "INPUT_2KA_JSON": "research/output/phase2k_alpha_backlog.json",
        "RUN_SUMMARY_JSON": "research/output/phase2g_c_real_data_run_summary.json",
    }
    for const, tail in expected.items():
        val = getattr(mod, const)
        assert val.replace("\\", "/").endswith(tail), \
            f"{const} must point at {tail}, got {val!r}"
    text = _read(_ANALYZER).lower()
    for tok in ("http://", "https://", "requests.", "urllib", "yfinance", "socket"):
        assert tok not in text, f"analyzer must not reach the network: {tok!r}"


# --------------------------------------------------------------------------- #
# 3. Writes only the Phase 2K-D plan JSON
# --------------------------------------------------------------------------- #
def test_writes_only_plan_json():
    mod = _import_analyzer()
    assert mod.RESULTS_JSON.replace("\\", "/").endswith(
        "research/output/phase2k_d_data_expansion_plan.json")
    text = _read(_ANALYZER)
    assert "to_csv" not in text, "analyzer must not write any CSV"
    assert "to_sql" not in text, "analyzer must not write to a database"
    assert text.count("open(") >= 1
    tree = ast.parse(text)
    write_opens = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
                    and "w" in str(node.args[1].value):
                write_opens += 1
    assert write_opens == 1, "exactly one write-open (the plan JSON)"


# --------------------------------------------------------------------------- #
# 4. No api_server import
# --------------------------------------------------------------------------- #
def test_no_api_server_import():
    text = _read(_ANALYZER)
    assert "import api_server" not in text
    assert "from api_server" not in text
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = ([n.name for n in node.names]
                    + ([node.module] if isinstance(node, ast.ImportFrom)
                       and node.module else []))
            assert not any((m or "").split(".")[0] == "api_server" for m in mods)


# --------------------------------------------------------------------------- #
# 5. No Paper Trader import
# --------------------------------------------------------------------------- #
def test_no_paper_trader_import():
    assert "paper_trader" not in _read(_ANALYZER).lower()
    _import_analyzer()
    offenders = [m for m in sys.modules
                 if m == "paper_trader" or m.startswith("paper_trader.")]
    assert not offenders, f"unexpected Paper Trader import: {offenders}"


# --------------------------------------------------------------------------- #
# 6. No deploy / gcloud / SSH / service / DB-write / migration logic
# --------------------------------------------------------------------------- #
def test_no_forbidden_infrastructure_logic():
    text = _read(_ANALYZER).lower()
    hits = [tok for tok in _FORBIDDEN_TOKENS if tok.lower() in text]
    assert not hits, f"analyzer contains forbidden token(s): {hits}"
    for tok in ("ssh ", "scp ", "uvicorn", "systemd", "restart"):
        assert tok not in text, f"analyzer contains forbidden token: {tok!r}"


# --------------------------------------------------------------------------- #
# 7. No model training / fitting logic (this phase plans data, not models)
# --------------------------------------------------------------------------- #
def test_no_model_training_tokens():
    text = _read(_ANALYZER)
    hits = [tok for tok in _FORBIDDEN_MODEL_TOKENS if tok in text]
    assert not hits, f"analyzer contains model-training/fitting token(s): {hits}"
    plan = _plan()
    interp = plan["interpretation"]
    assert interp["model_trained"] is False
    assert interp["model_fitted"] is False
    assert interp["model_candidate_created"] is False
    assert interp["authorized_to_train_model"] is False
    assert interp["authorized_to_serve_model"] is False


# --------------------------------------------------------------------------- #
# 8. Output JSON: required fields and safety flags
# --------------------------------------------------------------------------- #
def test_output_json_required_fields():
    plan = _plan()
    on_disk = _PLAN_CACHE["on_disk"]
    for d in (plan, on_disk):
        for k in _REQUIRED_JSON_FIELDS:
            assert k in d, f"plan JSON missing field: {k}"
        assert d["phase"] == "2K-D"
        for k in _REQUIRED_FALSE:
            assert d[k] is False, f"{k} must be false"
        for k in _REQUIRED_TRUE:
            assert d[k] is True, f"{k} must be true"
        ir = d["inputs_read"]
        assert ir["phase2l_b_json"].replace("\\", "/").endswith(
            "research/output/phase2l_b_walk_forward_validation.json")
        assert ir["run_summary_json"].replace("\\", "/").endswith(
            "research/output/phase2g_c_real_data_run_summary.json")


# --------------------------------------------------------------------------- #
# 9. Reads the Phase 2L-B result (and reflects it consistently)
# --------------------------------------------------------------------------- #
def test_reads_phase2l_b_result():
    plan = _plan()
    up = plan["upstream_phase2l_b_summary"]
    assert up["phase"] == "2L-B"
    assert up["recommendation"] in _ALLOWED_UPSTREAM
    # The summary must match the live Phase 2L-B artifact, not a hard-coded value.
    live = json.loads(_read(_INPUT_2LB_JSON))
    assert up["recommendation"] == live["recommendation"]["recommendation"]
    assert up["candidate"] == live["candidate"]["feature"]
    assert set(up["failed_gates"]) == {
        g["id"] for g in live["pass_fail_gates"] if g["passed"] is False}
    # The decision must be consistent with that upstream verdict.
    assert plan["decision"]["upstream_recommendation"] == up["recommendation"]


# --------------------------------------------------------------------------- #
# 10. Decision: no model research, no model candidate now
# --------------------------------------------------------------------------- #
def test_decision_blocks_model_work():
    dec = _plan()["decision"]
    assert dec["continue_model_research_now"] is False
    assert dec["create_model_candidate_now"] is False
    assert isinstance(dec["reason"], str) and dec["reason"].strip()


# --------------------------------------------------------------------------- #
# 11. recommended_next_phase routes to 2K-E
# --------------------------------------------------------------------------- #
def test_recommended_next_phase_is_2k_e():
    nxt = _plan()["recommended_next_phase"]
    for k in ("phase", "title", "purpose"):
        assert k in nxt, f"recommended_next_phase missing field: {k}"
    assert nxt["phase"] == "2K-E"
    assert isinstance(nxt["purpose"], str) and nxt["purpose"].strip()


# --------------------------------------------------------------------------- #
# 12. data_gap_analysis exists and explains the basis
# --------------------------------------------------------------------------- #
def test_data_gap_analysis_present():
    gap = _plan()["data_gap_analysis"]
    assert isinstance(gap, dict) and gap
    assert "why_cannot_advance" in gap
    assert "current_panel" in gap
    rec = _plan()["upstream_phase2l_b_summary"]["recommendation"]
    assert gap["decision_basis"] == rec
    if rec == "NEED_MORE_DATA":
        assert "insufficient_folds_or_observations" in gap
    elif rec == "WALK_FORWARD_FAIL":
        assert "failed_gates_detail" in gap


# --------------------------------------------------------------------------- #
# 13. expanded_dataset_requirements exists with the core data items
# --------------------------------------------------------------------------- #
def test_expanded_dataset_requirements_present():
    req = _plan()["expanded_dataset_requirements"]
    assert isinstance(req, dict) and req
    items = req["requirements"]
    for key in ("longer_price_history", "broader_equity_universe",
                "point_in_time_universe_membership", "survivorship_bias_controls"):
        assert key in items, f"expanded_dataset_requirements missing: {key}"
        assert items[key]["required_for_retesting_candidate"] is True
    # Fundamentals / estimates are future-phase, not required for the liquidity candidate.
    assert items["point_in_time_fundamentals"]["future_phase"] is True
    assert items["point_in_time_fundamentals"]["required_for_retesting_candidate"] is False


# --------------------------------------------------------------------------- #
# 14. target_validation_capacity exists and is well formed
# --------------------------------------------------------------------------- #
def test_target_validation_capacity_present():
    cap = _plan()["target_validation_capacity"]
    for k in ("min_nonoverlapping_validation_folds", "min_effective_validation_obs",
              "min_tickers_per_date", "min_years_history",
              "must_include_at_least_one_stressed_period"):
        assert k in cap, f"target_validation_capacity missing key: {k}"
    assert cap["must_include_at_least_one_stressed_period"] is True
    assert cap["min_tickers_per_date"] > 40, "target universe must be broader than ~40"


# --------------------------------------------------------------------------- #
# 15. data_quality_gates exists and covers the leakage controls
# --------------------------------------------------------------------------- #
def test_data_quality_gates_present():
    gates = _plan()["data_quality_gates"]
    assert isinstance(gates, list) and len(gates) >= 6
    ids = {g["id"] for g in gates}
    for required in ("adjusted_close_consistency", "delisting_survivorship_treatment",
                     "no_forward_filled_target_labels",
                     "no_lookahead_universe_membership"):
        assert required in ids, f"data_quality_gates missing: {required}"


# --------------------------------------------------------------------------- #
# 16. implementation_options exists with cost / point-in-time / risks
# --------------------------------------------------------------------------- #
def test_implementation_options_present():
    opts = _plan()["implementation_options"]
    assert isinstance(opts, list) and len(opts) >= 2
    for o in opts:
        for k in ("option", "data_scope", "cost", "point_in_time_membership",
                  "pros", "risks"):
            assert k in o, f"implementation option missing key: {k}"


# --------------------------------------------------------------------------- #
# 17. Doc has all required guardrail phrases
# --------------------------------------------------------------------------- #
def test_doc_has_required_phrases():
    text = _read(_DOC)
    missing = [p for p in _REQUIRED_DOC_PHRASES if p not in text]
    assert not missing, f"doc missing required phrase(s): {missing}"


# --------------------------------------------------------------------------- #
# Self-running harness (no pytest required)
# --------------------------------------------------------------------------- #
def _run_all():
    tests = sorted((n, f) for n, f in globals().items()
                   if n.startswith("test_") and callable(f))
    passed = failed = 0
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"FAIL {name}: {e}")
            failures.append((name, traceback.format_exc()))
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {len(tests)} total")
    if failures:
        print("\n--- failure details ---")
        for name, tb in failures:
            print(f"\n### {name}\n{tb}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
