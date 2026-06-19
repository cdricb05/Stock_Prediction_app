"""Phase 2K-Q tests for the Populate Sector Map analyzer.

These tests prove the analyzer is a disciplined sector-map population phase: it compiles and
imports cleanly; it references the expected small input summaries (Phase 2K-P result + template,
2K-O, 2K-N); it reads the Phase 2K-P result and confirms it routed here
(SECTOR_MAP_TEMPLATE_CREATED -> 2K-Q) with a 128-row template; it populates one row per template
ticker from a curated current-as-of seed and writes the populated map to
research/input/phase2k_p_sector_map_current.csv; it does NOT read the large D: price-history CSV
(no read_csv, no D: paths) and reads / writes nothing on the D: drive; it fetches nothing from
the network (no yfinance); the populated map has exactly 128 rows, no SPY, no duplicate tickers,
100% sector / industry coverage, point_in_time false for every row, and at least 5 sectors; it
emits only the allowed population recommendations and routes to Phase 2K-R; it never trains /
fits / scores a model; it imports neither api_server nor Paper Trader; it carries every required
output field and safety flag; and the doc carries the required guardrail phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase2k_q_populate_sector_map.py
  * without pytest: python tests/test_phase2k_q_populate_sector_map.py
The __main__ block discovers every test_* function, runs it, prints PASS/FAIL/SKIP, and exits
non-zero on any failure (the GCP venv has no pytest).
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

_ANALYZER = os.path.join(_REPO_ROOT, "research", "analyze_phase2k_q_populate_sector_map.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase2k_q_populate_sector_map_v1.md")
_COMMITTED_RESULTS = os.path.join(
    _REPO_ROOT, "research", "output", "phase2k_q_populate_sector_map.json")
_POPULATED_MAP = os.path.join(
    _REPO_ROOT, "research", "input", "phase2k_p_sector_map_current.csv")
_PHASE2K_P_TEMPLATE = os.path.join(
    _REPO_ROOT, "research", "output", "phase2k_p_sector_map_template.csv")


class _Skip(Exception):
    """Raised to mark a test skipped."""


# The small read-only upstream summaries this phase references.
_EXPECTED_INPUT_BASENAMES = (
    "phase2k_p_sector_relative_feasibility.json",
    "phase2k_p_sector_map_template.csv",
    "phase2k_o_reconfirmed_lead_decision_gate.json",
    "phase2k_n_narrow_model_free_retest.json",
)
# This phase must NOT read any expanded D: dataset path.
_FORBIDDEN_D_BASENAMES = (
    "phase2k_g_expanded_price_history_free.csv",
    "phase2k_g_data_quality_report.json",
    "phase2k_g_data_build_summary.json",
    "phase2k_g_survivorship_caveat.json",
)
_SECTOR_MAP_REQUIRED_COLUMNS = [
    "ticker", "sector", "industry", "source", "as_of_date", "point_in_time", "notes",
]
_EXPECTED_ROW_COUNT = 128

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

# Forbidden alpha-signal / label / retest computation tokens.
_FORBIDDEN_SIGNAL_TOKENS = [
    "adjusted_close", "forward_return", "future_return", "compute_label", "compute_signal",
    "spearman", "rank_ic",
]

# Forbidden dataset-mutation / D:-write tokens.
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
    "phase", "generated_at", "inputs_read", "sector_map_output",
    "upstream_phase2k_p_summary", "sector_map_population", "sector_map_validation",
    "sector_distribution", "industry_distribution", "caveats",
    "recommendation", "interpretation", "recommended_next_phase",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_edge_claimed",
    "model_trained", "model_candidate_created", "d_drive_read", "d_drive_written",
    "broad_alpha_screen_run", "sector_relative_retest_run", "network_used",
)
_REQUIRED_TRUE = ("no_trading", "no_orders", "no_automation")

_ALLOWED_RECOMMENDATIONS = {
    "SECTOR_MAP_POPULATED_READY_FOR_CAVEATED_RETEST",
    "SECTOR_MAP_POPULATED_WITH_WARNINGS",
    "SECTOR_MAP_POPULATION_INCOMPLETE",
    "NO_ACTION_POPULATION_BLOCKED",
}


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _read_csv_rows(path: str):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _import_analyzer():
    spec = importlib.util.spec_from_file_location("phase2k_q_analyzer_test", _ANALYZER)
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
# 2. Expected inputs referenced; output paths are in the C: repo; no D: paths
# --------------------------------------------------------------------------- #
def test_references_expected_inputs():
    analyzer = _import_analyzer()
    text = _read(_ANALYZER)
    for base in _EXPECTED_INPUT_BASENAMES:
        assert base in text, f"analyzer does not reference expected input: {base}"
    for base in _FORBIDDEN_D_BASENAMES:
        assert base not in text, f"analyzer must not reference D: path: {base}"
    assert analyzer.RESULTS_JSON.replace("\\", "/").endswith(
        "research/output/phase2k_q_populate_sector_map.json")
    assert analyzer.SECTOR_MAP_OUTPUT.replace("\\", "/").endswith(
        "research/input/phase2k_p_sector_map_current.csv")
    # Both writable outputs live under the C: repo, never the D: drive.
    assert not analyzer.RESULTS_JSON.replace("\\", "/").upper().startswith("D:")
    assert not analyzer.SECTOR_MAP_OUTPUT.replace("\\", "/").upper().startswith("D:")


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
# 6. Network-free, no signal/label compute, no D: read, no D: write, two write-opens
# --------------------------------------------------------------------------- #
def test_analyzer_is_network_free_no_d_read_no_signal():
    text = _read(_ANALYZER)
    low = text.lower()
    net = [tok for tok in _FORBIDDEN_NETWORK_TOKENS if tok.lower() in low]
    assert not net, f"analyzer contains network/acquisition token(s): {net}"
    sig = [tok for tok in _FORBIDDEN_SIGNAL_TOKENS if tok.lower() in low]
    assert not sig, f"analyzer computes alpha signals/labels (forbidden): {sig}"
    mut = [tok for tok in _FORBIDDEN_MUTATION_TOKENS if tok.lower() in low]
    assert not mut, f"analyzer contains dataset-mutation token(s): {mut}"
    # This phase does NOT read the D: price-history CSV at all.
    assert "read_csv" not in text, "phase 2K-Q must not read the D: price-history CSV"
    assert "import pandas" not in text, "phase 2K-Q needs no pandas"
    # Exactly two write-opens: the populated sector-map CSV and the results JSON.
    write_opens = 0
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
                    and "w" in str(node.args[1].value):
                write_opens += 1
    assert write_opens == 2, "analyzer must have exactly two write-opens (map CSV + JSON)"


def test_analyzer_does_not_reference_d_drive():
    # The analyzer may say in prose that it does NOT read the D: drive; what it must not do is
    # construct an actual D: drive path (D:\ or D:/).
    text = _read(_ANALYZER)
    assert "D:\\" not in text, "phase 2K-Q must not reference a D:\\ drive path"
    assert "D:/" not in text, "phase 2K-Q must not reference a D:/ drive path"


# --------------------------------------------------------------------------- #
# 7. Committed results artifact: required fields, safety flags, structure
# --------------------------------------------------------------------------- #
def test_committed_results_artifact_valid():
    assert os.path.isfile(_COMMITTED_RESULTS), "committed results JSON must exist"
    d = json.loads(_read(_COMMITTED_RESULTS))
    for k in _REQUIRED_JSON_FIELDS:
        assert k in d, f"committed results JSON missing field: {k}"
    assert d["phase"] == "2K-Q"
    for k in _REQUIRED_FALSE:
        assert d[k] is False, f"{k} must be false"
    for k in _REQUIRED_TRUE:
        assert d[k] is True, f"{k} must be true"
    rec = d["recommendation"]
    assert rec["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert rec["create_model_candidate_now"] is False
    assert rec["train_model_now"] is False
    assert rec["run_sector_relative_retest_now"] is False
    assert rec["production_edge_claimed"] is False
    assert d["recommended_next_phase"]["phase"] == "2K-R"


# --------------------------------------------------------------------------- #
# 8. Reads Phase 2K-P, confirms routing + SECTOR_MAP_TEMPLATE_CREATED + 128 rows
# --------------------------------------------------------------------------- #
def test_reads_phase2k_p_and_confirms_routing():
    d = json.loads(_read(_COMMITTED_RESULTS))
    kp = d["upstream_phase2k_p_summary"]
    assert kp["present"] is True
    assert kp["phase"] == "2K-P"
    assert kp["recommendation"] == "SECTOR_MAP_TEMPLATE_CREATED"
    assert kp["recommended_next_phase"]["phase"] == "2K-Q"
    assert kp["recommended_next_phase"]["title"] == "Populate Sector Map for Reconfirmed Leads"
    assert kp["template_generated"] is True
    assert kp["template_row_count"] == _EXPECTED_ROW_COUNT
    assert kp["create_model_candidate_now_false"] is True
    assert kp["train_model_now_false"] is True
    assert kp["run_sector_relative_retest_now_false"] is True
    assert kp["routing_confirmed"] is True


# --------------------------------------------------------------------------- #
# 9. Populated sector map: exists, 128 rows, schema, SPY absent, no duplicates
# --------------------------------------------------------------------------- #
def test_populated_sector_map_exists_and_well_formed():
    assert os.path.isfile(_POPULATED_MAP), "populated sector map must exist at research/input"
    rows = _read_csv_rows(_POPULATED_MAP)
    assert len(rows) == _EXPECTED_ROW_COUNT, f"populated map must have {_EXPECTED_ROW_COUNT} rows"
    header = _read(_POPULATED_MAP).splitlines()[0].strip()
    assert header == ",".join(_SECTOR_MAP_REQUIRED_COLUMNS)
    tickers = [r["ticker"].strip().upper() for r in rows]
    assert "SPY" not in tickers, "SPY must be absent from the populated sector map"
    assert len(tickers) == len(set(tickers)), "no duplicate tickers allowed"
    assert all(t == t.upper() and t for t in tickers), "all tickers must be uppercase / non-empty"


# --------------------------------------------------------------------------- #
# 10. Populated map coverage: 100% sector / industry, source / as_of populated, pit false
# --------------------------------------------------------------------------- #
def test_populated_sector_map_full_coverage():
    rows = _read_csv_rows(_POPULATED_MAP)
    assert all(r["sector"].strip() for r in rows), "sector coverage must be 100%"
    assert all(r["industry"].strip() for r in rows), "industry coverage must be 100%"
    assert all(r["source"].strip() for r in rows), "source must be populated for all rows"
    assert all(r["as_of_date"].strip() for r in rows), "as_of_date must be populated for all rows"
    assert all(r["point_in_time"].strip().lower() == "false" for r in rows), \
        "point_in_time must be false for every row"
    sectors = {r["sector"].strip() for r in rows}
    assert len(sectors) >= 5, "at least 5 distinct sectors required"


# --------------------------------------------------------------------------- #
# 11. Validation block in the JSON reflects a complete, caveated map
# --------------------------------------------------------------------------- #
def test_validation_block_reports_full_coverage():
    d = json.loads(_read(_COMMITTED_RESULTS))
    val = d["sector_map_validation"]
    assert val["row_count"] == _EXPECTED_ROW_COUNT
    assert val["template_ticker_count"] == _EXPECTED_ROW_COUNT
    assert val["ticker_coverage_fraction"] == 1.0
    assert val["sector_coverage_fraction"] == 1.0
    assert val["industry_coverage_fraction"] == 1.0
    assert val["has_required_columns"] is True
    assert val["tickers_unique"] is True
    assert val["spy_absent"] is True
    assert val["point_in_time_all_false"] is True
    assert val["distinct_sector_count"] >= 5
    assert val["at_least_min_sectors"] is True
    assert val["full_coverage"] is True
    assert val["missing_ticker_count"] == 0
    # Distributions sum to the row count and are non-empty.
    assert sum(d["sector_distribution"].values()) == _EXPECTED_ROW_COUNT
    assert sum(d["industry_distribution"].values()) == _EXPECTED_ROW_COUNT


# --------------------------------------------------------------------------- #
# 12. No retest / no broad screen / routes to 2K-R; disallows model work; caveats present
# --------------------------------------------------------------------------- #
def test_no_retest_and_routes_to_2k_r():
    d = json.loads(_read(_COMMITTED_RESULTS))
    assert d["sector_relative_retest_run"] is False
    assert d["broad_alpha_screen_run"] is False
    assert d["network_used"] is False
    assert d["d_drive_read"] is False
    assert d["d_drive_written"] is False
    interp = d["interpretation"]
    assert interp["model_trained"] is False
    assert interp["model_candidate_created"] is False
    assert interp["ran_sector_relative_retest"] is False
    assert interp["ran_broad_alpha_screen"] is False
    assert interp["fetched_sector_data_from_network"] is False
    assert interp["read_from_d_drive"] is False
    assert interp["wrote_to_d_drive"] is False
    assert interp["sector_map_is_point_in_time"] is False
    assert isinstance(d["caveats"], list) and len(d["caveats"]) >= 1
    assert any("not point-in-time" in c.lower() or "current-as-of" in c.lower()
               for c in d["caveats"]), "caveats must flag the current-as-of / not-PIT basis"
    assert d["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert d["recommended_next_phase"]["phase"] == "2K-R"


# --------------------------------------------------------------------------- #
# 13. Doc has all required guardrail phrases
# --------------------------------------------------------------------------- #
def test_doc_has_required_phrases():
    text = _read(_DOC)
    missing = [p for p in _REQUIRED_DOC_PHRASES if p not in text]
    assert not missing, f"doc missing required phrase(s): {missing}"


# --------------------------------------------------------------------------- #
# 14. Live run (reads committed summaries + template): structure + verdict
# --------------------------------------------------------------------------- #
def test_live_population_structure():
    analyzer = _import_analyzer()
    if not os.path.isfile(_PHASE2K_P_TEMPLATE):
        raise _Skip("phase 2K-P template not present in this environment")
    tmp = tempfile.mkdtemp(prefix="phase2k_q_")
    out = os.path.join(tmp, "populate.json")
    mp = os.path.join(tmp, "sector_map_current.csv")
    res = analyzer.run(output_path=out, sector_map_output=mp)
    blob = json.loads(_read(out))
    for d in (res, blob):
        assert d["phase"] == "2K-Q"
        for k in _REQUIRED_JSON_FIELDS:
            assert k in d, f"live results JSON missing field: {k}"
        for k in _REQUIRED_FALSE:
            assert d[k] is False
        for k in _REQUIRED_TRUE:
            assert d[k] is True
        assert d["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
        assert d["recommendation"]["run_sector_relative_retest_now"] is False
        assert d["recommended_next_phase"]["phase"] == "2K-R"
        assert d["upstream_phase2k_p_summary"]["routing_confirmed"] is True
        assert d["sector_map_validation"]["full_coverage"] is True
    # The live run writes the populated map.
    assert os.path.isfile(mp), "live run must write the populated sector map"
    rows = _read_csv_rows(mp)
    assert len(rows) == _EXPECTED_ROW_COUNT
    assert "SPY" not in {r["ticker"].strip().upper() for r in rows}


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
