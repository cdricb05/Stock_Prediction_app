# Phase 10-L-A — Historical Sector-Neutral Scored Panel Reconstruction (v1)

## 1. Why Phase 10-L-A exists

Phase 10-L-A reconstructs and **persists to disk** the historical per-`(month, ticker)` sector-neutral
scored panel that the Phase 10-D validation builds **in memory on every run but never saves**. It is a
**data-lineage / reproducibility phase** — not an alpha search, not a new-factor test, and not a
new-champion declaration. The single deliverable is a frozen panel that reproduces the Phase 10-D
baseline closely enough to support future reweighting / robustness testing (Phase 10-L-B).

## 2. What Phase 10-K proved

Phase 10-K (the skeptical narrow-improvement harness) returned **`BASELINE_REMAINS_CHAMPION`**, but its
decisive structural finding was that improvement testing was **blocked by missing data**. The frozen
Phase 10-D / 10-F / 10-H outputs contain only (a) backtested **summary metrics** for four *fixed*
signals (`composite_sn`, `composite_raw`, `fcf_to_assets`, `operating_accruals`) and (b) the **latest
single 2026Q2 cross-section**. They do **not** contain the **historical** per-`(month, ticker)`
sector-neutral scored panel with forward 63-day returns. In short: **Phase 10-K proved the historical
scored panel is missing**, so interior re-weightings (60/40, 40/60, 70/30, 30/70) and robustness
transforms (z-cap, winsorize) could not be honestly re-backtested and were reported
`INSUFFICIENT_INPUTS`.

## 3. Why no further alpha-improvement testing is trustworthy until this panel exists

Any 60/40 / 40/60 / z-cap / winsorize / stricter-filter test needs a per-observation history of the
**oriented within-month z-legs** and the **forward 63d return** so a reweighted composite can be scored
month by month, out of sample, net of cost. Without that history, a reweighting can only be evaluated on
one cross-section — which cannot demonstrate IC, turnover, cost survival, or OOS stability. That is
exactly why Phase 10-K refused to rank the interior weights. This phase produces the missing history
first, so that Phase 10-L-B can be trustworthy.

## 4. Which prior scripts and outputs were inspected

Discovered (not assumed) from the reuse chain that Phase 10-D already runs:

- **`research/run_phase10b_eodhd_norgate_exhaustive_alpha_factory.py`** — owner of the normalized quality
  leg CSVs and the secret-safety scanner.
- **`research/run_phase10c_eodhd_quality_oos_validation.py`** — `build_panel()` (Norgate
  survivorship-free earnings-event panel + PIT liquidity proxy + cohort tag), `attach_signals()`
  (as-of attach of each leg's PIT level → oriented `o_<feat>` + sector-neutral oriented `o_<feat>__sn`),
  and `_eval()`.
- **`research/run_phase10d_quarterly_quality_composite_validation.py`** — `build_composite()`
  (`comp_raw`/`comp_sn` = sum of within-month z of the oriented legs), `quarterly_backtest()`,
  `walk_forward_h()`, and the fixed a-priori `LEGS`.
- **`research/run_phase10k_quarterly_quality_composite_alpha_improvement_harness.py`** — the phase that
  established the missing-panel blocker.

Outputs inspected:

- `research/output/phase10d_quarterly_quality_composite_validation/phase10d_quarterly_quality_composite_validation.json`
  — the **frozen baseline** metrics reproduced here.
- `research/output/phase10b_.../` normalized leg directories, and the owned/local gitignored data:
  `research/data/eodhd/normalized/eod_prices/expanded_price_panel.csv`,
  `.../eodhd_fcf_to_assets/fcf_to_assets.csv`, `.../eodhd_operating_accruals/operating_accruals.csv`.
- `research/output/phase10c_.../` and `research/output/phase10k_.../` for lineage context.

The exact source→column mapping for every panel field is written to `panel_schema.csv` and the JSON
`panel_schema` block.

## 5. Which fields were successfully reconstructed

The frozen panel `historical_sector_neutral_scored_panel.csv` has **38,725 rows** (545 tickers,
2016-06-23 → 2026-05-22, 12 sectors) and all 22 required columns. Fully reconstructed:
`as_of_date`, `rebalance_date`, `ticker`, `sector`, `cohort`, `is_new_cohort`, `liquidity_proxy`,
`forward_63d_return_start_date`, `has_forward_return`, `source_phase`, `data_quality_flag`. Reconstructed
on every scoreable row (partial coverage only where the underlying fundamental/return is genuinely
absent): the raw fundamental levels `fcf_to_assets` / `operating_accruals`; the two **raw** within-month
z-legs `fcf_to_assets_raw` / `operating_accruals_raw`; the two **sector-neutral** within-month z-legs
`fcf_to_assets_sector_neutral_z` / `operating_accruals_sector_neutral_z` (and the identical
`operating_accruals_oriented_sector_neutral_z`); the composites `composite_sn` / `composite_raw`; and
`forward_63d_return`.

**Additive self-check (the core panel guarantee).** The stored z-legs sum **exactly** to the composites:
`max|fcf_raw + acc_raw − composite_raw| = 0.0` and `max|fcf_sn + acc_sn − composite_sn| = 0.0`. This is
what makes the panel directly reweightable: a future harness computes `w₁·fcf_sn + w₂·acc_sn` per row
with no re-derivation.

## 6. Which fields could not be reconstructed

Only one column is left blank: **`forward_63d_return_end_date`**. The 10-C/10-D builder persists the
realized 63-trading-day excess return but **not** the calendar end date of that window (deriving it needs
the per-ticker trading calendar). It is **non-blocking** — reweighting and IC reproduction use
`rebalance_date` + `forward_63d_return` only. `sector` is *current-as-of* the owned metadata (a share of
rows remain `Unknown`), so sector grouping is not strictly point-in-time membership; this is recorded as
a known limit, not a reconstruction failure. See `missing_fields_report.csv`.

## 7. Whether the Phase 10-D baseline was reproduced

**Yes — reproduced from the persisted file, within all tolerances.** Reloading the frozen CSV and
recomputing with the same engine functions 10-D used reproduces the frozen **`composite_sn`** baseline
essentially exactly:

| metric | frozen 10-D | reconstructed | abs diff | tolerance | gate |
|---|---|---|---|---|---|
| IC t-stat (63d) | 2.665 | 2.66461 | 3.9e-04 | 0.25 | ✅ |
| quarterly net-25bps | +0.00401 | +0.004010 | 2.8e-07 | 0.0015 | ✅ |
| quarterly net-50bps | +0.00095 | +0.000952 | 2.2e-06 | 0.0015 | ✅ |
| quarterly turnover | 0.6115 | 0.61151 | 1.5e-05 | 0.10 | ✅ |
| IC mean (63d) | 0.03494 | 0.03494 | 1.3e-06 | (info) | ✅ |
| quarterly gross spread | 0.00707 | 0.00707 | 2.7e-06 | (info) | ✅ |
| OOS pooled IC | 0.02956 | 0.02955 | 5.9e-06 | (info) | ✅ |
| OOS frac windows + | 0.50 | 0.50 | 0.0 | (info) | ✅ |
| top-sector share | 0.6262 | 0.6262 | 1.8e-07 | (info) | ✅ |

All **4/4 hard gates pass**, and the diagnostic `composite_raw` matches too (IC t 3.074, net-25bps
+0.00648, net-50bps +0.00349, turnover 0.5989). The **modest/boundary but positive net-25bps** direction
is preserved. Decision: **`PANEL_RECONSTRUCTION_READY`**.

## 8. Whether the panel is ready for Phase 10-L-B reweighting tests

**Yes.** All decision-critical columns are reconstructed, the z-legs additively reconstruct both
composites (error 0.0), and the frozen baseline reproduces within tolerance. Phase 10-L-B can now run the
Phase 10-K narrow-improvement harness **against this frozen historical panel** — honestly backtesting
interior leg re-weightings and robustness transforms on the persisted per-`(month, ticker)` z-legs +
forward 63d returns, under the same strict sector-neutral cost/turnover/OOS gates. Still offline,
owned-data-only, paper-only.

## 9. Why this phase creates no orders, automation, live trading, provider calls, or Paper Trader writes

This is a research reproducibility artifact. It runs the same **fully offline** pipeline 10-C/10-D already
run (reading only the owned/local gitignored expanded price panel and the owned EODHD normalized quality
leg CSVs) and writes only a frozen research panel + diagnostics. It makes **no live API calls** and no
external network calls, does not probe providers, does not touch GCP, does not deploy, connects to **no
broker**, and writes **nothing** to the Paper Trader. It creates **no orders** and **no automation** —
consistent with the project charter (harness-first, paper-only, owned-data-only). The safety block in the
JSON asserts `paper_only`, `owned_local_data_only`, `no_live_api_calls`, `no_orders`, `no_automation`,
`no_broker`, `no_deploy` all `true`.

## 10. Why the alpha remains modest / boundary

Reconstructing the panel does **not** change the economics. The 10-D `composite_sn` edge is **modest /
boundary**: net-of-cost quarterly return is small (net-25bps ≈ +0.00401, net-50bps ≈ +0.00095), the 63d
IC t-stat (≈ 2.665) sits **below** the project's 3.0 strong bar, and pooled OOS IC is positive but not
overwhelming (frac-positive 0.50). The short `operating_accruals` leg carries most of the robustness; the
long `fcf_to_assets` leg diversifies and lowers sector concentration rather than adding standalone alpha.
It is a real, transparent, cost-robust quality tilt — **not** a strong standalone signal and **not** a
prediction oracle. This phase must not be read as strengthening the alpha; it only makes the existing,
modest/boundary result honestly testable.

## Artifacts (`research/output/phase10l_historical_sector_neutral_scored_panel_reconstruction/`)

`phase10l_historical_sector_neutral_scored_panel_reconstruction.json` ·
`historical_sector_neutral_scored_panel.csv` (38,725 × 22) · `panel_schema.csv` ·
`panel_coverage_summary.csv` · `phase10d_reproduction_check.csv` · `missing_fields_report.csv` ·
`data_quality_report.csv`.

## Run / test (Windows PowerShell only)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python -m py_compile research/run_phase10l_historical_sector_neutral_scored_panel_reconstruction.py
python research/run_phase10l_historical_sector_neutral_scored_panel_reconstruction.py          # offline; no key
python -m pytest tests/test_phase10l_historical_sector_neutral_scored_panel_reconstruction.py -q
```

## Constraints honored

Offline (no network / key / provider probe); **owned/local data only**; no new purchase; **no Paper
Trader writes; NO orders; NO automation; NO broker; no live trading; no deploy; no GCP**; no package
install; targeted tests only; keys never printed or written; output is a frozen research panel + metadata
only. **No commit. No push.**
