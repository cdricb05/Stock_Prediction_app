"""Phase 3-W tests for the Manual Global ETF Price Data Intake and Repair Gate.

These tests prove the phase is a disciplined, research-only, NETWORK-FREE, NON-faking local manual
data-intake pack: the runner compiles, imports, and defines the required functions; the result JSON
exists with phase == "3-W"; recommended_next_phase.phase == "3-X"; every required output CSV exists;
the manual README + template exist; the source contains no network / Stooq / yfinance / Alpha Vantage
/ FRED / paid-vendor / DB-write / deployment / order-trading / production-candidate /
deployable-artifact tokens; the runner writes nothing to the data drive and never writes a Phase 3-M
/ 3-U / 3-V path; no price data is faked; and every output file is Git-safe (< 50 MB). A fast run on
an empty temp folder proves WAITING_FOR_FILES; a run on temp fixture CSVs proves the parser
normalizes accepted formats; and both prove Phase 3-M / 3-U / 3-V outputs are not modified.

Runs two ways:
  * under pytest:   pytest tests/test_phase3w_manual_global_etf_data_import.py
  * without pytest: python tests/test_phase3w_manual_global_etf_data_import.py
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

_RUNNER = os.path.join(_REPO_ROOT, "research", "run_phase3w_manual_global_etf_data_import.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase3w_manual_global_etf_data_import_v1.md")
_RESULT = os.path.join(_REPO_ROOT, "research", "output",
                       "phase3w_manual_global_etf_data_import.json")
_W_DIR = os.path.join(_REPO_ROOT, "research", "output", "phase3w_manual_global_etf_data_import")
_MANUAL_DIR = os.path.join(_REPO_ROOT, "research", "input", "global_assets", "manual")
_M_DIR = os.path.join(_REPO_ROOT, "research", "output", "phase3m_earnings_estimates_signal_gate")
_U_DIR = os.path.join(_REPO_ROOT, "research", "output",
                      "phase3u_global_asset_universe_readiness")
_V_DIR = os.path.join(_REPO_ROOT, "research", "output", "phase3v_global_etf_price_data_pack")


class _Skip(Exception):
    """Raised to mark a test skipped."""


_EXPECTED_OUTPUT_BASENAMES = (
    "manual_file_inventory.csv",
    "normalized_global_etf_price_panel.csv",
    "global_etf_price_coverage.csv",
    "global_etf_price_quality.csv",
    "missing_or_bad_format_files.csv",
    "manual_download_instructions.csv",
    "cross_asset_feature_readiness.csv",
    "readiness_decision_table.csv",
)

_REQUIRED_FUNCTIONS = (
    "confirm_phase3v", "load_target_universe", "ensure_manual_scaffolding",
    "inventory_manual_files", "parse_manual_file", "normalize_manual_files",
    "build_coverage_quality", "build_feature_readiness", "decide", "run",
)

# Forbidden tokens (assembled from fragments so they never self-match this test's prose). Phase 3-W
# is NETWORK-FREE, so urllib / urlopen / http(s) / sockets / Stooq are all forbidden here too. The
# flag names "network_called" / "stooq_called" / "yfinance_called" remain allowed (these patterns
# are import / attribute / URL / call forms only).
_FORBIDDEN_NETWORK_TOKENS = [
    "url" + "lib", "url" + "open", "http" + "://", "https" + "://", "sock" + "et.",
    "import " + "yf" + "inance", "yf" + "inance.", "alpha" + "vantage", "fin" + "nhub",
    "poly" + "gon.io", "iex" + "cloud", "fred" + "api", "pandas_" + "datareader",
    "stlouis" + "fed", "yahoo", "tiin" + "go", "quan" + "dl", ".download(", "stoo" + "q.com",
    "requests." + "get", "requests." + "post",
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
# Library roots that must NOT be imported (no network libs, no ML libs, no app server).
_FORBIDDEN_IMPORT_ROOTS = {
    "sklearn", "tensorflow", "torch", "keras", "lightgbm", "xgboost",
    "api_server", "yfinance", "requests", "httpx", "pandas_datareader",
    "urllib", "socket", "http",
}
# No data-vendor host (or Stooq) may appear anywhere in the runner.
_FORBIDDEN_DOMAINS = [
    "finance.yahoo", "query1.finance", "alphavantage.co", "stlouisfed.org",
    "finnhub.io", "polygon.io", "iexcloud.io", "tiingo.com", "quandl.com", "stooq.com",
]

_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "phase3v_confirmation", "target_count", "manual_file_count",
    "usable_ticker_count", "missing_ticker_count", "asset_classes_covered",
    "feature_families_ready", "recommendation", "recommended_next_phase",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed", "deployment_executed",
    "model_v2_enabled", "production_edge_claimed", "production_model_trained",
    "production_model_candidate_created", "deployable_model_artifact_written",
    "production_predictions_computed", "production_scores_computed", "portfolio_weights_computed",
    "order_instructions_created", "d_drive_read", "d_drive_written", "provider_api_called",
    "alpha_vantage_called", "fred_called", "stooq_called", "yfinance_called",
    "paid_vendor_api_called", "network_called", "data_faked",
)
_REQUIRED_TRUE = (
    "no_trading", "no_orders", "no_automation", "research_only", "labels_for_validation_only",
)
_ALLOWED_RECOMMENDATIONS = {
    "MANUAL_GLOBAL_ETF_DATA_READY",
    "MANUAL_GLOBAL_ETF_DATA_PARTIAL",
    "MANUAL_GLOBAL_ETF_DATA_WAITING_FOR_FILES",
    "MANUAL_GLOBAL_ETF_DATA_BAD_FORMAT",
}
_MAX_FILE_BYTES = 50 * 1024 * 1024


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _read_csv_rows(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _import_runner():
    spec = importlib.util.spec_from_file_location("phase3w_runner_test", _RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_result():
    if not os.path.isfile(_RESULT):
        raise _Skip("result JSON not present; run the runner first")
    return json.loads(_read(_RESULT))


def _rows(basename):
    p = os.path.join(_W_DIR, basename)
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
# 2. Outputs under research/output (C:), never the data drive / Phase 3-M / 3-U / 3-V
# --------------------------------------------------------------------------- #
def test_outputs_under_research_output():
    mod = _import_runner()
    assert mod._W_DIR.replace("\\", "/").endswith(
        "research/output/phase3w_manual_global_etf_data_import")
    assert mod.RESULT_JSON.replace("\\", "/").endswith(
        "research/output/phase3w_manual_global_etf_data_import.json")
    assert mod._MANUAL_INPUT_DIR.replace("\\", "/").endswith(
        "research/input/global_assets/manual")


def test_no_data_drive_or_prior_phase_writes_in_source():
    """Static guarantee: every write-mode open is a known local helper, and no write-helper call
    targets a Phase 3-M / 3-U / 3-V path or the data drive."""
    src = _read(_RUNNER)
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "open(" in line and '"w"' in line:
            ok = ("open(path," in line)
            assert ok, "write-mode open must be a known local helper form: %r" % line
        if "_write_csv(" in line or "_dump_json(" in line or "_write_text(" in line:
            low = line.lower()
            for bad in ("phase3m", "phase3u", "phase3v", "_m_dir", "_u_dir", "_v_dir",
                        "d:", "data_drive"):
                assert bad not in low, "write helper must not target %r: %r" % (bad, line)


# --------------------------------------------------------------------------- #
# 3. No forbidden tokens / imports / network domains
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


def test_no_network_domains_referenced():
    src = _read(_RUNNER).lower()
    for dom in _FORBIDDEN_DOMAINS:
        assert dom.lower() not in src, "network domain present: %r" % dom


# --------------------------------------------------------------------------- #
# 4. Result JSON exists with phase == "3-W" and required fields
# --------------------------------------------------------------------------- #
def test_result_json_exists_and_phase():
    res = _load_result()
    assert res["phase"] == "3-W"
    for field in _REQUIRED_JSON_FIELDS:
        assert field in res, "missing result field %r" % field


def test_recommendation_and_next_phase():
    res = _load_result()
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "3-X"


def test_safety_flags():
    res = _load_result()
    for flag in _REQUIRED_FALSE:
        assert res.get(flag) is False, "%s must be False" % flag
    for flag in _REQUIRED_TRUE:
        assert res.get(flag) is True, "%s must be True" % flag


def test_target_count_at_least_20():
    res = _load_result()
    assert int(res["target_count"]) >= 20, "target count must be >= 20"


def test_nothing_faked_in_result():
    res = _load_result()
    assert res["data_faked"] is False
    assert res["network_called"] is False
    assert res["recommendation"]["price_data_faked"] is False
    interp = res["interpretation"]
    assert interp["price_data_faked"] is False
    assert interp["network_called"] is False
    assert interp["production_predictions_computed"] is False
    assert interp["portfolio_weights_computed"] is False


def test_ready_rule_consistency():
    res = _load_result()
    manual_files = int(res["manual_file_count"])
    usable = int(res["usable_ticker_count"])
    classes = int(res["asset_classes_covered"])
    rec = res["recommendation"]["recommendation"]
    if manual_files <= 0:
        assert rec == "MANUAL_GLOBAL_ETF_DATA_WAITING_FOR_FILES"
    elif usable >= 12 and classes >= 5:
        assert rec == "MANUAL_GLOBAL_ETF_DATA_READY"
    elif usable >= 5:
        assert rec == "MANUAL_GLOBAL_ETF_DATA_PARTIAL"
    else:
        assert rec == "MANUAL_GLOBAL_ETF_DATA_BAD_FORMAT"


# --------------------------------------------------------------------------- #
# 5. Manual input scaffolding exists; output artifacts exist, Git-safe
# --------------------------------------------------------------------------- #
def test_manual_readme_and_template_exist():
    readme = os.path.join(_MANUAL_DIR, "README.md")
    template = os.path.join(_MANUAL_DIR, "manual_global_etf_price_template.csv")
    if not os.path.isfile(readme) or not os.path.isfile(template):
        raise _Skip("manual scaffolding not present; run the runner first")
    assert "manual" in _read(readme).lower()
    tmpl = _read(template)
    assert "SAMPLE" in tmpl, "template must use clearly-labelled SAMPLE rows"
    header = tmpl.splitlines()[0].lower()
    for c in ("ticker", "date", "close", "adjusted_close", "volume"):
        assert c in header, "template header missing %r" % c


def test_output_artifacts_exist():
    if not os.path.isdir(_W_DIR):
        raise _Skip("output dir not present; run the runner first")
    for base in _EXPECTED_OUTPUT_BASENAMES:
        p = os.path.join(_W_DIR, base)
        assert os.path.isfile(p), "missing output artifact %s" % base


def test_output_files_git_safe_under_50mb():
    if not os.path.isdir(_W_DIR):
        raise _Skip("output dir not present; run the runner first")
    for name in os.listdir(_W_DIR):
        p = os.path.join(_W_DIR, name)
        if os.path.isfile(p):
            assert os.path.getsize(p) <= _MAX_FILE_BYTES, "%s exceeds 50 MB" % name
    if os.path.isfile(_RESULT):
        assert os.path.getsize(_RESULT) <= _MAX_FILE_BYTES


def test_no_deployable_artifact_files_written():
    if not os.path.isdir(_W_DIR):
        raise _Skip("output dir not present; run the runner first")
    bad_ext = (".pk" + "l", ".job" + "lib", ".onn" + "x", ".h5", ".ker" + "as", ".pt", ".pth")
    for name in os.listdir(_W_DIR):
        assert not name.lower().endswith(bad_ext), "deployable artifact present: %s" % name


# --------------------------------------------------------------------------- #
# 6. Output content / columns
# --------------------------------------------------------------------------- #
def test_inventory_columns():
    rows = _rows("manual_file_inventory.csv")
    # header always present even with zero manual files; rows may be empty.
    with open(os.path.join(_W_DIR, "manual_file_inventory.csv"), "r", encoding="utf-8") as f:
        header = f.readline().strip().split(",")
    for c in ("file_path", "detected_ticker", "detected_format", "columns", "row_count",
              "date_min", "date_max", "usable", "blocker"):
        assert c in header, "inventory missing column %r" % c


def test_coverage_columns():
    rows = _rows("global_etf_price_coverage.csv")
    assert len(rows) >= 20
    cols = set(rows[0].keys())
    for c in ("ticker", "asset_class", "region", "locally_available", "row_count", "date_min",
              "date_max", "has_2016_start", "coverage_status", "blocker"):
        assert c in cols, "coverage missing column %r" % c


def test_quality_columns():
    rows = _rows("global_etf_price_quality.csv")
    assert len(rows) >= 20
    cols = set(rows[0].keys())
    for c in ("ticker", "duplicate_date_count", "missing_adjusted_close_count",
              "nonpositive_adjusted_close_count", "missing_volume_count", "quality_status"):
        assert c in cols, "quality missing column %r" % c


def test_instructions_columns_and_no_paid_api():
    rows = _rows("manual_download_instructions.csv")
    assert len(rows) >= 20
    cols = set(rows[0].keys())
    for c in ("ticker", "asset_class", "target_filename", "required_columns", "acceptable_sources",
              "notes"):
        assert c in cols, "instructions missing column %r" % c
    blob = " ".join(" ".join(r.values()) for r in rows).lower()
    for forbidden in ("api key", "alpha" + "vantage", "automation", "yf" + "inance"):
        assert forbidden not in blob, "instructions must not recommend %r" % forbidden


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


def test_missing_columns():
    _rows("missing_or_bad_format_files.csv")  # may be empty; just must exist with header
    with open(os.path.join(_W_DIR, "missing_or_bad_format_files.csv"), "r", encoding="utf-8") as f:
        header = f.readline().strip().split(",")
    for c in ("ticker_or_file", "issue_type", "reason", "next_action"):
        assert c in header, "missing table missing column %r" % c


def test_decision_table_columns():
    rows = _rows("readiness_decision_table.csv")
    assert rows
    cols = set(rows[0].keys())
    for c in ("decision_item", "value", "passed", "note"):
        assert c in cols, "decision table missing column %r" % c
    items = {r["decision_item"] for r in rows}
    assert "network_called" in items
    assert "no_price_data_faked" in items
    assert "phase3v_outputs_modified" in items


# --------------------------------------------------------------------------- #
# 7. Documentation guardrail phrases
# --------------------------------------------------------------------------- #
_REQUIRED_DOC_PHRASES = [
    "does not deploy",
    "does not run migrations",
    "does not write to production DB",
    "does not trade",
    "no network",
    "production edge",
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
# 8. Fast run on EMPTY temp folder proves WAITING_FOR_FILES and prior phases untouched
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


def test_waiting_when_no_manual_files():
    mod = _import_runner()
    before_m = _snapshot(_M_DIR)
    before_u = _snapshot(_U_DIR)
    before_v = _snapshot(_V_DIR)
    tmp = tempfile.mkdtemp(prefix="phase3w_")
    manual = os.path.join(tmp, "manual")
    res = mod.run(result_json_path=os.path.join(tmp, "result.json"), o_dir=tmp,
                  manual_dir=manual, create_scaffolding=True, verbose=False)
    # Scaffolding (README + template) is ignored by the scan -> zero manual files.
    assert res["manual_file_count"] == 0
    assert res["recommendation"]["recommendation"] == "MANUAL_GLOBAL_ETF_DATA_WAITING_FOR_FILES"
    assert res["recommended_next_phase"]["phase"] == "3-X"
    assert res["phase"] == "3-W"
    assert res["network_called"] is False
    assert res["data_faked"] is False
    assert res["d_drive_written"] is False
    assert res["d_drive_read"] is False
    assert _snapshot(_M_DIR) == before_m, "Phase 3-M outputs must not be modified"
    assert _snapshot(_U_DIR) == before_u, "Phase 3-U outputs must not be modified"
    assert _snapshot(_V_DIR) == before_v, "Phase 3-V outputs must not be modified"
    # README + template were created.
    assert os.path.isfile(os.path.join(manual, "README.md"))
    assert os.path.isfile(os.path.join(manual, "manual_global_etf_price_template.csv"))


# --------------------------------------------------------------------------- #
# 9. Parser normalizes accepted formats from temp fixture CSVs (test data only)
# --------------------------------------------------------------------------- #
def _synthetic_rows(n, start_year=2016):
    """Generate n daily-ish rows as TEST FIXTURE data (clearly synthetic, never written into the
    repo's real manual folder). Returns list of (date, o, h, l, c, v)."""
    import datetime as _dt
    out, d, price = [], _dt.date(start_year, 1, 4), 100.0
    while len(out) < n:
        if d.weekday() < 5:
            o = round(price, 2)
            c = round(price * 1.001, 2)
            out.append((d.isoformat(), o, round(o * 1.01, 2), round(o * 0.99, 2), c, 1000000))
            price = c
        d += _dt.timedelta(days=1)
    return out


def test_parser_normalizes_fixture_formats():
    mod = _import_runner()
    tmp = tempfile.mkdtemp(prefix="phase3w_fix_")
    manual = os.path.join(tmp, "manual")
    os.makedirs(manual, exist_ok=True)
    rows = _synthetic_rows(300)

    # Variant 1: per-ticker, Title-case with Adj Close (filename gives ticker).
    with open(os.path.join(manual, "SPY.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"])
        for (d, o, h, l, c, v) in rows:
            w.writerow([d, o, h, l, c, c, v])

    # Variant 4: combined file, ticker + adjusted_* (no plain close).
    with open(os.path.join(manual, "global_etf_prices.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "date", "adjusted_open", "adjusted_high", "adjusted_low",
                    "adjusted_close", "volume"])
        for tk in ("QQQ", "TLT"):
            for (d, o, h, l, c, v) in rows:
                w.writerow([tk, d, o, h, l, c, v])

    inv, parsed = mod.inventory_manual_files(manual)
    assert len(inv) == 2, "two non-template files expected"
    panel, by_ticker = mod.normalize_manual_files(parsed)
    assert set(["SPY", "QQQ", "TLT"]).issubset(set(by_ticker)), "all fixture tickers normalized"
    for tk in ("SPY", "QQQ", "TLT"):
        assert by_ticker[tk]["row_count"] == 300, "%s should have 300 normalized rows" % tk
    # adjusted_close mirrors close in the variant-4 (no plain close) rows -> close filled in.
    for r in panel[:25]:
        assert r["adjusted_close"] is not None
        assert r["close"] is not None
        assert r["source_file"] and r["source_type"] in ("per_ticker", "combined")

    # Full run on the fixtures -> READY (3 tickers across 3 classes is < 12/5, so PARTIAL/BAD).
    res = mod.run(result_json_path=os.path.join(tmp, "result.json"),
                  o_dir=os.path.join(tmp, "out"), manual_dir=manual,
                  create_scaffolding=False, verbose=False)
    assert res["manual_file_count"] == 2
    assert res["usable_ticker_count"] == 3
    # 3 usable (< 5) with files present -> BAD_FORMAT per the decision rule.
    assert res["recommendation"]["recommendation"] == "MANUAL_GLOBAL_ETF_DATA_BAD_FORMAT"
    assert res["recommended_next_phase"]["phase"] == "3-X"


def test_decide_rule_unit():
    mod = _import_runner()
    assert mod.decide(0, 0, 0) == "MANUAL_GLOBAL_ETF_DATA_WAITING_FOR_FILES"
    assert mod.decide(0, 99, 9) == "MANUAL_GLOBAL_ETF_DATA_WAITING_FOR_FILES"
    assert mod.decide(20, 12, 5) == "MANUAL_GLOBAL_ETF_DATA_READY"
    assert mod.decide(20, 11, 5) == "MANUAL_GLOBAL_ETF_DATA_PARTIAL"
    assert mod.decide(20, 12, 4) == "MANUAL_GLOBAL_ETF_DATA_PARTIAL"
    assert mod.decide(20, 5, 2) == "MANUAL_GLOBAL_ETF_DATA_PARTIAL"
    assert mod.decide(3, 4, 4) == "MANUAL_GLOBAL_ETF_DATA_BAD_FORMAT"


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
