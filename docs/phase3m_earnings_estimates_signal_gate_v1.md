# Phase 3-M — Earnings Estimates / Surprise Data Gate + Immediate Signal Test (v1)

> Status note: the provider-access, earnings-events, feature, combined-panel, IC-results,
> comparison, and final-recommendation sections below are finalized from the committed run artifact
> `research/output/phase3m_earnings_estimates_signal_gate.json` and its CSVs. When the run executes
> with no supported provider key in the environment, the artifact records the
> `EARNINGS_ESTIMATES_GATE_BLOCKED_NEEDS_API_KEY` result described under "Final recommendation".

## Why Phase 3-M follows Phase 3-L

Phase 3-L (`SEC_UNIVERSE_SIGNAL_GATE_WEAK_BUT_EXPAND_OR_ADD_REVISIONS`) expanded the SEC fundamental
pipeline to the full current 128-equity universe and produced a clean, leakage-free, multi-regime
panel — 303,201 aligned rows over 2016–2026, 11 distinct dense IC years, zero leakage. That decisively
fixed the Phase 3-K single-regime/tiny-sample limitation. But on signal strength it fell short of the
research-model gate: only 4 moderate-or-better features and 1 strong feature, versus the required
≥ 5 / ≥ 2. Phase 3-L therefore set `research_model_allowed_next = false` and routed explicitly to
Phase 3-M to add richer structured data — analyst earnings estimates / EPS surprise — *before* any
model is trained. Phase 3-M is that step, and it remains research-only and safety-controlled: it
**does not deploy**, **does not restart stock-api.service**, **does not enable** any serving flag,
**does not run migrations**, **does not write to production DB**, and **does not trade**.

## Why SEC-only fundamentals are insufficient

SEC as-reported fundamentals are backward-looking accounting facts. They carry weak, mostly
size/recency-driven cross-sectional signal (the Phase 3-L strong feature was `log_total_assets`),
and — critically — SEC public data exposes no forward analyst consensus and no estimate-revision
history, so earnings-surprise and revision-momentum effects (well-documented sources of
cross-sectional return predictability) simply cannot be constructed from EDGAR. The Phase 3-L
artifact recorded this directly (`earnings_revisions_gap.provider_selection_required = true`). Phase
3-M closes that specific gap by layering a configured earnings-estimates provider onto the
already-validated, leakage-free 128-name panel.

## Selected provider and why

A provider is selected only from an API key already present in the environment, in strict priority
order:

1. **Alpha Vantage** (`ALPHAVANTAGE_API_KEY`) — preferred first implementation. Its `EARNINGS`
   endpoint returns quarterly `reportedDate`, `reportedEPS`, `estimatedEPS`, `surprise`, and
   `surprisePercentage`, which map directly onto the canonical earnings event and give a clean,
   point-in-time report date.
2. **FMP** (`FMP_API_KEY`) — `earnings-surprises` (actual vs estimate per report date).
3. **Finnhub** (`FINNHUB_API_KEY`) — `stock/earnings` (actual / estimate / surprise / surprise %).
4. **Intrinio** (`INTRINIO_API_KEY`) — Zacks EPS surprises.

If multiple keys exist, Alpha Vantage is chosen first. If no supported key exists, the phase writes a
`EARNINGS_ESTIMATES_GATE_BLOCKED_NEEDS_API_KEY` result and does not fabricate data. The committed run
in this environment found **no supported key** and recorded that BLOCKED result; the provider
implementation, normalization, feature construction, combination, and IC gate are all present and
exercised by the test suite and would run on the next execution once a key is configured.

## API key / cache behavior

Keys are read **only** from environment variables. They are never hardcoded, never printed, never
written to any artifact, and the user is never prompted for a key inside code. The
`provider_access_report.json` records **only** the four supported environment-variable names mapped
to booleans (present/absent), the selected provider, a **redacted** endpoint template (the key is
replaced by the literal `REDACTED`), entitlement status, request/cache counts, and any rate-limit
error count — never a key value. Behavior is cache-first: raw provider **response bodies** (which
contain no key) are cached under
`research/output/phase3m_earnings_estimates_signal_gate/raw/`, keyed by provider and symbol; the
request URL — which carries the key — is never persisted. Reruns read the cache and issue no network
requests. Per-provider minimum request intervals are applied conservatively (Alpha Vantage 15 s to
respect the ~5-requests/minute free tier), under a hard cap of 200 total requests. Nothing is
purchased and no account is created. If a provider returns a rate-limit / entitlement /
premium-required response, the phase writes a `EARNINGS_ESTIMATES_GATE_BLOCKED_PROVIDER_LIMIT` result
instead of crashing.

## Earnings event normalization

Each provider response is normalized to a canonical event with columns `ticker`,
`fiscal_date_ending`, `reported_date`, `reported_eps`, `estimated_eps`, `surprise`,
`surprise_percentage`, `source`, `availability_date`, `point_in_time_usable`, `provider`, and
`validation_note` (`earnings_events_universe.csv`). Where a provider omits `surprise` /
`surprise_percentage`, they are derived from reported − estimated EPS. Events without a report date
are quarantined as not point-in-time usable.

## Point-in-time rules

`availability_date` is the **reported_date**, never the `fiscal_date_ending` — an event is treated as
knowable only from the day it is reported, and is **never** back-dated to the quarter end. An event
becomes active starting the first trading day strictly after `reported_date`. Future estimates that
post-date the report are never treated as if known earlier. Events whose reported date is on or
before the fiscal period end are flagged suspect and marked not usable.

## Earnings feature construction

Trailing-only, point-in-time features are built per ticker over events ordered by report date:
`eps_surprise`, `eps_surprise_pct`, `positive_surprise_flag`, `negative_surprise_flag`,
`surprise_magnitude_abs`, `trailing_4q_avg_surprise_pct`, `trailing_4q_positive_surprise_rate`,
`trailing_8q_avg_surprise_pct`, `surprise_acceleration`, `days_since_last_earnings`,
`earnings_event_recency_bucket`, and `estimate_revision_proxy`. Trailing aggregates use only strictly
prior events. `estimate_revision_proxy` requires a provider-supplied prior estimate for the same
event; the free endpoints used here do not expose a prior-estimate time series, so it is left blank
and documented as unavailable (`earnings_features_universe.csv`).

## Combined panel construction

The earnings features are joined to the Phase 3-L aligned panel by `ticker` and `scoring_date`. An
earnings event contributes to a scoring date only when its `availability_date` is strictly before the
scoring date and its age is within the 180-calendar-day staleness cap. The existing 21 / 63 / 126-day
Phase 3-L forward labels are **reused unchanged** — no new model target is computed and no label is
forward-filled. To keep the on-disk artifact Git-safe (the Phase 3-L panel alone is ~98 MB), the
written `combined_fundamental_earnings_panel.csv` is **compact**: it carries the join keys, earnings
event metadata, the earnings features, and the reused labels only; the 23 SEC features are not
duplicated to disk (they remain in the Phase 3-L panel) and are joined in-memory for the combined IC.
This keeps every individual artifact well under 50 MB.

## Leakage checks

Per active row, the gate verifies: the active earnings availability is present; it is strictly before
the scoring date; `availability_date` equals the report date and not the fiscal period end; the
active event is within the 180-day staleness cap; and the labels are reused from Phase 3-L rather than
recomputed (`decision_table.csv` / `leakage_check_summary`). A non-blocked run must show zero leakage
failures; any failure forces an inconclusive routing rather than a PASS.

## IC results

Signal is measured exactly as in Phase 3-L: cross-sectional daily Spearman rank IC against
`forward_excess_return_vs_spy` and `forward_return_rank_by_date`, with a partial floor of 15 names and
a dense floor of 25 names per date, summarized into mean / median IC, hit rate, IR, and
quintile / top-10 bucket spreads. IC is computed separately for three feature groups — earnings-only,
SEC-only baseline, and combined — so the incremental value of earnings data is visible
(`earnings_feature_ic_summary.csv`, `earnings_feature_family_ic_summary.csv`,
`earnings_horizon_readiness_summary.csv`). The committed BLOCKED run computed no IC (no provider data);
the headline numbers populate on the next keyed run.

## Comparison to SEC-only Phase 3-L

The phase reads the committed Phase 3-L `feature_ic_summary.csv` to recover the SEC-only baseline
(moderate-or-better and strong feature counts) and reports, in `comparison_to_phase3l_sec_only`,
whether earnings-only or combined features raise the moderate / strong counts above that baseline.
"Improvement over SEC-only" is a precondition for both PASS and WEAK_BUT_USEFUL.

## Final recommendation

The recommendation is one of `EARNINGS_ESTIMATES_SIGNAL_GATE_PASSES_RESEARCH_MODEL_ALLOWED`,
`EARNINGS_ESTIMATES_SIGNAL_GATE_WEAK_BUT_USEFUL`, `EARNINGS_ESTIMATES_GATE_BLOCKED_NEEDS_API_KEY`,
`EARNINGS_ESTIMATES_GATE_BLOCKED_PROVIDER_LIMIT`, `EARNINGS_ESTIMATES_SIGNAL_GATE_FAILS`, or
`EARNINGS_ESTIMATES_SIGNAL_GATE_INCONCLUSIVE_COVERAGE`. PASS requires a selected provider, ≥ 90
processed tickers, ≥ 75 tickers with earnings features, ≥ 75,000 combined rows, zero leakage, label
coverage at or above the Phase 3-L gates, and earnings-only or combined features producing ≥ 5
moderate-or-better, ≥ 2 strong, ≥ 2 stable families, and ≥ 6 distinct dense IC years while improving
on the SEC-only baseline. **In this environment no supported provider key was present, so the
committed run is `EARNINGS_ESTIMATES_GATE_BLOCKED_NEEDS_API_KEY`**: no data was fetched, faked, or
modeled; `selected_provider` is null; and the next phase is configuration, not modeling. The full
selected recommendation and decision table are in
`research/output/phase3m_earnings_estimates_signal_gate.json`.

## Whether a research model is allowed next

`research_model_allowed_next` is `true` **only** under
`EARNINGS_ESTIMATES_SIGNAL_GATE_PASSES_RESEARCH_MODEL_ALLOWED`. Under every other recommendation it is
`false`: a BLOCKED-needs-key result routes to configuring a provider, a BLOCKED-provider-limit result
routes to an entitlement decision, a weak result routes to a deeper provider / revision history, a
failing result routes to other rich data, and an inconclusive result routes to coverage repair. All
route to Phase 3-N. Even under PASS the next phase is a **research-only** walk-forward, never a
production candidate.

## Why no model is trained in Phase 3-M

This phase is a signal gate by design. It is allowed to ingest provider earnings data, build features,
reuse Phase 3-L validation-only labels, join the panel, and compute IC / bucket-spread / family /
yearly / sector diagnostics — and nothing more. It fits no regression, logistic, ridge, lasso, tree,
or any other machine-learning estimator; it computes no predictions, scores, trading rankings, or
portfolio weights; and it writes no deployable model artifact. Measuring whether earnings data adds
signal must precede — and here, gates — any fitting.

## Why no production model candidate is created

A production model candidate implies a frozen, deployable artifact and an implied claim of edge. This
phase makes no such claim: it is a research IC gate on a current-as-of, survivorship-biased universe.
It creates no production model candidate, writes no deployable model artifact, and claims no
**production edge**.

## Safety guardrails

This phase **does not deploy**, **does not restart stock-api.service**, **does not enable** the
model-serving flag, **does not run migrations**, **does not write to production DB**, and it **does
not trade** or place orders. It reads only Phase 3-L outputs on the C: drive (no D: drive access at
all), contacts only the single configured provider host (no SEC network, no yfinance, no paid vendor
purchase, no account creation), reuses Phase 3-L validation-only labels, fits no model, computes no
predictions / scores / portfolio weights, creates no production model candidate, and writes no
deployable model artifact. Provider API keys are read from the environment only and are never printed
or written to any artifact. It only measures information coefficients and bucket spreads on the
combined validation panel and claims no **production edge**.
