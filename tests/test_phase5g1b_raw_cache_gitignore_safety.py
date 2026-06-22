"""Tests for Phase 5-G1B - Raw Cache Gitignore Safety Patch.

These tests prove the repo-root .gitignore now ignores future raw provider
payloads written by the Phase 3-M earnings collector, WITHOUT deleting,
modifying, or untracking any existing raw file, and without touching any
committed-safe Phase 5-G1 artifact. The audit is read-only: no network, no API
key, no live collection. Every run is redirected to a temporary output dir so
the committed production artifacts are never overwritten (the Phase 5-G lesson).

Semantic note: `git check-ignore` never reports an already-tracked path as
ignored, so the Phase 3-M raw *directory* (which still holds 50 tracked files)
is intentionally still "not ignored". The meaningful guarantee - tested here -
is that a *new, untracked* payload under that directory IS ignored.
"""
import importlib.util
import os
import subprocess

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODULE_PATH = os.path.join(
    _REPO_ROOT, "research", "run_phase5g1b_raw_cache_gitignore_safety.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("phase5g1b_under_test", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


@pytest.fixture(scope="module")
def report(mod, tmp_path_factory):
    out_dir = str(tmp_path_factory.mktemp("phase5g1b_out"))
    rep = mod.run(out_dir=out_dir, verbose=False)
    return {"report": rep, "out_dir": out_dir}


# --------------------------------------------------------------------------- #
# .gitignore exists and carries the raw-cache rule
# --------------------------------------------------------------------------- #
def test_root_gitignore_exists(mod, report):
    assert os.path.isfile(mod.GITIGNORE_PATH)
    assert report["report"]["root_gitignore_exists"] is True


def test_phase3m_raw_cache_directory_is_covered_by_an_ignore_rule(mod, report):
    # "The Phase 3-M raw cache directory is ignored" - operationally this means
    # the directory is covered by an ignore rule (so new files in it are ignored).
    r = report["report"]
    assert r["raw_cache_ignore_rule_present"] is True
    assert any("phase3m_earnings_estimates_signal_gate/raw" in rule
               or rule == "research/output/*/raw/"
               for rule in r["matching_gitignore_rules"])


def test_hypothetical_new_raw_payload_is_ignored(mod, report):
    # The real safety property: a NEW (untracked) payload under the raw dir is ignored.
    assert report["report"]["hypothetical_new_raw_payload_ignored"] is True
    assert mod._is_path_gitignored(mod.HYPOTHETICAL_NEW_RAW) is True


def test_phase5g1_committed_safe_artifacts_not_ignored(mod, report):
    r = report["report"]
    assert r["phase5g1_committed_safe_artifacts_not_ignored"] is True
    # None of the committed-safe Phase 5-G1 artifacts may be ignored.
    for fn, ignored in r["phase5g1_artifact_ignore_status"].items():
        assert ignored is False, fn


# --------------------------------------------------------------------------- #
# Read-only: no network, no API key, no live collection
# --------------------------------------------------------------------------- #
def test_no_live_network_or_api_calls(report):
    r = report["report"]
    assert r["network_used"] is False
    assert r["paid_apis_used"] is False
    assert r["live_collection_run"] is False


def test_no_api_key_required(report):
    assert report["report"]["api_key_required"] is False
    assert report["report"]["recommendation"] != "ERROR"


# --------------------------------------------------------------------------- #
# Existing raw files are neither deleted nor untracked
# --------------------------------------------------------------------------- #
def test_existing_raw_files_not_deleted(mod, report):
    r = report["report"]
    assert r["existing_raw_files_deleted"] is False
    # The historical payloads must still be on disk.
    assert r["existing_raw_files_present_count"] > 0
    assert len(mod._present_existing_raw_files()) == r["existing_raw_files_present_count"]


def test_existing_tracked_raw_files_not_removed_from_index(mod, report):
    r = report["report"]
    assert r["existing_raw_files_untracked"] is False
    # The historical payloads must still be tracked in git.
    tracked = mod._git_tracked_files(mod.PHASE3M_RAW_DIR)
    assert len(tracked) == r["existing_raw_files_tracked_count"]
    assert len(tracked) > 0


def test_no_git_rm_cached_was_performed(mod):
    # Defensive: the present-on-disk count and the tracked count must agree
    # (nothing was untracked while left on disk).
    present = mod._present_existing_raw_files()
    tracked = mod._git_tracked_files(mod.PHASE3M_RAW_DIR)
    assert len(present) == len(tracked)


# --------------------------------------------------------------------------- #
# Forbidden-surface guarantees + recommendation + artifacts
# --------------------------------------------------------------------------- #
def test_safety_flags_correct(report):
    r = report["report"]
    assert r["preview_only"] is True
    assert r["orders_enabled"] is False
    assert r["automation_enabled"] is False
    assert r["broker_execution_enabled"] is False
    assert r["production_replacement"] is False
    assert r["deployed"] is False
    assert r["binary_artifacts_created"] is False
    assert r["phase3m_outputs_modified"] is False


def test_recommendation_in_allowed_values(mod, report):
    assert report["report"]["recommendation"] in mod.ALLOWED_RECOMMENDATIONS


def test_recommendation_ready_after_patch(report):
    # Patch applied + existing data preserved -> ready for controlled live collection.
    r = report["report"]
    assert r["recommendation"] == "READY_FOR_CONTROLLED_LIVE_EARNINGS_COLLECTION"
    assert r["recommended_next_phase"]["phase"] == "5-G1-LIVE"


def test_artifacts_written(mod, report):
    out = report["out_dir"]
    for fn in (mod.REPORT_NAME, mod.AUDIT_CSV_NAME):
        assert os.path.isfile(os.path.join(out, fn)), fn


def test_audit_csv_has_no_blocking_failures(mod, report):
    import csv
    out = report["out_dir"]
    rows = list(csv.DictReader(open(os.path.join(out, mod.AUDIT_CSV_NAME), encoding="utf-8")))
    by_check = {row["check"]: row["value"] for row in rows}
    for check in ("no_paper_trader_change", "no_gcp_change", "no_deployment", "no_orders",
                  "no_broker_execution", "no_automation", "no_binary_artifacts",
                  "no_package_install", "existing_raw_files_deleted",
                  "existing_raw_files_untracked", "phase3m_outputs_modified",
                  "live_collection_run", "network_used", "paid_apis_used"):
        assert check in by_check, check
    # The forbidden surfaces must be guaranteed safe.
    assert by_check["existing_raw_files_deleted"] == "False"
    assert by_check["existing_raw_files_untracked"] == "False"
    assert by_check["network_used"] == "False"
    assert by_check["paid_apis_used"] == "False"
    assert by_check["no_paper_trader_change"] == "True"


def test_run_does_not_touch_production_artifact_dir(mod, tmp_path):
    # A run redirected to a temp dir must not modify the committed production dir.
    prod = mod._OUT_DIR
    before = {}
    if os.path.isdir(prod):
        for fn in os.listdir(prod):
            fp = os.path.join(prod, fn)
            if os.path.isfile(fp):
                with open(fp, "rb") as f:
                    before[fn] = f.read()
    mod.run(out_dir=str(tmp_path / "isolated"), verbose=False)
    after = {}
    if os.path.isdir(prod):
        for fn in os.listdir(prod):
            fp = os.path.join(prod, fn)
            if os.path.isfile(fp):
                with open(fp, "rb") as f:
                    after[fn] = f.read()
    assert before == after


def test_no_paper_trader_gcp_deploy_order_broker_automation_changes(report):
    # No forbidden surface is enabled anywhere in the report.
    r = report["report"]
    forbidden_true = [k for k in (
        "orders_enabled", "automation_enabled", "broker_execution_enabled",
        "production_replacement", "deployed", "binary_artifacts_created",
        "live_collection_run", "network_used", "paid_apis_used",
    ) if r.get(k) is True]
    assert forbidden_true == []
