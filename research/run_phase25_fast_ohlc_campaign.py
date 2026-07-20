"""Phase 25 Track B - focused FAST OHLC alpha research campaign.

WHY THIS PHASE EXISTS
    Phase 24 proved close-to-close short-horizon reversal is INFORMATION_ONLY_NOT_TRADABLE (IC t~8.4
    but break-even ~2.3bps << 25bps) and identified the precise next owned-data action: use Norgate
    Open/High/Low to test mechanisms that need the intraday shape of the bar - overnight gaps, intraday
    moves, ranges, close location, compression breakouts - under explicit executable-timing conventions.
    This campaign is that test.  It is NOT another close-to-close reversal tuning pass.

    Universe: the Phase 25 survivorship-free daily OHLC panel (Norgate Russell 1000 Current & Past,
    3,076 symbols incl. 2,576 delisted retained, 2000-2026, TOTALRETURN O/H/L/C, PIT membership).
    Every experiment declares its timing convention (T1 close-signal/next-open-exec, T2 open-signal/
    open-exec, T3 close-signal/close-exec-overnight); T2/T3 use the same print that completes the
    signal, so they additionally must survive an explicit slippage stress (10bps/side) to qualify.
    The validation battery is REUSED from Phase 24 (never weakened): multi-cost net25/50/75, turnover
    attribution, break-even, dev/val/untouched-holdout, walk-forward, subperiods, regimes, cohorts,
    Bonferroni-style multiple-testing bar, concentration, parameter neighbours, clustering.

    RESEARCH-ONLY and PAPER-ONLY.  No orders, no broker, no automation, no live promotion, no champion
    replacement, no Paper Trader DB writes, no prediction-service calls, no new paid data (Norgate is
    owned, read-only, not upgraded), no network beyond the local owned service.

TERMINAL DECISIONS (exactly one)
    FAST_OHLC_ALPHA_VALIDATED | FAST_OHLC_INFORMATION_REAL_BUT_NOT_TRADABLE |
    NO_FAST_OHLC_ALPHA_FOUND | BLOCKED_FAST_OHLC_DATA
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import time

import numpy as np
import pandas as pd

from research import run_phase22_autonomous_high_conviction_alpha_discovery as P
from research import run_phase23_scalable_multi_alpha_research_os as P23
from research import phase25_ohlc_panel as OP
from research import phase25_fast_ohlc_engine as E

REPO_ROOT = P.REPO_ROOT
DATA_ROOT = P.DATA_ROOT
STORE_DIR = os.path.join(DATA_ROOT, "phase25_fast_ohlc")                      # primary artifact store (D:)
OUTPUT_DIR = os.path.join(REPO_ROOT, "research", "output", "phase25_fast_ohlc_campaign")  # compact mirror (git)
ENGINE_VERSION = "p25.v1"

# Existing validated clusters (Phase 23/24; retained, cited - never re-searched or weakened here).
EXISTING_CLUSTERS = [
    dict(cluster="fundamental_slow", model="composite_sn", horizon="slow(63d)",
         evidence=dict(ic_nw_t=2.93, net25=0.0102), source="Phase 10-D/17 committed"),
    dict(cluster="momentum_medium", model="mom_6_1", horizon="medium(63d)",
         evidence=dict(ic_nw_t=4.96, net25=0.0296, holdout_net25=0.0415, corr_champ=0.12),
         source="Phase 22/23 committed"),
    dict(cluster="lowvol_medium", model="lowvol_12m_sn", horizon="medium(63d)",
         evidence=dict(note="orthogonal but regime-fragile; risk overlay only"), source="Phase 23 committed"),
    dict(cluster="fast_reversal_information_only", model="short_reversal_close_to_close",
         horizon="fast(1-5d)", evidence=dict(ic_t=8.4, breakeven_bps=2.3, verdict="COST_KILLED"),
         source="Phase 24 committed ed28bb5"),
]

# Strict FAST-alpha qualification gate.  Same shape as Phase 24 (never weakened) + the slippage
# stress for the same-print timing conventions.
FAST_GATE = dict(ic_nw_t_min=3.0, net25_min=0.0, holdout_net25_min=0.0, breakeven_bps_min=25.0,
                 wf_pos_folds_min=4, max_year_frac_max=0.6, subperiod_both_positive=True,
                 slip_stress_required_for=("T2", "T3"), min_rebalances=100, min_book_adv=1e7)


# =========================================================================== #
# Registered hypotheses (declared BEFORE results are examined)                  #
# =========================================================================== #
FAST_HYPOTHESES = [
    dict(key="gap_reversal", family="OVERNIGHT_GAP_REVERSAL",
         mechanism="Overnight gaps overshoot (illiquid auction pricing); intraday session fades them.",
         expected="strong gross IC; full daily round-trip so cost bar is very high",
         turnover_prior="FULL (1.0/day)", relationship="genuinely new (needs Open; Phase 24 could not test)"),
    dict(key="gap_continuation", family="OVERNIGHT_GAP_CONTINUATION",
         mechanism="Informative (news-driven) gaps continue in the following sessions (PEAD-like drift).",
         expected="weaker but lower-frequency; volume confirmation should matter",
         turnover_prior="MEDIUM", relationship="candidate new fast-drift cluster"),
    dict(key="overnight_drift", family="INTRADAY_TO_OVERNIGHT",
         mechanism="Intraday winners/losers drift or revert over the following night (flow imbalance at close).",
         expected="small per-period edge; full nightly round trip",
         turnover_prior="FULL (1.0/night)", relationship="new overnight-session mechanism"),
    dict(key="close_location", family="CLOSE_LOCATION",
         mechanism="Close near the high/low of the day's range signals auction pressure that spills into the next session(s).",
         expected="modest continuation short-term", turnover_prior="HIGH", relationship="bar-shape mechanism"),
    dict(key="shock_recovery", family="MULTI_DAY_SHOCK_RECOVERY",
         mechanism="Multi-day standardized shocks recover over the next days (next-open executable).",
         expected="reversal-family economics but event-gated at longer horizon",
         turnover_prior="MEDIUM-HIGH", relationship="event-gated cousin of the Phase 24 reversal (executable timing differs)"),
    dict(key="compression_breakout", family="VOL_COMPRESSION_BREAKOUT",
         mechanism="Range compression then a close above the prior 5d high starts a short trend leg.",
         expected="low-frequency, medium turnover; capacity-friendly", turnover_prior="MEDIUM",
         relationship="candidate new range/trend cluster"),
    dict(key="range_expansion", family="RANGE_EXPANSION_CONTINUATION",
         mechanism="Directional wide-range days with volume confirmation continue for 1-2 sessions.",
         expected="high turnover; continuation vs reversal contest", turnover_prior="HIGH",
         relationship="bar-shape event mechanism"),
    dict(key="vol_accel", family="VOL_ACCELERATION",
         mechanism="Short-run realized-vol acceleration mean-reverts (fade names whose vol just spiked).",
         expected="overlaps low-vol cluster at faster horizon", turnover_prior="MEDIUM",
         relationship="fast diagnostic cousin of the low-vol cluster"),
]

# Experiment grid.  Each declares (signal, sign, fwd, r, timing, treatments).  ~27 registered runs;
# neighbours of the same mechanism double as parameter-stability probes.
EXPERIMENTS = [
    # --- H1 overnight gap reversal (T2 open-exec, intraday session) ---------------------------------- #
    dict(key="gapfade_oc_raw", hyp="gap_reversal", signal="on_gap", sign=-1, fwd="fwd_oc", r=1, timing="T2"),
    dict(key="gapfade_oc_resid", hyp="gap_reversal", signal="resid_gap", sign=-1, fwd="fwd_oc", r=1, timing="T2"),
    dict(key="gapfade_oc_z", hyp="gap_reversal", signal="gap_z", sign=-1, fwd="fwd_oc", r=1, timing="T2"),
    dict(key="gapfade_oc_extreme", hyp="gap_reversal", signal="gap_z", sign=-1, fwd="fwd_oc", r=1, timing="T2",
         event="gap_z", event_thresh=2.0),
    dict(key="gapfade_oc_secneu", hyp="gap_reversal", signal="sec_resid_gap", sign=-1, fwd="fwd_oc", r=1,
         timing="T2", sector_neutral=True),
    dict(key="gapfade_oc_highliq", hyp="gap_reversal", signal="resid_gap", sign=-1, fwd="fwd_oc", r=1,
         timing="T2", min_adv=100e6),
    dict(key="gapfade_oc_volconf", hyp="gap_reversal", signal="resid_gap", sign=-1, fwd="fwd_oc", r=1,
         timing="T2", event="dvol_surge", event_thresh=1.0),
    # --- H2 gap continuation (T1 next-open exec) ----------------------------------------------------- #
    dict(key="gapcont_oo1", hyp="gap_continuation", signal="on_gap", sign=1, fwd="fwd_oo_1", r=1, timing="T1"),
    dict(key="gapcont_oo2", hyp="gap_continuation", signal="on_gap", sign=1, fwd="fwd_oo_2", r=2, timing="T1"),
    dict(key="gapcont_oo5", hyp="gap_continuation", signal="on_gap", sign=1, fwd="fwd_oo_5", r=5, timing="T1"),
    dict(key="gapcont_volconf_oo5", hyp="gap_continuation", signal="gap_z", sign=1, fwd="fwd_oo_5", r=5,
         timing="T1", event="dvol_surge", event_thresh=2.0),
    dict(key="gapfade_next_noc", hyp="gap_reversal", signal="resid_gap", sign=-1, fwd="fwd_noc", r=1,
         timing="T1"),   # gap fade DELAYED to the next session (decay probe; strictly next-open executable)
    # --- H3 overnight drift (T3 close-exec, overnight session) --------------------------------------- #
    dict(key="overnight_rev", hyp="overnight_drift", signal="intraday", sign=-1, fwd="fwd_co", r=1, timing="T3"),
    dict(key="overnight_cont", hyp="overnight_drift", signal="intraday", sign=1, fwd="fwd_co", r=1, timing="T3"),
    dict(key="overnight_cloc", hyp="overnight_drift", signal="cloc", sign=1, fwd="fwd_co", r=1, timing="T3"),
    dict(key="overnight_cont_highliq", hyp="overnight_drift", signal="intraday", sign=1, fwd="fwd_co", r=1,
         timing="T3", min_adv=100e6),
    # --- H4 close-location next-day (T1) ------------------------------------------------------------- #
    dict(key="cloc_cont_noc", hyp="close_location", signal="cloc", sign=1, fwd="fwd_noc", r=1, timing="T1"),
    dict(key="cloc_rev_oo5", hyp="close_location", signal="cloc", sign=-1, fwd="fwd_oo_5", r=5, timing="T1"),
    # --- H5 multi-day shock recovery (T1, event-gated) ----------------------------------------------- #
    dict(key="shockrec_oo3", hyp="shock_recovery", signal="ret5_z", sign=-1, fwd="fwd_oo_3", r=3, timing="T1",
         event="ret5_z", event_thresh=2.0),
    dict(key="shockrec_volconf_oo5", hyp="shock_recovery", signal="ret5_z", sign=-1, fwd="fwd_oo_5", r=5,
         timing="T1", event="dvol_surge", event_thresh=1.0),
    # --- H6 compression breakout (T1) ---------------------------------------------------------------- #
    dict(key="breakout5_oo5", hyp="compression_breakout", signal="breakout5", sign=1, fwd="fwd_oo_5", r=5,
         timing="T1"),
    dict(key="breakout5_compress_oo5", hyp="compression_breakout", signal="breakout5", sign=1, fwd="fwd_oo_5",
         r=5, timing="T1", event="compress_score", event_thresh=0.5),
    dict(key="breakout5_inside_oo5", hyp="compression_breakout", signal="breakout5", sign=1, fwd="fwd_oo_5",
         r=5, timing="T1", event="inside_prev", event_thresh=0.5),
    dict(key="breakout5_oo10", hyp="compression_breakout", signal="breakout5", sign=1, fwd="fwd_oo_10", r=10,
         timing="T1"),
    # --- H7 range expansion continuation (T1) -------------------------------------------------------- #
    dict(key="rangeexp_cont_oo2", hyp="range_expansion", signal="absz", sign=1, fwd="fwd_oo_2", r=2,
         timing="T1", event="range_z", event_thresh=2.0),
    # --- H8 vol acceleration reversion (T1) ---------------------------------------------------------- #
    dict(key="rvaccel_rev_oo5", hyp="vol_accel", signal="rvaccel", sign=-1, fwd="fwd_oo_5", r=5, timing="T1"),
    # --- medium anchor (context / correlation reference; not a fast candidate) ----------------------- #
    dict(key="mom126_21_oo21", hyp="compression_breakout", signal="mom_126_21", sign=1, fwd="fwd_oo_21",
         r=21, timing="T1", anchor=True),
]


def _spec_kwargs(exp):
    kw = dict(signal=exp["signal"], sign=exp["sign"], fwd_r=exp["fwd"], rebalance_every=exp["r"])
    for k in ("top_k", "event", "event_thresh", "min_adv", "sector_neutral"):
        if k in exp:
            kw[k] = exp[k]
    return kw


def experiment_key(exp):
    payload = dict(engine=ENGINE_VERSION, universe="russell1000_cp_ohlc", **{k: exp.get(k) for k in
                   ("signal", "sign", "fwd", "r", "timing", "event", "event_thresh", "min_adv", "sector_neutral")})
    return P._fingerprint(P23._canon(payload))


# =========================================================================== #
# Strict qualification gate                                                     #
# =========================================================================== #
def classify(exp, m, n_experiments):
    """Apply the strict fast-alpha gate. Returns (status, reasons, info_real)."""
    if m.get("insufficient"):
        return "REJECTED", ["INSUFFICIENT_OBSERVATIONS"], False
    f = m["full"]
    reasons = []
    adj_bar = FAST_GATE["ic_nw_t_min"] + math.sqrt(2 * math.log(max(2, n_experiments)))
    info_real = abs(m["ic_nw_t"] or 0) >= adj_bar
    if m["n"] < FAST_GATE["min_rebalances"]:
        reasons.append(f"TOO_FEW_REBALANCES({m['n']})")
    if (f["net25"] or -1) <= FAST_GATE["net25_min"]:
        reasons.append(f"NET25<=0({f['net25']})")
    if (m["holdout"]["net25"] or -1) <= FAST_GATE["holdout_net25_min"]:
        reasons.append(f"HOLDOUT_NET25<=0({m['holdout']['net25']})")
    if m["breakeven_bps"] < FAST_GATE["breakeven_bps_min"]:
        reasons.append(f"BREAKEVEN<25bps({m['breakeven_bps']})")
    if FAST_GATE["subperiod_both_positive"]:
        pre = (m["pre2020"] or {}).get("net25")
        post = (m["post2020"] or {}).get("net25")
        if pre is None or post is None or pre <= 0 or post <= 0:
            reasons.append(f"SUBPERIOD_NOT_BOTH_POS(pre={pre},post={post})")
    if m["wf_pos_folds"] < FAST_GATE["wf_pos_folds_min"]:
        reasons.append(f"WF_POS_FOLDS<4({m['wf_pos_folds']})")
    if not (isinstance(m["max_year_frac"], float) and not math.isnan(m["max_year_frac"])
            and m["max_year_frac"] < FAST_GATE["max_year_frac_max"]):
        reasons.append(f"CONCENTRATED_YEAR({m['max_year_frac']})")
    if not info_real:
        reasons.append(f"IC_NW_T<ADJ_BAR({m['ic_nw_t']}<{round(adj_bar, 2)})")
    if (m.get("median_book_adv") or 0) < FAST_GATE["min_book_adv"]:
        reasons.append(f"BOOK_ADV<10M({m.get('median_book_adv')})")
    # same-print timing conventions must survive the slippage stress
    if exp["timing"] in FAST_GATE["slip_stress_required_for"]:
        if (m.get("net25_slip10") or -1) <= 0:
            reasons.append(f"SLIPPAGE_STRESS_FAILED(net25_slip10={m.get('net25_slip10')})")
        if (m.get("holdout_net25_slip10") or -1) <= 0:
            reasons.append(f"SLIPPAGE_STRESS_HOLDOUT_FAILED({m.get('holdout_net25_slip10')})")
    if exp.get("anchor"):
        reasons.append("ANCHOR_NOT_A_FAST_CANDIDATE")
    status = "FAST_ALPHA_PAPER_CHALLENGER_ELIGIBLE" if not reasons else "REJECTED"
    return status, (reasons or ["PASS"]), info_real


def reject_bucket(reasons):
    """Collapse the reason list into the Phase 25 status vocabulary (reason PREFIXES, not substrings)."""
    if "PASS" in reasons:
        return "FAST_ALPHA_PAPER_CHALLENGER_ELIGIBLE"
    tags = {r.split("(")[0] for r in reasons}
    cost_tags = {"NET25<=0", "BREAKEVEN<25bps", "SLIPPAGE_STRESS_FAILED",
                 "SLIPPAGE_STRESS_HOLDOUT_FAILED"}
    if "HOLDOUT_NET25<=0" in tags and not (tags & cost_tags):
        return "HOLDOUT_FAILED"
    if tags & cost_tags:
        return "COST_KILLED"
    return "REJECTED"


def cost_curve_row(exp, m):
    f = m["full"]
    return dict(candidate=exp["key"], timing=exp["timing"], gross=f["gross"], net25=f["net25"],
                net50=f["net50"], net75=f["net75"], net25_slip10=m.get("net25_slip10"),
                avg_turnover=m["avg_turnover"], breakeven_bps=m["breakeven_bps"], holding_days=exp["r"])


def turnover_attr_row(exp, m):
    a = m["attribution"]
    tot = max(1, a["total_out"])
    full_liq = m.get("full_liquidation")
    return dict(candidate=exp["key"], avg_turnover=m["avg_turnover"], holding_days=exp["r"],
                full_liquidation=bool(full_liq), names_out=a["total_out"],
                frac_rank_crossing=(1.0 if full_liq else P._round(a["rank_crossing"] / tot, 3)),
                frac_left_membership=(0.0 if full_liq else P._round(a["left_membership"] / tot, 3)),
                frac_event_dropped=(0.0 if full_liq else P._round(a["event_dropped"] / tot, 3)),
                breakeven_bps=m["breakeven_bps"], max_affordable_cost_bps=m["breakeven_bps"])


# =========================================================================== #
# Correlation clustering                                                        #
# =========================================================================== #
def _monthly_stream(sim, field="gross"):
    if not sim["rebalance_dates"]:
        return pd.Series(dtype=float)
    s = pd.Series(sim[field], index=pd.DatetimeIndex(sim["rebalance_dates"]))
    return s.resample("ME").sum()


def correlation_matrix(sims_by_key, keys):
    streams = {k: _monthly_stream(sims_by_key[k]) for k in keys if k in sims_by_key}
    keys = [k for k in keys if k in streams and not streams[k].empty]
    mat = [[None] * len(keys) for _ in keys]
    for i, ki in enumerate(keys):
        mat[i][i] = 1.0
        for j in range(i + 1, len(keys)):
            a, b = streams[ki].align(streams[keys[j]], join="inner")
            c = float(a.corr(b)) if len(a) >= 12 else None
            mat[i][j] = mat[j][i] = None if c is None or math.isnan(c) else round(c, 3)
    return keys, mat


def cluster_candidates(keys, mat, threshold=0.7):
    """Single-linkage grouping of |corr| >= threshold."""
    parent = {k: k for k in keys}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i, ki in enumerate(keys):
        for j in range(i + 1, len(keys)):
            c = mat[i][j]
            if c is not None and abs(c) >= threshold:
                parent[find(keys[j])] = find(ki)
    groups = {}
    for k in keys:
        groups.setdefault(find(k), []).append(k)
    return list(groups.values())


# =========================================================================== #
# Orchestration                                                                 #
# =========================================================================== #
def build(npz_path=None):
    t_all = time.perf_counter()
    stage = []

    def _stage(name, fn):
        t0 = time.perf_counter()
        v = fn()
        stage.append(dict(stage=name, seconds=round(time.perf_counter() - t0, 3)))
        return v

    if not OP.panel_exists(npz_path):
        return dict(terminal=dict(decision="BLOCKED_FAST_OHLC_DATA",
                                  detail=f"OHLC NPZ missing at {npz_path or OP.NPZ_PATH}"),
                    stage_timing=stage)
    manifest = {}
    if os.path.exists(OP.MANIFEST_PATH):
        with open(OP.MANIFEST_PATH) as fh:
            manifest = json.load(fh)

    panel = _stage("LOAD_OHLC_PANEL", lambda: OP.load_ohlc_panel(npz_path))
    feats = _stage("BUILD_OHLC_FEATURES", lambda: E.build_ohlc_features(panel))
    # cohort tags: current member at the final panel date vs delisted/removed
    feats["_is_current"] = panel["member"][-1].astype(bool)
    feats["_symbols"] = panel["symbols"]
    # free the raw O/H/L matrices (features hold what they need)
    for k in ("open", "high", "low", "dvol"):
        panel[k] = None

    n_exp = len(EXPERIMENTS)
    sims_by_key, results = {}, []

    def _run_all():
        for exp in EXPERIMENTS:
            sim = E.simulate(feats, **_spec_kwargs(exp))
            m = E.evaluate_ohlc_metrics(sim)
            status, reasons, info_real = classify(exp, m, n_exp)
            sims_by_key[exp["key"]] = sim
            results.append(dict(exp=exp, metrics=m, status=status, reasons=reasons,
                                info_real=info_real, exp_fingerprint=experiment_key(exp)))
        return True

    _stage("FAST_OHLC_CAMPAIGN", _run_all)

    # regimes + cohorts for every non-insufficient candidate
    regime_rows, cohort_rows = [], []

    def _slices():
        for res in results:
            exp = res["exp"]
            if res["metrics"].get("insufficient"):
                continue
            reg = E.regime_slices(sims_by_key[exp["key"]], feats)
            for rname, rv in reg.items():
                regime_rows.append(dict(candidate=exp["key"], regime=rname, **rv))
            coh = E.cohort_slices(feats, dict(signal=exp["signal"], sign=exp["sign"], fwd=exp["fwd"],
                                              rebalance_every=exp["r"]), feats["_is_current"])
            for cname, cv in coh.items():
                cohort_rows.append(dict(candidate=exp["key"], cohort=cname, **cv))
        return True

    _stage("REGIME_COHORT_SLICES", _slices)

    # correlation + clustering over all evaluated candidates
    keys = [r["exp"]["key"] for r in results if not r["metrics"].get("insufficient")]
    corr_keys, corr_mat = _stage("CORRELATION", lambda: correlation_matrix(sims_by_key, keys))
    clusters = cluster_candidates(corr_keys, corr_mat)

    # neighbour stability: within each hypothesis, do same-mechanism variants agree in net25 sign?
    by_hyp = {}
    for r in results:
        if not r["metrics"].get("insufficient") and not r["exp"].get("anchor"):
            by_hyp.setdefault(r["exp"]["hyp"], []).append(r)
    neighbor_rows = []
    for hyp, rs in by_hyp.items():
        nets = [(r["exp"]["key"], (r["metrics"]["full"]["net25"] or 0)) for r in rs]
        pos = sum(1 for _k, v in nets if v > 0)
        neighbor_rows.append(dict(hypothesis=hyp, n_variants=len(nets), n_net25_positive=pos,
                                  sign_stable=(pos == 0 or pos == len(nets)),
                                  variants=";".join(k for k, _v in nets)))

    survivors = [r for r in results if r["status"] == "FAST_ALPHA_PAPER_CHALLENGER_ELIGIBLE"]
    info_real_any = [r for r in results if r["info_real"] and not r["exp"].get("anchor")]

    # terminal decision
    if survivors:
        best = max(survivors, key=lambda r: r["metrics"]["full"]["net25"] or -9)
        decision = "FAST_OHLC_ALPHA_VALIDATED"
        detail = dict(model=best["exp"]["key"], metrics=best["metrics"]["full"],
                      breakeven_bps=best["metrics"]["breakeven_bps"])
    elif info_real_any:
        best = max(info_real_any, key=lambda r: abs(r["metrics"]["ic_nw_t"] or 0))
        bm = best["metrics"]
        decision = "FAST_OHLC_INFORMATION_REAL_BUT_NOT_TRADABLE"
        detail = dict(best_mechanism=best["exp"]["key"], hypothesis=best["exp"]["hyp"],
                      timing=best["exp"]["timing"], ic_nw_t=bm["ic_nw_t"], gross=bm["full"]["gross"],
                      net25=bm["full"]["net25"], avg_turnover=bm["avg_turnover"],
                      breakeven_bps=bm["breakeven_bps"],
                      binding_failure=";".join(best["reasons"][:3]))
    else:
        decision = "NO_FAST_OHLC_ALPHA_FOUND"
        detail = dict(hypotheses_tested=len(FAST_HYPOTHESES), experiments=len(EXPERIMENTS),
                      main_rejection_reasons=_top_reasons(results))

    frozen = dict(
        phase="25", qualified=bool(survivors), terminal=decision,
        frozen_specs=[dict(model=r["exp"]["key"], hypothesis=r["exp"]["hyp"], timing=r["exp"]["timing"],
                           signal=r["exp"]["signal"], sign=r["exp"]["sign"], fwd=r["exp"]["fwd"],
                           rebalance_every=r["exp"]["r"],
                           treatments={k: r["exp"][k] for k in ("event", "event_thresh", "min_adv",
                                                                "sector_neutral", "top_k") if k in r["exp"]},
                           metrics=r["metrics"]["full"], breakeven_bps=r["metrics"]["breakeven_bps"],
                           holdout=r["metrics"]["holdout"], exp_fingerprint=r["exp_fingerprint"],
                           cost_model="net = gross - 2*cost*turnover; cost 25/50/75bps (+10bps/side "
                                      "slippage stress for same-print timings); NEVER weakened")
                      for r in survivors],
        cost_model_never_weakened=True,
        note=("A fast sleeve in Paper Trader activates ONLY when qualified=true. "
              "No qualification -> the sleeve stays inactive (NO_VALIDATED_FAST_ALPHA)."))

    elapsed = round(time.perf_counter() - t_all, 1)
    return dict(
        manifest=manifest, results=results, sims_by_key=sims_by_key,
        regime_rows=regime_rows, cohort_rows=cohort_rows,
        corr_keys=corr_keys, corr_mat=corr_mat, clusters=clusters, neighbor_rows=neighbor_rows,
        survivors=survivors, terminal=dict(decision=decision, detail=detail),
        frozen=frozen, stage_timing=stage, elapsed_seconds=elapsed, n_experiments=len(EXPERIMENTS))


def _top_reasons(results):
    counts = {}
    for r in results:
        if r["status"] == "REJECTED":
            for reason in r["reasons"]:
                tag = reason.split("(")[0]
                counts[tag] = counts.get(tag, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1])[:6])


# =========================================================================== #
# Artifacts                                                                     #
# =========================================================================== #
def write_artifacts(result):
    os.makedirs(STORE_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    def _both(name, writer):
        writer(os.path.join(STORE_DIR, name))
        writer(os.path.join(OUTPUT_DIR, name))

    results = result["results"]

    _both("daily_ohlc_manifest.json", lambda p: P._write_json(p, result["manifest"]))
    _both("hypothesis_registry.csv", lambda p: P._write_csv(p, [dict(
        key=h["key"], family=h["family"], mechanism=h["mechanism"], expected=h["expected"],
        turnover_prior=h["turnover_prior"], relationship=h["relationship"]) for h in FAST_HYPOTHESES]))
    _both("experiment_registry.csv", lambda p: P._write_csv(p, [dict(
        key=e["key"], hypothesis=e["hyp"], signal=e["signal"], sign=e["sign"], fwd=e["fwd"], r=e["r"],
        timing=e["timing"], event=e.get("event"), event_thresh=e.get("event_thresh"),
        min_adv=e.get("min_adv"), sector_neutral=e.get("sector_neutral"),
        anchor=bool(e.get("anchor")), fingerprint=experiment_key(e)) for e in EXPERIMENTS]))

    cand_rows = []
    for r in results:
        m = r["metrics"]
        if m.get("insufficient"):
            cand_rows.append(dict(candidate=r["exp"]["key"], status=r["status"], insufficient=True))
            continue
        f = m["full"]
        cand_rows.append(dict(
            candidate=r["exp"]["key"], hypothesis=r["exp"]["hyp"], timing=r["exp"]["timing"],
            status=r["status"], n=m["n"], mean_ic=f["mean_ic"], ic_t=f["ic_t"], ic_nw_t=m["ic_nw_t"],
            gross=f["gross"], net25=f["net25"], net50=f["net50"], net75=f["net75"],
            net25_slip10=m.get("net25_slip10"), avg_turnover=m["avg_turnover"],
            breakeven_bps=m["breakeven_bps"], holdout_net25=m["holdout"]["net25"],
            wf_pos_folds=m["wf_pos_folds"], max_year_frac=m["max_year_frac"],
            maxdd_net25=m["maxdd_net25"], median_book_adv=m["median_book_adv"],
            pos_ic_rate=m["pos_ic_rate"], info_real=r["info_real"],
            reasons=";".join(r["reasons"])))
    _both("candidate_metrics.csv", lambda p: P23._write_csv_union(p, cand_rows))

    wf_rows, hold_rows = [], []
    for r in results:
        m = r["metrics"]
        if m.get("insufficient"):
            continue
        for fi, fo in enumerate(m["wf_folds"]):
            wf_rows.append(dict(candidate=r["exp"]["key"], fold=fi, n=fo["n"], net25=fo["net25"],
                                ic_t=fo["ic_t"], gross=fo["gross"]))
        hold_rows.append(dict(candidate=r["exp"]["key"], dev_net25=m["dev"]["net25"],
                              val_net25=m["val"]["net25"], holdout_net25=m["holdout"]["net25"],
                              holdout_ic_t=m["holdout"]["ic_t"],
                              holdout_net25_slip10=m.get("holdout_net25_slip10")))
    _both("walk_forward.csv", lambda p: P._write_csv(p, wf_rows))
    _both("holdout.csv", lambda p: P._write_csv(p, hold_rows))
    _both("cost_curve.csv", lambda p: P._write_csv(p, [cost_curve_row(r["exp"], r["metrics"])
                                                       for r in results if not r["metrics"].get("insufficient")]))
    _both("turnover_attribution.csv", lambda p: P._write_csv(p, [turnover_attr_row(r["exp"], r["metrics"])
                                                                 for r in results if not r["metrics"].get("insufficient")]))
    _both("regime_results.csv", lambda p: P._write_csv(p, result["regime_rows"]))
    _both("cohort_results.csv", lambda p: P._write_csv(p, result["cohort_rows"]))

    corr_rows = []
    for i, ki in enumerate(result["corr_keys"]):
        row = dict(candidate=ki)
        for j, kj in enumerate(result["corr_keys"]):
            row[kj] = result["corr_mat"][i][j]
        corr_rows.append(row)
    _both("correlation_matrix.csv", lambda p: P23._write_csv_union(p, corr_rows))

    cluster_rows = [dict(cluster=c["cluster"], kind="existing_retained", model=c["model"],
                         members=c["model"], source=c["source"]) for c in EXISTING_CLUSTERS]
    for gi, grp in enumerate(result["clusters"]):
        cluster_rows.append(dict(cluster=f"fast_ohlc_group_{gi}", kind="ohlc_candidate_group",
                                 model=grp[0], members=";".join(sorted(grp)), source="phase25 campaign"))
    _both("alpha_clusters.csv", lambda p: P._write_csv(p, cluster_rows))

    _both("rejection_graveyard.csv", lambda p: P._write_csv(p, [dict(
        candidate=r["exp"]["key"], hypothesis=r["exp"]["hyp"], timing=r["exp"]["timing"],
        status_bucket=reject_bucket(r["reasons"]), reasons=";".join(r["reasons"]))
        for r in results if r["status"] == "REJECTED"]))
    _both("survivor_registry.csv", lambda p: P._write_csv(p, [dict(
        candidate=r["exp"]["key"], hypothesis=r["exp"]["hyp"], timing=r["exp"]["timing"],
        net25=r["metrics"]["full"]["net25"], breakeven_bps=r["metrics"]["breakeven_bps"],
        holdout_net25=r["metrics"]["holdout"]["net25"], status="FAST_ALPHA_PAPER_CHALLENGER_ELIGIBLE")
        for r in result["survivors"]] or [dict(candidate="NONE", hypothesis="", timing="", net25=None,
                                               breakeven_bps=None, holdout_net25=None,
                                               status="NO_QUALIFYING_FAST_OHLC_ALPHA")]))
    _both("frozen_fast_specs.json", lambda p: P._write_json(p, result["frozen"]))
    _both("terminal_decision.json", lambda p: P._write_json(p, dict(
        phase="25", decided_at=P._iso_now(), **result["terminal"],
        gate=FAST_GATE, n_experiments=result["n_experiments"],
        existing_clusters_retained=[c["cluster"] for c in EXISTING_CLUSTERS])))
    _both("neighbor_stability.csv", lambda p: P._write_csv(p, result["neighbor_rows"]))
    _both("stage_timing.csv", lambda p: P._write_csv(p, result["stage_timing"]))

    repro = dict(
        engine_version=ENGINE_VERSION, built_at=P._iso_now(),
        panel_npz=OP.NPZ_PATH, panel_manifest=result["manifest"],
        code=["research/phase25_ohlc_panel.py", "research/phase25_fast_ohlc_engine.py",
              "research/run_phase25_fast_ohlc_campaign.py"],
        reproduce="python -m research.run_phase25_fast_ohlc_campaign",
        battery="phase24_fast_engine.evaluate_metrics reused verbatim + slippage stress",
        cost_model="net = gross - 2*cost*turnover (25/50/75bps); slip stress +10bps/side for T2/T3; never weakened",
        elapsed_seconds=result["elapsed_seconds"])
    _both("reproducibility_manifest.json", lambda p: P._write_json(p, repro))
    _both("secret_safety_audit.csv", lambda p: P._write_csv(p, P._secret_safety_audit()))

    return sorted(os.listdir(STORE_DIR))


def main():
    ap = argparse.ArgumentParser(description="Phase 25 fast OHLC alpha campaign")
    ap.add_argument("--npz", default=None)
    args = ap.parse_args()
    result = build(npz_path=args.npz)
    if "results" in result:
        files = write_artifacts(result)
        print(f"artifacts: {len(files)} files in {STORE_DIR} (mirrored to {OUTPUT_DIR})")
    print(json.dumps(result["terminal"], indent=2, default=str))
    print(json.dumps(result.get("stage_timing", []), indent=2))
    return result


if __name__ == "__main__":
    main()
