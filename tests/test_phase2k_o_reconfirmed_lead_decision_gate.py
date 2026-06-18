"""Phase 2K-O tests for the Reconfirmed-Lead Decision Gate analyzer.

These tests prove the analyzer is a disciplined, read-only decision gate: it compiles and
imports cleanly; it references the expected small input summaries (Phase 2K-N / 2K-M / 2K-K /
2K-J / 2K-H) and reads NONE of the large D: dataset; it reads the Phase 2K-N result and
confirms it routed here (RESEARCH_LEADS_RECONFIRMED_BUT_NOT_CONFIRMABLE -> 2K-O, exactly 3
RESEARCH_LEAD_RECONFIRMED leads, no model candidate / no model training); it never proposes
confirmation while no lead is KEEP_FOR_CONFIRMATION_DESIGN; it emits only the allowed overall
recommendations and routes to Phase 2K-P; it runs no broad alpha screen and reads no D: CSV;
it never trains or fits a model; it carries every required output field and safety flag; it
imports neither api_server nor Paper Trader; it contains no forbidden infrastructure /
model-training / network tokens and writes exactly one file (the small results JSON); and the
doc carries the required guardrail phrases.

The committed results artifact is the real, deterministic decision-gate output (this gate
reads only small Git-tracked JSON summaries, so there is no D: dependency). A live test runs
the analyzer against the committed summaries in a temp dir and re-checks the verdict; it skips
only if the Phase 2K-N summary is absent.

Runs two ways:
  * under pytest:   pytest tests/test_phase2k_o_reconfirmed_lead_decision_gate.py
  * without pytest: python tests/test_phase2k_o_reconfirmed_lead_decision_gate.py
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
    _REPO_ROOT, "research", "analyze_phase2k_o_reconfirmed_lead_decision_gate.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase2k_o_reconfirmed_lead_decision_gate_v1.md")
_COMMITTED_RESULTS = os.path.join(
    _REPO_ROOT, "research", "output", "phase2k_o_reconfirmed_lead_decision_gate.json")
_PHASE2K_N_JSON = os.path.join(
    _REPO_ROOT, "research", "output", "phase2k_n_narrow_model_free_retest.json")

# The 3 reconfirmed lead ids expected from Phase 2K-N (and ONLY these).
_RECONFIRMED_LEAD_IDS = {
    "residual_price_momentum_12_1@5d",
    "short_horizon_residual_reversal_5d@21d",
    "short_horizon_residual_reversal_21d@21d",
}


class _Skip(Exception):
    """Raised to mark a test skipped (the upstream Phase 2K-N summary is absent)."""


# The small read-only input summaries this phase references.
_EXPECTED_INPUT_BASENAMES = (
    "phase2k_n_narrow_model_free_retest.json",
    "phase2k_m_targeted_lead_diagnostics.json",
    "phase2k_k_expanded_alpha_screen.json",
    "phase2k_j_alpha_backlog_refresh.json",
    "phase2k_h_manual_build_run_summary.json",
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

# Forbidden model-training / fitting tokens (no model work here at all).
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

# This gate reads ONLY small JSON summaries: it must never read the large D: price-history
# CSV nor write / mutate any dataset. read_csv / pandas / the D: CSV name are all forbidden.
_FORBIDDEN_D_READ_TOKENS = [
    "read_csv", "phase2k_g_expanded_price_history", "import pandas", "import numpy",
]
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
    "phase", "generated_at", "inputs_read",
    "upstream_phase2k_n_summary", "reconfirmed_leads", "blocker_diagnosis",
    "strategic_options", "decision_matrix", "selected_path", "rejected_paths",
    "implementation_constraints", "recommendation", "interpretation",
    "recommended_next_phase",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_edge_claimed",
    "model_trained", "model_candidate_created", "d_drive_read", "d_drive_written",
    "broad_alpha_screen_run",
)
_REQUIRED_TRUE = ("no_trading", "no_orders", "no_automation")

_ALLOWED_RECOMMENDATIONS = {
    "PROCEED_TO_SECTOR_RELATIVE_FEASIBILITY",
    "REQUIRE_POINT_IN_TIME_UNIVERSE_DECISION",
    "STOP_RECONFIRMED_SUBFLOOR_LEADS",
    "NO_ACTION_CONFIRMATION_BLOCKED",
}


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _import_analyzer():
    spec = importlib.util.spec_from_file_location("phase2k_o_analyzer_test", _ANALYZER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_live(analyzer):
    """Run the analyzer against the committed summaries, writing to a temp path.

    Skips (raises _Skip) only when the upstream Phase 2K-N summary is absent.
    """
    if not os.path.isfile(_PHASE2K_N_JSON):
        raise _Skip("upstream Phase 2K-N summary not present in this environment")
    out = os.path.join(tempfile.mkdtemp(prefix="phase2k_o_"), "gate.json")
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
# 2. Expected input summaries referenced; no D: dataset paths
# --------------------------------------------------------------------------- #
def test_references_expected_inputs():
    analyzer = _import_analyzer()
    text = _read(_ANALYZER)
    for base in _EXPECTED_INPUT_BASENAMES:
        assert base in text, f"analyzer does not reference expected input: {base}"
    assert analyzer.RESULTS_JSON.replace("\\", "/").endswith(
        "research/output/phase2k_o_reconfirmed_lead_decision_gate.json")


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
# 6. No network logic, no D: CSV read, no mutation, exactly one write-open
# --------------------------------------------------------------------------- #
def test_analyzer_is_network_free_and_reads_no_d_drive():
    text = _read(_ANALYZER)
    low = text.lower()
    net = [tok for tok in _FORBIDDEN_NETWORK_TOKENS if tok.lower() in low]
    assert not net, f"analyzer contains network/acquisition token(s): {net}"
    d_read = [tok for tok in _FORBIDDEN_D_READ_TOKENS if tok.lower() in low]
    assert not d_read, f"analyzer reads the large D: dataset (forbidden): {d_read}"
    mut = [tok for tok in _FORBIDDEN_MUTATION_TOKENS if tok.lower() in low]
    assert not mut, f"analyzer contains dataset-mutation token(s): {mut}"
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
    assert os.path.isfile(_COMMITTED_RESULTS), "committed decision-gate JSON must exist"
    d = json.loads(_read(_COMMITTED_RESULTS))
    for k in _REQUIRED_JSON_FIELDS:
        assert k in d, f"committed decision-gate JSON missing field: {k}"
    assert d["phase"] == "2K-O"
    for k in _REQUIRED_FALSE:
        assert d[k] is False, f"{k} must be false"
    for k in _REQUIRED_TRUE:
        assert d[k] is True, f"{k} must be true"
    rec = d["recommendation"]
    assert rec["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert rec["create_model_candidate_now"] is False
    assert rec["train_model_now"] is False
    assert rec["deploy_now"] is False
    assert rec["production_edge_claimed"] is False
    assert d["recommended_next_phase"]["phase"] == "2K-P"


# --------------------------------------------------------------------------- #
# 8. Reads Phase 2K-N and confirms routing (committed artifact)
# --------------------------------------------------------------------------- #
def test_reads_phase2k_n_and_confirms_routing():
    d = json.loads(_read(_COMMITTED_RESULTS))
    kn = d["upstream_phase2k_n_summary"]
    assert kn["present"] is True
    assert kn["phase"] == "2K-N"
    assert kn["retest_executed"] is True
    assert kn["overall_recommendation"] == "RESEARCH_LEADS_RECONFIRMED_BUT_NOT_CONFIRMABLE"
    assert kn["recommended_next_phase"]["phase"] == "2K-O"
    assert kn["create_model_candidate_now_false"] is True
    assert kn["train_model_now_false"] is True
    assert kn["all_three_leads_reconfirmed"] is True
    assert kn["routing_confirmed"] is True


# --------------------------------------------------------------------------- #
# 9. Exactly 3 reconfirmed leads; none KEEP_FOR_CONFIRMATION_DESIGN
# --------------------------------------------------------------------------- #
def test_exactly_three_reconfirmed_leads_none_kept():
    d = json.loads(_read(_COMMITTED_RESULTS))
    leads = d["reconfirmed_leads"]
    assert len(leads) == 3, "exactly 3 reconfirmed leads required"
    lead_ids = {l["lead_id"] for l in leads}
    assert lead_ids == _RECONFIRMED_LEAD_IDS, f"unexpected reconfirmed set: {lead_ids}"
    for l in leads:
        assert l["status"] == "RESEARCH_LEAD_RECONFIRMED", f"bad status: {l['status']}"
        assert l["status"] != "KEEP_FOR_CONFIRMATION_DESIGN"
    counts = d["upstream_phase2k_n_summary"]["pair_recommendation_counts"]
    assert counts["KEEP_FOR_CONFIRMATION_DESIGN"] == 0
    assert counts["RESEARCH_LEAD_RECONFIRMED"] == 3


# --------------------------------------------------------------------------- #
# 10. Blocker diagnosis: sub-floor IC is the primary blocker
# --------------------------------------------------------------------------- #
def test_blocker_diagnosis_sub_floor_primary():
    d = json.loads(_read(_COMMITTED_RESULTS))
    diag = d["blocker_diagnosis"]
    assert diag["n_leads"] == 3
    assert diag["primary_blocker_across_leads"] == "sub_floor_residual_ic"
    assert diag["n_leads_sub_floor_residual_ic"] == 3
    assert len(diag["per_lead"]) == 3
    for c in diag["per_lead"]:
        assert c["blockers"]["sub_floor_residual_ic"] is True


# --------------------------------------------------------------------------- #
# 11. Strategic options include the four paths; CONTINUE is rejected
# --------------------------------------------------------------------------- #
def test_strategic_options_and_continue_rejected():
    d = json.loads(_read(_COMMITTED_RESULTS))
    opts = {o["option"] for o in d["strategic_options"]}
    assert {"SECTOR_RELATIVE_FEATURE_FEASIBILITY", "POINT_IN_TIME_UNIVERSE_DECISION",
            "STOP_AND_REFRESH_BACKLOG", "CONTINUE_CONFIRMATION_ANYWAY"} <= opts
    cont = next(o for o in d["strategic_options"]
                if o["option"] == "CONTINUE_CONFIRMATION_ANYWAY")
    assert cont.get("rejected") is True
    assert cont.get("eligible") is False


# --------------------------------------------------------------------------- #
# 12. Recommendation disallows model work; routes to 2K-P
# --------------------------------------------------------------------------- #
def test_recommendation_disallows_model_work():
    d = json.loads(_read(_COMMITTED_RESULTS))
    rec = d["recommendation"]
    assert rec["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert rec["create_model_candidate_now"] is False
    assert rec["train_model_now"] is False
    assert rec["deploy_now"] is False
    assert rec["production_edge_claimed"] is False
    interp = d["interpretation"]
    assert interp["model_trained"] is False
    assert interp["model_candidate_created"] is False
    assert interp["authorized_to_serve_model"] is False
    assert interp["ran_broad_alpha_screen"] is False
    assert interp["candidate_set_expanded"] is False
    assert interp["read_large_d_drive_csv"] is False
    assert d["recommended_next_phase"]["phase"] == "2K-P"
    assert d["broad_alpha_screen_run"] is False
    assert d["d_drive_read"] is False


# --------------------------------------------------------------------------- #
# 13. Doc has all required guardrail phrases
# --------------------------------------------------------------------------- #
def test_doc_has_required_phrases():
    text = _read(_DOC)
    missing = [p for p in _REQUIRED_DOC_PHRASES if p not in text]
    assert not missing, f"doc missing required phrase(s): {missing}"


# --------------------------------------------------------------------------- #
# 14. Live run (reads the committed summaries only): structure + verdict
# --------------------------------------------------------------------------- #
def test_live_decision_gate_structure():
    analyzer = _import_analyzer()
    res, on_disk = _run_live(analyzer)  # may raise _Skip
    blob = json.loads(_read(on_disk))
    for d in (res, blob):
        assert d["phase"] == "2K-O"
        for k in _REQUIRED_JSON_FIELDS:
            assert k in d, f"live decision-gate JSON missing field: {k}"
        for k in _REQUIRED_FALSE:
            assert d[k] is False
        for k in _REQUIRED_TRUE:
            assert d[k] is True
        assert {l["lead_id"] for l in d["reconfirmed_leads"]} == _RECONFIRMED_LEAD_IDS
        for l in d["reconfirmed_leads"]:
            assert l["status"] == "RESEARCH_LEAD_RECONFIRMED"
        assert d["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
        assert d["recommendation"]["create_model_candidate_now"] is False
        assert d["recommendation"]["train_model_now"] is False
        assert d["recommended_next_phase"]["phase"] == "2K-P"
        assert d["upstream_phase2k_n_summary"]["routing_confirmed"] is True


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
