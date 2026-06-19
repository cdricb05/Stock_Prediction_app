# Phase 3-G — SEC Fundamentals Mini-Pipeline Expansion (v1)

_Implemented by `research/prototype_phase3g_sec_fundamentals_minipipeline.py` and validated by
`tests/test_phase3g_sec_fundamentals_minipipeline.py`. Phase 3-G is a **controlled, read-only
research mini-pipeline**, not a training phase and not a production ingestion. After Phase 3-F
returned `SEC_FUNDAMENTALS_PROTOTYPE_SUCCESS` on a tiny 5-ticker sample, Phase 3-G expands the
same validated SEC path to a controlled 20-ticker sample and hardens it with stronger
data-quality checks — proving the source can scale from a proof-of-concept to a small research
mini-pipeline before any feature engineering or model training._

> Scope and safety. This mini-pipeline trains **no model**, creates **no production model
> candidate**, writes **no deployable model artifact**, computes **no model features or labels**,
> calls **no paid vendor**, purchases **no data**, uses **no third-party market-data vendor
> package**, touches no database, reads no D: price CSV, and writes nothing to the D: drive.
> Network is used **only** for official SEC public JSON endpoints. It is research / prototype
> tooling: it **does not deploy**, it **does not restart stock-api.service**, it **does not
> enable** the model-v2 serving flag, it **does not run migrations**, it **does not write to
> production DB**, and it **does not trade**. No orders, no automation, no model training, and no
> production integration happen here, and it claims no **production edge**.

## Why Phase 3-G follows Phase 3-F

Phase 3-F selected SEC public companyfacts + submissions as the first free fundamentals source and
proved, on AAPL / MSFT / JPM / XOM / UNH, that the Phase 3-E `company_identity` + `fundamentals`
schema can be filled point-in-time with full availability coverage and no duplicate keys
(`SEC_FUNDAMENTALS_PROTOTYPE_SUCCESS`, routing to 3-G). A 5-ticker success is not yet a pipeline.
Phase 3-G first **reads and confirms** the committed Phase 3-F result (phase `3-F`, recommendation
`SEC_FUNDAMENTALS_PROTOTYPE_SUCCESS`, next phase `3-G`, and all of its no-model / no-purchase /
no-D: flags), re-confirms the Phase 3-E ingestion contract (`company_identity` + `fundamentals`
tables, point-in-time columns `accepted_datetime` + `availability_datetime`,
`no_database_write_by_default` / `no_production_integration`) and schema catalog, and only then
expands the sample. The goal is to expose sector and accounting-field differences and to add the
data-quality machinery a later feature phase will rely on.

## Why price/volume-only modeling stopped

Upstream, Phase 3-C's kill switch triggered: after proper neutralization the refined
price/volume-only ridge collapsed to a near-zero mean rank IC, a sub-0.60 fold win rate, and
catastrophic folds. There is no robust residual price/volume alpha, so the next edge must come
from structured external data — and the SEC fundamentals track (3-E → 3-F → 3-G) builds the first
free slice of it.

## Why SEC public fundamentals are being expanded

SEC EDGAR public JSON (companyfacts + submissions) is free, requires no vendor contract, and is
natively point-in-time: each filing carries an acceptance timestamp that is the earliest safe
`availability_datetime`. Phase 3-F validated it on a tiny sample; Phase 3-G keeps the **same
selected source** and the **same XBRL mappings** and only scales the sample, so any scaling
problems (sector-specific missing fields, coverage gaps, reconciliation anomalies) surface now,
cheaply, on 20 names. **SEC does not provide forward earnings consensus or analyst estimate
revisions**, so that part of the track stays open (see the gap section below).

## 20-ticker sample

The controlled sample is exactly: **AAPL, MSFT, JPM, XOM, UNH, AMZN, NVDA, GOOGL, META, JNJ, PG,
HD, BAC, CVX, PFE, KO, DIS, CAT, GE, NEE** — only those present in the current-as-of sector map;
any missing ticker is skipped and reported. It spans technology, communication services, health
care, financials, energy, consumer staples, consumer discretionary, industrials, and utilities —
broad enough to expose sector and accounting-field differences, small enough to stay Git-safe and
within SEC fair-access limits. There is **no full 128-ticker ingestion** and the per-ticker
request budget is hard-capped.

## SEC access / fair-access constraints

- Network is restricted to official SEC public hosts only: `www.sec.gov` and `data.sec.gov`.
  Endpoints: `https://www.sec.gov/files/company_tickers.json`,
  `https://data.sec.gov/submissions/CIK##########.json`,
  `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`.
- A declared research `User-Agent` (`PaperTraderResearch/Phase3G …`) and `Accept-Encoding:
  gzip, deflate` are sent on every request.
- Throttle: a minimum 0.25s gap between requests; a hard cap of 45 requests total (a 20-ticker run
  uses 41: one `company_tickers` + two per ticker). No paid vendor, no third-party market-data
  vendor package.

## Cache behavior

Raw SEC responses are **pruned to the mapped concepts / most-recent periodic filings** (so the
sample stays Git-small) and cached under
`research/output/phase3g_sec_fundamentals_sample/raw/`. On any subsequent run, if a cache file
exists it is read instead of refetched, so the mini-pipeline runs fully offline from cache
(`network_used: false`, all cache hits). The raw cache is held well under a 5 MB soft target and
**validation fails if it exceeds 15 MB**. If the network and cache both fail, the mini-pipeline
writes a clearly-marked blocked result instead of crashing.

## Normalized company identity output

`company_identity_20ticker_sample.csv` carries `ticker, company_name, cik, sector, industry,
source, effective_from, effective_to`. `company_name` and `cik` come from SEC; `sector` /
`industry` are attached from the current-as-of sector map; `effective_from` records the sector
map's as-of date (identity here is current-as-of, not point-in-time, and stays
survivorship-caveated).

## Normalized fundamentals output

`fundamentals_20ticker_sample.csv` carries the full Phase 3-E-aligned column set: `ticker, cik,
fiscal_period_end, fiscal_year, fiscal_period, form, filed, frame, source_concept,
normalized_field, value, unit, source, availability_datetime, point_in_time_usable,
restatement_policy, validation_note`. The XBRL `us-gaap` mappings are identical to Phase 3-F
(revenue, net_income, operating_income, eps_diluted, total_assets, total_liabilities,
shareholder_equity, operating_cash_flow, capital_expenditures), and `free_cash_flow` is derived as
`operating_cash_flow − capital_expenditures` **only** when both inputs exist for a period. No
model features and no labels are computed — this is raw normalization only.

## Point-in-time handling

`availability_datetime` prefers the filing **acceptance** timestamp (`acceptanceDateTime` from
submissions, joined by accession number); when acceptance is unavailable it conservatively falls
back to the `filed` date. `fiscal_period_end` is **never** used as availability. A row is marked
`point_in_time_usable` only when it carries an `availability_datetime`. First-reported discipline
is enforced: per `(fiscal_period_end, fiscal_period, form)` the **earliest-filed** value is kept,
so later restatements never silently overwrite as-reported history.

## Stronger data-quality checks

Beyond the Phase 3-F coverage / availability / duplicate checks, Phase 3-G adds:

- **Field coverage by ticker** (`field_coverage_by_ticker.csv`): one row per (ticker, attempted
  field) with row count, `has_field`, earliest / latest fiscal period end, and per-field
  availability + point-in-time fractions.
- **Period coverage by ticker** (`period_coverage_by_ticker.csv`): one row per (ticker,
  fiscal_period_end, fiscal_period) with the count of available fields, the count and fraction of
  the four core required fields (revenue, net_income, total_assets, operating_cash_flow) present,
  and per-required-field booleans.
- **Reconciliation warnings** (`reconciliation_warnings.csv`): cross-period **sign-flip** flags
  and **extreme percentage-change** flags (vs the prior same-form period), plus
  **missing_required_field**, **missing_availability_datetime**, **missing_point_in_time_flag**,
  and **duplicate_key** flags. These are **warnings only**, not blockers — they never fail the run
  on their own; only severe coverage / point-in-time failures change the recommendation.

The data-quality report (`data_quality_report.json`) rolls these up with
`ticker_coverage_fraction`, a per-field `field_coverage_summary`, a `period_coverage_summary`,
`reconciliation_warning_count`, `sign_flip_warning_count`, and `raw_cache_bytes`.

## Field coverage results

Field coverage is reported per ticker for all ten attempted fields. The four core fields
(revenue, net_income, total_assets, operating_cash_flow) are expected across the sample;
sector-specific gaps are expected and documented — financial-sector filers (e.g. JPM, BAC) do not
report `OperatingIncomeLoss` or `PaymentsToAcquirePropertyPlantAndEquipment` the way industrials
do, so `operating_income`, `capital_expenditures`, and the derived `free_cash_flow` are
legitimately absent for some banks. These appear as `missing_required_field` /
missing-field-by-ticker entries, not as data errors.

## Period coverage results

Period coverage scores each (ticker, fiscal_period_end, fiscal_period) against the four core
required fields and reports the fraction fully covered. Quarterly (10-Q) and annual (10-K) periods
are both represented; the `period_coverage_summary` reports how many periods carry the full
required set.

## Reconciliation warnings

Sign-flip and extreme-change warnings are advisory signals comparing each value to the prior
same-form period of the same field; a sign flip in a flow metric (net_income, operating_income,
free_cash_flow, operating_cash_flow, eps_diluted) or an absolute change above 300% is surfaced for
review. Point-in-time integrity warnings (`missing_availability_datetime`,
`missing_point_in_time_flag`) and `duplicate_key` warnings should be empty on a clean run and are
surfaced if they ever appear. None of these block the run.

## SEC limitations

SEC public data covers **fundamentals only**. companyfacts may include later restated values
(handled via first-reported selection). Cached payloads are pruned and capped to recent periods —
this is a controlled 20-ticker research sample, not full historical coverage. The attached
sector / industry is current-as-of, so downstream results remain survivorship-caveated. Sector
accounting differences mean some fields are legitimately absent per filer.

## Earnings / analyst-revisions gap

SEC provides neither forward **earnings consensus** nor **analyst estimate revisions**. Those
remain an open provider-selection item: a separate free or paid provider must be researched and
selected (per the Phase 3-E provider requirements matrix) before earnings-surprise and
revision-momentum features can be prototyped. `provider_selection_required` stays true.

## Selected recommendation

**`SEC_FUNDAMENTALS_MINIPIPELINE_SUCCESS` → Phase 3-H (SEC Fundamentals Feature Engineering
Prototype).** The free SEC source scaled cleanly to the controlled 20-ticker sample with at least
18 of 20 tickers processed, more than 500 normalized fundamentals rows, full availability
coverage, point-in-time usability throughout, no duplicate keys, a ticker-coverage fraction at or
above 0.90, and a Git-safe raw cache under 15 MB. The recommendation sets
`create_production_model_candidate_now`, `train_production_model_now`, `deploy_now`,
`production_edge_claimed`, `full_128_ticker_ingestion_now`, `model_training_now`, and
`feature_engineering_now` all false.

## Why no model is trained and no production model candidate is created

Phase 3-G is a normalization + data-quality mini-pipeline. It ingests a controlled local research
sample, computes no model features or labels, trains nothing, and promotes nothing. It sets
`research_model_trained`, `production_model_trained`, `production_model_candidate_created`,
`deployable_model_artifact_written`, `model_features_computed`, `labels_computed`, and
`production_data_ingested` all false, keeps the model-v2 serving flag disabled, calls no paid
vendor and buys no data, and claims no **production edge**.

## What Phase 3-H should do

Run **SEC Fundamentals Feature Engineering Prototype**: build trailing-only, point-in-time
fundamental features from the 20-ticker sample and validate feature availability alignment (every
feature's as-of date must be ≤ its scoring date) — still **no model training**, no production
model candidate, no purchase, and no production integration. Like every phase in this track, Phase
3-H **does not deploy**, **does not restart stock-api.service**, **does not enable** the model-v2
flag, **does not run migrations**, **does not write to production DB**, and **does not trade**, and
it claims no **production edge**.

## Safety flags (from the mini-pipeline JSON)

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
network_used                        = true  (SEC fetched) / false (fully cached)
sec_public_data_used                = true
vendor_api_called                   = false
paid_vendor_api_called              = false
data_purchase_made                  = false
external_data_ingested              = true  (controlled local research sample only)
production_data_ingested            = false
full_128_ticker_ingestion           = false
model_features_computed             = false
labels_computed                     = false
```

## Conclusion

Phase 3-F proved the free SEC fundamentals source on a tiny sample; Phase 3-G scales it to a
controlled 20-ticker mini-pipeline and adds the field-coverage, period-coverage, and
reconciliation machinery a feature phase needs — `SEC_FUNDAMENTALS_MINIPIPELINE_SUCCESS`, routing
to Phase 3-H (SEC Fundamentals Feature Engineering Prototype). No model is trained, no production
model candidate is created, no deployable artifact is written, no production data is ingested, no
paid vendor is called, nothing is read from or written to the D: drive, and the recommendation is
**not a production edge**.
