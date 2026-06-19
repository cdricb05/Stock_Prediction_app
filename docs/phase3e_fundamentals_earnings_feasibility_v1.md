# Phase 3-E — Fundamentals and Earnings Data Feasibility (v1)

_Implemented by `research/analyze_phase3e_fundamentals_earnings_feasibility.py` and validated by
`tests/test_phase3e_fundamentals_earnings_feasibility.py`. Phase 3-E is a **feasibility /
planning phase**, not a training phase. After the Phase 3-D external-data decision selected the
structured-company-data track, it answers one question — **what exact fundamentals / earnings /
analyst-revisions data contract do we need before external-data model research can start, and
what is the safest next implementation step?**_

> Scope and safety. This phase fetches nothing from the network, calls no data vendor, purchases
> no data, ingests no external data, trains **no model**, creates **no production model
> candidate**, writes **no deployable model artifact**, reads nothing on the D: drive, and writes
> nothing to the D: drive. It reads small committed JSON/CSV inputs and writes four small files
> under `research/output`. It is research / planning tooling: it **does not deploy**, it **does
> not restart stock-api.service**, it **does not enable** the model-v2 serving flag, it **does not
> run migrations**, it **does not write to production DB**, and it **does not trade**. No order
> placement, no automation, no model training, and no data acquisition happen here, and it claims
> no **production edge**.

## Why Phase 3-E follows Phase 3-D

Phase 3-D scored eleven external-data families on a transparent 1–5 matrix and emitted
`PROCEED_TO_FUNDAMENTALS_EARNINGS_DATA_FEASIBILITY`, selecting **Fundamentals + Earnings +
Estimate Revisions** as the first external-data track and routing explicitly to Phase 3-E
("Fundamentals and Earnings Data Feasibility"). Phase 3-E is the disciplined next step: before
any data is fetched or any model is built, it defines exactly *what data contract is required* —
the vendor-agnostic schema, point-in-time rules, coverage gates, provider requirements, and
ingestion contract.

## Why price/volume-only modeling stopped

Upstream, Phase 3-C reran the refined greenfield configuration through a stricter, kill-switched
walk-forward and emitted `PRICE_VOLUME_ONLY_KILL_SWITCH_TRIGGERED`. Sector neutralization fixed
the concentration problem, but risk-neutralizing the beta / volatility / correlation tilt — the
very exposure that had driven the apparent signal — collapsed the primary 21-day refined ridge to
a near-zero mean rank IC (~0.004), a sub-0.60 fold win rate (~0.53), and three catastrophic folds.
There is **no robust residual price/volume alpha**, so the next edge must come from structured
external data rather than further price/volume tuning.

## Selected structured company-data track

The track is the bundle Phase 3-D selected: **fundamentals**, **earnings events**, and **analyst
estimates and revisions**. These are structured, can be made point-in-time, map naturally onto
cross-sectional equity ranking over the 128-ticker universe, and convert cleanly into trailing-only
features: valuation, quality, profitability, growth, leverage, accruals, capital returns, earnings
surprise (SUE), post-earnings-announcement drift, estimate-revision momentum, estimate dispersion,
and recommendation momentum.

## Schema catalog

The vendor-agnostic schema catalog (`research/output/phase3e_external_data_schema_catalog.csv`)
defines fields across five families, each row carrying `field_name`, `data_family`, `table_name`,
`required_or_optional`, `data_type`, `periodicity`, `point_in_time_required`,
`availability_timestamp_required`, `restatement_sensitive`, `can_be_lagged`, `example_feature_use`,
and `validation_note`:

- **company_identity** — ticker, company_name, cik, figi, exchange, sector, industry,
  effective_from, effective_to, source (effective-dated so historical joins are point-in-time).
- **fundamentals** — keys / timing (fiscal_period_end, fiscal_year, fiscal_quarter, filing_date,
  accepted_datetime, availability_datetime) plus income-statement, balance-sheet, and cash-flow
  line items (revenue, operating_income, net_income, eps_diluted, total_assets, shareholder_equity,
  operating_cash_flow, free_cash_flow, and more).
- **earnings_events** — announcement datetime and timing, reported vs pre-announcement consensus
  EPS / revenue, surprises, availability_datetime, source.
- **analyst_estimates_revisions** — consensus level / dispersion / analyst_count, trailing
  up/down revision counts and net-revision ratios, consensus changes, availability_datetime, source.
- **derived_feature_blocks** — the feature blocks to build later (valuation, quality, growth,
  leverage, accruals, capital returns, earnings surprise, post-earnings-announcement drift,
  estimate-revision momentum, estimate dispersion, recommendation momentum).

The schema is vendor-agnostic: fields are defined independently of any specific provider, and every
fundamentals / earnings / estimate field carries an explicit `availability_datetime`.

## Point-in-time rules

- No feature may use a value before its `availability_datetime`; `feature_asof_date` must be `<=`
  `scoring_date` for every row used on that date.
- For SEC filings, `accepted_datetime` (filing acceptance time) is the earliest safe
  `availability_datetime`; `fiscal_period_end` is **not** an availability date.
- For earnings, the announcement datetime plus timing (before_open / after_close / intraday /
  unknown) determines the first tradable date.
- For analyst revisions, each consensus snapshot must be timestamped to when the estimate changed
  or first became known; the earnings surprise must use the pre-announcement consensus.
- Restated fundamentals must not be used historically unless the provider offers as-reported
  point-in-time history; if only restated values exist, the track is marked caveated / prototype
  only.
- Every target label must remain strictly forward of the `feature_asof_date`.

## Minimum coverage gates

Before any modeling: ticker coverage ≥ 95% of the 128-ticker universe; history from ≥ 2016-01-01
to latest (or documented gaps); quarterly fundamentals ≥ 90% of ticker-quarter pairs; earnings
dates ≥ 90%; estimates / revisions ≥ 80% if included in the first model; no duplicate
ticker-period-source records unless versioned; 100% of records carry `availability_datetime`;
restatement handling explicitly documented; provider license permits local research storage; and
ingestion runs without production database writes.

## Provider requirements matrix

The provider requirements matrix (`research/output/phase3e_provider_requirements_matrix.csv`) is a
**requirements / evaluation template**, not a live vendor research result. It does not browse the
web, assert current prices, or claim provider features are currently available. It evaluates six
categories — SEC EDGAR / company-facts fundamentals, low-cost market-data APIs, dedicated
fundamentals / estimates APIs, professional institutional providers, earnings-calendar providers,
and estimate-revision providers — against required capabilities (point-in-time, as-reported,
estimate-revision, earnings-timestamp support), a cost-band placeholder, an implementation-
complexity estimate, licensing / data-quality questions, and a Phase 3-F fit flag. SEC fundamentals
are the strongest free first prototype source; clean point-in-time earnings consensus and analyst
revisions most likely require a paid provider that still needs manual research and selection.

## Ingestion contract

The ingestion contract (`research/output/phase3e_ingestion_contract.json`) defines source/version/
extraction-timestamp placeholders, a repo-local raw-file location (never the D: drive), normalized
tables and their schemas, primary keys and unique constraints, point-in-time columns, restatement
policy (store first-reported values; keep restated values only as separate versioned rows),
availability-datetime policy, ticker-mapping / currency / split-adjustment policies, data-quality /
coverage / leakage checks, allowed output locations (`research/output/` only), an explicit
forbidden-actions list, and `no_database_write_by_default: true` / `no_production_integration: true`.

## Feasibility score

The selected track is scored 1–5 (5 = most favorable; cost / complexity / leakage framed so that 5
is the safest outcome) across nine criteria: point-in-time feasibility (4), schema completeness (5),
coverage feasibility (4), cost risk (3), implementation complexity (4), leakage risk (4),
walk-forward compatibility (5), expected incremental predictive value (4), and readiness for an
ingestion prototype (4). The schema and point-in-time rules are clear and a free SEC fundamentals
prototype is buildable now; the open item is that clean point-in-time earnings consensus and analyst
revisions still require a selected provider.

## Selected recommendation

**`PROCEED_TO_VENDOR_SELECTION_AND_FREE_SOURCE_PROTOTYPE` → Phase 3-F (Fundamentals Source
Selection and Free Prototype).** The schema and point-in-time rules are clear and a free
fundamentals prototype can be built from SEC filing-acceptance data, but earnings consensus and
analyst revisions still need a selected provider. Phase 3-F should choose the first no/low-cost
fundamentals source, build a read-only local prototype downloader / normalizer for a tiny sample
only, and separately document what is still missing for earnings and analyst revisions. The
recommendation sets `create_production_model_candidate_now`, `train_production_model_now`,
`deploy_now`, and `production_edge_claimed` all false, with `provider_selection_required: true` and
`free_source_prototype_allowed: true`.

## Risks and caveats

Restatement / point-in-time risk; consensus-snapshot leakage; cost risk (clean point-in-time
consensus / revisions usually require a paid provider while SEC covers fundamentals for free);
coverage gaps across the universe or earlier history; licensing limits on local storage /
redistribution; persistent survivorship bias (universe and sector map are current-as-of); and
provider-availability uncertainty — this blueprint asserts no current vendor pricing or
availability, which must be confirmed by manual research in Phase 3-F.

## Why no model is trained and no production model candidate is created

Phase 3-E is a feasibility / planning artifact. It defines the data contract and the safest next
implementation step; it does not touch data beyond the committed result files, ingests no external
data, trains nothing, and promotes nothing. It sets `research_model_trained = false`,
`production_model_trained = false`, `production_model_candidate_created = false`,
`deployable_model_artifact_written = false`, and `external_data_ingested = false`, keeps the
model-v2 serving flag disabled, makes no network / vendor / purchase call, and claims no
**production edge**.

## What Phase 3-F should do

Run **Fundamentals Source Selection and Free Prototype**: choose the first no/low-cost fundamentals
source (most likely SEC filing-acceptance data), build a small, read-only local prototype
downloader / normalizer for a tiny ticker sample that writes only to `research/output`, and
separately document the earnings / analyst-revisions provider gap to research and select before
that part of the track can be prototyped — still with no model training, no model candidate, no
data-purchase commitment, and no production integration. Like every phase in this track, Phase 3-F
**does not deploy**, **does not restart stock-api.service**, **does not enable** the model-v2 flag,
**does not run migrations**, **does not write to production DB**, and **does not trade**, and it
claims no **production edge**.

## Safety flags (from the feasibility JSON)

```
database_touched                    = false
database_write_executed             = false
migration_executed                  = false
deployment_executed                 = false
model_v2_enabled                    = false
production_edge_claimed             = false
no_trading                          = true
no_orders                           = true
no_automation                       = true
research_model_trained              = false
production_model_trained            = false
production_model_candidate_created  = false
deployable_model_artifact_written   = false
d_drive_read                        = false
d_drive_written                     = false
network_used                        = false
vendor_api_called                   = false
data_purchase_made                  = false
external_data_ingested              = false
```

## Conclusion

Phase 3-D selected the structured-company-data track. Phase 3-E turns that into an implementable
data contract: a vendor-agnostic schema catalog, strict point-in-time rules, minimum coverage
gates, a provider requirements / evaluation template, and an ingestion contract — then recommends
`PROCEED_TO_VENDOR_SELECTION_AND_FREE_SOURCE_PROTOTYPE`, routing to Phase 3-F (Fundamentals Source
Selection and Free Prototype). No model is trained, no production model candidate is created, no
deployable artifact is written, no external data is ingested, nothing is read from or written to
the D: drive, nothing is fetched or purchased, and the recommendation is **not a production edge**.
