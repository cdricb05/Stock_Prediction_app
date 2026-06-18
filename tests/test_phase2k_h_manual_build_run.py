"""Phase 2K-H tests for the Manual Free Expanded Dataset Build Run tracking analyzer.

These tests prove the analyzer is a disciplined, read-only tracking artifact: it compiles and
imports cleanly; it references the expected D: drive input paths; it never runs the builder,
never makes a network call, and writes no file on the D: drive (its only write is the small
summary JSON in the C: repo); it handles the missing-outputs case gracefully (recommending
2K-H-RUN); it routes to 2K-I once a valid build is present; it carries every required output
field, safety flag, and decision field; it imports neither api_server nor Paper Trader; it
contains no forbidden infrastructure / model / network tokens; and the doc carries the
guardrail phrases.

The present-build case is exercised deterministically by using the Phase 2K-G builder's
fixture mode to populate a temp data root, then pointing the analyzer at it — never the real
D: drive and never the network build mode.

Runs two ways:
  * under pytest:   pytest tests/test_phase2k_h_manual_build_run.py
  * without pytest: python tests/test_phase2k_h_manual_build_run.py
The __main__ block discovers every test_* function, runs it, prints PASS/FAIL, and exits
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
    _REPO_ROOT, "research", "analyze_phase2k_h_manual_build_run.py")
_BUILDER = os.path.join(
    _REPO_ROOT, "research", "build_phase2k_g_free_expanded_dataset.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase2k_h_manual_build_run_v1.md")
_SAMPLE_UNIVERSE = os.path.join(
    _REPO_ROOT, "research", "input", "phase2k_g_free_universe_tickers_sample.csv")
_COMMITTED_SUMMARY = os.path.join(
    _REPO_ROOT, "research", "output", "phase2k_h_manual_build_run_summary.json")

# External data root on the D: drive (must match the builder's DEFAULT_DATA_ROOT).
_D_DATA_ROOT = r"D:\Stock_Prediction_app_data\phase2k_g"
_D_DATA_ROOT_NORM = _D_DATA_ROOT.replace("\\", "/")

_REAL_OUTPUT_FILENAMES = (
    "phase2k_g_expanded_price_history_free.csv",
    "phase2k_g_expanded_scored_free.csv",
    "phase2k_g_data_quality_report.json",
    "phase2k_g_data_build_summary.json",
    "phase2k_g_survivorship_caveat.json",
)
# Large outputs must never appear under the C: repo output dir.
_C_OUTPUT_FILES = tuple(
    os.path.join("research", "output", name) for name in _REAL_OUTPUT_FILENAMES)

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

# Forbidden model-training / fitting tokens (no model work here).
_FORBIDDEN_MODEL_TOKENS = [
    ".fit(", "fit_transform", "sklearn", "tensorflow", "lightgbm", "xgboost",
    "torch", "keras", "LinearRegression", "RandomForest", "MLPRegressor",
    "GradientBoosting",
]

# Forbidden network / acquisition tokens — the analyzer must stay network-free.
_FORBIDDEN_NETWORK_TOKENS = [
    "http://", "https://", "requests.", "urllib", "yf" + "inance", "socket",
    "url" + "open", "down" + "load(", "pur" + "chase(",
]

# The analyzer must not invoke the builder's network build mode.
_FORBIDDEN_INVOCATION_TOKENS = [
    "--exe" + "cute", "--allow-" + "network", "allow_" + "network",
    "execute_" + "build(", "fixture_" + "run(", "yf.",
]

# The analyzer must not mutate D: data files.
_FORBIDDEN_MUTATION_TOKENS = [
    "os." + "remove", "os." + "rename", "os." + "unlink", "shutil", "rm" + "tree",
    "to_csv", "to_sql",
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
    "phase", "generated_at", "inputs_read", "data_root", "expected_d_drive_outputs",
    "detected_outputs", "upstream_phase2k_g_summary", "data_quality_summary",
    "build_summary", "survivorship_caveat_summary", "csv_metadata", "decision",
    "recommended_next_phase",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_edge_claimed",
)
_REQUIRED_TRUE = ("no_trading", "no_orders", "no_automation")

_REQUIRED_DECISION_FIELDS = (
    "manual_build_detected", "ready_for_retest", "data_quality_passed",
    "survivorship_caveat_present", "point_in_time_membership_claimed",
    "large_data_on_d_drive", "repo_large_outputs_created", "model_candidate_created",
    "reason",
)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _import(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _import_analyzer():
    return _import("phase2k_h_analyzer_test", _ANALYZER)


def _import_builder():
    return _import("phase2k_g_builder_for_2kh_test", _BUILDER)


def _abs(rel: str) -> str:
    return os.path.join(_REPO_ROOT, rel)


def _run_missing(tmp_root: str):
    """Run the analyzer against an empty temp data root (no D: outputs)."""
    analyzer = _import_analyzer()
    out = os.path.join(tempfile.mkdtemp(), "summary.json")
    nope = os.path.join(tmp_root, "no_upstream.json")
    return analyzer.run(output_path=out, data_root=tmp_root, upstream_path=nope), out


def _run_present(tmp_root: str):
    """Populate <tmp_root>/output with a real fixture build, then run the analyzer on it."""
    builder = _import_builder()
    out_dir = os.path.join(tmp_root, "output")
    builder.fixture_run(output_dir=out_dir, universe_path=_SAMPLE_UNIVERSE)
    analyzer = _import_analyzer()
    out = os.path.join(tempfile.mkdtemp(), "summary.json")
    nope = os.path.join(tmp_root, "no_upstream.json")
    return analyzer.run(output_path=out, data_root=tmp_root, upstream_path=nope), out


def _assert_no_repo_large_outputs():
    for rel in _C_OUTPUT_FILES:
        assert not os.path.isfile(_abs(rel)), \
            f"large output must not exist under the C: repo: {rel}"


# --------------------------------------------------------------------------- #
# 1. Analyzer compiles and imports
# --------------------------------------------------------------------------- #
def test_analyzer_compiles():
    compile(_read(_ANALYZER), _ANALYZER, "exec")


def test_analyzer_imports():
    _import_analyzer()


# --------------------------------------------------------------------------- #
# 2. Expected D: input paths are referenced
# --------------------------------------------------------------------------- #
def test_references_expected_d_inputs():
    analyzer = _import_analyzer()
    assert analyzer.DATA_ROOT.replace("\\", "/") == _D_DATA_ROOT_NORM
    for const in (analyzer.PRICE_HISTORY_CSV, analyzer.SCORED_CSV,
                  analyzer.DATA_QUALITY_JSON, analyzer.BUILD_SUMMARY_JSON,
                  analyzer.SURVIVORSHIP_JSON):
        assert const.replace("\\", "/").startswith(_D_DATA_ROOT_NORM + "/output/"), \
            f"expected D: output path not on the data root: {const}"
    assert analyzer.RESULTS_JSON.replace("\\", "/").endswith(
        "research/output/phase2k_h_manual_build_run_summary.json")
    assert analyzer.UPSTREAM_2KG_VALIDATION_JSON.replace("\\", "/").endswith(
        "research/output/phase2k_g_builder_validation.json")


# --------------------------------------------------------------------------- #
# 3. Analyzer's D: paths match the Phase 2K-G builder constants (no drift)
# --------------------------------------------------------------------------- #
def test_d_paths_match_builder():
    analyzer = _import_analyzer()
    builder = _import_builder()
    assert analyzer.DATA_ROOT.replace("\\", "/") == builder.DEFAULT_DATA_ROOT.replace("\\", "/")
    pairs = (
        (analyzer.PRICE_HISTORY_CSV, builder.REAL_PRICE_HISTORY_CSV),
        (analyzer.SCORED_CSV, builder.REAL_SCORED_CSV),
        (analyzer.DATA_QUALITY_JSON, builder.REAL_DATA_QUALITY_JSON),
        (analyzer.BUILD_SUMMARY_JSON, builder.REAL_BUILD_SUMMARY_JSON),
        (analyzer.SURVIVORSHIP_JSON, builder.REAL_SURVIVORSHIP_JSON),
    )
    for a, b in pairs:
        assert a.replace("\\", "/") == b.replace("\\", "/"), \
            f"analyzer path {a!r} drifted from builder path {b!r}"


# --------------------------------------------------------------------------- #
# 4. No api_server import
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
# 5. No Paper Trader import
# --------------------------------------------------------------------------- #
def test_no_paper_trader_import():
    assert "paper_trader" not in _read(_ANALYZER).lower()


# --------------------------------------------------------------------------- #
# 6. No forbidden infrastructure / model tokens
# --------------------------------------------------------------------------- #
def test_no_forbidden_infrastructure_logic():
    text = _read(_ANALYZER)
    low = text.lower()
    hits = [tok for tok in _FORBIDDEN_TOKENS if tok.lower() in low]
    assert not hits, f"analyzer has forbidden token(s): {hits}"
    for tok in _FORBIDDEN_EXTRA:
        assert tok not in low, f"analyzer has forbidden token: {tok!r}"
    model_hits = [tok for tok in _FORBIDDEN_MODEL_TOKENS if tok in text]
    assert not model_hits, f"analyzer has model token(s): {model_hits}"


# --------------------------------------------------------------------------- #
# 7. No network logic, no builder invocation, no D: mutation, one write-open
# --------------------------------------------------------------------------- #
def test_analyzer_is_read_only_and_network_free():
    text = _read(_ANALYZER)
    low = text.lower()
    net = [tok for tok in _FORBIDDEN_NETWORK_TOKENS if tok.lower() in low]
    assert not net, f"analyzer contains network/acquisition token(s): {net}"
    inv = [tok for tok in _FORBIDDEN_INVOCATION_TOKENS if tok.lower() in low]
    assert not inv, f"analyzer contains builder-invocation token(s): {inv}"
    mut = [tok for tok in _FORBIDDEN_MUTATION_TOKENS if tok.lower() in low]
    assert not mut, f"analyzer contains D:-mutation token(s): {mut}"
    # Exactly one write-open (the small summary JSON); all other opens are read-only.
    write_opens = 0
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
                    and "w" in str(node.args[1].value):
                write_opens += 1
    assert write_opens == 1, "analyzer must have exactly one write-open (summary JSON)"


# --------------------------------------------------------------------------- #
# 8. Analyzer does not import or execute the Phase 2K-G builder
# --------------------------------------------------------------------------- #
def test_analyzer_does_not_run_builder():
    text = _read(_ANALYZER)
    assert "build_phase2k_g" not in text, "analyzer must not import / reference the builder module"
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = ([n.name for n in node.names]
                    + ([node.module] if isinstance(node, ast.ImportFrom)
                       and node.module else []))
            assert not any("build_phase2k_g" in (m or "") for m in mods)


# --------------------------------------------------------------------------- #
# 9. Missing-outputs case is handled gracefully
# --------------------------------------------------------------------------- #
def test_missing_outputs_graceful():
    tmp = tempfile.mkdtemp(prefix="phase2k_h_missing_")
    res, on_disk_path = _run_missing(tmp)
    dec = res["decision"]
    assert dec["manual_build_detected"] is False
    assert dec["ready_for_retest"] is False
    assert dec["large_data_on_d_drive"] is False
    assert res["recommended_next_phase"]["phase"] == "2K-H-RUN"
    # All five detected outputs report absent; CSV metadata reports not-exists.
    for v in res["detected_outputs"].values():
        assert v["exists"] is False
    for v in res["csv_metadata"].values():
        assert v["exists"] is False and v["data_row_count"] == 0
    assert os.path.isfile(on_disk_path)
    _assert_no_repo_large_outputs()


# --------------------------------------------------------------------------- #
# 10. Present-and-valid build routes to 2K-I (deterministic via fixture build)
# --------------------------------------------------------------------------- #
def test_present_valid_build_ready_for_retest():
    tmp = tempfile.mkdtemp(prefix="phase2k_h_present_")
    res, _ = _run_present(tmp)
    dec = res["decision"]
    assert dec["manual_build_detected"] is True
    assert dec["ready_for_retest"] is True
    assert dec["data_quality_passed"] is True
    assert dec["survivorship_caveat_present"] is True
    assert dec["point_in_time_membership_claimed"] is False
    assert dec["large_data_on_d_drive"] is True
    assert dec["repo_large_outputs_created"] is False
    assert dec["model_candidate_created"] is False
    assert res["recommended_next_phase"]["phase"] == "2K-I"
    assert res["recommended_next_phase"]["title"] == "Expanded Dataset Residual Signal Retest"
    # CSV metadata was read lightly (header + row count), not loaded as a frame.
    price = res["csv_metadata"]["expanded_price_history_csv"]
    assert price["exists"] is True
    assert "ticker" in (price["header"] or [])
    assert price["data_row_count"] > 0
    # The build summary and DQ summary were parsed.
    assert res["build_summary"]["present"] is True
    assert res["data_quality_summary"]["present"] is True
    assert res["data_quality_summary"]["status"] in ("PASS", "PASS_WITH_CAVEAT")
    _assert_no_repo_large_outputs()


# --------------------------------------------------------------------------- #
# 11. recommended_next_phase is always one of the two allowed phases
# --------------------------------------------------------------------------- #
def test_recommended_next_phase_allowed_values():
    tmp_missing = tempfile.mkdtemp(prefix="phase2k_h_rec_missing_")
    tmp_present = tempfile.mkdtemp(prefix="phase2k_h_rec_present_")
    miss, _ = _run_missing(tmp_missing)
    pres, _ = _run_present(tmp_present)
    for res in (miss, pres):
        nxt = res["recommended_next_phase"]
        for k in ("phase", "title", "purpose"):
            assert k in nxt, f"recommended_next_phase missing: {k}"
        assert nxt["phase"] in ("2K-H-RUN", "2K-I")


# --------------------------------------------------------------------------- #
# 12. Output JSON: required fields and safety flags
# --------------------------------------------------------------------------- #
def test_output_json_required_fields():
    tmp = tempfile.mkdtemp(prefix="phase2k_h_fields_")
    res, on_disk_path = _run_missing(tmp)
    on_disk = json.loads(_read(on_disk_path))
    for d in (res, on_disk):
        for k in _REQUIRED_JSON_FIELDS:
            assert k in d, f"summary JSON missing field: {k}"
        assert d["phase"] == "2K-H"
        for k in _REQUIRED_FALSE:
            assert d[k] is False, f"{k} must be false"
        for k in _REQUIRED_TRUE:
            assert d[k] is True, f"{k} must be true"
        assert d["data_root"].replace("\\", "/") == _D_DATA_ROOT_NORM \
            or d["data_root"].startswith(tmp.replace("\\", "/"))


# --------------------------------------------------------------------------- #
# 13. Decision block carries every required field
# --------------------------------------------------------------------------- #
def test_decision_block_fields():
    tmp = tempfile.mkdtemp(prefix="phase2k_h_decision_")
    res, _ = _run_missing(tmp)
    dec = res["decision"]
    for k in _REQUIRED_DECISION_FIELDS:
        assert k in dec, f"decision missing field: {k}"
    assert dec["repo_large_outputs_created"] is False
    assert dec["model_candidate_created"] is False
    assert isinstance(dec["reason"], str) and dec["reason"].strip()


# --------------------------------------------------------------------------- #
# 14. The default run reports the D: data root and never writes to the D: drive
# --------------------------------------------------------------------------- #
def test_default_run_reports_d_drive_and_writes_only_summary():
    analyzer = _import_analyzer()
    out = os.path.join(tempfile.mkdtemp(), "summary.json")
    res = analyzer.run(output_path=out)  # default data root = D: drive
    assert res["data_root"].replace("\\", "/") == _D_DATA_ROOT_NORM
    for path in res["expected_d_drive_outputs"].values():
        assert path.startswith(_D_DATA_ROOT_NORM + "/output/")
    assert os.path.isfile(out)
    _assert_no_repo_large_outputs()


# --------------------------------------------------------------------------- #
# 15. The committed summary artifact is schema-valid (pre-run snapshot)
# --------------------------------------------------------------------------- #
def test_committed_summary_artifact_valid():
    assert os.path.isfile(_COMMITTED_SUMMARY), "committed summary JSON must exist"
    d = json.loads(_read(_COMMITTED_SUMMARY))
    for k in _REQUIRED_JSON_FIELDS:
        assert k in d, f"committed summary missing field: {k}"
    assert d["phase"] == "2K-H"
    for k in _REQUIRED_FALSE:
        assert d[k] is False
    for k in _REQUIRED_TRUE:
        assert d[k] is True
    for k in _REQUIRED_DECISION_FIELDS:
        assert k in d["decision"], f"committed summary decision missing: {k}"
    assert d["recommended_next_phase"]["phase"] in ("2K-H-RUN", "2K-I")
    assert d["data_root"].replace("\\", "/") == _D_DATA_ROOT_NORM


# --------------------------------------------------------------------------- #
# 16. Doc has all required guardrail phrases
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
