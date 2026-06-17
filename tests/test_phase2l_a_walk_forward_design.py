"""Phase 2L-A tests for the Walk-Forward Residual Model Research Design analyzer.

These tests prove the analyzer is a design-only, read-only planning artifact: it
compiles and imports without side effects; it references only the Phase 2K-C / 2K-B
/ 2K-A / 2J-A JSON summaries and the Phase 2G run summary; it writes only
research/output/phase2l_a_walk_forward_design.json; it never imports api_server or
Paper Trader; its source contains no deploy / gcloud / SSH / service / DB-write /
migration logic; and the design JSON it produces carries every required field and
safety flag, selects avg_dollar_volume_21d (the sole Phase 2K-C model-research
candidate), describes a walk-forward design / model-free baseline / pass-fail gates
/ leakage controls (including the embargo), keeps the later single-feature model
strictly un-authorized, and recommends Phase 2L-B. The companion doc must contain
the guardrail phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase2l_a_walk_forward_design.py
  * without pytest: python tests/test_phase2l_a_walk_forward_design.py
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
    _REPO_ROOT, "research", "analyze_phase2l_a_walk_forward_design.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase2l_a_walk_forward_design_v1.md")
_INPUT_2KC_JSON = os.path.join(
    _REPO_ROOT, "research", "output", "phase2k_c_residual_robustness.json")

# The sole Phase 2K-C model-research candidate this design must target.
_EXPECTED_CANDIDATE = "avg_dollar_volume_21d"

# Forbidden source tokens: the analyzer must not deploy, shell out, touch a DB,
# or run migrations. Assembled from fragments so this test file does not itself
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

# Required top-level fields of the design JSON.
_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read", "upstream_summary",
    "candidate_selected", "walk_forward_design", "model_free_baseline_design",
    "later_model_candidate_design", "pass_fail_gates", "leakage_controls",
    "implementation_plan", "recommended_next_phase",
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
    spec = importlib.util.spec_from_file_location("phase2l_a_analyzer", _ANALYZER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Run the design build at most once per test process and cache both the returned
# dict and the on-disk JSON.
_DIAG_CACHE = {}


def _diag():
    if "diag" not in _DIAG_CACHE:
        mod = _import_analyzer()
        out = os.path.join(tempfile.mkdtemp(), "design.json")
        _DIAG_CACHE["diag"] = mod.run(output_path=out)
        _DIAG_CACHE["on_disk"] = json.loads(_read(out))
    return _DIAG_CACHE["diag"]


def _phase2k_c_survivors():
    """The Phase 2K-C model-research shortlist the design must target."""
    twokc = json.loads(_read(_INPUT_2KC_JSON))
    return list(twokc["recommendation_summary"]["keep_for_model_research"])


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
        "INPUT_2KC_JSON": "research/output/phase2k_c_residual_robustness.json",
        "INPUT_2KB_JSON": "research/output/phase2k_b_residual_signal.json",
        "INPUT_2KA_JSON": "research/output/phase2k_alpha_backlog.json",
        "INPUT_2J_JSON": "research/output/phase2j_research_decision.json",
        "RUN_SUMMARY_JSON": "research/output/phase2g_c_real_data_run_summary.json",
    }
    for const, tail in expected.items():
        val = getattr(mod, const)
        assert val.replace("\\", "/").endswith(tail), \
            f"{const} must point at {tail}, got {val!r}"
    text = _read(_ANALYZER).lower()
    for tok in ("http://", "https://", "requests.", "urllib", "yfinance",
                "socket"):
        assert tok not in text, f"analyzer must not reach the network: {tok!r}"


# --------------------------------------------------------------------------- #
# 3. Writes only the Phase 2L-A design JSON
# --------------------------------------------------------------------------- #
def test_writes_only_design_json():
    mod = _import_analyzer()
    assert mod.DESIGN_JSON.replace("\\", "/").endswith(
        "research/output/phase2l_a_walk_forward_design.json")
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
    assert write_opens == 1, "exactly one write-open (the design JSON)"


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
# 7. Output JSON: required fields and safety flags
# --------------------------------------------------------------------------- #
def test_output_json_required_fields():
    diag = _diag()
    on_disk = _DIAG_CACHE["on_disk"]
    for d in (diag, on_disk):
        for k in _REQUIRED_JSON_FIELDS:
            assert k in d, f"design JSON missing field: {k}"
        assert d["phase"] == "2L-A"
        for k in _REQUIRED_FALSE:
            assert d[k] is False, f"{k} must be false"
        for k in _REQUIRED_TRUE:
            assert d[k] is True, f"{k} must be true"
        ir = d["inputs_read"]
        assert ir["phase2k_c_json"].replace("\\", "/").endswith(
            "research/output/phase2k_c_residual_robustness.json")
        assert ir["run_summary_json"].replace("\\", "/").endswith(
            "research/output/phase2g_c_real_data_run_summary.json")


# --------------------------------------------------------------------------- #
# 8. candidate_selected is the sole Phase 2K-C model-research candidate
# --------------------------------------------------------------------------- #
def test_candidate_selected_is_avg_dollar_volume_21d():
    diag = _diag()
    cs = diag["candidate_selected"]
    assert cs["feature"] == _EXPECTED_CANDIDATE, \
        f"candidate_selected {cs['feature']!r} != {_EXPECTED_CANDIDATE!r}"
    assert cs["is_sole_model_research_candidate"] is True
    # And it matches the live Phase 2K-C decision.
    survivors = _phase2k_c_survivors()
    assert survivors == [_EXPECTED_CANDIDATE], \
        f"Phase 2K-C survivors {survivors} != [{_EXPECTED_CANDIDATE!r}]"
    assert isinstance(cs["primary_horizon_days"], int) and cs["primary_horizon_days"] > 0


# --------------------------------------------------------------------------- #
# 9. walk_forward_design exists and is well formed
# --------------------------------------------------------------------------- #
def test_walk_forward_design_present():
    wf = _diag()["walk_forward_design"]
    assert wf["candidate_signal"] == _EXPECTED_CANDIDATE
    assert isinstance(wf["primary_horizon_days"], int)
    assert wf["comparison_horizons_days"] == [21, 63]
    # Expanding AND rolling training options.
    tw = wf["training_windows"]
    assert "expanding_window" in tw and "rolling_window" in tw
    # Sequential, embargoed, no-overlap, no-random-CV validation.
    vw = wf["validation_windows"]
    assert vw["type"] == "sequential_time_splits"
    assert vw["test_period_always_after_training_period"] is True
    assert vw["no_overlap_between_train_and_validation_label_windows"] is True
    assert vw["no_random_cross_validation"] is True
    assert vw["embargo_equals_forward_horizon"] is True
    assert vw["embargo_sessions"] == wf["primary_horizon_days"]


# --------------------------------------------------------------------------- #
# 10. model_free_baseline_design exists and runs first, with no fitted model
# --------------------------------------------------------------------------- #
def test_model_free_baseline_design_present():
    mb = _diag()["model_free_baseline_design"]
    assert mb["run_first"] is True
    assert mb["fitted_model"] is False
    assert mb["strategy"] == "rank_only"
    metrics = mb["metrics_per_validation_window"]
    for m in ("residual_label_rank_ic", "top_minus_bottom_residual_spread",
              "top_decile_residual_hit_rate", "ticker_concentration_in_long_leg"):
        assert m in metrics, f"model-free baseline missing metric: {m}"


# --------------------------------------------------------------------------- #
# 11. later_model_candidate_design exists but authorizes no training
# --------------------------------------------------------------------------- #
def test_later_model_candidate_design_not_authorized():
    lm = _diag()["later_model_candidate_design"]
    assert lm["authorized_now"] is False
    assert lm["single_feature_only"] is True
    for forbidden in ("multi_feature_model", "neural_network", "complex_ml",
                      "production_scoring", "serving_integration",
                      "model_v2_enablement"):
        assert lm[forbidden] is False, f"{forbidden} must be False"
    # And the design as a whole creates no model candidate.
    interp = _diag()["interpretation"]
    assert interp["model_candidate_created"] is False
    assert interp["authorized_to_train_model"] is False


# --------------------------------------------------------------------------- #
# 12. pass_fail_gates exist and cover the required checks
# --------------------------------------------------------------------------- #
def test_pass_fail_gates_present():
    gates = _diag()["pass_fail_gates"]
    assert isinstance(gates, list) and len(gates) >= 5
    ids = {g["id"] for g in gates}
    for required in ("validation_mean_ic_above_floor",
                     "majority_validation_windows_same_sign",
                     "bootstrap_ci_excludes_zero_in_validation",
                     "no_single_validation_period_drives_result",
                     "no_excessive_ticker_concentration",
                     "no_regime_only_dependence",
                     "no_degradation_vs_phase2k_c",
                     "no_lookahead_leakage",
                     "enough_effective_observations_after_embargo"):
        assert required in ids, f"pass_fail_gates missing gate: {required}"
    for g in gates:
        assert "description" in g and "pass_condition" in g


# --------------------------------------------------------------------------- #
# 13. leakage_controls exist and include an embargo
# --------------------------------------------------------------------------- #
def test_leakage_controls_include_embargo():
    lc = _diag()["leakage_controls"]
    assert "embargo_between_train_and_validation" in lc, \
        "leakage_controls must include an embargo"
    emb = lc["embargo_between_train_and_validation"]
    assert isinstance(emb["embargo_sessions"], int) and emb["embargo_sessions"] > 0
    for k in ("trailing_only_features", "point_in_time_universe",
              "train_only_residualization_fit",
              "no_validation_set_threshold_tuning"):
        assert k in lc, f"leakage_controls missing control: {k}"


# --------------------------------------------------------------------------- #
# 14. recommended_next_phase is 2L-B and consistent with the upstream decision
# --------------------------------------------------------------------------- #
def test_recommended_next_phase_is_2l_b():
    diag = _diag()
    nxt = diag["recommended_next_phase"]
    for k in ("phase", "title", "purpose"):
        assert k in nxt, f"recommended_next_phase missing field: {k}"
    counts = diag["upstream_summary"]["recommendation_counts"]
    any_model = int(counts.get("KEEP_FOR_MODEL_RESEARCH", 0)) >= 1
    # The live Phase 2K-C decision has a model-research survivor -> 2L-B.
    assert any_model, "expected at least one Phase 2K-C model-research candidate"
    assert nxt["phase"] == "2L-B", f"unexpected next phase {nxt['phase']!r}"
    assert nxt["title"] == "Model-Free Walk-Forward Residual Signal Validation"
    assert isinstance(nxt["purpose"], str) and nxt["purpose"].strip()


# --------------------------------------------------------------------------- #
# 15. Doc has all required guardrail phrases
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
