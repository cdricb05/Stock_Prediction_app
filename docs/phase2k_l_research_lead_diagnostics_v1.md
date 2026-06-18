# Phase 2K-L — Research-Lead Diagnostics After the Expanded Alpha Screen (v1)

_Implemented by `research/analyze_phase2k_l_research_lead_diagnostics.py` and validated by
`tests/test_phase2k_l_research_lead_diagnostics.py`. Phase 2K-L diagnoses the 15
`RESEARCH_LEAD_ONLY` candidate / horizon pairs that Phase 2K-K produced, scores and ranks
them, identifies the recurring failed gates, classifies each lead, and recommends the next
research phase. It runs **no new D: screen**, reads **no large D: CSV**, and **trains no
model**._

> Scope: this phase reads only the small Git-tracked Phase 2K-K / 2K-J / 2K-I / 2K-H JSON
> summaries and writes one small results JSON in the C: repo. It is research tooling only:
> it **does not deploy**, it **does not restart stock-api.service**, it **does not enable**
> the model-v2 serving flag, it **does not run migrations**, it **does not write to
> production DB**, and it **does not trade**. No order placement, no automation, no model
> training, no model candidate, no new D: alpha screen, and no write to the D: drive happens
> here, and it claims no **production edge**.

## Why Phase 2K-L follows Phase 2K-K

Phase 2K-K ran a model-free screen of the prioritized price/volume alpha candidates on the
expanded, survivorship-caveated D: panel. It produced **15 `RESEARCH_LEAD_ONLY`** candidate
/ horizon pairs and **0 `KEEP_FOR_WALK_FORWARD_CONFIRMATION`** pairs, returning
`MODEL_FREE_SCREEN_HAS_RESEARCH_LEADS_ONLY` and routing explicitly to Phase 2K-L. Before
diagnosing anything, Phase 2K-L confirms that routing
(`recommendation == MODEL_FREE_SCREEN_HAS_RESEARCH_LEADS_ONLY`, `RESEARCH_LEAD_ONLY` count
== 15, `KEEP_FOR_WALK_FORWARD_CONFIRMATION` count == 0,
`recommended_next_phase.phase == "2K-L"` with the matching title, and the Phase 2K-K
recommendation's `create_model_candidate_now` / `train_model_now` both `false`).

Phase 2K-K found **no candidate / horizon pair strong enough for walk-forward
confirmation**, so no candidate is promoted to model-candidate design, no model is trained,
and no model candidate is created. Phase 2K-L is a fast, diagnostic / planning step that
reads only the small Phase 2K-K JSON artifact — it does not re-open the D: dataset.

## The stopped liquidity candidate remains excluded

`avg_dollar_volume_21d` was stopped in Phase 2K-J after the Phase 2K-I expanded retest
failure. Phase 2K-L records its policy (`status == STOPPED_AFTER_EXPANDED_RETEST_FAIL`,
`excluded_as_standalone_alpha == true`, `allowed_as_control_or_diagnostic_only == true`) and
verifies it never appears among the diagnosed research leads. It may remain only a control /
diagnostic / neutralizer, never a standalone alpha.

## Diagnostic method

Phase 2K-L extracts every `RESEARCH_LEAD_ONLY` pair from the Phase 2K-K
`candidate_recommendations` and enriches it with the concentration and regime-flip fields
from `screen_metrics.by_candidate`. It then computes a transparent, deterministic
**diagnostic score** on a 0–100 scale from the Phase 2K-K metrics **only** — nothing is
re-estimated on the D: panel. The five components are:

- **IC vs floor** (max 35): mean residual rank IC relative to the Phase 2K-K keep floor
  (0.03);
- **Same-sign fraction** (max 20): how far the same-sign-as-reference date fraction clears
  the 0.5 coin-flip line (full credit at 0.60);
- **Bootstrap CI excludes zero** (max 20): the moving-block 95% CI lower bound clears zero;
- **Leave-one-year-out stability** (max 15): the IC sign survives dropping any single year;
- **Few failed gates** (max 10): how few keep gates failed (full credit at one failure, zero
  at four).

Each lead is then classified conservatively:

- **`TARGETED_DIAGNOSTIC_CANDIDATE`** — enough data, positive mean IC, a bootstrap CI lower
  bound above zero, year-stable sign, at most two failed keep gates, and no severe
  concentration. It still does **not** become a model candidate; it only earns a focused,
  model-free follow-up diagnostic.
- **`DEPRIORITIZE_AFTER_DIAGNOSTIC`** — year-unstable sign and/or nearly every robustness
  gate failed.
- **`WEAK_RESEARCH_LEAD`** — directionally positive but several robustness / stability gates
  failed (typically a sub-floor IC and/or a bootstrap CI straddling zero); possibly useful
  for future feature engineering, not ready for confirmation.

## Failed-gate themes

Across the 15 leads the recurring failures are:

- **Sub-floor information coefficient (15/15):** every lead has a positive but sub-0.03 mean
  residual rank IC — the dominant blocker. The signals are directionally real but
  economically tiny.
- **Wide bootstrap confidence interval (10/15):** the 95% moving-block CI straddles zero,
  concentrated at the 21d / 63d horizons where effective observations are fewest.
- **Regime sign instability (10/15):** the IC sign flips across a SPY up/down or market-vol
  regime split, concentrated in volatility-adjusted momentum and the 5d reversal.
- **Single-year fragility (3/15):** the IC sign fails leave-one-year-out; these leads also
  fail the other robustness gates and form the deprioritized set.
- **Survivorship caveat (15/15):** all results are on a current-as-of, survivorship-caveated
  universe; a clean point-in-time universe would only tighten, never rescue, these results.

## Family and horizon diagnostics

- **Residual price momentum** (3 leads): the highest mean diagnostic score family; 1
  targeted, 2 weak. Its 63d horizon has the single highest IC but a CI that straddles zero.
- **Short-horizon reversal** (6 leads): the most targeted candidates (4); the best overall
  lead is the 21d-reversal at the 21d horizon. 1 weak, 1 deprioritized.
- **Volatility-adjusted momentum** (6 leads): the weakest family; 0 targeted, 4 weak, 2
  deprioritized, with the most regime instability.

By horizon, the 63d leads carry the largest point IC estimates but the widest CIs (no CI
clears zero), while the 5d and 21d horizons supply the diagnosable targeted candidates
because their CIs are tighter.

## Lead classifications (this run)

The committed artifact records **5 `TARGETED_DIAGNOSTIC_CANDIDATE`**, **7
`WEAK_RESEARCH_LEAD`**, and **3 `DEPRIORITIZE_AFTER_DIAGNOSTIC`** leads, with the best lead
the 21d-reversal at the 21d horizon and the worst the 63d-volatility-adjusted momentum at
the 5d horizon.

## Why no model candidate is created yet

Phase 2K-L is a diagnostic; it builds no model and re-runs no screen. A
`TARGETED_DIAGNOSTIC_CANDIDATE` only earns a later, **model-free** focused diagnostic phase
— never model training or model-candidate design. The model-candidate gate stays locked: no
candidate may be created until a feature family passes the IC screen **and** a stricter,
separately gated walk-forward battery on real data spanning more than one market regime,
which none of these leads has done. Phase 2K-L trains nothing, fits nothing, creates no
model candidate, keeps the model-v2 serving flag disabled, and claims no **production edge**.

## What Phase 2K-M should do

`recommended_next_phase` routes to **Phase 2K-M**, with the title and purpose chosen by the
diagnostic outcome:

- if any lead is `TARGETED_DIAGNOSTIC_CANDIDATE` → *Targeted Diagnostics for Best Expanded
  Alpha Leads* (focused, model-free diagnostics — failed-gate inspection, stricter
  concentration filters, regime-split IC — on the best leads, still no model training);
- else if all leads are weak / deprioritized → *Alpha Backlog Refresh v2 After Weak Expanded
  Leads*;
- else if the main blocker is the survivorship / current-membership caveat → *Point-in-Time
  Universe Decision Before More Alpha Screens*.

Like every phase in this track, Phase 2K-M **does not deploy**, **does not restart
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
ran_new_d_screen        = false
read_large_d_csv        = false
```

The `recommendation` and `interpretation` blocks additionally record
`create_model_candidate_now = false`, `train_model_now = false`, `deploy_now = false`,
`model_trained = false`, `model_candidate_created = false`, `authorized_to_serve_model =
false`, `ran_new_d_screen = false`, and `read_large_d_csv = false`.

## Conclusion

Phase 2K-L diagnoses the Phase 2K-K research leads from the small screen artifact alone,
scores and ranks them, surfaces the sub-floor-IC / wide-CI / regime-instability themes,
classifies each lead conservatively, keeps the stopped liquidity candidate excluded, and
routes to the appropriate Phase 2K-M. It runs no new D: screen, reads no large D: CSV,
trains nothing, creates no model candidate, and claims no **production edge**.
