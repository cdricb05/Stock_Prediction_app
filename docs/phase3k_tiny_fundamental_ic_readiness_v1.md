# Phase 3-K — Tiny Fundamental IC and Model-Readiness Diagnostic (v1)

## Why Phase 3-K follows Phase 3-J

Phase 3-J (`FUNDAMENTAL_ALIGNMENT_REPAIR_SUCCESS`) took the leak-free-but-stale Phase 3-I aligned
panel and applied an explicit 365-calendar-day feature-staleness cap, producing a clean repaired
20-ticker panel: 14,276 rows, all 20 tickers, median active feature age dropped from 819 days to 53
days, zero leakage, and label coverage of 0.97 / 0.91 / 0.82 at the 21 / 63 / 126-day horizons. That
phase deliberately stopped before any modeling and routed here. Phase 3-K is the **hard gate** before
any model is ever fit: it asks whether the point-in-time SEC fundamental features actually carry
**any** cross-sectional predictive signal against forward excess returns and forward rank labels, and
decides whether a tiny research model is even allowed next (Phase 3-L) or whether a larger SEC
universe / richer data is needed first. Phase 3-K **trains no model**.

## Why price/volume-only modeling stopped

Earlier phases (through the Phase 3-C kill-switch decision) showed a price/volume-only model on this
universe produced no defensible, leakage-free edge, and the research pivoted to **fundamentals as an
orthogonal signal source**. Phase 3-K keeps that pivot honest: price data is used only as the scoring
calendar and as the forward-return label source (both inherited unchanged from Phase 3-I / Phase 3-J),
never as a model input, and this phase fits nothing.

## Why the 365-day repaired panel is used

Phase 3-K reads **only** the Phase 3-J repaired panel and its quality / label artifacts, plus the
Phase 3-H feature dictionary and quality report — all repo-local on the C: drive. The repaired panel
is the only version of the alignment whose features are both leakage-free *and* not unreasonably
stale (every retained row's active fundamental is within 365 days). Running an IC diagnostic on the
pre-repair Phase 3-I panel would have measured signal against decade-old carried-forward fundamentals;
the repaired panel is the defensible substrate. This phase does **not** read or write the D: drive,
fetches no SEC data, and uses no network.

## IC methodology

For each engineered feature and each horizon (21 / 63 / 126 days) the analyzer computes the
**cross-sectional daily Spearman rank information coefficient**: rows are grouped by `scoring_date`,
and within each date the rank correlation is taken between the feature value and (a)
`forward_excess_return_vs_spy_{h}d` and (b) `forward_return_rank_by_date_{h}d`. A date contributes an
IC only when it has at least **8** non-null feature/label pairs. The per-date ICs are then summarized
into `mean_rank_ic`, `median_rank_ic`, an `ic_hit_rate` (the fraction of dates whose IC shares the
sign of the mean IC — a direction-agnostic consistency measure that is valid for both positive and
negative signals), and an `ic_ir` (mean IC divided by the sample standard deviation of the per-date
ICs). No model of any kind is fit — only rank correlations and bucket means are computed. Because the
two labels are monotonically related within a date (excess return = raw return minus a per-date
constant), the excess-return and forward-rank ICs are essentially identical by construction; both are
reported as a consistency check.

## Feature families

The 23 engineered features (metadata and all forward-label columns excluded) are mapped onto six
families using the Phase 3-H feature dictionary, with a name-based fallback, plus an `unknown` bucket:
`profitability_margin` (operating/net/fcf/operating-cash-flow margins), `balance_sheet_leverage`
(liabilities-to-assets, equity-to-assets, asset turnover, liability-to-equity), `growth_change`
(the YoY growth features), `cash_quality` (cash conversion, fcf-to-net-income, capex intensity,
accrual proxy), `size_scale` (log total assets / revenue / liabilities), and `availability_recency`
(filing lag days). This grouping is used to aggregate IC and to ask whether any *family* — not just an
individual feature — shows a directionally consistent signal.

## Top/bottom bucket diagnostics

As a second, model-free read on each feature, the analyzer ranks tickers by feature value on each
`scoring_date` and computes the average `forward_excess_return_vs_spy` in the top vs bottom **quintile**
(when a date has at least 10 valid observations) and, because the sample is tiny, also a top-3 vs
bottom-3 spread. The per-date top-minus-bottom spreads are summarized into a mean spread, a positive
spread fraction, and a directional consistency. No trade signals, scores, or portfolio weights are
created — these spreads are diagnostic statistics only.

## Horizon readiness

| horizon | label coverage | avg daily cross-section | qualifying IC dates | distinct IC years | best feature (\|IC\|) | moderate+ | strong | readiness |
|--------:|---------------:|------------------------:|--------------------:|------------------:|-----------------------|----------:|-------:|-----------|
| 21d  | 0.9706 | 5.31 | 450 | 3 | cash_conversion (0.059) | 1 | 1 | diagnostic_only |
| 63d  | 0.9117 | 5.07 | 408 | 3 | cash_conversion (0.122) | 3 | 2 | diagnostic_only |
| 126d | 0.8235 | 4.70 | 345 | 2 | cash_conversion (0.205) | 3 | 2 | diagnostic_only |

Several features clear the raw IC-magnitude bar (a "moderate"/"strong" label by absolute mean rank IC,
date count, hit rate, and non-null coverage), and the absolute ICs even *grow* with horizon. That
pattern is itself a warning sign, not a green light: the average daily cross-section is only ~5 names,
so the only dates with at least 8 names — every date that contributes an IC — fall in the recent
window where the 365-day cap left all 20 tickers with a fresh filing. To certify a model gate the
analyzer therefore also requires the qualifying dense cross-sections to span at least **4 distinct
calendar years**; here they span only **3** (2024–2026), so every horizon is capped at
`diagnostic_only` regardless of IC magnitude.

## Yearly stability

The yearly diagnostic confirms the concern. The "best" features (cash_conversion, filing_lag_days,
log_revenue_abs) have qualifying ICs **only in 2024, 2025, and 2026** — there is no pre-2024 dense
cross-section at all — and within that span the signs are not consistent: cash_conversion's 21-day IC
is roughly −0.10 in 2024 and 2025 but flips to +0.04 in 2026, and the long-horizon magnitudes (e.g. a
−0.65 mean IC with IR ≈ −3.7 over 89 dates for revenue_yoy_growth at 126 days) are inflated by the
heavily overlapping daily forward-return windows in a single market regime rather than by independent
evidence. This is exactly the kind of single-regime, overlapping-window artifact that a 20-ticker
sample produces, and it is why the apparent strength cannot be trusted.

## Sector sanity

The 20 tickers cover 9 sectors but most sectors hold only 1–2 names (Financials = BAC, JPM; Utilities
= NEE alone), so the cross-section is thin and uneven. Margin and cash-quality features are
structurally undefined for the banks (operating_margin and capex_intensity are ~0% non-null for
Financials), so any margin/cash signal is effectively measured on non-financial names only. No sector
neutralization or modeling is performed here; the sector summary is a sanity check that flags this
concentration and the bank-accounting gap.

## Selected recommendation

The recommendation is **`FUNDAMENTAL_IC_DIAGNOSTIC_INCONCLUSIVE_SMALL_SAMPLE`**. Data quality is
sufficient — leakage is zero, label coverage clears the gates, and feature non-null coverage is
adequate — and several features show non-trivial raw IC magnitude (max |mean IC| ≈ 0.205). But every
qualifying dense cross-section sits in a single recent ~2-year regime (3 distinct years, 2024–2026)
over overlapping forward-return windows with ~5 names per date, and the best features' yearly IC signs
are inconsistent. Twenty tickers are too few, and too temporally concentrated by the 365-day staleness
cap, to decide whether the fundamental feature families carry real signal. The honest call is that the
diagnostic is **inconclusive on this tiny sample**, not that a tiny model is cleared.

## Whether tiny research model training is allowed next

**No.** `tiny_research_model_allowed_next` is `false`. The tiny-model gate requires a horizon that is
`ready_for_tiny_model`, and none is — the temporal-breadth guard (≥ 4 distinct IC years) is not met.
Training a tiny model now would fit one short, survivorship-biased regime and call overlapping-window
noise an edge. Instead, `expand_sec_universe_next` is `true`: a larger SEC fundamentals universe is
needed so that dense cross-sections exist across the whole 2016–2026 history, not just the recent
window, before any modeling decision is taken.

## Why no model is trained in Phase 3-K

This phase is a readiness diagnostic by design. It is explicitly allowed to compute IC, rank IC,
top/bottom bucket return spreads, and feature-family diagnostics, and nothing more. It fits no
regression, logistic, ridge, lasso, tree, or any other machine-learning estimator; it computes no
predictions, scores, trading rankings, or portfolio weights; and it writes no deployable model
artifact. Measuring signal must precede — and here, gates — any fitting.

## Why no production model candidate is created

A production model candidate implies a frozen, deployable artifact and an implied claim of edge. This
phase makes no such claim: it is a repo-local IC diagnostic on a 20-ticker research sample whose
verdict is *inconclusive*. It creates no production model candidate and writes no deployable model
artifact, and it claims no **production edge**.

## What Phase 3-L should do

Given `FUNDAMENTAL_IC_DIAGNOSTIC_INCONCLUSIVE_SMALL_SAMPLE`, Phase 3-L is **"Expand SEC Fundamentals
Universe Before Decision"**: expand the SEC fundamentals pipeline well beyond 20 tickers (toward a
broader, denser cross-section across the full price history) so that cross-sectional IC can be
measured across multiple regimes and years, and only then revisit whether a tiny research model is
warranted. On the other recommendation values Phase 3-L would instead be the tiny research-model
walk-forward (`PASSES`), expansion-before-modeling (`WEAK_BUT_EXPANDABLE`), a stop-modeling /
alternative-data decision (`FAILS_STOP`), or an input repair (`BLOCKED`).

## Safety guardrails

This phase **does not deploy**, **does not restart stock-api.service**, **does not enable** the
model-serving flag, **does not run migrations**, **does not write to production DB**, and it
**does not trade** or place orders. It uses no network, reads and writes nothing on the D: drive,
fetches no SEC data, calls no vendor, purchases no data, ingests no production data, performs no full
128-ticker ingestion, fits no model, computes no predictions / scores / portfolio weights, creates no
production model candidate, and writes no deployable model artifact. It only measures information
coefficients and bucket spreads on the existing Phase 3-J repaired validation panel and claims no
**production edge**.
