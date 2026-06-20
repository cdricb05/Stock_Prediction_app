"""Phase 3-V tests for the Global ETF Price Data Pack.

These tests prove the phase is a disciplined, research-only, Stooq-ONLY, NON-faking local data
preparation pack: the runner compiles, imports, and defines the required functions; the result JSON
exists with phase == "3-V"; recommended_next_phase.phase == "3-W"; every required output CSV exists;
the target universe has >= 20 proxies; the only network domain referenced is stooq.com; the source
contains no yfinance / Alpha Vantage / FRED / paid-vendor / DB-write / deployment / order-trading /
production-candidate / deployable-artifact tokens; the runner writes nothing to the data drive and
never writes a Phase 3-M or Phase 3-U path; no price data is faked; and every output file is
Git-safe (< 50 MB). A fast offline run (allow_network=False) proves Phase 3-M and Phase 3-U outputs
are not modified and the gate stays safe with zero downloads.

Runs two ways:
  * under pytest:   pytest tests/test_phase3v_global_etf_price_data_pack.py
  * without pytest: python tests/test_phase3v_global_etf_price_data_pack.py
The __main__ block discovers every test_* function, runs it, prints PASS/FAIL/SKIP, and exits
non-zero on any failure.
"""
from __future__ import annotations

import ast
import csv
import importlib.util
import json
import os
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_RUNNER = os.path.join(_REPO_ROOT, "research", "run_phase3v_global_etf_price_data_pack.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase3v_global_etf_price_data_pack_v1.md")
_RESULT = os.path.join(_REPO_ROOT, "research", "output",
                       "phase3v_global_etf_price_data_pack.json")
_V_DIR = os.path.join(_REPO_ROOT, "research", "output", "phase3v_global_etf_price_data_pack")
_M_DIR = os.path.join(_REPO_ROOT, "research", "output", "phase3m_earnings_estimates_signal_gate")
_U_DIR = os.path.join(_REPO_ROOT, "research", "output",
                      "phase3u_global_asset_universe_readiness")


class _Skip(Exception):
    """Raised to mark a test skipped."""


_EXPECTED_OUTPUT_BASENAMES = (
    "download_manifest.csv",
    "global_etf_price_panel.csv",
    "global_etf_price_coverage.csv",
    "global_etf_price_quality.csv",
    "missing_or_failed_downloads.csv",
    "cross_asset_feature_readiness.csv",
    "readiness_decision_table.csv",
)

_REQUIRED_FUNCTIONS = (
    "confirm_phase3u", "load_target_universe", "collect_price_pack", "normalize_stooq_csv",
    "build_feature_readiness", "decide", "run",
)

# Forbidden tokens (assembled from fragments so they never self-match this test's prose). Stooq via
# stdlib urllib is ALLOWED here, so urllib/http(s) are deliberately NOT forbidden. The flag names
# "yfinance_called" / "alpha_vantage_called" / "fred_called" remain allowed (the patterns below are
# import/attribute/usage forms only).
_FORBIDDEN_NETWORK_TOKENS = [
    "import " + "yf" + "inance", "yf" + "inance.", "alpha" + "vantage", "fin" + "nhub",
    "poly" + "gon.io", "iex" + "cloud", "fred" + "api", "pandas_" + "datareader",
    "stlouis" + "fed", "yahoo", "tiin" + "go", "quan" + "dl", ".download(",
]
_FORBIDDEN_INFRA_TOKENS = [
    "gcl" + "oud", "sub" + "process", "os." + "system", "para" + "miko", "system" + "ctl",
    "kube" + "ctl", "alem" + "bic", "PREDICTOR_USE_" + "MODEL_V2", "uvi" + "corn",
]
_FORBIDDEN_DB_TOKENS = [
    "insert " + "into", "delete " + "from", "drop " + "table", "alter " + "table",
    "create " + "table", "trun" + "cate", "to_" + "sql",
]
_FORBIDDEN_DEPLOY_TRADE_TOKENS = [
    "place" + "_order", "submit" + "_order", "create" + "_order", "ssh ", "scp ",
]
_FORBIDDEN_ARTIFACT_TOKENS = [
    "to_" + "pickle", "pickle." + "dump", "joblib." + "dump", "import " + "pickle",
    "import " + "joblib", ".pk" + "l", ".job" + "lib", ".onn" + "x", ".ker" + "as",
]
# Network library roots that must NOT be imported (urllib IS allowed and intentionally absent here).
_FORBIDDEN_IMPORT_ROOTS = {
    "sklearn", "tensorflow", "torch", "keras", "lightgbm", "xgboost",
    "api_server", "yfinance", "requests", "httpx", "pandas_datareader",
}
# Only the Stooq domain may appear; any other data-vendor host is forbidden.
_FORBIDDEN_DOMAINS = [
    "finance.yahoo", "query1.finance", "alphavantage.co", "stlouisfed.org",
    "finnhub.io", "polygon.io", "iexcloud.io", "tiingo.com", "quandl.com",
]

_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "phase3u_confirmation", "target_count", "downloaded_count",
    "usable_count", "missing_count", "asset_classes_covered", "recommendation",
    "recommended_next_phase",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed", "deployment_executed",
    "model_v2_enabled", "production_edge_claimed", "production_model_trained",
    "production_model_candidate_created", "deployable_model_artifact_written",
    "production_predictions_computed", "production_scores_computed", "portfolio_weights_computed",
    "order_instructions_created", "d_drive_read", "d_drive_written", "alpha_vantage_called",
    "fred_called", "yfinance_called", "paid_vendor_api_called", "non_stooq_network_called",
    "data_faked",
)
_REQUIRED_TRUE = (
    "no_trading", "no_orders", "no_automation", "research_only", "labels_for_validation_only",
)
_ALLOWED_RECOMMENDATIONS = {
    "GLOBAL_ETF_PRICE_DATA_READY",
    "GLOBAL_ETF_PRICE_DATA_PARTIAL_DOWNLOAD",
    "GLOBAL_ETF_PRICE_DATA_BLOCKED_DOWNLOAD_FAILURE",
}
_MAX_FILE_BYTES = 50 * 1024 * 1024


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _read_csv_rows(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _import_runner():
    spec = importlib.util.spec_from_file_location("phase3v_runner_test", _RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_result():
    if not os.path.isfile(_RESULT):
        raise _Skip("result JSON not present; run the runner first")
    return json.loads(_read(_RESULT))


def _rows(basename):
    p = os.path.join(_V_DIR, basename)
    if not os.path.isfile(p):
        raise _Skip("%s not present; run the runner first" % basename)
    return _read_csv_rows(p)


# --------------------------------------------------------------------------- #
# 1. Runner compiles, imports, defines required functions
# --------------------------------------------------------------------------- #
def test_runner_compiles():
    compile(_read(_RUNNER), _RUNNER, "exec")


def test_runner_imports():
    _import_runner()


def test_required_functions_defined():
    tree = ast.parse(_read(_RUNNER))
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for fn in _REQUIRED_FUNCTIONS:
        assert fn in defined, "runner must define function %r" % fn


# --------------------------------------------------------------------------- #
# 2. Outputs under research/output (C:), never the data drive / Phase 3-M / Phase 3-U
# --------------------------------------------------------------------------- #
def test_outputs_under_research_output():
    mod = _import_runner()
    assert mod._V_DIR.replace("\\", "/").endswith(
        "research/output/phase3v_global_etf_price_data_pack")
    assert mod.RESULT_JSON.replace("\\", "/").endswith(
        "research/output/phase3v_global_etf_price_data_pack.json")
    assert mod._STOOQ_INPUT_DIR.replace("\\", "/").endswith(
        "research/input/global_assets/stooq")


def test_no_data_drive_phase3m_or_phase3u_writes_in_source():
    """Static guarantee: every write-mode open is the generic helper, and no write-helper call
    targets a Phase 3-M / Phase 3-U path or the data drive."""
    src = _read(_RUNNER)
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "open(" in line and '"w"' in line:
            ok = ("open(path," in line or "open(local_file," in line)
            assert ok, "write-mode open must be a known local helper form: %r" % line
        if "_write_csv(" in line or "_dump_json(" in line:
            low = line.lower()
            assert "phase3m" not in low, "write helper must not target a Phase 3-M path: %r" % line
            assert "phase3u" not in low, "write helper must not target a Phase 3-U path: %r" % line
            assert "_m_dir" not in low and "_u_dir" not in low, \
                "write helper must not target Phase 3-M/3-U dir: %r" % line
            assert "d:" not in low and "data_drive" not in low, \
                "write helper must not target the data drive: %r" % line


# --------------------------------------------------------------------------- #
# 3. No forbidden tokens / imports / non-Stooq domains; Stooq domain present
# --------------------------------------------------------------------------- #
def test_no_forbidden_tokens():
    src = _read(_RUNNER).lower()
    for token in (_FORBIDDEN_NETWORK_TOKENS + _FORBIDDEN_INFRA_TOKENS + _FORBIDDEN_DB_TOKENS +
                  _FORBIDDEN_DEPLOY_TRADE_TOKENS + _FORBIDDEN_ARTIFACT_TOKENS):
        assert token.lower() not in src, "forbidden token present: %r" % token


def test_no_forbidden_imports():
    tree = ast.parse(_read(_RUNNER))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                roots.add(n.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    bad = roots & _FORBIDDEN_IMPORT_ROOTS
    assert not bad, "forbidden imports present: %s" % sorted(bad)


def test_only_stooq_domain_referenced():
    src = _read(_RUNNER).lower()
    assert "stooq.com" in src, "runner must reference the Stooq domain"
    for dom in _FORBIDDEN_DOMAINS:
        assert dom.lower() not in src, "non-Stooq network domain present: %r" % dom


# --------------------------------------------------------------------------- #
# 4. Result JSON exists with phase == "3-V" and required fields
# --------------------------------------------------------------------------- #
def test_result_json_exists_and_phase():
    res = _load_result()
    assert res["phase"] == "3-V"
    for field in _REQUIRED_JSON_FIELDS:
        assert field in res, "missing result field %r" % field


def test_recommendation_and_next_phase():
    res = _load_result()
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "3-W"


def test_safety_flags():
    res = _load_result()
    for flag in _REQUIRED_FALSE:
        assert res.get(flag) is False, "%s must be False" % flag
    for flag in _REQUIRED_TRUE:
        assert res.get(flag) is True, "%s must be True" % flag
    # stooq_called is True only if downloads were attempted.
    assert isinstance(res.get("stooq_called"), bool)


def test_target_count_at_least_20():
    res = _load_result()
    assert int(res["target_count"]) >= 20, "target count must be >= 20"


def test_nothing_faked_in_result():
    res = _load_result()
    assert res["data_faked"] is False
    assert res["recommendation"]["price_data_faked"] is False
    interp = res["interpretation"]
    assert interp["price_data_faked"] is False
    assert interp["non_stooq_network_called"] is False
    assert interp["production_predictions_computed"] is False
    assert interp["portfolio_weights_computed"] is False


def test_ready_rule_consistency():
    res = _load_result()
    usable = int(res["usable_count"])
    classes = int(res["asset_classes_covered"])
    rec = res["recommendation"]["recommendation"]
    if usable >= 12 and classes >= 5:
        assert rec == "GLOBAL_ETF_PRICE_DATA_READY"
    elif usable >= 5:
        assert rec == "GLOBAL_ETF_PRICE_DATA_PARTIAL_DOWNLOAD"
    else:
        assert rec == "GLOBAL_ETF_PRICE_DATA_BLOCKED_DOWNLOAD_FAILURE"


# --------------------------------------------------------------------------- #
# 5. Output artifacts exist, Git-safe, no deployable artifacts
# --------------------------------------------------------------------------- #
def test_output_artifacts_exist():
    if not os.path.isdir(_V_DIR):
        raise _Skip("output dir not present; run the runner first")
    for base in _EXPECTED_OUTPUT_BASENAMES:
        p = os.path.join(_V_DIR, base)
        assert os.path.isfile(p), "missing output artifact %s" % base


def test_output_files_git_safe_under_50mb():
    if not os.path.isdir(_V_DIR):
        raise _Skip("output dir not present; run the runner first")
    for name in os.listdir(_V_DIR):
        p = os.path.join(_V_DIR, name)
        if os.path.isfile(p):
            assert os.path.getsize(p) <= _MAX_FILE_BYTES, "%s exceeds 50 MB" % name
    if os.path.isfile(_RESULT):
        assert os.path.getsize(_RESULT) <= _MAX_FILE_BYTES


def test_no_deployable_artifact_files_written():
    if not os.path.isdir(_V_DIR):
        raise _Skip("output dir not present; run the runner first")
    bad_ext = (".pk" + "l", ".job" + "lib", ".onn" + "x", ".h5", ".ker" + "as", ".pt", ".pth")
    for name in os.listdir(_V_DIR):
        assert not name.lower().endswith(bad_ext), "deployable artifact present: %s" % name


# --------------------------------------------------------------------------- #
# 6. Output content
# --------------------------------------------------------------------------- #
def test_manifest_columns_and_stooq_domain_only():
    rows = _rows("download_manifest.csv")
    assert len(rows) >= 20, "manifest must cover the full target universe"
    cols = set(rows[0].keys())
    for c in ("ticker", "stooq_symbol", "url_domain", "attempted", "downloaded", "local_file",
              "row_count", "date_min", "date_max", "error"):
        assert c in cols, "manifest missing column %r" % c
    for r in rows:
        assert r["url_domain"] == "stooq.com", "manifest url_domain must be stooq.com"
        assert r["stooq_symbol"].endswith(".us"), "stooq symbol must end with .us"


def test_panel_columns():
    rows = _rows("global_etf_price_panel.csv")
    cols = set(rows[0].keys()) if rows else set()
    if rows:
        for c in ("ticker", "date", "open", "high", "low", "close", "adjusted_close", "volume",
                  "source"):
            assert c in cols, "panel missing column %r" % c
        for r in rows[:50]:
            assert r["source"] == "stooq", "panel rows must be sourced from stooq"
            # adjusted_close mirrors close (Stooq daily CSV has no adjusted column).
            assert r["adjusted_close"] == r["close"]


def test_coverage_columns():
    rows = _rows("global_etf_price_coverage.csv")
    assert len(rows) >= 20
    cols = set(rows[0].keys())
    for c in ("ticker", "asset_class", "region", "downloaded", "row_count", "date_min", "date_max",
              "has_2016_start", "coverage_status", "blocker"):
        assert c in cols, "coverage missing column %r" % c


def test_quality_columns():
    rows = _rows("global_etf_price_quality.csv")
    assert len(rows) >= 20
    cols = set(rows[0].keys())
    for c in ("ticker", "duplicate_date_count", "missing_close_count", "nonpositive_close_count",
              "missing_volume_count", "quality_status"):
        assert c in cols, "quality missing column %r" % c


def test_feature_readiness_columns():
    rows = _rows("cross_asset_feature_readiness.csv")
    assert rows
    cols = set(rows[0].keys())
    for c in ("feature_family", "required_tickers", "available_tickers", "missing_tickers",
              "ready", "blocker"):
        assert c in cols, "feature readiness missing column %r" % c
    families = {r["feature_family"] for r in rows}
    for required in ("equity_risk_appetite", "global_equity_breadth", "rates_duration_regime",
                     "credit_risk", "commodity_inflation", "dollar_liquidity", "volatility_risk",
                     "cross_asset_regime"):
        assert required in families, "feature readiness missing family %r" % required


def test_decision_table_columns():
    rows = _rows("readiness_decision_table.csv")
    assert rows
    cols = set(rows[0].keys())
    for c in ("decision_item", "value", "passed", "note"):
        assert c in cols, "decision table missing column %r" % c
    items = {r["decision_item"] for r in rows}
    assert "no_price_data_faked" in items
    assert "non_stooq_network_called" in items
    assert "phase3m_outputs_modified" in items
    assert "phase3u_outputs_modified" in items


# --------------------------------------------------------------------------- #
# 7. Documentation guardrail phrases
# --------------------------------------------------------------------------- #
_REQUIRED_DOC_PHRASES = [
    "does not deploy",
    "does not run migrations",
    "does not write to production DB",
    "does not trade",
    "production edge",
    "Stooq",
    "research-only",
    "adjusted_close",
]


def test_doc_required_phrases():
    if not os.path.isfile(_DOC):
        raise _Skip("doc not present")
    doc = _read(_DOC)
    for phrase in _REQUIRED_DOC_PHRASES:
        assert phrase in doc, "doc missing required phrase %r" % phrase


def test_doc_explains_not_faked():
    if not os.path.isfile(_DOC):
        raise _Skip("doc not present")
    doc = _read(_DOC).lower()
    assert "not faked" in doc or "never faked" in doc or "are not faked" in doc


# --------------------------------------------------------------------------- #
# 8. Fast OFFLINE run proves Phase 3-M and Phase 3-U outputs are not modified
# --------------------------------------------------------------------------- #
def _snapshot(dir_path):
    snap = {}
    if not os.path.isdir(dir_path):
        return snap
    for dp, _dn, files in os.walk(dir_path):
        for n in files:
            p = os.path.join(dp, n)
            try:
                st = os.stat(p)
                snap[p] = (st.st_size, st.st_mtime_ns)
            except OSError:
                pass
    return snap


def test_offline_run_does_not_modify_phase3m_or_phase3u():
    mod = _import_runner()
    before_m = _snapshot(_M_DIR)
    before_u = _snapshot(_U_DIR)
    tmp = tempfile.mkdtemp(prefix="phase3v_")
    # No network -> zero downloads; proves the gate stays safe and writes only to tmp.
    res = mod.run(result_json_path=os.path.join(tmp, "result.json"), o_dir=tmp,
                  dest_dir=os.path.join(tmp, "stooq"), allow_network=False, verbose=False)
    assert _snapshot(_M_DIR) == before_m, "Phase 3-M outputs must not be modified"
    assert _snapshot(_U_DIR) == before_u, "Phase 3-U outputs must not be modified"
    assert res["phase"] == "3-V"
    assert res["recommended_next_phase"]["phase"] == "3-W"
    assert res["d_drive_written"] is False
    assert res["d_drive_read"] is False
    assert res["non_stooq_network_called"] is False
    assert res["alpha_vantage_called"] is False
    assert res["fred_called"] is False
    assert res["yfinance_called"] is False
    assert res["data_faked"] is False
    assert res["portfolio_weights_computed"] is False
    assert res["production_predictions_computed"] is False
    assert res["production_model_candidate_created"] is False
    assert res["deployable_model_artifact_written"] is False
    assert res["stooq_called"] is False  # network disabled -> not attempted
    # Zero usable downloads -> BLOCKED.
    assert res["recommendation"]["recommendation"] == "GLOBAL_ETF_PRICE_DATA_BLOCKED_DOWNLOAD_FAILURE"


def test_offline_run_ready_rule_with_injected_usable(monkeypatch_free=True):
    """Unit-level proof of the READY rule without network: feed collect_price_pack a fake
    per_ticker via decide()/build_feature_readiness composition is overkill, so assert decide()
    directly."""
    mod = _import_runner()
    assert mod.decide(12, 5) == "GLOBAL_ETF_PRICE_DATA_READY"
    assert mod.decide(11, 5) == "GLOBAL_ETF_PRICE_DATA_PARTIAL_DOWNLOAD"
    assert mod.decide(12, 4) == "GLOBAL_ETF_PRICE_DATA_PARTIAL_DOWNLOAD"
    assert mod.decide(5, 2) == "GLOBAL_ETF_PRICE_DATA_PARTIAL_DOWNLOAD"
    assert mod.decide(4, 4) == "GLOBAL_ETF_PRICE_DATA_BLOCKED_DOWNLOAD_FAILURE"


# --------------------------------------------------------------------------- #
# 9. Optional LIVE Stooq run (gated by PHASE3V_LIVE=1)
# --------------------------------------------------------------------------- #
def test_live_stooq_run_optional():
    if os.environ.get("PHASE3V_LIVE") != "1":
        raise _Skip("set PHASE3V_LIVE=1 to run the full live Stooq download")
    mod = _import_runner()
    before_m = _snapshot(_M_DIR)
    before_u = _snapshot(_U_DIR)
    tmp = tempfile.mkdtemp(prefix="phase3v_live_")
    res = mod.run(result_json_path=os.path.join(tmp, "result.json"), o_dir=tmp,
                  dest_dir=os.path.join(tmp, "stooq"), allow_network=True, verbose=False)
    assert _snapshot(_M_DIR) == before_m
    assert _snapshot(_U_DIR) == before_u
    assert res["phase"] == "3-V"
    assert res["recommended_next_phase"]["phase"] == "3-W"
    assert res["non_stooq_network_called"] is False
    assert res["alpha_vantage_called"] is False
    assert res["yfinance_called"] is False
    assert res["fred_called"] is False
    assert res["d_drive_written"] is False
    assert res["data_faked"] is False
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS


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
