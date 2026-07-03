"""Targeted tests for Phase 11-A - Orthogonal Data Acquisition Decision Package.

Targeted (not a full regression): compile the runner, run it once fully offline (design/decision
synthesis - no network, no key, no provider probe, no Paper Trader imports), and assert on the generated
JSON / CSVs / docs.
"""

import csv
import json
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STEM = "phase11a_orthogonal_data_acquisition_decision_package"
RUNNER = REPO / "research" / f"run_{STEM}.py"
DOCS = REPO / "docs" / f"{STEM}_v1.md"
OUT_DIR = REPO / "research" / "output" / STEM
OUT_JSON = OUT_DIR / f"{STEM}.json"
SCORECARD = OUT_DIR / "data_family_scorecard.csv"
VENDORS = OUT_DIR / "vendor_candidate_scorecard.csv"
FIELDS = OUT_DIR / "analyst_revisions_required_fields.csv"
ACCEPTANCE = OUT_DIR / "phase11b_trial_acceptance_criteria.csv"
RISK = OUT_DIR / "integration_risk_register.csv"

ALLOWED_DECISIONS = {
    "ANALYST_REVISIONS_FIRST",
    "SHORT_INTEREST_FIRST",
    "OPTIONS_DATA_FIRST",
    "SENTIMENT_DATA_FIRST",
    "PAUSE_NO_DATA_BUDGET",
}

SAFETY_KEYS = ["paper_only", "owned_local_data_only", "no_live_api_calls", "no_orders",
               "no_automation", "no_broker", "no_deploy"]


@pytest.fixture(scope="module")
def result():
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


def test_runner_compiles():
    py_compile.compile(str(RUNNER), doraise=True)


def test_runner_runs_offline(result):
    # runner ran to completion in the fixture; assert it self-reports offline / no network / no key
    assert result.get("phase") == "11-A"
    assert result.get("phase_name")
    assert result.get("offline") is True
    assert result.get("performs_network") is False
    assert result.get("eodhd_key_required") is False


def test_output_json_generated(result):
    assert isinstance(result, dict)
    assert OUT_JSON.exists()


def test_decision_in_enum(result):
    assert result["decision"] in ALLOWED_DECISIONS


def test_decision_is_analyst_revisions_first(result):
    # given the exhausted owned data + strongest orthogonality/evidence, the honest first pick is analyst
    # estimate revisions
    assert result["decision"] == "ANALYST_REVISIONS_FIRST", result.get("decision_rationale")
    champ = result["champion"]
    assert (champ.get("family_key") == "analyst_estimate_revisions")
    assert champ.get("requires_explicit_user_opt_in") is True
    assert champ.get("is_new_orthogonal_paid_data") is True


def test_analyst_revisions_evaluated(result):
    # analyst estimate revisions must be among the scored candidate families and ranked #1
    families = {c["key"]: c for c in result["candidates_tested"]}
    assert "analyst_estimate_revisions" in families, "analyst revisions family not evaluated"
    assert families["analyst_estimate_revisions"]["rank"] == 1
    # and it must be the top of the scorecard
    top = min(result["data_family_scorecard"], key=lambda r: r["rank"])
    assert top["family_key"] == "analyst_estimate_revisions"


def test_all_six_families_scored(result):
    keys = {c["key"] for c in result["candidates_tested"]}
    expected = {
        "analyst_estimate_revisions", "short_interest_securities_lending", "options_iv_skew",
        "insider_transactions", "institutional_ownership_13f", "news_event_sentiment_pit",
    }
    assert expected <= keys, f"missing families: {expected - keys}"


def test_each_family_has_required_dimensions(result):
    required = ["economic_rationale", "orthogonality_to_fcf_accruals", "update_frequency",
                "useful_horizons", "required_pit_fields", "required_historical_depth",
                "required_universe_coverage", "survivorship_bias_risks", "cost_entitlement_risk",
                "integration_complexity", "data_quality_checks", "mvp_trial_acceptance"]
    for fam in result["family_evaluations"]:
        for dim in required:
            assert dim in fam and fam[dim], f"{fam.get('key')} missing dimension: {dim}"
        # horizons must cover 5d / 21d / 63d
        for h in ("5d", "21d", "63d"):
            assert h in fam["useful_horizons"], f"{fam.get('key')} missing horizon {h}"


def test_analyst_required_fields_block(result):
    fields = result["analyst_revisions_required_fields"]
    keys = {f["field_key"] for f in fields}
    # the point-in-time effective date is the mandatory join key
    assert "pit_effective_date" in keys
    # the core revision-flow fields must be present
    for k in ("up_revisions_count", "down_revisions_count", "estimate_change_30d", "num_analysts"):
        assert k in keys, f"required analyst field missing: {k}"


def test_phase11b_test_plan_and_criteria(result):
    plan = result["phase11b_test_plan"]
    assert isinstance(plan, list) and len(plan) >= 8
    joined = " ".join(plan).lower()
    for token in ("normalize", "point-in-time", "5d", "21d", "63d", "standalone", "composite_sn",
                  "cost", "oos"):
        assert token in joined, f"11-B plan missing step token: {token}"
    ac = result["phase11b_acceptance_criteria"]
    assert isinstance(ac, list) and len(ac) >= 8


def test_required_report_blocks(result):
    for key in ("input_inventory", "baseline", "data_family_scorecard", "candidates_tested",
                "variants_tested", "rejected_candidates", "champion", "baseline_vs_champion",
                "vendor_candidates", "integration_risk_register", "oos_stability_summary",
                "cohort_stability_summary", "sector_concentration_summary", "turnover_cost_summary",
                "implementation_limits", "recommended_next_actions", "next_recommended_phase"):
        assert key in result, f"missing report block: {key}"


def test_vendors_not_probed(result):
    vendors = result["vendor_candidates"]
    assert vendors, "vendor candidates missing"
    for v in vendors:
        assert v.get("no_probe_performed") is True
        assert v.get("requires_user_opt_in") in ("yes", True)


def test_all_csv_artifacts_exist():
    for p in (SCORECARD, VENDORS, FIELDS, ACCEPTANCE, RISK):
        assert p.exists(), f"missing artifact: {p.name}"


def test_required_fields_csv_content():
    with open(FIELDS, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    keys = {r["field_key"] for r in rows}
    assert "pit_effective_date" in keys
    assert "up_revisions_count" in keys and "down_revisions_count" in keys


def test_acceptance_criteria_csv_content():
    with open(ACCEPTANCE, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    ids = {r["criterion_id"] for r in rows}
    # the OOS-stability reject rule must be an explicit acceptance criterion
    assert any("oos" in i.lower() for i in ids), ids


def test_scorecard_csv_ranks_analyst_first():
    with open(SCORECARD, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    rows.sort(key=lambda r: int(r["rank"]))
    assert rows[0]["family_key"] == "analyst_estimate_revisions"


def test_safety_flags_true(result):
    s = result["safety"]
    for key in SAFETY_KEYS:
        assert s.get(key) is True, f"safety flag not True: {key}"


def test_docs_mention_owned_data_exhausted(docs_text):
    assert "owned-data search was exhausted" in docs_text


def test_docs_mention_no_api_no_probing(docs_text):
    assert "no api calls" in docs_text
    assert "no provider probing" in docs_text


def test_docs_mention_no_orders_or_automation(docs_text):
    assert "no orders" in docs_text and "no automation" in docs_text


def test_docs_mention_modest_boundary(docs_text):
    assert "modest" in docs_text and "boundary" in docs_text


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
