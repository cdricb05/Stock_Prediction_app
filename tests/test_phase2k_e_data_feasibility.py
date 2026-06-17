"""Phase 2K-E tests for the Expanded Historical Universe Data Feasibility analyzer.

These tests prove the analyzer produces a disciplined feasibility artifact in response
to the Phase 2K-D data-expansion plan without acquiring data or doing model work: it
compiles and imports without side effects; it references only the upstream Phase 2K-D /
2L-B / 2K-A JSON summaries and the Phase 2G run summary; it writes only
research/output/phase2k_e_data_feasibility.json; it never imports api_server or Paper
Trader; its source contains no deploy / gcloud / SSH / service / DB-write / migration
logic, no network / paid-acquisition logic, and no model-training / fitting tokens; and
the feasibility JSON it produces reads the Phase 2K-D result, carries every required
field and safety flag, inventories local data, assesses each requirement with a status
from the allowed vocabulary, reaches a decision, and routes to Phase 2K-F. The companion
doc must contain the guardrail phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase2k_e_data_feasibility.py
  * without pytest: python tests/test_phase2k_e_data_feasibility.py
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
    _REPO_ROOT, "research", "analyze_phase2k_e_data_feasibility.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase2k_e_data_feasibility_v1.md")
_INPUT_2KD_JSON = os.path.join(
    _REPO_ROOT, "research", "output", "phase2k_d_data_expansion_plan.json")

# Allowed feasibility statuses (closed vocabulary).
_ALLOWED_STATUSES = {
    "FEASIBLE_NOW",
    "FEASIBLE_WITH_FREE_EXTENSION",
    "FEASIBLE_WITH_LOW_COST_SOURCE",
    "REQUIRES_PAID_POINT_IN_TIME_DATA",
    "NOT_FEASIBLE_YET",
}

# The nine requirements the feasibility map must cover.
_REQUIRED_REQUIREMENTS = (
    "longer_price_history",
    "broader_equity_universe",
    "point_in_time_universe_membership",
    "survivorship_bias_controls",
    "liquidity_and_volume_history",
    "benchmark_and_factor_data",
    "sector_industry_classification",
    "point_in_time_fundamentals",
    "analyst_estimates_and_surprise",
)

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

# Forbidden model-training / fitting tokens: this phase checks feasibility, not models.
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

# Forbidden network / paid-acquisition tokens: the analyzer inspects local files only.
_FORBIDDEN_NETWORK_TOKENS = [
    "http://",
    "https://",
    "requests.",
    "urllib",
    "yfinance",
    "socket",
    "url" + "open",
    "down" + "load(",
    "check" + "out(",
    "pur" + "chase(",
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

# Required top-level fields of the feasibility JSON.
_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read", "upstream_phase2k_d_summary",
    "local_data_inventory", "feasibility_by_requirement", "feasibility_summary",
    "blockers", "recommended_source_strategy", "proposed_data_build_scope",
    "data_quality_preflight_plan", "decision", "recommended_next_phase",
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
    spec = importlib.util.spec_from_file_location("phase2k_e_analyzer", _ANALYZER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Build the artifact at most once per test process and cache both the returned dict and
# the on-disk JSON.
_PLAN_CACHE = {}


def _plan():
    if "plan" not in _PLAN_CACHE:
        mod = _import_analyzer()
        out = os.path.join(tempfile.mkdtemp(), "feasibility.json")
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
        "INPUT_2KD_JSON": "research/output/phase2k_d_data_expansion_plan.json",
        "INPUT_2LB_JSON": "research/output/phase2l_b_walk_forward_validation.json",
        "INPUT_2KA_JSON": "research/output/phase2k_alpha_backlog.json",
        "RUN_SUMMARY_JSON": "research/output/phase2g_c_real_data_run_summary.json",
    }
    for const, tail in expected.items():
        val = getattr(mod, const)
        assert val.replace("\\", "/").endswith(tail), \
            f"{const} must point at {tail}, got {val!r}"


# --------------------------------------------------------------------------- #
# 3. Writes only the Phase 2K-E feasibility JSON
# --------------------------------------------------------------------------- #
def test_writes_only_feasibility_json():
    mod = _import_analyzer()
    assert mod.RESULTS_JSON.replace("\\", "/").endswith(
        "research/output/phase2k_e_data_feasibility.json")
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
    assert write_opens == 1, "exactly one write-open (the feasibility JSON)"


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
# 7. No network calls and no paid-data acquisition logic
# --------------------------------------------------------------------------- #
def test_no_network_or_paid_acquisition():
    text = _read(_ANALYZER).lower()
    hits = [tok for tok in _FORBIDDEN_NETWORK_TOKENS if tok.lower() in text]
    assert not hits, f"analyzer contains network/acquisition token(s): {hits}"
    # The artifact must assert no acquisition happened.
    interp = _plan()["interpretation"]
    assert interp["data_acquired"] is False
    assert interp["paid_data_acquired"] is False
    assert interp["network_calls_made"] is False
    assert interp["builds_executed_now"] is False
    scope = _plan()["proposed_data_build_scope"]
    assert scope["builds_executed_now"] is False
    assert scope["paid_data_acquired"] is False


# --------------------------------------------------------------------------- #
# 8. No model training / fitting logic (this phase checks feasibility, not models)
# --------------------------------------------------------------------------- #
def test_no_model_training_tokens():
    text = _read(_ANALYZER)
    hits = [tok for tok in _FORBIDDEN_MODEL_TOKENS if tok in text]
    assert not hits, f"analyzer contains model-training/fitting token(s): {hits}"
    interp = _plan()["interpretation"]
    assert interp["model_trained"] is False
    assert interp["model_fitted"] is False
    assert interp["model_candidate_created"] is False
    assert interp["authorized_to_train_model"] is False
    assert interp["authorized_to_serve_model"] is False


# --------------------------------------------------------------------------- #
# 9. Output JSON: required fields and safety flags
# --------------------------------------------------------------------------- #
def test_output_json_required_fields():
    plan = _plan()
    on_disk = _PLAN_CACHE["on_disk"]
    for d in (plan, on_disk):
        for k in _REQUIRED_JSON_FIELDS:
            assert k in d, f"feasibility JSON missing field: {k}"
        assert d["phase"] == "2K-E"
        for k in _REQUIRED_FALSE:
            assert d[k] is False, f"{k} must be false"
        for k in _REQUIRED_TRUE:
            assert d[k] is True, f"{k} must be true"
        ir = d["inputs_read"]
        assert ir["phase2k_d_json"].replace("\\", "/").endswith(
            "research/output/phase2k_d_data_expansion_plan.json")
        assert ir["run_summary_json"].replace("\\", "/").endswith(
            "research/output/phase2g_c_real_data_run_summary.json")


# --------------------------------------------------------------------------- #
# 10. Reads the Phase 2K-D result (and reflects it consistently)
# --------------------------------------------------------------------------- #
def test_reads_phase2k_d_result():
    plan = _plan()
    up = plan["upstream_phase2k_d_summary"]
    assert up["phase"] == "2K-D"
    live = json.loads(_read(_INPUT_2KD_JSON))
    # The candidate and the no-model decision must mirror the live Phase 2K-D artifact.
    assert up["candidate"] == live["interpretation"]["candidate"]
    assert up["continue_model_research_now"] == \
        live["decision"]["continue_model_research_now"]
    assert up["create_model_candidate_now"] == \
        live["decision"]["create_model_candidate_now"]
    # Phase 2K-D routed to 2K-E.
    assert up["kd_recommended_next_phase"] == live["recommended_next_phase"]["phase"]


# --------------------------------------------------------------------------- #
# 11. local_data_inventory exists and reflects the current export
# --------------------------------------------------------------------------- #
def test_local_data_inventory_present():
    inv = _plan()["local_data_inventory"]
    assert isinstance(inv, dict) and inv
    for k in ("current_scored_csv_exists", "current_price_history_csv_exists",
              "current_run_summary_exists", "current_ticker_count",
              "current_row_count", "satisfies_2kd_target_capacity",
              "existing_output_files_found"):
        assert k in inv, f"local_data_inventory missing key: {k}"
    assert isinstance(inv["existing_output_files_found"], list)
    # The current ~3-year, ~40-name export cannot already satisfy the 8-10y / >=100-name
    # target capacity.
    assert inv["satisfies_2kd_target_capacity"] is False


# --------------------------------------------------------------------------- #
# 12. feasibility_by_requirement covers the required items with allowed statuses
# --------------------------------------------------------------------------- #
def test_feasibility_by_requirement_present():
    feas = _plan()["feasibility_by_requirement"]
    assert isinstance(feas, dict) and feas
    for key in _REQUIRED_REQUIREMENTS:
        assert key in feas, f"feasibility_by_requirement missing: {key}"
        item = feas[key]
        for f in ("status", "evidence", "gap", "risk", "next_check",
                  "required_for_retesting_candidate"):
            assert f in item, f"{key} missing field: {f}"
        assert item["status"] in _ALLOWED_STATUSES, \
            f"{key} has disallowed status {item['status']!r}"
        assert isinstance(item["required_for_retesting_candidate"], bool)


# --------------------------------------------------------------------------- #
# 13. All feasibility statuses come from the allowed vocabulary
# --------------------------------------------------------------------------- #
def test_feasibility_statuses_allowed():
    feas = _plan()["feasibility_by_requirement"]
    for key, item in feas.items():
        assert item["status"] in _ALLOWED_STATUSES, \
            f"{key} status {item['status']!r} not in allowed set"
    summ = _plan()["feasibility_summary"]
    assert summ["clean_retest_status"] in _ALLOWED_STATUSES
    assert summ["caveated_free_retest_status"] in _ALLOWED_STATUSES


# --------------------------------------------------------------------------- #
# 14. decision exists with the required fields
# --------------------------------------------------------------------------- #
def test_decision_present():
    dec = _plan()["decision"]
    assert isinstance(dec, dict) and dec
    for k in ("can_build_expanded_dataset_without_paid_data",
              "can_retest_avg_dollar_volume_without_paid_data",
              "paid_data_required_now", "proceed_to_data_build", "reason"):
        assert k in dec, f"decision missing key: {k}"
    assert isinstance(dec["paid_data_required_now"], bool)
    assert isinstance(dec["reason"], str) and dec["reason"].strip()


# --------------------------------------------------------------------------- #
# 15. recommended_next_phase routes to 2K-F
# --------------------------------------------------------------------------- #
def test_recommended_next_phase_is_2k_f():
    nxt = _plan()["recommended_next_phase"]
    for k in ("phase", "title", "purpose"):
        assert k in nxt, f"recommended_next_phase missing field: {k}"
    assert nxt["phase"] == "2K-F"
    assert isinstance(nxt["purpose"], str) and nxt["purpose"].strip()


# --------------------------------------------------------------------------- #
# 16. blockers / source strategy / preflight plan are well formed
# --------------------------------------------------------------------------- #
def test_supporting_sections_present():
    plan = _plan()
    assert isinstance(plan["blockers"], list)
    strat = plan["recommended_source_strategy"]
    assert isinstance(strat.get("ordered_preference"), list) \
        and strat["ordered_preference"]
    pre = plan["data_quality_preflight_plan"]
    assert isinstance(pre.get("checks"), list) and len(pre["checks"]) >= 6


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
