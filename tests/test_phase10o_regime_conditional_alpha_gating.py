"""Targeted tests for Phase 10-O - Regime And Conditional Alpha Gating.

Targeted (not a full regression): compile the runner, run it once offline, and assert on the generated
JSON / CSVs / docs. No network, no key, no Paper Trader imports.
"""

import csv
import json
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STEM = "phase10o_regime_conditional_alpha_gating"
RUNNER = REPO / "research" / f"run_{STEM}.py"
DOCS = REPO / "docs" / f"{STEM}_v1.md"
OUT_DIR = REPO / "research" / "output" / STEM
OUT_JSON = OUT_DIR / f"{STEM}.json"
INVENTORY = OUT_DIR / "regime_inventory.csv"
SCORECARD = OUT_DIR / "regime_conditional_scorecard.csv"
STATE_DETAIL = OUT_DIR / "regime_state_detail.csv"
SUBPERIOD = OUT_DIR / "regime_subperiod_report.csv"
REJECTED = OUT_DIR / "rejected_regimes.csv"

ALLOWED_DECISIONS = {
    "CONDITIONAL_ALPHA_READY_FOR_PAPER_RULES",
    "BASELINE_REMAINS_CHAMPION",
    "REJECT_REGIME_OVERFIT",
    "NEEDS_REGIME_INPUT_REPAIR",
    "NEEDS_MORE_OWNED_DATA",
}

SAFETY_KEYS = ["paper_only", "owned_local_data_only", "no_live_api_calls", "no_orders",
               "no_automation", "no_broker", "no_deploy"]

REQUIRED_REGIMES = ["easy_regime", "high_rates", "market_drawdown", "high_oil", "strong_dollar",
                    "rates_10y_level", "curve_2s10s", "oil_momentum", "market_vol",
                    "return_dispersion", "market_liquidity"]


@pytest.fixture(scope="module")
def result():
    proc = subprocess.run(
        [sys.executable, str(RUNNER)],
        cwd=str(REPO), capture_output=True, text=True, timeout=900,
    )
    assert proc.returncode == 0, f"runner failed: {proc.stdout}\n{proc.stderr}"
    assert OUT_JSON.exists(), "runner did not produce the output JSON"
    with open(OUT_JSON, "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def docs_text():
    assert DOCS.exists(), "docs file missing"
    return DOCS.read_text(encoding="utf-8").lower()


@pytest.fixture(scope="module")
def scorecard_regimes():
    assert SCORECARD.exists(), "scorecard missing"
    with open(SCORECARD, "r", encoding="utf-8", newline="") as fh:
        return [row["regime"] for row in csv.DictReader(fh)]


def test_runner_compiles():
    py_compile.compile(str(RUNNER), doraise=True)


def test_output_json_generated(result):
    assert isinstance(result, dict) and result.get("phase") == "10-O"
    assert result.get("phase_name")


def test_decision_in_enum(result):
    assert result["decision"] in ALLOWED_DECISIONS


def test_panel_reproduces_10d(result):
    rep = result["phase10d_baseline_reproduction"]
    assert rep["reproduces_within_tolerance"] is True, rep
    assert result["decision"] != "NEEDS_REGIME_INPUT_REPAIR", result.get("decision_rationale")


def test_baseline_present(result):
    b = result.get("baseline")
    assert isinstance(b, dict)
    assert b.get("ic_t_63d") is not None
    assert b.get("quarterly_net_25bps") is not None


def test_all_regimes_present(result, scorecard_regimes):
    assert result["n_regimes"] == len(REQUIRED_REGIMES)
    tested = {v["regime"] for v in result["variants_tested"]}
    for r in REQUIRED_REGIMES:
        assert r in tested, f"regime missing from JSON: {r}"
        assert r in scorecard_regimes, f"regime missing from scorecard: {r}"


def test_sample_bars_present(result):
    sb = result["sample_bars"]
    assert sb["min_regime_quarters"] >= 1
    assert sb["min_regime_events"] >= 1
    assert sb["meaningful_multiple"] >= 1.0


def test_subperiod_report_present():
    assert SUBPERIOD.exists()
    with open(SUBPERIOD, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows
    assert "pre2020_net25" in rows[0] and "post2020_net25" in rows[0]


def test_costs_present(result):
    for v in result["variants_tested"]:
        assert "favourable_net_25bps" in v
    assert result["baseline"]["quarterly_net_25bps"] is not None
    assert result["baseline"]["quarterly_net_50bps"] is not None


def test_rejected_regimes_present(result):
    assert REJECTED.exists()
    assert isinstance(result.get("rejected_candidates"), list)


def test_all_csv_artifacts_exist():
    for p in (INVENTORY, SCORECARD, STATE_DETAIL, SUBPERIOD, REJECTED):
        assert p.exists(), f"missing artifact: {p.name}"


def test_champion_present(result):
    champ = result.get("champion")
    assert isinstance(champ, dict) and champ.get("champion")
    assert "baseline_remains_champion" in champ


def test_required_report_blocks(result):
    for key in ("input_inventory", "baseline_vs_champion", "oos_stability_summary",
                "cohort_stability_summary", "sector_concentration_summary", "turnover_cost_summary",
                "implementation_limits", "next_recommended_phase"):
        assert key in result, f"missing report block: {key}"


def test_safety_flags_true(result):
    s = result["safety"]
    for key in SAFETY_KEYS:
        assert s.get(key) is True, f"safety flag not True: {key}"


def test_docs_mention_owned_local_data(docs_text):
    assert "owned/local data only" in docs_text or "owned / local" in docs_text


def test_docs_mention_modest_boundary(docs_text):
    assert "modest" in docs_text and "boundary" in docs_text


def test_docs_mention_no_live_api_calls(docs_text):
    assert "no live api calls" in docs_text


def test_docs_mention_no_orders_or_automation(docs_text):
    assert "no orders" in docs_text and "no automation" in docs_text


def test_forbidden_token_scan():
    src = RUNNER.read_text(encoding="utf-8").lower()
    forbidden = [
        "requests.get", "requests.post", "import requests", "urllib", "httpx", "aiohttp",
        "yfinance", "gcloud", "ssh",
        "create_order", "place_order", "submit_order", "broker execution",
        "api_server", "paper_trader.api.app",
    ]
    hits = [tok for tok in forbidden if tok in src]
    assert not hits, f"forbidden tokens present in runner source: {hits}"
