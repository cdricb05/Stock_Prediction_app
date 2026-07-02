"""Targeted tests for Phase 10-Q - Owned Data Exhaustion And Research Decision.

Targeted (not a full regression): compile the runner, run it once offline (synthesis of prior-phase
JSONs), and assert on the generated JSON / CSVs / docs. No network, no key, no Paper Trader imports.
"""

import csv
import json
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STEM = "phase10q_owned_data_exhaustion_research_decision"
RUNNER = REPO / "research" / f"run_{STEM}.py"
DOCS = REPO / "docs" / f"{STEM}_v1.md"
OUT_DIR = REPO / "research" / "output" / STEM
OUT_JSON = OUT_DIR / f"{STEM}.json"
LEDGER = OUT_DIR / "research_avenue_ledger.csv"
BASELINE_STATUS = OUT_DIR / "baseline_status.csv"
EXHAUSTION = OUT_DIR / "owned_data_exhaustion.csv"
NEW_DATA = OUT_DIR / "new_data_requirements.csv"

ALLOWED_DECISIONS = {
    "PACKAGE_MODEST_BASELINE_FOR_PAPER_REVIEW",
    "PAUSE_ALPHA_RESEARCH_NEEDS_NEW_DATA",
    "NEEDS_DATA_REFRESH_BEFORE_DECISION",
}

SAFETY_KEYS = ["paper_only", "owned_local_data_only", "no_live_api_calls", "no_orders",
               "no_automation", "no_broker", "no_deploy"]

REQUIRED_AVENUE_PHASES = ["10-L-B", "10-M", "10-N", "10-O"]


@pytest.fixture(scope="module")
def result():
    proc = subprocess.run(
        [sys.executable, str(RUNNER)],
        cwd=str(REPO), capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, f"runner failed: {proc.stdout}\n{proc.stderr}"
    assert OUT_JSON.exists(), "runner did not produce the output JSON"
    with open(OUT_JSON, "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def docs_text():
    assert DOCS.exists(), "docs file missing"
    return DOCS.read_text(encoding="utf-8").lower()


def test_runner_compiles():
    py_compile.compile(str(RUNNER), doraise=True)


def test_output_json_generated(result):
    assert isinstance(result, dict) and result.get("phase") == "10-Q"
    assert result.get("phase_name")


def test_decision_in_enum(result):
    assert result["decision"] in ALLOWED_DECISIONS


def test_decision_is_package_baseline(result):
    # given 10-L-B/10-M/10-N/10-O all failed, the honest decision is to package the modest baseline
    assert result["decision"] == "PACKAGE_MODEST_BASELINE_FOR_PAPER_REVIEW", result.get(
        "decision_rationale")


def test_all_avenues_present(result):
    phases = {a["phase"] for a in result["candidates_tested"]}
    for p in REQUIRED_AVENUE_PHASES:
        assert p in phases, f"avenue missing: {p}"


def test_no_stronger_alpha_found(result):
    champ = result["champion"]
    assert champ["baseline_remains_champion"] is True
    assert champ.get("no_stronger_owned_alpha_found") is True
    # none of the avenues found a stronger winner
    assert all(not a["found_stronger_alpha"] for a in result["candidates_tested"])


def test_baseline_metrics_present(result):
    b = result["baseline"]
    assert b.get("ic_t_63d") is not None
    assert b.get("quarterly_net_25bps") is not None
    assert "modest" in (b.get("alpha_character") or "").lower()


def test_owned_data_and_new_data_blocks(result):
    assert isinstance(result.get("owned_data_exhaustion"), list) and result["owned_data_exhaustion"]
    assert isinstance(result.get("new_data_requirements"), list) and result["new_data_requirements"]
    # analyst estimate-revisions must be named as the top new-data family
    joined = " ".join(x["family"].lower() for x in result["new_data_requirements"])
    assert "revision" in joined


def test_required_report_blocks(result):
    for key in ("input_inventory", "baseline_vs_champion", "oos_stability_summary",
                "cohort_stability_summary", "sector_concentration_summary", "turnover_cost_summary",
                "implementation_limits", "recommended_next_actions", "next_recommended_phase"):
        assert key in result, f"missing report block: {key}"


def test_all_csv_artifacts_exist():
    for p in (LEDGER, BASELINE_STATUS, EXHAUSTION, NEW_DATA):
        assert p.exists(), f"missing artifact: {p.name}"


def test_ledger_has_four_avenues():
    with open(LEDGER, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    phases = {r["phase"] for r in rows}
    for p in REQUIRED_AVENUE_PHASES:
        assert p in phases


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
