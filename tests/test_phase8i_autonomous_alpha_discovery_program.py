"""Tests for Phase 8-I — Autonomous Alpha Discovery Program.

Verifies the program contract WITHOUT re-deriving any score: exact decision/alpha vocabularies, the
24 committed-safe artifacts, that every new combination candidate uses ONLY columns that exist in the
persisted grid (no invented features), the alpha-promotion mapping + provider-limited logic, all
recommendation branches, the no-key FINRA/news activation honesty, ranked next options, and an
offline end-to-end run that emits all artifacts and never confirms a signal on the current data.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, _REPO_ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


H = _load("phase8i_under_test", "research/run_phase8i_autonomous_alpha_discovery_program.py")
G = H.P8G
F = H.P8F
P = H.P8E


# --------------------------------------------------------------------------- #
# Helpers.
# --------------------------------------------------------------------------- #
def _exp(exp_id, promotion, *, family=H.FAM_EARNINGS, n_events=1500, needs_provider=False,
         is_challenge=False, real=True):
    return G.Experiment(
        exp_id=exp_id, cycle=3, family=family, agent=H.EARN_A, driver="d", cohort="c",
        is_challenge=is_challenge, real_external_data=real, needs_provider=needs_provider,
        hypothesis="h", status="", promotion=promotion, reason="",
        metrics={"n_events": n_events, "ev_after_25bps": 0.002, "lift_vs_control": 0.003})


# --------------------------------------------------------------------------- #
# Vocabularies + artifact contract.
# --------------------------------------------------------------------------- #
def test_recommendation_vocabulary_exact_and_ordered():
    assert H.ALLOWED_RECOMMENDATIONS == (
        "CONFIRMED_ALPHA_SIGNAL_FOUND", "PROMISING_ALPHA_SIGNAL_FOUND",
        "PROMISING_BUT_PROVIDER_LIMITED", "PROVIDER_REQUIRED_FOR_NEXT_BREAKTHROUGH",
        "EXTERNAL_DATA_EXPANSION_REQUIRED", "ALPHA_RESEARCH_REJECTED_ON_AVAILABLE_DATA",
        "ASSESSMENT_FRAMEWORK_BLOCKED", "ERROR")


def test_alpha_status_vocabulary():
    assert H.ST_ALPHA_CONFIRMED == "CONFIRMED_ALPHA_SIGNAL"
    assert H.ST_ALPHA_PROMISING == "PROMISING_ALPHA_SIGNAL"
    assert H.ST_ALPHA_PROVIDER_REQUIRED == "PROVIDER_REQUIRED"
    for s in (H.ST_ALPHA_CONFIRMED, H.ST_ALPHA_PROMISING, H.ST_ALPHA_PROVIDER_REQUIRED,
              H.ST_REJECTED, H.ST_BLOCKED):
        assert s in H.ALLOWED_ALPHA_STATUSES


def test_artifact_list_matches_brief():
    expected = {
        "phase8i_autonomous_alpha_discovery_program.json", "autonomous_research_memory.json",
        "hypothesis_backlog.csv", "data_source_activation_log.csv", "provider_key_inventory.csv",
        "local_no_key_source_results.csv", "normalized_event_panel_manifest.csv",
        "candidate_signal_registry.csv", "experiment_pre_registration.csv",
        "alpha_signal_scoreboard.csv", "matched_control_report.csv",
        "walk_forward_validation_report.csv", "recent_period_validation_report.csv",
        "tail_risk_report.csv", "concentration_report.csv", "placebo_leakage_report.csv",
        "multiple_testing_report.csv", "confirmed_alpha_signals.csv", "promising_alpha_signals.csv",
        "provider_required_signals.csv", "rejected_alpha_signals.csv",
        "model_candidate_registry_update.csv", "research_director_decision.json",
        "phase8j_next_plan.json"}
    assert set(H.ARTIFACTS) == expected
    assert len(H.ARTIFACTS) == 24


def test_agents_roster_present():
    for a in (H.DIR_A, H.SENS_A, H.VAL_A, H.RSK_A, H.MODEL_A, H.EARN_A, H.REV_A, H.EXT_A):
        assert a in H.ALL_AGENTS


# --------------------------------------------------------------------------- #
# New combination candidates: real columns only, fixed templates, >=30% challenges.
# --------------------------------------------------------------------------- #
def test_new_candidates_use_only_existing_grid_columns():
    panel = F.load_persisted_panel()
    if panel is None or panel.grid.empty:
        pytest.skip("persisted 8-E panel unavailable")
    # the grid AFTER 8-G augmentation adds the earnings/filing event columns the combos reference.
    grid, _ = G.augment_grid(panel.grid, G.load_earnings_events(),
                             pd.DataFrame(columns=["ticker", "availability_date", "form"]))
    cols = set(grid.columns)
    for s in H.plan_new_candidate_signals():
        for (col, _op, _val) in s.conditions:
            assert col in cols, f"{s.setup_id} references missing column {col}"


def test_new_candidate_family_has_enough_challenges():
    setups = H.plan_new_candidate_signals()
    testable = [s for s in setups if not s.is_challenge]
    challenges = [s for s in setups if s.is_challenge]
    assert len(challenges) / max(len(testable), 1) >= 0.30
    # at least one placebo and one wrong-sign challenge
    assert any(s.placebo for s in challenges)
    assert any("NEGATIVE" in s.hypothesis.upper() for s in challenges)


def test_new_candidates_cover_required_families():
    fams = {s.family for s in H.plan_new_candidate_signals() if not s.is_challenge}
    assert H.FAM_EARNINGS in fams          # earnings catalyst x context
    assert H.FAM_REVISION in fams          # revision proxy x sensitivity
    assert H.FAM_S8E011_EXT in fams        # macro/cross-asset x sensitivity + confirmation


# --------------------------------------------------------------------------- #
# Alpha promotion mapping + provider-limited logic.
# --------------------------------------------------------------------------- #
def test_alpha_promotion_mapping():
    assert H._alpha_promotion(_exp("a", H.ST_EXT_CONFIRMED)) == H.ST_ALPHA_CONFIRMED
    assert H._alpha_promotion(_exp("b", H.ST_EXT_PROMISING)) == H.ST_ALPHA_PROMISING
    assert H._alpha_promotion(_exp("c", H.ST_NEEDS_HISTORY)) == H.ST_ALPHA_PROMISING
    assert H._alpha_promotion(_exp("d", H.ST_NEEDS_PROVIDER, needs_provider=True)) == H.ST_ALPHA_PROVIDER_REQUIRED
    assert H._alpha_promotion(_exp("e", H.ST_REJECTED)) == H.ST_REJECTED


def test_revision_proxy_never_confirmed():
    # a proxy/thin family can be PROMISING at most, never CONFIRMED (capped in 8-G promotion ladder).
    assert H.FAM_REVISION in H.PROXY_OR_THIN_FAMILIES
    e = _exp("rev", H.ST_NEEDS_HISTORY, family=H.FAM_REVISION)
    assert H._alpha_promotion(e) != H.ST_ALPHA_CONFIRMED


def test_provider_limited_logic():
    assert H._provider_limited(_exp("x", H.ST_NEEDS_HISTORY)) is True
    assert H._provider_limited(_exp("y", H.ST_EXT_PROMISING, n_events=500)) is True   # under count gate
    assert H._provider_limited(_exp("z", H.ST_EXT_PROMISING, n_events=2000)) is False  # full coverage


# --------------------------------------------------------------------------- #
# Recommendation branches (synthetic ledgers).
# --------------------------------------------------------------------------- #
def test_recommendation_framework_blocked():
    rec, _ = H.derive_recommendation(False, [], {})
    assert rec == H.REC_FRAMEWORK_BLOCKED


def test_recommendation_confirmed():
    rec, _ = H.derive_recommendation(True, [_exp("c", H.ST_EXT_CONFIRMED)], {})
    assert rec == H.REC_CONFIRMED


def test_recommendation_clean_promising():
    rec, _ = H.derive_recommendation(True, [_exp("p", H.ST_EXT_PROMISING, n_events=3000)], {})
    assert rec == H.REC_PROMISING


def test_recommendation_provider_limited():
    # only promising lead is under the count gate -> provider/coverage-limited
    rec, _ = H.derive_recommendation(True, [_exp("p", H.ST_EXT_PROMISING, n_events=500)], {})
    assert rec == H.REC_PROVIDER_LIMITED


def test_recommendation_provider_required():
    rec, _ = H.derive_recommendation(
        True, [_exp("n", H.ST_NEEDS_PROVIDER, needs_provider=True, n_events=0)], {})
    assert rec == H.REC_PROVIDER_REQUIRED


def test_recommendation_rejected():
    rec, _ = H.derive_recommendation(True, [_exp("r", H.ST_REJECTED, n_events=2000)], {})
    assert rec == H.REC_REJECTED


# --------------------------------------------------------------------------- #
# Activation honesty (no-key sources never fake events).
# --------------------------------------------------------------------------- #
def test_finra_offline_executable_no_fake():
    rows, meta = H.finra_short_interest_activation(activate_live=False)
    assert meta["attempted"] is False
    assert all(not r["real_usable_panel"] for r in rows)   # never a real usable panel without bulk/key


def test_no_key_source_results_structure():
    panel_rows = H.data_source_activation_log(
        pd.DataFrame({"ticker": ["A"]}), pd.DataFrame(), {"n_fetched": 0, "n_from_cache": 0},
        {"http_status": "200", "succeeded": True}, {"succeeded": True, "note": ""},
        {}, {"n_earn_event_obs": 1})
    res = H.local_no_key_source_results(panel_rows)
    assert any(r["source"] == "gdelt_news_nokey" for r in res)
    # news connector live but no history -> not real events
    news = next(r for r in res if r["source"] == "gdelt_news_nokey")
    assert news["produced_real_events"] is False


# --------------------------------------------------------------------------- #
# Ranked options + backlog.
# --------------------------------------------------------------------------- #
def test_ranked_next_options_sorted_and_nonempty():
    opts = H.ranked_next_options([_exp("p", H.ST_EXT_PROMISING, family=H.FAM_MACRO, n_events=3000)],
                                 {}, {})
    assert len(opts) >= 3
    probs = [o["prob_success"] for o in opts]
    assert probs == sorted(probs, reverse=True)
    assert opts[0]["rank"] == 1


def test_state_rebuild_returns_contract_keys():
    state = H.rebuild_research_state()
    for k in ("ranked_existing_leads", "coverage_or_provider_blocked", "invalid_logic_rejected",
              "rejected_research_lines"):
        assert k in state


# --------------------------------------------------------------------------- #
# Safety block.
# --------------------------------------------------------------------------- #
def test_safety_block_all_forbidden_false():
    sb = H._safety_block({}, {"n_fetched": 0}, {"succeeded": False}, {"succeeded": False})
    for k in ("external_data_faked", "news_sentiment_faked", "short_interest_faked",
              "thresholds_modified_after_results", "factor_signs_modified_after_results",
              "optimized_weights", "regime_activation", "ml_fit", "packages_installed",
              "live_trading_signals", "broker_or_orders", "automation", "paper_trader_touched",
              "gcp_touched", "committed", "pushed", "failed_experiments_hidden"):
        assert sb[k] is False
    assert sb["thresholds_fixed_a_priori"] is True
    assert sb["point_in_time_join"] is True
    assert sb["revision_is_labelled_proxy_not_confirmed"] is True


# --------------------------------------------------------------------------- #
# End-to-end (offline; reuses cached EDGAR — no network required).
# --------------------------------------------------------------------------- #
def test_end_to_end_emits_all_artifacts(tmp_path):
    panel = F.load_persisted_panel()
    if panel is None or panel.grid.empty:
        pytest.skip("persisted 8-E panel unavailable")
    report = H.run(tmp_path, activate_live=False)
    assert report["recommendation"] in H.ALLOWED_RECOMMENDATIONS
    for name in H.ARTIFACTS:
        assert (tmp_path / name).exists(), f"missing artifact {name}"
    # pre-registration precedes scoring; >=30% challenges; budget within bounds.
    assert report["budget"]["challenge_ok"] is True
    assert report["budget"]["all_pre_registered"] is True
    # honest: no CONFIRMED alpha signal on the current local/no-key data.
    assert report["candidates"]["confirmed"] == []
    assert report["safety"]["committed"] is False and report["safety"]["pushed"] is False


def test_end_to_end_generates_new_combination_candidates(tmp_path):
    panel = F.load_persisted_panel()
    if panel is None or panel.grid.empty:
        pytest.skip("persisted 8-E panel unavailable")
    report = H.run(tmp_path, activate_live=False)
    # the program must NOT just re-test S8G-F20; it generates many new combination candidates.
    assert report["candidates"]["n_new_combination_candidates"] >= 10
    assert report["candidates"]["n_total"] >= 30
    reg = pd.read_csv(tmp_path / "candidate_signal_registry.csv")
    assert (reg["candidate_id"] == "S8I-C-SECLEAD-20").any()   # a new earnings x sector-leadership combo
