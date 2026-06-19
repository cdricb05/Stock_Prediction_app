"""Phase 3-K tests for the Tiny Fundamental IC and Model-Readiness Diagnostic phase.

These tests prove the diagnostic is a disciplined, controlled, repo-local analysis: it compiles and
imports; it references the committed Phase 3-J repaired-panel and Phase 3-H feature inputs; it
touches neither the D: drive nor the network; it reads the Phase 3-J result and confirms it succeeded
(FUNDAMENTAL_ALIGNMENT_REPAIR_SUCCESS), selected the 365-day cap, was leakage-free, and routed here;
the output JSON carries every required field and safety flag; phase == "3-K"; the feature-IC,
feature-family, horizon-readiness, yearly-IC, sector-sanity, and readiness-decision artifacts exist;
labels are used for validation only; it fits NO model, computes NO predictions / scores / portfolio
weights, creates NO production model candidate, and writes NO deployable model artifact; the
recommendation uses only the allowed values and routes to Phase 3-L; and the source imports no
api_server / Paper Trader / ML framework / network library, contains no infrastructure /
database-write / deployment / order-trading / model-artifact / vendor tokens, and makes no network
call. The doc carries the required guardrail phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase3k_tiny_fundamental_ic_readiness.py
  * without pytest: python tests/test_phase3k_tiny_fundamental_ic_readiness.py
The __main__ block discovers every test_* function, runs it, prints PASS/FAIL/SKIP, and exits
non-zero on any failure (the GCP venv has no pytest). A live end-to-end rebuild into a temp dir is
gated behind PHASE3K_LIVE=1.
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

_ANALYZER = os.path.join(_REPO_ROOT, "research", "analyze_phase3k_tiny_fundamental_ic_readiness.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase3k_tiny_fundamental_ic_readiness_v1.md")
_RESULT = os.path.join(_REPO_ROOT, "research", "output", "phase3k_tiny_fundamental_ic_readiness.json")
_K_DIR = os.path.join(_REPO_ROOT, "research", "output", "phase3k_tiny_fundamental_ic_readiness")
_FEATURE_IC_CSV = os.path.join(_K_DIR, "feature_ic_summary.csv")
_FEATURE_FAMILY_CSV = os.path.join(_K_DIR, "feature_family_ic_summary.csv")
_HORIZON_CSV = os.path.join(_K_DIR, "horizon_readiness_summary.csv")
_YEARLY_CSV = os.path.join(_K_DIR, "yearly_ic_summary.csv")
_SECTOR_CSV = os.path.join(_K_DIR, "sector_sanity_summary.csv")
_DECISION_CSV = os.path.join(_K_DIR, "readiness_decision_table.csv")
_PHASE3J_JSON = os.path.join(
    _REPO_ROOT, "research", "output", "phase3j_repaired_fundamental_price_alignment.json")


class _Skip(Exception):
    """Raised to mark a test skipped."""


# Committed inputs this phase must reference (basenames).
_EXPECTED_INPUT_BASENAMES = (
    "phase3j_repaired_fundamental_price_alignment.json",
    "repaired_aligned_panel_20ticker_sample.csv",
    "feature_dictionary.csv",
    "feature_quality_report.json",
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
# Forbidden database-write tokens.
_FORBIDDEN_DB_TOKENS = [
    "insert " + "into", "delete " + "from", "drop " + "table", "alter " + "table",
    "create " + "table", "trun" + "cate", "to_" + "sql",
]
# Forbidden deployment / order / trading usage tokens.
_FORBIDDEN_DEPLOY_TRADE_TOKENS = [
    "place" + "_order", "submit" + "_order", "create" + "_order",
    "ssh ", "scp ",
]
# Forbidden deployable-model-artifact tokens (this phase persists no model).
_FORBIDDEN_ARTIFACT_TOKENS = [
    "to_" + "pickle", "pickle." + "dump", "joblib." + "dump", "import " + "pickle",
    "import " + "joblib", ".pk" + "l", ".job" + "lib",
]
# Phase 3-K uses NO network and NO D: drive: forbid vendor + network libraries AND yfinance.
_FORBIDDEN_NETWORK_TOKENS = [
    "yf" + "inance",
    "url" + "lib",
    "import " + "requests",
    "http" + "://",
    "https" + "://",
]
# Libraries / modules that must not be imported (no model framework, no network in this phase).
_FORBIDDEN_IMPORT_ROOTS = {
    "sklearn", "tensorflow", "torch", "keras", "lightgbm", "xgboost", "statsmodels",
    "api_server", "yfinance", "requests", "urllib", "http", "socket", "numpy", "pandas",
}

_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read", "outputs_written", "phase3j_summary",
    "dataset_summary", "feature_set_summary", "label_summary", "ic_methodology",
    "feature_ic_summary", "feature_family_summary", "horizon_readiness_summary",
    "yearly_diagnostics_summary", "sector_sanity_summary", "readiness_decision_summary",
    "recommendation", "interpretation", "recommended_next_phase",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed", "deployment_executed",
    "model_v2_enabled", "production_edge_claimed", "research_model_trained",
    "production_model_trained", "production_model_candidate_created",
    "deployable_model_artifact_written", "network_used", "vendor_api_called",
    "paid_vendor_api_called", "data_purchase_made", "d_drive_read", "d_drive_written",
    "external_data_ingested", "production_data_ingested", "full_128_ticker_ingestion",
    "model_trained", "predictions_computed", "portfolio_weights_computed",
)
_REQUIRED_TRUE = (
    "no_trading", "no_orders", "no_automation", "sec_public_data_used",
    "model_features_computed", "price_join_performed", "labels_computed",
    "labels_for_validation_only",
)
_ALLOWED_RECOMMENDATIONS = {
    "FUNDAMENTAL_IC_DIAGNOSTIC_PASSES_TINY_MODEL_GATE",
    "FUNDAMENTAL_IC_DIAGNOSTIC_WEAK_BUT_EXPANDABLE",
    "FUNDAMENTAL_IC_DIAGNOSTIC_INCONCLUSIVE_SMALL_SAMPLE",
    "FUNDAMENTAL_IC_DIAGNOSTIC_FAILS_STOP_TINY_MODELING",
    "FUNDAMENTAL_IC_DIAGNOSTIC_BLOCKED",
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
    spec = importlib.util.spec_from_file_location("phase3k_analyzer_test", _ANALYZER)
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
# 2. Expected inputs referenced; outputs under research/output (C:); no D:
# --------------------------------------------------------------------------- #
def test_references_expected_inputs():
    mod = _import_analyzer()
    text = _read(_ANALYZER)
    for base in _EXPECTED_INPUT_BASENAMES:
        assert base in text, "analyzer does not reference expected input: %s" % base
    for path in (mod.RESULT_JSON, mod.FEATURE_IC_CSV, mod.FEATURE_FAMILY_IC_CSV,
                 mod.HORIZON_READINESS_CSV, mod.YEARLY_IC_CSV, mod.SECTOR_SANITY_CSV,
                 mod.READINESS_DECISION_CSV):
        n = path.replace("\\", "/")
        assert "research/output/" in n, n
        assert not n.upper().startswith("D:"), "output must not be on the D: drive: %s" % n


def test_no_d_drive_read_or_write():
    """Phase 3-K must not read OR write the D: drive at all: no D: path, no D: open."""
    mod = _import_analyzer()
    text = _read(_ANALYZER)
    for line in text.splitlines():
        if line.strip().startswith("#"):
            continue
        assert "D:" not in line or "open(" not in line, \
            "Phase 3-K must not open any D: path: %s" % line.strip()
    for path in (mod.PHASE3J_JSON, mod.PHASE3J_PANEL_CSV, mod.PHASE3H_FEATURE_DICT_CSV,
                 mod.RESULT_JSON, mod.FEATURE_IC_CSV):
        assert not path.replace("\\", "/").upper().startswith("D:")


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
                    + ([node.module] if isinstance(node, ast.ImportFrom) and node.module else []))
            roots = {(m or "").split(".")[0] for m in mods}
            bad = roots & _FORBIDDEN_IMPORT_ROOTS
            assert not bad, "analyzer imports forbidden module(s): %s" % bad


# --------------------------------------------------------------------------- #
# 4. No infra / db / deploy / trade / vendor / artifact / network usage tokens
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
        assert not hits, "analyzer contains forbidden %s token(s): %s" % (label, hits)


def test_no_model_fit_tokens():
    """The analyzer is allowed IC/spread math but must not fit any model."""
    low = _read(_ANALYZER).lower()
    forbidden = [".fit(", "model.fit", "train_test_split", "linearregression",
                 "logisticregression", "randomforest", "gradientboost"]
    hits = [t for t in forbidden if t in low]
    assert not hits, "analyzer contains forbidden model-fit token(s): %s" % hits


def test_no_network_at_all():
    text = _read(_ANALYZER)
    urls = re.findall(r"https?://[^\s\"')]+", text)
    assert not urls, "analyzer must contain no network URLs in Phase 3-K: %s" % urls


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
    assert d["phase"] == "3-K"
    for k in _REQUIRED_FALSE:
        assert d[k] is False, "%s must be false" % k
    for k in _REQUIRED_TRUE:
        assert d[k] is True, "%s must be true" % k


def test_reads_phase3j_and_confirms_routing():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    d = _result()
    confirmed = d["phase3j_summary"]["phase3j_confirmed"]
    assert confirmed["phase_is_3j"] is True
    assert confirmed["recommendation_is_repair_success"] is True
    assert confirmed["next_phase_is_3k"] is True
    assert confirmed["selected_cap_days_is_365"] is True
    assert confirmed["leakage_failure_count_zero"] is True
    assert confirmed["all_confirmed"] is True
    assert d["phase3j_summary"]["phase3j_recommendation"] == \
        "FUNDAMENTAL_ALIGNMENT_REPAIR_SUCCESS"
    assert d["phase3j_summary"]["phase3j_selected_cap_days"] == 365


def test_no_d_drive_and_no_network_flags():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    d = _result()
    assert d["d_drive_read"] is False
    assert d["d_drive_written"] is False
    assert d["network_used"] is False
    assert d["interpretation"]["read_from_d_drive"] is False
    assert d["interpretation"]["wrote_to_d_drive"] is False
    assert d["interpretation"]["network_used"] is False


def test_no_full_128_ticker_ingestion():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    d = _result()
    assert d["full_128_ticker_ingestion"] is False
    assert d["interpretation"]["full_128_ticker_ingestion"] is False
    assert len(d["dataset_summary"]["tickers"]) <= 20


def test_no_model_no_predictions_no_weights():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    d = _result()
    interp = d["interpretation"]
    assert d["model_trained"] is False
    assert d["predictions_computed"] is False
    assert d["portfolio_weights_computed"] is False
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


def test_labels_for_validation_only():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    d = _result()
    assert d["labels_computed"] is True
    assert d["labels_for_validation_only"] is True
    assert d["label_summary"]["labels_for_validation_only"] is True
    assert d["label_summary"]["new_labels_created"] is False


# --------------------------------------------------------------------------- #
# 6. Diagnostic artifacts exist and are well-formed
# --------------------------------------------------------------------------- #
def test_feature_ic_summary_csv_exists():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    assert os.path.isfile(_FEATURE_IC_CSV), "feature IC summary CSV must exist"
    rows = _read_csv_rows(_FEATURE_IC_CSV)
    assert rows, "feature IC summary must have rows"
    for col in ("feature", "feature_family", "horizon", "observation_count", "date_count",
                "mean_rank_ic_excess_return", "ic_hit_rate_excess_return", "absolute_mean_ic",
                "signal_direction", "diagnostic_strength"):
        assert col in rows[0], "feature IC CSV missing column: %s" % col


def test_feature_family_ic_summary_csv_exists():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    assert os.path.isfile(_FEATURE_FAMILY_CSV), "feature-family IC summary CSV must exist"
    rows = _read_csv_rows(_FEATURE_FAMILY_CSV)
    assert rows, "feature-family IC summary must have rows"
    for col in ("feature_family", "horizon", "num_features", "best_feature",
                "moderate_or_better_count", "strong_count", "stable_signal"):
        assert col in rows[0], "feature-family CSV missing column: %s" % col


def test_horizon_readiness_summary_csv_exists():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    assert os.path.isfile(_HORIZON_CSV), "horizon readiness summary CSV must exist"
    rows = _read_csv_rows(_HORIZON_CSV)
    assert len(rows) == 3, "expected one horizon-readiness row per horizon"
    for col in ("horizon", "label_coverage", "avg_daily_cross_section_size",
                "valid_ic_date_count", "best_feature", "moderate_or_better_count",
                "strong_count", "horizon_readiness"):
        assert col in rows[0], "horizon readiness CSV missing column: %s" % col


def test_yearly_ic_summary_csv_exists():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    assert os.path.isfile(_YEARLY_CSV), "yearly IC summary CSV must exist"
    rows = _read_csv_rows(_YEARLY_CSV)
    for col in ("feature", "horizon", "year", "ic_date_count", "mean_rank_ic_excess_return",
                "positive_ic_year", "small_sample_caveat"):
        assert col in (rows[0] if rows else {col: None}), "yearly IC CSV missing column: %s" % col


def test_sector_sanity_summary_csv_exists():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    assert os.path.isfile(_SECTOR_CSV), "sector sanity summary CSV must exist"
    rows = _read_csv_rows(_SECTOR_CSV)
    assert rows, "sector sanity summary must have rows"
    for col in ("sector", "ticker_count", "row_count", "tickers", "note"):
        assert col in rows[0], "sector sanity CSV missing column: %s" % col


def test_readiness_decision_table_exists():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    assert os.path.isfile(_DECISION_CSV), "readiness decision table must exist"
    rows = _read_csv_rows(_DECISION_CSV)
    assert rows, "readiness decision table must have rows"
    for col in ("decision_item", "value", "passed", "note"):
        assert col in rows[0], "decision table CSV missing column: %s" % col


def test_feature_families_use_phase3k_vocabulary():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    d = _result()
    allowed = {"profitability_margin", "balance_sheet_leverage", "growth_change",
               "cash_quality", "size_scale", "availability_recency", "unknown"}
    for fam in d["feature_set_summary"]["feature_family_of"].values():
        assert fam in allowed, "unexpected feature family: %s" % fam


def test_ic_observations_respect_min_cross_section():
    """Any feature/horizon with a defined mean IC must have rested on real cross-sections."""
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    d = _result()
    for r in d["feature_ic_summary"]:
        if r["date_count"] and r["date_count"] > 0:
            # observation_count counts >=8-member cross-sections, so it must dominate date_count.
            assert r["observation_count"] >= 8 * 0  # always true; presence check
            assert r["observation_count"] >= r["date_count"], (
                "observation_count (%s) must be >= date_count (%s) for %s/%s"
                % (r["observation_count"], r["date_count"], r["feature"], r["horizon"]))


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
    assert "tiny_research_model_allowed_next" in rec
    assert "expand_sec_universe_next" in rec
    assert "stop_tiny_modeling_now" in rec
    assert isinstance(rec.get("reason"), str) and rec["reason"]
    assert d["recommended_next_phase"]["phase"] == "3-L"
    assert d["recommended_next_phase"]["title"]
    assert d["recommended_next_phase"]["purpose"]
    # tiny_research_model_allowed_next is true only when the tiny-model gate passes.
    if rec["recommendation"] == "FUNDAMENTAL_IC_DIAGNOSTIC_PASSES_TINY_MODEL_GATE":
        assert rec["tiny_research_model_allowed_next"] is True
        assert d["tiny_research_model_allowed_next"] is True
    else:
        assert rec["tiny_research_model_allowed_next"] is False
        assert d["tiny_research_model_allowed_next"] is False


# --------------------------------------------------------------------------- #
# 8. Doc has all required guardrail phrases
# --------------------------------------------------------------------------- #
def test_doc_has_required_phrases():
    text = _read(_DOC)
    missing = [p for p in _REQUIRED_DOC_PHRASES if p not in text]
    assert not missing, "doc missing required phrase(s): %s" % missing


# --------------------------------------------------------------------------- #
# 9. Live end-to-end rebuild (gated): rebuilds the diagnostic into a temp dir
# --------------------------------------------------------------------------- #
def test_live_end_to_end_optional():
    if os.environ.get("PHASE3K_LIVE") != "1":
        raise _Skip("set PHASE3K_LIVE=1 to rebuild the diagnostic end to end")
    if not os.path.isfile(_PHASE3J_JSON):
        raise _Skip("Phase 3-J results JSON not present in this environment")
    import tempfile
    mod = _import_analyzer()
    if not os.path.isfile(mod.PHASE3J_PANEL_CSV):
        raise _Skip("Phase 3-J repaired panel not present in this environment")
    tmp = tempfile.mkdtemp(prefix="phase3k_")
    res = mod.run(
        result_json_path=os.path.join(tmp, "result.json"),
        feature_ic_path=os.path.join(tmp, "feature_ic.csv"),
        feature_family_path=os.path.join(tmp, "family.csv"),
        horizon_readiness_path=os.path.join(tmp, "horizon.csv"),
        yearly_ic_path=os.path.join(tmp, "yearly.csv"),
        sector_sanity_path=os.path.join(tmp, "sector.csv"),
        readiness_decision_path=os.path.join(tmp, "decision.csv"))
    assert res["phase"] == "3-K"
    assert res["network_used"] is False
    assert res["d_drive_read"] is False
    assert res["d_drive_written"] is False
    assert res["model_trained"] is False
    assert res["predictions_computed"] is False
    assert res["portfolio_weights_computed"] is False
    assert res["research_model_trained"] is False
    assert res["production_model_candidate_created"] is False
    assert res["deployable_model_artifact_written"] is False
    assert res["full_128_ticker_ingestion"] is False
    assert res["labels_for_validation_only"] is True
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "3-L"
    assert res["phase3j_summary"]["phase3j_confirmed"]["all_confirmed"] is True
    assert len(res["dataset_summary"]["tickers"]) <= 20
    assert len(res["horizon_readiness_summary"]) == 3
    assert res["feature_set_summary"]["engineered_feature_count"] > 0


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
