"""Targeted tests for Phase 13-A - Current Champion Alpha Paper-Test Package.

The runner is fully offline (reads the frozen 10-L panel + owned local EOD prices only), so this
suite makes ZERO network calls and touches no Paper Trader state. Structural assertions are used
rather than exact prices so the suite stays robust to owned-data refreshes.
"""
import csv
import json
import os
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STEM = "phase13a_current_champion_alpha_paper_test_package"
RUNNER = REPO / "research" / f"run_{STEM}.py"
DOCS = REPO / "docs" / f"{STEM}_v1.md"
OUT_DIR = REPO / "research" / "output" / STEM
OUT_JSON = OUT_DIR / f"{STEM}.json"

CSVS = [
    "current_alpha_full_ranked_universe.csv",
    "current_alpha_top25_candidates.csv",
    "current_alpha_top50_candidates.csv",
    "current_alpha_bottom25_avoid_list.csv",
    "current_alpha_sector_exposure.csv",
    "current_alpha_missing_data_report.csv",
    "current_alpha_paper_portfolio_top25.csv",
    "current_alpha_paper_portfolio_top50.csv",
    "current_alpha_tracking_template.csv",
    "current_alpha_risk_limits.csv",
    "current_alpha_go_no_go_scorecard.csv",
]

ALLOWED = {
    "CURRENT_ALPHA_READY_FOR_PAPER_TEST", "CURRENT_ALPHA_PACKAGE_READY_PANEL_ONLY",
    "CURRENT_ALPHA_NEEDS_FRESH_PRICES", "CURRENT_ALPHA_REJECTED_DUE_STALENESS",
    "BLOCKED_DATA_MISSING", "BLOCKED_RUNNER_ERROR",
}
# a valid paper-test package (not a blocker) is expected on the frozen owned data
PACKAGE_OK = {"CURRENT_ALPHA_READY_FOR_PAPER_TEST", "CURRENT_ALPHA_PACKAGE_READY_PANEL_ONLY",
              "CURRENT_ALPHA_NEEDS_FRESH_PRICES"}


def _read_csv(name):
    with open(OUT_DIR / name, "r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def result():
    # anchor staleness to the panel signal era so the decision is deterministic across wall-clock time
    proc = subprocess.run([sys.executable, str(RUNNER), "--as-of", "2026-06-26"], cwd=str(REPO),
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, f"runner failed: {proc.stdout}\n{proc.stderr}"
    with open(OUT_JSON, "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def docs_text():
    assert DOCS.exists()
    return DOCS.read_text(encoding="utf-8").lower()


def test_runner_compiles():
    py_compile.compile(str(RUNNER), doraise=True)


def test_output_json_exists(result):
    assert OUT_JSON.exists()
    assert result["phase"] == "13-A"


def test_decision_enum_valid(result):
    assert result["decision"] in ALLOWED
    assert result["decision"] in PACKAGE_OK        # frozen owned data yields a real package
    assert result["go_no_go"].startswith("GO_PAPER_ONLY")


def test_candidate_csvs_exist(result):
    for c in CSVS:
        assert (OUT_DIR / c).exists(), f"missing artifact: {c}"


def test_latest_signal_date_reported(result):
    assert result["signal_date"] == "2026-05-22"
    assert result["cross_section_month"] == "2026-05"
    assert result["n_ranked"] > 0


def test_top25_sorted_descending():
    rows = _read_csv("current_alpha_top25_candidates.csv")
    assert len(rows) == 25
    vals = [float(r["composite_sn"]) for r in rows]
    assert vals == sorted(vals, reverse=True), "top25 not sorted by descending composite_sn"
    assert [int(r["rank"]) for r in rows] == list(range(1, 26))


def test_top50_sorted_descending():
    rows = _read_csv("current_alpha_top50_candidates.csv")
    assert len(rows) == 50
    vals = [float(r["composite_sn"]) for r in rows]
    assert vals == sorted(vals, reverse=True), "top50 not sorted by descending composite_sn"


def test_bottom25_sorted_ascending():
    rows = _read_csv("current_alpha_bottom25_avoid_list.csv")
    assert len(rows) == 25
    vals = [float(r["composite_sn"]) for r in rows]
    assert vals == sorted(vals), "bottom/avoid list not sorted by ascending composite_sn"
    # the avoid list must be strictly worse than the top of the book
    top = _read_csv("current_alpha_top25_candidates.csv")
    assert vals[0] < float(top[0]["composite_sn"])


def test_paper_portfolios_are_equal_weight_long_only(result):
    for name, w, n in (("current_alpha_paper_portfolio_top25.csv", 0.04, 25),
                       ("current_alpha_paper_portfolio_top50.csv", 0.02, 50)):
        rows = _read_csv(name)
        assert len(rows) == n
        assert all(r["side"] == "LONG" for r in rows)
        assert all(abs(float(r["target_weight"]) - w) < 1e-9 for r in rows)
        assert all(r["order_action"] == "NO_ORDER" for r in rows)


def test_risk_limits_present():
    rows = _read_csv("current_alpha_risk_limits.csv")
    limits = {r["limit"] for r in rows}
    for required in ("PREVIEW ONLY", "NO ORDERS", "MANUAL REVIEW", "NO BROKER", "NO LIVE TRADING",
                     "no_averaging_down", "holding_horizon_trading_days", "rebalance_cadence"):
        assert required in limits, f"risk limit missing: {required}"


def test_stale_data_explicitly_reported(result):
    assert "days_since_signal" in result
    assert "stale_warning" in result
    assert "signal_is_stale" in result
    assert isinstance(result["stale_warning"], bool)
    # a scorecard row must explicitly carry a signal-freshness verdict
    sc = _read_csv("current_alpha_go_no_go_scorecard.csv")
    assert any(r["criterion"] == "signal_freshness" for r in sc)


def test_paper_test_rules_present(result):
    # tracking framework + rules are wired into the report and its artifacts
    assert result["holding_horizon_trading_days"] == 63
    assert result["rebalance_cadence"] == "QUARTERLY"
    assert result["weighting"] == "EQUAL_WEIGHT_LONG_ONLY"
    track = _read_csv("current_alpha_tracking_template.csv")
    assert track, "tracking template empty"
    for col in ("chk_1w_return", "chk_1m_return", "chk_2m_return", "chk_63d_return",
                "chk_63d_bench_rel", "max_drawdown", "hit_win_loss", "sector_attribution"):
        assert col in track[0], f"tracking template missing checkpoint column: {col}"


def test_benchmark_plan_present(result):
    b = result["expected_benchmark"]
    assert b["benchmark_signal"] == "composite_sn"
    assert b["ic_t_63d"] is not None
    # SPY is not owned locally -> plan must fall back and say so
    assert result["spy_benchmark_available_locally"] is False


def test_no_orders_broker_automation_deploy(result):
    for k in ("creates_orders", "creates_automation", "creates_broker_connection",
              "wrote_to_paper_trader", "live_trading", "deploy", "creates_paper_trader_signals",
              "creates_trade_decisions", "uses_paid_data", "uses_analyst_revision_data",
              "performs_network"):
        assert result[k] is False, f"safety flag {k} must be False"
    assert result["offline"] is True
    assert result["uses_owned_data_only"] is True


def test_no_paid_provider_calls_in_source():
    src = RUNNER.read_text(encoding="utf-8").lower()
    forbidden = ["requests.get", "requests.post", "import requests", "httpx", "aiohttp",
                 "urllib.request", "urllib.urlopen", "yfinance", "gcloud", "nasdaq", "intrinio",
                 "alphavantage", "finnhub", "polygon", "create_order", "place_order",
                 "submit_order", "os.system", "paper_trader.api.app", "api_server"]
    hits = [t for t in forbidden if t in src]
    assert not hits, f"forbidden tokens present in runner: {hits}"


def test_no_secrets_printed(result):
    assert result["api_key_printed"] is False
    assert result["api_key_written_to_disk"] is False
    assert result["secret_safety_leak_scan_clean"] is True
    blobs = [OUT_JSON.read_text(encoding="utf-8")]
    for c in CSVS:
        p = OUT_DIR / c
        if p.exists():
            blobs.append(p.read_text(encoding="utf-8"))
    joined = "\n".join(blobs)
    assert "api_key=" not in joined
    assert "apikey=" not in joined.lower()


def test_stale_reject_path(tmp_path):
    # a far-future package date must flip the decision to the staleness rejection
    out = tmp_path / "stale"
    proc = subprocess.run([sys.executable, str(RUNNER), "--as-of", "2027-12-31", "--quiet",
                           "--out", str(out)], cwd=str(REPO), capture_output=True, text=True,
                          timeout=180)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    rep = json.loads((out / f"{STEM}.json").read_text(encoding="utf-8"))
    assert rep["decision"] == "CURRENT_ALPHA_REJECTED_DUE_STALENESS"
    assert rep["stale_rejected"] is True


def test_missing_data_report_has_price_gaps(result):
    rows = _read_csv("current_alpha_missing_data_report.csv")
    issues = {r["issue"] for r in rows}
    assert "NO_LOCAL_PRICE" in issues
    assert "MISSING_COMPOSITE_LEG" in issues


def test_docs_mentions(docs_text):
    for token in ("preview only", "no orders", "manual review", "composite_sn", "paper test",
                  "63 trading", "quarterly", "staleness", "no live trading"):
        assert token in docs_text, f"docs missing token: {token}"
