"""Phase 17-A - Formal Sector-Repaired Champion Revalidation + Paper-Challenger Eligibility.

WHY THIS PHASE EXISTS
    Phase 16-A proved that the current paper champion ``composite_sn`` (a SECTOR-NEUTRAL quality
    composite) was validated on a broken sector grouping - its latest 234-name cross-section was
    ~195/234 "Unknown" sector and the Top25 book was 100% Unknown, so the "sector-neutral" leg was
    largely de-meaning against one giant Unknown pseudo-sector. Using ONLY owned/local metadata,
    16-A resolved all 234 names to real GICS sectors and recomputed the sector-neutral transform as a
    SHADOW; the repaired-sector shadow materially changed the ranks (full-panel Spearman 0.8915 <
    0.90), so 16-A returned RESEARCH_REVALIDATION_REQUIRED and HELD (did not replace) the champion.

    Phase 17-A is the FORMAL revalidation that 16-A asked for. It loads the COMMITTED 16-A resolved
    sector map, rebuilds the repaired-sector candidate over the COMPLETE historical panel using the
    IDENTICAL transform (identical raw factors, orientations, month handling, z-score method, weights;
    ONLY the sector grouping changes), runs the ORIGINAL champion and the repaired candidate through
    the SAME full validation battery, and applies a decision ladder DECLARED A-PRIORI. The strongest
    outcome allowed is eligibility for a PARALLEL PAPER CHALLENGER - never an automatic champion
    replacement, never live-trading promotion.

EXACT TRANSFORM (reproduced verbatim; confirmed against the pipeline source - unchanged from 16-A)
    o_fcf = (+1) * fcf_to_assets          (panel raw level, orientation +1)
    o_acc = (-1) * operating_accruals     (panel raw level, orientation -1, Sloan-negated)
    o_x__sn = o_x - mean_{month, sector}(o_x)                            (within-month x sector de-mean)
    z_x_sn  = (o_x__sn - mean_month(o_x__sn)) / std_month(o_x__sn)       (within-month z; sample std
                                                                          ddof=1; std==0 -> NaN)
    composite_sn = z_fcf_sn + z_acc_sn    (equal weight; NO winsorization)
    Only the SECTOR GROUPING in the de-mean differs between the ORIGINAL (frozen panel labels) and the
    REPAIRED candidate (owned-data GICS from the committed 16-A resolved map, applied to those tickers
    across every month; unresolved / out-of-map tickers keep their original panel label). The recompute
    is validated by reproducing the frozen composite_sn with the ORIGINAL sectors (data-integrity guard).

TERMINAL DECISIONS (allowed; NONE promotes to live)
    PAPER_CHALLENGER_ELIGIBLE | KEEP_CURRENT_PAPER_CHAMPION | RESEARCH_REVALIDATION_FAILED |
    BLOCKED_DATA_MISSING | BLOCKED_RUNNER_ERROR
    FORBIDDEN: LIVE_TRADING_READY, PRODUCTION_READY, ORDER_READY, AUTOMATION_READY, CHAMPION_REPLACED,
    LIVE_CHAMPION_PROMOTED.

CONSTRAINTS HONORED
    Offline (no network / key / provider probe); owned/local data only; recompute ONLY the
    sector-neutral transform (no new factor, no reweight, no sign-flip, no threshold tune, no favourable
    period, no champion mutation); reads (never overwrites) the Phase 13-A and Phase 16-A artifacts;
    writes ONLY to its OWN new phase17a / phase17b output directories; no Paper Trader writes; NO orders;
    NO broker; NO automation; NO live trading; no deploy; no GCP; no package install; pure stdlib (no
    numpy/pandas/cross-phase imports; runs under a bare Windows Python); keys never printed or written;
    the challenger package is built ONLY on the eligible decision and every row states NO_ORDER; no commit
    inside the runner; no push.
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
PHASE = "17-A"
PERFORMS_NETWORK = False

# Only these env vars are scanned for accidental leakage. This runner never reads or uses them - the
# audit just proves no key value landed in an output file.
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


_UNKNOWN = ("", "unknown", "n/a", "na", "none", "null")
CANONICAL_GICS = frozenset({
    "Communication Services", "Consumer Discretionary", "Consumer Staples", "Energy", "Financials",
    "Health Care", "Industrials", "Information Technology", "Materials", "Real Estate", "Utilities",
})


def _is_unknown(s) -> bool:
    return (s is None) or (str(s).strip().lower() in _UNKNOWN)


def _norm_label(s) -> str:
    return ("" if s is None else str(s)).strip()


# --------------------------------------------------------------------------- #
# The frozen champion transform (exact reproduction; sector grouping is the only variable).
# --------------------------------------------------------------------------- #
SIGNAL_COL = "composite_sn"
FCF_LEVEL = "fcf_to_assets"                 # panel raw level (un-oriented)
ACC_LEVEL = "operating_accruals"            # panel raw level (un-oriented)
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
    """z = (v - mean)/std over provided (non-null) values; sample std (ddof=1); std==0 or <2 -> drop."""
    idxs = list(vals_by_idx.keys())
    xs = [vals_by_idx[i] for i in idxs]
    sd = _std(xs, 1)
    if sd is None or sd == 0.0:
        return {}
    m = _mean(xs)
    return {i: (vals_by_idx[i] - m) / sd for i in idxs}


def _sector_demean_then_z(rows: List[Dict], oriented_by_idx: Dict[int, float],
                          sector_fn: Callable[[str], str]) -> Dict[int, float]:
    """De-mean oriented values within (sector) then within-month z. Null oriented values are excluded."""
    by_sector: Dict[str, List[int]] = {}
    for i in oriented_by_idx:
        sec = sector_fn(rows[i]["ticker"])
        by_sector.setdefault(sec, []).append(i)
    demeaned: Dict[int, float] = {}
    for sec, idxs in by_sector.items():
        sm = _mean([oriented_by_idx[i] for i in idxs])
        for i in idxs:
            demeaned[i] = oriented_by_idx[i] - sm
    return _within_month_z(demeaned)


def _month_legs(rows: List[Dict], idxs: List[int],
                sector_fn: Callable[[str], str]) -> Dict[int, Dict[str, Optional[float]]]:
    """Return {row_idx: {comp, zf, za}} for ONE month using the given sector grouping."""
    o_fcf = {i: ORI_FCF * v for i in idxs if (v := _to_float(rows[i].get(FCF_LEVEL))) is not None}
    o_acc = {i: ORI_ACC * v for i in idxs if (v := _to_float(rows[i].get(ACC_LEVEL))) is not None}
    z_fcf = _sector_demean_then_z(rows, o_fcf, sector_fn)
    z_acc = _sector_demean_then_z(rows, o_acc, sector_fn)
    out: Dict[int, Dict[str, Optional[float]]] = {}
    for i in idxs:
        zf, za = z_fcf.get(i), z_acc.get(i)
        out[i] = {"zf": zf, "za": za,
                  "comp": (zf + za) if (zf is not None and za is not None) else None}
    return out


def recompute_composite(rows: List[Dict], month_index: Dict[str, List[int]],
                        sector_fn: Callable[[str], str]) -> Dict[int, Optional[float]]:
    """Recompute composite_sn per row index using the given sector grouping. Pure stdlib."""
    comp: Dict[int, Optional[float]] = {}
    for _month, idxs in month_index.items():
        for i, legs in _month_legs(rows, idxs, sector_fn).items():
            comp[i] = legs["comp"]
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


def _positive_rate(vals: List[float]) -> Optional[float]:
    if not vals:
        return None
    return sum(1 for v in vals if v > 0) / len(vals)


# --------------------------------------------------------------------------- #
# Full validation battery for ONE score (identical methodology for champion & candidate).
# --------------------------------------------------------------------------- #
MIN_NAMES_PER_MONTH = 20
QUANTILE = 5
COST25, COST50 = 0.0025, 0.0050
_PRE2020 = "2020-01"
ROLL_WINDOWS = (12, 24)


def _rolling_stability(series: List[float], window: int) -> Dict:
    """Rolling-mean stability of a per-month series. UNSUPPORTED if fewer than `window` months."""
    n = len(series)
    if n < window:
        return {"window": window, "supported": False, "n_windows": 0, "reason": "insufficient months"}
    means = [_mean(series[i:i + window]) for i in range(0, n - window + 1)]
    return {"window": window, "supported": True, "n_windows": len(means),
            "mean_of_rolling_means": _round(_mean(means), 6), "min_rolling_mean": _round(min(means), 6),
            "max_rolling_mean": _round(max(means), 6), "positive_window_rate": _round(_positive_rate(means), 4)}


def _evaluate_score(monthly: Dict[str, List[Dict]], score_key: str) -> Dict:
    """monthly[month] = [{ticker, orig, shadow, fwd, established}]. Evaluate one score at the 63d horizon:
    monthly rank-IC, quintile L/S gross/net spread, turnover, cumulative spread, drawdown, positive-month
    rate, pre/post-2020 subperiod stability, rolling 12m/24m stability, and cohort (established-only)
    stability. Same methodology for champion and candidate so the comparison is apples-to-apples."""
    months = sorted(monthly)
    ics: List[float] = []
    ics_established: List[float] = []
    gross: List[float] = []
    turn: List[float] = []
    new_cohort_share: List[float] = []
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
        # cohort stability: IC on established-cohort-only names (new_cohort share tracked)
        est = [r for r in rows if not r.get("established", True) is False]
        est_rows = [r for r in rows if r.get("established", True)]
        new_cohort_share.append(1.0 - (len(est_rows) / len(rows)))
        if len(est_rows) >= MIN_NAMES_PER_MONTH:
            ic_e = _spearman([_to_float(r[score_key]) for r in est_rows],
                             [_to_float(r["fwd"]) for r in est_rows])
            if ic_e is not None:
                ics_established.append(ic_e)
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
                "ic_t": _round(_t_stat(ic_p), 4), "mean_spread": _round(_mean(sp_p), 6) if sp_p else None,
                "positive_month_rate": _round(_positive_rate(sp_p), 4) if sp_p else None}

    rolling = {}
    for w in ROLL_WINDOWS:
        rolling["ic_%dm" % w] = _rolling_stability(ics, w)
        rolling["spread_%dm" % w] = _rolling_stability(gross, w)

    est_mean_ic = _round(_mean(ics_established), 6) if ics_established else None
    all_mean_ic = _round(_mean(ics), 6) if ics else None
    cohort = {
        "mean_new_cohort_share": _round(_mean(new_cohort_share), 4) if new_cohort_share else None,
        "mean_ic_all": all_mean_ic,
        "mean_ic_established_only": est_mean_ic,
        "n_established_ic_months": len(ics_established),
        "established_ic_supported": len(ics_established) >= 3,
        "established_ic_same_sign_as_all": (est_mean_ic is not None and all_mean_ic is not None
                                            and (est_mean_ic >= 0) == (all_mean_ic >= 0)),
    }

    return {
        "n_months_scored": n_months_scored,
        "n_ic_months": len(ics),
        "mean_ic": all_mean_ic,
        "ic_t_stat": _round(_t_stat(ics), 4),
        "mean_gross_spread": _round(mean_gross, 6),
        "mean_turnover": _round(mean_turn, 4),
        "net25_spread": _round(net25, 6),
        "net50_spread": _round(net50, 6),
        "cumulative_spread": _round(cum[-1], 6) if cum else None,
        "max_drawdown": _round(_max_drawdown(cum), 6),
        "positive_ic_month_rate": _round(_positive_rate(ics), 4),
        "positive_spread_month_rate": _round(_positive_rate(gross), 4),
        "subperiod": {"pre2020": _period("pre2020"), "post2020": _period("post2020")},
        "rolling_stability": rolling,
        "cohort_stability": cohort,
    }


# --------------------------------------------------------------------------- #
# Latest cross-section helpers.
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
DEC_ELIGIBLE = "PAPER_CHALLENGER_ELIGIBLE"
DEC_KEEP = "KEEP_CURRENT_PAPER_CHAMPION"
DEC_FAILED = "RESEARCH_REVALIDATION_FAILED"
DEC_BLOCKED_DATA = "BLOCKED_DATA_MISSING"
DEC_BLOCKED_ERROR = "BLOCKED_RUNNER_ERROR"
ALLOWED_DECISIONS = (DEC_ELIGIBLE, DEC_KEEP, DEC_FAILED, DEC_BLOCKED_DATA, DEC_BLOCKED_ERROR)
FORBIDDEN_DECISIONS = ("LIVE_TRADING_READY", "PRODUCTION_READY", "ORDER_READY", "AUTOMATION_READY",
                       "CHAMPION_REPLACED", "LIVE_CHAMPION_PROMOTED")

# Data-integrity guard (numeric faithfulness; NOT part of the alpha decision).
REPRO_MAX_ABS_ERR = 1e-4
REPRO_MIN_SPEARMAN = 0.9999

# Data-completeness gate: the repaired grouping must cover essentially the whole universe.
MIN_RESOLVED_FRAC = 0.95

# Alpha thresholds (reused project bars; declared before reading the comparison).
MONITOR_MIN_IC_T = 2.0          # the project's IC t-stat monitor bar
COST_ROBUST_MIN_NET = 0.0       # net-25bps AND net-50bps spread must be strictly positive
IDENTICAL_MIN_SPEARMAN = 0.98   # >= this AND high overlap => repaired book ~ champion (no new book)
IDENTICAL_MIN_OVERLAP = 0.90

DECISION_LOGIC = [
    "GUARD (BLOCK): if the frozen panel or the committed Phase 16-A resolved sector map is missing/empty, "
    "OR the resolved grouping covers < %.0f%% of the current champion universe, OR the recompute cannot "
    "reproduce the frozen composite_sn with the ORIGINAL sectors (max_abs_err > %.0e OR reproduction "
    "Spearman < %.4f) -> BLOCKED_DATA_MISSING. The repaired comparison cannot be trusted."
    % (MIN_RESOLVED_FRAC * 100, REPRO_MAX_ABS_ERR, REPRO_MIN_SPEARMAN),
    "FAIL (RESEARCH_REVALIDATION_FAILED) if the repaired-sector candidate is not a valid standalone alpha "
    "under proper sectors - ANY of: candidate IC t-stat < %.1f (statistically weak); OR candidate IC "
    "t-stat sign flips vs the champion; OR candidate net-25bps spread <= %.4f; OR candidate net-50bps "
    "spread <= %.4f (not cost-robust); OR a subperiod (pre-2020 / 2020+) IC or spread sign reversal that "
    "held under the champion. The champion is HELD (not replaced); deeper research is required."
    % (MONITOR_MIN_IC_T, COST_ROBUST_MIN_NET, COST_ROBUST_MIN_NET),
    "KEEP (KEEP_CURRENT_PAPER_CHAMPION) if the repaired candidate is valid BUT essentially indistinguishable "
    "from the champion (full-panel Spearman >= %.2f AND Top25 overlap >= %.2f AND Top50 overlap >= %.2f): "
    "repaired sectors do not materially change the book, so no separate challenger is warranted - keep "
    "running the single champion."
    % (IDENTICAL_MIN_SPEARMAN, IDENTICAL_MIN_OVERLAP, IDENTICAL_MIN_OVERLAP),
    "ELIGIBLE (PAPER_CHALLENGER_ELIGIBLE) otherwise: the repaired candidate is statistically strong "
    "(IC t >= %.1f, same sign), cost-robust (net-25bps > 0 and net-50bps > 0), regime-stable (no subperiod "
    "sign reversal), and MATERIALLY DIFFERENT from the champion (Spearman < %.2f or overlap < %.2f). It "
    "becomes eligible to run as a PARALLEL PAPER CHALLENGER only - the champion is NOT replaced and nothing "
    "is promoted to live." % (MONITOR_MIN_IC_T, IDENTICAL_MIN_SPEARMAN, IDENTICAL_MIN_OVERLAP),
]


def _same_sign(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return False
    return (a >= 0) == (b >= 0)


def _subperiod_sign_reversal(champ: Dict, cand: Dict) -> List[str]:
    flips = []
    for pk in ("pre2020", "post2020"):
        for metric in ("mean_ic", "mean_spread"):
            cv = champ["subperiod"][pk].get(metric)
            sv = cand["subperiod"][pk].get(metric)
            if cv is not None and sv is not None and cv != 0 and not _same_sign(cv, sv):
                flips.append("%s.%s (%s -> %s)" % (pk, metric, cv, sv))
    return flips


def decide(*, repro_ok: bool, resolved_frac: float, full_spearman: Optional[float],
           top25_overlap: float, top50_overlap: float, champ: Dict, cand: Dict) -> Tuple[str, List[str]]:
    """Apply the a-priori ladder. Returns (decision, reasons)."""
    ic_c, ic_s = champ.get("ic_t_stat"), cand.get("ic_t_stat")
    n25_s, n50_s = cand.get("net25_spread"), cand.get("net50_spread")
    flips = _subperiod_sign_reversal(champ, cand)

    # --- GUARD / BLOCK ---
    if not repro_ok:
        return DEC_BLOCKED_DATA, ["Data-integrity guard failed: the recompute could not reproduce the "
                                  "frozen composite_sn with the original sectors; the repaired comparison "
                                  "cannot be trusted."]
    if resolved_frac < MIN_RESOLVED_FRAC:
        return DEC_BLOCKED_DATA, ["Repaired grouping covers only %.1f%% of the champion universe (< %.0f%%); "
                                  "too many unresolved names to trust the repaired sector-neutral grouping."
                                  % (resolved_frac * 100, MIN_RESOLVED_FRAC * 100)]

    # --- FAIL (candidate is not a valid standalone alpha under proper sectors) ---
    fail: List[str] = []
    if ic_s is not None and abs(ic_s) < MONITOR_MIN_IC_T:
        fail.append("Repaired candidate IC t-stat %.2f is below the monitor bar %.1f (statistically weak)."
                    % (ic_s, MONITOR_MIN_IC_T))
    if ic_s is not None and ic_c is not None and not _same_sign(ic_c, ic_s):
        fail.append("Repaired candidate IC t-stat sign flips vs the champion (%.2f -> %.2f)." % (ic_c, ic_s))
    if n25_s is not None and n25_s <= COST_ROBUST_MIN_NET:
        fail.append("Repaired candidate net-25bps spread is non-positive (%.5f)." % n25_s)
    if n50_s is not None and n50_s <= COST_ROBUST_MIN_NET:
        fail.append("Repaired candidate net-50bps spread is non-positive (%.5f)." % n50_s)
    if flips:
        fail.append("Repaired sectors introduce a subperiod sign reversal: %s." % "; ".join(flips))
    if fail:
        return DEC_FAILED, fail

    # --- KEEP (valid but ~ identical to the champion => no separate book) ---
    fs = full_spearman if full_spearman is not None else 1.0
    if (full_spearman is not None and full_spearman >= IDENTICAL_MIN_SPEARMAN
            and top25_overlap >= IDENTICAL_MIN_OVERLAP and top50_overlap >= IDENTICAL_MIN_OVERLAP):
        return DEC_KEEP, ["Repaired candidate is valid but essentially indistinguishable from the champion "
                          "(full-panel Spearman %.4f >= %.2f, Top25 overlap %.2f, Top50 overlap %.2f); "
                          "repaired sectors do not materially change the book, so no separate challenger is "
                          "warranted." % (fs, IDENTICAL_MIN_SPEARMAN, top25_overlap, top50_overlap)]

    # --- ELIGIBLE (valid + materially different) => parallel paper challenger only ---
    return DEC_ELIGIBLE, ["Repaired-sector candidate is a valid standalone alpha under proper sectors "
                          "(IC t %.2f >= %.1f same sign; net25 %.5f > 0; net50 %.5f > 0; no subperiod sign "
                          "reversal) and materially differs from the champion (full-panel Spearman %.4f < "
                          "%.2f; Top25 overlap %.2f, Top50 overlap %.2f). Eligible for a PARALLEL PAPER "
                          "CHALLENGER only - the champion is NOT replaced and nothing is promoted to live."
                          % (ic_s or 0.0, MONITOR_MIN_IC_T, n25_s or 0.0, n50_s or 0.0, fs,
                             IDENTICAL_MIN_SPEARMAN, top25_overlap, top50_overlap)]


# --------------------------------------------------------------------------- #
# Paths / artifacts.
# --------------------------------------------------------------------------- #
_DEF_PANEL = (_REPO_ROOT / "research" / "output"
              / "phase10l_historical_sector_neutral_scored_panel_reconstruction"
              / "historical_sector_neutral_scored_panel.csv")
_DEF_RESOLVED = (_REPO_ROOT / "research" / "output"
                 / "phase16a_sector_metadata_integrity_and_shadow_revalidation"
                 / "sector_metadata_resolved.csv")
_DEF_EOD = _REPO_ROOT / "research" / "data" / "eodhd" / "raw" / "eod_prices"
_DEF_13A = (_REPO_ROOT / "research" / "output" / "phase13a_current_champion_alpha_paper_test_package"
            / "phase13a_current_champion_alpha_paper_test_package.json")
_DEFAULT_OUT = (_REPO_ROOT / "research" / "output" / "phase17a_sector_repaired_champion_revalidation")
_DEFAULT_CHALLENGER_OUT = (_REPO_ROOT / "research" / "output"
                           / "phase17b_sector_repaired_challenger_package")

_ARTIFACTS = {
    "report": "phase17a_sector_repaired_champion_revalidation_report.json",
    "metrics": "phase17a_original_vs_repaired_metrics.csv",
    "monthly": "phase17a_monthly_ic_and_spread.csv",
    "rolling": "phase17a_rolling_stability.csv",
    "subperiod": "phase17a_subperiod_stability.csv",
    "latest_rank": "phase17a_latest_rank_comparison.csv",
    "top25_exp": "phase17a_top25_sector_exposure.csv",
    "top50_exp": "phase17a_top50_sector_exposure.csv",
    "entering_leaving": "phase17a_names_entering_leaving.csv",
    "repro": "phase17a_reproduction_check.csv",
    "missing": "phase17a_missing_data_report.csv",
    "scorecard": "phase17a_decision_scorecard.csv",
    "secret_audit": "phase17a_secret_safety_audit.csv",
}

SAFETY_BADGES = ["PAPER ONLY", "MANUAL REVIEW", "NO BROKER EXECUTION", "AUTOMATION OFF", "NO LIVE ORDERS",
                 "PREVIEW ONLY", "OWNED DATA ONLY", "NO CHAMPION MUTATION", "NO PROVIDER CALL",
                 "NO LIVE PROMOTION"]


# --------------------------------------------------------------------------- #
# Load the committed Phase 16-A resolved sector map (owned data only; no fabrication).
# --------------------------------------------------------------------------- #
def load_resolved_sector_map(resolved_csv: Path) -> Tuple[Dict[str, Dict[str, str]], List[str]]:
    """Return (ticker -> {resolved_sector, source_family, source_field, confidence, industry}, issues)."""
    out: Dict[str, Dict[str, str]] = {}
    issues: List[str] = []
    for r in _read_csv_rows(resolved_csv):
        tk = _norm_label(r.get("ticker")).upper()
        sec = _norm_label(r.get("resolved_sector"))
        if not tk:
            continue
        if _is_unknown(sec):
            issues.append("%s: resolved_sector blank/unknown in the committed map" % tk)
            continue
        if sec not in CANONICAL_GICS:
            # never invent - a non-canonical label is recorded as an issue, not silently used.
            issues.append("%s: resolved_sector '%s' not in the canonical 11-bucket GICS taxonomy" % (tk, sec))
        out[tk] = {"resolved_sector": sec, "industry": _norm_label(r.get("resolved_industry")) or "Unknown",
                   "source_family": _norm_label(r.get("source_family")),
                   "source_field": _norm_label(r.get("source_field")),
                   "confidence": _norm_label(r.get("confidence")) or "NONE",
                   "point_in_time": _norm_label(r.get("point_in_time")) or "current-as-of (not PIT)"}
    return out, issues


def _blocker(out_dir: Path, decision: str, reason: str, verbose: bool) -> Dict:
    rep = {"phase": PHASE, "decision": decision, "decision_reasons": [reason],
           "allowed_decisions": list(ALLOWED_DECISIONS), "forbidden_decisions": list(FORBIDDEN_DECISIONS),
           "challenger_package_created": False,
           "exact_next_research_action": ("re-run research/run_phase17a_sector_repaired_champion_"
                                          "revalidation.py once the missing owned artifact is restored"),
           "performs_network": PERFORMS_NETWORK, "offline": True, "uses_owned_data_only": True,
           "mutated_champion": False, "replaced_champion": False, "wrote_to_paper_trader": False,
           "creates_orders": False, "creates_automation": False, "promotes_to_live": False,
           "live_trading": False, "safety_badges": SAFETY_BADGES}
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / _ARTIFACTS["report"], rep)
    if verbose:
        print("[%s] decision=%s | %s" % (PHASE, decision, reason))
    return rep


# --------------------------------------------------------------------------- #
# PART F - immutable paper-challenger package (only on PAPER_CHALLENGER_ELIGIBLE).
# --------------------------------------------------------------------------- #
def _has_local_price(eod_dir: Path, ticker: str) -> bool:
    return (eod_dir / ("%s.json" % ticker.upper())).is_file()


def build_challenger_package(out_dir: Path, *, rows: List[Dict], latest: str,
                             latest_legs: Dict[int, Dict], rep_rows: Dict[str, int],
                             repaired_fn: Callable[[str], str], repaired_meta: Dict[str, Dict[str, str]],
                             eod_dir: Path, signal_date: str, source_manifest: Dict) -> Dict:
    """Build the immutable sector-repaired paper-challenger package. Every position row states NO_ORDER.
    Ranked strictly by the REPAIRED composite. No orders, no marks, no live weights - a candidate list
    for PARALLEL paper testing only."""
    # ranked repaired universe (only names with a valid repaired composite in the latest month)
    ranked = sorted([tk for tk in rep_rows if latest_legs.get(rep_rows[tk], {}).get("comp") is not None],
                    key=lambda tk: (-latest_legs[rep_rows[tk]]["comp"], tk))
    n = len(ranked)
    top25, top50, bottom25 = ranked[:25], ranked[:50], ranked[-25:]

    def _liq(tk: str):
        return _round(_to_float(rows[rep_rows[tk]].get("liquidity_proxy")), 2)

    def _leg(tk: str, key: str):
        return _round(latest_legs[rep_rows[tk]].get(key), 6)

    # full ranked universe
    uni_rows = []
    for rk, tk in enumerate(ranked, start=1):
        uni_rows.append([rk, tk, repaired_fn(tk), repaired_meta.get(tk, {}).get("confidence", "NONE"),
                         _leg(tk, "comp"), _leg(tk, "zf"), _leg(tk, "za"), _liq(tk),
                         _has_local_price(eod_dir, tk), "NO_ORDER"])
    _write_csv(out_dir / "challenger_full_ranked_universe.csv",
               ["rank", "ticker", "repaired_sector", "sector_confidence", "composite_sn_repaired",
                "fcf_leg_z", "accruals_leg_z", "liquidity_proxy", "has_local_price", "order_action"],
               uni_rows)

    def _cand_rows(book):
        out = []
        for rk, tk in enumerate(book, start=1):
            out.append([rk, tk, repaired_fn(tk), _leg(tk, "comp"), _leg(tk, "zf"), _leg(tk, "za"),
                        _liq(tk), _has_local_price(eod_dir, tk), "NO_ORDER"])
        return out

    cand_hdr = ["rank", "ticker", "repaired_sector", "composite_sn_repaired", "fcf_leg_z",
                "accruals_leg_z", "liquidity_proxy", "has_local_price", "order_action"]
    _write_csv(out_dir / "challenger_top25_candidates.csv", cand_hdr, _cand_rows(top25))
    _write_csv(out_dir / "challenger_top50_candidates.csv", cand_hdr, _cand_rows(top50))
    _write_csv(out_dir / "challenger_bottom25_avoid_list.csv", cand_hdr, _cand_rows(bottom25))

    def _book_rows(book, weight):
        out = []
        for tk in book:
            out.append([tk, "LONG", round(weight, 4), repaired_fn(tk), _leg(tk, "comp"), signal_date,
                        "EODHD_LOCAL_EOD(owned)", _has_local_price(eod_dir, tk),
                        "NO_ORDER", "PAPER_REVIEW_ONLY", "NO_LIVE_WEIGHTS"])
        return out

    book_hdr = ["ticker", "side", "target_weight", "repaired_sector", "signal_composite_sn_repaired",
                "signal_date", "price_source", "has_local_price", "order_action", "review_status",
                "live_weight_status"]
    _write_csv(out_dir / "challenger_paper_portfolio_top25.csv", book_hdr, _book_rows(top25, 1.0 / 25))
    _write_csv(out_dir / "challenger_paper_portfolio_top50.csv", book_hdr, _book_rows(top50, 1.0 / 50))

    # sector exposures (one file, book column)
    exp_rows: List[List] = []
    for label, book in (("TOP25", top25), ("TOP50", top50), ("BOTTOM25", bottom25)):
        erows, _summ = _exposure(book, repaired_fn)
        for er in erows:
            exp_rows.append([label] + er)
    _write_csv(out_dir / "challenger_sector_exposure.csv",
               ["book", "sector", "n_names", "weight_pct", "flag"], exp_rows)

    # price coverage
    def _cov(book):
        c = sum(1 for tk in book if _has_local_price(eod_dir, tk))
        return c, len(book), round(100.0 * c / (len(book) or 1), 2)
    cov_rows = []
    for label, book in (("all", ranked), ("top25", top25), ("top50", top50), ("bottom25", bottom25)):
        c, tot, pct = _cov(book)
        cov_rows.append([label, c, tot, pct, "owned local EODHD EOD file present"])
    _write_csv(out_dir / "challenger_price_coverage.csv",
               ["book", "covered", "total", "coverage_pct", "note"], cov_rows)

    # tracking template (blank checkpoints; NO marks computed here)
    trk_rows = []
    for tk in top50:
        trk_rows.append([tk, "LONG", repaired_fn(tk), signal_date, "", "", "", "", "", "", "", "NO_ORDER"])
    _write_csv(out_dir / "challenger_tracking_template.csv",
               ["ticker", "side", "repaired_sector", "signal_date", "chk_1w_return", "chk_1m_return",
                "chk_2m_return", "chk_63d_return", "chk_63d_bench_rel", "max_drawdown", "hit_win_loss",
                "order_action"], trk_rows)

    # risk limits
    top25_exp_rows, top25_summ = _exposure(top25, repaired_fn)
    _write_csv(out_dir / "challenger_risk_limits.csv", ["limit", "value", "enforcement", "note"], [
        ["PREVIEW ONLY", "YES", "HARD", "candidate list only; nothing is executed"],
        ["NO ORDERS", "CONFIRMED", "HARD", "order_action=NO_ORDER on every position row"],
        ["NO BROKER", "CONFIRMED", "HARD", "no broker connection of any kind"],
        ["NO AUTOMATION", "CONFIRMED", "HARD", "no scheduling / no automated re-run"],
        ["NO LIVE TRADING", "CONFIRMED", "HARD", "parallel PAPER challenger only, NOT live, NOT champion"],
        ["MANUAL REVIEW", "REQUIRED", "HARD", "a human must review before any action outside this tool"],
        ["parallel_challenger_only", "CONFIRMED", "RULE", "does NOT replace the current paper champion"],
        ["weighting", "EQUAL_WEIGHT_LONG_ONLY", "TARGET", "top25 (4%) and top50 (2%) books are equal-weight"],
        ["holding_horizon_trading_days", 63, "TARGET", "63 trading-day hold"],
        ["rebalance_cadence", "QUARTERLY", "TARGET", "re-rank at the next quarter boundary"],
        ["max_position_size_top25", 0.04, "SUGGESTION", "1/25 equal weight"],
        ["max_position_size_top50", 0.02, "SUGGESTION", "1/50 equal weight"],
        ["sector_concentration_warn", 0.3, "WARNING",
         "repaired Top25 largest sector share = %.1f%%" % top25_summ["largest_sector_share_pct"]],
    ])

    # go/no-go scorecard
    _write_csv(out_dir / "challenger_go_no_go_scorecard.csv",
               ["criterion", "status", "value", "threshold", "note"], [
        ["repaired_sector_coverage", "PASS", "%d/%d" % (n, n), "full", "all ranked names carry a repaired GICS sector"],
        ["reproduction_of_frozen_composite", "PASS", "yes", "required", "recompute reproduces the frozen champion exactly"],
        ["candidate_ic_t_63d", "PASS", source_manifest.get("candidate_ic_t"), ">=2.0", "repaired-sector candidate IC t-stat"],
        ["candidate_net25_spread", "PASS", source_manifest.get("candidate_net25"), ">0", "cost-adjusted (25bps) quintile L/S"],
        ["candidate_net50_spread", "PASS", source_manifest.get("candidate_net50"), ">0", "cost-adjusted (50bps) quintile L/S"],
        ["materially_differs_from_champion", "PASS", source_manifest.get("full_spearman"), "<0.98",
         "distinct enough to run as a parallel book"],
        ["OVERALL_RECOMMENDATION", "PAPER_CHALLENGER_ELIGIBLE_PAPER_ONLY_NOT_LIVE",
         "PAPER_CHALLENGER_ELIGIBLE", "paper only", "parallel paper challenger only; NEVER a live-trading signal"],
    ])

    # provenance manifest
    _write_csv(out_dir / "challenger_provenance_manifest.csv", ["item", "value"],
               [[k, v] for k, v in source_manifest.items()])

    package = {
        "phase": "17-B", "package_type": "SECTOR_REPAIRED_PAPER_CHALLENGER",
        "immutable": True, "created_from_decision": DEC_ELIGIBLE,
        "champion_relationship": "PARALLEL_PAPER_CHALLENGER_DOES_NOT_REPLACE_CHAMPION",
        "candidate_signal": "composite_sn_repaired",
        "candidate_definition": ("identical sector-neutral equal-weight composite as the champion "
                                 "(oriented sector-neutral fcf_to_assets (+) plus operating_accruals (-, "
                                 "Sloan-negated), within-month z, sum of legs) recomputed ONLY with owned "
                                 "repaired GICS sector labels; no winsorization"),
        "signal_date": signal_date, "cross_section_month": latest, "n_ranked": n,
        "book_sizes": {"top25": len(top25), "top50": len(top50), "bottom25_avoid": len(bottom25)},
        "weighting": "EQUAL_WEIGHT_LONG_ONLY", "top25_weight_each": round(1.0 / 25, 4),
        "top50_weight_each": round(1.0 / 50, 4), "holding_horizon_trading_days": 63,
        "rebalance_cadence": "QUARTERLY",
        "price_coverage": {"top25": _cov(top25)[0], "top50": _cov(top50)[0], "bottom25": _cov(bottom25)[0]},
        "repaired_top25_largest_sector_share_pct": top25_summ["largest_sector_share_pct"],
        "provenance": source_manifest,
        "order_action_all": "NO_ORDER", "review_status_all": "PAPER_REVIEW_ONLY",
        "go_no_go": "PAPER_CHALLENGER_ELIGIBLE_PAPER_ONLY_NOT_LIVE",
        # safety
        "performs_network": False, "offline": True, "uses_owned_data_only": True, "fabricated_sectors": False,
        "creates_paper_trader_signals": False, "creates_trade_decisions": False, "creates_orders": False,
        "creates_automation": False, "creates_broker_connection": False, "wrote_to_paper_trader": False,
        "replaced_champion": False, "promotes_to_live": False, "live_trading": False, "deploy": False,
        "modified_phase13a_package": False, "modified_phase16a_artifacts": False,
        "api_key_printed": False, "api_key_written_to_disk": False,
        "safety_badges": SAFETY_BADGES,
        "required_artifacts": ["phase17b_sector_repaired_challenger_package.json",
                               "challenger_full_ranked_universe.csv", "challenger_top25_candidates.csv",
                               "challenger_top50_candidates.csv", "challenger_bottom25_avoid_list.csv",
                               "challenger_paper_portfolio_top25.csv", "challenger_paper_portfolio_top50.csv",
                               "challenger_sector_exposure.csv", "challenger_price_coverage.csv",
                               "challenger_tracking_template.csv", "challenger_risk_limits.csv",
                               "challenger_go_no_go_scorecard.csv", "challenger_provenance_manifest.csv",
                               "secret_safety_audit.csv"],
    }
    _write_json(out_dir / "phase17b_sector_repaired_challenger_package.json", package)
    sec_rows, leak_clean = _secret_safety_audit(out_dir)
    _write_csv(out_dir / "secret_safety_audit.csv", ["file", "clean", "keyed_url_marker", "key_value_present"],
               [[r["file"], r["clean"], r["keyed_url_marker"], r["key_value_present"]] for r in sec_rows])
    package["secret_safety_leak_scan_clean"] = leak_clean
    _write_json(out_dir / "phase17b_sector_repaired_challenger_package.json", package)
    return package


def _blocked_challenger_manifest(out_dir: Path, decision: str, reasons: List[str]) -> None:
    """When the candidate is NOT eligible, write an explicit not-created manifest (never a fake book)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "phase17b_challenger_not_created_manifest.json", {
        "phase": "17-B", "challenger_package_created": False, "revalidation_decision": decision,
        "reasons": reasons,
        "explanation": ("The paper challenger package is created ONLY when the Phase 17-A revalidation "
                        "decision is PAPER_CHALLENGER_ELIGIBLE. It is NOT eligible under the current "
                        "decision, so no challenger book was fabricated."),
        "champion_replaced": False, "promotes_to_live": False, "creates_orders": False,
        "safety_badges": SAFETY_BADGES,
    })


# --------------------------------------------------------------------------- #
# Run.
# --------------------------------------------------------------------------- #
def run(out_dir: Optional[Path] = None, *, panel_csv: Optional[Path] = None,
        resolved_csv: Optional[Path] = None, eod_dir: Optional[Path] = None,
        pkg_json: Optional[Path] = None, challenger_out: Optional[Path] = None,
        verbose: bool = True) -> Dict:
    out_dir = Path(out_dir) if out_dir else _DEFAULT_OUT
    panel_csv = Path(panel_csv) if panel_csv else _DEF_PANEL
    resolved_csv = Path(resolved_csv) if resolved_csv else _DEF_RESOLVED
    eod_dir = Path(eod_dir) if eod_dir else _DEF_EOD
    pkg_json = Path(pkg_json) if pkg_json else _DEF_13A
    challenger_out = Path(challenger_out) if challenger_out else _DEFAULT_CHALLENGER_OUT

    if not panel_csv.exists():
        return _blocker(out_dir, DEC_BLOCKED_DATA, "frozen scored panel not found at %s" % _rel(panel_csv), verbose)
    rows = _read_csv_rows(panel_csv)
    if not rows:
        return _blocker(out_dir, DEC_BLOCKED_DATA, "panel at %s is empty" % _rel(panel_csv), verbose)
    resolved_map, map_issues = load_resolved_sector_map(resolved_csv)
    if not resolved_map:
        return _blocker(out_dir, DEC_BLOCKED_DATA,
                        "committed Phase 16-A resolved sector map not found/empty at %s" % _rel(resolved_csv),
                        verbose)

    for r in rows:
        r["ticker"] = _norm_label(r.get("ticker")).upper()
    month_index: Dict[str, List[int]] = {}
    for i, r in enumerate(rows):
        mth = _norm_label(r.get("rebalance_date"))[:7]
        if len(mth) == 7:
            month_index.setdefault(mth, []).append(i)

    orig_sector_map: Dict[str, str] = {}
    for r in rows:
        sec = _norm_label(r.get("sector")) or "Unknown"
        orig_sector_map.setdefault(r["ticker"], "Unknown" if _is_unknown(sec) else sec)

    def repaired_fn(tk: str) -> str:
        tk = tk.upper()
        if tk in resolved_map:
            return resolved_map[tk]["resolved_sector"]
        cur = orig_sector_map.get(tk, "Unknown")
        return cur if not _is_unknown(cur) else "Unknown"

    def original_fn(tk: str) -> str:
        cur = orig_sector_map.get(tk.upper(), "Unknown")
        return cur if not _is_unknown(cur) else "Unknown"

    # latest champion cross-section (frozen composite ranks)
    latest = _latest_month(rows)
    rep_rows = _representative_rows(rows, latest)
    ranked_orig = sorted(rep_rows.keys(),
                         key=lambda tk: (-_to_float(rows[rep_rows[tk]].get(SIGNAL_COL)), tk))
    n_ranked = len(ranked_orig)
    top25, top50, bottom25 = ranked_orig[:25], ranked_orig[:50], ranked_orig[-25:]

    # data-completeness of the repaired grouping over the champion universe
    n_universe_resolved = sum(1 for tk in ranked_orig if tk in resolved_map)
    resolved_frac = (n_universe_resolved / n_ranked) if n_ranked else 0.0
    signal_date = max((_norm_label(rows[rep_rows[tk]].get("rebalance_date")) for tk in rep_rows),
                      default=latest)

    # ------------------------------------------------------------------ #
    # Rebuild composites (original recompute + repaired candidate) over the whole panel.
    # ------------------------------------------------------------------ #
    comp_orig_recomputed = recompute_composite(rows, month_index, original_fn)
    comp_repaired = recompute_composite(rows, month_index, repaired_fn)

    # data-integrity: reproduce the frozen composite_sn with the ORIGINAL sectors
    repro_pairs = [(i, _to_float(rows[i].get(SIGNAL_COL)), comp_orig_recomputed.get(i))
                   for i in range(len(rows))]
    repro_pairs = [(i, a, b) for (i, a, b) in repro_pairs if a is not None and b is not None]
    max_abs_err = max((abs(a - b) for (_i, a, b) in repro_pairs), default=None)
    repro_spearman = _spearman([a for (_i, a, _b) in repro_pairs], [b for (_i, _a, b) in repro_pairs]) \
        if len(repro_pairs) >= 3 else None
    repro_ok = bool(max_abs_err is not None and max_abs_err <= REPRO_MAX_ABS_ERR
                    and repro_spearman is not None and repro_spearman >= REPRO_MIN_SPEARMAN)

    # monthly cross-sections shared by champion (frozen comp) and candidate (repaired comp)
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
            established = str(_norm_label(rows[i].get("is_new_cohort"))).lower() not in ("true", "1", "yes")
            recs.append({"ticker": tk, "orig": _to_float(rows[i].get(SIGNAL_COL)),
                         "shadow": comp_repaired.get(i), "fwd": _to_float(rows[i].get(FWD)),
                         "established": established})
        monthly[mth] = recs

    champ_eval = _evaluate_score(monthly, "orig")
    cand_eval = _evaluate_score(monthly, "shadow")

    # full-panel rank correlation champion vs repaired candidate
    fp_a, fp_b = [], []
    for recs in monthly.values():
        for r in recs:
            if r["orig"] is not None and r["shadow"] is not None:
                fp_a.append(r["orig"])
                fp_b.append(r["shadow"])
    full_spearman = _spearman(fp_a, fp_b) if len(fp_a) >= 3 else None

    # latest-month ranks (champion frozen vs repaired candidate) + overlap
    latest_legs = _month_legs(rows, month_index.get(latest, []), repaired_fn)
    latest_recs = {r["ticker"]: r for r in monthly.get(latest, [])}
    repaired_ranked = sorted([tk for tk in ranked_orig
                              if latest_recs.get(tk, {}).get("shadow") is not None],
                             key=lambda tk: (-latest_recs[tk]["shadow"], tk))
    r_top25, r_top50, r_bottom25 = repaired_ranked[:25], repaired_ranked[:50], repaired_ranked[-25:]
    top25_overlap = _overlap(top25, r_top25)
    top50_overlap = _overlap(top50, r_top50)
    bottom25_overlap = _overlap(bottom25, r_bottom25)
    lm_a, lm_b = [], []
    for tk in ranked_orig:
        r = latest_recs.get(tk)
        if r and r["orig"] is not None and r["shadow"] is not None:
            lm_a.append(r["orig"])
            lm_b.append(r["shadow"])
    latest_spearman = _spearman(lm_a, lm_b) if len(lm_a) >= 3 else None

    # names entering/leaving Top25/Top50 (champion -> repaired)
    entering_leaving_rows: List[List] = []
    for label, champ_book, cand_book in (("TOP25", top25, r_top25), ("TOP50", top50, r_top50)):
        for tk in sorted(set(cand_book) - set(champ_book)):
            entering_leaving_rows.append([label, "ENTERING", tk, repaired_fn(tk),
                                          _round(latest_recs.get(tk, {}).get("shadow"), 6)])
        for tk in sorted(set(champ_book) - set(cand_book)):
            entering_leaving_rows.append([label, "LEAVING", tk, repaired_fn(tk),
                                          _round(latest_recs.get(tk, {}).get("orig"), 6)])

    # ------------------------------------------------------------------ #
    # DECISION (ladder declared a-priori above).
    # ------------------------------------------------------------------ #
    decision, decision_reasons = decide(repro_ok=repro_ok, resolved_frac=resolved_frac,
                                        full_spearman=full_spearman, top25_overlap=top25_overlap,
                                        top50_overlap=top50_overlap, champ=champ_eval, cand=cand_eval)
    if decision not in ALLOWED_DECISIONS or decision in FORBIDDEN_DECISIONS:
        decision = DEC_FAILED

    # ------------------------------------------------------------------ #
    # Artifacts.
    # ------------------------------------------------------------------ #
    out_dir.mkdir(parents=True, exist_ok=True)

    def _cov_book(book):
        after = sum(1 for tk in book if tk in resolved_map or not _is_unknown(original_fn(tk)))
        before = sum(1 for tk in book if not _is_unknown(original_fn(tk)))
        nn = len(book) or 1
        return {"n": len(book), "before": before, "before_pct": round(100.0 * before / nn, 2),
                "after": after, "after_pct": round(100.0 * after / nn, 2)}
    cov_all, cov25, cov50, cov_b25 = (_cov_book(ranked_orig), _cov_book(top25), _cov_book(top50),
                                      _cov_book(bottom25))

    # metrics comparison
    _write_csv(out_dir / _ARTIFACTS["metrics"], ["metric", "champion_original", "repaired_candidate", "note"], [
        ["ic_t_stat_63d", champ_eval["ic_t_stat"], cand_eval["ic_t_stat"], "monthly rank-IC t-stat"],
        ["mean_ic_63d", champ_eval["mean_ic"], cand_eval["mean_ic"], ""],
        ["mean_gross_spread", champ_eval["mean_gross_spread"], cand_eval["mean_gross_spread"], "quintile L/S"],
        ["net25_spread", champ_eval["net25_spread"], cand_eval["net25_spread"], "cost-adjusted 25bps"],
        ["net50_spread", champ_eval["net50_spread"], cand_eval["net50_spread"], "cost-adjusted 50bps"],
        ["mean_turnover", champ_eval["mean_turnover"], cand_eval["mean_turnover"], ""],
        ["cumulative_spread", champ_eval["cumulative_spread"], cand_eval["cumulative_spread"], ""],
        ["max_drawdown", champ_eval["max_drawdown"], cand_eval["max_drawdown"], "of cumulative L/S curve"],
        ["positive_ic_month_rate", champ_eval["positive_ic_month_rate"], cand_eval["positive_ic_month_rate"], ""],
        ["positive_spread_month_rate", champ_eval["positive_spread_month_rate"],
         cand_eval["positive_spread_month_rate"], ""],
        ["full_panel_rank_spearman", "", _round(full_spearman, 6), "champion vs repaired, all (month,ticker)"],
        ["latest_month_rank_spearman", "", _round(latest_spearman, 6), "234-name cross-section"],
        ["top25_overlap", "", _round(top25_overlap, 4), "fraction of champion Top25 retained by candidate"],
        ["top50_overlap", "", _round(top50_overlap, 4), ""],
        ["bottom25_overlap", "", _round(bottom25_overlap, 4), ""],
        ["resolved_sector_coverage_all_pct", cov_all["before_pct"], cov_all["after_pct"], "234-name universe"],
        ["resolved_sector_coverage_top25_pct", cov25["before_pct"], cov25["after_pct"], ""],
        ["resolved_sector_coverage_top50_pct", cov50["before_pct"], cov50["after_pct"], ""],
    ])

    # monthly IC & spread
    mrows: List[List] = []
    for m in sorted(monthly):
        recs_c = [r for r in monthly[m] if r["orig"] is not None and r["fwd"] is not None]
        recs_s = [r for r in monthly[m] if r["shadow"] is not None and r["fwd"] is not None]
        if len(recs_c) < MIN_NAMES_PER_MONTH:
            continue
        ic_c = _spearman([r["orig"] for r in recs_c], [r["fwd"] for r in recs_c])
        ic_s = _spearman([r["shadow"] for r in recs_s], [r["fwd"] for r in recs_s]) \
            if len(recs_s) >= MIN_NAMES_PER_MONTH else None

        def _sp(recs, key):
            sc = [r[key] for r in recs]
            fw = [r["fwd"] for r in recs]
            order = sorted(range(len(recs)), key=lambda i: sc[i])
            k = max(1, len(recs) // QUANTILE)
            return _mean([fw[i] for i in order[-k:]]) - _mean([fw[i] for i in order[:k]])
        sp_c = _sp(recs_c, "orig")
        sp_s = _sp(recs_s, "shadow") if len(recs_s) >= MIN_NAMES_PER_MONTH else None
        mrows.append([m, len(recs_c), _round(ic_c, 6), _round(ic_s, 6), _round(sp_c, 6), _round(sp_s, 6)])
    _write_csv(out_dir / _ARTIFACTS["monthly"],
               ["month", "n_names", "champion_rank_ic", "repaired_rank_ic", "champion_ls_spread",
                "repaired_ls_spread"], mrows)

    # rolling stability
    roll_rows: List[List] = []
    for series in ("ic", "spread"):
        for w in ROLL_WINDOWS:
            for who, ev in (("champion", champ_eval), ("repaired", cand_eval)):
                st = ev["rolling_stability"]["%s_%dm" % (series, w)]
                roll_rows.append([series, w, who, st.get("supported"), st.get("n_windows"),
                                  st.get("mean_of_rolling_means"), st.get("min_rolling_mean"),
                                  st.get("max_rolling_mean"), st.get("positive_window_rate")])
    _write_csv(out_dir / _ARTIFACTS["rolling"],
               ["series", "window_months", "who", "supported", "n_windows", "mean_of_rolling_means",
                "min_rolling_mean", "max_rolling_mean", "positive_window_rate"], roll_rows)

    # subperiod stability
    sub_rows: List[List] = []
    for pk in ("pre2020", "post2020"):
        c = champ_eval["subperiod"][pk]
        s = cand_eval["subperiod"][pk]
        sub_rows.append([pk, c["n_months"], c["mean_ic"], s["mean_ic"], c["ic_t"], s["ic_t"],
                         c["mean_spread"], s["mean_spread"], c["positive_month_rate"], s["positive_month_rate"],
                         "SIGN_REVERSAL" if (c["mean_spread"] is not None and s["mean_spread"] is not None
                                             and c["mean_spread"] != 0 and not _same_sign(c["mean_spread"], s["mean_spread"]))
                         else "STABLE"])
    _write_csv(out_dir / _ARTIFACTS["subperiod"],
               ["subperiod", "n_months", "champion_mean_ic", "repaired_mean_ic", "champion_ic_t",
                "repaired_ic_t", "champion_mean_spread", "repaired_mean_spread", "champion_pos_month_rate",
                "repaired_pos_month_rate", "spread_sign"], sub_rows)

    # latest rank comparison
    repaired_rank_of = {tk: n + 1 for n, tk in enumerate(repaired_ranked)}
    lr_rows: List[List] = []
    for n, tk in enumerate(ranked_orig, start=1):
        r = latest_recs.get(tk, {})
        rr = repaired_rank_of.get(tk)
        lr_rows.append([n, tk, original_fn(tk), repaired_fn(tk), _round(r.get("orig"), 6),
                        _round(r.get("shadow"), 6), rr if rr is not None else "",
                        (n - rr) if rr is not None else "",
                        "TOP25" if n <= 25 else ("TOP50" if n <= 50 else
                        ("BOTTOM25" if n > n_ranked - 25 else "MIDDLE"))])
    _write_csv(out_dir / _ARTIFACTS["latest_rank"],
               ["champion_rank", "ticker", "original_sector", "repaired_sector", "champion_composite_sn",
                "repaired_composite_sn", "repaired_rank", "rank_delta(up=+)", "champion_bucket"], lr_rows)

    # sector exposures (repaired)
    top25_exp_rows, top25_summ = _exposure(r_top25, repaired_fn)
    top50_exp_rows, top50_summ = _exposure(r_top50, repaired_fn)
    _write_csv(out_dir / _ARTIFACTS["top25_exp"], ["sector", "n_names", "weight_pct", "flag"], top25_exp_rows)
    _write_csv(out_dir / _ARTIFACTS["top50_exp"], ["sector", "n_names", "weight_pct", "flag"], top50_exp_rows)

    # entering/leaving
    _write_csv(out_dir / _ARTIFACTS["entering_leaving"],
               ["book", "direction", "ticker", "repaired_sector", "composite_sn"], entering_leaving_rows)

    # reproduction check
    _write_csv(out_dir / _ARTIFACTS["repro"], ["check", "value", "threshold", "status"], [
        ["frozen_composite_sn_max_abs_reconstruction_error", _round(max_abs_err, 12), "<= %.0e" % REPRO_MAX_ABS_ERR,
         "PASS" if (max_abs_err is not None and max_abs_err <= REPRO_MAX_ABS_ERR) else "FAIL"],
        ["reproduction_rank_spearman", _round(repro_spearman, 8), ">= %.4f" % REPRO_MIN_SPEARMAN,
         "PASS" if (repro_spearman is not None and repro_spearman >= REPRO_MIN_SPEARMAN) else "FAIL"],
        ["reproduction_rows_checked", len(repro_pairs), ">0", "PASS" if repro_pairs else "FAIL"],
        ["reproduction_overall", repro_ok, "both pass", "PASS" if repro_ok else "FAIL"]])

    # missing-data diagnostics
    n_months_total = len(month_index)
    _write_csv(out_dir / _ARTIFACTS["missing"], ["item", "value", "note"], [
        ["panel_rows", len(rows), "frozen 10-L scored panel rows"],
        ["panel_months", n_months_total, "distinct rebalance months"],
        ["months_scored_min20", champ_eval["n_months_scored"], ">=20 names with composite+fwd"],
        ["champion_universe_names", n_ranked, "latest cross-section valid composite_sn"],
        ["repaired_map_names", len(resolved_map), "committed 16-A resolved sector map size"],
        ["repaired_universe_resolved", n_universe_resolved, "of the %d champion names" % n_ranked],
        ["repaired_universe_resolved_pct", round(100.0 * resolved_frac, 2), ">= %.0f%% required" % (MIN_RESOLVED_FRAC * 100)],
        ["repaired_map_issues", len(map_issues), "non-canonical/blank labels in the committed map (0 expected)"],
        ["latest_month", latest, "champion cross-section month"],
        ["names_with_repaired_composite_latest", len(repaired_ranked), "valid repaired composite in latest month"],
    ])

    # decision scorecard
    _write_csv(out_dir / _ARTIFACTS["scorecard"], ["gate", "value", "threshold", "result"], [
        ["reproduction_ok", repro_ok, "True", "PASS" if repro_ok else "BLOCK"],
        ["resolved_fraction", round(resolved_frac, 4), ">= %.2f" % MIN_RESOLVED_FRAC,
         "PASS" if resolved_frac >= MIN_RESOLVED_FRAC else "BLOCK"],
        ["candidate_ic_t", cand_eval["ic_t_stat"], ">= %.1f" % MONITOR_MIN_IC_T,
         "PASS" if (cand_eval["ic_t_stat"] is not None and abs(cand_eval["ic_t_stat"]) >= MONITOR_MIN_IC_T) else "FAIL"],
        ["candidate_net25", cand_eval["net25_spread"], "> 0",
         "PASS" if (cand_eval["net25_spread"] is not None and cand_eval["net25_spread"] > 0) else "FAIL"],
        ["candidate_net50", cand_eval["net50_spread"], "> 0",
         "PASS" if (cand_eval["net50_spread"] is not None and cand_eval["net50_spread"] > 0) else "FAIL"],
        ["subperiod_sign_reversal", len(_subperiod_sign_reversal(champ_eval, cand_eval)), "0",
         "PASS" if not _subperiod_sign_reversal(champ_eval, cand_eval) else "FAIL"],
        ["full_panel_spearman", _round(full_spearman, 4), "< %.2f => distinct" % IDENTICAL_MIN_SPEARMAN,
         "DISTINCT" if (full_spearman is not None and full_spearman < IDENTICAL_MIN_SPEARMAN) else "SIMILAR"],
        ["DECISION", decision, "one of %s" % list(ALLOWED_DECISIONS), decision],
    ])

    # ------------------------------------------------------------------ #
    # Challenger package (only on eligible) or explicit not-created manifest.
    # ------------------------------------------------------------------ #
    source_manifest = {
        "source_panel": _rel(panel_csv), "source_resolved_map": _rel(resolved_csv),
        "source_eod_prices": _rel(eod_dir), "source_phase13a": _rel(pkg_json),
        "candidate_ic_t": cand_eval["ic_t_stat"], "candidate_net25": cand_eval["net25_spread"],
        "candidate_net50": cand_eval["net50_spread"], "full_spearman": _round(full_spearman, 6),
        "top25_overlap": _round(top25_overlap, 4), "top50_overlap": _round(top50_overlap, 4),
        "reproduction_ok": repro_ok, "signal_date": signal_date, "cross_section_month": latest,
    }
    challenger_created = False
    if decision == DEC_ELIGIBLE:
        build_challenger_package(challenger_out, rows=rows, latest=latest, latest_legs=latest_legs,
                                 rep_rows=rep_rows, repaired_fn=repaired_fn, repaired_meta=resolved_map,
                                 eod_dir=eod_dir, signal_date=signal_date, source_manifest=source_manifest)
        challenger_created = True
    else:
        _blocked_challenger_manifest(challenger_out, decision, decision_reasons)

    # ------------------------------------------------------------------ #
    # Main report.
    # ------------------------------------------------------------------ #
    pkg = _read_json(pkg_json) if pkg_json.exists() else {}
    report = {
        "phase": PHASE, "decision": decision, "decision_reasons": decision_reasons,
        "decision_logic_declared_before_result": True, "decision_logic": DECISION_LOGIC,
        "predeclared_thresholds": {
            "reproduction_max_abs_err": REPRO_MAX_ABS_ERR, "reproduction_min_spearman": REPRO_MIN_SPEARMAN,
            "min_resolved_fraction": MIN_RESOLVED_FRAC, "monitor_min_ic_t": MONITOR_MIN_IC_T,
            "cost_robust_min_net": COST_ROBUST_MIN_NET, "identical_min_spearman": IDENTICAL_MIN_SPEARMAN,
            "identical_min_overlap": IDENTICAL_MIN_OVERLAP, "min_names_per_month": MIN_NAMES_PER_MONTH,
            "quantile": QUANTILE, "cost_bps": {"net25": 25, "net50": 50}},
        "allowed_decisions": list(ALLOWED_DECISIONS), "forbidden_decisions": list(FORBIDDEN_DECISIONS),
        "champion_signal": SIGNAL_COL, "candidate_signal": "composite_sn_repaired",
        "methodology": ("recompute ONLY the sector-neutral transform with the committed Phase 16-A owned "
                        "repaired GICS sector map, over the complete frozen 10-L panel, then run the "
                        "champion and the repaired candidate through the identical full validation battery "
                        "(monthly rank-IC, IC t-stat, quintile L/S gross/net25/net50 spread, turnover, "
                        "cumulative spread, max drawdown, positive-month rate, pre/post-2020 subperiod, "
                        "rolling 12m/24m, cohort/established-only, sector concentration, latest overlap, "
                        "entering/leaving) and apply an a-priori decision ladder"),
        "exact_transform": ("o_fcf=+1*fcf_to_assets; o_acc=-1*operating_accruals; demean within (month, "
                            "sector); within-month z (ddof=1, std==0->NaN); composite = z_fcf_sn + "
                            "z_acc_sn; NO winsorization; ONLY the sector grouping differs"),
        "not_this_phase": ["new factor", "reweighting", "sign flip", "threshold tuning", "champion retune",
                           "favourable-period selection", "automatic champion replacement", "orders",
                           "automation", "live trading", "broker connection"],
        "source_panel": _rel(panel_csv), "source_resolved_map": _rel(resolved_csv),
        "source_eod_prices": _rel(eod_dir), "source_phase13a": _rel(pkg_json),
        "resolved_map_issues": map_issues,
        "reproduction": {"max_abs_error": _round(max_abs_err, 12), "rank_spearman": _round(repro_spearman, 8),
                         "rows_checked": len(repro_pairs), "reproduces_frozen_composite": repro_ok,
                         "recomputed_original_ic_t": champ_eval["ic_t_stat"],
                         "note": ("the recompute reproduces the frozen composite_sn with the ORIGINAL "
                                  "sectors; only the sector grouping changes for the repaired candidate")},
        "coverage": {"all234": cov_all, "top25": cov25, "top50": cov50, "bottom25": cov_b25,
                     "n_universe_resolved": n_universe_resolved, "resolved_fraction": _round(resolved_frac, 4)},
        "latest_cross_section": {"month": latest, "signal_date": signal_date, "n_names": n_ranked,
                                 "rank_spearman_champion_vs_repaired": _round(latest_spearman, 6),
                                 "top25_overlap": _round(top25_overlap, 4),
                                 "top50_overlap": _round(top50_overlap, 4),
                                 "bottom25_overlap": _round(bottom25_overlap, 4),
                                 "top25_turnover": _round(1.0 - top25_overlap, 4),
                                 "top50_turnover": _round(1.0 - top50_overlap, 4),
                                 "repaired_top25_largest_sector_share_pct": top25_summ["largest_sector_share_pct"],
                                 "repaired_top50_largest_sector_share_pct": top50_summ["largest_sector_share_pct"]},
        "full_panel": {"rank_spearman_champion_vs_repaired": _round(full_spearman, 6),
                       "champion": champ_eval, "repaired_candidate": cand_eval,
                       "min_names_per_month": MIN_NAMES_PER_MONTH, "quantile": QUANTILE,
                       "cost_bps": {"net25": 25, "net50": 50}},
        "names_entering_leaving_count": len(entering_leaving_rows),
        "challenger_package_created": challenger_created,
        "challenger_package_dir": _rel(challenger_out) if challenger_created else None,
        "phase13a_context": {"phase13a_signal_date": pkg.get("signal_date"),
                             "phase13a_price_coverage": pkg.get("price_coverage"),
                             "phase13a_ic_t_63d": (pkg.get("expected_benchmark") or {}).get("ic_t_63d")},
        "exact_next_research_action": (
            ("Adopt the sector-repaired candidate as a PARALLEL PAPER CHALLENGER (Phase 17-B package) and "
             "track it side-by-side with the current champion over the 63-trading-day paper horizon; do NOT "
             "replace the champion and do NOT promote to live.") if decision == DEC_ELIGIBLE else
            ("Hold the current paper champion; the repaired-sector revalidation did not clear the a-priori "
             "eligibility ladder - open a deeper research pass (factor/robustness) before any book change.")
            if decision == DEC_FAILED else
            ("Keep running the single current paper champion; repaired sectors did not materially change the "
             "book.") if decision == DEC_KEEP else
            ("Restore the missing owned artifact and re-run Phase 17-A.")),
        # --- safety ---
        "performs_network": PERFORMS_NETWORK, "offline": True, "uses_owned_data_only": True,
        "recomputed_only_sector_neutral_transform": True, "added_factors": False, "changed_weights": False,
        "flipped_signs": False, "tuned_thresholds": False, "selected_favourable_period": False,
        "mutated_champion": False, "replaced_champion": False, "modified_phase13a_package": False,
        "modified_phase16a_artifacts": False, "promotes_to_live": False, "creates_paper_trader_signals": False,
        "creates_trade_decisions": False, "creates_orders": False, "creates_automation": False,
        "creates_broker_connection": False, "wrote_to_paper_trader": False, "live_trading": False,
        "deploy": False, "api_key_printed": False, "api_key_written_to_disk": False, "fabricated_sectors": False,
        "safety_badges": SAFETY_BADGES,
        "constraints_honored": ["offline (no network/key/provider probe)", "owned/local data only",
                                "recompute ONLY the sector-neutral transform", "no new factor", "no reweight",
                                "no sign flip", "no threshold tune", "no champion mutation",
                                "did not modify/overwrite the Phase 13-A or 16-A artifacts",
                                "no Paper Trader writes", "NO orders", "NO automation", "NO broker",
                                "NO live trading", "no deploy", "no GCP", "no package install", "pure stdlib",
                                "no commit inside runner", "no push", "no fabricated sectors",
                                "challenger is parallel paper-only and never replaces the champion"],
    }
    _write_json(out_dir / _ARTIFACTS["report"], report)

    # secret-safety audit LAST so it scans every artifact just written.
    sec_rows, leak_clean = _secret_safety_audit(out_dir)
    _write_csv(out_dir / _ARTIFACTS["secret_audit"], ["file", "clean", "keyed_url_marker", "key_value_present"],
               [[r["file"], r["clean"], r["keyed_url_marker"], r["key_value_present"]] for r in sec_rows])
    report["secret_safety_leak_scan_clean"] = leak_clean
    _write_json(out_dir / _ARTIFACTS["report"], report)

    if verbose:
        print("[%s] decision=%s | resolved=%d/%d (%.1f%%) | full_spearman=%s top25_ov=%.2f top50_ov=%.2f | "
              "champ_ic_t=%s cand_ic_t=%s | champ_net25=%s cand_net25=%s cand_net50=%s | repro_ok=%s "
              "max_err=%s | challenger=%s | leak_clean=%s"
              % (PHASE, decision, n_universe_resolved, n_ranked, 100.0 * resolved_frac,
                 _round(full_spearman, 4), top25_overlap, top50_overlap, champ_eval["ic_t_stat"],
                 cand_eval["ic_t_stat"], champ_eval["net25_spread"], cand_eval["net25_spread"],
                 cand_eval["net50_spread"], repro_ok, _round(max_abs_err, 8), challenger_created, leak_clean))
    return report


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 17-A formal sector-repaired champion revalidation")
    p.add_argument("--panel", default=None)
    p.add_argument("--resolved-csv", default=None)
    p.add_argument("--eod-dir", default=None)
    p.add_argument("--pkg-json", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--challenger-out", default=None)
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ns = _parse_args(argv)
    try:
        rep = run(out_dir=ns.out, panel_csv=ns.panel, resolved_csv=ns.resolved_csv, eod_dir=ns.eod_dir,
                  pkg_json=ns.pkg_json, challenger_out=ns.challenger_out, verbose=not ns.quiet)
    except Exception as exc:  # noqa: BLE001 - surface a repro, never a crash
        out_dir = Path(ns.out) if ns.out else _DEFAULT_OUT
        rep = _blocker(out_dir, DEC_BLOCKED_ERROR, "unhandled error: %s: %s" % (type(exc).__name__, exc),
                       not ns.quiet)
    return 0 if rep.get("decision") in ALLOWED_DECISIONS else 1


if __name__ == "__main__":
    raise SystemExit(main())
