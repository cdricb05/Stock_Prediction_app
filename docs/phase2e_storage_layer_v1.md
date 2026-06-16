# Phase 2E-A — Storage Layer + Idempotent SQL Design (v1)

_Offline / research only. This phase **designs and validates** the database
storage layer for model-v2 predictions. It does **not** connect to the live API,
does **not** write to the production database, and does **not** run any
migration. `api_server.py` is untouched and live API behavior is unchanged._

## Objective

Prepare the persistence layer that will eventually store the Phase 2D artifacts
(model-version metadata + canonical prediction rows):

1. an **idempotent, non-destructive** SQL migration defining the two future
   tables (`model_registry`, `prediction_outputs`),
2. a **pure validation + conversion** module (`model/store.py`) that turns the
   Phase 2D CSV/JSON into DB-ready records, and
3. a **dry-run** mode that validates everything and writes only a local JSON
   summary — no database is contacted.

## Why this is still offline

The model-v2 layer remains research-only until all five plan §8 promotion gates
pass on the **real** GCP DB across two non-overlapping sub-periods. This phase
adds **no** runtime coupling: the migration is a reviewed artifact that is *not*
executed here, the storage module never connects by default, and the live
serving spine (`api_server.py`) is not modified. Connecting predictions to the
API is deferred to Phase 2E-B, behind the `PREDICTOR_USE_MODEL_V2` flag.

## Schema summary

Two tables, defined in [`migrations/phase2e_prediction_outputs.sql`](../migrations/phase2e_prediction_outputs.sql)
using **`CREATE TABLE IF NOT EXISTS` only** (no `DROP`, no `TRUNCATE`, no
`DELETE`, no destructive `ALTER`). They mirror Phase 2D exactly:
`model_registry` ⟵ `model.registry.ModelVersionMetadata`, and
`prediction_outputs` ⟵ `model.persist.PREDICTION_OUTPUT_COLUMNS`.

### `model_registry` table

One row per offline model version.

| column | type | notes |
|---|---|---|
| `model_version` | TEXT | **primary key** |
| `feature_set_version` | TEXT | indexed |
| `created_at` | TIMESTAMP | when the artifact was built |
| `training_source` | TEXT | SYNTHETIC sample or read-only DB descriptor |
| `training_start_date` / `training_end_date` | DATE | training window |
| `horizon_days` | INTEGER | forecast horizon |
| `model_name` | TEXT | e.g. `ridge` |
| `calibration_method` | TEXT | how `prob_outperform_spy` was produced |
| `interval_method` | TEXT | how `lo/hi_return_5d` was produced |
| `decision_gate_summary` | **JSONB** | plan §8 gate results |
| `metrics_summary` | **JSONB** | Brier / coverage / drawdown summary |
| `is_synthetic` | BOOLEAN | TRUE for the sample artifact |
| `schema_version` | TEXT | registry schema tag |
| `inserted_at` | TIMESTAMP | DB insert time (default `now()`) |

The two summary objects are **JSONB** so they stay queryable; nothing is
pickled or stored as an opaque blob.

### `prediction_outputs` table

One row per `(model_version, ticker, as_of_date)`.

| column | type |
|---|---|
| `model_version` | TEXT (FK → `model_registry.model_version`) |
| `ticker` | TEXT |
| `as_of_date` | DATE |
| `generated_at` | TIMESTAMP |
| `target_date` | DATE |
| `feature_set_version` | TEXT |
| `horizon_days` | INTEGER |
| `score` | DOUBLE PRECISION |
| `predicted_excess_return_5d` | DOUBLE PRECISION |
| `prob_outperform_spy` | DOUBLE PRECISION |
| `lo_return_5d` / `hi_return_5d` / `interval_width` | DOUBLE PRECISION |
| `risk_band` | TEXT |
| `recommendation` | TEXT |
| `rank_in_universe` | INTEGER |
| `source` | TEXT |
| `inserted_at` | TIMESTAMP (default `now()`) |

Indexes support the common reads: by `as_of_date` (latest cross-section), by
`(ticker, as_of_date)` (per-ticker history), and by `model_version`.

## Duplicate protection

The natural key **`(model_version, ticker, as_of_date)`** is the primary key of
`prediction_outputs`, so the same model cannot store two rows for the same
instrument on the same day. The future write path uses
`INSERT ... ON CONFLICT (model_version, ticker, as_of_date) DO NOTHING`, and
`model.store.validate_prediction_rows` rejects a batch that already contains
duplicate natural keys before any write is attempted.

## Dry-run behavior

`model/store.py` defaults to a **pure dry run**:

- `persist(..., commit=False)` (the default) **validates** the metadata and
  prediction rows, computes `would_write_model_registry_rows` and
  `would_write_prediction_output_rows`, and writes a local JSON summary.
- **No database is contacted** unless `db_url` is explicitly supplied **and**
  `commit=True` is explicitly passed. With `commit=True` but no `db_url`, the
  module refuses and records a `write_error`.
- There is **no env-var read and no DB connection at import time**; the
  SQLAlchemy engine is imported lazily and only inside the explicit commit path.

CLI (dry run by default):

```bash
python -m model.store
# reads  research/output/phase2d_prediction_outputs_sample.csv
#        research/output/phase2d_model_metadata_sample.json
# writes research/output/phase2e_store_dry_run_summary.json
```

Sample dry-run summary
([`research/output/phase2e_store_dry_run_summary.json`](../research/output/phase2e_store_dry_run_summary.json)):
`metadata_rows_validated = 1`, `prediction_rows_validated = 808`,
`would_write_model_registry_rows = 1`,
`would_write_prediction_output_rows = 808`, `commit_enabled = false`,
`database_touched = false`, `migration_executed = false`.

> **Future Phase 2E-B example — DO NOT RUN YET.** Once the gates pass and the
> migration has been applied, a write would look like
> `python -m model.store --db-url "$DB_URL" --commit`. This is documented for
> orientation only; it is **not** exercised in this phase.

## How this prepares Phase 2E-B API flag integration

With the schema designed, validated, and dry-run-proven, Phase 2E-B can:

1. apply the reviewed migration once (manually / via the normal migration path),
2. enable the explicit `persist(..., db_url=..., commit=True)` write path to
   populate the tables, and
3. add a **read** path in the serving spine that returns these rows **only when
   `PREDICTOR_USE_MODEL_V2` is enabled**, falling back to the existing model
   otherwise — so the live default behavior stays byte-for-byte unchanged.

No part of that coupling is introduced here.

## Guarantees

- **`api_server.py` is unchanged.** No live API behavior is modified; the file
  contains no reference to `model.store` or `phase2e` (asserted by test).
- **No production database writes.** The default and every test/doc path run with
  `commit=False`; `database_touched` is `false`. A write requires an explicit
  `--db-url` **and** `--commit`, neither used in this phase.
- **The migration is not run in this phase.** The SQL file is shipped as a
  reviewed artifact only; `migration_executed` is `false`.
