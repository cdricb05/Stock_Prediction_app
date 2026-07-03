"""Targeted tests for Phase 12-A - Nasdaq Data Link Zacks entitlement / history probe.

The runner is exercised in OFFLINE replay mode (rebuilds every report from the committed,
key-free probe_log.json), so this suite is deterministic and makes ZERO network calls.
"""
import csv
import json
import os
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STEM = "phase12a_nasdaq_zacks_entitlement_download_probe"
RUNNER = REPO / "research" / f"run_{STEM}.py"
DOCS = REPO / "docs" / f"{STEM}_v1.md"
OUT_DIR = REPO / "research" / "output" / STEM
OUT_JSON = OUT_DIR / f"{STEM}.json"
PROBE_LOG = OUT_DIR / "probe_log.json"

CSVS = [
    "nasdaq_zacks_table_probe_results.csv",
    "nasdaq_zacks_schema_inventory.csv",
    "nasdaq_zacks_alpha_readiness_check.csv",
    "nasdaq_zacks_sample_download_manifest.csv",
    "nasdaq_zacks_blocked_tables.csv",
    "nasdaq_zacks_next_action.csv",
]

ALLOWED = {
    "NASDAQ_ZACKS_READY_FOR_FULL_DOWNLOAD", "NASDAQ_ZACKS_CURRENT_ONLY_NOT_BACKTESTABLE",
    "NASDAQ_ZACKS_ENTITLEMENT_BLOCKED", "NASDAQ_ZACKS_SCHEMA_BLOCKED",
    "NASDAQ_ZACKS_NEEDS_CONTACT_SALES_OR_TRIAL", "NASDAQ_ZACKS_NO_USABLE_TABLES",
}


@pytest.fixture(scope="module")
def result():
    assert PROBE_LOG.exists(), "probe_log.json missing; run the live probe once before testing"
    env = dict(os.environ, PHASE12A_OFFLINE="1")
    proc = subprocess.run([sys.executable, str(RUNNER), "--offline"], cwd=str(REPO),
                          capture_output=True, text=True, timeout=120, env=env)
    assert proc.returncode == 0, f"runner failed: {proc.stdout}\n{proc.stderr}"
    with open(OUT_JSON, "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def docs_text():
    assert DOCS.exists()
    return DOCS.read_text(encoding="utf-8").lower()


def test_runner_compiles():
    py_compile.compile(str(RUNNER), doraise=True)


def test_offline_replay_is_deterministic(result):
    # a second offline run must produce byte-identical JSON (no time/hash drift)
    first = OUT_JSON.read_bytes()
    env = dict(os.environ, PHASE12A_OFFLINE="1")
    subprocess.run([sys.executable, str(RUNNER), "--offline"], cwd=str(REPO),
                   capture_output=True, text=True, timeout=120, env=env, check=True)
    assert OUT_JSON.read_bytes() == first


def test_phase_and_decision(result):
    assert result["phase"] == "12-A"
    assert result["decision"] in ALLOWED
    # observed live outcome: the free key exposes only Nasdaq's premium SAMPLE
    assert result["decision"] == "NASDAQ_ZACKS_NEEDS_CONTACT_SALES_OR_TRIAL"
    assert result["next_phase"] == "user_opt_in_paid_zacks_trial"


def test_free_tier_detected_as_sample(result):
    assert result["free_tier_is_sample"] is True
    assert result["full_access_tables"] == []
    # the point-in-time estimate-history tables must be present but sample-gated
    assert "ZACKS/EEH" in result["sample_only_tables"]


def test_pit_tables_have_obs_date_schema(result):
    # EEH / SEH / EREV are the genuine point-in-time tables
    pit = set(result["schema_summary"]["pit_tables"])
    assert {"ZACKS/EEH", "ZACKS/EREV"}.issubset(pit)


def test_ee_is_current_snapshot_only(result):
    # ZACKS/EE has no obs_date -> must NOT be labelled historical/backtestable
    assert result["schema_class_by_table"]["ZACKS/EE"] == "CURRENT_SNAPSHOT_ONLY"


def test_no_full_download_started(result):
    assert result["phase12b_full_download"]["started"] is False


def test_key_never_leaked(result):
    # the actual key value must not appear in any produced artifact
    key = os.environ.get("NASDAQ_DATA_LINK_API_KEY") or os.environ.get("QUANDL_API_KEY")
    assert result["api_key_value_printed"] is False
    assert result["api_key_env_used"] in ("NASDAQ_DATA_LINK_API_KEY", "QUANDL_API_KEY", None)
    blobs = [OUT_JSON.read_text(encoding="utf-8"), PROBE_LOG.read_text(encoding="utf-8")]
    for c in CSVS:
        p = OUT_DIR / c
        if p.exists():
            blobs.append(p.read_text(encoding="utf-8"))
    joined = "\n".join(blobs)
    # the probe log stores no key-bearing URLs at all -> "api_key=" must be absent everywhere
    assert "api_key=" not in joined, "a key-bearing URL leaked into an artifact"
    if key:
        assert key not in joined, "raw API key leaked into an artifact"


def test_next_action_is_concrete(result):
    na = result["next_action"]
    assert na and isinstance(na, list)
    joined = " ".join(r["action"] + " " + r["detail"] for r in na).lower()
    # concrete: names the product/trial and alternate providers, not vague 'email support'
    assert "trial" in joined or "subscribe" in joined or "subscription" in joined
    assert "intrinio" in joined
    assert "fmp" in joined
    assert "email" not in joined  # explicitly NOT told to email support


def test_artifacts_exist(result):
    assert OUT_JSON.exists()
    for c in CSVS:
        assert (OUT_DIR / c).exists(), f"missing artifact: {c}"


def test_probe_results_csv_content():
    with open(OUT_DIR / "nasdaq_zacks_table_probe_results.csv", "r",
              encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    codes = {r["code"] for r in rows}
    assert {"ZACKS/EE", "ZACKS/EEH", "ZACKS/EREV", "ZACKS/SEH"}.issubset(codes)
    eeh = next(r for r in rows if r["code"] == "ZACKS/EEH")
    assert eeh["obs_date_column"] == "obs_date"
    assert eeh["appears_sample"] == "True"


def test_alpha_readiness_csv_flags_revisions():
    with open(OUT_DIR / "nasdaq_zacks_alpha_readiness_check.csv", "r",
              encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    eeh = {r["concept"]: r["present"] for r in rows if r["code"] == "ZACKS/EEH"}
    assert eeh["upward_revision_count"] == "True"
    assert eeh["downward_revision_count"] == "True"
    assert eeh["observation_or_effective_date"] == "True"
    assert eeh["_SCHEMA_BACKTESTABLE"] == "True"     # schema fits
    assert eeh["_FULL_ACCESS_NOT_SAMPLE"] == "False"  # but sample-gated
    assert eeh["_BACKTESTABLE"] == "False"


def test_safety(result):
    for k in ("paper_only", "no_secret_printed", "no_orders", "no_automation", "no_broker",
              "no_deploy", "no_gcp", "no_paper_trader_writes", "no_push"):
        assert result["safety"][k] is True


def test_docs_mentions(docs_text):
    for token in ("no orders", "no automation", "sample", "obs_date", "point-in-time",
                  "trial", "zacks/eeh", "no api key printed"):
        assert token in docs_text, f"docs missing token: {token}"


def test_forbidden_token_scan():
    # Network via urllib IS allowed in this phase's runner (scoped exception); the truly
    # dangerous tokens must still be absent.
    src = RUNNER.read_text(encoding="utf-8").lower()
    forbidden = ["requests.get", "requests.post", "import requests", "httpx", "aiohttp",
                 "yfinance", "gcloud", "create_order", "place_order", "submit_order",
                 "broker execution", "api_server", "paper_trader.api.app", "os.system",
                 "subprocess."]
    hits = [t for t in forbidden if t in src]
    assert not hits, f"forbidden tokens present: {hits}"
