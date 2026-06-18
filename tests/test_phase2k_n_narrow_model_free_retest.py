"""Phase 2K-N tests for the Narrow Model-Free Retest analyzer.

These tests prove the analyzer is a disciplined, read-only, narrow model-free retest: it
compiles and imports cleanly; it references the expected small input summaries and the D:
dataset paths; it reads the Phase 2K-M result and confirms it routed here
(RUN_NARROW_MODEL_FREE_RETEST -> 2K-N, exactly 3 NARROW_RETEST_CANDIDATE leads, no model
candidate / no model training / no new D: screen); it retests EXACTLY the 3 pre-registered
candidate / horizon pairs and never expands the candidate set or runs a broad alpha screen;
it keeps avg_dollar_volume_21d excluded as a standalone alpha; it emits only the allowed
per-pair recommendations; it never trains or fits a model; it carries every required output
field and safety flag; it imports neither api_server nor Paper Trader; it contains no
forbidden infrastructure / model-training / network tokens and never writes to the D: drive
(exactly one write-open, the small results JSON); and the doc carries the required guardrail
phrases.

The committed results artifact is validated structurally (it may be a pre-execution
snapshot). The live-compute assertions run only when the D: price-history CSV is present
(the host where the retest is validated manually); otherwise they are skipped so the suite
stays green in environments without the D: dataset.

Runs two ways:
  * under pytest:   pytest tests/test_phase2k_n_narrow_model_free_retest.py
  * without pytest: python tests/test_phase2k_n_narrow_model_free_retest.py
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
    _REPO_ROOT, "research", "analyze_phase2k_n_narrow_model_free_retest.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase2k_n_narrow_model_free_retest_v1.md")
_COMMITTED_RESULTS = os.path.join(
    _REPO_ROOT, "research", "output", "phase2k_n_narrow_model_free_retest.json")

_STOPPED_CANDIDATE = "avg_dollar_volume_21d"

# The 3 pre-registered candidate / horizon lead ids (and ONLY these).
_PRE_REGISTERED_LEAD_IDS = {
    "residual_price_momentum_12_1@5d",
    "short_horizon_residual_reversal_5d@21d",
    "short_horizon_residual_reversal_21d@21d",
}
# The leads explicitly excluded from this phase's retest.
_EXCLUDED_LEAD_IDS = {
    "short_horizon_residual_reversal_5d@5d",
    "short_horizon_residual_reversal_21d@5d",
}


class _Skip(Exception):
    """Raised to mark a test skipped (the D: dataset is absent in this environment)."""


# The small read-only input summaries this phase references.
_EXPECTED_INPUT_BASENAMES = (
    "phase2k_m_targeted_lead_diagnostics.json",
    "phase2k_l_research_lead_diagnostics.json",
    "phase2k_k_expanded_alpha_screen.json",
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
# numpy.linalg.lstsq, which is a per-date linear projection, not a fitted model).
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
    "upstream_phase2k_m_summary", "data_quality_summary", "survivorship_caveat_summary",
    "pre_registered_candidate_set", "explicitly_excluded_candidates",
    "feature_engineering_summary", "residualization_config", "narrow_retest_results",
    "pair_recommendations", "gate_summary", "overall_recommendation", "interpretation",
    "recommended_next_phase",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_edge_claimed",
    "model_trained", "model_candidate_created", "ran_broad_alpha_screen",
    "candidate_set_expanded", "d_drive_written",
)
_REQUIRED_TRUE = ("no_trading", "no_orders", "no_automation")

_ALLOWED_PAIR_RECS = {
    "KEEP_FOR_CONFIRMATION_DESIGN", "RESEARCH_LEAD_RECONFIRMED",
    "DROP_AFTER_NARROW_RETEST", "NEED_POINT_IN_TIME_UNIVERSE",
}
_ALLOWED_RECOMMENDATIONS = {
    "PROCEED_TO_CONFIRMATION_DESIGN",
    "RESEARCH_LEADS_RECONFIRMED_BUT_NOT_CONFIRMABLE",
    "DROP_NARROW_RETEST_LEADS",
    "NEED_POINT_IN_TIME_UNIVERSE_BEFORE_CONFIRMATION",
}


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _import_analyzer():
    spec = importlib.util.spec_from_file_location("phase2k_n_analyzer_test", _ANALYZER)
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
    out = os.path.join(tempfile.mkdtemp(prefix="phase2k_n_"), "retest.json")
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
        "research/output/phase2k_n_narrow_model_free_retest.json")


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
# 4. No Paper Trader import / reference
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
    assert os.path.isfile(_COMMITTED_RESULTS), "committed retest JSON must exist"
    d = json.loads(_read(_COMMITTED_RESULTS))
    for k in _REQUIRED_JSON_FIELDS:
        assert k in d, f"committed retest JSON missing field: {k}"
    assert d["phase"] == "2K-N"
    for k in _REQUIRED_FALSE:
        assert d[k] is False, f"{k} must be false"
    for k in _REQUIRED_TRUE:
        assert d[k] is True, f"{k} must be true"
    # Overall recommendation + routing are within the allowed vocabulary and route to 2K-O.
    assert d["overall_recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert d["overall_recommendation"]["create_model_candidate_now"] is False
    assert d["overall_recommendation"]["train_model_now"] is False
    assert d["recommended_next_phase"]["phase"] == "2K-O"
    # Per-pair recommendations use only the allowed statuses.
    for cr in d["pair_recommendations"]:
        assert cr["status"] in _ALLOWED_PAIR_RECS, f"bad status: {cr['status']}"


# --------------------------------------------------------------------------- #
# 8. Exactly 3 pre-registered candidates; candidate set not expanded
# --------------------------------------------------------------------------- #
def test_exactly_three_pre_registered_candidates():
    d = json.loads(_read(_COMMITTED_RESULTS))
    prereg = d["pre_registered_candidate_set"]
    assert len(prereg) == 3, "exactly 3 pre-registered candidate/horizon pairs required"
    lead_ids = {c["lead_id"] for c in prereg}
    assert lead_ids == _PRE_REGISTERED_LEAD_IDS, f"unexpected pre-registered set: {lead_ids}"
    # The retest results and pair recommendations cover exactly those 3 pairs.
    assert {c["lead_id"] for c in d["pair_recommendations"]} == _PRE_REGISTERED_LEAD_IDS
    assert {c["lead_id"] for c in d["narrow_retest_results"]} == _PRE_REGISTERED_LEAD_IDS
    assert d["candidate_set_expanded"] is False
    assert d["ran_broad_alpha_screen"] is False
    for c in prereg:
        assert c["production_allowed"] is False


# --------------------------------------------------------------------------- #
# 9. Excluded leads (hold-for-sector + stopped liquidity) are not retested
# --------------------------------------------------------------------------- #
def test_excluded_leads_not_retested():
    d = json.loads(_read(_COMMITTED_RESULTS))
    retested = {c["lead_id"] for c in d["pair_recommendations"]}
    # The two hold-for-sector-relative leads are never retested.
    assert not (_EXCLUDED_LEAD_IDS & retested), "hold-for-sector lead retested in 2K-N"
    # avg_dollar_volume_21d is excluded as a standalone alpha and never retested / ranked.
    excluded_candidates = {e.get("candidate") for e in d["explicitly_excluded_candidates"]}
    excluded_features = {e.get("feature") for e in d["explicitly_excluded_candidates"]}
    assert (_STOPPED_CANDIDATE in excluded_candidates
            or _STOPPED_CANDIDATE in excluded_features)
    prereg_candidates = {c["candidate"] for c in d["pre_registered_candidate_set"]}
    prereg_features = {c["feature"] for c in d["pre_registered_candidate_set"]}
    assert _STOPPED_CANDIDATE not in prereg_candidates
    assert _STOPPED_CANDIDATE not in prereg_features
    # The two hold-for-sector leads appear in the explicit exclusion list.
    assert _EXCLUDED_LEAD_IDS <= {e.get("lead_id") for e in d["explicitly_excluded_candidates"]}


# --------------------------------------------------------------------------- #
# 10. Reads Phase 2K-M and confirms routing (committed artifact)
# --------------------------------------------------------------------------- #
def test_reads_phase2k_m_and_confirms_routing():
    d = json.loads(_read(_COMMITTED_RESULTS))
    km = d["upstream_phase2k_m_summary"]
    assert km["present"] is True
    assert km["recommendation"] == "RUN_NARROW_MODEL_FREE_RETEST"
    assert km["n_narrow_retest_candidates"] == 3
    assert km["recommended_next_phase_is_2k_n"] is True
    assert km["narrow_retest_candidate_count_is_3"] is True
    assert km["pre_registered_pairs_match_phase2k_m_narrow_leads"] is True
    assert km["create_model_candidate_now_false"] is True
    assert km["train_model_now_false"] is True
    assert km["ran_new_d_screen_false"] is True
    assert km["routing_confirmed"] is True


# --------------------------------------------------------------------------- #
# 11. Recommendation block disallows model work; routes to 2K-O
# --------------------------------------------------------------------------- #
def test_recommendation_disallows_model_work():
    d = json.loads(_read(_COMMITTED_RESULTS))
    rec = d["overall_recommendation"]
    assert rec["create_model_candidate_now"] is False
    assert rec["train_model_now"] is False
    assert rec["deploy_now"] is False
    assert rec["production_edge_claimed"] is False
    interp = d["interpretation"]
    assert interp["model_trained"] is False
    assert interp["model_candidate_created"] is False
    assert interp["authorized_to_serve_model"] is False
    assert interp["candidate_set_expanded"] is False
    assert interp["ran_broad_alpha_screen"] is False
    assert d["recommended_next_phase"]["phase"] == "2K-O"


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
    # Only the pre-registered horizons (5d, 21d) are exercised.
    assert set(rc["residual_label_columns"].keys()) == {"5", "21"}


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
def test_live_retest_structure():
    analyzer = _import_analyzer()
    res, on_disk = _run_live(analyzer)  # may raise _Skip
    blob = json.loads(_read(on_disk))
    for d in (res, blob):
        assert d["phase"] == "2K-N"
        for k in _REQUIRED_JSON_FIELDS:
            assert k in d, f"live retest JSON missing field: {k}"
        for k in _REQUIRED_FALSE:
            assert d[k] is False
        for k in _REQUIRED_TRUE:
            assert d[k] is True
        # Exactly the 3 pre-registered pairs are retested; the set is never expanded.
        assert {c["lead_id"] for c in d["pair_recommendations"]} == _PRE_REGISTERED_LEAD_IDS
        assert d["candidate_set_expanded"] is False
        assert d["ran_broad_alpha_screen"] is False
        # The stopped liquidity candidate is never retested as a standalone alpha.
        prereg_features = {c["feature"] for c in d["pre_registered_candidate_set"]} | {
            c["candidate"] for c in d["pre_registered_candidate_set"]}
        assert _STOPPED_CANDIDATE not in prereg_features
        # Recommendations stay within the allowed vocabulary.
        for cr in d["pair_recommendations"]:
            assert cr["status"] in _ALLOWED_PAIR_RECS
        assert d["overall_recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
        assert d["overall_recommendation"]["create_model_candidate_now"] is False
        assert d["overall_recommendation"]["train_model_now"] is False
        assert d["recommended_next_phase"]["phase"] == "2K-O"


def test_live_retest_routing_and_execution():
    analyzer = _import_analyzer()
    res, _ = _run_live(analyzer)  # may raise _Skip
    km = res["upstream_phase2k_m_summary"]
    assert km["routing_confirmed"] is True
    # When the D: CSV is present and routing/build are ready, the retest actually computes.
    assert res["retest_executed"] is True
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
