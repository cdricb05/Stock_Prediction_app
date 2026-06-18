# Phase 2K-O — Decision Gate for Reconfirmed But Sub-Floor Leads (v1)

_Implemented by `research/analyze_phase2k_o_reconfirmed_lead_decision_gate.py` and validated
by `tests/test_phase2k_o_reconfirmed_lead_decision_gate.py`. Phase 2K-O is a **decision
gate**, not another empirical screen and not model training. It reads the small Git-tracked
Phase 2K-N / 2K-M / 2K-K / 2K-J / 2K-H result summaries, diagnoses why the 3 reconfirmed
narrow-retest leads are still not confirmable, weighs the strategic paths, and selects the
next step._

> Scope: this phase reads only the small Git-tracked Phase 2K-N / 2K-M / 2K-K / 2K-J / 2K-H
> JSON summaries and writes one small results JSON in the C: repo. It **reads no large D:
> price-history CSV**, reruns no retest, runs no broad alpha screen, and adds no candidate. It
> is research tooling only: it **does not deploy**, it **does not restart stock-api.service**,
> it **does not enable** the model-v2 serving flag, it **does not run migrations**, it **does
> not write to production DB**, and it **does not trade**. No order placement, no automation,
> no model training, no model candidate, and no write to the D: drive happens here, and it
> claims no **production edge**.

## Why Phase 2K-O follows Phase 2K-N

Phase 2K-N ran a narrow, pre-registered, model-free retest of the 3 Phase 2K-M
`NARROW_RETEST_CANDIDATE` pairs on the expanded, survivorship-caveated D: panel. Every one was
reconfirmed as a research lead — a positive, above-zero (by moving-block bootstrap),
year-stable, regime-stable, non-concentrated residual rank IC — but **all 3 remained below the
0.03 confirmation floor**. Phase 2K-N therefore returned
`RESEARCH_LEADS_RECONFIRMED_BUT_NOT_CONFIRMABLE` and routed explicitly here. Before deciding
anything, Phase 2K-O confirms that routing (`phase == "2K-N"`, `retest_executed == true`,
overall recommendation `RESEARCH_LEADS_RECONFIRMED_BUT_NOT_CONFIRMABLE`,
`recommended_next_phase.phase == "2K-O"` with the matching title, the recommendation's
`create_model_candidate_now` / `train_model_now` both `false`, and exactly 3
`RESEARCH_LEAD_RECONFIRMED` leads with 0 `KEEP_FOR_CONFIRMATION_DESIGN`).

Phase 2K-N was a meaningful empirical retest that re-opened the D: panel. Phase 2K-O is a
**fast, decision-focused gate** that consumes only the Phase 2K-N output (and the upstream
chain for context). It re-opens no data and recomputes no IC.

## The 3 reconfirmed but not-confirmable leads

| Lead | Mean residual IC | Bootstrap CI (low, high) | Same-sign frac | Year-out stable | Prior 2K-L score |
|------|------------------|--------------------------|----------------|-----------------|------------------|
| `residual_price_momentum_12_1@5d` | 0.0132 | (0.0002, 0.0269) | 0.542 | yes | 68.87 |
| `short_horizon_residual_reversal_5d@21d` | 0.0103 | (0.0018, 0.0212) | 0.540 | yes | 65.04 |
| `short_horizon_residual_reversal_21d@21d` | 0.0151 | (0.0041, 0.0279) | 0.563 | yes | 75.19 |

Each lead's single failed keep gate is `mean_residual_ic_at_or_above_floor_or_close`: the edge
reproduces, but the information coefficient is structurally weak.

## Why the leads are blocked

The gate decomposes every lead's blockers and aggregates them:

- **Primary blocker: sub-floor residual IC.** All 3 leads are positive and above zero but
  below the 0.03 floor (and below the 0.025 near-floor-with-support bar). This is the only
  failed keep gate.
- **Not blocked by robustness.** None of the leads flip sign across the SPY up/down or
  market-vol regimes (`regime_is_dominant_blocker == false`), none are over-concentrated in the
  long leg (top-3 share ≈ 0.04–0.07, far under the 0.5 ceiling;
  `concentration_is_dominant_blocker == false`), and every IC sign is leave-one-year-out stable
  (`stability_is_dominant_blocker == false`). Each lead has ample data
  (`data_sufficiency_is_dominant_blocker == false`).
- **Survivorship is a standing caveat, not the dominant blocker.** The panel is
  current-as-of / survivorship-biased and claims no point-in-time membership, but because every
  lead reproduces a positive, above-zero, year-stable edge, the binding constraint is the weak
  IC, not the universe construction (`survivorship_is_dominant_blocker == false`). A clean
  point-in-time universe would tighten, never rescue, a sub-floor edge.

Each lead is therefore classified `structurally_weak_but_repeatable_sector_relative_candidate`:
a reproducible price / reversal signal whose magnitude is the problem and which admits a
standard sector / industry neutralization.

## The strategic options considered

The gate weighs four paths, each with pros, cons, requirements, risk, cost, and a next action:

1. **`SECTOR_RELATIVE_FEATURE_FEASIBILITY`** — check whether sector / industry-neutral or
   sector-relative versions of the reconfirmed price / reversal leads can clear the floor.
   Model-free, offline, low cost; the main caveat is that no point-in-time sector map exists,
   so any current sector map may be used only as explicitly caveated research metadata.
2. **`POINT_IN_TIME_UNIVERSE_DECISION`** — decide whether to obtain paid point-in-time
   membership / sector / fundamentals data first. Highest cost and burden; appropriate only
   when the survivorship caveat is the dominant blocker.
3. **`STOP_AND_REFRESH_BACKLOG`** — stop the standalone leads and redirect to stronger,
   orthogonal factor families. Appropriate only when the leads are too weak or unrepeatable and
   no plausible transformation is justified.
4. **`CONTINUE_CONFIRMATION_ANYWAY`** — **explicitly rejected.** No lead met
   `KEEP_FOR_CONFIRMATION_DESIGN` in Phase 2K-N (0 KEEP, 3 RECONFIRMED), so confirmation design
   is not authorized and no model candidate may be created.

## Why confirmation and model work are blocked

Phase 2K-N produced zero `KEEP_FOR_CONFIRMATION_DESIGN` leads. The model-candidate gate stays
locked: no candidate may be created, and no walk-forward confirmation battery may be designed,
until a feature first clears the IC floor in a model-free retest. A reconfirmed but sub-floor
lead is a **research lead**, not a confirmable edge and not a **production edge**. This gate
therefore trains no model, fits nothing, creates no model candidate, keeps the model-v2
serving flag disabled, and authorizes no confirmation work.

## Selected path

**Recommendation: `PROCEED_TO_SECTOR_RELATIVE_FEASIBILITY`.** All 3 reconfirmed leads are
reproducible and above zero, their single dominant blocker is sub-floor IC, regime /
concentration / stability are not dominant blockers, and a sector-relative variant is plausible
and cheaper than committing to paid point-in-time data — exactly the conservative condition for
this path. The gate routes to **Phase 2K-P — _Sector-Relative Feature Feasibility for
Reconfirmed Leads_**, which assesses (still model-free, still no model-candidate design)
whether sector / industry metadata can support sector-relative versions of these leads.

## Rejected paths

- **`POINT_IN_TIME_UNIVERSE_DECISION`** — rejected: survivorship is a standing caveat, not the
  dominant blocker; a cheaper sector-relative feasibility check should be tried before paying
  for point-in-time data. Revisit only if the sector-relative variant is infeasible or also
  sub-floor.
- **`STOP_AND_REFRESH_BACKLOG`** — rejected: stopping is premature while a reproducible,
  above-zero, year-stable edge still has an untried, cheap transformation (sector / industry
  neutralization).
- **`CONTINUE_CONFIRMATION_ANYWAY`** — rejected by rule: no lead met
  `KEEP_FOR_CONFIRMATION_DESIGN`.

## What Phase 2K-P should do

Phase 2K-P should assess sector-relative feature feasibility for the 3 reconfirmed leads:
whether a sector / industry mapping (point-in-time if available; otherwise the current map used
only as caveated research metadata) can support sector-neutral or sector-relative variants that
lift the residual IC over the floor. It must stay model-free, add no candidate, train no model,
and design no model candidate. Like every phase in this track, Phase 2K-P **does not deploy**,
**does not restart stock-api.service**, **does not enable** the model-v2 flag, **does not run
migrations**, **does not write to production DB**, and **does not trade**, and it claims no
**production edge**.

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
d_drive_read            = false
d_drive_written         = false
broad_alpha_screen_run  = false
```

The `recommendation` and `interpretation` blocks additionally record
`create_model_candidate_now = false`, `train_model_now = false`, `deploy_now = false`,
`authorized_to_serve_model = false`, `ran_broad_alpha_screen = false`,
`candidate_set_expanded = false`, `reran_narrow_retest = false`, and
`read_large_d_drive_csv = false`.

## Conclusion

Phase 2K-O is a fast decision gate over the Phase 2K-N narrow-retest evidence. All 3 leads were
reconfirmed as research leads but blocked by sub-floor residual IC, not by regime,
concentration, instability, or the survivorship caveat. The gate selects a model-free
sector-relative feasibility assessment (Phase 2K-P) ahead of paid point-in-time data or
stopping, and authorizes no confirmation, no model candidate, and no model training. It reads
no large D: CSV, runs no broad alpha screen, writes nothing to the D: drive, and claims no
**production edge**.
