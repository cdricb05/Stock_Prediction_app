"""Targeted tests for Phase 10-M - Owned Fundamental Incremental Alpha Expansion.

Targeted (not a full regression): compile the runner, run it once offline against the owned Norgate panel
+ owned EODHD fundamentals, and assert on the generated JSON / CSVs / docs. No network, no key, no Paper
Trader imports.
"""

import csv
import json
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STEM = "phase10m_owned_fundamental_incremental_alpha_expansion"
RUNNER = REPO / "research" / f"run_{STEM}.py"
DOCS = REPO / "docs" / f"{STEM}_v1.md"
OUT_DIR = REPO / "research" / "output" / STEM
OUT_JSON = OUT_DIR / f"{STEM}.json"
INVENTORY = OUT_DIR / "factor_input_inventory.csv"
STANDALONE = OUT_DIR / "standalone_factor_screen.csv"
SCORECARD = OUT_DIR / "composite_variant_scorecard.csv"
BASELINE_VS = OUT_DIR / "baseline_vs_variants.csv"
OOS_CSV = OUT_DIR / "oos_stability_report.csv"
COHORT_CSV = OUT_DIR / "cohort_stability_report.csv"
SECTOR_CSV = OUT_DIR / "sector_concentration_report.csv"
TURNOVER_CSV = OUT_DIR / "turnover_cost_report.csv"
REJECTED_CSV = OUT_DIR / "rejected_candidates.csv"

ALLOWED_DECISIONS = {
    "INCREMENTAL_ALPHA_READY_FOR_PAPER_RULES",
    "BASELINE_REMAINS_CHAMPION",
    "REJECT_INCREMENTAL_OVERFIT",
    "NEEDS_FACTOR_INPUT_REPAIR",
    "NEEDS_MORE_OWNED_DATA",
}

SAFETY_KEYS = ["paper_only", "owned_local_data_only", "no_live_api_calls", "no_orders",
               "no_automation", "no_broker", "no_deploy"]

# The 8 pre-declared candidate factors (must all appear in the inventory + standalone screen).
REQUIRED_FACTORS = [
    "gross_profitability", "return_on_assets", "operating_margin", "cash_return_on_assets",
    "asset_growth", "net_share_issuance", "leverage_change", "debt_to_assets",
]


@pytest.fixture(scope="module")
def result():
    proc = subprocess.run(
        [sys.executable, str(RUNNER)],
        cwd=str(REPO), capture_output=True, text=True, timeout=1800,
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


# --- 2/3: runs offline, JSON generated -------------------------------------

def test_output_json_generated(result):
    assert isinstance(result, dict) and result.get("phase") == "10-M"
    assert result.get("phase_name")


# --- 4: decision in enum ---------------------------------------------------

def test_decision_in_enum(result):
    assert result["decision"] in ALLOWED_DECISIONS


# --- panel reproduces 10-D (else NEEDS_FACTOR_INPUT_REPAIR) -----------------

def test_panel_reproduces_10d(result):
    rep = result["phase10d_baseline_reproduction"]
    assert rep["reproduces_within_tolerance"] is True, rep
    assert result["decision"] != "NEEDS_FACTOR_INPUT_REPAIR", result.get("decision_rationale")


# --- 5: baseline present ---------------------------------------------------

def test_baseline_present(result):
    b = result.get("baseline")
    assert isinstance(b, dict)
    assert b.get("ic_t_63d") is not None
    assert b.get("quarterly_net_25bps") is not None


# --- 6: candidate factors present ------------------------------------------

def test_candidate_factors_present(result):
    assert result["n_candidate_factors"] == len(REQUIRED_FACTORS)
    inv_feats = {r["feature"] for r in result["input_inventory"]}
    screen_feats = {s["feature"] for s in result["standalone_screen"]}
    for f in REQUIRED_FACTORS:
        assert f in inv_feats, f"factor missing from inventory: {f}"
        assert f in screen_feats, f"factor missing from standalone screen: {f}"


def test_inventory_and_standalone_csvs():
    assert INVENTORY.exists() and STANDALONE.exists()
    with open(STANDALONE, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    feats = {r["feature"] for r in rows}
    for f in REQUIRED_FACTORS:
        assert f in feats, f"factor missing from standalone_factor_screen.csv: {f}"
    # every factor carries an eligibility verdict
    assert all("eligible" in r for r in rows)


# --- 7: 25bps and 50bps costs present --------------------------------------

def test_costs_present(result):
    for v in result["variants_tested"]:
        assert "quarterly_net_25bps" in v and "quarterly_net_50bps" in v
    base = next(v for v in result["variants_tested"] if v["classification"] == "BASELINE")
    assert base["quarterly_net_25bps"] is not None
    assert base["quarterly_net_50bps"] is not None


def test_turnover_cost_report_has_costs():
    assert TURNOVER_CSV.exists()
    with open(TURNOVER_CSV, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows, "turnover_cost_report is empty"
    assert "quarterly_net_25bps" in rows[0] and "quarterly_net_50bps" in rows[0]


# --- 8: OOS report present -------------------------------------------------

def test_oos_report_present():
    assert OOS_CSV.exists()
    with open(OOS_CSV, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows
    assert "oos_frac_windows_positive" in rows[0]


# --- 9: rejected candidates present ----------------------------------------

def test_rejected_candidates_present(result):
    assert REJECTED_CSV.exists()
    assert isinstance(result.get("rejected_candidates"), list)


# --- supporting CSV artifacts exist ----------------------------------------

def test_all_csv_artifacts_exist():
    for p in (INVENTORY, STANDALONE, SCORECARD, BASELINE_VS, OOS_CSV, COHORT_CSV, SECTOR_CSV,
              TURNOVER_CSV, REJECTED_CSV):
        assert p.exists(), f"missing artifact: {p.name}"


# --- champion + required JSON blocks ---------------------------------------

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


# --- doc content -----------------------------------------------------------

def test_docs_mention_owned_local_data(docs_text):
    assert "owned/local data only" in docs_text or "owned / local" in docs_text


def test_docs_mention_modest_boundary(docs_text):
    assert "modest" in docs_text and "boundary" in docs_text


def test_docs_mention_no_live_api_calls(docs_text):
    assert "no live api calls" in docs_text


def test_docs_mention_no_orders_or_automation(docs_text):
    assert "no orders" in docs_text and "no automation" in docs_text


# --- forbidden-token scan (scan runner source, scoped) ---------------------

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
