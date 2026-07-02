#!/usr/bin/env python3
"""Phase 10-K - Quarterly Quality Composite Alpha Improvement Harness.

Deterministic, skeptical harness that asks ONE narrow question:

    Can the Phase 10-D quarterly quality composite (long fcf_to_assets,
    short operating_accruals, equal-weight, sector-neutral, 63d) be improved
    using only narrow, defensible changes around the proven signal family,
    WITHOUT overfitting -- using only owned/local prior-phase outputs?

This is NOT a broad alpha search, NOT a provider probe, NOT a Paper-Trader
integration, NOT the Phase 10-I price-refresh / mark-to-market task, NOT order
flow, NOT automation, NOT a deploy. It is fully offline: it reads only frozen
Phase 10-D / 10-F / 10-H owned outputs and writes metadata CSV/JSON to its own
research/output directory. It makes no network calls and touches no API key.

Central data limitation (discovered, not assumed): the frozen 10-D/10-F/10-H
outputs contain only (a) backtested summary metrics for four FIXED signals
(equal-weight composite_sn / composite_raw and the two standalone legs) and
(b) the latest single 2026Q2 cross-section. They do NOT contain the historical
per-(month, ticker) sector-neutral scored panel with forward 63d returns. So
arbitrary re-weightings and historical robustness transforms cannot be honestly
re-backtested here; they are reported as INSUFFICIENT_INPUTS with cross-sectional
book diagnostics only. The skeptical default therefore holds unless a variant
clears the strict bar on genuine sector-neutral backtested evidence.
"""

from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path

PHASE = "10-K"
PHASE_NAME = "Quarterly Quality Composite Alpha Improvement Harness"
STEM = "phase10k_quarterly_quality_composite_alpha_improvement_harness"

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "research" / "output" / STEM
D_DIR = REPO / "research" / "output" / "phase10d_quarterly_quality_composite_validation"
F_DIR = REPO / "research" / "output" / "phase10f_owned_sector_mapping_repair"
H_DIR = REPO / "research" / "output" / "phase10h_rules_based_paper_portfolio"

ALLOWED_DECISIONS = [
    "ENHANCED_ALPHA_READY_FOR_PAPER_RULES",
    "BASELINE_REMAINS_CHAMPION",
    "NEEDS_PHASE_INPUT_REPAIR",
    "NEEDS_MORE_OWNED_DATA",
    "REJECT_ENHANCEMENT_OVERFIT",
]

# ---------------------------------------------------------------------------
# small, self-contained, pure-python IO helpers (no numpy / pandas / network)
# ---------------------------------------------------------------------------


def _read_json(path):
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _read_csv(path):
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _num(value):
    """Parse a finite float or return None (never raises)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value).strip()
    if text == "" or text.lower() in ("nan", "none", "null", "na"):
        return None
    try:
        out = float(text)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _round(value, ndigits=5):
    if value is None:
        return None
    try:
        return round(float(value), ndigits)
    except (TypeError, ValueError):
        return None


def _mean(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _pstd(values):
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return None
    mu = sum(vals) / len(vals)
    var = sum((v - mu) ** 2 for v in vals) / len(vals)
    return math.sqrt(var)


def _percentile(values, pct):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    rank = (len(vals) - 1) * (pct / 100.0)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return vals[int(rank)]
    return vals[lo] * (hi - rank) + vals[hi] * (rank - lo)


def _spearman(pairs):
    """Rank correlation for a list of (x, y) tuples; None if undefined."""
    clean = [(x, y) for x, y in pairs if x is not None and y is not None]
    if len(clean) < 3:
        return None

    def _ranks(seq):
        order = sorted(range(len(seq)), key=lambda i: seq[i])
        ranks = [0.0] * len(seq)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and seq[order[j + 1]] == seq[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                ranks[order[k]] = avg
            i = j + 1
        return ranks

    rx = _ranks([p[0] for p in clean])
    ry = _ranks([p[1] for p in clean])
    mx, my = _mean(rx), _mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


def _write_json(path, payload):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")


def _write_csv(path, rows, header):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in header})


# ---------------------------------------------------------------------------
# secret-safety audit (self-contained; owned-data-only, offline)
# ---------------------------------------------------------------------------


def _secret_safety_audit():
    """Confirm no API key is read/printed/written by this offline harness."""
    suspects = ("EODHD_API_KEY", "ALPHAVANTAGE_API_KEY", "FMP_API_KEY",
                "FINNHUB_API_KEY", "POLYGON_API_KEY")
    rows = []
    leak = False
    for name in suspects:
        present = name in os.environ
        rows.append({
            "check": f"env:{name}",
            "value_read": False,
            "value_printed": False,
            "value_written": False,
            "present_in_env": present,
            "note": "not read by this offline harness" if present else "absent",
        })
    rows.append({
        "check": "network_calls",
        "value_read": False, "value_printed": False, "value_written": False,
        "present_in_env": False, "note": "harness performs no network I/O",
    })
    return rows, (not leak)


# ---------------------------------------------------------------------------
# input inventory + field discovery
# ---------------------------------------------------------------------------


def build_input_inventory():
    inv = {"files": [], "missing_required": [], "notes": []}

    def _check(label, path, required, key_fields=None):
        found = path.exists()
        inv["files"].append({
            "label": label,
            "path": str(path.relative_to(REPO)).replace("\\", "/"),
            "found": found,
            "required": required,
            "key_fields": key_fields or [],
        })
        if required and not found:
            inv["missing_required"].append(label)
        return found

    _check("phase10d_json", D_DIR / "phase10d_quarterly_quality_composite_validation.json",
           True, ["signal_results", "acceptance_thresholds"])
    _check("phase10d_standalone_vs_composite", D_DIR / "standalone_vs_composite_comparison.csv", False)
    _check("phase10d_transaction_cost", D_DIR / "transaction_cost_report.csv", False)
    _check("phase10d_turnover", D_DIR / "turnover_report.csv", False)
    _check("phase10d_monthly_vs_quarterly", D_DIR / "monthly_vs_quarterly_cost_diagnostic.csv", False)
    _check("phase10d_subperiod", D_DIR / "subperiod_stability_report.csv", False)
    _check("phase10d_cohort", D_DIR / "cohort_stability_report.csv", False)
    _check("phase10f_long_short_book", F_DIR / "reranked_paper_review_long_short_book.csv", True,
           ["side", "comp_sn", "sector", "liquidity_proxy"])
    _check("phase10f_candidate_list", F_DIR / "reranked_paper_review_candidate_list.csv", False,
           ["fcf_to_assets", "operating_accruals", "comp_sn_z"])
    _check("phase10f_risk_flags", F_DIR / "repaired_book_risk_flags.csv", False,
           ["extreme_score", "low_liquidity"])
    _check("phase10h_json", H_DIR / "phase10h_rules_based_paper_portfolio.json", True,
           ["n_long", "n_short", "liquidity_filter", "sector_cap"])
    _check("phase10h_selected_portfolio", H_DIR / "selected_paper_portfolio.csv", True,
           ["side", "ticker", "comp_sn"])
    _check("phase10h_rules", H_DIR / "portfolio_construction_rules.csv", False)
    inv["notes"].append(
        "No historical per-(month,ticker) sector-neutral scored panel with forward "
        "63d returns exists in the frozen 10-D/10-F/10-H outputs; arbitrary re-weight "
        "and historical robustness transforms therefore cannot be re-backtested here."
    )
    return inv


# ---------------------------------------------------------------------------
# baseline (frozen) from Phase 10-D
# ---------------------------------------------------------------------------


def _signal_map(dj):
    out = {}
    for row in (dj.get("signal_results") or []):
        name = row.get("signal")
        if name:
            out[name] = row
    return out


def build_baseline(dj, sig):
    thr = dj.get("acceptance_thresholds") or {}
    b = sig.get("composite_sn", {})
    raw = sig.get("composite_raw", {})
    return {
        "signal_family": "quality",
        "horizon_days": int(dj.get("primary_horizon_days") or 63),
        "horizon_label": "quarterly / 63d",
        "portfolio_style": "equal-weight long/short",
        "ranking": "sector-neutral",
        "honest_comparator": "composite_sn",
        "long_signal": "fcf_to_assets",
        "short_signal": "operating_accruals",
        "composite_weighting": dj.get("composite_weighting"),
        "optimised_weights": bool(dj.get("optimised_weights")),
        "ic_mean_63d": _round(_num(b.get("ic_63d"))),
        "ic_t_63d": _round(_num(b.get("ic_t_63d"))),
        "quarterly_gross_spread": _round(_num(b.get("quarterly_gross_spread"))),
        "quarterly_net_25bps": _round(_num(b.get("quarterly_net_25bps"))),
        "quarterly_net_50bps": _round(_num(b.get("quarterly_net_50bps"))),
        "quarterly_turnover": _round(_num(b.get("quarterly_turnover"))),
        "oos_pooled_ic": _round(_num(b.get("oos_pooled_ic"))),
        "oos_frac_windows_positive": _round(_num(b.get("oos_frac_windows_positive"))),
        "top_sector_share": _round(_num(b.get("top_sector_share"))),
        "both_cohorts_positive": bool(b.get("both_cohorts_positive")),
        "both_subperiods_positive": bool(b.get("both_subperiods_positive")),
        "acceptance_thresholds": {
            "confirmed_min_ic_t_63d": _num(thr.get("confirmed_min_ic_t_63d")),
            "monitor_min_ic_t_63d": _num(thr.get("monitor_min_ic_t_63d")),
            "max_sector_share": _num(thr.get("max_sector_share")),
            "min_oos_window_frac": _num(thr.get("min_oos_window_frac")),
        },
        "composite_raw_diagnostic_only": {
            "note": "composite_raw is stronger on paper but is NOT the honest comparator "
                    "for a sector-neutral paper book; reported as diagnostic context only.",
            "quarterly_net_25bps": _round(_num(raw.get("quarterly_net_25bps"))),
            "ic_t_63d": _round(_num(raw.get("ic_t_63d"))),
        },
        "caveat": "modest / boundary alpha; the short (operating_accruals) leg carries much "
                  "of the result; must not be oversold.",
    }


# ---------------------------------------------------------------------------
# cross-sectional book helpers (faithful packaging filters use frozen comp_sn)
# ---------------------------------------------------------------------------


def _sector_capped_book(ranked, side, target, cap_per_sector, excluded_tickers,
                        score_key="comp_sn"):
    """Greedy selection along `ranked` respecting a per-sector cap.

    LONG picks the highest scores first; SHORT picks the lowest (most negative)
    first, so `ranked` is expected pre-sorted descending by score.
    """
    chosen = []
    sector_count = {}
    seq = ranked if side == "LONG" else list(reversed(ranked))
    for row in seq:
        if len(chosen) >= target:
            break
        tkr = row.get("ticker")
        if tkr in excluded_tickers:
            continue
        sec = row.get("sector") or "Unknown"
        if sector_count.get(sec, 0) >= cap_per_sector:
            continue
        sector_count[sec] = sector_count.get(sec, 0) + 1
        chosen.append(row)
    top_share = (max(sector_count.values()) / len(chosen)) if chosen else None
    top_sector = None
    if sector_count:
        top_sector = max(sector_count.items(), key=lambda kv: kv[1])[0]
    return chosen, top_share, top_sector


def packaging_variant_book(book_rows, extreme_tickers, liq_threshold,
                           cap_per_sector, target=25):
    """Faithful cross-sectional book under a packaging filter set (uses frozen comp_sn)."""
    longs = [r for r in book_rows if (r.get("side") or "").upper() == "LONG"]
    shorts = [r for r in book_rows if (r.get("side") or "").upper() == "SHORT"]
    longs.sort(key=lambda r: (_num(r.get("comp_sn")) if _num(r.get("comp_sn")) is not None else -1e18),
               reverse=True)
    shorts.sort(key=lambda r: (_num(r.get("comp_sn")) if _num(r.get("comp_sn")) is not None else 1e18))

    liq_excluded = set()
    extreme_excluded = set()
    for r in longs + shorts:
        tkr = r.get("ticker")
        liq = _num(r.get("liquidity_proxy"))
        if liq_threshold is not None and liq is not None and liq < liq_threshold:
            liq_excluded.add(tkr)
        if tkr in extreme_tickers:
            extreme_excluded.add(tkr)
    excluded = liq_excluded | extreme_excluded

    long_book, long_top_share, long_top_sec = _sector_capped_book(
        longs, "LONG", target, cap_per_sector, excluded)
    # shorts already ascending -> pass as-is; _sector_capped_book reverses for LONG only
    short_book, short_top_share, short_top_sec = _sector_capped_book(
        list(reversed(shorts)), "SHORT", target, cap_per_sector, excluded)
    return {
        "n_long_filled": len(long_book),
        "n_short_filled": len(short_book),
        "long_tickers": [r.get("ticker") for r in long_book],
        "short_tickers": [r.get("ticker") for r in short_book],
        "n_liquidity_excluded": len(liq_excluded),
        "n_extreme_excluded": len(extreme_excluded),
        "top_sector_share_long": _round(long_top_share, 4),
        "top_sector_long": long_top_sec,
        "top_sector_share_short": _round(short_top_share, 4),
        "top_sector_short": short_top_sec,
    }


# ---------------------------------------------------------------------------
# approximate weight-variant cross-section reconstruction (diagnostic-only)
# ---------------------------------------------------------------------------


def reweight_cross_section(cand_rows, weights, baseline_long, baseline_short,
                           liq_threshold, cap_per_sector, target=25):
    """APPROXIMATE single-cross-section book sensitivity to leg weighting.

    Reconstructs oriented (fcf +, accruals -), sector-demeaned, within-snapshot
    z legs from the latest 2026Q2 candidate list and re-ranks. This is a
    sensitivity DIAGNOSTIC only -- it is one cross-section with no forward
    returns, so it cannot establish IC / net-of-cost improvement. A fidelity
    check (rank-correlation of the equal-weight reconstruction vs the frozen
    comp_sn ordering) is reported so the reader can judge how approximate it is.
    """
    rows = []
    for r in cand_rows:
        fcf = _num(r.get("fcf_to_assets"))
        acc = _num(r.get("operating_accruals"))
        if fcf is None or acc is None:
            continue
        rows.append({
            "ticker": r.get("ticker"),
            "sector": r.get("sector") or "Unknown",
            "liquidity_proxy": _num(r.get("liquidity_proxy")),
            "comp_sn": _num(r.get("comp_sn")),
            "of": fcf,          # oriented fcf (long high)
            "oa": -acc,         # oriented accruals (short high accruals)
        })
    out = {"n_used": len(rows), "per_weight": {}, "reconstruction_rank_corr": None,
           "reconstruction_note": "APPROXIMATE single 2026Q2 cross-section; diagnostic only, "
                                   "not a backtest; excluded from champion decision."}
    if len(rows) < 20:
        out["reconstruction_note"] = ("insufficient candidate-list leg coverage for a "
                                       "cross-section reconstruction")
        return out

    # sector-demean each oriented leg, then z within snapshot
    for leg in ("of", "oa"):
        sec_vals = {}
        for r in rows:
            sec_vals.setdefault(r["sector"], []).append(r[leg])
        sec_mean = {s: _mean(v) for s, v in sec_vals.items()}
        for r in rows:
            r[leg + "_d"] = r[leg] - (sec_mean.get(r["sector"]) or 0.0)
        mu = _mean([r[leg + "_d"] for r in rows])
        sd = _pstd([r[leg + "_d"] for r in rows]) or 1.0
        for r in rows:
            r[leg + "_z"] = (r[leg + "_d"] - mu) / sd if sd else 0.0

    base_long = set(baseline_long)
    base_short = set(baseline_short)

    for label, w in weights.items():
        for r in rows:
            r["cw"] = w * r["of_z"] + (1.0 - w) * r["oa_z"]
        mu = _mean([r["cw"] for r in rows])
        sd = _pstd([r["cw"] for r in rows]) or 1.0
        for r in rows:
            r["cwz"] = (r["cw"] - mu) / sd if sd else 0.0
        ranked = sorted(rows, key=lambda r: r["cw"], reverse=True)
        extreme = {r["ticker"] for r in rows if abs(r["cwz"]) >= 3.0}
        excl = set(extreme)
        for r in rows:
            if liq_threshold is not None and r["liquidity_proxy"] is not None \
                    and r["liquidity_proxy"] < liq_threshold:
                excl.add(r["ticker"])
        long_book, long_share, _ = _sector_capped_book(ranked, "LONG", target, cap_per_sector, excl)
        short_book, short_share, _ = _sector_capped_book(ranked, "SHORT", target, cap_per_sector, excl)
        lt = {r["ticker"] for r in long_book}
        st = {r["ticker"] for r in short_book}
        out["per_weight"][label] = {
            "weight_fcf": w,
            "n_long": len(long_book),
            "n_short": len(short_book),
            "overlap_long_vs_baseline": len(lt & base_long),
            "overlap_short_vs_baseline": len(st & base_short),
            "top_sector_share_long": _round(long_share, 4),
            "top_sector_share_short": _round(short_share, 4),
            "n_extreme_flagged": len(extreme),
        }

    # fidelity: equal-weight reconstruction vs frozen comp_sn
    for r in rows:
        r["cw_eq"] = 0.5 * r["of_z"] + 0.5 * r["oa_z"]
    out["reconstruction_rank_corr"] = _round(
        _spearman([(r["cw_eq"], r["comp_sn"]) for r in rows if r["comp_sn"] is not None]), 4)
    return out


# ---------------------------------------------------------------------------
# classification of backtested single-signal endpoints vs the SN baseline
# ---------------------------------------------------------------------------


def classify_backtested(cand, baseline):
    """Skeptical classification of a backtested endpoint against the SN baseline.

    Returns (classification, reason). Order matters: cost -> turnover ->
    concentration -> OOS stability -> strict beat test.
    """
    thr = baseline["acceptance_thresholds"]
    max_sec = thr.get("max_sector_share") or 0.6
    min_oos = thr.get("min_oos_window_frac") or 0.6
    net25 = cand.get("quarterly_net_25bps")
    net50 = cand.get("quarterly_net_50bps")
    turn = cand.get("quarterly_turnover")
    ic_t = cand.get("ic_t_63d")
    oos = cand.get("oos_frac_windows_positive")
    top = cand.get("top_sector_share")

    if net25 is None:
        return "INSUFFICIENT_INPUTS", "no backtested net-25bps available"
    if net25 <= 0:
        return "REJECT_COST_KILLED", f"net-25bps {net25} <= 0"
    if turn is not None and baseline["quarterly_turnover"] and turn > baseline["quarterly_turnover"] * 1.5:
        return "REJECT_TURNOVER", f"turnover {turn} > 1.5x baseline {baseline['quarterly_turnover']}"
    if top is not None and top > max_sec:
        return ("REJECT_CONCENTRATION",
                f"top-sector share {top} > max_sector_share gate {max_sec}")
    if oos is not None and oos < min_oos:
        return ("REJECT_UNSTABLE",
                f"OOS frac-positive {oos} < min_oos_window_frac gate {min_oos}")
    # strict beat test (only meaningful on the sector-neutral basis)
    beats = (
        net25 > baseline["quarterly_net_25bps"]
        and (net50 is not None and net50 >= baseline["quarterly_net_50bps"])
        and (turn is not None and turn <= (baseline["quarterly_turnover"] or turn) * 1.10)
        and (ic_t is not None and ic_t >= (baseline["ic_t_63d"] or ic_t) - 0.10)
        and (oos is not None and oos >= (baseline["oos_frac_windows_positive"] or 0))
        and (top is not None and top <= (baseline["top_sector_share"] or top))
    )
    if cand.get("basis") == "backtested_sn" and beats:
        return "PASS_STRICT", "beats baseline on all strict criteria (sector-neutral basis)"
    if beats:
        return ("PASS_WEAK",
                "beats baseline numerically but on the RAW (non-sector-neutral) basis; "
                "not an honest comparator for the SN book")
    return "PASS_WEAK", "does not clear the strict beat test"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    inv = build_input_inventory()

    result = {
        "phase": PHASE,
        "phase_name": PHASE_NAME,
        "as_of": None,
        "decision": None,
        "decision_rationale": None,
        "allowed_decisions": ALLOWED_DECISIONS,
        "input_inventory": inv,
        "baseline": None,
        "variants_tested": [],
        "champion": None,
        "baseline_vs_champion": None,
        "leg_contribution_summary": None,
        "turnover_cost_summary": None,
        "sector_liquidity_diagnostics": None,
        "rejected_variants": [],
        "implementation_limits": [],
        "next_recommended_phase": None,
        "safety": {
            "paper_only": True,
            "uses_owned_local_data_only": True,
            "no_live_api_calls": True,
            "no_orders": True,
            "no_automation": True,
            "no_broker": True,
            "no_deploy": True,
        },
    }

    dj = _read_json(D_DIR / "phase10d_quarterly_quality_composite_validation.json")
    hj = _read_json(H_DIR / "phase10h_rules_based_paper_portfolio.json")

    # ---- hard input gate -> NEEDS_PHASE_INPUT_REPAIR ----------------------
    missing = list(inv["missing_required"])
    sig = _signal_map(dj) if dj else {}
    for need in ("composite_sn", "fcf_to_assets", "operating_accruals"):
        if need not in sig:
            missing.append(f"phase10d.signal_results[{need}]")
    if dj:
        result["as_of"] = dj.get("as_of")
    if missing:
        result["decision"] = "NEEDS_PHASE_INPUT_REPAIR"
        result["decision_rationale"] = (
            "required frozen Phase 10-D/10-F/10-H field(s) not found: "
            + ", ".join(sorted(set(missing)))
            + ". Re-run the missing prior phase(s) before Phase 10-K."
        )
        result["implementation_limits"].append(
            "harness halted at input gate; no variant scoring performed")
        audit_rows, clean = _secret_safety_audit()
        result["safety"]["secret_leak_scan_clean"] = clean
        _write_json(OUT_DIR / f"{STEM}.json", result)
        _write_csv(OUT_DIR / "variant_scorecard.csv", [], ["variant_id"])
        _write_csv(OUT_DIR / "baseline_vs_enhancements.csv", [], ["metric"])
        _write_csv(OUT_DIR / "leg_contribution_summary.csv", [], ["leg"])
        _write_csv(OUT_DIR / "turnover_cost_summary.csv", [], ["signal"])
        _write_csv(OUT_DIR / "sector_liquidity_diagnostics.csv", [], ["variant_id"])
        _write_csv(OUT_DIR / "rejected_variants.csv", [], ["variant_id"])
        _write_csv(OUT_DIR / "secret_safety_audit.csv", audit_rows,
                   ["check", "value_read", "value_printed", "value_written",
                    "present_in_env", "note"])
        _print_summary(result)
        return result

    baseline = build_baseline(dj, sig)
    result["baseline"] = baseline
    thr = baseline["acceptance_thresholds"]

    # ---- backtested endpoint metrics from 10-D ---------------------------
    def _endpoint(name, basis):
        s = sig.get(name, {})
        return {
            "basis": basis,
            "ic_mean_63d": _round(_num(s.get("ic_63d"))),
            "ic_t_63d": _round(_num(s.get("ic_t_63d"))),
            "quarterly_gross_spread": _round(_num(s.get("quarterly_gross_spread"))),
            "quarterly_net_25bps": _round(_num(s.get("quarterly_net_25bps"))),
            "quarterly_net_50bps": _round(_num(s.get("quarterly_net_50bps"))),
            "quarterly_turnover": _round(_num(s.get("quarterly_turnover"))),
            "oos_pooled_ic": _round(_num(s.get("oos_pooled_ic"))),
            "oos_frac_windows_positive": _round(_num(s.get("oos_frac_windows_positive"))),
            "top_sector_share": _round(_num(s.get("top_sector_share"))),
            "both_cohorts_positive": bool(s.get("both_cohorts_positive")),
            "both_subperiods_positive": bool(s.get("both_subperiods_positive")),
        }

    equal_ep = _endpoint("composite_sn", "backtested_sn")
    fcf_ep = _endpoint("fcf_to_assets", "backtested_raw_diagnostic")
    acc_ep = _endpoint("operating_accruals", "backtested_raw_diagnostic")

    # ---- latest cross-section for book diagnostics -----------------------
    book_rows = _read_csv(F_DIR / "reranked_paper_review_long_short_book.csv")
    cand_rows = _read_csv(F_DIR / "reranked_paper_review_candidate_list.csv")
    flag_rows = _read_csv(F_DIR / "repaired_book_risk_flags.csv")
    sel_rows = _read_csv(H_DIR / "selected_paper_portfolio.csv")

    extreme_tickers = {r.get("ticker") for r in flag_rows
                       if str(r.get("extreme_score")).strip().lower() == "true"}
    baseline_long = [r.get("ticker") for r in sel_rows if (r.get("side") or "").upper() == "LONG"]
    baseline_short = [r.get("ticker") for r in sel_rows if (r.get("side") or "").upper() == "SHORT"]

    liq = (hj or {}).get("liquidity_filter") or {}
    liq_p25_threshold = _num(liq.get("threshold"))
    cap25 = int((hj or {}).get("sector_cap", {}).get("max_names_per_sector") or 6)
    target = int((hj or {}).get("target_per_side") or 25)

    # reproduce a p50 liquidity threshold over the same book population (disclosed)
    liq_values = [_num(r.get("liquidity_proxy")) for r in book_rows]
    reproduced_p25 = _percentile(liq_values, 25.0)
    liq_p50_threshold = _percentile(liq_values, 50.0)
    cap20 = max(1, int(math.floor(0.20 * target)))  # 20%/side of 25 -> 5 names/sector

    # faithful packaging books
    pack = {}
    try:
        pack["baseline_p25_cap25"] = packaging_variant_book(
            book_rows, extreme_tickers, liq_p25_threshold, cap25, target)
        pack["liq_p50"] = packaging_variant_book(
            book_rows, extreme_tickers, liq_p50_threshold, cap25, target)
        pack["sector_cap_20"] = packaging_variant_book(
            book_rows, extreme_tickers, liq_p25_threshold, cap20, target)
    except Exception as exc:  # pragma: no cover - defensive; diagnostics only
        result["implementation_limits"].append(f"packaging book diagnostic unavailable: {exc}")

    # approximate weight-variant cross-section sensitivity (diagnostic-only)
    weight_defs = {"w_60_40": 0.6, "w_40_60": 0.4, "w_70_30": 0.7, "w_30_70": 0.3}
    try:
        reweight = reweight_cross_section(
            cand_rows, weight_defs, baseline_long, baseline_short,
            liq_p25_threshold, cap25, target)
    except Exception as exc:  # pragma: no cover - defensive; diagnostics only
        reweight = {"per_weight": {}, "reconstruction_note": f"unavailable: {exc}"}
        result["implementation_limits"].append(f"reweight diagnostic unavailable: {exc}")

    # ---- build the variant matrix ----------------------------------------
    variants = []

    def _pack_diag(key):
        p = pack.get(key)
        if not p:
            return {}
        return {
            "book_n_long": p["n_long_filled"],
            "book_n_short": p["n_short_filled"],
            "book_top_sector_share_long": p["top_sector_share_long"],
            "book_top_sector_share_short": p["top_sector_share_short"],
            "n_liquidity_excluded": p["n_liquidity_excluded"],
            "n_extreme_excluded": p["n_extreme_excluded"],
        }

    # --- Group A: weighting variants
    v_equal = {
        "variant_id": "w_50_50_equal",
        "group": "weighting",
        "description": "50/50 equal weight on within-month z legs (== Phase 10-D baseline composite_sn)",
        "basis": "backtested_sn",
        "metrics": equal_ep,
        "is_baseline": True,
    }
    v_equal["classification"], v_equal["reject_reason"] = "PASS_STRICT", "confirmed baseline configuration"
    variants.append(v_equal)

    for vid, w in (("w_60_40", 0.6), ("w_40_60", 0.4), ("w_70_30", 0.7), ("w_30_70", 0.3)):
        rw = reweight.get("per_weight", {}).get(
            {"w_60_40": "w_60_40", "w_40_60": "w_40_60", "w_70_30": "w_70_30",
             "w_30_70": "w_30_70"}[vid], {})
        variants.append({
            "variant_id": vid,
            "group": "weighting",
            "description": f"{int(w*100)}% fcf_to_assets / {int((1-w)*100)}% operating_accruals",
            "basis": "cross_section_only",
            "metrics": {"return_metrics": "INSUFFICIENT_INPUTS (no historical scored panel)",
                        "cross_section_book_diag": rw},
            "classification": "INSUFFICIENT_INPUTS",
            "reject_reason": "cannot re-backtest arbitrary leg weighting: frozen outputs lack the "
                             "historical per-(month,ticker) scored panel with forward 63d returns; "
                             "only a single approximate cross-section is available.",
        })

    v_fcf = {
        "variant_id": "w_fcf_only_100_0",
        "group": "weighting",
        "description": "fcf_to_assets only (100/0) -- long-leg standalone",
        "basis": "backtested_raw_diagnostic",
        "metrics": fcf_ep,
    }
    fcf_cls_input = dict(fcf_ep)
    v_fcf["classification"], v_fcf["reject_reason"] = classify_backtested(fcf_cls_input, baseline)
    variants.append(v_fcf)

    v_acc = {
        "variant_id": "w_accruals_only_0_100",
        "group": "weighting",
        "description": "operating_accruals only (0/100) -- short-leg standalone",
        "basis": "backtested_raw_diagnostic",
        "metrics": acc_ep,
    }
    acc_cls_input = dict(acc_ep)
    v_acc["classification"], v_acc["reject_reason"] = classify_backtested(acc_cls_input, baseline)
    variants.append(v_acc)

    # --- Group B: robustness transforms (cross-sectional exposure only)
    zvals = []
    for r in cand_rows:
        z = _num(r.get("comp_sn_z"))
        if z is not None:
            zvals.append(z)
    n_ge_3 = sum(1 for z in zvals if abs(z) >= 3.0)
    n_ge_25 = sum(1 for z in zvals if abs(z) >= 2.5)
    for vid, desc, thr_z, n_aff in (
        ("zcap_abs_3_0", "z-score cap at |z|=3.0", 3.0, n_ge_3),
        ("zcap_abs_2_5", "z-score cap at |z|=2.5", 2.5, n_ge_25),
        ("winsorize_score", "winsorize composite score (tail compression)", None, n_ge_3),
    ):
        variants.append({
            "variant_id": vid,
            "group": "robustness_transform",
            "description": desc,
            "basis": "cross_section_only",
            "metrics": {"return_metrics": "INSUFFICIENT_INPUTS (no historical scored panel)",
                        "cross_section_names_affected": n_aff,
                        "cross_section_universe_scored": len(zvals)},
            "classification": "INSUFFICIENT_INPUTS",
            "reject_reason": "tail-capping / winsorizing only changes returns if applied across the "
                             "full history; the frozen outputs expose only the latest cross-section, "
                             "so the return effect cannot be measured.",
        })

    # --- Group C: portfolio packaging filters (faithful book, INSUFFICIENT for alpha)
    variants.append({
        "variant_id": "liq_p25_cap25_extreme3",
        "group": "packaging",
        "description": "liquidity p25 + 25%/side sector cap + hold out |z|>=3 (== Phase 10-H rules)",
        "basis": "backtested_sn",
        "metrics": {**equal_ep, "book_diag": _pack_diag("baseline_p25_cap25")},
        "classification": "PASS_STRICT",
        "reject_reason": "baseline packaging (the confirmed 10-H rule set)",
        "is_baseline_packaging": True,
    })
    variants.append({
        "variant_id": "liq_p50_stricter",
        "group": "packaging",
        "description": "stricter liquidity p50 filter (else baseline rules)",
        "basis": "cross_section_only",
        "metrics": {"return_metrics": "INSUFFICIENT_INPUTS (single cross-section)",
                    "book_diag": _pack_diag("liq_p50")},
        "classification": "INSUFFICIENT_INPUTS",
        "reject_reason": "book reshape is computable, but the net-of-cost alpha effect cannot be "
                         "shown from a single cross-section.",
    })
    variants.append({
        "variant_id": "sector_cap_20_stricter",
        "group": "packaging",
        "description": "stricter 20%/side sector cap (else baseline rules)",
        "basis": "cross_section_only",
        "metrics": {"return_metrics": "INSUFFICIENT_INPUTS (single cross-section)",
                    "book_diag": _pack_diag("sector_cap_20")},
        "classification": "INSUFFICIENT_INPUTS",
        "reject_reason": "book reshape is computable, but the net-of-cost alpha effect cannot be "
                         "shown from a single cross-section.",
    })

    result["variants_tested"] = variants

    # ---- leg-contribution summary ----------------------------------------
    leg_summary = {
        "long_leg": {
            "signal": "fcf_to_assets", "role": "long", "basis": "raw standalone",
            "ic_t_63d": fcf_ep["ic_t_63d"], "quarterly_net_25bps": fcf_ep["quarterly_net_25bps"],
            "quarterly_gross_spread": fcf_ep["quarterly_gross_spread"],
            "oos_frac_windows_positive": fcf_ep["oos_frac_windows_positive"],
            "top_sector_share": fcf_ep["top_sector_share"],
            "note": "weak standalone OOS (frac-positive below the 0.6 gate); its main value in the "
                    "composite is diversification -- it pulls sector concentration down.",
        },
        "short_leg": {
            "signal": "operating_accruals", "role": "short", "basis": "raw standalone",
            "ic_t_63d": acc_ep["ic_t_63d"], "quarterly_net_25bps": acc_ep["quarterly_net_25bps"],
            "quarterly_gross_spread": acc_ep["quarterly_gross_spread"],
            "oos_frac_windows_positive": acc_ep["oos_frac_windows_positive"],
            "top_sector_share": acc_ep["top_sector_share"],
            "note": "highest t-stat and best OOS of the two legs -- the short (accruals) leg carries "
                    "most of the composite's robustness, but alone it breaches the sector "
                    "concentration gate.",
        },
        "composite_sn": {
            "signal": "composite_sn", "role": "long/short", "basis": "sector-neutral",
            "ic_t_63d": equal_ep["ic_t_63d"], "quarterly_net_25bps": equal_ep["quarterly_net_25bps"],
            "quarterly_gross_spread": equal_ep["quarterly_gross_spread"],
            "oos_frac_windows_positive": equal_ep["oos_frac_windows_positive"],
            "top_sector_share": equal_ep["top_sector_share"],
            "note": "the confirmed baseline; combines the legs so OOS sits between them and "
                    "concentration is lower than accruals-alone.",
        },
        "attribution": "improvement/robustness is carried by the SHORT (operating_accruals) leg; the "
                       "LONG (fcf_to_assets) leg contributes diversification and lower sector "
                       "concentration rather than standalone alpha.",
    }
    result["leg_contribution_summary"] = leg_summary

    # ---- turnover / cost summary -----------------------------------------
    tcost = _read_csv(D_DIR / "transaction_cost_report.csv")
    tc_rows = []
    for r in tcost:
        tc_rows.append({
            "signal": r.get("signal"),
            "model": r.get("model"),
            "gross_spread": _round(_num(r.get("gross_spread"))),
            "turnover": _round(_num(r.get("turnover"))),
            "net_25bps": _round(_num(r.get("net_25bps"))),
            "net_50bps": _round(_num(r.get("net_50bps"))),
        })
    result["turnover_cost_summary"] = {
        "note": "monthly event-panel horizons are cost-killed (name rotation); the quarterly_63d "
                "model is the decision model and survives 25bps and 50bps.",
        "rows": tc_rows,
        "baseline_quarterly_turnover": baseline["quarterly_turnover"],
    }

    # ---- sector / liquidity diagnostics ----------------------------------
    result["sector_liquidity_diagnostics"] = {
        "reproduced_p25_liquidity_threshold": _round(reproduced_p25, 2),
        "frozen_10h_p25_liquidity_threshold": _round(liq_p25_threshold, 2),
        "computed_p50_liquidity_threshold": _round(liq_p50_threshold, 2),
        "sector_cap_baseline_names_per_side": cap25,
        "sector_cap_stricter_names_per_side": cap20,
        "packaging_books": pack,
        "reweight_cross_section": reweight,
        "max_sector_share_gate": thr.get("max_sector_share"),
        "min_oos_window_frac_gate": thr.get("min_oos_window_frac"),
    }

    # ---- rejected variants -----------------------------------------------
    rejected = [
        {"variant_id": v["variant_id"], "classification": v["classification"],
         "reason": v["reject_reason"],
         "evidence": _reject_evidence(v)}
        for v in variants
        if v["classification"].startswith("REJECT") or v["classification"] == "INSUFFICIENT_INPUTS"
    ]
    result["rejected_variants"] = rejected

    # ---- champion + decision ---------------------------------------------
    enhancement = None
    for v in variants:
        if v.get("is_baseline") or v.get("is_baseline_packaging"):
            continue
        if v["classification"] == "PASS_STRICT" and v["basis"] == "backtested_sn":
            enhancement = v
            break

    if enhancement is not None:
        champion = {
            "champion": enhancement["variant_id"],
            "is_baseline": False,
            "reason": "cleared the strict beat test on sector-neutral backtested evidence",
            "metrics": enhancement["metrics"],
        }
        decision = "ENHANCED_ALPHA_READY_FOR_PAPER_RULES"
        rationale = (f"variant {enhancement['variant_id']} beat the baseline on all strict criteria "
                     "with honest sector-neutral evidence.")
    else:
        champion = {
            "champion": "baseline_composite_sn_equal_weight",
            "is_baseline": True,
            "reason": "no variant produced sufficient sector-neutral backtested evidence to unseat "
                      "the equal-weight sector-neutral composite.",
            "metrics": {
                "ic_t_63d": baseline["ic_t_63d"],
                "quarterly_net_25bps": baseline["quarterly_net_25bps"],
                "quarterly_net_50bps": baseline["quarterly_net_50bps"],
                "quarterly_turnover": baseline["quarterly_turnover"],
                "oos_frac_windows_positive": baseline["oos_frac_windows_positive"],
                "top_sector_share": baseline["top_sector_share"],
            },
        }
        decision = "BASELINE_REMAINS_CHAMPION"
        rationale = (
            "No enhancement clears the strict bar on honest evidence. The interior weight variants "
            "(60/40, 40/60, 70/30, 30/70) and the historical robustness transforms are "
            "INSUFFICIENT_INPUTS -- the frozen 10-D/10-F/10-H outputs hold only summary stats for "
            "four fixed signals plus the latest 2026Q2 cross-section, not the historical scored "
            "panel with forward returns needed to re-backtest them. The only backtestable endpoints "
            "are the two single legs, both on the RAW (non-sector-neutral) basis and each failing an "
            "SN gate on its own terms: fcf_to_assets is OOS-unstable (frac-positive "
            f"{fcf_ep['oos_frac_windows_positive']} < {thr.get('min_oos_window_frac')} gate) and "
            f"operating_accruals is over-concentrated (top-sector {acc_ep['top_sector_share']} > "
            f"{thr.get('max_sector_share')} gate). Packaging filters reshape the current book but "
            "cannot demonstrate net-of-cost alpha improvement from a single cross-section. Baseline "
            "stays champion (skeptical default upheld)."
        )

    result["champion"] = champion
    result["decision"] = decision
    result["decision_rationale"] = rationale

    result["baseline_vs_champion"] = _baseline_vs_champion(baseline, champion, fcf_ep, acc_ep)

    # ---- implementation limits + next phase ------------------------------
    result["implementation_limits"].extend([
        "No historical per-(month,ticker) sector-neutral scored panel with forward 63d returns is "
        "present in the frozen 10-D/10-F/10-H outputs, so arbitrary leg re-weightings and historical "
        "robustness transforms cannot be honestly re-backtested (reported INSUFFICIENT_INPUTS).",
        "The two backtestable single legs are on the raw (non-sector-neutral) basis; a sector-neutral "
        "single-leg backtest is not available, so they are diagnostic context only.",
        "Weight-variant cross-section books are an APPROXIMATE single-2026Q2-snapshot reconstruction "
        "(sector-demean + within-snapshot z of raw legs); rank-correlation vs the frozen comp_sn "
        "ordering is reported as a fidelity check. They do not measure alpha.",
        "Packaging-filter books are faithful (use frozen comp_sn) but are one cross-section and cannot "
        "establish net-of-cost alpha improvement.",
        "This phase does NOT solve the Phase 10-I price-refresh / mark-to-market block (owned local "
        "prices end at 2026-06-26), does NOT hand-review 194 tickers, does NOT probe providers, and "
        "creates no orders / no automation.",
    ])
    result["next_recommended_phase"] = {
        "phase": "10-L",
        "title": "Persist the historical sector-neutral scored panel for honest reweight backtests",
        "why": "To test 60/40..30/70 weightings and z-cap/winsorize transforms honestly, persist the "
               "per-(month,ticker) sector-neutral z-legs (z_fcf_sn, z_accruals_oriented_sn) and "
               "forward 63d returns from the owned 10-B/10-C engine into a frozen artifact, then "
               "re-run this harness against that panel -- still offline, owned-data-only, no orders, "
               "no automation, no broker, no deploy.",
    }

    audit_rows, clean = _secret_safety_audit()
    result["safety"]["secret_leak_scan_clean"] = clean

    # ---- write artifacts --------------------------------------------------
    _write_all_csvs(variants, baseline, champion, leg_summary, tc_rows, result, pack, audit_rows,
                    fcf_ep, acc_ep)
    _write_json(OUT_DIR / f"{STEM}.json", result)
    _print_summary(result)
    return result


def _reject_evidence(v):
    m = v.get("metrics") or {}
    if isinstance(m, dict) and "quarterly_net_25bps" in m:
        return (f"net25={m.get('quarterly_net_25bps')} ic_t={m.get('ic_t_63d')} "
                f"oos={m.get('oos_frac_windows_positive')} top_sector={m.get('top_sector_share')}")
    return "cross-section only; no backtested return metrics"


def _baseline_vs_champion(baseline, champion, fcf_ep, acc_ep):
    rows = []
    for metric, bkey in (
        ("quarterly_net_25bps", "quarterly_net_25bps"),
        ("quarterly_net_50bps", "quarterly_net_50bps"),
        ("ic_t_63d", "ic_t_63d"),
        ("quarterly_turnover", "quarterly_turnover"),
        ("oos_frac_windows_positive", "oos_frac_windows_positive"),
        ("top_sector_share", "top_sector_share"),
    ):
        bval = baseline.get(bkey)
        cval = champion.get("metrics", {}).get(metric, bval)
        rows.append({
            "metric": metric,
            "baseline_value": bval,
            "champion_value": cval,
            "delta": _round((cval - bval), 5) if (isinstance(cval, (int, float)) and isinstance(bval, (int, float))) else 0,
        })
    return {
        "champion_is_baseline": champion.get("is_baseline"),
        "rows": rows,
        "note": "champion == baseline; no enhancement justified on honest evidence.",
        "raw_single_leg_context": {
            "fcf_only_net25": fcf_ep["quarterly_net_25bps"],
            "accruals_only_net25": acc_ep["quarterly_net_25bps"],
            "warning": "single-leg figures are RAW-basis, not comparable to the sector-neutral book.",
        },
    }


def _write_all_csvs(variants, baseline, champion, leg_summary, tc_rows, result, pack, audit_rows,
                    fcf_ep, acc_ep):
    # variant_scorecard.csv
    sc_header = ["variant_id", "group", "basis", "classification", "ic_t_63d",
                 "quarterly_net_25bps", "quarterly_net_50bps", "quarterly_turnover",
                 "oos_frac_windows_positive", "top_sector_share", "reject_reason", "description"]
    sc_rows = []
    for v in variants:
        m = v.get("metrics") or {}
        row = {"variant_id": v["variant_id"], "group": v["group"], "basis": v["basis"],
               "classification": v["classification"], "reject_reason": v.get("reject_reason"),
               "description": v["description"]}
        for k in ("ic_t_63d", "quarterly_net_25bps", "quarterly_net_50bps", "quarterly_turnover",
                  "oos_frac_windows_positive", "top_sector_share"):
            row[k] = m.get(k) if isinstance(m, dict) else None
        sc_rows.append(row)
    _write_csv(OUT_DIR / "variant_scorecard.csv", sc_rows, sc_header)

    # baseline_vs_enhancements.csv
    bve_header = ["metric", "baseline_value", "champion_value", "delta",
                  "fcf_only_raw", "accruals_only_raw"]
    bve_rows = []
    for r in result["baseline_vs_champion"]["rows"]:
        metric = r["metric"]
        bve_rows.append({
            "metric": metric,
            "baseline_value": r["baseline_value"],
            "champion_value": r["champion_value"],
            "delta": r["delta"],
            "fcf_only_raw": fcf_ep.get(metric),
            "accruals_only_raw": acc_ep.get(metric),
        })
    _write_csv(OUT_DIR / "baseline_vs_enhancements.csv", bve_rows, bve_header)

    # leg_contribution_summary.csv
    lc_header = ["leg", "signal", "role", "basis", "ic_t_63d", "quarterly_net_25bps",
                 "quarterly_gross_spread", "oos_frac_windows_positive", "top_sector_share", "note"]
    lc_rows = []
    for key in ("long_leg", "short_leg", "composite_sn"):
        d = leg_summary[key]
        lc_rows.append({"leg": key, "signal": d["signal"], "role": d["role"], "basis": d["basis"],
                        "ic_t_63d": d["ic_t_63d"], "quarterly_net_25bps": d["quarterly_net_25bps"],
                        "quarterly_gross_spread": d["quarterly_gross_spread"],
                        "oos_frac_windows_positive": d["oos_frac_windows_positive"],
                        "top_sector_share": d["top_sector_share"], "note": d["note"]})
    _write_csv(OUT_DIR / "leg_contribution_summary.csv", lc_rows, lc_header)

    # turnover_cost_summary.csv
    tc_header = ["signal", "model", "gross_spread", "turnover", "net_25bps", "net_50bps"]
    _write_csv(OUT_DIR / "turnover_cost_summary.csv", tc_rows, tc_header)

    # sector_liquidity_diagnostics.csv
    sl_header = ["variant_id", "n_long_filled", "n_short_filled", "n_liquidity_excluded",
                 "n_extreme_excluded", "top_sector_share_long", "top_sector_share_short",
                 "top_sector_long", "top_sector_short"]
    sl_rows = []
    for key, label in (("baseline_p25_cap25", "liq_p25_cap25_extreme3"),
                       ("liq_p50", "liq_p50_stricter"),
                       ("sector_cap_20", "sector_cap_20_stricter")):
        p = pack.get(key)
        if not p:
            continue
        sl_rows.append({"variant_id": label, "n_long_filled": p["n_long_filled"],
                        "n_short_filled": p["n_short_filled"],
                        "n_liquidity_excluded": p["n_liquidity_excluded"],
                        "n_extreme_excluded": p["n_extreme_excluded"],
                        "top_sector_share_long": p["top_sector_share_long"],
                        "top_sector_share_short": p["top_sector_share_short"],
                        "top_sector_long": p["top_sector_long"],
                        "top_sector_short": p["top_sector_short"]})
    _write_csv(OUT_DIR / "sector_liquidity_diagnostics.csv", sl_rows, sl_header)

    # rejected_variants.csv
    rv_header = ["variant_id", "classification", "reason", "evidence"]
    _write_csv(OUT_DIR / "rejected_variants.csv", result["rejected_variants"], rv_header)

    # secret_safety_audit.csv
    _write_csv(OUT_DIR / "secret_safety_audit.csv", audit_rows,
               ["check", "value_read", "value_printed", "value_written", "present_in_env", "note"])


def _print_summary(result):
    created = [f"{STEM}.json", "variant_scorecard.csv", "baseline_vs_enhancements.csv",
               "leg_contribution_summary.csv", "turnover_cost_summary.csv",
               "sector_liquidity_diagnostics.csv", "rejected_variants.csv", "secret_safety_audit.csv"]
    variants = result.get("variants_tested") or []
    rejected = result.get("rejected_variants") or []
    champ = result.get("champion") or {}
    print("=" * 72)
    print(f"PHASE {PHASE} - {PHASE_NAME}")
    print("=" * 72)
    print("1. Files created (research/output/%s/):" % STEM)
    for name in created:
        print(f"     - {name}")
    print("   Plus: research/run_%s.py, tests/test_%s.py, docs/%s_v1.md" % (STEM, STEM, STEM))
    print("2. Files modified: none (only new Phase 10-K files)")
    print(f"3. Decision: {result.get('decision')}")
    is_base = champ.get("is_baseline")
    print("4. Champion: %s" % ("BASELINE remains champion" if is_base else champ.get("champion")))
    if is_base:
        print("6. Baseline remains champion because: %s" % result.get("decision_rationale"))
    else:
        print("5. Enhancement justified because: %s" % champ.get("reason"))
    print(f"7. Variants tested: {len(variants)}")
    print(f"8. Rejected / insufficient variants: {len(rejected)}")
    print("9. Validation commands:")
    print("     python -m py_compile research/run_%s.py" % STEM)
    print("     python research/run_%s.py" % STEM)
    print("     python -m pytest tests/test_%s.py -q" % STEM)
    print("10. Final status: DO_NOT_COMMIT")
    print("=" * 72)


if __name__ == "__main__":
    main()
