"""Tests for Phase 8-L — Autonomous Data-Family Expansion, Provider Acquisition & Signal Factory.

Proves the brief's acceptance criteria:
  * all 14 mandatory data families present; no family has a vague status (7 statuses incl. ERROR);
  * provider decision matrix + priority ranking + bundle + activation order produced;
  * FMP ranked first for broad earnings + analyst revisions; FINRA attempted before paid short interest;
    GDELT attempted before paid news; provider acquisition commands produced (placeholder only);
  * tail-risk repair experiments are SCORED; provider-unlocked placeholder specs are SPEC-ONLY (no fakes);
  * trade-idea candidates carry whether_trade_ready + reason_not_trade_ready;
  * no Paper Trader / GCP / order / deployment logic; no secrets printed; committed-safe outputs only;
  * self-refilling waves: an empty bank with waves remaining does NOT stop the factory.

Real runs (module-scoped fixtures) execute against the persisted 8-E grid, so the suite is slower; it is
deselected from quick runs unless phase8/8l is requested.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _load():
    path = _REPO_ROOT / "research" / "run_phase8l_data_family_expansion_signal_factory.py"
    spec = importlib.util.spec_from_file_location("phase8l_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P8L = _load()

# The 31 grid columns the persisted 8-E panel exposes; every condition must use one of these.
LEGAL_COLS = {
    "drv_market_shock_z", "drv_oil_shock_z", "drv_rates_shock_z", "drv_credit_shock_z",
    "drv_usd_shock_z", "drv_commodity_shock_z", "drv_vix_spike_z",
    "cohort_high_beta", "cohort_low_beta", "cohort_oil_pos", "cohort_oil_neg", "cohort_rates_pos",
    "cohort_rates_neg", "cohort_credit_sens", "cohort_usd_pos", "cohort_usd_neg",
    "cohort_vol_spike_sens", "cohort_sector_lead", "cohort_surprise_sensitive",
    "cohort_filing_active", "tkr_surprise_sensitive",
    "rel_str_60", "sector_rel_str_60", "vol_compress",
    "earn_surprise_pos", "earn_surprise_neg", "earn_surprise_large", "earn_revision_proxy_up",
    "earn_recent_pos", "earn_event", "filing_event",
}


# --------------------------------------------------------------------------- #
# Module-scoped real runs (execute the factory against the persisted grid).
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def once_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("p8l_once_out")
    state = tmp_path_factory.mktemp("p8l_once_state")
    f = P8L.DataFamilyExpansionFactory(Path(out), Path(state))
    report = f.run(once=True)
    return {"report": report, "out": Path(out), "state": Path(state), "factory": f}


@pytest.fixture(scope="module")
def full_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("p8l_full_out")
    state = tmp_path_factory.mktemp("p8l_full_state")
    f = P8L.DataFamilyExpansionFactory(Path(out), Path(state))
    report = f.run()  # default: all 12 waves, self-refill, terminal WAVE_BUDGET_EXHAUSTED
    return {"report": report, "out": Path(out), "state": Path(state), "factory": f}


def _read_csv(path):
    import pandas as pd
    return pd.read_csv(path)


# --------------------------------------------------------------------------- #
# Vocabulary exactness.
# --------------------------------------------------------------------------- #
def test_twelve_waves_in_order():
    assert len(P8L.WAVES) == 12
    assert P8L.WAVES[0] == P8L.WAVE_PHASE8K_STATE_REBUILD
    assert P8L.WAVES[-1] == P8L.WAVE_TRADE_IDEA_PROMOTION
    assert len(set(P8L.WAVES)) == 12
    assert set(P8L.WAVE_META) == set(P8L.WAVES)


def test_fourteen_data_families():
    assert len(P8L.DATA_FAMILIES) == 14
    assert len(set(P8L.DATA_FAMILIES)) == 14
    for fam in P8L.DATA_FAMILIES:
        assert fam in P8L.FAMILY_SPECS
    # the 8-L-specific additions are present
    for fam in (P8L.F_GUIDANCE, P8L.F_OPTIONS, P8L.F_SHORT, P8L.F_INSIDER, P8L.F_13F):
        assert fam in P8L.DATA_FAMILIES


def test_seven_family_statuses_include_error():
    assert len(P8L.ALLOWED_FAMILY_STATUSES) == 7
    assert P8L.DF_ERROR in P8L.ALLOWED_FAMILY_STATUSES
    assert P8L.DF_FREE_INSUFFICIENT == "FREE_NO_KEY_SOURCE_ATTEMPTED_BUT_INSUFFICIENT"


def test_thirtyone_committed_safe_artifacts():
    assert len(P8L.ARTIFACTS) == 31
    assert len(set(P8L.ARTIFACTS)) == 31
    for required in ("missing_data_family_matrix.csv", "local_cache_discovery_report.csv",
                     "free_no_key_activation_report.csv", "provider_key_inventory.csv",
                     "provider_decision_matrix.csv", "provider_priority_ranking.csv",
                     "provider_bundle_recommendation.csv", "provider_activation_order.csv",
                     "provider_acquisition_commands.ps1", "data_family_unlocked_signal_specs.csv",
                     "tail_risk_repair_scoreboard.csv", "provider_expansion_required_scoreboard.csv",
                     "phase8m_next_plan.json"):
        assert required in P8L.ARTIFACTS


def test_eleven_provider_keys_checked_by_name():
    assert len(P8L.KEY_NAMES) == 11
    assert "FMP_API_KEY" in P8L.KEY_NAMES
    for k in P8L.KEY_NAMES:
        assert k.isupper() and k.endswith(("_KEY",))


def test_stop_conditions_reused_from_8k():
    assert P8L.ALLOWED_STOPS == P8L.P8K.ALLOWED_STOPS
    assert P8L.evaluate_factory_stop is P8L.P8K.evaluate_factory_stop


# --------------------------------------------------------------------------- #
# Hypothesis-bank legality.
# --------------------------------------------------------------------------- #
def test_scoring_waves_only_use_legal_columns():
    for wave in P8L.WAVES:
        for setup in P8L.generate_wave_bank(wave):
            for cond in setup.conditions:
                assert cond[0] in LEGAL_COLS, (wave, setup.setup_id, cond[0])


def test_global_setup_id_uniqueness():
    seen = set()
    for wave in P8L.WAVES:
        for setup in P8L.generate_wave_bank(wave):
            assert setup.setup_id not in seen, setup.setup_id
            seen.add(setup.setup_id)
    assert len(seen) == 24  # 3 scoring waves x 8 setups


def test_every_scoring_wave_has_at_least_30pct_challenges():
    scoring = 0
    for wave in P8L.WAVES:
        bank = P8L.generate_wave_bank(wave)
        if not bank:
            assert wave in P8L._NON_SCORING_WAVES
            continue
        scoring += 1
        assert wave in P8L._SCORING_WAVES
        chal = sum(1 for s in bank if s.is_challenge)
        assert chal / len(bank) >= 0.30, (wave, chal, len(bank))
    assert scoring == 3


def test_non_scoring_waves_emit_no_grid_setups():
    for wave in P8L._NON_SCORING_WAVES:
        assert P8L.generate_wave_bank(wave) == []


def test_anchor_leads_are_rebuilt():
    ids = {s.setup_id for s in P8L.generate_wave_bank(P8L.WAVE_PHASE8K_STATE_REBUILD)}
    assert "S8L-RATES-MACRO-20" in ids      # S8E-011 full-coverage anchor
    assert "S8L-RATES-EARNCONF-20" in ids   # F20 earnings-confirmed
    assert "S8L-RATES-MACRO-SECLEAD-20" in ids


def test_tail_repair_wave_uses_fixed_structural_filters():
    bank = P8L.generate_wave_bank(P8L.WAVE_TAIL_RISK_REPAIR_SIGNAL_TESTS)
    cols = {c[0] for s in bank for c in s.conditions}
    assert "cohort_low_beta" in cols   # extreme-beta exclusion
    assert "vol_compress" in cols      # top-volatility-quintile exclusion
    assert len(bank) == 8


# --------------------------------------------------------------------------- #
# Self-refill stop logic (the core "don't stop when the bank empties" behaviour).
# --------------------------------------------------------------------------- #
def _agg(confirmed=0, clean=0, prov_req=0, prov_lim=0):
    return {"n_confirmed": confirmed, "n_clean_promising": clean, "n_promising": clean,
            "n_provider_required": prov_req, "n_provider_limited": prov_lim}


def test_empty_bank_with_waves_remaining_does_not_stop():
    stop = P8L.evaluate_factory_stop(
        _agg(), all_waves_done=False, cycles_done=1, experiments_scored=8, max_cycles=None,
        max_experiments=None, time_exhausted=False, stop_file=False, stop_on_confirmed=False,
        stop_on_provider_only=False, provider_only=False, safety_ok=True)
    assert stop is None


def test_all_waves_done_terminates_on_wave_budget():
    stop = P8L.evaluate_factory_stop(
        _agg(), all_waves_done=True, cycles_done=3, experiments_scored=24, max_cycles=None,
        max_experiments=None, time_exhausted=False, stop_file=False, stop_on_confirmed=False,
        stop_on_provider_only=False, provider_only=False, safety_ok=True)
    assert stop == P8L.STOP_WAVE_BUDGET


def test_confirmed_and_stopfile_and_safety_precedence():
    assert P8L.evaluate_factory_stop(
        _agg(confirmed=1), all_waves_done=False, cycles_done=1, experiments_scored=8, max_cycles=None,
        max_experiments=None, time_exhausted=False, stop_file=False, stop_on_confirmed=False,
        stop_on_provider_only=False, provider_only=False, safety_ok=True) == P8L.STOP_CONFIRMED
    assert P8L.evaluate_factory_stop(
        _agg(), all_waves_done=True, cycles_done=1, experiments_scored=8, max_cycles=None,
        max_experiments=None, time_exhausted=False, stop_file=True, stop_on_confirmed=False,
        stop_on_provider_only=False, provider_only=False, safety_ok=True) == P8L.STOP_MANUAL
    assert P8L.evaluate_factory_stop(
        _agg(), all_waves_done=False, cycles_done=1, experiments_scored=8, max_cycles=None,
        max_experiments=None, time_exhausted=False, stop_file=False, stop_on_confirmed=False,
        stop_on_provider_only=False, provider_only=False, safety_ok=False) == P8L.STOP_SAFETY


# --------------------------------------------------------------------------- #
# Data-family matrix completeness + concreteness.
# --------------------------------------------------------------------------- #
def test_matrix_covers_all_families_with_concrete_status():
    rows = P8L.missing_data_family_matrix_rows({k: False for k in P8L.KEY_NAMES}, {})
    assert len(rows) == 14
    assert {r["data_family"] for r in rows} == set(P8L.DATA_FAMILIES)
    for r in rows:
        assert r["current_status"] in P8L.ALLOWED_FAMILY_STATUSES
        assert r["best_provider"]                          # never blank
        assert r["next_action"]                            # never blank
        assert r["hard_decision"]                          # never blank
        assert r["signals_unlocked"]
        # no vague provider requirement: paid-required families name cost + endpoint + env var
        if r["subscription_likely_required"]:
            assert r["required_env_var"] != "(none)"
            assert r["approximate_cost_if_known_or_unknown"]
            assert r["exact_endpoint_or_doc_reference_if_known"]


def test_present_key_promotes_family_to_existing_key_activated():
    readiness = {k: False for k in P8L.KEY_NAMES}
    readiness["FMP_API_KEY"] = True
    status, present, env = P8L._family_status(P8L.F_EARN, readiness)
    assert status == P8L.DF_KEY_ACTIVATED and present and env == "FMP_API_KEY"


def test_local_families_are_local_without_any_key():
    readiness = {k: False for k in P8L.KEY_NAMES}
    for fam in (P8L.F_MACRO, P8L.F_SECTOR, P8L.F_LIQ):
        status, present, _ = P8L._family_status(fam, readiness)
        assert status == P8L.DF_LOCAL and not present


# --------------------------------------------------------------------------- #
# Provider ranking + recommendation rules.
# --------------------------------------------------------------------------- #
def test_fmp_first_for_earnings_and_revisions():
    readiness = {k: False for k in P8L.KEY_NAMES}
    rows = P8L.provider_decision_matrix_rows(readiness)
    for fam in (P8L.F_EARN, P8L.F_REV):
        first = [r["provider"] for r in rows if r["data_family"] == fam and r["recommended_first"]]
        assert first == ["FMP"], (fam, first)


def test_finra_before_paid_short_and_gdelt_before_paid_news():
    readiness = {k: False for k in P8L.KEY_NAMES}
    rows = P8L.provider_decision_matrix_rows(readiness)
    short = [r["provider"] for r in sorted(
        [r for r in rows if r["data_family"] == P8L.F_SHORT], key=lambda r: r["rank_in_family"])]
    news = [r["provider"] for r in sorted(
        [r for r in rows if r["data_family"] == P8L.F_NEWS], key=lambda r: r["rank_in_family"])]
    assert short[0] == "FINRA" and "Finnhub" in short and short.index("FINRA") < short.index("Finnhub")
    assert news[0] == "GDELT"
    assert all(not P8L.PROVIDERS[p]["free"] for p in news[1:])  # every paid news source ranks after GDELT


def test_first_paid_subscription_is_fmp():
    rows = P8L.provider_priority_ranking_rows({k: False for k in P8L.KEY_NAMES})
    paid = sorted([r for r in rows if r["tier"] == "PAID"], key=lambda r: r["order_within_tier"])
    assert paid[0]["provider"] == "FMP" and paid[0]["attacks_top_blocker"]


def test_provider_recommendation_has_all_mandatory_fields():
    rec = P8L.provider_recommendation({k: False for k in P8L.KEY_NAMES})
    for field in ("recommended_first_provider", "recommended_first_provider_reason",
                  "recommended_provider_bundle", "do_not_buy_yet_list", "free_sources_to_exhaust_first",
                  "provider_activation_order", "exact_env_vars_needed"):
        assert field in rec and rec[field]
    assert rec["recommended_first_provider"] == "FMP"
    assert rec["exact_env_vars_needed"] == ["FMP_API_KEY"]
    # pure specialists that touch NO top blocker (earnings/revisions) are NOT bought first
    assert "Polygon" in rec["do_not_buy_yet_list"]        # options/news specialist
    assert "FMP" not in rec["do_not_buy_yet_list"]        # FMP is the recommended first paid


def test_bundle_lists_free_sources_before_first_paid_fmp():
    rows = P8L.provider_bundle_recommendation_rows({k: False for k in P8L.KEY_NAMES})
    fmp_order = next(r["bundle_order"] for r in rows if r["provider"] == "FMP")
    free_orders = [r["bundle_order"] for r in rows if r["tier"] == "FREE_EXHAUST_FIRST"]
    assert free_orders and max(free_orders) < fmp_order
    fmp_row = next(r for r in rows if r["provider"] == "FMP")
    assert fmp_row["tier"] == "FIRST_PAID_SUBSCRIPTION"


def test_activation_order_puts_free_before_fmp():
    rows = P8L.provider_activation_order_rows({k: False for k in P8L.KEY_NAMES})
    fmp_order = next(r["activation_order"] for r in rows if r["provider"] == "FMP")
    finra_order = next(r["activation_order"] for r in rows if r["provider"] == "FINRA")
    assert finra_order < fmp_order


def test_discovery_and_inventory_never_read_key_values():
    readiness = {k: bool(i % 2) for i, k in enumerate(P8L.KEY_NAMES)}
    for r in P8L.provider_discovery_rows(readiness):
        assert r["value_read"] is False
    for r in P8L.provider_key_inventory_rows(readiness):
        assert r["value_read"] is False
    assert len(P8L.provider_key_inventory_rows(readiness)) == 11


def test_acquisition_ps1_is_placeholder_only():
    ps1 = P8L.provider_acquisition_ps1({k: False for k in P8L.KEY_NAMES})
    assert "<your_key>" in ps1
    assert "FMP" in ps1
    # no real-looking secret assignment
    assert "sk-" not in ps1 and "=key_" not in ps1


# --------------------------------------------------------------------------- #
# Placeholder unlocked-signal specs (Part C) — specs only, never fake results.
# --------------------------------------------------------------------------- #
def test_unlocked_signal_specs_are_spec_only_and_name_provider():
    specs = P8L.data_family_unlocked_signal_specs_rows()
    assert len(specs) >= 6
    for s in specs:
        assert s["status"] == "SPEC_ONLY_DATA_REQUIRED"
        assert s["results_faked"] is False
        assert s["required_data_family"] and s["required_provider"]
        assert s["required_columns_not_yet_in_grid"]
    fams = {s["required_data_family"] for s in specs}
    assert {P8L.F_REV, P8L.F_NEWS, P8L.F_OPTIONS, P8L.F_SHORT}.issubset(fams)


# --------------------------------------------------------------------------- #
# Trade-idea registry.
# --------------------------------------------------------------------------- #
def test_trade_idea_rows_carry_trade_ready_reason():
    registry = [
        {"candidate_id": "S8L-X", "family": P8L.FAM_MACRO, "is_challenge": False,
         "alpha_promotion": P8L.ST_ALPHA_PROMISING, "provider_limited": False, "ev_after_25bps": 0.001,
         "n_events": 5000, "n_recent_events": 2000, "lift_vs_control": 0.004,
         "recent_lift_vs_control": 0.003, "worst_decile_mean": -0.15, "reason": "macro lead"},
    ]
    rows = P8L.trade_idea_candidate_rows(registry, {})
    assert rows and rows[0]["whether_trade_ready"] is False
    assert rows[0]["reason_not_trade_ready"]
    assert "reason_not_trade_ready" in P8L._TI_COLS and "whether_trade_ready" in P8L._TI_COLS


# --------------------------------------------------------------------------- #
# Source-level safety (no order/automation logic, no live-trading wiring).
# --------------------------------------------------------------------------- #
def test_no_order_or_automation_logic_in_source():
    src = (_REPO_ROOT / "research" / "run_phase8l_data_family_expansion_signal_factory.py").read_text(
        encoding="utf-8").lower()
    for banned in ("broker_api", "broker_client", "place_order(", "submit_order(", "execute_order(",
                   "orderclient", "alpaca", "ib_insync"):
        assert banned not in src, banned


# --------------------------------------------------------------------------- #
# Real --once run.
# --------------------------------------------------------------------------- #
def test_once_runs_a_single_cycle(once_run):
    rep = once_run["report"]
    assert rep["loop"]["cycles_completed"] == 1
    assert rep["loop"]["experiments_scored"] == 8
    assert rep["recommendation"] in P8L.ALLOWED_RECOMMENDATIONS


def test_once_emits_all_committed_safe_artifacts(once_run):
    out = once_run["out"]
    for name in P8L.ARTIFACTS:
        assert (out / name).exists(), name


def test_once_matrix_has_fourteen_concrete_families(once_run):
    mx = _read_csv(once_run["out"] / "missing_data_family_matrix.csv")
    assert len(mx) == 14
    assert set(mx["current_status"]).issubset(set(P8L.ALLOWED_FAMILY_STATUSES))
    assert mx["best_provider"].notna().all()


def test_once_persists_durable_state(once_run):
    state = once_run["state"]
    assert (state / P8L.STATE_FILES["factory_state"]).exists()
    assert (state / P8L.STATE_FILES["data_family_matrix"]).exists()


# --------------------------------------------------------------------------- #
# Real full campaign — self-refill across all 12 waves.
# --------------------------------------------------------------------------- #
def test_full_run_activates_all_twelve_waves(full_run):
    rep = full_run["report"]
    assert rep["waves"]["n_waves_activated"] == 12
    assert rep["stop_reason"] == P8L.STOP_WAVE_BUDGET
    # self-refill proven: more than one scoring wave was reached (>= 2 scoring cycles)
    assert rep["loop"]["cycles_completed"] >= 3
    assert rep["loop"]["experiments_scored"] == 24


def test_full_run_challenge_fraction_at_least_30pct(full_run):
    assert full_run["report"]["loop"]["challenge_fraction"] >= 0.30


def test_full_run_tail_repair_experiments_are_scored(full_run):
    ts = _read_csv(full_run["out"] / "tail_risk_repair_scoreboard.csv")
    assert "exp_id" in ts.columns and len(ts) >= 5
    assert ts["exp_id"].astype(str).str.startswith("S8L-").all()


def test_full_run_earnings_confirmed_candidate_scored(full_run):
    sb = _read_csv(full_run["out"] / "autonomous_signal_scoreboard.csv")
    ids = set(sb["exp_id"].astype(str))
    assert "S8L-RATES-EARNCONF-20" in ids        # F20 earnings-confirmed
    assert "S8L-EARN-VOLSENS-20" in ids          # highest-EV provider-limited earnings candidate


def test_full_run_anchor_reproduces_s8e011_coverage(full_run):
    sb = _read_csv(full_run["out"] / "autonomous_signal_scoreboard.csv")
    row = sb[sb["exp_id"] == "S8L-RATES-MACRO-20"]
    assert not row.empty and int(row.iloc[0]["n_events"]) > 10000  # full-coverage macro anchor


def test_full_run_provider_required_signals_present(full_run):
    pe = _read_csv(full_run["out"] / "provider_expansion_required_scoreboard.csv")
    assert "provider_to_unlock" in pe.columns and len(pe) >= 1
    assert pe["env_var"].notna().all()


def test_full_run_trade_ideas_have_reason_not_trade_ready(full_run):
    ti = _read_csv(full_run["out"] / "trade_idea_candidate_registry.csv")
    assert {"whether_trade_ready", "reason_not_trade_ready"}.issubset(ti.columns)
    assert len(ti) >= 1


def test_full_run_safety_block_clean(full_run):
    safety = full_run["report"]["safety"]
    for flag in ("broker_or_orders", "automation_of_orders", "paper_trader_touched", "gcp_touched",
                 "deployment", "committed", "pushed", "external_data_faked", "secrets_printed",
                 "optimized_weights", "factor_signs_modified_after_results"):
        assert safety[flag] is False
    assert safety["research_only"] is True
    assert safety["unlocked_signal_specs_are_spec_only"] is True


def test_full_run_unlocked_specs_have_no_fake_results(full_run):
    sp = _read_csv(full_run["out"] / "data_family_unlocked_signal_specs.csv")
    assert (sp["status"] == "SPEC_ONLY_DATA_REQUIRED").all()
    assert (~sp["results_faked"].astype(bool)).all()


# --------------------------------------------------------------------------- #
# Dry-run + resume + manual stop file.
# --------------------------------------------------------------------------- #
def test_dry_run_does_not_persist_state(tmp_path):
    out = tmp_path / "out"
    state = tmp_path / "state"
    f = P8L.DataFamilyExpansionFactory(out, state, dry_run=True)
    f.run(once=True)
    assert not (state / P8L.STATE_FILES["factory_state"]).exists()
    assert (out / "phase8l_data_family_expansion_signal_factory.json").exists()


def test_resume_continues_from_durable_state(tmp_path):
    out = tmp_path / "out"
    state = tmp_path / "state"
    P8L.DataFamilyExpansionFactory(out, state).run(once=True)
    f2 = P8L.DataFamilyExpansionFactory(out, state)
    rep2 = f2.run(once=True, resume=True)
    assert rep2["loop"]["cycles_completed"] == 2
    assert rep2["loop"]["experiments_scored"] >= 16


def test_manual_stop_file_halts_before_next_cycle(tmp_path):
    out = tmp_path / "out"
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / P8L.STOP_FILE_NAME).write_text("stop", encoding="utf-8")
    rep = P8L.DataFamilyExpansionFactory(out, state).run()
    assert rep["stop_reason"] == P8L.STOP_MANUAL
    assert rep["loop"]["cycles_completed"] == 0
