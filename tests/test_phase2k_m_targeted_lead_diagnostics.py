"""Phase 2K-M tests for the Targeted-Lead Diagnostics analyzer.

These tests prove the analyzer is a disciplined, read-only diagnostic of the 5 Phase 2K-L
TARGETED_DIAGNOSTIC_CANDIDATE leads: it compiles and imports cleanly; it references the
expected small input summaries; it reads the Phase 2K-L diagnostics and confirms it routed
here (TARGETED_DIAGNOSTICS_FOR_BEST_RESEARCH_LEADS -> 2K-M, exactly 5 targeted candidates);
it diagnoses only TARGETED_DIAGNOSTIC_CANDIDATE leads, each sourced from a RESEARCH_LEAD_ONLY
Phase 2K-K pair, while keeping the stopped liquidity candidate excluded; it emits only the
allowed per-lead follow-up types and overall recommendations; it never trains or fits a
model and runs no new D: screen; it carries every required output field and safety flag; it
imports neither api_server nor the trading app; it contains no forbidden infrastructure /
model-training / network tokens, reads no large D: CSV, and never writes to the D: drive
(exactly one write-open, the small results JSON); and the doc carries the required guardrail
phrases.

The committed results artifact is validated structurally. A live recompute test re-runs
build_results() against the committed Phase 2K-L JSON and checks the invariants; it skips
when the Phase 2K-L input is absent so the suite stays green in stripped environments.

Runs two ways:
  * under pytest:   pytest tests/test_phase2k_m_targeted_lead_diagnostics.py
  * without pytest: python tests/test_phase2k_m_targeted_lead_diagnostics.py
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
    _REPO_ROOT, "research", "analyze_phase2k_m_targeted_lead_diagnostics.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase2k_m_targeted_lead_diagnostics_v1.md")
_COMMITTED_RESULTS = os.path.join(
    _REPO_ROOT, "research", "output", "phase2k_m_targeted_lead_diagnostics.json")
_PHASE2K_L_INPUT = os.path.join(
    _REPO_ROOT, "research", "output", "phase2k_l_research_lead_diagnostics.json")

_STOPPED_CANDIDATE = "avg_dollar_volume_21d"
_TARGETED = "TARGETED_DIAGNOSTIC_CANDIDATE"
_LEAD_STATUS = "RESEARCH_LEAD_ONLY"


class _Skip(Exception):
    """Raised to mark a test skipped (a required upstream input is absent)."""


# The small read-only input summaries this phase references.
_EXPECTED_INPUT_BASENAMES = (
    "phase2k_l_research_lead_diagnostics.json",
    "phase2k_k_expanded_alpha_screen.json",
    "phase2k_j_alpha_backlog_refresh.json",
    "phase2k_i_expanded_residual_retest.json",
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

# Phase 2K-M reads ONLY small JSON summaries — it must NOT read the large D: CSV and must
# never write / mutate D: data. read_csv / pandas / numpy are forbidden here too.
_FORBIDDEN_D_AND_MUTATION_TOKENS = [
    "read_csv", "to_csv", "to_sql", "to_parquet", "pandas", "numpy",
    "os." + "remove", "os." + "rename", "os." + "unlink", "shutil", "rm" + "tree",
    "expanded_price_history", "phase2k_g",
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
    "upstream_phase2k_l_summary", "stopped_candidate_policy",
    "survivorship_caveat_carried_forward", "targeted_leads", "targeted_lead_rankings",
    "family_diagnostics", "horizon_diagnostics", "blocker_analysis", "narrow_retest_plan",
    "recommendation", "interpretation", "recommended_next_phase",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_edge_claimed",
    "ran_new_d_screen", "read_large_d_csv",
)
_REQUIRED_TRUE = ("no_trading", "no_orders", "no_automation")

_ALLOWED_FOLLOW_UPS = {
    "NARROW_RETEST_CANDIDATE", "DIAGNOSTIC_ONLY", "HOLD_FOR_SECTOR_RELATIVE_VARIANT",
}
_ALLOWED_RECOMMENDATIONS = {
    "RUN_NARROW_MODEL_FREE_RETEST",
    "HOLD_FOR_SECTOR_RELATIVE_FEATURES",
    "STOP_TARGETED_LEADS_AND_REFRESH_BACKLOG",
}


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _import_analyzer():
    spec = importlib.util.spec_from_file_location("phase2k_m_analyzer_test", _ANALYZER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _committed():
    return json.loads(_read(_COMMITTED_RESULTS))


def _run_live(analyzer):
    """Re-run the analyzer against the committed Phase 2K-L JSON, writing to a temp path.

    Skips (raises _Skip) when the Phase 2K-L input is absent so the suite stays green in
    stripped environments.
    """
    if not os.path.isfile(_PHASE2K_L_INPUT):
        raise _Skip("Phase 2K-L input JSON not present in this environment")
    out = os.path.join(tempfile.mkdtemp(prefix="phase2k_m_"), "diagnostics.json")
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
# 2. Expected input summaries are referenced
# --------------------------------------------------------------------------- #
def test_references_expected_inputs():
    analyzer = _import_analyzer()
    text = _read(_ANALYZER)
    for base in _EXPECTED_INPUT_BASENAMES:
        assert base in text, f"analyzer does not reference expected input: {base}"
    assert analyzer.RESULTS_JSON.replace("\\", "/").endswith(
        "research/output/phase2k_m_targeted_lead_diagnostics.json")


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
# 4. No trading-app import; the analyzer source must not contain "paper_trader"
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
# 6. Network-free, no large D: CSV read, no D: mutation, exactly one write-open
# --------------------------------------------------------------------------- #
def test_analyzer_is_network_free_no_d_csv_no_d_writes():
    text = _read(_ANALYZER)
    low = text.lower()
    net = [tok for tok in _FORBIDDEN_NETWORK_TOKENS if tok.lower() in low]
    assert not net, f"analyzer contains network/acquisition token(s): {net}"
    dmut = [tok for tok in _FORBIDDEN_D_AND_MUTATION_TOKENS if tok.lower() in low]
    assert not dmut, f"analyzer reads the large D: CSV or mutates data: {dmut}"
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
    assert os.path.isfile(_COMMITTED_RESULTS), "committed diagnostics JSON must exist"
    d = _committed()
    for k in _REQUIRED_JSON_FIELDS:
        assert k in d, f"committed diagnostics JSON missing field: {k}"
    assert d["phase"] == "2K-M"
    for k in _REQUIRED_FALSE:
        assert d[k] is False, f"{k} must be false"
    for k in _REQUIRED_TRUE:
        assert d[k] is True, f"{k} must be true"
    assert d["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert d["recommendation"]["create_model_candidate_now"] is False
    assert d["recommendation"]["train_model_now"] is False
    assert d["recommendation"]["deploy_now"] is False
    assert d["recommendation"]["ran_new_d_screen"] is False
    assert d["recommended_next_phase"]["phase"] == "2K-N"


# --------------------------------------------------------------------------- #
# 8. Reads Phase 2K-L and confirms routing (committed artifact)
# --------------------------------------------------------------------------- #
def test_reads_phase2k_l_and_confirms_routing():
    d = _committed()
    kl = d["upstream_phase2k_l_summary"]
    assert kl["present"] is True
    assert kl["phase"] == "2K-L"
    assert kl["recommendation"] == "TARGETED_DIAGNOSTICS_FOR_BEST_RESEARCH_LEADS"
    assert kl["n_targeted_diagnostic_candidates"] == 5
    assert kl["recommended_next_phase_is_2k_m"] is True
    assert kl["routing_confirmed"] is True


# --------------------------------------------------------------------------- #
# 9. Exactly 5 targeted leads, all TARGETED_DIAGNOSTIC_CANDIDATE / RESEARCH_LEAD_ONLY
# --------------------------------------------------------------------------- #
def test_exactly_five_targeted_leads_from_research_lead_only_pairs():
    d = _committed()
    leads = d["targeted_leads"]
    assert len(leads) == 5, "expected exactly 5 targeted leads"
    for lead in leads:
        assert lead["source_classification"] == _TARGETED, \
            f"lead not from TARGETED_DIAGNOSTIC_CANDIDATE: {lead.get('lead_id')}"
        assert lead["source_status"] == _LEAD_STATUS, \
            f"lead not from RESEARCH_LEAD_ONLY pair: {lead.get('lead_id')}"
        assert lead["from_phase2k_k_research_lead_pair"] is True, \
            f"lead not traced to a Phase 2K-K RESEARCH_LEAD_ONLY pair: {lead.get('lead_id')}"
    assert d["upstream_phase2k_l_summary"][
        "all_targeted_from_phase2k_k_research_lead_pairs"] is True


# --------------------------------------------------------------------------- #
# 10. Stopped candidate stays stopped and excluded from the targeted leads
# --------------------------------------------------------------------------- #
def test_stopped_candidate_excluded_from_targeted_leads():
    d = _committed()
    policy = d["stopped_candidate_policy"]
    assert policy["stopped_candidate"] == _STOPPED_CANDIDATE
    assert policy["status"] == "STOPPED_AFTER_EXPANDED_RETEST_FAIL"
    assert policy["excluded_as_standalone_alpha"] is True
    assert policy["not_in_targeted_leads"] is True
    for lead in d["targeted_leads"]:
        assert lead["candidate"] != _STOPPED_CANDIDATE
        assert lead["feature"] != _STOPPED_CANDIDATE


# --------------------------------------------------------------------------- #
# 11. Follow-up types use allowed values only (and counts are consistent)
# --------------------------------------------------------------------------- #
def test_follow_up_types_allowed():
    d = _committed()
    counts = {"NARROW_RETEST_CANDIDATE": 0, "DIAGNOSTIC_ONLY": 0,
              "HOLD_FOR_SECTOR_RELATIVE_VARIANT": 0}
    for lead in d["targeted_leads"]:
        fu = lead["follow_up_type"]
        assert fu in _ALLOWED_FOLLOW_UPS, f"bad follow-up type: {fu}"
        counts[fu] += 1
    assert counts == d["follow_up_type_counts"]
    for rec_lead in d["targeted_lead_rankings"]["rankings"]:
        assert rec_lead["follow_up_type"] in _ALLOWED_FOLLOW_UPS
    # The narrow-retest plan id lists must match the per-lead follow-up types.
    narrow_ids = {l["lead_id"] for l in d["targeted_leads"]
                  if l["follow_up_type"] == "NARROW_RETEST_CANDIDATE"}
    hold_ids = {l["lead_id"] for l in d["targeted_leads"]
                if l["follow_up_type"] == "HOLD_FOR_SECTOR_RELATIVE_VARIANT"}
    plan = d["narrow_retest_plan"]
    assert set(plan["narrow_retest_candidate_lead_ids"]) == narrow_ids
    assert set(plan["hold_for_sector_relative_lead_ids"]) == hold_ids


# --------------------------------------------------------------------------- #
# 12. Recommendation disallows model work; routes to 2K-N
# --------------------------------------------------------------------------- #
def test_recommendation_disallows_model_work():
    d = _committed()
    rec = d["recommendation"]
    assert rec["create_model_candidate_now"] is False
    assert rec["train_model_now"] is False
    assert rec["deploy_now"] is False
    assert rec["ran_new_d_screen"] is False
    assert rec["production_edge_claimed"] is False
    interp = d["interpretation"]
    assert interp["model_trained"] is False
    assert interp["model_candidate_created"] is False
    assert interp["authorized_to_serve_model"] is False
    assert interp["ran_new_d_screen"] is False
    assert interp["read_large_d_csv"] is False
    plan = d["narrow_retest_plan"]
    assert plan["no_model_training"] is True
    assert plan["no_model_candidate"] is True
    assert plan["no_new_d_screen"] is True
    assert d["recommended_next_phase"]["phase"] == "2K-N"


# --------------------------------------------------------------------------- #
# 13. Cross-target diagnostics are coherent (families, horizons, blocker analysis)
# --------------------------------------------------------------------------- #
def test_cross_target_diagnostics_coherent():
    d = _committed()
    fams = {f["group_key"] for f in d["family_diagnostics"]}
    assert "short_term_reversal_mean_reversion" in fams
    n_fam = sum(f["n_targeted_leads"] for f in d["family_diagnostics"])
    assert n_fam == len(d["targeted_leads"])
    horizons = {h["group_key"] for h in d["horizon_diagnostics"]}
    assert {5, 21} <= horizons
    n_hor = sum(h["n_targeted_leads"] for h in d["horizon_diagnostics"])
    assert n_hor == len(d["targeted_leads"])
    blk = d["blocker_analysis"]
    assert blk["n_targeted_leads"] == len(d["targeted_leads"])
    assert blk["most_common_failed_gate"] in blk["gate_failure_counts"]
    assert blk["all_targeted_leads_fail_ic_floor"] is True
    assert isinstance(blk["only_blocking_gate_is_sub_floor_ic"], bool)


# --------------------------------------------------------------------------- #
# 14. Doc has all required guardrail phrases
# --------------------------------------------------------------------------- #
def test_doc_has_required_phrases():
    text = _read(_DOC)
    missing = [p for p in _REQUIRED_DOC_PHRASES if p not in text]
    assert not missing, f"doc missing required phrase(s): {missing}"


# --------------------------------------------------------------------------- #
# 15. Live recompute (only when the Phase 2K-L input is present): invariants
# --------------------------------------------------------------------------- #
def test_live_recompute_structure():
    analyzer = _import_analyzer()
    res, on_disk = _run_live(analyzer)  # may raise _Skip
    blob = json.loads(_read(on_disk))
    for d in (res, blob):
        assert d["phase"] == "2K-M"
        for k in _REQUIRED_JSON_FIELDS:
            assert k in d, f"live diagnostics JSON missing field: {k}"
        for k in _REQUIRED_FALSE:
            assert d[k] is False
        for k in _REQUIRED_TRUE:
            assert d[k] is True
        assert len(d["targeted_leads"]) == 5
        for lead in d["targeted_leads"]:
            assert lead["source_classification"] == _TARGETED
            assert lead["source_status"] == _LEAD_STATUS
            assert lead["from_phase2k_k_research_lead_pair"] is True
            assert lead["follow_up_type"] in _ALLOWED_FOLLOW_UPS
            assert lead["candidate"] != _STOPPED_CANDIDATE
        assert d["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
        assert d["recommendation"]["create_model_candidate_now"] is False
        assert d["recommendation"]["train_model_now"] is False
        assert d["recommendation"]["ran_new_d_screen"] is False
        assert d["recommended_next_phase"]["phase"] == "2K-N"


def test_live_recompute_routing_and_targeted_count():
    analyzer = _import_analyzer()
    res, _ = _run_live(analyzer)  # may raise _Skip
    kl = res["upstream_phase2k_l_summary"]
    assert kl["routing_confirmed"] is True
    assert kl["recommendation"] == "TARGETED_DIAGNOSTICS_FOR_BEST_RESEARCH_LEADS"
    assert kl["n_targeted_diagnostic_candidates"] == 5
    assert len(res["targeted_leads"]) == 5


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
