# Phase 2K-Q — Populate Sector Map for Reconfirmed Leads (v1)

_Implemented by `research/analyze_phase2k_q_populate_sector_map.py` and validated by
`tests/test_phase2k_q_populate_sector_map.py`. Phase 2K-Q is a **sector-map population phase**,
not a retest and not model training. It reads the small Git-tracked Phase 2K-P result and its
generated template (plus the 2K-O / 2K-N summaries for provenance), populates a current-as-of
sector / industry map for the 128 equity tickers, validates coverage, and decides whether a
narrow, model-free sector-relative retest is now feasible._

> Scope: this phase reads only small C: repo files. It **does not read the expanded D:
> price-history CSV** — the equity universe is recovered from the Phase 2K-P template — and it
> reads from / writes to nothing on the D: drive. It writes two small files in the C: repo: the
> populated sector-map CSV (`research/input/phase2k_p_sector_map_current.csv`) and the results
> JSON. It **runs no sector-relative retest**, runs no broad alpha screen, computes no alpha
> signal or label, fetches nothing from the network (no third-party data fetch), and trains /
> fits / scores / creates no model. It is research tooling only: it **does not deploy**, it
> **does not restart stock-api.service**, it **does not enable** the model-v2 serving flag, it
> **does not run migrations**, it **does not write to production DB**, and it **does not trade**.
> No order placement, no automation, no model training, and no model candidate happen here, and
> it claims no **production edge**.

## Why Phase 2K-Q follows Phase 2K-P

Phase 2K-P was a sector-relative feasibility gate. It read the expanded D: price-history CSV
read-only for the 128-ticker equity universe (SPY excluded as benchmark), found no local sector
map, generated a 128-row populate-me template, and chose `SECTOR_MAP_TEMPLATE_CREATED` — routing
here to fill the map before any sector-relative retest. Before doing anything, Phase 2K-Q
confirms that routing (`phase == "2K-P"`,
`recommendation.recommendation == "SECTOR_MAP_TEMPLATE_CREATED"`,
`recommended_next_phase.phase == "2K-Q"` with the matching title,
`sector_map_template.generated == true`, `sector_map_template.row_count == 128`, and the
recommendation's `create_model_candidate_now` / `train_model_now` /
`run_sector_relative_retest_now` all `false`).

## The 128-row template from Phase 2K-P

Phase 2K-P emitted a one-row-per-equity template at
`research/output/phase2k_p_sector_map_template.csv` with the columns
`ticker, sector, industry, source, as_of_date, point_in_time, notes` — `ticker` filled and
`sector` / `industry` blank. Phase 2K-Q reads that template to recover the exact equity universe
(128 tickers, SPY excluded) and populates one row per ticker. The large D: CSV is **not** read
again here.

## Populating a current-as-of sector map

Phase 2K-Q fills the sector / industry columns for every template ticker from a **curated static
seed embedded offline** (standard GICS-style sector / industry labels), never fetched from the
network. Every populated row carries:

- `source = curated_static_mapping_current_as_of_2026_06_18`
- `as_of_date = 2026-06-18`
- `point_in_time = false`
- `notes = current-as-of sector map; not point-in-time; for caveated research only`

The populated map is written to `research/input/phase2k_p_sector_map_current.csv` — the input a
sector-relative retest would consume.

## The map is not point-in-time

This is the central caveat. The map reflects sector / industry membership **as of 2026-06-18**,
not the membership in force at each historical test date. `point_in_time` is `false` for every
row, and the `caveats` block records it explicitly. Combined with the survivorship-biased /
current-membership D: universe inherited from Phase 2K-G, any sector-relative result built on
this map remains survivorship-biased and is **not a production edge**. A clean point-in-time
classification would tighten, never rescue, a sub-floor edge.

## Sector and industry coverage

The populated map covers **100%** of the 128-ticker equity universe on both `sector` and
`industry`, with no duplicate tickers, no SPY row, all tickers uppercase, and `source` /
`as_of_date` populated on every row. The universe spans **11 distinct sectors** (well above the
minimum of 5): Information Technology, Health Care, Financials, Industrials, Consumer
Discretionary, Consumer Staples, Communication Services, Energy, Utilities, Materials, and Real
Estate. The full per-sector and per-industry counts are recorded in the `sector_distribution`
and `industry_distribution` blocks of the results JSON.

## Caveats

The results JSON records, at minimum:

- The map is **current-as-of 2026-06-18 and not point-in-time**.
- The D: equity universe is **survivorship-biased / current-membership**, so sector-relative
  results stay survivorship-biased and are not a production edge.
- Sector / industry labels are a **curated static mapping**, not a licensed point-in-time feed.
- **Small sectors** (fewer than 3 tickers — here Real Estate, with EQIX and PLD) are reported,
  never silently dropped; the retest must treat within-sector ranking there cautiously.

Because coverage is complete but these caveats are real, Phase 2K-Q selects
**`SECTOR_MAP_POPULATED_WITH_WARNINGS`** rather than claiming a clean
`SECTOR_MAP_POPULATED_READY_FOR_CAVEATED_RETEST`. Both route to the same next phase; the
warnings variant simply carries the current-as-of and small-sector caveats forward explicitly.

## Why no sector-relative retest is run yet

This phase only populates and validates the map. Running the retest here would conflate map
construction with measurement and bury the caveats. The retest is deferred to Phase 2K-R, which
must carry the current-as-of / survivorship caveats into its results. Phase 2K-Q sets
`sector_relative_retest_run = false` and `run_sector_relative_retest_now = false`.

## Why no model candidate is created yet

No lead met `KEEP_FOR_CONFIRMATION_DESIGN` in Phase 2K-N (0 KEEP, 3 reconfirmed), so the
model-candidate gate stays locked: no candidate may be created and no confirmation battery may
be designed until a feature first clears the IC floor in a model-free retest. A reconfirmed but
sub-floor lead is a research lead, not a confirmable edge and not a **production edge**. This
phase trains no model, fits nothing, creates no model candidate, and keeps the model-v2 serving
flag disabled.

## What Phase 2K-R should do

Phase 2K-R should run a **narrow, model-free sector-relative retest** of the 3 reconfirmed leads
(`residual_price_momentum_12_1@5d`, `short_horizon_residual_reversal_5d@21d`,
`short_horizon_residual_reversal_21d@21d`) using this populated current-as-of sector map —
re-ranking each lead within its sector — and report whether the within-sector residual rank IC
clears the 0.03 floor, with the current-as-of / survivorship caveats stated up front. It must
stay model-free, add no candidate, train no model, and design no model candidate. Like every
phase in this track, Phase 2K-R **does not deploy**, **does not restart stock-api.service**,
**does not enable** the model-v2 flag, **does not run migrations**, **does not write to
production DB**, and **does not trade**, and it claims no **production edge**.

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
d_drive_read             = false
d_drive_written          = false
broad_alpha_screen_run   = false
sector_relative_retest_run = false
network_used             = false
```

The `recommendation` block additionally records `create_model_candidate_now = false`,
`train_model_now = false`, `deploy_now = false`, `run_sector_relative_retest_now = false`, and
`production_edge_claimed = false`; the `interpretation` block records `ran_sector_relative_retest
= false`, `fetched_sector_data_from_network = false`, `read_from_d_drive = false`,
`wrote_to_d_drive = false`, and `sector_map_is_point_in_time = false`.

## Conclusion

Phase 2K-Q populated a current-as-of sector / industry map for the 128-ticker equity universe
recovered from the Phase 2K-P template, achieving 100% sector / industry coverage across 11
sectors with no duplicates and no SPY row, all explicitly flagged as current-as-of (not
point-in-time). It selected `SECTOR_MAP_POPULATED_WITH_WARNINGS` and routes to Phase 2K-R for a
caveated, model-free sector-relative retest. It read no D: CSV, ran no retest, ran no broad alpha
screen, trained no model, created no model candidate, fetched nothing from the network, and wrote
nothing to the D: drive; all results remain survivorship-biased / current-as-of and are not a
**production edge**.
