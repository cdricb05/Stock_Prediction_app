"""Targeted tests for Phase 11-B0 - Local Existing Data Entitlement Probe.

Compile the runner, run it once fully offline (filesystem + env-name probe only), and assert on the
generated JSON / CSVs. Coverage numbers depend on the live local data, so the tests assert on STRUCTURE
and on invariants that must hold given the data actually present (insider MSPR is backtestable), not on
brittle exact counts.
"""
import csv
import json
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STEM = "phase11b0_local_data_entitlement_probe"
RUNNER = REPO / "research" / f"run_{STEM}.py"
DOCS = REPO / "docs" / f"{STEM}_v1.md"
OUT_DIR = REPO / "research" / "output" / STEM
OUT_JSON = OUT_DIR / f"{STEM}.json"
INVENTORY = OUT_DIR / "local_data_inventory.csv"
ENV_KEYS = OUT_DIR / "env_key_presence.csv"
READINESS = OUT_DIR / "family_readiness.csv"

ALLOWED_DECISIONS = {
    "LOCAL_DATA_READY_FOR_ALPHA_TEST", "EXISTING_KEYS_READY_FOR_DOWNLOAD",
    "LOCAL_SNAPSHOT_ONLY_NOT_BACKTESTABLE", "NO_LOCAL_OR_ENTITLED_DATA_FOUND",
}
SAFETY_KEYS = ["paper_only", "owned_local_data_only", "no_live_api_calls",
               "no_secret_values_read_or_emitted", "no_orders", "no_automation", "no_broker",
               "no_deploy", "no_gcp", "no_paper_trader_writes"]


@pytest.fixture(scope="module")
def result():
    proc = subprocess.run([sys.executable, str(RUNNER)], cwd=str(REPO),
                          capture_output=True, text=True, timeout=300)
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
    assert result.get("phase") == "11-B0"
    assert result.get("offline") is True
    assert result.get("performs_network") is False
    assert result.get("no_secret_values_emitted") is True


def test_decision_in_enum(result):
    assert result["decision"] in ALLOWED_DECISIONS


def test_local_data_ready(result):
    # given the insider MSPR data actually present, the honest probe outcome is LOCAL_DATA_READY
    assert result["decision"] == "LOCAL_DATA_READY_FOR_ALPHA_TEST", result.get("decision_rationale")
    assert "insider_sentiment_mspr" in result["backtestable_families"]


def test_insider_family_backtestable(result):
    fams = {f["family_key"]: f for f in result["local_family_inventory"]}
    ins = fams["insider_sentiment_mspr"]
    assert ins["classification"] == "BACKTESTABLE"
    assert ins["unique_tickers"] >= 100
    assert ins["median_obs_per_ticker"] >= 12
    assert ins["prior_status"] == "NEW_NEVER_TESTED"


def test_champion_family_is_paid_gated(result):
    # the Phase 11-A #1 family (analyst estimate revisions) must be flagged sparse/paid-gated locally
    st = result["phase_11a_champion_local_status"]
    assert st["family"] == "analyst_estimate_revisions"
    assert "PAID_GATED" in st["status"]


def test_env_key_presence_names_only(result):
    kp = result["env_key_presence"]
    # provider key NAMES must be reported; values are booleans, never secrets
    for name in ("EODHD_API_KEY", "FINNHUB_API_KEY", "POLYGON_API_KEY", "FMP_API_KEY"):
        assert name in kp
        assert isinstance(kp[name], bool)


def test_all_families_classified(result):
    valid = {"BACKTESTABLE", "BACKTESTABLE_NARROW", "SHALLOW_SNAPSHOT", "TOO_SPARSE",
             "SNAPSHOT_ONLY", "MISSING"}
    for f in result["local_family_inventory"]:
        assert f["classification"] in valid


def test_short_interest_deprioritized(result):
    # short interest family was rejected in 10-A; it must not be listed as ready even if broad
    fams = {f["family_key"]: f for f in result["local_family_inventory"]}
    si = fams["short_interest_days_to_cover"]
    assert si["prior_status"] == "FAMILY_REJECTED_10A"
    assert "short_interest_days_to_cover" not in result["backtestable_families"]


def test_artifacts_exist(result):
    for p in (OUT_JSON, INVENTORY, ENV_KEYS, READINESS):
        assert p.exists(), f"missing artifact: {p.name}"


def test_inventory_csv_content():
    with open(INVENTORY, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    keys = {r["family_key"] for r in rows}
    assert "insider_sentiment_mspr" in keys
    assert "analyst_estimate_revision_av" in keys


def test_readiness_csv_content():
    with open(READINESS, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    ready = {r["family_key"] for r in rows if r["ready_for_alpha_test"] == "True"}
    assert "insider_sentiment_mspr" in ready


def test_safety_flags_true(result):
    s = result["safety"]
    for key in SAFETY_KEYS:
        assert s.get(key) is True, f"safety flag not True: {key}"


def test_docs_mentions(docs_text):
    for token in ("no api calls", "no orders", "no automation", "backtestable", "insider",
                  "paid-gated", "no secret"):
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
