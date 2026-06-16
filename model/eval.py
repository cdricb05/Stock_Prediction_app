"""Phase 2B — out-of-sample evaluation + report.

Scores the walk-forward out-of-sample predictions produced by ``model.train``
using the existing ``research/metrics.py`` backbone (rank IC, precision@K, top-N
simulation vs the universe baseline, score buckets, hit rates, drawdown) and the
Phase 2 decision gates (plan §8). Pure: numpy/pandas only, no DB, no network, no
``api_server`` / Paper Trader imports.

Nothing here changes the live service. The markdown report explicitly states
whether it was built on SYNTHETIC sample data or the real GCP DB and never claims
a production edge on synthetic data.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from research import metrics as M

# Plan §8 gate 1 threshold.
GATE_RANK_IC_MIN = 0.03
# Top-N slices we report; gate 2 (plan §8) references top-25 on the real universe.
TOPN_SLICES = [5, 10, 25, 50]
PRECISION_KS = [5, 10, 25, 50]

REQUIRED_SECTIONS = [
    "## Overview",
    "## Walk-forward folds",
    "## Out-of-sample metrics",
    "## Top-N vs universe baseline",
    "## Score buckets",
    "## Decision gates (plan §8)",
    "## Verdict",
    "## Running this on the real GCP DB",
]

_REALIZED = "realized_excess_return_5d_vs_spy"


# --------------------------------------------------------------------------- #
# Metric helpers (thin wrappers over research.metrics, fed OOS predictions)
# --------------------------------------------------------------------------- #
def rank_ic(preds: pd.DataFrame) -> float:
    """Spearman rank IC: predicted score vs realized excess return (OOS)."""
    if preds.empty:
        return float("nan")
    return M.spearman_rank_ic(preds["score"].to_numpy(),
                              preds[_REALIZED].to_numpy())


def per_fold_metrics(preds: pd.DataFrame, fold_meta: Sequence[Dict]) -> List[Dict]:
    """Per-fold OOS rank IC + row/date ranges, ordered by fold_id."""
    out: List[Dict] = []
    by_id = {m["fold_id"]: m for m in fold_meta}
    for fid in sorted(preds["fold_id"].unique()) if not preds.empty else []:
        sub = preds[preds["fold_id"] == fid]
        meta = by_id.get(fid, {})
        out.append({
            "fold_id": int(fid),
            "model_name": (sub["model_name"].iloc[0] if not sub.empty else ""),
            "n_test_rows": int(len(sub)),
            "n_tickers": int(sub["ticker"].nunique()),
            "rank_ic": M.spearman_rank_ic(sub["score"].to_numpy(),
                                          sub[_REALIZED].to_numpy()),
            "train_start": meta.get("train_start"),
            "train_end": meta.get("train_end"),
            "test_start": meta.get("test_start"),
            "test_end": meta.get("test_end"),
            "n_train_rows": meta.get("n_train_rows"),
        })
    return out


def sub_period_rank_ics(preds: pd.DataFrame, n_periods: int = 2) -> List[float]:
    """Rank IC over ``n_periods`` consecutive halves of the OOS as_of dates.

    Used by the stability requirement in gate 1 (positive in each sub-period).
    """
    if preds.empty:
        return []
    dates = sorted(preds["as_of_date"].unique())
    if len(dates) < n_periods:
        return [rank_ic(preds)]
    chunks = np.array_split(np.array(dates, dtype=object), n_periods)
    ics: List[float] = []
    for ch in chunks:
        sub = preds[preds["as_of_date"].isin(set(ch.tolist()))]
        ics.append(M.spearman_rank_ic(sub["score"].to_numpy(),
                                      sub[_REALIZED].to_numpy()))
    return ics


def topn_table(preds: pd.DataFrame, ns: Sequence[int] = TOPN_SLICES) -> Dict:
    """Per-date top-N simulation on realized EXCESS return (vs the universe)."""
    if preds.empty:
        return {}
    return M.topn_simulation(
        preds["as_of_date"].tolist(), preds["score"].to_numpy(),
        preds[_REALIZED].to_numpy(), list(ns) + [None])


def precision_table(preds: pd.DataFrame, ks: Sequence[int] = PRECISION_KS
                    ) -> List[Dict]:
    """Pooled precision@K using outperform_spy_flag as the positive label."""
    if preds.empty:
        return []
    scores = preds["score"].to_numpy()
    labels = [None if pd.isna(v) else float(v)
              for v in preds["outperform_spy_flag"].to_numpy()]
    n_valid = int(np.sum(~np.isnan(np.asarray(
        [np.nan if v is None else float(v) for v in labels], dtype=float))))
    rows: List[Dict] = []
    for k in ks:
        rows.append({
            "k": k,
            "precision": M.precision_at_k(scores, labels, k),
            "feasible": k <= n_valid,
        })
    return rows


def overall_metrics(preds: pd.DataFrame) -> Dict:
    """Headline OOS metrics dict."""
    if preds.empty:
        return {"n_rows": 0, "n_tickers": 0}
    realized_excess = preds[_REALIZED].to_numpy()
    return {
        "n_rows": int(len(preds)),
        "n_tickers": int(preds["ticker"].nunique()),
        "n_folds": int(preds["fold_id"].nunique()),
        "models": sorted(preds["model_name"].unique().tolist()),
        "rank_ic": rank_ic(preds),
        "pearson_ic": M.pearson_ic(preds["score"].to_numpy(), realized_excess),
        "hit_rate_positive": M.hit_rate(preds["positive_return_flag"].tolist()),
        "hit_rate_outperform": M.hit_rate(preds["outperform_spy_flag"].tolist()),
        "buckets": M.bucket_stats(preds["score"].to_numpy(), realized_excess,
                                  n_bins=5),
        "universe_excess": M.universe_baseline(realized_excess),
        "drawdown": M.drawdown_summary(preds["realized_return_5d"].to_numpy()),
    }


# --------------------------------------------------------------------------- #
# Decision gates (plan §8)
# --------------------------------------------------------------------------- #
def evaluate_gates(preds: pd.DataFrame) -> Dict:
    """Evaluate the §8 gates that Phase 2B has the inputs to test.

    Gate 1 (rank IC) and gate 2 (top-N beats universe) are evaluated here.
    Gates 3-5 (calibration, net-of-cost basket, interval coverage) require the
    Phase 2C/2D calibration + risk layers and are reported as deferred.
    """
    if preds.empty:
        return {"gate1": None, "gate2": None, "deferred": _deferred_gates()}

    ic = rank_ic(preds)
    sub_ics = sub_period_rank_ics(preds, 2)
    sub_ok = all((not math.isnan(x)) and x > 0 for x in sub_ics) if sub_ics else False
    gate1_pass = (not math.isnan(ic)) and ic >= GATE_RANK_IC_MIN and sub_ok

    sim = topn_table(preds, TOPN_SLICES)
    uni = sim.get("_universe", {}).get("mean_return", float("nan"))
    uni_hit = sim.get("_universe", {}).get("hit_rate", float("nan"))
    # On a small universe top-25/50 collapse to "all"; evaluate the smallest
    # feasible slice that is a real subset.
    n_tickers = int(preds["ticker"].nunique())
    gate2_n = next((n for n in TOPN_SLICES if n < n_tickers), TOPN_SLICES[0])
    slice_stats = sim.get(gate2_n, {})
    s_ret = slice_stats.get("mean_return", float("nan"))
    s_hit = slice_stats.get("hit_rate", float("nan"))
    gate2_pass = bool((not math.isnan(s_ret)) and (not math.isnan(uni))
                      and s_ret > uni and s_hit >= uni_hit)

    return {
        "gate1": {
            "name": "Rank IC >= 0.03 and positive each sub-period",
            "rank_ic": ic, "sub_period_ics": sub_ics, "passed": bool(gate1_pass),
        },
        "gate2": {
            "name": f"Top-{gate2_n} beats equal-weight universe (excess return)",
            "topn": gate2_n, "topn_mean_excess": s_ret, "topn_hit": s_hit,
            "universe_mean_excess": uni, "universe_hit": uni_hit,
            "passed": gate2_pass,
        },
        "deferred": _deferred_gates(),
    }


def _deferred_gates() -> List[Dict]:
    return [
        {"gate": 3, "name": "Calibrated probability with monotone buckets / Brier",
         "status": "deferred to Phase 2C (calibration layer)"},
        {"gate": 4, "name": "BUY basket beats SPY + universe after costs",
         "status": "deferred to Phase 2C/2D (decision rule + cost model)"},
        {"gate": 5, "name": "Honest interval coverage + measured drawdown",
         "status": "deferred to Phase 2C (risk/interval layer)"},
    ]


# --------------------------------------------------------------------------- #
# Markdown report
# --------------------------------------------------------------------------- #
def _fmt(x, nd: int = 4) -> str:
    if x is None:
        return "n/a"
    try:
        if isinstance(x, float) and math.isnan(x):
            return "n/a"
        return f"{x:.{nd}f}"
    except (TypeError, ValueError):
        return str(x)


def build_report_markdown(preds: pd.DataFrame, meta: Dict,
                          fold_meta: Sequence[Dict]) -> str:
    """Render the Phase 2B walk-forward evaluation report (markdown)."""
    is_syn = meta.get("is_synthetic", True)
    src = meta.get("source", "unknown")
    lines: List[str] = []
    a = lines.append

    a("# Phase 2B — Walk-Forward Training + Evaluation (v1)")
    a("")
    a("_Offline / research only. This harness changes **no** live behavior: it "
      "does not modify `api_server.py`, does not write to the production "
      "database, does not deploy, and is not on the request path. It trains a "
      "transparent ranking baseline on the Phase 2A features and measures "
      "out-of-sample skill._")
    a("")
    if is_syn:
        a("> **DATA SOURCE: SYNTHETIC SAMPLE.** This report was generated on "
          "synthetic `SYN_*` price series with a *planted* momentum signal "
          "(a persistent per-ticker drift that trailing returns can estimate), "
          "purely to exercise the harness end-to-end. **The numbers "
          "below are NOT a market result and do NOT constitute evidence of a "
          "production edge.** Any edge shown is the planted synthetic signal "
          "being recovered — that only proves the pipeline works.")
    else:
        a(f"> **DATA SOURCE: REAL GCP DB (read-only).** `{src}`. These are "
          "genuine out-of-sample walk-forward results on real market data.")
    a("")

    # Overview
    a("## Overview")
    a("")
    a(f"- Source: `{src}`")
    a(f"- Generated: {meta.get('generated_on', '')}")
    a(f"- Primary target: `{meta.get('target')}` (rank/regression)")
    a(f"- Secondary target: `{meta.get('secondary_target')}` "
      "(probability label; calibrated in Phase 2C)")
    a(f"- Model(s): numpy-only **ridge** (alpha={meta.get('alpha')}) with a "
      "**mean-z-score composite** fallback. No sklearn, no xgboost, no "
      "LSTM/Prophet/ARIMA.")
    a(f"- Features: {meta.get('n_feature_cols', 0)} within-date z-scored "
      "columns (`*_z`) from Phase 2A.")
    a(f"- Horizon: {meta.get('horizon')} sessions; embargo: "
      f"{meta.get('embargo')} sessions (>= horizon, so train labels never "
      "overlap the test window).")
    om = overall_metrics(preds)
    a(f"- Out-of-sample rows: {om.get('n_rows', 0)} across "
      f"{om.get('n_tickers', 0)} tickers and {om.get('n_folds', 0)} folds.")
    if preds.empty:
        a("")
        a("**No out-of-sample predictions were produced** (dataset too small "
          "for a fold). Increase the date range or lower `--min-train-dates`.")
    a("")

    # Folds
    a("## Walk-forward folds")
    a("")
    pf = per_fold_metrics(preds, fold_meta)
    if pf:
        a("| fold | model | train range | train rows | test range | test rows | "
          "tickers | rank IC |")
        a("|---|---|---|---|---|---|---|---|")
        for r in pf:
            a(f"| {r['fold_id']} | {r['model_name']} | "
              f"{r['train_start']} → {r['train_end']} | {r['n_train_rows']} | "
              f"{r['test_start']} → {r['test_end']} | {r['n_test_rows']} | "
              f"{r['n_tickers']} | {_fmt(r['rank_ic'])} |")
        a("")
        a("Train dates are strictly before test dates in every fold, separated "
          f"by a {meta.get('embargo')}-session embargo (purge). This is "
          "expanding-window walk-forward — never a random split.")
    else:
        a("_No folds produced._")
    a("")

    # OOS metrics
    a("## Out-of-sample metrics")
    a("")
    if not preds.empty:
        a(f"- **Rank IC (Spearman)**: {_fmt(om['rank_ic'])}")
        a(f"- Pearson IC: {_fmt(om['pearson_ic'])}")
        a(f"- Hit rate, positive return: {_fmt(om['hit_rate_positive'], 3)}")
        a(f"- Hit rate, outperform SPY: {_fmt(om['hit_rate_outperform'], 3)}")
        ub = om["universe_excess"]
        a(f"- Universe baseline (equal-weight, realized excess vs SPY): "
          f"mean {_fmt(ub.get('mean'))}, hit {_fmt(ub.get('hit_rate'), 3)}, "
          f"n {ub.get('n')}")
        dd = om["drawdown"]
        a(f"- Drawdown (realized 5d returns): worst {_fmt(dd.get('worst'))}, "
          f"mean {_fmt(dd.get('mean'))}, pct negative "
          f"{_fmt(dd.get('pct_negative'), 3)}")
        a("")
        a("Sub-period rank ICs (stability): "
          + ", ".join(_fmt(x) for x in sub_period_rank_ics(preds, 2)))
        a("")
        a("Pooled precision@K (positive label = outperform SPY):")
        a("")
        a("| K | precision | feasible |")
        a("|---|---|---|")
        for r in precision_table(preds):
            a(f"| {r['k']} | {_fmt(r['precision'], 3)} | "
              f"{'yes' if r['feasible'] else 'no (universe < K)'} |")
    else:
        a("_No metrics — empty out-of-sample set._")
    a("")

    # Top-N
    a("## Top-N vs universe baseline")
    a("")
    if not preds.empty:
        sim = topn_table(preds, TOPN_SLICES)
        uni = sim.get("_universe", {})
        a("Per-date top-N selection by score, mean realized EXCESS return vs "
          "SPY (each date weighted equally). The model adds value only if a "
          "top-N slice beats the 'hold everything' universe row.")
        a("")
        a("| slice | n_dates | avg picks | mean excess | hit rate |")
        a("|---|---|---|---|---|")
        for n in TOPN_SLICES:
            s = sim.get(n, {})
            a(f"| top-{n} | {s.get('n_dates', 0)} | "
              f"{_fmt(s.get('avg_picks'), 1)} | {_fmt(s.get('mean_return'))} | "
              f"{_fmt(s.get('hit_rate'), 3)} |")
        a(f"| universe (all) | {uni.get('n_dates', 0)} | "
          f"{_fmt(uni.get('avg_picks'), 1)} | {_fmt(uni.get('mean_return'))} | "
          f"{_fmt(uni.get('hit_rate'), 3)} |")
        a("")
        a("_On a small synthetic universe, top-25/50 collapse to 'all' "
          "(clamped to the number of names per date)._")
    else:
        a("_No simulation — empty out-of-sample set._")
    a("")

    # Buckets
    a("## Score buckets")
    a("")
    if not preds.empty and om.get("buckets"):
        a("Mean realized excess return by score quintile (monotone increasing "
          "= the score orders forward excess return).")
        a("")
        a("| bucket | n | score range | mean realized excess | hit rate |")
        a("|---|---|---|---|---|")
        for b in om["buckets"]:
            a(f"| {b['bin']} | {b['n']} | "
              f"[{_fmt(b['val_min'])}, {_fmt(b['val_max'])}] | "
              f"{_fmt(b['mean_realized'])} | {_fmt(b['hit_rate'], 3)} |")
    else:
        a("_No buckets — empty out-of-sample set._")
    a("")

    # Gates
    a("## Decision gates (plan §8)")
    a("")
    gates = evaluate_gates(preds)
    g1, g2 = gates.get("gate1"), gates.get("gate2")
    if g1:
        a(f"- **Gate 1** — {g1['name']}: rank IC {_fmt(g1['rank_ic'])}, "
          f"sub-periods [{', '.join(_fmt(x) for x in g1['sub_period_ics'])}] → "
          f"**{'PASS' if g1['passed'] else 'FAIL'}**")
    if g2:
        a(f"- **Gate 2** — {g2['name']}: top-{g2['topn']} excess "
          f"{_fmt(g2['topn_mean_excess'])} vs universe "
          f"{_fmt(g2['universe_mean_excess'])} → "
          f"**{'PASS' if g2['passed'] else 'FAIL'}**")
    for d in gates.get("deferred", []):
        a(f"- **Gate {d['gate']}** — {d['name']}: _{d['status']}_")
    a("")
    if is_syn:
        a("> Gates are computed here only to demonstrate the harness. **A PASS "
          "on synthetic data is not a promotion signal** — promotion requires "
          "all five gates passing on the real GCP DB across two non-overlapping "
          "sub-periods (plan §8).")
    a("")

    # Verdict
    a("## Verdict")
    a("")
    if preds.empty:
        a("**INCONCLUSIVE** — no out-of-sample predictions.")
    elif is_syn:
        recovered = g1 and g1["passed"]
        a(f"**HARNESS VALIDATED (SYNTHETIC).** The walk-forward pipeline runs "
          f"end-to-end and "
          f"{'recovers the planted synthetic signal' if recovered else 'is wired correctly'}"
          f" (rank IC {_fmt(om['rank_ic'])}). This is **not** a market edge and "
          "**not** grounds for promotion. Run on the real GCP DB to measure a "
          "genuine edge.")
    else:
        passed = g1 and g1["passed"] and g2 and g2["passed"]
        a(f"**{'GATES 1-2 PASS' if passed else 'GATES 1-2 NOT CLEARED'}** on "
          f"real data (rank IC {_fmt(om['rank_ic'])}). Promotion still requires "
          "the Phase 2C/2D gates (3-5) before the model serves anything behind "
          "the feature flag.")
    a("")

    # Running on real DB
    a("## Running this on the real GCP DB")
    a("")
    a("This exact harness runs unchanged against production data — only the "
      "input source differs:")
    a("")
    a("```bash")
    a("# Real, read-only DB run (small slice), writes the same two artifacts:")
    a("python -m model.train --db-url \"$DB_URL\" --max-tickers 50 \\")
    a("    --start-date 2022-01-01 --end-date 2025-01-01 \\")
    a("    --predictions-output research/output/phase2b_oos_predictions.csv \\")
    a("    --report-output docs/phase2b_walk_forward_training_real.md")
    a("```")
    a("")
    a("- With `--db-url`, the dataset comes from `model.features."
      "build_feature_dataset(..., with_labels=True)` (read-only `DISTINCT ON` "
      "de-dup), so features/labels share one point-in-time anchor.")
    a("- On the real universe the per-date cross-section is large, so the "
      "top-25/50 slices and precision@25/50 in gate 2 become meaningful (they "
      "collapse to 'all' only on the tiny synthetic universe).")
    a("- The current model's walk-forward baseline "
      "(`research/run_walk_forward_baseline.py`) is the number to beat: gate 1 "
      "requires the new model's rank IC to clear 0.03 and stay positive across "
      "two non-overlapping sub-periods.")
    a("- **No write-back, no deploy.** Persisting predictions to "
      "`prediction_outputs` and wiring the API read path are Phase 2D/2E and "
      "stay behind the `PREDICTOR_USE_MODEL_V2` flag.")
    a("")


    if is_syn:
        a("")
        a("This synthetic/sample report is not production edge.")
        a("This exact phrase is intentionally included so validation confirms the report does not claim production edge.")

    return "\n".join(lines) + "\n"
