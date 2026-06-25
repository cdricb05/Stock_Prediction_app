"""Tests for Phase 8-G — External Data Activation and Signal Confirmation Campaign.

Covers: decision/status vocab, agents/families/artifacts, REAL local-data activation (earnings
cache load + PIT normalization), the labelled revision PROXY (never CONFIRMED), the leak-safe
merge_asof join (no future leakage; cohort uses only prior reactivity; persistent ticker flag),
the pre-registered setups + challenge fraction, the promotion ladder, the blocked provider
families, recommendation branches, budget/model-registry/safety guardrails, the S8E-011 external
confirmation overlay, and a synthetic end-to-end emitting all 25 artifacts with no network.
"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


G = _load("phase8g_under_test", "research/run_phase8g_external_data_activation_campaign.py")
P = G.P8E
F = G.P8F


# --------------------------------------------------------------------------- #
# Synthetic 8-E panel (planted oil edge) + synthetic earnings — no network, no D: panel.
# --------------------------------------------------------------------------- #
def _synth_panel(n_sym=70, seed=5):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("1996-01-01", "2026-06-30")
    sectors = ["Energy", "Information Technology", "Financials", "Health Care", "Industrials"]
    spy_close = pd.Series(100 * np.cumprod(1 + 0.0003 + 0.009 * rng.standard_normal(len(days))), index=days)
    oil_ret = 0.02 * rng.standard_normal(len(days))
    oil_close = pd.Series(50 * np.cumprod(1 + oil_ret), index=days)
    proxy_close, proxy_ret = {}, {}
    for d in P.SENS_DRIVERS:
        proxy = P.DRIVER_BY_KEY[d].proxy
        s = oil_close if d == "oil" else pd.Series(
            100 * np.cumprod(1 + 0.0002 + 0.012 * rng.standard_normal(len(days))), index=days)
        proxy_close[proxy] = s
        proxy_ret[proxy] = s.pct_change()
    for etf in set(P.SECTOR_ETF.values()):
        s = pd.Series(100 * np.cumprod(1 + 0.0002 + 0.011 * rng.standard_normal(len(days))), index=days)
        proxy_close[etf] = s
        proxy_ret[etf] = s.pct_change()
    grid_dates = P._weekly_grid_dates(days)
    shock_grid = P.build_driver_shock_grid(proxy_close, grid_dates)
    feat_cols = [c for c, _a, _d in P.P8D.FEATURE_CATALOG]
    label_cols = [c for c, _d in P.P8D.EVENT_LABELS]
    sens_cols = [f"sens_beta_{d}" for d in P.SENS_DRIVERS] + ["sens_beta_sector"]
    blocks, meta = [], []
    for i in range(n_sym):
        sector = sectors[i % len(sectors)]
        oil_load = 1.4 if i % 3 == 0 else 0.0
        rets = (0.0002 + 0.0006 * rng.standard_normal()) + 0.012 * rng.standard_normal(len(days)) + oil_load * oil_ret
        close = pd.Series(50 * np.cumprod(1 + rets), index=days)
        vol = pd.Series(1e6 * (1 + rng.random(len(days))), index=days)
        feats = P.symbol_features(close, vol, close * vol, spy_close)
        sens, _ = P.symbol_sensitivities(close, sector, proxy_ret)
        labels = P.forward_labels(close, spy_close)
        frame = pd.concat([feats, sens, labels], axis=1).reindex(grid_dates, method="ffill", limit=5)
        frame = frame.dropna(subset=["rv_20", "ret_60", "fwd_excess_20", "fwd_total_5", "sens_beta_market"])
        if frame.empty:
            continue
        frame = frame.reset_index().rename(columns={"index": "date"})
        if "date" not in frame.columns:
            frame = frame.rename(columns={frame.columns[0]: "date"})
        frame["symbol"] = f"S{i:03d}"
        frame["sector"] = sector
        blocks.append(frame)
        meta.append({"ticker": f"S{i:03d}", "gics_sector": sector,
                     "sector_etf": P.SECTOR_ETF.get(sector, ""), "first_quoted_date": "1996-01-01",
                     "last_quoted_date": "", "is_delisted": (i % 5 == 0), "n_grid_obs": len(frame)})
    grid = pd.concat(blocks, ignore_index=True)
    keep = ["date", "symbol", "sector"] + feat_cols + sens_cols + label_cols
    grid = grid[[c for c in keep if c in grid.columns]]
    grid = P._add_cross_sectional(grid, shock_grid)
    mask = (grid["cohort_oil_pos"] >= 1.0) & (grid["drv_oil_shock_z"] >= P.SHOCK_Z)
    for h in (5, 10, 20, 60):
        grid.loc[mask, f"fwd_excess_{h}"] += 0.02
    grid.loc[mask, "fwd_total_5"] += 0.02
    return F.SensPanel(grid, pd.DataFrame(meta).set_index("ticker"), spy_close, grid_dates, True,
                       pd.DataFrame(), [])


def _synth_earnings(panel, seed=7):
    """Quarterly earnings events landing on real grid dates of the panel's symbols (joins cleanly)."""
    rng = np.random.default_rng(seed)
    gd = panel.grid_dates
    rows = []
    for sym in sorted(panel.grid["symbol"].unique())[:40]:
        sdates = sorted(panel.grid.loc[panel.grid["symbol"] == sym, "date"].unique())
        if len(sdates) < 12:
            continue
        picks = sdates[::13][:24]                      # ~quarterly
        for j, dt in enumerate(picks):
            sp = float(rng.normal(2.0, 6.0))
            rows.append({
                "ticker": sym, "fiscal_date_ending": str(pd.Timestamp(dt).date()),
                "reported_date": str(pd.Timestamp(dt).date()),
                "availability_date": pd.Timestamp(dt), "reported_eps": 1.0, "estimated_eps": 0.95,
                "eps_surprise_pct": sp, "surprise_acceleration": float(rng.normal(0, 30)),
                "estimate_revision_proxy": np.nan, "provider": "synthetic_test",
                "point_in_time_usable": True})
    return pd.DataFrame(rows).sort_values(["ticker", "availability_date"]).reset_index(drop=True)


@pytest.fixture(scope="module")
def panel():
    return _synth_panel()


@pytest.fixture(scope="module")
def earn(panel):
    return _synth_earnings(panel)


@pytest.fixture(scope="module")
def aug(panel, earn):
    grid, diag = G.augment_grid(panel.grid, earn, pd.DataFrame())
    return grid, diag


# --------------------------------------------------------------------------- #
# Vocabulary / structure.
# --------------------------------------------------------------------------- #
def test_recommendation_vocab_exact_and_ordered():
    assert G.ALLOWED_RECOMMENDATIONS == (
        "CONFIRMED_EXTERNAL_SIGNAL_FOUND", "PROMISING_EXTERNAL_SIGNAL_FOUND",
        "PROMISING_NEEDS_PROVIDER_HISTORY", "NEEDS_NEWS_SENTIMENT_PROVIDER_DATA",
        "NEEDS_ANALYST_REVISION_PROVIDER_DATA", "NEEDS_EXTERNAL_PROVIDER_DATA",
        "EXTERNAL_SIGNAL_RESEARCH_REJECTED", "PROVIDER_ACCESS_BLOCKED", "ERROR")
    assert len(set(G.ALLOWED_RECOMMENDATIONS)) == len(G.ALLOWED_RECOMMENDATIONS)


def test_status_vocab():
    assert G.ST_EXT_CONFIRMED in G.ALLOWED_STATUSES
    assert G.ST_EXT_PROMISING in G.ALLOWED_STATUSES
    assert G.ST_NEEDS_HISTORY in G.ALLOWED_STATUSES


def test_agents_and_families():
    assert len(G.ALL_AGENTS) == 15
    for fam in (G.FAM_EARNINGS, G.FAM_REVISION, G.FAM_NEWS, G.FAM_S8E011_EXT, G.FAM_FILINGS):
        assert fam in G.ALL_FAMILIES


def test_artifacts_list_is_25_unique():
    assert len(G.ARTIFACTS) == 25
    assert len(set(G.ARTIFACTS)) == 25
    for name in ("earnings_surprise_event_panel.csv", "s8e011_external_confirmation_scoreboard.csv",
                 "research_director_decision.json", "phase8h_next_plan.json"):
        assert name in G.ARTIFACTS


def test_proxy_or_thin_families():
    assert G.FAM_REVISION in G.PROXY_OR_THIN_FAMILIES       # proxy: never CONFIRMED
    assert G.FAM_FILINGS in G.PROXY_OR_THIN_FAMILIES


# --------------------------------------------------------------------------- #
# Real local-data activation.
# --------------------------------------------------------------------------- #
def test_local_artifact_inventory_keys_and_pit():
    rows = G.local_artifact_inventory()
    assert rows
    for r in rows:
        assert set(r) >= {"artifact", "family", "path", "exists", "n_rows", "point_in_time_field",
                          "point_in_time_usable"}
    earn = next(r for r in rows if r["artifact"] == "earnings_surprise")
    # the real cache exists on disk and is point-in-time usable
    assert earn["exists"] is True and earn["point_in_time_usable"] is True


def test_load_real_earnings_events_pit_clean():
    df = G.load_earnings_events()
    assert not df.empty
    assert pd.api.types.is_datetime64_any_dtype(df["availability_date"])
    assert df["eps_surprise_pct"].notna().all()
    assert df["availability_date"].notna().all()


def test_normalized_earnings_schema_and_provenance(earn):
    panel = G.normalized_earnings_panel(earn)
    assert list(panel.columns) == F.SCHEMA_EARNINGS
    assert (panel["point_in_time_available_at"].astype(str).str.len() >= 8).all()
    assert panel["raw_payload_path"].str.startswith("LOCAL_CACHE").all()


def test_revision_proxy_is_labelled_proxy(earn):
    rev = G.normalized_revision_proxy_panel(earn)
    assert list(rev.columns) == F.SCHEMA_REVISION
    if not rev.empty:
        assert (rev["provider_id"] == "PROXY_LOCAL").all()
        assert rev["raw_payload_path"].str.startswith("LOCAL_PROXY").all()


# --------------------------------------------------------------------------- #
# Leak-safe join.
# --------------------------------------------------------------------------- #
def test_augment_adds_event_columns(aug):
    grid, diag = aug
    for c in ("earn_event", "earn_surprise_pos", "cohort_surprise_sensitive",
              "tkr_surprise_sensitive", "earn_recent_pos"):
        assert c in grid.columns
    assert diag["n_earn_event_obs"] > 0


def test_cohort_only_on_event_rows(aug):
    grid, _ = aug
    # surprise-sensitivity is only ever set where an earnings event was observed
    assert (grid.loc[grid["cohort_surprise_sensitive"] > 0, "earn_event"] > 0).all()


def test_join_is_backward_no_future_leak(panel):
    # an event strictly in the future of all grid dates must NOT attach to any observation
    sym = sorted(panel.grid["symbol"].unique())[0]
    future = pd.DataFrame([{"ticker": sym, "availability_date": pd.Timestamp("2099-01-01"),
                            "eps_surprise_pct": 50.0, "surprise_acceleration": 10.0,
                            "reported_date": "2099-01-01", "fiscal_date_ending": "2099-01-01",
                            "reported_eps": 1.0, "estimated_eps": 0.5, "provider": "t",
                            "point_in_time_usable": True}])
    grid, diag = G.augment_grid(panel.grid, future, pd.DataFrame())
    assert diag["n_earn_event_obs"] == 0


def test_surprise_cohort_needs_prior_events(panel, earn):
    grid, _ = G.augment_grid(panel.grid, earn, pd.DataFrame())
    ev = grid[grid["earn_event"] > 0].sort_values(["symbol", "date"])
    # the first MIN_PRIOR_EVENTS events of every ticker cannot be flagged sensitive (no prior history)
    firsts = ev.groupby("symbol").head(G.MIN_PRIOR_EVENTS)
    assert (firsts["cohort_surprise_sensitive"] == 0).all()


def test_ticker_flag_is_persistent_cummax(aug):
    grid, _ = aug
    for sym, sub in grid.groupby("symbol"):
        s = sub.sort_values("date")["tkr_surprise_sensitive"].to_numpy()
        assert np.all(np.diff(s) >= 0)                      # non-decreasing (cummax)


# --------------------------------------------------------------------------- #
# Setups / promotion / blocked families.
# --------------------------------------------------------------------------- #
def test_plan_setups_challenge_fraction_and_shape():
    setups = G.plan_external_setups()
    testable = [s for s in setups if not s.is_challenge]
    challenges = [s for s in setups if s.is_challenge]
    assert len(challenges) / len(testable) >= G.CHALLENGE_MIN_FRAC
    f20 = next(s for s in setups if s.setup_id == "S8G-F20")
    assert f20.driver and f20.cohort                       # confirmed-eligible needs both
    assert any(s.placebo for s in challenges)


def test_promotion_ladder():
    ev_pos = {"lift_vs_control": 0.005, "ev_after_25bps": 0.003, "recent_lift_vs_control": 0.004}
    ev_pos_norecent = {"lift_vs_control": 0.005, "ev_after_25bps": 0.003, "recent_lift_vs_control": -0.001}
    ev_neg = {"lift_vs_control": -0.001, "ev_after_25bps": -0.004, "recent_lift_vs_control": -0.001}
    # CONFIRMED on real non-proxy family -> external confirmed
    assert G._promotion_for(G.FAM_EARNINGS, P.ST_CONFIRMED, ev_pos, True) == G.ST_EXT_CONFIRMED
    # CONFIRMED on a proxy family -> capped to needs-history
    assert G._promotion_for(G.FAM_REVISION, P.ST_CONFIRMED, ev_pos, True) == G.ST_NEEDS_HISTORY
    # positive lift+EV+recency, real, non-proxy -> promising
    assert G._promotion_for(G.FAM_S8E011_EXT, P.ST_REJECTED, ev_pos, True) == G.ST_EXT_PROMISING
    # positive lift+EV but weak recency -> needs-history
    assert G._promotion_for(G.FAM_EARNINGS, P.ST_REJECTED, ev_pos_norecent, True) == G.ST_NEEDS_HISTORY
    # negative -> rejected
    assert G._promotion_for(G.FAM_EARNINGS, P.ST_REJECTED, ev_neg, True) == G.ST_REJECTED
    # needs-provider passthrough
    assert G._promotion_for(G.FAM_NEWS, P.ST_NEEDS_PROVIDER, {}, False) == G.ST_NEEDS_PROVIDER


def test_blocked_families_are_needs_provider():
    blocked = G._blocked_family_experiments()
    fams = {e.family for e in blocked}
    assert fams == {G.FAM_NEWS, G.FAM_OPTIONS, G.FAM_SHORT}
    assert all(e.status == P.ST_NEEDS_PROVIDER for e in blocked)


def _exp(family, promotion, needs_provider=False, n_events=10):
    return G.Experiment(exp_id="X", cycle=1, family=family, agent="a", driver="d", cohort="c",
                        is_challenge=False, real_external_data=not needs_provider,
                        needs_provider=needs_provider, hypothesis="h", status="s",
                        promotion=promotion, metrics={"n_events": n_events})


def test_recommendation_branches():
    rd = {f: False for f in F.FAMILY_PROVIDERS}
    assert G.derive_recommendation(True, [_exp(G.FAM_EARNINGS, G.ST_EXT_CONFIRMED)], rd)[0] == \
        "CONFIRMED_EXTERNAL_SIGNAL_FOUND"
    assert G.derive_recommendation(True, [_exp(G.FAM_S8E011_EXT, G.ST_EXT_PROMISING)], rd)[0] == \
        "PROMISING_EXTERNAL_SIGNAL_FOUND"
    assert G.derive_recommendation(True, [_exp(G.FAM_REVISION, G.ST_NEEDS_HISTORY)], rd)[0] == \
        "PROMISING_NEEDS_PROVIDER_HISTORY"
    assert G.derive_recommendation(True, [_exp(G.FAM_REVISION, G.ST_NEEDS_PROVIDER, True)], rd)[0] == \
        "NEEDS_ANALYST_REVISION_PROVIDER_DATA"
    assert G.derive_recommendation(True, [_exp(G.FAM_NEWS, G.ST_NEEDS_PROVIDER, True)], rd)[0] == \
        "NEEDS_NEWS_SENTIMENT_PROVIDER_DATA"
    assert G.derive_recommendation(True, [_exp(G.FAM_EARNINGS, G.ST_REJECTED)], rd)[0] == \
        "EXTERNAL_SIGNAL_RESEARCH_REJECTED"


# --------------------------------------------------------------------------- #
# Guardrails.
# --------------------------------------------------------------------------- #
def test_budget_guardrails():
    ledger = G.run_external_experiments(*_grid_panel())
    b = G._budget(ledger)
    assert b["within_total_budget"] and b["per_family_ok"]
    assert b["challenge_ok"]


def test_model_registry_never_deploys():
    ledger = G.run_external_experiments(*_grid_panel())
    for r in G.model_candidate_update(ledger):
        assert r["deployed"] is False and r["paper_trader_output"] is False and r["production"] is False


def test_safety_block_all_forbidden_false():
    sb = G._safety_block({f: False for f in F.FAMILY_PROVIDERS}, {"live_succeeded": False})
    for k in ("external_data_faked", "factor_signs_modified_after_results", "optimized_weights",
              "regime_activation", "ml_fit", "broker_or_orders", "automation",
              "paper_trader_touched", "gcp_touched", "committed", "pushed", "secrets_printed"):
        assert sb[k] is False
    assert sb["revision_is_labelled_proxy_not_confirmed"] is True
    assert sb["point_in_time_join"] is True


# --------------------------------------------------------------------------- #
# S8E-011 external confirmation overlay.
# --------------------------------------------------------------------------- #
_GP_CACHE = {}


def _grid_panel():
    if "v" not in _GP_CACHE:
        pan = _synth_panel()
        ea = _synth_earnings(pan)
        grid, _ = G.augment_grid(pan.grid, ea, pd.DataFrame())
        _GP_CACHE["v"] = (grid, pan)
    return _GP_CACHE["v"]


def test_s8e011_confirmation_has_baseline_and_overlay():
    grid, pan = _grid_panel()
    rows = G.s8e011_external_confirmation(grid, pan, [])
    variants = [r["variant"] for r in rows]
    assert "S8E-011_baseline" in variants
    assert "S8E-011+earn_confirm" in variants
    assert any("remove_extreme_beta_tails" in v for v in variants)


# --------------------------------------------------------------------------- #
# End-to-end (synthetic; no network, no D: panel).
# --------------------------------------------------------------------------- #
def test_end_to_end_emits_all_artifacts(tmp_path, monkeypatch, panel, earn):
    monkeypatch.setattr(G.P8F, "load_persisted_panel", lambda: panel)
    monkeypatch.setattr(G, "load_earnings_events", lambda: earn)
    monkeypatch.setattr(G, "load_sec_filing_events",
                        lambda activate_live=False: (pd.DataFrame(columns=["ticker", "availability_date", "form"]),
                                                     {"live_succeeded": False, "n_local": 0,
                                                      "live_attempted": False, "n_live": 0}))
    report = G.run(tmp_path, activate_live=False)
    for name in G.ARTIFACTS:
        assert (tmp_path / name).exists(), f"missing {name}"
    assert report["recommendation"] in G.ALLOWED_RECOMMENDATIONS
    assert report["safety"]["external_data_faked"] is False
    assert report["activation"]["live_external_collection_ran"] is False


def test_end_to_end_blocks_provider_families_and_no_confirm(tmp_path, monkeypatch, panel, earn):
    monkeypatch.setattr(G.P8F, "load_persisted_panel", lambda: panel)
    monkeypatch.setattr(G, "load_earnings_events", lambda: earn)
    monkeypatch.setattr(G, "load_sec_filing_events",
                        lambda activate_live=False: (pd.DataFrame(columns=["ticker", "availability_date", "form"]),
                                                     {"live_succeeded": False, "n_local": 0,
                                                      "live_attempted": False, "n_live": 0}))
    report = G.run(tmp_path, activate_live=False)
    # random synthetic surprises -> no real CONFIRMED external signal
    assert report["experiments"]["confirmed"] == []
    # news/options/short remain provider-blocked
    assert len(report["experiments"]["needs_provider"]) >= 3


def test_activation_log_marks_real_vs_blocked(earn):
    rev = G.normalized_revision_proxy_panel(earn)
    log = G.activation_log(G.local_artifact_inventory(), earn, rev, pd.DataFrame(),
                           {"n_local": 0, "live_attempted": False, "live_succeeded": False, "n_live": 0},
                           {f: False for f in F.FAMILY_PROVIDERS},
                           {"n_earn_event_obs": 5, "n_filing_event_obs": 0})
    earn_row = next(r for r in log if r["source"] == "earnings_surprise")
    assert earn_row["mode"] == "LOCAL_CACHE_ACTIVATED" and earn_row["real_data"] is True
    news_row = next(r for r in log if r["source"] == "news_sentiment")
    assert "GDELT" in news_row["mode"] and news_row["real_data"] is False
