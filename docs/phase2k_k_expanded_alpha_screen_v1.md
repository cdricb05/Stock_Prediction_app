# Phase 2K-K — Expanded Dataset Model-Free Alpha Screen (v1)

_Implemented by `research/analyze_phase2k_k_expanded_alpha_screen.py` and validated by
`tests/test_phase2k_k_expanded_alpha_screen.py`. Phase 2K-K runs a new model-free screen of
the prioritized price/volume alpha candidates from the Phase 2K-J refreshed backlog on the
expanded, survivorship-caveated D: panel, and decides which candidate / horizon pairs, if
any, deserve a later walk-forward confirmation phase. It reads the D: price/volume history
read-only and **trains no model**._

> Scope: this phase reads the small Git-tracked Phase 2K-J / 2K-I / 2K-H JSON summaries and
> the D: expanded price-history CSV plus its data-quality / build / survivorship JSONs
> (read-only), and writes one small results JSON in the C: repo. It is research tooling
> only: it **does not deploy**, it **does not restart stock-api.service**, it **does not
> enable** the model-v2 serving flag, it **does not run migrations**, it **does not write to
> production DB**, and it **does not trade**. No order placement, no automation, no model
> training, no model candidate, and no write to the D: drive happens here, and it claims no
> **production edge**.

## Why Phase 2K-K follows Phase 2K-J

Phase 2K-I retested the lone Phase 2K-C survivor, `avg_dollar_volume_21d`, on the expanded
panel and returned `EXPANDED_RETEST_FAIL` (its 63-day residual rank IC reversed negative).
Phase 2K-J stopped that liquidity candidate as a standalone alpha, refreshed the alpha
backlog toward diversified price/volume hypotheses, returned `REFRESH_ALPHA_BACKLOG`, and
routed explicitly to Phase 2K-K — *Expanded Dataset Model-Free Alpha Screen*. Phase 2K-K
confirms that routing (`recommendation == REFRESH_ALPHA_BACKLOG`,
`failed_candidate_decision.status == STOPPED_AFTER_EXPANDED_RETEST_FAIL`,
`standalone_liquidity_candidate_stopped == true`, `recommended_next_phase.phase == "2K-K"`)
before screening anything.

## The stopped liquidity candidate is excluded as a standalone alpha

`avg_dollar_volume_21d` is **not** re-screened as a standalone alpha. The
`stopped_candidate_policy` records its status `STOPPED_AFTER_EXPANDED_RETEST_FAIL`,
`excluded_as_standalone_alpha = true`, and `allowed_as_control_or_diagnostic_only = true`.
It appears only in `excluded_candidates` and, computationally, as a trailing size /
liquidity control or diagnostic — never as a ranking signal in `screened_candidates`.

## The expanded D: dataset

The screen reads the Phase 2K-H manual, free, survivorship-caveated panel on the D: data
root (`D:\Stock_Prediction_app_data\phase2k_g\output`): ~10.4 years, ~129 names (incl. SPY),
adjusted OHLCV + volume, data-quality `PASS_WITH_CAVEAT`. The CSV is read-only; the analyzer
never writes, moves, deletes, rewrites, or copies any D: file, and it creates no large repo
files — only the single small results JSON.

## Survivorship caveat

The universe is a current-as-of membership approximation, not a point-in-time constituent
set (`point_in_time_membership_claimed == false`). Every result is therefore reported as
survivorship-biased and carried forward as such. A clean point-in-time universe would only
tighten, never rescue, a screen result; no **production edge** is claimed regardless of the
screen outcome.

## Candidates screened

Five standalone price/volume candidates, each oriented bullish (a positive residual rank IC
is the hypothesised direction), screened at the 5d, 21d, and 63d horizons:

- `residual_price_momentum_12_1` — 252-day return excluding the most recent 21 sessions;
- `short_horizon_residual_reversal_5d` — negated trailing 5-day return;
- `short_horizon_residual_reversal_21d` — negated trailing 21-day return;
- `volatility_adjusted_momentum_63d` — 63-day return scaled by trailing 63-day realized vol;
- `volatility_adjusted_momentum_126d` — 126-day return scaled by trailing 126-day realized vol.

## Features and labels

All features are trailing point-in-time statistics computed per ticker from adjusted close
and volume: daily return, dollar volume, `avg_dollar_volume_21d` (control/diagnostic only),
trailing cumulative returns (5/21/63/126/252d), `momentum_12_1`, the reversal signals, the
volatility-adjusted momentum signals, realized volatility (21/63/126d), downside vol (21d),
rolling beta (63d), and rolling SPY correlation (63d). No feature uses forward data.

Labels are strictly forward and never forward-filled: the forward h-session excess return
vs SPY for h in {5, 21, 63}, then beta/vol-neutralized by a per-date cross-sectional OLS of
the forward excess label on the trailing controls (`rolling_beta_63d`, `realized_vol_21d`,
`realized_vol_63d`, `downside_vol_21d`, `rolling_corr_spy_63d`), with a cross-sectional
demean fallback. Each per-date fit uses no forward information, so there is no look-ahead.

## Model-free screen metrics

For every candidate / horizon pair the analyzer measures only rank-only metrics on the
residual label: per-date residual rank IC (mean, median, std), information ratio,
same-sign-as-reference date fraction, a moving-block bootstrap CI for the mean IC,
top-minus-bottom quintile residual spread, top-decile residual hit rate, top-quintile
ticker concentration, leave-one-year-out sign stability, and SPY up/down and high/low
market-volatility regime splits, plus enough-data flags. Nothing is fitted or trained.

## Candidate recommendations

Each candidate / horizon pair receives one conservative verdict:

- `KEEP_FOR_WALK_FORWARD_CONFIRMATION` — only when the mean residual IC is positive and at
  or above the floor, a majority of dates share the reference sign, the bootstrap CI lower
  bound is materially above zero, no single year or regime drives the result, and
  concentration is not excessive;
- `RESEARCH_LEAD_ONLY` — directionally positive but failing one or more robustness /
  stability gates;
- `DROP` — enough data exists and the direction / IC / stability fail;
- `NEED_MORE_DATA_OR_PIT_UNIVERSE` — too little usable data (or the survivorship caveat)
  prevents a meaningful conclusion.

The overall `recommendation` is `MODEL_FREE_SCREEN_HAS_CONFIRMATION_CANDIDATES`,
`MODEL_FREE_SCREEN_HAS_RESEARCH_LEADS_ONLY`, `MODEL_FREE_SCREEN_ALL_DROPPED`, or
`NEED_MORE_DATA_OR_PIT_UNIVERSE`, derived from the per-pair verdict counts.

> Note on the committed snapshot: the analyzer computes the IC battery on the full D: panel.
> The committed `research/output/phase2k_k_expanded_alpha_screen.json` is a pre-execution
> structural snapshot (`screen_executed = false`, null metrics, `NEED_MORE_DATA_OR_PIT_UNIVERSE`)
> per the phase operating rule that the analyzer is validated manually; running
> `python research/analyze_phase2k_k_expanded_alpha_screen.py` on the host where the D:
> dataset is mounted populates the real screen metrics and the data-driven recommendation.

## Why no model candidate is created yet

This phase screens features model-free; it builds no model. The model-candidate gate stays
locked: no model candidate may be created until a feature family passes the IC screen **and**
a stricter, separately gated walk-forward battery on real data spanning more than one market
regime, with no single-regime artifact, no excessive concentration, and no look-ahead. A
`KEEP` here only earns that later walk-forward phase. Phase 2K-K trains nothing, fits
nothing, creates no model candidate, keeps the model-v2 serving flag disabled, and claims no
**production edge**.

## What Phase 2K-L should do

`recommended_next_phase` routes to **Phase 2K-L**, with the title and purpose chosen by the
screen outcome:

- if any pair is `KEEP_FOR_WALK_FORWARD_CONFIRMATION` → *Walk-Forward Confirmation for
  Expanded Alpha Screen Survivors* (confirm survivors with stricter embargoed walk-forward
  gates before any model-candidate design);
- else if any pair is `RESEARCH_LEAD_ONLY` → *Research Lead Diagnostics After Expanded Alpha
  Screen*;
- else if all pairs are `DROP` → *Alpha Backlog Refresh v2 After Expanded Screen Failure*;
- else → *Point-in-Time Universe Decision Before Further Alpha Screens*.

Like every phase in this track, Phase 2K-L **does not deploy**, **does not restart
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
```

The `recommendation` and `interpretation` blocks additionally record
`create_model_candidate_now = false`, `train_model_now = false`, `deploy_now = false`,
`model_trained = false`, `model_candidate_created = false`, and
`authorized_to_serve_model = false`.

## Conclusion

Phase 2K-K screens the refreshed price/volume alpha backlog model-free on the expanded,
survivorship-caveated panel, excludes the stopped standalone liquidity candidate, applies
conservative per-pair gates, and routes to the appropriate Phase 2K-L. It reads the D:
dataset read-only, trains nothing, creates no model candidate, and claims no **production
edge**.
