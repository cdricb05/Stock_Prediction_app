"""Tests for Phase 8-J — Autonomous Alpha Research Daemon.

Fast unit tests over the pure daemon logic (bank legality, vocab exactness, next-action / stop
evaluation, provider blockers, agent boards) plus a small number of REAL bounded daemon runs
(--once / --resume / --dry-run / stop-file) on the persisted 8-E grid that assert the durable-state
contract, queue generation, classification validity, and committed-safe outputs.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

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


M = _load("phase8j_under_test", "research/run_phase8j_autonomous_alpha_research_daemon.py")

# Columns that exist in the persisted 8-E grid AFTER 8-G augmentation. Every hypothesis condition
# must reference one of these — no invented features.
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
# Real bounded daemon runs (module-scoped: score the grid once, assert widely).
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def once_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("out8j")
    state = tmp_path_factory.mktemp("state8j")
    daemon = M.AlphaResearchDaemon(out, state)
    report = daemon.run(once=True)
    return {"out": Path(out), "state": Path(state), "report": report, "daemon": daemon}


# --------------------------------------------------------------------------- #
# Vocabulary exactness.
# --------------------------------------------------------------------------- #
def test_stop_conditions_exact():
    assert set(M.ALLOWED_STOPS) == {
        "CONFIRMED_ALPHA_SIGNAL_FOUND", "HARD_PROVIDER_BLOCKER", "SAFETY_OR_LEAKAGE_BLOCKER",
        "EXPERIMENT_BUDGET_EXHAUSTED", "TIME_BUDGET_EXHAUSTED", "MANUAL_STOP_FILE_DETECTED"}


def test_next_action_vocab_exact():
    assert set(M.ALLOWED_ACTIONS) == {
        "CONTINUE_LOCAL_RESEARCH", "EXPAND_NO_KEY_DATA", "BUILD_BROADER_PANEL", "REQUIRE_PROVIDER",
        "PROMOTE_CONFIRMED_SIGNAL", "REJECT_FAMILY", "STOP"}


def test_agent_roles_exact():
    assert M.DAEMON_ROLES == [
        "research-director-agent", "data-foundation-agent", "universe-agent", "external-data-agent",
        "hypothesis-generator-agent", "macro-sensitivity-agent", "earnings-catalyst-agent",
        "news-sentiment-agent", "analyst-revision-agent", "options-short-interest-agent",
        "validation-skeptic-agent", "risk-tail-agent", "model-candidate-agent"]
    assert len(M.DAEMON_ROLES) == 13


def test_alpha_status_and_recommendation_vocab_reused():
    assert set(M.ALLOWED_ALPHA_STATUSES) >= {
        "CONFIRMED_ALPHA_SIGNAL", "PROMISING_ALPHA_SIGNAL", "PROVIDER_REQUIRED"}
    assert "PROMISING_ALPHA_SIGNAL_FOUND" in M.ALLOWED_RECOMMENDATIONS
    assert "PROMISING_BUT_PROVIDER_LIMITED" in M.ALLOWED_RECOMMENDATIONS


# --------------------------------------------------------------------------- #
# Hypothesis bank: column legality, challenge fraction, expected leads.
# --------------------------------------------------------------------------- #
def test_bank_conditions_use_only_legal_columns():
    for s in M.generate_hypothesis_bank():
        for cond in s.conditions:
            col = cond[0]
            assert col in LEGAL_COLS, f"{s.setup_id} uses illegal column {col}"


def test_bank_challenge_fraction_at_least_30pct():
    bank = M.generate_hypothesis_bank()
    n_chal = sum(1 for s in bank if s.is_challenge)
    n_real = sum(1 for s in bank if not s.is_challenge)
    assert n_chal / n_real >= 0.30


def test_bank_contains_required_combination_leads():
    ids = {s.setup_id for s in M.generate_hypothesis_bank()}
    # the proven base macro lead (S8E-011) + the earnings-confirmed rates lead (F20 equivalent)
    assert "S8J-RATES-MACRO-20" in ids
    assert "S8J-RATES-EARNCONF-20" in ids
    # combination families across the required dimensions
    for needed in ("S8J-OIL-POS-20", "S8J-USD-NEG-20", "S8J-CREDIT-20", "S8J-VIX-VOLSENS-20",
                   "S8J-EARN-SECLEAD-20", "S8J-EARN-HIGHBETA-20", "S8J-FILING-SECLEAD-20",
                   "S8J-REV-RATES-20"):
        assert needed in ids


def test_interleave_spreads_challenges_into_every_batch():
    bank = M._interleave_challenges(M.generate_hypothesis_bank())
    first12 = bank[:M.HYPOTHESES_PER_CYCLE]
    assert any(s.is_challenge for s in first12), "first batch must carry challenge/placebo controls"


def test_revision_and_filing_families_never_confirmable():
    # proxy / thin families are capped below CONFIRMED in the reused 8-G ladder
    assert M.FAM_REVISION in M.P8G.PROXY_OR_THIN_FAMILIES
    assert M.FAM_FILINGS in M.P8G.PROXY_OR_THIN_FAMILIES


# --------------------------------------------------------------------------- #
# Next-action decision logic.
# --------------------------------------------------------------------------- #
def test_decide_promote_on_confirmed():
    agg = {"n_confirmed": 1, "confirmed_ids": ["X"], "n_clean_promising": 0, "n_promising": 1,
           "n_provider_limited": 0, "n_provider_required": 0}
    act, _ = M.decide_next_action(agg, queue_remaining=5, readiness={}, rejected_fams=[])
    assert act == M.ACT_PROMOTE_CONFIRMED


def test_decide_continue_when_queue_remains():
    agg = {"n_confirmed": 0, "confirmed_ids": [], "n_clean_promising": 0, "n_promising": 0,
           "n_provider_limited": 0, "n_provider_required": 0}
    act, _ = M.decide_next_action(agg, queue_remaining=8, readiness={}, rejected_fams=[])
    assert act == M.ACT_CONTINUE_LOCAL


def test_decide_require_provider_when_drained_and_only_limited():
    agg = {"n_confirmed": 0, "confirmed_ids": [], "n_clean_promising": 0, "n_promising": 2,
           "n_provider_limited": 2, "n_provider_required": 0}
    act, _ = M.decide_next_action(agg, queue_remaining=0, readiness={"FMP_API_KEY": False},
                                  rejected_fams=[])
    assert act == M.ACT_REQUIRE_PROVIDER


def test_decide_reject_family_when_exhausted():
    agg = {"n_confirmed": 0, "confirmed_ids": [], "n_clean_promising": 0, "n_promising": 0,
           "n_provider_limited": 0, "n_provider_required": 0}
    act, _ = M.decide_next_action(agg, queue_remaining=0, readiness={}, rejected_fams=["FAMX"])
    assert act == M.ACT_REJECT_FAMILY


# --------------------------------------------------------------------------- #
# Stop evaluation.
# --------------------------------------------------------------------------- #
_BASE_AGG = {"n_confirmed": 0}


def _stop(**kw):
    args = dict(agg=_BASE_AGG, queue_remaining=5, batch_scored=True, cycles_done=1,
                experiments_scored=12, max_cycles=None, max_experiments=None, time_exhausted=False,
                stop_file=False, stop_on_confirmed=False, stop_on_provider_blocker=False,
                safety_ok=True, hard_provider=False)
    args.update(kw)
    return M.evaluate_stop(**args)


def test_stop_file_takes_precedence():
    assert _stop(stop_file=True) == M.STOP_MANUAL


def test_stop_on_safety():
    assert _stop(safety_ok=False) == M.STOP_SAFETY


def test_stop_on_confirmed_signal():
    assert _stop(agg={"n_confirmed": 1}) == M.STOP_CONFIRMED


def test_stop_max_cycles_is_hard_cap_even_with_queue():
    assert _stop(max_cycles=1, cycles_done=1, queue_remaining=20) == M.STOP_EXPERIMENT_BUDGET


def test_stop_max_experiments():
    assert _stop(max_experiments=10, experiments_scored=12) == M.STOP_EXPERIMENT_BUDGET


def test_stop_time_budget():
    assert _stop(time_exhausted=True) == M.STOP_TIME_BUDGET


def test_no_stop_when_work_remains():
    assert _stop() is None


def test_stop_provider_when_drained_and_flagged():
    assert _stop(queue_remaining=0, stop_on_provider_blocker=True, hard_provider=True) == M.STOP_PROVIDER


# --------------------------------------------------------------------------- #
# Provider blockers + agent boards (pure).
# --------------------------------------------------------------------------- #
def test_provider_blockers_logged_for_missing_keys():
    rows = M.provider_blocker_rows({})  # no keys present
    fams = {r["family"] for r in rows}
    assert M.FAM_NEWS in fams and M.FAM_OPTIONS in fams and M.FAM_SHORT in fams
    assert all(r["blocker_active"] for r in rows)
    # the PowerShell hint must reference an env var name, never a secret value
    assert all("<your_key>" in r["powershell_to_supply"] for r in rows)


def test_agent_task_board_covers_all_roles():
    bank = M._interleave_challenges(M.generate_hypothesis_bank())
    board = M.agent_task_board_rows(1, bank[:12], [], [], M.provider_blocker_rows({}))
    assert {r["role"] for r in board} == set(M.DAEMON_ROLES)
    summary = M.agent_cycle_summary_rows(board)
    assert len(summary) == 13


# --------------------------------------------------------------------------- #
# Real run: --once contract.
# --------------------------------------------------------------------------- #
def test_once_runs_single_cycle(once_run):
    rep = once_run["report"]
    assert rep["loop"]["cycles_completed"] == 1
    assert rep["loop"]["experiments_scored"] == M.HYPOTHESES_PER_CYCLE
    assert rep["stop_reason"] in M.ALLOWED_STOPS
    assert rep["recommendation"] in M.ALLOWED_RECOMMENDATIONS
    assert rep["next_action"] in M.ALLOWED_ACTIONS


def test_persistent_state_written(once_run):
    state = once_run["state"]
    for fn in M.STATE_FILES.values():
        assert (state / fn).exists(), f"missing durable state file {fn}"


def test_hypothesis_and_experiment_queues_generated(once_run):
    out = once_run["out"]
    import csv
    with open(out / "hypothesis_queue_snapshot.csv", newline="") as fh:
        hq = list(csv.DictReader(fh))
    with open(out / "experiment_queue_snapshot.csv", newline="") as fh:
        eq = list(csv.DictReader(fh))
    assert len(hq) == len(M.generate_hypothesis_bank())
    assert any(r["status"] == "TESTED" for r in hq) and any(r["status"] == "QUEUED" for r in hq)
    assert any(r["state"] == "SCORED" for r in eq) and any(r["state"] == "QUEUED" for r in eq)


def test_agent_task_board_populated_artifact(once_run):
    import csv
    with open(once_run["out"] / "agent_task_board.csv", newline="") as fh:
        board = list(csv.DictReader(fh))
    assert {r["role"] for r in board} == set(M.DAEMON_ROLES)


def test_candidate_registry_updates_and_classifications_valid(once_run):
    import csv
    with open(once_run["out"] / "candidate_signal_registry_snapshot.csv", newline="") as fh:
        reg = list(csv.DictReader(fh))
    assert len(reg) >= M.HYPOTHESES_PER_CYCLE
    valid = set(M.ALLOWED_ALPHA_STATUSES)
    for r in reg:
        assert r["alpha_promotion"] in valid


def test_provider_required_classification_present(once_run):
    import csv
    with open(once_run["out"] / "provider_required_signals.csv", newline="") as fh:
        rows = list(csv.DictReader(fh))
    # either explicit provider-required signals or the honest "none" marker
    assert rows
    if "status" in rows[0] and len(rows[0]) == 1:
        return
    assert all(r["alpha_promotion"] == "PROVIDER_REQUIRED" for r in rows)


def test_all_committed_safe_artifacts_present_and_parse(once_run):
    out = once_run["out"]
    for name in M.ARTIFACTS:
        p = out / name
        assert p.exists(), f"missing artifact {name}"
        if name.endswith(".json"):
            json.loads(p.read_text(encoding="utf-8"))


def test_safety_block_forbids_paper_trader_orders_gcp_and_secrets(once_run):
    safety = once_run["report"]["safety"]
    for flag in ("paper_trader_touched", "gcp_touched", "deployment", "broker_or_orders",
                 "automation_of_orders", "live_trading_signals", "secrets_printed",
                 "external_data_faked", "committed", "pushed", "packages_installed",
                 "optimized_weights", "factor_signs_modified_after_results"):
        assert safety[flag] is False
    assert safety["research_only"] is True
    assert safety["large_state_only_on_d"] is True


def test_no_secrets_in_provider_inventory(once_run):
    # provider detection is by NAME/presence only — the report never carries a key value
    prov = once_run["report"]["provider"]
    assert isinstance(prov["any_key_present"], bool)
    assert all(isinstance(v, bool) for v in prov["readiness"].values())


def test_no_order_or_automation_logic_in_source():
    src = (_REPO_ROOT / "research" / "run_phase8j_autonomous_alpha_research_daemon.py").read_text(
        encoding="utf-8").lower()
    for forbidden in ("place_order", "submit_order", "create_order", "broker.", "alpaca",
                      "def execute_trade", "gcloud ", "deploy("):
        assert forbidden not in src


# --------------------------------------------------------------------------- #
# Real run: resume continues from durable state.
# --------------------------------------------------------------------------- #
def test_resume_continues_from_prior_state(tmp_path):
    out, state = tmp_path / "out", tmp_path / "state"
    d1 = M.AlphaResearchDaemon(out, state)
    r1 = d1.run(once=True)
    assert r1["loop"]["experiments_scored"] == M.HYPOTHESES_PER_CYCLE
    d2 = M.AlphaResearchDaemon(out, state)
    r2 = d2.run(resume=True, max_cycles=2)
    assert r2["loop"]["experiments_scored"] == 2 * M.HYPOTHESES_PER_CYCLE
    assert r2["loop"]["cycles_completed"] == 2


# --------------------------------------------------------------------------- #
# Real run: --dry-run does not persist to the D: state dir.
# --------------------------------------------------------------------------- #
def test_dry_run_does_not_persist_state(tmp_path):
    out, state = tmp_path / "out", tmp_path / "state"
    d = M.AlphaResearchDaemon(out, state, dry_run=True)
    rep = d.run(once=True)
    assert (not state.exists()) or (not any(state.iterdir()))
    # snapshots are still emitted (committed-safe), just no durable D: state
    assert (out / "phase8j_autonomous_alpha_research_daemon.json").exists()
    assert rep["run_config"]["dry_run"] is True


# --------------------------------------------------------------------------- #
# Real run: manual STOP file halts before scoring.
# --------------------------------------------------------------------------- #
def test_stop_file_halts_daemon(tmp_path):
    out, state = tmp_path / "out", tmp_path / "state"
    state.mkdir(parents=True)
    (state / M.STOP_FILE_NAME).write_text("stop")
    d = M.AlphaResearchDaemon(out, state)
    rep = d.run(max_cycles=5)
    assert rep["stop_reason"] == M.STOP_MANUAL
    assert rep["loop"]["cycles_completed"] == 0
