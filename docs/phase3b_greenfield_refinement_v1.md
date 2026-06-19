# Phase 3-B — Greenfield Feature and Label Refinement (v1)

_Implemented by `research/analyze_phase3b_greenfield_refinement.py` and validated by
`tests/test_phase3b_greenfield_refinement.py`. Phase 3-B is a **diagnostic / refinement phase**:
it does not retrain any model. It reads the committed Phase 3-A artifacts, ranks every
walk-forward fold of the Phase 3-A best configuration, identifies the catastrophic folds and the
market regimes they correspond to, attributes the instability to specific feature families /
horizons / sector concentration / risk regime / redundancy / overlapping labels, and produces a
concrete refined configuration — with a kill switch — for a follow-up Phase 3-C rerun._

> Scope and safety. This phase trains **no model** (research or production), creates **no
> production model candidate**, and writes **no deployable model artifact** (no pickle / joblib /
> binary). It reads the committed Phase 3-A JSON and two summary CSVs, the current-as-of sector
> map, and — read-only — only the **SPY benchmark series** from the D: price panel to label each
> validation window's market regime; it writes only four small files under `research/output` and
> **nothing to the D: drive**. It is research tooling: it **does not deploy**, it **does not
> restart stock-api.service**, it **does not enable** the model-v2 serving flag,
> it **does not run migrations**, it **does not write to production DB**, and it
> **does not trade**. No order
> placement, no automation, no production model candidate, and no deployable artifact happen here,
> and it claims no **production edge**.

## Why Phase 3-B follows Phase 3-A

Phase 3-A abandoned the sub-floor Phase 2K single-signal rescue path and rebuilt from scratch: a
43-feature trailing panel, strictly-forward 5 / 21 / 63-day labels, three baseline models, and 15
chronological embargoed walk-forward folds. The best learned configuration — the numpy closed-form
**ridge at the 63-day horizon** — produced a positive out-of-sample mean rank IC of **~0.0506**
that **beat the model-free composite at every horizon** (the composite was near zero or negative),
with a fold win rate of ~0.64 and a positive-spread fraction of ~0.71. That is materially stronger
than the old Phase 2K path (~0.013 IC).

But Phase 3-A flagged it **`GREENFIELD_BASELINE_WEAK_BUT_IMPROVABLE`**, not promising, because one
walk-forward fold was **catastrophic** (worst fold rank IC **~ −0.1143**, beyond the −0.05
stability bound). Phase 3-B's job is to explain *why* and to turn that explanation into a concrete,
testable refinement — without retraining anything.

## Why Phase 3-A was better than the old 2K path but still unstable

The greenfield model learns a multi-feature cross-sectional ranking instead of re-measuring three
pre-registered residual signals, so it has more signal (higher IC, a real long-short spread that
beats the benchmark). The cost is that a *learned* coefficient vector can be **non-stationary**:
coefficients fit on one regime can invert in another. The 63d ridge concentrates that risk — it has
the highest IC but also the worst worst-fold and the highest sector concentration.

## Catastrophic fold diagnosis

Ranking the 14 valued folds of the best config (RIDGE @ 63d) by mean rank IC, **four** folds are
catastrophic (IC < −0.05), not one:

| Fold | Validation window | Mean rank IC | Top-sector share | Regime |
|------|-------------------|--------------|------------------|--------|
| **5** | 2021-10-05 → 2022-04-04 | **−0.1143** (worst) | 0.437 | 2022 selloff / rate-hike onset, growth→value & low-vol unwind |
| 11 | 2024-10-08 → 2025-04-09 | −0.0828 | 0.453 | late-2024 → early-2025 rotation |
| 10 | 2024-04-09 → 2024-10-07 | −0.0613 | 0.462 | 2024 mid-year rotation / Aug-2024 vol spike |
| 6 | 2022-04-05 → 2022-10-04 | −0.0502 | 0.379 | deep 2022 bear market |

The decisive tell is the **cross-model comparison at the worst fold**: at fold 5 / 63d the *fixed*
**model-free composite was strongly positive (+0.124)** while the *learned* ridge was **−0.114**
(and the logistic −0.125). A fixed long-momentum / low-vol / reversal blend stayed right while the
learned coefficients went the opposite way and lost. That is a **non-stationarity / regime**
signature, not a coding leak. The catastrophic folds also run **more concentrated** (top-quintile
single-sector share ~0.45) than the positive folds, which amplifies the drawdown when the bet is
wrong. The analyzer computes each window's SPY return / volatility / drawdown to label the regime
explicitly (this is the only D: read, SPY-only, read-only).

## Feature-family diagnosis

Grouping the 43 features into their seven families and combining the Phase 3-A full-sample IC
screen with the 63d ridge coefficient loadings:

- **Strongest positive families:** `volatility_risk` (low-vol effect; the single highest |IC|
  features — realized-vol and downside-vol), `market_relative` (relative strength / beta), and
  `reversal`.
- **Unstable / regime-sensitive families:** `volatility_risk` and `price_momentum` /
  `market_relative`. The 63d ridge is a **long relative-strength + low-absolute-volatility** book
  that also **shorts long-term winners** (negative on `return_252d`) and tilts **anti-mega-cap**
  (negative on `market_rank_avg_dollar_volume_21d`). Low-vol + momentum is exactly the combination
  that crashes at sharp regime turns — which is what folds 5/6/10/11 are.
- **Redundant families:** `cross_sectional_ranks` are **monotone rank transforms** of their base
  features (identical |IC|) and add collinearity, not information; and within `price_momentum` the
  raw `return_Nd` columns are the **exact negatives** of the `reversal_Nd` features (identical
  |IC|). The analyzer confirms these equalities programmatically. The 63d ridge also carries
  **conflicting volatility signs** (`realized_vol_63d` negative vs `realized_vol_126d` positive) —
  a collinearity artifact.
- **Weak / low-information family:** `volume_liquidity` (near-noise on this panel).
- **Leakage-risk families:** none — every feature is strictly trailing / point-in-time. The only
  caveat is the current-as-of sector map (a bias, not a leak).

Actions: **keep** `reversal`, `sector_relative`, and (with neutralisation) `volatility_risk` /
`market_relative`; **prune** `cross_sectional_ranks` entirely and the sign-mirrored `return_Nd`
duplicates; **collapse** `volume_liquidity` to a single liquidity control; **transform** by
de-duplicating and orthogonalising the low-vol / momentum blocks.

## Horizon diagnosis

| Horizon | Ridge mean IC | Ridge worst fold | Ridge positive-spread frac | Overlapping-label risk |
|---------|---------------|------------------|----------------------------|------------------------|
| 5d | 0.0198 | −0.0247 (not catastrophic) | 0.667 | low |
| 21d | 0.0265 | **−0.0419 (not catastrophic)** | **0.800** | moderate |
| 63d | **0.0506** | **−0.1143 (catastrophic)** | 0.714 | high |

63d genuinely has the highest IC and widest spread, but also the worst worst-fold and the highest
concentration, and its **overlapping forward windows** (63-day labels overlap heavily across
consecutive dates) inflate both apparent IC and fold variance. 21d is materially more stable: **no
catastrophic ridge fold** and the highest positive-spread fraction. 5d is lowest-signal.
Conclusion: Phase 3-C should treat **21d as primary**, **63d as secondary with an
overlapping-label correction**, and keep **5d as diagnostic-only**. Crucially, because 5d and 21d
are *not* catastrophic, the instability is **not structural** — a stable configuration exists.

## Sector / risk diagnosis

The best 63d book is a long relative-strength / momentum, **low-absolute-volatility**,
**anti-mega-cap** (smaller-name) tilt that shorts long-term winners and low market correlation. It
carries **high single-sector concentration** (~0.38 on average; higher in catastrophic folds). The
catastrophic folds are **risk-regime related** — they cluster where that tilt inverts while the
fixed composite stays positive. Sector-relative features (demeaned within sector) **lower**
concentration by construction and should be carried forward. Phase 3-C should add explicit
**sector neutralisation** and **risk / beta / volatility neutralisation** of the long-short book.

## Refined Phase 3-C configuration (`research/output/phase3b_refined_config.json`)

- **selected_models:** ridge (primary), logistic (secondary stability benchmark only), model-free
  composite (must-beat benchmark — it was robust in the catastrophic regime).
- **selected_horizons:** 21d primary, 63d secondary (with overlapping-label correction), 5d
  diagnostic-only.
- **selected_feature_families:** price_momentum, reversal, volatility_risk, market_relative,
  sector_relative.
- **pruned_feature_families:** cross_sectional_ranks (monotone duplicates); plus the sign-mirrored
  raw `return_Nd` columns and all but one liquidity control.
- **required_feature_transforms:** winsorize/clip extremes; de-duplicate sign-mirrored features;
  orthogonalise the low-vol vs momentum blocks; sector-neutralise predictions before ranking.
- **risk_controls:** cap the net low-vol / low-beta tilt; beta- and volatility-neutralise the
  long-short book; cap per-name / per-fold gross exposure.
- **sector_controls:** cap top-quintile single-sector share at ≤ 0.35; sector-neutralise the long
  book; carry sector-relative features.
- **validation_changes:** keep embargo ≥ max horizon; add per-regime IC reporting; advance only if
  the primary horizon has **no** catastrophic fold and fold win rate ≥ 0.60.

## Kill-switch rules

If the Phase 3-C refined rerun **still** produces any catastrophic fold (rank IC < −0.05) at the
chosen primary horizon, OR fold win rate < 0.60, OR an unstable mean IC, then **stop
price/volume-only modeling** and escalate to the external-data decision (fundamentals, estimates,
earnings, news, or options). Do **not** keep tuning price/volume features past that point. No
internet data, fundamentals, news, or options are added in Phase 3-C itself — only after a
kill-switch trip.

## Why no production model candidate is created

Phase 3-B trains nothing and decides nothing about production. The model is still only *weak but
improvable*, the universe is survivorship-biased, the sector map is current-as-of (not
point-in-time), and the diagnosis has just confirmed the 63d configuration is unstable across
regimes. Phase 3-B therefore sets `research_model_trained = false`,
`production_model_trained = false`, `production_model_candidate_created = false`, and
`deployable_model_artifact_written = false`, keeps the model-v2 serving flag disabled, and claims
no **production edge**.

## What Phase 3-C should do

Run the refined configuration above through a stricter walk-forward test with the catastrophic-fold
fixes applied (21d primary, sector + risk neutralisation, pruned redundancy, overlapping-label
correction at 63d), and apply the kill switch. Like every phase in this track, Phase 3-C **does not
deploy**, **does not restart stock-api.service**, **does not enable** the model-v2 flag, **does not
run migrations**, **does not write to production DB**, and **does not trade**, and it claims no
**production edge**.

## Safety flags (from the results JSON)

```
database_touched                    = false
database_write_executed             = false
migration_executed                  = false
deployment_executed                 = false
model_v2_enabled                    = false
production_edge_claimed             = false
no_trading                          = true
no_orders                           = true
no_automation                       = true
research_model_trained              = false
production_model_trained            = false
production_model_candidate_created  = false
deployable_model_artifact_written   = false
d_drive_read                        = true   (SPY benchmark series only, read-only)
d_drive_written                     = false
network_used                        = false
```

## Conclusion

Phase 3-B diagnosed the Phase 3-A instability without retraining: four catastrophic 63d folds
clustering at regime turning points, driven by non-stationary low-vol + momentum coefficients,
high sector concentration, structural feature redundancy, and 63d overlapping-label
autocorrelation — with a demonstrably more stable 21d configuration available. It emits a concrete,
kill-switched refined configuration for a Phase 3-C rerun, creates no production model candidate,
writes no deployable artifact, reads only the SPY series from the D: panel read-only and writes
nothing to D:, fetches nothing from the network, and all results remain survivorship-biased /
current-as-of and are **not a production edge**.
