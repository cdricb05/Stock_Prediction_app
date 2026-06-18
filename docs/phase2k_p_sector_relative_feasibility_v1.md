# Phase 2K-P — Sector-Relative Feature Feasibility for Reconfirmed Leads (v1)

_Implemented by `research/analyze_phase2k_p_sector_relative_feasibility.py` and validated by
`tests/test_phase2k_p_sector_relative_feasibility.py`. Phase 2K-P is a **feasibility phase**,
not a retest and not model training. It reads the small Git-tracked Phase 2K-O / 2K-N / 2K-H
result summaries, reads the expanded D: price-history CSV **read-only** for the ticker universe
and date coverage only, checks for an optional local sector map (generating a small template if
none exists), and decides whether a sector-relative retest of the 3 reconfirmed leads is
feasible._

> Scope: this phase reads the small upstream JSON summaries and the expanded D: dataset
> read-only (the price-history CSV only for ticker / date columns, plus its small metadata
> JSONs), and writes two small files in the C: repo — the results JSON and, when no local
> sector map exists, the sector-map template CSV. It **runs no sector-relative retest**, runs
> no broad alpha screen, computes no alpha signal or label, fetches nothing from the network
> (no yfinance), and writes nothing to the D: drive. It is research tooling only: it **does not
> deploy**, it **does not restart stock-api.service**, it **does not enable** the model-v2
> serving flag, it **does not run migrations**, it **does not write to production DB**, and it
> **does not trade**. No order placement, no automation, no model training, and no model
> candidate happen here, and it claims no **production edge**.

## Why Phase 2K-P follows Phase 2K-O

Phase 2K-O was a decision gate over the Phase 2K-N narrow model-free retest. All 3 reconfirmed
leads reproduced a positive, above-zero (by bootstrap), year-stable, regime-stable, and
non-concentrated residual rank IC, but every one stayed below the 0.03 confirmation floor — so
the single dominant blocker is **sub-floor residual IC**, not regime dependence, concentration,
instability, or the survivorship caveat. Phase 2K-O therefore chose
`PROCEED_TO_SECTOR_RELATIVE_FEASIBILITY` and routed here. Before doing anything, Phase 2K-P
confirms that routing (`phase == "2K-O"`,
`recommendation.recommendation == "PROCEED_TO_SECTOR_RELATIVE_FEASIBILITY"`,
`selected_path.option == "SECTOR_RELATIVE_FEATURE_FEASIBILITY"`,
`recommended_next_phase.phase == "2K-P"` with the matching title, and the recommendation's
`create_model_candidate_now` / `train_model_now` both `false`).

## The 3 reconfirmed leads

| Lead | Candidate | Category | Horizon | Prior mean residual IC |
|------|-----------|----------|---------|------------------------|
| `residual_price_momentum_12_1@5d` | `residual_price_momentum_12_1` | price_momentum_alternatives | 5d | 0.0132 |
| `short_horizon_residual_reversal_5d@21d` | `short_horizon_residual_reversal_5d` | short_term_reversal_mean_reversion | 21d | 0.0103 |
| `short_horizon_residual_reversal_21d@21d` | `short_horizon_residual_reversal_21d` | short_term_reversal_mean_reversion | 21d | 0.0151 |

All three are price / reversal factors that admit a standard sector / industry neutralization —
exactly the cheap transformation Phase 2K-O wants assessed before paid point-in-time data or a
backlog stop.

## What sector-relative testing would mean

A sector-relative variant re-ranks each lead **within its sector** (or against a sector-neutral
benchmark) so the residual edge is measured cross-sectionally inside sectors rather than across
the whole universe. The hypothesis is that neutralizing sector exposure lifts the residual rank
IC over the floor without adding a new candidate family or training a model. Phase 2K-P only
assesses whether this is feasible; the actual retest is deferred to Phase 2K-Q.

## D: universe extraction

Phase 2K-P reads the expanded D: price-history CSV
(`phase2k_g_expanded_price_history_free.csv`) **read-only**, loading only the `ticker` and
`date` columns. It extracts the unique ticker list, excludes `SPY` from the equity universe
(keeping it as benchmark metadata), and records `ticker_count`, `date_count`, `start_date`,
`end_date`, and `rows_loaded`. On the current panel this is **128 equity tickers** (129 panel
tickers minus the SPY benchmark) across 2,628 trading days (2016-01-04 → 2026-06-16, 338,169
rows). The small D: metadata JSONs (data-quality, build-summary, survivorship-caveat) are read
only to record the `current_as_of` membership basis and the standing survivorship caveat. No
alpha signal, no label, and no retest are computed.

## Optional sector-map input schema

If `research/input/phase2k_p_sector_map_current.csv` exists it is read and validated (never
fetched from the network). Required columns:

```
ticker, sector, industry, source, as_of_date, point_in_time, notes
```

Validation checks every ticker is uppercase and unique, the coverage of the D: equity universe,
sector / industry non-null coverage, the number of missing and extra tickers, the distinct
sector count and minimum tickers per sector, and whether `point_in_time` is true for all rows.
A current-as-of (non-point-in-time) map is **not** disqualifying for caveated research, but it
is clearly flagged as current-as-of metadata.

## Template generation behavior

When no local sector map exists, Phase 2K-P generates a small helper template at
`research/output/phase2k_p_sector_map_template.csv` — one row per equity ticker, with `ticker`
filled, `sector` / `industry` left blank, `source` set to `manual_required`, `point_in_time`
set to `false`, and `notes` set to `fill sector/industry before sector-relative retest`. This
is a small file (one row per equity ticker, SPY excluded); no large file is written and nothing
is written to the D: drive.

## Feasibility rules

The recommendation is exactly one of:

- **`SECTOR_MAP_READY_FOR_CAVEATED_RETEST`** — a sector map exists, covers ≥ 95% of the equity
  universe across at least 5 sectors with at least 3 tickers per sector, has all required
  columns, and a low missing-ticker count; it may be used as caveated current-as-of metadata.
- **`SECTOR_MAP_TEMPLATE_CREATED`** — no sector map exists and the template was generated.
- **`SECTOR_MAP_INCOMPLETE`** — a sector map exists but its coverage is below threshold or it is
  missing key columns.
- **`REQUIRE_POINT_IN_TIME_SECTOR_DATA`** — a current-as-of map would be misleading or
  insufficient for the next test.
- **`NO_ACTION_FEASIBILITY_BLOCKED`** — Phase 2K-O did not route here, or the D: universe cannot
  be read.

## Selected recommendation

**Recommendation: `SECTOR_MAP_TEMPLATE_CREATED`.** No local sector map was found, so the
analyzer generated a 128-row template (one row per equity ticker, SPY excluded as benchmark) at
`research/output/phase2k_p_sector_map_template.csv`. The Phase 2K-O routing is confirmed and the
D: equity universe is readable, so the gate routes to **Phase 2K-Q — _Populate Sector Map for
Reconfirmed Leads_**: fill or source the sector / industry map before any sector-relative
retest.

## Why no sector-relative retest is run yet

This phase is feasibility only. A sector-relative retest needs a populated sector / industry
map, which does not yet exist — the template must be filled first. Running a retest now would
either fabricate sectors or silently neutralize against an empty map, so the retest is deferred
to Phase 2K-Q. Phase 2K-P sets `sector_relative_retest_run = false` and
`run_sector_relative_retest_now = false`.

## Why no model candidate is created yet

No lead met `KEEP_FOR_CONFIRMATION_DESIGN` in Phase 2K-N (0 KEEP, 3 reconfirmed), so the
model-candidate gate stays locked: no candidate may be created and no confirmation battery may
be designed until a feature first clears the IC floor in a model-free retest. A reconfirmed but
sub-floor lead is a research lead, not a confirmable edge and not a **production edge**. This
phase trains no model, fits nothing, creates no model candidate, and keeps the model-v2 serving
flag disabled.

## What Phase 2K-Q should do

Phase 2K-Q should populate (fill or source) the sector / industry map produced as a template
here, validating coverage against the same D: equity universe, and only then decide whether a
narrow, model-free sector-relative retest of the 3 reconfirmed leads can run. It must stay
model-free, add no candidate, train no model, and design no model candidate. Like every phase
in this track, Phase 2K-Q **does not deploy**, **does not restart stock-api.service**, **does
not enable** the model-v2 flag, **does not run migrations**, **does not write to production
DB**, and **does not trade**, and it claims no **production edge**.

## Safety flags (from the results JSON)

```
database_touched         = false
database_write_executed  = false
migration_executed       = false
deployment_executed      = false
model_v2_enabled         = false
production_edge_claimed  = false
no_trading               = true
no_orders                = true
no_automation            = true
model_trained            = false
model_candidate_created  = false
d_drive_read             = true
d_drive_written          = false
broad_alpha_screen_run   = false
sector_relative_retest_run = false
network_used             = false
```

The `recommendation` block additionally records `create_model_candidate_now = false`,
`train_model_now = false`, `deploy_now = false`, `run_sector_relative_retest_now = false`, and
`production_edge_claimed = false`; the `interpretation` block records `ran_sector_relative_retest
= false`, `fetched_sector_data_from_network = false`, and `wrote_to_d_drive = false`.

## Conclusion

Phase 2K-P is a fast sector-relative feasibility check after the Phase 2K-O decision gate. It
read the D: price-history CSV read-only for the 128-ticker equity universe (SPY excluded as
benchmark), found no local sector map, and generated a populate-me template — routing to Phase
2K-Q to fill the map before any sector-relative retest. It ran no retest, ran no broad alpha
screen, trained no model, created no model candidate, fetched nothing from the network, and
wrote nothing to the D: drive; all results remain survivorship-biased / current-as-of and are
not a **production edge**.
