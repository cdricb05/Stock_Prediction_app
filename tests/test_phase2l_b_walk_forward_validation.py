"""Phase 2L-B tests for the Model-Free Walk-Forward Residual Signal Validation analyzer.

These tests prove the analyzer implements the Phase 2L-A model-free walk-forward
design without training, fitting, or serving any model: it compiles and imports
without side effects; it references only the Phase 2L-A / 2K-C / 2K-B JSON summaries
and the local Phase 2G real-data artifacts; it writes only
research/output/phase2l_b_walk_forward_validation.json; it never imports api_server or
Paper Trader; its source contains no deploy / gcloud / SSH / service / DB-write /
migration logic and no model-training / fitting tokens; and the results JSON it
produces carries every required field and safety flag, targets avg_dollar_volume_21d
at the 63d horizon with a 63-session embargo, builds strictly sequential folds whose
validation windows are chronologically after training with no train/validation label
overlap after the embargo, attaches per-fold metrics and aggregate metrics, evaluates
the Phase 2L-A pass/fail gates, issues a recommendation drawn only from the allowed
vocabulary, and routes to a next phase consistent with that recommendation. The
companion doc must contain the guardrail phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase2l_b_walk_forward_validation.py
  * without pytest: python tests/test_phase2l_b_walk_forward_validation.py
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
    _REPO_ROOT, "research", "analyze_phase2l_b_walk_forward_validation.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase2l_b_walk_forward_validation_v1.md")
_DESIGN_2LA_JSON = os.path.join(
    _REPO_ROOT, "research", "output", "phase2l_a_walk_forward_design.json")

# The sole Phase 2K-C model-research candidate this validation must target.
_EXPECTED_CANDIDATE = "avg_dollar_volume_21d"
_EXPECTED_HORIZON = 63

# Allowed recommendation values and the next-phase routing each implies.
_ALLOWED_RECOMMENDATIONS = {"WALK_FORWARD_PASS", "WALK_FORWARD_FAIL", "NEED_MORE_DATA"}
_NEXT_PHASE_FOR = {
    "WALK_FORWARD_PASS": "2L-C",
    "WALK_FORWARD_FAIL": "2K-D",
    "NEED_MORE_DATA": "2K-D",
}

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

# Forbidden model-training / fitting tokens: this phase is model-free.
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

# Required top-level fields of the results JSON.
_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read", "candidate", "residualization_config",
    "walk_forward_config", "folds", "fold_metrics", "aggregate_metrics",
    "pass_fail_gates", "recommendation", "interpretation", "recommended_next_phase",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_edge_claimed",
)
_REQUIRED_TRUE = ("no_trading", "no_orders", "no_automation")

# Per-fold metric keys required by the design.
_REQUIRED_FOLD_METRIC_KEYS = (
    "fold_id", "train_start", "train_end", "embargo_start", "embargo_end",
    "validation_start", "validation_end", "n_train_dates", "n_validation_dates",
    "n_validation_rows", "validation_mean_residual_rank_ic",
    "validation_information_ratio", "top_minus_bottom_residual_spread",
    "top_decile_residual_hit_rate", "top_quintile_ticker_concentration",
    "top3_long_leg_share", "effective_validation_obs",
)
# The Phase 2L-A pass/fail gate ids the results must evaluate.
_REQUIRED_GATE_IDS = (
    "validation_mean_ic_above_floor",
    "majority_validation_windows_same_sign",
    "bootstrap_ci_excludes_zero_in_validation",
    "no_single_validation_period_drives_result",
    "no_excessive_ticker_concentration",
    "no_regime_only_dependence",
    "no_degradation_vs_phase2k_c",
    "no_lookahead_leakage",
    "enough_effective_observations_after_embargo",
)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _import_analyzer():
    spec = importlib.util.spec_from_file_location("phase2l_b_analyzer", _ANALYZER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Run the validation at most once per test process and cache both the returned
# dict and the on-disk JSON.
_DIAG_CACHE = {}


def _diag():
    if "diag" not in _DIAG_CACHE:
        mod = _import_analyzer()
        out = os.path.join(tempfile.mkdtemp(), "validation.json")
        _DIAG_CACHE["diag"] = mod.run(output_path=out)
        _DIAG_CACHE["on_disk"] = json.loads(_read(out))
    return _DIAG_CACHE["diag"]


def _design_candidate():
    """The candidate the Phase 2L-A design selected for walk-forward validation."""
    design = json.loads(_read(_DESIGN_2LA_JSON))
    return design["candidate_selected"]["feature"]


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
        "DESIGN_2LA_JSON": "research/output/phase2l_a_walk_forward_design.json",
        "INPUT_2KC_JSON": "research/output/phase2k_c_residual_robustness.json",
        "INPUT_2KB_JSON": "research/output/phase2k_b_residual_signal.json",
        "SCORED_CSV": "research/output/phase2g_real_data_scored.csv",
        "PRICE_HISTORY_CSV": "research/output/phase2g_price_history_real.csv",
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
# 3. Writes only the Phase 2L-B results JSON
# --------------------------------------------------------------------------- #
def test_writes_only_results_json():
    mod = _import_analyzer()
    assert mod.RESULTS_JSON.replace("\\", "/").endswith(
        "research/output/phase2l_b_walk_forward_validation.json")
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
    assert write_opens == 1, "exactly one write-open (the results JSON)"


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
# 7. No model training / fitting logic (this phase is model-free)
# --------------------------------------------------------------------------- #
def test_no_model_training_tokens():
    text = _read(_ANALYZER)
    hits = [tok for tok in _FORBIDDEN_MODEL_TOKENS if tok in text]
    assert not hits, f"analyzer contains model-training/fitting token(s): {hits}"
    # The results must declare model-free explicitly.
    diag = _diag()
    assert diag["walk_forward_config"]["model_free"] is True
    assert diag["walk_forward_config"]["fitted_model"] is False
    assert diag["interpretation"]["model_trained"] is False
    assert diag["interpretation"]["model_fitted"] is False
    assert diag["interpretation"]["model_candidate_created"] is False
    assert diag["interpretation"]["authorized_to_train_model"] is False


# --------------------------------------------------------------------------- #
# 8. Output JSON: required fields and safety flags
# --------------------------------------------------------------------------- #
def test_output_json_required_fields():
    diag = _diag()
    on_disk = _DIAG_CACHE["on_disk"]
    for d in (diag, on_disk):
        for k in _REQUIRED_JSON_FIELDS:
            assert k in d, f"results JSON missing field: {k}"
        assert d["phase"] == "2L-B"
        for k in _REQUIRED_FALSE:
            assert d[k] is False, f"{k} must be false"
        for k in _REQUIRED_TRUE:
            assert d[k] is True, f"{k} must be true"
        ir = d["inputs_read"]
        assert ir["phase2l_a_design_json"].replace("\\", "/").endswith(
            "research/output/phase2l_a_walk_forward_design.json")
        assert ir["scored_csv"].replace("\\", "/").endswith(
            "research/output/phase2g_real_data_scored.csv")
        assert ir["price_history_csv"].replace("\\", "/").endswith(
            "research/output/phase2g_price_history_real.csv")


# --------------------------------------------------------------------------- #
# 9. Candidate is avg_dollar_volume_21d at the 63d horizon
# --------------------------------------------------------------------------- #
def test_candidate_is_avg_dollar_volume_21d():
    diag = _diag()
    cand = diag["candidate"]
    assert cand["feature"] == _EXPECTED_CANDIDATE, \
        f"candidate {cand['feature']!r} != {_EXPECTED_CANDIDATE!r}"
    assert cand["primary_horizon_days"] == _EXPECTED_HORIZON
    # And it matches the live Phase 2L-A design selection.
    assert _design_candidate() == _EXPECTED_CANDIDATE
    assert cand["is_production_edge"] is False


# --------------------------------------------------------------------------- #
# 10. walk_forward_config exists with a 63-session embargo == horizon
# --------------------------------------------------------------------------- #
def test_walk_forward_config_present():
    wf = _diag()["walk_forward_config"]
    assert wf["candidate_signal"] == _EXPECTED_CANDIDATE
    assert wf["primary_horizon_days"] == _EXPECTED_HORIZON
    assert wf["embargo_sessions"] == _EXPECTED_HORIZON
    assert wf["embargo_equals_forward_horizon"] is True
    assert wf["no_random_cross_validation"] is True
    assert wf["test_period_always_after_training_period"] is True
    assert wf["no_overlap_between_train_and_validation_label_windows"] is True
    assert int(wf["validation_sessions"]) == 63
    assert int(wf["min_train_sessions"]) >= 252


# --------------------------------------------------------------------------- #
# 11. Folds exist (or NEED_MORE_DATA with a clear reason)
# --------------------------------------------------------------------------- #
def test_folds_exist_or_need_more_data():
    diag = _diag()
    folds = diag["folds"]
    rec = diag["recommendation"]
    if not folds:
        assert rec["recommendation"] == "NEED_MORE_DATA", \
            "no folds must yield NEED_MORE_DATA"
        assert isinstance(rec["reason"], str) and rec["reason"].strip()
    else:
        # fold_metrics exist for every fold, 1:1 by fold_id.
        fm = diag["fold_metrics"]
        assert len(fm) == len(folds), "fold_metrics must cover every fold"
        assert {f["fold_id"] for f in folds} == {m["fold_id"] for m in fm}


# --------------------------------------------------------------------------- #
# 12. Validation is chronologically after training; embargo removes overlap
# --------------------------------------------------------------------------- #
def test_validation_after_training_no_overlap():
    diag = _diag()
    horizon = diag["candidate"]["primary_horizon_days"]
    for f in diag["folds"]:
        # Index-level: validation strictly after training, with a horizon embargo.
        assert f["validation_start_idx"] > f["train_end_idx"], \
            "validation must start after training"
        assert f["embargo_sessions"] == horizon
        assert f["validation_start_idx"] - f["train_end_idx"] >= f["embargo_sessions"], \
            "an embargo gap must separate train and validation"
        # The last training label's forward window must end before the first
        # validation date (no train/validation label overlap after the embargo).
        last_train_label_end = (f["train_end_idx"] - 1) + horizon
        assert f["validation_start_idx"] > last_train_label_end, \
            "train/validation label windows must not overlap after the embargo"
        # Date-level sanity (ISO strings sort chronologically).
        assert f["validation_start"] > f["train_end"]


# --------------------------------------------------------------------------- #
# 13. Per-fold metrics carry the required keys
# --------------------------------------------------------------------------- #
def test_fold_metrics_have_required_keys():
    fm = _diag()["fold_metrics"]
    for m in fm:
        for k in _REQUIRED_FOLD_METRIC_KEYS:
            assert k in m, f"fold_metrics entry missing key: {k}"
        # effective_validation_obs = n_validation_dates / horizon (>= 0).
        assert m["effective_validation_obs"] is None or m["effective_validation_obs"] >= 0


# --------------------------------------------------------------------------- #
# 14. aggregate_metrics exists and is well formed
# --------------------------------------------------------------------------- #
def test_aggregate_metrics_present():
    agg = _diag()["aggregate_metrics"]
    for k in ("n_folds", "total_effective_validation_obs",
              "pooled_validation_mean_residual_rank_ic",
              "frac_validation_windows_same_sign", "bootstrap_pooled_ic_ci",
              "leave_one_fold_out", "regimes", "degradation_vs_phase2k_c"):
        assert k in agg, f"aggregate_metrics missing key: {k}"
    boot = agg["bootstrap_pooled_ic_ci"]
    assert "ci_low" in boot and "ci_high" in boot and "excludes_zero" in boot


# --------------------------------------------------------------------------- #
# 15. pass_fail_gates exist and cover the Phase 2L-A gate set
# --------------------------------------------------------------------------- #
def test_pass_fail_gates_present():
    gates = _diag()["pass_fail_gates"]
    assert isinstance(gates, list) and len(gates) >= 5
    ids = {g["id"] for g in gates}
    for required in _REQUIRED_GATE_IDS:
        assert required in ids, f"pass_fail_gates missing gate: {required}"
    for g in gates:
        assert "passed" in g and isinstance(g["passed"], bool)
        assert "threshold" in g


# --------------------------------------------------------------------------- #
# 16. Recommendation is from the allowed vocabulary and consistent with the gates
# --------------------------------------------------------------------------- #
def test_recommendation_allowed_and_consistent():
    diag = _diag()
    rec = diag["recommendation"]
    assert rec["recommendation"] in _ALLOWED_RECOMMENDATIONS, \
        f"disallowed recommendation {rec['recommendation']!r}"
    assert rec["is_production_edge"] is False
    assert rec["authorizes_model_training"] is False
    gates = diag["pass_fail_gates"]
    all_pass = all(g["passed"] for g in gates)
    if rec["recommendation"] == "WALK_FORWARD_PASS":
        # A pass requires every gate to pass and enough data.
        assert all_pass, "WALK_FORWARD_PASS requires all gates to pass"
        assert rec["enough_data"] is True


# --------------------------------------------------------------------------- #
# 17. recommended_next_phase is consistent with the recommendation
# --------------------------------------------------------------------------- #
def test_recommended_next_phase_consistent():
    diag = _diag()
    rec = diag["recommendation"]["recommendation"]
    nxt = diag["recommended_next_phase"]
    for k in ("phase", "title", "purpose"):
        assert k in nxt, f"recommended_next_phase missing field: {k}"
    assert nxt["phase"] == _NEXT_PHASE_FOR[rec], \
        f"recommendation {rec!r} should route to {_NEXT_PHASE_FOR[rec]!r}, " \
        f"got {nxt['phase']!r}"
    assert isinstance(nxt["purpose"], str) and nxt["purpose"].strip()


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
