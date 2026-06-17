# Phase 2E-E — Flag-OFF Deployment Runbook + Post-Deploy Smoke (v1)

_The deployment plan and read-only smoke tooling for shipping the current commit
to GCP with `PREDICTOR_USE_MODEL_V2` **unset/off**. **This phase does not
deploy.** It only prepares the runbook and the local smoke-test script. No SSH,
no `gcloud`, no service restart, no migration, and no database write happens in
this phase._

## Objective

Have a precise, repeatable runbook to deploy the current commit as a **pure
code-only, flag-off** rollout — inert relative to today — together with a
read-only smoke-test script that proves, against the running service, that the
legacy contract is intact and the model-v2 path is inactive. Deploying is a
**separate, later** action; nothing here touches the live service.

## Current commit expected

`58a05ee Add Phase 2E predeploy readiness checks` (or later). Phases 2A–2E-D are
committed: feature builder, walk-forward training, calibration/risk, artifact
persistence, storage layer, serving adapter, flag-gated `api_server.py` wiring
(2E-C), and pre-deploy readiness checks (2E-D).

## This phase does not deploy

This phase only creates documentation and a local smoke script and runs offline
tests. **No deployment is performed.** There is no `gcloud`, no SSH, no container
build, no service restart, no systemd/service-file change, and no remote VM file
change. The running GCP service is untouched.

## Architecture reminder

- **GCP project:** `stock-prediction-app-466420`
- **VM:** `stock-prediction-vm-new`
- **Zone:** `us-central1-a`
- **Service:** `stock-api.service` (systemd)
- **Remote port:** `8000` (uvicorn)
- **Local tunnel:** `127.0.0.1:9000` → GCP VM port `8000`
- (Paper Trader's local backend is separate at `127.0.0.1:8001` and is **out of
  scope** for this phase.)

## Deployment principle

- **Deploy code only.** Ship the reviewed commit; change no behavior by default.
- **Keep `PREDICTOR_USE_MODEL_V2` unset/off.** The model-v2 path stays disabled
  and fail-closed; `/predict` and `/predict_all_models/` behave exactly as today.
- **No migration.** No DDL/DML, no Alembic, no schema change.
- **No DB write.** The flag-off path opens no DB connection and writes nothing.

## Pre-deploy checklist

1. On the deploy machine, confirm the checked-out commit is `58a05ee` or later
   (`git rev-parse HEAD`).
2. Run the offline readiness checks locally and confirm green:
   - `python tests/test_phase2_predeploy.py` → all pass.
   - `python scripts/phase2e_d_predeploy_check.py` → `safe_to_deploy_with_flag_off: true`.
   - `python tests/test_phase2_flagoff_deploy.py` → all pass.
3. Confirm the deploy will **not** set `PREDICTOR_USE_MODEL_V2` (it must remain
   unset in the service environment / unit file).
4. Confirm no migration step is part of the deploy pipeline.
5. Have the rollback target ready: the currently-deployed commit SHA.

## Deployment steps for the next phase — **DO NOT RUN IN THIS PHASE**

> The following is the forward plan for the actual deploy. **None of these
> commands are run in Phase 2E-E.** They are recorded here only so the next phase
> is unambiguous. The exact transport (gcloud SSH / CI runner / existing deploy
> script) follows the team's normal procedure.

1. **DO NOT RUN IN THIS PHASE** — Sync the reviewed commit `58a05ee` (or later)
   to the VM using the normal deploy path. No env change; do **not** add
   `PREDICTOR_USE_MODEL_V2`.
2. **DO NOT RUN IN THIS PHASE** — Restart `stock-api.service` (uvicorn on port
   `8000`) per the normal procedure.
3. **DO NOT RUN IN THIS PHASE** — Bring up the local tunnel `127.0.0.1:9000` →
   VM `8000`.
4. **DO NOT RUN IN THIS PHASE** — Run the post-deploy smoke (read-only):
   ```
   python scripts/phase2e_e_postdeploy_smoke.py --base-url http://127.0.0.1:9000
   ```
5. **DO NOT RUN IN THIS PHASE** — Keep the flag off through bake-in; monitor
   error rate and latency to confirm the additive wiring is inert.

## Post-deploy smoke checks

The smoke script (`scripts/phase2e_e_postdeploy_smoke.py`, stdlib only,
read-only) probes:

- `GET /ping`
- `GET /healthz`
- `GET /config`
- `GET /predict/{ticker}` (default ticker `AAPL`)
- `POST /predict_all_models/` (body only names the ticker to read)

It requires an explicit `--base-url`; point it at the local tunnel
(`http://127.0.0.1:9000`) or a staging URL.

## Expected behavior with the flag off

- **Legacy keys present** on the `/predict` responses: `recommendation`,
  `confidence`, `agreement`, `ensemble_day5`, `predictions`, `zscore`.
- **`model_v2` absent or false** — the model-v2 response path is **not active**.
- Behavior is byte-for-byte the same as the pre-2E-C service; the two-line gated
  check in `predict_all(...)` is a no-op because `model_v2_predict_single(...)`
  returns `None` before any lookup when the flag is off.

## Rollback plan

1. **Fastest (no redeploy):** the flag is already off, so there is nothing to
   disable. If anything looks off, redeploy/restart the **previous commit** and
   keep `PREDICTOR_USE_MODEL_V2` unset.
2. **Code rollback:** restore the previously-deployed commit SHA via the normal
   deploy path and restart `stock-api.service`. The 2E-C wiring is purely
   additive (gated block + two-line check), so reverting is clean with no schema
   or data dependency.
3. **Data:** none required — the flag-off path writes nothing, so there is no
   state to undo.
4. In all cases: **keep `PREDICTOR_USE_MODEL_V2` off.**

## Explicit statements

- **No deployment happened** in this phase. No `gcloud`, no SSH, no service
  restart, no remote VM or systemd/service-file change.
- **No migration was run.** No DDL/DML, no Alembic, no schema change.
- **No production database write happened.** No DB connection is opened on the
  flag-off path.
- **PREDICTOR_USE_MODEL_V2 must remain off.** The deploy is code-only and inert.
- **Do not enable model-v2 yet.** Enabling is gated to a later canary phase after
  DB population, behind the same fail-closed flag.

## Validation summary

- `tests/test_phase2_flagoff_deploy.py` — self-running (no pytest); proves the
  runbook and smoke script honor every flag-off / read-only guardrail.
- `research/output/phase2e_e_flagoff_deploy_plan_summary.json` —
  `safe_to_prepare_flagoff_deploy: true` with `deployment_executed`,
  `migration_executed`, `database_touched`, and `model_v2_enabled` all `false`.
- The smoke script itself does not run in this phase against any production URL;
  it requires an explicit `--base-url` and performs read-only requests only.
