# Phase 3-D — External Data Decision for Greenfield Modeling (v1)

_Implemented by `research/analyze_phase3d_external_data_decision.py` and validated by
`tests/test_phase3d_external_data_decision.py`. Phase 3-D is a **decision / planning phase**, not
a training phase. After the Phase 3-C price/volume-only kill switch tripped, it answers one
decisive question — **after the price/volume-only kill switch, which external data family should
we add first to maximize the chance of a real predictive edge while keeping cost, complexity, and
validation risk under control?**_

> Scope and safety. This phase trains **no model**, creates **no production model candidate**,
> writes **no deployable model artifact**, fetches nothing from the network, calls no data vendor,
> purchases no data, reads nothing on the D: drive, and writes nothing to the D: drive. It reads
> small committed JSON/CSV inputs and writes three small files under `research/output`. It is
> research / planning tooling: it **does not deploy**, it **does not restart stock-api.service**,
> it **does not enable** the model-v2 serving flag, it **does not run migrations**, it **does not
> write to production DB**, and it **does not trade**. No order placement, no automation, no model
> training, and no data acquisition happen here, and it claims no **production edge**.

## Why Phase 3-D follows Phase 3-C

Phase 3-C reran the Phase 3-B refined greenfield configuration through a stricter, kill-switched
walk-forward test and emitted `PRICE_VOLUME_ONLY_KILL_SWITCH_TRIGGERED`, routing explicitly to
Phase 3-D ("External Data Decision for Greenfield Modeling"). The strategic decision attached to
that result is firm: **stop tuning price/volume-only models, do not rerun another price/volume-only
retest, and do not return to the Phase 2K weak-signal rescue path.** Phase 3-D is therefore the
disciplined fork in the road: it does not model anything — it decides *what data to add next*.

## Why price/volume-only modeling is stopped

Phase 3-C's primary 21-day refined ridge, after the Phase 3-B refinements and proper
neutralization, was not stable enough to continue:

| Metric (primary 21d refined ridge) | Value |
|------------------------------------|-------|
| Mean rank IC | ~0.004058 (≈ zero) |
| Worst fold rank IC | ~−0.103648 |
| Fold win rate | ~0.533333 (< 0.60) |
| Catastrophic folds | 3 |
| Mean top-minus-bottom spread | ~−0.0018 (negative) |

Sector neutralization fixed the concentration problem, but risk-neutralizing the beta / volatility
/ correlation tilt — the very exposure that had driven the apparent Phase 3-A signal — **removed
the signal**. The conclusion is that there is **no robust residual price/volume alpha** on this
panel, so the next edge must come from external data rather than from further price/volume tuning.

## Data families evaluated

Eleven candidate external-data families were scored: **fundamentals**, **earnings events**,
**analyst estimates and revisions**, **company guidance / transcripts**, **news and sentiment**,
**options / implied volatility**, **short interest / securities lending**, **macro / rates /
commodities**, **insider transactions**, **institutional ownership / 13F**, and **alternative
data**.

## Scoring criteria

Each family is scored on a transparent **1–5 scale** (5 = most favorable for a small, careful
research project) across nine criteria: expected predictive relevance, point-in-time feasibility,
cost accessibility, implementation complexity (scored as ease — 5 = low complexity), validation
cleanliness, coverage for the 128-ticker universe, walk-forward-framework compatibility, risk of a
noisy / fragile signal (scored as robustness — 5 = low risk), and suitability as the immediate
next phase. A weighted priority score emphasizes relevance, point-in-time feasibility, validation
cleanliness, and next-phase suitability.

A family is **recommendable only if** it clears every gate: plausible predictive relevance beyond
price/volume, point-in-time (or clearly caveated) timestamps, feasible to test before any
production integration, no immediate paid enterprise infrastructure, convertible into trailing-only
features, and a fit for the current 128-ticker walk-forward framework.

## Selected first data track

**Recommendation: `PROCEED_TO_FUNDAMENTALS_EARNINGS_DATA_FEASIBILITY` → Phase 3-E (Fundamentals
and Earnings Data Feasibility).** The selected first track is the structured-company-data bundle:
**fundamentals + earnings events + analyst estimate revisions.** All three components clear every
decision gate and occupy the top three ranks of the option matrix. They are structured, can be
made point-in-time, map naturally onto cross-sectional equity ranking, add information not already
in price/volume, convert cleanly into trailing-only features (valuation, quality, growth,
profitability, leverage, revision momentum, earnings surprise, post-earnings drift), and are far
easier to validate leakage-free than raw news, options, or alternative data.

## Backup data track

The backup is **options / implied volatility** (`PROCEED_TO_OPTIONS_DATA_FEASIBILITY` if chosen):
it is natively point-in-time and forward-looking and is the strongest gate-passing family outside
the structured bundle, but it is more expensive, narrower in coverage, and harder to turn into
stable trailing features. **News and sentiment** is a secondary fallback with lower validation
cleanliness and robustness.

## Rejected tracks and why

- **Company guidance / transcripts** — timestamped but NLP-heavy and harder to validate cleanly.
- **News and sentiment** — noisy / fragile and the hardest to validate leakage-free (kept as a
  secondary fallback only).
- **Short interest / securities lending** — bi-monthly reporting lag weakens point-in-time
  alignment; richer borrow data is paid.
- **Macro / rates / commodities** — cheap and point-in-time, but market-level with little
  cross-sectional breadth for a 128-name ranking problem.
- **Insider transactions** — free and point-in-time clean (passes the gate), but sparse and
  low-breadth, so it ranks below the structured bundle and is deferred.
- **Institutional ownership / 13F** — filed ~45 days after quarter-end, badly stale.
- **Alternative data** — potentially high relevance but requires paid enterprise infrastructure,
  thin coverage, and is the hardest to validate; out of scope as a first track.

## Implementation requirements

Phase 3-E must establish the exact point-in-time source, schema, historical coverage, cost, and
ingestion plan **before any model training**; keep a vendor-agnostic feature schema; align every
field to the 128-ticker panel and trading calendar as strictly trailing, as-reported features;
preserve first-reported-vs-restated discipline so no restated fundamental leaks into the past; and
reuse the existing walk-forward harness with the model-free composite as a must-beat benchmark. No
production integration, no model candidate, and no deployable artifact in the feasibility phase.

## Validation requirements

Any external-data feature must be validated under the same chronological, embargoed, out-of-sample
walk-forward used for price/volume (≥3-year train, ~6-month validation, 63-day embargo); must beat
the model-free composite and clear kill-switch-style stability gates (no catastrophic fold, fold
win rate ≥ 0.60); must demonstrate strict point-in-time computability; must continue to be reported
as survivorship-biased / current-membership caveated; and must show incremental value beyond the
existing price/volume features.

## Cost / point-in-time / licensing caveats

Clean point-in-time fundamentals and estimate-revision history can require a paid provider, so the
feasibility phase must establish cost before any commitment. Fundamentals are frequently restated;
estimate revisions must be timestamped to when the consensus actually changed. Coverage may be thin
for parts of the universe or earlier history. Provider terms may restrict storage or redistribution
and must be confirmed before acquisition. The underlying universe and sector map remain
current-as-of, so every downstream result stays survivorship-caveated.

## Why no model is trained and no production model candidate is created

Phase 3-D is a decision artifact. It selects a *direction* for the next feasibility study; it does
not touch data beyond the committed result files, trains nothing, and promotes nothing. It sets
`research_model_trained = false`, `production_model_trained = false`,
`production_model_candidate_created = false`, and `deployable_model_artifact_written = false`,
keeps the model-v2 serving flag disabled, makes no network / vendor / purchase call, and claims no
**production edge**.

## What Phase 3-E should do

Run the **Fundamentals and Earnings Data Feasibility** study: determine the exact point-in-time
data source, schema, historical coverage, cost, and ingestion plan for fundamentals, earnings
events, and analyst estimate revisions — still with no model training, no model candidate, no data
purchase commitment beyond what feasibility requires, and no production integration. Like every
phase in this track, Phase 3-E **does not deploy**, **does not restart stock-api.service**, **does
not enable** the model-v2 flag, **does not run migrations**, **does not write to production DB**,
and **does not trade**, and it claims no **production edge**.

## Safety flags (from the decision JSON)

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
```

## Conclusion

Phase 3-C's kill switch showed there is no robust residual price/volume alpha to keep tuning. Phase
3-D scores eleven external-data families on a transparent 1–5 matrix and recommends adding
**structured company data first — fundamentals, earnings events, and analyst estimate revisions** —
with **options / implied volatility** as the backup. It routes to Phase 3-E (Fundamentals and
Earnings Data Feasibility) as a data feasibility study only. No model is trained, no production
model candidate is created, no deployable artifact is written, nothing is read from or written to
the D: drive, nothing is fetched or purchased, and the recommendation is **not a production edge**.
