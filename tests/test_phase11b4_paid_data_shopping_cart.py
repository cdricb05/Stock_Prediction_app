"""Targeted tests for Phase 11-B4 - Paid-Data Acquisition Shopping Cart (offline design)."""
import csv
import json
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STEM = "phase11b4_paid_data_shopping_cart"
RUNNER = REPO / "research" / f"run_{STEM}.py"
DOCS = REPO / "docs" / f"{STEM}_v1.md"
OUT_DIR = REPO / "research" / "output" / STEM
OUT_JSON = OUT_DIR / f"{STEM}.json"
CART_CSV = OUT_DIR / "shopping_cart.csv"
FIELDS_CSV = OUT_DIR / "required_fields.csv"
REJECT_CSV = OUT_DIR / "rejection_criteria.csv"

ALLOWED = {"ACTION_REQUIRED_ANALYST_REVISIONS_TRIAL", "ACTION_REQUIRED_SHORT_INTEREST_TRIAL",
           "ACTION_REQUIRED_OPTIONS_TRIAL", "ACTION_REQUIRED_MULTI_VENDOR_QUOTES",
           "NO_PAID_DATA_RECOMMENDED"}


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
    assert result["phase"] == "11-B4"
    assert result["offline"] is True
    assert result["performs_network"] is False
    assert result["no_payment_submitted"] is True
    assert result["no_signup_performed"] is True


def test_decision(result):
    assert result["decision"] in ALLOWED
    assert result["decision"] == "ACTION_REQUIRED_ANALYST_REVISIONS_TRIAL"
    assert result["recommended_family"] == "analyst_estimate_revisions"


def test_cart_ranked_with_must_try_first(result):
    ranks = {c["rank"] for c in result["cart"]}
    assert 1 in ranks
    r1 = [c for c in result["cart"] if c["rank"] == 1]
    assert all(c["family"] == "analyst_estimate_revisions" for c in r1)
    assert any(c["free_trial"] for c in result["cart"])


def test_required_fields_include_pit(result):
    f = result["required_fields"]
    assert "pit_effective_date" in f
    assert "up_revisions_count" in f and "down_revisions_count" in f
    assert "estimate_change_30d" in f


def test_rejection_criteria_include_subperiod_guard(result):
    ids = " ".join(r["criterion_id"] + " " + r["rule"] for r in result["rejection_criteria"]).lower()
    assert "subperiod" in ids and "oos" in ids


def test_baseline_to_beat_recorded(result):
    b = result["baseline_to_beat"]
    assert abs(float(b["quarterly_net_25bps"]) - 0.00401) < 1e-6
    assert b["signal"] == "composite_sn"


def test_artifacts(result):
    for p in (OUT_JSON, CART_CSV, FIELDS_CSV, REJECT_CSV):
        assert p.exists(), f"missing artifact: {p.name}"


def test_cart_csv_content():
    with open(CART_CSV, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert any(r["rank"] == "1" for r in rows)
    assert any("FMP" in r["provider"] for r in rows)
    assert any("Zacks" in r["provider"] for r in rows)


def test_fields_csv_content():
    with open(FIELDS_CSV, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    keys = {r["field_key"] for r in rows}
    assert "pit_effective_date" in keys and len(keys) >= 12


def test_safety(result):
    for k in ("paper_only", "no_live_api_calls", "no_payment_submitted", "no_signup_performed",
              "no_orders", "no_automation"):
        assert result["safety"][k] is True


def test_docs_mentions(docs_text):
    for token in ("no api calls", "no orders", "no automation", "analyst estimate revisions",
                  "user opt-in", "must-try"):
        assert token in docs_text, f"docs missing token: {token}"


def test_forbidden_token_scan():
    src = RUNNER.read_text(encoding="utf-8").lower()
    forbidden = ["requests.get", "requests.post", "import requests", "urllib", "httpx", "aiohttp",
                 "yfinance", "gcloud", "ssh", "create_order", "place_order", "submit_order",
                 "broker execution", "api_server", "paper_trader.api.app"]
    hits = [t for t in forbidden if t in src]
    assert not hits, f"forbidden tokens present: {hits}"
