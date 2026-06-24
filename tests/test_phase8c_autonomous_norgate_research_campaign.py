"""Tests for the Phase 8-C autonomous Norgate research campaign.

The module is loaded by ABSOLUTE PATH via importlib so the tests are cwd-independent and do
NOT require norgatedata (the engine imports it only lazily, inside the panel-build path which
these tests never trigger). Pure-logic tests run everywhere; the synthetic-panel integration
test exercises the full campaign loop without Norgate; the end-to-end tests are guarded with
skipif on the real Russell 3000 report so they validate the committed artifacts when present.
"""
import csv
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MODULE_PATH = _REPO_ROOT / "research" / "run_phase8c_autonomous_norgate_research_campaign.py"


def _load():
    spec = importlib.util.spec_from_file_location("phase8c_engine_test", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


P8C = _load()
OUT_DIR = P8C.DEFAULT_OUT_DIR
REPORT_JSON = OUT_DIR / "phase8c_autonomous_norgate_research_campaign.json"


# --------------------------------------------------------------------------- #
# Vocabulary / config.
# --------------------------------------------------------------------------- #
def test_recommendation_vocabulary_exact_and_ordered():
    assert P8C.ALLOWED_RECOMMENDATIONS == (
        "CONFIRMED_SIGNAL_FOUND", "LEADS_WEAK_KEEP_RESEARCH_ONLY",
        "SIGNAL_RESEARCH_REJECTED_ON_BROAD_DATA", "FULL_PANEL_BUILD_REQUIRED",
        "DATA_PANEL_BLOCKED", "ORCHESTRATOR_BLOCKED", "NEEDS_RESEARCH_DIRECTOR_REVIEW", "ERROR",
    )


def test_status_vocabulary_exact():
    assert P8C.ALLOWED_STATUSES == (
        "CONFIRMED_SIGNAL", "WEAK", "REJECTED", "BLOCKED", "DIAGNOSTIC_ONLY")


def test_universe_preference_order_and_index_names():
    assert P8C.UNIVERSE_PREFERENCE[0] == "Russell 3000 Current & Past"
    assert P8C.UNIVERSE_PREFERENCE[-1] == "S&P 500 Current & Past"   # fallback last
    for name in P8C.UNIVERSE_PREFERENCE:
        assert name in P8C.UNIVERSE_INDEX_NAME


def test_budget_constants():
    assert P8C.MAX_TOTAL_EXPERIMENTS == 120
    assert P8C.MAX_PER_FAMILY == 40
    assert P8C.CHALLENGE_MIN_FRAC == 0.30
    assert P8C.NON_MOMENTUM_MIN_FRAC == 0.25
    assert P8C.MAX_CYCLES == 3


def test_gate_thresholds_are_apriori():
    assert P8C.GATE_SPY_SHARPE_MARGIN == 0.15
    assert P8C.GATE_EW_SHARPE_MARGIN == 0.10
    assert P8C.GATE_ROLLING_BEAT_FRAC == 0.60
    assert P8C.GATE_MAX_TURNOVER == 0.50
    assert P8C.GATE_MIN_HOLDINGS == 30


# --------------------------------------------------------------------------- #
# Family taxonomy + agent allocation.
# --------------------------------------------------------------------------- #
def test_non_momentum_family_classification():
    assert P8C.is_non_momentum_family(P8C.FAM_LOW_VOL)
    assert P8C.is_non_momentum_family(P8C.FAM_REV)
    assert P8C.is_non_momentum_family(P8C.FAM_TREND)
    # momentum and volatility-adjusted-momentum are NOT counted as non-momentum
    assert not P8C.is_non_momentum_family(P8C.FAM_MOM)
    assert not P8C.is_non_momentum_family(P8C.FAM_VAM)


def test_agent_assignment():
    assert P8C.agent_for("low_vol", "NON_MOMENTUM") == P8C.VOL_A
    assert P8C.agent_for("mom_12_1", "SCAN") == P8C.MOM_A
    assert P8C.agent_for("vol_adj_mom", "CONFIRM") == P8C.MOM_A        # momentum agent owns VAM
    assert P8C.agent_for("rev_losers", "NON_MOMENTUM") == P8C.REV_A
    assert P8C.agent_for("trend_score", "SCAN") == P8C.TRB_A
    assert P8C.agent_for("anything", "CHALLENGE") == P8C.VAL_A
    assert P8C.agent_for("anything", "RISK_STRESS") == P8C.RSK_A


def test_feature_catalog_includes_8c_additions_all_registered():
    feats = {row[0] for row in P8C.FEATURE_CATALOG}
    for new in ("low_vol_6", "downside_vol_6", "trend_ma12", "vol_adj_mom_6"):
        assert new in feats
    # every feature is registered before scoring
    rows = P8C._feature_catalog_rows()
    assert rows and all(r["registered_before_scoring"] is True for r in rows)


# --------------------------------------------------------------------------- #
# Multiple-testing deflation.
# --------------------------------------------------------------------------- #
def test_mt_required_excess_monotonic_in_n():
    assert P8C.mt_required_excess(10) == pytest.approx(0.15)
    a, b, c = (P8C.mt_required_excess(10), P8C.mt_required_excess(100), P8C.mt_required_excess(1000))
    assert a <= b <= c
    assert b == pytest.approx(0.30)   # 0.15 * log10(100) = 0.30


# --------------------------------------------------------------------------- #
# CONFIRMED_SIGNAL gate — classify_8c on synthetic evaluations.
# --------------------------------------------------------------------------- #
def _ev(**over):
    """A synthetic evaluation dict that, by default, PASSES every CONFIRMED gate."""
    subs = {
        "1990-2004": {"status": "DIAGNOSTIC_ONLY", "beats_spy": True, "net_sharpe": 1.0, "spy_sharpe": 0.7},
        "2005-2014": {"status": "DIAGNOSTIC_ONLY", "beats_spy": True, "net_sharpe": 0.9, "spy_sharpe": 0.6},
        "2015-2026": {"status": "DIAGNOSTIC_ONLY", "beats_spy": True, "net_sharpe": 1.1, "spy_sharpe": 0.9},
    }
    ev = {
        "n_periods": 400, "avg_names": 120.0,
        "net_sharpe_25bps": 1.10, "spy_sharpe": 0.80, "ew_universe_sharpe": 0.90,
        "net_sharpe_minus_spy": 0.30, "net_sharpe_minus_ew": 0.20,
        "net_max_drawdown_25bps": -0.45, "spy_max_drawdown": -0.50,
        "one_sided_turnover": 0.20,
        "placebo_net_sharpe_25bps": 0.10, "net_sharpe_minus_placebo": 0.50,
        "leakage_check": "PASS_NO_LOOKAHEAD",
        "cost_sharpe": {10: 1.2, 25: 1.10, 50: 0.90, 100: 0.60},
        "subperiods": subs,
        "rolling": {"frac_beat_spy": 0.70, "frac_positive": 1.0, "min_sharpe": 0.3},
        "risk": {"delisted_weight_avg": 0.25, "top_sector": "Information Technology",
                 "top_sector_weight": 0.15, "beta_spy": 0.9},
    }
    ev.update(over)
    return ev


def test_classify_confirmed():
    status, reason, checks, _m = P8C.classify_8c(_ev(), n_search=20)
    assert status == "CONFIRMED_SIGNAL"
    assert all(checks.values())


def test_classify_weak_when_margin_too_thin():
    # positive, beats SPY recently + full-sample, cost-robust, but excess < 0.15 margin
    ev = _ev(net_sharpe_25bps=0.84, spy_sharpe=0.80, net_sharpe_minus_spy=0.04,
             ew_universe_sharpe=0.80, net_sharpe_minus_ew=0.04)
    status, reason, checks, _m = P8C.classify_8c(ev, n_search=20)
    assert status == "WEAK"
    assert not checks["net_sharpe_beats_spy_by_0.15"]


def test_classify_rejected_when_loses_recent_holdout():
    subs = {
        "1990-2004": {"status": "DIAGNOSTIC_ONLY", "beats_spy": True, "net_sharpe": 1.0, "spy_sharpe": 0.7},
        "2005-2014": {"status": "DIAGNOSTIC_ONLY", "beats_spy": True, "net_sharpe": 0.9, "spy_sharpe": 0.6},
        "2015-2026": {"status": "DIAGNOSTIC_ONLY", "beats_spy": False, "net_sharpe": 0.5, "spy_sharpe": 0.9},
    }
    status, _r, checks, _m = P8C.classify_8c(_ev(subperiods=subs), n_search=20)
    assert status == "REJECTED"
    assert not checks["beats_spy_recent_2015_2026"]


def test_classify_rejected_when_not_cost_robust():
    status, _r, _c, _m = P8C.classify_8c(
        _ev(cost_sharpe={10: 0.5, 25: 0.3, 50: -0.1, 100: -0.4}), n_search=20)
    assert status == "REJECTED"


def test_classify_blocked_when_too_little_history():
    status, _r, _c, _m = P8C.classify_8c(_ev(n_periods=20), n_search=20)
    assert status == "BLOCKED"


def test_confirmed_requires_recent_even_if_2of3_and_margin_ok():
    # beats SPY in 1990-2004 + 2005-2014 (2/3) but loses recent -> never CONFIRMED
    subs = {
        "1990-2004": {"status": "DIAGNOSTIC_ONLY", "beats_spy": True, "net_sharpe": 1.0, "spy_sharpe": 0.7},
        "2005-2014": {"status": "DIAGNOSTIC_ONLY", "beats_spy": True, "net_sharpe": 0.9, "spy_sharpe": 0.6},
        "2015-2026": {"status": "DIAGNOSTIC_ONLY", "beats_spy": False, "net_sharpe": 0.8, "spy_sharpe": 0.95},
    }
    status, _r, checks, _m = P8C.classify_8c(_ev(subperiods=subs), n_search=20)
    assert status != "CONFIRMED_SIGNAL"
    assert checks["beats_spy_2_of_3_holdouts"] and not checks["beats_spy_recent_2015_2026"]


# --------------------------------------------------------------------------- #
# Universe selection.
# --------------------------------------------------------------------------- #
def test_select_universe_picks_broadest_feasible():
    rows = [
        {"universe": "S&P 500 Current & Past", "available": True, "superset_symbols": 1894,
         "feasible": True, "index_name": "S&P 500"},
        {"universe": "Russell 3000 Current & Past", "available": True, "superset_symbols": 12266,
         "feasible": True, "index_name": "Russell 3000"},
    ]
    sel = P8C.select_universe(rows)
    assert sel["selected_universe"] == "Russell 3000 Current & Past"   # earlier in preference order


def test_select_universe_falls_back_when_none_feasible():
    rows = [{"universe": "S&P 500 Current & Past", "available": True, "feasible": False,
             "superset_symbols": 100, "index_name": "S&P 500"}]
    sel = P8C.select_universe(rows)
    assert sel["selected_universe"] == "S&P 500 Current & Past"


# --------------------------------------------------------------------------- #
# Feature leak-safety: a FUTURE close cannot change an EARLY-row signal value.
# --------------------------------------------------------------------------- #
def test_build_8c_blocks_is_leak_safe():
    idx = pd.date_range("1990-01-31", periods=60, freq="ME")
    cols = [f"S{i}" for i in range(8)]
    rng = np.random.default_rng(1)
    close = pd.DataFrame(100 * np.cumprod(1 + 0.01 * rng.standard_normal((60, 8)), axis=0),
                         index=idx, columns=cols)
    dv = pd.DataFrame(1e6 * (1 + rng.random((60, 8))), index=idx, columns=cols)
    spy = pd.Series(100 * np.cumprod(1 + 0.008 * rng.standard_normal(60)), index=idx)
    sect = {c: "Tech" for c in cols}

    b0 = P8C.build_8c_blocks(close, dv, spy, sect)
    early = {k: v.iloc[20].copy() for k, v in b0.items() if isinstance(v, pd.DataFrame)}

    close2 = close.copy()
    close2.iloc[50] *= 5.0   # corrupt a far-future row
    b1 = P8C.build_8c_blocks(close2, dv, spy, sect)
    for k, ser in early.items():
        v1 = b1[k].iloc[20]
        pd.testing.assert_series_equal(ser, v1, check_names=False,
                                       obj=f"{k} early row changed by future close")


# --------------------------------------------------------------------------- #
# Synthetic-panel integration: the campaign loop honours every budget guardrail
# without Norgate.
# --------------------------------------------------------------------------- #
def _synth_panel(n_names=50, n_months=438, seed=7):
    idx = pd.date_range("1990-01-31", periods=n_months, freq="ME")
    cols = [f"N{i:03d}" for i in range(n_names)]
    rng = np.random.default_rng(seed)
    close = pd.DataFrame(
        100 * np.cumprod(1 + 0.0015 + 0.05 * rng.standard_normal((n_months, n_names)), axis=0),
        index=idx, columns=cols)
    dv = pd.DataFrame(1e6 * (1 + rng.random((n_months, n_names))), index=idx, columns=cols)
    membership = pd.DataFrame(1.0, index=idx, columns=cols)
    spy = pd.Series(100 * np.cumprod(1 + 0.005 + 0.04 * rng.standard_normal(n_months)),
                    index=idx, name="Close")
    meta = pd.DataFrame({
        "gics_sector": [["Tech", "Financials", "Energy", "Health"][i % 4] for i in range(n_names)],
        "is_delisted": [(i % 5 == 0) for i in range(n_names)],
    }, index=cols)
    return P8C.Panel(close, dv, membership, meta, spy, True, [])


def test_campaign_respects_all_budget_guardrails():
    panel = _synth_panel()
    state, meta = P8C.run_campaign(panel)
    b = P8C.budget_report(state)
    assert b["experiments_registered"] <= P8C.MAX_TOTAL_EXPERIMENTS
    assert b["challenge_ok"], f"challenge fraction too low: {b['challenge_fraction']}"
    assert b["non_momentum_ok"], f"non-momentum fraction too low: {b['non_momentum_fraction']}"
    assert b["per_family_ok"], f"a family exceeded the cap: {b['per_family_counts']}"
    assert b["all_registered_before_scoring"] is True


def test_campaign_pre_registers_unique_fully_specified_experiments():
    panel = _synth_panel()
    state, _meta = P8C.run_campaign(panel)
    ids = [e.experiment_id for e in state.registry]
    assert len(ids) == len(set(ids)), "duplicate experiment ids"
    for e in state.registry:
        assert e.hypothesis and e.success_gate and e.stop_condition and e.owning_agent
        assert e.family in P8C.ALLOWED_FAMILIES
        # every scored experiment carries an allowed status
        assert e.status in P8C.ALLOWED_STATUSES


def test_campaign_challenges_target_the_carried_leads():
    panel = _synth_panel()
    state, _meta = P8C.run_campaign(panel)
    challenged = {e.challenges for e in state.registry if e.is_challenge and e.challenges}
    assert challenged, "no challenge experiments were registered"
    # at least one challenge references each priority lead family signal in cycle 1
    assert any("downside_vol" in c for c in challenged)
    assert any("low_vol" in c for c in challenged)


def test_per_family_cap_enforced_in_register():
    state = P8C.CampaignState()
    fam = P8C.FAM_LOW_VOL
    specs = [P8C._mk(f"X{i:03d}", 1, "NON_MOMENTUM", fam, "low_vol", "top_quintile",
                     "h", "g", "s") for i in range(P8C.MAX_PER_FAMILY + 5)]
    added = P8C._register(state, specs)
    assert len(added) == P8C.MAX_PER_FAMILY
    assert len(state.skipped) == 5
    assert all("family cap" in s["reason_skipped"] for s in state.skipped)


# --------------------------------------------------------------------------- #
# Decision rule.
# --------------------------------------------------------------------------- #
def _state_with(statuses):
    st = P8C.CampaignState()
    for i, s in enumerate(statuses):
        e = P8C._mk(f"F{i:02d}", 1, "SCAN", P8C.FAM_LOW_VOL, "low_vol", "top_quintile", "h", "g", "s")
        e.status = s
        st.registry.append(e)
    return st


def test_derive_recommendation_branches():
    assert P8C.derive_recommendation(False, True, P8C.CampaignState())[0] == "DATA_PANEL_BLOCKED"
    assert P8C.derive_recommendation(True, False, P8C.CampaignState())[0] == "ORCHESTRATOR_BLOCKED"
    assert P8C.derive_recommendation(True, True, _state_with(["CONFIRMED_SIGNAL", "REJECTED"]))[0] \
        == "CONFIRMED_SIGNAL_FOUND"
    assert P8C.derive_recommendation(True, True, _state_with(["WEAK", "REJECTED"]))[0] \
        == "LEADS_WEAK_KEEP_RESEARCH_ONLY"
    assert P8C.derive_recommendation(True, True, _state_with(["REJECTED", "REJECTED"]))[0] \
        == "SIGNAL_RESEARCH_REJECTED_ON_BROAD_DATA"


# --------------------------------------------------------------------------- #
# End-to-end: validate the committed Russell 3000 artifacts when present.
# --------------------------------------------------------------------------- #
_HAVE_REPORT = REPORT_JSON.exists()
skip_e2e = pytest.mark.skipif(not _HAVE_REPORT, reason="run the 8-C engine first (Russell 3000 report)")


@skip_e2e
def test_e2e_all_22_artifacts_emitted():
    for name in P8C.ARTIFACTS:
        assert (OUT_DIR / name).exists(), f"missing artifact {name}"
    assert len(P8C.ARTIFACTS) == 22


@skip_e2e
def test_e2e_recommendation_in_allowed_set():
    rep = json.loads(REPORT_JSON.read_text())
    assert rep["recommendation"] in P8C.ALLOWED_RECOMMENDATIONS


@skip_e2e
def test_e2e_budget_guardrails_in_report():
    rep = json.loads(REPORT_JSON.read_text())
    b = rep["budget"]
    assert b["experiments_registered"] <= 120
    assert b["challenge_ok"] and b["non_momentum_ok"] and b["per_family_ok"]


@skip_e2e
def test_e2e_broad_survivorship_panel():
    rep = json.loads(REPORT_JSON.read_text())
    ps = rep["panel_shape"]
    assert ps["symbols"] > 5000          # broad universe, not the S&P 500 sample
    assert ps["delisted"] > ps["active"] * 0.5    # survivorship-aware: many dead names
    assert rep["selected_universe"]["selected_universe"] == "Russell 3000 Current & Past"


@skip_e2e
def test_e2e_safety_flags_all_off():
    rep = json.loads(REPORT_JSON.read_text())
    s = rep["safety"]
    for k in ("broker_or_orders", "automation", "optimized_weights",
              "factor_signs_modified_after_results", "regime_activation", "fundamentals_used",
              "failed_experiments_hidden", "committed", "pushed", "paper_trader_touched",
              "gcp_touched", "packages_installed"):
        assert s[k] is False, f"safety flag {k} should be False"
    assert s["research_only"] is True and s["norgate_only"] is True


@skip_e2e
def test_e2e_failed_experiments_not_hidden():
    rep = json.loads(REPORT_JSON.read_text())
    with open(OUT_DIR / "failed_experiments.csv", newline="") as fh:
        rows = list(csv.DictReader(fh))
    # if anything was rejected, it must appear in the failed-experiments artifact
    if rep["rejected_signals"]:
        ids = {r["experiment_id"] for r in rows}
        assert set(rep["rejected_signals"]) & ids


@skip_e2e
def test_e2e_paper_contract_matches_confirmation():
    rep = json.loads(REPORT_JSON.read_text())
    with open(OUT_DIR / "paper_signal_contract.csv", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rep["confirmed_signals"]:
        assert rows and rows[0]["status"] == "NO_CONFIRMED_SIGNAL"
