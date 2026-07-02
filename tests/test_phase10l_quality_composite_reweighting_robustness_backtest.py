"""Targeted tests for Phase 10-L-B - Historical Quality Composite Reweighting And Robustness Backtest.

Targeted (not a full regression): compile the runner, run it once offline against the frozen Phase
10-L-A panel, and assert on the generated JSON / CSVs / docs. No network, no key, no Paper Trader imports.
"""

import csv
import json
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STEM = "phase10l_quality_composite_reweighting_robustness_backtest"
RUNNER = REPO / "research" / f"run_{STEM}.py"
DOCS = REPO / "docs" / f"{STEM}_v1.md"
OUT_DIR = REPO / "research" / "output" / STEM
OUT_JSON = OUT_DIR / f"{STEM}.json"
SCORECARD = OUT_DIR / "variant_scorecard.csv"
BASELINE_VS = OUT_DIR / "baseline_vs_variants.csv"
OOS_CSV = OUT_DIR / "oos_stability_report.csv"
COHORT_CSV = OUT_DIR / "cohort_stability_report.csv"
TURNOVER_CSV = OUT_DIR / "turnover_cost_report.csv"
SECTOR_CSV = OUT_DIR / "sector_concentration_report.csv"
REJECTED_CSV = OUT_DIR / "rejected_variants.csv"

ALLOWED_DECISIONS = {
    "REWEIGHTED_ALPHA_READY_FOR_PAPER_RULES",
    "BASELINE_REMAINS_CHAMPION",
    "REJECT_REWEIGHTING_OVERFIT",
    "NEEDS_PANEL_REPAIR",
}

SAFETY_KEYS = ["paper_only", "owned_local_data_only", "no_live_api_calls", "no_orders",
               "no_automation", "no_broker", "no_deploy"]

REQUIRED_VARIANT_IDS = [
    "w_50_50", "w_60_40", "w_40_60", "w_70_30", "w_30_70",
    "w_fcf_only_100_0", "w_accruals_only_0_100",
    "zcap_abs_3_0", "zcap_abs_2_5", "winsorize_1_99", "winsorize_5_95",
    "liq_p25_baseline", "liq_p50_stricter",
    "sector_cap_25_baseline", "sector_cap_20_stricter",
    "best_weight_plus_zcap_3_0", "best_weight_plus_liq_p50", "best_weight_plus_sector_cap_20",
]


@pytest.fixture(scope="module")
def result():
    """Run the runner offline once (req: runs offline) and return the parsed JSON (req: JSON exists)."""
    proc = subprocess.run(
        [sys.executable, str(RUNNER)],
        cwd=str(REPO), capture_output=True, text=True, timeout=900,
    )
    assert proc.returncode == 0, f"runner failed: {proc.stdout}\n{proc.stderr}"
    assert OUT_JSON.exists(), "runner did not produce the output JSON"
    with open(OUT_JSON, "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def scorecard_ids():
    assert SCORECARD.exists(), "variant_scorecard.csv missing"
    with open(SCORECARD, "r", encoding="utf-8", newline="") as fh:
        return [row["variant_id"] for row in csv.DictReader(fh)]


@pytest.fixture(scope="module")
def docs_text():
    assert DOCS.exists(), "docs file missing"
    return DOCS.read_text(encoding="utf-8").lower()


# --- 1: runner compiles ----------------------------------------------------

def test_runner_compiles():
    py_compile.compile(str(RUNNER), doraise=True)


# --- 2/3: runs offline, JSON generated -------------------------------------

def test_output_json_generated(result):
    assert isinstance(result, dict) and result.get("phase") == "10-L-B"


# --- 4: decision in enum ---------------------------------------------------

def test_decision_in_enum(result):
    assert result["decision"] in ALLOWED_DECISIONS


def test_panel_reproduces_10d(result):
    """The frozen 10-L-A panel must reproduce the 10-D baseline (else NEEDS_PANEL_REPAIR)."""
    rep = result["phase10d_baseline_reproduction"]
    assert rep["reproduces_within_tolerance"] is True, rep
    assert result["decision"] != "NEEDS_PANEL_REPAIR", result.get("decision_rationale")


# --- 5: baseline present ---------------------------------------------------

def test_baseline_present(result):
    b = result.get("baseline")
    assert isinstance(b, dict)
    assert b.get("ic_t_63d") is not None
    assert b.get("quarterly_net_25bps") is not None


# --- 6: required variants present ------------------------------------------

def test_required_variants_present(result, scorecard_ids):
    tested = {v["variant_id"] for v in result["variants_tested"]}
    for vid in REQUIRED_VARIANT_IDS:
        assert vid in tested, f"variant missing from JSON: {vid}"
        assert vid in scorecard_ids, f"variant missing from scorecard: {vid}"


def test_variant_count(result):
    assert result["n_variants"] == len(REQUIRED_VARIANT_IDS)


# --- 7: 25bps and 50bps costs present --------------------------------------

def test_costs_present(result):
    for v in result["variants_tested"]:
        # every backtested variant reports both cost columns (None allowed only if not backtestable)
        assert "quarterly_net_25bps" in v and "quarterly_net_50bps" in v
    base = next(v for v in result["variants_tested"] if v.get("is_baseline"))
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
    assert len(rows) >= len(REQUIRED_VARIANT_IDS)
    assert "oos_frac_windows_positive" in rows[0]


# --- 9: rejected variants present ------------------------------------------

def test_rejected_variants_present(result):
    assert REJECTED_CSV.exists()
    assert isinstance(result.get("rejected_variants"), list)


# --- supporting CSV artifacts exist ----------------------------------------

def test_all_csv_artifacts_exist():
    for p in (SCORECARD, BASELINE_VS, OOS_CSV, COHORT_CSV, TURNOVER_CSV, SECTOR_CSV, REJECTED_CSV):
        assert p.exists(), f"missing artifact: {p.name}"


# --- champion + safety -----------------------------------------------------

def test_champion_present(result):
    champ = result.get("champion")
    assert isinstance(champ, dict) and champ.get("champion")


def test_safety_flags_true(result):
    s = result["safety"]
    for key in SAFETY_KEYS:
        assert s.get(key) is True, f"safety flag not True: {key}"


# --- 10-13: doc content ----------------------------------------------------

def test_docs_mention_10la_panel(docs_text):
    assert "phase 10-l-a" in docs_text and "panel" in docs_text


def test_docs_mention_modest_boundary(docs_text):
    assert "modest" in docs_text and "boundary" in docs_text


def test_docs_mention_no_live_api_calls(docs_text):
    assert "no live api calls" in docs_text


def test_docs_mention_no_orders_or_automation(docs_text):
    assert "no orders" in docs_text and "no automation" in docs_text


# --- 14: forbidden-token scan (scan runner source, scoped) -----------------

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
