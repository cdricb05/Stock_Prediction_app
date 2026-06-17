# Phase 2G-B — Real-Data CSV Validation Runner (v1)

_A strict, offline, read-only runner that wraps the Phase 2G-A shadow validation
harness for a **real** historical price CSV export. **This phase does not deploy,
does not use gcloud or SSH, does not restart `stock-api.service`, does not enable
the feature flag, does not run migrations, does not connect to or write the
production database, and does not trade, place orders, or automate anything.** It
prepares the safe local runner, the validation gate, and this runbook so a later
phase can drop in a real read-only CSV and execute the validation without
changing any live behavior._

## Objective

Provide a single, repeatable, **strict** command that takes a real historical
price CSV export, validates it is fit for shadow evaluation, runs it through the
existing Phase 2G-A shadow harness as a confirmed real-data run, and emits a
clear `GO_CANDIDATE` / `NO_GO` verdict — entirely offline and file-based, with
the model-v2 path left **off** in production.

The runner is `research/run_phase2g_real_data_validation.py`. It writes a summary
JSON (default `research/output/phase2g_real_data_validation.json`) and the scored
per-row CSV (default `research/output/phase2g_real_data_scored.csv`).

## Relationship to the Phase 2G-A harness

This runner is a **thin wrapper** around
`research/run_phase2g_shadow_validation.py` (Phase 2G-A). The 2G-A harness does
the real work — point-in-time feature building, the transparent ranking
composite, forward-label joining, and the ranking metrics (rank IC, top-decile
vs universe, hit rate, bucket monotonicity, probability buckets, and the
`safe_for_canary` gate). The 2G-B runner adds, on top of that harness:

- **Two explicit gates** before any real-data run: `--input-csv` is required, and
  `--confirm-real-data` is required to treat the data as real (this is the only
  switch that sets `fixture_only: false` in the harness). A tests-only
  `--test-fixture-mode` flag bypasses the confirmation but forces a non-real,
  non-edge, `NO_GO` result.
- **CSV pre-flight validation**: required columns, benchmark presence, and
  minimum breadth/length thresholds.
- **A go / no-go verdict** derived from the harness `safe_for_canary` result.

The full 2G-A harness result is embedded under `phase2g_a_summary` in the 2G-B
summary, so nothing is hidden.

## Required CSV schema

| column | required | meaning |
| --- | --- | --- |
| `ticker` | yes | instrument symbol (include the benchmark, e.g. `SPY`) |
| `date` | yes | session date (`YYYY-MM-DD` or any pandas-parseable date) |
| `adj_close` | yes | split/dividend-adjusted close; rows with null or ≤ 0 are dropped by the harness |
| `volume` | no | session volume; volume features are emitted only when a real, fully-populated column is present, otherwise reported unavailable (never fabricated) |

Pre-flight thresholds for a real-data run (overridable on the CLI):

- the benchmark ticker (default `SPY`) must be present unless `--benchmark` is
  overridden (or the check disabled with `--no-require-benchmark`),
- at least `--min-tickers` distinct **universe** tickers (default `20`), and
- at least `--min-dates` distinct dates (default `120`).

## How to run once a real, read-only CSV export exists

The export step itself is **Phase 2G-C** — this phase does not produce it. Once a
read-only export CSV exists locally:

```bash
# Confirmed real-data validation (Phase 2G-C usage):
python research/run_phase2g_real_data_validation.py \
    --input-csv path/to/real_prices_export.csv \
    --confirm-real-data \
    --legacy-csv path/to/legacy_predictions.csv \
    --output-json research/output/phase2g_real_data_validation.json \
    --scored-csv research/output/phase2g_real_data_scored.csv
```

Notes:

- Without `--input-csv` the runner **refuses** and exits non-zero.
- Without `--confirm-real-data` (and not in `--test-fixture-mode`) the runner
  **refuses** and exits non-zero — it will not silently treat data as real.
- `--legacy-csv` is required for the harness canary gate to be able to pass
  (`legacy_comparison_available` must be true).
- `--test-fixture-mode` is for the test suite only; it runs the small synthetic
  fixture and always yields `test_fixture_mode: true`, `safe_for_canary: false`,
  `go_no_go: NO_GO`.

## How to interpret `go_no_go`

- **`NO_GO`** — the default and the fail-safe. Emitted whenever the run is a
  fixture/test run, the real-data confirmation is absent, or the real-data
  shadow validation did not clear the harness canary gate
  (`safe_for_canary: false`). No further action.
- **`GO_CANDIDATE`** — emitted only for a confirmed real-data run that cleared
  the harness canary gate (`safe_for_canary: true`). This is a **candidate
  signal only**. It does **not** authorize enabling model-v2. Enabling
  `PREDICTOR_USE_MODEL_V2` remains a separate, later, **human-approved** canary
  phase that stays fail-closed to legacy. `production_edge_claimed` stays
  `false` in this phase regardless of the verdict.

## Canary gate criteria

`safe_for_canary` (inherited from the 2G-A harness) is `true` only for a run that
**simultaneously**:

1. is a confirmed real-data run (`confirm_real_data: true`, so the harness
   `fixture_only` is false),
2. has a legacy comparison available (`legacy_comparison_available: true`),
3. clears the rank-IC floor (`rank_ic >= 0.03`),
4. shows monotone score buckets (`bucket_monotonic: true`), and
5. has the top decile beat the universe
   (`top_decile_mean_excess_return > universe_mean_excess_return`).

A `GO_CANDIDATE` verdict requires all of the above **and** is still only a
recommendation to consider a later, separate, human-approved canary.

## Explicit guardrail statements

- **This phase does not deploy.** No code rollout, no systemd change, no remote
  VM change.
- **This phase does not use gcloud or SSH.** No cloud CLI, no remote shell, no
  tunnel.
- **This phase does not restart `stock-api.service`.** The live service is never
  touched.
- **This phase does not enable `PREDICTOR_USE_MODEL_V2`.** The summary always
  records `model_v2_enabled: false`.
- **This phase does not run migrations.** No DDL/DML, no Alembic, no schema
  change.
- **This phase does not connect to the production database.** The runner is
  file-based; it opens no database connection.
- **This phase does not write to the production database.** Nothing is persisted
  anywhere except the local summary JSON and scored CSV.
- **This phase does not trade, create orders, or automate.** No broker, no order
  placement, no scheduler; the summary records `no_trading`, `no_orders`, and
  `no_automation` all true.
- **Test-fixture output is not production edge.** A `--test-fixture-mode` run is
  marked `test_fixture_mode: true`, `production_edge_claimed: false`,
  `safe_for_canary: false`, `go_no_go: NO_GO`; any skill shown is the planted
  synthetic signal being recovered, which only proves the wrapper runs.

## Summary fields

The runner writes a JSON summary containing at least: `phase` (`2G-B`),
`phase2g_a_summary` (the nested harness result), `input_mode` (`csv`),
`confirm_real_data`, `test_fixture_mode`, `real_data_validation_attempted`,
`database_touched` (false), `migration_executed` (false), `deployment_executed`
(false), `model_v2_enabled` (false), `production_edge_claimed` (false),
`no_trading`/`no_orders`/`no_automation` (true), `safe_for_canary` (from the
harness), `go_no_go` (`GO_CANDIDATE`/`NO_GO`), `go_no_go_reason`, and the
`input_stats` pre-flight breakdown.

## Next step

**Phase 2G-C — provide a real, read-only export and run this wrapper.** Produce a
read-only export of the real historical price history to a CSV matching the
schema above (no DB write, no migration, include the benchmark), then run this
wrapper with `--input-csv <export> --confirm-real-data` (and `--legacy-csv` for
the canary gate). Review the resulting `rank_ic`, top-decile-vs-universe, bucket
monotonicity, `safe_for_canary`, and `go_no_go` — still offline, still flag-off,
still no trading. Only a `GO_CANDIDATE` there would justify scheduling a
separate, human-approved canary that enables `PREDICTOR_USE_MODEL_V2` fail-closed
to legacy.
