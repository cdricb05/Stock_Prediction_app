# Phase 3-J — Repair Fundamental Price Alignment (v1)

## Why Phase 3-J follows Phase 3-I

Phase 3-I (`FUNDAMENTAL_PRICE_ALIGNMENT_PARTIAL_SUCCESS`) joined the 20-ticker point-in-time
fundamental feature snapshot to the Phase 2K-G daily price calendar, generated 21/63/126-day
forward-return labels **for validation only**, and proved there was **zero leakage** across ~197k
checks with full ticker and label coverage. But it deliberately surfaced one defect rather than
rubber-stamping success: the active features were too **stale**. The carry-forward rule propagated
the latest already-filed snapshot until a newer one became available, and because the Phase 3-H
sample was temporally sparse for several tickers, the panel's median active feature age was **819
days** — far beyond the conservative reasonableness threshold (≤ 400 days). Phase 3-J repairs that
panel: it applies an explicit maximum feature-age cap so no row carries an unreasonably stale
fundamental, and decides whether the repaired 20-ticker sample is ready for a tiny IC /
model-readiness diagnostic or whether the SEC history must first be densified.

## Why price/volume-only modeling stopped

Earlier phases (through the Phase 3-C kill-switch decision) showed a price/volume-only model on this
universe did not produce a defensible, leakage-free edge, and the research pivoted to
**fundamentals as an orthogonal signal source**. Phase 3-J keeps that pivot honest: price data is
used only as the scoring calendar and the label source (both inherited unchanged from Phase 3-I),
never as model inputs. The fundamental features remain the only candidate signal, and this phase
still trains nothing.

## Why Phase 3-I was partial, not failed

Phase 3-I was *partial* because the alignment itself was clean — every aligned row's feature was
observable strictly before its scoring date and on/after its fiscal period end, with zero leakage
failures, all 20 tickers aligned, ~32.9k rows, and label coverage of ~0.99 / 0.96 / 0.92. Nothing
about the alignment mechanics failed. What was *not yet* acceptable was the **feature age**: the
features were genuinely public before each scoring date (so this is not leakage), but decade-old
fundamentals carried forward are not a sound basis for a model-readiness diagnostic. A partial
result correctly says "the pipe is leak-free, but the water is stale — filter it before drinking."

## Feature staleness diagnosis

This phase reads **only** the repo-local Phase 3-I aligned panel and the Phase 3-H feature snapshot
(for source density). Of the 32,882 aligned rows, **18,606 (≈ 0.566)** carried an active feature
older than 365 days and **16,890 (≈ 0.514)** older than 730 days. Eleven tickers are flagged with a
staleness problem — they either carried a feature older than two years at some point or had fewer
than four distinct `feature_asof_date`s in the upstream sample: **AAPL, AMZN, CAT, GE, HD, JNJ,
META, MSFT, NEE, NVDA, UNH**. The worst cases (HD median 3,101 / max 5,009 days; MSFT 3,024 / 4,933;
CAT 2,752 / 4,660) each have only two distinct filing dates separated by a multi-year gap, so the
older snapshot is carried forward for years. The recently-sampled tickers (BAC, CVX, DIS, GOOGL, KO,
PFE, PG, XOM) sit at ~43-day median age and are not the problem. `stale_rows_by_ticker.csv` records
the full per-ticker breakdown.

## Staleness cap sensitivity

Four candidate maximum feature-age caps were evaluated; `staleness_cap_sensitivity.csv` holds the
full grid. Filtering by age can never introduce leakage, and the re-check confirmed **zero leakage
failures at every cap**:

| cap (days) | rows | retained | tickers | sectors | median age | cov 21d | cov 63d | cov 126d | leakage |
|-----------:|-----:|---------:|--------:|--------:|-----------:|--------:|--------:|---------:|--------:|
| 365 | 14,276 | 0.434 | 20 | all | 53 | 0.9706 | 0.9117 | 0.8235 | 0 |
| 400 | 14,446 | 0.439 | 20 | all | — | 0.9709 | 0.9128 | 0.8256 | 0 |
| 540 | 15,073 | 0.458 | 20 | all | — | 0.9721 | 0.9164 | 0.8328 | 0 |
| 730 | 15,992 | 0.486 | 20 | all | — | 0.9737 | 0.9212 | 0.8424 | 0 |

Every cap keeps all 20 tickers and clears the conservative coverage gates (≥ 0.80 / 0.70 / 0.60).
Looser caps retain modestly more rows (and slightly better 126d coverage, because they keep a few
more mid-life rows away from the unlabeled tail), but at the cost of admitting older features. The
retained rows still span 2016 → 2026 because every ticker has at least some recent-filing window
within the cap; the cap simply removes the long stale carry-forward stretches.

## Selected cap

The selection rule chooses the **strictest** cap that satisfies the full success gate (≥ 15
tickers, ≥ 5,000 rows, leakage-free, coverage ≥ 0.80 / 0.70 / 0.60). All four caps pass, so the
strictest — **365 calendar days** — is selected. This is the most defensible choice: it keeps the
active fundamentals within roughly a year (about a year's worth of quarterly filings), retains all
20 tickers and 14,276 rows, and drops the panel's median active feature age from **819 days to 53
days**. `repaired_aligned_panel_20ticker_sample.csv` contains only rows where `feature_age_days ≤
365` and the point-in-time invariants hold.

## Repaired aligned panel

The repaired panel keeps every original Phase 3-I column and only filters rows. It has **14,276
rows across all 20 tickers**, spans 2016-01-04 → 2026-06-16, and has a median active feature age of
53 days (mean and full distribution in `repaired_alignment_quality_report.json`). No ticker and no
sector is dropped by the selected cap.

## Label coverage after repair

Forward-return labels are **not** recomputed — the existing Phase 3-I validation labels are simply
carried on the retained rows. After the 365-day cap, coverage is **0.9706 (21d) / 0.9117 (63d) /
0.8235 (126d)**, comfortably above the conservative gates. Coverage is slightly lower than the full
Phase 3-I panel because capping age preferentially keeps rows nearer each ticker's recent filings,
which sit closer to the unlabeled tail of the price series; it remains well within tolerance.

## Leakage checks after repair

Filtering a leakage-free panel cannot create leakage, and Phase 3-J proves it by re-checking the
hard point-in-time invariants (as-of present, as-of strictly before scoring date, as-of on/after
and never equal to the fiscal period end) on the repaired rows: **leakage_failure_count_after_repair
= 0**, consistent with the Phase 3-I count of 0.

## Whether a model-readiness diagnostic is allowed next

Yes — conditionally and conservatively. The repaired panel is leakage-free, retains all 20 tickers
and > 5,000 rows with reasonable feature age and good label coverage, so a **tiny fundamental IC /
model-readiness diagnostic** is allowed as the next phase. That diagnostic is still only a
readiness check (univariate and feature-family information-coefficient measurement, sample-size and
label-sanity tests); it does **not** authorize training a model, and Phase 3-K must itself decide
whether a tiny research model is permitted.

## SEC limitations

The features are SEC as-reported fundamentals only, pruned to a recent-period 20-ticker sample. The
attached sector/industry is current-as-of (not point-in-time), and the inherited price panel's
membership is current-as-of, so this repaired alignment and any downstream result remain
**survivorship-biased**. The staleness cap removes stale rows but cannot manufacture the
intermediate filings the upstream sample never captured; a clean point-in-time universe and denser
history are deferred to later decisions.

## Earnings / analyst-revisions gap

SEC filings provide the trailing fundamentals repaired here but **no forward analyst consensus and
no estimate revisions**. Earnings-surprise and revision-momentum features still cannot be built or
aligned; a separate provider must be researched and selected (per the Phase 3-E provider
requirements matrix). `provider_selection_required` remains `true`.

## Selected recommendation

The recommendation is **`FUNDAMENTAL_ALIGNMENT_REPAIR_SUCCESS`**. A 365-day feature-staleness cap
produces a leakage-free repaired panel with all 20 tickers, 14,276 rows, a 53-day median feature
age, and label coverage of 0.97 / 0.91 / 0.82 — clearing every conservative success gate. The panel
is ready for a tiny fundamental IC / model-readiness diagnostic in Phase 3-K (still no model
training, no production candidate, no production edge claim).

## Why no model is trained

Repairing the alignment and re-validating coverage must come before any fitting. Training on a
tiny, survivorship-biased 20-ticker sample before the readiness diagnostic would risk overfitting
and baking in bias. This phase trains no research model and no production model.

## Why no production model candidate is created

A production model candidate implies a frozen, deployable artifact and an implied claim of edge.
This phase makes no such claim: it is a repo-local staleness repair of a 20-ticker research sample.
It creates no production model candidate and writes no deployable model artifact.

## What Phase 3-K should do

On `SUCCESS`, Phase 3-K is the **Tiny Fundamental IC and Model-Readiness Diagnostic**: run
univariate and feature-family IC diagnostics on the repaired 20-ticker panel, test cross-sectional
sample size and label sanity, and decide whether a tiny research model is even allowed. On
`PARTIAL_SUCCESS` it instead decides between a limited IC diagnostic and expanding SEC history; on
`NEEDS_DENSER_SEC_HISTORY` it rebuilds Phase 3-G / 3-H with denser history; on `BLOCKED` it repairs
the inputs; and on `REJECTED` it redesigns the alignment.

## Safety guardrails

This phase **does not deploy**, **does not restart stock-api.service**, **does not enable** the
model-serving flag, **does not run migrations**, **does not write to production DB**, and it
**does not trade** or place orders. It uses no network, reads and writes nothing on the D: drive, fetches
no SEC data, calls no vendor, purchases no data, ingests no production data, performs no full
128-ticker ingestion, fits no model, computes no predictions or scores, creates no production model
candidate, and writes no deployable model artifact. It only filters the existing Phase 3-I
validation labels and claims no **production edge**.
