# Phase 3-I — Fundamental Feature Price-Alignment Dry Run (v1)

## Why Phase 3-I follows Phase 3-H

Phase 3-H (`SEC_FUNDAMENTAL_FEATURES_PROTOTYPE_SUCCESS`) converted the normalized 20-ticker SEC
fundamentals sample into a **trailing-only, point-in-time feature snapshot**: 353 canonical filing
snapshots, a safe `feature_asof_date` on every row (the latest filing-acceptance timestamp of the
fields used, never the fiscal period end), annual and quarterly durations kept separate, zero
leakage-risk warnings. That snapshot proved the features are *safe to align*, but it deliberately
stopped short of any price join. Phase 3-I is the disciplined next step: line each feature snapshot
up against the trading days on which it was already public, generate forward-return labels **for
validation only**, and prove there is no look-ahead — before anyone considers a model.

## Why price/volume-only modeling stopped

Earlier phases (through the Phase 3-C kill-switch decision) showed a price/volume-only model on
this universe did not produce a defensible, leakage-free edge, and the research pivoted to
**fundamentals as an orthogonal signal source**. Phase 3-I keeps that pivot honest: it uses price
data only as the *scoring calendar* and the *label source*, not as model inputs. The fundamental
features remain the only candidate signal, and this phase still trains nothing.

## Why this is alignment and label validation only

This phase is scoped to alignment + leakage proof + label coverage measurement. It joins features
to price dates, attaches the active snapshot's age, and computes forward returns purely to (a)
prove every feature is observable strictly before its scoring date and (b) quantify how much of the
panel would even have a label. It fits **no** model, computes **no** predictions, scores, or
portfolio weights, and claims **no** production edge. Labels are never forward-filled and are not a
training target in this phase.

## Source inputs

- **Phase 3-H feature outputs (repo-local, C:)** — the confirmed result
  `phase3h_sec_fundamental_features.json` (checked for `phase == "3-H"`, recommendation
  `SEC_FUNDAMENTAL_FEATURES_PROTOTYPE_SUCCESS`, `recommended_next_phase.phase == "3-I"`, and the
  no-model / no-labels-yet / no-D:-write / no-network safety flags), the feature snapshot CSV (the
  primary input), and the feature dictionary / quality / alignment-warning artifacts for context.
- **Phase 2K-G price panel (D: drive, READ ONLY)** —
  `phase2k_g_expanded_price_history_free.csv` (`ticker, date, adjusted_open, adjusted_high,
  adjusted_low, adjusted_close, volume`) plus its data-quality, build-summary, and
  survivorship-caveat JSON. The panel is current-as-of and **survivorship-biased**.
- The current-as-of sector map (`phase2k_p_sector_map_current.csv`).

The universe is restricted to the **20 feature tickers plus the SPY benchmark** — never the full
128-ticker panel. Nothing is written to the D: drive.

## Scoring-date grid

For each of the 20 tickers, the scoring dates are the actual trading days in the price panel on
which an `adjusted_close` is available **and** which fall strictly after that ticker's first
`feature_asof_date`. A feature snapshot becomes active starting on the **first trading day strictly
after** its `feature_asof_date`; even if the as-of timestamp is intraday or after the close, the
first eligible scoring date is the next trading day. `fiscal_period_end` is never used as the
availability date.

## Feature activation rule

For each ticker and scoring date, the **active snapshot** is the latest feature snapshot whose
`feature_asof_date` (date part) is strictly before the scoring date. Features are carried forward
unchanged until a newer valid snapshot becomes available; ties on the as-of date are broken by the
later `fiscal_period_end`, then the higher `source_field_count`. Each aligned row records
`active_feature_asof_date`, `feature_age_days` (scoring date − as-of date), `active_fiscal_period_end`,
`active_fiscal_year`, `active_fiscal_period`, `active_form`, and `active_snapshot_type`, alongside
all engineered feature columns carried from Phase 3-H (blank stays blank — no zero-fill).

## Label-generation rules

Forward-return labels are generated for **validation only** at horizons of **21, 63, and 126
trading days**:

- `forward_return_h = adjusted_close[t+h] / adjusted_close[t] − 1`.
- `forward_spy_return_h` is the SPY return over the **identical calendar window** (SPY close on the
  scoring date to SPY close on the ticker's `t+h` date).
- `forward_excess_return_vs_spy_h = forward_return_h − forward_spy_return_h`.
- `binary_outperform_spy_h = 1` if the excess return is positive, else `0`.
- `forward_return_rank_by_date_h` is the ordinal rank (1 = lowest) of the ticker's forward return
  across the sample on that scoring date.

If a future price is unavailable (the tail of the series), the label is left null. Labels are
**never forward-filled** and are not used to train anything.

## Leakage checks

Every aligned row is run through six checks (`leakage_checks.csv`): `active_feature_asof_date_present`,
`active_feature_asof_before_scoring_date`, `active_feature_asof_after_or_equal_fiscal_period_end`,
`no_fiscal_period_end_as_availability_date`, `label_date_after_scoring_date`, and
`no_price_join_before_feature_available`. A **leakage failure** is any row whose as-of date is
missing, on/after the scoring date, before (or equal to) the fiscal period end, or whose label
horizon date is not strictly in the future. Any leakage failure forces a `REJECTED` recommendation.

## Alignment quality results

`alignment_quality_report.json` records the input feature rows/tickers, price rows/tickers read,
aligned rows/tickers and date span, rows by ticker and sector, the `feature_age_days` distribution,
the annual-vs-quarterly active-snapshot mix, label coverage by horizon, the leakage check/failure/
warning counts, and the per-column null-feature and null-label summaries. `date_alignment_summary.csv`
reports, per ticker, the price span, first feature as-of date, first eligible and first/last aligned
scoring dates, aligned row count, and the median/max feature age.

## Label coverage results

`label_summary_by_horizon.csv` reports, per horizon, the total rows, non-null counts for each label
family, mean forward and excess returns, the positive-return and outperform-SPY fractions, and the
scoring-date span with labels. Coverage is high for the shorter horizons and falls only at the tail
of the price series where the forward window runs past the last available date — exactly as
expected when labels are realized-only and never forward-filled. The conservative SUCCESS gates are
≥ 0.80 (21d), ≥ 0.70 (63d), and ≥ 0.60 (126d).

## SEC limitations

The features are SEC as-reported fundamentals only, pruned to a recent-period 20-ticker sample. The
attached sector/industry is current-as-of (not point-in-time). The price panel's membership is
current-as-of, so this alignment and any downstream result are **survivorship-biased**; a clean
point-in-time universe is deferred to a later paid-source decision.

## Earnings / analyst-revisions gap

SEC filings provide the trailing fundamentals aligned here but **no forward analyst consensus and
no estimate revisions**. Earnings-surprise and revision-momentum features still cannot be built or
aligned; a separate provider must be researched and selected (per the Phase 3-E provider
requirements matrix). `provider_selection_required` remains `true`.

## Selected recommendation

`SUCCESS` requires all 20 tickers aligned, more than 1,000 aligned rows, **zero leakage failures**,
label coverage above the conservative gates, **and a reasonable median active feature age** (≤ 400
days). The aligner returns `PARTIAL_SUCCESS` if alignment is leakage-free and fully covered but the
feature age is not yet reasonable (or coverage is short), `BLOCKED` if the Phase 3-H / D: inputs are
missing or corrupt, and `REJECTED` if any leakage failure is detected.

On the current sample the recommendation is **`FUNDAMENTAL_PRICE_ALIGNMENT_PARTIAL_SUCCESS`**. The
alignment is clean — all 20 tickers, ~32.9k aligned rows, **zero leakage failures** across ~197k
checks, and label coverage of ~0.99 / 0.96 / 0.92 at 21 / 63 / 126 days. However, the Phase 3-H
sample is **temporally sparse** for about seven tickers (e.g. HD and MSFT each have only two
distinct `feature_asof_date`s — one circa 2009/2010 and one circa 2024/2025, with a multi-year gap
between them). The activation rule therefore carries decade-old fundamentals forward for years,
pushing the **median active feature age to ~819 days** (max several thousand). This is **not
leakage** (the features were genuinely public before each scoring date), but the panel is not ready
for a model-readiness diagnostic. Phase 3-J must first repair the upstream temporal density (denser
Phase 3-H sampling) or introduce an explicit staleness cap. This is exactly the kind of defect the
dry run exists to surface before any modeling.

## Why no model is trained

Alignment and label validation must come before any fitting. Training on a tiny, survivorship-biased
20-ticker sample before the leakage proof would risk baking in look-ahead bias and overfitting. The
aligner trains no research model and no production model.

## Why no production model candidate is created

A production model candidate implies a frozen, deployable artifact and an implied claim of edge.
This phase makes no such claim: it is a dry-run alignment of a 20-ticker research sample. It creates
no production model candidate and writes no deployable model artifact.

## What Phase 3-J should do

On `SUCCESS`, Phase 3-J is the **Tiny Fundamental Model Readiness Diagnostic**: run readiness
diagnostics on the aligned 20-ticker dry-run panel — feature coverage, label sanity,
cross-sectional sample size, and baseline information-coefficient checks — to decide whether a tiny
research model is even allowed. On `PARTIAL`/`BLOCKED`/`REJECTED`, Phase 3-J instead repairs the
alignment, repairs the inputs, or redesigns the alignment, respectively.

## Safety guardrails

This phase **does not deploy**, **does not restart stock-api.service**, **does not enable** the
model-serving flag, **does not run migrations**, **does not write to production DB**, and it
**does not trade** or place orders. It uses no network, reads the D: price panel read-only and
writes nothing to the D: drive, calls no paid vendor, purchases no data, ingests no production data,
performs no full 128-ticker ingestion, fits no model, computes no predictions or scores, creates no
production model candidate, and writes no deployable model artifact. It generates forward labels for
validation only and claims no **production edge**.
