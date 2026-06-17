"""Phase 2K-C tests for the Residual Alpha Robustness Validation analyzer.

These tests prove the analyzer is observational and read-only: it compiles and
imports without side effects; it references only the Phase 2K-B / 2K-A / 2J-A JSON
summaries and the local Phase 2G real-data artifacts; it writes only
research/output/phase2k_c_residual_robustness.json; it never imports api_server or
Paper Trader; its source contains no deploy / gcloud / SSH / service / DB-write /
migration logic; and the diagnostics JSON it produces carries every required field
and safety flag, a residualization config, a candidates_tested list matching the
Phase 2K-B keep_for_robustness_test shortlist, a robustness block (bootstrap /
permutation / leave-one-year-out) plus concentration and regime-test blocks for
every candidate, recommendations drawn only from the allowed vocabulary, and a
recommended next phase consistent with those recommendations. The companion doc
must contain the guardrail phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase2k_c_residual_robustness.py
  * without pytest: python tests/test_phase2k_c_residual_robustness.py
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
    _REPO_ROOT, "research", "analyze_phase2k_c_residual_robustness.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase2k_c_residual_robustness_v1.md")
_INPUT_2KB_JSON = os.path.join(
    _REPO_ROOT, "research", "output", "phase2k_b_residual_signal.json")

# The only recommendation values recommendation_summary may emit.
_ALLOWED_RECOMMENDATIONS = {
    "KEEP_FOR_MODEL_RESEARCH", "KEEP_AS_RESEARCH_LEAD_ONLY", "DROP",
    "NEED_MORE_DATA",
}
# Allowed recommended-next-phase ids: 2L-A walk-forward model research design
# (a survivor exists) or 2K-D data acquisition (none). Never a model-training
# or deployment phase here.
_ALLOWED_NEXT_PHASES = {"2L-A", "2K-D"}

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

# Required top-level fields of the robustness JSON.
_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read", "residualization_config",
    "candidates_tested", "panel", "robustness", "concentration", "regime_tests",
    "recommendation_summary", "interpretation", "recommended_next_phase",
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
_EXPECTED_CONTROLS = {
    "rolling_beta_63d", "realized_vol_21d", "realized_vol_63d",
    "downside_vol_21d", "rolling_corr_spy_63d",
}


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _import_analyzer():
    spec = importlib.util.spec_from_file_location("phase2k_c_analyzer", _ANALYZER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Run the analysis at most once per test process and cache both the returned
# dict and the on-disk JSON.
_DIAG_CACHE = {}


def _diag():
    if "diag" not in _DIAG_CACHE:
        mod = _import_analyzer()
        out = os.path.join(tempfile.mkdtemp(), "robustness.json")
        _DIAG_CACHE["diag"] = mod.run(output_path=out)
        _DIAG_CACHE["on_disk"] = json.loads(_read(out))
    return _DIAG_CACHE["diag"]


def _phase2k_b_keep():
    """The Phase 2K-B residual shortlist the analyzer must validate."""
    twokb = json.loads(_read(_INPUT_2KB_JSON))
    return sorted(twokb["keep_drop"]["keep_for_robustness_test"])


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
        "INPUT_2KB_JSON": "research/output/phase2k_b_residual_signal.json",
        "INPUT_2KA_JSON": "research/output/phase2k_alpha_backlog.json",
        "INPUT_2J_JSON": "research/output/phase2j_research_decision.json",
        "SCORED_CSV": "research/output/phase2g_real_data_scored.csv",
        "PRICE_HISTORY_CSV": "research/output/phase2g_price_history_real.csv",
        "RUN_SUMMARY_JSON": "research/output/phase2g_c_real_data_run_summary.json",
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
# 3. Writes only the Phase 2K-C robustness JSON
# --------------------------------------------------------------------------- #
def test_writes_only_robustness_json():
    mod = _import_analyzer()
    assert mod.ROBUSTNESS_JSON.replace("\\", "/").endswith(
        "research/output/phase2k_c_residual_robustness.json")
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
    assert write_opens == 1, "exactly one write-open (the robustness JSON)"


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
            assert k in d, f"robustness JSON missing field: {k}"
        assert d["phase"] == "2K-C"
        for k in _REQUIRED_FALSE:
            assert d[k] is False, f"{k} must be false"
        for k in _REQUIRED_TRUE:
            assert d[k] is True, f"{k} must be true"
        ir = d["inputs_read"]
        assert ir["phase2k_b_json"].replace("\\", "/").endswith(
            "research/output/phase2k_b_residual_signal.json")
        assert ir["scored_csv"].replace("\\", "/").endswith(
            "research/output/phase2g_real_data_scored.csv")
        assert ir["price_history_csv"].replace("\\", "/").endswith(
            "research/output/phase2g_price_history_real.csv")


# --------------------------------------------------------------------------- #
# 8. residualization_config exists and is well formed
# --------------------------------------------------------------------------- #
def test_residualization_config_present():
    cfg = _diag()["residualization_config"]
    for k in _REQUIRED_RESID_CONFIG_FIELDS:
        assert k in cfg, f"residualization_config missing field: {k}"
    assert cfg["horizons"] == [5, 10, 21, 63]
    assert set(cfg["controls"]) == _EXPECTED_CONTROLS
    assert isinstance(cfg["min_names_for_ols"], int) and cfg["min_names_for_ols"] > 0
    for h in ("5", "10", "21", "63"):
        assert cfg["residual_label_columns"][h] == f"resid_{h}d"


# --------------------------------------------------------------------------- #
# 9. candidates_tested matches the Phase 2K-B keep_for_robustness_test shortlist
# --------------------------------------------------------------------------- #
def test_candidates_match_phase2k_b():
    diag = _diag()
    expected = _phase2k_b_keep()
    assert sorted(diag["candidates_tested"]) == expected, \
        f"candidates_tested {diag['candidates_tested']} != 2K-B keep {expected}"
    # Every tested candidate has a full per-candidate analysis block.
    analyzed = {c["feature"] for c in diag["candidates"]}
    assert analyzed == set(expected)


# --------------------------------------------------------------------------- #
# 10. A robustness block (bootstrap / permutation / LOYO / LOTO) per candidate
# --------------------------------------------------------------------------- #
def test_robustness_blocks_per_candidate():
    diag = _diag()
    expected = set(_phase2k_b_keep())
    rob = diag["robustness"]
    assert set(rob.keys()) == expected, \
        "robustness must have a block for every candidate"
    for feature, block in rob.items():
        assert "bootstrap_mean_ic_ci" in block, f"{feature} missing bootstrap block"
        assert "permutation_null" in block, f"{feature} missing permutation block"
        assert "leave_one_year_out" in block, f"{feature} missing leave-one-year-out"
        assert "leave_one_ticker_out" in block, f"{feature} missing leave-one-ticker-out"
        boot = block["bootstrap_mean_ic_ci"]
        assert "ci_low" in boot and "ci_high" in boot and "excludes_zero" in boot
        assert "p_value" in block["permutation_null"]
        assert "significant" in block["permutation_null"]
        assert "sign_stable" in block["leave_one_year_out"]


# --------------------------------------------------------------------------- #
# 11. Concentration and regime-test blocks exist for every candidate
# --------------------------------------------------------------------------- #
def test_concentration_and_regime_blocks_present():
    diag = _diag()
    expected = set(_phase2k_b_keep())
    assert set(diag["concentration"].keys()) == expected
    assert set(diag["regime_tests"].keys()) == expected
    for feature, reg in diag["regime_tests"].items():
        assert "spy_forward_return" in reg, f"{feature} missing SPY-regime split"
        assert "market_volatility_21d" in reg, f"{feature} missing market-vol split"


# --------------------------------------------------------------------------- #
# 12. Recommendations are drawn only from the allowed vocabulary
# --------------------------------------------------------------------------- #
def test_recommendations_allowed():
    rs = _diag()["recommendation_summary"]
    by_feature = rs["by_feature"]
    expected = set(_phase2k_b_keep())
    assert set(by_feature) == expected
    for f, rec in by_feature.items():
        assert rec in _ALLOWED_RECOMMENDATIONS, \
            f"feature {f!r} has disallowed recommendation {rec!r}"
    # The bucket lists partition every candidate exactly once.
    buckets = (rs["keep_for_model_research"] + rs["keep_as_research_lead_only"]
               + rs["drop"] + rs["need_more_data"])
    assert sorted(buckets) == sorted(expected)
    # Per-candidate recommendation matches the summary mapping.
    for c in _diag()["candidates"]:
        assert c["recommendation"]["recommendation"] == by_feature[c["feature"]]


# --------------------------------------------------------------------------- #
# 13. interpretation + recommended_next_phase exist and are consistent
# --------------------------------------------------------------------------- #
def test_recommended_next_phase_consistent():
    diag = _diag()
    interp = diag["interpretation"]
    assert "any_candidate_survives_robustness" in interp
    assert "edge_concentrated_in_few_tickers" in interp
    assert "edge_regime_dependent" in interp
    assert interp["production_edge_claimed"] is False

    nxt = diag["recommended_next_phase"]
    for k in _REQUIRED_NEXT_PHASE_FIELDS:
        assert k in nxt, f"recommended_next_phase missing field: {k}"
    assert nxt["phase"] in _ALLOWED_NEXT_PHASES, \
        f"unexpected next phase {nxt['phase']!r}"
    assert isinstance(nxt["title"], str) and nxt["title"].strip()
    assert isinstance(nxt["purpose"], str) and nxt["purpose"].strip()
    # If any candidate is KEEP_FOR_MODEL_RESEARCH -> 2L-A; otherwise 2K-D.
    counts = diag["recommendation_summary"]["counts"]
    any_model = counts["KEEP_FOR_MODEL_RESEARCH"] >= 1
    assert nxt["phase"] == ("2L-A" if any_model else "2K-D")
    assert bool(interp["any_candidate_survives_robustness"]) == any_model


# --------------------------------------------------------------------------- #
# 14. Doc has all required guardrail phrases
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
