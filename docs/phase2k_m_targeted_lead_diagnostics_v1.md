# Phase 2K-M — Targeted Diagnostics for the Best Expanded Alpha Leads (v1)

_Implemented by `research/analyze_phase2k_m_targeted_lead_diagnostics.py` and validated by
`tests/test_phase2k_m_targeted_lead_diagnostics.py`. Phase 2K-M takes only the 5
`TARGETED_DIAGNOSTIC_CANDIDATE` leads that Phase 2K-L identified, profiles each one,
identifies the exact failed gates, compares the targeted set by family and horizon, decides
whether a narrow model-free retest is justified per lead, and recommends the next research
phase. It runs **no new D: screen**, reads **no large D: CSV**, and **trains no model**._

> Scope: this phase reads only the small Git-tracked Phase 2K-L / 2K-K / 2K-J / 2K-I / 2K-H
> JSON summaries and writes one small results JSON in the C: repo. It is research tooling
> only: it **does not deploy**, it **does not restart stock-api.service**, it **does not
> enable** the model-v2 serving flag, it **does not run migrations**, it **does not write to
> production DB**, and it **does not trade**. No order placement, no automation, no model
> training, no model candidate, no new D: alpha screen, and no write to the D: drive happens
> here, and it claims no **production edge**.

## Why Phase 2K-M follows Phase 2K-L

Phase 2K-L diagnosed the 15 `RESEARCH_LEAD_ONLY` candidate / horizon pairs from the Phase
2K-K expanded model-free screen, scored and ranked them, and classified **5** as
`TARGETED_DIAGNOSTIC_CANDIDATE`, **7** as `WEAK_RESEARCH_LEAD`, and **3** as
`DEPRIORITIZE_AFTER_DIAGNOSTIC`. It returned `TARGETED_DIAGNOSTICS_FOR_BEST_RESEARCH_LEADS`
and routed explicitly to Phase 2K-M. Before diagnosing anything, Phase 2K-M confirms that
routing (`phase == "2K-L"`, `recommendation == TARGETED_DIAGNOSTICS_FOR_BEST_RESEARCH_LEADS`,
`recommended_next_phase.phase == "2K-M"` with the matching title,
`lead_classification_counts.TARGETED_DIAGNOSTIC_CANDIDATE == 5`, and the Phase 2K-L
recommendation's `create_model_candidate_now` / `train_model_now` both `false`).

Phase 2K-M is a fast, focused diagnostic / planning step that reads only the small Phase
2K-L JSON artifact (with the earlier summaries for provenance) — it does not re-open the D:
dataset. The 5 targeted leads are **targeted diagnostic candidates only**: they are **not
confirmation candidates** and **not model candidates**, and Phase 2K-M does not change that.

## The 5 targeted diagnostic candidates

The targeted leads carried forward from Phase 2K-L are:

| Lead | Family | Horizon | Mean residual IC | CI low | Diagnostic score |
|------|--------|---------|------------------|--------|------------------|
| `short_horizon_residual_reversal_21d@21d` | short-horizon reversal | 21d | 0.0151 | 0.0041 | 75.19 |
| `residual_price_momentum_12_1@5d` | residual price momentum | 5d | 0.0132 | 0.0002 | 68.87 |
| `short_horizon_residual_reversal_5d@21d` | short-horizon reversal | 21d | 0.0103 | 0.0018 | 65.04 |
| `short_horizon_residual_reversal_5d@5d` | short-horizon reversal | 5d | 0.0110 | 0.0022 | 60.48 |
| `short_horizon_residual_reversal_21d@5d` | short-horizon reversal | 5d | 0.0086 | 0.0010 | 56.84 |

Each lead traces back to a `RESEARCH_LEAD_ONLY` Phase 2K-K candidate / horizon pair (Phase
2K-M cross-checks this against the Phase 2K-K `candidate_recommendations`). Four of the five
are in the short-horizon reversal family; the set clusters around reversal rather than
momentum.

## The stopped liquidity candidate remains excluded

`avg_dollar_volume_21d` was stopped in Phase 2K-J after the Phase 2K-I expanded retest
failure. Phase 2K-M records its policy (`status == STOPPED_AFTER_EXPANDED_RETEST_FAIL`,
`excluded_as_standalone_alpha == true`, `allowed_as_control_or_diagnostic_only == true`) and
verifies it never appears among the targeted leads. It may remain only a control /
diagnostic / neutralizer, never a standalone alpha.

## Targeted-lead diagnostics

For every targeted lead Phase 2K-M records a transparent profile from the Phase 2K-K / 2K-L
metrics only — nothing is re-estimated on the D: panel:

- `lead_id`, `candidate`, `feature`, `family`, `horizon_days`;
- `mean_residual_rank_ic`, `frac_dates_same_sign_as_reference`, `bootstrap_ci_low` /
  `bootstrap_ci_high`, `leave_one_year_out_sign_stable`;
- `failed_keep_gates` / `n_failed_keep_gates`, `top3_long_leg_share`, the SPY / volatility
  regime-flip flags, and derived `regime_dependent` / `severe_concentration` booleans;
- the Phase 2K-L `diagnostic_score`;
- `why_targeted`, `why_not_confirmation_ready`, and a per-lead `suggested_follow_up_test`.

The leads are ranked by diagnostic score (best:
`short_horizon_residual_reversal_21d@21d`) and summarized by candidate family and by
horizon.

## Blocker analysis

Across the targeted set the blocking gates are:

- **Sub-floor information coefficient (5/5):** every targeted lead has a positive but
  sub-0.03 mean residual rank IC — the dominant and, for three of the five leads, the
  *only* obstruction. The edge is directionally real but economically tiny.
- **Regime sign dependence (2/5):** two short-horizon reversal leads at the 5d horizon also
  fail `no_regime_only_dependence` (their IC sign flips across a SPY up/down split).
- **No concentration problem (0/5):** every targeted lead has a top-3 long-leg share far
  below the 0.50 severe-concentration ceiling.
- **No single-year fragility and no straddling CI (0/5):** all targeted leads are
  year-stable and have a bootstrap CI lower bound above zero by construction (that is part
  of why Phase 2K-L targeted them).
- **Survivorship caveat (carried forward):** all results are on a current-as-of,
  survivorship-caveated universe; a clean point-in-time universe would only tighten, never
  rescue, these results.

So the targeted set is blocked principally by a sub-floor IC, with a secondary regime
dependence on the 5d reversal leads, and no concentration or single-year issues.

## Narrow-retest criteria and per-lead follow-up

Each targeted lead receives one conservative follow-up type:

- **`NARROW_RETEST_CANDIDATE`** — positive mean IC, a bootstrap CI lower bound above zero, a
  year-stable sign, at most the IC-floor keep gate failed, no severe concentration, and no
  regime dependence. (`residual_price_momentum_12_1@5d`,
  `short_horizon_residual_reversal_5d@21d`, `short_horizon_residual_reversal_21d@21d`.)
- **`HOLD_FOR_SECTOR_RELATIVE_VARIANT`** — otherwise solid but regime-dependent, so it
  likely needs sector-relative construction or a point-in-time sector map before any retest.
  (`short_horizon_residual_reversal_5d@5d`, `short_horizon_residual_reversal_21d@5d`.)
- **`DIAGNOSTIC_ONLY`** — promising but too small / too weak for a dedicated retest (none of
  the current targeted leads fall here).

Because three leads clear the narrow-retest bar, the overall recommendation is
`RUN_NARROW_MODEL_FREE_RETEST`. The narrow-retest plan stays model-free: pre-register the
exact leads, horizons, residualization, ranking, and CI method; re-measure each candidate's
residual rank IC and moving-block bootstrap CI at its own horizon only; hold the
regime-dependent leads for a sector-relative variant; keep `avg_dollar_volume_21d` excluded;
and stop / refresh the backlog rather than escalate if the edge does not reproduce.

## Why no model candidate is created yet

Phase 2K-M is a diagnostic; it builds no model and re-runs no screen. A targeted lead, even
a `NARROW_RETEST_CANDIDATE`, only earns a later, **model-free** narrow retest — never model
training or model-candidate design. The model-candidate gate stays locked: no candidate may
be created until a feature family clears the IC screen **and** a stricter, separately gated
walk-forward battery on real data spanning more than one market regime, which none of these
leads has done (every targeted lead is still below the IC floor). Phase 2K-M trains nothing,
fits nothing, creates no model candidate, keeps the model-v2 serving flag disabled, and
claims no **production edge**.

## What Phase 2K-N should do

`recommended_next_phase` routes to **Phase 2K-N**, with the title and purpose chosen by the
diagnostic outcome:

- if any lead is `NARROW_RETEST_CANDIDATE` → *Narrow Model-Free Retest for Best Targeted
  Alpha Leads* (run a narrow, pre-registered, model-free retest only on the best targeted
  leads, still no model training and no model-candidate design);
- else if no lead clears the bar but some are regime-dependent →
  *Sector-Relative Feature Feasibility for Targeted Leads*;
- else → *Alpha Backlog Refresh v3 After Targeted Diagnostics*.

For this run the outcome is the narrow model-free retest. Like every phase in this track,
Phase 2K-N **does not deploy**, **does not restart stock-api.service**, **does not enable**
the model-v2 flag, **does not run migrations**, **does not write to production DB**, and
**does not trade**, and it claims no **production edge**.

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
false`, `ran_new_d_screen = false`, and `read_large_d_csv = false`, and the
`narrow_retest_plan` records `no_model_training = true`, `no_model_candidate = true`,
`no_new_d_screen = true`, and `no_large_d_csv_read = true`.

## Conclusion

Phase 2K-M diagnoses the 5 Phase 2K-L targeted research leads from the small diagnostic
artifact alone, profiles and ranks them, confirms each traces back to a `RESEARCH_LEAD_ONLY`
Phase 2K-K pair, finds the dominant blocker to be a sub-floor IC (with regime dependence on
the 5d reversal leads and no concentration problem), assigns each lead a conservative
follow-up type, keeps the stopped liquidity candidate excluded, and routes to the
appropriate Phase 2K-N. It runs no new D: screen, reads no large D: CSV, trains nothing,
creates no model candidate, and claims no **production edge**.
