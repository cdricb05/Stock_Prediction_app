"""Phase 2K-J tests for the Alpha Backlog Refresh planning/diagnostic analyzer.

These tests prove the analyzer is a disciplined, read-only planning artifact: it compiles and
imports cleanly; it references the expected small input summaries; it reads the Phase 2K-I
result and confirms it routed here (EXPANDED_RETEST_FAIL -> 2K-J); it stops the failed
liquidity candidate and never reintroduces avg_dollar_volume_21d as a standalone alpha; it
refreshes the alpha backlog with fully-specified items; it routes forward to 2K-K; it carries
every required output field and safety flag; it imports neither api_server nor Paper Trader; it
contains no forbidden infrastructure / model-training / network / D:-mutation tokens; it has
exactly one write-open; and the doc carries the required guardrail phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase2k_j_alpha_backlog_refresh.py
  * without pytest: python tests/test_phase2k_j_alpha_backlog_refresh.py
The __main__ block discovers every test_* function, runs it, prints PASS/FAIL, and exits
non-zero on any failure (the GCP venv has no pytest).
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
    _REPO_ROOT, "research", "analyze_phase2k_j_alpha_backlog_refresh.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase2k_j_alpha_backlog_refresh_v1.md")
_INPUTS_DIR = os.path.join(_REPO_ROOT, "research", "output")
_COMMITTED_RESULTS = os.path.join(
    _REPO_ROOT, "research", "output", "phase2k_j_alpha_backlog_refresh.json")

_STOPPED_CANDIDATE = "avg_dollar_volume_21d"

# The eight read-only input summaries this phase references.
_EXPECTED_INPUT_BASENAMES = (
    "phase2k_i_expanded_residual_retest.json",
    "phase2k_h_manual_build_run_summary.json",
    "phase2k_alpha_backlog.json",
    "phase2k_c_residual_robustness.json",
    "phase2l_b_walk_forward_validation.json",
    "phase2k_d_data_expansion_plan.json",
    "phase2k_e_data_feasibility.json",
    "phase2k_f_free_data_build_plan.json",
)

# Forbidden infrastructure tokens (assembled from fragments) — apply to the analyzer source.
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
_FORBIDDEN_EXTRA = ("ssh ", "scp ", "uvicorn", "systemd", "restart")

# Forbidden model-training / fitting tokens (no model work here).
_FORBIDDEN_MODEL_TOKENS = [
    ".fit(", "fit_transform", "sklearn", "tensorflow", "lightgbm", "xgboost",
    "torch", "keras", "LinearRegression", "RandomForest", "MLPRegressor",
    "GradientBoosting",
]

# Forbidden network / acquisition tokens — the analyzer must stay network-free.
_FORBIDDEN_NETWORK_TOKENS = [
    "http://", "https://", "requests.", "urllib", "yf" + "inance", "socket",
    "url" + "open", "down" + "load(", "pur" + "chase(",
]

# The analyzer must not mutate D: data files or read the large D: CSVs.
_FORBIDDEN_MUTATION_TOKENS = [
    "os." + "remove", "os." + "rename", "os." + "unlink", "shutil", "rm" + "tree",
    "to_csv", "to_sql", "read_csv", "pd.",
]

_REQUIRED_DOC_PHRASES = [
    "does not deploy",
    "does not restart stock-api.service",
    "does not enable",
    "does not run migrations",
    "does not write to production DB",
    "does not trade",
    "production edge",
]

_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read", "upstream_phase2k_i_summary",
    "failed_candidate_decision", "failure_diagnostics", "alpha_backlog_refresh",
    "research_priorities", "next_model_free_tests", "data_requirements",
    "leakage_controls", "recommendation", "recommended_next_phase",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_edge_claimed",
)
_REQUIRED_TRUE = ("no_trading", "no_orders", "no_automation")

_REQUIRED_BACKLOG_ITEM_FIELDS = (
    "id", "name", "category", "rationale", "required_data", "leakage_risks",
    "expected_horizon", "priority", "status", "next_test", "production_allowed",
)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _import_analyzer():
    spec = importlib.util.spec_from_file_location("phase2k_j_analyzer_test", _ANALYZER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run():
    """Run the analyzer against the real committed input summaries, writing to a temp path."""
    analyzer = _import_analyzer()
    out = os.path.join(tempfile.mkdtemp(prefix="phase2k_j_"), "refresh.json")
    res = analyzer.run(output_path=out, inputs_dir=_INPUTS_DIR)
    return res, out


# --------------------------------------------------------------------------- #
# 1. Analyzer compiles and imports
# --------------------------------------------------------------------------- #
def test_analyzer_compiles():
    compile(_read(_ANALYZER), _ANALYZER, "exec")


def test_analyzer_imports():
    _import_analyzer()


# --------------------------------------------------------------------------- #
# 2. Expected input summaries are referenced
# --------------------------------------------------------------------------- #
def test_references_expected_inputs():
    analyzer = _import_analyzer()
    text = _read(_ANALYZER)
    for base in _EXPECTED_INPUT_BASENAMES:
        assert base in text, f"analyzer does not reference expected input: {base}"
    res, _ = _run()
    read = res["inputs_read"]
    referenced = "\n".join(read.values())
    for base in _EXPECTED_INPUT_BASENAMES:
        assert base in referenced, f"inputs_read missing: {base}"
    assert analyzer.RESULTS_JSON.replace("\\", "/").endswith(
        "research/output/phase2k_j_alpha_backlog_refresh.json")


# --------------------------------------------------------------------------- #
# 3. No api_server import
# --------------------------------------------------------------------------- #
def test_no_api_server_import():
    text = _read(_ANALYZER)
    assert "import api_server" not in text
    assert "from api_server" not in text
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = ([n.name for n in node.names]
                    + ([node.module] if isinstance(node, ast.ImportFrom)
                       and node.module else []))
            assert not any((m or "").split(".")[0] == "api_server" for m in mods)


# --------------------------------------------------------------------------- #
# 4. No Paper Trader import
# --------------------------------------------------------------------------- #
def test_no_paper_trader_import():
    assert "paper_trader" not in _read(_ANALYZER).lower()


# --------------------------------------------------------------------------- #
# 5. No forbidden infrastructure / model-training tokens
# --------------------------------------------------------------------------- #
def test_no_forbidden_infrastructure_or_model_logic():
    text = _read(_ANALYZER)
    low = text.lower()
    hits = [tok for tok in _FORBIDDEN_TOKENS if tok.lower() in low]
    assert not hits, f"analyzer has forbidden token(s): {hits}"
    for tok in _FORBIDDEN_EXTRA:
        assert tok not in low, f"analyzer has forbidden token: {tok!r}"
    model_hits = [tok for tok in _FORBIDDEN_MODEL_TOKENS if tok in text]
    assert not model_hits, f"analyzer has model-training token(s): {model_hits}"


# --------------------------------------------------------------------------- #
# 6. No network logic, no D: read/mutation, exactly one write-open
# --------------------------------------------------------------------------- #
def test_analyzer_is_read_only_and_network_free():
    text = _read(_ANALYZER)
    low = text.lower()
    net = [tok for tok in _FORBIDDEN_NETWORK_TOKENS if tok.lower() in low]
    assert not net, f"analyzer contains network/acquisition token(s): {net}"
    mut = [tok for tok in _FORBIDDEN_MUTATION_TOKENS if tok.lower() in low]
    assert not mut, f"analyzer contains D:-read/mutation token(s): {mut}"
    write_opens = 0
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
                    and "w" in str(node.args[1].value):
                write_opens += 1
    assert write_opens == 1, "analyzer must have exactly one write-open (the refresh JSON)"


# --------------------------------------------------------------------------- #
# 7. Output JSON: required fields and safety flags
# --------------------------------------------------------------------------- #
def test_output_json_required_fields():
    res, on_disk_path = _run()
    on_disk = json.loads(_read(on_disk_path))
    for d in (res, on_disk):
        for k in _REQUIRED_JSON_FIELDS:
            assert k in d, f"refresh JSON missing field: {k}"
        assert d["phase"] == "2K-J"
        for k in _REQUIRED_FALSE:
            assert d[k] is False, f"{k} must be false"
        for k in _REQUIRED_TRUE:
            assert d[k] is True, f"{k} must be true"


# --------------------------------------------------------------------------- #
# 8. Reads Phase 2K-I and confirms it routed here (EXPANDED_RETEST_FAIL -> 2K-J)
# --------------------------------------------------------------------------- #
def test_reads_phase2k_i_and_confirms_routing():
    res, _ = _run()
    up = res["upstream_phase2k_i_summary"]
    assert up["present"] is True
    assert up["recommendation"] == "EXPANDED_RETEST_FAIL"
    assert up["candidate_feature"] == _STOPPED_CANDIDATE
    routing = up["routing_confirmation"]
    assert routing["recommendation_is_expanded_retest_fail"] is True
    assert routing["recommended_next_phase_is_2k_j"] is True
    assert routing["recommended_next_phase_title_matches"] is True
    assert routing["routing_confirmed"] is True


# --------------------------------------------------------------------------- #
# 9. Failed-candidate decision stops the liquidity candidate
# --------------------------------------------------------------------------- #
def test_failed_candidate_decision():
    res, _ = _run()
    dec = res["failed_candidate_decision"]
    assert dec["candidate"] == _STOPPED_CANDIDATE
    assert dec["status"] == "STOPPED_AFTER_EXPANDED_RETEST_FAIL"
    assert dec["create_model_candidate_now"] is False
    assert dec["train_model_now"] is False
    assert dec["deploy_now"] is False
    assert dec["production_edge_claimed"] is False
    assert isinstance(dec["reason"], str) and dec["reason"].strip()


# --------------------------------------------------------------------------- #
# 10. Recommendation refreshes the backlog and stops standalone liquidity
# --------------------------------------------------------------------------- #
def test_recommendation_block():
    res, _ = _run()
    rec = res["recommendation"]
    assert rec["recommendation"] == "REFRESH_ALPHA_BACKLOG"
    assert rec["standalone_liquidity_candidate_stopped"] is True
    assert rec["continue_model_research_now"] is False
    assert rec["create_model_candidate_now"] is False
    assert rec["train_model_now"] is False
    assert isinstance(rec["reason"], str) and rec["reason"].strip()


# --------------------------------------------------------------------------- #
# 11. Recommended next phase routes to 2K-K
# --------------------------------------------------------------------------- #
def test_recommended_next_phase_is_2k_k():
    res, _ = _run()
    nxt = res["recommended_next_phase"]
    assert nxt["phase"] == "2K-K"
    assert nxt["title"] == "Expanded Dataset Model-Free Alpha Screen"
    assert isinstance(nxt["purpose"], str) and nxt["purpose"].strip()


# --------------------------------------------------------------------------- #
# 12. Backlog items exist and carry every required field
# --------------------------------------------------------------------------- #
def test_backlog_items_well_formed():
    res, _ = _run()
    refresh = res["alpha_backlog_refresh"]
    items = refresh["refreshed_backlog"]
    assert isinstance(items, list) and len(items) >= 5, "expected a refreshed backlog"
    for item in items:
        for f in _REQUIRED_BACKLOG_ITEM_FIELDS:
            assert f in item, f"backlog item {item.get('id')!r} missing field: {f}"
        assert item["production_allowed"] is False, \
            f"backlog item {item['id']} must have production_allowed=false"
        assert isinstance(item["required_data"], list) and item["required_data"]
    # Required refreshed categories are present.
    cats = {item["category"] for item in items}
    for needed in ("price_momentum_alternatives", "short_term_reversal_mean_reversion",
                   "volatility_adjusted_momentum", "sector_relative_momentum"):
        assert needed in cats, f"missing refreshed backlog category: {needed}"
    # Liquidity is present but deprioritized to diagnostic-only.
    assert any("liquidity" in c for c in cats), "liquidity category must still be represented"


# --------------------------------------------------------------------------- #
# 13. avg_dollar_volume_21d is NOT reintroduced as a standalone alpha candidate
# --------------------------------------------------------------------------- #
def test_liquidity_not_reintroduced_as_standalone_alpha():
    res, _ = _run()
    refresh = res["alpha_backlog_refresh"]
    assert _STOPPED_CANDIDATE in refresh["stopped_standalone_candidates"]
    for item in refresh["refreshed_backlog"]:
        if item.get("primary_feature") == _STOPPED_CANDIDATE:
            assert item.get("standalone_alpha") is False, \
                "stopped liquidity candidate must not be a standalone alpha"
            assert item["status"] == "DEPRIORITIZED_DIAGNOSTIC_ONLY"
    # The next model-free screen must not include the stopped candidate as a test id.
    for t in res["next_model_free_tests"]:
        assert t["id"] != _STOPPED_CANDIDATE
        assert t.get("trains_model") is False
    for p in res["research_priorities"]:
        assert p.get("excludes_standalone_avg_dollar_volume") is True


# --------------------------------------------------------------------------- #
# 14. Failure diagnostics capture the lessons
# --------------------------------------------------------------------------- #
def test_failure_diagnostics():
    res, _ = _run()
    diag = res["failure_diagnostics"]
    assert diag["full_sample_residual_ic_negative"] is True
    assert diag["walk_forward_validation_failed"] is True
    assert diag["signal_direction_reversed_vs_prior_thin_data"] is True
    assert isinstance(diag["failed_gates"], list) and diag["failed_gates"]
    assert isinstance(diag["survivorship_caveat_implications"], str)
    assert isinstance(diag["data_expansion_lesson"], str) and diag["data_expansion_lesson"].strip()


# --------------------------------------------------------------------------- #
# 15. No large D: outputs are written; only the small refresh JSON
# --------------------------------------------------------------------------- #
def test_only_small_refresh_json_written():
    res, on_disk_path = _run()
    assert os.path.isfile(on_disk_path)
    # The analyzer writes a small JSON, not a CSV / dataset.
    assert on_disk_path.endswith(".json")
    blob = json.loads(_read(on_disk_path))
    assert blob["phase"] == "2K-J"


# --------------------------------------------------------------------------- #
# 16. Leakage controls block is present and conservative
# --------------------------------------------------------------------------- #
def test_leakage_controls():
    res, _ = _run()
    lk = res["leakage_controls"]
    assert lk["features_trailing_point_in_time_only"] is True
    assert lk["labels_strictly_forward"] is True
    assert lk["labels_forward_filled"] is False
    assert lk["survivorship_caveat_carried_forward"] is True
    assert lk["point_in_time_membership_claimed"] is False


# --------------------------------------------------------------------------- #
# 17. The committed refresh artifact is schema-valid
# --------------------------------------------------------------------------- #
def test_committed_results_artifact_valid():
    assert os.path.isfile(_COMMITTED_RESULTS), "committed refresh JSON must exist"
    d = json.loads(_read(_COMMITTED_RESULTS))
    for k in _REQUIRED_JSON_FIELDS:
        assert k in d, f"committed refresh JSON missing field: {k}"
    assert d["phase"] == "2K-J"
    for k in _REQUIRED_FALSE:
        assert d[k] is False
    for k in _REQUIRED_TRUE:
        assert d[k] is True
    assert d["failed_candidate_decision"]["status"] == "STOPPED_AFTER_EXPANDED_RETEST_FAIL"
    assert d["recommendation"]["recommendation"] == "REFRESH_ALPHA_BACKLOG"
    assert d["recommendation"]["standalone_liquidity_candidate_stopped"] is True
    assert d["recommended_next_phase"]["phase"] == "2K-K"
    items = d["alpha_backlog_refresh"]["refreshed_backlog"]
    assert items and all("id" in it for it in items)


# --------------------------------------------------------------------------- #
# 18. Doc has all required guardrail phrases
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
