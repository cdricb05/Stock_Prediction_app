# Phase 5-D — External Data Alpha Enrichment Inventory & Point-in-Time Readiness (v1)

## Why Phase 5-D is necessary after Phase 5-C

Phase 5-C proved, with out-of-sample evidence, that the **price-only**
cross-sectional model is **not strong enough to deploy**: the shippable baseline
(`cross_sectional_composite_zscore`) had rank IC ≈ 0 and failed the worst-year
stability gate; the fitted ridge/logistic models showed only modest edge
(IC ≈ 0.035–0.045) on a survivorship-inflated universe. Its recommendation was
`PROCEED_TO_EXTERNAL_DATA_ALPHA_ENRICHMENT`.

Phase 5-D acts on that. It does **not** train a model and does **not** fabricate
data. It takes inventory of the **external (non-price) data that already lives in
this repo / on the read-only D: input drive**, decides what is point-in-time safe
and join-able to the Phase 5-C ticker/date panel without leakage, and produces a
machine-readable plan for **Phase 5-E** (the enriched feature panel + model rerun)
with go/no-go gates that compare against the Phase 5-C price-only baseline.

Runner: [`research/run_phase5d_external_data_alpha_enrichment.py`](../research/run_phase5d_external_data_alpha_enrichment.py).
Tests: [`tests/test_phase5d_external_data_alpha_enrichment.py`](../tests/test_phase5d_external_data_alpha_enrichment.py) (19 passing).
Report: [`research/output/phase5d_external_data_alpha_enrichment.json`](../research/output/phase5d_external_data_alpha_enrichment.json).

## What the scan found

205 CSVs were scanned across `research/input/`, `research/output/` (recursively),
and the read-only D: price file. To keep the inventory to genuine **candidate
alpha datasets** rather than the hundreds of prior diagnostic artifacts (IC
tables, decision tables, manifests), `research/output/` inclusion is a positive
allow-list of real data-panel filenames; `research/input/` is taken whole.
**28 datasets** were inventoried.

| family | count | point-in-time | join | verdict |
|---|---|---|---|---|
| price_history (D: free CSV) | 1 | safe | ticker+date | baseline source (5-C) |
| fundamentals (SEC, Phase 3-L/3-F/3-I) | 4 | **safe** (`active_feature_asof_date`) | ticker+date as-of | **use in 5-E** |
| macro (FRED: CPI, fed funds, yields, oil, USD) | 12 | potentially (needs release lag) | date-only as-of | use in 5-E |
| earnings (Phase 3-M events/features) | 3 | safe (`availability_date`); partial coverage | ticker+date as-of | use in 5-E (partial) |
| sector map (current-as-of) | 2 | **not** PIT | ticker static | caveated only |
| universe mapping (current member) | 1 | **not** PIT | ticker static | caveated only |
| global ETF cross-asset | 3 | header-only / unpopulated | date-only | future collection |
| analyst_revisions | 1 | stub only | — | **missing** |
| news_sentiment | 1 | stub only | — | **missing** |

Counts: **discovered 28, usable-now 19, point-in-time-safe 8, high-leakage-risk 3**.

## Which sources are usable now

- **SEC fundamentals (strongest ready-now family).** Phase 3-L
  `aligned_feature_price_panel_universe.csv` (≈303k rows, 128 tickers) already
  aligns quality/value/growth fundamentals to the price panel **point-in-time**
  via `active_feature_asof_date` (SEC filing-acceptance lag applied, never the
  fiscal-period end) and even carries the forward labels. Joins ticker+date as-of.
  Leakage risk low. `fundamentals_universe.csv` and the sample panels corroborate.
- **Macro regime.** Local FRED series (`CPIAUCSL`, `FEDFUNDS`,
  `macro_treasury_yields`/`DGS10`/`DGS2`, `DCOILWTICO`, `DTWEXBGS`) plus the
  Phase 3-R `macro_feature_registry` (17 PIT-safe features). Date-only as-of join
  (regime conditioning, identical across tickers on a date). **Caveat:**
  `observation_date` is the reference-period date, not the release date — a
  release/availability lag (release date if present, else a default 21-day lag for
  monthly series) must be applied before joining; hence classified
  *potentially* point-in-time, not unconditionally safe.
- **Earnings surprise (partial).** Phase 3-M `earnings_events_universe.csv` (≈5.6k
  rows) and `earnings_features_universe.csv` carry `reported_date` /
  `availability_date` and are PIT-safe as-of joins — but coverage is partial
  (the `combined_fundamental_earnings_panel.csv` is header-only) and must be used
  with a per-ticker coverage flag, never imputed.

## Which are blocked by point-in-time / leakage risk

- **Sector map & universe mapping** are **current-as-of 2026 snapshots**, not
  point-in-time membership/classification. Applying them to past dates injects
  look-ahead + survivorship bias. Usable only for explicitly *caveated*
  sector-relative features; never as point-in-time membership. (High leakage risk.)
- **Global ETF cross-asset** panels exist as schema shells but are **header-only**
  (the manual import was never populated; the Phase 3-Y AlphaVantage collector is
  out of scope here — no network). Blocked until populated.

## What is missing (must be collected later — never fabricated)

The report's `missing_critical_sources`:

1. **analyst_estimate_revisions** — needs ticker + revision date + estimate value.
   Only a Phase 3-S inventory stub exists (no real series).
2. **news_sentiment** — needs article timestamp + sentiment score. Stub only.
3. **options_implied_volatility** — IV level/skew/put-call; no local data.
4. **point_in_time_index_constituents** — add/drop-dated S&P 500 membership; needed
   to remove the survivorship bias that caps every absolute-return claim.

Also partial: **earnings full-universe coverage** and a **macro release calendar**
(to replace the default availability lag).

## Candidate feature families for Phase 5-E

Priority order in `phase5d_candidate_feature_families.csv`:

1. `sec_fundamental_quality_value_growth` — **ready_now**
2. `macro_regime` — **ready_now**
3. `sector_relative` — **caveated** (current-as-of sector map)
4. `earnings_surprise_event` — **partial**
5. `cross_asset_global_etf_regime` — blocked (unpopulated)
6. `analyst_estimate_revisions` — blocked (missing)
7. `news_sentiment` — blocked (missing)
8. `options_implied_volatility` — blocked (missing)

## Why no data is fabricated

The honest finding is that the strongest documented cross-sectional alpha families
beyond price (analyst revisions, sentiment, options) are **absent** locally. Faking
them would produce a model that looks better in-sample and fails live — the exact
failure mode Phase 5-C was built to avoid. Where data is missing it is reported as
`MISSING` / `future_collection_needed`; where partial (earnings, global ETF) that
is stated. The report carries `data_fabricated: false`.

## How Phase 5-E will test whether external data improves edge

`phase5d_phase5e_implementation_plan.json` specifies an **incremental-edge test**,
not a foregone win. Critically, the prior multisignal attempt (**Phase 3-P**) found
its best model was `ridge_technical_only @126d` (worst-year IC −0.108, FAIL) —
adding fundamentals/earnings did **not** beat price-only at the headline. So
Phase 5-E must:

- Hold the universe, rebalance dates, walk-forward folds, embargo (≥20), and models
  **identical** to Phase 5-C; the only change is appending point-in-time external
  features.
- Read: the D: price panel, the Phase 3-L fundamentals panel, the macro inputs +
  Phase 3-R registry, the caveated sector map, the Phase 3-M earnings features, and
  `phase5c_model_scoreboard.csv` (the baseline to beat).
- Apply leakage controls: as-of merges only; macro availability lag; fundamentals
  filing-acceptance + staleness cap; per-date cross-sectional standardization;
  placebo label-shuffle; survivorship gating of absolute returns.
- Compare `delta_mean_rank_ic = enriched − price_only` on the primary label
  `forward_excess_return_20d_vs_spy`, plus decile spread, worst-year IC, and
  cost-adjusted return.

**Go/no-go gates:** `enriched_beats_baseline_ic` (Δ mean rank IC ≥ +0.01 on the
primary horizon AND worst-year not worse than baseline), `incremental_edge_is_
leakage_clean`, `coverage_sufficient`, `survivorship_addressed_or_flagged`,
`no_fabricated_data`, `safety_contract`. If external data does **not** beat
price-only, that is reported honestly and the missing families are collected before
any deployment.

## Recommendation

**`PROCEED_TO_ENRICHED_ALPHA_PANEL`** — point-in-time external data (SEC
fundamentals + macro, with partial earnings) is available now and join-able without
leakage, so Phase 5-E is warranted. It is framed strictly as an incremental-edge
test over the Phase 5-C price-only baseline, with the survivorship caveat intact and
analyst-revision / sentiment / options-IV / point-in-time-constituent data still to
be collected.

## Outputs

- `phase5d_external_data_alpha_enrichment.json` — full report.
- `phase5d_external_data_inventory.csv` — every discovered dataset + classification.
- `phase5d_point_in_time_readiness.csv` — PIT status / leakage per source.
- `phase5d_candidate_feature_families.csv` — Phase 5-E feature-family plan.
- `phase5d_joinability_matrix.csv` — join type + look-ahead/stale/survivorship/coverage risk.
- `phase5d_missing_alpha_sources.csv` — what is missing and what to collect.
- `phase5d_phase5e_implementation_plan.json` — machine-readable next-phase plan.

## Reproduce (Windows PowerShell only)

```powershell
Set-Location "C:\Users\binis\Stock_Prediction_app_push"
python research\run_phase5d_external_data_alpha_enrichment.py
python -m pytest tests\test_phase5d_external_data_alpha_enrichment.py -q
```

## Safety contract

Research/inventory only. `preview_only=true`; `orders_enabled`,
`automation_enabled`, `broker_execution_enabled`, `production_replacement` all
`false`. No network, no paid APIs, no AlphaVantage, no deploy, no Paper Trader
changes, no GCP changes, no writes to D: (read-only input), no fabricated data, no
binary model artifacts, no commit.
