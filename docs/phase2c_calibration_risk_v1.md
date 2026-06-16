# Phase 2C — Calibrated Probability + Risk / Interval Layer (v1)

_Offline / research only. This layer changes **no** live behavior: it does not modify `api_server.py`, does not write to the production database, does not deploy, and is not on the request path. It converts the Phase 2B out-of-sample scores into a calibrated probability and an honest return interval._

> **DATA SOURCE: SYNTHETIC SAMPLE.** Built on the same synthetic `SYN_*` series as Phase 2B (a *planted* signal, not market data). **These calibration/interval numbers are NOT a market result and do NOT constitute evidence of a production edge** — they only prove the calibration and interval machinery works end-to-end.

## Objective

Take the Phase 2B out-of-sample model score and produce, per prediction:

1. a **calibrated probability** of outperforming SPY (`prob_outperform_spy`),
2. an **expected risk band** (`risk_band`),
3. a **lower/upper return interval** (`lo_return_5d` / `hi_return_5d`), and
4. a **calibration + interval quality** assessment against plan §8 gates 3 and 5.

## Data source

- Source: `SYNTHETIC (Phase 2B planted-signal SYN_* sample; no --db-url given — NOT real market data)`
- Generated: 2026-06-16
- Out-of-sample predictions consumed: 808 rows, 4 folds, 8 tickers (from `model.train.run_walk_forward`).
- Raw signal calibrated: `score`; probability label: `outperform_spy_flag`.
- **Synthetic/sample results are not production edge.**

## Calibration method

- **Isotonic regression (pool-adjacent-violators), numpy-only.** Monotone non-decreasing by construction, so a higher score can never map to a lower probability. If the relationship is flat/inverted it collapses to the base rate rather than fabricating a slope.
- **Fallback: Platt / logistic (IRLS)** with the slope clamped non-negative; used only when isotonic cannot be fit. A base-rate constant is the final guard for a single-class calibration set.
- **Leakage control:** fold *k* is calibrated on the pooled out-of-sample predictions of folds *0..k-1* only — never on the fold being scored. The first fold has no prior data and uses a label-free rank transform (monotone, uses no outcomes), reported separately and not outcome-calibrated.

## Calibration table

Reliability by predicted-probability bucket (mean predicted vs actual outperform rate), measured on the outcome-calibrated rows only (the first, label-free fallback fold is excluded). Reuses `research.metrics.calibration_table`.

| bucket | n | mean predicted prob | actual outperform rate |
|---|---|---|---|
| 1 | 98 | 0.228 | 0.459 |
| 2 | 129 | 0.302 | 0.318 |
| 3 | 71 | 0.309 | 0.732 |
| 4 | 100 | 0.324 | 0.270 |
| 5 | 34 | 0.348 | 0.647 |
| 6 | 74 | 0.388 | 0.338 |
| 7 | 44 | 0.452 | 0.841 |
| 8 | 58 | 0.623 | 0.845 |

## Brier score

- Calibrated Brier: **0.2554** (n=608, base rate=0.490)
- Base-rate Brier (predict the unconditional rate): 0.2499
- Calibrated probability does not beat the base-rate baseline.

## Monotonicity

- Reliability buckets monotone non-decreasing: **False**
- Calibrated-ish (metrics heuristic `is_calibrated`): **True**
- The calibrator itself is monotone by construction (isotonic / non-negative-slope Platt), so `prob_outperform_spy` is a non-decreasing function of the raw score.

## Interval method

- **Split-conformal**, numpy-only. For fold *k* we take the signed residuals `realized_excess - predicted_excess` from folds *0..k-1* only and use their empirical 80% quantiles as the band added to each point prediction (`lo_return_5d`, `hi_return_5d`).
- Only **prior-fold** residuals are used, so there is no future-residual leakage. The first fold has no prior residuals, so its interval is left empty (reported, not fabricated).
- `risk_band` (low / medium / high) is assigned from the **interval width relative to the predicted-return magnitude** (`width / (|predicted| + eps)`) split at terciles — wider band per unit of signal = higher risk. Deterministic given the data.

## Interval coverage

- Target coverage: 80%
- Empirical coverage (realized excess inside [lo, hi]): **0.814** over 608 rows with a conformal interval.
- Average interval width: 0.1052

Coverage by score bucket:

| bucket | n | mean score | coverage |
|---|---|---|---|
| 1 | 122 | -0.0167 | 0.779 |
| 2 | 121 | -0.0095 | 0.785 |
| 3 | 122 | -0.0054 | 0.754 |
| 4 | 121 | 0.0045 | 0.909 |
| 5 | 122 | 0.0301 | 0.844 |

## Risk bands

| risk_band | n | mean realized 5d return | worst | pct negative |
|---|---|---|---|---|
| low | 203 | 0.0080 | -0.0549 | 0.433 |
| medium | 202 | -0.0040 | -0.0752 | 0.554 |
| high | 203 | -0.0079 | -0.1003 | 0.635 |

Higher `risk_band` = wider interval per unit of predicted signal (more uncertainty relative to the call).

## Downside / drawdown

Realized 5d-return downside over rows that carry an interval (reuses `research.metrics.drawdown_summary`):

- n: 608
- worst: -0.1003
- mean: -0.0013
- pct negative: 0.541
- mean loss (negative rows only): -0.0238

## Decision gate 3 (calibration)

- Calibrated probability with monotone buckets / Brier
- Monotone buckets: False; calibrated-ish: True; Brier beats base rate: False
- Result: **FAIL** _(synthetic — not a promotion signal)_

## Decision gate 5 (intervals + drawdown)

- Honest interval coverage near nominal + a measured drawdown.
- Empirical coverage 0.814 vs target 80% (tolerance ±10 pts); drawdown measured over 608 rows.
- Result: **PASS** _(synthetic — not a promotion signal)_

## Running this on the real GCP DB

This layer consumes the same Phase 2B walk-forward output, so it runs unchanged on production data — only the input source differs:

```bash
# Real, read-only DB run; writes the calibrated CSV + this report:
python -m model.calibrate --db-url "$DB_URL" --max-tickers 50 \
    --start-date 2022-01-01 --end-date 2025-01-01 \
    --output research/output/phase2c_calibrated_predictions.csv \
    --report-output docs/phase2c_calibration_risk_real.md
```

- With `--db-url`, predictions come from `model.train.run_walk_forward` on `model.features.build_feature_dataset(..., with_labels=True)` (read-only).
- On the real universe there are many folds, so every fold after the first has prior out-of-sample rows to calibrate on and prior residuals to form conformal intervals — gates 3 and 5 become fully evaluable.
- **No write-back, no deploy.** Persisting calibrated probabilities to `prediction_outputs` and wiring the API read path are Phase 2D/2E and stay behind the `PREDICTOR_USE_MODEL_V2` flag.

## API unchanged

`api_server.py` is **not modified** by this work. `/predict` and `/predict_all_models/` behave exactly as before; this Phase 2C layer is pure offline research and is not on the live request path. This synthetic/sample report is not production edge.


Validation note: api_server.py is unchanged; Phase 2C is offline research only and does not affect the live API.
