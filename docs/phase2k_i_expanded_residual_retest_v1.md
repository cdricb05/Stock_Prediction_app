# Phase 2K-I — Expanded-Dataset Residual Signal Retest (v1)

_Implemented by `research/analyze_phase2k_i_expanded_residual_retest.py` and validated by
`tests/test_phase2k_i_expanded_residual_retest.py`. This phase retests the lone Phase 2K-C
robustness survivor — `avg_dollar_volume_21d` — on the larger, longer, survivorship-caveated
panel produced by the manual Phase 2K-H build. It is a **model-free retest only**: it trains
nothing, fits nothing, creates no model candidate, and claims no **production edge**._

> Scope: this phase reads the Phase 2K-H summary, the Phase 2K-C / 2K-B / 2L-B JSON summaries,
> and the D: drive expanded CSVs / JSONs, recomputes trailing features, and writes one small
> results JSON in the C: repo. It is research tooling only: it **does not deploy**, it
> **does not restart stock-api.service**, it **does not enable** the model-v2 serving flag, it
> **does not run migrations**, it **does not write to production DB**, and it **does not trade**.
> No order placement, no automation, no model training, no model candidate, and no live build
> is run here, and it claims no **production edge**.

## Why Phase 2K-I follows Phase 2K-H

Phase 2L-B ran the model-free, rank-only walk-forward validation for `avg_dollar_volume_21d`
on the thin ~40-name, single-regime 2023–2026 export and returned `NEED_MORE_DATA`: only ~7
effective validation observations remained after the horizon-length embargo, so the signal
could not be conclusively tested. The disciplined response (Phases 2K-D → 2K-H) was to acquire
**more data, not to train a model**: a free, broad, multi-year, survivorship-caveated price /
volume panel. Phase 2K-H built that panel manually on the D: data root and its tracking
analyzer confirmed it was ready (`manual_build_detected = true`, `ready_for_retest = true`,
`recommended_next_phase = 2K-I`, data-quality `PASS_WITH_CAVEAT`,
`point_in_time_membership_claimed = false`). Phase 2K-I is the retest on that expanded panel.

## The D: dataset used

The analyzer reads the manual 2K-H outputs from the D: data root (never the C: repo, never the
network):

- `D:\Stock_Prediction_app_data\phase2k_g\output\phase2k_g_expanded_price_history_free.csv`
  — the longer, broader adjusted-OHLCV + volume panel (≈10.4 years, ~129 names incl. SPY).
- `…\phase2k_g_data_quality_report.json` — the data-quality status (`PASS` / `PASS_WITH_CAVEAT`
  / `FAIL`) and stats.
- `…\phase2k_g_data_build_summary.json` — reproducible run metadata.
- `…\phase2k_g_survivorship_caveat.json` — the survivorship-caveat metadata.

The expanded **scored** CSV carries only basic columns (dollar volume, benchmark close, daily
return), so Phase 2K-I recomputes the candidate and the risk controls itself from the price
history. The analyzer is read-only with respect to every D: file; it writes exactly one small
JSON — `research/output/phase2k_i_expanded_residual_retest.json` — in the C: repo.

## Survivorship caveat (carried forward)

The expanded universe is a **current-as-of** membership approximation, never a point-in-time
constituent list (`point_in_time_membership_claimed = false`). Every result in this phase is
therefore reported as **survivorship-biased / current-membership caveated**. The survivorship
caveat is an explicit pass/fail gate, and the recommendation block flags
`results_are_survivorship_biased = true`. A survivorship-caveated retest pass is still **not a
production edge** and is not permission to train or serve a model — it only permits a later,
separately gated confirmation phase.

## Candidate tested

The single required candidate is **`avg_dollar_volume_21d`** at its 63-session primary horizon
(with the 21-session horizon reported for comparison). It is the only feature Phase 2K-C rated
`KEEP_FOR_MODEL_RESEARCH`; the read is bullish (`reference_sign = +1`).

## Feature and residual-label construction

All features are **trailing, point-in-time** rolling statistics computed per ticker from the
expanded adjusted-OHLCV history — they use only data up to and including the as-of date, never
forward information:

- `dollar_volume = adjusted_close × volume`, `avg_dollar_volume_21d` (trailing 21-session mean),
  `daily_return` (per-ticker `pct_change`, never forward filled);
- risk controls `rolling_beta_63d`, `realized_vol_21d`, `realized_vol_63d`, `downside_vol_21d`,
  `rolling_corr_spy_63d`, plus a market-level SPY trailing 21-session realized-vol regime scalar.

The residual label reproduces the Phase 2K-B / 2K-C / 2L-B construction: the forward
excess-vs-SPY return (horizons 21d and 63d) is neutralized, **per date and cross-sectionally**,
against the trailing risk controls via OLS (usable controls + intercept), with a cross-sectional
demean fallback when a date has no usable control or too few names. The label is strictly
forward, the controls are trailing, and each per-date fit uses no forward information, so there
is **no look-ahead** and **no forward-filled label**.

## Retest metrics

For `avg_dollar_volume_21d` at the 63d primary horizon (and 21d for comparison) the analyzer
measures, on the full expanded sample: per-date residual rank IC, mean and median residual IC,
information ratio, a moving-block bootstrap confidence interval for the mean IC, the top-minus-
bottom residual spread, the top-decile residual hit rate, top-quintile ticker concentration, a
leave-one-year-out stability check, and regime splits (SPY forward up/down, high/low market
realized vol). It also computes degradation versus the Phase 2K-C full-sample residual IC.

## Walk-forward metrics

A sequential, embargoed, **model-free** walk-forward is run on the labeled session calendar:
minimum training window 252 sessions, a 63-session (horizon-length) embargo, a 63-session
validation window, stepping forward 63 sessions per fold — **no random cross-validation**, and
nothing is fitted. Names are ranked by the single feature; only out-of-sample validation
residual IC is measured per fold. The analyzer reports per-fold and pooled validation residual
IC, whether the majority of validation windows share the reference sign, a pooled bootstrap CI,
and a leave-one-fold-out stability check.

## Pass/fail result

The verdict is conservative and drawn only from the allowed vocabulary
(`recommendation.allowed_values`):

- **`EXPANDED_RETEST_PASS`** — only if the mean residual IC is positive and above the floor, a
  majority of dates and validation folds share the sign, the bootstrap CI excludes (is
  materially above) zero, no single year or regime drives the result, concentration is not
  excessive, the result does not materially degrade versus Phase 2K-C, the survivorship caveat
  is carried forward, and enough effective observations remain after the embargo.
- **`EXPANDED_RETEST_FAIL`** — enough data exists but one or more gates fail; the signal does
  not hold up on the larger survivorship-caveated panel.
- **`NEED_MORE_DATA_OR_PIT_UNIVERSE`** — the Phase 2K-H build is not ready, the data quality is
  inadequate, the survivorship / current-membership caveat prevents a meaningful conclusion, or
  too few effective observations remain to conclude.

Whatever the verdict, the recommendation block records `create_model_candidate_now = false`,
`train_model_now = false`, `deploy_now = false`, and `production_edge_claimed = false`.

## Why no model candidate is created yet

This phase retests a signal; it builds no model. The Phase 2K-A model-candidate gate stays
locked: no model candidate may be created until a feature family passes the IC sweep **and** the
out-of-sample / walk-forward battery on real data spanning more than one market regime, with no
single-regime artifact, no excessive ticker concentration, and no look-ahead leakage — and even
then only a simple, separately gated single-feature design. Phase 2K-I trains nothing, fits
nothing, scores nothing, serves nothing, and creates no model candidate. The model-v2 serving
flag stays disabled, and no **production edge** is claimed.

## Recommended next phase

`recommended_next_phase` always routes to **Phase 2K-J**, with the title and purpose set by the
verdict:

- `EXPANDED_RETEST_PASS` → **Survivorship-Caveated Walk-Forward Residual Signal Confirmation**:
  confirm the expanded retest under stricter walk-forward gates before any model-candidate
  design.
- `EXPANDED_RETEST_FAIL` → **Alpha Backlog Refresh After Expanded Retest Failure**: stop the
  liquidity candidate and refresh the alpha backlog using the expanded-data diagnostics.
- `NEED_MORE_DATA_OR_PIT_UNIVERSE` → **Point-in-Time Universe Decision Before Further Retests**:
  decide whether to obtain a proper point-in-time membership universe before retesting again.

In every branch the next phase **does not deploy**, **does not restart stock-api.service**,
**does not enable** the model-v2 flag, **does not run migrations**, **does not write to
production DB**, and **does not trade**, and it claims no **production edge**.

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
```

The `recommendation` block additionally records `create_model_candidate_now = false`,
`train_model_now = false`, `deploy_now = false`, `production_edge_claimed = false`, and
`results_are_survivorship_biased = true`, and the `interpretation` block confirms
`model_trained = false`, `model_candidate_created = false`, and `authorized_to_serve_model =
false`.

## Conclusion

Phase 2K-I is a disciplined, model-free retest of `avg_dollar_volume_21d` on the expanded,
survivorship-caveated free panel — recomputing trailing features, rebuilding the same residual
label, and measuring rank-only full-sample and walk-forward metrics before routing to Phase
2K-J. It trains nothing, creates no model candidate, and claims no **production edge**; every
result is reported as survivorship-biased.
