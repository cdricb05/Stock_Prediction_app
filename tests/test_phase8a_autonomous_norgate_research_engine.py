"""Tests for Phase 8-A — Autonomous Norgate Research Engine.

Pure-logic tests run without Norgate (the adapter imports lazily). The two end-to-end
tests are guarded by skipif on the locally installed `norgatedata` package + the D: panel,
so the suite is green on any machine; on the research box they additionally assert the real
artifacts, the survivorship content, and the safety contract.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research import run_phase8a_autonomous_norgate_research_engine as M

_REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Vocabulary / config invariants.
# --------------------------------------------------------------------------- #
def test_recommendation_vocabulary_exact_and_ordered():
    assert M.ALLOWED_RECOMMENDATIONS == (
        "NORGATE_RESEARCH_ENGINE_READY", "BUILD_FULL_NORGATE_PANEL_NEXT",
        "SIGNAL_RESEARCH_REJECTED_ON_CLEAN_DATA", "NORGATE_INTEGRATION_BLOCKED",
        "AGENT_SYSTEM_CREATION_BLOCKED", "NEEDS_RESEARCH_DIRECTOR_REVIEW", "ERROR",
    )


def test_experiment_status_vocabulary():
    assert {M.ST_APPROVED, M.ST_WEAK, M.ST_REJECTED, M.ST_NEEDS_FULL} == {
        "APPROVED", "WEAK", "REJECTED", "NEEDS_FULL_PANEL"}


def test_config_constants_are_research_safe():
    assert M.COST_BPS_GRID == (0.0, 10.0, 25.0, 50.0)
    assert M.PRIMARY_COST_BPS == 25.0
    assert 0 < M.MAX_POSITION <= 0.10
    assert M.MAX_EXPERIMENTS == 20
    assert str(M.PANEL_ROOT).replace("\\", "/").startswith("D:/Stock_Prediction_app_data")


def test_experiment_specs_within_budget_and_cover_all_families():
    specs = M.experiment_specs()
    assert 1 <= len(specs) <= M.MAX_EXPERIMENTS
    fams = {s.family for s in specs}
    assert fams == {"momentum", "reversal", "trend_breakout", "volatility_liquidity",
                    "breadth_relative_strength"}
    ids = [s.experiment_id for s in specs]
    assert len(ids) == len(set(ids))  # unique ids


# --------------------------------------------------------------------------- #
# Agent system files (Part A + Part B) must exist on disk.
# --------------------------------------------------------------------------- #
def test_all_subagents_and_contracts_present():
    ok, subs, cons = M._agents_present()
    assert ok, f"missing subagents/contracts: subs={subs} cons={cons}"
    assert len(subs) == 12
    assert len(cons) == 6


def test_contract_jsons_parse_and_share_forbidden_actions():
    manifest = json.loads((_REPO / "research/agents/agent_manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["agents"]) == 12
    forb = manifest["global_forbidden_actions"]
    assert any("commit" in f for f in forb) and any("push" in f for f in forb)
    assert any("order" in f.lower() for f in forb)
    proto = json.loads((_REPO / "research/agents/research_director_protocol.json").read_text(encoding="utf-8"))
    assert proto["recommendation_vocabulary"] == list(M.ALLOWED_RECOMMENDATIONS)


# --------------------------------------------------------------------------- #
# Delisted detection.
# --------------------------------------------------------------------------- #
def test_is_delisted_symbol():
    assert M._is_delisted_symbol("AABA-201910", None) is True   # suffix
    assert M._is_delisted_symbol("ENRNQ", "2001-12-02") is True  # last-quoted date
    assert M._is_delisted_symbol("AAPL", None) is False
    assert M._is_delisted_symbol("BRK.B", None) is False         # dotted, not a date suffix


# --------------------------------------------------------------------------- #
# Weights: cap + no leverage.
# --------------------------------------------------------------------------- #
def test_capped_equal_weights_no_bind():
    w = M.capped_equal_weights([f"S{i}" for i in range(20)])  # 5% each < 10% cap
    assert pytest.approx(sum(w.values()), abs=1e-9) == 1.0
    assert max(w.values()) <= M.MAX_POSITION + 1e-9


def test_capped_equal_weights_binds_leaves_cash():
    w = M.capped_equal_weights(["A", "B", "C", "D"])  # would be 25% each -> capped at 10%
    assert max(w.values()) == pytest.approx(0.10)
    assert sum(w.values()) == pytest.approx(0.40)  # 60% cash, no leverage


def test_capped_equal_weights_empty():
    assert M.capped_equal_weights([]) == {}


# --------------------------------------------------------------------------- #
# Metrics.
# --------------------------------------------------------------------------- #
def test_metric_helpers():
    idx = pd.date_range("2000-01-31", periods=12, freq="ME")
    assert M.sharpe(pd.Series([0.01], index=idx[:1])) is None  # <2 points -> None
    r2 = pd.Series([0.02, -0.01, 0.03, 0.00, 0.01, -0.02] * 2, index=idx)
    assert M.sharpe(r2) is not None
    # sign of Sharpe matches sign of mean return
    assert (M.sharpe(r2) > 0) == (r2.mean() > 0)
    assert M.ann_vol(r2) > 0
    assert -1.0 <= M.max_drawdown(r2) <= 0.0
    assert 0.0 <= M.hit_rate(r2) <= 1.0
    assert M.ann_return(pd.Series([0.0] * 5)) == pytest.approx(0.0)


def test_net_returns_charges_both_legs():
    idx = pd.date_range("2000-01-31", periods=3, freq="ME")
    gross = pd.Series([0.01, 0.01, 0.01], index=idx)
    traded = pd.Series([1.0, 1.0, 1.0], index=idx)  # full turnover (entry+exit)
    net = M.net_returns(gross, traded, 25.0)
    assert net.iloc[0] == pytest.approx(0.01 - 1.0 * 25.0 / 1e4)


def test_yearly_returns_compound_by_year():
    idx = pd.to_datetime(["2001-06-30", "2001-12-31", "2002-06-30"])
    r = pd.Series([0.10, 0.10, -0.05], index=idx)
    y = M.yearly_returns(r)
    assert y["2001"] == pytest.approx(0.21, abs=1e-9)
    assert y["2002"] == pytest.approx(-0.05, abs=1e-9)


# --------------------------------------------------------------------------- #
# Simulation.
# --------------------------------------------------------------------------- #
def _trend_panel(n_names=60, n_months=40, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2000-01-31", periods=n_months, freq="ME")
    # persistent cross-sectional drift so momentum is selectable
    drift = np.linspace(-0.02, 0.02, n_names)
    rets = drift[None, :] + rng.normal(0, 0.03, size=(n_months, n_names))
    close = pd.DataFrame(100 * np.cumprod(1 + rets, axis=0),
                         index=idx, columns=[f"N{i}" for i in range(n_names)])
    return close


def test_simulate_long_only_selects_topk_equal_weight():
    close = _trend_panel()
    members = pd.DataFrame(1.0, index=close.index, columns=close.columns)
    score = close.shift(1) / close.shift(12) - 1.0
    fwd = close.pct_change().shift(-1)
    sim = M.simulate_long_only(score, fwd, members, q=5)  # top quintile of 60 = 12 names
    # max weight under cap (12 names @ 1/12 = 8.3% < 10%)
    assert sim["avg_max_weight"] <= M.MAX_POSITION + 1e-9
    assert 10 <= sim["avg_names"] <= 13
    assert sim["one_sided_turnover"] is not None


def test_simulate_long_only_skips_thin_cross_section():
    close = _trend_panel(n_names=10)  # below MIN_NAMES_PORT=20
    members = pd.DataFrame(1.0, index=close.index, columns=close.columns)
    score = close.shift(1) / close.shift(12) - 1.0
    fwd = close.pct_change().shift(-1)
    sim = M.simulate_long_only(score, fwd, members, q=5)
    assert len(sim["period_index"]) == 0  # nothing formed


def test_simulate_respects_membership_mask():
    close = _trend_panel(n_names=60)
    members = pd.DataFrame(1.0, index=close.index, columns=close.columns)
    members.iloc[:, 30:] = 0.0  # only 30 names are ever members
    score = close.shift(1) / close.shift(12) - 1.0
    fwd = close.pct_change().shift(-1)
    sim = M.simulate_long_only(score, fwd, members, q=5)
    for t, w in sim["weights_by_month"].items():
        assert all(int(n[1:]) < 30 for n in w)  # never selects a non-member


# --------------------------------------------------------------------------- #
# Leakage safety of the building blocks.
# --------------------------------------------------------------------------- #
def test_signal_blocks_are_leak_safe():
    close = _trend_panel(n_names=40, n_months=30)
    dv = close * 1000.0
    spy = pd.Series(np.linspace(100, 200, len(close)), index=close.index)
    sector_map = {c: ("S_A" if i % 2 else "S_B") for i, c in enumerate(close.columns)}
    blocks = M.build_signal_blocks(close, dv, spy, sector_map)
    # corrupt the future (after month index 20) and recompute; values at t<=19 must be identical
    t_check = close.index[15]
    close2 = close.copy()
    close2.iloc[21:] = close2.iloc[21:] * 5.0
    blocks2 = M.build_signal_blocks(close2, dv, spy, sector_map)
    for key in ("mom_12_1", "mom_6_1", "mom_3", "vol_adj_mom", "sector_rel_mom"):
        a = blocks[key].loc[t_check]
        b = blocks2[key].loc[t_check]
        pd.testing.assert_series_equal(a, b, check_names=False)


def test_seeded_permute_is_deterministic():
    close = _trend_panel(n_names=20, n_months=10)
    a = M._seeded_permute(close, 123)
    b = M._seeded_permute(close, 123)
    pd.testing.assert_frame_equal(a, b)
    # permutation preserves the row multiset
    np.testing.assert_allclose(np.sort(a.iloc[5].values), np.sort(close.iloc[5].values))


# --------------------------------------------------------------------------- #
# Classification / gating.
# --------------------------------------------------------------------------- #
def _exp(**kw):
    base = dict(experiment_id="EXPxx", n_periods=400, avg_names=50.0,
                net_sharpe_25bps=1.0, net_ann_return_25bps=0.10,
                net_max_drawdown_25bps=-0.25, one_sided_turnover=0.3,
                spy_sharpe=0.7, spy_max_drawdown=-0.30, ew_universe_sharpe=0.75,
                net_sharpe_minus_placebo=0.4, leakage_check="PASS_NO_LOOKAHEAD")
    base.update(kw)
    return base


def test_classify_approved():
    st, _ = M.classify_experiment(_exp())
    assert st == M.ST_APPROVED


def test_classify_rejected_after_costs():
    st, _ = M.classify_experiment(_exp(net_sharpe_25bps=-0.1))
    assert st == M.ST_REJECTED


def test_classify_rejected_underperforms_spy():
    st, _ = M.classify_experiment(_exp(net_sharpe_25bps=0.6, spy_sharpe=0.7))
    assert st == M.ST_REJECTED


def test_classify_rejected_fails_placebo():
    st, reason = M.classify_experiment(_exp(net_sharpe_minus_placebo=0.1))
    assert st == M.ST_REJECTED and "placebo" in reason


def test_classify_rejected_leakage():
    st, _ = M.classify_experiment(_exp(leakage_check="FAIL"))
    assert st == M.ST_REJECTED


def test_classify_weak_fails_ew_universe():
    st, reason = M.classify_experiment(_exp(net_sharpe_25bps=0.72, ew_universe_sharpe=0.80))
    assert st == M.ST_WEAK and "EW universe" in reason


def test_classify_needs_full_panel_short_history():
    st, _ = M.classify_experiment(_exp(n_periods=12))
    assert st == M.ST_NEEDS_FULL


# --------------------------------------------------------------------------- #
# Recommendation logic.
# --------------------------------------------------------------------------- #
def _verdicts(*statuses):
    return {f"E{i}": (s, "") for i, s in enumerate(statuses)}


def test_recommend_ready_when_one_approved():
    v = _verdicts("APPROVED", "REJECTED", "REJECTED")
    rec, _ = M.derive_recommendation(agents_created=True, norgate_available=True,
                                     membership_ok=True, delisted_ok=True,
                                     experiments=[{}, {}, {}], verdicts=v, panel_valid=True)
    assert rec == "NORGATE_RESEARCH_ENGINE_READY"


def test_recommend_rejected_when_all_fail():
    v = _verdicts("REJECTED", "REJECTED")
    rec, _ = M.derive_recommendation(agents_created=True, norgate_available=True,
                                     membership_ok=True, delisted_ok=True,
                                     experiments=[{}, {}], verdicts=v, panel_valid=True)
    assert rec == "SIGNAL_RESEARCH_REJECTED_ON_CLEAN_DATA"


def test_recommend_norgate_blocked():
    v = _verdicts("APPROVED")
    rec, _ = M.derive_recommendation(agents_created=True, norgate_available=True,
                                     membership_ok=False, delisted_ok=False,
                                     experiments=[{}], verdicts=v, panel_valid=True)
    assert rec == "NORGATE_INTEGRATION_BLOCKED"


def test_recommend_agent_blocked():
    rec, _ = M.derive_recommendation(agents_created=False, norgate_available=True,
                                     membership_ok=True, delisted_ok=True,
                                     experiments=[], verdicts={}, panel_valid=True)
    assert rec == "AGENT_SYSTEM_CREATION_BLOCKED"


def test_recommend_build_full_when_panel_invalid():
    v = _verdicts("NEEDS_FULL_PANEL")
    rec, _ = M.derive_recommendation(agents_created=True, norgate_available=True,
                                     membership_ok=True, delisted_ok=True,
                                     experiments=[{}], verdicts=v, panel_valid=False)
    assert rec == "BUILD_FULL_NORGATE_PANEL_NEXT"


def test_recommend_needs_review_when_only_weak():
    v = _verdicts("WEAK", "REJECTED")
    rec, _ = M.derive_recommendation(agents_created=True, norgate_available=True,
                                     membership_ok=True, delisted_ok=True,
                                     experiments=[{}, {}], verdicts=v, panel_valid=True)
    assert rec == "NEEDS_RESEARCH_DIRECTOR_REVIEW"


# --------------------------------------------------------------------------- #
# Survivorship audit.
# --------------------------------------------------------------------------- #
def test_survivorship_audit_counts():
    meta = pd.DataFrame({
        "is_delisted": [False, True, True, False],
        "n_member_months": [10, 5, 0, 8],
    }, index=["A", "B", "C", "D"])
    members = pd.DataFrame({"A": [1, 1], "B": [1, 0], "C": [0, 0], "D": [1, 1]})
    rows = {r["metric"]: r["value"] for r in M.build_survivorship_audit(meta, members)}
    assert rows["symbols_total"] == 4
    assert rows["symbols_delisted"] == 2
    assert rows["ever_sp500_members"] == 3        # A, B, D
    assert rows["ever_member_delisted"] == 1      # B (delisted & was member)


# --------------------------------------------------------------------------- #
# End-to-end (guarded): only on the research box with Norgate + the D: panel.
# --------------------------------------------------------------------------- #
def _norgate_present():
    return importlib.util.find_spec("norgatedata") is not None


_OUT = _REPO / "research/output/phase8a_autonomous_norgate_research_engine"


@pytest.mark.skipif(not _OUT.joinpath("phase8a_autonomous_norgate_research_engine.json").exists(),
                    reason="no committed-safe report present (run the engine first)")
def test_emitted_report_is_safe_and_consistent():
    rep = json.loads((_OUT / "phase8a_autonomous_norgate_research_engine.json").read_text(encoding="utf-8"))
    assert rep["recommendation"] in M.ALLOWED_RECOMMENDATIONS
    s = rep["safety"]
    assert s["committed"] is False and s["pushed"] is False
    assert s["packages_installed"] is False
    assert s["orders_or_automation_created"] is False
    assert s["large_data_only_on_d"] is True
    # agent system fully present
    assert rep["agent_system"]["n_subagents"] == 12
    assert rep["agent_system"]["n_contracts"] == 6


@pytest.mark.skipif(not _OUT.joinpath("survivorship_audit.csv").exists(),
                    reason="no survivorship audit present (run the engine first)")
def test_emitted_panel_is_survivorship_aware():
    audit = pd.read_csv(_OUT / "survivorship_audit.csv").set_index("metric")["value"]
    # the whole point of Norgate: delisted ever-members must be present
    assert float(audit.get("ever_member_delisted", 0)) > 0
    assert float(audit.get("symbols_delisted", 0)) > 0


@pytest.mark.skipif(not _norgate_present(),
                    reason="norgatedata not installed in this interpreter")
def test_adapter_introspects_without_inventing_functions():
    a = M.NorgateAdapter()
    assert a.available
    inv = {r["function"] for r in a.api_inventory()}
    # functions the phase relies on must actually exist in the installed package
    for fn in ("price_timeseries", "watchlist_symbols", "index_constituent_timeseries",
               "database_symbols", "classification_at_level"):
        assert fn in inv
