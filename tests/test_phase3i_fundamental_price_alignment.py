"""Phase 3-I tests for the Fundamental Feature Price-Alignment Dry Run.

These tests prove the aligner is a disciplined, controlled, read-only research dry run: it compiles
and imports; it references the committed Phase 3-H feature inputs and the Phase 2K-G D: price path,
and it touches the D: drive READ ONLY (no D: write); it reads the Phase 3-H result and confirms it
succeeded (recommendation == SEC_FUNDAMENTAL_FEATURES_PROTOTYPE_SUCCESS) and routed here (next
phase 3-I); the output JSON carries every required field and safety flag; phase == "3-I"; the
aligned panel / alignment-quality / label-summary / leakage-checks / date-alignment artifacts exist
on success or partial success; every aligned row's active_feature_asof_date is strictly before its
scoring_date and on/after (and never equal to) its active_fiscal_period_end; labels are computed
for validation only; it fits no model, computes no predictions/scores, creates no production model
candidate, and writes no deployable model artifact; the recommendation uses only the allowed values
and routes to Phase 3-J; and the source imports no api_server / Paper Trader / ML framework /
network library (no urllib / requests), contains no infrastructure / database-write / deployment /
order-trading / model-artifact / vendor tokens, and makes no network call. The doc carries the
required guardrail phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase3i_fundamental_price_alignment.py
  * without pytest: python tests/test_phase3i_fundamental_price_alignment.py
The __main__ block discovers every test_* function, runs it, prints PASS/FAIL/SKIP, and exits
non-zero on any failure (the GCP venv has no pytest). A live end-to-end rebuild into a temp dir is
gated behind PHASE3I_LIVE=1.
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

_BUILDER = os.path.join(_REPO_ROOT, "research", "align_phase3i_fundamental_price_dry_run.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase3i_fundamental_price_alignment_v1.md")
_RESULT = os.path.join(_REPO_ROOT, "research", "output", "phase3i_fundamental_price_alignment.json")
_I_DIR = os.path.join(_REPO_ROOT, "research", "output", "phase3i_fundamental_price_alignment")
_PANEL_CSV = os.path.join(_I_DIR, "aligned_feature_price_panel_20ticker_sample.csv")
_ALIGN_QUALITY_JSON = os.path.join(_I_DIR, "alignment_quality_report.json")
_LABEL_SUMMARY_CSV = os.path.join(_I_DIR, "label_summary_by_horizon.csv")
_LEAKAGE_CSV = os.path.join(_I_DIR, "leakage_checks.csv")
_DATE_ALIGN_CSV = os.path.join(_I_DIR, "date_alignment_summary.csv")
_PHASE3H_JSON = os.path.join(
    _REPO_ROOT, "research", "output", "phase3h_sec_fundamental_features.json")


class _Skip(Exception):
    """Raised to mark a test skipped."""


# Committed inputs this phase must reference (basenames), incl. the D: price path basename.
_EXPECTED_INPUT_BASENAMES = (
    "phase3h_sec_fundamental_features.json",
    "feature_snapshot_20ticker_sample.csv",
    "phase2k_g_expanded_price_history_free.csv",
    "phase2k_p_sector_map_current.csv",
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
# Phase 3-I uses NO network: forbid vendor packages AND the network libraries themselves.
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
    "phase", "generated_at", "inputs_read", "outputs_written", "phase3h_summary",
    "alignment_config", "price_data_summary", "aligned_panel_summary", "label_summary",
    "leakage_check_summary", "alignment_quality_summary", "source_limitations",
    "earnings_revisions_gap", "recommendation", "interpretation", "recommended_next_phase",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed", "deployment_executed",
    "model_v2_enabled", "production_edge_claimed", "research_model_trained",
    "production_model_trained", "production_model_candidate_created",
    "deployable_model_artifact_written", "network_used", "vendor_api_called",
    "paid_vendor_api_called", "data_purchase_made", "external_data_ingested",
    "production_data_ingested", "full_128_ticker_ingestion", "d_drive_written",
    "model_training_ready",
)
_REQUIRED_TRUE = (
    "no_trading", "no_orders", "no_automation", "sec_public_data_used", "d_drive_read",
    "model_features_computed", "price_join_performed", "labels_computed",
    "labels_for_validation_only",
)
_ALLOWED_RECOMMENDATIONS = {
    "FUNDAMENTAL_PRICE_ALIGNMENT_DRY_RUN_SUCCESS",
    "FUNDAMENTAL_PRICE_ALIGNMENT_PARTIAL_SUCCESS",
    "FUNDAMENTAL_PRICE_ALIGNMENT_BLOCKED",
    "FUNDAMENTAL_PRICE_ALIGNMENT_REJECTED",
}
_SUCCESS_OR_PARTIAL = {
    "FUNDAMENTAL_PRICE_ALIGNMENT_DRY_RUN_SUCCESS",
    "FUNDAMENTAL_PRICE_ALIGNMENT_PARTIAL_SUCCESS",
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
    spec = importlib.util.spec_from_file_location("phase3i_align_test", _BUILDER)
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
# 2. Expected inputs referenced; outputs under research/output (C:); D: read-only
# --------------------------------------------------------------------------- #
def test_references_expected_inputs():
    builder = _import_builder()
    text = _read(_BUILDER)
    for base in _EXPECTED_INPUT_BASENAMES:
        assert base in text, "aligner does not reference expected input: %s" % base
    for path in (builder.RESULT_JSON, builder.ALIGNED_PANEL_CSV, builder.ALIGNMENT_QUALITY_JSON,
                 builder.LABEL_SUMMARY_CSV, builder.LEAKAGE_CHECKS_CSV,
                 builder.DATE_ALIGNMENT_SUMMARY_CSV):
        n = path.replace("\\", "/")
        assert "research/output/" in n, n
        assert not n.upper().startswith("D:"), "output must not be on the D: drive: %s" % n


def test_references_d_drive_price_path():
    builder = _import_builder()
    assert builder.PRICE_CSV.replace("\\", "/").upper().startswith("D:"), \
        "Phase 3-I must read the price panel from the D: drive"


def test_d_drive_is_read_only():
    """The D: drive may be READ in Phase 3-I but never written: no write-mode open on a D: path."""
    text = _read(_BUILDER)
    for line in text.splitlines():
        if "D:" in line and "open(" in line:
            assert '"w"' not in line and "'w'" not in line and '"a"' not in line \
                and "'a'" not in line, "D: drive must be opened read-only: %s" % line.strip()
    # All output writers target research/output, never D:.
    builder = _import_builder()
    for path in (builder.RESULT_JSON, builder.ALIGNED_PANEL_CSV, builder.ALIGNMENT_QUALITY_JSON,
                 builder.LABEL_SUMMARY_CSV, builder.LEAKAGE_CHECKS_CSV,
                 builder.DATE_ALIGNMENT_SUMMARY_CSV):
        assert not path.replace("\\", "/").upper().startswith("D:")


# --------------------------------------------------------------------------- #
# 3. No api_server / Paper Trader / ML-framework / network imports
# --------------------------------------------------------------------------- #
def test_no_forbidden_imports():
    text = _read(_BUILDER)
    assert "paper_trader" not in text.lower(), "aligner must not reference paper_trader"
    assert "import api_server" not in text and "from api_server" not in text
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = ([n.name for n in node.names]
                    + ([node.module] if isinstance(node, ast.ImportFrom)
                       and node.module else []))
            roots = {(m or "").split(".")[0] for m in mods}
            bad = roots & _FORBIDDEN_IMPORT_ROOTS
            assert not bad, "aligner imports forbidden module(s): %s" % bad


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
        assert not hits, "aligner contains forbidden %s token(s): %s" % (label, hits)


def test_no_network_at_all():
    text = _read(_BUILDER)
    urls = re.findall(r"https?://[^\s\"')]+", text)
    assert not urls, "aligner must contain no network URLs in Phase 3-I: %s" % urls


# --------------------------------------------------------------------------- #
# 5. Committed result artifact: required fields, safety flags, structure
# --------------------------------------------------------------------------- #
def test_committed_result_artifact_valid():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed alignment result JSON not present")
    d = json.loads(_read(_RESULT))
    for k in _REQUIRED_JSON_FIELDS:
        assert k in d, "committed alignment JSON missing field: %s" % k
    assert d["phase"] == "3-I"
    for k in _REQUIRED_FALSE:
        assert d[k] is False, "%s must be false" % k
    for k in _REQUIRED_TRUE:
        assert d[k] is True, "%s must be true" % k


def test_reads_phase3h_and_confirms_routing():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed alignment result JSON not present")
    d = json.loads(_read(_RESULT))
    confirmed = d["phase3h_summary"]["phase3h_confirmed"]
    assert confirmed["phase_is_3h"] is True
    assert confirmed["recommendation_is_features_success"] is True
    assert confirmed["next_phase_is_3i"] is True
    assert confirmed["all_confirmed"] is True
    assert d["phase3h_summary"]["phase3h_recommendation"] == \
        "SEC_FUNDAMENTAL_FEATURES_PROTOTYPE_SUCCESS"


def test_d_drive_read_only_flags():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed alignment result JSON not present")
    d = json.loads(_read(_RESULT))
    assert d["d_drive_read"] is True
    assert d["d_drive_written"] is False
    assert d["network_used"] is False
    assert d["interpretation"]["read_from_d_drive"] is True
    assert d["interpretation"]["wrote_to_d_drive"] is False
    assert d["interpretation"]["network_used"] is False


def test_no_full_128_ticker_ingestion():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed alignment result JSON not present")
    d = json.loads(_read(_RESULT))
    assert d["full_128_ticker_ingestion"] is False
    assert d["interpretation"]["full_128_ticker_ingestion"] is False
    assert len(d["aligned_panel_summary"]["aligned_tickers"]) <= 20


def test_labels_for_validation_only_no_model():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed alignment result JSON not present")
    d = json.loads(_read(_RESULT))
    interp = d["interpretation"]
    assert d["labels_computed"] is True
    assert d["labels_for_validation_only"] is True
    assert d["price_join_performed"] is True
    assert d["model_features_computed"] is True
    assert interp["labels_for_validation_only"] is True
    assert interp["model_trained"] is False
    assert interp["predictions_computed"] is False
    assert interp["scores_computed"] is False
    assert interp["portfolio_weights_computed"] is False
    assert interp["research_model_trained"] is False
    assert interp["production_model_trained"] is False
    assert interp["production_model_candidate_created"] is False
    assert interp["deployable_model_artifact_written"] is False
    assert interp["model_training_ready"] is False
    rec = d["recommendation"]
    assert rec["train_production_model_now"] is False
    assert rec["create_production_model_candidate_now"] is False
    assert rec["model_training_now"] is False
    assert rec["labels_for_validation_only"] is True


def test_earnings_revisions_gap_documented():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed alignment result JSON not present")
    d = json.loads(_read(_RESULT))
    gap = d["earnings_revisions_gap"]
    assert gap["sec_provides_earnings_consensus"] is False
    assert gap["sec_provides_analyst_estimate_revisions"] is False
    assert gap["provider_selection_required"] is True


# --------------------------------------------------------------------------- #
# 6. Alignment artifacts exist and are well-formed on success/partial
# --------------------------------------------------------------------------- #
def _result_is_success_or_partial():
    d = json.loads(_read(_RESULT))
    return d["recommendation"]["recommendation"] in _SUCCESS_OR_PARTIAL


def test_aligned_panel_csv_exists_and_leakage_safe():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed alignment result JSON not present")
    if not _result_is_success_or_partial():
        raise _Skip("not success/partial")
    assert os.path.isfile(_PANEL_CSV), "aligned panel CSV must exist on success/partial"
    rows = _read_csv_rows(_PANEL_CSV)
    assert rows, "aligned panel must have rows on success/partial"
    for col in ("ticker", "company_name", "sector", "industry", "scoring_date", "adjusted_close",
                "active_feature_asof_date", "feature_age_days", "active_fiscal_period_end",
                "active_fiscal_year", "active_fiscal_period", "active_form",
                "active_snapshot_type", "forward_return_21d", "forward_return_63d",
                "forward_return_126d", "forward_excess_return_vs_spy_21d",
                "binary_outperform_spy_21d", "forward_return_rank_by_date_21d"):
        assert col in rows[0], "aligned panel CSV missing column: %s" % col
    # Every aligned row: as-of strictly before scoring date, on/after (never equal) period end.
    for r in rows:
        asof = r["active_feature_asof_date"]
        assert asof, "active_feature_asof_date missing for an aligned row"
        asof_d = asof.split("T")[0]
        scoring = r["scoring_date"]
        fpe = r["active_fiscal_period_end"]
        assert asof_d < scoring, \
            "active_feature_asof_date (%s) not before scoring_date (%s) - leakage" % (
                asof_d, scoring)
        assert asof_d >= fpe, \
            "active_feature_asof_date (%s) precedes fiscal_period_end (%s)" % (asof_d, fpe)
        assert asof_d != fpe, "active_feature_asof_date must not equal the fiscal_period_end"


def test_leakage_checks_csv_exists_and_all_pass():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed alignment result JSON not present")
    if not _result_is_success_or_partial():
        raise _Skip("not success/partial")
    assert os.path.isfile(_LEAKAGE_CSV), "leakage checks CSV must exist on success/partial"
    with open(_LEAKAGE_CSV, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for col in ("ticker", "scoring_date", "active_feature_asof_date",
                    "active_fiscal_period_end", "check_name", "passed", "severity", "note"):
            assert col in (reader.fieldnames or []), "leakage CSV missing column: %s" % col
        failures = [row for row in reader if str(row.get("passed")).strip().lower() != "true"]
    assert not failures, "leakage checks must all pass; %d failing rows" % len(failures)
    d = json.loads(_read(_RESULT))
    assert d["leakage_check_summary"]["leakage_failure_count"] == 0


def test_alignment_quality_report_exists():
    if not os.path.isfile(_ALIGN_QUALITY_JSON):
        raise _Skip("alignment quality report not present")
    aq = json.loads(_read(_ALIGN_QUALITY_JSON))
    for k in ("source_phase", "input_feature_rows", "input_feature_tickers", "price_rows_read",
              "price_tickers_read", "aligned_rows", "aligned_tickers", "aligned_start_date",
              "aligned_end_date", "rows_by_ticker", "rows_by_sector", "feature_age_days_summary",
              "annual_vs_quarterly_active_snapshot_mix", "label_coverage_by_horizon",
              "leakage_check_count", "leakage_failure_count", "leakage_warning_count",
              "null_feature_summary", "null_label_summary", "spy_available", "recommendation"):
        assert k in aq, "alignment quality report missing key: %s" % k
    assert aq["leakage_failure_count"] == 0


def test_label_summary_csv_exists():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed alignment result JSON not present")
    if not _result_is_success_or_partial():
        raise _Skip("not success/partial")
    assert os.path.isfile(_LABEL_SUMMARY_CSV), "label summary CSV must exist on success/partial"
    rows = _read_csv_rows(_LABEL_SUMMARY_CSV)
    assert rows, "label summary must have rows"
    for col in ("horizon", "total_rows", "non_null_forward_return",
                "non_null_forward_excess_return_vs_spy", "non_null_binary_outperform_spy",
                "non_null_rank", "mean_forward_return", "mean_forward_excess_return_vs_spy",
                "positive_return_fraction", "outperform_spy_fraction", "earliest_scoring_date",
                "latest_scoring_date"):
        assert col in rows[0], "label summary CSV missing column: %s" % col


def test_date_alignment_summary_csv_exists():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed alignment result JSON not present")
    if not _result_is_success_or_partial():
        raise _Skip("not success/partial")
    assert os.path.isfile(_DATE_ALIGN_CSV), "date alignment summary CSV must exist"
    rows = _read_csv_rows(_DATE_ALIGN_CSV)
    assert rows, "date alignment summary must have rows"
    for col in ("ticker", "first_price_date", "last_price_date", "first_feature_asof_date",
                "first_eligible_scoring_date", "first_aligned_scoring_date",
                "last_aligned_scoring_date", "aligned_rows", "median_feature_age_days",
                "max_feature_age_days"):
        assert col in rows[0], "date alignment summary CSV missing column: %s" % col


# --------------------------------------------------------------------------- #
# 7. Recommendation + next phase
# --------------------------------------------------------------------------- #
def test_recommendation_and_next_phase():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed alignment result JSON not present")
    d = json.loads(_read(_RESULT))
    rec = d["recommendation"]
    assert rec["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert rec["create_production_model_candidate_now"] is False
    assert rec["train_production_model_now"] is False
    assert rec["deploy_now"] is False
    assert rec["production_edge_claimed"] is False
    assert rec["full_128_ticker_ingestion_now"] is False
    assert rec["model_training_now"] is False
    assert rec["labels_for_validation_only"] is True
    assert isinstance(rec.get("reason"), str) and rec["reason"]
    assert d["recommended_next_phase"]["phase"] == "3-J"
    assert d["recommended_next_phase"]["title"]
    assert d["recommended_next_phase"]["purpose"]


# --------------------------------------------------------------------------- #
# 8. Doc has all required guardrail phrases
# --------------------------------------------------------------------------- #
def test_doc_has_required_phrases():
    text = _read(_DOC)
    missing = [p for p in _REQUIRED_DOC_PHRASES if p not in text]
    assert not missing, "doc missing required phrase(s): %s" % missing


# --------------------------------------------------------------------------- #
# 9. Live end-to-end rebuild (gated): rebuilds the alignment into a temp dir
# --------------------------------------------------------------------------- #
def test_live_end_to_end_optional():
    if os.environ.get("PHASE3I_LIVE") != "1":
        raise _Skip("set PHASE3I_LIVE=1 to rebuild the alignment end to end")
    if not os.path.isfile(_PHASE3H_JSON):
        raise _Skip("Phase 3-H results JSON not present in this environment")
    import tempfile
    builder = _import_builder()
    if not os.path.isfile(builder.PRICE_CSV):
        raise _Skip("D: price panel not present in this environment")
    tmp = tempfile.mkdtemp(prefix="phase3i_")
    res = builder.run(
        result_json_path=os.path.join(tmp, "result.json"),
        aligned_panel_path=os.path.join(tmp, "panel.csv"),
        alignment_quality_path=os.path.join(tmp, "quality.json"),
        label_summary_path=os.path.join(tmp, "labels.csv"),
        leakage_checks_path=os.path.join(tmp, "leakage.csv"),
        date_alignment_summary_path=os.path.join(tmp, "dates.csv"))
    assert res["phase"] == "3-I"
    assert res["network_used"] is False
    assert res["d_drive_read"] is True
    assert res["d_drive_written"] is False
    assert res["labels_computed"] is True
    assert res["labels_for_validation_only"] is True
    assert res["price_join_performed"] is True
    assert res["model_features_computed"] is True
    assert res["model_training_ready"] is False
    assert res["research_model_trained"] is False
    assert res["production_model_candidate_created"] is False
    assert res["deployable_model_artifact_written"] is False
    assert res["full_128_ticker_ingestion"] is False
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "3-J"
    assert res["leakage_check_summary"]["leakage_failure_count"] == 0
    assert len(res["aligned_panel_summary"]["aligned_tickers"]) <= 20


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
