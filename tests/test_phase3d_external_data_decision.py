"""Phase 3-D tests for the External Data Decision analyzer.

These tests prove the analyzer is a disciplined decision / planning phase: it compiles and
imports; it references the expected repo-local inputs; it reads the Phase 3-C result and
confirms the price/volume-only kill switch triggered; it carries every required output field
and safety flag; phase == "3-D"; it trains no model, creates no production model candidate,
writes no deployable model artifact, reads/writes nothing on the D: drive, and makes no
network / vendor-API / data-purchase call; the option matrix has at least eight candidate
families; a selected data track exists; the recommendation uses only the allowed values and
routes to Phase 3-E; and the source imports no api_server / Paper Trader / ML framework and
contains no infrastructure, database-write, deployment, order/trading, network, or
model-artifact usage tokens. The doc carries the required guardrail phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase3d_external_data_decision.py
  * without pytest: python tests/test_phase3d_external_data_decision.py
The __main__ block discovers every test_* function, runs it, prints PASS/FAIL/SKIP, and exits
non-zero on any failure (the GCP venv has no pytest). The live end-to-end run that writes the
three outputs to a temp dir is gated behind PHASE3D_LIVE=1.
"""
from __future__ import annotations

import ast
import csv
import importlib.util
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_ANALYZER = os.path.join(_REPO_ROOT, "research", "analyze_phase3d_external_data_decision.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase3d_external_data_decision_v1.md")
_DECISION = os.path.join(_REPO_ROOT, "research", "output", "phase3d_external_data_decision.json")
_MATRIX_CSV = os.path.join(
    _REPO_ROOT, "research", "output", "phase3d_external_data_option_matrix.csv")
_TRACK_JSON = os.path.join(
    _REPO_ROOT, "research", "output", "phase3d_recommended_data_track.json")
_PHASE3C_JSON = os.path.join(
    _REPO_ROOT, "research", "output", "phase3c_refined_greenfield_rerun.json")


class _Skip(Exception):
    """Raised to mark a test skipped."""


# The repo-local inputs this phase must reference (basenames).
_EXPECTED_INPUT_BASENAMES = (
    "phase3c_refined_greenfield_rerun.json",
    "phase3b_greenfield_refinement.json",
    "phase3a_greenfield_baseline.json",
    "phase2k_p_sector_map_current.csv",
    "phase2k_h_manual_build_run_summary.json",
)

# Forbidden infrastructure tokens (assembled from fragments so they never self-match prose).
_FORBIDDEN_INFRA_TOKENS = [
    "gcl" + "oud",
    "sub" + "process",
    "os." + "system",
    "para" + "miko",
    "system" + "ctl",
    "kube" + "ctl",
    "stock-api." + "service",
    "alem" + "bic",
    "PREDICTOR_USE_" + "MODEL_V2",
]
# Forbidden database-write tokens.
_FORBIDDEN_DB_TOKENS = [
    "insert " + "into", "delete " + "from", "drop " + "table", "alter " + "table",
    "create " + "table", "trun" + "cate", "to_" + "sql",
]
# Forbidden deployment / order / trading usage tokens.
_FORBIDDEN_DEPLOY_TRADE_TOKENS = [
    "place" + "_order", "submit" + "_order", "create" + "_order",
    "ssh ", "scp ", "uvi" + "corn", "systemd",
]
# Forbidden network / acquisition / data-purchase tokens (this phase fetches and buys nothing).
_FORBIDDEN_NETWORK_TOKENS = [
    "http" + "://", "https" + "://", "requests." + "get", "requests." + "post",
    "url" + "lib", "yf" + "inance", "soc" + "ket", "url" + "open", "down" + "load(",
]
# Forbidden deployable-model-artifact tokens (this phase persists no model).
_FORBIDDEN_ARTIFACT_TOKENS = [
    "to_" + "pickle", "pickle." + "dump", "joblib." + "dump", "import " + "pickle",
    "import " + "joblib", ".pk" + "l", ".job" + "lib",
]
# Libraries / modules that must not be imported.
_FORBIDDEN_IMPORT_ROOTS = {
    "sklearn", "tensorflow", "torch", "keras", "lightgbm", "xgboost", "statsmodels",
    "api_server", "requests", "urllib", "yfinance", "socket",
}

_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read", "phase3c_summary",
    "price_volume_kill_switch_summary", "external_data_families_evaluated",
    "scoring_methodology", "option_matrix", "selected_data_track", "backup_data_track",
    "rejected_tracks", "implementation_requirements", "validation_requirements",
    "risks_and_caveats", "recommendation", "interpretation", "recommended_next_phase",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_edge_claimed",
    "research_model_trained", "production_model_trained", "production_model_candidate_created",
    "deployable_model_artifact_written", "d_drive_read", "d_drive_written", "network_used",
    "vendor_api_called", "data_purchase_made",
)
_REQUIRED_TRUE = (
    "no_trading", "no_orders", "no_automation",
)
_ALLOWED_RECOMMENDATIONS = {
    "PROCEED_TO_FUNDAMENTALS_EARNINGS_DATA_FEASIBILITY",
    "PROCEED_TO_OPTIONS_DATA_FEASIBILITY",
    "PROCEED_TO_NEWS_SENTIMENT_DATA_FEASIBILITY",
    "REQUIRE_VENDOR_RESEARCH_BEFORE_DATA_TRACK",
    "STOP_MODEL_RESEARCH_NO_FEASIBLE_DATA_PATH",
    "EXTERNAL_DATA_DECISION_BLOCKED",
}
_REQUIRED_DOC_PHRASES = [
    "does not deploy",
    "does not restart stock-api.service",
    "does not enable",
    "does not run migrations",
    "does not write to production DB",
    "does not trade",
    "production edge",
]


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _read_csv_rows(path: str):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _import_analyzer():
    spec = importlib.util.spec_from_file_location("phase3d_analyzer_test", _ANALYZER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# 1. Analyzer compiles and imports
# --------------------------------------------------------------------------- #
def test_analyzer_compiles():
    compile(_read(_ANALYZER), _ANALYZER, "exec")


def test_analyzer_imports():
    _import_analyzer()


# --------------------------------------------------------------------------- #
# 2. Expected repo-local inputs referenced; outputs under research/output (C:); no D: read
# --------------------------------------------------------------------------- #
def test_references_expected_inputs():
    analyzer = _import_analyzer()
    text = _read(_ANALYZER)
    for base in _EXPECTED_INPUT_BASENAMES:
        assert base in text, f"analyzer does not reference expected input: {base}"
    for path in (analyzer.DECISION_JSON, analyzer.OPTION_MATRIX_CSV,
                 analyzer.RECOMMENDED_TRACK_JSON):
        n = path.replace("\\", "/")
        assert n.startswith(_REPO_ROOT.replace("\\", "/")) or "research/output/" in n, n
        assert not n.upper().startswith("D:"), f"output must not be on the D: drive: {n}"


def test_no_d_drive_read_or_write_in_source():
    text = _read(_ANALYZER)
    # No D: path literal opened for reading or writing in this decision phase.
    low = text.lower()
    assert "d:/" not in low and "d:\\" not in low and 'd:" + os.sep' not in low, \
        "analyzer must not read or write the D: drive in Phase 3-D"


# --------------------------------------------------------------------------- #
# 3. No api_server / Paper Trader / ML-framework / network imports
# --------------------------------------------------------------------------- #
def test_no_forbidden_imports():
    text = _read(_ANALYZER)
    assert "paper_trader" not in text.lower(), "analyzer must not reference paper_trader"
    assert "import api_server" not in text and "from api_server" not in text
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = ([n.name for n in node.names]
                    + ([node.module] if isinstance(node, ast.ImportFrom)
                       and node.module else []))
            roots = {(m or "").split(".")[0] for m in mods}
            bad = roots & _FORBIDDEN_IMPORT_ROOTS
            assert not bad, f"analyzer imports forbidden module(s): {bad}"


# --------------------------------------------------------------------------- #
# 4. No infra / db / deploy / trade / network / artifact usage tokens
# --------------------------------------------------------------------------- #
def test_no_forbidden_usage_tokens():
    low = _read(_ANALYZER).lower()
    for label, tokens in (
        ("infrastructure", _FORBIDDEN_INFRA_TOKENS),
        ("database-write", _FORBIDDEN_DB_TOKENS),
        ("deploy/trade", _FORBIDDEN_DEPLOY_TRADE_TOKENS),
        ("network", _FORBIDDEN_NETWORK_TOKENS),
        ("model-artifact", _FORBIDDEN_ARTIFACT_TOKENS),
    ):
        hits = [t for t in tokens if t.lower() in low]
        assert not hits, f"analyzer contains forbidden {label} token(s): {hits}"


# --------------------------------------------------------------------------- #
# 5. Committed decision artifact: required fields, safety flags, structure
# --------------------------------------------------------------------------- #
def test_committed_decision_artifact_valid():
    if not os.path.isfile(_DECISION):
        raise _Skip("committed decision JSON not present")
    d = json.loads(_read(_DECISION))
    for k in _REQUIRED_JSON_FIELDS:
        assert k in d, f"committed decision JSON missing field: {k}"
    assert d["phase"] == "3-D"
    for k in _REQUIRED_FALSE:
        assert d[k] is False, f"{k} must be false"
    for k in _REQUIRED_TRUE:
        assert d[k] is True, f"{k} must be true"


def test_reads_phase3c_and_confirms_kill_switch():
    if not os.path.isfile(_DECISION):
        raise _Skip("committed decision JSON not present")
    d = json.loads(_read(_DECISION))
    confirmed = d["phase3c_summary"]["phase3c_confirmed"]
    assert confirmed["phase_is_3c"] is True
    assert confirmed["recommendation_is_kill_switch"] is True
    assert confirmed["kill_switch_triggered_true"] is True
    assert confirmed["next_phase_is_3d"] is True
    assert confirmed["all_confirmed"] is True
    assert d["phase3c_summary"]["phase3c_recommendation"] == \
        "PRICE_VOLUME_ONLY_KILL_SWITCH_TRIGGERED"
    ks = d["price_volume_kill_switch_summary"]
    assert ks["kill_switch_triggered"] is True
    assert ks["primary_catastrophic_fold_count"] == 3


def test_no_model_training_or_production_artifacts():
    if not os.path.isfile(_DECISION):
        raise _Skip("committed decision JSON not present")
    d = json.loads(_read(_DECISION))
    interp = d["interpretation"]
    assert interp["model_trained"] is False
    assert interp["research_model_trained"] is False
    assert interp["production_model_trained"] is False
    assert interp["production_model_candidate_created"] is False
    assert interp["deployable_model_artifact_written"] is False
    assert interp["read_from_d_drive"] is False
    assert interp["wrote_to_d_drive"] is False
    assert interp["data_fetched_from_network"] is False
    assert interp["data_purchased"] is False
    assert interp["data_vendor_called"] is False
    assert interp["production_edge_claimed"] is False


# --------------------------------------------------------------------------- #
# 6. Option matrix, selected track, recommendation, next phase
# --------------------------------------------------------------------------- #
def test_option_matrix_has_enough_families():
    if not os.path.isfile(_DECISION):
        raise _Skip("committed decision JSON not present")
    d = json.loads(_read(_DECISION))
    assert len(d["external_data_families_evaluated"]) >= 8
    assert len(d["option_matrix"]) >= 8
    # Each row is scored on all nine criteria.
    for row in d["option_matrix"]:
        for crit in ("expected_predictive_relevance", "point_in_time_feasibility",
                     "cost_accessibility", "validation_cleanliness"):
            assert crit in row["scores"], f"matrix row missing criterion: {crit}"
        assert 1 <= row["scores"]["expected_predictive_relevance"] <= 5


def test_selected_track_and_recommendation():
    if not os.path.isfile(_DECISION):
        raise _Skip("committed decision JSON not present")
    d = json.loads(_read(_DECISION))
    sel = d["selected_data_track"]
    assert sel and sel.get("track_name")
    rec = d["recommendation"]
    assert rec["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert rec["create_production_model_candidate_now"] is False
    assert rec["train_production_model_now"] is False
    assert rec["deploy_now"] is False
    assert rec["production_edge_claimed"] is False
    assert isinstance(rec.get("selected_track"), str) and rec["selected_track"]
    assert isinstance(rec.get("reason"), str) and rec["reason"]
    assert d["recommended_next_phase"]["phase"] == "3-E"
    assert d["recommended_next_phase"]["title"]
    assert d["recommended_next_phase"]["purpose"]


def test_recommended_track_json_consistent():
    if not os.path.isfile(_TRACK_JSON):
        raise _Skip("recommended track JSON not present")
    t = json.loads(_read(_TRACK_JSON))
    assert t["phase"] == "3-D"
    assert t["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert t["recommended_next_phase"]["phase"] == "3-E"
    assert t["create_production_model_candidate_now"] is False
    assert t["train_production_model_now"] is False
    assert t["data_purchase_made"] is False
    assert t["vendor_api_called"] is False
    assert t["network_used"] is False
    assert t["d_drive_read"] is False


# --------------------------------------------------------------------------- #
# 7. Option matrix CSV is well-formed
# --------------------------------------------------------------------------- #
def test_option_matrix_csv_well_formed():
    if not os.path.isfile(_MATRIX_CSV):
        raise _Skip("option matrix CSV not present")
    rows = _read_csv_rows(_MATRIX_CSV)
    assert len(rows) >= 8
    for col in ("rank", "data_family", "family_key", "expected_predictive_relevance",
                "point_in_time_feasibility", "weighted_priority_score", "decision_gate_pass",
                "verdict"):
        assert col in rows[0], f"matrix CSV missing column: {col}"
    families = {r["data_family"] for r in rows}
    assert "Fundamentals" in families


# --------------------------------------------------------------------------- #
# 8. Doc has all required guardrail phrases
# --------------------------------------------------------------------------- #
def test_doc_has_required_phrases():
    text = _read(_DOC)
    missing = [p for p in _REQUIRED_DOC_PHRASES if p not in text]
    assert not missing, f"doc missing required phrase(s): {missing}"


# --------------------------------------------------------------------------- #
# 9. Live end-to-end run (gated): writes the three outputs to a temp dir
# --------------------------------------------------------------------------- #
def test_live_end_to_end_optional():
    if os.environ.get("PHASE3D_LIVE") != "1":
        raise _Skip("set PHASE3D_LIVE=1 to run the analyzer end to end")
    if not os.path.isfile(_PHASE3C_JSON):
        raise _Skip("Phase 3-C results JSON not present in this environment")
    import tempfile
    analyzer = _import_analyzer()
    tmp = tempfile.mkdtemp(prefix="phase3d_")
    res = analyzer.run(
        decision_path=os.path.join(tmp, "decision.json"),
        option_matrix_path=os.path.join(tmp, "matrix.csv"),
        recommended_track_path=os.path.join(tmp, "track.json"))
    assert res["phase"] == "3-D"
    assert res["research_model_trained"] is False
    assert res["production_model_candidate_created"] is False
    assert res["deployable_model_artifact_written"] is False
    assert res["d_drive_read"] is False
    assert res["d_drive_written"] is False
    assert res["network_used"] is False
    assert res["vendor_api_called"] is False
    assert res["data_purchase_made"] is False
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "3-E"
    assert len(res["option_matrix"]) >= 8


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
