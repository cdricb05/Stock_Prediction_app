"""Targeted tests for Phase 11-B1 - Provider And Data-Family Discovery Map (offline, no probing)."""
import csv
import json
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STEM = "phase11b1_provider_data_family_discovery_map"
RUNNER = REPO / "research" / f"run_{STEM}.py"
DOCS = REPO / "docs" / f"{STEM}_v1.md"
OUT_DIR = REPO / "research" / "output" / STEM
OUT_JSON = OUT_DIR / f"{STEM}.json"
MAP_CSV = OUT_DIR / "provider_data_family_map.csv"
FAM_CSV = OUT_DIR / "family_summary.csv"

ALLOWED = {"PROVIDER_MAP_READY", "PROVIDER_DISCOVERY_PARTIAL", "PROVIDER_DISCOVERY_BLOCKED"}


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
    assert result["phase"] == "11-B1"
    assert result["offline"] is True
    assert result["performs_network"] is False
    assert result["no_provider_probing"] is True


def test_decision(result):
    assert result["decision"] in ALLOWED
    assert result["decision"] == "PROVIDER_MAP_READY"


def test_all_five_families(result):
    for fam in ("A_analyst_estimates_revisions", "B_short_interest_securities_lending",
                "C_options_implied_vol", "D_insider_ownership", "E_news_sentiment"):
        assert fam in result["family_provider_counts"]
        assert result["family_provider_counts"][fam] >= 2


def test_providers_not_probed(result):
    assert result["providers"]
    for p in result["providers"]:
        assert p["no_probe_performed"] is True
        assert "access_status" in p and p["access_status"]


def test_analyst_family_top_priority(result):
    a = [p for p in result["providers"] if p["family"] == "A_analyst_estimates_revisions"]
    assert any(p["alpha_priority"] == 1 for p in a), "no top-priority analyst-revision provider"


def test_entitlement_overlay(result):
    # providers with an owned key must be flagged entitled (e.g., FMP / Finnhub / Polygon present)
    ov = result["entitled_keys_overlay"]
    assert isinstance(ov, dict) and any(v is True for v in ov.values())


def test_free_public_sources_present(result):
    # FINRA / SEC EDGAR must be catalogued as free/public (no payment) options
    provs = {p["provider"] for p in result["providers"]}
    assert "FINRA" in provs and "SEC EDGAR" in provs


def test_artifacts(result):
    for p in (OUT_JSON, MAP_CSV, FAM_CSV):
        assert p.exists(), f"missing artifact: {p.name}"


def test_map_csv_content():
    with open(MAP_CSV, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) >= 15
    assert all("access_status" in r for r in rows)


def test_safety(result):
    for k in ("paper_only", "no_live_api_calls", "no_provider_probing", "no_orders", "no_automation"):
        assert result["safety"][k] is True


def test_docs_mentions(docs_text):
    for token in ("no api calls", "no provider probing", "no orders", "no automation", "analyst"):
        assert token in docs_text, f"docs missing token: {token}"


def test_forbidden_token_scan():
    src = RUNNER.read_text(encoding="utf-8").lower()
    forbidden = ["requests.get", "requests.post", "import requests", "urllib", "httpx", "aiohttp",
                 "yfinance", "gcloud", "ssh", "create_order", "place_order", "submit_order",
                 "broker execution", "api_server", "paper_trader.api.app"]
    hits = [t for t in forbidden if t in src]
    assert not hits, f"forbidden tokens present: {hits}"
