"""Tests for Phase 5-G1 - Earnings Event Coverage Expansion (dry-run-first).

These tests prove the wrapper is dry-run by default, makes no network/provider call and
needs no API key in dry-run, requires an explicit --live flag for collection, never
deletes existing earnings data or cache, plans the missing tickers correctly, and emits
only safe text artifacts. Every run is redirected to a temporary output directory, so the
test suite can never overwrite the committed production artifacts (the Phase 5-G lesson).
"""
import importlib.util
import os

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODULE_PATH = os.path.join(_REPO_ROOT, "research", "run_phase5g1_earnings_coverage_expansion.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("phase5g1_under_test", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


@pytest.fixture(scope="module")
def dry_report(mod, tmp_path_factory):
    """A single dry-run into a temp dir, with an EMPTY env (no API key)."""
    out_dir = str(tmp_path_factory.mktemp("phase5g1_out"))
    report = mod.run(out_dir=out_dir, env={}, verbose=False)
    return {"report": report, "out_dir": out_dir}


# --------------------------------------------------------------------------- #
# Dry-run is the default; no network, no key, no live collection
# --------------------------------------------------------------------------- #
def test_dry_run_default_no_network(dry_report):
    r = dry_report["report"]
    assert r["live_collection_default"] is False
    assert r["explicit_live_flag_required"] is True
    assert r["live_mode_invoked"] is False
    assert r["dry_run_only_completed"] is True
    assert r["network_used"] is False
    assert r["paid_apis_used"] is False


def test_no_api_key_required_for_dry_run(mod, dry_report):
    # The dry_report fixture ran with env={}; it must still succeed with a valid recommendation.
    r = dry_report["report"]
    assert r["api_key_present_in_env"] is False
    assert r["recommendation"] != "ERROR"
    assert r["recommendation"] in mod.ALLOWED_RECOMMENDATIONS


def test_live_requires_explicit_flag(mod):
    assert mod._parse_args([]).live is False
    assert mod._parse_args(["--live"]).live is True


def test_no_provider_call_in_dry_run(mod, tmp_path, monkeypatch):
    # If dry-run ever attempted collection, run_live_collection would be invoked. Make it explode.
    def _boom(*a, **k):
        raise AssertionError("run_live_collection must NOT be called in dry-run")
    monkeypatch.setattr(mod, "run_live_collection", _boom)
    r = mod.run(out_dir=str(tmp_path / "o"), live=False, env={}, verbose=False)
    assert r["network_used"] is False
    assert r["live_collection_result"] is None


# --------------------------------------------------------------------------- #
# Existing earnings data + cache are never deleted / overwritten
# --------------------------------------------------------------------------- #
def _snapshot(path):
    out = {}
    if os.path.isdir(path):
        for fn in os.listdir(path):
            fp = os.path.join(path, fn)
            if os.path.isfile(fp):
                out[fn] = os.path.getsize(fp)
    return out


def test_existing_event_data_never_deleted(mod, tmp_path):
    events = mod._PHASE3M_EVENTS_CSV
    feats = mod._PHASE3M_FEATURES_CSV
    before = (os.path.getsize(events) if os.path.isfile(events) else None,
              os.path.getsize(feats) if os.path.isfile(feats) else None)
    mod.run(out_dir=str(tmp_path / "o"), env={}, verbose=False)
    after = (os.path.getsize(events) if os.path.isfile(events) else None,
             os.path.getsize(feats) if os.path.isfile(feats) else None)
    assert before == after
    assert os.path.isfile(events) and os.path.isfile(feats)


def test_existing_cache_preserved(mod, tmp_path):
    before = _snapshot(mod._PHASE3M_RAW_DIR)
    mod.run(out_dir=str(tmp_path / "o"), env={}, verbose=False)
    after = _snapshot(mod._PHASE3M_RAW_DIR)
    # No cached file may shrink or disappear (add-only contract).
    for fn, sz in before.items():
        assert fn in after and after[fn] >= sz


def test_run_does_not_touch_production_dir(mod, tmp_path):
    prod = mod._OUT_DIR
    before = {}
    if os.path.isdir(prod):
        for fn in os.listdir(prod):
            fp = os.path.join(prod, fn)
            if os.path.isfile(fp):
                with open(fp, "rb") as f:
                    before[fn] = f.read()
    mod.run(out_dir=str(tmp_path / "isolated"), env={}, verbose=False)
    after = {}
    if os.path.isdir(prod):
        for fn in os.listdir(prod):
            fp = os.path.join(prod, fn)
            if os.path.isfile(fp):
                with open(fp, "rb") as f:
                    after[fn] = f.read()
    assert before == after


# --------------------------------------------------------------------------- #
# Coverage detection + missing-ticker plan
# --------------------------------------------------------------------------- #
def test_current_coverage_detected(mod, dry_report):
    r = dry_report["report"]
    covered = mod.covered_tickers()
    cached = mod.cached_raw_tickers()
    # Coverage is read from the PIT feature file and must match the on-disk raw cache.
    assert r["current_coverage_count"] == len(covered)
    assert len(covered) == len(cached)
    # State-aware: coverage grows monotonically from the 50/128 authoring baseline toward the 128
    # universe (Batch 2 moved it to 75). Never hardcode a fixed count -- assert the floor + ceiling.
    assert 50 <= r["current_coverage_count"] <= mod.IDEAL_COVERAGE


def test_target_minimum_is_75(mod, dry_report):
    assert mod.TARGET_MIN_COVERAGE == 75
    assert dry_report["report"]["target_coverage_count"] == 75


def test_min_new_tickers_tracks_gap_to_75(dry_report):
    r = dry_report["report"]
    # The needed-count is purely a function of coverage vs the 75 gate -- this is the real invariant.
    assert r["minimum_new_tickers_needed"] == max(0, 75 - r["current_coverage_count"])
    # State-aware bound: at the 50 baseline it was 25; after Batch 2 (coverage 75) it is 0.
    assert 0 <= r["minimum_new_tickers_needed"] <= 25


def test_missing_ticker_plan_exists(mod, dry_report):
    plan_path = os.path.join(dry_report["out_dir"], "earnings_missing_ticker_plan.csv")
    assert os.path.isfile(plan_path)
    import csv
    rows = list(csv.DictReader(open(plan_path, encoding="utf-8")))
    r = dry_report["report"]
    assert len(rows) == r["missing_ticker_count"]
    # The first batch (needed_for_75) must total exactly the number needed to reach the gate.
    need75 = sum(1 for row in rows if row["needed_for_75"] == "True")
    assert need75 == r["minimum_new_tickers_needed"]
    # Every missing row is needed for full 128 coverage and is never already cached.
    assert all(row["needed_for_128"] == "True" for row in rows)
    assert all(row["currently_cached"] == "False" for row in rows)


def test_first_tickers_to_reach_minimum_are_deterministic(mod, dry_report):
    r = dry_report["report"]
    first = r["first_tickers_to_reach_minimum"]
    assert len(first) == r["minimum_new_tickers_needed"]
    assert first == sorted(first)  # deterministic alphabetical order
    covered = set(mod.covered_tickers())
    assert all(t not in covered for t in first)


# --------------------------------------------------------------------------- #
# Inventory + gitignore check + recommendation
# --------------------------------------------------------------------------- #
def test_collector_inventory_written(mod, dry_report):
    inv_path = os.path.join(dry_report["out_dir"], "earnings_collector_inventory.csv")
    assert os.path.isfile(inv_path)
    import csv
    rows = list(csv.DictReader(open(inv_path, encoding="utf-8")))
    scripts = {row["collector_script"] for row in rows}
    assert any("run_phase3m_earnings_estimates_signal_gate.py" in s for s in scripts)
    # The Phase 3-M collector must be marked reusable and never deleting data.
    p3m = [row for row in rows if "run_phase3m" in row["collector_script"]][0]
    assert p3m["reusable_for_expansion"] == "True"
    assert p3m["deletes_existing_data"] == "False"
    assert p3m["resumable"] == "True"


def test_raw_cache_gitignore_is_checked(mod, dry_report):
    r = dry_report["report"]
    assert "raw_cache_gitignored" in r
    assert isinstance(r["raw_cache_gitignored"], bool)
    # Must equal the actual git check-ignore verdict for the Phase 3-M raw cache path.
    assert r["raw_cache_gitignored"] == mod._is_path_gitignored(mod._PHASE3M_RAW_DIR)
    # And the safety audit must carry the explicit row.
    import csv
    safety_path = os.path.join(dry_report["out_dir"], "earnings_collection_safety_audit.csv")
    checks = {row["check"] for row in csv.DictReader(open(safety_path, encoding="utf-8"))}
    assert "raw_cache_gitignored" in checks


def test_recommendation_in_allowed_values(mod, dry_report):
    assert dry_report["report"]["recommendation"] in mod.ALLOWED_RECOMMENDATIONS


def test_recommendation_matches_coverage_state(dry_report):
    # State-aware: the recommendation flips at the 75 gate. Below it (50 baseline) the reusable
    # collector is READY_FOR_CONTROLLED_EARNINGS_COLLECTION; at/above it (Batch 2 reached 75) coverage
    # is EVENT_COVERAGE_ALREADY_SUFFICIENT. The collector stays reusable and next phase is 5-G2 in both.
    r = dry_report["report"]
    if r["current_coverage_count"] >= r["target_coverage_count"]:
        assert r["recommendation"] == "EVENT_COVERAGE_ALREADY_SUFFICIENT"
    else:
        assert r["recommendation"] == "READY_FOR_CONTROLLED_EARNINGS_COLLECTION"
    assert r["collector_reusable"] is True
    assert r["recommended_next_phase"]["phase"] == "5-G2"


# --------------------------------------------------------------------------- #
# Safety flags + forbidden-surface guarantees
# --------------------------------------------------------------------------- #
def test_safety_flags_correct(dry_report):
    r = dry_report["report"]
    assert r["preview_only"] is True
    assert r["orders_enabled"] is False
    assert r["automation_enabled"] is False
    assert r["broker_execution_enabled"] is False
    assert r["production_replacement"] is False
    assert r["deployed"] is False
    assert r["binary_artifacts_created"] is False
    assert r["existing_data_preserved"] is True


def test_safety_audit_has_no_blocking_failures(mod, dry_report):
    import csv
    safety_path = os.path.join(dry_report["out_dir"], "earnings_collection_safety_audit.csv")
    rows = list(csv.DictReader(open(safety_path, encoding="utf-8")))
    by_check = {row["check"]: row["passed"] for row in rows}
    # Every forbidden surface is explicitly guaranteed safe.
    for check in ("no_paper_trader_change", "no_gcp_change", "no_deployment", "no_orders",
                  "no_broker_execution", "no_automation", "no_binary_artifacts",
                  "no_package_install", "no_paid_apis_used", "dry_run_default",
                  "existing_event_data_preserved", "existing_raw_cache_preserved"):
        assert by_check[check] == "True", check
    assert dry_report["report"]["safety_failures"] == []


def test_all_artifacts_written(dry_report):
    out = dry_report["out_dir"]
    for fn in ("phase5g1_earnings_coverage_expansion.json", "earnings_coverage_gap_report.csv",
               "earnings_missing_ticker_plan.csv", "earnings_collector_inventory.csv",
               "earnings_collection_safety_audit.csv", "phase5g2_event_alpha_rerun_plan.json"):
        assert os.path.isfile(os.path.join(out, fn)), fn


def test_proposed_live_command_present_but_not_executed(dry_report):
    r = dry_report["report"]
    cmd = r["proposed_live_command_powershell"]
    assert "--live" in cmd
    assert "ALPHAVANTAGE_API_KEY" in cmd
    # Dry-run proved no network occurred even though the command is published.
    assert r["network_used"] is False
    assert r["controlled_live_collection_safe"] is True


def test_rerun_plan_gated_on_coverage(dry_report):
    out = dry_report["out_dir"]
    import json
    plan = json.load(open(os.path.join(out, "phase5g2_event_alpha_rerun_plan.json"),
                          encoding="utf-8"))
    assert plan["phase"] == "5-G2"
    assert plan["target_coverage_count"] == 75
    # State-aware: the re-run is greenlit iff coverage clears the 75 gate (50 baseline -> False;
    # Batch 2 coverage 75 -> True).
    cov = dry_report["report"]["current_coverage_count"]
    assert plan["proceed_to_event_alpha_rerun"] is (cov >= 75)
