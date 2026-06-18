# Phase 2K-J — Alpha Backlog Refresh After Expanded Retest Failure (v1)

_Implemented by `research/analyze_phase2k_j_alpha_backlog_refresh.py` and validated by
`tests/test_phase2k_j_alpha_backlog_refresh.py`. Phase 2K-J is the disciplined response to the
Phase 2K-I expanded-retest **failure**. It is a small, read-only planning / diagnostic
artifact: it reads the small JSON summaries of prior phases, stops the failed liquidity
candidate, extracts the diagnostic lessons, refreshes the alpha backlog, and recommends the next
model-free research phase. It computes no new alpha signal on the D: dataset and **trains no
model**._

> Scope: this phase reads only small Git-tracked JSON summaries (never the large D: CSVs) and
> writes one small results JSON in the C: repo. It is research tooling only: it **does not
> deploy**, it **does not restart stock-api.service**, it **does not enable** the model-v2
> serving flag, it **does not run migrations**, it **does not write to production DB**, and it
> **does not trade**. No order placement, no automation, no model training, no model candidate,
> and no new D: dataset computation is run here, and it claims no **production edge**.

## Why Phase 2K-J follows Phase 2K-I

Phase 2K-I retested the lone Phase 2K-C robustness survivor — `avg_dollar_volume_21d` — on the
expanded, longer, broader, survivorship-caveated free panel built manually in Phase 2K-H. It
returned **`EXPANDED_RETEST_FAIL`** and routed explicitly to Phase 2K-J — *Alpha Backlog Refresh
After Expanded Retest Failure*. The candidate's primary 63-day residual rank IC turned **negative
(≈ −0.049)** on the larger panel, the model-free walk-forward did not hold the reference sign
(pooled validation IC ≈ −0.043 across 35 folds), and the result degraded materially versus the
thin-data Phase 2K-C IC (+0.067). Phase 2K-J takes the disciplined next step: stop the failed
candidate and refresh the backlog — it does **not** train a model.

## The failed candidate is stopped

`avg_dollar_volume_21d` is recorded with status **`STOPPED_AFTER_EXPANDED_RETEST_FAIL`**: it
failed on the expanded survivorship-caveated panel and is stopped **as a standalone alpha**. The
`failed_candidate_decision` block records `create_model_candidate_now = false`,
`train_model_now = false`, `deploy_now = false`, and `production_edge_claimed = false`. No model
candidate is created, no model is trained, and no **production edge** is claimed.

## Diagnostic lessons from the expanded retest

The `failure_diagnostics` block captures what broke down and why:

- **Full-sample residual IC was negative** on the expanded panel (the apparent thin-data edge
  did not merely weaken — it reversed sign).
- **Walk-forward validation failed**: the pooled out-of-sample validation residual IC was
  negative and a minority of folds shared the reference sign.
- **The signal direction reversed versus the prior thin-data result**: Phase 2K-C (+0.067) and
  Phase 2L-B pooled validation (+0.041) were positive on the ~40-name single-regime 2023–2026
  export; on the ~10-year, ~129-name panel the IC is negative.
- **No single year or regime explains the failure**: the IC is negative across SPY up/down and
  high/low market-volatility splits and across leave-one-year-out, so the failure is pervasive,
  not a single-regime artifact.
- **Survivorship-caveat implications**: the expanded universe is a current-as-of (not
  point-in-time) membership approximation, so every result is reported as survivorship-biased. A
  clean point-in-time universe would only tighten, not rescue, this negative result.
- **Data-expansion lesson**: acquiring more, longer, broader data — rather than fitting a model
  to the thin sample — correctly overturned the earlier conclusion. Broader data changed the
  conclusion, which is exactly why the data was expanded before any model was built.

## Refreshed alpha backlog

The prior Phase 2K-A hypothesis backlog is preserved for reference, and the backlog is refreshed
toward diversified return drivers. Every backlog item carries `id`, `name`, `category`,
`rationale`, `required_data`, `leakage_risks`, `expected_horizon`, `priority`, `status`,
`next_test`, and `production_allowed = false`. The refreshed categories are:

- **price momentum alternatives** — residual price momentum (12-1), retested model-free on the
  expanded multi-regime panel;
- **short-term reversal / mean reversion** — short-horizon residual mean reversion;
- **volatility-adjusted momentum** — momentum scaled by trailing realized volatility to avoid the
  vol/beta tilt that failed earlier;
- **sector-relative momentum** — within-sector de-meaning (needs a low-cost point-in-time sector
  map);
- **earnings / fundamentals / estimate-revision candidates** — future-data category;
- **quality / profitability candidates** — future-data category;
- **liquidity** — **deprioritized to diagnostic-only**: usable as a size/liquidity neutralizer or
  control when paired with another mechanism, never again as a standalone alpha.
  `avg_dollar_volume_21d` is **not reintroduced as a standalone alpha candidate**.

## What the next model-free screens should test

The immediate next phase stays **model-free** and uses the expanded D: dataset only for
**price/volume-based features**: residual price momentum, short-horizon residual reversal, and
volatility-adjusted momentum, screened by per-date residual rank IC across the full sample and
embargoed walk-forward windows. Sector-relative tests follow once a low-cost point-in-time sector
map is attached. Standalone `avg_dollar_volume_21d` is explicitly excluded as an alpha candidate.

## Data requirements and leakage controls

The price/volume screens need only the expanded panel that already exists; a sector map is
low-cost; estimate / earnings / fundamentals families are deferred future-data backlog items and
**no paid data is purchased or built on yet**. Every screen keeps features trailing point-in-time
only, labels strictly forward (never forward-filled), residualizes per date and cross-sectionally,
embargoes by the forward horizon, uses block bootstrap at the holding horizon, and reports every
result as survivorship-biased until a point-in-time universe is acquired — so there is no
look-ahead leakage.

## Why no model candidate is created yet

This phase refreshes a research backlog; it builds no model. The Phase 2K-A model-candidate gate
stays locked: no model candidate may be created until a feature family passes the IC sweep **and**
the out-of-sample / walk-forward battery on real data spanning more than one market regime, with
no single-regime artifact, no excessive ticker concentration, and no look-ahead leakage. The
liquidity candidate just failed that bar on the expanded panel. Phase 2K-J trains nothing, fits
nothing, creates no model candidate, keeps the model-v2 serving flag disabled, and claims no
**production edge**.

## What Phase 2K-K should do

`recommended_next_phase` routes to **Phase 2K-K — Expanded Dataset Model-Free Alpha Screen**: run
a new model-free screen of the prioritized price/volume alpha candidates on the expanded
survivorship-caveated dataset, excluding standalone `avg_dollar_volume_21d`, and still with no
model training, no model candidate, and no production integration. Like every phase in this
track, Phase 2K-K **does not deploy**, **does not restart stock-api.service**, **does not enable**
the model-v2 flag, **does not run migrations**, **does not write to production DB**, and **does
not trade**, and it claims no **production edge**.

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

The `failed_candidate_decision` and `recommendation` blocks additionally record
`create_model_candidate_now = false`, `train_model_now = false`, `deploy_now = false`, and
`production_edge_claimed = false`, and the `interpretation` block confirms `model_trained = false`,
`model_candidate_created = false`, and `authorized_to_serve_model = false`.

## Conclusion

Phase 2K-J stops the failed liquidity candidate, distils the expanded-retest diagnostics, and
refreshes the alpha backlog toward diversified, model-free price/volume hypotheses now and
future-data factor families later — then routes to the Phase 2K-K model-free screen. It computes
no new alpha on the D: dataset, trains nothing, creates no model candidate, and claims no
**production edge**.
