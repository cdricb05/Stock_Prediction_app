# Current Model — Walk-Forward Baseline (v1)

_Generated 2026-06-16 03:24:28 by research/run_walk_forward_baseline.py — observational, read-only._

> Scope: research/evaluation only. No model change, no orders, no automation, no live broker execution. The current model is **price-only** (adjusted close) with heuristic confidence; this report measures, it does not modify.

## Evaluation window

- Requested: **2026-05-02** to **2026-06-16**, horizon **5** sessions, lookback **api_server.LOOKBACK_DAYS** sessions, min history **60** sessions.
- Actual graded as-of dates: **2026-05-04** to **2026-06-08**.
- Benchmark: **SPY** (excess-return metrics require benchmark coverage over the interval).
- Mode: **fast-smoke**.

## Number of tickers

- Distinct tickers with graded predictions: **3**

## Number of predictions evaluated

- Replayed predictions: **47**
- With realized 5-day outcome (graded): **47**
- With benchmark (SPY) excess outcome: **47**

## Coverage by model

| Model | Predictions it contributed to | Coverage |
|---|---:|---:|
| Drift | 47 | 100.0% |
| LinearTrend | 47 | 100.0% |
| XGBoost | 47 | 100.0% |
| Naive | 47 | 100.0% |
| SMA | 47 | 100.0% |

- Predictions with at least one model error: **0** (0.0%).

## Recommendation distribution (BUY/HOLD/SELL)

| Bucket | Count | Share |
|---|---:|---:|
| BUY | 29 | 61.7% |
| HOLD | 17 | 36.2% |
| SELL | 1 | 2.1% |

Raw recommendation labels: Buy=24, Hold=17, Sell=1, Strong Buy=5.

## Hit rate — positive 5-day return

- All predictions: **55.32%** (±7.25% SE, n=47).
- Unconditional base rate (any row positive): **55.32%**.
- By recommendation bucket:
  - BUY: **55.17%** (n=29)
  - HOLD: **58.82%** (n=17)
  - SELL: **0.00%** (n=1)

## Hit rate — outperforming SPY

- All predictions with SPY benchmark: **61.70%** (±7.09% SE, n=47).
- By recommendation bucket:
  - BUY: **62.07%** (n=29)
  - HOLD: **64.71%** (n=17)
  - SELL: **0.00%** (n=1)

## Average realized 5-day return by recommendation

| Bucket | n | Mean realized 5d | Std |
|---|---:|---:|---:|
| BUY | 29 | 1.11% | 4.10% |
| HOLD | 17 | 1.62% | 4.63% |
| SELL | 1 | -6.88% | n/a |

## Average realized excess return vs SPY by recommendation

| Bucket | n | Mean excess vs SPY | Std |
|---|---:|---:|---:|
| BUY | 29 | 0.30% | 3.64% |
| HOLD | 17 | 1.40% | 3.87% |
| SELL | 1 | -6.16% | n/a |

## Precision@K for top predicted returns

Predictions ranked by predicted 5-day return (highest first); precision = fraction of the top-K that were realized positive / outperformed SPY.

| K | Precision (positive) | Precision (beat SPY) |
|---:|---:|---:|
| 10 | 30.00% | 50.00% |
| 25 | 44.00% | 52.00% |
- For reference, picking at random would score the base rates above (~55.32% positive, ~61.70% beat-SPY).

## Rank IC / Spearman correlation

- Spearman rank IC (predicted vs realized 5d return): **-0.1652**
- Spearman rank IC (predicted vs realized excess vs SPY): **-0.2111**
- Pearson IC (predicted vs realized): **0.0270**
- Interpretation: |IC| < ~0.03 is generally indistinguishable from noise at this sample size; a usable single-factor signal is typically IC ≥ 0.03–0.05 and stable across time.

## Calibration table by confidence decile

| Decile | n | Conf range | Mean conf | Realized hit rate |
|---:|---:|---|---:|---:|
| 1 | 4 | 97–98 | 97.3 | 75.00% |
| 2 | 4 | 98–98 | 97.9 | 50.00% |
| 3 | 4 | 98–98 | 98.0 | 50.00% |
| 4 | 4 | 98–98 | 98.2 | 75.00% |
| 5 | 4 | 98–98 | 98.4 | 75.00% |
| 6 | 3 | 98–98 | 98.5 | 33.33% |
| 7 | 4 | 99–99 | 98.6 | 50.00% |
| 8 | 4 | 99–99 | 99.0 | 75.00% |
| 9 | 4 | 99–99 | 99.2 | 25.00% |
| 10 | 4 | 99–100 | 99.6 | 75.00% |

- Monotonic-ish (hit rate rises with confidence)? **no**.

## Confusion matrix — BUY/HOLD/SELL vs realized positive return

| Bucket | Realized positive | Realized non-positive | n | Positive rate |
|---|---:|---:|---:|---:|
| BUY | 16 | 13 | 29 | 55.17% |
| HOLD | 10 | 7 | 17 | 58.82% |
| SELL | 0 | 1 | 1 | 0.00% |

(Excess/beat-SPY confusion is summarized by the beat-SPY hit-rate section above.)

## Drawdown / downside summary for BUY recommendations

- BUY count (graded): **29**
- Worst realized 5d return: **-6.02%**
- Mean realized 5d return: **1.11%**
- Fraction negative: **44.83%**
- Fraction below -2.00%: **20.69%**
- Average loss among losers: **-2.27%**

## Verdict

**Evidence of edge: INCONCLUSIVE**

- Sample is small (n=47 graded predictions); estimates are noisy. Run the full universe over a multi-month window before drawing conclusions.
- Confidence appears **not calibrated** (hit rate does not rise with confidence decile) — current 'confidence' is forecast dispersion (CV), not a probability.
- BUY downside is real: worst 5d = -6.02%, 44.83% of BUYs were negative — there is no calibrated downside/interval estimate to size or filter these.

Direct answers to the required questions:
- **Where the model fails:** see the per-bucket hit rates, confusion matrix, and BUY drawdown above; HOLD-heavy output and noise-level rank IC are the main failures.
- **Is confidence calibrated?** No — it is dispersion-based, not a probability; the calibration table is the evidence.
- **Are BUY signals usable?** Only if BUY mean excess vs SPY is positive *and* stable across time with a controlled drawdown — see the BUY rows above before relying on them.
- **Safe for candidate ranking?** Not on this evidence alone. Treat current output as a *preview/diagnostic*, keep manual review, and do not let it drive automated selection.
- **Next required model improvements:** calibrated probabilities + prediction intervals, point-in-time fundamentals/known features (no leakage, no fabricated data), proper walk-forward CV with confidence intervals and regime splits, and a real out-of-sample edge test before any quant-grade claim. See docs/quant_model_upgrade_roadmap.md.

_This report is observational. It does not change the model, the live API, scoring thresholds, orders, or automation._