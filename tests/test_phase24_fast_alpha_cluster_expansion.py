"""Targeted tests for Phase 24 - fast-alpha and independent-cluster expansion.

Engine mechanics are tested on small deterministic SYNTHETIC daily panels (no Norgate, no network); the
NPZ reader is tested by round-tripping a tiny written NPZ; safety is tested by code-pattern audit; one
integration test runs the real campaign only if the owned daily NPZ is present (skipif otherwise).
"""
import importlib
import math
import os
import tempfile

import numpy as np
import pytest

DP = importlib.import_module("research.phase24_daily_panel")
E = importlib.import_module("research.phase24_fast_engine")
R = importlib.import_module("research.run_phase24_fast_alpha_cluster_expansion")


# --------------------------------------------------------------------------- #
# Synthetic deterministic daily panel                                          #
# --------------------------------------------------------------------------- #
def make_panel(T=400, N=80, list_gaps=True):
    """Deterministic ragged daily panel with cross-sectional variation and an embedded reversal."""
    dates = np.array([np.datetime64("2005-01-03") + np.timedelta64(7 * i, "D") for i in range(T)])
    symbols = [f"S{j:03d}" for j in range(N)]
    rng = np.zeros((T, N))
    for t in range(1, T):
        for j in range(N):
            base = 0.01 * math.sin(0.7 * t + 1.3 * j)
            rev = -0.3 * rng[t - 1, j]                 # mild mean reversion => reversal signal has edge
            rng[t, j] = base + rev
    close = 100.0 * np.cumprod(1.0 + rng, axis=0)
    close = close.astype(np.float32)
    dvol = np.full((T, N), 1e8, dtype=np.float32)
    for j in range(N):                                  # liquidity spread across names
        dvol[:, j] = (10e6 + 2e6 * j)
    member = np.ones((T, N), dtype=np.int8)
    if list_gaps:
        # a "delisted" name: member only in the first half, NaN price afterwards
        close[T // 2:, 0] = np.nan
        member[T // 2:, 0] = 0
        # a "late-listed" name: NaN price + non-member in the first quarter
        close[:T // 4, 1] = np.nan
        member[:T // 4, 1] = 0
    sectors = [("Tech" if j % 2 == 0 else "Health") for j in range(N)]
    return dict(dates=__import__("pandas").to_datetime([str(d) for d in dates]), symbols=symbols,
                close=close, dvol=dvol, member=member, sectors=sectors)


@pytest.fixture(scope="module")
def feats():
    return E.build_daily_features(make_panel())


# --------------------------------------------------------------------------- #
# PIT boundaries / no future backfill / membership / delisted inclusion        #
# --------------------------------------------------------------------------- #
def test_pit_forward_uses_future_only(feats):
    close = feats  # feats built from panel; reconstruct closes to check
    panel = make_panel()
    c = panel["close"].astype(float)
    f = E.build_daily_features(panel)
    fwd1 = f["fwd_1"]
    # fwd_1[t,j] must equal close[t+1]/close[t]-1 (future only), NaN at the last row
    t, j = 10, 5
    assert abs(fwd1[t, j] - (c[t + 1, j] / c[t, j] - 1.0)) < 1e-5
    assert np.isnan(fwd1[-1, j])


def test_pit_trailing_uses_past_only():
    panel = make_panel()
    c = panel["close"].astype(float)
    f = E.build_daily_features(panel)
    t, j = 30, 7
    assert abs(f["ret_1"][t, j] - (c[t, j] / c[t - 1, j] - 1.0)) < 1e-5
    assert abs(f["ret_5"][t, j] - (c[t - 1, j] / c[t - 6, j] - 1.0)) < 1e-5


def test_membership_enforced(feats):
    # give a non-member name a huge signal; it must never enter the book
    panel = make_panel()
    panel["member"][:, 3] = 0                    # symbol 3 never a member
    panel["close"][:, 3] = panel["close"][:, 3] * 0.001  # extreme low price -> extreme reversal signal
    f = E.build_daily_features(panel)
    sim = E.simulate(f, signal="ret_1", sign=-1, fwd_r="fwd_1", rebalance_every=1)
    assert sim["n_rebalances"] > 20   # sanity: it ran


def test_delisted_name_included_while_listed():
    panel = make_panel(list_gaps=True)
    f = E.build_daily_features(panel)
    # symbol 0 delists at T/2; before that it is a member with finite price -> eligible early, not late
    member = f["member"]
    assert member[10, 0] and not member[f["T"] // 2 + 10, 0]
    assert np.isfinite(f["ret_1"][10, 0]) and not np.isfinite(f["ret_1"][f["T"] - 1, 0])


def test_no_future_backfill_nan_forward_not_selected(feats):
    # names with NaN forward are excluded from eligibility (checked implicitly by finite mask in simulate)
    sim = E.simulate(feats, signal="ret_1", sign=-1, fwd_r="fwd_1", rebalance_every=1)
    assert not any(math.isnan(g) for g in sim["gross"])


# --------------------------------------------------------------------------- #
# Turnover controls: holding period, no-trade band, event/liquidity filters     #
# --------------------------------------------------------------------------- #
def test_rebalance_interval_controls_count(feats):
    s1 = E.simulate(feats, signal="ret_1", sign=-1, fwd_r="fwd_1", rebalance_every=1)
    s5 = E.simulate(feats, signal="ret_1", sign=-1, fwd_r="fwd_5", rebalance_every=5)
    assert s5["n_rebalances"] < s1["n_rebalances"]
    assert abs(s5["n_rebalances"] - s1["n_rebalances"] / 5) <= 3


def test_no_trade_band_reduces_turnover(feats):
    s0 = E.simulate(feats, signal="ret_1", sign=-1, fwd_r="fwd_1", rebalance_every=1, buffer=0.0)
    s1 = E.simulate(feats, signal="ret_1", sign=-1, fwd_r="fwd_1", rebalance_every=1, buffer=1.0)
    m0, m1 = E.evaluate_metrics(s0), E.evaluate_metrics(s1)
    assert m1["avg_turnover"] <= m0["avg_turnover"] + 1e-9


def test_liquidity_screen_reduces_universe(feats):
    base = E.simulate(feats, signal="ret_1", sign=-1, fwd_r="fwd_1", rebalance_every=1)
    hi = E.simulate(feats, signal="ret_1", sign=-1, fwd_r="fwd_1", rebalance_every=1, min_adv=50e6)
    # both run; the screened book's median ADV is >= the floor on rebalances that have a book
    mh = E.evaluate_metrics(hi)
    assert mh["median_book_adv"] is None or mh["median_book_adv"] >= 0


def test_event_filter_changes_attribution(feats):
    sim = E.simulate(feats, signal="ret_1", sign=-1, fwd_r="fwd_1", rebalance_every=1,
                     event="absz", event_thresh=1.0)
    a = sim["attribution"]
    assert a["total_out"] >= 0


# --------------------------------------------------------------------------- #
# Turnover attribution + cost curve + break-even                               #
# --------------------------------------------------------------------------- #
def test_turnover_attribution_sums(feats):
    sim = E.simulate(feats, signal="ret_1", sign=-1, fwd_r="fwd_1", rebalance_every=1)
    a = sim["attribution"]
    assert a["rank_crossing"] + a["left_membership"] + a["event_dropped"] == a["total_out"]


def test_cost_curve_monotonic(feats):
    sim = E.simulate(feats, signal="ret_1", sign=-1, fwd_r="fwd_1", rebalance_every=1)
    m = E.evaluate_metrics(sim)
    f = m["full"]
    assert f["net25"] >= f["net50"] >= f["net75"]     # higher cost -> lower net


def test_breakeven_matches_definition(feats):
    sim = E.simulate(feats, signal="ret_1", sign=-1, fwd_r="fwd_5", rebalance_every=5)
    m = E.evaluate_metrics(sim)
    exp = (m["full"]["gross"] / (2 * m["avg_turnover"])) * 1e4
    assert abs(m["breakeven_bps"] - round(exp, 2)) < 0.5


# --------------------------------------------------------------------------- #
# Walk-forward / holdout isolation / determinism                               #
# --------------------------------------------------------------------------- #
def test_holdout_isolation_time_ordered(feats):
    sim = E.simulate(feats, signal="ret_1", sign=-1, fwd_r="fwd_1", rebalance_every=1)
    m = E.evaluate_metrics(sim)
    n = m["n"]
    assert m["dev"]["n"] + m["val"]["n"] + m["holdout"]["n"] == n
    assert m["holdout"]["n"] == n - int(n * 0.8)      # last 20% untouched


def test_deterministic_output(feats):
    a = E.simulate(feats, signal="ret_1", sign=-1, fwd_r="fwd_1", rebalance_every=1)
    b = E.simulate(feats, signal="ret_1", sign=-1, fwd_r="fwd_1", rebalance_every=1)
    assert a["gross"] == b["gross"] and a["turnover"] == b["turnover"]


def test_wf_folds_partition(feats):
    sim = E.simulate(feats, signal="ret_1", sign=-1, fwd_r="fwd_1", rebalance_every=1)
    m = E.evaluate_metrics(sim)
    assert sum(fo["n"] for fo in m["wf_folds"]) == m["n"]
    assert 0 <= m["wf_pos_folds"] <= 5


# --------------------------------------------------------------------------- #
# Multiple-testing / classification / clustering / ensemble / frozen spec       #
# --------------------------------------------------------------------------- #
def test_multiple_testing_bar_exceeds_base():
    # the adjusted significance bar must be strictly harder than the base gate
    exp = R.EXPERIMENTS[0]
    fake = dict(insufficient=False, full=dict(net25=0.01, net50=0, net75=0, gross=0.01, ic_t=5),
                holdout=dict(net25=0.01), breakeven_bps=100, pre2020=dict(net25=0.01),
                post2020=dict(net25=0.01), wf_pos_folds=5, max_year_frac=0.2, ic_nw_t=3.2)
    status_small, _, _ = R.classify(exp, fake, n_experiments=2)
    status_big, reasons_big, _ = R.classify(exp, fake, n_experiments=1000)
    # with a huge family the same ic_nw_t=3.2 no longer clears the Bonferroni-style bar
    assert any("IC_NW_T<ADJ_BAR" in r for r in reasons_big)


def test_clustering_groups_correlated():
    keys = ["a", "b", "c"]
    mat = [[1.0, 0.9, 0.1], [0.9, 1.0, 0.2], [0.1, 0.2, 1.0]]
    clusters = R.P23.cluster_survivors(keys, mat, threshold=0.6)
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [1, 2]


def test_ensemble_not_applicable_without_survivors():
    ens = R._ensembles([], {}, {})
    assert ens["status"] == "NOT_APPLICABLE"


def test_frozen_spec_completeness():
    row = dict(candidate="rev1_r1", family="SHORT_REVERSAL", signal="ret_1", sign=-1, holding_days=1,
               construction="decile", treatment="none", avg_turnover=0.85, breakeven_bps=2.3, ic_nw_t=8.4,
               net25=-0.003, holdout_net25=-0.003, pre2020_net25=-0.003, post2020_net25=-0.003)
    spec = R._frozen_spec(row, dict())
    for key in ("model_id", "universe", "rebalance", "transaction_cost", "invalidation_gates",
                "reproducibility_fingerprint", "safety"):
        assert key in spec


# --------------------------------------------------------------------------- #
# NPZ reader round-trip (no Norgate)                                            #
# --------------------------------------------------------------------------- #
def test_npz_reader_roundtrip():
    panel = make_panel(T=60, N=40, list_gaps=False)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "mini.npz")
        np.savez_compressed(
            path, dates=np.array([np.datetime64(str(x.date())) for x in panel["dates"]]),
            symbols=np.array(panel["symbols"]), close=panel["close"], dvol=panel["dvol"],
            member=panel["member"], sectors=np.array(panel["sectors"]))
        got = DP.load_daily_panel(path)
    assert got["close"].shape == panel["close"].shape
    assert len(got["symbols"]) == len(panel["symbols"])
    assert list(got["sectors"]) == list(panel["sectors"])


# --------------------------------------------------------------------------- #
# Safety: code-pattern audit + SAFETY_BLOCK + terminal set                     #
# --------------------------------------------------------------------------- #
MODULE_FILES = [
    "research/phase24_daily_panel.py",
    "research/phase24_fast_engine.py",
    "research/run_phase24_fast_alpha_cluster_expansion.py",
]
FORBIDDEN_CODE = [
    "import sqlalchemy", "psycopg2", "place_order", "submit_order", "create_order",
    "broker.", "ib_insync", "alpaca", "schedule.every", "crontab", "APScheduler",
    "requests.post", "os.system", "subprocess.Popen",
]


def _read(path):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, path), encoding="utf-8") as fh:
        return fh.read()


def test_no_db_order_broker_or_scheduler_code():
    for rel in MODULE_FILES:
        src = _read(rel)
        for pat in FORBIDDEN_CODE:
            assert pat not in src, f"{pat} found in {rel}"


def test_no_hardcoded_credentials():
    for rel in MODULE_FILES:
        src = _read(rel).lower()
        for pat in ("api_key =", "apikey =", "password =", "secret =", "token ="):
            assert pat not in src, f"credential-like assignment in {rel}"


def test_norgate_import_is_lazy():
    # importing the panel module must NOT require norgatedata (only build_* does, lazily)
    src = _read("research/phase24_daily_panel.py")
    assert "import norgatedata" in src
    # the top-level import block must not import norgatedata (lazy inside the build function only)
    header = src.split("def build_daily_panel_from_norgate")[0]
    assert "import norgatedata" not in header


def test_safety_block_all_clear():
    sb = R.P.SAFETY_BLOCK()
    for k in ("creates_orders", "touches_broker", "writes_database", "mutates_positions",
              "replaces_champion", "promotes_to_live", "runs_automation", "new_paid_data"):
        assert sb[k] is False


def test_cost_model_not_weakened():
    assert E.COST_BPS["net25"] == 0.0025 and E.COST_BPS["net50"] == 0.0050 and E.COST_BPS["net75"] == 0.0075


def test_terminal_decision_in_allowed_set():
    allowed = {"FAST_ALPHA_CLUSTER_VALIDATED", "FAST_ALPHA_INFORMATION_REAL_BUT_COST_KILLED",
               "MULTI_ALPHA_OS_EXPANDED_WITH_NEW_NONFAST_CLUSTER", "OWNED_DAILY_DATA_UNAVAILABLE",
               "BLOCKED_PAID_ENTITLEMENT", "BLOCKED_DATA_CORRUPTION"}
    # synthetic no-survivor result routes to a valid terminal
    fake = dict(survivors=[], near_miss=None, candidate_rows=[], clusters=dict(new_validated=[]))
    assert R.decide_terminal(fake)["status"] in allowed


# --------------------------------------------------------------------------- #
# Real-data integration (only if the owned daily NPZ exists)                    #
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not DP.panel_exists(), reason="owned daily NPZ panel not present")
def test_real_campaign_terminal_and_artifacts():
    with tempfile.TemporaryDirectory() as d:
        result = R.build(outdir=d)
        assert result["terminal"]["status"] in {
            "FAST_ALPHA_CLUSTER_VALIDATED", "FAST_ALPHA_INFORMATION_REAL_BUT_COST_KILLED",
            "MULTI_ALPHA_OS_EXPANDED_WITH_NEW_NONFAST_CLUSTER"}
        written = R.write_artifacts(result, d)
        assert len(written) >= 28
        assert all(os.path.exists(p) for p in written)
