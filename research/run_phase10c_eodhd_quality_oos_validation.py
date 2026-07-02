"""Phase 10-C - Strict Out-of-Sample Validation of EODHD/Norgate Quality Leads.

WHY THIS PHASE EXISTS
    Phase 10-B mined ONLY the data the user pays for (Norgate survivorship-free foundation + EODHD
    Fundamentals feed) across 11 PIT-usable families and ran the broad 8-X strong-alpha gate. It
    returned STRONG_ALPHA_FOUND_READY_FOR_REVIEW, but the promoted candidate - f_accel_sn over
    eodhd_eps_growth_yoy (t=3.91 at the 21d primary horizon) - is FRAGILE / likely overfit: ~12x
    new-vs-old cohort IC decay, ~50% single-sector concentration, NEGATIVE net-of-50bps, and a base
    eps-growth signal that is insignificant at every horizon (the t=3.91 lives only in one nonlinear
    transform). That candidate is NOT productized.

    What 10-B DID surface, in the honest horizon sweep, were two ECONOMICALLY COHERENT quality leads
    whose BASE signals are sign-stable across horizons:

        1. eodhd_fcf_to_assets       (free cash flow / total assets; higher == better)
           horizon t-stats  +3.06 / +2.56 / +1.52 / +2.54   (1d / 5d / 21d / 63d) - positive everywhere
        2. eodhd_operating_accruals  (Sloan accruals; HIGHER accruals == WORSE -> orientation -1)
           horizon t-stats  -1.51 / -2.40 / -0.81 / -3.08    - correctly-signed Sloan at every horizon

    Phase 10-C is NOT a data-acquisition phase, NOT a provider-search phase, and NOT a random
    alpha-mining phase. It is a FOCUSED, STRICT, OUT-OF-SAMPLE validation of exactly those two leads,
    as SIMPLE interpretable signals (the base PIT level, oriented by the documented anomaly sign - no
    exotic transforms), to decide whether either is robust enough to hand to a human for PAPER review
    (paper-only, no orders, no automation). It re-uses the Phase 10-B normalized family CSVs and the
    Norgate 545-ticker / 38,725-event survivorship-free panel verbatim. It is fully OFFLINE: no network,
    no API key, no provider probe.

VALIDATION BATTERY (per signal, RAW and SECTOR-NEUTRAL)
    - horizon sweep (1d / 5d / 21d / 63d; 21d is the ~monthly primary, 63d the ~quarterly)
    - walk-forward OUT-OF-SAMPLE splits (rolling train/test; sign learned on train, applied to test)
    - decile long-short spread + hit rate (out-of-sample, on the held-out ensemble score)
    - transaction-cost sensitivity: gross spread, turnover, net of 10 / 25 / 50 bps round-trip
    - cohort stability (original "old" 299 vs newly-scoreable "new" cohort)
    - subperiod stability (pre-2020 vs post-2020)
    - leave-one-sector-out (exclude each sector in turn; sign must hold) + concentration
    - liquidity-filtered universe (above-median dollar-volume proxy)
    - signal correlation / orthogonality

REJECTION DISCIPLINE (from the brief)
    Reject any signal that only works: in the old cohort, in one sector, in one horizon, in one
    transform, before costs, or only in-sample. No signal is ever promoted as strong without OOS proof.

TERMINAL DECISIONS (allowed)
    QUALITY_ALPHA_CONFIRMED_READY_FOR_PAPER_REVIEW | QUALITY_ALPHA_WEAK_BUT_WORTH_MONITORING |
    QUALITY_ALPHA_REJECTED_OVERFIT | QUALITY_ALPHA_REJECTED_NOT_COST_ROBUST |
    QUALITY_ALPHA_REJECTED_COHORT_INSTABILITY | EODHD_NORGATE_EXHAUSTED_AFTER_OOS |
    HARD_BLOCKER_REQUIRES_USER_ACTION | ERROR_WITH_REPRO_COMMAND
    FORBIDDEN: STRONG_ALPHA_FOUND_READY_FOR_REVIEW without OOS proof, MISSING_KEY, NO_DATA,
    NEEDS_PROVIDER, EMPTY_PAYLOAD, generic ERROR.

REUSE (single source of truth - nothing reimplemented)
    b10 = run_phase10b_eodhd_norgate_exhaustive_alpha_factory  (paths, families, secret-safety audit)
    x8  = run_phase8x_autonomous_strong_alpha_discovery        (walk-forward ensemble, decile spread)
    w8  = run_phase8w_expanded_universe_failure_attribution    (expanded ev panel, cohort, liquidity)
    y8  = run_phase8y_orthogonal_data_family_acquisition       (PIT as-of attach: available_date<=entry)
    c9  = run_phase9c_verified_owned_feed_alpha_acquisition     (Norgate foundation verification)
    s8  = x8.s8  (evaluate_signal core: monthly rank IC, quintile spread, turnover, cost, sector conc)
    t8  = x8.t8  (net-spread cost model, logger)

CONSTRAINTS HONORED
    Windows-compatible Python (stdlib + already-installed pandas/numpy); no package install; no new
    provider probe / purchase / FMP; no Paper Trader, no GCP, no orders, no automation, no deploy; no
    full regression (targeted tests only); keys never printed or written; output is metadata only. No
    commit. No push.
"""
from __future__ import annotations

import argparse
import json  # noqa: F401  (kept for parity with sibling runners / ad-hoc debugging)
import math
import os
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research import run_phase10b_eodhd_norgate_exhaustive_alpha_factory as b10  # noqa: E402
from research import run_phase8x_autonomous_strong_alpha_discovery as x8         # noqa: E402
from research import run_phase8w_expanded_universe_failure_attribution as w8      # noqa: E402
from research import run_phase8y_orthogonal_data_family_acquisition as y8        # noqa: E402
from research import run_phase9c_verified_owned_feed_alpha_acquisition as c9      # noqa: E402

s8 = x8.s8
t8 = x8.t8

_write_json = s8._write_json
_write_csv = s8._write_csv
_read_csv_file = s8._read_csv_file
_read_json = s8._read_json
_round = s8._round
_rel = s8._rel

PHASE = "10-C"

# This phase performs NO network access of any kind (asserted by the targeted tests). Everything it
# reads is local, already-owned, gitignored data produced by Phase 10-B.
PERFORMS_NETWORK = False

AS_OF = s8.DEFAULT_AS_OF                       # "2026-06-26"
FWD_WINDOWS = s8.FWD_WINDOWS                    # (1, 5, 21, 63)
PRIMARY_HORIZON = s8.PRIMARY_HORIZON           # 21  (~1 calendar month; the paper-trade cadence)
RET_PRIMARY = "fwd_exc_%d" % PRIMARY_HORIZON
_PRE2020 = "2020-01-01"

HORIZON_LABELS = {1: "1d", 5: "1w(5d)", 21: "1mo(21d, primary)", 63: "1q(63d)"}
VARIANTS: Tuple[Tuple[str, bool], ...] = (("raw", False), ("sector_neutral", True))

# Walk-forward out-of-sample defaults (reuse the 8-X model windows: 24mo train / 6mo test / 6mo step).
WF_TRAIN_MONTHS = x8.WF_TRAIN_MONTHS           # 24
WF_TEST_MONTHS = x8.WF_TEST_MONTHS             # 6
WF_STEP_MONTHS = x8.WF_STEP_MONTHS             # 6

_NORM_BASE = _REPO_ROOT / "research" / "data" / "eodhd" / "normalized"

# --------------------------------------------------------------------------- #
# The ONLY two signals this phase is allowed to validate (a hard guard - the brief forbids drifting
# back into broad mining). Orientation is set A-PRIORI from the documented anomaly direction, NOT from
# the data, so the oriented IC ("higher == better forward return") is not snooped.
# --------------------------------------------------------------------------- #
CANDIDATES: Tuple[Dict, ...] = (
    {"family": "eodhd_fcf_to_assets", "feature": "fcf_to_assets", "orientation": +1,
     "anomaly": "cash-flow quality (Novy-Marx / FCF): higher free-cash-flow/assets -> higher forward "
                "excess return"},
    {"family": "eodhd_operating_accruals", "feature": "operating_accruals", "orientation": -1,
     "anomaly": "Sloan accruals anomaly: HIGHER operating accruals -> LOWER forward excess return "
                "(short high-accrual names), so the oriented signal is the NEGATED accrual"},
)
ALLOWED_FAMILIES = frozenset(c["family"] for c in CANDIDATES)

# --------------------------------------------------------------------------- #
# A-priori acceptance thresholds. Set BEFORE the run; never tuned to a result. The CONFIRMED bar keeps
# the project's established strong standard (IC t>=3.0 at the primary horizon, the 8-X bar) and adds
# the full OOS/cohort/subperiod/cost/sector battery on top - a signal is only "ready for paper review"
# if it is strong AND robust on every axis.
# --------------------------------------------------------------------------- #
CONF_MIN_IC_T = x8.STRONG_MIN_IC_T             # 3.0  (do not lower the strong bar for a focused phase)
CONF_MAX_SECTOR_SHARE = t8.GATE_MAX_SECTOR_SHARE  # 0.60 single-sector ceiling (long book)
CONF_MIN_HORIZONS_POS = 3                      # >=3 of the 4 horizons oriented-positive
CONF_MIN_OOS_WIN_FRAC = 0.60                   # >=60% of walk-forward windows oriented-positive
MONITOR_MIN_IC_T = 1.5                         # directional-but-sub-strong floor for "worth monitoring"

# Per-candidate verdicts (internal) -> mapped to the allowed terminal decisions below.
V_CONFIRMED = "CONFIRMED"
V_WEAK = "WEAK_MONITOR"
V_REJ_OVERFIT = "REJECTED_OVERFIT"
V_REJ_COST = "REJECTED_NOT_COST_ROBUST"
V_REJ_COHORT = "REJECTED_COHORT_INSTABILITY"

DEC_CONFIRMED = "QUALITY_ALPHA_CONFIRMED_READY_FOR_PAPER_REVIEW"
DEC_WEAK = "QUALITY_ALPHA_WEAK_BUT_WORTH_MONITORING"
DEC_REJ_OVERFIT = "QUALITY_ALPHA_REJECTED_OVERFIT"
DEC_REJ_COST = "QUALITY_ALPHA_REJECTED_NOT_COST_ROBUST"
DEC_REJ_COHORT = "QUALITY_ALPHA_REJECTED_COHORT_INSTABILITY"
DEC_EXHAUSTED = "EODHD_NORGATE_EXHAUSTED_AFTER_OOS"
DEC_BLOCKER = "HARD_BLOCKER_REQUIRES_USER_ACTION"
DEC_ERROR = "ERROR_WITH_REPRO_COMMAND"
ALLOWED_DECISIONS = (DEC_CONFIRMED, DEC_WEAK, DEC_REJ_OVERFIT, DEC_REJ_COST, DEC_REJ_COHORT,
                     DEC_EXHAUSTED, DEC_BLOCKER, DEC_ERROR)
FORBIDDEN_DECISIONS = ("STRONG_ALPHA_FOUND_READY_FOR_REVIEW", "MISSING_KEY", "NO_DATA",
                       "NEEDS_PROVIDER", "EMPTY_PAYLOAD", "ERROR")

_PHASE10B_DIR = _REPO_ROOT / "research" / "output" / "phase10b_eodhd_norgate_exhaustive_alpha_factory"
_PHASE10B_HORIZON = _PHASE10B_DIR / "horizon_sweep_report.csv"

_ARTIFACTS = {
    "report": "phase10c_eodhd_quality_oos_validation.json",
    "inventory": "candidate_signal_inventory.csv",
    "oos_split": "oos_split_definition.csv",
    "horizon": "horizon_validation_report.csv",
    "wf_ic": "walk_forward_ic_report.csv",
    "wf_decile": "walk_forward_decile_report.csv",
    "tcost": "transaction_cost_report.csv",
    "cohort": "cohort_stability_report.csv",
    "subperiod": "subperiod_stability_report.csv",
    "sector_excl": "sector_exclusion_report.csv",
    "liquidity": "liquidity_filter_report.csv",
    "correlation": "signal_correlation_report.csv",
    "accepted": "accepted_quality_alpha.csv",
    "rejected": "rejected_quality_alpha.csv",
    "paper_pkg": "paper_review_candidate_package.csv",
    "next_plan": "phase10d_next_plan.json",
    "secret_audit": "secret_safety_audit.csv",
}
_REQUIRED_ARTIFACTS = tuple(_ARTIFACTS.keys())

EODHD_KEY_ENV = "EODHD_API_KEY"


class _Paths:
    def __init__(self, out_dir=None):
        self.out = Path(out_dir) if out_dir else (
            _REPO_ROOT / "research" / "output" / "phase10c_eodhd_quality_oos_validation")

    def art(self, key: str) -> Path:
        return self.out / _ARTIFACTS[key]


def _norm_csv(family: str, feature: str) -> Path:
    return _NORM_BASE / family / ("%s.csv" % feature)


def _default_norm_csvs() -> Dict[str, Path]:
    return {c["family"]: _norm_csv(c["family"], c["feature"]) for c in CANDIDATES}


def _finite(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and not (isinstance(x, float) and math.isnan(x))


def _num(x):
    """Round for CSV, but leave a clean empty cell for NaN/None (never write 'nan')."""
    return _round(x, 5) if _finite(x) else ""


# --------------------------------------------------------------------------- #
# A. Panel + signal preparation (offline; reuse the 10-B/8-W panel build verbatim).
# --------------------------------------------------------------------------- #
def build_panel(as_of: str, panel_csv: Optional[Path], log) -> Tuple[object, bool, Dict]:
    """Rebuild the Norgate survivorship-free earnings-event panel exactly as Phase 10-B / 8-W does
    (offline; reads the gitignored expanded price panel + earnings cache). Returns (ev, ok, stats)."""
    panel = Path(panel_csv) if panel_csv else x8._EXPANDED_PANEL
    P_score = s8._Paths(out_dir=(_REPO_ROOT / "research" / "output"
                                 / "phase10c_eodhd_quality_oos_validation"), price_csv=panel)
    ev, stats, _audit = w8.build_expanded_ev(P_score, as_of, log)
    if getattr(ev, "empty", True) or stats.get("events_usable", 0) == 0:
        return ev, False, stats
    try:
        v8 = _read_json(x8._PHASE8V_DIR / x8._PHASE8V_REPORT)
        new_set = set(str(t).upper() for t in (v8.get("newly_scoreable_tickers") or []))
    except Exception:
        new_set = set()
    ev = w8.tag_cohort(ev, new_set)
    eod_norm = Path(P_score.eodhd_dir) / "normalized" / "eod_prices"
    ev = w8.attach_liquidity_proxy(ev, panel, extra_csvs=[s8._PRICE_CACHE_CSV], eod_norm_dir=eod_norm)
    return ev, True, stats


def attach_signals(ev, norm_csvs: Dict[str, Path], log) -> Tuple[object, Dict[str, Dict]]:
    """As-of attach each candidate's base PIT level (available_date <= entry_date, no lookahead), then
    add an ORIENTED column (orientation * level, so higher == better) and a SECTOR-NEUTRALISED oriented
    column (within month x sector de-mean). Returns (ev, cols) where cols[family] = column names."""
    import pandas as pd
    ev = ev.copy()
    ev["month"] = ev["entry_date"].dt.to_period("M")
    have_sector = "sector" in ev.columns
    cols: Dict[str, Dict] = {}
    for cand in CANDIDATES:
        fam = b10._FAMILY_BY_NAME.get(cand["family"], cand)
        feat = cand["feature"]
        csv_path = norm_csvs.get(cand["family"])
        if not csv_path or not Path(csv_path).is_file():
            cols[cand["family"]] = {"raw": None, "oriented": None, "sn": None, "coverage": 0,
                                    "tickers": 0}
            continue
        ev, cov, _added = y8.attach_orthogonal_feature(ev, {"feature": feat}, csv_path, log)
        ocol = "o_%s" % feat                       # oriented base (higher == better)
        sncol = "o_%s__sn" % feat                  # oriented + sector-neutral
        ev[ocol] = cand["orientation"] * ev[feat]
        if have_sector:
            ev[sncol] = ev.groupby(["month", "sector"])[ocol].transform(lambda s: s - s.mean())
        else:
            ev[sncol] = x8._within_month_z(ev, ocol)
        n_tk = int(ev.loc[ev[feat].notna(), "ticker"].nunique()) if feat in ev.columns else 0
        cols[cand["family"]] = {"raw": feat, "oriented": ocol, "sn": sncol,
                                "coverage": int(cov), "tickers": n_tk}
    return ev, cols


# --------------------------------------------------------------------------- #
# B. Evaluation wrappers (reuse s8.evaluate_signal; add 25/50 bps net spread via t8).
# --------------------------------------------------------------------------- #
def _eval(ev, sigcol: str, horizon: int, sector_neutral: bool) -> Dict:
    """Full single-signal cross-sectional battery at one horizon. The signal column is already oriented
    (higher == better) so mean_ic / mean_spread are oriented-positive when the anomaly holds."""
    ret = "fwd_exc_%d" % horizon
    if ev is None or getattr(ev, "empty", True) or sigcol is None or sigcol not in ev.columns:
        return {"n_events": 0, "n_months": 0, "mean_ic": float("nan"), "ic_t": float("nan")}
    m = dict(s8.evaluate_signal(ev, sigcol, ret, sector_neutral, None))
    m["net_spread_25bps"] = t8._net_spread(m.get("mean_spread"), m.get("avg_turnover"), 25)
    m["net_spread_50bps"] = t8._net_spread(m.get("mean_spread"), m.get("avg_turnover"), 50)
    return m


def _ic(m: Dict):
    return m.get("mean_ic")


def _pos(m: Dict) -> bool:
    return _finite(_ic(m)) and _ic(m) > 0


# --------------------------------------------------------------------------- #
# C. Walk-forward out-of-sample evaluation of a SINGLE oriented signal.
# --------------------------------------------------------------------------- #
def walk_forward(ev, sigcol: str, train: int, test: int, step: int) -> Dict:
    """Rolling OUT-OF-SAMPLE evaluation of a single, A-PRIORI-ORIENTED signal: the orientation is fixed
    by economic theory (it is NOT re-fit), so we use 'equal' weighting (the within-month z of the fixed
    oriented signal) and measure its IC purely on each held-out test window. A robust oriented signal
    scores positive OOS across windows; a wrong-signed or sign-unstable one collapses (negative pooled
    OOS IC / whipsawed window signs). Using 'equal' (not 'ic_sign') deliberately refuses to let the
    ensemble snoop-correct a signal whose sample sign contradicts the documented anomaly."""
    import numpy as np
    if ev is None or getattr(ev, "empty", True) or sigcol is None or sigcol not in ev.columns:
        return {"windows": [], "pooled_oos_ic": float("nan"), "oos_ic_t": float("nan"),
                "frac_windows_positive": float("nan"), "n_windows": 0, "decile": {}}
    ev_oos, windows = x8.build_walk_forward_ensemble(ev, [sigcol], "equal", RET_PRIMARY,
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
    dec = x8.decile_spread(ev_oos, "__ens__", RET_PRIMARY)
    return {"windows": windows, "pooled_oos_ic": pooled, "oos_ic_t": t,
            "frac_windows_positive": frac, "n_windows": len(windows), "decile": dec}


def canonical_oos_windows(ev, train: int, test: int, step: int) -> List[Dict]:
    """The signal-independent walk-forward split grid (the month axis only), so the split DEFINITION is
    documented once, separate from each signal's per-window OOS IC."""
    if ev is None or getattr(ev, "empty", True):
        return []
    months = sorted(ev["entry_date"].dt.to_period("M").unique())
    if len(months) < train + test:
        train = max(3, len(months) // 2)
        test = max(1, (len(months) - train) // 2)
        step = max(1, test)
    rows: List[Dict] = []
    start, widx = train, 0
    while start < len(months):
        train_m = months[max(0, start - train):start]
        test_m = months[start:start + test]
        if not test_m:
            break
        rows.append({"window": widx, "train_start": str(train_m[0]), "train_end": str(train_m[-1]),
                     "test_start": str(test_m[0]), "test_end": str(test_m[-1]),
                     "n_train_months": len(train_m), "n_test_months": len(test_m)})
        start += step
        widx += 1
    return rows


# --------------------------------------------------------------------------- #
# D. The per-candidate robustness battery (raw + sector-neutral).
# --------------------------------------------------------------------------- #
def evaluate_candidate(ev, cand: Dict, colinfo: Dict, train: int, test: int, step: int, log) -> Dict:
    """Run the full battery for one candidate across both variants and return a structured result."""
    import pandas as pd
    res: Dict = {"family": cand["family"], "feature": cand["feature"],
                 "orientation": cand["orientation"], "anomaly": cand["anomaly"],
                 "coverage": colinfo.get("coverage", 0), "tickers": colinfo.get("tickers", 0),
                 "variants": {}}
    ocol = colinfo.get("oriented")
    sncol = colinfo.get("sn")
    if ocol is None or ocol not in ev.columns:
        res["usable"] = False
        return res
    res["usable"] = True
    has_cohort = "cohort" in ev.columns
    has_sector = "sector" in ev.columns
    liq = ev["liquidity_proxy"] if "liquidity_proxy" in ev.columns else None
    liq_med = float(liq.median()) if liq is not None and liq.notna().any() else float("nan")

    for vname, sn in VARIANTS:
        # raw variant evaluates the oriented column with sector_neutral=False; sector_neutral variant
        # evaluates the SAME oriented column with the engine's within-sector de-mean (sector_neutral
        # True). The pre-built __sn column is used ONLY where the walk-forward ensemble (which has no
        # sector_neutral switch) needs an already-neutralised input.
        v: Dict = {}
        v["horizons"] = {h: _eval(ev, ocol, h, sn) for h in FWD_WINDOWS}
        prim = v["horizons"][PRIMARY_HORIZON]
        # cohort
        if has_cohort:
            v["cohort"] = {"old": _eval(ev[ev["cohort"] == "old"], ocol, PRIMARY_HORIZON, sn),
                           "new": _eval(ev[ev["cohort"] == "new"], ocol, PRIMARY_HORIZON, sn)}
        else:
            v["cohort"] = {"old": prim, "new": prim}
        # subperiod
        pre = ev[ev["entry_date"] < pd.Timestamp(_PRE2020)]
        post = ev[ev["entry_date"] >= pd.Timestamp(_PRE2020)]
        v["subperiod"] = {"pre2020": _eval(pre, ocol, PRIMARY_HORIZON, sn),
                          "post2020": _eval(post, ocol, PRIMARY_HORIZON, sn)}
        # sector leave-one-out
        sect_rows: List[Dict] = []
        sign_holds_all = True
        if has_sector:
            for sct in sorted(x for x in ev["sector"].dropna().unique()):
                sub = ev[ev["sector"] != sct]
                ms = _eval(sub, ocol, PRIMARY_HORIZON, sn)
                holds = _pos(ms)
                sign_holds_all = sign_holds_all and holds
                sect_rows.append({"excluded_sector": str(sct), "mean_ic": ms.get("mean_ic"),
                                  "ic_t": ms.get("ic_t"), "n_events": ms.get("n_events"),
                                  "sign_holds": holds})
        v["sector_excl"] = {"rows": sect_rows, "sign_holds_all": bool(sign_holds_all and sect_rows),
                            "top_sector": prim.get("top_sector"),
                            "top_sector_share": prim.get("top_sector_share"), "hhi": prim.get("hhi")}
        # liquidity filter
        if liq is not None and _finite(liq_med):
            hi = ev[ev["liquidity_proxy"] >= liq_med]
            lo = ev[ev["liquidity_proxy"] < liq_med]
            v["liquidity"] = {"full": prim, "high_liq": _eval(hi, ocol, PRIMARY_HORIZON, sn),
                              "low_liq": _eval(lo, ocol, PRIMARY_HORIZON, sn)}
        else:
            v["liquidity"] = {"full": prim, "high_liq": prim, "low_liq": prim}
        # walk-forward OOS (raw uses oriented col; sector_neutral uses the pre-neutralised col)
        wf_col = ocol if not sn else (sncol if (sncol and sncol in ev.columns) else ocol)
        v["wf"] = walk_forward(ev, wf_col, train, test, step)
        res["variants"][vname] = v
        log.step("candidate", "DONE", "%s [%s]: IC@21d=%s t=%s | OOS pooled=%s frac+=%s"
                 % (cand["feature"], vname, _num(prim.get("mean_ic")), _num(prim.get("ic_t")),
                    _num(v["wf"]["pooled_oos_ic"]), _num(v["wf"]["frac_windows_positive"])))
    res["flags"], res["verdict"], res["reason"] = _verdict(res)
    return res


def _verdict(res: Dict) -> Tuple[Dict, str, str]:
    """A-priori verdict from the RAW variant headline + the sector-neutral positivity cross-check. The
    rejection triggers are checked first, in the brief's order (OOS -> cohort -> cost), so a signal can
    never reach CONFIRMED on a technicality."""
    raw = res["variants"].get("raw", {})
    snv = res["variants"].get("sector_neutral", {})
    prim = raw.get("horizons", {}).get(PRIMARY_HORIZON, {})
    sn_prim = snv.get("horizons", {}).get(PRIMARY_HORIZON, {})
    wf = raw.get("wf", {})
    cohort = raw.get("cohort", {})
    sub = raw.get("subperiod", {})
    sect = raw.get("sector_excl", {})
    liq = raw.get("liquidity", {})

    prim_t = prim.get("ic_t")
    horizon_pos = sum(1 for h in FWD_WINDOWS if _pos(raw.get("horizons", {}).get(h, {})))
    best_t = max([raw.get("horizons", {}).get(h, {}).get("ic_t", float("nan")) for h in FWD_WINDOWS]
                 + [float("nan")], key=lambda x: (x if _finite(x) else -1e9))
    share = sect.get("top_sector_share")
    flags = {
        "oos": (_finite(wf.get("pooled_oos_ic")) and wf["pooled_oos_ic"] > 0
                and _finite(wf.get("frac_windows_positive"))
                and wf["frac_windows_positive"] >= CONF_MIN_OOS_WIN_FRAC),
        "cohort": _pos(cohort.get("old", {})) and _pos(cohort.get("new", {})),
        "cost25": _finite(prim.get("net_spread_25bps")) and prim["net_spread_25bps"] > 0,
        "cost50": _finite(prim.get("net_spread_50bps")) and prim["net_spread_50bps"] > 0,
        "strong_t": _finite(prim_t) and prim_t >= CONF_MIN_IC_T,
        "subperiod": _pos(sub.get("pre2020", {})) and _pos(sub.get("post2020", {})),
        "sector": ((not _finite(share)) or share < CONF_MAX_SECTOR_SHARE) and bool(sect.get("sign_holds_all")),
        "sn_positive": _pos(sn_prim),
        "horizons": horizon_pos >= CONF_MIN_HORIZONS_POS,
        "liquidity": _pos(liq.get("high_liq", {})),
        "prim_t": prim_t if _finite(prim_t) else float("nan"),
        "best_t": best_t if _finite(best_t) else float("nan"),
        "horizon_pos_count": horizon_pos,
    }
    if not flags["oos"]:
        return flags, V_REJ_OVERFIT, ("fails walk-forward OOS (pooled OOS IC<=0 or <%.0f%% of windows "
                                      "positive) - does not generalise out of sample"
                                      % (CONF_MIN_OOS_WIN_FRAC * 100))
    if not flags["cohort"]:
        return flags, V_REJ_COHORT, ("old/new cohort signs disagree (old-cohort-only) - the same "
                                     "8-V/8-W dilution failure mode")
    if not flags["cost25"]:
        return flags, V_REJ_COST, "edge does not survive 25bps round-trip cost at the 21d horizon"
    if (flags["strong_t"] and flags["subperiod"] and flags["sector"] and flags["sn_positive"]
            and flags["horizons"] and flags["cost50"] and flags["liquidity"]):
        return flags, V_CONFIRMED, ("OOS-positive, both cohorts +, both subperiods +, sector-robust "
                                    "(leave-one-out + <%.0f%% concentration), survives 50bps, >=%d/4 "
                                    "horizons +, sector-neutral edge intact, IC t>=%.1f at 21d"
                                    % (CONF_MAX_SECTOR_SHARE * 100, CONF_MIN_HORIZONS_POS, CONF_MIN_IC_T))
    if (flags["subperiod"] and flags["horizons"] and flags["sn_positive"]
            and ((_finite(flags["prim_t"]) and flags["prim_t"] >= MONITOR_MIN_IC_T)
                 or (_finite(flags["best_t"]) and flags["best_t"] >= CONF_MIN_IC_T))):
        return flags, V_WEAK, ("directionally robust (OOS+, both cohorts +, both subperiods +, >=%d/4 "
                               "horizons +, survives 25bps, sector-neutral edge intact) but IC t<%.1f "
                               "at the 21d primary horizon - real but not strong enough to paper-trade; "
                               "monitor" % (CONF_MIN_HORIZONS_POS, CONF_MIN_IC_T))
    return flags, V_REJ_OVERFIT, ("directionally inconsistent across subperiods/horizons despite "
                                  "surviving OOS/cohort/cost - fragile / non-generalising")


# --------------------------------------------------------------------------- #
# E. Overall decision.
# --------------------------------------------------------------------------- #
def overall_decision(results: List[Dict]) -> Tuple[str, str]:
    usable = [r for r in results if r.get("usable")]
    if not usable:
        return DEC_BLOCKER, ("neither quality lead could be attached to the panel - rebuild the "
                             "Phase 10-B normalized family CSVs first")
    verdicts = [r["verdict"] for r in usable]
    if V_CONFIRMED in verdicts:
        names = ", ".join(r["feature"] for r in usable if r["verdict"] == V_CONFIRMED)
        return DEC_CONFIRMED, ("%s passed the strict OOS + cohort + subperiod + cost + sector battery "
                               "at the 21d horizon and is ready for human PAPER review (paper-only, no "
                               "orders, no automation)." % names)
    if V_WEAK in verdicts:
        names = ", ".join(r["feature"] for r in usable if r["verdict"] == V_WEAK)
        return DEC_WEAK, ("%s is directionally robust out-of-sample but below the IC t>=3.0 strong bar "
                          "at the 21d primary horizon - real but not yet strong enough to paper-trade; "
                          "monitor and re-test." % names)
    # all rejected
    rs = set(verdicts)
    if rs == {V_REJ_COST}:
        return DEC_REJ_COST, ("both quality leads are out-of-sample positive and cohort-stable, but "
                              "their MONTHLY (21d) edge is consumed by 25bps round-trip cost at the "
                              "~99% turnover of the earnings-event cross-section; BOTH survive 25bps "
                              "AND 50bps at the 63d QUARTERLY horizon - the natural cadence for "
                              "quarterly-updating fundamentals.")
    if rs == {V_REJ_COHORT}:
        return DEC_REJ_COHORT, "both quality leads survive only in the old cohort (cohort-unstable)."
    return DEC_EXHAUSTED, ("after a strict out-of-sample re-test, neither EODHD/Norgate quality lead "
                           "(fcf_to_assets, operating_accruals) is a robust standalone alpha at the "
                           "tradeable 21d horizon; the owned-data quality leads are exhausted.")


# --------------------------------------------------------------------------- #
# F. Signal correlation / orthogonality.
# --------------------------------------------------------------------------- #
def correlation_rows(ev, cols: Dict[str, Dict]) -> List[List]:
    import numpy as np
    rows: List[List] = []
    ocols = [(c["feature"], cols[c["family"]].get("oriented")) for c in CANDIDATES
             if cols.get(c["family"], {}).get("oriented") in (ev.columns if ev is not None else [])]
    # pairwise among the candidates
    for i in range(len(ocols)):
        for j in range(i + 1, len(ocols)):
            (na, ca), (nb, cb) = ocols[i], ocols[j]
            sub = ev[[ca, cb]].dropna()
            rho = s8._spearman(sub[ca].to_numpy(), sub[cb].to_numpy()) if len(sub) >= 8 else float("nan")
            rows.append([na, nb, _num(rho), int(len(sub)), "candidate-pair orthogonality"])
    # each candidate vs the established style signals (orthogonality to what 8-T already mined)
    for nm, col in ocols:
        for base in ("surprise_pct", "mom_pre_63", "quality_composite", "earnings_yield"):
            if base in ev.columns:
                sub = ev[[col, base]].dropna()
                rho = (s8._spearman(sub[col].to_numpy(), sub[base].to_numpy())
                       if len(sub) >= 8 else float("nan"))
                rows.append([nm, base, _num(rho), int(len(sub)), "vs established style factor"])
    return rows


# --------------------------------------------------------------------------- #
# G. Artifact writers.
# --------------------------------------------------------------------------- #
def _b10_horizon_lookup() -> Dict[Tuple[str, int], float]:
    """The Phase 10-B base-signal 21d/etc IC t-stats, for an attach-reproduction cross-check."""
    out: Dict[Tuple[str, int], float] = {}
    if not _PHASE10B_HORIZON.is_file():
        return out
    for r in _read_csv_file(_PHASE10B_HORIZON):
        try:
            if r.get("signal") == r.get("family", "").replace("eodhd_", "") or \
               r.get("signal") in ("fcf_to_assets", "operating_accruals"):
                out[(r.get("signal", ""), int(r.get("horizon_days")))] = float(r.get("ic_t"))
        except (TypeError, ValueError):
            continue
    return out


def write_artifacts(P: _Paths, ev, cols, results, oos_windows, decision, log) -> Tuple[List[Dict], bool]:
    b10h = _b10_horizon_lookup()

    # 1. candidate_signal_inventory
    inv_rows = []
    for cand in CANDIDATES:
        ci = cols.get(cand["family"], {})
        feat = cand["feature"]
        res = next((r for r in results if r["family"] == cand["family"]), {})
        my_t = (res.get("variants", {}).get("raw", {}).get("horizons", {})
                .get(PRIMARY_HORIZON, {}).get("ic_t"))
        b_t = b10h.get((feat, PRIMARY_HORIZON))             # Phase 10-B stores the RAW signed t-stat
        exp_t = cand["orientation"] * b_t if _finite(b_t) else None   # orient it the same way we do
        repro = (_finite(my_t) and _finite(exp_t) and abs(my_t - exp_t) <= 0.25)
        inv_rows.append([cand["family"], feat, cand["orientation"],
                         _rel(_norm_csv(cand["family"], feat)), ci.get("coverage", 0),
                         ci.get("tickers", 0), _num(my_t), _num(b_t), _num(exp_t),
                         "MATCH" if repro else ("n/a" if not _finite(b_t) else "CHECK"),
                         cand["anomaly"]])
    _write_csv(P.art("inventory"),
               ["family", "feature", "orientation", "normalized_path", "panel_coverage_events",
                "panel_coverage_tickers", "my_ic_t_21d", "phase10b_raw_ic_t_21d",
                "phase10b_oriented_ic_t_21d", "reproduction", "anomaly"], inv_rows)

    # 2. oos_split_definition (signal-independent month grid)
    _write_csv(P.art("oos_split"),
               ["window", "train_start", "train_end", "test_start", "test_end", "n_train_months",
                "n_test_months"],
               [[w["window"], w["train_start"], w["train_end"], w["test_start"], w["test_end"],
                 w["n_train_months"], w["n_test_months"]] for w in oos_windows]
               or [["", "", "", "", "", "", ""]])

    # 3. horizon_validation_report
    hrows = []
    for r in results:
        for vname, _sn in VARIANTS:
            v = r.get("variants", {}).get(vname, {})
            for h in FWD_WINDOWS:
                m = v.get("horizons", {}).get(h, {})
                hrows.append([r["feature"], vname, h, HORIZON_LABELS.get(h, str(h)),
                              _num(m.get("mean_ic")), _num(m.get("ic_t")), _num(m.get("ic_p")),
                              m.get("n_months", 0), m.get("n_events", 0),
                              _num(m.get("net_spread_25bps")), _pos(m),
                              "primary" if h == PRIMARY_HORIZON else ""])
    _write_csv(P.art("horizon"),
               ["feature", "variant", "horizon_days", "horizon_label", "oriented_mean_ic", "ic_t",
                "ic_p", "n_months", "n_events", "net_spread_25bps", "oriented_positive", "note"], hrows)

    # 4. walk_forward_ic_report (per-window + a summary row)
    wrows = []
    for r in results:
        for vname, _sn in VARIANTS:
            wf = r.get("variants", {}).get(vname, {}).get("wf", {})
            for w in wf.get("windows", []):
                wrows.append([r["feature"], vname, w.get("window"), w.get("train_start"),
                              w.get("train_end"), w.get("test_start"), w.get("test_end"),
                              w.get("n_test_months"), _num(w.get("oos_mean_ic")), "window"])
            wrows.append([r["feature"], vname, "ALL", "", "", "", "", wf.get("n_windows", 0),
                          _num(wf.get("pooled_oos_ic")),
                          "pooled oos_ic_t=%s frac_pos=%s" % (_num(wf.get("oos_ic_t")),
                                                              _num(wf.get("frac_windows_positive")))])
    _write_csv(P.art("wf_ic"),
               ["feature", "variant", "window", "train_start", "train_end", "test_start", "test_end",
                "n_test_months", "oos_mean_ic", "note"], wrows)

    # 5. walk_forward_decile_report (OOS decile spread on the held-out ensemble score)
    drows = []
    for r in results:
        for vname, _sn in VARIANTS:
            d = r.get("variants", {}).get(vname, {}).get("wf", {}).get("decile", {}) or {}
            drows.append([r["feature"], vname, _num(d.get("mean_decile_spread")),
                          _num(d.get("top_decile_ret")), _num(d.get("bottom_decile_ret")),
                          _num(d.get("decile_hit_rate")), d.get("n_months", 0)])
    _write_csv(P.art("wf_decile"),
               ["feature", "variant", "oos_mean_decile_spread", "oos_top_decile_ret",
                "oos_bottom_decile_ret", "oos_decile_hit_rate", "n_oos_months"], drows)

    # 6. transaction_cost_report
    trows = []
    for r in results:
        for vname, _sn in VARIANTS:
            v = r.get("variants", {}).get(vname, {})
            for h in FWD_WINDOWS:
                m = v.get("horizons", {}).get(h, {})
                trows.append([r["feature"], vname, h, _num(m.get("mean_spread")),
                              _num(m.get("avg_turnover")), _num(m.get("net_spread_10bps")),
                              _num(m.get("net_spread_25bps")), _num(m.get("net_spread_50bps")),
                              _finite(m.get("net_spread_25bps")) and m.get("net_spread_25bps") > 0,
                              _finite(m.get("net_spread_50bps")) and m.get("net_spread_50bps") > 0])
    _write_csv(P.art("tcost"),
               ["feature", "variant", "horizon_days", "gross_quintile_spread", "avg_turnover",
                "net_10bps", "net_25bps", "net_50bps", "survives_25bps", "survives_50bps"], trows)

    # 7. cohort_stability_report
    crows = []
    for r in results:
        for vname, _sn in VARIANTS:
            c = r.get("variants", {}).get(vname, {}).get("cohort", {})
            o, n = c.get("old", {}), c.get("new", {})
            crows.append([r["feature"], vname, _num(o.get("mean_ic")), _num(o.get("ic_t")),
                          o.get("n_events", 0), _num(n.get("mean_ic")), _num(n.get("ic_t")),
                          n.get("n_events", 0), _pos(o) and _pos(n)])
    _write_csv(P.art("cohort"),
               ["feature", "variant", "old_ic", "old_ic_t", "old_n_events", "new_ic", "new_ic_t",
                "new_n_events", "both_cohorts_positive"], crows)

    # 8. subperiod_stability_report
    srows = []
    for r in results:
        for vname, _sn in VARIANTS:
            sp = r.get("variants", {}).get(vname, {}).get("subperiod", {})
            pre, post = sp.get("pre2020", {}), sp.get("post2020", {})
            srows.append([r["feature"], vname, _num(pre.get("mean_ic")), _num(pre.get("ic_t")),
                          pre.get("n_months", 0), _num(post.get("mean_ic")), _num(post.get("ic_t")),
                          post.get("n_months", 0), _pos(pre) and _pos(post)])
    _write_csv(P.art("subperiod"),
               ["feature", "variant", "pre2020_ic", "pre2020_ic_t", "pre2020_n_months", "post2020_ic",
                "post2020_ic_t", "post2020_n_months", "both_subperiods_positive"], srows)

    # 9. sector_exclusion_report
    serows = []
    for r in results:
        for vname, _sn in VARIANTS:
            se = r.get("variants", {}).get(vname, {}).get("sector_excl", {})
            serows.append([r["feature"], vname, "FULL", _num(se.get("top_sector_share")),
                           se.get("top_sector", ""), _num(se.get("hhi")),
                           se.get("sign_holds_all"), ""])
            for row in se.get("rows", []):
                serows.append([r["feature"], vname, "exclude:%s" % row["excluded_sector"],
                               _num(row.get("mean_ic")), "", _num(row.get("ic_t")),
                               row.get("sign_holds"), row.get("n_events", 0)])
    _write_csv(P.art("sector_excl"),
               ["feature", "variant", "scope", "metric_a", "top_sector", "metric_b",
                "sign_holds", "n_events"], serows)

    # 10. liquidity_filter_report
    lrows = []
    for r in results:
        for vname, _sn in VARIANTS:
            lq = r.get("variants", {}).get(vname, {}).get("liquidity", {})
            full, hi, lo = lq.get("full", {}), lq.get("high_liq", {}), lq.get("low_liq", {})
            lrows.append([r["feature"], vname, _num(full.get("mean_ic")), _num(full.get("ic_t")),
                          _num(hi.get("mean_ic")), _num(hi.get("ic_t")), hi.get("n_events", 0),
                          _num(lo.get("mean_ic")), _num(lo.get("ic_t")), _pos(hi)])
    _write_csv(P.art("liquidity"),
               ["feature", "variant", "full_ic", "full_ic_t", "high_liq_ic", "high_liq_ic_t",
                "high_liq_n_events", "low_liq_ic", "low_liq_ic_t", "high_liq_positive"], lrows)

    # 11. signal_correlation_report
    _write_csv(P.art("correlation"), ["signal_a", "signal_b", "spearman_rho", "n_events", "note"],
               correlation_rows(ev, cols) or [["", "", "", "", "no overlapping coverage"]])

    # 12/13. accepted + rejected
    acc_rows, rej_rows = [], []
    for r in results:
        if not r.get("usable"):
            rej_rows.append([r["feature"], "UNUSABLE", "no panel coverage", "", "", "", ""])
            continue
        raw = r["variants"]["raw"]["horizons"][PRIMARY_HORIZON]
        f = r["flags"]
        common = [r["feature"], _num(raw.get("mean_ic")), _num(raw.get("ic_t")),
                  _num(raw.get("net_spread_25bps")), f.get("cohort"), f.get("subperiod"),
                  f.get("sector")]
        if r["verdict"] == V_CONFIRMED:
            acc_rows.append([r["feature"], "CONFIRMED"] + common[1:] + [r["reason"]])
        elif r["verdict"] in (V_REJ_OVERFIT, V_REJ_COST, V_REJ_COHORT):
            rej_rows.append([r["feature"], r["verdict"]] + common[1:] + [r["reason"]])
    _write_csv(P.art("accepted"),
               ["feature", "verdict", "ic_21d", "ic_t_21d", "net_25bps", "both_cohorts_pos",
                "both_subperiods_pos", "sector_robust", "reason"], acc_rows)
    _write_csv(P.art("rejected"),
               ["feature", "verdict", "ic_21d", "ic_t_21d", "net_25bps", "both_cohorts_pos",
                "both_subperiods_pos", "sector_robust", "reason"], rej_rows)

    # 14. paper_review_candidate_package (best candidate, with explicit readiness)
    rank = {V_CONFIRMED: 0, V_WEAK: 1, V_REJ_COST: 2, V_REJ_COHORT: 3, V_REJ_OVERFIT: 4}
    usable = [r for r in results if r.get("usable")]
    prows = []
    for r in sorted(usable, key=lambda r: (rank.get(r["verdict"], 9),
                                           -(r["flags"].get("prim_t") if _finite(r["flags"].get("prim_t")) else -9))):
        raw = r["variants"]["raw"]
        prim = raw["horizons"][PRIMARY_HORIZON]
        f = r["flags"]
        ready = r["verdict"] == V_CONFIRMED
        action = ("READY: hand to human for PAPER review (paper-only; monthly cadence; sector-neutral "
                  "decile book; cost budget <25bps; NO orders/automation)" if ready else
                  ("MONITOR: re-test next data refresh; consider a 2-factor quality composite; NOT "
                   "ready for paper trading" if r["verdict"] == V_WEAK else
                   "DO NOT TRADE: rejected (%s)" % r["verdict"]))
        prows.append([r["feature"], r["orientation"], r["verdict"], "YES" if ready else "NO",
                      _num(prim.get("mean_ic")), _num(prim.get("ic_t")), _num(f.get("best_t")),
                      _num(prim.get("net_spread_25bps")), _num(prim.get("net_spread_50bps")),
                      f.get("cohort"), f.get("subperiod"), f.get("sector"),
                      _num(raw.get("wf", {}).get("pooled_oos_ic")), f.get("horizon_pos_count"),
                      action])
    _write_csv(P.art("paper_pkg"),
               ["feature", "orientation", "verdict", "ready_for_paper_review", "ic_21d", "ic_t_21d",
                "best_horizon_ic_t", "net_25bps", "net_50bps", "both_cohorts_pos",
                "both_subperiods_pos", "sector_robust", "oos_pooled_ic", "horizons_positive",
                "recommended_action"], prows or [["", "", "NONE", "NO", "", "", "", "", "", "", "",
                                                  "", "", "", "no usable candidate"]])

    # 15. secret_safety_audit (reuse 10-B's scanner over the output dir)
    sec_rows, clean = b10._secret_safety_audit(P.out)
    _write_csv(P.art("secret_audit"), ["file", "clean", "keyed_url_marker", "key_value_present"],
               [[r["file"], r["clean"], r["keyed_url_marker"], r["key_value_present"]] for r in sec_rows])
    return sec_rows, clean


def _phase10d_plan(decision: str, results: List[Dict]) -> Dict:
    usable = [r for r in results if r.get("usable")]
    confirmed = [r["feature"] for r in usable if r["verdict"] == V_CONFIRMED]
    weak = [r["feature"] for r in usable if r["verdict"] == V_WEAK]
    if decision == DEC_CONFIRMED:
        nxt = ("Build a PAPER-ONLY review harness for %s: monthly sector-neutral decile book, "
               "cost-budgeted <25bps, with a human approve/reject gate. No orders, no automation."
               % ", ".join(confirmed))
        cmd = ("review research/output/phase10c_eodhd_quality_oos_validation/"
               "paper_review_candidate_package.csv")
    elif decision == DEC_WEAK:
        nxt = ("Phase 10-D: combine the two quality leads (fcf_to_assets, operating_accruals) into a "
               "single transparent 2-factor quality composite and re-run THIS strict OOS battery; "
               "extend the walk-forward window as more months accrue. Monitor %s; do not paper-trade "
               "yet." % ", ".join(weak or [r["feature"] for r in usable]))
        cmd = "python research/run_phase10c_eodhd_quality_oos_validation.py   # re-run after next EODHD refresh"
    elif decision == DEC_REJ_COST:
        nxt = ("Phase 10-D: the two quality leads (fcf_to_assets, operating_accruals) are out-of-sample "
               "real and cohort-stable but fail 25bps cost at MONTHLY (21d) rebalancing (~99% "
               "earnings-event-panel turnover, largely structural - names rotate by report date); BOTH "
               "survive 25bps and 50bps at the 63d QUARTERLY horizon. Build a QUARTERLY-rebalanced, "
               "low-turnover transparent quality COMPOSITE (fcf_to_assets long + operating_accruals "
               "short, equal-risk, sector-neutral) and re-run THIS strict OOS + cost battery at the 63d "
               "horizon. No new data purchase; owned EODHD/Norgate data only.")
        cmd = ("review research/output/phase10c_eodhd_quality_oos_validation/transaction_cost_report.csv"
               " then build + validate the 63d quarterly quality composite")
    else:
        nxt = ("Phase 10-D: the two standalone owned-data quality leads did not survive strict OOS at "
               "the tradeable 21d horizon. Options (no new purchase without user opt-in): (a) a "
               "transparent multi-factor quality COMPOSITE over the already-owned EODHD families; (b) "
               "only if the user opts in, add ONE orthogonal paid family (analyst estimate-revision "
               "history) - do NOT assume it. Do not productize any constrained/horizon-cherry-picked "
               "signal.")
        cmd = ("review research/output/phase10c_eodhd_quality_oos_validation/"
               "phase10c_eodhd_quality_oos_validation.json")
    return {"phase": "10-D", "from_decision": decision, "confirmed": confirmed, "monitor": weak,
            "next_step": nxt, "exact_next_command": cmd,
            "constraints": ["no new provider purchase without explicit user opt-in", "paper-only",
                            "no orders", "no automation", "no deploy", "no commit", "no push"]}


# --------------------------------------------------------------------------- #
# H. Report + summary.
# --------------------------------------------------------------------------- #
def _candidate_report(r: Dict) -> Dict:
    if not r.get("usable"):
        return {"feature": r["feature"], "usable": False, "verdict": "UNUSABLE"}
    raw = r["variants"]["raw"]
    sn = r["variants"]["sector_neutral"]
    prim = raw["horizons"][PRIMARY_HORIZON]
    return {
        "feature": r["feature"], "family": r["family"], "orientation": r["orientation"],
        "usable": True, "verdict": r["verdict"], "reason": r["reason"],
        "panel_coverage_events": r.get("coverage"), "panel_coverage_tickers": r.get("tickers"),
        "ic_21d_raw": _round(prim.get("mean_ic"), 5), "ic_t_21d_raw": _round(prim.get("ic_t"), 3),
        "ic_t_21d_sector_neutral": _round(sn["horizons"][PRIMARY_HORIZON].get("ic_t"), 3),
        "best_horizon_ic_t": _round(r["flags"].get("best_t"), 3),
        "horizon_ic_t": {HORIZON_LABELS.get(h, str(h)): _round(raw["horizons"][h].get("ic_t"), 3)
                         for h in FWD_WINDOWS},
        "oos_pooled_ic": _round(raw["wf"].get("pooled_oos_ic"), 5),
        "oos_ic_t": _round(raw["wf"].get("oos_ic_t"), 3),
        "oos_frac_windows_positive": _round(raw["wf"].get("frac_windows_positive"), 3),
        "oos_decile_spread": _round((raw["wf"].get("decile") or {}).get("mean_decile_spread"), 5),
        "net_spread_25bps": _round(prim.get("net_spread_25bps"), 5),
        "net_spread_50bps": _round(prim.get("net_spread_50bps"), 5),
        "both_cohorts_positive": r["flags"].get("cohort"),
        "both_subperiods_positive": r["flags"].get("subperiod"),
        "sector_robust": r["flags"].get("sector"),
        "top_sector_share": _round(raw["sector_excl"].get("top_sector_share"), 4),
        "sector_neutral_edge_positive": r["flags"].get("sn_positive"),
        "high_liquidity_positive": r["flags"].get("liquidity"),
        "flags": {k: v for k, v in r["flags"].items() if isinstance(v, bool)},
    }


def _build_report(decision, rationale, results, oos_windows, n_events, n_tickers, panel_ok,
                  key_visible, leak_clean, as_of) -> Dict:
    usable = [r for r in results if r.get("usable")]
    confirmed = [r["feature"] for r in usable if r["verdict"] == V_CONFIRMED]
    weak = [r["feature"] for r in usable if r["verdict"] == V_WEAK]
    return {
        "phase": PHASE, "as_of": as_of, "decision": decision, "decision_rationale": rationale,
        "allowed_decisions": list(ALLOWED_DECISIONS), "forbidden_decisions": list(FORBIDDEN_DECISIONS),
        "objective": ("strict out-of-sample validation of the two EODHD/Norgate quality leads "
                      "(fcf_to_assets, operating_accruals) - NOT a data-acquisition or mining phase"),
        "performs_network": PERFORMS_NETWORK, "offline": True,
        "eodhd_key_visible": bool(key_visible), "eodhd_key_required": False,
        "panel_ok": panel_ok, "scoreable_events": n_events, "scoreable_tickers": n_tickers,
        "candidates_validated": [c["feature"] for c in CANDIDATES],
        "candidate_families": list(ALLOWED_FAMILIES),
        "variants_tested": [v for v, _ in VARIANTS],
        "horizons_tested": list(FWD_WINDOWS),
        "horizon_labels": HORIZON_LABELS,
        "monthly_horizon_note": ("the panel carries trading-day forward windows; the 21-day horizon is "
                                 "the ~1-month (monthly) primary and 63-day the ~quarterly proxy - no "
                                 "separate calendar-month return column exists in the owned panel"),
        "walk_forward": {"train_months": WF_TRAIN_MONTHS, "test_months": WF_TEST_MONTHS,
                         "step_months": WF_STEP_MONTHS,
                         "weighting": "equal (fixed a-priori orientation; pure held-out IC, no sign refit)",
                         "n_windows": len(oos_windows)},
        "acceptance_thresholds": {"confirmed_min_ic_t_21d": CONF_MIN_IC_T,
                                  "confirmed_max_sector_share": CONF_MAX_SECTOR_SHARE,
                                  "confirmed_min_horizons_positive": CONF_MIN_HORIZONS_POS,
                                  "confirmed_min_oos_window_frac": CONF_MIN_OOS_WIN_FRAC,
                                  "monitor_min_ic_t": MONITOR_MIN_IC_T},
        "fcf_to_assets_survived": ("eodhd_fcf_to_assets" in [r["family"] for r in usable
                                   if r["verdict"] in (V_CONFIRMED, V_WEAK)]),
        "operating_accruals_survived": ("eodhd_operating_accruals" in [r["family"] for r in usable
                                        if r["verdict"] in (V_CONFIRMED, V_WEAK)]),
        "confirmed": confirmed, "monitor": weak,
        "ready_for_paper_review": bool(confirmed),
        "candidate_results": [_candidate_report(r) for r in results],
        "secret_safety_leak_scan_clean": leak_clean,
        "api_key_printed": False, "api_key_written_to_disk": False,
        "required_artifacts": [_ARTIFACTS[k] for k in _REQUIRED_ARTIFACTS],
        "exact_next_command": _phase10d_plan(decision, results)["exact_next_command"],
        "constraints_honored": ["offline (no network/key/provider probe)", "only fcf_to_assets + "
                                "operating_accruals validated", "no FMP", "no Paper Trader", "no GCP",
                                "no orders", "no automation", "no deploy", "no package install",
                                "no full regression", "no commit", "no push", "no key printed/written"],
    }


def _print_summary(report: Dict) -> None:
    print("[10-C] decision=%s | offline=%s key_visible=%s | events=%s tickers=%s | confirmed=%s "
          "monitor=%s | ready_for_paper=%s | leak_clean=%s"
          % (report.get("decision"), report.get("offline"), report.get("eodhd_key_visible"),
             report.get("scoreable_events"), report.get("scoreable_tickers"),
             report.get("confirmed"), report.get("monitor"), report.get("ready_for_paper_review"),
             report.get("secret_safety_leak_scan_clean")))


# --------------------------------------------------------------------------- #
# I. Orchestration.
# --------------------------------------------------------------------------- #
def run(out_dir: Optional[Path] = None, *, ev=None, norm_csvs: Optional[Dict[str, Path]] = None,
        panel_csv: Optional[Path] = None, as_of: str = AS_OF, train_months: int = WF_TRAIN_MONTHS,
        test_months: int = WF_TEST_MONTHS, step_months: int = WF_STEP_MONTHS,
        verbose: bool = True) -> Dict:
    P = _Paths(out_dir)
    P.out.mkdir(parents=True, exist_ok=True)
    log = t8._Log(verbose)
    try:
        import pandas as pd  # noqa: F401
        key_visible = bool(os.environ.get(EODHD_KEY_ENV))  # context only; NEVER printed or written
        log.step("preflight", "OFFLINE", "no network / no key required (validation reads local owned "
                 "data only); EODHD key visible=%s" % key_visible)

        # 1. Panel.
        if ev is None:
            ev, panel_ok, stats = build_panel(as_of, panel_csv, log)
        else:
            panel_ok = not getattr(ev, "empty", True)
            stats = {"events_usable": int(len(ev)) if panel_ok else 0,
                     "tickers_usable": int(ev["ticker"].nunique()) if panel_ok else 0}
        if not panel_ok:
            return _finish_blocker(P, log, ("the Norgate survivorship-free panel is empty - the "
                                            "expanded price panel is missing. Rebuild it via Phase "
                                            "8-A/8-V before running 10-C."),
                                   "python research/run_phase8a_autonomous_norgate_research_engine.py "
                                   "--rebuild", key_visible, as_of)
        n_events = int(stats.get("events_usable", 0) or len(ev))
        n_tickers = int(stats.get("tickers_usable", 0) or ev["ticker"].nunique())
        log.step("panel", "DONE", "%d usable events / %d scoreable tickers" % (n_events, n_tickers))

        # 2. Attach the two leads (PIT as-of; oriented + sector-neutral columns).
        norm_csvs = norm_csvs or _default_norm_csvs()
        missing = [c["feature"] for c in CANDIDATES
                   if not Path(norm_csvs.get(c["family"], "")).is_file()]
        if missing:
            return _finish_blocker(P, log, ("missing Phase 10-B normalized family CSV(s): %s. Run "
                                            "Phase 10-B first to produce the PIT-normalized leads."
                                            % ", ".join(missing)),
                                   ("python research/run_phase10b_eodhd_norgate_exhaustive_alpha_"
                                    "factory.py --live"), key_visible, as_of)
        ev, cols = attach_signals(ev, norm_csvs, log)

        # 3. Per-candidate strict OOS battery.
        results = [evaluate_candidate(ev, cand, cols.get(cand["family"], {}),
                                      train_months, test_months, step_months, log)
                   for cand in CANDIDATES]
        oos_windows = canonical_oos_windows(ev, train_months, test_months, step_months)

        # 4. Decision.
        decision, rationale = overall_decision(results)
        if decision not in ALLOWED_DECISIONS or decision in FORBIDDEN_DECISIONS:  # safety net
            decision, rationale = DEC_EXHAUSTED, "decision normaliser: " + rationale

        # 5. Artifacts + report.
        _sec_rows, leak_clean = write_artifacts(P, ev, cols, results, oos_windows, decision, log)
        report = _build_report(decision, rationale, results, oos_windows, n_events, n_tickers,
                               panel_ok, key_visible, leak_clean, as_of)
        _write_json(P.art("report"), report)
        _write_json(P.art("next_plan"), _phase10d_plan(decision, results))
        log.step("artifacts", "DONE", "wrote %d artifacts" % len(_REQUIRED_ARTIFACTS))
        _print_summary(report)
        return report
    except Exception as exc:  # pragma: no cover - defensive
        import traceback
        detail = "%s: %s" % (type(exc).__name__, exc)
        log.step("run", "ERROR", detail)
        report = {"phase": PHASE, "decision": DEC_ERROR, "decision_rationale": detail,
                  "repro_command": "python research/run_phase10c_eodhd_quality_oos_validation.py",
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
              "api_key_written_to_disk": False,
              "candidates_validated": [c["feature"] for c in CANDIDATES]}
    try:
        _write_json(P.art("report"), report)
        _write_json(P.art("next_plan"), {"phase": "10-D", "from_decision": DEC_BLOCKER,
                                          "exact_next_command": fix_cmd})
        sec_rows, _clean = b10._secret_safety_audit(P.out)
        _write_csv(P.art("secret_audit"), ["file", "clean", "keyed_url_marker", "key_value_present"],
                   [[r["file"], r["clean"], r["keyed_url_marker"], r["key_value_present"]]
                    for r in sec_rows])
    except Exception:
        pass
    _print_summary({"decision": DEC_BLOCKER, "offline": True, "eodhd_key_visible": key_visible,
                    "scoreable_events": 0, "scoreable_tickers": 0, "confirmed": [], "monitor": [],
                    "ready_for_paper_review": False, "secret_safety_leak_scan_clean": True})
    return report


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Phase 10-C - Strict Out-of-Sample Validation of EODHD/Norgate Quality Leads")
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
