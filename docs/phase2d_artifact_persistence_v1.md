# Phase 2D — Model Artifact + Prediction Persistence Design (v1)

_Offline / research only. This phase defines a reproducible way to **save** the Phase 2B/2C output: model-version metadata, the canonical prediction row schema, the recommendation mapping, the ranking rule, and the promotion gate results. It changes **no** live behavior — `api_server.py` is untouched, **nothing is written to any database**, there is no deploy, and it is not on the request path._

> **DATA SOURCE: SYNTHETIC SAMPLE.** Built on the same synthetic `SYN_*` series as Phase 2B/2C (a *planted* signal, not market data). **These rows are NOT a market result and do NOT constitute evidence of a production edge** — they only demonstrate the artifact + persistence design end-to-end. This synthetic/sample output is not production edge.

## Objective

Take the Phase 2B/2C output and define a reproducible, auditable way to save:

1. model-version metadata,
2. the feature-set version,
3. calibration / risk metadata,
4. daily prediction rows, and
5. promotion gate results.

This is **not** API integration — connecting these rows to the live service is Phase 2E, behind the `PREDICTOR_USE_MODEL_V2` flag.

## Registry metadata schema

`model.registry.ModelVersionMetadata` is a JSON-serializable record (stdlib `json`, **no pickle**). Required fields:

| field | meaning |
|---|---|
| `model_version` | deterministic id `<prefix>-<model>-<date>-<hash6>` |
| `feature_set_version` | id over the sorted Phase 2A feature names `<prefix>-<n>f-<hash8>` |
| `created_at` | ISO timestamp the artifact was built |
| `training_source` | SYNTHETIC sample or the read-only DB descriptor |
| `training_start_date` / `training_end_date` | training window (ISO dates) |
| `horizon_days` | forecast horizon (sessions) |
| `model_name` | underlying model (e.g. `ridge`) |
| `calibration_method` | how `prob_outperform_spy` was produced |
| `interval_method` | how `lo/hi_return_5d` was produced |
| `decision_gate_summary` | plan §8 gate results (3 & 5 here) |
| `metrics_summary` | Brier / coverage / drawdown summary |

It round-trips: `ModelVersionMetadata.from_json(m.to_json()) == m`. NaN/Inf and numpy scalars are sanitized to valid JSON before writing.

Sample artifact for this run:

```json
{
  "model_version": "phase2-ridge-20260616-7bda0e",
  "feature_set_version": "phase2a-19f-7d7ba3b1",
  "created_at": "2026-06-16T18:44:53",
  "training_source": "SYNTHETIC (Phase 2B/2C planted-signal SYN_* sample; no --db-url given \u2014 NOT real market data)",
  "training_start_date": "2023-06-30",
  "training_end_date": "2024-02-16",
  "horizon_days": 5,
  "model_name": "ridge",
  "calibration_method": "isotonic (PAVA) -> Platt fallback -> base-rate; walk-forward, prior folds only",
  "interval_method": "split-conformal signed-residual quantiles (coverage 0.8); prior folds only",
  "decision_gate_summary": {
    "gate3": {
      "name": "Calibrated probability with monotone buckets / Brier",
      "evaluable": true,
      "passed": false
    },
    "gate5": {
      "name": "Honest interval coverage + measured drawdown",
      "evaluable": true,
      "passed": true
    },
    "gate4": {
      "name": "BUY basket beats SPY + universe after costs",
      "evaluable": false,
      "passed": null,
      "note": "deferred to Phase 2E (decision/cost backtest)"
    }
  },
  "metrics_summary": {
    "n_rows": 808,
    "brier": 0.2554185910807814,
    "brier_base_rate": 0.249902614265928,
    "base_rate": 0.4901315789473684,
    "interval_coverage": 0.8141447368421053,
    "target_coverage": 0.8,
    "avg_interval_width": 0.10522829888344677,
    "drawdown": {
      "n": 608,
      "worst": -0.1002819651583794,
      "mean": -0.001306046303845798,
      "pct_negative": 0.5411184210526315,
      "pct_below_threshold": 0.29605263157894735,
      "mean_loss": -0.023771404783676384,
      "loss_threshold": -0.02
    }
  },
  "schema_version": "phase2d-registry-v1",
  "is_synthetic": true
}
```

## Prediction output schema

`model.persist.PREDICTION_OUTPUT_COLUMNS` — one row per `(ticker, as_of_date)` prediction:

| column | meaning |
|---|---|
| `ticker` | instrument symbol |
| `as_of_date` | point-in-time anchor of the features |
| `generated_at` | when this offline row was materialized |
| `target_date` | date the horizon return resolves |
| `model_version` | FK to the registry metadata |
| `feature_set_version` | feature-set id used |
| `horizon_days` | forecast horizon (sessions) |
| `score` | raw ranking score |
| `predicted_excess_return_5d` | point prediction of excess vs SPY |
| `prob_outperform_spy` | calibrated probability in [0,1] |
| `lo_return_5d` | conformal lower bound |
| `hi_return_5d` | conformal upper bound |
| `interval_width` | hi - lo |
| `risk_band` | low / medium / high |
| `recommendation` | 5-level call (see below) |
| `rank_in_universe` | cross-sectional rank within as_of_date (1 = best) |
| `source` | SYNTHETIC sample or read-only DB descriptor |

Sample rows written: **808** (see `research\output\phase2d_prediction_outputs_sample.csv`).

## Recommendation mapping

A transparent, deterministic mapping from the **calibrated probability**, the **expected excess return**, and the **risk band** to a 5-level call. Conviction is a ladder (+2 Strong Buy … -2 Strong Sell); a `high` risk_band softens conviction by one notch toward Hold (never flips direction).

| condition | recommendation |
|---|---|
| `prob >= 0.62` and expected excess >= 0.0 | Strong Buy |
| `prob >= 0.55` and expected excess >= 0.0 | Buy |
| `0.45 < prob < 0.55` (or edge wrong sign) | Hold |
| `prob <= 0.45` and expected excess <= -0.0 | Sell |
| `prob <= 0.38` and expected excess <= -0.0 | Strong Sell |

Thresholds live in `RecommendationThresholds` (one dataclass, easy to retune). The strings match `api_server`'s vocabulary exactly, so a future serving path needs no translation — **but nothing is served here.**

Distribution on this sample:

| recommendation | n |
|---|---|
| Strong Buy | 108 |
| Buy | 40 |
| Hold | 163 |
| Sell | 176 |
| Strong Sell | 321 |

## Ranking logic

`rank_within_date` ranks rows **within each `as_of_date`** by `score` descending — the best score gets `rank_in_universe = 1`. Ties break by `ticker` ascending and non-finite scores rank last, so the ordering is **stable and deterministic** (no random tie-breaks).

## Mapping to the future database table

`PREDICTION_OUTPUT_COLUMNS` is designed to map 1:1 onto a future `prediction_outputs` table and `ModelVersionMetadata` onto a `model_registry` row (`model_version` is the foreign key). The natural primary key is `(model_version, ticker, as_of_date)`. **No table is created or altered in this phase** — this is a schema *design*, materialized only to local CSV/JSON. The actual write-back and the API read path are Phase 2E, gated by `PREDICTOR_USE_MODEL_V2`.

## Why this is still offline / research only

The numbers above are produced from a clearly-labeled SYNTHETIC sample and are not a promotion signal. Promotion requires all five plan §8 gates passing on the real GCP DB across two non-overlapping sub-periods. Persistence here means **writing local files only**; connecting to the live service is deferred to Phase 2E.

Promotion gate summary carried in the metadata:

| gate | evaluable | passed |
|---|---|---|
| gate3 | True | False |
| gate5 | True | True |

## API unchanged

`api_server.py` is **not modified** by this work. `/predict` and `/predict_all_models/` behave exactly as before; this Phase 2D layer is pure offline research and is not on the live request path.

## No database writes

This phase performs **no database writes of any kind**. The DB is read **only** when `--db-url` is supplied (the same read-only path as Phase 2B/2C); the default run is fully synthetic and offline. Output is written exclusively to local CSV/JSON/markdown files.

