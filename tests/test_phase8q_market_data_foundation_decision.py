"""Tests for Phase 8-Q - Market Data Foundation Decision Gate.

Fully offline. The runner makes NO network calls and reads only the committed-safe 8-N/8-O/8-P
artifacts; these tests point it at the real input dirs (read-only) and write outputs to tmp_path.
No API key is ever set, read, printed, or written.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

import research.run_phase8q_market_data_foundation_decision as q

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNNER = _REPO_ROOT / "research" / "run_phase8q_market_data_foundation_decision.py"


def _run(tmp_path: Path) -> dict:
    return q.run(out_dir=tmp_path, verbose=False)


def _rows(path: Path):
    with open(path, "r", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _read_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# Core behavior.
# --------------------------------------------------------------------------- #
def test_all_fourteen_artifacts_written(tmp_path):
    report = _run(tmp_path)
    for key, name in q._ARTIFACTS.items():
        assert (tmp_path / name).is_file(), "missing artifact %s (%s)" % (key, name)
    assert report["phase"] == "8-Q"
    assert report["decision"] in q.ALLOWED_DECISIONS


def test_is_decision_gate_no_network(tmp_path):
    report = _run(tmp_path)
    assert report["is_decision_gate_not_backfill"] is True
    assert report["network_used"] is False


def test_prior_phase_artifacts_are_read(tmp_path):
    """8-N, 8-O, and 8-P committed-safe artifacts must be read into the evidence."""
    report = _run(tmp_path)
    ev = report["evidence"]
    assert ev["phase8n_read"] is True
    assert ev["phase8o_read"] is True
    assert ev["phase8p_read"] is True
    # 8-P concrete numbers flow through.
    assert ev["av_combined_count"] >= 9
    assert ev["av_rate_limited"] is True


def test_20_is_minimum_gate_not_target_universe(tmp_path):
    report = _run(tmp_path)
    assert report["min_scoring_gate"] == 20
    assert report["min_scoring_gate_is_target_universe"] is False
    rows = _rows(tmp_path / q._ARTIFACTS["universe_targets"])
    gate = [r for r in rows if r["target"] == "minimum_scoring_gate"]
    assert gate and gate[0]["is_final_target"] == "False"


def test_target_universe_includes_current_and_sp500_broad(tmp_path):
    report = _run(tmp_path)
    joined = " ".join(report["target_universes"]).lower()
    assert "current" in joined
    assert "s&p 500" in joined or "sp 500" in joined or "500" in joined
    rows = _rows(tmp_path / q._ARTIFACTS["universe_targets"])
    targets = {r["target"] for r in rows}
    assert "current_working_universe" in targets
    assert "broad_research_universe" in targets
    broad = [r for r in rows if r["target"] == "broad_research_universe"][0]
    assert broad["is_final_target"] == "True"
    assert "500" in broad["size_estimate"]


def test_fmp_current_plan_classified_insufficient(tmp_path):
    report = _run(tmp_path)
    assert report["fmp_current_plan_insufficient"] is True
    rows = _rows(tmp_path / q._ARTIFACTS["current_coverage"])
    fmp = [r for r in rows if r["provider"] == "FMP (current plan)"][0]
    assert fmp["verdict"] == "INSUFFICIENT"


def test_alphavantage_helpful_but_not_proven_broad(tmp_path):
    report = _run(tmp_path)
    assert report["alphavantage_helpful_but_unproven_for_broad"] is True
    assert report["free_stack_viable_for_broad_earnings"] is False
    rows = _rows(tmp_path / q._ARTIFACTS["current_coverage"])
    av = [r for r in rows if r["provider"].startswith("Alpha Vantage")][0]
    assert av["verdict"] == "HELPS_BUT_NOT_BROAD"
    assert av["broad_coverage_proven"].startswith("no")


def test_fred_classified_macro_only(tmp_path):
    report = _run(tmp_path)
    assert report["fred_classified"] == "macro_only"
    rows = _rows(tmp_path / q._ARTIFACTS["requirement_catalog"])
    fred = [r for r in rows if "FRED macro" in r["family"]][0]
    assert fred["tier"] == "0"
    assert "macro only" in fred["rationale"].lower()


def test_simfin_classified_fundamentals_delayed_not_earnings(tmp_path):
    report = _run(tmp_path)
    assert report["simfin_classified"] == "fundamentals_delayed_not_earnings_or_analyst"
    rows = _rows(tmp_path / q._ARTIFACTS["paid_matrix"])
    simfin = [r for r in rows if r["provider"] == "SimFin"][0]
    assert simfin["point_in_time"] == "no (delayed)"
    assert "delayed" in simfin["note"].lower()


def test_market_data_families_are_tiered(tmp_path):
    _run(tmp_path)
    rows = _rows(tmp_path / q._ARTIFACTS["priority_tiers"])
    tiers = {r["tier"] for r in rows}
    # All four tiers (0..3) must be represented.
    assert {"0", "1", "2", "3"}.issubset(tiers)
    # Every family carries a tier label.
    assert all(r["tier_label"] for r in rows)


def test_expensive_families_can_be_deferred(tmp_path):
    report = _run(tmp_path)
    rows = _rows(tmp_path / q._ARTIFACTS["deferred_families"])
    families = {r["family"] for r in rows}
    # At least the canonical expensive alt-data families are deferred.
    for fam in ("earnings transcripts", "options implied volatility / skew", "news sentiment"):
        assert fam in families
    assert all(r["defer_until"] for r in rows)
    assert report["deferrable_data_families"]


def test_fmp_ultimate_not_recommended_blindly(tmp_path):
    report = _run(tmp_path)
    assert report["fmp_ultimate_rejected"] is True
    assert report["fmp_upgrade_justified"] is False
    rows = _rows(tmp_path / q._ARTIFACTS["paid_matrix"])
    ult = [r for r in rows if "Ultimate" in r["provider"]][0]
    assert "REJECTED" in ult["recommended"]


def test_output_includes_vendor_evaluation_criteria(tmp_path):
    report = _run(tmp_path)
    rows = _rows(tmp_path / q._ARTIFACTS["vendor_criteria"])
    criteria = {r["criterion"] for r in rows}
    for required in ("coverage_100_500_tickers", "history_depth", "point_in_time_dates",
                     "api_limits", "cost", "license_terms", "bulk_download_support"):
        assert required in criteria
    assert report["vendor_evaluation_criteria"]


def test_output_includes_procurement_questions(tmp_path):
    report = _run(tmp_path)
    rows = _rows(tmp_path / q._ARTIFACTS["procurement_questions"])
    assert len(rows) >= 5
    assert all(r["question"] and r["why_it_matters"] for r in rows)
    assert report["procurement_question_count"] == len(rows)
    blob = " ".join(r["question"].lower() for r in rows)
    assert "point-in-time" in blob or "point in time" in blob


def test_buy_vs_free_answers_the_explicit_questions(tmp_path):
    _run(tmp_path)
    rows = _rows(tmp_path / q._ARTIFACTS["buy_vs_free"])
    blob = " ".join(r["question"].lower() for r in rows)
    assert "stop trying free sources for earnings" in blob
    assert "fmp upgrade" in blob
    assert "fmp ultimate" in blob
    assert "cheaper earnings/fundamentals provider" in blob
    assert "sentiment/transcripts/options" in blob
    ultimate = [r for r in rows if "ultimate" in r["question"].lower()][0]
    assert ultimate["answer"] == "NO_REJECTED"


def test_decision_is_mixed_or_buy(tmp_path):
    report = _run(tmp_path)
    # Evidence (free works for macro/price/id, but broad earnings needs a buy) -> mixed core stack.
    assert report["decision"] in (q.DEC_MIXED, q.DEC_BUY_EARNINGS)
    assert report["paid_provider_recommended"] is True
    assert report["provider_category_to_evaluate_first"].lower().startswith("earnings")


def test_free_apis_still_worth_using_for_macro(tmp_path):
    report = _run(tmp_path)
    assert report["free_apis_still_worth_using"] is True
    assert report["free_stack_viable_for_macro_price_identity"] is True
    worth = " ".join(report["free_apis_worth_using_for"]).lower()
    assert "fred" in worth and "ohlcv" in worth


def test_provider_key_inventory_presence_only(tmp_path):
    _run(tmp_path)
    rows = _rows(tmp_path / q._ARTIFACTS["key_inventory"])
    env_vars = {r["env_var"] for r in rows}
    for ev in ("FRED_API_KEY", "ALPHAVANTAGE_API_KEY", "SIMFIN_API_KEY", "FMP_API_KEY",
               "FINNHUB_API_KEY", "EODHD_API_KEY"):
        assert ev in env_vars
    # value_read must be False for every provider (presence-only).
    assert all(r["value_read"] == "False" for r in rows)


def test_key_never_read_even_when_present(tmp_path, monkeypatch):
    """Even if a key is in the env, the value must never appear in any artifact."""
    secret = "SECRETKEYVALUE_DO_NOT_LEAK_1234567890"
    for ev in ("FRED_API_KEY", "ALPHAVANTAGE_API_KEY", "SIMFIN_API_KEY", "FMP_API_KEY",
               "FINNHUB_API_KEY", "EODHD_API_KEY"):
        monkeypatch.setenv(ev, secret)
    report = _run(tmp_path)
    assert report["secret_safety_leak_scan_clean"] is True
    for name in q._ARTIFACTS.values():
        text = (tmp_path / name).read_text(encoding="utf-8")
        assert secret not in text
        assert "apikey=" not in text.lower()
    # key_present booleans reflect the env, but value_read stays False.
    rows = _rows(tmp_path / q._ARTIFACTS["key_inventory"])
    assert all(r["key_present"] == "True" for r in rows)
    assert all(r["value_read"] == "False" for r in rows)


def test_no_raw_paid_data_in_artifacts(tmp_path):
    report = _run(tmp_path)
    assert report["raw_paid_data_in_artifacts"] is False
    # Raw earnings payload tokens must not appear in any committed artifact.
    forbidden = ("quarterlyEarnings", "reportedEPS", "estimatedEPS", "annualEarnings")
    for name in q._ARTIFACTS.values():
        text = (tmp_path / name).read_text(encoding="utf-8")
        for tok in forbidden:
            assert tok not in text, "raw payload token %r leaked into %s" % (tok, name)


def test_safety_flags_present(tmp_path):
    report = _run(tmp_path)
    assert report["preview_only"] is True
    assert report["orders_enabled"] is False
    assert report["automation_enabled"] is False
    assert report["broker_execution_enabled"] is False
    assert report["paper_trader_touched"] is False
    assert report["gcp_touched"] is False
    assert report["deployed"] is False
    assert report["data_fabricated"] is False
    assert report["committed"] is False


def test_recommended_stack_marks_mandatory_and_deferred(tmp_path):
    _run(tmp_path)
    rows = _rows(tmp_path / q._ARTIFACTS["recommended_stack"])
    statuses = {r["status"] for r in rows}
    assert "MANDATORY_NOW" in statuses
    assert "DEFERRED" in statuses
    # Tier-0 earnings recommends a paid buy.
    earn = [r for r in rows if "earnings actual" in r["family"]][0]
    assert earn["buy_or_free"] == "paid"


def test_paid_matrix_test_first_order_eodhd_before_fmp(tmp_path):
    _run(tmp_path)
    rows = _rows(tmp_path / q._ARTIFACTS["paid_matrix"])
    order = {r["provider"]: int(r["test_order"]) for r in rows}
    eodhd = order["EODHD"]
    fmp_prem = order["FMP (upgrade tier / Premium)"]
    av = order["Alpha Vantage"]
    # Current free AV evidence first, then EODHD before FMP Premium, Ultimate last.
    assert av < eodhd < fmp_prem
    assert order["FMP (Ultimate tier)"] == 99


def test_next_plan_targets_broad_bundle_evaluation(tmp_path):
    _run(tmp_path)
    plan = _read_json(tmp_path / q._ARTIFACTS["next_plan"])
    assert plan["phase"] == "8-R"
    assert plan["test_first_provider"] == "EODHD"
    assert plan["fmp_ultimate_rejected"] is True
    assert plan["do_not_run_more_free_micro_hunts_for_broad_earnings"] is True
    assert plan["committed"] is False


def test_decision_vocabulary_is_exactly_the_brief(tmp_path):
    report = _run(tmp_path)
    assert set(report["allowed_decisions"]) == set(q.ALLOWED_DECISIONS)
    assert q.DEC_FREE_VIABLE == "FREE_STACK_STILL_VIABLE"
    assert q.DEC_FREE_NOT_VIABLE == "FREE_STACK_NOT_VIABLE_FOR_CORE_RESEARCH"
    assert q.DEC_BUY_EARNINGS == "BUY_EARNINGS_FUNDAMENTALS_PROVIDER"
    assert q.DEC_MIXED == "MIXED_FREE_PLUS_PAID_CORE_STACK"
    assert q.DEC_DEFER_ALT == "DEFER_EXPENSIVE_ALT_DATA"
    assert q.DEC_NEEDS_QUOTES == "NEEDS_VENDOR_QUOTES"
    assert q.DEC_ERROR == "ERROR"


def test_source_has_no_forbidden_order_or_infra_logic():
    """No Paper Trader / GCP / order / deployment / automation logic in the runner source."""
    src = _RUNNER.read_text(encoding="utf-8").lower()
    for tok in ("create_order", "place_order", "submit_order", "execute_order",
                "import paper", "from api.app", "gcloud ", "subprocess.", "urllib.request",
                "requests.get", "requests.post"):
        assert tok not in src, "forbidden token %r in runner source" % tok


def test_all_families_classified_in_catalog(tmp_path):
    """All 15 data families from the brief are present in the catalog."""
    _run(tmp_path)
    rows = _rows(tmp_path / q._ARTIFACTS["requirement_catalog"])
    families = " ".join(r["family"].lower() for r in rows)
    for needle in ("adjusted ohlcv", "sector", "fred macro", "earnings actual",
                   "analyst estimates", "analyst recommendations", "price target",
                   "fundamentals statements", "ratios", "news sentiment", "transcripts",
                   "options implied volatility", "short interest", "insider", "13f"):
        assert needle in families, "family %r missing from catalog" % needle
