"""Phase 10-E - Paper-Only Review Harness for the Quarterly Quality Composite.

WHY THIS PHASE EXISTS
    Phase 10-D returned QUARTERLY_QUALITY_COMPOSITE_CONFIRMED_READY_FOR_PAPER_REVIEW - the FIRST signal
    in the entire 8-T -> 10-D research arc to clear the strict 63d gate (IC t=3.07, quarterly net-25bps
    +0.0065 / net-50bps +0.0035 at a realistic 0.60 quarterly turnover, OOS-positive, both cohorts +,
    both subperiods +, sector-robust, sector-neutral edge intact). It is a LEGITIMATE but MODEST,
    boundary-level pass and was dispositioned to HUMAN PAPER review - NOT cleared for live trading.

    Phase 10-E does the one allowed next thing: it builds a PAPER-ONLY HUMAN REVIEW PACKAGE for the
    quarterly SECTOR-NEUTRAL quality composite. It reconstructs the LATEST quarterly cross-section,
    ranks a long/short candidate book by the sector-neutral composite (the default review view),
    explains every score from its two transparent legs, surfaces sector / liquidity / cohort exposure,
    audits the unmapped "Unknown" sector bucket (the known 10-D caveat), flags per-name risks, and lays
    out a quarterly rebalance calendar plus a human approve/reject checklist.

    IT IS NOT a new alpha search, NOT a provider search, NOT order creation, NOT automation, NOT a
    deploy, and NOT (yet) a Paper Trader integration. It writes ONLY metadata CSV/JSON to its own
    research/output directory. It creates NO Paper Trader signals, NO trade decisions, and NO orders.

COMPOSITE (imported verbatim from Phase 10-D - single source of truth; NO re-definition here)
    leg 1: fcf_to_assets        oriented +1   (higher FCF/assets is better)
    leg 2: operating_accruals   oriented -1   (Sloan: higher accruals is worse -> negate)
    comp_sn  = within-month z(sector-neutral leg1) + within-month z(sector-neutral leg2)   [DEFAULT view]
    comp_raw = within-month z(leg1) + within-month z(leg2)                                  [reference]
    Equal weight 1.0 / 1.0; NO optimisation; NO sign-flipping; NO post-hoc selection.

REUSE (single source of truth - nothing re-implemented)
    d10 = run_phase10d_quarterly_quality_composite_validation  (composite definition + build_composite,
                                                                 LEGS, COMP_RAW/COMP_SN, thresholds)
    c10 = run_phase10c_eodhd_quality_oos_validation            (panel build, PIT attach + oriented /
                                                                 sector-neutral columns, helpers)
    b10 = run_phase10b_eodhd_norgate_exhaustive_alpha_factory  (secret-safety audit)
    s8 = d10.s8 (io helpers)   t8 = d10.t8 (logger)

TERMINAL DECISIONS (allowed)
    PAPER_REVIEW_PACKAGE_READY |
    PAPER_REVIEW_PACKAGE_READY_WITH_SECTOR_MAPPING_CAVEAT |
    PAPER_REVIEW_BLOCKED_BY_MISSING_COMPOSITE_INPUTS |
    PAPER_REVIEW_REJECTED_AFTER_POSITION_RECONSTRUCTION |
    HARD_BLOCKER_REQUIRES_USER_ACTION | ERROR_WITH_REPRO_COMMAND
    FORBIDDEN: LIVE_TRADING_READY, ORDER_READY, AUTOMATION_READY,
    STRONG_ALPHA_FOUND_READY_FOR_REVIEW, MISSING_KEY, NO_DATA, NEEDS_PROVIDER, EMPTY_PAYLOAD,
    generic ERROR.

CONSTRAINTS HONORED
    Fully offline (no network / key / provider probe); only fcf_to_assets + operating_accruals used;
    fixed equal weights (no optimisation / no sign-flip); no FMP / AlphaVantage / Polygon / Finnhub; no
    Paper Trader writes; no GCP; NO orders; NO automation; NO live trading; NO broker; no deploy; no
    package install; no full regression (targeted tests only); keys never printed or written; output is
    metadata only. No commit. No push.
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

from research import run_phase10d_quarterly_quality_composite_validation as d10  # noqa: E402
from research import run_phase10c_eodhd_quality_oos_validation as c10            # noqa: E402
from research import run_phase10b_eodhd_norgate_exhaustive_alpha_factory as b10  # noqa: E402

s8 = d10.s8
t8 = d10.t8
_write_json = s8._write_json
_write_csv = s8._write_csv
_read_csv_file = s8._read_csv_file
_read_json = s8._read_json
_round = s8._round
_rel = s8._rel
_finite = c10._finite
_num = c10._num

PHASE = "10-E"
PERFORMS_NETWORK = False

AS_OF = d10.AS_OF
FWD_WINDOWS = d10.FWD_WINDOWS
PRIMARY_HORIZON_D = d10.PRIMARY_HORIZON_D       # 63d / quarterly (carried from 10-D)
RET_PRIMARY = d10.RET_PRIMARY

# Composite is imported verbatim from Phase 10-D - never re-defined here.
LEGS = d10.LEGS
ALLOWED_FAMILIES = d10.ALLOWED_FAMILIES
COMP_RAW = d10.COMP_RAW
COMP_SN = d10.COMP_SN
COMPOSITE_WEIGHTING = d10.COMPOSITE_WEIGHTING
# The SECTOR-NEUTRAL composite is the DEFAULT review view (10-D: trade the sector-neutral version).
DEFAULT_REVIEW_SCORE = COMP_SN

# Book construction (paper review only - these are REVIEW labels, never orders).
N_QUANTILES = 5                                 # quintile long / short book (matches 10-D backtest)
_MIN_BOOK_NAMES = 10                            # need >=2 names per quintile to form a reviewable book
SIDE_LONG = "LONG"
SIDE_SHORT = "SHORT"
SIDE_HOLD = "HOLD"
REVIEW_STATUS = "PAPER_REVIEW_ONLY"

# Risk-flag thresholds (a-priori; transparent).
LOW_LIQ_PCTILE = 0.25                           # bottom-quartile liquidity proxy -> low-liquidity flag
EXTREME_Z = 2.5                                 # |within-quarter z of comp_sn| above this -> extreme
MAX_SECTOR_SHARE = d10.MAX_SECTOR_SHARE         # 0.60 mapped-sector concentration ceiling
UNKNOWN_SECTORS = frozenset({"Unknown", "", "nan", "None"})
# Material Unknown-sector book exposure above which the package carries the sector-mapping caveat.
UNKNOWN_CAVEAT_SHARE = 0.20
N_CALENDAR_QUARTERS = 5                          # current + next 4 quarterly rebalances

# Decisions.
DEC_READY = "PAPER_REVIEW_PACKAGE_READY"
DEC_READY_CAVEAT = "PAPER_REVIEW_PACKAGE_READY_WITH_SECTOR_MAPPING_CAVEAT"
DEC_BLOCKED = "PAPER_REVIEW_BLOCKED_BY_MISSING_COMPOSITE_INPUTS"
DEC_REJECTED = "PAPER_REVIEW_REJECTED_AFTER_POSITION_RECONSTRUCTION"
DEC_HARD_BLOCKER = "HARD_BLOCKER_REQUIRES_USER_ACTION"
DEC_ERROR = "ERROR_WITH_REPRO_COMMAND"
ALLOWED_DECISIONS = (DEC_READY, DEC_READY_CAVEAT, DEC_BLOCKED, DEC_REJECTED, DEC_HARD_BLOCKER, DEC_ERROR)
FORBIDDEN_DECISIONS = ("LIVE_TRADING_READY", "ORDER_READY", "AUTOMATION_READY",
                       "STRONG_ALPHA_FOUND_READY_FOR_REVIEW", "MISSING_KEY", "NO_DATA", "NEEDS_PROVIDER",
                       "EMPTY_PAYLOAD", "ERROR")

# Visible safety badges (req #8 - mandatory).
SAFETY_BADGES = (
    ("PAPER REVIEW ONLY", "This package is for human review only - it is not a tradeable instruction."),
    ("NO ORDERS", "No orders are created, staged, or sent anywhere by this harness."),
    ("NO AUTOMATION", "Nothing is automated; every position is human approve/reject gated."),
    ("HUMAN APPROVAL REQUIRED", "A human must individually approve or reject each candidate."),
    ("NO LIVE TRADING", "Paper-only; no live capital, no broker, no execution."),
    ("NO BROKER", "No broker connection exists or is configured by this phase."),
    ("CREATES NO TRADE DECISIONS", "No Paper Trader signals or trade decisions are written."),
    ("MANUAL REVIEW", "Quarterly rebalance is a manual, reviewed decision - never scheduled execution."),
)

EODHD_KEY_ENV = "EODHD_API_KEY"

_PHASE10D_DIR = (_REPO_ROOT / "research" / "output"
                 / "phase10d_quarterly_quality_composite_validation")

_ARTIFACTS = {
    "report": "phase10e_quarterly_quality_paper_review_harness.json",
    "candidates": "paper_review_candidate_list.csv",
    "book": "paper_review_long_short_book.csv",
    "explain": "paper_review_score_explainability.csv",
    "sector_exp": "paper_review_sector_exposure.csv",
    "liquidity": "paper_review_liquidity_report.csv",
    "unknown_audit": "paper_review_unknown_sector_audit.csv",
    "calendar": "quarterly_rebalance_calendar.csv",
    "turnover": "paper_review_turnover_estimate.csv",
    "risk_flags": "paper_review_risk_flags.csv",
    "checklist": "paper_review_human_checklist.csv",
    "badges": "paper_review_safety_badges.csv",
    "next_plan": "phase10f_next_plan.json",
    "secret_audit": "secret_safety_audit.csv",
}
_REQUIRED_ARTIFACTS = tuple(_ARTIFACTS.keys())


class _Paths:
    def __init__(self, out_dir=None):
        self.out = Path(out_dir) if out_dir else (
            _REPO_ROOT / "research" / "output" / "phase10e_quarterly_quality_paper_review_harness")

    def art(self, key: str) -> Path:
        return self.out / _ARTIFACTS[key]


def _is_unknown(sector) -> bool:
    import pandas as pd
    if sector is None:
        return True
    try:
        if pd.isna(sector):
            return True
    except (TypeError, ValueError):
        pass
    return str(sector).strip() in UNKNOWN_SECTORS


# --------------------------------------------------------------------------- #
# A. Reconstruct the LATEST quarterly cross-section composite scores.
# --------------------------------------------------------------------------- #
def latest_quarter_cross_section(ev, sigcol: str = DEFAULT_REVIEW_SCORE):
    """One scoreable observation per ticker for the most recent calendar quarter present in the panel.
    Scoreable == the composite is computable (both legs present). Returns (cross_section_df, quarter,
    prior_quarter, n_universe, n_scoreable)."""
    import pandas as pd
    if ev is None or getattr(ev, "empty", True) or "entry_date" not in ev.columns:
        return None, None, None, 0, 0
    work = ev.copy()
    work["q"] = work["entry_date"].dt.to_period("Q")
    quarters = sorted(work["q"].dropna().unique())
    if not quarters:
        return None, None, None, 0, 0
    q = quarters[-1]
    prior = quarters[-2] if len(quarters) >= 2 else None
    chunk = work[work["q"] == q].sort_values("entry_date").groupby("ticker", as_index=False).last()
    n_universe = int(len(chunk))
    scoreable = chunk[chunk[sigcol].notna()].copy() if sigcol in chunk.columns else chunk.iloc[0:0].copy()
    return scoreable, q, prior, n_universe, int(len(scoreable))


def _quarter_book(work, q, sigcol: str = DEFAULT_REVIEW_SCORE):
    """The long/short ticker sets for an arbitrary quarter (used for the prior-period turnover estimate).
    Returns (long_set, short_set, n_scoreable)."""
    import pandas as pd
    if work is None or q is None:
        return set(), set(), 0
    chunk = work[work["q"] == q].sort_values("entry_date").groupby("ticker", as_index=False).last()
    chunk = chunk[chunk[sigcol].notna()].copy()
    if len(chunk) < _MIN_BOOK_NAMES:
        return set(), set(), int(len(chunk))
    try:
        qd = pd.qcut(chunk[sigcol].rank(method="first"), N_QUANTILES, labels=False)
    except ValueError:
        return set(), set(), int(len(chunk))
    longs = set(chunk.loc[qd == N_QUANTILES - 1, "ticker"].astype(str))
    shorts = set(chunk.loc[qd == 0, "ticker"].astype(str))
    return longs, shorts, int(len(chunk))


def _availability_dates(norm_csvs: Dict[str, Path], tickers, entry_by_ticker) -> Dict[str, Dict[str, str]]:
    """For each leg, the PIT available_date actually used for each ticker in the cross-section (the most
    recent record with available_date <= the ticker's entry_date). Re-derived from the normalized CSVs
    because the as-of join keeps only the value, not the source date. Returns {feature: {ticker: date}}."""
    import pandas as pd
    out: Dict[str, Dict[str, str]] = {}
    for leg in LEGS:
        feat = leg["feature"]
        out[feat] = {}
        path = norm_csvs.get(leg["family"])
        if not path or not Path(path).is_file():
            continue
        rows = _read_csv_file(path)
        if not rows:
            continue
        nf = pd.DataFrame(rows)
        if "available_date" not in nf.columns:
            continue
        nf["available_date"] = pd.to_datetime(nf["available_date"], errors="coerce")
        nf = nf.dropna(subset=["available_date"])
        by_tk = {tk: g.sort_values("available_date") for tk, g in nf.groupby("ticker")}
        for tk in tickers:
            entry = entry_by_ticker.get(tk)
            g = by_tk.get(tk)
            if g is None or entry is None or pd.isna(entry):
                continue
            elig = g[g["available_date"] <= entry]
            if not elig.empty:
                out[feat][tk] = elig["available_date"].iloc[-1].date().isoformat()
    return out


# --------------------------------------------------------------------------- #
# B. Build the ranked candidate book (paper review only).
# --------------------------------------------------------------------------- #
def build_book(cs, sigcol: str = DEFAULT_REVIEW_SCORE) -> Tuple[object, Dict]:
    """Rank the scoreable cross-section by the (default) sector-neutral composite, assign quintile
    long/short/hold REVIEW labels, and add rank / percentile / within-quarter z. Returns (book_df,
    meta)."""
    import numpy as np
    import pandas as pd
    cs = cs.copy()
    n = int(len(cs))
    meta = {"n_scoreable": n, "quintile_feasible": n >= _MIN_BOOK_NAMES}
    if n == 0:
        return cs, meta
    # rank: 1 = strongest LONG (highest composite). percentile in [0, 100], higher == more long.
    cs["rank_sn"] = cs[sigcol].rank(ascending=False, method="first").astype(int)
    cs["percentile_sn"] = (cs[sigcol].rank(pct=True) * 100.0).round(2)
    if COMP_RAW in cs.columns:
        cs["rank_raw"] = cs[COMP_RAW].rank(ascending=False, method="first").astype(int)
    else:
        cs["rank_raw"] = cs["rank_sn"]
    mu = float(cs[sigcol].mean())
    sd = float(cs[sigcol].std(ddof=0)) or 1.0
    cs["comp_sn_z"] = ((cs[sigcol] - mu) / sd).round(4)
    if meta["quintile_feasible"]:
        qd = pd.qcut(cs[sigcol].rank(method="first"), N_QUANTILES, labels=False)
        cs["quintile"] = (qd + 1).astype(int)         # 1 (short) .. 5 (long)
        cs["review_label"] = np.where(qd == N_QUANTILES - 1, SIDE_LONG,
                                      np.where(qd == 0, SIDE_SHORT, SIDE_HOLD))
    else:
        cs["quintile"] = 0
        cs["review_label"] = SIDE_HOLD
    cs["review_status"] = REVIEW_STATUS
    cs = cs.sort_values("rank_sn").reset_index(drop=True)
    meta["n_long"] = int((cs["review_label"] == SIDE_LONG).sum())
    meta["n_short"] = int((cs["review_label"] == SIDE_SHORT).sum())
    meta["n_hold"] = int((cs["review_label"] == SIDE_HOLD).sum())
    return cs, meta


def _leg_levels(cs):
    """Convenience accessors for the two leg raw levels + oriented within-quarter z, for explainability."""
    import numpy as np
    out = {}
    for leg in LEGS:
        feat = leg["feature"]
        ocol = "o_%s" % feat
        raw = cs[feat] if feat in cs.columns else None
        if ocol in cs.columns and len(cs):
            mu = float(cs[ocol].mean())
            sd = float(cs[ocol].std(ddof=0)) or 1.0
            oz = ((cs[ocol] - mu) / sd)
        else:
            oz = None
        out[feat] = {"raw": raw, "oriented_z": oz, "orientation": leg["orientation"]}
    return out


# --------------------------------------------------------------------------- #
# C. Exposure / risk analytics on the reconstructed book.
# --------------------------------------------------------------------------- #
def sector_exposure(cs) -> Tuple[List[List], Dict]:
    import pandas as pd
    rows: List[List] = []
    summary = {"unknown_book_share": float("nan"), "unknown_long_share": float("nan"),
               "top_long_sector": "", "top_long_sector_share": float("nan"),
               "high_concentration": False, "n_unknown_book": 0}
    if cs is None or getattr(cs, "empty", True):
        return rows, summary
    cs = cs.copy()
    cs["sector_disp"] = cs["sector"].apply(lambda s: "Unknown" if _is_unknown(s) else str(s)) \
        if "sector" in cs.columns else "Unknown"
    book = cs[cs["review_label"].isin([SIDE_LONG, SIDE_SHORT])]
    longs = cs[cs["review_label"] == SIDE_LONG]
    shorts = cs[cs["review_label"] == SIDE_SHORT]
    sectors = sorted(cs["sector_disp"].unique())
    for sct in sectors:
        n_all = int((cs["sector_disp"] == sct).sum())
        n_long = int((longs["sector_disp"] == sct).sum())
        n_short = int((shorts["sector_disp"] == sct).sum())
        n_book = n_long + n_short
        rows.append([sct, n_all, n_long, n_short, n_book,
                     _num(n_long / len(longs)) if len(longs) else "",
                     _num(n_book / len(book)) if len(book) else ""])
    # summary
    n_book = int(len(book))
    n_unknown_book = int(book["sector_disp"].eq("Unknown").sum()) if n_book else 0
    summary["n_unknown_book"] = n_unknown_book
    summary["unknown_book_share"] = (n_unknown_book / n_book) if n_book else float("nan")
    summary["unknown_long_share"] = (int(longs["sector_disp"].eq("Unknown").sum()) / len(longs)
                                     if len(longs) else float("nan"))
    mapped_long = longs[longs["sector_disp"] != "Unknown"]
    if len(mapped_long):
        vc = mapped_long["sector_disp"].value_counts()
        summary["top_long_sector"] = str(vc.index[0])
        summary["top_long_sector_share"] = float(vc.iloc[0] / len(longs))
        summary["high_concentration"] = summary["top_long_sector_share"] >= MAX_SECTOR_SHARE
    return rows, summary


def liquidity_report(cs) -> Tuple[List[List], Dict, float]:
    import pandas as pd
    rows: List[List] = []
    summary = {"low_liq_threshold": float("nan"), "n_low_liq_book": 0, "median_liq": float("nan")}
    thr = float("nan")
    if cs is None or getattr(cs, "empty", True) or "liquidity_proxy" not in cs.columns:
        return rows, summary, thr
    liq = cs["liquidity_proxy"].dropna()
    if liq.empty:
        return rows, summary, thr
    thr = float(liq.quantile(LOW_LIQ_PCTILE))
    summary["low_liq_threshold"] = thr
    summary["median_liq"] = float(liq.median())
    book = cs[cs["review_label"].isin([SIDE_LONG, SIDE_SHORT])]
    summary["n_low_liq_book"] = int((book["liquidity_proxy"] < thr).sum()) if len(book) else 0
    for _, r in cs.iterrows():
        lv = r.get("liquidity_proxy")
        rows.append([r["ticker"], r.get("review_label"), _num(lv),
                     _finite(lv) and lv < thr])
    return rows, summary, thr


def unknown_sector_audit(cs) -> List[List]:
    """One row per Unknown-sector name in the reconstructed book, with the data needed to remediate the
    mapping from OWNED Norgate/EODHD metadata (no new purchase). This is the 10-D caveat made explicit."""
    rows: List[List] = []
    if cs is None or getattr(cs, "empty", True):
        return rows
    for _, r in cs.iterrows():
        if not _is_unknown(r.get("sector")):
            continue
        rows.append([r["ticker"], r.get("review_label"), r.get("rank_sn"),
                     _num(r.get(DEFAULT_REVIEW_SCORE)),
                     "sector unmapped in owned Norgate/EODHD metadata",
                     "map sector from owned Norgate symbol metadata / EODHD fundamentals General::Sector; "
                     "re-run the sector-neutral composite before sizing this name"])
    return rows


def risk_flags(cs, dropped_missing_leg, low_liq_thr, sect_summary) -> List[List]:
    """Per-name risk flags for the reconstructed book + dropped missing-leg names. Covers req #13:
    Unknown sector, low liquidity, missing leg, extreme score, old/new cohort, high concentration."""
    rows: List[List] = []
    high_conc = bool(sect_summary.get("high_concentration"))
    top_sct = sect_summary.get("top_long_sector")
    if cs is not None and not getattr(cs, "empty", True):
        for _, r in cs.iterrows():
            sct = r.get("sector")
            lv = r.get("liquidity_proxy")
            z = r.get("comp_sn_z")
            cohort = str(r.get("cohort")) if "cohort" in cs.columns else ""
            in_top = high_conc and (not _is_unknown(sct)) and str(sct) == str(top_sct) \
                and r.get("review_label") == SIDE_LONG
            rows.append([r["ticker"], r.get("review_label"), str(sct), _is_unknown(sct),
                         _finite(lv) and _finite(low_liq_thr) and lv < low_liq_thr,
                         False, _finite(z) and abs(z) > EXTREME_Z, cohort,
                         cohort == "new", bool(in_top)])
    for tk in dropped_missing_leg:
        rows.append([tk, "DROPPED", "", "", "", True, "", "", "", ""])
    return rows


# --------------------------------------------------------------------------- #
# D. Turnover estimate vs the prior review period.
# --------------------------------------------------------------------------- #
def turnover_estimate(ev, q, prior_q) -> Dict:
    import pandas as pd
    out = {"prior_quarter": str(prior_q) if prior_q is not None else "", "current_quarter": str(q),
           "long_turnover": float("nan"), "short_turnover": float("nan"),
           "book_turnover": float("nan"), "n_long_prior": 0, "n_short_prior": 0,
           "note": ""}
    if prior_q is None:
        out["note"] = "no prior quarter in the panel - turnover cannot be estimated yet"
        return out
    work = ev.copy()
    work["q"] = work["entry_date"].dt.to_period("Q")
    cur_long, cur_short, _ = _quarter_book(work, q)
    pri_long, pri_short, _ = _quarter_book(work, prior_q)
    out["n_long_prior"] = len(pri_long)
    out["n_short_prior"] = len(pri_short)
    if cur_long and pri_long:
        out["long_turnover"] = 1.0 - len(cur_long & pri_long) / max(1, len(cur_long))
    if cur_short and pri_short:
        out["short_turnover"] = 1.0 - len(cur_short & pri_short) / max(1, len(cur_short))
    lt, st = out["long_turnover"], out["short_turnover"]
    if _finite(lt) and _finite(st):
        out["book_turnover"] = (lt + st) / 2.0
    elif _finite(lt):
        out["book_turnover"] = lt
    elif _finite(st):
        out["book_turnover"] = st
    else:
        out["note"] = ("prior quarter had too few scoreable names for a quintile book - turnover "
                       "estimate unavailable")
    return out


# --------------------------------------------------------------------------- #
# E. Quarterly rebalance calendar.
# --------------------------------------------------------------------------- #
def rebalance_calendar(latest_q, as_of: str) -> List[List]:
    """Current + next N quarterly rebalance dates. MANUAL/REVIEWED dates - never scheduled execution."""
    import pandas as pd
    rows: List[List] = []
    if latest_q is None:
        return rows
    try:
        as_of_ts = pd.Timestamp(as_of)
    except Exception:
        as_of_ts = None
    for i in range(N_CALENDAR_QUARTERS):
        q = latest_q + i
        q_start = q.start_time.date().isoformat()
        q_end = q.end_time.date().isoformat()
        # Nominal review/rebalance date: shortly after quarter end, once fundamentals settle. This is a
        # MANUAL review date - nothing executes automatically.
        nominal = (q.end_time.normalize() + pd.Timedelta(days=1)).date().isoformat()
        if i == 0:
            status = "CURRENT_REVIEW"
        elif as_of_ts is not None and q.start_time <= as_of_ts <= q.end_time:
            status = "CURRENT_REVIEW"
        else:
            status = "SCHEDULED_MANUAL_REVIEW"
        rows.append([str(q), q_start, q_end, nominal, status,
                     "manual human review + approve/reject; NO automated execution"])
    return rows


# --------------------------------------------------------------------------- #
# F. Human review checklist.
# --------------------------------------------------------------------------- #
_CHECKLIST = (
    ("1. confirm data freshness", "The leg availability dates are current and precede each entry date.",
     "inspect paper_review_candidate_list.csv: avail_fcf_to_assets / avail_operating_accruals columns"),
    ("2. confirm no leakage", "Every leg value is point-in-time (available_date <= entry_date).",
     "PIT as-of join is enforced upstream; spot-check avail dates < entry/quarter end"),
    ("3. confirm sector exposure", "Sector exposure is acceptable and the Unknown bucket is understood.",
     "review paper_review_sector_exposure.csv + paper_review_unknown_sector_audit.csv"),
    ("4. confirm liquidity", "No low-liquidity name is sized beyond the paper book's comfort.",
     "review paper_review_liquidity_report.csv (low_liquidity flags)"),
    ("5. confirm no order automation", "No orders / automation / broker exist anywhere in this package.",
     "review paper_review_safety_badges.csv; this harness writes metadata only"),
    ("6. manually approve/reject each candidate", "A human signs off on every long/short name.",
     "annotate paper_review_long_short_book.csv per row; nothing proceeds without explicit approval"),
)


def _checklist_rows() -> List[List]:
    return [[step, desc, how, "PENDING_HUMAN"] for step, desc, how in _CHECKLIST]


# --------------------------------------------------------------------------- #
# G. Provenance: read the Phase 10-D confirmed-verdict summary (best-effort, read-only).
# --------------------------------------------------------------------------- #
def _phase10d_provenance(phase10d_dir: Optional[Path]) -> Dict:
    prov = {"available": False, "verdict": None, "ic_t_63d": None, "quarterly_net_25bps": None,
            "quarterly_net_50bps": None, "quarterly_turnover": None, "ready_for_paper_review": None}
    d = Path(phase10d_dir) if phase10d_dir else _PHASE10D_DIR
    rep = d / "phase10d_quarterly_quality_composite_validation.json"
    if not rep.is_file():
        return prov
    try:
        j = _read_json(rep)
    except Exception:
        return prov
    prov["available"] = True
    prov["verdict"] = j.get("composite_verdict")
    prov["ready_for_paper_review"] = j.get("ready_for_paper_review")
    for r in j.get("signal_results", []):
        if r.get("signal") == "composite_raw":
            prov["ic_t_63d"] = r.get("ic_t_63d")
            prov["quarterly_net_25bps"] = r.get("quarterly_net_25bps")
            prov["quarterly_net_50bps"] = r.get("quarterly_net_50bps")
            prov["quarterly_turnover"] = r.get("quarterly_turnover")
            break
    return prov


# --------------------------------------------------------------------------- #
# H. Decision.
# --------------------------------------------------------------------------- #
def decide(book_meta: Dict, sect_summary: Dict) -> Tuple[str, str]:
    n = book_meta.get("n_scoreable", 0)
    if not book_meta.get("quintile_feasible") or n < _MIN_BOOK_NAMES:
        return DEC_REJECTED, ("the reconstructed latest-quarter cross-section has only %d scoreable "
                              "name(s) - too few to form a reviewable quintile long/short book "
                              "(need >=%d)" % (n, _MIN_BOOK_NAMES))
    if book_meta.get("n_long", 0) == 0 or book_meta.get("n_short", 0) == 0:
        return DEC_REJECTED, ("the reconstructed book has an empty long or short side after position "
                              "reconstruction - not reviewable")
    unk = sect_summary.get("unknown_book_share")
    if _finite(unk) and unk >= UNKNOWN_CAVEAT_SHARE:
        return DEC_READY_CAVEAT, ("the paper-review package reconstructs cleanly, but %.0f%% of the book "
                                  "sits in the unmapped 'Unknown' sector bucket - review against the "
                                  "SECTOR-NEUTRAL composite and improve owned sector mapping before "
                                  "sizing (the documented 10-D caveat)" % (unk * 100))
    return DEC_READY, ("the paper-review package reconstructs cleanly with acceptable sector mapping; "
                       "hand the sector-neutral quarterly composite book to a human for approve/reject "
                       "review (paper-only; NO orders; NO automation)")


# --------------------------------------------------------------------------- #
# I. Artifact writers.
# --------------------------------------------------------------------------- #
def write_artifacts(P: _Paths, cs, book_meta, sect_rows, sect_summary, liq_rows, liq_summary,
                    low_liq_thr, unknown_rows, calendar_rows, turn, risk_rows, avail, q) -> bool:
    import pandas as pd

    # 1. paper_review_candidate_list (all scoreable names, ranked by the default sector-neutral score)
    legs = _leg_levels(cs) if cs is not None and len(cs) else {}
    chdr = ["rank_sn", "percentile_sn", "ticker", "review_label", "review_status", "quintile",
            "comp_sn", "comp_raw", "rank_raw", "comp_sn_z", "sector", "sector_is_unknown", "cohort",
            "liquidity_proxy"]
    for leg in LEGS:
        chdr += [leg["feature"], "avail_%s" % leg["feature"]]
    crows = []
    if cs is not None and not getattr(cs, "empty", True):
        for _, r in cs.iterrows():
            row = [r.get("rank_sn"), _num(r.get("percentile_sn")), r["ticker"], r.get("review_label"),
                   r.get("review_status"), r.get("quintile"), _num(r.get(COMP_SN)), _num(r.get(COMP_RAW)),
                   r.get("rank_raw"), _num(r.get("comp_sn_z")),
                   ("Unknown" if _is_unknown(r.get("sector")) else str(r.get("sector"))),
                   _is_unknown(r.get("sector")),
                   str(r.get("cohort")) if "cohort" in cs.columns else "", _num(r.get("liquidity_proxy"))]
            for leg in LEGS:
                feat = leg["feature"]
                row += [_num(r.get(feat)), avail.get(feat, {}).get(str(r["ticker"]), "")]
            crows.append(row)
    _write_csv(P.art("candidates"), chdr, crows)

    # 2. paper_review_long_short_book (LONG / SHORT names only - the reviewable book)
    bhdr = ["side", "rank_sn", "ticker", "comp_sn", "comp_raw", "sector", "sector_is_unknown", "cohort",
            "liquidity_proxy", "review_status"]
    for leg in LEGS:
        bhdr += [leg["feature"], "avail_%s" % leg["feature"]]
    brows = []
    if cs is not None and not getattr(cs, "empty", True):
        bk = cs[cs["review_label"].isin([SIDE_LONG, SIDE_SHORT])]
        for _, r in bk.iterrows():
            row = [r.get("review_label"), r.get("rank_sn"), r["ticker"], _num(r.get(COMP_SN)),
                   _num(r.get(COMP_RAW)),
                   ("Unknown" if _is_unknown(r.get("sector")) else str(r.get("sector"))),
                   _is_unknown(r.get("sector")),
                   str(r.get("cohort")) if "cohort" in cs.columns else "", _num(r.get("liquidity_proxy")),
                   REVIEW_STATUS]
            for leg in LEGS:
                feat = leg["feature"]
                row += [_num(r.get(feat)), avail.get(feat, {}).get(str(r["ticker"]), "")]
            brows.append(row)
    _write_csv(P.art("book"), bhdr, brows)

    # 3. paper_review_score_explainability (how each composite score decomposes into its two legs)
    ehdr = ["ticker", "review_label", "comp_sn", "comp_raw",
            "fcf_to_assets_level", "fcf_to_assets_oriented_z",
            "operating_accruals_level", "operating_accruals_oriented_z", "dominant_leg"]
    erows = []
    if cs is not None and not getattr(cs, "empty", True):
        fcf_z = legs.get("fcf_to_assets", {}).get("oriented_z")
        acc_z = legs.get("operating_accruals", {}).get("oriented_z")
        for i, (_, r) in enumerate(cs.iterrows()):
            fz = float(fcf_z.iloc[i]) if fcf_z is not None else float("nan")
            az = float(acc_z.iloc[i]) if acc_z is not None else float("nan")
            dom = "fcf_to_assets" if (_finite(fz) and _finite(az) and abs(fz) >= abs(az)) else \
                  ("operating_accruals" if _finite(az) else "n/a")
            erows.append([r["ticker"], r.get("review_label"), _num(r.get(COMP_SN)), _num(r.get(COMP_RAW)),
                          _num(r.get("fcf_to_assets")), _num(fz),
                          _num(r.get("operating_accruals")), _num(az), dom])
    _write_csv(P.art("explain"), ehdr, erows)

    # 4. paper_review_sector_exposure
    _write_csv(P.art("sector_exp"),
               ["sector", "n_in_universe", "n_long", "n_short", "n_book", "long_share", "book_share"],
               sect_rows or [["", 0, 0, 0, 0, "", ""]])

    # 5. paper_review_liquidity_report
    _write_csv(P.art("liquidity"), ["ticker", "review_label", "liquidity_proxy", "low_liquidity_flag"],
               liq_rows or [["", "", "", ""]])

    # 6. paper_review_unknown_sector_audit
    _write_csv(P.art("unknown_audit"),
               ["ticker", "review_label", "rank_sn", "comp_sn", "issue", "recommended_remediation"],
               unknown_rows or [["", "", "", "", "no Unknown-sector names in the reconstructed book",
                                 "no remediation needed"]])

    # 7. quarterly_rebalance_calendar
    _write_csv(P.art("calendar"),
               ["quarter", "quarter_start", "quarter_end", "nominal_review_date", "status", "note"],
               calendar_rows or [["", "", "", "", "", ""]])

    # 8. paper_review_turnover_estimate
    _write_csv(P.art("turnover"),
               ["current_quarter", "prior_quarter", "long_turnover", "short_turnover", "book_turnover",
                "n_long_prior", "n_short_prior", "note"],
               [[turn.get("current_quarter"), turn.get("prior_quarter"), _num(turn.get("long_turnover")),
                 _num(turn.get("short_turnover")), _num(turn.get("book_turnover")),
                 turn.get("n_long_prior"), turn.get("n_short_prior"), turn.get("note")]])

    # 9. paper_review_risk_flags
    _write_csv(P.art("risk_flags"),
               ["ticker", "review_label", "sector", "unknown_sector", "low_liquidity", "missing_leg",
                "extreme_score", "cohort", "is_new_cohort", "in_concentrated_sector"],
               risk_rows or [["", "", "", "", "", "", "", "", "", ""]])

    # 10. paper_review_human_checklist
    _write_csv(P.art("checklist"), ["step", "what_to_confirm", "how_to_verify", "status"],
               _checklist_rows())

    # 11. paper_review_safety_badges
    _write_csv(P.art("badges"), ["badge", "meaning"], [[b, m] for b, m in SAFETY_BADGES])

    # 12. secret_safety_audit
    sec_rows, clean = b10._secret_safety_audit(P.out)
    _write_csv(P.art("secret_audit"), ["file", "clean", "keyed_url_marker", "key_value_present"],
               [[r["file"], r["clean"], r["keyed_url_marker"], r["key_value_present"]] for r in sec_rows])
    return clean


def _phase10f_plan(decision: str, sect_summary: Dict) -> Dict:
    if decision in (DEC_READY, DEC_READY_CAVEAT):
        nxt = ("Phase 10-F: run the HUMAN approve/reject gate over paper_review_long_short_book.csv. On "
               "approval, build a PAPER-ONLY position tracker that marks the approved sector-neutral "
               "quarterly book to market each quarter and compares realised vs expected net-of-25bps "
               "spread - still NO orders, NO automation, NO broker, NO live trading, NO deploy. In "
               "parallel, close the 'Unknown' sector-mapping gap from OWNED Norgate/EODHD metadata and "
               "re-rank. No new data purchase.")
        cmd = ("review research/output/phase10e_quarterly_quality_paper_review_harness/"
               "paper_review_long_short_book.csv")
    elif decision == DEC_REJECTED:
        nxt = ("Phase 10-F: the latest quarter did not reconstruct a reviewable book (too few scoreable "
               "names this quarter). Re-run at the next quarterly earnings cluster / after the next "
               "EODHD refresh; widen the as-of window if a full quarter has not yet reported. No new "
               "data purchase.")
        cmd = ("python research/run_phase10e_quarterly_quality_paper_review_harness.py   "
               "# re-run after the next quarterly reporting cluster")
    elif decision == DEC_BLOCKED:
        nxt = ("Phase 10-F: a composite input is missing (panel or a normalized leg CSV). Rebuild the "
               "Phase 10-B normalized leads + the Norgate panel, then re-run this harness. No new data "
               "purchase.")
        cmd = "python research/run_phase10d_quarterly_quality_composite_validation.py"
    else:
        nxt = ("Phase 10-F: resolve the hard blocker noted in the report, then re-run this harness.")
        cmd = ("review research/output/phase10e_quarterly_quality_paper_review_harness/"
               "phase10e_quarterly_quality_paper_review_harness.json")
    return {"phase": "10-F", "from_decision": decision, "next_step": nxt, "exact_next_command": cmd,
            "unknown_book_share": _round(sect_summary.get("unknown_book_share"), 4),
            "constraints": ["owned data only; no new purchase without explicit user opt-in", "paper-only",
                            "NO orders", "NO automation", "NO live trading", "NO broker", "no deploy",
                            "no Paper Trader writes", "no commit", "no push"]}


# --------------------------------------------------------------------------- #
# J. Report + orchestration.
# --------------------------------------------------------------------------- #
def _build_report(decision, reason, book_meta, sect_summary, liq_summary, turn, q, prior_q, n_universe,
                  comp_cov, n_events, n_tickers, key_visible, leak_clean, provenance, as_of) -> Dict:
    return {
        "phase": PHASE, "as_of": as_of, "decision": decision, "decision_rationale": reason,
        "allowed_decisions": list(ALLOWED_DECISIONS), "forbidden_decisions": list(FORBIDDEN_DECISIONS),
        "objective": ("build a PAPER-ONLY human review package for the quarterly SECTOR-NEUTRAL quality "
                      "composite (fcf_to_assets long + operating_accruals short) - NOT alpha search, NOT "
                      "provider search, NOT orders, NOT automation, NOT a Paper Trader integration"),
        "performs_network": PERFORMS_NETWORK, "offline": True,
        "eodhd_key_visible": bool(key_visible), "eodhd_key_required": False,
        "creates_paper_trader_signals": False, "creates_trade_decisions": False,
        "creates_orders": False, "creates_automation": False, "wrote_to_paper_trader": False,
        "live_trading": False, "broker_connected": False, "deploy": False,
        "composite_source": "imported verbatim from Phase 10-D (run_phase10d_quarterly_quality_composite"
                            "_validation.build_composite); no re-definition",
        "composite_legs": [l["feature"] for l in LEGS], "leg_families": list(ALLOWED_FAMILIES),
        "composite_weighting": COMPOSITE_WEIGHTING, "optimised_weights": False, "sign_flipping": False,
        "default_review_score": DEFAULT_REVIEW_SCORE, "default_review_score_is_sector_neutral": True,
        "primary_horizon_days": PRIMARY_HORIZON_D,
        "phase10d_provenance": provenance,
        "panel_events": n_events, "panel_tickers": n_tickers, "composite_coverage_events": comp_cov,
        "latest_quarter": str(q) if q is not None else "", "prior_quarter": str(prior_q) if prior_q
        is not None else "",
        "latest_quarter_universe_names": n_universe,
        "latest_quarter_scoreable_names": book_meta.get("n_scoreable", 0),
        "book": {"n_long": book_meta.get("n_long", 0), "n_short": book_meta.get("n_short", 0),
                 "n_hold": book_meta.get("n_hold", 0)},
        "sector_exposure": {"unknown_book_share": _round(sect_summary.get("unknown_book_share"), 4),
                            "unknown_long_share": _round(sect_summary.get("unknown_long_share"), 4),
                            "n_unknown_book": sect_summary.get("n_unknown_book", 0),
                            "top_long_sector": sect_summary.get("top_long_sector"),
                            "top_long_sector_share": _round(sect_summary.get("top_long_sector_share"), 4),
                            "high_concentration": bool(sect_summary.get("high_concentration"))},
        "liquidity": {"low_liq_threshold": _round(liq_summary.get("low_liq_threshold"), 4),
                      "median_liq": _round(liq_summary.get("median_liq"), 4),
                      "n_low_liq_book": liq_summary.get("n_low_liq_book", 0),
                      "low_liq_pctile": LOW_LIQ_PCTILE},
        "estimated_turnover_vs_prior_quarter": {
            "long_turnover": _round(turn.get("long_turnover"), 4),
            "short_turnover": _round(turn.get("short_turnover"), 4),
            "book_turnover": _round(turn.get("book_turnover"), 4), "note": turn.get("note")},
        "safety_badges": [b for b, _m in SAFETY_BADGES],
        "secret_safety_leak_scan_clean": leak_clean,
        "api_key_printed": False, "api_key_written_to_disk": False,
        "required_artifacts": [_ARTIFACTS[k] for k in _REQUIRED_ARTIFACTS],
        "exact_next_command": _phase10f_plan(decision, sect_summary)["exact_next_command"],
        "constraints_honored": ["offline (no network/key/provider probe)", "only fcf_to_assets + "
                                "operating_accruals used", "composite imported from 10-D; no "
                                "re-definition; no optimisation; no sign-flip",
                                "no FMP/AlphaVantage/Polygon/Finnhub", "no Paper Trader writes", "no GCP",
                                "NO orders", "NO automation", "NO live trading", "NO broker", "no deploy",
                                "no package install", "no full regression", "no commit", "no push",
                                "no key printed/written"],
    }


def _print_summary(report: Dict) -> None:
    b = report.get("book", {})
    se = report.get("sector_exposure", {})
    print("[10-E] decision=%s | offline=%s | latest_q=%s | scoreable=%s | long=%s short=%s hold=%s | "
          "unknown_book_share=%s | wrote_pt=%s orders=%s automation=%s | leak_clean=%s"
          % (report.get("decision"), report.get("offline"), report.get("latest_quarter"),
             report.get("latest_quarter_scoreable_names"), b.get("n_long"), b.get("n_short"),
             b.get("n_hold"), se.get("unknown_book_share"), report.get("wrote_to_paper_trader"),
             report.get("creates_orders"), report.get("creates_automation"),
             report.get("secret_safety_leak_scan_clean")))


def run(out_dir: Optional[Path] = None, *, ev=None, norm_csvs: Optional[Dict[str, Path]] = None,
        panel_csv: Optional[Path] = None, phase10d_dir: Optional[Path] = None, as_of: str = AS_OF,
        verbose: bool = True) -> Dict:
    P = _Paths(out_dir)
    P.out.mkdir(parents=True, exist_ok=True)
    log = t8._Log(verbose)
    try:
        import pandas as pd  # noqa: F401
        key_visible = bool(os.environ.get(EODHD_KEY_ENV))  # context only; NEVER printed or written
        log.step("preflight", "OFFLINE", "paper-review packaging only; no network / no key required; "
                 "EODHD key visible=%s" % key_visible)

        provenance = _phase10d_provenance(phase10d_dir)
        log.step("provenance", "DONE" if provenance.get("available") else "ABSENT",
                 "Phase 10-D verdict=%s ready=%s" % (provenance.get("verdict"),
                                                     provenance.get("ready_for_paper_review")))

        # 1. Panel (reuse 10-C/10-D build verbatim).
        if ev is None:
            ev, panel_ok, stats = c10.build_panel(as_of, panel_csv, log)
        else:
            panel_ok = not getattr(ev, "empty", True)
            stats = {"events_usable": int(len(ev)) if panel_ok else 0,
                     "tickers_usable": int(ev["ticker"].nunique()) if panel_ok else 0}
        if not panel_ok:
            return _finish_blocker(P, log, DEC_BLOCKED, ("the Norgate survivorship-free panel is empty - "
                                   "the composite cannot be reconstructed."),
                                   "python research/run_phase10d_quarterly_quality_composite_"
                                   "validation.py", key_visible, as_of, provenance)
        n_events = int(stats.get("events_usable", 0) or len(ev))
        n_tickers = int(stats.get("tickers_usable", 0) or ev["ticker"].nunique())

        # 2. Legs present?
        norm_csvs = norm_csvs or c10._default_norm_csvs()
        missing = [l["feature"] for l in LEGS if not Path(norm_csvs.get(l["family"], "")).is_file()]
        if missing:
            return _finish_blocker(P, log, DEC_BLOCKED, ("missing Phase 10-B normalized leg CSV(s): %s - "
                                   "the composite inputs are unavailable." % ", ".join(missing)),
                                   "python research/run_phase10b_eodhd_norgate_exhaustive_alpha_"
                                   "factory.py --live", key_visible, as_of, provenance)

        # 3. Attach legs + build the composite (single source of truth = 10-D).
        ev, cols = c10.attach_signals(ev, norm_csvs, log)
        ev, comp_cov, _rl, _sl = d10.build_composite(ev, cols, log)
        if comp_cov == 0:
            return _finish_blocker(P, log, DEC_BLOCKED, ("the two quality legs do not co-occur on any "
                                   "event - the composite cannot be reconstructed."),
                                   "python research/run_phase10c_eodhd_quality_oos_validation.py",
                                   key_visible, as_of, provenance)

        # 4. Reconstruct the latest quarterly cross-section + book.
        cs, q, prior_q, n_universe, n_scoreable = latest_quarter_cross_section(ev)
        full_work = ev.copy()
        full_work["q"] = full_work["entry_date"].dt.to_period("Q")
        q_universe = full_work[full_work["q"] == q].sort_values("entry_date").groupby(
            "ticker", as_index=False).last() if q is not None else None
        dropped_missing_leg = []
        if q_universe is not None and COMP_SN in q_universe.columns:
            dropped_missing_leg = sorted(str(t) for t in
                                         q_universe.loc[q_universe[COMP_SN].isna(), "ticker"].tolist())
        cs, book_meta = build_book(cs)
        log.step("book", "DONE", "latest quarter %s: %d scoreable / %d universe -> long=%s short=%s hold=%s"
                 % (str(q), n_scoreable, n_universe, book_meta.get("n_long"), book_meta.get("n_short"),
                    book_meta.get("n_hold")))

        # 5. Exposure / risk / turnover / calendar / availability analytics.
        sect_rows, sect_summary = sector_exposure(cs)
        liq_rows, liq_summary, low_liq_thr = liquidity_report(cs)
        unknown_rows = unknown_sector_audit(cs)
        risk_rows = risk_flags(cs, dropped_missing_leg, low_liq_thr, sect_summary)
        turn = turnover_estimate(ev, q, prior_q)
        calendar_rows = rebalance_calendar(q, as_of)
        entry_by_ticker = {}
        tickers = []
        if cs is not None and not getattr(cs, "empty", True):
            tickers = [str(t) for t in cs["ticker"].tolist()]
            entry_by_ticker = {str(r["ticker"]): r["entry_date"] for _, r in cs.iterrows()}
        avail = _availability_dates(norm_csvs, tickers, entry_by_ticker)

        # 6. Decision (packaging readiness; NOT a re-validation of the alpha).
        decision, reason = decide(book_meta, sect_summary)
        if decision not in ALLOWED_DECISIONS or decision in FORBIDDEN_DECISIONS:
            decision = DEC_HARD_BLOCKER

        # 7. Artifacts + report.
        leak_clean = write_artifacts(P, cs, book_meta, sect_rows, sect_summary, liq_rows, liq_summary,
                                     low_liq_thr, unknown_rows, calendar_rows, turn, risk_rows, avail, q)
        report = _build_report(decision, reason, book_meta, sect_summary, liq_summary, turn, q, prior_q,
                               n_universe, comp_cov, n_events, n_tickers, key_visible, leak_clean,
                               provenance, as_of)
        _write_json(P.art("report"), report)
        _write_json(P.art("next_plan"), _phase10f_plan(decision, sect_summary))
        log.step("artifacts", "DONE", "wrote %d artifacts" % len(_REQUIRED_ARTIFACTS))
        _print_summary(report)
        return report
    except Exception as exc:  # pragma: no cover - defensive
        import traceback
        detail = "%s: %s" % (type(exc).__name__, exc)
        log.step("run", "ERROR", detail)
        report = {"phase": PHASE, "decision": DEC_ERROR, "decision_rationale": detail,
                  "repro_command": "python research/run_phase10e_quarterly_quality_paper_review_harness.py",
                  "traceback": traceback.format_exc()}
        try:
            P.out.mkdir(parents=True, exist_ok=True)
            _write_json(P.art("report"), report)
        except Exception:
            pass
        return report


def _finish_blocker(P: _Paths, log, decision: str, detail: str, fix_cmd: str, key_visible: bool,
                    as_of: str, provenance: Dict) -> Dict:
    log.step("blocker", decision, detail)
    report = {"phase": PHASE, "as_of": as_of, "decision": decision, "decision_rationale": detail,
              "exact_next_command": fix_cmd, "allowed_decisions": list(ALLOWED_DECISIONS),
              "forbidden_decisions": list(FORBIDDEN_DECISIONS), "offline": True,
              "performs_network": PERFORMS_NETWORK, "eodhd_key_visible": bool(key_visible),
              "api_key_printed": False, "api_key_written_to_disk": False,
              "creates_paper_trader_signals": False, "creates_orders": False,
              "creates_automation": False, "wrote_to_paper_trader": False, "live_trading": False,
              "composite_legs": [l["feature"] for l in LEGS], "phase10d_provenance": provenance,
              "safety_badges": [b for b, _m in SAFETY_BADGES]}
    try:
        _write_csv(P.art("badges"), ["badge", "meaning"], [[b, m] for b, m in SAFETY_BADGES])
        _write_json(P.art("next_plan"), {"phase": "10-F", "from_decision": decision,
                                         "exact_next_command": fix_cmd})
        sec_rows, _clean = b10._secret_safety_audit(P.out)
        _write_csv(P.art("secret_audit"), ["file", "clean", "keyed_url_marker", "key_value_present"],
                   [[r["file"], r["clean"], r["keyed_url_marker"], r["key_value_present"]]
                    for r in sec_rows])
        _write_json(P.art("report"), report)
    except Exception:
        pass
    print("[10-E] decision=%s | %s" % (decision, detail))
    return report


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Phase 10-E - Paper-Only Review Harness for the Quarterly Quality Composite")
    p.add_argument("--out-dir", default=None)
    p.add_argument("--panel-csv", default=None)
    p.add_argument("--phase10d-dir", default=None)
    p.add_argument("--as-of", default=AS_OF)
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ns = _parse_args(argv)
    report = run(out_dir=ns.out_dir, panel_csv=ns.panel_csv, phase10d_dir=ns.phase10d_dir,
                 as_of=ns.as_of, verbose=not ns.quiet)
    return 0 if report.get("decision") in ALLOWED_DECISIONS else 1


if __name__ == "__main__":
    raise SystemExit(main())
