# Phase 2E-F — Flag-OFF Deployment Evidence (v1)

_Evidence package documenting the completed flag-off deployment of the prediction
service to GCP and the read-only post-deploy smoke that validated it. **This is
documentation only — no deployment, restart, migration, or database write is
performed by this phase.** `PREDICTOR_USE_MODEL_V2` remains unset/off._

## Objective

Capture verifiable, after-the-fact evidence that the Phase 2E-F deployment was a
**pure code-only, flag-off** rollout: the reviewed commit was deployed with the
model-v2 feature flag unset, the legacy contract stayed byte-for-byte intact, the
model-v2 path stayed inactive, and no migration or production database write
occurred. The proof is the read-only smoke summary captured against the live
service through the local tunnel.

## Deployment facts

- **Deployed commit:** `65193d4`
- **Service:** `stock-api.service` (systemd)
- **VM:** `stock-prediction-vm-new`
- **Project:** `stock-prediction-app-466420`
- **Zone:** `us-central1-a`
- **Remote port:** `8000` (uvicorn)
- **Tunnel URL used for smoke:** `http://127.0.0.1:9000` → VM port `8000`
- **Feature flag:** `PREDICTOR_USE_MODEL_V2` — **unset/off**
- **Smoke evidence:** `research/output/phase2e_f_smoke_run.json`
- **Smoke ticker:** `AAPL`

## Feature flag remained off

`PREDICTOR_USE_MODEL_V2` was **never set** in the service environment for this
deployment. The model-v2 serving path is gated behind this flag and is
fail-closed (unset/empty/`0`/`false`/`no`/`off`/unrecognized → off), so the live
behavior is identical to the pre-2E-C service. The smoke summary records
`model_v2_inactive: true`, confirming the model-v2 response path was not active.

## Smoke-test results (read-only, from `phase2e_f_smoke_run.json`)

All probes were issued read-only over HTTP against `http://127.0.0.1:9000`
(GETs plus a single read-POST that only names a ticker to read a prediction).

| Check | Result |
| --- | --- |
| `GET /ping` (`ping_ok`) | **passed** (`true`) |
| `GET /healthz` (`health_ok`) | **passed** (`true`) |
| `GET /config` (`config_ok`) | **passed** (`true`) |
| `GET /predict/{ticker}` (`predict_ok`) | **passed** (`true`) |
| `POST /predict_all_models/` (`predict_all_ok`) | **passed** (`true`) |
| Legacy keys present (`legacy_keys_present`) | **true** |
| Model-v2 inactive (`model_v2_inactive`) | **true** |
| `database_touched` | **false** |
| `migration_executed` | **false** |
| `deployment_executed` (by the smoke script) | **false** |
| `safe_flagoff_smoke` | **true** |

### `/ping`

`/ping` **passed** (`ping_ok: true`) — the service is up and answering.

### `/healthz`

`/healthz` **passed** (`health_ok: true`, HTTP 200). Per the `/healthz` contract
and the confirmed deployed-state report, the health payload reported **`db_ok:
true`** and **`fast_only: true`**, i.e. the database connectivity check is green
and the service is running in its fast-only serving mode.

### `/config`

`/config` **passed** (`config_ok: true`) — the runtime configuration endpoint
answered successfully.

### `/predict`

`GET /predict/AAPL` **passed** (`predict_ok: true`) and returned a JSON object.

### `/predict_all_models`

`POST /predict_all_models/` **passed** (`predict_all_ok: true`) and returned a
JSON object. The request body only named the ticker (`{"ticker": "AAPL"}`) to
read a prediction — no write/admin route was touched.

### Legacy keys present

`legacy_keys_present: true` — the legacy response keys existing consumers depend
on were all present on the predict responses: `recommendation`, `confidence`,
`agreement`, `ensemble_day5`, `predictions`, `zscore`.

### Model-v2 inactive

`model_v2_inactive: true` — the `model_v2` key was absent or false on the predict
responses; the model-v2 serving path was **not active** with the flag off.

## Safety confirmations

- **`database_touched: false`** — no database was written or mutated.
- **`migration_executed: false`** — no migration was run (no DDL/DML, no Alembic,
  no schema change).
- **No production database write happened** — the flag-off path opens no DB
  connection and writes nothing; the smoke is read-only by construction.
- **No model-v2 enablement happened** — `PREDICTOR_USE_MODEL_V2` was never set;
  `model_v2_inactive: true` confirms the path stayed off.

## Explicit statements

- **The Phase 2E-F deploy was flag-off only.** Commit `65193d4` was shipped as a
  pure code-only rollout with `PREDICTOR_USE_MODEL_V2` unset; behavior is
  byte-for-byte identical to the pre-2E-C service.
- **Do not enable model-v2 yet.** Enabling is gated to a later phase after offline
  real-data validation passes, behind the same fail-closed flag.

## Next step recommendation

**Phase 2G — real-data model-v2 validation (offline / shadow only, no trading).**
Validate the model-v2 path against real data entirely offline / in shadow mode:
no flag enablement on the live service, no trading, no broker orders, no
automation, no production database write. Compare model-v2 outputs to the legacy
ensemble on historical/real data, measure calibration and agreement, and produce
an offline validation report. Only after that passes would a separate, later
canary phase consider enabling the flag — still fail-closed to legacy.

## Provenance

This evidence doc is derived entirely from the already-captured smoke summary at
`research/output/phase2e_f_smoke_run.json`. No `gcloud`, no SSH, no service
restart, no migration, no database write, and no `api_server.py` / model changes
were performed to produce it.

## Validation phrase compatibility

The post-deploy smoke confirmed model_v2 inactive with the flag off.