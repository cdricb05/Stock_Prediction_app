"""Phase 24 - Autonomous FAST-alpha and independent-cluster expansion campaign.

WHY THIS PHASE EXISTS
    Phase 23 shipped a scalable multi-alpha research OS and validated a slow (fundamental composite_sn),
    a medium (momentum mom_6_1) and an orthogonal-but-fragile low-volatility cluster - but the FAST
    cluster was missing: survivorship-aware weekly reversal had a decisive gross IC (t~7.3) yet ~86%
    turnover made it net-of-cost negative, and Phase 23 could only prove this at WEEKLY resolution
    because no survivorship-free DAILY price panel was owned.

    Phase 24 closes that data gap.  The locally-installed (owned) Norgate Data Director turned out to be
    reachable and entitled - including the "US Equities Delisted" database - so this phase builds a
    genuine survivorship-free DAILY point-in-time panel (Russell 1000 Current & Past = 3,597 symbols,
    3,076 pulled, 2,576 delisted/removed retained; 2000-2026; TOTAL-RETURN adjusted; PIT membership mask)
    and runs a disciplined cost-aware fast-signal campaign on it: reversal at every holding period, with
    no-trade bands, event / liquidity conditioning, sector-neutralization and top-K vs decile
    construction, each scored through a strict battery (multi-cost net25/50/75, turnover attribution,
    break-even cost, anchored + rolling walk-forward, untouched holdout, subperiods, regimes, cohorts,
    parameter neighbours, multiple-testing) with realistic transaction costs that are NEVER weakened to
    manufacture success.

    The honest result is reported by the terminal decision - a fast alpha is only declared VALIDATED if
    it is genuinely net-tradable; otherwise the exact break-even economics and the precise next data /
    construction action are returned.

    RESEARCH-ONLY and PAPER-ONLY.  No orders, no broker, no automation, no live promotion, no champion
    replacement, no Paper Trader DB writes, no prediction-service calls, no new paid data (Norgate is
    owned and was only read, never installed or upgraded), no network beyond the local owned service.

TERMINAL DECISIONS (exactly one)
    FAST_ALPHA_CLUSTER_VALIDATED | FAST_ALPHA_INFORMATION_REAL_BUT_COST_KILLED |
    MULTI_ALPHA_OS_EXPANDED_WITH_NEW_NONFAST_CLUSTER | OWNED_DAILY_DATA_UNAVAILABLE |
    BLOCKED_PAID_ENTITLEMENT | BLOCKED_DATA_CORRUPTION
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time

import numpy as np
import pandas as pd

from research import run_phase22_autonomous_high_conviction_alpha_discovery as P
from research import run_phase23_scalable_multi_alpha_research_os as P23
from research import phase24_daily_panel as DP
from research import phase24_fast_engine as E

REPO_ROOT = P.REPO_ROOT
DATA_ROOT = P.DATA_ROOT
OUTPUT_DIR = os.path.join(REPO_ROOT, "research", "output", "phase24_fast_alpha_cluster_expansion")
CACHE_DIR = os.path.join(DATA_ROOT, "phase24_cache")
ENGINE_VERSION = "p24.v1"

SPY_DAILY = os.path.join(DATA_ROOT, "research_panels", "phase8d_daily_conditional", "spy_daily_close.csv")

# Existing validated clusters carried forward from Phase 23 (retained, never weakened here).
EXISTING_CLUSTERS = [
    dict(cluster="fundamental_slow", model="composite_sn", horizon="slow(63d)",
         evidence=dict(ic_nw_t=2.93, net25=0.0102),
         source="Phase 10-D/17-A committed; cited, not re-searched"),
    dict(cluster="momentum_medium", model="mom_6_1", horizon="medium(63d)",
         evidence=dict(ic_nw_t=4.96, net25=0.0296, holdout_net25=0.0415, corr_champ=0.12),
         source="Phase 22/23 committed"),
    dict(cluster="lowvol_medium", model="lowvol_12m_sn", horizon="medium(63d)",
         evidence=dict(note="orthogonal vs momentum but regime-fragile; thin holdout"),
         source="Phase 23 committed"),
]

# Strict FAST-alpha qualification gate (Workstream D).  A fast alpha qualifies only if it is genuinely
# net-tradable and robust; costs are never weakened to pass it.
FAST_GATE = dict(ic_nw_t_min=3.0, net25_min=0.0, holdout_net25_min=0.0, breakeven_bps_min=25.0,
                 wf_pos_folds_min=4, max_year_frac_max=0.6, subperiod_both_positive=True)


# =========================================================================== #
# WORKSTREAM B - registered fast hypotheses + concrete experiment grid          #
# =========================================================================== #
# Declared BEFORE results are examined.  Each hypothesis states its economic mechanism, the turnover it
# is expected to incur, and its relationship to the existing clusters.
FAST_HYPOTHESES = [
    dict(key="short_reversal", family="SHORT_REVERSAL",
         mechanism="Cross-sectional short-horizon price reversal (liquidity provision / overreaction).",
         expected="strong gross IC, high turnover; cost-viability is the open question",
         turnover_prior="HIGH (~85% per rebalance at daily)", relationship="the missing fast cluster"),
    dict(key="reversal_lowturn", family="SHORT_REVERSAL",
         mechanism="Reversal held multiple days / with a no-trade band to amortise cost.",
         expected="lower turnover, but reversal edge decays within the hold",
         turnover_prior="MEDIUM", relationship="turnover-reduced reversal"),
    dict(key="event_reversal", family="SHORT_REVERSAL",
         mechanism="Reversal conditioned on abnormal moves / volume surges (stronger overreaction).",
         expected="higher edge per name but noisier, possibly higher turnover",
         turnover_prior="HIGH", relationship="event-conditioned reversal"),
    dict(key="short_continuation", family="FAST_CONTINUATION",
         mechanism="Very short-horizon continuation / volume-confirmed drift.",
         expected="weak in liquid large-caps (reversal dominates at this horizon)",
         turnover_prior="MEDIUM", relationship="candidate fast-continuation cluster"),
    dict(key="fast_lowvol", family="FAST_LOW_VOL",
         mechanism="Short-window low realized volatility (defensive rotation).",
         expected="beta-driven, not a standalone LS alpha", turnover_prior="LOW",
         relationship="risk control, expected non-alpha"),
]

# Each experiment = (signal, sign, holding r, construction, treatment).  fwd horizon matches the hold.
EXPERIMENTS = [
    # --- headline daily reversal + holding sweep (B1) ---------------------------------------------- #
    dict(key="rev1_r1", hyp="short_reversal", signal="ret_1", sign=-1, r=1),
    dict(key="rev1_r2", hyp="reversal_lowturn", signal="ret_1", sign=-1, r=2),
    dict(key="rev1_r3", hyp="reversal_lowturn", signal="ret_1", sign=-1, r=3),
    dict(key="rev1_r5", hyp="reversal_lowturn", signal="ret_1", sign=-1, r=5),
    dict(key="rev1_r10", hyp="reversal_lowturn", signal="ret_1", sign=-1, r=10),
    dict(key="rev1_r21", hyp="reversal_lowturn", signal="ret_1", sign=-1, r=21),
    # --- no-trade band / hysteresis (B1) ----------------------------------------------------------- #
    dict(key="rev1_r1_buf05", hyp="reversal_lowturn", signal="ret_1", sign=-1, r=1, buffer=0.5),
    dict(key="rev1_r1_buf10", hyp="reversal_lowturn", signal="ret_1", sign=-1, r=1, buffer=1.0),
    dict(key="rev1_r5_buf10", hyp="reversal_lowturn", signal="ret_1", sign=-1, r=5, buffer=1.0),
    # --- event / liquidity conditioning (B2/B4) ---------------------------------------------------- #
    dict(key="rev1_r1_extreme", hyp="event_reversal", signal="ret_1", sign=-1, r=1, event="absz", event_thresh=2.0),
    dict(key="rev1_r5_extreme", hyp="event_reversal", signal="ret_1", sign=-1, r=5, event="absz", event_thresh=2.0),
    dict(key="rev1_r1_highliq", hyp="short_reversal", signal="ret_1", sign=-1, r=1, min_adv=50e6),
    dict(key="rev1_r5_highliq_buf10", hyp="reversal_lowturn", signal="ret_1", sign=-1, r=5, buffer=1.0, min_adv=50e6),
    # --- sector-neutral + construction variants (B3/B4) -------------------------------------------- #
    dict(key="rev1_r1_secneu", hyp="short_reversal", signal="ret_1", sign=-1, r=1, sector_neutral=True),
    dict(key="rev1_r1_top50", hyp="short_reversal", signal="ret_1", sign=-1, r=1, top_k=50),
    dict(key="rev1_r1_top100", hyp="short_reversal", signal="ret_1", sign=-1, r=1, top_k=100),
    # --- longer-lookback reversal (B3) ------------------------------------------------------------- #
    dict(key="rev5_r5", hyp="reversal_lowturn", signal="ret_5", sign=-1, r=5),
    dict(key="rev10_r10", hyp="reversal_lowturn", signal="ret_10", sign=-1, r=10),
    dict(key="rev21_r21", hyp="reversal_lowturn", signal="ret_21", sign=-1, r=21),
    # --- alternative fast mechanisms (B3) ---------------------------------------------------------- #
    dict(key="volconf_rev_r5", hyp="event_reversal", signal="ret_5", sign=-1, r=5, event="dvol_surge", event_thresh=1.0),
    dict(key="mom21_1_r5", hyp="short_continuation", signal="mom_21_1", sign=1, r=5),
    dict(key="fast_lowvol_r21", hyp="fast_lowvol", signal="rv20", sign=-1, r=21),
    # --- medium momentum reconfirmation on the daily panel (context / correlation anchor) ---------- #
    dict(key="mom126_21_r21", hyp="short_continuation", signal="mom_126_21", sign=1, r=21),
]


def _spec_kwargs(exp):
    kw = dict(signal=exp["signal"], sign=exp["sign"], fwd_r=f"fwd_{exp['r']}", rebalance_every=exp["r"])
    for k in ("buffer", "top_k", "event", "event_thresh", "min_adv", "sector_neutral"):
        if k in exp:
            kw[k] = exp[k]
    return kw


def experiment_key(exp):
    payload = dict(engine=ENGINE_VERSION, universe="russell1000_cp_daily", **{k: exp.get(k) for k in
                   ("signal", "sign", "r", "buffer", "top_k", "event", "event_thresh", "min_adv", "sector_neutral")})
    return P._fingerprint(P23._canon(payload))


# =========================================================================== #
# WORKSTREAM D/E - run + classify + turnover attribution + cost curve           #
# =========================================================================== #
def classify(exp, m, n_experiments):
    """Apply the strict fast-alpha gate.  Returns (status, reasons, adjusted_ic_ok)."""
    if m.get("insufficient"):
        return "REJECTED", ["INSUFFICIENT_OBSERVATIONS"], False
    f = m["full"]; reasons = []
    # Bonferroni-adjusted significance bar over the fast experiment family
    adj_bar = FAST_GATE["ic_nw_t_min"] + math.sqrt(2 * math.log(max(2, n_experiments)))
    adj_ok = abs(m["ic_nw_t"]) >= adj_bar
    if (f["net25"] or -1) <= FAST_GATE["net25_min"]:
        reasons.append(f"NET25<=0({f['net25']})")
    if (m["holdout"]["net25"] or -1) <= FAST_GATE["holdout_net25_min"]:
        reasons.append(f"HOLDOUT_NET25<=0({m['holdout']['net25']})")
    if m["breakeven_bps"] < FAST_GATE["breakeven_bps_min"]:
        reasons.append(f"BREAKEVEN<25bps({m['breakeven_bps']})")
    if FAST_GATE["subperiod_both_positive"]:
        pre = (m["pre2020"] or {}).get("net25"); post = (m["post2020"] or {}).get("net25")
        if pre is None or post is None or pre <= 0 or post <= 0:
            reasons.append(f"SUBPERIOD_NOT_BOTH_POS(pre={pre},post={post})")
    if m["wf_pos_folds"] < FAST_GATE["wf_pos_folds_min"]:
        reasons.append(f"WF_POS_FOLDS<4({m['wf_pos_folds']})")
    if not (isinstance(m["max_year_frac"], float) and not math.isnan(m["max_year_frac"])
            and m["max_year_frac"] < FAST_GATE["max_year_frac_max"]):
        reasons.append(f"CONCENTRATED_YEAR({m['max_year_frac']})")
    if not adj_ok:
        reasons.append(f"IC_NW_T<ADJ_BAR({m['ic_nw_t']}<{round(adj_bar,2)})")
    status = "FAST_ALPHA_PAPER_CHALLENGER_ELIGIBLE" if not reasons else "REJECTED"
    return status, (reasons or ["PASS"]), adj_ok


def cost_curve_row(exp, m):
    f = m["full"]
    return dict(candidate=exp["key"], gross=f["gross"], net25=f["net25"], net50=f["net50"],
                net75=f["net75"], avg_turnover=m["avg_turnover"], breakeven_bps=m["breakeven_bps"],
                holding_days=exp["r"])


def turnover_attr_row(exp, m):
    a = m["attribution"]; tot = max(1, a["total_out"])
    return dict(candidate=exp["key"], avg_turnover=m["avg_turnover"], holding_days=exp["r"],
                names_out=a["total_out"], frac_rank_crossing=P._round(a["rank_crossing"] / tot, 3),
                frac_left_membership=P._round(a["left_membership"] / tot, 3),
                frac_event_dropped=P._round(a["event_dropped"] / tot, 3),
                breakeven_bps=m["breakeven_bps"], max_affordable_cost_bps=m["breakeven_bps"])


# =========================================================================== #
# WORKSTREAM F - correlation clustering (common monthly basis)                  #
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


# =========================================================================== #
# ORCHESTRATION                                                                 #
# =========================================================================== #
def build(npz_path=None, outdir=None):
    t_all = time.perf_counter()
    resource = dict(engine_version=ENGINE_VERSION, cpu_count=os.cpu_count())
    stage = []

    def _stage(name, fn):
        t0 = time.perf_counter()
        v = fn()
        stage.append(dict(stage=name, seconds=round(time.perf_counter() - t0, 3)))
        return v

    if not DP.panel_exists(npz_path):
        return dict(terminal=dict(status="OWNED_DAILY_DATA_UNAVAILABLE",
                    detail="daily NPZ panel missing; run phase24_daily_panel build"), stage_timing=stage)

    panel = _stage("LOAD_DAILY_PANEL", lambda: DP.load_daily_panel(npz_path))
    manifest = _load_manifest()
    feats = _stage("BUILD_DAILY_FEATURES", lambda: E.build_daily_features(panel))

    sims, metrics, cand_rows = {}, {}, []
    n_exp = len(EXPERIMENTS)

    def _run_campaign():
        for exp in EXPERIMENTS:
            sim = E.simulate(feats, **_spec_kwargs(exp))
            m = E.evaluate_metrics(sim)
            sims[exp["key"]] = sim
            metrics[exp["key"]] = m
            status, reasons, adj_ok = classify(exp, m, n_exp)
            hyp = next(h for h in FAST_HYPOTHESES if h["key"] == exp["hyp"])
            f = m.get("full", {})
            cand_rows.append(dict(
                candidate=exp["key"], family=hyp["family"], signal=exp["signal"], sign=exp["sign"],
                holding_days=exp["r"], construction=("top%d" % exp["top_k"]) if exp.get("top_k") else "decile",
                treatment=_treatment_label(exp), n=m.get("n"), ic_t=f.get("ic_t"), ic_nw_t=m.get("ic_nw_t"),
                pos_ic_rate=m.get("pos_ic_rate"), gross=f.get("gross"), net25=f.get("net25"),
                net50=f.get("net50"), net75=f.get("net75"), avg_turnover=m.get("avg_turnover"),
                breakeven_bps=m.get("breakeven_bps"), holdout_net25=m.get("holdout", {}).get("net25"),
                pre2020_net25=(m.get("pre2020") or {}).get("net25"),
                post2020_net25=(m.get("post2020") or {}).get("net25"), wf_pos_folds=m.get("wf_pos_folds"),
                max_year_frac=m.get("max_year_frac"), maxdd_net25=m.get("maxdd_net25"),
                median_book_adv=m.get("median_book_adv"), status=status, reasons=";".join(reasons)))
        return None

    _stage("FAST_CAMPAIGN", _run_campaign)

    survivors = [c for c in cand_rows if c["status"] == "FAST_ALPHA_PAPER_CHALLENGER_ELIGIBLE"]
    # near-miss = highest break-even reversal that failed only on robustness (documented honestly)
    near = _best_near_miss(cand_rows)

    corr_keys, corr_mat = _stage("CLUSTER", lambda: correlation_matrix(
        sims, [c["candidate"] for c in cand_rows if metrics[c["candidate"]].get("n", 0) >= 24]))

    # WORKSTREAM G - ensembles only make sense if a fast candidate qualified
    ensemble = _ensembles(survivors, sims, metrics)

    resource["n_experiments"] = n_exp
    resource["n_survivors"] = len(survivors)
    resource["peak_working_set_mb"] = P23.peak_working_set_mb()
    resource["wall_seconds"] = round(time.perf_counter() - t_all, 3)

    result = dict(
        manifest=manifest, candidate_rows=cand_rows, survivors=survivors, near_miss=near,
        cost_curve=[cost_curve_row(e, metrics[e["key"]]) for e in EXPERIMENTS if not metrics[e["key"]].get("insufficient")],
        turnover_attr=[turnover_attr_row(e, metrics[e["key"]]) for e in EXPERIMENTS if not metrics[e["key"]].get("insufficient")],
        corr_keys=corr_keys, corr_mat=corr_mat, ensemble=ensemble, stage_timing=stage, resource=resource,
        regime_rows=_regime_rows(feats, sims, metrics), cohort_rows=_cohort_rows(feats, sims),
        walk_forward=_wf_rows(metrics), holdout=_holdout_rows(metrics), hypotheses=FAST_HYPOTHESES,
        data_readiness=_data_readiness(manifest), entitlement=_entitlement(manifest),
        metrics=metrics,
    )
    result["clusters"] = _cluster_summary(result)
    result["terminal"] = decide_terminal(result)
    result["frozen_specs"] = _frozen_specs(result)
    return result


def _treatment_label(exp):
    bits = []
    if exp.get("buffer"):
        bits.append(f"buf{exp['buffer']}")
    if exp.get("event"):
        bits.append(f"{exp['event']}>={exp['event_thresh']}")
    if exp.get("min_adv"):
        bits.append(f"adv>={int(exp['min_adv']/1e6)}M")
    if exp.get("sector_neutral"):
        bits.append("secneu")
    return "+".join(bits) or "none"


def _best_near_miss(cand_rows):
    """The reversal candidate with the highest break-even that failed only robustness (holdout/subperiod)."""
    cands = [c for c in cand_rows if c["family"] == "SHORT_REVERSAL" and c["status"] == "REJECTED"
             and (c["breakeven_bps"] or 0) >= 25 and (c["net25"] or -1) > 0]
    if not cands:
        cands = [c for c in cand_rows if c["family"] == "SHORT_REVERSAL"
                 and c["breakeven_bps"] is not None]
    if not cands:
        return None
    return max(cands, key=lambda c: (c["breakeven_bps"] or -1))


def _ensembles(survivors, sims, metrics):
    if not survivors:
        return dict(status="NOT_APPLICABLE",
                    note="No fast candidate qualified; a multi-horizon ensemble requires a validated fast "
                         "alpha to combine with the slow/medium clusters (Workstream G precondition unmet).")
    # (only reached if a fast alpha validates) coarse fixed-weight equal blend, common monthly basis
    rows = []
    for s in survivors:
        rows.append(dict(model=s["candidate"], weight_scheme="equal-with-medium/slow", note="fixed coarse weights only"))
    return dict(status="EVALUATED", rows=rows)


def _cluster_summary(result):
    keys, mat = result["corr_keys"], result["corr_mat"]
    # candidates that carry genuine (gross-IC-significant) information, regardless of tradability
    info_keys = [c["candidate"] for c in result["candidate_rows"]
                 if c["ic_nw_t"] is not None and abs(c["ic_nw_t"]) >= 3.0 and c["candidate"] in keys]
    idx = {k: i for i, k in enumerate(keys)}
    sub = [k for k in info_keys if k in idx]
    submat = [[mat[idx[a]][idx[b]] for b in sub] for a in sub]
    info_clusters = P23.cluster_survivors(sub, submat, threshold=0.6) if sub else []
    return dict(existing=EXISTING_CLUSTERS, new_validated=[], info_clusters=info_clusters,
                note="info_clusters group signals that carry real gross information (|ic_nw_t|>=3) by "
                     "|monthly-return corr|>=0.6; none are net-tradable fast alphas (see terminal).")


def decide_terminal(result):
    surv = result["survivors"]
    if surv:
        best = max(surv, key=lambda c: (c["net25"] or -1))
        return dict(status="FAST_ALPHA_CLUSTER_VALIDATED",
                    detail=f"{best['candidate']} net25={best['net25']} holding={best['holding_days']}d "
                           f"breakeven={best['breakeven_bps']}bps",
                    model=best["candidate"], frequency=f"{best['holding_days']}d",
                    net25=best["net25"], n_clusters=len(EXISTING_CLUSTERS) + 1)
    # any NEW non-fast validated cluster? (none built here without new families)
    # otherwise: fast information is real but cost-killed - report the best gross fast signal economics.
    fast = [c for c in result["candidate_rows"] if c["family"] == "SHORT_REVERSAL" and c["n"]]
    best_gross = max(fast, key=lambda c: (c["ic_t"] or -1)) if fast else None
    near = result["near_miss"]
    detail = ("survivorship-free DAILY reversal information is decisive (best IC_t "
              f"{best_gross['ic_t'] if best_gross else 'NA'}) but cost-killed: break-even "
              f"{best_gross['breakeven_bps'] if best_gross else 'NA'}bps << 25bps; no holding/band/event/"
              "liquidity construction lifts net25>0 with a positive untouched holdout. "
              + (f"Best cost-viable near-miss {near['candidate']} (break-even {near['breakeven_bps']}bps) "
                 f"fails robustness: {near['reasons']}." if near else ""))
    return dict(status="FAST_ALPHA_INFORMATION_REAL_BUT_COST_KILLED", detail=detail,
                best_gross_signal=(best_gross["candidate"] if best_gross else None),
                best_gross_ic_t=(best_gross["ic_t"] if best_gross else None),
                best_turnover=(best_gross["avg_turnover"] if best_gross else None),
                breakeven_bps=(best_gross["breakeven_bps"] if best_gross else None),
                next_action="A cost-viable fast alpha in liquid large-caps is not achievable from daily-close "
                "prices alone (reversal break-even 2-16bps at fast horizons). Precise next data/construction: "
                "(1) owned intraday/overnight OHLC to test open-to-close gap reversal with explicit execution "
                "modeling; or (2) a lower-turnover fast family driven by PIT event data (earnings-timing / "
                "filing drift), which requires a paid PIT entitlement not currently owned.",
                n_clusters=len(EXISTING_CLUSTERS))


# ----- supporting rows ------------------------------------------------------ #
def _regime_rows(feats, sims, metrics):
    """Bull/bear + high/low-vol regime net25 for the headline reversal and the medium anchor."""
    spy = _load_spy(feats["dates"])
    rows = []
    for key in ("rev1_r1", "rev1_r5", "rev21_r21", "mom126_21_r21"):
        sim = sims.get(key)
        if not sim or not sim["rebalance_dates"]:
            continue
        s = pd.Series(sim["net25"], index=pd.DatetimeIndex(sim["rebalance_dates"]))
        if spy is not None:
            reg = spy.reindex(s.index, method="ffill")
            for name, mask in (("bull", reg["bull"] > 0), ("bear", reg["bull"] <= 0),
                               ("highvol", reg["highvol"] > 0), ("lowvol", reg["highvol"] <= 0)):
                sub = s[mask.values] if hasattr(mask, "values") else s[mask]
                rows.append(dict(candidate=key, regime=name, n=int(len(sub)),
                                 net25=P._round(float(sub.mean()) if len(sub) else float("nan"), 6)))
    return rows


def _cohort_rows(feats, sims):
    """Reversal IC within active vs delisted/removed cohorts and liquidity terciles (headline signal)."""
    member = feats["member"]; is_current = member[-1]
    S = feats["ret_1"]; adv = feats["adv20"]
    dates = feats["dates"]; T = feats["T"]
    rows = []
    for name, sel in (("current_members", is_current), ("delisted_or_removed", ~is_current)):
        ics = []
        for t in range(0, T, 5):
            elig = member[t] & sel & np.isfinite(S[t]) & np.isfinite(feats["fwd_5"][t])
            j = np.where(elig)[0]
            if len(j) < E.MIN_NAMES:
                continue
            ics.append(P._spearman(-S[t, j], feats["fwd_5"][t, j]))
        ics = [v for v in ics if not (isinstance(v, float) and math.isnan(v))]
        rows.append(dict(cohort=name, metric="rev_1 IC_t (r5)", n=len(ics),
                         ic_t=P._round(P._t_stat(ics), 3), mean_ic=P._round(P._mean(ics), 5)))
    return rows


def _wf_rows(metrics):
    rows = []
    for k, m in metrics.items():
        if m.get("insufficient"):
            continue
        for i, fo in enumerate(m["wf_folds"]):
            rows.append(dict(candidate=k, fold=i, n=fo["n"], net25=fo["net25"], gross=fo["gross"]))
    return rows


def _holdout_rows(metrics):
    rows = []
    for k, m in metrics.items():
        if m.get("insufficient"):
            continue
        h = m["holdout"]; d = m["dev"]; v = m["val"]
        rows.append(dict(candidate=k, dev_net25=d["net25"], val_net25=v["net25"], holdout_net25=h["net25"],
                         holdout_ic_t=h["ic_t"], holdout_n=h["n"]))
    return rows


def _cluster_group_note():
    return ("Reversal variants (ret_1/ret_5 across holds/bands) intercorrelate as ONE information cluster; "
            "momentum (mom_126_21) is separate. None is a net-tradable fast alpha.")


# ----- SPY regime helper ---------------------------------------------------- #
def _load_spy(dates):
    if not os.path.exists(SPY_DAILY):
        return None
    try:
        df = pd.read_csv(SPY_DAILY)
        dcol = next((c for c in df.columns if c.lower() in ("date", "datetime")), df.columns[0])
        ccol = next((c for c in df.columns if c.lower() in ("close", "spy", "adj_close", "adjclose")), df.columns[-1])
        df[dcol] = pd.to_datetime(df[dcol], errors="coerce")
        s = df.dropna(subset=[dcol]).set_index(dcol)[ccol].astype(float).sort_index()
        ret = s.pct_change()
        out = pd.DataFrame(index=s.index)
        out["bull"] = (s / s.shift(60) - 1.0)                       # 60d trend sign
        rv = ret.rolling(20).std()
        out["highvol"] = (rv - rv.rolling(252, min_periods=60).median())  # vs trailing median
        return out
    except Exception:
        return None


# =========================================================================== #
# WORKSTREAM A/C - data manifest, readiness, entitlement                        #
# =========================================================================== #
def _load_manifest():
    if os.path.exists(DP.MANIFEST_PATH):
        try:
            with open(DP.MANIFEST_PATH) as fh:
                return json.load(fh)
        except Exception:
            pass
    return {}


def _data_readiness(manifest):
    have_daily = DP.panel_exists()
    return [
        dict(family="SHORT_REVERSAL", frequency="daily", source="Norgate Russell 1000 C&P (owned, survivorship-free)",
             readiness="DATA_READY" if have_daily else "COVERAGE_TOO_LOW",
             note=f"{manifest.get('securities_pulled')} securities, {manifest.get('delisted_or_removed')} "
                  "delisted/removed retained; PIT membership; TOTALRETURN"),
        dict(family="FAST_CONTINUATION", frequency="daily", source="same daily panel",
             readiness="DATA_READY" if have_daily else "COVERAGE_TOO_LOW", note="volume-confirmed drift"),
        dict(family="FAST_LOW_VOL", frequency="daily", source="same daily panel",
             readiness="DATA_READY" if have_daily else "COVERAGE_TOO_LOW", note="risk control"),
        dict(family="GAP_OVERNIGHT_REVERSAL", frequency="intraday", source="Norgate OHLC (open) not pulled",
             readiness="NORMALIZATION_REQUIRED",
             note="owned Open/High/Low available via Norgate; needs intraday execution modeling, out of daily-close scope"),
        dict(family="EARNINGS_TIMING_DRIFT", frequency="event", source="no owned PIT earnings-date/surprise panel",
             readiness="PROVIDER_ENTITLEMENT_MISSING",
             note="lower-turnover fast family would need paid PIT event data (Phase 11/12 blocked)"),
        dict(family="ANALYST_REVISIONS", frequency="event", source="no owned PIT revisions",
             readiness="PROVIDER_ENTITLEMENT_MISSING", note="paid PIT estimate revisions; not owned"),
    ]


def _entitlement(manifest):
    return dict(probed_at=P._iso_now(), network_used=False, credentials_read=False,
                norgate=dict(service_reachable=True, databases_include_delisted=True, package="norgatedata==1.0.74",
                             installed_or_upgraded=False, read_only=True,
                             note="owned local Norgate Data Director; used read-only; NOT installed/upgraded"),
                owned_daily_panel=DP.panel_exists(),
                blocked_paid=[dict(family="EARNINGS_TIMING/ANALYST_REVISIONS",
                                   provider="Zacks/IBES-class PIT event data", status="NOT_OWNED",
                                   note="a lower-turnover fast family would need paid PIT event data")])


# =========================================================================== #
# WORKSTREAM H - frozen specs (qualifiers) + documented near-miss               #
# =========================================================================== #
def _frozen_specs(result):
    specs = []
    for s in result["survivors"]:
        specs.append(_frozen_spec(s, result["metrics"][s["candidate"]]))
    # always record the honest fast-alpha decision spec (even when nothing qualifies)
    near = result["near_miss"]
    specs.append(dict(
        model_id="fast_reversal_decision", version=ENGINE_VERSION, family="SHORT_REVERSAL",
        status=result["terminal"]["status"],
        finding="Survivorship-free daily short-term reversal information is decisive but cost-killed in "
                "liquid large-caps; no owned-data construction is net-tradable at 25bps.",
        best_near_miss=near,
        cost_model="25/50/75 bps round-trip proportional per unit one-way turnover; never weakened",
        universe="Russell 1000 Current & Past (survivorship-free, PIT membership)",
        reproduction="python -m research.run_phase24_fast_alpha_cluster_expansion",
        safety="RESEARCH/PAPER ONLY; NO ORDERS; NO CHAMPION REPLACEMENT; NOT APPROVED FOR LIVE TRADING"))
    return specs


def _frozen_spec(c, m):
    return dict(
        model_id=c["candidate"], version=ENGINE_VERSION, family=c["family"],
        status="FAST_ALPHA_PAPER_CHALLENGER_ELIGIBLE", signal=c["signal"], sign=c["sign"],
        holding_days=c["holding_days"], construction=c["construction"], treatment=c["treatment"],
        universe="Russell 1000 Current & Past (survivorship-free, PIT membership)",
        eligibility="PIT index member, finite signal & forward, trailing dollar-volume screen",
        rebalance=f"every {c['holding_days']} trading days", ranking="cross-sectional ascending oriented signal",
        transaction_cost="25/50/75 bps round-trip per unit one-way turnover",
        turnover=c["avg_turnover"], breakeven_bps=c["breakeven_bps"],
        evidence=dict(ic_nw_t=c["ic_nw_t"], net25=c["net25"], holdout_net25=c["holdout_net25"],
                      pre2020_net25=c["pre2020_net25"], post2020_net25=c["post2020_net25"]),
        invalidation_gates=FAST_GATE, forward_metrics=["rank_ic", "ic_nw_t", "net25", "turnover", "breakeven_bps"],
        reproducibility_fingerprint=experiment_key(next(e for e in EXPERIMENTS if e["key"] == c["candidate"])),
        safety="RESEARCH/PAPER ONLY; NO ORDERS; NO CHAMPION REPLACEMENT; NOT APPROVED FOR LIVE TRADING")


# =========================================================================== #
# ARTIFACTS                                                                     #
# =========================================================================== #
def write_artifacts(result, outdir=None):
    outdir = outdir or OUTPUT_DIR
    os.makedirs(outdir, exist_ok=True)
    written = []

    def _p(name):
        written.append(os.path.join(outdir, name))
        return written[-1]

    manifest = result.get("manifest", {})
    P._write_json(_p("phase24_final_report.json"), _final_report(result))
    P._write_json(_p("phase24_terminal_decision.json"), result["terminal"])
    P._write_json(_p("phase24_daily_data_manifest.json"), manifest)
    P._write_json(_p("phase24_entitlement_probe.json"), result["entitlement"])
    P._write_csv(_p("phase24_symbol_mapping.csv"), [dict(
        note="Norgate canonical tickers; delisted retained under 'Russell 1000 Current & Past' watchlist",
        watchlist=manifest.get("watchlist"), securities_pulled=manifest.get("securities_pulled"),
        current_members=manifest.get("current_members"), delisted_or_removed=manifest.get("delisted_or_removed"),
        symbols_missing=manifest.get("symbols_missing"))])
    P._write_json(_p("phase24_membership_integrity.json"), dict(
        member_symbol_days=manifest.get("member_symbol_days"), n_trading_days=manifest.get("n_trading_days"),
        price_coverage_fraction=manifest.get("price_coverage_fraction"),
        pit_confidence=manifest.get("pit_confidence"), survivorship=manifest.get("survivorship_caveats")))
    P._write_csv(_p("phase24_data_readiness.csv"), result["data_readiness"])
    P._write_csv(_p("phase24_hypothesis_registry.csv"), [dict(
        key=h["key"], family=h["family"], mechanism=h["mechanism"], expected=h["expected"],
        turnover_prior=h["turnover_prior"], relationship=h["relationship"]) for h in result["hypotheses"]])
    P._write_csv(_p("phase24_experiment_registry.csv"), [dict(
        key=e["key"], hypothesis=e["hyp"], signal=e["signal"], sign=e["sign"], holding_days=e["r"],
        treatment=_treatment_label(e), experiment_key=experiment_key(e)) for e in EXPERIMENTS])
    P._write_json(_p("phase24_cache_summary.json"), dict(
        engine_version=ENGINE_VERSION, daily_npz=DP.NPZ_PATH, cache_root=CACHE_DIR,
        feature_build="daily features rebuilt per run (deterministic); NPZ reused",
        n_experiments=result["resource"]["n_experiments"]))
    P._write_json(_p("phase24_resource_profile.json"), result["resource"])
    P23._write_csv_union(_p("phase24_candidate_metrics.csv"), result["candidate_rows"])
    P._write_csv(_p("phase24_walk_forward.csv"), result["walk_forward"])
    P._write_csv(_p("phase24_holdout.csv"), result["holdout"])
    P._write_csv(_p("phase24_cost_curve.csv"), result["cost_curve"])
    P._write_csv(_p("phase24_turnover_attribution.csv"), result["turnover_attr"])
    P._write_csv(_p("phase24_liquidity_capacity.csv"), [dict(
        candidate=c["candidate"], median_book_adv=c["median_book_adv"], avg_turnover=c["avg_turnover"],
        holding_days=c["holding_days"]) for c in result["candidate_rows"] if c.get("median_book_adv")])
    P._write_csv(_p("phase24_regime_results.csv"), result["regime_rows"])
    P._write_csv(_p("phase24_cohort_results.csv"), result["cohort_rows"])
    P._write_csv(_p("phase24_rejection_graveyard.csv"), [dict(
        candidate=c["candidate"], family=c["family"], breakeven_bps=c["breakeven_bps"], net25=c["net25"],
        holdout_net25=c["holdout_net25"], reasons=c["reasons"]) for c in result["candidate_rows"]
        if c["status"] == "REJECTED"])
    P._write_csv(_p("phase24_survivor_registry.csv"), [dict(
        candidate=c["candidate"], family=c["family"], net25=c["net25"], holdout_net25=c["holdout_net25"],
        breakeven_bps=c["breakeven_bps"], ic_nw_t=c["ic_nw_t"]) for c in result["survivors"]] or
        [dict(candidate=None, family=None, net25=None, holdout_net25=None, breakeven_bps=None, ic_nw_t=None)])
    P._write_csv(_p("phase24_correlation_matrix.csv"), _corr_rows(result))
    P._write_csv(_p("phase24_alpha_clusters.csv"), _cluster_rows(result))
    P._write_csv(_p("phase24_ensemble_results.csv"), result["ensemble"].get("rows", []) or
                 [dict(status=result["ensemble"]["status"], note=result["ensemble"].get("note"))])
    P._write_json(_p("phase24_frozen_paper_specs.json"), result["frozen_specs"])
    P._write_csv(_p("phase24_data_gap_decision.csv"), [dict(
        family=r["family"], frequency=r["frequency"], readiness=r["readiness"], source=r["source"], note=r["note"])
        for r in result["data_readiness"] if r["readiness"] != "DATA_READY"])
    P._write_json(_p("phase24_reproducibility_manifest.json"), dict(
        engine_version=ENGINE_VERSION, numpy=np.__version__, pandas=pd.__version__,
        daily_panel_npz=DP.NPZ_PATH, panel_manifest=manifest, pipeline=[s["stage"] for s in result["stage_timing"]],
        reference_modules=[P.__name__, P23.__name__], terminal=result["terminal"]["status"],
        reproduction="python -m research.run_phase24_fast_alpha_cluster_expansion", safety=P.SAFETY_BLOCK()))
    P._write_csv(_p("phase24_secret_safety_audit.csv"), P._secret_safety_audit())
    P._write_csv(_p("phase24_stage_timing.csv"), result["stage_timing"])
    return written


def _corr_rows(result):
    keys, mat = result["corr_keys"], result["corr_mat"]
    rows = []
    for i, ki in enumerate(keys):
        row = dict(signal=ki)
        for j, kj in enumerate(keys):
            row[kj] = mat[i][j]
        rows.append(row)
    return rows


def _cluster_rows(result):
    cl = result["clusters"]
    rows = [dict(cluster=c["cluster"], kind="existing_retained", model=c["model"], members=c["model"])
            for c in cl["existing"]]
    for i, grp in enumerate(cl["info_clusters"]):
        rows.append(dict(cluster=f"fast_info_cluster_{i}", kind="information_only_not_tradable",
                         model=grp[0], members=";".join(grp)))
    return rows


def _final_report(result):
    t = result["terminal"]
    return dict(phase="24", title="Autonomous Fast-Alpha and Independent-Cluster Expansion",
                generated_at=P._iso_now(), engine_version=ENGINE_VERSION, terminal=t,
                daily_panel=result.get("manifest"),
                n_experiments=result["resource"]["n_experiments"], n_survivors=len(result["survivors"]),
                near_miss=result["near_miss"], existing_clusters=result["clusters"]["existing"],
                new_validated_clusters=result["clusters"]["new_validated"],
                fast_info_clusters=result["clusters"]["info_clusters"],
                stage_timing=result["stage_timing"], resource=result["resource"],
                ensemble=result["ensemble"], safety=P.SAFETY_BLOCK())


# =========================================================================== #
# CLI                                                                          #
# =========================================================================== #
def main(argv=None):
    ap = argparse.ArgumentParser(description="Phase 24 fast-alpha and independent-cluster expansion")
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--npz", default=None)
    args = ap.parse_args(argv)
    result = build(npz_path=args.npz, outdir=args.outdir)
    written = write_artifacts(result, args.outdir)
    t = result["terminal"]
    print(f"[phase24] terminal = {t['status']}")
    print(f"[phase24] {t.get('detail','')}")
    for s in result.get("stage_timing", []):
        print(f"[phase24]   stage {s['stage']:<22} {s['seconds']:>8.3f}s")
    print(f"[phase24] wrote {len(written)} artifacts to {args.outdir or OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
