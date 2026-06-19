"""Phase 3-B tests for the Greenfield Feature and Label Refinement analyzer.

These tests prove the analyzer is a disciplined diagnostic / refinement phase: it compiles and
imports; it references the committed Phase 3-A artifacts (and the D: panel paths used for the
SPY regime read); it reads the Phase 3-A result and confirms its recommendation was
GREENFIELD_BASELINE_WEAK_BUT_IMPROVABLE; it identifies at least one catastrophic fold; it carries
every required output field and safety flag; phase == "3-B"; it trains NO model
(research_model_trained / production_model_trained / production_model_candidate_created /
deployable_model_artifact_written all false); the refined-config JSON exists with the required
keys; the recommendation uses only the allowed values and routes to Phase 3-C; and it imports no
api_server / Paper Trader / ML framework and contains no infrastructure, database, deployment,
order/trading, network, or model-artifact usage tokens. The doc carries the required guardrail
phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase3b_greenfield_refinement.py
  * without pytest: python tests/test_phase3b_greenfield_refinement.py
The __main__ block discovers every test_* function, runs it, prints PASS/FAIL/SKIP, and exits
non-zero on any failure (the GCP venv has no pytest). The optional live end-to-end run is gated
behind PHASE3B_LIVE=1.
"""
from __future__ import annotations

import ast
import csv
import importlib.util
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_ANALYZER = os.path.join(_REPO_ROOT, "research", "analyze_phase3b_greenfield_refinement.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase3b_greenfield_refinement_v1.md")
_RESULTS = os.path.join(_REPO_ROOT, "research", "output", "phase3b_greenfield_refinement.json")
_FAMILY_CSV = os.path.join(
    _REPO_ROOT, "research", "output", "phase3b_feature_family_diagnostics.csv")
_FOLD_CSV = os.path.join(_REPO_ROOT, "research", "output", "phase3b_fold_diagnostics.csv")
_REFINED_CONFIG = os.path.join(_REPO_ROOT, "research", "output", "phase3b_refined_config.json")
_PHASE3A_JSON = os.path.join(_REPO_ROOT, "research", "output", "phase3a_greenfield_baseline.json")


class _Skip(Exception):
    """Raised to mark a test skipped."""


# The inputs this phase must reference (basenames): the Phase 3-A artifacts + the D: panel paths
# used for the SPY regime read.
_EXPECTED_INPUT_BASENAMES = (
    "phase3a_greenfield_baseline.json",
    "phase3a_greenfield_feature_summary.csv",
    "phase3a_greenfield_walkforward_summary.csv",
    "phase2k_p_sector_map_current.csv",
    "phase2k_g_expanded_price_history_free.csv",
    "phase2k_g_survivorship_caveat.json",
)

# Forbidden infrastructure tokens (assembled from fragments so they never self-match prose).
_FORBIDDEN_INFRA_TOKENS = [
    "gcl" + "oud",
    "sub" + "process",
    "os." + "system",
    "para" + "miko",
    "system" + "ctl",
    "kube" + "ctl",
    "stock-api." + "service",
    "alem" + "bic",
    "PREDICTOR_USE_" + "MODEL_V2",
]
# Forbidden database-write tokens.
_FORBIDDEN_DB_TOKENS = [
    "insert " + "into", "delete " + "from", "drop " + "table", "alter " + "table",
    "create " + "table", "trun" + "cate", "to_" + "sql",
]
# Forbidden deployment / order / trading usage tokens.
_FORBIDDEN_DEPLOY_TRADE_TOKENS = [
    "place" + "_order", "submit" + "_order", "create" + "_order",
    "ssh ", "scp ", "uvi" + "corn", "systemd",
]
# Forbidden network / acquisition tokens.
_FORBIDDEN_NETWORK_TOKENS = [
    "http" + "://", "https" + "://", "requests." + "get", "requests." + "post",
    "url" + "lib", "yf" + "inance", "soc" + "ket", "url" + "open", "down" + "load(",
]
# Forbidden deployable-model-artifact tokens (this phase trains nothing and persists no model).
_FORBIDDEN_ARTIFACT_TOKENS = [
    "to_" + "pickle", "pickle." + "dump", "joblib." + "dump", "import " + "pickle",
    "import " + "joblib", ".pk" + "l", ".job" + "lib",
]
# ML frameworks that must not be imported (no model is trained at all here).
_FORBIDDEN_IMPORT_ROOTS = {
    "sklearn", "tensorflow", "torch", "keras", "lightgbm", "xgboost", "statsmodels",
    "api_server",
}

_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read", "phase3a_summary", "catastrophic_fold_diagnostics",
    "fold_diagnostics", "feature_family_diagnostics", "horizon_diagnostics",
    "sector_risk_diagnostics", "refined_config_summary", "recommendation", "interpretation",
    "recommended_next_phase",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_edge_claimed",
    "research_model_trained", "production_model_trained", "production_model_candidate_created",
    "deployable_model_artifact_written", "d_drive_written", "network_used",
)
_REQUIRED_TRUE = (
    "no_trading", "no_orders", "no_automation",
)
_ALLOWED_RECOMMENDATIONS = {
    "PROCEED_TO_REFINED_GREENFIELD_RERUN",
    "NEED_MORE_DIAGNOSTICS_BEFORE_RERUN",
    "PRICE_VOLUME_MODELING_TOO_UNSTABLE",
    "GREENFIELD_REFINEMENT_BLOCKED",
}
_REQUIRED_REFINED_CONFIG_KEYS = (
    "selected_models", "selected_horizons", "selected_feature_families",
    "pruned_feature_families", "required_feature_transforms", "fold_handling_notes",
    "risk_controls", "sector_controls", "validation_changes", "kill_switch_rules",
)
_REQUIRED_DOC_PHRASES = [
    "does not deploy",
    "does not restart stock-api.service",
    "does not enable",
    "does not run migrations",
    "does not write to production DB",
    "does not trade",
    "production edge",
]


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _read_csv_rows(path: str):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _import_analyzer():
    spec = importlib.util.spec_from_file_location("phase3b_analyzer_test", _ANALYZER)
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
# 2. Expected inputs + D: paths referenced; outputs under research/output (C:)
# --------------------------------------------------------------------------- #
def test_references_expected_inputs_and_d_paths():
    analyzer = _import_analyzer()
    text = _read(_ANALYZER)
    for base in _EXPECTED_INPUT_BASENAMES:
        assert base in text, f"analyzer does not reference expected input: {base}"
    for path in (analyzer.RESULTS_JSON, analyzer.FEATURE_FAMILY_CSV,
                 analyzer.FOLD_DIAGNOSTICS_CSV, analyzer.REFINED_CONFIG_JSON):
        n = path.replace("\\", "/")
        assert n.startswith("research/output/"), f"output must live under research/output: {n}"
        assert not n.upper().startswith("D:"), f"output must not be on the D: drive: {n}"
    # The D: panel path used for the (optional) SPY regime read resolves to the D: drive.
    assert analyzer.EXPANDED_PRICE_HISTORY_CSV.replace("\\", "/").upper().startswith("D:/")


# --------------------------------------------------------------------------- #
# 3. No api_server / Paper Trader / ML-framework imports
# --------------------------------------------------------------------------- #
def test_no_forbidden_imports():
    text = _read(_ANALYZER)
    assert "paper_trader" not in text.lower(), "analyzer must not reference paper_trader"
    assert "import api_server" not in text and "from api_server" not in text
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = ([n.name for n in node.names]
                    + ([node.module] if isinstance(node, ast.ImportFrom)
                       and node.module else []))
            roots = {(m or "").split(".")[0] for m in mods}
            bad = roots & _FORBIDDEN_IMPORT_ROOTS
            assert not bad, f"analyzer imports forbidden module(s): {bad}"


# --------------------------------------------------------------------------- #
# 4. No infrastructure / db / deploy / trade / network / artifact usage tokens
# --------------------------------------------------------------------------- #
def test_no_forbidden_usage_tokens():
    low = _read(_ANALYZER).lower()
    for label, tokens in (
        ("infrastructure", _FORBIDDEN_INFRA_TOKENS),
        ("database-write", _FORBIDDEN_DB_TOKENS),
        ("deploy/trade", _FORBIDDEN_DEPLOY_TRADE_TOKENS),
        ("network", _FORBIDDEN_NETWORK_TOKENS),
        ("model-artifact", _FORBIDDEN_ARTIFACT_TOKENS),
    ):
        hits = [t for t in tokens if t.lower() in low]
        assert not hits, f"analyzer contains forbidden {label} token(s): {hits}"


# --------------------------------------------------------------------------- #
# 5. Committed results artifact: required fields, safety flags, structure
# --------------------------------------------------------------------------- #
def test_committed_results_artifact_valid():
    assert os.path.isfile(_RESULTS), "committed results JSON must exist"
    d = json.loads(_read(_RESULTS))
    for k in _REQUIRED_JSON_FIELDS:
        assert k in d, f"committed results JSON missing field: {k}"
    assert d["phase"] == "3-B"
    for k in _REQUIRED_FALSE:
        assert d[k] is False, f"{k} must be false"
    for k in _REQUIRED_TRUE:
        assert d[k] is True, f"{k} must be true"
    # d_drive_read may be true (SPY regime read) or false (panel absent), but must be a bool.
    assert isinstance(d["d_drive_read"], bool)


def test_reads_phase3a_and_confirms_recommendation():
    if not os.path.isfile(_RESULTS):
        raise _Skip("committed results JSON not present")
    d = json.loads(_read(_RESULTS))
    summary = d["phase3a_summary"]
    assert summary["phase3a_recommendation"] == "GREENFIELD_BASELINE_WEAK_BUT_IMPROVABLE"
    confirmed = summary["phase3a_confirmed"]
    assert confirmed["phase_is_3a"] is True
    assert confirmed["recommendation_is_weak_but_improvable"] is True
    assert confirmed["recommended_next_phase_is_3b"] is True
    assert confirmed["research_model_trained_true"] is True
    assert confirmed["production_model_trained_false"] is True
    assert confirmed["production_model_candidate_created_false"] is True
    assert confirmed["deployable_model_artifact_written_false"] is True


def test_identifies_at_least_one_catastrophic_fold():
    d = json.loads(_read(_RESULTS))
    cat = d["catastrophic_fold_diagnostics"]
    assert cat["n_catastrophic_folds"] >= 1, "must identify at least one catastrophic fold"
    assert cat["catastrophic_fold_ids"], "must list the catastrophic fold ids"
    worst = cat["worst_fold"]
    assert worst is not None and worst["mean_rank_ic"] is not None
    assert worst["mean_rank_ic"] < -0.05, "worst fold should be catastrophic (< -0.05)"
    # Every diagnosed fold carries a classification.
    folds = d["fold_diagnostics"]
    assert isinstance(folds, list) and len(folds) >= 2
    for fdg in folds:
        assert fdg["classification"] in (
            "catastrophic", "weak_negative", "positive", "no_data")


def test_recommendation_and_next_phase():
    d = json.loads(_read(_RESULTS))
    rec = d["recommendation"]
    assert rec["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert rec["create_production_model_candidate_now"] is False
    assert rec["train_production_model_now"] is False
    assert rec["deploy_now"] is False
    assert rec["production_edge_claimed"] is False
    assert isinstance(rec.get("reason"), str) and rec["reason"]
    assert d["recommended_next_phase"]["phase"] == "3-C"
    assert d["recommended_next_phase"]["title"]


def test_interpretation_flags_no_training():
    d = json.loads(_read(_RESULTS))
    interp = d["interpretation"]
    assert interp["research_model_trained"] is False
    assert interp["production_model_trained"] is False
    assert interp["production_model_candidate_created"] is False
    assert interp["deployable_model_artifact_written"] is False
    assert interp["retrained_any_model"] is False
    assert interp["wrote_to_d_drive"] is False
    assert interp["fetched_data_from_network"] is False
    assert interp["production_edge_claimed"] is False


# --------------------------------------------------------------------------- #
# 6. Feature-family + horizon diagnostics structure
# --------------------------------------------------------------------------- #
def test_feature_family_and_horizon_diagnostics():
    d = json.loads(_read(_RESULTS))
    ff = d["feature_family_diagnostics"]
    fams = {r["family"] for r in ff["by_family"]}
    for fam in ("price_momentum", "reversal", "volatility_risk", "volume_liquidity",
                "market_relative", "sector_relative", "cross_sectional_ranks"):
        assert fam in fams, f"missing feature family: {fam}"
    summ = ff["summary"]
    assert summ["strongest_positive_families"]
    assert summ["redundant_families"]
    assert "cross_sectional_ranks" in summ["families_to_prune"]
    hd = d["horizon_diagnostics"]
    assert set(hd["by_horizon"].keys()) == {"5", "21", "63"}
    assert "overlapping_label_risk" in hd


# --------------------------------------------------------------------------- #
# 7. Refined config JSON exists with the required keys
# --------------------------------------------------------------------------- #
def test_refined_config_exists_and_complete():
    assert os.path.isfile(_REFINED_CONFIG), "refined config JSON must exist"
    cfg = json.loads(_read(_REFINED_CONFIG))
    for k in _REQUIRED_REFINED_CONFIG_KEYS:
        assert k in cfg, f"refined config missing key: {k}"
    assert cfg["for_next_phase"] == "3-C"
    assert cfg["kill_switch_rules"], "kill switch rules must be present"
    assert "cross_sectional_ranks" in cfg["pruned_feature_families"]


# --------------------------------------------------------------------------- #
# 8. Output CSVs exist and are well-formed
# --------------------------------------------------------------------------- #
def test_feature_family_csv_well_formed():
    if not os.path.isfile(_FAMILY_CSV):
        raise _Skip("feature family CSV not present")
    rows = _read_csv_rows(_FAMILY_CSV)
    assert len(rows) == 7, "one row per feature family"
    for col in ("family", "n_features", "mean_abs_full_sample_ic", "recommended_action"):
        assert col in rows[0], f"feature family CSV missing column: {col}"


def test_fold_diagnostics_csv_well_formed():
    if not os.path.isfile(_FOLD_CSV):
        raise _Skip("fold diagnostics CSV not present")
    rows = _read_csv_rows(_FOLD_CSV)
    assert len(rows) >= 2
    for col in ("fold", "val_start", "val_end", "mean_rank_ic", "classification",
                "top_quintile_max_sector_share", "regime_label"):
        assert col in rows[0], f"fold diagnostics CSV missing column: {col}"
    assert any(r["classification"] == "catastrophic" for r in rows)


# --------------------------------------------------------------------------- #
# 9. Doc has all required guardrail phrases
# --------------------------------------------------------------------------- #
def test_doc_has_required_phrases():
    text = _read(_DOC)
    missing = [p for p in _REQUIRED_DOC_PHRASES if p not in text]
    assert not missing, f"doc missing required phrase(s): {missing}"


# --------------------------------------------------------------------------- #
# 10. Optional live end-to-end run (gated)
# --------------------------------------------------------------------------- #
def test_live_end_to_end_optional():
    if os.environ.get("PHASE3B_LIVE") != "1":
        raise _Skip("set PHASE3B_LIVE=1 to run the analyzer end to end")
    if not os.path.isfile(_PHASE3A_JSON):
        raise _Skip("Phase 3-A results JSON not present in this environment")
    import tempfile
    analyzer = _import_analyzer()
    tmp = tempfile.mkdtemp(prefix="phase3b_")
    res = analyzer.run(
        results_path=os.path.join(tmp, "refine.json"),
        feature_family_path=os.path.join(tmp, "family.csv"),
        fold_diagnostics_path=os.path.join(tmp, "folds.csv"),
        refined_config_path=os.path.join(tmp, "config.json"))
    assert res["phase"] == "3-B"
    assert res["research_model_trained"] is False
    assert res["production_model_candidate_created"] is False
    assert res["d_drive_written"] is False
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "3-C"
    assert res["catastrophic_fold_diagnostics"]["n_catastrophic_folds"] >= 1


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
