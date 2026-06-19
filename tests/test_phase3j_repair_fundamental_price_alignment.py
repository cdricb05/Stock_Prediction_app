"""Phase 3-J tests for the Repair Fundamental Price Alignment phase.

These tests prove the repair is a disciplined, controlled, repo-local dry run: it compiles and
imports; it references the committed Phase 3-I and Phase 3-H inputs; it touches neither the D: drive
nor the network; it reads the Phase 3-I result and confirms it succeeded partially
(FUNDAMENTAL_PRICE_ALIGNMENT_PARTIAL_SUCCESS), was leakage-free, and routed here (next phase 3-J);
the output JSON carries every required field and safety flag; phase == "3-J"; the repaired panel /
sensitivity / stale-rows / decision-table / quality / label-summary artifacts exist (the repaired
panel + label summary on success or partial success); every repaired row satisfies the
point-in-time invariants and the selected staleness cap; labels are filtered for validation only; it
fits no model, computes no predictions/scores, creates no production model candidate, and writes no
deployable model artifact; the recommendation uses only the allowed values and routes to Phase 3-K;
and the source imports no api_server / Paper Trader / ML framework / network library, contains no
infrastructure / database-write / deployment / order-trading / model-artifact / vendor tokens, and
makes no network call. The doc carries the required guardrail phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase3j_repair_fundamental_price_alignment.py
  * without pytest: python tests/test_phase3j_repair_fundamental_price_alignment.py
The __main__ block discovers every test_* function, runs it, prints PASS/FAIL/SKIP, and exits
non-zero on any failure (the GCP venv has no pytest). A live end-to-end rebuild into a temp dir is
gated behind PHASE3J_LIVE=1.
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

_BUILDER = os.path.join(_REPO_ROOT, "research", "repair_phase3j_fundamental_price_alignment.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase3j_repair_fundamental_price_alignment_v1.md")
_RESULT = os.path.join(
    _REPO_ROOT, "research", "output", "phase3j_repaired_fundamental_price_alignment.json")
_J_DIR = os.path.join(
    _REPO_ROOT, "research", "output", "phase3j_repaired_fundamental_price_alignment")
_REPAIRED_PANEL_CSV = os.path.join(_J_DIR, "repaired_aligned_panel_20ticker_sample.csv")
_SENSITIVITY_CSV = os.path.join(_J_DIR, "staleness_cap_sensitivity.csv")
_REPAIRED_QUALITY_JSON = os.path.join(_J_DIR, "repaired_alignment_quality_report.json")
_REPAIRED_LABEL_SUMMARY_CSV = os.path.join(_J_DIR, "repaired_label_summary_by_horizon.csv")
_DECISION_TABLE_CSV = os.path.join(_J_DIR, "repair_decision_table.csv")
_STALE_ROWS_CSV = os.path.join(_J_DIR, "stale_rows_by_ticker.csv")
_PHASE3I_JSON = os.path.join(
    _REPO_ROOT, "research", "output", "phase3i_fundamental_price_alignment.json")


class _Skip(Exception):
    """Raised to mark a test skipped."""


# Committed inputs this phase must reference (basenames).
_EXPECTED_INPUT_BASENAMES = (
    "phase3i_fundamental_price_alignment.json",
    "aligned_feature_price_panel_20ticker_sample.csv",
    "phase3h_sec_fundamental_features.json",
    "feature_snapshot_20ticker_sample.csv",
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
# Phase 3-J uses NO network and NO D: drive: forbid vendor + network libraries AND yfinance.
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
    "api_server", "yfinance", "requests", "urllib", "http", "socket",
}

_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read", "outputs_written", "phase3i_summary",
    "staleness_diagnostics", "staleness_cap_sensitivity", "selected_repair",
    "repaired_panel_summary", "repaired_label_summary", "leakage_check_summary",
    "repair_decision_summary", "source_limitations", "earnings_revisions_gap",
    "recommendation", "interpretation", "recommended_next_phase",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed", "deployment_executed",
    "model_v2_enabled", "production_edge_claimed", "research_model_trained",
    "production_model_trained", "production_model_candidate_created",
    "deployable_model_artifact_written", "network_used", "vendor_api_called",
    "paid_vendor_api_called", "data_purchase_made", "d_drive_read", "d_drive_written",
    "external_data_ingested", "production_data_ingested", "full_128_ticker_ingestion",
    "model_trained",
)
_REQUIRED_TRUE = (
    "no_trading", "no_orders", "no_automation", "sec_public_data_used",
    "model_features_computed", "price_join_performed", "labels_computed",
    "labels_for_validation_only",
)
_ALLOWED_RECOMMENDATIONS = {
    "FUNDAMENTAL_ALIGNMENT_REPAIR_SUCCESS",
    "FUNDAMENTAL_ALIGNMENT_REPAIR_PARTIAL_SUCCESS",
    "FUNDAMENTAL_ALIGNMENT_NEEDS_DENSER_SEC_HISTORY",
    "FUNDAMENTAL_ALIGNMENT_REPAIR_BLOCKED",
    "FUNDAMENTAL_ALIGNMENT_REPAIR_REJECTED",
}
_SUCCESS_OR_PARTIAL = {
    "FUNDAMENTAL_ALIGNMENT_REPAIR_SUCCESS",
    "FUNDAMENTAL_ALIGNMENT_REPAIR_PARTIAL_SUCCESS",
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


def _import_builder():
    spec = importlib.util.spec_from_file_location("phase3j_repair_test", _BUILDER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# 1. Builder compiles and imports
# --------------------------------------------------------------------------- #
def test_builder_compiles():
    compile(_read(_BUILDER), _BUILDER, "exec")


def test_builder_imports():
    _import_builder()


# --------------------------------------------------------------------------- #
# 2. Expected inputs referenced; outputs under research/output (C:); no D:
# --------------------------------------------------------------------------- #
def test_references_expected_inputs():
    builder = _import_builder()
    text = _read(_BUILDER)
    for base in _EXPECTED_INPUT_BASENAMES:
        assert base in text, "repair does not reference expected input: %s" % base
    for path in (builder.RESULT_JSON, builder.REPAIRED_PANEL_CSV, builder.STALENESS_SENSITIVITY_CSV,
                 builder.REPAIRED_QUALITY_JSON, builder.REPAIRED_LABEL_SUMMARY_CSV,
                 builder.REPAIR_DECISION_TABLE_CSV, builder.STALE_ROWS_BY_TICKER_CSV):
        n = path.replace("\\", "/")
        assert "research/output/" in n, n
        assert not n.upper().startswith("D:"), "output must not be on the D: drive: %s" % n


def test_no_d_drive_read_or_write():
    """Phase 3-J must not read OR write the D: drive at all: no D: path, no D: open."""
    builder = _import_builder()
    text = _read(_BUILDER)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "D:" not in line or "open(" not in line, \
            "Phase 3-J must not open any D: path: %s" % stripped
    for path in (builder.PHASE3I_PANEL_CSV, builder.PHASE3I_JSON, builder.PHASE3H_JSON,
                 builder.RESULT_JSON, builder.REPAIRED_PANEL_CSV):
        assert not path.replace("\\", "/").upper().startswith("D:")


# --------------------------------------------------------------------------- #
# 3. No api_server / Paper Trader / ML-framework / network imports
# --------------------------------------------------------------------------- #
def test_no_forbidden_imports():
    text = _read(_BUILDER)
    assert "paper_trader" not in text.lower(), "repair must not reference paper_trader"
    assert "import api_server" not in text and "from api_server" not in text
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = ([n.name for n in node.names]
                    + ([node.module] if isinstance(node, ast.ImportFrom)
                       and node.module else []))
            roots = {(m or "").split(".")[0] for m in mods}
            bad = roots & _FORBIDDEN_IMPORT_ROOTS
            assert not bad, "repair imports forbidden module(s): %s" % bad


# --------------------------------------------------------------------------- #
# 4. No infra / db / deploy / trade / vendor / artifact / network usage tokens
# --------------------------------------------------------------------------- #
def test_no_forbidden_usage_tokens():
    low = _read(_BUILDER).lower()
    for label, tokens in (
        ("infrastructure", _FORBIDDEN_INFRA_TOKENS),
        ("database-write", _FORBIDDEN_DB_TOKENS),
        ("deploy/trade", _FORBIDDEN_DEPLOY_TRADE_TOKENS),
        ("network", _FORBIDDEN_NETWORK_TOKENS),
        ("model-artifact", _FORBIDDEN_ARTIFACT_TOKENS),
    ):
        hits = [t for t in tokens if t.lower() in low]
        assert not hits, "repair contains forbidden %s token(s): %s" % (label, hits)


def test_no_network_at_all():
    text = _read(_BUILDER)
    urls = re.findall(r"https?://[^\s\"')]+", text)
    assert not urls, "repair must contain no network URLs in Phase 3-J: %s" % urls


# --------------------------------------------------------------------------- #
# 5. Committed result artifact: required fields, safety flags, structure
# --------------------------------------------------------------------------- #
def test_committed_result_artifact_valid():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed repair result JSON not present")
    d = json.loads(_read(_RESULT))
    for k in _REQUIRED_JSON_FIELDS:
        assert k in d, "committed repair JSON missing field: %s" % k
    assert d["phase"] == "3-J"
    for k in _REQUIRED_FALSE:
        assert d[k] is False, "%s must be false" % k
    for k in _REQUIRED_TRUE:
        assert d[k] is True, "%s must be true" % k


def test_reads_phase3i_and_confirms_routing():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed repair result JSON not present")
    d = json.loads(_read(_RESULT))
    confirmed = d["phase3i_summary"]["phase3i_confirmed"]
    assert confirmed["phase_is_3i"] is True
    assert confirmed["recommendation_is_partial_success"] is True
    assert confirmed["next_phase_is_3j"] is True
    assert confirmed["leakage_failure_count_zero"] is True
    assert confirmed["all_confirmed"] is True
    assert d["phase3i_summary"]["phase3i_recommendation"] == \
        "FUNDAMENTAL_PRICE_ALIGNMENT_PARTIAL_SUCCESS"


def test_phase3i_leakage_was_zero():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed repair result JSON not present")
    d = json.loads(_read(_RESULT))
    assert d["phase3i_summary"]["phase3i_leakage_failure_count"] == 0
    assert d["leakage_check_summary"]["phase3i_leakage_failure_count"] == 0


def test_no_d_drive_and_no_network_flags():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed repair result JSON not present")
    d = json.loads(_read(_RESULT))
    assert d["d_drive_read"] is False
    assert d["d_drive_written"] is False
    assert d["network_used"] is False
    assert d["interpretation"]["read_from_d_drive"] is False
    assert d["interpretation"]["wrote_to_d_drive"] is False
    assert d["interpretation"]["network_used"] is False


def test_no_full_128_ticker_ingestion():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed repair result JSON not present")
    d = json.loads(_read(_RESULT))
    assert d["full_128_ticker_ingestion"] is False
    assert d["interpretation"]["full_128_ticker_ingestion"] is False
    assert len(d["repaired_panel_summary"]["repaired_aligned_tickers"]) <= 20


def test_labels_filtered_for_validation_only_no_model():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed repair result JSON not present")
    d = json.loads(_read(_RESULT))
    interp = d["interpretation"]
    assert d["labels_computed"] is True
    assert d["labels_for_validation_only"] is True
    assert d["price_join_performed"] is True
    assert d["model_features_computed"] is True
    assert interp["labels_for_validation_only"] is True
    assert interp["new_labels_created"] is False
    assert interp["labels_filtered_only"] is True
    assert interp["model_trained"] is False
    assert interp["predictions_computed"] is False
    assert interp["scores_computed"] is False
    assert interp["portfolio_weights_computed"] is False
    assert interp["research_model_trained"] is False
    assert interp["production_model_trained"] is False
    assert interp["production_model_candidate_created"] is False
    assert interp["deployable_model_artifact_written"] is False
    rec = d["recommendation"]
    assert rec["train_production_model_now"] is False
    assert rec["create_production_model_candidate_now"] is False
    assert rec["model_training_now"] is False
    assert rec["deploy_now"] is False
    assert rec["full_128_ticker_ingestion_now"] is False
    assert rec["labels_for_validation_only"] is True


def test_earnings_revisions_gap_documented():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed repair result JSON not present")
    d = json.loads(_read(_RESULT))
    gap = d["earnings_revisions_gap"]
    assert gap["sec_provides_earnings_consensus"] is False
    assert gap["sec_provides_analyst_estimate_revisions"] is False
    assert gap["provider_selection_required"] is True


# --------------------------------------------------------------------------- #
# 6. Repair artifacts exist and are well-formed
# --------------------------------------------------------------------------- #
def _result():
    return json.loads(_read(_RESULT))


def _result_is_success_or_partial():
    return _result()["recommendation"]["recommendation"] in _SUCCESS_OR_PARTIAL


def test_staleness_sensitivity_csv_exists():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed repair result JSON not present")
    assert os.path.isfile(_SENSITIVITY_CSV), "staleness sensitivity CSV must always exist"
    rows = _read_csv_rows(_SENSITIVITY_CSV)
    assert rows, "staleness sensitivity must have rows (one per candidate cap)"
    for col in ("cap_days", "aligned_rows_after_cap", "retained_row_fraction",
                "aligned_ticker_count", "aligned_sector_count", "median_feature_age_days",
                "p75_feature_age_days", "p90_feature_age_days", "max_feature_age_days",
                "label_coverage_21d", "label_coverage_63d", "label_coverage_126d",
                "min_rows_by_ticker", "median_rows_by_ticker", "max_rows_by_ticker",
                "tickers_dropped", "sectors_dropped", "leakage_failure_count",
                "recommendation_for_cap"):
        assert col in rows[0], "sensitivity CSV missing column: %s" % col


def test_stale_rows_by_ticker_csv_exists():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed repair result JSON not present")
    assert os.path.isfile(_STALE_ROWS_CSV), "stale rows by ticker CSV must always exist"
    rows = _read_csv_rows(_STALE_ROWS_CSV)
    assert rows, "stale rows by ticker must have rows"
    for col in ("ticker", "sector", "total_rows", "rows_over_365", "rows_over_400",
                "rows_over_540", "rows_over_730", "max_feature_age_days",
                "median_feature_age_days", "distinct_feature_asof_dates", "staleness_problem"):
        assert col in rows[0], "stale rows CSV missing column: %s" % col


def test_repair_decision_table_exists():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed repair result JSON not present")
    assert os.path.isfile(_DECISION_TABLE_CSV), "repair decision table must always exist"
    rows = _read_csv_rows(_DECISION_TABLE_CSV)
    assert rows, "repair decision table must have rows"
    for col in ("decision_item", "value", "passed", "note"):
        assert col in rows[0], "decision table CSV missing column: %s" % col


def test_repaired_quality_report_exists():
    if not os.path.isfile(_REPAIRED_QUALITY_JSON):
        raise _Skip("repaired quality report not present")
    aq = json.loads(_read(_REPAIRED_QUALITY_JSON))
    for k in ("source_phase", "phase3i_recommendation", "selected_cap_days",
              "original_aligned_rows", "repaired_aligned_rows", "retained_row_fraction",
              "repaired_aligned_tickers", "repaired_aligned_ticker_count",
              "repaired_aligned_sectors", "repaired_aligned_sector_count", "repaired_start_date",
              "repaired_end_date", "feature_age_days_summary_after_repair",
              "label_coverage_by_horizon_after_repair", "leakage_failure_count_after_repair",
              "rows_by_ticker_after_repair", "tickers_dropped_by_selected_cap",
              "sectors_dropped_by_selected_cap", "staleness_cap_sensitivity_summary",
              "recommendation"):
        assert k in aq, "repaired quality report missing key: %s" % k
    assert aq["leakage_failure_count_after_repair"] == 0


def test_repaired_panel_and_label_summary_exist_on_success():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed repair result JSON not present")
    if not _result_is_success_or_partial():
        raise _Skip("not success/partial")
    assert os.path.isfile(_REPAIRED_PANEL_CSV), \
        "repaired panel CSV must exist on success/partial"
    assert os.path.isfile(_REPAIRED_LABEL_SUMMARY_CSV), \
        "repaired label summary CSV must exist on success/partial"
    rows = _read_csv_rows(_REPAIRED_PANEL_CSV)
    assert rows, "repaired panel must have rows on success/partial"
    for col in ("ticker", "company_name", "sector", "industry", "scoring_date", "adjusted_close",
                "active_feature_asof_date", "feature_age_days", "active_fiscal_period_end",
                "forward_return_21d", "forward_return_63d", "forward_return_126d"):
        assert col in rows[0], "repaired panel CSV missing column: %s" % col


def test_repaired_rows_respect_cap_and_invariants():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed repair result JSON not present")
    if not _result_is_success_or_partial():
        raise _Skip("not success/partial")
    d = _result()
    cap = d["recommendation"]["selected_cap_days"]
    assert cap is not None, "a cap must be selected on success/partial"
    rows = _read_csv_rows(_REPAIRED_PANEL_CSV)
    for r in rows:
        age = float(r["feature_age_days"])
        assert age <= cap, "feature_age_days (%s) exceeds selected cap (%s)" % (age, cap)
        asof = r["active_feature_asof_date"]
        assert asof, "active_feature_asof_date missing for a repaired row"
        asof_d = asof.split("T")[0]
        scoring = r["scoring_date"]
        fpe = r["active_fiscal_period_end"]
        assert asof_d < scoring, \
            "active_feature_asof_date (%s) not before scoring_date (%s) - leakage" % (
                asof_d, scoring)
        assert asof_d >= fpe, \
            "active_feature_asof_date (%s) precedes fiscal_period_end (%s)" % (asof_d, fpe)
        assert asof_d != fpe, "active_feature_asof_date must not equal the fiscal_period_end"


# --------------------------------------------------------------------------- #
# 7. Recommendation + next phase
# --------------------------------------------------------------------------- #
def test_recommendation_and_next_phase():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed repair result JSON not present")
    d = _result()
    rec = d["recommendation"]
    assert rec["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert rec["create_production_model_candidate_now"] is False
    assert rec["train_production_model_now"] is False
    assert rec["deploy_now"] is False
    assert rec["production_edge_claimed"] is False
    assert rec["full_128_ticker_ingestion_now"] is False
    assert rec["model_training_now"] is False
    assert rec["labels_for_validation_only"] is True
    assert "selected_cap_days" in rec
    assert "proceed_to_ic_diagnostic" in rec
    assert isinstance(rec.get("reason"), str) and rec["reason"]
    assert d["recommended_next_phase"]["phase"] == "3-K"
    assert d["recommended_next_phase"]["title"]
    assert d["recommended_next_phase"]["purpose"]
    # model_training_ready is true only on full repair success.
    if rec["recommendation"] == "FUNDAMENTAL_ALIGNMENT_REPAIR_SUCCESS":
        assert d["model_training_ready"] is True
        assert rec["proceed_to_ic_diagnostic"] is True
    else:
        assert d["model_training_ready"] is False


# --------------------------------------------------------------------------- #
# 8. Doc has all required guardrail phrases
# --------------------------------------------------------------------------- #
def test_doc_has_required_phrases():
    text = _read(_DOC)
    missing = [p for p in _REQUIRED_DOC_PHRASES if p not in text]
    assert not missing, "doc missing required phrase(s): %s" % missing


# --------------------------------------------------------------------------- #
# 9. Live end-to-end rebuild (gated): rebuilds the repair into a temp dir
# --------------------------------------------------------------------------- #
def test_live_end_to_end_optional():
    if os.environ.get("PHASE3J_LIVE") != "1":
        raise _Skip("set PHASE3J_LIVE=1 to rebuild the repair end to end")
    if not os.path.isfile(_PHASE3I_JSON):
        raise _Skip("Phase 3-I results JSON not present in this environment")
    import tempfile
    builder = _import_builder()
    if not os.path.isfile(builder.PHASE3I_PANEL_CSV):
        raise _Skip("Phase 3-I aligned panel not present in this environment")
    tmp = tempfile.mkdtemp(prefix="phase3j_")
    res = builder.run(
        result_json_path=os.path.join(tmp, "result.json"),
        repaired_panel_path=os.path.join(tmp, "panel.csv"),
        staleness_sensitivity_path=os.path.join(tmp, "sensitivity.csv"),
        repaired_quality_path=os.path.join(tmp, "quality.json"),
        repaired_label_summary_path=os.path.join(tmp, "labels.csv"),
        repair_decision_table_path=os.path.join(tmp, "decision.csv"),
        stale_rows_by_ticker_path=os.path.join(tmp, "stale.csv"))
    assert res["phase"] == "3-J"
    assert res["network_used"] is False
    assert res["d_drive_read"] is False
    assert res["d_drive_written"] is False
    assert res["labels_computed"] is True
    assert res["labels_for_validation_only"] is True
    assert res["price_join_performed"] is True
    assert res["model_features_computed"] is True
    assert res["research_model_trained"] is False
    assert res["production_model_candidate_created"] is False
    assert res["deployable_model_artifact_written"] is False
    assert res["model_trained"] is False
    assert res["full_128_ticker_ingestion"] is False
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "3-K"
    assert res["leakage_check_summary"]["leakage_failure_count_after_repair"] == 0
    assert len(res["repaired_panel_summary"]["repaired_aligned_tickers"]) <= 20
    assert len(res["staleness_cap_sensitivity"]) == len(builder.CANDIDATE_CAPS)


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
