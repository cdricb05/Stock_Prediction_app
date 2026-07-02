# Phase 10-L-B — Historical Quality Composite Reweighting And Robustness Backtest (v1)

## 1. Why Phase 10-L-B exists

Phase 10-K asked the narrow question: *can the Phase 10-D quarterly quality composite (long
`fcf_to_assets`, short `operating_accruals`, equal-weight, sector-neutral, 63d) be improved by leg
re-weighting, z-cap / winsorize robustness transforms, or stricter liquidity / sector-cap packaging?*
It **could not answer honestly** — the frozen 10-D/10-F/10-H outputs held only summary metrics for four
fixed signals plus the latest 2026Q2 cross-section, so every re-weight / transform was reported
`INSUFFICIENT_INPUTS`.

**Phase 10-L-A** removed that blocker by persisting the historical per-`(month, ticker)` sector-neutral
scored panel — the additive within-month z-legs (`z_fcf_sn`, `z_acc_sn`) and the forward 63-day returns —
and proved it reproduces the frozen 10-D `composite_sn` baseline within tolerance.

**Phase 10-L-B is the honest re-run of the 10-K narrow-improvement harness against the Phase 10-L-A
panel.** Every variant is a reweighting / transform / filter of the **same two legs** — it is **not** a
broad alpha search, **not** a new factor, **not** a provider probe. Each variant is scored with the
**exact** engine functions Phase 10-D used, so results are directly comparable to the baseline.

## 2. Inputs (owned / local; offline)

- `research/output/phase10l_historical_sector_neutral_scored_panel_reconstruction/historical_sector_neutral_scored_panel.csv`
  — the frozen Phase 10-L-A panel (38,725 rows, 545 tickers, 22 columns). This phase reads the two
  sector-neutral z-legs (`fcf_to_assets_sector_neutral_z`, `operating_accruals_sector_neutral_z`), the
  additive `composite_sn`, the `forward_63d_return`, and `sector` / `cohort` / `liquidity_proxy`.
- `research/output/phase10l_historical_sector_neutral_scored_panel_reconstruction/phase10l_historical_sector_neutral_scored_panel_reconstruction.json`
  — the Phase 10-L-A metadata (context / lineage).
- `research/output/phase10d_quarterly_quality_composite_validation/phase10d_quarterly_quality_composite_validation.json`
  — the **frozen 10-D baseline** used as the panel-integrity guard.

No `build_panel`, no network, **no live API calls**, no key, no provider probe.

## 3. Engine reuse (single source of truth)

- `c10._eval` — cross-sectional IC battery (mean IC, IC t-stat, top-sector share) at 63d.
- `d10.quarterly_backtest` — realistic quarterly-cadence long-short book (quintile spread, turnover,
  net-25/50bps). The local `quarterly_book(cap_frac=None)` reproduces it **exactly** (cross-checked at
  runtime to `1e-9`) and additionally reports average long/short counts; with `cap_frac` set it applies a
  greedy per-sector book cap.
- `d10.walk_forward_h` — rolling out-of-sample IC (pooled OOS IC, frac windows positive), pure held-out,
  no sign refit.

## 4. Baseline (frozen Phase 10-D `composite_sn`)

Equal-weight, sector-neutral, 63d / quarterly, long `fcf_to_assets` / short `operating_accruals`:
IC t ≈ 2.665, quarterly net-25bps ≈ +0.00401, net-50bps ≈ +0.00095, turnover ≈ 0.6115, OOS
frac-positive ≈ 0.50, sector-neutral book top-sector share ≈ 0.63.

The alpha is **modest / boundary**: the net-of-cost quarterly edge is small and the 63d IC t sits **below**
the project's 3.0 strong bar. The short (`operating_accruals`) leg carries most of the robustness; the
long (`fcf_to_assets`) leg diversifies and lowers concentration. This is not oversold here.

## 5. Variants tested

**Weighting** (sector-neutral legs): `w_50_50` (baseline), `w_60_40`, `w_40_60`, `w_70_30`, `w_30_70`,
`w_fcf_only_100_0`, `w_accruals_only_0_100`.
**Robustness transforms** (applied per leg, then equal-weight): `zcap_abs_3_0`, `zcap_abs_2_5`,
`winsorize_1_99`, `winsorize_5_95` (within-month winsorize).
**Liquidity filters**: `liq_p25_baseline`, `liq_p50_stricter` (keep within-month liquidity ≥ pXX).
**Sector-cap packaging**: `sector_cap_25_baseline`, `sector_cap_20_stricter` (greedy per-side book cap).
**Pre-declared limited combinations** (best interior/extreme weight by quarterly net-25bps, then one
stricter control each): `best_weight_plus_zcap_3_0`, `best_weight_plus_liq_p50`,
`best_weight_plus_sector_cap_20`.

For each variant the harness computes: IC mean, IC t-stat, quarterly gross spread, quarterly net-25bps
and net-50bps, turnover, OOS / time-split (walk-forward + pre/post-2020 subperiods), cohort stability
(old vs new), top-sector share, long-leg and short-leg contribution, number of rebalance periods, and
average long / short counts.

## 6. Champion rule (strict, skeptical, RELATIVE)

A variant may unseat the baseline **only if all** of the following hold:

1. quarterly net-25bps strictly **higher** than baseline;
2. quarterly net-50bps **not worse** than baseline;
3. turnover **not materially worse** (≤ 1.10× baseline; a hard reject at > 1.50×);
4. IC t-stat **not materially worse** (≥ baseline − 0.10);
5. OOS frac-windows-positive **does not deteriorate** (≥ baseline);
6. sector concentration **does not worsen** (top-sector share ≤ baseline);
7. the improvement is **explainable and not one-period driven** — positive in **both** cohorts and
   **both** subperiods, quarterly hit-rate not worse, and not fewer rebalance periods.

The concentration criterion is **relative** (not worse than the baseline), because the sector-neutral
book already runs at ≈ 0.63 — above the 0.60 raw ceiling (a known 10-D/10-F `Unknown`-sector mapping
caveat), so an absolute 0.60 gate would reject the baseline itself.

## 7. Decision enum

- `REWEIGHTED_ALPHA_READY_FOR_PAPER_RULES` — at least one variant clears the strict relative test.
- `BASELINE_REMAINS_CHAMPION` — no variant even beats the baseline net-25bps on honest evidence.
- `REJECT_REWEIGHTING_OVERFIT` — a variant raises the in-sample net-25bps but fails a secondary
  criterion and/or is not robust across cohorts / subperiods / quarters (classic reweighting overfit);
  the baseline stays champion and the overfit reweighting is explicitly not productized.
- `NEEDS_PANEL_REPAIR` — the frozen panel fails the integrity / reproduction guard (z-legs do not
  additively reconstruct `composite_sn`, or it does not reproduce the 10-D baseline within tolerance);
  no variant scoring is performed and Phase 10-L-A must be re-run.

## 8. Panel-integrity guard (before any scoring)

The harness refuses to reweight an untrustworthy panel. It requires: (a) `max|composite_sn −
(z_fcf_sn + z_acc_sn)| ≤ 1e-6` (the 10-L-A additive guarantee); (b) the frozen panel reproduces the 10-D
`composite_sn` baseline within the a-priori tolerances (IC t 0.25, net-25/50bps 0.0015, turnover 0.10);
and (c) the local `quarterly_book(cap=None)` reproduces `d10.quarterly_backtest` to `1e-9`. Any failure
→ `NEEDS_PANEL_REPAIR`.

## 9. Leg contribution

Per variant, leg contribution = weight × standalone sector-neutral single-leg mean-IC — a transparent
weighted-IC attribution (directional, not an exact variance decomposition, because rank-IC is not linear
in the legs). Consistent with 10-K, the short `operating_accruals` leg carries most of the robustness and
the long `fcf_to_assets` leg diversifies.

## 10. Why this phase creates no orders, automation, live trading, provider calls, or Paper Trader writes

This is a research backtest over an owned, frozen panel. It runs fully offline, reads only owned/local
prior-phase outputs, and writes only research CSV/JSON to its own output directory. It makes **no live
API calls** and no external network calls, does not probe providers, does not touch GCP, does not deploy,
connects to **no broker**, and writes nothing to the Paper Trader. It creates **no orders** and **no
automation** — consistent with the project charter (harness-first, paper-only, owned-data-only). The
JSON `safety` block asserts `paper_only`, `owned_local_data_only`, `no_live_api_calls`, `no_orders`,
`no_automation`, `no_broker`, `no_deploy` all `true`. The alpha remains **modest / boundary** and is not
a prediction oracle; this phase does not change that.

## Artifacts (`research/output/phase10l_quality_composite_reweighting_robustness_backtest/`)

`phase10l_quality_composite_reweighting_robustness_backtest.json` · `variant_scorecard.csv` ·
`baseline_vs_variants.csv` · `oos_stability_report.csv` · `cohort_stability_report.csv` ·
`turnover_cost_report.csv` · `sector_concentration_report.csv` · `rejected_variants.csv`.

## Run / test (Windows PowerShell only)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python -m py_compile research/run_phase10l_quality_composite_reweighting_robustness_backtest.py
python research/run_phase10l_quality_composite_reweighting_robustness_backtest.py          # offline; no key
python -m pytest tests/test_phase10l_quality_composite_reweighting_robustness_backtest.py -q
```

## Constraints honored

Offline (no network / key / provider probe / `build_panel`); **owned/local data only** (frozen 10-L-A
panel + frozen 10-D report); no new purchase; **no new factor; no broad alpha search**; **no Paper Trader
writes; no orders; no automation; no broker; no live trading; no deploy; no GCP**; no package install;
targeted tests only; output is research metadata only. **No commit. No push.**
