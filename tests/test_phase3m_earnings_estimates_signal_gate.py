"""Phase 3-M tests for the Earnings Estimates / Surprise Data Gate + Immediate Signal Test.

These tests prove the gate is a disciplined, research-only, safety-controlled phase: it compiles and
imports; it references the committed Phase 3-L inputs and the sector map; provider API key VALUES
are never printed or written (only env-var-name -> boolean detection, redacted endpoints); the
output JSON carries every required field and safety flag; phase == "3-M"; it reads and confirms the
Phase 3-L recommendation; it never writes to D:; it fits NO model, computes NO predictions / scores /
portfolio weights, creates NO production model candidate, and writes NO deployable model artifact; it
imports no yfinance / requests / ML framework and contains no database-write / deployment /
order-trading / model-artifact tokens; when no provider key exists the result is
EARNINGS_ESTIMATES_GATE_BLOCKED_NEEDS_API_KEY and the tests still pass; when earnings data exists the
combined panel is leakage-free; the recommendation uses only allowed values and routes to Phase 3-N;
and the doc carries the required guardrail phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase3m_earnings_estimates_signal_gate.py
  * without pytest: python tests/test_phase3m_earnings_estimates_signal_gate.py
The __main__ block discovers every test_* function, runs it, prints PASS/FAIL/SKIP, and exits
non-zero on any failure. A live end-to-end rebuild (gated behind PHASE3M_LIVE=1) re-runs the gate on
a small ticker cap; without a provider key it still produces a valid BLOCKED result.
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

_ANALYZER = os.path.join(_REPO_ROOT, "research", "run_phase3m_earnings_estimates_signal_gate.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase3m_earnings_estimates_signal_gate_v1.md")
_RESULT = os.path.join(_REPO_ROOT, "research", "output",
                       "phase3m_earnings_estimates_signal_gate.json")
_M_DIR = os.path.join(_REPO_ROOT, "research", "output", "phase3m_earnings_estimates_signal_gate")
_PROVIDER_REPORT = os.path.join(_M_DIR, "provider_access_report.json")


class _Skip(Exception):
    """Raised to mark a test skipped."""


# Committed inputs this phase must reference (basenames / path fragments).
_EXPECTED_INPUT_FRAGMENTS = (
    "phase3l_sec_universe_signal_gate.json",
    "aligned_feature_price_panel_universe.csv",
    "company_identity_universe.csv",
    "label_summary_by_horizon.csv",
    "feature_ic_summary.csv",
    "horizon_readiness_summary.csv",
    "phase2k_p_sector_map_current.csv",
)

# Required output artifacts (basenames).
_EXPECTED_OUTPUT_BASENAMES = (
    "provider_access_report.json",
    "earnings_events_universe.csv",
    "earnings_features_universe.csv",
    "combined_fundamental_earnings_panel.csv",
    "earnings_feature_coverage_by_ticker.csv",
    "earnings_label_summary_by_horizon.csv",
    "earnings_feature_ic_summary.csv",
    "earnings_feature_family_ic_summary.csv",
    "earnings_horizon_readiness_summary.csv",
    "earnings_yearly_ic_summary.csv",
    "earnings_sector_sanity_summary.csv",
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
# Network IS allowed in Phase 3-M, but ONLY to the configured earnings-estimates provider hosts.
# Provider names (alpha vantage / finnhub / fmp / intrinio) are legitimate here, so they are NOT
# forbidden; yfinance and the requests package remain forbidden, as do unsupported vendors.
_FORBIDDEN_NETWORK_TOKENS = [
    "yf" + "inance",
    "import " + "requests",
    "from " + "requests",
    "poly" + "gon.io",
    "iex" + "cloud",
]
_FORBIDDEN_IMPORT_ROOTS = {
    "sklearn", "tensorflow", "torch", "keras", "lightgbm", "xgboost", "statsmodels",
    "api_server", "yfinance", "requests",
}
# Only these network hosts may appear in any URL in the source (the four supported providers).
_ALLOWED_NETWORK_HOSTS = {
    "www.alphavantage.co", "financialmodelingprep.com", "finnhub.io", "api-v2.intrinio.com",
}
_SUPPORTED_ENV_VARS = {
    "ALPHAVANTAGE_API_KEY", "FMP_API_KEY", "FINNHUB_API_KEY", "INTRINIO_API_KEY",
}

# Always present, whether the run computed the signal gate or is still collecting tickers.
_REQUIRED_JSON_FIELDS_BASE = (
    "phase", "generated_at", "inputs_read", "outputs_written", "phase3l_summary",
    "provider_access_summary", "earnings_events_summary", "earnings_feature_summary",
    "recommendation", "interpretation", "recommended_next_phase",
)
# Present only when the IC signal gate actually ran (i.e. not a resumable-collection result).
_GATE_ONLY_JSON_FIELDS = (
    "combined_panel_summary", "leakage_check_summary", "signal_gate_summary",
    "comparison_to_phase3l_sec_only",
)
# Always false regardless of provider availability.
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed", "deployment_executed",
    "model_v2_enabled", "production_edge_claimed", "research_model_trained",
    "production_model_trained", "production_model_candidate_created",
    "deployable_model_artifact_written", "paid_vendor_api_called", "data_purchase_made",
    "d_drive_read", "d_drive_written", "labels_computed", "model_trained", "predictions_computed",
    "portfolio_weights_computed",
)
# Always true regardless of provider availability.
_REQUIRED_TRUE = (
    "no_trading", "no_orders", "no_automation", "labels_for_validation_only",
)
_GATE_RECOMMENDATIONS = {
    "EARNINGS_ESTIMATES_SIGNAL_GATE_PASSES_RESEARCH_MODEL_ALLOWED",
    "EARNINGS_ESTIMATES_SIGNAL_GATE_WEAK_BUT_USEFUL",
    "EARNINGS_ESTIMATES_GATE_BLOCKED_NEEDS_API_KEY",
    "EARNINGS_ESTIMATES_GATE_BLOCKED_PROVIDER_LIMIT",
    "EARNINGS_ESTIMATES_SIGNAL_GATE_FAILS",
    "EARNINGS_ESTIMATES_SIGNAL_GATE_INCONCLUSIVE_COVERAGE",
}
# Phase 3-N resumable-collection controller recommendations (the result may carry either set).
_COLLECTION_RECOMMENDATIONS = {
    "EARNINGS_PROVIDER_COLLECTION_IN_PROGRESS",
    "EARNINGS_PROVIDER_COLLECTION_READY_FOR_SIGNAL_GATE",
    "EARNINGS_PROVIDER_COLLECTION_BLOCKED_NEEDS_API_KEY",
    "EARNINGS_PROVIDER_COLLECTION_BLOCKED_PROVIDER_LIMIT",
    "EARNINGS_PROVIDER_COLLECTION_ERROR",
}
_ALLOWED_RECOMMENDATIONS = _GATE_RECOMMENDATIONS | _COLLECTION_RECOMMENDATIONS
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
    spec = importlib.util.spec_from_file_location("phase3m_analyzer_test", _ANALYZER)
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
# 2. Expected Phase 3-L inputs referenced; outputs under research/output (C:)
# --------------------------------------------------------------------------- #
def test_references_expected_inputs():
    text = _read(_ANALYZER)
    for frag in _EXPECTED_INPUT_FRAGMENTS:
        assert frag in text, "analyzer does not reference expected input: %s" % frag


def test_outputs_under_research_output_and_not_on_d():
    mod = _import_analyzer()
    for path in (mod.RESULT_JSON, mod.PROVIDER_ACCESS_JSON, mod.EARNINGS_EVENTS_CSV,
                 mod.EARNINGS_FEATURES_CSV, mod.COMBINED_PANEL_CSV, mod.EARNINGS_FEATURE_IC_CSV,
                 mod.DECISION_TABLE_CSV):
        n = path.replace("\\", "/")
        assert "research/output/" in n, n
        assert not n.upper().startswith("D:"), "output must not be on the D: drive: %s" % n


def test_no_d_drive_paths_at_all():
    """Phase 3-M touches no D: drive - no path constant may reference D:."""
    mod = _import_analyzer()
    for name in dir(mod):
        val = getattr(mod, name)
        if isinstance(val, str) and val.replace("\\", "/").upper().startswith("D:"):
            raise AssertionError("Phase 3-M must not reference any D: path: %s=%s" % (name, val))
    flags = mod.build_safety_flags(mod.REC_BLOCKED_KEY, network_used=False, vendor_called=False,
                                   provider_selected=None, features_computed=False,
                                   price_join=False, labels_reused=False)
    assert flags["d_drive_read"] is False
    assert flags["d_drive_written"] is False


# --------------------------------------------------------------------------- #
# 3. No api_server / Paper Trader / ML-framework / vendor-package imports
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
# 4. No infra / db / deploy / trade / vendor / artifact / model-fit tokens
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


def test_network_restricted_to_provider_hosts():
    """Every URL in the source must point at a supported provider host (no other domains)."""
    text = _read(_ANALYZER)
    urls = re.findall(r"https?://[^\s\"')]+", text)
    for u in urls:
        host = re.sub(r"^https?://", "", u).split("/")[0]
        assert host in _ALLOWED_NETWORK_HOSTS, "non-provider network host in source: %s" % u


def test_no_yfinance_anywhere():
    low = _read(_ANALYZER).lower()
    assert "yf" + "inance" not in low, "yfinance must not appear"


# --------------------------------------------------------------------------- #
# 5. Provider key VALUES are never printed or written
# --------------------------------------------------------------------------- #
def test_provider_keys_read_from_env_only():
    mod = _import_analyzer()
    # Supported env vars are exactly the four required ones.
    assert set(mod.SUPPORTED_ENV_VARS) == _SUPPORTED_ENV_VARS
    # Detection returns booleans only (no values) and selects nothing without a key.
    sel, env_var, detected, host = mod.detect_providers(env={})
    assert sel is None and env_var is None and host is None
    assert set(detected.keys()) == _SUPPORTED_ENV_VARS
    for v in detected.values():
        assert isinstance(v, bool)
    # With a (fake) key present, priority picks Alpha Vantage and still stores no value.
    sel2, env2, detected2, host2 = mod.detect_providers(env={"ALPHAVANTAGE_API_KEY": "DUMMY"})
    assert sel2 == mod.PROVIDER_ALPHAVANTAGE and env2 == "ALPHAVANTAGE_API_KEY"
    assert all(isinstance(v, bool) for v in detected2.values())


def test_provider_access_report_has_no_key_values():
    mod = _import_analyzer()
    summary = mod.build_provider_access_summary(
        provider_selected=None, detected_keys={e: False for e in mod.SUPPORTED_ENV_VARS},
        client=None, provider_limited=False, recommendation=mod.REC_BLOCKED_KEY)
    for v in summary["detected_keys"].values():
        assert isinstance(v, bool)
    assert summary["key_values_never_stored"] is True
    # The endpoint template, when present, must be redacted.
    for tmpl in mod.ENDPOINT_TEMPLATES.values():
        assert "REDACTED" in tmpl
        assert "{apikey}" not in tmpl  # no literal key placeholder leaked either


def test_no_secret_looking_values_in_artifacts():
    """Any apikey/token/api_key parameter persisted in the artifacts must be REDACTED."""
    for path in (_RESULT, _PROVIDER_REPORT):
        if not os.path.isfile(path):
            continue
        text = _read(path)
        for m in re.findall(r"(?:apikey|api_key|token)=([^&\"'\s\\]+)", text):
            assert m == "REDACTED", "a non-redacted credential parameter was persisted: %s" % m


# --------------------------------------------------------------------------- #
# 6. Committed result artifact: required fields, safety flags, structure
# --------------------------------------------------------------------------- #
def _result():
    return json.loads(_read(_RESULT))


def _is_collection_result(d):
    """A resumable-collection (non-gate) result carries a collection recommendation."""
    return d.get("recommendation", {}).get("recommendation") in _COLLECTION_RECOMMENDATIONS


def test_committed_result_artifact_valid():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    d = _result()
    for k in _REQUIRED_JSON_FIELDS_BASE:
        assert k in d, "committed JSON missing field: %s" % k
    if not _is_collection_result(d):
        for k in _GATE_ONLY_JSON_FIELDS:
            assert k in d, "committed gate JSON missing field: %s" % k
    assert d["phase"] == "3-M"
    for k in _REQUIRED_FALSE:
        assert d[k] is False, "%s must be false" % k
    for k in _REQUIRED_TRUE:
        assert d[k] is True, "%s must be true" % k


def test_reads_phase3l_and_confirms_routing():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    d = _result()
    confirmed = d["phase3l_summary"]["phase3l_confirmed"]
    assert confirmed["phase_is_3l"] is True
    assert confirmed["recommendation_is_weak_add_revisions"] is True
    assert confirmed["next_phase_is_3m"] is True
    assert confirmed["processed_ticker_count_128"] is True
    assert confirmed["aligned_ticker_count_128"] is True
    assert confirmed["leakage_failure_count_zero"] is True
    assert confirmed["research_model_allowed_next_false"] is True
    assert confirmed["all_confirmed"] is True
    assert d["phase3l_summary"]["phase3l_recommendation"] == \
        "SEC_UNIVERSE_SIGNAL_GATE_WEAK_BUT_EXPAND_OR_ADD_REVISIONS"


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
    for k in ("model_trained", "research_model_trained", "production_model_trained",
              "production_model_candidate_created", "deployable_model_artifact_written",
              "predictions_computed", "scores_computed", "portfolio_weights_computed",
              "labels_computed"):
        assert interp[k] is False, "interpretation.%s must be false" % k
    assert interp["labels_for_validation_only"] is True
    rec = d["recommendation"]
    assert rec["create_production_model_candidate_now"] is False
    assert rec["train_production_model_now"] is False
    assert rec["deploy_now"] is False
    assert rec["production_edge_claimed"] is False


def test_labels_reused_not_recomputed():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    d = _result()
    assert d["labels_computed"] is False
    assert d["labels_for_validation_only"] is True
    if "signal_gate_summary" in d:
        assert d["signal_gate_summary"]["labels_for_validation_only"] is True


def test_no_paid_vendor_or_purchase():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    d = _result()
    assert d["paid_vendor_api_called"] is False
    assert d["data_purchase_made"] is False


def test_leakage_zero_unless_blocked():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    d = _result()
    rec = d["recommendation"]["recommendation"]
    # Leakage is only meaningful once the gate ran (combined panel built). BLOCKED and
    # resumable-collection results carry no leakage summary.
    if ("leakage_check_summary" in d
            and rec not in ("EARNINGS_ESTIMATES_GATE_BLOCKED_NEEDS_API_KEY",
                            "EARNINGS_ESTIMATES_GATE_BLOCKED_PROVIDER_LIMIT")):
        assert d["leakage_check_summary"]["leakage_failure_count"] == 0, \
            "a non-blocked run must be leakage-free"


def test_blocked_when_no_key_present():
    """No provider selected -> a BLOCKED_NEEDS_API_KEY routing that never allows a model.

    Phase 3-N hardening: when raw cache already exists the keyless run preserves it (the
    collection variant), and only an empty cache yields the zeroed gate variant. Both route to
    "Configure Earnings Estimates Provider" and allow no research model.
    """
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    d = _result()
    if d["provider_access_summary"]["selected_provider"] is not None:
        raise _Skip("committed run had a provider key; no-key branch not exercised")
    rec = d["recommendation"]["recommendation"]
    assert rec in ("EARNINGS_ESTIMATES_GATE_BLOCKED_NEEDS_API_KEY",
                   "EARNINGS_PROVIDER_COLLECTION_BLOCKED_NEEDS_API_KEY")
    assert d["recommendation"]["research_model_allowed_next"] is False
    assert d["research_model_allowed_next"] is False
    assert d["recommended_next_phase"]["phase"] == "3-N"
    assert d["recommended_next_phase"]["title"] == "Configure Earnings Estimates Provider"
    if rec == "EARNINGS_ESTIMATES_GATE_BLOCKED_NEEDS_API_KEY":
        # Empty cache -> nothing faked: no events / features / combined rows.
        assert d["earnings_events_summary"]["event_rows"] == 0
        assert d.get("combined_panel_summary", {}).get("combined_panel_rows", 0) == 0
    else:
        # Cache present -> partial state is PRESERVED, not zeroed.
        assert d["collection_progress"]["cached_ticker_count_after_run"] >= 1
        assert d["earnings_events_summary"]["event_rows"] >= 0


# --------------------------------------------------------------------------- #
# 7. Diagnostic artifacts exist and are well-formed
# --------------------------------------------------------------------------- #
def test_output_artifacts_exist():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    for base in _EXPECTED_OUTPUT_BASENAMES:
        assert os.path.isfile(os.path.join(_M_DIR, base)), "missing output artifact: %s" % base


def test_decision_table_well_formed():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    rows = _read_csv_rows(os.path.join(_M_DIR, "decision_table.csv"))
    assert rows, "decision table must have rows"
    for col in ("decision_item", "value", "passed", "note"):
        assert col in rows[0], "decision table CSV missing column: %s" % col


def test_earnings_feature_ic_csv_header():
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    # Header-only is valid in the BLOCKED case; check the schema columns exist.
    mod = _import_analyzer()
    with open(os.path.join(_M_DIR, "earnings_feature_ic_summary.csv"), "r",
              encoding="utf-8", newline="") as f:
        header = (f.readline() or "").strip().split(",")
    for col in ("feature", "feature_family", "feature_group", "horizon", "absolute_mean_ic",
                "diagnostic_strength"):
        assert col in header, "earnings feature IC CSV missing column: %s" % col


# --------------------------------------------------------------------------- #
# 8. Recommendation + next phase
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
    assert isinstance(rec.get("reason"), str) and rec["reason"]
    assert d["recommended_next_phase"]["phase"] == "3-N"
    assert d["recommended_next_phase"]["title"]
    assert d["recommended_next_phase"]["purpose"]
    if rec["recommendation"] == "EARNINGS_ESTIMATES_SIGNAL_GATE_PASSES_RESEARCH_MODEL_ALLOWED":
        assert rec["research_model_allowed_next"] is True
        assert d["research_model_allowed_next"] is True
    else:
        assert rec["research_model_allowed_next"] is False
        assert d["research_model_allowed_next"] is False


def test_all_recommendations_route_to_3n():
    mod = _import_analyzer()
    for rec in list(mod.ALLOWED_RECOMMENDATIONS) + list(mod.COLLECTION_RECOMMENDATIONS):
        nxt = mod.build_recommended_next_phase(rec)
        assert nxt["phase"] == "3-N"
        assert nxt["title"] and nxt["purpose"]


# --------------------------------------------------------------------------- #
# 8b. Phase 3-N resumable-collection controller
# --------------------------------------------------------------------------- #
def _collection_progress():
    mod = _import_analyzer()
    return json.loads(_read(mod.COLLECTION_PROGRESS_JSON))


def test_collection_controls_defaults():
    """Env-var controls default exactly as specified and read scalars only (no key value)."""
    mod = _import_analyzer()
    c = mod.read_collection_controls(env={}, provider=mod.PROVIDER_ALPHAVANTAGE)
    assert c["max_network_requests_per_run"] == 20
    assert c["min_provider_sleep_seconds"] == 15.0
    assert c["allow_partial_collection"] is True
    assert c["min_tickers_for_signal_gate"] == 75
    assert c["force_signal_gate"] is False
    # Overrides parse correctly.
    c2 = mod.read_collection_controls(env={
        "PHASE3M_MAX_NETWORK_REQUESTS_PER_RUN": "5",
        "PHASE3M_MIN_PROVIDER_SLEEP_SECONDS": "20",
        "PHASE3M_ALLOW_PARTIAL_COLLECTION": "0",
        "PHASE3M_MIN_TICKERS_FOR_SIGNAL_GATE": "40",
        "PHASE3M_FORCE_SIGNAL_GATE": "1",
    }, provider=mod.PROVIDER_ALPHAVANTAGE)
    assert c2["max_network_requests_per_run"] == 5
    assert c2["min_provider_sleep_seconds"] == 20.0
    assert c2["allow_partial_collection"] is False
    assert c2["min_tickers_for_signal_gate"] == 40
    assert c2["force_signal_gate"] is True


def test_collection_progress_builder_no_key_values():
    """build_collection_progress reports booleans only and never persists a key value."""
    mod = _import_analyzer()
    detected = {e: (e == "ALPHAVANTAGE_API_KEY") for e in mod.SUPPORTED_ENV_VARS}
    prog = mod.build_collection_progress(
        mod.PROVIDER_ALPHAVANTAGE, detected, ["AAPL", "MSFT", "IBM"], 1, 20, 2, 3, 0,
        provider_limit_hit=True, provider_limit_message="provider returned a rate-limit response",
        min_tickers=75, signal_gate_allowed=False)
    assert prog["phase"] == "3-N"
    assert prog["no_key_values_written"] is True
    for v in prog["supported_provider_keys_detected_as_booleans"].values():
        assert isinstance(v, bool)
    assert isinstance(prog["signal_gate_allowed_now"], bool)
    assert prog["cached_ticker_count_after_run"] == 3
    assert prog["missing_ticker_count_after_run"] == 0
    # The sanitized provider message never carries a URL or key-bearing token.
    msg = prog["provider_limit_message_sanitized"]
    assert "http" not in msg.lower() and "token" not in msg.lower() and "apikey" not in msg.lower()
    # Serialize and confirm no obvious secret leaks.
    blob = json.dumps(prog)
    assert "REDACTED" not in blob  # nothing key-shaped is even referenced here
    assert "DUMMY" not in blob


def test_collection_progress_partial_limit_does_not_fail():
    """A provider-limit partial collection still yields a valid, complete progress record."""
    mod = _import_analyzer()
    detected = {e: False for e in mod.SUPPORTED_ENV_VARS}
    detected["ALPHAVANTAGE_API_KEY"] = True
    prog = mod.build_collection_progress(
        mod.PROVIDER_ALPHAVANTAGE, detected, ["A"] * 128, 25, 20, 0, 25, 103,
        provider_limit_hit=True, provider_limit_message="rate limit", min_tickers=75,
        signal_gate_allowed=False)
    required = {
        "phase", "generated_at", "selected_provider",
        "supported_provider_keys_detected_as_booleans", "universe_ticker_count",
        "cached_ticker_count_before_run", "network_request_budget", "network_requests_this_run",
        "cached_ticker_count_after_run", "missing_ticker_count_after_run", "provider_limit_hit",
        "provider_limit_message_sanitized", "signal_gate_min_tickers", "signal_gate_allowed_now",
        "estimated_additional_runs_needed_at_current_budget", "next_action",
        "no_key_values_written",
    }
    assert required <= set(prog), "progress missing fields: %s" % (required - set(prog))
    assert prog["provider_limit_hit"] is True
    assert prog["signal_gate_allowed_now"] is False
    # 50 still needed (75-25), 20/run -> ceil(50/20) = 3 additional runs.
    assert prog["estimated_additional_runs_needed_at_current_budget"] == 3


def test_committed_collection_progress_artifacts():
    """When the committed run was a resumable-collection run, its progress artifacts are valid."""
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    mod = _import_analyzer()
    if not (os.path.isfile(mod.COLLECTION_PROGRESS_JSON)
            and os.path.isfile(mod.COLLECTION_PROGRESS_CSV)):
        raise _Skip("no committed collection progress artifacts")
    prog = _collection_progress()
    assert prog["phase"] == "3-N"
    assert prog["no_key_values_written"] is True
    assert isinstance(prog["cached_ticker_count_after_run"], int)
    assert isinstance(prog["missing_ticker_count_after_run"], int)
    assert isinstance(prog["signal_gate_allowed_now"], bool)
    for v in prog["supported_provider_keys_detected_as_booleans"].values():
        assert isinstance(v, bool)
    # The CSV is a flat metric/value table that mirrors the JSON metrics.
    rows = _read_csv_rows(mod.COLLECTION_PROGRESS_CSV)
    metrics = {r["metric"] for r in rows}
    for m in ("cached_ticker_count_after_run", "missing_ticker_count_after_run",
              "signal_gate_allowed_now", "phase"):
        assert m in metrics, "collection_progress.csv missing metric: %s" % m


def test_committed_collection_result_when_in_progress():
    """If the committed result is a collection result, it carries the contract fields."""
    if not os.path.isfile(_RESULT):
        raise _Skip("committed result JSON not present")
    d = _result()
    if not _is_collection_result(d):
        raise _Skip("committed result is a gate result, not a collection result")
    assert d["phase"] == "3-M"
    assert "collection_progress" in d
    assert isinstance(d["cached_ticker_count_after_run"], int)
    assert isinstance(d["missing_ticker_count_after_run"], int)
    assert d["signal_gate_allowed_now"] is False
    assert d["research_model_allowed_next"] is False
    assert d["recommendation"]["research_model_allowed_next"] is False
    assert d["recommended_next_phase"]["phase"] == "3-N"
    if d["recommendation"]["recommendation"] == "EARNINGS_PROVIDER_COLLECTION_IN_PROGRESS":
        assert d["recommended_next_phase"]["title"] == "Continue Resumable Earnings Collection"


# --------------------------------------------------------------------------- #
# 8c. Phase 3-N hardening: status-only mode, no-key cache preservation, same-day limit guard
# --------------------------------------------------------------------------- #
# Output path constants redirected into a temp dir so run() never touches committed artifacts.
_OUTPUT_PATH_ATTRS = (
    "RESULT_JSON", "PROVIDER_ACCESS_JSON", "EARNINGS_EVENTS_CSV", "EARNINGS_FEATURES_CSV",
    "COMBINED_PANEL_CSV", "EARNINGS_FEATURE_COVERAGE_CSV", "EARNINGS_LABEL_SUMMARY_CSV",
    "EARNINGS_FEATURE_IC_CSV", "EARNINGS_FEATURE_FAMILY_IC_CSV", "EARNINGS_HORIZON_READINESS_CSV",
    "EARNINGS_YEARLY_IC_CSV", "EARNINGS_SECTOR_SANITY_CSV", "DECISION_TABLE_CSV",
    "COLLECTION_PROGRESS_JSON", "COLLECTION_PROGRESS_CSV",
)
_DUMMY_KEY = "DUMMY-NOT-A-REAL-KEY"


def _sandbox_run(env, seed_cached=0, seed_progress=None, block_network=True):
    """Run the analyzer with ALL outputs redirected into a temp dir; never touches committed state.

    Optionally seeds the temp raw cache from real committed alpha_vantage_*.json payloads and/or a
    prior collection_progress.json. Network is tripwired OFF by default so no provider request can
    ever be made: if run() tries to fetch, the test fails loudly instead of hitting the network.
    Returns (mod, tmpdir, result).
    """
    import tempfile
    import shutil
    mod = _import_analyzer()
    tmp = tempfile.mkdtemp(prefix="phase3m_hard_")
    raw = os.path.join(tmp, "raw")
    os.makedirs(raw, exist_ok=True)
    for attr in _OUTPUT_PATH_ATTRS:
        setattr(mod, attr, os.path.join(tmp, os.path.basename(getattr(mod, attr))))
    src_raw = os.path.join(_M_DIR, "raw")
    if seed_cached and os.path.isdir(src_raw):
        files = sorted(f for f in os.listdir(src_raw)
                       if f.startswith("alpha_vantage_") and f.endswith(".json"))[:seed_cached]
        for f in files:
            shutil.copyfile(os.path.join(src_raw, f), os.path.join(raw, f))
    if seed_progress is not None:
        with open(mod.COLLECTION_PROGRESS_JSON, "w", encoding="utf-8") as fh:
            json.dump(seed_progress, fh)
    if block_network:
        def _no_net(self, symbol):  # noqa: ARG001
            raise AssertionError("network must not be attempted in this scenario: %s" % symbol)
        mod.ProviderClient.get_earnings = _no_net
    res = mod.run(result_json_path=mod.RESULT_JSON, m_dir=tmp, raw_dir=raw, env=env, verbose=False)
    return mod, tmp, res


def test_status_only_and_guard_controls_exist():
    """PHASE3M_STATUS_ONLY and PHASE3M_IGNORE_LIMIT_GUARD parse as scalar booleans (default 0)."""
    mod = _import_analyzer()
    default = mod.read_collection_controls(env={}, provider=mod.PROVIDER_ALPHAVANTAGE)
    assert default["status_only"] is False
    assert default["ignore_limit_guard"] is False
    on = mod.read_collection_controls(
        env={"PHASE3M_STATUS_ONLY": "1", "PHASE3M_IGNORE_LIMIT_GUARD": "1"},
        provider=mod.PROVIDER_ALPHAVANTAGE)
    assert on["status_only"] is True
    assert on["ignore_limit_guard"] is True


def test_same_day_limit_guard_logic():
    """The same-day guard fires only for a prior same-UTC-date limit, and the ignore flag bypasses."""
    mod = _import_analyzer()
    today = mod._utc_date_of(mod._utc_now_iso())
    hit_today = {"provider_limit_hit": True, "provider_limit_last_hit_utc": today}
    active, last = mod.compute_same_day_limit_guard(hit_today, ignore_guard=False)
    assert active is True and last == today
    # Deliberate bypass.
    assert mod.compute_same_day_limit_guard(hit_today, ignore_guard=True)[0] is False
    # A limit from another day no longer guards.
    old = {"provider_limit_hit": True, "provider_limit_last_hit_utc": "2000-01-01"}
    a3, l3 = mod.compute_same_day_limit_guard(old, ignore_guard=False)
    assert a3 is False and l3 == "2000-01-01"
    # No prior limit / no prior progress -> never active.
    assert mod.compute_same_day_limit_guard({"provider_limit_hit": False}, False) == (False, None)
    assert mod.compute_same_day_limit_guard(None, False) == (False, None)


def test_progress_builder_has_hardening_fields_and_next_actions():
    """build_collection_progress carries the new helper fields and routes next_action correctly."""
    mod = _import_analyzer()
    detected = {e: False for e in mod.SUPPORTED_ENV_VARS}
    no_key = mod.build_collection_progress(
        None, detected, ["AAPL"] * 30, 25, 20, 0, 25, 5, provider_limit_hit=False,
        provider_limit_message="", min_tickers=75, signal_gate_allowed=False,
        status_only=True, same_day_limit_guard_active=True, can_attempt_network_now=False,
        reason_network_not_attempted="status_only mode", provider_limit_last_hit_utc="2026-06-19")
    for k in ("generated_at_utc", "provider_limit_last_hit_utc", "same_day_limit_guard_active",
              "status_only", "can_attempt_network_now", "reason_network_not_attempted"):
        assert k in no_key, "progress missing hardening field: %s" % k
    assert no_key["status_only"] is True
    assert no_key["same_day_limit_guard_active"] is True
    assert no_key["can_attempt_network_now"] is False
    # No key but cache present -> ask the operator to set a key to continue collecting.
    assert no_key["next_action"] == "SET_PROVIDER_KEY_TO_CONTINUE_COLLECTION"
    # No key and no cache -> the plain set-key action.
    no_cache = mod.build_collection_progress(
        None, detected, ["AAPL"] * 30, 0, 20, 0, 0, 30, provider_limit_hit=False,
        provider_limit_message="", min_tickers=75, signal_gate_allowed=False)
    assert no_cache["next_action"] == "SET_PROVIDER_KEY"
    # Provider limit / same-day guard -> wait-for-reset action.
    limited = mod.build_collection_progress(
        mod.PROVIDER_ALPHAVANTAGE, detected, ["AAPL"] * 30, 25, 20, 0, 25, 5,
        provider_limit_hit=True, provider_limit_message="rate limit", min_tickers=75,
        signal_gate_allowed=False)
    assert limited["next_action"] == "WAIT_FOR_PROVIDER_LIMIT_RESET_OR_USE_HIGHER_LIMIT_PROVIDER"


def test_no_key_with_cache_preserves_state_and_does_not_wipe_events():
    """A keyless run with cached raw files preserves the cached count and the events CSV."""
    if not os.path.isdir(os.path.join(_M_DIR, "raw")):
        raise _Skip("no committed raw cache to seed from")
    mod, tmp, res = _sandbox_run(env={}, seed_cached=3)
    assert res["recommendation"]["recommendation"] == \
        "EARNINGS_PROVIDER_COLLECTION_BLOCKED_NEEDS_API_KEY"
    prog = res["collection_progress"]
    assert prog["cached_ticker_count_after_run"] == 3
    assert prog["missing_ticker_count_after_run"] == prog["universe_ticker_count"] - 3
    assert prog["next_action"] == "SET_PROVIDER_KEY_TO_CONTINUE_COLLECTION"
    assert prog["status_only"] is False
    assert prog["network_requests_this_run"] == 0
    # The events CSV must NOT be wiped: it carries real rows from the 3 cached tickers.
    rows = _read_csv_rows(mod.EARNINGS_EVENTS_CSV)
    assert len(rows) > 0, "keyless run with cache must NOT zero earnings_events_universe.csv"
    # Raw cache is intact (no file deleted, none added).
    assert len([f for f in os.listdir(tmp + os.sep + "raw") if f.endswith(".json")]) == 3
    # No modeling of any kind.
    for k in ("model_trained", "predictions_computed", "portfolio_weights_computed",
              "production_model_candidate_created", "deployable_model_artifact_written",
              "d_drive_written"):
        assert res[k] is False, "%s must be false" % k
    # No key value is ever written.
    assert json.loads(_read(mod.COLLECTION_PROGRESS_JSON))["no_key_values_written"] is True


def test_no_key_without_cache_blocks_without_destroying_unrelated_files():
    """A keyless run with no cache still produces a clean BLOCKED result (header-only artifacts)."""
    mod, tmp, res = _sandbox_run(env={}, seed_cached=0)
    assert res["recommendation"]["recommendation"] == "EARNINGS_ESTIMATES_GATE_BLOCKED_NEEDS_API_KEY"
    prog = res["collection_progress"]
    assert prog["cached_ticker_count_after_run"] == 0
    assert prog["next_action"] == "SET_PROVIDER_KEY"
    assert _read_csv_rows(mod.EARNINGS_EVENTS_CSV) == []


def test_status_only_mode_does_not_call_provider():
    """status-only mode reads the cache and writes status, but never attempts a provider request."""
    if not os.path.isdir(os.path.join(_M_DIR, "raw")):
        raise _Skip("no committed raw cache to seed from")
    env = {"ALPHAVANTAGE_API_KEY": _DUMMY_KEY, "PHASE3M_STATUS_ONLY": "1"}
    mod, tmp, res = _sandbox_run(env, seed_cached=3, block_network=True)
    prog = res["collection_progress"]
    assert prog["status_only"] is True
    assert prog["can_attempt_network_now"] is False
    assert prog["network_requests_this_run"] == 0
    assert "status_only" in prog["reason_network_not_attempted"]
    assert prog["cached_ticker_count_after_run"] == 3
    # The dummy key value must never reach any written artifact.
    blob = _read(mod.RESULT_JSON) + _read(mod.COLLECTION_PROGRESS_JSON) + _read(mod.PROVIDER_ACCESS_JSON)
    assert _DUMMY_KEY not in blob, "API key value must never be written to any artifact"


def test_same_day_guard_prevents_provider_request_in_run():
    """A prior same-UTC-day provider limit blocks another request without any network call."""
    if not os.path.isdir(os.path.join(_M_DIR, "raw")):
        raise _Skip("no committed raw cache to seed from")
    mod0 = _import_analyzer()
    today = mod0._utc_date_of(mod0._utc_now_iso())
    seed = {"phase": "3-N", "provider_limit_hit": True, "provider_limit_last_hit_utc": today,
            "generated_at_utc": mod0._utc_now_iso(), "cached_ticker_count_after_run": 3}
    env = {"ALPHAVANTAGE_API_KEY": _DUMMY_KEY}
    mod, tmp, res = _sandbox_run(env, seed_cached=3, seed_progress=seed, block_network=True)
    prog = res["collection_progress"]
    assert prog["same_day_limit_guard_active"] is True
    assert prog["can_attempt_network_now"] is False
    assert prog["network_requests_this_run"] == 0
    assert prog["next_action"] == "WAIT_FOR_PROVIDER_LIMIT_RESET_OR_USE_HIGHER_LIMIT_PROVIDER"
    assert prog["provider_limit_last_hit_utc"] == today
    assert res["recommendation"]["recommendation"] == \
        "EARNINGS_PROVIDER_COLLECTION_BLOCKED_PROVIDER_LIMIT"


# --------------------------------------------------------------------------- #
# 9. Doc has all required guardrail phrases
# --------------------------------------------------------------------------- #
def test_doc_has_required_phrases():
    text = _read(_DOC)
    missing = [p for p in _REQUIRED_DOC_PHRASES if p not in text]
    assert not missing, "doc missing required phrase(s): %s" % missing


# --------------------------------------------------------------------------- #
# 10. Live end-to-end rebuild (gated): without a key it still yields a valid BLOCKED result
# --------------------------------------------------------------------------- #
def test_live_end_to_end_optional():
    if os.environ.get("PHASE3M_LIVE") != "1":
        raise _Skip("set PHASE3M_LIVE=1 to rebuild the gate end to end")
    import tempfile
    mod = _import_analyzer()
    tmp = tempfile.mkdtemp(prefix="phase3m_")
    cap = int(os.environ.get("PHASE3M_LIVE_TICKERS", "8"))
    res = mod.run(result_json_path=os.path.join(tmp, "result.json"), m_dir=tmp,
                  raw_dir=os.path.join(tmp, "raw"), max_tickers=cap, verbose=False)
    assert res["phase"] == "3-M"
    assert res["d_drive_read"] is False
    assert res["d_drive_written"] is False
    assert res["model_trained"] is False
    assert res["predictions_computed"] is False
    assert res["portfolio_weights_computed"] is False
    assert res["production_model_candidate_created"] is False
    assert res["deployable_model_artifact_written"] is False
    assert res["labels_computed"] is False
    assert res["labels_for_validation_only"] is True
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "3-N"


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
