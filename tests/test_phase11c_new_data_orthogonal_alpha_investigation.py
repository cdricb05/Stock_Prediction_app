"""Targeted tests for Phase 11-C - New-Data Orthogonal Alpha Investigation.

Compile the runner, run it once fully offline (reads only the owned frozen panel + already-downloaded
normalized signals - no network, no key), and assert on the generated JSON / CSVs. The decisive
integrity check is that the panel reproduces the frozen 10-D baseline (net-25bps ~ +0.00401), so the
new-signal comparison is trustworthy.
"""
import csv
import json
import math
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STEM = "phase11c_new_data_orthogonal_alpha_investigation"
RUNNER = REPO / "research" / f"run_{STEM}.py"
DOCS = REPO / "docs" / f"{STEM}_v1.md"
OUT_DIR = REPO / "research" / "output" / STEM
OUT_JSON = OUT_DIR / f"{STEM}.json"
SCORECARD = OUT_DIR / "signal_scorecard.csv"
COVERAGE = OUT_DIR / "pit_join_coverage.csv"
BLENDS = OUT_DIR / "incremental_blend_results.csv"
BVC = OUT_DIR / "baseline_vs_champion.csv"

ALLOWED_DECISIONS = {
    "NEW_ALPHA_FOUND_READY_FOR_PAPER_RULES", "NEW_DATA_NO_ALPHA",
    "NEW_DATA_NEEDS_MORE_HISTORY", "NEW_DATA_TEST_BLOCKED",
}
SAFETY_KEYS = ["paper_only", "owned_local_data_only", "no_live_api_calls", "no_orders",
               "no_automation", "no_broker", "no_deploy", "no_gcp", "no_paper_trader_writes",
               "no_payment_submitted"]


@pytest.fixture(scope="module")
def result():
    proc = subprocess.run([sys.executable, str(RUNNER)], cwd=str(REPO),
                          capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, f"runner failed: {proc.stdout}\n{proc.stderr}"
    assert OUT_JSON.exists()
    with open(OUT_JSON, "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def docs_text():
    assert DOCS.exists(), "docs file missing"
    return DOCS.read_text(encoding="utf-8").lower()


def test_runner_compiles():
    py_compile.compile(str(RUNNER), doraise=True)


def test_runner_runs_offline(result):
    assert result.get("phase") == "11-C"
    assert result.get("offline") is True
    assert result.get("performs_network") is False
    assert result.get("eodhd_key_required") is False


def test_decision_in_enum(result):
    assert result["decision"] in ALLOWED_DECISIONS


def test_baseline_reproduces_10d(result):
    # panel-integrity guard: the frozen panel must reproduce the 10-D baseline, else the comparison is
    # untrustworthy. net-25bps ~ +0.00401, IC t ~ 2.665.
    b = result["baseline"]
    assert abs(float(b["quarterly_net_25bps"]) - 0.00401) < 0.0015, b
    assert abs(float(b["ic_t"]) - 2.665) < 0.25, b


def test_insider_family_attempted(result):
    assert "insider_sentiment_mspr" in result["data_families_attempted"]
    assert "finnhub" in result["providers_attempted"]


def test_standalone_and_blends_present(result):
    assert len(result["standalone_results"]) >= 2
    assert len(result["incremental_blend_results"]) >= 3
    # every blend carries the subperiod-net25 improvement guard fields
    for b in result["incremental_blend_results"]:
        assert "improvement_survives_subperiods" in b
        assert "blend_pre2020_net25" in b and "blend_post2020_net25" in b


def test_champion_consistency(result):
    # champion is set iff a new-alpha decision was reached
    if result["decision"] == "NEW_ALPHA_FOUND_READY_FOR_PAPER_RULES":
        assert result["champion"] is not None
        assert result["champion"]["improvement_survives_subperiods"] is True
        assert result["champion"]["classification"] == "PASS_STRICT"
    else:
        assert result["champion"] is None


def test_no_blend_passes_without_subperiod_guard(result):
    # any variant classified PASS_STRICT must also survive the subperiod guard to be a champion
    for b in result["incremental_blend_results"]:
        if b.get("classification") == "PASS_STRICT" and not b.get("improvement_survives_subperiods"):
            assert result["champion"] is None or result["champion"]["variant_id"] != b["variant_id"]


def test_horizon_limitation_recorded(result):
    hz = result.get("horizon_limitation", "").lower()
    assert "63d" in hz and ("5d" in hz or "21d" in hz)


def test_artifacts_exist(result):
    for p in (OUT_JSON, SCORECARD, COVERAGE, BLENDS, BVC):
        assert p.exists(), f"missing artifact: {p.name}"


def test_scorecard_csv_content():
    with open(SCORECARD, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    ids = {r["variant_id"] for r in rows}
    assert any("insider" in i for i in ids)


def test_coverage_csv_content():
    with open(COVERAGE, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert any(int(r["unique_tickers"]) >= 100 for r in rows), "no signal with broad coverage joined"


def test_safety_flags_true(result):
    s = result["safety"]
    for key in SAFETY_KEYS:
        assert s.get(key) is True, f"safety flag not True: {key}"


def test_docs_mentions(docs_text):
    for token in ("no api calls", "no orders", "no automation", "composite_sn", "insider",
                  "subperiod", "63d"):
        assert token in docs_text, f"docs missing token: {token}"


def test_forbidden_token_scan():
    src = RUNNER.read_text(encoding="utf-8").lower()
    forbidden = [
        "requests.get", "requests.post", "import requests", "urllib", "httpx", "aiohttp",
        "yfinance", "gcloud", "ssh", "create_order", "place_order", "submit_order",
        "broker execution", "api_server", "paper_trader.api.app",
    ]
    hits = [tok for tok in forbidden if tok in src]
    assert not hits, f"forbidden tokens present in runner source: {hits}"
