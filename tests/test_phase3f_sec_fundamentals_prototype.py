"""Phase 3-F tests for the SEC Fundamentals Source Selection and Free Prototype.

These tests prove the prototype is a disciplined, tiny, read-only free-source prototype: it
compiles and imports; it references the expected committed Phase 3-E inputs; it reads the Phase
3-E result and confirms it routed here (recommendation ==
PROCEED_TO_VENDOR_SELECTION_AND_FREE_SOURCE_PROTOTYPE, next phase 3-F); the sample is <= 5
tickers with no full 128-ticker ingestion; it selects an official SEC public source; the output
JSON carries every required field and safety flag; phase == "3-F"; the sample CSVs exist when the
prototype succeeded or partially succeeded; the data-quality report exists; it trains no model,
creates no production model candidate, writes no deployable model artifact, computes no model
features / labels, reads/writes nothing on the D: drive, and makes no paid-vendor / data-purchase
call; the recommendation uses only the allowed values and routes to Phase 3-G; and the source
imports no api_server / Paper Trader / ML framework, contains no infrastructure / database-write /
deployment / order-trading / model-artifact tokens, uses no yfinance, and restricts all network
usage to official SEC URL patterns. The doc carries the required guardrail phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase3f_sec_fundamentals_prototype.py
  * without pytest: python tests/test_phase3f_sec_fundamentals_prototype.py
The __main__ block discovers every test_* function, runs it, prints PASS/FAIL/SKIP, and exits
non-zero on any failure (the GCP venv has no pytest). The live end-to-end run that fetches/caches
the tiny SEC sample is gated behind PHASE3F_LIVE=1.
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

_PROTOTYPE = os.path.join(_REPO_ROOT, "research", "prototype_phase3f_sec_fundamentals.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase3f_sec_fundamentals_prototype_v1.md")
_RESULT = os.path.join(_REPO_ROOT, "research", "output", "phase3f_sec_fundamentals_prototype.json")
_SAMPLE_DIR = os.path.join(_REPO_ROOT, "research", "output", "phase3f_sec_fundamentals_sample")
_IDENTITY_CSV = os.path.join(_SAMPLE_DIR, "company_identity_sample.csv")
_FUNDAMENTALS_CSV = os.path.join(_SAMPLE_DIR, "fundamentals_sample.csv")
_DATA_QUALITY = os.path.join(_SAMPLE_DIR, "data_quality_report.json")
_PHASE3E_JSON = os.path.join(
    _REPO_ROOT, "research", "output", "phase3e_fundamentals_earnings_feasibility.json")


class _Skip(Exception):
    """Raised to mark a test skipped."""


# The committed Phase 3-E inputs this phase must reference (basenames).
_EXPECTED_INPUT_BASENAMES = (
    "phase3e_fundamentals_earnings_feasibility.json",
    "phase3e_external_data_schema_catalog.csv",
    "phase3e_ingestion_contract.json",
    "phase3e_provider_requirements_matrix.csv",
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
# Network is permitted ONLY for official SEC public endpoints; these vendors are still forbidden.
_FORBIDDEN_NETWORK_TOKENS = [
    "yf" + "inance",
]
# Libraries / modules that must not be imported (urllib IS allowed for SEC fetches this phase).
_FORBIDDEN_IMPORT_ROOTS = {
    "sklearn", "tensorflow", "torch", "keras", "lightgbm", "xgboost", "statsmodels",
    "api_server", "yfinance", "requests",
}

_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read", "outputs_written", "phase3e_summary",
    "source_selection", "sec_access_summary", "sample_universe", "company_identity_summary",
    "fundamentals_summary", "data_quality_summary", "source_limitations",
    "earnings_revisions_gap", "recommendation", "interpretation", "recommended_next_phase",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_edge_claimed",
    "research_model_trained", "production_model_trained", "production_model_candidate_created",
    "deployable_model_artifact_written", "d_drive_read", "d_drive_written",
    "vendor_api_called", "paid_vendor_api_called", "data_purchase_made",
    "production_data_ingested",
)
_REQUIRED_TRUE = (
    "no_trading", "no_orders", "no_automation", "sec_public_data_used", "external_data_ingested",
)
_ALLOWED_RECOMMENDATIONS = {
    "SEC_FUNDAMENTALS_PROTOTYPE_SUCCESS",
    "SEC_FUNDAMENTALS_PROTOTYPE_PARTIAL_SUCCESS",
    "SEC_FUNDAMENTALS_PROTOTYPE_BLOCKED",
    "SEC_FUNDAMENTALS_SOURCE_REJECTED",
}
_SUCCESS_OR_PARTIAL = {
    "SEC_FUNDAMENTALS_PROTOTYPE_SUCCESS",
    "SEC_FUNDAMENTALS_PROTOTYPE_PARTIAL_SUCCESS",
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


def _import_prototype():
    spec = importlib.util.spec_from_file_location("phase3f_prototype_test", _PROTOTYPE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# 1. Prototype compiles and imports
# --------------------------------------------------------------------------- #
def test_prototype_compiles():
    compile(_read(_PROTOTYPE), _PROTOTYPE, "exec")


def test_prototype_imports():
    _import_prototype()


# --------------------------------------------------------------------------- #
# 2. Expected inputs referenced; outputs under research/output (C:); no D: read/write
# --------------------------------------------------------------------------- #
def test_references_expected_inputs():
    prototype = _import_prototype()
    text = _read(_PROTOTYPE)
    for base in _EXPECTED_INPUT_BASENAMES:
        assert base in text, "prototype does not reference expected input: %s" % base
    for path in (prototype.PROTOTYPE_JSON, prototype.COMPANY_IDENTITY_CSV,
                 prototype.FUNDAMENTALS_CSV, prototype.DATA_QUALITY_JSON):
        n = path.replace("\\", "/")
        assert "research/output/" in n, n
        assert not n.upper().startswith("D:"), "output must not be on the D: drive: %s" % n


def test_no_d_drive_read_or_write_in_source():
    low = _read(_PROTOTYPE).lower()
    assert "d:/" not in low and "d:\\" not in low, \
        "prototype must not read or write the D: drive in Phase 3-F"


def test_sample_is_at_most_five_tickers():
    prototype = _import_prototype()
    assert len(prototype.SAMPLE_TICKERS) <= 5
    assert prototype.MAX_SAMPLE_TICKERS <= 5
    assert prototype.MAX_TOTAL_REQUESTS <= 20


# --------------------------------------------------------------------------- #
# 3. No api_server / Paper Trader / ML-framework imports
# --------------------------------------------------------------------------- #
def test_no_forbidden_imports():
    text = _read(_PROTOTYPE)
    assert "paper_trader" not in text.lower(), "prototype must not reference paper_trader"
    assert "import api_server" not in text and "from api_server" not in text
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = ([n.name for n in node.names]
                    + ([node.module] if isinstance(node, ast.ImportFrom)
                       and node.module else []))
            roots = {(m or "").split(".")[0] for m in mods}
            bad = roots & _FORBIDDEN_IMPORT_ROOTS
            assert not bad, "prototype imports forbidden module(s): %s" % bad


# --------------------------------------------------------------------------- #
# 4. No infra / db / deploy / trade / vendor / artifact usage tokens
# --------------------------------------------------------------------------- #
def test_no_forbidden_usage_tokens():
    low = _read(_PROTOTYPE).lower()
    for label, tokens in (
        ("infrastructure", _FORBIDDEN_INFRA_TOKENS),
        ("database-write", _FORBIDDEN_DB_TOKENS),
        ("deploy/trade", _FORBIDDEN_DEPLOY_TRADE_TOKENS),
        ("vendor-network", _FORBIDDEN_NETWORK_TOKENS),
        ("model-artifact", _FORBIDDEN_ARTIFACT_TOKENS),
    ):
        hits = [t for t in tokens if t.lower() in low]
        assert not hits, "prototype contains forbidden %s token(s): %s" % (label, hits)


# --------------------------------------------------------------------------- #
# 5. Network usage restricted to official SEC URL patterns only
# --------------------------------------------------------------------------- #
def test_network_restricted_to_sec_urls():
    text = _read(_PROTOTYPE)
    urls = re.findall(r"https?://[^\s\"')]+", text)
    assert urls, "prototype should declare its SEC endpoints"
    for url in urls:
        assert url.startswith("https://www.sec.gov/") or url.startswith("https://data.sec.gov/"), \
            "non-SEC URL present in prototype: %s" % url
    # No plain-http and no non-SEC hosts.
    assert "http://" not in text, "prototype must not use insecure/non-SEC http URLs"


def test_sec_access_policy_constants():
    prototype = _import_prototype()
    assert set(prototype.ALLOWED_SEC_HOSTS) <= {"www.sec.gov", "data.sec.gov"}
    assert prototype.MIN_REQUEST_INTERVAL_S >= 0.25
    assert "PaperTraderResearch/Phase3F" in prototype.SEC_USER_AGENT


# --------------------------------------------------------------------------- #
# 6. Committed result artifact: required fields, safety flags, structure
# --------------------------------------------------------------------------- #
def test_committed_result_artifact_valid():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed prototype JSON not present")
    d = json.loads(_read(_RESULT))
    for k in _REQUIRED_JSON_FIELDS:
        assert k in d, "committed prototype JSON missing field: %s" % k
    assert d["phase"] == "3-F"
    for k in _REQUIRED_FALSE:
        assert d[k] is False, "%s must be false" % k
    for k in _REQUIRED_TRUE:
        assert d[k] is True, "%s must be true" % k
    assert isinstance(d["network_used"], bool)


def test_reads_phase3e_and_confirms_routing():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed prototype JSON not present")
    d = json.loads(_read(_RESULT))
    confirmed = d["phase3e_summary"]["phase3e_confirmed"]
    assert confirmed["phase_is_3e"] is True
    assert confirmed["recommendation_is_free_source_prototype"] is True
    assert confirmed["next_phase_is_3f"] is True
    assert confirmed["free_source_prototype_allowed"] is True
    assert confirmed["all_confirmed"] is True
    assert d["phase3e_summary"]["phase3e_recommendation"] == \
        "PROCEED_TO_VENDOR_SELECTION_AND_FREE_SOURCE_PROTOTYPE"


def test_sample_universe_within_limits():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed prototype JSON not present")
    d = json.loads(_read(_RESULT))
    su = d["sample_universe"]
    assert len(su["sample_tickers_requested"]) <= 5
    assert su["max_sample_tickers"] <= 5
    assert len(su["sample_tickers_processed"]) <= 5
    assert su["full_128_ticker_ingestion"] is False


def test_official_sec_source_selected():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed prototype JSON not present")
    d = json.loads(_read(_RESULT))
    ss = d["source_selection"]
    assert ss["official_sec_public_source"] is True
    assert ss["covers_fundamentals"] is True
    assert ss["covers_earnings_consensus"] is False
    assert ss["covers_analyst_estimate_revisions"] is False
    assert "SEC" in ss["selected_source"]
    for url in ss["endpoints_used"]:
        assert "sec.gov" in url


def test_earnings_revisions_gap_documented():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed prototype JSON not present")
    d = json.loads(_read(_RESULT))
    gap = d["earnings_revisions_gap"]
    assert gap["sec_provides_earnings_consensus"] is False
    assert gap["sec_provides_analyst_estimate_revisions"] is False
    assert gap["provider_selection_required"] is True


def test_no_model_training_or_features():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed prototype JSON not present")
    d = json.loads(_read(_RESULT))
    interp = d["interpretation"]
    assert interp["model_trained"] is False
    assert interp["model_features_computed"] is False
    assert interp["labels_computed"] is False
    assert interp["research_model_trained"] is False
    assert interp["production_model_trained"] is False
    assert interp["production_model_candidate_created"] is False
    assert interp["deployable_model_artifact_written"] is False
    assert interp["production_data_ingested"] is False
    assert interp["full_128_ticker_ingestion"] is False
    assert interp["read_from_d_drive"] is False
    assert interp["wrote_to_d_drive"] is False
    assert interp["paid_vendor_called"] is False
    assert interp["data_purchased"] is False
    assert interp["production_edge_claimed"] is False


# --------------------------------------------------------------------------- #
# 7. Sample CSVs + data-quality report
# --------------------------------------------------------------------------- #
def test_sample_csvs_exist_when_successful():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed prototype JSON not present")
    d = json.loads(_read(_RESULT))
    rec = d["recommendation"]["recommendation"]
    if rec in _SUCCESS_OR_PARTIAL:
        assert os.path.isfile(_IDENTITY_CSV), "identity sample CSV must exist on success/partial"
        assert os.path.isfile(_FUNDAMENTALS_CSV), \
            "fundamentals sample CSV must exist on success/partial"
        irows = _read_csv_rows(_IDENTITY_CSV)
        frows = _read_csv_rows(_FUNDAMENTALS_CSV)
        for col in ("ticker", "company_name", "cik", "sector", "industry", "source",
                    "effective_from", "effective_to"):
            assert col in irows[0], "identity CSV missing column: %s" % col
        for col in ("ticker", "cik", "fiscal_period_end", "fiscal_year", "fiscal_period",
                    "form", "filed", "frame", "source_concept", "normalized_field", "value",
                    "unit", "source", "availability_datetime", "point_in_time_usable",
                    "restatement_policy", "validation_note"):
            assert col in frows[0], "fundamentals CSV missing column: %s" % col
        assert len(frows) > 0, "fundamentals CSV must have rows on success/partial"


def test_data_quality_report_exists():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed prototype JSON not present")
    if not os.path.isfile(_DATA_QUALITY):
        raise _Skip("data quality report not present")
    dq = json.loads(_read(_DATA_QUALITY))
    for k in ("sample_tickers_requested", "sample_tickers_processed", "sample_tickers_failed",
              "raw_files_written", "identity_rows", "fundamentals_rows", "fields_attempted",
              "fields_found", "missing_fields_by_ticker", "availability_datetime_coverage",
              "point_in_time_usable_fraction", "duplicate_key_count", "source_limitations",
              "sec_request_count", "used_cache_count", "network_request_count", "errors",
              "recommendation"):
        assert k in dq, "data quality report missing key: %s" % k
    assert len(dq["sample_tickers_requested"]) <= 5


# --------------------------------------------------------------------------- #
# 8. Recommendation + next phase
# --------------------------------------------------------------------------- #
def test_recommendation_and_next_phase():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed prototype JSON not present")
    d = json.loads(_read(_RESULT))
    rec = d["recommendation"]
    assert rec["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert rec["create_production_model_candidate_now"] is False
    assert rec["train_production_model_now"] is False
    assert rec["deploy_now"] is False
    assert rec["production_edge_claimed"] is False
    assert rec["full_128_ticker_ingestion_now"] is False
    assert rec["model_training_now"] is False
    assert isinstance(rec.get("selected_source"), str) and rec["selected_source"]
    assert isinstance(rec.get("reason"), str) and rec["reason"]
    assert d["recommended_next_phase"]["phase"] == "3-G"
    assert d["recommended_next_phase"]["title"]
    assert d["recommended_next_phase"]["purpose"]


# --------------------------------------------------------------------------- #
# 9. Doc has all required guardrail phrases
# --------------------------------------------------------------------------- #
def test_doc_has_required_phrases():
    text = _read(_DOC)
    missing = [p for p in _REQUIRED_DOC_PHRASES if p not in text]
    assert not missing, "doc missing required phrase(s): %s" % missing


# --------------------------------------------------------------------------- #
# 10. Live end-to-end run (gated): fetches/caches the tiny SEC sample to a temp dir
# --------------------------------------------------------------------------- #
def test_live_end_to_end_optional():
    if os.environ.get("PHASE3F_LIVE") != "1":
        raise _Skip("set PHASE3F_LIVE=1 to run the prototype end to end")
    if not os.path.isfile(_PHASE3E_JSON):
        raise _Skip("Phase 3-E results JSON not present in this environment")
    import tempfile
    prototype = _import_prototype()
    tmp = tempfile.mkdtemp(prefix="phase3f_")
    raw = os.path.join(tmp, "raw")
    res = prototype.run(
        prototype_path=os.path.join(tmp, "prototype.json"),
        identity_path=os.path.join(tmp, "identity.csv"),
        fundamentals_path=os.path.join(tmp, "fundamentals.csv"),
        data_quality_path=os.path.join(tmp, "dq.json"),
        raw_dir=raw)
    assert res["phase"] == "3-F"
    assert res["research_model_trained"] is False
    assert res["production_model_candidate_created"] is False
    assert res["deployable_model_artifact_written"] is False
    assert res["production_data_ingested"] is False
    assert res["d_drive_read"] is False
    assert res["d_drive_written"] is False
    assert res["vendor_api_called"] is False
    assert res["paid_vendor_api_called"] is False
    assert res["data_purchase_made"] is False
    assert res["sec_public_data_used"] is True
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "3-G"
    assert len(res["sample_universe"]["sample_tickers_requested"]) <= 5
    assert res["sec_access_summary"]["network_request_count"] <= prototype.MAX_TOTAL_REQUESTS


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
