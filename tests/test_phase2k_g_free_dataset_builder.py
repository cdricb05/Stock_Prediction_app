"""Phase 2K-G tests for the Free Expanded Price/Volume Dataset Builder + validator.

These tests prove the builder and its readiness analyzer are disciplined: the builder is
import-safe and creates nothing on import; its safe dry-run and fixture modes make no network
call and write none of the large real output files; its --execute mode refuses to run without
the explicit --allow-network guard; the analyzer reads the Phase 2K-F plan, runs the builder
only in safe modes, and writes one validation JSON carrying every required field, safety flag,
and decision; the analyzer contains no deploy / gcloud / SSH / DB-write / migration logic and
no network-execution logic; neither imports api_server or Paper Trader; and the doc carries
the guardrail phrases.

It is acceptable for the BUILDER to contain a guarded free-data retrieval dependency, because
it is reached only via --execute plus --allow-network and is never run here. The analyzer must
stay network-free.

Runs two ways:
  * under pytest:   pytest tests/test_phase2k_g_free_dataset_builder.py
  * without pytest: python tests/test_phase2k_g_free_dataset_builder.py
The __main__ block discovers every test_* function, runs it, prints PASS/FAIL, and exits
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

_BUILDER = os.path.join(
    _REPO_ROOT, "research", "build_phase2k_g_free_expanded_dataset.py")
_ANALYZER = os.path.join(
    _REPO_ROOT, "research", "analyze_phase2k_g_free_dataset_builder.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase2k_g_free_dataset_builder_v1.md")
_INPUT_2KF_JSON = os.path.join(
    _REPO_ROOT, "research", "output", "phase2k_f_free_data_build_plan.json")
_SAMPLE_UNIVERSE = os.path.join(
    _REPO_ROOT, "research", "input", "phase2k_g_free_universe_tickers_sample.csv")

# The large real output files. Phase 2K-G must NOT create these — neither on the D: data
# root (where the full build will write them) nor in the legacy C: repo output dir.
_REAL_OUTPUT_FILENAMES = (
    "phase2k_g_expanded_price_history_free.csv",
    "phase2k_g_expanded_scored_free.csv",
    "phase2k_g_data_quality_report.json",
    "phase2k_g_data_build_summary.json",
    "phase2k_g_survivorship_caveat.json",
)
# External data root on the D: drive (must match the builder's DEFAULT_DATA_ROOT).
_D_DATA_ROOT = r"D:\Stock_Prediction_app_data\phase2k_g"
_D_DATA_ROOT_NORM = _D_DATA_ROOT.replace("\\", "/") + "/"
_D_OUTPUT_FILES = tuple(
    os.path.join(_D_DATA_ROOT, "output", name) for name in _REAL_OUTPUT_FILENAMES)
# Legacy C: repo output location — must also stay clean.
_C_OUTPUT_FILES = tuple(
    os.path.join("research", "output", name) for name in _REAL_OUTPUT_FILENAMES)
# The full (non-sample) universe file must NOT be created by this phase, in the repo or on D:.
_FULL_UNIVERSE_CSV = os.path.join(
    "research", "input", "phase2k_g_free_universe_tickers.csv")
_D_FULL_UNIVERSE_CSV = os.path.join(
    _D_DATA_ROOT, "input", "phase2k_g_free_universe_tickers.csv")

# Forbidden infrastructure tokens (assembled from fragments). Apply to both sources.
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

# Forbidden model-training / fitting tokens. Apply to both sources (no model work here).
_FORBIDDEN_MODEL_TOKENS = [
    ".fit(", "fit_transform", "sklearn", "tensorflow", "lightgbm", "xgboost",
    "torch", "keras", "LinearRegression", "RandomForest", "MLPRegressor",
    "GradientBoosting",
]

# Forbidden network / acquisition tokens. Apply to the ANALYZER only (the builder is allowed
# guarded retrieval reached only via --execute --allow-network).
_FORBIDDEN_NETWORK_TOKENS = [
    "http://", "https://", "requests.", "urllib", "yf" + "inance", "socket",
    "url" + "open", "down" + "load(", "pur" + "chase(",
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
    "phase", "generated_at", "inputs_read", "upstream_phase2k_f_summary",
    "builder_script", "sample_universe", "dry_run_validation", "fixture_validation",
    "planned_real_outputs", "data_quality_check_catalog", "survivorship_caveat_policy",
    "decision", "recommended_next_phase",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_edge_claimed",
)
_REQUIRED_TRUE = ("no_trading", "no_orders", "no_automation")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _import(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _import_builder():
    return _import("phase2k_g_builder_test", _BUILDER)


def _import_analyzer():
    return _import("phase2k_g_analyzer_test", _ANALYZER)


def _abs(rel: str) -> str:
    return os.path.join(_REPO_ROOT, rel)


def _assert_no_real_outputs():
    # Large real outputs must not be created under the C: repo ...
    for rel in _C_OUTPUT_FILES:
        assert not os.path.isfile(_abs(rel)), \
            f"real output must not exist under the C: repo: {rel}"
    # ... nor on the D: default data root.
    for path in _D_OUTPUT_FILES:
        assert not os.path.isfile(path), \
            f"real output must not exist on the D: data root: {path}"


# Build the validation artifact at most once per test process.
_VAL_CACHE = {}


def _validation():
    if "val" not in _VAL_CACHE:
        mod = _import_analyzer()
        out = os.path.join(tempfile.mkdtemp(), "builder_validation.json")
        _VAL_CACHE["val"] = mod.run(output_path=out)
        _VAL_CACHE["on_disk"] = json.loads(_read(out))
    return _VAL_CACHE["val"]


# --------------------------------------------------------------------------- #
# 1-2. Both sources compile
# --------------------------------------------------------------------------- #
def test_builder_compiles():
    compile(_read(_BUILDER), _BUILDER, "exec")


def test_analyzer_compiles():
    compile(_read(_ANALYZER), _ANALYZER, "exec")


# --------------------------------------------------------------------------- #
# 3. Expected input files are referenced
# --------------------------------------------------------------------------- #
def test_references_expected_inputs():
    analyzer = _import_analyzer()
    assert analyzer.INPUT_2KF_JSON.replace("\\", "/").endswith(
        "research/output/phase2k_f_free_data_build_plan.json")
    assert analyzer.BUILDER_SCRIPT.replace("\\", "/").endswith(
        "research/build_phase2k_g_free_expanded_dataset.py")
    assert analyzer.SAMPLE_UNIVERSE_CSV.replace("\\", "/").endswith(
        "research/input/phase2k_g_free_universe_tickers_sample.csv")
    builder = _import_builder()
    assert builder.SAMPLE_UNIVERSE_CSV.replace("\\", "/").endswith(
        "research/input/phase2k_g_free_universe_tickers_sample.csv")


# --------------------------------------------------------------------------- #
# 4. No api_server import (both)
# --------------------------------------------------------------------------- #
def test_no_api_server_import():
    for path in (_BUILDER, _ANALYZER):
        text = _read(path)
        assert "import api_server" not in text
        assert "from api_server" not in text
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = ([n.name for n in node.names]
                        + ([node.module] if isinstance(node, ast.ImportFrom)
                           and node.module else []))
                assert not any((m or "").split(".")[0] == "api_server" for m in mods)


# --------------------------------------------------------------------------- #
# 5. No Paper Trader import (both)
# --------------------------------------------------------------------------- #
def test_no_paper_trader_import():
    for path in (_BUILDER, _ANALYZER):
        assert "paper_trader" not in _read(path).lower()


# --------------------------------------------------------------------------- #
# 6. No deploy / gcloud / SSH / DB-write / migration logic (both); no model tokens
# --------------------------------------------------------------------------- #
def test_no_forbidden_infrastructure_logic():
    for path in (_BUILDER, _ANALYZER):
        text = _read(path).lower()
        hits = [tok for tok in _FORBIDDEN_TOKENS if tok.lower() in text]
        assert not hits, f"{os.path.basename(path)} has forbidden token(s): {hits}"
        for tok in _FORBIDDEN_EXTRA:
            assert tok not in text, f"{os.path.basename(path)} has forbidden token: {tok!r}"
        model_hits = [tok for tok in _FORBIDDEN_MODEL_TOKENS if tok in _read(path)]
        assert not model_hits, f"{os.path.basename(path)} has model token(s): {model_hits}"


# --------------------------------------------------------------------------- #
# 7. The analyzer contains no network-execution logic and writes only its JSON
# --------------------------------------------------------------------------- #
def test_analyzer_has_no_network_logic():
    text = _read(_ANALYZER)
    hits = [tok for tok in _FORBIDDEN_NETWORK_TOKENS if tok.lower() in text.lower()]
    assert not hits, f"analyzer contains network/acquisition token(s): {hits}"
    assert "to_csv" not in text, "analyzer must not write any CSV"
    assert "to_sql" not in text, "analyzer must not write to a database"
    write_opens = 0
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
                    and "w" in str(node.args[1].value):
                write_opens += 1
    assert write_opens == 1, "analyzer must have exactly one write-open (validation JSON)"


# --------------------------------------------------------------------------- #
# 8. Builder script exists and is import-safe (no work / files on import)
# --------------------------------------------------------------------------- #
def test_builder_script_exists_and_import_safe():
    assert os.path.isfile(_BUILDER)
    _import_builder()
    _assert_no_real_outputs()


# --------------------------------------------------------------------------- #
# 9. Sample universe exists with the required columns
# --------------------------------------------------------------------------- #
def test_sample_universe_exists_and_columns():
    assert os.path.isfile(_SAMPLE_UNIVERSE), "sample universe CSV must exist"
    with open(_SAMPLE_UNIVERSE, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        rows = list(reader)
    for col in ("ticker", "name", "sector", "source", "active_as_of",
                "survivorship_caveat"):
        assert col in header, f"sample universe missing column: {col}"
    assert len(rows) >= 10, "sample universe should hold at least ~10 tickers"
    assert any(r["ticker"].strip().upper() == "SPY" for r in rows), "benchmark SPY expected"
    # The FULL universe file must NOT have been created by this phase, in the repo or on D:.
    assert not os.path.isfile(_abs(_FULL_UNIVERSE_CSV)), \
        "this phase must not create the full universe file in the repo"
    assert not os.path.isfile(_D_FULL_UNIVERSE_CSV), \
        "this phase must not create the full universe file on the D: data root"
    # The builder declares the full universe on the D: data root, not the C: repo.
    builder = _import_builder()
    assert builder.FULL_UNIVERSE_CSV.replace("\\", "/").startswith(_D_DATA_ROOT_NORM), \
        "builder must declare the full universe file on the D: data root"


# --------------------------------------------------------------------------- #
# 10. Dry-run mode creates no real output files
# --------------------------------------------------------------------------- #
def test_dry_run_creates_no_real_outputs():
    builder = _import_builder()
    summary = builder.dry_run(universe_path=_SAMPLE_UNIVERSE)
    assert summary["mode"] == "dry-run"
    assert summary["network_calls_made"] is False
    assert summary["real_outputs_created"] is False
    _assert_no_real_outputs()


# --------------------------------------------------------------------------- #
# 11. Fixture mode creates no real output files (writes only to its temp dir)
# --------------------------------------------------------------------------- #
def test_fixture_creates_no_real_outputs():
    builder = _import_builder()
    tmp = tempfile.mkdtemp(prefix="phase2k_g_test_fixture_")
    result = builder.fixture_run(output_dir=tmp, universe_path=_SAMPLE_UNIVERSE)
    assert result["mode"] == "fixture-mode"
    assert result["network_calls_made"] is False
    assert result["real_outputs_created"] is False
    assert result["data_quality_status"] in ("PASS", "PASS_WITH_CAVEAT", "FAIL")
    # Files were written into the temp dir, not research/output.
    assert result["files_written"], "fixture should write fixture files into the temp dir"
    for p in result["files_written"]:
        assert os.path.isfile(p.replace("/", os.sep)) or os.path.isfile(p)
    _assert_no_real_outputs()


# --------------------------------------------------------------------------- #
# 12. Execute mode refuses without --allow-network (and fetches nothing)
# --------------------------------------------------------------------------- #
def test_execute_requires_allow_network():
    builder = _import_builder()
    rc = builder.main(["--execute"])
    assert rc != 0, "--execute without --allow-network must fail safely (non-zero)"
    # Even with dates supplied, the network guard must still refuse.
    rc2 = builder.main(["--execute", "--start", "2016-01-01", "--end", "2026-01-01"])
    assert rc2 != 0
    _assert_no_real_outputs()


# --------------------------------------------------------------------------- #
# 13. Planned real outputs are declared
# --------------------------------------------------------------------------- #
def _assert_declared_on_d_root(declared):
    # Every planned real output lives on the D: external data root, under .../output/.
    for p in declared:
        assert p.startswith(_D_DATA_ROOT_NORM), \
            f"planned real output must live on the D: data root: {p}"
    for name in _REAL_OUTPUT_FILENAMES:
        assert any(p.endswith("/output/" + name) for p in declared), \
            f"missing declared real output: {name}"


def test_planned_real_outputs_declared():
    builder = _import_builder()
    planned = builder.planned_real_outputs()
    declared = {v["path"].replace("\\", "/") for v in planned.values()
                if isinstance(v, dict) and "path" in v}
    _assert_declared_on_d_root(declared)
    # The analyzer's JSON also declares them on the D: data root.
    ap = _validation()["planned_real_outputs"]
    adeclared = {v["path"].replace("\\", "/") for v in ap.values()
                 if isinstance(v, dict) and "path" in v}
    _assert_declared_on_d_root(adeclared)


# --------------------------------------------------------------------------- #
# 14. Planned real outputs are not created
# --------------------------------------------------------------------------- #
def test_planned_real_outputs_not_created():
    _validation()  # ensure the analyzer's safe-mode runs have happened
    _assert_no_real_outputs()
    assert _validation()["real_outputs_status"]["all_absent"] is True


# --------------------------------------------------------------------------- #
# 15. Data-quality check catalog exists (builder + analyzer JSON)
# --------------------------------------------------------------------------- #
def test_data_quality_check_catalog_present():
    builder = _import_builder()
    catalog = builder.data_quality_check_catalog()
    assert isinstance(catalog, list) and len(catalog) >= 12
    ids = {c["check_id"] for c in catalog}
    for required in ("adjusted_close_continuity", "duplicate_date_ticker_rows",
                     "missing_adjusted_close", "missing_volume", "benchmark_coverage",
                     "minimum_years_achieved", "minimum_ticker_count_achieved",
                     "no_forward_filled_labels", "no_point_in_time_membership_claim",
                     "survivorship_caveat_present"):
        assert required in ids, f"data-quality catalog missing: {required}"
    jcatalog = _validation()["data_quality_check_catalog"]
    assert isinstance(jcatalog, list) and len(jcatalog) >= 12


# --------------------------------------------------------------------------- #
# 16. Output JSON: required fields and safety flags
# --------------------------------------------------------------------------- #
def test_output_json_required_fields():
    val = _validation()
    on_disk = _VAL_CACHE["on_disk"]
    for d in (val, on_disk):
        for k in _REQUIRED_JSON_FIELDS:
            assert k in d, f"validation JSON missing field: {k}"
        assert d["phase"] == "2K-G"
        for k in _REQUIRED_FALSE:
            assert d[k] is False, f"{k} must be false"
        for k in _REQUIRED_TRUE:
            assert d[k] is True, f"{k} must be true"
        assert d["inputs_read"]["phase2k_f_json"].replace("\\", "/").endswith(
            "research/output/phase2k_f_free_data_build_plan.json")


# --------------------------------------------------------------------------- #
# 17. Decision defers the live build, dataset, paid data, and model
# --------------------------------------------------------------------------- #
def test_decision_flags():
    dec = _validation()["decision"]
    assert dec["builder_script_created"] is True
    assert dec["sample_universe_created"] is True
    assert dec["live_download_executed"] is False
    assert dec["real_dataset_outputs_created"] is False
    assert dec["paid_data_acquired"] is False
    assert dec["model_candidate_created"] is False
    assert isinstance(dec["ready_for_manual_live_build"], bool)
    assert isinstance(dec["reason"], str) and dec["reason"].strip()


# --------------------------------------------------------------------------- #
# 18. recommended_next_phase routes to 2K-H
# --------------------------------------------------------------------------- #
def test_recommended_next_phase_is_2k_h():
    nxt = _validation()["recommended_next_phase"]
    for k in ("phase", "title", "purpose"):
        assert k in nxt, f"recommended_next_phase missing: {k}"
    assert nxt["phase"] == "2K-H"
    assert nxt["title"] == "Manual Free Expanded Dataset Build Run"


# --------------------------------------------------------------------------- #
# 19. Reads the Phase 2K-F plan and reflects its routing
# --------------------------------------------------------------------------- #
def test_reads_phase2k_f_plan():
    up = _validation()["upstream_phase2k_f_summary"]
    live = json.loads(_read(_INPUT_2KF_JSON))
    assert up["phase"] == live["phase"] == "2K-F"
    assert up["kf_recommended_next_phase"] == live["recommended_next_phase"]["phase"]
    assert up["routed_to_2k_g"] is True


# --------------------------------------------------------------------------- #
# 20. Doc has all required guardrail phrases
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
