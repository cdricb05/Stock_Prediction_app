"""Phase 2K-K tests for the Expanded-Dataset Model-Free Alpha Screen analyzer.

These tests prove the analyzer is a disciplined, read-only model-free screen: it compiles
and imports cleanly; it references the expected small input summaries and the D: dataset
paths; it reads the Phase 2K-J result and confirms it routed here (REFRESH_ALPHA_BACKLOG ->
2K-K, the liquidity candidate stopped); it screens the prioritized price/volume candidates
while excluding standalone avg_dollar_volume_21d; it emits only the allowed per-pair
verdict statuses; it never trains or fits a model; it carries every required output field
and safety flag; it imports neither api_server nor Paper Trader; it contains no forbidden
infrastructure / model-training / network tokens and never writes to the D: drive (exactly
one write-open, the small results JSON); and the doc carries the required guardrail phrases.

The committed results artifact is validated structurally (it may be a pre-execution
snapshot). The live-compute assertions run only when the D: price-history CSV is present
(the host where the screen is validated manually); otherwise they are skipped so the suite
stays green in environments without the D: dataset.

Runs two ways:
  * under pytest:   pytest tests/test_phase2k_k_expanded_alpha_screen.py
  * without pytest: python tests/test_phase2k_k_expanded_alpha_screen.py
The __main__ block discovers every test_* function, runs it, prints PASS/FAIL/SKIP, and
exits non-zero on any failure (the GCP venv has no pytest).
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
    _REPO_ROOT, "research", "analyze_phase2k_k_expanded_alpha_screen.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase2k_k_expanded_alpha_screen_v1.md")
_COMMITTED_RESULTS = os.path.join(
    _REPO_ROOT, "research", "output", "phase2k_k_expanded_alpha_screen.json")

_STOPPED_CANDIDATE = "avg_dollar_volume_21d"


class _Skip(Exception):
    """Raised to mark a test skipped (the D: dataset is absent in this environment)."""


# The small read-only input summaries this phase references.
_EXPECTED_INPUT_BASENAMES = (
    "phase2k_j_alpha_backlog_refresh.json",
    "phase2k_i_expanded_residual_retest.json",
    "phase2k_h_manual_build_run_summary.json",
)

# The D: dataset paths this phase reads (read-only).
_EXPECTED_D_BASENAMES = (
    "phase2k_g_expanded_price_history_free.csv",
    "phase2k_g_data_quality_report.json",
    "phase2k_g_data_build_summary.json",
    "phase2k_g_survivorship_caveat.json",
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

# Forbidden model-training / fitting tokens (no model work here; residualization uses
# numpy.linalg.lstsq, which is not a fitted model).
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

# The analyzer reads the D: CSV (pandas read_csv is allowed) but must NEVER write / mutate
# D: data. Only write / delete tokens are forbidden here.
_FORBIDDEN_MUTATION_TOKENS = [
    "os." + "remove", "os." + "rename", "os." + "unlink", "shutil", "rm" + "tree",
    "to_csv", "to_sql", "to_parquet",
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
    "phase", "generated_at", "inputs_read", "data_root",
    "upstream_phase2k_j_summary", "dataset_summary", "survivorship_caveat_summary",
    "stopped_candidate_policy", "screened_candidates", "excluded_candidates",
    "feature_engineering_summary", "residualization_config", "screen_metrics",
    "candidate_recommendations", "pass_fail_gates", "research_priorities",
    "recommendation", "interpretation", "recommended_next_phase",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_edge_claimed",
)
_REQUIRED_TRUE = ("no_trading", "no_orders", "no_automation")

_ALLOWED_PAIR_STATUSES = {
    "KEEP_FOR_WALK_FORWARD_CONFIRMATION", "RESEARCH_LEAD_ONLY", "DROP",
    "NEED_MORE_DATA_OR_PIT_UNIVERSE",
}
_ALLOWED_RECOMMENDATIONS = {
    "MODEL_FREE_SCREEN_HAS_CONFIRMATION_CANDIDATES",
    "MODEL_FREE_SCREEN_HAS_RESEARCH_LEADS_ONLY",
    "MODEL_FREE_SCREEN_ALL_DROPPED",
    "NEED_MORE_DATA_OR_PIT_UNIVERSE",
}


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _import_analyzer():
    spec = importlib.util.spec_from_file_location("phase2k_k_analyzer_test", _ANALYZER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _d_csv_present(analyzer) -> bool:
    return os.path.isfile(analyzer.PRICE_HISTORY_CSV)


def _run_live(analyzer):
    """Run the analyzer against the real D: dataset, writing to a temp path.

    Skips (raises _Skip) when the D: price-history CSV is absent so the suite stays green
    in environments without the mounted dataset.
    """
    if not _d_csv_present(analyzer):
        raise _Skip("D: expanded price-history CSV not present in this environment")
    out = os.path.join(tempfile.mkdtemp(prefix="phase2k_k_"), "screen.json")
    res = analyzer.run(output_path=out)
    return res, out


# --------------------------------------------------------------------------- #
# 1. Analyzer compiles and imports
# --------------------------------------------------------------------------- #
def test_analyzer_compiles():
    compile(_read(_ANALYZER), _ANALYZER, "exec")


def test_analyzer_imports():
    _import_analyzer()


# --------------------------------------------------------------------------- #
# 2. Expected input summaries and D: paths are referenced
# --------------------------------------------------------------------------- #
def test_references_expected_inputs_and_d_paths():
    analyzer = _import_analyzer()
    text = _read(_ANALYZER)
    for base in _EXPECTED_INPUT_BASENAMES:
        assert base in text, f"analyzer does not reference expected input: {base}"
    for base in _EXPECTED_D_BASENAMES:
        assert base in text, f"analyzer does not reference expected D: file: {base}"
    assert "phase2k_g" in analyzer.DATA_ROOT
    assert analyzer.RESULTS_JSON.replace("\\", "/").endswith(
        "research/output/phase2k_k_expanded_alpha_screen.json")


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
# 6. No network logic, no D: mutation, exactly one write-open
# --------------------------------------------------------------------------- #
def test_analyzer_is_network_free_and_no_d_writes():
    text = _read(_ANALYZER)
    low = text.lower()
    net = [tok for tok in _FORBIDDEN_NETWORK_TOKENS if tok.lower() in low]
    assert not net, f"analyzer contains network/acquisition token(s): {net}"
    mut = [tok for tok in _FORBIDDEN_MUTATION_TOKENS if tok.lower() in low]
    assert not mut, f"analyzer contains D:-mutation token(s): {mut}"
    write_opens = 0
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
                    and "w" in str(node.args[1].value):
                write_opens += 1
    assert write_opens == 1, "analyzer must have exactly one write-open (the results JSON)"


# --------------------------------------------------------------------------- #
# 7. Committed results artifact: required fields, safety flags, structure
# --------------------------------------------------------------------------- #
def test_committed_results_artifact_valid():
    assert os.path.isfile(_COMMITTED_RESULTS), "committed screen JSON must exist"
    d = json.loads(_read(_COMMITTED_RESULTS))
    for k in _REQUIRED_JSON_FIELDS:
        assert k in d, f"committed screen JSON missing field: {k}"
    assert d["phase"] == "2K-K"
    for k in _REQUIRED_FALSE:
        assert d[k] is False, f"{k} must be false"
    for k in _REQUIRED_TRUE:
        assert d[k] is True, f"{k} must be true"
    # Recommendation + routing are within the allowed vocabulary and route to 2K-L.
    assert d["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert d["recommendation"]["create_model_candidate_now"] is False
    assert d["recommendation"]["train_model_now"] is False
    assert d["recommended_next_phase"]["phase"] == "2K-L"
    # Per-pair verdicts use only the allowed statuses.
    for cr in d["candidate_recommendations"]:
        assert cr["status"] in _ALLOWED_PAIR_STATUSES, f"bad status: {cr['status']}"


# --------------------------------------------------------------------------- #
# 8. Stopped candidate policy + exclusion (committed artifact)
# --------------------------------------------------------------------------- #
def test_stopped_candidate_excluded_not_screened():
    d = json.loads(_read(_COMMITTED_RESULTS))
    policy = d["stopped_candidate_policy"]
    assert policy["stopped_candidate"] == _STOPPED_CANDIDATE
    assert policy["status"] == "STOPPED_AFTER_EXPANDED_RETEST_FAIL"
    assert policy["excluded_as_standalone_alpha"] is True
    # avg_dollar_volume_21d appears in excluded_candidates (or the stopped policy)...
    excluded_features = {e["feature"] for e in d["excluded_candidates"]}
    assert (_STOPPED_CANDIDATE in excluded_features
            or policy["stopped_candidate"] == _STOPPED_CANDIDATE)
    # ...and is NOT screened as a standalone alpha candidate.
    screened_ids = {c["id"] for c in d["screened_candidates"]}
    screened_features = {c["feature"] for c in d["screened_candidates"]}
    assert _STOPPED_CANDIDATE not in screened_ids
    assert _STOPPED_CANDIDATE not in screened_features


# --------------------------------------------------------------------------- #
# 9. Screened candidates cover the prioritized price/volume families
# --------------------------------------------------------------------------- #
def test_screened_candidates_cover_families():
    d = json.loads(_read(_COMMITTED_RESULTS))
    ids = {c["id"] for c in d["screened_candidates"]}
    assert "residual_price_momentum_12_1" in ids
    assert any(i.startswith("short_horizon_residual_reversal") for i in ids), \
        "expected a short-horizon residual reversal candidate"
    assert any(i.startswith("volatility_adjusted_momentum") for i in ids), \
        "expected a volatility-adjusted momentum candidate"
    for c in d["screened_candidates"]:
        assert c["production_allowed"] is False


# --------------------------------------------------------------------------- #
# 10. Reads Phase 2K-J and confirms routing (committed artifact)
# --------------------------------------------------------------------------- #
def test_reads_phase2k_j_and_confirms_routing():
    d = json.loads(_read(_COMMITTED_RESULTS))
    kj = d["upstream_phase2k_j_summary"]
    assert kj["present"] is True
    assert kj["recommendation"] == "REFRESH_ALPHA_BACKLOG"
    assert kj["failed_candidate_status"] == "STOPPED_AFTER_EXPANDED_RETEST_FAIL"
    assert kj["standalone_liquidity_candidate_stopped"] is True
    assert kj["recommended_next_phase_is_2k_k"] is True
    assert kj["routing_confirmed"] is True


# --------------------------------------------------------------------------- #
# 11. Recommendation block disallows model work; routes to 2K-L
# --------------------------------------------------------------------------- #
def test_recommendation_disallows_model_work():
    d = json.loads(_read(_COMMITTED_RESULTS))
    rec = d["recommendation"]
    assert rec["create_model_candidate_now"] is False
    assert rec["train_model_now"] is False
    assert rec["deploy_now"] is False
    assert rec["production_edge_claimed"] is False
    interp = d["interpretation"]
    assert interp["model_trained"] is False
    assert interp["model_candidate_created"] is False
    assert interp["authorized_to_serve_model"] is False
    assert d["recommended_next_phase"]["phase"] == "2K-L"


# --------------------------------------------------------------------------- #
# 12. Leakage / no-look-ahead controls are declared
# --------------------------------------------------------------------------- #
def test_feature_and_label_controls():
    d = json.loads(_read(_COMMITTED_RESULTS))
    fes = d["feature_engineering_summary"]
    assert fes["all_features_trailing_point_in_time"] is True
    assert fes["labels_forward_filled"] is False
    rc = d["residualization_config"]
    assert rc["controls"] and isinstance(rc["controls"], list)
    assert set(rc["residual_label_columns"].keys()) >= {"5", "21", "63"}


# --------------------------------------------------------------------------- #
# 13. Doc has all required guardrail phrases
# --------------------------------------------------------------------------- #
def test_doc_has_required_phrases():
    text = _read(_DOC)
    missing = [p for p in _REQUIRED_DOC_PHRASES if p not in text]
    assert not missing, f"doc missing required phrase(s): {missing}"


# --------------------------------------------------------------------------- #
# 14. Live compute (only when the D: dataset is present): structure + invariants
# --------------------------------------------------------------------------- #
def test_live_screen_structure():
    analyzer = _import_analyzer()
    res, on_disk = _run_live(analyzer)  # may raise _Skip
    blob = json.loads(_read(on_disk))
    for d in (res, blob):
        assert d["phase"] == "2K-K"
        for k in _REQUIRED_JSON_FIELDS:
            assert k in d, f"live screen JSON missing field: {k}"
        for k in _REQUIRED_FALSE:
            assert d[k] is False
        for k in _REQUIRED_TRUE:
            assert d[k] is True
        # Stopped candidate is never screened as a standalone alpha.
        screened = {c["feature"] for c in d["screened_candidates"]} | {
            c["id"] for c in d["screened_candidates"]}
        assert _STOPPED_CANDIDATE not in screened
        # Verdicts and overall recommendation stay within the allowed vocabulary.
        for cr in d["candidate_recommendations"]:
            assert cr["status"] in _ALLOWED_PAIR_STATUSES
        assert d["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
        assert d["recommendation"]["create_model_candidate_now"] is False
        assert d["recommendation"]["train_model_now"] is False
        assert d["recommended_next_phase"]["phase"] == "2K-L"


def test_live_screen_routing_confirmed():
    analyzer = _import_analyzer()
    res, _ = _run_live(analyzer)  # may raise _Skip
    kj = res["upstream_phase2k_j_summary"]
    assert kj["routing_confirmed"] is True
    # When the D: CSV is present and the build is ready, the screen actually computes.
    assert res["screen_executed"] is True
    assert res["feature_engineering_summary"]["computed"] is True


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
            print(f"PASS {name}")
            passed += 1
        except _Skip as s:
            print(f"SKIP {name}: {s}")
            skipped += 1
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"FAIL {name}: {e}")
            failures.append((name, traceback.format_exc()))
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped, {len(tests)} total")
    if failures:
        print("\n--- failure details ---")
        for name, tb in failures:
            print(f"\n### {name}\n{tb}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
