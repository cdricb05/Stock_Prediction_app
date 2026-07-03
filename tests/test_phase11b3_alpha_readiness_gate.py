"""Targeted tests for Phase 11-B3 - Alpha-Readiness Gate (offline synthesis)."""
import csv
import json
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STEM = "phase11b3_alpha_readiness_gate"
RUNNER = REPO / "research" / f"run_{STEM}.py"
DOCS = REPO / "docs" / f"{STEM}_v1.md"
OUT_DIR = REPO / "research" / "output" / STEM
OUT_JSON = OUT_DIR / f"{STEM}.json"
GATE_CSV = OUT_DIR / "readiness_gate.csv"

ALLOWED = {"NEW_DATA_READY_FOR_ALPHA_TEST", "NEW_DATA_PARTIAL_NEEDS_REPAIR",
           "NEW_DATA_NOT_BACKTESTABLE", "NEEDS_PAID_DATA"}


@pytest.fixture(scope="module")
def result():
    proc = subprocess.run([sys.executable, str(RUNNER)], cwd=str(REPO),
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, f"runner failed: {proc.stdout}\n{proc.stderr}"
    with open(OUT_JSON, "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def docs_text():
    assert DOCS.exists()
    return DOCS.read_text(encoding="utf-8").lower()


def test_runner_compiles():
    py_compile.compile(str(RUNNER), doraise=True)


def test_offline(result):
    assert result["phase"] == "11-B3"
    assert result["offline"] is True
    assert result["performs_network"] is False


def test_decision(result):
    assert result["decision"] in ALLOWED
    # given 11-C already ran with NO alpha and analyst revisions are paid-gated -> NEEDS_PAID_DATA
    assert result["decision"] == "NEEDS_PAID_DATA", result["decision_rationale"]


def test_reads_prior_phases(result):
    assert result["inputs_read"]["phase11b0"] is True
    assert result["inputs_read"]["phase11c"] is True
    assert result["inputs_read"]["phase11c_decision"] == "NEW_DATA_NO_ALPHA"


def test_insider_tested_no_alpha(result):
    rows = {r["family_key"]: r for r in result["gate_rows"]}
    assert rows["insider_sentiment_mspr"]["gate_status"] == "READY_TESTED_NO_ALPHA"
    assert "insider_sentiment_mspr" in result["tested_no_alpha"]


def test_analyst_paid_gated(result):
    assert any("analyst" in k for k in result["paid_gated"])


def test_baseline_remains_champion(result):
    assert result["baseline_remains_champion"] is True


def test_artifacts(result):
    for p in (OUT_JSON, GATE_CSV):
        assert p.exists(), f"missing artifact: {p.name}"


def test_gate_csv_content():
    with open(GATE_CSV, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    keys = {r["family_key"] for r in rows}
    assert "insider_sentiment_mspr" in keys


def test_safety(result):
    for k in ("paper_only", "no_live_api_calls", "no_orders", "no_automation"):
        assert result["safety"][k] is True


def test_docs_mentions(docs_text):
    for token in ("no api calls", "no orders", "no automation", "paid", "composite_sn"):
        assert token in docs_text, f"docs missing token: {token}"


def test_forbidden_token_scan():
    src = RUNNER.read_text(encoding="utf-8").lower()
    forbidden = ["requests.get", "requests.post", "import requests", "urllib", "httpx", "aiohttp",
                 "yfinance", "gcloud", "ssh", "create_order", "place_order", "submit_order",
                 "broker execution", "api_server", "paper_trader.api.app"]
    hits = [t for t in forbidden if t in src]
    assert not hits, f"forbidden tokens present: {hits}"
