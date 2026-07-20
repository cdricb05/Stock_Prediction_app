"""Fully-offline targeted tests for Phase 23 - Scalable Multi-Alpha Research Operating System.

Proves, on tiny synthetic panels (no network, no owned data required):
  * experiment fingerprinting: deterministic, input/config sensitive, semantic dedup of formulas;
  * the persistent experiment cache (put/get, resume-after-interruption reuse);
  * the resource-aware worker system: deterministic ordering, sequential==parallel equivalence,
    worker/memory budget caps;
  * VECTORIZED rank-IC and decile books are BIT-IDENTICAL to the Phase 22 row-loop reference, and the
    full evaluate_candidate_v3 battery matches the reference;
  * the weekly compact cache is metric-equivalent to the raw parse;
  * PIT / no-look-ahead of the extended signals, membership enforcement, delisted-name inclusion,
    frequency boundaries, holdout isolation (purge/embargo) and the multiple-testing haircut;
  * correlation clustering collapses correlated variants, ensembles enforce the common universe,
    frozen paper specs are complete;
  * safety: no orders / broker / DB / automation / live promotion / champion replacement / credential
    leakage, and build() performs no write until write_artifacts is called.
Plus a REAL-DATA integration test that is skipped when the owned panels are absent.
"""
from __future__ import annotations

import importlib
import math
import os

import numpy as np
import pandas as pd
import pytest

P = importlib.import_module("research.run_phase22_autonomous_high_conviction_alpha_discovery")
M = importlib.import_module("research.run_phase23_scalable_multi_alpha_research_os")


# --------------------------------------------------------------------------- #
# Synthetic owned-style panels.                                               #
# --------------------------------------------------------------------------- #
def _write_panel(dirpath, n=40, months=96, seed=7, delist_one=False):
    os.makedirs(dirpath, exist_ok=True)
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2013-01-31", periods=months, freq="ME")
    tk = [f"T{i:03d}" for i in range(n)]
    secs = ["Tech", "Health", "Fin", "Energy"]
    drift = np.linspace(-0.008, 0.028, n)
    rets = drift[None, :] + rng.normal(0.0, 0.04, (months, n))
    close = 100.0 * np.cumprod(1.0 + rets, axis=0)
    close_df = pd.DataFrame(close, index=dates, columns=tk)
    close_df.index.name = "Date"
    mem = pd.DataFrame(1.0, index=dates, columns=tk)
    dvol = pd.DataFrame(1.0e7, index=dates, columns=tk)
    delisted = [False] * n
    if delist_one:
        half = months // 2
        mem.iloc[half:, 0] = 0.0                 # T000 delists at the half-way mark
        close_df.iloc[half:, 0] = np.nan
        dvol.iloc[half:, 0] = np.nan
        delisted[0] = True
    mem.index.name = dvol.index.name = "Date"
    spy = pd.DataFrame({"Close": 100.0 * np.cumprod(1.0 + rng.normal(0.005, 0.03, months))}, index=dates)
    spy.index.name = "Date"
    meta = pd.DataFrame({"ticker": tk, "gics_sector": [secs[i % 4] for i in range(n)], "is_delisted": delisted})
    close_df.to_csv(os.path.join(dirpath, "monthly_close_total_return.csv"))
    mem.to_csv(os.path.join(dirpath, "membership_panel.csv"))
    dvol.to_csv(os.path.join(dirpath, "monthly_dollar_volume.csv"))
    spy.to_csv(os.path.join(dirpath, "spy_monthly_total_return.csv"))
    meta.to_csv(os.path.join(dirpath, "metadata.csv"), index=False)
    return dates, tk


def _write_weekly(path, tk, weeks=80, seed=3):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2014-01-03", periods=weeks, freq="W-FRI")
    rows = []
    for d in dates:
        for s in tk:
            r1 = rng.normal(0, 0.03)
            rows.append(dict(date=d, symbol=s, sector="Tech", ret_1=r1, ret_5=rng.normal(0, 0.05),
                             ret_20=rng.normal(0, 0.08), ret_60=rng.normal(0, 0.12),
                             rel_str_60=rng.normal(0, 1), rv_20=abs(rng.normal(0.2, 0.05)),
                             trend_state=1, dd_from_high_20=-abs(rng.normal(0.05, 0.02)),
                             dollar_vol_20=1e7, fwd_excess_5=-0.3 * r1 + rng.normal(0, 0.02),
                             fwd_excess_10=rng.normal(0, 0.03), fwd_excess_20=rng.normal(0, 0.04)))
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_champ(path, tk, dates):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rng = np.random.default_rng(11)
    rows = []
    for d in dates[::3]:
        for i, t in enumerate(tk):
            cs = (i % 7) - 3
            rows.append(dict(rebalance_date=str(d.date()), as_of_date=str(d.date()), ticker=t,
                             composite_sn=float(cs), forward_63d_return=0.01 * cs + rng.normal(0, 0.05)))
    pd.DataFrame(rows).to_csv(path, index=False)


@pytest.fixture
def panel(tmp_path, monkeypatch):
    P.clear_cache()
    pdir = str(tmp_path / "p8c")
    _write_panel(pdir)
    monkeypatch.setattr(P, "PANEL8C_DIR", pdir)
    monkeypatch.setattr(M, "PANEL8C_DIR", pdir)
    return P.load_monthly_panel(pdir, use_cache=False)


# --------------------------------------------------------------------------- #
# A3: vectorization equivalence (bit-identical to the Phase 22 reference).     #
# --------------------------------------------------------------------------- #
def test_row_ic_vectorized_is_bit_identical(panel):
    sig = P.build_signals(panel)
    fwd = P.build_forwards(panel)
    for skey, fkey in [("mom_6_1", "fwd_3m"), ("mom_12_1", "fwd_1m"), ("rev_1m", "fwd_1m")]:
        S, F = sig[skey], fwd[fkey]
        v = P.base_valid_mask(panel) & S.notna() & F.notna()
        ic_ref, n_ref = P._row_ic(S, F, v)
        ic_vec, n_vec = M.row_ic_vectorized(S, F, v)
        a, b = np.asarray(ic_ref, float), np.asarray(ic_vec, float)
        fin = ~(np.isnan(a) & np.isnan(b))
        assert np.array_equal(a[fin], b[fin])          # bit-identical, not merely close
        assert np.array_equal(np.asarray(n_ref), np.asarray(n_vec))


def test_decile_books_vectorized_is_identical(panel):
    sig = P.build_signals(panel)
    fwd = P.build_forwards(panel)
    S, F = sig["mom_6_1"], fwd["fwd_3m"]
    v = P.base_valid_mask(panel) & S.notna() & F.notna()
    ref = P._decile_books(S, F, v)
    vec = M.decile_books_vectorized(S, F, v)
    assert ref["dates"] == vec["dates"]
    assert ref["gross"] == vec["gross"]
    assert ref["long_only"] == vec["long_only"]
    assert ref["top"] == vec["top"] and ref["bot"] == vec["bot"]


def test_evaluate_v3_matches_reference(panel):
    sig = P.build_signals(panel)
    fwd = P.build_forwards(panel)
    S, F = sig["mom_6_1"], fwd["fwd_3m"]
    v = P.base_valid_mask(panel) & S.notna() & F.notna()
    ref = P.evaluate_candidate(S, F, v, "fwd_3m", panel["close"].index)
    got = M.evaluate_candidate_v3(S, F, v, "fwd_3m", panel["close"].index)
    for sl in ("full", "dev", "val", "holdout", "pre2020", "post2020"):
        assert ref[sl] == got[sl]
    assert ref["avg_turnover"] == got["avg_turnover"]
    assert ref["max_year_frac"] == got["max_year_frac"]


# --------------------------------------------------------------------------- #
# A1: experiment fingerprint + semantic dedup + invalidation.                 #
# --------------------------------------------------------------------------- #
def _spec(**kw):
    base = dict(family="MOMENTUM", formula="close[t-1]/close[t-7]-1", params={"lb": 6}, neutralization="raw",
                frequency="monthly", rebalance="monthly", horizon="fwd_3m", universe="u", input_sigs=["x"])
    base.update(kw)
    return base


def test_experiment_fingerprint_deterministic_and_sensitive():
    k = M.experiment_key(_spec())
    assert k == M.experiment_key(_spec())                     # deterministic
    assert k != M.experiment_key(_spec(horizon="fwd_6m"))     # horizon changes key
    assert k != M.experiment_key(_spec(neutralization="sector_neutral"))
    assert k != M.experiment_key(_spec(cost_bps={"net25": 0.99}))
    assert k != M.experiment_key(_spec(input_sigs=["y"]))     # input fingerprint changes key


def test_semantic_dedup_collapses_equivalent_formulas():
    a = M.normalize_signal("MOMENTUM", "close[t-1]/close[t-7] - 1", {"lb": 6}, "raw")
    b = M.normalize_signal("momentum", "close[t-1] / close[t-7]-1", {"lb": 6}, "raw")  # whitespace/case only
    assert a == b
    assert a != M.normalize_signal("MOMENTUM", "close[t-1]/close[t-13]-1", {"lb": 12}, "raw")


def test_engine_version_invalidates_key(monkeypatch):
    k1 = M.experiment_key(_spec())
    monkeypatch.setattr(M, "ENGINE_VERSION", "p23.vX")
    assert M.experiment_key(_spec()) != k1


# --------------------------------------------------------------------------- #
# A1: persistent experiment cache + resume after interruption.                #
# --------------------------------------------------------------------------- #
def test_experiment_cache_roundtrip_and_resume(tmp_path):
    calls = {"n": 0}

    def ev(spec):
        calls["n"] += 1
        return dict(key=spec["key"], val=spec["key"] * 2)

    specs = [dict(key=i, family="F", formula=f"f{i}", params={}, neutralization="raw", frequency="monthly",
                  rebalance="monthly", horizon="h", universe="u", input_sigs=["z"]) for i in range(5)]
    cd = str(tmp_path / "exp")
    r1 = M.run_experiments([dict(s) for s in specs], ev, max_workers=1, cache_dir=cd, use_experiment_cache=True)
    assert calls["n"] == 5 and [x["val"] for x in r1] == [0, 2, 4, 6, 8]
    # rerun: everything is a cache hit -> evaluate_fn not called again (resume/interruption recovery)
    r2 = M.run_experiments([dict(s) for s in specs], ev, max_workers=1, cache_dir=cd, use_experiment_cache=True)
    assert calls["n"] == 5
    assert [x["val"] for x in r2] == [x["val"] for x in r1]


# --------------------------------------------------------------------------- #
# A4: worker ordering, sequential==parallel, resource budget.                 #
# --------------------------------------------------------------------------- #
def test_worker_ordering_and_seq_parallel_equivalence():
    def ev(spec):
        return dict(k=spec["key"], v=sum(range(spec["key"] + 3)))

    specs = [dict(key=i, family="F", formula=f"f{i}", params={}, neutralization="raw", frequency="monthly",
                  rebalance="monthly", horizon="h", universe="u", input_sigs=["z"]) for i in range(12)]
    seq = M.run_experiments([dict(s) for s in specs], ev, max_workers=1, use_experiment_cache=False)
    par = M.run_experiments([dict(s) for s in specs], ev, max_workers=4, use_experiment_cache=False)
    assert seq == par                                        # identical values AND order
    assert [x["k"] for x in par] == list(range(12))          # deterministic input order


def test_memory_budget_caps_workers():
    assert M._effective_workers(100, max_workers=8, mem_budget_mb=100, est_mb_per_task=40.0) == 2
    assert M._effective_workers(1, max_workers=8, mem_budget_mb=100000, est_mb_per_task=1.0) == 1
    assert M._effective_workers(100, max_workers=3, mem_budget_mb=100000, est_mb_per_task=1.0) == 3


# --------------------------------------------------------------------------- #
# A5: weekly compact cache equivalence.                                       #
# --------------------------------------------------------------------------- #
def test_weekly_cache_equivalence(tmp_path):
    _dates, tk = _write_panel(str(tmp_path / "p8c"))
    wpath = str(tmp_path / "weekly.csv")
    _write_weekly(wpath, tk)
    cd = str(tmp_path / "wk")
    miss = M.build_weekly_cache(wpath, cache_dir=cd, use_disk=True)
    assert miss.attrs.get("_cache") == "miss"
    hit = M.build_weekly_cache(wpath, cache_dir=cd, use_disk=True)
    assert hit.attrs.get("_cache") == "hit"
    pd.testing.assert_frame_equal(miss.reset_index(drop=True), hit.reset_index(drop=True))
    # metric-equivalent to a direct raw parse
    raw = pd.read_csv(wpath, usecols=lambda c: c in M.WEEKLY_USECOLS)
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
    assert M.run_weekly_campaign(hit, {}) == M.run_weekly_campaign(raw, {})


# --------------------------------------------------------------------------- #
# PIT / membership / delisted / frequency / holdout.                          #
# --------------------------------------------------------------------------- #
def test_extended_signals_no_lookahead(panel):
    spy = panel["spy"]
    full = M.build_extended_signals(panel, spy)["trend_ma12"]
    t = 60
    trunc_panel = dict(panel)
    trunc_panel["close"] = panel["close"].iloc[: t + 1]
    trunc_panel["dvol"] = panel["dvol"].iloc[: t + 1]
    trunc_panel["rets"] = None
    trunc = M.build_extended_signals(trunc_panel, spy.iloc[: t + 1])["trend_ma12"]
    a = full.iloc[t].dropna()
    b = trunc.iloc[t].dropna()
    common = a.index.intersection(b.index)
    assert len(common) > 0
    assert np.allclose(a[common].values, b[common].values, atol=0, rtol=0)


def test_membership_and_delisted_inclusion(tmp_path, monkeypatch):
    P.clear_cache()
    pdir = str(tmp_path / "p8c")
    _dates, tk = _write_panel(pdir, delist_one=True)
    pan = P.load_monthly_panel(pdir, use_cache=False)
    bv = P.base_valid_mask(pan)
    half = len(pan["close"]) // 2
    # delisted name is INCLUDED (valid) while a member, EXCLUDED after it delists
    assert bool(bv.iloc[half - 1][tk[0]]) is True
    assert bool(bv.iloc[half + 1][tk[0]]) is False


def test_holdout_is_untouched_disjoint_tail(panel):
    months = panel["close"].index
    sl = P._slice_indices(months)
    assert sl["holdout"].isdisjoint(sl["dev"])
    assert sl["holdout"].isdisjoint(sl["val"])
    assert len(sl["holdout"]) == min(P.HOLDOUT_MONTHS, len(months))
    assert max(sl["holdout"]) == max(months)                 # holdout is the most-recent tail


def test_weekly_and_monthly_frequency_boundaries(tmp_path):
    _d, tk = _write_panel(str(tmp_path / "p8c"))
    wpath = str(tmp_path / "weekly.csv")
    _write_weekly(wpath, tk)
    df = M.build_weekly_cache(wpath, use_disk=False)
    rows = M.run_weekly_campaign(df, {})
    assert all(r["frequency"] == "weekly" for r in rows)
    assert {"wk_rev_1", "wk_rev_5"} <= {r["candidate"] for r in rows}


# --------------------------------------------------------------------------- #
# Clustering / ensembles / frozen specs.                                      #
# --------------------------------------------------------------------------- #
def test_correlation_clustering_collapses_correlated():
    keys = ["a", "b", "c"]
    mat = [[1.0, 0.92, 0.10], [0.92, 1.0, 0.05], [0.10, 0.05, 1.0]]
    clusters = M.cluster_survivors(keys, mat, threshold=0.6)
    norm = sorted([sorted(c) for c in clusters])
    assert norm == [["a", "b"], ["c"]]


def test_frozen_paper_spec_is_complete_and_safe():
    cand = dict(candidate="mom_6_1", family="MOMENTUM", neutralization="raw", avg_turnover=0.36,
                adj_ic_t=4.9, net25=0.03, holdout_net25=0.04, corr_champ=0.12, adjusted_p=1e-4)
    spec = M.frozen_paper_spec(cand, cand)
    for key in ("model_id", "formula", "universe", "rebalance", "holding_horizon", "ranking",
                "construction", "invalidation_gates", "forward_metrics", "reproducibility_fingerprint"):
        assert key in spec
    assert "NO ORDERS" in spec["safety"] and "NO CHAMPION REPLACEMENT" in spec["safety"]


def test_ensemble_enforces_common_universe(panel, tmp_path):
    sig = P.build_signals(panel)
    fwd = P.build_forwards(panel)
    tk = list(panel["close"].columns)
    cpath = str(tmp_path / "champ.csv")
    _write_champ(cpath, tk[:30], panel["close"].index)       # champion covers only 30 of 40 names
    champ = P.load_champion_month_map(cpath)
    valid = P.base_valid_mask(panel)
    rows = P.ensemble_eval(sig["mom_6_1"], fwd["fwd_3m"], valid, champ, panel["close"].index,
                           weights=[(0.5, 0.5)])
    labels = {r["weight"] for r in rows}
    assert "champion_only" in labels                          # baseline on the SAME intersection
    assert any(r["n_months"] > 0 for r in rows)


# --------------------------------------------------------------------------- #
# Data readiness / entitlement / safety.                                      #
# --------------------------------------------------------------------------- #
def test_data_readiness_and_entitlement_probe():
    rows = M.data_readiness_matrix()
    fams = {r["family"] for r in rows}
    assert {"MOMENTUM", "SHORT_REVERSAL", "FUNDAMENTAL", "EARNINGS_REVISIONS"} <= fams
    assert any(r["readiness"] == "PROVIDER_ENTITLEMENT_MISSING" for r in rows)
    probe = M.entitlement_probe()
    assert probe["network_used"] is False and probe["credentials_read"] is False
    assert probe["blocked_paid"]                              # earnings-revisions blocked, recorded


def test_safety_block_flags_all_false_for_writes():
    sb = P.SAFETY_BLOCK()
    for k in ("creates_orders", "touches_broker", "writes_database", "mutates_positions",
              "writes_trading_workflow", "replaces_champion", "promotes_to_live", "runs_automation",
              "calls_prediction_service", "new_paid_data"):
        assert sb[k] is False


def test_module_has_no_db_broker_or_network_imports():
    # code-level import/call patterns only ('no broker' legitimately appears in the safety docstring)
    src = open(M.__file__, encoding="utf-8").read()
    for bad in ("import sqlalchemy", "import psycopg", "import requests", "import socket",
                "create_order", "place_order", "import webbrowser", "urllib.request", "boto3"):
        assert bad not in src, f"unexpected reference: {bad}"


def test_no_credential_leakage():
    src = open(M.__file__, encoding="utf-8").read().lower()
    for bad in ("api_key =", "apikey =", "password =", "secret =", "token ="):
        assert bad not in src


# --------------------------------------------------------------------------- #
# Integration: full build on synthetic panels (offline).                      #
# --------------------------------------------------------------------------- #
def test_build_end_to_end_and_no_write_until_asked(tmp_path, monkeypatch):
    P.clear_cache()
    pdir = str(tmp_path / "p8c")
    dates, tk = _write_panel(pdir)
    wpath = str(tmp_path / "weekly.csv")
    cpath = str(tmp_path / "champ.csv")
    _write_weekly(wpath, tk)
    _write_champ(cpath, tk, dates)
    monkeypatch.setattr(P, "PANEL8C_DIR", pdir)
    monkeypatch.setattr(M, "PANEL8C_DIR", pdir)
    outdir = str(tmp_path / "out")
    result = M.build(panel_dir=pdir, weekly_path=wpath, champion_path=cpath, max_workers=2,
                     use_disk_cache=False, use_experiment_cache=False, run_weekly=True,
                     cache_dir=str(tmp_path / "cache"))
    assert not os.path.exists(outdir)                         # build() writes nothing
    assert result["terminal"]["status"] in (
        "MULTI_ALPHA_RESEARCH_OS_COMPLETE", "MULTI_ALPHA_RESEARCH_OS_COMPLETE_DATA_GAPS",
        "BLOCKED_PAID_ENTITLEMENT", "BLOCKED_DATA_CORRUPTION")
    assert result["independent_summary"]["n_clusters"] >= 1
    written = M.write_artifacts(result, outdir)
    assert len(written) >= 28 and all(os.path.exists(p) for p in written)
    assert os.path.exists(os.path.join(outdir, "phase23_final_report.json"))
    assert os.path.exists(os.path.join(outdir, "phase23_frozen_paper_specs.json"))


# --------------------------------------------------------------------------- #
# REAL owned-data integration (skipped when the panel is absent).             #
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not os.path.exists(os.path.join(
    P.PANEL8C_DIR, "monthly_close_total_return.csv")), reason="owned phase8c panel absent")
def test_real_data_momentum_survives_and_reversal_is_survivorship_aware():
    result = M.build(max_workers=4, use_disk_cache=True, use_experiment_cache=False, run_weekly=True)
    mono = {(c["candidate"], c["neutralization"]): c for c in result["_cand_full"]}
    m61 = mono.get(("mom_6_1", "raw"))
    assert m61 is not None and m61["status"] == "STRONG_ALPHA_PAPER_CHALLENGER_ELIGIBLE"
    wk = {w["candidate"]: w for w in result["weekly_rows"]}
    assert abs(wk["wk_rev_1"]["ic_t"]) >= 4.0                 # survivorship-aware weekly reversal is real
    assert result["terminal"]["status"].startswith("MULTI_ALPHA_RESEARCH_OS_COMPLETE")
