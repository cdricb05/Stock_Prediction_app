"""Phase 2H-A NO_GO diagnostic analyzer (offline, read-only, file-based).

Reads the Phase 2G-C real-data validation artifacts and produces a single
diagnostics JSON explaining why the canary gate returned NO_GO and what to test
next. Guardrail rationale lives in docs/phase2h_no_go_diagnostics_v1.md, not here.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

PHASE = "2H-A"

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUTPUT_DIR = os.path.join("research", "output")

# Inputs (read-only).
VALIDATION_JSON = os.path.join(_OUTPUT_DIR, "phase2g_real_data_validation.json")
SCORED_CSV = os.path.join(_OUTPUT_DIR, "phase2g_real_data_scored.csv")
RUN_SUMMARY_JSON = os.path.join(_OUTPUT_DIR, "phase2g_c_real_data_run_summary.json")

# The single output this analyzer is allowed to write.
DIAGNOSTICS_JSON = os.path.join(_OUTPUT_DIR, "phase2h_no_go_diagnostics.json")

# Decision thresholds (the harness canary gate uses these).
RANK_IC_FLOOR = 0.03
HIT_RATE_FLOOR = 0.50
MIN_DATES = 120
MIN_TICKERS = 20

SCORE_COL = "score"
TARGET_COL = "realized_excess_return_5d_vs_spy"
DATE_COL = "as_of_date"
TICKER_COL = "ticker"


# --------------------------------------------------------------------------- #
# Loading (read-only)
# --------------------------------------------------------------------------- #
def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_inputs() -> Tuple[Dict[str, Any], "pd.DataFrame", Dict[str, Any]]:
    validation = _read_json(VALIDATION_JSON)
    scored = pd.read_csv(SCORED_CSV)
    run_summary = _read_json(RUN_SUMMARY_JSON)
    return validation, scored, run_summary


# --------------------------------------------------------------------------- #
# Metric helpers (pure)
# --------------------------------------------------------------------------- #
def _round(x: Optional[float], n: int = 6) -> Optional[float]:
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    return round(float(x), n)


def _spearman(a: "pd.Series", b: "pd.Series") -> float:
    """Spearman rank correlation as Pearson on ranks (no scipy dependency)."""
    pair = pd.DataFrame({"a": a, "b": b}).dropna()
    if len(pair) < 2:
        return float("nan")
    return pair["a"].rank().corr(pair["b"].rank())


def _per_date_rank_ic(frame: "pd.DataFrame") -> "pd.Series":
    """Cross-sectional Spearman(score, realized) per as_of_date.

    Mirrors the Phase 2G-A harness: rank correlation within each date, then
    these are averaged. Dates with fewer than two names yield NaN and are
    dropped by the callers.
    """
    def _ic(g: "pd.DataFrame") -> float:
        return _spearman(g[SCORE_COL], g[TARGET_COL])

    return frame.groupby(DATE_COL, sort=True).apply(_ic)


def _year(series: "pd.Series") -> "pd.Series":
    return pd.to_datetime(series).dt.year


def _quarter(series: "pd.Series") -> "pd.Series":
    d = pd.to_datetime(series)
    return d.dt.year.astype(str) + "-Q" + d.dt.quarter.astype(str)


def rank_ic_overall(frame: "pd.DataFrame") -> Tuple[Optional[float], int]:
    ics = _per_date_rank_ic(frame).dropna()
    if not len(ics):
        return None, 0
    return float(ics.mean()), int(len(ics))


def rank_ic_by(frame: "pd.DataFrame", key: "pd.Series") -> List[Dict[str, Any]]:
    """Mean per-date rank IC grouped by a calendar key (year or quarter)."""
    ics = _per_date_rank_ic(frame).dropna()
    if not len(ics):
        return []
    keyed = key.copy()
    keyed.index = frame.index
    # Map each date's key via the first row of that date.
    date_to_key = (frame.assign(_k=keyed.values)
                   .groupby(DATE_COL)["_k"].first())
    by = ics.groupby(ics.index.map(date_to_key))
    out = []
    for k, grp in by:
        out.append({"period": str(k),
                    "rank_ic": _round(grp.mean()),
                    "n_dates": int(len(grp))})
    out.sort(key=lambda r: r["period"])
    return out


def top_decile_by_year(frame: "pd.DataFrame") -> List[Dict[str, Any]]:
    """Per-year top-decile-by-score 5d excess vs the per-date universe mean."""
    rows: List[Dict[str, Any]] = []
    work = frame.copy()
    work["_year"] = _year(work[DATE_COL])
    for yr, ydf in work.groupby("_year"):
        td_vals: List[float] = []
        uni_vals: List[float] = []
        hits = 0
        n_dates = 0
        for _, g in ydf.groupby(DATE_COL):
            if len(g) < 10:
                continue
            n_dates += 1
            cutoff = g[SCORE_COL].quantile(0.9)
            top = g[g[SCORE_COL] >= cutoff]
            if not len(top):
                continue
            td_mean = top[TARGET_COL].mean()
            td_vals.append(td_mean)
            uni_vals.append(g[TARGET_COL].mean())
            if td_mean > 0:
                hits += 1
        if not td_vals:
            continue
        td_series = pd.Series(td_vals)
        rows.append({
            "year": int(yr),
            "n_dates": n_dates,
            "top_decile_mean_excess_return": _round(td_series.mean()),
            "universe_mean_excess_return": _round(pd.Series(uni_vals).mean()),
            "top_decile_hit_rate": _round(hits / len(td_vals)),
        })
    rows.sort(key=lambda r: r["year"])
    return rows


def hit_rate_by_year(frame: "pd.DataFrame") -> List[Dict[str, Any]]:
    """Per-year share of names whose realized 5d excess vs SPY was positive."""
    work = frame.copy()
    work["_year"] = _year(work[DATE_COL])
    rows = []
    for yr, ydf in work.groupby("_year"):
        positive = (ydf[TARGET_COL] > 0).mean()
        rows.append({"year": int(yr),
                     "n_rows": int(len(ydf)),
                     "positive_excess_rate": _round(positive)})
    rows.sort(key=lambda r: r["year"])
    return rows


def ticker_behavior(frame: "pd.DataFrame", n: int = 5
                    ) -> Dict[str, List[Dict[str, Any]]]:
    """Per-ticker rank IC and mean realized excess; surface best/worst names."""
    rows: List[Dict[str, Any]] = []
    for tkr, g in frame.groupby(TICKER_COL):
        if len(g) < 2:
            continue
        ic = _spearman(g[SCORE_COL], g[TARGET_COL])
        rows.append({
            "ticker": str(tkr),
            "n_rows": int(len(g)),
            "ticker_rank_ic": _round(ic),
            "mean_realized_excess_return": _round(g[TARGET_COL].mean()),
            "positive_excess_rate": _round((g[TARGET_COL] > 0).mean()),
        })
    ranked = [r for r in rows if r["ticker_rank_ic"] is not None]
    ranked.sort(key=lambda r: r["ticker_rank_ic"], reverse=True)
    return {
        "best_by_rank_ic": ranked[:n],
        "worst_by_rank_ic": list(reversed(ranked[-n:])) if ranked else [],
    }


# --------------------------------------------------------------------------- #
# Root-cause attribution (pure)
# --------------------------------------------------------------------------- #
def diagnose_root_causes(*, rank_ic: Optional[float], bucket_monotonic: bool,
                         top_decile_hit_rate: Optional[float],
                         top_decile_mean_excess: Optional[float],
                         universe_mean_excess: Optional[float],
                         n_dates: int, n_tickers: int,
                         legacy_comparison_available: bool) -> Dict[str, Any]:
    weak_rank_signal = (rank_ic is None) or (abs(rank_ic) < RANK_IC_FLOOR)
    non_monotonic_buckets = not bool(bucket_monotonic)
    top_decile_weak = (
        (top_decile_hit_rate is None or top_decile_hit_rate < HIT_RATE_FLOOR)
        or (top_decile_mean_excess is not None
            and universe_mean_excess is not None
            and top_decile_mean_excess <= universe_mean_excess))
    insufficient_sample = (n_dates < MIN_DATES) or (n_tickers < MIN_TICKERS)
    missing_legacy_comparison = not bool(legacy_comparison_available)

    flags = {
        "weak_rank_signal": bool(weak_rank_signal),
        "non_monotonic_buckets": bool(non_monotonic_buckets),
        "top_decile_weak": bool(top_decile_weak),
        "insufficient_sample": bool(insufficient_sample),
        "missing_legacy_comparison": bool(missing_legacy_comparison),
    }

    if weak_rank_signal and non_monotonic_buckets and not insufficient_sample:
        category = "signal_issue"
    elif insufficient_sample:
        category = "data_issue"
    elif missing_legacy_comparison and not weak_rank_signal:
        category = "gate_issue"
    else:
        category = "signal_issue"

    primary: List[str] = [k for k, v in flags.items() if v]
    return {
        "flags": flags,
        "failure_category": category,
        "primary_drivers": primary,
    }


def recommended_experiments(root_causes: Dict[str, Any]) -> List[Dict[str, str]]:
    """Concrete next modeling experiments. Research only; nothing is enabled."""
    exps: List[Dict[str, str]] = []
    flags = root_causes["flags"]
    if flags["weak_rank_signal"] or flags["non_monotonic_buckets"]:
        exps.append({
            "experiment": "feature_reweighting",
            "detail": "Replace the equal-weight mean-z composite with an "
                      "out-of-sample fitted cross-sectional weighting "
                      "(e.g. ridge / rank-regression on point-in-time "
                      "features), validated walk-forward to avoid lookahead.",
        })
        exps.append({
            "experiment": "horizon_sweep",
            "detail": "Re-score at 10d / 21d / 63d forward horizons; the 5d "
                      "excess target may be dominated by noise at the "
                      "weekly horizon.",
        })
        exps.append({
            "experiment": "feature_pruning",
            "detail": "Drop features with near-zero univariate per-date rank "
                      "IC and re-test; isolate momentum vs mean-reversion "
                      "blocks separately.",
        })
    if flags["top_decile_weak"]:
        exps.append({
            "experiment": "neutralization",
            "detail": "Sector / beta neutralize the score before ranking so "
                      "the top decile is not just a sector or high-beta tilt.",
        })
    if flags["missing_legacy_comparison"]:
        exps.append({
            "experiment": "wire_legacy_comparison",
            "detail": "Capture the existing production prediction series over "
                      "the same window and compute its rank IC head-to-head; "
                      "the canary gate requires this baseline before any "
                      "GO_CANDIDATE.",
        })
    if flags["insufficient_sample"]:
        exps.append({
            "experiment": "extend_window_and_universe",
            "detail": "Lengthen the history and widen the universe to raise "
                      "the per-date cross-section and the number of dates.",
        })
    return exps


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_diagnostics(validation: Dict[str, Any], scored: "pd.DataFrame",
                      run_summary: Dict[str, Any]) -> Dict[str, Any]:
    a = validation.get("phase2g_a_summary", {})

    reported_rank_ic = a.get("rank_ic")
    recomputed_rank_ic, n_dates_recomputed = rank_ic_overall(scored)
    bucket_monotonic = bool(a.get("bucket_monotonic", False))
    top_decile_hit_rate = a.get("top_decile_hit_rate")
    top_decile_mean_excess = a.get("top_decile_mean_excess_return")
    universe_mean_excess = a.get("universe_mean_excess_return")
    n_tickers = int(a.get("ticker_count")
                    or scored[TICKER_COL].nunique())
    n_dates = int(a.get("top_decile_n_dates")
                  or scored[DATE_COL].nunique())
    legacy_available = bool(a.get("legacy_comparison_available", False))

    root_causes = diagnose_root_causes(
        rank_ic=reported_rank_ic, bucket_monotonic=bucket_monotonic,
        top_decile_hit_rate=top_decile_hit_rate,
        top_decile_mean_excess=top_decile_mean_excess,
        universe_mean_excess=universe_mean_excess,
        n_dates=n_dates, n_tickers=n_tickers,
        legacy_comparison_available=legacy_available)

    return {
        "phase": PHASE,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "validation_json": VALIDATION_JSON,
            "scored_csv": SCORED_CSV,
            "run_summary_json": RUN_SUMMARY_JSON,
        },
        # Safety flags (machine-readable; asserted by the tests).
        "database_touched": False,
        "database_write_executed": False,
        "migration_executed": False,
        "deployment_executed": False,
        "model_v2_enabled": False,
        "production_edge_claimed": False,
        "no_trading": True,
        "no_orders": True,
        "no_automation": True,
        # Verdict carried forward from Phase 2G-C (unchanged here).
        "go_no_go": validation.get("go_no_go"),
        "safe_for_canary": validation.get("safe_for_canary"),
        "overall": {
            "reported_rank_ic": _round(reported_rank_ic),
            "recomputed_rank_ic": _round(recomputed_rank_ic),
            "recomputed_n_dates": n_dates_recomputed,
            "rank_ic_floor": RANK_IC_FLOOR,
            "top_decile_mean_excess_return": _round(top_decile_mean_excess),
            "universe_mean_excess_return": _round(universe_mean_excess),
            "top_decile_hit_rate": _round(top_decile_hit_rate),
            "hit_rate_floor": HIT_RATE_FLOOR,
            "bucket_monotonic": bucket_monotonic,
            "n_dates": n_dates,
            "n_tickers": n_tickers,
            "legacy_comparison_available": legacy_available,
        },
        "rank_ic_by_year": rank_ic_by(scored, _year(scored[DATE_COL])),
        "rank_ic_by_quarter": rank_ic_by(scored, _quarter(scored[DATE_COL])),
        "top_decile_excess_return_by_year": top_decile_by_year(scored),
        "hit_rate_by_year": hit_rate_by_year(scored),
        "score_buckets": a.get("score_buckets", []),
        "ticker_behavior": ticker_behavior(scored),
        "root_causes": root_causes,
        "recommended_experiments": recommended_experiments(root_causes),
    }


def write_diagnostics(diagnostics: Dict[str, Any],
                      path: str = DIAGNOSTICS_JSON) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(diagnostics, f, indent=2, allow_nan=False)


def run(output_path: str = DIAGNOSTICS_JSON) -> Dict[str, Any]:
    validation, scored, run_summary = load_inputs()
    diagnostics = build_diagnostics(validation, scored, run_summary)
    write_diagnostics(diagnostics, output_path)
    return diagnostics


def main() -> int:
    d = run()
    o = d["overall"]
    rc = d["root_causes"]
    print(f"[phase2h-a] go_no_go              : {d['go_no_go']}")
    print(f"[phase2h-a] safe_for_canary       : {d['safe_for_canary']}")
    print(f"[phase2h-a] reported rank_ic       : {o['reported_rank_ic']} "
          f"(floor {o['rank_ic_floor']})")
    print(f"[phase2h-a] recomputed rank_ic     : {o['recomputed_rank_ic']} "
          f"over {o['recomputed_n_dates']} dates")
    print(f"[phase2h-a] top_decile_hit_rate    : {o['top_decile_hit_rate']} "
          f"(floor {o['hit_rate_floor']})")
    print(f"[phase2h-a] bucket_monotonic       : {o['bucket_monotonic']}")
    print(f"[phase2h-a] n_dates / n_tickers     : {o['n_dates']} / "
          f"{o['n_tickers']}")
    print(f"[phase2h-a] failure_category       : {rc['failure_category']}")
    print(f"[phase2h-a] primary_drivers        : {rc['primary_drivers']}")
    print(f"[phase2h-a] recommended experiments: "
          f"{[e['experiment'] for e in d['recommended_experiments']]}")
    print(f"[phase2h-a] diagnostics written    : {DIAGNOSTICS_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
