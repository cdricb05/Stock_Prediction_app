"""Tests for Phase 6-A — Cross-Asset Macro Context Alpha Pack.

These tests run the real Phase 6-A harness against a fully SYNTHETIC local price
history AND fully SYNTHETIC macro input CSVs (so they never touch the D: drive, the
real research/input macro files, the network, or any provider). The synthetic macro
files mimic the FRED schemas the runner expects.

CRITICAL ISOLATION RULE: every ``run(...)`` call MUST pass ``out_dir=`` a temporary
directory and ``price_csv=`` / ``macro_input_dir=`` synthetic paths, so a test run can
never overwrite committed artifacts or read real data.
"""
from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import json
import re
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_RUNNER_PATH = _REPO_ROOT / "research" / "run_phase6a_cross_asset_macro_context_alpha.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("phase6a_runner_test", str(_RUNNER_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


phase6a = _load_module()


def _weekdays(start: dt.date, end: dt.date) -> list:
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += dt.timedelta(days=1)
    return days


def _synth_price_history(path: Path, n_tickers: int = 40) -> None:
    """Deterministic daily price history: SPY + n stocks, 2014-2023 weekdays."""
    rng = np.random.RandomState(7)
    tickers = ["SPY"] + [f"SYN{i:03d}" for i in range(n_tickers)]
    days = _weekdays(dt.date(2014, 1, 1), dt.date(2023, 12, 31))
    rows = [("date", "ticker", "adjusted_close", "volume")]
    for ti, t in enumerate(tickers):
        drift = 0.0002 + 0.00015 * ((ti % 7) - 3)
        vol = 0.011 + 0.001 * (ti % 5)
        px = 50.0 + ti
        shocks = rng.normal(drift, vol, size=len(days))
        for di, day in enumerate(days):
            px *= float(np.exp(shocks[di]))
            rows.append((day, t, f"{px:.4f}", str(1_000_000 + ti * 5000 + (di % 50) * 1000)))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)


def _synth_macro(dirpath: Path) -> None:
    """Synthetic FRED-style macro CSVs matching the schemas the runner reads."""
    dirpath.mkdir(parents=True, exist_ok=True)
    rng = np.random.RandomState(13)
    days = _weekdays(dt.date(2013, 1, 1), dt.date(2023, 12, 31))

    def _walk(start, drift, vol, floor=None):
        v = start
        out = []
        for s in rng.normal(drift, vol, size=len(days)):
            v = v + s if floor is None else max(floor, v + s)
            out.append(v)
        return out

    oil = _walk(60.0, 0.0, 0.9, floor=5.0)
    usd = _walk(100.0, 0.0, 0.3, floor=50.0)
    dgs10 = _walk(2.5, 0.0, 0.03, floor=0.1)
    dgs2 = _walk(1.5, 0.0, 0.03, floor=0.05)

    with open(dirpath / "DCOILWTICO.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["observation_date", "DCOILWTICO"])
        for d, v in zip(days, oil):
            w.writerow([d, f"{v:.2f}"])
    with open(dirpath / "DTWEXBGS.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["observation_date", "DTWEXBGS"])
        for d, v in zip(days, usd):
            w.writerow([d, f"{v:.4f}"])
    with open(dirpath / "fredgraph.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["observation_date", "DGS10", "DGS2"])
        for d, a, b in zip(days, dgs10, dgs2):
            w.writerow([d, f"{a:.2f}", f"{b:.2f}"])
    # Monthly series.
    months = []
    d = dt.date(2013, 1, 1)
    while d <= dt.date(2023, 12, 1):
        months.append(d.isoformat())
        d = (d.replace(day=28) + dt.timedelta(days=7)).replace(day=1)
    cpi = 230.0
    ff = 0.5
    with open(dirpath / "CPIAUCSL.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["observation_date", "CPIAUCSL"])
        for m in months:
            cpi *= 1.002
            w.writerow([m, f"{cpi:.3f}"])
    with open(dirpath / "FEDFUNDS.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["observation_date", "FEDFUNDS"])
        for i, m in enumerate(months):
            w.writerow([m, f"{ff + 0.02 * (i % 10):.2f}"])


@pytest.fixture(scope="module")
def run_ctx(tmp_path_factory):
    base = tmp_path_factory.mktemp("phase6a")
    price_csv = base / "synth_price_history.csv"
    macro_dir = base / "macro"
    _synth_price_history(price_csv)
    _synth_macro(macro_dir)
    out_dir = base / "out"
    report = phase6a.run(price_csv=str(price_csv), out_dir=str(out_dir),
                         macro_input_dir=str(macro_dir))
    return {"report": report, "out_dir": out_dir, "price_csv": price_csv,
            "macro_dir": macro_dir, "base": base}


@pytest.fixture(scope="module")
def report(run_ctx):
    return run_ctx["report"]


# --------------------------------------------------------------------------- #
# Identity / vocabulary
# --------------------------------------------------------------------------- #
def test_phase_id(report):
    assert report["phase"] == "6-A"


def test_recommendation_in_allowed_set(report):
    assert report["recommendation"] in set(phase6a.ALLOWED_RECOMMENDATIONS)


def test_allowed_recommendation_vocabulary_exact(report):
    assert set(report["recommendation_allowed_values"]) == {
        "MACRO_CONTEXT_ALPHA_CONFIRMED",
        "MACRO_CONTEXT_ALPHA_WEAK",
        "NEEDS_CONTROLLED_CROSS_ASSET_DATA_COLLECTION",
        "DATA_QUALITY_BLOCKED",
        "ERROR",
    }


def test_recommended_next_phase_is_6b(report):
    assert report["recommended_next_phase"]["phase"] == "6-B"


def test_models_compared_exact(report):
    assert report["models_compared"] == [
        "phase5c_reference", "price_only", "macro_only",
        "price_plus_macro", "price_plus_macro_regime_interaction",
    ]


# --------------------------------------------------------------------------- #
# Macro pack built + leakage safety
# --------------------------------------------------------------------------- #
def test_macro_features_built(report):
    assert report["macro_records_usable"] > 0


def test_same_dates_universe_comparison(report):
    assert report["same_dates_universe"] is True
    assert report["common_scored_dates"] > 0


def test_leakage_clean_and_macro_lagged(report):
    assert report["leakage_clean"] is True
    assert report["leakage_report"]["macro_features_lagged_t_minus_1"] is True
    assert report["leakage_report"]["embargo_respected"] in (True, None)
    assert report["leakage_report"]["features_use_future_data"] in (False, None)


def test_placebo_present_for_every_model(report):
    placebo = report["placebo_by_model"]
    for m in report["models_compared"]:
        assert m in placebo


def test_yearly_ic_breakdown_present(report):
    yearly = report["yearly_ic_by_model"]
    assert "phase5c_reference" in yearly and len(yearly["phase5c_reference"]) >= 1


def test_ic_t_stat_present(report):
    assert "phase5c_reference" in report["ic_t_stat_by_model"]


# --------------------------------------------------------------------------- #
# Reference correctness + incremental edge
# --------------------------------------------------------------------------- #
def test_phase5c_reference_is_a_price_only_champion(report):
    # The champion is dynamically selected from the Phase 5-C price-only model set.
    assert report["phase5c_reference_model_used"] in (
        "cross_sectional_composite_zscore",
        "ridge_cross_sectional_return_model",
        "top_quintile_score_model",
    )
    assert report["phase5c_reference_mean_rank_ic_common"] is not None


def test_incremental_ic_computed_vs_reference(report):
    inc = report["incremental_by_model_common"]
    for m in ("macro_only", "price_plus_macro", "price_plus_macro_regime_interaction"):
        assert m in inc


def test_within_family_macro_increment_reported(report):
    wf = report["within_family_macro_increment_common"]
    assert "price_plus_macro" in wf
    assert report["price_only_ridge_mean_rank_ic_common"] is not None


def test_price_only_ridge_sanity(report):
    # The price-only RIDGE baseline must produce a finite IC (it is the within-family
    # baseline used to isolate the macro contribution).
    assert isinstance(report["price_only_ridge_mean_rank_ic_common"], float)


def test_recommendation_supported_by_metrics(report):
    assert report["recommendation_supported_by_metrics"] is True


def test_confirmed_only_if_gate_cleared(report):
    if report["recommendation"] == "MACRO_CONTEXT_ALPHA_CONFIRMED":
        assert report["best_augmented_incremental_ic"] > phase6a.GATE_INCREMENTAL_IC
        assert report["macro_context_improved_signal"] is True
    else:
        assert report["macro_context_improved_signal"] is False


# --------------------------------------------------------------------------- #
# Gate matrix
# --------------------------------------------------------------------------- #
def test_incremental_gate_present(report):
    names = {g["gate_name"] for g in report["gate_summary"]["gates"]}
    assert "incremental_ic_over_reference_gate" in names
    assert "placebo_materially_lower_gate" in names
    assert "leakage_clean_gate" in names
    assert "same_universe_comparison_gate" in names


def test_safety_gates_pass(report):
    gates = {g["gate_name"]: g for g in report["gate_summary"]["gates"]}
    for nm in ("no_orders_gate", "no_broker_execution_gate", "no_automation_gate",
               "no_network_gate", "no_paid_api_gate", "no_deploy_gate",
               "no_strategy_test_gate", "no_binary_artifact_gate", "preview_only_gate"):
        assert gates[nm]["status"] == "PASS"


# --------------------------------------------------------------------------- #
# Safety flags
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("flag,expected", [
    ("network_used", False),
    ("paid_apis_used", False),
    ("api_key_required", False),
    ("live_data_used", False),
    ("preview_only", True),
    ("orders_enabled", False),
    ("broker_execution_enabled", False),
    ("automation_enabled", False),
    ("deployed", False),
    ("strategy_test_run", False),
    ("binary_artifacts_created", False),
    ("production_replacement", False),
    ("paper_trader_touched", False),
    ("gcp_touched", False),
    ("raw_files_modified", False),
    ("d_drive_written", False),
    ("production_edge_claimed", False),
])
def test_safety_flags(report, flag, expected):
    assert report[flag] is expected


# --------------------------------------------------------------------------- #
# Output artifacts
# --------------------------------------------------------------------------- #
def test_all_committed_safe_artifacts_exist(run_ctx):
    out = run_ctx["out_dir"]
    for name in ("phase6a_cross_asset_macro_context_alpha.json",
                 "cross_asset_data_inventory.csv",
                 "cross_asset_feature_catalog.csv",
                 "cross_asset_model_scoreboard.csv",
                 "cross_asset_incremental_edge_report.csv",
                 "cross_asset_gate_matrix.csv",
                 "phase6b_next_plan.json"):
        assert (out / name).is_file(), f"missing artifact {name}"


def test_no_binary_artifacts_written(run_ctx):
    bad = {".pkl", ".joblib", ".onnx", ".pt", ".h5", ".keras", ".npy", ".npz"}
    for p in run_ctx["out_dir"].rglob("*"):
        assert p.suffix.lower() not in bad, f"unexpected binary artifact {p}"


def test_next_plan_records_controlled_collection(run_ctx):
    plan = json.loads((run_ctx["out_dir"] / "phase6b_next_plan.json").read_text(encoding="utf-8"))
    assert plan["phase"] == "6-B"
    assert plan["controlled_collection_plan"]["do_not_run_live_now"] is True
    assert len(plan["controlled_collection_plan"]["exact_future_commands"]) >= 1


def test_data_inventory_present(report):
    inv = report["data_inventory"]
    assert "global_etf_readiness" in inv
    assert isinstance(inv["full_framework_cross_asset_ready"], bool)
    assert inv["daily_factors_available"] >= 1


# --------------------------------------------------------------------------- #
# Source-level hygiene (no live network / provider / unsafe ops)
# --------------------------------------------------------------------------- #
def test_source_makes_no_network_calls():
    src = _RUNNER_PATH.read_text(encoding="utf-8")
    for pat in (r"\bimport requests\b", r"\bimport urllib\b", r"\burlopen\b",
                r"\bhttp\.client\b", r"\bimport socket\b", r"requests\.get\(",
                r"requests\.post\("):
        assert re.search(pat, src) is None, f"network pattern {pat!r} present in runner source"


def test_source_has_no_forbidden_operations():
    src = _RUNNER_PATH.read_text(encoding="utf-8").lower()
    # No order/broker/automation execution, no deploy, no db writes, no D: writes.
    for token in ("place_order", "submit_order", "broker.", "execute_trade",
                  "subprocess.", "os.system", "shutil.rmtree", "to_sql(", "open(r\"d:"):
        assert token not in src, f"forbidden token {token!r} present in runner source"


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #
def test_determinism(run_ctx, tmp_path):
    out2 = tmp_path / "out2"
    r2 = phase6a.run(price_csv=str(run_ctx["price_csv"]), out_dir=str(out2),
                     macro_input_dir=str(run_ctx["macro_dir"]))
    r1 = run_ctx["report"]
    assert r2["recommendation"] == r1["recommendation"]
    assert r2["best_augmented_incremental_ic"] == r1["best_augmented_incremental_ic"]
    assert r2["phase5c_reference_mean_rank_ic_common"] == r1["phase5c_reference_mean_rank_ic_common"]
