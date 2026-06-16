# Phase 2E-B — Model-v2 Serving Adapter + Gate 4 BUY-Basket Backtest (v1)

_Offline / research only. This phase builds the **adapter** that will later let
the GCP API serve model-v2 prediction rows, plus the net-of-cost **gate 4**
backtest — but it does **not** wire anything into `api_server.py`, does **not**
connect to a database, and does **not** write to the production database._

## Objective

1. A pure serving adapter ([`model/serve_v2.py`](../model/serve_v2.py)) that
   reads Phase 2D / Phase 2E-A `prediction_outputs` rows from a local CSV,
   selects the latest row(s) per ticker, and converts them into the existing
   `/predict` and `/predict_all_models` response shapes.
2. A default-off feature-flag helper (`model_v2_enabled`) so the future API can
   gate the model-v2 read path behind `PREDICTOR_USE_MODEL_V2`.
3. An offline, net-of-cost BUY-basket backtest that closes plan §8 **gate 4** on
   the synthetic sample.

## Why `api_server.py` is unchanged

The model-v2 layer is still research-only until all five plan §8 promotion gates
pass on the **real** GCP DB across two non-overlapping sub-periods. This phase
adds the adapter as a standalone, importable module with **no runtime coupling**
to the live serving spine: `api_server.py` is not edited, not imported, and
contains no reference to `serve_v2`, `model_v2_enabled`, or
`PREDICTOR_USE_MODEL_V2` (asserted by test). The actual wiring — reading rows in
the live endpoints behind the flag — is **Phase 2E-C**.

## Why this is still offline

- The adapter reads a **local CSV** by default; it never opens a DB connection.
- It does **not** read environment variables (not even the flag) — the flag
  value is passed *in* by the caller, so this module stays pure.
- No migration is run; no production database is written. All outputs are
  SYNTHETIC sample artifacts, explicitly marked as **not a production edge**.

## Adapter response shape

`row_to_predict_response(row)` preserves every legacy key existing Paper Trader
consumers expect, and adds model-v2 fields alongside (no legacy key removed):

| legacy key | value in model-v2 adapter |
|---|---|
| `ticker` | from the row |
| `recommendation` | from the row (e.g. `Buy`, `Strong Buy`) |
| `confidence` | `max(p, 1−p) * 100` (see below) |
| `agreement` | `round(max(p, 1−p), 2)` — single-model directional proxy on 0..1 |
| `ensemble_day5` | `None` — model-v2 is return-based, not a price target |
| `predictions` | one v2 horizon point: `{model, horizon_days, predicted_excess_return_5d, prob_outperform_spy}` |
| `zscore` | `None` — needs a historical price series not present offline |

`ensemble_day5` and `zscore` are deliberately `None`: fabricating a price-level
target or a z-score from a return-based model would be fake data, which the
guardrails forbid. The key is **present** so consumers that read it do not break.

Additive model-v2 keys: `model_version`, `feature_set_version`, `score`,
`predicted_excess_return_5d`, `prob_outperform_spy`, `lo_return_5d`,
`hi_return_5d`, `interval_width`, `risk_band`, `rank_in_universe`, `as_of_date`,
`target_date`, `source`, and `model_v2: true`.

`rows_to_predict_all_response(rows)` returns a `/predict_all_models`-style batch:
`{model_v2, synthetic_sample, note, count, model_version, as_of_dates, source,
results: [ ...per-ticker responses... ]}`.

## Feature flag default-off logic

`model_v2_enabled(config_or_env_value)` is a **pure** helper:

- `None` → `False` (the default; nothing enables it implicitly).
- booleans / numbers pass through (`0` → off).
- strings: `1`, `true`, `t`, `yes`, `y`, `on`, `enabled` → `True`; everything
  else → `False`.

The module **never reads the environment**. The future API would read
`PREDICTOR_USE_MODEL_V2` itself and pass the value in, keeping the adapter pure
and testable. Tests assert the default is off.

## Confidence mapping

```
confidence = max(prob_outperform_spy, 1 − prob_outperform_spy) * 100
```

This is "how far the calibrated probability is from a coin flip," on a 0..100
scale (e.g. `p=0.72 → 72`, `p=0.30 → 70`). It is a simple, transparent heuristic
and is **not** production-proven; out-of-range / missing probabilities map to
`None`.

## Gate 4 backtest method

`gate4_backtest(predictions, labels, cost_bps=10)`:

1. **BUY basket** = every prediction row with `recommendation ∈ {Buy, Strong Buy}`.
2. Phase 2D `prediction_outputs` carry no realized labels, so realized outcomes
   are **joined from the Phase 2C calibrated sample**
   (`realized_excess_return_5d_vs_spy`, `outperform_spy_flag`) on the natural key
   `(ticker, as_of_date)`.
3. Computes: `n_buy_rows`, `n_buy_dates`, `avg_buy_realized_excess_return`,
   `avg_universe_realized_excess_return`, `buy_hit_rate_vs_spy`, `cost_bps`, and
   `net_of_cost_buy_excess_return = avg_buy_excess − cost_bps/10000`.
4. **Pass criteria** (synthetic sample only): net-of-cost BUY excess `> 0`
   **and** BUY excess `>` universe excess **and** hit rate `> 0.5`.

The result is marked `synthetic_sample: true`, `production_edge: false`. It makes
**no** claim of a production edge.

### Result on the synthetic sample

`n_buy_rows = 148`, `avg_buy_realized_excess_return ≈ 0.0140`,
`net_of_cost_buy_excess_return ≈ 0.0130` (10 bps), `buy_hit_rate_vs_spy ≈ 0.608`,
`passed = true` — **on the planted-signal synthetic sample only**.

## Sample output files

- [`research/output/phase2e_v2_api_response_sample.json`](../research/output/phase2e_v2_api_response_sample.json)
  — one single-ticker response and one multi-ticker batch response, both flagged
  synthetic / not-production-edge.
- [`research/output/phase2e_gate4_backtest.json`](../research/output/phase2e_gate4_backtest.json)
  — the gate-4 backtest result.

Regenerate locally (no DB, no writes to any database):

```bash
python -m model.serve_v2
# reads  research/output/phase2d_prediction_outputs_sample.csv
#        research/output/phase2c_calibrated_predictions_sample.csv
# writes research/output/phase2e_v2_api_response_sample.json
#        research/output/phase2e_gate4_backtest.json
```

## Next step: actual `api_server.py` wiring behind the flag (Phase 2E-C)

Phase 2E-C would, **only when `PREDICTOR_USE_MODEL_V2` is enabled**, read the
populated `prediction_outputs` rows (via the Phase 2E-A write path) and return
`row_to_predict_response` / `rows_to_predict_all_response`, falling back to the
existing model otherwise — so the live default behavior stays byte-for-byte
unchanged. That coupling is **not** introduced here.

## Guarantees

- **No production database writes.** The adapter never connects to a DB; the CLI
  reports `database_touched = false`.
- **No migration is run** in this phase.
- **Not a production edge.** Every sample output is SYNTHETIC and marked
  `production_edge: false`; the confidence mapping and gate-4 result are
  illustrative only.
- **`api_server.py` is unchanged** and live API behavior is not modified.
