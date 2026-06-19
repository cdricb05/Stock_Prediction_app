# Phase 3-H — SEC Fundamentals Feature Engineering Prototype (v1)

## Why Phase 3-H follows Phase 3-G

Phase 3-G (`SEC_FUNDAMENTALS_MINIPIPELINE_SUCCESS`) proved that the SEC fundamentals path scales
from a 5-ticker proof-of-concept to a controlled, point-in-time **20-ticker mini-pipeline**: 20/20
tickers processed, 1,416 normalized fundamentals rows, full `availability_datetime` coverage,
point-in-time usable throughout, zero duplicate keys, and a Git-safe raw cache. The mini-pipeline
delivered a clean, normalized, point-in-time fundamentals table — but no *features*. Phase 3-H is
the next disciplined step: turn that normalized sample into a **trailing-only, point-in-time
feature snapshot dataset** for the same 20 tickers, so that a later phase can attempt a
leakage-checked price-alignment dry run. Phase 3-H reads only the committed Phase 3-G outputs; it
uses no network at all.

## Why price/volume-only modeling stopped

Earlier phases (through the Phase 3-C kill-switch decision) showed that a price/volume-only model
on this universe did not produce a defensible, leakage-free edge, and the research direction
pivoted to **fundamentals as an orthogonal signal source**. Phase 3-H continues that pivot: it
builds fundamental features (margins, leverage, growth, cash-conversion quality, size controls)
that are independent of the price series. No price or volume data is read or joined here.

## Why this is feature engineering only

This phase is deliberately scoped to feature construction and point-in-time validation. It builds
features and proves they are trailing-only and safe to align. It **does not** compute target
labels, **does not** compute forward returns, **does not** join to price data, and **does not**
train any model. Those steps are deferred to Phase 3-I and beyond, and only after a leakage check.
This phase claims no **production edge**.

## Source inputs

All inputs are committed, repo-local Phase 3-G / Phase 3-E artifacts (no network, no D: drive):

- `research/output/phase3g_sec_fundamentals_minipipeline.json` — confirmed `phase == "3-G"`,
  `recommendation == SEC_FUNDAMENTALS_MINIPIPELINE_SUCCESS`, `recommended_next_phase.phase ==
  "3-H"`, and all of the no-model / no-D: / no-vendor / no-labels safety flags.
- `research/output/phase3g_sec_fundamentals_sample/fundamentals_20ticker_sample.csv` — the
  normalized, point-in-time fundamentals rows (the primary input).
- `research/output/phase3g_sec_fundamentals_sample/company_identity_20ticker_sample.csv` —
  attaches `company_name`, `sector`, `industry`, `cik`.
- The Phase 3-G data-quality, field-coverage, period-coverage, and reconciliation artifacts
  (context), the Phase 3-E ingestion contract (re-confirmed), and the current-as-of sector map.

## Canonical snapshot construction

The normalized fundamentals rows are grouped into **canonical filing snapshots** keyed by
`(ticker, fiscal_period_end, fiscal_year, fiscal_period, form)`, and the mapped `normalized_field`
values are pivoted wide. The pivoted fields are: `revenue`, `net_income`, `operating_income`,
`eps_diluted`, `total_assets`, `total_liabilities`, `shareholder_equity`, `operating_cash_flow`,
`capital_expenditures`, `free_cash_flow`. Each snapshot records `source_field_count` and a
`required_field_coverage_fraction` over the four core fields (`revenue`, `net_income`,
`total_assets`, `operating_cash_flow`).

## Point-in-time `feature_asof_date` rules

- `feature_asof_date` is the **maximum `availability_datetime`** across the fields used in the
  snapshot — i.e. the latest filing-acceptance timestamp of the data that goes into the row.
- `feature_asof_date` must exist and must be **≥ `fiscal_period_end`** (and in practice strictly
  after it — a filing is accepted only once the period has closed).
- The `fiscal_period_end` is **never** used as the availability date.
- A snapshot is `point_in_time_usable` only if every source row used is itself
  `point_in_time_usable`.
- Any row that would violate these rules is flagged (`missing_feature_asof_date`,
  `feature_asof_before_period_end`, `not_point_in_time_usable`) and counted as a **leakage-risk**
  warning, which blocks a `SUCCESS` recommendation.

## Annual vs quarterly handling

- A snapshot is **annual** if `form ∈ {10-K, 10-K/A}` or `fiscal_period == FY`.
- A snapshot is **quarterly** if `form ∈ {10-Q, 10-Q/A}` or `fiscal_period ∈ {Q1,Q2,Q3,Q4}`.
- Annual and quarterly rows are kept strictly separate; year-over-year growth compares annual to
  the prior fiscal year (FY) and quarterly to the **same fiscal quarter** of the prior year only —
  never annual-to-quarterly.
- companyfacts sometimes tags quarterly-duration facts with `fiscal_period == FY` *inside* a 10-K.
  For each `(ticker, fiscal_year)` the canonical annual snapshot is the one with the latest
  `fiscal_period_end`; any other FY-tagged row in the same fiscal year is an embedded quarterly
  duration and is flagged `annual_quarterly_mix_risk` and **excluded from annual YoY comparisons**
  rather than mixed with the true full-year duration.

## Feature families

- **A. Profitability / margins** — `operating_margin`, `net_margin`, `fcf_margin`,
  `operating_cash_flow_margin`. (`gross_margin` is documented but omitted: `gross_profit` is not in
  the Phase 3-G normalized sample.)
- **B. Balance sheet / leverage** — `debt_proxy_total_liabilities_to_assets`, `equity_to_assets`,
  `asset_turnover_proxy`, `liability_to_equity`.
- **C. Growth / change (trailing YoY)** — `revenue_yoy_growth`, `net_income_yoy_growth`,
  `operating_income_yoy_growth`, `eps_diluted_yoy_growth`, `total_assets_yoy_growth`,
  `operating_cash_flow_yoy_growth`, `free_cash_flow_yoy_growth`.
- **D. Quality / cash conversion** — `cash_conversion`, `fcf_to_net_income`, `capex_intensity`,
  `accrual_proxy`.
- **E. Size / scale controls** — `log_total_assets`, `log_revenue_abs`,
  `log_total_liabilities_abs`.
- **F. Availability / recency metadata** — `filing_lag_days`, plus the snapshot metadata
  `source_field_count`, `required_field_coverage_fraction`, `is_annual_snapshot`,
  `is_quarterly_snapshot`.

No price-based valuation feature (P/E, EV/EBITDA, market cap, dividend yield) is computed, because
no price or market-cap data is available or joined in this phase.

## Missing-field handling

- Missing financial-statement values are **left null, never zero-filled**.
- A ratio is blank/null whenever its numerator or denominator is missing, or the denominator is
  zero (a `division_by_zero` warning is recorded).
- Sector-specific missing fields are **reported, not treated as fatal**. For banks / financials,
  `operating_income`, `capital_expenditures`, and `free_cash_flow` are frequently absent; those
  features are simply null for those filers and a `missing_required_source_field` warning is
  recorded where a core field is absent.

## Numeric safeguards

- Division by zero is avoided; the affected feature is left null and warned.
- Extreme ratios (|ratio| > 10) and extreme YoY growth (|growth| > 5) are **flagged only** in the
  alignment-warnings file; the raw feature value is left exactly as computed in the snapshot.
- Negative denominators (e.g. negative `shareholder_equity` for `liability_to_equity`, negative
  `net_income` for `cash_conversion`) are flagged `negative_denominator`; the value is still left
  as computed.
- Unavailable values are written as blank/null, never as 0.

## Feature coverage results

Coverage is reported per `(ticker, feature)` in `feature_coverage_by_ticker.csv` (non-null count,
total snapshot count, coverage fraction, and the earliest/latest `feature_asof_date`), and
summarized per feature family in the feature-quality report. Same-period level/ratio features
(margins, leverage, size) have high coverage; the trailing YoY-growth features have lower coverage
because the Phase 3-G sample is pruned to the most-recent fiscal periods and because fiscal-year
duration quirks (above) suppress some annual comparisons — this is expected for a controlled
sample, not a defect.

## Warning results

Warnings are written to `feature_alignment_warnings.csv` and counted by type in the feature-quality
report. They are **advisory unless they indicate leakage**. The leakage-risk types
(`missing_feature_asof_date`, `feature_asof_before_period_end`, `not_point_in_time_usable`) are
expected to be **zero** for this sample (Phase 3-G delivered full availability and point-in-time
coverage). The non-leakage types (`annual_quarterly_mix_risk`, `missing_required_source_field`,
`division_by_zero`, `extreme_ratio`, `negative_denominator`) surface real accounting / XBRL nuances
(mixed durations, sector-specific gaps, sign-flipping cash flows) and do not block a `SUCCESS`
recommendation.

## SEC limitations

SEC public data is as-reported fundamentals only. It is pruned here to a controlled 20-ticker,
most-recent-periods sample (not full history), the attached sector/industry is current-as-of (not
point-in-time, so survivorship-caveated), and some sector-specific fields are legitimately absent.

## Earnings / analyst-revisions gap

SEC filings provide the trailing fundamentals used here but **no forward analyst consensus and no
estimate revisions**. Earnings-surprise and revision-momentum features therefore cannot be built
from SEC data; a separate provider must be researched and selected (per the Phase 3-E provider
requirements matrix) before those features are prototyped. `provider_selection_required` remains
`true`.

## Selected recommendation

The expected recommendation for this sample is **`SEC_FUNDAMENTAL_FEATURES_PROTOTYPE_SUCCESS`**:
all 20 tickers represented, more than 100 canonical snapshots, a `feature_asof_date` on every row,
full point-in-time usability, zero rows whose as-of date precedes the period end, zero leakage-risk
warnings, and well over 20 engineered feature columns — with annual and quarterly durations kept
separate. (The builder will instead return `PARTIAL_SUCCESS`, `BLOCKED`, or `REJECTED` if those
conservative conditions are not met; any leakage-risk warning forces `REJECTED`.)

## Why no model is trained

This phase exists to construct and validate features, not to fit anything. Training before a
leakage-checked price-alignment dry run would risk baking in look-ahead bias and overfitting a tiny
20-ticker sample. The builder trains no research model and no production model.

## Why no labels are generated

Labels require a forward-return horizon and a price join — both deferred to Phase 3-I, where the
alignment can be dry-run and leakage-checked first. Generating labels now would couple feature
construction to a target definition before the alignment is proven safe.

## Why no production model candidate is created

A production model candidate implies a frozen, deployable artifact and an implied claim of edge.
This phase makes no such claim: it is a 20-ticker research feature prototype. It creates no
production model candidate and writes no deployable model artifact.

## What Phase 3-I should do

On `SUCCESS`, Phase 3-I is the **Fundamental Feature Price-Alignment Dry Run**: join the 20-ticker
point-in-time feature snapshot to historical price dates in a dry run, generate forward labels for
**validation only**, and verify that every feature is observable strictly before its alignment date
(no leakage) — still with no model training. On `PARTIAL`/`BLOCKED`/`REJECTED`, Phase 3-I instead
repairs feature coverage, repairs the Phase 3-G inputs, or redesigns the feature/source handling,
respectively.

## Safety guardrails

This phase **does not deploy**, **does not restart stock-api.service**, **does not enable** the
model-serving flag, **does not run migrations**, **does not write to production DB**, and it
**does not trade** or place orders. It uses no network, reads/writes nothing on the D: drive, calls no
paid vendor, purchases no data, ingests no production data, performs no full 128-ticker ingestion,
computes no labels, joins no price data, trains no model, and writes no deployable model artifact.
It is a read-only research feature prototype and claims no **production edge**.
