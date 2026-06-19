"""Phase 3-A tests for the Greenfield Research Model Baseline trainer.

These tests prove the trainer is a disciplined research-only model-training phase: it compiles
and imports; it references the expected inputs (the expanded D: price panel + its provenance
JSONs, the populated sector map, and the prior-phase summaries) and the committed results record
those D: read paths; it carries every required output field and safety flag; phase == "3-A";
d_drive_read is true while d_drive_written / network_used are false; it trains research models
(research_model_trained true) but NOT a production model / candidate / deployable artifact
(production_model_trained / production_model_candidate_created / deployable_model_artifact_written
all false); the recommendation uses only the allowed greenfield values and routes to Phase 3-B;
it imports no api_server / Paper Trader / ML framework and contains no infrastructure, database,
deployment, order/trading, network, or model-artifact usage tokens; and the doc carries the
required guardrail phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase3a_greenfield_baseline.py
  * without pytest: python tests/test_phase3a_greenfield_baseline.py
The __main__ block discovers every test_* function, runs it, prints PASS/FAIL/SKIP, and exits
non-zero on any failure (the GCP venv has no pytest). The optional live end-to-end run is gated
behind PHASE3A_LIVE=1 because it reads the full ~338k-row panel and trains 15 walk-forward folds.
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

_TRAINER = os.path.join(_REPO_ROOT, "research", "train_phase3a_greenfield_baseline.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase3a_greenfield_baseline_v1.md")
_RESULTS = os.path.join(_REPO_ROOT, "research", "output", "phase3a_greenfield_baseline.json")
_FEATURE_CSV = os.path.join(
    _REPO_ROOT, "research", "output", "phase3a_greenfield_feature_summary.csv")
_WALKFORWARD_CSV = os.path.join(
    _REPO_ROOT, "research", "output", "phase3a_greenfield_walkforward_summary.csv")


class _Skip(Exception):
    """Raised to mark a test skipped."""


# The inputs this phase must reference (basenames). This phase DOES read the D: panel.
_EXPECTED_INPUT_BASENAMES = (
    "phase2k_g_expanded_price_history_free.csv",
    "phase2k_g_data_quality_report.json",
    "phase2k_g_data_build_summary.json",
    "phase2k_g_survivorship_caveat.json",
    "phase2k_p_sector_map_current.csv",
    "phase2k_q_populate_sector_map.json",
    "phase2k_n_narrow_model_free_retest.json",
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
    "ssh " , "scp " , "uvi" + "corn", "systemd",
]
# Forbidden network / acquisition tokens.
_FORBIDDEN_NETWORK_TOKENS = [
    "http" + "://", "https" + "://", "requests." + "get", "requests." + "post",
    "url" + "lib", "yf" + "inance", "soc" + "ket", "url" + "open", "down" + "load(",
]
# Forbidden deployable-model-artifact tokens (training research models is allowed; persisting a
# deployable artifact is NOT).
_FORBIDDEN_ARTIFACT_TOKENS = [
    "to_" + "pickle", "pickle." + "dump", "joblib." + "dump", "import " + "pickle",
    "import " + "joblib", ".pk" + "l", ".job" + "lib",
]
# ML frameworks that must not be imported (we use numpy closed-form / numpy GD only).
_FORBIDDEN_IMPORT_ROOTS = {
    "sklearn", "tensorflow", "torch", "keras", "lightgbm", "xgboost", "statsmodels",
    "api_server",
}

_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read", "data_summary", "survivorship_caveat",
    "sector_map_summary", "feature_engineering_summary", "label_summary", "models_trained",
    "walkforward_config", "walkforward_results", "aggregate_results",
    "feature_importance_or_coefficients", "benchmark_comparison", "failure_modes",
    "recommendation", "interpretation", "recommended_next_phase",
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
    "GREENFIELD_BASELINE_PROMISING",
    "GREENFIELD_BASELINE_WEAK_BUT_IMPROVABLE",
    "GREENFIELD_BASELINE_FAILED",
    "GREENFIELD_BASELINE_BLOCKED_BY_DATA",
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
    spec = importlib.util.spec_from_file_location("phase3a_trainer_test", _TRAINER)
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
# 2. Expected inputs + D: paths referenced; outputs live under research/output (C:)
# --------------------------------------------------------------------------- #
def test_references_expected_inputs_and_d_paths():
    trainer = _import_trainer()
    text = _read(_TRAINER)
    for base in _EXPECTED_INPUT_BASENAMES:
        assert base in text, f"trainer does not reference expected input: {base}"
    # The three outputs are under the C: repo research/output, never the D: drive.
    for path in (trainer.RESULTS_JSON, trainer.FEATURE_SUMMARY_CSV,
                 trainer.WALKFORWARD_SUMMARY_CSV):
        n = path.replace("\\", "/")
        assert n.startswith("research/output/"), f"output must live under research/output: {n}"
        assert not n.upper().startswith("D:"), f"output must not be on the D: drive: {n}"
    # The trainer resolves real D: read paths for the panel + provenance.
    assert trainer.EXPANDED_PRICE_HISTORY_CSV.replace("\\", "/").upper().startswith("D:/")
    assert trainer.DATA_QUALITY_REPORT_JSON.replace("\\", "/").upper().startswith("D:/")


def test_committed_results_record_d_drive_read_paths():
    if not os.path.isfile(_RESULTS):
        raise _Skip("committed results JSON not present")
    d = json.loads(_read(_RESULTS))
    ir = d["inputs_read"]
    assert ir["expanded_price_history_csv"].upper().startswith("D:/")
    assert ir["data_quality_report_json"].upper().startswith("D:/")
    assert ir["survivorship_caveat_json"].upper().startswith("D:/")
    # C: inputs.
    assert ir["sector_map_csv"].replace("\\", "/").startswith("research/input/")


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
    assert os.path.isfile(_RESULTS), "committed results JSON must exist"
    d = json.loads(_read(_RESULTS))
    for k in _REQUIRED_JSON_FIELDS:
        assert k in d, f"committed results JSON missing field: {k}"
    assert d["phase"] == "3-A"
    for k in _REQUIRED_FALSE:
        assert d[k] is False, f"{k} must be false"
    for k in _REQUIRED_TRUE:
        assert d[k] is True, f"{k} must be true"


def test_recommendation_and_next_phase():
    d = json.loads(_read(_RESULTS))
    rec = d["recommendation"]
    assert rec["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert rec["create_production_model_candidate_now"] is False
    assert rec["train_production_model_now"] is False
    assert rec["deploy_now"] is False
    assert rec["production_edge_claimed"] is False
    assert isinstance(rec.get("reason"), str) and rec["reason"]
    assert d["recommended_next_phase"]["phase"] == "3-B"
    assert d["recommended_next_phase"]["title"]


def test_interpretation_flags():
    d = json.loads(_read(_RESULTS))
    interp = d["interpretation"]
    assert interp["research_model_trained"] is True
    assert interp["production_model_trained"] is False
    assert interp["production_model_candidate_created"] is False
    assert interp["deployable_model_artifact_written"] is False
    assert interp["wrote_to_d_drive"] is False
    assert interp["fetched_data_from_network"] is False
    assert interp["production_edge_claimed"] is False
    assert interp["read_from_d_drive"] is True


# --------------------------------------------------------------------------- #
# 6. Walk-forward + feature engineering structure recorded in the JSON
# --------------------------------------------------------------------------- #
def test_walkforward_and_features_recorded():
    d = json.loads(_read(_RESULTS))
    cfg = d["walkforward_config"]
    assert cfg["embargo_days"] >= max(d["label_summary"]["horizons"]), \
        "embargo must be at least the max label horizon (no leakage)"
    assert cfg["n_folds"] >= 2, "need multiple out-of-sample folds"
    fe = d["feature_engineering_summary"]
    assert fe["all_features_trailing_point_in_time"] is True
    assert fe["no_lookahead"] is True
    assert fe["n_features"] == len(fe["features"]) and fe["n_features"] >= 25
    # All required feature families present.
    for fam in ("price_momentum", "reversal", "volatility_risk", "volume_liquidity",
                "market_relative", "sector_relative", "cross_sectional_ranks"):
        assert fam in fe["feature_families"], f"missing feature family: {fam}"
    ls = d["label_summary"]
    assert ls["forward_filled"] is False
    assert sorted(ls["horizons"]) == [5, 21, 63]
    # Models: composite (not trained) + ridge + logistic.
    names = {m["name"] for m in d["models_trained"]}
    assert "MODEL_FREE_COMPOSITE_BASELINE" in names
    assert "RIDGE_LINEAR_RANK_MODEL" in names
    assert any("LOGISTIC" in n for n in names)
    # Walk-forward results are populated and benchmark comparison exists.
    assert isinstance(d["walkforward_results"], list) and len(d["walkforward_results"]) > 0
    assert "mean_rank_ic_by_model_and_horizon" in d["benchmark_comparison"]


# --------------------------------------------------------------------------- #
# 7. Output CSVs exist and are well-formed
# --------------------------------------------------------------------------- #
def test_feature_summary_csv_well_formed():
    if not os.path.isfile(_FEATURE_CSV):
        raise _Skip("feature summary CSV not present")
    rows = _read_csv_rows(_FEATURE_CSV)
    assert len(rows) >= 25, "feature summary should cover the engineered features"
    for col in ("feature", "non_null_fraction",
                "full_sample_mean_daily_rank_ic_vs_excess_21d"):
        assert col in rows[0], f"feature summary missing column: {col}"


def test_walkforward_summary_csv_well_formed():
    if not os.path.isfile(_WALKFORWARD_CSV):
        raise _Skip("walk-forward summary CSV not present")
    rows = _read_csv_rows(_WALKFORWARD_CSV)
    assert len(rows) > 0
    for col in ("model", "horizon_days", "fold", "mean_rank_ic", "top_minus_bottom_spread"):
        assert col in rows[0], f"walk-forward summary missing column: {col}"
    models = {r["model"] for r in rows}
    assert "RIDGE_LINEAR_RANK_MODEL" in models


# --------------------------------------------------------------------------- #
# 8. Doc has all required guardrail phrases
# --------------------------------------------------------------------------- #
def test_doc_has_required_phrases():
    text = _read(_DOC)
    missing = [p for p in _REQUIRED_DOC_PHRASES if p not in text]
    assert not missing, f"doc missing required phrase(s): {missing}"


# --------------------------------------------------------------------------- #
# 9. Optional live end-to-end run (gated; reads the full D: panel, trains folds)
# --------------------------------------------------------------------------- #
def test_live_end_to_end_optional():
    if os.environ.get("PHASE3A_LIVE") != "1":
        raise _Skip("set PHASE3A_LIVE=1 to run the full ~338k-row walk-forward end to end")
    import tempfile
    trainer = _import_trainer()
    if not os.path.isfile(trainer.EXPANDED_PRICE_HISTORY_CSV):
        raise _Skip("expanded D: price-history CSV not present in this environment")
    tmp = tempfile.mkdtemp(prefix="phase3a_")
    res = trainer.run(
        results_path=os.path.join(tmp, "baseline.json"),
        feature_summary_path=os.path.join(tmp, "features.csv"),
        walkforward_summary_path=os.path.join(tmp, "walk.csv"))
    assert res["phase"] == "3-A"
    assert res["d_drive_read"] is True and res["d_drive_written"] is False
    assert res["research_model_trained"] is True
    assert res["production_model_candidate_created"] is False
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "3-B"


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
