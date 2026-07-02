# Phase 10-C — Strict Out-of-Sample Validation of EODHD/Norgate Quality Leads (v1)

## Purpose

Phase 10-B mined **only the data the user pays for** (Norgate survivorship-free foundation + EODHD
Fundamentals feed) and ran the broad Phase 8-X strong-alpha gate. It returned
`STRONG_ALPHA_FOUND_READY_FOR_REVIEW`, but the promoted candidate — `f_accel_sn` over
`eodhd_eps_growth_yoy` (t≈3.91 at the 21-day primary horizon) — was **fragile / likely overfit**
(~12× new-vs-old cohort IC decay, ~50% single-sector concentration, negative net-of-50bps, and a base
eps-growth signal that is insignificant at every horizon). It was **not** productized.

What 10-B *did* surface, in its honest horizon sweep, were two **economically coherent quality leads**
whose *base* signals are sign-stable across horizons:

| lead | orientation | 10-B base IC t (1d / 5d / 21d / 63d) |
|---|---|---|
| `eodhd_fcf_to_assets` (FCF / total assets; higher = better) | **+1** | +3.06 / +2.56 / +1.52 / +2.54 |
| `eodhd_operating_accruals` (Sloan accruals; higher = worse) | **−1** | −1.51 / −2.40 / −0.81 / −3.08 |

**Phase 10-C is a focused, strict, out-of-sample validation of exactly those two leads** — as *simple
interpretable signals* (the base point-in-time level, oriented by the documented anomaly sign, **no
exotic transforms**) — to decide whether either is robust enough to hand to a human for **paper**
review. It is **not** a data-acquisition, provider-search, or random-mining phase.

This phase is **fully offline**: no network, no API key, no provider probe. It re-uses the Phase 10-B
**normalized family CSVs** and the Norgate **545-ticker / 38,725-event** survivorship-free panel
verbatim.

## Inputs reused (owned data only — no new acquisition)

- Norgate survivorship-free expanded event panel (rebuilt offline via `w8.build_expanded_ev`):
  **38,725 events / 545 tickers**, with `cohort` (old/new), `sector`, `liquidity_proxy`, and
  `fwd_exc_{1,5,21,63}` forward excess returns.
- Phase 10-B PIT-normalized family CSVs (gitignored), as-of attached
  (`available_date ≤ entry_date`, no lookahead) via `y8.attach_orthogonal_feature`:
  - `research/data/eodhd/normalized/eodhd_fcf_to_assets/fcf_to_assets.csv`
  - `research/data/eodhd/normalized/eodhd_operating_accruals/operating_accruals.csv`

**Attach reproduction check:** the rebuilt base-signal 21-day IC t-stats reproduce the 10-B horizon
sweep exactly (fcf 1.516 → 1.516; accruals oriented +0.811 vs 10-B raw −0.811) → `MATCH` for both.

## Reuse chain (single source of truth — nothing reimplemented)

| alias | module | provides |
|---|---|---|
| `b10` | `run_phase10b_eodhd_norgate_exhaustive_alpha_factory` | paths, family registry, secret-safety audit |
| `x8` | `run_phase8x_autonomous_strong_alpha_discovery` | walk-forward ensemble, decile spread, strong-gate constants |
| `w8` | `run_phase8w_expanded_universe_failure_attribution` | expanded event panel, cohort tag, liquidity proxy |
| `y8` | `run_phase8y_orthogonal_data_family_acquisition` | PIT as-of attach (`available_date ≤ entry_date`) |
| `c9` | `run_phase9c_verified_owned_feed_alpha_acquisition` | Norgate foundation verification |
| `s8` | `x8.s8` | `evaluate_signal` core: monthly rank IC, quintile spread, turnover, cost, sector concentration |
| `t8` | `x8.t8` | net-spread cost model, logger |

## Validation battery (per signal × {raw, sector-neutral})

1. **Horizon sweep** — 1d / 5d / 21d / 63d (21d ≈ monthly primary; 63d ≈ quarterly). The owned panel
   has no separate calendar-month return column, so 21-day trading days is the monthly proxy.
2. **Walk-forward out-of-sample** — rolling 24-mo train / 6-mo test / 6-mo step (16 windows). The
   orientation is fixed a-priori (economic theory), so we use **`equal` weighting** (pure held-out IC
   of the fixed oriented signal — *no sign refit*, so the ensemble cannot snoop-correct a sample sign
   that contradicts the anomaly). Reports per-window OOS IC, pooled OOS IC, OOS IC t, and fraction of
   windows positive.
3. **Decile long-short spread + hit rate** — out-of-sample, on the held-out ensemble score.
4. **Transaction-cost sensitivity** — gross quintile spread, turnover, net of **10 / 25 / 50 bps**
   round-trip per unit turnover, at every horizon.
5. **Cohort stability** — original "old" 299 cohort vs newly-scoreable "new" cohort (both must be +).
6. **Subperiod stability** — pre-2020 vs post-2020 (both must be +).
7. **Leave-one-sector-out** — exclude each sector in turn; sign must hold; + concentration (top-sector
   share, HHI).
8. **Liquidity filter** — above- vs below-median dollar-volume proxy.
9. **Signal correlation / orthogonality** — pairwise + vs established style factors (surprise,
   momentum, quality, value).

## Strict acceptance gate (a-priori; never tuned to a result)

A signal is only **`CONFIRMED` (ready for paper review)** if, in its oriented form, it clears the
project's established strong bar **AND** the full robustness battery, at the **21-day primary horizon**:

- IC t ≥ **3.0** (the 8-X strong bar — *not* lowered for a focused phase);
- walk-forward OOS: pooled IC > 0 and ≥ **60%** of windows positive;
- both cohorts +, both subperiods +;
- sector-robust: top-sector share < 0.60 **and** leave-one-sector-out sign holds;
- sector-neutral edge still +; survives **50 bps**; ≥ **3 of 4** horizons +; high-liquidity subset +.

The brief's **rejection triggers are checked first, in order** (OOS → cohort → cost), so a signal can
never reach `CONFIRMED` on a technicality:

- fails OOS → `REJECTED_OVERFIT`
- old-cohort-only → `REJECTED_COHORT_INSTABILITY`
- net-of-25bps ≤ 0 at 21d → `REJECTED_NOT_COST_ROBUST`
- directional + OOS-robust but IC t < 3.0 at 21d → `WEAK_BUT_WORTH_MONITORING`

**Allowed terminal decisions:** `QUALITY_ALPHA_CONFIRMED_READY_FOR_PAPER_REVIEW` ·
`QUALITY_ALPHA_WEAK_BUT_WORTH_MONITORING` · `QUALITY_ALPHA_REJECTED_OVERFIT` ·
`QUALITY_ALPHA_REJECTED_NOT_COST_ROBUST` · `QUALITY_ALPHA_REJECTED_COHORT_INSTABILITY` ·
`EODHD_NORGATE_EXHAUSTED_AFTER_OOS` · `HARD_BLOCKER_REQUIRES_USER_ACTION` ·
`ERROR_WITH_REPRO_COMMAND`.
**Forbidden:** `STRONG_ALPHA_FOUND_READY_FOR_REVIEW` without OOS proof, `MISSING_KEY`, `NO_DATA`,
`NEEDS_PROVIDER`, `EMPTY_PAYLOAD`, generic `ERROR`.

## Artifacts (17, output dir `research/output/phase10c_eodhd_quality_oos_validation/`)

`phase10c_eodhd_quality_oos_validation.json` · `candidate_signal_inventory.csv` ·
`oos_split_definition.csv` · `horizon_validation_report.csv` · `walk_forward_ic_report.csv` ·
`walk_forward_decile_report.csv` · `transaction_cost_report.csv` · `cohort_stability_report.csv` ·
`subperiod_stability_report.csv` · `sector_exclusion_report.csv` · `liquidity_filter_report.csv` ·
`signal_correlation_report.csv` · `accepted_quality_alpha.csv` · `rejected_quality_alpha.csv` ·
`paper_review_candidate_package.csv` · `phase10d_next_plan.json` · `secret_safety_audit.csv`.

## Run / test (Windows PowerShell only)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python -m py_compile research/run_phase10c_eodhd_quality_oos_validation.py
python -m pytest tests/test_phase10c_eodhd_quality_oos_validation.py -q     # targeted only; 16 passed
python research/run_phase10c_eodhd_quality_oos_validation.py                # fully offline; no key
```

## Constraints honored

Offline (no network / key / provider probe); only `fcf_to_assets` + `operating_accruals` validated;
no FMP; no Paper Trader; no GCP; no orders; no automation; no deploy; no package install; no full
regression (targeted tests only); keys never printed or written; output is metadata only.
**No commit. No push.**

---

## Status — live run 2026-06-28 (offline; exit 0)

**Final decision: `QUALITY_ALPHA_REJECTED_NOT_COST_ROBUST`.**

Both quality leads are **out-of-sample real and cohort-stable**, but their **monthly (21d) edge is
consumed by 25 bps round-trip cost**. Both **survive 25 bps *and* 50 bps at the 63-day (quarterly)
horizon** — the natural rebalance cadence for quarterly-updating fundamentals. Neither is ready for
paper trading as a standalone monthly signal; the right next step is a **quarterly-rebalanced quality
composite**.

### What held (this is *not* the 10-B overfit failure mode)

| axis (21d primary, raw variant) | `fcf_to_assets` | `operating_accruals` (oriented −1) |
|---|---|---|
| walk-forward OOS pooled IC (t, % windows +) | **+0.0278** (t 1.34, 62.5% +) | **+0.0153** (t 1.13, 62.5% +) |
| both cohorts positive | ✅ old +0.027 / new +0.020 | ✅ old +0.005 / new +0.020 |
| both subperiods positive (pre/post-2020) | ✅ +0.024 / +0.027 | ✅ +0.007 / +0.013 |
| ≥3/4 horizons oriented-positive | ✅ 4/4 | ✅ 4/4 |
| sector-neutral edge positive | ✅ | ✅ |

Unlike the 10-B `f_accel_sn` promotion (new-cohort decay, sector-concentrated, negative net-50bps), the
two leads are **directionally stable out-of-sample, across both cohorts and both subperiods**. They are
genuine, weak-but-real quality signals.

### What failed — cost at the monthly horizon

| feature (raw, 21d) | gross quintile spread | turnover | net 25 bps | net 50 bps |
|---|---|---|---|---|
| `fcf_to_assets` | 0.0023 | **0.994** | **−0.0027** | −0.0076 |
| `operating_accruals` | 0.0034 | **0.993** | **−0.0016** | −0.0066 |

The binding failure is the brief's "works only before costs" trigger. **But the ~99% turnover is
largely a structural artifact of the *earnings-event* panel**: each calendar month's cross-section is a
different set of names *reporting that month*, so consecutive-month top quintiles are nearly disjoint by
construction — not because the signal flips. The same s8 cost model (used by all of 8-T…10-B) therefore
*overstates* turnover for an event-driven fundamental book.

The proof is the **63-day (quarterly) horizon**, where the gross spread is large enough to clear cost:

| feature (raw, 63d) | IC t | gross spread | net 25 bps | net 50 bps |
|---|---|---|---|---|
| `fcf_to_assets` | **2.53** | 0.0112 | **+0.0063** | **+0.0013** |
| `operating_accruals` | **3.07** | 0.0158 | **+0.0108** | **+0.0059** |

Both signals are strong **and** cost-robust at the quarterly cadence — which is exactly the rebalance
frequency a quarterly-updating fundamental signal should trade at.

### Per-end-report fields

- **Final decision:** `QUALITY_ALPHA_REJECTED_NOT_COST_ROBUST` (brief-compliant — both fail the "before
  costs only" trigger at the 21d primary horizon).
- **Did `fcf_to_assets` survive?** No at 21d (CONFIRMED/WEAK requires surviving 25 bps; it does not).
  Real and OOS-robust, and cost-robust at 63d, but **not** standalone-tradeable monthly.
- **Did `operating_accruals` survive?** No (same cost failure at 21d; also sector-concentration weak —
  top-sector share 0.63, leave-`Unknown`-out flips sign). Cost-robust and strong (t 3.07) at 63d.
- **Best OOS signal:** `fcf_to_assets` (cleaner: sector-robust, high-liquidity +, OOS pooled +0.028).
- **Best OOS horizon:** **63-day (quarterly)** for both — strong and cost-robust there.
- **Net-of-cost result:** negative at 25 bps for both at 21d; **positive at 25 bps and 50 bps for both
  at 63d**.
- **Cohort stability:** both cohorts positive for both signals (not old-cohort-only).
- **Sector stability:** `fcf_to_assets` robust (leave-one-out sign holds, top share 0.50); 
  `operating_accruals` weaker (top share 0.63, driven by unmapped-`Unknown`-sector names — a panel
  sector-mapping coverage gap, not a real single-sector bet).
- **Ready for Paper Trader review?** **No** — nothing reached `CONFIRMED`. No paper-review package is
  promoted.
- **Exact next command:** `review research/output/phase10c_eodhd_quality_oos_validation/transaction_cost_report.csv then build + validate the 63d quarterly quality composite`.
- **Targeted tests:** **16 passed**, 0 failed.
- **Commit recommendation:** **Do not commit** (standing rule). Runner, tests, doc, and 17 artifacts
  are on disk for review.

### Orthogonality / composite note

`fcf_to_assets` vs `operating_accruals` (oriented) Spearman ρ = **0.56** — correlated but not redundant
(a 2-factor composite adds diversification). Both are near-orthogonal to surprise (ρ≈0.04/0.01) and
momentum (ρ≈0.06/0.04); `fcf_to_assets` overlaps the existing `quality_composite` (ρ=0.44) — it is a
cleaner direct quality measure, not a brand-new factor.

### Recommended Phase 10-D

Build a **quarterly-rebalanced (63d), low-turnover, transparent quality composite** — `fcf_to_assets`
long + `operating_accruals` short, equal-risk, sector-neutral — and re-run **this** strict OOS + cost
battery at the 63-day horizon. **No new data purchase**; owned EODHD/Norgate data only. If the composite
clears the gate net of 25 bps at 63d, *then* promote it to a paper-only review harness (still no orders,
no automation).
