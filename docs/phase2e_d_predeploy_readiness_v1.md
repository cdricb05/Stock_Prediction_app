# Phase 2E-D — Pre-Deployment Readiness Pack (v1)

_A local, offline safety package proving the default-off model-v2 API wiring is
safe to deploy with `PREDICTOR_USE_MODEL_V2` **unset/off**. This phase does
**not** deploy, does **not** use `gcloud`/SSH, does **not** run migrations, and
does **not** write to any production database._

## Objective

Before anything reaches GCP, produce verifiable, repeatable local evidence that:

- `api_server.py` compiles,
- the model-v2 feature flag defaults **off**,
- with the flag off the live routes and legacy response keys are byte-for-byte
  intact and the model-v2 lookup is never invoked,
- the model-v2 wiring is fully guarded (import, flag read, no DB/migration
  tokens) so deploying the current commit with the flag unset cannot change live
  behavior.

The output is a readiness package — tests, a check script, a JSON summary, and
this doc — not a deployment.

## Current checkpoint

- Latest commit: `a3a637b Wire model v2 serving behind feature flag` (Phase 2E-C).
- Phase 2A–2C (feature builder, walk-forward training, calibration/risk), 2D
  (artifact persistence), 2E-A (storage layer), 2E-B (serving adapter), and 2E-C
  (flag-gated `api_server.py` wiring) are all committed.

## Why this is not a deployment

This phase only **reads** `api_server.py` and runs offline Python checks locally.
There is no `gcloud`, no SSH, no container build, no service restart, no change to
systemd/service files, and no change to any remote VM file. The running GCP
service is untouched until someone separately deploys this commit **and**
explicitly sets the flag. The check script reads `latest_git_commit` from local
`.git` metadata only (no network).

## Flag-off deployment safety logic

`api_server.py` contains a single self-contained, marker-delimited block
(`# >>> MODELV2_GATE_BEGIN` … `# <<< MODELV2_GATE_END`). The only edit on the
legacy request path is a two-line gated check at the top of `predict_all(...)`:

```python
v2_resp = model_v2_predict_single(ticker)
if v2_resp is not None:
    return v2_resp
# ... otherwise the existing legacy path runs unchanged ...
```

When the flag is off, `model_v2_predict_single(...)` returns `None` **before any
lookup**, so `v2_resp is None` and the legacy path executes exactly as before.
Both `/predict/{ticker}` and `/predict_all_models/` flow through `predict_all`,
so this one gated check covers both routes.

## Exact feature flag name

`PREDICTOR_USE_MODEL_V2`

- Read **once**, at request time, via `os.getenv("PREDICTOR_USE_MODEL_V2")`
  inside the gated helper `model_v2_is_enabled()`.
- The value is passed to the pure adapter `model.serve_v2.model_v2_enabled(...)`;
  the adapter never reads the environment itself.

## What happens if the flag is unset

The model-v2 path is **disabled**. `model_v2_is_enabled()` returns `False` (unset,
empty, `0`, `false`, `no`, `off`, or any unrecognized value → off). No rows are
loaded, no DB connection is opened, and `/predict` / `/predict_all_models/` behave
identically to the pre-2E-C service. This is the default and the recommended
deploy state for the next phase.

## What happens if the flag is on but rows are missing

The path is **fail-closed**. With the flag on, `model_v2_predict_single(...)`
still returns `None` — falling back to the legacy model — in every one of these
cases: the serving adapter import failed, no model-v2 rows could be loaded, the
requested ticker has no row, or **any** exception is raised while loading or
converting rows. The worst case is "behave like today."

## Rollback plan

1. **Fastest (no redeploy):** unset / set `PREDICTOR_USE_MODEL_V2=0` (or `off`)
   in the service environment and restart — the legacy path resumes immediately.
   Because the flag is read per request, this fully reverts model-v2 serving.
2. **Code rollback:** redeploy the previous commit. The 2E-C wiring is purely
   additive (gated block + two-line check), so reverting is a clean revert with
   no schema or data dependency.
3. **Data:** none required — the read path uses a local CSV by default and writes
   nothing, so there is no state to undo.

## Deployment plan for the next phase — **NOT RUN IN THIS PHASE**

> The following is a forward plan for **Phase 2E-E** and is **not executed here**.
> No command below is run in 2E-D.

1. Deploy commit `a3a637b` (or its successor) to GCP **with the flag unset** —
   pure no-op relative to today; confirm `/healthz`, `/ping`, `/config`,
   `/predict/{ticker}`, `/predict_all_models/` behave exactly as before.
2. Keep `PREDICTOR_USE_MODEL_V2` off through bake-in; monitor error rates and
   latency to confirm the additive wiring is inert.
3. Only in a **later** phase (after the plan §8 gates pass on the real GCP DB):
   apply the reviewed Phase 2E-A migration once via the normal migration path,
   populate `prediction_outputs` out-of-band, switch `_load_model_v2_rows()` to a
   read-only DB query, then enable the flag in a **canary** before any broad
   rollout — still fail-closed to legacy.

## Validation summary

- `tests/test_phase2_predeploy.py` — **13 tests, all passing** (self-running; no
  pytest, no DB, no network). Proves: api_server compiles; flag default off;
  flag-off ⇒ lookup not called; legacy routes present (`/predict/{ticker}`,
  `/predict_all_models/`, `/healthz`, `/config`, `/ping`); legacy keys present
  (`recommendation`, `confidence`, `agreement`, `ensemble_day5`, `predictions`,
  `zscore`); model-v2 import guarded; flag read exactly once via `os.getenv` in
  the gated block; no DB-write tokens in the gated block; no migration executed;
  no env/secret write in the gated block; no Paper Trader import; no
  sklearn/xgboost added by the model-v2 wiring; no broker/order/automation logic
  in the gated block.
- `scripts/phase2e_d_predeploy_check.py` → writes
  `research/output/phase2e_d_predeploy_summary.json` with
  `safe_to_deploy_with_flag_off: true` and `database_touched`,
  `migration_executed`, `deployment_executed` all `false`.
- Full Phase 2 regression also green: features 22, training 19, calibration 15,
  persistence 16, store 16, serve 17, wiring 14, predeploy 13 (**132 total**).

> Note on the sklearn/xgboost check: `api_server.py` legitimately imports
> sklearn/xgboost at module top-level for the **legacy** model. The "no
> sklearn/xgboost added" assertion is therefore scoped to the model-v2 **gated
> block** (and the test module), proving the model-v2 wiring introduces no new
> such dependency.

## Explicit statements

- **No deployment happened.** No `gcloud`, no SSH, no container build, no service
  restart, no remote VM or systemd/service file change.
- **No migration was run.** No DDL/DML, no Alembic, no schema change.
- **No production database write happened.** No DB connection is opened on this
  path; the default source is a local CSV and the read helper is read-only.
- **Do not enable model-v2 yet.** Deploy with `PREDICTOR_USE_MODEL_V2` unset/off;
  enabling is gated to a later canary phase after DB population.
