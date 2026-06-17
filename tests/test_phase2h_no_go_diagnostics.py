"""Phase 2H-A — tests for the NO_GO diagnostic analyzer.

These tests prove the analyzer is observational and read-only: it compiles and
imports without side effects; it reads only the three local Phase 2G artifacts;
it writes only research/output/phase2h_no_go_diagnostics.json; it never imports
api_server or Paper Trader; its source contains no deploy / gcloud / SSH /
service-restart logic and no DB-write / migration logic; the diagnostics JSON it
produces carries every required field; and the companion doc contains the
required guardrail phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase2h_no_go_diagnostics.py
  * without pytest: python tests/test_phase2h_no_go_diagnostics.py
The __main__ block discovers every test_* function, runs it, prints PASS/FAIL,
and exits non-zero on any failure (the GCP venv has no pytest).
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_ANALYZER = os.path.join(_REPO_ROOT, "research", "analyze_phase2g_no_go.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase2h_no_go_diagnostics_v1.md")
_OUTPUT_JSON = os.path.join(
    _REPO_ROOT, "research", "output", "phase2h_no_go_diagnostics.json")

# Forbidden source tokens: the analyzer must not deploy, shell out, touch a DB,
# or run migrations. Assembled from fragments so this test file does not itself
# trivially contain them as contiguous literals.
_FORBIDDEN_TOKENS = [
    "gcl" + "oud",
    "sub" + "process",
    "os." + "system",
    "para" + "miko",
    "system" + "ctl",
    "stock-api." + "service",
    "alem" + "bic",
    "create" + " table",
    "drop" + " table",
    "alter" + " table",
    "insert" + " into",
    "delete" + " from",
    "trun" + "cate",
    "place" + "_order",
    "submit" + "_order",
    "deploy" + "(",
]

# Guardrail phrases the doc must contain verbatim.
_REQUIRED_DOC_PHRASES = [
    "does not deploy",
    "does not restart stock-api.service",
    "does not enable",
    "does not run migrations",
    "does not write to production DB",
    "does not trade",
    "production edge",
]

# Required top-level fields of the diagnostics JSON.
_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read",
    "go_no_go", "safe_for_canary",
    "overall", "rank_ic_by_year", "rank_ic_by_quarter",
    "top_decile_excess_return_by_year", "hit_rate_by_year",
    "score_buckets", "ticker_behavior",
    "root_causes", "recommended_experiments",
)
_REQUIRED_OVERALL_FIELDS = (
    "reported_rank_ic", "recomputed_rank_ic", "rank_ic_floor",
    "top_decile_hit_rate", "bucket_monotonic", "n_dates", "n_tickers",
    "legacy_comparison_available",
)
_REQUIRED_ROOT_CAUSE_FLAGS = (
    "weak_rank_signal", "non_monotonic_buckets", "top_decile_weak",
    "insufficient_sample", "missing_legacy_comparison",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_edge_claimed",
)
_REQUIRED_TRUE = ("no_trading", "no_orders", "no_automation")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _import_analyzer():
    spec = importlib.util.spec_from_file_location("phase2h_analyzer", _ANALYZER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# 1. Analyzer compiles
# --------------------------------------------------------------------------- #
def test_analyzer_compiles():
    compile(_read(_ANALYZER), _ANALYZER, "exec")


# --------------------------------------------------------------------------- #
# 2. Reads only the three local Phase 2G artifacts
# --------------------------------------------------------------------------- #
def test_reads_only_local_phase2g_files():
    mod = _import_analyzer()
    for attr, leaf in (
            ("VALIDATION_JSON", "phase2g_real_data_validation.json"),
            ("SCORED_CSV", "phase2g_real_data_scored.csv"),
            ("RUN_SUMMARY_JSON", "phase2g_c_real_data_run_summary.json")):
        val = getattr(mod, attr)
        assert val.endswith(leaf), f"{attr} must point at {leaf}"
        assert val.replace("\\", "/").startswith("research/output/")
    # No network / URL fetches in source.
    text = _read(_ANALYZER).lower()
    for tok in ("http://", "https://", "requests.", "urllib", "yfinance",
                "socket"):
        assert tok not in text, f"analyzer must not reach the network: {tok!r}"


# --------------------------------------------------------------------------- #
# 3. Writes only research/output/phase2h_no_go_diagnostics.json
# --------------------------------------------------------------------------- #
def test_writes_only_diagnostics_json():
    mod = _import_analyzer()
    assert mod.DIAGNOSTICS_JSON.replace("\\", "/").endswith(
        "research/output/phase2h_no_go_diagnostics.json")
    # The only write sinks in source are json.dump / makedirs — no CSV/DB write.
    text = _read(_ANALYZER)
    assert "to_csv" not in text, "analyzer must not write any CSV"
    assert "to_sql" not in text, "analyzer must not write to a database"
    assert text.count("open(") >= 1
    # Statically, every open(..., "w"...) target is the diagnostics path var.
    tree = ast.parse(text)
    write_opens = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
                    and "w" in str(node.args[1].value):
                write_opens += 1
    assert write_opens == 1, "exactly one write-open (the diagnostics JSON)"


# --------------------------------------------------------------------------- #
# 4. No api_server import
# --------------------------------------------------------------------------- #
def test_no_api_server_import():
    text = _read(_ANALYZER)
    assert "import api_server" not in text
    assert "from api_server" not in text
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = ([n.name for n in node.names]
                    + ([node.module] if isinstance(node, ast.ImportFrom)
                       and node.module else []))
            assert not any((m or "").split(".")[0] == "api_server" for m in mods)


# --------------------------------------------------------------------------- #
# 5. No Paper Trader import
# --------------------------------------------------------------------------- #
def test_no_paper_trader_import():
    assert "paper_trader" not in _read(_ANALYZER).lower()
    _import_analyzer()
    offenders = [m for m in sys.modules
                 if m == "paper_trader" or m.startswith("paper_trader.")]
    assert not offenders, f"unexpected Paper Trader import: {offenders}"


# --------------------------------------------------------------------------- #
# 6. No deploy / gcloud / SSH / service-restart / DB-write / migration logic
# --------------------------------------------------------------------------- #
def test_no_forbidden_infrastructure_logic():
    text = _read(_ANALYZER).lower()
    hits = [tok for tok in _FORBIDDEN_TOKENS if tok in text]
    assert not hits, f"analyzer contains forbidden token(s): {hits}"
    for tok in ("ssh ", "scp ", "uvicorn", "systemd", "restart"):
        assert tok not in text, f"analyzer contains forbidden token: {tok!r}"


# --------------------------------------------------------------------------- #
# 7. Output JSON has all required fields (run the analyzer to a temp path)
# --------------------------------------------------------------------------- #
def test_output_json_required_fields():
    mod = _import_analyzer()
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "diag.json")
        diag = mod.run(output_path=out)
        on_disk = json.loads(_read(out))
    for d in (diag, on_disk):
        for k in _REQUIRED_JSON_FIELDS:
            assert k in d, f"diagnostics missing field: {k}"
        assert d["phase"] == "2H-A"
        for k in _REQUIRED_FALSE:
            assert d[k] is False, f"{k} must be false"
        for k in _REQUIRED_TRUE:
            assert d[k] is True, f"{k} must be true"
        for k in _REQUIRED_OVERALL_FIELDS:
            assert k in d["overall"], f"overall missing field: {k}"
        for k in _REQUIRED_ROOT_CAUSE_FLAGS:
            assert k in d["root_causes"]["flags"], f"missing root-cause: {k}"
        assert d["root_causes"]["failure_category"] in (
            "data_issue", "signal_issue", "gate_issue")
        assert isinstance(d["rank_ic_by_year"], list) and d["rank_ic_by_year"]
        assert isinstance(d["recommended_experiments"], list) \
            and d["recommended_experiments"]
        assert "best_by_rank_ic" in d["ticker_behavior"]
        assert "worst_by_rank_ic" in d["ticker_behavior"]


# --------------------------------------------------------------------------- #
# 8. Diagnostics correctly classify this run's NO_GO drivers
# --------------------------------------------------------------------------- #
def test_root_cause_matches_no_go():
    mod = _import_analyzer()
    with tempfile.TemporaryDirectory() as tmp:
        diag = mod.run(output_path=os.path.join(tmp, "diag.json"))
    flags = diag["root_causes"]["flags"]
    # This window: rank IC at noise, non-monotone buckets, no legacy baseline.
    assert flags["weak_rank_signal"] is True
    assert flags["non_monotonic_buckets"] is True
    assert flags["missing_legacy_comparison"] is True
    # The sample is adequate (854 dates, 40 tickers) — not a data-size failure.
    assert flags["insufficient_sample"] is False
    assert diag["go_no_go"] == "NO_GO"
    assert diag["safe_for_canary"] is False
    assert diag["production_edge_claimed"] is False


# --------------------------------------------------------------------------- #
# 9. Doc has all required guardrail phrases
# --------------------------------------------------------------------------- #
def test_doc_has_required_phrases():
    text = _read(_DOC)
    missing = [p for p in _REQUIRED_DOC_PHRASES if p not in text]
    assert not missing, f"doc missing required phrase(s): {missing}"


# --------------------------------------------------------------------------- #
# Self-running harness (no pytest required)
# --------------------------------------------------------------------------- #
def _run_all():
    tests = sorted((n, f) for n, f in globals().items()
                   if n.startswith("test_") and callable(f))
    passed = failed = 0
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"FAIL {name}: {e}")
            failures.append((name, traceback.format_exc()))
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {len(tests)} total")
    if failures:
        print("\n--- failure details ---")
        for name, tb in failures:
            print(f"\n### {name}\n{tb}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
