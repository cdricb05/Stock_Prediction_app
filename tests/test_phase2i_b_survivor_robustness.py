"""Phase 2I-B tests for the Survivor Robustness & Regime Validation analyzer.

These tests prove the analyzer is observational and read-only: it compiles and
imports without side effects; it reads only the local Phase 2G artifacts (through
the Phase 2I-A analyzer) plus the Phase 2I-A diagnostics JSON; it writes only
research/output/phase2i_b_survivor_robustness.json; it never imports api_server or
Paper Trader; its source contains no deploy / gcloud / SSH / service / DB-write /
migration logic; the diagnostics JSON it produces carries every required field,
every required safety flag, the six expected Phase 2I-A survivors, the six
robustness / regime / concentration diagnostic categories, and a per-survivor
recommendation drawn from the allowed set; and the companion doc contains the
required guardrail phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase2i_b_survivor_robustness.py
  * without pytest: python tests/test_phase2i_b_survivor_robustness.py
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
    _REPO_ROOT, "research", "analyze_phase2i_b_survivor_robustness.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase2i_b_survivor_robustness_v1.md")
_OUTPUT_JSON = os.path.join(
    _REPO_ROOT, "research", "output", "phase2i_b_survivor_robustness.json")

# The six Phase 2I-A KEEP survivors this phase must validate (all at 63d).
_EXPECTED_SURVIVORS = {
    "realized_vol_21d", "rolling_beta_63d", "downside_vol_21d",
    "realized_vol_63d", "rolling_corr_spy_63d", "momentum_12_1",
}

_ALLOWED_RECOMMENDATIONS = {
    "KEEP_FOR_MODEL", "KEEP_AS_RISK_FILTER_ONLY", "DROP", "NEED_MORE_DATA",
}

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

# Required top-level fields of the diagnostics JSON.
_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read", "config", "provenance",
    "panel", "survivors_tested", "survivors", "interpretation",
)
_REQUIRED_CONFIG_FIELDS = (
    "survivor_horizons", "bootstrap_n", "permutation_n", "ci_alpha",
    "perm_alpha", "seed", "rolling_window_sessions", "min_effective_obs",
    "concentration_top3_threshold", "risk_features", "recommendation_rule",
)
_REQUIRED_SURVIVOR_FIELDS = (
    "feature", "horizon_days", "edge_type", "phase2i_a", "overall",
    "stability", "regimes", "regime_dependent", "robustness",
    "concentration", "recommendation",
)
_REQUIRED_OVERALL_FIELDS = (
    "mean_rank_ic", "std_rank_ic", "information_ratio", "direction",
    "n_dates", "n_rows",
)
_REQUIRED_STABILITY_FIELDS = ("yearly", "quarterly", "rolling_6m", "sign_stability")
_REQUIRED_REGIME_FIELDS = (
    "spy_forward_return", "market_volatility_21d",
    "feature_cross_sectional_dispersion",
)
_REQUIRED_ROBUSTNESS_FIELDS = (
    "bootstrap_mean_ic_ci", "permutation_null", "leave_one_year_out",
    "leave_one_ticker_out",
)
_REQUIRED_INTERP_FIELDS = (
    "edge_character_dominant", "edge_types", "recommendation_counts",
    "supports_long_horizon_model", "robust_enough_for_phase2j",
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
    spec = importlib.util.spec_from_file_location("phase2i_b_analyzer", _ANALYZER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The full analysis is expensive, so run it at most once per test process and
# cache both the returned dict and the on-disk JSON; every structure test reuses
# this instead of re-running the analysis.
_DIAG_CACHE = {}


def _diag():
    if "diag" not in _DIAG_CACHE:
        mod = _import_analyzer()
        out = os.path.join(tempfile.mkdtemp(), "diag.json")
        _DIAG_CACHE["diag"] = mod.run(output_path=out)
        _DIAG_CACHE["on_disk"] = json.loads(_read(out))
    return _DIAG_CACHE["diag"]


# --------------------------------------------------------------------------- #
# 1. Analyzer compiles
# --------------------------------------------------------------------------- #
def test_analyzer_compiles():
    compile(_read(_ANALYZER), _ANALYZER, "exec")


# --------------------------------------------------------------------------- #
# 2. Reads only local artifacts, no network
# --------------------------------------------------------------------------- #
def test_reads_only_local_files():
    mod = _import_analyzer()
    assert mod.INPUT_2IA_JSON.replace("\\", "/").endswith(
        "research/output/phase2i_feature_ic_horizon_sweep.json")
    for attr, leaf in (
            ("SCORED_CSV", "phase2g_real_data_scored.csv"),
            ("PRICE_HISTORY_CSV", "phase2g_price_history_real.csv"),
            ("RUN_SUMMARY_JSON", "phase2g_c_real_data_run_summary.json")):
        val = getattr(mod, attr)
        assert val.endswith(leaf), f"{attr} must point at {leaf}"
    text = _read(_ANALYZER).lower()
    for tok in ("http://", "https://", "requests.", "urllib", "yfinance",
                "socket"):
        assert tok not in text, f"analyzer must not reach the network: {tok!r}"


# --------------------------------------------------------------------------- #
# 3. Writes only the Phase 2I-B diagnostics JSON
# --------------------------------------------------------------------------- #
def test_writes_only_diagnostics_json():
    mod = _import_analyzer()
    assert mod.DIAGNOSTICS_JSON.replace("\\", "/").endswith(
        "research/output/phase2i_b_survivor_robustness.json")
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
# 6. No deploy / gcloud / SSH / service / DB-write / migration logic
# --------------------------------------------------------------------------- #
def test_no_forbidden_infrastructure_logic():
    text = _read(_ANALYZER).lower()
    hits = [tok for tok in _FORBIDDEN_TOKENS if tok.lower() in text]
    assert not hits, f"analyzer contains forbidden token(s): {hits}"
    for tok in ("ssh ", "scp ", "uvicorn", "systemd", "restart"):
        assert tok not in text, f"analyzer contains forbidden token: {tok!r}"


# --------------------------------------------------------------------------- #
# 7. Output JSON: required fields, safety flags, config
# --------------------------------------------------------------------------- #
def test_output_json_required_fields():
    diag = _diag()
    on_disk = _DIAG_CACHE["on_disk"]
    for d in (diag, on_disk):
        for k in _REQUIRED_JSON_FIELDS:
            assert k in d, f"diagnostics missing field: {k}"
        assert d["phase"] == "2I-B"
        for k in _REQUIRED_FALSE:
            assert d[k] is False, f"{k} must be false"
        for k in _REQUIRED_TRUE:
            assert d[k] is True, f"{k} must be true"
        for k in _REQUIRED_CONFIG_FIELDS:
            assert k in d["config"], f"config missing field: {k}"
        ir = d["inputs_read"]
        assert ir["phase2i_a_json"].replace("\\", "/").endswith(
            "research/output/phase2i_feature_ic_horizon_sweep.json")


# --------------------------------------------------------------------------- #
# 8. Expected survivors present, each with the full diagnostic schema
# --------------------------------------------------------------------------- #
def test_expected_survivors_and_schema():
    diag = _diag()
    tested = set(diag["survivors_tested"])
    assert tested == _EXPECTED_SURVIVORS, \
        f"survivors mismatch: {tested ^ _EXPECTED_SURVIVORS}"
    seen = set()
    for s in diag["survivors"]:
        seen.add(s["feature"])
        for k in _REQUIRED_SURVIVOR_FIELDS:
            assert k in s, f"{s['feature']} missing field: {k}"
        # All Phase 2I-A survivors are strongest at the 63d horizon.
        assert s["horizon_days"] == 63, f"{s['feature']} expected 63d horizon"
        for k in _REQUIRED_OVERALL_FIELDS:
            assert k in s["overall"], f"{s['feature']} overall missing: {k}"
        for k in _REQUIRED_STABILITY_FIELDS:
            assert k in s["stability"], f"{s['feature']} stability missing: {k}"
        for k in _REQUIRED_REGIME_FIELDS:
            assert k in s["regimes"], f"{s['feature']} regimes missing: {k}"
        for k in _REQUIRED_ROBUSTNESS_FIELDS:
            assert k in s["robustness"], f"{s['feature']} robustness missing: {k}"
        assert isinstance(s["stability"]["yearly"], list)
        assert isinstance(s["stability"]["quarterly"], list)
        assert isinstance(s["regime_dependent"], bool)
    assert seen == _EXPECTED_SURVIVORS


# --------------------------------------------------------------------------- #
# 9. Robustness sub-blocks carry the required statistics
# --------------------------------------------------------------------------- #
def test_robustness_blocks_complete():
    diag = _diag()
    for s in diag["survivors"]:
        rb = s["robustness"]
        ci = rb["bootstrap_mean_ic_ci"]
        for k in ("method", "block_len_sessions", "n_resamples", "ci_low",
                  "ci_high", "excludes_zero"):
            assert k in ci, f"{s['feature']} bootstrap CI missing: {k}"
        assert ci["block_len_sessions"] == 63, "block length must equal the horizon"
        assert isinstance(ci["excludes_zero"], bool)
        perm = rb["permutation_null"]
        for k in ("method", "n_permutations", "p_value", "significant"):
            assert k in perm, f"{s['feature']} permutation missing: {k}"
        assert isinstance(perm["significant"], bool)
        loyo = rb["leave_one_year_out"]
        assert "per_year" in loyo and isinstance(loyo["per_year"], list)
        assert isinstance(loyo["sign_stable"], bool)
        assert "leave_one_ticker_out" in rb


# --------------------------------------------------------------------------- #
# 10. Each survivor gets an allowed recommendation; interpretation is complete
# --------------------------------------------------------------------------- #
def test_recommendations_and_interpretation():
    diag = _diag()
    counts = {r: 0 for r in _ALLOWED_RECOMMENDATIONS}
    for s in diag["survivors"]:
        rec = s["recommendation"]
        assert rec["recommendation"] in _ALLOWED_RECOMMENDATIONS, \
            f"{s['feature']} has invalid recommendation {rec['recommendation']!r}"
        assert isinstance(rec["reasons"], list) and rec["reasons"]
        counts[rec["recommendation"]] += 1

    interp = diag["interpretation"]
    for k in _REQUIRED_INTERP_FIELDS:
        assert k in interp, f"interpretation missing field: {k}"
    assert isinstance(interp["supports_long_horizon_model"], bool)
    assert isinstance(interp["robust_enough_for_phase2j"], bool)
    # The interpretation's recommendation tally agrees with the per-survivor recs.
    rc = interp["recommendation_counts"]
    for r in _ALLOWED_RECOMMENDATIONS:
        assert rc.get(r, 0) == counts[r], f"recommendation_counts mismatch for {r}"


# --------------------------------------------------------------------------- #
# 11. Concentration check present (dominant-name / top-contributor surface)
# --------------------------------------------------------------------------- #
def test_concentration_present():
    diag = _diag()
    for s in diag["survivors"]:
        conc = s["concentration"]
        assert conc is not None, f"{s['feature']} missing concentration block"
        for k in ("overly_concentrated", "top3_long_leg_share",
                  "top_contributors"):
            assert k in conc, f"{s['feature']} concentration missing: {k}"
        assert isinstance(conc["overly_concentrated"], bool)
        assert isinstance(conc["top_contributors"], list)


# --------------------------------------------------------------------------- #
# 12. Doc has all required guardrail phrases
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
