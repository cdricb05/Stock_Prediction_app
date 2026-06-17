"""Phase 2I-A Feature IC & Horizon Sweep analyzer (offline, read-only, file-based).

For each raw point-in-time feature and each forward horizon this computes the
cross-sectional rank IC (per-date Spearman, rank-then-Pearson; no scipy), its
information ratio, yearly / quarterly stability, the top-minus-bottom quintile
spread, quintile monotonicity, the top-decile hit rate, the feature direction
(bullish / bearish), and a KEEP / DROP recommendation. KEEP survivors are then
ranked by IC information ratio ("both, ranked"). The 63d horizon is swept with a
sample guard rather than dropped silently.

This phase is research only: it reads the Phase 2G-C real-data artifacts and
writes exactly one diagnostics JSON. It performs no infrastructure actions and
mutates no datastore. The machine-readable safety flags are emitted in the
diagnostics JSON, and the full guardrail rationale lives in
docs/phase2i_feature_ic_horizon_sweep_v1.md, not in this source.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import time
from typing import Any, Dict, List, Optional

import pandas as pd

PHASE = "2I-A"

_OUTPUT_DIR = os.path.join("research", "output")

# Inputs (read-only).
SCORED_CSV = os.path.join(_OUTPUT_DIR, "phase2g_real_data_scored.csv")
PRICE_HISTORY_CSV = os.path.join(_OUTPUT_DIR, "phase2g_price_history_real.csv")
RUN_SUMMARY_JSON = os.path.join(_OUTPUT_DIR, "phase2g_c_real_data_run_summary.json")

# The single output this analyzer is allowed to write.
DIAGNOSTICS_JSON = os.path.join(_OUTPUT_DIR, "phase2i_feature_ic_horizon_sweep.json")

# The 13 raw point-in-time features under test (all present in the scored CSV).
FEATURES: List[str] = [
    "return_5d",
    "return_10d",
    "return_21d",
    "return_63d",
    "momentum_12_1",
    "realized_vol_21d",
    "realized_vol_63d",
    "downside_vol_21d",
    "excess_return_vs_spy_21d",
    "excess_return_vs_spy_63d",
    "rolling_beta_63d",
    "rolling_corr_spy_63d",
    "volume_zscore_21d",
]

# Forward horizons (sessions) swept end-to-end. 63d is kept with a sample guard.
HORIZONS: List[int] = [5, 10, 21, 63]

# The 5-session forward excess label is taken directly from the scored CSV
# (its point-in-time realized label); only 10/21/63 are derived from price
# history. A price-derived 5-session excess is also computed solely to
# cross-check the scored 5d column.
SCORED_5D_EXCESS_COL = "realized_excess_return_5d_vs_spy"
PRICE_LABEL_HORIZONS: List[int] = [10, 21, 63]

# Decision thresholds.
RANK_IC_FLOOR = 0.03        # |mean per-date rank IC| KEEP floor (at best horizon)
YEAR_SIGN_MIN = 3           # IC sign must match the overall sign in >= this many years
MIN_DATES = 120             # per (feature, horizon) low-confidence guard
MIN_NAMES_PER_DATE = 5      # min cross-section to compute a date's rank IC / quintiles
MIN_NAMES_DECILE = 10       # min cross-section to compute a date's decile / spread

DATE_COL = "as_of_date"
TICKER_COL = "ticker"
SPY = "SPY"


# --------------------------------------------------------------------------- #
# Loading (read-only)
# --------------------------------------------------------------------------- #
def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_inputs():
    scored = pd.read_csv(SCORED_CSV, parse_dates=[DATE_COL])
    prices = pd.read_csv(PRICE_HISTORY_CSV, parse_dates=["date"])
    run_summary = _read_json(RUN_SUMMARY_JSON)
    return scored, prices, run_summary


def build_forward_labels(prices: "pd.DataFrame") -> "pd.DataFrame":
    """Per (ticker, date) forward h-session excess-vs-SPY return.

    Only the 10/21/63-session labels are derived here from price history; the
    official 5-session label comes from the scored CSV column in
    :func:`build_panel`. A price-derived 5-session excess (``px_excess_5d``) is
    also returned and used solely to cross-check that scored 5d label.

    Forward return is adj_close shifted -h within each ticker; rows whose forward
    window runs past the data end become NaN and are dropped downstream. SPY's
    forward return over the same horizon is subtracted to form the excess label.
    """
    horizons = PRICE_LABEL_HORIZONS + [5]  # 5 = price-derived cross-check only
    px = prices.sort_values([TICKER_COL, "date"]).copy()
    for h in horizons:
        px[f"_fwd_{h}"] = (
            px.groupby(TICKER_COL)["adj_close"].shift(-h) / px["adj_close"] - 1.0)

    spy_cols = {f"_fwd_{h}": f"_spy_fwd_{h}" for h in horizons}
    spy = (px[px[TICKER_COL] == SPY][["date"] + list(spy_cols)]
           .rename(columns=spy_cols))

    names = px[px[TICKER_COL] != SPY].merge(spy, on="date", how="left")
    for h in PRICE_LABEL_HORIZONS:
        names[f"excess_{h}d"] = names[f"_fwd_{h}"] - names[f"_spy_fwd_{h}"]
    names["px_excess_5d"] = names["_fwd_5"] - names["_spy_fwd_5"]

    keep = ([TICKER_COL, "date"]
            + [f"excess_{h}d" for h in PRICE_LABEL_HORIZONS]
            + ["px_excess_5d"])
    return names[keep]


def build_panel(scored: "pd.DataFrame", labels: "pd.DataFrame") -> "pd.DataFrame":
    """Point-in-time features joined to multi-horizon forward excess labels.

    The 5-session label is the scored CSV's ``realized_excess_return_5d_vs_spy``
    (renamed ``excess_5d``); 10/21/63 come from the price-history labels. The
    price-derived ``px_excess_5d`` rides along for the 5d cross-check only.
    """
    feat = (scored[[TICKER_COL, DATE_COL] + FEATURES + [SCORED_5D_EXCESS_COL]]
            .copy()
            .rename(columns={SCORED_5D_EXCESS_COL: "excess_5d"}))
    panel = feat.merge(labels, left_on=[TICKER_COL, DATE_COL],
                       right_on=[TICKER_COL, "date"], how="inner")
    return panel


# --------------------------------------------------------------------------- #
# Metric helpers (pure)
# --------------------------------------------------------------------------- #
def _round(x: Optional[float], n: int = 6) -> Optional[float]:
    if x is None:
        return None
    try:
        if pd.isna(x) or (isinstance(x, float) and math.isinf(x)):
            return None
    except (TypeError, ValueError):
        pass
    return round(float(x), n)


def _sign(x: Optional[float]) -> int:
    if x is None or pd.isna(x) or x == 0:
        return 0
    return 1 if x > 0 else -1


def _cell_frame(panel: "pd.DataFrame", fcol: str, lcol: str) -> "pd.DataFrame":
    """The single per (feature, horizon) working frame: date + feature + label,
    dropna'd once. Every metric below is derived from this one frame so the
    expensive per-date pandas work happens a single time per cell instead of
    once per metric."""
    return panel[[DATE_COL, fcol, lcol]].dropna().copy()


def _per_date_rank_ic(sub: "pd.DataFrame", fcol: str, lcol: str) -> "pd.Series":
    """Cross-sectional Spearman(feature, forward excess) per as_of_date.

    Spearman is Pearson on within-date ranks (no scipy). Computed for every date
    in one vectorized pass: rank within date, then form each date's Pearson
    correlation from grouped sums (the ddof cancels in the ratio, so this equals
    ``ranks.corr(ranks)`` per date). Dates with fewer than ``MIN_NAMES_PER_DATE``
    names, or with zero rank variance, yield no IC.
    """
    if sub.empty:
        return pd.Series(dtype=float)
    size = sub.groupby(DATE_COL)[fcol].transform("size")
    s = sub[size >= MIN_NAMES_PER_DATE]
    if s.empty:
        return pd.Series(dtype=float)
    gs = s.groupby(DATE_COL)
    tmp = pd.DataFrame({"d": s[DATE_COL].to_numpy()})
    tmp["fr"] = gs[fcol].rank().to_numpy()
    tmp["lr"] = gs[lcol].rank().to_numpy()
    tmp["frlr"] = tmp["fr"] * tmp["lr"]
    tmp["frfr"] = tmp["fr"] * tmp["fr"]
    tmp["lrlr"] = tmp["lr"] * tmp["lr"]
    agg = tmp.groupby("d").agg(
        n=("fr", "size"), sf=("fr", "sum"), sl=("lr", "sum"),
        sfl=("frlr", "sum"), sff=("frfr", "sum"), sll=("lrlr", "sum"))
    cov = agg["sfl"] - agg["sf"] * agg["sl"] / agg["n"]
    var_f = agg["sff"] - agg["sf"] ** 2 / agg["n"]
    var_l = agg["sll"] - agg["sl"] ** 2 / agg["n"]
    denom = (var_f * var_l) ** 0.5
    ics = (cov / denom).where(denom > 0).dropna()
    ics.index = pd.to_datetime(ics.index)
    return ics


def _calendar_ic(ics: "pd.Series", how: str) -> List[Dict[str, Any]]:
    if not len(ics):
        return []
    if how == "year":
        keys = ics.index.year.astype(str)
    else:
        keys = (ics.index.year.astype(str) + "-Q"
                + ics.index.quarter.astype(str))
    out: List[Dict[str, Any]] = []
    for k, grp in ics.groupby(keys):
        out.append({"period": str(k), "rank_ic": _round(grp.mean()),
                    "n_dates": int(len(grp))})
    out.sort(key=lambda r: r["period"])
    return out


def _quintile_monotonic(sub: "pd.DataFrame", fcol: str, lcol: str
                        ) -> Optional[bool]:
    """Pooled: are mean forward excess returns monotone across feature quintiles?"""
    if len(sub) < 5 * MIN_NAMES_PER_DATE or sub[fcol].nunique() < 5:
        return None
    q = pd.qcut(sub[fcol].rank(method="first"), 5, labels=False)
    means = sub.groupby(q)[lcol].mean().sort_index()
    return bool(means.is_monotonic_increasing or means.is_monotonic_decreasing)


def _spread_and_hit_rate(sub: "pd.DataFrame", fcol: str, lcol: str):
    """Top-minus-bottom quintile spread and top-decile hit rate in one pass.

    Both are per-date statistics averaged across dates. Per-date feature ranks /
    quintile buckets are formed once with grouped vectorized ops (no nested
    per-date Python loop, no repeated dropna): the bottom (0) and top (4) quintile
    buckets feed the spread, and the per-date 90th-percentile cutoff feeds the
    hit rate.
    """
    if sub.empty:
        return None, None
    g = sub.groupby(DATE_COL)[fcol]
    size = g.transform("size")
    nuniq = g.transform("nunique")

    # Top-minus-bottom quintile spread. Dates need >= MIN_NAMES_DECILE names and
    # >= 5 distinct feature values; rank(method="first") over 1..n maps to five
    # equal-frequency buckets via the 0.2/0.4/0.6/0.8 fractional rank cutoffs
    # (identical to pd.qcut on the distinct ranks).
    elig = sub[(size >= MIN_NAMES_DECILE) & (nuniq >= 5)]
    spread = None
    if not elig.empty:
        ge = elig.groupby(DATE_COL)[fcol]
        rank_first = ge.rank(method="first")
        ne = ge.transform("size")
        bucket = ((rank_first > 1 + 0.2 * (ne - 1)).astype(int)
                  + (rank_first > 1 + 0.4 * (ne - 1)).astype(int)
                  + (rank_first > 1 + 0.6 * (ne - 1)).astype(int)
                  + (rank_first > 1 + 0.8 * (ne - 1)).astype(int))
        elig = elig.assign(_bucket=bucket)
        top_q = elig[elig["_bucket"] == 4].groupby(DATE_COL)[lcol].mean()
        bot_q = elig[elig["_bucket"] == 0].groupby(DATE_COL)[lcol].mean()
        per_date = (top_q - bot_q).dropna()
        spread = float(per_date.mean()) if len(per_date) else None

    # Top-decile hit rate: per-date share of positive forward excess among names
    # at/above the date's 90th-percentile feature value, averaged across dates.
    dec = sub[size >= MIN_NAMES_DECILE]
    hit_rate = None
    if not dec.empty:
        cutoffs = dec.groupby(DATE_COL)[fcol].quantile(0.9)
        cut = dec[DATE_COL].map(cutoffs)
        top = dec[dec[fcol] >= cut]
        if not top.empty:
            pos = (top[lcol] > 0).astype(float)
            rate_by_date = pos.groupby(top[DATE_COL]).mean()
            hit_rate = float(rate_by_date.mean()) if len(rate_by_date) else None

    return spread, hit_rate


# --------------------------------------------------------------------------- #
# Per (feature, horizon) cell + per-feature roll-up
# --------------------------------------------------------------------------- #
def horizon_cell(panel: "pd.DataFrame", fcol: str, h: int) -> Dict[str, Any]:
    lcol = f"excess_{h}d"
    sub = _cell_frame(panel, fcol, lcol)
    n_rows = int(len(sub))

    ics = _per_date_rank_ic(sub, fcol, lcol)
    n_dates = int(len(ics))
    mean_ic = float(ics.mean()) if n_dates else None
    std_ic = float(ics.std(ddof=1)) if n_dates > 1 else None
    info_ratio = (mean_ic / std_ic
                  if (mean_ic is not None and std_ic not in (None, 0.0)
                      and not pd.isna(std_ic))
                  else None)

    yearly = _calendar_ic(ics, "year")
    quarterly = _calendar_ic(ics, "quarter")

    overall_sign = _sign(mean_ic)
    years_present = len(yearly)
    years_matching = sum(1 for y in yearly
                         if _sign(y["rank_ic"]) == overall_sign and overall_sign)

    spread, hit_rate = _spread_and_hit_rate(sub, fcol, lcol)
    monotonic = _quintile_monotonic(sub, fcol, lcol)
    low_confidence = n_dates < MIN_DATES

    return {
        "horizon_days": h,
        "mean_rank_ic": _round(mean_ic),
        "std_rank_ic": _round(std_ic),
        "information_ratio": _round(info_ratio),
        "n_dates": n_dates,
        "n_rows": n_rows,
        "low_confidence": bool(low_confidence),
        "direction": ("bullish" if overall_sign > 0
                      else "bearish" if overall_sign < 0 else "flat"),
        "top_minus_bottom_spread": _round(spread),
        "quintile_monotonic": monotonic,
        "top_decile_hit_rate": _round(hit_rate),
        "years_present": years_present,
        "years_matching_sign": years_matching,
        "year_sign_consistent": bool(years_matching >= YEAR_SIGN_MIN),
        "yearly_rank_ic": yearly,
        "quarterly_rank_ic": quarterly,
    }


def feature_block(panel: "pd.DataFrame", fcol: str) -> Dict[str, Any]:
    cells = {str(h): horizon_cell(panel, fcol, h) for h in HORIZONS}

    # Best horizon = max |mean rank IC| among confident cells; fall back to all
    # cells (flagged) when every horizon is below the sample guard.
    def _abs_ic(c: Dict[str, Any]) -> float:
        v = c["mean_rank_ic"]
        return abs(v) if v is not None else -1.0

    confident = [c for c in cells.values()
                 if not c["low_confidence"] and c["mean_rank_ic"] is not None]
    pool = confident or [c for c in cells.values() if c["mean_rank_ic"] is not None]
    best = max(pool, key=_abs_ic) if pool else None

    magnitude_pass = bool(best and abs(best["mean_rank_ic"]) >= RANK_IC_FLOOR)
    stability_pass = bool(best and best["year_sign_consistent"])
    sample_pass = bool(best and not best["low_confidence"])
    keep = bool(magnitude_pass and stability_pass and sample_pass)

    return {
        "best_horizon_days": best["horizon_days"] if best else None,
        "best_mean_rank_ic": best["mean_rank_ic"] if best else None,
        "best_information_ratio": best["information_ratio"] if best else None,
        "direction": best["direction"] if best else "flat",
        "keep": keep,
        "keep_criteria": {
            "magnitude_pass": magnitude_pass,
            "stability_pass": stability_pass,
            "sample_pass": sample_pass,
            "rank_ic_floor": RANK_IC_FLOOR,
            "year_sign_min": YEAR_SIGN_MIN,
        },
        "horizons": cells,
    }


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_diagnostics(scored: "pd.DataFrame", prices: "pd.DataFrame",
                      run_summary: Dict[str, Any]) -> Dict[str, Any]:
    labels = build_forward_labels(prices)
    panel = build_panel(scored, labels)

    # Cross-check the official scored 5d label against a price-derived 5d excess.
    cc = panel[["excess_5d", "px_excess_5d"]].dropna()
    cc_diff = (cc["excess_5d"] - cc["px_excess_5d"]).abs() if len(cc) else None
    cc_corr = float(cc["excess_5d"].corr(cc["px_excess_5d"])) if len(cc) > 1 else None
    cc_mad = float(cc_diff.mean()) if cc_diff is not None and len(cc) else None
    cc_max = float(cc_diff.max()) if cc_diff is not None and len(cc) else None
    label_5d_cross_check = {
        "official_5d_label_source": SCORED_5D_EXCESS_COL,
        "n_rows_compared": int(len(cc)),
        "pearson_corr_vs_price_derived_5d": _round(cc_corr),
        "mean_abs_diff_vs_price_derived_5d": _round(cc_mad),
    }

    # Audit-clarity top-level fields: explicit per-horizon label provenance and
    # the structured 5d scored-vs-price-derived cross-check.
    label_sources = {
        "5": "scored_csv.realized_excess_return_5d_vs_spy",
        "10": "price_history.forward_excess_return_vs_spy",
        "21": "price_history.forward_excess_return_vs_spy",
        "63": "price_history.forward_excess_return_vs_spy",
    }
    label_cross_checks = {
        "5d_scored_vs_price_derived": {
            "correlation": _round(cc_corr),
            "n": int(len(cc)),
            "mean_abs_diff": _round(cc_mad),
            "max_abs_diff": _round(cc_max),
        },
    }

    features = {f: feature_block(panel, f) for f in FEATURES}

    keep_ranked = sorted(
        ({"feature": f,
          "best_horizon_days": b["best_horizon_days"],
          "mean_rank_ic": b["best_mean_rank_ic"],
          "information_ratio": b["best_information_ratio"],
          "direction": b["direction"]}
         for f, b in features.items() if b["keep"]),
        key=lambda r: abs(r["information_ratio"]) if r["information_ratio"] is not None else -1.0,
        reverse=True)
    drop = sorted(f for f, b in features.items() if not b["keep"])

    panel_dates = pd.to_datetime(panel[DATE_COL])
    return {
        "phase": PHASE,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "scored_csv": SCORED_CSV,
            "price_history_csv": PRICE_HISTORY_CSV,
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
        "config": {
            "features": FEATURES,
            "horizons": HORIZONS,
            "rank_ic_floor": RANK_IC_FLOOR,
            "year_sign_min": YEAR_SIGN_MIN,
            "min_dates": MIN_DATES,
            "min_names_per_date": MIN_NAMES_PER_DATE,
            "forward_label_5d_source": SCORED_5D_EXCESS_COL,
            "forward_label_10_21_63_source": "phase2g_price_history_real.csv",
            "keep_rule": "KEEP if |mean per-date rank IC| at best horizon >= "
                         "rank_ic_floor AND IC sign matches the overall sign in "
                         ">= year_sign_min years AND best horizon is not "
                         "low_confidence; survivors ranked by |information ratio|.",
        },
        "label_sources": label_sources,
        "label_cross_checks": label_cross_checks,
        "label_5d_cross_check": label_5d_cross_check,
        "provenance": {
            "source": run_summary.get("source"),
            "date_start": run_summary.get("date_start"),
            "date_end": run_summary.get("date_end"),
            "price_row_count": run_summary.get("row_count"),
            "ticker_count": run_summary.get("ticker_count"),
        },
        "panel": {
            "n_rows": int(len(panel)),
            "n_tickers": int(panel[TICKER_COL].nunique()),
            "n_dates": int(panel[DATE_COL].nunique()),
            "date_start": panel_dates.min().date().isoformat() if len(panel) else None,
            "date_end": panel_dates.max().date().isoformat() if len(panel) else None,
        },
        "features": features,
        "keep_drop": {
            "keep_ranked": keep_ranked,
            "drop": drop,
            "n_keep": len(keep_ranked),
            "n_drop": len(drop),
        },
    }


def write_diagnostics(diagnostics: Dict[str, Any],
                      path: str = DIAGNOSTICS_JSON) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(diagnostics, f, indent=2, allow_nan=False)


def run(output_path: str = DIAGNOSTICS_JSON) -> Dict[str, Any]:
    scored, prices, run_summary = load_inputs()
    diagnostics = build_diagnostics(scored, prices, run_summary)
    write_diagnostics(diagnostics, output_path)
    return diagnostics


def main() -> int:
    t0 = time.perf_counter()
    d = run()
    elapsed = time.perf_counter() - t0
    p = d["panel"]
    kd = d["keep_drop"]
    print(f"[phase2i-a] panel rows / tickers / dates : {p['n_rows']} / "
          f"{p['n_tickers']} / {p['n_dates']}")
    print(f"[phase2i-a] window                       : {p['date_start']} -> "
          f"{p['date_end']}")
    print(f"[phase2i-a] horizons swept               : {d['config']['horizons']}")
    print(f"[phase2i-a] KEEP ({kd['n_keep']}), ranked by |IR| :")
    for r in kd["keep_ranked"]:
        print(f"             {r['feature']:<28} h={r['best_horizon_days']:>2}d  "
              f"IC={r['mean_rank_ic']}  IR={r['information_ratio']}  "
              f"({r['direction']})")
    print(f"[phase2i-a] DROP ({kd['n_drop']})                  : {kd['drop']}")
    # Low-confidence (sample-guarded) cells, e.g. 63d near the data tail.
    low = [(f, h) for f, b in d["features"].items()
           for h, c in b["horizons"].items() if c["low_confidence"]]
    print(f"[phase2i-a] low_confidence cells         : {len(low)}")
    cc = d["label_5d_cross_check"]
    print(f"[phase2i-a] 5d label source              : "
          f"{cc['official_5d_label_source']} "
          f"(corr vs price-derived {cc['pearson_corr_vs_price_derived_5d']}, "
          f"n={cc['n_rows_compared']})")
    print(f"[phase2i-a] diagnostics written          : {DIAGNOSTICS_JSON}")
    print(f"[phase2i-a] elapsed seconds              : {elapsed:.2f}")
    print("[phase2i-a] research-only diagnostics; safety flags emitted in JSON.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
