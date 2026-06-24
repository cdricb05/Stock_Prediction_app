"""Tests for Phase 8-B — Autonomous Research Director Orchestrator.

Pure-logic tests run with no external data (Norgate is never imported; the panel is not
required). A small synthetic panel exercises the evaluation path. End-to-end tests are
guarded with skipif on the presence of the committed-safe report + the D: panel, so the
suite stays green on machines without the local Norgate panel.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MODULE_PATH = _REPO_ROOT / "research" / "run_phase8b_autonomous_research_director.py"
_OUTPUT_DIR = _REPO_ROOT / "research" / "output" / "phase8b_autonomous_research_director"
_REPORT = _OUTPUT_DIR / "phase8b_autonomous_research_director.json"


def _load_module():
    spec = importlib.util.spec_from_file_location("phase8b_under_test", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load_module()


# --------------------------------------------------------------------------- #
# Vocabulary / config.
# --------------------------------------------------------------------------- #
def test_recommendation_vocabulary_exact_and_ordered():
    assert M.ALLOWED_RECOMMENDATIONS == (
        "AUTONOMOUS_RESEARCH_LOOP_READY", "SIGNALS_WEAK_KEEP_RESEARCH_ONLY",
        "SIGNAL_RESEARCH_REJECTED_ON_CLEAN_DATA", "ORCHESTRATOR_BLOCKED",
        "DATA_PANEL_BLOCKED", "NEEDS_RESEARCH_DIRECTOR_REVIEW", "ERROR",
    )


def test_status_vocabulary_exact():
    assert M.ALLOWED_STATUSES == ("APPROVED", "WEAK", "REJECTED", "DIAGNOSTIC_ONLY", "BLOCKED")


def test_qdiv_mapping():
    assert M.QDIV["top_quintile"] == 5
    assert M.QDIV["top_decile"] == 10
    assert M.QDIV["concentrated"] == 20    # ~25 names of ~500
    assert M.QDIV["broad"] == 3


def test_required_contracts_count():
    assert len(M.REQUIRED_CONTRACTS) == 6


def test_subperiods_and_recent_label():
    assert set(M.SUBPERIODS) == {"1990-2004", "2005-2014", "2015-2026"}
    assert M.RECENT_LABEL == "2015-2026"
    assert M.RECENT_LABEL in M.SUBPERIODS


# --------------------------------------------------------------------------- #
# Experiment queue — autonomy budget guardrails.
# --------------------------------------------------------------------------- #
def test_queue_respects_max_experiments():
    q = M.build_experiment_queue()
    assert 0 < len(q) <= M.MAX_EXPERIMENTS == 30


def test_queue_challenge_fraction_at_least_30pct():
    q = M.build_experiment_queue()
    n_challenge = sum(1 for e in q if e.category == "CHALLENGE")
    assert n_challenge / len(q) >= 0.30


def test_queue_non_momentum_fraction_at_least_20pct():
    q = M.build_experiment_queue()
    n_nonmom = sum(1 for e in q if e.category == "NON_MOMENTUM")
    assert n_nonmom / len(q) >= 0.20


def test_queue_challenges_target_8a_approved_signals():
    q = M.build_experiment_queue()
    challenged = {e.challenges for e in q if e.category == "CHALLENGE"}
    assert challenged == {"EXP02", "EXP11"}


def test_every_queued_experiment_is_fully_specified():
    q = M.build_experiment_queue()
    ids = [e.experiment_id for e in q]
    assert len(ids) == len(set(ids))                 # unique ids
    for e in q:
        assert e.hypothesis and e.success_gate and e.stop_condition
        assert e.category in {"CONFIRM", "MOMENTUM_ROBUSTNESS", "NON_MOMENTUM",
                              "CHALLENGE", "RISK_STRESS"}
        assert e.owning_agent
        assert e.quantile in M.QDIV


def test_queue_includes_confirmation_of_both_8a_signals():
    q = M.build_experiment_queue()
    confirm = {e.challenges for e in q if e.category == "CONFIRM"}
    assert confirm == {"EXP02", "EXP11"}


# --------------------------------------------------------------------------- #
# Stability gate — the key honesty fix: beating SPY out-of-sample, not just positive.
# --------------------------------------------------------------------------- #
def _ev(sub_beats, recent_beats, cost50=0.6, sub_pos=None):
    """Minimal ev dict for _stability. sub_beats: list of beats_spy for the 3 sub-periods."""
    if sub_pos is None:
        sub_pos = [True] * len(sub_beats)
    labels = ["1990-2004", "2005-2014", "2015-2026"]
    subs = {}
    for lab, bs, pos in zip(labels, sub_beats, sub_pos):
        subs[lab] = {"status": "DIAGNOSTIC_ONLY", "beats_spy": bs,
                     "net_sharpe": 0.5 if pos else -0.1}
    # force recent
    subs["2015-2026"]["beats_spy"] = recent_beats
    subs["2015-2026"]["net_sharpe"] = 0.5 if recent_beats or sub_pos[2] else -0.1
    return {"subperiods": subs, "cost_sharpe": {50: cost50}}


def test_stability_requires_beating_spy_not_just_positive():
    # positive in all 3 but beats SPY in none -> not stable
    ev = _ev(sub_beats=[False, False, False], recent_beats=False, sub_pos=[True, True, True])
    st = M._stability(ev)
    assert st["n_subperiods_positive"] == 3
    assert st["n_subperiods_beat_spy"] == 0
    assert st["stable"] is False


def test_stability_true_when_beats_spy_majority_incl_recent():
    ev = _ev(sub_beats=[True, False, True], recent_beats=True, cost50=0.5)
    st = M._stability(ev)
    assert st["n_subperiods_beat_spy"] == 2
    assert st["recent_beats_spy"] is True
    assert st["stable"] is True


def test_stability_false_when_recent_fails_even_if_two_earlier_beat():
    ev = _ev(sub_beats=[True, True, False], recent_beats=False, cost50=0.5)
    st = M._stability(ev)
    assert st["n_subperiods_beat_spy"] == 2
    assert st["recent_beats_spy"] is False
    assert st["stable"] is False           # recency requirement binds


def test_stability_false_when_not_cost_robust():
    ev = _ev(sub_beats=[True, True, True], recent_beats=True, cost50=-0.1)
    st = M._stability(ev)
    assert st["cost_robust_50bps"] is False
    assert st["stable"] is False


# --------------------------------------------------------------------------- #
# Candidate classification.
# --------------------------------------------------------------------------- #
def _full_ev(net_sharpe, spy=0.77, ew=0.75, placebo_gap=0.4, dd=-0.4, turnover=0.25,
             n_periods=400, avg_names=100, sub_beats=(True, True, True),
             recent_beats=True, cost50=0.6, beta=0.9):
    labels = ["1990-2004", "2005-2014", "2015-2026"]
    subs = {}
    for lab, bs in zip(labels, sub_beats):
        subs[lab] = {"status": "DIAGNOSTIC_ONLY", "beats_spy": bs,
                     "net_sharpe": 0.6 if bs else 0.3, "beats_ew": bs}
    subs["2015-2026"]["beats_spy"] = recent_beats
    return {
        "n_periods": n_periods, "avg_names": avg_names,
        "net_sharpe_25bps": net_sharpe, "net_ann_return_25bps": 0.1,
        "leakage_check": "PASS_NO_LOOKAHEAD", "spy_sharpe": spy, "ew_universe_sharpe": ew,
        "net_sharpe_minus_placebo": placebo_gap, "net_max_drawdown_25bps": dd,
        "spy_max_drawdown": -0.5, "one_sided_turnover": turnover,
        "subperiods": subs, "cost_sharpe": {50: cost50},
        "risk": {"beta_spy": beta},
    }


def test_classify_candidate_approved_when_gate_and_stable_and_risk():
    ev = _full_ev(net_sharpe=0.9, sub_beats=(True, True, True), recent_beats=True)
    status, reason, _ = M.classify_candidate(ev)
    assert status == "APPROVED"
    assert "confirmed" in reason


def test_classify_candidate_weak_when_positive_but_not_out_of_sample():
    ev = _full_ev(net_sharpe=0.8, sub_beats=(True, False, False), recent_beats=False)
    status, reason, _ = M.classify_candidate(ev)
    assert status == "WEAK"
    assert "does not confirm out-of-sample" in reason


def test_classify_candidate_rejected_when_underperforms_spy_full_sample():
    ev = _full_ev(net_sharpe=0.5, spy=0.77)     # below SPY full-sample
    status, _reason, _ = M.classify_candidate(ev)
    assert status == "REJECTED"


def test_classify_candidate_blocked_when_insufficient_history():
    ev = _full_ev(net_sharpe=0.9, n_periods=10)
    status, _reason, _ = M.classify_candidate(ev)
    assert status == "BLOCKED"


def test_risk_gate_rejects_high_beta():
    ev = _full_ev(net_sharpe=0.9, beta=2.0)
    assert M._risk_gate_ok(ev) is False
    ev2 = _full_ev(net_sharpe=0.9, beta=0.9)
    assert M._risk_gate_ok(ev2) is True


# --------------------------------------------------------------------------- #
# Decision derivation — every branch.
# --------------------------------------------------------------------------- #
class _Panel:
    def __init__(self, ok):
        self.ok = ok
        self.close = pd.DataFrame()


def _confirmation(status_exp02, status_exp11, ns02=0.8, ns11=0.84):
    def one(exp_id, st, ns):
        return {"phase8a_id": exp_id, "status": st, "net_sharpe_25bps": ns,
                "confirmed": st == "APPROVED"}
    return {"EXP02": one("EXP02", status_exp02, ns02),
            "EXP11": one("EXP11", status_exp11, ns11)}


def test_decision_loop_ready_when_one_8a_confirmed():
    conf = _confirmation("APPROVED", "WEAK")
    d = M._derive_decision({}, True, _Panel(True), conf, {})
    assert d["recommendation"] == "AUTONOMOUS_RESEARCH_LOOP_READY"


def test_decision_weak_when_positive_but_not_confirmed():
    conf = _confirmation("WEAK", "WEAK")
    d = M._derive_decision({}, True, _Panel(True), conf, {})
    assert d["recommendation"] == "SIGNALS_WEAK_KEEP_RESEARCH_ONLY"


def test_decision_rejected_when_all_fail_and_not_positive():
    conf = _confirmation("REJECTED", "REJECTED", ns02=-0.1, ns11=-0.2)
    d = M._derive_decision({}, True, _Panel(True), conf, {})
    assert d["recommendation"] == "SIGNAL_RESEARCH_REJECTED_ON_CLEAN_DATA"


def test_decision_data_blocked_when_panel_not_ok():
    conf = _confirmation("APPROVED", "APPROVED")
    d = M._derive_decision({}, True, _Panel(False), conf, {})
    assert d["recommendation"] == "DATA_PANEL_BLOCKED"


def test_decision_orchestrator_blocked():
    conf = _confirmation("APPROVED", "APPROVED")
    d = M._derive_decision({}, False, _Panel(True), conf, {})
    assert d["recommendation"] == "ORCHESTRATOR_BLOCKED"


# --------------------------------------------------------------------------- #
# Orchestrator readiness.
# --------------------------------------------------------------------------- #
def test_orchestrator_ready_true_for_complete_state():
    state = {
        "subagents_ok": True, "n_subagents": 12, "n_contracts": 6,
        "contracts_present": {fn: True for fn in M.REQUIRED_CONTRACTS},
        "phase8a_present": {"all_experiments_scoreboard.csv": True},
        "phase8a_approved_ids": ["EXP02", "EXP11"],
    }
    ok, problems = M.orchestrator_ready(state)
    assert ok is True and problems == []


def test_orchestrator_not_ready_when_contracts_missing():
    state = {
        "subagents_ok": True, "n_subagents": 12, "n_contracts": 4,
        "contracts_present": {fn: True for fn in M.REQUIRED_CONTRACTS[:4]},
        "phase8a_present": {"all_experiments_scoreboard.csv": True},
        "phase8a_approved_ids": ["EXP02"],
    }
    ok, problems = M.orchestrator_ready(state)
    assert ok is False and problems


# --------------------------------------------------------------------------- #
# Rolling windows / sub-period mask / promising leads.
# --------------------------------------------------------------------------- #
def test_rolling_stats_on_known_series():
    idx = pd.date_range("1990-01-31", periods=200, freq="ME")
    net = pd.Series(0.01, index=idx)              # constant positive (vol ~0 -> sharpe None)
    spy = pd.Series(0.005, index=idx)
    out = M._rolling_stats(net, spy, window=120, step=12)
    # constant series -> sharpe undefined -> no windows counted
    assert out["n_windows"] == 0 or out["frac_positive"] in (0.0, 1.0, None)


def test_rolling_stats_varied_series_counts_windows():
    rng = np.random.default_rng(0)
    idx = pd.date_range("1990-01-31", periods=200, freq="ME")
    net = pd.Series(rng.normal(0.01, 0.04, size=200), index=idx)
    spy = pd.Series(rng.normal(0.005, 0.04, size=200), index=idx)
    out = M._rolling_stats(net, spy, window=120, step=12)
    assert out["n_windows"] >= 1
    assert out["min_sharpe"] is not None
    assert 0.0 <= out["frac_beat_spy"] <= 1.0


def test_subperiod_mask():
    idx = pd.DatetimeIndex(["1995-12-31", "2008-06-30", "2020-01-31"])
    m = M._subperiod_mask(idx, 2005, 2014)
    assert list(m) == [False, True, False]


def test_promising_leads_only_weak_beating_spy_sorted():
    base_ev = {
        "NM01": {"score_key": "low_vol", "quantile": "top_quintile",
                 "net_sharpe_25bps": 0.85, "net_sharpe_minus_spy": 0.08,
                 "net_max_drawdown_25bps": -0.39},
        "MR05": {"score_key": "mom_6_1", "quantile": "top_quintile",
                 "net_sharpe_25bps": 0.61, "net_sharpe_minus_spy": -0.16,
                 "net_max_drawdown_25bps": -0.55},
    }
    verdicts = {
        "NM01": ("WEAK", "x", {"n_subperiods_beat_spy": 2}),
        "MR05": ("REJECTED", "x", {"n_subperiods_beat_spy": 0}),
    }
    leads = M._promising_leads(base_ev, verdicts)
    assert [l["experiment_id"] for l in leads] == ["NM01"]   # rejected & sub-SPY excluded


# --------------------------------------------------------------------------- #
# Feature catalog + 8-B blocks.
# --------------------------------------------------------------------------- #
def test_feature_catalog_registers_non_momentum_families():
    feats = {f for f, *_ in M.FEATURE_CATALOG}
    assert {"low_vol", "downside_vol", "illiquidity", "high_liquidity"} <= feats
    rows = M._feature_catalog_rows()
    assert all(r["registered_before_scoring"] is True for r in rows)


def test_build_8b_blocks_adds_orthogonal_blocks_and_is_leak_safe():
    idx = pd.date_range("2000-01-31", periods=48, freq="ME")
    cols = [f"S{i}" for i in range(10)]
    rng = np.random.default_rng(1)
    close = pd.DataFrame(100 * np.cumprod(1 + rng.normal(0.01, 0.05, size=(48, 10)), axis=0),
                         index=idx, columns=cols)
    dv = pd.DataFrame(rng.uniform(1e6, 1e7, size=(48, 10)), index=idx, columns=cols)
    spy = pd.Series(100 * np.cumprod(1 + rng.normal(0.008, 0.04, size=48)), index=idx)
    sector_map = {c: "Tech" for c in cols}
    blocks = M.build_8b_blocks(close, dv, spy, sector_map)
    for k in ("low_vol", "downside_vol", "illiquidity", "high_liquidity"):
        assert k in blocks
    # leak-safety: corrupting a FUTURE close must not change an early-row block value
    low_vol_before = blocks["low_vol"].iloc[12].copy()
    close2 = close.copy()
    close2.iloc[40:] *= 5.0
    blocks2 = M.build_8b_blocks(close2, dv, spy, sector_map)
    pd.testing.assert_series_equal(blocks2["low_vol"].iloc[12], low_vol_before)


# --------------------------------------------------------------------------- #
# evaluate_signal on a synthetic panel (exercises 8-A primitives, no D:/Norgate).
# --------------------------------------------------------------------------- #
def test_evaluate_signal_on_synthetic_panel():
    idx = pd.date_range("1990-01-31", periods=120, freq="ME")
    cols = [f"S{i}" for i in range(40)]
    rng = np.random.default_rng(7)
    close = pd.DataFrame(100 * np.cumprod(1 + rng.normal(0.01, 0.05, size=(120, 40)), axis=0),
                         index=idx, columns=cols)
    dv = pd.DataFrame(rng.uniform(1e6, 1e7, size=(120, 40)), index=idx, columns=cols)
    members = pd.DataFrame(1.0, index=idx, columns=cols)
    spy = pd.Series(100 * np.cumprod(1 + rng.normal(0.008, 0.04, size=120)), index=idx)
    sector_map = {c: "Tech" for c in cols}
    blocks = M.build_8b_blocks(close, dv, spy, sector_map)
    forward = close.pct_change().shift(-1)
    ev = M.evaluate_signal(blocks, forward, members, spy, sector_map, set(),
                           score_key="mom_3", quantile="top_quintile")
    assert ev["leakage_check"] == "PASS_NO_LOOKAHEAD"
    assert ev["n_periods"] > 0
    assert set(M.SUBPERIODS) == set(ev["subperiods"])
    assert "beta_spy" in ev["risk"]
    assert isinstance(ev["cost_sharpe"], dict) and 50 in ev["cost_sharpe"]


# --------------------------------------------------------------------------- #
# load_panel against tiny synthetic CSVs (ok) and missing files (blocked).
# --------------------------------------------------------------------------- #
def test_load_panel_ok_on_synthetic(tmp_path):
    idx = pd.date_range("1990-01-31", periods=40, freq="ME")
    cols = [f"S{i}" for i in range(25)]
    rng = np.random.default_rng(3)
    close = pd.DataFrame(100 * np.cumprod(1 + rng.normal(0.01, 0.05, size=(40, 25)), axis=0),
                         index=idx, columns=cols)
    close.index.name = "Date"
    close.to_csv(tmp_path / M.PANEL_FILES["monthly_close"])
    pd.DataFrame(1e6, index=idx, columns=cols).to_csv(tmp_path / M.PANEL_FILES["monthly_dollar_volume"])
    pd.DataFrame(1.0, index=idx, columns=cols).to_csv(tmp_path / M.PANEL_FILES["membership"])
    meta = pd.DataFrame({"gics_sector": ["Tech"] * 25,
                         "is_delisted": ([True] * 5) + ([False] * 20)}, index=cols)
    meta.index.name = "ticker"
    meta.to_csv(tmp_path / M.PANEL_FILES["metadata"])
    pd.Series(np.linspace(100, 200, 40), index=idx, name="Close").to_csv(
        tmp_path / M.PANEL_FILES["spy_monthly"])
    panel = M.load_panel(tmp_path)
    assert panel.ok is True
    assert panel.close.shape == (40, 25)
    assert panel.metadata["is_delisted"].sum() == 5


def test_load_panel_blocked_when_files_missing(tmp_path):
    panel = M.load_panel(tmp_path)
    assert panel.ok is False
    assert any("missing" in i for i in panel.issues)


# --------------------------------------------------------------------------- #
# End-to-end: the committed-safe artifacts (guarded; needs the real run on D:).
# --------------------------------------------------------------------------- #
_REQUIRE_RUN = pytest.mark.skipif(
    not _REPORT.exists(), reason="Phase 8-B report not generated (run the orchestrator first)")


@_REQUIRE_RUN
def test_report_recommendation_is_allowed():
    rep = json.loads(_REPORT.read_text(encoding="utf-8"))
    assert rep["recommendation"] in M.ALLOWED_RECOMMENDATIONS
    assert rep["allowed_recommendations"] == list(M.ALLOWED_RECOMMENDATIONS)


@_REQUIRE_RUN
def test_all_21_artifacts_present():
    expected = [
        "phase8b_autonomous_research_director.json", "research_agenda.csv",
        "agent_task_allocation.csv", "data_panel_check.csv", "universe_check.csv",
        "feature_catalog.csv", "experiment_queue.csv", "experiment_registry.csv",
        "all_experiments_scoreboard.csv", "failed_experiments.csv", "approved_signals.csv",
        "momentum_agent_report.csv", "reversal_agent_report.csv",
        "trend_breadth_agent_report.csv", "volatility_liquidity_agent_report.csv",
        "validation_skeptic_report.csv", "risk_portfolio_report.csv",
        "ensemble_readiness_report.csv", "paper_signal_contract.csv",
        "research_director_decision.json", "phase8c_next_plan.json",
    ]
    missing = [fn for fn in expected if not (_OUTPUT_DIR / fn).exists()]
    assert missing == [], f"missing artifacts: {missing}"
    assert len(expected) == 21


@_REQUIRE_RUN
def test_report_budget_guardrails_satisfied():
    rep = json.loads(_REPORT.read_text(encoding="utf-8"))
    ab = rep["autonomy_budget"]
    assert ab["experiments_registered"] <= ab["max_experiments"] == 30
    assert ab["challenge_fraction"] >= 0.30
    assert ab["non_momentum_fraction"] >= 0.20
    assert ab["all_registered_before_scoring"] is True


@_REQUIRE_RUN
def test_report_confirms_both_8a_signals_assessed():
    rep = json.loads(_REPORT.read_text(encoding="utf-8"))
    assert set(rep["confirmation"]) == {"EXP02", "EXP11"}
    for c in rep["confirmation"].values():
        assert c["status"] in M.ALLOWED_STATUSES


@_REQUIRE_RUN
def test_report_safety_flags_all_clean():
    rep = json.loads(_REPORT.read_text(encoding="utf-8"))
    s = rep["safety"]
    assert s["committed"] is False and s["pushed"] is False
    assert s["optimized_weights_used"] is False
    assert s["factor_signs_modified_after_results"] is False
    assert s["orders_or_automation_created"] is False
    assert s["reused_existing_panel_no_recollection"] is True


@_REQUIRE_RUN
def test_failed_experiments_not_hidden():
    # every non-APPROVED / non-DIAGNOSTIC candidate must appear in failed_experiments.csv
    rep = json.loads(_REPORT.read_text(encoding="utf-8"))
    failed = pd.read_csv(_OUTPUT_DIR / "failed_experiments.csv")
    scoreboard = pd.read_csv(_OUTPUT_DIR / "all_experiments_scoreboard.csv")
    should_fail = scoreboard[scoreboard["status"].isin(["REJECTED", "WEAK", "BLOCKED"])]
    assert len(failed) == len(should_fail)
    assert rep["safety"]["failed_experiments_hidden"] is False
