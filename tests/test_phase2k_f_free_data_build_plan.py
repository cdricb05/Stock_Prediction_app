"""Phase 2K-F tests for the Free Expanded Price/Volume Dataset Build Plan analyzer.

These tests prove the analyzer produces a disciplined build-PLAN artifact in response to
the Phase 2K-E feasibility result without building, fetching, or acquiring anything and
without model work: it compiles and imports without side effects; it references only the
upstream Phase 2K-E / 2K-D / 2K-A JSON summaries and the Phase 2G run summary; it writes
only research/output/phase2k_f_free_data_build_plan.json; it never imports api_server or
Paper Trader; its source contains no deploy / gcloud / SSH / service / DB-write / migration
logic, no network / paid-acquisition logic, and no model-training / fitting tokens; and the
plan JSON it produces reads the Phase 2K-E result, carries every required field and safety
flag, declares (but does not create) the future build script and the five future output
files, specifies the data-quality checks and pass/fail gates, decides to build nothing yet,
and routes to Phase 2K-G. The companion doc must contain the guardrail phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase2k_f_free_data_build_plan.py
  * without pytest: python tests/test_phase2k_f_free_data_build_plan.py
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
    _REPO_ROOT, "research", "analyze_phase2k_f_free_data_build_plan.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase2k_f_free_data_build_plan_v1.md")
_INPUT_2KE_JSON = os.path.join(
    _REPO_ROOT, "research", "output", "phase2k_e_data_feasibility.json")

# Future artifacts the plan must DECLARE but must NOT create in this phase.
_FUTURE_OUTPUT_FILES = (
    os.path.join("research", "output", "phase2k_g_expanded_price_history_free.csv"),
    os.path.join("research", "output", "phase2k_g_expanded_scored_free.csv"),
    os.path.join("research", "output", "phase2k_g_data_quality_report.json"),
    os.path.join("research", "output", "phase2k_g_data_build_summary.json"),
    os.path.join("research", "output", "phase2k_g_survivorship_caveat.json"),
)
_FUTURE_BUILD_SCRIPT = os.path.join(
    "research", "build_phase2k_g_free_expanded_dataset.py")
_FUTURE_UNIVERSE_CSV = os.path.join(
    "research", "input", "phase2k_g_free_universe_tickers.csv")

# Forbidden infrastructure tokens: the analyzer must not deploy, shell out, touch a DB,
# or run migrations. Assembled from fragments so this test file does not itself trivially
# contain them as contiguous literals.
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

# Forbidden model-training / fitting tokens: this phase plans data, not models.
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

# Forbidden network / paid-acquisition tokens: the analyzer inspects local files only and
# the build is deferred — no fetch or purchase logic may appear in this planning source.
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

# Required top-level fields of the build-plan JSON.
_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read", "upstream_phase2k_e_summary",
    "build_scope", "future_inputs", "future_outputs", "future_script_plan",
    "ticker_universe_plan", "free_data_retrieval_design", "data_quality_checks",
    "future_build_pass_fail_gates", "survivorship_caveat_policy",
    "implementation_sequence", "decision", "recommended_next_phase",
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
    spec = importlib.util.spec_from_file_location("phase2k_f_analyzer", _ANALYZER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Build the artifact at most once per test process and cache both the returned dict and
# the on-disk JSON.
_PLAN_CACHE = {}


def _plan():
    if "plan" not in _PLAN_CACHE:
        mod = _import_analyzer()
        out = os.path.join(tempfile.mkdtemp(), "build_plan.json")
        _PLAN_CACHE["plan"] = mod.run(output_path=out)
        _PLAN_CACHE["on_disk"] = json.loads(_read(out))
    return _PLAN_CACHE["plan"]


# --------------------------------------------------------------------------- #
# 1. Analyzer compiles
# --------------------------------------------------------------------------- #
def test_analyzer_compiles():
    compile(_read(_ANALYZER), _ANALYZER, "exec")


# --------------------------------------------------------------------------- #
# 2. Expected input files are referenced
# --------------------------------------------------------------------------- #
def test_references_expected_inputs():
    mod = _import_analyzer()
    expected = {
        "INPUT_2KE_JSON": "research/output/phase2k_e_data_feasibility.json",
        "INPUT_2KD_JSON": "research/output/phase2k_d_data_expansion_plan.json",
        "INPUT_2KA_JSON": "research/output/phase2k_alpha_backlog.json",
        "RUN_SUMMARY_JSON": "research/output/phase2g_c_real_data_run_summary.json",
    }
    for const, tail in expected.items():
        val = getattr(mod, const)
        assert val.replace("\\", "/").endswith(tail), \
            f"{const} must point at {tail}, got {val!r}"


# --------------------------------------------------------------------------- #
# 3. Writes only the Phase 2K-F build-plan JSON
# --------------------------------------------------------------------------- #
def test_writes_only_build_plan_json():
    mod = _import_analyzer()
    assert mod.RESULTS_JSON.replace("\\", "/").endswith(
        "research/output/phase2k_f_free_data_build_plan.json")
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
    assert write_opens == 1, "exactly one write-open (the build-plan JSON)"


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
    plan = _plan()
    interp = plan["interpretation"]
    assert interp["data_acquired"] is False
    assert interp["paid_data_acquired"] is False
    assert interp["network_calls_made"] is False
    assert interp["builds_executed_now"] is False
    assert interp["future_files_created_now"] is False
    design = plan["free_data_retrieval_design"]
    assert design["executed_now"] is False
    assert design["network_calls_made"] is False
    assert design["paid_data_acquired"] is False
    scope = plan["build_scope"]
    assert scope["builds_executed_now"] is False
    assert scope["data_fetched_now"] is False
    assert scope["paid_data_acquired_now"] is False


# --------------------------------------------------------------------------- #
# 8. No model training / fitting logic (this phase plans data, not models)
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
            assert k in d, f"build-plan JSON missing field: {k}"
        assert d["phase"] == "2K-F"
        for k in _REQUIRED_FALSE:
            assert d[k] is False, f"{k} must be false"
        for k in _REQUIRED_TRUE:
            assert d[k] is True, f"{k} must be true"
        ir = d["inputs_read"]
        assert ir["phase2k_e_json"].replace("\\", "/").endswith(
            "research/output/phase2k_e_data_feasibility.json")
        assert ir["run_summary_json"].replace("\\", "/").endswith(
            "research/output/phase2g_c_real_data_run_summary.json")


# --------------------------------------------------------------------------- #
# 10. Reads the Phase 2K-E result (and reflects it consistently)
# --------------------------------------------------------------------------- #
def test_reads_phase2k_e_result():
    plan = _plan()
    up = plan["upstream_phase2k_e_summary"]
    assert up["phase"] == "2K-E"
    live = json.loads(_read(_INPUT_2KE_JSON))
    # The candidate and the proceed decision must mirror the live Phase 2K-E artifact.
    assert up["candidate"] == live["interpretation"]["candidate"]
    assert up["proceed_to_data_build"] == live["decision"]["proceed_to_data_build"]
    # Phase 2K-E routed to 2K-F.
    assert up["ke_recommended_next_phase"] == live["recommended_next_phase"]["phase"]
    assert up["ke_recommended_next_phase"] == "2K-F"
    assert up["routed_to_2k_f"] is True


# --------------------------------------------------------------------------- #
# 11. decision: nothing is built / fetched / purchased / modeled now
# --------------------------------------------------------------------------- #
def test_decision_defers_everything():
    dec = _plan()["decision"]
    assert isinstance(dec, dict) and dec
    for k in ("create_build_script_now", "execute_data_build_now", "download_data_now",
              "acquire_paid_data_now", "create_model_candidate_now"):
        assert k in dec, f"decision missing key: {k}"
        assert dec[k] is False, f"decision.{k} must be false"
    assert isinstance(dec["reason"], str) and dec["reason"].strip()


# --------------------------------------------------------------------------- #
# 12. Future outputs are declared but not created
# --------------------------------------------------------------------------- #
def test_future_outputs_declared_not_created():
    fo = _plan()["future_outputs"]
    assert isinstance(fo, dict) and fo
    declared_paths = set()
    for key, entry in fo.items():
        if not isinstance(entry, dict) or "path" not in entry:
            continue  # the explanatory "note"
        assert entry["exists_now"] is False, \
            f"future output {key} must not exist yet (exists_now)"
        assert entry["created_by_this_phase"] is False
        declared_paths.add(entry["path"].replace("\\", "/"))
    # All five expected future output files are declared.
    for rel in _FUTURE_OUTPUT_FILES:
        assert _norm_rel(rel) in declared_paths, f"missing declared future output: {rel}"
        # And none of them may have been created on disk by this phase.
        assert not os.path.isfile(os.path.join(_REPO_ROOT, rel)), \
            f"this phase must not create {rel}"


# --------------------------------------------------------------------------- #
# 13. Future build script + ticker universe file declared but not created
# --------------------------------------------------------------------------- #
def test_future_script_and_universe_declared_not_created():
    plan = _plan()
    fsp = plan["future_script_plan"]
    assert fsp["path"].replace("\\", "/").endswith(
        "research/build_phase2k_g_free_expanded_dataset.py")
    assert fsp["exists_now"] is False
    assert fsp["created_by_this_phase"] is False
    assert not os.path.isfile(os.path.join(_REPO_ROOT, _FUTURE_BUILD_SCRIPT)), \
        "this phase must not create the future build script"

    tup = plan["ticker_universe_plan"]
    assert tup["proposed_universe_file"].replace("\\", "/").endswith(
        "research/input/phase2k_g_free_universe_tickers.csv")
    assert tup["proposed_universe_file_exists_now"] is False
    assert tup["source_must_be_local_or_manual"] is True
    assert tup["network_ticker_source_allowed"] is False
    for col in ("ticker", "name", "sector", "source", "active_as_of",
                "survivorship_caveat"):
        assert col in tup["columns"], f"ticker_universe_plan missing column: {col}"
    assert not os.path.isfile(os.path.join(_REPO_ROOT, _FUTURE_UNIVERSE_CSV)), \
        "this phase must not create the future ticker universe file"


# --------------------------------------------------------------------------- #
# 14. data_quality_checks exist and cover the required checks
# --------------------------------------------------------------------------- #
def test_data_quality_checks_present():
    checks = _plan()["data_quality_checks"]
    assert isinstance(checks, list) and len(checks) >= 12
    ids = {c["check_id"] for c in checks if isinstance(c, dict) and "check_id" in c}
    for required in ("adjusted_close_continuity", "duplicate_date_ticker_rows",
                     "missing_adjusted_close", "missing_volume", "benchmark_coverage",
                     "minimum_years_achieved", "minimum_ticker_count_achieved",
                     "no_forward_filled_labels", "no_point_in_time_membership_claim",
                     "survivorship_caveat_present"):
        assert required in ids, f"data_quality_checks missing: {required}"


# --------------------------------------------------------------------------- #
# 15. future_build_pass_fail_gates exist with an allowed report status
# --------------------------------------------------------------------------- #
def test_future_build_pass_fail_gates_present():
    gates_block = _plan()["future_build_pass_fail_gates"]
    gates = gates_block.get("gates")
    assert isinstance(gates, list) and len(gates) >= 6
    ids = {g["gate_id"] for g in gates if isinstance(g, dict) and "gate_id" in g}
    for required in ("required_files_created", "min_years", "min_tickers",
                     "duplicate_rate_zero", "survivorship_caveat_file_present",
                     "data_quality_report_status"):
        assert required in ids, f"pass/fail gates missing: {required}"
    status_gate = next(g for g in gates if g.get("gate_id") == "data_quality_report_status")
    assert set(status_gate["allowed_status"]) == {"PASS", "PASS_WITH_CAVEAT", "FAIL"}


# --------------------------------------------------------------------------- #
# 16. recommended_next_phase routes to 2K-G
# --------------------------------------------------------------------------- #
def test_recommended_next_phase_is_2k_g():
    nxt = _plan()["recommended_next_phase"]
    for k in ("phase", "title", "purpose"):
        assert k in nxt, f"recommended_next_phase missing field: {k}"
    assert nxt["phase"] == "2K-G"
    assert nxt["title"] == "Free Expanded Price/Volume Dataset Builder"
    assert isinstance(nxt["purpose"], str) and nxt["purpose"].strip()


# --------------------------------------------------------------------------- #
# 17. Supporting sections are well formed
# --------------------------------------------------------------------------- #
def test_supporting_sections_present():
    plan = _plan()
    scope = plan["build_scope"]
    assert scope["target_history_years_min"] is not None
    assert scope["target_min_tickers_per_date"] is not None
    assert scope["survivorship_caveat"]["explicitly_documented"] is True
    fi = plan["future_inputs"]
    for k in ("ticker_list_file", "start_date", "end_date", "benchmark_ticker",
              "output_directory"):
        assert k in fi, f"future_inputs missing: {k}"
    design = plan["free_data_retrieval_design"]
    assert isinstance(design.get("principles"), list) and design["principles"]
    pol = plan["survivorship_caveat_policy"]
    assert pol["no_false_point_in_time_membership_claim"] is True
    assert pol["clean_point_in_time_build_deferred"] is True
    seq = plan["implementation_sequence"]
    assert isinstance(seq, list) and seq


# --------------------------------------------------------------------------- #
# 18. Doc has all required guardrail phrases
# --------------------------------------------------------------------------- #
def test_doc_has_required_phrases():
    text = _read(_DOC)
    missing = [p for p in _REQUIRED_DOC_PHRASES if p not in text]
    assert not missing, f"doc missing required phrase(s): {missing}"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _norm_rel(rel: str) -> str:
    return rel.replace("\\", "/")


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
