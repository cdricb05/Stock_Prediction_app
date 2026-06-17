"""Phase 2J-A tests for the Research NO-GO Decision & Alpha Rebuild Plan analyzer.

These tests prove the analyzer is observational and read-only: it compiles and
imports without side effects; it references only the three upstream local JSON
artifacts (Phase 2I-A, Phase 2I-B, and the Phase 2G-C run summary); it writes only
research/output/phase2j_research_decision.json; it never imports api_server or
Paper Trader; its source contains no deploy / gcloud / SSH / service / DB-write /
migration logic; and the decision JSON it produces carries every required field,
every required safety flag, a locked NO_GO decision (promote_model_v2 false,
build_phase2j_model_candidate false), the enumerated failed hypotheses, the
"what still worked" record, the alpha-rebuild plan, and a recommended next phase
of 2K-A. The companion doc must contain the required guardrail phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase2j_research_decision.py
  * without pytest: python tests/test_phase2j_research_decision.py
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
    _REPO_ROOT, "research", "analyze_phase2j_research_decision.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase2j_research_no_go_decision_v1.md")
_OUTPUT_JSON = os.path.join(
    _REPO_ROOT, "research", "output", "phase2j_research_decision.json")

# The five hypotheses Phase 2J-A must record as ruled out.
_EXPECTED_FAILED_HYPOTHESES = {
    "short_horizon_momentum", "raw_return_momentum", "excess_return_momentum",
    "volume_zscore", "63d_volatility_beta_correlation_tilt",
}

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

# Required top-level fields of the decision JSON.
_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read", "provenance", "evidence_summary",
    "decision", "failed_hypotheses", "what_still_worked", "alpha_rebuild_plan",
    "recommended_next_phase",
)
_REQUIRED_EVIDENCE_FIELDS = (
    "phase2i_a_keep_count", "phase2i_b_keep_for_model_count",
    "phase2i_b_keep_as_risk_filter_count", "phase2i_b_drop_count",
    "phase2i_b_need_more_data_count", "robust_enough_for_phase2j",
    "production_edge_claimed",
)
_REQUIRED_DECISION_FIELDS = (
    "go_no_go", "promote_model_v2", "build_phase2j_model_candidate", "reason",
)
_REQUIRED_PLAN_FIELDS = (
    "new_hypothesis_categories", "required_additional_data",
    "recommended_order_of_research", "explicit_rule",
)
_REQUIRED_NEXT_PHASE_FIELDS = ("phase", "title", "purpose")
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_edge_claimed",
)
_REQUIRED_TRUE = ("no_trading", "no_orders", "no_automation")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _import_analyzer():
    spec = importlib.util.spec_from_file_location("phase2j_a_analyzer", _ANALYZER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Run the (very cheap) analysis at most once per test process and cache both the
# returned dict and the on-disk JSON.
_DIAG_CACHE = {}


def _diag():
    if "diag" not in _DIAG_CACHE:
        mod = _import_analyzer()
        out = os.path.join(tempfile.mkdtemp(), "decision.json")
        _DIAG_CACHE["diag"] = mod.run(output_path=out)
        _DIAG_CACHE["on_disk"] = json.loads(_read(out))
    return _DIAG_CACHE["diag"]


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
    assert mod.INPUT_2IA_JSON.replace("\\", "/").endswith(
        "research/output/phase2i_feature_ic_horizon_sweep.json")
    assert mod.INPUT_2IB_JSON.replace("\\", "/").endswith(
        "research/output/phase2i_b_survivor_robustness.json")
    assert mod.INPUT_RUN_SUMMARY_JSON.replace("\\", "/").endswith(
        "research/output/phase2g_c_real_data_run_summary.json")
    text = _read(_ANALYZER).lower()
    for tok in ("http://", "https://", "requests.", "urllib", "yfinance",
                "socket"):
        assert tok not in text, f"analyzer must not reach the network: {tok!r}"


# --------------------------------------------------------------------------- #
# 3. Writes only the Phase 2J-A decision JSON
# --------------------------------------------------------------------------- #
def test_writes_only_decision_json():
    mod = _import_analyzer()
    assert mod.DECISION_JSON.replace("\\", "/").endswith(
        "research/output/phase2j_research_decision.json")
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
    assert write_opens == 1, "exactly one write-open (the decision JSON)"


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
            assert k in d, f"decision missing field: {k}"
        assert d["phase"] == "2J-A"
        for k in _REQUIRED_FALSE:
            assert d[k] is False, f"{k} must be false"
        for k in _REQUIRED_TRUE:
            assert d[k] is True, f"{k} must be true"
        ir = d["inputs_read"]
        assert ir["phase2i_a_json"].replace("\\", "/").endswith(
            "research/output/phase2i_feature_ic_horizon_sweep.json")
        assert ir["phase2i_b_json"].replace("\\", "/").endswith(
            "research/output/phase2i_b_survivor_robustness.json")
        assert ir["run_summary_json"].replace("\\", "/").endswith(
            "research/output/phase2g_c_real_data_run_summary.json")


# --------------------------------------------------------------------------- #
# 8. Evidence summary carries every required field
# --------------------------------------------------------------------------- #
def test_evidence_summary_fields():
    ev = _diag()["evidence_summary"]
    for k in _REQUIRED_EVIDENCE_FIELDS:
        assert k in ev, f"evidence_summary missing field: {k}"
    for k in ("phase2i_a_keep_count", "phase2i_b_keep_for_model_count",
              "phase2i_b_keep_as_risk_filter_count", "phase2i_b_drop_count",
              "phase2i_b_need_more_data_count"):
        assert isinstance(ev[k], int), f"{k} must be an int"
    assert isinstance(ev["robust_enough_for_phase2j"], bool)
    assert isinstance(ev["production_edge_claimed"], bool)


# --------------------------------------------------------------------------- #
# 9. Decision is a locked NO_GO with model-v2 / candidate building disabled
# --------------------------------------------------------------------------- #
def test_decision_is_no_go():
    diag = _diag()
    dec = diag["decision"]
    for k in _REQUIRED_DECISION_FIELDS:
        assert k in dec, f"decision missing field: {k}"
    assert dec["go_no_go"] == "NO_GO", f"expected NO_GO, got {dec['go_no_go']!r}"
    assert dec["promote_model_v2"] is False
    assert dec["build_phase2j_model_candidate"] is False
    assert isinstance(dec["reason"], str) and dec["reason"].strip()
    # The top-level safety flag and the evidence must agree with NO_GO.
    assert diag["production_edge_claimed"] is False
    assert diag["evidence_summary"]["production_edge_claimed"] is False
    assert diag["evidence_summary"]["phase2i_b_keep_for_model_count"] == 0


# --------------------------------------------------------------------------- #
# 10. Failed hypotheses are enumerated
# --------------------------------------------------------------------------- #
def test_failed_hypotheses_present():
    fh = _diag()["failed_hypotheses"]
    keys = set(fh.keys())
    missing = _EXPECTED_FAILED_HYPOTHESES - keys
    assert not missing, f"failed_hypotheses missing: {missing}"


# --------------------------------------------------------------------------- #
# 11. What-still-worked record is present and non-empty
# --------------------------------------------------------------------------- #
def test_what_still_worked_present():
    wsw = _diag()["what_still_worked"]
    assert wsw, "what_still_worked must be non-empty"
    # The four platform wins the phase is meant to capture.
    blob = json.dumps(wsw).lower()
    for token in ("research harness", "safety flag", "feature flag",
                  "real-data", "paper trader"):
        assert token in blob, f"what_still_worked missing mention of {token!r}"


# --------------------------------------------------------------------------- #
# 12. Alpha rebuild plan is complete, with the no-candidate-without-robustness rule
# --------------------------------------------------------------------------- #
def test_alpha_rebuild_plan_complete():
    plan = _diag()["alpha_rebuild_plan"]
    for k in _REQUIRED_PLAN_FIELDS:
        assert k in plan, f"alpha_rebuild_plan missing field: {k}"
    assert isinstance(plan["new_hypothesis_categories"], list) \
        and plan["new_hypothesis_categories"]
    assert isinstance(plan["required_additional_data"], list) \
        and plan["required_additional_data"]
    assert isinstance(plan["recommended_order_of_research"], list) \
        and plan["recommended_order_of_research"]
    rule = plan["explicit_rule"].lower()
    assert "out-of-sample" in rule and "robustness" in rule, \
        "explicit_rule must require out-of-sample robustness before any candidate"


# --------------------------------------------------------------------------- #
# 13. Recommended next phase is 2K-A
# --------------------------------------------------------------------------- #
def test_recommended_next_phase_is_2k_a():
    nxt = _diag()["recommended_next_phase"]
    for k in _REQUIRED_NEXT_PHASE_FIELDS:
        assert k in nxt, f"recommended_next_phase missing field: {k}"
    assert nxt["phase"] == "2K-A", f"expected 2K-A, got {nxt['phase']!r}"
    assert isinstance(nxt["title"], str) and nxt["title"].strip()
    assert isinstance(nxt["purpose"], str) and nxt["purpose"].strip()


# --------------------------------------------------------------------------- #
# 14. Doc has all required guardrail phrases
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
