"""Targeted tests for Phase 11-B2 - Free / Currently-Entitled Data Manifest (offline)."""
import csv
import json
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STEM = "phase11b2_entitled_download_manifest"
RUNNER = REPO / "research" / f"run_{STEM}.py"
DOCS = REPO / "docs" / f"{STEM}_v1.md"
OUT_DIR = REPO / "research" / "output" / STEM
OUT_JSON = OUT_DIR / f"{STEM}.json"
MANIFEST = OUT_DIR / "data_manifest.csv"
COVERAGE = OUT_DIR / "coverage_report.csv"
CEILINGS = OUT_DIR / "free_tier_ceilings.csv"

ALLOWED = {"FREE_DATA_LOADED", "PARTIAL_FREE_DATA_LOADED", "NO_FREE_DATA_LOADABLE", "DOWNLOAD_BLOCKED"}


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
    assert result["phase"] == "11-B2"
    assert result["offline"] is True
    assert result["performs_network"] is False
    assert result["redownloaded"] is False


def test_decision(result):
    assert result["decision"] in ALLOWED
    assert result["decision"] == "PARTIAL_FREE_DATA_LOADED"


def test_manifest_lists_insider_and_short(result):
    fams = {m["family"] for m in result["manifest"]}
    assert "insider_sentiment_mspr" in fams
    assert "short_interest_days_to_cover" in fams


def test_insider_actually_present(result):
    m = {x["family"]: x for x in result["manifest"]}["insider_sentiment_mspr"]
    assert m["raw_files"] > 0 and m["normalized_present"] is True
    assert m["unique_tickers"] and int(m["unique_tickers"]) >= 100


def test_free_tier_ceilings_documented(result):
    ceilings = result["free_tier_ceilings"]
    fams = {c["family"] for c in ceilings}
    assert "analyst_estimate_revision" in fams
    av = [c for c in ceilings if c["provider"] == "AlphaVantage"][0]
    assert int(av["names_collected"]) < int(av["universe_needed"])  # free cap => cannot reach universe


def test_artifacts(result):
    for p in (OUT_JSON, MANIFEST, COVERAGE, CEILINGS):
        assert p.exists(), f"missing artifact: {p.name}"


def test_manifest_csv_content():
    with open(MANIFEST, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert any(r["family"] == "insider_sentiment_mspr" and int(r["raw_files"]) > 0 for r in rows)


def test_safety(result):
    for k in ("paper_only", "no_live_api_calls", "no_redownload", "no_orders", "no_automation"):
        assert result["safety"][k] is True


def test_docs_mentions(docs_text):
    for token in ("no api calls", "no orders", "no automation", "paid", "free-tier"):
        assert token in docs_text, f"docs missing token: {token}"


def test_forbidden_token_scan():
    src = RUNNER.read_text(encoding="utf-8").lower()
    forbidden = ["requests.get", "requests.post", "import requests", "urllib", "httpx", "aiohttp",
                 "yfinance", "gcloud", "ssh", "create_order", "place_order", "submit_order",
                 "broker execution", "api_server", "paper_trader.api.app"]
    hits = [t for t in forbidden if t in src]
    assert not hits, f"forbidden tokens present: {hits}"
