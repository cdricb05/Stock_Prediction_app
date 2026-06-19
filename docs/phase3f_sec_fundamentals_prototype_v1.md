# Phase 3-F — SEC Fundamentals Source Selection and Free Prototype (v1)

_Implemented by `research/prototype_phase3f_sec_fundamentals.py` and validated by
`tests/test_phase3f_sec_fundamentals_prototype.py`. Phase 3-F is a **tiny, read-only free-source
prototype**, not a training phase and not a production ingestion. After the Phase 3-E feasibility
study recommended `PROCEED_TO_VENDOR_SELECTION_AND_FREE_SOURCE_PROTOTYPE`, it proves one thing:
that the Phase 3-E company-identity + fundamentals schema can be filled, point-in-time, from a
free public source for a very small sample — and it names the safest next step._

> Scope and safety. This prototype trains **no model**, creates **no production model
> candidate**, writes **no deployable model artifact**, computes **no model features or labels**,
> calls **no paid vendor**, purchases **no data**, uses **no yfinance**, touches no database,
> reads no D: price CSV, and writes nothing to the D: drive. Network is used **only** for official
> SEC public JSON endpoints. It is research / prototype tooling: it **does not deploy**, it **does
> not restart stock-api.service**, it **does not enable** the model-v2 serving flag, it **does not
> run migrations**, it **does not write to production DB**, and it **does not trade**. No orders,
> no automation, no model training, and no production integration happen here, and it claims no
> **production edge**.

## Why Phase 3-F follows Phase 3-E

Phase 3-E defined the vendor-agnostic schema, point-in-time rules, minimum coverage gates, a
provider requirements template, and an ingestion contract, then emitted
`PROCEED_TO_VENDOR_SELECTION_AND_FREE_SOURCE_PROTOTYPE` (with `free_source_prototype_allowed:
true` and `provider_selection_required: true`), routing to Phase 3-F. The prototype first reads
and **confirms** that contract: that Phase 3-E is present, recommended this path, routes to 3-F,
and reported all of its own no-network / no-model / no-purchase flags as false — and that the
ingestion contract still requires `company_identity` + `fundamentals` tables, point-in-time
columns `accepted_datetime` and `availability_datetime`, and `no_database_write_by_default` /
`no_production_integration`.

## Why price/volume-only modeling stopped

Upstream, Phase 3-C's kill switch triggered: after proper neutralization the refined
price/volume-only ridge collapsed to a near-zero mean rank IC, a sub-0.60 fold win rate, and
catastrophic folds. There is no robust residual price/volume alpha, so the next edge must come
from structured external data — and Phase 3-F builds the first free slice of it.

## Why SEC public fundamentals are selected for the first prototype

SEC EDGAR public JSON (companyfacts + submissions) is free, requires no vendor contract, and is
natively point-in-time: each filing carries an acceptance timestamp that is the earliest safe
`availability_datetime`. It covers the large-cap universe's fundamentals well and was flagged by
Phase 3-E as the strongest free first prototype source. It is selected here as the first
`selected_source`. **SEC does not provide forward earnings consensus or analyst estimate
revisions**, so that part of the track stays open (see the gap section below).

## Sample tickers

The prototype fetches exactly the tiny sample **AAPL, MSFT, JPM, XOM, UNH** (≤ 5 names), and only
those present in the current-as-of sector map; any missing ticker is skipped and reported. There
is **no full 128-ticker ingestion** and the per-ticker request budget is hard-capped.

## SEC access / fair-access constraints

- Network is restricted to official SEC public hosts only: `www.sec.gov` and `data.sec.gov`.
  Endpoints: `https://www.sec.gov/files/company_tickers.json`,
  `https://data.sec.gov/submissions/CIK##########.json`,
  `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`.
- A declared research `User-Agent` (`PaperTraderResearch/Phase3F …`) and `Accept-Encoding:
  gzip, deflate` are sent on every request.
- Throttle: a minimum 0.25s gap between requests; a hard cap of 20 requests total (a 5-ticker run
  uses 11: one `company_tickers` + two per ticker). No paid vendor, no yfinance.

## Cache behavior

Raw SEC responses are **pruned to the mapped concepts / most-recent periodic filings** (so the
sample stays Git-small) and cached under
`research/output/phase3f_sec_fundamentals_sample/raw/`. On any subsequent run, if a cache file
exists it is read instead of refetched, so the prototype runs fully offline from cache
(`network_used: false`, all cache hits). If the network and cache both fail, the prototype writes
a clearly-marked blocked result instead of crashing.

## Normalized company identity output

`company_identity_sample.csv` carries `ticker, company_name, cik, sector, industry, source,
effective_from, effective_to`. `company_name` and `cik` come from SEC; `sector` / `industry` are
attached from the current-as-of sector map; `effective_from` records the sector map's as-of date
(identity here is current-as-of, not point-in-time, and stays survivorship-caveated).

## Normalized fundamentals output

`fundamentals_sample.csv` carries the full Phase 3-E-aligned column set: `ticker, cik,
fiscal_period_end, fiscal_year, fiscal_period, form, filed, frame, source_concept,
normalized_field, value, unit, source, availability_datetime, point_in_time_usable,
restatement_policy, validation_note`. XBRL `us-gaap` concepts are mapped to normalized fields
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

## Data quality results

On the validated run all five sample tickers were processed, with **368 normalized fundamentals
rows**, **availability_datetime coverage 1.0**, **point_in_time_usable fraction 1.0**, and
**0 duplicate keys**. `data_quality_report.json` records the requested / processed / failed
tickers, raw files written, identity and fundamentals row counts, fields attempted vs found,
missing-fields-by-ticker, availability and point-in-time fractions, duplicate-key count, source
limitations, SEC request / cache / network counts, errors, and the recommendation.

## SEC limitations

SEC public data covers **fundamentals only**. companyfacts may include later restated values
(handled via first-reported selection). Cached payloads are pruned and capped to recent periods —
this is a tiny prototype sample, not full historical coverage. The attached sector / industry is
current-as-of, so downstream results remain survivorship-caveated.

## Earnings / analyst-revisions gap

SEC provides neither forward **earnings consensus** nor **analyst estimate revisions**. Those
remain an open provider-selection item: a separate free or paid provider must be researched and
selected (per the Phase 3-E provider requirements matrix) before earnings-surprise and
revision-momentum features can be prototyped. `provider_selection_required` stays true.

## Selected recommendation

**`SEC_FUNDAMENTALS_PROTOTYPE_SUCCESS` → Phase 3-G (SEC Fundamentals Mini-Pipeline Expansion).**
The free SEC source filled the company-identity + fundamentals schema point-in-time for the full
5-ticker sample with complete availability coverage and no duplicate keys, so the source is
validated for fundamentals. The recommendation sets `create_production_model_candidate_now`,
`train_production_model_now`, `deploy_now`, `production_edge_claimed`,
`full_128_ticker_ingestion_now`, and `model_training_now` all false.

## Why no model is trained and no production model candidate is created

Phase 3-F is a source-selection / normalization prototype. It ingests a tiny local research
sample, computes no model features or labels, trains nothing, and promotes nothing. It sets
`research_model_trained`, `production_model_trained`, `production_model_candidate_created`,
`deployable_model_artifact_written`, and `production_data_ingested` all false, keeps the model-v2
serving flag disabled, calls no paid vendor and buys no data, and claims no **production edge**.

## What Phase 3-G should do

Run **SEC Fundamentals Mini-Pipeline Expansion**: expand the SEC fundamentals prototype from 5
tickers to a controlled ~20-ticker sample, add stronger data-quality checks (cross-statement
reconciliation, sign-flip flags, coverage gates), and prepare — but not yet train — feature
engineering, while continuing to document the earnings / analyst-revisions provider gap. Like
every phase in this track, Phase 3-G **does not deploy**, **does not restart stock-api.service**,
**does not enable** the model-v2 flag, **does not run migrations**, **does not write to production
DB**, and **does not trade**, and it claims no **production edge**.

## Safety flags (from the prototype JSON)

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
external_data_ingested              = true  (tiny local research sample only)
production_data_ingested            = false
```

## Conclusion

Phase 3-E said a free fundamentals prototype was buildable; Phase 3-F builds it. It selects SEC
public companyfacts + submissions, fetches a throttled, cached, ≤5-ticker sample from official SEC
endpoints only, and normalizes company identity + a subset of fundamentals into the Phase 3-E
schema with clean point-in-time availability timestamps — `SEC_FUNDAMENTALS_PROTOTYPE_SUCCESS`,
routing to Phase 3-G (SEC Fundamentals Mini-Pipeline Expansion). No model is trained, no
production model candidate is created, no deployable artifact is written, no production data is
ingested, no paid vendor is called, nothing is read from or written to the D: drive, and the
recommendation is **not a production edge**.
