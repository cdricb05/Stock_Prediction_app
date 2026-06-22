"""Tests for Phase 5-G1D - Post-Live Cache Integration and Coverage Rebuild.

These tests prove the cache-only integration rebuilds the committed-safe Phase 3-M earnings
features from the local raw cache with ZERO network calls and NO API key, detects the newly
collected tickers, increases coverage, preserves raw files (never deleted / untracked / staged),
keeps newly collected raw payloads gitignored, and recommends correctly. Every run is redirected
to a temp out_dir AND uses apply=False, so the suite never overwrites the committed production
Phase 3-M artifacts (the Phase 5-G lesson).
"""
import importlib.util
import os

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODULE_PATH = os.path.join(_REPO_ROOT, "research",
                            "run_phase5g1d_post_live_cache_integration.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("phase5g1d_under_test", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


@pytest.fixture(scope="module")
def report(mod, tmp_path_factory):
    out_dir = str(tmp_path_factory.mktemp("phase5g1d_out"))
    # apply=False: audit/projection only -> never rewrites committed production artifacts.
    rep = mod.run(out_dir=out_dir, apply=False, verbose=False)
    return {"report": rep, "out_dir": out_dir}


# --------------------------------------------------------------------------- #
# Cache-only: zero network, no API key
# --------------------------------------------------------------------------- #
def test_cache_only_uses_zero_network(report):
    assert report["report"]["network_used"] is False
    assert report["report"]["cache_only_mode"] is True


def test_no_api_key_required(report):
    r = report["report"]
    assert r["api_key_required"] is False
    assert r["api_key_used"] is False
    assert r["recommendation"] != "ERROR"


def test_no_api_key_required_even_when_key_present_in_env(mod, tmp_path):
    # A stray provider key in the env must be stripped (ignored) - still cache-only, no network.
    rep = mod.run(out_dir=str(tmp_path / "withkey"),
                  env={"ALPHAVANTAGE_API_KEY": "DUMMY_SHOULD_BE_IGNORED"},
                  apply=False, verbose=False)
    assert rep["network_used"] is False
    assert rep["api_key_used"] is False
    assert rep["api_key_present_in_env_was_ignored"] is True
    assert rep["recommendation"] in mod.ALLOWED_RECOMMENDATIONS


def test_existing_phase3m_cache_only_rebuild_path_detected(mod, report):
    # Step 2 of the task: existing Phase 3-M code already has a cache-only rebuild path.
    assert report["report"]["existing_phase3m_cache_only_rebuild_path"] is True
    # The Phase 3-M no-key branch never constructs a ProviderClient -> structurally no network.
    assert hasattr(mod._load_phase3m(), "_load_cached_events")


# --------------------------------------------------------------------------- #
# Raw files preserved / ignored / unstaged
# --------------------------------------------------------------------------- #
def test_raw_files_preserved(report):
    r = report["report"]
    assert r["raw_files_deleted"] is False
    assert r["raw_files_untracked"] is False
    # apply=False must not change the raw cache count.
    assert r["raw_cache_count_after"] == r["raw_cache_count_before"]


def test_new_raw_payloads_remain_gitignored(report):
    assert report["report"]["new_raw_payload_gitignored"] is True


def test_no_raw_files_staged(report):
    r = report["report"]
    assert r["raw_files_staged"] is False
    assert r["staged_raw_files"] == []


# --------------------------------------------------------------------------- #
# Detection + coverage + recommendation
# --------------------------------------------------------------------------- #
def test_newly_cached_tickers_detected(report):
    # State-robust: new cached tickers = cached-but-not-yet-in-committed-features. Each one that
    # parses must be integrated by the rebuild. (After the integration is applied this set is
    # empty - which is correct - so we assert the relationship, not a fixed count.)
    r = report["report"]
    integrated = set(r["new_tickers_integrated"])
    for t in r["new_cached_tickers_detected"]:
        if t not in r["failed_tickers"]:
            assert t in integrated, t


def test_batch1_tickers_present_in_cache_and_integrated(mod, report):
    # State-independent proof that detection + integration works: the 20 Batch-1 names are in the
    # raw cache, parse with quarterly earnings, and are integrated into the rebuilt features.
    import csv
    out = report["out_dir"]
    batch1 = {"GS", "HCA", "HD", "HON", "IBM", "ICE", "INTC", "INTU", "ISRG", "ITW",
              "JNJ", "JPM", "KLAC", "KO", "LIN", "LLY", "LMT", "LOW", "MA", "MAR"}
    with open(os.path.join(out, mod.PARSE_STATUS_CSV_NAME), encoding="utf-8") as f:
        by_ticker = {row["ticker"]: row for row in csv.DictReader(f)}
    for t in batch1:
        assert t in by_ticker, t
        assert by_ticker[t]["has_quarterly_earnings"] == "True", t
        assert by_ticker[t]["integrated"] == "True", t


def test_coverage_increases_when_all_files_parse(report):
    r = report["report"]
    assert r["coverage_count_after"] >= r["coverage_count_before"]
    # If no cached ticker failed to parse, every newly cached ticker is integrated.
    if not r["failed_tickers"]:
        gained = r["coverage_count_after"] - r["coverage_count_before"]
        assert gained == len(r["new_cached_tickers_detected"])


def test_coverage_reflects_full_cache_rebuild(mod, report):
    # State-aware: the projected coverage equals the distinct-ticker coverage produced from the
    # currently parseable raw cache, NOT a hardcoded count. This holds for Batch 1 (coverage 70,
    # NEEDS_BATCH2, gate closed) and Batch 2+ (coverage >= 75, READY_FOR_G2, gate open). The
    # parse-status `integrated` column is exactly the set the rebuild produces from cache
    # (run_phase5g1d_...py:259 -> `t in projected_cov_set`; apply=False coverage_after == that set).
    import csv
    r = report["report"]
    out = report["out_dir"]
    with open(os.path.join(out, mod.PARSE_STATUS_CSV_NAME), encoding="utf-8") as f:
        integrated_from_cache = {row["ticker"] for row in csv.DictReader(f)
                                 if row["integrated"] == "True"}

    # 1. coverage_after equals the distinct coverage produced from the parseable raw cache.
    assert r["coverage_count_after"] == len(integrated_from_cache)
    # 2. The raw cache holds at least as many payloads as the coverage it produced.
    assert r["raw_cache_count_before"] >= r["coverage_count_after"]
    assert r["raw_cache_count_after"] >= r["coverage_count_after"]
    # 3. Healthy local state: no cached ticker failed to parse into usable PIT events.
    assert r["failed_tickers"] == []
    # 4. Coverage never regresses against the committed CSV baseline.
    assert r["coverage_count_before"] <= r["coverage_count_after"]
    # 5. Recommendation + gate are coverage-driven and consistent across Batch 1 / Batch 2.
    if r["coverage_count_after"] >= 75:
        assert r["recommendation"] == "CACHE_INTEGRATION_SUCCESS_READY_FOR_G2"
        assert r["phase5g2_allowed"] is True
    else:
        assert r["recommendation"] == "CACHE_INTEGRATION_SUCCESS_NEEDS_BATCH2"
        assert r["phase5g2_allowed"] is False


def test_recommendation_is_correct(report):
    r = report["report"]
    cov = r["coverage_count_after"]
    rec = r["recommendation"]
    assert rec in mod_allowed(r)
    if r["failed_tickers"]:
        assert rec == "CACHE_INTEGRATION_PARTIAL_REVIEW_REQUIRED"
    elif cov >= 75:
        assert rec == "CACHE_INTEGRATION_SUCCESS_READY_FOR_G2"
        assert r["phase5g2_allowed"] is True
    else:
        assert rec == "CACHE_INTEGRATION_SUCCESS_NEEDS_BATCH2"
        assert r["batch2_needed"] is True
        assert r["phase5g2_allowed"] is False


def mod_allowed(_r):
    return [
        "CACHE_INTEGRATION_SUCCESS_NEEDS_BATCH2", "CACHE_INTEGRATION_SUCCESS_READY_FOR_G2",
        "CACHE_INTEGRATION_PARTIAL_REVIEW_REQUIRED", "CACHE_INTEGRATION_BLOCKED", "ERROR",
    ]


# --------------------------------------------------------------------------- #
# Safety flags + forbidden surfaces
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


def test_no_paper_trader_gcp_deploy_order_broker_automation_changes(report):
    r = report["report"]
    forbidden_true = [k for k in (
        "orders_enabled", "automation_enabled", "broker_execution_enabled", "production_replacement",
        "deployed", "binary_artifacts_created", "network_used", "api_key_used", "raw_files_deleted",
        "raw_files_untracked", "raw_files_staged",
    ) if r.get(k) is True]
    assert forbidden_true == []


def test_recommendation_in_allowed_values(mod, report):
    assert report["report"]["recommendation"] in mod.ALLOWED_RECOMMENDATIONS


# --------------------------------------------------------------------------- #
# Artifacts written + isolation
# --------------------------------------------------------------------------- #
def test_artifacts_written(mod, report):
    out = report["out_dir"]
    for fn in (mod.REPORT_NAME, mod.AUDIT_CSV_NAME, mod.PARSE_STATUS_CSV_NAME, mod.NEXT_PLAN_NAME):
        assert os.path.isfile(os.path.join(out, fn)), fn


def test_parse_status_csv_lists_every_cached_ticker(mod, report):
    import csv
    out = report["out_dir"]
    with open(os.path.join(out, mod.PARSE_STATUS_CSV_NAME), encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    # One row per cached ticker; all integrated rows must have quarterly earnings.
    assert len(rows) >= report["report"]["coverage_count_after"]
    for row in rows:
        if row["integrated"] == "True":
            assert row["has_quarterly_earnings"] == "True", row["ticker"]


def test_audit_only_does_not_modify_committed_artifacts(report):
    # apply=False must not record any committed-safe artifact as modified.
    assert report["report"]["committed_safe_artifacts_modified"] == []


def test_run_does_not_touch_production_phase3m_dir(mod, tmp_path):
    # An apply=False run must not modify the committed Phase 3-M production dir.
    p3m = mod._load_phase3m()
    prod = p3m._M_DIR
    before = mod._hash_dir_files(prod)
    mod.run(out_dir=str(tmp_path / "iso"), apply=False, verbose=False)
    after = mod._hash_dir_files(prod)
    assert before == after
