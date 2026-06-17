"""Phase 2K-B tests for the Beta/Vol-Neutralized Residual Signal Test analyzer.

These tests prove the analyzer is observational and read-only: it compiles and
imports without side effects; it references only the local Phase 2G real-data
artifacts and the upstream 2I-A / 2J-A / 2K-A JSON summaries; it writes only
research/output/phase2k_b_residual_signal.json; it never imports api_server or
Paper Trader; its source contains no deploy / gcloud / SSH / service / DB-write /
migration logic; and the diagnostics JSON it produces carries every required
field and safety flag, a residualization config, a residual label for every
horizon, the risk controls marked CONTROL_ONLY, a comparison to the Phase 2I-A
raw-excess IC, keep/drop recommendations drawn only from the allowed vocabulary,
and a recommended next phase. The companion doc must contain the guardrail
phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase2k_b_residual_signal.py
  * without pytest: python tests/test_phase2k_b_residual_signal.py
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

_ANALYZER = os.path.join(
    _REPO_ROOT, "research", "analyze_phase2k_b_residual_signal.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase2k_b_residual_signal_test_v1.md")
_OUTPUT_JSON = os.path.join(
    _REPO_ROOT, "research", "output", "phase2k_b_residual_signal.json")

_HORIZONS = ["5", "10", "21", "63"]

# The candidate alpha features and the trailing risk controls.
_EXPECTED_CANDIDATES = {
    "return_5d", "return_10d", "return_21d", "return_63d", "momentum_12_1",
    "excess_return_vs_spy_21d", "excess_return_vs_spy_63d", "volume_zscore_21d",
    "avg_dollar_volume_21d",
}
_EXPECTED_CONTROLS = {
    "rolling_beta_63d", "realized_vol_21d", "realized_vol_63d",
    "downside_vol_21d", "rolling_corr_spy_63d",
}

# The only recommendation values keep_drop may emit.
_ALLOWED_RECOMMENDATIONS = {
    "KEEP_FOR_ROBUSTNESS_TEST", "DROP", "NEED_MORE_DATA", "CONTROL_ONLY",
}
# Allowed recommended-next-phase ids: 2K-C residual robustness (survivors) or
# 2K-D data acquisition (no survivor). Never a model-training phase here.
_ALLOWED_NEXT_PHASES = {"2K-C", "2K-D"}

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
    "PREDICTOR_USE_" + "MODEL_V2",
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

# Required top-level fields of the residual-signal JSON.
_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read", "residualization_config", "panel",
    "candidate_features", "control_features", "features",
    "comparison_to_phase2i_a", "keep_drop", "interpretation",
    "recommended_next_phase",
)
_REQUIRED_RESID_CONFIG_FIELDS = (
    "label_base", "horizons", "controls", "method", "robust_fallback",
    "min_names_for_ols", "no_lookahead", "residual_label_columns",
)
_REQUIRED_NEXT_PHASE_FIELDS = ("phase", "title", "purpose")
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_edge_claimed",
)
_REQUIRED_TRUE = ("no_trading", "no_orders", "no_automation")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _import_analyzer():
    spec = importlib.util.spec_from_file_location("phase2k_b_analyzer", _ANALYZER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Run the analysis at most once per test process and cache both the returned
# dict and the on-disk JSON.
_DIAG_CACHE = {}


def _diag():
    if "diag" not in _DIAG_CACHE:
        mod = _import_analyzer()
        out = os.path.join(tempfile.mkdtemp(), "residual.json")
        _DIAG_CACHE["diag"] = mod.run(output_path=out)
        _DIAG_CACHE["on_disk"] = json.loads(_read(out))
    return _DIAG_CACHE["diag"]


# --------------------------------------------------------------------------- #
# 1. Analyzer compiles
# --------------------------------------------------------------------------- #
def test_analyzer_compiles():
    compile(_read(_ANALYZER), _ANALYZER, "exec")


# --------------------------------------------------------------------------- #
# 2. Expected input files are referenced; no network
# --------------------------------------------------------------------------- #
def test_references_expected_inputs():
    mod = _import_analyzer()
    expected = {
        "SCORED_CSV": "research/output/phase2g_real_data_scored.csv",
        "PRICE_HISTORY_CSV": "research/output/phase2g_price_history_real.csv",
        "RUN_SUMMARY_JSON": "research/output/phase2g_c_real_data_run_summary.json",
        "INPUT_2IA_JSON": "research/output/phase2i_feature_ic_horizon_sweep.json",
        "INPUT_2J_JSON": "research/output/phase2j_research_decision.json",
        "INPUT_2KA_JSON": "research/output/phase2k_alpha_backlog.json",
    }
    for const, tail in expected.items():
        val = getattr(mod, const)
        assert val.replace("\\", "/").endswith(tail), \
            f"{const} must point at {tail}, got {val!r}"
    text = _read(_ANALYZER).lower()
    for tok in ("http://", "https://", "requests.", "urllib", "yfinance",
                "socket"):
        assert tok not in text, f"analyzer must not reach the network: {tok!r}"


# --------------------------------------------------------------------------- #
# 3. Writes only the Phase 2K-B residual-signal JSON
# --------------------------------------------------------------------------- #
def test_writes_only_residual_json():
    mod = _import_analyzer()
    assert mod.RESIDUAL_JSON.replace("\\", "/").endswith(
        "research/output/phase2k_b_residual_signal.json")
    text = _read(_ANALYZER)
    assert "to_csv" not in text, "analyzer must not write any CSV"
    assert "to_sql" not in text, "analyzer must not write to a database"
    assert text.count("open(") >= 1
    tree = ast.parse(text)
    write_opens = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
                    and "w" in str(node.args[1].value):
                write_opens += 1
    assert write_opens == 1, "exactly one write-open (the residual JSON)"


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
# 6. No deploy / gcloud / SSH / service / DB-write / migration logic
# --------------------------------------------------------------------------- #
def test_no_forbidden_infrastructure_logic():
    text = _read(_ANALYZER).lower()
    hits = [tok for tok in _FORBIDDEN_TOKENS if tok.lower() in text]
    assert not hits, f"analyzer contains forbidden token(s): {hits}"
    for tok in ("ssh ", "scp ", "uvicorn", "systemd", "restart"):
        assert tok not in text, f"analyzer contains forbidden token: {tok!r}"


# --------------------------------------------------------------------------- #
# 7. Output JSON: required fields and safety flags
# --------------------------------------------------------------------------- #
def test_output_json_required_fields():
    diag = _diag()
    on_disk = _DIAG_CACHE["on_disk"]
    for d in (diag, on_disk):
        for k in _REQUIRED_JSON_FIELDS:
            assert k in d, f"residual JSON missing field: {k}"
        assert d["phase"] == "2K-B"
        for k in _REQUIRED_FALSE:
            assert d[k] is False, f"{k} must be false"
        for k in _REQUIRED_TRUE:
            assert d[k] is True, f"{k} must be true"
        ir = d["inputs_read"]
        assert ir["scored_csv"].replace("\\", "/").endswith(
            "research/output/phase2g_real_data_scored.csv")
        assert ir["price_history_csv"].replace("\\", "/").endswith(
            "research/output/phase2g_price_history_real.csv")
        assert ir["phase2i_a_json"].replace("\\", "/").endswith(
            "research/output/phase2i_feature_ic_horizon_sweep.json")


# --------------------------------------------------------------------------- #
# 8. residualization_config exists and is well formed
# --------------------------------------------------------------------------- #
def test_residualization_config_present():
    cfg = _diag()["residualization_config"]
    for k in _REQUIRED_RESID_CONFIG_FIELDS:
        assert k in cfg, f"residualization_config missing field: {k}"
    assert cfg["horizons"] == [5, 10, 21, 63]
    # The controls regressed out are exactly the trailing risk controls.
    assert set(cfg["controls"]) == _EXPECTED_CONTROLS
    assert isinstance(cfg["min_names_for_ols"], int) and cfg["min_names_for_ols"] > 0


# --------------------------------------------------------------------------- #
# 9. A residual label exists for every horizon (5/10/21/63)
# --------------------------------------------------------------------------- #
def test_residual_labels_for_all_horizons():
    diag = _diag()
    cols = diag["residualization_config"]["residual_label_columns"]
    for h in _HORIZONS:
        assert h in cols, f"residual_label_columns missing horizon {h}"
        assert cols[h] == f"resid_{h}d"
    # Every feature block must be measured against all four residual horizons,
    # each carrying the residual-label diagnostics (rank IC + top-decile hit).
    for fname, block in diag["features"].items():
        for h in _HORIZONS:
            assert h in block["horizons"], \
                f"feature {fname!r} missing horizon {h}"
            cell = block["horizons"][h]
            assert "mean_rank_ic" in cell
            assert "top_decile_residual_hit_rate" in cell


# --------------------------------------------------------------------------- #
# 10. Candidate / control feature lists are correct
# --------------------------------------------------------------------------- #
def test_candidate_and_control_features():
    diag = _diag()
    assert set(diag["candidate_features"]) == _EXPECTED_CANDIDATES
    assert set(diag["control_features"]) == _EXPECTED_CONTROLS
    # No feature is both a candidate and a control.
    assert not (set(diag["candidate_features"]) & set(diag["control_features"]))


# --------------------------------------------------------------------------- #
# 11. Risk controls are marked CONTROL_ONLY
# --------------------------------------------------------------------------- #
def test_controls_marked_control_only():
    diag = _diag()
    by_feature = diag["keep_drop"]["by_feature"]
    for c in _EXPECTED_CONTROLS:
        assert by_feature[c] == "CONTROL_ONLY", \
            f"control {c!r} must be CONTROL_ONLY, got {by_feature.get(c)!r}"
        assert diag["features"][c]["recommendation"] == "CONTROL_ONLY"
        assert diag["features"][c]["role"] == "control"
    assert set(diag["keep_drop"]["control_only"]) == _EXPECTED_CONTROLS


# --------------------------------------------------------------------------- #
# 12. comparison_to_phase2i_a exists and is well formed
# --------------------------------------------------------------------------- #
def test_comparison_to_phase2i_a_present():
    cmp = _diag()["comparison_to_phase2i_a"]
    assert "by_feature" in cmp and isinstance(cmp["by_feature"], dict)
    # Every feature compared has both a raw and a residual best-IC slot.
    for fname, row in cmp["by_feature"].items():
        assert "raw_best_mean_rank_ic" in row
        assert "residual_best_mean_rank_ic" in row
        assert "ic_shrunk_toward_zero" in row
    assert isinstance(
        cmp["neutralization_removed_63d_risk_premium"], bool)
    assert isinstance(cmp["newly_surviving_residual_candidates"], list)
    # The control collapse is reported for every control feature.
    assert set(cmp["control_features_ic_collapse"].keys()) == _EXPECTED_CONTROLS


# --------------------------------------------------------------------------- #
# 13. keep_drop recommendations are drawn only from the allowed vocabulary
# --------------------------------------------------------------------------- #
def test_keep_drop_recommendations_allowed():
    kd = _diag()["keep_drop"]
    by_feature = kd["by_feature"]
    assert set(by_feature) == (_EXPECTED_CANDIDATES | _EXPECTED_CONTROLS)
    for f, rec in by_feature.items():
        assert rec in _ALLOWED_RECOMMENDATIONS, \
            f"feature {f!r} has disallowed recommendation {rec!r}"
    # The bucket lists partition every feature exactly once.
    buckets = (kd["keep_for_robustness_test"] + kd["drop"]
               + kd["need_more_data"] + kd["control_only"])
    assert sorted(buckets) == sorted(_EXPECTED_CANDIDATES | _EXPECTED_CONTROLS)


# --------------------------------------------------------------------------- #
# 14. interpretation + recommended_next_phase exist and are consistent
# --------------------------------------------------------------------------- #
def test_recommended_next_phase_present():
    diag = _diag()
    interp = diag["interpretation"]
    assert "any_residual_alpha_candidate_survives" in interp
    assert "beta_vol_neutralization_eliminates_63d_signal" in interp
    assert interp["production_edge_claimed"] is False

    nxt = diag["recommended_next_phase"]
    for k in _REQUIRED_NEXT_PHASE_FIELDS:
        assert k in nxt, f"recommended_next_phase missing field: {k}"
    assert nxt["phase"] in _ALLOWED_NEXT_PHASES, \
        f"unexpected next phase {nxt['phase']!r}"
    assert isinstance(nxt["title"], str) and nxt["title"].strip()
    assert isinstance(nxt["purpose"], str) and nxt["purpose"].strip()
    # The branch must match the survival verdict.
    survives = bool(interp["any_residual_alpha_candidate_survives"])
    assert nxt["phase"] == ("2K-C" if survives else "2K-D")


# --------------------------------------------------------------------------- #
# 15. Doc has all required guardrail phrases
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
