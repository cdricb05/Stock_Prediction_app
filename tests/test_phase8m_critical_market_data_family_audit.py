"""Tests for Phase 8-M - Critical Market-Data Family Entitlement Audit and Controller Fix.

Proves the brief's acceptance criteria, fully offline (no key, no network):
  * earnings/analyst endpoint families are prioritized BEFORE fundamentals;
  * no endpoint cap can prevent critical families from being attempted (cap-exempt);
  * every mandatory data family ends with a concrete status (the 9-status vocabulary);
  * the FMP key is never printed or written; raw/normalized paid data stay gitignored;
  * failure diagnosis distinguishes subscription vs endpoint vs rate-limit;
  * provider-alternative + cheapest-viable matrices and the signal-unlock map are produced;
  * no Paper Trader / GCP / order / deployment logic; committed-safe outputs only.

The live entitlement probe is exercised via an injected ``transport`` so the suite needs
neither FMP_API_KEY nor a network. Module-scoped fixtures write to isolated tmp dirs.
"""
from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _load():
    path = _REPO_ROOT / "research" / "run_phase8m_critical_market_data_family_audit.py"
    spec = importlib.util.spec_from_file_location("phase8m_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P8M = _load()
import research.providers.fmp_client as fmp  # noqa: E402


# --------------------------------------------------------------------------- #
# A deterministic fake transport that exercises ALL nine outcome classes.
# --------------------------------------------------------------------------- #
def fake_transport(path: str):
    p = path.lower()
    if "analyst-estimates" in p:
        raise fmp.FmpError("provider returned HTTP 403", status_code=403, error_type="http_error")
    if "grades-consensus" in p:
        raise fmp.FmpError("provider returned HTTP 404", status_code=404, error_type="http_error")
    if "ratios" in p:
        raise fmp.FmpError("provider returned HTTP 402", status_code=402, error_type="http_error")
    if "key-metrics" in p:
        raise fmp.FmpError("provider returned HTTP 429", status_code=429, error_type="http_error")
    if "price-target" in p:
        return []  # 200 empty -> EMPTY_BUT_ENDPOINT_REACHABLE
    return [{"symbol": "X", "date": "2026-01-01", "value": 1.0}]


def _read_csv(path: Path):
    with open(path, "r", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- #
# Module-scoped runs (isolated tmp dirs so the two runs never clobber on disk).
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def dry_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("p8m_dry_out")
    data = tmp_path_factory.mktemp("p8m_dry_data")
    rep = P8M.run(live=False, out_dir=Path(out), data_dir=Path(data), verbose=False)
    return {"report": rep, "out": Path(out), "data": Path(data)}


@pytest.fixture(scope="module")
def sim_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("p8m_sim_out")
    data = tmp_path_factory.mktemp("p8m_sim_data")
    rep = P8M.run(live=False, transport=fake_transport, out_dir=Path(out),
                  data_dir=Path(data), verbose=False)
    return {"report": rep, "out": Path(out), "data": Path(data)}


# --------------------------------------------------------------------------- #
# Vocabulary / structure exactness.
# --------------------------------------------------------------------------- #
def test_twenty_mandatory_families():
    ids = P8M.MANDATORY_FAMILY_IDS
    assert len(ids) == 20
    assert len(set(ids)) == 20
    for expected in ("broad_earnings_surprise", "earnings_calendar", "analyst_estimates",
                     "analyst_recommendations", "analyst_price_targets", "ratings_or_grades",
                     "key_metrics", "ratios", "fundamentals_statements", "company_profile",
                     "transcripts_or_guidance", "news_or_press_releases", "insider_transactions",
                     "institutional_ownership_13f", "options_iv_skew_putcall",
                     "short_interest_borrow", "sec_filings_event_classification",
                     "macro_cross_asset_context", "sector_industry_context",
                     "liquidity_volume_volatility_positioning"):
        assert expected in ids


def test_nine_allowed_statuses():
    assert len(P8M.ALLOWED_STATUSES) == 9
    assert P8M.ST_PENDING_PROBE not in P8M.ALLOWED_STATUSES  # planning marker, not terminal


def test_seven_allowed_decisions():
    assert len(P8M.ALLOWED_DECISIONS) == 7
    assert P8M.DEC_DRY_RUN not in P8M.ALLOWED_DECISIONS


def test_six_critical_families_are_earnings_analyst():
    crit = [f["family"] for f in P8M.MANDATORY_FAMILIES if f["critical"]]
    assert crit == ["broad_earnings_surprise", "earnings_calendar", "analyst_estimates",
                    "analyst_recommendations", "analyst_price_targets", "ratings_or_grades"]


def test_bounded_limit_constants():
    assert P8M.MAX_TICKERS == 3
    assert P8M.MAX_ENDPOINT_FAMILIES == 12
    assert P8M.MAX_REQUESTS == 40


# --------------------------------------------------------------------------- #
# Controller fix: critical-first ordering + cap can never drop critical.
# --------------------------------------------------------------------------- #
def test_probe_endpoints_critical_first():
    eps = P8M.build_probe_endpoints()
    crit_ranks = [e["rank"] for e in eps if e["critical"]]
    noncrit_ranks = [e["rank"] for e in eps if not e["critical"]]
    assert max(crit_ranks) < min(noncrit_ranks)  # every critical ranked before any other


def test_earnings_analyst_before_fundamentals_in_default_queue():
    queue, _ = P8M.build_probe_queue()
    ids = [e["probe_id"] for e in queue]
    last_critical = max(i for i, e in enumerate(queue) if e["critical"])
    first_fundamental = min(i for i, e in enumerate(queue)
                            if e["probe_id"] in ("income_statement_quarterly",
                                                 "company_profile", "ratios_quarterly"))
    assert last_critical < first_fundamental
    # all six critical probe ids are present and at the front
    assert ids[:6] == ["earnings_surprises", "earnings_calendar", "analyst_estimates",
                       "analyst_recommendations", "analyst_price_targets",
                       "ratings_grades_consensus"]


def test_cap_cannot_drop_critical():
    # A generic cap well below the number of critical families must NOT drop any of them.
    for cap in (0, 1, 3, 5):
        queue, notes = P8M.build_probe_queue(max_endpoint_families=cap)
        crit = [e for e in queue if e["critical"]]
        assert len(crit) == 6, "cap=%d dropped a critical family" % cap
        assert any("OVERRIDING" in n for n in notes)


def test_cap_trims_only_non_critical_tail():
    queue, _ = P8M.build_probe_queue(max_endpoint_families=8)
    assert len([e for e in queue if e["critical"]]) == 6
    assert len([e for e in queue if not e["critical"]]) == 2  # 8 - 6


def test_request_budget_trims_non_critical_only():
    # Tight request budget: critical families survive; non-critical are trimmed.
    queue, notes = P8M.build_probe_queue(max_endpoint_families=12, n_tickers=3, max_requests=18)
    assert len([e for e in queue if e["critical"]]) == 6
    assert all(e["critical"] for e in queue)  # 6 * 3 == 18 fits; non-critical trimmed
    assert any("budget" in n.lower() for n in notes)


def test_default_queue_within_bounds():
    queue, _ = P8M.build_probe_queue()
    assert len(queue) <= P8M.MAX_ENDPOINT_FAMILIES
    assert len(queue) * P8M.MAX_TICKERS <= P8M.MAX_REQUESTS


# --------------------------------------------------------------------------- #
# Dry-run (default): no probe, no network, commit-safe artifacts.
# --------------------------------------------------------------------------- #
def test_dry_run_does_no_network(dry_run):
    rep = dry_run["report"]
    assert rep["mode"] == "dry_run"
    assert rep["probe_executed"] is False
    assert rep["requests_made"] == 0
    assert rep["network_used"] is False
    # the gitignored raw payload dir is never created by a dry run
    assert not (dry_run["data"] / "raw").exists()


def test_dry_run_decision_is_non_probe(dry_run):
    # Either blocked (no key in this shell) or the dry-run planning marker (key present).
    assert dry_run["report"]["decision"] in (P8M.DEC_BLOCKED_NO_KEY, P8M.DEC_DRY_RUN)


def test_all_seventeen_artifacts_written(dry_run):
    out = dry_run["out"]
    for name in P8M._ARTIFACTS.values():
        assert (out / name).is_file(), "missing artifact %s" % name
    assert len(P8M._ARTIFACTS) == 17


def test_inventory_has_all_twenty_families(dry_run):
    rows = _read_csv(dry_run["out"] / "market_data_family_inventory.csv")
    assert len(rows) == 20
    fams = {r["mandatory_family"] for r in rows}
    assert fams == set(P8M.MANDATORY_FAMILY_IDS)
    for r in rows:
        assert r["status"], "family %s has a blank status" % r["mandatory_family"]


def test_dry_non_probe_families_concrete(dry_run):
    # Local / free / not-available / no-mapping families are concrete even in dry-run.
    fs = dry_run["report"]["family_status"]
    assert fs["macro_cross_asset_context"] == P8M.ST_LOCAL_AVAILABLE
    assert fs["short_interest_borrow"] == P8M.ST_FREE_AVAILABLE
    assert fs["sec_filings_event_classification"] == P8M.ST_FREE_AVAILABLE
    assert fs["options_iv_skew_putcall"] == P8M.ST_NOT_AVAILABLE
    assert fs["transcripts_or_guidance"] == P8M.ST_NOT_TESTED_NO_MAPPING


# --------------------------------------------------------------------------- #
# Simulated live probe: concrete statuses, classification, decision.
# --------------------------------------------------------------------------- #
def test_sim_every_family_concrete_status(sim_run):
    rep = sim_run["report"]
    assert rep["probe_executed"] is True
    assert rep["every_family_has_concrete_status"] is True
    for fam, st in rep["family_status"].items():
        assert st in P8M.ALLOWED_STATUSES, "%s -> %s not a terminal status" % (fam, st)


def test_sim_classification_distinguishes_block_endpoint_ratelimit(sim_run):
    fs = sim_run["report"]["family_status"]
    assert fs["broad_earnings_surprise"] == P8M.ST_ACCESS_VERIFIED
    assert fs["analyst_estimates"] == P8M.ST_SUBSCRIPTION_BLOCK      # 403
    assert fs["ratios"] == P8M.ST_SUBSCRIPTION_BLOCK                 # 402
    assert fs["ratings_or_grades"] == P8M.ST_CLIENT_UPDATE           # 404
    assert fs["key_metrics"] == P8M.ST_RATE_LIMITED                  # 429
    assert fs["analyst_price_targets"] == P8M.ST_EMPTY_REACHABLE     # 200 empty


def test_sim_failure_diagnosis_distinct_causes(sim_run):
    rows = _read_csv(sim_run["out"] / "fmp_endpoint_failure_diagnosis.csv")
    causes = {r["likely_cause"] for r in rows}
    assert P8M.CAUSE_SUBSCRIPTION in causes
    assert P8M.CAUSE_WRONG_PATH in causes
    assert P8M.CAUSE_RATE_LIMIT in causes


def test_sim_decision_is_terminal_and_in_seven(sim_run):
    dec = sim_run["report"]["decision"]
    assert dec in P8M.ALLOWED_DECISIONS
    # subscription blocks + a client-update among criticals -> mixed strategy
    assert dec == P8M.DEC_MIXED_STRATEGY


def test_sim_requests_within_budget(sim_run):
    assert sim_run["report"]["requests_made"] <= P8M.MAX_REQUESTS


def test_critical_families_attempted_despite_cap(sim_run):
    rows = _read_csv(sim_run["out"] / "fmp_critical_endpoint_probe_results.csv")
    attempted = {r["mandatory_family"] for r in rows if r["attempted"] == "True"}
    for crit in ("broad_earnings_surprise", "earnings_calendar", "analyst_estimates",
                 "analyst_recommendations", "analyst_price_targets", "ratings_or_grades"):
        assert crit in attempted, "critical family %s was never attempted" % crit


def test_probe_results_have_required_fields(sim_run):
    rows = _read_csv(sim_run["out"] / "fmp_critical_endpoint_probe_results.csv")
    required = {"attempted", "provider", "endpoint_family", "attempted_url_redacted",
                "http_status", "response_type", "row_count", "result", "status",
                "error_type", "error_message_sanitized", "likely_cause", "next_action"}
    assert rows and required.issubset(set(rows[0].keys()))


def test_priority_queue_critical_first(sim_run):
    rows = _read_csv(sim_run["out"] / "missing_data_family_priority_queue.csv")
    crit_positions = [i for i, r in enumerate(rows) if r["critical"] == "True"]
    noncrit_positions = [i for i, r in enumerate(rows) if r["critical"] == "False"]
    if crit_positions and noncrit_positions:
        assert max(crit_positions) < min(noncrit_positions)


# --------------------------------------------------------------------------- #
# Provider comparison + signal-unlock map.
# --------------------------------------------------------------------------- #
def test_provider_alternative_matrix(dry_run):
    rows = _read_csv(dry_run["out"] / "provider_alternative_matrix.csv")
    assert len(rows) == 20
    cols = set(rows[0].keys())
    for c in ("fmp_can_access", "alpha_vantage", "finnhub", "eodhd", "free_source",
              "cheapest_viable_provider", "provider_unlocks_most_families"):
        assert c in cols


def test_cheapest_viable_provider_matrix(dry_run):
    rows = _read_csv(dry_run["out"] / "cheapest_viable_provider_matrix.csv")
    assert len(rows) == 20
    assert {"cheapest_viable_provider", "approx_monthly_cost_usd"}.issubset(set(rows[0].keys()))
    # FMP is the cheapest viable provider for the critical earnings/analyst families
    by_fam = {r["mandatory_family"]: r for r in rows}
    assert by_fam["broad_earnings_surprise"]["cheapest_viable_provider"] == "FMP"


def test_signal_unlock_map(dry_run):
    rows = _read_csv(dry_run["out"] / "data_family_to_signal_unlock_map.csv")
    sigs = {r["signal_unlocked"] for r in rows}
    assert "earnings_surprise x rates_sensitivity" in sigs
    assert "earnings_surprise x sector_leadership" in sigs
    assert "options_iv_skew x downside/volatility_sensitivity" in sigs
    assert "short_interest x liquidity/volatility/earnings_confirmation" in sigs


def test_recommended_first_provider_is_fmp(sim_run):
    assert "FMP" in sim_run["report"]["recommended_first_provider"]


# --------------------------------------------------------------------------- #
# Secret safety.
# --------------------------------------------------------------------------- #
def test_no_key_marker_in_any_artifact(sim_run, dry_run):
    marker = "api" + "key="  # assembled so this test file never contains it literally
    for run in (sim_run, dry_run):
        for p in run["out"].glob("*"):
            if p.is_file():
                text = p.read_text(encoding="utf-8", errors="replace").lower()
                assert marker not in text, "key marker leaked into %s" % p.name


def test_secret_audit_passes(dry_run):
    rows = _read_csv(dry_run["out"] / "secret_safety_audit.csv")
    by_check = {r["check"]: r for r in rows}
    assert by_check["no_key_in_output_files"]["passed"] == "True"
    assert by_check["api_key_logged"]["passed"] == "True"
    assert by_check["api_key_written_to_disk"]["passed"] == "True"
    assert dry_run["report"]["secret_safety_leak_scan_clean"] is True


def test_activation_commands_are_placeholder_only(dry_run):
    text = (dry_run["out"] / "provider_activation_commands.ps1").read_text(encoding="utf-8")
    assert "FMP_API_KEY" in text
    assert "<PASTE" in text  # placeholder, never a real key
    assert ("api" + "key=") not in text.lower()


def test_paid_data_dir_is_gitignored():
    gi = _REPO_ROOT / "research" / "data" / "fmp" / ".gitignore"
    assert gi.is_file()
    body = gi.read_text(encoding="utf-8")
    assert "raw/" in body and "normalized/" in body


# --------------------------------------------------------------------------- #
# No Paper Trader / GCP / orders / deployment; committed-safe.
# --------------------------------------------------------------------------- #
def test_no_paper_trader_gcp_or_order_logic(sim_run):
    rep = sim_run["report"]
    assert rep["paper_trader_touched"] is False
    assert rep["gcp_touched"] is False
    assert rep["deployed"] is False
    assert rep["orders_enabled"] is False
    assert rep["automation_enabled"] is False
    assert rep["broker_execution_enabled"] is False


def test_script_imports_no_forbidden_modules():
    src = (_REPO_ROOT / "research" / "run_phase8m_critical_market_data_family_audit.py").read_text(
        encoding="utf-8")
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("import ") or s.startswith("from "):
            low = s.lower()
            assert "api.app" not in low
            assert "google.cloud" not in low
            assert "alpha_vantage" not in low  # no AlphaVantage client import


def test_committed_safe_flags(sim_run):
    rep = sim_run["report"]
    assert rep["committed"] is False
    assert rep["data_fabricated"] is False
    assert rep["wrote_to_d_drive"] is False
    assert rep["preview_only"] is True
