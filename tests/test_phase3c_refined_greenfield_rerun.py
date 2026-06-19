"""Phase 3-C tests for the Refined Greenfield Walk-Forward Rerun trainer.

These tests prove the trainer is a disciplined research-training phase: it compiles and imports;
it references the Phase 3-B result + refined config, the Phase 3-A baseline, and the D: panel
paths; it confirms the Phase 3-B recommendation was PROCEED_TO_REFINED_GREENFIELD_RERUN and that
the refined config is used (21d primary, 63d secondary, cross_sectional_ranks pruned); it carries
every required output field and safety flag; phase == "3-C"; it trains research models only
(production_model_trained / production_model_candidate_created / deployable_model_artifact_written
all false); the recommendation uses only the allowed values and routes to Phase 3-D; the kill
switch is evaluated and (when the recommendation is the kill-switch one) kill_switch_triggered is
true; and it imports no api_server / Paper Trader / ML framework and contains no infrastructure,
database, deployment, order/trading, network, or model-artifact usage tokens. The doc carries the
required guardrail phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase3c_refined_greenfield_rerun.py
  * without pytest: python tests/test_phase3c_refined_greenfield_rerun.py
The __main__ block discovers every test_* function, runs it, prints PASS/FAIL/SKIP, and exits
non-zero on any failure (the GCP venv has no pytest). The heavy live end-to-end run (reads the D:
panel and trains) is gated behind PHASE3C_LIVE=1.
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

_TRAINER = os.path.join(_REPO_ROOT, "research", "train_phase3c_refined_greenfield_rerun.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase3c_refined_greenfield_rerun_v1.md")
_RESULTS = os.path.join(_REPO_ROOT, "research", "output", "phase3c_refined_greenfield_rerun.json")
_WF_CSV = os.path.join(_REPO_ROOT, "research", "output", "phase3c_refined_walkforward_summary.csv")
_FEAT_CSV = os.path.join(_REPO_ROOT, "research", "output", "phase3c_refined_feature_set.csv")
_REGIME_CSV = os.path.join(_REPO_ROOT, "research", "output", "phase3c_refined_regime_summary.csv")
_PHASE3B_JSON = os.path.join(
    _REPO_ROOT, "research", "output", "phase3b_greenfield_refinement.json")


class _Skip(Exception):
    """Raised to mark a test skipped."""


# The inputs this phase must reference (basenames): Phase 3-B result + refined config, the Phase
# 3-A baseline, the sector map, and the D: panel paths.
_EXPECTED_INPUT_BASENAMES = (
    "phase3b_greenfield_refinement.json",
    "phase3b_refined_config.json",
    "phase3a_greenfield_baseline.json",
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
# Forbidden deployable-model-artifact tokens (this phase persists no model).
_FORBIDDEN_ARTIFACT_TOKENS = [
    "to_" + "pickle", "pickle." + "dump", "joblib." + "dump", "import " + "pickle",
    "import " + "joblib", ".pk" + "l", ".job" + "lib",
]
# ML frameworks that must not be imported (numpy/pandas only).
_FORBIDDEN_IMPORT_ROOTS = {
    "sklearn", "tensorflow", "torch", "keras", "lightgbm", "xgboost", "statsmodels",
    "api_server",
}

_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read", "phase3b_summary", "refined_config_used",
    "data_summary", "survivorship_caveat", "feature_engineering_summary", "label_summary",
    "model_summary", "walkforward_config", "walkforward_results", "aggregate_results",
    "regime_summary", "sector_concentration_summary", "overlap_correction_summary",
    "kill_switch_evaluation", "recommendation", "interpretation", "recommended_next_phase",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_edge_claimed",
    "production_model_trained", "production_model_candidate_created",
    "deployable_model_artifact_written", "d_drive_written", "network_used",
)
_REQUIRED_TRUE = (
    "no_trading", "no_orders", "no_automation", "research_model_trained", "d_drive_read",
)
_ALLOWED_RECOMMENDATIONS = {
    "REFINED_GREENFIELD_PASSES_STABILITY_GATE",
    "REFINED_GREENFIELD_WEAK_BUT_STABLE",
    "PRICE_VOLUME_ONLY_KILL_SWITCH_TRIGGERED",
    "REFINED_GREENFIELD_RERUN_BLOCKED",
}
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


def _import_trainer():
    spec = importlib.util.spec_from_file_location("phase3c_trainer_test", _TRAINER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# 1. Trainer compiles and imports
# --------------------------------------------------------------------------- #
def test_trainer_compiles():
    compile(_read(_TRAINER), _TRAINER, "exec")


def test_trainer_imports():
    _import_trainer()


# --------------------------------------------------------------------------- #
# 2. Expected inputs + D: paths referenced; outputs under research/output (C:)
# --------------------------------------------------------------------------- #
def test_references_expected_inputs_and_d_paths():
    trainer = _import_trainer()
    text = _read(_TRAINER)
    for base in _EXPECTED_INPUT_BASENAMES:
        assert base in text, f"trainer does not reference expected input: {base}"
    for path in (trainer.RESULTS_JSON, trainer.WALKFORWARD_SUMMARY_CSV,
                 trainer.REFINED_FEATURE_SET_CSV, trainer.REFINED_REGIME_SUMMARY_CSV):
        n = path.replace("\\", "/")
        assert n.startswith("research/output/"), f"output must live under research/output: {n}"
        assert not n.upper().startswith("D:"), f"output must not be on the D: drive: {n}"
    assert trainer.EXPANDED_PRICE_HISTORY_CSV.replace("\\", "/").upper().startswith("D:/")


# --------------------------------------------------------------------------- #
# 3. No api_server / Paper Trader / ML-framework imports
# --------------------------------------------------------------------------- #
def test_no_forbidden_imports():
    text = _read(_TRAINER)
    assert "paper_trader" not in text.lower(), "trainer must not reference paper_trader"
    assert "import api_server" not in text and "from api_server" not in text
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = ([n.name for n in node.names]
                    + ([node.module] if isinstance(node, ast.ImportFrom)
                       and node.module else []))
            roots = {(m or "").split(".")[0] for m in mods}
            bad = roots & _FORBIDDEN_IMPORT_ROOTS
            assert not bad, f"trainer imports forbidden module(s): {bad}"


# --------------------------------------------------------------------------- #
# 4. No infrastructure / db / deploy / trade / network / artifact usage tokens
# --------------------------------------------------------------------------- #
def test_no_forbidden_usage_tokens():
    low = _read(_TRAINER).lower()
    for label, tokens in (
        ("infrastructure", _FORBIDDEN_INFRA_TOKENS),
        ("database-write", _FORBIDDEN_DB_TOKENS),
        ("deploy/trade", _FORBIDDEN_DEPLOY_TRADE_TOKENS),
        ("network", _FORBIDDEN_NETWORK_TOKENS),
        ("model-artifact", _FORBIDDEN_ARTIFACT_TOKENS),
    ):
        hits = [t for t in tokens if t.lower() in low]
        assert not hits, f"trainer contains forbidden {label} token(s): {hits}"


# --------------------------------------------------------------------------- #
# 5. Committed results artifact: required fields, safety flags, structure
# --------------------------------------------------------------------------- #
def test_committed_results_artifact_valid():
    if not os.path.isfile(_RESULTS):
        raise _Skip("committed results JSON not present")
    d = json.loads(_read(_RESULTS))
    for k in _REQUIRED_JSON_FIELDS:
        assert k in d, f"committed results JSON missing field: {k}"
    assert d["phase"] == "3-C"
    for k in _REQUIRED_FALSE:
        assert d[k] is False, f"{k} must be false"
    for k in _REQUIRED_TRUE:
        assert d[k] is True, f"{k} must be true"


def test_reads_phase3b_and_confirms_recommendation():
    if not os.path.isfile(_RESULTS):
        raise _Skip("committed results JSON not present")
    d = json.loads(_read(_RESULTS))
    summary = d["phase3b_summary"]
    assert summary["phase3b_recommendation"] == "PROCEED_TO_REFINED_GREENFIELD_RERUN"
    confirmed = summary["phase3b_confirmed"]
    assert confirmed["phase_is_3b"] is True
    assert confirmed["recommendation_is_proceed"] is True
    assert confirmed["recommended_next_phase_is_3c"] is True
    assert confirmed["all_confirmed"] is True


def test_confirms_refined_config_used():
    if not os.path.isfile(_RESULTS):
        raise _Skip("committed results JSON not present")
    d = json.loads(_read(_RESULTS))
    cfg = d["refined_config_used"]
    assert cfg["primary_horizon"] == 21, "primary horizon must be 21d"
    assert cfg["secondary_horizon"] == 63, "secondary horizon must be 63d"
    assert "cross_sectional_ranks" in cfg["pruned_feature_families"]
    confirmed = cfg["phase3b_config_confirmed"]
    assert confirmed["primary_horizon_contains_21"] is True
    assert confirmed["secondary_horizon_contains_63"] is True
    assert confirmed["cross_sectional_ranks_pruned"] is True
    assert confirmed["ridge_is_primary"] is True
    assert confirmed["all_confirmed"] is True
    # Feature engineering must drop the cross_sectional_ranks family.
    fe = d["feature_engineering_summary"]
    assert "cross_sectional_ranks" not in fe["selected_feature_families"]
    assert "cross_sectional_ranks" in fe["pruned_feature_families"]


def test_walkforward_config_horizons():
    if not os.path.isfile(_RESULTS):
        raise _Skip("committed results JSON not present")
    d = json.loads(_read(_RESULTS))
    cfg = d["walkforward_config"]
    assert cfg["primary_horizon"] == 21
    assert cfg["secondary_horizon"] == 63
    assert cfg["diagnostic_horizon"] == 5
    assert cfg["embargo_days"] >= 63, "embargo must be at least the max label horizon"
    assert set(cfg["horizons"]) == {5, 21, 63}


def test_kill_switch_and_recommendation():
    if not os.path.isfile(_RESULTS):
        raise _Skip("committed results JSON not present")
    d = json.loads(_read(_RESULTS))
    assert "kill_switch_evaluation" in d
    kill = d["kill_switch_evaluation"]
    assert "gates" in kill or kill.get("evaluable") is False
    rec = d["recommendation"]
    assert rec["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert rec["create_production_model_candidate_now"] is False
    assert rec["train_production_model_now"] is False
    assert rec["deploy_now"] is False
    assert rec["production_edge_claimed"] is False
    assert isinstance(rec.get("kill_switch_triggered"), bool)
    assert isinstance(rec.get("reason"), str) and rec["reason"]
    # If the kill switch recommendation is emitted, the flag must be true (and vice versa).
    if rec["recommendation"] == "PRICE_VOLUME_ONLY_KILL_SWITCH_TRIGGERED":
        assert rec["kill_switch_triggered"] is True
    else:
        assert rec["kill_switch_triggered"] is False
    assert d["recommended_next_phase"]["phase"] == "3-D"
    assert d["recommended_next_phase"]["title"]


def test_interpretation_flags_no_production():
    if not os.path.isfile(_RESULTS):
        raise _Skip("committed results JSON not present")
    d = json.loads(_read(_RESULTS))
    interp = d["interpretation"]
    assert interp["research_model_trained"] is True
    assert interp["production_model_trained"] is False
    assert interp["production_model_candidate_created"] is False
    assert interp["deployable_model_artifact_written"] is False
    assert interp["wrote_to_d_drive"] is False
    assert interp["fetched_data_from_network"] is False
    assert interp["production_edge_claimed"] is False


# --------------------------------------------------------------------------- #
# 6. Output CSVs exist and are well-formed
# --------------------------------------------------------------------------- #
def test_walkforward_csv_well_formed():
    if not os.path.isfile(_WF_CSV):
        raise _Skip("walkforward summary CSV not present")
    rows = _read_csv_rows(_WF_CSV)
    assert len(rows) >= 2
    for col in ("model", "horizon_days", "fold", "mean_rank_ic", "regime_label",
                "top_quintile_max_sector_share"):
        assert col in rows[0], f"walkforward CSV missing column: {col}"
    models = {r["model"] for r in rows}
    assert "REFINED_RIDGE_LINEAR_RANK_MODEL" in models
    assert "MODEL_FREE_COMPOSITE_BASELINE" in models


def test_refined_feature_set_csv_well_formed():
    if not os.path.isfile(_FEAT_CSV):
        raise _Skip("refined feature set CSV not present")
    rows = _read_csv_rows(_FEAT_CSV)
    assert len(rows) >= 20, "refined feature set should list the kept trailing features"
    feats = {r["feature"] for r in rows}
    # No cross_sectional_rank features survive; no sign-mirror raw return_Nd duplicates survive.
    assert not any(f.startswith("market_rank_") or f.startswith("sector_rank_") for f in feats)
    for dup in ("return_5d", "return_10d", "return_21d"):
        assert dup not in feats, f"sign-mirror duplicate not pruned: {dup}"
    for col in ("feature", "family", "full_sample_mean_daily_rank_ic_vs_excess_21d"):
        assert col in rows[0], f"refined feature set CSV missing column: {col}"


def test_regime_summary_csv_well_formed():
    if not os.path.isfile(_REGIME_CSV):
        raise _Skip("regime summary CSV not present")
    rows = _read_csv_rows(_REGIME_CSV)
    assert len(rows) >= 1
    for col in ("regime_label", "n_folds", "mean_rank_ic", "n_catastrophic_folds"):
        assert col in rows[0], f"regime summary CSV missing column: {col}"


# --------------------------------------------------------------------------- #
# 7. Doc has all required guardrail phrases
# --------------------------------------------------------------------------- #
def test_doc_has_required_phrases():
    text = _read(_DOC)
    missing = [p for p in _REQUIRED_DOC_PHRASES if p not in text]
    assert not missing, f"doc missing required phrase(s): {missing}"


# --------------------------------------------------------------------------- #
# 8. Heavy live end-to-end run (gated; reads the D: panel and trains)
# --------------------------------------------------------------------------- #
def test_live_end_to_end_optional():
    if os.environ.get("PHASE3C_LIVE") != "1":
        raise _Skip("set PHASE3C_LIVE=1 to run the trainer end to end")
    if not os.path.isfile(_PHASE3B_JSON):
        raise _Skip("Phase 3-B results JSON not present in this environment")
    import tempfile
    trainer = _import_trainer()
    if not os.path.isfile(trainer.EXPANDED_PRICE_HISTORY_CSV):
        raise _Skip("D: price panel not present in this environment")
    tmp = tempfile.mkdtemp(prefix="phase3c_")
    res = trainer.run(
        results_path=os.path.join(tmp, "rerun.json"),
        walkforward_summary_path=os.path.join(tmp, "wf.csv"),
        refined_feature_set_path=os.path.join(tmp, "feat.csv"),
        refined_regime_summary_path=os.path.join(tmp, "regime.csv"))
    assert res["phase"] == "3-C"
    assert res["research_model_trained"] is True
    assert res["production_model_candidate_created"] is False
    assert res["deployable_model_artifact_written"] is False
    assert res["d_drive_written"] is False
    assert res["network_used"] is False
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "3-D"
    assert "kill_switch_evaluation" in res


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
