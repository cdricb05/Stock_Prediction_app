"""Tests for Phase 5-F0 — Professional Alpha Model V3 Research Harness.

All tests are fully offline and deterministic. They inject a synthetic local
price history (SPY + standard tickers) so no real data, no network, and no API
key are ever required. They assert the safety contract, the point-in-time /
no-leakage discipline, the Phase 5-C comparison, the five-model scoreboard, the
documented sklearn fallback, and that all committed-safe artifacts are written.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import math
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNNER = _REPO_ROOT / "research" / "run_phase5f0_alpha_model_v3_research.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("phase5f0_runner_test", str(_RUNNER))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# Synthetic local price history (no network, no files, no key)
# --------------------------------------------------------------------------- #
def _business_days(start, end):
    import datetime as dt
    d = dt.date.fromisoformat(start)
    last = dt.date.fromisoformat(end)
    out = []
    while d <= last:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def _synth_price_history(n_tickers=34, seed=11, start="2015-01-02", end="2023-12-31"):
    """{ticker: {iso_date: (close, dollar_volume)}} for SPY + n synthetic names.

    Seeded geometric random walks with mild per-name drift so the cross-section
    has dispersion; SPY is the benchmark/regime proxy."""
    import random
    rng = random.Random(seed)
    dates = _business_days(start, end)
    hist = {}
    # SPY benchmark.
    px = 200.0
    spy = {}
    for d in dates:
        px *= (1.0 + rng.gauss(0.0003, 0.009))
        spy[d] = (px, px * 1_000_000.0)
    hist["SPY"] = spy
    for k in range(n_tickers):
        t = f"T{k:02d}"
        px = 50.0 + 5.0 * k
        drift = 0.0002 + 0.0004 * ((k % 5) - 2) / 2.0
        vol = 0.012 + 0.004 * (k % 4)
        series = {}
        for d in dates:
            px *= (1.0 + rng.gauss(drift, vol))
            px = max(px, 1.0)
            series[d] = (px, px * (200_000.0 + 50_000.0 * (k % 6)))
        hist[t] = series
    return hist


@pytest.fixture(scope="module")
def mod():
    return _load_runner()


@pytest.fixture(scope="module")
def synth():
    return _synth_price_history()


@pytest.fixture(scope="module")
def full_report(mod, synth, tmp_path_factory):
    out = tmp_path_factory.mktemp("phase5f0_out")
    return mod.run(price_history=synth, out_dir=out, write=True, verbose=False)


# --------------------------------------------------------------------------- #
# Identity / vocabulary
# --------------------------------------------------------------------------- #
def test_phase_identifier(full_report):
    assert full_report["phase"] == "5-F0"


def test_benchmark_is_spy(full_report):
    assert full_report["benchmark_regime_proxy"] == "SPY"


def test_primary_label(full_report, mod):
    assert full_report["primary_label"] == "forward_excess_return_20d_vs_spy"
    assert mod.PRIMARY_LABEL == "forward_excess_return_20d_vs_spy"


def test_recommendation_vocabulary_exact(mod):
    assert set(mod.ALLOWED_RECOMMENDATIONS) == {
        "READY_FOR_PHASE5F1_DEPLOYABLE_SCORER",
        "EDGE_PRESENT_BUT_NOT_DEPLOYABLE",
        "NO_ROBUST_EDGE",
        "DATA_BLOCKER",
        "ERROR",
    }


def test_recommendation_is_allowed(full_report, mod):
    assert full_report["recommendation"] in mod.ALLOWED_RECOMMENDATIONS


# --------------------------------------------------------------------------- #
# No network / no key / no provider work (static source scan)
# --------------------------------------------------------------------------- #
def test_source_has_no_network_or_provider_dependency():
    """Scan for genuine network / provider / key dependency patterns (imports and
    calls), not bare substrings — the module legitimately *names* SimFin/FMP in
    docstrings to document what it does NOT do."""
    src = _RUNNER.read_text(encoding="utf-8").lower()
    for tok in ("import requests", "import urllib", "urllib.request", "urllib.urlopen",
                "http.client", "httplib", "socket.socket", "import simfin", "from simfin",
                "set_api_key", "os.environ", ".getenv", "financialmodelingprep",
                "alphavantage", "www.alphavantage"):
        assert tok not in src, f"forbidden dependency pattern in source: {tok}"


def test_no_api_key_required(full_report):
    assert full_report["paid_apis_used"] is False
    assert full_report["network_used"] is False
    assert full_report["uses_external_paid_provider"] is False
    assert full_report["provider_work"] is False


def test_no_package_install(full_report):
    assert full_report["packages_installed"] is False


# --------------------------------------------------------------------------- #
# Safety contract
# --------------------------------------------------------------------------- #
def test_safety_flags(full_report):
    assert full_report["preview_only"] is True
    for flag in ("orders_enabled", "automation_enabled", "broker_execution_enabled",
                 "production_replacement", "deployed", "binary_artifacts_created",
                 "writes_to_d_drive", "modifies_paper_trader", "modifies_gcp", "committed"):
        assert full_report[flag] is False, f"{flag} must be False"


def test_d_drive_not_written(full_report):
    # The injected run never resolves a D: path and never writes outside out_dir.
    assert full_report["writes_to_d_drive"] is False
    src = _RUNNER.read_text(encoding="utf-8").lower()
    assert "open(r\"d:" not in src and "to_csv(\"d:" not in src


# --------------------------------------------------------------------------- #
# Features / labels
# --------------------------------------------------------------------------- #
def test_feature_families_seven(full_report, mod):
    assert full_report["feature_families"] == mod.FEATURE_FAMILIES
    assert len(mod.FEATURE_FAMILIES) == 7


def test_feature_count_matches(full_report, mod):
    assert full_report["feature_count"] == len(mod.FEATURES)
    assert full_report["feature_count"] >= 30


def test_labels_are_forward_only_and_not_features(mod):
    labels = {"forward_excess_return_5d_vs_spy", "forward_excess_return_10d_vs_spy",
              "forward_excess_return_20d_vs_spy", "forward_return_5d", "forward_return_10d",
              "forward_return_20d", "top_quintile_label", "bottom_quintile_label",
              "downside_risk_label"}
    assert set(mod.FEATURES).isdisjoint(labels)
    for feat in mod.FEATURES:
        assert not feat.startswith("forward"), feat


def test_sector_relative_strength_marked_unavailable(full_report):
    assert full_report["sector_relative_strength_available"] is False
    assert "no reliable local sector" in full_report["sector_relative_strength_note"].lower()


# --------------------------------------------------------------------------- #
# Models / scoreboard
# --------------------------------------------------------------------------- #
def test_five_models_evaluated(full_report, mod):
    assert full_report["models_evaluated"] == mod.MODELS_EVALUATED
    assert len(mod.MODELS_EVALUATED) == 5
    sb = full_report["model_scoreboard"]
    for m in mod.MODELS_EVALUATED:
        assert m in sb


def test_phase5c_reference_present(full_report, mod):
    sb = full_report["model_scoreboard"]
    assert mod.M_REF in sb
    assert sb[mod.M_REF]["evaluable"] is True
    pc = full_report["phase5c_comparison"]
    assert pc["reference_mean_rank_ic"] is not None
    assert "delta_best_v3_minus_5c" in pc
    assert pc["best_v3_model"] in (mod.M_COMPOSITE, mod.M_RIDGE, mod.M_TREE, mod.M_ENSEMBLE)


def test_best_model_is_a_v3_candidate(full_report, mod):
    # The deployable candidate is a V3 model, never the 5-C reference baseline.
    assert full_report["best_model"] in (mod.M_COMPOSITE, mod.M_RIDGE, mod.M_TREE, mod.M_ENSEMBLE)


def test_nonlinear_tree_fallback_documented(full_report, mod):
    # sklearn is not installed in this env -> documented numpy fallback, model still evaluated.
    assert full_report["sklearn_available"] == mod.sklearn_available()
    if not full_report["sklearn_available"]:
        assert full_report["nonlinear_model_backend"] == "numpy_gradient_boosted_stumps_fallback"
    assert full_report["model_scoreboard"][mod.M_TREE]["evaluable"] is True


def test_ensemble_is_out_of_sample(full_report, mod):
    # Ensemble combines OOS component scores; it must be evaluable and produce predictions.
    sb = full_report["model_scoreboard"]
    assert sb[mod.M_ENSEMBLE]["evaluable"] is True
    assert sb[mod.M_ENSEMBLE]["n_predictions"] > 0


# --------------------------------------------------------------------------- #
# Walk-forward / embargo / leakage / placebo
# --------------------------------------------------------------------------- #
def test_walk_forward_embargo_exists(full_report, mod):
    wf = full_report["walk_forward"]
    assert wf["embargo_days"] >= 20
    assert wf["ridge_folds"], "expected at least one walk-forward fold"
    for fo in wf["ridge_folds"]:
        assert fo["embargo_ok"] is True
        assert fo["train_rows"] > 0  # trained on strictly-past rows only


def test_placebo_label_shuffle_present_and_collapses(full_report):
    lk = full_report["leakage_checks"]
    assert "placebo_mean_ic" in lk
    placebo = lk["placebo_mean_ic"]
    # A shuffled-label refit must not retain edge.
    assert placebo is None or abs(placebo) <= 0.05


def test_no_leakage_gate_logic(full_report):
    gates = {g["gate_name"]: g for g in full_report["validation_gate_matrix"]}
    assert gates["no_leakage_gate"]["status"] in ("PASS", "FAIL")
    assert gates["forward_label_no_lookahead_gate"]["status"] == "PASS"


# --------------------------------------------------------------------------- #
# Portfolio simulation (equal + vol-adjusted) / cost sensitivity
# --------------------------------------------------------------------------- #
def test_portfolio_simulation_variants(full_report, mod):
    ps = full_report["portfolio_simulation"]
    assert set(ps["weightings"]) == {"equal", "vol_adj"}
    assert set(ps["cost_bps"]) == {25, 50}
    assert 10 in ps["baskets"] and 20 in ps["baskets"]


# --------------------------------------------------------------------------- #
# Gate matrix / recommendation honesty
# --------------------------------------------------------------------------- #
def test_gate_matrix_and_summary(full_report):
    gates = full_report["validation_gate_matrix"]
    names = {g["gate_name"] for g in gates}
    for required in ("no_leakage_gate", "mean_rank_ic_gate", "ic_t_stat_gate",
                     "positive_decile_spread_after_cost_gate", "worst_year_gate",
                     "turnover_gate", "drawdown_gate", "regime_robustness_gate",
                     "materially_beats_phase5c_gate", "liquidity_gate",
                     "survivorship_bias_gate", "no_fake_data_gate", "preview_only_gate",
                     "no_orders_gate", "no_broker_execution_gate", "no_automation_gate",
                     "no_binary_model_artifact_gate"):
        assert required in names, f"missing gate {required}"
    summ = full_report["validation_gate_summary"]
    assert sum(summ.values()) == len(gates)


def test_recommendation_not_forced_pass(full_report):
    # Synthetic random-walk data should not clear the deployment bar.
    assert full_report["recommendation"] != "READY_FOR_PHASE5F1_DEPLOYABLE_SCORER"
    assert full_report["any_model_clears_deployment_gates"] is False


# --------------------------------------------------------------------------- #
# Artifacts (all committed-safe, all text, all under the phase5f0 dir)
# --------------------------------------------------------------------------- #
def test_all_eleven_artifacts_written(mod, synth, tmp_path):
    out = tmp_path / "art"
    mod.run(price_history=synth, out_dir=out, write=True, verbose=False)
    expected = [
        "phase5f0_alpha_model_v3_research.json", "alpha_v3_feature_catalog.csv",
        "alpha_v3_panel_sample.csv", "alpha_v3_model_scoreboard.csv",
        "alpha_v3_ic_by_year.csv", "alpha_v3_decile_spread.csv",
        "alpha_v3_turnover_cost_report.csv", "alpha_v3_portfolio_simulation.csv",
        "alpha_v3_regime_breakdown.csv", "alpha_v3_validation_gate_matrix.csv",
        "phase5f1_deployable_scorer_plan.json",
    ]
    for name in expected:
        assert (out / name).is_file(), f"missing artifact {name}"


def test_artifacts_are_text_not_binary(mod, synth, tmp_path):
    out = tmp_path / "art2"
    mod.run(price_history=synth, out_dir=out, write=True, verbose=False)
    for p in out.iterdir():
        assert p.suffix in (".json", ".csv"), p.name
        # readable as utf-8 text
        p.read_text(encoding="utf-8")
    # no binary model artifact extensions anywhere
    for p in out.iterdir():
        assert p.suffix not in (".pkl", ".joblib", ".onnx", ".pt", ".h5", ".keras")


def test_default_output_paths_under_phase5f0_dir(mod):
    fresh = _load_runner()
    assert "phase5f0_alpha_model_v3_research" in str(fresh._REPORT_OUT)
    assert "data/simfin" not in str(fresh._REPORT_OUT).replace("\\", "/")


def test_phase5f1_plan_gated(mod, synth, tmp_path):
    out = tmp_path / "plan"
    rep = mod.run(price_history=synth, out_dir=out, write=True, verbose=False)
    plan = json.loads((out / "phase5f1_deployable_scorer_plan.json").read_text(encoding="utf-8"))
    assert plan["proceed_to_5f1"] == (rep["recommendation"] == "READY_FOR_PHASE5F1_DEPLOYABLE_SCORER")
    assert plan["orders_enabled"] is False and plan["automation_enabled"] is False


# --------------------------------------------------------------------------- #
# Data blocker path
# --------------------------------------------------------------------------- #
def test_missing_price_history_blocks(mod, tmp_path):
    out = tmp_path / "blocked"
    rep = mod.run(price_history={}, out_dir=out, write=True, verbose=False)
    assert rep["recommendation"] == "DATA_BLOCKER"
    assert rep["preview_only"] is True


# --------------------------------------------------------------------------- #
# Determinism (gradient-boosted-stumps fallback must be reproducible)
# --------------------------------------------------------------------------- #
def test_deterministic_recommendation(mod, synth, tmp_path):
    r1 = mod.run(price_history=synth, out_dir=tmp_path / "d1", write=True, verbose=False)
    r2 = mod.run(price_history=synth, out_dir=tmp_path / "d2", write=True, verbose=False)
    assert r1["recommendation"] == r2["recommendation"]
    assert r1["model_scoreboard"][mod.M_RIDGE]["mean_rank_ic"] == \
        r2["model_scoreboard"][mod.M_RIDGE]["mean_rank_ic"]
    assert r1["model_scoreboard"][mod.M_TREE]["mean_rank_ic"] == \
        r2["model_scoreboard"][mod.M_TREE]["mean_rank_ic"]
