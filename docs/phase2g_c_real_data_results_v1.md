# Phase 2G-C — Real-Data Export + Shadow Validation Results (v1)

_Generated for Phase 2G-C. Observational, offline, read-only. This document
reports measurements; it changes no live behavior._

> Scope: this phase exports a real, read-only price-history CSV via yfinance and
> runs it through the existing Phase 2G-B / 2G-A shadow validation harness. It is
> evaluation only. It **does not deploy**, it **does not restart
> stock-api.service**, it **does not enable** `PREDICTOR_USE_MODEL_V2`, it **does
> not run migrations**, it **does not write to production DB**, and it **does not
> trade**. No order placement, no automation.

## What was produced

| Artifact | Path |
|---|---|
| Real price-history CSV | `research/output/phase2g_price_history_real.csv` |
| Export run summary | `research/output/phase2g_c_real_data_run_summary.json` |
| Validation summary JSON | `research/output/phase2g_real_data_validation.json` |
| Scored per-row CSV | `research/output/phase2g_real_data_scored.csv` |

## Export — data source and schema

- **Source:** yfinance (explicit `--source yfinance` and `--confirm-network`).
- **Schema:** `ticker, date, adj_close` (required), plus `volume` when available.
- **Benchmark:** **SPY** is included in the universe and present in the export.
- **Universe:** a curated 40-stock list (large-cap, sector-diversified) + SPY.

### Export facts

| Fact | Value |
|---|---|
| row count | 35,014 |
| ticker count | 41 (40 names + SPY) |
| universe ticker count | 41 |
| date range | 2023-01-03 → 2026-05-29 |
| **SPY** present | yes |
| **volume present** | yes |
| missing tickers | none |

## Validation — shadow ranking metrics (real data)

Computed by the Phase 2G-A harness on point-in-time features joined to
forward 5-session labels (target: `realized_excess_return_5d_vs_spy`). Scored
frame: **32,800 rows**, **40 tickers**, **2023-02-14 → 2026-05-21**, horizon 5.

| Metric | Value | Read |
|---|---|---|
| **rank_ic** | `0.00138` | At/near zero — indistinguishable from noise (floor is 0.03). |
| **top_decile_mean_excess_return** | `0.00183` (0.18%) | Top-decile-by-score 5d excess vs SPY. |
| **universe_mean_excess_return** | `0.00011` (0.01%) | Per-date universe mean, like-for-like. |
| **top_decile_hit_rate** | `0.4899` (48.99%) | Below 50% — top decile is not more often positive. |
| **bucket_monotonic** | `false` | Mean realized return is **not** monotone across score quintiles. |

Score-quintile mean realized excess (bins 1→5): `+0.075%`, `-0.007%`,
`-0.180%`, `+0.085%`, `+0.084%` — no clean low-to-high ordering, consistent with
the near-zero rank IC.

## Verdict

| Gate | Value |
|---|---|
| **safe_for_canary** | `false` |
| **go_no_go** | `NO_GO` |
| reason | Real-data shadow validation did not clear the harness canary gate (`safe_for_canary=false`); rank IC is at noise level, the top decile does not beat the universe in hit rate, and score buckets are not monotone. The run also had no legacy-prediction comparison wired in, which the canary gate additionally requires. |

This transparent mean-z-score composite shows **no measurable cross-sectional
edge** on real data over this window. That is an honest negative result: the
harness runs end-to-end on real prices, and the result is that this particular
ranking signal is not usable as-is. It is **not** a **production edge** claim of
any kind.

## Safety flags (from the run summaries)

Both the export summary and the validation summary record:

```
database_touched        = false
database_write_executed = false
migration_executed      = false
deployment_executed     = false
model_v2_enabled        = false
production_edge_claimed = false
no_trading              = true
no_orders               = true
no_automation           = true
```

## Guardrails honored

- **does not deploy** — no deployment of any kind was performed.
- **does not restart stock-api.service** — the live service was not touched.
- **does not enable** `PREDICTOR_USE_MODEL_V2` — the feature flag stays off; this
  remains a separate, later, human-approved step even on a GO_CANDIDATE.
- **does not run migrations** — no schema change, no migration tooling.
- **does not write to production DB** — the entire phase is file-based; no
  database connection, read or write.
- **does not trade** — no order placement, no broker integration, no automation.
- No `gcloud`, no SSH, no service restart, no environment-variable writes.
- `api_server.py`, `model/features.py`, `model/eval.py`, the live serving code,
  and Paper Trader were not modified.

## Next step

A `NO_GO` here is the correct outcome to publish: it means the current
composite has no demonstrated edge on this real-data window and must not be
promoted. Any future canary candidacy still requires (a) a real legacy-prediction
comparison wired into the harness, (b) clearing the rank-IC floor with monotone
score buckets and a top decile that beats the universe, and (c) separate, explicit
human sign-off before `PREDICTOR_USE_MODEL_V2` is ever enabled.
