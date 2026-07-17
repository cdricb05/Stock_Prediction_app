"""Phase 16-A - Sector Metadata Integrity Audit + Shadow Sector-Neutral Revalidation.

WHY THIS PHASE EXISTS
    The current champion is `composite_sn`, a SECTOR-NEUTRAL quality composite. Its paper-test package
    (Phase 13-A) ranks the latest calendar-month cross-section (234 names, signal 2026-05-22). But that
    cross-section is ~195/234 "Unknown" sector, and the Top25 book is 100% Unknown - so the
    "sector-neutral" leg is largely de-meaning against one giant Unknown pseudo-sector, not against real
    sectors. Before trusting the champion further we must answer two questions honestly:

      (E) INTEGRITY: using ONLY owned/local metadata, how many of the 234 ranked names can be resolved to
          a real GICS sector, and what is the resulting coverage of the Top25/Top50/Bottom25 books?
      (F) REVALIDATION: if we recompute ONLY the sector-neutral transformation with the repaired sector
          labels (no new factors, no reweighting, no threshold tuning, no champion replacement), does the
          champion's validated edge survive? Or do proper sectors materially change the ranks / destroy
          the cost-adjusted spread?

    This is an INTEGRITY and RESEARCH-VALIDATION phase. It is NOT an order phase, NOT automation, NOT a
    live-trading approval, and NOT a champion replacement. It reads the FROZEN Phase 10-L scored panel
    and owned EODHD fundamentals metadata; it recomputes the sector-neutral legs off the panel's own raw
    columns; it VALIDATES that recompute by reproducing the frozen sector-neutral columns with the
    ORIGINAL sectors; only then does it compute the champion-vs-shadow comparison and apply a decision
    ladder that is DECLARED A-PRIORI (before the comparative result is read). It writes metadata CSV/JSON
    to its OWN new research/output directory and NEVER overwrites the Phase 13-A package.

SECTOR SOURCE PRIORITY (owned/local only; NO new acquisition; NO provider call; never fabricated)
    1. Norgate local symbol metadata      -> NOT cached on disk (out of offline scope). Logged UNAVAILABLE.
    2. EODHD raw fundamentals General.GicSector (research/data/eodhd/raw/fundamentals/<ticker>.json)
       -> PRIMARY. Same 11-bucket GICS taxonomy as the curated map. HIGH confidence.
    3. EODHD raw fundamentals General.Sector (Morningstar) via a documented 1:1 GICS crosswalk where
       GicSector is blank. MEDIUM confidence.
    4. EODHD normalized fundamentals metadata -> time-series features only; no sector. N/A.
    5. Curated phase2k sector map (the panel's existing source) -> checked + used as a cross-check.
    6. Cached EODHD company profile == the General block (same data as source 2/3).
    (FMP company_profile exists on disk but is HARD-BANNED for this phase - never read.)

    Repaired labels are CURRENT-as-of company classifications used ONLY as a static neutralisation
    GROUPING (sector is not a return feature; no return lookahead) - the same non-PIT basis as the
    curated map. Documented, not hidden.

EXACT SECTOR-NEUTRAL TRANSFORM (reproduced verbatim from the pipeline; confirmed against source)
    o_fcf = (+1) * fcf_to_assets            (panel raw level, orientation +1)
    o_acc = (-1) * operating_accruals       (panel raw level, orientation -1, Sloan-negated)
    o_x__sn = o_x - mean_{month, sector}(o_x)                 (within-month x sector de-mean)
    z_x_sn  = (o_x__sn - mean_month(o_x__sn)) / std_month(o_x__sn)   (within-month z; sample std ddof=1;
                                                                       std==0 -> NaN)
    composite_sn = z_fcf_sn + z_acc_sn
    Only the SECTOR GROUPING in the de-mean changes between the original (frozen) and the shadow
    (repaired) computation - everything else is identical. The recompute is validated by reproducing the
    frozen fcf/accruals sector-neutral z columns and composite_sn with the ORIGINAL sectors.

TERMINAL DECISIONS (allowed)
    KEEP_CURRENT_CHAMPION | KEEP_CURRENT_CHAMPION_PENDING_MORE_DATA | RESEARCH_REVALIDATION_REQUIRED |
    BLOCKED_DATA_MISSING | BLOCKED_RUNNER_ERROR
    FORBIDDEN: LIVE_TRADING_READY, ORDER_READY, AUTOMATION_READY, PAPER_TRADER_READY,
    STRONG_ALPHA_FOUND_READY_FOR_REVIEW, CHAMPION_REPLACED.

CONSTRAINTS HONORED
    Offline (no network / key / provider probe); owned/local data only; recompute ONLY the sector-neutral
    transform (no new factor, no reweight, no threshold tune, no sign-flip, no champion mutation); does
    NOT modify or overwrite the Phase 13-A package; no Paper Trader writes; NO orders; NO automation; NO
    broker; NO live trading; no deploy; no GCP; no package install; pure stdlib (no numpy/pandas/cross-
    phase imports, runs under a bare Windows Python); keys never printed or written; output is metadata
    only. No commit inside the runner. No push.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
PHASE = "16-A"
PERFORMS_NETWORK = False

# Only these env vars are scanned for accidental leakage (mirrors 13-A). This runner never reads or uses
# them - the audit just proves no key value landed in an output file.
_SECRET_ENV_VARS = ("EODHD_API_KEY", "FMP_API_KEY")


# --------------------------------------------------------------------------- #
# Vendored stdlib IO helpers (ZERO third-party / cross-phase imports).
# --------------------------------------------------------------------------- #
def _rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(_REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, allow_nan=False, default=str)
        fh.write("\n")


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow(["" if v is None else v for v in r])


def _read_csv_rows(path: Path) -> List[Dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def _read_json(path: Path) -> Dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _to_float(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def _round(x, n=6):
    v = _to_float(x)
    return None if v is None else round(v, n)


def _secret_safety_audit(out_dir: Path) -> Tuple[List[Dict], bool]:
    markers = ["apikey=", "api_token=", "token=", "api_key=", "&apikey", "?apikey", "&token"]
    present_values = []
    for env in _SECRET_ENV_VARS:
        v = os.environ.get(env)
        if isinstance(v, str) and v.strip():
            present_values.append(v.strip())
    rows: List[Dict] = []
    clean = True
    for p in sorted(out_dir.glob("*")):
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        low = text.lower()
        marker_hit = next((m for m in markers if m in low), "")
        value_hit = any(val in text for val in present_values)
        file_clean = not marker_hit and not value_hit
        clean = clean and file_clean
        rows.append({"file": p.name, "clean": file_clean,
                     "keyed_url_marker": marker_hit or "none", "key_value_present": value_hit})
    return rows, clean


# --------------------------------------------------------------------------- #
# Owned sector sources + repair (stdlib port of the Phase 10-F-A logic).
# --------------------------------------------------------------------------- #
CANONICAL_GICS = frozenset({
    "Communication Services", "Consumer Discretionary", "Consumer Staples", "Energy", "Financials",
    "Health Care", "Industrials", "Information Technology", "Materials", "Real Estate", "Utilities",
})
MORNINGSTAR_TO_GICS = {
    "technology": "Information Technology",
    "financial services": "Financials",
    "financial": "Financials",
    "healthcare": "Health Care",
    "health care": "Health Care",
    "consumer cyclical": "Consumer Discretionary",
    "consumer defensive": "Consumer Staples",
    "basic materials": "Materials",
    "communication services": "Communication Services",
    "industrials": "Industrials",
    "energy": "Energy",
    "utilities": "Utilities",
    "real estate": "Real Estate",
}

CONF_HIGH, CONF_MEDIUM = "HIGH", "MEDIUM"
SRC_NORGATE = "norgate_local_symbol_metadata"
SRC_EODHD_GIC = "eodhd_raw_fundamentals(General.GicSector)"
SRC_EODHD_SECTOR = "eodhd_raw_fundamentals(General.Sector->GICS_crosswalk)"
SRC_EODHD_NORM = "eodhd_normalized_fundamentals_metadata"
SRC_PRIOR_MAP = "prior_repo_curated_sector_map(phase2k)"
SRC_EODHD_PROFILE = "cached_eodhd_company_profile(General_block)"

_UNKNOWN = ("", "unknown", "n/a", "na", "none", "null")


def _is_unknown(s) -> bool:
    return (s is None) or (str(s).strip().lower() in _UNKNOWN)


def _norm_label(s) -> str:
    return ("" if s is None else str(s)).strip()


def load_eodhd_sector_meta(fund_dir: Path) -> Dict[str, Dict[str, str]]:
    """Read each owned EODHD fundamentals JSON's General block (offline). No network."""
    out: Dict[str, Dict[str, str]] = {}
    d = Path(fund_dir)
    if not d.is_dir():
        return out
    for fp in sorted(d.glob("*.json")):
        tk = fp.stem.strip().upper()
        if not tk:
            continue
        try:
            with open(fp, "r", encoding="utf-8") as fh:
                g = (json.load(fh) or {}).get("General", {}) or {}
        except (OSError, ValueError):
            continue
        out[tk] = {
            "gic_sector": _norm_label(g.get("GicSector")),
            "gic_industry": _norm_label(g.get("GicIndustry")),
            "sector": _norm_label(g.get("Sector")),
            "industry": _norm_label(g.get("Industry")),
            "type": _norm_label(g.get("Type")),
            "source_file": _rel(fp),
        }
    return out


def load_curated_map(curated_csv: Path) -> Dict[str, Tuple[str, str]]:
    out: Dict[str, Tuple[str, str]] = {}
    p = Path(curated_csv)
    if not p.is_file():
        return out
    for r in _read_csv_rows(p):
        tk = _norm_label(r.get("ticker")).upper()
        if tk:
            out[tk] = (_norm_label(r.get("sector")) or "Unknown",
                       _norm_label(r.get("industry")) or "Unknown")
    return out


def resolve_one(ticker: str, eodhd: Dict[str, Dict[str, str]],
                curated: Dict[str, Tuple[str, str]]) -> Tuple[Optional[Dict], List[Dict]]:
    """Try owned sources in priority order; never fabricate. Returns (resolution|None, attempts)."""
    tk = ticker.strip().upper()
    attempts: List[Dict] = []

    def att(family, outcome, value=""):
        attempts.append({"ticker": tk, "source_family": family, "outcome": outcome, "value": value})

    att(SRC_NORGATE, "UNAVAILABLE_NO_LOCAL_NORGATE_METADATA")
    meta = eodhd.get(tk)

    if meta and meta.get("gic_sector") in CANONICAL_GICS:
        att(SRC_EODHD_GIC, "HIT", meta["gic_sector"])
        return ({"sector": meta["gic_sector"],
                 "industry": meta.get("gic_industry") or meta.get("industry") or "Unknown",
                 "source_family": SRC_EODHD_GIC, "source_file": meta.get("source_file", ""),
                 "source_field": "General.GicSector", "confidence": CONF_HIGH, "point_in_time": False,
                 "reason": "owned EODHD General.GicSector in canonical 11-bucket GICS"}), attempts
    if meta and meta.get("gic_sector"):
        att(SRC_EODHD_GIC, "PRESENT_BUT_NON_CANONICAL", meta["gic_sector"])
    else:
        att(SRC_EODHD_GIC, "BLANK")

    if meta and meta.get("sector"):
        gx = MORNINGSTAR_TO_GICS.get(meta["sector"].lower())
        if gx in CANONICAL_GICS:
            att(SRC_EODHD_SECTOR, "HIT", "%s->%s" % (meta["sector"], gx))
            return ({"sector": gx, "industry": meta.get("industry") or "Unknown",
                     "source_family": SRC_EODHD_SECTOR, "source_file": meta.get("source_file", ""),
                     "source_field": "General.Sector", "confidence": CONF_MEDIUM, "point_in_time": False,
                     "reason": "owned EODHD General.Sector (Morningstar) via documented 1:1 GICS "
                               "crosswalk (GicSector blank)"}), attempts
        att(SRC_EODHD_SECTOR, "NO_CROSSWALK", meta.get("sector", ""))
    else:
        att(SRC_EODHD_SECTOR, "BLANK")

    att(SRC_EODHD_NORM, "NA_NO_SECTOR_IN_NORMALIZED_FEATURES")

    cur = curated.get(tk)
    if cur and cur[0] in CANONICAL_GICS:
        att(SRC_PRIOR_MAP, "HIT", cur[0])
        return ({"sector": cur[0], "industry": cur[1] or "Unknown", "source_family": SRC_PRIOR_MAP,
                 "source_file": _rel(_DEF_CURATED), "source_field": "sector", "confidence": CONF_HIGH,
                 "point_in_time": False,
                 "reason": "found in the owned curated phase2k sector map"}), attempts
    att(SRC_PRIOR_MAP, "NOT_FOUND")

    att(SRC_EODHD_PROFILE, "SAME_AS_EODHD_GENERAL_BLOCK")
    return None, attempts


# --------------------------------------------------------------------------- #
# The frozen champion transform (exact reproduction; sector grouping is the only variable).
# --------------------------------------------------------------------------- #
SIGNAL_COL = "composite_sn"
FCF_LEVEL = "fcf_to_assets"                 # panel raw level (un-oriented)
ACC_LEVEL = "operating_accruals"            # panel raw level (un-oriented)
FCF_SN_Z = "fcf_to_assets_sector_neutral_z"
ACC_SN_Z = "operating_accruals_sector_neutral_z"
FCF_RAW_Z = "fcf_to_assets_raw"
ACC_RAW_Z = "operating_accruals_raw"
FWD = "forward_63d_return"
ORI_FCF = +1.0
ORI_ACC = -1.0


def _mean(vals: List[float]) -> float:
    return sum(vals) / len(vals)


def _std(vals: List[float], ddof: int = 1) -> Optional[float]:
    n = len(vals)
    if n - ddof <= 0:
        return None
    m = _mean(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (n - ddof))


def _within_month_z(vals_by_idx: Dict[int, float]) -> Dict[int, float]:
    """z = (v - mean)/std over the provided (non-null) values; sample std (ddof=1); std==0 or <2 -> drop."""
    idxs = list(vals_by_idx.keys())
    xs = [vals_by_idx[i] for i in idxs]
    sd = _std(xs, 1)
    if sd is None or sd == 0.0:
        return {}
    m = _mean(xs)
    return {i: (vals_by_idx[i] - m) / sd for i in idxs}


def _sector_demean_then_z(rows: List[Dict], oriented_by_idx: Dict[int, float],
                          sector_fn: Callable[[str], str]) -> Dict[int, float]:
    """Given oriented values keyed by row index within one month, de-mean within (sector) then
    within-month z. Rows with a null oriented value are excluded (return no key)."""
    # 1. sector de-mean
    by_sector: Dict[str, List[int]] = {}
    for i in oriented_by_idx:
        sec = sector_fn(rows[i]["ticker"])
        by_sector.setdefault(sec, []).append(i)
    demeaned: Dict[int, float] = {}
    for sec, idxs in by_sector.items():
        sm = _mean([oriented_by_idx[i] for i in idxs])
        for i in idxs:
            demeaned[i] = oriented_by_idx[i] - sm
    # 2. within-month z of the de-meaned series
    return _within_month_z(demeaned)


def recompute_composite(rows: List[Dict], month_index: Dict[str, List[int]],
                        sector_fn: Callable[[str], str]) -> Dict[int, Optional[float]]:
    """Recompute composite_sn per row index using the given sector grouping. Pure stdlib."""
    comp: Dict[int, Optional[float]] = {}
    for _month, idxs in month_index.items():
        o_fcf = {i: ORI_FCF * v for i in idxs if (v := _to_float(rows[i].get(FCF_LEVEL))) is not None}
        o_acc = {i: ORI_ACC * v for i in idxs if (v := _to_float(rows[i].get(ACC_LEVEL))) is not None}
        z_fcf = _sector_demean_then_z(rows, o_fcf, sector_fn)
        z_acc = _sector_demean_then_z(rows, o_acc, sector_fn)
        for i in idxs:
            zf, za = z_fcf.get(i), z_acc.get(i)
            comp[i] = (zf + za) if (zf is not None and za is not None) else None
    return comp


# --------------------------------------------------------------------------- #
# Cross-sectional statistics (stdlib).
# --------------------------------------------------------------------------- #
def _rank(vals: List[float]) -> List[float]:
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(vals):
        j = i
        while j + 1 < len(vals) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(a: List[float], b: List[float]) -> Optional[float]:
    n = len(a)
    if n < 2:
        return None
    ma, mb = _mean(a), _mean(b)
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((x - mb) ** 2 for x in b))
    if da == 0 or db == 0:
        return None
    return num / (da * db)


def _spearman(a: List[float], b: List[float]) -> Optional[float]:
    if len(a) < 3:
        return None
    return _pearson(_rank(a), _rank(b))


def _t_stat(vals: List[float]) -> Optional[float]:
    n = len(vals)
    if n < 3:
        return None
    sd = _std(vals, 1)
    if sd is None or sd == 0:
        return None
    return _mean(vals) / (sd / math.sqrt(n))


def _max_drawdown(cum: List[float]) -> Optional[float]:
    if not cum:
        return None
    peak = cum[0]
    mdd = 0.0
    for v in cum:
        peak = max(peak, v)
        mdd = min(mdd, v - peak)
    return mdd


# --------------------------------------------------------------------------- #
# Backtest evaluation of ONE score over the monthly cross-sections (identical methodology for
# champion and shadow, so the comparison is apples-to-apples).
# --------------------------------------------------------------------------- #
MIN_NAMES_PER_MONTH = 20
QUANTILE = 5                                # top/bottom quintile L/S
COST25, COST50 = 0.0025, 0.0050
_PRE2020 = "2020-01"


def _evaluate_score(monthly: Dict[str, List[Dict]], score_key: str) -> Dict:
    """monthly[month] = [{ticker, orig, shadow, fwd}]. Evaluate one score (orig|shadow) at the 63d
    horizon: monthly rank-IC, quintile L/S gross/net spread, turnover, drawdown, subperiod stability."""
    months = sorted(monthly)
    ics: List[float] = []
    gross: List[float] = []
    turn: List[float] = []
    ic_by_period: Dict[str, List[float]] = {"pre2020": [], "post2020": []}
    spread_by_period: Dict[str, List[float]] = {"pre2020": [], "post2020": []}
    prev_long: Optional[set] = None
    prev_short: Optional[set] = None
    cum: List[float] = []
    running = 0.0
    n_months_scored = 0
    for m in months:
        rows = [r for r in monthly[m]
                if _to_float(r.get(score_key)) is not None and _to_float(r.get("fwd")) is not None]
        if len(rows) < MIN_NAMES_PER_MONTH:
            prev_long = prev_short = None
            continue
        n_months_scored += 1
        period = "pre2020" if m < _PRE2020 else "post2020"
        scores = [_to_float(r[score_key]) for r in rows]
        fwds = [_to_float(r["fwd"]) for r in rows]
        ic = _spearman(scores, fwds)
        if ic is not None:
            ics.append(ic)
            ic_by_period[period].append(ic)
        # quintiles
        order = sorted(range(len(rows)), key=lambda i: scores[i])
        k = max(1, len(rows) // QUANTILE)
        short_idx = order[:k]
        long_idx = order[-k:]
        long_ret = _mean([fwds[i] for i in long_idx])
        short_ret = _mean([fwds[i] for i in short_idx])
        sp = long_ret - short_ret
        gross.append(sp)
        spread_by_period[period].append(sp)
        running += sp
        cum.append(running)
        long_set = {rows[i]["ticker"] for i in long_idx}
        short_set = {rows[i]["ticker"] for i in short_idx}
        if prev_long is not None and prev_short is not None:
            denom = (len(long_set) + len(short_set)) or 1
            churn = len(long_set - prev_long) + len(short_set - prev_short)
            turn.append(churn / denom)
        prev_long, prev_short = long_set, short_set

    mean_gross = _mean(gross) if gross else None
    mean_turn = _mean(turn) if turn else None
    net25 = (mean_gross - COST25 * (mean_turn or 0.0)) if mean_gross is not None else None
    net50 = (mean_gross - COST50 * (mean_turn or 0.0)) if mean_gross is not None else None

    def _period(pk):
        ic_p = ic_by_period[pk]
        sp_p = spread_by_period[pk]
        return {"n_months": len(sp_p), "mean_ic": _round(_mean(ic_p), 6) if ic_p else None,
                "ic_t": _round(_t_stat(ic_p), 4), "mean_spread": _round(_mean(sp_p), 6) if sp_p else None}

    return {
        "n_months_scored": n_months_scored,
        "n_ic_months": len(ics),
        "mean_ic": _round(_mean(ics), 6) if ics else None,
        "ic_t_stat": _round(_t_stat(ics), 4),
        "mean_gross_spread": _round(mean_gross, 6),
        "mean_turnover": _round(mean_turn, 4),
        "net25_spread": _round(net25, 6),
        "net50_spread": _round(net50, 6),
        "cumulative_spread": _round(cum[-1], 6) if cum else None,
        "max_drawdown": _round(_max_drawdown(cum), 6),
        "subperiod": {"pre2020": _period("pre2020"), "post2020": _period("post2020")},
    }


# --------------------------------------------------------------------------- #
# Latest cross-section (matches Phase 13-A dedup: one row/ticker, latest rebalance_date w/ valid comp).
# --------------------------------------------------------------------------- #
def _latest_month(rows: List[Dict]) -> str:
    months = {(_norm_label(r.get("rebalance_date")))[:7] for r in rows
              if len(_norm_label(r.get("rebalance_date"))) >= 7}
    return max(months) if months else ""


def _representative_rows(rows: List[Dict], month: str) -> Dict[str, int]:
    """ticker -> row index of the latest rebalance_date in `month` with a valid frozen composite_sn."""
    best: Dict[str, Tuple[str, int]] = {}
    for i, r in enumerate(rows):
        if _norm_label(r.get("rebalance_date"))[:7] != month:
            continue
        tk = _norm_label(r.get("ticker"))
        if not tk or _to_float(r.get(SIGNAL_COL)) is None:
            continue
        rb = _norm_label(r.get("rebalance_date"))
        if tk not in best or rb > best[tk][0]:
            best[tk] = (rb, i)
    return {tk: idx for tk, (_rb, idx) in best.items()}


def _overlap(a: List[str], b: List[str]) -> float:
    sa, sb = set(a), set(b)
    return (len(sa & sb) / len(sa)) if sa else 0.0


def _exposure(tickers: List[str], sector_fn: Callable[[str], str]) -> Tuple[List[List], Dict]:
    agg: Dict[str, int] = {}
    for tk in tickers:
        sec = sector_fn(tk)
        agg[sec] = agg.get(sec, 0) + 1
    n = len(tickers) or 1
    rows, largest = [], 0.0
    for sec, cnt in sorted(agg.items(), key=lambda kv: (-kv[1], kv[0])):
        share = round(100.0 * cnt / n, 2)
        largest = max(largest, share)
        rows.append([sec, cnt, share, "CONCENTRATED" if share > 30.0 else "OK"])
    return rows, {"n_names": len(tickers), "largest_sector_share_pct": round(largest, 2),
                  "unknown_share_pct": round(100.0 * agg.get("Unknown", 0) / n, 2),
                  "n_sectors": len(agg)}


# --------------------------------------------------------------------------- #
# Decisions + thresholds (DECLARED A-PRIORI, before the comparative result is read).
# --------------------------------------------------------------------------- #
DEC_KEEP = "KEEP_CURRENT_CHAMPION"
DEC_KEEP_PENDING = "KEEP_CURRENT_CHAMPION_PENDING_MORE_DATA"
DEC_REVALIDATE = "RESEARCH_REVALIDATION_REQUIRED"
DEC_BLOCKED_DATA = "BLOCKED_DATA_MISSING"
DEC_BLOCKED_ERROR = "BLOCKED_RUNNER_ERROR"
ALLOWED_DECISIONS = (DEC_KEEP, DEC_KEEP_PENDING, DEC_REVALIDATE, DEC_BLOCKED_DATA, DEC_BLOCKED_ERROR)
FORBIDDEN_DECISIONS = ("LIVE_TRADING_READY", "ORDER_READY", "AUTOMATION_READY", "PAPER_TRADER_READY",
                       "STRONG_ALPHA_FOUND_READY_FOR_REVIEW", "CHAMPION_REPLACED")

# Data-integrity guard: the recompute must reproduce the frozen sector-neutral columns with the ORIGINAL
# sectors. This is a numeric faithfulness check, NOT part of the alpha decision.
REPRO_MAX_ABS_ERR = 1e-4            # frozen composite_sn vs recomputed-with-original-sectors
REPRO_MIN_SPEARMAN = 0.9999        # rank fidelity of the reproduction

# Alpha decision ladder (declared before reading the comparison):
KEEP_MIN_FULL_SPEARMAN = 0.98      # champion vs shadow rank correlation over all (month,ticker)
KEEP_MIN_TOP25_OVERLAP = 0.80
KEEP_MIN_TOP50_OVERLAP = 0.80
KEEP_MIN_IC_T = 2.0                # shadow IC t-stat must clear the project's monitor bar
KEEP_IC_T_BAND = 0.25             # shadow IC t within +/-25% of the champion's own recomputed IC t
REVAL_MAX_FULL_SPEARMAN = 0.90     # below this the repaired sectors MATERIALLY change ranks
MONITOR_MIN_IC_T = 2.0

DECISION_LOGIC = [
    "GUARD: if the recompute cannot reproduce the frozen sector-neutral composite with the ORIGINAL "
    "sectors (max_abs_err > %.0e OR reproduction Spearman < %.4f) -> RESEARCH_REVALIDATION_REQUIRED "
    "(data-integrity: the shadow recompute cannot be trusted)." % (REPRO_MAX_ABS_ERR, REPRO_MIN_SPEARMAN),
    "REVALIDATE if ANY of: full-panel rank Spearman(champion, shadow) < %.2f; OR shadow IC t < %.1f "
    "while champion IC t >= %.1f; OR shadow IC t sign flips vs champion; OR shadow net25 <= 0 while "
    "champion net25 > 0; OR a subperiod (pre-2020 / 2020+) IC or spread sign flips under the shadow that "
    "held under the champion." % (REVAL_MAX_FULL_SPEARMAN, MONITOR_MIN_IC_T, MONITOR_MIN_IC_T),
    "KEEP only if ALL of: full-panel Spearman >= %.2f; Top25 overlap >= %.2f; Top50 overlap >= %.2f; "
    "shadow IC t >= %.1f and same sign and within +/-%.0f%% of champion IC t; shadow net25 > 0; net50 "
    "sign matches champion; no subperiod sign flip." % (KEEP_MIN_FULL_SPEARMAN, KEEP_MIN_TOP25_OVERLAP,
                                                        KEEP_MIN_TOP50_OVERLAP, KEEP_MIN_IC_T,
                                                        KEEP_IC_T_BAND * 100),
    "KEEP_PENDING otherwise: champion-vs-shadow metrics are broadly consistent (no materiality trigger) "
    "but not tight enough to fully affirm; with only ~35/63 paper-test trading days elapsed, hold the "
    "champion and re-evaluate under repaired sectors at the next quarter boundary (do NOT auto-replace).",
]


def _same_sign(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return False
    return (a >= 0) == (b >= 0)


def _subperiod_sign_flip(champ: Dict, shad: Dict) -> List[str]:
    flips = []
    for pk in ("pre2020", "post2020"):
        for metric in ("mean_ic", "mean_spread"):
            cv = champ["subperiod"][pk].get(metric)
            sv = shad["subperiod"][pk].get(metric)
            if cv is not None and sv is not None and cv != 0 and not _same_sign(cv, sv):
                flips.append("%s.%s (%s -> %s)" % (pk, metric, cv, sv))
    return flips


def decide(*, repro_ok: bool, full_spearman: Optional[float], top25_overlap: float, top50_overlap: float,
           champ: Dict, shadow: Dict) -> Tuple[str, List[str]]:
    reasons: List[str] = []
    ic_c, ic_s = champ.get("ic_t_stat"), shadow.get("ic_t_stat")
    n25_c, n25_s = champ.get("net25_spread"), shadow.get("net25_spread")
    n50_c, n50_s = champ.get("net50_spread"), shadow.get("net50_spread")
    flips = _subperiod_sign_flip(champ, shadow)

    if not repro_ok:
        return DEC_REVALIDATE, ["Data-integrity guard failed: the shadow recompute could not reproduce "
                                "the frozen sector-neutral composite with the original sectors; the "
                                "comparison cannot be trusted."]

    fs = full_spearman if full_spearman is not None else 1.0
    # --- materiality triggers -> REVALIDATE ---
    if full_spearman is not None and full_spearman < REVAL_MAX_FULL_SPEARMAN:
        reasons.append("Repaired sectors materially change ranks: full-panel Spearman %.4f < %.2f."
                       % (full_spearman, REVAL_MAX_FULL_SPEARMAN))
    if ic_s is not None and ic_c is not None and abs(ic_c) >= MONITOR_MIN_IC_T and abs(ic_s) < MONITOR_MIN_IC_T:
        reasons.append("Shadow IC t-stat %.2f falls below the monitor bar %.1f while the champion clears "
                       "it (%.2f)." % (ic_s, MONITOR_MIN_IC_T, ic_c))
    if ic_s is not None and ic_c is not None and not _same_sign(ic_c, ic_s):
        reasons.append("Shadow IC t-stat sign flips vs champion (%.2f -> %.2f)." % (ic_c, ic_s))
    if n25_s is not None and n25_c is not None and n25_c > 0 and n25_s <= 0:
        reasons.append("Shadow cost-adjusted net-25bps spread is non-positive (%.5f) while the champion "
                       "is positive (%.5f)." % (n25_s, n25_c))
    if flips:
        reasons.append("Subperiod sign instability introduced by repaired sectors: %s." % "; ".join(flips))
    if reasons:
        return DEC_REVALIDATE, reasons

    # --- clean affirmation -> KEEP ---
    within_band = (ic_c is not None and ic_s is not None and ic_c != 0
                   and abs(ic_s - ic_c) <= KEEP_IC_T_BAND * abs(ic_c))
    keep_ok = (full_spearman is not None and full_spearman >= KEEP_MIN_FULL_SPEARMAN
               and top25_overlap >= KEEP_MIN_TOP25_OVERLAP and top50_overlap >= KEEP_MIN_TOP50_OVERLAP
               and ic_s is not None and abs(ic_s) >= KEEP_MIN_IC_T and _same_sign(ic_c, ic_s)
               and within_band and n25_s is not None and n25_s > 0 and _same_sign(n50_c, n50_s))
    if keep_ok:
        return DEC_KEEP, ["Repaired sectors preserve the champion: full-panel Spearman %.4f, Top25 "
                          "overlap %.2f, Top50 overlap %.2f, shadow IC t %.2f (champion %.2f), shadow "
                          "net25 %.5f > 0, no subperiod sign flip." % (fs, top25_overlap, top50_overlap,
                                                                       ic_s or 0.0, ic_c or 0.0,
                                                                       n25_s or 0.0)]
    # --- broadly consistent but not tight, and mid paper-test -> KEEP_PENDING ---
    return DEC_KEEP_PENDING, ["No materiality trigger fired, but the champion-vs-shadow agreement is not "
                              "tight enough to fully affirm (Spearman %s, Top25 overlap %.2f, Top50 "
                              "overlap %.2f, shadow IC t %s vs champion %s). With ~35/63 paper-test days "
                              "elapsed, hold the champion and re-evaluate under repaired sectors at the "
                              "next quarter boundary." % (fs, top25_overlap, top50_overlap, ic_s, ic_c)]


# --------------------------------------------------------------------------- #
# Paths / artifacts.
# --------------------------------------------------------------------------- #
_DEF_PANEL = (_REPO_ROOT / "research" / "output"
              / "phase10l_historical_sector_neutral_scored_panel_reconstruction"
              / "historical_sector_neutral_scored_panel.csv")
_DEF_FUND = _REPO_ROOT / "research" / "data" / "eodhd" / "raw" / "fundamentals"
_DEF_CURATED = _REPO_ROOT / "research" / "input" / "phase2k_p_sector_map_current.csv"
_DEF_13A = (_REPO_ROOT / "research" / "output" / "phase13a_current_champion_alpha_paper_test_package"
            / "phase13a_current_champion_alpha_paper_test_package.json")
_DEFAULT_OUT = (_REPO_ROOT / "research" / "output"
                / "phase16a_sector_metadata_integrity_and_shadow_revalidation")

_ARTIFACTS = {
    "report": "phase16a_sector_integrity_report.json",
    "shadow": "phase16a_shadow_revalidation_report.json",
    "resolved": "sector_metadata_resolved.csv",
    "unresolved": "sector_metadata_unresolved.csv",
    "attempts": "sector_metadata_resolution_attempts.csv",
    "coverage": "sector_metadata_coverage.json",
    "top25_exp": "top25_sector_exposure.csv",
    "top50_exp": "top50_sector_exposure.csv",
    "bottom25_exp": "bottom25_sector_exposure.csv",
    "comparison": "original_vs_shadow_comparison.csv",
    "rank_moves": "latest_month_rank_comparison.csv",
    "repro": "reproduction_check.csv",
    "subperiod": "subperiod_stability_comparison.csv",
    "secret_audit": "secret_safety_audit.csv",
}

SAFETY_BADGES = ["PAPER ONLY", "MANUAL REVIEW", "NO BROKER EXECUTION", "AUTOMATION OFF", "NO LIVE ORDERS",
                 "PREVIEW ONLY", "OWNED DATA ONLY", "NO CHAMPION MUTATION", "NO PROVIDER CALL"]


# --------------------------------------------------------------------------- #
# Run.
# --------------------------------------------------------------------------- #
def _blocker(out_dir: Path, decision: str, reason: str, repro_cmd: str, verbose: bool) -> Dict:
    rep = {"phase": PHASE, "decision": decision, "reason": reason,
           "allowed_decisions": list(ALLOWED_DECISIONS), "forbidden_decisions": list(FORBIDDEN_DECISIONS),
           "exact_next_command": repro_cmd, "performs_network": PERFORMS_NETWORK, "offline": True,
           "uses_owned_data_only": True, "mutated_champion": False, "wrote_to_paper_trader": False,
           "creates_orders": False, "creates_automation": False, "live_trading": False,
           "safety_badges": SAFETY_BADGES}
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / _ARTIFACTS["report"], rep)
    if verbose:
        print("[%s] decision=%s | %s" % (PHASE, decision, reason))
    return rep


def run(out_dir: Optional[Path] = None, *, panel_csv: Optional[Path] = None,
        fund_dir: Optional[Path] = None, curated_csv: Optional[Path] = None,
        pkg_json: Optional[Path] = None, verbose: bool = True) -> Dict:
    out_dir = Path(out_dir) if out_dir else _DEFAULT_OUT
    panel_csv = Path(panel_csv) if panel_csv else _DEF_PANEL
    fund_dir = Path(fund_dir) if fund_dir else _DEF_FUND
    curated_csv = Path(curated_csv) if curated_csv else _DEF_CURATED
    pkg_json = Path(pkg_json) if pkg_json else _DEF_13A

    if not panel_csv.exists():
        return _blocker(out_dir, DEC_BLOCKED_DATA, "frozen scored panel not found at %s" % _rel(panel_csv),
                        "python research/run_phase16a_sector_metadata_integrity_and_shadow_revalidation.py",
                        verbose)
    rows = _read_csv_rows(panel_csv)
    if not rows:
        return _blocker(out_dir, DEC_BLOCKED_DATA, "panel at %s is empty" % _rel(panel_csv),
                        "python research/run_phase16a_sector_metadata_integrity_and_shadow_revalidation.py",
                        verbose)

    # normalise ticker + build month index
    for r in rows:
        r["ticker"] = _norm_label(r.get("ticker")).upper()
    month_index: Dict[str, List[int]] = {}
    for i, r in enumerate(rows):
        mth = _norm_label(r.get("rebalance_date"))[:7]
        if len(mth) == 7:
            month_index.setdefault(mth, []).append(i)

    # original sectors (from the panel) + owned sources
    orig_sector_map: Dict[str, str] = {}
    for r in rows:
        sec = _norm_label(r.get("sector")) or "Unknown"
        orig_sector_map.setdefault(r["ticker"], "Unknown" if _is_unknown(sec) else sec)
    eodhd = load_eodhd_sector_meta(fund_dir)
    curated = load_curated_map(curated_csv)

    # ------------------------------------------------------------------ #
    # PART E - sector metadata resolution for the CURRENT champion universe.
    # ------------------------------------------------------------------ #
    latest = _latest_month(rows)
    rep_rows = _representative_rows(rows, latest)             # ticker -> row index (the 234 cross-section)
    ranked_orig = sorted(rep_rows.keys(),
                         key=lambda tk: (-_to_float(rows[rep_rows[tk]].get(SIGNAL_COL)), tk))
    n_ranked = len(ranked_orig)
    top25 = ranked_orig[:25]
    top50 = ranked_orig[:50]
    bottom25 = ranked_orig[-25:]

    resolved_rows: List[List] = []
    unresolved_rows: List[List] = []
    attempt_rows: List[List] = []
    repaired_sector: Dict[str, str] = {}          # ticker -> repaired sector (owned data)
    resolution_meta: Dict[str, Dict] = {}
    for tk in sorted(rep_rows.keys()):
        res, attempts = resolve_one(tk, eodhd, curated)
        for a in attempts:
            attempt_rows.append([a["ticker"], a["source_family"], a["outcome"], a["value"]])
        orig = orig_sector_map.get(tk, "Unknown")
        if res is None:
            why = ("no owned EODHD fundamentals file for this ticker" if eodhd.get(tk) is None
                   else "owned metadata present but no canonical-GICS sector derivable without guessing")
            unresolved_rows.append([tk, orig, why, "kept Unknown - not fabricated (owned data only)"])
            resolution_meta[tk] = {"resolved_sector": "Unknown", "source": "none", "confidence": "NONE"}
            continue
        repaired_sector[tk] = res["sector"]
        resolution_meta[tk] = {"resolved_sector": res["sector"], "industry": res["industry"],
                               "source": res["source_family"], "source_field": res["source_field"],
                               "confidence": res["confidence"], "point_in_time": res["point_in_time"]}
        resolved_rows.append([tk, orig, res["sector"], res["industry"], res["source_family"],
                              res["source_field"], res["source_file"], res["confidence"],
                              "current-as-of (not PIT)", res["reason"]])

    # repaired sector function for the whole panel: repaired where resolvable, else keep original label
    def repaired_fn(tk: str) -> str:
        tk = tk.upper()
        if tk in repaired_sector:
            return repaired_sector[tk]
        cur = orig_sector_map.get(tk, "Unknown")
        return cur if not _is_unknown(cur) else "Unknown"

    def original_fn(tk: str) -> str:
        cur = orig_sector_map.get(tk.upper(), "Unknown")
        return cur if not _is_unknown(cur) else "Unknown"

    def _cov(book: List[str]) -> Dict:
        resolved = sum(1 for tk in book if not _is_unknown(repaired_fn(tk)))
        before = sum(1 for tk in book if not _is_unknown(original_fn(tk)))
        n = len(book) or 1
        return {"n_names": len(book), "resolved_after": resolved,
                "resolved_pct_after": round(100.0 * resolved / n, 2), "resolved_before": before,
                "resolved_pct_before": round(100.0 * before / n, 2),
                "unknown_after": len(book) - resolved, "unknown_pct_after": round(100.0 * (len(book) - resolved) / n, 2)}

    coverage = {
        "phase": PHASE, "signal_date": max((_norm_label(rows[rep_rows[tk]].get("rebalance_date"))
                                            for tk in rep_rows), default=latest),
        "cross_section_month": latest, "champion_signal": SIGNAL_COL,
        "n_ranked": n_ranked,
        "all_234": _cov(ranked_orig), "top25": _cov(top25), "top50": _cov(top50), "bottom25": _cov(bottom25),
        "owned_sources": {"eodhd_fundamentals_tickers": len(eodhd), "curated_map_tickers": len(curated),
                          "norgate_local": False},
        "n_resolved": len(repaired_sector), "n_unresolved": len(unresolved_rows),
        "resolution_confidence": {
            "HIGH": sum(1 for m in resolution_meta.values() if m.get("confidence") == CONF_HIGH),
            "MEDIUM": sum(1 for m in resolution_meta.values() if m.get("confidence") == CONF_MEDIUM),
            "NONE": sum(1 for m in resolution_meta.values() if m.get("confidence") == "NONE")},
    }

    top25_exp_after, top25_sum_after = _exposure(top25, repaired_fn)
    top50_exp_after, top50_sum_after = _exposure(top50, repaired_fn)
    bottom25_exp_after, bottom25_sum_after = _exposure(bottom25, repaired_fn)

    # ------------------------------------------------------------------ #
    # PART F - shadow sector-neutral revalidation (recompute ONLY the SN transform).
    # ------------------------------------------------------------------ #
    comp_orig_recomputed = recompute_composite(rows, month_index, original_fn)
    comp_shadow = recompute_composite(rows, month_index, repaired_fn)

    # data-integrity: reproduce the frozen composite_sn with the ORIGINAL sectors
    repro_pairs = [(i, _to_float(rows[i].get(SIGNAL_COL)), comp_orig_recomputed.get(i))
                   for i in range(len(rows))]
    repro_pairs = [(i, a, b) for (i, a, b) in repro_pairs if a is not None and b is not None]
    max_abs_err = max((abs(a - b) for (_i, a, b) in repro_pairs), default=None)
    repro_spearman = _spearman([a for (_i, a, _b) in repro_pairs], [b for (_i, _a, b) in repro_pairs]) \
        if len(repro_pairs) >= 3 else None
    repro_ok = bool(max_abs_err is not None and max_abs_err <= REPRO_MAX_ABS_ERR
                    and repro_spearman is not None and repro_spearman >= REPRO_MIN_SPEARMAN)

    # monthly cross-sections shared by champion (frozen comp) and shadow (recomputed comp), one row/ticker
    monthly: Dict[str, List[Dict]] = {}
    for mth, idxs in month_index.items():
        best: Dict[str, Tuple[str, int]] = {}
        for i in idxs:
            tk = rows[i]["ticker"]
            if not tk or _to_float(rows[i].get(SIGNAL_COL)) is None:
                continue
            rb = _norm_label(rows[i].get("rebalance_date"))
            if tk not in best or rb > best[tk][0]:
                best[tk] = (rb, i)
        recs = []
        for tk, (_rb, i) in best.items():
            recs.append({"ticker": tk, "orig": _to_float(rows[i].get(SIGNAL_COL)),
                         "shadow": comp_shadow.get(i), "fwd": _to_float(rows[i].get(FWD))})
        monthly[mth] = recs

    champ_eval = _evaluate_score(monthly, "orig")
    shadow_eval = _evaluate_score(monthly, "shadow")

    # full-panel rank correlation champion vs shadow (over all representative rows with both scores)
    fp_a, fp_b = [], []
    for recs in monthly.values():
        for r in recs:
            if r["orig"] is not None and r["shadow"] is not None:
                fp_a.append(r["orig"])
                fp_b.append(r["shadow"])
    full_spearman = _spearman(fp_a, fp_b) if len(fp_a) >= 3 else None

    # latest-month rank comparison (champion = frozen comp; shadow = recomputed comp)
    latest_recs = {r["ticker"]: r for r in monthly.get(latest, [])}
    shadow_ranked = sorted([tk for tk in ranked_orig if latest_recs.get(tk, {}).get("shadow") is not None],
                           key=lambda tk: (-latest_recs[tk]["shadow"], tk))
    s_top25, s_top50, s_bottom25 = shadow_ranked[:25], shadow_ranked[:50], shadow_ranked[-25:]
    top25_overlap = _overlap(top25, s_top25)
    top50_overlap = _overlap(top50, s_top50)
    bottom25_overlap = _overlap(bottom25, s_bottom25)
    # latest-month rank Spearman on common names
    lm_a, lm_b = [], []
    for tk in ranked_orig:
        r = latest_recs.get(tk)
        if r and r["orig"] is not None and r["shadow"] is not None:
            lm_a.append(r["orig"])
            lm_b.append(r["shadow"])
    latest_spearman = _spearman(lm_a, lm_b) if len(lm_a) >= 3 else None

    shadow_rank_of = {tk: n + 1 for n, tk in enumerate(shadow_ranked)}
    rank_move_rows: List[List] = []
    for n, tk in enumerate(ranked_orig, start=1):
        r = latest_recs.get(tk, {})
        sr = shadow_rank_of.get(tk)
        rank_move_rows.append([n, tk, original_fn(tk), repaired_fn(tk),
                               _round(r.get("orig"), 6), _round(r.get("shadow"), 6),
                               sr if sr is not None else "", (n - sr) if sr is not None else "",
                               "TOP25" if n <= 25 else ("TOP50" if n <= 50 else
                               ("BOTTOM25" if n > n_ranked - 25 else "MIDDLE"))])

    # ------------------------------------------------------------------ #
    # DECISION (logic declared above, before this call).
    # ------------------------------------------------------------------ #
    decision, decision_reasons = decide(repro_ok=repro_ok, full_spearman=full_spearman,
                                        top25_overlap=top25_overlap, top50_overlap=top50_overlap,
                                        champ=champ_eval, shadow=shadow_eval)
    if decision not in ALLOWED_DECISIONS or decision in FORBIDDEN_DECISIONS:
        decision = DEC_REVALIDATE

    # ------------------------------------------------------------------ #
    # Write artifacts.
    # ------------------------------------------------------------------ #
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / _ARTIFACTS["resolved"],
               ["ticker", "original_sector", "resolved_sector", "resolved_industry", "source_family",
                "source_field", "source_file", "confidence", "point_in_time", "reason"], resolved_rows)
    _write_csv(out_dir / _ARTIFACTS["unresolved"],
               ["ticker", "original_sector", "why_unresolved", "disposition"], unresolved_rows)
    _write_csv(out_dir / _ARTIFACTS["attempts"],
               ["ticker", "source_family", "outcome", "value"], attempt_rows)
    _write_csv(out_dir / _ARTIFACTS["top25_exp"], ["sector", "n_names", "weight_pct", "flag"], top25_exp_after)
    _write_csv(out_dir / _ARTIFACTS["top50_exp"], ["sector", "n_names", "weight_pct", "flag"], top50_exp_after)
    _write_csv(out_dir / _ARTIFACTS["bottom25_exp"], ["sector", "n_names", "weight_pct", "flag"],
               bottom25_exp_after)
    coverage["top25_exposure_after"] = top25_sum_after
    coverage["top50_exposure_after"] = top50_sum_after
    coverage["bottom25_exposure_after"] = bottom25_sum_after
    _write_json(out_dir / _ARTIFACTS["coverage"], coverage)

    _write_csv(out_dir / _ARTIFACTS["repro"],
               ["check", "value", "threshold", "status"],
               [["frozen_composite_sn_max_abs_reconstruction_error", _round(max_abs_err, 12),
                 "<= %.0e" % REPRO_MAX_ABS_ERR, "PASS" if (max_abs_err is not None and max_abs_err <= REPRO_MAX_ABS_ERR) else "FAIL"],
                ["reproduction_rank_spearman", _round(repro_spearman, 8), ">= %.4f" % REPRO_MIN_SPEARMAN,
                 "PASS" if (repro_spearman is not None and repro_spearman >= REPRO_MIN_SPEARMAN) else "FAIL"],
                ["reproduction_rows_checked", len(repro_pairs), ">0", "PASS" if repro_pairs else "FAIL"],
                ["reproduction_overall", repro_ok, "both pass", "PASS" if repro_ok else "FAIL"]])

    cmp_rows = [
        ["resolved_sector_coverage_top25_pct", coverage["top25"]["resolved_pct_before"],
         coverage["top25"]["resolved_pct_after"], ""],
        ["resolved_sector_coverage_top50_pct", coverage["top50"]["resolved_pct_before"],
         coverage["top50"]["resolved_pct_after"], ""],
        ["resolved_sector_coverage_all234_pct", coverage["all_234"]["resolved_pct_before"],
         coverage["all_234"]["resolved_pct_after"], ""],
        ["full_panel_rank_spearman", "", _round(full_spearman, 6), "champion vs shadow, all (month,ticker)"],
        ["latest_month_rank_spearman", "", _round(latest_spearman, 6), "234-name cross-section"],
        ["top25_overlap", "", _round(top25_overlap, 4), "fraction of champion Top25 retained by shadow"],
        ["top50_overlap", "", _round(top50_overlap, 4), ""],
        ["bottom25_overlap", "", _round(bottom25_overlap, 4), ""],
        ["top25_turnover", "", _round(1.0 - top25_overlap, 4), "1 - overlap"],
        ["top50_turnover", "", _round(1.0 - top50_overlap, 4), ""],
        ["ic_t_stat_63d", champ_eval["ic_t_stat"], shadow_eval["ic_t_stat"], "self-recompute; 10-D headline 2.665"],
        ["mean_ic_63d", champ_eval["mean_ic"], shadow_eval["mean_ic"], ""],
        ["mean_gross_spread", champ_eval["mean_gross_spread"], shadow_eval["mean_gross_spread"], "quintile L/S"],
        ["net25_spread", champ_eval["net25_spread"], shadow_eval["net25_spread"], ""],
        ["net50_spread", champ_eval["net50_spread"], shadow_eval["net50_spread"], ""],
        ["mean_turnover", champ_eval["mean_turnover"], shadow_eval["mean_turnover"], ""],
        ["cumulative_spread", champ_eval["cumulative_spread"], shadow_eval["cumulative_spread"], ""],
        ["max_drawdown", champ_eval["max_drawdown"], shadow_eval["max_drawdown"], "of cumulative L/S curve"],
        ["top25_largest_sector_share_pct_after", "", top25_sum_after["largest_sector_share_pct"],
         "repaired shadow Top25 concentration"],
        ["top50_largest_sector_share_pct_after", "", top50_sum_after["largest_sector_share_pct"], ""],
    ]
    _write_csv(out_dir / _ARTIFACTS["comparison"], ["metric", "champion", "shadow", "note"], cmp_rows)

    _write_csv(out_dir / _ARTIFACTS["rank_moves"],
               ["champion_rank", "ticker", "original_sector", "repaired_sector", "champion_composite_sn",
                "shadow_composite_sn", "shadow_rank", "rank_delta(up=+)", "champion_bucket"], rank_move_rows)

    sub_rows: List[List] = []
    for pk in ("pre2020", "post2020"):
        c = champ_eval["subperiod"][pk]
        s = shadow_eval["subperiod"][pk]
        sub_rows.append([pk, c["n_months"], c["mean_ic"], s["mean_ic"], c["ic_t"], s["ic_t"],
                         c["mean_spread"], s["mean_spread"],
                         "SIGN_FLIP" if (c["mean_spread"] is not None and s["mean_spread"] is not None
                                        and c["mean_spread"] != 0 and not _same_sign(c["mean_spread"], s["mean_spread"]))
                         else "STABLE"])
    _write_csv(out_dir / _ARTIFACTS["subperiod"],
               ["subperiod", "n_months", "champion_mean_ic", "shadow_mean_ic", "champion_ic_t",
                "shadow_ic_t", "champion_mean_spread", "shadow_mean_spread", "spread_sign"], sub_rows)

    # frozen 13-A context (read-only)
    pkg = _read_json(pkg_json) if pkg_json.exists() else {}
    frozen_ctx = {"phase13a_signal_date": pkg.get("signal_date"),
                  "phase13a_price_coverage": pkg.get("price_coverage"),
                  "phase13a_ic_t_63d": (pkg.get("expected_benchmark") or {}).get("ic_t_63d"),
                  "phase13a_quarterly_net_25bps": (pkg.get("expected_benchmark") or {}).get("quarterly_net_25bps"),
                  "phase13a_sector_coverage": pkg.get("sector_coverage")}

    shadow_report = {
        "phase": PHASE, "decision": decision, "decision_reasons": decision_reasons,
        "decision_logic_declared_before_result": True, "decision_logic": DECISION_LOGIC,
        "allowed_decisions": list(ALLOWED_DECISIONS), "forbidden_decisions": list(FORBIDDEN_DECISIONS),
        "champion_signal": SIGNAL_COL,
        "objective": ("recompute ONLY the sector-neutral transform with owned repaired sector labels and "
                      "test whether the champion's validated edge survives; NOT a champion replacement"),
        "not_this_phase": ["new factor", "reweighting", "threshold tuning", "champion retune",
                           "favourable-period selection", "automatic champion replacement", "orders",
                           "automation", "live trading"],
        "source_panel": _rel(panel_csv), "source_fundamentals": _rel(fund_dir),
        "source_curated_map": _rel(curated_csv), "source_phase13a": _rel(pkg_json),
        "reproduction": {"max_abs_error": _round(max_abs_err, 12), "rank_spearman": _round(repro_spearman, 8),
                         "rows_checked": len(repro_pairs), "reproduces_frozen_composite": repro_ok,
                         "recomputed_original_ic_t": champ_eval["ic_t_stat"],
                         "note": ("the recompute reproduces the frozen sector-neutral composite with the "
                                  "ORIGINAL sectors; only the sector grouping changes for the shadow")},
        "latest_cross_section": {"month": latest, "n_names": n_ranked,
                                 "rank_spearman_champion_vs_shadow": _round(latest_spearman, 6),
                                 "top25_overlap": _round(top25_overlap, 4),
                                 "top50_overlap": _round(top50_overlap, 4),
                                 "bottom25_overlap": _round(bottom25_overlap, 4),
                                 "top25_turnover": _round(1.0 - top25_overlap, 4),
                                 "top50_turnover": _round(1.0 - top50_overlap, 4),
                                 "shadow_top25_largest_sector_share_pct": top25_sum_after["largest_sector_share_pct"],
                                 "shadow_top50_largest_sector_share_pct": top50_sum_after["largest_sector_share_pct"]},
        "full_panel": {"rank_spearman_champion_vs_shadow": _round(full_spearman, 6),
                       "champion": champ_eval, "shadow": shadow_eval,
                       "min_names_per_month": MIN_NAMES_PER_MONTH, "quantile": QUANTILE,
                       "cost_bps": {"net25": 25, "net50": 50}},
        "sector_coverage_summary": {"all234_before_pct": coverage["all_234"]["resolved_pct_before"],
                                    "all234_after_pct": coverage["all_234"]["resolved_pct_after"],
                                    "top25_before_pct": coverage["top25"]["resolved_pct_before"],
                                    "top25_after_pct": coverage["top25"]["resolved_pct_after"],
                                    "top50_before_pct": coverage["top50"]["resolved_pct_before"],
                                    "top50_after_pct": coverage["top50"]["resolved_pct_after"],
                                    "n_resolved": len(repaired_sector), "n_unresolved": len(unresolved_rows)},
        "phase13a_context": frozen_ctx,
        # --- safety ---
        "performs_network": PERFORMS_NETWORK, "offline": True, "uses_owned_data_only": True,
        "recomputed_only_sector_neutral_transform": True, "added_factors": False, "changed_weights": False,
        "tuned_thresholds": False, "selected_favourable_period": False, "mutated_champion": False,
        "modified_phase13a_package": False, "replaced_champion": False, "promotes_to_live": False,
        "creates_paper_trader_signals": False, "creates_trade_decisions": False, "creates_orders": False,
        "creates_automation": False, "creates_broker_connection": False, "wrote_to_paper_trader": False,
        "live_trading": False, "deploy": False, "api_key_printed": False, "api_key_written_to_disk": False,
        "safety_badges": SAFETY_BADGES,
        "exact_next_command": ("review research/output/phase16a_sector_metadata_integrity_and_shadow_"
                               "revalidation/phase16a_shadow_revalidation_report.json"),
        "constraints_honored": ["offline (no network/key/provider probe)", "owned/local data only",
                                "recompute ONLY the sector-neutral transform", "no new factor",
                                "no reweight", "no threshold tune", "no champion mutation",
                                "did not modify/overwrite the Phase 13-A package", "no Paper Trader writes",
                                "NO orders", "NO automation", "NO broker", "NO live trading", "no deploy",
                                "no GCP", "no package install", "pure stdlib", "no commit inside runner",
                                "no push", "no fabricated sectors"],
    }
    _write_json(out_dir / _ARTIFACTS["shadow"], shadow_report)

    integrity_report = {
        "phase": PHASE, "objective": ("resolve the current champion universe's sector metadata from owned "
                                      "data only and report before/after coverage; NOT a champion change"),
        "champion_signal": SIGNAL_COL, "cross_section_month": latest, "n_ranked": n_ranked,
        "coverage": coverage, "sector_shadow_decision": decision, "sector_shadow_reasons": decision_reasons,
        "owned_sources_priority": [SRC_NORGATE, SRC_EODHD_GIC, SRC_EODHD_SECTOR, SRC_EODHD_NORM,
                                   SRC_PRIOR_MAP, SRC_EODHD_PROFILE],
        "source_panel": _rel(panel_csv), "source_fundamentals": _rel(fund_dir),
        "source_curated_map": _rel(curated_csv),
        "performs_network": PERFORMS_NETWORK, "offline": True, "uses_owned_data_only": True,
        "fabricated_sectors": False, "sector_labels_point_in_time": False, "mutated_champion": False,
        "modified_phase13a_package": False, "wrote_to_paper_trader": False, "creates_orders": False,
        "creates_automation": False, "live_trading": False, "safety_badges": SAFETY_BADGES,
        "required_artifacts": list(_ARTIFACTS.values()),
        "exact_next_command": ("review research/output/phase16a_sector_metadata_integrity_and_shadow_"
                               "revalidation/phase16a_sector_integrity_report.json"),
    }
    _write_json(out_dir / _ARTIFACTS["report"], integrity_report)

    # secret-safety audit LAST so it scans every artifact just written.
    sec_rows, leak_clean = _secret_safety_audit(out_dir)
    _write_csv(out_dir / _ARTIFACTS["secret_audit"],
               ["file", "clean", "keyed_url_marker", "key_value_present"],
               [[r["file"], r["clean"], r["keyed_url_marker"], r["key_value_present"]] for r in sec_rows])
    integrity_report["secret_safety_leak_scan_clean"] = leak_clean
    shadow_report["secret_safety_leak_scan_clean"] = leak_clean
    _write_json(out_dir / _ARTIFACTS["report"], integrity_report)
    _write_json(out_dir / _ARTIFACTS["shadow"], shadow_report)

    if verbose:
        print("[%s] decision=%s | resolved=%d/%d (top25 %.0f%%->%.0f%%, top50 %.0f%%->%.0f%%) | "
              "full_spearman=%s top25_ov=%.2f top50_ov=%.2f | champ_ic_t=%s shadow_ic_t=%s | "
              "champ_net25=%s shadow_net25=%s | repro_ok=%s max_err=%s | leak_clean=%s"
              % (PHASE, decision, len(repaired_sector), n_ranked,
                 coverage["top25"]["resolved_pct_before"], coverage["top25"]["resolved_pct_after"],
                 coverage["top50"]["resolved_pct_before"], coverage["top50"]["resolved_pct_after"],
                 _round(full_spearman, 4), top25_overlap, top50_overlap,
                 champ_eval["ic_t_stat"], shadow_eval["ic_t_stat"],
                 champ_eval["net25_spread"], shadow_eval["net25_spread"],
                 repro_ok, _round(max_abs_err, 8), leak_clean))
    return shadow_report


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 16-A sector integrity + shadow SN revalidation")
    p.add_argument("--panel", default=None)
    p.add_argument("--fund-dir", default=None)
    p.add_argument("--curated-csv", default=None)
    p.add_argument("--pkg-json", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ns = _parse_args(argv)
    try:
        rep = run(out_dir=ns.out, panel_csv=ns.panel, fund_dir=ns.fund_dir, curated_csv=ns.curated_csv,
                  pkg_json=ns.pkg_json, verbose=not ns.quiet)
    except Exception as exc:  # noqa: BLE001 - surface a repro, never a crash
        out_dir = Path(ns.out) if ns.out else _DEFAULT_OUT
        rep = _blocker(out_dir, DEC_BLOCKED_ERROR, "unhandled error: %s: %s" % (type(exc).__name__, exc),
                       "python research/run_phase16a_sector_metadata_integrity_and_shadow_revalidation.py",
                       not ns.quiet)
    return 0 if rep.get("decision") in ALLOWED_DECISIONS else 1


if __name__ == "__main__":
    raise SystemExit(main())
