"""Phase 3-L tests for the Full SEC Universe Expansion + End-to-End Fundamental Signal Gate.

These tests prove the gate is a disciplined, research-only, safety-controlled phase: it compiles and
imports; it references the committed Phase 3-K / Phase 3-J / Phase 3-H / Phase 3-E inputs and the
sector map; it reads the D: price panel READ ONLY and writes nothing to D:; it reads the Phase 3-K
result and confirms it was inconclusive (FUNDAMENTAL_IC_DIAGNOSTIC_INCONCLUSIVE_SMALL_SAMPLE) and
routed here; the output JSON carries every required field and safety flag; phase == "3-L"; the
required CSV / JSON artifacts exist; labels are for validation only; it fits NO model, computes NO
predictions / scores / portfolio weights, creates NO production model candidate, and writes NO
deployable model artifact; network use is restricted to official SEC domains only (no paid vendor,
no yfinance); the recommendation uses only the allowed values and routes to Phase 3-M; and the
source imports no api_server / Paper Trader / ML framework, contains no infrastructure /
database-write / deployment / order-trading / model-artifact tokens. The doc carries the required
guardrail phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase3l_sec_universe_signal_gate.py
  * without pytest: python tests/test_phase3l_sec_universe_signal_gate.py
The __main__ block discovers every test_* function, runs it, prints PASS/FAIL/SKIP, and exits
non-zero on any failure. A live end-to-end rebuild (gated behind PHASE3L_LIVE=1) re-runs the gate on
a small ticker cap into a temp dir; it uses the SEC cache when present and the network otherwise.
"""
from __future__ import annotations

import ast
import csv
import importlib.util
import json
import os
import re
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_ANALYZER = os.path.join(_REPO_ROOT, "research", "run_phase3l_sec_universe_signal_gate.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase3l_sec_universe_signal_gate_v1.md")
_RESULT = os.path.join(_REPO_ROOT, "research", "output", "phase3l_sec_universe_signal_gate.json")
_L_DIR = os.path.join(_REPO_ROOT, "research", "output", "phase3l_sec_universe_signal_gate")


class _Skip(Exception):
    """Raised to mark a test skipped."""


# Committed inputs this phase must reference (basenames / path fragments).
_EXPECTED_INPUT_FRAGMENTS = (
    "phase3k_tiny_fundamental_ic_readiness.json",
    "phase3j_repaired_fundamental_price_alignment.json",
    "feature_dictionary.csv",
    "phase3e_ingestion_contract.json",
    "phase2k_p_sector_map_current.csv",
    "phase2k_g_expanded_price_history_free.csv",
)

# Required output artifacts (basenames).
_EXPECTED_OUTPUT_BASENAMES = (
    "company_identity_universe.csv",
    "fundamentals_universe.csv",
    "feature_snapshot_universe.csv",
    "aligned_feature_price_panel_universe.csv",
    "data_quality_report.json",
    "field_coverage_by_ticker.csv",
    "feature_coverage_by_ticker.csv",
    "staleness_summary.csv",
    "leakage_checks.csv",
    "label_summary_by_horizon.csv",
    "feature_ic_summary.csv",
    "feature_family_ic_summary.csv",
    "horizon_readiness_summary.csv",
    "yearly_ic_summary.csv",
    "sector_sanity_summary.csv",
    "decision_table.csv",
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
    "uvi" + "corn",
]
_FORBIDDEN_DB_TOKENS = [
    "insert " + "into", "delete " + "from", "drop " + "table", "alter " + "table",
    "create " + "table", "trun" + "cate", "to_" + "sql",
]
_FORBIDDEN_DEPLOY_TRADE_TOKENS = [
    "place" + "_order", "submit" + "_order", "create" + "_order",
    "ssh ", "scp ",
]
_FORBIDDEN_ARTIFACT_TOKENS = [
    "to_" + "pickle", "pickle." + "dump", "joblib." + "dump", "import " + "pickle",
    "import " + "joblib", ".pk" + "l", ".job" + "lib",
]
# Network IS allowed in Phase 3-L, but ONLY to official SEC public domains; paid vendor + yfinance
# remain forbidden.  urllib (stdlib) is permitted, so it is NOT in the forbidden list.
_FORBIDDEN_NETWORK_TOKENS = [
    "yf" + "inance",
    "import " + "requests",
    "from " + "requests",
    "alpha" + "vantage",
    "fin" + "nhub",
    "poly" + "gon.io",
    "iex" + "cloud",
]
_FORBIDDEN_IMPORT_ROOTS = {
    "sklearn", "tensorflow", "torch", "keras", "lightgbm", "xgboost", "statsmodels",
    "api_server", "yfinance", "requests",
}
# Only these network hosts may appear in any URL in the source.
_ALLOWED_NETWORK_HOSTS = {"www.sec.gov", "data.sec.gov"}

_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read", "outputs_written", "phase3k_summary",
    "universe_summary", "sec_access_summary", "fundamentals_summary", "feature_snapshot_summary",
    "alignment_summary", "staleness_summary", "label_summary", "leakage_check_summary",
    "ic_methodology", "feature_ic_summary", "feature_family_summary", "horizon_readiness_summary",
    "yearly_stability_summary", "sector_sanity_summary", "decision_summary", "recommendation",
    "interpretation", "recommended_next_phase",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed", "deployment_executed",
    "model_v2_enabled", "production_edge_claimed", "research_model_trained",
    "production_model_trained", "production_model_candidate_created",
    "deployable_model_artifact_written", "vendor_api_called", "paid_vendor_api_called",
    "data_purchase_made", "d_drive_written", "production_data_ingested", "model_trained",
    "predictions_computed", "portfolio_weights_computed",
)
_REQUIRED_TRUE = (
    "no_trading", "no_orders", "no_automation", "sec_public_data_used", "d_drive_read",
    "external_data_ingested", "model_features_computed", "price_join_performed", "labels_computed",
    "labels_for_validation_only",
)
_ALLOWED_RECOMMENDATIONS = {
    "SEC_UNIVERSE_SIGNAL_GATE_PASSES_RESEARCH_MODEL_ALLOWED",
    "SEC_UNIVERSE_SIGNAL_GATE_WEAK_BUT_EXPAND_OR_ADD_REVISIONS",
    "SEC_UNIVERSE_SIGNAL_GATE_INCONCLUSIVE_DATA_COVERAGE",
    "SEC_UNIVERSE_SIGNAL_GATE_FAILS_ADD_RICHER_DATA",
    "SEC_UNIVERSE_SIGNAL_GATE_BLOCKED",
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


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _read_csv_rows(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _import_analyzer():
    spec = importlib.util.spec_from_file_location("phase3l_analyzer_test", _ANALYZER)
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
# 2. Expected inputs referenced; outputs under research/output (C:)
# --------------------------------------------------------------------------- #
def test_references_expected_inputs():
    text = _read(_ANALYZER)
    for frag in _EXPECTED_INPUT_FRAGMENTS:
        assert frag in text, "analyzer does not reference expected input: %s" % frag


def test_outputs_under_research_output():
    mod = _import_analyzer()
    for path in (mod.RESULT_JSON, mod.COMPANY_IDENTITY_CSV, mod.FUNDAMENTALS_CSV,
                 mod.FEATURE_SNAPSHOT_CSV, mod.ALIGNED_PANEL_CSV, mod.FEATURE_IC_CSV,
                 mod.DECISION_TABLE_CSV):
        n = path.replace("\\", "/")
        assert "research/output/" in n, n
        assert not n.upper().startswith("D:"), "output must not be on the D: drive: %s" % n


def test_d_drive_is_read_only():
    """Only the price panel paths may reference D:, and the analyzer never writes to D:."""
    mod = _import_analyzer()
    # Every D: path is one of the read-only price inputs.
    for path in (mod.PRICE_CSV, mod.PRICE_QUALITY_JSON, mod.PRICE_SURVIVORSHIP_JSON):
        assert path.replace("\\", "/").upper().startswith("D:")
    assert mod.RESULT_JSON.replace("\\", "/").upper().startswith("D:") is False
    # No output path is on D:.
    for path in (mod.COMPANY_IDENTITY_CSV, mod.FUNDAMENTALS_CSV, mod.FEATURE_SNAPSHOT_CSV,
                 mod.ALIGNED_PANEL_CSV, mod.DATA_QUALITY_JSON):
        assert not path.replace("\\", "/").upper().startswith("D:")
    # The safety flags hard-code no D: write.
    flags = mod.build_safety_flags(mod.REC_INCONCLUSIVE, network_used=True)
    assert flags["d_drive_read"] is True
    assert flags["d_drive_written"] is False


# --------------------------------------------------------------------------- #
# 3. No api_server / Paper Trader / ML-framework imports
# --------------------------------------------------------------------------- #
def test_no_forbidden_imports():
    text = _read(_ANALYZER)
    assert "paper_trader" not in text.lower(), "analyzer must not reference paper_trader"
    assert "import api_server" not in text and "from api_server" not in text
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = ([n.name for n in node.names]
                    + ([node.module] if isinstance(node, ast.ImportFrom) and node.module else []))
            roots = {(m or "").split(".")[0] for m in mods}
            bad = roots & _FORBIDDEN_IMPORT_ROOTS
            assert not bad, "analyzer imports forbidden module(s): %s" % bad


# --------------------------------------------------------------------------- #
# 4. No infra / db / deploy / trade / vendor / artifact usage tokens
# --------------------------------------------------------------------------- #
def test_no_forbidden_usage_tokens():
    low = _read(_ANALYZER).lower()
    for label, tokens in (
        ("infrastructure", _FORBIDDEN_INFRA_TOKENS),
        ("database-write", _FORBIDDEN_DB_TOKENS),
        ("deploy/trade", _FORBIDDEN_DEPLOY_TRADE_TOKENS),
        ("network-vendor", _FORBIDDEN_NETWORK_TOKENS),
        ("model-artifact", _FORBIDDEN_ARTIFACT_TOKENS),
    ):
        hits = [t for t in tokens if t.lower() in low]
        assert not hits, "analyzer contains forbidden %s token(s): %s" % (label, hits)


def test_no_model_fit_tokens():
    low = _read(_ANALYZER).lower()
    forbidden = [".fit(", "model.fit", "train_test_split", "linearregression",
                 "logisticregression", "randomforest", "gradientboost"]
    hits = [t for t in forbidden if t in low]
    assert not hits, "analyzer contains forbidden model-fit token(s): %s" % hits


def test_network_restricted_to_sec_domains():
    """Every URL in the source must point at an official SEC public host (no other domains)."""
    text = _read(_ANALYZER)
    urls = re.findall(r"https?://[^\s\"')]+", text)
    assert urls, "expected SEC URLs in a network-using phase"
    for u in urls:
        host = re.sub(r"^https?://", "", u).split("/")[0]
        assert host in _ALLOWED_NETWORK_HOSTS, "non-SEC network host in source: %s" % u


def test_sec_user_agent_and_caps():
    mod = _import_analyzer()
    assert mod.SEC_USER_AGENT == "PaperTraderResearch/Phase3L cedric.binisti.research@example.com"
    assert mod.MIN_REQUEST_INTERVAL_S >= 0.25
    assert mod.MAX_TOTAL_REQUESTS <= 270
    assert set(mod.ALLOWED_SEC_HOSTS) <= _ALLOWED_NETWORK_HOSTS


# --------------------------------------------------------------------------- #
# 5. Committed result artifact: required fields, safety flags, structure
# --------------------------------------------------------------------------- #
def _result():
    return json.loads(_read(_RESULT))


def test_committed_result_artifact_valid():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    d = _result()
    for k in _REQUIRED_JSON_FIELDS:
        assert k in d, "committed JSON missing field: %s" % k
    assert d["phase"] == "3-L"
    for k in _REQUIRED_FALSE:
        assert d[k] is False, "%s must be false" % k
    for k in _REQUIRED_TRUE:
        assert d[k] is True, "%s must be true" % k


def test_reads_phase3k_and_confirms_routing():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    d = _result()
    confirmed = d["phase3k_summary"]["phase3k_confirmed"]
    assert confirmed["phase_is_3k"] is True
    assert confirmed["recommendation_is_inconclusive_small_sample"] is True
    assert confirmed["tiny_research_model_allowed_next_false"] is True
    assert confirmed["expand_sec_universe_next_true"] is True
    assert confirmed["next_phase_is_3l"] is True
    assert confirmed["model_trained_false"] is True
    assert confirmed["predictions_computed_false"] is True
    assert confirmed["portfolio_weights_computed_false"] is True
    assert confirmed["production_model_candidate_created_false"] is True
    assert confirmed["all_confirmed"] is True
    assert d["phase3k_summary"]["phase3k_recommendation"] == \
        "FUNDAMENTAL_IC_DIAGNOSTIC_INCONCLUSIVE_SMALL_SAMPLE"


def test_no_production_candidate_no_model_no_predictions_no_weights():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    d = _result()
    interp = d["interpretation"]
    assert d["model_trained"] is False
    assert d["predictions_computed"] is False
    assert d["portfolio_weights_computed"] is False
    assert d["production_model_candidate_created"] is False
    assert d["deployable_model_artifact_written"] is False
    assert interp["model_trained"] is False
    assert interp["research_model_trained"] is False
    assert interp["production_model_trained"] is False
    assert interp["production_model_candidate_created"] is False
    assert interp["deployable_model_artifact_written"] is False
    assert interp["predictions_computed"] is False
    assert interp["scores_computed"] is False
    assert interp["portfolio_weights_computed"] is False
    rec = d["recommendation"]
    assert rec["create_production_model_candidate_now"] is False
    assert rec["train_production_model_now"] is False
    assert rec["deploy_now"] is False
    assert rec["production_edge_claimed"] is False


def test_d_drive_read_only_and_sec_only_network():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    d = _result()
    assert d["d_drive_read"] is True
    assert d["d_drive_written"] is False
    assert d["sec_public_data_used"] is True
    assert d["vendor_api_called"] is False
    assert d["paid_vendor_api_called"] is False
    assert d["data_purchase_made"] is False
    hosts = set(d["sec_access_summary"]["allowed_hosts"])
    assert hosts <= _ALLOWED_NETWORK_HOSTS


def test_labels_for_validation_only():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    d = _result()
    assert d["labels_computed"] is True
    assert d["labels_for_validation_only"] is True
    assert d["label_summary"]["labels_for_validation_only"] is True


def test_leakage_zero_unless_blocked():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    d = _result()
    rec = d["recommendation"]["recommendation"]
    if rec != "SEC_UNIVERSE_SIGNAL_GATE_BLOCKED":
        assert d["leakage_check_summary"]["leakage_failure_count"] == 0, \
            "non-blocked run must be leakage-free"


# --------------------------------------------------------------------------- #
# 6. Diagnostic artifacts exist and are well-formed
# --------------------------------------------------------------------------- #
def test_output_artifacts_exist():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    for base in _EXPECTED_OUTPUT_BASENAMES:
        assert os.path.isfile(os.path.join(_L_DIR, base)), "missing output artifact: %s" % base


def test_feature_ic_summary_csv_well_formed():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    rows = _read_csv_rows(os.path.join(_L_DIR, "feature_ic_summary.csv"))
    assert rows, "feature IC summary must have rows"
    for col in ("feature", "feature_family", "horizon", "observation_count", "date_count",
                "mean_rank_ic_excess_return", "absolute_mean_ic", "diagnostic_strength"):
        assert col in rows[0], "feature IC CSV missing column: %s" % col


def test_horizon_readiness_csv_one_row_per_horizon():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    rows = _read_csv_rows(os.path.join(_L_DIR, "horizon_readiness_summary.csv"))
    assert len(rows) == 3, "expected one horizon-readiness row per horizon"
    for col in ("horizon", "label_coverage", "dense_ic_date_count", "distinct_ic_years",
                "horizon_readiness"):
        assert col in rows[0], "horizon readiness CSV missing column: %s" % col


def test_decision_table_well_formed():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    rows = _read_csv_rows(os.path.join(_L_DIR, "decision_table.csv"))
    assert rows, "decision table must have rows"
    for col in ("decision_item", "value", "passed", "note"):
        assert col in rows[0], "decision table CSV missing column: %s" % col


def test_feature_families_use_phase3k_vocabulary():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    d = _result()
    allowed = {"profitability_margin", "balance_sheet_leverage", "growth_change",
               "cash_quality", "size_scale", "availability_recency", "unknown"}
    for fam in d["feature_snapshot_summary"]["feature_family_of"].values():
        assert fam in allowed, "unexpected feature family: %s" % fam


# --------------------------------------------------------------------------- #
# 7. Recommendation + next phase
# --------------------------------------------------------------------------- #
def test_recommendation_and_next_phase():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    d = _result()
    rec = d["recommendation"]
    assert rec["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert rec["create_production_model_candidate_now"] is False
    assert rec["train_production_model_now"] is False
    assert rec["deploy_now"] is False
    assert rec["production_edge_claimed"] is False
    assert "research_model_allowed_next" in rec
    assert "add_richer_data_next" in rec
    assert "expand_or_repair_data_next" in rec
    assert isinstance(rec.get("reason"), str) and rec["reason"]
    assert d["recommended_next_phase"]["phase"] == "3-M"
    assert d["recommended_next_phase"]["title"]
    assert d["recommended_next_phase"]["purpose"]
    if rec["recommendation"] == "SEC_UNIVERSE_SIGNAL_GATE_PASSES_RESEARCH_MODEL_ALLOWED":
        assert rec["research_model_allowed_next"] is True
        assert d["research_model_allowed_next"] is True
    else:
        assert rec["research_model_allowed_next"] is False
        assert d["research_model_allowed_next"] is False


# --------------------------------------------------------------------------- #
# 8. Doc has all required guardrail phrases
# --------------------------------------------------------------------------- #
def test_doc_has_required_phrases():
    text = _read(_DOC)
    missing = [p for p in _REQUIRED_DOC_PHRASES if p not in text]
    assert not missing, "doc missing required phrase(s): %s" % missing


# --------------------------------------------------------------------------- #
# 9. Live end-to-end rebuild (gated): rebuilds the gate on a small ticker cap
# --------------------------------------------------------------------------- #
def test_live_end_to_end_optional():
    if os.environ.get("PHASE3L_LIVE") != "1":
        raise _Skip("set PHASE3L_LIVE=1 to rebuild the gate end to end (uses SEC cache/network)")
    import tempfile
    mod = _import_analyzer()
    if not os.path.isfile(mod.PHASE3K_JSON):
        raise _Skip("Phase 3-K results JSON not present in this environment")
    cap = int(os.environ.get("PHASE3L_LIVE_TICKERS", "12"))
    tmp = tempfile.mkdtemp(prefix="phase3l_")
    res = mod.run(result_json_path=os.path.join(tmp, "result.json"), l_dir=tmp,
                  raw_dir=mod._RAW_DIR, max_tickers=cap, verbose=False)
    assert res["phase"] == "3-L"
    assert res["d_drive_written"] is False
    assert res["model_trained"] is False
    assert res["predictions_computed"] is False
    assert res["portfolio_weights_computed"] is False
    assert res["production_model_candidate_created"] is False
    assert res["deployable_model_artifact_written"] is False
    assert res["labels_for_validation_only"] is True
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "3-M"
    assert set(res["sec_access_summary"]["allowed_hosts"]) <= _ALLOWED_NETWORK_HOSTS


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
            print("PASS %s" % name)
            passed += 1
        except _Skip as s:
            print("SKIP %s: %s" % (name, s))
            skipped += 1
        except Exception as e:  # noqa: BLE001
            import traceback
            print("FAIL %s: %s" % (name, e))
            failures.append((name, traceback.format_exc()))
            failed += 1
    print("\n%d passed, %d failed, %d skipped, %d total"
          % (passed, failed, skipped, len(tests)))
    if failures:
        print("\n--- failure details ---")
        for name, tb in failures:
            print("\n### %s\n%s" % (name, tb))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
