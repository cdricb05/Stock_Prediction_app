"""Tests for Phase 5-G2 — Expanded-Coverage Event Alpha Rerun.

These tests run the real Phase 5-G2 wrapper against a SYNTHETIC local price history
(so they never touch the D: drive or the network) joined to the REAL committed Phase
3-M earnings event panel (an in-repo research artifact, not the network). The synthetic
universe spans SPY + every ticker the committed earnings-features panel covers, so the
expanded covered cross-section reaches the project's >=75-ticker event gate.

CRITICAL ISOLATION RULE: every ``run(...)`` call MUST pass ``out_dir=`` a temporary
directory. The wrapper writes every committed-safe artifact under that directory AND
routes the underlying Phase 5-G run into a sub-directory of it, so a test run can never
overwrite the committed production artifacts (the original 50-ticker Phase 5-G dir or
the Phase 5-G2 dir).
"""
from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_RUNNER_PATH = _REPO_ROOT / "research" / "run_phase5g2_expanded_event_alpha_rerun.py"
_FEATURES_CSV = (_REPO_ROOT / "research" / "output"
                 / "phase3m_earnings_estimates_signal_gate" / "earnings_features_universe.csv")


def _load_module():
    spec = importlib.util.spec_from_file_location("phase5g2_runner_test", str(_RUNNER_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


phase5g2 = _load_module()


def _covered_tickers() -> list:
    """Distinct non-SPY tickers in the committed earnings-features panel (the 75 covered)."""
    seen = []
    with open(_FEATURES_CSV, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            t = (row.get("ticker") or "").strip().upper()
            if t and t != "SPY" and t not in seen:
                seen.append(t)
    return seen


def _synth_price_history(path: Path, tickers: list) -> None:
    """Deterministic daily price history: SPY + every covered ticker, 2015-2023.

    Each ticker is a seeded geometric random walk with a mild per-name drift so the
    cross-section ranks non-trivially. Pure synthetic; no real market data."""
    rng = np.random.RandomState(11)
    all_tickers = ["SPY"] + [t for t in tickers if t != "SPY"]
    days = []
    d = dt.date(2015, 1, 1)
    end = dt.date(2023, 12, 31)
    while d <= end:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += dt.timedelta(days=1)
    rows = [("date", "ticker", "adjusted_close", "volume")]
    for ti, t in enumerate(all_tickers):
        drift = 0.0002 + 0.00015 * ((ti % 7) - 3)
        vol = 0.011 + 0.001 * (ti % 5)
        px = 50.0 + ti
        shocks = rng.normal(drift, vol, size=len(days))
        for di, day in enumerate(days):
            px *= float(np.exp(shocks[di]))
            volume = 1_000_000 + (ti * 5000) + (di % 50) * 1000
            rows.append((day, t, f"{px:.4f}", str(volume)))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)


@pytest.fixture(scope="module")
def run_ctx(tmp_path_factory):
    base = tmp_path_factory.mktemp("phase5g2")
    price_csv = base / "synth_price_history.csv"
    _synth_price_history(price_csv, _covered_tickers())
    out_dir = base / "out"
    report = phase5g2.run(price_csv=str(price_csv), out_dir=str(out_dir))
    return {"report": report, "out_dir": out_dir, "price_csv": price_csv}


@pytest.fixture(scope="module")
def report(run_ctx):
    return run_ctx["report"]


def _paths(run_ctx):
    return phase5g2._OutPaths(run_ctx["out_dir"])


def _gates(report):
    return {g["gate_name"]: g for g in report["gate_summary"]["gates"]}


# --------------------------------------------------------------------------- #
# Identity + recommendation vocabulary
# --------------------------------------------------------------------------- #
def test_phase_id(report):
    assert report["phase"] == "5-G2"


def test_recommendation_is_allowed(report):
    assert report["recommendation"] in phase5g2.ALLOWED_RECOMMENDATIONS


def test_recommendation_vocabulary_exact_set():
    assert set(phase5g2.ALLOWED_RECOMMENDATIONS) == {
        "EVENT_ALPHA_CONFIRMED_ON_EXPANDED_COVERAGE",
        "EVENT_ALPHA_WEAKENS_NEEDS_REVIEW",
        "NO_INCREMENTAL_EVENT_EDGE",
        "DATA_QUALITY_BLOCKED",
        "ERROR",
    }


def test_recommended_next_phase_is_5g3(report):
    assert report["recommended_next_phase"]["phase"] == "5-G3"


# --------------------------------------------------------------------------- #
# Expanded coverage reaches the >=75-ticker gate
# --------------------------------------------------------------------------- #
def test_expanded_coverage_at_least_75(report):
    assert report["coverage_count"] >= 75
    assert report["covered_ticker_count"] >= 75
    assert report["preconditions"]["event_coverage_count"] >= 75
    assert report["preconditions"]["raw_cache_count"] >= 75


def test_coverage_gate_passes(report):
    g = _gates(report)["coverage_at_least_75_gate"]
    assert g["status"] == "PASS"
    assert g["value"] >= 75


# --------------------------------------------------------------------------- #
# No network / no API key / no provider calls (source scan + flags)
# --------------------------------------------------------------------------- #
def test_no_network_or_provider_calls_in_source():
    src = _RUNNER_PATH.read_text(encoding="utf-8")
    forbidden = ["import requests", "import urllib", "urllib.request", "import socket",
                 "http://", "https://", "alphavantage.co", "ALPHAVANTAGE_API_KEY",
                 "FMP_API_KEY", "FINNHUB_API_KEY", "apikey=", ".pkl", ".joblib",
                 "joblib.dump", "pickle.dump", "torch.save"]
    hits = [tok for tok in forbidden if tok in src]
    assert hits == [], f"forbidden network/provider/binary tokens in source: {hits}"


def test_no_order_broker_automation_paper_trader_gcp_deploy_in_source():
    src = _RUNNER_PATH.read_text(encoding="utf-8")
    forbidden = ["place_order", "submit_order", "execute_order", "broker.execute",
                 "paper_trader", "gcloud", "kubectl", "deploy(", "create_order"]
    hits = [tok for tok in forbidden if tok in src]
    assert hits == [], f"forbidden execution/deploy tokens in source: {hits}"


def test_no_api_key_required_flag(report):
    assert report["api_key_required"] is False
    assert report["preconditions"]["api_key_required"] is False
    assert report["network_used"] is False
    assert report["preconditions"]["network_used"] is False


def test_no_api_key_required_even_with_keys_in_env(monkeypatch, tmp_path):
    for var in ("ALPHAVANTAGE_API_KEY", "FMP_API_KEY", "FINNHUB_API_KEY", "INTRINIO_API_KEY"):
        monkeypatch.setenv(var, "DUMMY_SHOULD_BE_IGNORED")
    price_csv = tmp_path / "px.csv"
    _synth_price_history(price_csv, _covered_tickers())
    rep = phase5g2.run(price_csv=str(price_csv), out_dir=str(tmp_path / "out"))
    assert rep["recommendation"] in phase5g2.ALLOWED_RECOMMENDATIONS
    assert rep["network_used"] is False
    assert rep["api_key_required"] is False


# --------------------------------------------------------------------------- #
# Raw cache untouched / unstaged
# --------------------------------------------------------------------------- #
def test_no_raw_files_modified_or_staged(report):
    assert report["raw_files_modified"] is False
    assert report["raw_files_staged"] is False
    g = _gates(report)
    assert g["no_raw_files_modified_gate"]["status"] == "PASS"
    assert g["no_raw_files_staged_gate"]["status"] == "PASS"


def test_newly_collected_raw_payloads_gitignored(report):
    # Best-effort git evidence: a newly collected (untracked) raw payload must be ignored.
    assert report["preconditions"]["new_raw_payload_gitignored"] is True


# --------------------------------------------------------------------------- #
# Same covered universe + correct Phase 5-C reference model
# --------------------------------------------------------------------------- #
def test_same_covered_universe(report):
    assert report["same_covered_universe"] is True
    assert _gates(report)["same_covered_universe_gate"]["status"] == "PASS"


def test_phase5c_reference_model_selected_and_documented(report):
    champ = report["phase5c_reference_model_used"]
    assert champ, "the Phase 5-C champion (reference) model id must be recorded"
    assert report["phase5c_reference_mean_rank_ic_covered"] is not None


def test_pit_safety_gate_passes(report):
    assert _gates(report)["pit_safety_gate"]["status"] == "PASS"


# --------------------------------------------------------------------------- #
# Required metrics present
# --------------------------------------------------------------------------- #
def test_required_metric_keys_present(report):
    required = [
        "raw_cache_count", "coverage_count", "universe_count",
        "covered_ticker_count", "covered_date_count",
        "original_phase5g_coverage_count", "original_phase5g_best_event_model",
        "original_phase5g_incremental_ic", "phase5c_reference_model_used",
        "phase5c_reference_mean_rank_ic_covered", "event_alpha_only_mean_rank_ic",
        "price_plus_event_alpha_mean_rank_ic", "event_gated_price_signal_mean_rank_ic",
        "best_event_model", "best_event_model_mean_rank_ic", "incremental_ic_vs_reference",
        "placebo_ic", "ic_t_stat_by_model", "yearly_ic_by_model", "horizon_comparison",
        "gate_summary", "recommendation", "recommended_next_phase",
    ]
    missing = [k for k in required if k not in report]
    assert missing == [], f"missing required keys: {missing}"


def test_horizon_comparison_exists(run_ctx, report):
    # 5d / 10d / 20d for every scoreboard model, in the report and as an artifact.
    for m, hz in report["horizon_comparison"].items():
        assert set(hz) == {
            "forward_excess_return_5d_vs_spy",
            "forward_excess_return_10d_vs_spy",
            "forward_excess_return_20d_vs_spy",
        }
    horizon_csv = _paths(run_ctx).horizon
    assert horizon_csv.is_file()
    with open(horizon_csv, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert {r["model"] for r in rows} >= {"phase5c_reference", "price_plus_event_alpha"}


def test_yearly_ic_breakdown_exists(run_ctx, report):
    assert report["yearly_ic_by_model"]
    assert _paths(run_ctx).yearly_ic.is_file()


def test_placebo_check_exists(run_ctx, report):
    assert "placebo_ic_by_model" in report
    # Every evaluable model carries a placebo IC (the leakage probe ran).
    for m, blk in report["placebo_ic_by_model"].items():
        assert m in ("phase5c_reference", "event_alpha_only",
                     "price_plus_event_alpha", "event_gated_price_signal")
    g = _gates(report)["placebo_materially_lower_gate"]
    assert g["status"] in ("PASS", "FAIL", "NOT_EVALUABLE")


# --------------------------------------------------------------------------- #
# Recommendation is supported by the metrics
# --------------------------------------------------------------------------- #
def test_recommendation_metrics_agree_gate_passes(report):
    assert _gates(report)["recommendation_metrics_agree_gate"]["status"] == "PASS"


def test_recommendation_consistent_with_incremental_edge(report):
    rec = report["recommendation"]
    incr = report["incremental_ic_vs_reference"]
    best = report["best_event_model"]
    if rec == "EVENT_ALPHA_CONFIRMED_ON_EXPANDED_COVERAGE":
        assert best is not None
        assert incr is not None and incr >= phase5g2.GATE_INCREMENTAL_IC
        assert report["coverage_count"] >= 75
        assert report["event_alpha_survived_expanded_coverage"] is True
    elif rec == "EVENT_ALPHA_WEAKENS_NEEDS_REVIEW":
        assert incr is not None and 0 < incr < phase5g2.GATE_INCREMENTAL_IC
    elif rec == "NO_INCREMENTAL_EVENT_EDGE":
        # no clean positive edge: incremental IC is None or <= 0
        assert incr is None or incr <= 0


def test_original_vs_expanded_comparison_present(report):
    ov = report["original_vs_expanded"]
    for k in ("original_coverage", "expanded_coverage", "original_best_event_model",
              "expanded_best_event_model", "original_incremental_ic",
              "expanded_incremental_ic", "incremental_edge_direction_vs_original",
              "event_alpha_survived_expanded_coverage"):
        assert k in ov
    # The committed original baseline is the 50-ticker Phase 5-G result.
    assert report["original_phase5g_coverage_count"] == 50
    assert report["original_phase5g_best_event_model"] == "price_plus_event_alpha"


# --------------------------------------------------------------------------- #
# Safety flags
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("flag,expected", [
    ("preview_only", True),
    ("orders_enabled", False),
    ("automation_enabled", False),
    ("broker_execution_enabled", False),
    ("production_replacement", False),
    ("network_used", False),
    ("paid_apis_used", False),
    ("deployed", False),
    ("binary_artifacts_created", False),
    ("raw_files_modified", False),
    ("raw_files_staged", False),
    ("api_key_required", False),
])
def test_safety_flags(report, flag, expected):
    assert report[flag] is expected


def test_production_edge_not_claimed(report):
    assert report["production_edge_claimed"] is False
    assert report["survivorship_biased"] is True


def test_safety_gates_pass(report):
    g = _gates(report)
    for name in ("preview_only_gate", "no_network_used_gate", "no_orders_gate",
                 "no_broker_execution_gate", "no_automation_gate",
                 "no_binary_model_artifact_gate", "no_strategy_filter_optimization_gate"):
        assert g[name]["status"] == "PASS", name


# --------------------------------------------------------------------------- #
# Artifacts written + isolation
# --------------------------------------------------------------------------- #
def test_all_committed_artifacts_written(run_ctx):
    p = _paths(run_ctx)
    for path in (p.report, p.scoreboard, p.incremental, p.gate_matrix, p.horizon,
                 p.yearly_ic, p.next_plan):
        assert path.is_file(), f"missing artifact {path}"


def test_artifacts_are_text_not_binary(run_ctx):
    p = _paths(run_ctx)
    for path in (p.report, p.scoreboard, p.incremental, p.gate_matrix, p.horizon,
                 p.yearly_ic, p.next_plan):
        assert b"\x00" not in path.read_bytes(), f"{path} looks binary"


def test_next_plan_is_gated(run_ctx, report):
    with open(_paths(run_ctx).next_plan, encoding="utf-8") as fh:
        plan = json.load(fh)
    assert plan["phase"] == "5-G3"
    assert plan["orders_enabled"] is False
    assert plan["automation_enabled"] is False
    assert plan["deployed"] is False
    assert plan["network_used"] is False
    proceed = report["recommendation"] == "EVENT_ALPHA_CONFIRMED_ON_EXPANDED_COVERAGE"
    assert plan["proceed_to_phase5h_strategy_test"] is proceed


def test_run_does_not_touch_production_dirs(tmp_path):
    """A test run with out_dir=tmp MUST NOT modify the committed production artifacts:
    neither the Phase 5-G2 dir nor the original 50-ticker Phase 5-G dir."""
    prod_g2 = phase5g2._OUT_DIR / "phase5g2_expanded_event_alpha_rerun.json"
    prod_g = phase5g2._ORIGINAL_PHASE5G_REPORT
    before_g2 = prod_g2.read_bytes() if prod_g2.is_file() else None
    before_g = prod_g.read_bytes() if prod_g.is_file() else None
    price_csv = tmp_path / "px.csv"
    _synth_price_history(price_csv, _covered_tickers())
    phase5g2.run(price_csv=str(price_csv), out_dir=str(tmp_path / "out"))
    after_g2 = prod_g2.read_bytes() if prod_g2.is_file() else None
    after_g = prod_g.read_bytes() if prod_g.is_file() else None
    assert before_g2 == after_g2, "run(out_dir=...) overwrote the Phase 5-G2 production artifact"
    assert before_g == after_g, "run(out_dir=...) overwrote the original Phase 5-G artifact"
    assert (tmp_path / "out" / "phase5g2_expanded_event_alpha_rerun.json").is_file()


def test_underlying_run_routed_to_subdir_not_committed_dir(run_ctx):
    """The reused Phase 5-G run must land under the G2 out_dir, never the committed
    original Phase 5-G output directory."""
    p = _paths(run_ctx)
    assert p.expanded_run_dir.is_dir()
    assert (p.expanded_run_dir / "phase5g_earnings_revision_alpha.json").is_file()
    # The committed original 5-G dir is NOT under the temp out_dir.
    assert phase5g2._ORIGINAL_PHASE5G_REPORT.resolve() != \
        (p.expanded_run_dir / "phase5g_earnings_revision_alpha.json").resolve()


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #
def test_determinism(tmp_path):
    tickers = _covered_tickers()
    p1 = tmp_path / "a.csv"
    _synth_price_history(p1, tickers)
    r1 = phase5g2.run(price_csv=str(p1), out_dir=str(tmp_path / "out1"))
    r2 = phase5g2.run(price_csv=str(p1), out_dir=str(tmp_path / "out2"))
    assert r1["recommendation"] == r2["recommendation"]
    assert r1["incremental_ic_vs_reference"] == r2["incremental_ic_vs_reference"]
    assert r1["best_event_model"] == r2["best_event_model"]
