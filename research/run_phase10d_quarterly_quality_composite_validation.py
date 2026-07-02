"""Phase 10-D - Quarterly EODHD/Norgate Quality Composite Validation.

WHY THIS PHASE EXISTS
    Phase 10-C strictly out-of-sample validated the two owned-data quality leads as SIMPLE standalone
    signals and returned QUALITY_ALPHA_REJECTED_NOT_COST_ROBUST. The decisive findings were:
      * BOTH leads are out-of-sample positive AND cohort-stable AND subperiod-stable - they are NOT
        the 10-B overfit/decay failure mode.
      * Their MONTHLY (21d) edge is destroyed by 25bps cost ONLY because the earnings-EVENT panel has
        ~99% month-to-month turnover - largely a STRUCTURAL artifact (names rotate by report date), not
        genuine signal churn.
      * BOTH SURVIVE 25bps and 50bps at the 63d QUARTERLY horizon (fcf 63d IC t=2.53, accruals 63d
        IC t=3.07).

    Phase 10-D therefore builds the obvious, transparent next thing: a QUARTERLY-rebalanced, equal-
    weight QUALITY COMPOSITE of the two leads, and re-validates it AT THE 63d HORIZON with a REALISTIC
    QUARTERLY-CADENCE turnover/cost model (not the monthly event-panel turnover that broke 10-C). It is
    NOT a data-acquisition / provider-search / Paper-Trader / order / automation phase.

COMPOSITE (transparent; fixed weights; NO optimization; NO sign-flipping; NO post-hoc selection)
    leg 1: fcf_to_assets        oriented +1  (higher FCF/assets is better)
    leg 2: operating_accruals   oriented -1  (Sloan: higher accruals is worse -> negate)
    composite = within-month z(leg1) + within-month z(leg2)            [equal weight 1.0 / 1.0]
    sector-neutral composite = z(sector-neutral leg1) + z(sector-neutral leg2)
    Z-scoring each leg within month already equalises signal variance, so equal-weight-of-z IS equal-
    risk in signal space; no separate local volatility / risk-model inputs exist, so NO optimised
    equal-risk weighting is built (that would be "optimised weights"). Orientations are fixed a-priori
    from the documented anomaly - never flipped to fit the data.

DECISION HORIZON
    63d (quarterly) is the PRIMARY decision horizon. 1d/5d/21d/63d are reported for context only.

REUSE (single source of truth)
    c10 = run_phase10c_eodhd_quality_oos_validation   (panel build, PIT attach + oriented/sector-neutral
                                                        columns, evaluate wrapper, OOS window grid)
    x8  = run_phase8x_autonomous_strong_alpha_discovery (walk-forward ensemble, decile spread)
    b10 = run_phase10b_eodhd_norgate_exhaustive_alpha_factory (secret-safety audit)
    s8 = x8.s8 (evaluate_signal core)   t8 = x8.t8 (net-spread model, logger)

TERMINAL DECISIONS (allowed)
    QUARTERLY_QUALITY_COMPOSITE_CONFIRMED_READY_FOR_PAPER_REVIEW |
    QUARTERLY_QUALITY_COMPOSITE_WEAK_BUT_WORTH_MONITORING |
    QUARTERLY_QUALITY_COMPOSITE_REJECTED_NOT_COST_ROBUST |
    QUARTERLY_QUALITY_COMPOSITE_REJECTED_COHORT_INSTABILITY |
    QUARTERLY_QUALITY_COMPOSITE_REJECTED_SECTOR_INSTABILITY |
    EODHD_NORGATE_QUALITY_BRANCH_EXHAUSTED | HARD_BLOCKER_REQUIRES_USER_ACTION |
    ERROR_WITH_REPRO_COMMAND
    FORBIDDEN: STRONG_ALPHA_FOUND_READY_FOR_REVIEW without quarterly OOS proof, MISSING_KEY, NO_DATA,
    NEEDS_PROVIDER, EMPTY_PAYLOAD, generic ERROR.

CONSTRAINTS HONORED
    Fully offline (no network / key / provider probe); only fcf_to_assets + operating_accruals used; no
    FMP / AlphaVantage / Polygon / Finnhub; no Paper Trader, no GCP, no orders, no automation, no
    deploy; no package install; no full regression (targeted tests only); keys never printed/written;
    output is metadata only. No commit. No push.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research import run_phase10c_eodhd_quality_oos_validation as c10           # noqa: E402
from research import run_phase8x_autonomous_strong_alpha_discovery as x8         # noqa: E402
from research import run_phase10b_eodhd_norgate_exhaustive_alpha_factory as b10  # noqa: E402

s8 = x8.s8
t8 = x8.t8
_write_json = s8._write_json
_write_csv = s8._write_csv
_read_csv_file = s8._read_csv_file
_read_json = s8._read_json
_round = s8._round
_rel = s8._rel
_finite = c10._finite
_num = c10._num
_pos = c10._pos

PHASE = "10-D"
PERFORMS_NETWORK = False

AS_OF = c10.AS_OF
FWD_WINDOWS = c10.FWD_WINDOWS                   # (1, 5, 21, 63)
PRIMARY_HORIZON_D = 63                          # QUARTERLY - the decision horizon for this phase
RET_PRIMARY = "fwd_exc_%d" % PRIMARY_HORIZON_D
_PRE2020 = "2020-01-01"
HORIZON_LABELS = {1: "1d", 5: "1w(5d)", 21: "1mo(21d)", 63: "1q(63d, primary)"}

WF_TRAIN_MONTHS = c10.WF_TRAIN_MONTHS
WF_TEST_MONTHS = c10.WF_TEST_MONTHS
WF_STEP_MONTHS = c10.WF_STEP_MONTHS

# Composite legs (fixed, a-priori). Orientation is applied by c10.attach_signals which builds the
# oriented column `o_<feature>` and the sector-neutral oriented column `o_<feature>__sn`.
LEGS: Tuple[Dict, ...] = (
    {"family": "eodhd_fcf_to_assets", "feature": "fcf_to_assets", "orientation": +1, "weight": 1.0,
     "anomaly": "cash-flow quality: higher FCF/assets -> higher forward excess return"},
    {"family": "eodhd_operating_accruals", "feature": "operating_accruals", "orientation": -1,
     "weight": 1.0,
     "anomaly": "Sloan accruals: higher accruals -> lower forward excess return (oriented = negated)"},
)
ALLOWED_FAMILIES = frozenset(l["family"] for l in LEGS)
COMPOSITE_WEIGHTING = "equal (fixed 1.0/1.0 on within-month z legs; no optimisation, no sign-flip)"

COMP_RAW = "comp_raw"
COMP_SN = "comp_sn"

# Acceptance thresholds (a-priori; never tuned to a result).
CONF_MIN_IC_T = x8.STRONG_MIN_IC_T              # 3.0 at the 63d primary horizon (project strong bar)
MONITOR_MIN_IC_T = 2.0                          # OOS-strong-but-slightly-below-3 -> monitor (per brief)
MAX_SECTOR_SHARE = t8.GATE_MAX_SECTOR_SHARE     # 0.60 single-sector ceiling
MIN_OOS_WIN_FRAC = 0.60
COST_BPS = (10, 25, 50)
_QTR_MIN_NAMES = 20                             # a quarterly cross-section needs this many names

# Decisions.
DEC_CONFIRMED = "QUARTERLY_QUALITY_COMPOSITE_CONFIRMED_READY_FOR_PAPER_REVIEW"
DEC_WEAK = "QUARTERLY_QUALITY_COMPOSITE_WEAK_BUT_WORTH_MONITORING"
DEC_REJ_COST = "QUARTERLY_QUALITY_COMPOSITE_REJECTED_NOT_COST_ROBUST"
DEC_REJ_COHORT = "QUARTERLY_QUALITY_COMPOSITE_REJECTED_COHORT_INSTABILITY"
DEC_REJ_SECTOR = "QUARTERLY_QUALITY_COMPOSITE_REJECTED_SECTOR_INSTABILITY"
DEC_EXHAUSTED = "EODHD_NORGATE_QUALITY_BRANCH_EXHAUSTED"
DEC_BLOCKER = "HARD_BLOCKER_REQUIRES_USER_ACTION"
DEC_ERROR = "ERROR_WITH_REPRO_COMMAND"
ALLOWED_DECISIONS = (DEC_CONFIRMED, DEC_WEAK, DEC_REJ_COST, DEC_REJ_COHORT, DEC_REJ_SECTOR,
                     DEC_EXHAUSTED, DEC_BLOCKER, DEC_ERROR)
FORBIDDEN_DECISIONS = ("STRONG_ALPHA_FOUND_READY_FOR_REVIEW", "MISSING_KEY", "NO_DATA",
                       "NEEDS_PROVIDER", "EMPTY_PAYLOAD", "ERROR")

# Internal composite verdict labels.
V_CONFIRMED = "CONFIRMED"
V_WEAK = "WEAK_MONITOR"
V_REJ_COST = "REJECTED_NOT_COST_ROBUST"
V_REJ_COHORT = "REJECTED_COHORT_INSTABILITY"
V_REJ_SECTOR = "REJECTED_SECTOR_INSTABILITY"
V_EXHAUSTED = "EXHAUSTED"
_VERDICT_TO_DEC = {V_CONFIRMED: DEC_CONFIRMED, V_WEAK: DEC_WEAK, V_REJ_COST: DEC_REJ_COST,
                   V_REJ_COHORT: DEC_REJ_COHORT, V_REJ_SECTOR: DEC_REJ_SECTOR, V_EXHAUSTED: DEC_EXHAUSTED}

EODHD_KEY_ENV = "EODHD_API_KEY"

_ARTIFACTS = {
    "report": "phase10d_quarterly_quality_composite_validation.json",
    "composite_def": "composite_definition.csv",
    "input_inventory": "signal_input_inventory.csv",
    "qtr_horizon": "quarterly_horizon_validation_report.csv",
    "cost_diag": "monthly_vs_quarterly_cost_diagnostic.csv",
    "wf_ic": "walk_forward_ic_report.csv",
    "wf_decile": "walk_forward_decile_report.csv",
    "tcost": "transaction_cost_report.csv",
    "turnover": "turnover_report.csv",
    "cohort": "cohort_stability_report.csv",
    "subperiod": "subperiod_stability_report.csv",
    "sector_excl": "sector_exclusion_report.csv",
    "liquidity": "liquidity_filter_report.csv",
    "standalone_cmp": "standalone_vs_composite_comparison.csv",
    "accepted": "accepted_quarterly_quality_composite.csv",
    "rejected": "rejected_quarterly_quality_composite.csv",
    "paper_pkg": "paper_review_candidate_package.csv",
    "next_plan": "phase10e_next_plan.json",
    "secret_audit": "secret_safety_audit.csv",
}
_REQUIRED_ARTIFACTS = tuple(_ARTIFACTS.keys())


class _Paths:
    def __init__(self, out_dir=None):
        self.out = Path(out_dir) if out_dir else (
            _REPO_ROOT / "research" / "output" / "phase10d_quarterly_quality_composite_validation")

    def art(self, key: str) -> Path:
        return self.out / _ARTIFACTS[key]


# --------------------------------------------------------------------------- #
# A. Composite construction (transparent; fixed equal weights; no sign-flip).
# --------------------------------------------------------------------------- #
def build_composite(ev, cols: Dict[str, Dict], log):
    """comp_raw = z(oriented fcf) + z(oriented -accruals); comp_sn = the same over the sector-neutral
    oriented legs. Both legs must be present for an event (clean 2-factor intersection)."""
    import numpy as np
    ev = ev.copy()
    fcf = cols.get("eodhd_fcf_to_assets", {})
    acc = cols.get("eodhd_operating_accruals", {})
    raw_legs = [fcf.get("oriented"), acc.get("oriented")]
    sn_legs = [fcf.get("sn"), acc.get("sn")]
    if any(c is None or c not in ev.columns for c in raw_legs):
        return ev, 0, raw_legs, sn_legs
    ev[COMP_RAW] = x8._within_month_z(ev, raw_legs[0]) + x8._within_month_z(ev, raw_legs[1])
    if all(c is not None and c in ev.columns for c in sn_legs):
        ev[COMP_SN] = x8._within_month_z(ev, sn_legs[0]) + x8._within_month_z(ev, sn_legs[1])
    else:
        ev[COMP_SN] = ev[COMP_RAW]
    cov = int(ev[COMP_RAW].notna().sum())
    log.step("composite", "DONE", "equal-weight 2-factor composite built: %d events both-legs covered"
             % cov)
    return ev, cov, raw_legs, sn_legs


# --------------------------------------------------------------------------- #
# B. Walk-forward OOS at an arbitrary horizon (equal weighting; fixed orientation, no sign refit).
# --------------------------------------------------------------------------- #
def walk_forward_h(ev, sigcol, ret_col, train, test, step) -> Dict:
    import numpy as np
    if ev is None or getattr(ev, "empty", True) or sigcol is None or sigcol not in ev.columns:
        return {"windows": [], "pooled_oos_ic": float("nan"), "oos_ic_t": float("nan"),
                "frac_windows_positive": float("nan"), "n_windows": 0, "decile": {}}
    ev_oos, windows = x8.build_walk_forward_ensemble(ev, [sigcol], "equal", ret_col,
                                                     train=train, test=test, step=step)
    ics = [w["oos_mean_ic"] for w in windows if _finite(w.get("oos_mean_ic"))]
    if ics:
        arr = np.asarray(ics, dtype=float)
        pooled = float(arr.mean())
        sd = float(arr.std(ddof=1)) if len(arr) > 1 else float("nan")
        t = (pooled / (sd / math.sqrt(len(arr)))) if (_finite(sd) and sd > 0) else float("nan")
        frac = float(np.mean(arr > 0))
    else:
        pooled, t, frac = float("nan"), float("nan"), float("nan")
    return {"windows": windows, "pooled_oos_ic": pooled, "oos_ic_t": t,
            "frac_windows_positive": frac, "n_windows": len(windows),
            "decile": x8.decile_spread(ev_oos, "__ens__", ret_col)}


# --------------------------------------------------------------------------- #
# C. Quarterly long-short backtest with REALISTIC quarterly-cadence turnover.
#     The 63d forward window ~ one quarter, so a quarterly-rebalanced book holds each quarter's
#     selection ~63 trading days and pays cost once per quarter. Turnover is computed between
#     consecutive CALENDAR QUARTERS on the SAME recurring tickers (earnings names report every
#     quarter), so it reflects genuine signal churn - NOT the month-to-month NAME ROTATION that
#     inflated the 10-C monthly event-panel turnover to ~0.99.
# --------------------------------------------------------------------------- #
def quarterly_backtest(ev, sigcol, ret_col=RET_PRIMARY, min_names=_QTR_MIN_NAMES) -> Dict:
    import numpy as np
    import pandas as pd
    empty = {"mean_spread": float("nan"), "spread_t": float("nan"), "spread_hit_rate": float("nan"),
             "n_quarters": 0, "avg_turnover": float("nan"), "top_sector": "",
             "top_sector_share": float("nan"),
             "net_10bps": float("nan"), "net_25bps": float("nan"), "net_50bps": float("nan")}
    if ev is None or getattr(ev, "empty", True) or sigcol not in ev.columns or ret_col not in ev.columns:
        return empty
    sub = ev[ev[sigcol].notna() & ev[ret_col].notna()].copy()
    if sub.empty:
        return empty
    sub["q"] = sub["entry_date"].dt.to_period("Q")
    has_sector = "sector" in sub.columns
    spreads, turns, long_sectors = [], [], {}
    prev_long, prev_short = set(), set()
    for q in sorted(sub["q"].unique()):
        chunk = sub[sub["q"] == q].sort_values("entry_date").groupby("ticker", as_index=False).last()
        if len(chunk) < min_names:
            continue
        try:
            qd = pd.qcut(chunk[sigcol].rank(method="first"), 5, labels=False)
        except ValueError:
            continue
        top, bot = chunk[qd == 4], chunk[qd == 0]
        if top.empty or bot.empty:
            continue
        spreads.append(float(top[ret_col].mean() - bot[ret_col].mean()))
        cl, cs = set(top["ticker"]), set(bot["ticker"])
        if prev_long:
            turns.append(1.0 - len(cl & prev_long) / max(1, len(cl)))
        if prev_short:
            turns.append(1.0 - len(cs & prev_short) / max(1, len(cs)))
        prev_long, prev_short = cl, cs
        if has_sector:
            for sct in top["sector"]:
                long_sectors[sct] = long_sectors.get(sct, 0) + 1
    n_q = len(spreads)
    if not n_q:
        return empty
    arr = np.asarray(spreads, dtype=float)
    mean_spread = float(arr.mean())
    sd = float(arr.std(ddof=1)) if n_q > 1 else float("nan")
    t = (mean_spread / (sd / math.sqrt(n_q))) if (_finite(sd) and sd > 0) else float("nan")
    hit = float(np.mean(np.sign(arr) == np.sign(mean_spread)))
    avg_turn = float(np.mean(turns)) if turns else float("nan")
    tot = sum(long_sectors.values())
    if tot:
        shares = {k: v / tot for k, v in long_sectors.items()}
        top_sector = max(shares, key=shares.get)
        top_share = shares[top_sector]
    else:
        top_sector, top_share = "", float("nan")

    def net(bps):
        tn = avg_turn if _finite(avg_turn) else 1.0
        return mean_spread - (bps / 1e4) * tn * 2.0
    return {"mean_spread": mean_spread, "spread_t": t, "spread_hit_rate": hit, "n_quarters": n_q,
            "avg_turnover": avg_turn, "top_sector": top_sector, "top_sector_share": top_share,
            "net_10bps": net(10), "net_25bps": net(25), "net_50bps": net(50)}


# --------------------------------------------------------------------------- #
# D. Full per-signal robustness battery at the 63d horizon.
# --------------------------------------------------------------------------- #
def battery(ev, col, train, test, step) -> Dict:
    import pandas as pd
    has_cohort = "cohort" in ev.columns
    has_sector = "sector" in ev.columns
    liq = ev["liquidity_proxy"] if "liquidity_proxy" in ev.columns else None
    liq_med = float(liq.median()) if liq is not None and liq.notna().any() else float("nan")
    horizons = {h: c10._eval(ev, col, h, False) for h in FWD_WINDOWS}
    prim = horizons[PRIMARY_HORIZON_D]
    if has_cohort:
        cohort = {"old": c10._eval(ev[ev["cohort"] == "old"], col, PRIMARY_HORIZON_D, False),
                  "new": c10._eval(ev[ev["cohort"] == "new"], col, PRIMARY_HORIZON_D, False)}
    else:
        cohort = {"old": prim, "new": prim}
    pre = ev[ev["entry_date"] < pd.Timestamp(_PRE2020)]
    post = ev[ev["entry_date"] >= pd.Timestamp(_PRE2020)]
    subperiod = {"pre2020": c10._eval(pre, col, PRIMARY_HORIZON_D, False),
                 "post2020": c10._eval(post, col, PRIMARY_HORIZON_D, False)}
    sect_rows, sign_holds_all = [], True
    if has_sector:
        for sct in sorted(x for x in ev["sector"].dropna().unique()):
            ms = c10._eval(ev[ev["sector"] != sct], col, PRIMARY_HORIZON_D, False)
            holds = _pos(ms)
            sign_holds_all = sign_holds_all and holds
            sect_rows.append({"excluded_sector": str(sct), "mean_ic": ms.get("mean_ic"),
                              "ic_t": ms.get("ic_t"), "n_events": ms.get("n_events"),
                              "sign_holds": holds})
    sector_excl = {"rows": sect_rows, "sign_holds_all": bool(sign_holds_all and sect_rows),
                   "top_sector": prim.get("top_sector"), "top_sector_share": prim.get("top_sector_share"),
                   "hhi": prim.get("hhi")}
    if liq is not None and _finite(liq_med):
        liquidity = {"full": prim,
                     "high_liq": c10._eval(ev[ev["liquidity_proxy"] >= liq_med], col, PRIMARY_HORIZON_D, False),
                     "low_liq": c10._eval(ev[ev["liquidity_proxy"] < liq_med], col, PRIMARY_HORIZON_D, False)}
    else:
        liquidity = {"full": prim, "high_liq": prim, "low_liq": prim}
    wf = walk_forward_h(ev, col, RET_PRIMARY, train, test, step)
    qbt = quarterly_backtest(ev, col, RET_PRIMARY)
    return {"horizons": horizons, "cohort": cohort, "subperiod": subperiod, "sector_excl": sector_excl,
            "liquidity": liquidity, "wf": wf, "quarterly": qbt}


# --------------------------------------------------------------------------- #
# E. Composite verdict (decision on comp_raw headline + comp_sn cross-check; quarterly cost model).
# --------------------------------------------------------------------------- #
def decide_composite(b_raw: Dict, b_sn: Dict) -> Tuple[Dict, str, str]:
    h63 = b_raw["horizons"][PRIMARY_HORIZON_D]
    sn63 = b_sn["horizons"][PRIMARY_HORIZON_D]
    wf = b_raw["wf"]
    qbt = b_raw["quarterly"]
    cohort = b_raw["cohort"]
    sub = b_raw["subperiod"]
    sect = b_raw["sector_excl"]
    liq = b_raw["liquidity"]
    share = sect.get("top_sector_share")
    ic_t = h63.get("ic_t")
    flags = {
        "ic_t63": ic_t if _finite(ic_t) else float("nan"),
        "oos": (_finite(wf.get("pooled_oos_ic")) and wf["pooled_oos_ic"] > 0
                and _finite(wf.get("frac_windows_positive")) and wf["frac_windows_positive"] >= MIN_OOS_WIN_FRAC),
        "cohort": _pos(cohort.get("old", {})) and _pos(cohort.get("new", {})),
        "subperiod": _pos(sub.get("pre2020", {})) and _pos(sub.get("post2020", {})),
        "sector": ((not _finite(share)) or share < MAX_SECTOR_SHARE) and bool(sect.get("sign_holds_all")),
        "sn_positive": _pos(sn63),
        "highliq": _pos(liq.get("high_liq", {})),
        "cost25_q": _finite(qbt.get("net_25bps")) and qbt["net_25bps"] > 0,
        "cost50_q": _finite(qbt.get("net_50bps")) and qbt["net_50bps"] > 0,
        "strong_t": _finite(ic_t) and ic_t >= CONF_MIN_IC_T,
        "monitor_t": _finite(ic_t) and ic_t >= MONITOR_MIN_IC_T,
        "qtr_turnover": qbt.get("avg_turnover"),
    }
    if not flags["cohort"]:
        return flags, V_REJ_COHORT, ("the quarterly composite old/new cohort signs disagree "
                                     "(cohort-unstable)")
    if not flags["sector"]:
        return flags, V_REJ_SECTOR, ("the quarterly composite long book is single-sector dominated "
                                     "(top-sector share >= %.0f%%) or a leave-one-sector-out flips the "
                                     "sign" % (MAX_SECTOR_SHARE * 100))
    if not flags["cost25_q"]:
        return flags, V_REJ_COST, ("the quarterly composite 63d edge does not survive 25bps round-trip "
                                   "cost even at the realistic quarterly rebalance turnover")
    if (flags["strong_t"] and flags["subperiod"] and flags["sn_positive"] and flags["highliq"]
            and flags["oos"] and flags["cost50_q"]):
        return flags, V_CONFIRMED, ("quarterly composite clears the strict bar at 63d: IC t>=%.1f, "
                                    "survives 25bps AND 50bps at quarterly turnover, OOS-positive, both "
                                    "cohorts +, both subperiods +, sector-robust, sector-neutral edge "
                                    "intact, high-liquidity +" % CONF_MIN_IC_T)
    if ((flags["strong_t"] or (flags["monitor_t"] and flags["oos"])) and flags["subperiod"]
            and flags["sn_positive"] and flags["oos"]):
        return flags, V_WEAK, ("quarterly composite is out-of-sample real and survives 25bps at quarterly "
                               "turnover (both cohorts +, both subperiods +, sector-neutral edge intact) "
                               "but does not fully clear the IC t>=%.1f / 50bps strong bar at 63d - worth "
                               "monitoring, not yet paper-ready" % CONF_MIN_IC_T)
    return flags, V_EXHAUSTED, ("the quarterly composite adds no robust, cost-surviving edge beyond the "
                                "standalone leads at 63d - the owned-data quality branch is exhausted")


# --------------------------------------------------------------------------- #
# F. Artifact writers.
# --------------------------------------------------------------------------- #
def _hz_rows(name, b):
    out = []
    for h in FWD_WINDOWS:
        m = b["horizons"][h]
        out.append([name, h, HORIZON_LABELS.get(h, str(h)), _num(m.get("mean_ic")), _num(m.get("ic_t")),
                    _num(m.get("ic_p")), m.get("n_months", 0), m.get("n_events", 0),
                    "primary" if h == PRIMARY_HORIZON_D else ""])
    return out


def write_artifacts(P: _Paths, ev, cols, comp_cov, B, decision, verdict, reason, flags, log):
    # 1. composite_definition
    def_rows = []
    for leg in LEGS:
        def_rows.append([leg["feature"], leg["family"], leg["orientation"], leg["weight"],
                         "within-month z of oriented level", "comp_raw", leg["anomaly"]])
        def_rows.append([leg["feature"], leg["family"], leg["orientation"], leg["weight"],
                         "within month x sector demean, then within-month z", "comp_sn", leg["anomaly"]])
    def_rows.append(["__composite__", "equal-weight sum", "", COMPOSITE_WEIGHTING, "no optimisation",
                     "comp_raw + comp_sn", "z-scoring each leg within month equalises variance "
                     "(equal-risk in signal space); no local risk-model inputs -> no optimised weights"])
    _write_csv(P.art("composite_def"),
               ["leg_feature", "leg_family_or_role", "orientation", "weight", "transform", "composite",
                "note"], def_rows)

    # 2. signal_input_inventory
    inv = []
    for leg in LEGS:
        ci = cols.get(leg["family"], {})
        inv.append([leg["family"], leg["feature"], leg["orientation"],
                    _rel(c10._norm_csv(leg["family"], leg["feature"])), ci.get("coverage", 0),
                    ci.get("tickers", 0), "owned EODHD/Norgate (Phase 10-B normalized; PIT)"])
    inv.append(["__composite__", "comp_raw (both legs)", "", "", comp_cov, "", "intersection of legs"])
    _write_csv(P.art("input_inventory"),
               ["family", "feature", "orientation", "normalized_path", "panel_coverage_events",
                "panel_coverage_tickers", "source"], inv)

    # 3. quarterly_horizon_validation_report
    hrows = []
    for name, b in B.items():
        hrows.extend(_hz_rows(name, b))
    _write_csv(P.art("qtr_horizon"),
               ["signal", "horizon_days", "horizon_label", "mean_ic", "ic_t", "ic_p", "n_months",
                "n_events", "note"], hrows)

    # 4. monthly_vs_quarterly_cost_diagnostic
    crows = []
    for name, b in B.items():
        m21 = b["horizons"][21]
        m63 = b["horizons"][63]
        q = b["quarterly"]
        mt = m21.get("avg_turnover")
        qt = q.get("avg_turnover")
        artifact = (_finite(mt) and _finite(qt) and qt < mt - 0.2)
        diag = ("monthly event-panel turnover %s vs quarterly turnover %s -> the 21d net-of-cost "
                "failure was %s by the event-panel NAME-ROTATION structure, not by the signal"
                % (_num(mt), _num(qt), "DRIVEN" if artifact else "NOT explained"))
        crows.append([name, _num(mt), _num(qt), _num(m21.get("mean_spread")),
                      _num(m63.get("mean_spread")), _num(q.get("mean_spread")),
                      _num(m21.get("net_spread_25bps")), _num(q.get("net_25bps")),
                      _num(q.get("net_50bps")), diag])
    _write_csv(P.art("cost_diag"),
               ["signal", "monthly_event_turnover", "quarterly_turnover", "gross_spread_21d",
                "gross_spread_63d_eventpanel", "gross_spread_63d_quarterly", "net25_monthly_21d",
                "net25_quarterly_63d", "net50_quarterly_63d", "diagnosis"], crows)

    # 5. walk_forward_ic_report
    wrows = []
    for name, b in B.items():
        wf = b["wf"]
        for w in wf.get("windows", []):
            wrows.append([name, w.get("window"), w.get("train_start"), w.get("train_end"),
                          w.get("test_start"), w.get("test_end"), w.get("n_test_months"),
                          _num(w.get("oos_mean_ic")), "window"])
        wrows.append([name, "ALL", "", "", "", "", wf.get("n_windows", 0), _num(wf.get("pooled_oos_ic")),
                      "pooled oos_ic_t=%s frac_pos=%s" % (_num(wf.get("oos_ic_t")),
                                                          _num(wf.get("frac_windows_positive")))])
    _write_csv(P.art("wf_ic"),
               ["signal", "window", "train_start", "train_end", "test_start", "test_end",
                "n_test_months", "oos_mean_ic", "note"], wrows)

    # 6. walk_forward_decile_report
    drows = []
    for name, b in B.items():
        d = b["wf"].get("decile", {}) or {}
        drows.append([name, _num(d.get("mean_decile_spread")), _num(d.get("top_decile_ret")),
                      _num(d.get("bottom_decile_ret")), _num(d.get("decile_hit_rate")),
                      d.get("n_months", 0)])
    _write_csv(P.art("wf_decile"),
               ["signal", "oos_mean_decile_spread", "oos_top_decile_ret", "oos_bottom_decile_ret",
                "oos_decile_hit_rate", "n_oos_months"], drows)

    # 7. transaction_cost_report (event-panel 21d & 63d + quarterly 63d)
    trows = []
    for name, b in B.items():
        for h in (21, 63):
            m = b["horizons"][h]
            trows.append([name, "event_panel_%dd" % h, _num(m.get("mean_spread")),
                          _num(m.get("avg_turnover")), _num(m.get("net_spread_10bps")),
                          _num(m.get("net_spread_25bps")), _num(m.get("net_spread_50bps"))])
        q = b["quarterly"]
        trows.append([name, "quarterly_63d", _num(q.get("mean_spread")), _num(q.get("avg_turnover")),
                      _num(q.get("net_10bps")), _num(q.get("net_25bps")), _num(q.get("net_50bps"))])
    _write_csv(P.art("tcost"),
               ["signal", "model", "gross_spread", "turnover", "net_10bps", "net_25bps", "net_50bps"],
               trows)

    # 8. turnover_report
    turn = []
    for name, b in B.items():
        turn.append([name, _num(b["horizons"][21].get("avg_turnover")),
                     _num(b["quarterly"].get("avg_turnover")), b["horizons"][21].get("n_months", 0),
                     b["quarterly"].get("n_quarters", 0)])
    _write_csv(P.art("turnover"),
               ["signal", "monthly_event_turnover", "quarterly_turnover", "n_event_months",
                "n_quarters"], turn)

    # 9. cohort_stability
    crow = []
    for name, b in B.items():
        o, n = b["cohort"]["old"], b["cohort"]["new"]
        crow.append([name, _num(o.get("mean_ic")), _num(o.get("ic_t")), o.get("n_events", 0),
                     _num(n.get("mean_ic")), _num(n.get("ic_t")), n.get("n_events", 0),
                     _pos(o) and _pos(n)])
    _write_csv(P.art("cohort"),
               ["signal", "old_ic", "old_ic_t", "old_n_events", "new_ic", "new_ic_t", "new_n_events",
                "both_cohorts_positive"], crow)

    # 10. subperiod_stability
    srow = []
    for name, b in B.items():
        pre, post = b["subperiod"]["pre2020"], b["subperiod"]["post2020"]
        srow.append([name, _num(pre.get("mean_ic")), _num(pre.get("ic_t")), pre.get("n_months", 0),
                     _num(post.get("mean_ic")), _num(post.get("ic_t")), post.get("n_months", 0),
                     _pos(pre) and _pos(post)])
    _write_csv(P.art("subperiod"),
               ["signal", "pre2020_ic", "pre2020_ic_t", "pre2020_n_months", "post2020_ic",
                "post2020_ic_t", "post2020_n_months", "both_subperiods_positive"], srow)

    # 11. sector_exclusion
    serow = []
    for name, b in B.items():
        se = b["sector_excl"]
        serow.append([name, "FULL", _num(se.get("top_sector_share")), se.get("top_sector", ""),
                      _num(se.get("hhi")), se.get("sign_holds_all"), ""])
        for row in se.get("rows", []):
            serow.append([name, "exclude:%s" % row["excluded_sector"], _num(row.get("mean_ic")), "",
                          _num(row.get("ic_t")), row.get("sign_holds"), row.get("n_events", 0)])
    _write_csv(P.art("sector_excl"),
               ["signal", "scope", "metric_a", "top_sector", "metric_b", "sign_holds", "n_events"],
               serow)

    # 12. liquidity_filter
    lrow = []
    for name, b in B.items():
        full, hi, lo = b["liquidity"]["full"], b["liquidity"]["high_liq"], b["liquidity"]["low_liq"]
        lrow.append([name, _num(full.get("mean_ic")), _num(full.get("ic_t")), _num(hi.get("mean_ic")),
                     _num(hi.get("ic_t")), hi.get("n_events", 0), _num(lo.get("mean_ic")),
                     _num(lo.get("ic_t")), _pos(hi)])
    _write_csv(P.art("liquidity"),
               ["signal", "full_ic", "full_ic_t", "high_liq_ic", "high_liq_ic_t", "high_liq_n_events",
                "low_liq_ic", "low_liq_ic_t", "high_liq_positive"], lrow)

    # 13. standalone_vs_composite_comparison (63d)
    cmp_rows = []
    for name in ("composite_raw", "fcf_to_assets", "operating_accruals"):
        if name not in B:
            continue
        b = B[name]
        m63 = b["horizons"][63]
        q = b["quarterly"]
        cmp_rows.append([name, _num(m63.get("mean_ic")), _num(m63.get("ic_t")),
                         _num(b["wf"].get("pooled_oos_ic")), _num(b["wf"].get("frac_windows_positive")),
                         _num(q.get("mean_spread")), _num(q.get("avg_turnover")), _num(q.get("net_25bps")),
                         _num(q.get("net_50bps"))])
    _write_csv(P.art("standalone_cmp"),
               ["signal", "ic_63d", "ic_t_63d", "oos_pooled_ic", "oos_frac_pos",
                "quarterly_gross_spread", "quarterly_turnover", "quarterly_net_25bps",
                "quarterly_net_50bps"], cmp_rows)

    # 14/15. accepted + rejected
    b = B["composite_raw"]
    m63 = b["horizons"][63]
    q = b["quarterly"]
    row = ["composite_raw", verdict, _num(m63.get("mean_ic")), _num(m63.get("ic_t")),
           _num(q.get("net_25bps")), _num(q.get("net_50bps")), _num(q.get("avg_turnover")),
           flags.get("cohort"), flags.get("subperiod"), flags.get("sector"), reason]
    hdr = ["signal", "verdict", "ic_63d", "ic_t_63d", "quarterly_net_25bps", "quarterly_net_50bps",
           "quarterly_turnover", "both_cohorts_pos", "both_subperiods_pos", "sector_robust", "reason"]
    _write_csv(P.art("accepted"), hdr, [row] if verdict == V_CONFIRMED else [])
    _write_csv(P.art("rejected"), hdr,
               [row] if verdict in (V_REJ_COST, V_REJ_COHORT, V_REJ_SECTOR, V_EXHAUSTED) else [])

    # 16. paper_review_candidate_package
    ready = verdict == V_CONFIRMED
    action = ("READY: hand the quarterly quality composite to a human for PAPER review (paper-only; "
              "QUARTERLY rebalance; sector-neutral decile/quintile book; cost budget <25bps; NO orders, "
              "NO automation)" if ready else
              ("MONITOR: re-test at next data refresh / longer walk-forward; NOT ready for paper trading"
               if verdict == V_WEAK else "DO NOT TRADE: %s" % verdict))
    _write_csv(P.art("paper_pkg"),
               ["signal", "weighting", "primary_horizon", "verdict", "ready_for_paper_review", "ic_63d",
                "ic_t_63d", "quarterly_net_25bps", "quarterly_net_50bps", "quarterly_turnover",
                "oos_pooled_ic", "both_cohorts_pos", "both_subperiods_pos", "sector_robust",
                "recommended_action"],
               [["quarterly_quality_composite (fcf+ , accruals-)", COMPOSITE_WEIGHTING, "63d", verdict,
                 "YES" if ready else "NO", _num(m63.get("mean_ic")), _num(m63.get("ic_t")),
                 _num(q.get("net_25bps")), _num(q.get("net_50bps")), _num(q.get("avg_turnover")),
                 _num(b["wf"].get("pooled_oos_ic")), flags.get("cohort"), flags.get("subperiod"),
                 flags.get("sector"), action]])

    # 17. secret_safety_audit
    sec_rows, clean = b10._secret_safety_audit(P.out)
    _write_csv(P.art("secret_audit"), ["file", "clean", "keyed_url_marker", "key_value_present"],
               [[r["file"], r["clean"], r["keyed_url_marker"], r["key_value_present"]] for r in sec_rows])
    return clean


def _phase10e_plan(decision: str, B: Dict, flags: Dict) -> Dict:
    if decision == DEC_CONFIRMED:
        nxt = ("Phase 10-E: build a PAPER-ONLY review harness for the quarterly quality composite "
               "(quarterly rebalance, sector-neutral book, cost budget <25bps) with a human "
               "approve/reject gate. No orders, no automation, no deploy.")
        cmd = ("review research/output/phase10d_quarterly_quality_composite_validation/"
               "paper_review_candidate_package.csv")
    elif decision == DEC_WEAK:
        nxt = ("Phase 10-E: keep monitoring the quarterly quality composite; extend the walk-forward "
               "window as months accrue; consider improving Norgate/EODHD sector-mapping coverage for "
               "the 'Unknown' bucket (owned data) to firm up the sector-neutral book. Do NOT paper-trade "
               "yet. No new purchase.")
        cmd = ("python research/run_phase10d_quarterly_quality_composite_validation.py   "
               "# re-run after next EODHD refresh")
    elif decision == DEC_REJ_SECTOR:
        nxt = ("Phase 10-E: the composite is sector-unstable (likely the unmapped 'Unknown' sector "
               "bucket dominating the long book). Improve sector-mapping coverage from the OWNED "
               "Norgate/EODHD metadata, rebuild the sector-neutral composite, and re-run THIS battery. "
               "No new data purchase.")
        cmd = ("review research/output/phase10d_quarterly_quality_composite_validation/"
               "sector_exclusion_report.csv")
    else:
        nxt = ("Phase 10-E: the owned-data quality branch (fcf_to_assets + operating_accruals) does not "
               "yield a cost-robust, paper-ready alpha even as a quarterly composite. Options (no new "
               "purchase without explicit user opt-in): (a) widen the owned-EODHD quality family set "
               "into the composite (gross_profitability, asset_growth, net_share_issuance) at the "
               "quarterly horizon; (b) only if the user opts in, add ONE orthogonal paid family "
               "(analyst estimate-revision history). Do NOT productize any horizon-cherry-picked or "
               "cost-fragile signal.")
        cmd = ("review research/output/phase10d_quarterly_quality_composite_validation/"
               "phase10d_quarterly_quality_composite_validation.json")
    return {"phase": "10-E", "from_decision": decision, "next_step": nxt, "exact_next_command": cmd,
            "constraints": ["owned data only; no new purchase without explicit user opt-in", "paper-only",
                            "no orders", "no automation", "no deploy", "no commit", "no push"]}


# --------------------------------------------------------------------------- #
# G. Report + orchestration.
# --------------------------------------------------------------------------- #
def _sig_report(name, b):
    m63 = b["horizons"][63]
    q = b["quarterly"]
    return {
        "signal": name,
        "ic_63d": _round(m63.get("mean_ic"), 5), "ic_t_63d": _round(m63.get("ic_t"), 3),
        "horizon_ic_t": {HORIZON_LABELS.get(h, str(h)): _round(b["horizons"][h].get("ic_t"), 3)
                         for h in FWD_WINDOWS},
        "oos_pooled_ic": _round(b["wf"].get("pooled_oos_ic"), 5),
        "oos_ic_t": _round(b["wf"].get("oos_ic_t"), 3),
        "oos_frac_windows_positive": _round(b["wf"].get("frac_windows_positive"), 3),
        "quarterly_gross_spread": _round(q.get("mean_spread"), 5),
        "quarterly_spread_t": _round(q.get("spread_t"), 3),
        "quarterly_turnover": _round(q.get("avg_turnover"), 4),
        "quarterly_net_25bps": _round(q.get("net_25bps"), 5),
        "quarterly_net_50bps": _round(q.get("net_50bps"), 5),
        "monthly_event_turnover": _round(b["horizons"][21].get("avg_turnover"), 4),
        "both_cohorts_positive": _pos(b["cohort"]["old"]) and _pos(b["cohort"]["new"]),
        "both_subperiods_positive": _pos(b["subperiod"]["pre2020"]) and _pos(b["subperiod"]["post2020"]),
        "top_sector_share": _round(b["sector_excl"].get("top_sector_share"), 4),
        "sector_sign_holds_all": b["sector_excl"].get("sign_holds_all"),
        "high_liquidity_positive": _pos(b["liquidity"]["high_liq"]),
    }


def _build_report(decision, verdict, reason, flags, B, comp_cov, n_events, n_tickers, panel_ok,
                  key_visible, leak_clean, as_of) -> Dict:
    return {
        "phase": PHASE, "as_of": as_of, "decision": decision, "decision_rationale": reason,
        "composite_verdict": verdict,
        "allowed_decisions": list(ALLOWED_DECISIONS), "forbidden_decisions": list(FORBIDDEN_DECISIONS),
        "objective": ("validate a transparent quarterly EODHD/Norgate quality composite (fcf_to_assets "
                      "long + operating_accruals short) at the 63d horizon with realistic quarterly "
                      "turnover - NOT acquisition / provider-search / Paper-Trader / orders"),
        "performs_network": PERFORMS_NETWORK, "offline": True,
        "eodhd_key_visible": bool(key_visible), "eodhd_key_required": False,
        "panel_ok": panel_ok, "scoreable_events": n_events, "scoreable_tickers": n_tickers,
        "composite_coverage_events": comp_cov,
        "legs_used": [l["feature"] for l in LEGS], "leg_families": list(ALLOWED_FAMILIES),
        "composite_weighting": COMPOSITE_WEIGHTING, "optimised_weights": False, "sign_flipping": False,
        "primary_horizon_days": PRIMARY_HORIZON_D,
        "horizons_tested": list(FWD_WINDOWS), "horizon_labels": HORIZON_LABELS,
        "walk_forward": {"train_months": WF_TRAIN_MONTHS, "test_months": WF_TEST_MONTHS,
                         "step_months": WF_STEP_MONTHS,
                         "weighting": "equal (fixed orientation; pure held-out IC; no sign refit)"},
        "cost_model": {"decision_cost_model": "quarterly long-short backtest (realistic quarterly "
                       "rebalance turnover)", "diagnostic_cost_model": "event-panel monthly turnover "
                       "(the 10-C model; overstates turnover via name rotation)"},
        "acceptance_thresholds": {"confirmed_min_ic_t_63d": CONF_MIN_IC_T,
                                  "monitor_min_ic_t_63d": MONITOR_MIN_IC_T,
                                  "max_sector_share": MAX_SECTOR_SHARE,
                                  "min_oos_window_frac": MIN_OOS_WIN_FRAC,
                                  "cost_decision": "net 25bps positive at 63d on quarterly turnover"},
        "composite_flags": {k: v for k, v in flags.items() if isinstance(v, bool)},
        "composite_survived": verdict in (V_CONFIRMED, V_WEAK),
        "ready_for_paper_review": verdict == V_CONFIRMED,
        "signal_results": [_sig_report(n, b) for n, b in B.items()],
        "secret_safety_leak_scan_clean": leak_clean,
        "api_key_printed": False, "api_key_written_to_disk": False,
        "required_artifacts": [_ARTIFACTS[k] for k in _REQUIRED_ARTIFACTS],
        "exact_next_command": _phase10e_plan(decision, B, flags)["exact_next_command"],
        "constraints_honored": ["offline (no network/key/provider probe)", "only fcf_to_assets + "
                                "operating_accruals used", "fixed equal weights; no optimisation; no "
                                "sign-flip", "no FMP/AlphaVantage/Polygon/Finnhub", "no Paper Trader",
                                "no GCP", "no orders", "no automation", "no deploy", "no package install",
                                "no full regression", "no commit", "no push", "no key printed/written"],
    }


def _print_summary(report: Dict) -> None:
    print("[10-D] decision=%s | verdict=%s | offline=%s | events=%s | comp_cov=%s | 63d IC t=%s | "
          "qtr net25=%s net50=%s turnover=%s | ready_paper=%s | leak_clean=%s"
          % (report.get("decision"), report.get("composite_verdict"), report.get("offline"),
             report.get("scoreable_events"), report.get("composite_coverage_events"),
             _g(report, "ic_t_63d"), _g(report, "quarterly_net_25bps"), _g(report, "quarterly_net_50bps"),
             _g(report, "quarterly_turnover"), report.get("ready_for_paper_review"),
             report.get("secret_safety_leak_scan_clean")))


def _g(report, key):
    for r in report.get("signal_results", []):
        if r["signal"] == "composite_raw":
            return r.get(key)
    return None


def run(out_dir: Optional[Path] = None, *, ev=None, norm_csvs: Optional[Dict[str, Path]] = None,
        panel_csv: Optional[Path] = None, as_of: str = AS_OF, train_months: int = WF_TRAIN_MONTHS,
        test_months: int = WF_TEST_MONTHS, step_months: int = WF_STEP_MONTHS,
        verbose: bool = True) -> Dict:
    P = _Paths(out_dir)
    P.out.mkdir(parents=True, exist_ok=True)
    log = t8._Log(verbose)
    try:
        import pandas as pd  # noqa: F401
        key_visible = bool(os.environ.get(EODHD_KEY_ENV))  # context only; NEVER printed/written
        log.step("preflight", "OFFLINE", "no network / no key required; EODHD key visible=%s"
                 % key_visible)

        if ev is None:
            ev, panel_ok, stats = c10.build_panel(as_of, panel_csv, log)
        else:
            panel_ok = not getattr(ev, "empty", True)
            stats = {"events_usable": int(len(ev)) if panel_ok else 0,
                     "tickers_usable": int(ev["ticker"].nunique()) if panel_ok else 0}
        if not panel_ok:
            return _finish_blocker(P, log, ("the Norgate survivorship-free panel is empty - rebuild the "
                                            "expanded price panel (Phase 8-A/8-V) before running 10-D."),
                                   "python research/run_phase8a_autonomous_norgate_research_engine.py "
                                   "--rebuild", key_visible, as_of)
        n_events = int(stats.get("events_usable", 0) or len(ev))
        n_tickers = int(stats.get("tickers_usable", 0) or ev["ticker"].nunique())

        norm_csvs = norm_csvs or c10._default_norm_csvs()
        missing = [l["feature"] for l in LEGS if not Path(norm_csvs.get(l["family"], "")).is_file()]
        if missing:
            return _finish_blocker(P, log, ("missing Phase 10-B normalized family CSV(s): %s. Run Phase "
                                            "10-B first." % ", ".join(missing)),
                                   ("python research/run_phase10b_eodhd_norgate_exhaustive_alpha_"
                                    "factory.py --live"), key_visible, as_of)
        ev, cols = c10.attach_signals(ev, norm_csvs, log)
        ev, comp_cov, _rl, _sl = build_composite(ev, cols, log)
        if comp_cov == 0:
            return _finish_blocker(P, log, ("the two quality legs do not co-occur on any event - cannot "
                                            "form the composite."),
                                   "python research/run_phase10c_eodhd_quality_oos_validation.py",
                                   key_visible, as_of)

        ofcf = cols["eodhd_fcf_to_assets"]["oriented"]
        oacc = cols["eodhd_operating_accruals"]["oriented"]
        B: Dict[str, Dict] = {
            "composite_raw": battery(ev, COMP_RAW, train_months, test_months, step_months),
            "composite_sn": battery(ev, COMP_SN, train_months, test_months, step_months),
            "fcf_to_assets": battery(ev, ofcf, train_months, test_months, step_months),
            "operating_accruals": battery(ev, oacc, train_months, test_months, step_months),
        }
        for name in B:
            c63 = B[name]["horizons"][63]
            q = B[name]["quarterly"]
            log.step("signal", "DONE", "%s: 63d IC t=%s | qtr net25=%s turnover=%s"
                     % (name, _num(c63.get("ic_t")), _num(q.get("net_25bps")), _num(q.get("avg_turnover"))))

        flags, verdict, reason = decide_composite(B["composite_raw"], B["composite_sn"])
        decision = _VERDICT_TO_DEC.get(verdict, DEC_EXHAUSTED)
        if decision not in ALLOWED_DECISIONS or decision in FORBIDDEN_DECISIONS:
            decision = DEC_EXHAUSTED

        leak_clean = write_artifacts(P, ev, cols, comp_cov, B, decision, verdict, reason, flags, log)
        report = _build_report(decision, verdict, reason, flags, B, comp_cov, n_events, n_tickers,
                               panel_ok, key_visible, leak_clean, as_of)
        _write_json(P.art("report"), report)
        _write_json(P.art("next_plan"), _phase10e_plan(decision, B, flags))
        log.step("artifacts", "DONE", "wrote %d artifacts" % len(_REQUIRED_ARTIFACTS))
        _print_summary(report)
        return report
    except Exception as exc:  # pragma: no cover - defensive
        import traceback
        detail = "%s: %s" % (type(exc).__name__, exc)
        log.step("run", "ERROR", detail)
        report = {"phase": PHASE, "decision": DEC_ERROR, "decision_rationale": detail,
                  "repro_command": "python research/run_phase10d_quarterly_quality_composite_validation.py",
                  "traceback": traceback.format_exc()}
        try:
            P.out.mkdir(parents=True, exist_ok=True)
            _write_json(P.art("report"), report)
        except Exception:
            pass
        return report


def _finish_blocker(P: _Paths, log, detail: str, fix_cmd: str, key_visible: bool, as_of: str) -> Dict:
    log.step("blocker", "HARD_BLOCKER", detail)
    report = {"phase": PHASE, "as_of": as_of, "decision": DEC_BLOCKER, "decision_rationale": detail,
              "exact_next_command": fix_cmd, "allowed_decisions": list(ALLOWED_DECISIONS),
              "offline": True, "performs_network": PERFORMS_NETWORK,
              "eodhd_key_visible": bool(key_visible), "api_key_printed": False,
              "api_key_written_to_disk": False, "legs_used": [l["feature"] for l in LEGS]}
    try:
        _write_json(P.art("report"), report)
        _write_json(P.art("next_plan"), {"phase": "10-E", "from_decision": DEC_BLOCKER,
                                          "exact_next_command": fix_cmd})
        sec_rows, _clean = b10._secret_safety_audit(P.out)
        _write_csv(P.art("secret_audit"), ["file", "clean", "keyed_url_marker", "key_value_present"],
                   [[r["file"], r["clean"], r["keyed_url_marker"], r["key_value_present"]]
                    for r in sec_rows])
    except Exception:
        pass
    print("[10-D] decision=%s | %s" % (DEC_BLOCKER, detail))
    return report


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Phase 10-D - Quarterly EODHD/Norgate Quality Composite Validation")
    p.add_argument("--out-dir", default=None)
    p.add_argument("--panel-csv", default=None)
    p.add_argument("--as-of", default=AS_OF)
    p.add_argument("--train-months", type=int, default=WF_TRAIN_MONTHS)
    p.add_argument("--test-months", type=int, default=WF_TEST_MONTHS)
    p.add_argument("--step-months", type=int, default=WF_STEP_MONTHS)
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ns = _parse_args(argv)
    report = run(out_dir=ns.out_dir, panel_csv=ns.panel_csv, as_of=ns.as_of,
                 train_months=ns.train_months, test_months=ns.test_months, step_months=ns.step_months,
                 verbose=not ns.quiet)
    return 0 if report.get("decision") in ALLOWED_DECISIONS else 1


if __name__ == "__main__":
    raise SystemExit(main())
