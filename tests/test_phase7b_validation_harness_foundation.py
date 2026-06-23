"""Tests for Phase 7-B — Validation Harness Foundation.

These tests exercise the REAL harness on deterministic synthetic data only. They
never touch the network, the D: drive, any provider, the database, Paper Trader, or
GCP. Every ``run(...)`` call writes to a pytest ``tmp_path`` so a test can never
overwrite the committed artifacts under
``research/output/phase7b_validation_harness_foundation/``.

The harness is the project's measuring instrument (charter Section 6 / Phase 0);
these tests grade the instrument: that its splits are leakage-safe, its purge drops
overlapping rows, its holdout is single-touch, its multiple-testing haircut behaves,
its cost model nets correctly, its metric suite recovers a known signal while the
leakage placebo collapses, and that no unsafe capability is present.
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_RUNNER_PATH = _REPO_ROOT / "research" / "run_phase7b_validation_harness_foundation.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("phase7b_runner_test", str(_RUNNER_PATH))
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so dataclasses with string annotations resolve.
    sys.modules["phase7b_runner_test"] = mod
    spec.loader.exec_module(mod)
    return mod


h = _load_module()


# --------------------------------------------------------------------------- #
# End-to-end: run() produces all artifacts, READY, and JSON is valid.
# --------------------------------------------------------------------------- #
def test_run_end_to_end_ready(tmp_path: Path):
    report = h.run(out_dir=str(tmp_path))
    assert report["recommendation"] == h.REC_READY
    assert report["recommendation"] in h.ALLOWED_RECOMMENDATIONS
    # All 7 committed-safe artifacts exist.
    for name in ("phase7b_validation_harness_foundation.json",
                 "validation_harness_requirements.csv",
                 "validation_metric_catalog.csv",
                 "validation_gate_matrix.csv",
                 "multiple_testing_tracker_template.csv",
                 "walk_forward_split_plan.csv",
                 "phase7c_next_plan.json"):
        assert (tmp_path / name).is_file(), f"missing artifact {name}"
    # Main report is valid JSON and round-trips.
    loaded = json.loads((tmp_path / "phase7b_validation_harness_foundation.json").read_text())
    assert loaded["phase"] == "7-B"
    assert loaded["gate_summary"]["counts"]["FAIL"] == 0


def test_safety_flags_all_false_and_preview_only(tmp_path: Path):
    report = h.run(out_dir=str(tmp_path))
    for flag in ("network_used", "paid_apis_used", "live_data_used", "orders_enabled",
                 "broker_execution_enabled", "automation_enabled", "deployed",
                 "production_model_built", "factor_alpha_built", "factor_weights_optimized",
                 "paper_trader_touched", "gcp_touched", "binary_artifacts_created"):
        assert report[flag] is False, f"{flag} must be False"
    assert report["preview_only"] is True
    assert report["commit_appropriate"] is False


# --------------------------------------------------------------------------- #
# 1. Walk-forward splits are leakage-safe by construction.
# --------------------------------------------------------------------------- #
def test_walk_forward_train_labels_resolve_before_test_with_embargo():
    # 40 periods, 1 sample/period, horizon-1 labels.
    eval_idx = list(range(40))
    label_end = [p + 1 for p in range(40)]
    folds = h.walk_forward_splits(eval_idx, label_end, n_folds=4, min_train_periods=8, embargo=3)
    assert folds, "expected non-empty walk-forward folds"
    for f in folds:
        assert f.scheme == "walk_forward_expanding"
        test_lo = f.test_period_lo
        # Every train sample's label must resolve at least `embargo` periods before test.
        for i in f.train_idx:
            assert label_end[i] <= test_lo - 1 - f.embargo
            assert eval_idx[i] < test_lo
        # Train and test never share an index.
        assert not (set(f.train_idx) & set(f.test_idx))
        assert f.min_embargo_gap is None or f.min_embargo_gap >= 0


# --------------------------------------------------------------------------- #
# 2. Purged k-fold drops overlapping train rows and embargoes after test.
# --------------------------------------------------------------------------- #
def test_purged_kfold_purges_overlapping_label():
    # One extra sample at period 4 whose label resolves at 7 (overlaps a later test block).
    eval_idx = list(range(10)) + [4]
    label_end = [p + 1 for p in range(10)] + [7]
    overlap = len(eval_idx) - 1
    folds = h.purged_kfold_splits(eval_idx, label_end, n_folds=2, embargo=1)
    assert folds
    # The overlapping sample must be excluded (neither train nor test) from the fold
    # whose test interval its label spans.
    excluded_somewhere = any(
        overlap not in f.train_idx and overlap not in f.test_idx
        for f in folds if f.test_period_lo <= 7 and f.test_period_hi >= 5)
    assert excluded_somewhere
    assert sum(f.n_purged for f in folds) > 0


def test_purged_kfold_no_train_test_index_overlap():
    eval_idx = list(range(30))
    label_end = [p + 2 for p in range(30)]
    folds = h.purged_kfold_splits(eval_idx, label_end, n_folds=5, embargo=2)
    assert folds
    for f in folds:
        assert not (set(f.train_idx) & set(f.test_idx))


# --------------------------------------------------------------------------- #
# 3. No-lookahead guard catches same-day / backward labels.
# --------------------------------------------------------------------------- #
def test_strictly_forward_labels_detects_violation():
    ok = h.assert_strictly_forward_labels([0, 1, 2], [1, 2, 3])
    assert ok["strictly_forward"] and ok["n_violations"] == 0
    # Same-day label (label_end == eval) is a violation.
    bad = h.assert_strictly_forward_labels([0, 1, 2], [1, 1, 3])
    assert not bad["strictly_forward"] and bad["n_violations"] == 1


# --------------------------------------------------------------------------- #
# 4. Holdout single-touch discipline (OOS is a spent budget).
# --------------------------------------------------------------------------- #
def test_holdout_ledger_single_touch():
    ledger = h.HoldoutLedger(holdout_start="2025-01-01", holdout_end="2025-12-31")
    assert ledger.touched is False
    first = ledger.touch("final sign-off")
    assert first["permitted"] is True and first["violation"] is False
    second = ledger.touch("oops re-peek")
    assert second["permitted"] is False and second["violation"] is True
    assert ledger.touch_count == 2 and ledger.touched is True
    # Serializable for the audit ledger.
    assert ledger.to_dict()["touch_count"] == 2


# --------------------------------------------------------------------------- #
# 5. Multiple-testing counter + deflated Sharpe behave.
# --------------------------------------------------------------------------- #
def test_multiple_testing_counter_increments():
    t = h.MultipleTestingTracker()
    assert t.n_trials == 0
    for i in range(5):
        n = t.register(f"cfg_{i}", sharpe=0.1 * i)
        assert n == i + 1
    assert t.n_trials == 5
    assert t.sharpe_variance() is not None and t.sharpe_variance() > 0


def test_expected_max_sharpe_rises_with_trials():
    e2 = h.expected_max_sharpe(2, 0.25)
    e50 = h.expected_max_sharpe(50, 0.25)
    assert e50 > e2 >= 0.0


def test_deflated_sharpe_in_unit_interval_and_drops_with_more_trials():
    dsr_few = h.deflated_sharpe_ratio(1.0, n_obs=120, n_trials=2, sharpe_variance=0.25)
    dsr_many = h.deflated_sharpe_ratio(1.0, n_obs=120, n_trials=200, sharpe_variance=0.25)
    assert 0.0 <= dsr_many <= 1.0 and 0.0 <= dsr_few <= 1.0
    # More configurations tried -> the same Sharpe is less credible.
    assert dsr_many <= dsr_few


def test_norm_cdf_ppf_roundtrip():
    for p in (0.05, 0.25, 0.5, 0.84, 0.975):
        assert abs(h._norm_cdf(h._norm_ppf(p)) - p) < 1e-6


# --------------------------------------------------------------------------- #
# 6. Cost model: net below gross whenever turnover and bps are positive.
# --------------------------------------------------------------------------- #
def test_cost_model_net_below_gross():
    gross = [0.01, 0.0, -0.005, 0.02]
    turns = [0.3, 0.1, 0.5, 0.2]
    out = h.apply_transaction_costs(gross, turns, cost_bps=20.0)
    assert out["net_mean"] < out["gross_mean"]
    assert out["total_cost_drag"] > 0
    assert len(out["net_returns"]) == len(gross)


def test_turnover_from_weights():
    prev = {"AAA": 0.5, "BBB": 0.5}
    new = {"BBB": 0.5, "CCC": 0.5}
    # AAA 0.5->0, CCC 0->0.5, BBB unchanged -> one-sided turnover 0.5.
    assert abs(h.turnover_from_weights(prev, new) - 0.5) < 1e-9


# --------------------------------------------------------------------------- #
# 7. Metric suite recovers a known signal; placebo collapses.
# --------------------------------------------------------------------------- #
def test_signal_recovered_and_placebo_collapses():
    panel = h.build_synthetic_panel(seed=11)
    sc = h.run_self_check(panel)
    assert sc["signal_recovered"] is True
    assert sc["honest_ic"]["mean_rank_ic"] >= h.SELFCHECK_MIN_HONEST_IC
    assert sc["placebo_collapsed"] is True
    assert abs(sc["placebo_ic"]["mean_rank_ic"]) <= h.SELFCHECK_MAX_PLACEBO_ABS


def test_quantile_spread_and_risk_metrics():
    # Monotone score vs realized -> positive top-bottom spread.
    scores = list(range(100))
    realized = [float(x) for x in range(100)]
    assert h.quantile_spread(scores, realized, q=10) > 0
    # Sharpe of a positive-drift low-vol series is positive; flat series -> None.
    assert h.sharpe([0.01, 0.012, 0.009, 0.011], periods_per_year=12) > 0
    assert h.sharpe([0.01], periods_per_year=12) is None
    # Max drawdown of a series with a dip is negative.
    mdd = h.max_drawdown([0.1, -0.2, 0.05])
    assert mdd is not None and mdd < 0


def test_ic_summary_t_stat():
    period_ics = {f"2021-{i:02d}": 0.05 for i in range(1, 13)}
    s = h.ic_summary(period_ics)
    assert s["mean_rank_ic"] == pytest.approx(0.05)
    assert s["n_periods"] == 12


# --------------------------------------------------------------------------- #
# 8. Regime / sub-period decomposition.
# --------------------------------------------------------------------------- #
def test_decompose_by_group_and_yearly_ic():
    period_ics = {"2021-01": 0.04, "2021-02": 0.06, "2022-01": -0.01, "2022-02": 0.01}
    groups = {"2021-01": "risk_on", "2021-02": "risk_on", "2022-01": "risk_off", "2022-02": "risk_off"}
    dec = h.decompose_by_group(period_ics, groups)
    assert set(dec) == {"risk_on", "risk_off"}
    assert dec["risk_on"]["mean_rank_ic"] == pytest.approx(0.05)
    yearly = h.yearly_ic(period_ics)
    assert set(yearly) == {"2021", "2022"}


# --------------------------------------------------------------------------- #
# 9. Sensitivity-surface framework reports the whole range, never a single point.
# --------------------------------------------------------------------------- #
def test_sensitivity_surface_reports_full_range():
    surf = h.sensitivity_surface([0.0, 0.5, 1.0], lambda a: -(a - 0.5) ** 2 + 1.0)
    assert len(surf["surface"]) == 3
    assert surf["diagnostics"]["n_points"] == 3
    # The framework explicitly refuses to present a best point as a recommendation.
    assert surf["diagnostics"]["best_param_NOT_a_recommendation"] is True
    assert 0.0 <= surf["diagnostics"]["plateau_ratio"] <= 1.0


# --------------------------------------------------------------------------- #
# 10. Equal-weight composite is a simple average (no learned weights).
# --------------------------------------------------------------------------- #
def test_equal_weight_composite_is_plain_average():
    comp = h.equal_weight_composite({
        "f1": {"AAA": 2.0, "BBB": 0.0},
        "f2": {"AAA": 0.0, "BBB": 4.0},
    })
    assert comp["AAA"] == pytest.approx(1.0)
    assert comp["BBB"] == pytest.approx(2.0)


# --------------------------------------------------------------------------- #
# 11. Gate matrix: every gate PASSes on the honest self-check.
# --------------------------------------------------------------------------- #
def test_all_gates_pass_on_self_check():
    panel = h.build_synthetic_panel(seed=7)
    sc = h.run_self_check(panel)
    gates = h.build_gate_matrix(sc)
    fails = [g["gate_name"] for g in gates if g["status"] != "PASS"]
    assert not fails, f"unexpected gate failures: {fails}"
    assert h.derive_recommendation(sc, gates) == h.REC_READY


def test_requirements_and_metric_catalog_present():
    reqs = h.HARNESS_REQUIREMENTS
    # All 10 core requirements covered, exactly one sensitivity-surface template.
    assert {r["core_req"] for r in reqs} == set(range(1, 11))
    templates = [r for r in reqs if r["status"] != "implemented"]
    assert len(templates) == 1 and templates[0]["req"] == "sensitivity_surface_framework"
    cat = h.build_metric_catalog()
    names = {m["metric"] for m in cat}
    for required in ("mean_rank_ic", "ic_t_stat", "yearly_ic", "decile_spread",
                     "sharpe", "max_drawdown", "turnover", "cost_adjusted_return",
                     "deflated_sharpe_ratio", "placebo_ic"):
        assert required in names
