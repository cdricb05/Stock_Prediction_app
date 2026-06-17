"""Phase 2K-A tests for the New Alpha Hypothesis Backlog & Data Requirements analyzer.

These tests prove the analyzer is observational and read-only: it compiles and
imports without side effects; it references only the three upstream local JSON
artifacts (the Phase 2J-A decision, the Phase 2I-B survivor robustness, and the
Phase 2I-A feature-IC sweep); it writes only
research/output/phase2k_alpha_backlog.json; it never imports api_server or Paper
Trader; its source contains no deploy / gcloud / SSH / service / DB-write /
migration logic; and the backlog JSON it produces carries every required field,
every required safety flag, a confirmed upstream NO_GO, at least eight hypothesis
families, the required data requirements, a model-candidate gate locked closed,
and a recommended next phase of 2K-B. The companion doc must contain the required
guardrail phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase2k_alpha_backlog.py
  * without pytest: python tests/test_phase2k_alpha_backlog.py
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
    _REPO_ROOT, "research", "analyze_phase2k_alpha_backlog.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase2k_alpha_hypothesis_backlog_v1.md")
_OUTPUT_JSON = os.path.join(
    _REPO_ROOT, "research", "output", "phase2k_alpha_backlog.json")

# The eight hypothesis families Phase 2K-A must enumerate.
_EXPECTED_HYPOTHESIS_FAMILIES = {
    "beta_vol_neutralized_residual_signal", "cross_sectional_mean_reversion",
    "fundamental_quality_value", "estimate_revisions_and_surprise",
    "earnings_event_drift", "liquidity_breadth_and_microstructure",
    "sector_relative_strength", "regime_conditional_signals",
}

# The eight data requirements Phase 2K-A must enumerate.
_EXPECTED_DATA_REQUIREMENTS = {
    "longer_price_history", "broader_universe", "point_in_time_fundamentals",
    "analyst_estimates", "earnings_calendar_and_surprise",
    "sector_industry_mapping", "liquidity_and_volume_history",
    "benchmark_and_factor_data",
}

# Per-hypothesis fields the backlog must carry.
_REQUIRED_HYPOTHESIS_FIELDS = (
    "hypothesis_id", "title", "thesis", "why_it_addresses_failed_2I_signal",
    "required_data", "minimum_viable_test", "expected_horizon",
    "expected_direction", "leakage_risks", "validation_gate",
    "data_availability", "implementation_complexity", "priority", "go_to_test",
)
# Per-data-requirement fields.
_REQUIRED_DATA_REQ_FIELDS = (
    "description", "required_for_hypotheses", "must_be_point_in_time",
    "lookahead_risk", "free_or_low_cost_sources_to_investigate", "blockers",
    "priority",
)

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

# Required top-level fields of the backlog JSON.
_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read", "provenance",
    "upstream_decision_summary", "alpha_hypothesis_backlog", "data_requirements",
    "research_sequence", "model_candidate_gate", "recommended_next_phase",
)
_REQUIRED_UPSTREAM_FIELDS = (
    "phase2j_go_no_go", "promote_model_v2", "build_phase2j_model_candidate",
    "robust_enough_for_phase2j", "reason", "no_go_confirmed",
)
_REQUIRED_GATE_FIELDS = (
    "may_create_model_candidate", "required_before_model_candidate",
    "explicit_rule",
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
    spec = importlib.util.spec_from_file_location("phase2k_a_analyzer", _ANALYZER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Run the (very cheap) analysis at most once per test process and cache both the
# returned dict and the on-disk JSON.
_DIAG_CACHE = {}


def _diag():
    if "diag" not in _DIAG_CACHE:
        mod = _import_analyzer()
        out = os.path.join(tempfile.mkdtemp(), "backlog.json")
        _DIAG_CACHE["diag"] = mod.run(output_path=out)
        _DIAG_CACHE["on_disk"] = json.loads(_read(out))
    return _DIAG_CACHE["diag"]


def _backlog_by_id(diag):
    return {h["hypothesis_id"]: h for h in diag["alpha_hypothesis_backlog"]}


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
    assert mod.INPUT_2J_JSON.replace("\\", "/").endswith(
        "research/output/phase2j_research_decision.json")
    assert mod.INPUT_2IB_JSON.replace("\\", "/").endswith(
        "research/output/phase2i_b_survivor_robustness.json")
    assert mod.INPUT_2IA_JSON.replace("\\", "/").endswith(
        "research/output/phase2i_feature_ic_horizon_sweep.json")
    text = _read(_ANALYZER).lower()
    for tok in ("http://", "https://", "requests.", "urllib", "yfinance",
                "socket"):
        assert tok not in text, f"analyzer must not reach the network: {tok!r}"


# --------------------------------------------------------------------------- #
# 3. Writes only the Phase 2K-A backlog JSON
# --------------------------------------------------------------------------- #
def test_writes_only_backlog_json():
    mod = _import_analyzer()
    assert mod.BACKLOG_JSON.replace("\\", "/").endswith(
        "research/output/phase2k_alpha_backlog.json")
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
    assert write_opens == 1, "exactly one write-open (the backlog JSON)"


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
            assert k in d, f"backlog missing field: {k}"
        assert d["phase"] == "2K-A"
        for k in _REQUIRED_FALSE:
            assert d[k] is False, f"{k} must be false"
        for k in _REQUIRED_TRUE:
            assert d[k] is True, f"{k} must be true"
        ir = d["inputs_read"]
        assert ir["phase2j_json"].replace("\\", "/").endswith(
            "research/output/phase2j_research_decision.json")
        assert ir["phase2i_b_json"].replace("\\", "/").endswith(
            "research/output/phase2i_b_survivor_robustness.json")
        assert ir["phase2i_a_json"].replace("\\", "/").endswith(
            "research/output/phase2i_feature_ic_horizon_sweep.json")


# --------------------------------------------------------------------------- #
# 8. Upstream NO_GO is confirmed
# --------------------------------------------------------------------------- #
def test_upstream_no_go_confirmed():
    up = _diag()["upstream_decision_summary"]
    for k in _REQUIRED_UPSTREAM_FIELDS:
        assert k in up, f"upstream_decision_summary missing field: {k}"
    assert up["phase2j_go_no_go"] == "NO_GO", \
        f"expected upstream NO_GO, got {up['phase2j_go_no_go']!r}"
    assert up["promote_model_v2"] is False
    assert up["build_phase2j_model_candidate"] is False
    assert up["robust_enough_for_phase2j"] is False
    assert up["no_go_confirmed"] is True
    assert isinstance(up["reason"], str) and up["reason"].strip()


# --------------------------------------------------------------------------- #
# 9. At least eight hypothesis families, each fully specified
# --------------------------------------------------------------------------- #
def test_hypothesis_backlog_present():
    backlog = _diag()["alpha_hypothesis_backlog"]
    assert isinstance(backlog, list)
    assert len(backlog) >= 8, f"expected >= 8 hypothesis families, got {len(backlog)}"
    ids = {h["hypothesis_id"] for h in backlog}
    missing = _EXPECTED_HYPOTHESIS_FAMILIES - ids
    assert not missing, f"alpha_hypothesis_backlog missing families: {missing}"
    for h in backlog:
        for k in _REQUIRED_HYPOTHESIS_FIELDS:
            assert k in h, f"hypothesis {h.get('hypothesis_id')!r} missing field: {k}"
        assert isinstance(h["go_to_test"], bool)
        assert isinstance(h["required_data"], list) and h["required_data"]
    # At least one family must be cheap enough to test now (drives 2K-B).
    assert any(h["go_to_test"] for h in backlog), \
        "at least one hypothesis must be testable on current data"


# --------------------------------------------------------------------------- #
# 10. Required data requirements exist, each fully specified
# --------------------------------------------------------------------------- #
def test_data_requirements_present():
    reqs = _diag()["data_requirements"]
    assert isinstance(reqs, dict)
    missing = _EXPECTED_DATA_REQUIREMENTS - set(reqs.keys())
    assert not missing, f"data_requirements missing: {missing}"
    for name, r in reqs.items():
        for k in _REQUIRED_DATA_REQ_FIELDS:
            assert k in r, f"data requirement {name!r} missing field: {k}"
        assert isinstance(r["must_be_point_in_time"], bool)
        assert isinstance(r["required_for_hypotheses"], list)
        assert isinstance(r["free_or_low_cost_sources_to_investigate"], list)


# --------------------------------------------------------------------------- #
# 11. Research sequence is a ranked, non-empty plan
# --------------------------------------------------------------------------- #
def test_research_sequence_present():
    seq = _diag()["research_sequence"]
    assert isinstance(seq, list) and len(seq) >= 6, \
        "research_sequence must be a ranked plan of at least six steps"
    for step in seq:
        assert "rank" in step and "action" in step
    ranks = [step["rank"] for step in seq]
    assert ranks == sorted(ranks), "research_sequence must be ranked in order"
    # The cheapest current-data residual test must come first.
    assert "residual" in seq[0]["action"].lower()


# --------------------------------------------------------------------------- #
# 12. Model-candidate gate is locked closed
# --------------------------------------------------------------------------- #
def test_model_candidate_gate_closed():
    gate = _diag()["model_candidate_gate"]
    for k in _REQUIRED_GATE_FIELDS:
        assert k in gate, f"model_candidate_gate missing field: {k}"
    assert gate["may_create_model_candidate"] is False, \
        "may_create_model_candidate must be false"
    assert isinstance(gate["required_before_model_candidate"], list) \
        and gate["required_before_model_candidate"]
    rule = gate["explicit_rule"].lower()
    assert "robustness" in rule and "no model candidate" in rule


# --------------------------------------------------------------------------- #
# 13. Recommended next phase is 2K-B
# --------------------------------------------------------------------------- #
def test_recommended_next_phase_is_2k_b():
    nxt = _diag()["recommended_next_phase"]
    for k in _REQUIRED_NEXT_PHASE_FIELDS:
        assert k in nxt, f"recommended_next_phase missing field: {k}"
    assert nxt["phase"] == "2K-B", f"expected 2K-B, got {nxt['phase']!r}"
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
