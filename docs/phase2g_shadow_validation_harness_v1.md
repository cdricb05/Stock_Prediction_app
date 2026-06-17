# Phase 2G-A — Model-v2 Shadow Validation Harness (v1)

_An offline, read-only harness that evaluates the Phase 2 model-v2 ranking
pipeline on price data supplied as a CSV export. **This phase does not deploy,
does not enable the feature flag, does not run migrations, does not write to the
production database, and does not trade, place orders, or automate anything.** It
builds the harness and validates it on a small deterministic fixture only._

## Objective

Provide a single, repeatable command that takes a price CSV
(`ticker, date, adj_close`, optional `volume`), builds point-in-time features
with the existing Phase 2A feature builder, scores each row with a transparent
model-v2-style ranking composite, joins the realized 5-day forward labels, and
emits shadow-style ranking metrics (rank IC, top-decile excess return vs the
universe, hit rate, score-bucket monotonicity, optional probability buckets) as a
JSON summary plus the scored rows as CSV.

This is the measurement tool that **Phase 2G-B** will later point at a read-only
export of the real GCP price history. In Phase 2G-A the harness is exercised on a
deterministic fixture only, so its output is explicitly marked `fixture_only:
true` and is **not production edge**.

## Why this is offline / shadow only

- The model-v2 path stays **off** in production. Nothing here is on the request
  path; the harness never imports or modifies `api_server.py`, never imports
  Paper Trader, and never enables `PREDICTOR_USE_MODEL_V2`.
- Default behavior is **file-based and offline**: input is a CSV, output is a
  JSON + CSV on local disk. No database connection is opened by default; any
  future optional DB path must be **read-only, explicit, and disabled by
  default**.
- "Shadow" means we score and grade historical rows after the fact against
  realized labels — we never act on a prediction. There is no trading, no order,
  no broker, and no automation in this harness.

## Required CSV schema

| column | required | meaning |
| --- | --- | --- |
| `ticker` | yes | instrument symbol (include the benchmark, e.g. `SPY`) |
| `date` | yes | session date (`YYYY-MM-DD` or any pandas-parseable date) |
| `adj_close` | yes | split/dividend-adjusted close; rows with null or ≤ 0 are dropped |
| `volume` | no | session volume; volume features are emitted only when a real, fully-populated column is present, otherwise reported unavailable (never fabricated) |

The benchmark ticker (default `SPY`) must be present in the CSV for excess-return
labels. If it is absent, the harness falls back to the raw realized return as the
ranking target and records `target_metric` accordingly.

## How to run locally against a CSV export

```bash
# Validate a real, read-only price export (Phase 2G-B usage):
python research/run_phase2g_shadow_validation.py \
    --input-csv path/to/prices_export.csv \
    --output-json research/output/phase2g_shadow_validation.json \
    --scored-csv research/output/phase2g_shadow_scored.csv \
    --real-data

# Regenerate the deterministic fixture + sample artifacts (this phase):
python research/run_phase2g_shadow_validation.py
# -> writes research/output/phase2g_shadow_validation_sample.csv
#    and  research/output/phase2g_shadow_validation_sample.json
```

`--real-data` is the **only** way to set `fixture_only: false`; without it the
input is treated as a fixture (fail-safe toward "not real / not edge"). The DB is
never written and no flag is ever enabled regardless of the flags passed.

## What the metrics mean

- **rank_ic** — Spearman rank correlation between the composite score and the
  realized target (excess return vs SPY, or raw return on fallback). The single
  headline measure of ranking skill; the plan §8 gate-1 floor is `0.03`.
- **top_decile_mean_excess_return** — per as-of date, the mean realized target of
  the top 10% of names by score, averaged across dates (equal weight per date).
- **universe_mean_excess_return** — the per-date mean realized target across all
  names, averaged across dates: the "hold everything" baseline the top decile
  must beat to add value.
- **top_decile_hit_rate** — fraction of top-decile names with a positive realized
  target, averaged across dates.
- **bucket_monotonic** — whether mean realized target is non-decreasing across
  score quintiles (a coarse "does a higher score mean a better outcome" check).
- **probability_bucket_summary** — when a probability column exists, the realized
  outperform rate per probability decile. The probability is a transparent rank
  percentile of the score, **not a calibrated probability**, so this is a
  diagnostic, not a calibration proof.
- **legacy_comparison_available** — whether a legacy-prediction CSV was supplied
  to compare against (false in this phase).
- **safe_for_canary** — a conservative precondition flag (see below).

## Canary gate criteria

`safe_for_canary` is **false for all fixture/sample output** and is only `true`
for a real run that **simultaneously**:

1. is real data (`--real-data`, so `fixture_only` is false),
2. has a legacy comparison available (`legacy_comparison_available` true),
3. clears the rank-IC floor (`rank_ic >= 0.03`),
4. shows monotone score buckets (`bucket_monotonic` true), and
5. has the top decile beat the universe (`top_decile_mean_excess_return >
   universe_mean_excess_return`).

Even when `safe_for_canary` is true it is a *candidate* signal only: enabling
`PREDICTOR_USE_MODEL_V2` remains a **separate, later, human-approved canary
phase** that stays fail-closed to legacy. This harness never enables anything.

## Explicit guardrail statements

- **This phase does not deploy.** No `gcloud`, no SSH, no service restart, no
  systemd change, no remote VM change.
- **This phase does not enable `PREDICTOR_USE_MODEL_V2`.** The summary always
  records `model_v2_enabled: false`.
- **This phase does not run migrations.** No DDL/DML, no Alembic, no schema change.
- **This phase does not write to the production database.** No DB connection is
  opened by default; the only optional DB path would be read-only and is disabled
  by default.
- **This phase does not trade, create orders, or automate.** No broker, no order
  placement, no scheduler; the summary records `no_trading`, `no_orders`, and
  `no_automation` all true.
- **Fixture/sample output is not production edge.** It is marked `fixture_only:
  true`, `production_edge_claimed: false`, and `safe_for_canary: false`; any skill
  shown on the fixture is the planted synthetic signal being recovered, which only
  proves the pipeline runs.

## Next step

**Phase 2G-B — run this harness against a real DB export / read-only dataset.**
Produce a read-only export of the real GCP price history (no write-back, no
migration), run this exact harness with `--real-data` and a legacy-comparison
CSV, and review the resulting `rank_ic`, top-decile-vs-universe, bucket
monotonicity, and `safe_for_canary` against the canary gate criteria above —
still offline, still flag-off, still no trading.

## Validation phrase compatibility

This phase does not write to production DB.