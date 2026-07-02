"""Targeted tests for Phase 10-L-A - Historical Sector-Neutral Scored Panel Reconstruction.

Targeted (not a full regression): compile the runner, run it once offline, and assert on the generated
JSON / panel CSV / docs. No network, no key, no Paper Trader imports.
"""

import csv
import json
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STEM = "phase10l_historical_sector_neutral_scored_panel_reconstruction"
RUNNER = REPO / "research" / f"run_{STEM}.py"
DOCS = REPO / "docs" / f"{STEM}_v1.md"
OUT_DIR = REPO / "research" / "output" / STEM
OUT_JSON = OUT_DIR / f"{STEM}.json"
PANEL_CSV = OUT_DIR / "historical_sector_neutral_scored_panel.csv"
SCHEMA_CSV = OUT_DIR / "panel_schema.csv"
REPRO_CSV = OUT_DIR / "phase10d_reproduction_check.csv"
MISSING_CSV = OUT_DIR / "missing_fields_report.csv"
COVERAGE_CSV = OUT_DIR / "panel_coverage_summary.csv"
QUALITY_CSV = OUT_DIR / "data_quality_report.csv"

REQUIRED_TOP_LEVEL_KEYS = [
    "phase", "phase_name", "decision", "decision_rationale", "input_inventory",
    "reconstructed_panel", "panel_schema", "panel_coverage_summary", "phase10d_frozen_baseline",
    "phase10d_reproduction_check", "missing_fields_report", "data_quality_report",
    "implementation_limits", "next_recommended_phase", "safety",
]

ALLOWED_DECISIONS = {
    "PANEL_RECONSTRUCTION_READY",
    "PANEL_RECONSTRUCTION_PARTIAL",
    "PANEL_RECONSTRUCTION_FAILED",
    "NEEDS_PHASE_INPUT_REPAIR",
}

SAFETY_KEYS = ["paper_only", "owned_local_data_only", "no_live_api_calls", "no_orders",
               "no_automation", "no_broker", "no_deploy"]

REQUIRED_PANEL_COLUMNS = [
    "as_of_date", "rebalance_date", "ticker", "sector", "cohort", "is_new_cohort", "liquidity_proxy",
    "fcf_to_assets", "operating_accruals", "fcf_to_assets_raw", "operating_accruals_raw",
    "fcf_to_assets_sector_neutral_z", "operating_accruals_sector_neutral_z",
    "operating_accruals_oriented_sector_neutral_z", "composite_sn", "composite_raw",
    "forward_63d_return", "forward_63d_return_start_date", "forward_63d_return_end_date",
    "has_forward_return", "source_phase", "data_quality_flag",
]


@pytest.fixture(scope="module")
def result():
    """Run the runner offline once (req #2) and return the parsed JSON (req #3)."""
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


# --- 1: runner compiles ----------------------------------------------------

def test_runner_compiles():
    py_compile.compile(str(RUNNER), doraise=True)


# --- 3/4: JSON generated, keys present -------------------------------------

def test_output_json_generated(result):
    assert isinstance(result, dict) and result.get("phase") == "10-L-A"


def test_required_top_level_keys(result):
    for key in REQUIRED_TOP_LEVEL_KEYS:
        assert key in result, f"missing top-level key: {key}"


# --- 5: decision in enum (and, for this run, READY) ------------------------

def test_decision_in_enum(result):
    assert result["decision"] in ALLOWED_DECISIONS


def test_decision_is_ready(result):
    assert result["decision"] == "PANEL_RECONSTRUCTION_READY", result.get("decision_rationale")


# --- 6/7/8/9: required CSV artifacts exist ---------------------------------

def test_panel_csv_exists():
    assert PANEL_CSV.exists()


def test_schema_csv_exists():
    assert SCHEMA_CSV.exists()


def test_reproduction_csv_exists():
    assert REPRO_CSV.exists()


def test_missing_fields_csv_exists():
    assert MISSING_CSV.exists()


def test_coverage_and_quality_csv_exist():
    assert COVERAGE_CSV.exists() and QUALITY_CSV.exists()


# --- panel content: all required columns + non-trivial row count -----------

def test_panel_has_required_columns_and_rows():
    with open(PANEL_CSV, "r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        n_rows = sum(1 for _ in reader)
    assert header == REQUIRED_PANEL_COLUMNS, f"panel header mismatch: {header}"
    assert n_rows > 10000, f"panel unexpectedly small: {n_rows} rows"


# --- reproduction check: 4/4 gates + additive self-check -------------------

def test_reproduction_gates_all_pass(result):
    summary = result["phase10d_reproduction_check"]["summary"]
    assert summary["gates_total"] == 4
    assert summary["gates_passed"] == 4
    assert summary["all_gates_pass"] is True
    assert summary["direction_modest_boundary_positive_net25"] is True


def test_additive_self_check_ok(result):
    sc = result["reconstructed_panel"]["additive_self_check"]
    assert sc["additive_reconstruction_ok"] is True


# --- 10: safety flags true -------------------------------------------------

def test_safety_flags_true(result):
    s = result["safety"]
    for key in SAFETY_KEYS:
        assert s.get(key) is True, f"safety flag not True: {key}"


# --- 11-15: doc content ----------------------------------------------------

def test_docs_mention_10k_proved_panel_missing(docs_text):
    assert "phase 10-k" in docs_text
    assert "historical" in docs_text and "panel" in docs_text and "missing" in docs_text


def test_docs_mention_phase10d_baseline(docs_text):
    assert "phase 10-d" in docs_text and "baseline" in docs_text


def test_docs_mention_modest_boundary(docs_text):
    assert "modest" in docs_text and "boundary" in docs_text


def test_docs_mention_no_live_api_calls(docs_text):
    assert "no live api calls" in docs_text


def test_docs_mention_no_orders_or_automation(docs_text):
    assert "no orders" in docs_text and "no automation" in docs_text


# --- 16: forbidden-token scan (scan runner source, scoped) -----------------

def test_forbidden_token_scan():
    """Reject source patterns implying network / broker / order / deploy enablement. Scoped to concrete
    dangerous tokens so harmless safety documentation ('no orders', 'no broker', 'no deploy') passes."""
    src = RUNNER.read_text(encoding="utf-8").lower()
    forbidden = [
        "requests.get", "requests.post", "import requests", "urllib", "httpx", "aiohttp",
        "yfinance", "gcloud", "ssh",
        "create_order", "place_order", "submit_order", "broker execution",
        "api_server", "paper_trader.api.app",
    ]
    hits = [tok for tok in forbidden if tok in src]
    assert not hits, f"forbidden tokens present in runner source: {hits}"
