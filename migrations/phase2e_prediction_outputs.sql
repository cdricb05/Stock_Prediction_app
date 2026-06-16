-- Phase 2E-A — storage layer schema for model-v2 (offline DESIGN only).
--
-- This migration is **idempotent** and **non-destructive**: it uses only
-- CREATE TABLE / INDEX IF NOT EXISTS. It contains NO DROP, NO table clearing, NO
-- DELETE, and NO destructive ALTER. It defines the two future tables that back
-- the Phase 2D artifact + prediction-output design:
--
--   * model_registry     -- one row per offline model version (Phase 2D metadata)
--   * prediction_outputs -- one row per (model_version, ticker, as_of_date)
--
-- IMPORTANT: this file is NOT executed in this phase. There is no automation,
-- no deploy, and no production database write here. It is shipped as a reviewed
-- artifact so Phase 2E-B can apply it behind the PREDICTOR_USE_MODEL_V2 flag.
-- Wrapped in a transaction so a manual apply is all-or-nothing.

BEGIN;

-- --------------------------------------------------------------------------- --
-- model_registry: reproducible description of one offline model version.
-- Mirrors model.registry.ModelVersionMetadata (Phase 2D). No pickle / no blobs;
-- the two summary objects are stored as JSONB so they stay queryable.
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS model_registry (
    model_version          TEXT        NOT NULL,
    feature_set_version    TEXT        NOT NULL,
    created_at             TIMESTAMP   NOT NULL,
    training_source        TEXT        NOT NULL,
    training_start_date    DATE,
    training_end_date      DATE,
    horizon_days           INTEGER     NOT NULL,
    model_name             TEXT        NOT NULL,
    calibration_method     TEXT,
    interval_method        TEXT,
    decision_gate_summary  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    metrics_summary        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    is_synthetic           BOOLEAN     NOT NULL DEFAULT TRUE,
    schema_version         TEXT,
    inserted_at            TIMESTAMP   NOT NULL DEFAULT now(),
    CONSTRAINT pk_model_registry PRIMARY KEY (model_version)
);

CREATE INDEX IF NOT EXISTS ix_model_registry_feature_set_version
    ON model_registry (feature_set_version);
CREATE INDEX IF NOT EXISTS ix_model_registry_is_synthetic
    ON model_registry (is_synthetic);

-- --------------------------------------------------------------------------- --
-- prediction_outputs: canonical offline prediction rows (Phase 2D schema).
-- The natural key (model_version, ticker, as_of_date) protects against
-- duplicate rows for the same model on the same instrument/day.
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS prediction_outputs (
    model_version               TEXT             NOT NULL,
    ticker                      TEXT             NOT NULL,
    as_of_date                  DATE             NOT NULL,
    generated_at                TIMESTAMP        NOT NULL,
    target_date                 DATE,
    feature_set_version         TEXT             NOT NULL,
    horizon_days                INTEGER          NOT NULL,
    score                       DOUBLE PRECISION,
    predicted_excess_return_5d  DOUBLE PRECISION,
    prob_outperform_spy         DOUBLE PRECISION,
    lo_return_5d                DOUBLE PRECISION,
    hi_return_5d                DOUBLE PRECISION,
    interval_width              DOUBLE PRECISION,
    risk_band                   TEXT,
    recommendation              TEXT,
    rank_in_universe            INTEGER,
    source                      TEXT,
    inserted_at                 TIMESTAMP        NOT NULL DEFAULT now(),
    -- Natural duplicate-protection key.
    CONSTRAINT pk_prediction_outputs
        PRIMARY KEY (model_version, ticker, as_of_date),
    -- Every prediction belongs to a registered model version.
    CONSTRAINT fk_prediction_outputs_model_version
        FOREIGN KEY (model_version)
        REFERENCES model_registry (model_version)
);

-- Common read patterns: latest cross-section by date, and per-ticker history.
CREATE INDEX IF NOT EXISTS ix_prediction_outputs_as_of_date
    ON prediction_outputs (as_of_date);
CREATE INDEX IF NOT EXISTS ix_prediction_outputs_ticker_as_of_date
    ON prediction_outputs (ticker, as_of_date);
CREATE INDEX IF NOT EXISTS ix_prediction_outputs_model_version
    ON prediction_outputs (model_version);

COMMIT;

