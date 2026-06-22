"""Tests for Phase 5-G — Earnings / Revision / Post-Earnings Drift Alpha Module.

These tests run the real harness against a SYNTHETIC local price history (so they
never touch the D: drive or the network) joined to the REAL committed Phase 3-M
earnings event panel (an in-repo research artifact, not the network). They assert
the safety contract, the point-in-time discipline, the honest partial-data handling
(estimate-revision features omitted, not faked), and the required output shape.

CRITICAL ISOLATION RULE: every ``phase5g.run(...)`` call MUST pass ``out_dir=`` a
temporary directory. The runner writes every artifact under that directory, so a
test run can never overwrite the committed production artifacts. (A prior version
had no out_dir parameter, so the test suite silently clobbered the production JSON
with synthetic-data metrics — the exact bug the audit reconciliation fixed.)
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

_RUNNER_PATH = _REPO_ROOT / "research" / "run_phase5g_earnings_revision_alpha.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("phase5g_runner_test", str(_RUNNER_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


phase5g = _load_module()

# The 50 tickers the real Phase 3-M earnings panel covers (alphabetically first large caps).
_EARNINGS_TICKERS = [
    "AAPL", "ABBV", "ABT", "ACN", "ADBE", "ADI", "ADP", "AMAT", "AMD", "AMGN",
    "AMZN", "AON", "APD", "APH", "AVGO", "AXP", "BA", "BAC", "BKNG", "BLK",
    "BMY", "BSX", "C", "CAT", "CB", "CDNS", "CI", "CL", "CME", "CMG",
    "COP", "COST", "CRM", "CSCO", "CVS", "CVX", "DE", "DIS", "DUK", "ELV",
    "EMR", "EOG", "EQIX", "ETN", "FDX", "GD", "GE", "GILD", "GOOG", "GOOGL",
]


def _synth_price_history(path: Path) -> None:
    """Deterministic daily price history: SPY + the 50 earnings tickers, 2015-2023.

    Each ticker is a seeded geometric random walk with a mild per-name drift so the
    cross-section ranks non-trivially. Pure synthetic; no real market data."""
    rng = np.random.RandomState(7)
    tickers = ["SPY"] + _EARNINGS_TICKERS
    # Weekday calendar 2015-01-01 .. 2023-12-31.
    days = []
    d = dt.date(2015, 1, 1)
    end = dt.date(2023, 12, 31)
    while d <= end:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += dt.timedelta(days=1)
    rows = [("date", "ticker", "adjusted_close", "volume")]
    for ti, t in enumerate(tickers):
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
    """Run the harness ONCE into a temp out_dir; never touches the production dir."""
    base = tmp_path_factory.mktemp("phase5g")
    price_csv = base / "synth_price_history.csv"
    _synth_price_history(price_csv)
    out_dir = base / "out"
    report = phase5g.run(price_csv=str(price_csv), out_dir=str(out_dir))
    return {"report": report, "out_dir": out_dir, "price_csv": price_csv}


@pytest.fixture(scope="module")
def full_report(run_ctx):
    return run_ctx["report"]


def _paths(run_ctx):
    return phase5g._OutPaths(run_ctx["out_dir"])


# --------------------------------------------------------------------------- #
# Phase identity + recommendation vocabulary
# --------------------------------------------------------------------------- #
def test_phase_id(full_report):
    assert full_report["phase"] == "5-G"


def test_recommendation_is_allowed(full_report):
    assert full_report["recommendation"] in phase5g.ALLOWED_RECOMMENDATIONS


def test_recommendation_vocabulary_exact_set():
    assert set(phase5g.ALLOWED_RECOMMENDATIONS) == {
        "READY_FOR_PHASE5H_EVENT_ALPHA_STRATEGY_TEST",
        "EVENT_ALPHA_IMPROVES_SIGNAL",
        "EVENT_DATA_PARTIAL_BUT_USEFUL",
        "EVENT_DATA_BLOCKER",
        "NO_INCREMENTAL_EVENT_EDGE",
        "ERROR",
    }


def test_recommended_next_phase_is_5h(full_report):
    assert full_report["recommended_next_phase"]["phase"] == "5-H"


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
])
def test_safety_flags(full_report, flag, expected):
    assert full_report[flag] is expected


def test_production_edge_not_claimed(full_report):
    assert full_report["production_edge_claimed"] is False
    assert full_report["survivorship_biased"] is True


# --------------------------------------------------------------------------- #
# Required main-JSON keys
# --------------------------------------------------------------------------- #
def test_required_json_keys_present(full_report):
    required = [
        "phase", "objective", "local_event_data_found", "usable_event_data",
        "event_data_sources", "coverage_summary", "pit_safety_summary",
        "features_built", "models_evaluated", "phase5c_comparison",
        "phase5f0c_comparison", "validation_gate_summary", "recommendation",
        "recommended_next_phase", "preview_only", "orders_enabled",
        "automation_enabled", "broker_execution_enabled", "production_replacement",
        "network_used", "paid_apis_used", "deployed", "binary_artifacts_created",
    ]
    missing = [k for k in required if k not in full_report]
    assert missing == [], f"missing keys: {missing}"


# --------------------------------------------------------------------------- #
# Inventory + PIT safety
# --------------------------------------------------------------------------- #
def test_event_data_inventory_exists(run_ctx):
    inv = _paths(run_ctx).inventory
    assert inv.is_file()
    with open(inv, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows, "inventory must list candidate event files"
    roles = {r["role"] for r in rows}
    assert "earnings_events" in roles and "earnings_features" in roles
    assert "analyst_revision_inventory" in roles


def test_pit_safety_report_exists_and_checks_report_date(run_ctx):
    pit_path = _paths(run_ctx).pit_safety
    assert pit_path.is_file()
    with open(pit_path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    # The earnings feature/event rows must declare availability = report date (PIT-safe).
    by_role = {r["role"]: r for r in rows}
    feat = by_role.get("earnings_features") or by_role.get("earnings_events")
    assert feat is not None
    assert feat["availability_is_report_date"] == "True"
    assert feat["uses_fiscal_end_as_timestamp"] == "False"
    assert feat["point_in_time_safe"] == "True"


def test_pit_safety_explicitly_checked_in_report(full_report):
    pit = full_report["pit_safety_summary"]
    assert pit["availability_is_report_date"] is True
    assert pit["uses_fiscal_end_as_timestamp"] is False
    assert pit["no_future_event_used"] is True
    assert "availability_date <= rebalance_date" in pit["asof_join"]


# --------------------------------------------------------------------------- #
# Missing fields are NOT faked
# --------------------------------------------------------------------------- #
def test_revision_features_not_faked(full_report):
    fb = full_report["features_built"]
    # The committed analyst-revision inventory is NOT a usable PIT series.
    assert fb["estimate_revision_features_built"] is False
    assert fb["revision_omitted_reason"]  # a non-empty reason is recorded


def test_revision_feature_catalog_marks_omitted(run_ctx):
    cat = _paths(run_ctx).feature_catalog
    assert cat.is_file()
    with open(cat, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    rev = [r for r in rows if r["family"] == "estimate_revision"]
    assert rev, "revision features must appear in the catalog"
    for r in rev:
        assert r["built"] == "False"
        assert r["omitted_reason"]  # explains why it was not built


def test_surprise_and_drift_features_built(full_report):
    fb = full_report["features_built"]
    assert fb["earnings_surprise_features_built"] is True
    assert fb["post_earnings_drift_features_built"] is True


# --------------------------------------------------------------------------- #
# Models + Phase 5-C / F0C comparisons
# --------------------------------------------------------------------------- #
def test_required_models_present(full_report):
    assert full_report["models_evaluated"] == [
        "phase5c_reference", "event_alpha_only", "price_plus_event_alpha",
        "event_gated_price_signal", "best_event_candidate",
    ]


def test_phase5c_comparison_exists(full_report):
    cmp = full_report["phase5c_comparison"]
    assert "reference_mean_rank_ic_covered" in cmp
    sb = full_report["model_scoreboard"]
    assert "phase5c_reference" in sb


def test_phase5f0c_comparison_exists(full_report):
    assert "phase5f0c_comparison" in full_report


def test_incremental_edge_block_present(full_report):
    inc = full_report["incremental_edge_vs_phase5c"]
    assert "incremental_mean_rank_ic" in inc
    assert "reference_mean_rank_ic" in inc
    assert "best_event_mean_rank_ic" in inc


def test_multi_horizon_labels_evaluated(full_report):
    sb = full_report["model_scoreboard"]
    ref = sb["phase5c_reference"]
    if ref.get("evaluable"):
        mh = ref["multi_horizon_ic"]
        assert set(mh) == {
            "forward_excess_return_5d_vs_spy",
            "forward_excess_return_10d_vs_spy",
            "forward_excess_return_20d_vs_spy",
        }


# --------------------------------------------------------------------------- #
# Phase 5-C reference (baseline) is dynamically selected + documented
# --------------------------------------------------------------------------- #
def test_phase5c_reference_model_documented(full_report):
    """The reference must be an explicitly-recorded champion, not an implicit guess,
    and the two places it is reported must agree."""
    meta = full_report["phase5c_reference_meta"]
    champ = meta.get("champion_model")
    assert champ, "champion model id must be recorded in phase5c_reference_meta"
    assert full_report["phase5c_comparison"]["champion_model"] == champ


def test_runner_does_not_hardcode_phase5c_baseline():
    """The champion is chosen by max mean rank IC among the 5-C models, never pinned
    to a hardcoded model id."""
    src = _RUNNER_PATH.read_text(encoding="utf-8")
    assert "cross_sectional_composite_zscore" not in src
    assert "> champ_eval" in src  # the dynamic max-IC selection is present


# --------------------------------------------------------------------------- #
# Coverage + readiness gating
# --------------------------------------------------------------------------- #
def test_coverage_summary_present(full_report):
    cov = full_report["coverage_summary"]
    assert cov["universe_size"] >= 1
    assert 0 <= cov["covered_tickers"] <= cov["universe_size"]
    assert "coverage_fraction" in cov


def test_coverage_gate_in_matrix(full_report):
    gates = {g["gate_name"]: g for g in full_report["validation_gate_summary"]["gates"]}
    assert "coverage_sufficient_gate" in gates
    assert "incremental_ic_gate" in gates
    assert "no_future_event_leakage_gate" in gates
    assert "pit_safety_gate" in gates


def test_leakage_gate_passes(full_report):
    gates = {g["gate_name"]: g for g in full_report["validation_gate_summary"]["gates"]}
    assert gates["no_future_event_leakage_gate"]["status"] == "PASS"
    assert gates["pit_safety_gate"]["status"] == "PASS"


def test_safety_gates_pass(full_report):
    gates = {g["gate_name"]: g for g in full_report["validation_gate_summary"]["gates"]}
    for name in ("preview_only_gate", "no_network_gate", "no_orders_gate",
                 "no_broker_execution_gate", "no_automation_gate",
                 "no_binary_model_artifact_gate", "no_fundamentals_as_alpha_gate"):
        assert gates[name]["status"] == "PASS", name


def test_not_forced_ready_when_coverage_insufficient(full_report):
    """With <75 covered tickers, the harness must NOT emit a shadow-ready / improves
    verdict — coverage gates that down to partial-but-useful or no-edge."""
    cov = full_report["coverage_summary"]
    if cov["covered_tickers"] < phase5g.GATE_COVERAGE_SUFFICIENT:
        assert full_report["recommendation"] in (
            "EVENT_DATA_PARTIAL_BUT_USEFUL", "NO_INCREMENTAL_EVENT_EDGE",
            "EVENT_DATA_BLOCKER")


# --------------------------------------------------------------------------- #
# Recommendation is supported by its own scoreboard metrics
# --------------------------------------------------------------------------- #
def test_recommendation_supported_by_scoreboard(full_report):
    rec = full_report["recommendation"]
    inc = full_report["incremental_edge_vs_phase5c"]["incremental_mean_rank_ic"]
    best = full_report["best_event_candidate"]
    if rec == "NO_INCREMENTAL_EVENT_EDGE":
        # an "edge" verdict was NOT issued, so there must be no clean positive edge
        assert best is None or inc is None or inc <= 0
    if rec in ("EVENT_DATA_PARTIAL_BUT_USEFUL", "EVENT_ALPHA_IMPROVES_SIGNAL",
               "READY_FOR_PHASE5H_EVENT_ALPHA_STRATEGY_TEST"):
        # an edge verdict requires a real positive incremental IC over the baseline
        assert best is not None
        assert inc is not None and inc > 0
    if rec in ("EVENT_ALPHA_IMPROVES_SIGNAL",
               "READY_FOR_PHASE5H_EVENT_ALPHA_STRATEGY_TEST"):
        # the strong verdicts additionally require sufficient coverage
        assert full_report["coverage_summary"]["covered_tickers"] >= \
            phase5g.GATE_COVERAGE_SUFFICIENT


# --------------------------------------------------------------------------- #
# Artifacts
# --------------------------------------------------------------------------- #
def test_all_nine_artifacts_written(run_ctx):
    p = _paths(run_ctx)
    expected = [
        p.report, p.inventory, p.pit_safety, p.feature_catalog, p.panel_sample,
        p.scoreboard, p.incremental, p.gate_matrix, p.next_plan,
    ]
    for path in expected:
        assert path.is_file(), f"missing artifact {path}"


def test_audit_artifact_written(run_ctx):
    assert _paths(run_ctx).audit.is_file()


def test_artifacts_are_text_not_binary(run_ctx):
    p = _paths(run_ctx)
    for path in (p.scoreboard, p.incremental, p.gate_matrix, p.report, p.audit):
        raw = path.read_bytes()
        assert b"\x00" not in raw, f"{path} looks binary"


def test_next_plan_is_gated(run_ctx, full_report):
    with open(_paths(run_ctx).next_plan, encoding="utf-8") as fh:
        plan = json.load(fh)
    assert plan["orders_enabled"] is False
    assert plan["automation_enabled"] is False
    assert plan["deployed"] is False
    assert plan["network_used"] is False
    # proceed flag is consistent with the recommendation.
    proceed = full_report["recommendation"] in (
        "READY_FOR_PHASE5H_EVENT_ALPHA_STRATEGY_TEST", "EVENT_ALPHA_IMPROVES_SIGNAL")
    assert plan["proceed_to_event_alpha_strategy_test"] is proceed


def test_blocker_minimum_fields_documented(full_report):
    req = full_report["data_blocker_minimum_required_fields"]
    assert "earnings_surprise" in req and "estimate_revision" in req


# --------------------------------------------------------------------------- #
# Audit / consistency reconciliation (the bug this work fixed)
# --------------------------------------------------------------------------- #
def test_audit_reconciliation_consistent(run_ctx):
    """JSON recommendation and the console summary recommendation must agree, the
    verdict must be supported by the scoreboard metrics, and the run must not have
    overwritten production artifacts."""
    with open(_paths(run_ctx).audit, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows, "audit artifact must contain a reconciliation row"
    row = rows[0]
    assert row["json_recommendation"] == run_ctx["report"]["recommendation"]
    assert row["json_recommendation"] == row["console_summary_recommendation"]
    assert row["recommendation_supported_by_metrics"] == "True"
    assert row["tests_write_to_temp_dir_only"] == "True"
    assert row["production_artifact_overwrite_detected"] == "False"
    assert row["final_status"] == "CONSISTENT"
    assert row["phase5c_reference_model_used"]  # baseline model id recorded


def test_run_does_not_touch_production_dir(tmp_path):
    """A test run with out_dir=tmp MUST NOT modify the committed production artifact.
    (This is the guard against the original test-clobbers-production bug.)"""
    prod = phase5g._OUT_DIR / "phase5g_earnings_revision_alpha.json"
    before = prod.read_bytes() if prod.is_file() else None
    price_csv = tmp_path / "px.csv"
    _synth_price_history(price_csv)
    out_dir = tmp_path / "out"
    phase5g.run(price_csv=str(price_csv), out_dir=str(out_dir))
    after = prod.read_bytes() if prod.is_file() else None
    assert before == after, "run(out_dir=...) overwrote the production artifact"
    assert (out_dir / "phase5g_earnings_revision_alpha.json").is_file()


def test_test_module_redirects_all_runs_to_temp():
    """The runner's own guard must confirm this test file never calls run() without
    out_dir= (so the suite can never write into the production directory)."""
    assert phase5g._tests_write_to_temp_dir_only() is True


# --------------------------------------------------------------------------- #
# As-of join is leakage-free (unit test on the join helper)
# --------------------------------------------------------------------------- #
def test_asof_join_never_uses_future_event():
    events = [
        {"availability_date": "2020-01-15"},
        {"availability_date": "2020-04-20"},
        {"availability_date": "2020-07-22"},
    ]
    # Before the first event -> None (no look-ahead to a future earnings print).
    assert phase5g._latest_event_asof(events, "2020-01-01") is None
    # Strictly on/after an event date -> that event, never the next one.
    assert phase5g._latest_event_asof(events, "2020-04-20")["availability_date"] == "2020-04-20"
    assert phase5g._latest_event_asof(events, "2020-06-30")["availability_date"] == "2020-04-20"
    assert phase5g._latest_event_asof(events, "2099-01-01")["availability_date"] == "2020-07-22"


def test_load_earnings_events_are_sorted_and_pit():
    events = phase5g.load_earnings_events()
    assert events, "real committed earnings panel must load"
    assert "AAPL" in events
    dates = [e["availability_date"] for e in events["AAPL"]]
    assert dates == sorted(dates)


# --------------------------------------------------------------------------- #
# Source-level safety scan (no network / paid API / provider work)
# --------------------------------------------------------------------------- #
def test_source_has_no_network_or_provider_calls():
    src = _RUNNER_PATH.read_text(encoding="utf-8")
    forbidden = ["import requests", "import urllib", "urllib.request", "import socket",
                 "http://", "https://", "alphavantage.co", "ALPHAVANTAGE_API_KEY",
                 "FMP_API_KEY", "apikey=", "os.environ"]
    hits = [tok for tok in forbidden if tok in src]
    assert hits == [], f"forbidden network/provider tokens in source: {hits}"


def test_source_has_no_order_broker_automation_or_binary_writes():
    src = _RUNNER_PATH.read_text(encoding="utf-8")
    forbidden = ["place_order", "submit_order", "execute_order", "broker.execute",
                 ".pkl", ".joblib", ".onnx", "joblib.dump", "pickle.dump", "torch.save"]
    hits = [tok for tok in forbidden if tok in src]
    assert hits == [], f"forbidden execution/binary tokens in source: {hits}"


def test_no_api_key_required(monkeypatch, tmp_path):
    """The harness runs with no provider key in the environment."""
    for var in ("ALPHAVANTAGE_API_KEY", "FMP_API_KEY", "FINNHUB_API_KEY", "INTRINIO_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    price_csv = tmp_path / "px.csv"
    _synth_price_history(price_csv)
    report = phase5g.run(price_csv=str(price_csv), out_dir=str(tmp_path / "out"))
    assert report["recommendation"] in phase5g.ALLOWED_RECOMMENDATIONS
    assert report["network_used"] is False


def test_d_drive_not_written():
    src = _RUNNER_PATH.read_text(encoding="utf-8")
    # The only D: reference is the read-only price-history candidate inherited from 5-C
    # (via p5c._data_path); this runner never opens D: for writing.
    assert "open(r\"D:" not in src and 'open("D:' not in src


# --------------------------------------------------------------------------- #
# Determinism (two consecutive offline runs into temp dirs agree exactly)
# --------------------------------------------------------------------------- #
def test_determinism(tmp_path):
    p1 = tmp_path / "a.csv"
    _synth_price_history(p1)
    r1 = phase5g.run(price_csv=str(p1), out_dir=str(tmp_path / "out1"))
    r2 = phase5g.run(price_csv=str(p1), out_dir=str(tmp_path / "out2"))
    assert r1["recommendation"] == r2["recommendation"]
    assert (r1["incremental_edge_vs_phase5c"]["incremental_mean_rank_ic"]
            == r2["incremental_edge_vs_phase5c"]["incremental_mean_rank_ic"])
    # the full per-model scoreboard ICs must match across the two runs
    for m, blk in r1["model_scoreboard"].items():
        assert blk.get("mean_rank_ic") == r2["model_scoreboard"][m].get("mean_rank_ic")
