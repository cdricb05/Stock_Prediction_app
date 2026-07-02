"""Phase 10-L-B - Historical Quality Composite Reweighting And Robustness Backtest.

WHY THIS PHASE EXISTS
    Phase 10-K asked whether the Phase 10-D quarterly quality composite (long fcf_to_assets, short
    operating_accruals, equal-weight, sector-neutral, 63d) could be IMPROVED with narrow, defensible
    changes - interior leg re-weightings, z-cap / winsorize robustness transforms, and stricter
    liquidity / sector-cap packaging. It could NOT answer honestly: the frozen 10-D/10-F/10-H outputs
    held only summary stats for four FIXED signals plus the latest 2026Q2 cross-section, so every
    re-weight / transform was reported INSUFFICIENT_INPUTS (no historical scored panel to re-backtest).

    Phase 10-L-A removed that blocker by PERSISTING the historical per-(month, ticker) sector-neutral
    scored panel (the additive within-month z-legs z_fcf_sn / z_acc_sn + forward 63d returns), and
    proved it reproduces the frozen 10-D composite_sn baseline within tolerance.

    Phase 10-L-B is the honest re-run of the 10-K narrow-improvement harness AGAINST that frozen panel.
    It reconstructs each variant's signal from the persisted z-legs (a reweighting / transform / filter
    of the SAME two legs - NOT a new factor, NOT a broad alpha search, NOT a provider probe), then scores
    every variant with the EXACT engine functions Phase 10-D used (c10._eval, d10.quarterly_backtest,
    d10.walk_forward_h) so results are directly comparable to the baseline. A variant may only unseat the
    baseline if it clears a strict, skeptical, RELATIVE beat test (net-25bps up, net-50bps not worse,
    turnover not materially worse, IC t not materially worse, OOS/subperiod/cohort not deteriorating,
    sector concentration not worse, and the improvement not one-period driven). The skeptical default is
    that the baseline stays champion.

    THIS PHASE MUST NOT: add new factors, add providers, make a live API call, touch the network, touch
    GCP, touch the Paper Trader, create orders, or implement automation. The alpha remains MODEST /
    BOUNDARY (10-D composite_sn: 63d IC t~2.665, quarterly net-25bps ~+0.00401, net-50bps ~+0.00095) and
    is not oversold here; the short (operating_accruals) leg carries the robustness.

REUSE (single source of truth - nothing re-implemented)
    c10 = run_phase10c_eodhd_quality_oos_validation        (_eval, AS_OF, FWD/WF constants, _finite/_pos)
    d10 = run_phase10d_quarterly_quality_composite_validation (quarterly_backtest, walk_forward_h,
                                                               COMP_SN/COMP_RAW, gate constants)
    10-L-A panel = research/output/phase10l_historical_sector_neutral_scored_panel_reconstruction/
                   historical_sector_neutral_scored_panel.csv

DECISIONS (allowed)
    REWEIGHTED_ALPHA_READY_FOR_PAPER_RULES | BASELINE_REMAINS_CHAMPION | REJECT_REWEIGHTING_OVERFIT |
    NEEDS_PANEL_REPAIR

CONSTRAINTS HONORED
    Fully offline (reads only the owned/local 10-L-A frozen panel + the frozen 10-D report; no network,
    no key, no provider probe, no build_panel); paper-only; owned-local-data only; no new purchase; no
    Paper Trader writes; NO orders; NO automation; NO broker; NO deploy; NO GCP; no package install; no
    full regression (targeted tests only); output is metadata / research CSV+JSON only. No commit. No push.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research import run_phase10c_eodhd_quality_oos_validation as c10           # noqa: E402
from research import run_phase10d_quarterly_quality_composite_validation as d10  # noqa: E402

PHASE = "10-L-B"
PHASE_NAME = "Historical Quality Composite Reweighting And Robustness Backtest"
STEM = "phase10l_quality_composite_reweighting_robustness_backtest"
PERFORMS_NETWORK = False

AS_OF = c10.AS_OF
RET_PRIMARY = "fwd_exc_63"
WF_TRAIN = d10.WF_TRAIN_MONTHS
WF_TEST = d10.WF_TEST_MONTHS
WF_STEP = d10.WF_STEP_MONTHS
MAX_SECTOR_SHARE = d10.MAX_SECTOR_SHARE          # 0.60 raw ceiling (context only; SN book runs hotter)
_QTR_MIN_NAMES = 20
_PRE2020 = "2020-01-01"

# Panel + frozen baseline inputs (owned / local; produced by prior offline phases).
_PANEL_REL = ("research/output/phase10l_historical_sector_neutral_scored_panel_reconstruction/"
              "historical_sector_neutral_scored_panel.csv")
_PANEL_JSON_REL = ("research/output/phase10l_historical_sector_neutral_scored_panel_reconstruction/"
                   "phase10l_historical_sector_neutral_scored_panel_reconstruction.json")
_PHASE10D_JSON_REL = ("research/output/phase10d_quarterly_quality_composite_validation/"
                      "phase10d_quarterly_quality_composite_validation.json")

# Panel column -> internal name mapping (discovered from the 10-L-A schema, not assumed).
_COL_ZF = "fcf_to_assets_sector_neutral_z"          # long leg oriented + sector-neutral within-month z
_COL_ZA = "operating_accruals_sector_neutral_z"     # short leg oriented(-1) + sector-neutral within-month z
_COL_COMP_SN = "composite_sn"                        # == z_fcf_sn + z_acc_sn (additive; 10-L-A error 0.0)
_COL_FWD = "forward_63d_return"
_COL_RB = "rebalance_date"

# Reproduction tolerances (a-priori; identical to the 10-L-A brief - a panel-integrity guard, never
# tuned to a result). If the frozen panel does not reproduce the 10-D composite_sn baseline within
# these, the panel is not trustworthy for reweighting -> NEEDS_PANEL_REPAIR.
REPRO_TOL = {"ic_t": 0.25, "net_25bps": 0.0015, "net_50bps": 0.0015, "turnover": 0.10}
_ADDITIVE_TOL = 1e-6                                  # max|comp_sn - (z_fcf_sn + z_acc_sn)|

# Strict, RELATIVE champion-test margins (a-priori). The baseline composite_sn already sits at
# oos_frac_positive ~0.50 and SN top-sector-share ~0.63 (> the 0.60 raw ceiling; a known 10-D/10-F
# sector-mapping caveat), so the champion test is NON-WORSENING vs the baseline, not an absolute bar.
EPS_NET = 1e-6                    # net-25bps must be strictly higher; net-50bps must be >= baseline - EPS
TURN_OK_MULT = 1.10              # turnover may not exceed 1.10x baseline to "not materially worse"
TURN_HARD_MULT = 1.50            # turnover > 1.50x baseline is a hard reject
IC_T_MARGIN = 0.10               # IC t may not fall more than 0.10 below baseline
HIT_MARGIN = 0.05                # quarterly hit-rate may not fall more than 0.05 below baseline (anti one-quarter)
SHARE_EPS = 1e-9                 # top-sector share must not exceed baseline

# Decisions.
DEC_READY = "REWEIGHTED_ALPHA_READY_FOR_PAPER_RULES"
DEC_BASELINE = "BASELINE_REMAINS_CHAMPION"
DEC_OVERFIT = "REJECT_REWEIGHTING_OVERFIT"
DEC_REPAIR = "NEEDS_PANEL_REPAIR"
ALLOWED_DECISIONS = (DEC_READY, DEC_BASELINE, DEC_OVERFIT, DEC_REPAIR)

# Per-variant classifications.
CLS_BASELINE = "BASELINE"
CLS_PASS = "PASS_STRICT"
CLS_OVERFIT = "REJECT_OVERFIT"
CLS_COST = "REJECT_COST_KILLED"
CLS_TURN = "REJECT_TURNOVER"
CLS_NOIMPROVE = "NO_IMPROVEMENT"
CLS_INSUFFICIENT = "INSUFFICIENT_INPUTS"

EODHD_KEY_ENV = "EODHD_API_KEY"

_ARTIFACTS = {
    "report": "%s.json" % STEM,
    "scorecard": "variant_scorecard.csv",
    "baseline_vs": "baseline_vs_variants.csv",
    "oos": "oos_stability_report.csv",
    "cohort": "cohort_stability_report.csv",
    "turnover": "turnover_cost_report.csv",
    "sector": "sector_concentration_report.csv",
    "rejected": "rejected_variants.csv",
}


class _Paths:
    def __init__(self, out_dir=None):
        self.out = Path(out_dir) if out_dir else (_REPO_ROOT / "research" / "output" / STEM)

    def art(self, key: str) -> Path:
        return self.out / _ARTIFACTS[key]


class _Log:
    def __init__(self, verbose: bool = True):
        self.verbose = verbose

    def step(self, stage: str = "", status: str = "", msg: str = "", *args, **kwargs) -> None:
        if self.verbose:
            extra = ""
            if args:
                extra += " " + " ".join(str(a) for a in args)
            if kwargs:
                extra += " " + " ".join("%s=%s" % (k, v) for k, v in kwargs.items())
            print("[%s] %-11s %-9s %s%s" % (PHASE, stage, status, msg, extra))


# --------------------------------------------------------------------------- #
# Small local helpers (self-contained; no network, no key).
# --------------------------------------------------------------------------- #
def _finite(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and not (isinstance(x, float) and math.isnan(x))


def _r(x, nd=6):
    return round(float(x), nd) if _finite(x) else None


def _write_csv(path: Path, header: Sequence[str], rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(list(header))
        for row in rows:
            w.writerow(["" if v is None else v for v in row])


def _write_json(path: Path, obj: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str)


# --------------------------------------------------------------------------- #
# A. Load + integrity-check the frozen 10-L-A panel.
# --------------------------------------------------------------------------- #
def load_panel(panel_path: Path):
    """Load the frozen 10-L-A panel and expose the internal columns the engine needs. Returns
    (df, meta, error). df has entry_date / z_fcf_sn / z_acc_sn / comp_sn / fwd_exc_63 / sector /
    cohort / liquidity_proxy / month."""
    import pandas as pd
    if not panel_path.is_file():
        return None, {}, ("frozen 10-L-A panel not found: %s - run Phase 10-L-A first"
                          % _PANEL_REL)
    df = pd.read_csv(panel_path)
    need = [_COL_ZF, _COL_ZA, _COL_COMP_SN, _COL_FWD, _COL_RB]
    missing = [c for c in need if c not in df.columns]
    if missing:
        return None, {}, "frozen panel is missing required column(s): %s" % ", ".join(missing)
    df["entry_date"] = pd.to_datetime(df[_COL_RB], errors="coerce")
    df["z_fcf_sn"] = pd.to_numeric(df[_COL_ZF], errors="coerce")
    df["z_acc_sn"] = pd.to_numeric(df[_COL_ZA], errors="coerce")
    df["comp_sn"] = pd.to_numeric(df[_COL_COMP_SN], errors="coerce")
    df["fwd_exc_63"] = pd.to_numeric(df[_COL_FWD], errors="coerce")
    if "liquidity_proxy" in df.columns:
        df["liquidity_proxy"] = pd.to_numeric(df["liquidity_proxy"], errors="coerce")
    else:
        df["liquidity_proxy"] = float("nan")
    for c in ("sector", "cohort"):
        if c not in df.columns:
            df[c] = "Unknown"
    df["month"] = df["entry_date"].dt.to_period("M")
    meta = {"n_rows": int(len(df)), "n_tickers": int(df["ticker"].nunique()) if "ticker" in df else 0,
            "n_scoreable": int((df["comp_sn"].notna() & df["fwd_exc_63"].notna()).sum())}
    return df, meta, None


def additive_integrity(df) -> Dict:
    """Confirm comp_sn == z_fcf_sn + z_acc_sn (the 10-L-A reweightability guarantee)."""
    s = df["z_fcf_sn"] + df["z_acc_sn"]
    mask = df["comp_sn"].notna() & s.notna()
    if not bool(mask.any()):
        return {"max_abs_error": float("nan"), "rows_checked": 0, "additive_ok": False}
    err = float((s[mask] - df["comp_sn"][mask]).abs().max())
    return {"max_abs_error": err, "rows_checked": int(mask.sum()),
            "additive_ok": bool(err <= _ADDITIVE_TOL)}


def load_frozen_10d(path: Path) -> Tuple[Dict, Optional[str]]:
    if not path.is_file():
        return {}, "frozen 10-D report not found: %s" % _PHASE10D_JSON_REL
    try:
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception as exc:  # pragma: no cover - defensive
        return {}, "unreadable 10-D report (%s)" % exc
    for row in d.get("signal_results", []):
        if row.get("signal") == "composite_sn":
            return {"ic_t_63d": row.get("ic_t_63d"), "ic_63d": row.get("ic_63d"),
                    "quarterly_net_25bps": row.get("quarterly_net_25bps"),
                    "quarterly_net_50bps": row.get("quarterly_net_50bps"),
                    "quarterly_turnover": row.get("quarterly_turnover"),
                    "quarterly_gross_spread": row.get("quarterly_gross_spread"),
                    "oos_pooled_ic": row.get("oos_pooled_ic"),
                    "oos_frac_windows_positive": row.get("oos_frac_windows_positive"),
                    "top_sector_share": row.get("top_sector_share")}, None
    return {}, "frozen 10-D report has no composite_sn signal_result"


# --------------------------------------------------------------------------- #
# B. Signal builders (reweight / transform / filter of the SAME two frozen z-legs).
# --------------------------------------------------------------------------- #
def _weighted(df, w_fcf: float, w_acc: float):
    return w_fcf * df["z_fcf_sn"] + w_acc * df["z_acc_sn"]


def _zcapped(df, cap: float):
    zf = df["z_fcf_sn"].clip(lower=-cap, upper=cap)
    za = df["z_acc_sn"].clip(lower=-cap, upper=cap)
    return zf + za


def _winsor_leg(df, col: str, lo_pct: float, hi_pct: float):
    grp = df.groupby("month")[col]
    lo = grp.transform(lambda s: s.quantile(lo_pct / 100.0))
    hi = grp.transform(lambda s: s.quantile(hi_pct / 100.0))
    return df[col].clip(lower=lo, upper=hi)


def _winsorized(df, lo_pct: float, hi_pct: float):
    return _winsor_leg(df, "z_fcf_sn", lo_pct, hi_pct) + _winsor_leg(df, "z_acc_sn", lo_pct, hi_pct)


def _liq_filtered(df, pct: float):
    """Keep, within each month, the names at/above the p-th liquidity percentile (NaN-liquidity rows
    cannot be confirmed liquid, so they are excluded under the filter)."""
    grp = df.groupby("month")["liquidity_proxy"]
    thr = grp.transform(lambda s: s.quantile(pct / 100.0))
    keep = (df["liquidity_proxy"] >= thr).fillna(False)
    return df[keep].copy(), int((~keep).sum())


# --------------------------------------------------------------------------- #
# C. Quarterly long-short book (mirrors d10.quarterly_backtest; adds book counts + optional sector cap).
#     For cap_frac is None this reproduces d10.quarterly_backtest EXACTLY (cross-checked at runtime);
#     for cap_frac set it greedily fills a quintile-sized book per side under a per-sector cap.
# --------------------------------------------------------------------------- #
def _greedy_cap(ordered, target: int, cap: int, has_sector: bool):
    if not has_sector:
        return ordered.head(target)
    chosen, sec_count = [], {}
    for idx, sec in zip(ordered.index, ordered["sector"].fillna("Unknown")):
        if len(chosen) >= target:
            break
        if sec_count.get(sec, 0) >= cap:
            continue
        sec_count[sec] = sec_count.get(sec, 0) + 1
        chosen.append(idx)
    return ordered.loc[chosen]


def quarterly_book(df, sigcol: str, ret_col: str = RET_PRIMARY, cap_frac: Optional[float] = None,
                   min_names: int = _QTR_MIN_NAMES) -> Dict:
    import numpy as np
    import pandas as pd
    empty = {"mean_spread": float("nan"), "spread_t": float("nan"), "spread_hit_rate": float("nan"),
             "n_quarters": 0, "avg_turnover": float("nan"), "net_10bps": float("nan"),
             "net_25bps": float("nan"), "net_50bps": float("nan"), "avg_long_count": float("nan"),
             "avg_short_count": float("nan"), "top_sector_share_long": float("nan"),
             "top_sector_long": "", "top_sector_share_short": float("nan"), "top_sector_short": ""}
    if df is None or getattr(df, "empty", True) or sigcol not in df.columns or ret_col not in df.columns:
        return empty
    sub = df[df[sigcol].notna() & df[ret_col].notna()].copy()
    if sub.empty:
        return empty
    sub["q"] = sub["entry_date"].dt.to_period("Q")
    has_sector = "sector" in sub.columns
    spreads, turns, lcnt, scnt = [], [], [], []
    long_sectors, short_sectors = {}, {}
    prev_long, prev_short = set(), set()
    for q in sorted(sub["q"].unique()):
        chunk = sub[sub["q"] == q].sort_values("entry_date").groupby("ticker", as_index=False).last()
        if len(chunk) < min_names:
            continue
        if cap_frac is None:
            try:
                qd = pd.qcut(chunk[sigcol].rank(method="first"), 5, labels=False)
            except ValueError:
                continue
            top, bot = chunk[qd == 4], chunk[qd == 0]
        else:
            target = max(1, int(round(len(chunk) / 5.0)))
            cap = max(1, int(math.floor(cap_frac * target)))
            ranked = chunk.sort_values(sigcol, ascending=False)
            top = _greedy_cap(ranked, target, cap, has_sector)
            bot = _greedy_cap(ranked.iloc[::-1], target, cap, has_sector)
        if top.empty or bot.empty:
            continue
        spreads.append(float(top[ret_col].mean() - bot[ret_col].mean()))
        cl, cs = set(top["ticker"]), set(bot["ticker"])
        if prev_long:
            turns.append(1.0 - len(cl & prev_long) / max(1, len(cl)))
        if prev_short:
            turns.append(1.0 - len(cs & prev_short) / max(1, len(cs)))
        prev_long, prev_short = cl, cs
        lcnt.append(len(top))
        scnt.append(len(bot))
        if has_sector:
            for sct in top["sector"]:
                long_sectors[sct] = long_sectors.get(sct, 0) + 1
            for sct in bot["sector"]:
                short_sectors[sct] = short_sectors.get(sct, 0) + 1
    n_q = len(spreads)
    if not n_q:
        return empty
    arr = np.asarray(spreads, dtype=float)
    mean_spread = float(arr.mean())
    sd = float(arr.std(ddof=1)) if n_q > 1 else float("nan")
    t = (mean_spread / (sd / math.sqrt(n_q))) if (_finite(sd) and sd > 0) else float("nan")
    hit = float(np.mean(np.sign(arr) == np.sign(mean_spread)))
    avg_turn = float(np.mean(turns)) if turns else float("nan")

    def _share(counts):
        tot = sum(counts.values())
        if not tot:
            return "", float("nan")
        shares = {k: v / tot for k, v in counts.items()}
        top_s = max(shares, key=shares.get)
        return top_s, shares[top_s]

    top_sec_l, share_l = _share(long_sectors)
    top_sec_s, share_s = _share(short_sectors)

    def net(bps):
        tn = avg_turn if _finite(avg_turn) else 1.0
        return mean_spread - (bps / 1e4) * tn * 2.0

    return {"mean_spread": mean_spread, "spread_t": t, "spread_hit_rate": hit, "n_quarters": n_q,
            "avg_turnover": avg_turn, "net_10bps": net(10), "net_25bps": net(25), "net_50bps": net(50),
            "avg_long_count": float(np.mean(lcnt)) if lcnt else float("nan"),
            "avg_short_count": float(np.mean(scnt)) if scnt else float("nan"),
            "top_sector_share_long": share_l, "top_sector_long": top_sec_l,
            "top_sector_share_short": share_s, "top_sector_short": top_sec_s}


def _selfcheck_book(df, log) -> Dict:
    """Confirm the local quarterly_book(cap=None) reproduces d10.quarterly_backtest on the baseline."""
    mine = quarterly_book(df, "comp_sn", cap_frac=None)
    ref = d10.quarterly_backtest(df, "comp_sn", ret_col=RET_PRIMARY)
    checks = {}
    ok = True
    for k in ("mean_spread", "avg_turnover", "net_25bps", "net_50bps"):
        a, b = mine.get(k), ref.get(k)
        diff = abs(a - b) if (_finite(a) and _finite(b)) else float("nan")
        good = _finite(diff) and diff <= 1e-9
        ok = ok and good
        checks[k] = {"local": a, "d10": b, "abs_diff": diff, "match": good}
    log.step("selfcheck", "DONE" if ok else "WARN",
             "local quarterly_book vs d10.quarterly_backtest match=%s" % ok)
    return {"reproduces_d10_quarterly_backtest": ok, "checks": checks}


# --------------------------------------------------------------------------- #
# D. Signal-level battery (IC / OOS / cohort / subperiod) - EXACT 10-D engine functions.
# --------------------------------------------------------------------------- #
def signal_battery(df, sigcol: str, *, with_oos: bool = True) -> Dict:
    import pandas as pd
    ic = c10._eval(df, sigcol, 63, False)
    wf = (d10.walk_forward_h(df, sigcol, RET_PRIMARY, WF_TRAIN, WF_TEST, WF_STEP) if with_oos else {})
    if "cohort" in df.columns:
        old = c10._eval(df[df["cohort"] == "old"], sigcol, 63, False)
        new = c10._eval(df[df["cohort"] == "new"], sigcol, 63, False)
    else:
        old = new = ic
    pre = c10._eval(df[df["entry_date"] < pd.Timestamp(_PRE2020)], sigcol, 63, False)
    post = c10._eval(df[df["entry_date"] >= pd.Timestamp(_PRE2020)], sigcol, 63, False)
    return {"ic": ic, "wf": wf, "old": old, "new": new, "pre": pre, "post": post}


def _leg_ics(df) -> Dict:
    """Standalone sector-neutral single-leg 63d ICs (constant across variants; used for attribution)."""
    lf = c10._eval(df, "z_fcf_sn", 63, False)
    la = c10._eval(df, "z_acc_sn", 63, False)
    return {"fcf_ic": lf.get("mean_ic"), "fcf_ic_t": lf.get("ic_t"),
            "acc_ic": la.get("mean_ic"), "acc_ic_t": la.get("ic_t")}


# --------------------------------------------------------------------------- #
# E. Assemble a variant metrics record.
# --------------------------------------------------------------------------- #
def _assemble(vid, group, desc, w_fcf, w_acc, batt, book, leg_ics, basis, cap_frac=None,
              extra=None) -> Dict:
    ic, wf = batt["ic"], batt.get("wf", {})
    old, new, pre, post = batt["old"], batt["new"], batt["pre"], batt["post"]
    lc_f = (w_fcf * leg_ics["fcf_ic"]) if _finite(leg_ics.get("fcf_ic")) else None
    lc_a = (w_acc * leg_ics["acc_ic"]) if _finite(leg_ics.get("acc_ic")) else None
    rec = {
        "variant_id": vid, "group": group, "description": desc, "basis": basis,
        "weight_fcf": w_fcf, "weight_acc": w_acc, "cap_frac": cap_frac,
        "ic_mean": _r(ic.get("mean_ic")), "ic_t": _r(ic.get("ic_t"), 3),
        "n_events": int(ic.get("n_events", 0) or 0), "n_months": int(ic.get("n_months", 0) or 0),
        "quarterly_gross_spread": _r(book.get("mean_spread")),
        "quarterly_spread_t": _r(book.get("spread_t"), 3),
        "quarterly_spread_hit_rate": _r(book.get("spread_hit_rate"), 4),
        "quarterly_net_10bps": _r(book.get("net_10bps")),
        "quarterly_net_25bps": _r(book.get("net_25bps")),
        "quarterly_net_50bps": _r(book.get("net_50bps")),
        "quarterly_turnover": _r(book.get("avg_turnover"), 4),
        "n_quarters": int(book.get("n_quarters", 0) or 0),
        "avg_long_count": _r(book.get("avg_long_count"), 2),
        "avg_short_count": _r(book.get("avg_short_count"), 2),
        "oos_pooled_ic": _r(wf.get("pooled_oos_ic")),
        "oos_ic_t": _r(wf.get("oos_ic_t"), 3),
        "oos_frac_windows_positive": _r(wf.get("frac_windows_positive"), 3),
        "n_windows": int(wf.get("n_windows", 0) or 0),
        "old_ic": _r(old.get("mean_ic")), "old_ic_t": _r(old.get("ic_t"), 3),
        "old_n_events": int(old.get("n_events", 0) or 0),
        "new_ic": _r(new.get("mean_ic")), "new_ic_t": _r(new.get("ic_t"), 3),
        "new_n_events": int(new.get("n_events", 0) or 0),
        "both_cohorts_positive": bool(c10._pos(old) and c10._pos(new)),
        "pre2020_ic": _r(pre.get("mean_ic")), "post2020_ic": _r(post.get("mean_ic")),
        "both_subperiods_positive": bool(c10._pos(pre) and c10._pos(post)),
        "top_sector_share": _r(ic.get("top_sector_share"), 4),
        "q_top_sector_share_long": _r(book.get("top_sector_share_long"), 4),
        "q_top_sector_long": book.get("top_sector_long", ""),
        "q_top_sector_share_short": _r(book.get("top_sector_share_short"), 4),
        "q_top_sector_short": book.get("top_sector_short", ""),
        "long_leg_contribution": _r(lc_f), "short_leg_contribution": _r(lc_a),
        "classification": None, "reject_reason": None, "is_baseline": False,
    }
    if extra:
        rec.update(extra)
    return rec


# --------------------------------------------------------------------------- #
# F. Skeptical, RELATIVE champion classification.
# --------------------------------------------------------------------------- #
def classify(v: Dict, base: Dict) -> Tuple[str, str]:
    """Order: hard cost/turnover gates -> full relative beat test -> overfit / no-improvement split."""
    net25, net50 = v.get("quarterly_net_25bps"), v.get("quarterly_net_50bps")
    turn, ic_t = v.get("quarterly_turnover"), v.get("ic_t")
    oos = v.get("oos_frac_windows_positive")
    share = v.get("top_sector_share")
    hit = v.get("quarterly_spread_hit_rate")
    b_net25, b_net50 = base["quarterly_net_25bps"], base["quarterly_net_50bps"]
    b_turn, b_ic_t = base["quarterly_turnover"], base["ic_t"]
    b_oos, b_share, b_hit = (base["oos_frac_windows_positive"], base["top_sector_share"],
                             base["quarterly_spread_hit_rate"])

    if net25 is None:
        return CLS_INSUFFICIENT, "no backtested net-25bps available for this variant"
    if net25 <= 0:
        return CLS_COST, "quarterly net-25bps %s <= 0 (cost-killed)" % _r(net25)
    if _finite(turn) and _finite(b_turn) and b_turn > 0 and turn > b_turn * TURN_HARD_MULT:
        return CLS_TURN, ("quarterly turnover %s > %.2fx baseline %s"
                          % (_r(turn, 4), TURN_HARD_MULT, _r(b_turn, 4)))

    improves_headline = net25 > (b_net25 + EPS_NET)
    beat = (
        improves_headline
        and (net50 is not None and net50 >= b_net50 - EPS_NET)
        and (turn is not None and b_turn is not None and turn <= b_turn * TURN_OK_MULT)
        and (ic_t is not None and b_ic_t is not None and ic_t >= b_ic_t - IC_T_MARGIN)
        and (oos is not None and b_oos is not None and oos >= b_oos)
        and (share is not None and b_share is not None and share <= b_share + SHARE_EPS)
    )
    robust = (
        bool(v.get("both_cohorts_positive"))
        and bool(v.get("both_subperiods_positive"))
        and (hit is not None and b_hit is not None and hit >= b_hit - HIT_MARGIN)
        and (v.get("n_quarters", 0) >= base.get("n_quarters", 0) - 1)
    )
    if beat and robust:
        return CLS_PASS, ("beats the baseline on every strict RELATIVE criterion (net25 up, net50 not "
                          "worse, turnover/IC-t/OOS/concentration not worse) and the improvement holds "
                          "in both cohorts and both subperiods (not one-period driven)")
    if improves_headline:
        why = []
        if not (net50 is not None and net50 >= b_net50 - EPS_NET):
            why.append("net-50bps worse than baseline")
        if not (turn is not None and b_turn is not None and turn <= b_turn * TURN_OK_MULT):
            why.append("turnover materially worse")
        if not (ic_t is not None and b_ic_t is not None and ic_t >= b_ic_t - IC_T_MARGIN):
            why.append("IC t-stat materially worse")
        if not (oos is not None and b_oos is not None and oos >= b_oos):
            why.append("OOS frac-positive deteriorates")
        if not (share is not None and b_share is not None and share <= b_share + SHARE_EPS):
            why.append("sector concentration worsens")
        if not robust:
            why.append("improvement not robust across cohorts/subperiods/quarters")
        return CLS_OVERFIT, ("net-25bps improves in-sample but fails the strict test: %s"
                             % ("; ".join(why) or "secondary criteria not met"))
    return CLS_NOIMPROVE, ("does not beat the baseline net-25bps %s (variant %s); baseline stands"
                           % (_r(b_net25), _r(net25)))


# --------------------------------------------------------------------------- #
# G. Build the full variant matrix.
# --------------------------------------------------------------------------- #
def build_variants(df, base_batt, leg_ics, log) -> Tuple[List[Dict], Dict]:
    variants: List[Dict] = []

    # --- baseline (50/50 == frozen composite_sn) --------------------------
    base_book = quarterly_book(df, "comp_sn", cap_frac=None)
    base_rec = _assemble("w_50_50", "weighting",
                         "50/50 equal weight on within-month sector-neutral z-legs (== Phase 10-D "
                         "composite_sn baseline)", 0.5, 0.5, base_batt, base_book, leg_ics,
                         "backtested_sn")
    base_rec["is_baseline"] = True
    base_rec["classification"], base_rec["reject_reason"] = CLS_BASELINE, "confirmed baseline configuration"
    variants.append(base_rec)
    base_metrics = base_rec

    # --- Group A: interior + extreme weightings ---------------------------
    weight_defs = [("w_60_40", 0.6, 0.4), ("w_40_60", 0.4, 0.6),
                   ("w_70_30", 0.7, 0.3), ("w_30_70", 0.3, 0.7)]
    for vid, wfc, wac in weight_defs:
        col = "__sig_%s__" % vid
        df[col] = _weighted(df, wfc, wac)
        batt = signal_battery(df, col)
        book = quarterly_book(df, col, cap_frac=None)
        variants.append(_assemble(vid, "weighting",
                                  "%d%% fcf_to_assets / %d%% operating_accruals (sector-neutral legs)"
                                  % (round(wfc * 100), round(wac * 100)),
                                  wfc, wac, batt, book, leg_ics, "backtested_sn"))
        log.step("variant", "DONE", vid)

    df["__sig_fcf__"] = df["z_fcf_sn"]
    batt = signal_battery(df, "__sig_fcf__")
    variants.append(_assemble("w_fcf_only_100_0", "weighting",
                              "fcf_to_assets only (100/0) - long-leg standalone, sector-neutral",
                              1.0, 0.0, batt, quarterly_book(df, "__sig_fcf__"), leg_ics, "backtested_sn"))
    log.step("variant", "DONE", "w_fcf_only_100_0")
    df["__sig_acc__"] = df["z_acc_sn"]
    batt = signal_battery(df, "__sig_acc__")
    variants.append(_assemble("w_accruals_only_0_100", "weighting",
                              "operating_accruals only (0/100) - short-leg standalone, sector-neutral",
                              0.0, 1.0, batt, quarterly_book(df, "__sig_acc__"), leg_ics, "backtested_sn"))
    log.step("variant", "DONE", "w_accruals_only_0_100")

    # --- Group B: robustness transforms (equal-weight of transformed legs) -
    for vid, cap in (("zcap_abs_3_0", 3.0), ("zcap_abs_2_5", 2.5)):
        col = "__sig_%s__" % vid
        df[col] = _zcapped(df, cap)
        batt = signal_battery(df, col)
        variants.append(_assemble(vid, "robustness_transform",
                                  "z-score cap at |z|=%s on each sector-neutral leg, then equal-weight"
                                  % cap, 0.5, 0.5, batt, quarterly_book(df, col), leg_ics,
                                  "backtested_sn"))
        log.step("variant", "DONE", vid)
    for vid, lo, hi in (("winsorize_1_99", 1.0, 99.0), ("winsorize_5_95", 5.0, 95.0)):
        col = "__sig_%s__" % vid
        df[col] = _winsorized(df, lo, hi)
        batt = signal_battery(df, col)
        variants.append(_assemble(vid, "robustness_transform",
                                  "within-month winsorize each sector-neutral leg at %g/%g pct, then "
                                  "equal-weight" % (lo, hi), 0.5, 0.5, batt, quarterly_book(df, col),
                                  leg_ics, "backtested_sn"))
        log.step("variant", "DONE", vid)

    # --- Group C: liquidity filters (equal-weight composite on filtered universe) -
    for vid, pct in (("liq_p25_baseline", 25.0), ("liq_p50_stricter", 50.0)):
        fdf, n_excl = _liq_filtered(df, pct)
        batt = signal_battery(fdf, "comp_sn")
        book = quarterly_book(fdf, "comp_sn", cap_frac=None)
        variants.append(_assemble(vid, "liquidity_filter",
                                  "keep within-month liquidity >= p%g, then equal-weight composite_sn "
                                  "(%d row-months excluded)" % (pct, n_excl),
                                  0.5, 0.5, batt, book, leg_ics, "backtested_sn",
                                  extra={"n_rows_excluded": n_excl}))
        log.step("variant", "DONE", vid)

    # --- Group D: sector-cap packaging (baseline signal; capped book) ------
    # Signal unchanged (comp_sn) -> reuse baseline IC/OOS/cohort/subperiod; only the traded book changes.
    for vid, cap_frac in (("sector_cap_25_baseline", 0.25), ("sector_cap_20_stricter", 0.20)):
        book = quarterly_book(df, "comp_sn", cap_frac=cap_frac)
        variants.append(_assemble(vid, "sector_cap",
                                  "equal-weight composite_sn with a %d%%/side per-sector book cap"
                                  % round(cap_frac * 100), 0.5, 0.5, base_batt, book, leg_ics,
                                  "backtested_sn", cap_frac=cap_frac))
        log.step("variant", "DONE", vid)

    # --- classify everything so far (needed to pick the pre-declared best weight) ----
    for v in variants:
        if v.get("is_baseline"):
            continue
        v["classification"], v["reject_reason"] = classify(v, base_metrics)

    # --- pre-declared best interior/extreme weight (highest quarterly net-25bps among Group A) -------
    weight_ids = {"w_60_40", "w_40_60", "w_70_30", "w_30_70", "w_fcf_only_100_0", "w_accruals_only_0_100"}
    weight_variants = [v for v in variants if v["variant_id"] in weight_ids
                       and v.get("quarterly_net_25bps") is not None]
    best_w = max(weight_variants, key=lambda v: v["quarterly_net_25bps"]) if weight_variants else None
    best_meta = None

    # --- Group E: pre-declared limited combinations (best weight + one stricter control) -------------
    if best_w is not None:
        wfc, wac = best_w["weight_fcf"], best_w["weight_acc"]
        best_meta = {"best_weight_variant": best_w["variant_id"], "weight_fcf": wfc, "weight_acc": wac,
                     "selected_by": "highest quarterly net-25bps among the interior/extreme weightings"}

        col = "__sig_bw_zcap__"
        df[col] = wfc * df["z_fcf_sn"].clip(-3.0, 3.0) + wac * df["z_acc_sn"].clip(-3.0, 3.0)
        batt = signal_battery(df, col)
        v = _assemble("best_weight_plus_zcap_3_0", "combination",
                      "best weight %s + z-cap |z|=3.0 on each leg" % best_w["variant_id"],
                      wfc, wac, batt, quarterly_book(df, col), leg_ics, "backtested_sn")
        v["classification"], v["reject_reason"] = classify(v, base_metrics)
        variants.append(v)
        log.step("variant", "DONE", "best_weight_plus_zcap_3_0")

        fdf, n_excl = _liq_filtered(df, 50.0)
        col = "__sig_bw__"
        fdf[col] = _weighted(fdf, wfc, wac)
        batt = signal_battery(fdf, col)
        v = _assemble("best_weight_plus_liq_p50", "combination",
                      "best weight %s + stricter liquidity p50 filter (%d row-months excluded)"
                      % (best_w["variant_id"], n_excl), wfc, wac, batt,
                      quarterly_book(fdf, col), leg_ics, "backtested_sn",
                      extra={"n_rows_excluded": n_excl})
        v["classification"], v["reject_reason"] = classify(v, base_metrics)
        variants.append(v)
        log.step("variant", "DONE", "best_weight_plus_liq_p50")

        col = "__sig_bw2__"
        df[col] = _weighted(df, wfc, wac)
        batt = signal_battery(df, col)
        v = _assemble("best_weight_plus_sector_cap_20", "combination",
                      "best weight %s + stricter 20%%/side sector book cap" % best_w["variant_id"],
                      wfc, wac, batt, quarterly_book(df, col, cap_frac=0.20), leg_ics,
                      "backtested_sn", cap_frac=0.20)
        v["classification"], v["reject_reason"] = classify(v, base_metrics)
        variants.append(v)
        log.step("variant", "DONE", "best_weight_plus_sector_cap_20")

    return variants, {"best_weight": best_meta}


# --------------------------------------------------------------------------- #
# H. Decision.
# --------------------------------------------------------------------------- #
def decide(variants: List[Dict]) -> Tuple[str, str, Dict]:
    passers = [v for v in variants if v["classification"] == CLS_PASS]
    overfits = [v for v in variants if v["classification"] == CLS_OVERFIT]
    baseline = next(v for v in variants if v.get("is_baseline"))
    if passers:
        champ = max(passers, key=lambda v: v["quarterly_net_25bps"])
        champion = {"champion": champ["variant_id"], "is_baseline": False,
                    "reason": champ["reject_reason"], "metrics": _champ_metrics(champ)}
        rationale = ("variant %s cleared the strict RELATIVE beat test on honest sector-neutral "
                     "backtested evidence (net-25bps up, all secondary criteria not worse, robust "
                     "across cohorts/subperiods) - ready for paper rules." % champ["variant_id"])
        return DEC_READY, rationale, champion
    champion = {"champion": "baseline_composite_sn_equal_weight", "is_baseline": True,
                "reason": "no variant cleared the strict relative beat test on honest evidence",
                "metrics": _champ_metrics(baseline)}
    if overfits:
        ids = ", ".join(v["variant_id"] for v in overfits)
        rationale = ("one or more variants (%s) raise the in-sample quarterly net-25bps but FAIL the "
                     "strict test (a secondary criterion worse and/or the improvement not robust across "
                     "cohorts/subperiods/quarters) - classic reweighting overfit. The baseline "
                     "equal-weight sector-neutral composite remains the honest champion; do NOT "
                     "productize the overfit reweighting." % ids)
        return DEC_OVERFIT, rationale, champion
    rationale = ("no variant beats the baseline quarterly net-25bps on honest sector-neutral evidence. "
                 "Interior/extreme reweightings, z-cap/winsorize transforms, and stricter "
                 "liquidity/sector-cap packaging do not improve the modest/boundary edge - the short "
                 "operating_accruals leg carries the result and the long fcf leg diversifies. Baseline "
                 "remains champion (skeptical default upheld).")
    return DEC_BASELINE, rationale, champion


def _champ_metrics(v: Dict) -> Dict:
    return {k: v.get(k) for k in ("ic_t", "quarterly_net_25bps", "quarterly_net_50bps",
                                  "quarterly_turnover", "oos_frac_windows_positive",
                                  "top_sector_share", "both_cohorts_positive",
                                  "both_subperiods_positive", "n_quarters")}


# --------------------------------------------------------------------------- #
# I. Baseline reproduction guard (panel-integrity vs frozen 10-D).
# --------------------------------------------------------------------------- #
def reproduction_check(base_rec: Dict, frozen: Dict) -> Tuple[List[Dict], bool]:
    rows, all_ok = [], True
    pairs = [("ic_t_63d", "ic_t", base_rec.get("ic_t"), "ic_t"),
             ("quarterly_net_25bps", "net_25bps", base_rec.get("quarterly_net_25bps"), "net_25bps"),
             ("quarterly_net_50bps", "net_50bps", base_rec.get("quarterly_net_50bps"), "net_50bps"),
             ("quarterly_turnover", "turnover", base_rec.get("quarterly_turnover"), "turnover")]
    for fkey, tolkey, rv, label in pairs:
        fv = frozen.get(fkey)
        tol = REPRO_TOL[tolkey]
        diff = abs(fv - rv) if (_finite(fv) and _finite(rv)) else float("nan")
        ok = _finite(diff) and diff <= tol
        all_ok = all_ok and ok
        rows.append({"metric": fkey, "frozen_10d": fv, "reconstructed": rv, "abs_diff": diff,
                     "tolerance": tol, "within_tolerance": bool(ok)})
    return rows, all_ok


# --------------------------------------------------------------------------- #
# J. Artifact writers.
# --------------------------------------------------------------------------- #
def _write_artifacts(P: _Paths, variants: List[Dict]) -> None:
    sc_hdr = ["variant_id", "group", "basis", "weight_fcf", "weight_acc", "cap_frac",
              "ic_mean", "ic_t", "quarterly_gross_spread", "quarterly_net_25bps", "quarterly_net_50bps",
              "quarterly_turnover", "oos_pooled_ic", "oos_frac_windows_positive",
              "both_cohorts_positive", "both_subperiods_positive", "top_sector_share",
              "avg_long_count", "avg_short_count", "n_quarters", "long_leg_contribution",
              "short_leg_contribution", "classification", "reject_reason", "description"]
    _write_csv(P.art("scorecard"), sc_hdr, [[v.get(k) for k in sc_hdr] for v in variants])

    base = next(v for v in variants if v.get("is_baseline"))
    bv_hdr = ["variant_id", "classification", "net25_baseline", "net25_variant", "net25_delta",
              "net50_delta", "ic_t_delta", "turnover_ratio", "oos_frac_delta", "top_sector_share_delta"]
    bv_rows = []
    for v in variants:
        if v.get("is_baseline"):
            continue

        def _d(k):
            a, b = v.get(k), base.get(k)
            return _r(a - b) if (_finite(a) and _finite(b)) else None
        turn_ratio = (_r(v.get("quarterly_turnover") / base.get("quarterly_turnover"), 3)
                      if (_finite(v.get("quarterly_turnover")) and _finite(base.get("quarterly_turnover"))
                          and base.get("quarterly_turnover")) else None)
        bv_rows.append([v["variant_id"], v["classification"], base.get("quarterly_net_25bps"),
                        v.get("quarterly_net_25bps"), _d("quarterly_net_25bps"),
                        _d("quarterly_net_50bps"), _d("ic_t"), turn_ratio,
                        _d("oos_frac_windows_positive"), _d("top_sector_share")])
    _write_csv(P.art("baseline_vs"), bv_hdr, bv_rows)

    oos_hdr = ["variant_id", "oos_pooled_ic", "oos_ic_t", "oos_frac_windows_positive", "n_windows",
               "both_subperiods_positive", "pre2020_ic", "post2020_ic"]
    _write_csv(P.art("oos"), oos_hdr, [[v.get(k) for k in oos_hdr] for v in variants])

    coh_hdr = ["variant_id", "old_ic", "old_ic_t", "old_n_events", "new_ic", "new_ic_t", "new_n_events",
               "both_cohorts_positive"]
    _write_csv(P.art("cohort"), coh_hdr, [[v.get(k) for k in coh_hdr] for v in variants])

    tc_hdr = ["variant_id", "quarterly_gross_spread", "quarterly_turnover", "quarterly_net_10bps",
              "quarterly_net_25bps", "quarterly_net_50bps", "n_quarters", "avg_long_count",
              "avg_short_count"]
    _write_csv(P.art("turnover"), tc_hdr, [[v.get(k) for k in tc_hdr] for v in variants])

    se_hdr = ["variant_id", "top_sector_share", "q_top_sector_share_long", "q_top_sector_long",
              "q_top_sector_share_short", "q_top_sector_short", "cap_frac"]
    _write_csv(P.art("sector"), se_hdr, [[v.get(k) for k in se_hdr] for v in variants])

    rej = [v for v in variants if v["classification"] in
           (CLS_OVERFIT, CLS_COST, CLS_TURN, CLS_NOIMPROVE, CLS_INSUFFICIENT)]
    rj_hdr = ["variant_id", "group", "classification", "reason", "net25", "ic_t",
              "oos_frac_windows_positive", "top_sector_share"]
    _write_csv(P.art("rejected"), rj_hdr,
               [[v["variant_id"], v["group"], v["classification"], v["reject_reason"],
                 v.get("quarterly_net_25bps"), v.get("ic_t"), v.get("oos_frac_windows_positive"),
                 v.get("top_sector_share")] for v in rej])


# --------------------------------------------------------------------------- #
# K. Report + orchestration.
# --------------------------------------------------------------------------- #
def _leg_contribution_summary(variants, leg_ics) -> Dict:
    base = next(v for v in variants if v.get("is_baseline"))
    return {
        "long_leg": {"signal": "fcf_to_assets (sector-neutral)", "role": "long",
                     "standalone_ic_mean_63d": _r(leg_ics.get("fcf_ic")),
                     "standalone_ic_t_63d": _r(leg_ics.get("fcf_ic_t"), 3)},
        "short_leg": {"signal": "operating_accruals (sector-neutral, oriented -1)", "role": "short",
                      "standalone_ic_mean_63d": _r(leg_ics.get("acc_ic")),
                      "standalone_ic_t_63d": _r(leg_ics.get("acc_ic_t"), 3)},
        "baseline_composite_sn": {"ic_t_63d": base.get("ic_t"),
                                  "quarterly_net_25bps": base.get("quarterly_net_25bps")},
        "attribution": ("leg contribution per variant = weight x standalone sector-neutral single-leg "
                        "mean-IC (a transparent weighted-IC attribution; rank-IC is not exactly linear, "
                        "so this is directional, not an exact variance decomposition). Consistent with "
                        "10-K: the short operating_accruals leg carries most of the robustness; the long "
                        "fcf_to_assets leg diversifies and lowers sector concentration."),
    }


def _next_plan(decision: str) -> Dict:
    if decision == DEC_READY:
        nxt = ("Phase 10-M: package the winning reweighting as PAPER RULES (paper-only, quarterly "
               "rebalance, sector-neutral book, cost budget <25bps) with a human approve/reject gate. "
               "No orders, no automation, no broker, no deploy.")
    elif decision == DEC_OVERFIT:
        nxt = ("Do NOT productize the overfit reweighting. Keep the baseline equal-weight composite_sn. "
               "Optionally widen the OWNED-EODHD quality family (gross_profitability, asset_growth, "
               "net_share_issuance) into the composite at 63d and re-test with this same harness - "
               "owned-data only, no new purchase, paper-only, no orders/automation.")
    elif decision == DEC_BASELINE:
        nxt = ("Keep the baseline equal-weight sector-neutral composite_sn as champion. To seek genuine "
               "improvement, widen the OWNED-EODHD quality family into the composite at the quarterly "
               "horizon and re-run this harness; or improve the owned sector-mapping coverage to firm "
               "up the sector-neutral book. Owned-data only; no new purchase; paper-only; no "
               "orders/automation/broker/deploy.")
    else:
        nxt = ("Repair the panel first: re-run Phase 10-L-A so the frozen historical scored panel "
               "reproduces the 10-D composite_sn baseline within tolerance, then re-run this harness. "
               "Owned-data only; no new purchase.")
    return {"phase": "10-M", "from_decision": decision, "next_step": nxt,
            "constraints": ["owned data only; no new purchase", "paper-only", "no orders",
                            "no automation", "no broker", "no deploy", "no commit", "no push"]}


def _build_report(decision, rationale, panel_meta, repro_rows, repro_ok, additive, selfcheck,
                  frozen, ferr, variants, champion, best_meta, leg_ics) -> Dict:
    base = next(v for v in variants if v.get("is_baseline"))
    rejected = [{"variant_id": v["variant_id"], "group": v["group"],
                 "classification": v["classification"], "reason": v["reject_reason"],
                 "evidence": ("net25=%s ic_t=%s oos=%s top_sector=%s"
                              % (v.get("quarterly_net_25bps"), v.get("ic_t"),
                                 v.get("oos_frac_windows_positive"), v.get("top_sector_share")))}
                for v in variants if v["classification"] in
                (CLS_OVERFIT, CLS_COST, CLS_TURN, CLS_NOIMPROVE, CLS_INSUFFICIENT)]
    return {
        "phase": PHASE, "phase_name": PHASE_NAME, "as_of": AS_OF, "decision": decision,
        "decision_rationale": rationale, "allowed_decisions": list(ALLOWED_DECISIONS),
        "objective": ("honestly backtest whether the Phase 10-D quarterly quality composite can be "
                      "improved by leg reweighting, z-cap/winsorize robustness transforms, and stricter "
                      "liquidity/sector-cap packaging - using the frozen Phase 10-L-A historical "
                      "sector-neutral scored panel. NOT a broad alpha search, NOT a new factor, NOT a "
                      "provider probe."),
        "offline": True, "performs_network": PERFORMS_NETWORK,
        "eodhd_key_visible": bool(os.environ.get(EODHD_KEY_ENV)), "eodhd_key_required": False,
        "panel_source": {
            "path": _PANEL_REL, "n_rows": panel_meta.get("n_rows"),
            "n_tickers": panel_meta.get("n_tickers"), "n_scoreable": panel_meta.get("n_scoreable"),
            "additive_integrity": additive,
            "local_quarterly_book_selfcheck": selfcheck,
        },
        "phase10d_baseline_reproduction": {
            "frozen_10d_composite_sn": frozen, "frozen_baseline_error": ferr,
            "tolerances": REPRO_TOL, "rows": repro_rows, "reproduces_within_tolerance": bool(repro_ok),
        },
        "baseline": {
            "signal": "composite_sn (equal-weight, sector-neutral)", "horizon_days": 63,
            "long_leg": "fcf_to_assets (+1)", "short_leg": "operating_accruals (-1)",
            "ic_mean_63d": base.get("ic_mean"), "ic_t_63d": base.get("ic_t"),
            "quarterly_gross_spread": base.get("quarterly_gross_spread"),
            "quarterly_net_25bps": base.get("quarterly_net_25bps"),
            "quarterly_net_50bps": base.get("quarterly_net_50bps"),
            "quarterly_turnover": base.get("quarterly_turnover"),
            "n_quarters": base.get("n_quarters"),
            "oos_pooled_ic": base.get("oos_pooled_ic"),
            "oos_frac_windows_positive": base.get("oos_frac_windows_positive"),
            "both_cohorts_positive": base.get("both_cohorts_positive"),
            "both_subperiods_positive": base.get("both_subperiods_positive"),
            "top_sector_share": base.get("top_sector_share"),
            "characterisation": ("MODEST / BOUNDARY: net-of-cost quarterly edge is small (net-25bps "
                                 "~+0.004, net-50bps ~+0.001), 63d IC t sits below the 3.0 strong bar; "
                                 "must not be oversold. The SN book runs above the 0.60 raw sector "
                                 "ceiling (a known 10-D/10-F sector-mapping caveat), so the champion "
                                 "test is NON-WORSENING relative to this baseline, not an absolute bar."),
        },
        "champion_test": {
            "type": "strict RELATIVE non-worsening beat test",
            "criteria": ["net-25bps strictly higher than baseline",
                         "net-50bps not worse than baseline",
                         "turnover <= 1.10x baseline (hard reject > 1.50x)",
                         "IC t-stat >= baseline - 0.10",
                         "OOS frac-windows-positive >= baseline",
                         "top-sector share <= baseline",
                         "improvement robust: both cohorts +, both subperiods +, hit-rate not worse, "
                         "not fewer rebalance periods (anti one-period)"],
            "pre_declared_best_weight": best_meta.get("best_weight"),
        },
        "variants_tested": variants,
        "n_variants": len(variants),
        "champion": champion,
        "leg_contribution_summary": _leg_contribution_summary(variants, leg_ics),
        "rejected_variants": rejected,
        "implementation_limits": [
            "Every variant is a reweighting / transform / filter of the SAME two owned quality legs "
            "(fcf_to_assets, operating_accruals); this phase adds NO new factor and does NO broad "
            "alpha search.",
            "IC / OOS / cohort / subperiod are computed with the exact 10-D engine functions on the "
            "frozen panel; sector-cap variants leave the signal (composite_sn) unchanged and only "
            "reshape the traded book, so their IC/OOS equal the baseline by construction.",
            "The sector-neutral book concentration (~0.63) exceeds the 0.60 raw ceiling (known "
            "10-D/10-F 'Unknown'-sector caveat); the concentration criterion is therefore relative "
            "(not worse than baseline), not the absolute 0.60 gate.",
            "leg contribution is a weighted-standalone-IC attribution (directional), not an exact "
            "variance decomposition, because rank-IC is not linear in the legs.",
            "The alpha remains MODEST / BOUNDARY and is not a prediction oracle; this phase does not "
            "change that. It creates no orders, no automation, no broker, no deploy, no Paper Trader "
            "writes.",
        ],
        "next_recommended_phase": _next_plan(decision),
        "constraints_honored": [
            "offline (no network / no key / no provider probe / no build_panel)",
            "owned / local data only (frozen 10-L-A panel + frozen 10-D report)", "no new purchase",
            "no new factor; no broad alpha search", "no Paper Trader writes", "no orders",
            "no automation", "no broker", "no deploy", "no GCP", "no package install",
            "no full regression (targeted tests only)", "no commit", "no push",
        ],
        "safety": {
            "paper_only": True, "owned_local_data_only": True, "no_live_api_calls": True,
            "no_orders": True, "no_automation": True, "no_broker": True, "no_deploy": True,
        },
    }


def _needs_repair_report(rationale, panel_meta, repro_rows, additive, frozen, ferr) -> Dict:
    return {
        "phase": PHASE, "phase_name": PHASE_NAME, "as_of": AS_OF, "decision": DEC_REPAIR,
        "decision_rationale": rationale, "allowed_decisions": list(ALLOWED_DECISIONS),
        "offline": True, "performs_network": PERFORMS_NETWORK,
        "panel_source": {"path": _PANEL_REL, "n_rows": panel_meta.get("n_rows"),
                         "n_tickers": panel_meta.get("n_tickers"),
                         "n_scoreable": panel_meta.get("n_scoreable"), "additive_integrity": additive},
        "phase10d_baseline_reproduction": {"frozen_10d_composite_sn": frozen,
                                           "frozen_baseline_error": ferr, "tolerances": REPRO_TOL,
                                           "rows": repro_rows, "reproduces_within_tolerance": False},
        "baseline": None, "variants_tested": [], "n_variants": 0,
        "champion": {"champion": "baseline_composite_sn_equal_weight", "is_baseline": True,
                     "reason": rationale},
        "leg_contribution_summary": None, "rejected_variants": [],
        "implementation_limits": ["panel failed the integrity/reproduction guard; no variant scoring "
                                  "performed"],
        "next_recommended_phase": _next_plan(DEC_REPAIR),
        "constraints_honored": ["offline", "owned/local data only", "no orders", "no automation",
                                "no broker", "no deploy", "no commit", "no push"],
        "safety": {"paper_only": True, "owned_local_data_only": True, "no_live_api_calls": True,
                   "no_orders": True, "no_automation": True, "no_broker": True, "no_deploy": True},
    }


def _write_empty_csvs(P: _Paths) -> None:
    _write_csv(P.art("scorecard"), ["variant_id"], [])
    _write_csv(P.art("baseline_vs"), ["variant_id"], [])
    _write_csv(P.art("oos"), ["variant_id"], [])
    _write_csv(P.art("cohort"), ["variant_id"], [])
    _write_csv(P.art("turnover"), ["variant_id"], [])
    _write_csv(P.art("sector"), ["variant_id"], [])
    _write_csv(P.art("rejected"), ["variant_id"], [])


def _print_summary(report: Dict) -> None:
    champ = report.get("champion") or {}
    print("[%s] decision=%s | variants=%s | champion=%s | baseline_reproduces=%s"
          % (PHASE, report.get("decision"), report.get("n_variants"),
             ("BASELINE" if champ.get("is_baseline") else champ.get("champion")),
             report.get("phase10d_baseline_reproduction", {}).get("reproduces_within_tolerance")))


def run(out_dir: Optional[Path] = None, *, panel_csv: Optional[Path] = None,
        verbose: bool = True) -> Dict:
    P = _Paths(out_dir)
    P.out.mkdir(parents=True, exist_ok=True)
    log = _Log(verbose)
    try:
        log.step("preflight", "OFFLINE", "no network / no key; reweighting the frozen 10-L-A panel")
        panel_path = Path(panel_csv) if panel_csv else (_REPO_ROOT / _PANEL_REL)
        frozen, ferr = load_frozen_10d(_REPO_ROOT / _PHASE10D_JSON_REL)

        df, panel_meta, perr = load_panel(panel_path)
        if perr is not None or df is None:
            report = _needs_repair_report(perr or "panel load failed", panel_meta or {}, [],
                                          {"additive_ok": False}, frozen, ferr)
            _write_json(P.art("report"), report)
            _write_empty_csvs(P)
            _print_summary(report)
            return report

        additive = additive_integrity(df)
        log.step("integrity", "DONE" if additive["additive_ok"] else "WARN",
                 "max|comp_sn-(z_fcf_sn+z_acc_sn)|=%s over %d rows"
                 % (_r(additive["max_abs_error"], 12), additive["rows_checked"]))

        # Baseline battery + panel reproduction guard.
        base_batt = signal_battery(df, "comp_sn")
        base_book = quarterly_book(df, "comp_sn", cap_frac=None)
        base_probe = {"ic_t": _r(base_batt["ic"].get("ic_t"), 3),
                      "quarterly_net_25bps": _r(base_book.get("net_25bps")),
                      "quarterly_net_50bps": _r(base_book.get("net_50bps")),
                      "quarterly_turnover": _r(base_book.get("avg_turnover"), 4)}
        repro_rows, repro_ok = reproduction_check(base_probe, frozen)
        selfcheck = _selfcheck_book(df, log)
        log.step("reproduce", "DONE",
                 "baseline composite_sn: 63d IC t=%s | qtr net25=%s net50=%s turnover=%s | within_tol=%s"
                 % (base_probe["ic_t"], base_probe["quarterly_net_25bps"],
                    base_probe["quarterly_net_50bps"], base_probe["quarterly_turnover"], repro_ok))

        if not additive["additive_ok"] or not repro_ok or not selfcheck[
                "reproduces_d10_quarterly_backtest"]:
            reasons = []
            if not additive["additive_ok"]:
                reasons.append("z-legs do not additively reconstruct composite_sn")
            if not repro_ok:
                reasons.append("frozen panel does not reproduce the 10-D composite_sn baseline within "
                               "tolerance")
            if not selfcheck["reproduces_d10_quarterly_backtest"]:
                reasons.append("local quarterly book does not reproduce d10.quarterly_backtest")
            rationale = ("panel integrity/reproduction guard failed (%s) - the frozen panel cannot be "
                         "trusted for reweighting; re-run Phase 10-L-A." % "; ".join(reasons))
            report = _needs_repair_report(rationale, panel_meta, repro_rows, additive, frozen, ferr)
            _write_json(P.art("report"), report)
            _write_empty_csvs(P)
            _print_summary(report)
            return report

        leg_ics = _leg_ics(df)
        variants, best_meta = build_variants(df, base_batt, leg_ics, log)
        decision, rationale, champion = decide(variants)

        _write_artifacts(P, variants)
        report = _build_report(decision, rationale, panel_meta, repro_rows, repro_ok, additive,
                               selfcheck, frozen, ferr, variants, champion, best_meta, leg_ics)
        _write_json(P.art("report"), report)
        log.step("artifacts", "DONE", "wrote JSON + 7 CSVs (%d variants)" % len(variants))
        _print_summary(report)
        return report
    except Exception as exc:  # pragma: no cover - defensive
        import traceback
        detail = "%s: %s" % (type(exc).__name__, exc)
        log.step("run", "ERROR", detail)
        report = {"phase": PHASE, "phase_name": PHASE_NAME, "decision": DEC_REPAIR,
                  "decision_rationale": detail,
                  "repro_command": "python research/run_%s.py" % STEM,
                  "traceback": traceback.format_exc(),
                  "safety": {"paper_only": True, "owned_local_data_only": True,
                             "no_live_api_calls": True, "no_orders": True, "no_automation": True,
                             "no_broker": True, "no_deploy": True}}
        try:
            _write_json(P.art("report"), report)
            _write_empty_csvs(P)
        except Exception:
            pass
        return report


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 10-L-B - Historical Quality Composite Reweighting "
                                            "And Robustness Backtest (offline; owned data only)")
    p.add_argument("--out-dir", default=None)
    p.add_argument("--panel-csv", default=None)
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ns = _parse_args(argv)
    report = run(out_dir=ns.out_dir, panel_csv=ns.panel_csv, verbose=not ns.quiet)
    return 0 if report.get("decision") in ALLOWED_DECISIONS else 1


if __name__ == "__main__":
    raise SystemExit(main())
