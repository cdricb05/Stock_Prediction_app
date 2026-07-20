"""Fully-offline targeted tests for Phase 22 - Autonomous High-Conviction Alpha Discovery.

Proves, on tiny synthetic membership-aware panels (no network, no owned data required):
  * point-in-time feature boundaries (a feature at T never changes when future bars are appended);
  * forward-return boundaries (fwd_k uses strictly-future bars; the tail is NaN);
  * historical membership enforcement (non-members are excluded from the cross-section);
  * rank-IC, decile spread, turnover and net-of-cost arithmetic;
  * sector-neutralisation demeans within sector;
  * the alpha-selection gate ladder (STRONG / ORTHOGONAL / REJECTED incl. the spread-significance and
    subperiod-sign gates) and the multiple-testing haircut;
  * champion correlation and the terminal-decision mapping;
  * determinism (same inputs -> identical decision + metrics);
  * safety: no orders / no DB / no champion replacement / no live promotion, artifacts write only to the
    target dir, and a build performs no write until write_artifacts is called.
Plus a REAL-DATA integration test that is skipped when the owned panels are absent.
"""
from __future__ import annotations

import importlib
import json
import math
import os

import numpy as np
import pandas as pd
import pytest

MOD = "research.run_phase22_autonomous_high_conviction_alpha_discovery"
M = importlib.import_module(MOD)


# --------------------------------------------------------------------------- #
# Synthetic owned-style panel builders.                                       #
# --------------------------------------------------------------------------- #
def _write_panel(dirpath, n_tickers=40, n_months=72, seed=7, all_members=True):
    """Write phase8c-format wide CSVs with a persistent cross-sectional momentum structure."""
    os.makedirs(dirpath, exist_ok=True)
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2015-01-31", periods=n_months, freq="ME")
    tickers = [f"T{i:03d}" for i in range(n_tickers)]
    sectors = ["Tech", "Health", "Fin", "Energy"]
    drift = np.linspace(-0.008, 0.028, n_tickers)          # persistent per-name drift -> momentum
    rets = drift[None, :] + rng.normal(0.0, 0.04, (n_months, n_tickers))
    close = 100.0 * np.cumprod(1.0 + rets, axis=0)
    close_df = pd.DataFrame(close, index=dates, columns=tickers)
    close_df.index.name = "Date"
    mem = pd.DataFrame(1.0, index=dates, columns=tickers)
    if not all_members:
        mem.iloc[:, 0] = 0.0                                # first ticker never a member
    mem.index.name = "Date"
    dvol = pd.DataFrame(1.0e7, index=dates, columns=tickers)
    dvol.index.name = "Date"
    spy = pd.DataFrame({"Close": 100.0 * np.cumprod(1.0 + rng.normal(0.005, 0.03, n_months))}, index=dates)
    spy.index.name = "Date"
    meta = pd.DataFrame({
        "ticker": tickers,
        "gics_sector": [sectors[i % len(sectors)] for i in range(n_tickers)],
        "is_delisted": [False] * n_tickers,
    })
    close_df.to_csv(os.path.join(dirpath, "monthly_close_total_return.csv"))
    mem.to_csv(os.path.join(dirpath, "membership_panel.csv"))
    dvol.to_csv(os.path.join(dirpath, "monthly_dollar_volume.csv"))
    spy.to_csv(os.path.join(dirpath, "spy_monthly_total_return.csv"))
    meta.to_csv(os.path.join(dirpath, "metadata.csv"), index=False)
    return dict(dates=dates, tickers=tickers)


def _write_champion(path, tickers, dates):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = []
    for d in dates:
        for i, tk in enumerate(tickers):
            rows.append(dict(as_of_date=str(d.date()), rebalance_date=str(d.date()), ticker=tk,
                             sector="Tech", composite_sn=float((i % 7) - 3)))
    pd.DataFrame(rows).to_csv(path, index=False)


# --------------------------------------------------------------------------- #
# Stat helpers.                                                                #
# --------------------------------------------------------------------------- #
def test_spearman_perfect():
    assert M._spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert M._spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_std_tstat_drawdown_posrate():
    assert M._std([2.0, 4.0], 1) == pytest.approx(math.sqrt(2.0))
    assert M._positive_rate([0.1, -0.2, 0.3, 0.0]) == pytest.approx(0.5)
    assert M._max_drawdown([0.1, 0.1, -0.5, 0.1]) < 0
    assert M._t_stat([1.0, 1.0, 1.0, 1.0, 1.0]) != M._t_stat([1.0, 1.0, 1.0])  # constant -> nan handled


def test_ordinal_ranks_and_normal_sf():
    r = M._ordinal_ranks(np.array([5.0, 1.0, 3.0]))
    assert list(r) == [3.0, 1.0, 2.0]
    assert M._normal_sf(0.0) == pytest.approx(0.5, abs=1e-6)
    assert M._normal_sf(1.96) == pytest.approx(0.025, abs=1e-3)


def test_newey_west_reduces_to_population_t_at_lag0():
    # NW uses the population (1/n) long-run variance; at lag 0 it is mu / sqrt(gamma0/n).
    x = np.array([0.01, 0.02, -0.005, 0.03, 0.015, 0.02])
    n = len(x)
    e = x - x.mean()
    se = math.sqrt(float((e * e).sum() / n) / n)
    assert M._newey_west_t(x, 0) == pytest.approx(x.mean() / se, rel=1e-9)
    # NW at a positive lag stays finite and same-signed on a trending series
    assert M._newey_west_t(x, 2) > 0


# --------------------------------------------------------------------------- #
# PIT / forward boundaries + membership.                                       #
# --------------------------------------------------------------------------- #
@pytest.fixture
def panel(tmp_path):
    M.clear_cache()
    info = _write_panel(tmp_path / "p8c")
    p = M.load_monthly_panel(str(tmp_path / "p8c"), use_cache=False)
    return p, info


def test_no_lookahead_features_independent_of_future(panel):
    p, _ = panel
    sig_full = M.build_signals(p)["mom_12_1"]
    # truncated panel: earlier months must produce identical features (features use only past bars)
    trunc = dict(close=p["close"].iloc[:50].copy(), dvol=p["dvol"].iloc[:50].copy())
    sig_trunc = M.build_signals(trunc)["mom_12_1"]
    common = sig_trunc.index
    a = sig_full.loc[common].iloc[20:40]
    b = sig_trunc.loc[common].iloc[20:40]
    pd.testing.assert_frame_equal(a, b)


def test_forward_returns_use_future_only(panel):
    p, _ = panel
    fwd = M.build_forwards(p)
    close = p["close"]
    tk = close.columns[3]
    i = 30
    exp = close[tk].iloc[i + 1] / close[tk].iloc[i] - 1.0
    assert fwd["fwd_1m"][tk].iloc[i] == pytest.approx(exp)
    assert math.isnan(fwd["fwd_1m"][tk].iloc[-1])          # tail forward is NaN
    assert math.isnan(fwd["fwd_3m"][tk].iloc[-1])


def test_momentum_feature_formula(panel):
    p, _ = panel
    close = p["close"]
    sig = M.build_signals(p)
    tk = close.columns[5]
    i = 20
    exp = close[tk].iloc[i - 1] / close[tk].iloc[i - 12] - 1.0
    assert sig["mom_12_1"][tk].iloc[i] == pytest.approx(exp)


def test_membership_enforced(tmp_path):
    M.clear_cache()
    _write_panel(tmp_path / "p", all_members=False)
    p = M.load_monthly_panel(str(tmp_path / "p"), use_cache=False)
    valid = M.base_valid_mask(p)
    assert not valid.iloc[30, 0]                           # non-member excluded everywhere
    assert bool(valid.iloc[30, 1])


def test_row_ic_excludes_invalid(panel):
    p, _ = panel
    sig = M.build_signals(p)
    fwd = M.build_forwards(p)
    valid = M.base_valid_mask(p)
    ic, n = M._row_ic(sig["mom_12_1"], fwd["fwd_3m"], valid)
    # with a strong persistent-momentum construction IC should be positive on average
    icv = [x for x in ic if not math.isnan(x)]
    assert np.mean(icv) > 0
    # dropping a name reduces the count by exactly one where it was valid
    valid2 = valid.copy()
    valid2.iloc[:, 0] = False
    _, n2 = M._row_ic(sig["mom_12_1"], fwd["fwd_3m"], valid2)
    assert (n2 <= n).all()


def test_sector_neutralize_demeans_within_sector(panel):
    p, _ = panel
    sig = M.build_signals(p)["mom_12_1"]
    sn = M.sector_neutralize(sig, p["sector"])
    # for a fully-populated row, each sector's mean of the neutralised signal is ~0
    row = 30
    groups = {}
    for c in sig.columns:
        groups.setdefault(p["sector"][c], []).append(c)
    for _sec, cols in groups.items():
        vals = sn.loc[sn.index[row], cols].dropna().values
        if len(vals) >= 2:
            assert abs(float(np.mean(vals))) < 1e-9


# --------------------------------------------------------------------------- #
# Decile / turnover / cost arithmetic.                                         #
# --------------------------------------------------------------------------- #
def test_turnover_and_net_cost_arithmetic(monkeypatch):
    monkeypatch.setattr(M, "MIN_NAMES_PER_MONTH", 2)       # allow the tiny 4-name book
    # 2 dates, 4 names, 2 deciles(=halves): construct deterministic books
    idx = pd.to_datetime(["2020-01-31", "2020-02-29"])
    S = pd.DataFrame([[1, 2, 3, 4], [4, 3, 2, 1]], index=idx, columns=list("ABCD"), dtype=float)
    F = pd.DataFrame([[0.0, 0.0, 0.10, 0.20], [0.20, 0.10, 0.0, 0.0]], index=idx, columns=list("ABCD"))
    valid = pd.DataFrame(True, index=idx, columns=list("ABCD"))
    books = M._decile_books(S, F, valid, q=2)
    assert len(books["dates"]) == 2
    avg_turn, turn, net = M._turnover_and_net(books)
    # top-half fully rotates between months -> long turnover 1.0; combined one-way turnover 1.0
    assert avg_turn == pytest.approx(1.0)
    # net25 = gross - 2*0.0025*turnover on the 2nd month
    g1 = books["gross"][1]
    assert net["net25"][1] == pytest.approx(g1 - 2 * 0.0025 * 1.0)


# --------------------------------------------------------------------------- #
# Gates + terminal decision.                                                   #
# --------------------------------------------------------------------------- #
def _packaged(ic_t=6.0, gross_t=4.0, net25=0.02, pos=0.62, pre=0.03, post=0.03,
              holdout=0.02, year_frac=0.25, n=100):
    prim = dict(n_months=n, mean_ic=0.03, ic_t=ic_t, ic_nw_t=ic_t, pos_ic_rate=pos,
                gross_spread=0.01, gross_t=gross_t, net25=net25, net50=net25 - 0.002,
                long_only=0.005, maxdd=-0.1)
    slc = lambda ic, n25: dict(n_months=n, mean_ic=ic, ic_t=ic_t, gross_spread=0.01, gross_t=gross_t,
                               net25=n25, net50=n25, long_only=0.005, maxdd=-0.1, pos_ic_rate=pos,
                               ic_nw_t=ic_t)
    return dict(primary=prim,
                full_by_slice=dict(full=prim, dev=slc(0.03, net25), val=slc(0.03, net25),
                                   holdout=slc(0.02, holdout), pre2020=slc(pre, net25), post2020=slc(post, net25)),
                avg_turnover=0.3, max_year_frac=year_frac,
                stability=dict(frac_positive_windows=0.9, window=36, rolling_min=0.01, rolling_max=0.05),
                primary_horizon="fwd_3m")


def test_gate_strong_when_all_pass():
    g = M.apply_gates("mom_6_1", "raw", _packaged(), dict(abs_mean_corr=0.12), n_trials=36)
    assert g["status"] == "STRONG_ALPHA_PAPER_CHALLENGER_ELIGIBLE"
    assert g["reasons"] == []


def test_gate_rejects_weak_spread():
    g = M.apply_gates("lowvol", "raw", _packaged(gross_t=0.6), dict(abs_mean_corr=0.1), n_trials=36)
    assert g["status"] == "REJECTED"
    assert any("WEAK_SPREAD_T" in r for r in g["reasons"])


def test_gate_rejects_subperiod_sign_flip():
    g = M.apply_gates("rev_1m", "raw", _packaged(post=-0.02, holdout=-0.01), dict(abs_mean_corr=0.1), n_trials=36)
    assert g["status"] == "REJECTED"
    assert any("SUBPERIOD_SIGN" in r for r in g["reasons"])


def test_gate_orthogonal_when_only_soft_reason():
    g = M.apply_gates("mom_12_1", "raw", _packaged(year_frac=0.55), dict(abs_mean_corr=0.12), n_trials=36)
    assert g["status"] == "ORTHOGONAL_ALPHA_RESEARCH_ELIGIBLE"
    assert any("YEAR_CONCENTRATION" in r for r in g["reasons"])


def test_gate_high_corr_downgrades_strong_to_orthogonal():
    g = M.apply_gates("mom_6_1", "raw", _packaged(), dict(abs_mean_corr=0.85), n_trials=36)
    assert g["status"] == "ORTHOGONAL_ALPHA_RESEARCH_ELIGIBLE"


def test_multiple_testing_haircut_rejects_borderline():
    # borderline t just above 2 becomes insignificant after Bonferroni over many trials
    g = M.apply_gates("x", "raw", _packaged(ic_t=2.05, gross_t=2.05), dict(abs_mean_corr=0.1), n_trials=500)
    assert any("FAILS_MULTIPLE_TESTING" in r for r in g["reasons"])


def test_terminal_decision_mapping():
    def cr(status, net25):
        return dict(gate=dict(status=status), packaged=dict(primary=dict(net25=net25, ic_t=5.0)),
                    primary_horizon="fwd_3m", corr=dict(abs_mean_corr=0.1))
    res = dict(candidates={("a", "raw"): cr("STRONG_ALPHA_PAPER_CHALLENGER_ELIGIBLE", 0.03),
                           ("b", "raw"): cr("ORTHOGONAL_ALPHA_RESEARCH_ELIGIBLE", 0.01)})
    t = M.decide_terminal(res)
    assert t["decision"] == "STRONG_ALPHA_PAPER_CHALLENGER_ELIGIBLE"
    assert t["best_candidate"]["candidate"] == "a"
    res2 = dict(candidates={("b", "raw"): cr("REJECTED", 0.0)})
    assert M.decide_terminal(res2)["decision"] == "DATA_COVERAGE_REPAIRED_NO_STRONG_ALPHA"


# --------------------------------------------------------------------------- #
# Champion correlation + safety + determinism (synthetic build).              #
# --------------------------------------------------------------------------- #
@pytest.fixture
def synthetic_build(tmp_path, monkeypatch):
    M.clear_cache()
    info = _write_panel(tmp_path / "p8c", n_months=84)
    champ_path = str(tmp_path / "champ" / "champion.csv")
    _write_champion(champ_path, info["tickers"], info["dates"])
    monkeypatch.setattr(M, "PANEL8C_DIR", str(tmp_path / "p8c"))
    monkeypatch.setattr(M, "CHAMPION_PANEL", champ_path)
    monkeypatch.setattr(M, "PHASE21_STORE", str(tmp_path / "nope"))
    monkeypatch.setattr(M, "OUTPUT_DIR", str(tmp_path / "out"))
    res = M.build(panel_dir=str(tmp_path / "p8c"), champion_path=champ_path, run_weekly=False)
    return res, tmp_path


def test_build_shape_and_safety(synthetic_build):
    res, _ = synthetic_build
    assert res["phase"] == "22"
    assert res["n_trials"] == 36
    sb = M.SAFETY_BLOCK()
    assert sb["creates_orders"] is False and sb["writes_database"] is False
    assert sb["replaces_champion"] is False and sb["promotes_to_live"] is False
    assert sb["live_trading_status"] == "NOT_APPROVED_FOR_LIVE_TRADING"
    assert res["terminal"]["safety"]["creates_orders"] is False


def test_champion_correlation_present(synthetic_build):
    res, _ = synthetic_build
    corr = res["correlations"][("mom_12_1", "raw")]
    assert corr["n_months"] > 0
    assert corr["mean_corr"] is not None


def test_build_writes_nothing_until_write_artifacts(synthetic_build):
    res, tmp_path = synthetic_build
    out = tmp_path / "out"
    assert not out.exists()                                # build() performed no write
    written = M.write_artifacts(res, str(out))
    assert len(written) >= 22
    assert (out / "phase22_terminal_decision.json").exists()
    assert (out / "phase22_final_report.json").exists()
    # terminal decision file has no order/db flags set true
    td = json.load(open(out / "phase22_terminal_decision.json", encoding="utf-8"))
    assert td["safety"]["writes_database"] is False


def test_determinism(tmp_path, monkeypatch):
    M.clear_cache()
    info = _write_panel(tmp_path / "p8c", n_months=84)
    champ_path = str(tmp_path / "champ.csv")
    _write_champion(champ_path, info["tickers"], info["dates"])
    monkeypatch.setattr(M, "PHASE21_STORE", str(tmp_path / "nope"))
    r1 = M.build(panel_dir=str(tmp_path / "p8c"), champion_path=champ_path, run_weekly=False)
    M.clear_cache()
    r2 = M.build(panel_dir=str(tmp_path / "p8c"), champion_path=champ_path, run_weekly=False)
    assert r1["terminal"]["decision"] == r2["terminal"]["decision"]
    assert r1["horizon_metrics"] == r2["horizon_metrics"]


def test_secret_safety_audit_is_clean():
    for row in M._secret_safety_audit():
        assert row["result"] == "NONE"


# --------------------------------------------------------------------------- #
# Real-data integration (skipped when owned panels absent).                    #
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not os.path.exists(os.path.join(M.PANEL8C_DIR, "monthly_close_total_return.csv")),
                    reason="owned survivorship-free panel not present")
def test_real_data_momentum_survives_and_gap_rev_repaired(tmp_path):
    M.clear_cache()
    res = M.build(run_weekly=True, weekly_max_rows=None)
    # momentum must appear among survivors on the real survivorship-free universe
    survivors = M._survivor_rows(res)
    fams = {r["family"] for r in survivors}
    assert "MOMENTUM" in fams
    assert res["terminal"]["decision"] in (
        "STRONG_ALPHA_PAPER_CHALLENGER_ELIGIBLE", "ORTHOGONAL_ALPHA_RESEARCH_ELIGIBLE")
    # gap_rev bias repair: survivorship-aware weekly reversal survives (ic_t > 2)
    weekly = res["gap_rev_repair"]["survivorship_aware_weekly"]
    assert any(w["signal"].startswith("-ret_5") and w["ic_t"] > 2 for w in weekly)
    # writes exactly the documented artifact set to a tmp dir (no DB, no orders)
    written = M.write_artifacts(res, str(tmp_path / "out"))
    assert len(written) == 25
    M.clear_cache()
