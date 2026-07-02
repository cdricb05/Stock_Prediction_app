"""Phase 10-L-A - Historical Sector-Neutral Scored Panel Reconstruction.

WHY THIS PHASE EXISTS
    Phase 10-K asked whether the Phase 10-D quarterly quality composite could be IMPROVED with narrow,
    defensible changes (leg re-weightings, robustness transforms, packaging filters). Its decisive
    finding was BASELINE_REMAINS_CHAMPION - not because the baseline is unbeatable, but because the
    frozen 10-D / 10-F / 10-H outputs contain ONLY (a) backtested summary metrics for four FIXED signals
    (composite_sn, composite_raw, fcf_to_assets, operating_accruals) and (b) the latest single 2026Q2
    cross-section. They do NOT contain the historical per-(month, ticker) sector-neutral scored panel
    with forward 63-day returns. Without that panel, any 60/40 / 40/60 / z-cap / winsorize / stricter-
    filter test is un-backtestable and was correctly reported INSUFFICIENT_INPUTS.

    Phase 10-L-A removes that blocker. It is a DATA-LINEAGE / REPRODUCIBILITY phase, NOT an alpha search.
    Phase 10-D already reconstructs the full per-event panel IN MEMORY every run (via the 10-C panel
    build + as-of signal attach + the equal-weight composite) but never persists it. This phase runs
    that exact offline pipeline once and FREEZES the resulting per-(month, ticker) panel to disk - the
    two raw within-month z-legs, the two sector-neutral within-month z-legs, composite_raw, composite_sn,
    sector, cohort, liquidity proxy, and the forward 63d excess return - then REPRODUCES the frozen 10-D
    composite_sn baseline FROM the persisted file to prove the panel is faithful and sufficient for a
    future Phase 10-L-B reweighting / robustness harness.

    THIS PHASE MUST NOT: search for new alpha, test new factors, declare a new champion, add a provider,
    make a live API call, touch the network, touch GCP, touch the Paper Trader, create orders, or
    implement automation. The alpha remains MODEST / BOUNDARY (10-D composite_sn: 63d IC t~2.665,
    quarterly net-25bps ~+0.00401, net-50bps ~+0.00095) and is not oversold here.

RECONSTRUCTION SOURCE (single source of truth - nothing re-implemented)
    c10 = run_phase10c_eodhd_quality_oos_validation        (build_panel, attach_signals, _eval)
    d10 = run_phase10d_quarterly_quality_composite_validation (build_composite, quarterly_backtest,
                                                               walk_forward_h, LEGS, COMP_RAW/COMP_SN)
    x8  = run_phase8x_autonomous_strong_alpha_discovery     (_within_month_z; s8/t8 IO + scoring core)
    b10 = run_phase10b_eodhd_norgate_exhaustive_alpha_factory (owned normalized leg CSVs)

REPRODUCTION DECISIONS (allowed)
    PANEL_RECONSTRUCTION_READY | PANEL_RECONSTRUCTION_PARTIAL | PANEL_RECONSTRUCTION_FAILED |
    NEEDS_PHASE_INPUT_REPAIR

CONSTRAINTS HONORED
    Fully offline (reads only the owned/local gitignored expanded price panel + owned EODHD normalized
    quality leg CSVs that 10-C/10-D already read; no network, no key, no provider probe); paper-only;
    owned-local-data only; no new purchase; no Paper Trader writes; NO orders; NO automation; NO broker;
    NO deploy; NO GCP; no package install; no full regression (targeted tests only); output is metadata /
    a frozen research panel only. No commit. No push.
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
from research import run_phase8x_autonomous_strong_alpha_discovery as x8         # noqa: E402

PHASE = "10-L-A"
PHASE_NAME = "Historical Sector-Neutral Scored Panel Reconstruction"
STEM = "phase10l_historical_sector_neutral_scored_panel_reconstruction"
PERFORMS_NETWORK = False

AS_OF = c10.AS_OF                                     # "2026-06-26" (data-vintage snapshot)
COMP_RAW = d10.COMP_RAW                               # "comp_raw"
COMP_SN = d10.COMP_SN                                 # "comp_sn"
RET_PRIMARY = "fwd_exc_63"                            # the quarterly (63d) forward excess return
WF_TRAIN = d10.WF_TRAIN_MONTHS                        # 24
WF_TEST = d10.WF_TEST_MONTHS                          # 6
WF_STEP = d10.WF_STEP_MONTHS                          # 6

# Decisions.
DEC_READY = "PANEL_RECONSTRUCTION_READY"
DEC_PARTIAL = "PANEL_RECONSTRUCTION_PARTIAL"
DEC_FAILED = "PANEL_RECONSTRUCTION_FAILED"
DEC_NEEDS_REPAIR = "NEEDS_PHASE_INPUT_REPAIR"
ALLOWED_DECISIONS = (DEC_READY, DEC_PARTIAL, DEC_FAILED, DEC_NEEDS_REPAIR)

# Reproduction tolerances (a-priori; from the brief - never tuned to a result).
TOL = {"ic_t": 0.25, "net_25bps": 0.0015, "net_50bps": 0.0015, "turnover": 0.10}
# Directional conclusion the reproduction must preserve: MODEST / BOUNDARY but positive quarterly net25.
MODEST_IC_T_LO, MODEST_IC_T_HI = 2.0, 3.0
_SELFCHECK_TOL = 1e-6                                 # additive z-leg reconstruction of the composites

EODHD_KEY_ENV = "EODHD_API_KEY"

# Frozen 10-D panel-build inputs to inspect (owned / local; gitignored data + prior scripts).
_INPUT_SCRIPTS = [
    "research/run_phase10b_eodhd_norgate_exhaustive_alpha_factory.py",
    "research/run_phase10c_eodhd_quality_oos_validation.py",
    "research/run_phase10d_quarterly_quality_composite_validation.py",
    "research/run_phase10k_quarterly_quality_composite_alpha_improvement_harness.py",
]
_INPUT_DATA = [
    "research/data/eodhd/normalized/eod_prices/expanded_price_panel.csv",
    "research/data/eodhd/normalized/eodhd_fcf_to_assets/fcf_to_assets.csv",
    "research/data/eodhd/normalized/eodhd_operating_accruals/operating_accruals.csv",
]
_PHASE10D_JSON = ("research/output/phase10d_quarterly_quality_composite_validation/"
                  "phase10d_quarterly_quality_composite_validation.json")

# The frozen historical panel schema (spec order). Each entry: (column, maps_from, dtype, note).
PANEL_SCHEMA: Tuple[Tuple[str, str, str, str], ...] = (
    ("as_of_date", "constant AS_OF", "date",
     "data-vintage snapshot the panel was reconstructed at; all fundamentals attached point-in-time "
     "(available_date <= rebalance_date, no lookahead)"),
    ("rebalance_date", "ev.entry_date", "date",
     "per-observation scoring / entry date; the cross-section (month/quarter) axis for all IC and "
     "backtest computations"),
    ("ticker", "ev.ticker", "str", "issuer symbol (Norgate survivorship-free universe)"),
    ("sector", "ev.sector", "str", "GICS-style sector; 'Unknown' where owned metadata is unmapped"),
    ("cohort", "ev.cohort", "str", "'old' (original ~299) vs 'new' (8-V expanded) scoreable cohort"),
    ("is_new_cohort", "ev.cohort == 'new'", "bool", "boolean form of cohort"),
    ("liquidity_proxy", "ev.liquidity_proxy", "float", "dollar-volume liquidity proxy"),
    ("fcf_to_assets", "ev.fcf_to_assets", "float", "raw point-in-time fundamental level (un-oriented)"),
    ("operating_accruals", "ev.operating_accruals", "float",
     "raw point-in-time fundamental level (un-oriented)"),
    ("fcf_to_assets_raw", "within_month_z(o_fcf_to_assets)", "float",
     "within-month z of the ORIENTED (+1) non-sector-neutral fcf leg; additive building block of "
     "composite_raw"),
    ("operating_accruals_raw", "within_month_z(o_operating_accruals)", "float",
     "within-month z of the ORIENTED (-1, Sloan-negated) non-sector-neutral accruals leg; additive "
     "building block of composite_raw"),
    ("fcf_to_assets_sector_neutral_z", "within_month_z(o_fcf_to_assets__sn)", "float",
     "within-month z of the oriented + within-month x sector de-meaned fcf leg; additive building block "
     "of composite_sn"),
    ("operating_accruals_sector_neutral_z", "within_month_z(o_operating_accruals__sn)", "float",
     "within-month z of the oriented + within-month x sector de-meaned accruals leg; additive building "
     "block of composite_sn"),
    ("operating_accruals_oriented_sector_neutral_z", "within_month_z(o_operating_accruals__sn)", "float",
     "identical to operating_accruals_sector_neutral_z: the pipeline's sector-neutral accruals leg is "
     "oriented (-1) BEFORE the within-month x sector de-mean, so the oriented and sector-neutral z "
     "coincide by construction"),
    ("composite_sn", "ev.comp_sn", "float",
     "sector-neutral equal-weight composite = fcf_to_assets_sector_neutral_z + "
     "operating_accruals_sector_neutral_z; the honest 10-D baseline for the sector-neutral book"),
    ("composite_raw", "ev.comp_raw", "float",
     "raw equal-weight composite = fcf_to_assets_raw + operating_accruals_raw; DIAGNOSTIC only (not the "
     "champion for a sector-neutral book)"),
    ("forward_63d_return", "ev.fwd_exc_63", "float",
     "forward 63-trading-day (quarterly) benchmark-excess return from rebalance_date"),
    ("forward_63d_return_start_date", "ev.entry_date", "date", "== rebalance_date"),
    ("forward_63d_return_end_date", "(not persisted upstream)", "date",
     "the exact calendar END date of the 63-trading-day window is not persisted by the 10-C/10-D "
     "builder (only the realized 63d excess return is); requires the per-ticker trading calendar and is "
     "not needed for reweighting / IC tests"),
    ("has_forward_return", "ev.fwd_exc_63 notna", "bool", "whether a forward 63d return is present"),
    ("source_phase", "constant", "str", "10-B(normalized legs) + 10-C(panel) + 10-D(composite)"),
    ("data_quality_flag", "derived", "str",
     "OK | OK_UNKNOWN_SECTOR | MISSING_COMPOSITE | MISSING_FWD_RETURN"),
)
PANEL_COLUMNS = [c[0] for c in PANEL_SCHEMA]
# Columns that MUST be reconstructed with data for the panel to support 10-L-B (end_date is optional).
_CRITICAL_COLUMNS = [
    "rebalance_date", "ticker", "sector", "cohort", "liquidity_proxy",
    "fcf_to_assets_raw", "operating_accruals_raw",
    "fcf_to_assets_sector_neutral_z", "operating_accruals_sector_neutral_z",
    "composite_sn", "composite_raw", "forward_63d_return",
]
_OPTIONAL_BLANK_COLUMNS = ["forward_63d_return_end_date"]

_ARTIFACTS = {
    "report": "%s.json" % STEM,
    "panel": "historical_sector_neutral_scored_panel.csv",
    "schema": "panel_schema.csv",
    "coverage": "panel_coverage_summary.csv",
    "repro": "phase10d_reproduction_check.csv",
    "missing": "missing_fields_report.csv",
    "quality": "data_quality_report.csv",
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
        # Tolerant of the reused engine loggers' extra positional/keyword args (e.g. count=...).
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


def _fnum(x) -> str:
    """Full-precision float for the frozen panel (so reproduction is exact); blank for NaN/None."""
    return repr(float(x)) if _finite(x) else ""


def _round(x, nd=6):
    return round(float(x), nd) if _finite(x) else None


def _write_csv(path: Path, header: Sequence[str], rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(list(header))
        for r in rows:
            w.writerow(list(r))


def _write_json(path: Path, obj: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str)


# --------------------------------------------------------------------------- #
# A. Input inventory (field / source discovery before any reconstruction).
# --------------------------------------------------------------------------- #
def build_input_inventory() -> Tuple[List[Dict], List[str]]:
    inv: List[Dict] = []
    missing: List[str] = []
    for rel in _INPUT_SCRIPTS + _INPUT_DATA + [_PHASE10D_JSON]:
        p = _REPO_ROOT / rel
        exists = p.is_file()
        kind = ("prior_script" if rel in _INPUT_SCRIPTS else
                "frozen_10d_report" if rel == _PHASE10D_JSON else "owned_local_data")
        inv.append({"path": rel, "kind": kind, "exists": exists,
                    "size_bytes": (p.stat().st_size if exists else 0)})
        if not exists:
            missing.append(rel)
    return inv, missing


def load_frozen_baseline() -> Tuple[Dict, Dict, Optional[str]]:
    """Read the 10-D frozen composite_sn (honest baseline) + composite_raw (diagnostic)."""
    p = _REPO_ROOT / _PHASE10D_JSON
    if not p.is_file():
        return {}, {}, "missing frozen 10-D report: %s" % _PHASE10D_JSON
    try:
        with open(p, "r", encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception as exc:  # pragma: no cover - defensive
        return {}, {}, "unreadable 10-D report (%s)" % exc
    sn, raw = {}, {}
    for r in d.get("signal_results", []):
        if r.get("signal") == "composite_sn":
            sn = r
        elif r.get("signal") == "composite_raw":
            raw = r
    if not sn:
        return {}, {}, "frozen 10-D report has no composite_sn signal_result"
    return sn, raw, None


# --------------------------------------------------------------------------- #
# B. Reconstruct the historical panel (reuse the exact 10-C/10-D offline pipeline).
# --------------------------------------------------------------------------- #
def reconstruct_panel(log) -> Tuple[object, Optional[Dict], Optional[str]]:
    """Returns (ev_with_zlegs, meta, error). ev has the four within-month z-legs attached as columns
    z_fcf_raw / z_acc_raw / z_fcf_sn / z_acc_sn, alongside comp_raw / comp_sn."""
    ev, ok, stats = c10.build_panel(AS_OF, None, log)
    if not ok or getattr(ev, "empty", True):
        return None, None, ("the owned Norgate survivorship-free panel could not be rebuilt offline "
                            "(expanded_price_panel.csv / earnings cache) - rebuild the expanded price "
                            "panel before reconstructing the scored panel")
    norm = c10._default_norm_csvs()
    missing = [f for f, p in norm.items() if not Path(p).is_file()]
    if missing:
        return None, None, ("missing owned normalized quality leg CSV(s): %s" % ", ".join(missing))
    ev, cols = c10.attach_signals(ev, norm, log)
    ev, comp_cov, raw_legs, sn_legs = d10.build_composite(ev, cols, log)
    if comp_cov == 0 or COMP_SN not in ev.columns:
        return None, None, ("the two quality legs do not co-occur on any event - cannot form the "
                            "composite panel")
    # The four additive within-month z-legs (the reweightable building blocks).
    ev["z_fcf_raw"] = x8._within_month_z(ev, raw_legs[0])
    ev["z_acc_raw"] = x8._within_month_z(ev, raw_legs[1])
    ev["z_fcf_sn"] = x8._within_month_z(ev, sn_legs[0])
    ev["z_acc_sn"] = x8._within_month_z(ev, sn_legs[1])
    log.step("reconstruct", "DONE", "panel rebuilt: %d rows / %d tickers / comp_cov=%d"
             % (len(ev), ev["ticker"].nunique(), comp_cov))
    meta = {"n_rows": int(len(ev)), "n_tickers": int(ev["ticker"].nunique()),
            "comp_coverage": int(comp_cov), "stats": stats,
            "raw_legs": raw_legs, "sn_legs": sn_legs}
    return ev, meta, None


def _selfcheck_additivity(ev) -> Dict:
    """Confirm the stored z-legs additively reconstruct the composites (the core panel guarantee)."""
    raw_sum = ev["z_fcf_raw"] + ev["z_acc_raw"]
    sn_sum = ev["z_fcf_sn"] + ev["z_acc_sn"]
    def _diff(sum_col, comp_col):
        mask = ev[comp_col].notna() & sum_col.notna()
        if not bool(mask.any()):
            return float("nan"), 0
        d = (sum_col[mask] - ev[comp_col][mask]).abs()
        return float(d.max()), int(mask.sum())
    raw_max, raw_n = _diff(raw_sum, COMP_RAW)
    sn_max, sn_n = _diff(sn_sum, COMP_SN)
    return {"composite_raw_max_abs_reconstruction_error": raw_max, "composite_raw_rows_checked": raw_n,
            "composite_sn_max_abs_reconstruction_error": sn_max, "composite_sn_rows_checked": sn_n,
            "additive_reconstruction_ok": bool(_finite(sn_max) and sn_max <= _SELFCHECK_TOL
                                               and _finite(raw_max) and raw_max <= _SELFCHECK_TOL)}


# --------------------------------------------------------------------------- #
# C. Assemble the frozen panel rows + coverage / quality diagnostics.
# --------------------------------------------------------------------------- #
def _flag(comp_sn, fwd, sector) -> str:
    if not _finite(comp_sn):
        return "MISSING_COMPOSITE"
    if not _finite(fwd):
        return "MISSING_FWD_RETURN"
    if sector is None or str(sector) == "Unknown" or str(sector) == "":
        return "OK_UNKNOWN_SECTOR"
    return "OK"


def assemble_rows(ev) -> Tuple[List[List], Dict]:
    import numpy as np
    import pandas as pd

    def _v(row, col):
        return row[col] if col in ev.columns else float("nan")

    def _date(x):
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return ""
        try:
            return pd.Timestamp(x).strftime("%Y-%m-%d")
        except Exception:
            return ""

    rows: List[List] = []
    flag_counts: Dict[str, int] = {}
    nonblank = {c: 0 for c in PANEL_COLUMNS}
    for _, r in ev.iterrows():
        comp_sn = r.get(COMP_SN, float("nan"))
        comp_raw = r.get(COMP_RAW, float("nan"))
        fwd = _v(r, "fwd_exc_63")
        sector = r.get("sector", "")
        cohort = r.get("cohort", "")
        rb = _date(r.get("entry_date"))
        flag = _flag(comp_sn, fwd, sector)
        flag_counts[flag] = flag_counts.get(flag, 0) + 1
        row = [
            AS_OF,                                             # as_of_date
            rb,                                                # rebalance_date
            r.get("ticker", ""),                               # ticker
            "" if sector is None else sector,                  # sector
            "" if cohort is None else cohort,                  # cohort
            bool(cohort == "new"),                             # is_new_cohort
            _fnum(_v(r, "liquidity_proxy")),                   # liquidity_proxy
            _fnum(_v(r, "fcf_to_assets")),                     # fcf_to_assets
            _fnum(_v(r, "operating_accruals")),                # operating_accruals
            _fnum(r.get("z_fcf_raw", float("nan"))),           # fcf_to_assets_raw
            _fnum(r.get("z_acc_raw", float("nan"))),           # operating_accruals_raw
            _fnum(r.get("z_fcf_sn", float("nan"))),            # fcf_to_assets_sector_neutral_z
            _fnum(r.get("z_acc_sn", float("nan"))),            # operating_accruals_sector_neutral_z
            _fnum(r.get("z_acc_sn", float("nan"))),            # operating_accruals_oriented_sector_neutral_z
            _fnum(comp_sn),                                    # composite_sn
            _fnum(comp_raw),                                   # composite_raw
            _fnum(fwd),                                        # forward_63d_return
            rb,                                                # forward_63d_return_start_date
            "",                                                # forward_63d_return_end_date (not persisted)
            bool(_finite(fwd)),                                # has_forward_return
            "10-B(normalized legs)+10-C(panel)+10-D(composite)",   # source_phase
            flag,                                              # data_quality_flag
        ]
        rows.append(row)
        for i, col in enumerate(PANEL_COLUMNS):
            v = row[i]
            if v != "" and v is not None:
                nonblank[col] += 1
    return rows, {"flag_counts": flag_counts, "nonblank": nonblank}


def coverage_summary(ev, meta) -> List[List]:
    import pandas as pd
    ed = ev["entry_date"]
    by_year: Dict[int, int] = {}
    for y in ed.dt.year:
        by_year[int(y)] = by_year.get(int(y), 0) + 1
    rows = [
        ["n_rows", meta["n_rows"]],
        ["n_tickers", meta["n_tickers"]],
        ["composite_coverage_both_legs", meta["comp_coverage"]],
        ["composite_sn_non_null", int(ev[COMP_SN].notna().sum())],
        ["forward_63d_return_non_null", int(ev["fwd_exc_63"].notna().sum())],
        ["scoreable_rows_comp_sn_and_fwd", int((ev[COMP_SN].notna() & ev["fwd_exc_63"].notna()).sum())],
        ["rebalance_date_min", pd.Timestamp(ed.min()).strftime("%Y-%m-%d")],
        ["rebalance_date_max", pd.Timestamp(ed.max()).strftime("%Y-%m-%d")],
        ["n_months", int(ed.dt.to_period("M").nunique())],
        ["n_sectors", int(ev["sector"].nunique(dropna=True)) if "sector" in ev.columns else 0],
        ["unknown_sector_rows", int((ev["sector"] == "Unknown").sum()) if "sector" in ev.columns else 0],
        ["cohort_old_rows", int((ev["cohort"] == "old").sum()) if "cohort" in ev.columns else 0],
        ["cohort_new_rows", int((ev["cohort"] == "new").sum()) if "cohort" in ev.columns else 0],
    ]
    for y in sorted(by_year):
        rows.append(["rows_year_%d" % y, by_year[y]])
    return rows


# --------------------------------------------------------------------------- #
# D. Reproduce the frozen 10-D composite_sn baseline FROM the persisted panel.
# --------------------------------------------------------------------------- #
def reproduce_from_panel(panel_path: Path, log) -> Dict:
    """Reload the frozen CSV and recompute composite_sn (and composite_raw) metrics with the SAME engine
    functions 10-D used - proving the file alone regenerates the baseline."""
    import numpy as np  # noqa: F401
    import pandas as pd
    df = pd.read_csv(panel_path)
    df["entry_date"] = pd.to_datetime(df["rebalance_date"], errors="coerce")
    df = df.rename(columns={"composite_sn": COMP_SN, "composite_raw": COMP_RAW,
                            "forward_63d_return": "fwd_exc_63"})
    out: Dict[str, Dict] = {}
    for name, col in (("composite_sn", COMP_SN), ("composite_raw", COMP_RAW)):
        ic = c10._eval(df, col, 63, False)
        q = d10.quarterly_backtest(df, col, ret_col="fwd_exc_63")
        wf = d10.walk_forward_h(df, col, "fwd_exc_63", WF_TRAIN, WF_TEST, WF_STEP)
        out[name] = {
            "ic_63d": ic.get("mean_ic"), "ic_t_63d": ic.get("ic_t"),
            "quarterly_gross_spread": q.get("mean_spread"), "quarterly_spread_t": q.get("spread_t"),
            "quarterly_turnover": q.get("avg_turnover"),
            "quarterly_net_25bps": q.get("net_25bps"), "quarterly_net_50bps": q.get("net_50bps"),
            # top_sector_share is sourced from _eval (the 63d monthly quintile book) to match the basis
            # 10-D reports in signal_results, NOT the quarterly long book (a different concentration).
            "top_sector_share": ic.get("top_sector_share"),
            "oos_pooled_ic": wf.get("pooled_oos_ic"), "oos_ic_t": wf.get("oos_ic_t"),
            "oos_frac_windows_positive": wf.get("frac_windows_positive"),
        }
        log.step("reproduce", "DONE", "%s: 63d IC t=%s | qtr net25=%s net50=%s turnover=%s"
                 % (name, _round(ic.get("ic_t"), 3), _round(q.get("net_25bps"), 5),
                    _round(q.get("net_50bps"), 5), _round(q.get("avg_turnover"), 4)))
    return out


_REPRO_METRICS = [
    ("ic_t_63d", "ic_t"), ("quarterly_net_25bps", "net_25bps"),
    ("quarterly_net_50bps", "net_50bps"), ("quarterly_turnover", "turnover"),
]
# Additional metrics compared for evidence but NOT hard tolerance gates.
_REPRO_INFO = ["ic_63d", "quarterly_gross_spread", "quarterly_spread_t", "top_sector_share",
               "oos_pooled_ic", "oos_ic_t", "oos_frac_windows_positive"]


def compare_reproduction(frozen: Dict, repro: Dict) -> Tuple[List[Dict], Dict]:
    rows: List[Dict] = []
    gates_pass = 0
    for key, tolkey in _REPRO_METRICS:
        fv, rv = frozen.get(key), repro.get(key)
        tol = TOL[tolkey]
        diff = abs(fv - rv) if (_finite(fv) and _finite(rv)) else float("nan")
        ok = _finite(diff) and diff <= tol
        gates_pass += 1 if ok else 0
        rows.append({"metric": key, "frozen": fv, "reconstructed": rv, "abs_diff": diff,
                     "tolerance": tol, "is_gate": True, "within_tolerance": bool(ok)})
    for key in _REPRO_INFO:
        fv, rv = frozen.get(key), repro.get(key)
        diff = abs(fv - rv) if (_finite(fv) and _finite(rv)) else float("nan")
        rows.append({"metric": key, "frozen": fv, "reconstructed": rv, "abs_diff": diff,
                     "tolerance": "", "is_gate": False,
                     "within_tolerance": bool(_finite(diff) and diff <= 1e-3)})
    rnet25 = repro.get("quarterly_net_25bps")
    rict = repro.get("ic_t_63d")
    direction_ok = bool(_finite(rnet25) and rnet25 > 0 and _finite(rict)
                        and MODEST_IC_T_LO <= rict < MODEST_IC_T_HI)
    summary = {"gates_total": len(_REPRO_METRICS), "gates_passed": gates_pass,
               "all_gates_pass": gates_pass == len(_REPRO_METRICS),
               "direction_modest_boundary_positive_net25": direction_ok}
    return rows, summary


# --------------------------------------------------------------------------- #
# E. Missing-fields report + decision.
# --------------------------------------------------------------------------- #
def missing_fields_report(nonblank: Dict[str, int], n_rows: int) -> Tuple[List[Dict], List[str]]:
    rows: List[Dict] = []
    critical_gaps: List[str] = []
    for col in PANEL_COLUMNS:
        nb = nonblank.get(col, 0)
        if col in _OPTIONAL_BLANK_COLUMNS:
            status, reason = ("BLANK_OPTIONAL",
                              "not persisted by the 10-C/10-D builder; non-blocking - not needed for "
                              "reweighting / IC reproduction")
        elif nb == 0:
            status = "MISSING"
            reason = "column produced no data during reconstruction"
            if col in _CRITICAL_COLUMNS:
                critical_gaps.append(col)
        elif nb < n_rows:
            status, reason = ("PARTIAL_COVERAGE",
                              "%d/%d rows populated (rest lack the underlying fundamental/return)"
                              % (nb, n_rows))
        else:
            status, reason = "FULL", "reconstructed for all rows"
        rows.append({"column": col, "status": status, "rows_populated": nb, "rows_total": n_rows,
                     "reason": reason})
    return rows, critical_gaps


def classify(panel_built: bool, selfcheck: Dict, repro_summary: Dict, critical_gaps: List[str]
             ) -> Tuple[str, str]:
    if not panel_built:
        return DEC_NEEDS_REPAIR, "the historical panel could not be reconstructed from owned inputs"
    additive_ok = bool(selfcheck.get("additive_reconstruction_ok"))
    all_gates = bool(repro_summary.get("all_gates_pass"))
    direction_ok = bool(repro_summary.get("direction_modest_boundary_positive_net25"))
    gates_passed = int(repro_summary.get("gates_passed", 0))
    if critical_gaps:
        return DEC_PARTIAL, ("panel reconstructed but decision-critical column(s) incomplete: %s"
                             % ", ".join(critical_gaps))
    if all_gates and direction_ok and additive_ok:
        return DEC_READY, ("the frozen panel reproduces the 10-D composite_sn baseline within all "
                           "tolerances (IC t / net-25bps / net-50bps / turnover), preserves the "
                           "modest/boundary positive-net25 conclusion, and its stored z-legs additively "
                           "reconstruct both composites - ready for a Phase 10-L-B reweighting harness")
    if gates_passed >= len(_REPRO_METRICS) - 1 and additive_ok:
        return DEC_PARTIAL, ("panel reconstructed and additively self-consistent, but %d/%d reproduction "
                             "tolerance gates passed (or the modest/boundary direction check did not "
                             "hold) - usable with the noted caveat" % (gates_passed, len(_REPRO_METRICS)))
    return DEC_FAILED, ("the reconstructed panel does not reproduce the frozen 10-D composite_sn baseline "
                        "within tolerance (%d/%d gates) - do not use for reweighting"
                        % (gates_passed, len(_REPRO_METRICS)))


# --------------------------------------------------------------------------- #
# F. Report + orchestration.
# --------------------------------------------------------------------------- #
def _frozen_public(sn: Dict, raw: Dict) -> Dict:
    keys = ["ic_63d", "ic_t_63d", "quarterly_gross_spread", "quarterly_spread_t", "quarterly_turnover",
            "quarterly_net_25bps", "quarterly_net_50bps", "top_sector_share", "oos_pooled_ic",
            "oos_ic_t", "oos_frac_windows_positive", "both_cohorts_positive", "both_subperiods_positive",
            "high_liquidity_positive"]
    return {
        "honest_baseline_signal": "composite_sn",
        "diagnostic_only_signal": "composite_raw",
        "horizon_days": 63, "horizon_label": "quarterly (63d)",
        "long_leg": "fcf_to_assets (oriented +1)", "short_leg": "operating_accruals (oriented -1)",
        "weighting": "equal-weight (1.0 / 1.0 on within-month z legs; no optimisation, no sign-flip)",
        "composite_sn": {k: sn.get(k) for k in keys},
        "composite_raw_diagnostic": {k: raw.get(k) for k in keys},
        "source": _PHASE10D_JSON,
        "characterisation": ("MODEST / BOUNDARY: net-of-cost quarterly edge is small (net-25bps "
                             "~+0.00401, net-50bps ~+0.00095), IC t sits below the 3.0 strong bar; the "
                             "short accruals leg carries the robustness, the long fcf leg diversifies - "
                             "must not be oversold"),
    }


def _next_plan(decision: str) -> Dict:
    if decision == DEC_READY:
        nxt = ("Phase 10-L-B: run the 10-K narrow-improvement harness AGAINST this frozen historical "
               "panel - honestly backtest interior leg re-weightings (60/40, 40/60, 70/30, 30/70) and "
               "robustness transforms (z-cap, winsorize) on the persisted per-(month, ticker) z-legs + "
               "forward 63d returns, with the same strict sector-neutral cost/turnover/OOS gates. Still "
               "offline, owned-data-only, paper-only, NO orders, NO automation, NO broker, NO deploy.")
        cmd = ("review research/output/%s/historical_sector_neutral_scored_panel.csv   then build "
               "run_phase10lb_*.py against it" % STEM)
    elif decision == DEC_PARTIAL:
        nxt = ("Phase 10-L-B may proceed with the reconstructed panel for the reproduced metrics, but "
               "treat the noted caveat as a known limit; consider firming up the flagged column before "
               "trusting reweighting near the tolerance boundary. Owned-data-only; paper-only.")
        cmd = ("review research/output/%s/missing_fields_report.csv and "
               "phase10d_reproduction_check.csv" % STEM)
    else:
        nxt = ("Do NOT proceed to 10-L-B. Repair the panel input first: verify the owned expanded price "
               "panel + EODHD normalized quality legs are present and that 10-C/10-D still build the "
               "panel offline, then re-run this reconstruction. Owned-data-only; no new purchase.")
        cmd = "review research/output/%s/%s" % (STEM, _ARTIFACTS["report"])
    return {"phase": "10-L-B", "from_decision": decision, "next_step": nxt, "exact_next_command": cmd,
            "constraints": ["owned data only; no new purchase", "paper-only", "no orders",
                            "no automation", "no broker", "no deploy", "no commit", "no push"]}


def _build_report(decision, rationale, inv, meta, coverage_rows, frozen_pub, repro, repro_rows,
                  repro_summary, missing_rows, selfcheck, flag_counts) -> Dict:
    n_rows = meta["n_rows"] if meta else 0
    return {
        "phase": PHASE, "phase_name": PHASE_NAME, "decision": decision,
        "decision_rationale": rationale,
        "allowed_decisions": list(ALLOWED_DECISIONS),
        "objective": ("reconstruct and PERSIST the historical per-(month, ticker) sector-neutral scored "
                      "panel that 10-D builds in memory but never saves, then reproduce the frozen 10-D "
                      "composite_sn baseline from the persisted file - a data-lineage / reproducibility "
                      "phase, NOT an alpha search, NOT a new champion"),
        "offline": True, "performs_network": PERFORMS_NETWORK,
        "eodhd_key_visible": bool(os.environ.get(EODHD_KEY_ENV)), "eodhd_key_required": False,
        "input_inventory": inv,
        "reconstructed_panel": {
            "path": "research/output/%s/%s" % (STEM, _ARTIFACTS["panel"]),
            "n_rows": n_rows, "n_columns": len(PANEL_COLUMNS),
            "n_tickers": (meta["n_tickers"] if meta else 0),
            "composite_coverage_both_legs": (meta["comp_coverage"] if meta else 0),
            "columns": list(PANEL_COLUMNS),
            "data_quality_flag_counts": flag_counts,
            "additive_self_check": selfcheck,
        },
        "panel_schema": [{"column": c[0], "maps_from": c[1], "dtype": c[2], "note": c[3]}
                         for c in PANEL_SCHEMA],
        "panel_coverage_summary": {r[0]: r[1] for r in coverage_rows},
        "phase10d_frozen_baseline": frozen_pub,
        "phase10d_reproduction_check": {
            "reconstructed_composite_sn": repro.get("composite_sn"),
            "reconstructed_composite_raw_diagnostic": repro.get("composite_raw"),
            "tolerances": TOL, "rows": repro_rows, "summary": repro_summary,
        },
        "missing_fields_report": missing_rows,
        "data_quality_report": {
            "additive_self_check": selfcheck,
            "data_quality_flag_counts": flag_counts,
            "reproduction_gates_passed": repro_summary.get("gates_passed"),
            "reproduction_gates_total": repro_summary.get("gates_total"),
        },
        "implementation_limits": [
            "This phase does NOT search for new alpha, test new factors, or declare a new champion; it "
            "is a data-lineage / reproducibility phase only.",
            "The panel is reconstructed at a single data vintage (as_of %s); as_of_date is that vintage, "
            "not a per-row point-in-time stamp (PIT is guaranteed by the builder: available_date <= "
            "rebalance_date)." % AS_OF,
            "forward_63d_return_end_date is not persisted upstream (only the realized 63d excess return "
            "is); it is left blank and is not needed for reweighting / IC reproduction.",
            "sector is current-as-of the owned metadata (a share of rows remain 'Unknown'); grouping is "
            "not strictly point-in-time sector membership.",
            "The alpha remains MODEST / BOUNDARY and is not a prediction oracle; this phase does not "
            "change that.",
        ],
        "next_recommended_phase": _next_plan(decision),
        "constraints_honored": [
            "offline (no network / no key / no provider probe)", "owned / local data only",
            "no new purchase", "no Paper Trader writes", "no orders", "no automation", "no broker",
            "no deploy", "no GCP", "no package install", "no full regression (targeted tests only)",
            "no commit", "no push",
        ],
        "safety": {
            "paper_only": True, "owned_local_data_only": True, "no_live_api_calls": True,
            "no_orders": True, "no_automation": True, "no_broker": True, "no_deploy": True,
        },
    }


def _write_artifacts(P: _Paths, panel_rows, coverage_rows, repro_rows, missing_rows, selfcheck,
                     flag_counts):
    _write_csv(P.art("panel"), PANEL_COLUMNS, panel_rows)
    _write_csv(P.art("schema"), ["column", "maps_from", "dtype", "note"],
               [[c[0], c[1], c[2], c[3]] for c in PANEL_SCHEMA])
    _write_csv(P.art("coverage"), ["metric", "value"], coverage_rows)
    _write_csv(P.art("repro"),
               ["metric", "frozen_value", "reconstructed_value", "abs_diff", "tolerance", "is_gate",
                "within_tolerance"],
               [[r["metric"], r["frozen"], r["reconstructed"], _round(r["abs_diff"], 8), r["tolerance"],
                 r["is_gate"], r["within_tolerance"]] for r in repro_rows])
    _write_csv(P.art("missing"), ["column", "status", "rows_populated", "rows_total", "reason"],
               [[r["column"], r["status"], r["rows_populated"], r["rows_total"], r["reason"]]
                for r in missing_rows])
    qrows = [["additive_reconstruction_ok", selfcheck.get("additive_reconstruction_ok"),
              "z-legs sum to the composites"],
             ["composite_sn_max_abs_reconstruction_error",
              _round(selfcheck.get("composite_sn_max_abs_reconstruction_error"), 12),
              "max|z_fcf_sn + z_acc_sn - composite_sn|"],
             ["composite_raw_max_abs_reconstruction_error",
              _round(selfcheck.get("composite_raw_max_abs_reconstruction_error"), 12),
              "max|z_fcf_raw + z_acc_raw - composite_raw|"]]
    for flag in sorted(flag_counts):
        qrows.append(["flag_%s" % flag, flag_counts[flag], "data_quality_flag row count"])
    _write_csv(P.art("quality"), ["check", "value", "note"], qrows)


def _print_summary(report: Dict) -> None:
    rp = report.get("phase10d_reproduction_check", {}).get("summary", {})
    print("[10-L-A] decision=%s | rows=%s | tickers=%s | gates=%s/%s | direction_ok=%s | additive_ok=%s"
          % (report.get("decision"),
             report.get("reconstructed_panel", {}).get("n_rows"),
             report.get("reconstructed_panel", {}).get("n_tickers"),
             rp.get("gates_passed"), rp.get("gates_total"),
             rp.get("direction_modest_boundary_positive_net25"),
             report.get("reconstructed_panel", {}).get("additive_self_check", {})
             .get("additive_reconstruction_ok")))


def run(out_dir: Optional[Path] = None, *, verbose: bool = True) -> Dict:
    P = _Paths(out_dir)
    P.out.mkdir(parents=True, exist_ok=True)
    log = _Log(verbose)
    try:
        log.step("preflight", "OFFLINE", "no network / no key required; reconstructing owned panel")
        inv, missing_inputs = build_input_inventory()
        frozen_sn, frozen_raw, ferr = load_frozen_baseline()

        ev, meta, err = reconstruct_panel(log)
        if err is not None or ev is None:
            rationale = err or "panel reconstruction failed"
            report = _needs_repair_report(inv, missing_inputs, ferr, rationale)
            _write_json(P.art("report"), report)
            # best-effort skeleton CSVs so downstream tests/consumers see explicit emptiness
            _write_csv(P.art("panel"), PANEL_COLUMNS, [])
            _write_csv(P.art("schema"), ["column", "maps_from", "dtype", "note"],
                       [[c[0], c[1], c[2], c[3]] for c in PANEL_SCHEMA])
            _write_csv(P.art("coverage"), ["metric", "value"], [["n_rows", 0]])
            _write_csv(P.art("repro"),
                       ["metric", "frozen_value", "reconstructed_value", "abs_diff", "tolerance",
                        "is_gate", "within_tolerance"], [])
            _write_csv(P.art("missing"), ["column", "status", "rows_populated", "rows_total", "reason"],
                       [[c, "MISSING", 0, 0, "panel not reconstructed"] for c in PANEL_COLUMNS])
            _write_csv(P.art("quality"), ["check", "value", "note"],
                       [["panel_reconstructed", False, rationale]])
            _print_summary(report)
            return report

        selfcheck = _selfcheck_additivity(ev)
        panel_rows, asm = assemble_rows(ev)
        coverage_rows = coverage_summary(ev, meta)
        _write_csv(P.art("panel"), PANEL_COLUMNS, panel_rows)   # persist the frozen panel first

        repro = reproduce_from_panel(P.art("panel"), log)
        repro_rows, repro_summary = compare_reproduction(frozen_sn, repro.get("composite_sn", {}))
        missing_rows, critical_gaps = missing_fields_report(asm["nonblank"], meta["n_rows"])
        decision, rationale = classify(True, selfcheck, repro_summary, critical_gaps)

        frozen_pub = _frozen_public(frozen_sn, frozen_raw)
        _write_artifacts(P, panel_rows, coverage_rows, repro_rows, missing_rows, selfcheck,
                         asm["flag_counts"])
        report = _build_report(decision, rationale, inv, meta, coverage_rows, frozen_pub, repro,
                               repro_rows, repro_summary, missing_rows, selfcheck, asm["flag_counts"])
        _write_json(P.art("report"), report)
        log.step("artifacts", "DONE", "wrote panel (%d rows) + 5 CSVs + JSON" % len(panel_rows))
        _print_summary(report)
        return report
    except Exception as exc:  # pragma: no cover - defensive
        import traceback
        detail = "%s: %s" % (type(exc).__name__, exc)
        log.step("run", "ERROR", detail)
        report = {"phase": PHASE, "phase_name": PHASE_NAME, "decision": DEC_NEEDS_REPAIR,
                  "decision_rationale": detail,
                  "repro_command": "python research/run_%s.py" % STEM,
                  "traceback": traceback.format_exc(),
                  "safety": {"paper_only": True, "owned_local_data_only": True,
                             "no_live_api_calls": True, "no_orders": True, "no_automation": True,
                             "no_broker": True, "no_deploy": True}}
        try:
            _write_json(P.art("report"), report)
        except Exception:
            pass
        return report


def _needs_repair_report(inv, missing_inputs, ferr, rationale) -> Dict:
    return {
        "phase": PHASE, "phase_name": PHASE_NAME, "decision": DEC_NEEDS_REPAIR,
        "decision_rationale": rationale,
        "allowed_decisions": list(ALLOWED_DECISIONS),
        "offline": True, "performs_network": PERFORMS_NETWORK,
        "input_inventory": inv, "missing_inputs": missing_inputs, "frozen_baseline_error": ferr,
        "reconstructed_panel": {"n_rows": 0, "n_columns": len(PANEL_COLUMNS),
                                "columns": list(PANEL_COLUMNS)},
        "panel_schema": [{"column": c[0], "maps_from": c[1], "dtype": c[2], "note": c[3]}
                         for c in PANEL_SCHEMA],
        "panel_coverage_summary": {"n_rows": 0},
        "phase10d_frozen_baseline": {},
        "phase10d_reproduction_check": {"tolerances": TOL, "rows": [], "summary": {}},
        "missing_fields_report": [{"column": c, "status": "MISSING", "rows_populated": 0,
                                   "rows_total": 0, "reason": "panel not reconstructed"}
                                  for c in PANEL_COLUMNS],
        "data_quality_report": {"panel_reconstructed": False},
        "implementation_limits": ["panel could not be reconstructed from owned inputs"],
        "next_recommended_phase": _next_plan(DEC_NEEDS_REPAIR),
        "constraints_honored": ["offline", "owned/local data only", "no orders", "no automation",
                                "no broker", "no deploy", "no commit", "no push"],
        "safety": {"paper_only": True, "owned_local_data_only": True, "no_live_api_calls": True,
                   "no_orders": True, "no_automation": True, "no_broker": True, "no_deploy": True},
    }


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 10-L-A - Historical Sector-Neutral Scored Panel "
                                            "Reconstruction (offline; owned data only)")
    p.add_argument("--out-dir", default=None)
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ns = _parse_args(argv)
    report = run(out_dir=ns.out_dir, verbose=not ns.quiet)
    return 0 if report.get("decision") in ALLOWED_DECISIONS else 1


if __name__ == "__main__":
    raise SystemExit(main())
