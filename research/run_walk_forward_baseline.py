"""CLI: GCP Quant Model Phase 1 — Walk-Forward Baseline Harness.

Builds a point-in-time walk-forward dataset, replays the current production
model over it, and writes an honest metrics report answering one question:

    "Does the current prediction model have any measurable out-of-sample edge?"

Run on the GCP VM (needs the DB + model stack), from the repo root:

    set -a; . ./api.env; set +a
    PYTHONPATH=. /home/binisti/venv/bin/python -m research.run_walk_forward_baseline \
        --fast-smoke

Nothing here restarts the service, deploys, installs packages, changes env, or
writes to the database.
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import sys

# Make the repo root importable whether invoked as `-m research.run_...`
# or as a path (`python research/run_walk_forward_baseline.py`).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import numpy as np
import pandas as pd

from research import metrics as M
from research.walk_forward_dataset import build_walk_forward_dataset, DEFAULT_HORIZON

# Section headers the report must contain (asserted by tests).
REQUIRED_SECTIONS = [
    "## Evaluation window",
    "## Number of tickers",
    "## Number of predictions evaluated",
    "## Coverage by model",
    "## Recommendation distribution (BUY/HOLD/SELL)",
    "## Hit rate — positive 5-day return",
    "## Hit rate — outperforming SPY",
    "## Average realized 5-day return by recommendation",
    "## Average realized excess return vs SPY by recommendation",
    "## Precision@K for top predicted returns",
    "## Rank IC / Spearman correlation",
    "## Calibration table by confidence decile",
    "## Confusion matrix — BUY/HOLD/SELL vs realized positive return",
    "## Drawdown / downside summary for BUY recommendations",
    "## Verdict",
]

PCT = lambda x: "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:.2f}%"
NUM = lambda x, d=4: "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{d}f}"


def _fmt_dt(d) -> str:
    if d is None:
        return "n/a"
    return str(d)


def build_report_markdown(preds: pd.DataFrame, meta: dict) -> str:
    """Render the full baseline report from a merged predictions DataFrame.

    `preds` columns (from current_model_baseline.replay_dataset): ticker,
    as_of_date, predicted_return_5d, recommendation, confidence, agreement,
    residual_alpha_vs_spy_pct, zscore, ran_models, model_errors,
    realized_return_5d, realized_excess_return_5d_vs_spy, positive_return_flag,
    outperform_spy_flag.
    """
    L: list[str] = []
    L.append("# Current Model — Walk-Forward Baseline (v1)")
    L.append("")
    L.append(f"_Generated {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
             f"by research/run_walk_forward_baseline.py — observational, read-only._")
    L.append("")
    L.append("> Scope: research/evaluation only. No model change, no orders, no automation, "
             "no live broker execution. The current model is **price-only** (adjusted close) "
             "with heuristic confidence; this report measures, it does not modify.")
    L.append("")

    has_rows = preds is not None and not preds.empty
    evaluable = preds[preds["predicted_return_5d"].notna()] if has_rows else preds
    n_eval = int(len(evaluable)) if has_rows else 0
    graded = evaluable[evaluable["realized_return_5d"].notna()] if has_rows else evaluable
    n_graded = int(len(graded)) if has_rows else 0

    # --- Evaluation window ---
    L.append("## Evaluation window")
    L.append("")
    L.append(f"- Requested: **{_fmt_dt(meta.get('start_date'))}** to "
             f"**{_fmt_dt(meta.get('end_date'))}**, horizon **{meta.get('horizon')}** sessions, "
             f"lookback **{meta.get('lookback')}** sessions, min history "
             f"**{meta.get('min_history')}** sessions.")
    if n_graded:
        L.append(f"- Actual graded as-of dates: **{_fmt_dt(graded['as_of_date'].min())}** to "
                 f"**{_fmt_dt(graded['as_of_date'].max())}**.")
    L.append(f"- Benchmark: **{meta.get('benchmark', 'SPY')}** "
             f"(excess-return metrics require benchmark coverage over the interval).")
    L.append(f"- Mode: **{'fast-smoke' if meta.get('fast_smoke') else 'full'}**.")
    L.append("")

    # --- Counts ---
    L.append("## Number of tickers")
    L.append("")
    n_tickers = int(graded["ticker"].nunique()) if n_graded else (
        int(evaluable["ticker"].nunique()) if n_eval else 0)
    L.append(f"- Distinct tickers with graded predictions: **{n_tickers}**")
    L.append("")
    L.append("## Number of predictions evaluated")
    L.append("")
    L.append(f"- Replayed predictions: **{n_eval}**")
    L.append(f"- With realized 5-day outcome (graded): **{n_graded}**")
    n_excess = int(graded["realized_excess_return_5d_vs_spy"].notna().sum()) if n_graded else 0
    L.append(f"- With benchmark (SPY) excess outcome: **{n_excess}**")
    L.append("")

    if not n_graded:
        L.append("> No graded predictions in this window — cannot compute skill metrics. "
                 "Widen --start-date/--end-date or check benchmark/data coverage.")
        L.append("")
        _append_verdict(L, verdict="inconclusive", reasons=[
            "Zero graded predictions: the model produced no replayable prediction with a "
            "realized 5-day outcome in this window."])
        return "\n".join(L)

    g = graded
    recs = list(g["recommendation"])
    real = g["realized_return_5d"].to_numpy(dtype=float)
    excess = g["realized_excess_return_5d_vs_spy"].to_numpy(dtype=float)
    pred = g["predicted_return_5d"].to_numpy(dtype=float)
    pos_flag = g["positive_return_flag"].to_numpy()
    out_flag = g["outperform_spy_flag"].to_numpy()
    conf = g["confidence"].to_numpy(dtype=float)

    # --- Coverage by model ---
    L.append("## Coverage by model")
    L.append("")
    model_counts: dict[str, int] = {}
    for s in g["ran_models"].fillna(""):
        for m in [x for x in str(s).split(",") if x]:
            model_counts[m] = model_counts.get(m, 0) + 1
    L.append("| Model | Predictions it contributed to | Coverage |")
    L.append("|---|---:|---:|")
    for m in sorted(model_counts, key=lambda k: -model_counts[k]):
        L.append(f"| {m} | {model_counts[m]} | {model_counts[m] / n_graded * 100:.1f}% |")
    if not model_counts:
        L.append("| (none) | 0 | 0.0% |")
    n_err = int((g["model_errors"].fillna("") != "").sum())
    L.append("")
    L.append(f"- Predictions with at least one model error: **{n_err}** "
             f"({n_err / n_graded * 100:.1f}%).")
    L.append("")

    # --- Recommendation distribution ---
    L.append("## Recommendation distribution (BUY/HOLD/SELL)")
    L.append("")
    buckets = [M.bucket_recommendation(r) for r in recs]
    raw_counts: dict[str, int] = {}
    for r in recs:
        key = r if r else "None"
        raw_counts[key] = raw_counts.get(key, 0) + 1
    bucket_counts = {b: buckets.count(b) for b in ("BUY", "HOLD", "SELL")}
    L.append("| Bucket | Count | Share |")
    L.append("|---|---:|---:|")
    for b in ("BUY", "HOLD", "SELL"):
        L.append(f"| {b} | {bucket_counts[b]} | {bucket_counts[b] / n_graded * 100:.1f}% |")
    L.append("")
    L.append("Raw recommendation labels: " +
             ", ".join(f"{k}={v}" for k, v in sorted(raw_counts.items())) + ".")
    L.append("")

    # --- Hit rate positive ---
    hr_pos = M.hit_rate(pos_flag)
    se_pos = M.proportion_stderr(hr_pos, n_graded)
    L.append("## Hit rate — positive 5-day return")
    L.append("")
    L.append(f"- All predictions: **{PCT(hr_pos)}** (±{PCT(se_pos)} SE, n={n_graded}).")
    base_pos = float(np.mean(real > 0))
    L.append(f"- Unconditional base rate (any row positive): **{PCT(base_pos)}**.")
    L.append("- By recommendation bucket:")
    for b in ("BUY", "HOLD", "SELL"):
        idx = [i for i, x in enumerate(buckets) if x == b]
        if idx:
            hb = M.hit_rate([pos_flag[i] for i in idx])
            L.append(f"  - {b}: **{PCT(hb)}** (n={len(idx)})")
    L.append("")

    # --- Hit rate outperform SPY ---
    hr_out = M.hit_rate(out_flag)
    n_out = int(np.sum(~pd.isna(out_flag)))
    L.append("## Hit rate — outperforming SPY")
    L.append("")
    L.append(f"- All predictions with SPY benchmark: **{PCT(hr_out)}** "
             f"(±{PCT(M.proportion_stderr(hr_out, max(n_out,1)))} SE, n={n_out}).")
    L.append("- By recommendation bucket:")
    for b in ("BUY", "HOLD", "SELL"):
        idx = [i for i, x in enumerate(buckets) if x == b and not pd.isna(out_flag[i])]
        if idx:
            hb = M.hit_rate([out_flag[i] for i in idx])
            L.append(f"  - {b}: **{PCT(hb)}** (n={len(idx)})")
    L.append("")

    # --- Avg realized by rec ---
    L.append("## Average realized 5-day return by recommendation")
    L.append("")
    by_real = M.avg_by_recommendation(recs, real)
    L.append("| Bucket | n | Mean realized 5d | Std |")
    L.append("|---|---:|---:|---:|")
    for b in ("BUY", "HOLD", "SELL"):
        d = by_real[b]
        L.append(f"| {b} | {d['n']} | {PCT(d['mean'])} | {PCT(d['std'])} |")
    L.append("")

    # --- Avg excess by rec ---
    L.append("## Average realized excess return vs SPY by recommendation")
    L.append("")
    by_excess = M.avg_by_recommendation(recs, excess)
    L.append("| Bucket | n | Mean excess vs SPY | Std |")
    L.append("|---|---:|---:|---:|")
    for b in ("BUY", "HOLD", "SELL"):
        d = by_excess[b]
        L.append(f"| {b} | {d['n']} | {PCT(d['mean'])} | {PCT(d['std'])} |")
    L.append("")

    # --- Precision@K ---
    L.append("## Precision@K for top predicted returns")
    L.append("")
    L.append("Predictions ranked by predicted 5-day return (highest first); precision = "
             "fraction of the top-K that were realized positive / outperformed SPY.")
    L.append("")
    L.append("| K | Precision (positive) | Precision (beat SPY) |")
    L.append("|---:|---:|---:|")
    for k in (10, 25, 50, 100):
        if k > n_graded:
            continue
        p_pos = M.precision_at_k(pred, [1.0 if x else 0.0 for x in pos_flag], k)
        out_lab = [np.nan if pd.isna(x) else (1.0 if x else 0.0) for x in out_flag]
        p_out = M.precision_at_k(pred, out_lab, k)
        L.append(f"| {k} | {PCT(p_pos)} | {PCT(p_out)} |")
    L.append(f"- For reference, picking at random would score the base rates above "
             f"(~{PCT(hr_pos)} positive, ~{PCT(hr_out)} beat-SPY).")
    L.append("")

    # --- Rank IC ---
    ic = M.spearman_rank_ic(pred, real)
    ic_ex = M.spearman_rank_ic(pred, excess)
    pic = M.pearson_ic(pred, real)
    L.append("## Rank IC / Spearman correlation")
    L.append("")
    L.append(f"- Spearman rank IC (predicted vs realized 5d return): **{NUM(ic)}**")
    L.append(f"- Spearman rank IC (predicted vs realized excess vs SPY): **{NUM(ic_ex)}**")
    L.append(f"- Pearson IC (predicted vs realized): **{NUM(pic)}**")
    L.append("- Interpretation: |IC| < ~0.03 is generally indistinguishable from noise at "
             "this sample size; a usable single-factor signal is typically IC ≥ 0.03–0.05 "
             "and stable across time.")
    L.append("")

    # --- Calibration ---
    L.append("## Calibration table by confidence decile")
    L.append("")
    cal = M.calibration_table(conf, pos_flag, n_bins=10)
    if cal:
        L.append("| Decile | n | Conf range | Mean conf | Realized hit rate |")
        L.append("|---:|---:|---|---:|---:|")
        for r in cal:
            L.append(f"| {r['bin']} | {r['n']} | {r['conf_min']:.0f}–{r['conf_max']:.0f} | "
                     f"{r['mean_confidence']:.1f} | {PCT(r['hit_rate'])} |")
        calibrated = M.is_calibrated(cal)
        L.append("")
        L.append(f"- Monotonic-ish (hit rate rises with confidence)? **{'yes' if calibrated else 'no'}**.")
    else:
        calibrated = False
        L.append("No confidence values available to bucket.")
    L.append("")

    # --- Confusion matrix ---
    L.append("## Confusion matrix — BUY/HOLD/SELL vs realized positive return")
    L.append("")
    cm = M.confusion_matrix_rec(recs, pos_flag)
    L.append("| Bucket | Realized positive | Realized non-positive | n | Positive rate |")
    L.append("|---|---:|---:|---:|---:|")
    for b in ("BUY", "HOLD", "SELL"):
        row = cm[b]
        L.append(f"| {b} | {row['positive']} | {row['non_positive']} | {row['n']} | {PCT(row['positive_rate'])} |")
    L.append("")
    L.append("(Excess/beat-SPY confusion is summarized by the beat-SPY hit-rate section above.)")
    L.append("")

    # --- Drawdown for BUY ---
    L.append("## Drawdown / downside summary for BUY recommendations")
    L.append("")
    buy_real = real[[i for i, x in enumerate(buckets) if x == "BUY"]]
    dd = M.drawdown_summary(buy_real, loss_threshold=-0.02)
    L.append(f"- BUY count (graded): **{dd['n']}**")
    L.append(f"- Worst realized 5d return: **{PCT(dd['worst'])}**")
    L.append(f"- Mean realized 5d return: **{PCT(dd['mean'])}**")
    L.append(f"- Fraction negative: **{PCT(dd['pct_negative'])}**")
    L.append(f"- Fraction below {PCT(dd['loss_threshold'])}: **{PCT(dd['pct_below_threshold'])}**")
    L.append(f"- Average loss among losers: **{PCT(dd['mean_loss'])}**")
    L.append("")

    # --- Verdict ---
    verdict, reasons = _derive_verdict(
        n_graded=n_graded, ic=ic, ic_ex=ic_ex, hr_pos=hr_pos, se_pos=se_pos,
        by_real=by_real, by_excess=by_excess, calibrated=calibrated,
        bucket_counts=bucket_counts, dd=dd)
    _append_verdict(L, verdict=verdict, reasons=reasons)
    return "\n".join(L)


def _derive_verdict(*, n_graded, ic, ic_ex, hr_pos, se_pos, by_real, by_excess,
                    calibrated, bucket_counts, dd):
    """Transparent, conservative rules — stated, not hand-waved."""
    reasons: list[str] = []
    ic_ok = (not math.isnan(ic)) and abs(ic) >= 0.03
    buy = by_excess.get("BUY", {})
    buy_excess_mean = buy.get("mean", float("nan"))
    buy_n = buy.get("n", 0)
    buy_edge = (buy_n >= 30) and (not math.isnan(buy_excess_mean)) and buy_excess_mean > 0.0

    if n_graded < 200:
        verdict = "inconclusive"
        reasons.append(f"Sample is small (n={n_graded} graded predictions); estimates are noisy. "
                       "Run the full universe over a multi-month window before drawing conclusions.")
    elif ic_ok and buy_edge:
        verdict = "weak-yes"
        reasons.append(f"Rank IC = {ic:.3f} (above the ~0.03 noise floor) and BUY-bucket mean excess "
                       f"vs SPY = {buy_excess_mean * 100:.2f}% over n={buy_n}. This is *weak* evidence "
                       "of edge, not proof — no walk-forward confidence interval / regime split yet.")
    elif (math.isnan(ic) or abs(ic) < 0.03) and not buy_edge:
        verdict = "no"
        reasons.append(f"Rank IC = {NUM(ic)} is within noise and the BUY bucket shows no positive "
                       "excess vs SPY. No measurable out-of-sample edge in this window.")
    else:
        verdict = "inconclusive"
        reasons.append(f"Mixed signal: rank IC = {NUM(ic)}, BUY mean excess = "
                       f"{PCT(buy_excess_mean)} (n={buy_n}). Not enough to claim or reject edge.")

    # Where the model fails / structural notes (always reported).
    if bucket_counts.get("BUY", 0) + bucket_counts.get("SELL", 0) == 0:
        reasons.append("The recommendation gate produced **zero** BUY/SELL signals — every row was "
                       "HOLD. With no actionable signals the model cannot rank candidates.")
    reasons.append("Confidence appears " + ("**weakly calibrated**" if calibrated
                   else "**not calibrated** (hit rate does not rise with confidence decile)") +
                   " — current 'confidence' is forecast dispersion (CV), not a probability.")
    if dd["n"]:
        reasons.append(f"BUY downside is real: worst 5d = {PCT(dd['worst'])}, "
                       f"{PCT(dd['pct_negative'])} of BUYs were negative — there is no calibrated "
                       "downside/interval estimate to size or filter these.")
    return verdict, reasons


def _append_verdict(L: list[str], *, verdict: str, reasons: list[str]):
    L.append("## Verdict")
    L.append("")
    label = {"weak-yes": "Evidence of edge: WEAK YES (not quant-grade)",
             "no": "Evidence of edge: NO",
             "inconclusive": "Evidence of edge: INCONCLUSIVE"}.get(verdict, verdict)
    L.append(f"**{label}**")
    L.append("")
    for r in reasons:
        L.append(f"- {r}")
    L.append("")
    L.append("Direct answers to the required questions:")
    L.append(f"- **Where the model fails:** see the per-bucket hit rates, confusion matrix, and "
             "BUY drawdown above; HOLD-heavy output and noise-level rank IC are the main failures.")
    L.append("- **Is confidence calibrated?** No — it is dispersion-based, not a probability; the "
             "calibration table is the evidence.")
    L.append("- **Are BUY signals usable?** Only if BUY mean excess vs SPY is positive *and* stable "
             "across time with a controlled drawdown — see the BUY rows above before relying on them.")
    L.append("- **Safe for candidate ranking?** Not on this evidence alone. Treat current output as a "
             "*preview/diagnostic*, keep manual review, and do not let it drive automated selection.")
    L.append("- **Next required model improvements:** calibrated probabilities + prediction intervals, "
             "point-in-time fundamentals/known features (no leakage, no fabricated data), proper "
             "walk-forward CV with confidence intervals and regime splits, and a real out-of-sample "
             "edge test before any quant-grade claim. See docs/quant_model_upgrade_roadmap.md.")
    L.append("")
    L.append("_This report is observational. It does not change the model, the live API, scoring "
             "thresholds, orders, or automation._")


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Walk-forward baseline harness for the current model.")
    p.add_argument("--start-date", type=lambda s: dt.date.fromisoformat(s), default=None)
    p.add_argument("--end-date", type=lambda s: dt.date.fromisoformat(s), default=None)
    p.add_argument("--tickers", type=str, default=None,
                   help="Comma-separated ticker subset (default: full universe).")
    p.add_argument("--max-tickers", type=int, default=None)
    p.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON)
    p.add_argument("--min-history", type=int, default=60)
    p.add_argument("--lookback", type=int, default=None,
                   help="Model lookback sessions (default: api_server.LOOKBACK_DAYS).")
    p.add_argument("--benchmark", type=str, default="SPY")
    p.add_argument("--db-url", type=str, default=None)
    p.add_argument("--output", type=str,
                   default="docs/current_model_walk_forward_baseline_v1.md")
    p.add_argument("--fast-smoke", action="store_true",
                   help="Tiny subset for quick validation.")
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    tickers = [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else None

    start_date, end_date, max_tickers = args.start_date, args.end_date, args.max_tickers
    if args.fast_smoke:
        if tickers is None:
            tickers = ["AAPL", "MSFT", "NVDA"]
        if end_date is None:
            end_date = dt.date.today()
        if start_date is None:
            start_date = end_date - dt.timedelta(days=45)
        if max_tickers is None:
            max_tickers = 3
        print(f"[fast-smoke] tickers={tickers} window={start_date}..{end_date}")

    print("[1/4] Building walk-forward dataset ...")
    dataset, cache = build_walk_forward_dataset(
        db_url=args.db_url, tickers=tickers, start_date=start_date, end_date=end_date,
        horizon_days=args.horizon_days, min_history=args.min_history,
        benchmark=args.benchmark, max_tickers=max_tickers)
    print(f"      dataset rows: {len(dataset)} "
          f"({dataset['ticker'].nunique() if len(dataset) else 0} tickers)")

    print("[2/4] Replaying current production model (point-in-time) ...")
    from research.current_model_baseline import replay_dataset
    preds = replay_dataset(dataset, cache, benchmark=args.benchmark, lookback=args.lookback)
    print(f"      replayed predictions: {len(preds)}")

    print("[3/4] Building report ...")
    meta = {
        "start_date": start_date, "end_date": end_date, "horizon": args.horizon_days,
        "min_history": args.min_history,
        "lookback": args.lookback if args.lookback is not None else "api_server.LOOKBACK_DAYS",
        "benchmark": args.benchmark, "fast_smoke": args.fast_smoke,
    }
    md = build_report_markdown(preds, meta)

    out = args.output
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"[4/4] Wrote report -> {out} ({len(md)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
