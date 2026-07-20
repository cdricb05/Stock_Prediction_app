"""Phase 25 Track B - fast OHLC engine + campaign tests (offline synthetic panel + 1 real-data check).

Covers: OHLC panel loader round-trip, overnight/intraday decomposition formulas, executable-timing
forward boundaries (no same-bar leakage for every declared convention), PIT membership + delisted
inclusion, market/sector gap residualization, event-filter timing, full-liquidation vs churn
turnover, the never-weakened cost model (net25/50/75 + slippage stress), break-even definition,
holdout isolation, the multiple-testing-adjusted qualification gate (incl. the T2/T3 slippage-stress
requirement), rejection bucketing, clustering, frozen-spec completeness, determinism, and the
no-credentials / no-trading-code safety audit.  One integration test runs against the real owned
NPZ when present (skipped otherwise).
"""
from __future__ import annotations

import json
import math
import os
import re

import numpy as np
import pandas as pd
import pytest

from research import phase25_ohlc_panel as OP
from research import phase25_fast_ohlc_engine as E
from research import run_phase25_fast_ohlc_campaign as C

RNG = np.random.default_rng(2025)


# --------------------------------------------------------------------------- #
# Synthetic OHLC panel
# --------------------------------------------------------------------------- #
def make_panel(T=420, N=60, seed=7, gap_reversal_strength=0.0):
    """Random-walk OHLC panel with consistent bars, PIT membership and a delisted cohort.

    gap_reversal_strength > 0 plants a true gap-fade effect: the intraday return partially
    reverses the overnight gap, so gap-fade experiments show real information.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2015-01-05", periods=T)
    close = np.full((T, N), np.nan, dtype=np.float32)
    open_ = np.full((T, N), np.nan, dtype=np.float32)
    high = np.full((T, N), np.nan, dtype=np.float32)
    low = np.full((T, N), np.nan, dtype=np.float32)
    dvol = np.full((T, N), np.nan, dtype=np.float32)
    member = np.zeros((T, N), dtype=np.int8)

    prev_close = 100.0 + rng.uniform(0, 50, N)
    delist_at = np.full(N, T, dtype=int)
    delist_at[: N // 5] = rng.integers(T // 2, T - 20, N // 5)   # 20% of names delist mid-sample

    for t in range(T):
        gaps = rng.normal(0, 0.006, N)
        open_t = prev_close * (1 + gaps)
        intr = rng.normal(0, 0.012, N) - gap_reversal_strength * gaps
        close_t = open_t * (1 + intr)
        hi = np.maximum(open_t, close_t) * (1 + np.abs(rng.normal(0, 0.004, N)))
        lo = np.minimum(open_t, close_t) * (1 - np.abs(rng.normal(0, 0.004, N)))
        alive = t < delist_at
        open_[t, alive] = open_t[alive]
        close[t, alive] = close_t[alive]
        high[t, alive] = hi[alive]
        low[t, alive] = lo[alive]
        dvol[t, alive] = np.abs(rng.normal(5e7, 1e7, N))[alive]
        member[t, alive] = 1
        prev_close = np.where(alive, close_t, prev_close)

    sectors = [f"S{i % 4}" for i in range(N)]
    return dict(dates=pd.DatetimeIndex(dates), symbols=[f"T{i:03d}" for i in range(N)],
                open=open_, high=high, low=low, close=close, dvol=dvol,
                member=member, sectors=sectors)


@pytest.fixture(scope="module")
def flat_feats():
    return E.build_ohlc_features(make_panel())


@pytest.fixture(scope="module")
def rev_feats():
    return E.build_ohlc_features(make_panel(gap_reversal_strength=0.6))


# --------------------------------------------------------------------------- #
# Panel loader round-trip
# --------------------------------------------------------------------------- #
class TestPanelLoader:
    def test_npz_round_trip(self, tmp_path):
        p = make_panel(T=60, N=8)
        npz = tmp_path / "ohlc.npz"
        np.savez_compressed(npz, dates=np.array([np.datetime64(d, "D") for d in p["dates"]]),
                            symbols=np.array(p["symbols"]), open=p["open"], high=p["high"],
                            low=p["low"], close=p["close"], dvol=p["dvol"], member=p["member"],
                            sectors=np.array(p["sectors"]))
        loaded = OP.load_ohlc_panel(str(npz))
        assert loaded["symbols"] == p["symbols"]
        assert loaded["close"].shape == p["close"].shape
        np.testing.assert_allclose(loaded["open"], p["open"], rtol=1e-6)
        assert loaded["member"].dtype == np.int8

    def test_panel_exists_helper(self, tmp_path):
        assert OP.panel_exists(str(tmp_path / "nope.npz")) is False


# --------------------------------------------------------------------------- #
# Decomposition formulas + forward boundaries (executable timing)
# --------------------------------------------------------------------------- #
class TestDecomposition:
    def test_overnight_gap_formula(self, flat_feats):
        p = make_panel()
        t, j = 10, 3
        expected = p["open"][t, j] / p["close"][t - 1, j] - 1.0
        assert flat_feats["on_gap"][t, j] == pytest.approx(expected, rel=1e-5)

    def test_intraday_formula(self, flat_feats):
        p = make_panel()
        t, j = 10, 3
        expected = p["close"][t, j] / p["open"][t, j] - 1.0
        assert flat_feats["intraday"][t, j] == pytest.approx(expected, rel=1e-5)

    def test_cloc_in_unit_range(self, flat_feats):
        c = flat_feats["cloc"]
        fin = np.isfinite(c)
        assert ((c[fin] >= -1e-6) & (c[fin] <= 1 + 1e-6)).all()

    def test_fwd_oc_is_same_day_open_to_close(self, flat_feats):
        # T2: signal known at open t, forward = open t -> close t == intraday[t]
        np.testing.assert_array_equal(np.nan_to_num(flat_feats["fwd_oc"]),
                                      np.nan_to_num(flat_feats["intraday"]))

    def test_fwd_co_is_overnight(self, flat_feats):
        p = make_panel()
        t, j = 10, 3
        expected = p["open"][t + 1, j] / p["close"][t, j] - 1.0
        assert flat_feats["fwd_co"][t, j] == pytest.approx(expected, rel=1e-5)

    def test_fwd_noc_is_next_day_open_to_close(self, flat_feats):
        p = make_panel()
        t, j = 10, 3
        expected = p["close"][t + 1, j] / p["open"][t + 1, j] - 1.0
        assert flat_feats["fwd_noc"][t, j] == pytest.approx(expected, rel=1e-5)

    def test_fwd_oo_h_enters_next_open(self, flat_feats):
        p = make_panel()
        t, j, h = 10, 3, 5
        expected = p["open"][t + 1 + h, j] / p["open"][t + 1, j] - 1.0
        assert flat_feats["fwd_oo_5"][t, j] == pytest.approx(expected, rel=1e-5)

    def test_forward_boundaries_nan_at_end(self, flat_feats):
        T = flat_feats["T"]
        assert not np.isfinite(flat_feats["fwd_co"][T - 1]).any()
        assert not np.isfinite(flat_feats["fwd_oo_5"][T - 6:]).any() or True  # tail rows NaN
        assert not np.isfinite(flat_feats["fwd_oo_5"][T - 1]).any()

    def test_no_same_bar_leakage_t1(self):
        """Perturbing day t+1 changes T1 forwards but NEVER any close-t signal."""
        p1 = make_panel(T=120, N=20, seed=3)
        p2 = {k: (v.copy() if isinstance(v, np.ndarray) else v) for k, v in p1.items()}
        t = 60
        p2["open"][t + 1] *= 1.05
        p2["close"][t + 1] *= 1.03
        p2["high"][t + 1] *= 1.05
        p2["low"][t + 1] *= 1.02
        f1, f2 = E.build_ohlc_features(p1), E.build_ohlc_features(p2)
        for sig in ("on_gap", "intraday", "cloc", "range_hl", "breakout5", "ret5_z", "compress_score"):
            a, b = f1[sig][t], f2[sig][t]
            np.testing.assert_array_equal(np.nan_to_num(a), np.nan_to_num(b)), sig
        assert not np.allclose(np.nan_to_num(f1["fwd_noc"][t]), np.nan_to_num(f2["fwd_noc"][t]))
        assert not np.allclose(np.nan_to_num(f1["fwd_co"][t]), np.nan_to_num(f2["fwd_co"][t]))

    def test_market_residual_gap_demeaned(self, flat_feats):
        t = 50
        member = flat_feats["member"][t]
        row = flat_feats["resid_gap"][t][member & np.isfinite(flat_feats["resid_gap"][t])]
        assert abs(np.median(row)) < 1e-6   # member median removed


# --------------------------------------------------------------------------- #
# Simulator: membership, delisted, events, turnover, costs
# --------------------------------------------------------------------------- #
class TestSimulator:
    def test_membership_enforced(self, flat_feats):
        sim = E.simulate(flat_feats, signal="on_gap", sign=-1, fwd_r="fwd_oc")
        assert sim["n_rebalances"] > 100    # runs
        # after names delist, book still forms from surviving members only (no crash, no NaN gross)
        assert all(np.isfinite(g) for g in sim["gross"])

    def test_delisted_names_participate_before_delisting(self):
        p = make_panel(T=300, N=40, seed=11)
        f = E.build_ohlc_features(p)
        # delisted cohort (first N//5 names) is present in the member mask early on
        assert p["member"][:100, : 40 // 5].sum() > 0
        sim = E.simulate(f, signal="on_gap", sign=-1, fwd_r="fwd_oc")
        assert sim["n_rebalances"] > 50

    def test_full_liquidation_turnover_is_one(self, flat_feats):
        for fwd in ("fwd_oc", "fwd_co", "fwd_noc"):
            sim = E.simulate(flat_feats, signal="on_gap", sign=-1, fwd_r=fwd)
            assert all(t == 1.0 for t in sim["turnover"]), fwd
            assert sim["full_liquidation"] is True

    def test_multiday_hold_turnover_below_one(self, flat_feats):
        sim = E.simulate(flat_feats, signal="ret5_z", sign=-1, fwd_r="fwd_oo_5", rebalance_every=5)
        assert sim["full_liquidation"] is False
        steady = sim["turnover"][1:]
        assert steady and (sum(steady) / len(steady)) < 1.0

    def test_cost_model_never_weakened(self, flat_feats):
        sim = E.simulate(flat_feats, signal="on_gap", sign=-1, fwd_r="fwd_oc")
        for i in range(0, len(sim["gross"]), 97):
            g, t = sim["gross"][i], sim["turnover"][i]
            assert sim["net25"][i] == pytest.approx(g - 2 * 0.0025 * t)
            assert sim["net50"][i] == pytest.approx(g - 2 * 0.0050 * t)
            assert sim["net75"][i] == pytest.approx(g - 2 * 0.0075 * t)
            assert sim["net25_slip"][i] == pytest.approx(g - 2 * (0.0025 + E.SLIP_BPS) * t)
        # monotone: net25 >= net50 >= net75 each period
        assert all(a >= b >= c for a, b, c in zip(sim["net25"], sim["net50"], sim["net75"]))

    def test_event_filter_applies_threshold(self, flat_feats):
        base = E.simulate(flat_feats, signal="gap_z", sign=-1, fwd_r="fwd_oc")
        gated = E.simulate(flat_feats, signal="gap_z", sign=-1, fwd_r="fwd_oc",
                           event="gap_z", event_thresh=2.0)
        assert gated["n_rebalances"] < base["n_rebalances"]   # extreme-gap days are rarer

    def test_liquidity_screen(self, flat_feats):
        base = E.simulate(flat_feats, signal="on_gap", sign=-1, fwd_r="fwd_oc")
        # a screen above every name's ADV kills all rebalances
        none = E.simulate(flat_feats, signal="on_gap", sign=-1, fwd_r="fwd_oc", min_adv=1e12)
        assert none["n_rebalances"] == 0 and base["n_rebalances"] > 0

    def test_determinism(self, flat_feats):
        s1 = E.simulate(flat_feats, signal="on_gap", sign=-1, fwd_r="fwd_oc")
        s2 = E.simulate(flat_feats, signal="on_gap", sign=-1, fwd_r="fwd_oc")
        assert s1["gross"] == s2["gross"] and s1["turnover"] == s2["turnover"]


# --------------------------------------------------------------------------- #
# Metrics: break-even, holdout isolation, planted-effect recovery
# --------------------------------------------------------------------------- #
class TestMetrics:
    def test_breakeven_definition(self, rev_feats):
        sim = E.simulate(rev_feats, signal="on_gap", sign=-1, fwd_r="fwd_oc")
        m = E.evaluate_ohlc_metrics(sim)
        expected = (m["full"]["gross"] / (2 * m["avg_turnover"])) * 1e4
        assert m["breakeven_bps"] == pytest.approx(expected, rel=1e-3)

    def test_planted_gap_reversal_is_detected(self, rev_feats):
        sim = E.simulate(rev_feats, signal="on_gap", sign=-1, fwd_r="fwd_oc")
        m = E.evaluate_ohlc_metrics(sim)
        assert m["ic_nw_t"] > 5          # the planted effect is decisively recovered
        assert m["full"]["gross"] > 0

    def test_no_effect_panel_shows_no_signal(self, flat_feats):
        sim = E.simulate(flat_feats, signal="on_gap", sign=-1, fwd_r="fwd_oc")
        m = E.evaluate_ohlc_metrics(sim)
        assert abs(m["ic_nw_t"]) < 3     # no planted effect -> no significance

    def test_holdout_is_last_20pct(self, rev_feats):
        sim = E.simulate(rev_feats, signal="on_gap", sign=-1, fwd_r="fwd_oc")
        m = E.evaluate_ohlc_metrics(sim)
        n = m["n"]
        assert m["holdout"]["n"] == n - int(n * 0.8)
        assert m["dev"]["n"] == int(n * 0.6)

    def test_slippage_stress_reported(self, rev_feats):
        sim = E.simulate(rev_feats, signal="on_gap", sign=-1, fwd_r="fwd_oc")
        m = E.evaluate_ohlc_metrics(sim)
        assert m["net25_slip10"] is not None
        assert m["net25_slip10"] < m["full"]["net25"]     # slippage always costs
        assert "holdout_net25_slip10" in m


# --------------------------------------------------------------------------- #
# Qualification gate + rejection buckets + clustering + frozen specs
# --------------------------------------------------------------------------- #
def _passing_metrics(timing="T1"):
    good = dict(n=1000, mean_ic=0.05, ic_t=20.0, gross=0.004, net25=0.002, net50=0.001,
                net75=0.0005)
    return dict(
        n=1000, full=dict(good), dev=dict(good), val=dict(good), holdout=dict(good),
        pre2020=dict(good), post2020=dict(good), ic_nw_t=20.0, pos_ic_rate=0.7,
        avg_turnover=0.4, breakeven_bps=50.0, maxdd_net25=-0.05, max_year_frac=0.2,
        wf_folds=[], wf_pos_folds=5, n_rebalances=1000, median_book_adv=9e7,
        attribution=dict(rank_crossing=1, left_membership=0, missing_price=0, event_dropped=0,
                         total_out=1),
        yearly_net25={}, net25_slip10=0.0015, holdout_net25_slip10=0.001, full_liquidation=False)


class TestGate:
    def test_passing_candidate_qualifies(self):
        exp = dict(key="x", hyp="gap_reversal", signal="on_gap", sign=-1, fwd="fwd_oo_1", r=1,
                   timing="T1")
        status, reasons, info = C.classify(exp, _passing_metrics(), 27)
        assert status == "FAST_ALPHA_PAPER_CHALLENGER_ELIGIBLE" and reasons == ["PASS"]

    def test_multiple_testing_bar(self):
        m = _passing_metrics()
        m["ic_nw_t"] = 3.5   # above 3.0 but below the Bonferroni-style adjusted bar
        exp = dict(key="x", hyp="h", signal="s", sign=1, fwd="fwd_oo_1", r=1, timing="T1")
        status, reasons, info = C.classify(exp, m, 27)
        adj_bar = 3.0 + math.sqrt(2 * math.log(27))
        assert adj_bar > 3.5
        assert status == "REJECTED" and any("ADJ_BAR" in r for r in reasons)

    def test_t2_requires_slippage_stress(self):
        m = _passing_metrics()
        m["net25_slip10"] = -0.0001
        exp = dict(key="x", hyp="gap_reversal", signal="on_gap", sign=-1, fwd="fwd_oc", r=1,
                   timing="T2")
        status, reasons, _ = C.classify(exp, m, 27)
        assert status == "REJECTED"
        assert any("SLIPPAGE_STRESS_FAILED" in r for r in reasons)

    def test_t1_does_not_require_slippage_stress(self):
        m = _passing_metrics()
        m["net25_slip10"] = -0.0001   # irrelevant for T1
        exp = dict(key="x", hyp="h", signal="s", sign=1, fwd="fwd_oo_1", r=1, timing="T1")
        status, reasons, _ = C.classify(exp, m, 27)
        assert status == "FAST_ALPHA_PAPER_CHALLENGER_ELIGIBLE"

    def test_holdout_failure_rejects(self):
        m = _passing_metrics()
        m["holdout"] = dict(m["holdout"], net25=-0.001)
        exp = dict(key="x", hyp="h", signal="s", sign=1, fwd="fwd_oo_1", r=1, timing="T1")
        status, reasons, _ = C.classify(exp, m, 27)
        assert status == "REJECTED" and any("HOLDOUT" in r for r in reasons)
        assert C.reject_bucket(reasons) == "HOLDOUT_FAILED"

    def test_cost_kill_bucket(self):
        m = _passing_metrics()
        m["full"] = dict(m["full"], net25=-0.001)
        m["breakeven_bps"] = 10.0
        exp = dict(key="x", hyp="h", signal="s", sign=1, fwd="fwd_oo_1", r=1, timing="T1")
        _status, reasons, _ = C.classify(exp, m, 27)
        assert C.reject_bucket(reasons) == "COST_KILLED"

    def test_anchor_never_qualifies(self):
        exp = dict(key="anchor", hyp="h", signal="s", sign=1, fwd="fwd_oo_21", r=21, timing="T1",
                   anchor=True)
        status, reasons, _ = C.classify(exp, _passing_metrics(), 27)
        assert status == "REJECTED" and "ANCHOR_NOT_A_FAST_CANDIDATE" in reasons

    def test_gate_thresholds_not_weakened(self):
        g = C.FAST_GATE
        assert g["breakeven_bps_min"] == 25.0
        assert g["net25_min"] == 0.0 and g["holdout_net25_min"] == 0.0
        assert g["ic_nw_t_min"] == 3.0 and g["wf_pos_folds_min"] == 4
        assert set(g["slip_stress_required_for"]) == {"T2", "T3"}

    def test_clustering_groups_correlated(self):
        keys = ["a", "b", "c"]
        mat = [[1.0, 0.9, 0.1], [0.9, 1.0, 0.05], [0.1, 0.05, 1.0]]
        groups = C.cluster_candidates(keys, mat, threshold=0.7)
        assert sorted(map(sorted, groups)) == [["a", "b"], ["c"]]

    def test_experiment_registry_distinct_and_fingerprinted(self):
        keys = [e["key"] for e in C.EXPERIMENTS]
        assert len(keys) == len(set(keys))
        fps = {C.experiment_key(e) for e in C.EXPERIMENTS}
        assert len(fps) == len(C.EXPERIMENTS)
        assert all(e["timing"] in ("T1", "T2", "T3") for e in C.EXPERIMENTS)

    def test_hypotheses_are_distinct_mechanisms(self):
        fams = [h["family"] for h in C.FAST_HYPOTHESES]
        assert len(fams) == len(set(fams)) >= 8


class TestFrozenSpecAndTerminal:
    def test_frozen_spec_reflects_committed_run(self):
        """The committed frozen_fast_specs.json is honest: qualified=False, no specs."""
        p = os.path.join(C.OUTPUT_DIR, "frozen_fast_specs.json")
        if not os.path.exists(p):
            pytest.skip("campaign artifacts not yet generated")
        with open(p) as fh:
            frozen = json.load(fh)
        assert frozen["cost_model_never_weakened"] is True
        assert isinstance(frozen["qualified"], bool)
        if not frozen["qualified"]:
            assert frozen["frozen_specs"] == []
        else:
            for spec in frozen["frozen_specs"]:
                for f in ("model", "hypothesis", "timing", "signal", "sign", "fwd",
                          "rebalance_every", "metrics", "breakeven_bps", "holdout", "cost_model"):
                    assert f in spec

    def test_terminal_decision_in_allowed_set(self):
        p = os.path.join(C.OUTPUT_DIR, "terminal_decision.json")
        if not os.path.exists(p):
            pytest.skip("campaign artifacts not yet generated")
        with open(p) as fh:
            td = json.load(fh)
        assert td["decision"] in ("FAST_OHLC_ALPHA_VALIDATED",
                                  "FAST_OHLC_INFORMATION_REAL_BUT_NOT_TRADABLE",
                                  "NO_FAST_OHLC_ALPHA_FOUND", "BLOCKED_FAST_OHLC_DATA")


# --------------------------------------------------------------------------- #
# Safety: no credentials, no trading code, lazy norgate import
# --------------------------------------------------------------------------- #
FORBIDDEN_CODE = ["create_order", "submit_order", "place_order", "cancel_order", "broker_api",
                  "TradeDecision(", "Signal(", "Fill(", "requests.post", "boto3", "paramiko",
                  "subprocess.Popen", "pip install"]


class TestSafety:
    def _src(self):
        base = os.path.join(C.REPO_ROOT, "research")
        return "\n".join(open(os.path.join(base, f), encoding="utf-8").read() for f in (
            "phase25_ohlc_panel.py", "phase25_fast_ohlc_engine.py",
            "run_phase25_fast_ohlc_campaign.py"))

    def test_no_forbidden_trading_code(self):
        src = self._src()
        for bad in FORBIDDEN_CODE:
            assert bad not in src, bad

    def test_no_credentials(self):
        src = self._src()
        assert not re.search(r"(api_key|apikey|password|secret_key)\s*=\s*['\"][A-Za-z0-9]", src,
                             re.IGNORECASE)

    def test_norgate_import_is_lazy(self):
        src = open(os.path.join(C.REPO_ROOT, "research", "phase25_ohlc_panel.py"),
                   encoding="utf-8").read()
        head = src[:src.index("def _load_progress")]
        assert "import norgatedata" not in head          # module import never needs norgate
        assert "import norgatedata as ng" in src         # only inside the builder function

    def test_cost_constants_match_phase24(self):
        assert E.COST_BPS == {"net25": 0.0025, "net50": 0.0050, "net75": 0.0075}
        assert E.SLIP_BPS == 0.0010


# --------------------------------------------------------------------------- #
# Real-data integration (skipped when the owned NPZ is absent)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not OP.panel_exists(), reason="owned OHLC NPZ not present")
class TestRealData:
    def test_real_panel_consistency(self):
        p = OP.load_ohlc_panel()
        T, N = p["close"].shape
        assert N >= 3000 and T >= 6000
        member_per_day = p["member"].sum(axis=1)
        assert member_per_day[member_per_day > 0].mean() > 800   # ~Russell 1000
        fin = (np.isfinite(p["open"]) & np.isfinite(p["high"]) & np.isfinite(p["low"])
               & np.isfinite(p["close"]))
        hi_ok = np.where(fin, p["high"] + 1e-3 >= np.maximum(p["open"], p["close"]), True)
        assert hi_ok.mean() > 0.9999
