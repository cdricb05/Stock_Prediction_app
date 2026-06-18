"""Phase 2K-P tests for the Sector-Relative Feature Feasibility analyzer.

These tests prove the analyzer is a disciplined feasibility phase: it compiles and imports
cleanly; it references the expected small input summaries (Phase 2K-O / 2K-N / 2K-H) and the
expanded D: dataset paths; it reads the Phase 2K-O result and confirms it routed here
(PROCEED_TO_SECTOR_RELATIVE_FEASIBILITY -> 2K-P); it extracts exactly the 3 reconfirmed leads;
it reads the large D: price-history CSV READ-ONLY and ONLY for the ticker universe / date
coverage (ticker / date columns), computing no alpha signal, no label, and no retest; it runs
no broad alpha screen and no sector-relative retest; it fetches nothing from the network (no
yfinance); it validates the optional sector-map schema and generates a one-row-per-equity
template when no local map exists; it emits only the allowed feasibility recommendations and
routes to Phase 2K-Q; it never trains / fits / scores a model; it writes nothing to the D:
drive (only two small C: outputs); it imports neither api_server nor Paper Trader; it carries
every required output field and safety flag; and the doc carries the required guardrail
phrases.

The committed results artifact reflects the real D: universe (128 equity tickers, SPY excluded
as benchmark) and the SECTOR_MAP_TEMPLATE_CREATED outcome. A live test runs the analyzer
against the committed summaries + D: CSV in a temp dir and re-checks the verdict; it skips when
the D: CSV is absent or pandas is unavailable.

Runs two ways:
  * under pytest:   pytest tests/test_phase2k_p_sector_relative_feasibility.py
  * without pytest: python tests/test_phase2k_p_sector_relative_feasibility.py
The __main__ block discovers every test_* function, runs it, prints PASS/FAIL/SKIP, and exits
non-zero on any failure (the GCP venv has no pytest).
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

_ANALYZER = os.path.join(
    _REPO_ROOT, "research", "analyze_phase2k_p_sector_relative_feasibility.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase2k_p_sector_relative_feasibility_v1.md")
_COMMITTED_RESULTS = os.path.join(
    _REPO_ROOT, "research", "output", "phase2k_p_sector_relative_feasibility.json")
_COMMITTED_TEMPLATE = os.path.join(
    _REPO_ROOT, "research", "output", "phase2k_p_sector_map_template.csv")
_PHASE2K_O_JSON = os.path.join(
    _REPO_ROOT, "research", "output", "phase2k_o_reconfirmed_lead_decision_gate.json")

# The 3 reconfirmed lead ids expected from Phase 2K-N (and ONLY these).
_RECONFIRMED_LEAD_IDS = {
    "residual_price_momentum_12_1@5d",
    "short_horizon_residual_reversal_5d@21d",
    "short_horizon_residual_reversal_21d@21d",
}


class _Skip(Exception):
    """Raised to mark a test skipped (D: CSV absent or pandas unavailable)."""


# The small read-only upstream summaries this phase references.
_EXPECTED_INPUT_BASENAMES = (
    "phase2k_o_reconfirmed_lead_decision_gate.json",
    "phase2k_n_narrow_model_free_retest.json",
    "phase2k_h_manual_build_run_summary.json",
)
# The expanded D: dataset paths this phase references (read-only).
_EXPECTED_D_BASENAMES = (
    "phase2k_g_expanded_price_history_free.csv",
    "phase2k_g_data_quality_report.json",
    "phase2k_g_data_build_summary.json",
    "phase2k_g_survivorship_caveat.json",
)
_SECTOR_MAP_REQUIRED_COLUMNS = [
    "ticker", "sector", "industry", "source", "as_of_date", "point_in_time", "notes",
]

# Forbidden infrastructure tokens (assembled from fragments) — apply to the analyzer source.
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
    "PREDICTOR_USE_" + "MODEL_V2",
]
_FORBIDDEN_EXTRA = ("ssh ", "scp ", "uvicorn", "systemd", "restart")

# Forbidden model-training / fitting tokens (no model work here at all).
_FORBIDDEN_MODEL_TOKENS = [
    ".fit(", "fit_transform", "sklearn", "tensorflow", "lightgbm", "xgboost",
    "torch", "keras", "LinearRegression", "RandomForest", "MLPRegressor",
    "GradientBoosting",
]

# Forbidden network / acquisition tokens — the analyzer must stay network-free (no yfinance).
_FORBIDDEN_NETWORK_TOKENS = [
    "http://", "https://", "requests.", "urllib", "yf" + "inance", "socket",
    "url" + "open", "down" + "load(", "pur" + "chase(",
]

# Forbidden alpha-signal / label / retest computation tokens — this phase reads the D: CSV ONLY
# for the ticker universe + dates; it computes no signal, no label, and no sector-relative
# retest. (read_csv / pandas ARE allowed here, unlike the 2K-O gate.)
_FORBIDDEN_SIGNAL_TOKENS = [
    "adjusted_close", "adjusted_open", "adjusted_high", "adjusted_low",
    "forward_return", "future_return", "compute_label", "compute_signal",
    "spearman",
]

# Forbidden dataset-mutation / D:-write tokens (the template is written with csv.writer, never
# to the D: drive).
_FORBIDDEN_MUTATION_TOKENS = [
    "os." + "remove", "os." + "rename", "os." + "unlink", "shutil", "rm" + "tree",
    "to_sql", "to_parquet",
]

_REQUIRED_DOC_PHRASES = [
    "does not deploy",
    "does not restart stock-api.service",
    "does not enable",
    "does not run migrations",
    "does not write to production DB",
    "does not trade",
    "production edge",
]

_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read",
    "upstream_phase2k_o_summary", "reconfirmed_leads", "universe_summary",
    "sector_map_input_status", "sector_map_validation", "sector_map_template",
    "feasibility_decision", "recommendation", "interpretation",
    "recommended_next_phase",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_edge_claimed",
    "model_trained", "model_candidate_created", "d_drive_written",
    "broad_alpha_screen_run", "sector_relative_retest_run", "network_used",
)
_REQUIRED_TRUE = ("no_trading", "no_orders", "no_automation", "d_drive_read")

_ALLOWED_RECOMMENDATIONS = {
    "SECTOR_MAP_READY_FOR_CAVEATED_RETEST",
    "SECTOR_MAP_TEMPLATE_CREATED",
    "SECTOR_MAP_INCOMPLETE",
    "REQUIRE_POINT_IN_TIME_SECTOR_DATA",
    "NO_ACTION_FEASIBILITY_BLOCKED",
}


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _import_analyzer():
    spec = importlib.util.spec_from_file_location("phase2k_p_analyzer_test", _ANALYZER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_live(analyzer):
    """Run the analyzer against the committed summaries + D: CSV, writing to a temp dir.

    Skips (raises _Skip) when the D: price-history CSV is absent or pandas is unavailable.
    """
    if not os.path.isfile(analyzer.D_PRICE_HISTORY_CSV):
        raise _Skip("expanded D: price-history CSV not present in this environment")
    try:
        import pandas  # noqa: F401
    except Exception:  # noqa: BLE001
        raise _Skip("pandas not available in this environment")
    tmp = tempfile.mkdtemp(prefix="phase2k_p_")
    out = os.path.join(tmp, "feasibility.json")
    tmpl = os.path.join(tmp, "template.csv")
    res = analyzer.run(output_path=out, template_path=tmpl)
    return res, out, tmpl


# --------------------------------------------------------------------------- #
# 1. Analyzer compiles and imports
# --------------------------------------------------------------------------- #
def test_analyzer_compiles():
    compile(_read(_ANALYZER), _ANALYZER, "exec")


def test_analyzer_imports():
    _import_analyzer()


# --------------------------------------------------------------------------- #
# 2. Expected upstream + D: inputs referenced; output paths are in the C: repo
# --------------------------------------------------------------------------- #
def test_references_expected_inputs():
    analyzer = _import_analyzer()
    text = _read(_ANALYZER)
    for base in _EXPECTED_INPUT_BASENAMES:
        assert base in text, f"analyzer does not reference expected input: {base}"
    for base in _EXPECTED_D_BASENAMES:
        assert base in text, f"analyzer does not reference expected D: path: {base}"
    assert analyzer.RESULTS_JSON.replace("\\", "/").endswith(
        "research/output/phase2k_p_sector_relative_feasibility.json")
    assert analyzer.SECTOR_MAP_TEMPLATE.replace("\\", "/").endswith(
        "research/output/phase2k_p_sector_map_template.csv")
    # Both writable outputs live under the C: repo, never the D: drive.
    assert not analyzer.RESULTS_JSON.replace("\\", "/").upper().startswith("D:")
    assert not analyzer.SECTOR_MAP_TEMPLATE.replace("\\", "/").upper().startswith("D:")


# --------------------------------------------------------------------------- #
# 3. No api_server import
# --------------------------------------------------------------------------- #
def test_no_api_server_import():
    text = _read(_ANALYZER)
    assert "import api_server" not in text
    assert "from api_server" not in text
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = ([n.name for n in node.names]
                    + ([node.module] if isinstance(node, ast.ImportFrom)
                       and node.module else []))
            assert not any((m or "").split(".")[0] == "api_server" for m in mods)


# --------------------------------------------------------------------------- #
# 4. No Paper Trader import / reference
# --------------------------------------------------------------------------- #
def test_no_paper_trader_import():
    assert "paper_trader" not in _read(_ANALYZER).lower()


# --------------------------------------------------------------------------- #
# 5. No forbidden infrastructure / model-training tokens
# --------------------------------------------------------------------------- #
def test_no_forbidden_infrastructure_or_model_logic():
    text = _read(_ANALYZER)
    low = text.lower()
    hits = [tok for tok in _FORBIDDEN_TOKENS if tok.lower() in low]
    assert not hits, f"analyzer has forbidden token(s): {hits}"
    for tok in _FORBIDDEN_EXTRA:
        assert tok not in low, f"analyzer has forbidden token: {tok!r}"
    model_hits = [tok for tok in _FORBIDDEN_MODEL_TOKENS if tok in text]
    assert not model_hits, f"analyzer has model-training token(s): {model_hits}"


# --------------------------------------------------------------------------- #
# 6. Network-free, no signal/label compute, no D: write, exactly two write-opens
# --------------------------------------------------------------------------- #
def test_analyzer_is_network_free_and_no_signal_compute():
    text = _read(_ANALYZER)
    low = text.lower()
    net = [tok for tok in _FORBIDDEN_NETWORK_TOKENS if tok.lower() in low]
    assert not net, f"analyzer contains network/acquisition token(s): {net}"
    sig = [tok for tok in _FORBIDDEN_SIGNAL_TOKENS if tok.lower() in low]
    assert not sig, f"analyzer computes alpha signals/labels (forbidden): {sig}"
    mut = [tok for tok in _FORBIDDEN_MUTATION_TOKENS if tok.lower() in low]
    assert not mut, f"analyzer contains dataset-mutation token(s): {mut}"
    # Exactly two write-opens: the results JSON and the sector-map template CSV.
    write_opens = 0
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
                    and "w" in str(node.args[1].value):
                write_opens += 1
    assert write_opens == 2, "analyzer must have exactly two write-opens (JSON + template CSV)"


# --------------------------------------------------------------------------- #
# 7. Reads the D: CSV READ-ONLY and ONLY for universe extraction (ticker/date)
# --------------------------------------------------------------------------- #
def test_d_csv_read_only_for_universe():
    text = _read(_ANALYZER)
    assert text.count("read_csv") == 1, "the D: CSV must be read exactly once"
    assert "usecols" in text, "the D: CSV read must restrict to specific columns"
    assert '"ticker"' in text and '"date"' in text, \
        "the D: CSV read must select only ticker / date columns"


# --------------------------------------------------------------------------- #
# 8. Committed results artifact: required fields, safety flags, structure
# --------------------------------------------------------------------------- #
def test_committed_results_artifact_valid():
    assert os.path.isfile(_COMMITTED_RESULTS), "committed feasibility JSON must exist"
    d = json.loads(_read(_COMMITTED_RESULTS))
    for k in _REQUIRED_JSON_FIELDS:
        assert k in d, f"committed feasibility JSON missing field: {k}"
    assert d["phase"] == "2K-P"
    for k in _REQUIRED_FALSE:
        assert d[k] is False, f"{k} must be false"
    for k in _REQUIRED_TRUE:
        assert d[k] is True, f"{k} must be true"
    rec = d["recommendation"]
    assert rec["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert rec["create_model_candidate_now"] is False
    assert rec["train_model_now"] is False
    assert rec["deploy_now"] is False
    assert rec["run_sector_relative_retest_now"] is False
    assert rec["production_edge_claimed"] is False
    assert d["recommended_next_phase"]["phase"] == "2K-Q"


# --------------------------------------------------------------------------- #
# 9. Reads Phase 2K-O and confirms routing (committed artifact)
# --------------------------------------------------------------------------- #
def test_reads_phase2k_o_and_confirms_routing():
    d = json.loads(_read(_COMMITTED_RESULTS))
    ko = d["upstream_phase2k_o_summary"]
    assert ko["present"] is True
    assert ko["phase"] == "2K-O"
    assert ko["recommendation"] == "PROCEED_TO_SECTOR_RELATIVE_FEASIBILITY"
    assert ko["selected_option"] == "SECTOR_RELATIVE_FEATURE_FEASIBILITY"
    assert ko["recommended_next_phase"]["phase"] == "2K-P"
    assert ko["recommended_next_phase"]["title"] == \
        "Sector-Relative Feature Feasibility for Reconfirmed Leads"
    assert ko["create_model_candidate_now_false"] is True
    assert ko["train_model_now_false"] is True
    assert ko["routing_confirmed"] is True


# --------------------------------------------------------------------------- #
# 10. Exactly 3 reconfirmed leads (the pre-registered set)
# --------------------------------------------------------------------------- #
def test_exactly_three_reconfirmed_leads():
    d = json.loads(_read(_COMMITTED_RESULTS))
    leads = d["reconfirmed_leads"]
    assert len(leads) == 3, "exactly 3 reconfirmed leads required"
    lead_ids = {l["lead_id"] for l in leads}
    assert lead_ids == _RECONFIRMED_LEAD_IDS, f"unexpected reconfirmed set: {lead_ids}"
    for l in leads:
        assert l["status"] == "RESEARCH_LEAD_RECONFIRMED", f"bad status: {l['status']}"


# --------------------------------------------------------------------------- #
# 11. Universe summary: SPY excluded from the equity universe, kept as benchmark
# --------------------------------------------------------------------------- #
def test_universe_excludes_spy_as_benchmark():
    d = json.loads(_read(_COMMITTED_RESULTS))
    uni = d["universe_summary"]
    assert uni["benchmark"] == "SPY"
    assert "SPY" not in uni["equity_universe"], "SPY must be excluded from the equity universe"
    assert uni["ticker_count"] == len(uni["equity_universe"])
    assert uni["ticker_count"] > 0
    assert uni["date_count"] > 0
    assert uni["start_date"] and uni["end_date"]
    assert uni["rows_loaded"] > 0
    assert "expanded D: price history" in uni["universe_source"]


# --------------------------------------------------------------------------- #
# 12. Optional sector-map schema + template generated when no local map exists
# --------------------------------------------------------------------------- #
def test_sector_map_schema_and_template_created():
    d = json.loads(_read(_COMMITTED_RESULTS))
    status = d["sector_map_input_status"]
    assert status["required_columns"] == _SECTOR_MAP_REQUIRED_COLUMNS
    assert status["network_fetch_used"] is False
    tmpl = d["sector_map_template"]
    # The committed artifact reflects the no-local-map case: a template was created.
    if status["present"] is False:
        assert tmpl["generated"] is True
        assert tmpl["columns"] == _SECTOR_MAP_REQUIRED_COLUMNS
        assert tmpl["row_count"] == d["universe_summary"]["ticker_count"]
        assert d["recommendation"]["recommendation"] == "SECTOR_MAP_TEMPLATE_CREATED"
        assert os.path.isfile(_COMMITTED_TEMPLATE), "template CSV must exist when generated"
        header = _read(_COMMITTED_TEMPLATE).splitlines()[0].strip()
        assert header == ",".join(_SECTOR_MAP_REQUIRED_COLUMNS)


# --------------------------------------------------------------------------- #
# 13. No retest / no broad screen / routes to 2K-Q; disallows model work
# --------------------------------------------------------------------------- #
def test_no_retest_and_routes_to_2k_q():
    d = json.loads(_read(_COMMITTED_RESULTS))
    assert d["sector_relative_retest_run"] is False
    assert d["broad_alpha_screen_run"] is False
    assert d["network_used"] is False
    interp = d["interpretation"]
    assert interp["model_trained"] is False
    assert interp["model_candidate_created"] is False
    assert interp["ran_sector_relative_retest"] is False
    assert interp["ran_broad_alpha_screen"] is False
    assert interp["fetched_sector_data_from_network"] is False
    assert interp["wrote_to_d_drive"] is False
    assert d["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert d["recommended_next_phase"]["phase"] == "2K-Q"


# --------------------------------------------------------------------------- #
# 14. Doc has all required guardrail phrases
# --------------------------------------------------------------------------- #
def test_doc_has_required_phrases():
    text = _read(_DOC)
    missing = [p for p in _REQUIRED_DOC_PHRASES if p not in text]
    assert not missing, f"doc missing required phrase(s): {missing}"


# --------------------------------------------------------------------------- #
# 15. Live run (reads committed summaries + D: CSV): structure + verdict
# --------------------------------------------------------------------------- #
def test_live_feasibility_structure():
    analyzer = _import_analyzer()
    res, on_disk, tmpl = _run_live(analyzer)  # may raise _Skip
    blob = json.loads(_read(on_disk))
    for d in (res, blob):
        assert d["phase"] == "2K-P"
        for k in _REQUIRED_JSON_FIELDS:
            assert k in d, f"live feasibility JSON missing field: {k}"
        for k in _REQUIRED_FALSE:
            assert d[k] is False
        for k in _REQUIRED_TRUE:
            assert d[k] is True
        assert {l["lead_id"] for l in d["reconfirmed_leads"]} == _RECONFIRMED_LEAD_IDS
        assert "SPY" not in d["universe_summary"]["equity_universe"]
        assert d["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
        assert d["recommendation"]["run_sector_relative_retest_now"] is False
        assert d["recommended_next_phase"]["phase"] == "2K-Q"
        assert d["upstream_phase2k_o_summary"]["routing_confirmed"] is True
    # When no local sector map exists, the live run also writes the template CSV.
    if blob["sector_map_input_status"]["present"] is False:
        assert os.path.isfile(tmpl), "template CSV must be written when no local map exists"


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
            print(f"PASS {name}")
            passed += 1
        except _Skip as s:
            print(f"SKIP {name}: {s}")
            skipped += 1
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"FAIL {name}: {e}")
            failures.append((name, traceback.format_exc()))
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped, {len(tests)} total")
    if failures:
        print("\n--- failure details ---")
        for name, tb in failures:
            print(f"\n### {name}\n{tb}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
