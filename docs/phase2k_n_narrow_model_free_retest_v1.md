# Phase 2K-N — Narrow Model-Free Retest for the Best Targeted Alpha Leads (v1)

_Implemented by `research/analyze_phase2k_n_narrow_model_free_retest.py` and validated by
`tests/test_phase2k_n_narrow_model_free_retest.py`. Phase 2K-N runs a narrow,
pre-registered, model-free retest of ONLY the 3 Phase 2K-M `NARROW_RETEST_CANDIDATE` pairs
on the expanded, survivorship-caveated D: panel. It recomputes the trailing signals and the
forward residual labels from the D: price history, re-measures strict residual rank-IC /
bootstrap / regime / concentration / stability diagnostics, and decides whether any lead
deserves a later confirmation phase. It **trains no model**, creates no model candidate, runs
no broad alpha screen, and never expands the candidate set._

> Scope: this phase reads the small Git-tracked Phase 2K-M / 2K-L / 2K-K / 2K-H JSON
> summaries and the read-only D: expanded price-history CSV / data-quality / build /
> survivorship JSONs, and writes one small results JSON in the C: repo. It is research
> tooling only: it **does not deploy**, it **does not restart stock-api.service**, it **does
> not enable** the model-v2 serving flag, it **does not run migrations**, it **does not write
> to production DB**, and it **does not trade**. No order placement, no automation, no model
> training, no model candidate, no broad alpha screen, no candidate-set expansion, and no
> write to the D: drive happens here, and it claims no **production edge**.

## Why Phase 2K-N follows Phase 2K-M

Phase 2K-M took the 5 Phase 2K-L `TARGETED_DIAGNOSTIC_CANDIDATE` leads, profiled each one,
and assigned a conservative follow-up type: **3** `NARROW_RETEST_CANDIDATE`, **2**
`HOLD_FOR_SECTOR_RELATIVE_VARIANT`, **0** `DIAGNOSTIC_ONLY`. It returned
`RUN_NARROW_MODEL_FREE_RETEST` and routed explicitly to Phase 2K-N. Before retesting
anything, Phase 2K-N confirms that routing (`phase == "2K-M"`,
`recommendation == RUN_NARROW_MODEL_FREE_RETEST`,
`recommended_next_phase.phase == "2K-N"` with the matching title, exactly 3
`NARROW_RETEST_CANDIDATE` leads, the Phase 2K-M recommendation's
`create_model_candidate_now` / `train_model_now` / `ran_new_d_screen` all `false`) and that
the 3 pre-registered pairs fixed in this source match the Phase 2K-M narrow-retest lead ids
exactly.

Phase 2K-M was a planning / diagnostic step that read only small JSON artifacts. Phase 2K-N
is a **meaningful empirical retest**: it re-opens the D: panel and recomputes the signals and
residual labels from scratch — but only for the 3 approved pairs, and still with no model.

## The 3 pre-registered leads

Only these candidate / horizon pairs are retested, each at its own horizon:

| Lead | Feature | Horizon | Prior 2K-K mean residual IC | Prior CI low | Prior 2K-L score |
|------|---------|---------|-----------------------------|--------------|------------------|
| `residual_price_momentum_12_1@5d` | `momentum_12_1` | 5d | 0.0132 | 0.0002 | 68.87 |
| `short_horizon_residual_reversal_5d@21d` | `reversal_5d` | 21d | 0.0103 | 0.0018 | 65.04 |
| `short_horizon_residual_reversal_21d@21d` | `reversal_21d` | 21d | 0.0151 | 0.0041 | 75.19 |

The set is **pre-registered**: the exact leads, horizons, residualization, ranking, and CI
method are fixed in the source before any data is touched, so the retest cannot be over-fit.
The candidate set is never expanded.

## The explicitly excluded leads and candidates

These are deliberately **not** retested in Phase 2K-N:

- `short_horizon_residual_reversal_5d@5d` and `short_horizon_residual_reversal_21d@5d` —
  the two Phase 2K-M `HOLD_FOR_SECTOR_RELATIVE_VARIANT` leads. Their IC sign is
  regime-dependent at the 5d horizon, so they must wait for a sector-relative feasibility
  assessment rather than a like-for-like retest.
- `avg_dollar_volume_21d` as a standalone alpha — stopped in Phase 2K-J after the Phase 2K-I
  expanded retest failure. It is excluded as a ranking signal and may only ever be computed
  as a size / liquidity control or diagnostic, never retested or ranked here.
- all other candidates and horizons — out of scope for this narrow phase.

## The D: dataset and the survivorship caveat

Phase 2K-N reads the same expanded free panel built in Phase 2K-G and validated in Phase
2K-H: `phase2k_g_expanded_price_history_free.csv` (read-only, with pandas), gated on the
Phase 2K-H readiness (`ready_for_retest == true`, data quality `PASS` / `PASS_WITH_CAVEAT`,
`point_in_time_membership_claimed == false`). The universe is **current-as-of /
survivorship-caveated**; every result is reported as survivorship-biased and carries the
Phase 2K-K / 2K-L / 2K-M caveat forward unchanged. A clean point-in-time membership universe
would only tighten, never rescue, these results, so no result here is a **production edge**.

## Feature construction (all trailing; no look-ahead)

Every feature is a trailing (point-in-time) rolling statistic computed per ticker from
adjusted OHLCV, using only data up to and including the as-of date:

- `daily_return`, SPY `spy_return`, and trailing cumulative returns `return_5d`,
  `return_21d`, `return_63d`, `return_252d`;
- the 3 pre-registered signals: `momentum_12_1` (the 252-day return excluding the most recent
  21 sessions — classic 12-1 momentum, bullish), `reversal_5d` (`-return_5d`, bullish
  reversal), `reversal_21d` (`-return_21d`, bullish reversal);
- the trailing risk controls `rolling_beta_63d`, `realized_vol_21d`, `realized_vol_63d`,
  `downside_vol_21d`, `rolling_corr_spy_63d`;
- `avg_dollar_volume_21d` as a control / diagnostic only — never retested or ranked.

No feature uses forward data.

## Residual label construction

For each pre-registered horizon (5d, 21d) the forward label is the **forward excess return
vs SPY** (ticker forward h-session return minus SPY's own forward h-session return), never
forward-filled. Each date's forward excess label is then neutralized by a **per-date
cross-sectional ordinary-least-squares projection** onto the trailing controls plus an
intercept (`numpy.linalg.lstsq`); the residual is the risk-neutralized label. When a date has
no usable control or too few names, the robust fallback is a cross-sectional demean. This is
a per-date linear projection, **not** a trained, persisted, or scored model.

## Narrow-retest metrics

For each of the 3 pairs Phase 2K-N measures only model-free, rank-only metrics on the
residual forward-excess label, comparing each against the prior Phase 2K-K / 2K-M numbers:
usable dates / tickers / observations, per-date residual rank IC and its mean / median,
information ratio, same-sign date fraction, moving-block bootstrap CI for the mean IC,
top-minus-bottom residual spread, top-decile residual hit rate, top-quintile / top-3 long-leg
concentration, leave-one-year-out sign stability, and SPY up/down and market-vol regime
splits. Nothing is fitted.

## Pass/fail gates

Each pair receives one conservative recommendation from the allowed vocabulary:

- **`KEEP_FOR_CONFIRMATION_DESIGN`** — only when the mean residual IC is positive and at or
  above the 0.03 floor (or convincingly close, ≥ 0.025, with strong supporting diagnostics:
  CI above zero, same-sign fraction ≥ 0.55, information ratio ≥ 0.10, year- and
  regime-stable, not over-concentrated), the same-sign date fraction clears 0.52, the
  bootstrap CI lower bound is above zero, the IC sign is leave-one-year-out stable, there is
  no regime sign dependence, no severe long-leg concentration, and enough observations remain.
- **`RESEARCH_LEAD_RECONFIRMED`** — the positive, above-zero, year-stable edge is reproduced
  but the IC is still sub-floor (the expected dominant blocker); reconfirmed as a research
  lead but not confirmable yet, and not a model candidate.
- **`DROP_AFTER_NARROW_RETEST`** — the edge weakens or flips sign, the CI does not clear zero,
  the sign is year-unstable, or a regime / concentration robustness gate fails.
- **`NEED_POINT_IN_TIME_UNIVERSE`** — too little usable data remains, or the retest was not
  executed against the D: panel, so the survivorship / current-membership caveat is the main
  blocker.

The overall recommendation is `PROCEED_TO_CONFIRMATION_DESIGN` if at least one pair is a
KEEP; otherwise `RESEARCH_LEADS_RECONFIRMED_BUT_NOT_CONFIRMABLE` if at least one is
reconfirmed; otherwise `DROP_NARROW_RETEST_LEADS` if all fail; otherwise
`NEED_POINT_IN_TIME_UNIVERSE_BEFORE_CONFIRMATION` if the data caveat is the main blocker.

## Result and recommendation

The committed artifact is a pre-execution snapshot: it is schema-valid with null metrics,
`retest_executed = false`, every pair at `NEED_POINT_IN_TIME_UNIVERSE`, and an overall
`NEED_POINT_IN_TIME_UNIVERSE_BEFORE_CONFIRMATION` until the analyzer is run on the host where
the D: dataset is mounted. Running the analyzer there populates the real, data-driven
metrics and recommendation and overwrites the snapshot. Until then no lead proceeds to
confirmation design, no model candidate is created, and no **production edge** is claimed.

## Why no model candidate is created yet

Phase 2K-N is a retest, not a model build. Even a `KEEP_FOR_CONFIRMATION_DESIGN` only earns a
later, separately gated confirmation-design phase — never model training or model-candidate
design. The model-candidate gate stays locked: no candidate may be created until a feature
clears the IC floor **and** a stricter, separately gated walk-forward confirmation battery on
real data spanning more than one market regime. Phase 2K-N trains nothing, fits nothing,
creates no model candidate, keeps the model-v2 serving flag disabled, and claims no
**production edge**.

## What Phase 2K-O should do

`recommended_next_phase` always routes to **Phase 2K-O**, with the title and purpose chosen
by the retest outcome:

- if any lead is `KEEP_FOR_CONFIRMATION_DESIGN` → *Confirmation Design for Narrow Retest
  Survivors* (design a stricter walk-forward confirmation phase for the surviving model-free
  signal(s), still no model training and no deployment);
- else if any lead is `RESEARCH_LEAD_RECONFIRMED` → *Decision Gate for Reconfirmed But
  Sub-Floor Leads* (decide whether the reproducible sub-floor leads deserve sector-relative
  variants, better data, or a backlog refresh);
- else if all leads fail → *Alpha Backlog Refresh v3 After Narrow Retest Failure*;
- else → *Point-in-Time Universe Decision Before Confirmation* (decide whether proper
  point-in-time membership data is required before any further confirmation work).

Like every phase in this track, Phase 2K-O **does not deploy**, **does not restart
stock-api.service**, **does not enable** the model-v2 flag, **does not run migrations**,
**does not write to production DB**, and **does not trade**, and it claims no **production
edge**.

## Safety flags (from the results JSON)

```
database_touched        = false
database_write_executed = false
migration_executed      = false
deployment_executed     = false
model_v2_enabled        = false
production_edge_claimed = false
no_trading              = true
no_orders               = true
no_automation           = true
model_trained           = false
model_candidate_created = false
ran_broad_alpha_screen  = false
candidate_set_expanded  = false
d_drive_written         = false
retest_executed         = <true only after the analyzer runs against the D: panel>
```

The `overall_recommendation` and `interpretation` blocks additionally record
`create_model_candidate_now = false`, `train_model_now = false`, `deploy_now = false`,
`model_trained = false`, `model_candidate_created = false`, `authorized_to_serve_model =
false`, `ran_broad_alpha_screen = false`, and `candidate_set_expanded = false`.

## Conclusion

Phase 2K-N retests exactly the 3 Phase 2K-M `NARROW_RETEST_CANDIDATE` pairs on the expanded,
survivorship-caveated D: panel with strict, pre-registered, model-free diagnostics, holds the
two regime-dependent reversal leads for a sector-relative variant, keeps the stopped liquidity
candidate excluded, and routes to the appropriate Phase 2K-O. It trains nothing, fits nothing,
creates no model candidate, runs no broad alpha screen, never expands the candidate set, never
writes to the D: drive, and claims no **production edge**.
