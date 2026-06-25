"""Tests for the Phase 8-E sensitivity-aware multi-input signal factory.

Loaded by ABSOLUTE PATH via importlib so the tests are cwd-independent and do NOT require
norgatedata (the engine imports it only lazily inside the panel-build path, which these tests
never trigger). Pure-logic tests run everywhere; the synthetic-panel integration tests exercise
sensitivity estimation, cohort/shock materialisation, the cohort-aware matched control, and the
full campaign loop without Norgate; the end-to-end tests are guarded with skipif on the real
S&P 500 Current & Past report so they validate the committed artifacts when present.
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
_MODULE_PATH = _REPO_ROOT / "research" / "run_phase8e_sensitivity_aware_signal_factory.py"


def _load():
    spec = importlib.util.spec_from_file_location("phase8e_engine_test", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


P = _load()
OUT_DIR = P.DEFAULT_OUT_DIR
REPORT_JSON = OUT_DIR / "phase8e_sensitivity_aware_signal_factory.json"


# --------------------------------------------------------------------------- #
# Vocabulary / config.
# --------------------------------------------------------------------------- #
def test_recommendation_vocabulary_exact_and_ordered():
    assert P.ALLOWED_RECOMMENDATIONS == (
        "CONFIRMED_SENSITIVITY_SIGNAL_FOUND", "PROMISING_SENSITIVITY_SETUPS_NEED_MORE_VALIDATION",
        "NEEDS_EXTERNAL_PROVIDER_DATA", "SENSITIVITY_SIGNAL_RESEARCH_REJECTED",
        "NEEDS_HUMAN_PROVIDER_DECISION", "ASSESSMENT_FRAMEWORK_BLOCKED", "ERROR",
    )


def test_status_vocabulary_exact():
    assert P.ALLOWED_STATUSES == (
        "CONFIRMED_SENSITIVITY_SIGNAL", "PROMISING_SENSITIVITY_SETUP", "REJECTED",
        "NEEDS_PROVIDER_DATA", "BLOCKED")


def test_budget_constants():
    assert P.MAX_TOTAL_SETUPS == 200
    assert P.MAX_PER_FAMILY == 60
    assert P.CHALLENGE_MIN_FRAC == 0.30
    assert P.MAX_CYCLES == 3


def test_gate_thresholds_are_apriori():
    assert P.GATE_MIN_EVENTS_TOTAL == 1000
    assert P.GATE_MIN_EVENTS_RECENT == 100
    assert P.GATE_HIT_RATE_LIFT_PP == 0.03
    assert P.GATE_MIN_WF_FOLDS_POSITIVE == 2
    assert P.RECENT_LABEL == "2015-2026"


def test_horizons_and_grid():
    assert P.FWD_HORIZONS == (5, 10, 20, 60)
    assert P.GRID_FREQ.startswith("W-")


def test_roundtrip_cost_is_two_way():
    assert P._roundtrip_cost(25.0) == pytest.approx(2.0 * 25.0 / 1e4)


# --------------------------------------------------------------------------- #
# Families + agents + drivers + cohorts.
# --------------------------------------------------------------------------- #
def test_families_and_provider_families():
    assert len(P.ALLOWED_FAMILIES) == 4
    assert P.FAM_MACRO_SENS in P.ALLOWED_FAMILIES
    for fam in (P.FAM_REVISION_SENS, P.FAM_NEWS_SENS, P.FAM_OPTIONS_SENS):
        assert fam in P.PROVIDER_FAMILIES


def test_agent_roster_has_sensitivity_and_provider_agents():
    assert len(P.ALL_AGENTS) == 12
    assert P.SENS_A in P.ALL_AGENTS and P.MACRO_A in P.ALL_AGENTS and P.PROV_A in P.ALL_AGENTS
    assert P.agent_for(P.FAM_MACRO_SENS, True) == P.VAL_A
    assert P.agent_for(P.FAM_MACRO_SENS, False) == P.MACRO_A


def test_driver_catalog_has_local_and_provider_drivers():
    keys = {d.key for d in P.DRIVER_CATALOG}
    # macro/cross-asset proxies must be present and LOCAL_READY
    for k in ("market", "oil", "rates", "credit", "usd", "vix", "commodity", "sector"):
        assert k in keys
        assert P.DRIVER_BY_KEY[k].availability == "LOCAL_READY"
    # the missing families must be pre-registered as NEEDS_PROVIDER (never faked)
    for k in ("analyst_revision", "news_sentiment", "options_iv", "short_interest"):
        assert P.DRIVER_BY_KEY[k].availability == "NEEDS_PROVIDER"
        assert P.DRIVER_BY_KEY[k].proxy is None


def test_sensitivity_direction_is_estimated_not_hardcoded():
    """No driver hardcodes an economic sign; cohorts are quantiles of an ESTIMATED rolling beta."""
    rows = P._cohort_catalog_rows()
    assert rows
    for r in rows:
        assert "rolling" in r["estimated_from"] and "beta" in r["estimated_from"]
        assert r["side"] in ("high", "low")


def test_cohort_catalog_covers_required_cohorts():
    labels = {c.label for c in P.COHORT_CATALOG}
    for needed in ("oil_positive_sensitive", "oil_negative_sensitive", "rates_positive_sensitive",
                   "rates_negative_sensitive", "credit_stress_sensitive", "high_beta_market_sensitive",
                   "low_beta_defensive", "volatility_spike_sensitive", "dollar_positive_sensitive",
                   "sector_leadership_sensitive"):
        assert needed in labels


# --------------------------------------------------------------------------- #
# Multiple-testing deflation: monotone.
# --------------------------------------------------------------------------- #
def test_mt_required_lift_monotonic():
    a, b, c = P.mt_required_lift(10), P.mt_required_lift(100), P.mt_required_lift(1000)
    assert a <= b <= c
    assert a == pytest.approx(P.GATE_MIN_LIFT)
    assert b == pytest.approx(P.GATE_MIN_LIFT * 2.0)


# --------------------------------------------------------------------------- #
# Leak-safety: a FUTURE close cannot change an EARLY-row sensitivity beta.
# --------------------------------------------------------------------------- #
def test_symbol_sensitivities_are_leak_safe():
    idx = pd.bdate_range("2000-01-03", periods=500)
    rng = np.random.default_rng(3)
    oil = pd.Series(50 * np.cumprod(1 + 0.02 * rng.standard_normal(500)), index=idx)
    oil_ret = oil.pct_change()
    close = pd.Series(40 * np.cumprod(1 + 0.0002 + 0.01 * rng.standard_normal(500)
                                      + 1.2 * oil_ret.fillna(0).to_numpy()), index=idx)
    proxy_ret = {P.DRIVER_BY_KEY[d].proxy: pd.Series(0.01 * rng.standard_normal(500), index=idx)
                 for d in P.SENS_DRIVERS}
    proxy_ret["USO"] = oil_ret
    s0, _ = P.symbol_sensitivities(close, "Energy", proxy_ret)
    early = s0.iloc[300].copy()
    close2 = close.copy(); close2.iloc[450] *= 4.0   # corrupt a far-future close
    s1, _ = P.symbol_sensitivities(close2, "Energy", proxy_ret)
    pd.testing.assert_series_equal(early, s1.iloc[300], check_names=False)


def test_rolling_beta_recovers_known_loading():
    idx = pd.bdate_range("2000-01-03", periods=800)
    rng = np.random.default_rng(11)
    x = pd.Series(0.01 * rng.standard_normal(800), index=idx)
    y = 1.5 * x + 0.001 * rng.standard_normal(800)    # true beta 1.5
    beta = P._rolling_beta(y, x).dropna()
    assert beta.iloc[-1] == pytest.approx(1.5, abs=0.15)


# --------------------------------------------------------------------------- #
# Inventory / gap / provider-plan (Parts A/B).
# --------------------------------------------------------------------------- #
def test_scan_local_inventory_runs_readonly():
    rows = P.scan_local_inventory()
    assert rows and all("path" in r and "exists" in r and "populated" in r for r in rows)


def test_gap_report_marks_macro_local_and_missing_families_needs_provider():
    gap = P.gap_report_rows(P.scan_local_inventory())
    by = {r["family_key"]: r for r in gap}
    assert by["macro_cross_asset"]["availability"] == "LOCAL_READY"
    for fam in ("options_iv", "news", "sentiment"):
        assert by[fam]["availability"] == "NEEDS_PROVIDER"


def test_provider_plan_has_priority_and_schema():
    rows = P.provider_acquisition_rows()
    assert rows
    assert [r["priority"] for r in rows] == sorted(r["priority"] for r in rows)
    for r in rows:
        assert r["minimal_schema"] and r["providers"] and "availability_date" in r["point_in_time_requirement"]


# --------------------------------------------------------------------------- #
# Driver-shock grid (date-level, leak-safe).
# --------------------------------------------------------------------------- #
def test_build_driver_shock_grid_columns():
    idx = pd.bdate_range("2005-01-03", periods=900)
    rng = np.random.default_rng(2)
    proxy_close = {}
    for d in P.SHOCK_DRIVERS:
        proxy = P.DRIVER_BY_KEY[d].proxy
        proxy_close[proxy] = pd.Series(100 * np.cumprod(1 + 0.01 * rng.standard_normal(900)), index=idx)
    grid_dates = P._weekly_grid_dates(idx)
    sg = P.build_driver_shock_grid(proxy_close, grid_dates)
    for col in ("drv_oil_shock_z", "drv_vix_spike_z", "drv_rates_shock_z"):
        assert col in sg.columns
    # z-scores are finite for most of the (post-warmup) sample
    assert sg["drv_oil_shock_z"].notna().mean() > 0.4


# --------------------------------------------------------------------------- #
# Classification gate (the CONFIRMED_SENSITIVITY_SIGNAL gate).
# --------------------------------------------------------------------------- #
def _setup(driver="oil", cohort="cohort_oil_pos", needs_provider=False, is_challenge=False,
           placebo=False):
    return P._mk("T001", 1, P.FAM_MACRO_SENS, driver, cohort, 20,
                 [("drv_oil_shock_z", "ge", 1.0), ("cohort_oil_pos", "ge", 1.0)],
                 "h", is_challenge=is_challenge, placebo=placebo, needs_provider=needs_provider)


def _good_ev(**over):
    ev = {
        "n_events": 5000, "n_recent_events": 800, "is_challenge": False, "placebo": False,
        "ev_after_25bps": 0.004, "lift_vs_control": 0.004, "lift_vs_base_rate": 0.003,
        "hit_rate_lift_pp": 0.05, "payoff_ratio": 1.3, "control_payoff_ratio": 1.1,
        "worst_decile_mean": -0.06, "recent_lift_vs_control": 0.003,
        "max_year_fraction": 0.15, "max_sector_fraction": 0.30, "max_ticker_fraction": 0.02,
    }
    ev.update(over)
    return ev


def test_classify_confirmed_requires_driver_and_cohort():
    st, _r, checks = P.classify_setup(_good_ev(), {"n_folds_positive": 3},
                                      {"beats_spy_active": True, "beats_cash_active": True},
                                      n_search=25, setup=_setup())
    assert st == "CONFIRMED_SENSITIVITY_SIGNAL"
    assert checks["uses_external_driver_and_cohort"] is True
    assert all(checks.values())


def test_classify_rejected_without_cohort():
    st, _r, _c = P.classify_setup(_good_ev(), {"n_folds_positive": 3},
                                  {"beats_spy_active": True, "beats_cash_active": True},
                                  n_search=25, setup=_setup(cohort=""))
    assert st == "REJECTED"


def test_classify_needs_provider():
    st, _r, _c = P.classify_setup({}, {}, {}, n_search=25, setup=_setup(needs_provider=True))
    assert st == "NEEDS_PROVIDER_DATA"


def test_classify_challenge_is_diagnostic():
    st, _r, _c = P.classify_setup(_good_ev(is_challenge=True), {"n_folds_positive": 3},
                                  {"beats_spy_active": True, "beats_cash_active": True},
                                  n_search=25, setup=_setup(is_challenge=True))
    assert st == "REJECTED"


def test_classify_rejected_on_recency_and_ev_and_events():
    base_wf = {"n_folds_positive": 3}
    base_port = {"beats_spy_active": True, "beats_cash_active": True}
    assert P.classify_setup(_good_ev(recent_lift_vs_control=-0.001), base_wf, base_port,
                            25, _setup())[0] == "REJECTED"
    assert P.classify_setup(_good_ev(ev_after_25bps=-0.001), base_wf, base_port,
                            25, _setup())[0] == "REJECTED"
    assert P.classify_setup(_good_ev(n_recent_events=20), base_wf, base_port,
                            25, _setup())[0] == "REJECTED"


def test_classify_promising_when_misses_secondary_gate():
    st, _r, checks = P.classify_setup(_good_ev(), {"n_folds_positive": 3},
                                      {"beats_spy_active": False, "beats_cash_active": False},
                                      n_search=25, setup=_setup())
    assert st == "PROMISING_SENSITIVITY_SETUP"
    assert checks["portfolio_beats_spy_and_cash_active"] is False


def test_classify_blocked_when_no_events():
    st, _r, _c = P.classify_setup({"n_events": 0}, {}, {}, n_search=25, setup=_setup())
    assert st == "BLOCKED"


# --------------------------------------------------------------------------- #
# Synthetic-panel integration: sensitivities -> cohorts -> shocks -> campaign.
# --------------------------------------------------------------------------- #
def _synth_panel(n_sym=90, seed=7, plant=True):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2000-01-03", "2026-05-29")
    sectors = ["Energy", "Information Technology", "Financials", "Health Care"]
    spy = pd.Series(100 * np.cumprod(1 + 0.0003 + 0.009 * rng.standard_normal(len(days))), index=days)
    oil_ret = 0.02 * rng.standard_normal(len(days))
    oil_close = pd.Series(50 * np.cumprod(1 + oil_ret), index=days)
    proxy_close, proxy_ret = {}, {}
    for d in P.SENS_DRIVERS:
        proxy = P.DRIVER_BY_KEY[d].proxy
        s = oil_close if d == "oil" else pd.Series(
            100 * np.cumprod(1 + 0.0002 + 0.012 * rng.standard_normal(len(days))), index=days)
        proxy_close[proxy] = s; proxy_ret[proxy] = s.pct_change()
    for etf in set(P.SECTOR_ETF.values()):
        s = pd.Series(100 * np.cumprod(1 + 0.0002 + 0.011 * rng.standard_normal(len(days))), index=days)
        proxy_close[etf] = s; proxy_ret[etf] = s.pct_change()
    grid_dates = P._weekly_grid_dates(days)
    shock_grid = P.build_driver_shock_grid(proxy_close, grid_dates)
    feat_cols = [c for c, _a, _d in P.P8D.FEATURE_CATALOG]
    label_cols = [c for c, _d in P.P8D.EVENT_LABELS]
    sens_cols = [f"sens_beta_{d}" for d in P.SENS_DRIVERS] + ["sens_beta_sector"]
    blocks, meta = [], []
    for i in range(n_sym):
        sector = sectors[i % len(sectors)]
        oil_load = 1.4 if i % 3 == 0 else 0.0
        rets = 0.0002 + 0.012 * rng.standard_normal(len(days)) + oil_load * oil_ret
        close = pd.Series(50 * np.cumprod(1 + rets), index=days)
        vol = pd.Series(1e6 * (1 + rng.random(len(days))), index=days)
        dvol = close * vol
        frame = pd.concat([P.symbol_features(close, vol, dvol, spy),
                           P.symbol_sensitivities(close, sector, proxy_ret)[0],
                           P.forward_labels(close, spy)], axis=1)
        frame = frame.reindex(grid_dates, method="ffill", limit=5).dropna(
            subset=["rv_20", "ret_60", "fwd_excess_20", "fwd_total_5", "sens_beta_market"])
        if frame.empty:
            continue
        frame = frame.reset_index().rename(columns={"index": "date"})
        if "date" not in frame.columns:
            frame = frame.rename(columns={frame.columns[0]: "date"})
        frame["symbol"] = f"S{i:03d}"; frame["sector"] = sector
        blocks.append(frame)
        meta.append({"ticker": f"S{i:03d}", "gics_sector": sector,
                     "sector_etf": P.SECTOR_ETF.get(sector, ""), "is_delisted": (i % 5 == 0)})
    grid = pd.concat(blocks, ignore_index=True)
    keep = ["date", "symbol", "sector"] + feat_cols + sens_cols + label_cols
    grid = grid[[c for c in keep if c in grid.columns]]
    grid = P._add_cross_sectional(grid, shock_grid)
    if plant:
        mask = (grid["cohort_oil_pos"] >= 1.0) & (grid["drv_oil_shock_z"] >= P.SHOCK_Z)
        for h in (5, 10, 20, 60):
            grid.loc[mask, f"fwd_excess_{h}"] = grid.loc[mask, f"fwd_excess_{h}"] + 0.02
        grid.loc[mask, "fwd_total_5"] = grid.loc[mask, "fwd_total_5"] + 0.02
    return P.SensPanel(grid, pd.DataFrame(meta).set_index("ticker"), spy, grid_dates, True,
                       pd.DataFrame(), [{"proxy": p, "status": "OK", "n_rows": len(days)} for p in proxy_close])


@pytest.fixture(scope="module")
def planted_panel():
    return _synth_panel(plant=True)


def test_cohort_and_shock_columns_materialised(planted_panel):
    g = planted_panel.grid
    for c in P.COHORT_COLS:
        assert c in g.columns
    for col in P.SHOCK_COL_NAMES:
        assert col in g.columns
    # cohorts are bounded quintiles -> roughly <= 25% of names on a given date
    assert g["cohort_oil_pos"].mean() <= 0.30


def test_matched_control_recovers_planted_sensitivity_edge(planted_panel):
    setup = P.plan_cycle_1()[0]                 # S8E-001 oil_pos
    ev = P.evaluate_sensitivity_setup(setup, planted_panel.grid)
    assert ev["n_events"] > 0
    assert ev["lift_vs_control"] is not None and ev["lift_vs_control"] > 0.01


def test_wrong_cohort_challenge_does_not_recover_edge(planted_panel):
    # S8E-901: same oil-up shock but oil-NEGATIVE cohort -> should NOT show the planted lift
    wrong = next(s for s in P.plan_cycle_1() if s.setup_id == "S8E-901")
    ev = P.evaluate_sensitivity_setup(wrong, planted_panel.grid)
    assert (ev["lift_vs_control"] or 0) < 0.01


def test_campaign_respects_budget_guardrails(planted_panel):
    state, _m = P.run_campaign(planted_panel)
    b = P.budget_report(state)
    assert b["setups_registered"] <= P.MAX_TOTAL_SETUPS
    assert b["challenge_ok"], f"challenge fraction too low: {b['challenge_fraction']}"
    assert b["per_family_ok"], f"a family exceeded the cap: {b['per_family_counts']}"
    assert b["n_needs_provider"] >= 3


def test_campaign_pre_registers_unique_setups_with_driver(planted_panel):
    state, _m = P.run_campaign(planted_panel)
    ids = [e.setup_id for e in state.registry]
    assert len(ids) == len(set(ids))
    for e in state.registry:
        assert e.hypothesis and e.owning_agent and e.driver
        assert e.family in P.ALLOWED_FAMILIES
        assert e.status in P.ALLOWED_STATUSES
        # every testable (non-provider, non-challenge) candidate uses a sensitivity cohort
        if not e.needs_provider and not e.is_challenge:
            assert e.cohort, f"{e.setup_id} has no sensitivity cohort"


def test_provider_setups_marked_needs_provider(planted_panel):
    state, _m = P.run_campaign(planted_panel)
    prov = [e for e in state.registry if e.needs_provider]
    assert prov and all(e.status == P.ST_NEEDS_PROVIDER for e in prov)
    assert all(e.provider_note for e in prov)


def test_pure_noise_yields_no_confirmed(planted_panel):
    noise = _synth_panel(seed=123, plant=False)
    state, _m = P.run_campaign(noise)
    assert not [e for e in state.registry if e.status == P.ST_CONFIRMED]


def test_per_family_cap_enforced_in_register():
    state = P.CampaignState()
    specs = [P._mk(f"X{i:03d}", 1, P.FAM_MACRO_SENS, "oil", "cohort_oil_pos", 20,
                   [("cohort_oil_pos", "ge", 1.0)], "h")
             for i in range(P.MAX_PER_FAMILY + 5)]
    added = P._register(state, specs)
    assert len(added) == P.MAX_PER_FAMILY
    assert len(state.skipped) == 5


# --------------------------------------------------------------------------- #
# Decision rule.
# --------------------------------------------------------------------------- #
def _state_with(statuses):
    st = P.CampaignState()
    for i, s in enumerate(statuses):
        prov = (s == P.ST_NEEDS_PROVIDER)
        e = P._mk(f"F{i:02d}", 1, P.FAM_MACRO_SENS, "oil", "cohort_oil_pos", 20,
                  [("cohort_oil_pos", "ge", 1.0)], "h", needs_provider=prov)
        e.status = s
        st.registry.append(e)
    return st


def test_derive_recommendation_branches():
    assert P.derive_recommendation(True, False, True, P.CampaignState())[0] == "ASSESSMENT_FRAMEWORK_BLOCKED"
    assert P.derive_recommendation(True, True, False, P.CampaignState())[0] == "NEEDS_HUMAN_PROVIDER_DECISION"
    assert P.derive_recommendation(True, True, True, _state_with([P.ST_CONFIRMED]))[0] \
        == "CONFIRMED_SENSITIVITY_SIGNAL_FOUND"
    assert P.derive_recommendation(True, True, True, _state_with([P.ST_PROMISING, P.ST_REJECTED]))[0] \
        == "PROMISING_SENSITIVITY_SETUPS_NEED_MORE_VALIDATION"
    # macro tested & rejected but provider families remain -> NEEDS_EXTERNAL_PROVIDER_DATA
    assert P.derive_recommendation(True, True, True, _state_with([P.ST_REJECTED, P.ST_NEEDS_PROVIDER]))[0] \
        == "NEEDS_EXTERNAL_PROVIDER_DATA"
    # macro tested & rejected, no provider families -> REJECTED
    assert P.derive_recommendation(True, True, True, _state_with([P.ST_REJECTED, P.ST_REJECTED]))[0] \
        == "SENSITIVITY_SIGNAL_RESEARCH_REJECTED"


# --------------------------------------------------------------------------- #
# End-to-end: validate the committed S&P 500 Current & Past artifacts when present.
# --------------------------------------------------------------------------- #
_HAVE_REPORT = REPORT_JSON.exists()
skip_e2e = pytest.mark.skipif(not _HAVE_REPORT, reason="run the 8-E engine first (S&P 500 report)")


@skip_e2e
def test_e2e_all_20_artifacts_emitted():
    for name in P.ARTIFACTS:
        assert (OUT_DIR / name).exists(), f"missing artifact {name}"
    assert len(P.ARTIFACTS) == 20


@skip_e2e
def test_e2e_recommendation_in_allowed_set():
    rep = json.loads(REPORT_JSON.read_text())
    assert rep["recommendation"] in P.ALLOWED_RECOMMENDATIONS


@skip_e2e
def test_e2e_budget_guardrails_in_report():
    rep = json.loads(REPORT_JSON.read_text())
    b = rep["budget"]
    assert b["setups_registered"] <= 200
    assert b["challenge_ok"] and b["per_family_ok"]


@skip_e2e
def test_e2e_event_driven_survivorship_panel():
    rep = json.loads(REPORT_JSON.read_text())
    ps = rep["panel_shape"]
    assert ps["panel_ok"] is True
    assert ps["n_grid_observations"] > 100000
    assert ps["delisted"] > 0
    assert rep["selected_universe"]["selected_universe"] == "S&P 500 Current & Past"


@skip_e2e
def test_e2e_external_proxies_loaded_and_news_absent():
    rep = json.loads(REPORT_JSON.read_text())
    ed = rep["external_data"]
    assert ed["proxy_coverage_ok"] >= 15            # Norgate macro proxies live
    assert ed["news_or_sentiment_local"] is False   # honest: no local news/sentiment


@skip_e2e
def test_e2e_safety_flags_all_off():
    rep = json.loads(REPORT_JSON.read_text())
    s = rep["safety"]
    for k in ("network_or_paid_api_used", "packages_installed", "external_data_faked",
              "always_on_factor_test", "optimized_weights", "factor_signs_modified_after_results",
              "regime_activation", "ml_fit", "holdout_feedback_used_to_tune_thresholds",
              "failed_experiments_hidden", "live_trading_signals", "broker_or_orders", "automation",
              "paper_trader_touched", "gcp_touched", "committed", "pushed"):
        assert s[k] is False, f"safety flag {k} should be False"
    assert s["research_only"] is True


@skip_e2e
def test_e2e_needs_provider_templates_present_and_plan_emitted():
    with open(OUT_DIR / "provider_acquisition_plan.csv", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows, "provider acquisition plan is empty"
    with open(OUT_DIR / "setup_experiment_registry.csv", newline="") as fh:
        reg = list(csv.DictReader(fh))
    assert any(r["needs_provider"] in ("True", "true", "1") for r in reg)


@skip_e2e
def test_e2e_failed_setups_not_hidden():
    rep = json.loads(REPORT_JSON.read_text())
    with open(OUT_DIR / "failed_sensitivity_setups.csv", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if rep["setups"]["rejected_signals"]:
        ids = {r["setup_id"] for r in rows}
        assert set(rep["setups"]["rejected_signals"]) & ids


@skip_e2e
def test_e2e_sensitivity_map_and_cohorts_present():
    with open(OUT_DIR / "ticker_external_sensitivity_map.csv", newline="") as fh:
        sm = list(csv.DictReader(fh))
    assert sm, "sensitivity map empty"
    drivers = {r["driver"] for r in sm}
    assert "market" in drivers and "oil" in drivers
    with open(OUT_DIR / "sensitivity_allotments.csv", newline="") as fh:
        al = list(csv.DictReader(fh))
    assert any(r["cohort"] == "cohort_oil_pos" for r in al)
