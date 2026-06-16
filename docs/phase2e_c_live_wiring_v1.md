# Phase 2E-C — Flag-Gated `api_server.py` Wiring (v1)

_This phase wires the Phase 2E-B model-v2 serving adapter into the **live**
`api_server.py`, but **only behind a default-off feature flag** that fails
closed. With the flag off (the default), `/predict/{ticker}` and
`/predict_all_models/` behave byte-for-byte as before. No deployment, no
migration, and no production database write happens in this phase._

## Objective

Let the live API *optionally* serve stored model-v2 prediction rows, gated by a
single environment flag, while guaranteeing the existing (legacy) behavior is
the default and is fully preserved. If model-v2 rows are missing or anything
fails, the API silently falls back to the existing model path.

## Feature flag

- **Exact name:** `PREDICTOR_USE_MODEL_V2`
- **Default:** **off.** Unset, empty, `0`, `false`, `no`, `off`, or any
  unrecognized value → disabled.
- The flag is read **at request time** in `api_server.py` via
  `os.getenv("PREDICTOR_USE_MODEL_V2")`, and the value is passed into the pure
  helper `model.serve_v2.model_v2_enabled(...)`. The adapter module
  (`model/serve_v2.py`) **never reads the environment itself** — it stays pure
  and testable, consistent with Phase 2E-B.

## Default-off behavior

When the flag is off, the gated helper `model_v2_predict_single(ticker)` returns
`None` **before any model-v2 lookup is attempted** (the row loader is not even
called), so `predict_all(...)` proceeds directly into the unchanged legacy code
path (`get_fresh_series → run_model_suite → …`). This is proven by
`test_flag_off_lookup_not_called` (loader call-count is 0) and
`test_flag_off_legacy_path_reachable`.

## Fallback behavior (fail-closed)

When the flag is **on**, the gated helper still returns `None` — causing legacy
fallback — in every one of these cases:

- the serving adapter is unavailable (guarded import failed),
- no model-v2 rows could be loaded, or the requested ticker has no row,
- **any** exception is raised while loading/converting rows.

So the model-v2 path can never break a request: the worst case is "behave like
today." Proven by `test_flag_on_missing_rows_falls_back`,
`test_flag_on_unknown_ticker_falls_back`, and
`test_flag_on_loader_raises_falls_back`.

## What changes in `api_server.py`

Two small, additive edits — no route names changed, no response key removed:

1. A self-contained, flag-gated helper block (delimited by
   `# >>> MODELV2_GATE_BEGIN` / `# <<< MODELV2_GATE_END`):
   - a guarded `from model import serve_v2 as _serve_v2` (absence ⇒ legacy only),
   - `model_v2_is_enabled()` — reads the flag at request time, default off,
   - `_load_model_v2_rows()` — **read-only** load from a local CSV
     (`PREDICTOR_MODEL_V2_SOURCE` or the adapter default); called only when the
     flag is on; no DB connection by default,
   - `model_v2_predict_single(ticker)` → `serve_v2.row_to_predict_response`,
   - `model_v2_predict_batch(tickers)` → `serve_v2.rows_to_predict_all_response`
     (available for future use; the live `/predict_all_models/` is single-ticker).
2. Inside `predict_all(...)`, immediately after the ticker is parsed:
   ```python
   v2_resp = model_v2_predict_single(ticker)
   if v2_resp is not None:
       return v2_resp
   # ... otherwise the existing legacy path runs unchanged ...
   ```

Both `/predict/{ticker}` and `/predict_all_models/` flow through `predict_all`,
so this single gated check covers both routes.

### Response compatibility

A model-v2 response (from `serve_v2.row_to_predict_response`) preserves every
legacy key existing consumers read: `ticker`, `recommendation`, `confidence`,
`agreement`, `ensemble_day5`, `predictions`, `zscore` — plus additive model-v2
keys. No legacy key is removed. (`ensemble_day5`/`zscore` are `None` because
model-v2 is return-based, not price-level — see the Phase 2E-B doc.)

## Why live behavior is unchanged when the flag is off

- The flag defaults off; `model_v2_enabled(None) is False`.
- With the flag off the gated helper returns `None` without loading anything, so
  the legacy branch executes exactly as before.
- The only edit on the legacy path is the two-line gated check, which is a no-op
  when `v2_resp is None`.
- Existing routes, response keys, and the legacy response builder are untouched
  (asserted by `test_routes_and_legacy_keys_unchanged` and the features/store
  contract tests).

## Why no deployment happened

This phase only edits source files and runs offline tests locally. No SSH, no
`gcloud`, no container build, no service restart — the running GCP service is not
affected until someone deploys this commit and explicitly sets the flag.

## Why no migration ran

The model-v2 read path reads a **local CSV** by default and never issues DDL/DML.
The gated block contains no `create_engine`, `sqlalchemy`, `INSERT`, `UPDATE`,
`DELETE`, `commit`, `to_sql`, or migration call (asserted by
`test_gated_block_has_no_db_or_migration_tokens`). The Phase 2E-A migration is
still an un-executed, reviewed artifact.

## Why no production database write happened

- The default source is a local CSV; no DB connection is opened by default.
- The read helper is read-only and only invoked when the flag is explicitly on.
- `test_no_db_connection_or_writes_in_gated_path` sabotages the store engine
  builder and exercises both gated helpers (flag on) — proving no connection is
  attempted even on the enabled path with the default local source.

## Test summary

`tests/test_phase2_wiring.py` — **14 tests, all passing** (self-running; no
pytest, no DB, no network). It extracts the *real* gated block from
`api_server.py` (between the markers) and `exec`s it in an isolated namespace
with a dummy logger and the real adapter — so the behavioral tests run the
shipped source **without** importing fastapi/prophet/xgboost/sklearn or
triggering api_server's import-time DB connection. Coverage:

- flag defaults off; flag-off ⇒ lookup not called; flag-off ⇒ legacy reachable,
- flag-on + rows ⇒ v2 path used (legacy keys preserved); batch shape preserved,
- flag-on + missing/unknown rows ⇒ fallback; flag-on + loader raises ⇒ fallback,
- no DB connection/writes on the gated path; no migration tokens,
- no Paper Trader import; tests pull in no sklearn/xgboost,
- api_server references model-v2 **only** through the gated helper path; routes
  and legacy response keys unchanged.

Full Phase 2 regression also green: features 22, training 19, calibration 15,
persistence 16, store 16, serve 17, wiring 14.

> Note: the Phase 2E-B `test_phase2_serve.py` guard that previously asserted
> `api_server.py` contained **no** model-v2 references was updated to its 2E-C
> successor: it now asserts the wiring exists but **only** via the guarded import
> and a single `os.getenv("PREDICTOR_USE_MODEL_V2")` read.

## Next step recommendation (Phase 2E-D)

With the read path wired and fail-closed, Phase 2E-D would, **outside** the live
default path and only after the plan §8 gates pass on the **real** GCP DB:

1. apply the reviewed Phase 2E-A migration once (normal migration path),
2. populate `prediction_outputs` via the explicit
   `model.store.persist(..., db_url=..., commit=True)` write path (one-off, not
   in the request path), and
3. switch `_load_model_v2_rows()` to a **read-only** DB query (reusing existing
   app DB configuration, no new secrets), still gated by `PREDICTOR_USE_MODEL_V2`
   and still fail-closed to legacy — then enable the flag in a canary before any
   broad rollout.

No deployment, package install, migration run, or production DB write is part of
this phase.

## Guarantees

- **Live default behavior unchanged.** Flag defaults off; legacy path runs as
  before; no route renamed; no legacy response key removed.
- **Fail-closed.** Any missing rows / error on the model-v2 path falls back to
  the existing model.
- **No production database write, no DB connection by default, no migration
  run, no deployment, no package install** in this phase (all asserted by tests
  and unchanged config).
