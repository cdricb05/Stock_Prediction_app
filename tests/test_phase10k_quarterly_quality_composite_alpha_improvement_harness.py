"""Targeted tests for Phase 10-K - Quarterly Quality Composite Alpha Improvement Harness.

These are targeted tests only (not a full regression). They compile the runner,
run it offline, and assert on the generated JSON / docs. No network, no key, no
Paper Trader imports.
"""

import json
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STEM = "phase10k_quarterly_quality_composite_alpha_improvement_harness"
RUNNER = REPO / "research" / f"run_{STEM}.py"
DOCS = REPO / "docs" / f"{STEM}_v1.md"
OUT_DIR = REPO / "research" / "output" / STEM
OUT_JSON = OUT_DIR / f"{STEM}.json"

REQUIRED_TOP_LEVEL_KEYS = [
    "phase", "phase_name", "decision", "decision_rationale", "input_inventory",
    "baseline", "variants_tested", "champion", "baseline_vs_champion",
    "leg_contribution_summary", "turnover_cost_summary", "sector_liquidity_diagnostics",
    "rejected_variants", "implementation_limits", "next_recommended_phase", "safety",
]

ALLOWED_DECISIONS = {
    "ENHANCED_ALPHA_READY_FOR_PAPER_RULES",
    "BASELINE_REMAINS_CHAMPION",
    "NEEDS_PHASE_INPUT_REPAIR",
    "NEEDS_MORE_OWNED_DATA",
    "REJECT_ENHANCEMENT_OVERFIT",
}

REQUIRED_WEIGHT_VARIANTS = [
    "w_50_50_equal", "w_60_40", "w_40_60", "w_70_30", "w_30_70",
    "w_fcf_only_100_0", "w_accruals_only_0_100",
]

REQUIRED_CSVS = [
    "variant_scorecard.csv", "baseline_vs_enhancements.csv", "leg_contribution_summary.csv",
    "turnover_cost_summary.csv", "sector_liquidity_diagnostics.csv", "rejected_variants.csv",
]


@pytest.fixture(scope="module")
def result():
    """Run the runner offline once and return the parsed JSON (req #4)."""
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


# --- 1/2/3: files compile & exist -----------------------------------------

def test_runner_compiles():
    py_compile.compile(str(RUNNER), doraise=True)


def test_docs_file_exists():
    assert DOCS.exists()


def test_runner_file_exists():
    assert RUNNER.exists()


# --- 4/5/6: JSON generated, keys present, decision valid -------------------

def test_output_json_generated(result):
    assert isinstance(result, dict) and result.get("phase") == "10-K"


def test_required_top_level_keys(result):
    for key in REQUIRED_TOP_LEVEL_KEYS:
        assert key in result, f"missing top-level key: {key}"


def test_decision_in_enum(result):
    assert result["decision"] in ALLOWED_DECISIONS


# --- 7-10: baseline object -------------------------------------------------

def test_baseline_includes_composite_sn(result):
    assert result["baseline"]["honest_comparator"] == "composite_sn"


def test_baseline_references_fcf(result):
    assert result["baseline"]["long_signal"] == "fcf_to_assets"


def test_baseline_references_accruals(result):
    assert result["baseline"]["short_signal"] == "operating_accruals"


def test_baseline_references_quarterly_63d(result):
    b = result["baseline"]
    assert b["horizon_days"] == 63
    assert "quarterly" in b["horizon_label"].lower() and "63" in b["horizon_label"]


# --- 11: required weight variants ------------------------------------------

def test_variants_include_required_weights(result):
    ids = {v["variant_id"] for v in result["variants_tested"]}
    for vid in REQUIRED_WEIGHT_VARIANTS:
        assert vid in ids, f"missing required weight variant: {vid}"


# --- 12: 25bps and 50bps cost cases ----------------------------------------

def test_output_includes_25bps_and_50bps(result):
    b = result["baseline"]
    assert b["quarterly_net_25bps"] is not None
    assert b["quarterly_net_50bps"] is not None
    models = {r["model"] for r in result["turnover_cost_summary"]["rows"]}
    assert any("quarterly" in m for m in models)
    # both cost columns present on cost rows
    row = result["turnover_cost_summary"]["rows"][0]
    assert "net_25bps" in row and "net_50bps" in row


# --- 13: long-leg and short-leg diagnostics --------------------------------

def test_output_includes_leg_diagnostics(result):
    leg = result["leg_contribution_summary"]
    assert leg["long_leg"]["signal"] == "fcf_to_assets"
    assert leg["short_leg"]["signal"] == "operating_accruals"
    assert "attribution" in leg


# --- 14: rejected variants -------------------------------------------------

def test_output_includes_rejected_variants(result):
    assert isinstance(result["rejected_variants"], list)
    assert len(result["rejected_variants"]) >= 1
    for r in result["rejected_variants"]:
        assert "variant_id" in r and "classification" in r and "reason" in r


# --- 15: implementation limits ---------------------------------------------

def test_output_includes_implementation_limits(result):
    lim = result["implementation_limits"]
    assert isinstance(lim, list) and len(lim) >= 1


# --- 16-21: doc content ----------------------------------------------------

def test_docs_mention_modest_boundary(docs_text):
    assert "modest" in docs_text and "boundary" in docs_text


def test_docs_mention_prior_search_failed_or_diluted(docs_text):
    assert "dilut" in docs_text  # "diluted" / "dilution"


def test_docs_mention_provider_exhausted_or_blocked(docs_text):
    assert "exhausted" in docs_text or "blocked" in docs_text


def test_docs_mention_f_accel_sn_fragile(docs_text):
    assert "f_accel_sn" in docs_text
    assert "fragile" in docs_text or "overfit" in docs_text


def test_docs_mention_monthly_cost_killed(docs_text):
    assert "cost-killed" in docs_text and "monthly" in docs_text


def test_docs_mention_10i_price_block(docs_text):
    assert "2026-06-26" in docs_text


# --- extra: safety object + CSV artifacts ----------------------------------

def test_safety_object(result):
    s = result["safety"]
    for key in ("paper_only", "uses_owned_local_data_only", "no_live_api_calls",
                "no_orders", "no_automation", "no_broker", "no_deploy"):
        assert s.get(key) is True, f"safety flag not True: {key}"


def test_required_csv_artifacts_exist(result):
    for name in REQUIRED_CSVS:
        assert (OUT_DIR / name).exists(), f"missing CSV artifact: {name}"


# --- 22: forbidden-token scan (scan runner source, carefully) --------------

def test_forbidden_token_scan():
    """Reject source patterns implying network / broker / order / deploy / automation
    enablement. Scoped to concrete dangerous tokens so that harmless documentation of
    'no orders' / 'no automation' / 'no deploy' does NOT trip the scan."""
    src = RUNNER.read_text(encoding="utf-8").lower()
    forbidden = [
        "requests.get", "requests.post", "import requests", "urllib", "httpx", "aiohttp",
        "yfinance", "boto3", "paramiko", "socket.socket",
        "create_order", "place_order", "submit_order", "send_order",
        "gcloud", "subprocess.popen",
        "api_server", "from api", "import api",
    ]
    hits = [tok for tok in forbidden if tok in src]
    assert not hits, f"forbidden tokens present in runner source: {hits}"
