# Phase 3-C — Refined Greenfield Walk-Forward Rerun (v1)

_Implemented by `research/train_phase3c_refined_greenfield_rerun.py` and validated by
`tests/test_phase3c_refined_greenfield_rerun.py`. Phase 3-C is a **research-training phase**: it
reruns the Phase 3-B refined configuration through a stricter walk-forward test, applies a hard
kill switch to the primary 21-day horizon, and answers one decisive question — after the Phase
3-B refinements, does the greenfield price/volume/sector model become stable enough to continue,
or should price/volume-only modeling stop and move to richer external data?_

> Scope and safety. This phase trains numpy-only **research** models from scratch. It creates
> **no production model candidate** and writes **no deployable model artifact** (no pickle /
> joblib / binary). It reads the committed Phase 3-B result + refined config, the Phase 3-A
> baseline, the current-as-of sector map, and — read-only — the expanded D: price / volume
> panel; it writes only four small files under `research/output` and **nothing to the D: drive**,
> and never copies the D: file into the repo. It is research tooling: it **does not deploy**, it
> **does not restart stock-api.service**, it **does not enable** the model-v2 serving flag, it
> **does not run migrations**, it **does not write to production DB**, and it **does not trade**.
> No order placement, no automation, no production model candidate, and no deployable artifact
> happen here, and it claims no **production edge**.

## Why Phase 3-C follows Phase 3-B

Phase 3-A built a from-scratch greenfield baseline and flagged it
`GREENFIELD_BASELINE_WEAK_BUT_IMPROVABLE`: the best learned configuration (a closed-form ridge at
63 days) had a positive out-of-sample mean rank IC (~0.0506) that beat the model-free composite,
but one walk-forward fold was catastrophic. Phase 3-B diagnosed that instability without
retraining and emitted `PROCEED_TO_REFINED_GREENFIELD_RERUN` with a concrete refined
configuration and a kill switch. Phase 3-C executes that configuration — and is the **kill-switch
phase** for price/volume-only modeling.

## What Phase 3-B diagnosed

Four catastrophic 63d folds clustering at regime turning points, driven by **non-stationary
low-volatility / momentum coefficients that invert across regimes**, **high single-sector
concentration**, **structural feature redundancy** (`cross_sectional_ranks` were monotone
duplicates; raw `return_Nd` were sign-mirrors of `reversal_Nd`), and **63d overlapping-label
autocorrelation**. Because 21d was not catastrophic in 3-A, the instability looked *fixable*, so
3-B prescribed: promote 21d to primary, prune the redundant families, neutralise sector and
risk/beta/volatility exposure, and correct the 63d overlap.

## Refined feature set

23 strictly-trailing features (`research/output/phase3c_refined_feature_set.csv`):

- **Longer-term momentum:** `momentum_12_1`, `return_63d`, `return_126d`, `return_252d`.
- **Reversal:** `reversal_5d`, `reversal_10d`, `reversal_21d`.
- **Volatility / risk:** `realized_vol_21d`, `realized_vol_63d`, `downside_vol_21d`,
  `max_drawdown_63d`, `distance_from_63d_high`.
- **Single liquidity control:** `avg_dollar_volume_21d`.
- **Market-relative:** `excess_return_vs_spy_21d`, `excess_return_vs_spy_63d`, `rolling_beta_63d`,
  `rolling_corr_spy_63d`.
- **Sector-relative:** six demeaned-within-sector features.

## Removed feature families

- **`cross_sectional_ranks` — pruned entirely** (monotone rank transforms of their bases; pure
  collinearity).
- **Sign-mirror raw `return_5d` / `return_10d` / `return_21d` — dropped** (exact negatives of the
  reversals already kept).
- **Volume / liquidity collapsed to one control** (`avg_dollar_volume_21d`); the rest were
  near-noise.
- **`realized_vol_126d` dropped** to remove the conflicting-volatility redundancy (the
  `realized_vol_63d` vs `realized_vol_126d` sign clash flagged in 3-B).

## Prediction neutralization

Applied to the **learned** predictions only (the model-free composite stays raw as the must-beat
benchmark). Uses only trailing features and the prediction itself — **no forward labels**, so no
leakage:

1. **Sector-neutralise:** subtract the date-sector mean prediction.
2. **Risk-neutralise:** per date, residualise the prediction against `rolling_beta_63d`,
   `realized_vol_63d`, and `rolling_corr_spy_63d`.
3. **Winsorise:** clip the cross-sectional prediction by date to ±3σ.

## 21d primary / 63d secondary / 5d diagnostic

- **21d primary** — the gate horizon; the kill switch evaluates the 21d refined ridge.
- **63d secondary** — reported **with** an overlapping-label correction (below); 63d daily-overlap
  metrics alone are not allowed to pass the phase.
- **5d diagnostic** — reported for context only.

## Walk-forward design

Chronological, non-overlapping ~6-month validation windows (126 sessions) after a ≥3-year
(756-session) training window, with a **63-session embargo** (= the maximum label horizon)
between train and validation. Models train only on dates strictly before each validation window.
**15 folds.** Standardisation is fit on the training window only and clipped to ±4σ.

## 63d overlapping-label correction

Because 63d forward labels overlap heavily across consecutive dates, the daily-overlap IC and its
fold variance are misleading. Phase 3-C additionally samples validation dates approximately every
63 sessions (one block per label window) and reports the **non-overlap-corrected** 63d mean rank
IC alongside the daily one. Here the 63d refined ridge was negative either way — daily-overlap
**−0.0130** and non-overlap-corrected **−0.0181** — so the correction did not rescue 63d.

## Kill-switch criteria

The primary 21d refined ridge passes only if **all** of these hold: catastrophic fold count = 0;
worst fold rank IC > −0.05; fold win rate ≥ 0.60; mean rank IC > 0; beats the model-free
composite at 21d; average top-minus-bottom spread > 0; positive-spread fraction ≥ 0.60; mean
top-quintile single-sector share ≤ 0.35 (or clearly improved vs Phase 3-A); and no **production
edge** is claimed. Failure of any **major** gate (catastrophic fold present, worst fold ≤ −0.05,
or fold win rate < 0.60) trips the kill switch.

## Result and recommendation

**Recommendation: `PRICE_VOLUME_ONLY_KILL_SWITCH_TRIGGERED` → Phase 3-D (External Data Decision
for Greenfield Modeling).** The kill switch **triggered.**

The refinements split cleanly into one that worked and one that did not:

- **Sector neutralisation worked.** Mean top-quintile single-sector share fell from **0.376**
  (Phase 3-A best) to **0.209** — well within the 0.35 cap and clearly improved. The
  concentration gate passes.
- **Risk neutralisation removed the signal.** Residualising the prediction against beta /
  volatility / SPY-correlation stripped out the low-volatility / beta tilt — which Phase 3-B had
  identified as the **strongest-IC family** but also the regime-sensitive one. With that exposure
  removed, the 21d refined ridge collapses to a mean rank IC of **0.0041** (essentially zero),
  with a **negative** average long-short spread (−0.0018) and a **0.533** fold win rate.

Primary 21d refined ridge, kill-switch gates (3 of 9 fail, including all three major gates):

| Gate | Value | Pass |
|------|-------|------|
| catastrophic fold count = 0 | **3** | ✗ (major) |
| worst fold rank IC > −0.05 | **−0.1036** | ✗ (major) |
| fold win rate ≥ 0.60 | **0.533** | ✗ (major) |
| mean rank IC > 0 | 0.0041 | ✓ |
| beats model-free composite | 0.0041 vs −0.0149 | ✓ |
| avg top-minus-bottom spread > 0 | −0.0018 | ✗ |
| positive-spread fraction ≥ 0.60 | 0.60 | ✓ |
| sector concentration ≤ 0.35 / improved | 0.209 (was 0.376) | ✓ |
| no production edge claimed | true | ✓ |

The three catastrophic 21d folds after neutralisation are **f14** (2026-04→06, IC −0.1036), **f1**
(2019-10→2020-04, IC −0.0670), and **f3** (2020-10→2021-04, IC −0.0593) — they no longer cluster
in the 2022 selloff as in 3-A; the instability simply migrated once the dominant exposure was
removed. By regime, the 21d ridge is positive in choppy / sideways windows (mean IC +0.021, win
0.75) but negative across risk-on rallies (mean IC −0.025, win 0.40). The model-free composite is
also negative at 21d (−0.0149) and 63d (−0.0346), so this is not a single-model artifact.

**Interpretation:** the only thing that made the greenfield ridge look attractive in 3-A was a
non-stationary risk/regime tilt. Once that tilt is properly neutralised — exactly the fix 3-B
prescribed to kill the catastrophic folds — there is **no robust residual price/volume alpha**
left at the primary horizon, and new catastrophic folds appear. Price/volume-only modeling on
this panel is too unstable / weak to continue.

## Why no production model candidate is created

Phase 3-C trains research models only and the kill switch tripped, so there is nothing to
promote. It sets `research_model_trained = true`, `production_model_trained = false`,
`production_model_candidate_created = false`, and `deployable_model_artifact_written = false`,
keeps the model-v2 serving flag disabled, and claims no **production edge**. The universe remains
survivorship-biased and the sector map is current-as-of (not point-in-time).

## What Phase 3-D should do

Per the kill switch, **stop tuning price/volume features** and run the **External Data Decision
for Greenfield Modeling**: decide whether to add fundamentals, estimates, earnings, news, options,
macro, or alternative data — and on what (paid / point-in-time) source. Do not return to the old
Phase 2K single-signal rescue path. Like every phase in this track, Phase 3-D **does not deploy**,
**does not restart stock-api.service**, **does not enable** the model-v2 flag, **does not run
migrations**, **does not write to production DB**, and **does not trade**, and it claims no
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
research_model_trained              = true
production_model_trained            = false
production_model_candidate_created  = false
deployable_model_artifact_written   = false
d_drive_read                        = true   (price panel, read-only)
d_drive_written                     = false
network_used                        = false
```

## Conclusion

Phase 3-C reran the Phase 3-B refined configuration through a stricter, kill-switched
walk-forward test. Sector neutralisation fixed the concentration problem, but neutralising the
risk/beta/volatility tilt — the very exposure that drove both the 3-A signal and its catastrophic
folds — left no stable residual price/volume alpha: the primary 21d refined ridge has a near-zero
mean rank IC, a negative average long-short spread, a sub-0.60 fold win rate, and three
catastrophic folds. The kill switch triggered, the recommendation is
`PRICE_VOLUME_ONLY_KILL_SWITCH_TRIGGERED`, and the next step is the external-data decision (Phase
3-D). No production model candidate is created, no deployable artifact is written, nothing is
written to D:, nothing is fetched from the network, and all results remain survivorship-biased /
current-as-of and are **not a production edge**.
