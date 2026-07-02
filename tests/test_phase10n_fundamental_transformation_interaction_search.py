"""Targeted tests for Phase 10-N - Fundamental Transformation And Quality-Value Interaction Search.

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
STEM = "phase10n_fundamental_transformation_interaction_search"
RUNNER = REPO / "research" / f"run_{STEM}.py"
DOCS = REPO / "docs" / f"{STEM}_v1.md"
OUT_DIR = REPO / "research" / "output" / STEM
OUT_JSON = OUT_DIR / f"{STEM}.json"
INVENTORY = OUT_DIR / "transform_interaction_inventory.csv"
STANDALONE = OUT_DIR / "interaction_standalone_screen.csv"
SCORECARD = OUT_DIR / "transform_variant_scorecard.csv"
BASELINE_VS = OUT_DIR / "baseline_vs_variants.csv"
OOS_CSV = OUT_DIR / "oos_stability_report.csv"
COHORT_CSV = OUT_DIR / "cohort_stability_report.csv"
SECTOR_CSV = OUT_DIR / "sector_concentration_report.csv"
TURNOVER_CSV = OUT_DIR / "turnover_cost_report.csv"
REJECTED_CSV = OUT_DIR / "rejected_candidates.csv"

ALLOWED_DECISIONS = {
    "TRANSFORMED_ALPHA_READY_FOR_PAPER_RULES",
    "BASELINE_REMAINS_CHAMPION",
    "REJECT_TRANSFORM_OVERFIT",
    "NEEDS_TRANSFORM_INPUT_REPAIR",
    "NEEDS_MORE_OWNED_DATA",
}

SAFETY_KEYS = ["paper_only", "owned_local_data_only", "no_live_api_calls", "no_orders",
               "no_automation", "no_broker", "no_deploy"]

REQUIRED_INTERACTIONS = ["quality_x_value", "profitability_x_investment", "accruals_x_leverage",
                         "fcf_x_value"]
REQUIRED_TRANSFORM_IDS = ["altcomp_signed_log", "altcomp_rank", "altcomp_snrank"]


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


@pytest.fixture(scope="module")
def scorecard_ids():
    assert SCORECARD.exists(), "scorecard missing"
    with open(SCORECARD, "r", encoding="utf-8", newline="") as fh:
        return [row["variant_id"] for row in csv.DictReader(fh)]


# --- 1: runner compiles ----------------------------------------------------

def test_runner_compiles():
    py_compile.compile(str(RUNNER), doraise=True)


# --- 2/3: runs offline, JSON generated -------------------------------------

def test_output_json_generated(result):
    assert isinstance(result, dict) and result.get("phase") == "10-N"
    assert result.get("phase_name")


# --- 4: decision in enum ---------------------------------------------------

def test_decision_in_enum(result):
    assert result["decision"] in ALLOWED_DECISIONS


# --- panel reproduces 10-D -------------------------------------------------

def test_panel_reproduces_10d(result):
    rep = result["phase10d_baseline_reproduction"]
    assert rep["reproduces_within_tolerance"] is True, rep
    assert result["decision"] != "NEEDS_TRANSFORM_INPUT_REPAIR", result.get("decision_rationale")


# --- 5: baseline present ---------------------------------------------------

def test_baseline_present(result):
    b = result.get("baseline")
    assert isinstance(b, dict)
    assert b.get("ic_t_63d") is not None
    assert b.get("quarterly_net_25bps") is not None


# --- 6: required transforms + interactions present -------------------------

def test_transforms_present(result, scorecard_ids):
    for vid in REQUIRED_TRANSFORM_IDS:
        assert vid in scorecard_ids, f"transform missing from scorecard: {vid}"


def test_interactions_present(result):
    screened = {s["interaction_id"] for s in result["interaction_standalone_screen"]}
    for i in REQUIRED_INTERACTIONS:
        assert i in screened, f"interaction missing from standalone screen: {i}"


def test_candidate_caps_respected(result):
    lim = result["limits"]
    assert lim["n_transform_candidates"] <= lim["max_transform_candidates"]
    assert lim["n_interaction_candidates"] <= lim["max_interaction_candidates"]


# --- subperiod-robustness guard present ------------------------------------

def test_subperiod_guard_documented(result):
    assert "subperiod_robustness_guard" in result
    assert "rule" in result["subperiod_robustness_guard"]


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
    assert rows
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


# --- forbidden-token scan --------------------------------------------------

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
