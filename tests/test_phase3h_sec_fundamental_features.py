"""Phase 3-H tests for the SEC Fundamentals Feature Engineering Prototype.

These tests prove the feature builder is a disciplined, controlled, read-only research feature
prototype: it compiles and imports; it references the expected committed Phase 3-G / Phase 3-E
inputs and reads only repo-local outputs (never the D: drive); it reads the Phase 3-G result and
confirms it succeeded (recommendation == SEC_FUNDAMENTALS_MINIPIPELINE_SUCCESS) and routed here
(next phase 3-H); the output JSON carries every required field and safety flag; phase == "3-H";
the feature snapshot / dictionary / coverage / quality / alignment-warning artifacts exist on
success or partial success; every feature row carries a feature_asof_date and none is before its
fiscal_period_end; annual and quarterly snapshots are separated; it computes no labels, trains no
model, creates no production model candidate, and writes no deployable model artifact; the
recommendation uses only the allowed values and routes to Phase 3-I; and the source imports no
api_server / Paper Trader / ML framework / network library (no urllib / requests), contains no
infrastructure / database-write / deployment / order-trading / model-artifact / vendor tokens, and
makes no network call. The doc carries the required guardrail phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase3h_sec_fundamental_features.py
  * without pytest: python tests/test_phase3h_sec_fundamental_features.py
The __main__ block discovers every test_* function, runs it, prints PASS/FAIL/SKIP, and exits
non-zero on any failure (the GCP venv has no pytest). A live end-to-end rebuild into a temp dir is
gated behind PHASE3H_LIVE=1.
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

_BUILDER = os.path.join(_REPO_ROOT, "research", "build_phase3h_sec_fundamental_features.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase3h_sec_fundamental_features_v1.md")
_RESULT = os.path.join(_REPO_ROOT, "research", "output", "phase3h_sec_fundamental_features.json")
_H_DIR = os.path.join(_REPO_ROOT, "research", "output", "phase3h_sec_fundamental_features")
_SNAPSHOT_CSV = os.path.join(_H_DIR, "feature_snapshot_20ticker_sample.csv")
_DICTIONARY_CSV = os.path.join(_H_DIR, "feature_dictionary.csv")
_COVERAGE_CSV = os.path.join(_H_DIR, "feature_coverage_by_ticker.csv")
_QUALITY_JSON = os.path.join(_H_DIR, "feature_quality_report.json")
_WARNINGS_CSV = os.path.join(_H_DIR, "feature_alignment_warnings.csv")
_PHASE3G_JSON = os.path.join(
    _REPO_ROOT, "research", "output", "phase3g_sec_fundamentals_minipipeline.json")


class _Skip(Exception):
    """Raised to mark a test skipped."""


# The committed inputs this phase must reference (basenames).
_EXPECTED_INPUT_BASENAMES = (
    "phase3g_sec_fundamentals_minipipeline.json",
    "company_identity_20ticker_sample.csv",
    "fundamentals_20ticker_sample.csv",
    "phase3e_ingestion_contract.json",
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
    "system" + "d",
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
# Phase 3-H uses NO network: forbid vendor packages AND the network libraries themselves.
_FORBIDDEN_NETWORK_TOKENS = [
    "yf" + "inance",
    "url" + "lib",
    "import " + "requests",
    "http" + "://",
    "https" + "://",
]
# Libraries / modules that must not be imported (no network in this phase).
_FORBIDDEN_IMPORT_ROOTS = {
    "sklearn", "tensorflow", "torch", "keras", "lightgbm", "xgboost", "statsmodels",
    "api_server", "yfinance", "requests", "urllib", "http", "socket",
}

_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read", "outputs_written", "phase3g_summary",
    "feature_engineering_config", "snapshot_summary", "feature_dictionary_summary",
    "feature_coverage_summary", "feature_quality_summary", "warning_summary",
    "source_limitations", "earnings_revisions_gap", "recommendation", "interpretation",
    "recommended_next_phase",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_edge_claimed",
    "research_model_trained", "production_model_trained", "production_model_candidate_created",
    "deployable_model_artifact_written", "d_drive_read", "d_drive_written", "network_used",
    "vendor_api_called", "paid_vendor_api_called", "data_purchase_made",
    "external_data_ingested", "production_data_ingested", "full_128_ticker_ingestion",
    "labels_computed", "model_training_ready",
)
_REQUIRED_TRUE = (
    "no_trading", "no_orders", "no_automation", "sec_public_data_used", "model_features_computed",
)
_ALLOWED_RECOMMENDATIONS = {
    "SEC_FUNDAMENTAL_FEATURES_PROTOTYPE_SUCCESS",
    "SEC_FUNDAMENTAL_FEATURES_PARTIAL_SUCCESS",
    "SEC_FUNDAMENTAL_FEATURES_BLOCKED",
    "SEC_FUNDAMENTAL_FEATURES_REJECTED",
}
_SUCCESS_OR_PARTIAL = {
    "SEC_FUNDAMENTAL_FEATURES_PROTOTYPE_SUCCESS",
    "SEC_FUNDAMENTAL_FEATURES_PARTIAL_SUCCESS",
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
    spec = importlib.util.spec_from_file_location("phase3h_features_test", _BUILDER)
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
# 2. Expected inputs referenced; outputs under research/output (C:); no D: read/write
# --------------------------------------------------------------------------- #
def test_references_expected_inputs():
    builder = _import_builder()
    text = _read(_BUILDER)
    for base in _EXPECTED_INPUT_BASENAMES:
        assert base in text, "feature builder does not reference expected input: %s" % base
    for path in (builder.FEATURES_JSON, builder.FEATURE_SNAPSHOT_CSV,
                 builder.FEATURE_DICTIONARY_CSV, builder.FEATURE_COVERAGE_CSV,
                 builder.FEATURE_QUALITY_JSON, builder.FEATURE_ALIGNMENT_WARNINGS_CSV):
        n = path.replace("\\", "/")
        assert "research/output/" in n, n
        assert not n.upper().startswith("D:"), "output must not be on the D: drive: %s" % n


def test_no_d_drive_read_or_write_in_source():
    low = _read(_BUILDER).lower()
    assert "d:/" not in low and "d:\\" not in low, \
        "feature builder must not read or write the D: drive in Phase 3-H"


# --------------------------------------------------------------------------- #
# 3. No api_server / Paper Trader / ML-framework / network imports
# --------------------------------------------------------------------------- #
def test_no_forbidden_imports():
    text = _read(_BUILDER)
    assert "paper_trader" not in text.lower(), "feature builder must not reference paper_trader"
    assert "import api_server" not in text and "from api_server" not in text
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = ([n.name for n in node.names]
                    + ([node.module] if isinstance(node, ast.ImportFrom)
                       and node.module else []))
            roots = {(m or "").split(".")[0] for m in mods}
            bad = roots & _FORBIDDEN_IMPORT_ROOTS
            assert not bad, "feature builder imports forbidden module(s): %s" % bad


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
        assert not hits, "feature builder contains forbidden %s token(s): %s" % (label, hits)


def test_no_network_at_all():
    text = _read(_BUILDER)
    urls = re.findall(r"https?://[^\s\"')]+", text)
    assert not urls, "feature builder must contain no network URLs in Phase 3-H: %s" % urls


# --------------------------------------------------------------------------- #
# 5. Committed result artifact: required fields, safety flags, structure
# --------------------------------------------------------------------------- #
def test_committed_result_artifact_valid():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed feature result JSON not present")
    d = json.loads(_read(_RESULT))
    for k in _REQUIRED_JSON_FIELDS:
        assert k in d, "committed feature JSON missing field: %s" % k
    assert d["phase"] == "3-H"
    for k in _REQUIRED_FALSE:
        assert d[k] is False, "%s must be false" % k
    for k in _REQUIRED_TRUE:
        assert d[k] is True, "%s must be true" % k


def test_reads_phase3g_and_confirms_routing():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed feature result JSON not present")
    d = json.loads(_read(_RESULT))
    confirmed = d["phase3g_summary"]["phase3g_confirmed"]
    assert confirmed["phase_is_3g"] is True
    assert confirmed["recommendation_is_minipipeline_success"] is True
    assert confirmed["next_phase_is_3h"] is True
    assert confirmed["all_confirmed"] is True
    assert d["phase3g_summary"]["phase3g_recommendation"] == \
        "SEC_FUNDAMENTALS_MINIPIPELINE_SUCCESS"


def test_no_full_128_ticker_ingestion():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed feature result JSON not present")
    d = json.loads(_read(_RESULT))
    assert d["full_128_ticker_ingestion"] is False
    assert d["interpretation"]["full_128_ticker_ingestion"] is False
    assert len(d["snapshot_summary"]["tickers_with_features"]) <= 20


def test_no_labels_or_model_training():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed feature result JSON not present")
    d = json.loads(_read(_RESULT))
    interp = d["interpretation"]
    assert interp["model_trained"] is False
    assert interp["labels_computed"] is False
    assert interp["forward_returns_computed"] is False
    assert interp["price_join_performed"] is False
    assert interp["research_model_trained"] is False
    assert interp["production_model_trained"] is False
    assert interp["production_model_candidate_created"] is False
    assert interp["deployable_model_artifact_written"] is False
    assert interp["model_training_ready"] is False
    assert d["labels_computed"] is False
    assert d["model_features_computed"] is True
    assert d["recommendation"]["label_generation_now"] is False
    assert d["recommendation"]["price_join_now"] is False


def test_no_network_used_flag():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed feature result JSON not present")
    d = json.loads(_read(_RESULT))
    assert d["network_used"] is False
    assert d["interpretation"]["network_used"] is False
    assert d["d_drive_read"] is False
    assert d["d_drive_written"] is False


def test_earnings_revisions_gap_documented():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed feature result JSON not present")
    d = json.loads(_read(_RESULT))
    gap = d["earnings_revisions_gap"]
    assert gap["sec_provides_earnings_consensus"] is False
    assert gap["sec_provides_analyst_estimate_revisions"] is False
    assert gap["provider_selection_required"] is True


# --------------------------------------------------------------------------- #
# 6. Feature artifacts exist and are well-formed on success/partial
# --------------------------------------------------------------------------- #
def _result_is_success_or_partial():
    d = json.loads(_read(_RESULT))
    return d["recommendation"]["recommendation"] in _SUCCESS_OR_PARTIAL


def test_feature_snapshot_csv_exists_and_pit_safe():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed feature result JSON not present")
    if not _result_is_success_or_partial():
        raise _Skip("not success/partial")
    assert os.path.isfile(_SNAPSHOT_CSV), "feature snapshot CSV must exist on success/partial"
    rows = _read_csv_rows(_SNAPSHOT_CSV)
    assert rows, "feature snapshot must have rows on success/partial"
    for col in ("ticker", "company_name", "sector", "industry", "cik", "fiscal_period_end",
                "fiscal_year", "fiscal_period", "form", "snapshot_type", "feature_asof_date",
                "source_field_count", "required_field_coverage_fraction", "point_in_time_usable",
                "is_annual_snapshot", "is_quarterly_snapshot"):
        assert col in rows[0], "feature snapshot CSV missing column: %s" % col
    # Every feature row carries a feature_asof_date, and none precedes its fiscal_period_end.
    for r in rows:
        assert r["feature_asof_date"], "feature_asof_date missing for a feature row"
        asof = r["feature_asof_date"].split("T")[0]
        assert asof >= r["fiscal_period_end"], \
            "feature_asof_date (%s) precedes fiscal_period_end (%s) - leakage" % (
                asof, r["fiscal_period_end"])
        # feature_asof_date must never equal the fiscal_period_end mechanically (it is a filing
        # timestamp); a filing is accepted strictly after the period closes.
        assert asof != r["fiscal_period_end"], \
            "feature_asof_date must not be the fiscal_period_end itself"


def test_annual_and_quarterly_separated():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed feature result JSON not present")
    if not _result_is_success_or_partial():
        raise _Skip("not success/partial")
    rows = _read_csv_rows(_SNAPSHOT_CSV)
    for r in rows:
        is_annual = r["is_annual_snapshot"] == "True"
        is_quarterly = r["is_quarterly_snapshot"] == "True"
        assert is_annual != is_quarterly, \
            "snapshot must be exactly one of annual/quarterly: %s" % r["snapshot_type"]
        if is_annual:
            assert r["snapshot_type"] == "annual"
        else:
            assert r["snapshot_type"] == "quarterly"


def test_feature_dictionary_csv_exists():
    if not os.path.isfile(_DICTIONARY_CSV):
        raise _Skip("feature dictionary not present")
    rows = _read_csv_rows(_DICTIONARY_CSV)
    assert rows, "feature dictionary must have rows"
    for col in ("feature_name", "feature_family", "formula", "required_source_fields",
                "point_in_time_rule", "annual_quarterly_rule", "missing_value_policy",
                "leakage_risk_note"):
        assert col in rows[0], "feature dictionary missing column: %s" % col


def test_feature_coverage_csv_exists():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed feature result JSON not present")
    if not _result_is_success_or_partial():
        raise _Skip("not success/partial")
    assert os.path.isfile(_COVERAGE_CSV), "feature coverage CSV must exist on success/partial"
    rows = _read_csv_rows(_COVERAGE_CSV)
    for col in ("ticker", "sector", "feature_name", "non_null_count", "total_snapshot_count",
                "coverage_fraction", "latest_feature_asof_date", "earliest_feature_asof_date"):
        assert col in rows[0], "feature coverage CSV missing column: %s" % col


def test_feature_alignment_warnings_csv_exists():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed feature result JSON not present")
    if not _result_is_success_or_partial():
        raise _Skip("not success/partial")
    assert os.path.isfile(_WARNINGS_CSV), "feature alignment warnings CSV must exist"
    # Header must be present (the file may legitimately have zero warning rows).
    with open(_WARNINGS_CSV, "r", encoding="utf-8", newline="") as f:
        header = f.readline().strip().split(",")
    for col in ("ticker", "warning_type", "fiscal_period_end", "fiscal_period", "form",
                "feature_name", "feature_value", "severity", "note"):
        assert col in header, "warnings CSV missing column: %s" % col


def test_feature_quality_report_exists():
    if not os.path.isfile(_QUALITY_JSON):
        raise _Skip("feature quality report not present")
    fq = json.loads(_read(_QUALITY_JSON))
    for k in ("source_phase", "input_rows", "input_tickers", "snapshot_rows",
              "annual_snapshot_rows", "quarterly_snapshot_rows", "feature_columns_count",
              "feature_families", "feature_asof_date_coverage", "point_in_time_usable_fraction",
              "feature_asof_before_period_end_count", "null_coverage_summary",
              "coverage_by_feature_family", "warning_count", "warning_type_counts",
              "leakage_warning_count", "extreme_ratio_warning_count",
              "annual_quarterly_mix_warning_count", "tickers_with_features", "sector_summary",
              "recommendation"):
        assert k in fq, "feature quality report missing key: %s" % k
    assert fq["feature_columns_count"] >= 20, "expected at least 20 engineered feature columns"
    assert fq["feature_asof_before_period_end_count"] == 0, "no row may be available pre-period-end"
    assert fq["leakage_warning_count"] == 0, "no leakage-risk warnings expected"


# --------------------------------------------------------------------------- #
# 7. Recommendation + next phase
# --------------------------------------------------------------------------- #
def test_recommendation_and_next_phase():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed feature result JSON not present")
    d = json.loads(_read(_RESULT))
    rec = d["recommendation"]
    assert rec["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert rec["create_production_model_candidate_now"] is False
    assert rec["train_production_model_now"] is False
    assert rec["deploy_now"] is False
    assert rec["production_edge_claimed"] is False
    assert rec["full_128_ticker_ingestion_now"] is False
    assert rec["model_training_now"] is False
    assert rec["label_generation_now"] is False
    assert rec["price_join_now"] is False
    assert isinstance(rec.get("reason"), str) and rec["reason"]
    assert d["recommended_next_phase"]["phase"] == "3-I"
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
# 9. Live end-to-end rebuild (gated): rebuilds features into a temp dir from Phase 3-G outputs
# --------------------------------------------------------------------------- #
def test_live_end_to_end_optional():
    if os.environ.get("PHASE3H_LIVE") != "1":
        raise _Skip("set PHASE3H_LIVE=1 to rebuild the features end to end")
    if not os.path.isfile(_PHASE3G_JSON):
        raise _Skip("Phase 3-G results JSON not present in this environment")
    import tempfile
    builder = _import_builder()
    tmp = tempfile.mkdtemp(prefix="phase3h_")
    res = builder.run(
        features_json_path=os.path.join(tmp, "features.json"),
        snapshot_path=os.path.join(tmp, "snapshot.csv"),
        dictionary_path=os.path.join(tmp, "dictionary.csv"),
        coverage_path=os.path.join(tmp, "coverage.csv"),
        quality_path=os.path.join(tmp, "quality.json"),
        warnings_path=os.path.join(tmp, "warnings.csv"))
    assert res["phase"] == "3-H"
    assert res["network_used"] is False
    assert res["labels_computed"] is False
    assert res["model_features_computed"] is True
    assert res["model_training_ready"] is False
    assert res["research_model_trained"] is False
    assert res["production_model_candidate_created"] is False
    assert res["deployable_model_artifact_written"] is False
    assert res["d_drive_read"] is False
    assert res["d_drive_written"] is False
    assert res["full_128_ticker_ingestion"] is False
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "3-I"
    assert res["feature_quality_summary"]["feature_asof_before_period_end_count"] == 0
    assert res["feature_quality_summary"]["leakage_warning_count"] == 0
    assert len(res["snapshot_summary"]["tickers_with_features"]) <= 20


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
